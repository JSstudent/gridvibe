"""Source-view ground work for change marking: the fixed
``.explorer-source-frame`` wrapper around the scrollable Source view, the
``explorerPanelScrollTarget`` branch that sees through it, and the per-pane
memoization of the whole-document token map. None of this is user-visible on
its own, so the suite is contract-level on the served assets (selectors,
``data-*`` hooks, named functions) plus ordering guarantees the CSS relies on.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import api

VIEWER_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "explorer-viewer.js"
NODE = shutil.which("node")

# Enough document for explorer-viewer.js to evaluate, plus a panel whose
# querySelector answers the handful of selectors the scroll target asks for.
SCROLL_TARGET_HARNESS = """
const fs = require('fs');
const vm = require('vm');

function panelStub(kind, children) {
    const nodes = new Map(Object.entries(children || {}));
    const make = selector => {
        const entry = nodes.get(selector);
        if (!entry) { return null; }
        return {
            name: entry.name,
            querySelector: inner => (entry.children || []).includes(inner)
                ? { name: `${entry.name}${inner}` }
                : null
        };
    };
    return {
        name: 'panel',
        dataset: { explorerFilePanel: kind },
        querySelector: selector => make(selector)
    };
}

const sandbox = {
    console,
    document: {
        getElementById: () => null,
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
    sessionIds: [],
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const view = children => ({ '.explorer-source-view': { name: 'view', children } });
const cases = {
    // The overlay's textarea is overflow:hidden and content-height, so the
    // view goes on scrolling both layers.
    overlay: panelStub('source', view(['.explorer-edit-stack', '.explorer-source-editor'])),
    // Overlay stood down: the full-height textarea scrolls itself.
    bareEditor: panelStub('source', view(['.explorer-source-editor'])),
    readOnly: panelStub('source', view([])),
    frameOnly: panelStub('source', {}),
    diff: panelStub('diff', { '.explorer-diff-content': { name: 'diffContent' } })
};

const resolved = {};
Object.entries(cases).forEach(([key, panel]) => {
    resolved[key] = sandbox.explorerPanelScrollTarget(panel).name;
});
resolved.missing = String(sandbox.explorerPanelScrollTarget(null));
process.stdout.write(JSON.stringify(resolved));
"""


class ExplorerSourceFrameTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _viewer(self) -> str:
        return self._static("js/explorer-viewer.js")

    def test_source_panel_is_wrapped_in_a_fixed_frame(self):
        viewer = self._viewer()
        # The frame — not the scrollable view — carries the panel hook and the
        # panel class, so panel show/hide and scroll capture/restore address
        # the frame and whatever is docked beside the text hides with it.
        self.assertIn(
            '<div class="explorer-source-frame explorer-editor-panel" '
            'data-explorer-file-panel="source"',
            viewer,
        )
        # Every existing `explorer-code-N` lookup (15+ across viewer and
        # editor) still resolves the inner scrollable view, which no longer
        # carries the panel hook or class.
        self.assertIn(
            '<div class="explorer-source-view" id="explorer-code-${index}"></div>',
            viewer,
        )

    @unittest.skipUnless(NODE, "Node.js is required for scroll-target tests")
    def test_panel_scroll_target_sees_through_the_source_frame(self):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(SCROLL_TARGET_HARNESS, encoding="utf-8")
            completed = subprocess.run(
                ["node", str(script_path), str(VIEWER_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        resolved = json.loads(completed.stdout)

        # Without the source branch, scroll capture/restore would read the
        # overflow:hidden frame and pin every restored file to the top.
        self.assertEqual(resolved["readOnly"], "view")
        # Under the in-place editor's highlight overlay the textarea is
        # overflow:hidden and exactly as tall as its content, so the view goes
        # on scrolling both layers — reading the textarea would capture a
        # permanent zero and lose the position on every save.
        self.assertEqual(resolved["overlay"], "view")
        # Overlay stood down: the full-height textarea is the real scroller.
        self.assertEqual(resolved["bareEditor"], "view.explorer-source-editor")
        # A frame with no inner view falls back to the frame itself, and the
        # diff panel keeps its own inner-scroller behaviour.
        self.assertEqual(resolved["frameOnly"], "panel")
        self.assertEqual(resolved["diff"], "diffContent")
        self.assertEqual(resolved["missing"], "null")

    def test_source_frame_css_keeps_the_frame_fixed(self):
        css = self._static("css/terminals.css")
        self.assertIn(".explorer-source-frame {", css)
        block = css[css.index(".explorer-source-frame {"):]
        block = block[: block.index("}")]
        # Grid with a fixed side lane, and the frame itself never scrolls —
        # the inner .explorer-source-view keeps `overflow: auto`.
        self.assertIn("display: grid;", block)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto;", block)
        self.assertIn("overflow: hidden;", block)
        # Equal specificity with `.explorer-editor-panel { overflow: auto }`,
        # so the override only wins by coming later in the stylesheet.
        self.assertLess(
            css.index(".explorer-editor-panel {"),
            css.index(".explorer-source-frame {"),
        )
        # The hidden-panel rule must still beat the frame's `display: grid`.
        self.assertIn(".explorer-editor-panel[hidden] {", css)

    def test_document_tokens_are_memoized_per_pane(self):
        viewer = self._viewer()
        self.assertIn(
            "function explorerHighlightDocumentLinesCached(pane, content, normalizedLanguage)",
            viewer,
        )
        cached = viewer[
            viewer.index("function explorerHighlightDocumentLinesCached"):
            viewer.index("function explorerRenderHighlightedRuns")
        ]
        # A hit requires the same content and the same normalized language;
        # a miss tokenizes once and stores all three.
        self.assertIn(
            "cache.content === content && cache.language === normalizedLanguage",
            cached,
        )
        self.assertIn(
            "pane._explorerHighlightCache = { content, language: normalizedLanguage, lines };",
            cached,
        )

    def test_source_render_reuses_the_cached_token_map(self):
        viewer = self._viewer()
        render = viewer[
            viewer.index("function renderExplorerSource(index, searchRanges = [])"):
            viewer.index("function explorerPreviewBlockLanguage(code)")
        ]
        # Search keystrokes, wrap toggles and Markdown folds all re-render
        # without touching the content; they must hit the pane cache instead
        # of re-tokenizing the whole document each time.
        self.assertIn("explorerHighlightDocumentLinesCached(", render)
        self.assertIn("pane, content, normalizeExplorerLanguage(language)", render)
        self.assertIn("highlightedLines\n        );", render)

    def test_source_lines_renderer_honours_a_passed_in_token_map(self):
        viewer = self._viewer()
        lines = viewer[
            viewer.index("function renderExplorerSourceLines("):
            viewer.index("function explorerRevealMarkdownSearchMatches")
        ]
        # Only an absent argument tokenizes inline; an explicit null (cached
        # miss for an unsupported language) must not re-tokenize per render.
        self.assertIn("highlightedLines !== undefined", lines)
        self.assertIn(": explorerHighlightDocumentLines(content, normalizedLanguage);", lines)
        self.assertIn("explorerRenderHighlightedRuns(runs.get(record.number), searchRanges)", lines)


if __name__ == "__main__":
    unittest.main()
