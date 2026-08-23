"""Which panel Find is offered on, and who agrees about it.

One input in the file header serves Source, Preview and Diff. The large-file
tier turns Find off because that view has no per-line rows to point at — but
that verdict used to be applied to the whole pane, so the same file's **Diff**
tab lost its find bar too, even though the patch it shows is capped by the
backend's ``EXPLORER_GIT_DIFF_MAX_BYTES`` however large the file is.

The contracts executed here:

* the predicate answers ``True`` for ``diff`` while the source tier is
  ``large``, and ``False`` for ``source`` and ``preview`` — Preview shares
  Source's verdict because the preview find walks its whole subtree unbounded,
  which is the freeze the tier exists to remove;
* a real ``setExplorerFileView()`` round trip Source → Diff → Preview hides,
  shows and hides the search shell again;
* ``focusExplorerSearch()`` refuses on the two panels that cannot answer, so a
  control nobody can use never claims ``Ctrl+F`` from the browser's own find;
* the query survives being hidden, and Diff applies it.

The header markup itself (one shell, rendered whenever any panel could answer)
is covered in ``tests/test_api.py``.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
TIERS_JS = STATIC_JS / "explorer-tiers.js"
TABS_JS = STATIC_JS / "explorer-tabs.js"

NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

function classList() {
    const held = new Set();
    return {
        add: name => held.add(name),
        remove: name => held.delete(name),
        contains: name => held.has(name),
        toggle: (name, on) => (on ? held.add(name) : held.delete(name))
    };
}

function panel(view) {
    return {
        hidden: view !== 'source',
        dataset: { explorerFilePanel: view },
        classList: classList(),
        querySelector: () => null,
        querySelectorAll: () => []
    };
}

function tab(view, selected) {
    return {
        dataset: {
            explorerFileView: view,
            ...(view === 'diff' ? { explorerDiffToggle: '0' } : {})
        },
        attrs: { 'aria-selected': selected ? 'true' : 'false' },
        setAttribute(name, value) { this.attrs[name] = String(value); },
        getAttribute(name) { return this.attrs[name]; }
    };
}

const panels = { source: panel('source'), preview: panel('preview'), diff: panel('diff') };
const tabs = [tab('source', true), tab('preview', false), tab('diff', false)];
const body = { classList: classList() };

/* The header's find shell. `hidden` is the whole of what the sync owner may
   change: the shell is rendered once and never rebuilt on a panel switch, so
   an added-and-removed control would be the wrong shape to test. */
const searchShell = { hidden: false };
let focused = 0;
const searchInput = {
    value: '',
    dataset: {},
    focus() { focused += 1; },
    select() {},
    addEventListener() {}
};

const list = {
    id: 'explorer-list-0',
    classList: classList(),
    querySelector(selector) {
        if (selector === '.explorer-editor-body') return body;
        if (selector === '[data-explorer-file-view][aria-selected="true"]') {
            return tabs.find(entry => entry.attrs['aria-selected'] === 'true') || null;
        }
        return null;
    },
    querySelectorAll(selector) {
        if (selector === '[data-explorer-file-view]') return tabs;
        if (selector === '[data-explorer-file-panel]') return Object.values(panels);
        return [];
    }
};

const elements = new Map([
    ['explorer-list-0', list],
    ['explorer-diff-panel-0', panels.diff],
    ['explorer-diff-code-0', { querySelector: () => null }]
]);

const sandbox = {
    console,
    document: {
        getElementById: id => elements.get(id) || null,
        querySelector(selector) {
            if (selector === '[data-explorer-search="0"]') return searchShell;
            if (selector === '[data-explorer-search-input="0"]') return searchInput;
            const scoped = selector.match(
                /^#explorer-list-0 \[data-explorer-file-panel="([^"]+)"\]$/
            );
            if (scoped) return panels[scoped[1]] || null;
            return null;
        },
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {},
        setTimeout: () => 0,
        clearTimeout() {},
        requestAnimationFrame: () => 0,
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} }
    },
    navigator: {},
    // The below-the-tier step runs the real Source scan, which yields on a
    // time budget.
    performance: { now: () => Date.now() },
    setTimeout: () => 0,
    clearTimeout() {},
    requestAnimationFrame: () => 0,
    terminals: [],
    sessionIds: ['s0'],
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
sandbox.window.GridVibeExplorerTiers = sandbox.GridVibeExplorerTiers;

/* Everything the two functions under test collaborate with, stubbed to the
   shape it is called in. `markExplorerSearchInElement` stands in for the
   TreeWalker pass so a query that reaches the Diff branch is observable as a
   match count rather than as painted markup. */
const marks = [
    { classList: classList() },
    { classList: classList() }
];
sandbox.explorerActiveTab = () => ({ preferredMode: 'source', collapsedLines: new Set() });
sandbox.rememberExplorerPanelScroll = () => {};
sandbox.requestExplorerPanelScrollRestore = () => {};
sandbox.applyExplorerLineWrapState = () => {};
sandbox.ensureExplorerPreviewLoaded = () => {};
sandbox.loadExplorerDiff = () => {};
sandbox.renderExplorerSource = () => {};
sandbox.restoreExplorerPreview = () => null;
sandbox.renderExplorerDiff = () => {};
sandbox.cancelExplorerSearch = () => {};
sandbox.scheduleExplorerDiffScrollbarSync = () => {};
sandbox.whenExplorerSourceRendered = () => {};
sandbox.explorerRevealMarkdownSearchMatches = () => {};
let controlUpdates = [];
sandbox.updateExplorerSearchControls = (index, query, active, count) => {
    controlUpdates.push({ query, active, count });
};
sandbox.markExplorerSearchInElement = () => marks;

const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'huge.txt',
    _explorerFileLanguage: '',
    _explorerLastFileView: 'source',
    _explorerDiffLoaded: false,
    _explorerEdit: null
};
sandbox.terminals[0] = pane;

// 25,001 lines: over the row ceiling, so the tier is `large` exactly as the
// manual reproduction's huge.txt is.
const body_text = Array.from({ length: 25001 }, (_, i) => 'line ' + i).join('\n');
sandbox.applyExplorerSourceTier(pane, body_text);
pane._explorerFileContent = body_text;

const results = {
    tier: pane._explorerSourceTier,
    predicate: {
        source: sandbox.explorerPaneAllowsFind(pane, 'source'),
        preview: sandbox.explorerPaneAllowsFind(pane, 'preview'),
        diff: sandbox.explorerPaneAllowsFind(pane, 'diff'),
        // The bare call is what every pre-existing caller makes.
        defaulted: sandbox.explorerPaneAllowsFind(pane)
    },
    offersShell: {
        withDiff: sandbox.explorerFileOffersFind(pane, { hasGitDiff: true }),
        withoutDiff: sandbox.explorerFileOffersFind(pane, { hasGitDiff: false })
    },
    steps: []
};

// A query typed while the reader was on Diff. It has to survive being hidden.
const state = sandbox.ensureExplorerSearchState(pane, 'file');
state.query = 'CHANGED';

const step = async view => {
    controlUpdates = [];
    state.matchCount = 0;
    state.activeIndex = 3;
    sandbox.setExplorerFileView(0, view);
    // applyExplorerSearch() is async past its guard.
    for (let turn = 0; turn < 20; turn += 1) {
        await Promise.resolve();
    }
    results.steps.push({
        view,
        activeView: sandbox.activeExplorerFileView(0),
        shellHidden: searchShell.hidden === true,
        focusAccepted: sandbox.focusExplorerSearch(0),
        // Reset only if applyExplorerSearch() got past the availability guard.
        matchCount: state.matchCount,
        query: state.query,
        controlUpdates: controlUpdates.length
    });
};

(async () => {
    await step('source');
    await step('diff');
    await step('preview');
    results.focusCalls = focused;

    /* The same pane below the tier: nothing is hidden and nothing refuses,
       so the switch is not paying a cost on ordinary files. */
    sandbox.applyExplorerSourceTier(pane, 'one\ntwo\n');
    pane._explorerFileContent = 'one\ntwo\n';
    sandbox.setExplorerFileView(0, 'source');
    results.smallFile = {
        tier: pane._explorerSourceTier,
        shellHidden: searchShell.hidden === true,
        focusAccepted: sandbox.focusExplorerSearch(0)
    };

    /* A browsed listing has its own find control in the toolbar and no file
       header at all, so "there is no shell here" must not read as "Find is
       unavailable". */
    pane._explorerMode = 'directory';
    results.directoryAllows = sandbox.syncExplorerFindAvailability(0, null);

    process.stdout.write(JSON.stringify(results));
})();
"""


def _run_node(source: str, *paths: Path) -> dict:
    with TemporaryDirectory() as script_dir:
        script_path = Path(script_dir) / "harness.js"
        script_path.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            [NODE, str(script_path), *(str(path) for path in paths)],
            capture_output=True,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


@unittest.skipIf(NODE is None, "node is required to execute the viewer adapter")
class ExplorerFindAvailabilityTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_node(HARNESS, VIEWER_JS, TIERS_JS, TABS_JS)

    def test_the_fixture_is_actually_in_the_large_tier(self):
        self.assertEqual(self.results["tier"], "large")

    def test_find_is_refused_for_source_and_preview_and_allowed_for_diff(self):
        """Diff is not the file, and is bounded whatever the file's size is.

        Source is what the tier is about: above the ceiling there are no
        per-line rows for a find to address. Preview shares the verdict for a
        different reason — the render of a very large Markdown file is itself
        enormous and its find walks the whole subtree unbounded. Diff shares
        neither: its patch is capped by ``EXPLORER_GIT_DIFF_MAX_BYTES``.
        """
        predicate = self.results["predicate"]
        self.assertFalse(predicate["source"])
        self.assertFalse(predicate["preview"])
        self.assertTrue(predicate["diff"])
        # The bare call still answers for Source, so no existing caller moved.
        self.assertFalse(predicate["defaulted"])

    def test_the_shell_is_rendered_when_any_panel_could_answer(self):
        """The header is not rebuilt on a panel switch, so it cannot be per-view.

        One stable shell, rendered whenever some panel on this file could take
        a query, then hidden and shown from the active-view verdict.
        """
        offers = self.results["offersShell"]
        self.assertTrue(offers["withDiff"])
        self.assertFalse(offers["withoutDiff"])

    def test_the_shell_hides_and_returns_across_a_real_panel_switch(self):
        steps = {entry["view"]: entry for entry in self.results["steps"]}

        self.assertTrue(steps["source"]["shellHidden"])
        self.assertFalse(steps["diff"]["shellHidden"])
        self.assertTrue(steps["preview"]["shellHidden"])

    def test_ctrl_f_is_never_claimed_by_a_control_that_cannot_answer(self):
        """A hidden input that still takes the shortcut is worse than none.

        It suppresses the browser's own find as well, so the reader is left
        with no way to search at all.
        """
        steps = {entry["view"]: entry for entry in self.results["steps"]}

        self.assertFalse(steps["source"]["focusAccepted"])
        self.assertTrue(steps["diff"]["focusAccepted"])
        self.assertFalse(steps["preview"]["focusAccepted"])
        # Refused means not focused: exactly the one acceptance moved focus.
        self.assertEqual(self.results["focusCalls"], 1)

    def test_the_query_survives_being_hidden_and_diff_applies_it(self):
        steps = {entry["view"]: entry for entry in self.results["steps"]}

        # Kept while hidden, so switching to Diff resumes it rather than
        # asking the reader to retype it.
        for view in ("source", "diff", "preview"):
            self.assertEqual(steps[view]["query"], "CHANGED")

        # Only Diff got past the availability guard, so only Diff counted
        # matches and only Diff wrote the counter.
        self.assertEqual(steps["diff"]["matchCount"], 2)
        self.assertEqual(steps["source"]["matchCount"], 0)
        self.assertEqual(steps["preview"]["matchCount"], 0)
        self.assertEqual(steps["source"]["controlUpdates"], 0)
        self.assertEqual(steps["preview"]["controlUpdates"], 0)
        self.assertGreaterEqual(steps["diff"]["controlUpdates"], 1)

    def test_an_ordinary_file_pays_nothing_for_any_of_this(self):
        small = self.results["smallFile"]

        self.assertNotEqual(small["tier"], "large")
        self.assertFalse(small["shellHidden"])
        self.assertTrue(small["focusAccepted"])

    def test_a_browsed_listing_is_not_answered_from_the_missing_shell(self):
        """The listing's find lives in the toolbar, not in a file header."""
        self.assertTrue(self.results["directoryAllows"])


if __name__ == "__main__":
    unittest.main()
