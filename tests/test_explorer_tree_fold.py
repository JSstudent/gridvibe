"""Behavioral contracts for Files-tree navigation and fold controls.

The Markdown source view has fanned a fold out over every heading sharing the
clicked heading's level for a while; the Files tree only ever toggled the one
folder, so tidying a tree with a dozen folders open meant a dozen clicks. The
tree's notion of "this level" is the clicked folder's *siblings* — Alt+clicking
an open root-level folder therefore folds the whole tree in one gesture.

Folder-name navigation is independent of those controls: it opens the folder
in Preview without changing the tree's expanded set. Preview's reveal then
opens only the ancestors required to show its destination.

Executed in Node against the real ``explorer-tree.js`` rather than asserted as
source text: what matters is the expanded set each gesture leaves behind, the
Preview destination, and which directory listings it had to fetch to get there.
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

# A tree two levels deep in three separate branches, so a fold that reaches
# across branches (or down into one it should not) is visible in the result.
#
#   docs/        src/          tests/       readme.md
#     guides/      api/          unit/
#                    v2/
#                  core/
FIXTURE_CHILDREN = {
    "": [
        {"type": "directory", "path": "docs", "name": "docs"},
        {"type": "directory", "path": "src", "name": "src"},
        {"type": "directory", "path": "tests", "name": "tests"},
        {"type": "file", "path": "readme.md", "name": "readme.md"},
    ],
    "docs": [{"type": "directory", "path": "docs/guides", "name": "guides"}],
    "src": [
        {"type": "directory", "path": "src/api", "name": "api"},
        {"type": "directory", "path": "src/core", "name": "core"},
    ],
    "src/api": [{"type": "directory", "path": "src/api/v2", "name": "v2"}],
    "src/core": [],
    "src/api/v2": [],
    "tests": [{"type": "directory", "path": "tests/unit", "name": "unit"}],
    "tests/unit": [],
    "docs/guides": [],
}

# The fold never touches an element: renderExplorerTreePanel bails out when the
# panel id resolves to nothing, so the sandbox only has to be enough to load the
# module and hold the pane state the gesture rewrites.
HARNESS = """
const fs = require('fs');
const vm = require('vm');

function makeSandbox() {
    const sandbox = {
        console,
        document: {
            getElementById: () => null,
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
        sessionIds: [],
        escHtml: value => String(value == null ? '' : value)
    };
    sandbox.globalThis = sandbox;
    return sandbox;
}

const children = JSON.parse(process.argv[3]);
const cached = JSON.parse(process.argv[4]);
const expanded = JSON.parse(process.argv[5]);
const action = process.argv[6];
const target = process.argv[7];

const sandbox = makeSandbox();
const fetched = [];
sandbox.fetch = async (url) => {
    const path = decodeURIComponent(String(url).split('path=')[1] || '');
    fetched.push(path);
    return {
        ok: true,
        json: async () => ({ entries: children[path] || [], root_revision: 'r1' })
    };
};
// Owned by other modules; the fold only needs them to be callable.
const persisted = [];
const navigated = [];
sandbox.notePanePresentationChanged = index => persisted.push(index);
sandbox.updateExplorerFilesystemRootRevision = () => {};
sandbox.refreshExplorerFilesystemCutSource = () => {};
sandbox.loadExplorerPane = async (index, path) => {
    navigated.push({ index, path });
    return true;
};

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

// Built inside the context: the module gates its tree state on `instanceof
// Map`/`Set`, and those fail across vm realms — a pane assembled out here
// would be silently reset by ensureExplorerTreeState before the fold ran.
sandbox.__fixture = { children, cached, expanded };
vm.runInContext(`
    sessionIds[0] = 'sess-0';
    terminals[0] = {
        _explorerTreeSidebarOpen: true,
        _explorerMode: 'directory',
        _explorerPath: '',
        _explorerTreeExpanded: new Set(__fixture.expanded),
        _explorerTreeChildren: new Map(
            __fixture.cached.map(path => [path, __fixture.children[path]])
        ),
        _explorerTreeErrors: new Map(),
        _explorerTreeLoading: new Set()
    };
`, sandbox);

// The tree panel is not in this sandbox, so the real anchor no-ops. Recording
// it here is what shows the fold re-anchors on the clicked row, and on which.
const anchored = [];
const scrolled = sandbox.scrollExplorerTreeRowIntoView;
sandbox.scrollExplorerTreeRowIntoView = (paneIndex, path) => {
    anchored.push(path);
    return scrolled(paneIndex, path);
};

let run;
if (action === 'level') {
    run = sandbox.toggleExplorerTreeLevel(0, target);
} else if (action === 'open') {
    run = sandbox.openExplorerTreeDirectory(0, target);
} else if (action === 'reveal') {
    run = sandbox.revealExplorerTreePath(0, target);
} else if (action === 'restore') {
    run = sandbox.loadExplorerTree(0);
} else {
    run = sandbox.toggleExplorerTreeDirectory(0, target);
}

Promise.resolve(run).then(() => {
    process.stdout.write(JSON.stringify({
        expanded: Array.from(sandbox.terminals[0]._explorerTreeExpanded).sort(),
        fetched: fetched.slice().sort(),
        anchored,
        persisted,
        navigated
    }));
});
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer tree fold tests")
class ExplorerTreeFoldLevelTestCase(unittest.TestCase):
    def _fold(self, *, expanded, target, cached=None, action="level"):
        """Run the gesture and report the expanded set it leaves behind."""
        if cached is None:
            # Anything already expanded has necessarily been listed, plus the
            # root, which the tree loads on open.
            cached = sorted({""} | set(expanded))
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(TREE_JS),
                    json.dumps(FIXTURE_CHILDREN),
                    json.dumps(cached),
                    json.dumps(sorted(expanded)),
                    action,
                    target,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_alt_click_on_an_open_root_folder_folds_the_whole_tree(self):
        # The reported gesture: a pile of folders open, one Alt+click on any
        # open root-level folder, and the tree is tidy again.
        result = self._fold(
            expanded=[
                "docs",
                "docs/guides",
                "src",
                "src/api",
                "src/api/v2",
                "tests",
            ],
            target="src",
        )
        self.assertEqual(result["expanded"], [])
        # Collapsing is pure state — it must not re-list anything.
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["persisted"], [0])
        # Most of the rows under the scroll position have just gone, so the
        # tree re-anchors on the folder that was clicked rather than wherever
        # the clamped scrollTop happens to land.
        self.assertEqual(result["anchored"], ["src"])

    def test_folding_a_level_forgets_the_expansions_beneath_it(self):
        # A reset, not a hide: re-opening src/ afterwards gives a collapsed
        # folder rather than springing the old subtree back.
        result = self._fold(
            expanded=["src", "src/api", "src/api/v2"],
            target="src",
        )
        self.assertEqual(result["expanded"], [])

    def test_a_level_is_the_clicked_folder_s_siblings_not_its_depth(self):
        # src/api and docs/guides sit at the same depth. Alt+clicking src/api
        # must leave the docs/ branch — and src/ itself — exactly as they were.
        result = self._fold(
            expanded=["docs", "docs/guides", "src", "src/api", "src/api/v2"],
            target="src/api",
        )
        self.assertEqual(result["expanded"], ["docs", "docs/guides", "src"])

    def test_alt_click_on_a_closed_folder_opens_the_whole_level(self):
        # Mirrors the clicked row the other way: closed means expand, and every
        # sibling folder gets listed so its children are there to show.
        result = self._fold(expanded=[], target="docs")
        self.assertEqual(result["expanded"], ["docs", "src", "tests"])
        self.assertEqual(result["fetched"], ["docs", "src", "tests"])
        self.assertEqual(result["persisted"], [0])
        # Anchored once on the rebuild and again once the level has filled in,
        # since folders listed above the clicked one push it down the panel.
        self.assertEqual(result["anchored"], ["docs", "docs"])

    def test_expanding_a_level_reuses_already_listed_folders(self):
        # src/ was listed while it was open earlier; re-expanding the level
        # must come off the children cache instead of re-fetching it.
        result = self._fold(expanded=[], target="docs", cached=["", "src"])
        self.assertEqual(result["expanded"], ["docs", "src", "tests"])
        self.assertEqual(result["fetched"], ["docs", "tests"])

    def test_a_level_with_no_loaded_parent_listing_is_a_no_op(self):
        # Nothing is known about the siblings, so the gesture guesses nothing.
        result = self._fold(expanded=[], target="src/api", cached=[])
        self.assertEqual(result["expanded"], [])
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["persisted"], [])
        # Nothing moved, so nothing is scrolled either.
        self.assertEqual(result["anchored"], [])

    def test_a_plain_click_still_toggles_only_the_clicked_folder(self):
        # The unmodified click keeps its old behaviour, deeper folds included:
        # only Alt+click resets what is open underneath.
        result = self._fold(
            expanded=["docs", "src", "src/api"],
            target="src",
            action="directory",
        )
        self.assertEqual(result["expanded"], ["docs", "src/api"])
        self.assertEqual(result["navigated"], [])
        self.assertEqual(result["persisted"], [0])

    def test_a_folder_name_opens_preview_without_changing_tree_expansion(self):
        result = self._fold(
            expanded=["src", "src/api"],
            target="docs",
            action="open",
        )

        self.assertEqual(result["navigated"], [{"index": 0, "path": "docs"}])
        self.assertEqual(result["expanded"], ["src", "src/api"])
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["persisted"], [])

    def test_preview_navigation_expands_ancestors_but_not_the_destination(self):
        result = self._fold(
            expanded=[],
            cached=[""],
            target="src/api",
            action="reveal",
        )

        self.assertEqual(result["expanded"], ["src"])
        self.assertEqual(result["fetched"], ["src"])
        self.assertEqual(result["navigated"], [])


@unittest.skipUnless(NODE, "Node.js is required for explorer tree fold tests")
class ExplorerTreeExpansionRestoreTestCase(unittest.TestCase):
    """Opening the tree re-lists the expansion a restore handed back.

    Expansion is persisted and the listings behind it are not, so a restored
    pane holds a set of paths and an empty children cache. The reveal walk only
    ever opens the ancestors of the path the pane is showing, which left every
    other restored folder rendering an open chevron above nothing at all --
    reported as "expanded directories get folded after restore", with the
    chevron left pointing down over an empty branch.
    """

    _fold = ExplorerTreeFoldLevelTestCase._fold

    def test_a_restored_expansion_set_is_relisted_when_the_tree_opens(self):
        # Nothing cached: exactly what a restore hands back.
        result = self._fold(
            expanded=["docs", "docs/guides", "src", "src/api"],
            cached=[],
            target="",
            action="restore",
        )
        self.assertEqual(
            result["expanded"], ["docs", "docs/guides", "src", "src/api"]
        )
        # The root plus every restored folder, so each open chevron has rows
        # underneath it.
        self.assertEqual(
            result["fetched"], ["", "docs", "docs/guides", "src", "src/api"]
        )

    def test_a_branch_outside_the_shown_path_is_listed_too(self):
        # The reveal walk covers src/ because the pane is showing src/api;
        # docs/ is the branch that used to come back empty.
        result = self._fold(
            expanded=["docs", "docs/guides"],
            cached=[],
            target="",
            action="restore",
        )
        self.assertEqual(result["fetched"], ["", "docs", "docs/guides"])

    def test_an_already_listed_tree_is_not_refetched(self):
        # Re-opening the sidebar inside a session: the children cache still
        # holds every branch, so the walk costs no request at all.
        result = self._fold(
            expanded=["docs", "src"],
            cached=["", "docs", "src"],
            target="",
            action="restore",
        )
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["expanded"], ["docs", "src"])
        self.assertEqual(result["persisted"], [])

    def test_an_expansion_whose_folder_is_gone_is_dropped_not_fetched(self):
        # A folder deleted or renamed since the snapshot: its parent's listing
        # is the proof, so it is never requested and never cached as an error
        # against a row that no longer exists.
        result = self._fold(
            expanded=["src", "src/gone"],
            cached=[],
            target="",
            action="restore",
        )
        self.assertEqual(result["expanded"], ["src"])
        self.assertNotIn("src/gone", result["fetched"])
        # Dropping a stale path is a change to persisted pane state.
        self.assertEqual(result["persisted"], [0])

    def test_an_unproven_expansion_is_left_alone(self):
        # src/ is collapsed, so nothing loads its listing and nothing can say
        # whether src/api still exists. Unproven is not stale.
        result = self._fold(
            expanded=["src/api"],
            cached=[],
            target="",
            action="restore",
        )
        self.assertEqual(result["expanded"], ["src/api"])
        self.assertEqual(result["fetched"], [""])

    def test_reopening_a_collapsed_folder_relists_its_restored_descendants(self):
        # A collapse keeps what was open underneath it. Inside a session the
        # descendants come back off the cache; after a restore the cache is
        # empty, so expanding src/ alone left src/api open above nothing.
        result = self._fold(
            expanded=["src/api"],
            cached=[""],
            target="src",
            action="directory",
        )
        self.assertEqual(result["expanded"], ["src", "src/api"])
        self.assertEqual(result["fetched"], ["src", "src/api"])


ANCHOR_HARNESS = """
const fs = require('fs');
const vm = require('vm');

const rowTop = Number(process.argv[3]);
const scrollTop = Number(process.argv[4]);

// A 200px-tall panel showing 20px rows. The row under test is the only one
// rendered; the fold's anchor call resolves it exactly like a live tree does,
// through the row button's own dataset.
const panel = {
    hidden: false,
    scrollTop,
    getBoundingClientRect: () => ({ top: 0, bottom: 200, height: 200 })
};
const row = {
    className: 'explorer-tree-row',
    getBoundingClientRect: () => ({ top: rowTop, bottom: rowTop + 20, height: 20 })
};
const button = {
    dataset: { explorerTreeDir: 'src' },
    closest: selector => (selector === '.explorer-tree-row' ? row : null)
};
panel.querySelector = () => null;
panel.querySelectorAll = () => [button];

const sandbox = makeSandbox();
sandbox.document.getElementById = id => (id === 'explorer-tree-panel-0' ? panel : null);
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const found = sandbox.scrollExplorerTreeRowIntoView(0, 'src');
process.stdout.write(JSON.stringify({ scrollTop: panel.scrollTop, found: Boolean(found) }));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer tree fold tests")
class ExplorerTreeAnchorScrollTestCase(unittest.TestCase):
    """What the anchor actually does to the panel's scroll position.

    The fold calls this; these cases are the reason it fixes the tree landing
    somewhere unrelated once a level's worth of rows disappears.
    """

    def _anchor(self, *, row_top, scroll_top):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "anchor.js"
            # The sandbox factory lives in the fold harness; reuse it verbatim.
            preamble = HARNESS[: HARNESS.index("const children =")]
            script_path.write_text(
                preamble + ANCHOR_HARNESS[ANCHOR_HARNESS.index("const rowTop") :],
                encoding="utf-8",
            )
            completed = subprocess.run(
                [NODE, str(script_path), str(TREE_JS), str(row_top), str(scroll_top)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_row_below_the_viewport_is_scrolled_up_into_view(self):
        # Where a fold leaves you when the clamped scrollTop sits past the row.
        result = self._anchor(row_top=300, scroll_top=0)
        self.assertTrue(result["found"])
        # Just far enough to clear the bottom edge, plus the one-row margin.
        self.assertEqual(result["scrollTop"], 140)

    def test_a_row_above_the_viewport_is_scrolled_down_into_view(self):
        result = self._anchor(row_top=-50, scroll_top=100)
        self.assertEqual(result["scrollTop"], 30)

    def test_a_row_already_in_view_is_left_where_it_is(self):
        # The property the tree already relied on: ordinary clicking around
        # inside the tree must never make the panel jump.
        result = self._anchor(row_top=100, scroll_top=100)
        self.assertEqual(result["scrollTop"], 100)


if __name__ == "__main__":
    unittest.main()
