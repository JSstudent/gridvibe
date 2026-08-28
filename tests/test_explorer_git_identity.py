"""What an explorer Git action may still do once its answer comes back.

`explorer-git-sidebar.js` is not DOM-free, so it is evaluated in a Node `vm`
against a stubbed page — the technique `test_explorer_fs_batch.py` uses for
`explorer-fs.js`. That makes the completion path *executed* rather than
pattern-matched: the assertions below are about which pane object was written
to, which panel was painted, and which reload was issued.

The request itself is never in question here. Its scope rode in the URL and the
server acted on that scope, so nothing is cancelled or reissued. What is in
question is the identity the *answer* addresses, and it is three things at once:
the pane object, its session id, and the Git scope the action was presented
from. A group switch moves the first two out from under an integer index; Follow
browsing moves the third out from under the pane itself. The two failures need
different answers — a replaced slot must not be painted at all, while a pane
that only changed scope is still on screen and owes the user its current scope —
so the module answers with a state rather than a boolean.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
SIDEBAR_JS = ROOT / "web" / "static" / "js" / "explorer-git-sidebar.js"
# The scope policy the sidebar reads through `window`: what the pane is
# browsing is the policy module's answer, not the sidebar's own.
PIN_JS = ROOT / "web" / "static" / "js" / "explorer-git-pin.js"

NODE = shutil.which("node")

# One live explorer pane in slot 0, plus a second pane object standing by to
# take that slot. Everything the sidebar reaches for that belongs to another
# module is recorded rather than simulated, so a test can assert on what the
# completion asked the page to do — and on what it declined to ask.
HARNESS_PREAMBLE = """
const fs = require('fs');
const vm = require('vm');

const calls = {
    requests: [],
    renders: [],
    tabSyncs: [],
    directoryLoads: [],
    postActionRefreshes: [],
    repoLoads: []
};

function makePane(name) {
    return {
        name,
        _explorerMode: 'directory',
        _explorerPath: 'inscope',
        _explorerFilePath: '',
        _explorerGitSidebarOpen: true,
        _explorerGitFollowBrowsing: false,
        _explorerGitPinnedPath: 'inscope',
        _explorerGitRepo: { git: { branch: 'main' }, anchor_path: 'inscope' },
        _explorerGitRepoLoaded: true,
        _explorerGitAnchorPath: 'inscope',
        _explorerGitRevision: 'rev-old',
        _explorerGitActionBusy: false,
        _explorerGitRepoError: '',
        _explorerGitWatchSuspended: true,
        _explorerGitCommitMessage: ''
    };
}

const paneA = makePane('a');
const paneB = makePane('b');

// One deferred response per action, resolved by the test after it has moved
// the page underneath the request.
let deferred = null;
function nextResponse() {
    let settle = null;
    const promise = new Promise(resolve => { settle = resolve; });
    deferred = { promise, settle };
    return deferred;
}

const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    Promise,
    Set,
    Map,
    JSON,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Error,
    URLSearchParams,
    encodeURIComponent,
    terminals: [paneA],
    sessionIds: ['session-1'],
    document: { getElementById: () => null },
    fetch: async (url, options) => {
        calls.requests.push({ url, body: JSON.parse(options.body) });
        const canned = await deferred.promise;
        return {
            ok: canned.ok,
            status: canned.status || (canned.ok ? 200 : 500),
            json: async () => canned.data
        };
    }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);  // git pin policy
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);  // git sidebar

/* Overrides go on *after* evaluation: the sidebar's own function declarations
   would otherwise replace same-named stubs put on the sandbox up front. Every
   one of these is a paint or a fetch the completion is only allowed to reach
   for while its captured identity is still current. */
sandbox.renderExplorerGitPanels = index => { calls.renders.push(index); };
sandbox.renderExplorerGitPanel = index => { calls.renders.push(index); };
sandbox.syncExplorerGitActiveRows = () => {};
sandbox.syncExplorerTabGitFromRepo = (index, data) => {
    calls.tabSyncs.push({ index, revision: data && data.revision });
};
sandbox.loadExplorerPane = index => { calls.directoryLoads.push(index); };
sandbox.refreshExplorerAfterGitAction = async (index, path) => {
    calls.postActionRefreshes.push({ index, path });
};
sandbox.loadExplorerGitRepo = index => { calls.repoLoads.push(index); };

const act = (endpoint, body) => sandbox.performExplorerGitAction(0, endpoint, body || {});
const commit = () => sandbox.explorerGitCommit(0);
const switchGroup = () => { sandbox.terminals[0] = paneB; sandbox.sessionIds[0] = 'session-2'; };
const followInto = (path) => {
    paneA._explorerGitFollowBrowsing = true;
    paneA._explorerPath = path;
};
const paneState = (pane) => ({
    busy: pane._explorerGitActionBusy,
    loaded: pane._explorerGitRepoLoaded,
    anchor: pane._explorerGitAnchorPath,
    revision: pane._explorerGitRevision,
    reloadPending: Boolean(pane._explorerGitReloadPending),
    error: pane._explorerGitRepoError,
    watchSuspended: pane._explorerGitWatchSuspended,
    commitDraft: pane._explorerGitCommitMessage
});
const emit = (payload) => process.stdout.write(JSON.stringify(payload));
const fresh = { ok: true, data: { anchor_path: 'inscope', revision: 'rev-new' } };
const failed = { ok: false, data: { error: 'Git action failed for a reason' } };
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer Git identity tests")
class ExplorerGitIdentityHarness(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_PREAMBLE
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(SIDEBAR_JS), str(PIN_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class UnchangedIdentityTestCase(ExplorerGitIdentityHarness):
    """The ordinary action, unchanged. Everything below is a departure from
    this, so it is the control the others are read against."""

    def test_an_action_that_completes_in_place_behaves_exactly_as_before(self):
        result = self._run_node(
            """
            nextResponse();
            const pending = act('stage-all');
            deferred.settle(fresh);
            const succeeded = await pending;
            emit({
                succeeded,
                request: calls.requests[0].url,
                renders: calls.renders,
                tabSyncs: calls.tabSyncs,
                directoryLoads: calls.directoryLoads,
                postActionRefreshes: calls.postActionRefreshes,
                repoLoads: calls.repoLoads,
                pane: paneState(paneA)
            });
            """
        )
        self.assertTrue(result["succeeded"])
        # The scope the sidebar was showing is the scope the request carried.
        self.assertIn("scope=path", result["request"])
        self.assertEqual(result["renders"], [0, 0])
        self.assertEqual(result["tabSyncs"], [{"index": 0, "revision": "rev-new"}])
        self.assertEqual(result["directoryLoads"], [0])
        self.assertEqual(result["postActionRefreshes"], [{"index": 0, "path": ""}])
        self.assertEqual(result["repoLoads"], [])
        self.assertEqual(
            result["pane"],
            {
                "busy": False,
                "loaded": True,
                "anchor": "inscope",
                "revision": "rev-new",
                "reloadPending": False,
                "error": "",
                # A successful action is authoritative: it re-arms the watch.
                "watchSuspended": False,
                "commitDraft": "",
            },
        )


class ReplacedPaneTestCase(ExplorerGitIdentityHarness):
    """Another group took the slot while the action was in flight."""

    def test_a_delayed_success_paints_nothing_and_reloads_nothing(self):
        result = self._run_node(
            """
            nextResponse();
            const pending = act('stage-all');
            switchGroup();
            deferred.settle(fresh);
            const succeeded = await pending;
            emit({
                succeeded,
                renders: calls.renders,
                tabSyncs: calls.tabSyncs,
                directoryLoads: calls.directoryLoads,
                postActionRefreshes: calls.postActionRefreshes,
                repoLoads: calls.repoLoads,
                captured: paneState(paneA),
                incoming: paneState(paneB)
            });
            """
        )
        self.assertFalse(result["succeeded"])
        # The one render is the busy paint that went out *before* the fetch.
        self.assertEqual(result["renders"], [0])
        self.assertEqual(result["tabSyncs"], [])
        self.assertEqual(result["directoryLoads"], [])
        self.assertEqual(result["postActionRefreshes"], [])
        self.assertEqual(result["repoLoads"], [])
        # The captured pane keeps its last good model on screen and stops
        # counting as loaded, so the next load refetches instead of
        # short-circuiting on the anchor path it already holds.
        self.assertEqual(result["captured"]["loaded"], False)
        self.assertEqual(result["captured"]["anchor"], "")
        self.assertTrue(result["captured"]["reloadPending"])
        self.assertEqual(result["captured"]["revision"], "rev-old")
        # Neither pane is left permanently busy.
        self.assertFalse(result["captured"]["busy"])
        self.assertFalse(result["incoming"]["busy"])
        # The pane that merely inherited the slot is untouched in every field.
        self.assertEqual(
            result["incoming"],
            {
                "busy": False,
                "loaded": True,
                "anchor": "inscope",
                "revision": "rev-old",
                "reloadPending": False,
                "error": "",
                "watchSuspended": True,
                "commitDraft": "",
            },
        )

    def test_a_delayed_failure_reports_on_neither_pane(self):
        """An error belongs to the scope it was raised for. Painting it onto a
        replacement pane attaches it to a repository the user never asked
        this of."""
        result = self._run_node(
            """
            nextResponse();
            const pending = act('discard-all');
            switchGroup();
            deferred.settle(failed);
            const succeeded = await pending;
            emit({
                succeeded,
                renders: calls.renders,
                captured: paneState(paneA),
                incoming: paneState(paneB)
            });
            """
        )
        self.assertFalse(result["succeeded"])
        self.assertEqual(result["renders"], [0])
        self.assertEqual(result["captured"]["error"], "")
        self.assertEqual(result["incoming"]["error"], "")
        self.assertTrue(result["captured"]["reloadPending"])
        self.assertFalse(result["captured"]["busy"])
        self.assertFalse(result["incoming"]["busy"])

    def test_the_commit_draft_is_cleared_on_the_pane_that_typed_it(self):
        """The draft is pane state and follows the pane; the render is slot
        work and does not."""
        result = self._run_node(
            """
            paneA._explorerGitCommitMessage = 'a real commit message';
            nextResponse();
            const pending = commit();
            switchGroup();
            deferred.settle(fresh);
            await pending;
            emit({
                renders: calls.renders,
                captured: paneState(paneA),
                incoming: paneState(paneB)
            });
            """
        )
        # A stale commit answer is not a success, so the draft survives for the
        # fresh load rather than being thrown away on an unconfirmed result.
        self.assertEqual(result["captured"]["commitDraft"], "a real commit message")
        self.assertEqual(result["incoming"]["commitDraft"], "")
        self.assertEqual(result["renders"], [0])


class ChangedScopeTestCase(ExplorerGitIdentityHarness):
    """Same pane, still on screen, but Follow browsing moved its Git scope
    while the request was out."""

    def test_the_old_scope_payload_never_overwrites_the_current_model(self):
        result = self._run_node(
            """
            nextResponse();
            const pending = act('stage-all');
            followInto('outscope');
            deferred.settle(fresh);
            const succeeded = await pending;
            emit({
                succeeded,
                renders: calls.renders,
                tabSyncs: calls.tabSyncs,
                directoryLoads: calls.directoryLoads,
                postActionRefreshes: calls.postActionRefreshes,
                repoLoads: calls.repoLoads,
                captured: paneState(paneA)
            });
            """
        )
        self.assertFalse(result["succeeded"])
        self.assertEqual(result["tabSyncs"], [])
        self.assertEqual(result["directoryLoads"], [])
        self.assertEqual(result["postActionRefreshes"], [])
        # The pane is the one on screen, so it is painted — and the scope it is
        # now showing is loaded for real rather than left to a later gesture.
        self.assertEqual(result["renders"], [0, 0])
        self.assertEqual(result["repoLoads"], [0])
        self.assertEqual(result["captured"]["revision"], "rev-old")
        self.assertEqual(result["captured"]["loaded"], False)
        self.assertEqual(result["captured"]["anchor"], "")
        # The load was issued here, so nothing is left owed to a later return.
        self.assertFalse(result["captured"]["reloadPending"])
        self.assertFalse(result["captured"]["busy"])

    def test_a_delayed_failure_is_not_attached_to_the_new_scope(self):
        result = self._run_node(
            """
            nextResponse();
            const pending = act('discard-all');
            followInto('outscope');
            deferred.settle(failed);
            await pending;
            emit({ repoLoads: calls.repoLoads, captured: paneState(paneA) });
            """
        )
        self.assertEqual(result["captured"]["error"], "")
        self.assertEqual(result["repoLoads"], [0])
        self.assertFalse(result["captured"]["busy"])

    def test_a_closed_sidebar_is_marked_rather_than_loaded(self):
        """Nothing is on screen to load into, so the fresh load waits for the
        gesture that opens the panel."""
        result = self._run_node(
            """
            paneA._explorerGitSidebarOpen = false;
            nextResponse();
            const pending = act('stage-all');
            followInto('outscope');
            deferred.settle(fresh);
            await pending;
            emit({ repoLoads: calls.repoLoads, captured: paneState(paneA) });
            """
        )
        self.assertEqual(result["repoLoads"], [])
        self.assertFalse(result["captured"]["loaded"])
        self.assertTrue(result["captured"]["reloadPending"])


class ScopeIdentityTestCase(ExplorerGitIdentityHarness):
    """The three parts of the identity, each moved on its own."""

    def test_each_part_of_the_identity_is_load_bearing(self):
        result = self._run_node(
            """
            const identity = sandbox.explorerGitCaptureIdentity(0);
            const before = sandbox.explorerGitIdentityState(0, identity);

            sandbox.sessionIds[0] = 'session-reconnected';
            const afterSession = sandbox.explorerGitIdentityState(0, identity);
            sandbox.sessionIds[0] = 'session-1';

            sandbox.terminals[0] = paneB;
            const afterPane = sandbox.explorerGitIdentityState(0, identity);
            sandbox.terminals[0] = paneA;

            followInto('outscope');
            const afterScope = sandbox.explorerGitIdentityState(0, identity);

            emit({ before, afterSession, afterPane, afterScope });
            """
        )
        self.assertEqual(result["before"], "current")
        # A reconnect under the same pane object is still a different session.
        self.assertEqual(result["afterSession"], "pane-replaced")
        self.assertEqual(result["afterPane"], "pane-replaced")
        self.assertEqual(result["afterScope"], "scope-changed")

    def test_an_empty_slot_is_a_replaced_pane_rather_than_a_throw(self):
        result = self._run_node(
            """
            sandbox.terminals[0] = null;
            const identity = sandbox.explorerGitCaptureIdentity(0);
            emit({
                state: sandbox.explorerGitIdentityState(0, identity),
                missing: sandbox.explorerGitIdentityState(0, null)
            });
            """
        )
        self.assertEqual(result["state"], "pane-replaced")
        self.assertEqual(result["missing"], "pane-replaced")


LOAD_HARNESS_PREAMBLE = """
const fs = require('fs');
const vm = require('vm');

const calls = { requests: [], renders: [] };

/* A freshly rebuilt pane: nothing loaded, a pin already re-applied from the
   restored session record. This is the state a restored workspace starts in. */
function makePane(pinnedPath) {
    const pane = {
        _explorerMode: 'directory',
        _explorerPath: 'browsed',
        _explorerGitSidebarOpen: true,
        _explorerGitFollowBrowsing: false,
        _explorerGitRepoLoaded: false,
        _explorerGitRepoLoading: false,
        _explorerGitRepo: null,
        _explorerGitAnchorPath: '',
        _explorerGitRepoError: ''
    };
    if (pinnedPath !== null) {
        pane._explorerGitPinnedPath = pinnedPath;
    }
    return pane;
}

const pane = makePane(PINNED_PATH);

// What the server answers with. `anchor_path` is the *resolved* spelling and
// is deliberately allowed to differ from the requested one.
let answer = { anchor_path: RESOLVED_ANCHOR, revision: 'rev-1' };

const sandbox = {
    console: { error() {}, warn() {}, log() {} },
    Promise, Set, Map, JSON, Number, String, Boolean, Array, Object, Error,
    URLSearchParams, encodeURIComponent,
    terminals: [pane],
    sessionIds: ['session-1'],
    document: { getElementById: () => null },
    fetch: async (url) => {
        calls.requests.push(url);
        return { ok: true, status: 200, json: async () => answer };
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

sandbox.renderExplorerGitPanels = index => { calls.renders.push(index); };
sandbox.renderExplorerGitPanel = index => { calls.renders.push(index); };
sandbox.syncExplorerGitActiveRows = () => {};
sandbox.syncExplorerTabGitFromRepo = () => {};
sandbox.notePanePresentationChanged = () => {};

const emit = (payload) => process.stdout.write(JSON.stringify(payload));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer Git identity tests")
class LoadIdentityTestCase(unittest.TestCase):
    """Which scope the pane's loaded model is the model *for*.

    The load's early return compares "the scope the next request would carry"
    against a field the pane holds. Those have to be the same kind of thing.
    The server's ``anchor_path`` is the *resolved* spelling of the scope, and
    it is a different string whenever the requested spelling was not already
    canonical -- so storing the answer and comparing it to the question left
    such a pane permanently un-loaded: the early return never fired, and every
    render refetched the whole repository behind a pin that looked, from the
    outside, like it had simply not been restored.
    """

    def _run_node(self, body, *, pinned_path="docs", resolved_anchor="docs"):
        preamble = (
            LOAD_HARNESS_PREAMBLE.replace("PINNED_PATH", json.dumps(pinned_path))
            .replace("RESOLVED_ANCHOR", json.dumps(resolved_anchor))
        )
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
                [NODE, str(script_path), str(SIDEBAR_JS), str(PIN_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_second_load_of_an_unchanged_scope_issues_no_request(self):
        result = self._run_node(
            """
            await sandbox.loadExplorerGitRepo(0);
            const afterFirst = calls.requests.length;
            await sandbox.loadExplorerGitRepo(0);
            emit({
                afterFirst,
                afterSecond: calls.requests.length,
                anchor: pane._explorerGitAnchorPath
            });
            """
        )
        self.assertEqual(result["afterFirst"], 1)
        self.assertEqual(result["afterSecond"], 1)
        self.assertEqual(result["anchor"], "docs")

    def test_a_pin_the_server_spells_differently_still_counts_as_loaded(self):
        """The load identity is the scope that was *requested*.

        The server answers with its own resolved spelling of that scope, and
        storing the answer as the identity meant every later comparison found a
        difference: such a pane never counted as loaded, so every render
        refetched the whole repository.
        """
        result = self._run_node(
            """
            await sandbox.loadExplorerGitRepo(0);
            await sandbox.loadExplorerGitRepo(0);
            await sandbox.loadExplorerGitRepo(0);
            emit({
                requests: calls.requests.length,
                anchor: pane._explorerGitAnchorPath
            });
            """,
            pinned_path="docs/",
            resolved_anchor="docs",
        )
        self.assertEqual(result["requests"], 1)
        self.assertEqual(result["anchor"], "docs/")

    def test_a_root_pin_is_loaded_once_and_not_confused_with_no_pin(self):
        result = self._run_node(
            """
            await sandbox.loadExplorerGitRepo(0);
            const pinnedRequest = calls.requests[0];
            await sandbox.loadExplorerGitRepo(0);
            emit({ requests: calls.requests.length, pinnedRequest });
            """,
            pinned_path="",
            resolved_anchor="",
        )
        self.assertEqual(result["requests"], 1)
        # A root pin still travels as an explicit scope, not as an omitted one.
        self.assertIn("scope=path", result["pinnedRequest"])

    def test_a_changed_scope_is_reloaded_rather_than_served_from_the_old_model(self):
        result = self._run_node(
            """
            await sandbox.loadExplorerGitRepo(0);
            pane._explorerGitPinnedPath = 'web';
            answer = { anchor_path: 'web', revision: 'rev-2' };
            await sandbox.loadExplorerGitRepo(0);
            emit({ requests: calls.requests, anchor: pane._explorerGitAnchorPath });
            """
        )
        self.assertEqual(len(result["requests"]), 2)
        self.assertIn("path=web", result["requests"][1])
        self.assertEqual(result["anchor"], "web")


if __name__ == "__main__":
    unittest.main()
