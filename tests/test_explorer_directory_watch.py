"""Independent directory state watch (ISSUE-2026-061) frontend behavior.

``explorer-git-watch.js``'s new Git-free directory check is not DOM-free, so it
is evaluated in a Node ``vm`` against a stubbed page, the same technique the
stage-062 Git watch suite uses. The harness loads the *shipped* scheduler
(``explorerGitWatchTick`` / ``explorerDirectoryWatchCheckOne``) plus the shipped
directory baselines (``explorer-directory.js``) and the shipped tree
(``explorer-tree.js``, which owns the bounded-node constant). Tests execute the
real quiet listing, tree and coordinator helpers. Only DOM rendering/scroll
adapters and the transport are stubbed.

Assertions cover the issue's independence contract:

- a directory change is picked up with Git unavailable (``_explorerGitContext``
  absent) and with an unrelated pin, so neither gates the check;
- a change landing between a surface load and the first poll is detected from
  the load-recorded baseline, not silently bootstrapped;
- an unchanged directory costs no re-list;
- the browsed directory and every loaded expanded tree directory are targets;
- a hidden page neither polls nor applies;
- a deferred apply holds the newest plan and releases it;
- a stale pane/session/root response is dropped; navigation during an apply is
  rechecked;
- a large expanded tree is polled in a bounded rotating window;
- an open editor still leaves the tree refreshes eligible.
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
DIRECTORY_JS = STATIC_JS / "explorer-directory.js"
TREE_JS = STATIC_JS / "explorer-tree.js"
SIDEBAR_JS = STATIC_JS / "explorer-git-sidebar.js"
PIN_JS = STATIC_JS / "explorer-git-pin.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"

NODE = shutil.which("node")


HARNESS_PREAMBLE = r"""
const fs = require('fs');
const vm = require('vm');

let clock = 0;
function advanceClock(ms) { clock += ms; }

function makeElement(kind) {
    return {
        kind, id: '', _hover: false, scrollTop: 0, scrollHeight: 1000,
        selectionStart: 0, selectionEnd: 0,
        matches(selector) { return selector === ':hover' ? this._hover === true : false; },
        contains(el) { return el === this; },
        addEventListener() {}, focus() {},
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
        _explorerPath: '',
        _explorerEntries: [],
        _explorerTreeSidebarOpen: true,
        _explorerTreeExpanded: new Set(),
        _explorerTreeChildren: new Map([['', []]]),
        _explorerDirRevisions: new Map(),
        _explorerRootRevision: 'root-rev',
        _explorerGitContext: null,
        _explorerGitActionBusy: false,
        _explorerFsBusy: false,
        _explorerFsWatchRefreshing: false,
        _explorerDirWatchInFlight: false,
        _explorerDirWatchSuspended: false,
        _explorerDirWatchFailures: 0,
        _explorerDirWatchPending: null,
        _explorerDirWatchCursor: 0,
        _explorerDirWatchNextAt: 0,
        _explorerGitWatchNextAt: 0,
        _explorerFileWatchNextAt: 0,
        _explorerGitWatchInFlight: false,
        _explorerGitWatchSuspended: false,
        _explorerGitActionBusy: false,
        _explorerFsWatchRevision: '',
        _session: { mode: 'local' }
    };
}

const pane = makePane();
let dirAnswer = { revision: 'baseline', complete: true, path: '', root_revision: 'root-rev' };
let directoryStateCalls = 0;
const applyCalls = [];
let stateGate = null;

function holdNextDirectoryState() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    stateGate = { promise, resolve };
    return stateGate;
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
        if (String(url).includes('/directory/state')) {
            directoryStateCalls += 1;
            if (stateGate) { await stateGate.promise; }
            return { ok: true, status: 200, json: async () => dirAnswer };
        }
        if (String(url).includes('/entries')) {
            const path = new URL('http://localhost' + url).searchParams.get('path') || '';
            return { ok: true, status: 200, json: async () => ({
                path, root_revision: 'root-rev', directory_revision: dirAnswer.revision,
                entries: [{name: 'new.txt', path: path ? path + '/new.txt' : 'new.txt', type: 'file'}]
            }) };
        }
        return { ok: false, status: 404, json: async () => ({}) };
    }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(process.argv[5], 'utf8'), sandbox); // pin policy
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox); // git sidebar
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox); // tree
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox); // directory

// Run the shipped coordinator and both quiet consumers. Only DOM adapters are
// replaced: rendering simulates the browser clamping scroll after a rebuild.
const realQuietRefresh = sandbox.refreshExplorerFilesystemSurfacesQuiet;
const realQuietTree = sandbox.refreshExplorerTreeQuiet;
const realQuietListing = sandbox.refreshExplorerDirectoryQuiet;
sandbox.refreshExplorerFilesystemSurfacesQuiet = async (index, options) => {
    applyCalls.push({ index, dirs: options && options.dirs ? [...options.dirs] : null });
    return realQuietRefresh(index, options);
};
let listingRenders = 0, treeRenders = 0;
sandbox.explorerHashText = text => text;
sandbox.updateExplorerGitSummary = () => {};
sandbox.renderExplorerDirectoryRows = () => {
    listingRenders += 1;
    const list = elements['explorer-list-0'];
    if (list) { list.scrollTop = 0; list.scrollLeft = 0; }
};
sandbox.renderExplorerTreePanel = () => {
    treeRenders += 1;
    const tree = elements['explorer-tree-panel-0'];
    if (tree) { tree.scrollTop = 0; tree.scrollLeft = 0; }
};
sandbox.captureScrollMetrics = el => el ? {top: el.scrollTop, left: el.scrollLeft} : null;
sandbox.applyScrollMetrics = (el, metrics) => {
    if (el && metrics) { el.scrollTop = metrics.top; el.scrollLeft = metrics.left; }
};
function recordBoth(path, revision) {
    sandbox.recordExplorerDirectoryRevision(pane, path, revision);
    sandbox.recordExplorerDirectoryRevision(pane, path, revision, 'tree');
}
function installEntries(reader) {
    const previous = sandbox.fetch;
    sandbox.fetch = async url => {
        if (!String(url).includes('/entries')) return previous(url);
        const path = new URL('http://localhost' + url).searchParams.get('path') || '';
        const answer = await reader(path);
        return {ok: answer.ok !== false, status: answer.status || 200,
            json: async () => ({root_revision: 'root-rev', path, ...answer})};
    };
}
function gate() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return {promise, resolve};
}
async function nextTurn() { await new Promise(r => setTimeout(r, 0)); }
sandbox.refreshExplorerGitRepoQuiet = async () => ({ revision: 'rev' });
sandbox.applyExplorerGitRepoQuiet = () => true;
sandbox.refreshExplorerOpenFileQuiet = async () => true;
sandbox.refreshExplorerOverview = async () => {};
sandbox.renderExplorerGitPanel = () => {};
sandbox.renderExplorerGitPanels = () => {};

vm.runInContext(fs.readFileSync(process.argv[6], 'utf8'), sandbox); // watch

const emit = (payload) => process.stdout.write(JSON.stringify(payload));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer directory watch tests")
class ExplorerDirectoryWatchHarness(unittest.TestCase):
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
                [
                    NODE, str(script_path),
                    str(WATCH_JS), str(DIRECTORY_JS), str(TREE_JS),
                    str(SIDEBAR_JS), str(PIN_JS),
                    str(VIEWER_JS),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class IndependentSignalTestCase(ExplorerDirectoryWatchHarness):
    def test_change_is_detected_with_git_unavailable_and_an_unrelated_pin(self):
        result = self._run_node("""
            pane._explorerGitContext = null;          // no Git at all
            pane._explorerGitPinnedPath = 'elsewhere'; // an unrelated pin
            recordBoth('', 'baseline');
            dirAnswer = { revision: 'changed', complete: true, path: '', root_revision: 'root-rev' };
            await sandbox.explorerGitWatchTick();
            emit({
                applied: applyCalls.length,
                dirs: applyCalls[0] && applyCalls[0].dirs,
                polled: directoryStateCalls,
                baseline: sandbox.explorerDirectoryBaselineRevision(pane, '')
            });
        """)
        self.assertEqual(result["applied"], 1)
        self.assertIn("", result["dirs"])
        self.assertEqual(result["polled"], 1)
        self.assertEqual(result["baseline"], "changed")

    def test_change_before_first_poll_is_detected_not_bootstrapped(self):
        result = self._run_node("""
            // The load already recorded a baseline; the poll now reads a newer
            // revision, exactly the file whose creation landed in between.
            recordBoth('', 'at-load');
            dirAnswer = { revision: 'newer', complete: true, path: '', root_revision: 'root-rev' };
            await sandbox.explorerGitWatchTick();
            emit({ applied: applyCalls.length, baseline: sandbox.explorerDirectoryBaselineRevision(pane, '') });
        """)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["baseline"], "newer")

    def test_unchanged_directory_costs_no_relist(self):
        result = self._run_node("""
            recordBoth('', 'same');
            dirAnswer = { revision: 'same', complete: true, path: '', root_revision: 'root-rev' };
            await sandbox.explorerGitWatchTick();
            emit({ applied: applyCalls.length, polled: directoryStateCalls, pending: Boolean(pane._explorerDirWatchPending) });
        """)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["polled"], 1)
        self.assertFalse(result["pending"])

    def test_missing_baseline_conservatively_relists_once(self):
        result = self._run_node("""
            // No baseline recorded (the directory exceeded the state ceiling at
            // load): the poll must not assume the surface already matches.
            dirAnswer = { revision: 'now', complete: true, path: '', root_revision: 'root-rev' };
            await sandbox.explorerGitWatchTick();
            emit({ applied: applyCalls.length, baseline: sandbox.explorerDirectoryBaselineRevision(pane, '') });
        """)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["baseline"], "now")


class TargetsTestCase(ExplorerDirectoryWatchHarness):
    def test_browsed_directory_and_loaded_expanded_tree_are_targets(self):
        result = self._run_node("""
            pane._explorerTreeExpanded = new Set(['docs', 'docs/r&d/MCP/flows']);
            pane._explorerTreeChildren = new Map([
                ['', []], ['docs', []], ['docs/r&d/MCP/flows', []]
            ]);
            emit({ targets: sandbox.explorerDirectoryWatchTargets(pane) });
        """)
        self.assertEqual(
            result["targets"], ["", "docs", "docs/r&d/MCP/flows"]
        )

    def test_unloaded_tree_directories_are_not_polled(self):
        result = self._run_node("""
            pane._explorerTreeExpanded = new Set(['docs', 'docs/deeper']);
            pane._explorerTreeChildren = new Map([['', []], ['docs', []]]); // deeper never loaded
            emit({ targets: sandbox.explorerDirectoryWatchTargets(pane) });
        """)
        self.assertEqual(result["targets"], ["", "docs"])

    def test_editor_open_still_watches_the_tree(self):
        result = self._run_node("""
            pane._explorerMode = 'file';
            pane._explorerEdit = {};   // an open editor buffer
            pane._explorerTreeExpanded = new Set(['docs']);
            pane._explorerTreeChildren = new Map([['', []], ['docs', []]]);
            emit({
                targets: sandbox.explorerDirectoryWatchTargets(pane),
                eligible: sandbox.explorerDirectoryWatchEligible(0)
            });
        """)
        self.assertEqual(result["targets"], ["", "docs"])
        self.assertTrue(result["eligible"])


class DeferralAndStaleTestCase(ExplorerDirectoryWatchHarness):
    def test_change_is_deferred_while_interacting_then_applied(self):
        result = self._run_node("""
            elements['explorer-tree-panel-0'] = makeElement('panel');
            elements['explorer-tree-panel-0']._hover = true;
            elements['explorer-tree-panel-0'].scrollTop = 42;
            recordBoth('', 'baseline');
            dirAnswer = { revision: 'changed', complete: true, path: '', root_revision: 'root-rev' };
            await sandbox.explorerGitWatchTick();
            const deferred = { applied: applyCalls.length, pending: Boolean(pane._explorerDirWatchPending) };
            elements['explorer-tree-panel-0']._hover = false;
            await sandbox.explorerGitWatchTick();
            emit({ deferred, applied: applyCalls.length });
        """)
        self.assertEqual(result["deferred"], {"applied": 0, "pending": True})
        self.assertEqual(result["applied"], 1)

    def test_a_response_to_a_replaced_pane_is_dropped(self):
        result = self._run_node("""
            recordBoth('', 'baseline');
            dirAnswer = { revision: 'changed', complete: true, path: '', root_revision: 'root-rev' };
            holdNextDirectoryState();
            const pending = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            const replacement = makePane();
            sandbox.terminals[0] = replacement;
            stateGate.resolve();
            await pending;
            emit({ applied: applyCalls.length, replacementBaseline: sandbox.explorerDirectoryBaselineRevision(replacement, '') });
        """)
        self.assertEqual(result["applied"], 0)
        self.assertIsNone(result.get("replacementBaseline"))

    def test_a_response_to_a_reset_root_is_dropped(self):
        result = self._run_node("""
            recordBoth('', 'baseline');
            dirAnswer = { revision: 'changed', complete: true, path: '', root_revision: 'root-rev' };
            holdNextDirectoryState();
            const pending = sandbox.explorerGitWatchTick();
            await new Promise(r => setTimeout(r, 0));
            pane._explorerRootRevision = 'root-rev-2';
            stateGate.resolve();
            await pending;
            emit({ applied: applyCalls.length });
        """)
        self.assertEqual(result["applied"], 0)


class HiddenAndBoundedTestCase(ExplorerDirectoryWatchHarness):
    def test_hidden_page_neither_polls_nor_applies(self):
        result = self._run_node("""
            document.visibilityState = 'hidden';
            recordBoth('', 'baseline');
            dirAnswer = { revision: 'changed', complete: true, path: '', root_revision: 'root-rev' };
            await sandbox.explorerGitWatchTick();
            emit({ polled: directoryStateCalls, applied: applyCalls.length });
        """)
        self.assertEqual(result["polled"], 0)
        self.assertEqual(result["applied"], 0)

    def test_large_expanded_tree_rotates_the_poll_window(self):
        result = self._run_node("""
            const treeChildren = new Map([['', []]]);
            const expanded = new Set();
            const names = [];
            for (let i = 0; i < 20; i += 1) {
                names.push('d' + i);
                expanded.add('d' + i);
                treeChildren.set('d' + i, []);
            }
            pane._explorerTreeExpanded = expanded;
            pane._explorerTreeChildren = treeChildren;
            const first = sandbox.explorerDirectoryWatchWindow(pane);
            pane._explorerDirWatchCursor = 16;
            const second = sandbox.explorerDirectoryWatchWindow(pane);
            emit({
                firstName: first.paths[0],
                lastName: first.paths[first.paths.length - 1],
                secondName: second.paths[0],
                firstLen: first.paths.length,
                secondLen: second.paths.length
            });
        """)
        self.assertEqual(result["firstLen"], 16)
        self.assertEqual(result["secondLen"], 16)
        self.assertNotEqual(result["firstName"], result["secondName"])


class PendingWorkTestCase(ExplorerDirectoryWatchHarness):
    def test_user_baseline_reset_preserves_other_surface_pending_changes(self):
        result = self._run_node("""
            recordBoth('', 'old'); sandbox.queueExplorerDirectoryChange(pane,'');
            pane._explorerDirWatchSuspended=true;
            pane._explorerDirWatchFailures=5;
            sandbox.resetExplorerFsWatchBaseline(pane);
            emit({pending:[...pane._explorerDirWatchPending.paths],
                listing:sandbox.explorerDirectoryBaselineRevision(pane,''),
                tree:sandbox.explorerDirectoryBaselineRevision(pane,'','tree'),
                failures:pane._explorerDirWatchFailures,suspended:pane._explorerDirWatchSuspended});
        """)
        self.assertEqual(result, {'pending': [''], 'listing': 'old', 'tree': 'old',
                                 'failures': 0, 'suspended': False})

    def test_renderer_failures_remain_retryable_on_both_surfaces(self):
        for surface in ('listing', 'tree'):
            with self.subTest(surface=surface):
                result = self._run_node("""
                    recordBoth('', 'old');
                    pane._explorerMode=SURFACE==='tree'?'file':'directory';
                    const field=SURFACE==='tree'?'renderExplorerTreePanel':'renderExplorerDirectoryRows';
                    const render=sandbox[field];
                    sandbox[field]=()=>{throw new Error('render failed');};
                    sandbox.queueExplorerDirectoryChange(pane,'');
                    await sandbox.explorerDirectoryWatchFlushPending(0);
                    const failed={pending:Boolean(pane._explorerDirWatchPending),
                        baseline:sandbox.explorerDirectoryBaselineRevision(pane,'',SURFACE)};
                    sandbox[field]=render;
                    advanceClock(60000);
                    await sandbox.explorerDirectoryWatchFlushPending(0);
                    emit({failed,pending:Boolean(pane._explorerDirWatchPending),listingRenders,treeRenders});
                """.replace('SURFACE', json.dumps(surface)))
                self.assertEqual(result['failed'], {'pending': True, 'baseline': 'old'})
                self.assertFalse(result['pending'])
                self.assertEqual(result[surface + 'Renders'], 1)

    def test_repeated_held_polls_keep_baselines_and_newest_changes(self):
        result = self._run_node("""
            elements['explorer-tree-panel-0'] = makeElement('tree');
            elements['explorer-tree-panel-0']._hover = true;
            elements['explorer-tree-panel-0'].scrollTop = 30;
            recordBoth('', 'old');
            dirAnswer.revision = 'new';
            await sandbox.explorerDirectoryWatchCheckOne(0);
            await sandbox.explorerDirectoryWatchCheckOne(0);
            const held = {baseline: sandbox.explorerDirectoryBaselineRevision(pane, ''),
                pending: [...pane._explorerDirWatchPending.paths], applies: applyCalls.length};
            dirAnswer.revision = 'newest';
            await sandbox.explorerDirectoryWatchCheckOne(0);
            elements['explorer-tree-panel-0']._hover = false;
            await sandbox.explorerDirectoryWatchFlushPending(0);
            emit({held, baseline: sandbox.explorerDirectoryBaselineRevision(pane, ''),
                tree: sandbox.explorerDirectoryBaselineRevision(pane, '', 'tree'),
                pending: Boolean(pane._explorerDirWatchPending)});
        """)
        self.assertEqual(result['held'], {'baseline': 'old', 'pending': [''], 'applies': 0})
        self.assertEqual(result['baseline'], 'newest')
        self.assertEqual(result['tree'], 'newest')
        self.assertFalse(result['pending'])

    def test_apply_failure_backs_off_and_recovers_without_losing_work(self):
        result = self._run_node("""
            recordBoth('', 'old');
            dirAnswer.revision = 'new';
            let failed = true;
            installEntries(() => failed ? {ok:false} : {directory_revision:'new', entries:[]});
            await sandbox.explorerDirectoryWatchCheckOne(0);
            const first = {pending:Boolean(pane._explorerDirWatchPending),
                baseline:sandbox.explorerDirectoryBaselineRevision(pane, ''), failures:pane._explorerDirWatchFailures};
            await sandbox.explorerDirectoryWatchFlushPending(0);
            const heldCalls = applyCalls.length;
            failed = false;
            advanceClock(60000);
            await sandbox.explorerDirectoryWatchFlushPending(0);
            emit({first, heldCalls, calls:applyCalls.length,
                baseline:sandbox.explorerDirectoryBaselineRevision(pane, ''),
                pending:Boolean(pane._explorerDirWatchPending)});
        """)
        self.assertEqual(result['first'], {'pending': True, 'baseline': 'old', 'failures': 1})
        self.assertEqual(result['heldCalls'], 1)
        self.assertEqual(result['calls'], 2)
        self.assertEqual(result['baseline'], 'new')
        self.assertFalse(result['pending'])

    def test_pending_paths_survive_rotating_windows_and_each_apply_is_bounded(self):
        result = self._run_node("""
            pane._explorerFsBusy = true;
            pane._explorerMode = 'file';
            for (let i=0; i<24; i++) {
                pane._explorerTreeExpanded.add('d'+i);
                pane._explorerTreeChildren.set('d'+i, []);
                recordBoth('d'+i, 'old');
            }
            recordBoth('', 'old');
            dirAnswer.revision = 'new';
            await sandbox.explorerDirectoryWatchCheckOne(0);
            await sandbox.explorerDirectoryWatchCheckOne(0);
            const held = pane._explorerDirWatchPending.paths.size;
            pane._explorerFsBusy = false;
            await sandbox.explorerDirectoryWatchFlushPending(0);
            const remainder = pane._explorerDirWatchPending.paths.size;
            await sandbox.explorerDirectoryWatchFlushPending(0);
            emit({held,remainder,batches:applyCalls.map(call=>call.dirs.length),
                pending:Boolean(pane._explorerDirWatchPending),
                loaded:sandbox.explorerDirectoryBaselineRevision(pane,'d23','tree')});
        """)
        self.assertEqual(result['held'], 25)
        self.assertEqual(result['remainder'], 9)
        self.assertEqual(result['batches'], [16, 9])
        self.assertFalse(result['pending'])
        self.assertEqual(result['loaded'], 'new')

    def test_new_work_queued_during_apply_survives_success(self):
        result = self._run_node("""
            const held = gate();
            recordBoth('', 'old');
            sandbox.queueExplorerDirectoryChange(pane, '');
            installEntries(async () => {await held.promise; return {directory_revision:'new', entries:[]};});
            const applying = sandbox.explorerDirectoryWatchFlushPending(0);
            await nextTurn();
            sandbox.queueExplorerDirectoryChange(pane, '');
            held.resolve();
            await applying;
            const retained = [...pane._explorerDirWatchPending.paths];
            await sandbox.explorerDirectoryWatchFlushPending(0);
            emit({retained,pending:Boolean(pane._explorerDirWatchPending)});
        """)
        self.assertEqual(result['retained'], [''])
        self.assertFalse(result['pending'])


class RealSurfaceTestCase(ExplorerDirectoryWatchHarness):
    def test_newer_listing_does_not_mask_old_tree_or_inverse(self):
        result = self._run_node("""
            recordBoth('', 'old');
            dirAnswer.revision = 'new';
            await realQuietListing(0);
            const treeBefore = sandbox.explorerDirectoryBaselineRevision(pane, '', 'tree');
            await sandbox.explorerDirectoryWatchCheckOne(0);
            const first = {treeBefore,listingRenders,treeRenders,applies:applyCalls.length};
            sandbox.recordExplorerDirectoryRevision(pane, '', 'older-listing');
            sandbox.recordExplorerDirectoryRevision(pane, '', 'new', 'tree');
            await sandbox.explorerDirectoryWatchCheckOne(0);
            emit({first,applies:applyCalls.length,
                listing:sandbox.explorerDirectoryBaselineRevision(pane, ''),
                tree:sandbox.explorerDirectoryBaselineRevision(pane, '', 'tree')});
        """)
        self.assertEqual(result['first'], {'treeBefore': 'old', 'listingRenders': 1, 'treeRenders': 1, 'applies': 1})
        self.assertEqual(result['applies'], 2)
        self.assertEqual(result['listing'], 'new')
        self.assertEqual(result['tree'], 'new')

    def test_real_helpers_preserve_scroll_filter_tabs_expansion_and_draft(self):
        result = self._run_node("""
            const list=elements['explorer-list-0']=makeElement('list');
            list.scrollTop=77; list.scrollLeft=12; list.clientHeight=100;
            const tree=elements['explorer-tree-panel-0']=makeElement('tree');
            tree.scrollTop=88; tree.scrollLeft=13;
            pane._explorerDirectorySearch={query:'needle', caseSensitive:true};
            pane._explorerTabs=[{id:'preview'}, {id:'pinned',path:'draft.txt'}];
            pane._explorerActiveTabId='preview';
            pane._explorerGitCommitMessage='unsent';
            pane._explorerTreeExpanded.add('docs');
            pane._explorerTreeChildren.set('',[{path:'docs',name:'docs',type:'directory'}]);
            pane._explorerTreeChildren.set('docs',[]);
            installEntries(path => ({directory_revision:'new',entries:path==='' ?
                [{path:'docs',name:'docs',type:'directory'}, {path:'new',name:'new',type:'file'}] :
                [{path:'docs/child',name:'child',type:'file'}]}));
            const before={filter:pane._explorerDirectorySearch,tabs:pane._explorerTabs};
            const ok=await realQuietRefresh(0,{dirs:['','docs']});
            emit({ok,list:[list.scrollTop,list.scrollLeft],tree:[tree.scrollTop,tree.scrollLeft],
                filterSame:before.filter===pane._explorerDirectorySearch,
                tabsSame:before.tabs===pane._explorerTabs,active:pane._explorerActiveTabId,
                draft:pane._explorerGitCommitMessage,expansion:[...pane._explorerTreeExpanded],
                listing:pane._explorerEntries.map(e=>e.path),child:pane._explorerTreeChildren.get('docs')[0].path});
        """)
        self.assertTrue(result['ok'])
        self.assertEqual(result['list'], [77, 12])
        self.assertEqual(result['tree'], [88, 13])
        self.assertTrue(result['filterSame'])
        self.assertTrue(result['tabsSame'])
        self.assertEqual(result['active'], 'preview')
        self.assertEqual(result['draft'], 'unsent')
        self.assertEqual(result['expansion'], ['docs'])
        self.assertEqual(result['listing'], ['docs', 'new'])
        self.assertEqual(result['child'], 'docs/child')

    def test_editor_refreshes_tree_without_touching_its_buffer(self):
        result = self._run_node("""
            pane._explorerMode='file';
            const edit=pane._explorerEdit={buffer:'unsaved',dirty:true};
            const entries=pane._explorerEntries;
            const ok=await realQuietRefresh(0,{dirs:['']});
            emit({ok,listingRenders,treeRenders,buffer:pane._explorerEdit.buffer,
                sameEdit:pane._explorerEdit===edit,sameListing:entries===pane._explorerEntries});
        """)
        self.assertEqual(result, {'ok': True, 'listingRenders': 0, 'treeRenders': 1,
                                 'buffer': 'unsaved', 'sameEdit': True, 'sameListing': True})

    def test_unchanged_real_consumers_do_not_render_and_commit_new_tokens(self):
        result = self._run_node("""
            recordBoth('', 'old');
            installEntries(()=>({directory_revision:'new',entries:[]}));
            const ok=await realQuietRefresh(0,{dirs:['']});
            emit({ok,listingRenders,treeRenders,
                listing:sandbox.explorerDirectoryBaselineRevision(pane,''),
                tree:sandbox.explorerDirectoryBaselineRevision(pane,'','tree')});
        """)
        self.assertEqual(result, {'ok': True, 'listingRenders': 0, 'treeRenders': 0,
                                 'listing': 'new', 'tree': 'new'})

    def test_tree_tokens_commit_only_when_every_scratch_read_succeeds(self):
        result = self._run_node("""
            pane._explorerMode='file';
            pane._explorerTreeChildren.set('',[{path:'docs',type:'directory'}]);
            pane._explorerTreeChildren.set('docs',[]);
            pane._explorerTreeExpanded.add('docs');
            recordBoth('', 'old'); recordBoth('docs','old');
            const original=pane._explorerTreeChildren;
            installEntries(path=>path==='docs' ? {ok:false} :
                {directory_revision:'new',entries:[{path:'docs',type:'directory'}, {path:'new',type:'file'}]});
            const ok=await realQuietTree(0,['','docs']);
            emit({ok,sameMap:original===pane._explorerTreeChildren,treeRenders,
                baseline:sandbox.explorerDirectoryBaselineRevision(pane,'','tree')});
        """)
        self.assertEqual(result, {'ok': False, 'sameMap': True, 'treeRenders': 0, 'baseline': 'old'})

    def test_expanded_folder_deletion_or_rename_refreshes_parent_and_survivors(self):
        for replacement in ('', 'renamed'):
            with self.subTest(replacement=replacement):
                result = self._run_node("""
                    pane._explorerMode='file';
                    pane._explorerTreeChildren=new Map([
                        ['', [{path:'gone',type:'directory'}, {path:'keep',type:'directory'}]],
                        ['gone', [{path:'gone/deep',type:'directory'}]],['gone/deep',[]],['keep',[]]
                    ]);
                    pane._explorerTreeExpanded=new Set(['gone','gone/deep','keep']);
                    const calls=[];
                    const replacement=REPLACEMENT;
                    sandbox.fetch=async url=> {
                        const path=new URL('http://localhost'+url).searchParams.get('path')||'';
                        calls.push(path);
                        if(path==='gone'||path.startsWith('gone/')) return {
                            ok:false,status:404,json:async()=>({code:'not_found'})};
                        if(url.includes('/directory/state')) return {ok:true,json:async()=>({
                            revision:'new',root_revision:'root-rev',complete:true})};
                        return {ok:true,json:async()=>({root_revision:'root-rev',directory_revision:'new',
                            entries:path==='' ? [{path:'keep',type:'directory'},
                                ...(replacement?[{path:replacement,type:'directory'}]:[])] : []})};
                    };
                    sandbox.queueExplorerDirectoryChange(pane,'gone');
                    await sandbox.explorerDirectoryWatchCheckOne(0);
                    emit({suspended:Boolean(pane._explorerDirWatchSuspended),
                        targets:sandbox.explorerDirectoryWatchTargets(pane),
                        cached:[...pane._explorerTreeChildren.keys()],
                        root:pane._explorerTreeChildren.get('').map(e=>e.path),treeRenders});
                """.replace('REPLACEMENT', json.dumps(replacement)))
                self.assertFalse(result['suspended'])
                self.assertEqual(result['targets'], ['', 'keep'])
                self.assertEqual(result['cached'], ['', 'keep'])
                self.assertEqual(result['root'], ['keep'] + ([replacement] if replacement else []))
                self.assertEqual(result['treeRenders'], 1)

    def test_deleted_open_directory_does_not_block_surviving_tree(self):
        result = self._run_node("""
            pane._explorerPath='gone';
            pane._explorerTreeChildren=new Map([
                ['', [{path:'gone',type:'directory'}, {path:'keep',type:'directory'}]],
                ['gone', []], ['keep', []]
            ]);
            pane._explorerTreeExpanded=new Set(['gone','keep']);
            sandbox.queueExplorerDirectoryChange(pane,'gone');
            sandbox.fetch=async url=> {
                const path=new URL('http://localhost'+url).searchParams.get('path')||'';
                if(path==='gone') return {ok:false,status:404,json:async()=>({code:'not_found'})};
                if(url.includes('/directory/state')) return {ok:true,json:async()=>({
                    revision:'new',root_revision:'root-rev',complete:true})};
                return {ok:true,json:async()=>({root_revision:'root-rev',directory_revision:'new',
                    entries:path==='' ? [{path:'keep',type:'directory'}] : []})};
            };
            await sandbox.explorerDirectoryWatchCheckOne(0);
            await sandbox.explorerDirectoryWatchCheckOne(0);
            emit({suspended:Boolean(pane._explorerDirWatchSuspended),
                failures:pane._explorerDirWatchFailures,
                cached:[...pane._explorerTreeChildren.keys()],
                pending:Boolean(pane._explorerDirWatchPending),treeRenders});
        """)
        self.assertEqual(result, {'suspended': False, 'failures': 0,
                                 'cached': ['', 'keep'], 'pending': False, 'treeRenders': 1})


class QuietOwnershipTestCase(ExplorerDirectoryWatchHarness):
    def test_real_user_reload_invalidates_quiet_work_at_start(self):
        result = self._run_node("""
            vm.runInContext(fs.readFileSync(process.argv[7], 'utf8'), sandbox);
            sandbox.isExplorerSession=()=>true;
            const navigation=gate(), entries=gate();
            sandbox.confirmDiscardExplorerEdit=async()=>{await navigation.promise;return false;};
            let writes=0;
            sandbox.renderExplorerDirectoryRows=()=>{writes++;};
            recordBoth('', 'old');
            installEntries(async()=>{await entries.promise;return {directory_revision:'new',entries:[{path:'new'}]};});
            const applying=realQuietListing(0);
            await nextTurn();
            const loading=sandbox.loadExplorerPane(0,'',{force:true});
            entries.resolve();
            const applied=await applying;
            navigation.resolve(); await loading;
            emit({applied,writes,baseline:sandbox.explorerDirectoryBaselineRevision(pane,'')});
        """)
        self.assertEqual(result, {'applied': False, 'writes': 0, 'baseline': 'old'})

    def test_real_collapse_reexpand_aba_discards_quiet_tree_response(self):
        result = self._run_node("""
            pane._explorerMode='file';
            pane._explorerTreeExpanded.add('docs');
            pane._explorerTreeChildren.set('docs',[]);
            recordBoth('docs','old');
            const entries=gate();
            installEntries(async()=>{await entries.promise;return {directory_revision:'new',entries:[{path:'new'}]};});
            sandbox.notePanePresentationChanged=()=>{};
            sandbox.hydrateExplorerTreeExpansion=async()=>{};
            const applying=realQuietTree(0,['docs']);
            await nextTurn();
            await sandbox.toggleExplorerTreeDirectory(0,'docs');
            await sandbox.toggleExplorerTreeDirectory(0,'docs');
            const rendersBefore=treeRenders;
            entries.resolve();
            const applied=await applying;
            emit({applied,extraRenders:treeRenders-rendersBefore,
                expanded:pane._explorerTreeExpanded.has('docs'),
                baseline:sandbox.explorerDirectoryBaselineRevision(pane,'docs','tree')});
        """)
        self.assertEqual(result, {'applied': False, 'extraRenders': 0, 'expanded': True, 'baseline': 'old'})

    def test_listing_discards_navigation_reload_root_and_replacement_responses(self):
        for mutation in (
            "pane._explorerPath='elsewhere'",
            "pane._explorerDirectoryEpoch=(pane._explorerDirectoryEpoch||0)+1",
            "pane._explorerRootRevision='different'",
            "pane._explorerMode='file'",
            "sandbox.terminals[0]=makePane()",
            "sandbox.sessionIds[0]='new-session'",
        ):
            with self.subTest(mutation=mutation):
                result = self._run_node("""
                    recordBoth('', 'old');
                    const held=gate();
                    let requests=0;
                    installEntries(async()=>{requests++;await held.promise;return {directory_revision:'new',entries:[{path:'new'}]};});
                    const applying=realQuietRefresh(0,{dirs:['']});
                    await nextTurn();
                    MUTATION;
                    held.resolve();
                    const ok=await applying;
                    emit({ok,requests,listingRenders,treeRenders,
                        baseline:sandbox.explorerDirectoryBaselineRevision(pane,'')});
                """.replace('MUTATION', mutation))
                self.assertEqual(result, {'ok': False, 'requests': 1, 'listingRenders': 0,
                                         'treeRenders': 0, 'baseline': 'old'})

    def test_quiet_tree_discards_root_cache_open_expansion_and_reload_changes(self):
        for mutation in (
            "pane._explorerRootRevision='different'",
            "pane._explorerTreeSidebarOpen=false",
            "pane._explorerTreeChildren=new Map(pane._explorerTreeChildren)",
            "pane._explorerTreeExpanded.add('extra')",
            "pane._explorerTreeEpoch=(pane._explorerTreeEpoch||0)+1",
            "pane._explorerDirectoryEpoch=(pane._explorerDirectoryEpoch||0)+1",
        ):
            with self.subTest(mutation=mutation):
                result = self._run_node("""
                    pane._explorerMode='file'; recordBoth('', 'old');
                    const held=gate();
                    installEntries(async()=>{await held.promise;return {directory_revision:'new',entries:[{path:'new'}]};});
                    const applying=realQuietTree(0,['']);
                    await nextTurn(); MUTATION; held.resolve();
                    const ok=await applying;
                    emit({ok,treeRenders,baseline:sandbox.explorerDirectoryBaselineRevision(pane,'','tree')});
                """.replace('MUTATION', mutation))
                self.assertEqual(result, {'ok': False, 'treeRenders': 0, 'baseline': 'old'})

    def test_interaction_or_hidden_state_starting_during_fetch_holds_work(self):
        for mode in ('directory', 'file'):
            for mutation in (
                "vm.runInContext('explorerGitWatchPointerDown=true',sandbox)",
                "document.visibilityState='hidden'",
            ):
                with self.subTest(mode=mode, mutation=mutation):
                    result = self._run_node("""
                        pane._explorerMode=MODE; recordBoth('', 'old');
                        const held=gate();
                        installEntries(async()=>{await held.promise;return {directory_revision:'new',entries:[{path:'new'}]};});
                        sandbox.queueExplorerDirectoryChange(pane,'');
                        const applying=sandbox.explorerDirectoryWatchFlushPending(0);
                        await nextTurn(); MUTATION; held.resolve(); await applying;
                        const deferred={listingRenders,treeRenders,pending:Boolean(pane._explorerDirWatchPending),
                            baseline:sandbox.explorerDirectoryBaselineRevision(pane,'','tree')};
                        document.visibilityState='visible';
                        vm.runInContext('explorerGitWatchPointerDown=false',sandbox);
                        await sandbox.explorerDirectoryWatchFlushPending(0);
                        emit({deferred,pending:Boolean(pane._explorerDirWatchPending),treeRenders});
                    """.replace('MODE', json.dumps(mode)).replace('MUTATION', mutation))
                    self.assertEqual(result['deferred'], {'listingRenders': 0, 'treeRenders': 0,
                                                          'pending': True, 'baseline': 'old'})
                    self.assertFalse(result['pending'])
                    self.assertEqual(result['treeRenders'], 1)

    def test_poll_discards_same_path_reload_and_changed_target_membership(self):
        for mutation in (
            "pane._explorerDirectoryEpoch=(pane._explorerDirectoryEpoch||0)+1",
            "pane._explorerTreeSidebarOpen=false",
            "pane._explorerTreeChildren=new Map()",
        ):
            with self.subTest(mutation=mutation):
                result = self._run_node("""
                    recordBoth('', 'old'); dirAnswer.revision='new';
                    holdNextDirectoryState();
                    const polling=sandbox.explorerDirectoryWatchCheckOne(0);
                    await nextTurn(); MUTATION; stateGate.resolve(); await polling;
                    emit({calls:applyCalls.length,pending:Boolean(pane._explorerDirWatchPending)});
                """.replace('MUTATION', mutation))
                self.assertEqual(result, {'calls': 0, 'pending': False})

    def test_root_reset_invalidates_baselines_pending_and_inflight_generation(self):
        result = self._run_node("""
            recordBoth('', 'old'); sandbox.queueExplorerDirectoryChange(pane,'');
            const owner=sandbox.captureExplorerDirectoryOwner(0);
            sandbox.clearExplorerDirectoryBaselines(pane);
            emit({current:sandbox.explorerDirectoryOwnerCurrent(0,owner),
                listing:pane._explorerDirRevisions.size,tree:pane._explorerTreeDirRevisions.size,
                pending:Boolean(pane._explorerDirWatchPending),cache:pane._explorerTreeChildren.size});
        """)
        self.assertEqual(result, {'current': False, 'listing': 0, 'tree': 0, 'pending': False, 'cache': 0})


if __name__ == "__main__":
    unittest.main()
