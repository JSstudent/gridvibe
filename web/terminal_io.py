"""Terminal connection plumbing: registries, streams, startup, resize.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Owns the live
connection registry (``ssh_connections``), the rolling replay buffers, the
SSH/PTY output pump threads, the startup sequence, resize/input plumbing,
runtime agent-command tracking, and the SSH/local/WSL session connectors.
``web.api`` re-exports every name for backwards compatibility.
"""

import codecs
import logging
import os
import re
import select
import shlex
import socket
import struct
import subprocess
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from sessions.manager import SessionStatus
from web.agents import (
    AGENT_REGISTRY,
    _compose_agent_startup_command,
    _find_wsl_executable,
    _powershell_single_quote,
)
from web.app import session_manager, socketio
from web.config import runtime_config
from web.explorer import (
    _evict_all_pooled_ssh_clients,
    _evict_pooled_ssh_client,
    _is_browser_session,
    _is_explorer_session,
)
from web.hostkeys import _apply_host_key_policy
from web.terminal_cwd import (
    CWD_EVENT_DIRECTORY,
    CWD_EVENT_SHELL_PID,
    latest_event,
    normalize_observed_cwd,
    parse_cwd_events,
    remote_shell_integration_command,
    shell_integration_arguments,
    shell_integration_environment,
)
from web.workspaces import DEFAULT_WORKSPACE_ID, normalize_workspace_id, workspace_room

try:
    import pty
except ImportError:  # pragma: no cover - not available on native Windows
    pty = None

try:
    import fcntl
    import termios
except ImportError:  # pragma: no cover - not available on native Windows
    fcntl = None
    termios = None

try:
    import paramiko
except ImportError:  # pragma: no cover - handled at runtime when dependency is missing
    paramiko = None

try:
    from winpty import PtyProcess as WinPtyProcess
except ImportError:  # pragma: no cover - Windows-only optional dependency
    WinPtyProcess = None

logger = logging.getLogger(__name__)

WINDOWS_DEVICE_ATTRIBUTES_RESPONSE = "[?1;2c"

# Store active SSH connections and buffered output
ssh_connections: Dict[str, Dict[str, Any]] = {}
TERMINAL_OUTPUT_BUFFER_MAX_CHARS = 50000


class _OutputBuffer(deque):
    """Chunk deque that carries its own character total.

    The buffer is bounded by total *characters*, not by chunk count, so the
    number of chunks scales inversely with chunk size — 1-character keystroke
    echoes (the ``read(1)`` fallback path) fill it with 50 000 entries. Re-summing
    that on every append, while holding the process-wide ``connection_lock``,
    cost ~2.4 ms per character and serialised every other pane behind it (audit
    F3). The trim loop already computes the exact deltas, so the running total
    only has to be carried alongside the chunks.
    """

    __slots__ = ("total_chars",)

    def __init__(self, chunks=()):
        super().__init__(chunks)
        self.total_chars = sum(len(chunk) for chunk in self)


# Rolling replay buffers kept as chunk deques so a busy pane appends cheaply
# instead of re-copying the whole tail on every output chunk; join only at
# replay time via _get_buffered_terminal_output.
session_output_buffers: Dict[str, Deque[str]] = {}
client_joined_sessions: Dict[str, set[str]] = {}
_MAX_TRACKED_SOCKET_CLIENTS = 1000
# Lock ordering: connection_lock may be taken before session_manager.lock
# (e.g. re-validating a session inside the lock in the connectors), never the
# other way around — nothing may call into connection_lock while holding
# session_manager.lock.
connection_lock = threading.RLock()


def _broadcast_session_status(session_id: str):
    """Emit the latest session status to clients joined to the session room.

    Room-scoped (finding 1.1 step 3) so status payloads are not broadcast to
    every connected socket; clients join the room via `join_session` for each
    pane they display, and `join_session` itself replies with the current
    status.
    """
    # Snapshot under the lock, emit after: a slow client write must not stall
    # every thread waiting on the manager lock (same rationale as finding 2.4
    # for connection_lock).
    with session_manager.lock:
        session = session_manager.sessions.get(session_id)
        payload = session.to_dict() if session else None
    if payload is not None:
        socketio.emit('session_status', payload, room=session_id)


def _broadcast_session_groups_updated(
    reason: str = "",
    group_id: str = "",
    workspace_id: Optional[str] = None,
):
    """Notify open terminal windows that the set of session groups changed.

    Lets the frontend refresh on push instead of relying on its old 3-second
    reconciliation poll (that poll now only runs as a slow fallback while the
    Socket.IO connection is down). This is a pure UI event: the
    restore-after-restart snapshot is no longer written here — only the
    autosave timer and the explicit Save Workspace action capture it (10.5
    hardening), so transient mid-event shapes never reach the snapshot.
    """
    resolved_group_id = str(group_id or "").strip()
    if workspace_id is None and resolved_group_id:
        with session_manager.lock:
            group = session_manager.groups.get(resolved_group_id)
            workspace_id = group.workspace_id if group is not None else None
    resolved_workspace_id = normalize_workspace_id(
        workspace_id if workspace_id is not None else DEFAULT_WORKSPACE_ID
    )
    payload = {
        "workspace_id": resolved_workspace_id,
        "reason": str(reason or ""),
    }
    if resolved_group_id:
        payload["group_id"] = resolved_group_id
    socketio.emit(
        'session_groups_updated',
        payload,
        room=workspace_room(resolved_workspace_id),
    )


def _cache_terminal_output(session_id: str, output: str):
    """Keep a short rolling output buffer for late-joining clients."""
    if not output:
        return
    with connection_lock:
        buffer = session_output_buffers.get(session_id)
        if not isinstance(buffer, _OutputBuffer):
            # Adopts a plain deque left by an older path (or a test) by paying
            # the one-time sum, rather than discarding the buffered tail.
            buffer = _OutputBuffer(buffer or ())
            session_output_buffers[session_id] = buffer
        buffer.append(output)
        buffer.total_chars += len(output)
        while buffer and buffer.total_chars > TERMINAL_OUTPUT_BUFFER_MAX_CHARS:
            excess = buffer.total_chars - TERMINAL_OUTPUT_BUFFER_MAX_CHARS
            head = buffer[0]
            if len(head) <= excess:
                buffer.popleft()
                buffer.total_chars -= len(head)
            else:
                buffer[0] = head[excess:]
                buffer.total_chars = TERMINAL_OUTPUT_BUFFER_MAX_CHARS


def _get_buffered_terminal_output(session_id: str) -> str:
    """Join the buffered output chunks for one session into a replay string."""
    with connection_lock:
        buffer = session_output_buffers.get(session_id)
        return "".join(buffer) if buffer else ""


def _clear_client_joined_sessions(client_id: str):
    """Forget which session buffers have already been replayed to one client."""
    with connection_lock:
        client_joined_sessions.pop(client_id, None)


def _clear_terminal_output_buffer(session_id: str):
    """Drop the buffered replay output for one terminal session."""
    with connection_lock:
        session_output_buffers[session_id] = _OutputBuffer()


def _close_ssh_connection(session_id: str, clear_buffer: bool = True, *, expected=None):
    """Close and remove a single SSH connection."""
    with connection_lock:
        if expected is not None and ssh_connections.get(session_id) is not expected:
            return
        connection = ssh_connections.pop(session_id, None)
        if connection is not None:
            connection['retired'] = True
        if clear_buffer:
            session_output_buffers.pop(session_id, None)

    _shutdown_connection(connection)
    _evict_pooled_ssh_client(session_id)


def _shutdown_connection(connection: Optional[Dict[str, Any]]):
    """Close one SSH or local terminal connection payload."""
    if not connection:
        return
    connection['retired'] = True

    channel = connection.get("channel")
    client = connection.get("client")
    process = connection.get("process")
    pty_process = connection.get("pty_process")
    master_fd = connection.get("master_fd")
    stdin_handle = connection.get("stdin")
    stdout_handle = connection.get("stdout")

    try:
        if channel is not None:
            channel.close()
    except Exception:
        pass

    try:
        if client is not None:
            client.close()
    except Exception:
        pass

    for handle in (stdin_handle, stdout_handle):
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass

    try:
        if master_fd is not None:
            os.close(master_fd)
    except OSError:
        pass

    try:
        if pty_process is not None:
            pty_process.close(True)
    except Exception:
        pass

    if process is not None:
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=1)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _close_displaced_sessions(group_id: str, session_ids: Iterable[str]) -> List[str]:
    """Close the transports of panes an atomic relaunch already displaced.

    ``SessionManager.install_session_group`` removes the displaced records and
    publishes the replacements inside one lock hold (MW-07); their connections
    are closed here afterwards, with no shared lock held (guardrail 2). This
    replaced ``_replace_group_sessions``, which tore the old panes down first
    and left the group empty for as long as the teardown took.
    """
    removed_session_ids = [str(session_id) for session_id in session_ids]
    if not removed_session_ids:
        return []

    for session_id in removed_session_ids:
        _close_ssh_connection(session_id, clear_buffer=True)

    logger.info(
        "Replaced session group group_id=%s removed_sessions=%s",
        group_id,
        removed_session_ids,
    )
    return removed_session_ids


def _close_all_ssh_connections(clear_buffers: bool = True):
    """Close all active SSH connections."""
    with connection_lock:
        session_ids = list(ssh_connections.keys())
        if clear_buffers:
            session_output_buffers.clear()

    for session_id in session_ids:
        _close_ssh_connection(session_id, clear_buffer=not clear_buffers)

    # Explorer-only sessions have no ssh_connections entry, so flush the
    # pooled explorer transports as well.
    _evict_all_pooled_ssh_clients()


TERMINAL_WRITE_TIMEOUT = 5.0


def _send_connection_input(connection: Dict[str, Any], input_data: str):
    """Serialize complete writes; never retry a command after a partial failure."""
    deadline = time.monotonic() + TERMINAL_WRITE_TIMEOUT
    with connection_lock:
        write_lock = connection.setdefault('write_lock', threading.Lock())
    if not write_lock.acquire(timeout=TERMINAL_WRITE_TIMEOUT):
        raise TimeoutError('Terminal input is busy; input was not sent')
    try:
        if connection.get('retired'):
            raise OSError('Terminal connection closed')
        pty_process = connection.get('pty_process')
        if pty_process is not None:
            pty_process.write(input_data)
            return
        encoded = input_data.encode('utf-8')
        offset = 0
        while offset < len(encoded):
            if connection.get('retired'):
                raise OSError('Terminal connection closed during input')
            if time.monotonic() >= deadline:
                raise TimeoutError('Terminal input timed out; input may be incomplete')
            try:
                if connection.get('kind') == 'ssh':
                    channel = connection['channel']
                    if channel.closed:
                        raise OSError('SSH channel closed during input')
                    if not channel.send_ready():
                        time.sleep(0.01)
                        continue
                    count = channel.send(encoded[offset:offset + 4096])
                elif connection.get('master_fd') is not None:
                    fd = connection['master_fd']
                    if not select.select([], [fd], [], 0.05)[1]:
                        continue
                    count = os.write(fd, encoded[offset:offset + 4096])
                else:
                    handle = connection.get('stdin')
                    if handle is None:
                        raise RuntimeError('Connection does not accept input')
                    count = handle.write(encoded[offset:])
                    handle.flush()
            except (BlockingIOError, socket.timeout):
                continue
            if not isinstance(count, int) or count <= 0:
                raise OSError('Terminal closed before all input was sent')
            offset += count
    finally:
        write_lock.release()


def _terminal_cwd_probe_command(connection: Dict[str, Any], marker_start: str, marker_end: str) -> str:
    """Return a shell command that prints the current directory between markers."""
    shell_kind = str(connection.get("shell_kind") or "").strip()
    newline = "\r" if connection.get("pty_process") is not None and os.name == "nt" else "\n"

    if shell_kind == "powershell":
        return f'Write-Output "{marker_start}$((Get-Location).ProviderPath){marker_end}"{newline}'
    if shell_kind == "cmd":
        return f"echo {marker_start}%CD%{marker_end}{newline}"
    if shell_kind == "wsl":
        return (
            f"printf '{marker_start}%s{marker_end}\\n' "
            f'"$(wslpath -w "$PWD" 2>/dev/null || printf \'%s\' "$PWD")"{newline}'
        )
    return f"printf '{marker_start}%s{marker_end}\\n' \"$PWD\"{newline}"


def _extract_terminal_cwd_from_buffer(buffer: str, marker_start: str, marker_end: str) -> Optional[str]:
    """Extract the last cwd marker payload from a terminal output buffer."""
    matches = re.findall(
        f"{re.escape(marker_start)}(.*?){re.escape(marker_end)}",
        buffer,
        flags=re.DOTALL,
    )
    for raw_value in reversed(matches):
        candidate = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw_value)
        candidate = candidate.replace("\r", "").strip()
        if candidate and "\n" not in candidate and marker_start not in candidate and marker_end not in candidate:
            return candidate
    return None


def _normalize_probed_local_cwd(cwd: str, shell_kind: str) -> str:
    """Translate a probed shell cwd into the local filesystem form explorer expects."""
    return normalize_observed_cwd(cwd, shell_kind, on_windows=os.name == "nt")


#: Where an answer to "where is this pane now" came from. Reported alongside the
#: directory, because a caller that cannot tell an observation from an
#: assumption is exactly what made one gesture open two different roots on two
#: different days.
CWD_SOURCE_SHELL_INTEGRATION = "shell_integration"
CWD_SOURCE_PROCESS = "process"
CWD_SOURCE_PROBE = "probe"
CWD_SOURCE_LAUNCH = "launch"

#: Bounds for the corroboration read on a remote pane's own transport.
REMOTE_CWD_READ_TIMEOUT = 3.0
REMOTE_CWD_MAX_OUTPUT_BYTES = 4096


def _observe_terminal_output_cwd(
    session_id: str,
    connection: Dict[str, Any],
    output: str,
) -> None:
    """Read a pane's working directory out of the output it just produced.

    Source A of the three: the prompt hook installed at startup emits the cwd on
    every prompt, so the value is already known when a route asks, no write ever
    goes to the shell, and a pane running an agent keeps reporting the directory
    the agent was started in (the shell emits nothing while the agent holds the
    terminal).

    This runs on the pane's own pump thread -- the only writer of the residue --
    and the parse deliberately takes no lock: a per-chunk scan under
    ``connection_lock`` would sit in front of every other pane's output
    (guardrail 2). Publishing what it found is the part that needs the lock,
    and it is ``_publish_observed_cwd``'s. The chunk is still cached and
    replayed verbatim; this only observes it.
    """
    residue = str(connection.get("cwd_residue") or "")
    if "\x1b" not in output and not residue:
        return

    events, residue = parse_cwd_events(output, residue)
    connection["cwd_residue"] = residue
    if not events:
        return

    shell_pid = latest_event(events, CWD_EVENT_SHELL_PID)
    if shell_pid:
        connection["shell_pid"] = shell_pid

    reported = latest_event(events, CWD_EVENT_DIRECTORY)
    if not reported:
        return

    directory = normalize_observed_cwd(
        reported,
        str(connection.get("shell_kind") or ""),
        # A remote pane's paths are the remote host's, whatever this host is.
        on_windows=connection.get("kind") != "ssh" and os.name == "nt",
    )
    if not directory:
        return

    if _publish_observed_cwd(session_id, connection, directory):
        _broadcast_session_status(session_id)


def _publish_observed_cwd(
    session_id: str,
    connection: Dict[str, Any],
    directory: str,
) -> bool:
    """Write an observed directory back, but only while its connection is current.

    The parse above belongs to one connection entry, and a retargeting -- a
    shell switch, a mode switch -- clears ``current_directory`` and then
    replaces or removes that entry. The retiring shell's last prompt can still
    be in flight at that moment, so publishing on the strength of the parse
    alone puts the old shell's directory back *after* the clear that
    deliberately took it away, and the pane then answers "where am I?" with a
    directory nothing live is standing in. The connection that produced the
    sequence has to still be the registry's entry for the session, and
    ``is`` is the test: a replacement carrying the same shell kind is a
    different shell.

    The check and the write are one lock hold in the allowed
    ``connection_lock`` -> ``SessionManager.lock`` order, because a check the
    write does not sit inside is the same race one step later. Both are
    in-memory; the broadcast is the caller's, after every lock is released.
    """
    with connection_lock:
        if ssh_connections.get(session_id) is not connection:
            return False
        session = session_manager.get_session(session_id)
        if session is None:
            return False
        if str(getattr(session, "current_directory", "") or "") == directory:
            return False
        session_manager.update_session_metadata(session_id, current_directory=directory)
        return True


def _local_process_cwd(connection: Dict[str, Any]) -> str:
    """Read a local pane's cwd from the OS, when the OS can answer (source B)."""
    if os.name == "nt":
        # No /proc, and reading a Windows process's PEB needs a dependency
        # GridVibe does not carry, so a Windows pane rests on source A (D1).
        return ""
    process = connection.get("process")
    pid = getattr(process, "pid", None)
    if not pid:
        return ""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _remote_process_cwd(connection: Dict[str, Any]) -> str:
    """Read a remote pane's cwd over a second exec channel (source B).

    Never on the interactive channel, so it stays safe while an agent is
    running. The *drain* carries the bound rather than a ``| head -c`` pipeline,
    which would report head's exit status and turn a failed remote command into
    an empty successful one.
    """
    pid = str(connection.get("shell_pid") or "")
    client = connection.get("client")
    if not pid.isdigit() or client is None:
        return ""

    transport = client.get_transport()
    if transport is None or not transport.is_active():
        return ""

    channel = None
    try:
        channel = transport.open_session(timeout=REMOTE_CWD_READ_TIMEOUT)
        channel.settimeout(REMOTE_CWD_READ_TIMEOUT)
        channel.exec_command(f"readlink /proc/{pid}/cwd")
        chunks = []
        total = 0
        while total < REMOTE_CWD_MAX_OUTPUT_BYTES:
            data = channel.recv(min(4096, REMOTE_CWD_MAX_OUTPUT_BYTES - total))
            if not data:
                break
            chunks.append(data)
            total += len(data)
        if channel.exit_status_ready() and channel.recv_exit_status() != 0:
            return ""
        return b"".join(chunks).decode("utf-8", errors="ignore").strip()
    except Exception as exc:
        logger.debug("Unable to read the remote working directory: %s", exc)
        return ""
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass


def _process_reported_cwd(connection: Dict[str, Any]) -> str:
    """Return the OS's own answer for one pane's shell process, or ""."""
    if connection.get("kind") == "ssh":
        return _remote_process_cwd(connection)
    return _local_process_cwd(connection)


def effective_directory(
    session_id: str,
    session: Any,
    *,
    allow_probe: bool = False,
) -> Tuple[str, str]:
    """Answer "where is this pane now", and say where the answer came from.

    Sources in order: the shell-integration observation (A), the OS's own read
    of the pane's shell process (B), and -- only when the caller explicitly
    allows it -- the marker probe (C), which types at the prompt and is refused
    on an agent pane. The launch directory is the last answer and is reported as
    ``CWD_SOURCE_LAUNCH``, so a caller can tell it apart from an observation
    instead of silently presenting an assumption as a fact.
    """
    observed = str(getattr(session, "current_directory", "") or "").strip()
    if observed:
        return observed, CWD_SOURCE_SHELL_INTEGRATION

    with connection_lock:
        connection = ssh_connections.get(session_id)
    if connection is not None:
        # Outside the lock: a remote read opens a channel, and no network work
        # belongs inside a shared lock.
        process_cwd = _process_reported_cwd(connection).strip()
        if process_cwd:
            return process_cwd, CWD_SOURCE_PROCESS

    if allow_probe and str(getattr(session, "startup_mode", "") or "") != "agent":
        probed = _resolve_live_terminal_cwd(session_id, session)
        if probed:
            return probed, CWD_SOURCE_PROBE

    return str(getattr(session, "directory", "") or ""), CWD_SOURCE_LAUNCH


def _resolve_live_terminal_cwd(session_id: str, session: Any, timeout: float = 0.75) -> Optional[str]:
    """Probe an active terminal shell for its current working directory.

    Best effort by construction: the probe *types* a marker command at the
    pane's prompt and reads the marker back out of the rolling output buffer,
    so it answers only while the shell is idle. A caller that cannot act on
    "unknown" must not use it.

    An agent pane is refused outright. There is no shell prompt behind a
    running agent, so the probe line would be typed into the agent's own input
    box -- observation must never write something the user did not ask for.
    """
    if str(getattr(session, "startup_mode", "") or "") == "agent":
        logger.debug("Skipping terminal cwd probe for agent pane %s", session_id)
        return None

    with connection_lock:
        connection = ssh_connections.get(session_id)

    if not connection:
        return None

    marker = uuid.uuid4().hex
    marker_start = f"__GRIDVIBE_CWD_{marker}_START__"
    marker_end = f"__GRIDVIBE_CWD_{marker}_END__"
    command = _terminal_cwd_probe_command(connection, marker_start, marker_end)

    try:
        _send_connection_input(connection, command)
    except Exception as exc:
        logger.debug("Unable to probe terminal cwd for %s: %s", session_id, exc)
        return None

    deadline = time.time() + max(0.05, timeout)
    while time.time() < deadline:
        buffer = _get_buffered_terminal_output(session_id)
        cwd = _extract_terminal_cwd_from_buffer(buffer, marker_start, marker_end)
        if cwd:
            shell_kind = str(connection.get("shell_kind") or "").strip()
            if getattr(session, "mode", "") == "wsl":
                return _normalize_probed_local_cwd(cwd, shell_kind)
            return cwd
        time.sleep(0.05)

    return None


def _resize_connection(connection: Dict[str, Any], cols: Any, rows: Any):
    """Resize an active remote or local terminal session."""
    cols = max(8, min(int(cols), 400))
    rows = max(8, min(int(rows), 200))
    kind = connection.get("kind")

    if kind == "ssh":
        channel = connection.get("channel")
        if channel is None:
            raise RuntimeError("SSH channel is unavailable")
        channel.resize_pty(width=cols, height=rows)
        return

    pty_process = connection.get("pty_process")
    if pty_process is not None:
        pty_process.setwinsize(rows, cols)
        return

    master_fd = connection.get("master_fd")
    if master_fd is None or fcntl is None or termios is None:
        return

    assert fcntl is not None
    assert termios is not None
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize) # type: ignore


def _drain_until_prompt(
    session_id: str,
    connection: Dict[str, Any],
    timeout: float = 10.0,
):
    """Wait for the shell to emit initial output before sending startup commands.

    WSL takes noticeably longer than cmd/PowerShell to boot.  If the startup
    command is written to WinPty before the Linux shell is running, WinPty's
    console echoes the raw characters *and* the shell echoes them again once it
    starts, producing a visible duplicate.  Draining output first ensures the
    shell has started before the startup sequence writes any input.
    """
    pty_process = connection.get("pty_process")
    if pty_process is None:
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            output = pty_process.read(4096)
        except EOFError:
            return

        if output:
            _publish_ssh_terminal_output(session_id, output, connection)
            time.sleep(0.15)
            return

        time.sleep(0.1)


def _startup_directories(session: Any) -> Tuple[str, str]:
    """Return ``(target, fallback)`` for the startup sequence's ``cd``.

    D3: a pane comes back where it *was*, not where it started. A reconnected
    SSH pane whose shell dropped, and a restored one whose snapshot already
    carries the observed directory, both replay the same value -- deciding it
    only for one of them would leave a reconnect and a restore of the same pane
    disagreeing about which directory they replay.

    A directory that no longer resolves is the new failure mode this trade
    buys, so the launch directory rides along as the fallback and the ``cd``
    itself is written to try the second when the first fails. ``fallback`` is
    empty when there is nothing to fall back to -- either the pane was never
    observed, or the observation is the launch directory anyway.
    """
    launch_directory = str(getattr(session, "directory", "") or "")
    observed = str(getattr(session, "current_directory", "") or "").strip()
    if not observed or observed == launch_directory:
        return launch_directory, ""
    return observed, launch_directory


SSH_STARTUP_SCRUB_TIMEOUT = 2.0
SSH_STARTUP_SCRUB_MAX_CHARS = 64 * 1024
_SSH_STARTUP_READY_HEAD = "\x1b]777;gridvibe-startup-ready;"
_SSH_STARTUP_READY_TAIL = "\x1b\\"


def _arm_ssh_startup_scrub(
    connection: Dict[str, Any], commands: Iterable[str]
) -> str:
    """Arm one bounded scrub and return its invisible completion command.

    Only GridVibe's own SSH bootstrap lines are registered. The normal stream
    stays untouched, as do local shells and the user's configured startup
    command. A random OSC marker gives the reader an exact point after which
    every registered line has been processed by the remote shell.
    """
    token = uuid.uuid4().hex
    marker = f"{_SSH_STARTUP_READY_HEAD}{token}{_SSH_STARTUP_READY_TAIL}"
    marker_command = (
        f" printf '\\033]777;gridvibe-startup-ready;{token}\\033\\\\'"
    )
    connection["ssh_startup_scrub"] = {
        "commands": tuple(commands) + (marker_command,),
        "marker": marker,
        "pending": "",
        "deadline": time.monotonic() + SSH_STARTUP_SCRUB_TIMEOUT,
    }
    return marker_command


def _scrub_ssh_startup_output(
    connection: Dict[str, Any],
    output: str = "",
    *,
    now: Optional[float] = None,
    force: bool = False,
) -> str:
    """Hide exact SSH bootstrap echo lines, failing open on every uncertainty."""
    state = connection.get("ssh_startup_scrub")
    if not isinstance(state, dict):
        return output

    pending = f"{state.get('pending') or ''}{output}"
    state["pending"] = pending
    marker = str(state.get("marker") or "")
    marker_at = pending.find(marker) if marker else -1

    expired = (time.monotonic() if now is None else now) >= float(
        state.get("deadline") or 0.0
    )
    if marker_at < 0:
        if not force and not expired and len(pending) <= SSH_STARTUP_SCRUB_MAX_CHARS:
            return ""
        # A shell that did not understand the marker must never leave its MOTD
        # or diagnostics hidden. Drop the state and release the original bytes.
        connection.pop("ssh_startup_scrub", None)
        return pending

    marker_end = marker_at + len(marker)
    before_marker = pending[:marker_at]
    after_marker = pending[marker_end:]
    commands = tuple(
        command
        for command in state.get("commands", ())
        if isinstance(command, str) and command and "\n" not in command and "\r" not in command
    )
    cleaned = "".join(
        line
        for line in before_marker.splitlines(keepends=True)
        if not any(line.rstrip("\r\n").endswith(command) for command in commands)
    )
    connection.pop("ssh_startup_scrub", None)
    return f"{cleaned}{after_marker}"


def _run_startup_sequence(connection: Dict[str, Any], session: Any):
    """Change into the target directory and optionally run an initial command."""
    shell_kind = connection.get("shell_kind")
    if not shell_kind:
        shell_kind = (
            "cmd"
            if connection.get("kind") == "local" and connection.get("pty_process") is not None and os.name == "nt"
            else "posix"
        )
    newline = "\r" if connection.get("pty_process") is not None and os.name == "nt" else "\n"

    if shell_kind == "wsl":
        time.sleep(0.25)

    ssh_startup_commands = []

    # Only a *remote* shell is sent the hook: a local pane was handed it at
    # spawn (`_local_shell_integration`), where nothing is echoed into the pane.
    # `sshd` forwards only the environment its `AcceptEnv` allows, so there is
    # no equivalent here -- and the line is typed before the shell has drawn its
    # first prompt, which is why it is short. `terminal.shell_integration` is a
    # real kill switch because the hook mutates the user's prompt; observation
    # itself stays on either way, so a shell that emits OSC 7 from the user's
    # own configuration is still read.
    if connection.get("kind") == "ssh" and runtime_config.terminal_shell_integration:
        hook_command = remote_shell_integration_command()
        ssh_startup_commands.append(hook_command)
        _send_connection_input(connection, f"{hook_command}{newline}")
        time.sleep(0.15)

    startup_directory, fallback_directory = _startup_directories(session)
    if startup_directory and not connection.get("launch_cwd_applied"):
        target_directory = _normalize_local_directory(startup_directory, shell_kind)
        fallback_target = (
            _normalize_local_directory(fallback_directory, shell_kind)
            if fallback_directory
            else ""
        )
        if shell_kind == "cmd":
            escaped_directory = target_directory.replace('"', '""')
            command = f'cd /d "{escaped_directory}"'
            if fallback_target:
                escaped_fallback = fallback_target.replace('"', '""')
                command = f'{command} 2>nul || cd /d "{escaped_fallback}"'
        elif shell_kind == "powershell":
            quoted = _powershell_single_quote(target_directory)
            command = f"Set-Location -LiteralPath {quoted}"
            if fallback_target:
                # `Test-Path` rather than a trailing `-ErrorAction`: a failed
                # Set-Location writes a red error into the pane before the
                # fallback runs, and the pane's first line should not look
                # like the reconnect broke.
                command = (
                    f"if (Test-Path -LiteralPath {quoted}) {{ {command} }}"
                    f" else {{ Set-Location -LiteralPath"
                    f" {_powershell_single_quote(fallback_target)} }}"
                )
        else:
            command = f"cd {shlex.quote(target_directory)}"
            if fallback_target:
                command = f"{command} 2>/dev/null || cd {shlex.quote(fallback_target)}"
        if connection.get("kind") == "ssh":
            ssh_startup_commands.append(command)
        _send_connection_input(connection, f"{command}{newline}")
        time.sleep(0.15)

    if ssh_startup_commands:
        marker_command = _arm_ssh_startup_scrub(connection, ssh_startup_commands)
        _send_connection_input(connection, f"{marker_command}{newline}")

    startup_command = _compose_agent_startup_command(session)
    if startup_command:
        _send_connection_input(connection, f"{startup_command}{newline}")


def _connection_is_current(session_id, connection):
    with connection_lock:
        return connection is not None and ssh_connections.get(session_id) is connection


def _connection_status(session_id, connection, status, error_message=None):
    with connection_lock:
        if not _connection_is_current(session_id, connection):
            return
        session = session_manager.get_session(session_id)
        if session is None or _is_explorer_session(session) or _is_browser_session(session):
            return
        session_manager.update_session_status(session_id, status, error_message=error_message)
    _broadcast_session_status(session_id)


def _begin_connection(session_id):
    connection = {'write_lock': threading.Lock()}
    with connection_lock:
        if session_manager.get_session(session_id) is None:
            return None
        old = ssh_connections.get(session_id)
        if old is not None:
            old['retired'] = True
        ssh_connections[session_id] = connection
    _shutdown_connection(old)
    return connection


def _finalize_stream(session_id: str, connection=None):
    """Only the reader owning the current transport may retire the pane."""
    with connection_lock:
        if not _connection_is_current(session_id, connection):
            return
        session = session_manager.get_session(session_id)
        if (session and not _is_explorer_session(session) and not _is_browser_session(session)
                and session.status not in {SessionStatus.ERROR, SessionStatus.DISCONNECTED}):
            session_manager.update_session_status(session_id, SessionStatus.DISCONNECTED)
        ssh_connections.pop(session_id, None)
        session_output_buffers.pop(session_id, None)
        connection['retired'] = True
    _broadcast_session_status(session_id)
    _shutdown_connection(connection)
    _evict_pooled_ssh_client(session_id)


SSH_STREAM_RECV_TIMEOUT = 0.5


def _publish_ssh_terminal_output(session_id: str, output: str, connection=None) -> None:
    """Publish only output belonging to the current connection."""
    if not output:
        return
    with connection_lock:
        if not _connection_is_current(session_id, connection):
            return
        _cache_terminal_output(session_id, output)
    socketio.emit('terminal_output', {'session_id': session_id, 'data': output}, room=session_id)


def _decoded_terminal_output(session_id, connection, data=b'', *, final=False):
    decoder = connection.setdefault('decoder', codecs.getincrementaldecoder('utf-8')(errors='replace'))
    output = data if isinstance(data, str) else decoder.decode(data, final=final)
    _observe_terminal_output_cwd(session_id, connection, output)
    if connection.get('kind') == 'ssh':
        output = _scrub_ssh_startup_output(connection, output, force=final)
    _publish_ssh_terminal_output(session_id, output, connection)


def _stream_ssh_output(session_id: str, connection=None):
    """Read from the captured channel, never from its replacement."""
    if connection is None:
        with connection_lock:
            connection = ssh_connections.get(session_id)
    if connection is None:
        return
    try:
        channel = connection['channel']
        channel.settimeout(SSH_STREAM_RECV_TIMEOUT)
        while _connection_is_current(session_id, connection) and not channel.closed:
            try:
                data = channel.recv(4096)
            except socket.timeout:
                _publish_ssh_terminal_output(session_id, _scrub_ssh_startup_output(connection), connection)
                if channel.exit_status_ready():
                    break
                continue
            if not data:
                break
            _decoded_terminal_output(session_id, connection, data)
        _decoded_terminal_output(session_id, connection, final=True)
    except Exception as exc:
        if _connection_is_current(session_id, connection):
            _connection_status(session_id, connection, SessionStatus.ERROR, str(exc))
    finally:
        _finalize_stream(session_id, connection)


def _stream_local_output(session_id: str, connection=None):
    """Read terminal output from one captured PTY-backed local shell."""
    if connection is None:
        with connection_lock:
            connection = ssh_connections.get(session_id)
    if connection is None:
        return
    try:
        process = connection.get('process')
        pty_process = connection.get('pty_process')
        master_fd = connection.get('master_fd')
        stdout_handle = connection.get('stdout')
        while _connection_is_current(session_id, connection):
            if pty_process is not None:
                try:
                    output = pty_process.read(4096)
                except EOFError:
                    break
                if output:
                    _decoded_terminal_output(session_id, connection, output)
                    continue
                if not pty_process.isalive():
                    break
                time.sleep(0.05)
            elif master_fd is not None:
                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                    if ready:
                        output = os.read(master_fd, 4096)
                        if not output:
                            break
                        _decoded_terminal_output(session_id, connection, output)
                        continue
                except OSError:
                    break
                if process is not None and process.poll() is not None:
                    break
            elif stdout_handle is not None:
                read1 = getattr(stdout_handle, 'read1', None)
                output = read1(4096) if read1 is not None else stdout_handle.read(1)
                if not output:
                    break
                _decoded_terminal_output(session_id, connection, output)
            else:
                break
        _decoded_terminal_output(session_id, connection, final=True)
    except Exception as exc:
        if _connection_is_current(session_id, connection):
            _connection_status(session_id, connection, SessionStatus.ERROR, str(exc))
    finally:
        _finalize_stream(session_id, connection)


def _normalize_local_directory(directory: Any, shell_kind: str) -> str:
    """Translate local repo paths for the shell that will receive them."""
    normalized = str(directory or "").strip()
    if not normalized or shell_kind != "wsl":
        return normalized

    if normalized.startswith("/"):
        return normalized.replace("\\", "/")

    drive_match = re.match(r"^(?P<drive>[A-Za-z]):[\\/]*(?P<rest>.*)$", normalized)
    if drive_match:
        drive = drive_match.group("drive").lower()
        remainder = drive_match.group("rest").replace("\\", "/").strip("/")
        return f"/mnt/{drive}/{remainder}" if remainder else f"/mnt/{drive}"

    return normalized.replace("\\", "/")


def _resolve_local_launch_cwd(directory: Any, shell_kind: str) -> Optional[str]:
    """Return a native working directory when the process can start there directly."""
    if shell_kind == "wsl":
        return None

    candidate = str(directory or "").strip()
    if not candidate:
        return None

    resolved = os.path.abspath(os.path.expanduser(candidate))
    return resolved if os.path.isdir(resolved) else None


"""Local shell families a Local Repo pane can run under (Windows hosts only)."""
LOCAL_SHELL_KINDS = ("cmd", "powershell", "wsl")

_LOCAL_SHELL_KIND_ALIASES = {
    "cmd": "cmd",
    "cmd.exe": "cmd",
    "command": "cmd",
    "powershell": "powershell",
    "powershell.exe": "powershell",
    "pwsh": "powershell",
    "wsl": "wsl",
}


def _local_shell_kind(session: Any) -> str:
    """Return the shell family one local pane starts under.

    Mirrors the precedence ``_build_local_command`` applies: WSL wins over
    PowerShell, and non-Windows hosts always land on their POSIX login shell.
    """
    if getattr(session, "use_wsl", False):
        return "wsl"
    if os.name == "nt":
        return "powershell" if getattr(session, "use_powershell", False) else "cmd"
    return "posix"


def _normalize_local_shell_kind(value: Any) -> str:
    """Normalize a requested local shell family, or "" when unrecognized."""
    return _LOCAL_SHELL_KIND_ALIASES.get(str(value or "").strip().lower(), "")


def _local_shell_display_name(
    use_wsl: bool,
    use_powershell: bool,
    distribution: Any = "",
) -> str:
    """Return the secondary UI label for one local terminal pane."""
    configured_distribution = str(distribution or "").strip()
    if use_powershell:
        return "PowerShell"
    if use_wsl:
        return f"WSL ({configured_distribution})" if configured_distribution else "WSL"
    return "cmd" if os.name == "nt" else "Shell"


def _build_local_command(
    session: Any,
    resolved_distribution: str = "",
    startup_directory: str = "",
) -> List[str]:
    """Build the command used for a WSL/local terminal session."""
    if getattr(session, "use_wsl", False):
        wsl_executable = _find_wsl_executable()
        if wsl_executable:
            command = [wsl_executable]
            if resolved_distribution:
                command.extend(["--distribution", resolved_distribution])
            if session.username:
                command.extend(["--user", session.username])
            if startup_directory:
                command.extend(["--cd", startup_directory])
            return command

    if os.name == "nt":
        if getattr(session, "use_powershell", False):
            return ["powershell.exe", "-NoLogo"]
        return [os.environ.get("COMSPEC") or "cmd.exe"]

    shell = os.environ.get("SHELL") or "/bin/bash"
    return [shell, "-i"]


def _local_shell_integration(
    shell_kind: str,
    command: List[str],
    environment: Dict[str, str],
) -> Tuple[List[str], Dict[str, str]]:
    """Fold the prompt hook into a local shell's own argv and environment.

    A pane GridVibe starts itself never has the hook *typed* at it: a typed line
    is echoed into the pane -- twice, when it is sent before the shell has drawn
    its first prompt -- and a startup that prints a paragraph of shell at the
    reader is worse than the problem it solves. cmd and bash take their prompt
    hook from the environment and PowerShell takes it as a `-Command` argument,
    so nothing appears in the pane at all.

    Returns the command and environment to spawn with, unchanged when the
    setting is off or the shell family has no hook.
    """
    if not runtime_config.terminal_shell_integration:
        return command, environment

    updated_environment = dict(environment)
    updated_environment.update(shell_integration_environment(shell_kind, environment))
    return command + shell_integration_arguments(shell_kind), updated_environment


def _sanitize_terminal_input(connection: Dict[str, Any], input_data: Any) -> str:
    """Drop Windows terminal capability replies that leak into cmd/PowerShell panes."""
    text = str(input_data or "")
    if not text:
        return ""

    if (
        connection.get("kind") == "local"
        and connection.get("pty_process") is not None
        and os.name == "nt"
        and connection.get("shell_kind") != "wsl"
    ):
        return text.replace(WINDOWS_DEVICE_ATTRIBUTES_RESPONSE, "")

    return text


_TERMINAL_INPUT_ESCAPE_SEQUENCE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|.)")
_MAX_TRACKED_TERMINAL_COMMAND_LENGTH = 4096


def _agent_from_terminal_command(command: str) -> Optional[Tuple[str, str]]:
    """Return registered agent metadata when a submitted shell command starts one."""
    try:
        tokens = shlex.split(str(command or "").strip(), posix=True)
    except ValueError:
        return None

    while tokens and tokens[0].lower() in {"command", "exec", "sudo"}:
        tokens.pop(0)
    if not tokens:
        return None

    executable = re.split(r"[/\\]", tokens[0])[-1].lower()
    executable = re.sub(r"\.(?:bat|cmd|exe)$", "", executable)
    for agent_key, spec in AGENT_REGISTRY.items():
        binary = str(spec.get("binary") or agent_key).strip().lower()
        if executable == binary:
            return agent_key, str(command or "").strip()
    return None


def _track_terminal_agent_input(
    session_id: str,
    connection: Dict[str, Any],
    input_data: str,
) -> None:
    """Track submitted input lines and promote recognized agent commands to runtime metadata."""
    text = _TERMINAL_INPUT_ESCAPE_SEQUENCE.sub("", str(input_data or ""))
    submitted_lines = []
    exit_reason = None

    session = session_manager.get_session(session_id)

    # The _gridvibe_* tracking keys are shared across Socket.IO handler
    # threads (two windows may drive the same session), so read-modify-write
    # them only under connection_lock; the string handling inside is trivial.
    # Metadata updates and broadcasts happen after the lock is released.
    with connection_lock:
        if session and session.startup_mode == "agent":
            if "\x04" in text:
                connection["_gridvibe_input_line"] = ""
                exit_reason = "end-of-input"
            elif "\x03" in text:
                agent_key = str(session.agent_selection or "").strip().lower()
                now = time.monotonic()
                last_interrupt = float(connection.get("_gridvibe_agent_interrupt_at") or 0.0)
                interrupt_count = (
                    int(connection.get("_gridvibe_agent_interrupt_count") or 0) + 1
                    if now - last_interrupt <= 2.0
                    else 1
                )
                connection["_gridvibe_agent_interrupt_at"] = now
                connection["_gridvibe_agent_interrupt_count"] = interrupt_count
                if agent_key == "codex" or interrupt_count >= 2:
                    connection["_gridvibe_input_line"] = ""
                    exit_reason = "interrupt"

        if exit_reason is None:
            current_line = str(connection.get("_gridvibe_input_line") or "")
            for character in text:
                if character in {"\r", "\n"}:
                    if current_line.strip():
                        submitted_lines.append(current_line)
                    current_line = ""
                elif character in {"\b", "\x7f"}:
                    current_line = current_line[:-1]
                elif character in {"\x03", "\x15"}:
                    current_line = ""
                elif character.isprintable() or character == "\t":
                    current_line = (current_line + character)[-_MAX_TRACKED_TERMINAL_COMMAND_LENGTH:]
            connection["_gridvibe_input_line"] = current_line

    if exit_reason is not None:
        _mark_runtime_agent_exited(session_id, exit_reason)
        return

    for submitted_line in submitted_lines:
        if submitted_line.strip().lower() in {"/exit", "/quit"}:
            if _mark_runtime_agent_exited(session_id, "exit command"):
                return
        detected = _agent_from_terminal_command(submitted_line)
        if not detected:
            continue
        agent_selection, initial_command = detected
        # Promotion is the one moment GridVibe knows where the agent is being
        # started: the shell is still at its prompt, and a beat later the agent
        # owns the terminal and emits no prompt of its own. Stamp the observed
        # directory into the observation slot -- never the launch slot, which
        # keeps meaning "where this pane started" -- so a Save Workspace taken
        # while the agent runs restores it in the directory it was started in
        # (ISSUE-2026-045). The probe is deliberately not allowed: it types at
        # a prompt the agent is about to take over.
        observed_directory, observed_source = effective_directory(session_id, session)
        promotion_updates: Dict[str, Any] = {}
        if observed_source != CWD_SOURCE_LAUNCH and observed_directory:
            promotion_updates["current_directory"] = observed_directory
        updated = session_manager.update_session_metadata(
            session_id,
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection=agent_selection,
            custom_agent="",
            initial_command=initial_command,
            **promotion_updates,
        )
        if updated:
            logger.info(
                "Detected runtime agent command for session %s: %s",
                session_id,
                agent_selection,
            )
            _broadcast_session_status(session_id)
        return


def _mark_runtime_agent_exited(session_id: str, reason: str) -> bool:
    """Return an agent-backed runtime pane to ordinary terminal metadata."""
    session = session_manager.get_session(session_id)
    if not session or session.startup_mode != "agent":
        return False
    updated = session_manager.update_session_metadata(
        session_id,
        startup_mode="terminal",
        initial_command_mode="command",
        agent_selection="",
        custom_agent="",
        initial_command="",
    )
    if not updated:
        return False
    logger.info("Detected runtime agent exit for session %s: %s", session_id, reason)
    _broadcast_session_status(session_id)
    return True


def _resolve_wsl_distribution(session: Any) -> str:
    """Return the user-configured WSL distro for a local session."""
    if not getattr(session, "use_wsl", False) or getattr(session, "use_powershell", False):
        return ""
    return str(getattr(session, "distribution", "") or "").strip()


def _connect_ssh_session(session_id: str, session: Any):
    """Establish an SSH connection for a single terminal session."""
    connection = _begin_connection(session_id)
    if connection is None:
        return
    logger.info(
        f"[{session_id}] Connecting to {session.username}@{session.host}:{session.port}"
        f" dir={session.directory} cmd={session.initial_command!r}"
    )

    if paramiko is None:
        message = "Paramiko is not installed. Run `pip install -r requirements.txt`."
        _connection_status(
            session_id, connection,
            SessionStatus.ERROR,
            error_message=message
        )
        _close_ssh_connection(session_id, expected=connection)
        return

    _connection_status(session_id, connection, SessionStatus.CONNECTING)

    client = None
    # One captured generation for both SSH settings, taken before the slow
    # open rather than around it: `connect()` can sit here for the length of
    # the timeout it was handed, and an App Settings refresh landing inside
    # that window used to give the keepalive a different generation's value
    # than the timeout the transport was actually opened with.
    ssh_settings = runtime_config.snapshot().ssh_config
    try:
        client = paramiko.SSHClient()
        _apply_host_key_policy(client, paramiko)
        logger.info(
            f"[{session_id}] paramiko.connect hostname={session.host} port={session.port}"
            f" user={session.username} password={'***' if session.password else None}"
        )
        client.connect(
            hostname=session.host,
            port=session.port,
            username=session.username,
            password=session.password or None,
            timeout=ssh_settings.get("connection_timeout", 30),
            look_for_keys=not bool(session.password),
            allow_agent=not bool(session.password)
        )
        logger.info(f"[{session_id}] SSH connected successfully")

        keepalive_interval = int(ssh_settings.get("keepalive_interval", 60) or 0)
        if keepalive_interval > 0:
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(keepalive_interval)

        channel = client.invoke_shell(term='xterm', width=120, height=30)
        channel.settimeout(SSH_STREAM_RECV_TIMEOUT)

        resources = {
            "kind": "ssh",
            "client": client,
            "channel": channel,
        }

        # Re-validate inside the lock so a concurrent close cannot slip between
        # the session check and the registry insert (which would leak the client).
        with connection_lock:
            stale = (not _connection_is_current(session_id, connection)
                     or session_manager.get_session(session_id) is None)
            if not stale:
                connection.update(resources)
                session_output_buffers[session_id] = _OutputBuffer()
        if stale:
            logger.info("[%s] Session was removed before SSH startup completed", session_id)
            _shutdown_connection(resources)
            _close_ssh_connection(session_id, expected=connection)
            return

        _connection_status(session_id, connection, SessionStatus.CONNECTED)

        _run_startup_sequence(connection, session)
        _stream_ssh_output(session_id, connection)
    except (paramiko.SSHException, OSError, socket.error) as e:
        logger.error(f"Failed to connect SSH session {session_id}: {e}")
        _connection_status(
            session_id, connection,
            SessionStatus.ERROR,
            error_message=str(e)
        )
        with connection_lock:
            was_stored = connection.get("client") is client
        _close_ssh_connection(session_id, expected=connection)
        if not was_stored and client is not None:
            try:
                client.close()
            except Exception:
                pass


def _connect_local_session(session_id: str, session: Any):
    """Establish a PTY-backed local or WSL shell session."""
    connection = _begin_connection(session_id)
    if connection is None:
        return
    logger.info(
        f"[{session_id}] Starting local shell mode distribution={session.distribution!r}"
        f" user={session.username!r} dir={session.directory!r}"
        f" use_wsl={getattr(session, 'use_wsl', False)!r}"
        f" use_powershell={getattr(session, 'use_powershell', False)!r}"
    )
    _connection_status(session_id, connection, SessionStatus.CONNECTING)

    process = None
    master_fd = slave_fd = None
    try:
        resolved_distribution = _resolve_wsl_distribution(session)
        shell_kind = _local_shell_kind(session)
        # D3: a restarted local pane comes back where it was, not where it
        # started. Both spawn paths take the same answer, and a directory that
        # has since disappeared falls through to `_run_startup_sequence`, whose
        # `cd` carries the launch directory as its fallback.
        startup_directory, _fallback_directory = _startup_directories(session)
        wsl_startup_directory = ""
        if shell_kind == "wsl" and startup_directory:
            wsl_startup_directory = _normalize_local_directory(startup_directory, shell_kind)

        command = _build_local_command(
            session,
            resolved_distribution=resolved_distribution,
            startup_directory=wsl_startup_directory,
        )
        launch_cwd = _resolve_local_launch_cwd(startup_directory, shell_kind)
        command, shell_environment = _local_shell_integration(
            shell_kind, command, dict(os.environ)
        )
        logger.info(f"[{session_id}] local command: {command}")

        if os.name == "nt":
            if WinPtyProcess is None:
                raise RuntimeError(
                    "Interactive Windows local terminals require pywinpty. "
                    "Install core dependencies with `pip install -r requirements.txt`."
                )

            process = WinPtyProcess.spawn(command, cwd=launch_cwd, env=shell_environment)
            resources = {
                "kind": "local",
                "pty_process": process,
                "shell_kind": shell_kind,
                "launch_cwd_applied": bool(launch_cwd or wsl_startup_directory),
            }
        else:
            if pty is None:
                raise RuntimeError("PTY support is unavailable on this system")
            master_fd, slave_fd = pty.openpty()
            env = dict(shell_environment)
            env.setdefault("TERM", "xterm-256color")
            process = subprocess.Popen(
                command,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=launch_cwd,
                start_new_session=True,
                close_fds=True,
            )
            os.close(slave_fd)
            slave_fd = None
            os.set_blocking(master_fd, False)
            resources = {
                "kind": "local",
                "process": process,
                "master_fd": master_fd,
                "shell_kind": shell_kind,
                "launch_cwd_applied": bool(launch_cwd or wsl_startup_directory),
            }

        # Re-validate inside the lock so a concurrent close cannot slip between
        # the session check and the registry insert (which would leak the PTY).
        with connection_lock:
            stale = (not _connection_is_current(session_id, connection)
                     or session_manager.get_session(session_id) is None)
            if not stale:
                connection.update(resources)
                session_output_buffers[session_id] = _OutputBuffer()
        master_fd = None  # resources now owns the descriptor, even on refusal
        if stale:
            logger.info("[%s] Session was removed before local shell startup completed", session_id)
            _shutdown_connection(resources)
            _close_ssh_connection(session_id, expected=connection)
            return

        _connection_status(session_id, connection, SessionStatus.CONNECTED)

        if shell_kind == "wsl":
            _drain_until_prompt(session_id, connection)

        _run_startup_sequence(connection, session)
        _stream_local_output(session_id, connection)
    except Exception as e:
        logger.error(f"Failed to start local session {session_id}: {e}")
        _connection_status(
            session_id, connection,
            SessionStatus.ERROR,
            error_message=str(e)
        )
        _close_ssh_connection(session_id, expected=connection)
        if process is not None:
            try:
                process.kill() # type: ignore
            except Exception:
                pass

    finally:
        for fd in (master_fd, slave_fd):
            if fd is not None:
                os.close(fd)


def _connect_session(session_id: str):
    """Establish the configured connection for a single terminal session."""
    logger.info(f"[{session_id}] _connect_session started")
    session = session_manager.get_session(session_id)
    if not session:
        logger.error(f"[{session_id}] Session not found in manager")
        return

    # Browser panes are always mode == "wsl" (_normalize_startup_mode only admits
    # `browser` for that connection mode), so without this branch they fall into
    # the local-shell connector and _run_startup_sequence types their
    # initial_command — the tab's URL — at the prompt. Every caller guards
    # externally today; this is the guardrail-6 corollary held at the one place
    # that resolves a pane's kind, so the next caller cannot reintroduce it.
    if _is_explorer_session(session) or _is_browser_session(session):
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _broadcast_session_status(session_id)
        return

    if session.mode == "wsl":
        _connect_local_session(session_id, session)
        return

    _connect_ssh_session(session_id, session)
