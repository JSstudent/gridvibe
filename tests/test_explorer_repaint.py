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
* a rebuild of the same document holds the reader's scroll offset across it, and
  a restore returns an exact offset rather than a fraction of a scroll extent
  that may have moved;
* the Markdown Preview panel is repainted only when its render moved, and a
  reused panel has the find's marks taken out of it explicitly;
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
        querySelector: selector => (
            selector === ':scope > code' || selector === '> code' ? cell : null
        )
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
        // The Source panel *is* the scroller, and replacing its children is
        // what resets the offset in a browser. The stub does the same, so a
        // held scroll position is a restore and not an untouched field.
        scrollTop: 0,
        scrollLeft: 0,
        dataset: {},
        style: { setProperty() {}, removeProperty() {} },
        classList: {
            add() {}, remove() {}, contains: () => false, toggle: () => false
        },
        addEventListener() {},
        setAttribute() {},
        appendChild() {},
        /* A swap build hands its rows over as one element exchange. No layout
           happens in between, so unlike an innerHTML write this does not
           collapse the content and does not reset the offset. */
        replaceChild(next, previous) {
            if (block === previous) { block = next; }
            return previous;
        },
        get innerHTML() { return panel.raw; },
        set innerHTML(value) {
            panel.writes += 1;
            panel.raw = value;
            panel.scrollTop = 0;
            panel.scrollLeft = 0;
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
            createElement: tag => {
                // A frame-sliced rebuild assembles its rows in a detached
                // <template> so the ones on screen are never taken away first.
                if (tag === 'template') {
                    const template = { content: { firstElementChild: null } };
                    Object.defineProperty(template, 'innerHTML', {
                        set(value) {
                            template.content.firstElementChild =
                                String(value).includes('explorer-source-lines')
                                    ? makeLinesBlock(value)
                                    : null;
                        },
                        get() { return ''; }
                    });
                    return template;
                }
                return {
                    _html: '',
                    get innerHTML() { return this._html; },
                    set innerHTML(value) { this._html = value; this.children = parseRows(value); },
                    children: []
                };
            },
            createTextNode: text => ({ text: String(text) }),
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
        performance: { now: () => Date.now() },
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
            "  buildInFlightMoved: plan({"
            "    pending: true, nextDecorations: new Map([[9, 'b']])"
            "  }),"
            "  tooWide: plan({ nextDecorations: wide }),"
            "  tooWideForItsSize: plan({ nextDecorations: wide, rowCount: 20000 })"
            "});"
        )

        # The duplicate render every file open used to pay for.
        self.assertEqual(verdicts["unchanged"]["mode"], "skip")
        # Marks left line 4 and arrived on line 9: both rows, nothing else.
        self.assertEqual(verdicts["moved"]["mode"], "decorate")
        self.assertEqual(verdicts["moved"]["lines"], [4, 9])
        for reason in ("contentMoved", "languageMoved", "foldsMoved"):
            self.assertEqual(verdicts[reason]["mode"], "full", reason)
        # A panel that is no longer the one we rendered into has to rebuild —
        # skipping it is how a pane ends up empty.
        self.assertEqual(verdicts["replacedPanel"]["mode"], "full")
        # A frame-sliced build still emitting rows is judged on what this
        # render would *change*. Nothing to change is already being painted, so
        # rebuilding is the duplicate open render at full size; marks that did
        # move have to start again, because the slices not yet emitted carry
        # the ranges the build started from.
        self.assertEqual(verdicts["buildInFlight"]["mode"], "skip")
        self.assertEqual(verdicts["buildInFlightMoved"]["mode"], "full")
        # Past the ceiling one string beats hundreds of per-row parses — but
        # the ceiling is relative to the document, because the string a rebuild
        # parses is the whole of it. 900 rows is too many for a small file and
        # comfortably within reach on a 20,000-row one, which is exactly where
        # a find's first keystroke lands.
        self.assertEqual(verdicts["tooWide"]["mode"], "full")
        self.assertEqual(verdicts["tooWideForItsSize"]["mode"], "decorate")
        self.assertEqual(len(verdicts["tooWideForItsSize"]["lines"]), 900)

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

    def test_a_settle_repaints_only_the_rows_whose_colour_moved(self):
        """The settle pass establishes that the draft has not moved since the
        rows were painted, so the only thing a real Highlight.js answer can
        change is their colour. For an ordinary keystroke that is the one row
        the splice inserted; for a structure-changing edit it is the tail, and
        one bulk write is then the cheaper renderer.
        """
        plans = self._policy(
            "const keys = n => Array.from({ length: n }, (_, i) => 'k' + i);"
            "const base = keys(1000);"
            "const oneMoved = base.slice(); oneMoved[41] = 'other';"
            "const twoUnknown = base.slice(); twoUnknown[7] = null; twoUnknown[8] = null;"
            "const tail = base.map((key, i) => (i >= 200 ? 'comment' : key));"
            "const plan = (previous, next, extra) => repaint.highlightRepaintPlan("
            "  Object.assign({ previousKeys: previous, nextKeys: next, rowCount: next.length }, extra)"
            ");"
            "emit({"
            "  identical: plan(base, base.slice()),"
            "  oneLine: plan(base, oneMoved),"
            "  unknownRows: plan(twoUnknown, base),"
            "  structural: plan(base, tail),"
            "  everyRow: plan(keys(4), ['a', 'b', 'c', 'd']),"
            "  noPreviousKeys: plan(null, base),"
            "  wrongLength: plan(keys(999), base)"
            "});"
        )

        # Nothing to paint that is not already on screen.
        self.assertEqual(plans["identical"]["mode"], "skip")
        # The keystroke's row, and no other.
        self.assertEqual(plans["oneLine"]["mode"], "repaint")
        self.assertEqual(plans["oneLine"]["lines"], [42])
        # A row the splice rebuilt carries the unknown sentinel and repaints
        # whatever its run shape looks like — it is standing in fallback colour.
        self.assertEqual(plans["unknownRows"]["mode"], "repaint")
        self.assertEqual(plans["unknownRows"]["lines"], [8, 9])
        # Typing `/*` at the top recolours the tail: past the document-scaled
        # ceiling one bulk write beats hundreds of per-row parses.
        self.assertEqual(plans["structural"]["mode"], "full")
        # A delta covering every row is the same trade at its limit.
        self.assertEqual(plans["everyRow"]["mode"], "full")
        # Nothing comparable: the caller falls back to the renderer it had.
        self.assertEqual(plans["noPreviousKeys"]["mode"], "full")
        self.assertEqual(plans["wrongLength"]["mode"], "full")

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
class PreviewRepaintTestCase(NodeHarnessMixin, unittest.TestCase):
    """The Markdown Preview panel is repainted only when its render moved.

    Every path that shows the panel ran the paint: selecting the tab, switching
    Source/Preview/Diff, and every repaint of the find. Each one replaced the
    whole subtree, which puts the reader at the top — and because the diagrams
    draw as they scroll into view, the panel it lands on is shorter than the
    one it replaced, so the restore that follows cannot find the way back
    either. On a long document that reads as being thrown to the top.
    """

    def _preview(self, script: str):
        return self._run_node(
            DOM_STUB
            + """
            const fs = require('fs');
            const vm = require('vm');
            const preview = {
                id: 'explorer-preview-0',
                writes: 0,
                _html: '',
                dataset: {},
                get innerHTML() { return preview._html; },
                set innerHTML(value) { preview.writes += 1; preview._html = value; },
                classList: { add() {}, remove() {}, contains: () => false },
                addEventListener() {},
                querySelectorAll: () => [],
                querySelector: () => null
            };
            const sandbox = makeSandbox({ 'explorer-preview-0': preview });
            vm.createContext(sandbox);
            [process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
                vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
            });
            const pane = {
                _explorerMode: 'file',
                _explorerFilePath: 'notes.md',
                _explorerPreviewHtml: '<h1>Notes</h1>',
                _explorerPreviewLoaded: true,
                _explorerFilePlain: false
            };
            sandbox.terminals[0] = pane;
            """
            + script,
            str(REPAINT_JS),
            str(VIEWER_JS),
            str(TABS_JS),
        )

    def test_revisiting_a_preview_does_not_replace_what_is_on_screen(self):
        result = self._preview(
            "sandbox.paintExplorerPreview(0);"
            "sandbox.paintExplorerPreview(0);"
            "sandbox.restoreExplorerPreview(0);"
            "const revisits = preview.writes;"
            "pane._explorerPreviewHtml = '<h1>Notes</h1><p>and more</p>';"
            "sandbox.paintExplorerPreview(0);"
            "console.log(JSON.stringify({"
            "  revisits,"
            "  afterNewRender: preview.writes,"
            "  showing: preview.innerHTML"
            "}));"
        )

        # Three visits to an unchanged render, one paint.
        self.assertEqual(result["revisits"], 1)
        # A render that actually moved still repaints.
        self.assertEqual(result["afterNewRender"], 2)
        self.assertEqual(result["showing"], "<h1>Notes</h1><p>and more</p>")

    def test_a_reused_panel_has_the_find_marks_taken_out_of_it(self):
        """A repaint dropped them with the subtree; a reused panel cannot."""
        result = self._preview(
            "const parent = { normalized: 0, normalize() { this.normalized += 1; } };"
            "const marks = ['one', 'two'].map(text => ({"
            "  textContent: text, parentNode: parent, replacedWith: null"
            "}));"
            "parent.replaceChild = (node, old) => { old.replacedWith = node; };"
            "const root = {"
            "  querySelectorAll: selector => ("
            "    selector === 'mark.explorer-search-match' ? marks : []"
            "  )"
            "};"
            "sandbox.explorerClearSearchMarks(root);"
            "console.log(JSON.stringify({"
            "  unwrapped: marks.map(mark => mark.replacedWith && mark.replacedWith.text),"
            "  normalized: parent.normalized"
            "}));"
        )

        self.assertEqual(result["unwrapped"], ["one", "two"])
        # Once per parent, not once per mark: a paragraph with fifty hits in it
        # would otherwise re-walk its own children fifty times.
        self.assertEqual(result["normalized"], 1)


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

    def test_a_wide_find_on_a_big_file_repaints_instead_of_rebuilding(self):
        """The ceiling is relative, because a rebuild's cost is the document.

        A find's first keystrokes match nearly every line, so an absolute
        400-row ceiling turned each of them into a full rebuild of tens of
        thousands of rows — which is exactly the case the incremental repaint
        was built for and exactly the case it refused.
        """
        result = self._render(
            "const NL = String.fromCharCode(10);"
            "pane._explorerFileContent = Array.from({ length: 3000 },"
            "  (_, i) => (i % 5 === 0 ? 'hit here' : 'plain line')).join(NL) + NL;"
            "sandbox.renderExplorerSource(0);"
            "const before = rowIds();"
            "const ranges = [];"
            "let at = pane._explorerFileContent.indexOf('hit');"
            "while (at !== -1) {"
            "  ranges.push({ start: at, end: at + 3, active: ranges.length === 0 });"
            "  at = pane._explorerFileContent.indexOf('hit', at + 3);"
            "}"
            "sandbox.renderExplorerSource(0, ranges);"
            "console.log(JSON.stringify({"
            "  matches: ranges.length,"
            "  panelWrites: panel.writes,"
            "  survived: JSON.stringify(before) === JSON.stringify(rowIds()),"
            "  touched: cellWrites().filter(Boolean).length,"
            "  marked: panel.block.rows.filter("
            "    row => row.cell.innerHTML.includes('explorer-search-match')"
            "  ).length"
            "}));"
        )

        self.assertEqual(result["matches"], 600)
        # 600 rows is well past the old absolute ceiling and well inside a
        # 3,000-row document's share of itself.
        self.assertEqual(result["panelWrites"], 1, "the panel must not be rebuilt")
        self.assertTrue(result["survived"], "no row may be rebuilt")
        self.assertEqual(result["touched"], 600)
        self.assertEqual(result["marked"], 600)

    def test_a_rebuild_of_the_same_document_holds_the_scroll_position(self):
        """A presentation change is not navigation.

        A repaint too wide to do in place, and the syntax colours arriving from
        the worker, both rebuild rows that describe the same document — and the
        panel is the scroller, so rebuilding sent the reader back to line 1 a
        beat after a large file finished opening.
        """
        result = self._render(
            "const NL = String.fromCharCode(10);"
            "pane._explorerFileContent ="
            "  Array.from({ length: 600 }, () => 'hit line').join(NL) + NL;"
            "sandbox.renderExplorerSource(0);"
            "panel.scrollTop = 4200;"
            "panel.scrollLeft = 17;"
            "const ranges = [];"
            "let at = pane._explorerFileContent.indexOf('hit');"
            "while (at !== -1) {"
            "  ranges.push({ start: at, end: at + 3, active: false });"
            "  at = pane._explorerFileContent.indexOf('hit', at + 3);"
            "}"
            "sandbox.renderExplorerSource(0, ranges);"
            "const held = { top: panel.scrollTop, left: panel.scrollLeft };"
            "pane._explorerFileContent = 'a different file' + NL;"
            "sandbox.renderExplorerSource(0);"
            "console.log(JSON.stringify({"
            "  matches: ranges.length,"
            "  panelWrites: panel.writes,"
            "  held,"
            "  afterNewContent: panel.scrollTop"
            "}));"
        )

        # 600 marked rows in a 600-row document: past its share, so this is a
        # genuine rebuild and not the in-place repaint above.
        self.assertEqual(result["matches"], 600)
        self.assertEqual(result["panelWrites"], 3)
        self.assertEqual(result["held"], {"top": 4200, "left": 17})
        # A different document is navigation, and keeps nothing.
        self.assertEqual(result["afterNewContent"], 0)

    def test_a_restore_returns_an_exact_offset_and_falls_back_to_a_fraction(self):
        """Inside a session the offset is the truthful thing.

        A capture taken here carries both the offset and its ratio, and the
        content it is restored onto is the same content. Scaling by a scroll
        extent that moved a few pixels puts the reader somewhere they never
        were — and horizontally the extent is the length of the single longest
        line, which one fold or one row a frame-sliced build has not emitted
        yet is enough to change. Only a record that survived a restart has
        nothing but the fraction (explorer-persistence.js stores `{x, y}`), and
        that is the case the fraction exists for.
        """
        result = self._render(
            "const el = {"
            "  scrollTop: 0, scrollLeft: 0,"
            "  scrollHeight: 5000, clientHeight: 500,"
            "  scrollWidth: 4000, clientWidth: 400"
            "};"
            "const at = (metrics, widen) => {"
            "  el.scrollTop = 0; el.scrollLeft = 0;"
            "  el.scrollWidth = widen ? 9000 : 4000;"
            "  sandbox.applyScrollMetrics(el, metrics);"
            "  return { top: el.scrollTop, left: el.scrollLeft };"
            "};"
            "const captured = {"
            "  scrollLeft: 900, scrollLeftRatio: 900 / 3600,"
            "  scrollTop: 1800, scrollTopRatio: 1800 / 4500,"
            "  wasAtBottom: false"
            "};"
            "console.log(JSON.stringify({"
            "  exact: at(captured),"
            "  widened: at(captured, true),"
            "  persisted: at({ scrollLeftRatio: 0.25, scrollTopRatio: 0.4 }),"
            "  atBottom: at({ scrollTop: 10, scrollTopRatio: 0, wasAtBottom: true }),"
            "  clamped: at({ scrollLeft: 99999, scrollTop: 99999 })"
            "}));"
        )

        self.assertEqual(result["exact"], {"top": 1800, "left": 900})
        # The longest line got longer between capture and restore. The column
        # the reader was on did not move, so neither does the view.
        self.assertEqual(result["widened"], {"top": 1800, "left": 900})
        # Nothing but fractions: the best answer available, and the one a
        # restored workspace gets.
        self.assertEqual(result["persisted"], {"top": 1800, "left": 900})
        # "The end of the file" is an intent, not a coordinate.
        self.assertEqual(result["atBottom"]["top"], 4500)
        self.assertEqual(result["clamped"], {"top": 4500, "left": 3600})

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
class SourceWorkerAdapterTestCase(NodeHarnessMixin, unittest.TestCase):
    """The Source adapter paints first, then accepts only its worker answer."""

    def _worker_render(self, script: str):
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
            const jobs = [];
            sandbox.window.GridVibeExplorerWorkers = {
                canHighlight: () => true,
                highlight(source, grammar) {
                    return new Promise((resolve, reject) => jobs.push({
                        source, grammar, resolve, reject
                    }));
                }
            };
            const pane = {
                _explorerMode: 'file',
                _explorerFilePath: 'sample.py',
                _explorerFileContent: 'def first():',
                _explorerFileLanguage: 'python',
                _explorerFilePlain: false,
                _explorerEdit: null
            };
            sandbox.terminals[0] = pane;
            const keywordMap = (source, word) => new Map([[
                1, [
                    { className: 'hljs-keyword', text: word, start: 0 },
                    { className: '', text: source.slice(word.length), start: word.length }
                ]
            ]]);
            """
            + script,
            str(REPAINT_JS),
            str(VIEWER_JS),
            str(TABS_JS),
        )

    def test_plain_rows_paint_once_while_one_worker_job_is_shared(self):
        result = self._worker_render(
            "(async () => {"
            "  sandbox.renderExplorerSource(0);"
            "  const first = panel.block.rows[0].cell.innerHTML;"
            "  sandbox.renderExplorerSource(0);"
            "  const launchesBeforeAnswer = jobs.length;"
            "  jobs[0].resolve(keywordMap(jobs[0].source, 'def'));"
            "  await Promise.resolve(); await Promise.resolve();"
            "  console.log(JSON.stringify({"
            "    first, launchesBeforeAnswer, panelWrites: panel.writes,"
            "    final: panel.block.rows[0].cell.innerHTML"
            "  }));"
            "})();"
        )

        self.assertEqual(result["launchesBeforeAnswer"], 1)
        self.assertNotIn("explorer-code-keyword", result["first"])
        self.assertNotIn("hljs-keyword", result["first"])
        self.assertEqual(result["panelWrites"], 2)
        self.assertIn('<span class="hljs-keyword">def</span>', result["final"])

    def test_a_stale_worker_answer_never_repaints_newer_content(self):
        result = self._worker_render(
            "(async () => {"
            "  sandbox.renderExplorerSource(0);"
            "  pane._explorerFileContent = 'def second():';"
            "  sandbox.renderExplorerSource(0);"
            "  const writesBeforeStale = panel.writes;"
            "  jobs[0].resolve(keywordMap(jobs[0].source, 'def'));"
            "  await Promise.resolve(); await Promise.resolve();"
            "  const writesAfterStale = panel.writes;"
            "  jobs[1].resolve(keywordMap(jobs[1].source, 'def'));"
            "  await Promise.resolve(); await Promise.resolve();"
            "  console.log(JSON.stringify({"
            "    launches: jobs.length, writesBeforeStale, writesAfterStale,"
            "    final: panel.block.rows[0].cell.innerHTML"
            "  }));"
            "})();"
        )

        self.assertEqual(result["launches"], 2)
        self.assertEqual(result["writesAfterStale"], result["writesBeforeStale"])
        self.assertIn("second", result["final"])
        self.assertIn("hljs-keyword", result["final"])


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

    def test_a_detached_build_is_suspended_and_resumes_where_it_stopped(self):
        """A group switch detaches the card; the build kept spending its frame
        budget appending rows into a tree nobody could see, in competition
        with the incoming group's attach, fit and paint.
        """
        result = self._build(
            "const seen = [];"
            "sandbox.renderExplorerSource(0);"
            "sandbox.whenExplorerSourceRendered(0, () => seen.push(rowCount()));"
            "frames.shift()();"
            "const partial = rowCount();"
            "sandbox.explorerSuspendSourceRenderJob(pane);"
            "drain();"
            "const whileSuspended = rowCount();"
            "const ranWhileSuspended = seen.length;"
            "sandbox.explorerResumeSourceRenderJob(pane);"
            "drain();"
            "console.log(JSON.stringify({"
            "  partial,"
            "  whileSuspended,"
            "  ranWhileSuspended,"
            "  seen,"
            "  final: rowCount()"
            "}));"
        )

        self.assertGreater(result["partial"], 0)
        self.assertLess(result["partial"], 9001)
        # Not one further row while the card is off screen, however many
        # frames go by.
        self.assertEqual(result["whileSuspended"], result["partial"])
        self.assertEqual(result["ranWhileSuspended"], 0)
        # Suspension is not cancellation: the build resumes from where it
        # stopped and its reader still gets the whole document.
        self.assertEqual(result["seen"], [9001])
        self.assertEqual(result["final"], 9001)

    def test_a_build_closed_while_suspended_still_flushes_its_readers(self):
        """The group can be closed while its build is suspended. A reader left
        queued on a build that will never resume is the stranded scroll
        restore the hand-over contract exists to prevent.
        """
        result = self._build(
            "const seen = [];"
            "sandbox.renderExplorerSource(0);"
            "sandbox.whenExplorerSourceRendered(0, () => seen.push(rowCount()));"
            "frames.shift()();"
            "const partial = rowCount();"
            "sandbox.explorerSuspendSourceRenderJob(pane);"
            "sandbox.explorerAbandonSourceRenderJob(pane);"
            "drain();"
            "console.log(JSON.stringify({"
            "  partial,"
            "  seen,"
            "  final: rowCount()"
            "}));"
        )

        self.assertGreater(result["partial"], 0)
        self.assertEqual(result["seen"], [result["partial"]])
        # And nothing lands afterwards: the abandoned job is no longer the
        # pane's, so a frame still in the queue paints nothing.
        self.assertEqual(result["final"], result["partial"])


@unittest.skipUnless(NODE, "Node.js is required for explorer repaint tests")
class EditUnderlayRepaintTestCase(NodeHarnessMixin, unittest.TestCase):
    """The editor's underlay: replace the rows the keystroke moved."""

    def _type(self, script: str, *, language: str = "python",
              lines: str = "['one', 'two', 'three', 'four']"):
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
            const draft = LINES.join(NL);
            const pane = {
                _explorerFileContent: draft,
                _explorerFileLanguage: LANGUAGE,
                _explorerFilePlain: false,
                _explorerEdit: { draft }
            };
            sandbox.terminals[0] = pane;

            let findRepaints = 0;
            sandbox.window.repaintExplorerEditFind = () => { findRepaints += 1; };

            /* One run per line, so a per-line class is expressible and the run
               key is `length:className`. `styled` names the class for a line. */
            const runsFor = (text, styled) => {
                const map = new Map();
                let offset = 0;
                text.split(NL).forEach((line, at) => {
                    map.set(at + 1, [{
                        className: styled ? (styled(at + 1) || '') : '',
                        text: line,
                        start: offset
                    }]);
                    offset += line.length + 1;
                });
                return map;
            };

            /* The underlay as mountExplorerEditOverlay() leaves it: the rows,
               the line list a splice compares against, and the paint keys
               saying what those rows are actually coloured with. */
            const mount = runs => {
                const current = String(pane._explorerEdit.draft);
                const model = sandbox.explorerSourceRowModel(
                    current, LANGUAGE, new Set(), runs, { foldControls: false }
                );
                underlay.innerHTML = sandbox.explorerEditUnderlayRowsHtml(model);
                pane._explorerEditOverlayDraft = current;
                pane._explorerEditOverlayLines = model.records.map(record => record.text);
                pane._explorerEditUnderlayPaintKeys =
                    sandbox.explorerEditUnderlayPaintKeys(model);
            };
            mount(null);

            let tokenized = 0;
            sandbox.explorerHighlightDocumentLines = () => { tokenized += 1; return null; };
            const rowIds = () => underlay.block.rows.map(row => row.nodeId);
            const cellWrites = () => underlay.block.rows.map(row => row.cell.writes);
            const cellText = () => underlay.block.rows.map(row => row.cell.innerHTML);
            const numbers = () => underlay.block.rows.map(row => [
                row.dataset.explorerLine, row.firstElementChild.textContent
            ]);
            """.replace("LANGUAGE", json.dumps(language)).replace("LINES", lines)
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

    def test_a_settle_replaces_only_the_code_cells_whose_colour_moved(self):
        """The settle pass has already established that the draft has not moved
        since the rows were painted, so the row set is identical and the only
        thing the worker's answer changes is colour. Rewriting the whole
        underlay to say that was the last Source surface still rebuilding where
        it could repaint — and on a 15,000-line file it is a whole-document row
        build and an innerHTML parse, arriving about a second after the
        keystroke that asked for it.
        """
        result = self._type(
            "mount(runsFor(draft, () => ''));"
            "const mountedWrites = underlay.writes;"
            "const next = ['one', 'twoX', 'three', 'four'].join(NL);"
            "pane._explorerEdit.draft = next;"
            "sandbox.paintExplorerEditUnderlay(0);"
            "const spliced = rowIds();"
            "const findsAfterSplice = findRepaints;"
            "const writesAfterSplice = cellWrites();"
            "sandbox.paintExplorerEditUnderlay(0, {"
            "  full: true, runs: runsFor(next, line => (line === 2 ? 'hljs-string' : ''))"
            "});"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes, mountedWrites,"
            "  sameRows: rowIds().every((id, at) => id === spliced[at]),"
            "  writeDelta: cellWrites().map((count, at) => count - writesAfterSplice[at]),"
            "  second: cellText()[1],"
            "  findsAfterSplice, findRepaints"
            "}));"
        )

        # No whole-underlay write beyond the two mounts.
        self.assertEqual(result["underlayWrites"], result["mountedWrites"])
        # Every row is the same node it was, so the reader's selection, the
        # change marks and their marker buttons survive the settle.
        self.assertTrue(result["sameRows"])
        self.assertEqual(result["writeDelta"], [0, 1, 0, 0])
        self.assertIn("hljs-string", result["second"])
        # A replaced cell detaches the Ranges the find had on it, so the find
        # is re-derived exactly as it is after a full paint.
        self.assertGreater(result["findRepaints"], result["findsAfterSplice"])

    def test_an_edit_whose_run_shape_did_not_move_still_repaints_its_row(self):
        """The previous side of the comparison is what is *painted*, not the
        previous draft's runs. A splice paints its rebuilt rows through the
        per-line fallback lexer and leaves the rest of the Highlight.js DOM
        standing, so the surface is a hybrid — and replacing one identifier
        character with another leaves the run shape identical while the row on
        screen is still in its temporary fallback markup.
        """
        result = self._type(
            "mount(runsFor(draft, line => (line === 2 ? 'hljs-string' : '')));"
            "const mountedWrites = underlay.writes;"
            "const next = ['one', 'twx', 'three', 'four'].join(NL);"
            "pane._explorerEdit.draft = next;"
            "sandbox.paintExplorerEditUnderlay(0);"
            "const afterSplice = cellText()[1];"
            "const writesAfterSplice = cellWrites();"
            "sandbox.paintExplorerEditUnderlay(0, {"
            "  full: true, runs: runsFor(next, line => (line === 2 ? 'hljs-string' : ''))"
            "});"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes, mountedWrites, afterSplice,"
            "  writeDelta: cellWrites().map((count, at) => count - writesAfterSplice[at]),"
            "  second: cellText()[1]"
            "}));"
        )

        # 'two' and 'twx' are both one three-character string run, so a
        # run-shape comparison alone would call this row unchanged.
        self.assertNotIn("hljs-string", result["afterSplice"])
        self.assertEqual(result["writeDelta"], [0, 1, 0, 0])
        self.assertIn("hljs-string", result["second"])
        self.assertIn("twx", result["second"])
        self.assertEqual(result["underlayWrites"], result["mountedWrites"])

    def test_an_inserted_line_shifts_the_preserved_keys_with_their_nodes(self):
        """A splice moves the suffix's nodes down a line. Its keys move with
        them, or every row below an Enter would look changed against the line
        numbers it used to hold and the settle would rebuild the document.
        """
        result = self._type(
            "mount(runsFor(draft, line => (line === 3 ? 'hljs-title' : '')));"
            "const mountedWrites = underlay.writes;"
            "const next = ['one', 'tw', 'o', 'three', 'four'].join(NL);"
            "pane._explorerEdit.draft = next;"
            "sandbox.paintExplorerEditUnderlay(0);"
            "const spliced = rowIds();"
            "const writesAfterSplice = cellWrites();"
            "sandbox.paintExplorerEditUnderlay(0, {"
            "  full: true, runs: runsFor(next, line => (line === 4 ? 'hljs-title' : ''))"
            "});"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes, mountedWrites,"
            "  sameRows: rowIds().every((id, at) => id === spliced[at]),"
            "  writeDelta: cellWrites().map((count, at) => count - writesAfterSplice[at]),"
            "  fourth: cellText()[3], numbers: numbers()"
            "}));"
        )

        self.assertEqual(result["underlayWrites"], result["mountedWrites"])
        self.assertTrue(result["sameRows"])
        # Only the two rows the splice built. 'three' kept its node, its
        # colour and — one line lower — its key.
        self.assertEqual(result["writeDelta"], [0, 1, 1, 0, 0])
        self.assertIn("hljs-title", result["fourth"])
        self.assertEqual(
            result["numbers"],
            [["1", "1"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"]],
        )

    def test_a_markdown_row_repaints_when_only_its_heading_level_moved(self):
        """explorerSourceRowCodeHtml() wraps a heading row, so a run key alone
        does not describe the cell's markup. Opening a fence above a heading
        takes its heading level away without touching a character of its text.
        """
        result = self._type(
            "mount(runsFor(draft, () => ''));"
            "const beforeSettle = cellText();"
            "const mountedWrites = underlay.writes;"
            "const next = ['```', 'alpha', '# beta', 'gamma'].join(NL);"
            "pane._explorerEdit.draft = next;"
            "sandbox.paintExplorerEditUnderlay(0);"
            "const spliced = rowIds();"
            "const writesAfterSplice = cellWrites();"
            "sandbox.paintExplorerEditUnderlay(0, { full: true, runs: runsFor(next, () => '') });"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes, mountedWrites,"
            "  sameRows: rowIds().every((id, at) => id === spliced[at]),"
            "  writeDelta: cellWrites().map((count, at) => count - writesAfterSplice[at]),"
            "  headingBefore: beforeSettle[1], headingAfter: cellText()[2]"
            "}));",
            language="markdown",
            lines="['alpha', '# beta', 'gamma']",
        )

        self.assertIn("explorer-md-source-heading", result["headingBefore"])
        self.assertEqual(result["underlayWrites"], result["mountedWrites"])
        self.assertTrue(result["sameRows"])
        # The fence row the splice built, and the heading row that stopped
        # being one. 'alpha' and 'gamma' are untouched.
        self.assertEqual(result["writeDelta"], [1, 0, 1, 0])
        self.assertNotIn("explorer-md-source-heading", result["headingAfter"])
        self.assertIn("# beta", result["headingAfter"])

    def test_a_structure_changing_edit_still_rebuilds_the_whole_underlay(self):
        """Typing a `/*` at the top recolours the tail. Past the ceiling one
        bulk write beats a per-row parse for every line in the file, and a
        partial repaint here would leave the document half-commented.
        """
        result = self._type(
            "mount(runsFor(draft, () => ''));"
            "const mountedWrites = underlay.writes;"
            "const next = ['one', '/*twoX', 'three', 'four'].join(NL);"
            "pane._explorerEdit.draft = next;"
            "sandbox.paintExplorerEditUnderlay(0);"
            "sandbox.paintExplorerEditUnderlay(0, {"
            "  full: true, runs: runsFor(next, () => 'hljs-comment')"
            "});"
            "console.log(JSON.stringify({"
            "  underlayWrites: underlay.writes, mountedWrites, tokenized,"
            "  last: cellText()[3], settleArmed: Boolean(pane._explorerEditOverlaySettle)"
            "}));"
        )

        self.assertEqual(result["underlayWrites"], result["mountedWrites"] + 1)
        self.assertIn("hljs-comment", result["last"])
        # The answer was handed in; nothing was tokenized on this thread.
        self.assertEqual(result["tokenized"], 0)
        self.assertFalse(result["settleArmed"])

    def test_typing_does_not_evict_another_panes_line_records(self):
        """The draft moves on every keystroke, so its records never hit the
        page-wide LRU — but they did `unshift`, and with one explorer pane open
        the limit is two slots. Every typing frame evicted the previous draft
        *and* the pane's own file records.
        """
        result = self._type(
            "pane._explorerMode = 'file';"
            "sandbox.terminals[1] = { _explorerMode: 'file' };"
            "const other = ['a', 'b', 'c'].join(NL);"
            "const otherRecords = sandbox.explorerSourceLineRecords(other);"
            "for (let at = 0; at < 12; at += 1) {"
            "  pane._explorerEdit.draft = ['one', 'two' + at, 'three', 'four'].join(NL);"
            "  sandbox.paintExplorerEditUnderlay(0);"
            "}"
            "console.log(JSON.stringify({"
            "  kept: sandbox.explorerSourceLineRecords(other) === otherRecords,"
            "  rows: underlay.block.rows.length,"
            "  last: cellText()[1]"
            "}));"
        )

        self.assertTrue(result["kept"])
        self.assertEqual(result["rows"], 4)
        self.assertIn("two11", result["last"])

    def test_the_settle_pass_uses_the_worker_for_an_eligible_draft(self):
        result = self._type(
            "(async () => {"
            "  let resolveRuns; let workerCalls = 0;"
            "  sandbox.window.GridVibeExplorerWorkers = {"
            "    canHighlight: () => true,"
            "    highlight: () => { workerCalls += 1; return new Promise(resolve => { resolveRuns = resolve; }); }"
            "  };"
            "  pane._explorerEdit.draft = ['one', 'twoX', 'three', 'four'].join(NL);"
            "  sandbox.paintExplorerEditUnderlay(0);"
            "  timers.shift()();"
            "  const beforeAnswer = underlay.writes;"
            "  let offset = 0;"
            "  const runs = new Map();"
            "  pane._explorerEdit.draft.split(NL).forEach((line, at) => {"
            "    runs.set(at + 1, [{"
            "      className: at === 0 ? 'hljs-keyword' : '', text: line, start: offset"
            "    }]);"
            "    offset += line.length + 1;"
            "  });"
            "  resolveRuns(runs);"
            "  await Promise.resolve(); await Promise.resolve();"
            "  console.log(JSON.stringify({"
            "    workerCalls, tokenized, beforeAnswer, underlayWrites: underlay.writes,"
            "    first: underlay.block.rows[0].cell.innerHTML"
            "  }));"
            "})();"
        )

        self.assertEqual(result["workerCalls"], 1)
        self.assertEqual(result["tokenized"], 0)
        self.assertEqual(result["beforeAnswer"], 1)
        self.assertEqual(result["underlayWrites"], 2)
        self.assertIn("hljs-keyword", result["first"])


if __name__ == "__main__":
    unittest.main()
