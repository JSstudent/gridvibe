"""tmux-backed SSH terminals: the experimental launch option's one owner.

An SSH terminal or agent pane launched with tmux runs inside a named tmux
session on the remote host, so the session outlives the connection: closing
the pane, reconnecting, quitting GridVibe or restoring a workspace detaches
from it and attaches again. The session is the *only* thing GridVibe restores
for such a pane -- not its agent, mode, observed directory or startup command
(see ``tmux_snapshot_fields``).

The whole feature sits behind ``ssh.tmux_sessions`` (App Settings -> Terminal,
off by default). Every consumer asks :func:`tmux_sessions_enabled`; nothing
reads the key directly. Off, an SSH pane behaves exactly as it did before the
feature existed, and no tmux session on any host is ever touched because of it.

Every remote command goes over its own exec channel, never the interactive
one, with a timeout and a capped read. Session names are restricted to
``[A-Za-z0-9_-]`` and every target is written ``'=NAME'`` -- exact match, and
single-quoted so neither tmux's prefix matching nor a shell's ``=word``
expansion (zsh) can reach a different session.
"""

import logging
import re
import secrets
import shlex
import socket
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: What a session name may be. tmux reserves ``.`` and ``:`` in targets, and a
#: name is passed to a remote shell, so anything else is refused at launch and
#: dropped on restore.
TMUX_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

TMUX_EXEC_TIMEOUT = 10.0
TMUX_MAX_OUTPUT_BYTES = 8192

#: Outcomes of :func:`prepare`.
TMUX_MISSING = "missing"
TMUX_ATTACHED = "attached"
TMUX_CREATED = "created"
TMUX_CREATED_HOME = "created_home"

#: The terminal type the attach channel asks for. tmux renders for its
#: *client's* terminal, and plain ``xterm`` would hold every pane inside the
#: session to eight colours.
TMUX_CLIENT_TERM = "xterm-256color"

_PREPARE_EXIT_STATES = {
    0: TMUX_ATTACHED,
    3: TMUX_MISSING,
    10: TMUX_CREATED,
    11: TMUX_CREATED_HOME,
}


class TmuxError(Exception):
    """A tmux command on the remote host failed, with tmux's own message."""


# ==================== The experimental gate ====================


def tmux_sessions_enabled(settings: Any = None) -> bool:
    """Whether the experimental tmux sessions are switched on.

    Read from one captured settings generation; a caller already holding one
    passes it in. The connect path reads it once per connection, so a pane
    attached when the switch is turned off stays attached until its connection
    is replaced.
    """
    if settings is None:
        from web.config import runtime_config

        settings = runtime_config.snapshot()
    return bool(getattr(settings, "ssh_tmux_sessions", False))


# ==================== Names ====================


def generate_session_name() -> str:
    """A fresh GridVibe session name, ``gv-<12 hex chars>``."""
    return f"gv-{secrets.token_hex(6)}"


def normalize_session_name(value: Any) -> str:
    """The name when it is a valid one, else ``""`` -- the restore rule."""
    name = str(value or "").strip() if isinstance(value, str) else ""
    return name if TMUX_NAME_PATTERN.match(name) else ""


def launch_session_name(config: Dict[str, Any], *, enabled: bool, restore: bool) -> str:
    """The tmux session one requested pane launches into, or ``""``.

    A request asks for tmux with ``tmux: true`` (the launcher's "Run in tmux")
    or by naming a session in ``tmux_session`` (a typed name, a preset's, or a
    restored snapshot's). An empty name with the option on is generated here.

    Only SSH panes in terminal or agent mode carry one. With the setting off
    both fields are ignored, as for any field the API does not know. An
    invalid name refuses a launch (``ValueError``) and is dropped on restore,
    which then brings the pane back as a plain SSH terminal.
    """
    if not enabled:
        return ""
    if str(config.get("mode") or "") != "ssh":
        return ""
    if str(config.get("startup_mode") or "terminal") not in {"terminal", "agent"}:
        return ""
    raw_name = config.get("tmux_session")
    requested = config.get("tmux") is True or bool(str(raw_name or "").strip())
    if not requested:
        return ""
    stated = str(raw_name or "").strip() if isinstance(raw_name, str) else ""
    if not stated:
        return generate_session_name()
    name = normalize_session_name(stated)
    if name:
        return name
    if restore:
        logger.warning("Dropping an invalid tmux session name from a restored pane")
        return ""
    raise ValueError(
        "A tmux session name may only use letters, digits, '-' and '_' "
        "(at most 64 characters)."
    )


def preset_tmux_fields(entry: Dict[str, Any]) -> Dict[str, Any]:
    """The tmux launch option a reusable preset keeps for one pane.

    The option and its name, typed or generated alike, so a launch of the
    preset attaches to the session the saved pane ran in -- the same session a
    workspace restore reattaches. Only a preset saved before any launch has no
    name yet; its launch generates one.
    """
    name = normalize_session_name(entry.get("tmux_session"))
    wanted = entry.get("tmux") is True or bool(name)
    if not wanted:
        return {"tmux": False, "tmux_session": ""}
    return {"tmux": True, "tmux_session": name}


def session_key(host: Any, port: Any, username: Any, name: str) -> Tuple[str, str, str, str]:
    """What makes two panes name the same tmux session."""
    return (
        str(host or "").strip().lower(),
        str(port or 22).strip(),
        str(username or "").strip(),
        name,
    )


# ==================== Remote commands ====================


def _quoted_target(name: str, *, window: bool = False) -> str:
    """``'=NAME'`` (a session) or ``'=NAME:'`` (its current window).

    Quoted by hand rather than through ``shlex.quote``, which treats ``=`` as
    safe and would leave the word bare for zsh to expand. The name is already
    validated, so it holds nothing a single quote needs escaping from.
    """
    if not TMUX_NAME_PATTERN.match(name or ""):
        raise TmuxError("Invalid tmux session name")
    return f"'={name}{':' if window else ''}'"


def _shell_directory(directory: str) -> str:
    """A directory as a POSIX shell word, with a leading ``~`` left expandable."""
    directory = str(directory or "").strip()
    if directory == "~":
        return '"$HOME"'
    if directory.startswith("~/"):
        return f'"$HOME"/{shlex.quote(directory[2:])}'
    return shlex.quote(directory)


def _sh(script: str) -> str:
    """Run ``script`` under ``sh``, whatever the user's login shell is."""
    return f"sh -c {shlex.quote(script)}"


def _close_quietly(channel: Any) -> None:
    try:
        channel.close()
    except Exception:
        pass


class _Watchdog:
    """Close ``channel`` -- this one only -- if it is still waiting at the deadline.

    Paramiko waits for a channel request's acknowledgement with no timeout of
    its own, so a host that stops answering while the transport stays up
    would hold the caller for good. Closing the channel releases that wait
    with an error, and ``fired`` tells the caller the error was the deadline.
    The caller cancels the watchdog once the request is done.
    """

    def __init__(self, channel: Any, seconds: float):
        self.fired = threading.Event()
        self._channel = channel
        self._timer = threading.Timer(max(0.0, seconds), self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _expire(self) -> None:
        self.fired.set()
        _close_quietly(self._channel)

    def cancel(self) -> None:
        self._timer.cancel()


def _run(client: Any, command: str, timeout: float = TMUX_EXEC_TIMEOUT) -> Tuple[int, str]:
    """Run one command on its own exec channel: bounded, combined output."""
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TmuxError("tmux did not answer in time")
        return left

    channel = None
    watchdog = None
    try:
        transport = client.get_transport() if client is not None else None
        if transport is None or not transport.is_active():
            raise TmuxError("The SSH connection is not active")
        channel = transport.open_session(timeout=remaining())
        watchdog = _Watchdog(channel, remaining())
        channel.set_combine_stderr(True)
        channel.settimeout(remaining())
        channel.exec_command(command)
        chunks = []
        total = 0
        # The deadline bounds the whole command, not each read: output that
        # trickles in just inside every read's timeout still ends on time.
        while total < TMUX_MAX_OUTPUT_BYTES:
            channel.settimeout(remaining())
            data = channel.recv(min(4096, TMUX_MAX_OUTPUT_BYTES - total))
            if not data:
                break
            chunks.append(data)
            total += len(data)
        while not channel.exit_status_ready():
            remaining()
            time.sleep(0.02)
        status = channel.recv_exit_status()
        return status, b"".join(chunks).decode("utf-8", errors="replace").strip()
    except TmuxError:
        raise
    except socket.timeout as exc:
        raise TmuxError("tmux did not answer in time") from exc
    except Exception as exc:
        # A dropped transport surfaces as SSHException, EOFError or OSError
        # from any step above; every caller handles exactly one failure type.
        if (watchdog is not None and watchdog.fired.is_set()) or time.monotonic() >= deadline:
            raise TmuxError("tmux did not answer in time") from exc
        raise TmuxError(str(exc) or exc.__class__.__name__) from exc
    finally:
        if watchdog is not None:
            watchdog.cancel()
        if channel is not None:
            _close_quietly(channel)


def prepare(client: Any, name: str, directory: str) -> str:
    """Decide attach vs create in one round trip, creating when needed.

    Returns one of ``TMUX_MISSING`` (tmux is not on the non-interactive
    ``PATH``), ``TMUX_ATTACHED`` (the session exists), ``TMUX_CREATED`` (made
    in ``directory``) or ``TMUX_CREATED_HOME`` (made in the home directory
    because ``directory`` is empty or missing on the host). Anything else
    raises :class:`TmuxError` carrying tmux's message.
    """
    session = _quoted_target(name)
    plain = shlex.quote(name)
    script = (
        "command -v tmux >/dev/null 2>&1 || exit 3; "
        f"tmux has-session -t {session} 2>/dev/null && exit 0; "
        f"d={_shell_directory(directory)}; "
        'if [ -n "$d" ] && [ -d "$d" ]; then '
        f'tmux new-session -d -s {plain} -c "$d" && exit 10; '
        "else "
        f"tmux new-session -d -s {plain} && exit 11; "
        "fi; exit 1"
    )
    status, output = _run(client, _sh(script))
    state = _PREPARE_EXIT_STATES.get(status)
    if state is None:
        raise TmuxError(output or f"tmux failed with exit status {status}")
    return state


def attach_command(name: str) -> str:
    return f"tmux attach-session -t {_quoted_target(name)}"


def open_attach_channel(client: Any, name: str, cols: int, rows: int) -> Any:
    """The pane's interactive channel: a PTY whose root process is the tmux client.

    Detaching, or the session's last shell exiting, closes the channel, and
    the ordinary stream end marks the pane disconnected.
    """
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        raise TmuxError("The SSH connection is not active")
    channel = transport.open_session(timeout=TMUX_EXEC_TIMEOUT)
    # Both requests wait for the host's acknowledgement; the watchdog bounds
    # them and is cancelled before the channel becomes the pane's.
    watchdog = _Watchdog(channel, TMUX_EXEC_TIMEOUT)
    try:
        channel.get_pty(term=TMUX_CLIENT_TERM, width=cols, height=rows)
        channel.exec_command(attach_command(name))
    except Exception as exc:
        _close_quietly(channel)
        raise TmuxError(f"Could not attach to the tmux session: {exc}") from exc
    finally:
        watchdog.cancel()
    return channel


def send_line(client: Any, target: str, line: str) -> bool:
    """Type one line into a tmux pane with ``send-keys``, then press Enter.

    ``target`` is an already-quoted tmux target or a pane id such as ``%7``.
    Returns whether tmux accepted it.
    """
    if not line or "\n" in line or "\r" in line:
        return False
    script = (
        f"tmux send-keys -t {target} -l {shlex.quote(line)} && "
        f"tmux send-keys -t {target} Enter"
    )
    try:
        status, output = _run(client, _sh(script))
    except TmuxError as exc:
        logger.warning("tmux send-keys failed: %s", exc)
        return False
    if status != 0:
        logger.warning("tmux send-keys failed: %s", output or status)
        return False
    return True


def session_target(name: str) -> str:
    """The quoted target for the current window of session ``name``."""
    return _quoted_target(name, window=True)


_PANE_ID_PATTERN = re.compile(r"^%\d+$")


def new_window(client: Any, name: str, directory: str = "") -> Tuple[str, bool]:
    """Open a new window in the session, for a relaunch that needs a fresh shell.

    The session is never ended for this, and its other windows are untouched.
    ``directory`` is the stated one, or empty for the pane's current one
    (``pane_current_path``); it gets the same on-host ``[ -d ]`` check as the
    create step. Returns ``(pane_id, in_directory)``: ``in_directory`` is False
    when the window opened in the home directory instead.
    """
    session = _quoted_target(name)
    window = _quoted_target(name, window=True)
    script = (
        f"d={_shell_directory(directory)}; "
        f"[ -n \"$d\" ] || d=$(tmux display-message -p -t {window} "
        "'#{pane_current_path}' 2>/dev/null); "
        'if [ -n "$d" ] && [ -d "$d" ]; then '
        f"tmux new-window -P -F '#{{pane_id}}' -t {window} -c \"$d\" && exit 0; "
        "exit 1; "
        "fi; "
        f"tmux has-session -t {session} 2>/dev/null || exit 1; "
        f"tmux new-window -P -F '#{{pane_id}}' -t {window} && exit 11; "
        "exit 1"
    )
    status, output = _run(client, _sh(script))
    pane_id = next(
        (line.strip() for line in output.splitlines() if _PANE_ID_PATTERN.match(line.strip())),
        "",
    )
    if status not in (0, 11) or not pane_id:
        raise TmuxError(output or f"tmux new-window failed with exit status {status}")
    return pane_id, status == 0


def kill(client: Any, name: str) -> bool:
    """End the session. Only ever asked for by the person, on close."""
    try:
        status, output = _run(client, f"tmux kill-session -t {_quoted_target(name)}")
    except TmuxError as exc:
        logger.warning("tmux kill-session failed: %s", exc)
        return False
    if status != 0:
        logger.warning("tmux kill-session failed: %s", output or status)
        return False
    return True


def current_path(client: Any, name: str) -> str:
    """Where the session's active pane is standing, or ``""``.

    tmux swallows the escape sequences the prompt hook prints, so this is how
    a tmux pane's working directory is read instead.
    """
    try:
        status, output = _run(
            client,
            f"tmux display-message -p -t {_quoted_target(name, window=True)} "
            "'#{pane_current_path}'",
            timeout=3.0,
        )
    except TmuxError as exc:
        logger.debug("Unable to read the tmux pane's directory: %s", exc)
        return ""
    if status != 0:
        return ""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    path = lines[-1] if lines else ""
    return path if path.startswith("/") else ""


# ==================== Notices ====================


def notice(text: str) -> str:
    """A yellow GridVibe line in the pane's output, the shape the others use."""
    return f"\r\n\x1b[33mGridVibe: {text}\x1b[0m\r\n"


MISSING_NOTICE = (
    "tmux was not found on this host's non-interactive PATH, so this pane "
    "opened a plain shell. If tmux is installed (for example in /usr/local/bin "
    "or through Homebrew), make it reachable from a non-interactive shell."
)


# ==================== Relaunch intents ====================
#
# A relaunch (a new agent, no agent, a stated directory) replaces the process
# behind a pane by closing its connection and starting a new one. On a tmux
# pane the new connection attaches to the same session, so the relaunch states
# its intent here first: the next connection opens a new window in the
# session, and the launch line goes there. Taken once, by that connection.

_pending_windows: Dict[str, str] = {}
_pending_lock = threading.Lock()


def request_new_window(session_id: str, directory: str = "") -> None:
    with _pending_lock:
        _pending_windows[str(session_id)] = str(directory or "")


def take_new_window(session_id: str) -> Optional[str]:
    """The directory a pending relaunch asked for (``""`` = current), or None."""
    with _pending_lock:
        return _pending_windows.pop(str(session_id), None)


def forget_session(session_id: str) -> None:
    with _pending_lock:
        _pending_windows.pop(str(session_id), None)


# ==================== Snapshot ====================


def tmux_snapshot_fields(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The reduced launch shape a tmux pane is captured as, or None.

    Only the connection identity, the session name, the launch directory (in
    both directory slots -- it is only ever used to recreate a session that has
    gone) and the title. Everything else is written as a plain SSH terminal's
    default by the caller, so a tmux pane always restores as a terminal that
    attaches, with no agent, mode, startup command or conversation.
    """
    if str(data.get("mode") or "") != "ssh":
        return None
    name = normalize_session_name(data.get("tmux_session"))
    if not name:
        return None
    launch_directory = str(data.get("launch_directory") or data.get("directory") or "")
    return {
        "host": data.get("host"),
        "port": data.get("port"),
        "username": data.get("username"),
        "title": data.get("title"),
        "tmux_session": name,
        "directory": launch_directory,
        "launch_directory": launch_directory,
    }
