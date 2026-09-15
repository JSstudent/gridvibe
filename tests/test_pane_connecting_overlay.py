"""The "Connecting…" overlay's life, executed rather than asserted as source.

A pane wears the overlay while its transport is coming up, and the connected
`session_status` event is what takes it off. That event is not always available
to do it: a pane that connects while its group is not the visible one is
returned from before the overlay is reached, and a relaunch can raise its
spinner behind the event that would have removed it. Either way the pane is
connected, attached, and covered by a spinner for the life of the window
(ISSUE-2026-053).

So the removal is not the status event's alone. The real `initialLoad()` and
`refreshStatuses()` are lifted out of ``terminals.js`` and run in Node against a
stub page, and what is pinned here is that each of them heals a connected,
already-attached pane -- while leaving a pane that is genuinely still coming up
wearing the spinner it has earned, and one that failed or dropped wearing its
own overlay. `syncPanePlaceholder()`, which the relaunch calls to take back a
spinner for a request that failed, is driven here too.

The relaunch's own half -- painting the pane before it asks -- lives in
`tests/test_terminal_shell_menu.py`.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINALS_JS = (REPO_ROOT / "web" / "static" / "js" / "terminals.js").read_text(
    encoding="utf-8"
)

NODE = shutil.which("node")


def _js_function_source(script: str, name: str) -> str:
    """Return one top-level JS function's source, brace-matched."""
    start = script.index(f"function {name}(")
    if script[max(0, start - 6):start] == "async ":
        start -= 6
    depth = 0
    for index in range(script.index("{", script.index(")", start)), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# The shipped functions, loaded whole: the two load paths, the overlay helpers
# they paint through, and the attach that removes one on its own.
TERMINALS_SOURCE = "\n\n".join(
    _js_function_source(TERMINALS_JS, name)
    for name in (
        "ensurePanePlaceholder",
        "showPlaceholderRetryState",
        "showPlaceholderError",
        "showPlaceholderDisconnected",
        "showPlaceholderConnecting",
        "isRetryableDisconnect",
        "syncPanePlaceholder",
        "attachTerminal",
        "initialLoad",
        "refreshStatuses",
    )
)

# Everything those functions reach for. The page is a stub, but nothing that
# decides whether an overlay stays on the pane is: the placeholders are built,
# appended and removed through the real helpers against a DOM that really holds
# them, so "the overlay is gone" is read off the page rather than off a spy.
HARNESS_STUBS = r"""
const calls = { statuses: [], opened: [], rebuilt: [], redrawn: [] };

class StubNode {
    constructor(id = '') {
        this.id = id;
        this.children = [];
        this.parentNode = null;
        this.innerHTML = '';
        this.textContent = '';
        this.className = '';
        this.dataset = {};
        this.style = {};
        const names = new Set();
        this.classList = {
            names,
            add: (...values) => values.forEach(value => names.add(value)),
            remove: (...values) => values.forEach(value => names.delete(value)),
            contains: value => names.has(value)
        };
    }
    appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        if (child.id) { byId.set(child.id, child); }
        return child;
    }
    remove() {
        if (this.parentNode) {
            this.parentNode.children = this.parentNode.children.filter(node => node !== this);
            this.parentNode = null;
        }
        if (byId.get(this.id) === this) { byId.delete(this.id); }
    }
    /* The retry placeholders wire a button inside their own markup. */
    querySelector() { return new StubNode(); }
    addEventListener() {}
}

const byId = new Map();
function element(id) {
    if (!byId.has(id)) { byId.set(id, new StubNode(id)); }
    return byId.get(id);
}
const document = {
    createElement: () => new StubNode(),
    getElementById: id => byId.get(id) || null
};
/* The chrome every load reaches for, and the layout class its "is this the
   view I am already showing" check compares against. */
element('sessionLabel');
element('emptyState');
element('terminalsGrid').className = 'layout-1';

function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/* ── The page's own state ── */
var terminals = [];
var sessionIds = [];
var activeGroupId = 'g1';
var visibleGroupId = 'g1';
var gridBuilt = true;
var workspaceGone = false;
var activeLoadToken = 0;
var pendingCloseClientState = null;
var pendingSplitRestore = null;
var sessionGroups = [];
var knownGroupIds = [];
var socket = null;
var cachedGroupViews = new Map();
var SESSIONS = [];

/* ── Everything the two load paths call and this test does not decide ── */
async function loadSessionGroups() { return false; }
async function resetSessionView() {}
function getSessionApiPath() { return '/api/sessions'; }
async function fetch() {
    return { ok: true, json: async () => ({ sessions: SESSIONS, layout: 'grid' }) };
}
function getLayoutClass() { return 'layout-1'; }
function hasMatchingSessionViews() { return true; }
function applyConfiguredSurfaceMode() {}
function applyConfiguredAgentSidebarSide() {}
function applyWorkspaceLayoutSnapshot() {}
function cacheVisibleGroupView() {}
function dropCachedGroupView(groupId) { cachedGroupViews.delete(groupId); }
/* The real restore re-mounts the cached panes; here they are already in
   `terminals`, which is the state it would have left behind. */
function restoreCachedGroupView() { return true; }
function buildGrid() { calls.rebuilt.push(true); }
function setStatus(index, status) { calls.statuses.push([index, status]); }
function syncPaneIdentityChrome() {}
function setSessionRoute() {}
function isExplorerSession(session) { return session?.startup_mode === 'explorer'; }
function isBrowserSession(session) { return session?.startup_mode === 'browser'; }
function syncExplorerPane() {}
function restoreExplorerPaneFromClose() {}
function restoreExplorerSidebarState() {}
function updateSessionChrome() {}
function captureTerminalViewportState() { return null; }
function restoreTerminalViewportState() {}
async function ensureAttachedTerminalsReady() {}
async function redrawAttachedTerminals(indices) { calls.redrawn.push(...indices); }
async function redrawAttachedTerminalsLikeFullscreen() {}
function settleWorkspaceFocusTarget() {}
function renderSessionTabs() {}
function retrySessionConnection() {}
function applyGroupFontOverride() {}
function _wireClipboard() {}
function observeTerminalResize() {}
function scheduleFit() {}

/* ── One pane, and whatever it is wearing ── */
function makePane(index, { attached = true, overlay = '' } = {}) {
    element(`tw-${index}`);
    element(`tcanvas-${index}`);
    terminals[index] = {
        _attached: attached,
        _session: null,
        term: { open() { calls.opened.push(index); } }
    };
    sessionIds[index] = `s${index + 1}`;
    if (overlay === 'connecting') {
        showPlaceholderConnecting(index);
    } else if (overlay) {
        /* An overlay the pane could only be wearing from its own status. */
        showPlaceholderDisconnected(index);
    }
    return terminals[index];
}

function session(index, status, extra) {
    return Object.assign({
        session_id: `s${index + 1}`,
        status,
        startup_mode: 'terminal'
    }, extra || {});
}

/* What the reader sees over the pane, named. */
function overlayOf(index) {
    const ph = document.getElementById(`ph-${index}`);
    if (!ph) { return 'none'; }
    if (ph.innerHTML.includes('spinner')) { return 'connecting'; }
    if (ph.innerHTML.includes('Connection Error')) { return 'error'; }
    if (ph.innerHTML.includes('Disconnected')) { return 'disconnected'; }
    return 'other';
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for pane overlay tests")
class PaneOverlayTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + TERMINALS_SOURCE
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


class StaleConnectingOverlayTestCase(PaneOverlayTestCase):
    """A connected pane that is already attached wears no overlay."""

    def test_a_status_refresh_heals_an_overlay_nobody_removed(self):
        result = self._run_node(
            """
            makePane(0, { attached: true, overlay: 'connecting' });
            SESSIONS = [session(0, 'connected')];
            const before = overlayOf(0);
            await refreshStatuses();
            report({ before, after: overlayOf(0), opened: calls.opened });
            """
        )
        self.assertEqual(result["before"], "connecting")
        self.assertEqual(result["after"], "none")
        # Healed, not rebuilt: the pane keeps the xterm it was already showing.
        self.assertEqual(result["opened"], [])

    def test_returning_to_the_tab_heals_a_pane_that_connected_out_of_sight(self):
        """The connected event reached a pane whose group was not visible, so it
        returned before the removal. Coming back to that tab is a load, and the
        load is what finally takes the spinner off."""
        result = self._run_node(
            """
            makePane(0, { attached: true, overlay: 'connecting' });
            SESSIONS = [session(0, 'connected')];
            /* The tab being switched to is not the one on screen, and its view
               is cached -- which is exactly the pane the status event skipped. */
            visibleGroupId = 'g1';
            activeGroupId = 'g2';
            cachedGroupViews.set('g2', {
                terminals: [terminals[0]], className: 'layout-1', sessionIds: ['s1']
            });
            await initialLoad();
            report({ overlay: overlayOf(0), rebuilt: calls.rebuilt, opened: calls.opened });
            """
        )
        self.assertEqual(result["overlay"], "none")
        self.assertEqual(result["rebuilt"], [])
        self.assertEqual(result["opened"], [])

    def test_a_pane_still_coming_up_keeps_its_spinner(self):
        """The overlay is only stale once the pane is connected; a pane that is
        genuinely pending must still look like it."""
        result = self._run_node(
            """
            makePane(0, { attached: true, overlay: 'connecting' });
            SESSIONS = [session(0, 'connecting')];
            await refreshStatuses();
            const afterRefresh = overlayOf(0);
            await initialLoad();
            report({ afterRefresh, afterLoad: overlayOf(0) });
            """
        )
        self.assertEqual(result["afterRefresh"], "connecting")
        self.assertEqual(result["afterLoad"], "connecting")

    def test_a_pane_that_dropped_or_failed_still_gets_its_own_overlay(self):
        """The new branch sits inside the same ladder it always did, so nothing
        that is not connected reaches it."""
        result = self._run_node(
            """
            makePane(0, { attached: true, overlay: 'connecting' });
            SESSIONS = [session(0, 'disconnected')];
            await refreshStatuses();
            const dropped = overlayOf(0);
            makePane(1, { attached: false });
            SESSIONS = [session(0, 'disconnected'), session(1, 'error', {
                error_message: 'Host unreachable'
            })];
            await refreshStatuses();
            report({ dropped, failed: overlayOf(1) });
            """
        )
        self.assertEqual(result["dropped"], "disconnected")
        self.assertEqual(result["failed"], "error")

    def test_a_connected_pane_that_is_not_attached_yet_is_still_attached(self):
        """Attaching removes the overlay itself, and that path is untouched: the
        healing branch is the *other* side of the same test."""
        result = self._run_node(
            """
            makePane(0, { attached: false, overlay: 'connecting' });
            SESSIONS = [session(0, 'connected')];
            await refreshStatuses();
            report({
                overlay: overlayOf(0),
                opened: calls.opened,
                attached: terminals[0]._attached
            });
            """
        )
        self.assertEqual(result["overlay"], "none")
        self.assertEqual(result["opened"], [0])
        self.assertTrue(result["attached"])

    def test_an_explorer_pane_is_left_to_its_own_branch(self):
        """Explorer and browser panes have no xterm to attach and no spinner of
        this kind; the terminal branch must not reach them."""
        result = self._run_node(
            """
            makePane(0, { attached: false });
            SESSIONS = [session(0, 'connected', { startup_mode: 'explorer' })];
            await refreshStatuses();
            report({ opened: calls.opened, attached: terminals[0]._attached });
            """
        )
        self.assertEqual(result["opened"], [])
        self.assertFalse(result["attached"])


class FailedRelaunchRepaintTestCase(PaneOverlayTestCase):
    """`syncPanePlaceholder()`: what a pane wears when its relaunch never ran.

    The relaunch raises its spinner before the request, so a request that then
    fails has covered a pane whose shell is still running. The pane is repainted
    from its own session record rather than blanked, because the record is the
    only thing that knows what it was wearing before.
    """

    def test_a_connected_pane_is_uncovered(self):
        result = self._run_node(
            """
            const pane = makePane(0, { attached: true, overlay: 'connecting' });
            pane._session = session(0, 'connected');
            syncPanePlaceholder(0);
            report({ overlay: overlayOf(0) });
            """
        )
        self.assertEqual(result["overlay"], "none")

    def test_a_pane_that_was_already_down_gets_its_overlay_back(self):
        result = self._run_node(
            """
            const dropped = makePane(0, { attached: true, overlay: 'connecting' });
            dropped._session = session(0, 'disconnected');
            syncPanePlaceholder(0);
            const failed = makePane(1, { attached: true, overlay: 'connecting' });
            failed._session = session(1, 'error', { error_message: 'No such host' });
            syncPanePlaceholder(1);
            report({ dropped: overlayOf(0), failed: overlayOf(1) });
            """
        )
        self.assertEqual(result["dropped"], "disconnected")
        self.assertEqual(result["failed"], "error")

    def test_a_pane_with_nothing_to_go_on_is_left_alone(self):
        """No record is not a reason to take an overlay off a pane -- a pane the
        page knows nothing about is the last one to uncover."""
        result = self._run_node(
            """
            makePane(0, { attached: true, overlay: 'connecting' });
            syncPanePlaceholder(0);
            report({ overlay: overlayOf(0) });
            """
        )
        self.assertEqual(result["overlay"], "connecting")


if __name__ == "__main__":
    unittest.main()
