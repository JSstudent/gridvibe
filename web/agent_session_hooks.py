"""How a Claude pane tells GridVibe which conversation it is in *now*.

``web/agent_conversations.py`` gives a built-in Claude launch an exact
conversation id before the process starts. That id is only true until the
reader switches conversation inside the TUI -- ``/resume`` from the picker,
``/clear``, a fork -- and nothing about that switch reaches the terminal stream:
Claude's title is a topic summary, never an id. The submitted-line watcher can
see that a switch *may* have happened and forgets the old id, which is safe,
but on its own it leaves the pane with no identity to restore.

Claude Code has a supported way to say it: a ``SessionStart`` hook fires on
startup, resume, clear, compact and fork with the live ``session_id`` on its
stdin. So a built-in Claude launch in a pane whose shell runs natively on this
machine is handed ``--settings`` naming one generated file that registers
exactly that hook. The hook runs GridVibe's own interpreter against
``utils/agent_session_hook.py`` in exec form -- no shell parses the command --
and that script posts the id back over loopback.

Three rules hold it up:

- **The report is owned by one connection.** Every local pane spawn carries a
  fresh random token in its environment, and the connection keeps the same
  token. A report is accepted only while that exact connection is the pane's
  current, unretired transport and the token matches, so a hook from a
  replaced shell can never relabel its replacement.
- **The report is data, never a command.** It is parsed into a canonical UUID
  and published through the same ownership-gated commit a Codex title uses;
  nothing from it is ever interpolated into a launch line except through the
  existing composer's UUID validation.
- **It costs the pane nothing when it fails.** A missing file, an unreachable
  GridVibe or an older CLI that ignores exec-form hooks all degrade to the
  phase-1 behaviour: the switch clears identity and restore starts fresh.

Codex is deliberately not handed a hook. Its hooks run only after the reader
trusts them in Codex's own review screen, and its identity comes from its
title instead (``web/agent_conversations.py``).

WSL and SSH panes are deliberately not handed the hook: the interpreter path
names a program on this machine, and a WSL2 or remote process cannot reach this
machine's loopback without a tunnel.
"""

import json
import logging
import os
import secrets
import sys
from typing import Any, Dict, Mapping, Optional, Tuple

from web.agent_conversations import normalize_conversation_id
from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

CLAUDE_SETTINGS_FILENAME = ".gridvibe_claude_settings.json"
PRODUCTION_CLAUDE_SETTINGS_PATH = os.path.join(BASE_DIR, CLAUDE_SETTINGS_FILENAME)

#: The script the hook runs. A script path rather than ``-m``: the hook's
#: working directory is the user's project, not this install.
HOOK_SCRIPT = os.path.join(BASE_DIR, "utils", "agent_session_hook.py")

#: The per-connection secret a local pane carries into every child.
PANE_TOKEN_VARIABLE = "GRIDVIBE_PANE_TOKEN"

#: Header the hook sends the token in. A custom header also makes a browser
#: preflight the request, on top of the same-origin write guard.
PANE_TOKEN_HEADER = "X-GridVibe-Pane-Token"

#: Providers whose hook may report. Deliberately separate from the registry:
#: the registry advertises a capability, this is the executable allowlist.
HOOK_REPORTING_PROVIDERS = frozenset({"claude"})

#: What ``SessionStart`` says caused it. Every one of them names the session
#: the TUI is in after the event, so every one is an authoritative reading.
HOOK_SOURCES = frozenset({"startup", "resume", "clear", "compact", "fork"})

#: The sources whose session already exists on disk. ``startup`` and
#: ``clear`` name a session Claude writes only on its first prompt.
SAVED_HOOK_SOURCES = frozenset({"resume", "compact", "fork"})

#: Seconds Claude waits for the hook. The script bounds its own request well
#: inside this; the ceiling only matters if the interpreter itself stalls.
HOOK_TIMEOUT_SECONDS = 10

#: Characters a launch line cannot carry inside double quotes in every shell a
#: native pane runs: `"` ends the argument, `$` and a backtick expand in
#: PowerShell and POSIX, `%` expands in cmd.
_UNQUOTABLE_PATH_CHARACTERS = frozenset('"$`%')


class SessionReportError(ValueError):
    """A hook report that is not a well-formed conversation identity."""


def claude_settings_path() -> str:
    """Where the generated settings live, refusing production state in tests.

    The same rule as ``web.mcp_launch.mcp_config_path``: ``run_server`` writes
    this file with a plain ``open()``, and a test calls ``run_server``.
    """
    override = os.environ.get("GRIDVIBE_CLAUDE_SETTINGS_PATH")
    if override:
        return override
    if os.environ.get("GRIDVIBE_TEST_MODE"):
        raise RuntimeError(
            "Refusing the production .gridvibe_claude_settings.json in test mode; "
            "set GRIDVIBE_CLAUDE_SETTINGS_PATH (tests/__init__.py does this)."
        )
    return PRODUCTION_CLAUDE_SETTINGS_PATH


def build_claude_settings(*, interpreter: str, script: str = HOOK_SCRIPT) -> Dict[str, Any]:
    """The whole generated document, as data.

    No ``matcher``: every ``SessionStart`` source names the live session. The
    exec form (``command`` + ``args``) spawns the interpreter directly, so a
    path with spaces or shell metacharacters never meets a shell parser.
    """
    return {
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": interpreter,
                            "args": [script, "--provider", "claude"],
                            "timeout": HOOK_TIMEOUT_SECONDS,
                        }
                    ]
                }
            ]
        }
    }


def write_claude_settings(
    *,
    interpreter: Optional[str] = None,
    path: Optional[str] = None,
) -> str:
    """Rewrite the generated settings. Returns the path, or "" if it could not.

    Rewritten on every start for the reason the MCP config is: the interpreter
    is per-install. A failure costs conversation tracking across in-TUI
    switches and nothing else, so it is logged rather than raised.
    """
    target = path or claude_settings_path()
    document = build_claude_settings(interpreter=interpreter or sys.executable or "python")
    try:
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        logger.warning("Could not write %s: %s", target, exc)
        return ""
    logger.info("Claude session hook settings written to %s", target)
    return target


def available_claude_settings_path(path: Optional[str] = None) -> str:
    """The settings path when it exists and can be quoted on a launch line."""
    try:
        target = str(path if path is not None else claude_settings_path())
    except RuntimeError:
        return ""
    if not target or any(ch in _UNQUOTABLE_PATH_CHARACTERS for ch in target):
        return ""
    return target if os.path.isfile(target) else ""


def claude_settings_fragment(settings_path: Any) -> str:
    """The launch-line fragment naming the generated settings, or ""."""
    target = str(settings_path or "")
    if not target or any(ch in _UNQUOTABLE_PATH_CHARACTERS for ch in target):
        return ""
    return f'--settings "{target}"'


def new_pane_token() -> str:
    """A fresh per-connection secret."""
    return secrets.token_urlsafe(32)


def token_matches(expected: Any, presented: Any) -> bool:
    """Constant-time comparison that refuses an empty or non-text token."""
    if not isinstance(expected, str) or not isinstance(presented, str):
        return False
    if not expected or not presented:
        return False
    return secrets.compare_digest(expected, presented)


def parse_session_report(payload: Any) -> Tuple[str, str, bool]:
    """Return ``(provider, conversation_id, saved)`` from one report, or raise.

    A report about a subagent names a session that is not the pane's, so it is
    refused outright rather than published. ``saved`` is whether the session
    already exists on disk: a resumed, compacted or forked one does, a started
    or cleared one does not until its first prompt.
    """
    if not isinstance(payload, Mapping):
        raise SessionReportError("report must be an object")
    provider = str(payload.get("provider") or "").strip().lower()
    if provider not in HOOK_REPORTING_PROVIDERS:
        raise SessionReportError("provider does not report sessions")
    if payload.get("agent_id"):
        raise SessionReportError("subagent sessions are not the pane's")
    source = payload.get("source")
    normalized_source = str(source or "").strip().lower()
    if source is not None and normalized_source not in HOOK_SOURCES:
        raise SessionReportError("unknown session source")
    conversation_id = normalize_conversation_id(payload.get("conversation_id"))
    if not conversation_id:
        raise SessionReportError("conversation id is invalid")
    return provider, conversation_id, normalized_source in SAVED_HOOK_SOURCES
