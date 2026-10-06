"""Git sidebar change-listener deferral and preservation (ISSUE-2026-062).

``explorer-git-watch.js`` is not DOM-free, so it is evaluated in a Node ``vm``
against a stubbed page — the same technique ``test_explorer_git_identity.py``
uses for ``explorer-git-sidebar.js``. That makes the deferral gates *executed*
rather than pattern-matched:

- retained DOM focus (a button, or an idle text control) must never alone hold
  a pending Git update, even after focus leaves and returns to the window;
- genuine interaction — active typing, IME composition, an active selection, a
  menu/modal, a pointer-down gesture — still defers, with a bounded settle for
  typing and a release path that ends the deferral;
- a ``pointercancel``, a window blur and a hidden page reconcile pointer/edit
  state so the wake on return flushes rather than waiting a stale gesture out;
- the newest deferred payload wins; a deferred payload is dropped rather than
  applied under a scope the pane has left; stale pane/session/scope responses
  are discarded; a hidden page neither polls nor applies on return past a single
  check;
- a tab swap wakes the shown panes once (debounced), a wake landing during a
  running pass re-runs it on the panes now shown, and the adaptive interval is
  measured from the state request alone (ISSUE-2026-064).

A second harness loads the *shipped* ``applyExplorerGitRepoQuiet`` so the quiet
apply's caret/selection/scroll preservation is executed too, including that it
restores the caret without stealing foreground focus after a blur.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"
WATCH_JS = STATIC_JS / "explorer-git-watch.js"
SIDEBAR_JS = STATIC_JS / "explorer-git-sidebar.js"
PIN_JS = STATIC_JS / "explorer-git-pin.js"
DIRECTORY_JS = STATIC_JS / "explorer-directory.js"

NODE = shutil.which("node")

# A live explorer Git sidebar pane in slot 0. Only the sidebar consumer is armed
# (no filesystem context, directory mode), so one state poll serves one baseline.
WATCH_HARNESS_PREAMBLE = r"""
const fs = require('fs');
const vm = require('vm');

// ── clock ────────────────────────────────────────────────────────────────────
let clock = 0;
function advanceClock(ms) { clock += ms; }

// ── element stub ─────────────────────────────────────────────────────────────
function makeElement(kind) {
    return {
        kind,
        id: '',
        editable: kind === 'textarea',
        _hover: false,
        scrollTop: 0,
        scrollHeight: 1000,
        selectionStart: 0,
        selectionEnd: 0,
        selectionDirection: 'none',
        _contains: new Set(),
        _focused: false,
        value: '',
        matches(selector) {
            if (selector === ':hover') return this._hover === true;
            if (selector === 'input, textarea, [contenteditable]') return this.editable === true;
            return false;
        },
        contains(el) { return el === this || this._contains.has(el); },
        addEventListener() {},
        focus() { this._focused = true; },
        setSelectionRange(start, end, direction) {
            this.selectionStart = start;
            this.selectionEnd = end;
            if (direction !== undefined) this.selectionDirection = direction;
        }
    };
}

const elements = {};
const docListeners = {};
const winListeners = {};

const document = {
    activeElement: null,
    visibilityState: 'visible',
    _hasFocus: true,
    hasFocus() { return this._hasFocus; },
    getElementById(id) { return elements[id] || null; },
    addEventListener(type, fn) { (docListeners[type] || (docListeners[type] = [])).push(fn); },
    removeEventListener() {}
};

function makePane() {
    return {
        _explorerMode: 'directory',
        _explorerPath: 'scope',
        _explorerGitSidebarOpen: true,
        _explorerGitRepoLoaded: true,
        _explorerGitRepoLoading: false,
        _explorerGitRepoRefreshing: false,
        _explorerGitAnchorPath: 'scope',
        _explorerGitAnchorKind: 'dir',
        _explorerGitRevision: 'rev-old',
        _explorerGitFollowBrowsing: false,
        _explorerGitPinnedPath: 'scope',
        _explorerGitCommitMessage: '',
        _explorerGitWatchInFlight: false,
        _explorerGitWatchSuspended: false,
        _explorerGitWatchFailures: 0,
        _explorerGitWatchPending: null,
        _explorerGitWatchNextAt: 0,
        _explorerGitActionBusy: false,
        _explorerFsBusy: false
    };
}

const pane = makePane();
const applyCalls = [];
let repoData = { revision: 'rev-new' };
let stateAnswer = { revision: 'rev-new' };
let fetchStateCalls = 0;
let stateGate = null;

function holdNextState() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    stateGate = { promise, resolve };
    return stateGate;
}

function dispatch(type, event) {
    ((docListeners[type] || [])).forEach(fn => fn(event || {}));
    ((winListeners[type] || [])).forEach(fn => fn(event || {}));
}

const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    Promise, Set, Map, JSON, Number, String, Boolean, Array, Object, Error,
    URLSearchParams, encodeURIComponent,
    Date: { now: () => clock },
    performance: { now: () => clock },
    setTimeout() { return 0; },
    clearTimeout() {},
    addEventListener(type, fn) { (winListeners[type] || (winListeners[type] = [])).push(fn); },
    removeEventListener() {},
    isExplorerPaneInstance: () => true,
    terminals: [pane],
    sessionIds: ['session-1'],
    document,
    fetch: async (url) => {
        // This harness exercises Git only. The independent membership poll has
        // no usable token and does not contribute to the Git request count.
        if (String(url).includes('/directory/state')) {
            return {ok:true,status:200,json:async()=>({complete:false,revision:''})};
        }
        if (String(url).includes('/state')) {
            fetchStateCalls += 1;
            if (stateGate) {
                await stateGate.promise;
            }
            return { ok: true, status: 200, json: async () => stateAnswer };
        }
        return { ok: false, status: 404, json: async () => ({}) };
    }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox); // pin policy
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox); // git sidebar
vm.runInContext(fs.readFileSync(process.argv[5], 'utf8'), sandbox); // directory ownership
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox); // watch

// Overrides go on *after* evaluation so calls resolve to the stubs at call time.
const realRefreshExplorerGitRepoQuiet = sandbox.refreshExplorerGitRepoQuiet;
// `applyExplorerGitRepoQuiet` is recorded (and still advances the pane's
// revision, the way the shipped one does) so the deferral tests can assert on
// *when* an apply lands without rebuilding the panel.
const quietRepoOptions = [];
sandbox.refreshExplorerGitRepoQuiet = async (index, options) => {
    quietRepoOptions.push(options || null);
    return repoData;
};
sandbox.applyExplorerGitRepoQuiet = (index, data, scopePath, scopeKind) => {
    applyCalls.push({ index, data, scopePath, scopeKind });
    const target = sandbox.terminals[index];
    if (target && data && typeof data.revision === 'string') {
        target._explorerGitRevision = data.revision;
    }
    return true;
};
sandbox.renderExplorerGitPanel = () => {};
sandbox.renderExplorerGitPanels = () => {};
sandbox.syncExplorerTabGitFromRepo = () => {};
sandbox.refreshExplorerFilesystemSurfacesQuiet = async () => true;
sandbox.refreshExplorerOpenFileQuiet = async () => true;
sandbox.refreshExplorerOverview = async () => {};

const emit = (payload) => process.stdout.write(JSON.stringify(payload));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer Git watch tests")
class ExplorerGitWatchHarness(unittest.TestCase):
    """Base harness: every body runs in a fresh Node process against the shipped
    watcher. Asserts are about which pane was written and when an apply landed."""

    def _run_node(self, body: str):
        preamble = WATCH_HARNESS_PREAMBLE
        script = (
            preamble
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(WATCH_JS), str(SIDEBAR_JS), str(PIN_JS), str(DIRECTORY_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class RetainedFocusTestCase(ExplorerGitWatchHarness):
    """Idle retained DOM focus must not hold a pending Git update."""

    def test_a_retained_focused_button_does_not_defer_when_unfocused(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const button = makeElement('button');
            panel._contains.add(button);
            document.activeElement = button;
            document._hasFocus = false;   // user is in another application
            await sandbox.explorerGitWatchTick();
            emit({
                applied: applyCalls.length,
                pending: Boolean(pane._explorerGitWatchPending),
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["applied"], 1)
        self.assertFalse(result["pending"])
        self.assertEqual(result["revision"], "rev-new")

    def test_a_retained_focused_textarea_idle_does_not_defer(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const textarea = makeElement('textarea');
            textarea.id = 'explorer-git-commit-message-0';
            panel._contains.add(textarea);
            document.activeElement = textarea;
            document._hasFocus = true;    // focused but not editing
            await sandbox.explorerGitWatchTick();
            emit({
                applied: applyCalls.length,
                pending: Boolean(pane._explorerGitWatchPending),
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["applied"], 1)
        self.assertFalse(result["pending"])
        self.assertEqual(result["revision"], "rev-new")

    def test_focus_return_does_not_rearm_a_retained_input(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const textarea = makeElement('textarea');
            panel._contains.add(textarea);
            document.activeElement = textarea;
            document._hasFocus = true;
            await sandbox.explorerGitWatchTick();
            const firstApply = applyCalls.length;
            // Return with the same retained focus; a new change must still apply
            // without any further interaction.
            stateAnswer = { revision: 'rev-new-2' };
            repoData = { revision: 'rev-new-2' };
            advanceClock(6000);
            await sandbox.explorerGitWatchTick();
            emit({
                firstApply,
                total: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["firstApply"], 1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["revision"], "rev-new-2")


class GenuineInteractionTestCase(ExplorerGitWatchHarness):
    """Active typing, composition and selection still defer; each ends."""

    def _setup_textarea(self):
        return """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const textarea = makeElement('textarea');
            textarea.id = 'explorer-git-commit-message-0';
            panel._contains.add(textarea);
            document.activeElement = textarea;
            document._hasFocus = true;
        """

    def test_typing_defers_until_the_settle_window_elapses(self):
        result = self._run_node(
            self._setup_textarea()
            + """
            dispatch('input', { target: textarea });   // genuine keystroke
            await sandbox.explorerGitWatchTick();
            const before = { applied: applyCalls.length, pending: Boolean(pane._explorerGitWatchPending) };
            advanceClock(801);                          // settle window ends
            await sandbox.explorerGitWatchTick();
            emit({
                before,
                afterApplied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["before"], {"applied": 0, "pending": True})
        self.assertEqual(result["afterApplied"], 1)
        self.assertEqual(result["revision"], "rev-new")

    def test_composition_defers_and_compositionend_releases(self):
        result = self._run_node(
            self._setup_textarea()
            + """
            dispatch('compositionstart', { target: textarea });
            await sandbox.explorerGitWatchTick();
            const before = { applied: applyCalls.length, pending: Boolean(pane._explorerGitWatchPending) };
            dispatch('compositionend', { target: textarea });
            emit({
                before,
                afterApplied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["before"], {"applied": 0, "pending": True})
        self.assertEqual(result["afterApplied"], 1)
        self.assertEqual(result["revision"], "rev-new")

    def test_an_active_selection_defers_until_focus_leaves(self):
        result = self._run_node(
            self._setup_textarea()
            + """
            textarea.selectionStart = 2;
            textarea.selectionEnd = 6;
            await sandbox.explorerGitWatchTick();
            const before = { applied: applyCalls.length, pending: Boolean(pane._explorerGitWatchPending) };
            document._hasFocus = false;   // remembered selection, no active edit
            await sandbox.explorerGitWatchTick();
            emit({
                before,
                afterApplied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["before"], {"applied": 0, "pending": True})
        self.assertEqual(result["afterApplied"], 1)
        self.assertEqual(result["revision"], "rev-new")


class PointerStateTestCase(ExplorerGitWatchHarness):
    """Missed release, cancellation, and the newest deferred payload."""

    def test_a_pointer_released_outside_the_window_is_cleared_on_blur(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            dispatch('pointerdown');
            await sandbox.explorerGitWatchTick();
            const deferred = { applied: applyCalls.length, pending: Boolean(pane._explorerGitWatchPending) };
            advanceClock(6000);
            await sandbox.explorerGitWatchTick();
            const afterRepeat = { applied: applyCalls.length, pending: Boolean(pane._explorerGitWatchPending) };
            dispatch('blur');   // pointerup never reached the page; blur reconciles
            emit({
                deferred,
                afterRepeat,
                afterBlurApplied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["deferred"], {"applied": 0, "pending": True})
        # A second poll while the stale gesture is held must not apply either.
        self.assertEqual(result["afterRepeat"], {"applied": 0, "pending": True})
        self.assertEqual(result["afterBlurApplied"], 1)
        self.assertEqual(result["revision"], "rev-new")

    def test_pointercancel_releases_a_pending_apply(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            dispatch('pointerdown');
            await sandbox.explorerGitWatchTick();
            const deferred = Boolean(pane._explorerGitWatchPending);
            dispatch('pointercancel');
            emit({
                deferred,
                applied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertTrue(result["deferred"])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["revision"], "rev-new")

    def test_the_newest_deferred_revision_replaces_the_older_one(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            dispatch('pointerdown');
            await sandbox.explorerGitWatchTick();     // pending = rev-new
            stateAnswer = { revision: 'rev-new-2' };
            repoData = { revision: 'rev-new-2' };
            advanceClock(6000);
            await sandbox.explorerGitWatchTick();     // pending replaced by rev-new-2
            dispatch('pointerup');
            emit({
                applied: applyCalls.length,
                appliedRevision: applyCalls[0] && applyCalls[0].data.revision,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["appliedRevision"], "rev-new-2")
        self.assertEqual(result["revision"], "rev-new-2")


class HiddenPageTestCase(ExplorerGitWatchHarness):
    """A hidden page suspends polling; the visible wake applies once."""

    def test_hidden_page_does_not_poll_and_wake_applies_once(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            document.visibilityState = 'hidden';
            await sandbox.explorerGitWatchTick();
            const hiddenFetches = fetchStateCalls;
            const hiddenApplied = applyCalls.length;
            document.visibilityState = 'visible';
            sandbox.explorerGitWatchWake();
            await new Promise(r => setTimeout(r, 0));
            emit({
                hiddenFetches,
                hiddenApplied,
                applied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["hiddenFetches"], 0)
        self.assertEqual(result["hiddenApplied"], 0)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["revision"], "rev-new")

    def test_hidden_page_clears_a_stale_pointer_gesture(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            dispatch('pointerdown');
            document.visibilityState = 'hidden';
            dispatch('visibilitychange');   // reconcile pointer/edit state
            await sandbox.explorerGitWatchTick();   // hidden: no poll
            document.visibilityState = 'visible';
            sandbox.explorerGitWatchWake();
            await new Promise(r => setTimeout(r, 0));
            emit({
                applied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["revision"], "rev-new")


class ViewSwapWakeTestCase(ExplorerGitWatchHarness):
    """A tab swap catches the shown panes up within one poll (ISSUE-2026-064)."""

    def test_a_swap_during_a_running_pass_reruns_on_the_shown_panes(self):
        result = self._run_node(
            """
            elements['explorer-git-panel-0'] = makeElement('panel');
            elements['explorer-git-panel-1'] = makeElement('panel');
            const polled = [];
            const transport = sandbox.fetch;
            sandbox.fetch = async url => {
                if (String(url).includes('/git/state')) {
                    polled.push(String(url).split('/')[3]);
                }
                return transport(url);
            };
            // Tab A: two explorer panes; the first poll hangs on `git status`.
            sandbox.terminals = [pane, makePane()];
            sandbox.sessionIds = ['session-a0', 'session-a1'];
            holdNextState();
            const running = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            // Swap to tab B, whose pane was left half-way through its interval.
            const shown = makePane();
            shown._explorerGitWatchNextAt = clock + 30000;
            sandbox.terminals = [shown];
            sandbox.sessionIds = ['session-b'];
            sandbox.explorerGitWatchWake();
            stateGate.resolve();
            await running;
            emit({
                polled,
                shownRevision: shown._explorerGitRevision,
                leftRevision: pane._explorerGitRevision,
                clock
            });
            """
        )
        # A0 was already in flight; A1 is never polled for a tab no longer
        # shown; B is polled by the immediate rerun, with no clock movement.
        self.assertEqual(result["polled"], ["session-a0", "session-b"])
        self.assertEqual(result["shownRevision"], "rev-new")
        self.assertEqual(result["leftRevision"], "rev-old")
        self.assertEqual(result["clock"], 0)

    def test_a_wake_during_a_running_pass_is_not_dropped(self):
        result = self._run_node(
            """
            elements['explorer-git-panel-0'] = makeElement('panel');
            elements['explorer-git-panel-1'] = makeElement('panel');
            // The pass skips slot 0 (not yet due) and hangs on slot 1.
            pane._explorerGitWatchNextAt = clock + 30000;
            sandbox.terminals.push(makePane());
            sandbox.sessionIds.push('session-2');
            holdNextState();
            const running = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            // A focus wake now makes slot 0 due, behind the running pass.
            sandbox.explorerGitWatchWake();
            stateGate.resolve();
            await running;
            emit({ polls: fetchStateCalls, revision: pane._explorerGitRevision });
            """
        )
        # Slot 1 once in the pass, then slots 0 and 1 again in the rerun: slot
        # 1's first answer predates the wake.
        self.assertEqual(result, {"polls": 3, "revision": "rev-new"})

    def test_a_pane_in_flight_at_the_wake_is_polled_again(self):
        # Leaving a tab and coming back restores the same pane list, so the
        # poll that was in flight passes every identity guard -- but it read the
        # repository before the wake. The rerun must poll that pane again.
        result = self._run_node(
            """
            elements['explorer-git-panel-0'] = makeElement('panel');
            const answers = [{ revision: 'rev-old' }, { revision: 'rev-new' }];
            const transport = sandbox.fetch;
            sandbox.fetch = async url => {
                if (!String(url).includes('/git/state')) return transport(url);
                const answer = answers.shift();
                const response = await transport(url);
                return { ...response, json: async () => answer };
            };
            holdNextState();
            const running = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            sandbox.explorerGitWatchWake();
            stateGate.resolve();
            await running;
            emit({
                polls: fetchStateCalls,
                revision: pane._explorerGitRevision,
                applied: applyCalls.length,
                clock
            });
            """
        )
        self.assertEqual(result, {"polls": 2, "revision": "rev-new", "applied": 1, "clock": 0})

    def test_fast_tab_cycling_wakes_the_shown_panes_once(self):
        result = self._run_node(
            """
            elements['explorer-git-panel-0'] = makeElement('panel');
            const timers = new Map();
            let nextTimer = 1;
            sandbox.setTimeout = (fn, ms) => {
                timers.set(nextTimer, { fn, ms });
                return nextTimer++;
            };
            sandbox.clearTimeout = id => { timers.delete(id); };
            pane._explorerGitWatchNextAt = clock + 30000;
            sandbox.explorerGitWatchWakeVisible();
            sandbox.explorerGitWatchWakeVisible();
            sandbox.explorerGitWatchWakeVisible();
            const settle = [...timers.values()];
            const beforeSettle = fetchStateCalls;
            timers.clear();
            settle.forEach(timer => timer.fn());
            await new Promise(r => setTimeout(r, 0));
            emit({
                pending: settle.length,
                settleMs: settle.map(timer => timer.ms),
                beforeSettle,
                polls: fetchStateCalls,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["settleMs"], [200])
        self.assertEqual(result["beforeSettle"], 0)
        self.assertEqual(result["polls"], 1)
        self.assertEqual(result["revision"], "rev-new")

    def test_a_swap_wake_waits_while_the_page_is_hidden(self):
        result = self._run_node(
            """
            elements['explorer-git-panel-0'] = makeElement('panel');
            let settle = null;
            sandbox.setTimeout = fn => { settle = fn; return 1; };
            sandbox.explorerGitWatchWakeVisible();
            document.visibilityState = 'hidden';
            settle();
            await new Promise(r => setTimeout(r, 0));
            emit({ polls: fetchStateCalls, applied: applyCalls.length });
            """
        )
        self.assertEqual(result, {"polls": 0, "applied": 0})


class BackgroundStatusBoundTestCase(ExplorerGitWatchHarness):
    """The watcher's own refetch asks for the server's background Git bound."""

    def test_only_the_watcher_refetch_is_marked_background(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            panel.classList = { add() {}, remove() {} };
            elements['explorer-git-panel-0'] = panel;
            await sandbox.explorerGitWatchTick();
            const urls = [];
            sandbox.fetch = async url => {
                urls.push(String(url));
                return { ok: false, status: 500, json: async () => ({}) };
            };
            await realRefreshExplorerGitRepoQuiet(0, { background: true });
            await realRefreshExplorerGitRepoQuiet(0);
            emit({
                watcher: quietRepoOptions,
                background: new URL('http://x' + urls[0]).searchParams.get('background'),
                interactive: new URL('http://x' + urls[1]).searchParams.get('background')
            });
            """
        )
        self.assertEqual(result["watcher"], [{"background": True}])
        self.assertEqual(result["background"], "1")
        self.assertIsNone(result["interactive"])


class AdaptiveIntervalTestCase(ExplorerGitWatchHarness):
    """The next poll is spaced by the state request, not the refresh it caused."""

    def test_a_slow_refresh_after_a_change_does_not_stretch_the_interval(self):
        result = self._run_node(
            """
            elements['explorer-git-panel-0'] = makeElement('panel');
            const transport = sandbox.fetch;
            sandbox.fetch = async url => {
                if (String(url).includes('/git/state')) advanceClock(1000);
                return transport(url);
            };
            sandbox.refreshExplorerGitRepoQuiet = async () => {
                advanceClock(4000);   // the full /git/repo refetch
                return repoData;
            };
            await sandbox.explorerGitWatchTick();
            emit({
                lastMs: pane._explorerGitWatchLastMs,
                delay: pane._explorerGitWatchNextAt - clock,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["revision"], "rev-new")
        self.assertEqual(result["lastMs"], 1000)
        # 1000 ms x 6, not (1000 + 4000) ms x 6 = 30 s.
        self.assertEqual(result["delay"], 6000)


class ReviewInteractionRegressionTestCase(ExplorerGitWatchHarness):
    def test_element_blur_during_a_pointer_press_keeps_the_gesture(self):
        result = self._run_node("""
            elements['explorer-git-panel-0'] = makeElement('panel');
            dispatch('pointerdown');
            await sandbox.explorerGitWatchTick();
            docListeners.blur.forEach(fn => fn({ target: makeElement('textarea') }));
            const before = applyCalls.length;
            dispatch('pointerup');
            emit({ before, after: applyCalls.length });
        """)
        self.assertEqual(result, {"before": 0, "after": 1})

    def test_hidden_events_cannot_apply_an_already_pending_payload(self):
        result = self._run_node("""
            elements['explorer-git-panel-0'] = makeElement('panel');
            dispatch('pointerdown');
            await sandbox.explorerGitWatchTick();
            document.visibilityState = 'hidden';
            dispatch('visibilitychange');
            dispatch('blur');
            dispatch('pointercancel');
            const hidden = applyCalls.length;
            document.visibilityState = 'visible';
            await sandbox.explorerGitWatchTick();
            emit({ hidden, after: applyCalls.length });
        """)
        self.assertEqual(result, {"hidden": 0, "after": 1})

    def test_search_input_composition_is_protected_until_it_ends(self):
        result = self._run_node("""
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const search = makeElement('textarea');
            search.id = 'search-input';
            panel._contains.add(search);
            document.activeElement = search;
            dispatch('compositionstart', { target: search });
            await sandbox.explorerGitWatchTick();
            advanceClock(6000);
            await sandbox.explorerGitWatchTick();
            const before = applyCalls.length;
            dispatch('compositionend', { target: search });
            emit({ before, after: applyCalls.length });
        """)
        self.assertEqual(result, {"before": 0, "after": 1})

    def test_retained_selection_and_hover_do_not_rearm_on_focus_return(self):
        result = self._run_node("""
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const textarea = makeElement('textarea');
            textarea.selectionStart = 2;
            textarea.selectionEnd = 5;
            panel._contains.add(textarea);
            panel._hover = true;
            panel.scrollTop = 42;
            document.activeElement = textarea;
            await sandbox.explorerGitWatchTick();
            document.visibilityState = 'hidden';
            dispatch('visibilitychange');
            document.visibilityState = 'visible';
            sandbox.explorerGitWatchWake();
            await new Promise(r => setTimeout(r, 0));
            const resumed = applyCalls.length;
            dispatch('input', { target: textarea });
            stateAnswer = repoData = { revision: 'rev-new-2' };
            advanceClock(6000);
            await sandbox.explorerGitWatchTick();
            emit({ resumed, duringSelection: applyCalls.length });
        """)
        self.assertEqual(result, {"resumed": 1, "duringSelection": 1})

    def test_text_selection_in_sidebar_rows_defers_the_rebuild(self):
        result = self._run_node("""
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            const rowText = {};
            panel._contains.add(rowText);
            let selected = true;
            sandbox.getSelection = () => ({ isCollapsed: !selected, anchorNode: rowText });
            await sandbox.explorerGitWatchTick();
            const before = applyCalls.length;
            selected = false;
            await sandbox.explorerGitWatchTick();
            emit({ before, after: applyCalls.length });
        """)
        self.assertEqual(result, {"before": 0, "after": 1})


class StaleResponseTestCase(ExplorerGitWatchHarness):
    """A response answered under another pane/session/scope never applies."""

    def test_a_response_to_a_replaced_pane_is_dropped(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            holdNextState();
            const pending = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            const replacement = makePane();
            sandbox.terminals[0] = replacement;
            stateGate.resolve();
            await pending;
            emit({
                applied: applyCalls.length,
                incomingRevision: replacement._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["incomingRevision"], "rev-old")

    def test_a_response_to_a_changed_scope_is_dropped(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            holdNextState();
            const pending = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            pane._explorerGitFollowBrowsing = true;
            pane._explorerPath = 'other';
            stateGate.resolve();
            await pending;
            emit({
                applied: applyCalls.length,
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["revision"], "rev-old")

    def test_a_deferred_payload_is_dropped_under_a_new_scope(self):
        result = self._run_node(
            """
            const panel = makeElement('panel');
            elements['explorer-git-panel-0'] = panel;
            dispatch('pointerdown');
            await sandbox.explorerGitWatchTick();
            const deferred = Boolean(pane._explorerGitWatchPending);
            // The pane navigates to another scope while the payload is deferred.
            pane._explorerGitFollowBrowsing = true;
            pane._explorerPath = 'other';
            dispatch('pointerup');
            emit({
                deferred,
                applied: applyCalls.length,
                pending: Boolean(pane._explorerGitWatchPending),
                revision: pane._explorerGitRevision
            });
            """
        )
        self.assertTrue(result["deferred"])
        self.assertEqual(result["applied"], 0)
        self.assertFalse(result["pending"])
        self.assertEqual(result["revision"], "rev-old")


# The shipped quiet apply, executed against a small-but-real DOM-ish panel so the
# caret/selection/scroll preservation is asserted rather than stubbed.
APPLY_HARNESS_PREAMBLE = r"""
const fs = require('fs');
const vm = require('vm');

function makeElement(kind) {
    return {
        kind,
        id: '',
        editable: kind === 'textarea',
        scrollTop: 0,
        scrollHeight: 1000,
        selectionStart: 0,
        selectionEnd: 0,
        selectionDirection: 'none',
        _contains: new Set(),
        focusCount: 0,
        matches() { return false; },
        contains(el) { return el === this || this._contains.has(el); },
        focus() { this.focusCount += 1; },
        setSelectionRange(start, end, direction) {
            this.selectionStart = start;
            this.selectionEnd = end;
            this.selectionDirection = direction;
        }
    };
}

const panel = makeElement('panel');
const input = makeElement('textarea');
input.id = 'explorer-git-commit-message-0';
input.selectionStart = 3;
input.selectionEnd = 7;
input.selectionDirection = 'backward';
panel._contains.add(input);
panel.scrollTop = 42;

const replacement = makeElement('textarea');
replacement.id = 'explorer-git-commit-message-0';
panel.querySelector = selector =>
    String(selector).includes(replacement.id) ? replacement : null;

const document = {
    activeElement: input,
    _hasFocus: true,
    hasFocus() { return this._hasFocus; },
    getElementById(id) { return id === 'explorer-git-panel-0' ? panel : null; },
    addEventListener() {},
    removeEventListener() {}
};

function makePane() {
    return {
        _explorerGitSidebarOpen: true,
        _explorerGitFollowBrowsing: false,
        _explorerGitPinnedPath: 'scope',
        _explorerGitRepo: null,
        _explorerGitRepoLoaded: false,
        _explorerGitRevision: ''
    };
}

const pane = makePane();
const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    Promise, Set, Map, JSON, Number, String, Boolean, Array, Object, Error,
    URLSearchParams, encodeURIComponent,
    terminals: [pane],
    sessionIds: ['session-1'],
    document,
    CSS: { escape: s => s }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox); // pin policy
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox); // git sidebar

// Paint-only collaborators, not the subject of this harness.
sandbox.renderExplorerGitPanel = () => {};
sandbox.renderExplorerGitPanels = () => {};
sandbox.syncExplorerTabGitFromRepo = () => {};

const emit = (payload) => process.stdout.write(JSON.stringify(payload));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer Git watch tests")
class QuietApplyPreservationTestCase(unittest.TestCase):
    """The shipped ``applyExplorerGitRepoQuiet`` preserves caret/scroll, and does
    not steal foreground focus after a blur."""

    def _run_node(self, body: str):
        script = (
            APPLY_HARNESS_PREAMBLE
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(WATCH_JS), str(SIDEBAR_JS), str(PIN_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_focused_apply_restores_caret_direction_and_scroll(self):
        result = self._run_node(
            """
            const ok = sandbox.applyExplorerGitRepoQuiet(0, { revision: 'rev-new' }, 'scope', 'dir');
            emit({
                ok,
                revision: pane._explorerGitRevision,
                loaded: pane._explorerGitRepoLoaded,
                scroll: panel.scrollTop,
                focusCount: replacement.focusCount,
                start: replacement.selectionStart,
                end: replacement.selectionEnd,
                direction: replacement.selectionDirection
            });
            """
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], "rev-new")
        self.assertTrue(result["loaded"])
        self.assertEqual(result["scroll"], 42)
        self.assertEqual(result["focusCount"], 1)
        self.assertEqual(result["start"], 3)
        self.assertEqual(result["end"], 7)
        self.assertEqual(result["direction"], "backward")

    def test_blurred_apply_restores_the_caret_without_stealing_focus(self):
        result = self._run_node(
            """
            document._hasFocus = false;   // window left; retaining a focused input
            const ok = sandbox.applyExplorerGitRepoQuiet(0, { revision: 'rev-new' }, 'scope', 'dir');
            emit({
                ok,
                revision: pane._explorerGitRevision,
                scroll: panel.scrollTop,
                focusCount: replacement.focusCount,
                start: replacement.selectionStart,
                end: replacement.selectionEnd,
                direction: replacement.selectionDirection
            });
            """
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], "rev-new")
        self.assertEqual(result["scroll"], 42)
        self.assertEqual(result["focusCount"], 0)
        self.assertEqual(result["start"], 3)
        self.assertEqual(result["end"], 7)
        self.assertEqual(result["direction"], "backward")

    def test_search_caret_survives_repeated_background_rebuilds(self):
        result = self._run_node("""
            document._hasFocus = false;
            input.id = replacement.id = 'explorer-git-commit-search-0';
            sandbox.applyExplorerGitRepoQuiet(0, { revision: 'one' }, 'scope', 'dir');
            document.activeElement = {};
            replacement.selectionStart = replacement.selectionEnd = 0;
            sandbox.applyExplorerGitRepoQuiet(0, { revision: 'two' }, 'scope', 'dir');
            emit({ start: replacement.selectionStart, end: replacement.selectionEnd,
                direction: replacement.selectionDirection, focusCount: replacement.focusCount });
        """)
        self.assertEqual(result, {"start": 3, "end": 7, "direction": "backward", "focusCount": 0})


if __name__ == "__main__":
    unittest.main()
