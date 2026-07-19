"""Agent CLI registry, binary detection, preflight, and host probing.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Covers the static
agent registry, the per-target detection probes (Windows/POSIX/WSL/SSH) with
their result cache, the launcher preflight payload builder, SSH ping/TCP
probes, and the local WSL distribution inspection helpers. ``web.api``
re-exports every name for backwards compatibility.
"""

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from web.config import _load_json_file, runtime_config
from web.hostkeys import _apply_host_key_policy
from web.paths import BASE_DIR
from web.saved_sessions import _normalize_connection_mode

try:
    import paramiko
except ImportError:  # pragma: no cover - handled at runtime when dependency is missing
    paramiko = None

logger = logging.getLogger(__name__)

AGENT_REGISTRY_PATH = os.path.join(BASE_DIR, "agent_registry.json")


def _load_agent_registry() -> Dict[str, Any]:
    """Load the static agent CLI registry from disk."""
    try:
        registry = _load_json_file(AGENT_REGISTRY_PATH)
    except OSError as exc:
        logger.warning(f"Failed to load {AGENT_REGISTRY_PATH}: {exc}")
        return {}
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse {AGENT_REGISTRY_PATH}: {exc}")
        return {}

    normalized: Dict[str, Any] = {}
    for key, value in registry.items():
        if isinstance(value, dict):
            normalized[str(key).strip().lower()] = value
    return normalized


AGENT_REGISTRY = _load_agent_registry()
_AGENT_DETECTION_CACHE_TTL_SECONDS = 30
_agent_detection_cache: Dict[Tuple[str, str, str, str, str, int], Tuple[float, Dict[str, Any]]] = {}
_agent_detection_cache_lock = threading.Lock()


def _agent_auto_mode_flag(agent_key: Any) -> str:
    """Return the registry-defined auto-mode flag for one agent, or ""."""
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    auto_mode = spec.get("auto_mode")
    if not isinstance(auto_mode, dict):
        return ""
    flag = str(auto_mode.get("flag") or "").strip()
    # A registered flag must look like a CLI option so a registry typo can
    # never smuggle a second command into the composed launch line.
    if not flag.startswith("-") or any(ch.isspace() for ch in flag):
        return ""
    return flag


def _agent_auto_mode_description(agent_key: Any) -> str:
    """Return the registry-defined auto-mode description for one agent, or ""."""
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    auto_mode = spec.get("auto_mode")
    if not isinstance(auto_mode, dict):
        return ""
    return str(auto_mode.get("description") or "").strip()


def _agent_options() -> List[Dict[str, str]]:
    """Return launcher agent choices sourced from the registry."""
    options = [
        {
            "value": key,
            "label": str(spec.get("label") or key),
            "auto_mode_flag": _agent_auto_mode_flag(key),
            "auto_mode_description": _agent_auto_mode_description(key),
        }
        for key, spec in AGENT_REGISTRY.items()
    ]
    options.sort(key=lambda item: item["label"])
    options.append({"value": "other", "label": "other", "auto_mode_flag": "", "auto_mode_description": ""})
    return options


def _compose_agent_startup_command(session: Any) -> str:
    """Return the startup command for a session, applying its auto-mode flag.

    The persisted ``initial_command`` always stays the base agent key so
    preflight detection and saved-session round-trips keep working; the flag
    is appended only here, at launch time, and only when the selected
    built-in agent has a registered auto-mode option.
    """
    base = str(getattr(session, "initial_command", "") or "").strip()
    if not base:
        return base
    if str(getattr(session, "initial_command_mode", "") or "") != "agent":
        return base
    if not bool(getattr(session, "agent_auto_mode", False)):
        return base
    agent_key = _normalize_agent_key(getattr(session, "agent_selection", "")) or _normalize_agent_key(base)
    if _normalize_agent_key(base) != agent_key:
        # Custom or already-modified commands launch verbatim.
        return base
    flag = _agent_auto_mode_flag(agent_key)
    if not flag:
        return base
    return f"{base} {flag}"


def _normalize_agent_key(value: Any) -> str:
    """Normalize an agent registry key."""
    return str(value or "").strip().lower()


def _normalize_port_number(value: Any, default: int = 22) -> int:
    """Return a bounded integer port."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = default
    return max(1, min(65535, port))


def _normalize_ping_target(value: Any) -> str:
    """Return a host/IP value safe to pass as one subprocess argument."""
    target = str(value or "").strip()
    if not target:
        raise ValueError("Enter an SSH host or IP address before pinging.")
    if len(target) > 253:
        raise ValueError("SSH host is too long.")
    if any(character.isspace() for character in target):
        raise ValueError("SSH host cannot contain spaces.")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", target):
        raise ValueError("SSH host contains unsupported characters.")
    return target


def _ping_command(target: str) -> List[str]:
    """Build the local OS ping command for a single quick probe."""
    if os.name == "nt":
        return ["ping", "-n", "1", "-w", "3000", target]
    return ["ping", "-c", "1", "-W", "3", target]


def _parse_ping_latency_ms(output: str) -> Optional[float]:
    """Extract a representative ping latency from common ping output."""
    match = re.search(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms", output, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


def _tcp_probe_target(target: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    """Check whether the SSH TCP port accepts a connection."""
    start = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=timeout):
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            return {
                "reachable": True,
                "method": "tcp",
                "latency_ms": latency_ms,
                "message": f"Reached {target}:{port} over TCP in {latency_ms:.0f} ms.",
            }
    except OSError as exc:
        return {
            "reachable": False,
            "method": "tcp",
            "latency_ms": None,
            "message": f"Could not reach {target}:{port}: {exc}",
        }


def _ping_ssh_target(target: Any, port: Any = 22) -> Dict[str, Any]:
    """Ping a launcher SSH target, falling back to a TCP SSH-port probe."""
    normalized_target = _normalize_ping_target(target)
    normalized_port = _normalize_port_number(port, 22)
    ping_executable = shutil.which("ping")

    if ping_executable:
        command = [ping_executable, *_ping_command(normalized_target)[1:]]
        try:
            start = time.monotonic()
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
                check=False,
            )
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            output = f"{result.stdout}\n{result.stderr}".strip()
            latency_ms = _parse_ping_latency_ms(output) or elapsed_ms
            if result.returncode == 0:
                return {
                    "reachable": True,
                    "method": "icmp",
                    "target": normalized_target,
                    "port": normalized_port,
                    "latency_ms": latency_ms,
                    "message": f"Ping reached {normalized_target} in {latency_ms:.0f} ms.",
                }
            tcp_result = _tcp_probe_target(normalized_target, normalized_port)
            return {
                **tcp_result,
                "target": normalized_target,
                "port": normalized_port,
                "ping_error": output,
            }
        except (OSError, subprocess.SubprocessError) as exc:
            tcp_result = _tcp_probe_target(normalized_target, normalized_port)
            return {
                **tcp_result,
                "target": normalized_target,
                "port": normalized_port,
                "ping_error": str(exc),
            }

    tcp_result = _tcp_probe_target(normalized_target, normalized_port)
    return {
        **tcp_result,
        "target": normalized_target,
        "port": normalized_port,
        "ping_error": "Local ping command is unavailable.",
    }


def _powershell_single_quote(value: Any) -> str:
    """Escape a string for use inside a PowerShell single-quoted literal."""
    return "'" + str(value or "").replace("'", "''") + "'"


def _shell_single_quote(value: Any) -> str:
    """Escape a string for use inside a POSIX single-quoted literal."""
    return "'" + str(value or "").replace("'", "'\"'\"'") + "'"


def _contains_html_payload(value: Any) -> bool:
    """Return True when captured command output looks like an HTML page."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False

    return any(
        marker in normalized
        for marker in (
            "<!doctype html",
            "<html",
            "<title>just a moment",
            "enable javascript and cookies to continue",
        )
    )


def _build_posix_detection_command(binary: str) -> str:
    """Build a shell probe that detects files, aliases, and shell functions."""
    binary_literal = _shell_single_quote(binary)
    fast_script = (
        f"TF_BINARY={binary_literal}; "
        'if command -v "$TF_BINARY" >/dev/null 2>&1; then '
        'TF_PATH=$(command -v "$TF_BINARY" 2>/dev/null || true); '
        'TF_HEAD=""; '
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' file; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\"; "
        "exit 0; "
        "fi"
    )
    bash_script = (
        f"TF_BINARY={binary_literal}; "
        'if ! type "$TF_BINARY" >/dev/null 2>&1; then exit 1; fi; '
        'TF_KIND=$(type -t "$TF_BINARY" 2>/dev/null || printf file); '
        'TF_PATH=$(type -P "$TF_BINARY" 2>/dev/null || command -v "$TF_BINARY" 2>/dev/null || true); '
        "TF_HEAD=''; "
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' \"$TF_KIND\"; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\""
    )
    sh_script = (
        f"TF_BINARY={binary_literal}; "
        'if ! command -v "$TF_BINARY" >/dev/null 2>&1; then exit 1; fi; '
        'TF_PATH=$(command -v "$TF_BINARY" 2>/dev/null || true); '
        "TF_HEAD=''; "
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' file; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\""
    )
    return (
        f"{fast_script}; "
        'if command -v bash >/dev/null 2>&1; then '
        f'bash -ilc {_shell_single_quote(bash_script)}; '
        'else '
        f'sh -lc {_shell_single_quote(sh_script)}; '
        'fi'
    )


def _build_login_shell_detection_command(binary: str) -> str:
    """Build a probe that runs inside the user's login shell when possible."""
    binary_literal = _shell_single_quote(binary)
    posix_probe = (
        'if ! command -v "$TF_BINARY" >/dev/null 2>&1; then exit 1; fi; '
        'TF_PATH=$(command -v "$TF_BINARY" 2>/dev/null || true); '
        'TF_KIND=""; '
        'case "$TF_PATH" in '
        'alias\\ *) TF_KIND=alias; TF_PATH="" ;; '
        'esac; '
        'if [ -z "$TF_KIND" ] && [ "$TF_PATH" = "$TF_BINARY" ]; then TF_KIND=builtin; TF_PATH=""; fi; '
        "TF_HEAD=''; "
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' \"$TF_KIND\"; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\""
    )
    posix_command = f"TF_BINARY={binary_literal}; {posix_probe}"
    fish_command = f"env TF_BINARY={binary_literal} /bin/sh -lc {_shell_single_quote(posix_probe)}"
    return (
        'TF_LOGIN_SHELL=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7 | head -n 1); '
        '[ -n "$TF_LOGIN_SHELL" ] || TF_LOGIN_SHELL="${SHELL:-/bin/sh}"; '
        '[ -x "$TF_LOGIN_SHELL" ] || TF_LOGIN_SHELL=/bin/sh; '
        'TF_LOGIN_NAME=$(basename "$TF_LOGIN_SHELL"); '
        'case "$TF_LOGIN_NAME" in '
        f'fish) exec "$TF_LOGIN_SHELL" -ilc {_shell_single_quote(fish_command)} ;; '
        f'bash|zsh|ksh) exec "$TF_LOGIN_SHELL" -ilc {_shell_single_quote(posix_command)} ;; '
        f'*) exec "$TF_LOGIN_SHELL" -lc {_shell_single_quote(posix_command)} ;; '
        'esac'
    )


def _parse_posix_detection_output(
    binary: str,
    stdout: str,
    command_label: str,
    stderr: str = "",
    returncode: int = 0,
) -> Dict[str, Any]:
    """Normalize shell detection output from POSIX, WSL, and SSH probes."""
    metadata: Dict[str, str] = {}
    found_marker = False

    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if line == "__TF_FOUND__":
            found_marker = True
            continue
        if line.startswith("__TF_KIND__:"):
            metadata["kind"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("__TF_PATH__:"):
            metadata["path"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("__TF_HEAD__:"):
            metadata["head"] = line.split(":", 1)[1].strip()

    resolved = metadata.get("path", "")
    kind = metadata.get("kind", "")
    lowered_resolved = resolved.lower()
    if not kind and lowered_resolved.startswith("alias "):
        kind = "alias"
        resolved = ""
    elif not kind and "function" in lowered_resolved:
        kind = "function"
        resolved = ""
    elif not kind and resolved == binary:
        kind = "builtin"
        resolved = ""
    display_path = resolved or (f"{kind} {binary}" if kind in {"alias", "function", "builtin"} else "")

    if found_marker:
        if _contains_html_payload(metadata.get("head", "")):
            return {
                "found": False,
                "path": display_path,
                "command": command_label,
                "error": f"{binary} resolves to an HTML page instead of a working CLI.",
                "failed": True,
                "kind": kind,
            }
        return {
            "found": True,
            "path": display_path,
            "command": command_label,
            "error": "",
            "kind": kind,
        }

    return {
        "found": False,
        "path": "",
        "command": command_label,
        "error": str(stderr or "").strip() if returncode != 0 else "",
    }


def _agent_target_label(target: Dict[str, Any]) -> str:
    """Return a short human-readable target label."""
    environment_key = target.get("environment_key")
    shell_kind = target.get("shell_kind")
    if environment_key == "ssh":
        host = str(target.get("host") or "").strip()
        return f"SSH {host}" if host else "SSH"
    if environment_key == "windows_native":
        if shell_kind == "powershell":
            return "PowerShell"
        if shell_kind == "cmd":
            return "cmd"
        return "Windows"
    if shell_kind == "wsl":
        distribution = str(target.get("distribution") or "").strip()
        return f"WSL {distribution}" if distribution else "WSL"
    return "local shell"


def _resolve_preflight_wsl_distribution(value: Any) -> str:
    """Return the user-configured WSL distro for agent preflight, unmodified."""
    return str(value or "").strip()


def _resolve_agent_target(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the requested preflight environment from launcher form state."""
    connection_mode = _normalize_connection_mode(payload.get("connection_mode"))
    ssh_data = payload.get("ssh") if isinstance(payload.get("ssh"), dict) else {}
    wsl_data = payload.get("wsl") if isinstance(payload.get("wsl"), dict) else {}
    terminal = payload.get("terminal") if isinstance(payload.get("terminal"), dict) else {}
    use_powershell = bool(terminal.get("use_powershell"))
    use_wsl = bool(terminal.get("use_wsl")) and not use_powershell

    if connection_mode == "ssh":
        return {
            "connection_mode": "ssh",
            "environment_key": "ssh",
            "shell_kind": "ssh",
            "host": str(ssh_data.get("host") or "").strip(),
            "username": str(ssh_data.get("username") or "ubuntu").strip() or "ubuntu",
            "password": str(ssh_data.get("password") or ""),
            "port": _normalize_port_number(ssh_data.get("port"), 22),
        }

    configured_distribution = str(terminal.get("distribution") or wsl_data.get("distribution") or "").strip()
    distribution = _resolve_preflight_wsl_distribution(configured_distribution) if use_wsl else configured_distribution
    username = str(wsl_data.get("username") or "").strip()
    if os.name != "nt":
        return {
            "connection_mode": connection_mode,
            "environment_key": "wsl_linux",
            "shell_kind": "wsl" if use_wsl else "posix",
            "distribution": distribution,
            "username": username,
        }

    if use_wsl:
        return {
            "connection_mode": connection_mode,
            "environment_key": "wsl_linux",
            "shell_kind": "wsl",
            "distribution": distribution,
            "username": username,
        }

    return {
        "connection_mode": connection_mode,
        "environment_key": "windows_native",
        "shell_kind": "powershell" if use_powershell else "cmd",
        "distribution": distribution,
        "username": username,
    }


def _detect_windows_command(binary: str) -> Dict[str, Any]:
    """Detect a command in native Windows shells."""
    command_label = f"Get-Command {binary} -ErrorAction SilentlyContinue"
    if os.name != "nt":
        resolved = shutil.which(binary)
        return {
            "found": bool(resolved),
            "path": resolved or "",
            "command": f"command -v {binary}",
            "error": "",
        }

    script = (
        f"$cmd = Get-Command {_powershell_single_quote(binary)} -ErrorAction SilentlyContinue; "
        f"if ($cmd) {{ $cmd.Source }}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }

    resolved = (result.stdout or "").strip()
    return {
        "found": bool(resolved),
        "path": resolved,
        "command": command_label,
        "error": (result.stderr or "").strip() if result.returncode != 0 else "",
    }


def _detect_posix_command(binary: str) -> Dict[str, Any]:
    """Detect a command in the current POSIX shell."""
    command_label = f"type {binary}"
    probe_command = _build_posix_detection_command(binary)
    try:
        result = subprocess.run(
            ["sh", "-lc", probe_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }

    return _parse_posix_detection_output(
        binary,
        result.stdout or "",
        command_label,
        stderr=result.stderr or "",
        returncode=result.returncode,
    )


def _detect_wsl_command(binary: str, distribution: str = "") -> Dict[str, Any]:
    """Detect a command inside a local WSL environment."""
    command_label = f"type {binary}"
    if os.name != "nt":
        return _detect_posix_command(binary)

    wsl_executable = _find_wsl_executable()
    if not wsl_executable:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": "WSL is not available on this system.",
            "failed": True,
        }

    command = [wsl_executable]
    if distribution:
        command.extend(["--distribution", distribution])
    # --exec bypasses wsl.exe's intermediate shell, preventing premature $VAR expansion
    command.extend(["--exec", "bash", "-lc", _build_login_shell_detection_command(binary)])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }

    # bash --exec login shells emit "logout" to stderr on exit — filter it out
    stderr = result.stderr or ""
    if stderr.strip().lower() == "logout":
        stderr = ""

    return _parse_posix_detection_output(
        binary,
        result.stdout or "",
        command_label,
        stderr=stderr,
        returncode=result.returncode,
    )


def _detect_ssh_command(binary: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """Detect a command on a remote SSH host."""
    host = str(target.get("host") or "").strip()
    username = str(target.get("username") or "").strip()
    port = _normalize_port_number(target.get("port"), 22)
    command_label = f"type {binary}"
    if not host or not username:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": "Enter an SSH host and username to inspect the remote CLI.",
            "incomplete": True,
        }

    if paramiko is None:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": "Remote CLI detection is unavailable because Paramiko is not installed.",
            "failed": True,
        }

    client = None
    try:
        client = paramiko.SSHClient()
        _apply_host_key_policy(client, paramiko)
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=target.get("password") or None,
            timeout=min(int(runtime_config.ssh_config.get("connection_timeout", 30)), 5),
            auth_timeout=5,
            banner_timeout=5,
            look_for_keys=not bool(target.get("password")),
            allow_agent=not bool(target.get("password")),
        )
        _, stdout, stderr = client.exec_command(
            _build_login_shell_detection_command(binary),
            timeout=8,
        )
        exit_status = stdout.channel.recv_exit_status()
        return _parse_posix_detection_output(
            binary,
            stdout.read().decode("utf-8", errors="ignore"),
            command_label,
            stderr=stderr.read().decode("utf-8", errors="ignore"),
            returncode=exit_status,
        )
    except Exception as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _detect_agent_binary(target: Dict[str, Any], binary: str) -> Dict[str, Any]:
    """Detect whether an agent binary exists in the resolved environment."""
    environment_key = target.get("environment_key")
    if environment_key == "ssh":
        return _detect_ssh_command(binary, target)
    if environment_key == "windows_native":
        return _detect_windows_command(binary)
    return _detect_wsl_command(binary, str(target.get("distribution") or "").strip())


def _agent_detection_cache_key(
    target: Dict[str, Any],
    binary: str,
) -> Tuple[str, str, str, str, str, int]:
    """Build a stable cache key for one agent binary detection target."""
    return (
        str(binary or "").strip(),
        str(target.get("environment_key") or ""),
        str(target.get("shell_kind") or ""),
        str(target.get("distribution") or "").strip(),
        str(target.get("host") or "").strip(),
        _normalize_port_number(target.get("port"), 22),
    )


def _detect_agent_binary_cached(target: Dict[str, Any], binary: str) -> Dict[str, Any]:
    """Detect an agent binary while reusing recent identical probe results."""
    if target.get("environment_key") == "ssh":
        return _detect_agent_binary(target, binary)

    key = _agent_detection_cache_key(target, binary)
    now = time.monotonic()

    with _agent_detection_cache_lock:
        cached = _agent_detection_cache.get(key)
        if cached is not None:
            cached_at, cached_payload = cached
            if now - cached_at <= _AGENT_DETECTION_CACHE_TTL_SECONDS:
                return dict(cached_payload)
            _agent_detection_cache.pop(key, None)

    # Probe outside the lock: the subprocess can take up to ~8s (WSL) and must
    # not serialize concurrent preflights for other agents/rows. Duplicate
    # concurrent probes for the same key are idempotent and cached afterwards.
    detection = _detect_agent_binary(target, binary)
    with _agent_detection_cache_lock:
        _agent_detection_cache[key] = (time.monotonic(), dict(detection))
    return detection


def _check_install_requirement(requirement: Dict[str, Any], target: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether one install prerequisite is available."""
    kind = str(requirement.get("kind") or "").strip().lower()
    message = str(requirement.get("message") or "Missing prerequisite.").strip()
    if kind == "wsl":
        return bool(_find_wsl_executable()), message

    if kind == "command":
        binary = str(requirement.get("binary") or "").strip()
        if not binary:
            return True, ""
        if target.get("environment_key") in {"ssh", "wsl_linux"}:
            return True, ""
        detected = _detect_agent_binary_cached(
            {
                **target,
                "environment_key": target.get("environment_key"),
            },
            binary,
        )
        return bool(detected.get("found")), message

    return True, ""


def _select_install_option(environment_spec: Dict[str, Any], target: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Pick the first install option whose prerequisites are satisfied."""
    options = environment_spec.get("install_options")
    if not isinstance(options, list) or not options:
        return None, []

    missing_messages: List[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        requirements = option.get("requires")
        unmet_for_option: List[str] = []
        for requirement in requirements if isinstance(requirements, list) else []:
            if not isinstance(requirement, dict):
                continue
            available, message = _check_install_requirement(requirement, target)
            if not available and message:
                unmet_for_option.append(message)
        if not unmet_for_option:
            return option, []
        if not missing_messages:
            missing_messages = unmet_for_option

    first_option = next((option for option in options if isinstance(option, dict)), None)
    return first_option, missing_messages


def _agent_status_label(status: str) -> str:
    """Map internal preflight states to short UI labels."""
    return {
        "installed": "Installed",
        "missing": "Missing",
        "unsupported_here": "Unsupported here",
        "missing_prerequisite": "Missing prerequisite",
        "needs_manual_install": "Manual install",
        "target_incomplete": "Awaiting target",
        "check_failed": "Check failed",
    }.get(status, "Unknown")


def _build_agent_preflight_request(agent_key: str, connection_mode: str, session_config: Dict[str, Any]) -> Dict[str, Any]:
    """Build one agent preflight request from a prepared session payload."""
    normalized_mode = _normalize_connection_mode(connection_mode)
    session_data = session_config if isinstance(session_config, dict) else {}
    return {
        "agent": agent_key,
        "connection_mode": normalized_mode,
        "ssh": {
            "host": str(session_data.get("host") or "").strip(),
            "username": str(session_data.get("username") or "ubuntu").strip() or "ubuntu",
            "password": str(session_data.get("password") or ""),
            "port": _normalize_port_number(session_data.get("port"), 22),
        },
        "wsl": {
            "distribution": str(session_data.get("distribution") or "").strip(),
            "username": str(session_data.get("username") or "").strip(),
            "default_dir": str(session_data.get("directory") or "").strip(),
        },
        "terminal": {
            "distribution": str(session_data.get("distribution") or "").strip(),
            "use_wsl": bool(session_data.get("use_wsl")),
            "use_powershell": bool(session_data.get("use_powershell")),
        },
    }


def _sanitize_agent_launch_commands(connection_mode: str, sessions: List[Dict[str, Any]]) -> List[str]:
    """Clear startup commands that already failed preflight inspection."""
    normalized_mode = _normalize_connection_mode(connection_mode)
    warnings: List[str] = []
    for index, session in enumerate(sessions):
        initial_command = str(session.get("initial_command") or "").strip()
        agent_key = _normalize_agent_key(initial_command)
        if agent_key not in AGENT_REGISTRY:
            continue

        preflight = _agent_preflight_payload(
            agent_key,
            _build_agent_preflight_request(agent_key, normalized_mode, session),
        )
        if preflight.get("status") != "check_failed":
            continue

        title = str(session.get("title") or f"Terminal {index + 1}").strip() or f"Terminal {index + 1}"
        warning = f"{title}: {preflight.get('message') or 'Agent preflight failed.'} Startup command cleared."
        logger.warning("Clearing startup command because agent preflight failed: %s", warning)
        session["initial_command"] = ""
        warnings.append(warning)

    return warnings


def _agent_preflight_payload(agent_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a registry-driven agent preflight response."""
    spec = AGENT_REGISTRY.get(agent_key)
    if not isinstance(spec, dict):
        raise ValueError("Unknown agent selection")

    target = _resolve_agent_target(payload)
    environment_key = str(target.get("environment_key") or "")
    environments = spec.get("environments") if isinstance(spec.get("environments"), dict) else {}
    environment_spec = environments.get(environment_key) if isinstance(environments.get(environment_key), dict) else {}
    label = str(spec.get("display_name") or spec.get("label") or agent_key)
    binary = str(spec.get("binary") or agent_key)
    verify = spec.get("verify") if isinstance(spec.get("verify"), list) else []
    post_install = str(spec.get("post_install") or "").strip()

    response = {
        "agent": agent_key,
        "label": label,
        "binary": binary,
        "status": "unsupported_here",
        "status_label": _agent_status_label("unsupported_here"),
        "message": f"{label} is not supported in {_agent_target_label(target)}.",
        "warning": str(environment_spec.get("warning") or "").strip(),
        "target": {
            "environment_key": environment_key,
            "shell_kind": str(target.get("shell_kind") or ""),
            "label": _agent_target_label(target),
            "host": str(target.get("host") or "").strip(),
            "distribution": str(target.get("distribution") or "").strip(),
        },
        "detection": {
            "command": "",
            "path": "",
            "kind": "",
        },
        "install": {
            "label": "",
            "command": "",
            "manual_only": False,
        },
        "verify": verify,
        "post_install": post_install,
        "missing_prerequisites": [],
    }

    if not environment_spec or not bool(environment_spec.get("supported")):
        return response

    detection = _detect_agent_binary_cached(target, binary)
    install_option, missing_prerequisites = _select_install_option(environment_spec, target)
    response["warning"] = str(environment_spec.get("warning") or "").strip()
    response["detection"] = {
        "command": str(detection.get("command") or ""),
        "path": str(detection.get("path") or ""),
        "kind": str(detection.get("kind") or ""),
    }
    response["install"] = {
        "label": str((install_option or {}).get("label") or ""),
        "command": str((install_option or {}).get("command") or ""),
        "manual_only": bool((install_option or {}).get("manual_only")),
    }
    response["missing_prerequisites"] = missing_prerequisites

    if detection.get("incomplete"):
        response["status"] = "target_incomplete"
        response["message"] = str(detection.get("error") or "The target environment is incomplete.")
    elif detection.get("failed"):
        response["status"] = "check_failed"
        response["message"] = str(detection.get("error") or "The preflight check failed.")
    elif detection.get("found"):
        response["status"] = "installed"
        response["message"] = f"{label} is available in {_agent_target_label(target)}."
    elif missing_prerequisites and environment_key != "wsl_linux":
        response["status"] = "missing_prerequisite"
        response["message"] = missing_prerequisites[0]
    elif bool(environment_spec.get("detect_only")) or bool((install_option or {}).get("manual_only")):
        response["status"] = "needs_manual_install"
        response["message"] = f"{label} is missing in {_agent_target_label(target)}."
    else:
        response["status"] = "missing"
        response["message"] = f"{label} is missing in {_agent_target_label(target)}."

    response["status_label"] = _agent_status_label(response["status"])
    return response


def _find_wsl_executable() -> Optional[str]:
    """Locate wsl.exe when WSL mode targets a Windows distro."""
    for candidate in ("wsl.exe", "/mnt/c/Windows/System32/wsl.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if os.path.exists(candidate):
            return candidate
    return None


def _parse_wsl_list_output(output: str) -> List[Dict[str, Any]]:
    """Parse `wsl -l -v` output into distro metadata."""
    distros: List[Dict[str, Any]] = []

    for raw_line in str(output or "").splitlines():
        line = raw_line.replace("\x00", "").strip()
        normalized = line.lower()

        if not line:
            continue
        if "name" in normalized and "state" in normalized and "version" in normalized:
            continue
        if normalized.startswith("the following is a list"):
            continue

        is_default = line.startswith("*")
        if is_default:
            line = line[1:].strip()

        parts = re.split(r"\s{2,}", line)
        if len(parts) < 3:
            continue

        distros.append(
            {
                "name": parts[0].strip(),
                "state": parts[1].strip(),
                "version": parts[2].strip(),
                "default": is_default,
            }
        )

    return distros


def _inspect_wsl_distributions() -> Dict[str, Any]:
    """Inspect local WSL distributions and return both parsed and raw command output."""
    command_label = "wsl -l -v"
    wsl_executable = _find_wsl_executable()
    if not wsl_executable:
        return {
            "available": False,
            "command": command_label,
            "distros": [],
            "raw_output": "",
            "error": "WSL is not available on this system.",
        }

    try:
        result = subprocess.run(
            [wsl_executable, "-l", "-v"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Failed to inspect WSL distributions: {exc}")
        return {
            "available": False,
            "command": command_label,
            "distros": [],
            "raw_output": "",
            "error": str(exc),
        }

    raw_output = "\n".join(
        part.strip("\n")
        for part in (result.stdout or "", result.stderr or "")
        if part
    ).strip()
    return {
        "available": True,
        "command": command_label,
        "distros": _parse_wsl_list_output(raw_output),
        "raw_output": raw_output,
        "error": "",
    }
