"""Run the page's actual resize bridge with measured grid and persistence stubs."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "Node.js is required")
class ResizeBridgeTestCase(unittest.TestCase):
    def test_page_persists_before_painting_and_refuses_without_mutation(self):
        source = (ROOT / "web/static/js/terminals.js").read_text(encoding="utf-8")
        start = source.index("    const resizeBridge = {")
        end = source.index("    window.GridVibeResizeBridge = resizeBridge;", start)
        bridge = source[start:end] + "\nglobalThis.bridge = resizeBridge;\n"
        script = r"""
const vm = require('vm');
const geometry = require(process.argv[1]);
const bridgeSource = process.argv[2];
const results = {};
async function run(kind) {
    const events = [];
    let closeEpoch = 0;
    const rects = [{x:1,y:1,w:1,h:1}, {x:2,y:1,w:1,h:1}];
    const cards = [{dataset:{slot:'0'}}, {dataset:{slot:'1'}}];
    const grid = {children: cards, className:'layout-2-vertical'};
    const group = {group_id:'g', presentation_revision:1};
    const context = {
        window: {innerWidth: kind === 'narrow' ? 600 : 1200,
                 GridVibeSplitGeometry: geometry},
        document: {getElementById: () => grid},
        currentWorkspaceId:'ws', sessionGroups:[group],
        activeGroupId:'g', visibleGroupId:'g', gridBuilt:true,
        resizeIntentInFlight:false, activeGridResize:null,
        backgroundResize:null,
        closeGeometryCoordinator: {epoch: () => closeEpoch},
        splitColumnWeights:[1,1], splitRowWeights:[1],
        terminals:[{},{}], sessionIds:['p1','p2'],
        originalSplitSlotCount:2,
        presentationController: () => ({
            settleGroup: async () => {
                events.push('settle');
                if (kind === 'closeBeforeWrite') closeEpoch += 1;
            },
            captureGroup: () => ({pane_order:['p1','p2'], panes:[{session_id:'p1'},{session_id:'p2'}]}),
            setGroupRevision: (_id, revision) => {events.push('revision'); group.presentation_revision=revision;}
        }),
        fetch: async () => ({ok:true,json:async () => ({
            presentation_revision: kind === 'stale' ? 2 : 1,
            panes:[{session_id:'p1'},{session_id:'p2'}],
            geometry:{split_slot_rects:rects,column_weights:[1,1],row_weights:[1]}
        })}),
        ensureSplitSlotRects: () => rects,
        getSplitGridSize: () => ({columns:2,rows:1}),
        getSharedGridEdgeSegments: () => [{start:1,end:2}],
        normalizeSplitTrackWeights: (weights) => weights.slice(),
        getResizableGridMetrics: (_grid, columns, rows) => ({
            gridContentWidth:1200,gridContentHeight:600,
            columnGap:0,rowGap:0,
            columnSizes:columns.map(w => 1200*w/columns.reduce((a,b)=>a+b)),
            rowSizes:rows.map(w => 600*w/rows.reduce((a,b)=>a+b))
        }),
        getResizeTrackGroups: () => ({before:[0],after:[1]}),
        validateResizeCandidate: () => kind !== 'minimum',
        buildWorkspaceLayoutSnapshotFromState: (_count,_class,rs,cols,rows) => ({
            split_slot_rects:rs,split_column_weights:cols,split_row_weights:rows
        }),
        postPresentation: async (_url,payload) => {
            events.push('post');
            if (kind === 'postThrow') throw new Error('connection lost');
            if (kind === 'closeAfterWrite') closeEpoch += 1;
            return {ok:kind !== 'persist',json:async () => kind === 'persist'
                ? {error:'Save failed'} : kind === 'badResponse'
                ? Promise.reject(new Error('invalid JSON')) : kind === 'missingRevision'
                ? {} : {presentation_revision:2}};
        },
        applySplitSlotGeometry: () => {events.push('paint');},
        redrawAttachedTerminals: () => {events.push('redraw');},
        affectedResizeIndices: () => [0,1],
        getGroupById: () => group,
        console
    };
    vm.runInNewContext(bridgeSource, context);
    const answer = await context.bridge.perform({
        workspace_id:'ws',group_id:'g',axis:'vertical',line_index:1,
        position:0.6,expected_revision:1
    });
    return {answer,events,revision:group.presentation_revision};
}
(async () => {
    for (const kind of [
        'ok','stale','narrow','minimum','persist','postThrow','badResponse',
        'missingRevision','closeBeforeWrite','closeAfterWrite'
    ]) {
        results[kind] = await run(kind);
    }
    console.log(JSON.stringify(results));
})().catch(error => {console.error(error); process.exitCode=1;});
"""
        result = subprocess.run(
            [NODE, "-e", script,
             str(ROOT / "web/static/js/split-geometry.js"), bridge],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        cases = json.loads(result.stdout)
        self.assertTrue(cases["ok"]["answer"]["ok"])
        self.assertEqual(cases["ok"]["events"], ["settle", "post", "paint", "redraw", "revision"])
        self.assertEqual(cases["ok"]["answer"]["result"]["column_weights"], [1.2, 0.8])
        for kind in ("stale", "narrow", "minimum", "persist"):
            self.assertFalse(cases[kind]["answer"]["ok"], kind)
            self.assertNotIn("paint", cases[kind]["events"])
            self.assertEqual(cases[kind]["revision"], 1)
        for kind in ("postThrow", "badResponse", "missingRevision"):
            self.assertFalse(cases[kind]["answer"]["ok"])
            self.assertTrue(cases[kind]["answer"]["unknown"])
            self.assertIn("outcome is unknown", cases[kind]["answer"]["error"])
            self.assertNotIn("Nothing changed", cases[kind]["answer"]["error"])
            self.assertNotIn("paint", cases[kind]["events"])
        self.assertFalse(cases["closeBeforeWrite"]["answer"]["ok"])
        self.assertEqual(cases["closeBeforeWrite"]["events"], ["settle"])
        self.assertIn("Nothing changed", cases["closeBeforeWrite"]["answer"]["error"])
        self.assertFalse(cases["closeAfterWrite"]["answer"]["ok"])
        self.assertTrue(cases["closeAfterWrite"]["answer"]["unknown"])
        self.assertEqual(cases["closeAfterWrite"]["events"], ["settle", "post"])
        self.assertIn("saved layout may still hold them", cases["closeAfterWrite"]["answer"]["error"])
