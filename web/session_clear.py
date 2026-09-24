"""Clearing one pane, asked for by an agent rather than pressed by a reader.

The header's Clear button does two things at once, and only one of them is the
server's. It resets the pane's xterm and unwinds the mouse reporting a crashed
TUI left armed -- both of which need the live terminal, and neither of which
exists outside a page -- and it purges the rolling replay buffer, which is
GridVibe's own and nothing else's.

So this transaction owns the half it can own and *asks* for the other. It
purges the buffer, which is what decides what the next window to show this pane
replays, and then broadcasts `terminal_cleared` into the pane's room. Every
open window showing that pane runs the Clear button's own handler when it
arrives -- the same function, so the two paths cannot drift, and the shell's
clear command keeps its single owner on the page that knows the pane's shell
family.

That split is also why the answer this returns is worded the way it is: the
purge is a *fact*, the display reset is a *request*. A pane nobody is looking
at gets a clean replay buffer and no typed command, which is the honest outcome
rather than a pretended one.

The gates are `web/pane_gates.py`'s self and lineage, plus this transaction's
own kind rule: only a plain terminal pane is cleared by a tool. An explorer or
browser pane has no terminal to clear (the button refreshes them instead, which
is a different action wearing the same icon), and a pane with an agent running
in it is refused without `override` because the clear types at a prompt -- and
behind a running agent, a prompt is that agent's own input.

Nothing here imports `web.api`: that would cycle. The two effects it cannot own
-- the replay buffer and the Socket.IO emit -- arrive as `ClearEffects`,
resolved by the route from the module the tests already patch.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from web.app import session_manager
from web.pane_gates import (
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

#: How this transaction names itself inside a shared refusal. The self reason
#: is not "would end this agent": clearing does not end anything, it erases the
#: scrollback and types at the prompt -- which for the calling pane is the
#: agent's own input.
CLEAR_WORDING = GateWording(
    request_noun="a clear",
    self_reason=(
        "Clearing it would erase this agent's own display and type at its "
        "own prompt."
    ),
    act="clear it",
    acts="clears",
)

#: The pane kinds a tool may clear. A pane whose startup mode is neither is one
#: the button would have refreshed rather than cleared.
_CLEARABLE_STARTUP_MODES = ("terminal", "agent")


class ClearTransitionError(Exception):
    """One clear refused, with the status the route answers.

    Narrow on purpose, exactly as `ModeTransitionError` and
    `ShellTransitionError` are: the route maps `message` and `status_code` onto
    an error response and nothing else.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        #: A gate refusal's structure -- which gate, whether `override` could
        #: waive it, and the question to ask -- carried to the route's body.
        self.details = dict(details or {})


@dataclass(frozen=True)
class ClearEffects:
    """The two side effects the transaction cannot own itself.

    Passed in rather than imported because they belong to the connection
    registry and the Socket.IO server `web/api.py` holds, and resolving them in
    the route body keeps them as patchable as every other effect here.
    """

    purge_buffer: Callable[[str], Any]
    broadcast_cleared: Callable[[str], Any]


def _clear_question(facts: Any) -> str:
    """The question a calling agent puts to the person before an override."""
    typed = (
        f", and types GridVibe's clear command at the {facts.agent} agent's "
        "own prompt"
        if facts.agent
        else ""
    )
    return (
        f"Clearing {facts.name} erases its scrollback, which cannot be read "
        f"back{typed}. Override {facts.name}?"
    )


def apply_agent_pane_clear(
    session_id: str,
    payload: Dict[str, Any],
    effects: ClearEffects,
) -> Dict[str, Any]:
    """Clear one pane on behalf of a *calling agent's* pane.

    Every gate is checked before the buffer is touched, so a refusal leaves the
    pane's replay buffer exactly as it was found and no window is told anything.
    """
    # One translation point for the whole gate sequence: every refusal below
    # is a `PaneGateRefusal` carrying the status the route should answer, and
    # this is where it becomes the one exception `web/api.py` maps.
    session = None
    try:
        request = read_agent_request(payload, CLEAR_WORDING)

        session = session_manager.get_session(session_id)
        if not session:
            raise PaneGateRefusal("Session not found", 404)

        check_caller(session_id, request, CLEAR_WORDING)

        startup_mode = str(getattr(session, "startup_mode", "") or "")
        if startup_mode not in _CLEARABLE_STARTUP_MODES:
            raise refuse(
                MODE_GATE,
                f"This pane is a {startup_mode or 'non-terminal'} pane, so it "
                "has no terminal to clear. Switch it to terminal mode first.",
            )
        if startup_mode == "agent" and not request.override:
            raise refuse(
                MODE_GATE,
                "This pane is running an agent, and a clear types at the "
                "prompt -- which here is that agent's own input. Unless the "
                "user explicitly asked to override this pane, leave it alone.",
                waivable=True,
            )

        check_lineage(session, request, CLEAR_WORDING)
    except PaneGateRefusal as exc:
        attach_confirmation(exc, session, _clear_question)
        raise ClearTransitionError(
            exc.message, exc.status_code, exc.details()
        ) from exc

    effects.purge_buffer(session_id)
    effects.broadcast_cleared(session_id)
    logger.info(
        "Agent-requested pane clear session_id=%s "
        "requested_by_session_id=%s override=%s",
        session_id,
        request.caller_session_id,
        request.override,
    )
    return {
        "session_id": session_id,
        # A fact and a request, kept apart on purpose: the buffer is gone, and
        # the windows showing this pane have been told to reset. A pane nobody
        # has open resets nothing, and this payload does not claim otherwise.
        "buffer_purged": True,
        "display_reset_requested": True,
    }
