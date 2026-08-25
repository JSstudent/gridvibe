"""An explorer pane opens on a directory, never on an empty viewer.

A freshly launched or imported explorer session persists no tab state, and the
restore used to answer that by leaving the Preview tab empty: the viewer said
"Select a file to view" and the path bar still held the raw absolute directory
the pane was launched with — inert text, because the clickable breadcrumb is
only rendered by a directory load. The way out was to click a folder in the
tree and walk back up to the top, which is where the pane should have started.

These run ``restoreExplorerPersistedTabs`` in Node against the real module, so
what is asserted is which directory the restore actually browses.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

TABS_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "explorer-tabs.js"
NODE = shutil.which("node")

# The restore only reaches DOM code through collaborators owned by other
# modules (the strip renderer, the file opener, the directory loader), so all
# this harness needs is somewhere for those to be recorded.
HARNESS = """
const fs = require('fs');
const vm = require('vm');
const scenario = JSON.parse(process.argv[3]);

const calls = { loaded: [], opened: [], persisted: [] };
const sandbox = {
    console,
    document: { getElementById: () => null, querySelector: () => null },
    window: { setTimeout, clearTimeout },
    setTimeout,
    clearTimeout,
    terminals: [],
    sessionIds: ['sess-0'],
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

// Collaborators from explorer-viewer.js, recorded rather than run.
sandbox.loadExplorerPane = (index, path) => {
    calls.loaded.push(path);
    const pane = sandbox.terminals[index];
    if (scenario.missingDirs && scenario.missingDirs.includes(path)) {
        return Promise.resolve(false);
    }
    pane._explorerPath = path;
    pane._explorerMode = 'directory';
    return Promise.resolve(true);
};
sandbox.openExplorerFile = (index, path) => {
    calls.opened.push(path);
    const ok = !(scenario.missingFiles || []).includes(path);
    if (!ok) {
        sandbox.terminals[index]._explorerOpenErrorCode = 'not_found';
    }
    return Promise.resolve(ok);
};
sandbox.renderExplorerTabStrip = () => {};
sandbox.persistExplorerTabsToSession = index => calls.persisted.push(index);

sandbox.terminals[0] = { _session: scenario.session };
sandbox.terminals[0]._explorerPath = sandbox.explorerInitialPreviewDirectory(scenario.session);
sandbox.restoreExplorerPersistedTabs(0).then(() => {
    const pane = sandbox.terminals[0];
    const preview = pane._explorerTabs.find(tab => tab.id === '__preview__');
    process.stdout.write(JSON.stringify({
        calls,
        initialPath: sandbox.explorerInitialPreviewDirectory(scenario.session),
        activeTabId: pane._explorerActiveTabId,
        previewHasDirPath: Object.prototype.hasOwnProperty.call(preview, 'dirPath'),
        previewDirPath: preview.dirPath,
        previewPath: preview.path,
        pinned: pane._explorerTabs.filter(tab => tab.pinned).map(tab => tab.path)
    }));
});
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer startup tests")
class ExplorerStartupPreviewTestCase(unittest.TestCase):
    def _restore(self, session, *, missing_files=(), missing_dirs=()):
        scenario = {
            "session": session,
            "missingFiles": list(missing_files),
            "missingDirs": list(missing_dirs),
        }
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TABS_JS), json.dumps(scenario)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_fresh_pane_opens_on_the_root_listing(self):
        """Nothing persisted — a launch or an import — browses the root."""
        result = self._restore({})

        self.assertEqual(result["calls"]["loaded"], [""])
        self.assertEqual(result["calls"]["opened"], [])
        self.assertEqual(result["activeTabId"], "__preview__")
        # '' is the explorer root, and its presence is what tells a later tab
        # switch that the Preview tab has a directory of its own.
        self.assertTrue(result["previewHasDirPath"])
        self.assertEqual(result["previewDirPath"], "")

    def test_a_saved_preview_directory_still_wins_over_the_root(self):
        result = self._restore({"explorer_tab_views": {"__preview__": {"dir": "web/static"}}})

        self.assertEqual(result["calls"]["loaded"], ["web/static"])
        self.assertEqual(result["initialPath"], "web/static")
        self.assertEqual(result["previewDirPath"], "web/static")

    def test_a_live_mode_switch_uses_the_fresh_cwd_instead_of_the_saved_directory(self):
        result = self._restore({
            "explorer_open_path": "src/current",
            "explorer_tab_views": {"__preview__": {"dir": "res"}},
        })

        # The live pane seed uses the cwd returned by the mode-switch route. The
        # restore routine is intentionally still free to read durable tab state;
        # live replacement does not invoke that workspace-startup routine.
        self.assertEqual(result["initialPath"], "src/current")

    def test_a_saved_directory_that_is_gone_falls_back_to_the_root(self):
        result = self._restore(
            {"explorer_tab_views": {"__preview__": {"dir": "moved/away"}}},
            missing_dirs=["moved/away"],
        )

        self.assertEqual(result["calls"]["loaded"], ["moved/away", ""])

    def test_a_saved_preview_file_reopens_without_a_directory_load(self):
        result = self._restore({"explorer_tab_views": {"__preview__": {"path": "README.md"}}})

        self.assertEqual(result["calls"]["opened"], ["README.md"])
        self.assertEqual(result["calls"]["loaded"], [])
        self.assertEqual(result["previewPath"], "README.md")

    def test_a_restored_pinned_tab_keeps_the_viewer_but_preview_means_root(self):
        """The pinned tab wins the viewer, so nothing is browsed now — but the
        Preview tab still records the root, so switching to it later shows the
        listing instead of the empty viewer."""
        result = self._restore({
            "explorer_open_tabs": ["docs/notes.md"],
            "explorer_active_tab": "docs/notes.md",
        })

        self.assertEqual(result["calls"]["opened"], ["docs/notes.md"])
        self.assertEqual(result["calls"]["loaded"], [])
        self.assertEqual(result["activeTabId"], "docs/notes.md")
        self.assertTrue(result["previewHasDirPath"])
        self.assertEqual(result["previewDirPath"], "")

    def test_a_pinned_tab_whose_file_is_gone_lands_on_the_root_listing(self):
        result = self._restore(
            {"explorer_open_tabs": ["docs/gone.md"], "explorer_active_tab": "docs/gone.md"},
            missing_files=["docs/gone.md"],
        )

        self.assertEqual(result["calls"]["loaded"], [""])
        self.assertEqual(result["activeTabId"], "__preview__")
        self.assertEqual(result["pinned"], [])


if __name__ == "__main__":
    unittest.main()
