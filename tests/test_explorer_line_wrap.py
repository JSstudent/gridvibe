"""The line-wrap button, driven through its own click handler.

The control is rendered, labelled, press-stated and hidden from one registered
mode list (EXPLORER_LINE_WRAP_MODES); its click handler carried a second,
older list of its own that spelled out Preview and Diff. Source was added to
every other half of the feature -- the mode list, the render, the CSS, the
editor textarea, the per-tab record and its persistence -- so the button drew
itself and reported the right state on a Source file, and clicking it did
nothing at all.

Nothing that read the source text caught that, because the button was never
pressed: the assertions checked that the functions and the CSS selectors
existed, and every one of them did. These tests run the real viewer, tab-strip
and persistence scripts in Node and press the real button once per registered
mode, checking the four things one press is supposed to move -- the per-tab
record, the panel class, the button's own pressed state, and the wrap map the
record serializes to -- so a mode the handler forgets fails here rather than
in the pane.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
# The DOM-free persistence module is what turns a tab record into the saved
# wrap map, so the serializing end of this runs against the real one.
PERSISTENCE_JS = STATIC_JS / "explorer-persistence.js"
ICONS_JS = STATIC_JS / "terminal-icons.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
TABS_JS = STATIC_JS / "explorer-tabs.js"
NODE = shutil.which("node")

# Wrapping defaults on in all three modes, so one press is always an opt-out.
ALL_WRAPPED = {"source": True, "preview": True, "diff": True}
NONE_WRAPPED = {"source": False, "preview": False, "diff": False}

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

/* One stub per surface the wrap control touches: the three view panels it
   toggles `wrap-lines` on, the editor textarea whose own `wrap` attribute has
   to stay in step, and the button itself. */
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

const textarea = { wrap: 'off' };

function makePanel(name) {
    return {
        name,
        classList: makeClassList(),
        querySelector: sel => (sel === '.explorer-source-editor' ? textarea : null)
    };
}

const panels = {
    source: makePanel('source'),
    preview: makePanel('preview'),
    diff: makePanel('diff')
};

let clickHandler = null;
const button = {
    dataset: {},
    hidden: false,
    attrs: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name]; },
    addEventListener(type, fn) { if (type === 'click') clickHandler = fn; }
};

const searchApplied = [];

const sandbox = {
    console,
    document: {
        getElementById: id => {
            if (id === 'explorer-code-0') return panels.source;
            if (id === 'explorer-preview-0') return panels.preview;
            if (id === 'explorer-diff-code-0') return panels.diff;
            return null;
        },
        querySelector: sel => (sel === '[data-explorer-line-wrap="0"]' ? button : null),
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
    applyExplorerSearch: index => { searchApplied.push(index); },
    applyExplorerChangeMarks: () => {},
    updateExplorerFilesystemRootRevision: () => {}
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
vm.createContext(sandbox);
process.argv.slice(2).forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});

/* Top-level `const` in a vm script lands in the script's global lexical scope,
   not on the sandbox object, so the registered mode list is read back through
   the context. Function declarations do become sandbox properties, which is
   what lets the two seams below be re-pointed after the real files load. */
const WRAP_MODES = vm.runInContext('EXPLORER_LINE_WRAP_MODES', sandbox);

/* In a browser `window` *is* the global object, so the persistence module's
   globalThis attachment and the tab strip's `window.` read are the same slot;
   in a vm sandbox they are two, so they are re-joined here. */
sandbox.window.GridVibeExplorerPersistence = sandbox.GridVibeExplorerPersistence;

const snapshot = value => JSON.parse(JSON.stringify(value === undefined ? null : value));

/* The active view mode is the viewer's own; this harness drives it directly so
   one click can be aimed at each of the three panels in turn. */
let activeView = 'source';
sandbox.activeExplorerFileView = () => activeView;
sandbox.applyExplorerSearch = index => { searchApplied.push(index); };

const pane = {
    _session: {},
    _explorerDiffLoaded: true
};
sandbox.terminals[0] = pane;

/* A tab with a path and a rendered view is what serializes to a record at all;
   without one the persistable view is null and the wrap map has nothing to
   ride in. This is the shape a reader who has opened a file actually has. */
const previewTab = sandbox.explorerPreviewTab(pane);
previewTab.path = 'src/a.py';
previewTab.dirPath = 'src';
previewTab.view = { mode: 'source', revisions: { source: 'rev-1' }, scroll: {} };

function clickWrapButton(mode) {
    activeView = mode;
    sandbox.applyExplorerLineWrapState(0);
    const before = {
        mode: button.dataset.explorerWrapMode,
        hidden: button.hidden,
        pressed: button.getAttribute('aria-pressed'),
        wrapped: panels[mode].classList.contains('wrap-lines')
    };
    const searchesBefore = searchApplied.length;
    clickHandler();
    return {
        before,
        after: {
            pressed: button.getAttribute('aria-pressed'),
            title: button.getAttribute('title'),
            wrapped: panels[mode].classList.contains('wrap-lines'),
            textareaWrap: textarea.wrap,
            searches: searchApplied.length - searchesBefore
        }
    };
}

const results = {};

sandbox.wireExplorerLineWrapControl(0);
results.wired = clickHandler !== null;

/* Every registered mode goes through the one real click handler. */
results.modes = WRAP_MODES.slice();
results.clicks = {};
results.modes.forEach(mode => {
    results.clicks[mode] = clickWrapButton(mode);
});

/* The per-tab record the click wrote, and the map that record serializes to. */
const tab = sandbox.explorerActiveTab(pane);
results.tabLineWrap = { ...tab.lineWrap };
results.serialized = snapshot(pane._session.explorer_tab_views);
/* Toggling back restores the default, and the serialized map says so. */
results.clicksBack = {};
results.modes.forEach(mode => {
    results.clicksBack[mode] = clickWrapButton(mode);
});
results.tabLineWrapBack = { ...sandbox.explorerActiveTab(pane).lineWrap };
results.serializedBack = snapshot(pane._session.explorer_tab_views);

/* The record a save wrote, read back the way a restore reads it. */
results.restoredFromOff = sandbox.explorerPersistedTabLineWrap(results.serialized.__preview__);
results.restoredFromOn = sandbox.explorerPersistedTabLineWrap(results.serializedBack.__preview__);

/* A mode the control was never registered for cannot be toggled into the tab
   record by forging the button's dataset. */
activeView = 'source';
sandbox.applyExplorerLineWrapState(0);
button.dataset.explorerWrapMode = 'listing';
clickHandler();
results.forgedMode = { ...sandbox.explorerActiveTab(pane).lineWrap };

process.stdout.write(JSON.stringify(results, null, 2));

"""


@unittest.skipUnless(NODE, "Node.js is required for explorer line-wrap tests")
class ExplorerLineWrapControlTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(PERSISTENCE_JS),
                    str(ICONS_JS),
                    str(VIEWER_JS),
                    str(TABS_JS),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(f"node harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_the_button_is_wired_for_every_registered_mode(self):
        # The mode list is the control's one source of truth: it decides what
        # is rendered, what is labelled and what is hidden. A handler serving
        # fewer modes than that list is the defect itself.
        self.assertTrue(self.results["wired"])
        self.assertEqual(self.results["modes"], ["source", "preview", "diff"])

    def test_one_press_turns_wrapping_off_in_each_mode(self):
        for mode in self.results["modes"]:
            with self.subTest(mode=mode):
                click = self.results["clicks"][mode]
                # The control really was showing this mode, pressed, over a
                # wrapped panel -- otherwise the press proves nothing about
                # the mode it was aimed at.
                self.assertEqual(click["before"]["mode"], mode)
                self.assertFalse(click["before"]["hidden"])
                self.assertEqual(click["before"]["pressed"], "true")
                self.assertTrue(click["before"]["wrapped"])
                # Panel class, pressed state and label all move together.
                self.assertFalse(click["after"]["wrapped"])
                self.assertEqual(click["after"]["pressed"], "false")
                self.assertIn("Enable line wrapping", click["after"]["title"])

    def test_a_press_writes_the_per_tab_record(self):
        # Wrapping belongs to the tab, not the pane, so the press has to land
        # in the active tab's own record.
        self.assertEqual(self.results["tabLineWrap"], NONE_WRAPPED)

    def test_a_second_press_turns_wrapping_back_on(self):
        for mode in self.results["modes"]:
            with self.subTest(mode=mode):
                click = self.results["clicksBack"][mode]
                self.assertEqual(click["before"]["pressed"], "false")
                self.assertTrue(click["after"]["wrapped"])
                self.assertEqual(click["after"]["pressed"], "true")
                self.assertIn("Disable line wrapping", click["after"]["title"])
        self.assertEqual(self.results["tabLineWrapBack"], ALL_WRAPPED)

    def test_the_source_press_carries_the_editor_textarea_with_it(self):
        # The in-place editor replaces the Source panel's contents, so the same
        # flag drives the textarea's own soft wrapping -- never `hard`, which
        # would inject newlines into the saved value.
        self.assertEqual(
            self.results["clicks"]["source"]["after"]["textareaWrap"], "off"
        )
        self.assertEqual(
            self.results["clicksBack"]["source"]["after"]["textareaWrap"], "soft"
        )

    def test_only_the_diff_press_re_runs_the_search(self):
        # A wrap change rebuilds the Diff rows, so its marks are repainted;
        # the other two panels keep theirs and must not pay for it.
        self.assertEqual(self.results["clicks"]["diff"]["after"]["searches"], 1)
        self.assertEqual(self.results["clicks"]["source"]["after"]["searches"], 0)
        self.assertEqual(self.results["clicks"]["preview"]["after"]["searches"], 0)

    def test_the_press_reaches_the_serialized_wrap_map(self):
        # What a save writes, for all three modes, from the presses above.
        self.assertEqual(
            self.results["serialized"]["__preview__"]["wrap"], NONE_WRAPPED
        )
        self.assertEqual(
            self.results["serializedBack"]["__preview__"]["wrap"], ALL_WRAPPED
        )

    def test_the_saved_map_restores_to_what_was_pressed(self):
        # Read back the way a restore reads it: an explicit false survives, and
        # wrapping is on wherever nothing said otherwise.
        self.assertEqual(self.results["restoredFromOff"], NONE_WRAPPED)
        self.assertEqual(self.results["restoredFromOn"], ALL_WRAPPED)

    def test_an_unregistered_mode_cannot_be_pressed_into_the_record(self):
        # The gate is the mode list, so widening that list is the only way to
        # add a mode -- a forged data-explorer-wrap-mode writes nothing.
        self.assertEqual(self.results["forgedMode"], ALL_WRAPPED)


if __name__ == "__main__":
    unittest.main()
