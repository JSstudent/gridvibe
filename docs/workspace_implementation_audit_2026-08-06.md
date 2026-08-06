# Multiple-workspace implementation audit

Date: 2026-08-06

Status: findings and remediation plan. **MW-01 and MW-02 are fixed** (see the
"Resolution" blocks under each); every other finding is still open and describes
current behavior.

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

Still open from the recommendations above: production state paths are still
mutable module globals rather than an injected owner, and there is still no
OS-level single-writer lock for two concurrent *production* processes.

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

Still open from the recommendations above: the ordering is per-workspace tickets
rather than a full state-store coordinator with persisted revisions, the snapshot
does not record a live-shape generation, and cross-process production writes are
still unguarded (MW-01 removes the test process from that set, which was the only
observed instance).

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

### MW-06 — High: immediate last-pane close can leave an empty group forever

`DELETE /api/sessions/<session_id>` calls `clear_disconnected_sessions()` without forcing the owning group (`web/api.py:2515`). Empty groups younger than `EMPTY_GROUP_GRACE_SECONDS` are protected because group creation and pane creation are not atomic (`sessions/manager.py:1013`). There is no periodic cleanup that returns after the five-second window.

If the user closes the last pane within five seconds of launch, the session disappears but the empty group remains indefinitely. That group keeps a non-default workspace alive, prevents the default snapshot from being forgotten, and can leave misleading group counts in workspace lists. Existing tests avoid the problem by manually aging the group, and `test_a_group_still_inside_its_grace_window_keeps_the_default_slot` explicitly codifies the stale outcome (`tests/test_multi_workspace.py:1642`).

Recommended correction:

- An explicit last-pane close should force cleanup of its owning group, just as explicit group close already does.
- Retain launch protection through an explicit `initializing` state or an atomic create operation, not a time-based exemption with no follow-up sweep.

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

### MW-16 — Medium: validation and diagnostics are too weak for recovery

`load_restorable_workspace` checks that `groups` is a nonempty list and that the origin is recognized, but does not validate each group/session shape (`web/runtime_state.py:288`). A group with zero sessions is shown in summary counts but cannot launch. Capture success is not logged with safe metadata, so the log records the later restore but not which writer created the bad slot.

Recommended correction:

- Validate schema version, normalized workspace/group IDs, nonempty session arrays, bounded pane counts, and allowed shape fields before offering a slot.
- Quarantine malformed state and preserve the last-good file.
- Log safe capture metadata: workspace ID, writer, generation, group count, pane count, and commit result. Do not log hosts, directories, commands, or credentials.
- Log close/forget with the removed live ID, persisted revision, and whether the public workspace disappeared.

## Existing coverage that currently protects the wrong behavior

Several tests are valuable but encode contracts that conflict with the desired model:

| Existing test | Current assertion | Required replacement |
| --- | --- | --- |
| `test_r2_existing_preset_wins_over_the_snapshot` | Edited preset replaces saved shape | Snapshot shape wins; preset supplies credentials only |
| `test_r3_layout_is_discarded_when_the_preset_pane_count_changed` | Current preset pane count controls restore | Snapshot pane count/layout remains unchanged |
| `test_a_group_still_inside_its_grace_window_keeps_the_default_slot` | Immediate last-pane close leaves slot/group | Explicit last-pane close removes group and clears slot |
| Per-pane close tests that manually age groups | Cleanup works after five seconds | Cleanup works immediately after explicit close |
| Sequential second-restore test | Later request sees live groups | Simultaneous requests produce exactly one restore |

## Regression matrix to add

All of these should be behavioral tests against routes or public manager/store operations, not source-text assertions.

1. **Test isolation:** start with a sentinel production state file, run every API test that saves/captures, and prove the sentinel is unchanged while a temporary state file receives the writes. *(Added with MW-01, non-destructively: the test reads the production file's current bytes rather than planting a sentinel in it, and the test-mode guard covers the rest.)*
2. **Exact snapshot:** save a three-pane preset-backed workspace, edit the preset into one `Files` pane, restore, and assert the original three-pane snapshot.
3. **Two writers:** rename while the timer/manual writer is mocked; assert no shape capture occurs and the next actual capture persists the label.
4. **Default close/listing:** restore one group into `default`, close it, then assert no user-visible live workspace/destination remains and no saved slot remains.
5. **Immediate pane close:** close the last pane immediately after launch in default and non-default workspaces; assert group, workspace visibility, native close signal, and slot cleanup.
6. **Close versus autosave:** pause autosave after its manager snapshot, close/forget the workspace, resume autosave, and assert the slot stays deleted. *(Added with MW-02.)*
7. **Manual versus timer ordering:** pause an older timer capture, perform a newer manual capture, release the timer, and assert the manual/newer revision remains. *(Added with MW-02.)*
8. **Atomic launch snapshot:** pause a multi-pane launch at every installation boundary while autosave runs; every stored slot must equal either the complete old shape or complete new shape.
9. **Concurrent restore:** release two restore requests through a barrier; assert one successful group set and one deterministic conflict response.
10. **Single-mode live restore:** with default already populated, invoke the single-mode restore route and assert no duplicate or replacement.
11. **ID collision:** create presets `a/b` and `a-b`, launch them in the same and different workspaces, and assert independent groups with no destructive teardown.
12. **Write failure:** force the atomic replace to fail and assert manual save/forget returns failure without a success payload.
13. **Duplicate preset references:** restore two retained slots referencing one preset and assert the chosen product policy without partial silent success.
14. **Capacity:** autosave 13 live workspaces and assert every live workspace remains restorable.
15. **Malformed state:** offer a zero-pane/corrupt group and assert it is quarantined rather than listed as restorable.

## Recommended remediation order

1. **Stop further corruption:** isolate the entire test process from production `runtime_state.json`; add the test-mode guard. **Done — MW-01.**
2. **Lock the persistence contract:** remove rename shape capture, make snapshot shape authoritative, distinguish writer origin from pinning, and make write failures observable.
3. **Order state commits:** add per-workspace generations/tombstones and serialize capture against clear/forget; add production single-writer protection. **Done — MW-02**, except production single-writer protection.
4. **Fix close visibility:** force explicit last-pane cleanup and hide the empty internal default record from every user-visible list.
5. **Make live shape atomic:** stage and atomically install/replace groups and sessions.
6. **Unify restore:** use the server service in both modes and add a per-workspace restore reservation.
7. **Harden identity and recovery:** remove sanitized-ID collisions, validate saved slots, handle duplicate preset references, and improve safe diagnostics.
8. **Resolve product semantics:** name the distinct close/forget actions and decide how deliberately empty workspaces expire.

The first four items address both halves of the reported incident and the most likely remaining intermittent reappearance/collapse paths. The later items remove deeper collision and recovery hazards before more workspace functionality is built on the current contracts.
