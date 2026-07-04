"""
Web API for GridVibe frontend integration.
Provides REST endpoints and WebSocket support for terminal sessions.
"""

import atexit
import json
import logging
import os
import posixpath
import re
import select
import shlex
import shutil
import socket
import stat as stat_module
import struct
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from cryptography.fernet import Fernet
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room

from gridvibe_version import __version__
from sessions.manager import SessionManager, SessionStatus

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
    import bleach
    import markdown
except ImportError:  # pragma: no cover - dependency declared for runtime installs
    bleach = None
    markdown = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional for faster-whisper input handling
    np = None

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - optional for faster-whisper voice input
    WhisperModel = None

logger = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "default_config.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SAVED_SESSIONS_PATH = os.path.join(BASE_DIR, "saved_sessions.json")
AGENT_REGISTRY_PATH = os.path.join(BASE_DIR, "agent_registry.json")
DEFAULT_SAVED_SESSION_ID = "default-session"
EXPLORER_FILE_PREVIEW_MAX_BYTES = 1024 * 1024
EXPLORER_GIT_DIFF_MAX_BYTES = 256 * 1024
EXPLORER_GIT_DIFF_MAX_LINES = 4000
DEFAULT_SAVED_SESSION_NAME = "Default Session"
WINDOWS_DEVICE_ATTRIBUTES_RESPONSE = "\x1b[?1;2c"
SELF_UPDATE_REPO_DIR = BASE_DIR


class AppUpdateError(RuntimeError):
    """Raised when the application update flow cannot complete safely."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

ENCRYPTION_KEY_PATH = os.path.join(BASE_DIR, ".encryption_key")

def _get_encryption_key() -> bytes:
    """Load or generate encryption key for password storage."""
    if os.path.exists(ENCRYPTION_KEY_PATH):
        with open(ENCRYPTION_KEY_PATH, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(ENCRYPTION_KEY_PATH, "wb") as f:
        f.write(key)
    os.chmod(ENCRYPTION_KEY_PATH, 0o600)
    return key

_cipher = Fernet(_get_encryption_key())

def _encrypt_password(password: str) -> str:
    """Encrypt password for storage."""
    if not password:
        return ""
    return _cipher.encrypt(password.encode()).decode()

def _decrypt_password(encrypted: str) -> str:
    """Decrypt stored password."""
    if not encrypted:
        return ""
    try:
        return _cipher.decrypt(encrypted.encode()).decode()
    except Exception:
        return encrypted


# ==================== Configuration ====================

def _load_json_file(path: str) -> Dict[str, Any]:
    """Load one JSON object from disk."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge dictionaries while replacing scalar values and lists."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from file, falling back to default_config.json."""
    target_path = config_path or CONFIG_PATH

    default_config: Dict[str, Any] = {}
    if target_path != DEFAULT_CONFIG_PATH and os.path.exists(DEFAULT_CONFIG_PATH):
        default_config = _load_json_file(DEFAULT_CONFIG_PATH)

    if os.path.exists(target_path):
        loaded = _load_json_file(target_path)
        return _merge_dicts(default_config, loaded) if default_config else loaded

    return default_config


def save_config(config: Dict[str, Any], config_path: Optional[str] = None):
    """Save configuration to file."""
    target_path = config_path or CONFIG_PATH
    with open(target_path, 'w', encoding="utf-8") as f:
        json.dump(config, f, indent=2)


app_config: Dict[str, Any] = {}
ssh_config: Dict[str, Any] = {}
max_sessions = 4
app_theme = "system"
app_surface_mode = "normal"
voice_enabled = True
voice_engine = "vosk"
vosk_service_url = "ws://localhost:2700"
vosk_model = "vosk-model-en-us-0.22"
whisper_model = "base"
whisper_device = "cpu"
whisper_compute_type = "int8"
voice_language = "en-US"
vosk_startup_timeout_seconds = 180
WHISPER_MODEL_OPTIONS = {
    "tiny.en",
    "tiny",
    "base.en",
    "base",
    "small.en",
    "small",
    "medium.en",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large",
    "distil-large-v2",
    "distil-medium.en",
    "distil-small.en",
    "distil-large-v3",
    "distil-large-v3.5",
    "large-v3-turbo",
    "turbo",
}


def _normalize_surface_mode(value: Any, default: str = "normal") -> str:
    """Normalize workspace chrome density for terminal session windows."""
    normalized = str(value or "").strip().lower()
    if normalized in {"normal", "max"}:
        return normalized
    return default if default in {"normal", "max"} else "normal"


def _refresh_runtime_config():
    """Reload runtime config-backed globals from disk."""
    global app_config
    global ssh_config
    global max_sessions
    global app_theme
    global app_surface_mode
    global voice_enabled
    global voice_engine
    global vosk_service_url
    global vosk_model
    global whisper_model
    global whisper_device
    global whisper_compute_type
    global voice_language
    global vosk_startup_timeout_seconds

    app_config = load_config()
    ssh_config = app_config.get("ssh", {})
    max_sessions = app_config.get("terminal", {}).get("max_sessions", 4)
    appearance_config = app_config.get("appearance", {})
    app_theme = str(appearance_config.get("theme", "system")).strip().lower()
    if app_theme not in {"system", "light", "dark"}:
        app_theme = "system"

    workspace_config = app_config.get("workspace", {})
    app_surface_mode = _normalize_surface_mode(workspace_config.get("surface_mode"))

    voice_config = app_config.get("voice_input", {})
    voice_enabled = voice_config.get("enabled", True)
    voice_engine = str(voice_config.get("engine", "vosk")).strip().lower()
    if voice_engine not in {"vosk", "whisper"}:
        voice_engine = "vosk"
    vosk_service_url = voice_config.get("vosk_service_url", "ws://localhost:2700")
    vosk_model = voice_config.get("vosk_model", "vosk-model-en-us-0.22")
    whisper_model = str(voice_config.get("whisper_model", "base")).strip() or "base"
    if whisper_model not in WHISPER_MODEL_OPTIONS:
        whisper_model = "base"
    whisper_device = voice_config.get("whisper_device", "cpu")
    whisper_compute_type = voice_config.get("whisper_compute_type", "int8")
    voice_language = voice_config.get("language", "en-US")
    try:
        vosk_startup_timeout_seconds = max(
            30,
            int(voice_config.get("vosk_startup_timeout_seconds", 180)),
        )
    except (ValueError, TypeError):
        vosk_startup_timeout_seconds = 180


_refresh_runtime_config()


def _active_voice_model_name() -> str:
    """Return the currently configured STT model name."""
    return whisper_model if voice_engine == "whisper" else vosk_model


def _public_app_config() -> Dict[str, Any]:
    """Return the subset of app config that the launcher can edit safely."""
    return {
        "appearance": {
            "theme": app_theme,
        },
        "workspace": {
            "surface_mode": app_surface_mode,
        },
        "voice_input": {
            "enabled": voice_enabled,
            "engine": voice_engine,
            "vosk_model": vosk_model,
            "whisper_model": whisper_model,
            "whisper_device": whisper_device,
            "whisper_compute_type": whisper_compute_type,
            "language": voice_language,
        }
    }


def _normalize_app_config_update(data: Any) -> Dict[str, Any]:
    """Validate and normalize launcher-editable app settings."""
    payload = data if isinstance(data, dict) else {}
    appearance = payload.get("appearance")
    if not isinstance(appearance, dict):
        appearance = {}
    theme = str(appearance.get("theme", app_theme)).strip().lower()
    if theme not in {"system", "light", "dark"}:
        theme = app_theme

    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
    surface_mode = _normalize_surface_mode(workspace.get("surface_mode"), app_surface_mode)

    voice_input = payload.get("voice_input")
    if not isinstance(voice_input, dict):
        voice_input = {}

    engine = str(voice_input.get("engine", voice_engine)).strip().lower()
    if engine not in {"vosk", "whisper"}:
        engine = voice_engine

    whisper_device_value = str(
        voice_input.get("whisper_device", whisper_device)
    ).strip().lower()
    if whisper_device_value not in {"cpu", "cuda"}:
        whisper_device_value = whisper_device

    next_whisper_model = str(
        voice_input.get("whisper_model", whisper_model)
    ).strip() or whisper_model
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
            "enabled": bool(voice_input.get("enabled", voice_enabled)),
            "engine": engine,
            "vosk_model": str(voice_input.get("vosk_model", vosk_model)).strip() or vosk_model,
            "whisper_model": next_whisper_model,
            "whisper_device": whisper_device_value,
            "whisper_compute_type": str(
                voice_input.get("whisper_compute_type", whisper_compute_type)
            ).strip() or whisper_compute_type,
            "language": str(voice_input.get("language", voice_language)).strip() or voice_language,
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
            "distribution": "",
            "use_wsl": False,
            "use_powershell": False,
        }
        for index in range(max_sessions)
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
    if normalized == "explorer" and connection_mode in {"ssh", "wsl"}:
        return "explorer"
    return "terminal"


def _normalize_workspace_layout(data: Any, terminal_count: int) -> Optional[Dict[str, Any]]:
    """Normalize optional runtime workspace geometry stored with a saved preset."""
    if not isinstance(data, dict):
        return None

    raw_rects = data.get("split_slot_rects")
    if not isinstance(raw_rects, list) or len(raw_rects) != terminal_count:
        return None

    rects = []
    max_grid_line = max(2, max_sessions * 4)
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
                "originSlot": max(0, min(max_sessions - 1, origin_slot)),
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
        "original_split_slot_count": max(1, min(max_sessions, original_count)),
    }


def _default_session_config() -> Dict[str, Any]:
    """Default saved setup used by the launcher form."""
    default_count = min(4, max_sessions)
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

    for index in range(max_sessions):
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
                "initial_command_mode": startup_mode if startup_mode in {"agent", "explorer"} else "command",
                "startup_mode": startup_mode,
                "agent_selection": str(entry.get("agent_selection") or ""),
                "custom_agent": str(entry.get("custom_agent") or ""),
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

    terminal_count = max(1, min(max_sessions, terminal_count))
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
    "layout": _normalize_layout("grid", min(4, max_sessions)),
    "terminal_count": min(4, max_sessions),
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
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=target.get("password") or None,
            timeout=min(int(ssh_config.get("connection_timeout", 30)), 5),
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

        detection = _detect_agent_binary(target, binary)
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

    configured_secret = app_config.get("security", {}).get("secret_key")
    if isinstance(configured_secret, str) and configured_secret.strip():
        return configured_secret

    return os.urandom(32)


def _resolve_cors_origins():
    """Return configured Socket.IO CORS origins for the local app."""
    origins = app_config.get("security", {}).get("cors_origins", ["*"])
    return origins if origins else ["*"]
# Create Flask app
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["SECRET_KEY"] = _resolve_secret_key()
app.config['JSON_SORT_KEYS'] = False

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins=_resolve_cors_origins(), async_mode="threading")

# Initialize session manager
session_manager = SessionManager()

# Store active SSH connections and buffered output
ssh_connections: Dict[str, Dict[str, Any]] = {}
session_output_buffers: Dict[str, str] = {}
client_joined_sessions: Dict[str, set[str]] = {}
_MAX_TRACKED_SOCKET_CLIENTS = 1000
connection_lock = threading.RLock()
folder_dialog_lock = threading.Lock()


# ==================== App Update Helpers ====================

def _run_git_command(args: List[str]) -> subprocess.CompletedProcess[str]:
    """Run one git command inside the project checkout."""
    git_path = shutil.which("git")
    if not git_path:
        raise AppUpdateError("Git is not installed or is not available on PATH.", 400)

    try:
        return subprocess.run(
            [git_path, "-C", SELF_UPDATE_REPO_DIR, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise AppUpdateError(f"Failed to run git {' '.join(args)}: {exc}", 500) from exc


def _git_error_message(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Return the most useful stderr/stdout text from a git command."""
    return (result.stderr or result.stdout or fallback).strip()


def perform_self_update() -> Dict[str, Any]:
    """Fetch and fast-forward the current git checkout when safe."""
    repo_result = _run_git_command(["rev-parse", "--is-inside-work-tree"])
    if repo_result.returncode != 0 or repo_result.stdout.strip().lower() != "true":
        raise AppUpdateError(
            "This installation is not running from a git checkout.",
            400,
        )

    branch_result = _run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(branch_result, "Could not determine the current branch."),
            500,
        )

    branch = branch_result.stdout.strip()
    if not branch or branch == "HEAD":
        raise AppUpdateError(
            "This checkout is in detached HEAD mode and cannot self-update safely.",
            409,
        )

    status_result = _run_git_command(["status", "--porcelain"])
    if status_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(status_result, "Could not inspect the git worktree."),
            500,
        )

    if status_result.stdout.strip():
        raise AppUpdateError(
            "Local changes are present. Commit, stash, or discard them before checking for updates.",
            409,
        )

    upstream_result = _run_git_command(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    if upstream_result.returncode != 0:
        raise AppUpdateError(
            f"Branch '{branch}' does not track a remote branch, so automatic updates are unavailable.",
            400,
        )

    upstream = upstream_result.stdout.strip()

    fetch_result = _run_git_command(["fetch", "--all", "--prune"])
    if fetch_result.returncode != 0:
        raise AppUpdateError(
            f"Git fetch failed: {_git_error_message(fetch_result, 'Unable to contact the remote repository.')}",
            500,
        )

    count_result = _run_git_command(["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    if count_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(count_result, "Could not compare the local branch with its upstream."),
            500,
        )

    counts = count_result.stdout.strip().split()
    if len(counts) != 2:
        raise AppUpdateError("Git returned an invalid branch comparison result.", 500)

    try:
        ahead_count = int(counts[0])
        behind_count = int(counts[1])
    except ValueError as exc:
        raise AppUpdateError("Git returned a non-numeric branch comparison result.", 500) from exc

    if behind_count == 0 and ahead_count == 0:
        return {
            "updated": False,
            "restart_required": False,
            "branch": branch,
            "upstream": upstream,
            "ahead_count": 0,
            "behind_count": 0,
            "message": f"GridVibe is already up to date on '{branch}'.",
        }

    if behind_count == 0 and ahead_count > 0:
        return {
            "updated": False,
            "restart_required": False,
            "branch": branch,
            "upstream": upstream,
            "ahead_count": ahead_count,
            "behind_count": 0,
            "message": f"Branch '{branch}' is already ahead of {upstream}; no update was applied.",
        }

    if ahead_count > 0 and behind_count > 0:
        raise AppUpdateError(
            f"Branch '{branch}' has diverged from {upstream}. Resolve the branch state manually before updating.",
            409,
        )

    previous_commit_result = _run_git_command(["rev-parse", "HEAD"])
    if previous_commit_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(previous_commit_result, "Could not determine the current commit."),
            500,
        )

    previous_commit = previous_commit_result.stdout.strip()

    pull_result = _run_git_command(["pull", "--ff-only"])
    if pull_result.returncode != 0:
        raise AppUpdateError(
            f"Git pull failed: {_git_error_message(pull_result, 'The remote update could not be applied.')}",
            500,
        )

    current_commit_result = _run_git_command(["rev-parse", "HEAD"])
    if current_commit_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(current_commit_result, "Could not determine the updated commit."),
            500,
        )

    current_commit = current_commit_result.stdout.strip()
    return {
        "updated": previous_commit != current_commit,
        "restart_required": previous_commit != current_commit,
        "branch": branch,
        "upstream": upstream,
        "ahead_count": 0,
        "behind_count": behind_count,
        "previous_commit": previous_commit,
        "current_commit": current_commit,
        "message": (
            f"Updated '{branch}' from {previous_commit[:7]} to {current_commit[:7]}."
            if previous_commit != current_commit
            else f"GridVibe is already up to date on '{branch}'."
        ),
    }


# ==================== SSH Helpers ====================

def _broadcast_session_status(session_id: str):
    """Emit the latest session status to connected clients."""
    with session_manager.lock:
        session = session_manager.sessions.get(session_id)
        if session:
            socketio.emit('session_status', session.to_dict())


def _resolve_group_id() -> str:
    """Return the requested session group id, if any."""
    return str(request.args.get("group") or "").strip()


def _get_group_response_meta(group_id: str) -> Dict[str, Any]:
    """Return layout metadata for one session group."""
    group = session_manager.get_group(group_id)
    if not group:
        return {
            "group": None,
            "layout": active_launch_options["layout"],
            "connection_mode": active_launch_options["connection_mode"],
            "terminal_count": 0,
            "workspace_layout": None,
            "surface_mode": app_surface_mode,
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
    with connection_lock:
        existing = session_output_buffers.get(session_id, "")
        session_output_buffers[session_id] = (existing + output)[-50000:]


def _clear_client_joined_sessions(client_id: str):
    """Forget which session buffers have already been replayed to one client."""
    with connection_lock:
        client_joined_sessions.pop(client_id, None)


def _clear_terminal_output_buffer(session_id: str):
    """Drop the buffered replay output for one terminal session."""
    with connection_lock:
        session_output_buffers[session_id] = ""


def _close_ssh_connection(session_id: str, clear_buffer: bool = True):
    """Close and remove a single SSH connection."""
    with connection_lock:
        connection = ssh_connections.pop(session_id, None)
        if clear_buffer:
            session_output_buffers.pop(session_id, None)

    _shutdown_connection(connection)


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
        with connection_lock:
            buffer = session_output_buffers.get(session_id, "")
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
            with connection_lock:
                _cache_terminal_output(session_id, output)
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
                f"Set-Location -LiteralPath {shlex.quote(target_directory)}{newline}",
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


def _stream_ssh_output(session_id: str):
    """Read terminal output from the SSH channel and forward it to clients."""
    try:
        while True:
            with connection_lock:
                connection = ssh_connections.get(session_id)

            if not connection:
                break

            channel = connection["channel"]

            if channel.recv_ready():
                output = channel.recv(4096).decode("utf-8", errors="ignore")
                if output:
                    with connection_lock:
                        _cache_terminal_output(session_id, output)
                        socketio.emit(
                            'terminal_output',
                            {'session_id': session_id, 'data': output},
                            room=session_id # type: ignore
                        )
                continue

            if channel.closed or channel.exit_status_ready():
                break

            time.sleep(0.05)
    except Exception as e:
        session = session_manager.get_session(session_id)
        if session and _is_explorer_session(session):
            logger.debug("Ignoring stream shutdown for explorer session %s: %s", session_id, e)
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
                    with connection_lock:
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
                            with connection_lock:
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

            output = stdout_handle.read(1)
            if output:
                chunk = output.decode("utf-8", errors="ignore")
                with connection_lock:
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


def _is_explorer_session(session: Any) -> bool:
    """Return whether a session should render as a file explorer pane."""
    return (
        getattr(session, "mode", "") in {"ssh", "wsl"}
        and getattr(session, "startup_mode", "") == "explorer"
    )


def _is_remote_explorer_session(session: Any) -> bool:
    """Return whether explorer requests should browse over SSH/SFTP."""
    return getattr(session, "mode", "") == "ssh" and _is_explorer_session(session)


def _explorer_root_directory(session: Any) -> str:
    """Return the stable explorer root, falling back to the session directory."""
    return str(
        getattr(session, "explorer_root_directory", "")
        or getattr(session, "directory", "")
        or ""
    ).strip()


def _default_explorer_candidate_path(session: Any, root_path: str) -> str:
    """Return the default explorer directory when no path is requested."""
    current_raw = str(getattr(session, "directory", "") or "").strip()
    if not current_raw:
        return root_path

    candidate = os.path.realpath(os.path.abspath(os.path.expanduser(current_raw)))
    if not os.path.isdir(candidate):
        return root_path

    try:
        common_path = os.path.commonpath([root_path, candidate])
    except ValueError:
        return root_path

    if os.path.normcase(common_path) != os.path.normcase(root_path):
        return root_path

    return candidate


MARKDOWN_PREVIEW_EXTENSIONS = {".md", ".markdown"}
CODE_PREVIEW_LANGUAGES = {
    ".bash": "shell",
    ".bat": "batch",
    ".c": "c",
    ".cc": "cpp",
    ".cfg": "config",
    ".cmd": "batch",
    ".conf": "config",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".env": "dotenv",
    ".example": "config",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".ini": "ini",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".jsonl": "jsonl",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".log": "log",
    ".lua": "lua",
    ".php": "php",
    ".ps1": "powershell",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".spec": "python",
    ".swift": "swift",
    ".txt": "text",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
CODE_PREVIEW_FILENAMES = {
    ".editorconfig": "ini",
    ".env": "dotenv",
    ".gitattributes": "config",
    ".gitignore": "gitignore",
    ".gitkeep": "text",
    ".python-version": "text",
    "dockerfile": "dockerfile",
    "makefile": "makefile",
}
EXPLORER_BINARY_SAMPLE_BYTES = 4096
EXPLORER_TEXT_CONTROL_BYTES = {7, 8, 9, 10, 12, 13, 27}
MARKDOWN_ALLOWED_TAGS = {
    "a",
    "abbr",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "input",
    "ins",
    "kbd",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
MARKDOWN_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "abbr": ["title"],
    "img": ["alt", "src", "title"],
    "input": ["checked", "disabled", "type"],
    "td": ["align"],
    "th": ["align"],
}


def _is_markdown_file(path: str) -> bool:
    """Return whether an explorer file should get a Markdown preview."""
    _, extension = os.path.splitext(path.lower())
    return extension in MARKDOWN_PREVIEW_EXTENSIONS


def _explorer_code_language(path: str) -> Optional[str]:
    """Return the source language for code files shown in explorer previews."""
    filename = os.path.basename(path).lower()
    if filename in CODE_PREVIEW_FILENAMES:
        return CODE_PREVIEW_FILENAMES[filename]
    if filename.startswith(".env."):
        return "dotenv"
    _, extension = os.path.splitext(path.lower())
    if extension in MARKDOWN_PREVIEW_EXTENSIONS:
        return "markdown"
    return CODE_PREVIEW_LANGUAGES.get(extension)


def _explorer_editor_language(path: str) -> str:
    """Return the editor language or reject unsupported explorer formats."""
    language = _explorer_code_language(path)
    if language is None:
        raise ValueError("Explorer file format is not supported for editor preview")
    return language


def _explorer_content_looks_binary(raw_content: bytes) -> bool:
    """Return whether a preview sample should be treated as binary content."""
    sample = raw_content[:EXPLORER_BINARY_SAMPLE_BYTES]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    control_count = sum(
        1
        for byte in sample
        if byte < 32 and byte not in EXPLORER_TEXT_CONTROL_BYTES
    )
    return control_count / len(sample) > 0.30


def _render_markdown_preview(content: str) -> Optional[str]:
    """Render Markdown to sanitized HTML for explorer previews."""
    if markdown is None or bleach is None:
        return None

    html = markdown.markdown(
        content,
        extensions=["extra", "sane_lists", "tables"],
        output_format="html",
    )
    return bleach.clean(
        html,
        tags=MARKDOWN_ALLOWED_TAGS,
        attributes=MARKDOWN_ALLOWED_ATTRIBUTES,
        protocols=bleach.sanitizer.ALLOWED_PROTOCOLS,
        strip=True,
    )


def _resolve_explorer_candidate_path(
    session: Any,
    requested_path: Any = "",
    *,
    allow_empty_root: bool = True,
) -> Tuple[str, str]:
    """Resolve an explorer request path while keeping it inside the session root."""
    if not _is_explorer_session(session):
        raise ValueError("Session is not a file explorer pane")

    root_raw = _explorer_root_directory(session)
    if not root_raw:
        raise ValueError("Explorer root directory is not configured")

    root_path = os.path.realpath(os.path.abspath(os.path.expanduser(root_raw)))
    if not os.path.isdir(root_path):
        raise ValueError("Explorer root directory does not exist")

    if requested_path is None:
        if not allow_empty_root:
            raise ValueError("Explorer file path is required")
        candidate = _default_explorer_candidate_path(session, root_path)
    else:
        raw_path = str(requested_path or "").strip()
        if not raw_path:
            if not allow_empty_root:
                raise ValueError("Explorer file path is required")
            candidate = root_path
        elif os.path.isabs(raw_path):
            candidate = os.path.realpath(os.path.abspath(os.path.expanduser(raw_path)))
        else:
            candidate = os.path.realpath(os.path.abspath(os.path.join(root_path, raw_path)))

    try:
        common_path = os.path.commonpath([root_path, candidate])
    except ValueError as exc:
        raise ValueError("Explorer path must stay inside the configured root") from exc

    if os.path.normcase(common_path) != os.path.normcase(root_path):
        raise ValueError("Explorer path must stay inside the configured root")

    return root_path, candidate


def _resolve_explorer_paths(session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve an explorer directory path while keeping it inside the session root."""
    root_path, candidate = _resolve_explorer_candidate_path(session, requested_path)
    if not os.path.isdir(candidate):
        raise ValueError("Explorer path is not a directory")

    return root_path, candidate


def _resolve_explorer_file_path(session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve an explorer file path while keeping it inside the session root."""
    root_path, candidate = _resolve_explorer_candidate_path(
        session,
        requested_path,
        allow_empty_root=False,
    )
    if os.path.isdir(candidate):
        raise ValueError("Explorer path is a directory")
    if not os.path.isfile(candidate):
        raise ValueError("Explorer path is not a file")

    return root_path, candidate


def _relative_explorer_path(root_path: str, path: str) -> str:
    """Return a stable slash-separated explorer path relative to the root."""
    relative = os.path.relpath(path, root_path)
    return "" if relative == "." else relative.replace(os.sep, "/")


def _explorer_entry_payload(root_path: str, entry: os.DirEntry) -> Dict[str, Any]:
    """Return metadata for one explorer entry."""
    try:
        stat_result = entry.stat(follow_symlinks=False)
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError:
        stat_result = None
        is_dir = False

    return {
        "name": entry.name,
        "path": _relative_explorer_path(root_path, entry.path),
        "type": "directory" if is_dir else "file",
        "size": None if is_dir or stat_result is None else stat_result.st_size,
        "modified": None if stat_result is None else stat_result.st_mtime,
    }


def _empty_explorer_git_context(error: Optional[str] = None) -> Dict[str, Any]:
    """Return a non-fatal empty Git metadata payload for explorer responses."""
    return {
        "available": False,
        "repo_root": None,
        "branch": None,
        "head": None,
        "ahead": None,
        "behind": None,
        "dirty": False,
        "error": error,
    }


def _clean_git_path(path: str) -> str:
    """Normalize Git path output to slash-separated relative paths."""
    return str(path or "").strip().replace("\\", "/").strip("/")


def _run_git_command(
    args: List[str],
    *,
    cwd: str,
    timeout: float = 2.0,
) -> subprocess.CompletedProcess:
    """Run a read-only Git command with predictable process settings."""
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _decode_git_output(raw_output: bytes) -> str:
    """Decode Git command output without raising on repository filename bytes."""
    return raw_output.decode("utf-8", errors="replace").strip()


def _parse_git_ahead_behind(value: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse a porcelain v2 branch.ab header value."""
    ahead = None
    behind = None
    for part in value.split():
        if part.startswith("+"):
            try:
                ahead = int(part[1:])
            except ValueError:
                ahead = None
        elif part.startswith("-"):
            try:
                behind = int(part[1:])
            except ValueError:
                behind = None
    return ahead, behind


def _git_status_name(index_status: str, worktree_status: str, record_type: str) -> str:
    """Map porcelain status codes to the compact explorer status set."""
    if record_type == "?":
        return "untracked"
    if record_type == "!":
        return "ignored"
    status_code = f"{index_status}{worktree_status}"
    if record_type == "u" or "U" in status_code:
        return "conflicted"
    if "R" in status_code:
        return "renamed"
    if "A" in status_code:
        return "added"
    if "D" in status_code:
        return "deleted"
    if any(code in status_code for code in ("M", "T")):
        return "modified"
    return "clean"


def _clean_git_entry_status() -> Dict[str, Any]:
    """Return the default Git status metadata for an explorer entry."""
    return {
        "status": "clean",
        "index_status": " ",
        "worktree_status": " ",
        "has_descendant_changes": False,
        "descendant_status": None,
    }


def _git_status_payload(
    path: str,
    index_status: str,
    worktree_status: str,
    record_type: str,
    original_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build per-path Git status metadata from one porcelain record."""
    status = _git_status_name(index_status, worktree_status, record_type)
    payload: Dict[str, Any] = {
        "path": path,
        "status": status,
        "index_status": index_status or " ",
        "worktree_status": worktree_status or " ",
        "has_descendant_changes": status != "clean",
    }
    if original_path:
        payload["original_path"] = original_path
    return payload


def _parse_git_status_porcelain_v2(raw_output: bytes) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Parse NUL-delimited `git status --porcelain=v2 -z --branch` output."""
    branch: Dict[str, Any] = {
        "branch": None,
        "head": None,
        "ahead": None,
        "behind": None,
    }
    statuses: Dict[str, Dict[str, Any]] = {}
    records = raw_output.decode("utf-8", errors="replace").split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# "):
            header = record[2:]
            key, _, value = header.partition(" ")
            if key == "branch.head" and value != "(detached)":
                branch["branch"] = value or None
            elif key == "branch.oid":
                branch["head"] = None if value == "(initial)" else value[:12]
            elif key == "branch.ab":
                branch["ahead"], branch["behind"] = _parse_git_ahead_behind(value)
            continue

        record_type = record[0]
        if record_type in ("?", "!"):
            path = _clean_git_path(record[2:])
            if path:
                statuses[path] = _git_status_payload(path, "?", "?", record_type)
            continue
        if record_type == "1":
            parts = record.split(" ", 8)
            if len(parts) >= 9:
                index_status, worktree_status = parts[1][0], parts[1][1]
                path = _clean_git_path(parts[8])
                if path:
                    statuses[path] = _git_status_payload(path, index_status, worktree_status, record_type)
            continue
        if record_type == "2":
            parts = record.split(" ", 9)
            original_path = records[index] if index < len(records) else ""
            if index < len(records):
                index += 1
            if len(parts) >= 10:
                index_status, worktree_status = parts[1][0], parts[1][1]
                path = _clean_git_path(parts[9])
                if path:
                    statuses[path] = _git_status_payload(
                        path,
                        index_status,
                        worktree_status,
                        record_type,
                        _clean_git_path(original_path),
                    )
            continue
        if record_type == "u":
            parts = record.split(" ", 11)
            if len(parts) >= 12:
                index_status, worktree_status = parts[1][0], parts[1][1]
                path = _clean_git_path(parts[11])
                if path:
                    statuses[path] = _git_status_payload(path, index_status, worktree_status, record_type)

    return branch, statuses


def _repo_relative_git_path(repo_root: str, path: str) -> str:
    """Return a slash-separated path relative to a Git repository root."""
    relative = os.path.relpath(path, repo_root)
    return "" if relative == "." else relative.replace(os.sep, "/")


def _repo_relative_remote_git_path(repo_root: str, path: str) -> str:
    """Return a slash-separated remote path relative to a Git repository root."""
    repo_clean = _remote_path_clean(repo_root).rstrip("/") or "/"
    path_clean = _remote_path_clean(path).rstrip("/") or "/"
    relative = posixpath.relpath(path_clean, repo_clean)
    return "" if relative == "." else relative.replace("\\", "/")


def _git_pathspec(repo_root: str, scope_path: str) -> str:
    """Return a Git pathspec for a validated absolute path."""
    repo_relative = _repo_relative_git_path(repo_root, scope_path)
    return repo_relative or "."


def _remote_git_pathspec(repo_root: str, scope_path: str) -> str:
    """Return a Git pathspec for a validated remote path."""
    repo_relative = _repo_relative_remote_git_path(repo_root, scope_path)
    return repo_relative or "."


def _git_status_for_entry(
    repo_root: str,
    entry_path: str,
    statuses: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Return exact or descendant Git status metadata for one explorer entry."""
    repo_relative = _clean_git_path(_repo_relative_git_path(repo_root, entry_path))
    status = dict(statuses.get(repo_relative, _clean_git_entry_status()))
    descendant_prefix = f"{repo_relative}/" if repo_relative else ""
    if descendant_prefix:
        descendant_status = _aggregate_git_status(
            item.get("status", "clean")
            for path, item in statuses.items()
            if path.startswith(descendant_prefix)
        )
        if descendant_status:
            status["has_descendant_changes"] = True
            status["descendant_status"] = descendant_status
            if status.get("status") == "clean":
                status["status"] = descendant_status
    return status


def _remote_git_status_for_entry(
    repo_root: str,
    entry_path: str,
    statuses: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Return exact or descendant Git status metadata for one remote explorer entry."""
    repo_relative = _clean_git_path(_repo_relative_remote_git_path(repo_root, entry_path))
    status = dict(statuses.get(repo_relative, _clean_git_entry_status()))
    descendant_prefix = f"{repo_relative}/" if repo_relative else ""
    if descendant_prefix:
        descendant_status = _aggregate_git_status(
            item.get("status", "clean")
            for path, item in statuses.items()
            if path.startswith(descendant_prefix)
        )
        if descendant_status:
            status["has_descendant_changes"] = True
            status["descendant_status"] = descendant_status
            if status.get("status") == "clean":
                status["status"] = descendant_status
    return status


def _aggregate_git_status(statuses: Any) -> Optional[str]:
    """Return the most useful status badge for a collection of descendant changes."""
    priority = (
        "conflicted",
        "modified",
        "added",
        "deleted",
        "renamed",
        "untracked",
        "ignored",
    )
    status_set = {status for status in statuses if status and status != "clean"}
    for status in priority:
        if status in status_set:
            return status
    return None


def _get_git_context(root_path: str, current_path: str) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Return repository metadata and path statuses for a local explorer directory."""
    try:
        rev_parse = _run_git_command(
            ["rev-parse", "--show-toplevel", "--is-inside-work-tree"],
            cwd=current_path,
            timeout=2.0,
        )
    except FileNotFoundError:
        return _empty_explorer_git_context("Git executable was not found"), {}
    except subprocess.TimeoutExpired:
        return _empty_explorer_git_context("Git repository detection timed out"), {}
    except OSError as exc:
        return _empty_explorer_git_context(str(exc)), {}

    if rev_parse.returncode != 0:
        return _empty_explorer_git_context(), {}

    rev_lines = _decode_git_output(rev_parse.stdout).splitlines()
    if len(rev_lines) < 2 or rev_lines[1].lower() != "true":
        return _empty_explorer_git_context(), {}

    repo_root = os.path.realpath(os.path.abspath(rev_lines[0]))
    try:
        os.path.commonpath([repo_root, root_path])
        os.path.commonpath([repo_root, current_path])
    except ValueError:
        return _empty_explorer_git_context("Git repository path could not be compared"), {}

    status_args = [
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--",
        _git_pathspec(repo_root, current_path),
    ]
    try:
        status_result = _run_git_command(status_args, cwd=repo_root, timeout=2.0)
    except subprocess.TimeoutExpired:
        context = _empty_explorer_git_context("Git status timed out")
        context["repo_root"] = repo_root
        return context, {}
    except OSError as exc:
        context = _empty_explorer_git_context(str(exc))
        context["repo_root"] = repo_root
        return context, {}

    if status_result.returncode != 0:
        error = _decode_git_output(status_result.stderr) or "Git status failed"
        context = _empty_explorer_git_context(error)
        context["repo_root"] = repo_root
        return context, {}

    branch, statuses = _parse_git_status_porcelain_v2(status_result.stdout)
    context = {
        "available": True,
        "repo_root": repo_root,
        "branch": branch.get("branch"),
        "head": branch.get("head"),
        "ahead": branch.get("ahead"),
        "behind": branch.get("behind"),
        "dirty": any(item.get("status") != "clean" for item in statuses.values()),
        "error": None,
    }
    return context, statuses


def _remote_git_shell_command(args: List[str], cwd: str) -> str:
    """Build a read-only Git command for a remote POSIX-compatible SSH shell."""
    quoted_args = " ".join(shlex.quote(part) for part in args)
    return f"GIT_OPTIONAL_LOCKS=0 git -C {shlex.quote(cwd)} {quoted_args}"


def _run_remote_git_command(
    client: Any,
    args: List[str],
    *,
    cwd: str,
    timeout: float = 2.0,
) -> Any:
    """Run a read-only Git command over SSH and return a subprocess-like result."""
    command = _remote_git_shell_command(args, cwd)
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdout_data = stdout.read()
    stderr_data = stderr.read()
    if isinstance(stdout_data, str):
        stdout_data = stdout_data.encode("utf-8", errors="replace")
    if isinstance(stderr_data, str):
        stderr_data = stderr_data.encode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        args=command,
        returncode=stdout.channel.recv_exit_status(),
        stdout=stdout_data,
        stderr=stderr_data,
    )


def _get_remote_git_context(
    client: Any,
    root_path: str,
    current_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Return repository metadata and path statuses for a remote explorer directory."""
    try:
        rev_parse = _run_remote_git_command(
            client,
            ["rev-parse", "--show-toplevel", "--is-inside-work-tree"],
            cwd=current_path,
            timeout=2.0,
        )
    except TimeoutError:
        return _empty_explorer_git_context("Git repository detection timed out"), {}
    except Exception as exc:
        return _empty_explorer_git_context(str(exc)), {}

    if rev_parse.returncode != 0:
        return _empty_explorer_git_context(), {}

    rev_lines = _decode_git_output(rev_parse.stdout).splitlines()
    if len(rev_lines) < 2 or rev_lines[1].lower() != "true":
        return _empty_explorer_git_context(), {}

    repo_root = _remote_path_clean(rev_lines[0]).rstrip("/") or "/"
    if not _remote_path_inside(repo_root, current_path):
        return _empty_explorer_git_context("Git repository path could not be compared"), {}

    status_args = [
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--",
        _remote_git_pathspec(repo_root, current_path),
    ]
    try:
        status_result = _run_remote_git_command(client, status_args, cwd=repo_root, timeout=2.0)
    except TimeoutError:
        context = _empty_explorer_git_context("Git status timed out")
        context["repo_root"] = repo_root
        return context, {}
    except Exception as exc:
        context = _empty_explorer_git_context(str(exc))
        context["repo_root"] = repo_root
        return context, {}

    if status_result.returncode != 0:
        error = _decode_git_output(status_result.stderr) or "Git status failed"
        context = _empty_explorer_git_context(error)
        context["repo_root"] = repo_root
        return context, {}

    branch, statuses = _parse_git_status_porcelain_v2(status_result.stdout)
    context = {
        "available": True,
        "repo_root": repo_root,
        "branch": branch.get("branch"),
        "head": branch.get("head"),
        "ahead": branch.get("ahead"),
        "behind": branch.get("behind"),
        "dirty": any(item.get("status") != "clean" for item in statuses.values()),
        "error": None,
    }
    return context, statuses


def _attach_git_status_to_entries(
    root_path: str,
    git_context: Dict[str, Any],
    statuses: Dict[str, Dict[str, Any]],
    entries: List[Dict[str, Any]],
) -> None:
    """Attach per-entry Git metadata to local explorer entries."""
    if not git_context.get("available"):
        for entry in entries:
            entry["git"] = _clean_git_entry_status()
        return

    repo_root = str(git_context.get("repo_root") or "")
    for entry in entries:
        entry_path = os.path.realpath(os.path.abspath(os.path.join(root_path, entry.get("path") or "")))
        entry["git"] = _git_status_for_entry(repo_root, entry_path, statuses)


def _attach_remote_git_status_to_entries(
    root_path: str,
    git_context: Dict[str, Any],
    statuses: Dict[str, Dict[str, Any]],
    entries: List[Dict[str, Any]],
) -> None:
    """Attach per-entry Git metadata to remote explorer entries."""
    if not git_context.get("available"):
        for entry in entries:
            entry["git"] = _clean_git_entry_status()
        return

    repo_root = str(git_context.get("repo_root") or "")
    for entry in entries:
        entry_path = _remote_path_join(root_path, entry.get("path") or "")
        entry["git"] = _remote_git_status_for_entry(repo_root, entry_path, statuses)


def _append_deleted_git_entries(
    root_path: str,
    current_path: str,
    git_context: Dict[str, Any],
    statuses: Dict[str, Dict[str, Any]],
    entries: List[Dict[str, Any]],
) -> None:
    """Add read-only placeholder rows for deleted tracked files in the current directory."""
    if not git_context.get("available"):
        return

    repo_root = str(git_context.get("repo_root") or "")
    current_repo_relative = _clean_git_path(_repo_relative_git_path(repo_root, current_path))
    existing_paths = {entry.get("path") for entry in entries}
    for status_path, status in statuses.items():
        if status.get("status") != "deleted":
            continue
        if current_repo_relative:
            prefix = f"{current_repo_relative}/"
            if not status_path.startswith(prefix):
                continue
            current_relative = status_path[len(prefix):]
        else:
            current_relative = status_path
        if not current_relative or "/" in current_relative:
            continue

        deleted_abs_path = os.path.abspath(os.path.join(repo_root, status_path.replace("/", os.sep)))
        deleted_explorer_path = _relative_explorer_path(root_path, deleted_abs_path)
        if deleted_explorer_path in existing_paths:
            continue
        entries.append(
            {
                "name": current_relative,
                "path": deleted_explorer_path,
                "type": "file",
                "size": None,
                "modified": None,
                "git": dict(status),
                "deleted": True,
            }
        )
        existing_paths.add(deleted_explorer_path)


def _append_deleted_remote_git_entries(
    root_path: str,
    current_path: str,
    git_context: Dict[str, Any],
    statuses: Dict[str, Dict[str, Any]],
    entries: List[Dict[str, Any]],
) -> None:
    """Add read-only placeholder rows for deleted tracked files in the current remote directory."""
    if not git_context.get("available"):
        return

    repo_root = str(git_context.get("repo_root") or "")
    current_repo_relative = _clean_git_path(_repo_relative_remote_git_path(repo_root, current_path))
    existing_paths = {entry.get("path") for entry in entries}
    for status_path, status in statuses.items():
        if status.get("status") != "deleted":
            continue
        if current_repo_relative:
            prefix = f"{current_repo_relative}/"
            if not status_path.startswith(prefix):
                continue
            current_relative = status_path[len(prefix):]
        else:
            current_relative = status_path
        if not current_relative or "/" in current_relative:
            continue

        deleted_abs_path = _remote_path_join(repo_root, status_path)
        deleted_explorer_path = _relative_remote_explorer_path(root_path, deleted_abs_path)
        if deleted_explorer_path in existing_paths:
            continue
        entries.append(
            {
                "name": current_relative,
                "path": deleted_explorer_path,
                "type": "file",
                "size": None,
                "modified": None,
                "git": dict(status),
                "deleted": True,
            }
        )
        existing_paths.add(deleted_explorer_path)


def _bounded_git_diff(repo_root: str, args: List[str]) -> Tuple[str, bool, int]:
    """Run Git diff and return bounded UTF-8 text output."""
    result = _run_git_command(args, cwd=repo_root, timeout=3.0)
    if result.returncode != 0:
        error = _decode_git_output(result.stderr) or "Git diff failed"
        raise ValueError(error)
    if b"\x00" in result.stdout:
        raise ValueError("Git diff appears to contain binary data")

    truncated = len(result.stdout) > EXPLORER_GIT_DIFF_MAX_BYTES
    diff_bytes = result.stdout[:EXPLORER_GIT_DIFF_MAX_BYTES]
    diff_text = diff_bytes.decode("utf-8", errors="replace")
    lines = diff_text.splitlines(keepends=True)
    if len(lines) > EXPLORER_GIT_DIFF_MAX_LINES:
        diff_text = "".join(lines[:EXPLORER_GIT_DIFF_MAX_LINES])
        truncated = True
    return diff_text, truncated, len(result.stdout)


def _get_git_diff(root_path: str, file_path: str, mode: str) -> Dict[str, Any]:
    """Return a bounded read-only Git diff for a local explorer file."""
    if mode not in {"worktree", "staged", "head"}:
        raise ValueError("Invalid Git diff mode")

    git_context, _statuses = _get_git_context(root_path, os.path.dirname(file_path))
    if not git_context.get("available"):
        raise ValueError(git_context.get("error") or "Folder is not inside a Git worktree")

    repo_root = str(git_context["repo_root"])
    pathspec = _git_pathspec(repo_root, file_path)
    diff_args_by_mode = {
        "worktree": ["diff", "--no-ext-diff", "--no-color", "--", pathspec],
        "staged": ["diff", "--cached", "--no-ext-diff", "--no-color", "--", pathspec],
        "head": ["diff", "HEAD", "--no-ext-diff", "--no-color", "--", pathspec],
    }
    try:
        diff_text, truncated, raw_bytes = _bounded_git_diff(repo_root, diff_args_by_mode[mode])
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git diff timed out") from exc
    return {
        "git": git_context,
        "diff": diff_text,
        "truncated": truncated,
        "byte_count": raw_bytes,
        "line_count": len(diff_text.splitlines()),
    }


def _bounded_remote_git_diff(client: Any, repo_root: str, args: List[str]) -> Tuple[str, bool, int]:
    """Run remote Git diff and return bounded UTF-8 text output."""
    result = _run_remote_git_command(client, args, cwd=repo_root, timeout=3.0)
    if result.returncode != 0:
        error = _decode_git_output(result.stderr) or "Git diff failed"
        raise ValueError(error)
    if b"\x00" in result.stdout:
        raise ValueError("Git diff appears to contain binary data")

    truncated = len(result.stdout) > EXPLORER_GIT_DIFF_MAX_BYTES
    diff_bytes = result.stdout[:EXPLORER_GIT_DIFF_MAX_BYTES]
    diff_text = diff_bytes.decode("utf-8", errors="replace")
    lines = diff_text.splitlines(keepends=True)
    if len(lines) > EXPLORER_GIT_DIFF_MAX_LINES:
        diff_text = "".join(lines[:EXPLORER_GIT_DIFF_MAX_LINES])
        truncated = True
    return diff_text, truncated, len(result.stdout)


def _get_remote_git_diff(client: Any, root_path: str, file_path: str, mode: str) -> Dict[str, Any]:
    """Return a bounded read-only Git diff for a remote explorer file."""
    if mode not in {"worktree", "staged", "head"}:
        raise ValueError("Invalid Git diff mode")

    git_context, _statuses = _get_remote_git_context(client, root_path, _remote_path_dirname(file_path))
    if not git_context.get("available"):
        raise ValueError(git_context.get("error") or "Folder is not inside a Git worktree")

    repo_root = str(git_context["repo_root"])
    pathspec = _remote_git_pathspec(repo_root, file_path)
    diff_args_by_mode = {
        "worktree": ["diff", "--no-ext-diff", "--no-color", "--", pathspec],
        "staged": ["diff", "--cached", "--no-ext-diff", "--no-color", "--", pathspec],
        "head": ["diff", "HEAD", "--no-ext-diff", "--no-color", "--", pathspec],
    }
    diff_text, truncated, raw_bytes = _bounded_remote_git_diff(client, repo_root, diff_args_by_mode[mode])
    return {
        "git": git_context,
        "diff": diff_text,
        "truncated": truncated,
        "raw_bytes": raw_bytes,
    }


_REMOTE_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _remote_path_clean(path: Any) -> str:
    """Normalize remote path separators without applying local OS path rules."""
    return str(path or "").strip().replace("\\", "/")


def _remote_path_is_absolute(path: str) -> bool:
    """Return whether a remote path is absolute on POSIX or Windows SFTP servers."""
    cleaned = _remote_path_clean(path)
    return cleaned.startswith("/") or bool(_REMOTE_WINDOWS_DRIVE_RE.match(cleaned))


def _remote_path_join(base: str, child: str) -> str:
    """Join remote path fragments using SFTP-friendly separators."""
    base = _remote_path_clean(base)
    child = _remote_path_clean(child)
    if not base:
        return child
    if not child:
        return base
    return f"{base.rstrip('/')}/{child.lstrip('/')}"


def _remote_path_dirname(path: str) -> str:
    """Return a remote parent path using POSIX-style separators."""
    cleaned = _remote_path_clean(path).rstrip("/")
    if not cleaned:
        return ""
    if _REMOTE_WINDOWS_DRIVE_RE.match(cleaned) and "/" not in cleaned[3:]:
        return cleaned
    parent = posixpath.dirname(cleaned)
    return parent or ("/" if cleaned.startswith("/") else "")


def _remote_compare_path(path: str) -> str:
    """Return a stable path string for remote containment checks."""
    cleaned = _remote_path_clean(path)
    if cleaned != "/":
        cleaned = cleaned.rstrip("/")
    if _REMOTE_WINDOWS_DRIVE_RE.match(cleaned) or re.match(r"^/[A-Za-z]:", cleaned):
        return cleaned.lower()
    return cleaned


def _remote_path_inside(root_path: str, candidate_path: str) -> bool:
    """Return whether candidate_path is root_path or a descendant of it."""
    root_compare = _remote_compare_path(root_path)
    candidate_compare = _remote_compare_path(candidate_path)
    if root_compare == candidate_compare:
        return True
    if root_compare == "/":
        return candidate_compare.startswith("/")
    return candidate_compare.startswith(f"{root_compare.rstrip('/')}/")


def _relative_remote_explorer_path(root_path: str, path: str) -> str:
    """Return a slash-separated remote explorer path relative to the root."""
    root_clean = _remote_path_clean(root_path).rstrip("/")
    path_clean = _remote_path_clean(path).rstrip("/")
    if _remote_compare_path(root_clean) == _remote_compare_path(path_clean):
        return ""
    relative = posixpath.relpath(path_clean, root_clean or "/")
    return "" if relative == "." else relative.replace("\\", "/")


def _remote_explorer_entry_payload(root_path: str, directory_path: str, entry: Any) -> Dict[str, Any]:
    """Return metadata for one remote explorer entry."""
    mode = getattr(entry, "st_mode", None)
    is_dir = stat_module.S_ISDIR(mode) if mode is not None else False
    entry_path = _remote_path_join(directory_path, getattr(entry, "filename", ""))
    return {
        "name": getattr(entry, "filename", ""),
        "path": _relative_remote_explorer_path(root_path, entry_path),
        "type": "directory" if is_dir else "file",
        "size": None if is_dir else getattr(entry, "st_size", None),
        "modified": getattr(entry, "st_mtime", None),
    }


def _open_ssh_sftp(session: Any) -> Tuple[Any, Any]:
    """Open a short-lived SSH/SFTP connection for one explorer request."""
    if paramiko is None:
        raise RuntimeError("Paramiko is not installed. Run `pip install -r requirements.txt`.")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=session.host,
        port=session.port,
        username=session.username,
        password=session.password or None,
        timeout=ssh_config.get("connection_timeout", 30),
        look_for_keys=not bool(session.password),
        allow_agent=not bool(session.password),
    )
    return client, client.open_sftp()


def _close_sftp_client(client: Any, sftp: Any) -> None:
    """Close an SFTP handle and its owning SSH client."""
    try:
        sftp.close()
    except Exception:
        pass
    try:
        client.close()
    except Exception:
        pass


def _sftp_request_error_types() -> Tuple[type, ...]:
    """Return expected connection and SFTP errors for explorer routes."""
    error_types: List[type] = [OSError, RuntimeError]
    ssh_exception = getattr(paramiko, "SSHException", None) if paramiko is not None else None
    if isinstance(ssh_exception, type):
        error_types.append(ssh_exception)
    return tuple(error_types)


def _remote_is_directory(sftp: Any, path: str) -> bool:
    """Return whether a remote path is a directory."""
    attrs = sftp.stat(path)
    mode = getattr(attrs, "st_mode", None)
    return stat_module.S_ISDIR(mode) if mode is not None else False


def _remote_is_file(sftp: Any, path: str) -> bool:
    """Return whether a remote path is a regular file."""
    attrs = sftp.stat(path)
    mode = getattr(attrs, "st_mode", None)
    return stat_module.S_ISREG(mode) if mode is not None else True


def _remote_explorer_root_directory(session: Any) -> str:
    """Return a configured remote root, falling back to a sensible SSH default."""
    return _remote_path_clean(_explorer_root_directory(session) or getattr(session, "directory", "") or "/")


def _remote_default_explorer_candidate_path(sftp: Any, session: Any, root_path: str) -> str:
    """Return the default remote explorer directory when no path is requested."""
    current_raw = _remote_path_clean(getattr(session, "directory", "") or "")
    if not current_raw:
        return root_path

    try:
        candidate = sftp.normalize(current_raw)
        if _remote_is_directory(sftp, candidate) and _remote_path_inside(root_path, candidate):
            return candidate
    except OSError:
        pass
    return root_path


def _resolve_remote_explorer_candidate_path(
    sftp: Any,
    session: Any,
    requested_path: Any = "",
    *,
    allow_empty_root: bool = True,
) -> Tuple[str, str]:
    """Resolve an SSH explorer path while keeping it inside the remote root."""
    if not _is_remote_explorer_session(session):
        raise ValueError("Session is not an SSH file explorer pane")

    root_raw = _remote_explorer_root_directory(session)
    if not root_raw:
        raise ValueError("Explorer root directory is not configured")

    root_path = sftp.normalize(root_raw)
    if not _remote_is_directory(sftp, root_path):
        raise ValueError("Explorer root directory does not exist")

    if requested_path is None:
        if not allow_empty_root:
            raise ValueError("Explorer file path is required")
        candidate = _remote_default_explorer_candidate_path(sftp, session, root_path)
    else:
        raw_path = _remote_path_clean(requested_path)
        if not raw_path:
            if not allow_empty_root:
                raise ValueError("Explorer file path is required")
            candidate_raw = root_path
        elif _remote_path_is_absolute(raw_path):
            candidate_raw = raw_path
        else:
            candidate_raw = _remote_path_join(root_path, raw_path)
        candidate = sftp.normalize(candidate_raw)

    if not _remote_path_inside(root_path, candidate):
        raise ValueError("Explorer path must stay inside the configured root")

    return root_path, candidate


def _resolve_remote_explorer_paths(sftp: Any, session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve a remote explorer directory path inside the configured root."""
    root_path, candidate = _resolve_remote_explorer_candidate_path(sftp, session, requested_path)
    if not _remote_is_directory(sftp, candidate):
        raise ValueError("Explorer path is not a directory")
    return root_path, candidate


def _resolve_remote_explorer_file_path(sftp: Any, session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve a remote explorer file path inside the configured root."""
    root_path, candidate = _resolve_remote_explorer_candidate_path(
        sftp,
        session,
        requested_path,
        allow_empty_root=False,
    )
    if _remote_is_directory(sftp, candidate):
        raise ValueError("Explorer path is a directory")
    if not _remote_is_file(sftp, candidate):
        raise ValueError("Explorer path is not a file")
    return root_path, candidate


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
        logger.info(
            f"[{session_id}] paramiko.connect hostname={session.host} port={session.port}"
            f" user={session.username} password={'***' if session.password else None}"
        )
        client.connect(
            hostname=session.host,
            port=session.port,
            username=session.username,
            password=session.password or None,
            timeout=ssh_config.get("connection_timeout", 30),
            look_for_keys=not bool(session.password),
            allow_agent=not bool(session.password)
        )
        logger.info(f"[{session_id}] SSH connected successfully")

        channel = client.invoke_shell(term='xterm', width=120, height=30)

        connection = {
            "kind": "ssh",
            "client": client,
            "channel": channel,
        }

        if session_manager.get_session(session_id) is None:
            logger.info("[%s] Session was removed before SSH startup completed", session_id)
            _shutdown_connection(connection)
            return

        with connection_lock:
            ssh_connections[session_id] = connection
            session_output_buffers[session_id] = ""

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

        if session_manager.get_session(session_id) is None:
            logger.info("[%s] Session was removed before local shell startup completed", session_id)
            _shutdown_connection(connection)
            return

        with connection_lock:
            ssh_connections[session_id] = connection
            session_output_buffers[session_id] = ""

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
    return render_template(
        'index.html',
        max_sessions=max_sessions,
        agent_options=_agent_options(),
        local_windows_shells_available=os.name == "nt",
    )


@app.route('/terminals')
def terminals_page():
    """Page showing active terminal instances."""
    logger.info("GET /terminals")
    return render_template('terminals.html', max_sessions=max_sessions,
                           app_surface_mode=app_surface_mode,
                           voice_enabled=voice_enabled,
                           voice_engine=voice_engine,
                           voice_model=_active_voice_model_name(),
                           voice_language=voice_language)


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

    current = load_config()
    current = _merge_dicts(current, _normalize_app_config_update(data))
    save_config(current)
    _refresh_runtime_config()
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
        payload.update(
            {
                "group": None,
                "layout": active_launch_options["layout"],
                "connection_mode": active_launch_options["connection_mode"],
                "terminal_count": active_launch_options["terminal_count"],
                "workspace_layout": None,
                "surface_mode": app_surface_mode,
            }
        )
    return jsonify(payload)


@app.route('/api/explorer/<session_id>/entries', methods=['GET'])
def get_explorer_entries(session_id: str):
    """List entries for a file explorer pane."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    if _is_remote_explorer_session(session):
        client = None
        sftp = None
        try:
            client, sftp = _open_ssh_sftp(session)
            root_path, current_path = _resolve_remote_explorer_paths(
                sftp,
                session,
                request.args["path"] if "path" in request.args else None,
            )
            entries = [
                _remote_explorer_entry_payload(root_path, current_path, entry)
                for entry in sftp.listdir_attr(current_path)
            ]
            git_context, git_statuses = _get_remote_git_context(client, root_path, current_path)
            _attach_remote_git_status_to_entries(root_path, git_context, git_statuses, entries)
            _append_deleted_remote_git_entries(root_path, current_path, git_context, git_statuses, entries)
            entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))

            parent_path = ""
            if _remote_compare_path(current_path) != _remote_compare_path(root_path):
                parent_path = _relative_remote_explorer_path(root_path, _remote_path_dirname(current_path))

            return jsonify(
                {
                    "session_id": session.session_id,
                    "root": root_path,
                    "path": _relative_remote_explorer_path(root_path, current_path),
                    "parent_path": parent_path,
                    "git": git_context,
                    "entries": entries,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except _sftp_request_error_types() as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            if client is not None and sftp is not None:
                _close_sftp_client(client, sftp)

    try:
        root_path, current_path = _resolve_explorer_paths(
            session,
            request.args["path"] if "path" in request.args else None,
        )
        entries = []
        with os.scandir(current_path) as iterator:
            for entry in iterator:
                entries.append(_explorer_entry_payload(root_path, entry))
        git_context, git_statuses = _get_git_context(root_path, current_path)
        _attach_git_status_to_entries(root_path, git_context, git_statuses, entries)
        _append_deleted_git_entries(root_path, current_path, git_context, git_statuses, entries)
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))

        parent_path = ""
        if os.path.normcase(current_path) != os.path.normcase(root_path):
            parent_path = _relative_explorer_path(root_path, os.path.dirname(current_path))

        return jsonify(
            {
                "session_id": session.session_id,
                    "root": root_path,
                    "path": _relative_explorer_path(root_path, current_path),
                    "parent_path": parent_path,
                    "git": git_context,
                    "entries": entries,
                }
            )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500


@app.route('/api/explorer/<session_id>/file', methods=['GET'])
def get_explorer_file(session_id: str):
    """Return a safe, read-only text preview for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    if _is_remote_explorer_session(session):
        client = None
        sftp = None
        try:
            client, sftp = _open_ssh_sftp(session)
            root_path, file_path = _resolve_remote_explorer_file_path(
                sftp,
                session,
                request.args.get("path", ""),
            )
            stat_result = sftp.stat(file_path)
            code_language = _explorer_editor_language(file_path)
            with sftp.open(file_path, "rb") as file_handle:
                raw_content = file_handle.read(EXPLORER_FILE_PREVIEW_MAX_BYTES + 1)

            if _explorer_content_looks_binary(raw_content):
                raise ValueError("Explorer file appears to be binary")

            truncated = len(raw_content) > EXPLORER_FILE_PREVIEW_MAX_BYTES
            preview_bytes = raw_content[:EXPLORER_FILE_PREVIEW_MAX_BYTES]
            content = preview_bytes.decode("utf-8", errors="replace")
            preview_html = _render_markdown_preview(content) if _is_markdown_file(file_path) else None
            git_context, git_statuses = _get_remote_git_context(client, root_path, _remote_path_dirname(file_path))
            file_git = (
                _remote_git_status_for_entry(str(git_context["repo_root"]), file_path, git_statuses)
                if git_context.get("available")
                else _clean_git_entry_status()
            )

            return jsonify(
                {
                    "session_id": session.session_id,
                    "root": root_path,
                    "path": _relative_remote_explorer_path(root_path, file_path),
                    "name": posixpath.basename(_remote_path_clean(file_path)),
                    "size": getattr(stat_result, "st_size", None),
                    "modified": getattr(stat_result, "st_mtime", None),
                    "encoding": "utf-8",
                    "truncated": truncated,
                    "content": content,
                    "preview_type": "markdown" if preview_html is not None else None,
                    "preview_html": preview_html,
                    "language": code_language,
                    "git": file_git,
                    "git_context": git_context,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except _sftp_request_error_types() as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            if client is not None and sftp is not None:
                _close_sftp_client(client, sftp)

    try:
        root_path, file_path = _resolve_explorer_file_path(
            session,
            request.args.get("path", ""),
        )
        stat_result = os.stat(file_path, follow_symlinks=False)
        code_language = _explorer_editor_language(file_path)
        with open(file_path, "rb") as file_handle:
            raw_content = file_handle.read(EXPLORER_FILE_PREVIEW_MAX_BYTES + 1)

        if _explorer_content_looks_binary(raw_content):
            raise ValueError("Explorer file appears to be binary")

        truncated = len(raw_content) > EXPLORER_FILE_PREVIEW_MAX_BYTES
        preview_bytes = raw_content[:EXPLORER_FILE_PREVIEW_MAX_BYTES]
        content = preview_bytes.decode("utf-8", errors="replace")
        preview_html = _render_markdown_preview(content) if _is_markdown_file(file_path) else None
        git_context, git_statuses = _get_git_context(root_path, os.path.dirname(file_path))
        file_git = (
            _git_status_for_entry(str(git_context["repo_root"]), file_path, git_statuses)
            if git_context.get("available")
            else _clean_git_entry_status()
        )

        return jsonify(
            {
                "session_id": session.session_id,
                "root": root_path,
                "path": _relative_explorer_path(root_path, file_path),
                "name": os.path.basename(file_path),
                "size": stat_result.st_size,
                "modified": stat_result.st_mtime,
                "encoding": "utf-8",
                "truncated": truncated,
                "content": content,
                "preview_type": "markdown" if preview_html is not None else None,
                "preview_html": preview_html,
                "language": code_language,
                "git": file_git,
                "git_context": git_context,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500


@app.route('/api/explorer/<session_id>/git/diff', methods=['GET'])
def get_explorer_git_diff(session_id: str):
    """Return a bounded read-only Git diff for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if _is_remote_explorer_session(session):
        client = None
        sftp = None
        mode = request.args.get("mode", "worktree")
        try:
            client, sftp = _open_ssh_sftp(session)
            root_path, file_path = _resolve_remote_explorer_file_path(
                sftp,
                session,
                request.args.get("path", ""),
            )
            diff_payload = _get_remote_git_diff(client, root_path, file_path, mode)
            return jsonify(
                {
                    "session_id": session.session_id,
                    "root": root_path,
                    "path": _relative_remote_explorer_path(root_path, file_path),
                    "mode": mode,
                    **diff_payload,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except _sftp_request_error_types() as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            if client is not None and sftp is not None:
                _close_sftp_client(client, sftp)

    mode = request.args.get("mode", "worktree")
    try:
        root_path, file_path = _resolve_explorer_file_path(
            session,
            request.args.get("path", ""),
        )
        diff_payload = _get_git_diff(root_path, file_path, mode)
        return jsonify(
            {
                "session_id": session.session_id,
                "root": root_path,
                "path": _relative_explorer_path(root_path, file_path),
                "mode": mode,
                **diff_payload,
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500


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
        meta = {
            "group": None,
            "layout": active_launch_options["layout"],
            "connection_mode": active_launch_options["connection_mode"],
            "terminal_count": active_launch_options["terminal_count"],
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
    config = _normalize_session_config(data.get("config"))
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

        if len(sessions_config) > max_sessions:
            logger.warning(f"Too many sessions requested: {len(sessions_config)} > {max_sessions}")
            return jsonify({"error": f"Maximum {max_sessions} sessions allowed"}), 400

        connection_mode = _normalize_connection_mode(data.get("connection_mode"))
        layout = _normalize_layout(data.get("layout"), len(sessions_config))
        workspace_layout = _normalize_workspace_layout(data.get("workspace_layout"), len(sessions_config))
        surface_mode = _normalize_surface_mode(data.get("surface_mode"), app_surface_mode)
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
                if startup_mode == "explorer":
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
                    "File Explorer"
                    if startup_mode == "explorer"
                    else _local_shell_display_name(
                        use_wsl=use_wsl,
                        use_powershell=use_powershell,
                        distribution=prepared.get("distribution"),
                    )
                )
                prepared["use_wsl"] = use_wsl
                prepared["use_powershell"] = use_powershell

            prepared_sessions.append(prepared)

        launch_warnings = _sanitize_agent_launch_commands(connection_mode, prepared_sessions)

        active_launch_options.update(
            {
                "connection_mode": connection_mode,
                "layout": layout,
                "terminal_count": len(prepared_sessions),
            }
        )

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
            if _is_explorer_session(session):
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

    if _is_explorer_session(source):
        return jsonify({"error": "Explorer panes cannot be split"}), 400

    group = session_manager.get_group(source.group_id)
    if not group:
        return jsonify({"error": "Session group not found"}), 404

    group_sessions = session_manager.get_group_sessions(group.group_id)
    if len(group_sessions) >= max_sessions:
        return jsonify({"error": f"Maximum {max_sessions} sessions allowed"}), 400

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
    """Switch one terminal pane between terminal and file explorer mode."""
    session = session_manager.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    if session.mode not in {"ssh", "wsl"}:
        return jsonify({"error": "File explorer mode is only available for SSH and Local Repo sessions"}), 400

    data = request.get_json(silent=True) or {}
    target_mode = _normalize_startup_mode(data.get("startup_mode"), session.mode)
    if target_mode not in {"terminal", "explorer"}:
        return jsonify({"error": "startup_mode must be 'terminal' or 'explorer'"}), 400

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
                client, sftp = _open_ssh_sftp(session)
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
                if client is not None and sftp is not None:
                    _close_sftp_client(client, sftp)

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

    if not _is_explorer_session(session):
        return jsonify(session.to_dict())

    next_directory = session.directory
    root_path = _explorer_root_directory(session)
    if _is_remote_explorer_session(session):
        client = None
        sftp = None
        try:
            client, sftp = _open_ssh_sftp(session)
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
            if client is not None and sftp is not None:
                _close_sftp_client(client, sftp)
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

        session_manager.clear_disconnected_sessions()
        return jsonify({"message": "Session group closed successfully", "group_id": group_id})

    session_manager.close_all_sessions()
    _close_all_ssh_connections(clear_buffers=True)
    session_manager.reset_sessions()

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
        buffered_output = session_output_buffers.get(session_id, "") if should_replay_buffer else ""

        if buffered_output:
            # Serialize replay with live output streaming so startup output is not
            # delivered once from the buffer and again from the active stream.
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
_whisper_model_lock = threading.Lock()
_whisper_audio_buffers: Dict[str, bytearray] = {}
_whisper_audio_lock = threading.Lock()


def _whisper_engine_available() -> bool:
    """Return True when faster-whisper and numpy are importable."""
    return WhisperModel is not None and np is not None


def _voice_engine_unavailable_message(engine: Optional[str] = None) -> str:
    """Return a user-facing reason the configured voice backend cannot start."""
    selected_engine = engine or voice_engine
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
    """Load the configured faster-whisper model lazily."""
    global _whisper_model_instance

    if WhisperModel is None:
        raise RuntimeError(
            "faster-whisper is not installed. Install it with: pip install faster-whisper"
        )
    if np is None:
        raise RuntimeError(
            "numpy is not installed. Install it with: pip install numpy"
        )

    with _whisper_model_lock:
        if _whisper_model_instance is None:
            logger.info(
                "Loading faster-whisper model %s on %s (%s)",
                whisper_model,
                whisper_device,
                whisper_compute_type,
            )
            _whisper_model_instance = WhisperModel(
                whisper_model,
                device=whisper_device,
                compute_type=whisper_compute_type,
            )
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
    language = _whisper_language_code(voice_language)
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
        ws = ws_client.create_connection(vosk_service_url, timeout=timeout)
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
                logger.info("Using already-running vosk-service at %s", vosk_service_url)
            return True

        if _vosk_process is not None and _vosk_process.poll() is None:
            if _wait_for_vosk_ready(_vosk_process, timeout=vosk_startup_timeout_seconds):
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
            if not _wait_for_vosk_ready(process, timeout=vosk_startup_timeout_seconds):
                if process.poll() is None:
                    logger.error(
                        "vosk-service not ready after %ss; model download/load may still be in progress or the service may be hung",
                        vosk_startup_timeout_seconds,
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
    if voice_engine == "vosk":
        service_running: Optional[bool] = _vosk_service_reachable(timeout=1.0)
        service_url = vosk_service_url
        engine_available = ws_client is not None
    else:
        service_running = None
        service_url = ""
        engine_available = _whisper_engine_available()
    status_message = (
        "Voice backend is available."
        if engine_available
        else _voice_engine_unavailable_message(voice_engine)
    )
    return jsonify({
        'enabled': voice_enabled,
        'engine': voice_engine,
        'engine_available': engine_available,
        'ws_client_available': ws_client is not None,
        'service_running': service_running,
        'service_url': service_url,
        'model': _active_voice_model_name(),
        'language': voice_language,
        'startup_timeout_seconds': vosk_startup_timeout_seconds,
        'whisper_device': whisper_device,
        'whisper_compute_type': whisper_compute_type,
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
        ws = ws_client.create_connection(vosk_service_url, timeout=5)
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
                ws = ws_client.create_connection(vosk_service_url, timeout=5)
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
    if not voice_enabled:
        emit('voice_status', {'status': 'error',
                              'message': 'Voice input is disabled in config'})
        return

    session_id = data.get('session_id')
    if not session_id:
        emit('voice_status', {'status': 'error', 'message': 'Missing session_id'})
        return

    if voice_engine == 'whisper':
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

    if voice_engine == 'whisper':
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

    if voice_engine == 'whisper':
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
