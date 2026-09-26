"""The page half of the window intent, executed in Node.

`window-intent.js` takes its requests, timers and visibility reads from an
injected runtime, so the real module runs here against stubs rather than the
tests asserting source text.

What is pinned:

- **Claim before opening, and one window per intent.** Two pages poll the same
  pending intent; the claim endpoint hands it to exactly one of them, and the
  loser does nothing at all -- not even report a failure, because losing a
  claim is not a failure.
- **Report what happened, once.** `opened` or `blocked`, never a retry and
  never an outcome the page did not observe.
- **A hidden page polls nothing**, and a pass that is still running is never
  overlapped -- opening a window can take a second or more.
- **An intent is acted on once**, even when the poll comes round again before
  the server has settled the record.
- **Only the page holding a tab switches to it**, and it reports what it now
  shows -- a switch its own rules refused is ``blocked`` with its reason.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
WINDOW_INTENT_JS = STATIC_JS / "window-intent.js"

NODE = shutil.which("node")

HARNESS = """
const intentModule = require(MODULE_PATH);

function runtime(options = {}) {
    const calls = { listed: 0, claims: [], results: [], opens: [] };
    const timers = [];
    const state = {
        intents: options.intents || [],
        claimOk: options.claimOk !== false,
        opens: options.opens !== false,
        visible: options.visible !== false
    };
    const splitCalls = { owns: [], candidates: [], performed: [], reasons: [] };
    const splitBridge = options.splitBridge === null ? null : {
        owns(sessionId) {
            splitCalls.owns.push(sessionId);
            return (options.ownedSessions || ['pane-1']).includes(sessionId);
        },
        candidates(sessionId) {
            splitCalls.candidates.push(sessionId);
            return options.candidates === undefined
                ? ['vertical', 'horizontal']
                : options.candidates;
        },
        disabledReason(axis, sessionId) {
            splitCalls.reasons.push({ axis, sessionId });
            return options.disabledReason
                || 'Side-by-side split needs at least 8 columns in each terminal';
        },
        async perform(sessionId, axis, request) {
            splitCalls.performed.push({ sessionId, axis, request });
            if (options.performThrows) throw new Error('grid exploded');
            if (options.performFails) {
                return { ok: false, error: 'Split failed with status 400' };
            }
            return {
                ok: true,
                index: 2,
                session: {
                    session_id: 'pane-9',
                    group_id: 'g-1',
                    title: 'Terminal 3',
                    startup_mode: 'agent',
                    agent_selection: 'claude',
                    password: 'hunter2'
                }
            };
        }
    };
    const focusCalls = { holds: [], activated: [] };
    const focusBridge = options.focusBridge === null ? null : {
        holds(workspaceId, groupId) {
            focusCalls.holds.push({ workspaceId, groupId });
            return (options.heldGroups || ['ws-1/g-1']).includes(`${workspaceId}/${groupId}`);
        },
        activeGroupId() { return 'g-0'; },
        async activate(groupId, sessionId) {
            focusCalls.activated.push({ groupId, sessionId });
            if (options.activateThrows) throw new Error('grid exploded');
            if (options.activateResult) return options.activateResult;
            return {
                ok: true,
                activeGroupId: groupId,
                paneVisible: Boolean(sessionId),
                focused: Boolean(sessionId)
            };
        }
    };
    const poll = intentModule.create({
        splitBridge,
        focusBridge,
        listIntents: async () => {
            calls.listed += 1;
            if (options.listThrows) throw new Error('list failed');
            return state.intents;
        },
        claimIntent: async (intentId, claimant) => {
            calls.claims.push({ intentId, claimant });
            return state.claimOk
                ? { ok: true, state: 'claimed', intent_id: intentId }
                : { ok: false, state: 'claimed', error: 'Another window took it.' };
        },
        reportResult: async (intentId, outcome, detail, result) => {
            calls.results.push({ intentId, outcome, detail, result });
        },
        openWorkspaceWindow: async (workspaceId, opts) => {
            calls.opens.push({ workspaceId, groupId: opts.groupId });
            if (options.openThrows) throw new Error('bridge exploded');
            return state.opens;
        },
        setInterval: (handler, interval) => {
            timers.push({ handler, interval });
            return timers.length;
        },
        clearInterval: handle => { timers[handle - 1] = null; },
        isVisible: () => state.visible,
        claimant: 'window-a',
        onError: () => {}
    });
    return { poll, calls, timers, state, splitCalls, focusCalls };
}

const activateIntent = (id, group = 'g-1', session = '', workspace = 'ws-1') => ({
    intent_id: id,
    kind: 'activate',
    workspace_id: workspace,
    group_id: group,
    session_id: session,
    state: 'pending'
});

const splitIntent = (id, session = 'pane-1', axis = 'vertical', request = null) => ({
    intent_id: id,
    kind: 'split',
    workspace_id: 'ws-1',
    group_id: 'g-1',
    session_id: session,
    axis,
    split_request: request || { axis, kind: 'agent', agent: 'claude' },
    state: 'pending'
});

const intent = (id, workspace = 'ws-1', group = 'g-1') => ({
    intent_id: id, workspace_id: workspace, group_id: group, state: 'pending'
});

const out = {};

(async () => {
    // A pending intent is claimed, opened, and reported once.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')] });
        await poll.tick();
        out.happyPath = {
            claims: calls.claims,
            opens: calls.opens,
            results: calls.results
        };
    }

    // The page that loses the claim opens nothing and reports nothing.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')], claimOk: false });
        await poll.tick();
        out.lostClaim = { opens: calls.opens.length, results: calls.results.length };
    }

    // A window that refused is reported as blocked, not retried.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')], opens: false });
        await poll.tick();
        out.blocked = calls.results;
    }

    // A bridge that threw is still an honest `blocked`.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')], openThrows: true });
        await poll.tick();
        out.threw = calls.results.map(item => item.outcome);
    }

    // A second pass over the same intent acts once.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')] });
        await poll.tick();
        await poll.tick();
        out.repeated = { claims: calls.claims.length, opens: calls.opens.length };
    }

    // A hidden page polls nothing at all.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')], visible: false });
        await poll.tick();
        out.hidden = { listed: calls.listed, claims: calls.claims.length };
    }

    // A failing list is swallowed, and the next pass still runs.
    {
        const { poll, calls } = runtime({ intents: [intent('i-1')], listThrows: true });
        await poll.tick();
        out.listFailure = { listed: calls.listed, claims: calls.claims.length };
    }

    // Two intents in one pass are both delivered, in the server's order.
    {
        const { poll, calls } = runtime({
            intents: [intent('i-1', 'ws-1'), intent('i-2', 'ws-2')]
        });
        await poll.tick();
        out.twoIntents = calls.opens.map(item => item.workspaceId);
    }

    // start() schedules exactly one timer; stop() clears it.
    {
        const { poll, timers } = runtime({ intents: [] });
        poll.start();
        poll.start();
        out.scheduled = { count: timers.length, interval: timers[0].interval };
        poll.stop();
        out.cleared = timers[0] === null;
    }

    // policy, on its own.
    out.actionable = intentModule.policy.actionable([
        intent('i-1'),
        { ...intent('i-2'), state: 'claimed' },
        { ...intent('i-3'), workspace_id: '' },
        null
    ]).map(item => item.intent_id);
    out.claimedVerdicts = [
        intentModule.policy.claimed({ ok: true, state: 'claimed' }),
        intentModule.policy.claimed({ ok: false, state: 'claimed' }),
        intentModule.policy.claimed({ ok: true, state: 'pending' }),
        intentModule.policy.claimed(null)
    ];

    // ── splits ──

    // A split intent for a pane this window holds is claimed and performed.
    {
        const { poll, calls, splitCalls } = runtime({ intents: [splitIntent('s-1')] });
        await poll.tick();
        out.splitHappy = {
            claims: calls.claims.length,
            performed: splitCalls.performed,
            results: calls.results
        };
    }

    // A pane this window does not hold is never claimed at all.
    {
        const { poll, calls, splitCalls } = runtime({
            intents: [splitIntent('s-1', 'pane-elsewhere')]
        });
        await poll.tick();
        out.splitNotOurs = {
            claims: calls.claims.length,
            performed: splitCalls.performed.length,
            results: calls.results.length
        };
    }

    // A page with no split bridge at all — the launcher — claims nothing.
    {
        const { poll, calls } = runtime({
            intents: [splitIntent('s-1')],
            splitBridge: null
        });
        await poll.tick();
        out.splitNoBridge = { claims: calls.claims.length, results: calls.results.length };
    }

    // A pane too small on the asked axis is refused, and the other axis named.
    {
        const { poll, calls, splitCalls } = runtime({
            intents: [splitIntent('s-1', 'pane-1', 'vertical')],
            candidates: ['horizontal']
        });
        await poll.tick();
        out.splitRefused = {
            performed: splitCalls.performed.length,
            results: calls.results,
            asked: splitCalls.reasons
        };
    }

    // Neither axis works: the refusal says so rather than naming one.
    {
        const { poll, calls } = runtime({
            intents: [splitIntent('s-1')],
            candidates: []
        });
        await poll.tick();
        out.splitNoAxis = calls.results;
    }

    // A split the page attempted and could not finish is an honest refusal.
    {
        const { poll, calls } = runtime({
            intents: [splitIntent('s-1')],
            performFails: true
        });
        await poll.tick();
        out.splitFailed = calls.results;
    }

    // A bridge that threw is reported, not swallowed into a false success.
    {
        const { poll, calls } = runtime({
            intents: [splitIntent('s-1')],
            performThrows: true
        });
        await poll.tick();
        out.splitThrew = calls.results.map(item => item.outcome);
    }

    // A window intent and a split intent in one pass are both delivered.
    {
        const { poll, calls, splitCalls } = runtime({
            intents: [intent('i-1'), splitIntent('s-1')]
        });
        await poll.tick();
        out.bothKinds = {
            opens: calls.opens.length,
            performed: splitCalls.performed.length,
            outcomes: calls.results.map(item => item.outcome)
        };
    }

    // Two passes over the same split intent perform it once.
    {
        const { poll, splitCalls } = runtime({ intents: [splitIntent('s-1')] });
        await poll.tick();
        await poll.tick();
        out.splitRepeated = splitCalls.performed.length;
    }

    // policy, on its own.
    out.splitActionable = intentModule.policy.actionable(
        [splitIntent('s-1'), splitIntent('s-2', 'pane-elsewhere'), intent('i-1')],
        sessionId => sessionId === 'pane-1'
    ).map(item => item.intent_id);
    out.splitKinds = [
        intentModule.policy.kind(splitIntent('s-1')),
        intentModule.policy.kind(intent('i-1')),
        intentModule.policy.kind({})
    ];
    out.refusalSentences = [
        intentModule.policy.splitRefusal('vertical', ['horizontal'], 'Too narrow.'),
        intentModule.policy.splitRefusal('vertical', [], 'Too narrow.')
    ];

    // ── activations ──

    // The page holding the tab claims it, switches, focuses, and reports.
    {
        const { poll, calls, focusCalls } = runtime({
            intents: [activateIntent('a-1', 'g-1', 'pane-1')]
        });
        await poll.tick();
        out.activateHappy = {
            claims: calls.claims.length,
            activated: focusCalls.activated,
            opens: calls.opens.length,
            results: calls.results
        };
    }

    // A group with no pane named switches the tab and focuses nothing.
    {
        const { poll, calls } = runtime({ intents: [activateIntent('a-1')] });
        await poll.tick();
        out.activateGroupOnly = calls.results;
    }

    // A page that does not hold the tab never claims it.
    {
        const { poll, calls, focusCalls } = runtime({
            intents: [activateIntent('a-1', 'g-elsewhere')]
        });
        await poll.tick();
        out.activateNotOurs = {
            claims: calls.claims.length,
            activated: focusCalls.activated.length,
            results: calls.results.length
        };
    }

    // The launcher — no focus bridge — claims no activation.
    {
        const { poll, calls } = runtime({
            intents: [activateIntent('a-1')],
            focusBridge: null
        });
        await poll.tick();
        out.activateNoBridge = { claims: calls.claims.length, results: calls.results.length };
    }

    // The page's own refusal (an unsaved editor) is reported, with its words.
    {
        const { poll, calls } = runtime({
            intents: [activateIntent('a-1', 'g-1', 'pane-1')],
            activateResult: {
                ok: false,
                activeGroupId: 'g-0',
                error: 'An open file in this window has unsaved changes.'
            }
        });
        await poll.tick();
        out.activateBlocked = calls.results;
    }

    // A page that says ok but shows another tab is not an activation.
    {
        const { poll, calls } = runtime({
            intents: [activateIntent('a-1', 'g-1')],
            activateResult: { ok: true, activeGroupId: 'g-0' }
        });
        await poll.tick();
        out.activateWrongTab = calls.results;
    }

    // The pane is on screen but did not take focus: shown, and said so.
    {
        const { poll, calls } = runtime({
            intents: [activateIntent('a-1', 'g-1', 'pane-1')],
            activateResult: { ok: true, activeGroupId: 'g-1', paneVisible: true, focused: false }
        });
        await poll.tick();
        out.activateUnfocused = calls.results;
    }

    // A page claiming focus on a pane it does not show is not believed.
    {
        const { poll, calls } = runtime({
            intents: [activateIntent('a-1', 'g-1', 'pane-1')],
            activateResult: { ok: true, activeGroupId: 'g-1', paneVisible: false, focused: true }
        });
        await poll.tick();
        out.activateInvisible = calls.results;
    }

    // A bridge that threw is an honest `blocked`.
    {
        const { poll, calls } = runtime({
            intents: [activateIntent('a-1')],
            activateThrows: true
        });
        await poll.tick();
        out.activateThrew = calls.results;
    }

    // Two passes over one activation switch once.
    {
        const { poll, focusCalls } = runtime({ intents: [activateIntent('a-1')] });
        await poll.tick();
        await poll.tick();
        out.activateRepeated = focusCalls.activated.length;
    }

    out.activateActionable = intentModule.policy.actionable(
        [
            activateIntent('a-1'),
            activateIntent('a-2', 'g-2'),
            activateIntent('a-3', 'g-1', '', 'ws-2'),
            { ...activateIntent('a-4'), group_id: '' },
            intent('i-1')
        ],
        null,
        (workspaceId, groupId) => workspaceId === 'ws-1' && groupId === 'g-1'
    ).map(item => item.intent_id);

    console.log(JSON.stringify(out));
})();
"""


@unittest.skipIf(NODE is None, "Node.js is required for the window-intent suite")
class WindowIntentClientTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as directory:
            script = Path(directory) / "harness.cjs"
            script.write_text(
                f"const MODULE_PATH = {json.dumps(str(WINDOW_INTENT_JS))};\n" + HARNESS,
                encoding="utf-8",
            )
            result = subprocess.run(
                [NODE, str(script)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        cls.out = json.loads(result.stdout.strip().splitlines()[-1])

    def test_a_pending_intent_is_claimed_opened_and_reported_once(self):
        happy = self.out["happyPath"]

        self.assertEqual(happy["claims"], [{"intentId": "i-1", "claimant": "window-a"}])
        self.assertEqual(happy["opens"], [{"workspaceId": "ws-1", "groupId": "g-1"}])
        self.assertEqual(
            happy["results"], [{"intentId": "i-1", "outcome": "opened", "detail": ""}]
        )

    def test_the_page_that_loses_the_claim_does_nothing(self):
        # This is what stops two open pages delivering one request twice.
        self.assertEqual(self.out["lostClaim"], {"opens": 0, "results": 0})

    def test_a_window_that_refused_is_reported_as_blocked(self):
        blocked = self.out["blocked"]

        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["outcome"], "blocked")
        self.assertTrue(blocked[0]["detail"])

    def test_a_bridge_that_threw_is_still_reported_honestly(self):
        self.assertEqual(self.out["threw"], ["blocked"])

    def test_an_intent_is_acted_on_once_even_if_it_is_still_listed(self):
        # The poll comes round before the server has necessarily settled the
        # record; acting twice would open a second window.
        self.assertEqual(self.out["repeated"], {"claims": 1, "opens": 1})

    def test_a_hidden_page_polls_nothing(self):
        self.assertEqual(self.out["hidden"], {"listed": 0, "claims": 0})

    def test_a_failed_list_costs_the_pass_and_nothing_more(self):
        self.assertEqual(self.out["listFailure"], {"listed": 1, "claims": 0})

    def test_two_intents_are_delivered_in_the_servers_order(self):
        self.assertEqual(self.out["twoIntents"], ["ws-1", "ws-2"])

    def test_start_schedules_one_timer_and_stop_clears_it(self):
        self.assertEqual(self.out["scheduled"]["count"], 1)
        self.assertEqual(self.out["scheduled"]["interval"], 2000)
        self.assertTrue(self.out["cleared"])

    def test_only_a_pending_intent_with_a_workspace_is_actionable(self):
        self.assertEqual(self.out["actionable"], ["i-1"])

    def test_a_claim_counts_only_when_the_server_said_claimed(self):
        self.assertEqual(self.out["claimedVerdicts"], [True, False, False, False])


@unittest.skipIf(NODE is None, "Node.js is required for the window-intent suite")
class SplitIntentClientTestCase(WindowIntentClientTestCase):
    """The split half of the same poll, executed in Node.

    A split is an intent because the axis never reaches the server: the page
    computes the rectangles and owns every refusal, measured off the live
    terminal. What is pinned here is who may claim one, and that a refusal is
    an answer rather than a retry.
    """

    def test_a_split_for_a_pane_this_window_holds_is_performed_and_reported(self):
        happy = self.out["splitHappy"]

        self.assertEqual(happy["claims"], 1)
        self.assertEqual(len(happy["performed"]), 1)
        self.assertEqual(happy["performed"][0]["sessionId"], "pane-1")
        self.assertEqual(happy["performed"][0]["axis"], "vertical")
        # The body the server built is forwarded verbatim, not recomposed.
        self.assertEqual(happy["performed"][0]["request"]["agent"], "claude")
        self.assertEqual(happy["results"][0]["outcome"], "split")

    def test_the_settled_split_reports_the_new_pane_through_a_field_list(self):
        result = self.out["splitHappy"]["results"][0]["result"]

        self.assertEqual(result["session_id"], "pane-9")
        self.assertEqual(result["index"], 2)
        self.assertEqual(result["agent_selection"], "claude")
        self.assertNotIn("password", result)

    def test_a_window_that_does_not_hold_the_pane_never_claims_it(self):
        """The ownership gate, and the reason two windows do not fight."""
        self.assertEqual(
            self.out["splitNotOurs"], {"claims": 0, "performed": 0, "results": 0}
        )

    def test_a_page_with_no_panes_claims_no_splits(self):
        """The launcher polls the same list and takes none of them."""
        self.assertEqual(self.out["splitNoBridge"], {"claims": 0, "results": 0})

    def test_a_pane_too_small_on_that_axis_is_refused_naming_the_other(self):
        refused = self.out["splitRefused"]

        # Never split the other way instead: the caller asked for one axis.
        self.assertEqual(refused["performed"], 0)
        self.assertEqual(refused["results"][0]["outcome"], "refused")
        self.assertIn("stacked split would work", refused["results"][0]["detail"])
        # The sentence is the *pane's* — which of the split rules refused is a
        # fact about that rectangle, not about the axis.
        self.assertEqual(
            refused["asked"], [{"axis": "vertical", "sessionId": "pane-1"}]
        )

    def test_a_pane_that_cannot_be_split_at_all_says_that_instead(self):
        detail = self.out["splitNoAxis"][0]["detail"]

        self.assertIn("Neither axis would work", detail)

    def test_a_split_that_failed_in_the_page_is_reported_with_its_reason(self):
        results = self.out["splitFailed"]

        self.assertEqual(results[0]["outcome"], "refused")
        self.assertIn("status 400", results[0]["detail"])

    def test_a_bridge_that_threw_is_still_an_honest_refusal(self):
        self.assertEqual(self.out["splitThrew"], ["refused"])

    def test_both_kinds_are_delivered_in_one_pass(self):
        both = self.out["bothKinds"]

        self.assertEqual(both["opens"], 1)
        self.assertEqual(both["performed"], 1)
        self.assertEqual(sorted(both["outcomes"]), ["opened", "split"])

    def test_a_split_intent_is_acted_on_once(self):
        self.assertEqual(self.out["splitRepeated"], 1)

    def test_actionable_takes_only_the_split_whose_pane_is_here(self):
        self.assertEqual(self.out["splitActionable"], ["s-1", "i-1"])

    def test_an_intent_with_no_kind_reads_as_a_window(self):
        self.assertEqual(self.out["splitKinds"], ["split", "window", "window"])

    def test_the_refusal_sentence_names_the_axis_that_would_have_worked(self):
        with_other, without = self.out["refusalSentences"]

        self.assertIn("stacked split would work", with_other)
        self.assertIn("Neither axis would work", without)


@unittest.skipIf(NODE is None, "Node.js is required for the window-intent suite")
class ActivateIntentClientTestCase(unittest.TestCase):
    """The tab-switch half of the same poll, executed in Node.

    Raising a window does not change the tab it shows, so a tool asking to see
    a session is answered by the page holding it -- and only that page.
    """

    setUpClass = classmethod(WindowIntentClientTestCase.setUpClass.__func__)

    def test_the_page_holding_the_tab_switches_focuses_and_reports(self):
        happy = self.out["activateHappy"]

        self.assertEqual(happy["claims"], 1)
        self.assertEqual(happy["activated"], [{"groupId": "g-1", "sessionId": "pane-1"}])
        # Switching a tab is not opening a window.
        self.assertEqual(happy["opens"], 0)
        self.assertEqual(
            happy["results"],
            [{
                "intentId": "a-1",
                "outcome": "activated",
                "detail": "",
                "result": {
                    "active_group_id": "g-1",
                    "session_id": "pane-1",
                    "pane_visible": True,
                    "focused": True,
                },
            }],
        )

    def test_a_group_alone_is_activated_without_claiming_a_focus(self):
        result = self.out["activateGroupOnly"][0]

        self.assertEqual(result["outcome"], "activated")
        self.assertFalse(result["result"]["focused"])

    def test_a_page_that_does_not_hold_the_tab_never_claims_it(self):
        self.assertEqual(
            self.out["activateNotOurs"], {"claims": 0, "activated": 0, "results": 0}
        )

    def test_the_launcher_claims_no_activation(self):
        self.assertEqual(self.out["activateNoBridge"], {"claims": 0, "results": 0})

    def test_a_switch_the_page_refused_is_blocked_with_its_reason(self):
        result = self.out["activateBlocked"][0]

        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("unsaved changes", result["detail"])
        # What the page actually shows, so the tool can say the tab stayed.
        self.assertEqual(result["result"]["active_group_id"], "g-0")
        self.assertFalse(result["result"]["focused"])

    def test_ok_on_the_wrong_tab_is_not_an_activation(self):
        result = self.out["activateWrongTab"][0]

        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("did not switch", result["detail"])

    def test_a_shown_pane_that_did_not_take_focus_says_so(self):
        result = self.out["activateUnfocused"][0]

        self.assertEqual(result["outcome"], "activated")
        self.assertTrue(result["result"]["pane_visible"])
        self.assertFalse(result["result"]["focused"])

    def test_focus_on_a_pane_that_is_not_shown_is_not_believed(self):
        result = self.out["activateInvisible"][0]

        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("not in it", result["detail"])
        self.assertFalse(result["result"]["focused"])

    def test_a_bridge_that_threw_is_still_an_honest_block(self):
        result = self.out["activateThrew"][0]

        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("grid exploded", result["detail"])

    def test_an_activation_is_acted_on_once(self):
        self.assertEqual(self.out["activateRepeated"], 1)

    def test_actionable_takes_only_the_tab_this_page_holds(self):
        # The window intent is still actionable beside it.
        self.assertEqual(self.out["activateActionable"], ["a-1", "i-1"])


if __name__ == "__main__":
    unittest.main()
