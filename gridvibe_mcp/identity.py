"""Which pane is this agent sitting in, and how deep is it allowed to go.

The sidecar takes no identity arguments. It is a grandchild of the pane
process -- pane shell -> agent CLI -> sidecar -- so the pane's environment is
already its environment, through two levels of ordinary inheritance. An agent
started by hand outside GridVibe inherits none of it, and ``whoami`` says so
rather than guessing.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

#: Every variable GridVibe injects at a local pane's spawn. Named once here so
#: the sidecar, the injector and the WSLENV forward list cannot drift.
IDENTITY_VARIABLES = (
    "GRIDVIBE_URL",
    "GRIDVIBE_SESSION_ID",
    "GRIDVIBE_GROUP_ID",
    "GRIDVIBE_WORKSPACE_ID",
    "GRIDVIBE_AGENT_DEPTH",
)

#: How many generations of agent-launching-agent are allowed. ``terminal.
#: max_sessions`` caps a group, not a fleet: without this, an agent that can
#: start agents can start agents that start agents.
DEFAULT_MAX_AGENT_DEPTH = 2


@dataclass(frozen=True)
class PaneIdentity:
    """What the environment says about the pane this sidecar was started in."""

    url: str = ""
    session_id: str = ""
    group_id: str = ""
    workspace_id: str = ""
    agent_depth: int = 0
    #: False when nothing named a depth -- an agent started by hand. Kept apart
    #: from ``agent_depth == 0`` because they mean different things to a reader.
    depth_stated: bool = False

    @property
    def inside_gridvibe(self) -> bool:
        """Whether GridVibe started the pane this agent is running in."""
        return bool(self.session_id)

    @property
    def child_depth(self) -> int:
        """The depth to stamp on a pane this agent creates."""
        return self.agent_depth + 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inside_gridvibe": self.inside_gridvibe,
            "url": self.url,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "workspace_id": self.workspace_id,
            "agent_depth": self.agent_depth,
            "agent_depth_stated": self.depth_stated,
        }


def _text(environ: Mapping[str, str], name: str) -> str:
    return str(environ.get(name) or "").strip()


def read_identity(
    environ: Optional[Mapping[str, str]] = None,
    *,
    default_url: str = "",
) -> PaneIdentity:
    """Read the pane identity out of a process environment.

    Missing variables degrade rather than guess: an agent outside GridVibe gets
    an identity whose ``inside_gridvibe`` is False, and a depth that was never
    stated reads as 0 while saying it was never stated.
    """
    source = os.environ if environ is None else environ
    raw_depth = _text(source, "GRIDVIBE_AGENT_DEPTH")
    depth = 0
    depth_stated = False
    if raw_depth:
        try:
            depth = max(0, int(raw_depth))
            depth_stated = True
        except ValueError:
            depth = 0
            depth_stated = False
    return PaneIdentity(
        url=_text(source, "GRIDVIBE_URL") or str(default_url or "").strip(),
        session_id=_text(source, "GRIDVIBE_SESSION_ID"),
        group_id=_text(source, "GRIDVIBE_GROUP_ID"),
        workspace_id=_text(source, "GRIDVIBE_WORKSPACE_ID"),
        agent_depth=depth,
        depth_stated=depth_stated,
    )


def depth_budget(
    identity: PaneIdentity,
    maximum: int = DEFAULT_MAX_AGENT_DEPTH,
) -> Tuple[bool, str]:
    """Whether this agent may launch panes, and the sentence if it may not.

    A guardrail against a runaway loop, not against an adversary: the agent it
    constrains can unset the variable. The server-side per-group counter that
    would close that is a later phase, and the sidecar README says so.
    """
    limit = max(0, int(maximum))
    if identity.agent_depth >= limit:
        return False, (
            f"This agent is {identity.agent_depth} levels deep in agent-launched "
            f"panes, and GridVibe stops at {limit}. Ask the person running "
            "GridVibe to launch the pane instead."
        )
    return True, ""
