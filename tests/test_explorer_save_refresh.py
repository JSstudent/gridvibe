"""What saving a file is allowed to do to the file tree beside it.

A save changes one file's *contents*. It cannot create, delete or rename a
path, so the set of rows the tree draws cannot have moved — only the saved
file's own row can (its Git badge turns a clean file modified, and the
filesystem revision the delete/move guards check moves with its mtime).

Reloading the whole tree for that dropped every cached directory, flashed a
near-empty panel, spent one request per expanded folder and left the reader
scrolled back to the top of a tree they had navigated by hand. These tests run
the real viewer and extracted Git-sidebar scripts in Node and pin the narrower contract: one
request, for one directory; every other folder keeps its cache and its
expansion; the rows being re-read stay on screen for the round trip; and the
panel's scroll survives the rebuild that follows.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
# The row markup pulls its icons from terminal-icons.js, which is nothing but
# constants — the tree renders against the real ones rather than stand-ins.
ICONS_JS = STATIC_JS / "terminal-icons.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
GIT_SIDEBAR_JS = STATIC_JS / "explorer-git-sidebar.js"
NODE = shutil.which("node")

HARNESS = """
const fs = require('fs');
const vm = require('vm');

// The tree panel is its own overflow:auto scroller; a render replaces its body
// and the browser drops the position, which is the reset this stub reproduces.
const panel = {
    scrollTop: 0,
    scrollLeft: 0,
    scrollHeight: 1200,
    clientHeight: 300,
    scrollWidth: 260,
    clientWidth: 260
};

const requested = [];
let duringFetch = null;

const sandbox = {
    console,
    document: {
        getElementById: id => (id === 'explorer-tree-panel-0' ? panel : null),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {},
        setTimeout,
        clearTimeout,
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        requestAnimationFrame: () => 0
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    terminals: [],
    sessionIds: ['s0'],
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value),
    // Owned by explorer-fs.js, which this harness does not load.
    updateExplorerFilesystemRootRevision: () => {},
    fetch: async url => {
        requested.push(url);
        const pane = sandbox.terminals[0];
        // The rows being re-read must still be renderable while the request is
        // in flight — clearing the cache first is what blanked the folder.
        duringFetch = {
            cached: pane._explorerTreeChildren.has('src'),
            expanded: [...pane._explorerTreeExpanded]
        };
        return {
            ok: true,
            json: async () => ({
                root_revision: 'root-rev',
                entries: [
                    { name: 'a.py', path: 'src/a.py', type: 'file', git: { status: 'modified' }, revision: 'new' }
                ]
            })
        };
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});

/* Rendering the panel needs the whole tree DOM; what matters here is that it
   runs and that it costs the panel its scroll, exactly as the real rebuild
   does. */
let renders = 0;
sandbox.renderExplorerTreePanel = () => {
    renders += 1;
    panel.scrollTop = 0;
};

/* ensureExplorerTreeState() rebuilds any tree collection that is not an
   `instanceof Map`/`Set` — and a Map built out here belongs to the harness's
   realm, not the sandbox's, so it fails that check and the fixture is silently
   thrown away. Build them with the context's own constructors. */
const VmMap = vm.runInContext('Map', sandbox);
const VmSet = vm.runInContext('Set', sandbox);

function openTree(extra) {
    const pane = Object.assign({
        _explorerTreeSidebarOpen: true,
        _explorerTreeExpanded: new VmSet(['src']),
        _explorerTreeChildren: new VmMap([
            ['', [{ name: 'src', path: 'src', type: 'directory' }]],
            ['src', [{ name: 'a.py', path: 'src/a.py', type: 'file', git: null, revision: 'old' }]],
            ['docs', [{ name: 'guide.md', path: 'docs/guide.md', type: 'file' }]]
        ]),
        _explorerTreeErrors: new VmMap(),
        _explorerTreeLoading: new VmSet()
    }, extra || {});
    sandbox.terminals[0] = pane;
    requested.length = 0;
    duringFetch = null;
    renders = 0;
    panel.scrollTop = 420;
    return pane;
}

(async () => {
    const results = {};

    // 1. The saved file's own directory, and nothing else.
    {
        const pane = openTree();
        await sandbox.refreshExplorerTreeFileEntry(0, 'src/a.py');
        results.refreshed = {
            requested: requested.slice(),
            duringFetch,
            renders,
            scrollTop: panel.scrollTop,
            badge: pane._explorerTreeChildren.get('src')[0].git,
            revision: pane._explorerTreeChildren.get('src')[0].revision,
            keptOtherDirectory: pane._explorerTreeChildren.has('docs'),
            keptExpansion: [...pane._explorerTreeExpanded],
            settled: pane._explorerTreeLoading.size
        };
    }

    // 2. A file at the explorer root re-reads the root listing.
    {
        openTree();
        await sandbox.refreshExplorerTreeFileEntry(0, 'README.md');
        results.rootFile = { requested: requested.slice() };
    }

    // 3. Nothing to refresh: a directory the tree never loaded has no row, and
    //    a closed sidebar has no panel. Neither may spend a request.
    {
        openTree();
        await sandbox.refreshExplorerTreeFileEntry(0, 'vendor/deep/x.js');
        const unloaded = requested.slice();
        openTree({ _explorerTreeSidebarOpen: false });
        await sandbox.refreshExplorerTreeFileEntry(0, 'src/a.py');
        results.noop = { unloaded, closed: requested.slice() };
    }

    // 4. A folder with nothing cached still says so; only a re-read is silent.
    {
        const pane = openTree();
        pane._explorerTreeLoading.add('src');
        const reReading = sandbox.renderExplorerTreeNodes(pane, 'src', 0);
        pane._explorerTreeChildren.delete('src');
        const firstLoad = sandbox.renderExplorerTreeNodes(pane, 'src', 0);
        results.placeholder = {
            reReadingIsPlaceholder: reReading.includes('explorer-tree-loading'),
            firstLoadIsPlaceholder: firstLoad.includes('explorer-tree-loading')
        };
    }

    process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer tree refresh tests")
class ExplorerSaveTreeRefreshTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(ICONS_JS),
                    str(VIEWER_JS),
                    str(GIT_SIDEBAR_JS),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(f"node harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_a_save_re_reads_only_the_saved_file_s_directory(self):
        refreshed = self.results["refreshed"]
        self.assertEqual(refreshed["requested"], ["/api/explorer/s0/entries?path=src"])
        # The rest of the tree keeps everything the reader built up.
        self.assertTrue(refreshed["keptOtherDirectory"])
        self.assertEqual(refreshed["keptExpansion"], ["src"])

    def test_the_row_that_moved_is_the_one_that_is_updated(self):
        # The Git badge and the filesystem revision the delete/move guards
        # check are the only things a save can change about a tree row.
        refreshed = self.results["refreshed"]
        self.assertEqual(refreshed["badge"], {"status": "modified"})
        self.assertEqual(refreshed["revision"], "new")

    def test_the_rows_being_re_read_stay_on_screen(self):
        # Dropping the cache before the request is what flashed a near-empty
        # panel; the old entries have to survive the whole round trip.
        during = self.results["refreshed"]["duringFetch"]
        self.assertTrue(during["cached"])
        self.assertEqual(during["expanded"], ["src"])
        self.assertEqual(self.results["refreshed"]["settled"], 0)

    def test_the_panel_keeps_its_scroll_across_the_rebuild(self):
        refreshed = self.results["refreshed"]
        # The render really did reset it — otherwise this proves nothing.
        self.assertGreaterEqual(refreshed["renders"], 1)
        self.assertEqual(refreshed["scrollTop"], 420)

    def test_a_file_at_the_root_re_reads_the_root_listing(self):
        self.assertEqual(
            self.results["rootFile"]["requested"], ["/api/explorer/s0/entries?path="]
        )

    def test_a_row_the_tree_is_not_showing_costs_no_request(self):
        noop = self.results["noop"]
        self.assertEqual(noop["unloaded"], [])
        self.assertEqual(noop["closed"], [])

    def test_only_a_folder_with_nothing_cached_shows_the_loading_placeholder(self):
        placeholder = self.results["placeholder"]
        self.assertFalse(placeholder["reReadingIsPlaceholder"])
        self.assertTrue(placeholder["firstLoadIsPlaceholder"])


if __name__ == "__main__":
    unittest.main()
