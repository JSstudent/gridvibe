# MCP focus pinning — only the focus tools move what the person sees

Source: a tool-by-tool check of the `gridvibe` MCP surface on 2026-09-29, done
on branch `szua_wrk_gv-mcp-override`.

> **Status:** all six stages are implemented. Stage 0 (`split_pane` in a
> tab that isn't showing) is committed as `07db2eb`. Stage 1 (agent-opened
> tabs don't take over the window, plus step 5) is `1d4160b`; Stage 2
> (`open_window` never brings a window forward) is `57ab6f2`; Stage 3
> (`resize_divider` in a tab that isn't showing) is `a6ee0b7`; Stage 4 (the
> visible-window requirement stated) is `ad386c4`. Stage 5 (the rule pinned
> by test and moved into maintained docs) is implemented, uncommitted. The
> hands-on checks of Stages 1–5 are pending (§4).
>
> **This file is no longer authoritative.** With Stage 5 the rule in §1, the
> `opened_by` semantics of §3.1, `open_window`'s no-raise path, the
> hidden-tab resize and the visible-window requirement live in
> `docs/engineering_contracts.md` (Agent tools (MCP)) and
> `gridvibe_mcp/README.md`; `VIEW_MOVING_TOOLS` / `VIEW_MOVING_FLAGS` /
> `BACKGROUND_TOOLS` in `gridvibe_mcp/server.py` and their test pin it. Read
> those. What follows is the design history. `docs/r&d/` is scratch.

---

## 1. The rule

An agent working in one tab must never change what the person is looking at.
Nothing comes forward unless the person asked for it, by asking an agent to
*focus* something or *bring it forward*. No tool may switch the window's
session tab, move keyboard focus, raise, restore or newly show a window in
front, or need the person to do one of those first, **except**:

| Tool | Allowed to move the view |
| --- | --- |
| `focus_session` | yes: that is what it is for |
| `focus_pane` | yes: that is what it is for |
| `move_session` with `show: true` | yes: `show` is an explicit request to show it; its description should tell the agent to set it only when the person asked to see or focus the moved session (Stage 5) |

Everything else runs in the background: it works in any tab of the window,
whether that tab is showing or not, and leaves the person's tab, focus and
window order exactly as they were.

"Closing the tab the person is looking at moves the window to another tab" is
not a violation. The tab they were looking at no longer exists, so something
else has to show. That behaviour stays.

## 2. Tool-by-tool findings

| Tool | Needs a page | Moves the view today | Finding |
| --- | --- | --- | --- |
| `gridvibe_status`, `list_*`, `whoami`, `read_handoff`, `report_result`, `wait_for_results` | no | no | — |
| `create_workspace` | no | no | Creates a workspace record only; no window. |
| `save_group_layout` | flushes an open page | no | — |
| `clear_pane` | no | no | A pane in a hidden tab is reset in place (`terminal_cleared`, cached branch). |
| `set_pane_agent`, `set_pane_mode` | no | no | `session_status` ignores panes whose tab isn't showing; a pane in the shown tab is redrawn in place. |
| `close_pane`, `close_group`, `close_workspace` | no | only when the shown tab closes | Unavoidable, see §1. |
| `split_pane` | yes | **no** (Stage 0) | §2.1: implemented. Still needs a visible window (§2.4). |
| `launch_panes` | no | **yes** | §2.2: the new tab takes over the window. |
| `move_session` (no `show`) | no | **yes**, in the destination window | §2.2: same cause. |
| `resize_divider` | yes | **no** (Stage 3) | §2.3: implemented. Still needs a visible window (§2.4). |
| `open_window` | yes | **yes** | §2.5: an already-open window is raised. |
| `focus_session`, `focus_pane`, `move_session show:true` | yes | yes | Intended. |

### 2.1 `split_pane` — implemented (Stage 0)

**What was wrong.** A split intent could only be claimed by the window
showing the pane's tab. For a pane in any other tab the person had to switch
to it, or the agent had to call `focus_pane` first.

**What it does now.** The split intent is claimed by the window that holds the
pane in *any* of its tabs:

- `splitBridge.owns()` in `terminals.js` also claims a pane that
  `backgroundGroupHolding()` finds in a tab that isn't showing (read from the
  group list, not the cached views).
- `background-split.js` does the split as an edit to the tab's *model*: pane
  order, one rectangle each, and the column and row weights. It reads that
  model from the tab's cached view while the cache still holds exactly the
  server's panes, and otherwise from the server's summary
  (`readBackgroundGroupModel`).
- Measurement uses the window's one shared grid (`measureGridForModel`) with
  the split button's own rules: pane cap, narrow window, integer grid and
  character floor. `policy.blockers` is checked case by case against the
  page's `getSplitBlockers`.
- The sequence is: refuse before anything is created, create the pane on the
  server, drop the tab's cached view, write the arrangement through the
  group's revisioned presentation transaction, then take the new record into
  the tab strip. Nothing touches the grid on screen, the tab, or focus. The
  wiring test pins that the path never reaches `switchGroup`,
  `focusPaneForArrival`, `initialLoad` or `restoreCachedGroupView`.
- A tab opened between the measurement and the request is handed to the
  visible handler (`performShown`).
- **In-flight hold.** The tab is held from the moment the request goes out
  until the arrangement is written. `initialLoad` waits for
  `backgroundSplit.settled(groupId)` twice: before its group-list read and
  before its read of the tab. So a tab the person picks mid-request is
  painted with the new pane already in place. It is never painted from the
  server's pre-save arrangement, and a cached view that is about to be
  dropped is never restored. The cache is dropped for every tab except the
  one painted (`discardBackgroundGroupView`), including a tab that is active
  but still waiting to load.
- A pane that was created is reported even if its layout could not be saved.
  In that case a `note` says it will appear with the default arrangement.
- The sidecar relays that `note`, and `NO_PAGE_HINT` now says the window has
  to be open and visible and that it doesn't matter which tab it shows.

Tests: `tests/test_background_split.py`, which covers the module sequence,
the page adapter, the bridge routing, the in-flight hold, and the load
waiting for it.

**Known limitations, carried into later stages:**

- It still needs a visible native window (§2.4).
- The *visible* handler (`splitTerminalPane`) has a related gap. If the
  person switches away while its request is in flight, the pane is created
  and adopted into the tab strip, but its place is never saved. That tab then
  comes back with the default arrangement. This isn't a focus problem, but it
  belongs to the same sequence, and it is fixed in Stage 1 (step 5).

### 2.2 A new tab takes over the window

`loadSessionGroups()` (`terminals.js`, the `hasNewGroup` branch) moves
`activeGroupId` to the newest group whenever a group appears that the window
had not seen. That is right when the person launches a session themselves
from that window or the launcher. It is wrong when an agent does it:

- `launch_panes` into an existing workspace (`"launched"` broadcast) switches
  every window on that workspace to the new tab.
- `move_session` without `show` does the same in the destination window
  (`"moved"` broadcast).

The rule can't key off the broadcast alone, because the fallback poll runs
`loadSessionGroups` with no broadcast at all. The group record itself has to
say who asked for it.

### 2.3 `resize_divider` needs its tab showing

`resizeBridge.perform` refuses with *"Open this session tab before resizing its
divider"* unless `groupId === activeGroupId === visibleGroupId`. It checks
again after its reads. The only way around that is `focus_session` first.

The measurement it needs is the same one Stage 0 already does for a tab that
isn't showing: the tab's model plus the shared grid's metrics. The
planner, `GridVibeSplitGeometry.planDividerResize`, is pure. The live-only
parts are `ensureSplitSlotRects`, `getResizableGridMetrics(grid, …)` on the
live weights, `validateResizeCandidate` and the repaint.

### 2.4 Split and resize need a visible window

`window-intent.js` doesn't poll while `document.visibilityState === 'hidden'`
(established practice: a background tab polls nothing). A minimized native
window normally reports hidden, so `split_pane` and `resize_divider` end as
`no_window_available`, and the person has to restore the window, which
brings it forward. The same happens when the workspace has no open window at
all.

This one is a design decision, not a bug. **Decided: A** (§3.4). The
requirement stays and is stated clearly; nothing wakes a hidden page.

### 2.5 `open_window` raises a window that is already open

`open_window` → open intent → page `openWorkspaceWindow` → pywebview bridge
`_open_workspace_window`. When the window already exists, that path calls
`_bring_to_front` (`webview_launcher.py`, the reuse branch). Launcher clicks
depend on that. An agent asking for a window that is already open should get
"already open" and nothing should move.

`focus_session` and `focus_pane` go through the same `window_opener`, then an
activation intent. So the change has to be a parameter of the open step,
never a change to the shared path.

A window that doesn't exist yet must not come forward either (§5): it is
created without taking the foreground (§3.3).

## 3. Design

### 3.1 Who opened a group (for §2.2)

- Add `opened_by` to the group record: `"person"` (default, including every
  record that predates this) or `"agent"`. The launch route sets it from the
  `tool_launch: true` flag that every MCP launch already sends (with or
  without a calling pane). The move route sets it from the
  `requested_by_session_id` the `move_session` tool always sends.
- The page's `hasNewGroup` switch only counts new groups whose
  `opened_by !== 'agent'`. A window with no tab at all still picks the newest
  group, because showing something in an empty window takes nothing away.
- Once an agent-opened tab has arrived, it is an ordinary tab: the person can
  click it, and `focus_session` switches to it.
- A `move_session` with `show: true` still switches, through its activation
  intent, not through this rule.
- No unseen marker (§5). The tab just appears in the strip, and it is shown
  only when the person clicks it or asks an agent to focus it or bring it
  forward.

### 3.2 A hidden-tab path for `resize_divider` (for §2.3)

- Extract the model-reading half of Stage 0 into a shared module, so split
  and resize read a hidden tab the same way instead of each getting their own
  copy. That half is `readBackgroundGroupModel`, `measureGridForModel`,
  `measureTerminalCell`, the hold, and the discard/adopt steps. Per the
  architecture extraction triggers it becomes one domain module, say
  `background-tab.js`, with `background-split.js` and a new
  `background-resize.js` on top.
- The resize is then: settle the tab's presentation queue, read its model,
  and check `expected_revision` against the server's record. Measure the
  shared grid for the model's weights, rebuild the track groups off the
  model's rects, then run `planDividerResize` and the minimum-size check.
  Hold the tab, write through the revisioned transaction, drop the tab's
  cached view, and take the new record.
- Refusals keep their current sentences: no shared edge, outside the grid, a
  pane below its minimum, the revision changed. Only *"Open this session tab
  …"* goes away.
- `resizeBridge.holds()` already claims any group the window holds, so no
  change is needed in `window-intent.js`.

### 3.3 `open_window` without raising (for §2.5)

- The page's `openWorkspaceWindow` gains an option not to raise, which is
  passed through to the bridge's `open_workspace_window`. Every existing
  caller keeps the default, which raises.
- The open intent records `raise: false` when it came from the `open_window`
  tool, and `raise: true` when it came from `focus_session`, `focus_pane` or
  `move_session show:true`.
- In the reuse branch, `raise: false` returns
  `{"ok": True, "reused": True, "raised": False}` without calling
  `_bring_to_front` and without restoring zoom. The tool reports
  `status: "opened"` with `already_open: true`.
- A new window must not come forward either (§5). It is created without
  activation and placed behind the window the person is using. Check the
  pinned pywebview's `focus` argument of `create_window`: not activating a
  window is not the same as not raising it, since on Windows a window shown
  without activation can still land on top. If the platform can't put it
  behind, create it **minimized**. The tool then says so: the window exists
  in the taskbar, and under §3.4 it can't perform split or resize until the
  person restores it.
- The tool reports how the window was left: `raised: false` always, plus
  `minimized: true` when that fallback was used.

### 3.4 Visible-window requirement (for §2.4)

**Decided: A. Keep it, and say so.** `split_pane` and `resize_divider` need
the workspace window open and not minimized. Which tab it shows doesn't
matter (for resize, once Stage 3 lands). `NO_PAGE_HINT` already says this for
split. The resize tool's expired answer (`gridvibe_mcp/geometry.py`) says the
same, and so do the tool descriptions and the READMEs. The agent relays the
sentence and never restores the window itself.

Not chosen, recorded in case it comes back: waking a hidden page with an
`intent_pending` socket event. It would first need a spike on whether a
minimized WebView2 window still reports its grid's real size.

## 4. Staged implementation plan

Each stage is one commit with one changelog note, and each is shippable on
its own.

### Stage 0 — `split_pane` in a tab that isn't showing *(done)*

§2.1. Code: `web/static/js/background-split.js`, the adapter and bridge in
`terminals.js`, the `initialLoad` hold, the script tag in `terminals.html`,
and the note and hint in `gridvibe_mcp/splits.py`. Tests:
`tests/test_background_split.py`, plus additions in
`test_window_intent_client.py`, `test_split_geometry.py` and
`test_mcp_tools.py`.

Committed as `07db2eb`. Its changelog note carries the background split as a
continuation paragraph (the headline is the in-flight fix, and must stay the
commit subject). README, `gridvibe_mcp/README.md` and the MCP contract describe
it, and ISSUE-2026-059 is closed.

### Stage 1 — agent-opened tabs don't take over the window *(committed as `1d4160b`)*

§2.2, design §3.1.

**As implemented** (starting commit `bee7d9a`):

- `web/workspaces.py` owns the value: `GROUP_OPENED_BY_PERSON` /
  `GROUP_OPENED_BY_AGENT` and `normalize_group_opened_by` (anything but
  `"agent"` is the person's). `SessionGroup.opened_by` is in `to_dict`, so
  `/api/session-groups` carries it.
- Launch: `_launch_opened_by` sets `"agent"` for every tool launch (the
  existing `tool_launch` reading: `tool_launch: true` or an
  `origin_session_id`, never a restore). A restore replays the snapshot's
  value; any other launch is the person's whatever its body says.
- Move: `move_group_to_workspace` sets `"agent"` when the body names
  `requested_by_session_id`, `"person"` otherwise; `move_group_for_agent`
  now forwards that id into the transaction body. `move_group` records it
  on the group.
- Beyond the plan: `_create_group_locked` no longer points the workspace's
  `active_group_id` hint (dashboard "active" marker, snapshot front tab) at
  an agent's new group, unless the hint names no live group of that
  workspace.
- Persistence: `_snapshot_group` captures it; `_validate_group` normalizes it
  (chrome, not shape: a bad value degrades to `"person"`, never costs the
  group); `_restore_group_request` carries it into the restore launch.
- Page: `loadSessionGroups` switches only to the newest *new* group whose
  `opened_by !== 'agent'`; an empty window (or one whose tab closed) still
  picks the newest group.
- Review fix: `renderSessionTabs` rebuilds every tab control on each
  refresh, so an agent's tab arriving dropped keyboard focus from a focused
  tab or close button. `captureSessionTabFocus` / `restoreSessionTabFocus`
  hand the focus to the rebuilt control of the same tab.
- Step 5: `captureSplitPlacement` in `terminals.js` reads the tab model
  (visual-order ids, rects, normalized weights, base count) and the cut
  (`planSplitSlotGeometryFor`, pure, no weight initialization) before the
  request. When the source is no longer shown and the source tab is not the
  painted one, `backgroundSplit.placeAfterMove(view, axis, cut, posted)` —
  the Stage 0 tail (`place`: discard, arrange, revisioned write against the
  split's revision, adopt) under the same hold — places the pane and returns
  the Stage 0 answer, `note` included. A tab rebuilt in place (painted from
  the server) keeps the old behaviour: it only adopts the record.
- Tests: `tests/test_agent_opened_tabs.py` (Node-executed
  `loadSessionGroups` and `renderSessionTabs` focus; launch/move routes;
  hint; snapshot → validate →
  restore round trip; legacy/invalid values) and `SplitInFlightTestCase` in
  `tests/test_background_split.py`, now running the real module under the
  real `splitTerminalPane`.

**Known limitations:** a tab rebuilt in place mid-request still comes back
with the server's default arrangement (the rebuild owns what is painted);
a presentation write that lands for the source tab between the split and
the placement makes the placement's revision stale, and the answer carries
the `note`.

**Hands-on checks (pending):**

1. With the workspace window on tab A, an agent in A runs `launch_panes`
   into this workspace: the new tab appears in the strip, the window stays
   on A, focus stays where it was.
2. Same with `move_session` (no `show`) from another workspace into this
   window: the tab arrives, the window stays. With `show: true` it switches.
3. The person launches a session into the open workspace from the launcher:
   the window switches to it (unchanged).
4. `focus_session` on the agent's tab switches to it; clicking it works.
5. Close every tab, then an agent launches one: the empty window shows it.
6. Save the workspace with an agent-opened tab, restart, restore: tabs come
   back; the front tab is the person's.
7. Step 5: start a split from a pane's header (or `split_pane` on a shown
   pane) and pick another tab before it lands (slow it with a throttled
   network or a breakpoint): return to the tab — the new pane is in its
   split position, not the default arrangement.

**Original plan:**

1. `sessions/manager.py`: add `opened_by` to the group record, and to its
   serialisation and restore (default `"person"`). Follow the
   session-state guide for persisted fields.
2. The launch route (`web/workspaces.py`, `"launched"`): set `"agent"` when
   the body has `tool_launch: true`. The move route (`"moved"`): set it when
   the body has `requested_by_session_id`.
3. `terminals.js` `loadSessionGroups`: `hasNewGroup` counts only groups not
   opened by an agent.
4. No unseen marker on the tab chip.
5. The visible split (formerly 1b). When `splitTerminalPane`'s window moved
   on mid-request (`splitSourceStillShown` is false), the pane is currently
   only adopted into the tab strip. Instead it is placed the way Stage 0
   places a hidden-tab pane: read the source tab's model, use the rectangle
   and plan captured *before* the request, write the arrangement through the
   revisioned transaction against the split response's revision, drop the
   tab's cached view, and hold the tab for that write so a return to it
   waits. The rectangle is captured at the same point as the source, so it
   can't be re-measured off whatever grid is showing now. A failed write
   reports the Stage 0 `note`.
6. Tests: a Node-executed `loadSessionGroups` with a new agent-opened group,
   a new person-opened group, and an empty window, plus a Python route test
   for the field and a restore round-trip. For step 5, extend
   `SplitInFlightTestCase`: switched away → arrangement written and tab held;
   switched back → painted in place and no second write; write failed →
   `note`.

Changelog: one `(fix)` note for the takeover, since it is user-visible
behaviour. Step 5 is part of the same commit, and its body gets a
continuation paragraph.

### Stage 2 — `open_window` leaves an open window where it is *(committed as `57ab6f2`)*

§2.5, design §3.3.

**Spike (pywebview 6.2.1, read from the installed source, no windows
launched).** `create_window` offers `focus`, `minimized`, `hidden` and
`on_top`, and nothing that places a window behind another:

- `focus=False` is not "don't activate this once". WinForms sets
  `WS_EX_NOACTIVATE` on the form and re-applies it on every activation; Qt
  sets `WA_ShowWithoutActivating` plus `WindowDoesNotAcceptFocus`; GTK calls
  `set_accept_focus(False)`. The window can never take keyboard focus, so
  nobody could type into its terminals, and on Windows `Form.Show()` still
  puts it at the top of the z-order (and a no-activate window gets no
  taskbar button). Unusable.
- `minimized=True`: WinForms sets `WindowState = Minimized` before `Show()`,
  so the form appears only in the taskbar — but a minimized form is shown
  with `SW_SHOWMINIMIZED`, which *activates* it, and its `Shown` handler
  focuses the WebView. Qt calls `showNormal()` then `showMinimized()` (a
  pywebview workaround) and skips `activateWindow()` / `raise_()` for a
  minimized window; GTK calls `iconify()`.
- `hidden=True` on WinForms is `Show()` at opacity 0 then `Hide()` — an
  activation flicker, and a later show would need raw Win32 z-order calls.

**Route chosen: minimized.** "Behind the foreground window" can't be
guaranteed with these options, so a new window is created with
`minimized=True` on every platform. On Windows the activation that
`SW_SHOWMINIMIZED` causes is undone: the bridge records
`GetForegroundWindow()` before creating and, right after creation and again
on the window's `shown` event, hands the foreground back with
`SetForegroundWindow` — only when the foreground is now the new window, so a
window the person picked meanwhile is never taken from them.

**As implemented** (starting commit `1d4160b`):

- `web/window_intents.py`: `open(..., raise_window=True)` records `raise`;
  the public window payload carries it (a record without it reads `true`).
  `WINDOW_RESULT_FIELDS = ("reused", "raised", "minimized")` lets the page
  report how the window was left.
- `web/api.py` `/api/windows/open`: only a boolean `raise: false` clears it.
- `gridvibe_mcp/client.py`: `open_window_intent(..., raise_window=True)`
  sends `raise: false` only when asked. `gridvibe_mcp/windows.py`:
  `open_window(..., raise_window=True)`; with `False`, an `opened` answer
  adds `already_open`, `raised: false`, and `minimized: true` with a `note`
  (created minimized, or open but minimized; split and resize need it
  restored — ask the person, don't call a focus tool). The focus path's
  answers are unchanged. `gridvibe_mcp/server.py`: the `open_window` tool
  passes `raise_window=False`; its description says it never brings a window
  forward, that `focus_session` / `focus_pane` are for that, and that in
  browser mode the browser may show the tab it opens. A page that reports no
  window result gets no `raised` / `already_open` claim, only a note that it
  is not known.
- `window-intent.js`: `policy.raises(intent)`, `policy.opened(answer)`,
  `policy.windowResult(intent, answer)`; `deliverWindow` passes
  `raise: false` only when the intent says so and reports the window result
  (none after a raise, or from a host answering a bare boolean). `bootstrap`
  prefers the host's `openWorkspaceWindowResult`.
- `workspaces.js`: `openWorkspaceWindowResult(workspaceId, options)` returns
  `{ ok, reused, raised, minimized }` and always passes `raise` to the bridge
  as the fourth argument; with `raise: false` it skips the arrival pulse.
  `openWorkspaceWindow` is that function's `.ok`, so every existing caller is
  unchanged. With `raise: false` a refused native open returns `{ ok: false }`
  instead of falling back to `window.open`, so the intent reports `blocked`.
- `web/webview_launcher.py`: `open_workspace_window(..., raise_window=True)`
  (only an explicit `False` skips the raise). Reuse branch without raise:
  no `_bring_to_front`, no zoom, answers
  `{"ok", "reused": True, "raised": False, "minimized": <tracked>}`. New
  window without raise: `minimized=True`; the window is tracked minimized
  from *before* `create_window` (so its own `minimized` event is an echo and
  never starts the minimize cascade), registered with
  `register_window(..., minimized=True)` (new keyword), and the flag is reset
  if creation fails. `_foreground_window_handle` / `_hand_foreground_back`
  do the Windows hand-back; it answers `None` (nothing to hand back),
  `True` or `False` (refused), and a refused immediate hand-back adds
  `focus_moved: True` to the bridge's answer, which the page and tool relay
  with a note (a refusal from the later `shown` callback can only be
  logged).
- Tests: `tests/test_open_window_no_raise.py` — route/store round trip and
  result field list; the tool's request body and answers (reused, new
  minimized, reused minimized, focus path unchanged), the dispatch passing
  `raise_window=False`, and the description; the launcher with fake windows
  (reuse neither shows nor zooms; a new window is created with
  `minimized=True`, tracked minimized at creation, registered minimized, and
  hands the foreground back twice; the raised path is unchanged; failed or
  throwing creation leaves no stale flag); the hand-back rules with a fake
  `user32`; Node-executed `window-intent.js` (flag passed on, result
  reported, legacy and raising intents unchanged, bootstrap prefers the
  result form) and `openWorkspaceWindowResult` from `workspaces.js`. The
  fake clients in `tests/test_mcp_tools.py` take the new keyword.

**Review (codex, independent review; OCR delegate failed with access
denied):**

1. HIGH — a refused native open with `raise: false` fell through to the
   browser `window.open` fallback, which can bring a window forward, and
   the tool then claimed `raised: false`. **Fixed**: no fallback for a
   no-raise open; the tool answers for the window only from a page report
   that says `raised: false`.
2. MEDIUM — a failed foreground hand-back was silent. **Fixed in part**:
   the immediate hand-back's refusal is reported as `focus_moved: true`
   with a note, and every refusal is logged; the later `shown` callback runs
   after the answer is sent and can only log. A guaranteed no-focus creation
   path does not exist in pywebview 6.2.1 (see the spike).
3. MEDIUM — `gridvibe_mcp/README.md:163` still says `open_window` puts a
   window on screen. **Deferred to Stage 5** as the handoff directs (README,
   engineering contracts and the MCP README are that stage's pass).

Re-review (codex, independent review, in a separate review tab
`focus-pin S2 re-review` because the first reviewer pane was still running
and relaunching it needs the person's override): no actionable findings;
fixes 1 and 2 verified, 3 not re-raised.

**Known limitations:**

- Windows: a hand-back refused from the `shown` callback is logged only.
- Linux (Qt): pywebview's `showNormal()` before `showMinimized()` may map
  the window for a moment before it minimizes, depending on the window
  manager; nothing here can suppress that.
- Browser mode: `open_window` still hands the URL to the OS browser, which
  may bring the browser forward. The description says so.
- A minimized window's page reports `hidden`, so under §3.4 it can't take
  split or resize intents until the person restores it; the tool's note
  says so.

**Hands-on checks (pending):**

1. Workspace window open behind another window: an agent calls
   `open_window` for it — the answer says `already_open: true`,
   `raised: false`; nothing moves, keyboard focus stays where it was.
2. Same with the workspace window minimized: it stays minimized; the answer
   says `minimized: true` with the note.
3. A workspace with no window: `open_window` creates it minimized in the
   taskbar; the person's window keeps keyboard focus (type into a terminal
   right after — the keys land there); `minimized: true` in the answer.
   Clicking the taskbar entry restores it and the page works (terminals fit
   the window).
4. With `workspace.minimize_cascade` on, step 3 does not minimize the
   person's other windows.
5. `focus_session` / `focus_pane` on a window behind others still bring it
   forward (unchanged), including one created minimized by step 3.
6. Launcher: opening a workspace from the launcher still raises it
   (unchanged).
7. Linux (Qt) if available: step 3 — note whether the window flashes before
   minimizing.

**Original plan:**

1. `web/window_intents.py`: the open intent carries `raise`. The tool sends
   `false`, and the activation path sends `true`.
2. `window-intent.js`: pass `raise` through to `openWorkspaceWindow`.
   `workspaces.js`: pass it on to the bridge.
3. `web/webview_launcher.py`: in the reuse branch, honour `raise`. For a new
   window with `raise: false`, create it without activation and behind the
   current foreground window, or minimized if that can't be guaranteed
   (§3.3). Start with a short spike of the pinned pywebview on Windows and
   Linux.
4. `gridvibe_mcp/windows.py`: report `already_open`, `raised: false`, and
   `minimized` when the fallback was used. Update the `open_window`
   description to say it never brings a window forward, and that
   `focus_session` / `focus_pane` are for that.
5. Tests: intent round-trip, Node policy for the page, launcher unit tests
   with a fake window (reuse doesn't call `_bring_to_front`; a new window is
   created with the no-activation or minimized options), and the tool's
   answer.

### Stage 3 — `resize_divider` in a tab that isn't showing *(committed as `a6ee0b7`)*

§2.3, design §3.2.

**As implemented** (starting commit `57ab6f2`):

- `web/static/js/background-tab.js` (`GridVibeBackgroundTab`): the shared
  half. `read(groupId)` settles the tab's queue (best effort, reported
  through `onError`) and reads its model; `measure(model)`; `hold(groupId)` /
  `settled(groupId)` (the per-tab set of in-flight edits, moved out of
  `background-split.js` unchanged); `write(groupId, revision, layout)` drops
  the cache, then writes (no revision writes nothing; a throw comes back as
  `{ ok: false, thrown }`); `adopt(group, layout, saved)`. One instance per
  window, handed to both edits as `page.tab`.
- `background-split.js` now takes `page.tab` and keeps only its own policy
  and sequence; no behaviour change (its whole suite passes with the harness
  building the tab).
- `web/static/js/background-resize.js` (`GridVibeBackgroundResize`):
  `policy.sameLayout(model, live)` (the visible path's three comparisons)
  and `policy.fits(...)` (the `validateResizeCandidate` rule: a sixteenth of
  the track space, then the character floor unless the pane is an
  explorer). `create(page).perform(intent)`: shown → visible handler; not
  held → "switching" refusal; a close still pending for the tab → refused;
  then read model and `/api/panes/layout`, revision, line/position,
  shared edge, same layout, narrow, measurable, track groups,
  `planDividerResize`, minimum; `isShown` again with nothing before the
  hold; hold, write, adopt. Every refusal keeps its visible sentence and
  ends "Nothing changed."; a read that throws is refused "The resize
  failed: …"; after the write starts, a throw, a no-revision answer, a
  revision not above the expected one, or a close is `unknown`.
- `terminals.js`: `backgroundTab` built once from the Stage 0 adapter
  (`readBackgroundGroupModel`, `measureGridForModel` — which now also
  returns its `metrics` — `discardBackgroundGroupView`, the save,
  `adoptBackgroundGroup`, renamed from `adoptBackgroundSplit`); the save
  answers `unknown: true` when the server accepted but returned no revision
  (the split still treats it as not saved). `backgroundTabSettled` (was
  `backgroundSplitSettled`) is what `initialLoad` waits on, so the load hold
  covers both edits. `getResizeTrackGroups(axis, lineIndex, rects =
  splitSlotRects)`. `backgroundGroupHeld(groupId)` is the resize's hold
  condition (settled window, not the shown tab, in the group list — the same
  settledness `backgroundGroupHolding` requires). `resizeBridge.perform`
  routes a held background tab to `backgroundResize.perform`, otherwise to
  `resizeBridge.performShown` (the old body); its first refusal is now "This
  window is switching session tabs; try the resize again shortly." —
  reached only between tabs. `readExemptions` asks `/api/sessions` for the
  group (a never-shown tab has no pane objects). `holds()` is unchanged, so
  `window-intent.js` is unchanged.
- `templates/terminals.html` loads `background-tab.js`, `background-split.js`,
  `background-resize.js`, in that order, before `terminals.js`.
- `gridvibe_mcp/server.py`: the `resize_divider` description says the
  session may be any tab of its window, showing or not, that the window
  doesn't switch tabs or move focus, and "The open native window measures…"
  instead of "The visible native page…". (The full visible-window sentence
  is Stage 4.)
- Tests: `tests/test_background_tab.py` (hold across two edits and a load
  that started between them, other tabs free, read/settle, write order,
  no-revision, throw, refused write); `tests/test_background_resize.py`
  (module sequence with the real tab module, planner and track groups:
  order, written weights and revision, stacked axis, hold and load
  ordering, shown-at-start and opened-while-measuring hand-offs, sixteen
  refusals before any write, server refusal, four `unknown` cases, release
  on every exit; `fits` against the page's `validateResizeCandidate` over
  768 cases, both answers occurring; `sameLayout`; bridge routing lifted
  from `terminals.js`); `tests/test_background_split.py` harnesses build
  the tab, the wiring test checks all three script tags and the three
  modules plus both adapters for `switchGroup` / `focusPaneForArrival` /
  `initialLoad` / `restoreCachedGroupView`; `test_resize_bridge.py`,
  `test_pane_connecting_overlay.py` and `test_mcp_geometry.py` (description)
  follow the renames. The legacy `test_api.py` signature assertion for
  `getResizeTrackGroups(axis, lineIndex)` is replaced by an executed check
  that the painted and model readings agree.

**Review (codex, independent review of all 14 changed/new files; OCR
delegate failed with access denied):** no actionable findings. The stale
contract line ("A resize still needs its tab showing") and README wording
were noted as Stage 5's, per the plan.

**Known limitations:**

- A tab with a pane close that has not been shown since is refused ("…its
  tab has not been shown since, so its arrangement is not settled yet"):
  the pending close model is applied at that tab's next load and would
  replace the written weights. The agent cannot clear this without the
  person showing the tab. (A split from behind is not affected: its new
  pane makes the pending close mismatch and be dropped.)
- The tab's cached view is dropped for a resize as for a split, so the tab
  is rebuilt from the server when next shown (terminal scrollback replays;
  explorer state is what the presentation record keeps).
- The minimum check uses the window's shared cell and header size (read off
  a live terminal on screen, defaults otherwise), not each pane's own; the
  font is window-wide, so this matches unless panes differ in header
  height.
- Still needs an open, visible window (§2.4 / Stage 4); the
  `docs/engineering_contracts.md` line "A resize still needs its tab
  showing" and the READMEs are Stage 5's pass.

**Hands-on checks (pending):**

1. Workspace window on tab A; an agent in A reads `list_panes` for tab B
   (two panes side by side) and calls `resize_divider` on B's vertical
   line at 0.3: the answer is `resized`, the window stays on A, focus stays
   where it was. Click B: the divider is at about 30%.
2. Same with B never shown since the window opened (restore a workspace,
   stay on A).
3. Resize B to a position that would make a pane too small: refused with
   the minimum sentence, B unchanged when shown.
4. Stale revision (resize B twice with the first revision): the second is
   refused "The session layout changed…".
5. Close a pane in B from an agent without showing B, then resize B: refused
   with the "has not been shown since" sentence; show B, re-read
   `list_panes`, resize again from A after returning: works.
6. Start a background resize and click B while it is in flight (slow
   network or a breakpoint in `saveLayout`): B paints with the new weights,
   not the old ones.
7. Resize the tab being shown (A) still works and repaints at once
   (unchanged).
8. An explorer pane in B at a narrow width: a position that leaves it
   under the character floor but above a sixteenth of the grid is allowed,
   as on the visible grid.

**Original plan:**

1. Extract `background-tab.js` from Stage 0 with no behaviour change, and
   move its tests along with it.
2. Add `background-resize.js` (pure policy plus `create(page)` sequence),
   routed from `resizeBridge.perform` when the group isn't the painted one.
3. The load hold covers resize too (the same `settled`).
4. Update the tool description in `gridvibe_mcp/server.py`, which currently
   says "in an open session".
5. Tests: the module sequence (refuse before writing, revision mismatch,
   hold, release on every exit); parity of the model-based minimum check
   against `validateResizeCandidate`; and the bridge routing.

### Stage 4 — state the visible-window requirement (option A) *(committed as `ad386c4`)*

§2.4, design §3.4. Words only; no behaviour change.

**As implemented** (starting commit `a6ee0b7`):

- `gridvibe_mcp/splits.py`: `VISIBLE_WINDOW_REQUIREMENT`, the one sentence
  for both tools: "A split or resize needs a native-mode GridVibe window on
  its workspace that is open and visible, not minimized or hidden; whichever
  session tab it shows does not matter. If that window is closed or
  minimized, ask the person running GridVibe to open or restore it rather
  than calling focus_session or focus_pane." `NO_PAGE_HINT` is now its two
  split-specific sentences ("No GridVibe window performed the split. The
  panes and the workspace are untouched.") followed by that constant.
- `gridvibe_mcp/geometry.py`: the no-intent answer ("No resize was recorded;
  nothing changed.") and the expired answer (which keeps its "A page may
  have claimed it; read list_panes before retrying.") append the constant.
  The deadline answer (outcome unknown, the intent may still be pending) is
  left as it was: it is not a no-window finding.
- `gridvibe_mcp/server.py`: the `split_pane` and `resize_divider`
  descriptions include the constant (after the "any tab, nothing switches"
  sentence for split; after "persists before reporting resized" for resize).
- Not changed: `open_window`'s minimized note in `gridvibe_mcp/windows.py`
  keeps its own contextual sentence ("split_pane and resize_divider need it
  restored: ask the person to restore it rather than calling focus_session
  or focus_pane."), which says the same thing about a window it just left
  minimized.
- Tests: `tests/test_mcp_geometry.py` — both resize no-window answers carry
  the constant, `NO_PAGE_HINT` carries it, the constant names the four
  parts (not minimized or hidden, session tab, ask the person, not a focus
  tool), the expiry keeps "read list_panes"; both tool descriptions carry
  it. `tests/test_mcp_tools.py` — the split expiry answer says "ask the
  person" and "rather than calling focus_session or focus_pane".

**Review (codex, independent review of all six changed files; OCR delegate
failed with access denied):** no findings for Stage 4. It confirmed the
constant reaches both descriptions, the split's no-window hint and both
resize no-window answers, and that the unknown-outcome paths keep their
uncertainty. It noted the stale contract line ("A resize still needs its tab
showing") as Stage 5's, per the plan.

**Known limitations:** none new. The requirement itself stands (§3.4):
nothing wakes a hidden page, so a minimized or closed window still ends
split and resize as `no_window_available`. `docs/engineering_contracts.md`
and the READMEs are Stage 5's pass.

**Hands-on checks (pending):**

1. Minimize the workspace window; an agent calls `split_pane` on one of its
   panes: after the wait, `no_window_available` with the requirement
   sentence; nothing moves, the window stays minimized, and the agent asks
   the person to restore it instead of calling `focus_session` /
   `focus_pane`.
2. Same with `resize_divider`: the answer carries the same sentence.
3. Restore the window (on any tab) and repeat both: they work.

**Original plan:**

1. `gridvibe_mcp/geometry.py`: the resize's expired and no-intent answers
   carry the same requirement sentence as `NO_PAGE_HINT` (open, not
   minimized, any tab), sharing one constant rather than a copy.
2. The `split_pane` and `resize_divider` descriptions in
   `gridvibe_mcp/server.py` state the requirement, and that the agent should
   ask the person to restore the window rather than calling a focus tool.
3. Tests: the resize answer's detail, and the description text through the
   existing tool-spec tests.

### Stage 5 — pin the rule and move it into maintained docs *(implemented, not yet committed)*

**As implemented** (starting commit `ad386c4`):

- `gridvibe_mcp/server.py`: `VIEW_MOVING_TOOLS = ("focus_session",
  "focus_pane")`, `VIEW_MOVING_FLAGS = {"move_session": "show"}`, and
  `BACKGROUND_TOOLS` — every other tool, spelled out name by name (not built
  from the tier tuples, so a tool added to a tier is in neither set until
  placed), with a comment stating §1, the arrival rule of §3.1, the minimized
  `open_window`, the close exception and the visible-window requirement. `move_session`'s `show`
  description: "Afterwards, raise the destination window and switch it to this
  tab. Only when the person asked to see or focus the moved session; without
  it the destination window stays on the tab it shows." Beyond the plan:
  `launch_panes`'s description now says a new tab in a workspace whose window
  already shows a tab joins the strip without being shown, and to call `focus_session`
  only when the person asked (Stage 1 shipped the behaviour but never told the
  agent).
- `tests/test_mcp_tools.py`
  (`test_every_tool_says_whether_it_may_move_the_persons_view`): the two sets
  are exactly the focus pair / everything else, disjoint, no duplicates, and
  together equal `tool_names()`, so a new tool fails until it is placed; each
  view-moving tool's description says it raises its workspace window; the one
  flag is a boolean on a background tool whose description says "Only when the
  person asked"; `launch_panes` says "without being shown". The existing
  `MoveSessionTestCase` already pins behaviourally that a move without
  `show` never calls the window opener.
- `docs/engineering_contracts.md` → Agent tools (MCP): new bullets **Only an
  explicit focus request moves what the person sees** (§1 and the three
  constants), **An agent's new tab joins the strip without being shown** (§3.1
  `opened_by`: values, launch/move sources, normalization, the page rule and
  empty-window exception, the `active_group_id` hint, tab-control focus
  hand-over, snapshot/restore), **`open_window` never brings a window
  forward** (Stage 2), **Split and resize need an open, visible window, and
  say so** (Stage 4). The split bullet's tail ("The page still has to poll …
  A resize still needs its tab showing.") is replaced: its hold now names the
  shared `backgroundTabSettled` / `background-tab.js`, and it states Stage 1
  step 5 (`placeAfterMove`). The resize bullet's "Only the visible page can
  claim it" is replaced by the any-tab claim and the `background-resize.js`
  path with its not-yet-shown-close refusal.
- `gridvibe_mcp/README.md`: a rule paragraph under Tools; per-tool rows for
  `launch_panes` (new tab unshown), `open_window` (never forward,
  `already_open` / `raised` / `minimized`, browser mode), `move_session`
  (`show` only on the person's ask), `resize_divider` (any tab); the resize
  paragraph's "A native window must show the session tab." replaced; the
  "need a page" section's open-window and resize bullets and its no-window
  paragraph (split *and* resize, `VISIBLE_WINDOW_REQUIREMENT`, ask the
  person).
- `README.md`: one new bullet, **Agents work in the background** (native
  window). Stale wording fixed in place, not new bullets: the tier table's
  `open_window` ("put it on screen" → "give it a window") and
  `resize_divider` ("in an open session" → "in any session tab"), "the open
  session's width", and the former **Splitting a pane needs a window open**
  bullet now covers resizing and asking the person to restore the window. The
  shortcut table is untouched.
- `CHANGELOG.md`: one `(doc)` note at the top of Unreleased; its headline
  is the commit subject.

**Plan as written:**

1. `gridvibe_mcp/server.py`: a `VIEW_MOVING_TOOLS` constant
   (`focus_session`, `focus_pane`), plus the `show` flag of `move_session`.
   Add one test in `tests/test_mcp_tools.py` that walks every registered tool
   and requires each one to be either in that set or documented as a
   background tool, so a new tool has to choose. Reword `move_session`'s
   `show` description ("Afterwards, raise the destination window on this
   tab.") to say it is only for when the person asked to see or focus the
   moved session.
2. `docs/engineering_contracts.md` → Agent tools (MCP): state the §1 rule
   and the §3.1 `opened_by` semantics.
3. `gridvibe_mcp/README.md`: per-tool notes (split and resize work in any
   tab; `open_window` never brings a window forward; split and resize need
   the window open and not minimized).
4. README: at most one bullet ("Agents work in the background: only an
   explicit focus request moves your view").

**Review (codex, independent review of the six-file diff; OCR delegate failed
with access denied):**

1. MEDIUM — `BACKGROUND_TOOLS` was built from the tier tuples, so a
   view-moving tool added to an existing tier would join it automatically and
   the test would pass without anyone choosing. **Fixed**: the tuple is spelled
   out name by name; the contract and MCP README say so.
2. LOW — `launch_panes`'s description (and its MCP README row) promised an
   unshown tab even in an empty window, which Stage 1 deliberately fills.
   **Fixed**: "a window that already shows a tab".
3. MEDIUM — the contract rule said no tool may "need the person to" restore a
   window first, contradicting Stage 4's kept requirement. **Fixed**: that
   clause is dropped from the rule, and the visible-window requirement is named
   beside the close exception as not a violation (contract, MCP README, the
   constant's comment).
4. LOW — the README bullet promised new windows leave focus alone, though a
   refused Windows hand-back reports `focus_moved`. **Fixed**: it now promises
   no tab switch and no window brought forward, and that a window an agent
   opens starts minimized.

Re-review (codex, independent review; OCR delegate failed with access
denied): the four fixes verified. One new finding, MEDIUM — the rule in the
contract and the MCP README still read as an unconditional guarantee that no
background tool moves keyboard focus, while Stage 2 reports `focus_moved`
when Windows refuses the hand-back and browser mode may show the opened tab.
**Fixed** (docs only): both now say the rule binds what a tool asks for, and
name those two platform exceptions beside it. Doc-only, so no third review
was requested.

**Known limitations:**

- The pin is structural: the test makes a new tool *choose* a set and checks
  the descriptions, but it cannot prove that a tool placed in
  `BACKGROUND_TOOLS` really leaves the view alone. The behaviour is pinned by
  each stage's own tests (`test_agent_opened_tabs.py`,
  `test_open_window_no_raise.py`, `test_background_split.py`,
  `test_background_resize.py`, `MoveSessionTestCase`).
- Browser mode: `open_window` still hands the URL to the OS browser, and a
  refused Windows foreground hand-back is reported (`focus_moved`) or logged,
  not prevented; the README bullet says "In the native window" for that
  reason.
- The limitations of Stages 1–4 stand unchanged.

**Hands-on checks (pending):**

1. Ask an agent to split, resize, launch a tab and open a window for another
   workspace, without asking to see anything: the tab, focus and window order
   stay as they were throughout.
2. Ask an agent to "move session X to workspace Y" (no mention of showing):
   it calls `move_session` without `show`; "… and show it" sets `show`.
3. Ask an agent to "launch a Codex pane in a new tab": it does not follow
   with `focus_session` unless asked.
4. The hands-on checks of Stages 1–4 above.

## 5. Decisions (2026-09-29)

- **Stage 2:** nothing is brought forward unless the person says so by
  asking to *focus* it or *bring it forward*. That covers a newly created
  window too (§3.3).
- **Stage 4:** option A. Keep the visible-window requirement and state it;
  don't wake hidden pages (§3.4).
- **Stage 1:** no unseen marker. An agent-opened tab shows only when the
  person clicks it or asks to focus it or bring it forward. The former
  Stage 1b (visible split whose window moved on) is now part of Stage 1,
  as step 5.
