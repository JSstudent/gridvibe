"""Close reflow is one DOM-free policy shared by X and agent closes."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
CLOSE_GEOMETRY_JS = REPO_ROOT / "web" / "static" / "js" / "close-geometry.js"
TERMINALS_JS = REPO_ROOT / "web" / "static" / "js" / "terminals.js"
TERMINALS_HTML = REPO_ROOT / "templates" / "terminals.html"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for the close geometry tests")
class CloseGeometryTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = f"const geometry = require({json.dumps(str(CLOSE_GEOMETRY_JS))});\n{body}\n"
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

    def test_longest_border_wins_and_ties_use_first_visual_index(self):
        result = self._run_node(
            r"""
            const model = {
                entries: [
                    { sessionId: 'a', visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 8 } },
                    { sessionId: 'b', visualIndex: 1, rect: { x: 9, y: 1, w: 8, h: 8 } },
                    { sessionId: 'c', visualIndex: 2, rect: { x: 1, y: 9, w: 8, h: 8 } },
                    { sessionId: 'd', visualIndex: 3, rect: { x: 9, y: 9, w: 8, h: 8 } }
                ],
                originalSplitSlotCount: 4
            };
            const reduced = geometry.reduceCloseGeometry(model, ['d']);
            const byId = Object.fromEntries(reduced.model.entries.map(entry => [entry.sessionId, entry.rect]));
            const longestModel = { entries: [
                { sessionId: 'left-top', visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 12 } },
                { sessionId: 'above', visualIndex: 1, rect: { x: 9, y: 1, w: 8, h: 8 } },
                { sessionId: 'left-bottom', visualIndex: 2, rect: { x: 1, y: 13, w: 8, h: 4 } },
                { sessionId: 'closed', visualIndex: 3, rect: { x: 9, y: 9, w: 8, h: 8 } }
            ] };
            const longest = geometry.reduceCloseGeometry(longestModel, ['closed']);
            const longestById = Object.fromEntries(
                longest.model.entries.map(entry => [entry.sessionId, entry.rect])
            );
            process.stdout.write(JSON.stringify({
                ok: reduced.ok,
                upper: byId.b,
                left: byId.c,
                longest: longestById.above,
                borders: [
                    geometry.sharedBorderLength(model.entries[3].rect, model.entries[1].rect),
                    geometry.sharedBorderLength(model.entries[3].rect, model.entries[2].rect)
                ]
            }));
            """
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["borders"], [8, 8])
        self.assertEqual(result["upper"], {"x": 9, "y": 1, "w": 8, "h": 16, "ancestors": []})
        self.assertEqual(result["left"]["h"], 8)
        self.assertEqual(result["longest"]["h"], 16)

    def test_full_side_group_fallback_expands_every_contact_without_overlap(self):
        result = self._run_node(
            r"""
            const model = { entries: [
                { sessionId: 'top', visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 8 } },
                { sessionId: 'bottom', visualIndex: 1, rect: { x: 1, y: 9, w: 8, h: 8 } },
                { sessionId: 'tall', visualIndex: 2, rect: { x: 9, y: 1, w: 8, h: 16 } }
            ], originalSplitSlotCount: 3 };
            const reduced = geometry.reduceCloseGeometry(model, ['tall']);
            const rects = reduced.model.entries.map(entry => entry.rect);
            process.stdout.write(JSON.stringify({
                ok: reduced.ok,
                rects,
                overlap: geometry.splitRectsOverlap(rects[0], rects[1]),
                area: rects.reduce((sum, rect) => sum + geometry.splitRectArea(rect), 0)
            }));
            """
        )
        self.assertTrue(result["ok"])
        self.assertEqual([rect["w"] for rect in result["rects"]], [16, 16])
        self.assertFalse(result["overlap"])
        self.assertEqual(result["area"], 16 * 16)

    def test_common_case_expands_one_contact_and_never_moves_other_survivors(self):
        result = self._run_node(
            r"""
            const model = { entries: [
                { sessionId: 'a', visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 8 } },
                { sessionId: 'b', visualIndex: 1, rect: { x: 9, y: 1, w: 8, h: 8 } },
                { sessionId: 'c', visualIndex: 2, rect: { x: 1, y: 9, w: 8, h: 8 } },
                { sessionId: 'd', visualIndex: 3, rect: { x: 9, y: 9, w: 8, h: 8 } }
            ] };
            const reduced = geometry.reduceCloseGeometry(model, ['d']);
            const before = Object.fromEntries(model.entries.map(entry => [entry.sessionId, entry.rect]));
            const changed = reduced.model.entries
                .filter(entry => JSON.stringify(entry.rect) !== JSON.stringify(before[entry.sessionId]))
                .map(entry => entry.sessionId);
            process.stdout.write(JSON.stringify(changed));
            """
        )
        self.assertEqual(result, ["b"])

    def test_sequential_closes_compose_and_repeated_ids_are_idempotent(self):
        result = self._run_node(
            r"""
            const model = { entries: [
                { sessionId: 'a', visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 8 } },
                { sessionId: 'b', visualIndex: 1, rect: { x: 9, y: 1, w: 8, h: 8 } },
                { sessionId: 'c', visualIndex: 2, rect: { x: 1, y: 9, w: 8, h: 8 } },
                { sessionId: 'd', visualIndex: 3, rect: { x: 9, y: 9, w: 8, h: 8 } }
            ] };
            const once = geometry.reduceCloseGeometry(model, ['d', 'c']);
            const repeated = geometry.reduceCloseGeometry(model, ['d', 'd', 'c']);
            process.stdout.write(JSON.stringify({
                once: once.model,
                repeated: repeated.model,
                ignored: repeated.ignoredClosedSessionIds
            }));
            """
        )
        self.assertEqual(result["once"], result["repeated"])
        self.assertEqual(result["ignored"], ["d"])
        self.assertEqual(
            {entry["sessionId"] for entry in result["once"]["entries"]}, {"a", "b"}
        )

    def test_last_pane_has_no_restore(self):
        result = self._run_node(
            r"""
            const reduced = geometry.reduceCloseGeometry({ entries: [
                { sessionId: 'only', visualIndex: 0, rect: { x: 1, y: 1, w: 16, h: 8 } }
            ] }, ['only']);
            const coordinator = geometry.createCoordinator();
            const staged = coordinator.stage({
                groupId: 'g',
                snapshot: { entries: [
                    { sessionId: 'only', visualIndex: 0, rect: { x: 1, y: 1, w: 16, h: 8 } }
                ] },
                closedSessionIds: ['only']
            });
            process.stdout.write(JSON.stringify({
                status: reduced.status,
                count: reduced.model.entries.length,
                record: staged.record,
                pending: coordinator.pendingGroups()
            }));
            """
        )
        self.assertEqual(result, {
            "status": "last-pane", "count": 0, "record": None, "pending": []
        })

    def test_missing_close_target_is_not_treated_as_applied(self):
        result = self._run_node(
            r"""
            const reduced = geometry.reduceCloseGeometry({ entries: [
                { sessionId: 'survivor', visualIndex: 0,
                  rect: { x: 1, y: 1, w: 16, h: 8 } }
            ] }, ['missing']);
            process.stdout.write(JSON.stringify({
                ok: reduced.ok,
                ignored: reduced.ignoredClosedSessionIds,
                applied: geometry.closeResultApplied(reduced, 'missing')
            }));
            """
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["ignored"], ["missing"])
        self.assertFalse(result["applied"])

    def test_track_weights_and_bounding_box_are_preserved(self):
        result = self._run_node(
            r"""
            const model = {
                entries: [
                    { sessionId: 'left', visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 8 } },
                    { sessionId: 'right', visualIndex: 1, rect: { x: 9, y: 1, w: 8, h: 8 } }
                ],
                originalSplitSlotCount: 4,
                columnWeights: Array.from({ length: 16 }, (_, i) => i + 1),
                rowWeights: Array.from({ length: 8 }, (_, i) => 8 - i)
            };
            const reduced = geometry.reduceCloseGeometry(model, ['right']);
            const rect = reduced.model.entries[0].rect;
            process.stdout.write(JSON.stringify({
                rect,
                columns: reduced.model.columnWeights,
                rows: reduced.model.rowWeights,
                slots: reduced.model.originalSplitSlotCount
            }));
            """
        )
        self.assertEqual(result["rect"]["w"], 16)
        self.assertEqual(result["rect"]["h"], 8)
        self.assertEqual(result["columns"], list(range(1, 17)))
        self.assertEqual(result["rows"], list(range(8, 0, -1)))
        self.assertEqual(result["slots"], 4)

    def test_reducer_matches_pre_refactor_x_button_fixtures(self):
        result = self._run_node(
            r"""
            const fixtures = [
                { entries: [
                    { sessionId: 'a', rect: { x: 1, y: 1, w: 8, h: 8 } },
                    { sessionId: 'b', rect: { x: 9, y: 1, w: 8, h: 8 } }
                ], close: 'a', expected: {
                    b: { x: 1, y: 1, w: 16, h: 8 }
                } },
                { entries: [
                    { sessionId: 'a', rect: { x: 1, y: 1, w: 16, h: 8 } },
                    { sessionId: 'b', rect: { x: 1, y: 9, w: 16, h: 8 } }
                ], close: 'b', expected: {
                    a: { x: 1, y: 1, w: 16, h: 16 }
                } },
                { entries: [
                    { sessionId: 'a', rect: { x: 1, y: 1, w: 8, h: 8 } },
                    { sessionId: 'b', rect: { x: 9, y: 1, w: 8, h: 8 } },
                    { sessionId: 'c', rect: { x: 1, y: 9, w: 8, h: 8 } },
                    { sessionId: 'd', rect: { x: 9, y: 9, w: 8, h: 8 } }
                ], close: 'a', expected: {
                    b: { x: 1, y: 1, w: 16, h: 8 },
                    c: { x: 1, y: 9, w: 8, h: 8 },
                    d: { x: 9, y: 9, w: 8, h: 8 }
                } },
                { entries: [
                    { sessionId: 'a', rect: { x: 1, y: 1, w: 8, h: 8 } },
                    { sessionId: 'b', rect: { x: 9, y: 1, w: 8, h: 8 } },
                    { sessionId: 'c', rect: { x: 1, y: 9, w: 8, h: 8 } },
                    { sessionId: 'd', rect: { x: 9, y: 9, w: 8, h: 8 } }
                ], close: 'd', expected: {
                    a: { x: 1, y: 1, w: 8, h: 8 },
                    b: { x: 9, y: 1, w: 8, h: 16 },
                    c: { x: 1, y: 9, w: 8, h: 8 }
                } },
                { entries: [
                    { sessionId: 'a', rect: { x: 1, y: 1, w: 16, h: 8 } },
                    { sessionId: 'b', rect: { x: 1, y: 9, w: 16, h: 8 } },
                    { sessionId: 'c', rect: { x: 17, y: 1, w: 8, h: 16 } }
                ], close: 'c', expected: {
                    a: { x: 1, y: 1, w: 24, h: 8 },
                    b: { x: 1, y: 9, w: 24, h: 8 }
                } }
            ];
            const outcomes = fixtures.map(fixture => {
                const snapshot = {
                    entries: fixture.entries.map((entry, visualIndex) => ({ ...entry, visualIndex }))
                };
                const reduced = geometry.reduceCloseGeometry(snapshot, [fixture.close]);
                const actual = Object.fromEntries(reduced.model.entries.map(entry => [
                    entry.sessionId,
                    { x: entry.rect.x, y: entry.rect.y, w: entry.rect.w, h: entry.rect.h }
                ]));
                return { ok: reduced.ok, actual, expected: fixture.expected };
            });
            process.stdout.write(JSON.stringify(outcomes));
            """
        )
        for outcome in result:
            self.assertTrue(outcome["ok"])
            self.assertEqual(outcome["actual"], outcome["expected"])

    def test_group_keyed_staging_is_composable_and_background_safe(self):
        result = self._run_node(
            r"""
            const coordinator = geometry.createCoordinator();
            const row = prefix => ({ entries: [
                { sessionId: `${prefix}1`, visualIndex: 0, rect: { x: 1, y: 1, w: 8, h: 8 } },
                { sessionId: `${prefix}2`, visualIndex: 1, rect: { x: 9, y: 1, w: 8, h: 8 } },
                { sessionId: `${prefix}3`, visualIndex: 2, rect: { x: 17, y: 1, w: 8, h: 8 } }
            ], clientStateBySessionId: {
                [`${prefix}1`]: { marker: `${prefix}1` },
                [`${prefix}2`]: { marker: `${prefix}2` },
                [`${prefix}3`]: { marker: `${prefix}3` }
            } });
            const visible = coordinator.stage({ groupId: 'visible', snapshot: row('v'), closedSessionIds: ['v3'] });
            const visibleBefore = JSON.stringify(coordinator.peek('visible'));
            const background = coordinator.stage({ groupId: 'background', snapshot: row('b'), closedSessionIds: ['b3'] });
            const unchanged = visibleBefore === JSON.stringify(coordinator.peek('visible'));
            const composed = coordinator.stage({ groupId: 'background', snapshot: row('b'), closedSessionIds: ['b2'] });
            const consumed = coordinator.consume('background', composed.generation);
            coordinator.invalidate('visible');
            process.stdout.write(JSON.stringify({
                unchanged,
                visibleGeneration: visible.generation,
                backgroundGeneration: background.generation,
                survivors: consumed.model.entries.map(entry => entry.sessionId),
                clientStateIds: Object.keys(consumed.clientStateBySessionId),
                pending: coordinator.pendingGroups(),
                epoch: coordinator.epoch()
            }));
            """
        )
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["survivors"], ["b1"])
        self.assertEqual(result["clientStateIds"], ["b1"])
        self.assertEqual(result["pending"], [])
        self.assertGreaterEqual(result["epoch"], 4)

    def test_exact_delta_routes_partial_closes_and_invalidates_complete_groups(self):
        result = self._run_node(
            r"""
            const routed = geometry.planCloseDelta({
                closed_session_ids: ['a1', 'b1', 'b2'],
                closed_group_ids: ['b']
            }, sessionId => sessionId[0]);
            process.stdout.write(JSON.stringify(routed));
            """
        )
        self.assertEqual(result["closedGroupIds"], ["b"])
        self.assertEqual(result["groups"], [{"groupId": "a", "closedSessionIds": ["a1"]}])

    def test_page_wiring_loads_reducer_and_stages_before_refresh(self):
        markup = TERMINALS_HTML.read_text(encoding="utf-8")
        script = TERMINALS_JS.read_text(encoding="utf-8")
        self.assertLess(markup.index("close-geometry.js"), markup.index("terminals.js"))
        handler = script[
            script.index("socket.on('session_groups_updated'"):
            script.index("socket.on('voice_prefs_updated'")
        ]
        self.assertLess(
            handler.index("stageSessionCloseDelta(message || {});"),
            handler.index("scheduleStatusRefresh();"),
        )


if __name__ == "__main__":
    unittest.main()
