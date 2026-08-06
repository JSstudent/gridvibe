"""Workspace-shape snapshot persistence for restore-after-restart.

Deep-dive feature 10.5: live shells cannot survive a backend restart by
design, but the workspace *shape* (groups + per-session launch config) can.
Schema v2 stores one slot per workspace id (``workspaces`` dict); with
multi-workspace there is one slot per captured workspace. Three writers
exist: the autosave timer (``capture_live_workspaces``), the explicit Save
Workspace action, and workspace rename (both ``capture_workspace``). A slot
also records which group was in front (``active_group_id``) so the restore
reopens the workspace on it rather than on whichever group happens to be
newest. The snapshot never contains passwords — a restored SSH session
re-authenticates with keys or a saved-session password. ``runtime_state.json``
is local state and gitignored.

Two ownership rules keep the file honest. Every capture takes a monotonic
ordering ticket *before* it reads the live manager, so a snapshot taken before
an explicit close/forget is rejected at commit time instead of resurrecting the
slot it was meant to remove. And the file location comes from
``GRIDVIBE_RUNTIME_STATE_PATH`` when set, with a ``GRIDVIBE_TEST_MODE`` guard
that refuses the canonical production path outright — the in-process lock
orders writers within one process only, so a second process (a test run) must
never share this file.
"""

import json
import logging
import math
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from web.paths import BASE_DIR
from web.workspaces import DEFAULT_WORKSPACE_ID, normalize_workspace_id

logger = logging.getLogger(__name__)

# The one file a production process owns. ``RUNTIME_STATE_PATH`` starts there
# but is deliberately overridable: a test process (or a second GridVibe run)
# points ``GRIDVIBE_RUNTIME_STATE_PATH`` at its own file so it can never
# read-modify-replace the user's real workspace snapshots.
PRODUCTION_STATE_PATH = os.path.join(BASE_DIR, "runtime_state.json")
RUNTIME_STATE_PATH = os.environ.get("GRIDVIBE_RUNTIME_STATE_PATH") or PRODUCTION_STATE_PATH
_runtime_state_lock = threading.Lock()

# Commit ordering (one entry per workspace, guarded by _runtime_state_lock).
# Every capture takes a ticket *before* it snapshots the live manager, so a
# snapshot taken before a close/forget carries an older ticket than the clear
# that followed it and is rejected at commit time instead of resurrecting the
# slot. ``_state_ticket_seq`` is the monotonic source; ``_workspace_commits``
# maps workspace id -> (newest ticket committed, "commit" | "clear").
_state_ticket_seq = 0
_workspace_commits: Dict[str, tuple] = {}

SCHEMA_VERSION = 2
RESTORABLE_ORIGINS = ("auto", "manual")
# The restore offer is deliberately permanent (no maximum age), so with N
# workspaces "one slot" would grow into "one slot per workspace ever autosaved"
# and only Forget would ever shrink it. Bound the automatic ones at the single
# write point; an explicit Save Workspace (origin "manual") is never evicted, so
# it keeps its permanent-offer promise.
MAX_AUTO_WORKSPACE_SLOTS = 12
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
    "explorer_source_font",
    "explorer_theme",
    "browser_tabs",
    "browser_active_tab",
)


def _snapshot_session(session: Any) -> Dict[str, Any]:
    """Return the replayable launch config for one live session."""
    data = session if isinstance(session, dict) else session.to_dict()
    return {key: data.get(key) for key in _SESSION_SNAPSHOT_FIELDS}


def _snapshot_group(group: Any, sessions: List[Any]) -> Dict[str, Any]:
    """Return one password-free runtime-state group payload."""
    data = group if isinstance(group, dict) else group.to_dict()
    return {
        "group_id": data.get("group_id"),
        "name": data.get("name"),
        "connection_mode": data.get("connection_mode"),
        "layout": data.get("layout"),
        "workspace_layout": data.get("workspace_layout"),
        # No surface_mode: chrome density is a live global setting, so a
        # restore must never replay the value a group launched with.
        "saved_session_id": data.get("saved_session_id"),
        "sessions": [_snapshot_session(session) for session in sessions],
    }


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


class RuntimeStatePathError(RuntimeError):
    """Raised when this process must not touch the production state file."""


def _checked_state_path() -> str:
    """Return the state file to use, refusing production state under test mode.

    ``GRIDVIBE_TEST_MODE`` marks a process that must never own the user's
    ``runtime_state.json``: the in-process lock serializes writers inside one
    process only, so a test run that reached the canonical path would happily
    read-modify-replace a live app's snapshots (and vice versa). Failing loudly
    beats corrupting the file that restore-after-restart depends on.
    """
    path = RUNTIME_STATE_PATH
    if os.environ.get("GRIDVIBE_TEST_MODE") and os.path.abspath(path) == os.path.abspath(
        PRODUCTION_STATE_PATH
    ):
        raise RuntimeStatePathError(
            "Refusing to use the production runtime_state.json in test mode; "
            "set GRIDVIBE_RUNTIME_STATE_PATH or patch RUNTIME_STATE_PATH."
        )
    return path


def _next_state_ticket() -> int:
    """Take the monotonic ordering ticket for one capture/clear operation."""
    global _state_ticket_seq
    with _runtime_state_lock:
        _state_ticket_seq += 1
        return _state_ticket_seq


def _capture_is_stale_locked(workspace_id: str, ticket: int, origin: str) -> bool:
    """True when this capture must not commit. Caller holds the lock.

    A capture is stale against anything committed after its ticket was taken:

    * a **clear** always wins — the workspace was explicitly closed or
      forgotten after this snapshot was read, so writing it back would make a
      closed workspace restorable again;
    * a newer **commit** wins over an autosave capture, so an older timer tick
      cannot overwrite the shape a later capture already stored. An explicit
      Save Workspace still wins over a newer autosave: it is the user's
      deliberate act, and only a close/forget may override it.
    """
    last = _workspace_commits.get(workspace_id)
    if last is None:
        return False
    last_ticket, last_kind = last
    if last_ticket <= ticket:
        return False
    return last_kind == "clear" or origin != "manual"


def _record_commit_locked(workspace_id: str, ticket: int, kind: str) -> None:
    """Record the newest ticket that reached the file. Caller holds the lock."""
    last = _workspace_commits.get(workspace_id)
    newest = max(ticket, last[0]) if last else ticket
    _workspace_commits[workspace_id] = (newest, kind)


def _read_state_locked() -> Dict[str, Any]:
    """Read the state file and migrate a legacy v1 blob. Caller holds the lock."""
    state_path = _checked_state_path()
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_state()
    except Exception as exc:
        logger.warning("Could not read %s: %s", state_path, exc)
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
    state_path = _checked_state_path()
    temp_path = os.path.join(
        os.path.dirname(os.path.abspath(state_path)) or ".",
        f".{os.path.basename(state_path)}.{uuid.uuid4().hex}.tmp",
    )
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
        os.replace(temp_path, state_path)
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

    The ordering ticket is taken before the live snapshot, so a capture that
    read the manager before an explicit close/forget is rejected here rather
    than committed afterwards — see :func:`_capture_is_stale_locked`. ``None``
    is returned in that case too, exactly as for an empty workspace.
    """
    workspace_id = normalize_workspace_id(workspace_id)
    ticket = _next_state_ticket()
    live_snapshot = session_manager.snapshot_live_workspaces().get(workspace_id)
    if not live_snapshot:
        return None
    groups = [
        _snapshot_group(group, list(group.get("sessions") or []))
        for group in live_snapshot.get("groups") or []
        if isinstance(group, dict) and group.get("sessions")
    ]
    if not groups:
        return None

    if active_group_id is None:
        active_group_id = live_snapshot.get("active_group_id")
    active_group_id = str(active_group_id or "").strip()
    captured_group_ids = {group["group_id"] for group in groups}
    normalized_zoom = normalize_native_zoom_factor(native_zoom_factor)
    workspace_label = str(live_snapshot.get("label") or "").strip()

    with _runtime_state_lock:
        if _capture_is_stale_locked(workspace_id, ticket, origin):
            logger.debug(
                "Dropped a stale %s capture of workspace %s", origin, workspace_id
            )
            return None
        state = _read_state_locked()
        previous_slot = state.get("workspaces", {}).get(workspace_id)
        previous_slot = previous_slot if isinstance(previous_slot, dict) else {}
        if normalized_zoom is None:
            normalized_zoom = normalize_native_zoom_factor(
                previous_slot.get("native_zoom_factor")
            )
        slot = {
            "workspace_id": workspace_id,
            "label": (
                str(label or "").strip()
                or workspace_label
                or _derive_workspace_label(groups)
            ),
            # A manual save is pinned for the Stage-4 auto-slot cap. Later
            # auto captures (autosave refresh, workspace rename) update the
            # slot without silently demoting it to an evictable auto slot.
            "origin": (
                "manual"
                if origin == "manual" or previous_slot.get("origin") == "manual"
                else "auto"
            ),
            "saved_at": time.time(),
            "active_group_id": active_group_id if active_group_id in captured_group_ids else "",
            "groups": groups,
        }
        if normalized_zoom is not None:
            slot["native_zoom_factor"] = normalized_zoom
        workspaces = state.setdefault("workspaces", {})
        workspaces[workspace_id] = slot
        _evict_excess_auto_slots(workspaces)
        _record_commit_locked(workspace_id, ticket, "commit")
        _write_state_locked(state)
    return slot


def load_restorable_workspace(workspace_id: str = DEFAULT_WORKSPACE_ID) -> Optional[Dict[str, Any]]:
    """Return the saved slot iff it has groups and a restorable origin.

    The offer is permanent — there is deliberately no maximum age.
    """
    workspace_id = normalize_workspace_id(workspace_id)
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


def clear_workspace(workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
    """Remove one workspace slot, preserving siblings.

    This is the **Forget** action: it deletes the workspace *snapshot* only —
    saved session presets live in a separate store (``saved_sessions.json``)
    and other slots may reference the same preset, so they are never touched.
    The file itself is kept (with version 2) even when the last slot is
    removed. Idempotent: ``False`` means the slot was already gone.

    The tombstone is recorded even when there was nothing to delete: a capture
    already holding a pre-close snapshot of this workspace must be rejected
    whether or not that snapshot had reached the file yet.
    """
    workspace_id = normalize_workspace_id(workspace_id)
    ticket = _next_state_ticket()
    with _runtime_state_lock:
        _record_commit_locked(workspace_id, ticket, "clear")
        state = _read_state_locked()
        if workspace_id not in state.get("workspaces", {}):
            return False
        del state["workspaces"][workspace_id]
        _write_state_locked(state)
        return True


def capture_live_workspaces(
    session_manager: Any,
    origin: str = "auto",
) -> Dict[str, Dict[str, Any]]:
    """Capture every non-empty live workspace with one consistent file write.

    The manager serializes live state during one lock hold and returns before
    this function acquires the file lock, preserving the documented lock order.
    Existing sibling slots for closed workspaces and saved native zoom values
    are retained. Workspaces closed, forgotten, or captured again between this
    tick's snapshot and its commit are skipped individually, so one stale tick
    can neither resurrect a closed workspace nor undo a newer capture.
    """
    ticket = _next_state_ticket()
    live_snapshots = session_manager.snapshot_live_workspaces()
    if not live_snapshots:
        return {}

    saved_at = time.time()
    with _runtime_state_lock:
        state = _read_state_locked()
        workspaces = state.setdefault("workspaces", {})
        stored_slots: Dict[str, Dict[str, Any]] = {}
        for workspace_id, snapshot in live_snapshots.items():
            if _capture_is_stale_locked(workspace_id, ticket, origin):
                logger.debug(
                    "Dropped a stale %s capture of workspace %s", origin, workspace_id
                )
                continue
            groups = [
                _snapshot_group(group, list(group.get("sessions") or []))
                for group in snapshot.get("groups") or []
                if isinstance(group, dict) and group.get("sessions")
            ]
            if not groups:
                continue
            previous_slot = workspaces.get(workspace_id)
            previous_slot = previous_slot if isinstance(previous_slot, dict) else {}
            slot_origin = (
                "manual"
                if origin == "manual" or previous_slot.get("origin") == "manual"
                else "auto"
            )
            slot = {
                "workspace_id": workspace_id,
                "label": (
                    str(snapshot.get("label") or "").strip()
                    or _derive_workspace_label(groups)
                ),
                # A manual save is pinned for the Stage-4 auto-slot cap. Later
                # autosaves refresh its shape without silently demoting it.
                "origin": slot_origin,
                "saved_at": saved_at,
                "active_group_id": str(snapshot.get("active_group_id") or "").strip(),
                "groups": groups,
            }
            previous_zoom = normalize_native_zoom_factor(
                previous_slot.get("native_zoom_factor")
            )
            if previous_zoom is not None:
                slot["native_zoom_factor"] = previous_zoom
            workspaces[workspace_id] = slot
            stored_slots[workspace_id] = slot
            _record_commit_locked(workspace_id, ticket, "commit")
        if stored_slots:
            _evict_excess_auto_slots(workspaces)
            _write_state_locked(state)
    return stored_slots


def _evict_excess_auto_slots(workspaces: Dict[str, Any]) -> None:
    """Keep only the newest ``MAX_AUTO_WORKSPACE_SLOTS`` auto slots.

    Applied at the single write point, oldest-first, and only to slots whose
    origin is still ``"auto"`` — an explicit Save Workspace is pinned. Caller
    holds ``_runtime_state_lock``.
    """
    auto_slots = [
        (workspace_id, slot)
        for workspace_id, slot in workspaces.items()
        if isinstance(slot, dict) and slot.get("origin") != "manual"
    ]
    if len(auto_slots) <= MAX_AUTO_WORKSPACE_SLOTS:
        return

    auto_slots.sort(
        key=lambda item: (
            item[1].get("saved_at") if isinstance(item[1].get("saved_at"), (int, float)) else 0,
            item[0],
        ),
        reverse=True,
    )
    evicted = [workspace_id for workspace_id, _ in auto_slots[MAX_AUTO_WORKSPACE_SLOTS:]]
    for workspace_id in evicted:
        del workspaces[workspace_id]
    logger.debug("Evicted %d oldest auto workspace slots", len(evicted))


def list_restorable_workspaces() -> List[Dict[str, Any]]:
    """Return credential-free summaries for all valid restorable slots."""
    with _runtime_state_lock:
        state = _read_state_locked()

    summaries = []
    for workspace_id, slot in state.get("workspaces", {}).items():
        if not isinstance(slot, dict):
            continue
        try:
            normalized_id = normalize_workspace_id(workspace_id)
        except ValueError:
            continue
        groups = slot.get("groups")
        if (
            not isinstance(groups, list)
            or not groups
            or slot.get("origin") not in RESTORABLE_ORIGINS
        ):
            continue
        valid_groups = [group for group in groups if isinstance(group, dict)]
        if not valid_groups:
            continue
        pane_count = sum(
            len(group.get("sessions") or [])
            for group in valid_groups
            if isinstance(group.get("sessions"), list)
        )
        summaries.append(
            {
                "workspace_id": normalized_id,
                "label": (
                    str(slot.get("label") or "").strip()
                    or _derive_workspace_label(valid_groups)
                ),
                "origin": slot.get("origin"),
                "saved_at": slot.get("saved_at"),
                "group_count": len(valid_groups),
                "pane_count": pane_count,
            }
        )
    return sorted(
        summaries,
        key=lambda summary: (
            -(summary["saved_at"] if isinstance(summary["saved_at"], (int, float)) else 0),
            summary["workspace_id"],
        ),
    )
