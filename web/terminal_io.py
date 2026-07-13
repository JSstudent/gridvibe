"""Terminal connection plumbing: registries, streams, startup, resize.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Owns the live
connection registry (``ssh_connections``), the rolling replay buffers, the
SSH/PTY output pump threads, the startup sequence, resize/input plumbing,
runtime agent-command tracking, and the SSH/local/WSL session connectors.
``web.api`` re-exports every name for backwards compatibility.
"""

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
from typing import Any, Deque, Dict, List, Optional, Tuple

from sessions.manager import SessionStatus
from web.agents import AGENT_REGISTRY, _find_wsl_executable, _powershell_single_quote
from web.app import session_manager, socketio
from web.config import runtime_config
from web.explorer import (
    _evict_all_pooled_ssh_clients,
    _evict_pooled_ssh_client,
    _is_explorer_session,
)
from web.hostkeys import _load_persistent_host_keys

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
# Rolling replay buffers kept as chunk deques so a busy pane appends cheaply
# instead of re-copying the whole tail on every output chunk; join only at
# replay time via _get_buffered_terminal_output.
session_output_buffers: Dict[str, Deque[str]] = {}
TERMINAL_OUTPUT_BUFFER_MAX_CHARS = 50000
client_joined_sessions: Dict[str, set[str]] = {}
_MAX_TRACKED_SOCKET_CLIENTS = 1000
# Lock ordering: connection_lock may be taken before session_manager.lock
# (e.g. re-validating a session inside the lock in the connectors), never the
# other way around — nothing may call into connection_lock while holding
# session_manager.lock.
connection_lock = threading.RLock()


def _broadcast_session_status(session_id: str):
    """Emit the latest session status to connected clients."""
    with session_manager.lock:
        session = session_manager.sessions.get(session_id)
        if session:
            socketio.emit('session_status', session.to_dict())


def _broadcast_session_groups_updated(reason: str = ""):
    """Notify open terminal windows that the set of session groups changed.

    Lets the frontend refresh on push instead of relying on its old 3-second
    reconciliation poll (that poll now only runs as a slow fallback while the
    Socket.IO connection is down).
    """
    socketio.emit('session_groups_updated', {"reason": reason})


def _cache_terminal_output(session_id: str, output: str):
    """Keep a short rolling output buffer for late-joining clients."""
    if not output:
        return
    with connection_lock:
        buffer = session_output_buffers.get(session_id)
        if buffer is None:
            buffer = deque()
            session_output_buffers[session_id] = buffer
        buffer.append(output)
        total = sum(len(chunk) for chunk in buffer)
        while buffer and total > TERMINAL_OUTPUT_BUFFER_MAX_CHARS:
            excess = total - TERMINAL_OUTPUT_BUFFER_MAX_CHARS
            head = buffer[0]
            if len(head) <= excess:
                buffer.popleft()
                total -= len(head)
            else:
                buffer[0] = head[excess:]
                total = TERMINAL_OUTPUT_BUFFER_MAX_CHARS


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
        session_output_buffers[session_id] = deque()


def _close_ssh_connection(session_id: str, clear_buffer: bool = True):
    """Close and remove a single SSH connection."""
    with connection_lock:
        connection = ssh_connections.pop(session_id, None)
        if clear_buffer:
            session_output_buffers.pop(session_id, None)

    _shutdown_connection(connection)
    _evict_pooled_ssh_client(session_id)


def _shutdown_connection(connection: Optional[Dict[str, Any]]):
    """Close one SSH or local terminal connection payload."""
    if not connection:
        return

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


def _replace_group_sessions(group_id: str) -> List[str]:
    """Close and remove the tracked sessions for one launched group."""
    existing_sessions = session_manager.get_group_sessions(group_id)
    if not existing_sessions:
        return []

    for session in existing_sessions:
        session_manager.close_session(session.session_id)
        _close_ssh_connection(session.session_id, clear_buffer=True)

    removed_session_ids = session_manager.remove_group_sessions(group_id)
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


def _send_connection_input(connection: Dict[str, Any], input_data: str):
    """Send keystrokes or commands to an active SSH or local shell connection."""
    kind = connection.get("kind")

    if kind == "ssh":
        connection["channel"].send(input_data)
        return

    pty_process = connection.get("pty_process")
    if pty_process is not None:
        pty_process.write(input_data)
        return

    encoded = input_data.encode("utf-8", errors="ignore")
    master_fd = connection.get("master_fd")
    if master_fd is not None:
        os.write(master_fd, encoded)
        return

    stdin_handle = connection.get("stdin")
    if stdin_handle is None:
        raise RuntimeError("Connection does not accept input")

    stdin_handle.write(encoded)
    stdin_handle.flush()


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
    if os.name == "nt" and shell_kind == "wsl":
        mount_match = re.match(r"^/mnt/([A-Za-z])(?:/(.*))?$", cwd)
        if mount_match:
            drive = mount_match.group(1).upper()
            remainder = (mount_match.group(2) or "").replace("/", "\\")
            return f"{drive}:\\" + remainder if remainder else f"{drive}:\\"
    return cwd


def _resolve_live_terminal_cwd(session_id: str, session: Any, timeout: float = 0.75) -> Optional[str]:
    """Probe an active terminal shell for its current working directory."""
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
            _cache_terminal_output(session_id, output)
            # Emit outside connection_lock: a slow client write must not stall
            # every other terminal's pump behind the global lock.
            socketio.emit(
                'terminal_output',
                {'session_id': session_id, 'data': output},
                room=session_id  # type: ignore
            )
            time.sleep(0.15)
            return

        time.sleep(0.1)


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

    if session.directory and not connection.get("launch_cwd_applied"):
        target_directory = _normalize_local_directory(session.directory, shell_kind)
        if shell_kind == "cmd":
            escaped_directory = target_directory.replace('"', '""')
            _send_connection_input(connection, f'cd /d "{escaped_directory}"{newline}')
        elif shell_kind == "powershell":
            _send_connection_input(
                connection,
                f"Set-Location -LiteralPath {_powershell_single_quote(target_directory)}{newline}",
            )
        else:
            _send_connection_input(connection, f"cd {shlex.quote(target_directory)}{newline}")
        time.sleep(0.15)

    if session.initial_command:
        _send_connection_input(connection, f"{session.initial_command}{newline}")


def _finalize_stream(session_id: str):
    """Mark a session disconnected after a stream ends."""
    session = session_manager.get_session(session_id)
    if session and _is_explorer_session(session):
        _close_ssh_connection(session_id)
        return

    if session and session.status not in {SessionStatus.ERROR, SessionStatus.DISCONNECTED}:
        session_manager.update_session_status(session_id, SessionStatus.DISCONNECTED)
        _broadcast_session_status(session_id)
    _close_ssh_connection(session_id)


SSH_STREAM_RECV_TIMEOUT = 0.5


def _stream_ssh_output(session_id: str):
    """Read terminal output from the SSH channel and forward it to clients."""
    try:
        with connection_lock:
            connection = ssh_connections.get(session_id)

        if not connection:
            return

        # The connection entry never changes for a session's lifetime, so fetch
        # the channel once and block on recv with a timeout instead of polling.
        # An intentional close (_close_ssh_connection) closes the channel, which
        # wakes the recv and ends the loop.
        channel = connection["channel"]
        channel.settimeout(SSH_STREAM_RECV_TIMEOUT)

        while not channel.closed:
            try:
                data = channel.recv(4096)
            except socket.timeout:
                if channel.exit_status_ready():
                    break
                continue

            if not data:
                break

            output = data.decode("utf-8", errors="ignore")
            _cache_terminal_output(session_id, output)
            # Emit outside connection_lock: a slow client write must not stall
            # every other terminal's pump behind the global lock.
            socketio.emit(
                'terminal_output',
                {'session_id': session_id, 'data': output},
                room=session_id # type: ignore
            )
    except Exception as e:
        session = session_manager.get_session(session_id)
        if session and _is_explorer_session(session):
            logger.debug("Ignoring stream shutdown for explorer session %s: %s", session_id, e)
            return

        with connection_lock:
            intentional_close = session_id not in ssh_connections
        if intentional_close or session is None or session.status == SessionStatus.DISCONNECTED:
            logger.debug("Stream ended for closed session %s: %s", session_id, e)
            return

        logger.error(f"Error streaming output for session {session_id}: {e}")
        if session and session.status != SessionStatus.DISCONNECTED:
            session_manager.update_session_status(
                session_id,
                SessionStatus.ERROR,
                error_message=str(e)
            )
            _broadcast_session_status(session_id)
    finally:
        _finalize_stream(session_id)


def _stream_local_output(session_id: str):
    """Read terminal output from a PTY-backed local shell."""
    try:
        while True:
            with connection_lock:
                connection = ssh_connections.get(session_id)

            if not connection:
                break

            process = connection.get("process")
            pty_process = connection.get("pty_process")
            master_fd = connection.get("master_fd")
            stdout_handle = connection.get("stdout")

            if pty_process is not None:
                try:
                    output = pty_process.read(4096)
                except EOFError:
                    break

                if output:
                    _cache_terminal_output(session_id, output)
                    socketio.emit(
                        'terminal_output',
                        {'session_id': session_id, 'data': output},
                        room=session_id # type: ignore
                    )
                    continue

                try:
                    if not pty_process.isalive():
                        break
                except Exception:
                    break

                time.sleep(0.05)
                continue

            if master_fd is not None:
                try:
                    ready, _, _ = select.select([master_fd], [], [], 0.05)
                    if ready:
                        output = os.read(master_fd, 4096).decode("utf-8", errors="ignore")
                        if output:
                            _cache_terminal_output(session_id, output)
                            socketio.emit(
                                'terminal_output',
                                {'session_id': session_id, 'data': output},
                                room=session_id # type: ignore
                            )
                        continue
                except OSError:
                    break

                if process is not None and process.poll() is not None:
                    break

                continue

            if stdout_handle is None:
                break

            # read1 returns whatever is buffered (blocking until at least one
            # byte or EOF) instead of one syscall + one emit per byte.
            read1 = getattr(stdout_handle, "read1", None)
            output = read1(4096) if read1 is not None else stdout_handle.read(1)
            if output:
                chunk = output.decode("utf-8", errors="ignore")
                _cache_terminal_output(session_id, chunk)
                socketio.emit(
                    'terminal_output',
                    {'session_id': session_id, 'data': chunk},
                    room=session_id # type: ignore
                )
                continue

            if process is not None and process.poll() is not None:
                break

            time.sleep(0.05)
    except Exception as e:
        session = session_manager.get_session(session_id)
        if session and _is_explorer_session(session):
            logger.debug("Ignoring local stream shutdown for explorer session %s: %s", session_id, e)
            return

        with connection_lock:
            intentional_close = session_id not in ssh_connections
        if intentional_close or session is None or session.status == SessionStatus.DISCONNECTED:
            logger.debug("Local stream ended for closed session %s: %s", session_id, e)
            return

        logger.error(f"Error streaming local output for session {session_id}: {e}")
        if session and session.status != SessionStatus.DISCONNECTED:
            session_manager.update_session_status(
                session_id,
                SessionStatus.ERROR,
                error_message=str(e)
            )
            _broadcast_session_status(session_id)
    finally:
        _finalize_stream(session_id)


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
        updated = session_manager.update_session_metadata(
            session_id,
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection=agent_selection,
            custom_agent="",
            initial_command=initial_command,
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
    logger.info(
        f"[{session_id}] Connecting to {session.username}@{session.host}:{session.port}"
        f" dir={session.directory} cmd={session.initial_command!r}"
    )

    if paramiko is None:
        message = "Paramiko is not installed. Run `pip install -r requirements.txt`."
        session_manager.update_session_status(
            session_id,
            SessionStatus.ERROR,
            error_message=message
        )
        _broadcast_session_status(session_id)
        return

    session_manager.update_session_status(session_id, SessionStatus.CONNECTING)
    _broadcast_session_status(session_id)

    client = None
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        _load_persistent_host_keys(client)
        logger.info(
            f"[{session_id}] paramiko.connect hostname={session.host} port={session.port}"
            f" user={session.username} password={'***' if session.password else None}"
        )
        client.connect(
            hostname=session.host,
            port=session.port,
            username=session.username,
            password=session.password or None,
            timeout=runtime_config.ssh_config.get("connection_timeout", 30),
            look_for_keys=not bool(session.password),
            allow_agent=not bool(session.password)
        )
        logger.info(f"[{session_id}] SSH connected successfully")

        keepalive_interval = int(runtime_config.ssh_config.get("keepalive_interval", 60) or 0)
        if keepalive_interval > 0:
            transport = client.get_transport()
            if transport is not None:
                transport.set_keepalive(keepalive_interval)

        channel = client.invoke_shell(term='xterm', width=120, height=30)

        connection = {
            "kind": "ssh",
            "client": client,
            "channel": channel,
        }

        # Re-validate inside the lock so a concurrent close cannot slip between
        # the session check and the registry insert (which would leak the client).
        with connection_lock:
            stale = session_manager.get_session(session_id) is None
            if not stale:
                ssh_connections[session_id] = connection
                session_output_buffers[session_id] = deque()
        if stale:
            logger.info("[%s] Session was removed before SSH startup completed", session_id)
            _shutdown_connection(connection)
            return

        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _broadcast_session_status(session_id)

        _run_startup_sequence(connection, session)
        _stream_ssh_output(session_id)
    except (paramiko.SSHException, OSError, socket.error) as e:
        logger.error(f"Failed to connect SSH session {session_id}: {e}")
        session_manager.update_session_status(
            session_id,
            SessionStatus.ERROR,
            error_message=str(e)
        )
        _broadcast_session_status(session_id)
        with connection_lock:
            was_stored = session_id in ssh_connections
        _close_ssh_connection(session_id)
        if not was_stored and client is not None:
            try:
                client.close()
            except Exception:
                pass


def _connect_local_session(session_id: str, session: Any):
    """Establish a PTY-backed local or WSL shell session."""
    logger.info(
        f"[{session_id}] Starting local shell mode distribution={session.distribution!r}"
        f" user={session.username!r} dir={session.directory!r}"
        f" use_wsl={getattr(session, 'use_wsl', False)!r}"
        f" use_powershell={getattr(session, 'use_powershell', False)!r}"
    )
    session_manager.update_session_status(session_id, SessionStatus.CONNECTING)
    _broadcast_session_status(session_id)

    process = None
    try:
        resolved_distribution = _resolve_wsl_distribution(session)
        use_wsl = getattr(session, "use_wsl", False)
        shell_kind = (
            "wsl"
            if use_wsl
            else (
                "powershell"
                if os.name == "nt" and getattr(session, "use_powershell", False)
                else ("cmd" if os.name == "nt" else "posix")
            )
        )
        wsl_startup_directory = ""
        if shell_kind == "wsl" and session.directory:
            wsl_startup_directory = _normalize_local_directory(session.directory, shell_kind)

        command = _build_local_command(
            session,
            resolved_distribution=resolved_distribution,
            startup_directory=wsl_startup_directory,
        )
        launch_cwd = _resolve_local_launch_cwd(session.directory, shell_kind)
        logger.info(f"[{session_id}] local command: {command}")

        if os.name == "nt":
            if WinPtyProcess is None:
                raise RuntimeError(
                    "Interactive Windows local terminals require pywinpty. "
                    "Install desktop dependencies with `pip install -r requirements-desktop.txt`."
                )

            command_line = subprocess.list2cmdline(command)
            process = WinPtyProcess.spawn(command_line, cwd=launch_cwd)
            connection = {
                "kind": "local",
                "pty_process": process,
                "shell_kind": shell_kind,
                "launch_cwd_applied": bool(launch_cwd or wsl_startup_directory),
            }
        else:
            if pty is None:
                raise RuntimeError("PTY support is unavailable on this system")
            master_fd, slave_fd = pty.openpty()
            env = os.environ.copy()
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
            connection = {
                "kind": "local",
                "process": process,
                "master_fd": master_fd,
                "shell_kind": shell_kind,
                "launch_cwd_applied": bool(launch_cwd or wsl_startup_directory),
            }

        # Re-validate inside the lock so a concurrent close cannot slip between
        # the session check and the registry insert (which would leak the PTY).
        with connection_lock:
            stale = session_manager.get_session(session_id) is None
            if not stale:
                ssh_connections[session_id] = connection
                session_output_buffers[session_id] = deque()
        if stale:
            logger.info("[%s] Session was removed before local shell startup completed", session_id)
            _shutdown_connection(connection)
            return

        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _broadcast_session_status(session_id)

        if shell_kind == "wsl":
            _drain_until_prompt(session_id, connection)

        _run_startup_sequence(connection, session)
        _stream_local_output(session_id)
    except Exception as e:
        logger.error(f"Failed to start local session {session_id}: {e}")
        session_manager.update_session_status(
            session_id,
            SessionStatus.ERROR,
            error_message=str(e)
        )
        _broadcast_session_status(session_id)
        _close_ssh_connection(session_id)
        if process is not None:
            try:
                process.kill() # type: ignore
            except Exception:
                pass


def _connect_session(session_id: str):
    """Establish the configured connection for a single terminal session."""
    logger.info(f"[{session_id}] _connect_session started")
    session = session_manager.get_session(session_id)
    if not session:
        logger.error(f"[{session_id}] Session not found in manager")
        return

    if _is_explorer_session(session):
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _broadcast_session_status(session_id)
        return

    if session.mode == "wsl":
        _connect_local_session(session_id, session)
        return

    _connect_ssh_session(session_id, session)
