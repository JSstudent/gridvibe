"""Workspace identity, destination resolution, launch, and restore services.

The identity half deliberately stays independent from Flask and SessionManager
so the session model, HTTP boundary, Socket.IO rooms, and native bridge all
enforce the same opaque-id contract without creating import cycles.

The orchestration half (destination resolution, the shared launch service, and
server-side restore) is the backend home for multi-workspace behaviour so
``web/api.py`` keeps only thin routes. ``sessions/manager.py`` imports this
module at import time, so every collaborator that leads back to the manager
(``web.app``, ``web.terminal_io``, ``web.saved_sessions``) is imported lazily
inside the functions that need it — the cycle only exists at import time.
"""

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_ID = "default"
_WORKSPACE_ID_PATTERN = re.compile(r"^[a-z0-9]{12}$")

# A label is chrome, not an identifier: bound it so a hand-crafted request
# cannot push an unbounded string into every workspace picker and saved slot.
WORKSPACE_LABEL_MAX_LENGTH = 80


def normalize_workspace_id(value: Any = None) -> str:
    """Return a valid opaque workspace id, defaulting omitted values.

    Only the permanent ``default`` workspace and generated-style twelve
    character lowercase alphanumeric ids are accepted. Room names and storage
    keys must only ever be derived from this normalized value.
    """
    workspace_id = str(value or DEFAULT_WORKSPACE_ID).strip()
    if workspace_id == DEFAULT_WORKSPACE_ID or _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        return workspace_id
    raise ValueError("Invalid workspace id")


def generate_workspace_id() -> str:
    """Generate an opaque workspace id matching :func:`normalize_workspace_id`."""
    return uuid.uuid4().hex[:12]


def workspace_room(workspace_id: Any = None) -> str:
    """Return the Socket.IO room for a normalized workspace id."""
    return f"workspace:{normalize_workspace_id(workspace_id)}"


def normalize_workspace_label(value: Any) -> str:
    """Return a bounded, single-line workspace label ("" when unset)."""
    label = " ".join(str(value or "").split())
    return label[:WORKSPACE_LABEL_MAX_LENGTH]


def public_workspace_payload(workspace: Any, group_count: int = 0) -> Dict[str, Any]:
    """Return the credential-free public summary for one live workspace."""
    return {
        "workspace_id": normalize_workspace_id(getattr(workspace, "workspace_id", None)),
        "label": str(getattr(workspace, "label", "") or "").strip(),
        "created_at": float(getattr(workspace, "created_at", 0.0) or 0.0),
        "active_group_id": str(getattr(workspace, "active_group_id", "") or "").strip(),
        "group_count": max(0, int(group_count)),
        "retain_when_empty": bool(getattr(workspace, "retain_when_empty", False)),
    }


def workspace_missing_payload() -> Dict[str, Any]:
    """Return the error body for a request whose workspace no longer exists.

    ``workspace_missing`` is the machine-readable half of the message. A
    workspace window outlives its workspace whenever the last group leaves it —
    closed here, closed pane by pane, or moved out from another window — and
    without this marker the window could only render "Load error: Workspace not
    found" over a stale tab list it can never reload. It closes itself instead.
    """
    return {"error": "Workspace not found", "workspace_missing": True}


# ==================== Live workspace inspection ====================


def _manager():
    """Return the shared SessionManager (imported lazily — see module docstring)."""
    from web.app import session_manager

    return session_manager


def list_live_workspaces() -> List[Dict[str, Any]]:
    """Return public summaries for every live workspace, default first."""
    session_manager = _manager()
    with session_manager.lock:
        return [
            public_workspace_payload(
                workspace,
                len(session_manager.get_workspace_groups(workspace.workspace_id)),
            )
            for workspace in session_manager.get_all_workspaces()
        ]


def workspace_has_groups(workspace_id: Any) -> bool:
    """True when the workspace is live *and* currently owns at least one group.

    "Already live" is deliberately about content, not about the record: the
    ``default`` workspace record is permanent, so an existence check alone would
    make the default slot permanently unrestorable.
    """
    try:
        resolved_workspace_id = normalize_workspace_id(workspace_id)
    except ValueError:
        return False
    session_manager = _manager()
    if session_manager.get_workspace(resolved_workspace_id) is None:
        return False
    return bool(session_manager.get_workspace_groups(resolved_workspace_id))


def workspace_label(workspace_id: Any) -> str:
    """Return one live workspace's label, or "" when it is unknown/unlabelled."""
    try:
        workspace = _manager().get_workspace(normalize_workspace_id(workspace_id))
    except ValueError:
        return ""
    return str(getattr(workspace, "label", "") or "").strip()


def forget_pruned_workspaces(workspace_ids: Any) -> List[str]:
    """Drop the saved snapshot of every workspace pruned by losing its last group.

    Emptying a non-default workspace removes the live record, so leaving its
    snapshot behind made the launcher contradict itself: the workspace was gone
    from the Workspaces card while *Reopen saved…* still offered it, and
    reopening it resurrected a workspace the user had just emptied. For this
    release, removing the last group removes the workspace globally.

    Two deliberate exclusions:

    * ``default`` never reaches this path — its live record is permanent, so it
      is never *pruned* and has no id to pass here. Emptying it is handled by
      :func:`forget_emptied_default_workspace`, which the same callers invoke.
    * Leaving multi-workspace mode (``close_extra_workspaces``) removes its
      workspaces without pruning them here, so everything autosave or
      Workspace ▸ Save Workspace already wrote stays restorable.

    Returns the ids whose snapshot actually existed and was removed.
    """
    from web.runtime_state import clear_workspace

    forgotten: List[str] = []
    for workspace_id in workspace_ids or ():
        try:
            resolved_workspace_id = normalize_workspace_id(workspace_id)
        except ValueError:
            continue
        if resolved_workspace_id == DEFAULT_WORKSPACE_ID:
            continue
        try:
            if clear_workspace(resolved_workspace_id):
                forgotten.append(resolved_workspace_id)
        except Exception:
            logger.exception(
                "Could not clear the saved snapshot for pruned workspace %s",
                resolved_workspace_id,
            )
    if forgotten:
        logger.debug("Forgot saved snapshots for pruned workspaces: %s", forgotten)
    return forgotten


def forget_emptied_default_workspace(closed_workspace_id: Any) -> bool:
    """Drop ``default``'s snapshot when an explicit close just emptied it.

    ``default``'s live record is permanent, so it never reaches
    :func:`forget_pruned_workspaces` and its snapshot used to be immortal: every
    other workspace forgets its shape on losing its last group, while a group
    once run in ``default`` stayed on offer in the restore chooser forever, only
    removable by hand. It came back on every restore — including as a workspace
    the user had already closed minutes earlier.

    The narrow condition is what keeps restore-after-restart intact:

    * ``closed_workspace_id`` must be ``default`` itself. A close in a sibling
      workspace leaves an *unrelated* empty ``default`` behind (it may simply
      never have been used this run) and must not touch its snapshot.
    * ``default`` must own no groups afterwards, so closing one of several tabs
      keeps the workspace — and its slot — alive.
    * Only explicit user closes and moves call this. A process exit does not, so
      the snapshot still survives a restart and is still offered, which is the
      whole point of the feature.

    Returns ``True`` only when a snapshot actually existed and was removed. The
    caller must not hold ``SessionManager.lock`` — this writes the state file.
    """
    from web.runtime_state import clear_workspace

    try:
        if normalize_workspace_id(closed_workspace_id) != DEFAULT_WORKSPACE_ID:
            return False
    except ValueError:
        return False
    if _manager().get_workspace_groups(DEFAULT_WORKSPACE_ID):
        return False
    try:
        forgotten = clear_workspace(DEFAULT_WORKSPACE_ID)
    except Exception:
        logger.exception("Could not clear the saved snapshot for the default workspace")
        return False
    if forgotten:
        logger.debug("Forgot the saved snapshot of the emptied default workspace")
    return forgotten


# ==================== Destination resolution ====================


class WorkspaceRequestError(ValueError):
    """A workspace request the caller got wrong; carries an HTTP status."""

    def __init__(self, message: str, status: int = 400, payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


def resolve_launch_destination(data: Dict[str, Any]) -> Tuple[str, str]:
    """Resolve a launch/move destination to ``(workspace_id, created_id)``.

    ``created_id`` is non-empty only when this call created the workspace, so a
    failure downstream can roll it back and never leave a dead destination in
    the picker. Omitting both fields keeps targeting ``default``.
    """
    session_manager = _manager()
    wants_new = bool(data.get("new_workspace"))
    requested_id = data.get("workspace_id")

    if wants_new:
        label = normalize_workspace_label(
            data.get("workspace_label")
            if data.get("workspace_label") is not None
            else data.get("label")
        )
        workspace = session_manager.create_workspace(label=label)
        return workspace.workspace_id, workspace.workspace_id

    try:
        workspace_id = normalize_workspace_id(requested_id)
    except ValueError as exc:
        raise WorkspaceRequestError(str(exc)) from exc
    if session_manager.get_workspace(workspace_id) is None:
        raise WorkspaceRequestError("Workspace not found")
    return workspace_id, ""


def rollback_created_workspace(created_workspace_id: str) -> None:
    """Drop a destination this request created after the request failed."""
    if created_workspace_id:
        _manager().remove_workspace(created_workspace_id)


def saved_session_conflict(saved_session_id: str, target_workspace_id: str) -> Optional[Dict[str, Any]]:
    """Return the §6 conflict body when a preset is live in another workspace.

    A saved-preset-backed group is live in at most one workspace at a time, so
    this must be answered *before* any destructive replace-in-place: replacing
    first would tear down the source workspace's live sessions and only then
    discover the ownership conflict.
    """
    normalized = str(saved_session_id or "").strip()
    if not normalized:
        return None
    session_manager = _manager()
    group = session_manager.find_saved_session_group(normalized)
    if group is None or group.workspace_id == target_workspace_id:
        return None
    return {
        "error": "That saved session is already open in another workspace",
        "conflict": "saved_session_live",
        "saved_session_id": normalized,
        "group_id": group.group_id,
        "workspace_id": group.workspace_id,
        "workspace_label": workspace_label(group.workspace_id),
    }


def _taken_session_names() -> List[str]:
    """Every session name a new scratch launch has to stay clear of.

    Live groups *and* saved presets: saving a scratch session under the name it
    was given ("10.0.0.5 (1)") has to make the next scratch launch skip that
    number rather than mint a second session with the same name.
    """
    from web.saved_sessions import load_saved_sessions

    names = [group.name for group in _manager().get_all_groups()]
    names.extend(entry["name"] for entry in load_saved_sessions())
    return names


# ==================== Shared launch service ====================


def _redacted_launch_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize a launch request for the log without any credential.

    ISSUE-2026-037: the launch route used to log the whole request body, which
    put every saved-session password into ``logs/gridvibe.log``. Only shapes and
    non-secret identifiers are logged now; ``password`` never appears.
    """
    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
    return {
        "connection_mode": data.get("connection_mode"),
        "layout": data.get("layout"),
        "session_count": len(sessions),
        "named": bool(data.get("session_name")),
        "saved_session_id": str(data.get("saved_session_id") or ""),
        "workspace_id": str(data.get("workspace_id") or ""),
        "new_workspace": bool(data.get("new_workspace")),
        "restore": bool(data.get("restore")),
        "startup_modes": [
            str(
                (config or {}).get("startup_mode")
                or (config or {}).get("initial_command_mode")
                or ""
            )
            for config in sessions
            if isinstance(config, dict)
        ],
        "credentials_supplied": any(
            bool(isinstance(config, dict) and config.get("password"))
            for config in sessions
        ),
    }


def _prepare_launch_sessions(
    sessions_config: List[Dict[str, Any]],
    connection_mode: str,
) -> List[Dict[str, Any]]:
    """Normalize each requested pane into its TerminalSession launch fields."""
    import os

    from web.saved_sessions import (
        DEFAULT_BROWSER_URL,
        _normalize_browser_active_tab,
        _normalize_browser_tabs,
        _normalize_browser_url,
        _normalize_startup_mode,
    )
    from web.terminal_io import _local_shell_display_name

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
                browser_tabs = _normalize_browser_tabs(
                    prepared.get("browser_tabs"),
                    prepared.get("initial_command") or DEFAULT_BROWSER_URL,
                )
                if not browser_tabs:
                    # Every entry was rejected. Re-validate the seed so the 400
                    # carries the precise reason (bad scheme, too long, ...)
                    # rather than a generic "needs a URL".
                    _normalize_browser_url(
                        prepared.get("initial_command") or DEFAULT_BROWSER_URL
                    )
                    raise ValueError("Browser panes require an HTTP or HTTPS URL")
                browser_active_tab = _normalize_browser_active_tab(
                    prepared.get("browser_active_tab"), browser_tabs
                )
                prepared["browser_tabs"] = browser_tabs
                prepared["browser_active_tab"] = browser_active_tab
                prepared["initial_command"] = browser_tabs[browser_active_tab]
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
                use_wsl = (
                    os.name == "nt"
                    and bool(prepared.get("use_wsl"))
                    and not use_powershell
                )
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

    return prepared_sessions


def launch_session_group(
    data: Any,
    on_launch_options: Any = None,
) -> Tuple[Dict[str, Any], int]:
    """Launch one session group into a resolved workspace.

    This is the single launch code path: ``POST /api/sessions`` and server-side
    restore both build their panes here, so a restore can never drift from a
    normal launch (it used to be reimplemented in ``buildRestoreGroupBody()``).

    Returns ``(payload, status)``; the caller only serializes it.
    """
    from sessions.manager import SessionStatus
    from web.agents import _sanitize_agent_launch_commands
    from web.app import socketio
    from web.config import runtime_config
    from web.explorer import _is_browser_session, _is_explorer_session
    from web.saved_sessions import (
        DEFAULT_SAVED_SESSION_ID,
        _build_launch_group_id,
        _normalize_connection_mode,
        _normalize_launch_session_id,
        _normalize_layout,
        _normalize_workspace_layout,
        build_unique_session_name,
    )
    from web.terminal_io import (
        _broadcast_session_groups_updated,
        _broadcast_session_status,
        _connect_session,
        _replace_group_sessions,
    )

    session_manager = _manager()
    created_workspace_id = ""
    try:
        if not isinstance(data, dict) or "sessions" not in data:
            logger.warning("Missing 'sessions' in request body")
            return {"error": "Missing 'sessions' in request body"}, 400

        sessions_config = data["sessions"]
        if not isinstance(sessions_config, list) or not sessions_config:
            logger.warning("Empty or invalid sessions list")
            return {"error": "At least one session is required"}, 400

        if len(sessions_config) > runtime_config.max_sessions:
            logger.warning(
                "Too many sessions requested: %d > %d",
                len(sessions_config),
                runtime_config.max_sessions,
            )
            return {
                "error": f"Maximum {runtime_config.max_sessions} sessions allowed"
            }, 400

        connection_mode = _normalize_connection_mode(data.get("connection_mode"))
        layout = _normalize_layout(data.get("layout"), len(sessions_config))
        workspace_layout = _normalize_workspace_layout(
            data.get("workspace_layout"), len(sessions_config)
        )
        session_name = str(data.get("session_name") or "").strip()
        saved_session_id = _normalize_launch_session_id(data.get("saved_session_id"))
        # The built-in "Default Session" is a blank *form*, not a stored preset,
        # so it carries no launch identity. Treating it as one gave every
        # scratch launch the same stable group id, which made a second launch
        # replace the first in place (and 409 across workspaces) — the user
        # could only ever have one session per host or repository open. Scratch
        # launches now always mint a fresh group.
        if saved_session_id == DEFAULT_SAVED_SESSION_ID:
            saved_session_id = ""
        stable_group_id = _build_launch_group_id(saved_session_id) or None

        # Destination and the uniqueness guard are settled first, because
        # `_replace_group_sessions()` below is destructive: replacing before
        # resolving ownership would tear down the *source* workspace's live
        # sessions and only then discover the conflict.
        workspace_id, created_workspace_id = resolve_launch_destination(data)
        conflict = saved_session_conflict(saved_session_id, workspace_id)
        if conflict is not None:
            rollback_created_workspace(created_workspace_id)
            return conflict, 409

        prepared_sessions = _prepare_launch_sessions(sessions_config, connection_mode)

        # A restore replays a workspace the user already had running; a cold
        # post-restart agent probe (status "check_failed") must not silently
        # clear its startup command, which would drop the agent and its
        # auto-mode flag. Skip preflight-clearing on restore and let the pane
        # surface any real launch error itself.
        is_restore = bool(data.get("restore"))
        launch_warnings = (
            []
            if is_restore
            else _sanitize_agent_launch_commands(connection_mode, prepared_sessions)
        )

        if callable(on_launch_options):
            on_launch_options(
                {
                    "connection_mode": connection_mode,
                    "layout": layout,
                    "terminal_count": len(prepared_sessions),
                }
            )

        if stable_group_id:
            _replace_group_sessions(stable_group_id)

        # A restore replays a stored workspace shape: its group name (or a
        # neutral fallback) wins — it must never mint a fresh "Session
        # HH:MM:SS" timestamp name (10.5 hardening). The timestamp fallback is
        # only for genuinely unnamed NEW groups.
        group_name = session_name or (
            "Workspace" if is_restore else f"Session {time.strftime('%H:%M:%S')}"
        )
        # A scratch launch is named after its connection target, so launching
        # the same host or repository twice would produce two identically named
        # tabs. Suffix the repeats instead. A saved preset keeps its own name
        # verbatim (it is live in at most one workspace anyway), and a restore
        # replays a stored shape, so neither is renumbered.
        if not saved_session_id and not is_restore:
            group_name = build_unique_session_name(group_name, _taken_session_names())
        group = session_manager.create_group(
            name=group_name,
            connection_mode=connection_mode,
            layout=layout,
            terminal_count=len(prepared_sessions),
            group_id=stable_group_id,
            saved_session_id=saved_session_id,
            workspace_layout=workspace_layout,
            workspace_id=workspace_id,
        )
        logger.info(
            "Created session group group_id=%s workspace_id=%s saved_session_id=%r "
            "name=%r mode=%s layout=%s terminal_count=%d",
            group.group_id,
            group.workspace_id,
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
        logger.info("Created %d sessions", len(created_sessions))
        if not created_sessions:
            logger.error("No valid sessions were created")
            session_manager.remove_group(group.group_id)
            rollback_created_workspace(created_workspace_id)
            return {"error": "No valid sessions were created"}, 400

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
                session_manager.update_session_status(
                    session.session_id, SessionStatus.CONNECTED
                )
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

        _broadcast_session_groups_updated(
            "launched",
            group_id=group.group_id,
            workspace_id=group.workspace_id,
        )

        return {
            "sessions": [session.to_dict() for session in created_sessions],
            "count": len(created_sessions),
            "group_id": group.group_id,
            "workspace_id": group.workspace_id,
            "workspace_created": bool(created_workspace_id),
            "group": group.to_dict(),
            "layout": layout,
            "connection_mode": connection_mode,
            "terminal_count": len(created_sessions),
            "workspace_layout": workspace_layout,
            "surface_mode": runtime_config.app_surface_mode,
            "launch_target": "web",
            "warnings": launch_warnings,
        }, 201

    except WorkspaceRequestError as exc:
        rollback_created_workspace(created_workspace_id)
        logger.warning("Invalid session launch destination: %s", exc)
        return {"error": str(exc), **exc.payload}, exc.status
    except ValueError as exc:
        rollback_created_workspace(created_workspace_id)
        logger.warning("Invalid session launch request: %s", exc)
        return {"error": str(exc)}, 400
    except Exception as exc:  # pragma: no cover - defensive parity with the route
        rollback_created_workspace(created_workspace_id)
        logger.error("Error creating sessions: %s", exc)
        return {"error": str(exc)}, 500


# ==================== Group moves ====================


def move_group_to_workspace(group_id: str, data: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Move one live group to an existing or new workspace.

    The move is pure ownership + ordering: terminal sessions keep their ids,
    processes, SSH connections, replay buffers, and per-session Socket.IO
    rooms. Both affected windows are notified after every lock is released.
    """
    from web.terminal_io import _broadcast_session_groups_updated

    session_manager = _manager()
    normalized_group_id = str(group_id or "").strip()
    group = session_manager.get_group(normalized_group_id)
    if group is None:
        return {"error": "Session group not found"}, 404

    source_workspace_id = group.workspace_id
    created_workspace_id = ""
    try:
        target_workspace_id, created_workspace_id = resolve_launch_destination(
            {
                "workspace_id": data.get("target_workspace_id", data.get("workspace_id")),
                "new_workspace": data.get("new_workspace"),
                "workspace_label": data.get("label", data.get("workspace_label")),
            }
        )
    except WorkspaceRequestError as exc:
        return {"error": str(exc), **exc.payload}, exc.status

    if target_workspace_id == source_workspace_id:
        rollback_created_workspace(created_workspace_id)
        return {
            "moved": False,
            "group_id": normalized_group_id,
            "workspace_id": source_workspace_id,
            "source_workspace_id": source_workspace_id,
        }, 200

    try:
        moved_group = session_manager.move_group(normalized_group_id, target_workspace_id)
    except ValueError as exc:
        rollback_created_workspace(created_workspace_id)
        return {"error": str(exc)}, 400
    if moved_group is None:
        rollback_created_workspace(created_workspace_id)
        return {"error": "Session group not found"}, 404

    # Snapshot both sides and make the prune decision atomically. The manager
    # uses an RLock, so its helpers may safely re-enter it here. Broadcasts and
    # snapshot cleanup remain below, after the lock is released.
    with session_manager.lock:
        source_groups = [
            item.to_dict()
            for item in session_manager.get_workspace_groups(source_workspace_id)
        ]
        target_groups = [
            item.to_dict()
            for item in session_manager.get_workspace_groups(target_workspace_id)
        ]
        pruned_source = (
            source_workspace_id != DEFAULT_WORKSPACE_ID
            and not source_groups
            and session_manager.remove_workspace(source_workspace_id)
        )
    if pruned_source:
        # The source workspace no longer exists; its saved snapshot goes with it
        # so the restore chooser cannot offer a workspace the launcher no longer
        # lists.
        forget_pruned_workspaces([source_workspace_id])
    else:
        # `default` is never pruned, but moving its last group out leaves its
        # snapshot describing a workspace that is now empty.
        forget_emptied_default_workspace(source_workspace_id)

    _broadcast_session_groups_updated(
        "moved",
        group_id=normalized_group_id,
        workspace_id=source_workspace_id,
    )
    _broadcast_session_groups_updated(
        "moved",
        group_id=normalized_group_id,
        workspace_id=target_workspace_id,
    )
    logger.debug(
        "Moved group %s from workspace %s to %s (source pruned=%s)",
        normalized_group_id,
        source_workspace_id,
        target_workspace_id,
        bool(pruned_source),
    )

    return {
        "moved": True,
        "group_id": normalized_group_id,
        "group": moved_group.to_dict(),
        "workspace_id": target_workspace_id,
        "workspace_created": bool(created_workspace_id),
        "source_workspace_id": source_workspace_id,
        "source_workspace_pruned": bool(pruned_source),
        "source_groups": source_groups,
        "groups": target_groups,
    }, 200


# ==================== Server-side restore ====================
#
# The launcher used to rebuild every restored pane in JavaScript
# (`buildRestoreGroupBody()`), which meant the restore path drifted from the
# launch path *and* required the backend to hand a decrypted saved-session
# password to the browser (ISSUE-2026-037). Restore now resolves the preset and
# its credential in-process and relaunches through `launch_session_group()`.


def _directory_is_absolute(path: str, mode: str) -> bool:
    """Mirror of ``isAbsoluteDirectory`` in web/static/js/shared.js."""
    trimmed = str(path or "").strip()
    if not trimmed:
        return False
    if re.match(r"^[A-Za-z]:[\\/]", trimmed):
        return True
    if trimmed.startswith("\\\\") or trimmed.startswith("/") or trimmed.startswith("~"):
        return True
    return mode == "wsl" and trimmed.startswith("\\")


def _join_directories(base_dir: str, child_dir: str, mode: str) -> str:
    """Mirror of ``joinDirectories`` in web/static/js/shared.js."""
    raw_base = str(base_dir or "").strip()
    base = raw_base if raw_base == "/" else raw_base.rstrip("\\/")
    child = str(child_dir or "").strip().lstrip("\\/")
    if not base:
        return child
    if not child:
        return base
    separator = "\\" if mode == "wsl" and "\\" in base and "/" not in base else "/"
    normalized_child = re.sub(r"[\\/]+", separator, child)
    return f"{base}{separator}{normalized_child}"


def _launch_directory(default_dir: str, terminal_dir: str, mode: str) -> str:
    """Mirror of ``buildLaunchDirectory`` in web/static/js/shared.js.

    A restore must resolve pane directories exactly the way the launcher does,
    or the same preset would land in different directories depending on which
    path opened it.
    """
    base_directory = str(default_dir or "").strip()
    raw_directory = str(terminal_dir or "").strip()
    if not raw_directory:
        return base_directory
    if not base_directory:
        return raw_directory
    if not _directory_is_absolute(raw_directory, mode):
        return _join_directories(base_directory, raw_directory, mode)
    return raw_directory


def build_sessions_from_saved_config(
    config: Dict[str, Any],
    count: int,
) -> List[Dict[str, Any]]:
    """Expand one saved preset config into `POST /api/sessions` pane entries.

    The server-side twin of ``buildSessionsFromConfig()`` in launcher.js, used
    only by restore. Both feed the same launch service, so a restored pane and
    a launched pane are built from the same fields.
    """
    connection_mode = str(config.get("connection_mode") or "ssh")
    ssh_config = config.get("ssh") if isinstance(config.get("ssh"), dict) else {}
    wsl_config = config.get("wsl") if isinstance(config.get("wsl"), dict) else {}
    configured_default_dir = str(
        (wsl_config if connection_mode == "wsl" else ssh_config).get("default_dir") or ""
    ).strip()
    launch_default_dir = configured_default_dir or ("/" if connection_mode == "ssh" else "")

    sessions: List[Dict[str, Any]] = []
    terminals = config.get("terminals") if isinstance(config.get("terminals"), list) else []
    for index, terminal in enumerate(terminals[: max(0, int(count or 0))]):
        if not isinstance(terminal, dict):
            continue
        startup_mode = str(terminal.get("startup_mode") or "terminal")
        shell_flags_allowed = startup_mode not in {"explorer", "browser"}
        directory = _launch_directory(
            configured_default_dir,
            terminal.get("directory"),
            connection_mode,
        ) or launch_default_dir
        common = {
            "title": terminal.get("title") or f"Terminal {index + 1}",
            "directory": directory,
            "initial_command": None if startup_mode == "explorer" else (terminal.get("initial_command") or None),
            "initial_command_mode": terminal.get("initial_command_mode") or "command",
            "startup_mode": startup_mode,
            "agent_selection": terminal.get("agent_selection") or "",
            "custom_agent": terminal.get("custom_agent") or "",
            "agent_auto_mode": bool(terminal.get("agent_auto_mode")),
            "explorer_tree_open": bool(terminal.get("explorer_tree_open")),
            "explorer_git_open": bool(terminal.get("explorer_git_open")),
            "explorer_search_open": bool(terminal.get("explorer_search_open")),
            "explorer_open_tabs": list(terminal.get("explorer_open_tabs") or []),
            "explorer_active_tab": terminal.get("explorer_active_tab") or "",
            "explorer_tab_views": dict(terminal.get("explorer_tab_views") or {}),
            "explorer_md_preset": terminal.get("explorer_md_preset") or "",
            "explorer_md_font": terminal.get("explorer_md_font") or "",
            "explorer_source_font": terminal.get("explorer_source_font") or "",
            "explorer_theme": terminal.get("explorer_theme") or "dark",
            "browser_tabs": list(terminal.get("browser_tabs") or []),
            "browser_active_tab": int(terminal.get("browser_active_tab") or 0),
        }
        if connection_mode == "ssh":
            sessions.append(
                {
                    **common,
                    "host": ssh_config.get("host") or "",
                    "username": ssh_config.get("username") or "ubuntu",
                    # Resolved in-process: the decrypted credential goes
                    # straight into the launch service and never into a
                    # response body, a snapshot, or the log.
                    "password": ssh_config.get("password") or None,
                    "port": ssh_config.get("port") or 22,
                }
            )
            continue
        sessions.append(
            {
                **common,
                "distribution": terminal.get("distribution") or wsl_config.get("distribution") or "",
                "username": wsl_config.get("username") or "",
                "use_wsl": shell_flags_allowed and bool(terminal.get("use_wsl")),
                "use_powershell": shell_flags_allowed and bool(terminal.get("use_powershell")),
            }
        )
    return sessions


def _find_saved_preset(saved_session_id: str) -> Optional[Dict[str, Any]]:
    """Return one saved preset (credentials decrypted in-process) or ``None``."""
    from web.saved_sessions import load_saved_sessions

    normalized = str(saved_session_id or "").strip()
    if not normalized:
        return None
    for entry in load_saved_sessions():
        if entry.get("id") == normalized:
            return entry
    return None


def _restore_group_request(
    snapshot_group: Dict[str, Any],
    workspace_id: str,
) -> Tuple[Dict[str, Any], str]:
    """Build one group's launch body; returns ``(body, warning)``.

    Latest preset wins (R2): a still-existing named preset is replayed from its
    *current* config, so Save All Sessions followed by Save Workspace restores
    the edited state. A deleted preset (R1) degrades to the password-free
    snapshot with a `preset_missing` warning rather than failing the workspace.
    """
    snapshot_sessions = [
        session
        for session in (snapshot_group.get("sessions") or [])
        if isinstance(session, dict)
    ]
    body = {
        "sessions": snapshot_sessions,
        "connection_mode": snapshot_group.get("connection_mode"),
        "layout": snapshot_group.get("layout"),
        "workspace_layout": snapshot_group.get("workspace_layout"),
        "session_name": snapshot_group.get("name"),
        "saved_session_id": snapshot_group.get("saved_session_id") or "",
        "workspace_id": workspace_id,
        # Replay verbatim: a cold post-restart agent probe must not silently
        # clear a startup command that was working before the restart.
        "restore": True,
    }

    from web.saved_sessions import DEFAULT_SAVED_SESSION_ID

    saved_session_id = str(snapshot_group.get("saved_session_id") or "").strip()
    # The blank built-in default holds no real host/directory, so it is never
    # treated as a preset a restore should prefer — nor as one that went
    # missing when it is not in saved_sessions.json.
    if not saved_session_id or saved_session_id == DEFAULT_SAVED_SESSION_ID:
        return body, ""

    preset = _find_saved_preset(saved_session_id)
    if preset is None:
        return body, "preset_missing"

    config = preset.get("config") if isinstance(preset.get("config"), dict) else None
    if not config or not isinstance(config.get("terminals"), list):
        return body, "preset_missing"

    preset_sessions = build_sessions_from_saved_config(
        config, config.get("terminal_count") or len(snapshot_sessions)
    )
    if not preset_sessions:
        return body, "preset_missing"

    body["sessions"] = preset_sessions
    body["connection_mode"] = config.get("connection_mode")
    body["layout"] = config.get("layout")
    body["session_name"] = preset.get("name") or snapshot_group.get("name")
    # R3: a stored layout array is sized for the pane count it was captured
    # with. Reusing it against a preset that has since gained or lost a pane is
    # the classic source of a broken grid, so it is dropped rather than scaled.
    body["workspace_layout"] = (
        config.get("workspace_layout")
        if len(preset_sessions) != len(snapshot_sessions)
        else (snapshot_group.get("workspace_layout") or config.get("workspace_layout"))
    )
    return body, ""


def restore_workspace(workspace_id: Any) -> Dict[str, Any]:
    """Restore one saved workspace slot in place; returns a per-group report."""
    from web.runtime_state import load_restorable_workspace

    session_manager = _manager()
    try:
        resolved_workspace_id = normalize_workspace_id(workspace_id)
    except ValueError:
        return {
            "workspace_id": str(workspace_id or ""),
            "restored": False,
            "reason": "invalid_workspace_id",
            "groups": [],
        }

    slot = load_restorable_workspace(resolved_workspace_id)
    if not slot:
        return {
            "workspace_id": resolved_workspace_id,
            "restored": False,
            "reason": "not_found",
            "groups": [],
        }

    label = str(slot.get("label") or "").strip()
    # R5/R12: idempotent by workspace id. A second restore (double click, two
    # launcher windows) sees the workspace live and refuses instead of
    # duplicating every tab.
    if workspace_has_groups(resolved_workspace_id):
        return {
            "workspace_id": resolved_workspace_id,
            "label": label or workspace_label(resolved_workspace_id),
            "restored": False,
            "reason": "already_live",
            "groups": [],
        }

    created_workspace = False
    if session_manager.get_workspace(resolved_workspace_id) is None:
        # The slot's *exact* id is reused so a later autosave refreshes this
        # slot instead of growing a second one for the same workspace.
        session_manager.create_workspace(
            label=label,
            workspace_id=resolved_workspace_id,
            retain_when_empty=True,
        )
        created_workspace = True
    elif label:
        session_manager.rename_workspace(resolved_workspace_id, label)

    group_results: List[Dict[str, Any]] = []
    started_group_ids: List[str] = []
    active_snapshot_group_id = str(slot.get("active_group_id") or "").strip()
    active_group_id = ""

    for snapshot_group in slot.get("groups") or []:
        if not isinstance(snapshot_group, dict):
            continue
        snapshot_group_id = str(snapshot_group.get("group_id") or "")
        name = str(snapshot_group.get("name") or "")
        saved_session_id = str(snapshot_group.get("saved_session_id") or "").strip()

        # R6: the preset is already live in a sibling workspace. Skip this one
        # group and keep restoring the rest of the workspace.
        conflict = saved_session_conflict(saved_session_id, resolved_workspace_id)
        if conflict is not None:
            group_results.append(
                {
                    "snapshot_group_id": snapshot_group_id,
                    "name": name,
                    "started": False,
                    "skipped": "already_live",
                    "workspace_id": conflict["workspace_id"],
                    "workspace_label": conflict["workspace_label"],
                }
            )
            continue

        body, warning = _restore_group_request(snapshot_group, resolved_workspace_id)
        payload, status = launch_session_group(body)
        started = status == 201
        result = {
            "snapshot_group_id": snapshot_group_id,
            "name": name,
            "started": started,
            "warning": warning,
        }
        if started:
            result["group_id"] = payload.get("group_id", "")
            result["pane_count"] = payload.get("count", 0)
            started_group_ids.append(result["group_id"])
            if snapshot_group_id and snapshot_group_id == active_snapshot_group_id:
                active_group_id = result["group_id"]
        else:
            # R7: a per-group failure is reported and retryable; it never
            # becomes a failed workspace or a 500.
            result["error"] = str(payload.get("error") or "Relaunch failed")
        group_results.append(result)

    if not started_group_ids:
        # A failed restore must not leave a live-but-empty workspace behind, or
        # the retry would hit `already_live` against a workspace with nothing
        # in it.
        if created_workspace:
            session_manager.remove_workspace(resolved_workspace_id)
        return {
            "workspace_id": resolved_workspace_id,
            "label": label,
            "restored": False,
            "reason": "no_groups_started",
            "groups": group_results,
        }

    # R8: the saved front-group hint only survives if that group actually
    # started; otherwise fall back to the first group that did.
    if not active_group_id:
        active_group_id = started_group_ids[0]
    session_manager.set_active_group(resolved_workspace_id, active_group_id)

    return {
        "workspace_id": resolved_workspace_id,
        "label": label,
        "restored": True,
        "reason": "",
        "active_group_id": active_group_id,
        "native_zoom_factor": slot.get("native_zoom_factor"),
        "group_count": len(started_group_ids),
        "groups": group_results,
    }


def restore_workspaces(workspace_ids: Any) -> Tuple[Dict[str, Any], int]:
    """Restore a subset of saved workspaces; returns ``(payload, status)``.

    Unselected slots are untouched and stay on offer. The response reports that
    a relaunch *started* — SSH authentication is asynchronous and keeps
    arriving through the existing room-scoped `session_status` events.
    """
    if not isinstance(workspace_ids, list) or not workspace_ids:
        return {"error": "A non-empty 'workspace_ids' list is required"}, 400

    seen = set()
    results = []
    for raw_id in workspace_ids:
        key = str(raw_id or "")
        if key in seen:
            continue
        seen.add(key)
        results.append(restore_workspace(raw_id))

    return {
        "message": "Relaunch started",
        "workspaces": results,
        "restored_count": sum(1 for result in results if result["restored"]),
    }, 200


def list_restorable_workspace_summaries() -> List[Dict[str, Any]]:
    """Return saved-slot summaries plus a live-conflict flag, no launch config."""
    from web.runtime_state import list_restorable_workspaces

    summaries = []
    for summary in list_restorable_workspaces():
        live_conflict = workspace_has_groups(summary["workspace_id"])
        summaries.append({**summary, "live_conflict": live_conflict})
    return summaries


# ==================== Leaving multi-workspace mode ====================


def close_extra_workspaces() -> Dict[str, Any]:
    """Close every live workspace except ``default``: sessions, then the record.

    This is what switching ``workspace.multi_workspace_enabled`` off does to the
    workspaces the feature created — with the flag off there is no window that
    could reach them, so leaving them running would strand live shells.

    Nothing is captured here on purpose: a snapshot written *during* a teardown
    would race the very state it is trying to record. Restorability comes from
    whatever the autosave timer or an explicit Workspace ▸ Save Workspace has
    already written, and those slots survive this untouched.
    """
    from web.terminal_io import (
        _broadcast_session_groups_updated,
        _close_ssh_connection,
    )

    session_manager = _manager()
    closed: List[Dict[str, Any]] = []

    for workspace in session_manager.get_all_workspaces():
        workspace_id = workspace.workspace_id
        if workspace_id == DEFAULT_WORKSPACE_ID:
            continue

        # Check-then-act in one hold; the slow work (SSH teardown) and the emit
        # both happen after the lock is released (guardrail 2).
        with session_manager.lock:
            session_ids = [
                session.session_id
                for session in session_manager.get_workspace_sessions(workspace_id)
            ]
            group_ids = [
                group.group_id
                for group in session_manager.get_workspace_groups(workspace_id)
            ]
            for session_id in session_ids:
                session_manager.close_session(session_id)
            # Leaving the mode is explicit, so these groups must not survive on
            # the empty-group grace period the way a transient empty one does.
            session_manager.clear_disconnected_sessions(force_group_ids=set(group_ids))
            # A workspace created deliberately empty is retained until its first
            # group arrives; that promise ends with the mode itself.
            workspace.retain_when_empty = False
            removed = session_manager.remove_workspace(workspace_id)

        for session_id in session_ids:
            _close_ssh_connection(session_id, clear_buffer=True)
        _broadcast_session_groups_updated(
            "workspace_closed",
            workspace_id=workspace_id,
        )
        logger.debug(
            "Closed workspace %s leaving multi-workspace mode "
            "(groups=%d sessions=%d removed=%s)",
            workspace_id,
            len(group_ids),
            len(session_ids),
            bool(removed),
        )
        closed.append({
            "workspace_id": workspace_id,
            "label": str(workspace.label or "").strip(),
            "group_count": len(group_ids),
            "session_count": len(session_ids),
        })

    return {
        "closed": closed,
        "closed_count": len(closed),
    }
