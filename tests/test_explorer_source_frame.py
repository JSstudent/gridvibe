"""Source-view ground work for change marking: the fixed
``.explorer-source-frame`` wrapper around the scrollable Source view, the
``explorerPanelScrollTarget`` branch that sees through it, and the per-pane
memoization of the whole-document token map. None of this is user-visible on
its own, so the suite is contract-level on the served assets (selectors,
``data-*`` hooks, named functions) plus ordering guarantees the CSS relies on.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import api

VIEWER_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "explorer-viewer.js"
_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TIERS_JS = _JS / "explorer-tiers.js"
REPAINT_JS = _JS / "explorer-repaint.js"
TABS_JS = _JS / "explorer-tabs.js"
NODE = shutil.which("node")

# Enough document for explorer-viewer.js to evaluate, plus a panel whose
# querySelector answers the handful of selectors the scroll target asks for.
SCROLL_TARGET_HARNESS = """
const fs = require('fs');
const vm = require('vm');

function panelStub(kind, children) {
    const nodes = new Map(Object.entries(children || {}));
    const make = selector => {
        const entry = nodes.get(selector);
        if (!entry) { return null; }
        return {
            name: entry.name,
            querySelector: inner => (entry.children || []).includes(inner)
                ? { name: `${entry.name}${inner}` }
                : null
        };
    };
    return {
        name: 'panel',
        dataset: { explorerFilePanel: kind },
        querySelector: selector => make(selector)
    };
}

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
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const view = children => ({ '.explorer-source-view': { name: 'view', children } });
const cases = {
    // The overlay's textarea is overflow:hidden and content-height, so the
    // view goes on scrolling both layers.
    overlay: panelStub('source', view(['.explorer-edit-stack', '.explorer-source-editor'])),
    // Overlay stood down: the full-height textarea scrolls itself.
    bareEditor: panelStub('source', view(['.explorer-source-editor'])),
    readOnly: panelStub('source', view([])),
    frameOnly: panelStub('source', {}),
    diff: panelStub('diff', { '.explorer-diff-content': { name: 'diffContent' } })
};

const resolved = {};
Object.entries(cases).forEach(([key, panel]) => {
    resolved[key] = sandbox.explorerPanelScrollTarget(panel).name;
});
resolved.missing = String(sandbox.explorerPanelScrollTarget(null));
process.stdout.write(JSON.stringify(resolved));
"""

# The three states of renderExplorerSourceLines()'s token-map argument, with
# the whole-document pass replaced by a spy so "did this tokenize?" is an
# observation rather than a reading of the source.
TOKEN_MAP_HARNESS = """
const fs = require('fs');
const vm = require('vm');

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
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const source = 'def spam' + String.fromCharCode(10);
const calls = [];
sandbox.explorerHighlightDocumentLines = (content, normalizedLanguage) => {
    calls.push([content, normalizedLanguage]);
    return null;
};

const run = (label, invoke) => {
    calls.length = 0;
    const html = invoke();
    return { html, tokenizeCalls: calls.length, tokenizeArgs: calls[0] || null };
};

const supplied = new Map([[1, [{ className: 'hljs-keyword', text: 'def', start: 0 }]]]);
process.stdout.write(JSON.stringify({
    supplied: run('supplied', () => sandbox.renderExplorerSourceLines(
        source, 'python', [], new Set(), supplied
    )),
    cachedMiss: run('cachedMiss', () => sandbox.renderExplorerSourceLines(
        source, 'python', [], new Set(), null
    )),
    omitted: run('omitted', () => sandbox.renderExplorerSourceLines(source, 'python'))
}));
"""


# A highlight job that rejects, driven through the real cache gate. The two
# neighbours it reports to — the whole-document tokenizer and the re-render —
# are spies, so what the failure path does is observable rather than inferred.
HIGHLIGHT_FAILURE_HARNESS = """
const fs = require('fs');
const vm = require('vm');

let rejectJob = null;
const sandbox = {
    console: { log: console.log, error() {} },
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
        requestAnimationFrame: () => 0,
        GridVibeExplorerWorkers: {
            available: () => true,
            // Above the worker floor by construction: the gate under test is
            // what happens when the job fails, not when it is offered.
            canHighlight: () => true,
            highlight: () => new Promise((resolve, reject) => { rejectJob = reject; })
        }
    },
    navigator: {},
    AbortController,
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    terminals: [],
    sessionIds: [],
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const source = 'def spam():' + String.fromCharCode(10) + '    return 1' + String.fromCharCode(10);
const colours = new Map([[1, [{ className: 'hljs-keyword', text: 'def', start: 0 }]]]);

let tokenizeCalls = 0;
let renderCalls = 0;
sandbox.explorerHighlightDocumentLines = () => { tokenizeCalls += 1; return colours; };
sandbox.renderExplorerSource = () => { renderCalls += 1; };
sandbox.explorerSourceSearchRangesOnScreen = () => [];

const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'app.py',
    _explorerFileContent: source,
    _explorerFileLanguage: 'python',
    _explorerFilePlain: false,
    _explorerSourceRender: { stale: false },
    _explorerEdit: null
};
sandbox.terminals[0] = pane;

const ask = () => sandbox.explorerHighlightLinesForRender(0, pane, source, 'python');

// First paint: the worker owns this buffer, so the rows are deliberately plain
// and nothing has been tokenized on this thread.
const firstAsk = ask();
const pendingAtFirst = typeof firstAsk === 'symbol';
const tokenizeBeforeFailure = tokenizeCalls;

rejectJob(new Error('worker exploded'));

setTimeout(() => {
    const afterFailure = ask();
    process.stdout.write(JSON.stringify({
        pendingAtFirst,
        tokenizeBeforeFailure,
        tokenizeAfterFailure: tokenizeCalls,
        renderCalls,
        markedStale: pane._explorerSourceRender.stale,
        // What the *open* buffer gets from here on: real colours, not the
        // pending sentinel forever.
        stillPending: typeof afterFailure === 'symbol',
        recolouredLines: afterFailure instanceof Map ? afterFailure.size : null,
        // ...and asking again re-uses that answer rather than tokenizing anew
        // or starting a second doomed job.
        tokenizeAfterRepeat: (ask(), tokenizeCalls)
    }));
}, 0);
"""


# The line-record cache, driven through its one public entry point. Records are
# identity-comparable per call, so "was this a hit?" is observable without
# reaching inside the cache: a hit returns the very same array.
LINE_RECORD_CACHE_HARNESS = """
const fs = require('fs');
const vm = require('vm');

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
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const NL = String.fromCharCode(10);
const doc = name => name + NL + name + ' second line' + NL;

// One document per pane, plus the editor draft that is live beside it.
const documents = ['a', 'b', 'c'].map(doc);
const draft = doc('a') + 'draft' + NL;

const openPanes = count => {
    sandbox.terminals.length = 0;
    for (let at = 0; at < count; at += 1) {
        sandbox.terminals.push({
            _explorerMode: 'file',
            _explorerFileContent: documents[at],
            _explorerEdit: null
        });
    }
};

// Ask about every document once, then ask about the first again. With the
// cache big enough for the live panes that last ask is a hit; with a flat two
// entries it has already been evicted by the panes beside it.
const roundTrip = count => {
    const live = documents.slice(0, count);
    const first = live.map(source => sandbox.explorerSourceLineRecords(source));
    return live.map(
        (source, at) => sandbox.explorerSourceLineRecords(source) === first[at]
    );
};

openPanes(1);
const onePane = roundTrip(1);

openPanes(3);
const threePanes = roundTrip(3);

// A pane editing its file holds two live buffers at once, and both must stay
// resident or every keystroke re-walks the document it is not about.
openPanes(3);
sandbox.terminals[0]._explorerEdit = { draft };
const sourceRecords = sandbox.explorerSourceLineRecords(documents[0]);
const draftRecords = sandbox.explorerSourceLineRecords(draft);
const editingKeepsBoth = (
    sandbox.explorerSourceLineRecords(documents[0]) === sourceRecords
    && sandbox.explorerSourceLineRecords(draft) === draftRecords
);

// Closing the last pane hands the documents back: nothing holds them, and an
// LRU never evicts without an insert to evict on.
const beforeRelease = sandbox.explorerSourceLineRecords(documents[0]) === sourceRecords;
sandbox.explorerReleaseLineRecordCache();
const afterRelease = sandbox.explorerSourceLineRecords(documents[0]) === sourceRecords;

process.stdout.write(JSON.stringify({
    onePane, threePanes, editingKeepsBoth, beforeRelease, afterRelease
}));
"""


STALE_RANGE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const code = {
    id: 'explorer-code-0',
    innerHTML: '',
    dataset: {},
    style: { setProperty() {}, removeProperty() {} },
    classList: { add() {}, remove() {}, contains: () => false, toggle: () => false },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener() {},
    setAttribute() {}
};

const sandbox = {
    console,
    document: {
        getElementById: id => (id === 'explorer-code-0' ? code : null),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        createElement: () => ({ innerHTML: '', className: '', appendChild() {} }),
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {}, setTimeout, clearTimeout,
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        requestAnimationFrame: () => 0
    },
    navigator: {}, setTimeout, clearTimeout, requestAnimationFrame: () => 0,
    terminals: [], sessionIds: [],
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
};
sandbox.globalThis = sandbox;
sandbox.applyExplorerChangeMarks = () => {};
sandbox.scheduleExplorerOccurrenceHighlight = () => {};
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4], process.argv[5]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
sandbox.window.GridVibeExplorerTiers = sandbox.GridVibeExplorerTiers;
sandbox.window.GridVibeExplorerRepaint = sandbox.GridVibeExplorerRepaint;

const NL = String.fromCharCode(13, 10);
const rows = 40;
const line = n => 'const s' + n + ' = { id: ' + n + ' };';
const crlf = Array.from({ length: rows }, (_, i) => line(i + 1)).join(NL) + NL;
const lf = crlf.split(NL).join(String.fromCharCode(10));

const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'probe.js',
    _explorerFileContent: crlf,
    _explorerFileLanguage: 'javascript',
    _explorerEdit: null
};
sandbox.terminals[0] = pane;
sandbox.applyExplorerSourceTier(pane, crlf);

// Offsets resolved against the LF-normalised buffer the in-place editor keeps,
// then stamped as this pane's result — exactly what a stale range set is.
const needle = 'const s';
const stale = [];
let at = lf.indexOf(needle);
while (at !== -1) { stale.push({ start: at, end: at + needle.length }); at = lf.indexOf(needle, at + 1); }

const state = sandbox.ensureExplorerSearchState(pane);
state.query = needle;
state.resultQuery = needle;
state.ranges = stale;
state.resultContent = lf;

const onScreen = sandbox.explorerSourceSearchRangesOnScreen(0, pane);

// Paint the stale set directly to show what it would have done to the rows.
sandbox.renderExplorerSource(0, stale);
const painted = [...code.innerHTML.matchAll(
    /data-explorer-line="(\d+)"[\s\S]*?<code[^>]*>([\s\S]*?)<\/code>/g
)].map(match => {
    const cell = match[2];
    const mark = cell.indexOf('<mark');
    if (mark === -1) return null;
    const before = cell.slice(0, mark).replace(/<[^>]*>/g, '')
        .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
    return before.length;
}).filter(column => column !== null);

console.log(JSON.stringify({
    staleCount: stale.length,
    onScreen: onScreen.length,
    driftedColumns: painted.filter(column => column !== 0).length
}));
"""

class ExplorerSourceFrameTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _viewer(self) -> str:
        return self._static("js/explorer-viewer.js")

    def _run_node(self, harness: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(VIEWER_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail("node harness failed:" + chr(10) + completed.stderr)
        return json.loads(completed.stdout)

    def test_search_ranges_never_outlive_the_buffer_they_address(self):
        """A range set is offsets into one exact string, so it dies with it.

        The in-place editor normalizes CRLF to LF for its draft while the file
        keeps its own endings, so the two buffers differ by one character per
        line. Keying cached ranges on the query alone let a set resolved
        against one be painted onto rows built from the other, putting every
        mark a line-count of characters away from its match — a highlight that
        walked across each row and wrapped at the row length.
        """
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "stale.js"
            script.write_text(STALE_RANGE_HARNESS, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(script), str(TIERS_JS), str(REPAINT_JS),
                 str(VIEWER_JS), str(TABS_JS)],
                capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout.strip().splitlines()[-1])

        # The set really is stale and really would have drifted: every mark
        # after the first lands away from column 0 on rows built from CRLF.
        self.assertEqual(rendered["staleCount"], 40)
        self.assertGreater(
            rendered["driftedColumns"], 20,
            "the harness must reproduce the drift it is guarding against",
        )
        # And the resolver refuses to hand those offsets to a render at all.
        self.assertEqual(
            rendered["onScreen"], 0,
            "ranges stamped with a different buffer must not reach the rows",
        )

    def test_source_panel_is_wrapped_in_a_fixed_frame(self):
        viewer = self._viewer()
        # The frame — not the scrollable view — carries the panel hook and the
        # panel class, so panel show/hide and scroll capture/restore address
        # the frame and whatever is docked beside the text hides with it.
        self.assertIn(
            '<div class="explorer-source-frame explorer-editor-panel" '
            'data-explorer-file-panel="source"',
            viewer,
        )
        # Every existing `explorer-code-N` lookup (15+ across viewer and
        # editor) still resolves the inner scrollable view, which no longer
        # carries the panel hook or class.
        self.assertIn(
            '<div class="explorer-source-view" id="explorer-code-${index}"></div>',
            viewer,
        )

    @unittest.skipUnless(NODE, "Node.js is required for scroll-target tests")
    def test_panel_scroll_target_sees_through_the_source_frame(self):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(SCROLL_TARGET_HARNESS, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(script_path), str(VIEWER_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        resolved = json.loads(completed.stdout)

        # Without the source branch, scroll capture/restore would read the
        # overflow:hidden frame and pin every restored file to the top.
        self.assertEqual(resolved["readOnly"], "view")
        # Under the in-place editor's highlight overlay the textarea is
        # overflow:hidden and exactly as tall as its content, so the view goes
        # on scrolling both layers — reading the textarea would capture a
        # permanent zero and lose the position on every save.
        self.assertEqual(resolved["overlay"], "view")
        # Overlay stood down: the full-height textarea is the real scroller.
        self.assertEqual(resolved["bareEditor"], "view.explorer-source-editor")
        # A frame with no inner view falls back to the frame itself, and the
        # diff panel keeps its own inner-scroller behaviour.
        self.assertEqual(resolved["frameOnly"], "panel")
        self.assertEqual(resolved["diff"], "diffContent")
        self.assertEqual(resolved["missing"], "null")

    def test_source_frame_css_keeps_the_frame_fixed(self):
        css = self._static("css/terminals.css")
        self.assertIn(".explorer-source-frame {", css)
        block = css[css.index(".explorer-source-frame {"):]
        block = block[: block.index("}")]
        # Grid with a fixed side lane, and the frame itself never scrolls —
        # the inner .explorer-source-view keeps `overflow: auto`.
        self.assertIn("display: grid;", block)
        # The lane is *reserved*, not `auto`: an auto track collapses to zero
        # whenever the overview column stands down (the in-place editor, an
        # empty file, the large-file tier), which widened the text by the
        # ruler width and re-wrapped every line of it on the way in and back
        # again on the way out.
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) var(--explorer-overview-ruler-width);",
            block,
        )
        self.assertNotIn("grid-template-columns: minmax(0, 1fr) auto;", block)
        self.assertIn("overflow: hidden;", block)
        # Equal specificity with `.explorer-editor-panel { overflow: auto }`,
        # so the override only wins by coming later in the stylesheet.
        self.assertLess(
            css.index(".explorer-editor-panel {"),
            css.index(".explorer-source-frame {"),
        )
        # The hidden-panel rule must still beat the frame's `display: grid`.
        self.assertIn(".explorer-editor-panel[hidden] {", css)

    @staticmethod
    def _css_block(css: str, selector: str, contains: str = "") -> str:
        """The first block for ``selector`` that carries ``contains``.

        A selector can head more than one rule — ``.explorer-diff-content``
        also appears as one half of the source-font custom-property host — so
        the marker picks out the rule being asserted on.
        """
        start = 0
        while True:
            start = css.index(selector, start)
            block = css[start : css.index("}", start)]
            if contains in block:
                return block
            start += len(selector)

    def test_every_file_panel_reserves_the_same_overview_lane(self):
        """Source, Preview and Diff end their scrollers at the same x.

        Source's scroller stops at the overview column, while Preview and Diff
        are their own scrollers spanning the whole pane — so every switch
        between views moved the visible scrollbar sideways by the ruler width
        and re-wrapped the text to a different measure. Both reserve the
        identical lane inside their own box, from the one declaration of that
        width.
        """
        css = self._static("css/terminals.css")
        # One declaration of the lane's width, on the parent every file panel
        # shares — never restated as a literal beside any of them.
        body = self._css_block(css, ".explorer-editor-body {")
        self.assertIn("--explorer-overview-ruler-width:", body)

        # A transparent border reserves the lane inside the scroller, so the
        # bar lands where Source's does; the gradient paints that reserved
        # strip as the overview column's own chrome (1 px separator over the
        # bar background) instead of leaving a bare seam.
        panels = {
            ".explorer-markdown-preview {": "",
            ".explorer-diff-content {": "overflow-y: auto;",
        }
        for selector, marker in panels.items():
            with self.subTest(selector=selector):
                block = self._css_block(css, selector, marker)
                self.assertIn(
                    "border-right: var(--explorer-overview-ruler-width) solid transparent;",
                    block,
                )
                self.assertIn("background-origin: border-box;", block)
                self.assertIn(
                    "background-size: var(--explorer-overview-ruler-width) 100%;", block
                )
                self.assertIn("var(--explorer-row-border) 0 1px", block)
                self.assertIn("var(--explorer-bar-bg) 1px", block)
                # Guardrail 7: the lane's width and both colours are tokens.
                self.assertNotIn("14px", block)

    def test_every_scroller_reserves_its_scrollbar_gutter(self):
        """The bar appearing must not re-wrap the text under it.

        A fold, a find that changes the row set and a tier switch can each
        make the vertical scrollbar come and go; without a stable gutter that
        alone reflows every line.
        """
        css = self._static("css/terminals.css")
        scrollers = {
            ".explorer-source-view {": "overflow: auto;",
            ".explorer-markdown-preview {": "",
            ".explorer-diff-content {": "overflow-y: auto;",
        }
        for selector, marker in scrollers.items():
            with self.subTest(selector=selector):
                block = self._css_block(css, selector, marker)
                self.assertIn("scrollbar-gutter: stable;", block)

    def test_document_tokens_are_memoized_per_pane(self):
        viewer = self._viewer()
        self.assertIn(
            "function explorerHighlightDocumentLinesCached(pane, content, normalizedLanguage)",
            viewer,
        )
        cached = viewer[
            viewer.index("function explorerHighlightDocumentLinesCached"):
            viewer.index("function explorerRenderHighlightedRuns")
        ]
        # A hit requires the same content and the same normalized language;
        # a miss tokenizes once and stores all three.
        self.assertIn(
            "cache.content === content && cache.language === normalizedLanguage",
            cached,
        )
        self.assertIn(
            "pane._explorerHighlightCache = { content, language: normalizedLanguage, lines };",
            cached,
        )

    @unittest.skipUnless(NODE, "Node.js is required for highlight-failure tests")
    def test_a_failed_highlight_job_recolours_the_buffer_that_was_open(self):
        """A worker that dies must not leave one file grey for as long as it is open.

        The pending sentinel is the deliberate first paint while a job runs:
        plain escaped text, and pointedly *not* the per-line fallback lexer,
        because on a 1.5 MiB minified line that lexer is the long main-thread
        task the worker exists to avoid. On failure that sentinel used to be
        cached as a resting state — nothing re-rendered, and every later ask
        read the cached miss straight back as the sentinel — so the file that
        was open when the worker died stayed wholly uncoloured. The next file
        recovered, because the pool disables itself and ``canHighlight()`` then
        routes it down the synchronous path: one failure, two different answers
        for the same file depending on when it was opened.

        The failure now resolves to real tokens on this thread — exactly what a
        page with no worker support does — and the rows on screen are rebuilt
        to show them.
        """
        failure = self._run_node(HIGHLIGHT_FAILURE_HARNESS)

        # The first paint is still the plain sentinel, tokenizing nothing.
        self.assertTrue(failure["pendingAtFirst"])
        self.assertEqual(failure["tokenizeBeforeFailure"], 0)

        # The failure tokenizes here, once, and rebuilds the rows to show it.
        self.assertEqual(failure["tokenizeAfterFailure"], 1)
        self.assertEqual(failure["renderCalls"], 1)
        # The repaint policy would otherwise skip: the content did not move.
        self.assertTrue(failure["markedStale"])

        # The buffer is coloured from here on, and the answer is cached — no
        # sentinel, no second tokenization, no second doomed job.
        self.assertFalse(failure["stillPending"])
        self.assertEqual(failure["recolouredLines"], 1)
        self.assertEqual(failure["tokenizeAfterRepeat"], 1)

    @unittest.skipUnless(NODE, "Node.js is required for line-record cache tests")
    def test_line_record_cache_is_sized_by_the_panes_and_is_given_back(self):
        """Two entries is right per pane, and the cache is shared by all of them.

        The records exist so that a single find keystroke stops walking the
        document three times over. Two slots is the correct number for *one*
        pane — the Source rows and the editor's draft are both live during an
        edit and they are different strings — but the cache is module-level, so
        a flat two meant a workspace with three explorer file panes evicted on
        every cross-pane call: the optimisation stopped applying in exactly the
        configuration whose total cost is highest.

        And an LRU only evicts on insert, so with the last pane closed there
        was nothing left to ask a question and whichever documents it had last
        answered about — for a 4 MiB file, ~100k record objects plus the
        string — stayed resident for the life of the page.
        """
        cache = self._run_node(LINE_RECORD_CACHE_HARNESS)

        # One pane, one document: a repeat ask is a hit, as it always was.
        self.assertEqual(cache["onePane"][0], True)
        # Three panes, three documents: every one of them is still a hit.
        self.assertEqual(cache["threePanes"], [True, True, True])
        # The pair a pane holds while editing survives the panes beside it.
        self.assertTrue(cache["editingKeepsBoth"])
        # Released on teardown, and only then.
        self.assertTrue(cache["beforeRelease"])
        self.assertFalse(cache["afterRelease"])

    def test_source_render_reuses_the_cached_token_map(self):
        viewer = self._viewer()
        render = viewer[
            viewer.index("function renderExplorerSource(index, searchRanges = [])"):
            viewer.index("function explorerPreviewBlockLanguage(code)")
        ]
        # Search keystrokes, wrap toggles and Markdown folds all go through the
        # async-aware cache gate. It returns the memoized map, one shared
        # pending worker job, or the synchronous small-file result — never a
        # second whole-document tokenization for the same identity.
        self.assertIn("explorerHighlightLinesForRender(", render)
        self.assertIn("index, pane, content, normalizeExplorerLanguage(language)", render)
        self.assertIn("highlightedLines\n        );", render)

    @unittest.skipUnless(NODE, "Node.js is required for token-map tests")
    def test_source_lines_renderer_honours_a_passed_in_token_map(self):
        """Only an *absent* token map tokenizes inline.

        An explicit ``null`` is a cached miss — an unsupported language, an
        oversized file, a Highlight.js failure — and re-tokenizing on it would
        pay the whole-document cost on every single render. Executed rather
        than read: the whole-document pass is replaced by a spy, so what the
        renderer does with each of the three argument states is observable.
        """
        rendered = self._run_node(TOKEN_MAP_HARNESS)

        # A supplied map is used as given, and nothing is tokenized.
        self.assertEqual(rendered["supplied"]["tokenizeCalls"], 0)
        self.assertIn('<span class="hljs-keyword">def</span>', rendered["supplied"]["html"])
        # A cached miss renders through the per-line fallback lexer, silently.
        self.assertEqual(rendered["cachedMiss"]["tokenizeCalls"], 0)
        self.assertNotIn("hljs-keyword", rendered["cachedMiss"]["html"])
        self.assertIn('<span class="explorer-code-keyword">def</span> spam', rendered["cachedMiss"]["html"])
        # Omitted: this caller has no cache of its own, so it tokenizes here.
        self.assertEqual(rendered["omitted"]["tokenizeCalls"], 1)
        self.assertEqual(
            rendered["omitted"]["tokenizeArgs"], ["def spam" + chr(10), "python"]
        )


if __name__ == "__main__":
    unittest.main()
