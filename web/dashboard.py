"""One reading of everything that is live: workspaces, sessions, panes.

GridVibe already answers "what is in *this* window" several times over --
``/api/workspaces`` lists workspaces, ``/api/session-groups`` lists one
workspace's groups, ``/api/sessions`` lists one group's panes -- and a control
surface that wants all three at once would otherwise have to fan out N+1
requests and stitch the answers together while they moved underneath it. This
module is that answer composed once, on the server, from one pass over the
manager.

Two rules shape what it carries:

* **Facts, not names.** The payload states what a pane *is* -- its startup
  mode, its agent selection, its status, what it has been announcing -- and
  never what to call it. Turning that into "Claude" or "Terminal 3" is one rule
  with two consumers (the pane header and the dashboard row), and it lives with
  them, in ``web/static/js/agent-identity.js``. A second implementation here is
  exactly how the two surfaces would come to disagree about the same pane.
* **A credential-free subset, built rather than filtered.** Pane payloads are
  assembled field by field from a fixed list, so a field added to
  ``TerminalSession`` later cannot arrive here by default -- passwords least of
  all.

:func:`compose_dashboard` is pure: dictionaries in, dictionary out, no manager,
no Flask, no clock of its own. :func:`build_dashboard_snapshot` is the thin
gatherer that feeds it, and it takes the two shared locks in the allowed order
and never nested -- the activity snapshot is read (and released) before the
manager is touched at all.
"""

import time
from typing import Any, Dict, List, Optional

from web.agent_activity import describe_agent_activity

#: The pane fields the dashboard publishes. Everything else a session holds --
#: credentials, explorer view state, browser tabs, the presentation fields --
#: stays where it is; a dashboard row needs to know what a pane is and where it
#: points, and nothing more.
PANE_FIELDS = (
    "session_id",
    "group_id",
    "title",
    "host",
    "mode",
    "startup_mode",
    "agent_selection",
    "custom_agent",
    "agent_auto_mode",
    "status",
)


def _pane_directory(session: Dict[str, Any]) -> str:
    """Where the pane is now, falling back to where it started.

    The observed directory when one has been observed, exactly as the explorer
    reads it: a pane that has moved says so, and one that never reported still
    names somewhere real rather than nothing.
    """
    current = str(session.get("current_directory") or "").strip()
    return current or str(session.get("directory") or "").strip()


def compose_pane(
    session: Dict[str, Any],
    index: int,
    workspace_id: str,
    activity: Optional[Dict[str, Any]],
    now: float,
) -> Dict[str, Any]:
    """Build one pane row.

    ``activity`` is ``None`` for a pane with no transport to observe -- an
    explorer or a browser pane -- and the row says so by carrying ``None``
    rather than a blank reading, because "nothing to observe here" and
    "observed nothing yet" are different answers.
    """
    pane = {field: session.get(field) for field in PANE_FIELDS}
    pane["workspace_id"] = workspace_id
    pane["index"] = index
    pane["directory"] = _pane_directory(session)
    pane["activity"] = (
        describe_agent_activity(activity, now) if activity is not None else None
    )
    return pane


def compose_group(
    group: Dict[str, Any],
    sessions: List[Dict[str, Any]],
    workspace_id: str,
    active_group_id: str,
    activity: Dict[str, Any],
    now: float,
) -> Dict[str, Any]:
    """Build one session row and the pane rows under it."""
    panes = [
        compose_pane(
            session,
            index,
            workspace_id,
            activity.get(str(session.get("session_id") or "")),
            now,
        )
        for index, session in enumerate(sessions)
    ]
    return {
        "group_id": group.get("group_id"),
        "workspace_id": workspace_id,
        "name": group.get("name"),
        "connection_mode": group.get("connection_mode"),
        "layout": group.get("layout"),
        "saved_session_id": group.get("saved_session_id") or "",
        "created_at": group.get("created_at"),
        # The workspace's own hint about which tab is in front. It is a hint on
        # purpose: no window has to be open for it to be true, and a dashboard
        # that only marked a group when a window happened to be reporting would
        # blink the marker on and off as windows come and go.
        "is_active": bool(active_group_id) and group.get("group_id") == active_group_id,
        "pane_count": len(panes),
        "panes": panes,
    }


def compose_dashboard(
    *,
    workspaces: List[Dict[str, Any]],
    groups_by_workspace: Dict[str, List[Dict[str, Any]]],
    sessions_by_group: Dict[str, List[Dict[str, Any]]],
    activity: Dict[str, Any],
    now: float,
) -> Dict[str, Any]:
    """Compose the whole reading from data already gathered.

    Order is the order every other surface uses -- workspaces as
    ``list_live_workspaces`` returns them (default first), groups in their
    workspace's own display order, panes in their group's pane order -- so a
    row is where the window that owns it would put it.
    """
    composed_workspaces = []
    for workspace in workspaces:
        workspace_id = str(workspace.get("workspace_id") or "")
        active_group_id = str(workspace.get("active_group_id") or "")
        groups = [
            compose_group(
                group,
                sessions_by_group.get(str(group.get("group_id") or ""), []),
                workspace_id,
                active_group_id,
                activity,
                now,
            )
            for group in groups_by_workspace.get(workspace_id, [])
        ]
        composed_workspaces.append({
            "workspace_id": workspace_id,
            "label": str(workspace.get("label") or ""),
            "created_at": workspace.get("created_at"),
            "active_group_id": active_group_id,
            "group_count": len(groups),
            "pane_count": sum(group["pane_count"] for group in groups),
            "groups": groups,
        })

    panes = [
        pane
        for workspace in composed_workspaces
        for group in workspace["groups"]
        for pane in group["panes"]
    ]
    return {
        "generated_at": now,
        "workspaces": composed_workspaces,
        # Counted here rather than in the page so every surface that shows a
        # badge counts the same way, and a page that has not expanded a
        # workspace still knows what is inside it.
        "totals": {
            "workspaces": len(composed_workspaces),
            "sessions": sum(workspace["group_count"] for workspace in composed_workspaces),
            "panes": len(panes),
            "agents": sum(1 for pane in panes if pane.get("startup_mode") == "agent"),
        },
    }


def build_dashboard_snapshot(session_manager: Any, activity: Dict[str, Any]) -> Dict[str, Any]:
    """Gather one pass over the live manager and compose it.

    ``activity`` arrives already snapshotted, which is the point: reading it
    takes ``connection_lock``, and taking that inside the manager lock would
    invert the one ordering the whole backend depends on. The caller reads it
    first, releases, and hands it in.
    """
    from web.workspaces import list_live_workspaces

    workspaces = list_live_workspaces()
    groups_by_workspace: Dict[str, List[Dict[str, Any]]] = {}
    sessions_by_group: Dict[str, List[Dict[str, Any]]] = {}
    with session_manager.lock:
        for workspace in workspaces:
            workspace_id = str(workspace.get("workspace_id") or "")
            groups = session_manager.get_workspace_groups(workspace_id)
            groups_by_workspace[workspace_id] = [group.to_dict() for group in groups]
            for group in groups:
                sessions_by_group[group.group_id] = [
                    session.to_dict()
                    for session in session_manager.get_group_sessions(group.group_id)
                ]
    return compose_dashboard(
        workspaces=workspaces,
        groups_by_workspace=groups_by_workspace,
        sessions_by_group=sessions_by_group,
        activity=activity,
        now=time.time(),
    )
