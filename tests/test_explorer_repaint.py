"""What a Source re-render is allowed to skip, and what it has to touch.

Two halves, both executed rather than read.

``explorer-repaint.js`` is DOM-free and require()-able, so the decisions
themselves run in Node: which rows a decoration change moves, when a repaint is
cheaper than a rebuild, and which run of lines a moved draft actually replaced.

The adapters are then run against a DOM stub, because the rules only pay off if
the DOM agrees with them. The contracts:

* a render that would paint what is already on screen paints nothing — which is
  what stops every file open rendering its rows twice (``renderExplorerFile``
  builds them, ``applyExplorerSearch`` used to build them again);
* stepping from one find match to the next repaints exactly the two rows whose
  marks moved, and every other row survives as the same node — so the change
  marks, their marker buttons and the reader's selection survive with it;
* a real change (content, language, the fold set) still rebuilds;
* the in-place editor's underlay replaces the rows a keystroke moved instead of
  re-tokenizing the whole draft on every animation frame, and it renumbers the
  rows a new line shifted.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
REPAINT_JS = STATIC_JS / "explorer-repaint.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
# The Source render reads the active tab's Markdown folds, and the tab records
# live in explorer-tabs.js — the page loads the pair together, so does this.
TABS_JS = STATIC_JS / "explorer-tabs.js"
OVERLAY_JS = STATIC_JS / "explorer-edit-overlay.js"

NODE = shutil.which("node")

# A Source panel that behaves like the one on the page for the three things the
# repaint path asks of it: assigning innerHTML replaces the rows, the lines
# block is reachable as a direct child, and a row's code cell can be rewritten
# on its own. Every row and cell keeps its own write counter and its own
# identity, so "which rows did this touch" and "did these rows survive" are
# observations rather than inferences.
DOM_STUB = """
const ROW_RE = /<div class="explorer-source-line" data-explorer-line="(\\d+)">([\\s\\S]*?)<\\/div>/g;
const CODE_RE = /<code class="[^"]*">([\\s\\S]*?)<\\/code>/;

let nodeSeq = 0;

function makeRow(line, body) {
    const cell = {
        nodeId: ++nodeSeq,
        writes: 0,
        _html: (body.match(CODE_RE) || ['', ''])[1],
        get innerHTML() { return this._html; },
        set innerHTML(value) { this._html = value; this.writes += 1; }
    };
    const gutter = { textContent: String(line) };
    return {
        nodeId: ++nodeSeq,
        dataset: { explorerLine: String(line) },
        cell,
        firstElementChild: gutter,
        classList: {
            add() {}, remove() {}, contains: () => false, toggle: () => false
        },
        _host: null,
        remove() {
            const at = this._host ? this._host.indexOf(this) : -1;
            if (at !== -1) { this._host.splice(at, 1); }
        },
        querySelector: selector => (selector === '> code' ? cell : null)
    };
}

function parseRows(html) {
    const rows = [];
    ROW_RE.lastIndex = 0;
    let match = ROW_RE.exec(html);
    while (match) {
        const row = makeRow(Number(match[1]), match[2]);
        row._host = rows;
        rows.push(row);
        match = ROW_RE.exec(html);
    }
    return rows;
}

function makeLinesBlock(html) {
    const rows = parseRows(html);
    const block = {
        nodeId: ++nodeSeq,
        dataset: {},
        rows,
        children: rows,
        querySelector(selector) {
            const found = selector.match(/data-explorer-line="(\\d+)"/);
            if (!found) { return null; }
            const row = rows.find(entry => entry.dataset.explorerLine === found[1]);
            if (!row) { return null; }
            return selector.trim().endsWith('> code') ? row.cell : row;
        },
        querySelectorAll: () => [],
        insertAdjacentHTML(position, markup) {
            parseRows(markup).forEach(row => {
                row._host = rows;
                rows.push(row);
            });
        },
        insertBefore(row, anchor) {
            const at = anchor ? rows.indexOf(anchor) : rows.length;
            row._host = rows;
            rows.splice(at === -1 ? rows.length : at, 0, row);
        }
    };
    return block;
}

function makeSourcePanel(id) {
    let block = null;
    const panel = {
        id,
        writes: 0,
        raw: '',
        dataset: {},
        style: { setProperty() {}, removeProperty() {} },
        classList: {
            add() {}, remove() {}, contains: () => false, toggle: () => false
        },
        addEventListener() {},
        setAttribute() {},
        appendChild() {},
        get innerHTML() { return panel.raw; },
        set innerHTML(value) {
            panel.writes += 1;
            panel.raw = value;
            block = value.includes('explorer-source-lines') ? makeLinesBlock(value) : null;
        },
        get block() { return block; },
        querySelector(selector) {
            if (selector === ':scope > .explorer-source-lines'
                || selector === '.explorer-source-lines') {
                return block;
            }
            return block ? block.querySelector(selector) : null;
        },
        querySelectorAll: () => []
    };
    return panel;
}

function makeSandbox(nodes) {
    const sandbox = {
        console,
        document: {
            getElementById: id => nodes[id] || null,
            querySelector: selector => nodes[selector] || null,
            querySelectorAll: () => [],
            addEventListener() {},
            createElement: () => ({
                _html: '',
                get innerHTML() { return this._html; },
                set innerHTML(value) { this._html = value; this.children = parseRows(value); },
                children: []
            }),
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
        applyExplorerChangeMarks: () => {},
        escHtml: value => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    };
    sandbox.globalThis = sandbox;
    return sandbox;
}
"""


class NodeHarnessMixin:
    def _run_node(self, harness: str, *args: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        if completed.returncode != 0:
            self.fail("node harness failed:" + chr(10) + completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])


@unittest.skipUnless(NODE, "Node.js is required for explorer repaint tests")
class RepaintPolicyTestCase(NodeHarnessMixin, unittest.TestCase):
    """The decisions, executed on their own."""

    def _policy(self, body: str):
        return self._run_node(
            "const repaint = require(" + json.dumps(str(REPAINT_JS)) + ");\n"
            "const emit = value => console.log(JSON.stringify(value));\n"
            + body
        )

    def test_decorations_are_keyed_per_line_and_relative_to_it(self):
        """The same marks on the same line must key the same wherever the line
        sits, or a match near the end of a file would repaint the whole file.
        """
        keys = self._policy(
            "const records = ["
            "  { number: 1, start: 0, text: 'alpha' },"
            "  { number: 2, start: 6, text: 'alpha' },"
            "  { number: 3, start: 12, text: 'beta' }"
            "];"
            "const map = repaint.decorationMap(records, ["
            "  { start: 0, end: 5 }, { start: 6, end: 11 }"
            "]);"
            "emit({"
            "  lines: Array.from(map.keys()),"
            "  first: map.get(1),"
            "  second: map.get(2),"
            "  identical: map.get(1) === map.get(2)"
            "});"
        )

        self.assertEqual(keys["lines"], [1, 2])
        self.assertTrue(keys["identical"], "identical marks must key identically")

    def test_the_active_match_is_part_of_a_line_key(self):
        """Stepping matches changes no text at all — only which one is active —
        so if `active` were not keyed, Enter would repaint nothing.
        """
        stepped = self._policy(
            "const records = ["
            "  { number: 1, start: 0, text: 'hit' },"
            "  { number: 2, start: 4, text: 'hit' },"
            "  { number: 3, start: 8, text: 'quiet' }"
            "];"
            "const before = repaint.decorationMap(records, ["
            "  { start: 0, end: 3, active: true }, { start: 4, end: 7, active: false }"
            "]);"
            "const after = repaint.decorationMap(records, ["
            "  { start: 0, end: 3, active: false }, { start: 4, end: 7, active: true }"
            "]);"
            "emit({ moved: repaint.decorationDelta(before, after) });"
        )

        self.assertEqual(stepped["moved"], [1, 2])

    def test_a_repaint_is_skipped_decorated_or_refused_on_its_merits(self):
        verdicts = self._policy(
            "const plan = extra => repaint.sourceRenderPlan(Object.assign({"
            "  sameSurface: true,"
            "  contentChanged: false,"
            "  languageChanged: false,"
            "  foldsChanged: false,"
            "  previousDecorations: new Map([[4, 'a']]),"
            "  nextDecorations: new Map([[4, 'a']])"
            "}, extra || {}));"
            "const wide = new Map();"
            "for (let line = 1; line <= 900; line += 1) { wide.set(line, 'x'); }"
            "emit({"
            "  unchanged: plan(),"
            "  moved: plan({ nextDecorations: new Map([[9, 'b']]) }),"
            "  contentMoved: plan({ contentChanged: true }),"
            "  languageMoved: plan({ languageChanged: true }),"
            "  foldsMoved: plan({ foldsChanged: true }),"
            "  replacedPanel: plan({ sameSurface: false }),"
            "  buildInFlight: plan({ pending: true }),"
            "  tooWide: plan({ nextDecorations: wide })"
            "});"
        )

        # The duplicate render every file open used to pay for.
        self.assertEqual(verdicts["unchanged"]["mode"], "skip")
        # Marks left line 4 and arrived on line 9: both rows, nothing else.
        self.assertEqual(verdicts["moved"]["mode"], "decorate")
        self.assertEqual(verdicts["moved"]["lines"], [4, 9])
        for reason in ("contentMoved", "languageMoved", "foldsMoved"):
            self.assertEqual(verdicts[reason]["mode"], "full", reason)
        # A panel that is no longer the one we rendered into, and a build still
        # emitting rows with the ranges it started from, both have to rebuild —
        # skipping either is how a pane ends up empty or half-marked.
        self.assertEqual(verdicts["replacedPanel"]["mode"], "full")
        self.assertEqual(verdicts["buildInFlight"]["mode"], "full")
        # Past the ceiling one string beats hundreds of per-row parses.
        self.assertEqual(verdicts["tooWide"]["mode"], "full")

    def test_a_moved_draft_names_the_run_of_lines_it_replaced(self):
        plans = self._policy(
            "const base = ['one', 'two', 'three', 'four'];"
            "emit({"
            "  untouched: repaint.lineSplicePlan(base, base.slice()),"
            "  typed: repaint.lineSplicePlan(base, ['one', 'twoX', 'three', 'four']),"
            "  split: repaint.lineSplicePlan(base, ['one', 'tw', 'o', 'three', 'four']),"
            "  joined: repaint.lineSplicePlan(base, ['one', 'twothree', 'four']),"
            "  appended: repaint.lineSplicePlan(base, base.concat('five')),"
            "  firstPaint: repaint.lineSplicePlan(null, base),"
            "  pastedWide: repaint.lineSplicePlan("
            "    base, ['one'].concat(Array.from({ length: 400 }, (_, i) => 'x' + i))"
            "  )"
            "});"
        )

        self.assertEqual(plans["untouched"]["mode"], "none")
        self.assertEqual(
            {k: plans["typed"][k] for k in ("mode", "start", "removed", "inserted")},
            {"mode": "splice", "start": 1, "removed": 1, "inserted": 1},
        )
        # Enter mid-line: one row out, two in, and every row below shifts down.
        self.assertEqual(
            {k: plans["split"][k] for k in ("mode", "start", "removed", "inserted")},
            {"mode": "splice", "start": 1, "removed": 1, "inserted": 2},
        )
        self.assertEqual(plans["joined"]["mode"], "splice")
        self.assertEqual(plans["appended"]["mode"], "splice")
        self.assertEqual(plans["appended"]["removed"], 0)
        # No previous rows to compare against, and a change too wide to splice:
        # both fall back to the renderer the caller already had.
        self.assertEqual(plans["firstPaint"]["mode"], "full")
        self.assertEqual(plans["pastedWide"]["mode"], "full")

    def test_only_a_document_worth_chunking_is_chunked(self):
        plans = self._policy(
            "emit({"
            "  small: repaint.chunkPlan(500, { async: true }),"
            "  atFloor: repaint.chunkPlan(repaint.CHUNK_MIN_ROWS, { async: true }),"
            "  large: repaint.chunkPlan(repaint.CHUNK_MIN_ROWS * 3, { async: true }),"
            "  noFrames: repaint.chunkPlan(100000, { async: false })"
            "});"
        )

        self.assertFalse(plans["small"]["chunked"])
        self.assertTrue(plans["atFloor"]["chunked"])
        self.assertTrue(plans["large"]["chunked"])
        self.assertGreater(plans["large"]["slices"], 1)
        # Without requestAnimationFrame there is nothing to slice against, so
        # the build stays synchronous rather than never finishing.
        self.assertFalse(plans["noFrames"]["chunked"])


@unittest.skipUnless(NODE, "Node.js is required for explorer repaint tests")
class SourceRepaintAdapterTestCase(NodeHarnessMixin, unittest.TestCase):
    """renderExplorerSource() against a DOM that reports what it was told."""

    def _render(self, script: str):
        return self._run_node(
            DOM_STUB
            + """
            const fs = require('fs');
            const vm = require('vm');
            const panel = makeSourcePanel('explorer-code-0');
            const sandbox = makeSandbox({ 'explorer-code-0': panel });
            vm.createContext(sandbox);
            [process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
                vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
            });
            sandbox.window.GridVibeExplorerRepaint = sandbox.GridVibeExplorerRepaint;
            const pane = {
                _explorerMode: 'file',
                _explorerFilePath: 'notes.txt',
                _explorerFileContent: 'alpha hit' + String.fromCharCode(10)
                    + 'beta' + String.fromCharCode(10)
                    + 'gamma hit' + String.fromCharCode(10),
                _explorerFileLanguage: '',
                _explorerFilePlain: true,
                _explorerEdit: null
            };
            sandbox.terminals[0] = pane;
            const rowIds = () => panel.block.rows.map(row => row.nodeId);
            const cellWrites = () => panel.block.rows.map(row => row.cell.writes);
            """
            + script,
            str(REPAINT_JS),
            str(VIEWER_JS),
            str(TABS_JS),
        )

    def test_a_second_render_of_the_same_view_paints_nothing(self):
        """Finding 9: every file open rendered its rows, then rendered them
        again through applyExplorerSearch(). The rows on screen already say
        what the second pass would say.
        """
        result = self._render(
            "sandbox.renderExplorerSource(0);"
            "const first = rowIds();"
            "sandbox.renderExplorerSource(0);"
            "console.log(JSON.stringify({"
            "  panelWrites: panel.writes,"
            "  rows: first.length,"
            "  survived: JSON.stringify(first) === JSON.stringify(rowIds()),"
            "  cellWrites: cellWrites()"
            "}));"
        )

        self.assertEqual(result["panelWrites"], 1)
        self.assertEqual(result["rows"], 4)
        self.assertTrue(result["survived"], "no row may be rebuilt")
        self.assertEqual(result["cellWrites"], [0, 0, 0, 0])

    def test_stepping_a_match_repaints_only_the_two_rows_that_moved(self):
        """The keystone. Rows survive, so the change marks bolted onto them
        survive, and so does the reader's selection.
        """
        result = self._render(
            "const ranges = active => ["
            "  { start: 6, end: 9, active: active === 0 },"
            "  { start: 21, end: 24, active: active === 1 }"
            "];"
            "sandbox.renderExplorerSource(0, ranges(0));"
            "const before = rowIds();"
            "sandbox.renderExplorerSource(0, ranges(1));"
            "console.log(JSON.stringify({"
            "  panelWrites: panel.writes,"
            "  survived: JSON.stringify(before) === JSON.stringify(rowIds()),"
            "  cellWrites: cellWrites(),"
            "  activeMarks: panel.block.rows.map("
            "    row => (row.cell.innerHTML.match(/explorer-search-match active/g) || []).length"
            "  )"
            "}));"
        )

        self.assertEqual(result["panelWrites"], 1, "the panel must not be rebuilt")
        self.assertTrue(result["survived"])
        # Rows 1 and 3 carry the two matches; rows 2 and 4 are untouched.
        self.assertEqual(result["cellWrites"], [1, 0, 1, 0])
        self.assertEqual(result["activeMarks"], [0, 0, 1, 0])

    def test_a_content_change_still_rebuilds_the_rows(self):
        result = self._render(
            "sandbox.renderExplorerSource(0);"
            "const before = rowIds();"
            "pane._explorerFileContent = 'replaced' + String.fromCharCode(10);"
            "sandbox.renderExplorerSource(0);"
            "console.log(JSON.stringify({"
            "  panelWrites: panel.writes,"
            "  rows: panel.block.rows.length,"
            "  reused: rowIds().some(id => before.includes(id))"
            "}));"
        )

        self.assertEqual(result["panelWrites"], 2)
        self.assertEqual(result["rows"], 2)
        self.assertFalse(result["reused"])

    def test_a_replaced_panel_is_rebuilt_rather_than_reused(self):
        """A skip that trusted pane state alone would leave the pane empty."""
        result = self._render(
            "sandbox.renderExplorerSource(0);"
            "panel.innerHTML = '<textarea id=\\\"explorer-edit-textarea-0\\\"></textarea>';"
            "sandbox.renderExplorerSource(0);"
            "console.log(JSON.stringify({"
            "  rows: panel.block ? panel.block.rows.length : 0"
            "}));"
        )

        self.assertEqual(result["rows"], 4)


@unittest.skipUnless(NODE, "Node.js is required for explorer repaint tests")
class ChunkedSourceBuildTestCase(NodeHarnessMixin, unittest.TestCase):
    """A document too large to build in one task fills in over frames.

    The risk this carries is that six things used to read the rows the instant
    renderExplorerSource() returned — the fold controls, the occurrence tint,
    the change marks, the scroll restore, the editor's selection restore and
    the find's scroll-to-match. They now go through whenExplorerSourceRendered(),
    which is what these tests pin: nothing runs early, nothing is dropped when a
    newer render supersedes the build, and the rows are all there in the end.
    """

    def _build(self, script: str):
        return self._run_node(
            DOM_STUB
            + """
            const fs = require('fs');
            const vm = require('vm');
            const panel = makeSourcePanel('explorer-code-0');
            const sandbox = makeSandbox({ 'explorer-code-0': panel });
            const frames = [];
            sandbox.window.requestAnimationFrame = run => frames.push(run);
            sandbox.window.cancelAnimationFrame = () => {};
            vm.createContext(sandbox);
            [process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
                vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
            });
            sandbox.window.GridVibeExplorerRepaint = sandbox.GridVibeExplorerRepaint;
            const NL = String.fromCharCode(10);
            const body = count => Array.from(
                { length: count }, (_, i) => 'line ' + i
            ).join(NL) + NL;
            const pane = {
                _explorerMode: 'file',
                _explorerFilePath: 'big.log',
                _explorerFileContent: body(9000),
                _explorerFileLanguage: '',
                _explorerFilePlain: true,
                _explorerEdit: null
            };
            sandbox.terminals[0] = pane;
            const drain = () => {
                let guard = 0;
                while (frames.length && (guard += 1) < 100) { frames.shift()(); }
            };
            const rowCount = () => (panel.block ? panel.block.rows.length : 0);
            """
            + script,
            str(REPAINT_JS),
            str(VIEWER_JS),
            str(TABS_JS),
        )

    def test_the_rows_arrive_over_frames_and_readers_wait_for_them(self):
        result = self._build(
            "const seen = [];"
            "sandbox.renderExplorerSource(0);"
            "const afterCall = rowCount();"
            "sandbox.whenExplorerSourceRendered(0, () => seen.push(rowCount()));"
            "const beforeFrames = seen.length;"
            "const firstSlice = frames.length;"
            "drain();"
            "console.log(JSON.stringify({"
            "  afterCall,"
            "  firstSlice,"
            "  ranEarly: beforeFrames,"
            "  seen,"
            "  final: rowCount()"
            "}));"
        )

        # The call itself commits the empty lines block and one queued frame.
        self.assertEqual(result["afterCall"], 0)
        self.assertEqual(result["firstSlice"], 1)
        # Nothing may read the rows before they exist.
        self.assertEqual(result["ranEarly"], 0)
        self.assertEqual(result["seen"], [9001])
        self.assertEqual(result["final"], 9001)

    def test_a_superseded_build_stops_and_hands_over_its_readers(self):
        """A reader queued against a build that never finishes would strand a
        scroll restore on a pane the reader is still looking at.
        """
        result = self._build(
            "const seen = [];"
            "sandbox.renderExplorerSource(0);"
            "sandbox.whenExplorerSourceRendered(0, () => seen.push(rowCount()));"
            "frames.shift()();"
            "const partial = rowCount();"
            "pane._explorerFileContent = body(5000);"
            "sandbox.renderExplorerSource(0);"
            "drain();"
            "console.log(JSON.stringify({"
            "  partial,"
            "  seen,"
            "  final: rowCount()"
            "}));"
        )

        self.assertGreater(result["partial"], 0)
        self.assertLess(result["partial"], 9001)
        # The reader ran once, against the rows of the render that won.
        self.assertEqual(result["seen"], [5001])
        self.assertEqual(result["final"], 5001)


@unittest.skipUnless(NODE, "Node.js is required for explorer repaint tests")
class EditUnderlayRepaintTestCase(NodeHarnessMixin, unittest.TestCase):
    """The editor's underlay: replace the rows the keystroke moved."""

    def _type(self, script: str):
        return self._run_node(
            DOM_STUB
            + """
            const fs = require('fs');
            const vm = require('vm');
            const underlay = makeSourcePanel('underlay');
            const stack = {
                style: { setProperty() {}, removeProperty() {} },
                querySelector: selector => (
                    selector === '.explorer-edit-underlay' ? underlay : null
                )
            };
            const nodes = { '[data-explorer-edit-stack="0"]': stack };
            const sandbox = makeSandbox(nodes);
            const timers = [];
            sandbox.window.setTimeout = (run) => { timers.push(run); return timers.length; };
            sandbox.window.clearTimeout = () => {};
            vm.createContext(sandbox);
            [process.argv[2], process.argv[3], process.argv[4], process.argv[5]]
                .forEach(path => {
                    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
                });
            sandbox.window.GridVibeExplorerRepaint = sandbox.GridVibeExplorerRepaint;
            sandbox.EXPLORER_PLAIN_PREVIEW_THRESHOLD = 2 * 1024 * 1024;

            const NL = String.fromCharCode(10);
            const draft = ['one', 'two', 'three', 'four'].join(NL);
            const pane = {
                _explorerFileContent: draft,
                _explorerFileLanguage: 'python',
                _explorerFilePlain: false,
                _explorerEdit: { draft }
            };
            sandbox.terminals[0] = pane;
            // The underlay as the mount left it, plus the line list the mount
            // records so the first keystroke is already a splice.
            underlay.innerHTML = sandbox.explorerEditUnderlayHtml(draft, 'python', null);
            pane._explorerEditOverlayDraft = draft;
            pane._explorerEditOverlayLines = sandbox.explorerEditUnderlayLines(draft);

            let tokenized = 0;
            sandbox.explorerHighlightDocumentLines = () => { tokenized += 1; return null; };
            const rowIds = () => underlay.block.rows.map(row => row.nodeId);
            const numbers = () => underlay.block.rows.map(row => [
                row.dataset.explorerLine, row.firstElementChild.textContent
            ]);
            """
            + script,
            str(REPAINT_JS),
            str(VIEWER_JS),
            str(TABS_JS),
            str(OVERLAY_JS),
        )

    def test_typing_replaces_one_row_and_tokenizes_nothing(self):
        """Finding 10: the underlay used to run a whole-document Highlight.js
        pass, a run-map build and a full innerHTML commit on every animation
        frame, which made the cost of typing O(document) rather than O(edit).
        """
        result = self._type(
            "const before = rowIds();"
            "pane._explorerEdit.draft = ['one', 'twoX', 'three', 'four'].join(NL);"
            "sandbox.paintExplorerEditUnderlay(0);"
            "const after = rowIds();"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes,"
            "  tokenized,"
            "  rows: after.length,"
            "  replaced: after.filter((id, at) => id !== before[at]).length,"
            "  settleArmed: Boolean(pane._explorerEditOverlaySettle),"
            "  text: underlay.block.rows.map(row => row.cell.innerHTML).join('|')"
            "}));"
        )

        # One write only — the mount. The keystroke spliced rows instead.
        self.assertEqual(result["underlayWrites"], 1)
        self.assertEqual(result["tokenized"], 0)
        self.assertEqual(result["rows"], 4)
        self.assertEqual(result["replaced"], 1)
        self.assertIn("twoX", result["text"])
        # Fallback colour is an interim state, and it says so by arming the
        # settle pass that repaints with real tokens once typing stops.
        self.assertTrue(result["settleArmed"])

    def test_a_new_line_renumbers_the_rows_below_it(self):
        """The underlay is the visible text and the textarea over it is
        transparent, so a gutter that disagreed with the caret's line would be
        read as the editor losing its place.
        """
        result = self._type(
            "pane._explorerEdit.draft = ['one', 'tw', 'o', 'three', 'four'].join(NL);"
            "sandbox.paintExplorerEditUnderlay(0);"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes,"
            "  numbers: numbers()"
            "}));"
        )

        self.assertEqual(result["underlayWrites"], 1)
        self.assertEqual(
            result["numbers"],
            [["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]],
        )

    def test_the_settle_pass_repaints_the_whole_draft_with_real_tokens(self):
        result = self._type(
            "pane._explorerEdit.draft = ['one', 'twoX', 'three', 'four'].join(NL);"
            "sandbox.paintExplorerEditUnderlay(0, { full: true });"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes,"
            "  tokenized,"
            "  settleArmed: Boolean(pane._explorerEditOverlaySettle)"
            "}));"
        )

        self.assertEqual(result["underlayWrites"], 2)
        self.assertEqual(result["tokenized"], 1)
        # A full paint is the settled state; nothing is left pending behind it.
        self.assertFalse(result["settleArmed"])


if __name__ == "__main__":
    unittest.main()
