"""Working-directory observation for live terminal panes.

A pane's *launch* directory is where it started; its *working* directory is
where it is now. This module owns the half of that answer that is pure text:
the shell-integration lines GridVibe installs at startup, and the parser that
reads the escape sequences those lines emit back out of the terminal's own
output stream.

Two properties are the reason it is shaped this way:

- **Observation never writes to the shell.** The prompt hook is installed once,
  and every later answer comes out of output the shell was going to produce
  anyway. Nothing types at a prompt the user may be using, and nothing types
  into a running agent. A shell GridVibe starts itself is handed its hook at
  spawn -- through its own environment or its own argv -- so nothing is typed
  even once and the pane shows nothing; only a remote shell, which accepts
  neither, is sent a line, and that one is kept short.
- **The value is already known when a route asks.** A query costs a dictionary
  read, not a round trip with a deadline, so the same gesture cannot open two
  different roots on two different days depending on what the shell happened to
  be doing.

Everything here is text-in/values-out, so ``tests/test_terminal_cwd.py``
executes it directly; its one import is the sequence scanner it shares with the
agent-activity observer (``web/osc_stream.py``), which is text-in/text-out too.
The stream side -- choosing when to parse, where the residue lives, and what to
do with the result -- stays in ``web/terminal_io.py``.
"""

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from web.osc_stream import pending_osc_residue

#: Event kinds ``parse_cwd_events`` reports.
CWD_EVENT_DIRECTORY = "cwd"
CWD_EVENT_SHELL_PID = "pid"

#: Upper bound on the split-sequence residue one connection may carry between
#: two reads. A sequence is a few dozen characters; anything longer is either a
#: pathological path or a stream deliberately opening ``ESC ] 7 ;`` and never
#: closing it, and neither may grow a per-connection buffer without limit.
CWD_RESIDUE_MAX_CHARS = 2048

#: The three sequences GridVibe reads. OSC 7 is the de-facto POSIX convention
#: (``file://<host><path>``), still accepted from any shell that emits it on its
#: own; OSC 9;9 carries a raw, unencoded path and is what *GridVibe's own* hooks
#: emit, for every shell family; the OSC 777 line is GridVibe's own too, emitted
#: exactly once per SSH shell so the ``/proc/<pid>/cwd`` corroboration source has
#: a pid to read.
_OSC7_HEAD = "\x1b]7;"
_OSC9_HEAD = "\x1b]9;9;"
_OSC_PID_HEAD = "\x1b]777;gridvibe-pid;"
_SEQUENCE_HEADS = (_OSC7_HEAD, _OSC9_HEAD, _OSC_PID_HEAD)

# A payload runs to the terminator and may contain neither BEL nor ESC, so the
# pattern cannot run away across the rest of a chunk looking for a close.
_OSC_EVENT_PATTERN = re.compile(
    r"\x1b\]"
    r"(?:7;(?P<osc7>[^\x07\x1b]*)"
    r"|9;9;(?P<osc9>[^\x07\x1b]*)"
    r"|777;gridvibe-pid;(?P<pid>[^\x07\x1b]*))"
    r"(?:\x07|\x1b\\)"
)

_WSL_MOUNT_PATTERN = re.compile(r"^/mnt/([A-Za-z])(?:/(.*))?$")
_URL_DRIVE_PATTERN = re.compile(r"^/([A-Za-z]):(?:[\\/](.*))?$")


def _pending_residue(text: str) -> str:
    """Return the trailing fragment of a cwd sequence still being written.

    The scan itself is ``web/osc_stream.py``'s -- shared with the agent-activity
    observer, which reads the same stream for different sequences -- and what
    stays here is the pair this module owns: which heads count, and how much of
    an unterminated one may be carried.
    """
    return pending_osc_residue(text, _SEQUENCE_HEADS, CWD_RESIDUE_MAX_CHARS)


def parse_cwd_events(chunk: str, residue: str = "") -> Tuple[List[Tuple[str, str]], str]:
    """Read cwd/pid sequences out of one terminal output chunk.

    Returns every event in the order it appeared plus the residue to hand back
    on the next read. The stream is only *observed*: the caller still caches and
    replays the chunk verbatim, because the same replay serves a pane whose TUI
    is still running and a filtered replay would break the live case.
    """
    text = (residue or "") + (chunk or "")
    if "\x1b" not in text:
        return [], ""

    events: List[Tuple[str, str]] = []
    for match in _OSC_EVENT_PATTERN.finditer(text):
        pid = match.group("pid")
        if pid is not None:
            value = pid.strip()
            if value.isdigit():
                events.append((CWD_EVENT_SHELL_PID, value))
            continue
        raw = match.group("osc7")
        if raw is not None:
            target = decode_osc7_target(raw)
        else:
            target = (match.group("osc9") or "").strip().strip('"')
        target = target.strip()
        if target:
            events.append((CWD_EVENT_DIRECTORY, target))

    return events, _pending_residue(text)


def latest_event(events: List[Tuple[str, str]], kind: str) -> str:
    """Return the last value reported for one event kind, or ""."""
    for event_kind, value in reversed(events):
        if event_kind == kind:
            return value
    return ""


def decode_osc7_target(value: str) -> str:
    """Turn an OSC 7 ``file://<host><path>`` payload into a path.

    A payload that is already a bare path is returned unchanged -- some shells
    emit one, and refusing it would cost an observation for no gain.
    """
    candidate = str(value or "").strip().replace("\r", "")
    if not candidate:
        return ""
    if not candidate.lower().startswith("file://"):
        return unquote(candidate) if "%" in candidate else candidate
    return unquote(urlsplit(candidate).path)


def normalize_observed_cwd(cwd: str, shell_kind: str, *, on_windows: bool) -> str:
    """Translate an observed shell cwd into the form the explorer resolves.

    ``on_windows`` is a parameter rather than a read of ``os.name`` so the
    translation can be exercised from either host. A WSL pane reports a Linux
    path for a Windows filesystem, and a ``file:///C:/...`` URL path arrives with
    the leading slash the URL form requires; both name the directory the local
    explorer would.
    """
    candidate = str(cwd or "").strip().replace("\r", "")
    if not candidate:
        return ""

    if on_windows:
        drive_match = _URL_DRIVE_PATTERN.match(candidate)
        if drive_match:
            remainder = (drive_match.group(2) or "").replace("/", "\\")
            drive = drive_match.group(1).upper()
            return f"{drive}:\\" + remainder if remainder else f"{drive}:\\"

        if str(shell_kind or "").strip() == "wsl":
            mount_match = _WSL_MOUNT_PATTERN.match(candidate)
            if mount_match:
                remainder = (mount_match.group(2) or "").replace("/", "\\")
                drive = mount_match.group(1).upper()
                return f"{drive}:\\" + remainder if remainder else f"{drive}:\\"

    return candidate


# A hook that is *typed* at the prompt is echoed by the terminal -- twice, when
# it is sent before the shell has drawn its first prompt -- so a pane GridVibe
# starts itself never types one: the shell is handed its hook at spawn, through
# its own environment or its own argv, and nothing appears in the pane at all.
#
# `$PWD`, `$$`, `$P` and the PowerShell provider path are the shell's own
# values; nothing GridVibe holds is interpolated into any of these strings,
# which is why there is no quoting step here -- the safest form of the
# shell-quoting rule is having nothing to quote.

#: bash reads `PROMPT_COMMAND` from its environment, so this needs no typing and
#: no function. It is evaluated at every prompt, which is when `$PWD` expands.
#:
#: `$PWD` is a printf *argument* and never part of the format string, and the
#: sequence is OSC 9;9 rather than OSC 7 so what travels is raw path text.
#: Neither half is cosmetic. Expanding `$PWD` into the format made a `%` in a
#: directory name a conversion specification, so a pane sitting in
#: `/srv/100%done` reported `/srv/1000one`. And OSC 7 is a URL, so its reader
#: has to percent-decode -- which turns a directory literally named `a%2Fb` into
#: the two-segment path `a/b`. A raw path in a data argument is unambiguous for
#: `%`, `%2F`, `?` and `#` alike, with no encoder to run at every prompt.
_POSIX_PROMPT_COMMAND = 'printf \'\\033]9;9;%s\\033\\\\\' "$PWD"'

#: cmd reads `PROMPT` from its environment. `$e` is ESC, `$P` the path, `$G` the
#: `>`; unlike the other two shells cmd has no seam to wrap, so the default
#: prompt is restated after the sequence.
_CMD_PROMPT = "$e]9;9;$P$e\\$P$G"

#: PowerShell can neither take a prompt from the environment nor define a
#: function through one, but it will run a `-Command` before dropping into its
#: interactive prompt -- an argument, not input, so it is not echoed either. The
#: user's own `prompt` is copied aside and still called.
_POWERSHELL_HOOK = (
    "if (-not (Test-Path Function:_GridVibePrompt)) { "
    "Copy-Item Function:prompt Function:_GridVibePrompt; "
    "function global:prompt { "
    "\"$([char]27)]9;9;"
    "$($ExecutionContext.SessionState.Path.CurrentLocation.ProviderPath)"
    "$([char]27)\\\" + (_GridVibePrompt) } }"
)

#: The remote case is the one that cannot be handed anything at spawn: `sshd`
#: accepts only the environment variables its `AcceptEnv` allows (`LANG`/`LC_*`
#: almost everywhere), and writing an rc file to the remote host is not something
#: a terminal gets to do. So this one *is* typed, and is kept as short as it can
#: be while still covering both prompt mechanisms: it opens with a space, so a
#: shell with the usual `HISTCONTROL` keeps it out of history, appends to an
#: existing `PROMPT_COMMAND` rather than replacing it, and reports the shell pid
#: once so `/proc/<pid>/cwd` has something to read on a shell (zsh, a login
#: shell with its own hook) where the prompt hook does not take.
_REMOTE_HOOK = (
    " _gv(){ printf '\\033]9;9;%s\\033\\\\' \"$PWD\"; }; "
    "[ -n \"$ZSH_VERSION\" ] && precmd_functions+=(_gv) || "
    "PROMPT_COMMAND=\"_gv${PROMPT_COMMAND:+;$PROMPT_COMMAND}\"; _gv; "
    "printf '\\033]777;gridvibe-pid;%s\\033\\\\' $$"
)

#: `wsl.exe` only forwards the environment variables `WSLENV` names.
_WSLENV_VARIABLE = "WSLENV"
_WSL_FORWARDED = "PROMPT_COMMAND"


def shell_integration_environment(
    shell_kind: str,
    environment: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Return the environment entries that install the hook for a local shell.

    Empty for a shell family that cannot take one this way (PowerShell, which
    uses `shell_integration_arguments()` instead) or one GridVibe does not know.
    ``environment`` is the spawn environment as it stands, read only to extend
    an existing ``WSLENV`` rather than replace it.
    """
    kind = str(shell_kind or "").strip()
    if kind == "cmd":
        return {"PROMPT": _CMD_PROMPT}
    if kind in {"posix", ""}:
        return {"PROMPT_COMMAND": _POSIX_PROMPT_COMMAND}
    if kind == "wsl":
        current = str((environment or {}).get(_WSLENV_VARIABLE) or "").strip()
        parts = [part for part in current.split(":") if part]
        if not any(part.split("/", 1)[0] == _WSL_FORWARDED for part in parts):
            parts.append(_WSL_FORWARDED)
        return {
            "PROMPT_COMMAND": _POSIX_PROMPT_COMMAND,
            _WSLENV_VARIABLE: ":".join(parts),
        }
    return {}


def shell_integration_arguments(shell_kind: str) -> List[str]:
    """Return the extra argv that installs the hook, for a shell that needs it."""
    if str(shell_kind or "").strip() == "powershell":
        return ["-NoExit", "-Command", _POWERSHELL_HOOK]
    return []


def remote_shell_integration_command() -> str:
    """Return the one line typed at a remote shell to install the hook."""
    return _REMOTE_HOOK
