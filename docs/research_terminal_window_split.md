# Terminal Window Split Research

## Todo Item / Goal

Todo 7: Add a split button for each terminal that splits the window. Splitting should stop when the resulting terminal pane would be smaller than 8 columns by 8 rows. Existing terminal swapping should still work; when terminals are swapped, they should take on the destination pane size.

## Current Repo Observations

- `templates/terminals.html` owns almost all visible terminal layout behavior. The backend serves it from `web.api.terminals_page()` at `web/api.py:2592`.
- Session groups currently store only `layout` and `terminal_count`, not arbitrary pane geometry. See `SessionGroup` in `sessions/manager.py:72` and `SessionManager.create_group()` in `sessions/manager.py:108`.
- Launch-time layouts are normalized to a small fixed set in `web/api.py:358`: single, two vertical/horizontal, three vertical/horizontal/split, and grid for four or more terminals.
- The terminal grid is a CSS grid with fixed classes in `templates/terminals.html:326`, `templates/terminals.html:337`, and `templates/terminals.html:382`. Four or more terminals use `layout-grid` with `--grid-columns` and `--grid-rows`.
- `buildGrid(sessions, layout)` in `templates/terminals.html:2148` creates one direct `.terminal-container` per session, assigns `data-slot`, builds the terminal header buttons, and appends cards into `#terminalsGrid`.
- Xterm resizing is already well factored. `observeTerminalResize()` at `templates/terminals.html:1208` observes each wrapper, `scheduleFit()` at `templates/terminals.html:1789` debounces refits, `fitTerminal()` at `templates/terminals.html:1757` calls `fitAddon.fit()`, and `emitTerminalResize()` at `templates/terminals.html:1682` sends `terminal_resize`.
- Backend PTY resize already exists through `handle_terminal_resize()` in `web/api.py:3239`, which calls `_resize_connection()` at `web/api.py:1839`.
- Current resize emission ignores terminals smaller than 20 columns or 5 rows at `templates/terminals.html:1692`. This conflicts with the requested 8 by 8 minimum and would need to change if 8-column panes are allowed.
- Drag swapping currently swaps DOM cards only when both cards have the same parent. See `swapTerminalCards()` and `wireCardDragAndDrop()` in `templates/terminals.html:2035` and `templates/terminals.html:2057`. This works with fixed CSS grid geometry because the size is tied to the grid position, not the terminal session.
- Cached session-group views preserve the grid DOM fragment, class name, and grid CSS vars in `cacheVisibleGroupView()` / `restoreCachedGroupView()` at `templates/terminals.html:1251` and `templates/terminals.html:1295`.
- There is no existing "append one terminal to this group" API. `POST /api/sessions` at `web/api.py:2890` creates a new session group or replaces a stable saved-session group, so using it for split would risk restarting live sessions.
- `SessionManager.create_session()` at `sessions/manager.py:141` can create one session, and `_connect_session` is already used after launch from `web/api.py:3036`, so a narrow append/split endpoint is feasible.

## Implementation Possibilities

### 1. Client-only layout split of existing terminals

Add split controls that only reshape currently launched terminals. This requires no backend work and reuses current xterm resize plumbing.

Tradeoff: It likely does not match terminal-emulator "split" expectations because it cannot create a new terminal pane. It only changes sizes of terminals that already exist.

### 2. Recreate the whole group with one more terminal

Use the existing `POST /api/sessions` route with an expanded session list and a new layout.

Tradeoff: This is the riskiest option. The route is launch-oriented, updates `active_launch_options`, creates/replaces groups, and starts sessions from scratch. It can lose live terminal state and replay buffers.

### 3. Add a narrow append/split endpoint plus client-side split geometry

Add a dedicated route such as `POST /api/sessions/<session_id>/split` that clones the clicked terminal's connection settings into the same group, starts exactly one new backend session, increments the group's terminal count, and returns the new session. The frontend then splits the clicked pane's visual slot and attaches the new terminal.

Tradeoff: This touches both backend and frontend, but it keeps behavior narrow and avoids disturbing existing live sessions. It best matches the todo.

### 4. Persist arbitrary split layouts in session config/groups

Extend saved sessions and `SessionGroup` with a layout tree or slot geometry.

Tradeoff: This is useful later, but it expands the data model and migration surface. It is not necessary for a first live-window split implementation.

## Recommended Safest/Simple Approach

Use option 3, but keep the first version session-window local and non-persistent.

Concretely:

- Add a small backend split endpoint that appends one cloned terminal to the current group instead of recreating the group.
- Keep split geometry in the frontend session window only.
- Represent geometry as explicit slots on the existing direct children of `#terminalsGrid`, so drag/drop can continue to work with minimal change.
- Enforce the 8 by 8 limit before creating a backend session. If the clicked pane cannot split into two panes that each fit at least 8 columns and 8 rows, disable or reject the split.
- Keep pane size attached to slot position, not terminal identity. After drag/drop, reapply geometry by DOM order so a terminal moved into another slot takes that slot's size.

This is safer than using `POST /api/sessions` because it does not restart the group, and simpler than persisting arbitrary split trees before the interaction is proven.

## Implementation Outline

1. Backend: add a small group mutation helper in `sessions/manager.py`.
   - Either add `append_session_to_group()` or use `create_session()` plus a helper to update `SessionGroup.terminal_count`.
   - Keep locking consistent with existing `SessionManager` methods.

2. Backend: add `POST /api/sessions/<session_id>/split`.
   - Load the source `TerminalSession`.
   - Reject missing sessions, missing groups, file-explorer panes, and groups already at `max_sessions`.
   - Clone safe settings from the source session: `group_id`, `host`, `directory`, `username`, `port`, `password`, `mode`, `distribution`, `use_wsl`, `use_powershell`, and `startup_mode`.
   - Choose a title like `Terminal N` or `<source title> split`.
   - Start `_connect_session` for terminal panes, mirroring `web/api.py:3036`.
   - Return the new session and updated group metadata.

3. Frontend: add a split button to the terminal header in `buildGrid()` near `templates/terminals.html:2199`.
   - Use a compact symbol or text label consistent with existing `.terminal-action-btn`.
   - Hide or disable it for explorer panes and when no valid split axis exists.

4. Frontend: introduce split slot state.
   - Maintain an array of slot rectangles or grid spans for the active group.
   - For current fixed launch layouts, initialize slot state from the existing layout class.
   - On split, divide the clicked slot along the larger viable axis.
   - Keep cards as direct grid children if possible. Apply geometry by child order, not by session id.

5. Frontend: enforce 8 by 8 before backend mutation.
   - Estimate resulting `cols` and `rows` from the pane content rectangle and xterm cell size.
   - Prefer using current xterm measurements; fall back to conservative cell dimensions if unavailable.
   - Require both resulting panes to fit at least 8 columns and 8 rows.
   - Update `emitTerminalResize()` from the current 20x5 guard to an 8x8 guard so backend PTYs receive valid small sizes.

6. Frontend: after the backend returns the new session.
   - Insert a new terminal object and session id at the destination slot.
   - Create the new card using the same card construction path as `buildGrid()` or extract card creation into a helper.
   - Apply split slot geometry.
   - Join the socket room, attach xterm when connected, call `scheduleFit()` for affected panes, and force one resize emit after fit.

7. Frontend: preserve swapping.
   - Keep using drag/drop if cards remain direct children.
   - After `swapTerminalCards()`, call the split-layout apply function and `scheduleFit()` for the swapped indices.
   - If inline geometry is stored on card elements, reapply it by DOM index after the swap so size follows destination slot.

8. Frontend: cache split layouts per visible group.
   - Extend `cacheVisibleGroupView()` to store split slot state.
   - Extend `restoreCachedGroupView()` to restore and reapply it.
   - Do not persist it to saved sessions in the first pass.

## Risks / Tests To Consider

- Minimum size conflict: `emitTerminalResize()` currently rejects columns below 20. Tests or manual checks should confirm 8x8 panes still resize the backend PTY.
- Swap behavior: verify dragging a large pane onto a small pane swaps terminal content and that both terminals are refit to their new sizes.
- Session lifecycle: verify split creates only one new backend session, does not restart existing sessions, and respects `max_sessions`.
- Connection cloning: SSH password is available in the in-memory `TerminalSession`, but saved/replayed behavior should not expose it in JSON responses.
- WSL and PowerShell modes: verify split cloning works for local terminal panes. Disable split for explorer panes until there is a clear expected behavior.
- Cached group views: split a pane, switch session tabs, switch back, and verify geometry and terminal output remain intact.
- Mobile layout: current CSS collapses layouts under 700px. The first version should probably disable split on narrow screens or force a stacked layout with the same 8x8 validation.
- Resize/repaint: verify `ResizeObserver`, `scheduleFit()`, and backend `terminal_resize` fire after split, after swap, after tab restore, and after window resize.
- Error rollback: if the backend split request fails after the frontend predicted a valid split, leave the current layout unchanged and show a small error state.

## Implementation Follow-up Notes

- The live split layout is derived from the launcher layout. Each original launcher pane owns a stable slot; single-pane and two-pane launcher layouts may split recursively, while larger launcher layouts only split original slots once.
- Splitting always uses the visible pane's long edge. If the long-edge split would not fit inside that original slot or the 8x8 terminal-size guard, the split button is disabled instead of falling back to the short edge.
- Single-pane and two-pane launcher layouts can recursively split up to the backend cap of eight panes. Layouts with three or more original panes are limited to one split per original pane, so a 3-pane launcher layout can split to at most 6 panes.
- Split panes now expose a close/unsplit control. Closing a split deletes the split-created backend session, reloads the visible group, restores the surviving pane to the parent slot, and keeps nested split ancestry intact until the original launcher layout is reached.
- The resize guard and backend PTY clamp now allow 8x8 terminal sizes.
