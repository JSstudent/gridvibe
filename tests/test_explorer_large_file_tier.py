"""What the large-file tier actually puts on screen.

``explorer-tiers.js`` decides *whether* a file degrades and *what the notice
says*; those rules are executed in ``test_explorer_tiers.py``. This file tests
the other half — that the viewer's adapter honours the decision — by running
``renderExplorerSource()`` in Node against a DOM stub, the same way
``test_explorer_editor_group_switch.py`` runs the editor.

The contracts:

* above the tier no per-line row is built at all, however many lines there are;
* the notice is present, is a ``role="status"`` element inside the pane rather
  than the launcher's global banner, and says plainly that Find is unavailable;
* nothing is lost — the chunks reproduce the file;
* a file below the tier still renders exactly the rows it always did.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TIERS_JS = STATIC_JS / "explorer-tiers.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
# The Source render reads the active tab's Markdown folds, and the tab records
# live in explorer-tabs.js — the page loads the pair together, so does this.
TABS_JS = STATIC_JS / "explorer-tabs.js"

NODE = shutil.which("node")

HARNESS = """
const fs = require('fs');
const vm = require('vm');

// The chunks arrive over animation frames, so the stub has to be a real
// element for the host lookup and a real queue for the frames — otherwise the
// harness would silently measure the one-pass fallback instead of the paint
// the page performs.
function makePlainHost() {
    return {
        className: 'explorer-source-plain',
        _html: '',
        get innerHTML() { return this._html; },
        set innerHTML(value) { this._html = value; },
        insertAdjacentHTML(position, markup) { this._html += markup; }
    };
}

let plainHost = null;

const code = {
    id: 'explorer-code-0',
    _raw: '',
    get innerHTML() {
        return plainHost
            ? code._raw.replace(
                '<div class="explorer-source-plain"></div>',
                '<div class="explorer-source-plain">' + plainHost.innerHTML + '</div>'
            )
            : code._raw;
    },
    set innerHTML(value) {
        code._raw = value;
        plainHost = value.includes('explorer-source-plain') ? makePlainHost() : null;
    },
    dataset: {},
    style: { setProperty() {}, removeProperty() {} },
    classList: { add() {}, remove() {}, contains: () => false, toggle: () => false },
    querySelector: selector => (
        selector === '.explorer-source-plain' ? plainHost : null
    ),
    querySelectorAll: () => [],
    addEventListener() {},
    setAttribute() {}
};

const frames = [];
const runFrames = () => {
    let ran = 0;
    while (frames.length) {
        frames.shift()();
        ran += 1;
    }
    return ran;
};

/* The commit-diff view's own two elements. It never builds source rows, so a
   settable innerHTML and the handful of members it touches are the whole of
   what it reads. */
const viewer = { innerHTML: '', querySelector: () => null, querySelectorAll: () => [] };
const list = {
    id: 'explorer-list-0',
    classList: { add() {}, remove() {}, contains: () => false, toggle: () => false },
    querySelector: () => null,
    querySelectorAll: () => []
};

const sandbox = {
    console,
    document: {
        getElementById: id => (
            id === 'explorer-code-0' ? code : id === 'explorer-list-0' ? list : null
        ),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        createElement: () => ({ innerHTML: '', className: '', appendChild() {} }),
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {},
        setTimeout,
        clearTimeout,
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        requestAnimationFrame: callback => frames.push(callback)
    },
    navigator: {},
    performance: { now: () => Date.now() },
    setTimeout,
    clearTimeout,
    requestAnimationFrame: callback => frames.push(callback),
    terminals: [],
    sessionIds: [],
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
};
sandbox.globalThis = sandbox;
// Owned by explorer-overview.js; repaints marks onto fresh rows.
sandbox.applyExplorerChangeMarks = () => {};
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
// The page hangs the tier policy on `window`; the module's UMD wrapper put it
// on the sandbox global instead, so bridge the two the way a browser does.
sandbox.window.GridVibeExplorerTiers = sandbox.GridVibeExplorerTiers;

const lines = Number(process.argv[5]);
/* 'blanks' puts an empty line on every chunk cut. sourceChunks() cuts every
   SOURCE_LARGE_CHUNK_LINES lines, so a blank line at a multiple of 5000 is the
   first character of the following chunk — which is the one position a <pre>
   start tag can eat. */
const fixture = process.argv[6] || 'plain';
const body = Array.from(
    { length: lines },
    (_, i) => (fixture === 'blanks' && i > 0 && i % 5000 === 0 ? '' : 'line ' + i)
).join(String.fromCharCode(10)) + String.fromCharCode(10);

const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'huge.log',
    _explorerFileContent: body,
    _explorerFileLanguage: '',
    _explorerEdit: null
};
sandbox.terminals[0] = pane;
sandbox.applyExplorerSourceTier(pane, body);
sandbox.renderExplorerSource(0);
// What the pane shows before a single frame has run, and how many frames the
// paint took to finish.
const beforeFrames = code.innerHTML;
const frameCount = runFrames();

if (fixture === 'commitdiff') {
    /* Everything the commit-diff renderer collaborates with, stubbed to the
       shape it expects. The renderer itself is the real one: the contract
       under test is what it leaves on the pane, not what its neighbours do. */
    /* Lives in terminal-icons.js, which the page loads beside the viewer. Its
       top-level `const` does not cross a vm.runInContext boundary, so the
       header template resolves the name through the sandbox global instead —
       the header itself is the real one. */
    sandbox.EXPLORER_LINE_WRAP_ICON = '<svg aria-hidden="true"></svg>';
    sandbox.explorerEnsureViewerShell = () => viewer;
    sandbox.explorerAssignOpenTab = () => ({ id: 'commit-tab' });
    [
        'clearExplorerDirectorySearchControls', 'cancelExplorerSearch',
        'explorerCaptureActiveTabView', 'setExplorerFileWatchBaseline',
        'renderExplorerPathBreadcrumb', 'wireExplorerLineWrapControl',
        'wireExplorerSearchControls', 'applyExplorerEditorFontSize',
        'applyExplorerSourceFontToElement', 'loadExplorerDiff',
        'renderExplorerTabStrip', 'syncExplorerGitActiveRows'
    ].forEach(name => { sandbox[name] = () => {}; });

    const allowsFindOnTheLargeFile = sandbox.explorerPaneAllowsFind(pane);
    const rendered = sandbox.renderExplorerCommitDiffFile(
        0, 'src/app.js', '0123456789abcdef0123456789abcdef01234567'
    );
    console.log(JSON.stringify({
        rendered,
        allowsFindOnTheLargeFile,
        tierBefore: 'large',
        tierAfter: pane._explorerSourceTier,
        allowsFindAfter: sandbox.explorerPaneAllowsFind(pane),
        // The header renders the bar unconditionally, so the tier is the only
        // thing standing between the reader and a control that does nothing.
        rendersFindBar: viewer.innerHTML.includes('data-explorer-search-input="0"'),
        mode: pane._explorerMode,
        content: pane._explorerFileContent
    }));
    return;
}

const html = code.innerHTML;
const chunkBodies = [...html.matchAll(
    /<pre class="explorer-source-chunk">([\\s\\S]*?)<\\/pre>/g
)].map(match => match[1]);
console.log(JSON.stringify({
    tier: pane._explorerSourceTier,
    metrics: pane._explorerSourceMetrics,
    allowsFind: sandbox.explorerPaneAllowsFind(pane),
    rowCount: (html.match(/class="explorer-source-line"/g) || []).length,
    linesBlocks: (html.match(/class="explorer-source-lines"/g) || []).length,
    chunkCount: chunkBodies.length,
    frameCount,
    // The notice is up immediately; the chunks are not.
    noticeBeforeFrames: beforeFrames.includes('explorer-source-tier-notice'),
    chunksBeforeFrames: (
        beforeFrames.match(/class="explorer-source-chunk"/g) || []
    ).length,
    noticeCount: (html.match(/class="explorer-source-tier-notice"/g) || []).length,
    hasStatusRole: html.includes('<div class="explorer-source-tier-notice" role="status">'),
    notice: (html.match(/<div class="explorer-source-tier-notice"[\\s\\S]*?<\\/div>/) || [''])[0],
    // Chunks are escaped markup; unescape the one entity a plain log can grow.
    // What the *markup* carries. Not the contract — a chunk that reproduces
    // the file as a string can still lose a line once parsed.
    losslessMarkup: chunkBodies.join('').replace(/&amp;/g, '&') === body,
    // What the pane actually shows. There is no DOM here to parse with, so the
    // one parser rule that applies is modelled explicitly: the HTML fragment
    // parser drops a single U+000A immediately after a `<pre>` start tag, and
    // `innerHTML`/`insertAdjacentHTML` both run that algorithm. Every chunk is
    // put through it before the file is reassembled.
    lossless: chunkBodies
        .map(chunkBody => (chunkBody.startsWith(String.fromCharCode(10))
            ? chunkBody.slice(1)
            : chunkBody))
        .join('')
        .replace(/&amp;/g, '&') === body,
    chunksLeadingNewline: chunkBodies.filter(
        chunkBody => chunkBody.startsWith(String.fromCharCode(10))
    ).length
}));
"""


@unittest.skipUnless(NODE, "Node.js is required for large-file tier tests")
class ExplorerLargeFileTierTestCase(unittest.TestCase):
    def _render(self, lines: int, fixture: str = "plain"):
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "harness.js"
            script.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(
                [
                    NODE,
                    str(script),
                    str(TIERS_JS),
                    str(VIEWER_JS),
                    str(TABS_JS),
                    str(lines),
                    fixture,
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_above_the_tier_no_row_is_built_at_all(self):
        rendered = self._render(40000)

        self.assertEqual(rendered["tier"], "large")
        self.assertEqual(rendered["metrics"]["lines"], 40000)
        self.assertEqual(rendered["metrics"]["rows"], 40001)
        # The point of the tier: DOM stops tracking the line count.
        self.assertEqual(rendered["rowCount"], 0)
        self.assertEqual(rendered["linesBlocks"], 0)
        self.assertEqual(rendered["chunkCount"], 8)
        self.assertTrue(
            rendered["lossless"], "the parsed chunks must reproduce the file"
        )

    def test_a_blank_line_on_a_chunk_cut_survives_the_parser(self):
        """The one line a `<pre>` chunk can silently eat.

        ``sourceChunks()`` cuts *after* a newline, so a chunk begins with the
        next line's first character — and when that line is blank, the chunk
        begins with a newline. The HTML fragment parser drops a single U+000A
        immediately following a ``<pre>`` start tag, which is exactly where
        that one lands, so the blank line disappeared from the pane and the
        view stopped being byte-faithful to the file.

        The cure is a sacrificial newline of our own for the parser to eat, so
        the markup is deliberately *not* lossless while the parsed text is.
        Asserting only on the markup string is what let this through.
        """
        rendered = self._render(40000, fixture="blanks")

        self.assertEqual(rendered["tier"], "large")
        self.assertEqual(rendered["chunkCount"], 8)
        # The contract first: what the reader ends up looking at.
        self.assertTrue(
            rendered["lossless"],
            "a blank line landing on a chunk cut must survive the parser",
        )
        # And the shape that buys it — every chunk carries the sacrificial
        # newline, so the markup no longer reproduces the file on its own and
        # must not be asserted to.
        self.assertEqual(rendered["chunksLeadingNewline"], 8)
        self.assertFalse(rendered["losslessMarkup"])

    def test_the_chunks_arrive_over_frames_behind_the_notice(self):
        """Not building rows is not the same as not blocking the thread.

        Escaping the whole buffer and handing the parser one string that size
        is the same uninterruptible task the tier exists to remove, so the
        chunks are paced exactly as the rows are: the notice is on screen
        before any of them, and no frame carries the whole file.
        """
        rendered = self._render(40000)

        self.assertTrue(rendered["noticeBeforeFrames"])
        self.assertEqual(rendered["chunksBeforeFrames"], 0)
        self.assertGreater(rendered["frameCount"], 0)
        # Still every chunk, and still byte-for-byte the file.
        self.assertEqual(rendered["chunkCount"], 8)
        self.assertTrue(rendered["lossless"])

    def test_the_notice_explains_the_tier_and_states_find_plainly(self):
        rendered = self._render(40000)

        self.assertEqual(rendered["noticeCount"], 1)
        # In-pane status, not the launcher's one global banner: this describes a
        # state that lasts as long as the file is open, not an event.
        self.assertTrue(rendered["hasStatusRole"])
        notice = rendered["notice"]
        self.assertIn("Large file view", notice)
        self.assertIn("40,000 lines", notice)
        self.assertIn("Find is unavailable in this view.", notice)
        for turned_off in ("syntax highlighting", "line numbers", "change marks"):
            self.assertIn(turned_off, notice)
        self.assertIn("Download, Upload and Edit still work.", notice)
        # A control that cannot answer is not offered.
        self.assertFalse(rendered["allowsFind"])

    def test_a_commit_diff_does_not_inherit_the_previous_files_tier(self):
        """The tier is a pane field, and a commit diff is `file` mode.

        `renderExplorerCommitDiffFile()` clears the buffer and sets
        `_explorerMode = 'file'`, but it used to leave the outgoing file's tier
        standing — so a commit opened after a large file rendered its Find bar
        (that header renders it unconditionally) over a `large` tier, and
        `applyExplorerSearch()` then refused to serve it. The reader got a
        control that marked nothing and moved no counter, *and* lost the
        browser's own find with it: `focusExplorerSearch()` succeeds whenever
        the input exists, so the global Ctrl+F handler still called
        `preventDefault()`. It self-healed only on the next ordinary file open.
        """
        rendered = self._render(40000, fixture="commitdiff")

        self.assertTrue(rendered["rendered"])
        # The pane really was in the state that used to be inherited.
        self.assertFalse(rendered["allowsFindOnTheLargeFile"])

        # A commit diff has no buffer, so it is not a large file.
        self.assertEqual(rendered["mode"], "file")
        self.assertEqual(rendered["content"], "")
        self.assertEqual(rendered["tierAfter"], "full")

        # The bar this header always renders is now one the find will answer.
        self.assertTrue(rendered["rendersFindBar"])
        self.assertTrue(rendered["allowsFindAfter"])

    def test_below_the_tier_nothing_changes(self):
        rendered = self._render(500)

        self.assertEqual(rendered["tier"], "full")
        # 500 lines of text plus the trailing empty row the per-line renderer
        # always emits after the final newline. That row is why the tier counts
        # rows rather than the reader-facing line count: at the boundary the
        # two disagree, and the DOM is what the ceiling is protecting.
        self.assertEqual(rendered["metrics"]["lines"], 500)
        self.assertEqual(rendered["metrics"]["rows"], 501)
        self.assertEqual(rendered["rowCount"], rendered["metrics"]["rows"])
        self.assertEqual(rendered["linesBlocks"], 1)
        self.assertEqual(rendered["chunkCount"], 0)
        self.assertEqual(rendered["noticeCount"], 0)
        self.assertTrue(rendered["allowsFind"])


if __name__ == "__main__":
    unittest.main()
