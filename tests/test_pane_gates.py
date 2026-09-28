"""The gates three tool-reachable pane transitions share, as rules.

`web/session_shell.py`'s relaunch, `web/session_modes.py`'s mode switch and
`web/session_clear.py`'s clear each do something to a pane somebody else may be
looking at, and each is reached by a tool rather than pressed by a reader. The
self and lineage gates in front of all three are one decision, so they live in
`web/pane_gates.py` and are pinned here once.

What the three transaction suites pin is the *behaviour* — each refusal
asserted on a whole-pane snapshot, in its own route. What this file pins is the
part that is only visible when the three are looked at together:

- **The self gate never reads `override`.** It is the one refusal with no
  escape hatch, and the way to keep it that way is for the check not to take
  the flag at all.
- **Override mode is `override` read off the caller's record, and no more.**
  It waives what a stated `override` waives; self and a gone caller still
  refuse, and the waiver log says which of the two granted it.
- **A malformed request is 400 and a denial is 403.** A request that cannot
  name a caller is not a denied one.
- **Every refusal names its gate, in one shape.** An agent told only "refused"
  calls again.
- **Each transaction's wording is true of that transaction.** The clear does
  not borrow the relaunch's "would end this agent": it ends nothing, and a
  refusal that says otherwise is a sentence an agent will relay to a person.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import pane_gates  # noqa: E402
from web.session_clear import CLEAR_WORDING  # noqa: E402
from web.session_modes import MODE_SWITCH_WORDING  # noqa: E402
from web.session_shell import RELAUNCH_WORDING  # noqa: E402

ALL_WORDINGS = {
    "relaunch": RELAUNCH_WORDING,
    "mode switch": MODE_SWITCH_WORDING,
    "clear": CLEAR_WORDING,
}


class _Pane:
    """The two fields a gate reads off a session, and nothing else."""

    def __init__(self, session_id="pane-2", created_by_session_id=""):
        self.session_id = session_id
        self.created_by_session_id = created_by_session_id


class _Registry:
    """Stands in for the session manager: which panes are open right now."""

    def __init__(self, *open_ids):
        self.open_ids = set(open_ids)

    def get_session(self, session_id):
        return _Pane(session_id) if session_id in self.open_ids else None


def _request(caller="pane-1", override=False):
    return pane_gates.AgentPaneRequest(caller, override)


class _Caller(_Pane):
    """A calling pane as the registry holds it: an agent, maybe with its grant."""

    def __init__(
        self,
        session_id="pane-1",
        *,
        agent_mcp_override=True,
        agent_mcp=True,
        startup_mode="agent",
    ):
        super().__init__(session_id)
        self.agent_mcp_override = agent_mcp_override
        self.agent_mcp = agent_mcp
        self.startup_mode = startup_mode


class _Callers:
    """A registry of callers stated whole, so a grant is on the record or not."""

    def __init__(self, *callers):
        self.panes = {caller.session_id: caller for caller in callers}

    def get_session(self, session_id):
        return self.panes.get(session_id)


def _read(body, registry, wording=CLEAR_WORDING):
    with patch.object(pane_gates, "session_manager", registry):
        return pane_gates.read_agent_request(body, wording)


class ReadAgentRequestTestCase(unittest.TestCase):
    def test_a_request_that_names_no_caller_is_malformed_not_denied(self):
        """400, not 403: nobody was refused, the body was unusable."""
        with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
            pane_gates.read_agent_request({}, RELAUNCH_WORDING)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("requested_by_session_id", raised.exception.message)
        # Named so the reader knows which verb is missing its caller.
        self.assertIn(RELAUNCH_WORDING.request_noun, raised.exception.message)

    def test_an_absent_override_is_false_rather_than_unstated(self):
        request = pane_gates.read_agent_request(
            {"requested_by_session_id": "pane-1"}, CLEAR_WORDING
        )

        self.assertEqual(request.caller_session_id, "pane-1")
        self.assertFalse(request.override)

    def test_whitespace_alone_names_nobody(self):
        with self.assertRaises(pane_gates.PaneGateRefusal):
            pane_gates.read_agent_request(
                {"requested_by_session_id": "   "}, CLEAR_WORDING
            )


class SelfGateTestCase(unittest.TestCase):
    def test_the_caller_is_refused_on_its_own_pane(self):
        with patch.object(pane_gates, "session_manager", _Registry("pane-1")):
            with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                pane_gates.check_caller("pane-1", _request(), RELAUNCH_WORDING)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn(f"[{pane_gates.SELF_GATE} gate]", raised.exception.message)

    def test_the_self_gate_has_no_escape_hatch(self):
        """`override` is stated and still refused -- the check never reads it."""
        with patch.object(pane_gates, "session_manager", _Registry("pane-1")):
            with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                pane_gates.check_caller(
                    "pane-1", _request(override=True), RELAUNCH_WORDING
                )

        self.assertIn(f"[{pane_gates.SELF_GATE} gate]", raised.exception.message)

    def test_a_caller_pane_that_is_no_longer_open_fails_the_lineage_gate(self):
        """A creator id naming nothing live names nothing at all."""
        with patch.object(pane_gates, "session_manager", _Registry()):
            with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                pane_gates.check_caller("pane-2", _request(), RELAUNCH_WORDING)

        self.assertIn(f"[{pane_gates.LINEAGE_GATE} gate]", raised.exception.message)

    def test_a_live_caller_on_another_pane_passes(self):
        with patch.object(pane_gates, "session_manager", _Registry("pane-1")):
            self.assertIsNone(
                pane_gates.check_caller("pane-2", _request(), RELAUNCH_WORDING)
            )


class LineageGateTestCase(unittest.TestCase):
    def test_a_pane_this_caller_created_passes(self):
        pane = _Pane(created_by_session_id="pane-1")

        self.assertIsNone(
            pane_gates.check_lineage(pane, _request(), RELAUNCH_WORDING)
        )

    def test_a_pane_with_no_creator_is_refused_and_offers_a_split(self):
        """A pane from before a restart has no creator, and GridVibe says so."""
        with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
            pane_gates.check_lineage(_Pane(), _request(), RELAUNCH_WORDING)

        message = raised.exception.message
        self.assertIn(f"[{pane_gates.LINEAGE_GATE} gate]", message)
        self.assertIn("Split off a new pane instead", message)
        self.assertIn(pane_gates.OVERRIDE_TAIL, message)

    def test_another_agents_pane_is_refused(self):
        pane = _Pane(created_by_session_id="pane-9")

        with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
            pane_gates.check_lineage(pane, _request(), RELAUNCH_WORDING)

        self.assertIn(f"[{pane_gates.LINEAGE_GATE} gate]", raised.exception.message)
        self.assertIn(pane_gates.OVERRIDE_TAIL, raised.exception.message)

    def test_override_waives_both_lineage_refusals(self):
        for pane in (_Pane(), _Pane(created_by_session_id="pane-9")):
            with self.subTest(creator=pane.created_by_session_id or "none"):
                self.assertIsNone(
                    pane_gates.check_lineage(
                        pane, _request(override=True), RELAUNCH_WORDING
                    )
                )

    def test_a_waiver_is_logged_naming_both_panes(self):
        """The interesting event, and unreadable afterwards without both ids."""
        pane = _Pane(session_id="pane-7", created_by_session_id="pane-9")

        with self.assertLogs(pane_gates.logger, level="INFO") as captured:
            pane_gates.check_lineage(pane, _request(override=True), CLEAR_WORDING)

        record = "\n".join(captured.output)
        self.assertIn("pane-7", record)
        self.assertIn("pane-1", record)
        self.assertIn("pane-9", record)

    def test_a_pane_the_caller_did_own_logs_no_waiver(self):
        """Override on a pane it created waived nothing, so it records nothing."""
        pane = _Pane(created_by_session_id="pane-1")

        with patch.object(pane_gates.logger, "info") as info:
            pane_gates.check_lineage(pane, _request(override=True), CLEAR_WORDING)

        info.assert_not_called()


class OverrideModeTestCase(unittest.TestCase):
    """A caller pane launched in override mode carries `override` on every
    gated request, read from its record -- and waives nothing more than a
    stated `override` would. The negative cases are the point."""

    BODY = {"requested_by_session_id": "pane-1"}

    def test_a_granted_caller_carries_override_it_did_not_state(self):
        request = _read(self.BODY, _Callers(_Caller()))

        self.assertTrue(request.override)
        self.assertEqual(request.waiver_source, pane_gates.OVERRIDE_FROM_MODE)

    def test_every_pane_transaction_and_the_move_read_the_grant(self):
        """One choke point: no verb reads its caller any other way."""
        registry = _Callers(_Caller())
        for name, wording in ALL_WORDINGS.items():
            with self.subTest(transaction=name):
                self.assertTrue(_read(self.BODY, registry, wording).override)
        with patch.object(pane_gates, "session_manager", registry):
            self.assertTrue(pane_gates.read_caller_request(self.BODY, "a move").override)

    def test_a_stated_override_is_logged_as_the_call_even_under_a_grant(self):
        request = _read({**self.BODY, "override": True}, _Callers(_Caller()))

        self.assertTrue(request.override)
        self.assertEqual(request.waiver_source, pane_gates.OVERRIDE_FROM_CALL)

    def test_a_caller_without_the_grant_carries_nothing(self):
        request = _read(self.BODY, _Callers(_Caller(agent_mcp_override=False)))

        self.assertFalse(request.override)
        self.assertEqual(request.waiver_source, "")

    def test_only_a_true_grant_on_a_live_mcp_agent_counts(self):
        """The record invariant, repeated at the gate: a stale or truthy-looking
        value on a pane that is no longer an MCP agent grants nothing."""
        for label, caller in (
            ("truthy string", _Caller(agent_mcp_override="true")),
            ("tools off", _Caller(agent_mcp=False)),
            ("not an agent", _Caller(startup_mode="terminal")),
        ):
            with self.subTest(caller=label):
                self.assertFalse(_read(self.BODY, _Callers(caller)).override)

    def test_a_request_cannot_claim_the_grant_for_itself(self):
        """The grant is read off the record; the payload has no say."""
        body = {**self.BODY, "agent_mcp_override": True, "override_source": "mode"}

        request = _read(body, _Callers(_Caller(agent_mcp_override=False)))

        self.assertFalse(request.override)

    def test_a_caller_that_is_gone_holds_no_grant_and_is_still_refused(self):
        """Dead caller: nothing to read a grant from, and the liveness check --
        not waivable -- refuses it whatever the request carries."""
        request = _read(self.BODY, _Callers())
        self.assertFalse(request.override)

        granted = _read(self.BODY, _Callers(_Caller()))
        with patch.object(pane_gates, "session_manager", _Callers()):
            with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                pane_gates.check_caller("pane-2", granted, CLEAR_WORDING)

        self.assertIn(f"[{pane_gates.LINEAGE_GATE} gate]", raised.exception.message)
        self.assertFalse(raised.exception.waivable)

    def test_override_mode_never_reaches_the_callers_own_pane(self):
        registry = _Callers(_Caller())
        request = _read(self.BODY, registry)

        for name, wording in ALL_WORDINGS.items():
            with self.subTest(transaction=name):
                with patch.object(pane_gates, "session_manager", registry):
                    with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                        pane_gates.check_caller("pane-1", request, wording)
                self.assertIn(f"[{pane_gates.SELF_GATE} gate]", raised.exception.message)
                self.assertFalse(raised.exception.waivable)

    def test_override_mode_waives_both_lineage_refusals(self):
        request = _read(self.BODY, _Callers(_Caller()))

        for pane in (_Pane(), _Pane(created_by_session_id="pane-9")):
            with self.subTest(creator=pane.created_by_session_id or "none"):
                self.assertIsNone(
                    pane_gates.check_lineage(pane, request, RELAUNCH_WORDING)
                )

    def test_the_waiver_log_says_whether_it_was_the_mode_or_the_call(self):
        pane = _Pane(session_id="pane-7", created_by_session_id="pane-9")
        registry = _Callers(_Caller())

        for body, source in (
            (self.BODY, "mode"),
            ({**self.BODY, "override": True}, "call"),
        ):
            with self.subTest(source=source):
                request = _read(body, registry)
                with self.assertLogs(pane_gates.logger, level="INFO") as captured:
                    pane_gates.check_lineage(pane, request, CLEAR_WORDING)
                self.assertIn(f"source={source}", "\n".join(captured.output))

    def test_a_request_built_without_a_source_logs_as_the_call(self):
        self.assertEqual(_request(override=True).waiver_source, pane_gates.OVERRIDE_FROM_CALL)
        self.assertEqual(_request().waiver_source, "")


class StatedCallerTestCase(unittest.TestCase):
    """The caller id is an input, not a proof -- pinned rather than commented.

    On the stdio path `requested_by_session_id` traces back to
    `GRIDVIBE_SESSION_ID` in the sidecar's own environment, inherited from the
    agent CLI that the gates exist to constrain, and `list_panes` publishes
    every live id. So the interesting question is not "can an agent state
    another pane's id" -- it can -- but *what the gates then do*, which is
    evaluate against the pane that was named. These two tests are that answer,
    written down so the module's stated weakness cannot quietly become a
    stated strength.
    """

    def test_a_stated_caller_moves_the_self_gate_onto_that_pane(self):
        """An agent in pane-1 that names pane-5: pane-5 is what self protects,
        and pane-1 -- its own -- is no longer the pane the request came from."""
        borrowed = _request(caller="pane-5")

        with patch.object(pane_gates, "session_manager", _Registry("pane-1", "pane-5")):
            self.assertIsNone(
                pane_gates.check_caller("pane-1", borrowed, RELAUNCH_WORDING)
            )
            with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                pane_gates.check_caller("pane-5", borrowed, RELAUNCH_WORDING)

        self.assertIn(f"[{pane_gates.SELF_GATE} gate]", raised.exception.message)

    def test_a_stated_caller_inherits_that_panes_whole_lineage(self):
        """Every pane pane-5 created, admitted with no `override` stated."""
        made_by_pane_5 = _Pane(created_by_session_id="pane-5")

        self.assertIsNone(
            pane_gates.check_lineage(
                made_by_pane_5, _request(caller="pane-5"), RELAUNCH_WORDING
            )
        )

    def test_the_gates_still_bind_an_agent_that_states_its_own(self):
        """What the gates do buy, in the same file: the ordinary caller reaches
        neither its own pane nor one it did not create."""
        pane_of_another = _Pane(created_by_session_id="pane-9")

        with patch.object(pane_gates, "session_manager", _Registry("pane-1")):
            with self.assertRaises(pane_gates.PaneGateRefusal):
                pane_gates.check_caller("pane-1", _request(), RELAUNCH_WORDING)
        with self.assertRaises(pane_gates.PaneGateRefusal):
            pane_gates.check_lineage(pane_of_another, _request(), RELAUNCH_WORDING)


class RefusalShapeTestCase(unittest.TestCase):
    def test_every_refusal_names_its_gate_the_same_way(self):
        self.assertEqual(
            pane_gates.refusal_text(pane_gates.MODE_GATE, "Because."),
            "[mode gate] Because.",
        )

    def test_the_factory_answers_403(self):
        refusal = pane_gates.refuse(pane_gates.MODE_GATE, "Because.")

        self.assertEqual(refusal.status_code, 403)
        self.assertEqual(refusal.message, "[mode gate] Because.")


class WordingTestCase(unittest.TestCase):
    """Each transaction says what *it* does, in sentences a person will read."""

    def test_the_three_transactions_do_not_share_a_verb(self):
        nouns = {wording.request_noun for wording in ALL_WORDINGS.values()}
        acts = {wording.act for wording in ALL_WORDINGS.values()}

        self.assertEqual(len(nouns), len(ALL_WORDINGS))
        self.assertEqual(len(acts), len(ALL_WORDINGS))

    def test_only_the_transactions_that_end_something_say_so(self):
        """A clear ends nothing, and its refusal must not claim it does."""
        for name in ("relaunch", "mode switch"):
            with self.subTest(transaction=name):
                self.assertIn(
                    "would end this agent", ALL_WORDINGS[name].self_reason
                )
        self.assertNotIn("would end", CLEAR_WORDING.self_reason)

    def test_every_self_reason_is_a_whole_sentence(self):
        for name, wording in ALL_WORDINGS.items():
            with self.subTest(transaction=name):
                self.assertTrue(wording.self_reason.endswith("."))
                self.assertTrue(wording.self_reason[:1].isupper())

    def test_a_shared_refusal_reads_as_that_transactions_own(self):
        """The whole point of the wording: one code path, three true sentences."""
        with patch.object(pane_gates, "session_manager", _Registry("pane-1")):
            with self.assertRaises(pane_gates.PaneGateRefusal) as raised:
                pane_gates.check_caller("pane-1", _request(), CLEAR_WORDING)

        self.assertIn("Clearing it", raised.exception.message)
        self.assertNotIn("Relaunching", raised.exception.message)


if __name__ == "__main__":  # pragma: no cover - convenience runner
    unittest.main()
