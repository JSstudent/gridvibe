# Multiple-workspace implementation audit

Date: 2026-08-06

Status: findings and remediation plan. **MW-01, MW-02, MW-04, MW-05, MW-06,
MW-10, MW-12, MW-15, and the persistence half of MW-16 are fixed** (see the
"Resolution" blocks under each) — that is Phases 0, 1 and 2 complete. Every
other finding is still open and describes current behavior.

## Executive summary

The reported one-pane **Files** workspace is explained by a concrete test-isolation bug, and the workspace that remains in the launch dropdown is explained by a separate lifecycle/UI contract mismatch.

The incident chain is:

1. `ApiRoutesTestCase` redirects `config.json` and `saved_sessions.json` to temporary files, but not `runtime_state.json` (`tests/test_api.py:134`).
2. `test_workspace_save_refreshes_live_view_used_by_launcher_reopen` creates a one-pane WSL explorer named `Files`, rooted at `C:\repo`, creates a temporary saved preset, and posts to the real `POST /api/runtime-state/save` endpoint (`tests/test_api.py:9308`, `tests/test_api.py:9393`). The test therefore overwrites the user's real `default` restore slot.
3. The generated preset ID embedded in the observed restore, `session-20260806-094325-19b823`, records a creation time of 09:43:25 UTC. The local application log then shows that exact ID, name, mode, pane count, and directory restoring at 11:46:05 local time. The three-minute gap and the exact fixture payload tie the saved slot to that test.
4. The saved preset itself lived under the test's temporary `saved_sessions.json` and disappeared at teardown. On restart, restore therefore fell back to the password-free workspace snapshot and launched the fixture-shaped `Files` group.
5. At 11:46:42 the log shows a successful explicit group close. The snapshot was forgotten and the native window closed, but the group belonged to `default`. `SessionManager` deliberately never removes the `default` live record (`sessions/manager.py:313`). `/api/workspaces` returns that empty record, and the launch-destination menu renders every returned record (`web/static/js/launcher.js:2966`). The Workspaces card separately filters out empty records, so the two lists disagree.

This is not the only flaky path. Snapshot capture and snapshot deletion are not ordered as one transaction, so a timer that took its in-memory snapshot just before an explicit close can write after the close and resurrect the deleted slot. Launch, replacement, and restore also span multiple lock holds, allowing autosave to capture partial groups and concurrent restore requests to pass the same idempotency check.

The requested source-of-truth rule is not true today. Workspace shape can be persisted by the timer, manual save, and rename; restore can then replace the saved shape with the current saved-preset configuration. The target contract should be: **only timer autosave and the user's manual Save Workspace action capture workspace shape, and restore replays that captured shape exactly**. A saved preset may supply a secret that is intentionally absent from the snapshot, but it must not replace names, pane count, modes, directories, commands, or layout.

## Current model

| Layer | Current responsibility | Important identity |
| --- | --- | --- |
| `SessionManager` | Live workspaces, groups, sessions, order, and active-group hints | `workspace_id`, `group_id`, `session_id` |
| `runtime_state.json` | Restore-after-restart workspace shape, one slot per workspace | `workspace_id` |
| `saved_sessions.json` | Reusable launcher presets and encrypted SSH credentials | `saved_session_id` |
| Flask routes | Launch, close, move, save, forget, and restore orchestration | Request workspace/group IDs |
| Launcher/workspace JavaScript | Destination lists, restore UI, and window dispatch | Cached live/saved summaries |
| Native launcher | One registered native window per workspace | `workspace_id` |

The current persistence/restore path has more authority edges than intended:

```text
                         +----------------------+
timer ------------------>|                      |
manual Save Workspace -->| runtime_state.json   |----+
workspace rename -------->| workspace snapshot   |    |
                         +----------------------+    v
                                                   restore
saved_sessions.json --------------------------> "latest preset wins"
                                                    |
                                                    v
                                             live SessionManager
```

The desired path is:

```text
timer or user manual save -> one ordered snapshot commit -> exact restore
saved preset -------------------------------------------> credentials only
```

## Required invariants

These invariants should become the implementation and test contract:

1. Only the autosave timer and a user-initiated **Save Workspace** capture workspace shape.
2. Restore replays the selected snapshot's groups, pane count, names, modes, directories, commands, and layout exactly.
3. A successful explicit close of the last group removes that user-visible workspace from every live-workspace list. The internal `default` container must not appear as a ghost workspace.
4. A stale capture can never overwrite a newer manual capture or resurrect a slot after an explicit close/forget.
5. Autosave sees either the complete old live shape or the complete new live shape, never an initializing or half-replaced group.
6. Concurrent restore of one workspace is idempotent and cannot duplicate, replace, or partially destroy groups.
7. Persisted and live identities are collision-free; a display-safe transformation is not an identity function.
8. A save/forget response reports success only after the state file has actually been replaced.
9. Tests and additional GridVibe processes cannot silently use the production runtime-state namespace.

## Findings

### MW-01 — Critical: the test suite writes the production restore file

`ApiRoutesTestCase.setUp` patches `CONFIG_PATH` and `SAVED_SESSIONS_PATH`, but leaves `web.runtime_state.RUNTIME_STATE_PATH` pointing at the project-local production file (`tests/test_api.py:135`). Two tests in that class write it:

- `test_workspace_save_refreshes_live_view_used_by_launcher_reopen` uses the real manual-save route (`tests/test_api.py:9308`, `tests/test_api.py:9393`). Its `Files`/WSL/`C:\repo` fixture is the exact workspace observed after reboot.
- `test_surface_mode_setting_reaches_already_launched_groups` directly calls `capture_workspace` (`tests/test_api.py:9660`, `tests/test_api.py:9694`). It can replace the default slot with another one-group test fixture.

Because the test's saved-preset store is temporary while the runtime-state store is not, the resulting slot commonly contains a `saved_session_id` that no longer exists. Restore then deliberately falls back to the snapshot, making the test fixture look like a random user workspace.

The in-process state lock does not protect against a live app and a test process writing the same file. Both perform read-modify-replace independently, so the last process to replace the file wins and can also discard sibling updates read by the other process.

Recommended correction:

- Make the test runner assign a temporary runtime-state path before importing the application, not just in individual persistence test classes.
- Also patch the path in `ApiRoutesTestCase` so running a single test remains safe.
- Add an explicit test-mode guard that refuses to use the canonical `runtime_state.json` path.
- Give production state paths an injectable/configured owner rather than relying on mutable module globals.
- Do not treat an inter-process lock as sufficient isolation: it would serialize the test overwrite, not prevent it.

**Resolution (fixed).** Confirmed as reported: `ApiRoutesTestCase.setUp` patched
only `CONFIG_PATH` and `SAVED_SESSIONS_PATH`, and both named tests reach a real
write — one through `POST /api/runtime-state/save`, one through a direct
`capture_workspace` call — against the project-local production file.

- `web/runtime_state.py` now resolves its file as
  `GRIDVIBE_RUNTIME_STATE_PATH or PRODUCTION_STATE_PATH`, and every read and
  write goes through `_checked_state_path()`, which raises
  `RuntimeStatePathError` when `GRIDVIBE_TEST_MODE` is set and the resolved path
  is the canonical `runtime_state.json`. A missed redirect now fails loudly
  instead of overwriting the user's restore slot.
- `tests/__init__.py` sets `GRIDVIBE_TEST_MODE` and points
  `GRIDVIBE_RUNTIME_STATE_PATH` at a per-run temporary directory (removed at
  exit) before any test module imports `web`. `tests/run_tests.py` imports the
  package explicitly, because discovery may load the test modules as top-level
  modules and would otherwise skip the package bootstrap.
- `ApiRoutesTestCase.setUp` additionally patches `RUNTIME_STATE_PATH` to a file
  in its own temporary directory, so a single test run in isolation is safe and
  the class's tests no longer share one slot.

Coverage: `RuntimeStateProductionPathGuardTestCase` (test_multi_workspace.py)
asserts the suite never resolves the production path, that reads/clears against
it raise, and that a redirected path is untouched by the guard;
`ApiRoutes: saving a workspace never touches the production state file` performs
the real manual save and asserts the temporary file received it while the
production file's bytes are unchanged.

**Closed in Phase 1.** The two remaining recommendations are now implemented:
`RuntimeStateStore` owns the path (through an injected resolver, so the
process-wide default is the only thing that still consults a module global),
and every read-modify-replace runs under `_CrossProcessStateLock`, an
OS-level exclusive lock on `<state>.lock` (`fcntl.flock` on POSIX,
`msvcrt.locking` on Windows, with a bounded wait that raises rather than
hanging). Two concurrent production processes are now serialized instead of
silently discarding each other's updates.

### MW-02 — Critical: stale snapshot commits can resurrect a closed workspace

Both capture functions take the `SessionManager` snapshot before acquiring `_runtime_state_lock` (`web/runtime_state.py:234`, `web/runtime_state.py:351`). `clear_workspace` only coordinates the later file mutation (`web/runtime_state.py:321`). This permits:

```text
autosave: snapshot workspace W
close:    remove W live, then clear W's saved slot
autosave: acquire file lock and write the stale snapshot of W
result:   explicitly closed W is restorable again
```

The same ordering problem lets an older timer snapshot commit after a newer manual save. `_runtime_state_lock` is process-local, so a second process adds lost-update variants of the same race.

Recommended correction:

- Put capture, save, clear, and forget behind one state-store coordinator with an ordered revision per workspace.
- A close/forget needs a revisioned tombstone, or equivalent serialization, so any capture taken before it is rejected.
- Record the live-shape generation in the snapshot and verify it at commit time.
- Add an OS-level single-writer/file lock or enforce one server owner for production state. Test state still needs a separate path.

**Resolution (fixed).** Confirmed as reported: both capture functions read the
live manager before taking `_runtime_state_lock`, and both real close paths
(`forget_pruned_workspaces`, `forget_emptied_default_workspace`) call
`clear_workspace` after the live removal, so the interleaving was reachable from
an ordinary explicit close.

`web/runtime_state.py` now orders commits per workspace:

- `_next_state_ticket()` hands every capture and every `clear_workspace` a
  monotonic ticket. A capture takes its ticket **before** it snapshots the
  manager, so a snapshot read before a close always carries an older ticket than
  the clear that followed it.
- `_workspace_commits` records, per workspace, the newest ticket that reached the
  file and whether it was a `commit` or a `clear`. `_capture_is_stale_locked()`
  is evaluated inside the state lock: a newer **clear** rejects any capture (no
  resurrection); a newer **commit** rejects an *auto* capture (no older timer
  tick collapsing a newer shape), while an explicit Save Workspace still wins
  over a newer autosave — only a close/forget overrides the user's own save.
- A rejected capture returns `None`, which the existing callers already handle:
  autosave stores nothing for that workspace, and `POST /api/runtime-state/save`
  answers `409 {"saved": false}` — the correct answer when the workspace was
  closed mid-save.
- `clear_workspace` records its tombstone before it looks for the slot, so a
  close of a workspace that was never captured still blocks an in-flight capture
  of it.
- `capture_live_workspaces` applies the check per workspace, so one stale
  workspace in a tick no longer costs the other workspaces their capture.

Coverage: `RuntimeStateCommitOrderingTestCase` (test_multi_workspace.py) pauses a
capture inside `snapshot_live_workspaces`, performs the close/forget or a newer
manual save while it is held, releases it, and asserts the file. Four of its five
cases fail against the previous implementation; the fifth pins the positive path
(a capture taken *after* a forget still saves, so a tombstone is not permanent).

**Closed in Phase 1.** The ordering now lives in `RuntimeStateStore` and is
backed by a **durable** per-workspace revision, not only in-process tickets:

- The state file carries a `revisions` map (`{workspace_id: {revision, kind}}`).
  Every capture reads the revision it is working from *before* it snapshots the
  live manager (`RuntimeStateStore.observed_revisions`) and re-checks it inside
  the file lock at commit time, so a clear or a newer commit performed by *any*
  process — including one that has since exited — rejects the stale capture.
  `clear_workspace` writes a revisioned tombstone for the workspace even when no
  slot existed.
- Tombstones are bounded (`MAX_TOMBSTONES`, newest first) so the map cannot grow
  without limit; a live slot's entry is never pruned.
- The in-process tickets are retained alongside the durable revisions, as the
  phase plan called for.
- One deliberate limit, documented in `clear_workspace`: when there is **no
  state file at all**, the tombstone is recorded in memory only. Group close is
  a caller, and a close must not conjure a `runtime_state.json` the user never
  saved into (group events are not snapshot writers). With nothing persisted
  there is no slot for another process to resurrect *from*, and the in-process
  ticket still orders this process's own captures.

Not implemented: the snapshot still does not record a live-shape *generation*
from `SessionManager` (that belongs with the Phase 3 atomic-launch work, which
is what makes such a generation meaningful).

Coverage: `RuntimeStateCrossProcessOrderingTestCase` (test_multi_workspace.py)
drives two `RuntimeStateStore` instances over one file — each with its own
ticket sequence, standing in for two processes — and asserts that the other
owner's forget rejects a pre-clear capture, that its newer manual save survives
an older paused autosave, that a tombstone written by one owner still rejects a
capture made by a *freshly constructed* owner (the restart case), that a fresh
capture after the tombstone still saves, that `_CrossProcessStateLock` is
exclusive and released, and that the tombstone map stays bounded.

### MW-03 — High: restore has a second shape source, `saved_sessions.json`

`_restore_group_request` explicitly implements “latest preset wins” (`web/workspaces.py:907`). If a snapshot group has a live `saved_session_id`, the restore replaces the snapshot's sessions, connection mode, layout, name, and sometimes workspace layout with the preset's current config. The single-workspace browser path repeats this policy in `buildRestoreGroupBody` (`web/static/js/launcher.js:2718`).

This means a three-pane workspace saved by the timer or user can restore later as a one-pane `Files` workspace merely because the referenced launcher preset was edited. No workspace save is required for that change. `tests/test_multi_workspace.py:1298` currently asserts this behavior, and `README.md:121` documents it.

Recommended correction:

- Make the snapshot authoritative for every non-secret shape field.
- Resolve only credentials or other deliberately excluded secrets from a matching preset.
- If credentials cannot be mapped safely to the captured panes, restore the shape and surface the existing retry/authentication state instead of substituting a different shape.
- Replace the “preset wins” regression tests and update README/CHANGELOG wording when implementation changes.

### MW-04 — High: rename is an undocumented third shape writer

The runtime-state module correctly says there are three writers (`web/runtime_state.py:7`), while `CHANGELOG.md:121` claims exactly two. `PATCH /api/workspaces/<id>` calls `capture_workspace(..., origin="auto")` after changing the live label (`web/api.py:1613`, `web/api.py:1630`). That operation captures the entire current group/session shape, so a cosmetic rename can persist a transient shape that neither the timer nor the user explicitly saved.

The `origin` field also conflates two concepts. Once a slot has ever been manually saved, later auto captures preserve `origin="manual"` (`web/runtime_state.py:268`). It therefore describes eviction pinning, not the writer of the current shape.

Recommended correction:

- Under the strict two-writer contract, rename changes only the live label; the next timer/manual capture persists it.
- If immediate durable label changes are required, implement a metadata-only operation that cannot recapture groups and document it as metadata persistence, not a shape writer.
- Separate `last_capture_origin` from a `pinned` or `manually_saved_at` field.

**Resolution (fixed).** Confirmed as reported: `PATCH /api/workspaces/<id>`
called `capture_workspace(..., origin="auto")` after changing the live label,
the module docstring said "three writers" while `CHANGELOG.md` said two, and
`origin` was rewritten to `"manual"` on every auto capture of a slot that had
ever been saved by hand — so it described eviction pinning, not the writer.

- The rename route no longer captures. It changes the live label and returns;
  its docstring says why, and the next autosave tick or explicit **Save
  Workspace** persists the label (it reaches the slot through the live
  workspace record the capture already reads). No metadata-only durable-label
  operation was added — nothing in the product needs the label durable before
  the next capture, and the simplest correct answer is fewer writers, not a
  fourth one.
- `origin` now names the writer of the *current* shape and nothing else.
  Durable pinning moved to `manually_saved_at`, set by an explicit save and
  carried forward untouched by later auto captures. `_slot_is_pinned()` is the
  one predicate the auto-slot cap consults; it still honours a legacy v2 slot
  that only carries `origin == "manual"`, and the v2→v3 migration backfills
  `manually_saved_at` from such a slot's `saved_at`.
- `list_restorable_workspaces` exposes `manually_saved_at` in its summaries,
  and the launcher's "saved manually" note in `workspaces.js` reads that field
  (falling back to the legacy `origin`) so an autosave refresh no longer erases
  the note from the restore chooser.
- The module docstring and `CHANGELOG.md` now agree with the code: exactly two
  writers.

Coverage: `MultiWorkspaceStage3: rename changes the live label without writing
the snapshot` patches `_write_state_locked` and asserts it is never called
during a rename; `…the next real capture persists a renamed label` renames and
then runs one autosave tick, asserting the slot's label follows with
`origin == "auto"`. `MultiWorkspacePersistence: autosave refresh keeps the pin
while origin names the writer`, `…a pinned slot survives the auto cap after a
timer refresh`, and `…a legacy v2 manual slot keeps its pin through migration`
pin the split itself.

### MW-05 — High: an empty permanent `default` record leaks into the launch dropdown

`SessionManager.remove_workspace` refuses to remove `default` (`sessions/manager.py:313`), and `list_live_workspaces` returns every manager record (`web/workspaces.py:94`). The launcher Workspaces card filters to `group_count > 0`, but `toggleLaunchDestinationMenu` maps the complete cache (`web/static/js/launcher.js:2855`, `web/static/js/launcher.js:2966`). After the final default group closes, the user therefore sees:

- no open workspace row in the Workspaces card;
- an empty “Main workspace — 0 sessions” destination in the launch dropdown;
- an internal backend container presented as though it were still a user workspace.

This is exactly the second half of the reported incident. It is not merely stale frontend cache; repeated `/api/workspaces` reads continue to return the permanent record.

Recommended correction:

- Keep `default` internally if it simplifies legacy APIs, but exclude an empty, non-retained default record from user-visible live-workspace summaries and destination choices.
- Keep “New workspace” as the launch action when no populated/deliberately-created destination exists.
- Define one shared predicate for “user-visible live workspace” and use it in the API, card, menu, cycle shortcut, and native-window reconciliation.

**Resolution (fixed).** Re-verified as reported before the fix:
`list_live_workspaces` built a summary for every record from
`get_all_workspaces()`, `toggleLaunchDestinationMenu` mapped the complete
`liveWorkspaceCache` while the Workspaces card filtered it to `group_count > 0`,
and `SessionManager.remove_workspace` still refuses `default`.

- `workspace_is_user_visible(workspace, group_count)` in `web/workspaces.py` is
  the one predicate: a record is a workspace when it holds at least one group,
  or when the user created it deliberately empty (`retain_when_empty`, which
  always came with a window). That covers both halves of the ghost — the empty
  permanent `default` **and** a non-default record between
  `resolve_launch_destination` creating it and its first group arriving, which
  was briefly advertised as a destination too.
- `list_live_workspaces()` filters by it, so `GET /api/workspaces` is filtered
  at the source. `include_hidden=True` returns the raw record set for
  diagnostics and for the tests that assert what the filter removed; nothing
  user-facing uses it.
- `isUserVisibleWorkspace(workspace)` in `web/static/js/workspaces.js` is the
  client half, applied to cached summaries by the Workspaces card, the launch
  destination menu and its default answer, the Alt+W cycle, and the
  turn-off-the-mode confirm. Native-window reconciliation needed no change: a
  window already closes itself the moment its own workspace holds no groups
  (`_closeWindowAfterLastSession`), which is the same rule.
- Existence stayed a separate question. Routes still resolve a hidden record by
  id, so every single-workspace-mode launch still targets `default`, restore
  still targets a slot by id, and `workspace_missing` still means the record
  itself is gone.

One deliberate consequence, worth naming: with multiple workspaces **on**, an
emptied `default` is not offered as a launch or move destination any more, so
"move this tab back to Main workspace" becomes "move it to a new workspace"
until something is launched into `default` again. That is what the finding asks
for ("exclude … from user-visible live-workspace summaries and destination
choices"), and the alternative — showing it only when nothing else is live — is
exactly the reported ghost.

Coverage: `WorkspaceVisibilityTestCase` (test_multi_workspace.py) asserts that
an emptied `default` disappears from `/api/workspaces` while the record itself
survives and still accepts a launch, that a launch in flight is not listed, that
a deliberately empty workspace *is* listed, and — by running the shipped
`isUserVisibleWorkspace` under node against the same fixtures the Python
predicate sees — that the two halves of the predicate agree.

### MW-06 — High: immediate last-pane close can leave an empty group forever

`DELETE /api/sessions/<session_id>` calls `clear_disconnected_sessions()` without forcing the owning group (`web/api.py:2515`). Empty groups younger than `EMPTY_GROUP_GRACE_SECONDS` are protected because group creation and pane creation are not atomic (`sessions/manager.py:1013`). There is no periodic cleanup that returns after the five-second window.

If the user closes the last pane within five seconds of launch, the session disappears but the empty group remains indefinitely. That group keeps a non-default workspace alive, prevents the default snapshot from being forgotten, and can leave misleading group counts in workspace lists. Existing tests avoid the problem by manually aging the group, and `test_a_group_still_inside_its_grace_window_keeps_the_default_slot` explicitly codifies the stale outcome (`tests/test_multi_workspace.py:1642`).

Recommended correction:

- An explicit last-pane close should force cleanup of its owning group, just as explicit group close already does.
- Retain launch protection through an explicit `initializing` state or an atomic create operation, not a time-based exemption with no follow-up sweep.

**Resolution (close half fixed; the initialization half is Phase 3, as
planned).** Re-verified as reported: `DELETE /api/sessions/<session_id>` called
`clear_disconnected_sessions()` with no `force_group_ids` (the line had drifted
from `web/api.py:2515` to `2580`), and `test_a_group_still_inside_its_grace_
window_keeps_the_default_slot` codified the stale outcome.

- The route now passes `force_group_ids={group_id}` for the closed pane's own
  group. Closing a pane is exactly as explicit as closing a tab, so its group is
  swept immediately instead of riding the five-second exemption. Forcing a group
  that still holds panes is a no-op — it is not empty, so it is never a
  candidate — so this only ever affects the last pane.
- With that change **every** caller of `clear_disconnected_sessions` now forces
  the group it is acting on (both session-close routes, and the workspace
  teardown below), so no explicitly closed group depends on the grace period any
  more and none can outlive its last pane.
- Two tests that aged their group by hand "the way real use does" no longer need
  to; the aging was removed rather than kept as decoration.

Not implemented here, by the plan's own division of labour: the grace period
itself remains for the *non-explicit* path, because what makes it unnecessary is
Phase 3's atomic group-install transaction (`initializing`/reservation records
are listed under Phase 3's implementation order, and Phase 3's Findings line
claims "the initialization part of MW-06"). Nothing is left immortal in the
meantime — an empty group that was not explicitly closed is swept by the next
cleanup once it is older than the window.

Coverage: `…an immediate last pane close forgets the default slot` closes the
last pane immediately after launch — no aging — and asserts the group, the
workspace's group list, the saved slot, and the now-empty user-visible list;
`…an immediate pane close keeps a group that still has panes` pins that forcing
the owning group cannot sweep a group that is still live; and the non-default
twin `test_closing_the_last_session_of_the_last_group_forgets_it_too` lost its
manual aging. The grace-window assertion that protected the stale outcome is
gone.

### MW-07 — High: launch/replacement is not an atomic live-shape transaction

`launch_session_group` removes the sessions of a stable group, creates/overwrites the group, then calls `create_sessions` (`web/workspaces.py:434`). `_replace_group_sessions` performs slow connection teardown outside a single manager transaction (`web/terminal_io.py:244`), and `create_sessions` inserts panes one at a time under separate lock holds (`sessions/manager.py:447`).

The autosave snapshot itself is internally consistent for the instant it takes the manager lock (`sessions/manager.py:860`), but that instant can be between any of those launch steps. It can therefore observe:

- a newly created empty group, which is skipped and leaves an older slot untouched;
- one pane of a multi-pane group, which persists a collapsed shape;
- a stable group after its old panes were removed but before all replacements exist;
- a session inserted after its group was concurrently removed.

Recommended correction:

- Validate and build all session objects first, then install the group and all panes in one manager lock hold.
- Stage stable-group replacement and swap it atomically; close old transports after the live ownership swap.
- If an initializing state is retained, autosave must keep the prior complete slot for that workspace rather than persist a partial workspace.

### MW-08 — High: restore idempotency is check-then-act, not a reservation

`restore_workspace` checks `workspace_has_groups`, then separately creates/renames the workspace and launches groups (`web/workspaces.py:974`). Two concurrent restore requests can both pass the check.

For a non-default slot, the second request can fail with an uncaught “Workspace already exists.” For `default`, both can proceed: scratch groups duplicate, while stable preset groups can replace/interleave with each other. The existing idempotency tests are sequential (`tests/test_multi_workspace.py:1382`), despite comments referring to two launcher windows.

Recommended correction:

- Atomically claim a per-workspace restore reservation under the manager lock.
- Release it in `finally`; do not hold the manager lock during connection work or Socket.IO emits.
- Return `already_restoring`/`already_live` deterministically to the losing request.

### MW-09 — High: single- and multi-workspace restore orchestration have drifted

Multi-workspace mode uses the server `POST /api/runtime-state/restore`. Single-workspace mode fetches the raw default slot and loops over `POST /api/sessions` in the browser (`web/static/js/launcher.js:2677`, `web/static/js/launcher.js:2765`). The client path:

- duplicates preset-substitution logic;
- ignores the endpoint's `active_group_count` and can restore into an already-live default workspace;
- lacks the server's workspace-level idempotency and per-group report;
- can stop after a partial browser/network failure;
- does not apply the saved workspace label through the same path.

Recommended correction: route both modes through the server restore service, with `default` simply being one requested workspace ID.

### MW-10 — High: persistence failures are returned as successful saves

`_write_state_locked` catches every exception, logs a warning, and returns no status (`web/runtime_state.py:190`). `capture_workspace` then returns the intended in-memory slot (`web/runtime_state.py:285`), and `POST /api/runtime-state/save` answers `saved: true` with HTTP 200. `clear_workspace` similarly returns `True` after a failed delete write.

A full disk, permission error, antivirus lock, or failed replace can therefore produce a success toast for data that was never stored.

Recommended correction:

- Let the write raise a typed persistence error or return a checked result.
- Manual save/forget should return a retryable 5xx failure and never claim success.
- Autosave should log one structured, rate-limited error and retain a last-good backup.
- A malformed read should not be silently converted into an empty state that the next capture overwrites without quarantine/backup.

**Resolution (fixed).** Confirmed as reported: `_write_state_locked` caught
every exception, logged a warning, and returned; `capture_workspace` then
returned its in-memory slot and `POST /api/runtime-state/save` answered `200
{"saved": true}` for a snapshot that never reached the disk, while
`clear_workspace` returned `True` after a failed delete write.

- `_write_state_locked` now raises `RuntimeStatePersistenceError` on any
  failure, after removing its temporary file. It also `fsync`s the temporary
  file before the replace, and copies the file it is about to replace to
  `<state>.bak` first.
- `POST /api/runtime-state/save` answers `503 {"saved": false, "retryable":
  true}` and `DELETE /api/runtime-state` answers `503 {"forgotten": false,
  "retryable": true}`. Both clients already treated a non-`ok` response as a
  failure with retry wording (`terminals.js` shows "Workspace save failed: … —
  try again", `workspaces.js` surfaces `data.error`), and the launcher's
  restart path still treats only `409` as "nothing to save", so no frontend
  change was needed beyond the `manually_saved_at` note under MW-04.
- The autosave tick reports through `_report_autosave_failure`: one structured
  `ERROR` naming the failure kind per failure *streak* (rate-limited to one per
  15 min, reset by the next successful tick), with the rest at `DEBUG`. It
  never claims a commit, and the last good file is untouched.
- The last-good backup and quarantine that keep a malformed read from becoming
  an overwritable empty state are covered under MW-16 below.

Coverage: `RuntimeStatePersistenceFailureTestCase` (test_multi_workspace.py)
forces a failed temporary write and a failed `os.replace` (asserting the raise
and that no `.tmp` is left behind), asserts the `503`/no-success-payload
responses for save and forget with the slot state unchanged in each direction,
and asserts the autosave streak logs exactly one error across three failing
ticks while the file's bytes are unchanged — and that a recovered tick re-arms
the report.

### MW-11 — High: stable group IDs can collide after sanitization

Saved-session IDs accept arbitrary nonblank strings (`web/saved_sessions.py:808`, `web/api.py:2025`). `_build_launch_group_id` replaces every run of non-`[A-Za-z0-9._-]` characters with `-` (`web/saved_sessions.py:980`). Distinct IDs such as `a/b` and `a-b` therefore map to the same live group ID.

Within one workspace, launching the second preset destructively replaces the first group's sessions. Across workspaces, the conflict check compares the raw `saved_session_id` and misses the collision; replacement can close the first workspace's sessions before `create_group` discovers that the colliding group belongs to another workspace.

Recommended correction:

- Use a collision-free encoding or append a cryptographic hash of the complete raw ID.
- Check actual stable group ownership before any destructive replacement.
- Prefer a separate opaque group-instance identity over deriving runtime identity from an external preset ID.

### MW-12 — Medium: retained empty workspaces have no terminal lifecycle

`POST /api/workspaces` creates a deliberately empty record with `retain_when_empty=True` (`web/api.py:1582`). Closing its empty window does not close/remove the workspace, and no dedicated delete endpoint exists. An abandoned **New Workspace** can therefore remain as a zero-session launch destination until multi-workspace mode is disabled or the process restarts.

Recommended correction: provide an explicit “Delete empty workspace” lifecycle, or give never-populated records a bounded reservation that is released when their window closes or a launch fails.

**Resolution (fixed).** Re-verified as reported: `POST /api/workspaces` created
the record with `retain_when_empty=True`, `clear_disconnected_sessions` skipped
it for exactly that reason, and no route could remove it.

Both halves of the recommendation are implemented, because they answer different
user actions:

- **Explicit deletion.** `DELETE /api/workspaces/<workspace_id>` (see MW-15) is
  the terminal lifecycle. With no groups to close it simply releases the
  reservation and answers `{"closed": true, "removed": true, "group_count": 0}`.
  The launcher's Workspaces card exposes it as **Close** on the row.
- **Released with its window.** `closeThisWorkspaceWindow()` in `terminals.js`
  now releases a non-default workspace that holds no session tabs before closing
  the window. Closing the window of a **New Workspace** you never used is the
  user saying the tab is not coming; nothing is live, so nothing is confirmed.
  A window with tabs is untouched — closing it still leaves the workspace
  running, which is the whole point of that verb.
- **Failed launch.** Already correct and left alone: `resolve_launch_destination`
  hands back a `created_id` and every failure path in `launch_session_group`
  calls `rollback_created_workspace`. A regression test now pins it.

Coverage: `WorkspaceCloseActionMatrixTestCase` (test_multi_workspace.py) covers
creation → survives cleanup → explicit deletion (asserting it leaves no visible
workspace), a repeated delete answering `404 workspace_missing` rather than a
half-state, a failed launch leaving no record behind, and the restart terminus
(live records are in-memory, so a restart is the other end of the lifecycle).

### MW-13 — Medium: retained slots and global preset ownership conflict

Closed workspace slots are intentionally retained in several flows, including disabling multi-workspace mode. Over time, two slots can reference the same preset even though a preset is allowed live in only one workspace. Restoring both makes the first selected slot win and causes a partial restore of the second. The snapshot is therefore not independently restorable.

Recommended correction:

- Treat `saved_session_id` as a template/credential reference, not the restored group's global runtime identity; or
- preflight the complete selected restore set, report every collision before launching anything, and require an explicit choice.

### MW-14 — Medium: the auto-slot cap can evict live workspaces arbitrarily

`capture_live_workspaces` assigns one `saved_at` value to every workspace in a timer tick, then limits automatic slots to 12. If more than 12 auto workspaces are live, their timestamps tie and workspace ID becomes the retention tie-breaker (`web/runtime_state.py:400`). Some currently live workspaces are silently omitted based on an opaque random ID.

Recommended correction: never evict a workspace captured in the current live set. Apply the cap only to stale closed auto slots, or surface an explicit capacity error/limit before a thirteenth workspace can be created.

### MW-15 — Medium: close semantics are inconsistent

Closing the last group or last pane clears that workspace's snapshot. `DELETE /api/sessions` without a group closes everything but deliberately preserves all snapshots (`web/api.py:2607`; also documented in `CHANGELOG.md:14`). “Close this workspace window” closes no sessions. These may be defensible individual choices, but the UI verbs do not make the persistence outcome obvious.

Recommended correction: define and expose separate actions for **Close window**, **Close live workspace**, and **Close and forget saved workspace**. Apply the same rule to one group, one workspace, and all workspaces.

**Resolution (fixed).** Re-verified as reported: the three outcomes were as
described (the bulk-close line had drifted from `web/api.py:2607` to `2612`),
and the UI offered no verb between "close a tab" and "close a window".

The matrix is written down once, in `web/workspaces.py` above the close
service, and every close path implements exactly it:

| Verb | Where | Live effect | Snapshot |
| --- | --- | --- | --- |
| Close window | Workspace ▸ Close Workspace Window | nothing closed | kept |
| Close group / last pane | tab ✕, pane ✕ | group gone; workspace gone when it was the last | **cleared** — the workspace emptied itself |
| Close live workspace | Workspace ▸ Close Workspace, **Close** on the launcher card | every group and session closed; record removed | **kept** — the restorable close |
| Close and forget | **Close and forget** in the restore chooser | as above | removed |
| Close all sessions | Sessions ▸ Close All | every shell ends | kept everywhere (this is what makes restore-after-restart work) |

- `DELETE /api/workspaces/<workspace_id>` is the new verb; `?forget=true` is the
  *and forget* variant. Both go through `close_live_workspace()`.
- `_close_workspace_contents()` is the single teardown, shared with
  `close_extra_workspaces()` (leaving multi-workspace mode is *Close live
  workspace* applied to every workspace but `default`), so the two cannot drift.
  It closes sessions, force-sweeps the groups, clears `retain_when_empty`, and
  drops the record — under one `SessionManager.lock` hold, with the SSH teardown
  and the room broadcast after every lock is released (guardrail 2).
- Closing `default` empties it rather than removing its permanent record; with
  MW-05 an empty `default` is not a user-visible workspace, so the outcome the
  user sees is the same.
- The live half never rolls back for the persisted half: a forget that cannot
  reach the disk answers `503 {"closed": true, "forgotten": false, "retryable":
  true}` and says the close already happened, so the retry is the forget alone.
- Destructive variants confirm through the shared in-page modal
  (`openGenericConfirmModal`, never `window.confirm` — guardrail 4), busy state
  is a class, and the failure message keeps the "— try again" retry wording
  (guardrail 8). An empty workspace has nothing to lose and is released without
  a prompt.
- The restore chooser's **Forget** was a disabled button reading "Close this
  workspace first" whenever the slot was live. It now offers **Close and
  forget** on that row, which is what turned the fourth verb from a parameter
  nothing calls into a reachable action.

Coverage: `WorkspaceCloseActionMatrixTestCase` (test_multi_workspace.py) asserts
each row of the table by its two effects — sessions/record *and* slot — for
close-live (keeps the slot), close-and-forget (removes it), closing `default`
(empties without removing the container, no ghost left), closing the last group
(still forgets), and bulk close-all (keeps every snapshot); plus `404
workspace_missing` / `400` for an unknown and a malformed id, and the failed
forget reporting `503` with the close done and the slot intact for the retry.

### MW-16 — Medium: validation and diagnostics are too weak for recovery

`load_restorable_workspace` checks that `groups` is a nonempty list and that the origin is recognized, but does not validate each group/session shape (`web/runtime_state.py:288`). A group with zero sessions is shown in summary counts but cannot launch. Capture success is not logged with safe metadata, so the log records the later restore but not which writer created the bad slot.

Recommended correction:

- Validate schema version, normalized workspace/group IDs, nonempty session arrays, bounded pane counts, and allowed shape fields before offering a slot.
- Quarantine malformed state and preserve the last-good file.
- Log safe capture metadata: workspace ID, writer, generation, group count, pane count, and commit result. Do not log hosts, directories, commands, or credentials.
- Log close/forget with the removed live ID, persisted revision, and whether the public workspace disappeared.

**Resolution (persistence half fixed; field validation deferred to Phase 5, as
planned).** Confirmed as reported: a read error was swallowed into
`_empty_state()`, there was no backup or quarantine, a file carrying a version
this build does not understand was reinterpreted through the v1 migration path
and then overwritten, and no capture was logged at all.

- `SCHEMA_VERSION` is now `3` and `SUPPORTED_SCHEMA_VERSIONS` is explicit.
  `_parse_state` returns `None` — meaning "quarantine this" — for a non-object
  root, a version outside the supported set (notably a *newer* file), or a
  supported version with the wrong shape. v1 and v2 files still migrate on
  read; a readable but empty file is not treated as corrupt.
- `_read_state_locked` quarantines a bad file as
  `runtime_state.json.corrupt-<UTC timestamp>` (uniquified on collision) and
  then recovers from `<state>.bak`, the copy taken before the previous commit.
  The evidence is preserved and the last-good state is what the next capture
  builds on, so one bad write can no longer erase every saved workspace.
- `_log_commit` logs each committed capture at DEBUG with workspace id, writer,
  durable revision, pinned flag, group count, and pane count — and nothing
  else. Hosts, directories, commands, and credentials are absent by
  construction. `clear_workspace` logs the workspace id and whether a slot
  actually existed.
- Deferred to Phase 5 as the plan specifies: per-group/per-session field
  validation (normalized ids, nonempty session arrays, bounded pane counts,
  allowed mode/shape fields, layout consistency). A zero-pane group is still
  countable in a summary; only the file-level integrity work is closed here.

Coverage: `RuntimeStateSchemaRecoveryTestCase` (test_multi_workspace.py) asserts
that a corrupt file is quarantined and the last-good backup's shape recovered,
that a `version: 99` file is quarantined rather than reinterpreted, that a
corrupt file with no backup is still quarantined and removed, that the
quarantined bytes survive the next capture verbatim, that a v2 file loads and is
rewritten at the current version with a `revisions` map, and that the commit log
carries shape metadata but no host or directory.

## Existing coverage that currently protects the wrong behavior

Several tests are valuable but encode contracts that conflict with the desired model:

| Existing test | Current assertion | Required replacement |
| --- | --- | --- |
| `test_r2_existing_preset_wins_over_the_snapshot` | Edited preset replaces saved shape | Snapshot shape wins; preset supplies credentials only |
| `test_r3_layout_is_discarded_when_the_preset_pane_count_changed` | Current preset pane count controls restore | Snapshot pane count/layout remains unchanged |
| ~~`test_a_group_still_inside_its_grace_window_keeps_the_default_slot`~~ | ~~Immediate last-pane close leaves slot/group~~ | **Replaced in Phase 2** by `…an immediate last pane close forgets the default slot` |
| ~~Per-pane close tests that manually age groups~~ | ~~Cleanup works after five seconds~~ | **Done in Phase 2** — the two tests lost their manual aging |
| Sequential second-restore test | Later request sees live groups | Simultaneous requests produce exactly one restore |

## Regression matrix to add

All of these should be behavioral tests against routes or public manager/store operations, not source-text assertions.

1. **Test isolation:** start with a sentinel production state file, run every API test that saves/captures, and prove the sentinel is unchanged while a temporary state file receives the writes. *(Added with MW-01, non-destructively: the test reads the production file's current bytes rather than planting a sentinel in it, and the test-mode guard covers the rest.)*
2. **Exact snapshot:** save a three-pane preset-backed workspace, edit the preset into one `Files` pane, restore, and assert the original three-pane snapshot.
3. **Two writers:** rename while the timer/manual writer is mocked; assert no shape capture occurs and the next actual capture persists the label.
4. **Default close/listing:** restore one group into `default`, close it, then assert no user-visible live workspace/destination remains and no saved slot remains. *(Added with MW-05/MW-06.)*
5. **Immediate pane close:** close the last pane immediately after launch in default and non-default workspaces; assert group, workspace visibility, native close signal, and slot cleanup. *(Added with MW-06; the native close signal stays a frontend rule — a window already closes itself when its own workspace holds no groups.)*
6. **Close versus autosave:** pause autosave after its manager snapshot, close/forget the workspace, resume autosave, and assert the slot stays deleted. *(Added with MW-02.)*
7. **Manual versus timer ordering:** pause an older timer capture, perform a newer manual capture, release the timer, and assert the manual/newer revision remains. *(Added with MW-02.)*
8. **Atomic launch snapshot:** pause a multi-pane launch at every installation boundary while autosave runs; every stored slot must equal either the complete old shape or complete new shape.
9. **Concurrent restore:** release two restore requests through a barrier; assert one successful group set and one deterministic conflict response.
10. **Single-mode live restore:** with default already populated, invoke the single-mode restore route and assert no duplicate or replacement.
11. **ID collision:** create presets `a/b` and `a-b`, launch them in the same and different workspaces, and assert independent groups with no destructive teardown.
12. **Write failure:** force the atomic replace to fail and assert manual save/forget returns failure without a success payload. *(Added with MW-10; extended in Phase 2 to the close-and-forget verb, which reports the close as done and the forget as retryable.)*
13. **Duplicate preset references:** restore two retained slots referencing one preset and assert the chosen product policy without partial silent success.
14. **Capacity:** autosave 13 live workspaces and assert every live workspace remains restorable.
15. **Malformed state:** offer a zero-pane/corrupt group and assert it is quarantined rather than listed as restorable.

## Recommended remediation order

Treat each phase below as a contract boundary. A phase may be split into small
changes, but its exit gate should pass before work starts on a phase that depends
on it. Findings that span layers intentionally appear in more than one phase;
each phase's **Findings** line says which part is being closed there.

### Phase 0 — Contain the observed corruption paths (complete)

**Findings:** MW-01 and MW-02.

The test suite now uses an isolated runtime-state namespace, test mode refuses
the production path, and in-process tickets/tombstones reject stale captures.
Keep those regression tests in place throughout the later work.

The remaining recommendations under MW-01 and MW-02 are not discarded. An
injected state-store owner, persisted generations, and cross-process production
writer protection move into Phase 1 because they are durability architecture,
not incident containment.

**Exit gate:** the existing MW-01/MW-02 tests remain green, including direct
single-test execution and the paused-capture close/manual-save races.

### Phase 1 — Make runtime state a trustworthy, two-writer store (complete)

**Findings:** remaining MW-01/MW-02 work, MW-04, MW-10, and the persistence
foundation of MW-16.

**Goal:** a success response means the intended revision is durable, and only
timer autosave or the user's **Save Workspace** action can capture workspace
shape.

Implementation order:

1. Introduce one application-owned runtime-state store/coordinator. It should
   own the injected path, process lock, file operations, schema version, and
   per-workspace commit metadata. Routes and background capture should receive
   that owner instead of mutating module-global paths.
2. Protect the complete production read-modify-replace operation with an
   OS-level file lock (or enforce and verify one production server owner).
   Continue using a unique same-directory temporary file for every atomic
   replace. Never hold `SessionManager.lock` during file I/O.
3. Persist a monotonic workspace revision/generation and the information needed
   to reject a pre-clear capture after a process restart. Retain the existing
   in-process tickets while migrating; the durable generation becomes the
   authority for cross-process ordering.
4. Return a typed commit result or raise a typed persistence error. Manual
   save/forget routes must return a retryable 5xx response on replace failure;
   autosave should retain the last good file and emit one structured,
   rate-limited error rather than claiming a commit.
5. Remove the rename call to `capture_workspace`. Rename changes the live label,
   and the next timer/manual capture persists it. Separate `last_capture_origin`
   from durable pinning metadata such as `manually_saved_at`/`pinned`.
6. Add schema-version parsing, last-good backup, and malformed-file quarantine
   before a bad read can be converted to an empty state and overwritten. Start
   safe commit logging here; complete field-level validation in Phase 5 once the
   final identity schema is known.

Verification:

- Retain the MW-02 close-versus-autosave and manual-versus-timer barriers, then
  run equivalent coordinator tests across two store instances/processes.
- Force read, temporary-write, flush, and replace failures and assert that
  manual save/forget never return a success payload.
- Rename while both permitted writers are stubbed and prove that no shape write
  occurs; then prove the next real capture persists the new label.
- Feed malformed and unsupported-version files through the public load path and
  prove they are quarantined while the last-good state remains recoverable.

**Exit gate:** all runtime-state mutations flow through the coordinator; the
two-writer rule is behavioral-test enforced; acknowledgements match durable
file contents; and concurrent production processes cannot silently lose each
other's updates.

**Outcome (all six steps done; exit gate met).**

| Step | Result |
| --- | --- |
| 1. One application-owned store | `RuntimeStateStore` owns the injected path resolver, process lock, file operations, schema, and per-workspace commit metadata. `_default_store` is the process-wide owner; the module functions are thin delegates kept for the existing call sites and `web/api.py`'s re-exports. |
| 2. OS-level lock + unique temp + no manager lock held | `_CrossProcessStateLock` wraps every read-modify-replace; each atomic replace still uses a unique same-directory temp path; every capture reads the live manager and returns *before* the store takes any file lock. |
| 3. Durable revision | `revisions` map in the file, observed before the live snapshot and re-checked at commit; in-process tickets retained. |
| 4. Typed persistence result | `RuntimeStatePersistenceError`; `503` from save/forget; rate-limited structured autosave error with the last good file retained. |
| 5. Rename is not a writer; pinning split out | Rename touches the live label only; `manually_saved_at` carries the pin, `origin` carries the writer. |
| 6. Schema version, backup, quarantine, safe commit logging | `SCHEMA_VERSION = 3`, `<state>.bak`, `runtime_state.json.corrupt-<ts>`, `_log_commit`. Field-level validation stays in Phase 5 per the plan. |

New sidecar files (`.lock`, `.bak`, `.corrupt-*`) are gitignored.
`README.md`, `CHANGELOG.md`, `CLAUDE.md`, and `AGENTS.md` were updated for the
two-writer rule, the honest save/forget failure, and the quarantine behavior.
Verified with `python tests/run_tests.py` (1135 tests, 7 skipped, 0 failures)
and `python -m ruff check .`.

Two items were consciously **not** done here, both by the plan's own division of
labour: the snapshot does not record a `SessionManager` live-shape generation
(meaningful only once Phase 3 makes launch atomic), and per-field slot
validation stays in Phase 5 where the final identity schema is known.

### Phase 2 — Make workspace lifecycle and visibility consistent (complete)

**Findings:** MW-05, MW-06, MW-12, and MW-15.

**Goal:** an internal container, grace-period artifact, or abandoned empty
reservation can no longer masquerade as a user workspace, and each close verb
has one documented persistence effect.

Implementation order:

1. Write one close-action matrix for **Close window**, **Close group/last pane**,
   **Close live workspace**, and **Close and forget saved workspace** before
   changing endpoints. Preserve the audit's current last-group expectation:
   explicitly closing the final group/pane removes the live workspace and its
   runtime-state slot. If a restorable "close live" action is desired, expose it
   as a distinct verb that preserves the slot instead of overloading group
   close. Apply the chosen rules to one group, one workspace, and bulk close.
2. Define one backend predicate for a user-visible live workspace. Exclude the
   empty, non-retained internal `default` record, and consume the same filtered
   result in the API, Workspaces card, launch-destination menu, cycle shortcut,
   and native-window reconciliation.
3. Make an explicit last-pane close force cleanup of its owning group. Track
   launch initialization explicitly so cleanup safety no longer depends on a
   five-second grace period with no follow-up sweep.
4. Give retained empty workspaces a terminal lifecycle. Provide **Delete empty
   workspace**, and release a never-populated reservation when its launch fails
   or its empty native window is deliberately closed.
5. Use the shared in-page confirmation flow for destructive variants and keep
   retry affordances visible when cleanup or persistence fails.

Verification:

- Close the last pane immediately in both `default` and non-default workspaces;
  assert group removal, public-list removal, native-window reconciliation, and
  the selected snapshot outcome from the action matrix.
- Assert every user-visible consumer receives the same workspace IDs after the
  final default group closes.
- Exercise creation, failed launch, empty-window close, explicit deletion, and
  restart for deliberately empty retained workspaces.
- Replace the existing grace-window assertions that protect stale empty groups.

**Exit gate:** no ghost destination remains after a final close, no explicit
last-pane close can leave an immortal group, and every close label predicts its
live-state and persisted-state result.

**Outcome (all five steps done; exit gate met).**

All four findings were re-verified against the code before anything changed —
each one still reproduced exactly as written, with only line numbers drifted
(`web/api.py:2515`→`2580` in MW-06, `2607`→`2612` in MW-15). See the Resolution
block under each finding for the detail.

| Step | Result |
| --- | --- |
| 1. Close-action matrix, written first | The table above `close_live_workspace` in `web/workspaces.py`, mirrored in `README.md` ("What each 'close' does") and pointed at from the two session-close route docstrings. The audit's last-group expectation is preserved: closing the final group/pane still removes the workspace *and* its slot; the restorable close is a separate verb. |
| 2. One user-visible predicate | `workspace_is_user_visible` (server) + `isUserVisibleWorkspace` (client), consumed by `/api/workspaces`, the Workspaces card, the launch destination menu and its default answer, the Open/Move menus, the Alt+W cycle, and the mode-off confirm. A node-driven test asserts the two halves agree. |
| 3. Explicit last-pane close forces its group | `DELETE /api/sessions/<id>` passes `force_group_ids={group_id}`; every caller of `clear_disconnected_sessions` now forces the group it is acting on. The grace period survives only for the non-explicit path, and Phase 3 is what removes the need for it. |
| 4. Terminal lifecycle for empty workspaces | `DELETE /api/workspaces/<id>` (`?forget=true` for the fourth verb); the launcher card's **Close**; and `closeThisWorkspaceWindow()` releasing a workspace that never held a tab. Failed-launch rollback was already correct and is now pinned by a test. |
| 5. In-page confirms and retry affordances | `openGenericConfirmModal` for every destructive variant (guardrail 4), busy-as-a-class, "— try again" wording preserved, and the restore chooser's dead-end disabled **Forget** replaced by a working **Close and forget**. |

Two consequences worth naming rather than burying:

- With multiple workspaces **on**, an emptied `default` stops being a launch or
  move destination, so "move this tab back to Main workspace" becomes "move it
  to a new workspace" until something is launched into `default` again. This is
  what MW-05 asks for; the alternative reintroduces the reported ghost.
- The empty-group grace period still exists for groups that were not explicitly
  closed. That is the `initializing`/atomic-launch half of MW-06, which the plan
  assigns to Phase 3 — nothing is immortal in the meantime, because such a group
  is swept by the next cleanup once it is past the window.

Verified with `python tests/run_tests.py` (1151 tests, 7 skipped) and
`python -m ruff check .`. Two failures remain and are unrelated to this work:
`ApiRoutes: voice status endpoint …` ×2 fail on this machine because the
optional `websocket-client` voice dependency is not installed, and they fail
identically on the unmodified tree.

### Phase 3 — Make live-shape mutation atomic and runtime identity collision-free

**Findings:** MW-07, MW-11, and the initialization part of MW-06.

**Goal:** autosave observes the complete old shape or complete new shape, and no
external preset identifier can destructively alias another live group.

Implementation order:

1. Add a manager transaction that validates and stages a complete group and all
   session records before publication, then installs or swaps them in one
   `SessionManager.lock` hold. Snapshot the displaced transports under the lock;
   close transports and emit Socket.IO updates only after releasing every
   shared lock.
2. Use the same transaction for new launch, stable-group replacement, restore,
   and rollback. An `initializing`/reservation record may protect ownership, but
   it must not appear in autosave or user-visible summaries as a completed
   group, and it must be released in `finally`.
3. Stop deriving runtime group identity solely from a sanitized
   `saved_session_id`. Prefer an opaque group-instance ID with the raw preset ID
   retained only as a template/credential reference; a collision-free encoded
   ID plus a hash is an acceptable compatibility bridge.
4. Check actual runtime group ownership before any teardown. Add a versioned
   compatibility path for snapshots containing legacy derived group IDs without
   rewriting their user-visible shape merely because they were loaded.

Verification:

- Pause a multi-pane create/replacement at every staging and publication
  boundary while autosave runs; every committed slot must equal the complete
  old or complete new shape.
- Launch `a/b` and `a-b` in the same and different workspaces and prove that no
  group or transport is replaced by the other.
- Inject launch failure before and after publication and prove reservations,
  groups, sessions, and old transports end in a coherent state.

**Exit gate:** there is one atomic group-install path, snapshots cannot contain
partial launches/replacements, and runtime identity is independent of lossy
display-safe transformations.

### Phase 4 — Make restore one server-owned, exact-snapshot operation

**Findings:** MW-03, MW-08, MW-09, and MW-13.

**Goal:** both UI modes invoke the same idempotent restore transaction; the
snapshot is the only source of shape, while presets can supply secrets only.

Implementation order:

1. Make `_restore_group_request` build names, pane count, modes, directories,
   commands, group layout, and workspace layout exclusively from the captured
   snapshot. A saved preset may supply a credential only when it can be safely
   matched to the captured pane through a credential reference; never match a
   newly edited preset shape by position and never persist the secret into
   runtime state.
2. If a credential is missing or cannot be mapped, keep the captured shape and
   return a per-pane authentication/retry state. Do not substitute the preset's
   current panes or partially collapse the group.
3. Atomically reserve the requested workspace for restore under the manager
   lock, then release the lock before connection work. Release the reservation
   in `finally` and return deterministic `already_live` or `already_restoring`
   results to losing requests.
4. Preflight the complete selected restore set before launching. With the Phase
   3 identity model, two slots may safely reference the same preset as a
   template/credential source; they must not share global runtime ownership. If
   any remaining conflict is unsupported, report all conflicts before changing
   live state.
5. Route single-workspace and multi-workspace startup through
   `POST /api/runtime-state/restore`, including `default`. Remove the browser's
   raw-slot loop and preset-substitution policy, and have both surfaces consume
   the same workspace/group/pane report and retry states.

Verification:

- Replace the two "preset wins" tests with the three-pane-snapshot/edited
  one-pane-preset regression and assert exact snapshot shape.
- Release two restore requests through a barrier; assert one complete restore
  and one deterministic conflict response with no duplicate or replacement.
- Restore into an already-live `default` workspace in single-workspace mode and
  assert that it is not duplicated or partially replaced.
- Restore two retained slots referencing one preset and assert two independent
  live groups, or a complete preflight rejection if a residual policy conflict
  remains; partial silent success is never acceptable.

**Exit gate:** there is no client-side restore orchestrator, every restore is
reserved/idempotent, preset edits cannot alter captured shape, and duplicate
preset references have an explicit non-partial result.

### Phase 5 — Finish retention, validation, and operational recovery

**Findings:** MW-14 and the remaining schema/diagnostic work in MW-16, followed
by a final cross-check of all findings.

**Goal:** valid live workspaces are never evicted from restore state, corrupt
state is diagnosable and recoverable, and the completed behavior is documented
as a stable contract.

Implementation order:

1. Change the automatic-slot cap so the complete current live set is protected.
   Apply retention only to stale, closed automatic slots; keep manual/pinned
   slots under their documented policy. If a hard total limit is still needed,
   reject creation before a workspace becomes uncapturable and expose that
   limit to the user.
2. Complete validation against the now-final schema: schema version, normalized
   workspace/group IDs, nonempty groups and session arrays, bounded pane counts,
   allowed mode/shape fields, credential-reference form, and layout consistency.
   Invalid slots are quarantined and omitted from restorable summaries without
   destroying the last-good file.
3. Complete safe diagnostics for capture, close/forget, quarantine, migration,
   and restore. Include IDs, writer, generation, commit result, group count, and
   pane count; exclude hosts, directories, commands, and credentials.
4. Run the full regression matrix in this audit, then update `README.md` and
   `CHANGELOG.md` for the exact-snapshot rule, close-action semantics, empty
   workspace lifecycle, restore retry behavior, and retention policy.

Verification:

- Autosave 13 or more live workspaces and assert every live workspace remains
  restorable while only eligible stale closed slots are evicted.
- Offer zero-pane, malformed, unsupported-version, and legacy-version slots and
  assert validation, quarantine, migration, summary visibility, and last-good
  recovery behavior.
- Review emitted logs to prove they contain enough safe metadata to reconstruct
  writer/order decisions without leaking connection or command data.
- Run `make check` (or `python tests/run_tests.py` plus
  `python -m ruff check .` on Windows) after the complete sequence.

**Exit gate:** every required invariant and regression-matrix row is covered by
a behavioral test, maintained documentation matches the implementation, and no
remaining audit finding is left open without an explicit product decision.

This order first makes persistence outcomes reliable, then fixes the lifecycle
surface users interact with, then provides the atomic manager and identity
primitives that restore depends on. Restore consolidation follows those
prerequisites, and retention/schema hardening comes last so it validates the
final persisted and runtime model rather than an intermediate one.
