# Engineering Contracts

Maintained implementation contracts referenced by [CLAUDE.md](../CLAUDE.md), the
canonical agent instructions. Read the sections relevant to a change before
editing. Keep each rule here once; update its owning section when behavior changes.
Regression history and audit narratives do not belong in this reference.

## Contents

- [Security and trust](#security-and-trust)
- [Concurrency and resource ownership](#concurrency-and-resource-ownership)
- [Configuration and durable state](#configuration-and-durable-state)
- [Terminal transport and working directories](#terminal-transport-and-working-directories)
- [Pane transitions](#pane-transitions)
- [Explorer filesystem and transfers](#explorer-filesystem-and-transfers)
- [Processes and search bounds](#processes-and-search-bounds)
- [Git scope and actions](#git-scope-and-actions)
- [Git sidebar presentation](#git-sidebar-presentation)
- [Explorer rendering and scroll](#explorer-rendering-and-scroll)
- [Presentation persistence](#presentation-persistence)
- [Workspace lifecycle and windows](#workspace-lifecycle-and-windows)
- [Agent dashboard](#agent-dashboard)
- [Architecture and extraction boundaries](#architecture-and-extraction-boundaries)
- [UI and styling](#ui-and-styling)
- [Logging](#logging)
- [Launcher setup and voice](#launcher-setup-and-voice)

## Security and trust

- GridVibe is a local-use application: default bind `127.0.0.1`, no authentication
  or multi-user isolation. Preserve derived same-origin CORS defaults, the
  cross-origin write guard, and room-scoped Socket.IO emits. Explicit
  `security.cors_origins: ["*"]` disables the guard and must warn at startup.
- Explicit CLI host/port flags govern every derived address and origin. Use
  `resolve_server_settings()` and apply the resolved origins before serving;
  import-time config-derived origins alone are insufficient.
- Every paramiko client uses `web/hostkeys.py::_apply_host_key_policy()`.
  `auto-add` accepts and persists unknown keys to project-local `.known_hosts`;
  `known-hosts` additionally warns for each newly accepted key; both reject
  changed keys. `strict` uses `RejectPolicy`.
- Create a missing project trust file exclusively and load it before installing
  an acceptance policy. Unreadable or malformed existing trust refuses the
  connection and remains available for repair. Strict mode also refuses a failed
  system-store load.
- Saved SSH passwords remain Fernet-encrypted in `saved_sessions.json`. First-run
  `.encryption_key` creation is exclusive and atomic so concurrent processes
  converge on one complete key. Never put credentials into new state files,
  responses, logs, or browser storage. Flag any intended weakening explicitly.

## Concurrency and resource ownership

- Check-then-act and multi-field snapshots of shared state use one lock hold.
  Never emit, wait, perform network/disk I/O, or call native UI APIs while holding
  shared registry/manager locks. Snapshot first, release, then act.
- Lock order is `connection_lock` → `SessionManager.lock`, never the reverse.
  A terminal entry's ownership gate precedes `connection_lock`. Workspace label
  claims precede manager/runtime-state locks; never acquire the claim in reverse.
- Reserve a terminal connection entry before slow connect/spawn. Publication,
  readers, status/output/input tracking, and retirement must match that captured
  entry by identity. A late completion closes only its own resources. Keep
  unrelated panes progressing; close both untransferred POSIX descriptors on
  failure.
- Reserve a pooled SSH/SFTP entry and increment `in_use` in one lock hold, then
  open the channel outside the lock. Commit, rollback, and release match the entry
  object, not its session-id key, which a replacement may reuse.
- Deferred UI work captures pane object, session id, and relevant tab/path/Git
  scope. Recheck ownership before every post-await render, reload, or refresh.
  Update the captured pane's own state even when cached; resolve its current slot
  before painting. Release captured busy DOM nodes, never replacement nodes found
  by id. A grid index alone is never identity.
- A completed Git action on a replaced/cached pane marks its model stale without
  blanking it; restore performs the fresh load. A still-visible pane whose scope
  changed paints and loads its current scope immediately. Already-sent shell/Git
  mutations are neither cancelled nor reissued on a group switch.
- Browser disposal removes frame/load/popup listeners and interception. Search
  suspension cancels debounce/request and invalidates completions while retaining
  cached results; disposal clears them.
- Native minimize batches use both a re-entrancy flag and per-window state to
  absorb synchronous and delayed event echoes. Claim the flag under a lock,
  release it in `finally`, call native UI outside it, and skip already-minimized
  windows.

## Configuration and durable state

Read [session_state_guideline.md](session_state_guideline.md) before adding or
changing any field that survives restart; it owns the complete save/restore flow.

- Runtime settings come from `RuntimeConfig`, with local `config.json` overriding
  `default_config.json`. Every default key needs a live reader. New app settings
  also pass through `/api/app-config` normalization.
- An operation reading multiple settings captures `runtime_config.snapshot()`
  once and uses that immutable generation for decisions, work, logging, and
  response text. Publish `RuntimeConfigState` by one reference swap; do not add
  reader locks or re-read settings across a slow operation.
- `update_config()` holds in-process and cross-process locks across latest read,
  merge/normalization, validation, durable write, and runtime publication. App
  Settings and voice preferences both use it. Validate the root and every present
  known section as objects; a refused refresh/write preserves the published state
  and useful backup.
- All durable JSON stores use `web/state_files.py`: sidecar OS lock around the
  whole read-modify-replace, unique same-directory temp, fsync, atomic
  `os.replace`, last-good `.bak`, and corrupt/unsupported-data quarantine with
  validated backup recovery. This covers runtime state, saved sessions, config,
  and any future store. Invalid UTF-8/shape is corruption; a read `OSError` alone
  is not, and must not quarantine potentially valid bytes.
- Persistence failures raise the store's `StateFilePersistenceError` subclass;
  callers report a retryable failure, never claim a save succeeded. Runtime-state
  reads validate without rewriting. Commits retain per-workspace ticket/revision
  ordering; autosave and rename refresh never demote a manually saved snapshot.
- Workspace labels are case/whitespace-insensitively unique across live workspaces
  and saved slots. `_claim_workspace_label()` holds the process-local namespace
  lock across verdict and mutation; create, rename, launch-into-new and
  move-into-new use the canonical labelled-workspace services. Return actionable
  `409` on conflict. Empty labels are unconstrained; advisory validation must be
  rechecked at commit. Cross-process duplicate labels remain an accepted limit.

## Terminal transport and working directories

- Decode UTF-8 incrementally per connection, including cwd observation and final
  EOF residue; malformed bytes produce replacement characters. Never assume
  stream reads or writes are complete.
- Serialize input through a per-entry write lock with a five-second deadline.
  Continue partial SSH/POSIX writes from remaining bytes. Closed, retired,
  zero-progress, or timed-out writes fail without replaying the whole command;
  input tracking follows successful delivery only. WinPty keeps its string API.
- Replay buffers stay verbatim. `handle_join_session()` emits replay inside the
  handler, not a background task. Mouse recovery is a client `term.write()` of
  `MOUSE_REPORTING_RESET`, never shell input or replay sanitization. Clear also
  purges the server buffer; Reset view waits for replay acknowledgement or a
  bounded fallback and resets exactly once, on the captured pane. A live TUI may
  need to re-arm mouse reporting afterwards.
- Ask `effective_directory()` where a pane is: prompt observation, then
  `/proc/<pid>/cwd`, then an explicitly opted-in marker probe, then a directory
  fallback reported as an assumption. Never probe an agent's input box.
- Local prompt hooks arrive at spawn: cmd `PROMPT`, bash `PROMPT_COMMAND` (forwarded
  to WSL through `WSLENV`), PowerShell startup arguments wrapping the user's
  prompt. Only SSH receives a typed integration command. Disabling
  `terminal.shell_integration` stops installation, not parsing sequences emitted
  by the user's shell.
- `web/agent_activity.py` reads OSC 0/1/2 titles and OSC 9;4 progress from the
  same stream, with its own bounded residue and no stream filtering. OSC 1 is
  the preferred tab/chat title; OSC 0 replaces both title scopes. Title-only,
  CSI-only, and control-only output does not refresh the liveness timestamp.
  Both observers share the residue scanner in `web/osc_stream.py`; each owns its sequence heads
  and its ceiling. The reading lives on the pane's connection entry and is
  replaced, never edited, so a lock-free pump write and a locked snapshot read
  cannot meet a half-updated record. A retired entry's reading is unreachable
  and needs no ownership check. Observation runs on every chunk of every pane,
  agent or not, so it stays off the copy path: neither sequence pattern can
  match without its own two-character head, and the liveness test searches for
  the first visible character instead of substituting the chunk down to one.
- `web/terminal_cwd.py` parses OSC 7 / OSC 9;9 outside locks with bounded
  per-connection residue and never filters output. `_publish_observed_cwd()`
  checks exact registry-entry identity and writes metadata in the same
  `connection_lock` → manager-lock hold; broadcast follows release. Retargeting
  clears `current_directory`, and retired pumps must not republish it.
- Splits, reconnects, saves, and restores use the observed directory. Persist it
  in the snapshot's existing `directory` slot; promotion stamps
  `current_directory`, and reconnect uses shell `cd A || cd B` fallback instead of
  a precheck. `launch_directory` is the immutable, persisted origin/widen-guard
  floor; `directory` can move during mode transitions. That floor applies only
  while the shell remains inside it. Under a shared lock, read only already-known
  metadata, never run a probe.
- Quote for the target shell: `_powershell_single_quote` for PowerShell,
  `shlex.quote` for POSIX.

## Pane transitions

- `session_modes.py` and `session_shell.py` own transactions without Flask globals.
  Validate every fallible input and resolve directories before presentation
  cleanup, metadata mutation, teardown, or restart. Refusal leaves the whole pane,
  status, and connection unchanged. Routes supply late-resolved side effects.
- Shell family and agent are independent tri-state payload dimensions: omitted
  leaves that dimension alone; explicit `agent: ""` alone clears an agent. An
  arbitrary startup command is not an agent. Only a shell-family change retargets
  the directory; agent-only relaunch preserves the observed cwd. Reselecting the
  current choice is a no-op on both sides.
- Header shell rows state both family and agent; pressing a family row is its
  plain-shell relaunch. A separate adjacent chevron lazily expands agents. Windows
  Local Repo panes have families; SSH/POSIX panes get the flat agent list without
  a local-family choice. Use registry-backed `AGENT_OPTIONS` minus `other`.
  Expansion is temporary UI state, never persisted.
- A live terminal→explorer switch opens the response-only `explorer_open_path`
  relative to the newly resolved root, not an old root's saved tabs. Persist
  `explorer_root_directory` with `explorer_root_configured`, computed against the
  root actually stored. Derived roots must not become configured across restart;
  only configured roots retarget an outgoing explorer/browser terminal directory.
  Legacy unstated flags default to configured for explorer panes only.

## Explorer filesystem and transfers

Filesystem browsing is read-only by default. All operations remain root-confined;
mutations resolve parent then literal leaf and coordinate with editor saves using
cross-session ancestor-aware claims. Do not expand the following seven families
unless the task explicitly changes this contract.

| Family | Allowed operation and guards |
| --- | --- |
| Git | `POST .../git/{stage,stage-all,unstage,unstage-all,revert,discard-all,commit,publish}`; scope rules below. Discard requires confirmation and restores tracked files only, never `git clean`. Unstage-all is index-only and needs no confirmation. Checkout/pull/merge remain outside this surface. |
| Editor save | `PUT .../file`: existing complete strict-UTF-8 text, ≤10 MiB, one line-ending style; SHA-256 `base_revision` check, structured `409`, short-held claim, atomic replace; preserve BOM, line endings, permission bits. |
| Copy/delete | `POST .../{paste,delete}`: same live session/root, no overwrite; delete is revision-checked and confirmed in-page. |
| Create | `POST .../create`: one exact empty file/folder, exclusive create, validated leaf, no fallback name, `.git` protected. |
| Move | `POST .../move`: same root and leaf, no replace; reject collisions, self-nesting, `.git`, and cross-device moves; no copy+delete fallback. |
| Rename | `POST .../rename`: one exact validated leaf in its own parent, no replace or relocation; reject unchanged names, collisions, links, and anything under `.git`. |
| Upload | `POST .../upload`: one multipart file (`root_revision`, `destination_directory`, `name`, `file`), ≤100 MB, streamed and exclusively created; numbered collision names, never replacement. |

- No overwrite through upload/paste/move/rename or cross-session/root transfer.
  Local regular-file hard links and Windows rename retain exclusive behavior;
  POSIX directory/hard-link fallback renames use `web/rename_noreplace.py`
  (`renameat2`/`renamex_np`). Unsupported guarantees refuse; never use an `lstat`
  check followed by potentially overwriting `os.rename`.
- Upload checks `Content-Length` before touching `request.files` and enforces the
  ceiling during reads. Each extension-aware numbered candidate is allocated by
  exclusive create, bounded by `EXPLORER_COPY_NAME_ATTEMPTS`. Claim the candidate
  leaf, not the whole folder; skip busy candidates. Report `requested_name`,
  `stored_name`, and `renamed`. Failure removes its partial file and reports
  `mutated: false`; failed cleanup reports possibly-applied.
- Download is ≤100 MB, streamed in bounded chunks, with confinement/stat/cap
  checked before committing the response and the cap enforced again during read.
  Release generator-owned resources (including pooled SFTP channels) in `finally`,
  also on disconnect. Fetch status before claiming browser-download success.
- Inline images are recognized types only, ≤25 MB on stat and read, with sandboxed
  `Content-Security-Policy` and `X-Content-Type-Options: nosniff`.
- Markdown preview is a lazy `GET .../file/preview`, root-confined and ≤10 MiB;
  refuse non-Markdown. The file/save payload always states `preview_type`
  independently of fetched HTML. Source opens must not render/sanitize an unused
  preview. `GET .../find` searches names only; repository search may read content.
- `git/state` and `file/state` are bounded read polls. Share Git-state changes
  across sidebar, tree/listing refresh and Source marks. External file changes may
  refresh the viewer, never overwrite an editor buffer. `POST .../reveal` is a
  separate local-only, non-mutating OS-file-manager action.
- Multi-entry selection belongs to session id + root revision + one surface;
  changing any drops it. It never spans tree/listing or persists. Prune targets to
  topmost paths; rename stays single-entry. Batches issue N existing per-entry
  requests, never a new bulk endpoint or archive download.
- A batch has one busy hold, one confirmation, one deferred refresh and one
  outcome report. Partial failure retries only failed entries and only if every
  failure reported `mutated: false`.
- `explorer-upload.js` owns destinations: folder → itself, file → parent, blank
  space → the surface's displayed folder. Offer upload wherever download is
  offered plus the explorer-bar button. With no destination, withhold it; never
  silently fall back to root. Upload is not a selection action. Missing root
  revision asks for refresh; more than ten files requires one batch confirmation.
  Native picker/POST uses `select_upload_files` / `upload_file`, streams there too,
  and posts only to this application's upload route.

## Processes and search bounds

- Every subprocess is bounded; every Git invocation sets
  `GIT_TERMINAL_PROMPT=0`. Reuse `web/process_bounds.py` for process groups,
  tree termination, and bounded reap when helpers may inherit pipes. A reaped
  parent's PID must not be tree-killed. A timeout on the direct child alone is
  insufficient.
- Runner-owned stdout/stderr ceilings are defaults callers may only tighten.
  Bound reads, not the finished buffer. Keep observed exit status, completion,
  stdout truncation, stderr truncation, and output-limit termination distinct.
  Missing exit status and either ceiling never imply success. Remote drains bound
  the channel; `| head -c N` must not mask the command's exit status.
- Structural Git reads and mutations require complete results. An incomplete
  mutation is possibly applied, never automatically retried, and requires fresh
  Git state. Only explicitly partial search/diff responses may consume truncated
  stdout; truncated stderr or missing status remains failure.
- `SearchMatcher` uses a per-request isolated worker for arbitrary Python regex,
  with a deadline and bounded spans. Timeout/early termination releases worker
  and producer. Git/remote grep selects candidates; the shared Python matcher
  verifies case and whole-word semantics.
- Apply local include filters to confined relative paths before bounded body
  reads; growing files cannot evade limits. Drain remote stdout/stderr separately
  under byte/time ceilings; distinguish exact-limit output from overflow. Partial
  responses preserve complete records and state the bound/error.
- JSON ranges, snippet offsets and line lengths use UTF-16 code units; internal
  matching/windowing uses Python code points.
- Unified-diff headers exist outside hunks: inside a hunk, leading `-`/`+` is the
  row marker even for `---`/`+++` text. Reset at each `diff --git` boundary. Diff
  context is a server-allowlisted name from `GIT_DIFF_CONTEXT_WIDTHS`, never a
  client number; unknown names return `400`, Source marks request `zero`.
- Pool SSH, push over Socket.IO where appropriate, vendor pinned assets, and avoid
  full-buffer copies per chunk, sub-second polling, and busy-wait loops.

## Git scope and actions

- One selected scope governs graph, watcher and actions independently of filesystem
  navigation. A scope is root-relative `(path, kind)`, with server-allowlisted
  `kind=dir|file`; omitted kind means `dir`, invalid kind returns `400` unchanged.
  `_explorer_git_anchor_paths()` separates selected pathspec from repository
  discovery directory and confines both. A file scope must not widen to its parent.
- Pin freezes the browsed path/kind, even outside a worktree. Keep and name that
  pin with Clear pin instead of widening it. `''` is a real root pin; active flag,
  path and kind are saved/restored together, never inferred from truthiness or a
  later stat. Follow independently overrides the pin with the live browsed scope;
  turning it off returns to the fixed root/pin.
- Browsed scope is the open file or listed folder, optionally overridden by a
  single selected/context-menu row together with the derived scope at selection
  time. Navigation invalidates that override by changing the derived scope;
  repaint and emptied selection do not. Multiple selection clears it. This row
  override is never persisted. Pin and Follow read the same model.
- The pin button distinguishes here/elsewhere/none: binary `aria-pressed` means
  here, a separate class means elsewhere, and re-pin is one write. Clear pin is
  available whenever a pin exists, including at the pin and for missing paths.
  Repo bar shows pin first and Follow separately, labels overridden pin in words,
  and puts Clear pin only on its own path's row.
- Tree marks derive from exact path-and-kind equality, never ancestor/prefix
  matching. Root scope marks the Files header; off-tree scopes do not expand the
  tree. Pin and Follow may both mark a row. Paint both in one walk, preserving
  markup order and touching changed attributes only; never rebuild for a mark.
- Every browsing surface calls the shared scope-refresh path immediately, and
  asks `explorerGitScopeNeedsLoad(pane)` before loading. Unchanged scope costs no
  load or panel render. Follow-off navigation only repaints affordances. Judge a
  load against the scope requested, not the repository anchor the server resolved.
- Tree and Preview context menus use `explorerGitScopeMenuItem()` with identical
  path/kind/surface semantics; blank space names that surface's folder. Unpin or
  unfollow only at that exact scope; elsewhere the entry moves it in one write.
  Entries are single-entry only, never on commit rows, and disabled with a reason
  outside worktrees, checked through existing read-only `git/state`.
- All Git state/repo and mutation routes carry the selected scope; a single-file
  mutation's target remains a separate field. Repository identity in revision
  tokens is explorer-root-relative, never an absolute host path.
- Stage All and Unstage All use the selected pathspec (`git add --all -- ...`,
  `git reset --quiet HEAD -- ...`, or `git rm --cached -r --quiet -- ...` before
  the first commit). Discard All restores only tracked, non-conflicted worktree
  paths from a complete scoped status read; preserve staged/untracked content.
  Titles and scope rows disclose file/subdirectory scope for bulk actions.
- Commit is a normal staged commit, but a narrowed scope refuses if a complete
  staged-path read includes anything outside it. Publish remains branch-wide.
- Entering terminal mode clears pin and Follow. Restore preserves both; launcher
  preset re-rooting drops the pin only.

## Git sidebar presentation

- The five policy modules (`explorer-git-{active,menu,search,graph,pin}.js`) stay
  DOM-free and Node-tested. Search/active/card changes paint existing controls;
  structural graph expansion may rebuild rows. Defer watcher rebuild while focus
  is inside the panel and preserve commit-message caret on Alt-collapse.
- Active rows match diff identity, not path. History requires its commit, changes
  require their diff mode without a commit, and non-file views mark nothing.
  Reveal an open diff's commit once per pane/commit when loaded; later manual
  collapse is final.
- Commit find searches only the loaded, scoped graph with no new request. Subject
  search is case-insensitive; hash queries accept `[0-9a-f]{1,40}`, match
  `full_hash || hash` once per commit, and visibly mark/tint abbreviated hashes.
  Keep wraparound stepping, `current/total`, Enter/Shift+Enter and ↑/↓/×.
  Magnifier/Escape hides the existing bar and clears query while retaining its
  runtime mode. Neither query nor mode persists. Distinguish invalid hash from no
  loaded matches, state the bound, and put Show more by that explanation.
- Alt-clicking a commit/chevron collapses all expanded commits, never expands all.
  Show more reads `commit_limit`, `commit_page`, `commit_limit_max`, and
  `commit_has_more` from the server; never duplicate the ceiling. Distinguish an
  initial complete graph, expanded completion, and a capped history.
- Right-click alone opens one lazily built commit card; no hover card or per-row
  card markup. Use already-fetched author, authored date in its own ISO offset,
  full id and separate `%D` decoration; omit missing factual fields. A commit is
  never a filesystem selection/path/download target.
- `explorer-git-menu.js` owns card copy values: `%s` message and `%H` hash with
  displayed abbreviation fallback; never strip parentheses to derive a subject.
  Missing copy values disable controls; invalid `[0-9a-f]{7,40}` hashes never
  reach clipboard. Graph policy tags the hash copy slot instead of matching labels.
- Build the card on `document.body`, stamp the pane's theme, keep accessible row
  facts, and allow width beyond the sidebar with viewport horizontal clamping.
  Placement stays vertically within the Git panel; measure while visibility-hidden.
  Escape, outside press, copy, panel rebuild, containing-list scroll, group switch
  and pane release close it and remove its three document listeners. Judge
  staleness by the row; unrelated scroller movement must not close it.
- Keep repository/branch/scope sticky in the panel's existing scroller; Publish/Push
  scrolls in a separate section below. Stack find below the variable-height header
  through `--explorer-git-header-height`, published by one retargeted
  `ResizeObserver` per pane, with `0px` fallback. No post-render `offsetHeight`
  read while the panel may be hidden.

## Explorer rendering and scroll

- `explorer-repaint.js` owns skip/decorate/rebuild decisions. Rebuild Source rows
  only for document/language/fold changes; repaint changed code cells for marks
  or syntax color, using a document-scaled ceiling and one row walk. Decorations
  clear their own previous output. An unchanged in-flight build continues; changed
  marks supersede its pending slices.
- Stamp actual rendered surfaces, not just pane state. Source stamps must detect
  editor/tab replacement; Preview stamps cover path/HTML and reused content has
  search marks explicitly cleared. Diff stamps cover patch, diff identity, tier,
  wrap and undo capability. Placeholder/error/empty/fallback paths invalidate the
  stamp; an unparseable fallback is not stamped.
- Above roughly 4,000 rows, Source builds in token-guarded animation-frame slices,
  bounded by a per-frame chunk ceiling and roughly 8 ms. Replacement builds keep
  old content visible and swap a complete off-screen build; first paint may fill
  incrementally. Same-document rebuilds preserve scroll. Row-dependent work joins
  `whenExplorerSourceRendered()`, including scroll, find and editor selection.
- Cached-group detach suspends jobs and preserves queues/position; attach resumes.
  Surface replacement abandons the build and executes its waiting readers;
  whole-pane disposal cancels frames, drops readers unexecuted, and aborts every
  request/worker through `explorerReleasePaneWork()`. Never strand an in-flight
  marker or flush a disposed pane's callbacks into its replacement. Cache costly
  content-revision hashes by stable input identity.
- `explorer-tiers.js` owns thresholds as constants, not settings. Source above
  20,000 rendered rows or 4 MiB uses plain chunks bounded in lines and bytes over
  frames. Drop per-line highlighting/gutter/folds/occurrence/change marks/ruler and
  avoid unusable diff fetches; editor underlay also stands down above 2 MiB or the
  large Source tier. `sourceTierAllows()` drives both unavailable Find and its
  notice. Notices report reader-facing line counts, not internal trailing records.
- Diff small preserves normal behavior; medium drops word/intraline matching and
  syntax; large uses the handwritten side-by-side fallback, preserving per-line
  and per-block undo. Never substitute a plain `pre` or diff2html refusal knobs
  (`diffMaxChanges`/`diffMaxLineLength`) for a tier. Every removed capability is
  named in an in-pane `role="status"` notice, never the launcher banner.
- One lazy worker pool, capped at `min(max(hardwareConcurrency - 1, 1), 4)`, handles
  Source Highlight.js at/above 64 KiB (also editor settle) and large-Diff parsing.
  Small Source/small-medium Diff remain synchronous. Paint usable fallback colors
  first; workers import only same-origin modules and pinned vendored Highlight.js.
- Highlight results use a class dictionary plus typed offset/length/id/line arrays,
  with eager structure validation and lazy line materialization, never a cloned
  run-object Map or synchronous whole-result expansion. Superseding active work
  terminates its worker; recheck content/language/diff identity before paint and
  retain a usable fallback on failure.
- Editor underlay splices a bounded changed-row run with fallback colors; tokenize
  once after typing settles, never per frame or by interval. Settle repaints changed
  cells with the same scaled ceiling, falling back to bulk work when necessary.
  Compare against what is painted: unknown sentinels for spliced rows, shifted
  suffix keys, markup-sensitive keys (including Markdown heading level), no
  cross-answer numeric class ids. Ignoring absolute offsets is valid only for the
  search-free underlay. `HighlightLines.lineKey()` must not materialize runs;
  repaint re-derives find ranges. Draft line caches are pane-local, not the shared LRU.
- Capture the panel being hidden; restore only the panel shown once its content
  can hold the offset. Source/Preview/Diff revision validity is independent.
  Undetermined Diff revision preserves pending scroll only while known revisions
  match; a present mismatch invalidates it. Persisted v2 resolution remains
  `explorer-persistence.js`'s decision, with pending Diff arrival checked separately.
- Async panel arrival reapplies its own identity-guarded offset. Use bounded
  animation-frame readiness retries, cancelled by user scrolling. Prefer exact
  in-session offsets over fractions; `wasAtBottom` wins. `applyScrollMetrics()`
  is the shared implementation. Batch multiple scrollers as one read pass then
  one write pass and stop once applied.
- Never capture a loading/rebuilding panel's clamped zero over its stored offset.
  This includes Source jobs, Preview loading and Files-tree rebuild depth. Tree
  rebuilds restore their own point; hydrate/reveal uses `scroll: false` when a
  stored point exists and reveals on first show otherwise. Post-await slot work
  rechecks pane identity; release rebuild depth even when ownership changed.

## Presentation persistence

- `web/session_presentation.py` owns type-checking/bounds for live updates, presets
  and runtime reads. `explorer-persistence.js` owns v2 building, v1 migration and
  revision resolution. Keep one shared gate, not coercion or duplicated bounds.
- Whole-group and workspace presentation have separate ordered compare-and-swap
  revisions; stale writes return `409` with current revision and do not apply.
  Reject launch/credential/status fields. The client queue keeps one write in
  flight, coalesces latest state, applies a one-second floor during continuous
  changes, rebases conflicts, bounds repair and supplies the flush barrier.
- Persist durable tabs/mode/Diff/navigation intent separately from revision-bound
  per-panel scroll/folds. Never persist fetched content, search query/results or
  dirty buffers. Viewer find is runtime state of tab + path, reapplied on render
  with `scroll: false`. Workspace Markdown/source appearance follows the workspace
  revision; per-pane aliases migrate, `localStorage` is cache only. Re-bound theme
  cache keys to live panes on every write/grid build.
- Sidebar capture resolves the pane object, including cached groups without a
  slot. Explorer paths stay relative to their captured root; full restore may
  reopen saved directories, while live transitions obey the fresh open path.
  Root/configured flag and immutable `launch_directory` travel through launch,
  `to_dict()` and snapshot fields together; preserve local/SSH confinement.
- A wrong-typed pane invalidates its whole group, never silently drops a pane or
  coerces a value. Invalid window chrome/appearance degrades to defaults on stored
  read. Restore chooser and restore use the same validation gate.
- `MAX_STORED_SESSION_PANES` is the immutable schema ceiling (64).
  `runtime_config.max_sessions` applies only at launch/split through
  `capacity_refusal()`, which never rewrites a stored preset/snapshot.

## Workspace lifecycle and windows

- Manual/update restart, explicit browser close and native X use the shared
  lifecycle controller and `POST /api/lifecycle/prepare`. Choices are `none`,
  `workspaces`, `sessions+workspaces`. Account for every window, flush presentation,
  save reusable presets before one all-live runtime capture, report partial
  failures, and require the successful one-use decision for browser/native teardown.
  A failed requested save leaves the app open unless the user chooses no-save.
- Window ids stay in per-window `sessionStorage`; reload replaces its record.
  Registrations are bounded. Fresh disconnection is `client_stale`; past a bounded
  grace it is departed. A drop during flush resolves immediately; deliberate leave
  is forgotten. No dead registration may block saving forever.
- Resolve workspace chrome per field, oldest-joined first so the newest window
  wins. Stale `active_group_id` falls back to the server hint; malformed types or
  out-of-range zoom still raise. `topbar_visible` stores only the chevron choice
  (`topbar-collapsed`); fullscreen/peek use derived `topbar-hidden`/`topbar-peek`,
  never persistence or a terminal refit for transient reveal.
- Per-row `POST /api/workspaces/<id>/save` reuses flush-then-capture for that
  workspace only, leaves presets alone, and refuses when no window is reachable.
  Never capture stale server presentation as a successful explicit save.
- `POST /api/session-groups/<id>/save` saves one live group as a reusable preset
  for a surface that is not the window holding it. It shares the exit save's own
  builder (`_save_live_group_preset()`), flushes the owning window when one is
  connected, and — deliberately unlike the workspace save — does **not** refuse
  when none is: a session preset carries no window chrome, so with nothing open
  there is nothing newer to wait for. It captures no workspace slot, issues no
  teardown decision, and closes nothing; a partial result (preset written, group
  gone before it could be linked) is reported as one rather than rounded either
  way. An empty group is `409`, a missing one `404`.
- The three-outcome close prompt is `close-session-modal.js` over
  `partials/close_session_modal.html`, shared by the session tab and the
  dashboard's session card. One irreversible act gets one prompt: a second
  surface with its own copy is how two of them come to warn about differently
  sized consequences. A second open resolves the outgoing prompt to cancel. The
  prompt is skipped only for a group whose panes have *all* stopped — an empty
  status list means the lookup failed and the safe reading is to ask.
- Keep lifecycle credential snapshots server-only. Do not synchronously evaluate
  JS in pywebview's synchronous `closing` callback: cancel immediately and schedule
  the in-page prompt after returning.
- Browser startup requests a new default-browser window using only known flags
  (Chrome/Edge `--new-window`, Firefox `-new-window`), otherwise the stdlib fallback.
  Each workspace uses a named `window.open` to reuse its tab. Report refusal once
  per batch with `WORKSPACE_TAB_BLOCKED_HINT` and per-row Open; attempt every
  restored workspace even if earlier tabs were blocked. Pre-reserved blanks do
  not bypass browser gesture limits.
- Native Alt+X/button and opt-in, default-off minimize cascade share
  `minimize_all_windows()`. Minimize, never hide; taskbar restores individual
  windows and restore never cascades. Preserve maximized-window restoration.
  `gridVibeMinimizeAllAvailable()` controls button and settings visibility; browser
  saves omit invisible native settings. Page-specific shortcut guards exclude
  AltGr (`!event.ctrlKey`) and match `event.code`.

## Agent dashboard

- `GET /api/dashboard` is the only cross-workspace read: one pass composing
  every live workspace, its groups and their agent panes. Consumers must not fan
  out per-workspace requests to rebuild it.
- The payload is agent-scoped, and the filter is the server's. Only
  `startup_mode == "agent"` panes are composed; a group with no agent and a
  workspace with no such group are dropped, so "empty" means one thing on both
  sides. `pane["index"]` stays the pane's position in its *whole* group — it is
  what names and focuses the pane — so the filter is applied after `enumerate`,
  never before. Every count (`totals`, `agent_count`) is agent-scoped;
  `pane_count` on a group and `live_group_count` on a workspace are the only
  unfiltered numbers. A close confirmation states consequences, so it reads
  `live_group_count`: naming the agent-scoped count there would understate an
  irreversible act.
- `totals.working` is the button badge's number and is composed here, beside
  the rows, so the badge is a tally of the state dots in the list it labels
  rather than a second answer to the same question. A pane counts only when
  its `status` is `connected` **and** its activity reading is `working` — the
  same override `dashboardPaneStateKey()` makes on the row, so a dead shell's
  last frames and a pane that is still connecting are never counted. It is a
  field beside `totals.agents`, never a replacement: the dialog lists what
  exists, and the badge signals what wants looking at.
- `web/dashboard.py` composes; the route stays thin. `compose_dashboard()` is
  pure (dictionaries in, dictionary out, no manager, no clock). The gatherer
  snapshots activity under `connection_lock` and releases it *before* taking the
  manager lock — the two are never nested.
- Pane payloads are built from `PANE_FIELDS`, never filtered from
  `TerminalSession.to_dict()`, so a field added later cannot leak. No
  credentials, no explorer view state, no presentation fields.
- The payload states what a pane *is*, never what to call it. Both namings —
  the agent's display name and the transport tag — are
  `web/static/js/agent-identity.js`, shared by the pane header and the dashboard
  row; a second implementation server-side is what would let the two disagree.
  The transport tag reads `mode` plus the `use_wsl`/`use_powershell` precedence
  `paneShellKind()` already uses, so the tag and the relaunch menu agree.
- The dashboard conversation line is `paneChatLine()` in `agent-identity.js`,
  not the dashboard's own ladder: the agent's usable OSC tab/window title, then
  a non-generic pane title, then `New session` plus where the pane is. GridVibe
  cannot synthesize a conversation name an agent never publishes, and the
  fallback says so rather than substituting the next fact down — a directory
  read as a title the agent chose, and a pane with no directory yet repeated the
  agent's own name on a row that already states it.
- **What a pane announces is not automatically a conversation name.** Two peer
  rules reject a title before it can be read as one, both anchored to the whole
  title so prose survives. `isShellSelfTitle()` covers the shell talking about
  itself: ConPTY forwards a console-title change as OSC 0 and bash's stock `PS1`
  carries one, so an image path, a console label and `user@host: ~/dir` arrive
  in the field an agent publishes its chat title in. Its load-bearing clause is
  a comparison and not a pattern — a title that *is* the pane's own directory
  restates a fact the row already holds, whoever wrote it.
  `isOpaqueIdentifierTitle()` covers an id standing where a name should be:
  built-in Codex launches request `tui.terminal_title=['thread-title']` as a
  launch-only override (saved and custom command text stays unchanged), and an
  unnamed thread's title *is* its id, so a fresh Codex pane announces a bare
  UUID. It is a peer rule and not a clause in the first, because nothing about
  a thread id is a shell. Bare hex is rejected only from 24 characters, so a
  short commit id — prose a reader may well have titled a chat with — is left
  alone. `agentChatTitle()` still removes known provider-only labels and
  transient status marks.
- The line carries the pane's location as a **leaf** (`host:leaf` when remote),
  never an absolute path: it is one `nowrap` row with an ellipsis at its end, so
  a full path is clipped at exactly the segment that identifies the pane. The
  full path is on the row's hover, which is what makes shortening the line
  lossless. The hover is therefore `paneChatTooltip()`'s value on every write,
  including an in-place update, and it is compared separately from the line: a
  pane that only moved keeps its announced title, and `directory` is absent from
  the repaint's structure key, so the hover is the one thing on the row that
  says where the pane now is.
- A pane with no transport carries `activity: null`. "Nothing to observe" and
  "observed nothing yet" (`state: "unknown"`) are different answers.
- Liveness falls back to output cadence, because most agents publish no progress
  at all; a published progress state stops driving the reading once stale. The
  state names the input that decided it. Fresh OSC 9;4 error state is reported
  as an error, and stale progress does not paint a progress bar. A pane whose
  `status` is not `connected` reports the transport's word instead of an
  activity reading.
- A generic `Terminal N` title is treated as unset so an agent pane can name
  itself; a title the user typed always wins, and the display name is never
  persisted back as the pane's title. Every path that changes which agent a
  pane runs — the mode transitions and the shell/agent relaunch — repaints the
  pane header's name and agent glyph from the session it got back. Plain,
  explorer, and browser panes carry no agent glyph.
- The dashboard is a **dialog over the page that raised it**
  (`dashboard-dialog.js` over `partials/agent_dashboard_dialog.html`), not a
  window and not a panel. It has no route and no native window of its own:
  `/dashboard` is not served, nothing registers it for minimize or teardown,
  and `_should_exit_after_window_close()` grants it no exemption. Its root is
  the app's own `.modal-shell`, so both pages' scrim and blur already cover it
  and `EXPLORER_ESCAPE_CLAIM_SELECTOR` already claims Escape for it. Include
  the partial *before* the confirm dialogs on each page; at equal z-index the
  later element wins.
- It polls only while it is open. Opening arms the poll, reads once, publishes
  the exclusivity claim below and moves focus to the surface rather than to a
  control in it; closing disarms the poll, aborts what is in flight, and clears
  the action notice while leaving the read notice describing the tree still on
  screen.
- **There is one dialog across every window, and the claim that keeps it so is
  a notice, never a lock.** Nothing may refuse to open, or a window killed with
  its dialog up would leave the button dead everywhere else. An arriving claim
  is compared with this window's own: later wins, an exact tie breaks on window
  id, and only an open is ever broadcast. `BroadcastChannel` is the fast path
  and skips this document's own `source` (a channel does deliver to other
  channel objects in the same document); `localStorage` is the fallback. A page
  restored from the back/forward cache has missed every claim made while it was
  frozen, so it claims again rather than reading.
- Four dismissals, and leaving the window is one of them: the title-bar ×, the
  backdrop *alone*, Escape, and `blur` plus `visibilitychange` together, since
  neither of those covers every host and closing is idempotent. Escape is
  answered only while this is the top visible `.modal-shell`, so a close prompt
  raised over the dialog keeps its own key; nothing here calls `preventDefault`.
  Focus returns to the opener only when the dialog still holds it **and** this
  window still has focus.
- Both host pages carry the button, the dialog partial and the close-prompt
  partial, and `dashboard.js` wires all of it: it toggles the dialog through
  `toggleAgentDashboardDialog()`, binds `Alt+A` (matched on `event.code`, Ctrl
  excluded so AltGr cannot fire it, and gated by the page's own
  `minimizeAllShortcutBlocked`), and polls for the badge without overlapping
  requests. Badge and dialog reads have bounded deadlines, cancel on
  hide/pagehide, refresh on focus, reject malformed payloads, and discard
  answers superseded by a newer request.
- A row lands on the pane it names, not merely on the window. The workspace the
  reader is already in is applied directly through `applyWorkspaceFocusTarget`,
  because raising an already-raised window fires no `focus` event; every other
  workspace gets a one-shot, TTL-bounded `requestWorkspaceFocusTarget` stored
  *before* the window is asked for, claimed once by the window it names, never
  by the window that wrote it. A held target is settled again at the end of the
  load that produces its pane, and expires rather than waiting for a pane that
  is never coming.
- The dashboard's close verbs are the app's existing ones reached from here,
  never new questions: a session card's × opens the same three-outcome prompt
  the session tab's × does (`close-session-modal.js`), and the band's workspace
  verbs are `confirmCloseLiveWorkspace()` and the launcher's own close. *Save
  and close* saves first and closes only if that succeeded. Close window is
  **withheld** in browser mode rather than disabled, because `window.close()`
  from here would close the dashboard's own page. The in-flight guard is module
  state keyed by target, not a class on a button the poll may replace.
- The badge paints `totals.working` and validates that same field — a payload
  accepted on one count and painted from another reports `0` where it should
  report `?`. No working agent hides the badge rather than showing a zero: a
  badge that counts what is merely open is lit permanently and signals
  nothing, so its absence has to be a reading too.
- `dashboard-focus.js` owns a same-origin, short-lived focus lease, and which
  page owns it is no longer fixed for that page's life: every host can raise the
  dialog, so `setDashboardActive()` moves ownership and the page holds the lease
  only while its dialog is up. While it is, unfocused launcher/workspace
  documents add the content-only blur class; focusing a host clears its own blur
  immediately. Releasing **publishes** rather than letting the lease lapse, or
  every other window keeps its dim for the rest of the lease. BroadcastChannel
  is the fast path, localStorage is the fallback, and expiry remains the
  backstop against a page that died holding it.
- Dashboard layout must remain usable without horizontal overflow at narrow
  widths. A polling update that changes only a row's title, hover, status,
  progress, or idle age updates that row in place, each field on its own
  comparison; structural changes rebuild the tree while restoring scroll and
  focus. A failed read leaves the last good tree on screen behind a stated retry
  notice, and an action failure survives successful polls.

## Architecture and extraction boundaries

- Backend transactions belong in their canonical domain modules; `web/api.py`
  parses, delegates, maps status and dispatches. Shared local/remote explorer logic
  uses the existing backend; shared page JS and dialogs use their shared modules
  and `templates/partials/`. Do not re-grow monoliths or duplicate local/SSH policy.
- Substantial new frontend surfaces get the existing domain file or their own
  static JS file. Prefer DOM-free, Node-tested policy with thin DOM adapters;
  even a single predicate belongs in one module if multiple surfaces use it.
- Before the next substantial directory-list/Preview change, extract that domain
  from `explorer-viewer.js`. Existing tabs, Diff, Git sidebar and Files tree
  domains stay extracted. Pure moves preserve extracted lines and behavior;
  source-file references in tests may move, their behavioral assertions may not.
- At roughly 2,200 lines in `web/workspaces.py`, or when adding a fourth
  close/teardown path, extract the close-action matrix (launch/restore is the
  alternative cut). `test_multi_workspace.py` must pass unchanged for a pure move.
- Follow `session_modes.py` / `session_shell.py` for service extraction:
  characterize behavior first, inject side effects resolved inside the route to
  preserve patchability, prohibit cycling imports, test without Flask context,
  and retain route-size boundary checks.
- New pane kinds must reach every `startup_mode` resolver. Function-local imports
  are reserved for intra-app peers deferred for documented cycles/late binding;
  stdlib imports stay at the module header.
- Do not ship unused config keys, endpoints, callbacks, events or controls. Every
  emitted event needs a client listener or documented external consumer.

## UI and styling

- Never use `window.prompt`, `confirm`, or `alert`, including browser-only paths.
  Use `openGenericConfirmModal()` and shared in-page markup. Irreversible live
  closes need confirmation; failures need retry affordances; busy states toggle
  classes without rewriting button markup. Observe failure before reporting
  success and report batch outcomes once.
- Launcher global notices use only `showGridVibeNotice(text, type)` and
  `notice-banner.js`: one replaceable slot, no stack/queue/history or second sink.
  `#message` is static helper text. Error/warning persist; success/info dismiss
  after six seconds; dismissal is cosmetic. Use `textContent`, icon/border/tint,
  and `console.error` for errors only. Banner has no position/z-index; dialogs stay
  above it. Contextual validation must not become another global sink.
- Colors/radii come from `tokens.css` and existing theme tokens. Migrate literals
  in legacy CSS blocks being touched. Use stroke-style `currentColor` SVGs with
  explicit box/inline-flex centering; remove glyph font sizing and convert paired
  controls together. Find bars keep the shared ↑/↓/× exception; any conversion
  converts all find bars. Reuse selectors/tokens instead of copying declarations.
  Supplied artwork with a palette of its own — the app logo, the dashboard
  button's `active_ws.ico` — stays an `<img>` from `/docs/images/`; it is an
  identity, not a control glyph, and must not be converted to a stroke SVG.
- A session's hue is `session-colour.js` and an agent's mark is
  `agent-glyphs.js`, each DOM-free, Node-tested and read by both the workspace
  window and the dashboard. The palette's order is load-bearing — it is what the
  group-id hash indexes — so a hue is replaced in place and never reordered, and
  the hash itself never changes. A glyph is emitted with its registry key on the
  wrapper for the stylesheet to tint; an agent GridVibe has not drawn falls back
  to the shared terminal mark, never to nothing.
- `agent-dashboard.css` dresses one dialog on two pages and states no page's
  palette: no `color-scheme`, no `body` rule, no full-height frame. It reads the
  shared `--gv-dialog-*` and status tokens, so both legacy page palettes dress
  it without either learning it is there.
- Floating explorer surfaces use `--explorer-float-border` in all five explorer
  palette blocks. Body-mounted surfaces carry the pane's `data-explorer-theme`
  and corresponding selectors so opposite-theme panes stay consistent.
- Reserved chrome keeps its width while inactive. Source overview retains its
  track/separator when editor/empty/large views disable canvas, viewport, gestures
  and scrollbar semantics. Preview and Diff reserve the same lane from
  `--explorer-overview-ruler-width` on `.explorer-editor-body`. Reserve scrollbars
  with `scrollbar-gutter: stable`; use `hidden` only to remove a lane entirely.
- Operating controls such as find stay sticky within their searched surface;
  stacked sticky controls use the height the preceding variable box publishes,
  measured by `ResizeObserver`, not forced post-render layout.
- `session-menu.js` lives in the always-visible session tab line: one panel with
  exactly Sessions and Workspace rows, and flyouts beside them. Whole-row
  hover/press/focus opens idempotently; preserve existing focus without stealing
  it on hover. Root pointer-leave closes after grace; focus does not retain it.
  Every close cancels its timer. Expansion is temporary; fetch Workspace lists
  only when that section opens, and paint save-state changes without rebuilding.
  `setWorkspaceSaveMessage()` owns status and uses the workspace toast when the
  top bar is hidden. Topbar peek retains only pointer/focus.
- Shortcuts help is a read-only, unfiltered list shared by both pages, with mode
  labels and a separate Mouse group. No registry, editable bindings, config,
  capture UI, conflict checker or rewritten handlers. Chords remain per-handler,
  exclude AltGr, match `event.code`, and are labelled by what the layout prints
  (`event.key` for layout-dependent letters). Pin README's table to the array
  in both directions with `test_shortcuts_help.py`.
- The shortcut panel is lazy, non-modal, without backdrop/focus trap; return focus
  only if it still had it. Its root claims Escape through
  `EXPLORER_ESCAPE_CLAIM_SELECTOR`. Hidden, non-peeking topbar closes it. Share
  shape and seven `--sh-*` tokens, use each page's palette, and open upward on the
  launcher's bottom bar. Nothing about it is persisted.

## Logging

Follow [logging_guide.md](logging_guide.md).

- Routine teardown/window management logs at DEBUG. Keep ANSI sequences and
  high-frequency polling requests out of `logs/gridvibe.log`.
- Set noisy third-party logger levels explicitly in `setup_logging()`; preserve
  WARNING/ERROR and let `--debug` restore full output. Do not increase rotation
  budgets to compensate for library chatter.
- Lifecycle logging is shape-only: ids, revisions, counts and failure categories;
  never paths, commands, file contents, credentials or payloads.

## Launcher setup and voice

- `utils/launcher_setup.py` verifies requirement/interpreter/direct-package
  fingerprints and bounded imports. Core/desktop/voice markers are separate
  disposable `.venv` caches written only after successful verification. Unchanged
  warm launch runs no pip; changed/broken environments and root-launcher
  `--repair` run setup. Required failure stops launch, optional choices survive,
  and Quit/POSIX EOF is handled before setup.
- Voice is optional Vosk or faster-whisper; follow
  [voice_guideline.md](voice_guideline.md), including its dictation ceiling.
  Vosk audio and stop share the captured recording's I/O lock. Stop waits at most
  five seconds; refusal closes that WebSocket and reports cancellation without
  concurrent EOF/send/recv or success. Results/errors/removal match WebSocket
  identity so a restarted recording is unaffected.
