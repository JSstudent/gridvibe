"""Explorer panel-scroll policy and the viewer adapter that applies it.

The policy is DOM-free and executed directly in Node. The adapter is exercised
against the real ``explorer-viewer.js`` with a small DOM stub so these tests
observe panel positions across view switches, delayed Preview arrival and an
in-place Markdown refresh rather than pinning implementation text.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
SCROLL_JS = STATIC_JS / "explorer-scroll.js"
PERSISTENCE_JS = STATIC_JS / "explorer-persistence.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
SCROLL_ADAPTER_JS = STATIC_JS / "explorer-scroll-adapter.js"
TABS_JS = STATIC_JS / "explorer-tabs.js"
NODE = shutil.which("node")

POLICY_HARNESS = r"""
const scroll = require(process.argv[2]);

const metrics = {
    scrollLeft: 30,
    scrollLeftRatio: 0.1,
    scrollTop: 600,
    scrollTopRatio: 0.5,
    wasAtBottom: false
};
const tab = {
    collapsedLines: new Set([3, 8]),
    view: {
        mode: 'preview',
        revisions: { source: 'same', preview: 'same', diff: 'loaded-diff' },
        scroll: {
            activeView: 'preview',
            panels: { preview: metrics, diff: { ...metrics, scrollTop: 900 } },
            sidebar: {}
        }
    }
};
const persisted = {
    view: { persistedRecord: { version: 2, intent: { mode: 'preview' } } }
};
let persistedArgs = null;

const attempts = [];
for (let attempt = 0; attempt < scroll.MAX_PANEL_SCROLL_RESTORE_ATTEMPTS; attempt += 1) {
    attempts.push(scroll.restorePlan(metrics, {
        scrollHeight: 300,
        clientHeight: 300,
        scrollWidth: 100,
        clientWidth: 100
    }, attempt));
}

process.stdout.write(JSON.stringify({
    matching: scroll.resolveTabView(tab, { source: 'same', preview: 'same' }),
    partial: scroll.resolveTabView(tab, {
        source: 'same', preview: 'same', diff: 'new-diff'
    }),
    changed: scroll.resolveTabView(tab, { source: 'new', preview: 'new' }),
    persisted: scroll.resolveTabView(persisted, { preview: 'same' }, {
        resolveRecord: (record, current) => {
            persistedArgs = { record, current };
            return { mode: record.intent.mode, scroll: { panels: {} } };
        }
    }),
    persistedArgs,
    short: attempts[0],
    final: attempts[attempts.length - 1],
    ready: scroll.restorePlan(metrics, {
        scrollHeight: 1500,
        clientHeight: 300,
        scrollWidth: 500,
        clientWidth: 100
    }, 0),
    mermaidAbove: scroll.shouldReapplyAfterGrowth(metrics, 200, 600),
    mermaidBelow: scroll.shouldReapplyAfterGrowth(metrics, 900, 600)
}));
"""

ADAPTER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

function classList() {
    return { add() {}, remove() {}, contains: () => false, toggle() {} };
}

function scroller(id, height) {
    const listeners = {};
    return {
        id,
        hidden: false,
        dataset: {},
        scrollTop: 0,
        scrollLeft: 0,
        scrollHeight: height,
        clientHeight: 300,
        scrollWidth: 600,
        clientWidth: 300,
        textContent: '',
        innerHTML: '',
        style: { setProperty() {}, removeProperty() {} },
        classList: classList(),
        addEventListener(type, listener) { (listeners[type] ||= []).push(listener); },
        dispatch(type) { (listeners[type] || []).forEach(listener => listener({})); },
        querySelector: () => null,
        querySelectorAll: () => [],
        setAttribute() {}
    };
}

const sourceView = scroller('explorer-code-0', 1800);
const sourcePanel = {
    id: 'source-panel',
    hidden: false,
    dataset: { explorerFilePanel: 'source' },
    querySelector: selector => selector === '.explorer-source-view' ? sourceView : null
};
const preview = scroller('explorer-preview-0', 300);
preview.hidden = true;
preview.dataset.explorerFilePanel = 'preview';

function button(mode, selected) {
    return {
        dataset: { explorerFileView: mode },
        attrs: { 'aria-selected': selected ? 'true' : 'false' },
        setAttribute(name, value) { this.attrs[name] = String(value); },
        getAttribute(name) { return this.attrs[name]; }
    };
}
const sourceButton = button('source', true);
const previewButton = button('preview', false);
const body = { classList: classList() };
const nameLabel = { textContent: '', title: '' };
const metaLabel = { textContent: '' };

const list = {
    id: 'explorer-list-0',
    scrollTop: 0,
    scrollLeft: 0,
    scrollHeight: 300,
    clientHeight: 300,
    scrollWidth: 300,
    clientWidth: 300,
    querySelector(selector) {
        if (selector === '.explorer-editor-body') return body;
        if (selector === '.explorer-editor-name') return nameLabel;
        if (selector === '.explorer-editor-meta') return metaLabel;
        if (selector === '[data-explorer-file-view][aria-selected="true"]') {
            return [sourceButton, previewButton].find(
                entry => entry.attrs['aria-selected'] === 'true'
            ) || null;
        }
        if (selector === '[data-explorer-file-panel]') return sourcePanel;
        const panel = selector.match(/^\[data-explorer-file-panel="([^"]+)"\]$/);
        if (panel) return panel[1] === 'source' ? sourcePanel : panel[1] === 'preview' ? preview : null;
        return null;
    },
    querySelectorAll(selector) {
        if (selector === '[data-explorer-file-view]') return [sourceButton, previewButton];
        if (selector === '[data-explorer-file-panel]') return [sourcePanel, preview];
        return [];
    }
};

const elements = new Map([
    ['explorer-list-0', list],
    ['explorer-code-0', sourceView],
    ['explorer-preview-0', preview]
]);
const raf = [];
const fetches = [];
const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'README.md',
    _explorerFileContent: '# old',
    _explorerFileLanguage: 'markdown',
    _explorerSourceTier: 'full',
    _explorerLastFileView: 'source',
    _explorerPreviewLoaded: false,
    _explorerPreviewHtml: '',
    _explorerDiffLoaded: false,
    _explorerDiffSplit: false,
    _explorerActiveTabId: 'preview',
    _explorerRenderedTabId: 'preview'
};

const sandbox = {
    console,
    document: {
        getElementById: id => elements.get(id) || null,
        querySelector(selector) {
            const found = selector.match(/data-explorer-file-panel="([^"]+)"/);
            if (!found) return null;
            return found[1] === 'source' ? sourcePanel : found[1] === 'preview' ? preview : null;
        },
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {},
        setTimeout: () => 0,
        clearTimeout() {},
        requestAnimationFrame: callback => { raf.push(callback); return raf.length; },
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} }
    },
    navigator: {},
    setTimeout: () => 0,
    clearTimeout() {},
    requestAnimationFrame: callback => { raf.push(callback); return raf.length; },
    terminals: [pane],
    sessionIds: ['s0'],
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value),
    fetch: () => new Promise(resolve => fetches.push(resolve))
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
sandbox.window.GridVibeExplorerScroll = sandbox.GridVibeExplorerScroll;
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[5], 'utf8'), sandbox);
sandbox.window.GridVibeExplorerPersistence = sandbox.GridVibeExplorerPersistence;
vm.runInContext(fs.readFileSync(process.argv[6], 'utf8'), sandbox);

const realTabRuntime = {
    active: sandbox.explorerActiveTab,
    activate: sandbox.activateExplorerTab,
    find: sandbox.explorerFindTab,
    matching: sandbox.explorerMatchingTabView
};

const tab = { preferredMode: 'source' };
sandbox.explorerActiveTab = () => tab;
sandbox.applyExplorerLineWrapState = () => {};
sandbox.applyExplorerSearch = () => {};
sandbox.loadExplorerDiff = () => {};
sandbox.renderExplorerSource = () => {};
sandbox.loadExplorerChangeMarks = () => {};
sandbox.applyExplorerEditorFontSize = () => {};
sandbox.updateExplorerGitSummary = () => {};
sandbox.renderExplorerPathBreadcrumb = () => {};
sandbox.setExplorerEditChromeDisabled = () => {};
sandbox.refreshExplorerEditControls = () => {};
sandbox.whenExplorerSourceRendered = () => {};
sandbox.renderExplorerTabStrip = () => {};
sandbox.explorerFindTab = () => tab;

let paints = 0;
sandbox.paintExplorerPreview = () => {
    paints += 1;
    preview.scrollHeight = 1500;
    return preview;
};
const realEnsurePreview = sandbox.ensureExplorerPreviewLoaded;

function select(mode) {
    sourceButton.setAttribute('aria-selected', mode === 'source' ? 'true' : 'false');
    previewButton.setAttribute('aria-selected', mode === 'preview' ? 'true' : 'false');
    sourcePanel.hidden = mode !== 'source';
    preview.hidden = mode !== 'preview';
}

function resolvePreview(html) {
    const resolve = fetches.shift();
    resolve({ ok: true, json: async () => ({ preview_html: html }) });
}

(async () => {
    const results = {};

    // A hidden panel keeps its own offset; showing it applies that offset and
    // captures the panel being hidden for the return trip.
    sandbox.ensureExplorerPreviewLoaded = () => Promise.resolve(preview);
    sandbox.setExplorerPanelScrollState(0, {
        activeView: 'source',
        panels: {
            source: { scrollTop: 240, scrollLeft: 12 },
            preview: { scrollTop: 520, scrollLeft: 0 }
        },
        sidebar: {}
    });
    sourceView.scrollTop = 240;
    sourceView.scrollLeft = 12;
    pane._explorerPreviewLoaded = true;
    preview.scrollHeight = 1500;
    sandbox.setExplorerFileView(0, 'preview');
    const previewOnShow = preview.scrollTop;
    sandbox.setExplorerFileView(0, 'source');
    results.viewSwitch = { previewOnShow, sourceOnReturn: sourceView.scrollTop };

    // The cached-group path uses the same capture/restore pair after the card
    // is detached and reattached; the active Preview panel keeps its offset.
    sandbox.setExplorerFileView(0, 'preview');
    preview.scrollTop = 830;
    const cachedGroupScroll = sandbox.captureExplorerFileScroll(0);
    preview.scrollTop = 0;
    sandbox.restoreExplorerFileScroll(0, cachedGroupScroll);
    results.groupSwitch = { preview: preview.scrollTop };

    // Preview arrives after the first show attempt found an empty panel. Its
    // own arrival, not Source readiness, is what reapplies the offset.
    sandbox.ensureExplorerPreviewLoaded = realEnsurePreview;
    select('preview');
    pane._explorerPreviewLoaded = false;
    pane._explorerPreviewHtml = '';
    // The production loader can have a small nonzero extent. An early apply
    // would clamp 600 to 60, then the delegated page listener would capture
    // that programmatic clamp over the real target before the fetch arrives.
    preview.scrollHeight = 360;
    preview.scrollTop = 0;
    sandbox.setExplorerPanelScrollState(0, {
        activeView: 'preview',
        panels: { preview: { scrollTop: 600, scrollLeft: 0 } },
        sidebar: {}
    });
    const prematureApplied = sandbox.requestExplorerPanelScrollRestore(0, 'preview');
    const capturedWhilePending = sandbox.captureExplorerFileScroll(0);
    const delayed = realEnsurePreview(0);
    const beforeArrival = preview.scrollTop;
    resolvePreview('<p>ready</p>');
    await delayed;
    results.delayedPreview = {
        prematureApplied,
        capturedWhilePending: capturedWhilePending.panels.preview.scrollTop,
        beforeArrival,
        afterArrival: preview.scrollTop
    };

    // One lazy diagram above the restored point may correct layout growth;
    // once the reader moves, the same callback must leave them alone.
    sandbox.setExplorerPanelScrollState(0, {
        activeView: 'preview',
        panels: { preview: { scrollTop: 600, scrollLeft: 0 } },
        sidebar: {}
    });
    sandbox.requestExplorerPanelScrollRestore(0, 'preview');
    preview.scrollTop = 540;
    sandbox.reapplyExplorerPreviewScrollAfterMermaid(preview, 200);
    const corrected = preview.scrollTop;
    sandbox.setExplorerPanelScrollState(0, {
        activeView: 'preview',
        panels: { preview: { scrollTop: 600, scrollLeft: 0 } },
        sidebar: {}
    });
    sandbox.requestExplorerPanelScrollRestore(0, 'preview');
    preview.scrollTop = 680;
    preview.dispatch('scroll');
    sandbox.reapplyExplorerPreviewScrollAfterMermaid(preview, 200);
    results.mermaid = { corrected, afterReaderMove: preview.scrollTop };

    // A response for a pane that moved to another file cannot paint or scroll
    // the replacement pane.
    pane._explorerPreviewLoaded = false;
    preview.scrollHeight = 300;
    preview.scrollTop = 0;
    const stalePaintCount = paints;
    const stale = realEnsurePreview(0);
    sandbox.terminals[0] = {
        _explorerMode: 'file',
        _explorerFilePath: 'other.md',
        _explorerFileContent: '# other'
    };
    resolvePreview('<p>stale</p>');
    await stale;
    results.lateArrival = {
        paints: paints - stalePaintCount,
        scrollTop: preview.scrollTop
    };

    // An in-place Markdown refresh installs the captured Preview offset before
    // it starts the replacement preview request.
    sandbox.terminals[0] = pane;
    select('preview');
    pane._explorerFileContent = '# old';
    pane._explorerPreviewLoaded = true;
    preview.scrollHeight = 1500;
    preview.scrollTop = 700;
    const refreshed = sandbox.updateExplorerFileInPlace(0, {
        path: 'README.md',
        name: 'README.md',
        content: '# changed',
        language: 'markdown',
        preview_type: 'markdown',
        editable: true,
        git: null,
        git_context: null
    }, {
        activeView: 'preview',
        panels: { preview: { scrollTop: 700, scrollLeft: 0 } },
        sidebar: {}
    });
    const beforeRefreshArrival = preview.scrollTop;
    resolvePreview('<p>changed</p>');
    await Promise.resolve();
    await Promise.resolve();
    results.saveRefresh = {
        refreshed,
        beforeArrival: beforeRefreshArrival,
        afterArrival: preview.scrollTop
    };

    // Exercise the actual explorer-tab activation path. A Diff revision may
    // have been loaded earlier in a tab even though Preview is the view being
    // captured. Diff is not available yet on the next file render; that must
    // not invalidate Preview's independently matching offset.
    sandbox.explorerActiveTab = realTabRuntime.active;
    sandbox.explorerFindTab = realTabRuntime.find;
    sandbox.explorerMatchingTabView = realTabRuntime.matching;
    sandbox.confirmDiscardExplorerEdit = async () => true;
    sandbox.renderExplorerTabStrip = () => {};
    sandbox.persistExplorerTabsToSession = () => {};
    const previewTab = { id: 'preview', pinned: false, path: '', name: 'Preview' };
    const tabA = { id: 'a.md', pinned: true, path: 'a.md', name: 'a.md' };
    const tabB = { id: 'b.md', pinned: true, path: 'b.md', name: 'b.md' };
    pane._explorerTabs = [previewTab, tabA, tabB];
    pane._explorerActiveTabId = tabA.id;
    pane._explorerRenderedTabId = tabA.id;
    pane._explorerMode = 'file';
    pane._explorerFilePath = tabA.path;
    pane._explorerFileContent = '# a';
    pane._explorerPreviewLoaded = true;
    pane._explorerDiffLoaded = true;
    pane._explorerDiffContent = 'diff a';
    select('preview');
    preview.scrollHeight = 1500;
    preview.scrollTop = 640;
    sandbox.renderExplorerActiveTab = index => {
        const active = realTabRuntime.active(pane);
        pane._explorerMode = 'file';
        pane._explorerFilePath = active.path;
        pane._explorerFileContent = active.id === tabA.id ? '# a' : '# b';
        pane._explorerRenderedTabId = active.id;
        pane._explorerPreviewLoaded = true;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffContent = '';
        select('preview');
        preview.scrollHeight = 1500;
        preview.scrollTop = 0;
        const restored = realTabRuntime.matching(
            active,
            sandbox.explorerCurrentContentRevisions(pane)
        );
        sandbox.setExplorerPanelScrollState(0, restored?.scroll || null);
        sandbox.restoreExplorerFileScroll(0, restored?.scroll || null);
    };
    await realTabRuntime.activate(0, tabB.id);
    await realTabRuntime.activate(0, tabA.id);
    results.tabRoundTrip = { preview: preview.scrollTop };

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
        raise AssertionError(f"node harness failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "Node.js is required for explorer scroll tests")
class ExplorerScrollPolicyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_node(POLICY_HARNESS, SCROLL_JS)

    def test_live_tab_scroll_is_filtered_by_each_panels_content_revision(self):
        self.assertEqual(
            self.results["matching"]["scroll"]["panels"]["preview"]["scrollTop"],
            600,
        )
        self.assertEqual(
            self.results["partial"]["scroll"]["panels"]["preview"]["scrollTop"],
            600,
        )
        self.assertNotIn("diff", self.results["partial"]["scroll"]["panels"])
        self.assertEqual(self.results["matching"]["folds"], [3, 8])
        self.assertEqual(self.results["changed"]["mode"], "preview")
        self.assertEqual(self.results["changed"]["scroll"]["panels"], {})
        self.assertEqual(self.results["changed"]["folds"], [])

    def test_persisted_records_stay_owned_by_the_persistence_resolver(self):
        self.assertEqual(self.results["persisted"]["mode"], "preview")
        self.assertEqual(self.results["persistedArgs"]["current"], {"preview": "same"})

    def test_restore_attempts_stop_when_satisfied_or_at_the_bound(self):
        self.assertFalse(self.results["short"]["apply"])
        self.assertTrue(self.results["short"]["retry"])
        self.assertFalse(self.results["short"]["satisfiable"])
        self.assertTrue(self.results["final"]["apply"])
        self.assertFalse(self.results["final"]["retry"])
        self.assertTrue(self.results["ready"]["apply"])
        self.assertTrue(self.results["ready"]["satisfiable"])
        self.assertFalse(self.results["ready"]["retry"])

    def test_only_growth_above_the_restored_position_needs_correction(self):
        self.assertTrue(self.results["mermaidAbove"])
        self.assertFalse(self.results["mermaidBelow"])


@unittest.skipUnless(NODE, "Node.js is required for explorer scroll tests")
class ExplorerScrollAdapterTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_node(
            ADAPTER_HARNESS,
            SCROLL_JS,
            VIEWER_JS,
            SCROLL_ADAPTER_JS,
            PERSISTENCE_JS,
            TABS_JS,
        )

    def test_hidden_panel_offset_is_applied_when_that_panel_is_shown(self):
        self.assertEqual(
            self.results["viewSwitch"],
            {"previewOnShow": 520, "sourceOnReturn": 240},
        )

    def test_cached_group_restore_keeps_the_active_preview_offset(self):
        self.assertEqual(self.results["groupSwitch"], {"preview": 830})

    def test_preview_arrival_reapplies_an_offset_the_empty_panel_could_not_hold(self):
        self.assertEqual(
            self.results["delayedPreview"],
            {
                "prematureApplied": False,
                "capturedWhilePending": 600,
                "beforeArrival": 0,
                "afterArrival": 600,
            },
        )

    def test_lazy_diagram_growth_never_overrides_a_reader_scroll(self):
        self.assertEqual(
            self.results["mermaid"],
            {"corrected": 600, "afterReaderMove": 680},
        )

    def test_a_late_preview_response_cannot_scroll_the_replacement_pane(self):
        self.assertEqual(self.results["lateArrival"], {"paints": 0, "scrollTop": 0})

    def test_an_in_place_markdown_refresh_keeps_the_preview_position(self):
        refreshed = self.results["saveRefresh"]
        self.assertTrue(refreshed["refreshed"])
        self.assertEqual(refreshed["afterArrival"], 700)

    def test_a_preview_offset_survives_the_real_tab_activation_path(self):
        self.assertEqual(self.results["tabRoundTrip"], {"preview": 640})


if __name__ == "__main__":
    unittest.main()
