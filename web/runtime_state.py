"""Workspace-shape snapshot persistence for restore-after-restart.

Deep-dive feature 10.5: live shells cannot survive a backend restart by
design, but the workspace *shape* (groups + per-session launch config) can.
Schema v2 stores one slot per workspace id (``workspaces`` dict) so a future
multi-window upgrade is additive; today there is exactly one ``"default"``
workspace. Exactly two writers exist — the autosave timer and the explicit
Save Workspace action — and both funnel through ``capture_workspace``. A slot
also records which group was in front (``active_group_id``) so the restore
reopens the workspace on it rather than on whichever group happens to be
newest. The snapshot never contains passwords — a restored SSH session
re-authenticates with keys or a saved-session password. ``runtime_state.json``
is local state and gitignored.
"""

import json
import logging
import math
import os
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

RUNTIME_STATE_PATH = os.path.join(BASE_DIR, "runtime_state.json")
_runtime_state_lock = threading.Lock()

SCHEMA_VERSION = 2
DEFAULT_WORKSPACE_ID = "default"
RESTORABLE_ORIGINS = ("auto", "manual")
NATIVE_ZOOM_FACTOR_MIN = 0.25
NATIVE_ZOOM_FACTOR_MAX = 5.0


def normalize_native_zoom_factor(value: Any) -> Optional[float]:
    """Normalize the optional desktop session-window zoom stored with a slot."""
    if value is None or value == "":
        return None
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(factor) or not (
        NATIVE_ZOOM_FACTOR_MIN <= factor <= NATIVE_ZOOM_FACTOR_MAX
    ):
        return None
    return round(factor, 3)


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
    "explorer_search_open",
    "explorer_open_tabs",
    "explorer_active_tab",
    "explorer_tab_views",
    "explorer_md_preset",
    "explorer_md_font",
    "explorer_theme",
    "browser_tabs",
    "browser_active_tab",
)


def _snapshot_session(session: Any) -> Dict[str, Any]:
    """Return the replayable launch config for one live session."""
    data = session.to_dict()
    return {key: data.get(key) for key in _SESSION_SNAPSHOT_FIELDS}


def _looks_like_timestamp_name(name: str) -> bool:
    """True for the legacy auto-generated ``Session HH:MM:SS`` group names."""
    parts = name.rsplit(" ", 1)
    if len(parts) != 2:
        return False
    digits = parts[1].split(":")
    return len(digits) == 3 and all(part.isdigit() for part in digits)


def _derive_workspace_label(groups: List[Dict[str, Any]]) -> str:
    """Compute a stable human-facing label for a captured workspace.

    Precedence: the first group's real name → a session host → a session
    directory basename → a neutral "Workspace". Never a bare timestamp —
    ``Session HH:MM:SS`` names are auto-generated and meaningless a day later.
    """
    for group in groups:
        name = str(group.get("name") or "").strip()
        if name and not _looks_like_timestamp_name(name):
            return name
    for group in groups:
        for session in group.get("sessions") or []:
            host = str(session.get("host") or "").strip()
            if host:
                return host
    for group in groups:
        for session in group.get("sessions") or []:
            directory = str(session.get("directory") or "").strip()
            if directory:
                basename = os.path.basename(directory.rstrip("/\\"))
                if basename:
                    return basename
    return "Workspace"


def _empty_state() -> Dict[str, Any]:
    return {"version": SCHEMA_VERSION, "workspaces": {}}


def _read_state_locked() -> Dict[str, Any]:
    """Read the state file and migrate a legacy v1 blob. Caller holds the lock."""
    try:
        with open(RUNTIME_STATE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_state()
    except Exception as exc:
        logger.warning("Could not read %s: %s", RUNTIME_STATE_PATH, exc)
        return _empty_state()

    if not isinstance(data, dict):
        return _empty_state()
    if data.get("version") == SCHEMA_VERSION and isinstance(data.get("workspaces"), dict):
        return data

    # One-time v1 → v2 migration: a legacy single blob wraps into the
    # "default" slot with origin "auto" so an existing user still gets the
    # restore offer once; the next capture rewrites the file as v2.
    groups = data.get("groups")
    if not isinstance(groups, list) or not groups:
        return _empty_state()
    state = _empty_state()
    state["workspaces"][DEFAULT_WORKSPACE_ID] = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "label": _derive_workspace_label(groups),
        "origin": "auto",
        "saved_at": data.get("saved_at") if isinstance(data.get("saved_at"), (int, float)) else time.time(),
        # v1 predates the active-group hint; a restore falls back to the
        # workspace's own group ordering, exactly as it did before.
        "active_group_id": "",
        "groups": groups,
    }
    return state


def _write_state_locked(state: Dict[str, Any]) -> None:
    """Atomically persist the state. Caller holds the lock."""
    temp_path = f"{RUNTIME_STATE_PATH}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.replace(temp_path, RUNTIME_STATE_PATH)
    except Exception as exc:
        logger.warning("Could not persist runtime workspace state: %s", exc)
        try:
            os.remove(temp_path)
        except OSError:
            pass


def capture_workspace(
    session_manager: Any,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    origin: str = "auto",
    label: Optional[str] = None,
    active_group_id: Optional[str] = None,
    native_zoom_factor: Any = None,
) -> Optional[Dict[str, Any]]:
    """Capture one workspace's shape from the live manager and persist its slot.

    Read-modify-writes only that workspace's slot so sibling slots survive.
    Returns the stored slot dict, or ``None`` when the workspace has no live
    groups (the existing slot, if any, is left untouched — a slot is only ever
    overwritten by a non-empty capture, never cleared by the timer).

    ``active_group_id`` names the group the restore should reopen on; ``None``
    (the autosave timer's case) asks the manager for its live hint. Either way
    it only survives if it names a group this capture actually stored, so a
    restore never targets a group that was skipped for having no sessions.

    ``native_zoom_factor`` is optional desktop-window state. An explicit manual
    save supplies it; captures without one (notably the autosave timer) preserve
    the value already stored in this workspace's slot.
    """
    workspace_id = str(workspace_id or DEFAULT_WORKSPACE_ID).strip() or DEFAULT_WORKSPACE_ID
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
                # No surface_mode: chrome density is a live global setting, so
                # a restore must never replay the value a group launched with.
                "saved_session_id": group.saved_session_id,
                "sessions": [_snapshot_session(session) for session in sessions],
            }
        )
    if not groups:
        return None

    if active_group_id is None:
        getter = getattr(session_manager, "get_active_group_id", None)
        active_group_id = getter() if callable(getter) else ""
    active_group_id = str(active_group_id or "").strip()
    captured_group_ids = {group["group_id"] for group in groups}
    normalized_zoom = normalize_native_zoom_factor(native_zoom_factor)

    with _runtime_state_lock:
        state = _read_state_locked()
        if normalized_zoom is None:
            previous_slot = state.get("workspaces", {}).get(workspace_id)
            if isinstance(previous_slot, dict):
                normalized_zoom = normalize_native_zoom_factor(
                    previous_slot.get("native_zoom_factor")
                )
        slot = {
            "workspace_id": workspace_id,
            "label": str(label or "").strip() or _derive_workspace_label(groups),
            "origin": "manual" if origin == "manual" else "auto",
            "saved_at": time.time(),
            "active_group_id": active_group_id if active_group_id in captured_group_ids else "",
            "groups": groups,
        }
        if normalized_zoom is not None:
            slot["native_zoom_factor"] = normalized_zoom
        state.setdefault("workspaces", {})[workspace_id] = slot
        _write_state_locked(state)
    return slot


def load_restorable_workspace(workspace_id: str = DEFAULT_WORKSPACE_ID) -> Optional[Dict[str, Any]]:
    """Return the saved slot iff it has groups and a restorable origin.

    The offer is permanent — there is deliberately no maximum age.
    """
    workspace_id = str(workspace_id or DEFAULT_WORKSPACE_ID).strip() or DEFAULT_WORKSPACE_ID
    with _runtime_state_lock:
        state = _read_state_locked()
    slot = state.get("workspaces", {}).get(workspace_id)
    if not isinstance(slot, dict):
        return None
    groups = slot.get("groups")
    if not isinstance(groups, list) or not groups:
        return None
    if slot.get("origin") not in RESTORABLE_ORIGINS:
        return None
    # Re-validate the hint on read: the file is local state a user may edit, and
    # an id naming no stored group must degrade to "no preference", never send
    # the restore looking for a group it will not create.
    active_group_id = str(slot.get("active_group_id") or "").strip()
    stored_group_ids = {
        str(group.get("group_id") or "")
        for group in groups
        if isinstance(group, dict)
    }
    slot["active_group_id"] = active_group_id if active_group_id in stored_group_ids else ""
    # Hand-edited or older local state degrades to "no zoom preference".
    slot["native_zoom_factor"] = normalize_native_zoom_factor(
        slot.get("native_zoom_factor")
    )
    return slot


def clear_workspace(workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
    """Remove one workspace slot, preserving siblings.

    Multi-workspace skeleton: not wired to the single-workspace UI. The file
    itself is kept (with version 2) even when the last slot is removed.
    """
    workspace_id = str(workspace_id or DEFAULT_WORKSPACE_ID).strip() or DEFAULT_WORKSPACE_ID
    with _runtime_state_lock:
        state = _read_state_locked()
        if workspace_id not in state.get("workspaces", {}):
            return
        del state["workspaces"][workspace_id]
        _write_state_locked(state)


def iter_live_workspaces(session_manager: Any) -> Iterator[Tuple[str, List[Any]]]:
    """Yield ``(workspace_id, groups)`` for each live workspace with sessions.

    Single mapping point between live groups and workspace ids: today every
    live group belongs to the one "default" workspace, so this yields at most
    one entry (and nothing at all when no group has sessions). When windows
    own their own group sets, only this helper changes.
    """
    groups = [
        group
        for group in session_manager.get_all_groups()
        if session_manager.get_group_sessions(group.group_id)
    ]
    if groups:
        yield DEFAULT_WORKSPACE_ID, groups
