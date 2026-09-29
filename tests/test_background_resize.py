"""A divider moves in a session tab the window is not showing, unseen.

Like a split from behind, the resize is an edit to the tab's *model*, measured
against the window's one shared grid and written through the group's
revisioned presentation transaction -- no tab switch, no focus change, no
repaint of the grid on screen. What is pinned here is executed in Node:

- **The sequence.** Every refusal comes before anything is written, in the
  visible resize's own words; a tab opened meanwhile goes to the visible
  handler; the tab is held for the write and released on every way out; once
  the write has started a lost answer is `unknown`.
- **The minimum is the live one.** The model-based check is compared against
  the page's own `validateResizeCandidate`, case for case.
- **The bridge routes** a tab held in the background to this sequence and the
  painted tab to the handler that owns its grid.
"""

import json
import unittest

from tests.test_background_split import (
    BACKGROUND_RESIZE_JS,
    BACKGROUND_TAB_JS,
    NODE,
    SPLIT_GEOMETRY_JS,
    TERMINALS_JS,
    _between,
    _function_source,
    _run_node_file,
)
from tests.test_split_geometry import _js_const_source

TRACK_SOURCE_NAMES = ("getSharedGridEdgeSegments", "getResizeTrackGroups")

MODULE_HARNESS = r"""
const resize = require(RESIZE_PATH);
const backgroundTab = require(TAB_PATH);
const geometry = require(GEOMETRY_PATH);
var splitSlotRects = null;

const RECTS = () => [{ x: 1, y: 1, w: 8, h: 8 }, { x: 9, y: 1, w: 8, h: 8 }];
const ones = count => Array.from({ length: count }, () => 1);

function baseModel() {
    return {
        groupId: 'g-2', ids: ['pane-a', 'pane-b'], rects: RECTS(),
        columnWeights: ones(16), rowWeights: ones(8), baseCount: 2
    };
}

function baseLive() {
    return {
        ok: true, presentation_revision: 5,
        panes: [{ session_id: 'pane-a' }, { session_id: 'pane-b' }],
        geometry: { split_slot_rects: RECTS(), column_weights: ones(16), row_weights: ones(8) }
    };
}

/* The shared grid: 1216 x 816 content, no gaps. */
function measureFor(model, options) {
    if (options.unmeasurable) return null;
    const width = 1216;
    const height = 816;
    const scale = (weights, extent) => {
        const total = weights.reduce((sum, weight) => sum + weight, 0);
        return weights.map(weight => extent * weight / total);
    };
    const columnSizes = scale(model.columnWeights, width);
    const rowSizes = scale(model.rowWeights, height);
    const span = (sizes, start, count) => sizes.slice(start, start + count).reduce((a, b) => a + b, 0);
    return {
        narrow: Boolean(options.narrow),
        metrics: {
            gridContentWidth: width, gridContentHeight: height,
            columnGap: 0, rowGap: 0, columnSizes, rowSizes,
            columnTrackSpace: width, rowTrackSpace: height
        },
        surfaces: model.rects.map(rect => ({
            width: span(columnSizes, rect.x - 1, rect.w),
            height: span(rowSizes, rect.y - 1, rect.h)
        })),
        cell: { width: 8, height: 17 },
        headerHeight: 34
    };
}

function fakePage(options = {}) {
    const log = [];
    const saves = [];
    const adopted = [];
    const state = { epoch: 0, shownCalls: 0 };
    let tab = null;
    const page = {
        holds: () => options.holds !== false,
        isShown: () => {
            state.shownCalls += 1;
            log.push('isShown');
            return options.shownFrom !== undefined && state.shownCalls >= options.shownFrom;
        },
        settle: async () => { log.push('settle'); },
        readModel: async () => {
            log.push('readModel');
            if (options.readThrows) throw new Error('list failed');
            return options.model === undefined ? baseModel() : options.model;
        },
        measure: model => { log.push('measure'); return measureFor(model, options); },
        readLayout: async () => {
            log.push('readLayout');
            if (options.closeDuringRead) state.epoch += 1;
            return options.live || baseLive();
        },
        readExemptions: async (_groupId, ids) => { log.push('readExemptions'); return ids.map(() => false); },
        trackGroups: (rects, axis, lineIndex) => getResizeTrackGroups(axis, lineIndex, rects),
        sharedEdges: getSharedGridEdgeSegments,
        planResize: geometry.planDividerResize,
        groupRecord: groupId => ({ group_id: groupId, name: 'Background' }),
        closeEpoch: () => state.epoch,
        closePending: () => Boolean(options.closePending),
        discard: groupId => log.push(`discard:${groupId}`),
        saveLayout: async args => {
            log.push('saveLayout');
            /* A load of the tab picked while the write is out waits for it. */
            let loaded = false;
            tab.settled('g-2').then(() => { loaded = true; log.push('load'); });
            await new Promise(resolve => setTimeout(resolve, 5));
            saves.push({ ...args, heldDuringWrite: !loaded });
            if (options.closeDuringWrite) state.epoch += 1;
            if (options.saveThrows) throw new Error('connection lost');
            return options.saveResult || { ok: true, revision: 6 };
        },
        adopt: (group, saved) => { log.push('adopt'); adopted.push({ group, saved }); },
        performShown: async intent => { log.push('performShown'); return { ok: true, viaVisible: true, intent }; },
        limits: { minCols: 8, minRows: 4, minSurfaceRatio: 1 / 16 },
        onError: error => log.push(`error:${error.message}`)
    };
    tab = backgroundTab.create(page);
    const api = resize.create({ ...page, tab });
    return { api, tab, log, saves, adopted };
}

async function freeSoon(tab, groupId) {
    return Promise.race([
        tab.settled(groupId).then(() => true),
        new Promise(resolve => setTimeout(() => resolve(false), 30))
    ]);
}

const INTENT = (overrides = {}) => ({
    group_id: 'g-2', axis: 'vertical', line_index: 8, position: 0.6, expected_revision: 5, ...overrides
});

async function run(options = {}, intent = INTENT()) {
    const t = fakePage(options);
    const answer = await t.api.perform(intent);
    await new Promise(resolve => setTimeout(resolve, 5));
    return {
        answer, log: t.log, saves: t.saves, adopted: t.adopted,
        released: await freeSoon(t.tab, 'g-2')
    };
}

const out = {};
(async () => {
    // The pointer drag reads the painted grid's rectangles; a tab from behind
    // hands its model's. Same answer for the same rectangles.
    splitSlotRects = RECTS();
    out.tracks = {
        painted: getResizeTrackGroups('vertical', 8),
        model: getResizeTrackGroups('vertical', 8, RECTS()),
        noLine: getResizeTrackGroups('vertical', 4, RECTS())
    };
    splitSlotRects = null;
    out.happy = await run();
    out.stacked = await run({
        model: { ...baseModel(), rects: [{ x: 1, y: 1, w: 16, h: 4 }, { x: 1, y: 5, w: 16, h: 4 }] },
        live: { ...baseLive(), geometry: {
            split_slot_rects: [{ x: 1, y: 1, w: 16, h: 4 }, { x: 1, y: 5, w: 16, h: 4 }],
            column_weights: ones(16), row_weights: ones(8)
        } }
    }, INTENT({ axis: 'horizontal', line_index: 4, position: 0.4 }));
    out.shownAtStart = await run({ shownFrom: 1 });
    out.openedWhileMeasuring = await run({ shownFrom: 2 });
    out.notHeld = await run({ holds: false });
    out.closePending = await run({ closePending: true });
    out.closeDuringRead = await run({ closeDuringRead: true });
    out.stale = await run({ live: { ...baseLive(), presentation_revision: 6 } });
    out.differs = await run({ live: { ...baseLive(), geometry: { ...baseLive().geometry, column_weights: [2, ...ones(15)] } } });
    out.reordered = await run({ live: { ...baseLive(), panes: [{ session_id: 'pane-b' }, { session_id: 'pane-a' }] } });
    out.gone = await run({ model: null });
    out.serverGone = await run({ live: { ok: false, error: 'Session group not found' } });
    out.outsideLine = await run({}, INTENT({ line_index: 16 }));
    out.outsidePosition = await run({}, INTENT({ position: 1 }));
    out.noEdge = await run({}, INTENT({ line_index: 4 }));
    out.narrow = await run({ narrow: true });
    out.unmeasurable = await run({ unmeasurable: true });
    out.noSpace = await run({}, INTENT({ position: 0.00001 }));
    out.minimum = await run({}, INTENT({ position: 0.97 }));
    out.readThrew = await run({ readThrows: true });
    out.saveRefused = await run({ saveResult: { ok: false, error: 'Presentation revision is stale' } });
    out.saveThrew = await run({ saveThrows: true });
    out.saveUnknown = await run({ saveResult: { ok: false, unknown: true, error: 'no revision' } });
    out.saveOldRevision = await run({ saveResult: { ok: true, revision: 5 } });
    out.closeDuringWrite = await run({ closeDuringWrite: true });
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""

PARITY_SOURCE_NAMES = (
    "isExplorerSession",
    "isExplorerPaneInstance",
    "sumTrackSpan",
    "getResizableGridMetrics",
    "getPaneCandidateSurface",
    "validateResizeCandidate",
)

PARITY_HARNESS = r"""
const resize = require(RESIZE_PATH);

var splitSlotRects = null;
var splitColumnWeights = null;
var splitRowWeights = null;
var terminals = [];
var gridBox = { width: 1216, height: 816 };
var headerHeight = 34;
const grid = { children: [], getBoundingClientRect: () => gridBox };
var window = globalThis;
window.getComputedStyle = () => ({
    paddingLeft: '8px', paddingRight: '8px', paddingTop: '8px', paddingBottom: '8px',
    columnGap: '8px', rowGap: '8px', gap: '8px'
});
const document = { getElementById: id => (id === 'terminalsGrid' ? grid : null) };

const ones = count => Array.from({ length: count }, () => 1);
const layouts = {
    sideBySide: [{ x: 1, y: 1, w: 8, h: 8 }, { x: 9, y: 1, w: 8, h: 8 }],
    leftAndStack: [
        { x: 1, y: 1, w: 8, h: 8 }, { x: 9, y: 1, w: 8, h: 4 }, { x: 9, y: 5, w: 8, h: 4 }
    ]
};
const skews = [0.5, 0.9, 0.97, 0.99, 1.2, 3, 8, 15];
const boxes = [{ width: 1216, height: 816 }, { width: 520, height: 300 }, { width: 300, height: 160 }];
const cells = [{ width: 8, height: 17 }, { width: 12, height: 24 }];

const cases = [];
for (const [name, rects] of Object.entries(layouts)) {
    for (const box of boxes) {
        for (const cell of cells) {
            for (const header of [34, 40]) {
                for (const explorerFirst of [false, true]) {
                    for (const skew of skews) {
                        for (const axis of ['vertical', 'horizontal']) {
                            gridBox = box;
                            headerHeight = header;
                            splitSlotRects = rects;
                            splitColumnWeights = ones(16);
                            splitRowWeights = ones(8);
                            terminals = rects.map((_rect, index) => (explorerFirst && index === 0
                                ? { _paneType: 'explorer' }
                                : { term: { _core: { _renderService: { dimensions: { css: { cell } } } } } }));
                            grid.children = rects.map((_rect, index) => ({
                                dataset: { slot: String(index) },
                                querySelector: () => ({ getBoundingClientRect: () => ({ height: header }) })
                            }));
                            const candidate = axis === 'vertical'
                                ? [...Array(8).fill(skew), ...Array(8).fill(1)]
                                : [...Array(4).fill(skew), ...Array(4).fill(1)];
                            const page = validateResizeCandidate(axis, candidate);
                            const columns = axis === 'vertical' ? candidate : splitColumnWeights;
                            const rows = axis === 'horizontal' ? candidate : splitRowWeights;
                            const metrics = getResizableGridMetrics(grid, columns, rows);
                            const pure = resize.policy.fits({
                                surfaces: rects.map(rect => getPaneCandidateSurface(rect, columns, rows, metrics)),
                                columnTrackSpace: metrics.columnTrackSpace,
                                rowTrackSpace: metrics.rowTrackSpace,
                                exempt: rects.map((_rect, index) => explorerFirst && index === 0),
                                cell,
                                headerHeight: header,
                                minCols: MIN_SPLIT_COLS,
                                minRows: MIN_SPLIT_ROWS,
                                minSurfaceRatio: MIN_RESIZE_SURFACE_RATIO
                            });
                            cases.push({ name, box, cell, header, explorerFirst, skew, axis, page, pure });
                        }
                    }
                }
            }
        }
    }
}
const policy = resize.policy;
const RECTS = layouts.sideBySide;
const model = { ids: ['a', 'b'], rects: RECTS, columnWeights: ones(16), rowWeights: ones(8) };
const live = geometry => ({
    panes: [{ session_id: 'a' }, { session_id: 'b' }],
    geometry: { split_slot_rects: RECTS, column_weights: ones(16), row_weights: ones(8), ...geometry }
});
console.log(JSON.stringify({
    total: cases.length,
    fitting: cases.filter(item => item.page).length,
    different: cases.filter(item => item.page !== item.pure).slice(0, 3),
    same: {
        equal: policy.sameLayout(model, live({})),
        withinRounding: policy.sameLayout(model, live({ column_weights: ones(16).map(w => w + 1e-9) })),
        weights: policy.sameLayout(model, live({ row_weights: ones(7) })),
        rects: policy.sameLayout(model, live({ split_slot_rects: [RECTS[0], { ...RECTS[1], h: 4 }] })),
        noGeometry: policy.sameLayout(model, { panes: live({}).panes, geometry: null }),
        panes: policy.sameLayout(model, { ...live({}), panes: [{ session_id: 'a' }] })
    }
}));
"""

BRIDGE_HARNESS = r"""
function build(options = {}) {
    const calls = [];
    const context = {
        currentWorkspaceId: 'ws',
        gridBuilt: true,
        visibleGroupId: options.visible === undefined ? 'g-1' : options.visible,
        activeGroupId: options.active === undefined ? 'g-1' : options.active,
        sessionGroups: [{ group_id: 'g-1' }, { group_id: 'g-2' }],
        /* The painted handler's first refusal is enough to know it ran. */
        resizeIntentInFlight: Boolean(options.inFlight),
        activeGridResize: null,
        backgroundResize: options.module === null ? null : {
            perform: async intent => { calls.push(['background', intent.group_id]); return { ok: true, via: 'background' }; }
        }
    };
    vm.runInNewContext(`${BRIDGE_SOURCE}\nglobalThis.bridge = resizeBridge;`, context);
    return { bridge: context.bridge, calls };
}
const intent = group => ({ workspace_id: 'ws', group_id: group, axis: 'vertical', line_index: 1, position: 0.5, expected_revision: 1 });

(async () => {
    const out = {};
    {
        const { bridge, calls } = build({ inFlight: true });
        out.background = await bridge.perform(intent('g-2'));
        out.painted = await bridge.perform(intent('g-1'));
        out.unknownGroup = await bridge.perform(intent('g-9'));
        out.calls = calls;
        out.holds = { held: bridge.holds('ws', 'g-2'), painted: bridge.holds('ws', 'g-1'), elsewhere: bridge.holds('ws2', 'g-2') };
    }
    {
        // Mid-switch: the window cannot say which grid it would measure.
        const { bridge, calls } = build({ active: 'g-2', visible: 'g-1' });
        out.switching = await bridge.perform(intent('g-2'));
        out.switchingCalls = calls;
    }
    {
        const { bridge } = build({ module: null });
        out.withoutModule = await bridge.perform(intent('g-2'));
    }
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def _terminals_source() -> str:
    return TERMINALS_JS.read_text(encoding="utf-8")


@unittest.skipIf(NODE is None, "Node.js is required for the background-resize suite")
class BackgroundResizeModuleTestCase(unittest.TestCase):
    """The sequence, against a page that records what it is asked to do, with
    the real tab module, the real planner and the page's real track groups."""

    @classmethod
    def setUpClass(cls):
        tracks = "\n\n".join(
            _function_source(_terminals_source(), name) for name in TRACK_SOURCE_NAMES
        )
        cls.out = _run_node_file(
            f"const RESIZE_PATH = {json.dumps(str(BACKGROUND_RESIZE_JS))};\n"
            f"const TAB_PATH = {json.dumps(str(BACKGROUND_TAB_JS))};\n"
            f"const GEOMETRY_PATH = {json.dumps(str(SPLIT_GEOMETRY_JS))};\n"
            + tracks + "\n" + MODULE_HARNESS
        )

    def test_a_divider_is_read_checked_measured_then_written_and_taken(self):
        happy = self.out["happy"]

        self.assertTrue(happy["answer"]["ok"])
        self.assertEqual(
            happy["log"],
            [
                "isShown", "settle", "readModel", "readLayout", "measure", "measure",
                "readExemptions", "isShown", "discard:g-2", "saveLayout", "adopt", "load",
            ],
        )

    def test_the_track_groups_are_the_pointer_drags_for_any_rectangles(self):
        tracks = self.out["tracks"]

        self.assertEqual(
            tracks["painted"], {"before": list(range(8)), "after": list(range(8, 16))}
        )
        self.assertEqual(tracks["model"], tracks["painted"])
        self.assertIsNone(tracks["noLine"])

    def test_the_weights_are_written_against_the_revision_the_agent_read(self):
        happy = self.out["happy"]
        save = happy["saves"][0]

        self.assertEqual(save["groupId"], "g-2")
        self.assertEqual(save["expectedRevision"], 5)
        self.assertEqual(save["ids"], ["pane-a", "pane-b"])
        self.assertEqual(save["baseCount"], 2)
        # 0.6 of the width: the eight tracks before the line grow by a fifth,
        # the eight after shrink by as much; rows untouched.
        self.assertEqual([round(weight, 6) for weight in save["columnWeights"]], [1.2] * 8 + [0.8] * 8)
        self.assertEqual(save["rowWeights"], [1] * 8)
        result = happy["answer"]["result"]
        self.assertEqual(result["revision"], 6)
        self.assertEqual(result["column_weights"], save["columnWeights"])
        self.assertEqual(
            result["panes"][1],
            {"session_id": "pane-b", "index": 1, "rect": {"x": 9, "y": 1, "w": 8, "h": 8}},
        )
        adopted = happy["adopted"][0]
        self.assertEqual(adopted["group"]["group_id"], "g-2")
        self.assertEqual(adopted["saved"]["revision"], 6)
        self.assertEqual(adopted["saved"]["columnWeights"], save["columnWeights"])

    def test_a_stacked_divider_moves_the_rows(self):
        save = self.out["stacked"]["saves"][0]

        self.assertTrue(self.out["stacked"]["answer"]["ok"])
        self.assertEqual(save["columnWeights"], [1] * 16)
        self.assertEqual([round(weight, 6) for weight in save["rowWeights"]], [0.8] * 4 + [1.2] * 4)

    def test_a_tab_picked_while_the_write_is_out_loads_after_it(self):
        happy = self.out["happy"]

        self.assertTrue(happy["saves"][0]["heldDuringWrite"])
        self.assertEqual(happy["log"][-3:], ["saveLayout", "adopt", "load"])
        self.assertTrue(happy["released"])

    def test_a_tab_showing_is_resized_by_the_handler_that_owns_its_grid(self):
        for name in ("shownAtStart", "openedWhileMeasuring"):
            with self.subTest(case=name):
                case = self.out[name]
                self.assertTrue(case["answer"]["viaVisible"])
                self.assertEqual(case["answer"]["intent"]["group_id"], "g-2")
                self.assertNotIn("saveLayout", case["log"])
                self.assertFalse(any(step.startswith("discard") for step in case["log"]))
        # Opened at the start: nothing was even read.
        self.assertEqual(self.out["shownAtStart"]["log"], ["isShown", "performShown"])

    def test_every_refusal_comes_before_anything_is_written(self):
        expected = {
            "notHeld": "switching session tabs",
            "closePending": "has not been shown since",
            "closeDuringRead": "A pane closed while the resize was being prepared.",
            "stale": "The session layout changed; read list_panes and retry.",
            "differs": "The page and stored layout differ",
            "reordered": "The page and stored layout differ",
            "gone": "The session is no longer open.",
            "serverGone": "Session group not found",
            "outsideLine": "outside this grid",
            "outsidePosition": "outside this grid",
            "noEdge": "There is no shared pane edge at that divider.",
            "narrow": "too narrow to resize the grid",
            "unmeasurable": "The grid has no measurable space.",
            "noSpace": "leaves no space beside the divider",
            "minimum": "smaller than its minimum width or height",
            "readThrew": "The resize failed: list failed",
        }
        for name, sentence in expected.items():
            with self.subTest(case=name):
                case = self.out[name]
                self.assertFalse(case["answer"]["ok"])
                self.assertNotIn("unknown", case["answer"])
                self.assertIn(sentence, case["answer"]["error"])
                self.assertTrue(case["answer"]["error"].endswith("Nothing changed."))
                self.assertNotIn("saveLayout", case["log"])
                self.assertFalse(any(step.startswith("discard") for step in case["log"]))
                self.assertEqual(case["adopted"], [])
                self.assertTrue(case["released"])

    def test_a_close_waiting_for_the_tab_refuses_before_reading(self):
        self.assertEqual(self.out["closePending"]["log"], ["isShown"])

    def test_a_write_the_server_refused_changed_nothing(self):
        refused = self.out["saveRefused"]

        self.assertFalse(refused["answer"]["ok"])
        self.assertNotIn("unknown", refused["answer"])
        self.assertIn("Presentation revision is stale", refused["answer"]["error"])
        self.assertIn("Nothing changed", refused["answer"]["error"])
        self.assertEqual(refused["adopted"], [])
        self.assertTrue(refused["released"])

    def test_once_the_write_started_a_lost_answer_is_unknown(self):
        expected = {
            "saveThrew": "could not be confirmed: connection lost",
            "saveUnknown": "returned no valid revision",
            "saveOldRevision": "returned no valid revision",
            "closeDuringWrite": "A pane closed while the resize write was in flight",
        }
        for name, sentence in expected.items():
            with self.subTest(case=name):
                case = self.out[name]
                self.assertFalse(case["answer"]["ok"])
                self.assertTrue(case["answer"]["unknown"])
                self.assertIn(sentence, case["answer"]["error"])
                self.assertNotIn("Nothing changed", case["answer"]["error"])
                self.assertEqual(case["adopted"], [])
                self.assertTrue(case["released"])


@unittest.skipIf(NODE is None, "Node.js is required for the background-resize suite")
class BackgroundResizeMinimumParityTestCase(unittest.TestCase):
    """One rule, two measurement sources: the model-based minimum check
    against the page's own `validateResizeCandidate`, case for case."""

    @classmethod
    def setUpClass(cls):
        source = _terminals_source()
        lifted = "\n\n".join(
            [_js_const_source(source, "MIN_SPLIT_COLS", "MIN_SPLIT_ROWS", "MIN_RESIZE_SURFACE_RATIO")]
            + [_function_source(source, name) for name in PARITY_SOURCE_NAMES]
        )
        harness = PARITY_HARNESS.replace("var window = globalThis;", "")
        cls.out = _run_node_file(
            f"const RESIZE_PATH = {json.dumps(str(BACKGROUND_RESIZE_JS))};\n"
            "var window = globalThis;\n"
            + lifted + "\n" + harness
        )

    def test_the_pure_minimum_gives_the_page_minimum_case_for_case(self):
        parity = self.out

        self.assertEqual(parity["total"], 2 * 3 * 2 * 2 * 2 * 8 * 2)
        self.assertEqual(parity["different"], [])
        # Both answers occur, so the comparison is not vacuous.
        self.assertGreater(parity["fitting"], 0)
        self.assertLess(parity["fitting"], parity["total"])

    def test_the_model_is_the_record_only_when_panes_rects_and_weights_agree(self):
        same = self.out["same"]

        self.assertTrue(same["equal"])
        self.assertTrue(same["withinRounding"])
        for name in ("weights", "rects", "noGeometry", "panes"):
            with self.subTest(case=name):
                self.assertFalse(same[name])


@unittest.skipIf(NODE is None, "Node.js is required for the background-resize suite")
class ResizeBridgeRoutingTestCase(unittest.TestCase):
    """The resize bridge: a tab held in the background is resized from behind,
    the painted tab by the handler that owns its grid."""

    @classmethod
    def setUpClass(cls):
        source = _terminals_source()
        bridge = _function_source(source, "backgroundGroupHeld") + "\n" + _between(
            source, "    const resizeBridge = {", "    window.GridVibeResizeBridge = resizeBridge;"
        )
        cls.out = _run_node_file(
            "const vm = require('vm');\n"
            f"const BRIDGE_SOURCE = {json.dumps(bridge)};\n" + BRIDGE_HARNESS
        )

    def test_a_tab_held_in_the_background_is_resized_from_behind(self):
        self.assertEqual(self.out["background"], {"ok": True, "via": "background"})
        self.assertEqual(self.out["calls"], [["background", "g-2"]])

    def test_the_painted_tab_is_resized_by_the_visible_handler(self):
        self.assertFalse(self.out["painted"]["ok"])
        self.assertIn("Another resize is in progress.", self.out["painted"]["error"])

    def test_the_bridge_still_claims_any_tab_of_its_workspace(self):
        self.assertEqual(
            self.out["holds"], {"held": True, "painted": True, "elsewhere": False}
        )

    def test_a_window_between_tabs_refuses_rather_than_measuring_another_grid(self):
        self.assertEqual(self.out["switchingCalls"], [])
        self.assertIn("switching session tabs", self.out["switching"]["error"])
        self.assertIn("Nothing changed", self.out["switching"]["error"])

    def test_open_this_session_tab_is_no_longer_asked_for(self):
        for name in ("switching", "withoutModule", "unknownGroup"):
            with self.subTest(case=name):
                self.assertNotIn("Open this session tab", self.out[name]["error"])


if __name__ == "__main__":
    unittest.main()
