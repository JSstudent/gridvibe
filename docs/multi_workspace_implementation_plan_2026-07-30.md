# Multi-workspace implementation plan (staged)

Date: 2026-07-30
Updated: 2026-07-31
Status: Stages 1–4 implemented, post-test corrections applied in two rounds
(2026-07-31); §8 amended by round 2
Scope: Multiple live workspaces, one terminal window per workspace, moving a
session group between workspaces, and selective restore after restart.

This revision keeps the 07-28 investigation's conclusions but rewrites the plan
as **four sequenced stages with explicit entry/exit criteria**, resolves the
open questions raised against it (window chrome, duplicate tabs, restore edge
cases), and re-grounds every claim in the code as it stands today.

---

## 1. What changed against the 07-28 proposal

| # | Question | Decision in this plan |
|---|---|---|
| 1 | Do new workspace windows get the **Sessions…** and **Workspace…** menus? | **Yes, automatically** — both live in `templates/terminals.html:33-90`, and every workspace window is the same page. The work is *scoping* them to the window's workspace, plus new Workspace-menu items (§7). |
| 2 | The same saved session live in two workspaces? | **Disabled for this release.** A saved-preset-backed group is live in **at most one workspace at a time** (§6). This deletes the riskiest part of Stage 1 and keeps `saved-session-<id>` globally unique, exactly as today. |
| 3 | Restoring a workspace whose tabs reference deleted/changed presets, missing directories, already-live groups… | Fully enumerated as a **12-row edge-case matrix with defined outcomes** (§9); every row degrades to a per-group result, never to a failed workspace or a silent wrong launch. |
| 4 | Drag a tab onto a destination tray | Kept, but **demoted to the last optional step of Stage 3**, behind the same single `moveGroupToWorkspace()` call as the context menu. Dropping it changes no contract. |
| 5 | With N slots, is "Dismiss" still enough? Do we need **Forget**? | **Yes — three verbs at two levels** (§9.1): Restore and Forget are per row, Dismiss stays panel-level ("not now"). Forget needs **no new backend** — it wires up the already-written, caller-less `DELETE /api/runtime-state` → `clear_workspace()`. |
| 6 | Rename a workspace | **In scope** (Stage 3). The label already exists in the slot schema; renaming is a few lines and the absence of it makes the feature feel unfinished. Delete/archive stay non-goals. |
| 7 | Shipping safety | Everything lands behind one wired runtime flag, `workspace.multi_workspace_enabled` (§10.5). Stages 1–2 ship dark. |

Two single-workspace assumptions the 07-28 plan missed, added here:

- **`SessionManager._active_group_id` is global** (`sessions/manager.py:151`). It
  is the "which tab was in front" restore hint. With two windows, the second
  window's tab switch overwrites the first window's hint. It must move onto the
  workspace record (§10.1).
- **`active_launch_options` leaks into `GET /api/sessions`** as a fallback
  (`web/api.py:489`, `:735`, `:514`). With two windows, a window whose group
  vanished mid-request can render another workspace's last-launch layout. A
  request that names a workspace must never fall back to it (§10.2).

---

## 2. Model and vocabulary

```text
Workspace  (one OS window, one row in runtime_state.json "workspaces")
└── SessionGroup  (one session tab)
    └── TerminalSession  (one pane: terminal / agent / explorer / browser)
```

Moving a group changes **only** `SessionGroup.workspace_id` and two display
orders. It never recreates sessions, closes SSH connections, restarts local
processes, or changes per-session Socket.IO rooms.

**Naming rule (enforced in review).** The frontend currently uses "workspace"
for three different things — the window (new), the pane grid
(`buildActiveWorkspaceLayoutSnapshot`, `getWorkspacePanesInVisualOrder`,
`terminals.js:1672`, `:1710`), and the saved snapshot. From this plan on:

| Concept | Name |
|---|---|
| The window-level container | `workspace` |
| A session tab | `group` |
| The pane grid inside a tab | `paneGrid` / `layout` |

New code uses these strictly. Existing `*Workspace*` helpers that mean "pane
grid" are renamed **only when otherwise touched**, to keep diffs reviewable.

---

## 3. Verified baseline (what exists today)

Reusable as-is:

- `web/runtime_state.py` is already schema v2 with a `workspaces` dict,
  sibling-preserving writes, labels, origins, `active_group_id`, and
  `native_zoom_factor` (`:31`, `:182`, `:258`, `:291`).
- Snapshots are password-free by construction (`_SESSION_SNAPSHOT_FIELDS`,
  `runtime_state.py:55`).
- Per-session Socket.IO rooms (`join_session`, `web/api.py:2455`) already carry
  terminal output and status. Nothing about them changes.
- Both launch paths already accept a starting group via `/terminals?group=<id>`
  (`launcher.js:2325`, `webview_launcher.py:801`).
- `templates/terminals.html` already ships the **Sessions…** / **Workspace…**
  menus and a `genericConfirmModal` shell for in-page confirms.

Single-workspace assumptions that must change:

| # | Location | Assumption |
|---|---|---|
| 1 | `sessions/manager.py:111`, `:458`, `:484` | `SessionGroup` has no owner; `display_order` and `reorder_groups()` are global. |
| 2 | `sessions/manager.py:151`, `:199` | The active-group hint is global. |
| 3 | `web/runtime_state.py:182` | `capture_workspace(workspace_id=...)` captures **every** live group regardless of the id it was asked for. |
| 4 | `web/runtime_state.py:306` | `iter_live_workspaces()` maps all live groups to `"default"`. |
| 5 | `web/api.py:1461`, `:1468`, `:1481`, `:715` | Group/session/order/active routes are global. |
| 6 | `web/api.py:489`, `:514`, `:735` | `active_launch_options` is a global fallback. |
| 7 | `web/terminal_io.py:100` | `session_groups_updated` is emitted to **every** socket with no workspace id. |
| 8 | `web/static/js/terminals.js:514`, `:6554` | The page reads only `group` from the URL and loads all groups. |
| 9 | `launcher.js:2325`, `templates/index.html:129`, `browser-pane.js:431` | One browser window name, `gridvibe-sessions`. |
| 10 | `web/webview_launcher.py:515`, `:801`, `:895`, `:1144` | One `_session_window`; the native registry is a `set` of kinds containing `"session"`. |
| 11 | `launcher.js:2372`, `:2452` | Restore fetches only the default slot and replays it client-side. |
| 12 | `terminals.js:6882` | The last closed group closes *the* window. |

Adjacent open issue: **ISSUE-2026-037** (`docs/testing_issues.md:36`) — restore
returns a decrypted preset password to the browser, `POST /api/sessions` logs
the full request body (`web/api.py:1811`), and a `201` record-creation is
reported as a successful restore. Stage 4 resolves these rather than
multiplying the client-side replay path across N workspaces.

---

## 4. Stage overview

| Stage | Theme | Ships | Reversible? |
|---|---|---|---|
| **1** | Live ownership + persistence partitioning | Backend only, no UI change, flag off | Yes — every path defaults to `"default"` |
| **2** | Isolated windows | Two real windows, flag on in dev only | Yes — flag off restores single-window |
| **3** | Launch destination, workspace menus, move | User-visible multi-workspace | Flag off hides UI; backend stays valid |
| **4** | Selective restore (server-side) | Restore chooser + ISSUE-2026-037 fixes | Flag off falls back to the single-slot banner |

Each stage ends with `python tests/run_tests.py` + `python -m ruff check .`
green, and is independently mergeable.

### Implementation record — 2026-07-31 (Stages 1–2)

Stages 1 and 2 are implemented behind
`workspace.multi_workspace_enabled` (default `false`):

- `SessionManager` owns a permanent default workspace plus independent
  non-default workspace records, per-workspace active groups and compact display
  orders. Moving a group preserves its live sessions and ids.
- Runtime capture is partitioned and password-free. One autosave tick takes one
  consistent manager snapshot and performs one sibling-preserving file write.
  Restorable summaries expose counts and metadata only.
- The read/order/active HTTP contracts are workspace-scoped, reject foreign
  groups, and never use `active_launch_options` for an explicitly named
  workspace. Legacy callers still resolve to `"default"`.
- Workspace Socket.IO rooms isolate group-list invalidation events. Browser and
  native windows have stable per-workspace identities; native fullscreen, zoom,
  close, theme and download-dialog targeting are keyed by workspace id.
- Focused coverage lives in `tests/test_multi_workspace.py`, with manager and
  native-window coverage in `tests/test_session_manager.py` and
  `tests/test_webview_launcher.py`. Existing API/config/markup assertions were
  extended in `tests/test_api.py`.

Implementation exposed four requirements for the remaining stages:

1. A stable saved-group conflict must be resolved **before**
   `_replace_group_sessions()` runs. Replacing first would destroy the source
   workspace's live sessions and only then discover the ownership conflict.
2. A UI-created empty workspace needs an explicit transient
   `retain_when_empty` policy. Absence of groups alone cannot distinguish it from
   a workspace emptied by close or move.
3. A shared native `js_api` cannot infer which window invoked any method,
   including `save_download`; every new bridge call must carry the workspace id.
4. Restore must create the live workspace with the saved slot's exact id and
   roll it back if no group starts. Autosave already preserves a slot's
   `origin: "manual"` so the Stage 4 auto-slot cap can never demote or evict a
   manual save.

### Implementation record — 2026-07-31 (Stages 3–4)

Stages 3 and 4 are implemented, still behind
`workspace.multi_workspace_enabled` (default `false`). The flag hides UI only;
ownership checks, the uniqueness guard, and every validation path run either
way.

**Backend.** `web/workspaces.py` grew from an identity helper into the
orchestration module the plan asked for: destination resolution
(`resolve_launch_destination`, `rollback_created_workspace`), the §6 guard
(`saved_session_conflict`), the **one** launch service (`launch_session_group`),
the move (`move_group_to_workspace`), and server-side restore
(`restore_workspace`, `restore_workspaces`, `list_restorable_workspace_summaries`).
`sessions/manager.py` imports this module at import time, so every collaborator
that leads back to the manager (`web.app`, `web.terminal_io`,
`web.saved_sessions`) is imported lazily inside the function that needs it — the
cycle exists only at import time. `web/api.py` keeps thin routes; the old
~210-line `POST /api/sessions` body is deleted, not duplicated.

**Manager.** `Workspace.retain_when_empty` (live-only, never persisted),
`rename_workspace()`, and `remove_workspace()` — the rollback for a destination
created for a launch that then failed. Creating a group in, or moving one into,
a workspace clears its retention flag, after which normal empty-workspace
pruning applies again. `"default"` stays permanent.

**Routes.** `GET`/`POST /api/workspaces`, `PATCH /api/workspaces/<id>`,
`POST /api/session-groups/<group_id>/move`,
`GET /api/runtime-state/workspaces`, `POST /api/runtime-state/restore`, and
`DELETE /api/runtime-state` returning `{"forgotten": bool}` (`409` while that
workspace is live). `POST /api/sessions` accepts `workspace_id` **or**
`new_workspace` + `workspace_label` and returns `workspace_id` +
`workspace_created`.

**Frontend.** New `web/static/js/workspaces.js` and
`web/static/css/workspaces.css`, loaded by both pages: workspace identity, every
workspace API call, destination lists, the single `moveGroupToWorkspace()`, and
browser/native window dispatch. The duplicated dispatch previously inlined three
times in `launcher.js` now calls `openWorkspaceWindow()` /
`focusWorkspaceWindow()`. `launcher.js` keeps form collection and launch
orchestration (destination select, live-workspace list, restore chooser);
`terminals.js` keeps page integration (workspace menus, tab context menu, cache
eviction, move flow). Every confirm goes through `openGenericConfirmModal(...)`
and naming through the new in-page `workspaceNameModal` — no `window.confirm`,
`prompt`, or `alert` anywhere. Icons are stroke `currentColor` SVG; colours come
from `tokens.css` variables only.

**Decisions taken during implementation** (not spelled out in the plan above):

1. **"Already live" means *has groups*, not *record exists*.** The `"default"`
   workspace record is permanent, so an existence check would have made the
   default slot permanently unrestorable and permanently un-forgettable.
   `workspace_has_groups()` is the single predicate behind R5, R12, and the
   Forget refusal.
2. **Moving the last group out closes the source window** (§8) rather than
   leaving it on a pruned workspace. The move response carries `source_groups`,
   so the source window decides without re-listing a workspace the backend has
   already removed — re-listing would `400`.
3. **Rename also refreshes the saved slot's label** through `capture_workspace`,
   so the restore chooser stops offering the old name. An empty workspace has no
   slot and is skipped.
4. **`build_sessions_from_saved_config()` is a deliberate server-side twin** of
   `buildSessionsFromConfig()` in `launcher.js`, including the directory
   resolution mirrored from `shared.js`. Restore needs the preset expanded
   in-process (that is the whole point of ISSUE-2026-037), and both twins feed
   the same launch service, so a restored pane and a launched pane are built
   from the same fields.
5. **The `409` conflict has two callers with different affordances.** From the
   launcher the honest action is *Open it* (the launcher has no workspace of its
   own to move into); from a terminal window it is *Move it here* with *Open
   that workspace* as the cancel path. Both route through the same endpoints.

**ISSUE-2026-037 is closed by Stage 4.** Decrypted passwords never leave the
process, `_redacted_launch_summary()` replaced the full request-body log
(reporting shapes and a boolean `credentials_supplied`), and the response says
*Relaunch started*.

**Coverage.** `tests/test_multi_workspace.py` gains
`MultiWorkspaceStage3TestCase` (destination, rollback, the full §6 table, move
ordering/pruning/rooms, rename, retention lifetime, flag-gated markup) and
`MultiWorkspaceRestoreTestCase` (all fourteen §9.3 rows, subset restore, Forget
idempotency and the live refusal, the auto-slot cap, credential and log
redaction, and an assertion that restore and launch share one code path).
`tests/test_session_manager.py` gains `WorkspaceLifetimeTestCase`. Suite: 972
tests, Ruff clean.

**Not shipped (unchanged from the plan).** The optional drag destination tray
(§5 Stage 3 step 9) was cut; it shares the same `moveGroupToWorkspace()` call,
so adding it later changes no contract. Tab drag-and-drop still reorders within
a window only.

### Post-test corrections — 2026-07-31 (`docs/r&d/todos.txt`)

Testing Stages 1–4 with the flag on produced six findings. All are fixed; none
changed a contract in §10.

1. **The flag had no control.** §10.5 wired `multi_workspace_enabled` end to end
   but stopped at the page, so it could only be changed by editing
   `config.json` and restarting — a guardrail-5 half-wire in practice. It is now
   an App Settings checkbox below the auto-save interval. Changing it applies
   immediately: both pages render the mode from server-side markup, so the
   saving window reloads itself and every other window follows the existing
   app-config broadcast. Switching it **off** confirms in page and then calls
   the new `POST /api/workspaces/close-extra` → `close_extra_workspaces()`,
   which closes every workspace but `"default"` (sessions, groups, record) —
   with the flag off no window can reach them, so leaving them running would
   strand live shells. That teardown deliberately writes **no** snapshot: a
   capture taken during a teardown races the state it is recording, so
   restorability is exactly what autosave or Save Workspace already wrote.
   `retain_when_empty` is a promise for the lifetime of the mode, not past it.
2. **The launcher grid collapsed with the chooser open** (`docs/images/launcher_display_broken.png`).
   Root cause: `.card { min-height: 0 }` removes each grid item's automatic
   minimum, so the right column's `1fr` row could be squeezed to nothing and
   **Terminal Setup** rendered as a clipped header strip. Three fixes:
   `.column-right` declares its third row (the Workspaces card), both content
   cards have a height floor so the column scrolls instead of collapsing, and
   the chooser became a dialog so its height never reflows the grid at all.
3. **The destination was not visibly connected to Launch.** The `<select>` in
   card 04 is gone; the CTA is a split button that names its destination, with a
   caret opening an in-page picker (live workspaces + **New workspace…**, which
   uses the shared `workspaceNameModal`). Card 04 keeps only what already
   exists — the live workspaces and the entry back into the chooser. The
   launcher also stopped going stale: it has no Socket.IO connection, so a
   workspace window relays its room events (plus rename, which emits none) over
   a `gridvibe.workspaces` broadcast, and the launcher re-reads both lists on
   that and on window focus. No polling was added.
4. **View Active Terminals always opened `"default"`.** It now follows the
   launch destination, then the only populated workspace, then `"default"`.
5. **"Saved workspaces (N)" read as a duplicate of the live list** it sat near.
   Renamed **Reopen saved… (N)** and moved into the Workspaces card header,
   beside the live list it is explicitly not.
6. **A window did not say which workspace it was.** `CURRENT_WORKSPACE_LABEL` is
   server-rendered and leads the session line and the window title; a rename
   updates both without a reload.

One real bug surfaced while testing (1): a window reacted to the broadcast it
sent itself. A `BroadcastChannel` never delivers to the object that posted, but
it does deliver to any *other* channel object in the same document, and every
sender here opens a fresh channel per message. Harmless for the idempotent
app-config updates that existed before; not harmless for one that reloads the
window, which cut off the teardown the sender was still running and left the
workspaces alive. Payloads now carry `source: GRIDVIBE_WINDOW_ID` (shared.js)
and every listener skips its own.

Coverage: `MultiWorkspaceModeToggleTestCase` in `tests/test_multi_workspace.py`.
Suite: 982 tests, Ruff clean.

### Post-test corrections, round 2 — 2026-07-31 (`docs/r&d/todos.txt`)

A second pass with the flag on produced five findings. Four are chrome; one
changes a §8 lifetime rule and is called out as an amendment below.

1. **Two confirmations could not be clicked** (todos 1 and 3 — one root cause).
   **Forget** from the restore chooser and *"Turn off multiple workspaces?"*
   from App Settings both painted *behind* the dialog that asked for them. Each
   page stacks its own `.modal-shell` dialogs (`z-index: 20` on the launcher,
   `12000` on the workspace page) and both share the App Settings dialog
   (`12000`), so the top of the stack was decided by document order — and
   `genericConfirmModal` sits earlier in both templates than the chooser and the
   App Settings include. `#genericConfirmModal` and `#workspaceNameModal` are
   always the *response* to something already open, so they now lead the stack
   outright (`12100`) rather than depending on markup order. One rule, in
   `workspaces.css`, which both pages load last (an id beats a class).
2. **View Active Terminals is hidden with the flag on.** Round 1 made it follow
   the launch destination; that still left a button naming *one* live workspace
   directly beside the Workspaces card that lists them all with their own
   **Open** buttons — a second, worse copy of the same control. It stays exactly
   as it was with the flag off. `viewActiveTerminalsWorkspaceId()` went with it
   (guardrail 5), and the flag-off restore banner passes its own id.
3. **The save confirmation never gave the session line back.** It is written
   into `#sessionLabel`, which is otherwise only rewritten by
   `updateSessionChrome()` on a tab switch — so with a single tab open the
   confirmation stayed in the window chrome for the rest of the session, reading
   as part of the workspace name. It now clears after 6s. The line has two
   shapes (live chrome and the empty state) and a *deferred* rewrite can outlive
   the grid it described, so both shapes moved behind one `renderSessionLine()`
   that `resetSessionView()` also uses.
4. **§8 amendment — emptying a non-default workspace removes it globally.**
   §8 said the live record is pruned but "the saved slot is not erased — it
   stays restorable". In use that made the launcher contradict itself: the
   workspace vanished from the Workspaces card while **Reopen saved…** still
   offered it, and accepting that offer resurrected a workspace the user had
   just emptied. Closing the last group, or moving it out, now also clears the
   saved slot through the new `forget_pruned_workspaces()` in `web/workspaces.py`,
   fed by the `pruned_workspace_ids` both close routes and the move already
   returned. Two exclusions are deliberate: `"default"` is permanent and never
   reaches this path, so single-workspace restore-after-restart is unchanged;
   and `close_extra_workspaces()` (leaving the mode) removes its workspaces
   *without* pruning them here, so its promise that everything already captured
   stays restorable still holds. A failed restore's rollback
   (`restore_workspace`) also stays out — it removes the live record it just
   created and must never delete the snapshot it failed to open.
   The emptied window announces the change itself before closing
   (`notifyWorkspacesChanged('workspace_emptied')`): the room event that would
   normally relay it races the window teardown, and a launcher that missed it
   would keep listing a workspace that no longer exists in either list.

Coverage: `WorkspaceEmptiedRemovalTestCase` and
`MultiWorkspaceDialogChromeTestCase` in `tests/test_multi_workspace.py`.
Suite: 991 tests, Ruff clean.

---

## 5. The stages

### Stage 1 — Live ownership and persistence partitioning

**Implementation status (2026-07-31): Complete.**

**Goal.** The backend can own N workspaces correctly. Nothing in the UI changes.

**Entry.** None.

**Work.**

1. **Model** (`sessions/manager.py`)
   - `@dataclass Workspace: workspace_id, label, created_at, active_group_id=""`.
   - `SessionGroup.workspace_id: str = "default"`, included in `to_dict()`.
   - `SessionManager.workspaces: Dict[str, Workspace]`, seeded with a permanent
     `"default"` record. Remove the global `_active_group_id`.
2. **Id normalization** — one helper, `normalize_workspace_id()`: accepts
   `"default"` or `^[a-z0-9]{12}$`; anything else raises. Generated ids come from
   `uuid.uuid4().hex[:12]`, matching the existing group-id convention
   (`manager.py:172`). Ids are opaque everywhere; **the Socket.IO room name is
   derived only from a normalized id**.
3. **Manager methods** (all check-then-act inside one `self.lock` hold):
   `create_workspace`, `get_workspace`, `get_all_workspaces`,
   `get_workspace_groups`, `get_workspace_sessions`, `move_group`,
   `reorder_groups(workspace_id, ids)`, `set_active_group(workspace_id, id)`,
   `get_active_group_id(workspace_id)`, `find_saved_session_group(saved_id)`
   (global — see §6), `snapshot_live_workspaces()`.
4. **Ordering** — `display_order` is compacted **per workspace**. `create_group`
   takes `workspace_id` and computes `max(order within that workspace) + 1`.
   `move_group` compacts the source order and appends to the destination.
5. **Pruning** — `clear_disconnected_sessions()` (`manager.py:624`) also drops
   workspace records with no groups, using the same
   `EMPTY_GROUP_GRACE_SECONDS` guard, **except `"default"`, which is permanent**.
   It returns the pruned workspace ids so the caller can emit *after* the lock.
6. **Persistence** (`web/runtime_state.py`)
   - `capture_workspace()` iterates `get_workspace_groups(workspace_id)`, not
     `get_all_groups()` — this is the actual bug fix behind assumption 3.
   - `iter_live_workspaces()` yields every live workspace that has groups.
   - New `capture_live_workspaces()`: take **one** consistent snapshot of all
     live workspaces under one manager lock hold, release it, then do **one**
     sibling-preserving file write. Today's autosave tick
     (`web/api.py:1574`) does one read-modify-write *per workspace*; with N
     workspaces that is N file rewrites per tick (guardrail 3) and N chances to
     interleave.
   - New `list_restorable_workspaces()` → summaries only
     (`workspace_id`, `label`, `origin`, `saved_at`, `group_count`,
     `pane_count`), no launch config, no credentials.
   - `clear_workspace()` is unchanged and finally has a caller.
7. **Backend orchestration module** — new canonical `web/workspaces.py` holding
   normalization and the public workspace payload. Stage 3 adds destination
   resolution and Stage 4 adds the shared launch service there. `web/api.py`
   gets thin routes only (guardrail 6 — do not regrow the monolith).
8. **Compatibility** — every route/manager entry point that omits a workspace id
   resolves to `"default"`. Existing tests must pass **unmodified** except where
   they assert a payload that gains a `workspace_id` key.

**Exit criteria.**

- Two workspaces created in one manager list, order, and snapshot independently.
- `capture_workspace("a")` contains only workspace `a`'s groups; slot `b`
  is byte-identical before and after.
- One autosave tick writes `runtime_state.json` exactly once for N workspaces.
- No `socketio.emit` and no file I/O occurs while `SessionManager.lock` is held
  (guardrail 2), verified by review and by the lock-ordering tests.
- Full suite + ruff green with **zero UI changes**.

**Explicitly not in Stage 1.** Any route surface change beyond optional
`workspace_id` parameters; any frontend file; any window change.

**Implemented notes.** Forced removal of a last group prunes an empty
non-default workspace immediately; background cleanup uses the existing grace
period. The default workspace is never pruned. Autosave preserves an existing
manual slot's origin while refreshing its live contents.

---

### Stage 2 — Isolated workspace windows

**Implementation status (2026-07-31): Complete.**

**Goal.** Two windows exist, each showing only its own tabs, each updating
independently. Still no new UI controls — reached by hand-typed URL and by the
flag in dev.

**Entry.** Stage 1 merged.

**Work.**

1. **URL identity** — `/terminals?workspace=<id>&group=<id>`. Legacy
   `/terminals` and `/terminals?group=<id>` mean the default workspace. When
   both are supplied and the group is **not** owned by that workspace, the page
   is served but the group is ignored and the window uses its existing local
   selection policy (currently the newest group by display order);
   `GET /api/session-groups?workspace_id=` never returns a foreign group. (This
   is the post-move race: a stale URL must never render a sibling workspace's
   data.)
2. **Filtered reads** — `GET /api/session-groups?workspace_id=`,
   `GET /api/sessions?workspace_id=`, `POST /api/session-groups/order` with
   `workspace_id` (rejects foreign group ids with `400`),
   `POST /api/session-groups/active` with `workspace_id`.
   **A request that names a workspace never falls back to `active_launch_options`**
   — it returns that workspace's real (possibly empty) state.
3. **Socket rooms** — `join_workspace` / `leave_workspace`, room
   `workspace:<normalized id>`. `session_groups_updated` gains
   `{"workspace_id", "reason", "group_id"}` and is emitted **only** to the
   affected room, after every lock is released. A move emits to the source room
   and the destination room. Per-session rooms are untouched.
4. **Browser windows** — window name becomes `gridvibe-workspace-<id>`
   (`launcher.js:2325`, `templates/index.html:129`). `browser-pane.js:431`'s
   comment about `gridvibe-sessions` is updated so the "don't stack tabs"
   rationale still reads true.
5. **Native windows** (`web/webview_launcher.py`)
   - `_session_window` / `_session_window_group_id` / `_session_is_fullscreen`
     / `_session_window_minimized` become dicts keyed by workspace id.
   - Registry key becomes `workspace:<id>` — a `set` holding `"session"`
     (`:1144`) cannot track two windows.
   - New bridge methods, each taking an explicit workspace id because a shared
     `js_api` object cannot infer its caller: `open_workspace_window`,
     `focus_workspace_window`, `close_workspace_window`,
     `toggle_workspace_fullscreen`, `exit_workspace_fullscreen`,
     `get_workspace_fullscreen_state`, `get_workspace_native_zoom`.
   - The singular methods stay as **thin wrappers targeting `"default"`** so
     `launcher.js` and `tests/test_webview_launcher.py` keep working during the
     stage; they are removed at the end of Stage 3 together with their callers.
   - `_should_exit_after_window_close` (`:423`) keeps its meaning: the launcher
     owns the app lifecycle; closing one workspace window closes only its own
     bridge state.
    - Native frame theme, zoom, and close callbacks are applied per window.
    - Shared bridge operations such as `save_download` also take an explicit
      workspace id; caller identity is never inferred from the shared `js_api`.
6. **Last-group behavior** — `terminals.js:6882` closes only *this* workspace's
   window, and only when the user closed the last group **in that window**. A
   workspace created deliberately empty (Stage 3) is never auto-closed.
   Closing the window does **not** close sessions; the workspace stays live and
   reopenable.

**Exit criteria.**

- Two browser windows and two native windows render different tab sets from one
  backend, simultaneously.
- A change in workspace A produces exactly one event, in room
  `workspace:A`; a socket joined only to `workspace:B` receives nothing.
- Closing window A leaves window B and the launcher untouched; closing the
  launcher still shuts the app down.
- A stale `?group=` from a sibling workspace renders that window's own tabs,
  never the foreign group.

**Explicitly not in Stage 2.** Creating workspaces from the UI, moving groups,
menu changes, restore changes.

**Implemented notes.** `POST /api/sessions` remains a default-workspace launch
until Stage 3, but already returns `workspace_id`. The Stage 2 client dispatch,
identity, filtered loading and room-join logic is functional in the existing
files; Stage 3 extracts shared pieces into `workspaces.js` instead of
reimplementing them.

---

### Stage 3 — Destination, workspace menus, and move

**Implementation status (2026-07-31): Complete.**

**Goal.** The feature becomes usable: launch into a chosen workspace, manage
workspaces from inside a window, move a tab between workspaces.

**Entry.** Stage 2 merged; flag on in dev.

**Work.**

1. **Launcher destination control** (`launcher.js`, `index.html`)
   - A select: each live workspace by label, plus **New workspace** with an
     optional label field.
   - Default: **New workspace** when none is live; the single live workspace
     when exactly one exists; otherwise the last explicit choice for this
     launcher session.
   - A **Live workspaces** list with an Open/Focus button per workspace.
2. **`POST /api/sessions` destination** — accepts `workspace_id`, **or**
   `new_workspace: true` + optional `workspace_label`; returns the resolved
   `workspace_id` (the response field already exists from Stage 2). Omitting
   both keeps targeting `"default"`.
   Workspace creation and group insertion are one manager operation; if
   validation or session creation fails, the new workspace record is rolled
   back so a dead destination can never appear in the picker
   (mirrors the existing `remove_group` rollback at `web/api.py:1960`).
3. **Uniqueness guard** — see §6. `409` with a structured conflict body, and an
   in-page **Open it / Move it here** affordance (guardrail 8: failure states
   need a retry affordance). For stable `saved-session-<id>` groups,
   `find_saved_session_group()` and destination ownership resolution run
   **before** `_replace_group_sessions()`. Replace-in-place is allowed only
   after the existing group is proven to belong to the resolved target.
4. **Workspace menus in every window** — full spec in §7.
5. **Move** — `POST /api/session-groups/<group_id>/move` with
   `{"target_workspace_id"}` or `{"new_workspace": true, "label": ""}`.
   The manager's Stage 1 `move_group()` performs the ownership and ordering
   mutation atomically under `SessionManager.lock`; the thin route resolves a
   destination, calls it, and emits after the lock. Sequence:
   1. validate source group + destination (or create destination);
   2. reassign `group.workspace_id`;
   3. compact the source workspace's order;
   4. append to the destination's order;
   5. clear the source workspace's `active_group_id` if it named this group;
   6. snapshot both workspaces' resulting group lists;
   7. **release the lock**;
   8. emit `session_groups_updated` to both rooms with `reason: "moved"`.
   Terminal sessions keep their ids, processes, SSH connections, replay
   buffers, and per-session rooms. The source window drops its cached view and
   selects another local tab; the destination window loads the tab through the
   normal session APIs.
6. **Unsaved-edit confirm** — before the source window evicts a moved group's
   cached view, if any Explorer pane in it has a dirty editor buffer, confirm
   through `openGenericConfirmModal(...)`. **No `window.confirm` / `prompt` /
   `alert` anywhere** — WebView2 blocks them and
   `GuardrailAuditFixesTestCase` enforces it (guardrail 4).
7. **Deliberately empty lifetime** — add transient
   `Workspace.retain_when_empty: bool = false`. `POST /api/workspaces` sets it
   for a UI-created empty workspace. Adding or moving in the first group clears
   it, after which normal last-group close/move pruning applies. It is live
   lifecycle state only and is not written to `runtime_state.json`.
8. **Frontend structure** (guardrail 6)
   - New `web/static/js/workspaces.js`, loaded by both pages: workspace
      identity, workspace API calls, destination lists, move actions,
      browser/native window dispatch.
   - New `web/static/css/workspaces.css`, `tokens.css` variables only, no
     palette literals (guardrail 7).
   - Extract the Stage 2 dispatch/API helpers already present in `launcher.js`
     and `terminals.js`; do not introduce a parallel implementation.
     `launcher.js` keeps only form collection and launch orchestration;
     `terminals.js` keeps only page integration (current workspace id, filtered
     loading, cache eviction, room joins).
   - Every native bridge helper, including download dialogs, passes the current
     workspace id explicitly.
   - Icons are stroke-style `currentColor` SVG.
9. *(Optional, last)* **Drag destination tray** — a small in-window tray of
   workspace targets; dropping a tab calls the same `moveGroupToWorkspace()`.
   Cross-OS-window HTML drag/drop is **not** attempted: it is inconsistent
   across browsers and embedded WebViews. If this step is cut, nothing else
   changes.

**Exit criteria.**

- Launch into an existing workspace and into a new one; the correct window
  opens or is focused in both browser and native mode.
- A preset already live elsewhere produces the `409` conflict UI, never a
  silent steal or a duplicate tab.
- A live SSH group and a live local group each move between workspaces with no
  reconnect, no lost scrollback, and no pane re-creation.
- Both windows reflect the move promptly; a third workspace receives nothing.
- The dirty-editor confirm blocks, cancels, and proceeds correctly.

---

### Stage 4 — Selective, server-side restore

**Implementation status (2026-07-31): Complete.**

**Goal.** After a restart, restore any subset of saved workspaces, one window
each, without ever handing a credential to the browser.

**Entry.** Stage 3 merged.

**Work.**

1. **Summaries** — `GET /api/runtime-state/workspaces` returns
   the Stage 1 `list_restorable_workspaces()` result plus a per-row
   `live_conflict` flag when that workspace id is already active. This is a thin
   route; no launch config or credentials are returned.
2. **Chooser UI** — the launcher's single banner becomes a list with a checkbox,
   label, saved-age, group count, pane count, and a disabled state with the
   reason. Restore any subset; unselected slots stay saved. Per row: a
   **Forget** action (confirmed, destructive) wired to the existing
   `DELETE /api/runtime-state`. Panel-level: **Restore selected** and
   **Dismiss** ("not now"). Full semantics and rules in §9.1.
   The launcher also gains a **Saved workspaces (N)** entry that reopens the
   same chooser after a Dismiss.
3. **One launch service** — extract the body of `POST /api/sessions`
   (`web/api.py:1788`) into `web/workspaces.py` so normal launch and restore
   build panes through **one** code path. This is the fix for the current
   drift, where `buildRestoreGroupBody()` (`launcher.js:2405`) reimplements
   launch in JavaScript.
4. **`POST /api/runtime-state/restore`** with `{"workspace_ids": [...]}`.
   Per workspace, server-side: load the password-free slot → reject if already
   live → create the live workspace record with the slot's **exact saved id** →
   resolve the referenced preset and its credential **on the server** →
   relaunch each group through the shared service → return per-workspace and
   per-group results. If no group starts, remove the just-created live workspace
   so a failed restore does not become an `already_live` conflict on retry.
5. **ISSUE-2026-037** — decrypted passwords never leave the process; the
   full-request-body log at `web/api.py:1811` is redacted (log a field
   summary, never `password`); the response says **relaunch started**, not
   *connection established*. Authentication outcomes keep arriving through the
   existing room-scoped `session_status` events and the existing retry UI.
6. **Windows** — the launcher opens a window only for workspaces whose relaunch
   actually started, and only after the response.
7. **Slot cap** — apply `MAX_AUTO_WORKSPACE_SLOTS` at the single
   `capture_live_workspaces()` write point. Stage 1 already preserves an
   existing manual slot's origin during autosave; eviction therefore considers
   only slots whose origin is still `"auto"`.
8. **Edge cases** — §9, all fourteen rows, each with a test.

**Exit criteria.**

- A subset restore opens exactly the selected workspaces; unselected slots are
  still offered afterwards.
- Every row of §9 behaves as specified, with a test each.
- No password appears in any response body, any snapshot, or
  `logs/gridvibe.log` — asserted by test, not by inspection.
- Restoring an already-live workspace is refused, not duplicated.

---

## 6. Uniqueness rule — no duplicate session tabs (this release)

**Rule.** A group backed by a saved preset — group id `saved-session-<id>`
(`web/saved_sessions.py:899`) — may be **live in at most one workspace at a
time**. Ad-hoc groups are unique by construction (fresh `uuid4().hex[:12]`).

**Why.** It is the single decision that removes the most flakiness from a first
release. Allowing duplicates would require replacing the global stable group id
with a `(workspace_id, saved_session_id)` composite, which changes group
identity in the URL, the cached-view keys, `_replace_group_sessions()`
(`web/terminal_io.py:221`), the snapshot schema, and every "which tab is this"
comparison in `terminals.js`. Keeping the id global means **Stage 1 does not
touch group identity at all**.

**Behavior.**

| Situation | Result |
|---|---|
| Preset not live anywhere | Launches normally into the chosen workspace. |
| Preset live **in the target workspace** | Existing replace-in-place behavior, unchanged (`web/api.py:1924`). |
| Preset live **in another workspace** | `409` `{"conflict": "saved_session_live", "saved_session_id", "group_id", "workspace_id", "workspace_label"}`. The UI offers **Open that workspace** or **Move it here** (which calls the move endpoint, then relaunches in place). |
| Restore would recreate a preset group already live elsewhere | That group is skipped with a per-group `skipped: "already_live"` result; the rest of the workspace restores (§9, R6). |

Silently relaunching in the *other* workspace was rejected: the user's window
would show nothing and another window would change under them.

**Future.** Lifting this needs only the composite-id change above; nothing else
in this plan assumes uniqueness, which is why the restriction is safe to make
now and cheap to remove later.

---

## 7. Window chrome — the Sessions… and Workspace… menus

**Answer to the question: yes.** Every workspace window is the same
`templates/terminals.html`, so both dropdowns (`:33-90`) appear in every
workspace window with no work. What needs work is (a) scoping them to the
window's own workspace, and (b) filling out the near-empty **Workspace…** menu,
which today has exactly one item.

### Sessions… (scoped to this window's workspace)

| Item | Change |
|---|---|
| Import Session … | Must post `workspace_id` so the preset lands in **this** window, and must honour the §6 conflict (`409` → Open / Move it here). Today it launches globally. |
| Save Session | No change (acts on the active tab). |
| Save Session as … | No change. |
| Save All Sessions | Becomes workspace-scoped **for free** once `loadSessionGroups()` is filtered in Stage 2 — it iterates the page's own `sessionGroups` (`terminals.js:6554`). Verified by test, not assumed. |

### Workspace… (per window)

| Item | Stage | Notes |
|---|---|---|
| Save Workspace | 2 | Existing (`terminals.js:2005`); now sends this window's `workspace_id` and its own `active_group_id`. |
| Rename Workspace … | 3 | Label only, via the existing name-dialog pattern (`explorerNameModal`), never `window.prompt`. Updates the live record and the saved slot's label. |
| New Workspace … | 3 | Creates an empty workspace with transient `retain_when_empty=true` and opens its window. The empty window is usable: it shows the existing empty state plus **Sessions ▸ Import Session …**. Adding or moving in its first group clears the retention flag. |
| Open Workspace ▸ | 3 | Submenu of live workspaces (current one marked, disabled); focuses or opens that window. |
| Move Session to Workspace ▸ | 3 | Acts on the active tab; keyboard-accessible parity with the tab context menu. Same `moveGroupToWorkspace()` call. |
| Close Workspace Window | 3 | Closes this window, keeps its sessions live and reopenable. Explicit, unlike the OS close button. |

The **tab context menu** (right-click a session tab) offers *Move to Workspace ▸*
and *Move to New Workspace*, both routing to the same call. All submenus are
in-page menus built in `workspaces.js` — no native menus, no `window.*` dialogs.

When `workspace.multi_workspace_enabled` is false, the four multi-workspace
items are hidden and the menu degrades to today's single **Save Workspace**.

---

## 8. Workspace lifetime rules

- `"default"` is permanent and is the compatibility target for every caller and
  URL that omits a workspace id.
- Closing a workspace **window** never closes its sessions. The workspace stays
  live and can be reopened from the launcher.
- Closing the **last group** in a window closes that window and prunes the empty
  live workspace record (returning the id so the event can be emitted after the
  lock). **Amended after testing (round 2, §5):** the workspace's saved slot is
  erased with it, so an emptied workspace is removed globally rather than
  surviving in the restore chooser as an offer the live list no longer matches.
  `"default"` keeps its slot (it is never pruned), and leaving multi-workspace
  mode does not come through this path.
- Moving the last group out has the same effect on the source window, including
  the saved slot.
- A deliberately created empty workspace is not auto-closed while its transient
  `retain_when_empty` flag is set. Adding or moving in the first group clears
  the flag; it is never persisted to a saved slot.
- App settings, theme, voice settings, and surface mode stay **global**. They
  are not copied into workspace records — a workspace must never pin a stale
  copy of a live global setting (the same reasoning already documented at
  `sessions/manager.py:166-170` and `web/api.py:504-511`).
- No workspace record, API response, or snapshot ever contains a password.

---

## 9. Restore: slot lifecycle and edge cases

### 9.1 Restore / Dismiss / Forget

Today's banner has two actions, and "Dismiss" is hide-only —
`dismissRestoreBanner()` (`launcher.js:2442`) sets `banner.hidden` and drops the
cached groups; the slot survives on disk and is offered again next start. That
is coherent with one slot. With N slots it stops being coherent: dismissing
"the banner" would silently hide *every* saved workspace, and nothing in the
product could ever remove one.

**Three verbs, at two different levels — not three peer buttons:**

| Verb | Level | Meaning | Destructive |
|---|---|---|---|
| **Restore** | per row | Check the workspaces you want, press Restore once. | No |
| **Forget** | per row | Permanently delete that saved snapshot. | **Yes** — confirmed |
| **Dismiss** | panel | "Not now." Closes the chooser for this launcher session; every slot survives. | No |

**Why there is no per-row Dismiss.** A row you simply leave unchecked *is*
dismissed — it is not restored and it stays saved. A per-row dismiss would
invent a fourth state ("hidden, but still saved, until the next restart") that
no user can distinguish from Forget, and which is silently undone by a restart.
The two useful per-row answers are "restore it" and "delete it"; "not now" is a
statement about the whole prompt, so it belongs to the panel. This also
preserves today's Dismiss semantics exactly.

**Backend: already built.** `DELETE /api/runtime-state?workspace_id=<id>` →
`clear_workspace()` (`web/runtime_state.py:291`, `web/api.py:1556`) removes one
slot and preserves siblings. It is currently caller-less, documented as
"multi-workspace skeleton; not wired to the single-workspace UI" — Forget is the
caller it was written for, which retires a guardrail-5 dead-code item rather
than adding surface. The only change: return `{"forgotten": true|false}` so the
UI can distinguish "removed" from "already gone", while keeping the `200`
idempotent.

**Rules.**

1. **Forget deletes the workspace snapshot only — never a saved session
   preset.** `runtime_state.json` and `saved_sessions.json` are separate stores;
   a snapshot merely *references* presets by id, and other workspaces may
   reference the same one. The confirm copy says so verbatim: *"Forget removes
   this saved workspace snapshot. Your saved sessions are not affected."*
2. **Forget is disabled while that workspace is live**, with the reason shown
   ("Close this workspace first"). Autosave captures every live workspace
   (`iter_live_workspaces`), so forgetting a live slot would be undone by the
   next tick — a Forget that comes back is worse than no Forget.
3. **Forget confirms in-page** via the existing `openGenericConfirmModal(...)`
   (`launcher.js:1875`, shell at `index.html:218`), `danger: true` — irreversible
   action, guardrail 8. Never `window.confirm`.
4. **Forget is disabled on a row whose restore is in flight**, and the restore
   call is single-flight (§9.2 R12).
5. Forgetting the `"default"` slot is allowed; it removes the snapshot, not the
   permanent live `"default"` workspace.
6. When the last row is forgotten or restored, the chooser hides itself.

**Re-entry point.** Because Dismiss is now a real "not now" rather than the only
exit, the launcher gains a **Saved workspaces (N)** entry that reopens the
chooser on demand. Without it, Dismiss would strand both Restore and Forget
until the next restart — and housekeeping ("I'm done with that project") is
something people do mid-session, not at startup. The chooser is one component
used in both places.

### 9.2 Slot growth

Adjacent consequence worth deciding now: the restore offer is deliberately
permanent with no maximum age (`runtime_state.py:260`), and multi-workspace
turns "one slot" into "one slot per workspace the user has ever autosaved".
Nothing shrinks that set except Forget.

Recommendation — a small cap at the single write point in
`capture_live_workspaces()`: keep the most recent `MAX_AUTO_WORKSPACE_SLOTS`
(12) slots with `origin: "auto"`, evicting oldest-first; **slots with
`origin: "manual"` are never evicted**, so an explicit Workspace ▸ Save Workspace
keeps its permanent-offer promise. Roughly ten lines, one place, no config key
(guardrail 5 — no half-wired settings). If you would rather ship nothing here,
Forget alone is sufficient and the cap can follow later; it changes no contract.

Stage 1 already prevents autosave from changing an existing slot from
`origin: "manual"` to `"auto"`. Stage 4 therefore adds only the bounded
oldest-auto-first eviction at this write point.

### 9.3 Edge-case matrix

Every row is a defined outcome and a test. The invariant: **a bad row degrades
to a per-group result; it never fails the workspace and never launches
something the user did not save.**

| # | Situation | Outcome |
|---|---|---|
| R1 | Referenced preset was **deleted** | Replay the password-free snapshot verbatim. Group result carries `warning: "preset_missing"`. SSH panes without usable key auth land in the existing error placeholder **with its Retry button** — never a silent hang. |
| R2 | Preset still exists but was **edited/renamed** | Latest preset wins (today's behavior, `launcher.js:2399-2404`). Documented, kept, tested. |
| R3 | Preset's **pane count/layout differs** from the snapshot | Relaunch with the preset's shape and **discard the snapshot `workspace_layout`** when the pane counts differ. A layout array sized for the old count is the classic source of a broken grid. |
| R4 | A pane's directory / `explorer_root_directory` **no longer exists** | The pane launches into its error placeholder with Retry. The group and workspace still restore. |
| R5 | The workspace id is **already live** | Row is disabled in the chooser with the reason; the endpoint refuses it (`already_live`) rather than duplicating. |
| R6 | A group's preset is **live in another workspace** (§6) | That group is skipped with `skipped: "already_live"` plus the owning workspace's label; the rest of the workspace restores. |
| R7 | Some groups start, some fail | Partial success: the workspace opens with what started; failed groups are listed with a retryable reason. |
| R8 | `active_group_id` names a group that was **skipped or failed** | Falls back to the first group that actually started, then to no preference. (`load_restorable_workspace` already re-validates the hint against stored groups, `runtime_state.py:277-283`; this extends the same idea to *started* groups.) |
| R9 | Slot has a blank/missing **label** | Derived by the existing `_derive_workspace_label()` (`runtime_state.py:100`), never a bare timestamp. |
| R10 | Hand-edited / legacy `runtime_state.json` — v1 blob, unknown keys, empty `groups`, out-of-range zoom | v1 migrates into the `"default"` slot as today (`:146-163`); empty/invalid slots are filtered by `load_restorable_workspace`; a bad zoom degrades to "no preference" (`:284-287`). Unknown keys are ignored, never echoed back. |
| R11 | User restores a subset, then restores another slot later | Allowed. Unselected slots survive save, clear, and restore untouched. |
| R12 | Double-clicked restore / two restores in flight | Single-flight guard in the UI **and** server-side idempotency by workspace id: the second call sees the workspace live and returns `already_live`, not a duplicate. |
| R13 | **Forget** a slot that was already removed (double-click, two launcher windows) | Idempotent `200` with `{"forgotten": false}`; the row disappears either way. Siblings are untouched. |
| R14 | **Forget** a slot for a workspace that is live, or restore-then-forget in the same session | Refused in the UI (rule 2) and harmless if forced: the next autosave re-captures the live workspace. After round 2 there is nothing left to forget once the workspace's last group is closed — the slot goes with the pruned record (§8) — and it stays gone, because an empty workspace is never captured (`capture_workspace` returns `None`, `runtime_state.py:225`). |

Also covered, outside restore: a group closed in window A while window B has a
stale tab list (B reconciles on the room event, and a missing group id resolves
to this window's valid local selection); a `max_sessions` cap exceeded mid-restore
(per-group `400`, surfaced as a failed group, never a 500).

---

## 10. Contracts

### 10.1 Manager

```python
@dataclass
class Workspace:
    workspace_id: str
    label: str = ""
    created_at: float = field(default_factory=time.time)
    active_group_id: str = ""     # replaces SessionManager._active_group_id
    retain_when_empty: bool = False  # Stage 3 live-only lifecycle hint

@dataclass
class SessionGroup:
    ...
    workspace_id: str = "default"
```

`create_workspace`, `get_workspace`, `get_all_workspaces`,
`get_workspace_groups`, `get_workspace_sessions`, `move_group`,
`reorder_groups(workspace_id, ids)`, `set_active_group(workspace_id, group_id)`,
`get_active_group_id(workspace_id)`, `find_saved_session_group(saved_id)`,
`snapshot_live_workspaces()`.

Lock rules (guardrail 2, unchanged): check-then-act inside one hold; never
`socketio.emit` or touch the filesystem while holding `SessionManager.lock`;
`connection_lock` may be taken before it, never the reverse.

### 10.2 HTTP

New:

- `GET /api/workspaces` — live summaries + group counts.
- `POST /api/workspaces` — create (label optional).
- `POST /api/workspaces/close-extra` — close every workspace but `"default"`
  (leaving the mode; confirmed by the caller, writes no snapshot).
- `PATCH /api/workspaces/<id>` — rename (label only).
- `POST /api/session-groups/<group_id>/move` — `target_workspace_id`, or
  `new_workspace` + `label`.
- `GET /api/runtime-state/workspaces` — restorable summaries, no credentials.
- `POST /api/runtime-state/restore` — `{"workspace_ids": [...]}`.

Extended:

- `POST /api/sessions` — `workspace_id` | `new_workspace` + `workspace_label`;
  returns the resolved `workspace_id`; `409` on the §6 conflict.
- `GET /api/session-groups?workspace_id=`
- `GET /api/sessions?workspace_id=` — **no `active_launch_options` fallback when
  a workspace is named.**
- `POST /api/session-groups/order` — `workspace_id`; foreign ids rejected.
- `POST /api/session-groups/active` — `workspace_id`.
- `GET`/`POST`/`DELETE /api/runtime-state` — keep single-slot compatibility,
  now filtering captures by workspace correctly. `DELETE` (the existing
  caller-less `clear_workspace()` path) becomes the **Forget** action and
  returns `{"forgotten": bool}`; it stays idempotent and sibling-preserving.

Validation: unknown or malformed workspace id → `400`; group not owned by the
named workspace → `400`, never a sibling's data.

### 10.3 Socket.IO

`join_workspace` / `leave_workspace`; room `workspace:<normalized id>`.
`session_groups_updated` payload gains `workspace_id`, `reason`, and optional
`group_id`, emitted only to the affected room(s), always after locks are
released. Per-session `session_status` and output rooms are unchanged.

### 10.4 Persistence

Schema stays **v2** — no migration beyond the existing v1 path. Each slot holds
only its own workspace's groups. One autosave tick = one consistent snapshot +
one file write. Empty live workspaces never erase an older saved slot.

### 10.5 Runtime flag

`workspace.multi_workspace_enabled` (default `false` until Stage 4 ships),
wired end-to-end through `RuntimeConfig` → `/api/app-config` normalization →
both pages, exactly like the existing terminal settings (guardrail 5 — a config
key that nothing reads must not be added). The flag hides UI only; it never
gates validation, ownership checks, or the uniqueness guard.

---

## 11. Guardrail compliance

| Guardrail | How this plan satisfies it |
|---|---|
| 1 Security | Room-scoped emits, derived same-origin defaults untouched, no credential ever returned to the browser (Stage 4), request-body logging redacted. |
| 2 Concurrency | Check-then-act in one lock hold; snapshot under the lock, emit and write after release; documented lock order preserved. |
| 3 Performance | One file write per autosave tick regardless of N; push over Socket.IO, no new polling; no per-request SSH handshakes; no CDN assets. |
| 4 Correctness | `openGenericConfirmModal(...)` for every confirm, never `window.confirm/prompt/alert`; explicit request fields beat config. |
| 5 Dead code | The one new config key is wired page-to-backend or omitted; no unused endpoints — `clear_workspace()` finally gets its caller. |
| 6 Architecture/DRY | Backend in `web/workspaces.py`, not `web/api.py`; frontend in `workspaces.js` + `workspaces.css`, not `terminals.js`; one launch service shared by launch and restore. **Corollary check: every place that resolves a pane's `startup_mode` must keep working across a move** — a moved group's panes are never rebuilt, so this is verified rather than re-implemented. |
| 7 Styling | `tokens.css` variables only; stroke `currentColor` SVG icons. |
| 8 Interaction | Closing live sessions and moving a dirty-editor group both confirm in-page; conflicts and failed restores expose retry; busy states toggle classes. |
| 9 Logging | Workspace prune/close at DEBUG; no ANSI; no high-frequency requests. |
| 10 New features | Built on `RuntimeConfig`, `/api/app-config`, the shared explorer backend, room-scoped Socket.IO; no new secret storage. |

---

## 12. Test plan by stage

**Stage 1 — manager & persistence**
Omitted ids resolve to `"default"`; per-workspace list/order independence;
`move_group` retains every session id and normalizes both orders; moving the
last group yields an empty source; empty-workspace pruning spares `"default"`;
`capture_workspace` partitions correctly and leaves siblings byte-identical;
one tick → one write for N workspaces; v1 migration and default-slot behavior
unchanged; no password in any snapshot; lock-hold assertions.

Implemented automated coverage: `MultiWorkspaceSessionManagerTestCase` plus the
runtime-state cases in `tests/test_multi_workspace.py`.

**Stage 2 — routes, rooms, windows**
Filtered group/session/order/active responses; foreign-group rejection;
mismatched `workspace`+`group` falls back safely; a named workspace never gets
`active_launch_options`; source/destination rooms receive events and unrelated
rooms do not; browser window names are stable per workspace; opening one
workspace twice reuses one native window; two ids create two windows with
distinct URLs; closing one window preserves the other and the launcher; closing
the launcher still exits; fullscreen/minimize/zoom/theme target the right
window.

Implemented automated coverage: route/room/browser/runtime cases in
`tests/test_multi_workspace.py`, native registry cases in
`tests/test_webview_launcher.py`, and compatibility assertions in
`tests/test_api.py`.

**Stage 3 — launch, menus, move**
New-workspace launch and rollback on failed session creation; the §6 conflict
table, all four rows; move to existing/new/unknown/no-op; per-workspace reorder;
rename; the workspace menu items exist and are wired in the served page (the
existing `test_api.py` markup-assertion pattern); dirty-editor confirm blocks;
`GuardrailAuditFixesTestCase` still passes (no `window.confirm/prompt/alert`).

Implemented automated coverage: `MultiWorkspaceStage3TestCase` in
`tests/test_multi_workspace.py` and `WorkspaceLifetimeTestCase` in
`tests/test_session_manager.py`.

**Stage 4 — restore**
All fourteen rows of §9.3; **Forget** removes exactly one slot and preserves
siblings, is idempotent, is refused for a live workspace, and never touches
`saved_sessions.json`; **Dismiss** leaves every slot on disk and the chooser
reopens from the launcher entry; the auto-slot cap evicts oldest-auto-first and
never evicts a `manual` slot; partial results; credential rehydration happens
server-side; the launch-request log contains no password; the response wording
is *relaunch started*; unselected slots survive; the shared launch service is
used by both paths (one code path, asserted).

Implemented automated coverage: `MultiWorkspaceRestoreTestCase` in
`tests/test_multi_workspace.py`, with one test per §9.3 row
(`test_r1_...` through `test_r14_...`).

**Manual matrix** (both themes, browser **and** native): launch into existing
and new workspaces; right-click and drag moves; cancel/retry paths; source-tab
fallback after a move; subset restore and later restore of a skipped slot; two
simultaneous windows of each kind; moving a live SSH group without reconnect or
lost output.

Per stage:

```powershell
python tests/run_tests.py
python -m ruff check .
```

---

## 13. Acceptance criteria

1. Two workspaces produce two distinct terminal windows, browser and native.
2. Each window lists only its own tabs and receives only its own events.
3. A group moves via the tab context menu (and, if shipped, the drag tray)
   without restarting any terminal session.
4. Both affected windows update promptly; an unrelated workspace gets nothing.
5. A saved preset cannot be live in two workspaces; the conflict is explained
   with **Open it** / **Move it here**, never silently resolved.
6. Every workspace window has working, workspace-scoped **Sessions…** and
   **Workspace…** menus, including New / Rename / Open / Move / Close Window.
7. Autosave writes correct, non-overlapping slots in one file write per tick.
8. After a restart, any subset restores — one window each — leaving other slots
   available, and every §9.3 row behaves as specified.
9. A saved workspace can be **forgotten** permanently (confirmed, per row,
   snapshot only), **dismissed** for the session without losing anything, and
   the chooser can be reopened from the launcher afterwards.
10. Restore discloses no credential, logs no credential, and never reports an
    asynchronous connection as already established.
11. Existing single-workspace URLs, launches, and saved sessions keep working
    with the flag off **and** on.
12. Full unittest suite and Ruff pass at the end of every stage.

---

## 14. Non-goals for this release

- Dragging a tab directly between OS windows.
- Moving an individual pane between groups.
- Persisting window bounds, monitor placement, or fullscreen state.
- Closing/archiving a *live* workspace from a management screen (rename **is**
  in scope, §7; forgetting a *saved snapshot* **is** in scope, §9.1).
- Bulk **Forget all** — per-row Forget only, to keep the destructive surface
  small.
- Per-workspace app settings or themes.
- The same saved preset live in two workspaces (§6 — deliberately deferred, and
  cheap to add once the composite group id is introduced).
- Sharing live workspaces across multiple GridVibe backend processes.
- Any change to the read-only Explorer contract.

---

## 15. Risks

| Risk | Mitigation |
|---|---|
| The "workspace" naming collision in `terminals.js` causes a subtle wrong-scope bug | The §2 naming rule; new window-level code lives only in `workspaces.js`; group-level helpers renamed when touched. |
| The native bridge cannot tell which window called it | Every workspace-aware bridge method takes an explicit workspace id; the page always passes its own. |
| Stage 2's window-registry rewrite breaks `tests/test_webview_launcher.py` | Singular methods stay as `"default"` wrappers through Stages 2–3; the tests migrate method-by-method with their callers, not in one sweep. |
| Restore regressions land with credentials in scope | Stage 4 extracts the shared launch service **before** wiring the chooser, so the credential path changes once, in one place, with ISSUE-2026-037's tests. |
| Scope creep through the drag tray | It is the last, optional step of Stage 3 and shares the move call — cutting it changes nothing else. |
