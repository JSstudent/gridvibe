"""The browsing surfaces and the Git scope: what they offer, and when they load.

Two contracts, one subject -- what the pane is *showing* is what the Git scope
is about, so every surface that shows something has to agree.

**What they offer.** The pin is one exact path, and both filesystem browsing
surfaces name exact paths: the Files tree and the directory listing the Preview
tab shows. Only the tree carried the ``data-explorer-git-scope-*`` hooks, so
right-clicking the very same folder in the listing offered copy, download and
the filesystem actions and no way to scope Git to it.

**When they load.** A file is a first-class Git scope, so opening one moves the
scope exactly as walking into a folder does. Only the listing and the image
viewer said so; the ordinary file render did not, which left the Graph on the
previous scope until the change listener's next poll noticed the revision token
had moved -- a folder switch updated at once and a file switch updated on an
interval. The converse matters just as much: navigation that leaves the scope
where it was must reach neither load nor render, because the Git panel carries
the commit-message textarea and a render nobody asked for takes the caret.

Neither ``handleExplorerContextMenu()`` nor ``renderExplorerFile()`` is
DOM-free, so both are executed in a Node ``vm`` against a stubbed page, the way
``test_explorer_fs_batch.py`` runs the filesystem menu builder. The real
``explorer-git-pin.js`` policy, ``explorer-selection.js`` model and (below)
``explorer-git-sidebar.js`` request adapter are loaded rather than stubbed, so
what is asserted is the menu the page would show and the request it would send.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
PIN_JS = STATIC_JS / "explorer-git-pin.js"
SELECTION_JS = STATIC_JS / "explorer-selection.js"

# The file render reaches across most of the explorer, so the real modules are
# loaded rather than stubbed: what is under test is which request the page
# sends, and a stubbed sidebar would only prove the stub was called.
# In the page's own load order, so the classic scripts see each other exactly
# as they do in the browser.
RENDER_MODULES = [
    STATIC_JS / name
    for name in (
        "explorer-persistence.js",
        "explorer-scroll.js",
        "explorer-theme-store.js",
        "explorer-selection.js",
        "explorer-tiers.js",
        "explorer-repaint.js",
        "explorer-git-menu.js",
        "explorer-git-active.js",
        "explorer-git-search.js",
        "explorer-git-pin.js",
        "terminal-icons.js",
        "explorer-viewer.js",
        "explorer-tree.js",
        "explorer-git-sidebar.js",
        "explorer-scroll-adapter.js",
        "explorer-diff.js",
        "explorer-tabs.js",
        "explorer-edit-highlight.js",
        "explorer-edit-overlay.js",
        "explorer-edit-find.js",
        "explorer-editor.js",
        "explorer-search.js",
        "explorer-tree-search.js",
        "explorer-overview.js",
    )
]

NODE = shutil.which("node")

# One live explorer pane. Everything the context menu reaches for that belongs
# to another module is recorded rather than simulated, so a test can assert on
# what the menu offered and on what a chosen entry asked the page to do.
HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const spec = JSON.parse(process.argv[5]);
const calls = { pins: [], follows: [], probes: [], browseTargets: [] };

function makeNode(dataset) {
    const node = {
        dataset: dataset || {},
        classList: { add() {}, remove() {}, contains: () => false },
        matches: () => false,
        querySelector: () => null,
        querySelectorAll: () => [],
        getBoundingClientRect: () => ({ top: 0, left: 0, width: 10, height: 10 })
    };
    node.closest = selector => {
        if (selector.includes('data-explorer-git-commit-toggle')) return null;
        if (selector.includes('data-explorer-copy-path')) return node.__row ? node : null;
        return null;
    };
    return node;
}

const sandbox = {
    console,
    document: {
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        createElement: () => makeNode({}),
        addEventListener() {},
        body: { dataset: {}, addEventListener() {}, classList: { add() {}, remove() {} } }
    },
    navigator: {},
    // The worktree probe takes a request slot, so a pane that goes away stops
    // holding it and a second gesture supersedes the first one's request.
    AbortController,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame() {},
    performance: { now: () => 0 },
    terminals: [],
    sessionIds: [],
    escHtml: value => String(value == null ? '' : value)
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);  // selection
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);  // git pin
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);  // viewer

/* Owned by explorer-git-sidebar.js on the page. The probe is the read-only
   state route the menu uses to decide whether a prospective pin is inside a
   worktree; `worktree` says what the server would answer. */
sandbox.explorerGitRequestUrl = (sessionId, endpoint, path, extra, kind) =>
    `/api/explorer/${sessionId}/git/${endpoint}?path=${path}&kind=${kind}`;
let probeSeq = 0;
sandbox.fetch = async url => {
    calls.probes.push(String(url));
    const mine = ++probeSeq;
    /* For the two-gesture case the *first* probe resolves last, which is the
       ordering the menu token exists for: without it the superseded handler
       opens its menu over the one the reader is actually looking at. */
    if (spec.doubleGesture) {
        await new Promise(resolve => setTimeout(resolve, mine === 1 ? 20 : 1));
    }
    return { ok: spec.worktree !== false, json: async () => ({}) };
};
sandbox.setExplorerGitPinnedScope = (index, path, kind) => {
    calls.pins.push({ index, path, kind });
    return true;
};
sandbox.clearExplorerGitPinnedScope = index => {
    calls.pins.push({ index, path: null, kind: null });
    return true;
};
sandbox.toggleExplorerGitFollowBrowsing = index => {
    calls.follows.push(index);
    sandbox.terminals[index]._explorerGitFollowBrowsing =
        !sandbox.terminals[index]._explorerGitFollowBrowsing;
    return true;
};
/* explorer-git-sidebar.js on the page. Kept faithful rather than inert,
   because what the Follow entry offers depends on where Follow currently is,
   and what it writes is the browsed scope the next load would carry. */
sandbox.explorerGitBrowsingScope = pane => {
    const derived = pane?._explorerMode === 'file' && pane._explorerFilePath
        ? { path: String(pane._explorerFilePath), kind: 'file' }
        : { path: String(pane?._explorerPath || ''), kind: 'dir' };
    return sandbox.GridVibeExplorerGitPin.explorerGitBrowsedScope(
        derived.path, derived.kind, pane?._explorerGitBrowseTarget || null
    );
};
sandbox.setExplorerGitBrowseTarget = (index, path, kind) => {
    calls.browseTargets.push({ index, path, kind });
    const pane = sandbox.terminals[index];
    const derived = { path: String(pane?._explorerPath || ''), kind: 'dir' };
    pane._explorerGitBrowseTarget = sandbox.GridVibeExplorerGitPin
        .explorerGitBrowseOverride(path, kind, derived.path, derived.kind);
    return true;
};
sandbox.refreshExplorerGitScopeAffordances = () => {};
sandbox.explorerGitScopeNeedsLoad = () => false;
sandbox.loadExplorerGitRepo = () => {};

// The menu itself: capture the item list instead of building a popup. Every
// call is recorded, because "how many menus opened" is the observation a
// superseded gesture is judged on.
let offered = [];
const menus = [];
sandbox.showExplorerContextMenu = (x, y, items) => { offered = items; menus.push(items); };

const pane = {
    _session: { startup_mode: 'explorer', explorer_root_directory: '/repo' },
    _explorerMode: spec.paneMode || 'directory',
    _explorerPath: spec.panePath || '',
    _explorerRootRevision: 'rev-1',
    _explorerGitSidebarOpen: true,
    _explorerGitFollowBrowsing: Boolean(spec.following)
};
if (spec.pinnedPath !== null && spec.pinnedPath !== undefined) {
    pane._explorerGitPinnedPath = spec.pinnedPath;
    pane._explorerGitPinKind = spec.pinnedKind || 'dir';
}
/* The repository the sidebar has already loaded. `repo_path: ''` is the
   server's word for "the worktree contains the explorer root", which settles
   the menu's worktree question for every path under it without a request. */
if (spec.loadedRepoPath !== undefined) {
    pane._explorerGitRepo = {
        git: { available: true, repo_path: spec.loadedRepoPath }
    };
}
sandbox.terminals[0] = pane;
sandbox.sessionIds[0] = 'sess-0';

/* Right-clicking a row that is part of the live selection acts on the whole
   selection, so a multi-entry selection is how the "several rows name no one
   path" rule is reached at all. Seeded through the real selection model. */
if (Array.isArray(spec.selected) && spec.selected.length) {
    let selection = null;
    for (const entry of spec.selected) {
        selection = sandbox.GridVibeExplorerSelection.applyPointerSelection(
            selection,
            {
                sessionId: 'sess-0',
                rootRevision: 'rev-1',
                surface: spec.selectedSurface || 'preview',
                entry: { path: entry.path, kind: entry.kind, revision: 'r1' },
                ctrlKey: true
            },
            spec.selected.map(item => ({ path: item.path, kind: item.kind, revision: 'r1' }))
        ).selection;
    }
    sandbox.storeExplorerSelection(0, selection);
}

const row = spec.row ? makeNode(spec.row) : null;
if (row) {
    row.__row = true;
}
const makeEvent = () => ({
    clientX: 10,
    clientY: 10,
    preventDefault() {},
    target: {
        closest: selector => {
            if (selector.includes('data-explorer-git-commit-toggle')) return null;
            if (selector.includes('data-explorer-copy-path')) return row;
            if (selector.includes('explorer-tree-panel')) return spec.blank === 'tree' ? makeNode({}) : null;
            if (selector.includes('explorer-viewer')) return spec.blank === 'preview' ? makeNode({}) : null;
            return null;
        }
    }
});

const gesture = () => Promise.resolve(sandbox.handleExplorerContextMenu(makeEvent(), 0));
// Two overlapping right-clicks, started before either can settle.
const gestures = spec.doubleGesture ? [gesture(), gesture()] : [gesture()];

Promise.all(gestures).then(async () => {
    const git = offered.filter(item => /Git/.test(item.label || ''));
    // Run the chosen entry so the menu's promise is observable, not its text.
    const chosen = spec.click ? git.find(item => item.label === spec.click) : null;
    if (chosen && !chosen.disabled) {
        // A group switch between opening the menu and clicking it: the slot
        // now holds another group's pane, and the entry must not write to it.
        if (spec.replacePaneBeforeClick) {
            sandbox.terminals[0] = { _explorerMode: 'directory', _explorerPath: '' };
        }
        await chosen.action();
    }
    process.stdout.write(JSON.stringify({
        labels: offered.map(item => item.label),
        git: git.map(item => ({
            label: item.label,
            title: item.title,
            disabled: Boolean(item.disabled)
        })),
        pins: calls.pins,
        follows: calls.follows,
        browseTargets: calls.browseTargets,
        probes: calls.probes,
        menuCount: menus.length
    }));
}).catch(error => {
    console.error(error);
    process.exit(1);
});
"""


def _menu(**spec):
    with TemporaryDirectory() as script_dir:
        script_path = Path(script_dir) / "harness.js"
        script_path.write_text(HARNESS, encoding="utf-8")
        completed = subprocess.run(
            [
                NODE,
                str(script_path),
                str(VIEWER_JS),
                str(SELECTION_JS),
                str(PIN_JS),
                json.dumps(spec),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    return completed


def _preview_row(path, kind="dir", entry_kind="directory"):
    """A directory-listing row exactly as ``explorerDirectoryRowHtml`` builds it."""
    return {
        "explorerCopyPath": path,
        "explorerContextPath": path,
        "explorerContextKind": entry_kind,
        "explorerContextRevision": "r1",
        "explorerContextSurface": "preview",
        "explorerGitScopePath": path,
        "explorerGitScopeKind": kind,
        "explorerGitScopeSurface": "preview",
    }


def _tree_row(path, kind="dir", entry_kind="directory"):
    row = _preview_row(path, kind, entry_kind)
    row["explorerContextSurface"] = "tree"
    row["explorerGitScopeSurface"] = "tree"
    return row


@unittest.skipUnless(NODE, "Node.js is required for explorer Git scope menu tests")
class ExplorerGitScopeMenuSurfaceTestCase(unittest.TestCase):
    def _ask(self, **spec):
        spec.setdefault("pinnedPath", None)
        spec.setdefault("row", None)
        completed = _menu(**spec)
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_listing_folder_offers_the_same_entries_a_tree_folder_does(self):
        listing = self._ask(row=_preview_row("web"))
        tree = self._ask(row=_tree_row("web"))
        self.assertEqual(
            [item["label"] for item in listing["git"]],
            ["Pin Git here", "Follow Git browsing"],
        )
        self.assertEqual(
            [item["label"] for item in listing["git"]],
            [item["label"] for item in tree["git"]],
        )

    def test_a_listing_file_row_scopes_the_pin_to_that_file(self):
        result = self._ask(
            row=_preview_row("web/api.py", kind="file", entry_kind="file"),
            click="Pin Git here",
        )
        self.assertEqual(
            result["pins"], [{"index": 0, "path": "web/api.py", "kind": "file"}]
        )

    def test_the_pinned_listing_row_offers_the_unpin_and_never_probes_for_it(self):
        result = self._ask(
            row=_preview_row("web"),
            pinnedPath="web",
            click="Unpin Git",
        )
        self.assertEqual([item["label"] for item in result["git"]][0], "Unpin Git")
        self.assertEqual(result["pins"], [{"index": 0, "path": None, "kind": None}])
        # Unpinning never needs the path to still resolve.
        self.assertEqual(result["probes"], [])

    def test_a_pin_made_elsewhere_repins_from_the_listing_in_one_write(self):
        result = self._ask(
            row=_preview_row("docs"),
            pinnedPath="web",
            click="Pin Git here",
        )
        self.assertEqual(
            result["pins"], [{"index": 0, "path": "docs", "kind": "dir"}]
        )

    def test_a_listing_row_outside_a_worktree_is_disabled_not_dropped(self):
        result = self._ask(row=_preview_row("outside"), worktree=False)
        pin = result["git"][0]
        self.assertEqual(pin["label"], "Pin Git here")
        self.assertTrue(pin["disabled"])
        self.assertIn("not inside a Git worktree", pin["title"])

    def test_follow_from_the_listing_toggles_the_pane_flag(self):
        result = self._ask(row=_preview_row("web"), click="Follow Git browsing")
        self.assertEqual(result["follows"], [0])
        result = self._ask(
            row=_preview_row("web"),
            following=True,
            panePath="web",
            click="Unfollow Git browsing",
        )
        self.assertEqual(result["follows"], [0])

    def test_follow_on_a_listing_file_row_scopes_to_that_file(self):
        """The row the reader pointed at, not the folder the listing shows.

        The pin entry has always named the row; Follow read the pane's derived
        browsing scope instead, so choosing it on `web/api.py` scoped Git to
        `web`.
        """
        result = self._ask(
            row=_preview_row("web/api.py", kind="file", entry_kind="file"),
            panePath="web",
            click="Follow Git browsing",
        )
        self.assertEqual(
            result["browseTargets"],
            [{"index": 0, "path": "web/api.py", "kind": "file"}],
        )
        self.assertEqual(result["follows"], [0])

    def test_following_elsewhere_moves_follow_here_in_one_write(self):
        """The pin button's rule: only the followed row offers to unfollow."""
        result = self._ask(
            row=_preview_row("web/api.py", kind="file", entry_kind="file"),
            panePath="web",
            following=True,
            click="Follow Git browsing",
        )
        self.assertEqual(
            result["browseTargets"],
            [{"index": 0, "path": "web/api.py", "kind": "file"}],
        )
        # Already following: naming the row is the whole write. Toggling would
        # switch Follow off.
        self.assertEqual(result["follows"], [])

    def test_the_followed_row_is_the_only_one_that_offers_to_unfollow(self):
        followed = self._ask(
            row=_preview_row("web"), panePath="web", following=True
        )
        other = self._ask(
            row=_preview_row("docs"), panePath="web", following=True
        )
        self.assertEqual([item["label"] for item in followed["git"]][1],
                         "Unfollow Git browsing")
        self.assertEqual([item["label"] for item in other["git"]][1],
                         "Follow Git browsing")

    def test_a_multi_entry_selection_offers_neither_pin_nor_follow(self):
        """Both are one exact path, so several rows name neither."""
        result = self._ask(
            row=_preview_row("web/api.py", kind="file", entry_kind="file"),
            panePath="web",
            selected=[
                {"path": "web/api.py", "kind": "file"},
                {"path": "web/explorer.py", "kind": "file"},
            ],
        )
        self.assertEqual([item["label"] for item in result["git"]], [])

    def test_one_highlighted_row_is_itself_a_browsing_act(self):
        """A single Ctrl-click names one exact path, so the scope follows it.

        This is what the Graph header's own Follow button reads: it has no row
        of its own, so "the highlighted file" has to reach it through the
        pane's browsed scope.
        """
        result = self._ask(
            row=_preview_row("web/api.py", kind="file", entry_kind="file"),
            panePath="web",
            selected=[{"path": "web/api.py", "kind": "file"}],
        )
        self.assertEqual(
            result["browseTargets"][0],
            {"index": 0, "path": "web/api.py", "kind": "file"},
        )

    def test_blank_space_in_the_listing_names_the_folder_it_is_showing(self):
        # The tree's blank space is the explorer root; the listing's is
        # wherever it has been browsed to.
        result = self._ask(blank="preview", panePath="web/static", click="Pin Git here")
        self.assertEqual(
            result["pins"], [{"index": 0, "path": "web/static", "kind": "dir"}]
        )

    def test_blank_space_in_the_tree_still_names_the_root(self):
        result = self._ask(blank="tree", panePath="web/static", click="Pin Git here")
        self.assertEqual(result["pins"], [{"index": 0, "path": "", "kind": "dir"}])

    # ── What the menu costs to open ────────────────────────────────────────
    #
    # The entry has to know whether a prospective pin is inside a worktree, and
    # the menu does not open until it does. Asking the server every time meant
    # a right-click on a remote pane highlighted the row and then showed
    # nothing for the length of a pooled `rev-parse` + `git status`. The
    # sidebar's loaded repository already answers it for the ordinary case.

    def test_a_row_inside_the_panes_own_repository_opens_without_a_request(self):
        # repo_path '' is the server's word for "this worktree contains the
        # explorer root", so every path under the root is inside it.
        result = self._ask(row=_tree_row("web"), loadedRepoPath="")
        self.assertEqual(result["probes"], [])
        self.assertEqual([item["label"] for item in result["git"]][0], "Pin Git here")
        self.assertFalse(result["git"][0]["disabled"])

    def test_a_file_row_inside_the_panes_own_repository_also_skips_the_request(self):
        result = self._ask(
            row=_preview_row("web/api.py", kind="file", entry_kind="file"),
            loadedRepoPath="",
        )
        self.assertEqual(result["probes"], [])
        self.assertFalse(result["git"][0]["disabled"])

    def test_a_repository_below_the_explorer_root_still_asks(self):
        # The loaded worktree is a subdirectory, so a sibling folder may be in
        # no worktree at all and the pane's model cannot say.
        result = self._ask(row=_tree_row("docs"), loadedRepoPath="web")
        self.assertEqual(len(result["probes"]), 1)

    def test_a_pane_with_no_loaded_repository_still_asks(self):
        result = self._ask(row=_tree_row("web"))
        self.assertEqual(len(result["probes"]), 1)

    def test_a_row_under_dot_git_is_never_answered_optimistically(self):
        # `rev-parse` inside the repository's own git directory is not in a
        # worktree, so the fast path must not claim it is.
        for path in (".git", ".git/hooks", "web/.git/config"):
            with self.subTest(path=path):
                result = self._ask(row=_tree_row(path), loadedRepoPath="", worktree=False)
                self.assertEqual(len(result["probes"]), 1)
                self.assertTrue(result["git"][0]["disabled"])

    def test_a_path_merely_containing_dot_git_is_not_treated_as_the_git_dir(self):
        # Segment-wise, not substring: `web/.gitignore` is an ordinary file.
        result = self._ask(
            row=_preview_row("web/.gitignore", kind="file", entry_kind="file"),
            loadedRepoPath="",
        )
        self.assertEqual(result["probes"], [])

    def test_a_superseded_right_click_opens_no_menu_of_its_own(self):
        # Two overlapping gestures where the first probe resolves last. Only
        # the gesture the reader made most recently may put a menu on screen --
        # otherwise the stale one lands over it, and over its invoker.
        result = self._ask(row=_tree_row("web"), doubleGesture=True)
        self.assertEqual(len(result["probes"]), 2)
        self.assertEqual(result["menuCount"], 1)

    def test_an_entry_clicked_after_the_slot_changed_hands_writes_nothing(self):
        # Guardrail 4: the menu can outlive the slot it was opened over, and
        # these entries write a scope onto the pane the gesture was made on.
        for label in ("Pin Git here", "Follow Git browsing"):
            with self.subTest(entry=label):
                result = self._ask(
                    row=_tree_row("web"),
                    click=label,
                    replacePaneBeforeClick=True,
                )
                self.assertEqual(result["pins"], [])
                self.assertEqual(result["follows"], [])


# The page the file render draws into. Every element is the same permissive
# stub, because what is asserted here is the request the render asks for, not
# the markup it writes; the collaborators the render reaches for that belong to
# modules outside this set are no-ops for the same reason.
RENDER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const spec = JSON.parse(process.argv[3]);
const modules = JSON.parse(process.argv[2]);
const requests = [];

function fakeElement(id) {
    const node = {
        id,
        innerHTML: '',
        textContent: '',
        value: '',
        hidden: false,
        scrollTop: 0,
        scrollLeft: 0,
        scrollHeight: 0,
        scrollWidth: 0,
        clientHeight: 0,
        clientWidth: 0,
        children: [],
        firstChild: null,
        parentNode: null,
        style: { setProperty() {}, removeProperty() {} },
        dataset: {},
        classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
        appendChild() {},
        removeChild() {},
        remove() {},
        replaceChildren() {},
        insertAdjacentHTML() {},
        setAttribute() {},
        removeAttribute() {},
        getAttribute: () => null,
        hasAttribute: () => false,
        addEventListener() {},
        removeEventListener() {},
        focus() {},
        blur() {},
        scrollTo() {},
        getBoundingClientRect: () => ({
            top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100
        }),
        querySelector: () => null,
        querySelectorAll: () => [],
        closest: () => null,
        matches: () => false
    };
    node.cloneNode = () => fakeElement(id);
    return node;
}

const elements = new Map();
const sandbox = {
    console,
    URL,
    URLSearchParams,
    encodeURIComponent,
    document: {
        activeElement: null,
        getElementById: id => {
            if (!elements.has(id)) elements.set(id, fakeElement(id));
            return elements.get(id);
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        createElement: () => fakeElement('created'),
        createDocumentFragment: () => fakeElement('fragment'),
        addEventListener() {},
        body: {
            dataset: {},
            addEventListener() {},
            classList: { add() {}, remove() {} }
        }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame() {},
    performance: { now: () => 0 },
    CSS: { highlights: { set() {}, delete() {} } },
    terminals: [],
    sessionIds: [],
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    fetch: async url => {
        requests.push(String(url));
        return {
            ok: true,
            json: async () => ({
                anchor_path: '', revision: 'rev-1',
                git: { available: true, repo_name: 'repo', branch: 'main' },
                changes: [], commits: []
            })
        };
    }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
modules.forEach(path => vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox));

/* Everything above is the real module; what is left belongs to terminals.js,
   which this harness cannot load. None of it can decide whether the Git scope
   moved, so a no-op is the whole of its contribution here. A name that is
   still missing after the render surfaces as a ReferenceError rather than
   being swallowed, so the list cannot quietly rot. */
[
    'notePanePresentationChanged', 'applyExplorerTheme', 'explorerPaneLiveTheme',
    'isExplorerSession', 'showGridVibeNotice', 'openGenericConfirmModal'
].forEach(name => {
    if (typeof sandbox[name] === 'undefined') sandbox[name] = () => undefined;
});

const pane = {
    _session: { startup_mode: 'explorer', explorer_root_directory: '/repo' },
    _attached: true,
    _explorerMode: spec.paneMode || 'directory',
    _explorerPath: spec.panePath || '',
    _explorerFilePath: spec.paneFilePath || '',
    _explorerTabs: [],
    _explorerGitSidebarOpen: spec.sidebarOpen !== false,
    _explorerGitFollowBrowsing: Boolean(spec.following),
    // A pane whose sidebar is already showing a model for `anchor`.
    _explorerGitRepoLoaded: true,
    _explorerGitRepo: { anchor_path: spec.anchor || '', git: { available: true } },
    _explorerGitAnchorPath: spec.anchor || '',
    _explorerGitAnchorKind: spec.anchorKind || 'dir'
};
if (spec.pinnedPath !== null && spec.pinnedPath !== undefined) {
    pane._explorerGitPinnedPath = spec.pinnedPath;
    pane._explorerGitPinKind = spec.pinnedKind || 'dir';
}
sandbox.terminals[0] = pane;
sandbox.sessionIds[0] = 'sess-0';

let panelRenders = 0;
sandbox.renderExplorerGitPanels = () => { panelRenders += 1; };

const rendered = sandbox.renderExplorerFile(0, {
    path: spec.openPath,
    name: spec.openPath.split('/').pop(),
    content: 'x = 1',
    language: 'python'
}, {});

// The load is fire-and-forget from the render; let its request settle.
setTimeout(() => {
    process.stdout.write(JSON.stringify({
        rendered: Boolean(rendered),
        requests: requests.filter(url => url.includes('/git/')),
        panelRenders
    }));
}, 0);
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer Git scope tests")
class ExplorerGitScopeLoadOnOpenTestCase(unittest.TestCase):
    """Opening a file is a scope change, and is answered like one."""

    def _render(self, **spec):
        spec.setdefault("pinnedPath", None)
        spec.setdefault("openPath", "web/api.py")
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "render.js"
            script_path.write_text(RENDER_HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    json.dumps([str(path) for path in RENDER_MODULES]),
                    json.dumps(spec),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        result = json.loads(completed.stdout)
        self.assertTrue(result["rendered"], "the file did not render")
        return result

    def test_opening_a_file_under_follow_requests_that_file_s_scope_at_once(self):
        # The reported defect: the Graph updated on the watcher's interval
        # where a folder switch updated immediately.
        result = self._render(following=True, panePath="web", anchor="web")
        self.assertEqual(
            result["requests"],
            ["/api/explorer/sess-0/git/repo?scope=path&path=web%2Fapi.py&kind=file"],
        )

    def test_switching_between_two_files_under_follow_rescopes_each_time(self):
        # The pane is already showing one file and its scope; opening the next
        # one is another scope change, not a repaint of the same one.
        result = self._render(
            following=True,
            paneMode="file",
            paneFilePath="web/explorer.py",
            anchor="web/explorer.py",
            anchorKind="file",
            openPath="web/api.py",
        )
        self.assertEqual(
            result["requests"],
            ["/api/explorer/sess-0/git/repo?scope=path&path=web%2Fapi.py&kind=file"],
        )

    def test_reopening_the_file_the_scope_is_already_on_asks_for_nothing(self):
        result = self._render(
            following=True,
            paneMode="file",
            paneFilePath="web/api.py",
            anchor="web/api.py",
            anchorKind="file",
            openPath="web/api.py",
        )
        self.assertEqual(result["requests"], [])
        # And no render of the Git panel either: it carries the commit-message
        # textarea, and a render nobody asked for takes the caret out of it.
        self.assertEqual(result["panelRenders"], 0)

    def test_opening_a_file_with_follow_off_leaves_a_fixed_pin_alone(self):
        # Follow off is the whole point of a pin: browsing must not move it,
        # and must not cost a request or a panel render.
        result = self._render(
            pinnedPath="docs",
            anchor="docs",
            openPath="web/api.py",
        )
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["panelRenders"], 0)

    def test_a_closed_git_sidebar_never_loads_on_a_file_open(self):
        result = self._render(following=True, sidebarOpen=False, anchor="web")
        self.assertEqual(result["requests"], [])


if __name__ == "__main__":
    unittest.main()
