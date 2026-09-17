"""A split is two halves, executed against a real track grid.

`split-geometry.js` answers the one question the split button and the sidecar's
`split_pane` both ask: where inside this pane's span does the cut go, and what
must the axis weights become for the two halves to render the same width. It is
DOM-free, so the arithmetic is run rather than read.

The grid it reasons about is the one the page paints: `n` weighted tracks with a
fixed gap between each pair, a pane being a run of tracks plus the gaps inside
it. Every case below rebuilds that grid from the weights the module hands back
and measures the panes, so what is pinned is rendered width, never a formula:

- **Half means half.** Both children of a split measure the same, including on
  an odd span, where the child holding fewer tracks also holds fewer gaps and is
  given that width back in track space.
- **A drag the pane inherited is not a divider.** A pane that absorbed a
  resized neighbour carries its lopsided weights; splitting it lands on the
  middle of its *width*, not on the line the old divider left behind.
- **No pane moves that was not asked about.** The rewrite preserves the span's
  own weight total exactly, so every other pane on the grid measures what it did
  before. Where that cannot be had — another pane's edge falls inside the span,
  so the only even cut would drag that pane's divider — the weights are left
  alone and the cut goes to the line nearest the middle.
- **Unmeasured is unchanged.** With no track sizes to reason about, the answer
  is the middle track line and untouched weights: exactly what the page did
  before this module existed.

The page's own half is executed too: `planSplitSlotGeometry` and `splitSlotRect`
are lifted whole out of `terminals.js` and run against a stub grid, so what is
pinned there is the wiring — the plan is made against the live measurements, its
weights are published as one generation of the axis, its offset is the one the
rectangles are spliced at, and a page that somehow reached the planner before it
loaded falls back instead of throwing with a card already appended.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SPLIT_GEOMETRY_JS = REPO_ROOT / "web" / "static" / "js" / "split-geometry.js"
TERMINALS_JS = REPO_ROOT / "web" / "static" / "js" / "terminals.js"
TERMINALS_HTML = REPO_ROOT / "templates" / "terminals.html"

NODE = shutil.which("node")

# The grid as `terminals.js` measures it (`getResizableGridMetrics` and
# `sumTrackSpan`), rebuilt here from the module's own output so a case asserts
# painted width rather than the weights that produced it.
HARNESS = r"""
function trackSizes(weights, contentSize, gap) {
    const trackSpace = contentSize - (weights.length - 1) * gap;
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    return weights.map(weight => trackSpace * (weight / total));
}

function paneSize(sizes, start, span, gap) {
    let total = 0;
    for (let offset = 0; offset < span; offset++) { total += sizes[start - 1 + offset]; }
    return total + (span - 1) * gap;
}

/* Every pane's rendered extent on one axis, keyed the way the caller lists
   them: what a case compares before and after a split. */
function paneSizes(rects, weights, contentSize, gap) {
    const sizes = trackSizes(weights, contentSize, gap);
    return rects.map(rect => paneSize(sizes, rect.start, rect.span, gap));
}

function otherIntervals(rects, index) {
    return rects.filter((_rect, position) => position !== index);
}

function planFor(rects, index, weights, contentSize, gap) {
    const rect = rects[index];
    return geometry.planSplit({
        start: rect.start,
        span: rect.span,
        weights,
        sizes: trackSizes(weights, contentSize, gap),
        gap,
        foreignEdges: geometry.foreignEdgeOffsets(
            otherIntervals(rects, index), rect.start, rect.span
        ),
    });
}

/* The two children a plan produces, as intervals on the same axis. */
function childRects(rect, firstSpan) {
    return [
        { start: rect.start, span: firstSpan },
        { start: rect.start + firstSpan, span: rect.span - firstSpan },
    ];
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the split geometry tests")
class SplitGeometryTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            SPLIT_GEOMETRY_JS.read_text(encoding="utf-8")
            + "\nconst geometry = module.exports;\n"
            + HARNESS
            + body
            + "\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_an_untouched_grid_splits_on_its_middle_line_and_rewrites_nothing(self):
        """Two panes side by side on a grid nobody has dragged: the answer is
        the one the page has always given, and no weight is republished."""
        result = self._run_node(
            """
            const weights = Array.from({ length: 16 }, () => 1);
            const rects = [{ start: 1, span: 8 }, { start: 9, span: 8 }];
            const plan = planFor(rects, 0, weights, 1200, 8);
            const children = childRects(rects[0], plan.firstSpan);
            const sizes = paneSizes(children, weights, 1200, 8);
            report({
                firstSpan: plan.firstSpan,
                rewrote: plan.weights !== null,
                even: plan.even,
                sizes
            });
            """
        )
        self.assertEqual(result["firstSpan"], 4)
        self.assertFalse(result["rewrote"])
        self.assertTrue(result["even"])
        self.assertAlmostEqual(result["sizes"][0], result["sizes"][1], places=6)

    def test_a_pane_that_absorbed_a_dragged_neighbour_splits_down_its_own_middle(self):
        """The shape a close leaves behind: one pane holding both sides of a
        divider the user had dragged to 65/35. Splitting it used to reproduce
        that divider — the two halves came out 65/35 — because the middle track
        line was no longer the middle of the pane."""
        result = self._run_node(
            """
            const weights = Array.from({ length: 16 }, (_value, index) => (
                index < 8 ? 1.2962974080584266 : 0.7037025919415735
            ));
            const rects = [{ start: 1, span: 16 }];
            const before = planFor(rects, 0, weights, 1200, 8);
            const legacyChildren = childRects(rects[0], 8);
            report({
                legacy: paneSizes(legacyChildren, weights, 1200, 8),
                firstSpan: before.firstSpan,
                even: before.even,
                halves: paneSizes(
                    childRects(rects[0], before.firstSpan), before.weights, 1200, 8
                ),
                spanWeight: before.weights.reduce((sum, weight) => sum + weight, 0)
            });
            """
        )
        legacy_first, legacy_second = result["legacy"]
        self.assertGreater(legacy_first / legacy_second, 1.5)
        self.assertEqual(result["firstSpan"], 8)
        self.assertTrue(result["even"])
        self.assertAlmostEqual(result["halves"][0], result["halves"][1], places=6)
        # The pane was the whole axis, so its total is the axis total: unchanged.
        self.assertAlmostEqual(result["spanWeight"], 16.0, places=6)

    def test_an_odd_span_is_halved_by_paying_the_missing_gap_back_in_track_width(self):
        """Six-column grids saved before the base cell had halving room are
        still restored, and a three-track pane has no middle line at all: one
        child gets one track, the other two, and the two-track child also
        carries the gap between them."""
        result = self._run_node(
            """
            const weights = Array.from({ length: 6 }, () => 1);
            const rects = [{ start: 1, span: 3 }, { start: 4, span: 3 }];
            const plan = planFor(rects, 0, weights, 1200, 8);
            report({
                legacy: paneSizes(childRects(rects[0], 1), weights, 1200, 8),
                firstSpan: plan.firstSpan,
                even: plan.even,
                halves: paneSizes(childRects(rects[0], plan.firstSpan), plan.weights, 1200, 8),
                neighbourBefore: paneSizes([rects[1]], weights, 1200, 8)[0],
                neighbourAfter: paneSizes([rects[1]], plan.weights, 1200, 8)[0]
            });
            """
        )
        legacy_first, legacy_second = result["legacy"]
        self.assertGreater(legacy_second / legacy_first, 1.8)
        self.assertTrue(result["even"])
        self.assertAlmostEqual(result["halves"][0], result["halves"][1], places=6)
        self.assertAlmostEqual(
            result["neighbourBefore"], result["neighbourAfter"], places=6
        )

    def test_a_divider_inside_the_span_is_left_where_the_user_put_it(self):
        """A full-height pane beside a stacked pair shares that pair's divider.
        Making its halves exactly equal would drag the divider, so the cut goes
        to the nearest line instead and the pair stays as it was dragged."""
        result = self._run_node(
            """
            const rowWeights = Array.from({ length: 16 }, (_value, index) => (
                index < 8 ? 1.0182817936172266 : 0.9817182063827733
            ));
            /* Rows: the tall pane spans all 16, the stacked pair splits at 8. */
            const rects = [
                { start: 1, span: 16 },
                { start: 1, span: 8 },
                { start: 9, span: 8 }
            ];
            const plan = planFor(rects, 0, rowWeights, 800, 8);
            const halves = paneSizes(childRects(rects[0], plan.firstSpan), rowWeights, 800, 8);
            report({
                edges: geometry.foreignEdgeOffsets(otherIntervals(rects, 0), 1, 16),
                firstSpan: plan.firstSpan,
                rewrote: plan.weights !== null,
                even: plan.even,
                skew: Math.abs(halves[0] - halves[1]) / (halves[0] + halves[1]),
                pair: paneSizes([rects[1], rects[2]], rowWeights, 800, 8)
            });
            """
        )
        self.assertEqual(result["edges"], [8])
        self.assertEqual(result["firstSpan"], 8)
        self.assertFalse(result["rewrote"])
        self.assertFalse(result["even"])
        # The cut lands on the pair's own divider, which is where the user put
        # it: off the middle by the amount they dragged it and no more.
        self.assertLess(result["skew"], 0.02)
        self.assertNotAlmostEqual(result["pair"][0], result["pair"][1], places=3)

    def test_the_cut_is_never_further_from_the_middle_than_the_middle_track_line(self):
        """The guarantee that makes the foreign-edge case safe to ship: the
        middle line is one of the offsets considered, so a grid where the
        weights cannot be rewritten still never splits worse than it did."""
        result = self._run_node(
            """
            /* A pane spanning a badly lopsided run of tracks, with a neighbour
               pinned to a line one track off its middle. */
            const weights = [3, 3, 3, 3, 0.4, 0.4, 0.4, 0.4];
            const rects = [{ start: 1, span: 8 }, { start: 5, span: 2 }];
            const plan = planFor(rects, 0, weights, 1000, 6);
            const chosen = paneSizes(childRects(rects[0], plan.firstSpan), weights, 1000, 6);
            const middle = paneSizes(childRects(rects[0], 4), weights, 1000, 6);
            report({
                firstSpan: plan.firstSpan,
                rewrote: plan.weights !== null,
                chosenSkew: Math.abs(chosen[0] - chosen[1]),
                middleSkew: Math.abs(middle[0] - middle[1])
            });
            """
        )
        self.assertFalse(result["rewrote"])
        self.assertLessEqual(result["chosenSkew"], result["middleSkew"] + 1e-9)

    def test_a_split_changes_no_pane_it_was_not_asked_about(self):
        """The whole grid, measured before and after: only the pane that was
        split is allowed to have moved."""
        result = self._run_node(
            """
            /* Three panes across a 24-track axis, the left one holding the sum
               of two dragged spans (a close), the right two untouched. */
            const weights = Array.from({ length: 24 }, (_value, index) => {
                if (index < 8) { return 1.4; }
                if (index < 16) { return 0.6; }
                return 1.0;
            });
            const rects = [
                { start: 1, span: 16 },
                { start: 17, span: 4 },
                { start: 21, span: 4 }
            ];
            const before = paneSizes(rects, weights, 1600, 8);
            const plan = planFor(rects, 0, weights, 1600, 8);
            const after = paneSizes(rects, plan.weights, 1600, 8);
            report({
                even: plan.even,
                halves: paneSizes(childRects(rects[0], plan.firstSpan), plan.weights, 1600, 8),
                splitPaneUnchanged: Math.abs(after[0] - before[0]) < 1e-6,
                othersMoved: [1, 2].map(index => Math.abs(after[index] - before[index]))
            });
            """
        )
        self.assertTrue(result["even"])
        self.assertAlmostEqual(result["halves"][0], result["halves"][1], places=6)
        # The split pane keeps its own outline; only its interior is rebalanced.
        self.assertTrue(result["splitPaneUnchanged"])
        for moved in result["othersMoved"]:
            self.assertLess(moved, 1e-6)

    def test_an_edge_on_the_span_boundary_is_not_a_divider_inside_it(self):
        """Panes beside the span, and panes containing it, constrain nothing —
        only an edge strictly inside is a divider this split would have to
        move."""
        result = self._run_node(
            """
            const intervals = [
                { start: 1, span: 4 },    /* ends exactly where the span starts */
                { start: 12, span: 6 },   /* starts exactly where it ends */
                { start: 1, span: 20 },   /* contains it */
                { start: 6, span: 3 }     /* two edges inside it */
            ];
            report({
                edges: geometry.foreignEdgeOffsets(intervals, 5, 7),
                none: geometry.foreignEdgeOffsets(intervals.slice(0, 3), 5, 7)
            });
            """
        )
        self.assertEqual(result["edges"], [1, 4])
        self.assertEqual(result["none"], [])

    def test_without_measurements_the_answer_is_the_middle_track_line(self):
        """A grid that has not been painted has no track sizes, and uniform
        tracks: the plan degrades to exactly what the page did before."""
        result = self._run_node(
            """
            const plans = [
                geometry.planSplit({ start: 1, span: 8, weights: null, sizes: null, gap: 8 }),
                geometry.planSplit({
                    start: 1, span: 9, weights: [1, 1], sizes: null, gap: 8
                }),
                geometry.planSplit({
                    start: 1, span: 2, weights: [1, 1], sizes: [0, 0], gap: 8
                })
            ];
            report(plans.map(plan => ({ firstSpan: plan.firstSpan, rewrote: plan.weights !== null })));
            """
        )
        self.assertEqual(
            result,
            [
                {"firstSpan": 4, "rewrote": False},
                {"firstSpan": 4, "rewrote": False},
                {"firstSpan": 1, "rewrote": False},
            ],
        )

    def test_a_rewrite_that_a_stored_layout_could_not_hold_is_declined(self):
        """Weights are clamped to `[0.01, 100]` on the way to disk, so a rewrite
        outside them would come back as a different layout. The uneven split is
        the lesser wrong: it survives the save."""
        result = self._run_node(
            """
            /* An axis whose stored weights already sit at the ceiling: the
               even rewrite wants one above it, and a clamped weight would come
               back off disk as a different layout. */
            const weights = [100, 100, 100];
            const plan = planFor([{ start: 1, span: 3 }], 0, weights, 1200, 8);
            report({ rewrote: plan.weights !== null, even: plan.even, firstSpan: plan.firstSpan });
            """
        )
        self.assertFalse(result["rewrote"])
        self.assertFalse(result["even"])
        self.assertGreaterEqual(result["firstSpan"], 1)

    def test_the_workspace_page_loads_the_module_before_terminals(self):
        """Wiring, not behaviour: `terminals.js` reads the planner off the
        window, so the tag has to be there and has to come first."""
        markup = TERMINALS_HTML.read_text(encoding="utf-8")
        scripts = re.findall(r"filename='js/([a-z0-9\-]+\.js)'", markup)
        self.assertIn("split-geometry.js", scripts)
        self.assertLess(
            scripts.index("split-geometry.js"), scripts.index("terminals.js")
        )



def _js_function_source(script: str, name: str) -> str:
    """Return one top-level JS function's source, brace-matched."""
    start = script.index(f"function {name}(")
    depth = 0
    for index in range(script.index("{", script.index(")", start)), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


# The page's own half, loaded whole: the rectangle factories, the weight
# normalizer, the grid measurement, and the two functions a split actually calls.
PAGE_SOURCE = "\n\n".join(
    _js_function_source(TERMINALS_JS.read_text(encoding="utf-8"), name)
    for name in (
        "cloneSplitRect",
        "cloneSplitAncestors",
        "makeSplitRectId",
        "normalizeSplitRectMetadata",
        "makeSplitLeaf",
        "normalizeSplitTrackWeights",
        "initializeSplitTrackWeights",
        "getSplitGridSize",
        "getResizableGridMetrics",
        "splitSlotSpan",
        "planSplitSlotGeometry",
        "splitSlotRect",
    )
)

# The page is a stub; the grid it measures is not. `getResizableGridMetrics`
# reads a real box and real computed padding and gaps off it, exactly as it does
# in the window, so the plan is made against numbers of the shape it really sees.
PAGE_STUBS = r"""
var splitColumnWeights = null;
var splitRowWeights = null;

const gridStub = {
    className: 'layout-split-local',
    style: {},
    getBoundingClientRect: () => ({ top: 0, left: 0, width: 1216, height: 816 })
};
const document = { getElementById: id => (id === 'terminalsGrid' ? gridStub : null) };
var window = globalThis;
window.getComputedStyle = () => ({
    paddingLeft: '8px',
    paddingRight: '8px',
    paddingTop: '8px',
    paddingBottom: '8px',
    columnGap: '8px',
    rowGap: '8px',
    gap: '8px'
});

/* The rendered width of one rectangle, measured the way the page's own resize
   maths does: the tracks it spans plus the gaps inside it. */
function renderedWidth(rect) {
    const metrics = getResizableGridMetrics(gridStub, splitColumnWeights, splitRowWeights);
    let total = 0;
    for (let offset = 0; offset < rect.w; offset++) {
        total += metrics.columnSizes[rect.x - 1 + offset];
    }
    return total + (rect.w - 1) * metrics.columnGap;
}

function renderedHeight(rect) {
    const metrics = getResizableGridMetrics(gridStub, splitColumnWeights, splitRowWeights);
    let total = 0;
    for (let offset = 0; offset < rect.h; offset++) {
        total += metrics.rowSizes[rect.y - 1 + offset];
    }
    return total + (rect.h - 1) * metrics.rowGap;
}

/* One split, as `splitTerminalPane` performs it: plan against the live
   rectangles, publish the weights the plan returns, splice in the two
   children. */
function performSplit(rects, visualIndex, axis) {
    const plan = planSplitSlotGeometry(rects, visualIndex, rects[visualIndex], axis);
    const children = splitSlotRect(rects[visualIndex], axis, plan.firstSpan);
    if (Array.isArray(plan.weights)) {
        if (axis === 'vertical') { splitColumnWeights = plan.weights; }
        else { splitRowWeights = plan.weights; }
    }
    rects.splice(visualIndex, 1, children[0], children[1]);
    return { plan, children };
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the split geometry tests")
class PageSplitTestCase(unittest.TestCase):
    """The page's own `planSplitSlotGeometry` and `splitSlotRect`, lifted out of
    `terminals.js` and run against a stub grid: the wiring, not the rule."""

    def _run_node(self, body: str, *, load_module: bool = True):
        script = "\n".join(
            ([SPLIT_GEOMETRY_JS.read_text(encoding="utf-8")] if load_module else [])
            + [PAGE_SOURCE, PAGE_STUBS, body, ""]
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_page_publishes_the_weights_the_plan_asks_for(self):
        """A pane holding both sides of a divider its closed neighbour left
        behind: the page splits it down the middle of its width, and the two
        cards it lays out measure the same."""
        result = self._run_node(
            """
            splitColumnWeights = Array.from({ length: 16 }, (_value, index) => (
                index < 8 ? 1.4 : 0.6
            ));
            const rects = [makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 16, h: 8 })];
            const outcome = performSplit(rects, 0, 'vertical');
            report({
                firstSpan: outcome.plan.firstSpan,
                published: splitColumnWeights.slice(0, 16),
                spans: outcome.children.map(child => ({ x: child.x, w: child.w })),
                widths: outcome.children.map(renderedWidth),
                rectCount: rects.length
            });
            """
        )
        self.assertEqual(result["firstSpan"], 8)
        self.assertEqual(result["spans"], [{"x": 1, "w": 8}, {"x": 9, "w": 8}])
        self.assertEqual(result["rectCount"], 2)
        self.assertAlmostEqual(result["widths"][0], result["widths"][1], places=6)
        # Published, not patched: one whole generation of the axis.
        self.assertEqual(len(result["published"]), 16)
        self.assertAlmostEqual(sum(result["published"]), 16.0, places=6)

    def test_a_stacked_split_reads_the_row_axis(self):
        """The same wiring on the other axis, where the pane's own rows are what
        the gaps come out of."""
        result = self._run_node(
            """
            splitRowWeights = Array.from({ length: 8 }, (_value, index) => (
                index < 4 ? 1.6 : 0.4
            ));
            const rects = [makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 16, h: 8 })];
            const outcome = performSplit(rects, 0, 'horizontal');
            report({
                spans: outcome.children.map(child => ({ y: child.y, h: child.h })),
                heights: outcome.children.map(renderedHeight),
                columnsUntouched: splitColumnWeights.every(weight => weight === 1)
            });
            """
        )
        self.assertEqual(result["spans"], [{"y": 1, "h": 4}, {"y": 5, "h": 4}])
        self.assertAlmostEqual(result["heights"][0], result["heights"][1], places=6)
        # Splitting on one axis initializes the other but never re-weights it.
        self.assertTrue(result["columnsUntouched"])

    def test_an_odd_span_lands_where_the_plan_says_and_still_measures_even(self):
        """A three-track pane out of a layout saved before the base cell had
        halving room: one child gets one track and the other two, and they come
        out the same width without moving the pane beside them."""
        result = self._run_node(
            """
            splitColumnWeights = Array.from({ length: 6 }, () => 1);
            splitRowWeights = Array.from({ length: 2 }, () => 1);
            const rects = [
                makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 3, h: 2 }),
                makeSplitLeaf({ originSlot: 1, x: 4, y: 1, w: 3, h: 2 })
            ];
            const neighbourBefore = renderedWidth(rects[1]);
            const outcome = performSplit(rects, 0, 'vertical');
            report({
                spans: outcome.children.map(child => ({ x: child.x, w: child.w })),
                widths: outcome.children.map(renderedWidth),
                neighbourBefore,
                neighbourAfter: renderedWidth(rects[2])
            });
            """
        )
        self.assertEqual(result["spans"], [{"x": 1, "w": 1}, {"x": 2, "w": 2}])
        self.assertAlmostEqual(result["widths"][0], result["widths"][1], places=6)
        self.assertAlmostEqual(
            result["neighbourBefore"], result["neighbourAfter"], places=6
        )

    def test_without_the_module_the_page_splits_the_way_it_always_did(self):
        """Load order is the one thing the markup assertion cannot prove is
        harmless: a page reaching for the planner before it existed would throw
        mid-split, with a card already appended. It falls back instead."""
        result = self._run_node(
            """
            splitColumnWeights = Array.from({ length: 16 }, (_value, index) => (
                index < 8 ? 1.4 : 0.6
            ));
            const rects = [makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 16, h: 8 })];
            const outcome = performSplit(rects, 0, 'vertical');
            report({
                planner: typeof window.GridVibeSplitGeometry,
                firstSpan: outcome.plan.firstSpan,
                rewrote: outcome.plan.weights !== null,
                spans: outcome.children.map(child => ({ x: child.x, w: child.w })),
                weightsUntouched: splitColumnWeights[0] === 1.4
            });
            """,
            load_module=False,
        )
        self.assertEqual(result["planner"], "undefined")
        self.assertEqual(result["firstSpan"], 8)
        self.assertFalse(result["rewrote"])
        self.assertEqual(result["spans"], [{"x": 1, "w": 8}, {"x": 9, "w": 8}])
        self.assertTrue(result["weightsUntouched"])


if __name__ == "__main__":
    unittest.main()
