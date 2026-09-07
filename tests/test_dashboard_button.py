"""The dashboard button both host pages carry, driven through its own wiring.

`dashboard.js` is small on purpose — the dashboard itself is a window with its
own module — so what is pinned here is the whole of what the host pages know
about it:

- **The button opens a window, and only one.** Native first (the pywebview
  bridge), a *named* `window.open` second, so a second press focuses the
  dashboard rather than stacking another copy of it. A native bridge that
  refuses still lands somewhere.
- **The chord is `Alt+A`, matched on `event.code`.** Ctrl is excluded because
  AltGr arrives as Ctrl+Alt on Windows, and a page that says the chord may not
  fire from here — the same answer it already gives Alt+X — is obeyed rather
  than second-guessed.
- **The badge is honest without opening anything**, and a slow answer that
  lands after a newer one was asked for is dropped rather than painted.
- **A hidden document costs nothing**: the poll is torn down rather than left
  running, and reads once on the way back.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_JS = REPO_ROOT / "web" / "static" / "js" / "dashboard.js"

NODE = shutil.which("node")

HARNESS_STUBS = r"""
function fakeListeners() {
    const handlers = new Map();
    return {
        addEventListener(type, handler) {
            if (!handlers.has(type)) { handlers.set(type, new Set()); }
            handlers.get(type).add(handler);
        },
        removeEventListener(type, handler) {
            handlers.get(type)?.delete(handler);
        },
        listenerCount(type) { return handlers.get(type)?.size || 0; },
        fire(type, event) { [...(handlers.get(type) || [])].forEach(handler => handler(event)); }
    };
}

function fakeElement(id) {
    return {
        id,
        textContent: '',
        hidden: false,
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        focus() {},
        ...fakeListeners()
    };
}

const byId = new Map();
['dashboardRoot', 'dashboardBtn', 'dashboardBadge'].forEach(id => byId.set(id, fakeElement(id)));

const document = {
    hidden: false,
    getElementById: id => byId.get(id) || null,
    ...fakeListeners()
};

const window = globalThis;
const calls = { fetches: 0, opens: [], bridge: [], intervals: 0, cleared: 0 };

Object.assign(globalThis, {
    /* Real timers would keep the process alive, and the cadence itself is not
       what these cases are about — only whether one is armed at all. */
    setInterval: () => { calls.intervals += 1; return calls.intervals; },
    clearInterval: () => { calls.cleared += 1; },
    open(url, name) {
        calls.opens.push({ url, name });
        return openAnswer === null ? null : { focus() { openAnswer.focused = true; } };
    }
});

/* Swapped per case: `null` is a browser that blocked the pop-up. */
let openAnswer = {};
/* Swapped per case: `null` is browser mode (no native window at all). */
let bridgeAnswer = null;
Object.defineProperty(globalThis, 'pywebview', {
    get() {
        if (bridgeAnswer === null) { return undefined; }
        return {
            api: {
                open_dashboard_window() {
                    calls.bridge.push('open_dashboard_window');
                    if (bridgeAnswer instanceof Error) { throw bridgeAnswer; }
                    return Promise.resolve(bridgeAnswer);
                }
            }
        };
    }
});

/* Each page's own answer to "may a chord fire from here". Declared, because a
   page that has one is the normal case; a case that wants the other deletes
   it. */
let blockChord = false;
function minimizeAllShortcutBlocked(target) {
    return blockChord && target === 'a-focused-pane';
}

let fetchAnswer = null;
globalThis.fetch = async () => {
    calls.fetches += 1;
    const answer = typeof fetchAnswer === 'function' ? await fetchAnswer() : fetchAnswer;
    if (answer === null) {
        return { ok: false, status: 500, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => answer };
};

function badge() { return byId.get('dashboardBadge'); }

function snapshot(agents) {
    return { generated_at: 100, workspaces: [], totals: { workspaces: 1, sessions: 1, agents } };
}

/* A key press, as the document delivers one. */
function chord(overrides) {
    let prevented = false;
    document.fire('keydown', Object.assign({
        altKey: true,
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
        repeat: false,
        code: 'KeyA',
        target: 'the-page',
        preventDefault() { prevented = true; }
    }, overrides || {}));
    return prevented;
}

/* Let every pending microtask run: the badge paints from an async fetch. */
function settle() { return new Promise(resolve => setTimeout(resolve, 0)); }

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard button tests")
class DashboardButtonTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + DASHBOARD_JS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_native_window_is_asked_first_and_nothing_else_is_opened(self):
        result = self._run_node(
            """
            bridgeAnswer = { ok: true };
            const opened = await openAgentDashboardWindow();
            report({ opened, bridge: calls.bridge, opens: calls.opens });
            """
        )
        self.assertTrue(result["opened"])
        self.assertEqual(result["bridge"], ["open_dashboard_window"])
        self.assertEqual(result["opens"], [])

    def test_the_browser_opens_one_named_target_so_a_second_press_focuses_it(self):
        result = self._run_node(
            """
            await openAgentDashboardWindow();
            await openAgentDashboardWindow();
            report({ opens: calls.opens, bridge: calls.bridge });
            """
        )
        self.assertEqual(result["bridge"], [])
        self.assertEqual(len(result["opens"]), 2)
        # Same URL and the same name both times: the name is what makes the
        # second press reuse the tab instead of stacking another one.
        self.assertEqual({open_["url"] for open_ in result["opens"]}, {"/dashboard"})
        self.assertEqual(
            {open_["name"] for open_ in result["opens"]},
            {"gridvibe-agent-dashboard"},
        )

    def test_a_native_bridge_that_refuses_still_lands_somewhere(self):
        result = self._run_node(
            """
            bridgeAnswer = { ok: false, error: 'pywebview is unavailable' };
            const refused = await openAgentDashboardWindow();
            bridgeAnswer = new Error('bridge exploded');
            const threw = await openAgentDashboardWindow();
            report({ refused, threw, opens: calls.opens.length, bridge: calls.bridge.length });
            """
        )
        self.assertTrue(result["refused"])
        self.assertTrue(result["threw"])
        self.assertEqual(result["bridge"], 2)
        self.assertEqual(result["opens"], 2)

    def test_a_blocked_pop_up_is_reported_rather_than_claimed(self):
        result = self._run_node(
            """
            openAnswer = null;
            report({ opened: await openAgentDashboardWindow() });
            """
        )
        self.assertFalse(result["opened"])

    def test_the_chord_opens_it_and_takes_the_key(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            wireDashboard();
            await settle();
            const prevented = chord();
            await settle();
            report({ prevented, opens: calls.opens.length });
            """
        )
        self.assertTrue(result["prevented"])
        self.assertEqual(result["opens"], 1)

    def test_altgr_and_the_wrong_key_are_not_the_chord(self):
        """AltGr reaches the page as Ctrl+Alt, so Ctrl is load-bearing."""
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            wireDashboard();
            await settle();
            const prevented = [
                chord({ ctrlKey: true }),
                chord({ metaKey: true }),
                chord({ shiftKey: true }),
                chord({ repeat: true }),
                chord({ altKey: false }),
                chord({ code: 'KeyS' })
            ];
            await settle();
            report({ prevented, opens: calls.opens.length });
            """
        )
        self.assertEqual(result["prevented"], [False] * 6)
        self.assertEqual(result["opens"], 0)

    def test_the_page_decides_whether_the_chord_may_fire_from_here(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            blockChord = true;
            wireDashboard();
            await settle();
            const inAPane = chord({ target: 'a-focused-pane' });
            const onThePage = chord({ target: 'the-page' });
            await settle();
            report({ inAPane, onThePage, opens: calls.opens.length });
            """
        )
        self.assertFalse(result["inAPane"])
        self.assertTrue(result["onThePage"])
        self.assertEqual(result["opens"], 1)

    def test_the_badge_is_filled_without_anything_being_opened(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(3);
            const before = { text: badge().textContent, hidden: badge().hidden, fetches: calls.fetches };
            wireDashboard();
            await settle();
            report({ before, after: { text: badge().textContent, hidden: badge().hidden, fetches: calls.fetches }, opens: calls.opens.length });
            """
        )
        self.assertEqual(result["before"]["fetches"], 0)
        self.assertEqual(result["after"], {"text": "3", "hidden": False, "fetches": 1})
        self.assertEqual(result["opens"], 0)

    def test_no_agents_hides_the_badge_rather_than_showing_a_zero(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            wireDashboard();
            await settle();
            report({ text: badge().textContent, hidden: badge().hidden });
            """
        )
        self.assertTrue(result["hidden"])

    def test_a_very_large_count_is_capped_rather_than_overflowing_the_badge(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(140);
            wireDashboard();
            await settle();
            report({ text: badge().textContent });
            """
        )
        self.assertEqual(result["text"], "99+")

    def test_a_slow_answer_never_repaints_over_a_newer_one(self):
        result = self._run_node(
            """
            let pending = null;
            fetchAnswer = () => new Promise(resolve => { pending = resolve; });
            const slow = refreshDashboardBadge();
            const slowResolve = pending;
            fetchAnswer = snapshot(9);
            await refreshDashboardBadge();
            const afterNewer = badge().textContent;
            slowResolve(snapshot(1));
            await slow;
            report({ afterNewer, afterSlowLanded: badge().textContent });
            """
        )
        self.assertEqual(result["afterNewer"], "9")
        self.assertEqual(result["afterSlowLanded"], "9")

    def test_a_failed_read_marks_the_badge_unknown(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(2);
            await refreshDashboardBadge();
            fetchAnswer = null;
            await refreshDashboardBadge();
            report({ text: badge().textContent, hidden: badge().hidden });
            """
        )
        self.assertEqual(result["text"], "?")
        self.assertFalse(result["hidden"])

    def test_a_hidden_document_arms_no_poll_and_reads_again_on_the_way_back(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(1);
            wireDashboard();
            await settle();
            const wired = { intervals: calls.intervals, fetches: calls.fetches };
            document.hidden = true;
            document.fire('visibilitychange');
            await settle();
            const hidden = { intervals: calls.intervals, cleared: calls.cleared, fetches: calls.fetches };
            document.hidden = false;
            document.fire('visibilitychange');
            await settle();
            report({ wired, hidden, back: { intervals: calls.intervals, fetches: calls.fetches } });
            """
        )
        self.assertEqual(result["wired"]["intervals"], 1)
        self.assertEqual(result["wired"]["fetches"], 1)
        # Hidden: the timer is torn down and no request is made.
        self.assertEqual(result["hidden"]["intervals"], 1)
        self.assertGreaterEqual(result["hidden"]["cleared"], 1)
        self.assertEqual(result["hidden"]["fetches"], 1)
        # Back in front: armed again, and read once now rather than showing a
        # count from before the window was hidden until the next tick.
        self.assertEqual(result["back"]["intervals"], 2)
        self.assertEqual(result["back"]["fetches"], 2)

    def test_a_page_without_the_button_wires_nothing(self):
        """The dashboard window itself loads no button, and must not poll."""
        result = self._run_node(
            """
            byId.delete('dashboardBtn');
            fetchAnswer = snapshot(4);
            wireDashboard();
            await settle();
            report({ fetches: calls.fetches, intervals: calls.intervals, keydown: document.listenerCount('keydown') });
            """
        )
        self.assertEqual(result, {"fetches": 0, "intervals": 0, "keydown": 0})


if __name__ == "__main__":
    unittest.main()
