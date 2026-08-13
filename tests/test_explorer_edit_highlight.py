"""The in-place editor's highlight overlay owns two pure decisions, and both
run in Node against the real module rather than being asserted as source text.

* Viability drives the silent fallback. The overlay is an enhancement, so an
  oversized buffer — or the policy module never loading — has to leave edit
  mode exactly as it was: a bare textarea, no notice, no second global sink.
* The refresh rule keeps the underlay's rebuild off the keystroke path. It is
  a whole-document render, so it is coalesced into one animation frame and
  skipped outright when the draft has not moved (arrow keys, a click, a
  keystroke that inserted nothing all reach the same input handler).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
HIGHLIGHT_JS = STATIC_JS / "explorer-edit-highlight.js"
OVERLAY_JS = STATIC_JS / "explorer-edit-overlay.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"

NODE = shutil.which("node")

# The adapter half runs against the *real* row renderer — explorer-viewer.js
# evaluated in the same context — so what the overlay paints behind the
# textarea is literally what the read-only Source view paints.
OVERLAY_HARNESS = """
const fs = require('fs');
const vm = require('vm');

function element(name) {
    const node = {
        name,
        innerHTML: '',
        paints: 0,
        dataset: {},
        classes: new Set(),
        style: {
            props: {},
            setProperty(key, value) { this.props[key] = value; },
            removeProperty(key) { delete this.props[key]; }
        },
        listeners: [],
        addEventListener(type) { node.listeners.push(type); },
        querySelector: () => null
    };
    node.classList = {
        add: name => node.classes.add(name),
        remove: name => node.classes.delete(name),
        contains: name => node.classes.has(name)
    };
    return node;
}

const nodes = {};
let nextHandle = 0;
const frames = new Map();
const runFrames = () => {
    const queued = Array.from(frames.values());
    frames.clear();
    queued.forEach(callback => callback());
};

const sandbox = {
    console,
    document: {
        getElementById: id => nodes[id] || null,
        querySelector: selector => nodes[selector] || null,
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
        requestAnimationFrame: callback => {
            nextHandle += 1;
            frames.set(nextHandle, callback);
            return nextHandle;
        },
        cancelAnimationFrame: handle => { frames.delete(handle); }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    terminals: [],
    sessionIds: [],
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
// In a page `window` *is* the global object; the stub above is a separate one.
sandbox.window.GridVibeExplorerEditHighlight = sandbox.GridVibeExplorerEditHighlight;

const TEXTAREA = '<textarea id="explorer-edit-textarea-0"></textarea>';

function openEditor(draft) {
    const view = element('view');
    nodes['explorer-code-0'] = view;
    nodes['explorer-edit-textarea-0'] = element('textarea');
    sandbox.terminals[0] = { _explorerEdit: { draft } };
    sandbox.mountExplorerEditOverlay(0, view, TEXTAREA);
    return view;
}

// Once mounted, the stack and its underlay are children of the panel; the
// stub resolves them by the same selectors the adapter uses.
function wireStack() {
    const stack = element('stack');
    const underlay = element('underlay');
    Object.defineProperty(underlay, 'innerHTML', {
        get() { return underlay._html || ''; },
        set(value) { underlay._html = value; underlay.paints += 1; }
    });
    stack.querySelector = selector =>
        (selector === '.explorer-edit-underlay' ? underlay : null);
    nodes['[data-explorer-edit-stack="0"]'] = stack;
    return { stack, underlay };
}

const results = {};

// 1. A viable buffer: the rows go in behind the textarea, hidden from
//    assistive technology, with one gutter width for the whole document.
{
    const view = openEditor('alpha\\nbeta\\ngamma\\n');
    results.mounted = {
        html: view.innerHTML,
        rendered: sandbox.terminals[0]._explorerEditOverlayDraft,
        focusListeners: nodes['explorer-edit-textarea-0'].listeners.slice()
    };
}

// 2. Oversized: the panel holds exactly the bare textarea, as it always did.
{
    const view = openEditor('x'.repeat(2 * 1024 * 1024 + 1));
    results.oversized = { html: view.innerHTML };
}

// 3. A burst of typing costs one paint, and the last draft is the one shown.
{
    openEditor('one\\ntwo\\n');
    const { stack, underlay } = wireStack();
    const state = sandbox.terminals[0]._explorerEdit;
    ['one\\ntwo\\nt', 'one\\ntwo\\nth', 'one\\ntwo\\nthree'].forEach(draft => {
        state.draft = draft;
        sandbox.refreshExplorerEditOverlay(0);
    });
    const paintsBeforeFrame = underlay.paints;
    runFrames();
    // An input that changed nothing (a click, an arrow key) must not repaint.
    sandbox.refreshExplorerEditOverlay(0);
    runFrames();
    results.coalesced = {
        paintsBeforeFrame,
        paints: underlay.paints,
        showsLastDraft: underlay.innerHTML.includes('three'),
        gutter: stack.style.props['--explorer-source-gutter-width']
    };
}

// 4. A new digit in the line count widens the gutter for every row at once.
{
    openEditor('start\\n');
    const { stack } = wireStack();
    sandbox.terminals[0]._explorerEdit.draft = 'line\\n'.repeat(1000);
    sandbox.refreshExplorerEditOverlay(0);
    runFrames();
    results.grown = { gutter: stack.style.props['--explorer-source-gutter-width'] };
}

// 5. Teardown releases the queued frame: no paint lands after the editor is
//    gone, and the scroller's focus ring goes with it.
{
    const view = openEditor('a\\n');
    const { underlay } = wireStack();
    view.classes.add('editor-focused');
    sandbox.terminals[0]._explorerEdit.draft = 'a\\nb\\n';
    sandbox.refreshExplorerEditOverlay(0);
    sandbox.teardownExplorerEditOverlay(0);
    const queued = frames.size;
    runFrames();
    results.tornDown = {
        queued,
        paints: underlay.paints,
        focusRing: view.classes.has('editor-focused'),
        frame: sandbox.terminals[0]._explorerEditOverlayFrame
    };
}

process.stdout.write(JSON.stringify(results));
"""


class NodeHarnessMixin:
    def _run_node(self, harness: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(HIGHLIGHT_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "Node.js is required for explorer edit overlay tests")
class OverlayViabilityTestCase(NodeHarnessMixin, unittest.TestCase):
    def _viability(self, cases):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const cases = %s;
            process.stdout.write(JSON.stringify(
                cases.map(one => policy.overlayViability(one))
            ));
            """
            % json.dumps(cases)
        )

    def test_a_buffer_within_the_bound_gets_the_overlay(self):
        (result,) = self._viability(
            [{"contentLength": 4096, "maxContentLength": 2 * 1024 * 1024}]
        )
        self.assertTrue(result["enabled"])
        self.assertEqual(result["reason"], "")

    def test_the_bound_itself_is_still_viable(self):
        # An off-by-one here would drop the overlay for a file the read-only
        # view still renders normally.
        (result,) = self._viability(
            [{"contentLength": 2048, "maxContentLength": 2048}]
        )
        self.assertTrue(result["enabled"])

    def test_an_oversized_buffer_stands_the_overlay_down(self):
        # Re-rendering the whole document inside a frame budget on every
        # keystroke is what the performance guardrail forbids, so past the
        # bound editing silently keeps the bare textarea.
        (result,) = self._viability(
            [{"contentLength": 2049, "maxContentLength": 2048}]
        )
        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "oversized")

    def test_an_unmeasurable_buffer_stands_the_overlay_down(self):
        results = self._viability(
            [
                {"maxContentLength": 2048},
                {"contentLength": -1, "maxContentLength": 2048},
                {"contentLength": "many", "maxContentLength": 2048},
            ]
        )
        for result in results:
            self.assertFalse(result["enabled"])
            self.assertEqual(result["reason"], "unknown")

    def test_no_bound_never_refuses(self):
        # The caller supplies the bound; a missing one must not silently
        # disable a working overlay.
        results = self._viability(
            [{"contentLength": 10 ** 7}, {"contentLength": 10 ** 7, "maxContentLength": 0}]
        )
        for result in results:
            self.assertTrue(result["enabled"])


@unittest.skipUnless(NODE, "Node.js is required for explorer edit overlay tests")
class RefreshDecisionTestCase(NodeHarnessMixin, unittest.TestCase):
    def _decisions(self, cases):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const cases = %s;
            process.stdout.write(JSON.stringify(
                cases.map(one => policy.refreshDecision(one))
            ));
            """
            % json.dumps(cases)
        )

    def test_a_changed_draft_schedules_one_frame(self):
        self.assertEqual(
            self._decisions(
                [{"enabled": True, "pending": False, "draft": "b", "rendered": "a"}]
            ),
            ["schedule"],
        )

    def test_further_input_inside_a_queued_frame_is_coalesced(self):
        # The queued frame reads the draft when it runs, so a burst of typing
        # costs exactly one render, not one per keystroke.
        self.assertEqual(
            self._decisions(
                [{"enabled": True, "pending": True, "draft": "abc", "rendered": "a"}]
            ),
            ["coalesce"],
        )

    def test_an_unmoved_draft_is_not_re_rendered(self):
        self.assertEqual(
            self._decisions(
                [
                    {"enabled": True, "pending": False, "draft": "same", "rendered": "same"},
                    {"enabled": True, "pending": True, "draft": "same", "rendered": "same"},
                ]
            ),
            ["skip", "skip"],
        )

    def test_a_stood_down_overlay_never_renders(self):
        # Nothing behind the textarea to keep in step, whatever the draft does.
        self.assertEqual(
            self._decisions(
                [{"enabled": False, "pending": False, "draft": "b", "rendered": "a"}]
            ),
            ["off"],
        )

    def test_the_first_paint_of_an_empty_buffer_is_a_skip(self):
        # Mounting records the draft it rendered, so an empty file that is
        # still empty has nothing to repaint.
        self.assertEqual(
            self._decisions([{"enabled": True, "pending": False, "draft": "", "rendered": ""}]),
            ["skip"],
        )

    def test_a_never_rendered_underlay_schedules(self):
        self.assertEqual(
            self._decisions([{"enabled": True, "pending": False, "draft": "x"}]),
            ["schedule"],
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer edit overlay tests")
class EditOverlayAdapterTestCase(unittest.TestCase):
    """What the overlay actually installs, refreshes, and releases."""

    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(OVERLAY_HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(VIEWER_JS),
                    str(HIGHLIGHT_JS),
                    str(OVERLAY_JS),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(f"node harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_the_rows_are_installed_behind_the_textarea(self):
        mounted = self.results["mounted"]
        html = mounted["html"]
        # The stack holds both layers, and the textarea is untouched — the
        # editor's own lookups still find it by id.
        self.assertIn('data-explorer-edit-stack="0"', html)
        self.assertIn('id="explorer-edit-textarea-0"', html)
        # The rows come from the read-only renderer, so the gutter survives
        # entering edit mode instead of vanishing for the duration.
        for number in ("1", "2", "3"):
            self.assertIn(
                f'<span class="explorer-source-line-number">{number}</span>', html
            )
        self.assertIn("alpha", html)
        # One gutter width for the whole document, so both layers agree on
        # where the code column starts.
        self.assertIn("--explorer-source-gutter-width:", html)
        # The textarea stays the sole accessible surface.
        self.assertIn('aria-hidden="true"', html)
        # No fold buttons: a focusable control under a covering textarea is an
        # unclickable tab trap.
        self.assertNotIn("<button", html)
        self.assertEqual(mounted["rendered"], "alpha\nbeta\ngamma\n")
        # The focus ring moves to the scroller, which needs both edges.
        self.assertEqual(sorted(mounted["focusListeners"]), ["blur", "focus"])

    def test_an_oversized_buffer_keeps_the_bare_textarea(self):
        # Silent degradation: no stack, no notice, edit mode exactly as it was.
        html = self.results["oversized"]["html"]
        self.assertEqual(html, '<textarea id="explorer-edit-textarea-0"></textarea>')

    def test_a_burst_of_typing_costs_one_render(self):
        coalesced = self.results["coalesced"]
        # Nothing renders on the keystroke itself…
        self.assertEqual(coalesced["paintsBeforeFrame"], 0)
        # …and the one frame that follows shows the latest draft, not the
        # first. A second refresh with an unmoved draft adds nothing.
        self.assertEqual(coalesced["paints"], 1)
        self.assertTrue(coalesced["showsLastDraft"])
        self.assertEqual(
            coalesced["gutter"], "max(42px, calc(1ch + 19px))"
        )

    def test_a_new_digit_widens_the_gutter_for_the_whole_document(self):
        # Hazard 2: a four-digit row used to start its code column further
        # right than its neighbours, which under an overlay is a hard desync.
        self.assertEqual(
            self.results["grown"]["gutter"], "max(42px, calc(4ch + 19px))"
        )

    def test_teardown_releases_the_queued_frame_and_the_focus_ring(self):
        torn = self.results["tornDown"]
        self.assertEqual(torn["queued"], 0)
        self.assertEqual(torn["paints"], 0)
        self.assertFalse(torn["focusRing"])
        self.assertEqual(torn["frame"], 0)


if __name__ == "__main__":
    unittest.main()
