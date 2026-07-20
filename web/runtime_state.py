"""Workspace-shape snapshot persistence for restore-after-restart.

Deep-dive feature 10.5: live shells cannot survive a backend restart by
design, but the workspace *shape* (groups + per-session launch config) can.
The snapshot is rewritten on every group create/close/reorder and never
contains passwords — a restored SSH session re-authenticates with keys or a
saved-session password. `runtime_state.json` is local state and gitignored.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

RUNTIME_STATE_PATH = os.path.join(BASE_DIR, "runtime_state.json")
RUNTIME_STATE_MAX_AGE_SECONDS = 12 * 60 * 60
_runtime_state_lock = threading.Lock()

# TerminalSession launch fields worth replaying through POST /api/sessions.
# `password` is deliberately absent; ids/status/timestamps are per-run state.
_SESSION_SNAPSHOT_FIELDS = (
    "host",
    "directory",
    "username",
    "port",
    "initial_command",
    "initial_command_mode",
    "agent_selection",
    "custom_agent",
    "agent_auto_mode",
    "title",
    "distribution",
    "use_wsl",
    "use_powershell",
    "startup_mode",
    "explorer_root_directory",
    "explorer_tree_open",
    "explorer_git_open",
    "explorer_open_tabs",
    "explorer_active_tab",
    "explorer_tab_views",
    "explorer_md_preset",
    "explorer_md_font",
)


def _snapshot_session(session: Any) -> Dict[str, Any]:
    """Return the replayable launch config for one live session."""
    data = session.to_dict()
    return {key: data.get(key) for key in _SESSION_SNAPSHOT_FIELDS}


def build_workspace_snapshot(session_manager: Any) -> Dict[str, Any]:
    """Capture the current workspace shape from the live session manager."""
    groups = []
    for group in session_manager.get_all_groups():
        sessions = session_manager.get_group_sessions(group.group_id)
        if not sessions:
            continue
        groups.append(
            {
                "group_id": group.group_id,
                "name": group.name,
                "connection_mode": group.connection_mode,
                "layout": group.layout,
                "workspace_layout": group.workspace_layout,
                "surface_mode": group.surface_mode,
                "saved_session_id": group.saved_session_id,
                "sessions": [_snapshot_session(session) for session in sessions],
            }
        )
    return {"version": 1, "saved_at": time.time(), "groups": groups}


def save_workspace_snapshot(session_manager: Any) -> None:
    """Persist the current workspace shape; failures only log a warning."""
    snapshot = build_workspace_snapshot(session_manager)
    temp_path = f"{RUNTIME_STATE_PATH}.tmp"
    try:
        with _runtime_state_lock:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, indent=2)
                handle.write("\n")
            os.replace(temp_path, RUNTIME_STATE_PATH)
    except Exception as exc:
        logger.warning("Could not persist runtime workspace state: %s", exc)
        try:
            os.remove(temp_path)
        except OSError:
            pass


def load_restorable_workspace(
    max_age_seconds: float = RUNTIME_STATE_MAX_AGE_SECONDS,
) -> Optional[Dict[str, Any]]:
    """Return the last saved snapshot if it has groups and is fresh enough."""
    try:
        with _runtime_state_lock:
            with open(RUNTIME_STATE_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Could not read %s: %s", RUNTIME_STATE_PATH, exc)
        return None

    if not isinstance(data, dict):
        return None
    groups = data.get("groups")
    saved_at = data.get("saved_at")
    if not isinstance(groups, list) or not groups:
        return None
    if not isinstance(saved_at, (int, float)) or time.time() - saved_at > max_age_seconds:
        return None
    return data


def prune_group_from_snapshot(group_id: str) -> None:
    """Remove one group from the persisted snapshot without rebuilding it.

    Closing a group must not rebuild the whole snapshot from the live session
    manager: mid-session the live set can be transiently smaller than the
    workspace the user actually has (e.g. a leftover scratch pane), which would
    collapse the restorable snapshot. Instead we surgically drop just the closed
    group and keep the rest of the last-written shape intact.
    """
    group_id = str(group_id or "").strip()
    if not group_id:
        return
    try:
        with _runtime_state_lock:
            try:
                with open(RUNTIME_STATE_PATH, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except FileNotFoundError:
                return
            if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
                return
            remaining = [
                group
                for group in data["groups"]
                if isinstance(group, dict) and group.get("group_id") != group_id
            ]
            if len(remaining) == len(data["groups"]):
                return
            data["groups"] = remaining
            data["saved_at"] = time.time()
            temp_path = f"{RUNTIME_STATE_PATH}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            os.replace(temp_path, RUNTIME_STATE_PATH)
    except Exception as exc:
        logger.warning("Could not prune group %s from runtime state: %s", group_id, exc)


def clear_runtime_state() -> None:
    """Remove the persisted snapshot (used when the user dismisses restore)."""
    try:
        with _runtime_state_lock:
            os.remove(RUNTIME_STATE_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove %s: %s", RUNTIME_STATE_PATH, exc)
