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
- **A layout is restored onto the grid this build draws.** A snapshot written
  when the base cell was two grid units wide reached the integer floor after one
  split, and a pane there could never be halved again however wide the window
  got. Its own box still names the unit it was written at, so the migration is a
  uniform multiplication — the same arrangement, addressed finely enough to
  halve. Every way the unit cannot be read off is left alone instead.
- **A refusal names the rule that refused.** The integer grid and the character
  floor are different problems with different answers, and the pane above was
  told the one it was nowhere near breaking.

The page's own half is executed too. `planSplitSlotGeometry` and `splitSlotRect`
are lifted whole out of `terminals.js` and run against a stub grid, and so are
the restore and the two split rules — `applyWorkspaceLayoutSnapshot` is handed a
real saved snapshot and the rectangles it publishes are what the split buttons
are then asked about. What is pinned there is the wiring — the plan is made against the live measurements, its
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
SHARED_JS = REPO_ROOT / "web" / "static" / "js" / "shared.js"
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

    def test_a_layout_from_a_coarser_grid_is_rescaled_to_this_builds_unit(self):
        """A six-pane base laid out at two grid units per cell, with one cell
        split once: the split pane's span of 1 is the floor of the integer
        grid, and rescaling it to this build's unit gives it room to halve
        again. The box a split never grows is what names the old unit."""
        result = self._run_node(
            """
            const rects = [
                { x: 1, y: 1, w: 2, h: 2 }, { x: 3, y: 1, w: 2, h: 2 },
                { x: 5, y: 1, w: 2, h: 2 }, { x: 1, y: 3, w: 2, h: 2 },
                { x: 3, y: 3, w: 2, h: 2 }, { x: 5, y: 3, w: 1, h: 2 },
                { x: 6, y: 3, w: 1, h: 2 }
            ];
            const plan = geometry.planSnapshotRescale({
                rects,
                columnWeights: [1, 1, 1, 1, 1, 1],
                rowWeights: [1, 1, 1, 1],
                cellShapes: [{ columns: 3, rows: 2 }],
                unit: 8,
                maxGridLine: 512
            });
            report({
                unit: plan.unit,
                factor: plan.factor,
                box: { columns: plan.columns, rows: plan.rows },
                split: plan.rects.slice(5),
                first: plan.rects[0],
                columns: plan.columnWeights.length,
                rows: plan.rowWeights.length
            });
            """
        )
        self.assertEqual(result["unit"], 2)
        self.assertEqual(result["factor"], 4)
        self.assertEqual(result["box"], {"columns": 24, "rows": 16})
        self.assertEqual(result["first"], {"x": 1, "y": 1, "w": 8, "h": 8})
        # The pane that was stuck at a span of 1: three halvings back.
        self.assertEqual(
            result["split"],
            [{"x": 17, "y": 9, "w": 4, "h": 8}, {"x": 21, "y": 9, "w": 4, "h": 8}],
        )
        self.assertEqual(result["columns"], 24)
        self.assertEqual(result["rows"], 16)

    def test_a_rescaled_layout_nobody_dragged_renders_pixel_for_pixel(self):
        """The finer grid carries four times as many gap lines, and a pane that
        spans them absorbs exactly the track space those gaps took away. On a
        grid nobody has dragged the two cancel to the pixel."""
        result = self._run_node(
            """
            const rects = [{ x: 1, y: 1, w: 5, h: 4 }, { x: 6, y: 1, w: 1, h: 4 }];
            const weights = [1, 1, 1, 1, 1, 1];
            const plan = geometry.planSnapshotRescale({
                rects,
                columnWeights: weights,
                rowWeights: [1, 1, 1, 1],
                cellShapes: [{ columns: 3, rows: 2 }],
                unit: 8,
                maxGridLine: 512
            });
            const before = paneSizes(
                rects.map(rect => ({ start: rect.x, span: rect.w })), weights, 1900, 8
            );
            const after = paneSizes(
                plan.rects.map(rect => ({ start: rect.x, span: rect.w })),
                plan.columnWeights, 1900, 8
            );
            report({ before, after });
            """
        )
        for before, after in zip(result["before"], result["after"]):
            self.assertAlmostEqual(before, after, places=6)

    def test_a_dragged_divider_keeps_its_share_of_the_axis_exactly(self):
        """Tracks are repeated rather than divided, so a dragged pane keeps its
        share of the axis total to the last decimal and no stored weight moves
        toward the floor a save clamps it to. The pixels the extra gap lines
        redistribute are what a share cannot express: bounded here, paid once,
        on the one restore that migrates the record."""
        result = self._run_node(
            """
            const rects = [{ x: 1, y: 1, w: 5, h: 4 }, { x: 6, y: 1, w: 1, h: 4 }];
            const weights = [1.5, 1.5, 1.5, 0.5, 0.5, 0.5];
            const plan = geometry.planSnapshotRescale({
                rects,
                columnWeights: weights,
                rowWeights: [1, 1, 1, 1],
                cellShapes: [{ columns: 3, rows: 2 }],
                unit: 8,
                maxGridLine: 512
            });
            const share = (rectList, axisWeights) => {
                const total = axisWeights.reduce((sum, weight) => sum + weight, 0);
                return rectList.map(rect => {
                    let held = 0;
                    for (let offset = 0; offset < rect.w; offset++) {
                        held += axisWeights[rect.x - 1 + offset];
                    }
                    return held / total;
                });
            };
            const before = paneSizes(
                rects.map(rect => ({ start: rect.x, span: rect.w })), weights, 1900, 8
            );
            const after = paneSizes(
                plan.rects.map(rect => ({ start: rect.x, span: rect.w })),
                plan.columnWeights, 1900, 8
            );
            report({
                shareBefore: share(rects, weights),
                shareAfter: share(plan.rects, plan.columnWeights),
                floor: Math.min(...plan.columnWeights),
                drift: before.map((size, index) => Math.abs(size - after[index]) / 1900)
            });
            """
        )
        for before, after in zip(result["shareBefore"], result["shareAfter"]):
            self.assertAlmostEqual(before, after, places=12)
        # Dividing by the factor would have walked this toward 0.01, where a
        # save clamps it and hands back a different layout.
        self.assertGreaterEqual(result["floor"], 0.5)
        for drift in result["drift"]:
            self.assertLess(drift, 0.02)

    def test_a_snapshot_already_at_this_unit_is_not_touched(self):
        """The migration is one-way and idempotent: a layout written at the
        current unit infers it, and a factor of one is nothing to do."""
        result = self._run_node(
            """
            const rects = [
                { x: 1, y: 1, w: 8, h: 16 }, { x: 9, y: 1, w: 8, h: 16 },
                { x: 17, y: 1, w: 8, h: 8 }, { x: 17, y: 9, w: 4, h: 8 },
                { x: 21, y: 9, w: 4, h: 8 }
            ];
            report({
                unit: geometry.inferSnapshotUnit(rects, [{ columns: 3, rows: 2 }]),
                plan: geometry.planSnapshotRescale({
                    rects, cellShapes: [{ columns: 3, rows: 2 }], unit: 8, maxGridLine: 512
                })
            });
            """
        )
        self.assertEqual(result["unit"], 8)
        self.assertIsNone(result["plan"])

    def test_a_record_the_unit_cannot_be_read_off_is_left_alone(self):
        """Every way the reading fails, and all of them answer the same way:
        change nothing. A wrong factor is a layout nobody saved."""
        result = self._run_node(
            """
            const shapes = [{ columns: 3, rows: 2 }];
            report({
                /* A box that divides by neither offered shape. */
                indivisible: geometry.inferSnapshotUnit(
                    [{ x: 1, y: 1, w: 5, h: 4 }], shapes
                ),
                /* Two shapes that both divide it, answering differently. */
                ambiguous: geometry.inferSnapshotUnit(
                    [{ x: 1, y: 1, w: 4, h: 4 }],
                    [{ columns: 2, rows: 2 }, { columns: 4, rows: 4 }]
                ),
                /* No shape offered at all. */
                unstated: geometry.inferSnapshotUnit([{ x: 1, y: 1, w: 6, h: 4 }], []),
                /* One unreadable rectangle, so the whole record is unread. */
                malformed: geometry.snapshotGridBox(
                    [{ x: 1, y: 1, w: 2, h: 2 }, { x: 3, y: 1, w: 0, h: 2 }]
                ),
                fractional: geometry.snapshotGridBox([{ x: 1, y: 1, w: 2.5, h: 2 }]),
                /* Scaling this would overshoot the ceiling a save enforces, and
                   the server drops a geometry record all-or-nothing. */
                ceiling: geometry.planSnapshotRescale({
                    rects: [{ x: 1, y: 1, w: 200, h: 4 }],
                    cellShapes: [{ columns: 100, rows: 2 }],
                    unit: 8,
                    maxGridLine: 512
                }),
                /* The same record with no ceiling stated does rescale, so the
                   case above is the bound talking and not the arithmetic. */
                unbounded: geometry.planSnapshotRescale({
                    rects: [{ x: 1, y: 1, w: 200, h: 4 }],
                    cellShapes: [{ columns: 100, rows: 2 }],
                    unit: 8
                }) !== null
            });
            """
        )
        self.assertEqual(result["indivisible"], 0)
        self.assertEqual(result["ambiguous"], 0)
        self.assertEqual(result["unstated"], 0)
        self.assertIsNone(result["malformed"])
        self.assertIsNone(result["fractional"])
        self.assertIsNone(result["ceiling"])
        self.assertTrue(result["unbounded"])



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

def _js_const_source(script: str, *names: str) -> str:
    """The page's own `const NAME = ...;` lines, so a case executes the real
    numbers rather than a copy of them that could drift from them."""
    lines = []
    for name in names:
        match = re.search(rf"^\s*(const {re.escape(name)} = .+;)$", script, re.M)
        if match is None:
            raise AssertionError(f"no const {name} in terminals.js")
        lines.append(match.group(1))
    return "\n".join(lines)


# The restore path and the two rules that gate a split, lifted whole. The base
# shapes come from the page's own layout tables, so a case that builds a layout
# and a case that reads one back are talking about the same grid.
RESTORE_SOURCE = "\n\n".join(
    [
        _js_function_source(SHARED_JS.read_text(encoding="utf-8"), "getGridMetrics"),
        _js_const_source(
            TERMINALS_JS.read_text(encoding="utf-8"),
            "MIN_SPLIT_COLS",
            "MIN_SPLIT_ROWS",
            "SPLIT_CELL_UNIT",
            "MAX_STORED_SPLIT_GRID_LINE",
            "SPLIT_BLOCKED_BY_WINDOW",
            "SPLIT_BLOCKED_BY_GRID",
            "SPLIT_BLOCKED_BY_SIZE",
        ),
    ]
    + [
        _js_function_source(TERMINALS_JS.read_text(encoding="utf-8"), name)
        for name in (
            "cloneSplitRect",
            "cloneSplitAncestors",
            "makeSplitRectId",
            "normalizeSplitRectMetadata",
            "makeSplitLeaf",
            "normalizeSplitTrackWeights",
            "getSplitGridSize",
            "cloneSplitSlotRects",
            "getBaseLayoutSlots",
            "baseLayoutCellShapes",
            "fixedLayoutSlotRects",
            "rescaleCoarseLayoutSnapshot",
            "applyWorkspaceLayoutSnapshot",
            "getSplitBlockers",
            "getSplitCandidates",
            "getSplitDisabledReason",
        )
    ]
)

# Everything the restore reaches for that is not geometry: the page globals it
# publishes into, the one measurement it takes off a live terminal, and the
# paint at the end of it. The measurement is a stub because a case here is about
# which rule answered, and the character rule is the one that needs a real
# terminal to answer at all.
RESTORE_STUBS = r"""
var splitSlotRects = null;
var splitColumnWeights = null;
var splitRowWeights = null;
var originalSplitSlotCount = 0;
var terminals = [];
/* `Math.min(16, MAX_SESSIONS)` on the page, off a template global. No case here
   comes near it; the cap has its own refusal and its own sentence. */
const MAX_SPLIT_TERMINALS = 16;

var window = globalThis;
window.innerWidth = 1600;
const document = { getElementById: () => null };

var paneCharacters = { cols: 120, rows: 40 };
function estimatePaneCharacters() {
    return paneCharacters;
}

var painted = 0;
function applySplitSlotGeometry() {
    painted += 1;
    return true;
}

function boxOf(rects) {
    return {
        columns: Math.max(...rects.map(rect => rect.x + rect.w - 1)),
        rows: Math.max(...rects.map(rect => rect.y + rect.h - 1))
    };
}

function plainRects(rects) {
    return rects.map(rect => ({ x: rect.x, y: rect.y, w: rect.w, h: rect.h }));
}

/* A six-pane base at the old two-units-per-cell resolution, its last cell split
   side by side once. Both halves of that cell are a single grid line wide,
   which is the floor: the layout renders, and neither can ever be halved. */
function coarseSnapshot() {
    return {
        split_slot_rects: [
            { originSlot: 0, x: 1, y: 1, w: 2, h: 2 },
            { originSlot: 1, x: 3, y: 1, w: 2, h: 2 },
            { originSlot: 2, x: 5, y: 1, w: 2, h: 2 },
            { originSlot: 3, x: 1, y: 3, w: 2, h: 2 },
            { originSlot: 4, x: 3, y: 3, w: 2, h: 2 },
            { originSlot: 5, x: 5, y: 3, w: 1, h: 2 },
            { originSlot: 5, x: 6, y: 3, w: 1, h: 2 }
        ],
        split_column_weights: [1, 1, 1, 1, 1, 1],
        split_row_weights: [1, 1, 1, 1],
        original_split_slot_count: 6
    };
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the split geometry tests")
class RestoredLayoutTestCase(unittest.TestCase):
    """A layout saved on a coarser grid, restored: the page's own
    `applyWorkspaceLayoutSnapshot` and the two rules that decide whether a pane
    can be split, executed against the rectangles it publishes."""

    def _run_node(self, body: str, *, load_module: bool = True):
        script = "\n".join(
            ([SPLIT_GEOMETRY_JS.read_text(encoding="utf-8")] if load_module else [])
            + [RESTORE_SOURCE, RESTORE_STUBS, body, ""]
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

    def test_a_layout_saved_before_the_base_cell_grew_is_made_finer_on_restore(self):
        """The whole restore, as the page runs it: the snapshot's own box names
        the unit it was written at, every rectangle is multiplied by the same
        factor, and each axis gets that many tracks back."""
        result = self._run_node(
            """
            const restored = applyWorkspaceLayoutSnapshot(coarseSnapshot(), 7);
            report({
                restored,
                painted,
                box: boxOf(splitSlotRects),
                rects: plainRects(splitSlotRects),
                columns: splitColumnWeights.length,
                rows: splitRowWeights.length,
                originSlots: splitSlotRects.map(rect => rect.originSlot),
                baseCount: originalSplitSlotCount
            });
            """
        )
        self.assertTrue(result["restored"])
        self.assertEqual(result["painted"], 1)
        self.assertEqual(result["box"], {"columns": 24, "rows": 16})
        self.assertEqual(result["rects"][0], {"x": 1, "y": 1, "w": 8, "h": 8})
        self.assertEqual(
            result["rects"][5:],
            [
                {"x": 17, "y": 9, "w": 4, "h": 8},
                {"x": 21, "y": 9, "w": 4, "h": 8},
            ],
        )
        self.assertEqual(result["columns"], 24)
        self.assertEqual(result["rows"], 16)
        # The migration moves coordinates and nothing else: every pane still
        # answers for the slot it was restored into.
        self.assertEqual(result["originSlots"], [0, 1, 2, 3, 4, 5, 5])
        self.assertEqual(result["baseCount"], 6)

    def test_the_pane_that_could_never_be_split_side_by_side_can_be_again(self):
        """The bug as the reader met it, then the same pane after the restore.
        The measurement is deliberately generous throughout, so nothing here is
        the character floor talking."""
        result = self._run_node(
            """
            terminals = Array.from({ length: 7 }, () => ({}));
            const stored = coarseSnapshot().split_slot_rects[5];
            const before = {
                blockers: getSplitBlockers(5, stored),
                candidates: getSplitCandidates(5, stored)
            };
            before.reason = getSplitDisabledReason('vertical', before.blockers.vertical);

            applyWorkspaceLayoutSnapshot(coarseSnapshot(), 7);
            const rect = splitSlotRects[5];
            report({
                before,
                after: {
                    blockers: getSplitBlockers(5, rect),
                    candidates: getSplitCandidates(5, rect),
                    width: rect.w
                }
            });
            """
        )
        self.assertEqual(result["before"]["blockers"]["vertical"], "grid")
        self.assertEqual(result["before"]["candidates"], ["horizontal"])
        # The sentence the reader used to get named the column floor, which the
        # pane was nowhere near; it names the rule that actually refused.
        self.assertIn("grid space", result["before"]["reason"])
        self.assertNotIn("columns", result["before"]["reason"])
        self.assertEqual(result["after"]["blockers"], {"vertical": "", "horizontal": ""})
        self.assertEqual(result["after"]["candidates"], ["vertical", "horizontal"])
        self.assertGreaterEqual(result["after"]["width"], 2)

    def test_a_pane_that_is_genuinely_too_small_still_names_the_character_floor(self):
        """The other rule, unchanged: a pane with all the grid space in the
        world but no room on screen is refused for its size, and says so."""
        result = self._run_node(
            """
            terminals = Array.from({ length: 2 }, () => ({}));
            paneCharacters = { cols: 4, rows: 2 };
            const rect = { x: 1, y: 1, w: 8, h: 8 };
            const blockers = getSplitBlockers(0, rect);
            report({
                blockers,
                candidates: getSplitCandidates(0, rect),
                vertical: getSplitDisabledReason('vertical', blockers.vertical),
                horizontal: getSplitDisabledReason('horizontal', blockers.horizontal)
            });
            """
        )
        self.assertEqual(result["blockers"], {"vertical": "size", "horizontal": "size"})
        self.assertEqual(result["candidates"], [])
        self.assertIn("8 columns", result["vertical"])
        self.assertIn("4 rows", result["horizontal"])

    def test_a_layout_already_at_this_resolution_is_restored_untouched(self):
        """Idempotence, through the page rather than the module: a snapshot
        written by this build comes back exactly as it was stored, so a restore
        is not a slow way of growing a layout."""
        result = self._run_node(
            """
            const snapshot = {
                split_slot_rects: [
                    { originSlot: 0, x: 1, y: 1, w: 8, h: 16 },
                    { originSlot: 1, x: 9, y: 1, w: 8, h: 16 },
                    { originSlot: 2, x: 17, y: 1, w: 8, h: 8 },
                    { originSlot: 3, x: 17, y: 9, w: 4, h: 8 },
                    { originSlot: 4, x: 21, y: 9, w: 4, h: 8 }
                ],
                split_column_weights: Array.from({ length: 24 }, (_v, i) => (i < 8 ? 1.4 : 0.9)),
                split_row_weights: Array.from({ length: 16 }, () => 1),
                original_split_slot_count: 6
            };
            applyWorkspaceLayoutSnapshot(snapshot, 5);
            report({
                rects: plainRects(splitSlotRects),
                stored: plainRects(snapshot.split_slot_rects),
                weights: splitColumnWeights,
                storedWeights: snapshot.split_column_weights
            });
            """
        )
        self.assertEqual(result["rects"], result["stored"])
        self.assertEqual(result["weights"], result["storedWeights"])

    def test_the_base_shapes_are_the_layouts_the_page_actually_builds(self):
        """The one thing the inference depends on: that the shapes a snapshot is
        read against are the shapes `fixedLayoutSlotRects()` lays out. Every
        count and every class the page can produce is built here and read back,
        so a layout this build writes is never mistaken for an older one."""
        result = self._run_node(
            """
            const planner = window.GridVibeSplitGeometry;
            const classesFor = count => {
                if (count === 1) return ['layout-single'];
                if (count === 2) return ['layout-2-vertical', 'layout-2-horizontal'];
                if (count === 3) {
                    return ['layout-3-vertical', 'layout-3-horizontal', 'layout-3-split'];
                }
                return ['layout-grid'];
            };
            const rows = [];
            for (let count = 1; count <= 16; count++) {
                classesFor(count).forEach(layoutClass => {
                    const rects = fixedLayoutSlotRects(count, layoutClass);
                    const box = boxOf(rects);
                    const shapes = baseLayoutCellShapes(count);
                    rows.push({
                        count,
                        layoutClass,
                        cells: {
                            columns: box.columns / SPLIT_CELL_UNIT,
                            rows: box.rows / SPLIT_CELL_UNIT
                        },
                        offered: shapes,
                        unit: planner.inferSnapshotUnit(rects, shapes),
                        rescale: planner.planSnapshotRescale({
                            rects,
                            cellShapes: shapes,
                            unit: SPLIT_CELL_UNIT,
                            maxGridLine: MAX_STORED_SPLIT_GRID_LINE
                        })
                    });
                });
            }
            report(rows);
            """
        )
        self.assertEqual(len(result), 19)
        for row in result:
            where = f"{row['count']} panes, {row['layoutClass']}"
            self.assertIn(row["cells"], row["offered"], where)
            self.assertEqual(row["unit"], 8, where)
            self.assertIsNone(row["rescale"], where)

    def test_a_page_that_reached_the_restore_without_the_module_keeps_its_record(self):
        """Load order again: the restore asks the planner for the migration and
        takes the coordinates it was given when there is nobody to ask. An old
        layout stays unsplittable, which is where it already was — it does not
        cost the reader the layout."""
        result = self._run_node(
            """
            const restored = applyWorkspaceLayoutSnapshot(coarseSnapshot(), 7);
            report({
                planner: typeof window.GridVibeSplitGeometry,
                restored,
                box: boxOf(splitSlotRects),
                rects: plainRects(splitSlotRects).slice(5)
            });
            """,
            load_module=False,
        )
        self.assertEqual(result["planner"], "undefined")
        self.assertTrue(result["restored"])
        self.assertEqual(result["box"], {"columns": 6, "rows": 4})
        self.assertEqual(
            result["rects"],
            [{"x": 5, "y": 3, "w": 1, "h": 2}, {"x": 6, "y": 3, "w": 1, "h": 2}],
        )

    def test_the_restore_declines_at_the_ceiling_the_server_enforces(self):
        """The client's own bound is the server's: `_normalize_workspace_layout`
        drops a geometry record all-or-nothing, so a rescale that overshot it
        would cost the whole layout on the next save."""
        from web.session_presentation import MAX_STORED_SESSION_PANES

        source = TERMINALS_JS.read_text(encoding="utf-8")
        ceiling = re.search(r"const MAX_STORED_SPLIT_GRID_LINE = (\d+);", source)
        self.assertIsNotNone(ceiling)
        self.assertEqual(int(ceiling.group(1)), MAX_STORED_SESSION_PANES * 8)


if __name__ == "__main__":
    unittest.main()
