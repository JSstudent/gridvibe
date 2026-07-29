# Deep dive recap — guardrail check-up, 2026-07-29

Date: 2026-07-29
Status: Review complete — F1–F5 validated and fixed; F6–F8 remain proposed
Scope: every change since the `1.2.0` release tag (`5823ea0`), i.e. the `1.3.0`
release and the current Unreleased block, audited against the ten Regression
Guardrails in `CLAUDE.md`. This recap is also the pre-flight check for
`docs/r&d/multi_workspace_initial_plan_2026-07-28.md`.

Reviewed surfaces (commits `b1bc0b8..ff046f6`):

| Feature | Backend | Frontend |
|---|---|---|
| Repository-wide explorer search | `web/explorer_search.py`, `web/explorer.py` (`search_lines`), `web/config.py`, `web/api.py` | `web/static/js/explorer-search.js`, sidebar N-panel registry in `explorer-viewer.js` |
| Explorer copy / paste / delete | `web/explorer_fs.py`, claims in `web/explorer.py`, routes in `web/api.py` | `web/static/js/explorer-fs.js` |
| Tabbed browser panes + tab persistence | `/api/sessions/<id>/mode` tabs, `web/saved_sessions.py` normalizers, `sessions/manager.py` fields | `web/static/js/browser-pane.js` |
| Wrap toggles (Source/Preview/Diff) | `_normalize_explorer_tab_views` | `explorer-viewer.js`, `explorer-editor.js` |
| Active-group restore + native zoom | `POST /api/session-groups/active`, `web/runtime_state.py`, `web/webview_launcher.py` | `terminals.js` (`syncLocationToGroup`), `launcher.js`, `shared.js` |
| Gutter line-undo + tab/Git sync | (reuses `PUT /api/explorer/<id>/file`) | `explorer-viewer.js` (commit `ff046f6`) |
| 1.3.0 fixes (surface mode, app-config lock, voice deps, deps-bump) | `web/api.py`, `web/config.py`, `utils/bump_requirements.py` | `app-settings.js`, `shared.js` |

Verification state at review time: `python tests/run_tests.py` — **794 tests,
0 failures, 6 skipped**; `ruff check .` — clean.

Post-fix verification for F1/F2: `.venv\Scripts\python.exe tests\run_tests.py`
— **800 tests, 0 failures, 6 skipped**; `.venv\Scripts\python.exe -m ruff
check .` — clean; `git diff --check` — clean.

Post-fix verification for F3/F4/F5:
`.venv\Scripts\python.exe tests\run_tests.py` — **802 tests, 0 failures, 6
skipped**; `.venv\Scripts\python.exe -m ruff check .` — clean; `node --check`
— clean for all four changed JavaScript files; `git diff --check` — clean.

---

## 1. Guardrail scorecard

| # | Guardrail | Verdict |
|---|---|---|
| 1 | Security (same-origin defaults, host keys, secrets) | **Pass.** Paste/delete/save are POSTs/PUTs under the app-level cross-origin write guard; search/download stay GETs. No new origin, host-key, or secret handling was touched. Pre-existing ISSUE-2026-037 (restore credential exposure, High) remains open — see §4. |
| 2 | Concurrency (no emit under locks, single-hold check-then-act, lock ordering) | **Pass with one remaining note.** The new claim set (`web/explorer.py:560`) holds its mutex only around set membership and releases before any I/O. No new `socketio.emit` under any shared lock. F1 is fixed; the low-severity check-then-act note in F6 remains. |
| 3 | Performance (no per-request handshakes, polling, unbounded buffers) | **Pass.** SSH pooling reused everywhere; copies stream in 1 MiB chunks and now enforce each file's scanned byte budget while streaming (F3); the only `setInterval` is the pre-existing 15 s socket-down fallback. F2's `git grep` stdout is bounded, and repository search now has its own 350 ms debounce (F5). |
| 4 | Correctness (target-shell quoting, no `window.confirm`/`alert`, CLI flags beat config) | **Pass.** `build_remote_grep_command` and `_remote_git_shell_command` quote with `shlex.quote` for POSIX; no shell is involved in copy/delete. Zero `window.confirm/prompt/alert` anywhere in `web/static/js`; all new confirmations (delete, line-undo, discard) go through `openGenericConfirmModal`. |
| 5 | Dead code (everything wired end-to-end) | **Pass.** All five `explorer_search.*` config keys go through `RuntimeConfig` (`web/config.py:210,273`) and are read by `search_limits_from_config()`. The previously dead `ignored=1` parameter got its UI toggle. `DELETE /api/runtime-state` and `clear_workspace` are explicitly labeled multi-workspace skeleton — deliberate, documented, and consumed by tests. |
| 6 | Architecture / DRY (backend abstraction, own JS modules, pane-kind resolution) | **Pass.** New backend logic landed in the right modules (`explorer_fs.py`, `explorer_search.py`); new frontend surfaces got their own files (`browser-pane.js`, `explorer-fs.js`, `explorer-search.js`); local/SFTP share one policy through the backend `fs_*` primitives. Pane startup-mode resolution and mode-gated launch fields now live once in `shared.js`, with both saved-session launch paths calling the shared helpers (F4). |
| 7 | Styling (tokens, stroke-style SVG icons) | **Pass with a cosmetic note.** No hardcoded palette literals were added to `terminals.css` since 1.2.0 (verified by diff). New icons (search magnifier, revert/undo) are stroke-style `currentColor` SVG. A few new controls use text glyphs (F8). |
| 8 | Interaction (in-page confirm, retry affordance, busy via CSS classes) | **Pass.** Delete and line-undo confirm in-page with danger styling; the filesystem error bar distinguishes **Retry** (only when `mutated: false`) from **Refresh**; busy states toggle `explorer-fs-busy` / `is-busy` classes; search errors have a Retry button. |
| 9 | Logging (teardown at DEBUG, no ANSI, no high-frequency polling) | **Pass.** `explorer_fs.py` logs quarantine-unavailable at DEBUG and genuine cleanup failures at WARNING; the autosave daemon logs once at startup and only exceptions per tick. |
| 10 | New features on existing contracts | **Pass.** Tab/wrap/browser state rides the existing allowlist normalizers in `saved_sessions.py` across all three persistence paths; no new state file persists secrets; browser tab updates reuse `/api/sessions/<id>/mode` instead of a new route; the line-undo writes through the existing revision-guarded save route. |

---

## 2. New findings

Ordered by severity. F1–F5 were validated and fixed on 2026-07-29; F6–F8
remain proposals.

### F1 — Stale debounced browser-tab persist can flip a pane back to browser mode — Fixed

**Severity: Medium (race, user-visible).**
`browserPersistTabs()` (`web/static/js/browser-pane.js:159`) schedules a 400 ms
debounced `POST /api/sessions/<id>/mode` with `startup_mode: 'browser'`. Nothing
cancels that timer when the pane leaves browser mode, and the `push()` closure
re-validates nothing at fire time — it captures `pane` and `sessionId` and posts
unconditionally.

Failure sequence: reorder/close/navigate a tab (persist scheduled) → within
400 ms click the pane's mode toggle back to terminal
(`switchSessionBrowserMode`, `terminals.js:5469`) → the terminal mode switch
succeeds and `_connect_session` starts → the stale persist fires and the backend
(`web/api.py:1918`) flips the session back to browser mode, calls
`_close_ssh_connection(..., clear_buffer=True)` on the freshly started terminal,
and broadcasts CONNECTED-browser. The pane's UI and its session record now
disagree; the next rebuild or restore resurrects the browser pane.

The same fire-time blindness applies after a pane/group close (harmless 404) and
after a grid rebuild replaces `terminals[index]` (posts from a detached pane
object — state is stale but usually identical).

**Validation:** valid. The timer had no cancellation on mode change and the
callback had no fire-time identity/mode guard.

**Implemented solution:**

1. Added `browserCancelPendingPersist(sessionId)` in `browser-pane.js` and call
   it before browser-mode switches and confirmed pane/group closes.
2. The debounced callback now resolves the live session index and bails unless
   the same pane object is still present, is still in browser mode, and has no
   mode switch pending.
3. Added `SessionManager.update_browser_tab_strip()`, which checks and updates
   the browser strip under one `SessionManager.lock` hold. A tab-strip POST made
   after the pane has entered terminal mode now returns **409** and cannot
   re-enter browser mode.
4. Browser-only tab updates return directly after the atomic metadata update;
   they no longer run the mode-switch connection teardown path, so a concurrent
   terminal startup cannot be closed by a late tab update.
5. Added source-level cancellation/guard coverage and a behavioural
   browser → terminal → stale-tabs regression test in `tests/test_api.py`.

### F2 — `git grep` output is buffered unbounded on both backends — Fixed

**Severity: Medium-Low (memory robustness).**
The remote *plain-grep* fallback caps its stream at 4 MiB
(`SEARCH_REMOTE_MAX_OUTPUT_BYTES`, `web/explorer_search.py:334` — `| head -c`).
The primary `git grep` engine has no equivalent: locally
`subprocess` buffers the whole stdout (`_run_git_command`), and remotely
`_run_remote_git_command` (`web/explorer.py:1216`) does one unbounded
`stdout.read()` over SSH. `collect_search_payload` caps what is *kept*
(5 000 matches), but only after the full byte stream is already in memory. A
1-character query over the HTTP API (the 2-character minimum is client-side
only) on a large repo can produce tens to hundreds of MB inside the deadline.

**Validation:** valid. Match-count limits were applied only after each backend
had already buffered the complete `git grep` stdout.

**Implemented solution:**

1. Added an 8 MiB `SEARCH_GIT_MAX_OUTPUT_BYTES` cap used only by the primary
   search engine.
2. Local Git execution uses `subprocess.Popen` with concurrent stdout/stderr
   draining; it retains at most the cap, terminates Git when the cap is
   exceeded, preserves the existing timeout behaviour, and closes its pipes.
3. Remote Git execution accepts the same optional cap and appends the bounded
   `head -c` pipeline to the POSIX command.
4. A truncated final partial `git grep -z` record is discarded before parsing.
   The engine raises an internal output-limit signal after yielding complete
   records.
5. Search responses now include `truncated.output`; the explorer search footer
   reports “stopped at the output limit.”
6. Added local byte-cap, payload truncation, remote command-construction, and
   output-signal regression tests in `tests/test_explorer_search.py`.

The optional two-character HTTP minimum was not added: the byte cap now protects
the API independently of client validation, while retaining legitimate
single-character repository searches.

### F3 — Copy byte-cap is enforced at scan time, not during streaming — Fixed

**Severity: Low (cap bypass on a moving file).**
`paste_explorer_entry_payload` pre-scans the tree and enforces
`EXPLORER_COPY_MAX_BYTES` against the *scanned* sizes
(`web/explorer_fs.py:300`). `_copy_file_to_handle` (`:355`) then streams
`fs_read_chunks` with no ceiling — it re-checks that the source is still a file
but not its size. A file that grows between scan and copy (an actively appended
log; on Windows even the revision's mtime/size check passes for the *top-level*
entry only) streams past the cap; a file that grows continuously during the copy
streams for as long as it grows.

**Validation:** valid. The pre-scan bounded only the recorded sizes; the copy
loop neither counted streamed bytes nor compared the completed byte count with
the scan.

**Implemented solution:**

1. `_copy_file_to_handle` now treats the scanned file size as a hard streaming
   budget, counts each chunk before writing it, and raises
   `ExplorerFsEntryChangedError` if the next chunk would exceed that budget.
   Because the tree scan already caps the sum of those sizes, the same check
   preserves the global `EXPLORER_COPY_MAX_BYTES` limit without another I/O
   pass.
2. The completed byte count must also equal the scanned size, so a file that
   shrinks during the copy is rejected instead of producing a partial snapshot.
3. The existing `ExplorerRouteError` cleanup path removes the exclusively
   reserved destination and reports `mutated: false`.
4. Added a regression test in `tests/test_explorer_fs.py` whose patched
   `fs_read_chunks` yields more than the scanned size; it verifies the
   `entry_changed` failure and destination cleanup.

### F4 — The two saved-session payload builders are still parallel hand-copies — Fixed

**Severity: Medium as process debt (this exact duplication caused the shipped
browser-URL-as-command bug).**
At validation time, the Unreleased fix had taught *both* builders to resolve
`browser` — but they remained two structurally identical ~60-line
field-by-field blocks:
`buildSavedSessionLaunchPayload()` in `terminals.js:1430` and the launcher's
builder at `launcher.js:2520`. Every new pane kind or per-pane field must now be
added in two places (three counting the restore path's shared
`buildSessionsFromConfig()` helper on the launcher side), and guardrail 6's own
corollary notes a missed branch degrades silently.

**Validation:** valid. Both blocks independently resolved pane kind and gated
the same browser, agent, explorer, and local-shell fields. Workspace restore
reused the launcher block, so divergence there would also affect restored
presets.

**Implemented solution:**

1. Added `resolvePaneStartupMode(terminal)` to `shared.js`; it resolves
   `startup_mode` and `initial_command_mode` once for explorer, browser, agent,
   and terminal panes.
2. Added `buildPaneLaunchFields(terminal, startupMode)` to `shared.js`; it owns
   initial-command mode/nulling, `browser_*`, `agent_*`, `explorer_*`,
   `startup_mode`, and the mode-gated local shell flags.
3. Both `buildSavedSessionLaunchPayload()` and `buildSessionsFromConfig()` now
   call the shared helpers. Their genuine connection-specific differences —
   SSH credentials, WSL distribution/user, directory defaults, and session
   count handling — remain local.
4. Extended `ExtractedFrontendAssetsTestCase` to require the helpers exactly
   once in `shared.js`, forbid page-local definitions, require both call sites,
   and reject the old `savedStartupMode` implementation. Existing payload
   source-contract tests now follow the shared fields.

### F5 — Repo-search shares the 160 ms in-file debounce; aborted requests keep running server-side — Fixed

**Severity: Low (wasted bounded work).**
`scheduleExplorerRepoSearch` defaults to `EXPLORER_SEARCH_DEBOUNCE_MS = 160`
(`explorer-viewer.js:232`) — chosen for the in-memory in-file find. A
repository-wide search costs a subprocess or an SSH round trip; typing a
7-character word can fire several intermediate searches whose `fetch` is
aborted client-side but whose `git grep`/walk still runs to completion on the
backend, each remote one holding an SSH pool connection for up to the deadline.

**Validation:** valid. The repository scheduler's default referenced
`EXPLORER_SEARCH_DEBOUNCE_MS` from `explorer-viewer.js`, while aborting the
browser-side fetch did not cancel repository work already running inside the
Flask request.

**Implemented solution:** added
`EXPLORER_REPO_SEARCH_DEBOUNCE_MS = 350` in `explorer-search.js` and made it the
default for `scheduleExplorerRepoSearch`. The in-file find remains at 160 ms;
explicit option/scope toggles and seeded shortcut searches retain their
intentional `delay: 0`. The terminals-page repository-search contract test
locks in the dedicated constant and scheduler default. No server-side sequence
state was added: the longer debounce removes the common typing burst without
introducing new per-session concurrency state.

### F6 — `/mode` browser branch reads session state outside the manager lock

**Severity: Low (documented-pattern note, no realistic corruption).**
`change_session_mode`'s browser branch (`web/api.py:1936-1948`) reads
`session.initial_command`, `session.browser_tabs`, and
`session.browser_active_tab` from the dataclass, computes the merged strip, and
then writes it back via `update_session_metadata` — a check-then-act spanning
two lock holds (guardrail 2's letter). Two concurrent tab POSTs for the same
pane interleave as last-writer-wins on the whole strip; the client debounce
makes overlap rare, and the loser is a 400 ms-old strip, not corruption.

**Proposed solution.** Low priority. If touched (e.g. while implementing F1),
add a `SessionManager.merge_browser_tabs(session_id, tabs, active_tab)` that
reads and writes under one `self.lock` hold, and have the route call it. Note
`_normalize_browser_tabs` is pure — safe to call inside the lock.

### F7 — Search result cap can emit an empty trailing file group

**Severity: Cosmetic.**
In `collect_search_payload` (`web/explorer_search.py:418-434`), a new file entry
is appended *before* the `total_matches >= limits.max_matches` check, so when
the global cap lands exactly on the first match of a new file, the response
carries a final group with `match_count: 0` and no matches, which renders as a
"0 matches" foldable group. Reorder the cap check ahead of entry creation (or
pop the empty entry before breaking).

### F8 — Text glyphs on new controls

**Severity: Cosmetic (guardrail 7 letter).**
The search panel uses text glyphs — `Aa`, `ab`, `.*` toggles, `+`/`−`
expand/collapse, `▸`/`▾` fold arrows (`explorer-search.js:331-349,219`) — and
the browser tab strip uses `×`/`+` (`browser-pane.js:271,286`). `Aa`/`.*` are
industry-standard search mnemonics and `×` matches the existing explorer/session
tab strips, so full conversion is arguably over-correction; the fold triangles
and `+`/`−` pair are the ones that read as glyph-icons. Suggested disposition:
document `Aa`/`ab`/`.*`/`×` as accepted conventions in the guardrail wording,
and swap the fold/expand glyphs for the stroke-style chevron/plus SVGs that
`terminal-icons.js` already ships, whenever the panel is next touched.

---

## 3. Verified clean (what was checked and held up)

- **Claims machinery** (`web/explorer.py:526-596`): mutex held only around set
  membership; ancestor/descendant conflicts are namespace-aware
  (cross-session-same-root conflicts, cross-host non-conflicts); claims cover
  copy source + destination and delete target; the delete quarantine name is
  UUID-random so it cannot collide with a claimed path.
- **`explorer_fs.py` policy**: parent-then-leaf resolution never canonicalizes
  the leaf (deleted links are unlinked, never followed); `.git` protected at
  top level and inside scanned trees; root protected; revision re-checked
  inside the claim and again after the scan; every error carries an explicit
  `mutated` flag and the cleanup paths distinguish "removed" from
  "cleanup_incomplete".
- **Gutter line-undo** (`explorer-viewer.js`, `ff046f6`): gated on editable +
  complete + untruncated diff + no `\ No newline at end of file` ambiguity +
  clean-index rule for direct-tab HEAD diffs; verifies the expected line text
  against the in-memory content before writing; confirms in-page; re-validates
  path/revision after the `await`; writes through the revision-guarded
  `PUT /file` so a concurrent disk change 409s. LF-normalized content is safe:
  `_encode_explorer_edit` re-applies the file's stored line-ending style.
- **Browser pane nesting guard**: one level of self-preview renders; the level
  below renders `browser-frame-blocked` (and clears its spinner); the `+` tab
  deliberately opens the default URL rather than duplicating the active tab to
  avoid recursive self-embedding; named-window reuse prevents tab stacking.
- **Active-group hint**: event-driven (`syncLocationToGroup` is the single
  choke point, no polling), deduplicated client-side, validated at write
  (unknown id ignored), at capture (must name a stored group), and at read
  (re-checked against the slot's own groups; hand-edited file degrades to "no
  preference").
- **Native zoom bridge**: factor normalized/clamped in one shared helper
  (`normalize_native_zoom_factor`), applied on the WebView UI thread via
  `Invoke`, restore-time application deferred to the `loaded` event; autosave
  preserves the stored zoom when no window is available to ask.
- **Wrap flags**: opt-out-only persistence (`false` written, absence = wrapped)
  bounded server-side in `_normalize_explorer_line_wrap`; textarea uses
  `soft`/`off`, never `hard`.
- **1.3.0 app-config fix**: save/merge/refresh now under one `_config_lock`
  hold; the Socket.IO broadcast fires after release — matches guardrail 2
  exactly.
- **No `window.confirm/prompt/alert`**, **no CDN assets**, **no new palette
  literals in page CSS**, **no new polling loops** — all verified by sweep, not
  by changelog claim.

---

## 4. Multi-workspace readiness

The plan (`docs/r&d/multi_workspace_initial_plan_2026-07-28.md`) predates two
Unreleased features that landed on 2026-07-29. Its architecture findings are
still accurate (verified: `iter_live_workspaces` maps everything to
`"default"`; `capture_workspace` captures all live groups regardless of the
requested id; group ordering, events, and the restore banner are global), but
the plan needs three additions before implementation starts:

1. **The active-group hint is a new global that must become per-workspace.**
   `SessionManager._active_group_id` (`sessions/manager.py:151`) is one field
   for the whole process, and `POST /api/session-groups/active`
   (`web/api.py:1276`) carries no workspace identity. With two windows, each
   front-group report clobbers the other's hint, so every workspace's autosave
   records the *last window touched anywhere*. Plan delta: key the hint by
   workspace (`Dict[str, str]`), have the endpoint accept/derive
   `workspace_id`, and have `capture_workspace` ask for that workspace's hint —
   this slots naturally into the plan's `SessionGroup.workspace_id` step.
2. **Native zoom save/restore assumes the single session window.**
   `get_session_native_zoom()` and the zoom-restoring `open_session_window()`
   (`web/webview_launcher.py`) read/apply zoom on `self._session_window`, and
   `native_zoom_factor` lives per slot. The plan's window-dictionary refactor
   (`open_workspace_window(workspace_id, ...)`) must carry the zoom read/apply
   pair per workspace window, or first-window zoom will be applied to whichever
   window restores last.
3. **ISSUE-2026-037 stays a hard prerequisite** for the restore part of the
   plan (the plan already says so; re-confirmed still Open/High as of this
   review). Multi-workspace restore multiplies the client-side replay path —
   the server-side restore contract with credential redaction should land
   first or together with it.

The overlapping code prerequisites **F1** and **F4** are now complete before
the multi-workspace branch. The schema-side confirmation that
`runtime_state.json` v2 needs no version bump for per-workspace
`active_group_id`/zoom also still holds — both are already slot-scoped.

---

## 5. Work status and suggested remaining order

| Priority | Item | Status / size |
|---|---|---|
| Done | F1 — cancel/guard the debounced browser-tab persist | Completed (frontend guard + atomic backend stale-update rejection + tests) |
| Done | F2 — cap `git grep` output (remote `head -c`, local incremental read) | Completed (backend cap + truncation UI + tests) |
| Done | F3 — enforce the copy byte budget during streaming | Completed (scanned-size streaming budget + cleanup regression test) |
| Done | F4 — extract shared pane launch fields into `shared.js` | Completed (two shared helpers + both callers + lock-in tests) |
| Done | F5 — dedicated repo-search debounce constant | Completed (350 ms repo default; 160 ms in-file default retained) |
| 1 | F6/F7/F8 — opportunistic; fold into the next touch of each file | Trivial each |
| 2 | Multi-workspace plan deltas from §4 folded into the plan doc | Doc-only |

The remaining F6/F7/F8 findings are independent of the multi-workspace branch.
