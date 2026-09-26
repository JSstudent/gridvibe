"""Navigating live sessions on a tool's behalf: which tab to show, and where a
group lives.

Two questions a tool asks about groups that already exist, answered from the
live registry at the moment of the request and never from what the caller
read a moment earlier -- a group can move or close between a `list_panes` and
the write that names it.

* **Show this group / this pane.** A window intent or an activation intent
  names a workspace, a group and optionally a pane. :func:`resolve_view_target`
  checks that the three still belong together before anything is recorded, so
  a page is never asked to switch to a tab another window now holds.
* **Move this group.** :func:`move_group_for_agent` is the gated twin of the
  launcher's own move route. The caller's own group needs no permission; any
  other group passes the lineage rule every other tool-reachable pane change
  passes (`web/pane_gates.py`), widened from one pane to the whole group, with
  a confirmation question that names the destination.

No Flask here: a refusal is a :class:`NavigationRefusal` carrying the status
and, for a gate, the structured half of the body.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from web.app import session_manager
from web.pane_gates import (
    LINEAGE_GATE,
    OVERRIDE_TAIL,
    PaneGateRefusal,
    refuse,
)

logger = logging.getLogger(__name__)


class NavigationRefusal(Exception):
    """A request that changed nothing, with the status its route answers."""

    def __init__(self, message: str, status_code: int = 400, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})

    def payload(self) -> Dict[str, Any]:
        return {"error": self.message, "changed": False, **self.details}


@dataclass(frozen=True)
class ViewTarget:
    """A workspace, one of its groups, and optionally one of that group's panes."""

    workspace_id: str
    group_id: str
    session_id: str = ""


def resolve_view_target(
    workspace_id: str = "",
    group_id: str = "",
    session_id: str = "",
) -> ViewTarget:
    """Resolve and check what a window or activation intent names.

    A pane names its own group, and a group names its own workspace, so only
    the most specific id is needed; any id the caller also stated must agree
    with the live answer. A disagreement is a stale read (409), never silently
    corrected: the caller was about to show the person something that is no
    longer where it thought.
    """
    stated_workspace = str(workspace_id or "").strip()
    stated_group = str(group_id or "").strip()
    stated_session = str(session_id or "").strip()

    resolved_group = stated_group
    if stated_session:
        session = session_manager.get_session(stated_session)
        if session is None:
            raise NavigationRefusal(
                f"Pane {stated_session} is not open. Nothing was shown; "
                "call list_panes for the panes that are.",
                404,
            )
        live_group = str(getattr(session, "group_id", "") or "")
        if stated_group and stated_group != live_group:
            raise NavigationRefusal(
                f"Pane {stated_session} is no longer in session group "
                f"{stated_group}. Nothing was shown; call list_panes again.",
                409,
            )
        resolved_group = live_group

    if not resolved_group:
        if not stated_workspace:
            raise NavigationRefusal("A workspace, session group or pane has to be named.")
        return ViewTarget(stated_workspace, "")

    group = session_manager.get_group(resolved_group)
    if group is None:
        raise NavigationRefusal(
            f"Session group {resolved_group} is not open. Nothing was shown; "
            "call list_workspaces for the groups that are.",
            404,
        )
    live_workspace = str(group.workspace_id or "")
    if stated_workspace and stated_workspace != live_workspace:
        raise NavigationRefusal(
            f"Session group {resolved_group} is in workspace {live_workspace}, "
            f"not {stated_workspace}. Nothing was shown; name that workspace "
            "or call list_workspaces again.",
            409,
            {"workspace_id": live_workspace, "group_id": resolved_group},
        )
    return ViewTarget(live_workspace, resolved_group, stated_session)


# ---------------- the gated group move ----------------


def _caller(payload: Mapping[str, Any]) -> Tuple[str, bool]:
    caller = str((payload or {}).get("requested_by_session_id") or "").strip()
    if not caller:
        raise NavigationRefusal(
            "requested_by_session_id is required: a move has to name the pane "
            "asking for it.",
            400,
        )
    return caller, bool((payload or {}).get("override"))


def _workspace_label(workspace_id: str) -> str:
    workspace = session_manager.get_workspace(workspace_id) if workspace_id else None
    label = str(getattr(workspace, "label", "") or "") if workspace is not None else ""
    return label or workspace_id


def _destination_words(payload: Mapping[str, Any]) -> str:
    data = payload or {}
    if data.get("new_workspace"):
        label = str(data.get("label") or data.get("workspace_label") or "").strip()
        return f"a new workspace '{label}'" if label else "a new workspace"
    target = str(data.get("target_workspace_id") or data.get("workspace_id") or "").strip()
    return f"workspace '{_workspace_label(target)}'" if target else "another workspace"


def _group_confirmation(group: Any, pane_count: int, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """The question a waivable move refusal carries, naming the destination.

    Target-specific on purpose: "move it?" answered yes for one destination is
    not a yes for another, so the question the person answers states both ends.
    """
    source = _workspace_label(str(group.workspace_id or ""))
    destination = _destination_words(payload)
    name = str(group.name or "") or str(group.group_id)
    panes = "1 pane" if pane_count == 1 else f"{pane_count} panes"
    return {
        "group": {
            "group_id": str(group.group_id),
            "name": str(group.name or ""),
            "pane_count": pane_count,
            "workspace_id": str(group.workspace_id or ""),
        },
        "destination": destination,
        "question": (
            f"Move session '{name}' ({panes}, still running) from workspace "
            f"'{source}' to {destination}?"
        ),
    }


def check_group_move(group_id: str, payload: Mapping[str, Any], *, log_override: bool = True) -> Any:
    """The gates a tool-requested move passes. Returns the live group.

    * The caller must still be open: a closed caller cannot say whose group is
      whose, and is not waivable.
    * Its own group moves without permission -- the person's agent moving the
      tab it lives in is the common case, and it ends nothing.
    * Any other group moves only when every pane in it was created by the
      caller, or when the person said so (``override``).

    In-memory only, so the move transaction can run it again under the
    manager lock; ``log_override`` is false there, because a log line is I/O.
    """
    caller, override = _caller(payload)
    group = session_manager.get_group(str(group_id or "").strip())
    if group is None:
        raise NavigationRefusal(
            f"Session group {group_id} is not open. Nothing was moved; call "
            "list_workspaces for the groups that are.",
            404,
        )
    caller_session = session_manager.get_session(caller)
    if caller_session is None:
        refusal = refuse(
            LINEAGE_GATE,
            "The pane this request came from is no longer open, so GridVibe "
            "cannot tell whether it created this session group. Nothing was moved.",
        )
        raise NavigationRefusal(refusal.message, 403, refusal.details())

    if str(getattr(caller_session, "group_id", "") or "") == group.group_id:
        return group

    panes: List[Any] = session_manager.get_group_sessions(group.group_id)
    creators = {str(getattr(pane, "created_by_session_id", "") or "") for pane in panes}
    if panes and creators == {caller}:
        return group
    if override:
        if not log_override:
            return group
        logger.info(
            "Group move gate override group_id=%s requested_by_session_id=%s",
            group.group_id,
            caller,
        )
        return group
    refusal: PaneGateRefusal = refuse(
        LINEAGE_GATE,
        "This session group is not this agent's own and was not created by "
        f"it, so a tool does not move it. Nothing was moved, {OVERRIDE_TAIL}.",
        waivable=True,
    )
    refusal.confirm = _group_confirmation(group, len(panes), payload)
    raise NavigationRefusal(refusal.message, 403, refusal.details())


#: What a tool is told about a move. Explicit, because the transaction's own
#: payload carries every group in both workspaces -- a window's refresh, not an
#: answer.
MOVE_RESULT_FIELDS = (
    "moved",
    "group_id",
    "workspace_id",
    "source_workspace_id",
    "workspace_created",
    "source_workspace_pruned",
)


def move_group_for_agent(group_id: str, payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Gate, then run the launcher's own move transaction unchanged."""
    from web.workspaces import move_group_to_workspace

    data = payload or {}
    try:
        # Stated, never defaulted: the transaction reads an empty destination
        # as the default workspace, which is not what a tool that forgot to
        # name one asked for.
        if not data.get("new_workspace") and not str(
            data.get("target_workspace_id") or data.get("workspace_id") or ""
        ).strip():
            raise NavigationRefusal(
                "Name a target_workspace_id or ask for new_workspace. Nothing was moved."
            )
        group = check_group_move(group_id, data)
    except NavigationRefusal as exc:
        return exc.payload(), exc.status_code
    body: Dict[str, Any] = {
        "target_workspace_id": data.get("target_workspace_id") or data.get("workspace_id") or "",
        "new_workspace": bool(data.get("new_workspace")),
        "label": data.get("label") or data.get("workspace_label") or "",
    }
    gated_source = str(group.workspace_id or "")

    def recheck() -> Optional[Tuple[Dict[str, Any], int]]:
        # Run again under the manager lock in the same hold as the move: the
        # caller could have closed, a pane it did not create could have
        # joined, or the group could have moved since the first check -- and
        # the confirmation the person answered named that source.
        try:
            live = check_group_move(group.group_id, data, log_override=False)
        except NavigationRefusal as exc:
            return exc.payload(), exc.status_code
        if str(live.workspace_id or "") != gated_source:
            return NavigationRefusal(
                f"Session group {group.group_id} moved to workspace "
                f"{live.workspace_id} while this request was checked. Nothing "
                "was moved; read it again and ask again.",
                409,
                {"workspace_id": str(live.workspace_id or "")},
            ).payload(), 409
        return None

    result, status = move_group_to_workspace(group.group_id, body, guard=recheck)
    if status != 200:
        return {**result, "changed": False}, status
    answer = {key: result[key] for key in MOVE_RESULT_FIELDS if key in result}
    answer.setdefault("workspace_created", False)
    answer.setdefault("source_workspace_pruned", False)
    # The group's own record, read after the move: the one field a caller
    # needs to act next is where the group is *now*.
    live = session_manager.get_group(group.group_id)
    answer["current_workspace_id"] = str(live.workspace_id) if live is not None else ""
    answer["group_name"] = str(group.name or "")
    logger.info(
        "Agent group move group_id=%s moved=%s from=%s to=%s requested_by_session_id=%s",
        group.group_id,
        answer.get("moved"),
        answer.get("source_workspace_id") or "-",
        answer.get("workspace_id") or "-",
        str(data.get("requested_by_session_id") or "-"),
    )
    return answer, 200
