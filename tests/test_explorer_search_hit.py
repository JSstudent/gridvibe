"""Clicking a repository-search hit, driven through the real reveal.

A hit names an exact source location, and reaching it is two DOM reads: the
row is scrolled to and flashed, and the matched substring on it is tinted. Both
asked for the row by `[data-explorer-line]` in the same task that opened the
file -- which is fine for a file the Source view renders in one pass, and
wrong for every file above ~4,000 rows, where the rows are emitted over
animation frames and `openExplorerFile()` returns once the build has *started*.
On those files both reads got null and returned silently, so the reader was
left at the top of the file the render had just painted, for every hit in it.

Nothing that read the source caught it: the functions existed, the selector was
right, and the small-file path they were tested on worked. These tests run the
real search module against a stubbed page and complete the build afterwards,
so a reveal that reads the rows too early fails here rather than in the pane.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
ICONS_JS = STATIC_JS / "terminal-icons.js"
# The render queue the reveal has to go through is the viewer's own, so the
# real one is loaded rather than stubbed: this is a test of that ordering.
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
SEARCH_JS = STATIC_JS / "explorer-search.js"
NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

function makeClassList() {
    const set = new Set();
    return {
        set,
        toggle(name, on) { if (on) set.add(name); else set.delete(name); },
        contains: name => set.has(name),
        add(name) { set.add(name); },
        remove(name) { set.delete(name); }
    };
}

/* One rendered source row per line a hit in these tests points at. */
function makeRow(line) {
    return {
        line,
        classList: makeClassList(),
        scrolls: 0,
        scrollOptions: null,
        scrollIntoView(options) { this.scrolls += 1; this.scrollOptions = options; },
        querySelector: () => null
    };
}

const ROWS = new Map();
[1, 12, 4200, 7100].forEach(line => ROWS.set(line, makeRow(line)));

/* Whether the build has put its rows in the document yet. */
let rowsRendered = false;

const card = {
    querySelector(selector) {
        const match = /^\[data-explorer-line="(\d+)"\]$/.exec(selector);
        if (!match || !rowsRendered) {
            return null;
        }
        return ROWS.get(Number(match[1])) || null;
    }
};

const sandbox = {
    console,
    document: {
        getElementById: id => (id === 'tc-0' ? card : null),
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
    escHtml: value => String(value == null ? '' : value),
    notePanePresentationChanged: () => {},
    applyExplorerSearch: () => {},
    applyExplorerChangeMarks: () => {},
    updateExplorerFilesystemRootRevision: () => {}
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
process.argv.slice(2).forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});

/* -- Seams --
   The open is stubbed because a hit's reveal is not a test of the file GET;
   what matters is the state it leaves behind -- the pane's path, and whether a
   build is still in flight -- and what runs after it. The paint is stubbed
   because its internals are the CSS Custom Highlight API over a real text
   tree; *when* it runs and against which line is what this is about. The
   scroll is the real one, so the DOM read that used to come up empty is the
   read under test. */
const calls = [];
let openLeavesBuildRunning = false;

sandbox.openExplorerFile = async (index, path) => {
    calls.push('open');
    const pane = sandbox.terminals[index];
    pane._explorerFilePath = path;
    pane._explorerSourceRenderJob = openLeavesBuildRunning ? { at: 0 } : null;
    return true;
};
sandbox.clearExplorerSearch = () => { calls.push('clearSearch'); };
sandbox.setExplorerFileView = () => { calls.push('setView'); };

const paints = [];
sandbox.paintExplorerSearchHitMatches = (index, line, match) => {
    calls.push('paint');
    paints.push({ index, line, match });
};

const MATCH = { line: 4200, text: 'explorerSourceRenderJob', ranges: [[0, 23]] };

function makePane() {
    return { _session: {}, _explorerFilePath: '', _explorerSourceRenderJob: null };
}

function rowState(line) {
    const row = ROWS.get(line);
    return {
        scrolls: row.scrolls,
        block: row.scrollOptions ? row.scrollOptions.block : null,
        flashed: row.classList.contains('explorer-source-line-flash')
    };
}

function resetRows() {
    ROWS.forEach(row => {
        row.scrolls = 0;
        row.scrollOptions = null;
        row.classList.remove('explorer-source-line-flash');
    });
}

/* The build the open started reaches its last row: the job clears and the
   pane's reader queue is flushed, exactly as explorerFinishSourceRender does. */
function finishBuild(pane) {
    pane._explorerSourceRenderJob = null;
    rowsRendered = true;
    sandbox.explorerFlushSourceRenderCallbacks(pane);
}

const results = {};

(async () => {
    /* 1. The reported case as the reader meets it: a first paint of a file big
          enough to be emitted over frames. The row the hit names does not
          exist when the open returns. */
    resetRows();
    calls.length = 0;
    paints.length = 0;
    rowsRendered = false;
    openLeavesBuildRunning = true;
    const paneA = makePane();
    sandbox.terminals[0] = paneA;
    await sandbox.activateExplorerSearchHit(0, 'web/static/js/explorer-viewer.js', 7100, { match: MATCH });
    results.firstPaintDuringOpen = { row: rowState(7100), paints: paints.length };
    finishBuild(paneA);
    results.firstPaintAfterBuild = { row: rowState(7100), paints: paints.length };

    /* 2. The same wait with rows already on screen: a build replacing the rows
          the reader is looking at reaches the hit's line by selector long
          before it is the line the finished document will hold, so the reveal
          has to be behind the build and not merely behind the DOM. */
    resetRows();
    calls.length = 0;
    paints.length = 0;
    rowsRendered = true;
    const paneB = makePane();
    sandbox.terminals[0] = paneB;
    await sandbox.activateExplorerSearchHit(0, 'web/static/js/explorer-viewer.js', 4200, { match: MATCH });
    results.slicedDuringOpen = { row: rowState(4200), paints: paints.length, calls: calls.slice() };
    finishBuild(paneB);
    results.slicedAfterBuild = {
        row: rowState(4200),
        paints: paints.slice(),
        calls: calls.slice(),
        otherRowsScrolled: [1, 12, 7100].map(line => rowState(line).scrolls)
    };

    /* 3. A file small enough to render in one pass has no build to wait for,
          so the reveal still lands in the activating task. */
    resetRows();
    calls.length = 0;
    paints.length = 0;
    openLeavesBuildRunning = false;
    const paneC = makePane();
    sandbox.terminals[0] = paneC;
    await sandbox.activateExplorerSearchHit(0, 'web/api.py', 12, { match: MATCH });
    results.onePass = { row: rowState(12), paints: paints.length, calls: calls.slice() };

    /* 4. The slot changed hands while the build ran, so the pane that asked is
          not the pane in this slot any more. */
    resetRows();
    paints.length = 0;
    openLeavesBuildRunning = true;
    const paneD = makePane();
    sandbox.terminals[0] = paneD;
    await sandbox.activateExplorerSearchHit(0, 'web/static/js/explorer-viewer.js', 4200, { match: MATCH });
    sandbox.terminals[0] = makePane();
    finishBuild(paneD);
    results.replacedSlot = { row: rowState(4200), paints: paints.length };

    /* 5. Same pane, different file: the hit's line belongs to a document the
          reader has already left. */
    resetRows();
    paints.length = 0;
    const paneE = makePane();
    sandbox.terminals[0] = paneE;
    await sandbox.activateExplorerSearchHit(0, 'web/static/js/explorer-viewer.js', 4200, { match: MATCH });
    paneE._explorerFilePath = 'web/static/js/explorer-tabs.js';
    finishBuild(paneE);
    results.navigatedAway = { row: rowState(4200), paints: paints.length };

    process.stdout.write(JSON.stringify(results, null, 2));
})();
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer search-hit tests")
class ExplorerSearchHitRevealTestCase(unittest.TestCase):
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
                    str(SEARCH_JS),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(f"node harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_a_hit_in_a_frame_sliced_file_is_revealed_when_the_rows_exist(self):
        # The whole of the reported defect: with the rows not yet built there
        # was nothing to scroll to, so the reader stayed at the top of the file
        # for every hit in it. The reveal now waits for the build.
        self.assertEqual(self.results["firstPaintDuringOpen"]["row"]["scrolls"], 0)
        self.assertEqual(self.results["firstPaintDuringOpen"]["paints"], 0)
        self.assertEqual(self.results["firstPaintAfterBuild"]["row"]["scrolls"], 1)
        self.assertTrue(self.results["firstPaintAfterBuild"]["row"]["flashed"])
        self.assertEqual(self.results["firstPaintAfterBuild"]["paints"], 1)

    def test_the_wait_is_on_the_build_and_not_on_the_row_appearing(self):
        # A build replacing rows the reader is looking at leaves the hit's line
        # reachable by selector throughout, so a reveal that only checked for
        # the row would scroll to the outgoing document.
        during = self.results["slicedDuringOpen"]
        self.assertEqual(during["row"]["scrolls"], 0)
        self.assertEqual(during["paints"], 0)
        # The file was opened and the Source panel selected all the same: only
        # the two reads that need the rows are deferred.
        self.assertEqual(during["calls"], ["open", "clearSearch", "setView"])
        after = self.results["slicedAfterBuild"]
        self.assertEqual(after["row"]["scrolls"], 1)
        self.assertEqual(after["row"]["block"], "center")
        self.assertTrue(after["row"]["flashed"])
        # The panel is shown before the row inside it is scrolled to.
        self.assertEqual(after["calls"], ["open", "clearSearch", "setView", "paint"])
        # Exactly the hit's line, and the hit's own match record with it.
        self.assertEqual([paint["line"] for paint in after["paints"]], [4200])
        self.assertEqual(after["paints"][0]["match"]["ranges"], [[0, 23]])
        self.assertEqual(after["otherRowsScrolled"], [0, 0, 0])

    def test_a_one_pass_file_is_still_revealed_in_the_activating_task(self):
        # Every smaller file renders in one pass and has always worked; the
        # queue is immediate when no build is in flight, so that ordering is
        # unchanged rather than newly deferred by a frame.
        self.assertEqual(self.results["onePass"]["row"]["scrolls"], 1)
        self.assertEqual(self.results["onePass"]["row"]["block"], "center")
        self.assertEqual(self.results["onePass"]["paints"], 1)
        self.assertEqual(
            self.results["onePass"]["calls"],
            ["open", "clearSearch", "setView", "paint"],
        )

    def test_a_slot_that_changed_hands_is_not_scrolled(self):
        # A grid slot is not an identity: a group switch rehouses it while the
        # build runs, and the pane sitting there now never asked for this hit.
        self.assertEqual(self.results["replacedSlot"]["row"]["scrolls"], 0)
        self.assertEqual(self.results["replacedSlot"]["paints"], 0)

    def test_a_pane_showing_another_file_is_not_scrolled(self):
        # The line number belongs to the file the hit named. Applying it to
        # whatever the pane opened afterwards would move the reader to an
        # unrelated line and tint a string that is not there.
        self.assertEqual(self.results["navigatedAway"]["row"]["scrolls"], 0)
        self.assertEqual(self.results["navigatedAway"]["paints"], 0)


if __name__ == "__main__":
    unittest.main()
