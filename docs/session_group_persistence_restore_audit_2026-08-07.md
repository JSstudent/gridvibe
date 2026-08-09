# Session-group persistence and restore audit

Date: 2026-08-07

Status: findings and implementation proposal. Stage 0 is **done** — the snapshot contract is frozen as executable tests in `tests/test_session_persistence_contract.py` (see [Stage 0 results](#stage-0-results)). Stage 1 is **done** — the saved-preset store and the encryption key are durable (see [Stage 1 results](#stage-1-results)); SGP-05 is closed. Stage 2 is **done** — the ordered live presentation transactions, canonical normalizer, manager revisions/order, and DOM-free client queue are implemented (see [Stage 2 results](#stage-2-results)). Stage 3 is **done** — the live page is wired to those transactions, Save Workspace is an exact flush barrier, and both superseded writers are gone (see [Stage 3 results](#stage-3-results)); SGP-01 and SGP-02 are closed. Stage 4 is **done** — voluntary close/restart uses one process-wide flush/save/exit transaction, and its Stage 6 items 1–3 prerequisite shipped with it (see [Stage 4 results](#stage-4-results)); SGP-11 is closed and SGP-06 is narrowed to the remaining launch-validation work. Stage 5 is **done** — explorer v2 presentation records, complete bounded structural state, exact explorer roots, and workspace-scoped appearance are implemented (see [Stage 5 results](#stage-5-results)); SGP-03, SGP-04, and SGP-08 are closed. Stage 6 is **done** — nested pane state is validated through the canonical non-coercing normalizer at both the launch and runtime-state read boundaries, a rejected pane fails its whole group, and the capacity refusal is actionable and nondestructive (see [Stage 6 results](#stage-6-results)); SGP-06 and SGP-07 are closed, and SGP-10 is narrowed to Stage 7's legacy source-text cleanup. Stage 7 is **done** — the maintained docs match the shipped behavior, lifecycle logging is shape-only, both legacy localStorage cache families are bounded, and the legacy source-text assertions are removed (see [Stage 7 results](#stage-7-results)); SGP-09 and SGP-10 are closed. All seven stages are complete and every finding is closed.

Stages 0–4 were then re-reviewed end to end against the source and the guardrails; the result is in [Stages 0–4 verification review](#stages-04-verification-review-2026-08-08). The shipped work holds — gates are green and non-flaky, and no `## Regression Guardrails` rule is breached — with one defect found in Stage 4's own code (SGP-12). Stage 4.5, added by that review to collect SGP-12 plus the two `docs/r&d/todos.txt` items no existing stage covered (SGP-13, SGP-14), is now **done** — see [Stage 4.5 results](#stage-45-results); all three findings are closed, and item 1 shipped before Stage 5 or 6 began, as the closing recommendation requires. Stage 5 is now **done** after a fresh validity review against the shipped source and product decisions, and Stage 6 is **done** after the same treatment. Stage 7 is now **done** too — see [Stage 7 results](#stage-7-results).

One production change has since landed outside the audit's stage sequence: workspace top-bar visibility is now persisted and restored, and the launcher's **Save & Restart** was corrected to capture every live workspace. It touches surfaces Stages 2, 3, 4, 6, and 7 own. Its effect on the plan is recorded in [Out-of-band change: workspace top-bar visibility](#out-of-band-change-workspace-top-bar-visibility), and the affected findings and stages carry amendment notes inline. It did not flip any frozen Stage 0 test.

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
6. Decouple persistence normalization from the mutable `terminal.max_sessions` setting. Lowering that setting currently truncates or mutates saved preset data on load/save, silently rewrites stored split geometry on the runtime-state read path, and makes exact workspace restore fail. This decoupling must land before any lifecycle action that mass-writes presets, or that action becomes a one-click truncation of every live group's preset.
7. Replace the asymmetric restart/close paths with one explicit lifecycle decision. The current **Save & Restart** saves only the default workspace, does not save reusable sessions, and restarts even after a failed save; ordinary application close saves nothing new.

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
- restart, browser shutdown, and native-window teardown in `web/static/js/launcher.js`, `web/api.py`, and `web/webview_launcher.py`;
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
11. A close/restart action never claims a save succeeded and never tears down after a failed requested save without a second explicit user decision. Plain close/restart does not rewrite or clear previously committed snapshots or presets.

## Current state-authority map

The current model has three different meanings of “current”:

The table below is the map **as the audit found it**. Stage 3 changed the third column for every browser-owned row; the post-Stage-3 map is in [Stage 3 results](#stage-3-results). The original is kept because the findings below reference it.

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
| workspace top-bar visibility | browser DOM, mirrored to the server on toggle | not a preset property | server hint, plus the exact DOM value on explicit Save Workspace |

The last row is the out-of-band change. It is the only browser-owned presentation field that currently reaches the server on change *and* is read straight from the DOM by explicit Save Workspace, which is why it is nearly right and still needs Stage 2/3 work: the on-change writer is a second ad-hoc route with no ordering guarantee, and the launcher reads a `localStorage` copy of it rather than asking the window that owns it.

This split is the reason field allowlists alone do not prove persistence correctness. `web/runtime_state.py` includes explorer fields in `_SESSION_SNAPSHOT_FIELDS`, but that only copies the values currently in `TerminalSession`; it does not make the browser send its newer values.

### Two stored versions of a session group

A live session group can have two independent persisted representations. They are intentionally not kept synchronized by ordinary autosave:

| Representation | Stored in | Written by | Read by | Meaning |
|---|---|---|---|---|
| reusable session preset | `saved_sessions.json` | **Save Session**, **Save Session As**, **Save All Sessions**, and launcher preset editing/import | **Import Session** and normal saved-session launch | The most recent version the user explicitly saved as a reusable session. |
| group inside a workspace snapshot | `runtime_state.json` | workspace autosave and **Save Workspace** | **Restore Workspace** after restart | The group state captured as part of that workspace's latest successfully committed snapshot. |

Therefore **Import Session** loads the latest manually saved preset version; it does not load a newer workspace-autosave snapshot. Conversely, **Restore Workspace** loads the group stored in the workspace snapshot; it does not replace that shape with a newer version from the saved-session library. A matching saved preset may supply its encrypted SSH password during workspace restore, but its layout, panes, modes, browser/explorer tabs, and presentation fields remain non-authoritative.

The two versions can legitimately differ. For example, saving a session, changing its explorer tabs, and allowing workspace autosave to run produces an older reusable import preset and a newer restorable workspace group. Saving the session again updates the reusable preset, but an already committed workspace snapshot remains unchanged until the next workspace capture.

The proposed **Save open sessions + workspaces** lifecycle action deliberately aligns them at that point in time: save/update every reusable session preset first, attach the resulting preset identities to the live groups, then capture the workspaces. This does not merge the two products or change restore authority; it only makes their separately stored versions start from the same state when the application closes or restarts.

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

There is one current slot per workspace, not separate “last manual” and “latest auto” restore versions. Every successful non-empty capture replaces that slot's `groups`, `active_group_id`, label, and save time. A manual save records `manually_saved_at`; a later autosave retains that manual-retention marker but replaces the slot's shape with the newer live manager snapshot. Restore therefore uses:

| Capture history before restart | Shape restored |
|---|---|
| manual save, then no later successful capture | the manual-save shape |
| manual save, then a later successful autosave | the later autosave shape |
| restored workspace changed, then autosave commits | the current acknowledged live shape after those changes |
| UI changed but its browser-owned state was not acknowledged before capture/exit | the last server-known shape, which may be older than the screen |

The manual marker pins/protects the workspace's retention; it is not a pointer to a frozen manual generation. A matching saved-session preset may contribute an SSH password during restore, but it never replaces the slot's shape.

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

### Restart and application close

The current lifecycle paths do not share one persistence contract:

- The launcher's **Save & Restart** calls `/api/runtime-state/save` without a `workspace_id`. The route therefore resolves the default workspace and manually captures that workspace only. It does not call **Save All Sessions**, so `saved_sessions.json` is unchanged.
- That launcher save sends no client presentation payload or revision barrier. It captures the manager's current default-workspace state, with the stale explorer/browser limitations described above.
- A failed save only changes the status text. The code still calls the native restart bridge and terminates all live sessions.
- An update-triggered automatic restart calls the native restart bridge directly and does not run the launcher's workspace-save helper.
- The explicit browser-mode close button confirms, calls `/api/browser-shutdown`, closes all sessions, and exits without a new snapshot.
- Closing the native launcher window is application shutdown: its `closed` handler closes all sessions and exits without a new snapshot. Closing an individual workspace window while the launcher remains open is different; it hides that window without ending its sessions.
- Keyboard interrupt and external process termination also have no teardown snapshot, by design.

All of these no-save paths preserve whatever `runtime_state.json` and `saved_sessions.json` already contain; they do not clear those files. The problem is loss of changes newer than the last successful capture, not deletion of the older restore point.

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

**Status: fixed by Stage 2 (transaction) and Stage 3 (wiring)** (2026-08-08). The evidence and direction below describe the code the fix replaced; what shipped is in [Stage 3 results](#stage-3-results). Some line references no longer resolve.

Evidence:

- `SessionManager.snapshot_live_workspaces()` serializes only server objects (`sessions/manager.py:1078`).
- autosave calls that snapshot directly (`web/api.py:1944`).
- Save Workspace sends no group or pane presentation data (`web/static/js/terminals.js:2353`) and the route calls `capture_workspace()` directly (`web/api.py:1784`). *Amended:* it now sends exactly one presentation field, `topbar_visible`, read from the live DOM at click time. That is the correct shape for one field and does not generalize — every other browser-owned field in the map above is still absent.
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

**Status: fixed by Stage 3** (2026-08-08). Both writers named below are gone: browser tabs and top-bar visibility ride the ordered compare-and-swap queues, and neither the mode route nor `PATCH /api/workspaces/<id>/ui-state` can write presentation any more. See [Stage 3 results](#stage-3-results).

Evidence:

- `browserPersistTabs()` waits 400 ms for normal changes and starts `push()` without returning a promise to its caller (`web/static/js/browser-pane.js:168`).
- the local `pane._session` update occurs inside `push()`, immediately before an asynchronous fetch, but Save Workspace does not serialize that local object.
- the timer map tracks only pending timers, not an in-flight request. A newer request can be sent while an older one is still in flight.
- `update_browser_tab_strip()` has no client revision or compare-and-swap check (`sessions/manager.py:777`). Last arrival wins, even if it is older.
- the response handler assigns the server payload back over the local object (`web/static/js/browser-pane.js:215`), so a late older response regresses the browser's own state as well as the server's.

*Amended — there is now a second writer in this class.* `reportTopbarVisibility()` in `web/static/js/terminals.js` sends a fire-and-forget `PATCH /api/workspaces/<id>/ui-state` on every toggle. It is better than the browser-tab writer in two ways — it carries no debounce timer to be overtaken, and explicit Save Workspace bypasses it entirely by reading the DOM — and worse in one: it has no revision, no compare-and-swap, and no in-flight tracking, so two fast toggles can land out of order and leave the server holding the older value while the window shows the newer one. Nothing repairs that drift until the next toggle; the `reportedTopbarVisible` memo is reset to `null` on failure but never retried, and autosave will then commit the stale server value. The exposure is small because the field is one boolean the user toggles deliberately, but it is the same defect, and Stage 3 must not leave it behind as a third writer.

Impact:

- navigating, opening, closing, or switching a browser tab and immediately clicking Save Workspace can save the previous strip;
- a slow older request can overwrite a newer strip on the server and later autosave the regression;
- the same late response also overwrites `pane._session`, which is the object `browserSerializeTabs()` falls back to and the object Save Session serializes. The comment at `web/static/js/browser-pane.js:193` — that keeping the local session in step protects a following Save Workspace — is therefore defeated by the handler directly below it: out-of-order completion regresses client state, not only server state;
- the comment's claim is true for Save Session serialization only while no stale response lands, and is never true for the runtime save path.

Low-risk direction:

Move browser tab presentation onto the common ordered group-presentation queue from SGP-01. Keep the mode endpoint for actual mode transitions. If an intermediate migration keeps tab writes on the existing endpoint, serialize them with one in-flight/coalesced queue and make `browserPersistTabs(..., { immediate: true })` return an awaitable promise used by Save Workspace.

### SGP-03 — High: explorer snapshot coverage does not meet the requested snapshot semantics

**Status: fixed by Stage 5** (2026-08-09). Explorer records are now versioned, intent is independent of revision-bound state, every approved structural field is bounded and synchronized, and old flat records remain readable. See [Stage 5 results](#stage-5-results).

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

**Status: fixed by Stage 5** (2026-08-09). Launch preparation now preserves a captured root for both local and SSH explorers and retains the legacy fallback to the captured directory when an old slot has no root.

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

**Status: fixed by Stage 1** (2026-08-08). The evidence and direction below are kept as written, because they describe the code the fix replaced; what shipped is recorded in [Stage 1 results](#stage-1-results). The line references no longer resolve.

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
- encrypted passwords and all non-secret preset data share the same failure domain;
- the encryption-key race can stop the application from starting at all, not merely make stored passwords unreadable (see below).

Low-risk direction:

Introduce a small `SavedSessionStore` in `web/saved_sessions.py` (or a focused new `web/saved_session_store.py`) with:

- one in-process lock plus an OS-level sibling lock file;
- one locked read-modify-write callback per operation;
- unique same-directory temp files, flush/fsync where supported, and `os.replace`;
- a last-good `.bak` and corrupt-file quarantine behavior aligned with runtime state;
- a dedicated persistence exception mapped to retryable non-2xx API responses;
- no plaintext password in logs, exceptions, backup metadata, or diagnostics.

Also make first-run Fernet-key creation exclusive. `_get_encryption_key()` currently checks existence and then writes, so two first-start processes can generate different keys; the process that loses the file race may keep a cipher built from a key no longer on disk (`web/secrets.py:15`).

There is a second, more damaging variant of the same race. `_get_encryption_key()` is called at *module import* (`web/secrets.py:27`), and its read path is a bare exists-then-read with no atomicity (`web/secrets.py:17`). A process that reads while another is mid-write observes a zero-byte or partially written key, `Fernet(...)` raises, module import fails, and GridVibe does not start. The failure is therefore a startup crash, not only an unreadable password.

Use exclusive create plus a unique same-directory temp file and `os.replace`, so the key file is only ever observed complete; then let the loser read the winner's whole key. Cover both variants in tests: two first-run processes converge on one key, and no importer can observe a partial key file.

### SGP-06 — High: persistence normalization is coupled to the mutable launch cap

**Status: fixed by the Stage 4 prerequisite (items 1–3) and Stage 6 (items 6–7)** (2026-08-09). Stored preset width and both saved-preset/runtime-state geometry read paths use the immutable `MAX_STORED_SESSION_PANES` schema ceiling rather than `runtime_config.max_sessions`; valid high `originSlot` values are preserved and an out-of-schema origin rejects the geometry instead of being clamped onto another pane. Reads and unrelated preset writes are therefore nondestructive. The current cap is enforced only at the launch/split boundary, and it now answers with `capacity_refusal()` — one actionable sentence naming the required pane count and the setting — on launch, split, and per-group restore alike. See [Stage 6 results](#stage-6-results). The evidence below describes the code these changes replaced.

Evidence:

- `_normalize_terminal_entries()` iterates `range(runtime_config.max_sessions)` (`web/saved_sessions.py:514`).
- `_normalize_session_config()` clamps `terminal_count` to that same current value (`web/saved_sessions.py:625`).
- `_normalize_workspace_layout()` clamps origin slots and geometry bounds using the mutable setting (`web/saved_sessions.py:409`).
- every saved-session load normalizes through those functions; every subsequent save rewrites all entries from the normalized in-memory list.
- runtime-state validation intentionally keeps a larger stored group visible, but `launch_session_group()` refuses a group above the current cap (`web/workspaces.py:521`).

Example: save an eight-pane preset while `max_sessions=8`, lower the setting to four, then update or delete another preset. The eight-pane entry is loaded as four panes and the next store rewrite makes that truncation durable. Raising the setting later cannot recover the removed panes. A captured eight-pane workspace remains listed but its group restore fails.

The coupling is not confined to the saved-preset store. `_validate_group()` calls the same capacity-coupled `_normalize_workspace_layout()` on the runtime-state *read* path (`web/runtime_state.py:312`), so a lowered setting also reshapes workspace snapshots. The consequence there is worse than truncation because it is a silent value rewrite rather than a rejection: `originSlot` is clamped to `runtime_config.max_sessions - 1` (`web/saved_sessions.py:442`), so with the setting at four, every stored origin slot from four upward collapses to three. The grid-line bound survives only because `max_grid_line` has a floor of 64 (`web/saved_sessions.py:422`). A group still inside the current cap therefore restores with corrupted geometry instead of failing visibly, and the next autosave commits the corrupted values over the good ones.

Impact:

- a runtime preference can destroy stored data unrelated to the preference change;
- import/load is not a faithful read of the file;
- a lowered cap silently rewrites custom split geometry on restore and then makes the rewrite durable through autosave;
- README's exact-snapshot restore promise conflicts with current behavior.

Low-risk direction:

Separate constants and responsibilities:

- storage/schema cap: the absolute supported product maximum (currently 16 for normal panes);
- current launch cap: `runtime_config.max_sessions`;
- corruption cap: the runtime-state defensive ceiling.

Normalize stored presets against the storage cap, never the current preference. Enforce the current launch cap only at a launch/split boundary and return an actionable error without mutating the stored config. Fix both callers: the saved-preset path and the runtime-state read path (`web/runtime_state.py:312`) share one normalizer, so scoping the change to `web/saved_sessions.py` alone would leave snapshot geometry still coupled to the preference. Geometry that cannot be represented must be rejected as geometry, never clamped into different valid-looking values. The exact restore behavior when the current cap is lower is an open product question below.

### SGP-07 — Medium: runtime nested-field validation is shallower than its contract claims

**Status: fixed by Stage 6** (2026-08-09). `_validate_session()` and `SessionManager._session_launch_fields()` both route nested pane state through `normalize_pane_presentation_fields()`, which type-checks and rejects; a rejected pane fails its whole group at both boundaries, so all three rows of the table below now have the same visible outcome. See [Stage 6 results](#stage-6-results).

Evidence:

- `_validate_session()` copies the session allowlist without normalizing field types or nested explorer/browser structures (`web/runtime_state.py:259`).
- `_validate_group()` therefore counts a dictionary-shaped pane as valid even if `browser_active_tab`, `explorer_open_tabs`, or `explorer_tab_views` has an invalid type.
- `SessionManager.install_session_group()` catches per-pane staging exceptions and continues with the remaining panes (`sessions/manager.py:639`, `sessions/manager.py:675`). A malformed pane can disappear while the group still launches successfully.

Real snapshots produced by this build normally carry valid types, but backups, older versions, hand edits, partial external writes, or a future live-presentation endpoint can reach this path. The restore chooser's count can then disagree with the successful restore result—the exact class of disagreement the runtime-state validation gate is meant to prevent.

Malformed nested state has two distinct outcomes, and only one of them is the dropped pane above. `_session_launch_fields()` coerces defensively (`sessions/manager.py:620`), so the behavior depends on whether the coercion happens to raise:

| Bad value | Constructor | Outcome |
|---|---|---|
| `explorer_tab_views: "x"` | `dict("x")` raises | pane dropped, group launches smaller |
| `browser_active_tab: "abc"` | `int("abc")` raises | pane dropped, group launches smaller |
| `explorer_open_tabs: "abc"` | `list("abc")` succeeds | pane kept, tab list silently becomes `['a','b','c']` |

The third row is the more dangerous one: nothing raises, nothing is logged, and the corrupted value is installed as live state and re-captured by the next autosave. A fix that only converts dropped panes into group-level failures does not address it.

Impact:

- a corrupt group can restore partially rather than fail visibly;
- a corrupt group can also restore *complete* but with silently coerced garbage in a pane's presentation fields, which autosave then makes durable;
- invalid nested state can be passed farther into the client than intended;
- four normalization sites can drift.

Low-risk direction:

Extract presentation-field normalizers from `web/saved_sessions.py` into one import-cycle-safe module and use them for saved presets, runtime-state read validation, live presentation updates, and launch preparation. The canonical normalizer must type-*check* and reject, not coerce: a value of the wrong type is a validation failure, never an input to `list()`/`dict()`/`int()`. For restore, reject the whole group if any captured pane is not launchable after validation; report a per-group error and leave the snapshot available for retry/recovery.

### SGP-08 — Medium: Markdown/source appearance is modeled per pane but implemented page-globally

**Status: fixed by Stage 5** (2026-08-09). The workspace record is authoritative, uses the existing ordered workspace-presentation revision and runtime-slot path, and deterministically migrates the first legacy per-pane value. Per-pane fields remain read aliases; `localStorage` is only a startup cache.

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

Either resolution requires moving the authority off localStorage, which is why the product decision below cannot be implemented by keeping the current store. `localStorage` is scoped per origin, so every workspace window open in the same browser profile shares one value; a "workspace-global" setting held there would be silently global across *all* workspaces. The workspace record on the server must become the authority, with localStorage demoted to a cache or removed. Native mode does not change this: whether two workspace windows share a webview data directory is a platform detail the contract must not depend on.

### SGP-09 — Low: explorer theme localStorage accumulates dead session IDs

**Status: fixed by Stage 7** (2026-08-09). The explorer-theme override object now lives in `web/static/js/explorer-theme-store.js` and is re-bounded to live pane keys on every write and every grid build, emptying itself when no explorer pane remains; forgetting a workspace drops its `gridvibe.terminalTopbarVisibility.<workspace_id>` cache key along with the snapshot. Both halves are executed in Node by `tests/test_explorer_theme_store.py`. The historical evidence below is kept because it explains why the caches exist at all.

Explorer light/dark overrides are stored in a JSON object keyed by ephemeral `session_id` (`web/static/js/terminals.js:389`). New restart sessions receive new IDs, and close/teardown removes DOM attributes but not the saved object entry. Long-running use can grow the object indefinitely.

This is not the primary persistence defect, but the common presentation store makes it unnecessary. Once accepted theme state lives in the manager, prune the local override after acknowledgement or bound/migrate the legacy object. Do not remove it before server synchronization is reliable, because it currently protects live UI state from some rebuilds.

*Amended after Stage 3.* Both key families survive deliberately, and both are now *caches* rather than the only copy: the manager holds the acknowledged explorer theme (the pane sends it on every toggle) and the acknowledged top-bar visibility. Item 8 of Stage 3 forbids pruning them yet. At that point the launcher still read the top-bar key cross-window, and the explorer-theme object protected live UI state across a rebuild that has no session id to key on.

*Amended after Stage 4.* The launcher no longer reads the top-bar key. Its lifecycle request asks each owning workspace window to flush the ordered workspace queue and return its exact top-bar value with active-group/native-zoom metadata; the one all-live server capture consumes that validated result. The top-bar key is now only a same-window restoration cache. The unbounded explorer-theme object and deletion of legacy cache entries remain Stage 7 cleanup.

A second key family now exists: `gridvibe.terminalTopbarVisibility.<workspace_id>`, written by `storeWorkspaceTopbarVisible()` in `shared.js`. It is materially better than the theme object — one key per workspace rather than one entry per ephemeral session ID, so it does not grow without bound and it survives a restart meaningfully — but it is still a client-side authority for state the server now also holds, and it is read *cross-window*: the launcher page reads the key a workspace window wrote in order to include the field in that workspace's restart capture. That works only because both pages share one origin, which is precisely the coupling SGP-08 identifies as unsound for anything the workspace record should own. Once the server value is reliably acknowledged, the launcher must stop reading it and the key becomes a non-authoritative cache. Deleting a workspace should also drop its key; nothing does that today.

### SGP-10 — Medium: current tests prove field presence more often than a user-visible round trip

**Status: narrowed by Stage 5, further narrowed by Stage 6, closed by Stage 7** (2026-08-09). Explorer record behavior is now executed in Node and the agreed fixture crosses saved-session import/relaunch, manual workspace save/restore, autosave/restore, local panes, and SSH panes. Stage 6 added the malformed launch-shape coverage it owned — wrong-typed and silently coercible pane fields through both `POST /api/sessions` and the runtime-state read path, the type/bounds boundary, the chrome-degrades exception, and the actionable capacity refusal from both persistence products. Stage 7 removed the remaining legacy top-bar source-text assertions from `tests/test_api.py` without adding equivalents, closing the finding.

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

### SGP-11 — High: restart and application close have incomplete, asymmetric save semantics

**Status: fixed by Stage 4** (2026-08-08). The historical evidence below is kept because it explains the asymmetry the stage replaced. `saveWorkspaceForRestart()` and the client-side per-workspace loop are gone. Manual restart, update restart, explicit browser shutdown, and the native launcher X now enter the same lifecycle controller; teardown requires a one-use successful server decision, and any requested flush/preset/workspace failure keeps the process and shells running. See [Stage 4 results](#stage-4-results).

The lifecycle controls currently expose three materially different behaviors behind similar language:

- ~~**Save & Restart** manually captures only the default workspace because its request omits `workspace_id`.~~ **Fixed.** The launcher now lists `/api/workspaces`, filters to those with live groups, and captures each by its real ID with its own native window zoom. It still does not save reusable session presets — that half of the bullet stands, and it is a lifecycle *choice*, not a defect, once Stage 4 lands the modal.
- ~~The restart continues after that capture fails.~~ **Fixed.** A failure now returns early, re-enables the button as a retry affordance, and does not invoke the restart bridge.
- Update-triggered restart bypasses even that helper and calls `restart_application()` directly (`web/static/js/launcher.js:2459`).
- Browser shutdown and the native launcher `closed` event close live sessions and exit without capturing current state (`web/api.py:708`, `web/webview_launcher.py:1466`).

*Amendment.* The fix is correct behavior reached through a shape Stage 4 must replace rather than extend. The all-live capture is a **client-side loop in the launcher issuing one `POST /api/runtime-state/save` per workspace**, whereas Stage 4 item 2 requires one server-side lifecycle preparation service. Three consequences follow, and none of them is visible from the button:

- the loop is not a transaction — a failure on the third workspace leaves the first two committed and the user with a generic failure message, which is acceptable under product decision 12 only once the per-workspace result list that decision requires actually exists;
- it captures presentation only as well as the server already knows it, because the launcher cannot flush a queue that does not exist yet — the Stage 3 barrier and the Stage 4 item 3 process-wide flush handshake are still owed in full;
- it sources `topbar_visible` from `localStorage` rather than from the window that owns the value (see SGP-09 amendment), which is exactly the guess Stage 4 item 3 forbids: "do not guess that a debounce has completed."

The remaining asymmetry is therefore unchanged in kind and smaller in extent. Manual restart now saves all live workspaces and fails safe; update restart, explicit browser shutdown, and native launcher close still do neither.

The existing “no teardown snapshot” rule is valuable for involuntary shutdown: a late server-only capture can overwrite a better committed snapshot with stale browser-owned presentation. It should not, however, prevent a deliberate, user-selected save barrier before close or restart.

Impact:

- ~~in multi-workspace mode the restart button can truthfully say “Workspace saved” while sibling workspaces were not manually captured;~~ resolved;
- unsynchronized explorer/browser changes may be absent even from the snapshots the restart button now does capture;
- ~~requested save failures still lead to irreversible live-shell teardown;~~ resolved for the manual restart button only; update restart and both shutdown paths still tear down without a save or a decision;
- users cannot choose whether to update reusable sessions as well as workspace snapshots;
- close, manual restart, and update restart have different persistence effects — now more divergent than before, because only one of the three learned the all-live scope.

Low-risk direction:

Use one shared lifecycle-choice controller for explicit close, manual restart, and update restart. Offer **Without saving current changes**, **Save open workspaces**, and **Save open sessions + workspaces**, plus **Cancel**. “Open” must mean every live workspace/group in the process, not only the window invoking the action. The workspace-only choice flushes acknowledged client presentation and captures password-free runtime slots. The sessions-plus-workspaces choice first saves/updates reusable presets, then captures workspace slots that reference the resulting preset identities. Any requested-save failure keeps GridVibe open with the same three choices available, so the user can select the same save choice to try it again or explicitly continue without saving.

Do not make `beforeunload`, a late `closed` callback, or a blind teardown capture the correctness path. The native launcher X needs a cancellable pre-close event that opens the in-page choice UI; the explicit browser close button can use the same UI. Console interrupts, task-manager kills, and browser-tab closure cannot reliably complete an asynchronous save and should retain the documented last-good-snapshot behavior.

### SGP-12 — High: a stale lifecycle window registration permanently blocks every later save-on-exit

Found by the [Stages 0–4 verification review](#stages-04-verification-review-2026-08-08), in code Stage 4 shipped. This is the one defect that review turned up in production code.

Evidence:

- `LifecycleCoordinator._windows` entries are removed in exactly two places: `leave_workspace()`, and replacement by a `join_workspace()` carrying the *same* `window_id` (`web/lifecycle.py:97`, `web/lifecycle.py:90`).
- `disconnect_client()` deliberately does not remove the record. It pops the client from `_client_windows` and sets `connected = False`, leaving the window registered as stale (`web/lifecycle.py:116`).
- Because `_client_windows[client]` is gone, a later `leave_workspace()` for that dead socket cannot find the window either — the record is unreachable by every removal path except a same-`window_id` rejoin.
- The `window_id` is **not stable across page loads**. `lifecycleWindowId` is a module-scope `const` seeded from `crypto.randomUUID()` on every script evaluation (`web/static/js/terminals.js:530`), so a reloaded window registers a *new* id and never replaces the old record.
- `request_flush()` puts every stale record into `expected`, pre-acknowledges it, and appends a `client_stale` error, so the result is `ok: False` (`web/lifecycle.py:190`).
- `POST /api/lifecycle/prepare` turns any non-`ok` flush into a `503` with `retryable: true` and performs no save (`web/api.py:771`).
- Nothing reaps stale records: there is no TTL, no sweep, and no cap on `_windows`. Only `reset()` clears them, and `reset()` is for tests.

The normal paths are clean, which is why this survived: a browser reload or tab close fires `pagehide`, which emits `leave_workspace` while the socket is still connected (`web/static/js/terminals.js:7979`), and a plain Socket.IO reconnect re-joins with the same in-page id. The defect needs a socket to die *without* `pagehide` — a renderer or browser crash, a `kill -9`, a network partition or VPN flap, a laptop suspend long enough to drop the transport, or a mobile/background tab evicted without firing the event.

`tests/test_lifecycle.py::test_disconnected_window_stays_stale_until_its_stable_id_rejoins` encodes the current behavior as intended, and its name states the assumption the client does not satisfy: the id is per *page load*, not per window.

Impact:

- for the rest of the process's life, **Save open workspaces** and **Save open sessions + workspaces** fail with `503` for that workspace on manual restart, update restart, browser close, and native close; only **Continue without saving** still works;
- the user's escape hatch is to restart GridVibe — which is precisely the action being blocked from saving, so the recovery costs the unsaved changes;
- `_windows` grows without bound in a long-lived process, and each stale record adds one more permanent error to every lifecycle response;
- the failure text ("A workspace window is disconnected and cannot flush") names a window the user cannot see and cannot close, so it is not actionable.

Low-risk direction:

Make the stale record recoverable rather than permanent, without weakening the guarantee that a genuinely unreachable window is reported instead of ignored:

- give the workspace page a `window_id` that is stable per *window*, not per page load — `sessionStorage` is scoped to the tab/window and survives reload while staying distinct between tabs — so a reload replaces its own record even when `pagehide` did not fire;
- record a disconnect timestamp and treat a record stale for longer than a bounded grace period as departed: drop it and do not count it in `expected`. A window that has been gone for minutes is not a window whose flush is worth waiting for;
- bound `_windows` per workspace so an unbounded accumulation cannot occur even if both rules above are somehow bypassed;
- keep the short-lived case exactly as it is today — a socket that dropped seconds ago is still a real window and must still be reported as `client_stale`.

Add a test proving that a disconnected window is reported while it is fresh, is no longer reported once it is past the grace period, and that a reload with the same stable id replaces its own record even when no `leave_workspace` was sent.

### SGP-13 — Medium: workspace labels have no uniqueness rule, so a live workspace and a saved snapshot can share a name

Raised by `docs/r&d/todos.txt`, verified against the source.

Evidence:

- `normalize_workspace_label()` trims, collapses whitespace, and truncates. It applies no uniqueness rule of any kind (`web/workspaces.py:54`).
- `POST /api/workspaces` and `PATCH /api/workspaces/<id>` both pass a label straight through to `SessionManager.create_workspace()` / `rename_workspace()`, whose only collision check is on the workspace *id* (`web/api.py:1686`, `web/api.py:1728`, `sessions/manager.py:341`).
- Closing a live workspace without `?forget=true` deliberately leaves its saved slot on offer in the restore chooser (`web/api.py:1737`). That is the documented **Close live workspace** verb, and the launcher row's own tooltip says so ("its saved snapshot stays on offer"); **Close and forget** is the other verb, offered on the saved-workspace row.
- The restore chooser's `live_conflict` flag compares workspace *ids*, so two entries whose labels match but whose ids differ are not a conflict and both render.

The retained saved slot is therefore correct and intended — that half of the `todos.txt` note is the feature working. The real gap is the one immediately after it: because nothing owns the label namespace, the user can close a workspace called "api work", launch a new one with the same name, and end up with a live "api work" beside a saved "api work" that restores into a *third* workspace. The launcher's own display falls back to a positional label when a workspace is unlabelled, so duplicates are not even distinguishable by their metadata.

Impact:

- the restore chooser and the Workspaces card can show two or three rows the user cannot tell apart;
- **Launch into a new workspace** silently accepts a name that already identifies stored state, so the user believes they are reusing a workspace when they are creating a rival to it;
- a user who meant "reopen my old workspace" can instead accumulate duplicates and then guess which snapshot to forget;
- naming is the only handle the user has on a workspace — the id is opaque — so an unowned namespace defeats the identity model rather than merely being untidy.

Low-risk direction:

Make the label namespace explicit and enforce it in one place, over both live workspaces *and* saved slots:

- add one normalizer/validator in `web/workspaces.py` that resolves a requested label against both sets, case- and whitespace-insensitively;
- reject a duplicate at `POST /api/workspaces` and `PATCH /api/workspaces/<id>` with a `409` whose body names the conflicting kind (live or saved) so the client can say *"'api work' is a saved workspace — reopen it, forget it, or pick another name"* and offer the matching action inline;
- do not auto-rename and do not auto-forget: both discard a decision that is the user's. The audit's own rule — an actionable error that mutates nothing — applies here exactly as it does to the capacity error in SGP-06.
- leave an *empty* label alone. It is the "unnamed workspace" case, positional labels already disambiguate it, and forcing uniqueness on it would make creating two scratch workspaces impossible.

### SGP-14 — Low: the launcher's live-workspace rows cannot save one workspace

Raised by `docs/r&d/todos.txt`.

Evidence:

- each row in the launcher's Workspaces card renders exactly two actions, **Open** and **Close** (`web/static/js/launcher.js:2916`, `web/static/js/launcher.js:2931`).
- explicit save exists in two shapes only: **Save Workspace** inside a workspace window, which saves *that* window; and the Stage 4 lifecycle choices, which save *every* live workspace and then exit.
- there is no way to durably capture one named workspace from the surface that lists them all, and the launcher is the only page that lists them all.

This is a genuine gap in the verb set rather than a defect: after Stage 4 the launcher can already reach every workspace window's flush barrier, so the machinery a per-row save needs is built and unused for this purpose.

Impact:

- the only "save this one workspace, keep working" action requires switching to that workspace's window and finding its button;
- the Workspaces card otherwise offers the complete workspace verb set (open, close, close-and-forget, restore, forget), so its omission reads as an oversight;
- users reach for **Save & Restart** — a whole-process, teardown-coupled action — to get a durable snapshot of one workspace.

Low-risk direction:

Add a per-row **Save** button that reuses the Stage 4 flush handshake scoped to one workspace, rather than adding a third capture path. It must flush the owning window before capturing (or refuse), so the button means the same thing as in-window **Save Workspace**; it must not touch reusable presets; and it must never terminate anything. A workspace with no reachable window is the interesting case — report it rather than silently capturing the last acknowledged server state, for the same reason Stage 4 item 3 forbids guessing that a debounce completed.

## Risk-ranked implementation proposal

Each stage is independently reviewable and preserves backward compatibility. New optional fields should be ignored by old readers; new readers must accept old files.

The stages are otherwise in dependency order, with one exception recorded in Stage 4: its **Save open sessions + workspaces** action depends on the capacity decoupling in Stage 6 items 1–3, which must therefore be scheduled earlier. Every other stage may ship in the order written.

### Stage 0 — Freeze the snapshot contract with failing behavioral tests — **done**

Outcome recorded in [Stage 0 results](#stage-0-results).

Connected findings: SGP-01, SGP-02, SGP-03, SGP-04, SGP-06, SGP-07, SGP-08, SGP-10, SGP-11.

Add tests before production changes. Write every one of them as an assertion of the *target* behavior, marked `unittest.expectedFailure` until its stage lands, and remove the decorator as part of that stage. Do not write tests that assert the current defect: a green suite encoding the bug has to be inverted later, which destroys its value as a stable contract and misleads anyone bisecting through these stages.

1. Build a live explorer pane whose manager state is old and whose client snapshot is new; assert Save Workspace captures the client snapshot.
2. Model browser persistence A then B with A completing last; assert neither server nor client state regresses to A.
3. Save/restore a group after pane reorder and split-track resize.
4. Save/restore an explorer rooted at a parent while viewing a child directory.
5. Define an explorer fixture with Preview plus pinned tabs, active Diff, per-panel scrolls, zoom, wrap, folds, theme/fonts, sidebars, width, and tree expansion.
6. Lower `max_sessions` after saving a larger preset and prove load plus an unrelated write does not alter its stored shape.
7. Feed invalid nested presentation data through runtime validation and prove no partial group is advertised as exact.
8. Freeze the close/restart action matrix: all-live-workspace scope, save ordering, failure behavior, and preservation of the previous committed snapshot when no new save is requested.

Use backend behavioral tests for storage/manager transactions. Put pure browser snapshot normalization and queue logic in a small standalone JS module so Node-based tests can execute it without a DOM. Avoid adding new raw source-text assertions.

Exit gate: the desired field matrix and the deliberate exclusions are executable contracts, and the open questions below have answers for any field included in Stage 5.

#### Stage 0 results

Completed 2026-08-07. Everything landed in one new file, `tests/test_session_persistence_contract.py` (27 tests). No production module was touched, so Stage 0 carries no rollback surface and no behavior change.

**Gate results (re-run for this change):**

- `python -m ruff check .` — passed, "All checks passed!".
- `python tests/run_tests.py` — 1,231 tests ran; `OK (skipped=7, expected failures=26)`. The suite is green, and the 26 expected failures are the frozen contract. The two voice-environment failures noted in the earlier revision of this document no longer occur in this environment.

**Coverage — all eight Stage 0 items are executable:**

| Stage 0 item | Test case | Tests | Currently failing on |
|---|---|---|---|
| 1. Save Workspace captures the client snapshot | `SaveWorkspaceFlushBarrierTestCase` | 4 | no presentation route (`404`) |
| 2. Out-of-order browser writes | `BrowserPresentationOrderingTestCase` | 2 | no presentation route; no `session-persistence.js` |
| 3. Pane reorder + split-track resize | `PaneOrderAndSplitGeometryTestCase` | 1 | no presentation route |
| 4. Explorer rooted at a parent | `ExplorerRootRestoreTestCase` | 2 (1 passing) | root narrowed to the child directory |
| 5. Full explorer presentation fixture | `ExplorerPresentationFixtureTestCase` | 5 | no `web/session_presentation.py`; no presentation route; no workspace appearance route |
| 6. Lowering `max_sessions` is non-destructive | `LaunchCapacityNondestructiveTestCase` | 3 | preset truncated `8 → 4`; `originSlot` clamped `7 → 3`; capacity error not actionable |
| 7. Malformed nested presentation state | `MalformedPresentationValidationTestCase` | 3 | corrupt group offered as restorable; chooser counts `2` groups where a restore keeps `1` |
| 8. Close/restart action matrix | `LifecycleActionMatrixTestCase` | 7 | no lifecycle route; browser shutdown tears down without a lifecycle decision |

Every failing test was verified to fail on its *intended* assertion, not on incidental setup. Three were rewritten during Stage 0 after they failed for the wrong reason: `load_saved_sessions()` returns a list rather than a payload dict; two lifecycle tests indexed a `None` JSON body from the 404; and the browser-shutdown test was reaching the "shutdown unavailable" `404` because the test process has no shutdown token, which said nothing about the contract.

**Names frozen by Stage 0.** The tests necessarily commit to the surfaces later stages must build. They are declared once as module constants at the top of the test file, so a stage may rename one — but only by editing that constant in the same commit, never by adding a second parallel surface:

| Constant | Value | Owed by |
|---|---|---|
| `PRESENTATION_ROUTE` | `POST /api/session-presentation` | Stage 2 |
| `PRESENTATION_MODULE` | `web/session_presentation.py` | Stage 2 (normalizers), Stage 6 (reuse) |
| `PRESENTATION_JS` | `web/static/js/session-persistence.js` | Stage 2 |
| `LIFECYCLE_ROUTE` | `POST /api/lifecycle/prepare` | Stage 4 |
| `LIFECYCLE_SAVE_*` | `none`, `workspaces`, `sessions+workspaces` | Stage 4 |
| `EXPLORER_PRESENTATION_FIXTURE` | the item 5 fixture | Stage 5 |

The presentation request body is the one printed in Stage 2. Three response fields are also now fixed by tests: the route returns `presentation_revision`, answers a stale writer with `409` carrying the current revision, and the lifecycle route returns `action`, `save`, `saved_workspaces`, `saved_sessions`, `ready_to_exit`, `retryable`, and `errors`.

The item 5 fixture is the concrete answer to product decision 1. It carries Preview plus two pinned tabs, an active staged Diff, per-panel horizontal *and* vertical scroll ratios, a directory-list scroll, per-tab zoom, per-view wrap opt-outs, Markdown folds, theme/fonts, all three sidebar open flags, sidebar width, and Files-tree expansion — and deliberately carries no dirty buffer, Git commit draft, or search query/result, per product decisions 2-4.

**Three deviations from the stage text, and why:**

1. **One test ships without `expectedFailure`.** `test_a_missing_root_still_falls_back_to_the_captured_directory` already passes: when no root was captured, falling back to `directory` is correct today and must stay correct after Stage 5. It pins the half of SGP-04 that is *not* a defect, so decorating it would have been the "test that asserts the current behavior and must be inverted later" the stage forbids. It is the only undecorated test in the file.
2. **Item 8's involuntary-termination row is not executable and was reframed.** Ctrl+C, a task-manager kill, and a disappearing browser tab have no request surface to assert against, and the "writes nothing" half is already covered by the `save: "none"` test. The slot was spent instead on the genuinely missing contract next to it: the explicit browser close button is a *voluntary* exit, so `POST /api/browser-shutdown` must refuse to tear down before a lifecycle decision. Stage 4 item 8 therefore remains a design constraint reviewers must uphold by reading, not a test.
3. **The client-half queue test skips without Node.** `test_the_client_queue_keeps_one_write_in_flight_and_ignores_late_replies` runs the queue for real in Node (existing precedent in `tests/test_multi_workspace.py`) and skips when `node` is absent, so it cannot be the only guard for SGP-02. Its server half is a plain backend test that always runs.

**Note for whoever ships Stage 2 and Stage 4.** `expectedFailure` is not inert: an unexpected success fails the run. A decorator left behind after its stage lands will break `make check` rather than pass quietly, which is the intended forcing function — removing it is part of the stage, not a follow-up.

### Out-of-band change: workspace top-bar visibility

A change landed after Stage 0 that persists and restores whether a workspace window's top bar is shown, and that corrects the launcher's **Save & Restart** scope along the way. It was not part of this plan's sequence. This section records what it means for the stages that have not shipped.

**What it added.**

| Surface | Change |
|---|---|
| `sessions/manager.py` | `Workspace.topbar_visible`, `set_topbar_visible()` / `get_topbar_visible()` under the manager lock, and the field in `snapshot_live_workspaces()`. |
| `web/api.py` | `PATCH /api/workspaces/<id>/ui-state`; `topbar_visible` on `GET /api/session-groups`, `GET /api/runtime-state`, and `POST /api/runtime-state/save`. |
| `web/runtime_state.py` | `normalize_topbar_visible()`, slot validation, slot assembly, and the capture parameter. |
| `web/workspaces.py` | Restore reapplies the stored value to the live workspace and reports it. |
| `web/static/js/shared.js` | Per-workspace `localStorage` key helpers. |
| `web/static/js/terminals.js` | `reportTopbarVisibility()` on toggle; `topbar_visible` read from the DOM in `saveWorkspace()`; the value applied from the `/api/session-groups` payload. |
| `web/static/js/launcher.js` | `saveWorkspaceForRestart()` rewritten to enumerate and capture every live workspace, and to abort the restart on failure. |

**What it got right, and should be kept as precedent.**

1. `normalize_topbar_visible()` type-*checks* rather than coerces — `isinstance(value, bool)` or `None`. That is the rule SGP-07 and Stage 6 item 4 ask for, applied before either was written. `0` and `"hidden"` are rejected with `400` at both routes rather than becoming `False`.
2. Explicit Save Workspace reads the field from the live DOM at click time instead of trusting the last acknowledged server value. That is the SGP-01 flush-barrier principle, correct for one field.
3. The workspace record — not `localStorage` — is where the durable value lives. Product decision 5 wants exactly this for Markdown/source appearance, and this change proves the path end to end: manager field → `snapshot_live_workspaces()` → slot → restore. Stage 5 item 8 now has a worked example to copy rather than a design to invent.
4. It stayed inside the established durability machinery. No new state file, no new writer, no lock held across I/O, and the manual-retention and ordered-revision rules are untouched.

**What it owes the remaining stages.**

1. ~~**A transition writer remains for Stage 3 to delete.**~~ **Done.** Stage 3 routed the toggle through `POST /api/workspace-presentation` and deleted `PATCH /api/workspaces/<id>/ui-state`, its route function, and every test that used it. There is one chrome writer.
2. **The workspace-scoped design decision is resolved.** Group state stays on `POST /api/session-presentation`; window chrome uses the separate `POST /api/workspace-presentation` transaction and its own workspace revision. `topbar_visible` is not attached to an arbitrary group, so a two-group workspace still has one chrome authority.
3. **`normalize_topbar_visible()` moved to the canonical module.** `web/session_presentation.py` now owns it, and `web/runtime_state.py` imports the definition. The existing non-coercing behavior and invalid-slot fallback remain unchanged.
4. **Its slot validation defaults rather than rejects, deliberately — record why.** An invalid stored `topbar_visible` degrades to `True` instead of failing the slot, unlike the pane-level rule Stage 6 item 5 sets. That asymmetry is defensible: window chrome is not launchable shape, a wrong value costs one keystroke to correct, and failing a whole workspace restore over it would be worse than the defect. Stage 6 should keep the behavior and state the boundary explicitly — *shape fails, chrome degrades* — rather than let a future reader read it as an inconsistency to "fix."
5. **The launcher reads another window's `localStorage`.** Covered under SGP-09 and SGP-11 above. Stage 4 item 3's flush handshake replaces it; until then it is the only way the launcher can see the value, so do not remove it early.
6. **New source-text assertions were added.** `tests/test_api.py` gained assertions on JavaScript substrings (`"function workspaceTopbarVisibilityStorageKey(workspaceId)"`, `"/ui-state\`, {"`, `"typeof data.topbar_visible === 'boolean'"`, and four in the launcher block). `CLAUDE.md` forbids adding these and SGP-10 lists them as the coverage to *replace*. The backend tests added alongside them are genuinely behavioral and cover the round trip well; the string assertions add nothing those do not, and they will break on the Stage 3 refactor that deletes the route they name. Stage 7 item 4 should remove them as part of retiring the writer. *Stage 3 removed the `"/ui-state\`, {"` one, because it named a route that no longer exists; the other five name surfaces that are still live and remain Stage 7's.*

**What it did not affect.** Stage 0's frozen contract is intact: `tests/test_session_persistence_contract.py` is untouched and still reports 26 expected failures and one pass, so no stage's forcing function was consumed. Stage 1 is unaffected in full — nothing here reaches `saved_sessions.json` or `web/secrets.py`. The full suite is green (1,237 tests, `OK (skipped=7, expected failures=26)`).

### Stage 1 — Make saved-session and encryption-key persistence durable

Connected findings: SGP-05, SGP-10, SGP-11.

1. Wrap upsert, delete, and last-session changes in one locked read-modify-write transaction.
2. Use a unique sibling temp file and atomic replace; retain and recover a last-good backup.
3. Quarantine invalid/unsupported payloads instead of returning an empty store that a later save overwrites.
4. Surface write/delete failures as retryable API errors; never return a false success.
5. Make Fernet-key first creation exclusive and durable.
6. Add paused-writer thread/process tests, replace-failure tests, corrupt-primary/valid-backup tests, and secret-redaction assertions.

Keep the public JSON shape and encryption format unchanged. This stage is isolated from frontend behavior and has a small rollback surface. The out-of-band top-bar change does not touch anything in this stage; ship it as written.

Exit gate: concurrent unrelated saved-session mutations survive, interrupted writes retain a readable last-good store, and no plaintext secret reaches logs or runtime state.

#### Stage 1 results

Completed 2026-08-08. All six items landed, plus one refactor the stage text did not call for and the DRY guardrail did.

**What shipped.**

| Surface | Change |
|---|---|
| `web/state_files.py` (new) | The durability mechanics both stores need, in one place: `CrossProcessFileLock`, `write_json_atomically()`, `back_up_state_file()`, `quarantine_state_file()`, `read_backup_json()`, and `create_file_exclusively()`. |
| `web/saved_session_store.py` (new) | `SavedSessionStore` — path resolver, process lock, cross-process lock, and one `transaction(mutate)` entry point. Deliberately schema-free; it moves whole JSON payloads, nothing else. `SavedSessionsPersistenceError` lives here. |
| `web/saved_sessions.py` | `upsert_saved_session()`, `delete_saved_sessions()`, and `set_last_saved_session()` are each now one transaction instead of a load/save pair. Normalization split into the pure `_normalize_stored_payload()` / `_build_saved_sessions_commit()` inverse pair so the mutator does no I/O. |
| `web/runtime_state.py` | Rewired onto the shared primitives. `_CrossProcessStateLock`, `_quarantine_state_file`, `_write_state_locked` and `_recover_from_backup` keep their names and behavior as thin wrappers; `_back_up_current_state` is gone (absorbed by the shared writer). |
| `web/secrets.py` | Exclusive, atomic first-run key creation plus a bounded retry against a legacy non-atomic writer, and a loud `RuntimeError` instead of an opaque `Fernet` failure on an empty key file. |
| `web/api.py` | `POST /api/saved-sessions`, `DELETE /api/saved-sessions`, and `POST /api/session-config` answer `503` with `retryable: true` on a failed commit, through one `_saved_sessions_write_failure()` helper. |
| `.gitignore` | The new sidecars (`saved_sessions.json.{lock,bak,corrupt-*}`) and both stores' scratch files. |

**Item-by-item.**

| Stage 1 item | Where | Note |
|---|---|---|
| 1. One locked read-modify-write per mutation | `SavedSessionStore.transaction` | `_exclusive()` is re-entrant per thread, because the file lock takes a fresh descriptor and would otherwise deadlock against itself. |
| 2. Unique temp file, atomic replace, last-good backup | `write_json_atomically` | Shared with runtime state, so the two files cannot drift apart on this. |
| 3. Quarantine instead of an empty store | `SavedSessionStore._read_locked` | "Unsupported" is `_saved_payload_is_supported()`: a dict (current) or a bare list (pre-`last_session`). Anything else is not a preset store. |
| 4. Surfaced failures | `_saved_sessions_write_failure` | Matches the shape the runtime-state save route already returns. A failed *delete* raises too, which is the "false success" bullet in SGP-05. |
| 5. Exclusive, durable Fernet key | `create_file_exclusively` | `os.rename` on Windows, `os.link` elsewhere — both fail rather than clobber. Content is complete and fsynced before the name exists. |
| 6. Tests | `tests/test_saved_session_store.py` | 26 tests: concurrent upserts, concurrent delete-vs-upsert, a paused writer blocking the next transaction, cross-process lock exclusivity, failed replace / failed temp write / failed delete, unique temp paths, corrupt-primary and unsupported-payload recovery, quarantine surviving the next save, plaintext absence from file/backup/quarantine/exception/logs, the three retryable routes, key convergence across threads, the never-partial key claim, and the frozen file shape plus legacy bare-list load. |

**One deviation from the stage text.** The stage said to add the store and left the file primitives implicit. Copying ~120 lines of lock/atomic-write/backup/quarantine code out of `web/runtime_state.py` would have violated guardrail 6 and given the two files two chances to drift, so the primitives were extracted to `web/state_files.py` first and `runtime_state.py` was rewired onto them. Its module-level names and their behavior are unchanged, which is why the existing runtime-state tests (including the ones that patch `_write_state_locked` and instantiate `_CrossProcessStateLock`) all still pass untouched.

**Gate results:**

- `python -m ruff check .` — passed, "All checks passed!".
- `python tests/run_tests.py` — 1,263 tests, `OK (skipped=7, expected failures=26)`. The 26 expected failures are unchanged: Stage 1 touches nothing Stage 0 froze, so no forcing function was consumed.

**What this does *not* do.** The capacity coupling in SGP-06 still runs through `upsert_saved_session()`, so the Stage 4 hard prerequisite is unaffected — a durable store makes a truncating write *reliably* durable, which is the wrong direction until Stage 6 items 1–3 land.

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

Four rules the schema above does not settle, each of which must be decided before this stage starts:

**Workspace-scoped presentation.** The payload above has no home for state that belongs to a workspace window rather than to a group, and one such field already exists in production: `topbar_visible`. Decide now whether the transaction gains an optional `workspace` object or whether window chrome keeps a separate small route under the same revision discipline. Either is acceptable; attaching a workspace-wide value to one group's payload is not, because a two-group workspace would then have two writers for one value. Whichever is chosen, `PATCH /api/workspaces/<id>/ui-state` is superseded and Stage 3 deletes it. Move `normalize_topbar_visible()` out of `web/runtime_state.py` into this stage's `web/session_presentation.py` at the same time — it is already the non-coercing shape the canonical normalizer requires.

**Stale-writer recovery.** Rejecting a stale update with `409` protects the server, but it does not say what the losing window does with the presentation the user actually arranged there. Discarding it silently trades a lost-update bug for a lost-work bug. The rule for this stage is: on `409` the client refetches the current group revision, reapplies its own structural intent (pane order, open tabs, active tab, view mode, sidebar state) on top of it, and surfaces the reconciliation rather than dropping it. Product decision 7 establishes authority; it does not by itself close this. If reconciliation proves too costly for a first implementation, the acceptable fallback is narrower rather than lossier: let the revision guard ordering *within* one client and accept last-writer-wins per field between clients, which is no worse than today's behavior and still removes the out-of-order regression.

**Write amplification.** Presentation updates must never themselves trigger a runtime-state capture. Autosave keeps its existing timer and simply reads the most recently acknowledged manager state. Coalescing is tiered: structural changes (tab open/close/reorder, pane reorder, mode switch, layout settle) enqueue immediately; continuous changes (scroll, zoom drag) coalesce on a floor of at least one second. Guardrail 3 forbids sub-second polling, and an uncoalesced scroll-driven queue would approach exactly that.

**No orphan events.** Guardrail 5 requires every server event to have a client listener. Name the consumer of the presentation notification in the same change that introduces the emit, or do not emit at all — a revision broadcast with no subscriber is dead code on arrival.

Exit gate: the manager is the canonical acknowledged presentation source, update order is deterministic, autosave sees either a complete old group presentation or a complete new one, scroll activity alone produces no disk write, and every emitted event has a named consumer.

#### Stage 2 results

Completed 2026-08-08. The backend transaction, manager authority, schema extraction, workspace-chrome decision, and client ordering primitive all landed. Stage 3 still owns DOM capture and replacement of the legacy writers; completing Stage 2 does not claim that current browser/explorer events are wired yet.

**Correctness validation and stage-boundary amendments.**

1. The proposed compare-and-swap batch is correct only if pane order and group geometry are applied in Stage 2. Deferring those manager fields to Stage 3 would leave the manager unable to be the canonical acknowledged snapshot source. Consequently, the frozen pane-order/geometry test now passes in Stage 2 when it manually posts a transaction; Stage 3 still owns capturing that state from the DOM.
2. The strict live boundary belongs here, while strict runtime-file rejection remains Stage 6. `normalize_pane_presentation()` now rejects unknown and wrong-typed live fields before the manager lock. Saved presets import the same field normalizers. `runtime_state.py` read validation and launch preparation are deliberately not switched to whole-group rejection yet, so the two Stage 6 malformed-restore forcing tests remain expected failures.
3. Workspace chrome uses a separate transaction and revision. This avoids attaching `topbar_visible` to an arbitrary group and gives a multi-group workspace one chrome writer. The legacy `/ui-state` bridge increments the same revision until Stage 3 removes it.
4. No Socket.IO notification was introduced. There is therefore no orphan server event and no emit under `SessionManager.lock`; clients can refetch `GET /api/sessions` on a stale revision. Stage 3 may add a room-scoped notification only with its listener in the same change.

**What shipped.**

| Surface | Change |
|---|---|
| `web/session_presentation.py` (new) | Import-cycle-safe owner of browser/explorer presentation bounds and normalizers; strict pane/group/workspace payload validation; thin service functions translating manager outcomes to HTTP status. `normalize_topbar_visible()` moved here. |
| `sessions/manager.py` | `SessionGroup.pane_order` and `presentation_revision`; `Workspace.presentation_revision`; atomic group/workspace compare-and-swap methods; ordered session enumeration/snapshotting; deep-copy application; browser active URL mirrored into `initial_command`. |
| `web/api.py` | Thin `POST /api/session-presentation` and `POST /api/workspace-presentation` routes. `GET /api/session-groups` exposes the workspace revision; the transitional `/ui-state` response exposes and participates in that revision. |
| `web/saved_sessions.py` | Imports and compatibility-re-exports the canonical presentation field definitions instead of owning duplicate browser/explorer/geometry normalizers. The saved JSON contract is unchanged. |
| `web/runtime_state.py` | Imports `normalize_topbar_visible()` from the canonical module; runtime-state schema and persistence behavior are unchanged. |
| `web/static/js/session-persistence.js` (new) | DOM-free per-group queue with one in-flight request, one coalesced latest snapshot, structural microtask batching, a one-second continuous-update floor, explicit flush, revision rebasing, current-state refetch/reconciliation hooks, and no late-response overwrite of local state. |
| `tests/test_session_presentation.py` (new) | Six behavioral tests covering atomic rejection, deep-copy isolation, ordered snapshot output, derived browser URL state, no runtime-state write, separately revisioned workspace chrome, continuous coalescing, stale refetch/rebase, and retry ordering. |
| `tests/test_session_persistence_contract.py` | Removed eight `expectedFailure` decorators now satisfied: four route/CAS cases, two server/client browser-ordering cases, pane order/geometry, and strict live normalization. Future-stage decorators remain. |

**Transaction behavior.** The group route requires exact workspace/group identities, a non-negative expected revision, a duplicate-free pane order, and exactly one pane entry for every live session. It rejects launch, credential, process/status, unknown, cross-group, cross-workspace, missing, or mode-incompatible fields. All values are normalized before the lock. Revision, ownership, membership, pane order, optional layout/geometry, and every pane update are checked/applied in one lock hold; nested values are deep-copied; stale requests return `409` with the current revision. The route performs no file write and no emit, so autosave keeps its existing cadence and observes either the complete old presentation or the complete new one.

**Gate results:**

- `python -m ruff check .` — passed, "All checks passed!".
- `python tests/run_tests.py` — 1,269 tests, `OK (skipped=7, expected failures=18)`. The expected-failure count fell from 26 to 18 exactly because the eight Stage 2 forcing functions above were consumed.

**What remains for Stage 3.** Load `session-persistence.js` in the terminals page; capture full live group presentation; route browser tabs, explorer state, pane reorder/layout settle, and top-bar toggles through the queues; surface stale reconciliation; make explicit Save Workspace flush every group and workspace queue; then remove the browser-tab presentation writer and `PATCH /api/workspaces/<id>/ui-state`. *All of that shipped — see [Stage 3 results](#stage-3-results).*

### Stage 3 — Wire exact Save Workspace and existing browser/explorer state into the canonical source — **done**

Connected findings: SGP-01, SGP-02, SGP-09, SGP-10, SGP-11.

1. Route existing explorer tab/view/sidebar/theme changes through the Stage 2 queue.
2. Route browser tab changes through the same ordered queue; stop using fire-and-forget tab updates on the mode endpoint. Before removing that writer, enumerate every remaining caller of `update_browser_tab_strip()` — real mode switches and restore also reach it — and confirm which of them still need to deliver a tab strip through the mode route. Removing the last caller of a still-needed path, or leaving a second writer behind, are both failure modes here.
3. Capture pane order and custom layout/weights whenever drag/resize settles.
4. Capture visible group state before caching/detaching it, as today, then enqueue that snapshot.
5. On **Save Workspace**, synchronously capture all visible/cached groups, await queue flush/ack, and only then call `/api/runtime-state/save`.
6. If the flush fails, show the existing retryable failure affordance and do not claim the workspace was saved.
7. Leave autosave non-blocking. It snapshots the most recently acknowledged manager state; normal debounce bounds the lag, while explicit save remains exact.
8. Migrate/prune legacy explorer-theme localStorage entries only after acknowledgement is reliable. The same applies to the newer `gridvibe.terminalTopbarVisibility.<workspace_id>` keys, with one extra constraint: the launcher currently *reads* them cross-window, so they cannot be demoted to a cache until item 9's barrier gives the launcher a better source.
9. Expose the exact flush barrier as a reusable lifecycle operation; close/restart must not duplicate mode-specific capture logic.
10. Route workspace top-bar visibility through the same queue and remove `PATCH /api/workspaces/<id>/ui-state` in the same change. Keep the DOM-read on explicit Save Workspace — it is already the correct barrier behavior for that field, and it must keep working while the on-change writer moves. Confirm no other caller of the `/ui-state` route exists before deleting it; the same enumerate-then-remove discipline as item 2.

Do not use `beforeunload` as the correctness mechanism; browsers may cancel asynchronous work. State-change events plus the explicit save barrier are the reliable paths.

Exit gate: change tabs/view/layout, immediately click Save Workspace, restart, and restore the exact acknowledged state. A forced failed sync produces no success toast.

#### Stage 3 results

Completed 2026-08-08. All ten items landed. Both superseded writers were removed in the same change rather than deferred, so no field has two paths to the manager.

**What shipped.**

| Surface | Change |
|---|---|
| `web/static/js/session-persistence.js` | Grew the DOM-free half Stage 3 needs: `buildGroupPresentationPayload()` / `buildWorkspacePresentationPayload()` (descriptor → wire body) and `createPresentationController()` (one queue per group plus one for window chrome, re-capture on enqueue, revision rebasing, a bounded repair after a failed write, and the `flush()` barrier). The queue gained `setRevision()`, `forget()`, and an `onAccepted` hook. Still `require()`-able from Node with no DOM. |
| `web/static/js/terminals.js` | The adapter only: `describePanePresentation()` / `describeGroupPresentation()` read a group from the live DOM, `customSplitLayoutSnapshot()` decides when geometry travels, `explorerPaneLiveTheme()` is now shared with Save Session, and `flushLivePresentation()` is the barrier. `reportTopbarVisibility()` posts through the workspace queue. Change hooks at `swapTerminalCards()`, `finishGridResize()`, `splitTerminalPane()`, `replaceSessionPaneMode()`, `toggleExplorerTheme()`, and `cacheVisibleGroupView()`. `loadSessionGroups()` rebases every queue on the revisions it just read. |
| `web/static/js/explorer-viewer.js` | Three one-line hooks at the existing funnels: `persistExplorerTabsToSession()` (tabs, view mode, wrap, folds, zoom), `setExplorerSidebarPanelOpen()`, and `setExplorerMarkdownAppearance()`. |
| `web/static/js/browser-pane.js` | `browserPersistTabs()` no longer owns a debounce timer, a fetch, or a response handler — it updates the local session object and enqueues. `browserCancelPendingPersist()` and its three call sites are gone. |
| `templates/terminals.html` | Loads `js/session-persistence.js` first. |
| `web/api.py` | `PATCH /api/workspaces/<id>/ui-state` deleted. The mode route's `tabs` branch deleted; it does mode transitions and single-URL navigation only. |
| `sessions/manager.py` | `update_browser_tab_strip()` deleted (its only caller was that branch). |

**Item-by-item.**

| Stage 3 item | Where | Note |
|---|---|---|
| 1. Explorer tab/view/sidebar/theme on the queue | the three explorer funnels + `toggleExplorerTheme()` | Scroll is deliberately *not* an event: it is folded into the batch by `explorerCaptureActiveTabView()` at capture time, so an explicit save is exact and a drag writes nothing. |
| 2. Browser tabs on the queue, old writer removed | `browserPersistTabs()` | Enumeration first, as the stage demands — see the table below. |
| 3. Pane order and geometry on settle | `swapTerminalCards()`, `finishGridResize()`, `splitTerminalPane()` | Settle, not drag: the pointer stream itself enqueues nothing. |
| 4. Capture before caching a group | `cacheVisibleGroupView()` | Enqueued right after `captureCachedPaneUiState()`, while the cards are still in the document — a detached pane can still be serialized but its live view and theme can only be read from the DOM. |
| 5. Save Workspace flushes first | `saveWorkspace()` → `flushLivePresentation()` | The barrier re-captures every visible/cached group *and* window chrome, then awaits the acknowledgement, before `/api/runtime-state/save`. |
| 6. A failed flush is not a save | `saveWorkspace()` | The existing retryable failure message; no success toast and no capture. |
| 7. Autosave untouched | — | No timer, cadence, or route changed. Presentation writes never trigger a capture (already covered by a Stage 2 test). |
| 8. Legacy `localStorage` retained | — | Deliberately unchanged; see the SGP-09 amendment. Both key families are now caches, but the launcher still reads the top-bar key cross-window and cannot stop until Stage 4 item 3. |
| 9. Reusable flush barrier | `window.gridvibeFlushLivePresentation` | Exposed as a named seam so Stage 4 reuses this one barrier instead of re-deriving mode-specific capture. It is the one export here with no consumer yet, and that is on purpose. |
| 10. Top bar on the queue, `/ui-state` removed | `reportTopbarVisibility()` | The DOM read in explicit Save Workspace is kept — it was already the correct barrier behaviour for that field. |

**Enumerating `update_browser_tab_strip()` before removing it, as item 2 requires.** The stage text warns that "real mode switches and restore also reach it." They do not, and the removal is safe because of that:

| Caller | Reached by | Disposition |
|---|---|---|
| `change_session_mode()`, `"tabs" in data` branch | `browserPersistTabs()` only | Both removed. The strip is now presentation state. |
| `change_session_mode()`, URL branch | terminal ⇄ browser toggle, URL-bar navigate | Untouched — it calls `merge_browser_tabs()`, a different method. |
| restart restore and saved-preset launch | `POST /api/sessions` launch config | Untouched — never went through the mode route. |

The F1 guarantee that used to live in the deleted branch ("only an explicit mode switch may put a pane back into browser mode") is preserved and better placed: `pane_fields_for_mode()` rejects `browser_tabs` for a pane whose `startup_mode` is not `browser`, so a late strip is a `400` instead of a silent re-entry. `tests/test_api.py::test_stale_browser_tab_post_cannot_reenter_browser_mode` now asserts that.

**Three decisions the stage text left open.**

1. **Geometry is sent only for a real custom split.** `buildActiveWorkspaceLayoutSnapshot()` will happily synthesise a `layout-split-local` record for a standard grid, and sending it would make every restore take the split path for no gain. The payload omits `workspace_layout` unless the live (or cached) grid actually is `layout-split-local`; omitted means "unchanged" in the manager, so a group's stored geometry is never overwritten by a derived equivalent. `layout` is never sent — nothing in this stage changes it.
2. **A pane a batch cannot identify cancels the whole batch.** The transaction is all-or-nothing by design, so `buildGroupPresentationPayload()` returns `null` rather than a partial group, and a tab this window has never rendered contributes nothing at all (the manager's copy stays authoritative). The same rule keeps a not-yet-rendered browser pane from blanking a stored strip with an empty one.
3. **Pane mode is read from `session.startup_mode`, not from the rendered pane type.** They disagree for a moment during a mode switch, and the server decides which fields a pane may carry from the former. `replaceSessionPaneMode()` republishes the group afterwards, so a batch built for the previous mode self-heals instead of waiting for the next user action.

**Coverage.** `tests/test_session_presentation.py` grew from 6 to 17 tests. The client half runs for real in Node (skipped when `node` is absent) rather than being asserted as source text, and the payload it produces is posted at the live route so the two halves cannot drift:

| Test case | Covers |
|---|---|
| `GroupPresentationPayloadTestCase` (4) | Every in-boundary field in visual pane order, no launch/credential field, custom geometry present vs. a standard layout absent, an unidentifiable pane sending nothing, an empty strip not blanking a stored one. |
| `PresentationWiringTestCase` (2) | The page's own Node-built payload accepted end to end, then Save Workspace storing the reordered panes, resized weights, explorer tabs/theme/sidebars, browser strip, and top-bar state — starting from a manager whose copy is deliberately older. Plus: the terminals page really loads the module. |
| `PresentationControllerTestCase` (5) | The flush barrier re-capturing the newest state before resolving; a failed write failing the barrier *and* being repaired within a bounded budget; the repair stopping on the first acceptance; two fast top-bar toggles leaving the newer value; a refetched revision refusing to rebase over an in-flight write. |
| `PresentationRouteTestCase` | Now also asserts `PATCH /ui-state` is absent from the URL map. |

Three `tests/test_api.py` cases were rewritten rather than deleted: the whole-strip replacement and the stale-strip refusal moved to the presentation route, and the concurrent-write injection in `test_switch_browser_navigation_merges_with_latest_tab_strip` now lands a presentation transaction between the read and the merge. One pure source-text test (`test_browser_tab_persist_is_cancelled_and_revalidated_before_post`) was removed with the functions it named; two single-line source-text assertions went with the surfaces they named.

**Post-Stage-3 state-authority map.** Only the rows the stage changed:

| State | Live authority | Runtime autosave / Save Workspace source |
|---|---|---|
| pane visual order | browser DOM, acknowledged on drop | manager `pane_order` |
| custom split rectangles/weights | browser, acknowledged on resize/split settle | manager `workspace_layout` |
| browser tab URLs and active tab | browser, acknowledged on the ordered queue | manager, exact after a flush |
| explorer open/active tabs, view mode, diff target, scroll, zoom, wrap, folds | browser, acknowledged on change (scroll at capture) | manager, exact after a flush |
| explorer sidebar open flags | browser, acknowledged on toggle | manager |
| explorer theme | browser DOM, acknowledged on toggle; `localStorage` is a cache | manager |
| Markdown/source appearance | page-global `localStorage`, acknowledged per explorer pane | manager (still per-pane — scope is Stage 5) |
| workspace top-bar visibility | browser DOM, acknowledged on the ordered workspace queue | manager, plus the exact DOM value on explicit Save Workspace |

**What this stage does *not* do.** Autosave still reads the last acknowledged state, so a change made in the second between an event and its acknowledgement is not in an autosave snapshot — only an explicit save is exact, which is the stage's own contract (item 7). Save Session still writes presentation fields through `update_group_saved_session()` without touching the group revision; it is a different product and Stage 5 owns any reconciliation. And Stage 3 changed no field *schema*: the richer explorer record in SGP-03 is Stage 5's, so the Stage 0 fixture tests are still expected failures.

**Gate results:**

- `python -m ruff check .` — passed, "All checks passed!".
- `python tests/run_tests.py` — 1,279 tests, `OK (skipped=7, expected failures=18)`. The expected-failure count is unchanged at 18: Stage 2 consumed the eight forcing functions this work shares with it, and the remaining ones belong to Stages 4-6.

### Stage 4 — Unify explicit close and restart as one save-or-exit transaction

Connected findings: SGP-01, SGP-02, SGP-05, SGP-06, SGP-10, SGP-11.

**Baseline note.** Two of SGP-11's four evidence bullets were fixed out of band: the manual **Save & Restart** button now captures every live workspace by ID and aborts the restart on a failed save. Do not re-plan those as new work, and do not treat the existing implementation as the target shape either — it is a client-side loop over `POST /api/runtime-state/save` where item 2 requires one server-side lifecycle service, and it sources `topbar_visible` from `localStorage` where item 3 requires a flush handshake with the owning window. This stage **replaces** `saveWorkspaceForRestart()` rather than extending it. The behavior it delivers today is the floor this stage may not regress: after Stage 4, a manual restart must still cover every live workspace and must still refuse to tear down after a failed save.

**Hard prerequisite: Stage 6 items 1–3 must land before this stage ships.** The **Save open sessions + workspaces** action writes a reusable preset for every live group, which runs through `upsert_saved_session()` → `_normalize_session_config()` → the SGP-06 capacity clamp. A user who has lowered `max_sessions` and then chooses that action at close would durably truncate every live group's preset in a single click — a destructive path this stage would *introduce*, guarded only two stages later. Land the capacity decoupling first (as a "Stage 3.5" if the numbering is kept), or ship this stage with the sessions-plus-workspaces choice disabled until it lands. The workspace-only choice has no such dependency and can ship on the original ordering.

Add a shared, in-page lifecycle modal with action-specific labels:

| Restart action | Close action | Persistence effect |
|---|---|---|
| **Restart without saving current changes** | **Close without saving current changes** | Write nothing; keep older committed snapshots and presets unchanged. |
| **Save open workspaces & restart** | **Save open workspaces & close** | Flush every live workspace's acknowledged presentation, then manually capture every non-empty live workspace into `runtime_state.json`; do not change reusable presets. |
| **Save open sessions + workspaces & restart** | **Save open sessions + workspaces & close** | Save/update a reusable preset for every live group, then capture every live workspace so its groups reference the saved preset identities. |
| **Cancel** | **Cancel** | Make no change and do not terminate. |

Implementation rules:

1. Put reusable lifecycle UI/controller code in a focused frontend module and shared partial, not in `terminals.js` or duplicated between launcher and terminal pages. Do not use `window.confirm` or native browser prompts.
2. Use one server-side lifecycle preparation service. Snapshot/check manager membership under one `SessionManager.lock` hold, release it, perform file I/O, then emit room-scoped updates. Never hold manager or connection locks across client waits, encryption, or disk writes.
3. Treat “open” as process-wide. Ask every live workspace window to flush its Stage 3 presentation queue and acknowledge a target revision. Use a bounded wait and report missing/stale windows; do not guess that a debounce has completed.
4. For workspace-only save, extend the existing all-live capture transaction to accept validated per-workspace active-group/native-zoom metadata. Keep the unique-temp, file-lock, durable-revision, and manual-retention rules.
5. For sessions-plus-workspaces, persist session presets first and update group preset identities, then capture runtime state. This order lets restart restore obtain an encrypted SSH password only from the matching saved preset while keeping `runtime_state.json` password-free.
6. Do not claim cross-file atomicity. If session presets commit and the later workspace capture fails, keep the app open, report the partial result, and leave the original choices active. Selecting the same save choice retries it; **Continue without saving current changes** remains the first choice. Never silently roll back unrelated user presets.
7. Route the manual restart button, update-triggered restart, explicit browser shutdown, and native launcher close request through this controller. For the native X, cancel/defer the pre-close event once, show the in-page modal, and use a guarded approved-close flag to avoid re-entry.
8. Leave involuntary termination semantics unchanged. Ctrl+C, process kill, power loss, or a browser tab disappearing restore the last successfully committed snapshot; they do not attempt an unsafe teardown write.
9. Add behavioral tests for all actions, all-live scope, client timeout, save failure, partial cross-file failure, retry, update restart, native close re-entry, no emits under locks, and secret absence from runtime state/logs.

Exit gate: close and restart expose the same choices and effects, every requested save covers all live workspaces, no failed requested save tears down without a second explicit choice, and old snapshots survive the no-save path untouched.

#### Stage 4 results

Completed 2026-08-08. The proposal remained valid, with two implementation clarifications: its hard prerequisite was delivered in the same change rather than disabling the sessions-plus-workspaces choice, and “missing window” includes a socket that disconnected without the page's explicit `leave_workspace`. Each workspace page now supplies a stable per-window id, so reconnect replaces that stale registration while a genuinely unreachable window is reported instead of silently ignored.

**What shipped.**

| Surface | Change |
|---|---|
| `web/lifecycle.py` | New Flask/Socket.IO-independent lifecycle service and coordinator. It tracks stable workspace windows, performs a bounded process-wide flush handshake, validates/coalesces per-window active-group/native-zoom/top-bar metadata, saves live presets before runtime state, reports partial results without pretending cross-file atomicity, and issues one-use 60-second close/restart decisions only after success. |
| `POST /api/lifecycle/prepare` | The single server entry point for all three save choices. It snapshots manager membership before client waits, emits `lifecycle_flush_requested` only to workspace rooms, accepts `lifecycle_flush_ack` only from the sending socket id, and never holds `SessionManager.lock` or `connection_lock` across an emit, wait, encryption, or file write. |
| `SessionManager.snapshot_lifecycle_workspaces()` | The only credential-bearing live snapshot. It captures shape plus an in-memory SSH password under one manager lock hold; only the preset writer consumes it. The ordinary runtime snapshot remains password-free. |
| `RuntimeStateStore.capture_live_workspaces()` | Accepts validated per-workspace metadata and still commits all non-empty live workspaces through one locked, ordered, atomic file transaction. Native zoom falls back to the previous slot only when the owning window supplied none. |
| `web/static/js/lifecycle.js` + `templates/partials/lifecycle_modal.html` + `web/static/css/lifecycle.css` | Focused shared controller/modal loaded by both pages. It exposes one ordered list: continue without saving, save workspaces, or save sessions plus workspaces. A failed save leaves that list active, so selecting the same save choice retries naturally. The DOM-free request/flush half runs under Node tests. No browser-native dialog is used. |
| `terminals.js` | Reuses `gridvibeFlushLivePresentation`; after that barrier resolves it acknowledges with the live window's active group, native zoom, and top-bar value. A stable window id survives Socket.IO reconnects, while `pagehide` explicitly retires a window. |
| `launcher.js` | `saveWorkspaceForRestart()` and the cross-window `localStorage` read are deleted. Manual restart, update restart, and browser close all open the shared lifecycle controller and pass its decision token to the actual teardown surface. |
| `webview_launcher.py` / `/api/browser-shutdown` | Native restart, native launcher close, and browser shutdown refuse teardown without the matching one-use lifecycle decision. The native `closing` event is cancelled once, opens the in-page modal, and uses guarded approved-close/cancel flags to prevent re-entry. Involuntary process termination paths are unchanged. |

**Hard prerequisite delivered (Stage 6 items 1–3).** `MAX_STORED_SESSION_PANES = 64` is now the immutable saved-shape ceiling shared by preset terminal normalization and custom geometry. `runtime_config.max_sessions` remains only the current default/launch preference. `_normalize_terminal_entries()` preserves a wider stored list, `_normalize_session_config()` no longer truncates `terminal_count`, and `_normalize_workspace_layout()` uses schema-safe grid/origin/original-count bounds for both saved presets and runtime-state reads. An invalid origin makes the geometry invalid; it is never clamped to a different pane.

**Failure and ordering behavior.** `save: none` performs no flush and writes neither file. Workspace-only save flushes then performs one all-live manual capture without touching presets. Sessions-plus-workspaces takes the dedicated credential snapshot, creates uniquely named presets for unattached groups or updates attached presets under their existing credential rules, links each successful preset identity back to the still-live group, and only then captures runtime state. A preset error stops before workspace capture; a later runtime-state error keeps the successful preset commits and reports them. In either case `ready_to_exit` is false, no exit token exists, and the original three choices remain available without a second Retry/Review group.

**Coverage.** The seven Stage 4 forcing functions and the two prerequisite capacity/geometry forcing functions in `tests/test_session_persistence_contract.py` had their `expectedFailure` decorators removed. `tests/test_lifecycle.py` adds executable coverage for all-window acknowledgement, bounded timeout, stale disconnect/rejoin, conflicting metadata, workspace-room scope, no emit under either shared lock, exact window metadata in the stored slot, retryable preparation, encrypted unsaved-SSH credentials with password-free runtime state, both-page wiring, and the real JavaScript flush responder. `tests/test_webview_launcher.py` covers native-X cancellation/re-entry and one-use close/restart decisions. The touched legacy source-text checks were replaced by page/route or Node behavior.

**Exit gate:** met. All four voluntary exit surfaces share the same choices and service; requested saves cover every non-empty live workspace; a flush or persistence failure cannot tear down; partial cross-file success is explicit; and the no-save path leaves both prior persistence files byte-for-byte unchanged.

**Gate results:**

- `python -m ruff check .` — passed, “All checks passed!”.
- `python tests/run_tests.py` — 1,290 tests, `OK (skipped=7, expected failures=9)`. The nine remaining forcing functions belong to Stages 5 and the remainder of 6.

**Post-implementation regression correction (2026-08-08).** The first Stage 4 build used an `open` class when showing and hiding the shared lifecycle dialog, but both page shells define modal visibility with the established `visible` class. Lifecycle requests therefore opened an invisible blocking dialog, affecting both Save & Restart and the native window close button. The controller now uses the shared `visible` state, and an executable Node DOM test verifies that opening exposes the dialog and Cancel hides it again while invoking the cancellation callback. Correction gates: `python -m ruff check .` passed; `python tests/run_tests.py` passed 1,291 tests with 7 skips and the 9 expected failures reserved for later audit stages.

**Native-close and menu follow-up (2026-08-08).** Native X initially called pywebview's synchronous `evaluate_js()` from inside its synchronous cancellable `closing` event. On WebView2 that can block the UI callback waiting for JavaScript while the webview is waiting for the callback to return, presenting as a frozen window. The closing callback now records one pending prompt, schedules JavaScript evaluation on a daemon worker, and returns `False` immediately. The worker opens the shared in-page dialog after the callback releases the UI thread. The confusing post-failure Retry/Review group was also removed: Continue without saving is the first of one persistent three-choice list, and a failed save reports its error while leaving that list active. Behavioral tests verify deferred native JavaScript, single-prompt re-entry protection, the exact choice order on both pages, and failure-state reuse of the same list. Follow-up gates: `python -m ruff check .` passed; JavaScript syntax checks passed; `python tests/run_tests.py` passed 1,292 tests with 7 skips and the 9 expected failures reserved for later audit stages.

### Stages 0–4 verification review (2026-08-08)

Stages 0–4 were re-read against the source after Stage 4 landed, to answer three questions: is the shipped work solid, is it flaky, and did it breach any `## Regression Guardrails` rule. Summary: **solid, not flaky, no guardrail breach, one defect** (SGP-12).

**Gates, re-run on this machine.**

| Gate | Result |
|---|---|
| `python -m ruff check .` | passed, "All checks passed!" |
| `python tests/run_tests.py` | 1,292 tests, `OK (skipped=7, expected failures=9)` |
| `python -m unittest tests.test_lifecycle tests.test_session_presentation tests.test_saved_session_store tests.test_session_persistence_contract` ×3 | 81 tests, `OK (expected failures=9)` on every run; 2.55 s ±0.02 s |

The counts match what [Stage 4 results](#stage-4-results) recorded, so nothing has drifted since.

**Flakiness.** The persistence suites were run three times and are bit-identical in outcome and near-identical in wall time. The reason they are stable rather than luckily green is structural, and worth stating so a later change does not give it away: the lifecycle tests drive `LifecycleCoordinator` directly and acknowledge the flush *synchronously inside the `emit` callback*, so the "success" cases never depend on a thread winning a race. The two tests that do use a wall-clock timeout (`timeout=0` and `timeout=0.1`) are the cases that are *expected* to time out, where a slow machine makes the assertion more certain rather than less. No test sleeps to wait for a condition, and the Node-backed client tests skip cleanly when `node` is absent instead of failing.

**The nine remaining expected failures are the right nine.** They map exactly onto unshipped stages, with no stage's forcing function consumed early and none left behind:

| Test | Owed by |
|---|---|
| `test_restore_keeps_a_root_wider_than_the_current_directory` | Stage 5 item 1 |
| `test_the_canonical_normalizer_preserves_every_in_boundary_field` | Stage 5 items 2–4 |
| `test_the_fixture_round_trips_through_save_workspace_and_restore` | Stage 5 items 2–4 |
| `test_changed_content_drops_scroll_and_folds_but_keeps_view_intent` | Stage 5 item 5 |
| `test_a_staged_diff_identity_tracks_the_index_not_the_working_file` | Stage 5 item 6 |
| `test_markdown_appearance_is_one_workspace_scoped_value` | Stage 5 item 8 |
| `test_an_oversized_group_fails_with_an_actionable_capacity_error` | Stage 6 items 6–7 |
| `test_a_malformed_pane_makes_the_whole_group_unrestorable` | Stage 6 items 4–5 |
| `test_the_chooser_count_matches_what_a_restore_would_really_start` | Stage 6 items 4–5 |

**Guardrail audit.** Each rule was checked against the Stage 1–4 diff (34 files, +8,257/−1,009), not merely against the stage text.

| Guardrail | Verdict | Evidence |
|---|---|---|
| 1. Security | pass | The cross-origin write guard is a blanket `before_request` on every non-GET, so the three new POST routes are covered without registration (`web/app.py:91`). Emits are room-scoped to `workspace_room(...)`. `snapshot_lifecycle_workspaces()` is the only credential-bearing snapshot; its consumer is the preset writer alone, its result never enters a response, and `_save_live_presets()` deliberately drops exception text so a state-file path or secret cannot leak into an error body (`web/lifecycle.py:471`). Host-key policy and password handling are untouched. |
| 2. Concurrency | pass | `apply_group_presentation()` normalizes before the lock, then does every identity/revision/membership check and the whole mutation in one `SessionManager.lock` hold with no emit, request, or file work inside it (`sessions/manager.py:1012`). `capture_live_workspaces()` takes the manager snapshot *before* acquiring the file locks, preserving the documented order (`web/runtime_state.py:844`). `/api/lifecycle/prepare` snapshots membership, releases, and only then emits and waits (`web/api.py:762`). `LifecycleCoordinator` emits outside its own condition lock. `self.lock` is an `RLock`, so `snapshot_lifecycle_workspaces()` nesting `snapshot_live_workspaces()` is safe rather than lucky. `write_json_atomically()` builds a unique `uuid4` temp name in the target directory, fsyncs, backs up, then `os.replace`s (`web/state_files.py`). |
| 3. Performance | pass | No new polling. `CONTINUOUS_UPDATE_FLOOR_MS = 1000` floors scroll/zoom coalescing, and scroll is not even an event — it is folded into the batch at capture time. Structural changes batch on a microtask. No new SSH handshakes and no CDN assets; `session-persistence.js` and `lifecycle.js` are vendored local files. |
| 4. Correctness | pass | No `window.confirm`/`alert`/`prompt` anywhere in `lifecycle.js` or `session-persistence.js`; the lifecycle dialog is the shared in-page partial, and the post-correction fix to use the shared `visible` class is the right one. Shell quoting is untouched. |
| 5. Dead code | pass | `lifecycle_flush_requested` has exactly one listener (`lifecycle.js:182`) and `lifecycle_flush_ack` exactly one handler (`api.py:2926`) — the emit and its consumer shipped in the same change, as Stage 2 required. Both superseded writers were deleted rather than left dual. The one deliberate exception is documented: `window.gridvibeFlushLivePresentation` was exported by Stage 3 with no consumer, and Stage 4 consumed it. |
| 6. Architecture/DRY | pass, with a note | New backend code went to `web/lifecycle.py`, `web/session_presentation.py`, `web/state_files.py`, and `web/saved_session_store.py`; the API routes stayed thin. Stage 1's unplanned extraction of the file primitives was the DRY-correct call — copying the lock/atomic-write/backup code would have given the two stores two chances to drift. Frontend logic went to two new files. **Note:** `terminals.js` grew ~340 lines to 8,001 and is again the largest frontend file (`explorer-viewer.js` is 7,934). The growth is defensible — what landed there is the DOM adapter, which by definition cannot leave the page — but the file is back at the size that triggered the original split, and the next substantial addition to it should force a domain extraction rather than another exception. |
| 7. Styling | pass | `lifecycle.css` contains no hex or `rgb()` literal; every color comes from `tokens.css`. Icons are unchanged `currentColor` SVGs. |
| 8. Interaction | pass | Irreversible actions keep their in-page confirms; the lifecycle modal *is* the confirm for close/restart. Failure states keep the three choices active, so retry is the same button. Busy state is `card.classList.toggle('is-busy', …)` and `aria-busy`, never rewritten markup. |
| 9. Logging | pass | No new INFO-level teardown chatter and no ANSI. `web/lifecycle.py` logs nothing at all, which is safe but leaves Stage 7 item 3's shape diagnostics (workspace/group ids, revisions, failure category) still owed. |
| 10. New features | pass | No new state file; both new writers go through the existing durable machinery. `runtime_state.json` stays password-free — the credential snapshot is a separate method feeding only the preset writer. Manual-retention markers are preserved: `capture_live_workspaces()` carries `previous_slot` into `_build_slot()`, and native zoom falls back to the stored value only when the owning window supplied none. |

**What was verified beyond the guardrails.**

- **Stage 3's flush barrier is real, not nominal.** `saveWorkspace()` re-captures every visible *and* cached group plus window chrome, awaits acknowledgement, and only then calls `/api/runtime-state/save`; a failed flush produces no capture and no success toast.
- **Stage 4's ordering is as specified.** Presets commit before runtime state; a preset error returns before workspace capture; a runtime-state error keeps the successful preset commits and reports them; `ready_to_exit` is false and no token is issued in either case. `save: none` writes neither file.
- **Teardown really is gated.** `/api/browser-shutdown` consumes a one-use decision and answers `409 lifecycle_decision_required` without one, and the native bridge does the same. The token is `secrets.token_urlsafe(24)`, single-use, 60-second TTL, pruned and capped.
- **The `attachFlushResponder` listener is registered once**, at socket construction rather than on `connect`, so a reconnect does not stack duplicate handlers.

**The one defect.** The stale-window registration in `LifecycleCoordinator` is unbounded in time and unreachable by every removal path once its socket dies without `pagehide`, and the `window_id` that is supposed to redeem it is regenerated on every page load. The consequence is that one abnormally lost window permanently blocks save-on-exit for its workspace. Written up in full as [SGP-12](#sgp-12--high-a-stale-lifecycle-window-registration-permanently-blocks-every-later-save-on-exit); fixed by Stage 4.5 item 1.

**Where `docs/r&d/todos.txt` fits.** Its notes were checked against every stage, shipped and unshipped. Two are genuinely uncovered and become Stage 4.5; one is the design working as intended.

| `todos.txt` note | Covered by | Disposition |
|---|---|---|
| "Add save button to workspaces panel in launcher window … besides open and close" | nothing | New — [SGP-14](#sgp-14--low-the-launchers-live-workspace-rows-cannot-save-one-workspace), Stage 4.5 item 3. Stage 4 built the flush handshake this needs and used it only for the all-live exit transaction. |
| "I can close a workspace, it gets removed from the opened workspaces list but remains in the saved Reopen a saved workspace pop up" | intended behavior | **Not a defect.** This is the **Close live workspace** verb, distinct from **Close and forget**; both are offered, and the launcher row's tooltip states the difference. Recorded in [SGP-13](#sgp-13--medium-workspace-labels-have-no-uniqueness-rule-so-a-live-workspace-and-a-saved-snapshot-can-share-a-name) so it is not "fixed" into a data-losing close. If the distinction is being missed in practice that is a labelling problem, not a persistence one. |
| "I can make a new workspace with the same workspace name … should notify the user the name is taken" | nothing | New — [SGP-13](#sgp-13--medium-workspace-labels-have-no-uniqueness-rule-so-a-live-workspace-and-a-saved-snapshot-can-share-a-name), Stage 4.5 item 2. Confirmed: no uniqueness check exists over live workspaces or saved slots. |

The note's own closing question — whether the workspace-name issue fits into Stage 4 — is answered no. Stage 4 owns the close/restart *transaction*; workspace naming and identity is a different surface that Stage 4 neither touched nor depends on.

### Stage 4.5 — Lifecycle window recovery, workspace name identity, and per-workspace save — **done**

Connected findings: SGP-12, SGP-13, SGP-14.

This stage was added by the [Stages 0–4 verification review](#stages-04-verification-review-2026-08-08). It exists because three problems share one surface — the launcher's workspace list and the Stage 4 flush handshake behind it — and because item 1 is a defect in shipped code rather than new work.

**Ordering.** Item 1 is a bug fix in Stage 4's own module and should ship first; it is independent of Stages 5 and 6 and must not wait for them. Items 2 and 3 are user-visible additions that can ship in parallel with Stage 5 or 6, but before Stage 7 closes the documentation. Item 3 depends on item 1: a per-workspace Save that inherits a permanently stale window record would fail exactly as the exit transaction does today.

1. **Make a lost workspace window recoverable.** Give the workspace page a `window_id` that is stable per window across reloads (`sessionStorage` is window-scoped and survives reload while staying distinct between tabs), so a reload replaces its own registration even when `pagehide` never fired. Add a bounded disconnect grace period after which a stale record is treated as departed and dropped from `expected` rather than blocking the flush forever. Bound `_windows` per workspace. Keep the fresh-disconnect case reporting `client_stale` exactly as today — the point is to stop a *permanent* block, not to start ignoring real windows. Update `test_disconnected_window_stays_stale_until_its_stable_id_rejoins`, whose name currently asserts a stability the client does not provide.
2. **Give workspace labels one owner.** Add a single resolver in `web/workspaces.py` that checks a requested label against both live workspaces and saved slots, case- and whitespace-insensitively, and reject a duplicate at `POST /api/workspaces` and `PATCH /api/workspaces/<id>` with a `409` that names the conflicting kind. The client turns that into an actionable choice — reopen the saved workspace, forget it, or pick another name — with the matching action inline. Mutate nothing on rejection: no auto-rename, no auto-forget. Leave empty labels unconstrained; "unnamed" is not a name, and positional labels already disambiguate them.
3. **Add a per-row Save to the launcher's Workspaces card.** Reuse the Stage 4 flush handshake scoped to one workspace rather than adding a third capture path. It flushes the owning window before capturing or refuses with the existing retryable affordance; it never writes reusable presets and never terminates anything. A workspace with no reachable window is reported, not silently captured from the last acknowledged server state — the same rule as Stage 4 item 3.
4. **Tests.** A window lost without `leave_workspace` blocks the flush while fresh, stops blocking it after the grace period, and is replaced by its own reload; a duplicate label is refused at create and at rename against both a live and a saved namesake, with stored state unchanged; an empty label is still accepted twice; the per-row Save captures one workspace after a real flush, leaves `saved_sessions.json` untouched, leaves every other workspace's slot untouched, and reports rather than guesses when the window is unreachable.

Exit gate: no single lost window can permanently prevent saving on exit, a workspace name identifies at most one workspace across both live and saved state, and one workspace can be saved from the surface that lists them all without exiting anything.

#### Stage 4.5 results

Completed 2026-08-08 and corrected 2026-08-09 after hands-on launcher testing. All three items shipped in one change, in the stage's order — item 1 (the Stage 4 defect) first, item 3 built on it. SGP-12, SGP-13, and SGP-14 are closed.

**What shipped.**

| Surface | Change |
|---|---|
| `web/static/js/shared.js` + `terminals.js` | `getLifecycleWindowId()` keeps the lifecycle window id in `sessionStorage` — stable per *window* across reloads, distinct between tabs — so a reload rejoins with its own id and replaces its own registration even when `pagehide` never fired. A per-load fallback covers locked-down storage, bounded server-side by the grace period. |
| `web/lifecycle.py` | Window records carry `disconnected_at`/`joined_at`. `LIFECYCLE_STALE_WINDOW_GRACE_SECONDS = 120.0`: a stale record inside the grace period still blocks the flush with `client_stale` exactly as before; past it the record is *departed* — dropped and not counted in `expected`. `_MAX_WINDOWS_PER_WORKSPACE = 16` caps records per workspace (disconnected first, then oldest), so accumulation is impossible even if both other rules are bypassed. New `connected_window_count()` backs the per-workspace save's reachability check. |
| `web/lifecycle.py` — `prepare_workspace_save()` | The Stage 4 flush handshake scoped to one workspace: flush-then-capture or refuse. A workspace with no reachable window is reported (retryable `503`), never captured from the last acknowledged server state; the capture goes through `capture_workspace()`, so only that slot is written, `saved_sessions.json` is never touched, and no teardown decision is issued. |
| `web/workspaces.py` — `workspace_label_conflict()` | The one owner of the workspace-name namespace: case- and whitespace-insensitive, over live workspaces *and* restorable saved slots, with self-exclusion so a rename may keep its own name (its live record and its own saved slot are the same identity). Empty labels stay unconstrained. Nothing is mutated on a conflict — no auto-rename, no auto-forget. |
| `POST /api/workspaces`, `PATCH /api/workspaces/<id>`, `POST /api/workspaces/validate-label`, `resolve_launch_destination()` | A duplicate label answers `409` with `conflict: workspace_label_taken`, `conflict_kind: live|saved`, and the holder's workspace id. The validation route is read-only in effect: it lets a launch draft ask the same namespace owner without creating an empty workspace. The launch-destination choke point checks again when it commits, so **Launch into a new workspace** and **Move to new workspace** cannot bypass the namespace or a validation/launch race — the exit gate cannot hold otherwise. This is the one deviation from the stage text, which named only the create and rename routes. |
| `POST /api/workspaces/<id>/save` | The per-row Save route: thin boundary over `prepare_workspace_save()`, emitting `lifecycle_flush_requested` to the one workspace room. An empty workspace answers `409` without capturing; unknown `404`, malformed id `400`. |
| `web/static/js/workspaces.js` | `workspaceNameError()` carries the 409 conflict fields; `resolveWorkspaceNameConflict()` turns one into the inline choice — open the live namesake, forget the saved snapshot (confirmed, danger-styled), or cancel and pick another name. The shared name modal also accepts an async validator: it stays open, restores its controls, and renders the exact server error beside the input instead of accepting a rejected value. `saveLiveWorkspace()` calls the per-workspace Save route. |
| `terminals.js` / `launcher.js` | Rename and New Workspace route name conflicts through the inline choice. **Launch into a new workspace** is always a fresh blank draft, validates the name inside that popup before putting it on the Launch button, and clears the error as the user edits. The launch route remains the commit-time guard; if another request takes the name after validation, its `409` reopens the same blank popup with the exact error rather than leaving the rejected draft selected or degrading to the launcher's page-level failure line. The launcher's Workspaces card gains the per-row **Save** button: busy class while saving, retry wording on a `503`, an info message on the empty-workspace `409`. |

**Behavioral clarifications.** The per-workspace Save treats a *disconnected* window — fresh or past grace — the same as no window at all ("no reachable window", retryable). The grace-period distinction belongs to the exit transaction's `request_flush`, where a fresh loss must still block; the save verb only needs reachability, and both unreachable cases are reported rather than guessed. A new-workspace launch name is not durable state and is not a reservation: reopening its picker starts blank. The non-mutating validation improves the interaction, while the launch transaction remains authoritative.

**Coverage.** `tests/test_lifecycle.py`: a fresh disconnect blocks the flush with `client_stale`, a past-grace record is dropped and stops blocking, a reload with the same stable id replaces its own record without `leave_workspace` (one registration, not two), and per-workspace window records are bounded. The route tests prove the per-row Save flushes one window and captures only its slot — the sibling slot byte-for-byte unchanged, `saved_sessions.json` never created — and that it reports rather than guesses with no window and with a lost window, answers `409` for an empty workspace, and rejects unknown/malformed ids. `tests/test_multi_workspace.py`: create and rename refuse live and saved namesakes case/whitespace-insensitively with nothing mutated (the stored slot byte-for-byte unchanged), a rename may keep its own name with new casing, unlabelled workspaces stay unconstrained, and launch-into-new refuses a taken label without minting a workspace. The validation-route regression proves a saved conflict and a free normalized name mutate neither live nor stored state. Its Node behavioral regression executes the shipped shared modal, validator, and launcher chooser: stale drafts are cleared, the dialog starts blank and remains open on the exact `409`, editing clears the error, a free name alone becomes the destination, and a commit-time race returns to a fresh popup. The old `test_disconnected_window_stays_stale_until_its_stable_id_rejoins` was replaced by the fresh/grace/reload/bound quartet — its name asserted a per-window stability the client now actually provides. Regression matrix items 36–39 are now executable and green.

**Exit gate:** met. No single lost window can permanently prevent saving on exit; a non-empty workspace name identifies at most one workspace across live and saved state; and one workspace can be saved from the launcher without exiting anything.

**Gate results:**

- `python -m ruff check .` — passed, "All checks passed!".
- `python tests/run_tests.py` — 1,309 tests, `OK (skipped=7, expected failures=9)` after the launcher-dialog correction. The nine remaining forcing functions belong to Stage 5 and the remainder of 6, and still fail on their intended assertions.

**Stage 5 readiness.** Item 1 shipped before Stage 5 or 6 began, as the closing recommendation requires. Nothing in this stage touched the surfaces Stage 5 owns — the explorer presentation record, `_prepare_launch_sessions()` root handling, or the appearance-field migration path — and the frozen Stage 0 contract constants are unchanged, so Stage 5 starts from a green suite with its forcing functions still failing for the right reasons.

### Stage 5 — Complete explorer snapshot semantics and preserve explorer root — **done**

Outcome recorded in [Stage 5 results](#stage-5-results).

Connected findings: SGP-03, SGP-04, SGP-08, SGP-10.

1. Preserve a supplied `explorer_root_directory` through `_prepare_launch_sessions()` with the existing root-confinement validation.
2. Version the explorer presentation record while continuing to read the current flat record.
3. Separate view intent from content-bound state.
4. Add bounded per-panel horizontal/vertical ratios, directory-list scroll, Files/Git structural sidebar scroll, sidebar width, expanded tree paths, and approved Git commit expansion. Repository-search result scroll remains ephemeral under product decision 4.
5. Restore intent even when content changed; restore scroll/folds only when the relevant revision matches.
6. For Diff, use an identity that changes with the rendered diff (commit hash or Git/index/worktree revision), not only working-file content plus mode.
7. Re-fetch directory, tree, Git, and search data. Persist only normalized navigation/expansion intent; never persist the Search query, results, selection, expansion, or result-list scroll.
8. Move Markdown/source appearance authority to the workspace record, keep the per-pane fields as backward-compatible read aliases during migration, and retire the shared `localStorage` keys as an authority. Product decision 5 chose workspace-global scope, and that scope cannot be expressed in `localStorage`, which is per origin and therefore shared by every workspace window in one browser profile. **Follow the `topbar_visible` path already in production** — `Workspace` field → `snapshot_live_workspaces()` → slot validation with a non-coercing normalizer → restore reapplication — rather than designing a new one. The only difference is that appearance has three values instead of one boolean and needs a migration read from the existing per-pane fields; the plumbing is otherwise identical and already proven by tests.

Suggested backward-compatible record shape:

```json
{
  "version": 2,
  "intent": {"mode": "diff", "diff_mode": "staged"},
  "content_revision": "sha256:index...",
  "content_revisions": {
    "source": "sha256:file...",
    "diff": "sha256:index..."
  },
  "scroll": {
    "source": {"x": 0.0, "y": 0.3},
    "preview": {"x": 0.0, "y": 0.6},
    "diff": {"x": 0.2, "y": 0.4}
  },
  "font_size": 18,
  "wrap": {"source": true, "preview": true, "diff": false},
  "folds": [12, 44],
  "fold_revision": "sha256:file..."
}
```

The exact keys are less important than one canonical normalizer and separate identity semantics.

Exit gate: the agreed explorer fixture round-trips through Save Session/import, manual workspace save/restart restore, and autosave/restart restore on both local and mocked SSH/SFTP explorers.

#### Stage 5 results

Completed 2026-08-09 after re-validating every item against the post-Stage-4.5 source, the product decisions above, and the executable Stage 0 fixture. SGP-03, SGP-04, and SGP-08 are closed; SGP-10 is narrowed to the Stage 6/7 work outside explorer persistence.

**Validity review and corrections.** All eight implementation points remain necessary and compatible with the current architecture. Four details needed correction before implementation:

1. The frozen fixture draft named unsupported appearance choices (`compact` and `mono`). The actual allowlists are the UI/backend contract, so the fixture now uses `paper` and `jetbrains-mono` rather than adding dead aliases.
2. Directory scroll is content-bound too. The Preview directory fixture now carries a directory revision; source, preview, diff, and folds carry their relevant per-panel/fold revisions instead of sharing one ambiguous identity.
3. Workspace appearance belongs on the existing `POST /api/workspace-presentation` compare-and-swap transaction. A separate appearance endpoint would have introduced the duplicate writer item 8 explicitly forbids.
4. “Sidebar scroll” is structural Files/Git scroll. Search's only scrolling body is fetched result state, which product decision 4 excludes. Conversely, product decision 3 explicitly approves Git view/expansion intent, so bounded expanded commit identities were added alongside Files-tree paths.

Item 1's “existing root-confinement validation” remains the explorer backend's local/SFTP operation boundary: preserving the captured root does not weaken it. Old snapshots without a root still fall back to their captured directory.

**What shipped.**

| Surface | Change |
|---|---|
| `web/session_presentation.py` | One strict bounded v2 normalizer plus flat-v1 read compatibility. Durable mode/diff intent is separate from per-panel x/y scroll and Markdown folds; content revisions are bounded per panel. Sidebar width, Files/Git scroll, Files-tree paths, and Git commit expansion are normalized here for every persistence product. Unsupported record versions and wrong nested types are rejected at the live boundary. |
| `web/static/js/explorer-persistence.js` | New DOM-free, Node-testable record builder/migrator/resolver. It filters scroll panel by panel, keeps view intent through changed content, migrates flat records, and derives Diff identity from the actual rendered patch plus target. |
| `explorer-viewer.js` / `terminals.js` / `session-persistence.js` | The DOM adapter captures/restores every approved structural field, queues continuous scroll/resize updates on the existing one-second floor, refetches content, and delays Diff scroll restoration until the asynchronous rendered patch can be revision-checked. Search result state remains unsaved. |
| `sessions/manager.py`, `web/saved_sessions.py`, `web/runtime_state.py` | The manager model and both durable products carry the same canonical fields. Save Session/import, acknowledged autosave, explicit Save Workspace, lifecycle capture, and restart restore therefore share one schema. |
| `web/workspaces.py` | `_prepare_launch_sessions()` preserves a supplied local or SSH `explorer_root_directory`; an absent legacy value still falls back to `directory`. |
| Workspace appearance path | `Workspace` now owns preset/Markdown-font/source-font values. The existing workspace transaction, manager snapshot, runtime-slot validation, and restore path carry them with `topbar_visible`. The first valid legacy explorer pane seeds an uninitialized workspace once, then workspace values mirror back to per-pane read aliases. Browser `localStorage` remains only a non-authoritative startup cache. |

**Behavioral coverage.** `tests/test_session_presentation.py` executes v1 migration, v2 construction, per-panel x/y filtering, stale-content intent retention, fold revision filtering, directory scroll, rendered-Diff identity, workspace payload construction, strict appearance validation, and the existing ordered workspace transaction. `tests/test_session_persistence_contract.py` now runs the complete explorer fixture through saved-session create/import/relaunch, explicit workspace save/restart restore, acknowledged autosave/restart restore, legacy runtime appearance migration, local root restore, SSH presentation restore, and remote parent-root preservation. Existing mocked-SFTP explorer tests continue to prove that operations remain confined to that restored root. The six Stage 5 forcing functions no longer use `expectedFailure`; the three remaining expected failures belong to Stage 6.

**Exit gate:** met. The fixture crosses both persistence products and both runtime capture intents; local and SSH explorer restoration preserve presentation and distinct roots, while mocked-SFTP behavior remains root-confined.

**Gate results:**

- `.venv\Scripts\python.exe -m ruff check .` — passed, "All checks passed!".
- `.venv\Scripts\python.exe tests/run_tests.py` — 1,318 tests, `OK (skipped=7, expected failures=3)`. The six Stage 5 forcing functions are green; the three expected failures are the explicitly deferred Stage 6 contracts.

### Stage 6 — Decouple stored shape from current capacity and harden restore validation

Connected findings: SGP-06, SGP-07, SGP-10.

**Items 1–3 shipped with Stage 4 as its hard prerequisite** (see [Stage 4 results](#stage-4-results)). Items 4–7 shipped here — see [Stage 6 results](#stage-6-results).

1. Normalize saved data against an immutable schema/product maximum, not `runtime_config.max_sessions`.
2. Preserve extra stored terminal entries even when the current launch cap is lower.
3. Make workspace-layout normalization use schema-safe bounds and the actual stored pane count; apply current-cap checks only at launch/split. Fix the normalizer for **both** callers — the saved-preset path and the runtime-state read path (`web/runtime_state.py:312`) — and make unrepresentable geometry fail as geometry instead of clamping `originSlot` into a different valid-looking value.
4. Validate every nested pane field through the canonical presentation normalizer, which rejects wrong types rather than coercing them. `normalize_topbar_visible()` is already written this way and should be adopted as the pattern once Stage 2 moves it into `web/session_presentation.py`.
5. Make a malformed restored group fail as a group rather than silently dropping panes, and make a silently coercible malformed value (`explorer_open_tabs: "abc"`) fail the same way instead of installing garbage. State the boundary of this rule explicitly rather than applying it everywhere: **launchable shape fails; window chrome degrades.** An invalid stored `topbar_visible` correctly defaults to visible instead of failing the slot, because chrome is not shape and losing a whole workspace's restore over a stray boolean would be worse than the defect it guards. Keep that behavior and document it here so a later reader does not "fix" it into a rejection.
6. Return an actionable capacity error and keep the snapshot/preset untouched.
7. Apply the chosen capacity policy from the open questions.

Exit gate: lowering and raising the setting is nondestructive for both stored shape and stored geometry values, restore chooser counts match validated launchable shape, no wrong-typed nested value survives as coerced live state, and no partial success is reported as exact restore.

#### Stage 6 results

Completed 2026-08-09 after re-validating items 4–7 against the post-Stage-5 source. SGP-06 and SGP-07 are closed; SGP-10 is narrowed to the Stage 7 cleanup only.

**Validity review.** All four remaining items were confirmed against the source before implementation. Nothing needed correcting, and one item was already partly satisfied:

1. Item 4 held exactly as written. `_validate_session()` was still `{key: session.get(key) for key in _SESSION_SNAPSHOT_FIELDS}` (`web/runtime_state.py:267`) — an allowlist over *keys*, with no value check — and `_session_launch_fields()` still ran `list()`/`dict()`/`int()` over the same fields (`sessions/manager.py:790`–`802`). The audit's three-row table reproduced verbatim: `dict("x")` and `int("abc")` raised, `list("abc")` did not.
2. Item 5 held. `install_session_group()` staged panes in a `try/except … continue` loop and only failed when *every* pane was unusable (`sessions/manager.py:841`), so one bad pane produced a smaller group reported as a successful restore. The item's stated exception was already true and needed no code change: `_validate_slot()` degrades an invalid `topbar_visible` to visible and an invalid appearance to the defaults rather than failing the slot. That behavior is now asserted and documented in `_validate_group()`'s docstring so a later reader does not "fix" it into a rejection, as the item asks.
3. Item 6 held for the *message* and was already satisfied for the *data*. The refusal was `"Maximum {max_sessions} sessions allowed"` (`web/workspaces.py:612`, `web/api.py:2468`) — it named the ceiling but never the number to raise it to, and `restore_workspace()` forwards only the error string into its per-group result, so anything actionable had to be inside that sentence. Nondestructiveness was already correct: a launch refusal writes neither persistence file.
4. Item 7 is item 6's policy half (product decision 6) and required no separate mechanism.

One implementation consequence worth recording: the canonical normalizer's dependency rules (`explorer_active_tab` requires `explorer_open_tabs`) are a *client payload* rule, and applying them unchanged to stored panes would have made a hypothetical older record unrestorable. `normalize_pane_presentation_fields()` therefore treats absent and `None` as "not captured by this build" and supplies the empty companion list, so the dependency degrades to the ordinary "an active tab must be an open tab" value rule on the read path while staying a hard rejection for live payloads.

**What shipped.**

| Surface | Change |
|---|---|
| `web/session_presentation.py` | New `normalize_pane_presentation_fields()` — the strict presentation subset of a pane body that also carries launch fields. Absent/`None` stays absent so a legacy record keeps its defaults; every supplied field is type-checked and bounded by the existing `normalize_pane_presentation()`. Raising is the signal that the pane is not launchable. |
| `web/runtime_state.py` | `_validate_session()` runs nested explorer/browser state through that normalizer and returns `None` on rejection. `_validate_group()` now fails the **whole group** on any rejected pane instead of appending the survivors. Both read paths share the gate, so the chooser's counts and a restore's shape still cannot disagree. A read still never rewrites the file. |
| `sessions/manager.py` | `_session_launch_fields()` no longer coerces. It holds each presentation field's *default* and overlays the normalizer's output, so a wrong-typed request field raises instead of being converted. `install_session_group()` turns any unstageable pane into a failed group with a shape-only log line (pane position, group id, validation reason — no host, directory, or command). |
| `web/workspaces.py`, `web/api.py` | New `capacity_refusal()` — one actionable sentence naming the required pane count, the current `max_sessions`, and where to change it. Used by launch, by `POST /api/sessions/<id>/split`, and therefore by every per-group restore result that forwards a launch error. |

**Behavioral coverage.** `tests/test_multi_workspace.py::RuntimeStateNestedFieldValidationTestCase` covers seven wrong-typed fields costing the group (including the silently coercible `explorer_open_tabs: "abc"`), the type/bounds boundary (an out-of-range but correctly typed value is still bounded, not rejected), an active tab outside its open tabs degrading to Preview, a legacy slot without the newer fields restoring unchanged, invalid window chrome degrading instead of costing the slot, shape-only rejection logging, and a rejecting read leaving the file byte-for-byte intact. `AtomicGroupInstallTestCase` gains the launch-side cases: one malformed pane among good ones fails the group and installs nothing, a coercible malformed value never becomes live state, and a preset whose `explorer_sidebar_width` is the store's `0` "unset" still opens at the 260px default rather than at the normalizer's 180px floor. `tests/test_session_persistence_contract.py` adds the launch-side capacity case (an oversized preset is refused actionably and left byte-for-byte unchanged) and asserts the refused slot is still on offer; the three Stage 6 forcing functions no longer use `expectedFailure`. The capacity assertions in `tests/test_api.py` and `tests/test_multi_workspace.py` now compare against `capacity_refusal()` rather than a hard-coded sentence, so the actionable wording has one owner.

**Exit gate:** met. Lowering and raising `max_sessions` leaves both stored shape and stored geometry *values* unchanged (Stage 4 prerequisite, still green); chooser counts equal validated launchable shape; no wrong-typed nested value survives as coerced live state at either the read or the launch boundary; and no partial group success can be reported as an exact restore.

**Gate results:**

- `.venv\Scripts\python.exe -m ruff check .` — passed, "All checks passed!".
- `.venv\Scripts\python.exe tests/run_tests.py` — 1,328 tests, `OK (skipped=7)`. No expected failures remain: Stage 0's forcing functions are fully consumed by Stages 1–6.
- The persistence suites (`test_session_persistence_contract`, `test_multi_workspace`, `test_session_presentation`, `test_lifecycle`, `test_session_manager`) were run three times — 348 tests, `OK` each time — for flakiness.

**Guardrails.** No emit or file work was added under `SessionManager.lock` or `connection_lock` (staging stays outside the lock and now raises before it is taken); no new config key, endpoint, or emit without a consumer — `capacity_refusal()` returns a message the existing error surfaces already display, and no unread structured field was added beside it; the rejection log is DEBUG-appropriate shape-only metadata at WARNING/ERROR for a genuine data fault, with no path, command, or credential; and the shared normalizer keeps one definition per field rather than a second copy in `sessions/manager.py`.

### Stage 7 — Documentation, diagnostics, and cleanup

Connected findings: all, especially SGP-09 and SGP-10.

1. Update `README.md` and `CHANGELOG.md` with the final snapshot boundary and capacity behavior.
2. Keep `CLAUDE.md` and `AGENTS.md` field/architecture contracts accurate if a new presentation module or route is added.
3. Log safe shape diagnostics only: workspace/group/session IDs, revisions, mode names, field counts, and failure category. Never log paths, URLs with secrets, commands, file contents, passwords, or full payloads.
4. Remove superseded browser-tab and local-only explorer persistence writers after every consumer uses the canonical path; do not leave dual writers. *Stage 3 removed the browser-tab writer (`update_browser_tab_strip()` and the mode route's `tabs` branch) and `PATCH /api/workspaces/<id>/ui-state`; Stage 4 removed the launcher's cross-window top-bar `localStorage` read.* What remains is pruning/bounding the two legacy cache families.
5. Remove the JavaScript source-text assertions added with the top-bar change in `tests/test_api.py` — the `workspaceTopbarVisibilityStorageKey`, `data.topbar_visible`, and `snapshotState` string checks. Their behavior is already covered by the backend round-trip tests beside them. *The `/ui-state` one is gone: Stage 3 deleted the route it named.* `CLAUDE.md` forbids adding new ones; do not replace them with equivalents.
6. Run the full Windows gates: `python tests/run_tests.py` and `python -m ruff check .`.

Exit gate: maintained docs match behavior, dead writers are gone, every server event has a consumer, and the full suite passes without source-text-only tests being added.

#### Stage 7 results

Completed 2026-08-09. SGP-09 and SGP-10 are closed; with them every finding in this audit is closed.

**Validity review.** The six items were re-read against the post-Stage-6 source before implementation:

1. Item 1 was already satisfied in substance — Stages 5 and 6 had written the final snapshot boundary and capacity behavior into `README.md` ("Save & restore" and "Restore fidelity" rows). What remained was a `CHANGELOG.md` entry for this stage's own user-visible change, added under Unreleased.
2. Item 2 became real the moment item 4 introduced a new static JS module: `CLAUDE.md`'s structure tree and `AGENTS.md`'s module list both name the frontend domain files, so both now carry `explorer-theme-store.js`, and both contracts gained the one sentence that makes the cache hierarchy explicit (localStorage is cache only, bounded to live panes; lifecycle logging is shape-only).
3. Item 3 held as the verification review recorded it: `web/lifecycle.py` logged nothing. Diagnostics were added there and at the one route-level flush failure in `web/api.py`.
4. Item 4's remainder was exactly the two cache families the SGP-09 amendments quarantined for this stage.
5. Item 5's `snapshotState` half was already gone — it named the launcher's cross-window capture, which Stage 4 deleted along with its tests. What still existed in `tests/test_api.py` were the `workspaceTopbarVisibilityStorageKey` / `getStoredWorkspaceTopbarVisible(currentWorkspaceId)` / `typeof data.topbar_visible === 'boolean'` assertions in `test_terminals_page_exposes_collapsible_topbar` and the `topbar_visible: !document.body...` payload assertion in `test_terminals_page_ships_workspace_save_menu` — all added by the same out-of-band commit (`b8bccf6`), all naming live surfaces, all removed. Their behavior stays covered by the backend round-trip tests beside them (`test_reported_topbar_visibility_is_captured_by_autosave`, `test_manual_save_captures_topbar_visibility_and_the_next_autosave_agrees`, and `tests/test_lifecycle.py`). No equivalents were added, as the item and `CLAUDE.md` require.
6. Item 6's gate results are recorded below.

**What shipped.**

| Surface | Change |
|---|---|
| `web/static/js/explorer-theme-store.js` | New DOM-free, `require()`-able owner of the `gridvibe.explorerTheme` override object (SGP-09). Every `saveTheme()` write and every `pruneStore()` grid-build pass re-bounds the object to live pane keys; an empty result removes the key entirely, and the legacy bare-string format migrates to an empty store. |
| `web/static/js/terminals.js` | The inline store logic delegates to the module; `buildGrid()` prunes after every rebuild and `toggleExplorerTheme()` writes through the bounding path. The override stays a same-run cache for a value the manager owns. |
| `web/static/js/shared.js`, `web/static/js/launcher.js` | New `clearStoredWorkspaceTopbarVisible()`; a successful **Forget** / **Close and forget** drops the workspace's `gridvibe.terminalTopbarVisibility.<id>` cache key, and a failed forget keeps it so a retry sees the same state. |
| `web/lifecycle.py`, `web/api.py` | Shape-only diagnostics: window rejoin/departure/eviction at DEBUG, launcher-save refusal/flush/metadata/persist outcomes and the exit transaction's flush failure at WARNING with failure *categories*, and successful saves at INFO with workspace id, origin, and revision. Failure categories are exception class names, never exception text — store failures can embed filesystem paths. |
| `tests/test_api.py` | The four remaining top-bar source-text assertions removed (item 5). |
| `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md` | Snapshot-boundary/capacity docs verified against shipped behavior; CHANGELOG entry for the cache bounding; module lists and cache/logging contracts updated. |

**Behavioral coverage.** `tests/test_explorer_theme_store.py` (new, executed in Node against the real modules): a theme write drops dead pane keys in the same write while keeping live ones; a grid-build prune drops a restart's dead session ids; pruning with no live panes removes the key entirely; the legacy bare string migrates to an empty store; reads normalize and a keyless write is a no-op; and clearing a workspace's top-bar key removes exactly that key. `tests/test_lifecycle.py::LifecycleDiagnosticsTestCase` (new): a successful launcher save logs id/origin/revision and not the pane's directory; a flush failure logs the category and not client error text; a capture failure logs the exception class and not its path-bearing message; a preset failure logs counts and scopes and never the password, host, directory, or path; and window-registry transitions log at DEBUG with ids only.

**Exit gate:** met. Maintained docs match behavior (item 1–2); no superseded writer remains — both legacy cache families are bounded non-authoritative caches, not writers (item 4); the lifecycle events already had their listeners, and no new emit, endpoint, or config key was added; the suite passes with the legacy source-text assertions deleted and no source-text-only tests added (item 5).

**Gate results:**

- `.venv\Scripts\python.exe -m ruff check .` — passed, "All checks passed!".
- `.venv\Scripts\python.exe tests/run_tests.py` — 1,340 tests, `OK (skipped=7)`.
- The persistence suites (`test_session_persistence_contract`, `test_multi_workspace`, `test_session_presentation`, `test_lifecycle`, `test_session_manager`, `test_explorer_theme_store`) were run three times — 360 tests, `OK` each time — for flakiness.

**Guardrails.** No lock-order or emit-under-lock changes (the coordinator still snapshots under its condition and emits outside it; logging added inside lock holds is `logging` only, never socket or file work). The new log lines follow guide levels — window-management state at DEBUG, one-off save outcomes at INFO, actionable anomalies at WARNING, none reachable per-keystroke — and carry no ANSI, paths, commands, or payloads. The frontend change follows the split convention: a new DOM-free domain module instead of growth in `terminals.js`, loaded before it in `templates/terminals.html`, with behavior executed in Node rather than asserted as source text.

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
12. response A arriving after newer response B cannot regress server state **or** client state — the late response must not overwrite `pane._session` with the older strip;
13. a stale second window receives `409` and cannot overwrite a newer presentation silently;
14. that same `409`'d window reconciles and surfaces the outcome rather than silently discarding the arrangement its user made;
15. closing/moving a group during presentation sync cannot update another group or resurrect a closed one;
16. saved-session concurrent create/update/delete preserves unrelated entries;
17. failed replace/delete returns retryable failure and keeps the last-good file;
18. corrupt primary plus valid backup recovers without overwriting the corrupt evidence;
19. two first-run processes converge on one Fernet key, and no importer can observe a partial or zero-byte key file;
20. lowering `max_sessions` does not change stored shape; capacity failure is actionable and nondestructive (covered by Stage 4's prerequisite and Stage 6 — `LaunchCapacityNondestructiveTestCase`, from both the snapshot and the preset side);
21. lowering `max_sessions` leaves stored `originSlot` and geometry *values* unchanged on the runtime-state read path, not merely refusing an oversized restore (covered by Stage 4's prerequisite);
22. malformed nested explorer/browser state rejects the group rather than producing a smaller successful restore (covered by Stage 6 — `MalformedPresentationValidationTestCase`, `RuntimeStateNestedFieldValidationTestCase`, and `AtomicGroupInstallTestCase` for the launch side);
23. malformed-but-coercible nested state (`explorer_open_tabs: "abc"`) is rejected rather than silently coerced into live state and re-captured by autosave (covered by Stage 6, at both the read and the launch boundary);
24. runtime-state snapshots and logs contain no passwords;
25. Socket.IO presentation notifications remain workspace-room scoped and no emit occurs under manager/connection locks;
26. continuous presentation activity (scrolling, zoom dragging) produces no runtime-state disk write on its own and leaves the autosave cadence unchanged;
27. restore after manual save followed by acknowledged autosave uses the newer autosave shape while retaining manual-slot protection;
28. restart/close without saving current changes writes neither persistence file and preserves the previous restore point;
29. save-open-workspaces restart/close flushes and captures every live workspace, not only `default` or the invoking window;
30. save-open-sessions-plus-workspaces saves every live group first, captures matching preset identities second, and keeps passwords out of runtime state;
31. a flush, preset write, or runtime-state write failure leaves the app running with retry and explicit no-save continuation affordances;
32. manual restart, update restart, browser shutdown, and native launcher close use the same lifecycle action contract;
33. workspace top-bar visibility round-trips through manual Save Workspace, autosave, and restart restore, and an invalid stored value degrades to visible without failing the slot (covered today);
34. two fast top-bar toggles cannot leave the server holding the older value once the field moves onto the ordered queue, and a failed write is repaired rather than left to be committed by the next autosave (covered by Stage 3 — `PresentationControllerTestCase`);
35. after `PATCH /api/workspaces/<id>/ui-state` is removed, top-bar visibility still reaches the snapshot from a live window and from a launcher-initiated all-workspace save, with no `localStorage` read in the launcher path (covered by Stages 3–4; Stage 4's lifecycle route test acknowledges the owning window's exact value and asserts it in the all-live stored slot);
36. a workspace window whose socket dies without `leave_workspace` blocks the lifecycle flush while the loss is fresh, stops blocking it once past the grace period, and is replaced by its own reload rather than accumulating a second registration (Stage 4.5 item 1 — covered by `tests/test_lifecycle.py::LifecycleCoordinatorTestCase`);
37. creating or renaming a workspace to a label already held by a live workspace *or* by a saved slot is refused with an actionable conflict, and neither the live workspace nor the stored slot is mutated by the refusal (Stage 4.5 item 2 — covered by `tests/test_multi_workspace.py::MultiWorkspaceStage3TestCase`);
38. two workspaces may still both be unlabelled — uniqueness applies to names, not to their absence (Stage 4.5 item 2 — covered);
39. a per-workspace Save from the launcher flushes that workspace's window and captures only its slot, leaving every sibling slot and `saved_sessions.json` byte-for-byte unchanged, and reports rather than guesses when the window is unreachable (Stage 4.5 item 3 — covered by `tests/test_lifecycle.py::LifecycleRouteTestCase`).

## Product decisions

The schema and lifecycle choices previously left open for Stages 4 and 5 are resolved as follows.

1. **Explorer snapshot boundary:** persist structural navigation and view intent (tabs/order/active tab, Preview path/directory, Source/Preview/Diff, diff target, zoom/wrap/folds, theme/appearance, sidebar open/width/scroll, tree expansion, panel/directory scroll). Do not persist selections, hover, transient errors/loading, fetched results, or clipboard state.
2. **Dirty editor buffers:** do not include them. They contain unsaved file contents, complicate revision conflict handling, and would put filesystem content in workspace state. Keep the existing confirm/discard behavior and persist only successfully saved files.
3. **Git commit-message drafts and destructive-action form state:** do not include them. Persist Git view/expansion intent only, never mutation drafts or busy state.
4. **File-find and repository-search queries/results:** do not restore them. Omit query text, results, result-group expansion, selected-result state, and result scroll. Persist only the structural Search sidebar state already inside the explorer snapshot boundary, such as open state, width, and sidebar scroll.
5. **Markdown preset/font/source-font scope:** make these settings workspace-global. Use the simplest, most efficient, lowest-risk implementation that keeps one canonical appearance value per workspace rather than per explorer pane. Note that this choice forces the authority off `localStorage`: that store is per origin, so one value there is shared by every workspace window in a browser profile and would be application-global, not workspace-global. The workspace record is the authority; `localStorage` may remain only as a non-authoritative cache.
6. **Stored groups larger than `max_sessions`:** preserve the stored data and refuse restore with an actionable “increase to N and retry” path. Do not silently truncate, mutate the global setting, or bypass the preference.
7. **Multiple browser windows controlling one live workspace:** treat one accepted group revision as authoritative and reject stale updates with compare-and-swap. Collaborative merge rules and ownership are outside this design.
8. **Explorer root on Save Session:** keep the current documented and tested behavior. Preserve the launcher's original directory contract while saving presentation; runtime Save Workspace must preserve the live `explorer_root_directory` exactly.
9. **Missing persisted file or diff target:** retain the current drop/fallback behavior rather than showing a retryable missing tab.
10. **Close surfaces using lifecycle choices:** the explicit browser close button and the native launcher's close button/X use the shared modal. Closing only a workspace window retains its current “hide window, leave sessions live” contract. Browser tab/window closure, Ctrl+C, task-manager kill, and power loss retain last-good behavior because they cannot reliably await a save.
11. **Unsaved SSH groups in “Save open sessions”:** create a uniquely named preset and encrypt the live password server-side without returning it to the browser. Updating an already attached preset preserves its existing credential rules. The modal states that session passwords are encrypted while workspace snapshots remain password-free.
12. **Partial combined-save success:** save presets first and workspace snapshots second. Keep GridVibe open on any failure, show per-group/workspace results, and keep the same three choices active; selecting the same save option tries it again, while Continue without saving current changes remains first. Do not promise atomic rollback across two files, delete successful unrelated writes, or terminate immediately after a failure.
13. **Workspace name uniqueness scope:** a workspace label identifies at most one workspace across *both* live workspaces and saved slots, compared case- and whitespace-insensitively. An empty label is exempt — it is the absence of a name, and two unnamed scratch workspaces must remain possible. A collision is refused with an actionable conflict that mutates nothing; GridVibe never auto-renames and never auto-forgets on the user's behalf, because both silently discard a decision only the user can make. Closing a workspace deliberately keeps its saved slot on offer, so the retained slot continues to own its name until the user forgets it.
14. **Scope of a per-workspace Save:** it is exactly in-window **Save Workspace**, invoked from the launcher. It captures one runtime slot, never writes reusable presets, never terminates anything, and requires a real flush of the owning window — an unreachable window is reported, not approximated from the last acknowledged server state.

## Audit verification

Every finding was re-verified against the source before the corrections above were folded in. The confirmations behind the amended text are: `web/static/js/browser-pane.js:215` (client-state regression on a late response), `web/runtime_state.py:312` calling the capacity-coupled `_normalize_workspace_layout` with the `originSlot` clamp at `web/saved_sessions.py:442`, `sessions/manager.py:620` coercing `list("abc")` without raising while `dict()`/`int()` raise, `web/secrets.py:17` and `:27` making the key race an import-time failure, and `web/static/js/explorer-viewer.js:4096`/`:4148` holding appearance in per-origin `localStorage`.

The findings revision changed only this Markdown document, and its gate results were:

- `python -m ruff check .`: passed.
- `python tests/run_tests.py`: 1,204 tests ran; 1,195 passed, 7 skipped, and 2 unrelated voice-environment tests failed because this environment does not have `websocket-client`/an available external Vosk service. No persistence, workspace, saved-session, explorer, browser, session-manager, or restore test failed.

Stage 0 then added `tests/test_session_persistence_contract.py` and touched no production file. Its own gate results are recorded in [Stage 0 results](#stage-0-results): ruff passed, and the suite reported `OK (skipped=7, expected failures=26)` across 1,231 tests.

Stages 0–4 were then re-verified against the source after Stage 4 landed. The gate results, guardrail audit, flakiness check, and the mapping of `docs/r&d/todos.txt` onto the stages are in [Stages 0–4 verification review](#stages-04-verification-review-2026-08-08). New line references confirmed for that review: `web/lifecycle.py:90`/`:97`/`:116`/`:190` (the window registry's only two removal paths, the disconnect that removes neither, and the pre-acknowledged stale error), `web/static/js/terminals.js:530` (a `window_id` regenerated per page load) and `:7979` (the `pagehide` that is the only clean removal), `web/api.py:771` (a non-`ok` flush becoming a `503` with no save), `web/workspaces.py:54` and `sessions/manager.py:341` (a label namespace with no owner; only ids collide), and `web/static/js/launcher.js:2916`/`:2931` (Open and Close as the complete live-workspace verb set). The review's own gates were `python -m ruff check .` passed and `python tests/run_tests.py` reporting `OK (skipped=7, expected failures=9)` across 1,292 tests, with the persistence suites run three times for stability. It changed only this Markdown document.

Stage 6's four remaining items were re-verified against the post-Stage-5 source before implementation; all four held, and the confirmations are listed in [Stage 6 results](#stage-6-results). New line references confirmed for that review: `web/runtime_state.py:267` (the key-only session allowlist), `sessions/manager.py:790`–`802` (the `list()`/`dict()`/`int()` coercions) and `:841` (the staging loop that dropped a pane and continued), and `web/workspaces.py:612` / `web/api.py:2468` (the two copies of the non-actionable capacity message). Its gates are recorded in the same section: ruff passed and the suite reported `OK (skipped=7)` across 1,328 tests with no expected failures left, the persistence suites run three times for stability.

The out-of-band top-bar change was then read end to end against this document, and the amendments above record only what the source now says. Line references updated for it: `web/static/js/launcher.js:2459` (update-triggered restart, unchanged) and `web/api.py:708` (browser shutdown, unchanged); the two SGP-11 bullets that pointed at the old `saveWorkspaceForRestart()` are struck through rather than renumbered, because the behavior they described no longer exists. Gate results for this revision: `python tests/run_tests.py` reported `OK (skipped=7, expected failures=26)` across 1,237 tests, and `tests/test_session_persistence_contract.py` alone reported `OK (expected failures=26)` — confirming the change consumed none of Stage 0's forcing functions.

## Final recommendation

Do not start by adding more fields directly to `runtime_state.py`. The durable store already records whatever the manager gives it. First make the manager the canonical acknowledged source for group presentation, make explicit save a flush barrier, and make the saved-session store durable. Then decouple stored shape from the mutable launch cap, put close/restart behind the shared lifecycle transaction, and only then expand the explorer schema through one normalizer and one synchronization path.

That order has the lowest blast radius: it fixes data authority and write safety before increasing the amount of state being persisted, keeps all slow work outside shared locks, avoids polling, preserves the server-owned restore model, and prevents another round of duplicate client/server persistence logic.

The out-of-band top-bar change does not alter that order. It is a well-built vertical slice through machinery Stage 5 will need anyway, and its one real cost is a third presentation writer that Stage 2 must absorb and Stage 3 must delete. The lesson worth carrying forward is the one it demonstrates rather than the debt it adds: a workspace-scoped UI field can be persisted correctly end to end today, provided the on-change writer is ordered and the explicit save reads the live value instead of the last acknowledged one.

Stages 0–4 have now shipped in that order and hold up under re-review, which is evidence for the ordering rather than merely for the code. One correction follows from the review and belongs in the recommendation itself: **Stage 4.5 item 1 should ship before Stage 5 or 6 begins.** It is not new scope competing with them for priority — it is a defect in Stage 4's own module that makes the save-on-exit transaction Stage 4 exists to provide permanently unavailable after one abnormally lost window. Shipping more persistence surface on top of a save path that can be silently disabled would be the same mistake this audit's ordering was designed to avoid. Items 2 and 3 of that stage are ordinary user-visible work and may be scheduled freely against Stage 5 and 6. *All three items have since shipped — see [Stage 4.5 results](#stage-45-results) — so this precondition is now satisfied.*

Two ordering rules carry the most weight and should not be relaxed for convenience. Capacity decoupling precedes any lifecycle action that mass-writes presets, because that action would otherwise turn a lowered preference into one-click preset truncation. And no stage may introduce a coercing normalizer, a silent stale-writer discard, or an emit without a consumer — each of those trades a visible defect for an invisible one, which is the failure mode this whole audit exists to remove.

All seven stages have now shipped in that order and every finding is closed — SGP-09, the last open one, with Stage 7. Stage 6 is worth one closing note, which Stage 7's own work bore out: the second of those two rules is what makes it a *stage* rather than a refactor. Its whole content is deleting three coercions and one `continue`, and each of those four lines was individually harmless — the damage was that together they made the outcome of a corrupt snapshot depend on which conversion happened to raise. The rule to carry forward is the one that follows from that: when a value is not what the contract says it is, the only safe answers are "reject it" and "fall back to a stated default", and which of the two applies must be decided by what the value *is* — launchable shape or window chrome — never by what the surrounding code happens to do when it fails.
