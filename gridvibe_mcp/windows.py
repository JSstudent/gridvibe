"""Making a workspace actually appear on screen.

Creating a workspace over HTTP creates a record; it does not open a window.
The two runtime modes need different answers, and the branch is read per call
rather than cached, because a browser-mode fallback can happen mid-session.

**Native.** Nothing outside a page can open a pywebview window, so the sidecar
stores an *intent* and a page claims it. Exactly one claimant wins, so two open
pages deliver one window.

**Browser.** The sidecar is not a page. It is a local process, and
``webbrowser.open`` hands the URL to the OS default browser -- no pop-up
blocker is involved, because nothing is popping up. It works with zero GridVibe
pages open.

Three honest outcomes, and the tool reports which: ``opened``, ``blocked``
(a native page refused, or no browser would take the URL), and
``no_window_available`` (the intent expired with no page to claim it). It never
retries and it never pretends.
"""

import time
import urllib.parse
import webbrowser
from typing import Any, Callable, Dict, Optional

from gridvibe_mcp.client import GridVibeClient, GridVibeError

OPENED = "opened"
BLOCKED = "blocked"
NO_WINDOW_AVAILABLE = "no_window_available"

#: How long the sidecar waits for a page to claim an intent and report back.
#: Comfortably longer than the store's own TTL so the expiry is the store's
#: answer rather than a race between two clocks.
DEFAULT_WAIT_SECONDS = 25.0
DEFAULT_POLL_SECONDS = 0.5

#: Said after both failing outcomes: the workspace exists either way, and that
#: is the thing the reader needs to know.
FALLBACK_HINT = "The workspace exists and can be opened from the GridVibe launcher."


def workspace_url(base_url: str, workspace_id: str, group_id: str = "") -> str:
    """The page a workspace window shows."""
    query = {"workspace": str(workspace_id or "")}
    if group_id:
        query["group"] = str(group_id)
    return f"{base_url}/terminals?{urllib.parse.urlencode(query)}"


def open_window(
    client: GridVibeClient,
    workspace_id: str,
    group_id: str = "",
    *,
    window_mode: str = "",
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    browser_opener: Optional[Callable[[str], bool]] = None,
    sleep: Optional[Callable[[float], None]] = None,
    monotonic: Optional[Callable[[], float]] = None,
) -> Dict[str, Any]:
    """Open one workspace window, by whichever route this runtime mode has."""
    resolved_workspace_id = str(workspace_id or "").strip()
    if not resolved_workspace_id:
        return {"status": BLOCKED, "detail": "No workspace was named."}

    mode = str(window_mode or "").strip().lower()
    if not mode:
        try:
            mode = str(client.health().get("window_mode") or "").strip().lower()
        except GridVibeError:
            mode = ""

    if mode == "native":
        return _open_native(
            client,
            resolved_workspace_id,
            group_id,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            sleep=sleep or time.sleep,
            monotonic=monotonic or time.monotonic,
        )
    return _open_browser(
        client,
        resolved_workspace_id,
        group_id,
        browser_opener=browser_opener or _default_browser_opener,
    )


def _default_browser_opener(url: str) -> bool:
    return bool(webbrowser.open(url, new=2))


def _open_browser(
    client: GridVibeClient,
    workspace_id: str,
    group_id: str,
    *,
    browser_opener: Callable[[str], bool],
) -> Dict[str, Any]:
    url = workspace_url(client.base_url, workspace_id, group_id)
    try:
        opened = browser_opener(url)
    except Exception as exc:  # noqa: BLE001 - report the refusal, never raise past it
        return {
            "status": BLOCKED,
            "window_mode": "browser",
            "url": url,
            "detail": f"No browser would take the URL ({exc}). {FALLBACK_HINT}",
        }
    if opened:
        return {"status": OPENED, "window_mode": "browser", "url": url}
    return {
        "status": BLOCKED,
        "window_mode": "browser",
        "url": url,
        "detail": f"No browser would take the URL. {FALLBACK_HINT}",
    }


def _open_native(
    client: GridVibeClient,
    workspace_id: str,
    group_id: str,
    *,
    wait_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> Dict[str, Any]:
    intent = client.open_window_intent(workspace_id, group_id)
    intent_id = str(intent.get("intent_id") or "").strip()
    if not intent_id:
        return {
            "status": NO_WINDOW_AVAILABLE,
            "window_mode": "native",
            "detail": f"GridVibe did not record the request. {FALLBACK_HINT}",
        }

    deadline = monotonic() + max(0.0, float(wait_seconds))
    state = "pending"
    detail = ""
    while True:
        record = client.read_window_intent(intent_id)
        state = str(record.get("state") or "").strip() or "pending"
        detail = str(record.get("detail") or "").strip()
        if state in {OPENED, BLOCKED, "expired"}:
            break
        if monotonic() >= deadline:
            state = "expired"
            break
        sleep(max(0.05, float(poll_seconds)))

    if state == OPENED:
        return {"status": OPENED, "window_mode": "native", "intent_id": intent_id}
    if state == BLOCKED:
        return {
            "status": BLOCKED,
            "window_mode": "native",
            "intent_id": intent_id,
            "detail": (f"{detail} " if detail else "") + FALLBACK_HINT,
        }
    return {
        "status": NO_WINDOW_AVAILABLE,
        "window_mode": "native",
        "intent_id": intent_id,
        "detail": (
            "No GridVibe window was open to hand the request to. " + FALLBACK_HINT
        ),
    }
