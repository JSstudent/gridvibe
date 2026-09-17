"""The gates a pane transition passes when an *agent* asked for it.

Three transactions are now reachable from a tool -- relaunching a pane into
another agent (`web/session_shell.py`), switching what kind of pane it is
(`web/session_modes.py`), and clearing it (`web/session_clear.py`) -- and each
of them does something to a pane somebody else may be looking at. The rule that
bounds all three is the same, so it lives here rather than three times:

* **Self.** Never the pane the request came from. An agent does not reach
  through a tool call to end, re-mode or type into its own pane.
* **Lineage.** Only the panes this agent's own pane created, and only while
  that caller pane is still open. A creator id naming nothing live names
  nothing at all.

What is *not* shared is the third gate, because it is a different question in
each transaction: a relaunch refuses anything that is not a plain terminal, a
mode switch refuses a pane with an agent running in it, and a clear refuses
both. Each transaction states its own, in its own module, using the gate names
below so every refusal reads the same way.

`override` waives lineage and never self. The waiver is not a decision this
module makes: it arrives in the payload because the *calling agent* stated it,
and it should state it only when the person it is talking to asked for this
specific pane in this conversation. The residual risk is stated rather than
designed away -- a pane that passes every gate can still be sitting mid-command,
and liveness is a heuristic (`web/agent_activity.py`) rather than a fact about
the foreground process.

No Flask, no HTTP, and no error type of its own crossing a route boundary:
`PaneGateRefusal` carries a message and the status the route should answer, and
each transaction translates it into the error type its own route already maps.
"""

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from web.app import session_manager

logger = logging.getLogger(__name__)

#: Named so a refusal says which gate failed. An agent told only "refused"
#: tries the same call again; an agent told "the lineage gate" offers a split.
MODE_GATE = "mode"
LINEAGE_GATE = "lineage"
SELF_GATE = "self"

#: Every waivable refusal ends with this, so an agent relaying one has been
#: told what the escape hatch is and who has to ask for it.
OVERRIDE_TAIL = "unless the user explicitly asked to override this pane"


@dataclass(frozen=True)
class GateWording:
    """What one transaction calls itself, so a shared refusal reads naturally.

    Four phrases rather than one verb, because the self gate's reason is not a
    conjugation of the lineage gate's: a relaunch ends a process, a clear types
    at a prompt, and saying "would end this agent" about a clear would be a
    sentence that is not true.
    """

    #: "a relaunch" -- the thing that has to name the pane asking for it.
    request_noun: str
    #: The whole sentence after "This is the pane the request came from."
    self_reason: str
    #: "relaunch it" -- what a tool does not do to a pane it did not create.
    act: str
    #: "relaunches" -- what an agent does only to the panes it created.
    acts: str


@dataclass(frozen=True)
class AgentPaneRequest:
    """Who asked, and whether they said the user asked for this pane by name."""

    caller_session_id: str
    override: bool


class PaneGateRefusal(Exception):
    """One gate refused, with the status the route should answer.

    Deliberately not one of the transaction error types: this module is below
    all three of them, and each translates rather than re-raises so its own
    route keeps mapping exactly one exception.
    """

    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def refusal_text(gate: str, message: str) -> str:
    """The one refusal shape: which gate failed, then why."""
    return f"[{gate} gate] {message}"


def refuse(gate: str, message: str) -> PaneGateRefusal:
    """One gate's refusal, ready to raise. 403: it is a denial, not a mistake.

    Public because each transaction's own third gate raises it too, so every
    refusal a tool can meet -- shared or not -- comes out of one factory and
    reads the same way.
    """
    return PaneGateRefusal(refusal_text(gate, message), 403)


def read_agent_request(
    payload: Mapping[str, Any],
    wording: GateWording,
) -> AgentPaneRequest:
    """Read who is asking, or refuse a request that names nobody.

    Refused here rather than by a gate, and with 400 rather than 403: a request
    that cannot name a caller is malformed, not denied.
    """
    data = payload or {}
    caller_session_id = str(data.get("requested_by_session_id") or "").strip()
    if not caller_session_id:
        raise PaneGateRefusal(
            f"requested_by_session_id is required: {wording.request_noun} has "
            "to name the pane asking for it.",
            400,
        )
    return AgentPaneRequest(caller_session_id, bool(data.get("override")))


def check_caller(
    session_id: str,
    request: AgentPaneRequest,
    wording: GateWording,
) -> None:
    """The self gate, then the caller's own liveness. Neither is waivable.

    The self gate is first because it is the one refusal that holds however the
    caller pane is doing: an agent that reached its own pane would end, re-mode
    or type into itself in the middle of the call that asked.
    """
    if session_id == request.caller_session_id:
        raise refuse(
            SELF_GATE,
            f"This is the pane the request came from. {wording.self_reason}",
        )

    if session_manager.get_session(request.caller_session_id) is None:
        raise refuse(
            LINEAGE_GATE,
            "The pane this request came from is no longer open, so GridVibe "
            "cannot tell whether it created this one.",
        )


def check_lineage(
    session: Any,
    request: AgentPaneRequest,
    wording: GateWording,
) -> None:
    """The lineage gate: a pane this agent's own pane created, or `override`.

    A pane that existed before a GridVibe restart carries no creator and is
    refused for that reason, which is the honest answer -- GridVibe does not
    know who made it, so it does not guess.
    """
    creator = str(getattr(session, "created_by_session_id", "") or "")
    if not request.override:
        if not creator:
            raise refuse(
                LINEAGE_GATE,
                f"This pane was not created by an agent, so a tool does not "
                f"{wording.act}. Split off a new pane instead, {OVERRIDE_TAIL}.",
            )
        if creator != request.caller_session_id:
            raise refuse(
                LINEAGE_GATE,
                f"This pane was created by a different pane. An agent "
                f"{wording.acts} only the panes it created itself, "
                f"{OVERRIDE_TAIL}.",
            )
        return

    if not creator or creator != request.caller_session_id:
        # Logged rather than counted: the waiver is the interesting event, and
        # the record has to name both panes for it to be readable afterwards.
        logger.info(
            "Pane gate override session_id=%s act=%s "
            "requested_by_session_id=%s creator=%s",
            str(getattr(session, "session_id", "") or "-"),
            wording.act,
            request.caller_session_id,
            creator or "-",
        )
