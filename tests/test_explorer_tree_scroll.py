"""The Files tree keeps the reader's place across the things that rebuild it.

Two halves of one report. Expansion has survived a workspace save and restore
for a while (``explorer_tree_expanded`` rides the presentation snapshot and
``hydrateExplorerTreeExpansion`` re-lists it), but the *offset* captured beside
it did not: ``loadExplorerTree`` finished with the reveal walk, which renders
the panel and scrolls the shown file's row into view, so every restored pane
opened its tree parked on whatever the Preview tab happened to be showing
rather than where the reader had left it. And a filesystem mutation --
upload, delete, rename, or a Git action -- runs ``reloadExplorerTree``, which
empties the panel to re-list it and handed the reader back the top of a tree
they had navigated by hand.

Executed in Node against the real ``explorer-tree.js``. The panel stub models
the one browser behaviour that makes this a bug rather than a preference: an
emptied scroller clamps to 0, and it reports that clamp as a ``scroll`` event
the sidebar's capture-phase listener answers by *storing* it.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

TREE_JS = (
    Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "explorer-tree.js"
)

NODE = shutil.which("node")

#   docs/        src/          tests/       readme.md
#     guides/      api/          unit/
#                    v2/
#                      thing.js
#                  core/
FIXTURE_CHILDREN = {
    "": [
        {"type": "directory", "path": "docs", "name": "docs"},
        {"type": "directory", "path": "src", "name": "src"},
        {"type": "directory", "path": "tests", "name": "tests"},
        {"type": "file", "path": "readme.md", "name": "readme.md"},
    ],
    "docs": [{"type": "directory", "path": "docs/guides", "name": "guides"}],
    "docs/guides": [],
    "src": [
        {"type": "directory", "path": "src/api", "name": "api"},
        {"type": "directory", "path": "src/core", "name": "core"},
    ],
    "src/api": [{"type": "directory", "path": "src/api/v2", "name": "v2"}],
    "src/api/v2": [{"type": "file", "path": "src/api/v2/thing.js", "name": "thing.js"}],
    "src/core": [],
    "tests": [{"type": "directory", "path": "tests/unit", "name": "unit"}],
    "tests/unit": [],
}

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const options = JSON.parse(process.argv[3]);
const children = options.children;
const events = [];

/* The panel stub carries exactly the browser behaviour under test: replacing
   the body's markup collapses the document under the scroller, so the offset
   is clamped to 0 -- and the sidebar's capture-phase scroll listener answers
   that clamp by storing it as the reader's position. */
let panel;
const body = {
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(value) {
        this._html = value;
        events.push({ type: 'render', scrollTop: panel.scrollTop });
        panel.scrollTop = 0;
        scrollListener();
    }
};
panel = {
    hidden: false,
    scrollTop: 0,
    scrollLeft: 0,
    scrollHeight: 1000,
    clientHeight: 100,
    scrollWidth: 100,
    clientWidth: 100,
    _section: false,
    dataset: {},
    get innerHTML() { return ''; },
    set innerHTML(value) {
        this._section = true;
        this.scrollTop = 0;
    },
    querySelector(selector) {
        if (selector === '.explorer-tree-section') return this._section ? {} : null;
        if (selector === '[data-explorer-tree-body]') return this._section ? body : null;
        return null;
    },
    querySelectorAll() { return []; },
    addEventListener() {}
};

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
        requestAnimationFrame: () => 0,
        GridVibeExplorerGitPin: {
            explorerGitPathIsPinned: (pinned, path, pinKind, kind) => (
                typeof pinned === 'string' && pinned === path && pinKind === kind
            )
        }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    terminals: [],
    sessionIds: [],
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;

const fetched = [];
let replaced = null;
sandbox.fetch = async (url) => {
    const path = decodeURIComponent(String(url).split('path=')[1] || '');
    fetched.push({
        path,
        rebuilding: sandbox.explorerTreeRebuildInFlight(sandbox.terminals[0])
    });
    /* A group switch rehouses the slot while the listings are out. The pane
       that asked keeps its own state; nothing addressed by slot may land. */
    if (options.swapAfterFetches && fetched.length === options.swapAfterFetches) {
        replaced = sandbox.terminals[0];
        vm.runInContext(`
            terminals[0] = {
                _explorerTreeSidebarOpen: true,
                _explorerMode: 'directory',
                _explorerPath: '',
                _explorerFilePath: '',
                _explorerSidebarScroll: {},
                _explorerTreeExpanded: new Set(),
                _explorerTreeChildren: new Map(),
                _explorerTreeErrors: new Map(),
                _explorerTreeLoading: new Set()
            };
        `, sandbox);
    }
    return {
        ok: true,
        json: async () => ({ entries: children[path] || [], root_revision: 'r1' })
    };
};

// Owned by other modules; stubbed with the semantics the tree relies on.
const persisted = [];
const restored = [];
sandbox.notePanePresentationChanged = index => persisted.push(index);
sandbox.updateExplorerFilesystemRootRevision = () => {};
sandbox.refreshExplorerFilesystemCutSource = () => {};
sandbox.resetExplorerFsWatchBaseline = () => {};
sandbox.wireExplorerCopyPathMenu = () => {};
sandbox.loadExplorerPane = async () => true;
// Row markup only; their content is irrelevant to where the panel scrolls.
sandbox.EXPLORER_FOLDER_ICON = '';
sandbox.EXPLORER_OPEN_FOLDER_ICON = '';
sandbox.EXPLORER_OPEN_TAB_ICON = '';
sandbox.EXPLORER_GIT_PIN_ICON = '';
sandbox.EXPLORER_GIT_FOLLOW_ICON = '';
sandbox.explorerGitStatusLabel = () => '';
sandbox.explorerGitBadgeHtml = () => '';
sandbox.EXPLORER_TREE_INDENT_PX = 12;
sandbox.refreshExplorerSelectionHighlight = () => {};
sandbox.wireExplorerFilesystemDragSources = () => {};
sandbox.explorerTreeSearchActive = () => false;
sandbox.UI_CHEVRON_DOWN_ICON = '';
sandbox.UI_CHEVRON_RIGHT_ICON = '';
sandbox.explorerFileTypeIconHtml = () => '';

sandbox.captureScrollMetrics = el => {
    if (!el) return null;
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    return {
        scrollLeft: el.scrollLeft,
        scrollLeftRatio: 0,
        scrollTop: el.scrollTop,
        scrollTopRatio: max > 0 ? el.scrollTop / max : 0,
        wasAtBottom: max > 0 && el.scrollTop >= max - 2
    };
};
sandbox.applyScrollMetrics = (el, metrics) => {
    if (!el || !metrics) return;
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    el.scrollTop = metrics.wasAtBottom
        ? max
        : Math.min(max, Number.isFinite(metrics.scrollTop)
            ? metrics.scrollTop
            : Math.round(max * (metrics.scrollTopRatio || 0)));
};

/* The real one lives in explorer-viewer.js: it reads the pane's stored point
   (a ratio pair, which is all a restart persists) and writes it back. */
sandbox.restoreExplorerSidebarPresentation = index => {
    restored.push({ index, at: events.length });
    const point = sandbox.terminals[index] && sandbox.terminals[index]._explorerSidebarScroll
        ? sandbox.terminals[index]._explorerSidebarScroll.tree
        : null;
    if (!point) return;
    const max = Math.max(0, panel.scrollHeight - panel.clientHeight);
    panel.scrollTop = Math.round(max * (Number(point.y) || 0));
};

/* The sidebar's capture-phase scroll listener: every scroll event -- the
   rebuild's own clamp included -- is answered by re-capturing the panel into
   the pane's stored point, unless the tree says a rebuild is in flight. */
const stored = [];
function scrollListener() {
    const pane = sandbox.terminals[0];
    if (!pane) return;
    if (sandbox.explorerTreeRebuildInFlight(pane)) {
        stored.push({ suspended: true, scrollTop: panel.scrollTop });
        return;
    }
    const max = Math.max(0, panel.scrollHeight - panel.clientHeight);
    pane._explorerSidebarScroll = Object.assign({}, pane._explorerSidebarScroll, {
        tree: { x: 0, y: max > 0 ? panel.scrollTop / max : 0 }
    });
    stored.push({ suspended: false, scrollTop: panel.scrollTop });
}

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

// Built inside the context: the module gates its tree state on `instanceof
// Map`/`Set`, and those fail across vm realms.
sandbox.__fixture = options;
vm.runInContext(`
    sessionIds[0] = 'sess-0';
    terminals[0] = {
        _explorerTreeSidebarOpen: true,
        _explorerMode: __fixture.mode,
        _explorerPath: __fixture.directory,
        _explorerFilePath: __fixture.filePath,
        _explorerSidebarScroll: __fixture.storedScroll,
        _explorerTreeExpanded: new Set(__fixture.expanded),
        _explorerTreeChildren: new Map(
            __fixture.cached.map(path => [path, __fixture.children[path]])
        ),
        _explorerTreeErrors: new Map(),
        _explorerTreeLoading: new Set()
    };
`, sandbox);

panel.scrollTop = options.scrollTop || 0;

const anchored = [];
const scrolled = sandbox.scrollExplorerTreeRowIntoView;
sandbox.scrollExplorerTreeRowIntoView = (paneIndex, path) => {
    anchored.push(path);
    return scrolled(paneIndex, path);
};

const run = options.action === 'reload'
    ? sandbox.reloadExplorerTree(0)
    : sandbox.loadExplorerTree(0);

Promise.resolve(run).then(() => {
    const reported = replaced || sandbox.terminals[0];
    process.stdout.write(JSON.stringify({
        expanded: Array.from(reported._explorerTreeExpanded).sort(),
        fetched,
        anchored,
        persisted,
        restored,
        stored,
        renders: events.length,
        scrollTop: panel.scrollTop,
        replacedRebuilding: replaced
            ? sandbox.explorerTreeRebuildInFlight(replaced)
            : null,
        storedPoint: reported._explorerSidebarScroll.tree || null,
        rebuildingAfter: sandbox.explorerTreeRebuildInFlight(reported)
    }));
});
"""

RESTORED_EXPANSION = ["docs", "docs/guides", "src", "src/api", "src/api/v2"]


@unittest.skipUnless(NODE, "Node.js is required for explorer tree scroll tests")
class ExplorerTreeScrollTestCase(unittest.TestCase):
    def _run(
        self,
        *,
        action="open",
        expanded=RESTORED_EXPANSION,
        cached=None,
        stored_scroll=None,
        scroll_top=0,
        mode="file",
        file_path="src/api/v2/thing.js",
        directory="",
        swap_after_fetches=0,
    ):
        if cached is None:
            cached = sorted({""} | set(expanded))
        options = {
            "children": FIXTURE_CHILDREN,
            "cached": cached,
            "expanded": sorted(expanded),
            "storedScroll": stored_scroll or {},
            "scrollTop": scroll_top,
            "mode": mode,
            "filePath": file_path,
            "directory": directory,
            "action": action,
            "swapAfterFetches": swap_after_fetches,
        }
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TREE_JS), json.dumps(options)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    # -- Opening the tree: a restore is not a navigation --

    def test_a_restored_pane_opens_where_the_reader_left_the_tree(self):
        # The workspace snapshot carries the expansion *and* the offset it was
        # captured with. Landing on the Preview file's row instead is the
        # reported "jumps to the preview file position".
        result = self._run(stored_scroll={"tree": {"x": 0, "y": 0.5}})

        self.assertEqual(result["anchored"], [])
        self.assertEqual(result["scrollTop"], 450)
        # The ancestors of the shown file are still opened -- only the scroll
        # is left to the restore.
        self.assertIn("src/api/v2", result["expanded"])
        self.assertTrue(result["restored"])

    def test_the_offset_is_applied_after_the_last_render_not_before_it(self):
        # Every render empties the body and clamps the scroller, so a restore
        # that ran before the reveal's own render would be undone by it.
        result = self._run(stored_scroll={"tree": {"x": 0, "y": 0.5}})

        self.assertGreater(result["renders"], 0)
        self.assertEqual(result["restored"][-1]["at"], result["renders"])

    def test_a_first_show_still_reveals_the_file_the_pane_is_showing(self):
        # No stored point: the pane has never been scrolled, so there is
        # nothing to return to and the open file is the only thing to point at.
        result = self._run(stored_scroll={})

        self.assertEqual(result["anchored"], ["src/api/v2/thing.js"])
        self.assertEqual(result["restored"], [])

    def test_a_tree_left_at_the_top_is_restored_to_the_top_not_revealed(self):
        # y == 0 is a real position, not a missing one: the reader was at the
        # top, and jumping to a deep file's row would move them.
        result = self._run(stored_scroll={"tree": {"x": 0, "y": 0}})

        self.assertEqual(result["anchored"], [])
        self.assertEqual(result["scrollTop"], 0)

    # -- Rebuilds under a reader who did not ask to move --

    def test_a_reload_puts_the_reader_back_where_the_rebuild_found_them(self):
        # The upload/delete/rename/Git-action path: the panel is emptied and
        # re-listed, and the offset has to survive it.
        result = self._run(action="reload", scroll_top=360)

        self.assertEqual(result["scrollTop"], 360)
        self.assertEqual(result["expanded"], RESTORED_EXPANSION)

    def test_a_reload_relists_every_expanded_folder(self):
        # Preserving the offset must not cost the re-read the reload exists
        # for: the children cache is dropped and every open folder comes back.
        result = self._run(action="reload", scroll_top=360)

        self.assertEqual(
            sorted(entry["path"] for entry in result["fetched"]),
            ["", "docs", "docs/guides", "src", "src/api", "src/api/v2"],
        )

    # -- The capture the rebuild's own clamp would otherwise win --

    def test_the_clamp_a_rebuild_causes_is_never_stored_as_the_reader_s_place(self):
        # Without the guard the sequence is: empty the panel, clamp to 0, the
        # capture-phase listener stores 0, and the restore queued behind the
        # rebuild then applies that 0. The point stands until the branches are
        # back.
        result = self._run(action="reload", scroll_top=360)

        self.assertTrue(result["stored"])
        self.assertTrue(all(entry["suspended"] for entry in result["stored"]))

    def test_the_rebuild_flag_is_in_flight_only_while_the_tree_is_rebuilding(self):
        result = self._run(action="reload", scroll_top=360)

        self.assertTrue(result["fetched"])
        self.assertTrue(all(entry["rebuilding"] for entry in result["fetched"]))
        self.assertFalse(result["rebuildingAfter"])

    def test_a_reload_whose_pane_was_rehoused_writes_no_offset_into_the_slot(self):
        # `terminals[index]` is where a pane sits, not what it is: a group
        # switch hands the slot to another group's pane while the listings are
        # out, and applying this pane's offset there would move a reader who
        # never asked. The pane that asked still releases its own rebuild
        # depth, or it could never be rebuilt again.
        result = self._run(action="reload", scroll_top=360, swap_after_fetches=2)

        self.assertEqual(result["scrollTop"], 0)
        self.assertFalse(result["replacedRebuilding"])

    def test_opening_the_tree_is_bracketed_the_same_way(self):
        # loadExplorerTree nests the reveal walk inside its own rebuild, which
        # is why the guard is a depth rather than a flag.
        result = self._run(stored_scroll={"tree": {"x": 0, "y": 0.5}}, cached=[])

        self.assertTrue(result["fetched"])
        self.assertTrue(all(entry["rebuilding"] for entry in result["fetched"]))
        self.assertFalse(result["rebuildingAfter"])


if __name__ == "__main__":
    unittest.main()
