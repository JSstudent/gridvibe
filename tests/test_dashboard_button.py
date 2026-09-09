"""The dashboard button both host pages carry, driven through its own wiring.

`dashboard.js` is small on purpose — the dashboard itself is a dialog with its
own module — so what is pinned here is the whole of what the host pages know
about it:

- **The button raises the dialog on this page, and opens nothing.** It was a
  window: native first through the pywebview bridge, a named `window.open`
  second. Both are gone, and the assertion that neither happens is the point —
  a leftover `window.open` is how "it opens in a new tab sometimes" comes back.
- **A second press puts it away.** A control that brings a surface up and
  cannot take it away is half a control, and the button and the chord are the
  same control.
- **The chord is `Alt+A`, matched on `event.code`.** Ctrl is excluded because
  AltGr arrives as Ctrl+Alt on Windows, and a page that says the chord may not
  fire from here — the same answer it already gives Alt+X — is obeyed rather
  than second-guessed.
- **The badge counts working agents, not open ones.** How many agent panes
  exist is something the reader already knows; a badge tallying those is always
  on and so signals nothing. It paints `totals.working`, and a payload full of
  idle agents leaves it hidden — its absence is a reading too.
- **The badge is honest without opening anything**, and a slow answer that
  lands after a newer one was asked for is dropped rather than painted.
- **A hidden document costs nothing**: the poll is torn down rather than left
  running, and reads once on the way back.
- **The cross-window dim is started here and driven from there.** This page
  starts the lease; the dialog turns it on and off, through the one named
  function rather than by reaching for the handle.
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
const calls = { fetches: 0, opens: [], bridge: [], intervals: 0, cleared: 0, lease: [] };

Object.assign(globalThis, {
    /* Real timers would keep the process alive, and the cadence itself is not
       what these cases are about — only whether one is armed at all. */
    setInterval: () => { calls.intervals += 1; return calls.intervals; },
    clearInterval: () => { calls.cleared += 1; },
    /* Still here, and still counted: what these cases assert about it now is
       that it is never reached. A leftover window.open is how "it opens in a
       new tab sometimes" comes back. */
    open(url, name) {
        calls.opens.push({ url, name });
        return { focus() {} };
    }
});

/* Still here for the same reason `open` is. Nothing in this module may reach
   for a native window any more. */
Object.defineProperty(globalThis, 'pywebview', {
    get() {
        return {
            api: new Proxy({}, {
                get(_target, name) {
                    calls.bridge.push(String(name));
                    return () => Promise.resolve({ ok: true });
                }
            })
        };
    }
});

/* dashboard-dialog.js's half of the pair, recorded rather than loaded: what the
   button owes the dialog is one call, and what the dialog does with it is that
   module's own test. */
let dialogUp = false;
let wiredDialog = 0;
/* Assigned onto the global rather than declared, because "this page did not
   load the dialog module" is a case below and a declaration cannot be taken
   away. */
globalThis.toggleAgentDashboardDialog = () => {
    dialogUp = !dialogUp;
    return dialogUp;
};
globalThis.wireAgentDashboard = () => { wiredDialog += 1; };

/* The focus lease this page starts. `setDashboardActive` is the whole of what
   dashboard-dialog.js asks of it. */
globalThis.GridVibeDashboardFocus = {
    started: 0,
    start() {
        this.started += 1;
        return { setDashboardActive: active => calls.lease.push(active) };
    }
};

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

/* `working` is what the badge paints; `agents` rides along because it is on
   every real payload and a case below asserts the badge does not read it. */
function snapshot(working, agents = working) {
    return {
        generated_at: 100,
        workspaces: [],
        totals: { workspaces: 1, sessions: 1, agents, working }
    };
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

    def test_the_button_raises_the_dialog_and_opens_no_window_at_all(self):
        """It was a window — a native one through the bridge, a named tab
        otherwise. Both are gone, and asserting that neither is reached is the
        point: a leftover `window.open` here is how "it opens in a new tab
        sometimes" comes back."""
        result = self._run_node(
            """
            const raised = openAgentDashboard();
            report({ raised, up: dialogUp, opens: calls.opens, bridge: calls.bridge });
            """
        )
        self.assertTrue(result["raised"])
        self.assertTrue(result["up"])
        self.assertEqual(result["opens"], [])
        self.assertEqual(result["bridge"], [])

    def test_a_second_press_puts_it_away(self):
        """The button and the chord are one control, and a control that brings
        a surface up and cannot take it away is half a control."""
        result = self._run_node(
            """
            const states = [openAgentDashboard(), openAgentDashboard(), openAgentDashboard()];
            report({ states, opens: calls.opens.length });
            """
        )
        self.assertEqual(result["states"], [True, False, True])
        self.assertEqual(result["opens"], 0)

    def test_the_press_is_the_button_press_and_stops_there(self):
        """It sits inside the session bar and inside the launcher's action row,
        both of which have handlers of their own."""
        result = self._run_node(
            """
            const seen = [];
            openAgentDashboard({
                preventDefault() { seen.push('preventDefault'); },
                stopPropagation() { seen.push('stopPropagation'); }
            });
            report({ seen, up: dialogUp });
            """
        )
        self.assertEqual(result["seen"], ["preventDefault", "stopPropagation"])
        self.assertTrue(result["up"])

    def test_a_page_without_the_dialog_module_refuses_rather_than_throwing(self):
        """The button's partial and the dialog's are two includes, so a page
        that ships one without the other is a real state — and it must be a
        press that does nothing, never an exception in an inline onclick."""
        result = self._run_node(
            """
            globalThis.toggleAgentDashboardDialog = undefined;
            report({ raised: openAgentDashboard() });
            """
        )
        self.assertFalse(result["raised"])

    def test_wiring_the_button_wires_the_dialog_and_starts_the_dim_lease(self):
        """One feature, one call: a page carrying the button carries the
        surface it opens, and the lease that dims the other windows while it is
        up is started here because this is what starts once per page."""
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            wireDashboard();
            wireDashboard();
            await settle();
            markDashboardFocusActive(true);
            markDashboardFocusActive(false);
            report({
                wiredDialog,
                started: GridVibeDashboardFocus.started,
                lease: calls.lease
            });
            """
        )
        # Once, however many times the page asks.
        self.assertEqual(result["wiredDialog"], 1)
        self.assertEqual(result["started"], 1)
        self.assertEqual(result["lease"], [True, False])

    def test_a_page_that_never_started_a_lease_answers_harmlessly(self):
        """`GridVibeDashboardFocus` is a separate script tag, so "it is not
        there" is a real state and must not be an exception thrown out of the
        dialog's own open."""
        result = self._run_node(
            """
            globalThis.GridVibeDashboardFocus = undefined;
            fetchAnswer = snapshot(0);
            wireDashboard();
            await settle();
            markDashboardFocusActive(true);
            report({ ok: true });
            """
        )
        self.assertTrue(result["ok"])

    def test_the_chord_raises_it_and_takes_the_key(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            wireDashboard();
            await settle();
            const prevented = chord();
            await settle();
            report({ prevented, up: dialogUp, opens: calls.opens.length });
            """
        )
        self.assertTrue(result["prevented"])
        self.assertTrue(result["up"])
        self.assertEqual(result["opens"], 0)

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
            report({ prevented, up: dialogUp });
            """
        )
        self.assertEqual(result["prevented"], [False] * 6)
        self.assertFalse(result["up"])

    def test_the_page_decides_whether_the_chord_may_fire_from_here(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(0);
            blockChord = true;
            wireDashboard();
            await settle();
            const inAPane = chord({ target: 'a-focused-pane' });
            const afterPane = dialogUp;
            const onThePage = chord({ target: 'the-page' });
            await settle();
            report({ inAPane, afterPane, onThePage, up: dialogUp });
            """
        )
        self.assertFalse(result["inAPane"])
        self.assertFalse(result["afterPane"])
        self.assertTrue(result["onThePage"])
        self.assertTrue(result["up"])

    def test_the_badge_is_filled_without_anything_being_opened(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot(3);
            const before = { text: badge().textContent, hidden: badge().hidden, fetches: calls.fetches };
            wireDashboard();
            await settle();
            report({ before, after: { text: badge().textContent, hidden: badge().hidden, fetches: calls.fetches }, up: dialogUp });
            """
        )
        self.assertEqual(result["before"]["fetches"], 0)
        self.assertEqual(result["after"], {"text": "3", "hidden": False, "fetches": 1})
        self.assertFalse(result["up"])

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

    def test_the_badge_counts_working_agents_and_not_open_ones(self):
        """The number on the button is `totals.working`. A window holding nine
        agents that are all sitting at a prompt has nothing to go and look at,
        so it wears no badge at all — reading `totals.agents` there is a number
        that is always on and never means anything."""
        result = self._run_node(
            """
            fetchAnswer = snapshot(2, 9);
            wireDashboard();
            await settle();
            const working = { text: badge().textContent, hidden: badge().hidden };
            fetchAnswer = snapshot(0, 9);
            await refreshDashboardBadge();
            report({ working, idle: { text: badge().textContent, hidden: badge().hidden } });
            """
        )
        self.assertEqual(result["working"], {"text": "2", "hidden": False})
        self.assertTrue(result["idle"]["hidden"])

    def test_a_payload_without_the_working_count_is_not_painted_as_zero(self):
        """The field the badge paints is the field it validates. Accepting a
        payload on `totals.agents` while drawing `totals.working` is how a badge
        comes to report "nothing running" off an answer that never carried the
        number."""
        result = self._run_node(
            """
            fetchAnswer = snapshot(4);
            await refreshDashboardBadge();
            const before = { text: badge().textContent, hidden: badge().hidden };
            fetchAnswer = { generated_at: 100, workspaces: [], totals: { workspaces: 1, sessions: 1, agents: 4 } };
            await refreshDashboardBadge();
            report({ before, after: { text: badge().textContent, hidden: badge().hidden } });
            """
        )
        self.assertEqual(result["before"], {"text": "4", "hidden": False})
        # Reported as unreadable rather than quietly shown as none running.
        self.assertEqual(result["after"], {"text": "?", "hidden": False})

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
        """A page that carries neither the button nor the surface it opens must
        not poll for a badge it has nowhere to draw."""
        result = self._run_node(
            """
            byId.delete('dashboardBtn');
            fetchAnswer = snapshot(4);
            wireDashboard();
            await settle();
            report({
                fetches: calls.fetches,
                intervals: calls.intervals,
                keydown: document.listenerCount('keydown'),
                wiredDialog
            });
            """
        )
        self.assertEqual(
            result,
            {"fetches": 0, "intervals": 0, "keydown": 0, "wiredDialog": 0},
        )


if __name__ == "__main__":
    unittest.main()
