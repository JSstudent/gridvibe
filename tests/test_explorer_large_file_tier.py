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
const body = Array.from({ length: lines }, (_, i) => 'line ' + i).join(String.fromCharCode(10))
    + String.fromCharCode(10);

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
    lossless: chunkBodies.join('').replace(/&amp;/g, '&') === body
}));
"""


@unittest.skipUnless(NODE, "Node.js is required for large-file tier tests")
class ExplorerLargeFileTierTestCase(unittest.TestCase):
    def _render(self, lines: int):
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
        self.assertTrue(rendered["lossless"], "the chunks must reproduce the file")

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
        self.assertIn("Download and Edit still work.", notice)
        # A control that cannot answer is not offered.
        self.assertFalse(rendered["allowsFind"])

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
