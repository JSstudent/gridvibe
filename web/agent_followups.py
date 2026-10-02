"""Handing a running agent its next task: ``send_task`` and ``wait_for_task``.

A task handed with ``split_pane``, ``launch_panes`` or ``set_pane_agent``
starts a new agent. A back-and-forth -- ask, read the report, answer, ask
again -- needs the *same* agent to hear the next message, and relaunching it
each round throws away everything it knows. A follow-up reaches the agent
already running instead:

* **Only the agent that asked.** The pane's live assignment
  (``web/agent_results.py``) names its requester, and a follow-up from any
  other pane is refused; ``override`` does not change that. This continues a
  conversation the caller started. Reaching an agent somebody else started is
  a different design (an offer the receiver accepts) and is not this.
* **Only after its report.** An agent still working on the last task is
  refused a new one; collect its report first. That is also what keeps one
  live assignment per pane.
* **Only the agent that read the last one.** The pane's connection must still
  hold the handoff that agent read (``web/terminal_io.py``), and it gives that
  up when the agent exits or another is started at the same shell. So a
  relaunch, an agent restarted by hand, a closed connection or a second
  follow-up racing this one is refused rather than handing the task to an
  agent that never saw the conversation.
* **Nothing is typed into the pane.** The receiving agent stands by inside a
  ``wait_for_task`` call -- because its task asked it to -- and the follow-up
  wakes that call. An agent that ended its turn instead picks it up the next
  time it calls ``wait_for_task`` or ``read_handoff``; the sender is told
  which case it is in.
* **The same machine** follows from the first task, which was already held to
  the caller's own machine.

The text follows a task's rules everywhere else: validated and never repaired,
never logged, fetched only by the pane it was handed to.

No Flask here. The route passes the connection bind in, so this module needs
nothing from ``web/terminal_io.py`` and is tested with a stand-in.
"""

import logging
from typing import Any, Callable, Dict, Mapping, Optional

from web.agent_handoffs import (
    FOLLOWUP_STALE_MESSAGE,
    NEXT_NONE,
    NEXT_TASK,
    HandoffError,
    pane_description,
    validate_task,
    worker_description,
)
from web.agent_handoffs import handoffs as agent_handoffs
from web.agent_results import REPORTED
from web.agent_results import results as agent_results
from web.app import session_manager
from web.pane_gates import (
    LINEAGE_GATE,
    MODE_GATE,
    SELF_GATE,
    PaneGateRefusal,
    read_caller_request,
    refuse,
)

logger = logging.getLogger(__name__)

#: ``bind(session_id, previous_handoff_id, create)``: run ``create`` under the
#: pane's connection gate while that connection still holds the previous
#: handoff, and return its id -- or ``None`` when the connection moved on.
ConnectionBind = Callable[[str, str, Callable[[], str]], Optional[str]]

NOT_STANDING_BY_NOTE = (
    "Its agent is not inside a wait_for_task call right now. It receives this "
    "task the next time it calls wait_for_task or read_handoff; if it has "
    "ended its turn, that is when the person next prompts it. When you plan "
    "to send more, ask for that in the task: report, then call wait_for_task."
)


class FollowupError(Exception):
    """A follow-up refused, with the status and structure a route answers."""

    def __init__(self, message: str, status_code: int = 400, details: Optional[Mapping[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def _refused(exc: PaneGateRefusal) -> FollowupError:
    return FollowupError(exc.message, exc.status_code, exc.details())


def _check_followup(worker_session_id: str, payload: Mapping[str, Any]) -> tuple:
    """Every rule a follow-up passes. Returns ``(caller, worker, live assignment)``."""
    request = read_caller_request(payload, "a follow-up task")
    caller_id = request.caller_session_id
    if worker_session_id == caller_id:
        raise refuse(
            SELF_GATE,
            "This is the pane the request came from. A follow-up task goes to "
            "an agent you handed a task to, never to yourself.",
        )
    caller = session_manager.get_session(caller_id)
    if caller is None:
        raise refuse(
            LINEAGE_GATE,
            "The pane this request came from is no longer open, so GridVibe "
            "cannot tell whether it handed this pane its task.",
        )
    worker = session_manager.get_session(worker_session_id)
    if worker is None:
        raise PaneGateRefusal("Session not found", 404)
    if str(getattr(worker, "startup_mode", "") or "") != "agent":
        raise refuse(
            MODE_GATE,
            "This pane is not running an agent, so there is nobody to hand a "
            "follow-up to. Start one with set_pane_agent and a 'task'.",
        )
    live = agent_results.live_assignment(worker_session_id)
    if live is None:
        raise refuse(
            LINEAGE_GATE,
            "No agent you handed a task to is still running in this pane: it "
            "was never handed one through GridVibe, or the agent that read it "
            "was relaunched or its connection closed. A follow-up only reaches "
            "the agent that read your last task; start a new one with a 'task' "
            "on split_pane or set_pane_agent instead.",
        )
    if live["requester_session_id"] != caller_id:
        raise refuse(
            LINEAGE_GATE,
            "This pane's agent was handed its task by a different pane. Only "
            "the agent that handed it a task can send it the next one, and "
            "override does not change that.",
        )
    if live["state"] != REPORTED:
        raise PaneGateRefusal(
            "This pane's agent has not reported on the task you handed it yet. "
            "Collect its report with wait_for_results, then send the next task.",
            409,
        )
    return caller, worker, live


def hand_followup_task(
    worker_session_id: str,
    payload: Mapping[str, Any],
    bind: ConnectionBind,
) -> Dict[str, Any]:
    """Hand the agent running in ``worker_session_id`` its next task.

    The task is validated before any rule, so a task that could never be
    delivered is refused for what it is. Every rule is checked before anything
    is recorded, and the bind re-proves the last one under the connection's
    gate, so a refusal leaves the store and the pane exactly as they were.
    """
    data = payload or {}
    try:
        text = validate_task(data.get("task"))
    except HandoffError as exc:
        raise FollowupError(exc.message, exc.status_code) from exc
    try:
        caller, worker, live = _check_followup(str(worker_session_id or ""), data)
    except PaneGateRefusal as exc:
        raise _refused(exc) from exc

    caller_id = str(getattr(caller, "session_id", "") or "")
    worker_agent = worker_description(worker)
    created = {}

    def create() -> str:
        view = agent_handoffs.create_followup(
            text,
            session_id=worker_session_id,
            previous_handoff_id=live["handoff_id"],
            source_session_id=caller_id,
            worker_agent=worker_agent,
            **pane_description(caller),
        )
        created["view"] = view
        return view.handoff_id

    try:
        handoff_id = bind(worker_session_id, live["handoff_id"], create)
    except HandoffError as exc:
        raise FollowupError(exc.message, exc.status_code) from exc
    if not handoff_id:
        raise FollowupError(FOLLOWUP_STALE_MESSAGE, 409)

    standing_by = agent_handoffs.awaiting_task([worker_session_id]).get(worker_session_id, False)
    state = agent_handoffs.public_state(worker_session_id) or {}
    logger.info(
        "Handoff %s follow-up handed session=%s requested_by_session_id=%s "
        "chars=%d standing_by=%s",
        handoff_id,
        worker_session_id,
        caller_id,
        created["view"].chars,
        standing_by,
    )
    result: Dict[str, Any] = {
        "handed": True,
        "session_id": worker_session_id,
        "delivery": state.get("delivery"),
        "chars": created["view"].chars,
        "agent_standing_by": standing_by,
        "instructions": "Collect its report with wait_for_results.",
    }
    if not standing_by:
        result["note"] = NOT_STANDING_BY_NOTE
    return result


def next_task(session_id: str, wait_seconds: float) -> Dict[str, Any]:
    """What ``wait_for_task`` answers the agent in ``session_id``.

    The task, exactly as ``read_handoff`` would return it (with the receipt its
    report must carry), once one it has not read is there; otherwise why the
    call came back empty and whether to call again.
    """
    outcome, message = agent_handoffs.wait_for_next_task(session_id, wait_seconds)
    if outcome == NEXT_TASK:
        return agent_handoffs.read(session_id)
    if outcome == NEXT_NONE:
        # The message says what to do instead: nothing, or report first.
        return {"handoff": None, "message": message}
    return {
        "handoff": None,
        "timed_out": True,
        "message": "No task has come yet.",
        "instructions": "Call wait_for_task again to keep standing by.",
    }
