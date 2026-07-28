# Multi-workspace investigation and minimal implementation plan

Date: 2026-07-28  
Status: Proposal  
Scope: Multiple live workspaces, one terminal window per workspace, moving session
groups between workspaces, and selective restore after restart.

## Summary

GridVibe already has part of the persistence foundation for multiple workspaces:
`runtime_state.json` schema v2 stores a `workspaces` dictionary keyed by
`workspace_id`. The live application is still single-workspace, however:

- every `SessionGroup` is stored in one global ordered collection;
- session-group APIs and `session_groups_updated` events are global;
- the terminals page loads every group;
- the launcher and browser fallback reuse one named terminal window;
- the native bridge owns one `_session_window`;
- restore asks only for the `"default"` saved slot.

The minimal reliable implementation is to make a workspace the owner of one or
more session groups while keeping sessions owned by their existing group:

```text
Workspace (one window)
└── SessionGroup (one tab)
    └── TerminalSession (one pane)
```

Moving a group then changes only `SessionGroup.workspace_id` and per-workspace
tab order. It does not recreate terminal sessions, close SSH connections, or
restart local processes.

The persisted schema can remain version 2. A saved slot already implies the
workspace that owns its groups; the missing work is live ownership and
workspace-aware APIs, events, URLs, and native windows.

## Current architecture findings

### What is already reusable

- `web/runtime_state.py` has `SCHEMA_VERSION = 2`, a `"workspaces"` dictionary,
  sibling-preserving writes, labels, origins, and per-workspace load/clear
  helpers.
- Workspace snapshots contain group launch configuration and deliberately omit
  passwords.
- The frontend already treats a session group as a movable/reorderable tab and
  caches inactive group views.
- Terminal output and status events are already scoped to per-session
  Socket.IO rooms. Moving a group does not require changing those rooms.
- Browser and pywebview launch paths already accept a starting group through
  `/terminals?group=<id>`.
- Existing in-page confirmation infrastructure can protect unsaved Explorer
  edits before a move.

### Single-workspace assumptions that must change

1. `SessionManager.groups` has one global `display_order`. `SessionGroup` has no
   `workspace_id`, and `get_all_groups()` / `reorder_groups()` are global.
2. `capture_workspace(session_manager, workspace_id=...)` currently captures
   every live group regardless of the requested ID. The existing multi-slot
   test proves sibling-slot preservation, not correct group partitioning.
3. `iter_live_workspaces()` explicitly maps all live groups to `"default"`.
4. `GET /api/session-groups`, unfiltered `GET /api/sessions`, autosave, and the
   global `session_groups_updated` event expose all groups to every terminal
   window.
5. A saved preset gets a global stable group ID
   (`saved-session-<preset-id>`). Launching the same preset into two workspaces
   would replace the first workspace's live group.
6. `active_launch_options` is a global fallback, not per-workspace state.
7. `terminals.js` reads only `group` from the URL. Its group list, reorder
   request, Save Workspace request, and empty-window behavior have no workspace
   identity.
8. Launcher window names (`gridvibe-sessions`) and the pywebview bridge both
   enforce one terminal window.
9. The restore banner fetches only `/api/runtime-state` (the default slot) and
   restores every group into the global manager.
10. Closing the last group closes the single terminal window. With multiple
    windows, this must affect only the empty workspace window.

### Adjacent issue that restore work must not worsen

Open issue `ISSUE-2026-037` documents that the current restore path can expose a
decrypted SSH password to the browser and full request-body logging, and calls
a `201` record-creation response a successful restore before authentication
finishes. Multi-workspace restore should use a server-side restore contract and
credential redaction rather than multiplying the current client-side replay
path.

## Proposed MVP behavior

### Workspace identity and lifetime

- `"default"` remains the compatibility workspace for existing callers and
  URLs that omit a workspace ID.
- New workspaces receive an opaque backend-generated ID and a short
  user-facing label. IDs are validated and treated as opaque everywhere.
- A live workspace owns an ordered set of session groups and may have one open
  terminal window.
- Closing a workspace window does not close its live sessions; it can be
  reopened from the launcher.
- Closing or moving the last group out of a workspace makes that terminal
  window empty and closes it. The manager prunes that empty live workspace
  record after returning the source ID needed for the update event. A previously
  saved restore slot is not erased.
- App settings, theme preference, voice settings, and surface-mode defaults
  remain global. They are intentionally not copied into workspace records.

### Launching

Add a launcher destination control:

- an existing live workspace; or
- **New workspace**, with an optional label.

When no live workspace exists, **New workspace** is selected. When exactly one
exists, it is selected to preserve today's behavior. With several workspaces,
the last explicit launcher selection remains selected for that launcher
session.

`POST /api/sessions` accepts either `workspace_id` or `new_workspace: true`
plus an optional `workspace_label`, and returns the resolved `workspace_id`.
Omitting both continues to target `"default"`.

For a new destination, workspace creation and group insertion are one manager
operation. If request validation or session creation fails, the empty workspace
record is rolled back so it cannot appear as a dead launcher destination.

Launching a saved preset replaces the matching preset-backed group only inside
the selected workspace. The implementation must stop deriving global group
identity solely from `saved_session_id`; it should look up a group by
`(workspace_id, saved_session_id)` and preserve the group's opaque ID.

After launch, the launcher opens or focuses that workspace's window:

```text
/terminals?workspace=<workspace-id>&group=<group-id>
```

Legacy `/terminals` continues to mean the default workspace.

### One window per workspace

Each terminals page loads only the groups belonging to the workspace in its
URL. It may switch among those group tabs, but never discovers tabs from a
sibling workspace.

Browser mode uses one stable window name per workspace, for example
`gridvibe-workspace-<id>`, instead of the global `gridvibe-sessions` name.

Native mode replaces the singular session-window fields with dictionaries keyed
by workspace ID. Native window actions take an explicit workspace ID because a
shared pywebview bridge cannot safely infer which window called it:

- `open_workspace_window(workspace_id, group_id="")`
- `focus_workspace_window(workspace_id)`
- `close_workspace_window(workspace_id)`
- `toggle_workspace_fullscreen(workspace_id)`
- `exit_workspace_fullscreen(workspace_id)`
- `get_workspace_fullscreen_state(workspace_id)`

The window registry must also use unique keys such as
`workspace:<workspace_id>`; a set containing only `"session"` cannot track
multiple windows. Closing one workspace window removes only its own bridge
state. Closing the launcher keeps the current application-owner behavior and
shuts down the app.

The old singular bridge methods can remain as temporary wrappers for
`"default"` while frontend callers and tests migrate.

### Moving a session group

Provide both of the requested interactions:

1. Right-click a session-group tab to open an in-page context menu with
   **Move to Workspace** destinations and **Move to New Workspace**.
2. Drag a tab onto a workspace destination tray/popover in the same window.

Dragging directly between two operating-system windows is not part of the MVP.
Cross-window HTML drag/drop is inconsistent across browsers and embedded
WebView renderers. The in-window destination tray provides predictable drag/drop
while the context menu provides keyboard-accessible parity.

Before moving a group whose cached or visible Explorer panes contain unsaved
edits, use the existing in-page confirmation shell. No `window.confirm`,
`prompt`, or `alert` may be introduced.

The backend move is atomic under `SessionManager.lock`:

1. validate the source group and destination workspace;
2. change the group's workspace owner;
3. compact display order in the source workspace;
4. append the group to the destination workspace's order;
5. snapshot the result;
6. release the lock;
7. emit updates to the source and destination workspace rooms.

Terminal sessions retain their session IDs, group ID, processes, SSH
connections, replay buffers, and per-session Socket.IO rooms. The source window
evicts its cached view and selects another local group; the destination window
loads the moved group from the normal session APIs.

### Selective restore

Replace the single restore offer with a list of saved workspace summaries:

- checkbox;
- label;
- saved age/time;
- group count;
- pane count;
- disabled/live-conflict state when the same workspace ID is already active.

The user can restore any subset. Each selected saved workspace keeps its
original ID and opens in its own window. Unselected slots remain saved and can
be restored later.

Use a server-side restore endpoint rather than returning decrypted preset
credentials to the browser:

```http
POST /api/runtime-state/restore
Content-Type: application/json

{
  "workspace_ids": ["default", "ab12cd34ef56"]
}
```

For each selected workspace, the server:

- loads the password-free snapshot;
- rejects an already-live workspace instead of duplicating it;
- resolves a matching saved preset and credential server-side where valid;
- relaunches each group into that workspace through one extracted launch
  service shared with `POST /api/sessions`;
- returns per-workspace and per-group results;
- describes `201`-equivalent outcomes as **relaunch started**, not
  **connection established**.

Partial failure leaves successful workspaces running and returns retryable
details for failed workspaces. The launcher opens windows only for workspaces
whose relaunch started. Authentication results continue to arrive through
room-scoped session status events and existing retry UI.

## Backend contracts

### Live model

Add a small `Workspace` dataclass and workspace ownership:

```python
@dataclass
class Workspace:
    workspace_id: str
    label: str
    created_at: float = field(default_factory=time.time)

@dataclass
class SessionGroup:
    ...
    workspace_id: str = "default"
```

Recommended `SessionManager` methods:

- `create_workspace(label="", workspace_id=None)`
- `get_workspace(workspace_id)`
- `get_all_workspaces()`
- `get_workspace_groups(workspace_id)`
- `get_workspace_sessions(workspace_id)`
- `find_saved_session_group(workspace_id, saved_session_id)`
- `move_group(group_id, target_workspace_id)`
- `reorder_groups(workspace_id, ordered_group_ids)`
- `snapshot_workspace(workspace_id)` and/or
  `snapshot_all_live_workspaces()`

Snapshots should be copied consistently under one manager lock, then serialized
to disk after releasing that lock. File I/O and Socket.IO emits must never occur
while holding `SessionManager.lock`.

### HTTP API

Add:

- `GET /api/workspaces` — live workspace summaries and group counts.
- `POST /api/session-groups/<group_id>/move` — move to
  `target_workspace_id`, or atomically create a new target workspace.
- `GET /api/runtime-state/workspaces` — saved restorable summaries without
  credentials or full pane configuration.
- `POST /api/runtime-state/restore` — restore selected workspace IDs
  server-side.

Extend:

- `POST /api/sessions` with workspace destination fields.
- `GET /api/session-groups?workspace_id=<id>`.
- `POST /api/session-groups/order` with `workspace_id`; only IDs owned by that
  workspace may be reordered.
- `GET /api/sessions?workspace_id=<id>` for launcher/diagnostic use.
- `GET`, `POST`, and `DELETE /api/runtime-state` to keep their existing
  single-slot compatibility while correctly filtering captures by workspace.

When both `group` and `workspace_id` are supplied, reject a mismatched group
instead of showing a sibling workspace's data after a move race.

### Socket.IO

Add `join_workspace` / `leave_workspace` events and rooms named from a
server-normalized ID, such as `workspace:<id>`.

`session_groups_updated` gains `workspace_id`, `reason`, and optional
`group_id`, and is emitted only to the affected workspace room. A move emits
after the manager lock is released to both source and destination rooms.

Per-session `session_status` and terminal-output rooms remain unchanged.

### Persistence

Keep runtime schema v2. Change capture so each slot includes only that
workspace's groups. Autosave should take one consistent snapshot of all live
workspaces, then update all non-empty slots in one sibling-preserving file
write. Empty live workspaces must continue to leave older saved slots intact.

No workspace record or API may persist or return a password. Remove or redact
full launch request-body logging as part of the server-side restore work.

## Frontend structure

This is a substantial new surface and should not regrow `terminals.js`.

- Add `web/static/js/workspaces.js`, loaded by launcher and terminals pages, for
  workspace identity, API calls, destination lists, move actions, saved
  workspace summaries, and browser/native window dispatch.
- Add `web/static/css/workspaces.css`, using only `tokens.css` variables, for
  the destination control, restore chooser, tab context menu, and drag targets.
- Keep `launcher.js` changes to launcher form collection, launch/restore
  orchestration, and page-specific messages.
- Keep `terminals.js` changes to page integration: current workspace ID,
  workspace-filtered group loading, cache eviction, unsaved-edit checks, and
  socket room joins.
- Add reusable in-page modal/menu shells to the templates. Icons must be
  stroke-style `currentColor` SVG.

This naming also helps resolve existing terminology debt: many frontend
functions call a session group a "workspace". New code should use
`workspace` only for the window-level container and `group` for a tab; old
helpers can be renamed gradually when touched.

## Implementation sequence

### Wave 1 — Live ownership and compatibility

1. Add `Workspace` and `SessionGroup.workspace_id`, defaulting to `"default"`.
2. Add workspace-filtered manager queries, per-workspace ordering, atomic move,
   and workspace-local saved-preset lookup.
3. Add thin workspace routes in `web/api.py`; put normalization, public payload,
   and restore/launch orchestration in a new canonical `web/workspaces.py`.
4. Make runtime capture and autosave partition groups correctly and snapshot
   manager state consistently.
5. Keep all omitted-workspace paths mapped to `"default"` so existing behavior
   and most tests remain valid.

This wave is backend-only and should be shippable without enabling the new UI.

### Wave 2 — Isolated workspace windows

1. Add `workspace` to terminal URLs and all group/session-list requests.
2. Add workspace Socket.IO rooms and scoped group-change events.
3. Convert browser window names and the native bridge to workspace-keyed
   windows and fullscreen/minimized state.
4. Make last-group behavior close only the corresponding empty workspace
   window.
5. Verify two windows can render and receive updates independently.

### Wave 3 — Launch and move UX

1. Add the launcher destination selector and **New workspace** path.
2. Add the workspace-aware browser/native open/focus helpers.
3. Add the session-tab in-page context menu.
4. Add the drag destination tray and atomic move call.
5. Confirm unsaved Explorer edits before evicting a moved group's frontend
   view, and show retryable failure UI.

### Wave 4 — Selective restore

1. Add saved-workspace summary listing and selection modal.
2. Extract one server-side group launch service used by normal launch and
   restore.
3. Rehydrate valid saved credentials only on the server and redact launch
   logging, resolving the relevant parts of `ISSUE-2026-037`.
4. Restore selected workspaces with per-workspace results and open one window
   for each successful relaunch.
5. Leave unselected and dismissed snapshots intact.

## Test plan

### Session manager

- omitted workspace IDs still use `"default"`;
- groups list and reorder independently per workspace;
- moving a group retains all session IDs and normalizes both tab orders;
- moving the final group produces an empty source workspace result;
- the same saved preset can run once in each workspace without replacement;
- check-then-act move and order operations occur in one lock hold.

### Runtime state

- each saved slot contains only its own groups;
- one autosave tick captures multiple live workspaces without cross-copying;
- sibling slots and unselected slots survive save, clear, and restore;
- empty workspaces do not erase their previous slot;
- legacy v1 and current default-slot behavior still migrate/load;
- no password or other secret appears in snapshots or restore responses.

### API and Socket.IO

- workspace creation/launch validation and rollback on failed launch;
- filtered workspace/group/session responses and mismatch rejection;
- move to existing/new workspace, unknown IDs, and no-op moves;
- per-workspace reorder rejects foreign group IDs;
- source and destination rooms receive a move event; unrelated rooms do not;
- preset replacement is workspace-local;
- restore conflicts, partial results, credential mismatch/failure, and request
  log redaction.

### Browser/native window bridge

- opening one workspace twice focuses/reuses one window;
- two workspace IDs create two windows with distinct URLs;
- closing one workspace window preserves the other window and launcher;
- fullscreen, minimized state, theme application, and close callbacks target
  the correct workspace;
- closing the launcher retains current full-app shutdown behavior;
- browser fallback uses stable per-workspace window names.

### Frontend/manual checks

- launch into existing and new workspaces;
- right-click and drag/drop moves in dark and light themes;
- dirty Explorer editor confirmation, cancel, and retry paths;
- source tab fallback and destination tab appearance after a move;
- subset restore, partial failure, and later restore of an unselected slot;
- two simultaneous browser windows and two simultaneous native windows;
- moving a live SSH/local group without reconnecting or losing output.

Run the full repository checks after implementation:

```powershell
python tests/run_tests.py
python -m ruff check .
```

## MVP acceptance criteria

1. A user can launch two workspaces and receives two distinct terminal windows.
2. Each window lists only its own session-group tabs.
3. A group can move through the tab context menu or drag destination tray
   without restarting its terminal sessions.
4. Both windows update promptly after a move, and an unrelated workspace does
   not receive the event.
5. The same saved session can be launched in two workspaces independently.
6. Autosave writes correct non-overlapping slots.
7. After restart, the launcher can restore any selected subset, one window per
   restored workspace, while leaving other slots available.
8. Existing single-workspace/default URLs and launch behavior continue to work.
9. Restore does not disclose or log credentials and does not claim an
   asynchronous connection is already established.
10. The full unittest and Ruff checks pass.

## Explicit non-goals for the first release

- dragging a tab directly between operating-system windows;
- moving an individual pane between groups;
- persisting window bounds, monitor placement, or fullscreen state;
- workspace rename/delete/archive management beyond labels assigned at create
  and restore time;
- workspace-specific app settings or themes;
- sharing live workspaces across multiple GridVibe backend processes;
- changing the read-only Explorer contract.

These can be added after the workspace ownership, filtering, and window registry
contracts are stable.
