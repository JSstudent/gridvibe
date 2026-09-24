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

**These gates constrain an agent that follows its instructions. They are not a
security boundary, and cannot be one.** Both of them are evaluated against
`requested_by_session_id`, which is an input rather than a proof. On the stdio
path it comes from `GRIDVIBE_SESSION_ID`, read out of the sidecar's own
environment -- inherited from the agent CLI, which is the process being
constrained and can set it to any value before the sidecar starts -- and
`list_panes` publishes every live session id, so the values are not secret
either. An agent that stated another pane's id would be gated against *that*
pane: self would protect it rather than its own, and lineage would admit
everything that pane created. (The token path is not spoofable this way: the
caller comes out of the registry in `web/mcp_http.py`, not out of the caller.
It is the minority path, and nothing below assumes it.)

One level up, the same is true more plainly: a local agent pane runs with the
user's own privileges and GridVibe's API on loopback, where
`DELETE /api/sessions/<id>` and the ungated twins of all three of these
transactions pass no gate at all. `identity.depth_budget()` states its
equivalent weakness in the same words, and for the same reason.

What the gates do buy is real and worth keeping: an agent following its
instructions does not end, re-mode or type into a pane it did not make, and one
that has been prompt-injected has to leave the tool surface and start
constructing HTTP requests to get any further. That is a meaningfully higher
bar. It is not "cannot".

No Flask, no HTTP, and no error type of its own crossing a route boundary:
`PaneGateRefusal` carries a message and the status the route should answer, and
each transaction translates it into the error type its own route already maps.

**A refusal is also a structure, so an agent can ask before it overrides.**
Every gate refusal names its ``gate`` and whether ``override`` could ever
``waive`` it. A waivable one carries a ``confirm`` block -- which pane, what is
running there and GridVibe's last reading of it, and the question to put to the
person, built here from the live registry so every agent asks the same question
with the same facts. A refusal nothing can waive (self, an explorer or browser
pane, another machine) carries no question: the only answers are a split or no.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from web.app import session_manager

logger = logging.getLogger(__name__)

#: Named so a refusal says which gate failed. An agent told only "refused"
#: tries the same call again; an agent told "the lineage gate" offers a split.
MODE_GATE = "mode"
LINEAGE_GATE = "lineage"
SELF_GATE = "self"
#: Only a verb carrying a task meets this one: a task runs on the caller's own
#: machine, and nothing -- `override` included -- waives that.
MACHINE_GATE = "machine"

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
    route keeps mapping exactly one exception -- carrying :meth:`details` with
    it, so the structure survives the translation.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 403,
        *,
        gate: str = "",
        waivable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.gate = gate
        self.waivable = bool(waivable)
        #: Filled in by the transaction that knows what it was asked to do,
        #: for a waivable refusal only -- see :func:`confirmation`.
        self.confirm: Optional[Dict[str, Any]] = None

    def details(self) -> Dict[str, Any]:
        """The structured half of a refusal, for the route's JSON body."""
        if not self.gate:
            return {}
        payload: Dict[str, Any] = {"gate": self.gate, "waivable": self.waivable}
        if self.waivable and self.confirm:
            payload["confirm"] = self.confirm
        return payload


def refusal_text(gate: str, message: str) -> str:
    """The one refusal shape: which gate failed, then why."""
    return f"[{gate} gate] {message}"


def refuse(gate: str, message: str, *, waivable: bool = False) -> PaneGateRefusal:
    """One gate's refusal, ready to raise. 403: it is a denial, not a mistake.

    Public because each transaction's own third gate raises it too, so every
    refusal a tool can meet -- shared or not -- comes out of one factory and
    reads the same way. ``waivable`` is true only where ``override`` would
    actually change the answer.
    """
    return PaneGateRefusal(refusal_text(gate, message), 403, gate=gate, waivable=waivable)


# ---------------- the confirmation question ----------------


@dataclass(frozen=True)
class PaneFacts:
    """What the person is asked about: which pane, and what runs in it."""

    session_id: str
    title: str
    index: Optional[int]
    agent: str
    activity: str

    @property
    def name(self) -> str:
        if self.title:
            return self.title
        return f"pane {self.index + 1}" if self.index is not None else "this pane"


def _pane_agent(session: Any) -> str:
    if str(getattr(session, "startup_mode", "") or "") != "agent":
        return ""
    return str(
        getattr(session, "agent_selection", "")
        or getattr(session, "custom_agent", "")
        or ""
    ).strip().lower()


def _pane_activity(session_id: str) -> str:
    """GridVibe's last working/idle reading of a pane, or ``""``.

    Read from the same observation the dashboard publishes, and imported late:
    this module sits below `web/terminal_io.py`'s users, and the snapshot takes
    `connection_lock`, so it is read here with no manager lock held.
    """
    try:
        from web.agent_activity import describe_agent_activity
        from web.terminal_io import agent_activity_snapshot

        record = agent_activity_snapshot().get(session_id)
    except Exception:  # pragma: no cover - a reading is never worth a failure
        return ""
    if record is None:
        return ""
    return str(describe_agent_activity(record, time.time()).get("state") or "")


def pane_facts(session: Any) -> PaneFacts:
    """The facts a confirmation question states, read from the live registry."""
    session_id = str(getattr(session, "session_id", "") or "")
    index: Optional[int] = None
    group_id = str(getattr(session, "group_id", "") or "")
    if group_id:
        ordered = [
            str(getattr(item, "session_id", "") or "")
            for item in session_manager.get_group_sessions(group_id)
        ]
        index = ordered.index(session_id) if session_id in ordered else None
    agent = _pane_agent(session)
    return PaneFacts(
        session_id=session_id,
        title=str(getattr(session, "title", "") or ""),
        index=index,
        agent=agent,
        activity=_pane_activity(session_id) if agent else "",
    )


def what_ends(facts: PaneFacts) -> str:
    """What a replacement ends, in the words the question uses."""
    if not facts.agent:
        return "the shell there and anything running in it"
    reading = (
        f", which GridVibe last read as {facts.activity},"
        if facts.activity and facts.activity != "unknown"
        else ""
    )
    return f"the {facts.agent} agent there{reading} and its conversation"


def confirmation(facts: PaneFacts, question: str) -> Dict[str, Any]:
    """The ``confirm`` block a waivable refusal carries."""
    return {
        "pane": {
            "session_id": facts.session_id,
            "title": facts.title,
            "index": facts.index,
        },
        "ends": {
            "agent": facts.agent or None,
            "activity": facts.activity or None,
        },
        "question": question,
    }


def attach_confirmation(
    refusal: PaneGateRefusal,
    session: Any,
    question_for: Any,
) -> None:
    """Give a waivable refusal its question. ``question_for(facts)`` words it.

    A refusal nothing can waive gets none: offering a person a choice that
    `override` cannot honour would be asking them for nothing.
    """
    if not refusal.waivable or session is None:
        return
    facts = pane_facts(session)
    refusal.confirm = confirmation(facts, question_for(facts))


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
                waivable=True,
            )
        if creator != request.caller_session_id:
            raise refuse(
                LINEAGE_GATE,
                f"This pane was created by a different pane. An agent "
                f"{wording.acts} only the panes it created itself, "
                f"{OVERRIDE_TAIL}.",
                waivable=True,
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
