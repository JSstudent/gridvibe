"""Where each pane sits, and the copy of the frontend's table that says so.

`web/pane_geometry.py` derives the arrangement of a group that has no stored
geometry record, because the page only writes one for a group that has actually
been split locally. That derivation is a second copy of something the frontend
already owns, and this file is what makes the copy safe:

- **The preset table is pinned to the stylesheet.** Every layout class the
  table names is read out of `terminals.css` and its declared tracks compared,
  so moving a pane in CSS fails here rather than lying to an agent.
- **The grid breakpoints are pinned to `getGridMetrics`.** Read out of
  `shared.js`, the three of them, in the order the function tests them.

A source assertion on CSS declarations and named constants is the sanctioned
kind: it is pinning the *duplication*, not a signature.

Everything else is behaviour: the adjacency rules over real rectangles, a
stored record read back as it was written, a bad record dropped whole, and the
route composing all of it for one live group.
"""

import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import api  # noqa: E402
import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web.pane_geometry import (  # noqa: E402
    GRID_BREAKPOINTS,
    LAYOUT_CLASS_TRACKS,
    compose_group_geometry,
    grid_metrics,
    layout_class,
    layout_is_advisory,
    neighbour_indexes,
    preset_geometry,
    read_geometry,
    relative_areas,
)

TERMINALS_CSS = PROJECT_ROOT / "web" / "static" / "css" / "terminals.css"
SHARED_JS = PROJECT_ROOT / "web" / "static" / "js" / "shared.js"


def _declared_tracks(css: str, class_name: str):
    """The grid tracks one layout class declares, straight out of the file."""
    match = re.search(
        r"#terminalsGrid\." + re.escape(class_name) + r"\s*\{(.*?)\}",
        css,
        re.DOTALL,
    )
    if match is None:
        return None
    body = match.group(1)
    columns = re.search(r"grid-template-columns:\s*([^;]+);", body)
    rows = re.search(r"grid-template-rows:\s*([^;]+);", body)
    return (
        columns.group(1).strip() if columns else "",
        rows.group(1).strip() if rows else "",
    )


class PresetTableIsPinnedToTheStylesheetTestCase(unittest.TestCase):
    """The accepted duplication, and the thing that keeps it honest."""

    @classmethod
    def setUpClass(cls):
        cls.css = TERMINALS_CSS.read_text(encoding="utf-8")
        cls.shared = SHARED_JS.read_text(encoding="utf-8")

    def test_every_preset_layout_class_declares_the_tracks_the_table_states(self):
        for class_name, expected in LAYOUT_CLASS_TRACKS.items():
            with self.subTest(layout=class_name):
                self.assertEqual(_declared_tracks(self.css, class_name), expected)

    def test_the_grid_breakpoints_match_get_grid_metrics(self):
        """`getGridMetrics`' three ladders, in the order it tests them."""
        body = self.shared[self.shared.index("function getGridMetrics(count)"):]
        body = body[: body.index("\n    }\n")]
        found = [
            (int(count), int(columns), int(rows))
            for count, columns, rows in re.findall(
                r"count >= (\d+)\D+?columns: (\d+), rows: (\d+)", body, re.DOTALL
            )
        ]

        self.assertEqual(found, list(GRID_BREAKPOINTS))

    def test_every_class_the_table_names_is_a_class_the_page_can_apply(self):
        """The table may only name classes `getLayoutClass` actually produces.

        The two-pane classes are built from a template there, so the assertion
        is on the class *set* rather than on seven literals: every name this
        module derives has to be styled by the stylesheet the page wears.
        """
        for count, layout in (
            (1, "single"),
            (2, "vertical"),
            (2, "horizontal"),
            (3, "vertical"),
            (3, "horizontal"),
            (3, "split"),
            (4, "grid"),
        ):
            with self.subTest(count=count, layout=layout):
                self.assertIn(
                    f"#terminalsGrid.{layout_class(count, layout)}", self.css
                )

    def test_the_layout_class_branches_match_the_pages_own(self):
        """The same decisions `getLayoutClass` makes, for the same counts."""
        terminals = (PROJECT_ROOT / "web" / "static" / "js" / "terminals.js").read_text(
            encoding="utf-8"
        )
        chunk = terminals[terminals.index("function getLayoutClass(count, layout)"):]
        chunk = chunk[: chunk.index("\n    }\n")]

        self.assertIn("if (count === 1) return 'layout-single';", chunk)
        self.assertIn(
            "layout-2-${layout === 'horizontal' ? 'horizontal' : 'vertical'}", chunk
        )
        self.assertIn("return 'layout-3-horizontal';", chunk)
        self.assertIn("return 'layout-3-split';", chunk)
        self.assertIn("return 'layout-3-vertical';", chunk)
        self.assertIn("if (count >= 4) return 'layout-grid';", chunk)

    def test_the_split_record_class_is_not_in_the_preset_table(self):
        """`layout-split-local` is a *written* record, never a derived one."""
        self.assertNotIn("layout-split-local", LAYOUT_CLASS_TRACKS)


class PresetGeometryTestCase(unittest.TestCase):
    def rects(self, count, layout):
        geometry = preset_geometry(count, layout)
        return [
            (rect["x"], rect["y"], rect["w"], rect["h"])
            for rect in geometry["split_slot_rects"]
        ]

    def test_two_panes_side_by_side_and_stacked_are_different_grids(self):
        self.assertEqual(self.rects(2, "vertical"), [(1, 1, 1, 1), (2, 1, 1, 1)])
        self.assertEqual(self.rects(2, "horizontal"), [(1, 1, 1, 1), (1, 2, 1, 1)])

    def test_the_three_pane_split_puts_one_pane_across_both_rows(self):
        """The CSS places pane 3 in column 2 spanning rows 1-2."""
        self.assertEqual(
            self.rects(3, "split"),
            [(1, 1, 1, 1), (1, 2, 1, 1), (2, 1, 1, 2)],
        )
        geometry = preset_geometry(3, "split")
        # And the wider left column is a *weight*, not a wider rectangle:
        # three equal rectangles over unequal tracks are three sizes.
        self.assertEqual(geometry["split_column_weights"], [2.0, 1.0])

    def test_a_fifth_pane_lands_in_a_third_row_rather_than_on_top_of_the_first(self):
        """`getGridMetrics(5)` declares 2x2; the fifth pane needs a third row."""
        self.assertEqual(grid_metrics(5), {"columns": 2, "rows": 2})
        self.assertEqual(
            self.rects(5, "grid"),
            [(1, 1, 1, 1), (2, 1, 1, 1), (1, 2, 1, 1), (2, 2, 1, 1), (1, 3, 1, 1)],
        )
        self.assertEqual(len(preset_geometry(5, "grid")["split_row_weights"]), 3)

    def test_an_empty_group_has_no_arrangement_to_describe(self):
        self.assertIsNone(preset_geometry(0, "single"))

    def test_the_layout_name_is_advisory_from_four_panes_up(self):
        """GridVibe forces `grid` at four or more whatever was asked."""
        self.assertFalse(layout_is_advisory(3))
        self.assertTrue(layout_is_advisory(4))
        self.assertEqual(layout_class(4, "vertical"), "layout-grid")


class RelativeAreaTestCase(unittest.TestCase):
    def test_equal_rectangles_over_unequal_tracks_are_different_sizes(self):
        """The whole reason the weights are published with the rectangles."""
        geometry = preset_geometry(3, "split")
        areas = relative_areas(geometry)

        # Left column carries weight 2 of 3; each left pane holds one of two
        # rows, so 2/3 * 1/2 each, and the right pane holds 1/3 of both rows.
        self.assertEqual(areas, [round(1 / 3, 4), round(1 / 3, 4), round(1 / 3, 4)])

    def test_the_smaller_panes_are_the_ones_with_the_smaller_share(self):
        stored = {
            "split_slot_rects": [
                {"x": 1, "y": 1, "w": 1, "h": 2},
                {"x": 2, "y": 1, "w": 1, "h": 1},
                {"x": 2, "y": 2, "w": 1, "h": 1},
            ],
            "split_column_weights": [3.0, 1.0],
            "split_row_weights": [1.0, 1.0],
        }
        areas = relative_areas(read_geometry(3, "grid", stored))

        self.assertEqual(areas[0], 0.75)
        self.assertEqual(sorted(areas)[:2], [0.125, 0.125])

    def test_a_grid_with_an_empty_cell_does_not_claim_the_whole_window(self):
        """Five panes in a 2x3 grid leave a cell; the shares say so."""
        areas = relative_areas(preset_geometry(5, "grid"))

        self.assertLess(sum(areas), 1.0)
        self.assertAlmostEqual(sum(areas), 5 / 6, places=3)


class AdjacencyTestCase(unittest.TestCase):
    """"Below" is exact, and it is a list because it has to be."""

    TALL_OVER_TWO = [
        {"x": 1, "y": 1, "w": 1, "h": 2},
        {"x": 2, "y": 1, "w": 1, "h": 1},
        {"x": 2, "y": 2, "w": 1, "h": 1},
    ]

    def test_below_is_the_pane_whose_top_edge_is_my_bottom_edge(self):
        rects = [
            {"x": 1, "y": 1, "w": 1, "h": 1},
            {"x": 1, "y": 2, "w": 1, "h": 1},
        ]

        self.assertEqual(neighbour_indexes(rects, 0)["below"], [1])
        self.assertEqual(neighbour_indexes(rects, 1)["above"], [0])
        self.assertEqual(neighbour_indexes(rects, 0)["right"], [])

    def test_a_tall_pane_has_two_panes_to_its_right_ordered_top_to_bottom(self):
        """An honest answer that names both, never a pick."""
        right = neighbour_indexes(self.TALL_OVER_TWO, 0)["right"]

        self.assertEqual(right, [1, 2])

    def test_a_wide_pane_above_two_lists_both_left_to_right(self):
        rects = [
            {"x": 1, "y": 1, "w": 2, "h": 1},
            {"x": 2, "y": 2, "w": 1, "h": 1},
            {"x": 1, "y": 2, "w": 1, "h": 1},
        ]

        # Ordered along the cross axis, not by array position: the pane at
        # column 1 is named first even though it is second in pane order.
        self.assertEqual(neighbour_indexes(rects, 0)["below"], [2, 1])

    def test_a_pane_that_only_touches_a_corner_is_nobodys_neighbour(self):
        rects = [
            {"x": 1, "y": 1, "w": 1, "h": 1},
            {"x": 2, "y": 2, "w": 1, "h": 1},
        ]

        self.assertEqual(
            neighbour_indexes(rects, 0),
            {"above": [], "below": [], "left": [], "right": []},
        )

    def test_an_index_outside_the_group_has_no_neighbours(self):
        self.assertEqual(
            neighbour_indexes(self.TALL_OVER_TWO, 9),
            {"above": [], "below": [], "left": [], "right": []},
        )


class StoredGeometryTestCase(unittest.TestCase):
    STORED = {
        "class_name": "layout-split-local",
        "split_slot_rects": [
            {"originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 2},
            {"originSlot": 1, "x": 3, "y": 1, "w": 1, "h": 1},
            {"originSlot": 2, "x": 3, "y": 2, "w": 1, "h": 1},
        ],
        "split_column_weights": [1.4, 1.4, 0.8],
        "split_row_weights": [1, 1],
        "original_split_slot_count": 3,
    }

    def test_a_written_record_is_read_back_and_marked_as_written(self):
        geometry = read_geometry(3, "grid", self.STORED)

        self.assertFalse(geometry["implied"])
        self.assertEqual(geometry["class_name"], "layout-split-local")
        self.assertEqual(geometry["split_column_weights"], [1.4, 1.4, 0.8])
        self.assertEqual(len(geometry["split_slot_rects"]), 3)

    def test_a_derived_record_says_it_was_derived(self):
        """"Implied" is not decoration: it separates described from invented."""
        self.assertTrue(read_geometry(3, "split", None)["implied"])

    def test_a_record_for_the_wrong_number_of_panes_falls_back_to_the_preset(self):
        geometry = read_geometry(2, "vertical", self.STORED)

        self.assertTrue(geometry["implied"])
        self.assertEqual(len(geometry["split_slot_rects"]), 2)

    def test_one_unreadable_rectangle_drops_the_whole_record(self):
        """All-or-nothing: keeping the rest reassigns a rectangle to a pane."""
        broken = dict(self.STORED)
        broken["split_slot_rects"] = [
            self.STORED["split_slot_rects"][0],
            {"x": "wide", "y": 1, "w": 1, "h": 1},
            self.STORED["split_slot_rects"][2],
        ]

        geometry = read_geometry(3, "split", broken)

        self.assertTrue(geometry["implied"])
        self.assertEqual(geometry["class_name"], "layout-3-split")

    def test_a_missing_weight_reads_as_an_even_track_rather_than_nothing(self):
        stored = {
            "split_slot_rects": [
                {"x": 1, "y": 1, "w": 1, "h": 1},
                {"x": 2, "y": 1, "w": 1, "h": 1},
            ],
            "split_column_weights": [2.0],
        }

        geometry = read_geometry(2, "vertical", stored)

        self.assertEqual(geometry["split_column_weights"], [2.0, 1.0])


class ComposerTestCase(unittest.TestCase):
    def test_the_composer_keys_every_position_by_session_id(self):
        composed = compose_group_geometry(["a", "b", "c"], "split")

        self.assertEqual([pane["session_id"] for pane in composed["panes"]], ["a", "b", "c"])
        self.assertEqual([pane["index"] for pane in composed["panes"]], [0, 1, 2])
        self.assertEqual(composed["panes"][0]["neighbours"]["below"], ["b"])
        self.assertEqual(composed["panes"][0]["neighbours"]["right"], ["c"])
        self.assertEqual(composed["panes"][2]["neighbours"]["left"], ["a", "b"])

    def test_the_composer_says_when_the_layout_name_is_advisory(self):
        composed = compose_group_geometry(["a", "b", "c", "d"], "vertical")

        self.assertTrue(composed["layout_advisory"])
        self.assertEqual(composed["geometry"]["columns"], 2)
        self.assertEqual(composed["geometry"]["rows"], 2)

    def test_an_empty_group_composes_no_panes_and_no_geometry(self):
        composed = compose_group_geometry([], "single")

        self.assertEqual(composed["panes"], [])
        self.assertIsNone(composed["geometry"])


class PaneLayoutRouteTestCase(unittest.TestCase):
    """The one read behind an agent describing the workspace it is in."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)

    def _group(self, count, layout="grid", workspace_layout=None):
        group = api.session_manager.create_group(
            name="Layout",
            connection_mode="wsl",
            layout=layout,
            terminal_count=count,
            workspace_layout=workspace_layout,
        )
        ids = []
        for index in range(count):
            session = api.session_manager.create_session(
                group_id=group.group_id,
                host="cmd",
                directory="C:/repo",
                mode="wsl",
                startup_mode="terminal",
                title=f"Terminal {index + 1}",
            )
            ids.append(session.session_id)
        return group, ids

    def test_the_route_answers_pane_order_with_a_rect_each(self):
        group, ids = self._group(3, layout="split")

        payload = self.client.get(
            f"/api/panes/layout?group_id={group.group_id}"
        ).get_json()

        self.assertEqual(payload["group_id"], group.group_id)
        self.assertEqual([pane["session_id"] for pane in payload["panes"]], ids)
        self.assertEqual(payload["layout"], "split")
        self.assertFalse(payload["layout_advisory"])
        self.assertTrue(payload["geometry"]["implied"])
        self.assertEqual(payload["panes"][0]["neighbours"]["below"], [ids[1]])

    def test_a_stored_geometry_is_reported_rather_than_derived(self):
        group, ids = self._group(
            2,
            layout="vertical",
            workspace_layout={
                "class_name": "layout-split-local",
                "split_slot_rects": [
                    {"originSlot": 0, "x": 1, "y": 1, "w": 1, "h": 1},
                    {"originSlot": 0, "x": 1, "y": 2, "w": 1, "h": 1},
                ],
                "split_column_weights": [1],
                "split_row_weights": [3, 1],
                "original_split_slot_count": 1,
            },
        )

        payload = self.client.get(
            f"/api/panes/layout?group_id={group.group_id}"
        ).get_json()

        self.assertFalse(payload["geometry"]["implied"])
        # The record says stacked even though the layout name says vertical:
        # the geometry is what holds.
        self.assertEqual(payload["panes"][0]["neighbours"]["below"], [ids[1]])
        self.assertGreater(
            payload["panes"][0]["relative_area"], payload["panes"][1]["relative_area"]
        )

    def test_a_group_that_is_not_open_is_a_404(self):
        self.assertEqual(
            self.client.get("/api/panes/layout?group_id=nope").status_code, 404
        )

    def test_the_route_needs_a_group_because_position_needs_one(self):
        self.assertEqual(self.client.get("/api/panes/layout").status_code, 400)

    def test_no_credential_reaches_the_layout_payload(self):
        group, _ids = self._group(2)
        for session in api.session_manager.get_group_sessions(group.group_id):
            api.session_manager.update_session_metadata(
                session.session_id, password="hunter2"
            )

        body = self.client.get(
            f"/api/panes/layout?group_id={group.group_id}"
        ).get_data(as_text=True)

        self.assertNotIn("hunter2", body)
        self.assertNotIn("password", json.loads(body).keys())


if __name__ == "__main__":
    unittest.main()
