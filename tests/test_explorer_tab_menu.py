"""What a tab's right-click menu offers, and which tabs offer it.

A tab in the strip names a path the same way a Files-tree row does, so it
carries the shared ``data-explorer-copy-path`` hook and its menu offers Copy
path / Copy relative path / Download file. The permanent Preview tab was the
one exception, and only in that half: its Git-scope hook already asked
``tab.path`` alone, so right-clicking the file it was showing offered to pin
Git to a path the same menu would not spell or copy.

Neither ``renderExplorerTabStrip()`` nor ``handleExplorerContextMenu()`` is
DOM-free, so both run in a Node ``vm`` against a stubbed page the way
``test_explorer_git_scope_surfaces.py`` runs the browsing surfaces. The menu is
built from the attributes the strip actually rendered rather than from a
hand-written row, so the two halves cannot drift apart in the test either.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"
NODE = shutil.which("node")

# The page's own load order, so the classic scripts see each other exactly as
# they do in the browser. The strip renderer reaches the viewer (file-type
# icons, the context menu) and the sidebar (the Git badge), so both are loaded
# rather than stubbed.
MODULES = [
    STATIC_JS / name
    for name in (
        "explorer-persistence.js",
        "explorer-scroll.js",
        "explorer-theme-store.js",
        "explorer-selection.js",
        "explorer-tiers.js",
        "explorer-repaint.js",
        "explorer-git-menu.js",
        "explorer-git-active.js",
        "explorer-git-search.js",
        "explorer-git-pin.js",
        "terminal-icons.js",
        "explorer-viewer.js",
        "explorer-tree.js",
        "explorer-git-sidebar.js",
        "explorer-scroll-adapter.js",
        "explorer-diff.js",
        "explorer-tabs.js",
    )
]

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const modules = JSON.parse(process.argv[2]);
const spec = JSON.parse(process.argv[3]);

function fakeElement(id) {
    const node = {
        id,
        innerHTML: '',
        textContent: '',
        value: '',
        hidden: false,
        scrollTop: 0,
        scrollLeft: 0,
        dataset: {},
        style: { setProperty() {}, removeProperty() {} },
        classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
        appendChild() {},
        removeChild() {},
        remove() {},
        replaceChildren() {},
        insertAdjacentHTML() {},
        setAttribute() {},
        removeAttribute() {},
        getAttribute: () => null,
        hasAttribute: () => false,
        addEventListener() {},
        removeEventListener() {},
        focus() {},
        getBoundingClientRect: () => ({
            top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100
        }),
        querySelector: () => null,
        querySelectorAll: () => [],
        closest: () => null,
        matches: () => false
    };
    node.cloneNode = () => fakeElement(id);
    return node;
}

const elements = new Map();
const sandbox = {
    console,
    URL,
    URLSearchParams,
    encodeURIComponent,
    AbortController,
    document: {
        activeElement: null,
        getElementById: id => {
            if (!elements.has(id)) elements.set(id, fakeElement(id));
            return elements.get(id);
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        createElement: () => fakeElement('created'),
        createDocumentFragment: () => fakeElement('fragment'),
        addEventListener() {},
        body: { dataset: {}, addEventListener() {}, classList: { add() {}, remove() {} } }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame() {},
    performance: { now: () => 0 },
    CSS: { highlights: { set() {}, delete() {} } },
    terminals: [],
    sessionIds: [],
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    fetch: async () => ({ ok: true, json: async () => ({}) })
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
modules.forEach(path => vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox));

/* Owned by terminals.js, which this harness cannot load. Recorded rather than
   simulated, so a chosen entry is observed by what it asked the page to do. */
const copied = [];
const downloads = [];
sandbox._copyText = text => { copied.push(String(text)); };
sandbox.downloadExplorerFile = (index, target) => { downloads.push(target.path); };
sandbox.downloadExplorerFiles = (index, targets) => {
    downloads.push(targets.map(entry => entry.path).join('\n'));
};
['notePanePresentationChanged', 'applyExplorerTheme', 'explorerPaneLiveTheme',
 'isExplorerSession', 'showGridVibeNotice', 'openGenericConfirmModal'
].forEach(name => {
    if (typeof sandbox[name] === 'undefined') sandbox[name] = () => undefined;
});

let offered = [];
sandbox.showExplorerContextMenu = (x, y, items) => { offered = items; };

const pane = {
    _session: { startup_mode: 'explorer', explorer_root_directory: '/repo' },
    _attached: true,
    _explorerMode: spec.paneMode || 'file',
    _explorerPath: spec.panePath || '',
    _explorerFilePath: spec.paneFilePath || '',
    _explorerRootRevision: 'rev-1',
    _explorerTabs: spec.tabs,
    _explorerActiveTabId: spec.activeTabId,
    _explorerGitSidebarOpen: true,
    _explorerGitRepo: { git: { available: true, repo_path: '' } }
};
sandbox.terminals[0] = pane;
sandbox.sessionIds[0] = 'sess-0';

sandbox.renderExplorerTabStrip(0);
const markup = elements.get('explorer-tabs-0').innerHTML;

/* Each rendered tab as the browser would expose it: the `data-*` attributes
   the strip wrote, read back as the dataset the context menu consults. */
const tabs = markup.split('<div class="explorer-tab').slice(1).map(chunk => {
    const open = chunk.slice(0, chunk.indexOf('>'));
    const dataset = {};
    for (const match of open.matchAll(/data-([a-z0-9-]+)="([^"]*)"/g)) {
        const key = match[1].replace(/-([a-z0-9])/g, (_, ch) => ch.toUpperCase());
        dataset[key] = match[2];
    }
    return dataset;
});

const target = tabs.find(dataset => dataset.explorerTab === spec.menuTab) || null;
const promise = target
    ? (() => {
        const row = {
            dataset: target,
            classList: { add() {}, remove() {}, contains: () => false },
            matches: () => false,
            querySelector: () => null
        };
        return Promise.resolve(sandbox.handleExplorerContextMenu({
            clientX: 10,
            clientY: 10,
            preventDefault() {},
            target: {
                closest: selector => {
                    if (selector.includes('data-explorer-git-commit-toggle')) return null;
                    if (selector.includes('data-explorer-copy-path')) return row;
                    return null;
                }
            }
        }, 0));
    })()
    : Promise.resolve();

promise.then(async () => {
    for (const label of (spec.click || [])) {
        const item = offered.find(entry => entry.label === label);
        if (item && !item.disabled) {
            await item.action();
        }
    }
    process.stdout.write(JSON.stringify({
        tabs,
        labels: offered.map(item => item.label),
        copied,
        downloads
    }));
}).catch(error => {
    console.error(error);
    process.exit(1);
});
"""

PREVIEW_ID = "__preview__"


def _tab(tab_id, path, name=""):
    return {"id": tab_id, "pinned": tab_id != PREVIEW_ID, "path": path, "name": name}


@unittest.skipUnless(NODE, "Node.js is required for explorer tab menu tests")
class ExplorerTabMenuTestCase(unittest.TestCase):
    def _strip(self, tabs, menu_tab=None, click=(), **spec):
        spec.update({
            "tabs": tabs,
            "activeTabId": spec.get("activeTabId", tabs[0]["id"]),
            "menuTab": menu_tab,
            "click": list(click),
        })
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "tabmenu.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    json.dumps([str(path) for path in MODULES]),
                    json.dumps(spec),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_preview_tab_showing_a_file_carries_the_copy_path_hooks(self):
        """The tab the reader is looking at names its file like any other tab."""
        result = self._strip(
            [_tab(PREVIEW_ID, "web/api.py")],
            paneFilePath="web/api.py",
        )
        preview = result["tabs"][0]
        self.assertEqual(preview["explorerCopyPath"], "web/api.py")
        self.assertEqual(preview["explorerDownloadPath"], "web/api.py")
        # The hook it always had, now on the same condition as the other two.
        self.assertEqual(preview["explorerGitScopePath"], "web/api.py")

    def test_the_preview_tab_showing_a_directory_names_no_file(self):
        """A listing is not a path the menu could copy, download or pin."""
        result = self._strip([_tab(PREVIEW_ID, "")], paneMode="directory", panePath="web")
        preview = result["tabs"][0]
        self.assertNotIn("explorerCopyPath", preview)
        self.assertNotIn("explorerDownloadPath", preview)
        self.assertNotIn("explorerGitScopePath", preview)

    def test_a_pinned_tab_is_unchanged(self):
        result = self._strip(
            [_tab(PREVIEW_ID, ""), _tab("tab-1", "web/explorer.py", "explorer.py")],
            paneMode="directory",
        )
        pinned = result["tabs"][1]
        self.assertEqual(pinned["explorerCopyPath"], "web/explorer.py")
        self.assertEqual(pinned["explorerDownloadPath"], "web/explorer.py")

    def test_no_tab_exposes_a_filesystem_context_kind(self):
        """Copy-only: create/move/delete never reach a tab row."""
        result = self._strip(
            [_tab(PREVIEW_ID, "web/api.py"), _tab("tab-1", "web/explorer.py", "explorer.py")],
            paneFilePath="web/api.py",
            menu_tab=PREVIEW_ID,
        )
        for dataset in result["tabs"]:
            self.assertNotIn("explorerContextKind", dataset)
        self.assertNotIn("Delete", " ".join(result["labels"]))
        self.assertNotIn("Rename", " ".join(result["labels"]))

    def test_right_clicking_the_preview_tab_offers_the_path_entries(self):
        """The menu built from the strip's own attributes, entries run."""
        result = self._strip(
            [_tab(PREVIEW_ID, "web/api.py")],
            paneFilePath="web/api.py",
            menu_tab=PREVIEW_ID,
            click=("Copy path", "Copy relative path", "Download file"),
        )
        self.assertIn("Copy path", result["labels"])
        self.assertIn("Copy relative path", result["labels"])
        self.assertIn("Download file", result["labels"])
        # The absolute path is the explorer root joined to the tab's own
        # relative path; the relative entry copies exactly what the tab names.
        self.assertEqual(result["copied"], ["/repo/web/api.py", "web/api.py"])
        self.assertEqual(result["downloads"], ["web/api.py"])

    def test_the_preview_tab_offers_the_same_entries_a_pinned_tab_does(self):
        preview = self._strip(
            [_tab(PREVIEW_ID, "web/api.py")],
            paneFilePath="web/api.py",
            menu_tab=PREVIEW_ID,
        )
        pinned = self._strip(
            [_tab(PREVIEW_ID, ""), _tab("tab-1", "web/api.py", "api.py")],
            paneMode="directory",
            menu_tab="tab-1",
        )
        self.assertEqual(preview["labels"], pinned["labels"])
        self.assertTrue(preview["labels"])
