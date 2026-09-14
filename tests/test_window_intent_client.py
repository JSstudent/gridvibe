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
    const poll = intentModule.create({
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
        reportResult: async (intentId, outcome, detail) => {
            calls.results.push({ intentId, outcome, detail });
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
    return { poll, calls, timers, state };
}

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


if __name__ == "__main__":
    unittest.main()
