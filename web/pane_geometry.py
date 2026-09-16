"""Where each pane of a group actually is, and which pane is next to which.

A pane's size is never stored. Each pane holds a rectangle in a CSS grid and
each grid track carries a weight, so a pane's area is the sum of its spanned
column weights times the sum of its spanned row weights. "The smaller
terminals" needs the rectangles *and* the weights -- publishing one without the
other is worse than publishing neither, because three equal rectangles over
unequal tracks are three different sizes.

Two things live here and nowhere else in Python:

* **The preset table.** The page writes a ``workspace_layout`` record only for
  a group that has actually been split locally. Every other group wears one of
  the preset grids, whose tracks are declared in ``terminals.css`` and whose
  pane placement is implicit in DOM order. Deriving that table here is a second
  copy of something the frontend already owns, and the copy is accepted
  knowingly: persisting a geometry record for every group so the page became
  the single source would churn presentation revisions on every launch for a
  benefit no user can see. What makes the copy safe is
  ``tests/test_pane_geometry.py``, which reads the declared tracks out of
  ``terminals.css`` and ``getGridMetrics``' three breakpoints out of
  ``shared.js`` and asserts they match the constants below.

* **The adjacency rules.** With rectangles in hand "below" is exact and needs
  no measurement. Each direction is a *list*, ordered along the cross axis,
  because a tall pane can sit above two stacked ones -- and "the terminal below
  me" resolving to two panes is an honest answer that names both, never a pick.

Pure: no Flask, no session manager, no I/O. The route composes; this module
decides.
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: The grid each preset layout class declares in ``terminals.css``, as the
#: track lists the stylesheet literally carries. Pinned by the CSS-reading test
#: so a stylesheet edit cannot silently move a pane the agent was told about.
LAYOUT_CLASS_TRACKS: Dict[str, Tuple[str, str]] = {
    "layout-single": ("1fr", "1fr"),
    "layout-2-vertical": ("1fr 1fr", "1fr"),
    "layout-2-horizontal": ("1fr", "1fr 1fr"),
    "layout-3-vertical": ("repeat(3, 1fr)", "1fr"),
    "layout-3-horizontal": ("1fr", "repeat(3, 1fr)"),
    "layout-3-split": ("2fr 1fr", "1fr 1fr"),
}

#: ``getGridMetrics`` in ``web/static/js/shared.js``, as ``(at least this many
#: panes, columns, declared rows)`` -- highest breakpoint first, exactly the
#: order the function tests them in. The *declared* rows are what the CSS
#: variable carries; the real row count is ``ceil(count / columns)``, because a
#: fifth pane in a 2x2 grid lands in an implicit third row.
GRID_BREAKPOINTS: Tuple[Tuple[int, int, int], ...] = (
    (8, 4, 2),
    (6, 3, 2),
    (4, 2, 2),
)

#: The layout names ``_normalize_layout`` accepts. Above three panes the name
#: is advisory: the normalizer forces ``grid`` regardless of what was asked, so
#: a reader that acted on the name alone would be acting on a lie.
LAYOUTS = ("single", "vertical", "horizontal", "split", "grid")

#: Above this many panes the layout name no longer describes the arrangement.
ADVISORY_LAYOUT_THRESHOLD = 4

#: The record the page writes for a locally split group.
SPLIT_CLASS_NAME = "layout-split-local"


def layout_class(count: int, layout: str) -> str:
    """The CSS class a group of this size and layout wears.

    Mirrors ``getLayoutClass`` in ``terminals.js``; the shared test pins the
    tracks each returned class declares.
    """
    panes = max(0, int(count or 0))
    name = str(layout or "").strip().lower()
    if panes <= 1:
        return "layout-single"
    if panes == 2:
        return "layout-2-horizontal" if name == "horizontal" else "layout-2-vertical"
    if panes == 3:
        if name == "horizontal":
            return "layout-3-horizontal"
        if name == "split":
            return "layout-3-split"
        return "layout-3-vertical"
    return "layout-grid"


def grid_metrics(count: int) -> Optional[Dict[str, int]]:
    """``getGridMetrics``: the declared columns and rows for a grid layout."""
    panes = max(0, int(count or 0))
    for threshold, columns, rows in GRID_BREAKPOINTS:
        if panes >= threshold:
            return {"columns": columns, "rows": rows}
    return None


def layout_is_advisory(count: int) -> bool:
    """Whether the group's layout *name* still describes its arrangement."""
    return max(0, int(count or 0)) >= ADVISORY_LAYOUT_THRESHOLD


def _rect(x: int, y: int, w: int, h: int, origin_slot: int) -> Dict[str, int]:
    return {"originSlot": origin_slot, "x": x, "y": y, "w": w, "h": h}


def preset_geometry(count: int, layout: str) -> Optional[Dict[str, Any]]:
    """The rectangles and track weights a group with no stored record wears.

    ``None`` for an empty group -- there is no arrangement to describe.
    """
    panes = max(0, int(count or 0))
    if panes <= 0:
        return None

    name = layout_class(panes, layout)

    if panes == 1:
        return _geometry(name, [_rect(1, 1, 1, 1, 0)], [1.0], [1.0])

    if panes == 2:
        if name == "layout-2-horizontal":
            return _geometry(
                name,
                [_rect(1, 1, 1, 1, 0), _rect(1, 2, 1, 1, 1)],
                [1.0],
                [1.0, 1.0],
            )
        return _geometry(
            name,
            [_rect(1, 1, 1, 1, 0), _rect(2, 1, 1, 1, 1)],
            [1.0, 1.0],
            [1.0],
        )

    if panes == 3:
        if name == "layout-3-split":
            # Two stacked panes on the left over a 2fr track, one full-height
            # pane on the right over a 1fr track.
            return _geometry(
                name,
                [_rect(1, 1, 1, 1, 0), _rect(1, 2, 1, 1, 1), _rect(2, 1, 1, 2, 2)],
                [2.0, 1.0],
                [1.0, 1.0],
            )
        if name == "layout-3-horizontal":
            return _geometry(
                name,
                [_rect(1, index + 1, 1, 1, index) for index in range(3)],
                [1.0],
                [1.0, 1.0, 1.0],
            )
        return _geometry(
            name,
            [_rect(index + 1, 1, 1, 1, index) for index in range(3)],
            [1.0, 1.0, 1.0],
            [1.0],
        )

    metrics = grid_metrics(panes) or {"columns": 2, "rows": 2}
    columns = max(1, int(metrics["columns"]))
    # The declared rows are what the CSS variable says; the real count is what
    # the panes actually occupy, so a fifth pane in a 2x2 grid is placed in the
    # implicit third row rather than on top of the first.
    rows = max(1, -(-panes // columns))
    return _geometry(
        name,
        [
            _rect(1 + (index % columns), 1 + (index // columns), 1, 1, index)
            for index in range(panes)
        ],
        [1.0] * columns,
        [1.0] * rows,
    )


def _geometry(
    class_name: str,
    rects: List[Dict[str, int]],
    column_weights: List[float],
    row_weights: List[float],
) -> Dict[str, Any]:
    return {
        "class_name": class_name,
        "split_slot_rects": rects,
        "split_column_weights": list(column_weights),
        "split_row_weights": list(row_weights),
        "original_split_slot_count": len(rects),
    }


def _weights(values: Any, span: int) -> List[float]:
    """Read a stored weight list, filling a missing track with 1.0."""
    resolved: List[float] = []
    source = values if isinstance(values, (list, tuple)) else []
    for index in range(max(0, span)):
        try:
            resolved.append(float(source[index]))
        except (IndexError, TypeError, ValueError):
            resolved.append(1.0)
    return resolved


def read_geometry(
    count: int,
    layout: str,
    workspace_layout: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """The arrangement of a group, stored if there is one and derived if not.

    Carries ``implied``: ``True`` when the rectangles came from the preset
    table rather than from a record the page wrote. A reader that cannot tell
    the two apart cannot tell a described layout from an invented one.
    """
    panes = max(0, int(count or 0))
    if panes <= 0:
        return None

    stored = workspace_layout if isinstance(workspace_layout, Mapping) else None
    rects = stored.get("split_slot_rects") if stored else None
    if isinstance(rects, list) and len(rects) == panes:
        normalized: List[Dict[str, int]] = []
        for index, raw in enumerate(rects):
            if not isinstance(raw, Mapping):
                normalized = []
                break
            try:
                normalized.append(
                    _rect(
                        int(raw.get("x", 1)),
                        int(raw.get("y", 1)),
                        int(raw.get("w", 1)),
                        int(raw.get("h", 1)),
                        int(raw.get("originSlot", index)),
                    )
                )
            except (TypeError, ValueError):
                # A geometry record is all-or-nothing: one unreadable rectangle
                # and the whole record is dropped for the preset, because
                # keeping the rest would assign a rectangle to another pane.
                normalized = []
                break
        if normalized:
            columns = max(item["x"] + item["w"] - 1 for item in normalized)
            rows = max(item["y"] + item["h"] - 1 for item in normalized)
            geometry = _geometry(
                str(stored.get("class_name") or SPLIT_CLASS_NAME),
                normalized,
                _weights(stored.get("split_column_weights"), columns),
                _weights(stored.get("split_row_weights"), rows),
            )
            geometry["implied"] = False
            return geometry

    geometry = preset_geometry(panes, layout)
    if geometry is None:
        return None
    geometry["implied"] = True
    return geometry


def relative_areas(geometry: Mapping[str, Any]) -> List[float]:
    """Each pane's share of the grid, so "the smaller two" needs no arithmetic.

    The shares of a grid holding an empty cell sum to less than one, which is
    the honest reading: a 2x2 grid with five panes has a sixth cell nobody is
    using.
    """
    rects = list(geometry.get("split_slot_rects") or [])
    columns = list(geometry.get("split_column_weights") or [])
    rows = list(geometry.get("split_row_weights") or [])
    total = sum(columns) * sum(rows)
    if total <= 0:
        return [0.0 for _ in rects]
    areas = []
    for rect in rects:
        width = sum(columns[rect["x"] - 1: rect["x"] - 1 + rect["w"]])
        height = sum(rows[rect["y"] - 1: rect["y"] - 1 + rect["h"]])
        areas.append(round((width * height) / total, 4))
    return areas


def _overlaps(start_a: int, span_a: int, start_b: int, span_b: int) -> bool:
    return start_a < start_b + span_b and start_b < start_a + span_a


def neighbour_indexes(
    rects: Sequence[Mapping[str, int]],
    index: int,
) -> Dict[str, List[int]]:
    """Which panes touch this one, per direction, as lists.

    A list rather than an optional id on purpose: a tall pane sits above two
    stacked ones, and a verb asking for "the one below" has to be able to see
    that there are two and refuse naming both.
    """
    empty: Dict[str, List[int]] = {"above": [], "below": [], "left": [], "right": []}
    if index < 0 or index >= len(rects):
        return empty
    mine = rects[index]
    above: List[Tuple[int, int]] = []
    below: List[Tuple[int, int]] = []
    left: List[Tuple[int, int]] = []
    right: List[Tuple[int, int]] = []
    for other_index, other in enumerate(rects):
        if other_index == index:
            continue
        columns_overlap = _overlaps(mine["x"], mine["w"], other["x"], other["w"])
        rows_overlap = _overlaps(mine["y"], mine["h"], other["y"], other["h"])
        if columns_overlap and other["y"] + other["h"] == mine["y"]:
            above.append((other["x"], other_index))
        if columns_overlap and other["y"] == mine["y"] + mine["h"]:
            below.append((other["x"], other_index))
        if rows_overlap and other["x"] + other["w"] == mine["x"]:
            left.append((other["y"], other_index))
        if rows_overlap and other["x"] == mine["x"] + mine["w"]:
            right.append((other["y"], other_index))
    # Ordered along the cross axis, so "the two below me" reads left to right
    # and "the two to my right" reads top to bottom.
    return {
        "above": [item[1] for item in sorted(above)],
        "below": [item[1] for item in sorted(below)],
        "left": [item[1] for item in sorted(left)],
        "right": [item[1] for item in sorted(right)],
    }


def compose_group_geometry(
    session_ids: Sequence[str],
    layout: str,
    workspace_layout: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """The whole positional answer for one group, keyed by session id.

    Pure, and the one composer both the HTTP route and its tests call.
    ``session_ids`` is the group's own pane order, so an entry's array position
    *is* its index.
    """
    ordered = [str(item or "") for item in session_ids]
    count = len(ordered)
    geometry = read_geometry(count, layout, workspace_layout)
    resolved_layout = str(layout or "").strip().lower() or "single"

    if geometry is None:
        return {
            "layout": resolved_layout,
            "layout_advisory": layout_is_advisory(count),
            "terminal_count": 0,
            "geometry": None,
            "panes": [],
        }

    rects = geometry["split_slot_rects"]
    areas = relative_areas(geometry)
    panes = []
    for index, session_id in enumerate(ordered):
        rect = rects[index]
        adjacency = neighbour_indexes(rects, index)
        panes.append(
            {
                "session_id": session_id,
                "index": index,
                "rect": {key: rect[key] for key in ("x", "y", "w", "h")},
                "relative_area": areas[index],
                "neighbours": {
                    direction: [ordered[item] for item in members]
                    for direction, members in adjacency.items()
                },
            }
        )

    return {
        "layout": resolved_layout,
        "layout_advisory": layout_is_advisory(count),
        "terminal_count": count,
        "geometry": {
            "class_name": geometry["class_name"],
            "implied": geometry["implied"],
            "columns": len(geometry["split_column_weights"]),
            "rows": len(geometry["split_row_weights"]),
            "column_weights": geometry["split_column_weights"],
            "row_weights": geometry["split_row_weights"],
            "split_slot_rects": rects,
        },
        "panes": panes,
    }
