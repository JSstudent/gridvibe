"""Shared close/restart preparation and live-window flush coordination.

Voluntary application close and restart use the same transaction contract:
flush every connected workspace window, optionally save reusable presets,
capture all live workspace slots once, and authorize teardown only after every
requested step succeeded.  This module is Flask- and Socket.IO-independent so
the route and native bridge share one decision registry without an import
cycle.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from web.runtime_state import (
    RuntimeStatePersistenceError,
    capture_live_workspaces,
    capture_workspace,
    normalize_native_zoom_factor,
)
from web.saved_sessions import (
    _find_saved_session_entry,
    _load_saved_sessions_payload,
    _merge_workspace_session_config,
    _normalize_session_config,
    build_live_session_view_updates,
    build_unique_session_name,
    upsert_saved_session,
)

LIFECYCLE_ACTIONS = frozenset({"close", "restart"})
LIFECYCLE_SAVE_NONE = "none"
LIFECYCLE_SAVE_WORKSPACES = "workspaces"
LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES = "sessions+workspaces"
LIFECYCLE_SAVE_CHOICES = frozenset(
    {
        LIFECYCLE_SAVE_NONE,
        LIFECYCLE_SAVE_WORKSPACES,
        LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES,
    }
)
LIFECYCLE_FLUSH_TIMEOUT_SECONDS = 5.0
LIFECYCLE_DECISION_TTL_SECONDS = 60.0
# A socket that died without `pagehide` (crash, kill, network partition, a
# suspended laptop) leaves its window registered as stale. That record must
# block the flush while the loss is fresh — it may be a real window about to
# reconnect — but a window gone for minutes is departed, not busy: past the
# grace period its record is dropped instead of blocking every later save.
LIFECYCLE_STALE_WINDOW_GRACE_SECONDS = 120.0
_MAX_DECISIONS = 128
_MAX_CLIENT_ERROR_LENGTH = 300
_MAX_WINDOW_ID_LENGTH = 128
# Safety net behind the grace period and the stable per-window id: even if both
# are somehow bypassed, one workspace can never accumulate window records
# without bound. Generous enough that real multi-window use never reaches it.
_MAX_WINDOWS_PER_WORKSPACE = 16

logger = logging.getLogger(__name__)


class LifecycleValidationError(ValueError):
    """Raised when a lifecycle action or client metadata is invalid."""


class LifecycleCoordinator:
    """Track workspace-window flushes and one-use teardown decisions."""

    def __init__(
        self,
        stale_window_grace_seconds: float = LIFECYCLE_STALE_WINDOW_GRACE_SECONDS,
    ):
        self._condition = threading.Condition(threading.Lock())
        self._client_windows: Dict[str, Set[str]] = {}
        self._windows: Dict[str, Dict[str, Any]] = {}
        self._flushes: Dict[str, Dict[str, Any]] = {}
        self._decisions: Dict[str, Tuple[str, float]] = {}
        self._stale_window_grace = max(0.0, float(stale_window_grace_seconds))
        self._join_sequence = 0

    def reset(self):
        """Clear transient state. Intended for tests and process reconfiguration."""
        with self._condition:
            self._client_windows.clear()
            self._windows.clear()
            self._flushes.clear()
            self._decisions.clear()
            self._condition.notify_all()

    def join_workspace(
        self, client_id: Any, workspace_id: Any, window_id: Any = None
    ):
        client = str(client_id or "").strip()
        workspace = str(workspace_id or "").strip()
        window = str(window_id or client).strip()
        if len(window) > _MAX_WINDOW_ID_LENGTH:
            window = client
        if not client or not workspace or not window:
            return
        with self._condition:
            # A window id is stable per window (the page keeps it in
            # sessionStorage), so a reload rejoins with its own id and replaces
            # its stale record even when `pagehide` never fired.
            previous = self._windows.get(window)
            if previous is not None:
                previous_client = previous["client_id"]
                previous_windows = self._client_windows.get(previous_client)
                if previous_windows is not None:
                    previous_windows.discard(window)
                    if not previous_windows:
                        self._client_windows.pop(previous_client, None)
                logger.debug(
                    "Lifecycle window rejoined workspace=%s, replacing its own record",
                    workspace,
                )
            self._join_sequence += 1
            self._windows[window] = {
                "client_id": client,
                "workspace_id": workspace,
                "connected": True,
                "disconnected_at": None,
                "joined_at": self._join_sequence,
            }
            self._client_windows.setdefault(client, set()).add(window)
            self._enforce_window_bound_locked(workspace)

    def leave_workspace(self, client_id: Any, workspace_id: Any = None):
        client = str(client_id or "").strip()
        workspace = str(workspace_id or "").strip()
        if not client:
            return
        with self._condition:
            joined = set(self._client_windows.get(client) or set())
            departed: Set[str] = set()
            for window in joined:
                record = self._windows.get(window)
                if record is None:
                    continue
                if workspace and record["workspace_id"] != workspace:
                    continue
                self._windows.pop(window, None)
                self._client_windows.get(client, set()).discard(window)
                departed.add(window)
            if not self._client_windows.get(client):
                self._client_windows.pop(client, None)
            self._drop_pending_windows_locked(departed)
            self._condition.notify_all()

    def disconnect_client(self, client_id: Any):
        """Mark the client's windows stale; they stay recoverable, not permanent.

        The record is kept so a genuinely unreachable window is still reported
        (`client_stale`) while the loss is fresh, but it carries the disconnect
        time so :meth:`request_flush` can treat it as departed once the grace
        period has passed rather than blocking every later save-on-exit.
        """
        client = str(client_id or "").strip()
        if not client:
            return
        with self._condition:
            now = time.monotonic()
            stale: Set[str] = set()
            for window in self._client_windows.pop(client, set()):
                record = self._windows.get(window)
                if record is not None:
                    record["connected"] = False
                    record["disconnected_at"] = now
                    stale.add(window)
            self._stale_pending_windows_locked(stale)
            self._condition.notify_all()

    def connected_window_count(self, workspace_id: Any) -> int:
        """How many windows of one workspace can currently answer a flush.

        Departed records (disconnected past the grace period) are dropped on
        the way through, so the count never includes a window that could not
        be reached anyway.
        """
        workspace = str(workspace_id or "").strip()
        if not workspace:
            return 0
        with self._condition:
            self._drop_departed_windows_locked(time.monotonic())
            return sum(
                1
                for record in self._windows.values()
                if record["workspace_id"] == workspace and record["connected"]
            )

    def _drop_window_locked(self, window_id: str, record: Dict[str, Any]):
        """Remove one window record and its client back-reference."""
        self._windows.pop(window_id, None)
        client = record.get("client_id")
        joined = self._client_windows.get(client)
        if joined is not None:
            joined.discard(window_id)
            if not joined:
                self._client_windows.pop(client, None)

    def _stale_pending_windows_locked(self, window_ids: Set[str]):
        """Answer a flush in flight for windows that just went stale.

        ``request_flush`` decides its expected, pre-acknowledged, and emit sets
        in one hold, but a window can still drop *after* that snapshot. Such a
        window was emitted to and can never acknowledge, so without this the
        flush waits out its whole timeout and then reports ``client_timeout``
        for a loss the coordinator already knows about. Pre-acknowledging it
        with the same ``client_stale`` category ``request_flush`` uses for an
        already-disconnected window keeps the outcome identical, accurate, and
        immediate.
        """
        if not window_ids:
            return
        for pending in self._flushes.values():
            dropped = {
                key
                for key in pending["expected"] - pending["acknowledged"]
                if key[0] in window_ids
            }
            if not dropped:
                continue
            pending["acknowledged"].update(dropped)
            pending["errors"].extend(
                {
                    "workspace_id": workspace_id,
                    "category": "client_stale",
                    "error": "A workspace window is disconnected and cannot flush",
                }
                for _window_id, workspace_id in sorted(dropped)
            )

    def _drop_pending_windows_locked(self, window_ids: Set[str]):
        """Release a flush in flight from a window that has left the workspace.

        A window that left before the flush was never in ``expected`` at all,
        so one that leaves during the flush is forgotten the same way rather
        than reported: a deliberate departure is not a failed save.
        """
        if not window_ids:
            return
        for pending in self._flushes.values():
            departed = {key for key in pending["expected"] if key[0] in window_ids}
            if not departed:
                continue
            pending["expected"].difference_update(departed)
            pending["acknowledged"].difference_update(departed)
            for key in departed:
                pending["clients"].pop(key, None)
                pending["joined"].pop(key, None)

    def _drop_departed_windows_locked(self, now: float):
        """Forget windows whose disconnect outlived the grace period."""
        departed = [
            (window_id, record)
            for window_id, record in self._windows.items()
            if not record["connected"]
            and record["disconnected_at"] is not None
            and now - record["disconnected_at"] >= self._stale_window_grace
        ]
        for window_id, record in departed:
            logger.debug(
                "Lifecycle window departed past grace workspace=%s",
                record["workspace_id"],
            )
            self._drop_window_locked(window_id, record)

    def _enforce_window_bound_locked(self, workspace_id: str):
        """Cap one workspace's window records, evicting the least useful first.

        Disconnected records go before connected ones, oldest first within each
        kind. Reaching the cap with every window connected means the stable-id
        and grace mechanisms were both bypassed; losing the oldest record's
        flush tracking is still better than unbounded growth.
        """
        records = [
            (window_id, record)
            for window_id, record in self._windows.items()
            if record["workspace_id"] == workspace_id
        ]
        excess = len(records) - _MAX_WINDOWS_PER_WORKSPACE
        if excess <= 0:
            return
        records.sort(key=lambda item: (item[1]["connected"], item[1]["joined_at"]))
        for window_id, record in records[:excess]:
            self._drop_window_locked(window_id, record)
        logger.debug(
            "Lifecycle window bound reached workspace=%s evicted=%d",
            workspace_id,
            excess,
        )

    def acknowledge_flush(self, client_id: Any, data: Any) -> bool:
        """Accept one Socket.IO acknowledgement from its authenticated sid."""
        if not isinstance(data, dict):
            return False
        client = str(client_id or "").strip()
        request_id = str(data.get("request_id") or "").strip()
        workspace_id = str(data.get("workspace_id") or "").strip()
        with self._condition:
            pending = self._flushes.get(request_id)
            if pending is None:
                return False
            matching = {
                key
                for key, expected_client in pending["clients"].items()
                if expected_client == client and key[1] == workspace_id
            }
            if not matching:
                return False
            if matching.issubset(pending["acknowledged"]):
                return True
            pending["acknowledged"].update(matching)
            if data.get("ok") is True:
                metadata = data.get("metadata")
                # Carry the acknowledging window's join order alongside its
                # chrome so `request_flush` can hand the records back oldest
                # window first; that ordering is what makes a disagreement
                # between two windows resolvable instead of fatal.
                joined_at = max(
                    (pending["joined"].get(key, 0) for key in matching),
                    default=0,
                )
                pending["metadata"].setdefault(workspace_id, []).append(
                    (joined_at, metadata if isinstance(metadata, dict) else {})
                )
            else:
                error = str(data.get("error") or "Presentation flush failed")
                pending["errors"].append(
                    {
                        "workspace_id": workspace_id,
                        "category": "client_flush",
                        "error": error[:_MAX_CLIENT_ERROR_LENGTH],
                    }
                )
            self._condition.notify_all()
            return True

    def request_flush(
        self,
        workspace_ids: Iterable[str],
        emit_request: Callable[[str, str], None],
        timeout: float = LIFECYCLE_FLUSH_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Ask every currently joined window to flush, then wait boundedly.

        Each workspace's returned ``metadata`` list is ordered oldest-joined
        window first, so a caller resolving window chrome can apply
        last-writer-wins without knowing anything about the window registry.
        """
        live_ids = {str(value or "").strip() for value in workspace_ids}
        live_ids.discard("")
        request_id = uuid.uuid4().hex
        with self._condition:
            # A window disconnected past the grace period is departed: drop its
            # record instead of counting it in `expected`, or one abnormally
            # lost window would block save-on-exit for the rest of the process.
            self._drop_departed_windows_locked(time.monotonic())
            expected = {
                (window_id, record["workspace_id"])
                for window_id, record in self._windows.items()
                if record["workspace_id"] in live_ids
            }
            if not expected:
                return {
                    "ok": True,
                    "request_id": request_id,
                    "metadata": {},
                    "errors": [],
                    "missing_workspaces": [],
                }
            pending = {
                "expected": expected,
                "acknowledged": {
                    key
                    for key in expected
                    if not self._windows[key[0]]["connected"]
                },
                "clients": {
                    key: self._windows[key[0]]["client_id"] for key in expected
                },
                "joined": {
                    key: self._windows[key[0]]["joined_at"] for key in expected
                },
                "metadata": {},
                "errors": [
                    {
                        "workspace_id": workspace_id,
                        "category": "client_stale",
                        "error": "A workspace window is disconnected and cannot flush",
                    }
                    for window_id, workspace_id in expected
                    if not self._windows[window_id]["connected"]
                ],
            }
            self._flushes[request_id] = pending
            # The emit set is decided in the *same* hold that built `expected`
            # and the pre-acknowledged set, so the two can never disagree. Read
            # outside the lock, a window that dropped in between was neither
            # emitted to nor pre-acknowledged, so its flush could only end in a
            # full timeout reported as `client_timeout` instead of the accurate
            # `client_stale`.
            connected_workspaces = {
                workspace
                for window, workspace in expected
                if self._windows[window]["connected"]
            }

        # Room emits are deliberately outside the coordinator lock and outside
        # SessionManager.lock. A slow transport must never stall shared state.
        for workspace_id in sorted(connected_workspaces):
            try:
                emit_request(workspace_id, request_id)
            except Exception:
                with self._condition:
                    current = self._flushes.get(request_id)
                    if current is not None:
                        failed = {
                            key for key in current["expected"] if key[1] == workspace_id
                        }
                        current["acknowledged"].update(failed)
                        current["errors"].append(
                            {
                                "workspace_id": workspace_id,
                                "category": "client_emit",
                                "error": "Could not request the workspace flush",
                            }
                        )
                        self._condition.notify_all()

        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            pending = self._flushes.get(request_id)
            while pending is not None and pending["acknowledged"] != pending["expected"]:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
                pending = self._flushes.get(request_id)
            pending = self._flushes.pop(request_id, pending)

        if pending is None:
            return {
                "ok": False,
                "request_id": request_id,
                "metadata": {},
                "errors": [{"category": "client_flush", "error": "Flush was cancelled"}],
                "missing_workspaces": sorted(live_ids),
            }
        missing = pending["expected"] - pending["acknowledged"]
        missing_workspaces = sorted({workspace for _client, workspace in missing})
        errors = list(pending["errors"])
        errors.extend(
            {
                "workspace_id": workspace_id,
                "category": "client_timeout",
                "error": "The workspace window did not acknowledge its flush in time",
            }
            for workspace_id in missing_workspaces
        )
        return {
            "ok": not errors,
            "request_id": request_id,
            "metadata": {
                workspace_id: [
                    record
                    for _joined_at, record in sorted(
                        records, key=lambda entry: entry[0]
                    )
                ]
                for workspace_id, records in pending["metadata"].items()
            },
            "errors": errors,
            "missing_workspaces": missing_workspaces,
        }

    def issue_decision(self, action: str) -> str:
        if action not in LIFECYCLE_ACTIONS:
            raise LifecycleValidationError("Unknown lifecycle action")
        now = time.monotonic()
        token = secrets.token_urlsafe(24)
        with self._condition:
            self._prune_decisions_locked(now)
            self._decisions[token] = (action, now + LIFECYCLE_DECISION_TTL_SECONDS)
            while len(self._decisions) > _MAX_DECISIONS:
                oldest = min(self._decisions, key=lambda item: self._decisions[item][1])
                self._decisions.pop(oldest, None)
        return token

    def consume_decision(self, token: Any, action: str) -> bool:
        candidate = str(token or "").strip()
        now = time.monotonic()
        with self._condition:
            self._prune_decisions_locked(now)
            decision = self._decisions.get(candidate)
            if decision is None or decision[0] != action:
                return False
            self._decisions.pop(candidate, None)
            return True

    def _prune_decisions_locked(self, now: float):
        expired = [token for token, (_action, expiry) in self._decisions.items() if expiry <= now]
        for token in expired:
            self._decisions.pop(token, None)


lifecycle_coordinator = LifecycleCoordinator()


def _live_group_config(group: Dict[str, Any]) -> Dict[str, Any]:
    """Build one server-only reusable-preset candidate from a live group."""
    panes = [pane for pane in group.get("sessions") or [] if isinstance(pane, dict)]
    first = panes[0] if panes else {}
    connection_mode = "wsl" if group.get("connection_mode") == "wsl" else "ssh"
    terminals = []
    terminal_fields = (
        "session_id",
        "title",
        "directory",
        "initial_command",
        "initial_command_mode",
        "startup_mode",
        "agent_selection",
        "custom_agent",
        "agent_auto_mode",
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
        "distribution",
        "use_wsl",
        "use_powershell",
    )
    for pane in panes:
        terminals.append({field: pane.get(field) for field in terminal_fields})
    return {
        "connection_mode": connection_mode,
        "terminal_count": len(panes),
        "layout": group.get("layout"),
        "workspace_layout": group.get("workspace_layout"),
        "ssh": {
            "host": first.get("host"),
            "username": first.get("username"),
            "password": first.get("password") or "",
            "port": first.get("port"),
            "default_dir": first.get("directory"),
        },
        "wsl": {
            "distribution": first.get("distribution"),
            "username": first.get("username"),
            "default_dir": first.get("directory"),
        },
        "terminals": terminals,
    }


def normalize_workspace_metadata(
    metadata_by_workspace: Any,
    live_snapshots: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Validate and resolve the window chrome reported by a workspace's windows.

    Two windows on one workspace legitimately disagree — the front session tab
    is per window, and a top-bar toggle in one is not pushed to the other — so a
    disagreement is resolved, never refused: **a workspace's chrome is whichever
    window most recently joined.** Records for one workspace arrive ordered
    oldest-joined first (:meth:`LifecycleCoordinator.request_flush`), and each
    field is applied last-writer-wins, so the newest window supplying a field
    owns it. This is product decision 7's compare-and-swap ordering applied to
    chrome, and Stage 6's *launchable shape fails; window chrome degrades*
    boundary applied where it belongs: refusing here costs the whole save and
    leaves "continue without saving" as the only escape.

    An ``active_group_id`` naming no live group is a window that has not yet
    processed a close from a sibling window, not a corrupt payload: that one
    field is dropped and the capture falls back to the server's own hint, which
    :meth:`RuntimeStateStore._build_slot` re-validates anyway.

    Malformed *types* — a non-boolean ``topbar_visible``, an out-of-range native
    zoom — still raise: those indicate a broken client, not a disagreement.
    """
    if not isinstance(metadata_by_workspace, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for workspace_id, raw_records in metadata_by_workspace.items():
        snapshot = live_snapshots.get(workspace_id)
        if snapshot is None:
            continue
        records = raw_records if isinstance(raw_records, list) else [raw_records]
        valid_group_ids = {
            str(group.get("group_id") or "") for group in snapshot.get("groups") or []
        }
        resolved: Dict[str, Any] = {}
        disagreeing_fields: Set[str] = set()
        stale_active_groups = 0
        for raw in records:
            if not isinstance(raw, dict):
                raw = {}
            candidate: Dict[str, Any] = {}
            if "active_group_id" in raw:
                active_group_id = str(raw.get("active_group_id") or "").strip()
                if active_group_id and active_group_id not in valid_group_ids:
                    stale_active_groups += 1
                else:
                    candidate["active_group_id"] = active_group_id
            if "topbar_visible" in raw:
                if not isinstance(raw.get("topbar_visible"), bool):
                    raise LifecycleValidationError(
                        f"Workspace {workspace_id} reported invalid top-bar state"
                    )
                candidate["topbar_visible"] = raw["topbar_visible"]
            if "native_zoom_factor" in raw and raw.get("native_zoom_factor") is not None:
                zoom = normalize_native_zoom_factor(raw.get("native_zoom_factor"))
                if zoom is None:
                    raise LifecycleValidationError(
                        f"Workspace {workspace_id} reported invalid native zoom"
                    )
                candidate["native_zoom_factor"] = zoom
            for field, value in candidate.items():
                if field in resolved and resolved[field] != value:
                    disagreeing_fields.add(field)
            resolved.update(candidate)
        if disagreeing_fields or stale_active_groups:
            # Shape only: the workspace id, how many windows answered, which
            # fields disagreed, and how many stale tab hints were dropped.
            logger.debug(
                "Workspace %s chrome resolved to the newest window: "
                "windows=%d disagreed=%s stale_active_group=%d",
                workspace_id,
                len(records),
                sorted(disagreeing_fields),
                stale_active_groups,
            )
        if resolved:
            normalized[workspace_id] = resolved
    return normalized


def _save_live_presets(
    session_manager: Any,
    snapshots: Dict[str, Dict[str, Any]],
) -> Tuple[List[str], List[Dict[str, str]]]:
    stored = _load_saved_sessions_payload()
    entries = list(stored.get("sessions") or [])
    taken_names = [entry.get("name") for entry in entries]
    saved_ids: List[str] = []
    errors: List[Dict[str, str]] = []

    for workspace_id in sorted(snapshots):
        for group in snapshots[workspace_id].get("groups") or []:
            group_id = str(group.get("group_id") or "").strip()
            attached_id = str(group.get("saved_session_id") or "").strip()
            existing = _find_saved_session_entry(entries, attached_id)
            live_config = _live_group_config(group)
            if existing is not None:
                config = _merge_workspace_session_config(existing["config"], live_config)
                preset_name = existing["name"]
                preset_id = existing["id"]
            else:
                config = _normalize_session_config(live_config)
                base_name = str(group.get("name") or "Open session").strip() or "Open session"
                preset_name = build_unique_session_name(base_name, taken_names)
                preset_id = attached_id or None
            try:
                saved = upsert_saved_session(
                    config,
                    name=preset_name,
                    session_id=preset_id,
                    set_last_session=False,
                )
                updated = session_manager.update_group_saved_session(
                    group_id,
                    saved["id"],
                    saved["name"],
                    layout=saved["config"].get("layout"),
                    workspace_layout=saved["config"].get("workspace_layout"),
                    session_view_updates=build_live_session_view_updates(
                        live_config, saved["config"]
                    ),
                )
                saved_ids.append(saved["id"])
                entries.append(saved)
                taken_names.append(saved["name"])
                if updated is None:
                    errors.append(
                        {
                            "scope": "session",
                            "id": group_id,
                            "error": "The live group closed before its preset could be linked",
                        }
                    )
            except Exception:
                # Deliberately omit names, targets, paths, and exception text:
                # preset errors can contain state-file paths and this response is
                # a shape diagnostic, not a secret-bearing debug channel.
                errors.append(
                    {
                        "scope": "session",
                        "id": group_id,
                        "error": "The reusable session could not be saved",
                    }
                )
    return saved_ids, errors


def prepare_lifecycle_action(
    session_manager: Any,
    action: str,
    save: str,
    workspace_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Perform persistence work and report whether teardown is now allowed."""
    if action not in LIFECYCLE_ACTIONS:
        raise LifecycleValidationError("Unknown lifecycle action")
    if save not in LIFECYCLE_SAVE_CHOICES:
        raise LifecycleValidationError("Unknown lifecycle save choice")

    result: Dict[str, Any] = {
        "action": action,
        "save": save,
        "ready_to_exit": False,
        "retryable": False,
        "saved_sessions": [],
        "saved_workspaces": [],
        "errors": [],
    }
    if save == LIFECYCLE_SAVE_NONE:
        result["ready_to_exit"] = True
        return result

    if save == LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES:
        credential_snapshot = session_manager.snapshot_lifecycle_workspaces()
        saved_ids, preset_errors = _save_live_presets(
            session_manager, credential_snapshot
        )
        result["saved_sessions"] = saved_ids
        result["errors"].extend(preset_errors)
        if preset_errors:
            # Shape diagnostics only: counts and scopes, never names, targets,
            # or exception text — preset failures can carry state-file paths.
            logger.warning(
                "Lifecycle %s preset save incomplete: saved=%d failed=%d scopes=%s",
                action,
                len(saved_ids),
                len(preset_errors),
                sorted({error.get("scope", "session") for error in preset_errors}),
            )
            result["retryable"] = True
            return result

    try:
        stored = capture_live_workspaces(
            session_manager,
            origin="manual",
            workspace_metadata=workspace_metadata,
        )
        result["saved_workspaces"] = sorted(stored)
    except Exception as exc:
        # The category is the exception class, not its text: store failures
        # can embed filesystem paths, which must stay out of the log.
        logger.warning(
            "Lifecycle %s workspace capture failed: category=%s",
            action,
            type(exc).__name__,
        )
        result["errors"].append(
            {
                "scope": "workspace",
                "error": "The open workspaces could not be saved",
            }
        )
        result["retryable"] = True
        return result

    logger.info(
        "Lifecycle %s prepared save=%s presets=%d workspaces=%s",
        action,
        save,
        len(result["saved_sessions"]),
        sorted(stored),
    )
    result["ready_to_exit"] = True
    return result


def prepare_workspace_save(
    session_manager: Any,
    workspace_id: Any,
    emit_request: Callable[[str, str], None],
) -> Tuple[Dict[str, Any], int]:
    """Flush one workspace's window and capture only its slot (launcher Save).

    The Stage 4 flush handshake scoped to a single workspace, so the launcher's
    per-row **Save** means exactly what in-window **Save Workspace** means: the
    owning window acknowledges its latest presentation before the capture. It
    never writes reusable presets and never issues a teardown decision.

    A workspace with no reachable window is reported, never silently captured
    from the last acknowledged server state — the same rule the exit
    transaction follows. Sibling slots and ``saved_sessions.json`` are left
    untouched by construction (:func:`capture_workspace` writes one slot).

    Returns ``(payload, status)``; the caller only serializes it.
    """
    workspace = str(workspace_id or "").strip()
    if session_manager.get_workspace(workspace) is None:
        return {"error": "Workspace not found", "workspace_missing": True}, 404

    if lifecycle_coordinator.connected_window_count(workspace) == 0:
        logger.debug(
            "Workspace save %s refused: category=no_reachable_window", workspace
        )
        return {
            "saved": False,
            "workspace_id": workspace,
            "error": "No reachable window for this workspace — open it, then save again",
            "retryable": True,
        }, 503

    flush_result = lifecycle_coordinator.request_flush({workspace}, emit_request)
    if not flush_result["ok"]:
        logger.warning(
            "Workspace save %s flush failed: categories=%s",
            workspace,
            sorted({error.get("category", "client_flush") for error in flush_result["errors"]}),
        )
        return {
            "saved": False,
            "workspace_id": workspace,
            "error": "The workspace window did not finish flushing — try again",
            "retryable": True,
            "errors": flush_result["errors"],
            "missing_workspaces": flush_result["missing_workspaces"],
        }, 503

    snapshots = session_manager.snapshot_live_workspaces()
    live_snapshot = snapshots.get(workspace) or {}
    try:
        metadata = normalize_workspace_metadata(
            flush_result["metadata"], {workspace: live_snapshot}
        ).get(workspace) or {}
    except LifecycleValidationError as exc:
        logger.warning(
            "Workspace save %s rejected client metadata: category=client_metadata",
            workspace,
        )
        return {
            "saved": False,
            "workspace_id": workspace,
            "error": str(exc),
            "retryable": True,
            "errors": [{"category": "client_metadata", "error": str(exc)}],
        }, 503

    topbar_visible = metadata.get("topbar_visible")
    try:
        slot = capture_workspace(
            session_manager,
            workspace_id=workspace,
            origin="manual",
            active_group_id=metadata.get("active_group_id") or None,
            native_zoom_factor=metadata.get("native_zoom_factor"),
            topbar_visible=topbar_visible if isinstance(topbar_visible, bool) else None,
        )
    except RuntimeStatePersistenceError as exc:
        # Never answer "saved" for a revision that did not reach the disk.
        logger.warning(
            "Workspace save %s could not commit: category=%s",
            workspace,
            type(exc).__name__,
        )
        return {
            "saved": False,
            "workspace_id": workspace,
            "error": "The workspace snapshot could not be written to disk",
            "retryable": True,
        }, 503
    if slot is None:
        # An empty workspace is never captured, so it never overwrites (or
        # clears) the previously saved slot.
        logger.debug("Workspace save %s skipped: category=empty_workspace", workspace)
        return {
            "saved": False,
            "workspace_id": workspace,
            "error": "This workspace has no sessions to save",
        }, 409
    logger.info(
        "Workspace %s saved from launcher origin=%s revision=%s",
        slot["workspace_id"],
        slot["origin"],
        slot.get("revision"),
    )
    return {
        "saved": True,
        "workspace_id": slot["workspace_id"],
        "label": slot["label"],
        "origin": slot["origin"],
        "saved_at": slot["saved_at"],
        "active_group_id": slot["active_group_id"],
        "native_zoom_factor": slot.get("native_zoom_factor"),
        "topbar_visible": slot["topbar_visible"],
    }, 200
