"""The Files tree marks the folder the Git scope is pinned to.

A pin captures the folder that was being browsed and then freezes there, so
navigating away used to leave nothing on screen saying *where* it was: the
Graph header's pin button reported only that a pin existed. A restored
workspace that came back in some other folder therefore read as a pin that had
been lost.

The marker is derived, never persisted -- it is ``_explorerGitPinnedPath``
answered per row -- and it moves by painting exactly the two rows that disagree
with the pin, because rebuilding ``[data-explorer-tree-body]`` empties the
tree's scroller and the capture-phase scroll listener would persist that
clamped 0 as the reader's position.

Executed in Node against the real modules: the predicate, the row markup and
the paint are run, not read. Only the markup hooks themselves
(``explorer-tree-pin-mark``, the title) are asserted as text, which is the
documented exception for rendered markup.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TREE_JS = STATIC_JS / "explorer-tree.js"
PIN_JS = STATIC_JS / "explorer-git-pin.js"

NODE = shutil.which("node")

# Two root-level folders and a file, plus a nested folder, so "exactly one row"
# is an observation over a tree that has somewhere else to put the marker.
FIXTURE_CHILDREN = {
    "": [
        {"type": "directory", "path": "docs", "name": "docs"},
        {"type": "directory", "path": "web", "name": "web"},
        {"type": "file", "path": "readme.md", "name": "readme.md"},
    ],
    "web": [{"type": "directory", "path": "web/static", "name": "static"}],
    "web/static": [],
    "docs": [],
}

# The tree's own globals come from explorer-viewer.js and terminal-icons.js on
# the page; the marker only needs them to render something identifiable.
SANDBOX = r"""
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
        navigator: {},
        setTimeout,
        clearTimeout,
        requestAnimationFrame: () => 0,
        terminals: [],
        sessionIds: [],
        EXPLORER_TREE_INDENT_PX: 12,
        EXPLORER_GIT_PIN_ICON: '<svg class="explorer-btn-icon" data-icon="pin"></svg>',
        EXPLORER_OPEN_FOLDER_ICON: '<svg class="explorer-btn-icon"></svg>',
        EXPLORER_OPEN_TAB_ICON: '<svg class="explorer-btn-icon"></svg>',
        EXPLORER_FOLDER_ICON: '<span class="explorer-icon"></span>',
        UI_CHEVRON_DOWN_ICON: '<svg></svg>',
        UI_CHEVRON_RIGHT_ICON: '<svg></svg>',
        explorerFileTypeIconHtml: () => '<span class="explorer-icon"></span>',
        explorerGitStatusLabel: () => '',
        explorerGitBadgeHtml: () => '',
        escHtml: value => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    };
    sandbox.globalThis = sandbox;
    // The tree reaches its policy module through `window`, exactly as the
    // other explorer-git-* adapters do.
    sandbox.window = sandbox;
    return sandbox;
}

function loadSandbox(pinJs, treeJs) {
    const sandbox = makeSandbox();
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(pinJs, 'utf8'), sandbox);
    vm.runInContext(fs.readFileSync(treeJs, 'utf8'), sandbox);
    return sandbox;
}

/* The tree state is gated on `instanceof Set`/`Map`, which fails across vm
   realms, so the pane is assembled inside the context. */
function installPane(sandbox, fixture, expanded, pinned) {
    sandbox.__fixture = { fixture, expanded, pinned };
    vm.runInContext(`
        sessionIds[0] = 'sess-0';
        terminals[0] = {
            _explorerTreeExpanded: new Set(__fixture.expanded),
            _explorerTreeChildren: new Map(Object.entries(__fixture.fixture)),
            _explorerTreeErrors: new Map(),
            _explorerTreeLoading: new Set()
        };
        if (__fixture.pinned !== null) {
            terminals[0]._explorerGitPinnedPath = __fixture.pinned;
        }
    `, sandbox);
    return sandbox;
}
"""

# A tree panel with just enough behaviour for the paint: rows keep their own
# identity and their own child list, so "were these the same nodes afterwards"
# and "which children moved" are observations rather than inferences. The
# panel's scrollTop is a plain field -- nothing in the paint may touch it.
PAINT_HARNESS = SANDBOX + r"""
let nodeSeq = 0;

function makeMark() {
    return {
        nodeId: ++nodeSeq,
        className: 'explorer-tree-pin-mark',
        _host: null,
        remove() {
            const at = this._host ? this._host.indexOf(this) : -1;
            if (at !== -1) { this._host.splice(at, 1); }
        }
    };
}

function makeOpenControl(className, children) {
    return {
        nodeId: ++nodeSeq,
        className,
        insertAdjacentHTML(position, markup) {
            if (position !== 'beforebegin') { throw new Error('unexpected ' + position); }
            if (!markup.includes('explorer-tree-pin-mark')) { throw new Error('not a mark'); }
            const mark = makeMark();
            mark._host = children;
            children.splice(children.indexOf(this), 0, mark);
        }
    };
}

function makeRow(path, kind, marked) {
    const children = [];
    const row = {
        nodeId: ++nodeSeq,
        dataset: { explorerContextPath: path },
        children,
        insertAdjacentHTML(position, markup) {
            if (position !== 'beforeend') { throw new Error('unexpected ' + position); }
            if (!markup.includes('explorer-tree-pin-mark')) { throw new Error('not a mark'); }
            const mark = makeMark();
            mark._host = children;
            children.push(mark);
        },
        querySelector(selector) {
            const wanted = selector.split(',').map(part => part.trim().replace(/^\./, ''));
            return children.find(child => wanted.includes(child.className)) || null;
        }
    };
    if (marked) {
        const mark = makeMark();
        mark._host = children;
        children.push(mark);
    }
    children.push(makeOpenControl(
        kind === 'directory' ? 'explorer-open-folder-btn' : 'explorer-open-tab-btn',
        children
    ));
    return row;
}

const pinned = process.argv[4] === '__none__' ? null : process.argv[4];
const rowSpec = JSON.parse(process.argv[5]);

const sandbox = loadSandbox(process.argv[2], process.argv[3]);
installPane(sandbox, {}, [], pinned);

const rows = rowSpec.map(spec => makeRow(spec.path, spec.kind, Boolean(spec.marked)));
const rootMark = { nodeId: ++nodeSeq, hidden: true };
const panel = {
    scrollTop: 412,
    scrollLeft: 7,
    querySelector: selector => (
        selector === '[data-explorer-tree-pin-root]' ? rootMark : null
    ),
    querySelectorAll: selector => (selector === '.explorer-tree-row' ? rows : [])
};
sandbox.document.getElementById = id => (id === 'explorer-tree-panel-0' ? panel : null);

const before = rows.map(row => row.nodeId);
sandbox.applyExplorerTreePinMark(0);

const hasMark = row => row.children.some(c => c.className === 'explorer-tree-pin-mark');

process.stdout.write(JSON.stringify({
    rowIdsUnchanged: JSON.stringify(before) === JSON.stringify(rows.map(row => row.nodeId)),
    marked: rows.filter(hasMark).map(row => row.dataset.explorerContextPath),
    // The marker's position inside every row that has one: it belongs
    // immediately left of that row's open control.
    markBeforeOpen: rows.every(row => {
        const at = row.children.findIndex(c => c.className === 'explorer-tree-pin-mark');
        if (at === -1) { return true; }
        const open = row.children.findIndex(
            c => String(c.className).startsWith('explorer-open-')
        );
        return open === -1 || at < open;
    }),
    rootHidden: rootMark.hidden,
    scrollTop: panel.scrollTop,
    scrollLeft: panel.scrollLeft
}));
"""

# The rendered tree body, straight out of the real row builder.
RENDER_HARNESS = SANDBOX + r"""
const pinned = process.argv[5] === '__none__' ? null : process.argv[5];
const sandbox = loadSandbox(process.argv[2], process.argv[3]);
installPane(sandbox, JSON.parse(process.argv[4]), ['web'], pinned);

const html = sandbox.renderExplorerTreeNodes(sandbox.terminals[0], '', 0);
// Which rows carry the marker: `split` hands back the markup between one row's
// path attribute and the next one's, so each slice is that row, and a marker
// landing on the wrong row is reported as that row's path.
const rows = html.split('data-explorer-context-path="').slice(1).map(part => ({
    path: part.slice(0, part.indexOf('"')),
    body: part
}));
const marked = rows
    .filter(row => row.body.includes('explorer-tree-pin-mark'))
    .map(row => row.path);

process.stdout.write(JSON.stringify({
    html,
    marked,
    // Within the row that has it, the marker precedes that row's own open
    // control -- compared per row, since every row has an open control.
    markBeforeOpen: rows.every(row => {
        const at = row.body.indexOf('explorer-tree-pin-mark');
        if (at === -1) { return true; }
        const open = row.body.search(/explorer-open-(folder|tab)-btn/);
        return open === -1 || at < open;
    }),
    markerCount: (html.match(/explorer-tree-pin-mark/g) || []).length
}));
"""

PREDICATE_HARNESS = r"""
const api = require(process.argv[2]);
const cases = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(
    cases.map(([pinned, path]) => api.explorerGitPathIsPinned(pinned, path))
));
"""


def _run(script, *args):
    with TemporaryDirectory() as script_dir:
        path = Path(script_dir) / "harness.js"
        path.write_text(script, encoding="utf-8")
        return subprocess.run(
            [NODE, str(path), *args], capture_output=True, text=True, check=False
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerGitPinPredicateTestCase(unittest.TestCase):
    """One predicate answers the marker, so its edges are the marker's edges."""

    def _ask(self, cases):
        completed = _run(PREDICATE_HARNESS, str(PIN_JS), json.dumps(cases))
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_pin_matches_its_own_path_and_nothing_else(self):
        self.assertEqual(
            self._ask([["web/static", "web/static"], ["web/static", "docs"]]),
            [True, False],
        )

    def test_an_ancestor_pin_is_not_a_pin_here(self):
        # Exact equality, never a prefix match: a pin on web/ is a pin on web/,
        # and marking web/static too would point at a scope nobody chose.
        self.assertEqual(
            self._ask([["web", "web/static"], ["web/static", "web"]]),
            [False, False],
        )
        # A prefix that is not a path boundary must not match either.
        self.assertEqual(self._ask([["web", "website"]]), [False])

    def test_the_empty_string_is_the_root_and_is_a_real_pin(self):
        self.assertEqual(self._ask([["", ""], ["", "docs"]]), [True, False])

    def test_no_pin_is_the_field_s_type_not_its_truthiness(self):
        # `''` is falsy and is a pin; `null`/`undefined` are the absence of one.
        self.assertEqual(self._ask([[None, ""], [None, "docs"]]), [False, False])


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerTreePinMarkRenderTestCase(unittest.TestCase):
    """The rendered tree: which row wears the marker, and how many do."""

    def _render(self, pinned):
        completed = _run(
            RENDER_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            json.dumps(FIXTURE_CHILDREN),
            "__none__" if pinned is None else pinned,
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_marker_lands_on_exactly_the_pinned_row(self):
        result = self._render("web")
        self.assertEqual(result["marked"], ["web"])
        self.assertEqual(result["markerCount"], 1)

    def test_a_nested_pin_is_marked_on_its_own_row(self):
        result = self._render("web/static")
        self.assertEqual(result["marked"], ["web/static"])
        self.assertEqual(result["markerCount"], 1)

    def test_no_pin_marks_no_row(self):
        result = self._render(None)
        self.assertEqual(result["marked"], [])
        self.assertEqual(result["markerCount"], 0)

    def test_a_root_pin_marks_no_row_because_the_body_lists_the_root_s_children(self):
        # Honest rather than approximate: the tree body has no row for the
        # explorer root, so a root pin is reported by the FILES head's marker
        # (see the paint tests) and never by a nearby row standing in for it.
        result = self._render("")
        self.assertEqual(result["marked"], [])
        self.assertEqual(result["markerCount"], 0)

    def test_a_pin_on_a_folder_that_is_not_rendered_marks_nothing(self):
        # A collapsed ancestor, or a path outside the tree entirely. The Graph
        # header still names the scope; the tree does not auto-expand to it.
        self.assertEqual(self._render("web/static/js")["marked"], [])
        self.assertEqual(self._render("elsewhere")["marked"], [])

    def test_the_marker_is_a_span_carrying_the_documented_hooks(self):
        # Markup hooks are the documented source-assertion exception: the class
        # the CSS and the paint both key on, and the title that says what the
        # marker means. A <span>, because the tree *reports* the pin -- moving
        # it is the Graph header button's job.
        html = self._render("web")["html"]
        self.assertIn('class="explorer-tree-pin-mark"', html)
        self.assertIn('title="Git scope pinned here"', html)
        self.assertIn('aria-label="Git scope pinned here"', html)
        self.assertIn('role="img"', html)
        marker_at = html.index("explorer-tree-pin-mark")
        self.assertEqual(html.rfind("<span", 0, marker_at), html.rfind("<", 0, marker_at))

    def test_the_marker_sits_immediately_left_of_the_open_control(self):
        # Per row, not per document: every row carries an open control, so the
        # comparison is only meaningful inside the row that has the marker.
        self.assertTrue(self._render("web")["markBeforeOpen"])
        self.assertTrue(self._render("readme.md")["markBeforeOpen"])


@unittest.skipUnless(NODE, "Node.js is required for explorer tree pin mark tests")
class ExplorerTreePinMarkPaintTestCase(unittest.TestCase):
    """The move is a paint: two rows change, the tree body does not."""

    ROWS = [
        {"path": "docs", "kind": "directory"},
        {"path": "web", "kind": "directory", "marked": True},
        {"path": "readme.md", "kind": "file"},
    ]

    def _paint(self, pinned, rows=None):
        completed = _run(
            PAINT_HARNESS,
            str(PIN_JS),
            str(TREE_JS),
            "__none__" if pinned is None else pinned,
            json.dumps(self.ROWS if rows is None else rows),
        )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_mark_moves_between_two_rows_without_rebuilding_them(self):
        result = self._paint("docs")
        self.assertEqual(result["marked"], ["docs"])
        # The rows the reader is looking at are the same nodes: a rebuild would
        # have replaced them, and taken the scroll offset with them.
        self.assertTrue(result["rowIdsUnchanged"])
        self.assertEqual(result["scrollTop"], 412)
        self.assertEqual(result["scrollLeft"], 7)

    def test_a_painted_mark_lands_left_of_the_row_s_open_control(self):
        self.assertTrue(self._paint("docs")["markBeforeOpen"])

    def test_clearing_the_pin_takes_the_mark_off_and_adds_none(self):
        result = self._paint(None)
        self.assertEqual(result["marked"], [])
        self.assertTrue(result["rowIdsUnchanged"])
        self.assertEqual(result["scrollTop"], 412)

    def test_a_file_row_can_carry_the_mark_beside_its_open_in_tab_button(self):
        result = self._paint("readme.md")
        self.assertEqual(result["marked"], ["readme.md"])
        self.assertTrue(result["markBeforeOpen"])

    def test_painting_the_pin_that_is_already_shown_changes_nothing(self):
        # Idempotent: a freshly rendered tree already carries the marker in its
        # row markup, so the paint that follows a render must be a no-op.
        result = self._paint("web")
        self.assertEqual(result["marked"], ["web"])
        self.assertTrue(result["rowIdsUnchanged"])

    def test_the_head_s_root_marker_is_shown_only_for_a_root_pin(self):
        # The tree body lists the root's children, so the FILES head stands in
        # for the explorer root. Toggled by attribute, never added and removed:
        # rebuilding the head would drop the caret out of the name filter.
        self.assertFalse(self._paint("")["rootHidden"])
        self.assertTrue(self._paint("web")["rootHidden"])
        self.assertTrue(self._paint(None)["rootHidden"])

    def test_a_root_pin_marks_the_head_and_no_row(self):
        result = self._paint("")
        self.assertEqual(result["marked"], [])
        self.assertFalse(result["rootHidden"])


if __name__ == "__main__":
    unittest.main()
