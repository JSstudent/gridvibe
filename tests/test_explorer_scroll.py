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
GIT_SIDEBAR_JS = STATIC_JS / "explorer-git-sidebar.js"
SCROLL_ADAPTER_JS = STATIC_JS / "explorer-scroll-adapter.js"
TABS_JS = STATIC_JS / "explorer-tabs.js"
TIERS_JS = STATIC_JS / "explorer-tiers.js"
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
            sidebar: { tree: { scrollTop: 120 } }
        }
    }
};
/* A browsed listing. captureExplorerFileScroll() writes the offset twice —
   the `directory` metrics and the legacy top-level `listScrollLeft`/
   `listScrollTop` — and restoreExplorerFileScroll() falls back to the legacy
   pair whenever `directory` is absent. Both spellings describe the one
   scroller, so a revision mismatch has to take both. */
const listingTab = {
    collapsedLines: new Set(),
    view: {
        mode: 'preview',
        revisions: { directory: 'dir-one' },
        scroll: {
            activeView: 'preview',
            listScrollLeft: 42,
            listScrollTop: 1234,
            directory: { scrollLeftRatio: 0.2, scrollTopRatio: 0.6, wasAtBottom: false },
            panels: {},
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
    listingSame: scroll.resolveTabView(listingTab, { directory: 'dir-one' }),
    listingNew: scroll.resolveTabView(listingTab, { directory: 'dir-two' }),
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
/* The give-up status paints a Refresh button into the panel's own subtree and
   wires it there, so the stub has to be able to hand it back. */
const previewRefresh = {
    disabled: false,
    listeners: [],
    addEventListener(type, listener) {
        if (type === 'click') this.listeners.push(listener);
    },
    click() { this.listeners.forEach(listener => listener({})); }
};
preview.querySelector = selector => (
    String(selector).startsWith('[data-explorer-preview-refresh') ? previewRefresh : null
);

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
// fetches[] is drained as each response is resolved, so the pending queue
// cannot answer "how many requests did this path issue".
let fetchCount = 0;
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
    fetch: () => {
        fetchCount += 1;
        return new Promise(resolve => fetches.push(resolve));
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
sandbox.window.GridVibeExplorerScroll = sandbox.GridVibeExplorerScroll;
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[5], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[6], 'utf8'), sandbox);
sandbox.window.GridVibeExplorerPersistence = sandbox.GridVibeExplorerPersistence;
vm.runInContext(fs.readFileSync(process.argv[7], 'utf8'), sandbox);

const realTabRuntime = {
    active: sandbox.explorerActiveTab,
    activate: sandbox.activateExplorerTab,
    capture: sandbox.explorerCaptureActiveTabView,
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

function resolvePreview(html, stateRevision) {
    const resolve = fetches.shift();
    const body = { preview_html: html };
    // Omitted entirely unless a case is about the token, so the other cases
    // keep exercising the "older server, no token" shape.
    if (stateRevision !== undefined) { body.state_revision = stateRevision; }
    resolve({ ok: true, json: async () => body });
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

    /* The find query is captured against the tab *and* the path it was typed
       on, so leaving a tab cannot carry its search onto the next file. A
       pane-wide query re-ran the outgoing file's find over the incoming one and
       scrolled it to the first hit, overriding the offset just restored. */
    pane._explorerMode = 'file';
    pane._explorerActiveTabId = tabA.id;
    pane._explorerRenderedTabId = tabA.id;
    pane._explorerFilePath = tabA.path;
    pane._explorerFileContent = '# a';
    pane._explorerSearch = { query: 'needle' };
    realTabRuntime.capture(0);
    const capturedOnA = tabA.find && { ...tabA.find };
    // The permanent Preview tab shows a different file on every plain click, so
    // its stored query answers for that path only.
    pane._explorerActiveTabId = previewTab.id;
    pane._explorerRenderedTabId = previewTab.id;
    previewTab.path = 'first.md';
    pane._explorerFilePath = 'first.md';
    pane._explorerFileContent = '# first';
    pane._explorerSearch = { query: 'first-query' };
    realTabRuntime.capture(0);
    const previewCarry = previewTab.find && { ...previewTab.find };
    // Clearing the find clears what the tab carries — never a stale query kept
    // alive by a tab nobody searched again.
    pane._explorerSearch = { query: '' };
    realTabRuntime.capture(0);
    results.tabFindCarry = {
        capturedOnA,
        untouchedTab: tabB.find == null,
        previewCarry,
        clearedCarry: previewTab.find
    };

    // The content identity behind every revision comparison is computed once
    // per document, not once per capture. A group switch used to run a djb2
    // pass over every character of the open file (plus the join's transient
    // second copy of it) for every explorer pane, in both directions.
    sandbox.terminals[0] = pane;
    pane._explorerMode = 'file';
    pane._explorerFilePath = 'big.txt';
    pane._explorerFileContent = 'x'.repeat(4096);
    pane._explorerDiffLoaded = false;
    let hashed = 0;
    const realHash = sandbox.explorerHashText;
    sandbox.explorerHashText = value => { hashed += 1; return realHash(value); };
    const firstRevisions = sandbox.explorerCurrentContentRevisions(pane);
    const hashedFirst = hashed;
    const repeatRevisions = sandbox.explorerCurrentContentRevisions(pane);
    const hashedRepeat = hashed - hashedFirst;
    pane._explorerFileContent = 'y'.repeat(4096);
    const movedRevisions = sandbox.explorerCurrentContentRevisions(pane);
    results.contentRevisions = {
        hashedFirst,
        hashedRepeat,
        hashedAfterChange: hashed - hashedFirst - hashedRepeat,
        repeated: repeatRevisions.source === firstRevisions.source,
        aliased: repeatRevisions === firstRevisions,
        moved: movedRevisions.source !== firstRevisions.source
    };
    sandbox.explorerHashText = realHash;

    // The listing and the three sidebar scrollers are restored in one read
    // pass and one write pass, and only until they land.
    const ops = [];
    function instrument(el, id, box) {
        ['scrollHeight', 'clientHeight', 'scrollWidth', 'clientWidth'].forEach(name => {
            Object.defineProperty(el, name, {
                configurable: true,
                get() { ops.push('read:' + id); return box[name]; }
            });
        });
        let top = 0;
        let left = 0;
        Object.defineProperty(el, 'scrollTop', {
            configurable: true,
            get: () => top,
            set(value) { ops.push('write:' + id); top = value; }
        });
        Object.defineProperty(el, 'scrollLeft', {
            configurable: true,
            get: () => left,
            set(value) { ops.push('write:' + id); left = value; }
        });
        return box;
    }
    const treePanel = scroller('explorer-tree-panel-0', 900);
    const gitPanel = scroller('explorer-git-panel-0', 900);
    const searchPanel = scroller('explorer-search-panel-0', 900);
    elements.set('explorer-tree-panel-0', treePanel);
    elements.set('explorer-git-panel-0', gitPanel);
    elements.set('explorer-search-panel-0', searchPanel);
    const tall = { scrollHeight: 900, clientHeight: 300, scrollWidth: 300, clientWidth: 300 };
    instrument(list, 'list', { scrollHeight: 300, clientHeight: 300, scrollWidth: 300, clientWidth: 300 });
    // The sidebar tree is still as short as its viewport: nothing may
    // manufacture a smaller offset for it while it is still filling in.
    const treeBox = instrument(treePanel, 'tree', {
        scrollHeight: 300, clientHeight: 300, scrollWidth: 300, clientWidth: 300
    });
    instrument(gitPanel, 'git', { ...tall });
    instrument(searchPanel, 'search', { ...tall });
    select('source');
    pane._explorerPanelScrollStore = null;
    raf.length = 0;
    ops.length = 0;
    sandbox.restoreExplorerFileScroll(0, {
        activeView: 'source',
        listScrollLeft: 0,
        listScrollTop: 0,
        panels: {},
        sidebar: {
            tree: { scrollTop: 220, scrollLeft: 0 },
            git: { scrollTop: 140, scrollLeft: 0 },
            search: { scrollTop: 60, scrollLeft: 0 }
        }
    });
    const firstPassOps = ops.slice();
    const treeBeforeContent = treePanel.scrollTop;
    const retryQueued = raf.length;
    treeBox.scrollHeight = 900;
    let retryPasses = 0;
    while (raf.length && retryPasses < 20) {
        retryPasses += 1;
        raf.shift()();
    }
    results.outerScrollers = {
        ops: firstPassOps,
        treeBeforeContent,
        retryQueued,
        retryPasses,
        tree: treePanel.scrollTop,
        git: gitPanel.scrollTop,
        search: searchPanel.scrollTop
    };

    /* The lazy Preview request, counted. Every first entry into the panel asks
       for it twice inside one frame: the caller starts the fetch, then the
       find runs synchronously into restoreExplorerPreview(), which sees an
       unloaded panel and asks again. The stub stands in for the real find's
       preview branch, which is exactly that call. */
    sandbox.terminals[0] = pane;
    pane._explorerFilePath = 'lazy.md';
    pane._explorerFileContent = '# lazy';
    pane._explorerPreviewLoaded = false;
    pane._explorerPreviewHtml = '';
    pane._explorerPreviewLoadInFlight = null;
    pane._explorerSearch = { query: 'needle' };
    select('preview');
    preview.scrollHeight = 300;
    preview.scrollTop = 0;
    fetches.length = 0;
    fetchCount = 0;
    let searchApplied = 0;
    sandbox.applyExplorerSearch = () => {
        searchApplied += 1;
        sandbox.restoreExplorerPreview(0);
    };
    sandbox.setExplorerFileView(0, 'preview');
    const requestsBeforeArrival = fetchCount;
    const searchesBeforeArrival = searchApplied;
    const paintsBeforeLazy = paints;
    resolvePreview('<p>needle</p>');
    // The fetch, its json(), the joined caller and the loader's own finally
    // each cost a turn; drain generously rather than counting them.
    for (let turn = 0; turn < 40; turn += 1) {
        await Promise.resolve();
    }
    results.lazyPreviewRequest = {
        requestsBeforeArrival,
        requestsTotal: fetchCount,
        searchesBeforeArrival,
        // The find that ran against the loader's placeholder counted 0; the
        // arrival has to re-run it over the document that turned up.
        searchesAfterArrival: searchApplied - searchesBeforeArrival,
        paints: paints - paintsBeforeLazy,
        loaded: pane._explorerPreviewLoaded,
        inFlightCleared: pane._explorerPreviewLoadInFlight == null
    };

    /* No query means nothing to re-apply: the arrival hook must not repaint a
       panel on behalf of a find nobody typed. */
    pane._explorerPreviewLoaded = false;
    pane._explorerPreviewHtml = '';
    pane._explorerPreviewLoadInFlight = null;
    pane._explorerSearch = { query: '' };
    fetches.length = 0;
    fetchCount = 0;
    searchApplied = 0;
    sandbox.setExplorerFileView(0, 'preview');
    resolvePreview('<p>quiet</p>');
    for (let turn = 0; turn < 40; turn += 1) {
        await Promise.resolve();
    }
    results.lazyPreviewQuiet = {
        requestsTotal: fetchCount,
        searchesAfterArrival: searchApplied - 1
    };
    /* The *file* moving on, which the loader's own before/after checks cannot
       see: they compare the viewer against itself. Source and Preview are two
       reads now, so a write landing between them would put a render of the
       newer bytes beside Source's older ones. */
    const previewCase = async (revision, html) => {
        pane._explorerPreviewLoaded = false;
        pane._explorerPreviewHtml = '';
        pane._explorerPreviewLoadInFlight = null;
        pane._explorerSearch = { query: '' };
        fetches.length = 0;
        fetchCount = 0;
        const paintsBefore = paints;
        preview.innerHTML = '';
        previewRefresh.listeners.length = 0;
        previewRefresh.disabled = false;
        sandbox.setExplorerFileView(0, 'preview');
        resolvePreview(html, revision);
        for (let turn = 0; turn < 40; turn += 1) {
            await Promise.resolve();
        }
        return {
            loaded: pane._explorerPreviewLoaded,
            html: pane._explorerPreviewHtml,
            paints: paints - paintsBefore,
            panelHtml: preview.innerHTML,
            wired: previewRefresh.listeners.length
        };
    };

    pane._explorerFileStateRevision = '120:1700000000.000000';
    results.lazyPreviewStale = await previewCase('188:1700000009.000000', '<p>newer</p>');

    /* The retry. It runs the whole-file refresh, never a second Preview-only
       fetch: Source still carries the revision the response disagreed with, so
       refetching the preview alone would be declined for the same reason. */
    const realQuietRefresh = sandbox.refreshExplorerOpenFileQuiet;
    let quietRefreshes = 0;
    let quietAnswer = true;
    sandbox.refreshExplorerOpenFileQuiet = async () => {
        quietRefreshes += 1;
        return quietAnswer;
    };
    fetchCount = 0;
    previewRefresh.click();
    const disabledWhileRunning = previewRefresh.disabled;
    for (let turn = 0; turn < 10; turn += 1) {
        await Promise.resolve();
    }
    results.lazyPreviewStaleRetry = {
        refreshes: quietRefreshes,
        previewFetches: fetchCount,
        disabledWhileRunning,
        usableAfter: previewRefresh.disabled === false
    };

    // A refresh that fails leaves the affordance standing rather than
    // trading one dead end for another.
    quietAnswer = false;
    previewRefresh.click();
    for (let turn = 0; turn < 10; turn += 1) {
        await Promise.resolve();
    }
    results.lazyPreviewStaleRetryFailed = {
        refreshes: quietRefreshes,
        usableAfter: previewRefresh.disabled === false,
        panelHtml: preview.innerHTML
    };
    sandbox.refreshExplorerOpenFileQuiet = realQuietRefresh;

    results.lazyPreviewMatched = await previewCase('120:1700000000.000000', '<p>same</p>');
    // An older server sends no token at all; that must still work.
    results.lazyPreviewUntokened = await previewCase(undefined, '<p>untokened</p>');

    sandbox.applyExplorerSearch = () => {};

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

    def test_a_diff_revision_the_render_cannot_know_yet_keeps_its_offset(self):
        """Absent is undetermined, not different.

        The patch is fetched after the render, so a tab restore knows the
        file's bytes and not its diff. Reading that silence as a mismatch
        discarded the reader's Diff position on every tab switch — and,
        because it also made the whole view look changed, the Files tree and
        Git sidebar offsets with it. The offset is kept and handed to the Diff
        arrival path, which is the thing that can tell whether the patch still
        matches.
        """
        matching = self.results["matching"]["scroll"]
        self.assertEqual(matching["panels"]["diff"]["scrollTop"], 900)
        self.assertEqual(matching["sidebar"], {"tree": {"scrollTop": 120}})

        # Stated and different is still a plain mismatch: the patch moved, so
        # the offset into it is meaningless.
        self.assertNotIn("diff", self.results["partial"]["scroll"]["panels"])

        # And undetermined is not a licence. A file whose bytes moved has a
        # patch that moved with them, so the Diff offset drops with the rest
        # rather than waiting to be told.
        self.assertEqual(self.results["changed"]["scroll"]["panels"], {})
        self.assertEqual(self.results["changed"]["scroll"]["sidebar"], {})

    def test_a_new_listing_drops_both_spellings_of_the_old_listings_offset(self):
        """Navigation starts at the top, and it takes two deletions to say so.

        ``captureExplorerFileScroll()`` records the listing offset twice: the
        ``directory`` metrics and the legacy top-level ``listScrollLeft`` /
        ``listScrollTop`` pair, which ``restoreExplorerFileScroll()`` falls
        back to whenever ``directory`` is absent. Filtering only ``directory``
        on a revision mismatch therefore dropped nothing the restore could not
        find another way — and since ``applyScrollMetrics()`` prefers an exact
        offset to a ratio, the stale legacy number was applied verbatim, so
        opening a subdirectory landed at the parent listing's position.
        """
        same = self.results["listingSame"]["scroll"]
        # The listing is still the same listing: the reader keeps their place,
        # in both spellings.
        self.assertEqual(same["directory"]["scrollTopRatio"], 0.6)
        self.assertEqual(same["listScrollTop"], 1234)
        self.assertEqual(same["listScrollLeft"], 42)

        # A genuinely new directory keeps neither.
        new = self.results["listingNew"]["scroll"]
        self.assertNotIn("directory", new)
        self.assertNotIn("listScrollTop", new)
        self.assertNotIn("listScrollLeft", new)

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
            GIT_SIDEBAR_JS,
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

    def test_the_first_preview_visit_costs_exactly_one_request(self):
        """The in-flight join `loadExplorerDiff()` already carried.

        `_explorerPreviewLoaded` is set only once the response lands, so it
        cannot answer for a load still in the air — and every first entry into
        the panel asks twice inside one frame: the caller starts the fetch,
        then `applyExplorerSearch()` runs synchronously into
        `restoreExplorerPreview()`, which sees an unloaded panel and asks
        again. The second ask aborted the first and refetched the identical
        URL, so each first visit cost two requests and two server-side Markdown
        renders — Flask does not cancel on client abort, so the abandoned one
        still ran to completion.
        """
        lazy = self.results["lazyPreviewRequest"]

        self.assertEqual(lazy["requestsBeforeArrival"], 1)
        self.assertEqual(lazy["requestsTotal"], 1)
        # The second caller really did arrive; it was joined, not skipped.
        self.assertEqual(lazy["searchesBeforeArrival"], 1)
        self.assertTrue(lazy["loaded"])
        # And the join record is released once the load settles, so the next
        # visit to a different file is not answered by this one.
        self.assertTrue(lazy["inFlightCleared"])

    def test_an_active_find_is_re_applied_to_the_preview_that_arrives(self):
        """0/0 against the loader's placeholder is not an answer.

        With a query already typed, the find's preview branch runs before the
        fetch lands, so it marks the literal string `Rendering preview...` and
        sets the counter to 0. `paintExplorerPreview()` then replaces that
        whole subtree, and nothing re-ran the search — the reader had to retype
        or step the query to get any marks at all. Diff has carried this
        arrival hook all along; Preview now does too.
        """
        lazy = self.results["lazyPreviewRequest"]

        self.assertEqual(lazy["searchesAfterArrival"], 1)
        # The arrival painted before it re-ran the query, so the find had a
        # rendered document to mark rather than the loader's placeholder.
        self.assertGreaterEqual(lazy["paints"], 1)

        # A panel nobody searched is left alone: the hook is for re-applying a
        # query, not for repainting on every arrival.
        quiet = self.results["lazyPreviewQuiet"]
        self.assertEqual(quiet["requestsTotal"], 1)
        self.assertEqual(quiet["searchesAfterArrival"], 0)

    def test_a_preview_describing_different_bytes_is_declined(self):
        """The loader's own checks compare the viewer against itself.

        Source and Preview used to come from a single read and were consistent
        by construction. Splitting the preview out made them two reads, so a
        write landing between them yields a render of newer bytes beside
        Source's older ones — and the guard as written (same pane, same
        session, same path, same buffer, same element) catches the *viewer*
        moving on and cannot catch the *file* moving on.

        Declined rather than painted or refetched: the open-file change
        listener is already going to see the same revision move and reload the
        file, and the panel paints from that.
        """
        stale = self.results["lazyPreviewStale"]
        self.assertFalse(stale["loaded"])
        self.assertEqual(stale["html"], "")
        self.assertEqual(stale["paints"], 0)

    def test_a_declined_preview_says_so_and_offers_a_retry(self):
        """"Rendering preview…" is not a state anything on screen can leave.

        Declining silently relied on the open-file change listener to notice
        the same revision move — but that watcher suspends itself after
        repeated failures, and nothing else repaints this panel while the
        reader stays on it. Guardrail 8: name what happened, and give it
        something to click.
        """
        stale = self.results["lazyPreviewStale"]
        self.assertIn(
            "The file changed while the preview was rendering.", stale["panelHtml"]
        )
        self.assertIn("Refresh", stale["panelHtml"])
        self.assertEqual(stale["wired"], 1)

    def test_the_preview_retry_refreshes_the_file_not_the_preview(self):
        """A second Preview-only fetch would be declined for the same reason.

        Source still carries the revision the response disagreed with, so the
        recovery has to re-read the file and establish one new revision; the
        active Preview path then renders against that.
        """
        retry = self.results["lazyPreviewStaleRetry"]
        self.assertEqual(retry["refreshes"], 1)
        self.assertEqual(retry["previewFetches"], 0)
        # Busy state is the button's own disabled flag, not rewritten markup.
        self.assertTrue(retry["disabledWhileRunning"])
        self.assertTrue(retry["usableAfter"])

    def test_a_failed_preview_retry_leaves_the_affordance_standing(self):
        failed = self.results["lazyPreviewStaleRetryFailed"]
        self.assertEqual(failed["refreshes"], 2)
        self.assertTrue(failed["usableAfter"])
        self.assertIn("Refresh", failed["panelHtml"])

        # The ordinary case is unaffected: matching tokens paint as before.
        matched = self.results["lazyPreviewMatched"]
        self.assertTrue(matched["loaded"])
        self.assertEqual(matched["html"], "<p>same</p>")
        self.assertEqual(matched["paints"], 1)

        # And a response carrying no token at all is accepted, so the check
        # cannot turn an older server into a permanently blank panel.
        untokened = self.results["lazyPreviewUntokened"]
        self.assertTrue(untokened["loaded"])
        self.assertEqual(untokened["html"], "<p>untokened</p>")

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

    def test_the_find_query_is_carried_by_tab_and_path_never_by_the_pane(self):
        """One pane-wide find query is a find the reader did not ask for.

        Leaving a tab used to leave its query on the pane, so the next file
        opened was searched with it, painted with its marks and scrolled to its
        first hit — over the offset the tab restore had just put back. The
        query now travels with the tab *and* the path it was typed against,
        which is what lets the permanent Preview tab drop it when the file
        under it changes while a pinned tab keeps it.
        """
        carry = self.results["tabFindCarry"]
        self.assertEqual(carry["capturedOnA"], {"path": "a.md", "query": "needle"})
        # A tab nobody searched carries nothing to re-apply.
        self.assertTrue(carry["untouchedTab"])
        self.assertEqual(
            carry["previewCarry"], {"path": "first.md", "query": "first-query"}
        )
        # Clearing the find clears the tab's carry with it.
        self.assertIsNone(carry["clearedCarry"])

    def test_the_content_identity_is_computed_once_per_document(self):
        """A capture is not a reason to re-hash the file.

        ``explorerCurrentContentRevisions()`` is called for every explorer
        pane on every group switch, every tab switch and every presentation
        capture; the buffer it hashed is a stable string reference, so the
        answer only moves when the bytes do.
        """
        revisions = self.results["contentRevisions"]
        self.assertGreater(revisions["hashedFirst"], 0)
        self.assertEqual(revisions["hashedRepeat"], 0)
        self.assertTrue(revisions["repeated"])
        # A stored tab view must never alias the next caller's answer.
        self.assertFalse(revisions["aliased"])
        # Exact, not approximate: new bytes are a new identity, hashed again.
        self.assertGreater(revisions["hashedAfterChange"], 0)
        self.assertTrue(revisions["moved"])

    def test_the_outer_scrollers_are_read_then_written_and_only_until_they_land(self):
        """One read pass, one write pass, and no fixed number of passes.

        Reading an extent after writing another element's offset forces the
        layout again, once per element per pass, for every pane of the
        incoming group. The four unconditional passes this replaces also made
        a 20,000-row document pay three layouts it had no use for.
        """
        outer = self.results["outerScrollers"]
        reads = [at for at, op in enumerate(outer["ops"]) if op.startswith("read:")]
        writes = [at for at, op in enumerate(outer["ops"]) if op.startswith("write:")]
        self.assertTrue(reads)
        self.assertTrue(writes)
        self.assertLess(max(reads), min(writes))
        # Each target is measured once in that pass, never re-measured by the
        # write that follows it.
        self.assertEqual(len(reads), 16)

        # A panel that cannot hold its offset yet is left alone and retried;
        # the ones that can are already home.
        self.assertEqual(outer["treeBeforeContent"], 0)
        self.assertEqual(outer["retryQueued"], 1)
        self.assertEqual(outer["retryPasses"], 1)
        self.assertEqual(outer["tree"], 220)
        self.assertEqual(outer["git"], 140)
        self.assertEqual(outer["search"], 60)


LARGE_TIER_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

/* The Source panel as far as scrolling is concerned: a scroller whose extent
   is the sum of its children's heights, plus the browser behaviour the viewer
   has to survive. Two pieces of that behaviour are what these tests are about.

   A `scroll` event is dispatched a task after any offset changes — including
   the clamp to 0 that collapsing a scrolled element's content produces — and
   explorer-viewer.js listens for those in the CAPTURE phase on
   #explorer-list-N, so an inner .explorer-source-view scroll reaches it.

   And a frame is followed by a paint, so what the reader is actually shown is
   sampled after every frame, not only at the end. */
const LINE_HEIGHT = 20;
const frames = [];
const events = [];
const captureListeners = [];
const shown = [];

function classList() {
    const names = new Set();
    return {
        add: name => names.add(name),
        remove: name => names.delete(name),
        contains: name => names.has(name),
        toggle() {}
    };
}

function queueScroll(target) {
    events.push(() => captureListeners.forEach(listener => listener({ target })));
}

/* One node per thing the panel can hold; `lines` is its height in rows. */
function makeNode(kind, lines) {
    return {
        kind,
        lines: lines || 0,
        dataset: {},
        classList: classList(),
        addEventListener() {},
        set innerHTML(value) {
            this.lines = (value.match(/\n/g) || []).length + (value ? 1 : 0);
            code.relayout();
        },
        get innerHTML() { return ''; },
        insertAdjacentHTML(where, html) {
            this.lines += (html.match(/\n/g) || []).length + 1;
            code.relayout();
        },
        querySelector: () => null,
        querySelectorAll: () => []
    };
}

function parseMarkup(html) {
    const text = String(html || '');
    if (!text) return null;
    if (text.indexOf('explorer-source-plain') !== -1) return makeNode('plain', 0);
    if (text.indexOf('explorer-source-lines') !== -1) return makeNode('lines', 0);
    if (text.indexOf('explorer-source-tier-notice') !== -1) return makeNode('notice', 1);
    return makeNode('other', 0);
}

const code = {
    id: 'explorer-code-0',
    hidden: false,
    dataset: {},
    children: [],
    top: 0,
    scrollLeft: 0,
    clientHeight: 400,
    clientWidth: 300,
    scrollWidth: 600,
    scrollHeight: 400,
    style: { setProperty() {}, removeProperty() {} },
    classList: classList(),
    addEventListener() {},
    setAttribute() {},
    get scrollTop() { return this.top; },
    set scrollTop(value) {
        const max = Math.max(0, this.scrollHeight - this.clientHeight);
        const next = Math.max(0, Math.min(max, Number(value) || 0));
        if (next !== this.top) { this.top = next; queueScroll(this); }
    },
    relayout() {
        this.scrollHeight = Math.max(400,
            this.children.reduce((total, child) => total + child.lines, 0) * LINE_HEIGHT);
        const max = Math.max(0, this.scrollHeight - this.clientHeight);
        if (this.top > max) { this.top = max; queueScroll(this); }
    },
    set innerHTML(value) {
        this.children = [];
        const text = String(value || '');
        if (text.indexOf('explorer-source-tier-notice') !== -1) {
            this.children.push(makeNode('notice', 1));
        }
        if (text.indexOf('explorer-source-plain') !== -1) {
            const node = makeNode('plain', 0);
            node.lines = (text.match(/\n/g) || []).length;
            this.children.push(node);
        }
        if (text.indexOf('explorer-source-lines') !== -1) {
            const node = makeNode('lines', 0);
            node.lines = (text.match(/class="explorer-source-line"/g) || []).length;
            this.children.push(node);
        }
        this.relayout();
    },
    get innerHTML() { return ''; },
    appendChild(node) { this.children.push(node); this.relayout(); return node; },
    replaceChild(next, previous) {
        const at = this.children.indexOf(previous);
        if (at === -1) throw new Error('replaceChild: not a child');
        this.children[at] = next;
        this.relayout();
        return previous;
    },
    querySelector(selector) {
        if (selector === '.explorer-source-plain') {
            return this.children.find(child => child.kind === 'plain') || null;
        }
        if (selector === '.explorer-source-tier-notice') {
            return this.children.find(child => child.kind === 'notice') || null;
        }
        if (selector === ':scope > .explorer-source-lines') {
            return this.children.find(child => child.kind === 'lines') || null;
        }
        return null;
    },
    querySelectorAll: () => []
};

const sourcePanel = {
    hidden: false,
    dataset: { explorerFilePanel: 'source' },
    querySelector: selector => (selector === '.explorer-source-view' ? code : null)
};
const sourceButton = {
    dataset: { explorerFileView: 'source' },
    attrs: { 'aria-selected': 'true' },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    getAttribute(name) { return this.attrs[name]; }
};
const list = {
    id: 'explorer-list-0',
    dataset: {},
    scrollTop: 0,
    scrollLeft: 0,
    scrollHeight: 300,
    clientHeight: 300,
    scrollWidth: 300,
    clientWidth: 300,
    classList: classList(),
    addEventListener(type, listener, options) {
        if (type === 'scroll' && options && options.capture) captureListeners.push(listener);
    },
    querySelector(selector) {
        if (selector === '.explorer-editor-body') return { classList: classList() };
        if (selector === '.explorer-editor-name') return { textContent: '', title: '' };
        if (selector === '.explorer-editor-meta') return { textContent: '' };
        if (selector === '[data-explorer-file-view][aria-selected="true"]') return sourceButton;
        if (selector === '[data-explorer-file-panel]') return sourcePanel;
        const panel = selector.match(/^\[data-explorer-file-panel="([^"]+)"\]$/);
        if (panel) return panel[1] === 'source' ? sourcePanel : null;
        return null;
    },
    querySelectorAll(selector) {
        if (selector === '[data-explorer-file-view]') return [sourceButton];
        if (selector === '[data-explorer-file-panel]') return [sourcePanel];
        return [];
    }
};

const elements = new Map([['explorer-list-0', list], ['explorer-code-0', code]]);
const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'big.log',
    _explorerFileContent: '',
    _explorerFileLanguage: 'text',
    _explorerLastFileView: 'source',
    _explorerPreviewLoaded: false,
    _explorerDiffLoaded: false,
    _explorerDiffSplit: false,
    _explorerActiveTabId: '__preview__',
    _explorerRenderedTabId: '__preview__',
    _explorerTabs: [{ id: '__preview__', pinned: false, path: 'big.log', name: 'big.log' }]
};

let clock = 0;
const sandbox = {
    console,
    // Three milliseconds a call, so the 8 ms frame budget really does end a
    // frame and the build spans several of them.
    performance: { now: () => (clock += 3) },
    document: {
        getElementById: id => elements.get(id) || null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        createElement(tag) {
            if (tag !== 'template') return makeNode('other', 0);
            const template = { content: { firstElementChild: null } };
            Object.defineProperty(template, 'innerHTML', {
                set(value) { template.content.firstElementChild = parseMarkup(value); },
                get() { return ''; }
            });
            return template;
        },
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {},
        setTimeout: () => 0,
        clearTimeout() {},
        requestAnimationFrame: callback => frames.push(callback),
        cancelAnimationFrame() {},
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} }
    },
    navigator: {},
    setTimeout: () => 0,
    clearTimeout() {},
    requestAnimationFrame: callback => frames.push(callback),
    terminals: [pane],
    sessionIds: ['s0'],
    escHtml: value => String(value == null ? '' : value),
    fetch: () => new Promise(() => {})
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(file => {
    vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox);
});
sandbox.window.GridVibeExplorerTiers = sandbox.GridVibeExplorerTiers;
sandbox.window.GridVibeExplorerScroll = sandbox.GridVibeExplorerScroll;
sandbox.window.GridVibeExplorerPersistence = sandbox.GridVibeExplorerPersistence;
[process.argv[5], process.argv[6], process.argv[7], process.argv[8]].forEach(file => {
    vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox);
});

['applyExplorerLineWrapState', 'loadExplorerChangeMarks', 'applyExplorerEditorFontSize',
 'updateExplorerGitSummary', 'renderExplorerPathBreadcrumb', 'setExplorerEditChromeDisabled',
 'refreshExplorerEditControls', 'renderExplorerTabStrip', 'scheduleExplorerOccurrenceHighlight',
 'applyExplorerChangeMarks', 'cancelExplorerSearch', 'ensureExplorerPreviewLoaded',
 'restoreExplorerPreview', 'renderExplorerDiff', 'updateExplorerSearchControls',
 'wireExplorerContextMenu', 'notePanePresentationChanged'
].forEach(name => { sandbox[name] = () => {}; });

function flushEvents() { while (events.length) events.shift()(); }
function settle(limit = 5000) {
    let passes = 0;
    while ((frames.length || events.length) && passes < limit) {
        passes += 1;
        if (frames.length) frames.shift()();
        flushEvents();
        shown.push(code.scrollTop);
    }
    return passes;
}

const content = Array.from({ length: 25000 }, (_, at) => 'line ' + at).join('\n');
const appended = content + '\nline 25000';
const results = {};

sandbox.explorerEnsureViewerShell(0);
results.captureListeners = captureListeners.length;
sandbox.applyExplorerSourceTier(pane, content);
pane._explorerFileContent = content;
sandbox.renderExplorerSource(0);
results.tier = pane._explorerSourceTier;
results.openFrames = settle();
results.scrollHeight = code.scrollHeight;

code.scrollTop = 200000;
flushEvents();
results.readerAt = code.scrollTop;

// ── the watcher's in-place refresh: a log file gaining one line ────────────
shown.length = 0;
const captured = sandbox.captureExplorerFileScroll(0);
results.captured = captured.panels.source.scrollTop;
results.applied = sandbox.updateExplorerFileInPlace(0, {
    path: 'big.log',
    name: 'big.log',
    content: appended,
    language: 'text',
    preview_type: null,
    editable: true,
    git: null,
    git_context: null
}, captured);
results.afterRenderReturned = code.scrollTop;
results.refreshFrames = settle();
results.shownDuringRefresh = [results.afterRenderReturned].concat(shown);
results.restored = code.scrollTop;
results.storedAfterRestore =
    pane._explorerPanelScrollStore.panels.source.metrics.scrollTop;
results.tabViewAfterRestore =
    pane._explorerTabs[0].view.scroll.panels.source.scrollTop;

/* A build can still collapse the panel it is replacing — a file crossing the
   tier boundary swaps one kind of Source view for another — so a capture
   taken while one is in flight must still stand on the stored offset rather
   than on whatever the live element reports. */
const other = Array.from({ length: 25000 }, (_, at) => 'other ' + at).join('\n');
pane._explorerFileContent = other;
sandbox.applyExplorerSourceTier(pane, other);
sandbox.setExplorerPanelScrollState(0, {
    activeView: 'source',
    panels: { source: { scrollTop: 200000, scrollLeft: 0 } },
    sidebar: {}
});
sandbox.renderExplorerSource(0);
results.buildInFlight = Boolean(pane._explorerSourceRenderJob);
code.top = 0;                                   // the collapse, with no reader
const midBuild = sandbox.captureExplorerFileScroll(0);
results.capturedMidBuild = midBuild.panels.source.scrollTop;
results.storedMidBuild =
    pane._explorerPanelScrollStore.panels.source.metrics.scrollTop;

process.stdout.write(JSON.stringify(results));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer scroll tests")
class ExplorerLargeSourceScrollTestCase(unittest.TestCase):
    """A frame-sliced rebuild keeps the reader where they were, throughout."""

    @classmethod
    def setUpClass(cls):
        cls.results = _run_node(
            LARGE_TIER_HARNESS,
            TIERS_JS,
            SCROLL_JS,
            PERSISTENCE_JS,
            VIEWER_JS,
            GIT_SIDEBAR_JS,
            SCROLL_ADAPTER_JS,
            TABS_JS,
        )

    def test_the_scenario_is_the_one_the_regression_needs(self):
        """The harness must reproduce the conditions, or it proves nothing."""
        self.assertEqual(self.results["tier"], "large")
        # The paint really is spread over frames, not done in one pass.
        self.assertGreater(self.results["openFrames"], 1)
        self.assertGreater(self.results["refreshFrames"], 1)
        # The page really is listening for scroll in the capture phase, so an
        # inner scroller's event reaches captureExplorerFileScroll().
        self.assertEqual(self.results["captureListeners"], 1)
        self.assertEqual(self.results["readerAt"], 200000)
        self.assertTrue(self.results["applied"])

    def test_an_in_place_refresh_never_shows_the_reader_the_top_of_the_file(self):
        """The rebuild is assembled off-screen, so nothing flashes.

        Emptying the scroller first collapses the document under the reader,
        which parks them at line 1 for every frame the rebuild lasts before
        the restore snaps them home — a log file gaining a line did that on
        every poll. The replacement is built detached and swapped in whole, so
        every frame in between shows the position they were already at.
        """
        self.assertEqual(self.results["captured"], 200000)
        self.assertEqual(
            set(self.results["shownDuringRefresh"]),
            {200000},
            "the reader was shown a different offset mid-rebuild",
        )
        self.assertEqual(self.results["restored"], 200000)

    def test_a_capture_taken_mid_build_stands_on_the_stored_offset(self):
        """The rebuild's own reset is not the reader's position.

        Where a build does still collapse what it replaces, the browser
        reports that clamp as a scroll event a task later, and the
        capture-phase listener answers it with a full capture. Storing it
        would overwrite the offset the restore queued behind that same build
        is about to apply.
        """
        self.assertTrue(self.results["buildInFlight"])
        self.assertEqual(self.results["capturedMidBuild"], 200000)
        self.assertEqual(self.results["storedMidBuild"], 200000)
        # Neither the pane store nor the tab's saved view may carry a clamp.
        self.assertEqual(self.results["storedAfterRestore"], 200000)
        self.assertEqual(self.results["tabViewAfterRestore"], 200000)


if __name__ == "__main__":
    unittest.main()
