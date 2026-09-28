"""Gated, whole-target close transactions for agent tools."""

import logging
from typing import Any, Dict, List, Mapping, Tuple

from sessions.manager import SessionStatus
from web.app import session_manager
from web.pane_gates import (
    LINEAGE_GATE,
    MODE_GATE,
    GateWording,
    PaneGateRefusal,
    attach_confirmation,
    check_caller,
    check_lineage,
    read_agent_request,
    refuse,
)

logger = logging.getLogger(__name__)

CLOSE_WORDING = GateWording(
    request_noun="a close",
    self_reason="A tool cannot close the pane it is running in.",
    act="close it",
    acts="closes",
)


def _target(kind: str, target_id: str, label: str) -> Dict[str, str]:
    return {"kind": kind, "id": target_id, "name": label or target_id}


def _question(kind: str, target: Mapping[str, str], facts: Any) -> str:
    name = target["name"]
    if kind == "pane":
        return f"Close pane {facts.name}, ending {facts.agent + ' and its conversation' if facts.agent else 'its shell or view'}?"
    return (
        f"Close {kind} {name}, including pane {facts.name} and every other pane "
        "in it? Any running agents and their conversations will end."
    )


def _closed_payload(target: Mapping[str, str], pane_ids: List[str], group_ids: List[str],
                    workspace_ids: List[str]) -> Dict[str, Any]:
    return {
        "target": dict(target),
        "closed_session_ids": pane_ids,
        "closed_group_ids": group_ids,
        "closed_workspace_ids": workspace_ids,
    }


def close_for_agent(kind: str, target_id: str, payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], int]:
    """Preflight the entire live target, then close it with ownership held.

    No transport teardown, emit, persistence write or activity lookup occurs
    under the manager lock. A close interrupted after its first pane reports
    precisely which pane IDs were marked closed.
    """
    if kind not in ("pane", "group", "workspace"):
        return {"error": "Unknown close target."}, 400
    try:
        request = read_agent_request(payload, CLOSE_WORDING)
    except PaneGateRefusal as exc:
        return {"error": exc.message, **exc.details()}, exc.status_code

    pane_ids: List[str] = []
    group_ids: List[str] = []
    workspace_ids: List[str] = []
    refusal = None
    refused_pane = None
    affected = []
    affected_panes: List[Dict[str, Any]] = []
    original_group_ids: List[str] = []
    owners: Dict[str, Tuple[Any, str]] = {}
    failure = ""
    pruned = []
    target = _target(kind, target_id, "")
    workspace_id = ""

    with session_manager.lock:
        caller = session_manager.sessions.get(request.caller_session_id)
        if caller is None:
            refusal = refuse(LINEAGE_GATE, "The pane this request came from is no longer open.")
        else:
            if kind == "pane":
                session = session_manager.sessions.get(target_id)
                if session is None:
                    return {"error": "Pane not found"}, 404
                sessions = [session]
                group = session_manager.groups.get(session.group_id)
                if group is None:
                    return {"error": "Pane has no live group"}, 409
                workspace_id = group.workspace_id
                target = _target(kind, target_id, session.title)
            elif kind == "group":
                group = session_manager.groups.get(target_id)
                if group is None:
                    return {"error": "Session group not found"}, 404
                sessions = session_manager.get_group_sessions(target_id)
                original_group_ids = [target_id]
                workspace_id = group.workspace_id
                target = _target(kind, target_id, group.name)
            else:
                workspace = session_manager.workspaces.get(target_id)
                if workspace is None:
                    return {"error": "Workspace not found"}, 404
                sessions = session_manager.get_workspace_sessions(target_id)
                original_group_ids = [group.group_id for group in session_manager.get_workspace_groups(target_id)]
                workspace_id = target_id
                target = _target(kind, target_id, workspace.label)

            affected = [session.session_id for session in sessions]
            affected_panes = [
                {
                    "session_id": session.session_id,
                    "title": str(session.title or ""),
                    "agent": (
                        str(session.agent_selection or session.custom_agent or "")
                        if session.startup_mode == "agent" else ""
                    ),
                }
                for session in sessions
            ]
            owners = {
                session.session_id: (session_manager.groups.get(session.group_id), session.group_id)
                for session in sessions
            }
            # Self is never waived, even when an earlier pane would have a
            # waivable lineage refusal.
            if request.caller_session_id in affected:
                refusal = refuse("self", f"The {kind} contains the pane this request came from. A tool cannot close it.")
            else:
                for session in sessions:
                    try:
                        check_caller(session.session_id, request, CLOSE_WORDING)
                        check_lineage(session, request, CLOSE_WORDING)
                        if str(getattr(session, "startup_mode", "") or "") == "agent" and not request.override:
                            raise refuse(
                                MODE_GATE,
                                "This pane is running an agent. Closing it ends the agent and its conversation; "
                                "ask the user before overriding this pane.",
                                waivable=True,
                            )
                    except PaneGateRefusal as exc:
                        refusal = exc
                        refused_pane = session
                        break

        if refusal is None:
            try:
                for session in sessions:
                    # The manager lock makes this a fresh ownership check at
                    # execution, including concurrent moves and replacements.
                    if session_manager.sessions.get(session.session_id) is not session:
                        raise RuntimeError("A pane changed ownership during close")
                    owner, group_id = owners[session.session_id]
                    if (
                        session.group_id != group_id
                        or session_manager.groups.get(group_id) is not owner
                        or owner is None
                        or owner.workspace_id != workspace_id
                    ):
                        raise RuntimeError("A pane moved to a different group or workspace during close")
                    if not session_manager.close_session(session.session_id):
                        raise RuntimeError("A pane disappeared during close")
                    pane_ids.append(session.session_id)
                if kind == "workspace":
                    workspace.retain_when_empty = False
                pruned = session_manager.clear_disconnected_sessions()
                if kind == "workspace":
                    session_manager.remove_workspace(target_id)
                if kind == "group" and target_id not in session_manager.groups:
                    group_ids.append(target_id)
                elif kind == "pane" and group.group_id not in session_manager.groups:
                    group_ids.append(group.group_id)
                elif kind == "workspace":
                    group_ids.extend(group_id for group_id in original_group_ids if group_id not in session_manager.groups)
                if kind == "workspace" and not session_manager.get_workspace_groups(target_id):
                    workspace_ids.append(target_id)
                elif kind != "workspace" and workspace_id in pruned:
                    workspace_ids.append(workspace_id)
            except Exception as exc:  # partial close must never be described as success
                logger.error("Agent close interrupted kind=%s target=%s category=%s", kind, target_id, type(exc).__name__)
                failure = "The close stopped during execution"
                # A close implementation can fail after marking the current
                # pane disconnected. Report that actual state too.
                for session in sessions:
                    if session.session_id not in pane_ids and session.status == SessionStatus.DISCONNECTED:
                        pane_ids.append(session.session_id)
                # Sweep only after a mutation, to remove sessions successfully
                # marked disconnected before the interruption.
                if pane_ids:
                    try:
                        pruned = session_manager.clear_disconnected_sessions()
                    except Exception as exc:
                        logger.error("Agent close sweep failed kind=%s target=%s category=%s", kind, target_id, type(exc).__name__)
                        failure += "; cleanup failed"
                if kind == "group" and target_id not in session_manager.groups:
                    group_ids.append(target_id)
                if kind == "pane" and group.group_id not in session_manager.groups:
                    group_ids.append(group.group_id)
                if kind == "workspace" and target_id not in session_manager.workspaces:
                    workspace_ids.append(target_id)
                if kind == "workspace":
                    group_ids.extend(group_id for group_id in original_group_ids if group_id not in session_manager.groups)
                if kind != "workspace" and workspace_id in pruned:
                    workspace_ids.append(workspace_id)

    if refusal is not None:
        attach_confirmation(refusal, refused_pane, lambda facts: _question(kind, target, facts))
        details = refusal.details()
        if refusal.waivable and "confirm" in details:
            details["confirm"]["target"] = target
            details["confirm"]["affected_session_ids"] = affected
            details["confirm"]["affected_panes"] = affected_panes
        return {"error": refusal.message, "changed": False, **details}, refusal.status_code

    # Each close ends the worker's pending assignment ("pane closed") and
    # drops assignments owed to a requester that has itself closed.
    from web.terminal_io import (
        _broadcast_session_groups_updated,
        _close_ssh_connection,
        agent_handoffs,
        agent_results,
    )
    from web.workspaces import forget_emptied_default_workspace, forget_pruned_workspaces

    for session_id in pane_ids:
        try:
            _close_ssh_connection(session_id, clear_buffer=True)
        except Exception as exc:
            logger.error("Agent close transport teardown failed pane=%s category=%s", session_id, type(exc).__name__)
            failure = f"{failure}; transport teardown failed for {session_id}".strip("; ")
        # A manager cleanup failure can leave a disconnected record present.
        # End assignments regardless of whether transport retirement reached
        # its own forget_session calls.
        for cleanup in (agent_handoffs.forget_session, agent_results.forget_session):
            try:
                cleanup(session_id)
            except Exception as exc:
                logger.error("Agent close assignment cleanup failed pane=%s category=%s", session_id, type(exc).__name__)
                failure = f"{failure}; assignment cleanup failed for {session_id}".strip("; ")
    if kind != "workspace":
        try:
            forget_pruned_workspaces(pruned)
            forget_emptied_default_workspace(workspace_id)
        except Exception as exc:
            logger.error("Agent close snapshot cleanup failed workspace=%s category=%s", workspace_id, type(exc).__name__)
            failure = f"{failure}; snapshot cleanup failed".strip("; ")
    try:
        event_group_id = target_id if kind == "group" else group.group_id if kind == "pane" else ""
        if kind == "workspace" and target_id in workspace_ids:
            event_reason = "workspace_closed"
        elif kind == "group" and target_id in group_ids:
            event_reason = "group_closed"
        else:
            event_reason = "session_closed"
        _broadcast_session_groups_updated(
            event_reason,
            group_id=event_group_id,
            workspace_id=workspace_id,
            closed_session_ids=pane_ids,
            closed_group_ids=group_ids,
        )
        for pruned_workspace_id in pruned:
            if pruned_workspace_id != workspace_id:
                _broadcast_session_groups_updated(
                    "workspace_pruned", workspace_id=pruned_workspace_id
                )
    except Exception as exc:
        logger.error("Agent close broadcast failed kind=%s target=%s category=%s", kind, target_id, type(exc).__name__)
        failure = f"{failure}; broadcast failed".strip("; ")

    result = _closed_payload(target, pane_ids, group_ids, workspace_ids)
    if failure:
        return {**result, "error": f"Close was interrupted: {failure}", "partial": True}, 500
    return {**result, "closed": True, "partial": False}, 200
