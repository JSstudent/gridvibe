"""The pane relaunch transaction: which shell a pane runs, and which agent.

Extracted from ``web/api.py``'s ``change_session_shell()`` when the header's
reset dropdown grew a second dimension. The route used to answer one question
-- "restart this Local Repo pane under another shell family" -- and now answers
two: the shell family (cmd / PowerShell / a WSL distro) *and* the agent CLI the
replacement shell starts, which is the one dimension an SSH pane has as well.
Two dimensions in one transaction is the shape ``web/session_modes.py`` was cut
out for, so this module follows it: ``web/api.py`` keeps request parsing and
status mapping, and the transaction lives here.

The two dimensions are independent, and each is a *tri-state* read off the
payload rather than a value with a default:

- ``shell`` absent leaves the pane's shell family alone; present, it is
  validated as a local Windows shell family and refuses an SSH pane, which has
  no such family to pick.
- ``agent`` absent leaves the pane's agent alone -- that is what the shell rows
  meant before this module existed, and the old shell-switch behaviour is
  exactly "shell stated, agent not". ``""`` is a *stated* choice of no agent,
  and only that clears a pane's agent metadata.

A stated agent is also checked before anything moves: the launcher's own
registry-driven preflight (``_agent_absent_reason()``) is asked about the
environment the replacement shell will start in, and a binary that is not there
refuses the relaunch. The pane in front of the reader is already running, so a
refusal costs nothing -- unlike at launch, where there is no pane yet to keep.

Nothing here imports ``web.api``: that would cycle, and the transaction has no
business knowing what a Flask response looks like. The three effects it cannot
own -- closing the pane's connection, broadcasting its status, starting the
replacement shell -- arrive as ``ShellTransitionEffects``, resolved by the
route from the module the tests already patch.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from sessions.manager import SessionStatus, _normalize_agent_depth
from web.agents import (
    AGENT_REGISTRY,
    _agent_absent_reason,
    _agent_supports_mcp,
    _normalize_agent_key,
)
from web.app import session_manager
from web.explorer import _is_browser_session, _is_explorer_session
from web.pane_gates import (  # noqa: F401 - LINEAGE_GATE/SELF_GATE re-exported
    LINEAGE_GATE,
    MODE_GATE,
    SELF_GATE,
    GateWording,
    PaneGateRefusal,
    check_caller,
    check_lineage,
    read_agent_request,
    refuse,
)
from web.terminal_io import (
    LOCAL_SHELL_KINDS,
    _local_shell_display_name,
    _local_shell_kind,
    _normalize_local_shell_kind,
    effective_directory,
)

logger = logging.getLogger(__name__)

#: A pane with no shell process of its own cannot be relaunched into one.
_PANE_MODES_WITH_A_SHELL = ("ssh", "wsl")


class ShellTransitionError(Exception):
    """One pane relaunch refused, with the status the route answers.

    Narrow on purpose, exactly as ``ModeTransitionError`` is: the route maps
    ``message`` and ``status_code`` onto an error response and nothing else.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ShellTransitionEffects:
    """The three side effects the transaction cannot own itself.

    Passed in rather than imported because they belong to the Socket.IO server
    and the connection registry ``web/api.py`` holds. Resolving them in the
    route body is also what keeps them as patchable as they were before the
    move.
    """

    close_connection: Callable[..., Any]
    broadcast_status: Callable[[str], Any]
    start_connector: Callable[[str], Any]


def _requested_shell(payload: Dict[str, Any]) -> Optional[str]:
    """Return the requested shell family, or ``None`` when none was stated.

    Absent means "leave the shell alone", which is what an agent-only relaunch
    asks for -- and what every SSH pane asks for, since it has no local shell
    family to name.
    """
    if payload.get("shell") is None:
        return None
    shell_kind = _normalize_local_shell_kind(payload.get("shell"))
    if shell_kind not in LOCAL_SHELL_KINDS:
        raise ShellTransitionError("shell must be 'cmd', 'powershell', or 'wsl'")
    return shell_kind


def _requested_agent(payload: Dict[str, Any]) -> Optional[str]:
    """Return the requested agent key, ``""`` for none, or ``None`` if unstated.

    Only registry keys are accepted. The launcher's ``other`` free-text agent
    needs an input field to go with it and the pane menu has none, so a pane
    already running a custom agent can be sent back to a plain shell or moved
    onto a registered one -- it just cannot mint a new custom command here.
    """
    if payload.get("agent") is None:
        return None
    agent_key = _normalize_agent_key(payload.get("agent"))
    if not agent_key:
        return ""
    if agent_key not in AGENT_REGISTRY:
        raise ShellTransitionError("agent must be a known agent CLI or an empty value")
    return agent_key


def _requested_mcp(payload: Dict[str, Any]) -> Optional[bool]:
    """Return the requested MCP choice, or ``None`` when none was stated.

    The third tri-state, read exactly like the other two: absent leaves the
    pane's MCP setting alone -- which for an agent change means it follows the
    agent, the same rule auto mode has.
    """
    value = payload.get("mcp")
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ShellTransitionError("mcp must be true or false")
    return value


def _pane_agent_key(session: Any) -> str:
    """Return the agent a pane runs now, or ``""`` for a plain shell.

    A startup command is not an agent: a pane launched with ``npm run dev`` has
    no agent selected, so a stated ``""`` leaves it exactly as it is rather than
    treating its command as something to clear.
    """
    if str(getattr(session, "startup_mode", "") or "") != "agent":
        return ""
    return _normalize_agent_key(
        getattr(session, "agent_selection", "")
    ) or _normalize_agent_key(getattr(session, "custom_agent", ""))


def _agent_preflight_config(
    session: Any,
    shell_kind: Optional[str],
    distribution: str,
) -> Dict[str, Any]:
    """Describe the environment the replacement shell will start in.

    The preflight is asked about where the pane is *going*, not where it is: a
    row under the WSL chevron relaunches into that distro, so its agent has to
    be looked for there. An unstated shell family means the pane keeps the one
    it has, which is also the only thing an SSH pane can mean.
    """
    if shell_kind is None:
        use_wsl = bool(getattr(session, "use_wsl", False))
        use_powershell = bool(getattr(session, "use_powershell", False))
        target_distribution = str(getattr(session, "distribution", "") or "").strip()
    else:
        use_wsl = shell_kind == "wsl"
        use_powershell = shell_kind == "powershell"
        target_distribution = distribution

    return {
        "host": str(getattr(session, "host", "") or ""),
        "username": str(getattr(session, "username", "") or ""),
        "password": getattr(session, "password", "") or "",
        "port": getattr(session, "port", 22),
        "directory": str(getattr(session, "directory", "") or ""),
        "distribution": target_distribution,
        "use_wsl": use_wsl,
        "use_powershell": use_powershell,
    }


def _refuse_an_agent_that_is_not_installed(
    session: Any,
    agent_key: str,
    shell_kind: Optional[str],
    distribution: str,
) -> None:
    """Refuse a relaunch onto an agent whose binary is not on the target.

    The launcher answers this before a pane opens, and the pane menu is the
    other way into the same launch, so it asks the same question here --
    before anything is mutated, closed or restarted. Refusing is what the
    launcher cannot do: the pane the reader is looking at is already running,
    so the honest outcome is that nothing happens and the menu says why,
    rather than a plain shell wearing the agent's name until its exit is
    observed.

    Only "the binary is not there" refuses. A check that could not run says
    nothing about the binary, so the relaunch proceeds exactly as it did
    before this guard existed and the reader gets the shell's own error.
    """
    reason = _agent_absent_reason(
        agent_key,
        str(getattr(session, "mode", "") or ""),
        _agent_preflight_config(session, shell_kind, distribution),
    )
    if not reason:
        return
    logger.info("Refusing pane relaunch onto a missing agent: %s", reason)
    raise ShellTransitionError(f"{reason} This pane was left as it is.")


def _shell_updates(
    session: Any,
    session_id: str,
    shell_kind: str,
    distribution: str,
) -> Dict[str, Any]:
    """Metadata for a shell-family change, including where the pane is now.

    The replacement shell starts where the pane is, not where it launched --
    and asks the observed sources first, so switching shells mid-build lands in
    the right directory instead of the one the probe could not confirm. The old
    shell's last report is not an observation of the new one, so the observation
    slot is cleared once its answer has been folded into the directory the
    restart will ``cd`` to.
    """
    use_wsl = shell_kind == "wsl"
    use_powershell = shell_kind == "powershell"

    next_directory = session.directory
    observed_directory, _ = effective_directory(session_id, session, allow_probe=True)
    if observed_directory and os.path.isdir(observed_directory):
        next_directory = observed_directory

    return {
        "directory": next_directory,
        "current_directory": None,
        "distribution": distribution,
        "use_wsl": use_wsl,
        "use_powershell": use_powershell,
        "host": _local_shell_display_name(
            use_wsl=use_wsl,
            use_powershell=use_powershell,
            distribution=distribution,
        ),
    }


def _agent_updates(session: Any, agent_key: str) -> Dict[str, Any]:
    """Metadata for an agent change, in either direction.

    Auto mode follows the agent it was chosen for: relaunching the same agent
    under another shell keeps it, and moving to a different agent starts from
    the plain launch, because a flag registered for one CLI says nothing about
    the next one.
    """
    if not agent_key:
        return {
            "startup_mode": "terminal",
            "initial_command_mode": "command",
            "agent_selection": "",
            "custom_agent": "",
            "initial_command": "",
            "agent_auto_mode": False,
            "agent_mcp": False,
        }
    return {
        "startup_mode": "agent",
        "initial_command_mode": "agent",
        "agent_selection": agent_key,
        "custom_agent": "",
        "initial_command": agent_key,
        "agent_auto_mode": (
            bool(getattr(session, "agent_auto_mode", False))
            and _pane_agent_key(session) == agent_key
        ),
        "agent_mcp": (
            bool(getattr(session, "agent_mcp", False))
            and _pane_agent_key(session) == agent_key
        ),
    }


def apply_pane_shell_change(
    session_id: str,
    payload: Dict[str, Any],
    effects: ShellTransitionEffects,
    metadata_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Relaunch one terminal pane under a stated shell family and/or agent.

    The pane keeps its slot, title, group and startup mode metadata that the
    request did not state: only the process behind it is replaced, so the
    startup sequence replays under the new choice. Validation runs before any
    mutation, so a refusal leaves the pane exactly as it was found.

    ``metadata_overrides`` are fields the *caller* owns rather than the payload
    -- today only the agent depth an agent-requested relaunch hands down. They
    are written inside this transaction, before the replacement shell is
    started, because the spawn reads them: setting them afterwards would race
    the connector that is already reading the pane. A refusal writes none of
    them, exactly like a refusal writes none of the payload's own.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise ShellTransitionError("Session not found", 404)

    if getattr(session, "mode", "") not in _PANE_MODES_WITH_A_SHELL:
        raise ShellTransitionError("This pane has no shell to relaunch")

    if _is_explorer_session(session) or _is_browser_session(session):
        raise ShellTransitionError(
            "Switch this pane back to terminal mode before changing its shell"
        )

    shell_kind = _requested_shell(payload)
    agent_key = _requested_agent(payload)
    mcp_enabled = _requested_mcp(payload)

    if shell_kind is not None:
        if session.mode != "wsl":
            raise ShellTransitionError(
                "Shell switching is only available for Local Repo sessions"
            )
        if os.name != "nt":
            raise ShellTransitionError(
                "cmd, PowerShell and WSL shells are only available on Windows hosts"
            )

    distribution = (
        str(payload.get("distribution") or "").strip() if shell_kind == "wsl" else ""
    )

    if agent_key:
        _refuse_an_agent_that_is_not_installed(
            session, agent_key, shell_kind, distribution
        )

    # A valid, explicitly stated dimension is also the relaunch instruction.
    # Its value need not differ from the pane's metadata: the checked menu row
    # is still an action, and selecting it replaces the process behind the pane.
    relaunch_requested = (
        shell_kind is not None or agent_key is not None or mcp_enabled is not None
    )

    updates: Dict[str, Any] = {}
    if shell_kind is not None and (
        shell_kind != _local_shell_kind(session)
        or distribution != str(session.distribution or "").strip()
    ):
        updates.update(_shell_updates(session, session_id, shell_kind, distribution))
    if agent_key is not None and agent_key != _pane_agent_key(session):
        updates.update(_agent_updates(session, agent_key))
    if mcp_enabled is not None:
        # Stated last, so it wins over the value `_agent_updates` carried
        # forward -- and a pane with no agent cannot have MCP, because there is
        # no CLI to register the sidecar with. Nor can a pane whose CLI has no
        # way to be handed one: `agent_mcp` is what paints the pane header's
        # MCP tag and what opens a tunnel on an SSH pane, and neither should
        # happen for a launch line that carries nothing. Dropped rather than
        # refused, exactly as a stated `mcp` on a pane with no agent is.
        resolved_agent = agent_key if agent_key is not None else _pane_agent_key(session)
        updates["agent_mcp"] = (
            bool(mcp_enabled)
            and bool(resolved_agent)
            and _agent_supports_mcp(resolved_agent)
        )

    # An empty payload states no choice at all and retains the old no-op API
    # behaviour. Every menu row states at least `agent`, so a real selection
    # always crosses the relaunch boundary even when no metadata has changed.
    if not relaunch_requested:
        return session.to_dict()

    if metadata_overrides:
        updates.update(metadata_overrides)
    if updates:
        session_manager.update_session_metadata(session_id, **updates)
    logger.info(
        "Pane relaunch session_id=%s shell=%s distribution=%s agent=%s directory=%s",
        session_id,
        shell_kind or "-",
        distribution or "-",
        "-" if agent_key is None else (agent_key or "none"),
        updates.get("directory", session.directory),
    )
    effects.close_connection(session_id, clear_buffer=True)
    session_manager.update_session_status(session_id, SessionStatus.PENDING)
    effects.broadcast_status(session_id)
    effects.start_connector(session_id)

    return session_manager.get_session(session_id).to_dict()


# ==================== The gated half: a relaunch asked for by an agent ========
#
# `apply_pane_shell_change` above is the pane header's own reset dropdown: the
# person looking at the pane pressed it, so there is nobody to check. An agent
# asking for the same relaunch is a different question, because a relaunch ends
# whatever is running in the pane -- the first thing in the MCP surface that
# destroys anything.
#
# The self and lineage gates are `web/pane_gates.py`'s, shared with the two
# other tool-reachable pane transactions. Either of them alone would leave a
# hole the other closes:
#
# * **Mode alone** would let an agent relaunch any plain terminal in the
#   workspace, including one the user opened and is about to type into.
# * **Lineage alone** would let an agent relaunch a pane it created, then handed
#   to the user, who has been working in it for an hour.
#
# The third gate is this transaction's own, and it is the strictest of the
# three: only a plain terminal pane is relaunched by a tool.
#
# The residual risk is stated rather than designed away: a pane that passes all
# three can still be sitting mid-command. The gates establish *who made it* and
# *what kind of pane it is*, not *whether something is running in it*.
# `web/agent_activity.py` reads working/idle from output cadence, which is a
# liveness heuristic and not a fact about the foreground process, so consulting
# it would trade a clear refusal for a guess.
#
# **`override` waives lineage and "already an agent", never self or kind.**
# Practice surfaced a pane neither gate was written for: one the *user* made by
# hand (so lineage refuses it) or reused from an earlier agent run (so it is
# already flagged as an agent pane, even though nothing is running in it) --
# exactly the pane a person means when they say "override the bottom terminal
# and start codex there." An agent cannot decide this for itself: `override`
# only does anything when the calling agent states it, and it should state it
# only when the person it is talking to said so for *this* pane, in *this*
# turn -- never because a file it read, or another pane's output, told it to.
# It waives lineage and the "already running an agent" refusal and nothing
# else: an explorer or browser pane is still never relaunched by a tool (that
# is a mode switch, a different transaction entirely -- see
# `web/session_modes.py`), and the caller can still never relaunch itself.

#: How this transaction names itself inside a shared refusal.
RELAUNCH_WORDING = GateWording(
    request_noun="a relaunch",
    self_reason="Relaunching it would end this agent in the middle of the call.",
    act="relaunch it",
    acts="relaunches",
)


def apply_agent_pane_relaunch(
    session_id: str,
    payload: Dict[str, Any],
    effects: ShellTransitionEffects,
) -> Dict[str, Any]:
    """Relaunch one pane into an agent, on behalf of a *calling agent's* pane.

    Every gate is checked before anything is mutated, closed or restarted, so a
    refusal leaves the pane exactly as it was found -- which is what the
    whole-pane snapshot assertions in the tests pin.
    """
    # One translation point for the whole gate sequence: every refusal below
    # is a `PaneGateRefusal` carrying the status the route should answer, and
    # this is where it becomes the one exception `web/api.py` maps.
    try:
        request = read_agent_request(payload, RELAUNCH_WORDING)

        session = session_manager.get_session(session_id)
        if not session:
            raise PaneGateRefusal("Session not found", 404)

        check_caller(session_id, request, RELAUNCH_WORDING)

        startup_mode = str(getattr(session, "startup_mode", "") or "")
        already_agent = startup_mode == "agent" and bool(_pane_agent_key(session))
        if startup_mode not in ("terminal", "agent"):
            raise refuse(
                MODE_GATE,
                f"This pane is a {startup_mode or 'non-terminal'} pane. Only a "
                "plain terminal pane is relaunched by a tool; split off a new "
                "pane instead.",
            )
        if already_agent and not request.override:
            raise refuse(
                MODE_GATE,
                "This pane is already running an agent. Only a plain terminal "
                "pane is relaunched by a tool; split off a new pane instead, "
                "unless the user explicitly asked to override this pane.",
            )

        check_lineage(session, request, RELAUNCH_WORDING)

        # Read again rather than carried down from `check_caller`: nothing held
        # the caller open in between, and a caller that has gone is the same
        # fact that gate already refuses on. Without this,
        # `getattr(None, "agent_depth", 0) + 1` is 1, so a caller closing
        # mid-call silently *resets* the chain's budget instead of ending it.
        caller = session_manager.get_session(request.caller_session_id)
        if caller is None:
            raise refuse(
                LINEAGE_GATE,
                "The pane this request came from closed while its request was "
                "being checked, so GridVibe cannot tell what depth budget the "
                "replacement agent should inherit.",
            )
    except PaneGateRefusal as exc:
        raise ShellTransitionError(exc.message, exc.status_code) from exc

    # Past the gates this is the ordinary relaunch, with the ordinary refusals:
    # an unknown agent key, a binary that is not installed, a pane with no shell.
    # The agent it is going to run inherits the caller's budget, so a chain of
    # agents-turning-panes-into-agents still runs out.
    relaunch = {
        key: payload[key] for key in ("shell", "agent", "mcp", "distribution")
        if key in payload
    }
    return apply_pane_shell_change(
        session_id,
        relaunch,
        effects,
        # Bounded by the same normalizer every other write of this field uses
        # (`create_session`, and the split route). `update_session_metadata` is
        # a raw `setattr` over an allowlist and normalizes nothing, so without
        # it this is the one write path that could persist a depth past
        # `_MAX_AGENT_DEPTH` into `runtime_state.json`.
        {"agent_depth": _normalize_agent_depth(int(getattr(caller, "agent_depth", 0)) + 1)},
    )
