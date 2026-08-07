# Session-group persistence and restore audit

Date: 2026-08-07

Status: findings and implementation proposal; no production changes are included in this audit.

## Executive summary

GridVibe has two persistence products with deliberately different purposes:

- `saved_sessions.json` stores reusable launcher presets, including an encrypted SSH password.
- `runtime_state.json` stores a password-free snapshot of live workspace shape for restart restore.

The durable `runtime_state.json` machinery and the server-owned restore path are already strong. They use an OS-level file lock, unique same-directory temporary files, atomic replacement, backups, corrupt-file quarantine, ordered per-workspace revisions, restore claims, atomic group installation, and credential matching that never lets a preset replace snapshot shape. Those mechanisms should remain the foundation.

The main defect is earlier in the pipeline: the browser owns important live presentation state that the `SessionManager` does not know. Autosave and **Save Workspace** snapshot only the manager. As a result, the runtime-state file can be internally consistent and durably written while still being an out-of-date snapshot of what the user sees.

This affects explorer tabs and views most severely, and also affects pane order and custom split geometry. Browser tabs are pushed to the server, but their debounced requests can race an explicit workspace save or one another. **Save Session** is better because it serializes the browser-owned state directly, but that path uses the older `saved_sessions.json` store, whose writes are neither locked nor atomic.

The highest-priority conclusions are:

1. Establish one canonical, normalized presentation snapshot per live group on the server. Client changes should update it through a bounded, event-driven, ordered synchronization path; no polling is needed.
2. Make **Save Workspace** flush the current client snapshot before capturing, so the button is a true point-in-time snapshot rather than “whatever the server last heard.”
3. Extend the explorer snapshot contract. The current schema keeps open tabs, the active tab, a selected file-view mode, one vertical scroll fraction, zoom, wrapping, folds, appearance, and sidebar open flags, but it does not keep all scrollbar positions or several structural explorer states.
4. Preserve `explorer_root_directory` on restart restore. The field is captured today and then overwritten during launch preparation.
5. Give `saved_sessions.json` the same basic durability properties as runtime state: one read-modify-write lock, unique temporary file, atomic replace, last-good backup/quarantine, and surfaced write failures.
6. Decouple persistence normalization from the mutable `terminal.max_sessions` setting. Lowering that setting currently truncates or mutates saved preset data on load/save and makes exact workspace restore fail.

No change is recommended to the established terminal/agent/browser content rules: do not snapshot terminal scrollback or process state, do not persist runtime status, and do not put passwords in `runtime_state.json`. The proposal changes how the existing launch/presentation fields are synchronized and validated, then adds the explorer UI fields explicitly requested for a true snapshot.

## Scope and evidence

The audit traced these paths end to end:

- live group/session construction in `sessions/manager.py`;
- normal launch, mode switching, and server-side restore in `web/workspaces.py`, `web/api.py`, and `web/terminal_io.py`;
- reusable preset normalization, save, import, and load in `web/saved_sessions.py`;
- restart capture, validation, commit, listing, and restore in `web/runtime_state.py`;
- group caching, pane ordering/layout, Save Session, Save All Sessions, and Save Workspace in `web/static/js/terminals.js`;
- explorer tab/view serialization and restore in `web/static/js/explorer-viewer.js` and related explorer modules;
- browser tab synchronization in `web/static/js/browser-pane.js`;
- launcher import/edit/relaunch in `web/static/js/launcher.js` and shared launch-field construction in `web/static/js/shared.js`;
- maintained contracts in `README.md`, `CLAUDE.md`, and `docs/workspace_implementation_audit_2026-08-06.md`;
- persistence and restore coverage in `tests/test_api.py`, `tests/test_multi_workspace.py`, and `tests/test_session_manager.py`.

`docs/r&d/` was not used as a source of record.

## Intended invariants

The implementation should preserve these invariants throughout the proposed work:

1. A reusable saved session is a launch preset. Its SSH password may be encrypted in `saved_sessions.json`.
2. A saved workspace is a password-free snapshot. A preset may supply a matching SSH credential during restore, but never shape.
3. **Save Session** captures the current group presentation while retaining the existing launcher-connection rules documented and tested today.
4. **Save Workspace** captures every live group in the workspace, including the presentation state visible at the instant the user initiated the save.
5. Autosave captures the latest acknowledged live presentation state without polling and without blocking on browser DOM work.
6. A restore either starts a complete validated group or reports why it did not. It must not silently drop a malformed pane and call the smaller group a successful exact restore.
7. Shared-state check-and-act operations occur under one `SessionManager.lock` hold. No emit, network request, filesystem write, SFTP operation, or process work occurs while that lock is held.
8. Every state-file commit uses a unique same-directory temporary path and atomic replacement. Concurrent writers cannot lose an unrelated preset or workspace.
9. New explorer state is bounded, root-relative where it contains paths, version-tolerant, and contains no file contents, editor buffers, passwords, search results, or Git credentials.
10. The normalizer used by Save Session, runtime-state validation, the live presentation endpoint, and restore has one definition for each field.

## Current state-authority map

The current model has three different meanings of “current”:

| State | Live authority | Saved Session source | Runtime autosave / Save Workspace source |
|---|---|---|---|
| group name and saved-preset identity | server | server plus save target | server |
| group-tab order | server (`display_order`) | server/client list | server |
| pane membership | server | server sessions | server |
| pane visual order | browser DOM | browser DOM | server insertion order |
| standard layout name | server | server | server |
| custom split rectangles/weights | browser | browser snapshot | launch-time server value |
| terminal/agent launch metadata | server | server-backed client session objects | server |
| browser tab URLs and active tab | browser, asynchronously mirrored to server | browser | server, possibly stale |
| explorer root and launch metadata | server | server-backed client session objects | server |
| explorer open/active tabs | browser only unless Save Session was used | browser | server, normally stale |
| explorer view mode, diff target, scroll, zoom, wrap, folds | browser only unless Save Session was used | browser | server, normally stale |
| explorer sidebar open flags | browser only unless Save Session was used | browser | server, normally stale |
| explorer theme | browser DOM/localStorage unless Save Session was used | browser | server, normally stale |
| Markdown/source appearance | page-global localStorage | browser | server, normally stale |
| active group in a workspace | server | not a preset property | server |
| native desktop zoom | native window queried at save | not a preset property | explicit save only |

This split is the reason field allowlists alone do not prove persistence correctness. `web/runtime_state.py` includes explorer fields in `_SESSION_SNAPSHOT_FIELDS`, but that only copies the values currently in `TerminalSession`; it does not make the browser send its newer values.

## Current flow review

### Save Session and Save All Sessions

`buildWorkspaceTerminalEntry()` in `web/static/js/terminals.js` reads each pane's current mode and serializes browser tabs and explorer state. Before serializing an explorer pane it captures the active tab's current view. Cached/off-screen groups capture their last visible explorer state before their DOM is detached, so Save All can serialize them without rebuilding the group.

The request posts a normalized `workspace_only` config to `POST /api/saved-sessions`. `_merge_workspace_session_config()` intentionally preserves the preset's existing connection/default-directory/title contract while updating live modes, browser/explorer presentation, shell selection, layout, and split geometry. The server then refreshes the live group's saved-session identity and presentation fields by session ID under one manager lock.

This is the cleanest existing path for explorer presentation. Its main weaknesses are the durability of `saved_sessions.json`, the incomplete explorer schema, and capacity-dependent normalization described below.

### Import Session and later launch

The launcher and terminals page fetch one normalized preset, build pane launch fields through the shared helpers, and launch through the same `POST /api/sessions` service as a normal launch. Explorer tab paths are root-relative and are discarded if the user edits the imported row's directory before relaunching. Browser tabs retain their whole strip while the active URL remains editable.

The launch path is largely clean and DRY. Server-side restart restore also reaches the same `launch_session_group()` function, so mode handling no longer has a separate restore implementation.

### Autosave and Save Workspace

Autosave calls `capture_live_workspaces(session_manager)`. Explicit Save Workspace sends only workspace ID, active group ID, and native zoom to `/api/runtime-state/save`, which also calls `capture_workspace(session_manager)`. Both paths copy the manager's live objects.

Neither path asks the browser for pane order, current split geometry, or explorer presentation. Explicit Save Workspace therefore is not currently a snapshot barrier. The durable file records a coherent server snapshot, but not necessarily the current screen.

### Restart restore

The restore path is server-owned and shape-authoritative:

- validated slots are loaded from `runtime_state.json`;
- a per-workspace restore claim prevents concurrent duplicate restore;
- selected slots are preflighted for duplicate preset ownership;
- each group request is built from snapshot shape alone;
- only a matching saved-preset SSH credential is injected in-process;
- launch, replacement, and restore use one atomic group-install path;
- errors are reported per group and the workspace is removed if no group starts.

The remaining restore defects are field-level normalization/capacity behavior and the explorer-root overwrite, not the orchestration model.

## Mode-by-mode field review

### Terminal mode

The runtime snapshot includes host, directory, username, port, title, initial command/mode, local shell flags, and WSL distribution. Password, session ID, connection status, timestamps, error state, terminal buffer, and terminal viewport are intentionally absent.

This matches the current contract and should remain unchanged. “Snapshot” means relaunchable shape, not a suspended shell process.

### Agent mode

Agent selection, custom command, base initial command, and auto-mode flag are represented in `TerminalSession`, saved-session normalization, runtime capture, shared launch helpers, and restore. Restore also correctly skips the cold agent preflight that could erase a previously valid command.

No new agent-specific persistence defect was found. Agent panes still inherit the common pane-order, custom-layout, capacity, and saved-store findings.

### Browser mode

The full bounded URL list and active index are normalized, saved, loaded, and restored. The active URL is mirrored into `initial_command` for compatibility. Older one-URL presets upgrade to a one-tab strip.

The data model is adequate under the stated browser contract. The defect is synchronization ordering: the browser uses a 400 ms debounce and fire-and-forget requests to the mode endpoint. An explicit Save Workspace can overtake the pending request, and two in-flight requests can complete out of order.

### Explorer mode

Already represented and normalized:

- explorer root launch metadata;
- Files, Git, and Search sidebar open flags;
- ordered pinned file tabs and active pinned tab (empty means Preview);
- Preview tab's file path or browsed directory;
- Source, Preview, or Diff mode;
- worktree/staged/commit diff target;
- one active panel vertical scroll fraction guarded by a content identity;
- per-tab font size;
- per-tab Source/Preview/Diff wrap opt-outs;
- Markdown folds guarded by a content identity;
- Markdown preset, Markdown font, source/diff font, and explorer light/dark theme.

Not represented in the durable schema:

- horizontal scroll for Source/Diff/Preview;
- separate scroll positions for each view panel;
- directory-list scroll;
- Files, Git, and Search sidebar scroll;
- sidebar width;
- expanded Files-tree paths;
- tab-strip horizontal scroll;
- expanded Git history rows;
- Search query, result-group expansion, selected result, or result scroll;
- file-find query and selected match.

Some of those should remain ephemeral. The open questions at the end define the product decisions needed before expanding the schema.

## Findings

Severity describes user-data or restore impact, not exploitability.

### SGP-01 — High: runtime snapshots read a stale presentation source

Evidence:

- `SessionManager.snapshot_live_workspaces()` serializes only server objects (`sessions/manager.py:1078`).
- autosave calls that snapshot directly (`web/api.py:1944`).
- Save Workspace sends no group or pane presentation data (`web/static/js/terminals.js:2353`) and the route calls `capture_workspace()` directly (`web/api.py:1784`).
- explorer tab persistence writes only `pane._session` in the browser (`web/static/js/explorer-viewer.js:6939`). There is no explorer presentation endpoint.
- custom geometry is built only for Save Session (`web/static/js/terminals.js:2002`, `web/static/js/terminals.js:2172`).
- pane card reordering changes DOM order; the server continues to enumerate sessions in dictionary insertion order (`sessions/manager.py:983`).

Impact:

- restart can restore old/empty explorer tabs, old sidebar flags, old theme/fonts, and no current view/scroll even after a successful manual workspace save;
- a dragged pane order can revert;
- split rectangles and resized track weights can revert to launch-time geometry;
- later durable/autosave hardening cannot correct the source-data gap.

Low-risk direction:

Add one bounded group-presentation update service in the correct backend module and one dedicated frontend persistence module. The batch should identify the workspace, group, exact live session IDs, pane order, layout/geometry, and presentation-only fields. Normalize first, then verify and apply the complete batch under one `SessionManager.lock` hold. Do no emit or file work under the lock.

The client should be event-driven and coalesced, with at most one in-flight update per group. Save Workspace must await a flush of every group's latest local snapshot before calling the runtime-state save route. Autosave then consumes the last acknowledged state; it does not poll the browser.

### SGP-02 — High: browser tab writes can race Save Workspace and each other

Evidence:

- `browserPersistTabs()` waits 400 ms for normal changes and starts `push()` without returning a promise to its caller (`web/static/js/browser-pane.js:168`).
- the local `pane._session` update occurs inside `push()`, immediately before an asynchronous fetch, but Save Workspace does not serialize that local object.
- the timer map tracks only pending timers, not an in-flight request. A newer request can be sent while an older one is still in flight.
- `update_browser_tab_strip()` has no client revision or compare-and-swap check (`sessions/manager.py:777`). Last arrival wins, even if it is older.

Impact:

- navigating, opening, closing, or switching a browser tab and immediately clicking Save Workspace can save the previous strip;
- a slow older request can overwrite a newer strip on the server and later autosave the regression;
- the comment that the local session update protects Save Workspace is true for Save Session serialization, but not for the runtime save path.

Low-risk direction:

Move browser tab presentation onto the common ordered group-presentation queue from SGP-01. Keep the mode endpoint for actual mode transitions. If an intermediate migration keeps tab writes on the existing endpoint, serialize them with one in-flight/coalesced queue and make `browserPersistTabs(..., { immediate: true })` return an awaitable promise used by Save Workspace.

### SGP-03 — High: explorer snapshot coverage does not meet the requested snapshot semantics

Evidence:

- `captureExplorerFileScroll()` already knows horizontal and vertical metrics for every file panel plus Files/Git sidebar scroll (`web/static/js/explorer-viewer.js:5560`).
- `explorerPersistableTabView()` reduces that rich state to the selected mode and one vertical fraction (`web/static/js/explorer-viewer.js:6763`).
- directory view snapshots have an empty mode; `_normalize_explorer_view_snapshot()` rejects them, so a persisted Preview directory keeps its path but not its scroll (`web/saved_sessions.py:202`).
- sidebar width and expanded tree paths exist only in live pane fields (`web/static/js/explorer-viewer.js:1692`, `web/static/js/explorer-viewer.js:1846`).

There is also a semantic coupling problem: mode and scroll share the same content-identity gate. When file content changes, `explorerMatchingTabView()` rejects the whole view record, so the chosen Preview/Diff mode can fall back to Source along with the intentionally discarded scroll (`web/static/js/explorer-viewer.js:5735`, `web/static/js/explorer-viewer.js:7262`). A user's durable mode preference is not the same thing as a content-relative scroll coordinate.

For staged diffs, the current identity is based on path, working-file content, and the word `staged`; an index change that leaves the working file unchanged can incorrectly look identical and restore scroll into different diff content.

Impact:

- current tabs and some appearance settings survive Save Session, but the restored explorer is not a complete visual snapshot;
- Preview/Diff intent can be lost after an external file change;
- some scroll restoration can be applied to changed staged diff content.

Low-risk direction:

Version the explorer presentation record and separate:

- durable intent: view mode, diff selector, open tabs/order, active tab, appearance, wrap, sidebar open/width, expanded paths;
- content-bound state: per-panel scroll and folds, each guarded by the relevant file/diff/directory revision.

Store normalized scroll ratios rather than pixels. Persist only intent needed to rebuild; re-fetch Files/Git/Search data instead of storing results. Keep strict caps on paths, expanded nodes, hashes, queries, and maps.

### SGP-04 — High: restore captures and then discards a distinct explorer root

Evidence:

- `explorer_root_directory` is in the session model and runtime snapshot allowlist (`sessions/manager.py:73`, `web/runtime_state.py:162`).
- `_prepare_launch_sessions()` overwrites it with `directory` for both SSH and local explorer panes (`web/workspaces.py:410`, `web/workspaces.py:442`).

The two fields can legitimately differ. A pane may be rooted at `/srv/app` while its current terminal-derived directory is `/srv/app/src`. Restore currently narrows the explorer root to the latter even though it captured the former.

Impact:

- parent navigation and root-confined explorer operations can expose a smaller tree after restart than before save;
- the persisted field appears supported but is ineffective on the common launch/restore path.

Low-risk direction:

In launch preparation, preserve a provided normalized `explorer_root_directory` and fall back to `directory` only when it is absent. Add local and mocked-SFTP behavioral tests proving `directory` remains inside the root and invalid roots degrade safely. Do not weaken the explorer backend's root-confinement checks.

### SGP-05 — High: `saved_sessions.json` has lost-update and torn-write paths

Evidence:

- upsert, last-session update, and delete are separate load/modify/save operations with no shared in-process or OS-level lock (`web/saved_sessions.py:1020`, `web/saved_sessions.py:1063`, `web/saved_sessions.py:1072`).
- the file is written directly with `open(..., "w")` and `json.dump()` (`web/saved_sessions.py:923`).
- two processes or two request threads can read the same old payload and each replace it; the later write loses the other's unrelated update.
- a crash or disk failure after truncation can leave invalid JSON.
- a read error returns an empty store. A later successful save can overwrite the damaged file, removing the last chance to recover older presets.
- deleting the final preset catches `os.remove()` failure, logs a warning, and still returns an empty success result even though the old file may remain.

The runtime-state store already demonstrates the required repository pattern. The saved-session store does not need runtime-state tickets or workspace tombstones, but it does need one locked read-modify-atomic-replace transaction.

Impact:

- concurrent Save Session / Save All / delete / last-session selection can lose presets;
- interruption can corrupt the whole preset store;
- callers can receive success for a deletion that did not reach disk;
- encrypted passwords and all non-secret preset data share the same failure domain.

Low-risk direction:

Introduce a small `SavedSessionStore` in `web/saved_sessions.py` (or a focused new `web/saved_session_store.py`) with:

- one in-process lock plus an OS-level sibling lock file;
- one locked read-modify-write callback per operation;
- unique same-directory temp files, flush/fsync where supported, and `os.replace`;
- a last-good `.bak` and corrupt-file quarantine behavior aligned with runtime state;
- a dedicated persistence exception mapped to retryable non-2xx API responses;
- no plaintext password in logs, exceptions, backup metadata, or diagnostics.

Also make first-run Fernet-key creation exclusive. `_get_encryption_key()` currently checks existence and then writes, so two first-start processes can generate different keys; the process that loses the file race may keep a cipher built from a key no longer on disk (`web/secrets.py:15`). Use exclusive create, then let the loser read the winner's complete key.

### SGP-06 — High: persistence normalization is coupled to the mutable launch cap

Evidence:

- `_normalize_terminal_entries()` iterates `range(runtime_config.max_sessions)` (`web/saved_sessions.py:514`).
- `_normalize_session_config()` clamps `terminal_count` to that same current value (`web/saved_sessions.py:625`).
- `_normalize_workspace_layout()` clamps origin slots and geometry bounds using the mutable setting (`web/saved_sessions.py:409`).
- every saved-session load normalizes through those functions; every subsequent save rewrites all entries from the normalized in-memory list.
- runtime-state validation intentionally keeps a larger stored group visible, but `launch_session_group()` refuses a group above the current cap (`web/workspaces.py:521`).

Example: save an eight-pane preset while `max_sessions=8`, lower the setting to four, then update or delete another preset. The eight-pane entry is loaded as four panes and the next store rewrite makes that truncation durable. Raising the setting later cannot recover the removed panes. A captured eight-pane workspace remains listed but its group restore fails.

Impact:

- a runtime preference can destroy stored data unrelated to the preference change;
- import/load is not a faithful read of the file;
- README's exact-snapshot restore promise conflicts with current behavior.

Low-risk direction:

Separate constants and responsibilities:

- storage/schema cap: the absolute supported product maximum (currently 16 for normal panes);
- current launch cap: `runtime_config.max_sessions`;
- corruption cap: the runtime-state defensive ceiling.

Normalize stored presets against the storage cap, never the current preference. Enforce the current launch cap only at a launch/split boundary and return an actionable error without mutating the stored config. The exact restore behavior when the current cap is lower is an open product question below.

### SGP-07 — Medium: runtime nested-field validation is shallower than its contract claims

Evidence:

- `_validate_session()` copies the session allowlist without normalizing field types or nested explorer/browser structures (`web/runtime_state.py:259`).
- `_validate_group()` therefore counts a dictionary-shaped pane as valid even if `browser_active_tab`, `explorer_open_tabs`, or `explorer_tab_views` has an invalid type.
- `SessionManager.install_session_group()` catches per-pane staging exceptions and continues with the remaining panes (`sessions/manager.py:639`, `sessions/manager.py:675`). A malformed pane can disappear while the group still launches successfully.

Real snapshots produced by this build normally carry valid types, but backups, older versions, hand edits, partial external writes, or a future live-presentation endpoint can reach this path. The restore chooser's count can then disagree with the successful restore result—the exact class of disagreement the runtime-state validation gate is meant to prevent.

Impact:

- a corrupt group can restore partially rather than fail visibly;
- invalid nested state can be passed farther into the client than intended;
- four normalization sites can drift.

Low-risk direction:

Extract presentation-field normalizers from `web/saved_sessions.py` into one import-cycle-safe module and use them for saved presets, runtime-state read validation, live presentation updates, and launch preparation. For restore, reject the whole group if any captured pane is not launchable after validation; report a per-group error and leave the snapshot available for retry/recovery.

### SGP-08 — Medium: Markdown/source appearance is modeled per pane but implemented page-globally

Evidence:

- every `TerminalSession` carries `explorer_md_preset`, `explorer_md_font`, and `explorer_source_font`.
- `explorerMarkdownAppearance()` reads shared localStorage keys, and `setExplorerMarkdownAppearance()` applies the result to every explorer view in the page (`web/static/js/explorer-viewer.js:4096`).
- restore applies each session's saved appearance once and writes it back to the shared keys (`web/static/js/explorer-viewer.js:6569`).

Normally all panes save the same values because the live control is global. If imported data contains different per-pane values, render order decides the winner and the last applied session changes every pane. The schema promises a scope the UI does not implement.

Impact:

- per-pane saved values are not independently restorable;
- restoring groups in a different order can change the final global appearance;
- future synchronization can amplify the ambiguity unless scope is decided first.

Low-risk direction:

Choose one scope explicitly. If appearance is workspace/global, store it once at that scope and let pane fields remain backward-compatible read aliases during migration. If it is per pane, remove the shared localStorage authority and apply appearance to the pane root only. Do not add another duplicate source.

### SGP-09 — Low: explorer theme localStorage accumulates dead session IDs

Explorer light/dark overrides are stored in a JSON object keyed by ephemeral `session_id` (`web/static/js/terminals.js:389`). New restart sessions receive new IDs, and close/teardown removes DOM attributes but not the saved object entry. Long-running use can grow the object indefinitely.

This is not the primary persistence defect, but the common presentation store makes it unnecessary. Once accepted theme state lives in the manager, prune the local override after acknowledgement or bound/migrate the legacy object. Do not remove it before server synchronization is reliable, because it currently protects live UI state from some rebuilds.

### SGP-10 — Medium: current tests prove field presence more often than a user-visible round trip

Positive coverage already exists for:

- saved-session explorer tab/path normalization and bounds;
- view mode, scroll fraction, font, wrapping, folds, and appearance normalization;
- browser tab bounds and legacy one-tab upgrade;
- runtime-state ordering, lock, backup, quarantine, restore claims, duplicate preset preflight, and exact shape authority;
- manager model round trips for explorer/browser/agent fields.

Important gaps:

- several frontend tests assert JavaScript source strings rather than execute serialization/restore behavior;
- `test_runtime_state_snapshot_includes_tab_views_and_md_appearance` checks only allowlist membership (`tests/test_api.py:16864`), not that live browser state reached the server before capture;
- no test changes explorer UI state, runs autosave or Save Workspace, restarts, and asserts the restored pane state;
- no test covers an explicit save racing the browser debounce;
- no test covers two out-of-order browser presentation responses;
- no test covers pane visual reorder/custom geometry through runtime save and restore;
- no test covers a distinct explorer root through runtime restore;
- no test covers concurrent saved-session upserts/deletes or interrupted writes;
- no test proves lowering `max_sessions` leaves stored preset bytes/shape intact.

The implementation stages below replace touched source-text assertions with API/manager contract tests and small executable JavaScript tests where practical. The repository already has precedent for invoking Node from unittest when available.

## Risk-ranked implementation proposal

Each stage is independently reviewable and preserves backward compatibility. New optional fields should be ignored by old readers; new readers must accept old files.

### Stage 0 — Freeze the snapshot contract with failing behavioral tests

Connected findings: SGP-01, SGP-02, SGP-03, SGP-04, SGP-06, SGP-07, SGP-08, SGP-10.

Add tests before production changes:

1. Build a live explorer pane whose manager state is old and whose client snapshot is new; assert the current Save Workspace path demonstrates the mismatch.
2. Model browser persistence A then B with A completing last; assert the old implementation regresses and the replacement queue does not.
3. Save/restore a group after pane reorder and split-track resize.
4. Save/restore an explorer rooted at a parent while viewing a child directory.
5. Define an explorer fixture with Preview plus pinned tabs, active Diff, per-panel scrolls, zoom, wrap, folds, theme/fonts, sidebars, width, and tree expansion.
6. Lower `max_sessions` after saving a larger preset and prove load plus an unrelated write does not alter its stored shape.
7. Feed invalid nested presentation data through runtime validation and prove no partial group is advertised as exact.

Use backend behavioral tests for storage/manager transactions. Put pure browser snapshot normalization and queue logic in a small standalone JS module so Node-based tests can execute it without a DOM. Avoid adding new raw source-text assertions.

Exit gate: the desired field matrix and the deliberate exclusions are executable contracts, and the open questions below have answers for any field included in Stage 4.

### Stage 1 — Make saved-session and encryption-key persistence durable

Connected findings: SGP-05, SGP-10.

1. Wrap upsert, delete, and last-session changes in one locked read-modify-write transaction.
2. Use a unique sibling temp file and atomic replace; retain and recover a last-good backup.
3. Quarantine invalid/unsupported payloads instead of returning an empty store that a later save overwrites.
4. Surface write/delete failures as retryable API errors; never return a false success.
5. Make Fernet-key first creation exclusive and durable.
6. Add paused-writer thread/process tests, replace-failure tests, corrupt-primary/valid-backup tests, and secret-redaction assertions.

Keep the public JSON shape and encryption format unchanged. This stage is isolated from frontend behavior and has a small rollback surface.

Exit gate: concurrent unrelated saved-session mutations survive, interrupted writes retain a readable last-good store, and no plaintext secret reaches logs or runtime state.

### Stage 2 — Add one canonical live group-presentation transaction

Connected findings: SGP-01, SGP-02, SGP-07, SGP-10.

Recommended architecture:

- backend normalizers in a focused module such as `web/session_presentation.py`;
- manager-owned `presentation_revision` and explicit pane ordering;
- one thin route in `web/api.py` delegating to a service, not new domain logic in the API monolith;
- frontend capture/queue/flush logic in a new `web/static/js/session-persistence.js`, not more persistence code in `terminals.js` or `explorer-viewer.js`.

The group update should contain only presentation fields and exact identities:

```json
{
  "workspace_id": "...",
  "group_id": "...",
  "expected_revision": 12,
  "pane_order": ["session-a", "session-b"],
  "layout": "split",
  "workspace_layout": {"...": "bounded geometry"},
  "panes": [
    {"session_id": "session-a", "explorer_open_tabs": ["docs/a.md"]},
    {"session_id": "session-b", "browser_tabs": ["http://127.0.0.1:3000"]}
  ]
}
```

Rules:

1. reject unknown, missing, duplicate, cross-group, or cross-workspace session IDs;
2. normalize every field before the manager lock;
3. compare revision, verify membership, update pane order/group geometry/all pane fields, and increment the revision in one lock hold;
4. deep-copy nested state;
5. emit any room-scoped update only after releasing the lock;
6. never accept password, host, username, command, filesystem contents, or status in this route;
7. return `409` for a stale revision with the current revision, never silently apply an older state.

The client queue should maintain one in-flight request and one coalesced latest snapshot per group. A response cannot overwrite newer local state. Socket notifications should carry only group/revision metadata and remain workspace-room scoped.

Exit gate: the manager is the canonical acknowledged presentation source, update order is deterministic, and autosave sees either a complete old group presentation or a complete new one.

### Stage 3 — Wire exact Save Workspace and existing browser/explorer state into the canonical source

Connected findings: SGP-01, SGP-02, SGP-09, SGP-10.

1. Route existing explorer tab/view/sidebar/theme changes through the Stage 2 queue.
2. Route browser tab changes through the same ordered queue; stop using fire-and-forget tab updates on the mode endpoint.
3. Capture pane order and custom layout/weights whenever drag/resize settles.
4. Capture visible group state before caching/detaching it, as today, then enqueue that snapshot.
5. On **Save Workspace**, synchronously capture all visible/cached groups, await queue flush/ack, and only then call `/api/runtime-state/save`.
6. If the flush fails, show the existing retryable failure affordance and do not claim the workspace was saved.
7. Leave autosave non-blocking. It snapshots the most recently acknowledged manager state; normal debounce bounds the lag, while explicit save remains exact.
8. Migrate/prune legacy explorer-theme localStorage entries only after acknowledgement is reliable.

Do not use `beforeunload` as the correctness mechanism; browsers may cancel asynchronous work. State-change events plus the explicit save barrier are the reliable paths.

Exit gate: change tabs/view/layout, immediately click Save Workspace, restart, and restore the exact acknowledged state. A forced failed sync produces no success toast.

### Stage 4 — Complete explorer snapshot semantics and preserve explorer root

Connected findings: SGP-03, SGP-04, SGP-08, SGP-10.

1. Preserve a supplied `explorer_root_directory` through `_prepare_launch_sessions()` with the existing root-confinement validation.
2. Version the explorer presentation record while continuing to read the current flat record.
3. Separate view intent from content-bound state.
4. Add bounded per-panel horizontal/vertical ratios, directory-list scroll, sidebar scroll/width, expanded tree paths, and any additional state approved in the open questions.
5. Restore intent even when content changed; restore scroll/folds only when the relevant revision matches.
6. For Diff, use an identity that changes with the rendered diff (commit hash or Git/index/worktree revision), not only working-file content plus mode.
7. Re-fetch directory, tree, Git, and search data. Persist only normalized navigation/expansion intent.
8. Resolve Markdown/source appearance scope before migrating its fields.

Suggested backward-compatible record shape:

```json
{
  "version": 2,
  "intent": {"mode": "diff", "diff_mode": "staged"},
  "content_revision": "bounded opaque revision",
  "scroll": {
    "source": {"x": 0.0, "y": 0.3},
    "preview": {"x": 0.0, "y": 0.6},
    "diff": {"x": 0.2, "y": 0.4}
  },
  "font_size": 18,
  "wrap": {"source": true, "preview": true, "diff": false},
  "folds": [12, 44]
}
```

The exact keys are less important than one canonical normalizer and separate identity semantics.

Exit gate: the agreed explorer fixture round-trips through Save Session/import, manual workspace save/restart restore, and autosave/restart restore on both local and mocked SSH/SFTP explorers.

### Stage 5 — Decouple stored shape from current capacity and harden restore validation

Connected findings: SGP-06, SGP-07, SGP-10.

1. Normalize saved data against an immutable schema/product maximum, not `runtime_config.max_sessions`.
2. Preserve extra stored terminal entries even when the current launch cap is lower.
3. Make workspace-layout normalization use schema-safe bounds and the actual stored pane count; apply current-cap checks only at launch/split.
4. Validate every nested pane field through the canonical presentation normalizer.
5. Make a malformed restored group fail as a group rather than silently dropping panes.
6. Return an actionable capacity error and keep the snapshot/preset untouched.
7. Apply the chosen capacity policy from the open questions.

Exit gate: lowering and raising the setting is nondestructive, restore chooser counts match validated launchable shape, and no partial success is reported as exact restore.

### Stage 6 — Documentation, diagnostics, and cleanup

Connected findings: all, especially SGP-09 and SGP-10.

1. Update `README.md` and `CHANGELOG.md` with the final snapshot boundary and capacity behavior.
2. Keep `CLAUDE.md` and `AGENTS.md` field/architecture contracts accurate if a new presentation module or route is added.
3. Log safe shape diagnostics only: workspace/group/session IDs, revisions, mode names, field counts, and failure category. Never log paths, URLs with secrets, commands, file contents, passwords, or full payloads.
4. Remove superseded browser-tab and local-only explorer persistence writers after every consumer uses the canonical path; do not leave dual writers.
5. Run the full Windows gates: `python tests/run_tests.py` and `python -m ruff check .`.

Exit gate: maintained docs match behavior, dead writers are gone, every server event has a consumer, and the full suite passes without source-text-only tests being added.

## Proposed regression matrix

The implementation should include at least these behavioral cases:

1. terminal, agent, browser, local explorer, and SSH explorer in one group round-trip through Save Session/import;
2. the same mixed group round-trips through manual Save Workspace/restart restore;
3. the same mixed group round-trips through autosave/restart restore after the presentation queue acknowledges;
4. explorer Preview plus multiple pinned tabs preserves order and active Preview/pinned selection;
5. Source, Preview, worktree Diff, staged Diff, and commit Diff preserve intent;
6. changed file/diff content preserves mode intent but suppresses stale scroll/folds;
7. per-panel vertical/horizontal scroll, directory scroll, sidebar scroll/width, and approved expansion state round-trip within bounds;
8. missing files are dropped/fallback safely without blocking the rest of the pane;
9. explorer root is wider than current directory and remains so after restore, locally and through mocked SFTP;
10. pane reorder and custom split geometry/weights survive both persistence products;
11. Save Workspace immediately after a browser/explorer change waits for the acknowledged revision;
12. response A arriving after newer response B cannot regress server state;
13. a stale second window receives `409` and cannot overwrite a newer presentation silently;
14. closing/moving a group during presentation sync cannot update another group or resurrect a closed one;
15. saved-session concurrent create/update/delete preserves unrelated entries;
16. failed replace/delete returns retryable failure and keeps the last-good file;
17. corrupt primary plus valid backup recovers without overwriting the corrupt evidence;
18. two first-run processes converge on one Fernet key;
19. lowering `max_sessions` does not change stored shape; capacity failure is actionable and nondestructive;
20. malformed nested explorer/browser state rejects the group rather than producing a smaller successful restore;
21. runtime-state snapshots and logs contain no passwords;
22. Socket.IO presentation notifications remain workspace-room scoped and no emit occurs under manager/connection locks.

## Open questions

These choices affect schema and should be answered before Stage 4. Recommended defaults are included.

1. **What is the explorer snapshot boundary?** Recommended: persist structural navigation and view intent (tabs/order/active tab, Preview path/directory, Source/Preview/Diff, diff target, zoom/wrap/folds, theme/appearance, sidebar open/width/scroll, tree expansion, panel/directory scroll). Do not persist selections, hover, transient errors/loading, fetched results, or clipboard state.
2. **Should dirty editor buffers be included?** Recommended: no. They contain unsaved file contents, complicate revision conflict handling, and would put filesystem content in workspace state. Keep the existing confirm/discard behavior and persist only successfully saved files.
3. **Should Git commit-message drafts or destructive-action form state be included?** Recommended: no. Persist Git view/expansion intent only, never mutation drafts or busy state.
4. **Should file-find and repository-search queries/results be restored?** Recommended: optionally persist bounded query text and collapsed/selected UI intent, but never results; re-run only after the pane is visible and only if the user confirms this is desirable. The lowest-risk first implementation omits queries and keeps only Search sidebar open/width/scroll.
5. **Are Markdown preset/font/source-font app-global, workspace-global, group-global, or per explorer pane?** Current UI is page-global while storage is per pane. Recommended: workspace-global if these settings are intended to travel with a saved workspace; otherwise move them to app config and remove them from per-pane snapshots. Per-pane behavior has the largest frontend styling change.
6. **When `max_sessions` is lower than a stored group, should restore temporarily exceed the preference or refuse with an “increase to N and retry” action?** Recommended: preserve stored data and refuse actionably; do not silently truncate and do not silently mutate global settings. If exact one-click restore is more important, explicitly authorize restore to bypass the preference up to the immutable product maximum.
7. **Can two browser windows intentionally control the same live workspace at once?** Recommended: treat one accepted group revision as authoritative and reject stale updates with compare-and-swap. If collaborative multi-view control is desired, merge rules and an ownership model need a separate design.
8. **Should Save Session update an explorer preset's root to the pane's current root, or preserve the launcher's original directory contract?** Current documented/tested behavior preserves launcher directories while saving presentation. Recommended: keep that rule unless “snapshot” is intended to retarget future launches; runtime Save Workspace should always preserve the live `explorer_root_directory` exactly.
9. **When a persisted file or diff target no longer exists, should the tab be dropped or shown as a retryable missing tab?** Current behavior drops/falls back. Recommended: retain that low-friction behavior unless missing tabs need to remain visible as historical intent.

## Audit verification

The documentation change was checked with the repository's Windows gates:

- `python -m ruff check .`: passed.
- `python tests/run_tests.py`: 1,204 tests ran; 1,195 passed, 7 skipped, and 2 unrelated voice-environment tests failed because this environment does not have `websocket-client`/an available external Vosk service. No persistence, workspace, saved-session, explorer, browser, session-manager, or restore test failed.

## Final recommendation

Do not start by adding more fields directly to `runtime_state.py`. The durable store already records whatever the manager gives it. First make the manager the canonical acknowledged source for group presentation, make explicit save a flush barrier, and make the saved-session store durable. Then expand the explorer schema through one normalizer and one synchronization path.

That order has the lowest blast radius: it fixes data authority and write safety before increasing the amount of state being persisted, keeps all slow work outside shared locks, avoids polling, preserves the server-owned restore model, and prevents another round of duplicate client/server persistence logic.
