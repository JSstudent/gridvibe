"""The explorer's HTML Preview panel, executed against the real viewer.

An HTML file previews as itself in a sandboxed iframe rather than through the
Markdown pipeline's innerHTML. These tests load ``explorer-viewer.js`` into a
Node VM with a small DOM stub and observe what the panel actually receives:
the frame, its sandbox, the route it loads, when it is (and is not) replaced,
and which Markdown-only affordances stand down.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
SCROLL_JS = STATIC_JS / "explorer-scroll.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
PERSISTENCE_JS = STATIC_JS / "explorer-persistence.js"
TABS_JS = STATIC_JS / "explorer-tabs.js"
NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

function element(id, dataset = {}) {
    return {
        id,
        hidden: false,
        disabled: false,
        dataset: { ...dataset },
        attrs: {},
        children: [],
        textContent: '',
        style: {
            props: {},
            setProperty(name, value) { this.props[name] = String(value); },
            removeProperty(name) { delete this.props[name]; }
        },
        classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
        setAttribute(name, value) { this.attrs[name] = String(value); },
        getAttribute(name) { return this.attrs[name]; },
        replaceChildren(...nodes) { this.children = nodes; },
        querySelector(selector) {
            return selector === 'iframe'
                ? this.children.find(node => node.tagName === 'IFRAME') || null
                : null;
        },
        querySelectorAll: () => [],
        addEventListener() {}
    };
}

const preview = element('explorer-preview-0', {
    explorerFilePanel: 'preview',
    explorerPreviewKind: 'html'
});
const list = element('explorer-list-0');
/* The header's view tabs and -/+ zoom pair, which is all of the header the
   zoom reads: which view is selected, and the controls it relabels. */
let selectedView = 'preview';
const zoomValue = element('zoom-value');
const zoomDecrease = element('zoom-decrease');
const zoomIncrease = element('zoom-increase');
list.querySelector = selector => {
    if (selector === '[data-explorer-file-view][aria-selected="true"]') {
        return { dataset: { explorerFileView: selectedView } };
    }
    if (selector === '[data-explorer-zoom-value="0"]') return zoomValue;
    if (selector === '[data-explorer-zoom-decrease="0"]') return zoomDecrease;
    if (selector === '[data-explorer-zoom-increase="0"]') return zoomIncrease;
    return null;
};
const tab = {};
const code = element('explorer-code-0');
const elements = new Map([
    ['explorer-list-0', list],
    ['explorer-code-0', code],
    ['explorer-preview-0', preview]
]);
let fetchCount = 0;
const pane = {
    _explorerMode: 'file',
    _explorerFilePath: 'docs/mock ups.html',
    _explorerFileName: 'mock ups.html',
    _explorerFileContent: '<p>one</p>',
    _explorerFileStateRevision: 'rev-1',
    _explorerFileLanguage: 'html',
    _explorerPreviewKind: 'html',
    _explorerSourceTier: 'full',
    _explorerPreviewLoaded: false,
    _explorerPreviewHtml: ''
};

const sandbox = {
    console,
    document: {
        getElementById: id => elements.get(id) || null,
        createElement: tag => {
            const node = element('');
            node.tagName = String(tag).toUpperCase();
            // Each frame's own window, the identity a message's source is
            // compared against.
            node.contentWindow = { frame: node };
            return node;
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        listeners: {},
        addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); },
        setTimeout: () => 0,
        clearTimeout() {},
        requestAnimationFrame: () => 0,
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} }
    },
    navigator: {},
    setTimeout: () => 0,
    clearTimeout() {},
    requestAnimationFrame: () => 0,
    terminals: [pane],
    sessionIds: ['s 0'],
    escHtml: value => String(value == null ? '' : value),
    // Owned by explorer-diff.js; this file has no Git diff to report.
    explorerHasGitDiff: () => false,
    // Owned by explorer-tabs.js / explorer-diff.js respectively.
    explorerActiveTab: () => tab,
    scheduleExplorerDiffScrollbarSync: () => {},
    fetch: () => {
        fetchCount += 1;
        return new Promise(() => {});
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
sandbox.window.GridVibeExplorerScroll = sandbox.GridVibeExplorerScroll;
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[4], 'utf8'), sandbox);
sandbox.window.GridVibeExplorerPersistence = sandbox.GridVibeExplorerPersistence;
vm.runInContext(fs.readFileSync(process.argv[5], 'utf8'), sandbox);
// explorer-tabs.js brings its own; this harness drives one tab directly.
sandbox.explorerActiveTab = () => tab;
let persistCount = 0;
sandbox.persistExplorerTabsToSession = () => { persistCount += 1; };

(async () => {
    const results = {};
    results.kinds = ['markdown', 'html', 'image', null, 'pdf'].map(
        kind => sandbox.explorerPreviewKindForPayload({ preview_type: kind })
    );

    await sandbox.ensureExplorerPreviewLoaded(0);
    const first = preview.children[0] || null;
    results.first = first && {
        tag: first.tagName,
        sandbox: first.attrs.sandbox,
        referrer: first.attrs.referrerpolicy,
        src: first.src,
        title: first.title,
        count: preview.children.length
    };
    results.loaded = pane._explorerPreviewLoaded;

    // A revisit of the same bytes keeps the frame, and the reader's place in it.
    sandbox.paintExplorerPreview(0);
    await sandbox.ensureExplorerPreviewLoaded(0);
    results.revisitKeptFrame = preview.children[0] === first;

    // New bytes (a save, or an in-place refresh) load a new frame.
    pane._explorerFileContent = '<p>two</p>';
    sandbox.paintExplorerPreview(0);
    results.newBytesReplacedFrame = preview.children[0] !== first
        && preview.children[0]?.tagName === 'IFRAME';

    results.fetchCount = fetchCount;

    /* A refusal page tells its panel through one fixed message. */
    const post = (source, data) => (sandbox.window.listeners.message || [])
        .forEach(listener => listener({ source, data }));
    const refusal = status => ({ source: 'gridvibe-html-preview', status });
    results.messageListeners = (sandbox.window.listeners.message || []).length;

    // Anything not from this panel's own frame, or not the fixed shape, is ignored.
    let shown = preview.children[0];
    const stamp = preview.dataset.explorerPreviewRender;
    post({}, refusal(500));
    post(shown.contentWindow, { source: 'someone-else', status: 500 });
    post(shown.contentWindow, 'gridvibe-html-preview');
    results.ignored = preview.dataset.explorerPreviewRender === stamp
        && preview.children[0] === shown;

    // A transient failure: the stamp comes off, so an unchanged refresh retries.
    post(shown.contentWindow, refusal(500));
    results.failedStampCleared = preview.dataset.explorerPreviewRender === undefined;
    sandbox.paintExplorerPreview(0);
    results.retryLoadedNewFrame = preview.children[0] !== shown
        && preview.children[0]?.tagName === 'IFRAME';

    // A 409: the file moved on after Source read it — the stale notice.
    shown = preview.children[0];
    post(shown.contentWindow, refusal(409));
    results.conflict = {
        notice: String(preview.innerHTML || '').includes('The file changed'),
        stampCleared: preview.dataset.explorerPreviewRender === undefined
    };
    // The next paint (what the notice's Refresh leads to) brings a frame back.
    sandbox.paintExplorerPreview(0);
    results.conflictRecovers = preview.children[0]?.tagName === 'IFRAME'
        && preview.children[0] !== shown;

    // A new revision for the same bytes is a new frame request, bound to it.
    shown = preview.children[0];
    pane._explorerFileStateRevision = 'rev-2';
    sandbox.paintExplorerPreview(0);
    results.newRevision = {
        replaced: preview.children[0] !== shown,
        src: preview.children[0]?.src
    };

    // Find has nothing to mark inside the frame; Source still answers.
    results.find = {
        htmlPreview: sandbox.explorerPaneAllowsFind(pane, 'preview'),
        htmlSource: sandbox.explorerPaneAllowsFind(pane, 'source'),
        offered: sandbox.explorerFileOffersFind(pane),
        markdownPreview: sandbox.explorerPaneAllowsFind(
            { ...pane, _explorerPreviewKind: 'markdown' }, 'preview'
        )
    };

    // On the HTML preview the -/+ pair zooms the page, not the editor font.
    const zoomVar = '--explorer-html-preview-zoom';
    sandbox.applyExplorerEditorFontSize(0);
    const fontBefore = tab.fontSize;
    results.zoomInitial = { label: zoomValue.textContent, panel: preview.style.props[zoomVar] };
    sandbox.stepExplorerZoom(0, 1);
    sandbox.stepExplorerZoom(0, 1);
    results.zoomIn = {
        label: zoomValue.textContent,
        panel: preview.style.props[zoomVar],
        fontUnchanged: tab.fontSize === fontBefore,
        title: zoomIncrease.attrs.title
    };
    results.persistedPerStep = persistCount;
    // What the tab saves, and what a restore reads back from it.
    const saved = sandbox.explorerPersistableTabView({
        view: { mode: 'preview', revisions: {}, scroll: {} },
        htmlZoom: tab.htmlZoom
    });
    const savedAtDefault = sandbox.explorerPersistableTabView({
        view: { mode: 'preview', revisions: {}, scroll: {} },
        htmlZoom: 1
    });
    results.persisted = {
        saved: saved && saved.html_zoom,
        defaultSaved: Boolean(savedAtDefault) && 'html_zoom' in savedAtDefault,
        restored: sandbox.explorerPersistedTabHtmlZoom({ html_zoom: 1.24 }),
        restoredJunk: sandbox.explorerPersistedTabHtmlZoom({ html_zoom: 'big' })
    };
    for (let step = 0; step < 40; step += 1) sandbox.stepExplorerZoom(0, -1);
    results.zoomFloor = {
        label: zoomValue.textContent,
        decreaseDisabled: zoomDecrease.disabled,
        increaseDisabled: zoomIncrease.disabled
    };
    // A freshly painted frame (new bytes) keeps the tab's zoom.
    pane._explorerFileContent = '<p>zoomed</p>';
    sandbox.paintExplorerPreview(0);
    results.zoomSurvivesRepaint = preview.style.props[zoomVar];

    // On Source the same pair is the font size again, and the zoom is kept.
    selectedView = 'source';
    const zoomBeforeSource = tab.htmlZoom;
    sandbox.syncExplorerZoomControls(0);
    const labelOnSource = zoomValue.textContent;
    sandbox.stepExplorerZoom(0, 1);
    results.sourceStep = {
        labelOnSource,
        fontGrew: tab.fontSize > fontBefore,
        zoomKept: tab.htmlZoom === zoomBeforeSource,
        title: zoomIncrease.attrs.title
    };
    selectedView = 'preview';

    // A Markdown panel cannot be refreshed in place into an HTML one.
    preview.dataset.explorerPreviewKind = 'markdown';
    pane._explorerFilePath = 'docs/mock ups.html';
    results.kindChangeRebuilds = sandbox.updateExplorerFileInPlace(0, {
        path: 'docs/mock ups.html',
        content: '<p>three</p>',
        preview_type: 'html',
        git: null
    }) === false;

    process.stdout.write(JSON.stringify(results));
})().catch(error => {
    process.stderr.write(String(error && error.stack || error));
    process.exit(1);
});
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


@unittest.skipUnless(NODE, "Node.js is required for explorer HTML preview tests")
class ExplorerHtmlPreviewTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_node(HARNESS, SCROLL_JS, VIEWER_JS, PERSISTENCE_JS, TABS_JS)

    def test_only_markdown_and_html_payloads_offer_a_preview_panel(self):
        self.assertEqual(self.results["kinds"], ["markdown", "html", "", "", ""])

    def test_the_document_loads_in_a_script_only_sandboxed_frame(self):
        first = self.results["first"]
        self.assertIsNotNone(first)
        self.assertEqual(first["tag"], "IFRAME")
        self.assertEqual(first["count"], 1)
        # Never allow-same-origin: that is what keeps the page's scripts out of
        # GridVibe's origin.
        self.assertEqual(first["sandbox"], "allow-scripts")
        self.assertEqual(first["referrer"], "no-referrer")
        # Bound to the revision Source is showing, so the frame can never
        # show newer bytes than Source does.
        self.assertEqual(
            first["src"],
            "/api/explorer/s%200/file/html?path=docs%2Fmock%20ups.html&revision=rev-1",
        )
        self.assertIn("mock ups.html", first["title"])
        self.assertTrue(self.results["loaded"])

    def test_the_frame_fetches_its_own_document(self):
        # No Markdown render request is made for an HTML file.
        self.assertEqual(self.results["fetchCount"], 0)

    def test_refusal_messages_are_only_heard_from_the_panels_own_frame(self):
        self.assertEqual(self.results["messageListeners"], 1)
        self.assertTrue(self.results["ignored"])

    def test_a_failed_frame_is_retried_instead_of_kept(self):
        self.assertTrue(self.results["failedStampCleared"])
        self.assertTrue(self.results["retryLoadedNewFrame"])

    def test_a_revision_conflict_shows_the_refresh_notice(self):
        self.assertEqual(
            self.results["conflict"], {"notice": True, "stampCleared": True}
        )
        self.assertTrue(self.results["conflictRecovers"])

    def test_a_new_revision_requests_a_new_bound_frame(self):
        self.assertTrue(self.results["newRevision"]["replaced"])
        self.assertTrue(self.results["newRevision"]["src"].endswith("&revision=rev-2"))

    def test_a_revisit_keeps_the_frame_and_new_bytes_replace_it(self):
        self.assertTrue(self.results["revisitKeptFrame"])
        self.assertTrue(self.results["newBytesReplacedFrame"])

    def test_find_stands_down_on_the_frame_but_not_on_source(self):
        self.assertEqual(
            self.results["find"],
            {
                "htmlPreview": False,
                "htmlSource": True,
                "offered": True,
                "markdownPreview": True,
            },
        )

    def test_the_zoom_pair_zooms_the_html_preview_independently(self):
        self.assertEqual(self.results["zoomInitial"], {"label": "100%", "panel": "1"})
        zoom_in = self.results["zoomIn"]
        # Two steps up the ladder: 100% -> 110% -> 125%.
        self.assertEqual(zoom_in["label"], "125%")
        self.assertEqual(zoom_in["panel"], "1.25")
        self.assertTrue(zoom_in["fontUnchanged"])
        self.assertEqual(zoom_in["title"], "Zoom in preview")

    def test_each_zoom_step_is_saved_with_the_tab(self):
        # Two steps, two presentation changes queued for the workspace.
        self.assertEqual(self.results["persistedPerStep"], 2)
        self.assertEqual(
            self.results["persisted"],
            {
                "saved": 1.25,
                # 100% persists nothing, like an unzoomed font size.
                "defaultSaved": False,
                # A restored value is snapped back onto the ladder.
                "restored": 1.25,
                "restoredJunk": 0,
            },
        )

    def test_the_zoom_stops_at_the_bottom_of_the_ladder(self):
        self.assertEqual(
            self.results["zoomFloor"],
            {"label": "25%", "decreaseDisabled": True, "increaseDisabled": False},
        )

    def test_a_reloaded_frame_keeps_the_tabs_zoom(self):
        self.assertEqual(self.results["zoomSurvivesRepaint"], "0.25")

    def test_source_keeps_the_font_size_and_leaves_the_zoom_alone(self):
        step = self.results["sourceStep"]
        self.assertTrue(step["labelOnSource"].endswith("px"))
        self.assertTrue(step["fontGrew"])
        self.assertTrue(step["zoomKept"])
        self.assertEqual(step["title"], "Increase font size")

    def test_a_change_of_preview_kind_hands_back_to_a_full_rebuild(self):
        self.assertTrue(self.results["kindChangeRebuilds"])


if __name__ == "__main__":
    unittest.main()
