"""A split reaches a pane in a session tab the window is not showing, unseen.

The point is that nobody notices: no tab switch, no focus change, no repaint of
the grid on screen. So the tab is edited as a *model* -- pane order, one
rectangle each, two lists of track weights -- measured against the window's one
shared grid, and written through the group's revisioned presentation
transaction. What is pinned here is executed in Node, not read as source:

- **The rules are the split button's rules.** The pure reading of a pane is
  compared against the page's own `getSplitBlockers`, case for case.
- **The sequence.** Refuse before anything is created; create before writing;
  drop the tab's cached view before the write; look at whether the tab was
  opened *immediately* before the request.
- **A pane that exists is reported, whatever happened to its layout.**
- **The page half reads a tab as data**: from its cached view while that still
  holds exactly the server's panes, otherwise from the server's summary, at this
  build's resolution -- and never with the window's own refresh, which would
  re-point the active tab.
- **A split whose request was in flight when the window moved on paints nothing
  into whatever is showing.**
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_split_geometry import RESTORE_SOURCE, RESTORE_STUBS

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"
BACKGROUND_SPLIT_JS = STATIC_JS / "background-split.js"
SPLIT_GEOMETRY_JS = STATIC_JS / "split-geometry.js"
TERMINALS_JS = STATIC_JS / "terminals.js"
TERMINALS_HTML = ROOT / "templates" / "terminals.html"

NODE = shutil.which("node")


def _function_source(script: str, name: str) -> str:
    """One top-level function's source, brace-matched, `async` included."""
    start = script.index(f"function {name}(")
    if script[max(0, start - 6):start] == "async ":
        start -= 6
    depth = 0
    for index in range(script.index("{", script.index(")", start)), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _between(source: str, start: str, end: str) -> str:
    first = source.index(start)
    return source[first:source.index(end, first)]


def _run_node_file(script: str) -> dict:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "harness.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [NODE, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


MODULE_HARNESS = r"""
const bg = require(MODULE_PATH);

const RECT = (originSlot, x, y, w, h) => ({ originSlot, x, y, w, h });

function baseModel() {
    return {
        groupId: 'g-2',
        ids: ['pane-a', 'pane-b'],
        rects: [RECT(0, 1, 1, 8, 8), RECT(1, 9, 1, 8, 8)],
        columnWeights: Array.from({ length: 16 }, () => 1),
        rowWeights: Array.from({ length: 8 }, () => 1),
        baseCount: 2
    };
}

const ROOMY = { width: 800, height: 600 };
const CELL = { width: 8, height: 17 };

/* A page that records everything it is asked to do, in order. */
function fakePage(options = {}) {
    const log = [];
    const saves = [];
    const adopted = [];
    const requests = [];
    const model = options.model === undefined ? baseModel() : options.model;
    const measured = options.measured === undefined
        ? { narrow: false, surfaces: [ROOMY, ROOMY], cell: CELL, headerHeight: 34 }
        : options.measured;
    const state = { shown: Boolean(options.shown) };
    const page = {
        groupOf: id => ((options.held || ['pane-b']).includes(id) ? 'g-2' : ''),
        isShown: () => { log.push('isShown'); return state.shown; },
        settle: async () => {
            log.push('settle');
            if (options.settleThrows) throw new Error('queue failed');
        },
        readModel: async () => {
            log.push('readModel');
            if (options.readThrows) throw new Error('list failed');
            return model;
        },
        measure: () => { log.push('measure'); return measured; },
        plan: (_model, _index, axis) => {
            log.push('plan');
            return { firstSpan: 4, weights: axis === 'vertical' ? Array.from({ length: 16 }, () => 2) : [3, 3, 3, 3, 3, 3, 3, 3] };
        },
        splitRect: (rect, axis, firstSpan) => (axis === 'vertical'
            ? [RECT(rect.originSlot, rect.x, rect.y, firstSpan, rect.h),
               RECT(rect.originSlot, rect.x + firstSpan, rect.y, rect.w - firstSpan, rect.h)]
            : [RECT(rect.originSlot, rect.x, rect.y, rect.w, firstSpan),
               RECT(rect.originSlot, rect.x, rect.y + firstSpan, rect.w, rect.h - firstSpan)]),
        reason: (axis, blocker, count) => `reason:${axis}:${blocker}:${count}`,
        unmeasurable: () => 'unmeasurable',
        split: async (id, axis, request) => {
            log.push('split');
            requests.push({ id, axis, request });
            if (options.duringSplit) options.duringSplit(api, log);
            if (options.splitWaits) await options.splitWaits;
            if (options.splitThrows) throw new Error('network down');
            return options.splitResult || {
                ok: true,
                session: { session_id: 'pane-new' },
                group: { group_id: 'g-2', presentation_revision: 7 }
            };
        },
        discard: () => { log.push('discard'); },
        saveLayout: async args => {
            log.push('saveLayout');
            saves.push(args);
            if (options.saveThrows) throw new Error('write failed');
            return options.saveResult || { ok: true, revision: 8 };
        },
        adopt: (group, saved) => { log.push('adopt'); adopted.push({ group, saved }); },
        performShown: async () => { log.push('performShown'); return { ok: true, viaVisibleHandler: true }; },
        limits: { maxPanes: options.maxPanes || 16, minCols: 8, minRows: 4 },
        onError: error => log.push(`error:${error.message}`)
    };
    const api = bg.create(page);
    return { page, log, saves, adopted, requests, api };
}

/* Whether the tab is free to load within a tick or two, or still held. */
async function releasedSoon(api, groupId) {
    return Promise.race([
        api.settled(groupId).then(() => true),
        new Promise(resolve => setTimeout(() => resolve(false), 50))
    ]);
}

const out = {};

(async () => {
    // ── the pure reading ──
    out.capacity = {
        roomyVertical: bg.policy.capacity(ROOMY, 'vertical', CELL, 34),
        roomyHorizontal: bg.policy.capacity(ROOMY, 'horizontal', CELL, 34),
        tinyVertical: bg.policy.capacity({ width: 100, height: 100 }, 'vertical', CELL, 34),
        tinyHorizontal: bg.policy.capacity({ width: 100, height: 100 }, 'horizontal', CELL, 34),
        unusable: bg.policy.capacity(null, 'vertical', null, 0)
    };
    const read = (overrides = {}) => bg.policy.blockers({
        narrow: false, count: 2, maxPanes: 16,
        rect: RECT(0, 1, 1, 8, 8), surface: ROOMY, cell: CELL, headerHeight: 34,
        minCols: 8, minRows: 4, ...overrides
    });
    out.blockers = {
        free: read(),
        narrow: read({ narrow: true }),
        atCap: read({ count: 16 }),
        oneLineWide: read({ rect: RECT(0, 1, 1, 1, 8) }),
        oneLineTall: read({ rect: RECT(0, 1, 1, 8, 1) }),
        tiny: read({ surface: { width: 100, height: 100 } }),
        // The grid is asked first: a pane with no line left has no halves to measure.
        gridBeforeSize: read({ rect: RECT(0, 1, 1, 1, 1), surface: { width: 100, height: 100 } })
    };
    const arranged = bg.policy.arrange({
        ids: ['pane-a', 'pane-b', 'pane-c'],
        rects: [RECT(0, 1, 1, 8, 8), RECT(1, 9, 1, 8, 8), RECT(2, 17, 1, 8, 8)],
        columnWeights: [1], rowWeights: [1],
        visualIndex: 1, axis: 'vertical',
        plan: { firstSpan: 4, weights: [7, 7] }, newId: 'pane-new',
        splitRect: (rect, axis, firstSpan) => [
            RECT(rect.originSlot, rect.x, rect.y, firstSpan, rect.h),
            RECT(rect.originSlot, rect.x + firstSpan, rect.y, rect.w - firstSpan, rect.h)
        ]
    });
    out.arranged = arranged;
    out.arrangedStacked = bg.policy.arrange({
        ids: ['pane-a'], rects: [RECT(0, 1, 1, 8, 8)], columnWeights: [1], rowWeights: [1],
        visualIndex: 0, axis: 'horizontal', plan: { firstSpan: 4, weights: null }, newId: 'pane-new',
        splitRect: (rect, axis, firstSpan) => [
            RECT(0, rect.x, rect.y, rect.w, firstSpan),
            RECT(0, rect.x, rect.y + firstSpan, rect.w, rect.h - firstSpan)
        ]
    });

    // ── the sequence ──
    {
        const t = fakePage();
        const answer = await t.api.perform('pane-b', 'vertical', { kind: 'agent', agent: 'codex' });
        out.happy = {
            answer, log: t.log, saves: t.saves, adopted: t.adopted, requests: t.requests
        };
    }
    {
        const t = fakePage();
        const answer = await t.api.perform('pane-b', 'horizontal', null);
        out.stacked = { answer, saves: t.saves };
    }
    {
        const t = fakePage();
        out.held = { yes: t.api.holds('pane-b'), no: t.api.holds('pane-elsewhere') };
        out.candidatesRoomy = await t.api.candidates('pane-b');
        out.candidatesElsewhere = await t.api.candidates('pane-elsewhere');
    }
    {
        const t = fakePage({ measured: { narrow: false, surfaces: [ROOMY, { width: 100, height: 100 }], cell: CELL, headerHeight: 34 } });
        out.candidatesTiny = await t.api.candidates('pane-b');
        out.reasonTiny = await t.api.reason('vertical', 'pane-b');
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.refusedTiny = { answer, log: t.log };
    }
    {
        const t = fakePage({ model: { ...baseModel(), rects: [RECT(0, 1, 1, 8, 8), RECT(1, 9, 1, 1, 8)] } });
        out.oneLineWide = {
            candidates: await t.api.candidates('pane-b'),
            reason: await t.api.reason('vertical', 'pane-b')
        };
    }
    {
        const t = fakePage({ maxPanes: 2 });
        out.atCap = {
            candidates: await t.api.candidates('pane-b'),
            reason: await t.api.reason('horizontal', 'pane-b')
        };
    }
    {
        const t = fakePage({ measured: null });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.unmeasurable = {
            candidates: await t.api.candidates('pane-b'),
            reason: await t.api.reason('vertical', 'pane-b'),
            answer,
            log: t.log
        };
    }
    {
        const t = fakePage({ shown: true });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.openedMeanwhile = { answer, log: t.log };
    }
    {
        const t = fakePage({ splitResult: { ok: false, error: 'Split failed with status 400' } });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.createFailed = { answer, log: t.log, released: await releasedSoon(t.api, 'g-2') };
    }
    {
        /* The tab is picked while the request is out: its load asks first, and
           runs only once the split has written the arrangement. */
        let load = null;
        let answerRequest = null;
        let requestSent = null;
        const sent = new Promise(resolve => { requestSent = resolve; });
        const t = fakePage({
            splitWaits: new Promise(resolve => { answerRequest = resolve; }),
            duringSplit: (api, log) => {
                load = api.settled('g-2').then(() => log.push('load'));
                requestSent();
            }
        });
        const performing = t.api.perform('pane-b', 'vertical', null);
        await sent;
        const heldDuring = !(await releasedSoon(t.api, 'g-2'));
        const otherTabFree = await releasedSoon(t.api, 'g-1');
        answerRequest();
        const answer = await performing;
        await load;
        out.openedDuringRequest = {
            answer, log: t.log, heldDuring, otherTabFree,
            releasedAfter: await releasedSoon(t.api, 'g-2')
        };
    }
    {
        const t = fakePage({ splitThrows: true });
        let thrown = '';
        try { await t.api.perform('pane-b', 'vertical', null); } catch (error) { thrown = error.message; }
        out.splitThrew = { thrown, released: await releasedSoon(t.api, 'g-2') };
    }
    {
        const t = fakePage();
        out.idle = await releasedSoon(t.api, 'g-2');
    }
    {
        const t = fakePage({ saveResult: { ok: false, error: 'stale' } });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.saveFailed = { answer, adopted: t.adopted, log: t.log };
    }
    {
        const t = fakePage({ saveThrows: true });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.saveThrew = {
            answer, adopted: t.adopted, log: t.log, released: await releasedSoon(t.api, 'g-2')
        };
    }
    {
        const t = fakePage({ splitResult: { ok: true, session: { session_id: 'pane-new' }, group: { group_id: 'g-2' } } });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.noRevision = { answer, saves: t.saves, log: t.log };
    }
    {
        const t = fakePage({ settleThrows: true });
        const answer = await t.api.perform('pane-b', 'vertical', null);
        out.settleThrew = { answer, log: t.log };
    }
    {
        const t = fakePage({ model: null });
        out.gone = { answer: await t.api.perform('pane-b', 'vertical', null), log: t.log };
    }
    {
        const t = fakePage({ model: { ...baseModel(), ids: ['pane-a', 'pane-c'] } });
        out.leftItsTab = { answer: await t.api.perform('pane-b', 'vertical', null), log: t.log };
    }
    {
        const t = fakePage({ readThrows: true });
        let thrown = '';
        try { await t.api.perform('pane-b', 'vertical', null); } catch (error) { thrown = error.message; }
        out.readThrew = { thrown, log: t.log };
    }
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""

ADAPTER_SOURCE_NAMES = (
    "cloneSplitTrackWeights",
    "buildWorkspaceLayoutSnapshotFromState",
    "getLayoutClass",
    "adoptSplitGroupRecord",
    "sumTrackSpan",
    "getResizableGridMetrics",
    "getPaneCandidateSurface",
    "backgroundGroupHolding",
    "fetchGroupRecord",
    "readBackgroundGroupModel",
    "measureTerminalCell",
    "measureGridForModel",
    "adoptBackgroundSplit",
    "discardBackgroundGroupView",
)

ADAPTER_STUBS = r"""
const bg = require(BACKGROUND_SPLIT_PATH);

var gridBuilt = true;
var visibleGroupId = 'g-1';
var activeGroupId = 'g-1';
var sessionIds = ['pane-a', 'pane-b'];
var sessionGroups = [];
var cachedGroupViews = new Map();
var currentWorkspaceId = 'ws-1';
var revisionsSet = [];
var tabsRendered = 0;
function presentationController() {
    return { setGroupRevision: (groupId, revision) => revisionsSet.push([groupId, revision]) };
}
function renderSessionTabs() { tabsRendered += 1; }
var dropped = [];
function dropCachedGroupView(groupId) { dropped.push(groupId); }

var served = { ok: true, groups: [] };
async function fetch() {
    return {
        ok: served.ok,
        status: served.ok ? 200 : 500,
        json: async () => (served.ok ? { groups: served.groups } : { error: 'server down' })
    };
}

const GRID = { getBoundingClientRect: () => ({ width: 1216, height: 816 }) };
var gridBox = { width: 1216, height: 816 };
GRID.getBoundingClientRect = () => gridBox;
var headerBox = null;
document.getElementById = id => (id === 'terminalsGrid' ? GRID : null);
document.querySelector = () => (headerBox ? { getBoundingClientRect: () => headerBox } : null);
window.getComputedStyle = () => ({
    paddingLeft: '8px', paddingRight: '8px', paddingTop: '8px', paddingBottom: '8px',
    columnGap: '8px', rowGap: '8px', gap: '8px'
});

function cardsFor(slots) {
    return { children: slots.map(slot => ({ dataset: { slot: String(slot) } })) };
}
"""

ADAPTER_BODY = r"""
const ids = ['pane-a', 'pane-b'];
const twoPaneRects = [
    { originSlot: 0, x: 1, y: 1, w: 8, h: 8 },
    { originSlot: 1, x: 9, y: 1, w: 8, h: 8 }
];
const plain = rects => rects.map(rect => ({ x: rect.x, y: rect.y, w: rect.w, h: rect.h }));
const out = {};

(async () => {
    // ── which tab holds a pane this window is not showing ──
    sessionGroups = [
        { group_id: 'g-1', pane_order: ['pane-a', 'pane-b'] },
        { group_id: 'g-2', pane_order: ['pane-c'] },
        { group_id: 'g-3' }
    ];
    out.holding = {
        background: backgroundGroupHolding('pane-c'),
        onScreen: backgroundGroupHolding('pane-a'),
        unknown: backgroundGroupHolding('pane-nowhere'),
        empty: backgroundGroupHolding('')
    };
    activeGroupId = 'g-2';
    const switching = backgroundGroupHolding('pane-c');
    activeGroupId = 'g-1';
    gridBuilt = false;
    const unbuilt = backgroundGroupHolding('pane-c');
    gridBuilt = true;
    visibleGroupId = '';
    const noneVisible = backgroundGroupHolding('pane-c');
    visibleGroupId = 'g-1';
    out.unsettled = { switching, unbuilt, noneVisible };

    // ── a tab as data: the server's summary ──
    served = { ok: true, groups: [{
        group_id: 'g-2', pane_order: ids, layout: 'vertical',
        workspace_layout: {
            split_slot_rects: [
                { originSlot: 0, x: 1, y: 1, w: 8, h: 16 },
                { originSlot: 1, x: 9, y: 1, w: 8, h: 8 }
            ],
            split_column_weights: Array.from({ length: 16 }, (_v, i) => (i < 8 ? 1.5 : 0.5)),
            split_row_weights: Array.from({ length: 16 }, () => 1),
            original_split_slot_count: 2
        }
    }] };
    const stored = await readBackgroundGroupModel('g-2');
    out.stored = {
        ids: stored.ids, rects: plain(stored.rects),
        columns: stored.columnWeights.length, rows: stored.rowWeights.length,
        firstColumnWeight: stored.columnWeights[0], baseCount: stored.baseCount
    };

    // ── no record the page wrote: the preset its size and layout name call for ──
    served = { ok: true, groups: [{ group_id: 'g-2', pane_order: ids, layout: 'horizontal', workspace_layout: null }] };
    const preset = await readBackgroundGroupModel('g-2');
    out.preset = {
        rects: plain(preset.rects), columns: preset.columnWeights.length,
        rows: preset.rowWeights.length, baseCount: preset.baseCount
    };

    // ── a layout saved at a coarser grid is read at this build's resolution ──
    const coarse = coarseSnapshot();
    served = { ok: true, groups: [{
        group_id: 'g-2', layout: 'grid',
        pane_order: coarse.split_slot_rects.map((_r, i) => `pane-${i}`),
        workspace_layout: coarse
    }] };
    const finer = await readBackgroundGroupModel('g-2');
    out.coarse = { box: boxOf(finer.rects), first: plain(finer.rects)[0], baseCount: finer.baseCount };

    // ── its cached view, while that still holds exactly the server's panes ──
    served = { ok: true, groups: [{ group_id: 'g-2', pane_order: ['pane-a', 'pane-b', 'pane-c'], layout: 'vertical' }] };
    cachedGroupViews.set('g-2', {
        className: 'layout-split-local',
        fragment: cardsFor([2, 0, 1]),
        sessionIds: ['pane-a', 'pane-b', 'pane-c'],
        splitSlotRects: [
            { originSlot: 0, x: 1, y: 1, w: 8, h: 8 },
            { originSlot: 1, x: 9, y: 1, w: 8, h: 8 },
            { originSlot: 2, x: 1, y: 9, w: 16, h: 8 }
        ],
        splitColumnWeights: Array.from({ length: 16 }, () => 1.25),
        splitRowWeights: Array.from({ length: 16 }, () => 1),
        originalSplitSlotCount: 3
    });
    const cached = await readBackgroundGroupModel('g-2');
    out.cached = {
        // Visual order is the cards' order, not creation order.
        ids: cached.ids, rects: plain(cached.rects),
        weight: cached.columnWeights[0], baseCount: cached.baseCount
    };

    // ── ...and not once the server lists panes it does not hold ──
    served = { ok: true, groups: [{ group_id: 'g-2', pane_order: ['pane-a', 'pane-b', 'pane-c', 'pane-d'], layout: 'vertical' }] };
    const stale = await readBackgroundGroupModel('g-2');
    out.staleCache = { ids: stale.ids, rects: stale.rects.length, weight: stale.columnWeights[0] };
    cachedGroupViews.clear();

    served = { ok: true, groups: [] };
    out.missing = await readBackgroundGroupModel('g-2');
    served = { ok: false, groups: [] };
    let listFailure = '';
    try { await readBackgroundGroupModel('g-2'); } catch (error) { listFailure = error.message; }
    out.listFailure = listFailure;

    // ── the shared grid, measured for one tab's weights ──
    const model = {
        rects: twoPaneRects.map(rect => ({ ...rect })),
        columnWeights: Array.from({ length: 16 }, () => 1),
        rowWeights: Array.from({ length: 8 }, () => 1)
    };
    window.innerWidth = 1600;
    const measured = measureGridForModel(model);
    out.measured = {
        narrow: measured.narrow,
        widths: measured.surfaces.map(surface => Math.round(surface.width * 10) / 10),
        height: Math.round(measured.surfaces[0].height * 10) / 10,
        cell: measured.cell, headerHeight: measured.headerHeight
    };
    terminals = [{}, { term: { _core: { _renderService: { dimensions: { css: { cell: { width: 9, height: 20 } } } } } } }];
    headerBox = { height: 40 };
    const live = measureGridForModel(model);
    out.liveCell = { cell: live.cell, headerHeight: live.headerHeight };
    terminals = [];
    headerBox = null;
    window.innerWidth = 600;
    out.narrowWindow = measureGridForModel(model).narrow;
    window.innerWidth = 1600;
    gridBox = { width: 0, height: 0 };
    out.collapsed = measureGridForModel(model);
    gridBox = { width: 1216, height: 816 };

    // ── the tab's record after a split the page just wrote ──
    sessionGroups = [{ group_id: 'g-2', pane_order: ['pane-a'], workspace_layout: null, presentation_revision: 3 }];
    adoptBackgroundSplit(
        { group_id: 'g-2', pane_order: ['pane-a', 'pane-new'], presentation_revision: 7, terminal_count: 2 },
        {
            ids: ['pane-a', 'pane-new'],
            rects: [{ originSlot: 0, x: 1, y: 1, w: 4, h: 8 }, { originSlot: 0, x: 5, y: 1, w: 4, h: 8 }],
            columnWeights: [1, 1, 1, 1, 1, 1, 1, 1], rowWeights: Array.from({ length: 8 }, () => 1),
            baseCount: 1, revision: 8
        }
    );
    out.adopted = {
        record: sessionGroups[0], revisions: revisionsSet.slice(), tabsRendered
    };
    revisionsSet = [];
    sessionGroups = [{ group_id: 'g-2', pane_order: ['pane-a'] }];
    adoptBackgroundSplit({ group_id: 'g-2', pane_order: ['pane-a', 'pane-new'], presentation_revision: 7 }, null);
    out.adoptedUnsaved = {
        order: sessionGroups[0].pane_order, layout: sessionGroups[0].workspace_layout || null,
        revision: sessionGroups[0].presentation_revision, revisions: revisionsSet.slice()
    };

    // ── which cached view a background split drops ──
    visibleGroupId = 'g-1';
    activeGroupId = 'g-1';
    discardBackgroundGroupView('g-2');
    discardBackgroundGroupView('g-1');
    // A tab picked while the split was in flight: active, waiting, not painted.
    activeGroupId = 'g-2';
    discardBackgroundGroupView('g-2');
    activeGroupId = 'g-1';
    out.dropped = dropped.slice();

    // ── the rules: this reading against the page's own getSplitBlockers ──
    const cell = { width: 8, height: 17 };
    const header = 34;
    const cases = [];
    const rectsToTry = [
        { x: 1, y: 1, w: 8, h: 8 }, { x: 1, y: 1, w: 1, h: 8 }, { x: 1, y: 1, w: 8, h: 1 },
        { x: 1, y: 1, w: 1, h: 1 }, { x: 1, y: 1, w: 2, h: 2 }
    ];
    const surfacesToTry = [
        { width: 800, height: 600 }, { width: 100, height: 100 }, { width: 140, height: 600 },
        { width: 800, height: 120 }, { width: 0, height: 0 }
    ];
    for (const innerWidth of [1600, 700]) {
        for (const paneCount of [2, 16]) {
            for (const rect of rectsToTry) {
                for (const surface of surfacesToTry) {
                    window.innerWidth = innerWidth;
                    terminals = Array.from({ length: paneCount }, () => ({}));
                    estimatePaneCharacters = (_index, axis) => bg.policy.capacity(surface, axis, cell, header);
                    const page = getSplitBlockers(0, rect);
                    const pure = bg.policy.blockers({
                        narrow: innerWidth <= 700, count: paneCount, maxPanes: MAX_SPLIT_TERMINALS,
                        rect, surface, cell, headerHeight: header,
                        minCols: MIN_SPLIT_COLS, minRows: MIN_SPLIT_ROWS
                    });
                    cases.push({ same: JSON.stringify(page) === JSON.stringify(pure), page, pure });
                }
            }
        }
    }
    window.innerWidth = 1600;
    out.parity = {
        total: cases.length,
        different: cases.filter(item => !item.same).slice(0, 3),
        codes: [SPLIT_BLOCKED_BY_WINDOW, SPLIT_BLOCKED_BY_GRID, SPLIT_BLOCKED_BY_SIZE]
    };
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""

BRIDGE_HARNESS = r"""
function build(options) {
    const calls = [];
    const context = {
        sessionIds: options.visible || ['pane-a'],
        backgroundSplit: options.module === null ? null : {
            holds: id => id === 'pane-c',
            candidates: async id => { calls.push(['candidates', id]); return ['vertical']; },
            reason: async (axis, id) => { calls.push(['reason', axis, id]); return 'why'; },
            perform: async (id, axis, request) => { calls.push(['perform', id, axis, request]); return { ok: true, viaBackground: true }; }
        },
        paneSplitTarget: id => (id === 'pane-a' ? { index: 0, rect: { x: 1, y: 1, w: 8, h: 8 } } : null),
        getSplitCandidates: () => ['vertical', 'horizontal'],
        getSplitBlockers: () => ({ vertical: '', horizontal: 'size' }),
        getSplitDisabledReason: (axis, blocker) => `visible:${axis}:${blocker}`,
        splitTerminalPane: async (index, axis, request) => { calls.push(['visible', index, axis, request]); return { ok: true, viaVisible: true }; }
    };
    vm.runInNewContext(`${BRIDGE_SOURCE}\nglobalThis.bridge = splitBridge;`, context);
    return { bridge: context.bridge, calls };
}

(async () => {
    const out = {};
    {
        const { bridge } = build({});
        out.owns = {
            visible: bridge.owns('pane-a'), background: bridge.owns('pane-c'), unknown: bridge.owns('pane-x')
        };
    }
    {
        const { bridge } = build({ module: null });
        out.ownsWithoutModule = { visible: bridge.owns('pane-a'), background: bridge.owns('pane-c') };
        out.performWithoutModule = await bridge.perform('pane-c', 'vertical', null);
    }
    {
        const { bridge, calls } = build({});
        out.candidates = {
            visible: bridge.candidates('pane-a'),
            background: await bridge.candidates('pane-c'),
            unknown: bridge.candidates('pane-x')
        };
        out.reasons = {
            visible: bridge.disabledReason('horizontal', 'pane-a'),
            background: await bridge.disabledReason('vertical', 'pane-c')
        };
        out.performed = {
            visible: await bridge.perform('pane-a', 'vertical', { kind: 'agent' }),
            background: await bridge.perform('pane-c', 'vertical', { kind: 'agent' }),
            unknown: await bridge.perform('pane-x', 'vertical', null)
        };
        out.calls = calls;
    }
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""

SPLIT_IN_FLIGHT_HARNESS = r"""
/* The real `splitTerminalPane`, against a page that is only as much of a page
   as it reads. `interrupt(context)` runs while the request is in flight, which
   is where a tab switch or a rebuild lands. */
async function run(interrupt) {
    const events = [];
    const original = { terminals: [{ _session: {} }], sessionIds: ['pane-a'] };
    const sourceCard = { querySelectorAll: () => [], after: () => events.push('card-added') };
    const grid = { children: [sourceCard] };
    const context = {
        resizeIntentInFlight: false,
        MAX_SPLIT_TERMINALS: 16,
        activeGroupId: 'g-1',
        visibleGroupId: 'g-1',
        terminals: original.terminals,
        sessionIds: original.sessionIds,
        splitSlotRects: [{ x: 1, y: 1, w: 8, h: 8 }],
        splitColumnWeights: null,
        splitRowWeights: null,
        socket: null,
        sessionGroups: [{ group_id: 'g-1', pane_order: ['pane-a'] }],
        document: {
            getElementById: id => id === 'terminalsGrid' ? grid : (id === 'tc-0' ? sourceCard : null)
        },
        closeAllPaneActionMenus: () => {},
        closeAllPaneShellMenus: () => {},
        updateAllSplitButtonStates: () => {},
        updateSplitButtonState: () => {},
        ensureSplitSlotRects: () => context.splitSlotRects,
        getSplitBlockers: () => ({ vertical: '', horizontal: '' }),
        getSplitDisabledReason: () => 'refused',
        isExplorerSession: () => false,
        getExplorerSelectedDirectory: () => '',
        makeTerminal: () => ({}),
        setSessionRoute: () => {},
        createSplitTerminalCard: () => ({}),
        planSplitSlotGeometry: () => ({ firstSpan: 4, weights: null }),
        splitSlotRect: rect => [rect, rect],
        applySplitSlotGeometry: () => events.push('painted'),
        attachSplitTerminalEvents: () => {},
        attachTerminal: () => {},
        renderSessionTabs: () => events.push('tabs-rendered'),
        updateSessionChrome: () => {},
        presentationController: () => ({ setGroupRevision: () => events.push('revision') }),
        noteGroupPresentationChanged: () => events.push('presentation-noted'),
        ensureAttachedTerminalsReady: async () => {},
        emitTerminalResize: () => {},
        setWorkspaceSaveMessage: () => {},
        console: { error: () => {} },
        fetch: async () => {
            interrupt(context, original);
            return {
                ok: true,
                json: async () => ({
                    session: { session_id: 'pane-new', status: 'starting' },
                    group: { group_id: 'g-1', pane_order: ['pane-a', 'pane-new'] }
                })
            };
        }
    };
    vm.runInNewContext(`${SPLIT_SOURCE}\nglobalThis.api = { splitTerminalPane };`, context);
    const answer = await context.api.splitTerminalPane(0, 'vertical', null);
    return {
        ok: answer.ok,
        index: answer.index,
        sessionId: answer.session && answer.session.session_id,
        events,
        showingTerminals: context.terminals.length,
        showingSessionIds: context.sessionIds.slice(),
        originalTerminals: original.terminals.length,
        originalSessionIds: original.sessionIds.slice(),
        groupPanes: context.sessionGroups[0].pane_order.slice()
    };
}

(async () => {
    const out = {};
    // Nothing moves while the request is out: the pane is painted where it was
    // asked for.
    out.steady = await run(() => {});
    // Another tab is picked while the request is out.
    out.switchedAway = await run(context => {
        context.activeGroupId = 'g-2';
        context.visibleGroupId = 'g-2';
        context.terminals = [{ _session: {} }];
        context.sessionIds = ['pane-other'];
    });
    // Another tab picked and the first one picked again: the same arrays are
    // back on screen, so the pane still belongs there.
    out.switchedBack = await run((context, original) => {
        context.terminals = [{ _session: {} }];
        context.sessionIds = ['pane-other'];
        context.terminals = original.terminals;
        context.sessionIds = original.sessionIds;
    });
    // The same tab is rebuilt from the server: new arrays, same group id.
    out.rebuilt = await run(context => {
        context.terminals = [{ _session: {} }];
        context.sessionIds = ['pane-a'];
    });
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


LOAD_GATE_HARNESS = r"""
/* The real `initialLoad`, as far as its first reads, against a tab whose split
   is in flight. The tab has no panes on the server, so a load that gets past
   the hold ends in `resetSessionView` rather than building a grid. */
async function run(scenario) {
    const events = [];
    let release = null;
    const held = new Promise(resolve => { release = resolve; });
    const context = {
        activeLoadToken: 0,
        activeGroupId: 'g-2',
        workspaceGone: false,
        backgroundSplit: {
            settled: async groupId => {
                events.push(`settled:${groupId}`);
                if (groupId === 'g-2') await held;
            }
        },
        document: {
            getElementById: () => ({ textContent: '', style: {}, classList: { add: () => {} } })
        },
        loadSessionGroups: async () => { events.push('groups'); return false; },
        getSessionApiPath: groupId => `/api/sessions?group_id=${groupId}`,
        fetch: async url => {
            events.push(`fetch:${url}`);
            return { ok: true, json: async () => ({ sessions: [] }) };
        },
        resetSessionView: async () => { events.push('reset'); },
        console: { error: (...args) => events.push(`error:${args.join(' ')}`) }
    };
    vm.runInNewContext(
        `${LOAD_SOURCE}\nglobalThis.api = { initialLoad, bump: () => { activeLoadToken += 1; } };`,
        context
    );
    const loading = context.api.initialLoad();
    await new Promise(resolve => setTimeout(resolve, 10));
    const whileHeld = events.slice();
    if (scenario === 'superseded') context.api.bump();
    release();
    await loading;
    return { whileHeld, after: events.slice() };
}

(async () => {
    const out = {};
    out.held = await run('held');
    out.superseded = await run('superseded');
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipIf(NODE is None, "Node.js is required for the background-split suite")
class BackgroundSplitModuleTestCase(unittest.TestCase):
    """The pure reading and the sequence, against a page that records what it
    is asked to do."""

    @classmethod
    def setUpClass(cls):
        cls.out = _run_node_file(
            f"const MODULE_PATH = {json.dumps(str(BACKGROUND_SPLIT_JS))};\n" + MODULE_HARNESS
        )

    # ── the reading ──

    def test_a_pane_is_measured_after_being_halved_the_way_the_live_reading_does(self):
        capacity = self.out["capacity"]

        # A side-by-side half keeps the rows and half the columns.
        self.assertEqual(capacity["roomyVertical"], {"cols": 49, "rows": 33})
        # A stacked half keeps the columns and half the height less its header.
        self.assertEqual(capacity["roomyHorizontal"], {"cols": 99, "rows": 15})
        self.assertEqual(capacity["tinyVertical"], {"cols": 6, "rows": 3})
        self.assertEqual(capacity["tinyHorizontal"], {"cols": 12, "rows": 0})
        # Nothing measurable reads as no room, never as a crash.
        self.assertEqual(capacity["unusable"], {"cols": 0, "rows": 0})

    def test_each_rule_refuses_in_the_order_the_button_tests_them(self):
        blockers = self.out["blockers"]

        self.assertEqual(blockers["free"], {"vertical": "", "horizontal": ""})
        self.assertEqual(blockers["narrow"], {"vertical": "window", "horizontal": "window"})
        self.assertEqual(blockers["atCap"], {"vertical": "window", "horizontal": "window"})
        self.assertEqual(blockers["oneLineWide"]["vertical"], "grid")
        self.assertEqual(blockers["oneLineWide"]["horizontal"], "")
        self.assertEqual(blockers["oneLineTall"]["horizontal"], "grid")
        self.assertEqual(blockers["tiny"], {"vertical": "size", "horizontal": "size"})
        # A pane with no line left has no halves to measure.
        self.assertEqual(blockers["gridBeforeSize"], {"vertical": "grid", "horizontal": "grid"})

    def test_the_tab_after_a_split_has_the_new_pane_beside_the_one_it_came_from(self):
        arranged = self.out["arranged"]

        self.assertEqual(arranged["ids"], ["pane-a", "pane-b", "pane-new", "pane-c"])
        self.assertEqual(arranged["index"], 2)
        self.assertEqual(
            [(rect["x"], rect["w"]) for rect in arranged["rects"]],
            [(1, 8), (9, 4), (13, 4), (17, 8)],
        )
        # The plan's weights replace the split axis, and only that axis.
        self.assertEqual(arranged["columnWeights"], [7, 7])
        self.assertEqual(arranged["rowWeights"], [1])

    def test_a_stacked_split_leaves_the_column_weights_alone(self):
        stacked = self.out["arrangedStacked"]

        self.assertEqual(stacked["columnWeights"], [1])
        self.assertEqual(stacked["rowWeights"], [1])
        self.assertEqual([rect["h"] for rect in stacked["rects"]], [4, 4])

    # ── the sequence ──

    def test_a_split_is_read_measured_created_saved_and_taken_into_the_tab_strip(self):
        happy = self.out["happy"]

        self.assertTrue(happy["answer"]["ok"])
        self.assertEqual(happy["answer"]["session"]["session_id"], "pane-new")
        self.assertEqual(happy["answer"]["index"], 2)
        self.assertEqual(happy["answer"]["note"], "")
        # Whether the tab was opened meanwhile is asked with nothing between it
        # and the request; the cache is gone before the write can re-describe it.
        self.assertEqual(
            happy["log"],
            [
                "settle", "readModel", "measure", "isShown", "plan",
                "split", "discard", "saveLayout", "adopt",
            ],
        )

    def test_the_request_the_server_built_is_forwarded_unchanged(self):
        request = self.out["happy"]["requests"][0]

        self.assertEqual(
            request,
            {"id": "pane-b", "axis": "vertical", "request": {"kind": "agent", "agent": "codex"}},
        )

    def test_the_layout_is_written_against_the_revision_the_split_returned(self):
        save = self.out["happy"]["saves"][0]

        self.assertEqual(save["expectedRevision"], 7)
        self.assertEqual(save["ids"], ["pane-a", "pane-b", "pane-new"])
        self.assertEqual(len(save["rects"]), 3)
        self.assertEqual(save["baseCount"], 2)
        self.assertEqual(save["columnWeights"], [2] * 16)
        adopted = self.out["happy"]["adopted"][0]
        self.assertEqual(adopted["saved"]["revision"], 8)
        self.assertEqual(adopted["saved"]["ids"], ["pane-a", "pane-b", "pane-new"])

    def test_a_stacked_split_rewrites_the_row_weights(self):
        save = self.out["stacked"]["saves"][0]

        self.assertEqual(save["rowWeights"], [3] * 8)
        self.assertEqual(save["columnWeights"], [1] * 16)

    def test_a_pane_is_held_only_when_the_window_holds_it_in_another_tab(self):
        self.assertEqual(self.out["held"], {"yes": True, "no": False})
        self.assertEqual(self.out["candidatesRoomy"], ["vertical", "horizontal"])
        self.assertIsNone(self.out["candidatesElsewhere"])

    def test_a_pane_too_small_is_refused_before_anything_is_created(self):
        self.assertEqual(self.out["candidatesTiny"], [])
        self.assertEqual(self.out["reasonTiny"], "reason:vertical:size:2")
        refused = self.out["refusedTiny"]
        self.assertFalse(refused["answer"]["ok"])
        self.assertEqual(refused["answer"]["refusal"]["axis"], "vertical")
        self.assertEqual(refused["answer"]["refusal"]["candidates"], [])
        self.assertNotIn("split", refused["log"])
        self.assertNotIn("saveLayout", refused["log"])

    def test_a_refusal_names_the_rule_and_the_axis_that_would_have_worked(self):
        wide = self.out["oneLineWide"]

        self.assertEqual(wide["candidates"], ["horizontal"])
        self.assertEqual(wide["reason"], "reason:vertical:grid:2")

    def test_the_pane_cap_counts_the_tabs_own_panes(self):
        capped = self.out["atCap"]

        self.assertEqual(capped["candidates"], [])
        self.assertEqual(capped["reason"], "reason:horizontal:window:2")

    def test_a_window_with_no_grid_to_measure_refuses_rather_than_guesses(self):
        gone = self.out["unmeasurable"]

        self.assertEqual(gone["candidates"], [])
        self.assertEqual(gone["reason"], "unmeasurable")
        self.assertEqual(gone["answer"]["refusal"]["reason"], "unmeasurable")
        self.assertNotIn("split", gone["log"])

    def test_a_tab_opened_meanwhile_is_split_by_the_handler_that_owns_its_grid(self):
        opened = self.out["openedMeanwhile"]

        self.assertTrue(opened["answer"]["viaVisibleHandler"])
        self.assertNotIn("split", opened["log"])
        self.assertNotIn("saveLayout", opened["log"])

    def test_a_tab_picked_while_its_split_is_in_flight_loads_after_the_write(self):
        """The finding this guards: the tab opened mid-request used to load the
        new pane from the server before its place was saved, and kept showing
        the default arrangement while the tool reported a split."""
        opened = self.out["openedDuringRequest"]

        self.assertTrue(opened["answer"]["ok"])
        self.assertTrue(opened["heldDuring"])
        self.assertEqual(
            opened["log"][-5:], ["split", "discard", "saveLayout", "adopt", "load"]
        )
        # Only the tab being split is held; any other tab loads at once.
        self.assertTrue(opened["otherTabFree"])
        self.assertTrue(opened["releasedAfter"])

    def test_a_tab_is_never_left_held(self):
        """Every way out of the sequence lets the tab load: a refused create,
        a failed write, a request that threw."""
        self.assertTrue(self.out["idle"])
        self.assertTrue(self.out["createFailed"]["released"])
        self.assertTrue(self.out["saveThrew"]["released"])
        self.assertEqual(self.out["splitThrew"]["thrown"], "network down")
        self.assertTrue(self.out["splitThrew"]["released"])

    def test_a_split_the_server_refused_changes_nothing_here(self):
        failed = self.out["createFailed"]

        self.assertFalse(failed["answer"]["ok"])
        self.assertIn("status 400", failed["answer"]["error"])
        for step in ("discard", "saveLayout", "adopt"):
            self.assertNotIn(step, failed["log"])

    def test_a_pane_that_exists_is_reported_even_when_its_layout_could_not_be_saved(self):
        for name in ("saveFailed", "saveThrew"):
            with self.subTest(case=name):
                case = self.out[name]
                self.assertTrue(case["answer"]["ok"])
                self.assertEqual(case["answer"]["session"]["session_id"], "pane-new")
                self.assertIn("could not be saved", case["answer"]["note"])
                # The tab is still dropped and its record still taken, so it
                # is rebuilt from the server with its default arrangement.
                self.assertIn("discard", case["log"])
                self.assertIsNone(case["adopted"][0]["saved"])
        self.assertIn("error:write failed", self.out["saveThrew"]["log"])

    def test_a_split_that_returned_no_revision_is_not_written_blind(self):
        case = self.out["noRevision"]

        self.assertTrue(case["answer"]["ok"])
        self.assertEqual(case["saves"], [])
        self.assertIn("could not be saved", case["answer"]["note"])

    def test_a_queue_that_would_not_settle_is_reported_and_not_fatal(self):
        case = self.out["settleThrew"]

        self.assertTrue(case["answer"]["ok"])
        self.assertIn("error:queue failed", case["log"])

    def test_a_tab_that_is_gone_or_no_longer_holds_the_pane_is_not_split(self):
        for name in ("gone", "leftItsTab"):
            with self.subTest(case=name):
                case = self.out[name]
                self.assertFalse(case["answer"]["ok"])
                self.assertIn("Nothing changed", case["answer"]["error"])
                self.assertNotIn("split", case["log"])

    def test_a_list_that_could_not_be_read_is_an_error_not_a_guess(self):
        case = self.out["readThrew"]

        self.assertEqual(case["thrown"], "list failed")
        self.assertNotIn("split", case["log"])


@unittest.skipIf(NODE is None, "Node.js is required for the background-split suite")
class BackgroundSplitPageAdapterTestCase(unittest.TestCase):
    """The page's half, run from `terminals.js`'s own source.

    The restore path's real functions, lifted out by name -- the same approach
    `tests/test_split_geometry.py` takes -- so a tab read back here is read by
    the code that rebuilds it when it is shown."""

    @classmethod
    def setUpClass(cls):
        terminals = TERMINALS_JS.read_text(encoding="utf-8")
        adapter = "\n\n".join(_function_source(terminals, name) for name in ADAPTER_SOURCE_NAMES)
        script = "\n".join(
            [
                SPLIT_GEOMETRY_JS.read_text(encoding="utf-8"),
                RESTORE_SOURCE,
                RESTORE_STUBS,
                f"const BACKGROUND_SPLIT_PATH = {json.dumps(str(BACKGROUND_SPLIT_JS))};",
                adapter,
                ADAPTER_STUBS,
                ADAPTER_BODY,
                "",
            ]
        )
        cls.out = _run_node_file(script)

    def test_a_pane_is_held_in_another_tab_by_the_window_group_list(self):
        holding = self.out["holding"]

        self.assertEqual(holding["background"], "g-2")
        # On screen, unknown to this window and empty are all "nothing to split here".
        self.assertEqual(holding["onScreen"], "")
        self.assertEqual(holding["unknown"], "")
        self.assertEqual(holding["empty"], "")

    def test_a_window_that_has_not_settled_on_one_tab_claims_nothing(self):
        """Mid-load `sessionIds` still names the tab being left, so the window
        cannot say what the others hold. A later poll asks again."""
        self.assertEqual(
            self.out["unsettled"], {"switching": "", "unbuilt": "", "noneVisible": ""}
        )

    def test_a_tab_read_from_the_server_keeps_its_stored_arrangement(self):
        stored = self.out["stored"]

        self.assertEqual(stored["ids"], ["pane-a", "pane-b"])
        self.assertEqual(
            stored["rects"],
            [{"x": 1, "y": 1, "w": 8, "h": 16}, {"x": 9, "y": 1, "w": 8, "h": 8}],
        )
        self.assertEqual(stored["firstColumnWeight"], 1.5)
        self.assertEqual(stored["baseCount"], 2)

    def test_a_tab_with_no_stored_arrangement_wears_its_preset(self):
        preset = self.out["preset"]

        # Two panes, stacked: one column wide, two cells tall, at this build's unit.
        self.assertEqual(
            preset["rects"],
            [{"x": 1, "y": 1, "w": 16, "h": 8}, {"x": 1, "y": 9, "w": 16, "h": 8}],
        )
        self.assertEqual((preset["columns"], preset["rows"]), (16, 16))
        self.assertEqual(preset["baseCount"], 2)

    def test_an_arrangement_saved_at_a_coarser_grid_is_read_at_this_resolution(self):
        coarse = self.out["coarse"]

        self.assertEqual(coarse["box"], {"columns": 24, "rows": 16})
        self.assertEqual(coarse["first"], {"x": 1, "y": 1, "w": 8, "h": 8})
        self.assertEqual(coarse["baseCount"], 6)

    def test_the_cached_view_is_the_picture_while_it_holds_exactly_the_servers_panes(self):
        cached = self.out["cached"]

        # Visual order is the order of the cards, not of creation.
        self.assertEqual(cached["ids"], ["pane-c", "pane-a", "pane-b"])
        self.assertEqual(len(cached["rects"]), 3)
        self.assertEqual(cached["weight"], 1.25)
        self.assertEqual(cached["baseCount"], 3)

    def test_a_cached_view_of_other_panes_is_not_trusted(self):
        stale = self.out["staleCache"]

        self.assertEqual(stale["ids"], ["pane-a", "pane-b", "pane-c", "pane-d"])
        self.assertEqual(stale["rects"], 4)
        self.assertEqual(stale["weight"], 1)

    def test_a_tab_the_server_no_longer_lists_is_not_a_model(self):
        self.assertIsNone(self.out["missing"])
        self.assertIn("server down", self.out["listFailure"])

    def test_the_shared_grid_gives_each_rectangle_the_pixels_it_would_span(self):
        measured = self.out["measured"]

        # (1216 - 16 padding - 15 gaps of 8) / 16 tracks = 67.5px; eight tracks
        # and the seven gaps inside them.
        self.assertEqual(measured["widths"], [596.0, 596.0])
        self.assertFalse(measured["narrow"])
        # Nothing on screen to read a font off: the defaults the live reading uses.
        self.assertEqual(measured["cell"], {"width": 8, "height": 17})
        self.assertEqual(measured["headerHeight"], 34)

    def test_the_cell_and_header_are_read_off_a_live_terminal_when_there_is_one(self):
        self.assertEqual(
            self.out["liveCell"], {"cell": {"width": 9, "height": 20}, "headerHeight": 40}
        )

    def test_a_narrow_window_says_so(self):
        self.assertTrue(self.out["narrowWindow"])

    def test_a_grid_with_no_size_is_not_measured(self):
        self.assertIsNone(self.out["collapsed"])

    def test_the_tab_record_is_taken_with_its_new_arrangement_and_revision(self):
        adopted = self.out["adopted"]
        record = adopted["record"]

        self.assertEqual(record["pane_order"], ["pane-a", "pane-new"])
        self.assertEqual(record["presentation_revision"], 8)
        self.assertEqual(len(record["workspace_layout"]["split_slot_rects"]), 2)
        self.assertEqual(record["workspace_layout"]["class_name"], "layout-split-local")
        self.assertEqual(adopted["revisions"], [["g-2", 8]])
        self.assertEqual(adopted["tabsRendered"], 1)

    def test_a_tab_whose_layout_was_not_saved_is_taken_as_the_server_described_it(self):
        unsaved = self.out["adoptedUnsaved"]

        self.assertEqual(unsaved["order"], ["pane-a", "pane-new"])
        self.assertIsNone(unsaved["layout"])
        self.assertEqual(unsaved["revision"], 7)
        # No revision the page did not write is claimed.
        self.assertEqual(unsaved["revisions"], [])

    def test_every_tab_but_the_painted_one_loses_its_cached_view(self):
        # The background tab, then the same tab again once it was picked while
        # the split was out -- its load is held and has not read the cache yet.
        # The painted tab keeps its live view.
        self.assertEqual(self.out["dropped"], ["g-2", "g-2"])

    def test_the_pure_reading_gives_the_page_reading_case_for_case(self):
        """One rule, two measurement sources. Every combination of window width,
        pane count, rectangle and surface, against the page's own
        `getSplitBlockers`."""
        parity = self.out["parity"]

        self.assertEqual(parity["total"], 100)
        self.assertEqual(parity["different"], [])
        self.assertEqual(parity["codes"], ["window", "grid", "size"])


@unittest.skipIf(NODE is None, "Node.js is required for the background-split suite")
class BackgroundSplitBridgeTestCase(unittest.TestCase):
    """The split bridge's routing: a pane on screen is the visible handler's,
    a pane in another tab is the background sequence's."""

    @classmethod
    def setUpClass(cls):
        bridge = _between(
            TERMINALS_JS.read_text(encoding="utf-8"),
            "    const splitBridge = {",
            "    window.GridVibeSplitBridge = splitBridge;",
        )
        cls.out = _run_node_file(
            "const vm = require('vm');\n"
            f"const BRIDGE_SOURCE = {json.dumps(bridge)};\n" + BRIDGE_HARNESS
        )

    def test_the_bridge_owns_a_pane_showing_or_held_in_another_tab(self):
        self.assertEqual(
            self.out["owns"], {"visible": True, "background": True, "unknown": False}
        )

    def test_without_the_module_only_the_showing_tab_is_claimed(self):
        """Nothing to split a tab with means nothing to promise for one."""
        self.assertEqual(self.out["ownsWithoutModule"], {"visible": True, "background": False})
        self.assertFalse(self.out["performWithoutModule"]["ok"])

    def test_the_answer_for_a_pane_in_another_tab_is_the_background_sequences(self):
        self.assertEqual(self.out["candidates"]["visible"], ["vertical", "horizontal"])
        self.assertEqual(self.out["candidates"]["background"], ["vertical"])
        self.assertIsNone(self.out["candidates"]["unknown"])
        self.assertEqual(self.out["reasons"]["visible"], "visible:horizontal:size")
        self.assertEqual(self.out["reasons"]["background"], "why")

    def test_a_pane_is_split_by_the_handler_that_owns_its_grid(self):
        performed = self.out["performed"]

        self.assertTrue(performed["visible"]["viaVisible"])
        self.assertTrue(performed["background"]["viaBackground"])
        self.assertFalse(performed["unknown"]["ok"])
        # The request the server built travels to whichever handler runs.
        self.assertIn(["perform", "pane-c", "vertical", {"kind": "agent"}], self.out["calls"])
        self.assertIn(["visible", 0, "vertical", {"kind": "agent"}], self.out["calls"])


@unittest.skipIf(NODE is None, "Node.js is required for the background-split suite")
class SplitInFlightTestCase(unittest.TestCase):
    """A split's request is in flight while the window can move on.

    The pane is created on the server whatever the window does next. What the
    page must never do is paint it into whichever group is showing when the
    answer lands. Executed from the real `splitTerminalPane`.
    """

    @classmethod
    def setUpClass(cls):
        source = TERMINALS_JS.read_text(encoding="utf-8")
        split = _between(
            source,
            "    function captureSplitSource(index) {",
            "    /* The pane's slot and rectangle in this window",
        )
        cls.out = _run_node_file(
            "const vm = require('vm');\n"
            f"const SPLIT_SOURCE = {json.dumps(split)};\n" + SPLIT_IN_FLIGHT_HARNESS
        )

    def test_a_split_nothing_interrupted_is_painted_where_it_was_asked_for(self):
        steady = self.out["steady"]

        self.assertTrue(steady["ok"])
        self.assertEqual(steady["index"], 1)
        self.assertEqual(steady["showingSessionIds"], ["pane-a", "pane-new"])
        self.assertIn("painted", steady["events"])

    def test_a_tab_picked_while_the_request_was_out_gets_no_pane_painted_into_it(self):
        moved = self.out["switchedAway"]

        # Made on the server, so it is a split -- and it is not in the tab now
        # showing, whose own arrays are exactly as they were.
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["sessionId"], "pane-new")
        self.assertIsNone(moved["index"])
        self.assertEqual(moved["showingSessionIds"], ["pane-other"])
        self.assertEqual(moved["showingTerminals"], 1)
        self.assertNotIn("painted", moved["events"])
        self.assertNotIn("presentation-noted", moved["events"])
        # The tab it went into is still told about it, so the next intent for
        # that pane finds its tab and a return to the tab rebuilds with it.
        self.assertEqual(moved["groupPanes"], ["pane-a", "pane-new"])
        self.assertIn("tabs-rendered", moved["events"])

    def test_a_tab_picked_and_picked_back_still_takes_the_pane(self):
        """The same arrays are on screen again, so nothing was misplaced."""
        back = self.out["switchedBack"]

        self.assertTrue(back["ok"])
        self.assertEqual(back["index"], 1)
        self.assertEqual(back["originalSessionIds"], ["pane-a", "pane-new"])
        self.assertIn("painted", back["events"])

    def test_a_grid_rebuilt_in_place_gets_no_pane_painted_into_it(self):
        rebuilt = self.out["rebuilt"]

        self.assertTrue(rebuilt["ok"])
        self.assertIsNone(rebuilt["index"])
        self.assertEqual(rebuilt["showingSessionIds"], ["pane-a"])
        self.assertNotIn("painted", rebuilt["events"])


@unittest.skipIf(NODE is None, "Node.js is required for the background-split suite")
class LoadWaitsForBackgroundSplitTestCase(unittest.TestCase):
    """A tab being split from behind is loaded only once the split has written
    its arrangement. Executed from the real `initialLoad`."""

    @classmethod
    def setUpClass(cls):
        source = TERMINALS_JS.read_text(encoding="utf-8")
        load = "\n\n".join(
            _function_source(source, name) for name in ("backgroundSplitSettled", "initialLoad")
        )
        cls.out = _run_node_file(
            "const vm = require('vm');\n"
            f"const LOAD_SOURCE = {json.dumps(load)};\n" + LOAD_GATE_HARNESS
        )

    def test_nothing_is_read_for_the_tab_until_its_split_has_settled(self):
        held = self.out["held"]

        # Not even the group list: a list read ahead of the layout write would
        # hand the tab strip the arrangement being replaced.
        self.assertEqual(held["whileHeld"], ["settled:g-2"])
        self.assertEqual(
            held["after"],
            [
                "settled:g-2", "groups", "settled:g-2",
                "fetch:/api/sessions?group_id=g-2", "reset",
            ],
        )

    def test_a_load_superseded_while_it_waited_reads_nothing(self):
        superseded = self.out["superseded"]

        self.assertEqual(superseded["after"], ["settled:g-2"])


class BackgroundSplitWiringTestCase(unittest.TestCase):
    def test_the_workspace_page_loads_the_module_before_terminals(self):
        """Wiring, not behaviour: `terminals.js` builds its adapter from the
        module at load, so the tag has to be there and has to come first."""
        markup = TERMINALS_HTML.read_text(encoding="utf-8")
        scripts = re.findall(r"filename='js/([a-z0-9\-]+\.js)'", markup)

        self.assertIn("background-split.js", scripts)
        self.assertLess(
            scripts.index("background-split.js"), scripts.index("terminals.js")
        )

    def test_no_tab_is_switched_to_split_a_pane_in_it(self):
        """The whole point, stated against the source: this path must never
        reach for the tab strip's own switch, the focus bridge, or a landing on
        a pane. A source assertion because there is nothing to execute -- the
        property is an absence."""
        module = BACKGROUND_SPLIT_JS.read_text(encoding="utf-8")
        adapter = _between(
            TERMINALS_JS.read_text(encoding="utf-8"),
            "    async function readBackgroundGroupModel(groupId) {",
            "    const splitBridge = {",
        )

        for forbidden in ("switchGroup", "focusPaneForArrival", "initialLoad", "restoreCachedGroupView"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, module)
                self.assertNotIn(forbidden, adapter)


if __name__ == "__main__":
    unittest.main()
