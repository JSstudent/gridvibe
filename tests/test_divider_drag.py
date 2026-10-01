"""Dragging a divider between panes, executed in Node.

A pane's minimum (a sixteenth of the grid's width and of its height, and a
character floor for a terminal) limits how far a divider can *shrink* it, one
dimension at a time. What is pinned:

- **The floor is per pane, not per grid.** A pane at or below its minimum
  blocks only the dividers that would make it smaller; every other divider,
  and the pane's own dividers moved to grow it, still move.
- **The floor is per axis.** A short pane can still be made narrower, and a
  narrow one shorter: only the dimension the move shrinks is checked.
- **A divider pushed past a minimum stops at it**, however far the pointer
  goes, rather than staying wherever the last accepted event left it.
- **The arithmetic** (`dragDividerWeights`, `clampDividerDelta`) and the pure
  rule (`policy.fits` with `previous`) are run directly.
"""

import json
import unittest

from tests.test_background_split import (
    BACKGROUND_RESIZE_JS,
    NODE,
    SPLIT_GEOMETRY_JS,
    TERMINALS_JS,
    _function_source,
    _run_node_file,
)
from tests.test_split_geometry import _js_const_source

PAGE_SOURCE_NAMES = (
    "isExplorerSession",
    "isExplorerPaneInstance",
    "sumTrackSpan",
    "getResizableGridMetrics",
    "getPaneCandidateSurface",
    "validateResizeCandidate",
    "updateGridResize",
)

PURE_HARNESS = r"""
const resize = require(RESIZE_PATH);
const geometry = require(GEOMETRY_PATH);
const limits = {
    columnTrackSpace: 1000, rowTrackSpace: 800, cell: { width: 8, height: 17 },
    headerHeight: 34, minCols: 8, minRows: 4, minAxisRatio: 1 / 16
};
const fits = (surface, previous) => resize.policy.fits({
    ...limits, surfaces: [surface], previous: previous ? [previous] : undefined, exempt: [false]
});
const tiny = { width: 40, height: 800 };
const out = {
    tinyAlone: fits(tiny),
    tinyUntouched: fits(tiny, tiny),
    tinyUntouchedWithinRounding: fits({ width: 40 - 1e-9, height: 800 }, tiny),
    tinyGrowing: fits({ width: 50, height: 800 }, tiny),
    tinyShrinking: fits({ width: 30, height: 800 }, tiny),
    largeShrinkingAboveFloor: fits({ width: 400, height: 800 }, { width: 500, height: 800 }),
    largeShrinkingBelowFloor: fits({ width: 50, height: 800 }, { width: 500, height: 800 }),
    /* 59 columns wide and short: its width is far above its floor, though
       its area is under a sixteenth of the grid's. */
    shortWideNarrowing: fits({ width: 300, height: 110 }, { width: 400, height: 110 }),
    shortWideShorter: fits({ width: 400, height: 40 }, { width: 400, height: 110 }),
    flatNarrowing: fits({ width: 300, height: 30 }, { width: 400, height: 30 }),
    columnFloorHolds: fits({ width: 65, height: 800 }, { width: 400, height: 800 }),
    columnFloorMet: fits({ width: 70, height: 800 }, { width: 400, height: 800 }),
    rowFloorHolds: fits({ width: 400, height: 100 }, { width: 400, height: 300 }),
};

const weights = [1, 1, 1, 1];
const sizes = [100, 100, 100, 100];
const groups = { before: [0, 1], after: [2, 3] };
out.dragged = geometry.dragDividerWeights(weights, sizes, groups, 50);
out.untouched = geometry.dragDividerWeights([1, 2, 3, 4], sizes, { before: [1], after: [2] }, 20);

/* A group thinner than a pixel, its track already at the weight floor: the
   arrangement a review reproduced a non-monotone clamp on. */
const thin = { weights: [1, 1, 0.01], sizes: [17, 17, 0.17], groups: { before: [0, 1], after: [2] } };
out.thinRange = geometry.dividerDragRange(thin.weights, thin.sizes, thin.groups);
out.thinAtZero = geometry.dragDividerWeights(thin.weights, thin.sizes, thin.groups, 0);
out.thinPastRange = geometry.dragDividerWeights(thin.weights, thin.sizes, thin.groups, 2000);
out.thinGrowing = geometry.dragDividerWeights(thin.weights, thin.sizes, thin.groups, -8.5);
const total = values => values.reduce((sum, value) => sum + value, 0);
out.totalKept = Math.abs(total(out.thinGrowing) - total(thin.weights)) < 1e-9
    && Math.abs(total(out.dragged) - total(weights)) < 1e-9;
out.heavyRange = geometry.dividerDragRange([80, 40], [800, 400], { before: [0], after: [1] });
out.unmeasured = geometry.dividerDragRange([1, 1], [0, 0], { before: [0], after: [1] });

const edgeAt = limit => delta => delta <= limit;
out.clampAccepted = geometry.clampDividerDelta(30, edgeAt(100));
out.clampEdge = geometry.clampDividerDelta(500, edgeAt(120.3));
out.clampNegativeEdge = geometry.clampDividerDelta(-500, delta => delta >= -75);
out.clampNothingAllowed = geometry.clampDividerDelta(80, delta => delta <= 0);
out.clampZero = geometry.clampDividerDelta(0, () => false);
out.clampNaN = geometry.clampDividerDelta(NaN, () => true);
console.log(JSON.stringify(out));
"""

PAGE_HARNESS = r"""
require(RESIZE_PATH);
require(GEOMETRY_PATH);

var gridBox = { width: 1216, height: 816 };
const grid = { children: [], getBoundingClientRect: () => gridBox };
window.getComputedStyle = () => ({
    paddingLeft: '8px', paddingRight: '8px', paddingTop: '8px', paddingBottom: '8px',
    columnGap: '8px', rowGap: '8px', gap: '8px'
});
const document = { getElementById: id => (id === 'terminalsGrid' ? grid : null) };
let painted = 0;
function applySplitSlotGeometry() { painted += 1; return true; }
function scheduleActiveGridResizeFits() {}

/* A tall pane B squeezed below its minimum between A and the stacked C/D. */
var splitSlotRects = [
    { x: 1, y: 1, w: 4, h: 8 },
    { x: 5, y: 1, w: 4, h: 8 },
    { x: 9, y: 1, w: 8, h: 4 },
    { x: 9, y: 5, w: 8, h: 4 }
];
const START_COLUMNS = [1, 1, 1, 1, 0.05, 0.05, 0.05, 0.05, 1, 1, 1, 1, 1, 1, 1, 1];
const START_ROWS = [1, 1, 1, 1, 1, 1, 1, 1];
var splitColumnWeights = START_COLUMNS.slice();
var splitRowWeights = START_ROWS.slice();
const cell = { width: 8, height: 17 };
var terminals = splitSlotRects.map(() => ({ term: { _core: { _renderService: { dimensions: { css: { cell } } } } } }));
grid.children = splitSlotRects.map((_rect, index) => ({
    dataset: { slot: String(index) },
    querySelector: () => ({ getBoundingClientRect: () => ({ height: 34 }) })
}));
var activeGridResize = null;

function sizesFor(weights, contentSize) {
    const space = contentSize - (weights.length - 1) * 8;
    const total = weights.reduce((sum, weight) => sum + weight, 0);
    return weights.map(weight => space * weight / total);
}

/* One drag from the start arrangement, through the page's own handler. */
function drag(axis, lineIndex, groups, pointerDelta) {
    splitColumnWeights = START_COLUMNS.slice();
    splitRowWeights = START_ROWS.slice();
    painted = 0;
    const handle = { style: { left: '400px', top: '300px' } };
    activeGridResize = {
        axis, lineIndex, pointerId: 1, startClientX: 400, startClientY: 300,
        startColumnWeights: START_COLUMNS.slice(), startRowWeights: START_ROWS.slice(),
        startColumnSizes: sizesFor(START_COLUMNS, 1200), startRowSizes: sizesFor(START_ROWS, 800),
        trackGroups: groups, affectedIndices: [], handle,
        startHandleOffset: axis === 'vertical' ? 400 : 300, appliedDelta: 0, fitFrame: null
    };
    const vertical = axis === 'vertical';
    updateGridResize({
        pointerId: 1,
        clientX: 400 + (vertical ? pointerDelta : 0),
        clientY: 300 + (vertical ? 0 : pointerDelta),
        preventDefault() {}, stopPropagation() {}
    });
    const applied = activeGridResize.appliedDelta;
    const weights = vertical ? START_COLUMNS : START_ROWS;
    const sizes = vertical ? activeGridResize.startColumnSizes : activeGridResize.startRowSizes;
    const beyond = applied + Math.sign(pointerDelta) * 1;
    return {
        applied,
        painted,
        handle: vertical ? handle.style.left : handle.style.top,
        beyondRefused: !validateResizeCandidate(
            axis, window.GridVibeSplitGeometry.dragDividerWeights(weights, sizes, groups, beyond), weights
        )
    };
}

const BC = { before: [4, 5, 6, 7], after: [8, 9, 10, 11, 12, 13, 14, 15] };
const AB = { before: [0, 1, 2, 3], after: [4, 5, 6, 7] };
const CD = { before: [0, 1, 2, 3], after: [4, 5, 6, 7] };
console.log(JSON.stringify({
    unrelatedDivider: drag('horizontal', 4, CD, 100),
    growRight: drag('vertical', 8, BC, 100),
    growLeft: drag('vertical', 4, AB, -60),
    shrinkRight: drag('vertical', 4, AB, 40),
    shrinkLeft: drag('vertical', 8, BC, -40),
    pushPastMinimum: drag('horizontal', 4, CD, -2000)
}));
"""


def _paths() -> str:
    return (
        f"const RESIZE_PATH = {json.dumps(str(BACKGROUND_RESIZE_JS))};\n"
        f"const GEOMETRY_PATH = {json.dumps(str(SPLIT_GEOMETRY_JS))};\n"
    )


@unittest.skipIf(NODE is None, "Node.js is required for the divider drag suite")
class DividerDragArithmeticTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = _run_node_file(_paths() + PURE_HARNESS)

    def test_a_pane_below_its_floor_passes_unless_it_is_made_smaller(self):
        out = self.out
        self.assertFalse(out["tinyAlone"])
        self.assertTrue(out["tinyUntouched"])
        self.assertTrue(out["tinyUntouchedWithinRounding"])
        self.assertTrue(out["tinyGrowing"])
        self.assertFalse(out["tinyShrinking"])

    def test_only_the_dimension_a_move_shrinks_is_held(self):
        self.assertTrue(self.out["shortWideNarrowing"])
        self.assertFalse(self.out["shortWideShorter"])
        # Below its height floor, a pane can still be made narrower.
        self.assertTrue(self.out["flatNarrowing"])

    def test_the_character_floor_is_per_axis_too(self):
        self.assertFalse(self.out["columnFloorHolds"])
        self.assertTrue(self.out["columnFloorMet"])
        self.assertFalse(self.out["rowFloorHolds"])

    def test_a_shrinking_pane_is_held_to_its_floor(self):
        self.assertTrue(self.out["largeShrinkingAboveFloor"])
        self.assertFalse(self.out["largeShrinkingBelowFloor"])

    def test_a_drag_rescales_only_the_two_groups_beside_the_line(self):
        self.assertEqual(self.out["dragged"], [1.25, 1.25, 0.75, 0.75])
        self.assertEqual(self.out["untouched"][0], 1)
        self.assertEqual(self.out["untouched"][3], 4)

    def test_a_drag_of_nothing_changes_nothing_even_below_a_pixel(self):
        self.assertEqual(self.out["thinAtZero"], [1, 1, 0.01])
        # The thin track is already at the weight floor: it cannot shrink, so
        # a pointer far past the range moves nothing.
        self.assertEqual(self.out["thinRange"]["max"], 0)
        self.assertAlmostEqual(self.out["thinRange"]["min"], -33.66)
        self.assertEqual(self.out["thinPastRange"], [1, 1, 0.01])

    def test_a_drag_keeps_the_axis_total_and_the_stored_weight_bounds(self):
        self.assertTrue(self.out["totalKept"])
        self.assertAlmostEqual(self.out["thinGrowing"][0], 0.75)
        # Either side stops where its track reaches the stored ceiling of 100.
        self.assertAlmostEqual(self.out["heavyRange"]["max"], 200)
        self.assertAlmostEqual(self.out["heavyRange"]["min"], -600)
        self.assertEqual(self.out["unmeasured"], {"min": 0, "max": 0})

    def test_a_refused_move_stops_at_the_edge_it_crossed(self):
        out = self.out
        self.assertEqual(out["clampAccepted"], 30)
        self.assertLessEqual(out["clampEdge"], 120.3)
        self.assertGreater(out["clampEdge"], 119.8)
        self.assertGreaterEqual(out["clampNegativeEdge"], -75)
        self.assertLess(out["clampNegativeEdge"], -74.5)
        self.assertEqual(out["clampNothingAllowed"], 0)
        self.assertEqual(out["clampZero"], 0)
        self.assertEqual(out["clampNaN"], 0)


@unittest.skipIf(NODE is None, "Node.js is required for the divider drag suite")
class DividerDragPageTestCase(unittest.TestCase):
    """The page's own `updateGridResize` and `validateResizeCandidate`, on a
    grid where one pane is already below its minimum."""

    @classmethod
    def setUpClass(cls):
        source = TERMINALS_JS.read_text(encoding="utf-8")
        lifted = "\n\n".join(
            [_js_const_source(source, "MIN_SPLIT_COLS", "MIN_SPLIT_ROWS", "MIN_RESIZE_AXIS_RATIO")]
            + [_function_source(source, name) for name in PAGE_SOURCE_NAMES]
        )
        cls.out = _run_node_file(
            _paths() + "var window = globalThis;\n" + lifted + "\n" + PAGE_HARNESS
        )

    def test_a_pane_below_its_minimum_does_not_freeze_other_dividers(self):
        case = self.out["unrelatedDivider"]
        self.assertEqual(case["applied"], 100)
        self.assertEqual(case["painted"], 1)
        self.assertEqual(case["handle"], "400px")

    def test_a_pane_below_its_minimum_can_still_be_made_larger(self):
        self.assertEqual(self.out["growRight"]["applied"], 100)
        self.assertEqual(self.out["growRight"]["handle"], "500px")
        self.assertEqual(self.out["growLeft"]["applied"], -60)
        self.assertEqual(self.out["growLeft"]["handle"], "340px")

    def test_only_the_borders_that_would_shrink_it_are_held(self):
        for name in ("shrinkRight", "shrinkLeft"):
            with self.subTest(case=name):
                case = self.out[name]
                self.assertEqual(case["applied"], 0)
                self.assertEqual(case["painted"], 0)
                self.assertTrue(case["beyondRefused"])

    def test_a_divider_pushed_past_a_minimum_stops_at_it(self):
        case = self.out["pushPastMinimum"]
        # The upper pane gives up most of its height before its floor holds.
        self.assertLess(case["applied"], -250)
        self.assertGreater(case["applied"], -400)
        self.assertTrue(case["beyondRefused"])
        self.assertEqual(case["painted"], 1)


REFRESH_HARNESS = r"""
var activeGridResize = null;
const frames = [];
var window = { requestAnimationFrame: callback => { frames.push(callback); return frames.length; } };
const calls = [];
function scheduleFit(index) { calls.push(['fit', index]); }
function updateSplitButtonState(index) { calls.push(['buttons', index]); }
function updatePaneHeaderLayout(index) { calls.push(['header', index]); }
/* A terminal with its resize observer, an explorer and a browser pane, and
   one pane beside no moved line. */
var terminals = [{ _resizeObserved: true }, { _paneType: 'explorer' }, { _paneType: 'browser' }, {}];
activeGridResize = { affectedIndices: [0, 1, 2], fitFrame: null };
scheduleActiveGridResizeFits();
scheduleActiveGridResizeFits();
const queued = frames.length;
frames.splice(0).forEach(callback => callback());
console.log(JSON.stringify({ queued, calls }));
"""


@unittest.skipIf(NODE is None, "Node.js is required for the divider drag suite")
class DividerDragPaneRefreshTestCase(unittest.TestCase):
    """While a divider moves, the panes beside it refresh their headers and
    split buttons once a frame; a terminal does so from its own observer."""

    @classmethod
    def setUpClass(cls):
        source = TERMINALS_JS.read_text(encoding="utf-8")
        cls.out = _run_node_file(
            _function_source(source, "scheduleActiveGridResizeFits") + "\n" + REFRESH_HARNESS
        )

    def test_unobserved_panes_beside_the_line_are_refreshed_once_a_frame(self):
        self.assertEqual(self.out["queued"], 1)
        self.assertEqual(
            self.out["calls"],
            [
                ["fit", 0],
                ["fit", 1], ["buttons", 1], ["header", 1],
                ["fit", 2], ["buttons", 2], ["header", 2],
            ],
        )


AFFECTED_HARNESS = r"""
/* T spans the left half above L and M; R and S are two columns on the right.
   Line 8 rescales tracks 1-8 (T's span) and 9-12 (R's): L moves with the
   group though it does not touch the line, and S keeps its width. */
var splitSlotRects = [
    { x: 1, y: 1, w: 8, h: 4 },
    { x: 1, y: 5, w: 4, h: 4 },
    { x: 5, y: 5, w: 4, h: 4 },
    { x: 9, y: 1, w: 4, h: 8 },
    { x: 13, y: 1, w: 4, h: 8 }
];
var terminals = splitSlotRects.map(() => ({}));
const grid = { children: splitSlotRects.map((_rect, index) => ({ dataset: { slot: String(10 + index) } })) };
const document = { getElementById: id => (id === 'terminalsGrid' ? grid : null) };
console.log(JSON.stringify({
    nested: affectedResizeIndices('vertical', 8),
    stacked: affectedResizeIndices('horizontal', 4)
}));
"""


@unittest.skipIf(NODE is None, "Node.js is required for the divider drag suite")
class DividerDragAffectedPanesTestCase(unittest.TestCase):
    """The panes a drag refreshes and redraws are the panes its track groups
    resize, including one further along a group than the line."""

    @classmethod
    def setUpClass(cls):
        source = TERMINALS_JS.read_text(encoding="utf-8")
        lifted = "\n\n".join(
            _function_source(source, name)
            for name in ("getResizeTrackGroups", "affectedResizeIndices")
        )
        cls.out = _run_node_file(lifted + "\n" + AFFECTED_HARNESS)

    def test_a_pane_resized_by_the_group_is_affected_though_off_the_line(self):
        self.assertEqual(self.out["nested"], [10, 11, 12, 13])

    def test_every_pane_holding_a_rescaled_row_is_affected(self):
        self.assertEqual(self.out["stacked"], [10, 11, 12, 13, 14])


if __name__ == "__main__":
    unittest.main()
