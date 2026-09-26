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
``no_window_available`` (the intent expired with no page to claim it, or the
wait ended with GridVibe unreadable -- a dropped poll is not a failed open, so
it degrades and the deadline answers). It never retries and it never pretends.

**A named group is a second step, and a page has to confirm it.** Raising a
native window that is already open does not change which tab it shows, so a
raise is not proof the group is on screen. Once the window step answers
``opened``, an *activate* intent is recorded, and only the workspace page
holding that group claims it: it switches tabs under its own refusals (an
unsaved editor, a copy in flight), focuses the named pane, and reports what it
now shows. ``opened`` with ``group_activated: true`` is that page's word; a
refusal is ``blocked`` with its reason and ``window_raised: true``; nobody
answering is ``no_window_available``, never a success. Browser mode has no
page to ask, so it says the tab it opened is ``verified: false``.
"""

import time
import urllib.parse
import webbrowser
from typing import Any, Callable, Dict, Optional, Tuple

from gridvibe_mcp.client import GridVibeClient, GridVibeError

OPENED = "opened"
BLOCKED = "blocked"
NO_WINDOW_AVAILABLE = "no_window_available"

#: The page's own word for "this group is the tab I now show". Only an
#: activation settles with it; it is never this module's outcome.
ACTIVATED = "activated"

#: How long the sidecar waits for a page to claim an intent and report back.
#: Longer than the store's own worst case, which is not its 15s claim window
#: but that plus the 20s a claimant then has to report: a page claiming at
#: 14.9s is still entitled to answer at 34.9s. The wait has to outlast that,
#: so the expiry is the store's answer rather than a race between two clocks.
#: `web/` cannot be imported from here, so the two numbers are pinned against
#: each other by `tests/test_mcp_tools.py` instead.
DEFAULT_WAIT_SECONDS = 40.0
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
    session_id: str = "",
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

    resolved_group_id = str(group_id or "").strip()
    resolved_session_id = str(session_id or "").strip()
    if mode == "native":
        timing = {
            "wait_seconds": wait_seconds,
            "poll_seconds": poll_seconds,
            "sleep": sleep or time.sleep,
            "monotonic": monotonic or time.monotonic,
        }
        opened = _open_native(client, resolved_workspace_id, resolved_group_id, **timing)
        if opened.get("status") != OPENED or not resolved_group_id:
            return opened
        return _activate_native(
            client,
            resolved_workspace_id,
            resolved_group_id,
            resolved_session_id,
            **timing,
        )
    result = _open_browser(
        client,
        resolved_workspace_id,
        resolved_group_id,
        browser_opener=browser_opener or _default_browser_opener,
    )
    if result.get("status") == OPENED and resolved_group_id:
        # A URL handed to the OS browser is a tab asked for, not one a page
        # has confirmed showing -- and nothing in it focuses a pane.
        result["verified"] = False
        result["note"] = (
            "Browser mode: a tab was opened at this session, but no GridVibe "
            "page confirms which tab it shows"
            + (" or focuses the pane." if resolved_session_id else ".")
        )
    return result


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

    state, detail, _result, read_error = _wait_for_intent(
        client,
        intent_id,
        (OPENED, BLOCKED),
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        monotonic=monotonic,
    )

    if state == OPENED:
        return {"status": OPENED, "window_mode": "native", "intent_id": intent_id}
    if state == BLOCKED:
        return {
            "status": BLOCKED,
            "window_mode": "native",
            "intent_id": intent_id,
            "detail": (f"{detail} " if detail else "") + FALLBACK_HINT,
        }
    if read_error:
        return {
            "status": NO_WINDOW_AVAILABLE,
            "window_mode": "native",
            "intent_id": intent_id,
            "detail": (
                "GridVibe could not be reached while waiting for the window "
                f"({read_error}), so whether a page opened one is not known "
                f"here. {FALLBACK_HINT}"
            ),
        }
    return {
        "status": NO_WINDOW_AVAILABLE,
        "window_mode": "native",
        "intent_id": intent_id,
        "detail": (
            "No GridVibe window was open to hand the request to. " + FALLBACK_HINT
        ),
    }


def _wait_for_intent(
    client: GridVibeClient,
    intent_id: str,
    settled: Tuple[str, ...],
    *,
    wait_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> Tuple[str, str, Dict[str, Any], str]:
    """Poll one intent until it settles, goes, or the wait runs out.

    Returns ``(state, detail, result, read_error)``; a wait that ran out reads
    ``expired``.
    """
    deadline = monotonic() + max(0.0, float(wait_seconds))
    state = "pending"
    detail = ""
    result: Dict[str, Any] = {}
    read_error = ""
    while True:
        try:
            record = client.read_window_intent(intent_id)
            read_error = ""
        except GridVibeError as exc:
            # A failed *poll* is not a failed open. The intent is recorded and a
            # page may be claiming it this second, so one dropped read must not
            # become "the call failed" about a window that then appears. The
            # read degrades and the deadline decides; the last failure is kept
            # so the answer can say it never found out rather than that nothing
            # happened.
            record = {}
            read_error = str(exc)
        state = str(record.get("state") or "").strip() or "pending"
        detail = str(record.get("detail") or "").strip()
        raw_result = record.get("result")
        result = dict(raw_result) if isinstance(raw_result, dict) else {}
        if state in settled or state == "expired":
            break
        if monotonic() >= deadline:
            state = "expired"
            break
        sleep(max(0.05, float(poll_seconds)))
    return state, detail, result, read_error


def _activate_native(
    client: GridVibeClient,
    workspace_id: str,
    group_id: str,
    session_id: str,
    *,
    wait_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> Dict[str, Any]:
    """Ask the page holding the group to show it, and report what it said.

    Runs only after the window step answered ``opened``, so every answer here
    says ``window_raised: true`` -- the window is up whatever the tab does.
    """
    base: Dict[str, Any] = {
        "window_mode": "native",
        "workspace_id": workspace_id,
        "group_id": group_id,
        "window_raised": True,
    }
    if session_id:
        base["session_id"] = session_id
    try:
        intent = client.activate_intent(workspace_id, group_id, session_id)
    except GridVibeError as exc:
        # The group or pane moved or closed after the window step read it.
        return {
            **base,
            "status": BLOCKED,
            "group_activated": False,
            "detail": f"The window was raised, but the tab was not switched: {exc}",
        }
    intent_id = str(intent.get("intent_id") or "").strip()
    if not intent_id:
        return {
            **base,
            "status": NO_WINDOW_AVAILABLE,
            "group_activated": False,
            "detail": (
                "The window was raised, but GridVibe did not record the "
                "request to show that session."
            ),
        }
    base["intent_id"] = intent_id

    state, detail, result, read_error = _wait_for_intent(
        client,
        intent_id,
        (ACTIVATED, BLOCKED),
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        monotonic=monotonic,
    )
    active_group_id = str(result.get("active_group_id") or "")
    if active_group_id:
        base["active_group_id"] = active_group_id
    activated = bool(active_group_id) and active_group_id == group_id
    if state == ACTIVATED and activated:
        answer = {**base, "status": OPENED, "group_activated": True}
        if session_id:
            answer["pane_visible"] = bool(result.get("pane_visible"))
            answer["focused"] = bool(result.get("focused"))
            if not answer["focused"]:
                answer["note"] = (
                    "The pane is on screen in its session tab but did not take "
                    "keyboard focus -- an explorer or browser pane cannot hold it."
                )
        return answer
    if state in (ACTIVATED, BLOCKED):
        return {
            **base,
            "status": BLOCKED,
            "group_activated": activated,
            "detail": detail or "The GridVibe window did not switch to that session.",
        }
    if read_error:
        return {
            **base,
            "status": NO_WINDOW_AVAILABLE,
            "group_activated": False,
            "detail": (
                "The window was raised, but GridVibe could not be reached while "
                f"waiting for it to show that session ({read_error}), so which "
                "tab it shows is not known here."
            ),
        }
    return {
        **base,
        "status": NO_WINDOW_AVAILABLE,
        "group_activated": False,
        "detail": (
            "The window was raised, but no GridVibe page holding that session "
            "confirmed switching to it, so which tab it shows is not known."
        ),
    }
