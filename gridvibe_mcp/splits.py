"""Splitting a pane from outside the browser.

The split button looks like an HTTP call and is not one. The *axis* never
reaches the server: the page computes the new rectangles, splices its own rect
list, and persists the result. Every refusal is the page's too, and they are
measured off the live terminal -- the minimum columns and rows below a terminal
header come from the pane's own character cell size, which a server cannot see.
A process that posted straight to ``/split`` would get a pane with no geometry
at all and would have consulted none of those rules.

So a split is an *intent*, exactly as opening a window is. The sidecar records
what it wants, the page that can measure the pane claims it and runs
``splitTerminalPane`` -- the same handler the button runs -- and reports back.

Three honest outcomes, the same shape ``windows.py`` has:

* ``split`` -- it happened, and the result names the new pane.
* ``refused`` -- GridVibe's own sentence, and the axis that *would* have worked
  if either does. Never a retry on the other axis: an agent that asked for a
  side-by-side split and silently got a stacked one has been lied to.
* ``no_window_available`` -- the intent expired with no page to claim it.

The last one is what browser mode always answers, because the intent poll runs
in a native GridVibe window only: a browser tab must not pay for a poll on
every page load. ``open_window`` has a browser-mode fallback because
``webbrowser.open`` is a real alternative; there is no equivalent for "measure
this pane".
"""

import time
from typing import Any, Callable, Dict, Mapping, Optional

from gridvibe_mcp.client import GridVibeClient

SPLIT = "split"
REFUSED = "refused"
NO_WINDOW_AVAILABLE = "no_window_available"

#: How long the sidecar waits for a page to claim the intent and report back.
#: Longer than the store's own worst case, which is not its 15s claim window
#: but that plus the 20s a claimant then has to report: a page claiming at
#: 14.9s is still entitled to answer at 34.9s. The wait has to outlast that,
#: because both hints below state that nothing happened -- and at 25s any
#: claim landing after about 5s could settle *after* the sidecar had already
#: said so, leaving an agent told a pane does not exist while it appears on
#: screen. `web/` cannot be imported from here, so the two numbers are pinned
#: against each other by `tests/test_mcp_tools.py` instead.
DEFAULT_WAIT_SECONDS = 40.0
DEFAULT_POLL_SECONDS = 0.5

#: Said after ``no_window_available``: the pane is untouched, and the reader
#: needs to know the split did not half-happen.
NO_PAGE_HINT = (
    "No GridVibe window was open to perform the split. The panes and the "
    "workspace are untouched. Splitting needs an open GridVibe window "
    "(native mode); ask the person running GridVibe to open the workspace."
)


def split_pane(
    client: GridVibeClient,
    session_id: str,
    axis: str,
    pane: Optional[Mapping[str, Any]] = None,
    *,
    origin_session_id: str = "",
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    sleep: Optional[Callable[[float], None]] = None,
    monotonic: Optional[Callable[[], float]] = None,
) -> Dict[str, Any]:
    """Ask an open GridVibe page to split one pane, and report what happened."""
    body: Dict[str, Any] = {"axis": axis, **dict(pane or {})}
    if origin_session_id:
        body["origin_session_id"] = origin_session_id

    # Everything decidable without measuring a pane is decided by this call,
    # before any waiting starts: an unknown agent key, a browser pane on a
    # remote host, an axis that is not one of the two. Its refusal is
    # GridVibe's own sentence and travels up as a typed error.
    intent = client.split_intent(session_id, body)
    intent_id = str(intent.get("intent_id") or "").strip()
    if not intent_id:
        return {
            "status": NO_WINDOW_AVAILABLE,
            "detail": f"GridVibe did not record the request. {NO_PAGE_HINT}",
        }

    rest = sleep or time.sleep
    now = monotonic or time.monotonic
    deadline = now() + max(0.0, float(wait_seconds))
    state = "pending"
    detail = ""
    result: Dict[str, Any] = {}
    while True:
        record = client.read_window_intent(intent_id)
        state = str(record.get("state") or "").strip() or "pending"
        detail = str(record.get("detail") or "").strip()
        if isinstance(record.get("result"), Mapping):
            result = dict(record["result"])
        if state in {SPLIT, REFUSED, "expired"}:
            break
        if now() >= deadline:
            state = "expired"
            break
        rest(max(0.05, float(poll_seconds)))

    if state == SPLIT:
        payload: Dict[str, Any] = {
            "status": SPLIT,
            "intent_id": intent_id,
            "axis": str(intent.get("axis") or axis),
        }
        if result:
            payload["pane"] = result
        return payload
    if state == REFUSED:
        return {
            "status": REFUSED,
            "intent_id": intent_id,
            "axis": str(intent.get("axis") or axis),
            # GridVibe's own sentence, unretried and unparaphrased. It names
            # which rule refused and, when the other axis would work, says so --
            # so the agent can offer that rather than calling again.
            "detail": detail or "GridVibe refused the split and gave no reason.",
        }
    return {
        "status": NO_WINDOW_AVAILABLE,
        "intent_id": intent_id,
        "detail": NO_PAGE_HINT,
    }
