"""Page-backed resize of a live group divider."""

import time
from typing import Any, Dict

from gridvibe_mcp.client import GridVibeClient, GridVibeError, project
from gridvibe_mcp.splits import DEFAULT_POLL_SECONDS, DEFAULT_WAIT_SECONDS

_RESULT_FIELDS = ("group_id", "revision", "column_weights", "row_weights", "panes")
_PANE_FIELDS = ("session_id", "index", "rect")


def resize_divider(
    client: GridVibeClient, group_id: str, axis: str,
    line_index: int, position: float, expected_revision: int,
    *, wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> Dict[str, Any]:
    intent = client.resize_intent(group_id, {
        "axis": axis,
        "line_index": line_index,
        "position": position,
        "expected_revision": expected_revision,
    })
    intent_id = str(intent.get("intent_id") or "")
    if not intent_id:
        return {"status": "no_window_available", "detail": "No resize was recorded; nothing changed."}
    deadline = time.monotonic() + wait_seconds
    read_error = ""
    while True:
        try:
            record = client.read_window_intent(intent_id)
            read_error = ""
        except GridVibeError as exc:
            record = {}
            read_error = str(exc)
        state = str(record.get("state") or "pending")
        if state == "resized":
            raw = record.get("result") or {}
            result = project(raw, _RESULT_FIELDS)
            result["panes"] = [project(p, _PANE_FIELDS) for p in raw.get("panes", [])]
            return {"status": "resized", **result}
        if state == "refused":
            return {
                "status": "refused", "detail": record.get("detail") or "The page refused the resize; nothing changed.",
                "changed": False,
            }
        if state == "expired":
            return {
                "status": "no_window_available",
                "detail": (
                    "No page reported a completed resize before the request expired. "
                    "A page may have claimed it; read list_panes before retrying."
                ),
            }
        if time.monotonic() >= deadline:
            detail = (
                f"The resize result could not be read ({read_error}). Read list_panes before retrying; "
                "the outcome is unknown."
                if read_error else
                "The resize has not settled. Read list_panes before retrying; the outcome is unknown."
            )
            return {"status": "no_window_available", "detail": detail}
        time.sleep(DEFAULT_POLL_SECONDS)
