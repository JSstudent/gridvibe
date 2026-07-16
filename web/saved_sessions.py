"""Saved launcher presets: persistence and session-config normalization.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Covers the launcher
session-config normalization helpers, the built-in default preset, and the
``saved_sessions.json`` load/save/upsert/delete flows. ``web.api`` re-exports
every name for backwards compatibility.
"""

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from web.config import runtime_config
from web.paths import BASE_DIR
from web.secrets import _decrypt_password, _encrypt_password

logger = logging.getLogger(__name__)

SAVED_SESSIONS_PATH = os.path.join(BASE_DIR, "saved_sessions.json")
DEFAULT_SAVED_SESSION_ID = "default-session"
DEFAULT_SAVED_SESSION_NAME = "Default Session"

# Explorer tabbed viewer persistence bounds (ISSUE-2026-015).
EXPLORER_MAX_OPEN_TABS = 12
EXPLORER_MAX_TAB_PATH_LENGTH = 4096


def _normalize_explorer_tab_path(value: Any) -> str:
    """Normalize one persisted explorer tab path (root-relative, no traversal).

    Returns "" for absolute paths, drive letters, ``..`` traversal, or anything
    over the length cap, so an unsafe or out-of-root entry is dropped rather
    than restored.
    """
    text = str(value or "").replace("\\", "/").strip()
    if not text or len(text) > EXPLORER_MAX_TAB_PATH_LENGTH:
        return ""
    segments: List[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == ".." or ":" in segment:
            return ""
        segments.append(segment)
    return "/".join(segments)


def _normalize_explorer_open_tabs(value: Any) -> List[str]:
    """Bound and de-duplicate the persisted list of open explorer tab paths."""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen = set()
    for item in value:
        path = _normalize_explorer_tab_path(item)
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
        if len(result) >= EXPLORER_MAX_OPEN_TABS:
            break
    return result


def _normalize_explorer_active_tab(value: Any, open_tabs: List[str]) -> str:
    """Keep the active tab only when it points at one of the open tabs."""
    path = _normalize_explorer_tab_path(value)
    return path if path in open_tabs else ""


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
            "explorer_open_tabs": [],
            "explorer_active_tab": "",
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
        open_tabs = _normalize_explorer_open_tabs(entry.get("explorer_open_tabs"))
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
                "explorer_open_tabs": open_tabs,
                "explorer_active_tab": _normalize_explorer_active_tab(entry.get("explorer_active_tab"), open_tabs),
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
        saved_terminal["explorer_open_tabs"] = (
            workspace_terminal["explorer_open_tabs"] if startup_mode == "explorer" else []
        )
        saved_terminal["explorer_active_tab"] = (
            workspace_terminal["explorer_active_tab"] if startup_mode == "explorer" else ""
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
