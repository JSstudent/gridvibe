"""The tool surface, and the dispatch behind it.

Thin for the same reason a Flask route is thin: parse arguments, call
``client.py``, map the result. No HTTP in a tool handler, and no policy in a
tool handler -- the field allowlists live in the client and the depth budget
lives in ``identity.py``.

Nine tools, grouped by blast radius. The destroy tier -- closing a pane, a
group or a workspace, switching a pane's mode or shell, moving a group, and
typing into a terminal -- is **absent from the build**, not flag-gated. A tool
that does not exist cannot be talked into running by a file an agent reads.

This module deliberately imports no MCP SDK: ``__main__.py`` owns the protocol
wiring, so the tool surface can be tested without the SDK installed.
"""

from typing import Any, Callable, Dict, List, Mapping, Optional

from gridvibe_mcp.client import GridVibeClient, GridVibeError
from gridvibe_mcp.identity import (
    DEFAULT_MAX_AGENT_DEPTH,
    PaneIdentity,
    depth_budget,
    read_identity,
)
from gridvibe_mcp.windows import open_window as open_window_for

#: "wsl" is GridVibe's historical spelling of a *local* launch, as opposed to
#: "ssh". It does not mean the pane runs under WSL -- that is `use_wsl`, chosen
#: per pane. A pane's own ``mode`` field uses the same two spellings, which is
#: how ``whoami`` below tells a remote pane from a local one.
#:
#: Only ever the *fallback* now. A launch from inside a pane names that pane
#: instead, and GridVibe resolves the connection from it -- an agent on an SSH
#: pane has no credential to state and must not be guessed local.
LOCAL_CONNECTION_MODE = "wsl"

READ_TOOLS = (
    "gridvibe_status",
    "list_workspaces",
    "list_panes",
    "list_agents",
    "whoami",
)

CREATE_TOOLS = (
    "create_workspace",
    "launch_panes",
    "open_window",
    "split_pane",
)

PANE_KINDS = ("agent", "terminal", "explorer", "browser")
SHELL_KINDS = ("powershell", "cmd", "wsl")
LAYOUTS = ("single", "split", "grid", "stack")


class ToolArgumentError(Exception):
    """A refusal decided before any HTTP call is made."""


def _text(arguments: Mapping[str, Any], name: str, default: str = "") -> str:
    value = arguments.get(name, default)
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        raise ToolArgumentError(f"'{name}' must be text.")
    return str(value).strip()


def _flag(arguments: Mapping[str, Any], name: str, default: bool = False) -> bool:
    value = arguments.get(name, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    raise ToolArgumentError(f"'{name}' must be true or false.")


def _choice(value: str, allowed: tuple, name: str, default: str = "") -> str:
    resolved = (value or default).strip().lower()
    if not resolved:
        return default
    if resolved not in allowed:
        raise ToolArgumentError(
            f"'{name}' must be one of: {', '.join(allowed)}."
        )
    return resolved


def tool_specs() -> List[Dict[str, Any]]:
    """Every tool this build registers, in blast-radius order."""
    return [
        {
            "name": "gridvibe_status",
            "description": (
                "Is GridVibe running, which version, and how many workspaces "
                "does it hold right now."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "list_workspaces",
            "description": "Every live GridVibe workspace, with its label and group count.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "list_panes",
            "description": (
                "The panes in one workspace or one session group: what each is, "
                "where it points and what it runs on."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "Defaults to this agent's own workspace."},
                    "group_id": {"type": "string", "description": "Narrow to one session group."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "list_agents",
            "description": (
                "Every agent running anywhere in GridVibe, with a working/idle "
                "reading, under the workspace and session that holds it."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "whoami",
            "description": (
                "Which GridVibe pane this agent is running in: its session, "
                "group and workspace ids, its directory, which machine that "
                "directory is on, and how deep in agent-launched panes it is. "
                "Call this before resolving 'this directory' or 'this "
                "workspace'."
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "create_workspace",
            "description": (
                "Create one empty, labelled workspace. Creating it does not "
                "make a window appear -- call open_window for that."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "The workspace name the user will see."},
                },
                "required": ["label"],
                "additionalProperties": False,
            },
        },
        {
            "name": "launch_panes",
            "description": (
                "Launch one session group of panes, into a new workspace or an "
                "existing one. Each pane is an agent, a plain terminal, a file "
                "explorer or a browser preview. The panes open on the same "
                "machine as this agent -- for a pane connected over SSH that "
                "is the remote host, not the machine GridVibe runs on, and a "
                "browser pane is refused there because GridVibe draws it "
                "locally."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "panes": {
                        "type": "array",
                        "minItems": 1,
                        "description": "One entry per pane, in the order they should appear.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": list(PANE_KINDS),
                                    "description": "Defaults to 'terminal'.",
                                },
                                "title": {"type": "string"},
                                "directory": {
                                    "type": "string",
                                    "description": (
                                        "Absolute path on the machine this agent's pane runs "
                                        "on. Use whoami's directory for 'this directory'."
                                    ),
                                },
                                "agent": {
                                    "type": "string",
                                    "description": "Agent CLI key for kind='agent', e.g. 'claude'.",
                                },
                                "auto_mode": {"type": "boolean"},
                                "mcp": {
                                    "type": "boolean",
                                    "description": "Give the launched agent these same GridVibe tools.",
                                },
                                "shell": {"type": "string", "enum": list(SHELL_KINDS)},
                                "url": {"type": "string", "description": "For kind='browser'."},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "workspace_id": {"type": "string", "description": "Launch into this existing workspace."},
                    "new_workspace": {"type": "boolean", "description": "Create a workspace for this group."},
                    "workspace_label": {"type": "string", "description": "Name for a new workspace."},
                    "session_name": {"type": "string", "description": "Name of the session group (the tab)."},
                    "layout": {"type": "string", "enum": list(LAYOUTS)},
                },
                "required": ["panes"],
                "additionalProperties": False,
            },
        },
        {
            "name": "open_window",
            "description": (
                "Make a workspace appear on screen. Reports opened, blocked, or "
                "no_window_available -- it never retries and never pretends."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string"},
                    "group_id": {"type": "string", "description": "Open with this session group active."},
                },
                "required": ["workspace_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "split_pane",
            "description": (
                "Append one pane to the group holding an existing pane. A "
                "terminal clones itself; an explorer or browser pane splits "
                "into a terminal rooted where it is showing."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
                "additionalProperties": False,
            },
        },
    ]


def tool_names() -> List[str]:
    return [spec["name"] for spec in tool_specs()]


# ---------------- pane request building ----------------


def build_pane_request(pane: Mapping[str, Any], *, agent_depth: int) -> Dict[str, Any]:
    """Turn one tool-level pane into the session config GridVibe launches.

    The pane *family* is chosen per pane by ``use_powershell``/``use_wsl``, not
    by the group's ``connection_mode`` -- which only separates local from SSH.
    """
    if not isinstance(pane, Mapping):
        raise ToolArgumentError("Each entry in 'panes' must be an object.")
    kind = _choice(_text(pane, "kind"), PANE_KINDS, "kind", "terminal")
    directory = _text(pane, "directory")
    title = _text(pane, "title")

    request: Dict[str, Any] = {"directory": directory}
    if title:
        request["title"] = title

    if kind == "explorer":
        request.update(
            {
                "startup_mode": "explorer",
                "initial_command_mode": "explorer",
                "initial_command": "",
            }
        )
        if directory:
            request["explorer_root_directory"] = directory
            request["explorer_root_configured"] = True
        return request

    if kind == "browser":
        url = _text(pane, "url")
        if not url:
            raise ToolArgumentError("A browser pane needs a 'url'.")
        request.update(
            {
                "startup_mode": "browser",
                "initial_command_mode": "browser",
                "initial_command": url,
            }
        )
        return request

    shell = _choice(_text(pane, "shell"), SHELL_KINDS, "shell", "powershell")
    request["use_powershell"] = shell == "powershell"
    request["use_wsl"] = shell == "wsl"

    if kind == "terminal":
        request.update(
            {
                "startup_mode": "terminal",
                "initial_command_mode": "command",
                "initial_command": "",
            }
        )
        return request

    agent = _text(pane, "agent").lower()
    if not agent:
        raise ToolArgumentError("An agent pane needs an 'agent', e.g. 'claude'.")
    request.update(
        {
            "startup_mode": "agent",
            "initial_command_mode": "agent",
            "initial_command": agent,
            "agent_selection": agent,
            "agent_auto_mode": _flag(pane, "auto_mode", False),
            "agent_mcp": _flag(pane, "mcp", False),
            # Lineage, so the refusal compounds: a pane this agent creates is
            # one level deeper than the pane this agent is in.
            "agent_depth": agent_depth,
        }
    )
    return request


def build_launch_request(
    arguments: Mapping[str, Any],
    *,
    identity: PaneIdentity,
) -> Dict[str, Any]:
    """Build the whole ``POST /api/sessions`` body, or refuse before sending."""
    panes = arguments.get("panes")
    if not isinstance(panes, list) or not panes:
        raise ToolArgumentError("'panes' must be a non-empty list.")

    workspace_id = _text(arguments, "workspace_id")
    workspace_label = _text(arguments, "workspace_label")
    new_workspace = _flag(arguments, "new_workspace", bool(workspace_label) and not workspace_id)
    if not workspace_id and not new_workspace:
        # Default to the workspace this agent is already in rather than
        # guessing at one; an agent outside GridVibe must state one.
        workspace_id = identity.workspace_id
    if not workspace_id and not new_workspace:
        raise ToolArgumentError(
            "Name a 'workspace_id', or set 'new_workspace' with a 'workspace_label'."
        )

    session_name = _text(arguments, "session_name") or workspace_label
    layout = _choice(_text(arguments, "layout"), LAYOUTS, "layout", "")

    body: Dict[str, Any] = {
        "connection_mode": LOCAL_CONNECTION_MODE,
        "sessions": [
            build_pane_request(pane, agent_depth=identity.child_depth) for pane in panes
        ],
    }
    if identity.session_id:
        # Where, not what. GridVibe reads the connection off this pane in its
        # own process -- an agent is never shown its pane's credential, and an
        # agent on an SSH pane that fell back to `connection_mode` above got
        # panes opened on GridVibe's machine holding the remote host's paths.
        body["origin_session_id"] = identity.session_id
    if new_workspace:
        body["new_workspace"] = True
        if workspace_label:
            body["workspace_label"] = workspace_label
    else:
        body["workspace_id"] = workspace_id
    if session_name:
        body["session_name"] = session_name
    body["layout"] = layout or ("split" if len(body["sessions"]) > 1 else "single")
    return body


# ---------------- dispatch ----------------


def dispatch(
    name: str,
    arguments: Optional[Mapping[str, Any]] = None,
    *,
    client: GridVibeClient,
    identity: Optional[PaneIdentity] = None,
    max_agent_depth: int = DEFAULT_MAX_AGENT_DEPTH,
    window_opener: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run one tool. Returns a result payload, or an ``{"error": ...}`` one.

    Never raises for an ordinary refusal: an agent reading a tool result should
    see GridVibe's own sentence, not a stack trace.
    """
    resolved = str(name or "").strip()
    args: Mapping[str, Any] = arguments or {}
    if not isinstance(args, Mapping):
        return {"error": "Tool arguments must be an object.", "kind": "invalid_arguments"}
    if resolved not in tool_names():
        return {"error": f"GridVibe has no tool named '{resolved}'.", "kind": "unknown_tool"}
    pane = identity if identity is not None else read_identity(default_url=client.base_url)

    try:
        return _run(
            resolved,
            args,
            client=client,
            identity=pane,
            max_agent_depth=max_agent_depth,
            window_opener=window_opener or open_window_for,
        )
    except ToolArgumentError as exc:
        return {"error": str(exc), "kind": "invalid_arguments"}
    except GridVibeError as exc:
        return exc.to_dict()


def _run(
    name: str,
    args: Mapping[str, Any],
    *,
    client: GridVibeClient,
    identity: PaneIdentity,
    max_agent_depth: int,
    window_opener: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    if name == "gridvibe_status":
        health = client.health()
        workspaces = client.workspaces()
        return {
            "gridvibe": health,
            "url": client.base_url,
            "workspace_count": len(workspaces),
            "workspaces": workspaces,
        }

    if name == "list_workspaces":
        workspaces = client.workspaces()
        return {"workspaces": workspaces, "count": len(workspaces)}

    if name == "list_panes":
        workspace_id = _text(args, "workspace_id") or identity.workspace_id
        group_id = _text(args, "group_id")
        return client.panes(workspace_id=workspace_id, group_id=group_id)

    if name == "list_agents":
        return client.agents()

    if name == "whoami":
        payload = identity.to_dict()
        if identity.session_id:
            try:
                payload["pane"] = client.pane(identity.session_id)
            except GridVibeError as exc:
                payload["pane"] = None
                payload["pane_error"] = str(exc)
            pane = payload.get("pane") or {}
            payload["directory"] = (
                pane.get("current_directory") or pane.get("directory") or ""
            )
            # Which machine every path in this conversation is a path on. An
            # agent that read `directory` without it handed a remote path to
            # panes opened on GridVibe's own machine, where the shells could
            # not cd into a directory that does not exist. Stated only when the
            # pane was actually read: a failed read knows nothing, and guessing
            # "local" here is the guess that caused the defect.
            if pane:
                payload["host"] = str(pane.get("host") or "")
                remote = str(pane.get("mode") or "") != LOCAL_CONNECTION_MODE
                payload["runs_on"] = "remote_host" if remote else "gridvibe_host"
                if remote:
                    payload["note"] = (
                        f"This pane's shell runs on {payload['host']} over SSH. "
                        "Its directory is a path on that host, and panes "
                        "launched from here open on that host too."
                    )
        else:
            payload["directory"] = ""
            payload["note"] = (
                "This agent was not started by GridVibe, so it has no pane. "
                "Read tools still work; launched panes must name a workspace."
            )
        allowed, refusal = depth_budget(identity, max_agent_depth)
        payload["may_launch_panes"] = allowed
        if not allowed:
            payload["launch_refusal"] = refusal
        return payload

    if name == "create_workspace":
        label = _text(args, "label")
        if not label:
            raise ToolArgumentError("A workspace needs a 'label'.")
        return {"workspace": client.create_workspace(label)}

    if name == "launch_panes":
        allowed, refusal = depth_budget(identity, max_agent_depth)
        if not allowed:
            return {"error": refusal, "kind": "depth_limit", "agent_depth": identity.agent_depth}
        body = build_launch_request(args, identity=identity)
        result = client.launch(body)
        result["request"] = body
        return result

    if name == "open_window":
        workspace_id = _text(args, "workspace_id")
        if not workspace_id:
            raise ToolArgumentError("open_window needs a 'workspace_id'.")
        return window_opener(client, workspace_id, _text(args, "group_id"))

    if name == "split_pane":
        session_id = _text(args, "session_id")
        if not session_id:
            raise ToolArgumentError("split_pane needs a 'session_id'.")
        return client.split(session_id, {})

    # Unreachable: dispatch() checks the name first.
    return {"error": f"GridVibe has no tool named '{name}'.", "kind": "unknown_tool"}
