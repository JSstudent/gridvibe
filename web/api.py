"""
Web API for GridVibe frontend integration.
Provides REST endpoints and WebSocket support for terminal sessions.
"""

import atexit
import json
import logging
import os
import re
import select
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

from gridvibe_version import __version__
from sessions.manager import SessionManager, SessionStatus
from web.config import (
    WHISPER_MODEL_OPTIONS,
    _config_lock,
    _load_json_file,
    _merge_dicts,
    _normalize_surface_mode,
    load_config,
    runtime_config,
    save_config,
)
from web.explorer import (
    EXPLORER_FILE_PREVIEW_MAX_BYTES,
    _acquire_ssh_sftp,
    _append_deleted_git_entries,
    _attach_git_status_to_entries,
    _clean_git_entry_status,
    _evict_all_pooled_ssh_clients,
    _evict_pooled_ssh_client,
    _explorer_backend,
    _explorer_content_looks_binary,
    _explorer_editor_language,
    _explorer_root_directory,
    _get_git_context,
    _get_git_diff,
    _get_git_repo_summary,
    _git_commit,
    _git_publish,
    _git_stage_path,
    _git_status_for_entry,
    _git_unstage_path,
    _is_browser_session,
    _is_explorer_session,
    _is_markdown_file,
    _is_remote_explorer_session,
    _release_ssh_sftp,
    _remote_explorer_root_directory,
    _remote_is_directory,
    _remote_path_clean,
    _remote_path_inside,
    _render_markdown_preview,
    _resolve_explorer_candidate_path,
    _resolve_remote_explorer_candidate_path,
    _sftp_request_error_types,
)
from web.hostkeys import _load_persistent_host_keys
from web.paths import BASE_DIR
from web.secrets import _decrypt_password, _encrypt_password
from web.selfupdate import AppUpdateError, perform_self_update

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
    import tkinter as tk
    from tkinter import filedialog
except ImportError:  # pragma: no cover - optional for native folder picker
    tk = None
    filedialog = None

try:
    from winpty import PtyProcess as WinPtyProcess
except ImportError:  # pragma: no cover - Windows-only optional dependency
    WinPtyProcess = None

try:
    import websocket as ws_client
except ImportError:  # pragma: no cover - optional for voice input
    ws_client = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional for faster-whisper input handling
    np = None

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - optional for faster-whisper voice input
    WhisperModel = None

logger = logging.getLogger(__name__)
SAVED_SESSIONS_PATH = os.path.join(BASE_DIR, "saved_sessions.json")
AGENT_REGISTRY_PATH = os.path.join(BASE_DIR, "agent_registry.json")
DEFAULT_SAVED_SESSION_ID = "default-session"
DEFAULT_SAVED_SESSION_NAME = "Default Session"
WINDOWS_DEVICE_ATTRIBUTES_RESPONSE = "\x1b[?1;2c"
_browser_shutdown_lock = threading.RLock()
_browser_shutdown_token = ""


def configure_browser_shutdown(enabled: bool) -> str:
    """Enable process shutdown controls only for explicit browser launch mode."""
    global _browser_shutdown_token
    with _browser_shutdown_lock:
        _browser_shutdown_token = uuid.uuid4().hex if enabled else ""
        return _browser_shutdown_token



# ==================== Configuration ====================
# load_config/save_config and the RuntimeConfig singleton moved to
# web/config.py (finding 6.2); the names imported above stay re-exported here
# for backwards compatibility. Runtime settings are read as
# runtime_config.<name> so a refresh (or a test patch) is seen everywhere.


def _refresh_runtime_config():
    """Reload runtime config-backed settings from disk."""
    runtime_config.refresh()


def _active_voice_model_name() -> str:
    """Return the currently configured STT model name."""
    return runtime_config.whisper_model if runtime_config.voice_engine == "whisper" else runtime_config.vosk_model


def _public_app_config() -> Dict[str, Any]:
    """Return the subset of app config that the launcher can edit safely."""
    return {
        "appearance": {
            "theme": runtime_config.app_theme,
        },
        "workspace": {
            "surface_mode": runtime_config.app_surface_mode,
        },
        "voice_input": {
            "enabled": runtime_config.voice_enabled,
            "engine": runtime_config.voice_engine,
            "vosk_model": runtime_config.vosk_model,
            "whisper_model": runtime_config.whisper_model,
            "whisper_device": runtime_config.whisper_device,
            "whisper_compute_type": runtime_config.whisper_compute_type,
            "language": runtime_config.voice_language,
        }
    }


def _broadcast_app_config_update():
    """Notify open app windows that launcher-editable settings changed."""
    socketio.emit(
        "app_config_updated",
        {
            "workspace": {
                "surface_mode": runtime_config.app_surface_mode,
            },
            "timestamp": int(time.time() * 1000),
        },
    )


def _normalize_app_config_update(data: Any) -> Dict[str, Any]:
    """Validate and normalize launcher-editable app settings."""
    payload = data if isinstance(data, dict) else {}
    appearance = payload.get("appearance")
    if not isinstance(appearance, dict):
        appearance = {}
    theme = str(appearance.get("theme", runtime_config.app_theme)).strip().lower()
    if theme not in {"system", "light", "dark"}:
        theme = runtime_config.app_theme

    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
    surface_mode = _normalize_surface_mode(workspace.get("surface_mode"), runtime_config.app_surface_mode)

    voice_input = payload.get("voice_input")
    if not isinstance(voice_input, dict):
        voice_input = {}

    engine = str(voice_input.get("engine", runtime_config.voice_engine)).strip().lower()
    if engine not in {"vosk", "whisper"}:
        engine = runtime_config.voice_engine

    whisper_device_value = str(
        voice_input.get("whisper_device", runtime_config.whisper_device)
    ).strip().lower()
    if whisper_device_value not in {"cpu", "cuda"}:
        whisper_device_value = runtime_config.whisper_device

    next_whisper_model = str(
        voice_input.get("whisper_model", runtime_config.whisper_model)
    ).strip() or runtime_config.whisper_model
    if next_whisper_model not in WHISPER_MODEL_OPTIONS:
        next_whisper_model = "base"

    return {
        "appearance": {
            "theme": theme,
        },
        "workspace": {
            "surface_mode": surface_mode,
        },
        "voice_input": {
            "enabled": bool(voice_input.get("enabled", runtime_config.voice_enabled)),
            "engine": engine,
            "vosk_model": str(voice_input.get("vosk_model", runtime_config.vosk_model)).strip() or runtime_config.vosk_model,
            "whisper_model": next_whisper_model,
            "whisper_device": whisper_device_value,
            "whisper_compute_type": str(
                voice_input.get("whisper_compute_type", runtime_config.whisper_compute_type)
            ).strip() or runtime_config.whisper_compute_type,
            "language": str(voice_input.get("language", runtime_config.voice_language)).strip() or runtime_config.voice_language,
        }
    }


def _whisper_language_code(language: Any) -> Optional[str]:
    """Normalize app language strings such as en-US to faster-whisper codes."""
    if not isinstance(language, str):
        return None

    normalized = language.strip().lower()
    if not normalized or normalized == "auto":
        return None

    return normalized.split("-", 1)[0].split("_", 1)[0] or None


def _default_terminal_entries():
    """Build default per-terminal settings."""
    return [
        {
            "title": f"Terminal {index + 1}",
            "directory": "",
            "initial_command": "",
            "initial_command_mode": "command",
            "startup_mode": "terminal",
            "agent_selection": "",
            "custom_agent": "",
            "explorer_tree_open": False,
            "explorer_git_open": False,
            "distribution": "",
            "use_wsl": False,
            "use_powershell": False,
        }
        for index in range(runtime_config.max_sessions)
    ]


def _normalize_connection_mode(value: Any) -> str:
    """Normalize the requested connection mode."""
    return "wsl" if value == "wsl" else "ssh"


def _normalize_layout(value: Any, count: int) -> str:
    """Normalize the requested layout for a terminal count."""
    if count == 2:
        return value if value in {"vertical", "horizontal"} else "vertical"
    if count == 3:
        return value if value in {"vertical", "horizontal", "split"} else "vertical"
    if count >= 4:
        return "grid"
    return "single"


def _normalize_startup_mode(value: Any, connection_mode: str = "ssh") -> str:
    """Normalize the requested per-pane startup mode."""
    normalized = str(value or "").strip().lower()
    if normalized == "agent":
        return "agent"
    if normalized == "browser" and connection_mode == "wsl":
        return "browser"
    if normalized == "explorer" and connection_mode in {"ssh", "wsl"}:
        return "explorer"
    return "terminal"


def _normalize_browser_url(value: Any) -> str:
    """Return a browser-pane URL with only HTTP(S) schemes allowed."""
    raw_value = str(value or DEFAULT_BROWSER_URL).strip()
    if not raw_value:
        raise ValueError("Browser panes require an HTTP or HTTPS URL")

    candidate = raw_value
    if "://" not in candidate:
        candidate = f"http://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Browser panes only support http:// and https:// URLs")

    return candidate


DEFAULT_BROWSER_URL = "http://127.0.0.1:3000"


def _normalize_workspace_layout(data: Any, terminal_count: int) -> Optional[Dict[str, Any]]:
    """Normalize optional runtime workspace geometry stored with a saved preset."""
    if not isinstance(data, dict):
        return None

    raw_rects = data.get("split_slot_rects")
    if not isinstance(raw_rects, list) or len(raw_rects) != terminal_count:
        return None

    rects = []
    max_grid_line = max(2, runtime_config.max_sessions * 4)
    for index, raw_rect in enumerate(raw_rects):
        if not isinstance(raw_rect, dict):
            return None
        try:
            x = int(raw_rect.get("x", 1))
            y = int(raw_rect.get("y", 1))
            w = int(raw_rect.get("w", 1))
            h = int(raw_rect.get("h", 1))
            origin_slot = int(raw_rect.get("originSlot", index))
        except (TypeError, ValueError):
            return None

        if x < 1 or y < 1 or w < 1 or h < 1:
            return None
        if x + w - 1 > max_grid_line or y + h - 1 > max_grid_line:
            return None

        rects.append(
            {
                "originSlot": max(0, min(runtime_config.max_sessions - 1, origin_slot)),
                "x": x,
                "y": y,
                "w": w,
                "h": h,
            }
        )

    def normalize_weights(values: Any, target_length: int) -> List[float]:
        if not isinstance(values, list):
            return [1.0 for _ in range(target_length)]
        normalized = []
        for index in range(target_length):
            try:
                value = float(values[index])
            except (IndexError, TypeError, ValueError):
                value = 1.0
            normalized.append(max(0.01, min(value, 100.0)))
        return normalized

    column_count = max(rect["x"] + rect["w"] - 1 for rect in rects)
    row_count = max(rect["y"] + rect["h"] - 1 for rect in rects)
    try:
        original_count = int(data.get("original_split_slot_count", terminal_count))
    except (TypeError, ValueError):
        original_count = terminal_count

    return {
        "class_name": "layout-split-local",
        "split_slot_rects": rects,
        "split_column_weights": normalize_weights(data.get("split_column_weights"), column_count),
        "split_row_weights": normalize_weights(data.get("split_row_weights"), row_count),
        "original_split_slot_count": max(1, min(runtime_config.max_sessions, original_count)),
    }


def _default_session_config() -> Dict[str, Any]:
    """Default saved setup used by the launcher form."""
    default_count = min(4, runtime_config.max_sessions)
    return {
        "connection_mode": "ssh",
        "terminal_count": default_count,
        "layout": _normalize_layout("grid", default_count),
        "ssh": {
            "host": "",
            "username": "ubuntu",
            "password": "",
            "port": 22,
            "default_dir": "",
        },
        "wsl": {
            "distribution": "",
            "username": "",
            "default_dir": "",
        },
        "terminals": _default_terminal_entries(),
        "workspace_layout": None,
    }


def _default_saved_session_entry() -> Dict[str, Any]:
    """Return the built-in default launcher preset as a virtual saved session."""
    now = _utc_timestamp()
    return {
        "id": DEFAULT_SAVED_SESSION_ID,
        "name": DEFAULT_SAVED_SESSION_NAME,
        "created_at": now,
        "updated_at": now,
        "config": _default_session_config(),
    }


def _normalize_terminal_entries(entries: Any, connection_mode: str = "ssh") -> List[Dict[str, Any]]:
    """Ensure the saved terminal list is bounded and complete."""
    normalized = []
    entries = entries if isinstance(entries, list) else []

    for index in range(runtime_config.max_sessions):
        entry = entries[index] if index < len(entries) and isinstance(entries[index], dict) else {}
        use_powershell = bool(entry.get("use_powershell"))
        raw_startup_mode = entry.get("startup_mode")
        if raw_startup_mode is None:
            raw_startup_mode = "agent" if entry.get("initial_command_mode") == "agent" else "terminal"
        startup_mode = _normalize_startup_mode(raw_startup_mode, connection_mode)
        normalized.append(
            {
                "title": str(entry.get("title") or f"Terminal {index + 1}"),
                "directory": str(entry.get("directory") or ""),
                "initial_command": str(entry.get("initial_command") or ""),
                "initial_command_mode": startup_mode if startup_mode in {"agent", "explorer", "browser"} else "command",
                "startup_mode": startup_mode,
                "agent_selection": str(entry.get("agent_selection") or ""),
                "custom_agent": str(entry.get("custom_agent") or ""),
                "explorer_tree_open": bool(entry.get("explorer_tree_open")),
                "explorer_git_open": bool(entry.get("explorer_git_open")),
                "distribution": str(entry.get("distribution") or ""),
                "use_wsl": bool(entry.get("use_wsl")) and not use_powershell,
                "use_powershell": use_powershell,
            }
        )

    return normalized


def _normalize_session_config(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize persisted form state before saving or returning it."""
    data = data or {}
    default_config = _default_session_config()
    connection_mode = _normalize_connection_mode(data.get("connection_mode"))

    try:
        terminal_count = int(data.get("terminal_count", default_config["terminal_count"]))
    except (TypeError, ValueError):
        terminal_count = default_config["terminal_count"]

    terminal_count = max(1, min(runtime_config.max_sessions, terminal_count))
    ssh_data = data.get("ssh") if isinstance(data.get("ssh"), dict) else {}
    wsl_data = data.get("wsl") if isinstance(data.get("wsl"), dict) else {}

    try:
        ssh_port = int(ssh_data.get("port", default_config["ssh"]["port"])) # type: ignore
    except (TypeError, ValueError):
        ssh_port = default_config["ssh"]["port"]

    ssh_port = max(1, min(65535, ssh_port))

    return {
        "connection_mode": connection_mode,
        "terminal_count": terminal_count,
        "layout": _normalize_layout(data.get("layout"), terminal_count),
        "ssh": {
            "host": str(ssh_data.get("host") or ""),# type: ignore
            "username": str(ssh_data.get("username") or default_config["ssh"]["username"]),# type: ignore
            "password": str(ssh_data.get("password") or ""), # type: ignore
            "port": ssh_port,
            "default_dir": str(ssh_data.get("default_dir") or ""),# type: ignore
        },
        "wsl": {
            "distribution": str(wsl_data.get("distribution") or ""),# type: ignore
            "username": str(wsl_data.get("username") or ""),# type: ignore
            "default_dir": str(wsl_data.get("default_dir") or default_config["wsl"]["default_dir"]),# type: ignore
        },
        "terminals": _normalize_terminal_entries(data.get("terminals"), connection_mode),
        "workspace_layout": _normalize_workspace_layout(data.get("workspace_layout"), terminal_count),
    }


def _merge_workspace_session_config(
    base_config: Dict[str, Any],
    workspace_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply saveable live-workspace state without changing launcher setup fields."""
    base = _normalize_session_config(base_config)
    workspace_input = dict(workspace_config or {})
    workspace_input["connection_mode"] = base["connection_mode"]
    workspace = _normalize_session_config(workspace_input)
    merged = json.loads(json.dumps(base))

    merged["terminal_count"] = workspace["terminal_count"]
    merged["layout"] = workspace["layout"]
    merged["workspace_layout"] = workspace["workspace_layout"]

    for index in range(min(len(merged["terminals"]), len(workspace["terminals"]))):
        saved_terminal = merged["terminals"][index]
        workspace_terminal = workspace["terminals"][index]
        startup_mode = workspace_terminal["startup_mode"]

        saved_terminal["startup_mode"] = startup_mode
        saved_terminal["initial_command_mode"] = (
            startup_mode if startup_mode in {"agent", "explorer", "browser"} else "command"
        )

        # Agent identity and command are required mode metadata. Unlike the
        # terminal directory, these values must follow the live pane so the
        # saved preset can recreate Codex, Claude, or a custom agent pane.
        if startup_mode == "agent":
            agent_selection = workspace_terminal["agent_selection"]
            custom_agent = workspace_terminal["custom_agent"]
            initial_command = workspace_terminal["initial_command"]
            if not initial_command:
                initial_command = custom_agent if agent_selection == "other" else agent_selection
            saved_terminal["agent_selection"] = agent_selection
            saved_terminal["custom_agent"] = custom_agent
            saved_terminal["initial_command"] = initial_command
        elif (
            base["terminals"][index]["initial_command_mode"] == "agent"
            or base["terminals"][index]["agent_selection"]
            or base["terminals"][index]["custom_agent"]
        ):
            saved_terminal["agent_selection"] = ""
            saved_terminal["custom_agent"] = ""
            saved_terminal["initial_command"] = ""

        saved_terminal["explorer_tree_open"] = (
            startup_mode == "explorer" and workspace_terminal["explorer_tree_open"]
        )
        saved_terminal["explorer_git_open"] = (
            startup_mode == "explorer" and workspace_terminal["explorer_git_open"]
        )

        # Browser panes need a valid URL, but navigation performed in the live
        # browser pane is transient. Keep an existing configured browser URL or
        # use the product default when a pane is newly switched to browser mode.
        if startup_mode == "browser" and base["terminals"][index]["startup_mode"] != "browser":
            saved_terminal["initial_command"] = DEFAULT_BROWSER_URL

    return _normalize_session_config(merged)


def load_session_config() -> Dict[str, Any]:
    """Load launcher settings plus last-used saved-session metadata."""
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    config = last_entry["config"] if last_entry else _default_session_config()

    config["last_session"] = state["last_session"]
    config["saved_session"] = _saved_session_meta(last_entry) if last_entry else None
    return config


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp string for persisted metadata."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _generate_saved_session_id() -> str:
    """Build a short unique identifier for a saved launcher preset."""
    return f"session-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"


def _normalize_saved_session_entry(entry: Any, encrypt_password: bool = False) -> Optional[Dict[str, Any]]:
    """Validate and normalize one saved session entry."""
    if not isinstance(entry, dict):
        return None

    session_id = str(entry.get("id") or _generate_saved_session_id()).strip()
    if not session_id:
        session_id = _generate_saved_session_id()

    created_at = str(entry.get("created_at") or _utc_timestamp())
    updated_at = str(entry.get("updated_at") or created_at)
    config = _normalize_session_config(entry.get("config"))

    if encrypt_password and config.get("ssh", {}).get("password"):
        config["ssh"]["password"] = _encrypt_password(config["ssh"]["password"])

    name = str(entry.get("name") or session_id).strip() or session_id

    return {
        "id": session_id,
        "name": name,
        "created_at": created_at,
        "updated_at": updated_at,
        "config": config,
    }


def _load_saved_sessions_payload() -> Dict[str, Any]:
    """Load named saved launcher presets and last-used metadata from disk."""
    if not os.path.exists(SAVED_SESSIONS_PATH):
        return {"sessions": [], "last_session": ""}

    try:
        with open(SAVED_SESSIONS_PATH, "r", encoding="utf-8") as file_handle:
            raw = json.load(file_handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to load {SAVED_SESSIONS_PATH}: {exc}")
        return {"sessions": [], "last_session": ""}

    has_last_session_field = False
    if isinstance(raw, dict):
        raw_entries = raw.get("sessions", [])
        has_last_session_field = "last_session" in raw
        last_session = str(raw.get("last_session") or "").strip()
    else:
        raw_entries = raw
        last_session = ""

    normalized_entries = []
    seen_ids = set()
    for entry in raw_entries if isinstance(raw_entries, list) else []:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=False)
        if normalized is not None:
            if normalized.get("config", {}).get("ssh", {}).get("password"):
                normalized["config"]["ssh"]["password"] = _decrypt_password(
                    normalized["config"]["ssh"]["password"]
                )
            if normalized["id"] in seen_ids:
                continue
            seen_ids.add(normalized["id"])
            normalized_entries.append(normalized)

    normalized_entries.sort(key=lambda item: item["updated_at"], reverse=True)
    valid_last_session_ids = set(seen_ids)
    valid_last_session_ids.add(DEFAULT_SAVED_SESSION_ID)
    if last_session and last_session not in valid_last_session_ids:
        last_session = ""
    if not has_last_session_field and not last_session and normalized_entries:
        last_session = normalized_entries[0]["id"]
    return {"sessions": normalized_entries, "last_session": last_session}


def load_saved_sessions() -> List[Dict[str, Any]]:
    """Load the saved launcher presets list from disk."""
    return _load_saved_sessions_payload()["sessions"]


def _save_saved_sessions_payload(
    entries: List[Dict[str, Any]],
    last_session: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist named saved launcher presets plus last-used metadata to disk."""
    normalized_entries = []
    seen_ids = set()
    for entry in entries:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=False)
        if normalized is not None:
            if normalized["id"] in seen_ids:
                continue
            seen_ids.add(normalized["id"])
            normalized_entries.append(normalized)

    normalized_entries.sort(key=lambda item: item["updated_at"], reverse=True)
    if not normalized_entries:
        if os.path.exists(SAVED_SESSIONS_PATH):
            try:
                os.remove(SAVED_SESSIONS_PATH)
            except OSError as exc:
                logger.warning(f"Failed to remove {SAVED_SESSIONS_PATH}: {exc}")
        return {"sessions": [], "last_session": ""}

    valid_ids = {entry["id"] for entry in normalized_entries}
    valid_ids.add(DEFAULT_SAVED_SESSION_ID)
    if last_session is None:
        last_session_value = normalized_entries[0]["id"] if normalized_entries else ""
    else:
        last_session_value = str(last_session).strip()
        if last_session_value and last_session_value not in valid_ids:
            last_session_value = ""

    encrypted_entries = []
    for entry in normalized_entries:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=True)
        if normalized is not None:
            encrypted_entries.append(normalized)

    with open(SAVED_SESSIONS_PATH, "w", encoding="utf-8") as file_handle:
        json.dump(
            {
                "last_session": last_session_value,
                "sessions": encrypted_entries,
            },
            file_handle,
            indent=2,
        )
    return {"sessions": normalized_entries, "last_session": last_session_value}


def save_saved_sessions(entries: List[Dict[str, Any]], last_session: Optional[str] = None):
    """Persist named saved launcher presets to disk."""
    return _save_saved_sessions_payload(entries, last_session=last_session)


def _find_saved_session_entry(entries: List[Dict[str, Any]], session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return one saved session entry by id."""
    target_id = str(session_id or "").strip()
    if not target_id:
        return None
    if target_id == DEFAULT_SAVED_SESSION_ID:
        return _default_saved_session_entry()

    for entry in entries:
        if entry["id"] == target_id:
            return entry
    return None


def _saved_session_meta(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Return minimal metadata for the active saved session."""
    if not entry:
        return None
    return {"id": entry["id"], "name": entry["name"]}


def _saved_session_response(entry: Dict[str, Any], include_config: bool = False) -> Dict[str, Any]:
    """Return a client-friendly saved session payload."""
    config = entry["config"]
    response = {
        "id": entry["id"],
        "name": entry["name"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "connection_mode": config["connection_mode"],
        "terminal_count": config["terminal_count"],
        "layout": config["layout"],
        "is_default": entry["id"] == DEFAULT_SAVED_SESSION_ID,
    }
    if include_config:
        response["config"] = config
    return response


def _normalize_launch_session_id(value: Any) -> str:
    """Normalize the optional saved-session identifier attached to a launch."""
    return str(value or "").strip()


def _build_launch_group_id(saved_session_id: Any) -> str:
    """Return a stable launched-group key for one saved-session identifier."""
    normalized = _normalize_launch_session_id(saved_session_id)
    if not normalized:
        return ""

    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip("-")
    return f"saved-session-{safe_id or DEFAULT_SAVED_SESSION_ID}"


def upsert_saved_session(
    config: Dict[str, Any],
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    set_last_session: bool = True,
) -> Dict[str, Any]:
    """Create or update one named saved session preset."""
    normalized_config = _normalize_session_config(config)
    if str(session_id or "").strip() == DEFAULT_SAVED_SESSION_ID:
        session_id = None
    normalized_name = str(name or session_id or "").strip()
    state = _load_saved_sessions_payload()
    saved_sessions = state["sessions"]
    now = _utc_timestamp()

    if session_id:
        for entry in saved_sessions:
            if entry["id"] == session_id:
                entry["name"] = normalized_name or entry["name"]
                entry["updated_at"] = now
                entry["config"] = normalized_config
                save_saved_sessions(
                    saved_sessions,
                    last_session=entry["id"] if set_last_session else state["last_session"],
                )
                return entry

    entry_id = session_id or _generate_saved_session_id()
    entry = {
        "id": entry_id,
        "name": normalized_name or entry_id,
        "created_at": now,
        "updated_at": now,
        "config": normalized_config,
    }
    saved_sessions.append(entry)
    save_saved_sessions(
        saved_sessions,
        last_session=entry_id if set_last_session else state["last_session"],
    )
    return entry


def set_last_saved_session(session_id: Optional[str]):
    """Persist the last-used saved session id when it still exists."""
    state = _load_saved_sessions_payload()
    target_id = str(session_id or "").strip()
    if not state["sessions"] and target_id != DEFAULT_SAVED_SESSION_ID:
        return state
    return save_saved_sessions(state["sessions"], last_session=target_id)


def delete_saved_sessions(session_ids: List[str]) -> Dict[str, Any]:
    """Delete one or more saved presets."""
    state = _load_saved_sessions_payload()
    target_ids = {str(session_id).strip() for session_id in session_ids if str(session_id).strip()}
    remaining_entries = [entry for entry in state["sessions"] if entry["id"] not in target_ids]

    if not remaining_entries:
        return save_saved_sessions([], last_session="")

    next_last_session = state["last_session"]
    if next_last_session in target_ids or not _find_saved_session_entry(remaining_entries, next_last_session):
        next_last_session = remaining_entries[0]["id"]

    return save_saved_sessions(remaining_entries, last_session=next_last_session)


active_launch_options: Dict[str, Any] = {
    "connection_mode": "ssh",
    "layout": _normalize_layout("grid", min(4, runtime_config.max_sessions)),
    "terminal_count": min(4, runtime_config.max_sessions),
}


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


def _agent_options() -> List[Dict[str, str]]:
    """Return launcher agent choices sourced from the registry."""
    options = [
        {
            "value": key,
            "label": str(spec.get("label") or key),
        }
        for key, spec in AGENT_REGISTRY.items()
    ]
    options.sort(key=lambda item: item["label"])
    options.append({"value": "other", "label": "other"})
    return options


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
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        _load_persistent_host_keys(client)
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


def _resolve_secret_key() -> bytes | str:
    """Return a session signing key without shipping a static public secret."""
    env_secret = os.environ.get("GRIDVIBE_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if env_secret:
        return env_secret

    configured_secret = runtime_config.app_config.get("security", {}).get("secret_key")
    if isinstance(configured_secret, str) and configured_secret.strip():
        return configured_secret

    return os.urandom(32)


def _resolve_cors_origins():
    """Return Socket.IO CORS origins; defaults to same-origin only.

    The Socket.IO channel accepts terminal input, so a wildcard here would let
    any web page in the user's browser reach live shells. An explicit
    ``security.cors_origins`` list in config (including ``["*"]``) still wins,
    e.g. for reverse-proxy setups.
    """
    configured = runtime_config.app_config.get("security", {}).get("cors_origins")
    if configured:
        return configured
    server_config = runtime_config.app_config.get("server", {})
    port = server_config.get("port", 5050)
    host = str(server_config.get("host", "127.0.0.1")).strip()
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if host and host not in {"127.0.0.1", "localhost", "0.0.0.0", "::", "::1"}:
        origins.append(f"http://{host}:{port}")
    return origins
# Create Flask app
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["SECRET_KEY"] = _resolve_secret_key()
app.config['JSON_SORT_KEYS'] = False

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins=_resolve_cors_origins(), async_mode="threading")


def _allowed_write_origin_netlocs() -> Optional[set]:
    """Origins allowed to issue state-changing requests; None means allow all."""
    netlocs = set()
    for entry in runtime_config.app_config.get("security", {}).get("cors_origins") or []:
        entry = str(entry).strip()
        if entry == "*":
            return None
        parsed = urlparse(entry if "//" in entry else f"//{entry}")
        if parsed.netloc:
            netlocs.add(parsed.netloc.lower())
    host = request.host.lower()
    netlocs.add(host)
    netlocs.add(host.replace("127.0.0.1", "localhost", 1))
    netlocs.add(host.replace("localhost", "127.0.0.1", 1))
    return netlocs


@app.before_request
def _reject_cross_origin_writes():
    """Reject cross-origin state-changing requests.

    CORS stops a hostile page from *reading* responses, but "simple"
    cross-origin POSTs still execute server-side. The app's own pages send a
    matching Origin header (or none for non-CORS requests and pywebview),
    while a cross-site fetch/form post always carries the attacker's origin.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return None
    allowed = _allowed_write_origin_netlocs()
    if allowed is None:
        return None
    if origin.lower() == "null" or urlparse(origin).netloc.lower() not in allowed:
        logger.warning(
            "Rejected cross-origin %s %s from Origin %s",
            request.method,
            request.path,
            origin,
        )
        return jsonify({"error": "Cross-origin request rejected"}), 403
    return None

# Initialize session manager
session_manager = SessionManager()

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
folder_dialog_lock = threading.Lock()


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


def _resolve_group_id() -> str:
    """Return the requested session group id, if any."""
    return str(request.args.get("group") or "").strip()


def _get_group_response_meta(group_id: str) -> Dict[str, Any]:
    """Return layout metadata for one session group."""
    group = session_manager.get_group(group_id)
    if not group:
        launch_options = active_launch_options
        return {
            "group": None,
            "layout": launch_options["layout"],
            "connection_mode": launch_options["connection_mode"],
            "terminal_count": 0,
            "workspace_layout": None,
            "surface_mode": runtime_config.app_surface_mode,
        }

    return {
        "group": group.to_dict(),
        "layout": group.layout,
        "connection_mode": group.connection_mode,
        "terminal_count": group.terminal_count,
        "workspace_layout": group.workspace_layout,
        "surface_mode": group.surface_mode,
    }


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


def _pick_local_folder(initial_dir: str = "") -> str:
    """Open a native folder picker and return the selected path."""
    if tk is None or filedialog is None:
        raise RuntimeError("Native folder picker support is unavailable")

    candidate_dir = os.path.expanduser(str(initial_dir or "").strip())
    if candidate_dir and not os.path.isdir(candidate_dir):
        candidate_dir = os.path.dirname(candidate_dir)
    if not candidate_dir or not os.path.isdir(candidate_dir):
        candidate_dir = os.path.expanduser("~")

    with folder_dialog_lock:
        root = tk.Tk()
        root.withdraw()

        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        try:
            root.update_idletasks()
            selected = filedialog.askdirectory(
                parent=root,
                initialdir=candidate_dir,
                mustexist=True,
                title="Select Local Repository",
            )
        finally:
            root.destroy()

    return str(selected or "").strip()


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


# ==================== HTML Routes ====================

@app.route('/')
def index():
    """Main page with terminal interface."""
    logger.info("GET /")
    with _browser_shutdown_lock:
        browser_shutdown_token = _browser_shutdown_token
    return render_template(
        'index.html',
        max_sessions=runtime_config.max_sessions,
        agent_options=_agent_options(),
        local_windows_shells_available=os.name == "nt",
        browser_shutdown_enabled=bool(browser_shutdown_token),
        browser_shutdown_token=browser_shutdown_token,
        version=__version__,
    )


@app.route('/terminals')
def terminals_page():
    """Page showing active terminal instances."""
    logger.info("GET /terminals")
    return render_template('terminals.html', max_sessions=runtime_config.max_sessions,
                           app_surface_mode=runtime_config.app_surface_mode,
                           voice_enabled=runtime_config.voice_enabled,
                           voice_engine=runtime_config.voice_engine,
                           voice_model=_active_voice_model_name(),
                           voice_language=runtime_config.voice_language,
                           version=__version__)


@app.route('/docs/images/<path:filename>')
def docs_images(filename: str):
    """Serve bundled documentation images used by the local UI."""
    return send_from_directory(os.path.join(BASE_DIR, "docs", "images"), filename)


# ==================== API Routes ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "GridVibe",
        "version": __version__
    })


def _shutdown_browser_process():
    """Allow the HTTP response to flush, then close sessions and exit."""
    time.sleep(0.2)
    logger.info("Browser mode requested application shutdown")
    try:
        session_manager.close_all_sessions()
    except Exception:
        logger.exception("Failed to close sessions during browser shutdown")
    os._exit(0)


def _schedule_browser_shutdown():
    shutdown_thread = threading.Thread(
        target=_shutdown_browser_process,
        name="gridvibe-browser-shutdown",
        daemon=True,
    )
    shutdown_thread.start()


@app.route('/api/browser-shutdown', methods=['POST'])
def shutdown_browser_application():
    """End GridVibe only when explicit browser mode enabled this endpoint."""
    with _browser_shutdown_lock:
        expected_token = _browser_shutdown_token
    if not expected_token:
        return jsonify({"error": "Browser shutdown is unavailable"}), 404

    provided_token = request.headers.get("X-GridVibe-Shutdown-Token", "")
    if provided_token != expected_token:
        return jsonify({"error": "Invalid shutdown token"}), 403

    logger.info("Accepted browser mode shutdown request")
    _schedule_browser_shutdown()
    return jsonify({"message": "GridVibe is shutting down"}), 202


@app.route('/api/app-update', methods=['POST'])
def update_application():
    """Check for git updates, apply them when available, and report the outcome."""
    try:
        result = perform_self_update()
        logger.info(
            "Application update check completed branch=%s updated=%s behind=%s ahead=%s",
            result.get("branch"),
            result.get("updated"),
            result.get("behind_count"),
            result.get("ahead_count"),
        )
        return jsonify(result)
    except AppUpdateError as exc:
        logger.warning("Application update blocked: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        logger.exception("Unexpected application update failure")
        return jsonify({"error": f"Unexpected update failure: {exc}"}), 500


@app.route('/api/app-config', methods=['GET'])
def get_app_config():
    """Return launcher-editable application settings."""
    return jsonify(_public_app_config())


@app.route('/api/app-config', methods=['POST'])
def set_app_config():
    """Persist launcher-editable application settings to config.json."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid payload"}), 400

    with _config_lock:
        current = load_config()
        current = _merge_dicts(current, _normalize_app_config_update(data))
        save_config(current)
    _refresh_runtime_config()
    _broadcast_app_config_update()
    return jsonify(_public_app_config())


@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get all sessions."""
    group_id = _resolve_group_id()
    sessions = (
        session_manager.get_group_sessions(group_id)
        if group_id
        else session_manager.get_all_sessions()
    )
    logger.debug(
        f"GET /api/sessions  group={group_id or 'all'} count={len(sessions)} "
        f"statuses={[s.status.value for s in sessions]}"
    )
    payload = {
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
    }
    if group_id:
        payload.update(_get_group_response_meta(group_id))
    else:
        launch_options = active_launch_options
        payload.update(
            {
                "group": None,
                "layout": launch_options["layout"],
                "connection_mode": launch_options["connection_mode"],
                "terminal_count": launch_options["terminal_count"],
                "workspace_layout": None,
                "surface_mode": runtime_config.app_surface_mode,
            }
        )
    return jsonify(payload)


@app.route('/api/explorer/<session_id>/entries', methods=['GET'])
def get_explorer_entries(session_id: str):
    """List entries for a file explorer pane."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args["path"] if "path" in request.args else None

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, current_path = backend.resolve_dir(requested_path)
        entries = backend.list_entries(root_path, current_path)
        git_context, git_statuses = _get_git_context(backend, root_path, current_path)
        _attach_git_status_to_entries(backend, root_path, git_context, git_statuses, entries)
        _append_deleted_git_entries(backend, root_path, current_path, git_context, git_statuses, entries)
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
        return {
            "root": root_path,
            "path": backend.rel_explorer_path(root_path, current_path),
            "parent_path": backend.parent_explorer_path(root_path, current_path),
            "git": git_context,
            "entries": entries,
        }

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/file', methods=['GET'])
def get_explorer_file(session_id: str):
    """Return a safe, read-only text preview for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_file(requested_path)
        size, modified = backend.stat_file(file_path)
        code_language = _explorer_editor_language(file_path)
        raw_content = backend.read_file_prefix(file_path, EXPLORER_FILE_PREVIEW_MAX_BYTES + 1)

        if _explorer_content_looks_binary(raw_content):
            raise ValueError("Explorer file appears to be binary")

        truncated = len(raw_content) > EXPLORER_FILE_PREVIEW_MAX_BYTES
        preview_bytes = raw_content[:EXPLORER_FILE_PREVIEW_MAX_BYTES]
        content = preview_bytes.decode("utf-8", errors="replace")
        preview_html = _render_markdown_preview(content) if _is_markdown_file(file_path) else None
        git_context, git_statuses = _get_git_context(backend, root_path, backend.file_dirname(file_path))
        file_git = (
            _git_status_for_entry(backend, str(git_context["repo_root"]), file_path, git_statuses)
            if git_context.get("available")
            else _clean_git_entry_status()
        )
        return {
            "root": root_path,
            "path": backend.rel_explorer_path(root_path, file_path),
            "name": backend.basename(file_path),
            "size": size,
            "modified": modified,
            "encoding": "utf-8",
            "truncated": truncated,
            "content": content,
            "preview_type": "markdown" if preview_html is not None else None,
            "preview_html": preview_html,
            "language": code_language,
            "git": file_git,
            "git_context": git_context,
        }

    return _explorer_route_response(session, handler)


def _explorer_route_response(session: Any, handler: Any):
    """Run one explorer Git route handler with shared backend and error mapping."""
    error_types = (
        _sftp_request_error_types()
        if _is_remote_explorer_session(session)
        else (OSError,)
    )
    try:
        with _explorer_backend(session) as backend:
            payload = handler(backend)
        return jsonify({"session_id": session.session_id, **payload})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except error_types as exc:
        return jsonify({"error": str(exc)}), 500


@app.route('/api/explorer/<session_id>/git/diff', methods=['GET'])
def get_explorer_git_diff(session_id: str):
    """Return a bounded read-only Git diff for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    mode = request.args.get("mode", "worktree")
    commit = request.args.get("commit")
    requested_path = request.args.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_diff_path(requested_path)
        diff_payload = _get_git_diff(backend, root_path, file_path, mode, commit)
        return {
            "root": root_path,
            "path": backend.rel_explorer_path(root_path, file_path),
            "mode": mode,
            **diff_payload,
        }

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/repo', methods=['GET'])
def get_explorer_git_repo(session_id: str):
    """Return bounded read-only Git repository metadata for the diff sidebar."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path = backend.root_directory()
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/stage', methods=['POST'])
def stage_explorer_git_file(session_id: str):
    """Stage one changed file in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_stage_path(backend, root_path, file_path)
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/unstage', methods=['POST'])
def unstage_explorer_git_file(session_id: str):
    """Unstage one file in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_unstage_path(backend, root_path, file_path)
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/commit', methods=['POST'])
def commit_explorer_git(session_id: str):
    """Commit staged changes in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path = backend.root_directory()
        _git_commit(backend, root_path, message)
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/publish', methods=['POST'])
def publish_explorer_git(session_id: str):
    """Push the current branch of an explorer Git repository to its remote."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path = backend.root_directory()
        _git_publish(backend, root_path)
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/sessions/active', methods=['GET'])
def get_active_sessions():
    """Get all active sessions."""
    group_id = _resolve_group_id()
    if group_id:
        sessions = [
            session
            for session in session_manager.get_group_sessions(group_id)
            if session.status == SessionStatus.CONNECTED
        ]
        meta = _get_group_response_meta(group_id)
    else:
        sessions = session_manager.get_active_sessions()
        launch_options = active_launch_options
        meta = {
            "group": None,
            "layout": launch_options["layout"],
            "connection_mode": launch_options["connection_mode"],
            "terminal_count": launch_options["terminal_count"],
            "workspace_layout": None,
        }
    return jsonify({
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
        **meta,
    })


@app.route('/api/session-groups', methods=['GET'])
def get_session_groups():
    """Return all launched groups for the session tabs."""
    groups = [group.to_dict() for group in session_manager.get_all_groups()]
    return jsonify({"groups": groups, "count": len(groups)})


@app.route('/api/session-groups/order', methods=['POST'])
def reorder_session_groups():
    """Persist the requested session-tab order."""
    data = request.get_json(silent=True) or {}
    group_ids = data.get("group_ids")
    if not isinstance(group_ids, list) or not group_ids:
        return jsonify({"error": "A non-empty 'group_ids' list is required"}), 400

    groups = [group.to_dict() for group in session_manager.reorder_groups(group_ids)]
    _broadcast_session_groups_updated("reordered")
    return jsonify({"groups": groups, "count": len(groups)})


@app.route('/api/session-config', methods=['GET'])
def get_session_config():
    """Load the launcher configuration from the local override or project default."""
    return jsonify(load_session_config())


@app.route('/api/session-config', methods=['POST'])
def persist_session_config():
    """Persist the last-used saved session selection."""
    data = request.get_json(silent=True) or {}
    set_last_saved_session(data.get("saved_session_id"))
    return jsonify(load_session_config())


@app.route('/api/select-folder', methods=['POST'])
def select_folder():
    """Open a native folder picker for local repo mode."""
    data = request.get_json(silent=True) or {}

    try:
        selected = _pick_local_folder(str(data.get("initial_dir") or ""))
    except RuntimeError as exc:
        if str(exc) == "Native folder picker support is unavailable":
            logger.info("Native folder picker unavailable; local repo path can be entered manually")
            return jsonify({
                "path": "",
                "selected": False,
                "manual_entry": True,
                "error": str(exc),
            })
        logger.error(f"Folder picker failed: {exc}")
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        logger.error(f"Folder picker failed: {exc}")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"path": selected, "selected": bool(selected)})


@app.route('/api/wsl-distros', methods=['GET'])
def get_wsl_distros():
    """Return the locally available WSL distros from `wsl -l -v`."""
    snapshot = _inspect_wsl_distributions()
    return jsonify(snapshot)


@app.route('/api/ssh-ping', methods=['POST'])
def ssh_ping():
    """Ping the launcher SSH target from the local machine."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(_ping_ssh_target(data.get("host"), data.get("port", 22)))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route('/api/agent-preflight', methods=['POST'])
def agent_preflight():
    """Run selection-time CLI detection for one configured agent target."""
    data = request.get_json(silent=True) or {}
    agent_key = _normalize_agent_key(data.get("agent"))
    if not agent_key or agent_key == "other":
        return jsonify({"error": "Select a known agent before running preflight."}), 400

    try:
        return jsonify(_agent_preflight_payload(agent_key, data))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route('/api/saved-sessions', methods=['GET'])
def get_saved_sessions():
    """Return all named saved launcher presets."""
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    sessions = [_saved_session_response(entry) for entry in state["sessions"]]
    return jsonify(
        {
            "sessions": sessions,
            "count": len(sessions),
            "last_session": state["last_session"],
            "saved_session": _saved_session_meta(last_entry),
            "default_session": _saved_session_response(_default_saved_session_entry()),
        }
    )


@app.route('/api/saved-sessions', methods=['POST'])
def create_saved_session():
    """Persist one named saved launcher preset."""
    data = request.get_json(silent=True) or {}
    raw_config = data.get("config") if isinstance(data.get("config"), dict) else {}
    config = _normalize_session_config(raw_config)
    if data.get("workspace_only") is True:
        state = _load_saved_sessions_payload()
        source_session_id = str(
            data.get("id") or data.get("source_saved_session_id") or ""
        ).strip()
        source_entry = (
            _default_saved_session_entry()
            if source_session_id == DEFAULT_SAVED_SESSION_ID
            else _find_saved_session_entry(state["sessions"], source_session_id)
        )
        if source_entry:
            config = _merge_workspace_session_config(source_entry["config"], raw_config)
    group_id = str(data.get("group_id") or "").strip()
    activate_saved_session = data.get("activate", True) is not False
    saved_entry = upsert_saved_session(
        config=config,
        name=data.get("name"),
        session_id=data.get("id"),
        set_last_session=activate_saved_session,
    )
    group = (
        session_manager.update_group_saved_session(
            group_id,
            saved_entry["id"],
            saved_entry["name"],
        )
        if group_id
        else None
    )
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    return jsonify(
        {
            **_saved_session_response(saved_entry, include_config=True),
            "last_session": state["last_session"],
            "saved_session": _saved_session_meta(last_entry),
            "activated": activate_saved_session,
            "group": group.to_dict() if group else None,
        }
    ), 201


@app.route('/api/saved-sessions', methods=['DELETE'])
def remove_saved_sessions():
    """Delete one or more saved launcher presets."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids")

    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "At least one saved session id is required"}), 400

    state = delete_saved_sessions(raw_ids)
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    return jsonify(
        {
            "message": "Saved sessions updated successfully",
            "deleted_ids": [str(session_id).strip() for session_id in raw_ids if str(session_id).strip()],
            "sessions": [_saved_session_response(entry) for entry in state["sessions"]],
            "count": len(state["sessions"]),
            "last_session": state["last_session"],
            "saved_session": _saved_session_meta(last_entry),
            "config": last_entry["config"] if last_entry else _default_session_config(),
        }
    )


@app.route('/api/saved-sessions/<saved_session_id>', methods=['GET'])
def get_saved_session(saved_session_id: str):
    """Return one named saved launcher preset."""
    if saved_session_id == DEFAULT_SAVED_SESSION_ID:
        return jsonify(_saved_session_response(_default_saved_session_entry(), include_config=True))

    for entry in load_saved_sessions():
        if entry["id"] == saved_session_id:
            return jsonify(_saved_session_response(entry, include_config=True))

    return jsonify({"error": "Saved session not found"}), 404


@app.route('/api/sessions', methods=['POST'])
def create_sessions():
    """
    Create one or more terminal sessions.

    Request body:
    {
        "sessions": [
            {
                "host": "server1",
                "directory": "/home/user/repo",
                "username": "ubuntu",
                "password": "",
                "port": 22,
                "initial_command": "codex",
                "title": "Terminal 2"
            }
        ]
    }
    """
    global active_launch_options
    try:
        data = request.get_json()
        logger.info(f"POST /api/sessions  body={data}")

        if not data or 'sessions' not in data:
            logger.warning("Missing 'sessions' in request body")
            return jsonify({"error": "Missing 'sessions' in request body"}), 400

        sessions_config = data['sessions']
        if not isinstance(sessions_config, list) or not sessions_config:
            logger.warning("Empty or invalid sessions list")
            return jsonify({"error": "At least one session is required"}), 400

        if len(sessions_config) > runtime_config.max_sessions:
            logger.warning(f"Too many sessions requested: {len(sessions_config)} > {runtime_config.max_sessions}")
            return jsonify({"error": f"Maximum {runtime_config.max_sessions} sessions allowed"}), 400

        connection_mode = _normalize_connection_mode(data.get("connection_mode"))
        layout = _normalize_layout(data.get("layout"), len(sessions_config))
        workspace_layout = _normalize_workspace_layout(data.get("workspace_layout"), len(sessions_config))
        surface_mode = _normalize_surface_mode(data.get("surface_mode"), runtime_config.app_surface_mode)
        session_name = str(data.get("session_name") or "").strip()
        saved_session_id = _normalize_launch_session_id(data.get("saved_session_id"))
        stable_group_id = _build_launch_group_id(saved_session_id) or None
        prepared_sessions = []

        for config in sessions_config:
            prepared = dict(config)
            prepared["mode"] = connection_mode
            startup_mode = _normalize_startup_mode(
                prepared.get("startup_mode") or prepared.get("initial_command_mode"),
                connection_mode,
            )
            prepared["startup_mode"] = startup_mode

            if connection_mode == "ssh" and startup_mode == "explorer":
                prepared["initial_command"] = ""
                prepared["explorer_root_directory"] = prepared.get("directory") or ""

            if connection_mode == "wsl":
                if startup_mode == "browser":
                    use_powershell = False
                    use_wsl = False
                    prepared["initial_command"] = _normalize_browser_url(
                        prepared.get("initial_command")
                    )
                    prepared["initial_command_mode"] = "browser"
                    prepared["explorer_root_directory"] = None
                    prepared["distribution"] = ""
                    prepared["username"] = ""
                elif startup_mode == "explorer":
                    use_powershell = False
                    use_wsl = False
                    prepared["initial_command"] = ""
                    prepared["explorer_root_directory"] = prepared.get("directory") or ""
                    prepared["distribution"] = ""
                    prepared["username"] = ""
                else:
                    use_powershell = os.name == "nt" and bool(prepared.get("use_powershell"))
                    use_wsl = os.name == "nt" and bool(prepared.get("use_wsl")) and not use_powershell
                prepared["password"] = None
                prepared["port"] = 22
                prepared["host"] = (
                    "Browser"
                    if startup_mode == "browser"
                    else (
                        "File Explorer"
                        if startup_mode == "explorer"
                        else _local_shell_display_name(
                            use_wsl=use_wsl,
                            use_powershell=use_powershell,
                            distribution=prepared.get("distribution"),
                        )
                    )
                )
                prepared["use_wsl"] = use_wsl
                prepared["use_powershell"] = use_powershell

            prepared_sessions.append(prepared)

        launch_warnings = _sanitize_agent_launch_commands(connection_mode, prepared_sessions)

        # Atomic reference swap instead of in-place update so concurrent
        # readers never observe a half-updated layout/count pair.
        active_launch_options = {
            **active_launch_options,
            "connection_mode": connection_mode,
            "layout": layout,
            "terminal_count": len(prepared_sessions),
        }

        if stable_group_id:
            _replace_group_sessions(stable_group_id)

        group = session_manager.create_group(
            name=session_name or f"Session {time.strftime('%H:%M:%S')}",
            connection_mode=connection_mode,
            layout=layout,
            terminal_count=len(prepared_sessions),
            group_id=stable_group_id,
            saved_session_id=saved_session_id,
            workspace_layout=workspace_layout,
            surface_mode=surface_mode,
        )
        logger.info(
            "Created session group group_id=%s saved_session_id=%r name=%r mode=%s layout=%s terminal_count=%d",
            group.group_id,
            saved_session_id,
            group.name,
            connection_mode,
            layout,
            len(prepared_sessions),
        )

        created_sessions = session_manager.create_sessions(
            prepared_sessions,
            group_id=group.group_id,
        )
        logger.info(f"Created {len(created_sessions)} sessions")
        if not created_sessions:
            logger.error("No valid sessions were created")
            session_manager.remove_group(group.group_id)
            return jsonify({"error": "No valid sessions were created"}), 400

        logger.info(
            "Launch summary group_id=%s sessions=%s",
            group.group_id,
            [
                {
                    "session_id": session.session_id,
                    "title": session.title,
                    "host": session.host,
                    "directory": session.directory,
                    "mode": session.mode,
                }
                for session in created_sessions
            ],
        )

        for session in created_sessions:
            if _is_explorer_session(session) or _is_browser_session(session):
                session_manager.update_session_status(session.session_id, SessionStatus.CONNECTED)
                _broadcast_session_status(session.session_id)
                continue
            logger.info(
                "Spawning connection task for session_id=%s mode=%s host=%s group_id=%s",
                session.session_id,
                session.mode,
                session.host,
                session.group_id,
            )
            socketio.start_background_task(_connect_session, session.session_id)

        _broadcast_session_groups_updated("launched")

        return jsonify({
            "sessions": [s.to_dict() for s in created_sessions],
            "count": len(created_sessions),
            "group_id": group.group_id,
            "group": group.to_dict(),
            "layout": layout,
            "connection_mode": connection_mode,
            "terminal_count": len(created_sessions),
            "workspace_layout": workspace_layout,
            "surface_mode": surface_mode,
            "launch_target": "web",
            "warnings": launch_warnings,
        }), 201

    except ValueError as e:
        logger.warning("Invalid session launch request: %s", e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating sessions: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id: str):
    """Get a specific session by ID."""
    session = session_manager.get_session(session_id)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    return jsonify(session.to_dict())


@app.route('/api/sessions/<session_id>/split', methods=['POST'])
def split_session(session_id: str):
    """Append one cloned terminal session to the source session's group."""
    source = session_manager.get_session(session_id)
    if not source:
        return jsonify({"error": "Session not found"}), 404

    if _is_explorer_session(source) or _is_browser_session(source):
        return jsonify({"error": "Explorer and browser panes cannot be split"}), 400

    group = session_manager.get_group(source.group_id)
    if not group:
        return jsonify({"error": "Session group not found"}), 404

    group_sessions = session_manager.get_group_sessions(group.group_id)
    if len(group_sessions) >= runtime_config.max_sessions:
        return jsonify({"error": f"Maximum {runtime_config.max_sessions} sessions allowed"}), 400

    title = f"Terminal {len(group_sessions) + 1}"
    new_session = session_manager.append_session_to_group(
        group_id=group.group_id,
        host=source.host,
        directory=source.directory,
        username=source.username,
        port=source.port,
        password=source.password,
        initial_command=None,
        initial_command_mode="command",
        agent_selection="",
        custom_agent="",
        title=title,
        mode=source.mode,
        distribution=source.distribution,
        use_wsl=source.use_wsl,
        use_powershell=source.use_powershell,
        startup_mode=source.startup_mode,
        explorer_root_directory=source.explorer_root_directory,
    )
    if not new_session:
        return jsonify({"error": "Session group not found"}), 404

    logger.info(
        "Split session source_id=%s new_session_id=%s group_id=%s",
        source.session_id,
        new_session.session_id,
        group.group_id,
    )
    socketio.start_background_task(_connect_session, new_session.session_id)
    _broadcast_session_groups_updated("split")

    return jsonify(
        {
            "session": new_session.to_dict(),
            "group_id": group.group_id,
            "group": group.to_dict(),
            "terminal_count": group.terminal_count,
        }
    ), 201


@app.route('/api/sessions/<session_id>/mode', methods=['POST'])
def change_session_mode(session_id: str):
    """Switch one pane between terminal, file explorer, and browser modes."""
    session = session_manager.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    if session.mode not in {"ssh", "wsl"}:
        return jsonify({"error": "Pane mode switching is only available for SSH and Local Repo sessions"}), 400

    data = request.get_json(silent=True) or {}
    target_mode = _normalize_startup_mode(data.get("startup_mode"), session.mode)
    if target_mode not in {"terminal", "explorer", "browser"}:
        return jsonify({"error": "startup_mode must be 'terminal', 'explorer', or 'browser'"}), 400

    if target_mode == "browser":
        if session.mode != "wsl":
            return jsonify({"error": "Browser mode is only available for Local Repo sessions"}), 400
        try:
            browser_url = _normalize_browser_url(
                data.get("url")
                or data.get("initial_command")
                or session.initial_command
                or DEFAULT_BROWSER_URL
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        session_manager.update_session_metadata(
            session_id,
            host="Browser",
            username="",
            port=22,
            password=None,
            initial_command=browser_url,
            initial_command_mode="browser",
            startup_mode="browser",
        )
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _close_ssh_connection(session_id, clear_buffer=True)
        _broadcast_session_status(session_id)
        return jsonify(session_manager.get_session(session_id).to_dict())

    if target_mode == "explorer":
        requested_directory = data.get("directory")
        if data.get("refresh_cwd"):
            requested_directory = _resolve_live_terminal_cwd(session_id, session) or requested_directory
        next_directory = session.directory
        root_directory = ""

        if session.mode == "ssh":
            if requested_directory:
                next_directory = _remote_path_clean(requested_directory)
            next_directory = _remote_path_clean(next_directory or "/")
            root_candidate = _remote_explorer_root_directory(session) or next_directory
            client = None
            sftp = None
            try:
                client, sftp = _acquire_ssh_sftp(session)
                next_directory = sftp.normalize(next_directory)
                if not _remote_is_directory(sftp, next_directory):
                    raise ValueError("Explorer root directory does not exist")
                try:
                    root_directory = sftp.normalize(root_candidate)
                    root_is_valid = _remote_is_directory(sftp, root_directory)
                except OSError:
                    root_directory = next_directory
                    root_is_valid = False
                if not root_is_valid or not _remote_path_inside(root_directory, next_directory):
                    root_directory = next_directory
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except _sftp_request_error_types() as exc:
                return jsonify({"error": str(exc)}), 500
            finally:
                _release_ssh_sftp(session, client, sftp)

            session_manager.update_session_metadata(
                session_id,
                directory=next_directory,
                explorer_root_directory=root_directory,
                initial_command="",
                startup_mode="explorer",
            )
        else:
            if requested_directory:
                next_directory = os.path.abspath(os.path.expanduser(str(requested_directory)))
            if not next_directory or not os.path.isdir(next_directory):
                return jsonify({"error": "Explorer root directory does not exist"}), 400

            root_directory = _explorer_root_directory(session) or next_directory
            root_directory = os.path.realpath(os.path.abspath(os.path.expanduser(root_directory)))
            next_directory = os.path.realpath(os.path.abspath(os.path.expanduser(next_directory)))
            try:
                common_path = os.path.commonpath([root_directory, next_directory])
            except ValueError:
                common_path = ""
            if (
                not os.path.isdir(root_directory)
                or os.path.normcase(common_path) != os.path.normcase(root_directory)
            ):
                root_directory = next_directory

            session_manager.update_session_metadata(
                session_id,
                host="File Explorer",
                directory=next_directory,
                explorer_root_directory=root_directory,
                username="",
                port=22,
                password=None,
                initial_command="",
                startup_mode="explorer",
            )
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _close_ssh_connection(session_id, clear_buffer=True)
        _broadcast_session_status(session_id)
        return jsonify(session_manager.get_session(session_id).to_dict())

    if not (_is_explorer_session(session) or _is_browser_session(session)):
        return jsonify(session.to_dict())

    next_directory = session.directory
    root_path = _explorer_root_directory(session)
    if _is_browser_session(session):
        next_directory = session.directory
    elif _is_remote_explorer_session(session):
        client = None
        sftp = None
        try:
            client, sftp = _acquire_ssh_sftp(session)
            root_path, selected_directory = _resolve_remote_explorer_candidate_path(
                sftp,
                session,
                data.get("directory", ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except _sftp_request_error_types() as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            _release_ssh_sftp(session, client, sftp)
        next_directory = selected_directory
    else:
        try:
            root_path, selected_directory = _resolve_explorer_candidate_path(
                session,
                data.get("directory", ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not os.path.isdir(selected_directory):
            return jsonify({"error": "Selected explorer path is not a directory"}), 400
        next_directory = selected_directory

    updates = {
        "directory": next_directory,
        "explorer_root_directory": root_path,
        "initial_command": "",
        "initial_command_mode": "command",
        "startup_mode": "terminal",
    }
    if session.mode == "wsl":
        updates["host"] = _local_shell_display_name(
            use_wsl=session.use_wsl,
            use_powershell=session.use_powershell,
            distribution=session.distribution,
        )
    session_manager.update_session_metadata(session_id, **updates)
    session_manager.update_session_status(session_id, SessionStatus.PENDING)
    _broadcast_session_status(session_id)
    socketio.start_background_task(_connect_session, session_id)
    return jsonify(session_manager.get_session(session_id).to_dict())


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def close_session(session_id: str):
    """Close a specific session."""
    success = session_manager.close_session(session_id)

    if not success:
        return jsonify({"error": "Session not found"}), 404

    _close_ssh_connection(session_id, clear_buffer=True)
    session_manager.clear_disconnected_sessions()
    _broadcast_session_groups_updated("session_closed")

    return jsonify({"message": "Session closed successfully"})


@app.route('/api/sessions', methods=['DELETE'])
def close_all_sessions():
    """Close all sessions."""
    group_id = _resolve_group_id()
    if group_id:
        sessions = session_manager.get_group_sessions(group_id)
        if not sessions and not session_manager.get_group(group_id):
            return jsonify({"error": "Session group not found"}), 404

        for session in sessions:
            session_manager.close_session(session.session_id)
            _close_ssh_connection(session.session_id, clear_buffer=True)

        # The user explicitly closed this group, so it must not survive on the
        # empty-group grace period.
        session_manager.clear_disconnected_sessions(force_group_ids={group_id})
        _broadcast_session_groups_updated("group_closed")
        return jsonify({"message": "Session group closed successfully", "group_id": group_id})

    session_manager.close_all_sessions()
    _close_all_ssh_connections(clear_buffers=True)
    session_manager.reset_sessions()
    _broadcast_session_groups_updated("all_closed")

    return jsonify({"message": "All sessions closed successfully"})


# ==================== WebSocket Events ====================

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info(f"Client connected: {request.sid}") # type: ignore
    emit('connected', {'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info(f"Client disconnected: {request.sid}") # type: ignore
    _clear_client_joined_sessions(request.sid) # type: ignore


_TERMINAL_QUERY_RE = re.compile(
    r'\x1b\['       # CSI
    r'[>=]?'        # DA2 (>) or DA3 (=) prefix, optional
    r'(?:0?c|\?[0-9;]*c)'  # Device Attributes request or response
    r'|'
    r'\x1b\[[56]n'  # Device Status Report / Cursor Position Report
    r'|'
    r'\x1b\](?:1[012]);\?(?:\x07|\x1b\\)'  # OSC foreground/background/cursor color query
)


@socketio.on('join_session')
def handle_join_session(data):
    """
    Join a terminal session room.

    Expected data:
    {
        "session_id": "abc123"
    }
    """
    session_id = data.get('session_id')

    if not session_id:
        emit('error', {'message': 'Missing session_id'})
        return

    session = session_manager.get_session(session_id)
    if session:
        emit('session_status', session.to_dict())

    with connection_lock:
        joined_sessions = client_joined_sessions.get(request.sid) # type: ignore
        if joined_sessions is None:
            client_joined_sessions[request.sid] = set() # type: ignore
            if len(client_joined_sessions) > _MAX_TRACKED_SOCKET_CLIENTS:
                oldest_client_id = next(iter(client_joined_sessions))
                client_joined_sessions.pop(oldest_client_id, None)
            joined_sessions = client_joined_sessions[request.sid] # type: ignore
        should_replay_buffer = session_id not in joined_sessions
        if should_replay_buffer:
            joined_sessions.add(session_id)
        join_room(session_id)
        logger.info(f"Client {request.sid} joined session {session_id}") # type: ignore
        # Snapshot the replay buffer while still holding the lock; the emit
        # happens outside it so a slow client cannot stall other terminals.
        # join_room already happened under the lock, so any output cached after
        # this point reaches the client live, after the replay.
        buffered_output = _get_buffered_terminal_output(session_id) if should_replay_buffer else ""

    if buffered_output:
        buffered_output = _TERMINAL_QUERY_RE.sub('', buffered_output)
        emit('terminal_output', {
            'session_id': session_id,
            'data': buffered_output
        })


@socketio.on('leave_session')
def handle_leave_session(data):
    """Leave a terminal session room."""
    session_id = data.get('session_id')

    if session_id:
        with connection_lock:
            joined_sessions = client_joined_sessions.get(request.sid) # type: ignore
            if joined_sessions is not None:
                joined_sessions.discard(session_id)
                if not joined_sessions:
                    client_joined_sessions.pop(request.sid, None) # type: ignore
        leave_room(session_id)
        logger.info(f"Client {request.sid} left session {session_id}")# type: ignore


@socketio.on('clear_terminal_buffer')
def handle_clear_terminal_buffer(data):
    """Clear the rolling replay buffer for one terminal session."""
    session_id = data.get('session_id')

    if not session_id:
        emit('error', {'message': 'Missing session_id'})
        return

    _clear_terminal_output_buffer(session_id)


@socketio.on('terminal_input')
def handle_terminal_input(data):
    """
    Handle terminal input from web client.

    Expected data:
    {
        "session_id": "abc123",
        "data": "ls -la\n"
    }
    """
    session_id = data.get('session_id')
    input_data = data.get('data', '')

    if not session_id:
        emit('error', {'message': 'Missing session_id'})
        return

    with connection_lock:
        connection = ssh_connections.get(session_id)

    if not connection:
        emit('error', {'message': 'Session is not connected'})
        return

    try:
        sanitized_input = _sanitize_terminal_input(connection, input_data)
        if not sanitized_input:
            return
        _track_terminal_agent_input(session_id, connection, sanitized_input)
        _send_connection_input(connection, sanitized_input)
    except Exception as e:
        logger.error(f"Error sending input: {e}")
        emit('terminal_output', {
            'session_id': session_id,
            'data': f'\r\nError: {str(e)}\r\n'
        })


@socketio.on('terminal_resize')
def handle_terminal_resize(data):
    """Resize the backend PTY to match the visible xterm size."""
    session_id = data.get('session_id')
    cols = data.get('cols')
    rows = data.get('rows')

    if not session_id or cols is None or rows is None:
        return

    with connection_lock:
        connection = ssh_connections.get(session_id)

    if not connection:
        return

    try:
        _resize_connection(connection, cols, rows)
    except Exception as exc:
        logger.warning(f"Failed to resize terminal {session_id}: {exc}")


# ==================== Voice Input (Vosk / faster-whisper) ====================

_vosk_process = None
_vosk_ws_connections: Dict[str, Any] = {}
_vosk_lock = threading.Lock()
_vosk_session_locks: Dict[str, threading.Lock] = {}
_vosk_process_lock = threading.Lock()
_whisper_model_instance = None
_whisper_model_params = None
_whisper_model_lock = threading.Lock()
_whisper_audio_buffers: Dict[str, bytearray] = {}
_whisper_audio_lock = threading.Lock()
# Engine each active recording started with, so audio/stop keep routing to it
# even if the user switches engines in App Settings mid-recording.
_active_voice_sessions: Dict[str, str] = {}
_active_voice_sessions_lock = threading.Lock()


def _whisper_engine_available() -> bool:
    """Return True when faster-whisper and numpy are importable."""
    return WhisperModel is not None and np is not None


def _voice_engine_unavailable_message(engine: Optional[str] = None) -> str:
    """Return a user-facing reason the configured voice backend cannot start."""
    selected_engine = engine or runtime_config.voice_engine
    if selected_engine == "whisper":
        missing = []
        if WhisperModel is None:
            missing.append("faster-whisper")
        if np is None:
            missing.append("numpy")
        if missing:
            return (
                "Cannot start faster-whisper because missing package(s): "
                + " and ".join(missing)
                + ". Install optional voice dependencies with: "
                "pip install -r requirements-voice.txt"
            )
        return "faster-whisper is not available."

    if ws_client is None:
        return (
            "Cannot start Vosk because websocket-client is not installed. "
            "Install optional voice dependencies with: pip install -r requirements-voice.txt"
        )
    return "Voice backend is not available."


def _ensure_whisper_model():
    """Load the configured faster-whisper model lazily, rebuilding it when settings change."""
    global _whisper_model_instance
    global _whisper_model_params

    if WhisperModel is None:
        raise RuntimeError(
            "faster-whisper is not installed. Install it with: pip install faster-whisper"
        )
    if np is None:
        raise RuntimeError(
            "numpy is not installed. Install it with: pip install numpy"
        )

    with _whisper_model_lock:
        wanted_params = (runtime_config.whisper_model, runtime_config.whisper_device, runtime_config.whisper_compute_type)
        if _whisper_model_instance is None or _whisper_model_params != wanted_params:
            logger.info(
                "Loading faster-whisper model %s on %s (%s)",
                runtime_config.whisper_model,
                runtime_config.whisper_device,
                runtime_config.whisper_compute_type,
            )
            _whisper_model_instance = WhisperModel(
                runtime_config.whisper_model,
                device=runtime_config.whisper_device,
                compute_type=runtime_config.whisper_compute_type,
            )
            _whisper_model_params = wanted_params
        return _whisper_model_instance


def _pcm16le_to_float32(audio_bytes: bytes):
    """Convert raw PCM16LE bytes to normalized float32 mono samples."""
    if np is None:
        raise RuntimeError(
            "numpy is required to decode PCM audio for faster-whisper"
        )
    if not audio_bytes:
        return np.array([], dtype=np.float32)

    pcm = np.frombuffer(audio_bytes, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _transcribe_whisper_audio(audio_bytes: bytes) -> str:
    """Run a final transcription pass over the buffered session audio."""
    model = _ensure_whisper_model()
    audio_array = _pcm16le_to_float32(audio_bytes)
    language = _whisper_language_code(runtime_config.voice_language)
    segments, _info = model.transcribe(
        audio_array,
        language=language,
        beam_size=1,
        best_of=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return " ".join(
        segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()
    ).strip()


def _vosk_service_reachable(timeout=2.0):
    """Return True when the configured Vosk WebSocket endpoint accepts a handshake."""
    if ws_client is None:
        return False

    ws = None
    try:
        ws = ws_client.create_connection(runtime_config.vosk_service_url, timeout=timeout)
        return True
    except Exception:
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _ensure_vosk_service():
    """Start vosk-service subprocess if not already running."""
    global _vosk_process

    with _vosk_process_lock:
        if _vosk_service_reachable(timeout=1.5):
            if _vosk_process is None:
                logger.info("Using already-running vosk-service at %s", runtime_config.vosk_service_url)
            return True

        if _vosk_process is not None and _vosk_process.poll() is None:
            if _wait_for_vosk_ready(_vosk_process, timeout=runtime_config.vosk_startup_timeout_seconds):
                return True

        # Clean up a dead process handle
        _vosk_process = None

        vosk_script = os.path.join(BASE_DIR, "services", "vosk_service.py")
        if not os.path.exists(vosk_script):
            logger.error("vosk_service.py not found at %s", vosk_script)
            return False

        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                [sys.executable, vosk_script],
                creationflags=creation_flags,
            )
            _vosk_process = process

            # Poll until the service accepts a real WebSocket handshake.
            if not _wait_for_vosk_ready(process, timeout=runtime_config.vosk_startup_timeout_seconds):
                if process.poll() is None:
                    logger.error(
                        "vosk-service not ready after %ss; model download/load may still be in progress or the service may be hung",
                        runtime_config.vosk_startup_timeout_seconds,
                    )
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except Exception:
                        pass
                else:
                    logger.error(
                        "vosk-service exited before becoming ready (code %s)",
                        process.returncode,
                    )
                _vosk_process = None
                return False
            logger.info("vosk-service started (PID %d)", process.pid)
            return True
        except Exception as exc:
            logger.error("Failed to start vosk-service: %s", exc)
            return False


def _wait_for_vosk_ready(process, timeout=30):
    """Block until vosk-service accepts a WebSocket connection, or timeout."""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False  # process exited
        if _vosk_service_reachable(timeout=1.5):
            return True
        time.sleep(0.5)
    return False


def _restart_vosk_service():
    """Kill the vosk-service and start a fresh one."""
    global _vosk_process
    with _vosk_process_lock:
        if _vosk_process is not None:
            try:
                _vosk_process.kill()
                _vosk_process.wait(timeout=3)
            except Exception:
                pass
            _vosk_process = None
    return _ensure_vosk_service()


def _stop_vosk_service():
    """Terminate the vosk-service subprocess and close all voice connections."""
    global _vosk_process
    with _vosk_lock:
        for ws in _vosk_ws_connections.values():
            try:
                ws.close()
            except Exception:
                pass
        _vosk_ws_connections.clear()
        _vosk_session_locks.clear()

    with _vosk_process_lock:
        if _vosk_process is not None and _vosk_process.poll() is None:
            _vosk_process.terminate()
            try:
                _vosk_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _vosk_process.kill()
            logger.info("vosk-service stopped")
        _vosk_process = None

    with _whisper_audio_lock:
        _whisper_audio_buffers.clear()


atexit.register(_stop_vosk_service)


@app.route('/api/voice-status', methods=['GET'])
def voice_status_endpoint():
    """Check voice input availability and service status."""
    if runtime_config.voice_engine == "vosk":
        service_running: Optional[bool] = _vosk_service_reachable(timeout=1.0)
        service_url = runtime_config.vosk_service_url
        engine_available = ws_client is not None
    else:
        service_running = None
        service_url = ""
        engine_available = _whisper_engine_available()
    status_message = (
        "Voice backend is available."
        if engine_available
        else _voice_engine_unavailable_message(runtime_config.voice_engine)
    )
    return jsonify({
        'enabled': runtime_config.voice_enabled,
        'engine': runtime_config.voice_engine,
        'engine_available': engine_available,
        'ws_client_available': ws_client is not None,
        'service_running': service_running,
        'service_url': service_url,
        'model': _active_voice_model_name(),
        'language': runtime_config.voice_language,
        'startup_timeout_seconds': runtime_config.vosk_startup_timeout_seconds,
        'whisper_device': runtime_config.whisper_device,
        'whisper_compute_type': runtime_config.whisper_compute_type,
        'status_message': status_message,
    })


VOICE_PREFS_VALID_KEYS = {'profile', 'deviceId', 'pttEnabled', 'pttKeybind'}


def _default_voice_prefs() -> Dict[str, Any]:
    return {
        'profile': 'laptop',
        'deviceId': '',
        'pttEnabled': False,
        'pttKeybind': '',
    }


def _load_voice_prefs() -> Dict[str, Any]:
    cfg = load_config()
    stored = cfg.get('voice_prefs', {})
    defaults = _default_voice_prefs()
    return {k: stored.get(k, v) for k, v in defaults.items()}


def _save_voice_prefs(prefs: Dict[str, Any]):
    with _config_lock:
        cfg = load_config()
        cfg['voice_prefs'] = prefs
        save_config(cfg)


@app.route('/api/voice-prefs', methods=['GET'])
def get_voice_prefs():
    """Return persisted voice preferences."""
    return jsonify(_load_voice_prefs())


@app.route('/api/voice-prefs', methods=['POST'])
def set_voice_prefs():
    """Persist voice preferences to config.json."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Invalid payload'}), 400
    current = _load_voice_prefs()
    for key in VOICE_PREFS_VALID_KEYS:
        if key in data:
            current[key] = data[key]
    _save_voice_prefs(current)
    return jsonify(current)


def _start_vosk_voice_session(session_id: str):
    """Start a Vosk-backed voice session."""
    if ws_client is None:
        emit('voice_status', {'status': 'error',
                              'message': 'websocket-client package not installed'})
        return

    if not _ensure_vosk_service():
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': 'Voice service unavailable — is vosk installed?',
        })
        return

    with _vosk_lock:
        old_ws = _vosk_ws_connections.pop(session_id, None)
        _vosk_session_locks.pop(session_id, None)
    if old_ws:
        try:
            old_ws.close()
        except Exception:
            pass

    try:
        ws = ws_client.create_connection(runtime_config.vosk_service_url, timeout=5)
        ws.send(json.dumps({"config": {"sample_rate": 16000}}))
        with _vosk_lock:
            leaked = _vosk_ws_connections.pop(session_id, None)
            _vosk_ws_connections[session_id] = ws
            _vosk_session_locks[session_id] = threading.Lock()
        if leaked:
            try:
                leaked.close()
            except Exception:
                pass
        emit('voice_status', {'session_id': session_id, 'status': 'listening'})
        logger.info("Voice started for session %s using Vosk", session_id)
    except Exception as exc:
        logger.warning("vosk-service connection failed, restarting: %s", exc)
        try:
            ws.close()
        except Exception:
            pass
        with _vosk_lock:
            stale = _vosk_ws_connections.pop(session_id, None)
            _vosk_session_locks.pop(session_id, None)
        if stale:
            try:
                stale.close()
            except Exception:
                pass
        if _restart_vosk_service():
            try:
                ws = ws_client.create_connection(runtime_config.vosk_service_url, timeout=5)
                ws.send(json.dumps({"config": {"sample_rate": 16000}}))
                with _vosk_lock:
                    leaked = _vosk_ws_connections.pop(session_id, None)
                    _vosk_ws_connections[session_id] = ws
                    _vosk_session_locks[session_id] = threading.Lock()
                if leaked:
                    try:
                        leaked.close()
                    except Exception:
                        pass
                emit('voice_status', {'session_id': session_id,
                                      'status': 'listening'})
                logger.info("Voice started for session %s using Vosk (after restart)",
                            session_id)
                return
            except Exception as retry_exc:
                logger.error("Retry also failed: %s", retry_exc)
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': f'Cannot reach voice service: {exc}',
        })


def _start_whisper_voice_session(session_id: str):
    """Start a faster-whisper-backed voice session."""
    with _whisper_audio_lock:
        _whisper_audio_buffers[session_id] = bytearray()

    try:
        _ensure_whisper_model()
    except Exception as exc:
        with _whisper_audio_lock:
            _whisper_audio_buffers.pop(session_id, None)
        logger.error("Cannot start faster-whisper for session %s: %s", session_id, exc)
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': f'Cannot start faster-whisper: {exc}',
        })
        return

    emit('voice_status', {'session_id': session_id, 'status': 'listening'})
    logger.info("Voice started for session %s using faster-whisper", session_id)


def _handle_vosk_audio_chunk(session_id: str, audio: Any):
    """Forward an audio chunk to vosk-service and relay the transcription result."""
    with _vosk_lock:
        ws = _vosk_ws_connections.get(session_id)
        session_lock = _vosk_session_locks.get(session_id)
    if not ws or not session_lock:
        return

    if not session_lock.acquire(timeout=2):
        return
    try:
        raw = audio if isinstance(audio, bytes) else bytes(audio)
        ws.send(raw, opcode=0x2)  # OPCODE_BINARY
        result_str = ws.recv()
        if result_str:
            parsed = json.loads(result_str)
            text = parsed.get('text', '')
            partial = parsed.get('partial', '')
            if text:
                emit('voice_result', {
                    'session_id': session_id,
                    'text': text,
                    'final': True,
                })
            elif partial:
                emit('voice_result', {
                    'session_id': session_id,
                    'text': partial,
                    'final': False,
                })
    except Exception as exc:
        logger.error("Voice audio proxy error for %s: %s", session_id, exc)
        with _vosk_lock:
            _vosk_ws_connections.pop(session_id, None)
            _vosk_session_locks.pop(session_id, None)
        try:
            ws.close()
        except Exception:
            pass
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': 'Voice service connection lost',
        })
    finally:
        session_lock.release()


def _handle_whisper_audio_chunk(session_id: str, audio: Any):
    """Buffer raw PCM audio for later faster-whisper transcription."""
    raw = audio if isinstance(audio, bytes) else bytes(audio)
    with _whisper_audio_lock:
        buffer = _whisper_audio_buffers.get(session_id)
        if buffer is None:
            buffer = bytearray()
            _whisper_audio_buffers[session_id] = buffer
        buffer.extend(raw)


def _stop_vosk_voice_session(session_id: str):
    """Stop a Vosk-backed voice session and flush final text."""
    with _vosk_lock:
        ws = _vosk_ws_connections.pop(session_id, None)
        session_lock = _vosk_session_locks.pop(session_id, None)

    if not ws:
        return

    acquired = session_lock.acquire(timeout=5) if session_lock else True
    try:
        ws.send('{"eof": 1}')
        result_str = ws.recv()
        if result_str:
            parsed = json.loads(result_str)
            text = parsed.get('text', '')
            if text:
                emit('voice_result', {
                    'session_id': session_id,
                    'text': text,
                    'final': True,
                })
    except Exception as exc:
        logger.debug("Error during voice_stop flush: %s", exc)
    finally:
        try:
            ws.close()
        except Exception:
            pass
        if session_lock and acquired:
            session_lock.release()


def _stop_whisper_voice_session(session_id: str):
    """Stop a faster-whisper-backed voice session and emit final text."""
    with _whisper_audio_lock:
        audio = bytes(_whisper_audio_buffers.pop(session_id, bytearray()))

    if not audio:
        return

    try:
        text = _transcribe_whisper_audio(audio)
    except Exception as exc:
        logger.error("faster-whisper transcription failed for %s: %s", session_id, exc)
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': f'faster-whisper transcription failed: {exc}',
        })
        return

    if text:
        emit('voice_result', {
            'session_id': session_id,
            'text': text,
            'final': True,
        })


@socketio.on('voice_start')
def handle_voice_start(data):
    """
    Start voice recognition for a terminal session.
    Uses the configured backend engine and prepares per-session state.
    """
    logger.info("voice_start requested by client %s for session %s",
                request.sid, data.get('session_id'))  # type: ignore[arg-type]
    if not runtime_config.voice_enabled:
        emit('voice_status', {'status': 'error',
                              'message': 'Voice input is disabled in config'})
        return

    session_id = data.get('session_id')
    if not session_id:
        emit('voice_status', {'status': 'error', 'message': 'Missing session_id'})
        return

    engine = 'whisper' if runtime_config.voice_engine == 'whisper' else 'vosk'
    with _active_voice_sessions_lock:
        _active_voice_sessions[session_id] = engine

    if engine == 'whisper':
        _start_whisper_voice_session(session_id)
        return

    _start_vosk_voice_session(session_id)


@socketio.on('voice_audio')
def handle_voice_audio(data):
    """
    Forward or buffer an audio chunk for the configured voice engine.
    Audio is raw PCM int16, 16 kHz mono.
    """
    session_id = data.get('session_id')
    audio = data.get('audio')
    if not session_id or not audio:
        return

    with _active_voice_sessions_lock:
        engine = _active_voice_sessions.get(session_id, runtime_config.voice_engine)

    if engine == 'whisper':
        _handle_whisper_audio_chunk(session_id, audio)
        return

    _handle_vosk_audio_chunk(session_id, audio)


@socketio.on('voice_stop')
def handle_voice_stop(data):
    """
    Stop voice recognition for a session.
    Flushes the configured backend and emits any final text.
    """
    session_id = data.get('session_id')
    logger.info("voice_stop requested by client %s for session %s",
                request.sid, session_id)  # type: ignore[arg-type]
    if not session_id:
        return

    with _active_voice_sessions_lock:
        engine = _active_voice_sessions.pop(session_id, runtime_config.voice_engine)

    if engine == 'whisper':
        _stop_whisper_voice_session(session_id)
    else:
        _stop_vosk_voice_session(session_id)

    emit('voice_status', {'session_id': session_id, 'status': 'stopped'})
    logger.info("Voice stopped for session %s", session_id)


# ==================== Main Entry Point ====================

def run_server(host: str = "127.0.0.1", port: int = 5050, debug: bool = False):
    """Run the Flask-SocketIO server."""
    logger.info(f"Starting GridVibe server on {host}:{port}")
    socketio.run(app, host=host, port=port, debug=debug)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_server(debug=True)
