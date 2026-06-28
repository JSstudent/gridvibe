# File Explorer Refresh Flicker Research

## Todo Item / Goal

Todo 2: file explorer keeps refreshing and flickering. It must stop auto-refreshing/resetting and should reset only when the user clicks the refresh button in the window panel next to the Connected indication.

## Current Repo Observations

- Explorer panes are first-class session panes, identified by `mode == "wsl"` and `startup_mode == "explorer"` in `web/api.py:_is_explorer_session` at `web/api.py:2208`.
- Explorer sessions skip PTY/background terminal startup and are immediately marked connected during session creation in `web/api.py:create_sessions` at `web/api.py:3024-3028`.
- Directory listing is a pull endpoint only: `GET /api/explorer/<session_id>/entries`, implemented by `web/api.py:get_explorer_entries` at `web/api.py:2686-2722`. It validates the path with `_resolve_explorer_paths` at `web/api.py:2213` and returns sorted entries. There is no backend watcher or server-pushed explorer refresh.
- The flicker source is in `templates/terminals.html`, not the backend. `loadExplorerPane(index, path = null)` at `templates/terminals.html:4262` always calls `renderExplorerMessage(index, 'Loading directory...')` at `templates/terminals.html:4270`, then replaces `explorer-list.innerHTML` after fetch. Any repeated caller visibly clears the list.
- Repeated callers exist:
  - `initialLoad()` calls `loadExplorerPane(i)` for each connected explorer at `templates/terminals.html:3863-3865`.
  - `refreshStatuses()` runs every 3 seconds via `setInterval(refreshStatuses, 3000)` at `templates/terminals.html:4460` and calls `loadExplorerPane(i)` for every connected explorer at `templates/terminals.html:4136-4138`.
  - Socket `session_status` events also call `loadExplorerPane(index)` for connected explorer sessions at `templates/terminals.html:4377-4379`.
- The per-pane refresh button already exists in the header next to the status badge: markup at `templates/terminals.html:2195-2207`, click handler at `templates/terminals.html:2366-2377`.
- `refreshTerminalDisplay(index)` already branches for explorer panes at `templates/terminals.html:1941-1946` and calls `loadExplorerPane(index)`. This is the natural manual refresh/reset entry point.

## Implementation Possibilities

1. Guard automatic explorer loads after first attach.
   - Add an option or early-return behavior so `loadExplorerPane(index)` does nothing when the pane is already attached and no explicit path/force was requested.
   - Let explicit directory navigation continue to call `loadExplorerPane(index, path)`.
   - Let the panel refresh button call with `force: true`.
   - Tradeoff: simplest and lowest risk. It preserves session polling for status labels while stopping repeated DOM replacement.

2. Remove `loadExplorerPane()` calls from `refreshStatuses()` and socket `session_status`.
   - Tradeoff: also simple, but less centralized. Future automatic callers could reintroduce flicker. It also leaves `initialLoad()` reuse/cache paths more fragile because the load condition is spread across callers.

3. Diff entries before replacing the DOM.
   - Fetch on every status tick, compare the returned payload with cached entries, and update only when changed.
   - Tradeoff: supports passive filesystem updates, but the todo explicitly says reset only on clicking refresh. It adds complexity and still performs avoidable I/O every 3 seconds.

4. Debounce/throttle explorer loads.
   - Tradeoff: reduces flicker frequency but does not meet the goal because automatic refreshes still happen.

## Recommended Safest/Simple Approach

Use option 1: make explorer loading explicit after the initial attach.

Concretely, `loadExplorerPane` should distinguish between:

- initial attach: allowed when `pane._attached` is false;
- navigation: allowed when a concrete `path` argument is supplied;
- manual panel refresh: allowed when a `force` option is true;
- automatic status/socket calls: ignored when the explorer is already attached and no explicit path/force is supplied.

This keeps the status polling mechanism intact, so Connected/Error badges continue to update, but stops the file list from being cleared every 3 seconds.

## Implemented

- Updated `templates/terminals.html:loadExplorerPane` to accept an options object with `force` and `showLoading`.
- Added an early guard so already attached explorer panes ignore automatic reload calls unless the caller supplies a concrete navigation path or `force: true`.
- Kept directory navigation behavior unchanged because directory row clicks still pass an explicit path.
- Updated `refreshTerminalDisplay(index)` so the panel refresh button forces an explorer reload of the current path with `loadExplorerPane(index, null, { force: true })`.
- Left `initialLoad()`, `refreshStatuses()`, and socket `session_status` callers unchanged; the centralized guard now allows first attach and blocks repeated automatic DOM replacement afterward.
- Added focused template regression coverage in `tests/test_api.py` for the guarded loader signature/early return and the forced manual explorer refresh path.

## Implementation Outline Completed

1. Updated `loadExplorerPane` signature in `templates/terminals.html` from:

   ```js
   async function loadExplorerPane(index, path = null) {
   ```

   to something like:

   ```js
   async function loadExplorerPane(index, path = null, { force = false, showLoading = true } = {}) {
   ```

2. Added an early guard after resolving `pane` and `sessionId`:

   ```js
   const isNavigation = path !== null;
   if (pane._attached && !force && !isNavigation) {
       return true;
   }
   ```

3. Kept current path resolution:

   ```js
   const nextPath = path === null ? (pane._explorerPath || '') : path;
   ```

4. Only show the loading message when the operation is initial/manual/navigation and the caller wants it:

   ```js
   if (showLoading) {
       renderExplorerMessage(index, 'Loading directory...');
   }
   ```

5. Changed the explorer branch in `refreshTerminalDisplay(index)` to force a manual reload:

   ```js
   await loadExplorerPane(index, null, { force: true });
   ```

   If product intent is that panel refresh should return to the explorer root, pass `''` instead of `null`.

6. Left these callers unchanged:
   - `initialLoad()` at `templates/terminals.html:3863-3865`;
   - `refreshStatuses()` at `templates/terminals.html:4136-4138`;
   - socket `session_status` at `templates/terminals.html:4377-4379`.

   With the guard, they still perform first attach but stop reloading attached explorer panes.

7. Added focused template-level tests in `tests/test_api.py` similar to the existing terminal refresh assertions:
   - assert `loadExplorerPane` accepts an options object with `force`;
   - assert attached explorer panes return early without `force`;
   - assert `refreshTerminalDisplay` calls `loadExplorerPane(index, null, { force: true })` for explorer panes.

## Risks / Tests To Consider

- Cached group views: when switching away and back, an attached cached explorer should not auto-refresh. That matches the todo, but verify the path label/list remain visible after tab switches.
- Initial explorer load: ensure a newly created explorer still removes the placeholder and displays entries once.
- Manual refresh behavior: verify the header refresh button reloads the current directory and clears/rebuilds the list only then.
- Navigation behavior: verify clicking directories and the up button still loads the requested path even after `_attached` is true.
- Error recovery: if the first explorer load fails, `_attached` should remain false so a later status tick or manual refresh can retry. The current `loadExplorerPane` only sets `_attached = true` after a successful response, which supports that.
- Backend path validation tests already cover directory listing and path escape rejection in `tests/test_api.py:1212-1271`; this change should not need backend changes.
