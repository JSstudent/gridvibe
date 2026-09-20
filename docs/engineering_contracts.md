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
- [Agent tools (MCP)](#agent-tools-mcp)
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
- **One reading of a pane's path state, `web/pane_paths.py`, for every surface
  that saves one.** `capture_pane_paths()` answers the four facts — `directory`
  (the observation when the pane produced one, the recorded directory
  otherwise), the immutable `launch_directory`, `explorer_root_directory`, and
  `explorer_root_configured` — from one already-coherent pane snapshot, reading
  no process and no disk so it is safe under the runtime-state lock. The
  runtime snapshot, the exit/dashboard preset builder and saved-preset
  normalization all read it; a surface that decides for itself is how the same
  gesture came to save two different locations.
- **Saving a live pane saves where it is.** Updating an existing preset takes
  each live pane's directory and explorer root (`SAVED_PANE_PATH_FIELDS`);
  connection setup — the `ssh`/`wsl` blocks and the stored password — stays the
  base preset's. A payload stating no directory could not find out where its
  pane was and leaves the preset's own alone, which is a different outcome from
  a pane that moved. A preset never stores `launch_directory`: it is a template
  whose panes are built where it puts them, and only a restore is the same pane
  coming back.
- **A captured path is launched as it was captured.** `buildLaunchDirectory()`
  is the one rule both launch adapters read: a *relative* pane directory is a
  launcher input and joins the Step 2 default folder; an *absolute* one is an
  exact path and is launched verbatim, never rebased onto that folder and never
  refused for sitting outside it — a saved pane whose shell had walked out of
  the preset's folder was otherwise unlaunchable. Directory validation stays
  where it can be answered: the server checks the path exists and the explorer
  stays confined to its own root. A launcher row carries a hydrated explorer
  root back out of the form, dropped by the same edited-directory signal that
  drops its saved tabs and pin.
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
- Replay buffers keep every rendering and mode-setting sequence verbatim; only
  terminal *queries* are filtered out of them (`_TERMINAL_QUERY_RE`, below).
  `handle_join_session()` emits replay inside the handler, not a background
  task. Mouse recovery is a client `term.write()` of `MOUSE_REPORTING_RESET`,
  never shell input or replay sanitization. Clear also purges the server buffer;
  Reset view waits for replay acknowledgement or a bounded fallback and resets
  exactly once, on the captured pane. A live TUI may need to re-arm mouse
  reporting afterwards.
- **A terminal's answer is only correct in the instant it is produced, so an
  answer GridVibe is late in producing is not delivered at all.** A query —
  `\x1b]11;?` for the background colour, `\x1b[6n` for the cursor, DA,
  XTVERSION, DECRQM, the XTWINOPS *reports*, OSC 4/5/52, DCS `+q`/`$q` — travels
  the pane's output and is answered on its *input*, where the answer is
  indistinguishable from a keystroke. GridVibe answers late because it defers
  parsing: a pane that is not yet fitted holds output in `_pendingOutput` behind
  a debounce and a bounded fit-retry ladder, so a pane being restored, replaced
  or switched to parses its backlog long after the asker stopped reading and the
  reply is typed into whatever prompt is there by then.
  `web/static/js/terminal-replies.js` owns the rule, and the rule is about
  *age*, not identity: a backlog held longer than `STALE_DEFERRAL_MS` is
  stripped of its queries before the parser can see them and parsed inside a
  per-pane quiet window in which that pane's `onData` is refused. Stripping is
  the cure and is a known list; the quiet window is the structural backstop
  under it, so a query form the list has never heard of still cannot leak. A
  backlog inside the budget is written exactly as an undeferred one — which is
  what keeps an agent CLI on a promptly-fitted pane detecting the terminal's
  colours at all.
- The two owners of that list must agree. `TERMINAL_QUERY_SOURCES` filters the
  backlog the page writes late; `_TERMINAL_QUERY_RE` filters the buffer the
  server replays into a pane whose program has since changed. Neither may filter
  a sequence that renders or sets state — a rejoin to a pane whose TUI is still
  running has to restore that program's modes, and the title stack (`CSI 22/23
  t`) and DECSCUSR sit beside query shapes the list does match.
  `tests/test_terminal_replies.py` pins both sides to one fixture table.
- **The quiet window always closes.** It is a depth rather than a flag, so two
  overlapping late writes cannot reopen it early. It is released by the parse
  completion callback, by `QUIET_WRITE_TIMEOUT_MS` when that callback never
  arrives, by a throwing write, and on the call itself when no clock was
  supplied. A permanently mute pane is a far worse failure than one unsuppressed
  reply.
- **A sequence split across the deferral boundary is completed inside the same
  filter, never outside it.** A stale backlog can end mid-sequence, so its
  incomplete tail is held on the pane (`_terminalQueryResidue`) instead of being
  written, and the next output is prepended with it so the whole completion runs
  through the strip and the quiet window again (`writeFollowing()`). Otherwise a
  query straddling the boundary would be reassembled by xterm alone and answered
  after everything around it had been filtered. The residue is pane state and is
  dropped wherever that pane's stream is: Clear, Reset view, a `terminal_cleared`
  raised from outside the window, a reconnect, and a relaunch.
- **Input is forwarded to the pane that owns the xterm callback, never to the
  grid slot that pane occupied when the callback was registered.** `onData`
  closes over the pane object and the session id captured at wiring time, and
  `inputForwardPlan()` resolves both: the keystroke goes to that session, and
  Broadcast typing fans out from the pane's *current* index only while that pane
  still holds it under that session. A pane whose slot has changed hands types
  into its own session or into nothing — never into the session that took the
  slot.
- **A PTY is opened at the size the pane is already drawn at, never at a
  default it waits to be corrected from.** `session_terminal_sizes` records the
  last viewport a client reported, keyed by *session id* so it outlives the
  transport a relaunch discards, and every connector reads it through
  `_terminal_size_for()` before spawning — SSH `invoke_shell`, WinPty
  `dimensions`, POSIX `TIOCSWINSZ` on the master before `Popen`.
  `DEFAULT_TERMINAL_COLS`/`ROWS` apply only to a pane that has never reported
  one. Record on every reported resize, including when there is no connection to
  resize: that gap is exactly the relaunch window whose replacement the record
  decides. The record is dropped only with the session itself. A late correction
  is not equivalent — `_run_startup_sequence()` types an agent's command as soon
  as the shell is up, so the agent's first frame is drawn before any resize could
  arrive. On the client, `emitTerminalResize()` skips an unchanged size, so a
  pane reconnecting with its xterm attached must forget that memo and
  re-announce; a relaunch changes no dimension the page can see.
- Ask `effective_directory()` where a pane is: prompt observation, then
  `/proc/<pid>/cwd`, then an explicitly opted-in marker probe, then a directory
  fallback reported as an assumption. Never probe an agent's input box.
- **A startup command is not run in a directory the reader did not ask for.**
  `_run_startup_sequence()`'s `cd` carries the launch directory as a visible
  shell-level fallback, and an agent or startup command that then ran would be
  working on the wrong tree. `_unreachable_local_startup_directory()` answers
  only where this host's filesystem *is* the pane's — a local, non-WSL pane
  whose directory the sequence had to `cd` into — and the command is skipped
  with a notice naming the folder. A pane whose process was spawned in its
  directory (`launch_cwd_applied`) has already proved it exists and is not
  re-checked; SSH and WSL panes are left to the `cd`'s own fallback, because
  this host would be answering about another filesystem. Nothing is rewritten:
  the pane keeps the directory it recorded, so a save still stores what was
  asked for.
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
- **A pane stops being an agent pane when its shell draws a prompt.** An agent
  CLI owns the terminal while it runs and so emits no prompt hook; the pane's
  next prompt is the shell taking the terminal back, whatever ended the agent —
  an exit by any key, a crash, a kill, or a binary that was never installed.
  `_arm_agent_runtime()` starts watching at the one moment GridVibe knows an
  agent command was handed to the shell (runtime promotion, and the launch
  sequence's own startup command), and `_note_shell_prompt()` retires the pane
  through `_mark_runtime_agent_exited()` on the first prompt past the arming
  mark. It checks registry-entry identity exactly as `_publish_observed_cwd()`
  does, so a retiring pump cannot demote the relaunch that replaced it.
  Arming survives a swallowed prompt: a pane stays watched until it is retired.
- **The mark is only meaningful at a moment when nothing of GridVibe's own is
  in flight, and `_run_startup_sequence()` is not such a moment.** It runs
  *before* the pump, so the prompts its own `cd`/hook/marker lines draw are
  still in the transport when it returns — three commands' worth on a remote
  pane, and `_REMOTE_HOOK` emits twice — and a watch armed there retires a
  healthy agent as its own bootstrap arrives. It must never arm; it records
  `startup_finished_at` and nothing else.
- **There are exactly two honest arming moments.** A runtime promotion: the
  user was at a prompt to type the command, so the mark is exact — and it is
  taken *before* `effective_directory()`, which can wait out the bounded remote
  read, with the arming re-asking at once. And a launched pane's **first
  meaningful reader input** (`_arm_agent_runtime_on_input()`), by which time the
  bootstrap output is long read. xterm capability replies, focus reports and
  TUI mouse packets share the browser's `onData` callback with keystrokes, but
  become empty when terminal escape sequences are removed and must not arm the
  watch. That moment is not a compromise: ending an agent takes input, so the
  gesture that ends it is the one that arms the watch for it, and the prompt
  that follows is retired on that same gesture.
  `AGENT_RUNTIME_ARM_MIN_AGE_SECONDS` is a floor under *when arming may begin*,
  never a window in which a prompt is ignored, so failing it costs nothing —
  the next input arms instead.
- **A binary that is not installed is the preflight's answer, not the pane's.**
  It is knowable before the pane opens, and the pane's own answer is a prompt
  drawn before its output has been read. `_sanitize_agent_launch_commands()`
  therefore answers two questions: `check_failed` (the check could not run)
  clears the command, and `AGENT_PREFLIGHT_ABSENT_STATUSES` (the binary is not
  there) **keeps** the command so the reader still gets the real error in the
  terminal. Both clear the agent identity, as one unit. Restore skips this
  entirely.
- **Every path that starts an agent asks that one question, and it is one
  function.** `_agent_absent_reason()` is the registry probe reduced to the
  true/false a caller needs — the message when the binary is not there, `""`
  otherwise, reading the same `AGENT_PREFLIGHT_ABSENT_STATUSES` -- so the
  launcher row and the pane's relaunch menu cannot answer it differently. A
  second implementation of "is this agent here" is what would let them. What
  the two callers *do* with the answer differs, because what they hold
  differs: see the relaunch rule under [Pane
  transitions](#pane-transitions).
- **Where the prompt is observed, the keystroke heuristics stand down.** The
  double-Ctrl+C and `/exit` readings guess at the same question from what the
  user typed, and typing is not the same fact — two interrupts are how Codex
  quits *and* how a reader interrupts two turns. `_agent_runtime_is_observed()`
  requires both that the pane is armed and that it has actually drawn a prompt
  GridVibe read, because `terminal.shell_integration` is a kill switch and a
  remote shell may refuse the hook; a pane with no working hook keeps the
  guesses as its only answer.
- **Retiring a pane retires the title it announced as an agent, too.** Codex
  does not clear its own title on the way out and the inheriting shell says
  nothing, so `_mark_runtime_agent_exited()` raises `agent_title_floor` and
  `agent_activity_snapshot()` applies it through `mask_agent_titles()`. The
  record itself is never edited — the pump thread is its only writer — and a
  title the pane announces *after* the floor stands, so the next agent's first
  announcement replaces the mask rather than fighting it.
- Splits, reconnects, saves, and restores use the observed directory. Persist it
  in the snapshot's existing `directory` slot; promotion stamps
  `current_directory`, and reconnect uses shell `cd A || cd B` fallback instead of
  a precheck. `launch_directory` is the immutable, persisted record of where the
  pane was built, and a record is all it is: it clamps no root and gates no
  transition. `directory` moves with every mode and shell transition. Under a
  shared lock, read only already-known metadata, never run a probe.
- Quote for the target shell: `_powershell_single_quote` for PowerShell,
  `shlex.quote` for POSIX.

## Pane transitions

- `session_modes.py` and `session_shell.py` own transactions without Flask globals.
  Validate every fallible input and resolve directories before presentation
  cleanup, metadata mutation, teardown, or restart. Refusal leaves the whole pane,
  status, and connection unchanged. Routes supply late-resolved side effects.
- Shell family, agent and MCP are independent tri-state payload dimensions:
  omitted leaves that dimension alone; explicit `agent: ""` alone clears an
  agent. An arbitrary startup command is not an agent. Only a shell-family
  change retargets the directory; agent-only relaunch preserves the observed
  cwd. Reselecting the current choice is a no-op on both sides.
- **MCP is resolved last and cannot outlive its agent.** An unstated `mcp`
  follows the agent, which is the rule auto mode already has: carried forward
  when the agent is unchanged, dropped when it changes, because a mechanism
  registered for one CLI says nothing about the next. A stated `mcp` wins over
  that carry-forward, and is still `and`-ed with the resolved agent — a pane
  with no agent has no CLI to register the sidecar with. Both directions are
  available to an SSH pane: its tools arrive over a reverse forward on the
  transport its shell already runs on, so `pane_can_run_the_sidecar()` picks
  the *shape* of the answer, never whether there is one.
- Each of these transitions has a **gated twin** reached by a tool rather than
  by the pane header — `agent-relaunch`, `agent-mode-switch`, `clear` — and the
  rules those add are in [Agent tools (MCP)](#agent-tools-mcp). Everything in
  this section holds for both halves; the gates run before any of it.
- **A split with no stated `kind` is a plain terminal, and its metadata has to
  say so.** The pane kind is deliberately not cloned: an explorer, browser or
  agent source all split off a terminal rooted where the source is showing. The
  clone already clears the command, the agent selection and both agent flags, so
  an `agent` `startup_mode` carried across would leave a plain shell wearing an
  agent pane's metadata — and everything reading that field believes it: the
  dashboard would list an agent with no agent, the header would paint one, and
  the gated relaunch reads the same field to decide what a tool may do to the
  pane. Normalize it beside the explorer and browser cases; a stated `kind`
  replaces it, as it always did.
- **A stated agent is preflighted before anything moves, and an absent binary
  refuses the relaunch.** The launcher has no pane yet, so it opens one as a
  plain terminal; the menu's pane is already running, so the honest outcome is
  that nothing happens and the page says why — rather than a plain shell
  wearing the agent's name until its exit is observed. The probe describes the
  environment the row would launch *into* (the chevron's shell family and
  distro, or an SSH pane's own host/username/port), never the one the pane is
  in. It runs after validation and before any mutation, so the refusal is
  atomic like every other. `check_failed` is not an absence: the check did not
  run, so the relaunch proceeds and the reader gets the shell's own error. A
  stated `""` and an unstated agent probe nothing. The refusal message is the
  toast, so it names the agent, the target, and that the pane was left alone.
- Header rows state every dimension; pressing a family row is its plain-shell
  relaunch. A separate adjacent chevron lazily expands agents. Windows Local
  Repo panes have families; SSH/POSIX panes get the flat agent list without a
  local-family choice. Use registry-backed `AGENT_OPTIONS` minus `other`.
  Expansion is temporary UI state, never persisted.
- An agent row whose option publishes `mcp_supported` carries a second target
  in that same adjacent slot: the row starts it plainly, the button starts it
  with GridVibe tools, and exactly one of the pair wears the check. So a plain
  row is the documented way *back off* the tools, which is why every row states
  `mcp` rather than leaving it silent. The control is inline — the panel is
  anchored to the pane's own right edge and capped, so it grows away from the
  window edge and nothing opens sideways off a right-hand pane. An agent that
  publishes no mechanism gets no button, exactly as a pane with no shell family
  gets no chevron; the two surfaces read the one registry field, never a
  second rule client-side.
- **A pane is painted for a relaunch before the relaunch is requested, and no
  pane is left behind an overlay nothing removes.** The route starts the new
  transport while it is still writing its response — a local shell is marked
  connected inside that same request — so the connected `session_status` can
  reach the page first, and on an already-attached pane that event is the only
  thing that takes the overlay off. `relaunchSessionShell()` therefore raises
  the "Connecting…" overlay, and resets the pane's xterm, before its POST: a
  reset that waited would clear what the new shell had already drawn, and an
  overlay that waited would outlive the window. A request that then fails
  repaints the pane from its own session record (`syncPanePlaceholder()`)
  rather than leaving a spinner over a shell that is still running, under the
  same slot-ownership re-check as everything else done after an await. On the
  receiving side the status event is not the only remover: every group load and
  status refresh takes the overlay off a connected pane that is already
  attached, which is also what heals a pane that connected while its group was
  not the visible one.
- **A relaunch undoes the mouse reporting its own transition leaves armed, and
  only where the successor has no program to own it.** The pre-POST reset
  disarms the mode; the outgoing agent, still alive while the request is in
  flight, goes on redrawing and re-asserts `\x1b[?1003h` after it, so the plain
  shell that inherits the prompt collects every pointer movement and Enter
  submits the lot. `relaunchSessionShell()` therefore writes
  `MOUSE_REPORTING_RESET` once the response is in and the old shell is gone: a
  teardown draws nothing, so unlike a second `term.reset()` it cannot wipe what
  the new shell has already drawn — which is why the reset itself must stay in
  front of the request. It is owed to the pane and not to the slot, so the
  captured target flushes that pane's own queue first and follows it across a
  slot change. It is skipped when the relaunch starts a **new agent**, whose
  connector has already started and which owns its own mouse mode, and a
  *refused* relaunch writes none at all — that pane is still running the TUI
  that armed it. Until this, only the Reset view button undid the state
  GridVibe's own transition had created.
- **Every terminal/agent→Files switch derives a fresh root from where the pane
  is standing:** the Git worktree containing its working directory, else that
  directory itself (`_resolve_explorer_open_root()`, which takes those two
  inputs and nothing else). A root the pane was carrying and the directory it
  was built on are facts about the past and may not override it, so a pane
  launched on a parent of three repositories opens Files on the repository its
  shell walked into. Returning to the parent is a navigation the reader makes
  in the terminal — move up, reopen Files — never a pin the pane remembers for
  them. The resolved root is always stored, because the live explorer needs a
  confinement boundary, and always stored as `explorer_root_configured: False`.
- A live terminal→explorer switch opens the response-only `explorer_open_path`
  relative to that newly resolved root, not an old root's saved tabs. Persist
  `explorer_root_directory` with `explorer_root_configured`, computed against the
  root actually stored. Derived roots must not become configured across restart;
  only configured roots retarget an outgoing explorer/browser terminal directory
  and are inherited by a split clone. Legacy unstated flags default to configured
  for explorer panes only. A root restored from a snapshot or preset is replayed
  exactly and never re-derived — and never pins the next explicit switch either.

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
- `GET .../download` serves one file or, with `kind=directory`, one ZIP rooted at
  the requested directory. Both are root-confined and ≤100 MB. Files check stat
  before committing the response and enforce the cap again during bounded reads.
  Directories are planned and archived before committing the response: preserve
  empty directories; cap the plan at 10,000 entries; refuse links, special entries
  and unsafe archive names; cap both uncompressed bytes and the final archive; and
  re-resolve every queued directory/file through the backend immediately before
  listing/opening it. Stream the completed archive in the same bounded chunks.
  Release generator-owned resources (including pooled SFTP channels and temporary
  archives) in `finally`, also on disconnect. Fetch status before claiming
  browser-download success.
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
  requests, never a new bulk endpoint or multi-selection archive. The directory
  ZIP is a separate single-folder row action and is disabled for a multi-selection.
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
- `agent_sidebar_open` and `agent_sidebar_scale` are optional workspace chrome
  fields: omitting either leaves that dimension alone. The scale is an integer
  percent from 100 to 200, normalized in `web/session_presentation.py` beside
  `AGENT_SIDEBAR_SCALE_MIN/MAX`; booleans, strings, floats and out-of-range values
  are invalid live input. Stored reads default absent or invalid chrome to
  `False` and `100`. Both fields follow the workspace presentation transaction,
  live snapshot, explicit save, autosave, lifecycle flush/capture and restore.
  Which side the panel docks to is not among them: it is the global
  `workspace.agent_sidebar_side` setting (see [Agent dashboard](#agent-dashboard)),
  and the transaction refuses it as an unknown field.
- Persist durable tabs/mode/Diff/navigation intent separately from revision-bound
  per-panel scroll/folds. Never persist fetched content, search query/results or
  dirty buffers. Viewer find is runtime state of tab + path, reapplied on render
  with `scroll: false`. Workspace Markdown/source appearance follows the workspace
  revision; per-pane aliases migrate, `localStorage` is cache only. Re-bound theme
  cache keys to live panes on every write/grid build.
- Sidebar capture resolves the pane object, including cached groups without a
  slot. Explorer paths stay relative to their captured root; full restore may
  reopen saved directories, while live transitions obey the fresh open path.
  The root and its configured flag travel together through launch, `to_dict()`,
  snapshot fields and saved presets — never one without the other — while the
  immutable `launch_directory` travels through launch, `to_dict()` and the
  snapshot only. `directory` means the same thing in every mode: a pane rooted
  wider than the folder it is showing states both, and neither field carries
  the other's value. Preserve local/SSH confinement.
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
- A native window close is announced, never inferred. The page registers its
  window id through `register_workspace_lifecycle_window()`, and both close paths
  — the `close_workspace_window()` verb and the title-bar X in `_handle_closed` —
  retire that one record via `forget_window()` before the window is destroyed.
  The webview dies before `pagehide` can emit a leave, so an unannounced close
  reads as a crash: its `client_stale` record then fails every flush for that
  workspace until the grace expires, including the save made from the window that
  reopened it, which a new id cannot replace the way a reload replaces its own.
  Never retire a record whose workspace slot already holds a different window.
  The window-id ceiling has one owner (`LIFECYCLE_MAX_WINDOW_ID_LENGTH`); the
  bridge refuses an id past it rather than storing one that can match nothing.
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
  `partials/close_session_modal.html`, and it asks about two kinds: one session
  group, and one whole workspace. One irreversible act gets one prompt — a
  second surface with its own copy is how two of them come to warn about
  differently sized consequences — so the session tab, the dashboard's session
  card, the Workspace menu's Close Workspace, the dashboard band's Close
  workspace and the launcher's per-row Close all raise this one. The kind
  decides the title, the sentence, the note and the danger button's label, and
  all four are written on every open: one modal serves both, so a field left
  alone is the previous question still on screen. A second open resolves the
  outgoing prompt to cancel. The session prompt is skipped only for a group
  whose panes have *all* stopped — an empty status list means the lookup failed
  and the safe reading is to ask. The workspace prompt is skipped only at zero
  sessions, where the count is a field of the record rather than a lookup that
  may have failed.
- `confirmCloseLiveWorkspace()` resolves to a `CLOSE_SESSION_*` decision, never
  a boolean: `'cancel'` is truthy, so a caller testing it as one closes the
  workspace on every dismissal. What the decision does has one owner,
  `runWorkspaceCloseDecision()` in `workspaces.js` — the per-workspace save runs
  first and a failed save cancels the close, and it returns `{ ok, step, error }`
  instead of throwing so each surface can report *which* half failed on its own
  line. Do not repeat that ordering in a caller. `{ forget: true }` keeps the
  two-outcome generic confirm, because it removes the snapshot a save would
  have just written.
- Keep lifecycle credential snapshots server-only. Do not synchronously evaluate
  JS in pywebview's synchronous `closing` callback: cancel immediately and schedule
  the in-page prompt after returning.
- Browser startup requests a new default-browser window using only known flags
  (Chrome/Edge `--new-window`, Firefox `-new-window`), otherwise the stdlib fallback.
  Each workspace uses a named `window.open` to reuse its tab. Report refusal once
  per batch with `WORKSPACE_TAB_BLOCKED_HINT` and per-row Open; attempt every
  restored workspace even if earlier tabs were blocked. Pre-reserved blanks do
  not bypass browser gesture limits.
- The workspace a launcher is opened from is recorded once per handover
  (`gridvibe.launcherOrigin`: id + timestamp) and answers two questions — Alt+W's
  way back, and what the next launch targets. Resolve it against the live list
  through `isUserVisibleWorkspace` for both: a record with no window is neither.
  The destination claims it by timestamp (newer than the last adopted, and within
  `WORKSPACE_LAUNCHER_HANDOVER_TTL_MS`), so a destination picked after arriving
  stands and a record left by an earlier run steers nothing. Never consume the
  record — the way back needs it for as long as the launcher stays open. Claim on
  every destination refresh, including the focus/visibility arrival that is all a
  handover into an already-open launcher window amounts to.
- `open_launcher_window(workspace_id)` places the native launcher on the screen
  holding that workspace's window before showing it, and after the restore when it
  was minimized. `plan_window_placement()` is the one rule: a window already on
  that monitor is never moved, otherwise centre it on the anchor and clamp it into
  the work area. Read and write geometry in one coordinate space — Win32 physical
  pixels on Windows, pywebview logical ones elsewhere — and restore a maximized
  window before moving it, then maximize it again. Placement is a courtesy on top
  of focus: an unusable id, a workspace with no window, or geometry that cannot be
  read costs the focus call nothing, and browser mode places nothing.
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
- The payload carries **every live session, agents first**. The surface is the
  agent list *and* the only place every workspace and session is named at once,
  so it is also how a reader reaches one — which makes dropping a session the
  removal of its only route from here. The two jobs are reconciled in the
  order, never by omission: `agents_first()` puts the rows holding an agent
  ahead of the rows holding none at both levels (a workspace and a group both
  carry `agent_count`, so it is one function), and each half keeps the order
  its own window would use. A workspace holding no session at all is still
  absent, because `list_live_workspaces` already decided that is not a live
  workspace.
- **Panes stay agent-only, and the filter is the server's.** Only
  `startup_mode == "agent"` panes are composed, so the page and the payload
  cannot disagree about what an agent is, and a plain terminal is never a row —
  it is already visible in the window that holds it. `pane["index"]` stays the
  pane's position in its *whole* group — it is what names and focuses the pane
  — so the filter is applied after `enumerate`, never before. A group's
  `agent_count` is what says a card has none; the page must not infer that from
  an empty `panes` list, because a card that carries no count at all is a
  different case. `totals.agents` and `totals.working` are agent-scoped;
  `totals.workspaces`, `totals.sessions`, a workspace's `group_count` and a
  group's `pane_count` count what is listed. `group_count` is therefore also
  what a close confirmation states, and it is read off the payload rather than
  off the rendered cards: a payload and a painted tree are two different
  moments.
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
- **Whether a pane's agent has GridVibe tools is stated where the pane is
  named**, by `paneAgentMcpTag()` in the same module, so the pane header and the
  dashboard row cannot disagree about it. Its rule is `agent_mcp` **and** an
  agent pane, mirroring `paneAgentMcp()` in `terminal-shell.js`: the flag
  outlives the agent that justified it in presets and snapshots, and a plain
  shell must not wear a chip for tools nothing is holding. The transport is not
  part of the rule — a remote pane's tools arrive over the reverse forward on
  its own SSH transport, so `pane_can_run_the_sidecar()` picks the *shape* of
  the answer (local config file against tunnelled URL) and never whether there
  is one. The tag is its own value and is never folded into `paneDisplayTitle()`
  or `paneChatLine()`: a title is also what the reader typed, and a chip
  concatenated into one would be indistinguishable from a name and would reach
  the typed-title comparison as though somebody had chosen it. `agent_mcp`
  therefore stays in `PANE_FIELDS` and in the repaint's structure key, so a
  relaunch on or off the tools rebuilds the row and an unchanged poll does not.
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
- **A row leaves this surface when its pane stops running an agent**, and that
  is decided in one place for both surfaces: the pane's own metadata. See the
  prompt-hook retirement rule under [Terminal transport and working
  directories](#terminal-transport-and-working-directories) — a dashboard that
  went on listening for its own signal would be a second definition of "is this
  still an agent". A preflight that clears a pane's agent command clears its
  agent identity with it, for the same reason: a plain shell that is still
  *called* Codex is a row that would never do anything.
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
- The dashboard opened by `Alt+A` or the dashboard button is a **dialog over
  the page that raised it**
  (`dashboard-dialog.js` over `partials/agent_dashboard_dialog.html`), not a
  separate window. It has no route and no native window of its own:
  `/dashboard` is not served, nothing registers it for minimize or teardown,
  and `_should_exit_after_window_close()` grants it no exemption. Its root is
  the app's own `.modal-shell`, so both pages' scrim and blur already cover it
  and `EXPLORER_ESCAPE_CLAIM_SELECTOR` already claims Escape for it. Include
  the partial *before* the confirm dialogs on each page; at equal z-index the
  later element wins.
- The dialog polls only while it is open **and its document is visible**.
  Opening arms the poll, reads once, publishes the exclusivity claim below and
  moves focus to the surface rather than to a control in it; closing disarms the
  poll, aborts what is in flight, and clears the action notice while leaving the
  read notice describing the tree still on screen. The dialog outlives the
  reader leaving the window, so open does not imply being looked at: hiding the
  document disarms and aborts without dismissing, and becoming visible again
  re-arms and reads once. Losing the focus is neither — an unfocused window on
  another monitor keeps reading.
- **There is one dialog across every window, and the claim that keeps it so is
  a notice, never a lock.** Nothing may refuse to open, or a window killed with
  its dialog up would leave the button dead everywhere else. An arriving claim
  is compared with this window's own: later wins, an exact tie breaks on window
  id, and only an open is ever broadcast. `BroadcastChannel` is the fast path
  and skips this document's own `source` (a channel does deliver to other
  channel objects in the same document); `localStorage` is the fallback. A page
  restored from the back/forward cache has missed every claim made while it was
  frozen, so it claims again rather than reading.
- Three dismissals, each said on the window the dialog is on: the title-bar ×,
  the backdrop *alone*, and Escape. Escape is answered only while this is the
  top visible `.modal-shell`, so a close prompt raised over the dialog keeps its
  own key; nothing here calls `preventDefault`. Focus returns to the opener only
  when the dialog still holds it **and** this window still has focus — the close
  that arrives from outside is the exclusivity claim, and it must not pull a
  deactivated window back in front of the one the reader chose.
- **Leaving the window dismisses nothing.** `blur` closes nothing and a hidden
  document suspends the poll instead; the dialog stays up on the window it was
  raised from so it can be read on one screen while the reader works on
  another. The exclusivity claim is therefore the mechanism rather than a
  backstop: raising the dialog in another window is the only thing that puts
  this one away without the reader touching it.
- Both host pages carry the button, the dialog partial and the close-prompt
  partial, and `dashboard.js` wires all of it: it toggles the dialog through
  `toggleAgentDashboardDialog()`, binds `Alt+A` (matched on `event.code`, Ctrl
  excluded so AltGr cannot fire it, and gated by the page's own
  `minimizeAllShortcutBlocked`), and polls for the badge without overlapping
  requests. Badge and dialog reads have bounded deadlines, cancel on
  hide/pagehide, reject malformed payloads, and discard answers superseded by a
  newer request. Each reads once when its page comes back — the badge on
  `focus`, the dialog on becoming visible.
- The workspace also carries a docked dashboard: `dashboard-sidebar.js` over
  `partials/agent_dashboard_sidebar.html`, beside the grid in `.workspace-body`.
  Its handle heads the session tab line. It is workspace chrome, with no scrim,
  no Escape dismissal and no exclusivity claim — several windows may show it at
  once, and each remembers it per workspace. It reads the same dashboard payload
  and uses the dialog's field renderers and target resolver. The narrow row
  draws the dot, agent mark and chat title; the agent name remains in the
  accessible text. Polling runs every four seconds only while open and the
  document is visible.
  Unchanged markup is skipped; a rebuild preserves scroll and focus. Read
  failures retain the last good tree, and successful polls leave action notices
  intact.
- **Which edge the sidebar docks to is the global `workspace.agent_sidebar_side`
  setting, never a workspace's.** It follows `surface_mode`'s rules, not the
  panel's own: `_normalize_agent_sidebar_side()` in `web/config.py` accepts
  `left`/`right` and falls back to the captured generation's value, every
  `/api/sessions` shape and `/api/app-config` report the *current* value so a
  window that missed the broadcast reconciles on its next read, and a save
  broadcasts it beside the surface mode. No workspace presentation transaction,
  runtime-state slot or saved preset carries it — the presentation route refuses
  the field — so `agent_sidebar_open` and `agent_sidebar_scale` restore
  unchanged onto whichever edge the setting names. The page applies it as the
  one body class `agent-sidebar-right`; the stylesheet orders the panel past the
  grid (never `row-reverse`, which would also swap the empty state sharing that
  row) and flips the frame border and the resizer's end. Markup, the single
  toggle and its two marks, the open/shut state and the width are identical on
  both sides.
- Sidebar width is `calc(var(--agent-sidebar-width) *
  var(--agent-sidebar-scale, 1))`, with the base owned solely by the CSS clamp
  `clamp(240px, 15%, 400px)`. Dragging measures the rendered border-box width
  divided by its current scale, never restating the clamp in JavaScript. The
  edge button captures the pointer and listens for move/up/cancel on the window;
  each move writes a scale clamped to 100..200. The drag captures its
  direction from the side at the press, so the handle always widens away from
  the grid; a side change under the pointer abandons the drag rather than
  finishing it against the other edge. Release reports the changed
  integer percent and calls `onLayoutChanged` once; live pane ResizeObservers
  handle the drag without a second explicit refit on every move. Cancellation
  restores the starting width without reporting. `apply()` also refits when a
  restored width changes. The sidebar stylesheet owns the 22px session × and
  a separate row for workspace word buttons beneath the band heading; shared
  `agent-dashboard.css` rules and palette tokens remain the styling owners.
- Acting closes the dialog only when the act lands on this page: a row for the
  workspace this window already is takes the dialog with it, because a surface
  over the pane it just reached is in the way. A row for any other workspace
  leaves it up, and so does a refusal, which is reported on it. The close verbs
  never close it.
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
  the session tab's × does (`close-session-modal.js`), and the band's **Close
  workspace** opens that same prompt through `confirmCloseLiveWorkspace()`.
  *Save and close* saves first and closes only if that succeeded — for a
  session that ordering is this controller's, because only this window can ask
  for the preset; for a workspace it is `runWorkspaceCloseDecision()`'s, shared
  with the launcher's card and the in-window menu, so this controller hands the
  decision over and reports which half failed rather than keeping a third copy
  of the rule. Close window is **withheld** in browser mode rather than
  disabled, because `window.close()` from here would close the dashboard's own
  page. The in-flight guard is module
  state keyed by target, not a class on a button the poll may replace. The
  dialog and sidebar share one `GridVibeDashboardClose` controller instance;
  it claims the guard before reading status or opening a prompt and releases
  it on cancellation, failure or completion. `run(action, target, element,
  { notice, refresh })` accepts per-call reporting hooks, defaulting to the
  dialog's hooks; sidebar calls supply its own notice and refresh. Hooks are
  local to the call, never reassigned on the controller. Closing a target
  leaves the dashboard surface open. Both surfaces invalidate their rendered
  caches and refresh on `pywebviewready` so the native window verb can appear
  after the first paint.
- The badge paints `totals.working` and validates that same field — a payload
  accepted on one count and painted from another reports `0` where it should
  report `?`. No working agent hides the badge rather than showing a zero: a
  badge that counts what is merely open is lit permanently and signals
  nothing, so its absence has to be a reading too.
- The dialog's blur is the host page's own `.modal-shell` scrim and reaches no
  further than that page. No cross-window dim: the short focus lease that
  blurred every *other* GridVibe document while the dialog was up is removed,
  because a dialog the reader keeps up to work elsewhere must not dim the window
  they are working in. Nothing in this feature may write a blur class onto
  another page's `body`.
- A row holding nothing draws a statement, never rows it does not have. A
  session card with no agent wears `is-quiet`, states its own pane count rather
  than "0 agents", and carries one muted line where its rows would be; a band
  with no session carries the same line where its cards would be. Quiet keeps
  the full width, the heading control, the session hue and every close verb —
  the card exists so the reader can go there — and gives up only the weight
  that was drawing the eye to the agents, so it cannot read as disabled. An
  empty tree means nothing is running at all, not that nothing agentic is.
- An agent row's state is its **leading** column and one 8px dot: the card is
  scanned for "is anything still going", and a colour answers that before any
  column after it is read. The word the dot replaced is not drawn, is carried
  verbatim on the indicator's own hover, and stays in the markup out of flow
  so the row's accessible name still states it. The progress bar is the
  trailing column and a separate reading: only the agents that speak the
  progress sequence have one, so it must never widen the dot's column.
- The drawn row is therefore the dot, the agent's mark and name, the chat
  title, `MCP` and `auto` — the two chips left on it, and both say what this
  agent may *do*, which is what a reader choosing a row to instruct is deciding
  between. What the pane runs *on* is still `paneTransportLabel()`'s single
  word, and the dashboard states it as the last line of the row's own hover
  rather than as a chip on the line: it is looked up when something is wrong
  with a pane, not scanned down a card, and the width belongs to the title.
  A chip may carry a hover of its own for a label the reader may not recognise;
  one whose label is already the word carries none.
- The docked sidebar's row drops the agent's *name* out of flow because its mark
  already answers which agent it is, and it draws no `auto` chip — but it does
  draw `MCP`, from `dashboardMcpTagHtml()`, the dialog's own builder handed in
  through the runtime. Nothing else on that row says whether the agent can act
  on GridVibe, which is what a reader picking a pane to instruct is deciding,
  and picking one *while* working is what a docked panel is for. Every other
  field on that row stays the dialog's answer asked for by name; a second copy
  of any of them is how one pane comes to read two ways on two surfaces.
- Dashboard layout must remain usable without horizontal overflow at narrow
  widths. A polling update that changes only a row's title, hover, status,
  progress, or idle age updates that row in place, each field on its own
  comparison; structural changes rebuild the tree while restoring scroll and
  focus. A failed read leaves the last good tree on screen behind a stated retry
  notice, and an action failure survives successful polls.

## Agent tools (MCP)

[`gridvibe_mcp/README.md`](../gridvibe_mcp/README.md) is the reference for this
feature — the tool list, what each tool answers, which CLIs can be handed the
sidecar, and the stated-weakness notes. Do not restate the tool surface here or
in `README.md`; state the rules a change has to keep.

- **The sidecar is a sibling, not a subsystem.** Nothing under `web/` or
  `sessions/` imports `gridvibe_mcp` except `web/mcp_http.py`, and only the
  SDK-free halves of it (`server`, `client`, `identity`), function-locally;
  `gridvibe_mcp` imports nothing from GridVibe and reaches it over loopback HTTP.
  Only `gridvibe_mcp/__main__.py` imports the MCP SDK, which is what keeps the
  asyncio-native SDK out of the threading-mode Flask process.
  The SDK stays optional: `make check` and a plain `pip install -r requirements.txt`
  must both leave it uninstalled and the suite green.
- **One dispatch, both transports.** stdio and `POST /mcp/<token>` both run
  `gridvibe_mcp.server.dispatch` against a `GridVibeClient`. A tool must not
  behave differently depending on which transport asked; a new tool is added
  once, in `tool_specs()` and `_run()`, and reaches both. The endpoint is the POST
  half of streamable HTTP deliberately: `GET` and `DELETE` answer `405` with
  `Allow: POST` and a sentence naming what is absent and why, decided before the
  token is resolved so the refusal says nothing about whether one is live.
- **Every tool result is built from an explicit field list in `client.py`, never
  a pass-through of `to_dict()`**, and `scrub()` drops any key that looks like a
  secret at any depth regardless of the list. `list_saved_layouts` is the sharp
  case: the route it reads answers with a *decrypted* SSH password by design.
  Failures are typed and carry GridVibe's own sentence verbatim, unretried.
- **Four tiers, and the destroy tier is absent from the build.** Read and create
  only ever make something new; `set_pane_agent`/`set_pane_mode` replace what is
  behind an existing pane; `clear_pane` erases what one has drawn. Closing a
  pane, group or workspace, moving a group, and typing arbitrary input into a
  terminal are not written, not registered and not flag-gated — a tool that does
  not exist cannot be talked into running by a file an agent reads. `clear_pane`
  is not `send_input`: the only thing reaching stdin is GridVibe's own clear
  command, chosen by the window that knows the pane's shell family.
- **Every create verb is bounded by something.** `launch_panes` by
  `terminal.max_sessions` and the depth budget, `split_pane` by the group cap, and
  `create_workspace` by `MAX_EMPTY_WORKSPACES` — counted over workspaces that are
  both `retain_when_empty` and still holding no group, so filling one makes room
  for another, and decided inside the label claim so two requests cannot both read
  fifteen. The count is over the world rather than the caller, because the route
  cannot tell a tool from the launcher's **New Workspace** button.
- **Every tool-reachable pane transaction passes the shared gates in
  `web/pane_gates.py` before anything is mutated, closed or restarted**, so a
  refusal leaves a whole-pane snapshot unchanged — which is what the suites pin.
  Self (never the calling pane) and lineage (only panes that pane created, and
  only while it is still open) are shared; the third gate is each transaction's
  own kind rule and lives in its own module, raised through the same `refuse()`
  factory so every refusal names which gate failed. `PaneGateRefusal` carries the
  status; each transaction translates it into the one exception its route maps.
- **`override` is the user's word, never the tool's inference.** It waives
  lineage and the "already running an agent" refusal; never self, and never the
  kind gate's mode rule. It is forwarded because the calling agent stated it, is
  logged with both pane ids, and the tool descriptions must keep saying that only
  a person's words in that conversation justify it.
- **Lineage is read from the live registry, never from the request body.**
  `created_by_session_id` is stamped from the pane a launch or split actually
  came from (`_live_session_id` / `_live_origin_session_id`), and is deliberately
  absent from `runtime_state.json`: a creator id that survived a restart would
  name a stranger, so every restored pane refuses the gate that reads it. An
  origin that is *stated* and names nothing open is a lineage refusal at both
  ends of the record-then-perform split — when the intent is recorded and again
  when the page performs it — never a pane stamped with nobody, which would hand
  an agent's new pane a fresh depth budget of 0. Only an omitted origin is a
  person's own split.
- **The depth budget bounds agents launching agents, and only that.** A pane a
  tool creates is stamped one deeper than the pane that *asked*; a split that
  creates an agent costs budget, a split that creates a plain pane does not.
  Depths are bounded by `_normalize_agent_depth` wherever they are written — the
  gated relaunch included, which is the one path whose raw `setattr` through
  `update_session_metadata` normalizes nothing of its own. That route reads the
  caller *inside* the gate sequence, so a caller that closed mid-call raises the
  lineage refusal rather than restarting the chain's budget at 1.
- **A tool is never handed a silent normalization.** Where a page's own route may
  normalize (the toggle only offers what a pane can be), the gated twin refuses
  instead: browser mode on a remote pane, a `startup_mode` outside
  `_AGENT_MODE_TARGETS`, a browser pane in a group opening on another host. Being
  handed a plain terminal labelled a success is the one answer a tool must not get.
- **A launch from inside a pane opens on that pane's machine.** The body names
  `origin_session_id` and `workspaces.resolve_origin_connection` reads the host,
  user, port and password off that live session in this process; none of it
  reaches a response, a preset or a snapshot. An origin pane that has closed is a
  refusal, never a fall back to this machine.
- **That same pane, not the caller, answers an unstated workspace.** "Here" is
  the workspace the origin pane's *group* is in — read by GridVibe, in the launch
  itself (`workspace_anchor_session_id`), and re-read inside the lock that
  publishes the new group (`install_session_group`'s `workspace_from_session_id`).
  A caller that resolves it for itself and names the result in a second request
  can only ever be naming where the group *was*: a move carries the whole group
  and leaves every pane's `group_id` alone, so the panes would open in the
  workspace it just left, or fail once that workspace had been pruned. A stated
  `workspace_id` or `new_workspace` still wins; this is the default, not a second
  opinion.
- **Every *read* of "my workspace" is the group's answer too, never the
  inherited one.** Identity is captured once — at spawn, or when the token was
  minted — and a workspace is not a property of a pane that holds still, so
  `whoami` and `list_panes` resolve it through `live_workspace_id()` off the
  live group and keep the inherited id only as the fallback for a read that
  failed: degraded rather than wrong, exactly like the geometry beside it.
  `list_panes` reads that arrangement *before* the panes and hands it on, so
  both questions still cost one request and a caller that names a workspace
  costs none.
- **Identity arrives by inheritance locally and by token remotely.** The five
  `GRIDVIBE_*` variables are merged at the spawn call site in
  `_connect_local_session`, *not* inside `_local_shell_integration` — that
  function returns unchanged when `terminal.shell_integration` is off, which is a
  kill switch for the prompt hook and says nothing about MCP. WSL panes extend
  `WSLENV` through `merge_wslenv` so the two callers cannot overwrite each other.
  A tunnelled pane's identity comes from `PaneTokenRegistry`, in memory only,
  minted idempotently per pane and never written into a preset or a snapshot. It
  is revoked on the pane's own close path — the one that knows the session id,
  not `_shutdown_connection`, which does not — including the close that lands
  *inside* `_establish_mcp_tunnel`, where a stale connection is torn down rather
  than recorded and a raising teardown still costs the token. A tunnel that could
  not be opened at all revokes it on the same breath: a token with nothing to
  spend it on is still a live key to this machine's tools. The registry is
  bounded (`MAX_PANE_TOKENS`, oldest evicted, logged) so a revoke that never runs
  is a bounded leak rather than a permanent one.
- **`agent_mcp` is only ever set on a CLI that publishes a mechanism.**
  `_agent_supports_mcp` is asked at every write of the flag, not only where it is
  spent: the one launch normalizer in `web/saved_sessions.py`, the split
  overrides in `web/api.py`, `apply_pane_shell_change` in `web/session_shell.py`,
  and `_establish_mcp_tunnel` last, which is the only one with a cost attached —
  such a pane opens no port, mints no token and writes no remote file. The pane
  header's **MCP** tag paints off the flag, so the tag is honest for free.
- **`pane_can_run_the_sidecar()` picks the *shape* of the answer, never whether
  there is one.** A local pane gets the generated config; a remote pane gets a
  URL. The predicate is held there rather than at the launcher checkbox because a
  saved preset, a restored snapshot and the relaunch route all carry `agent_mcp`
  forward.
- **The generated `.gridvibe_mcp.json` is per install and rewritten on every app
  start**, gitignored, carrying no `env` block so one file serves every pane.
  Composition is registry-driven: `_MCP_FLAG_TEMPLATE` admits one option token and
  one placeholder so a registry typo cannot smuggle a second command onto the
  launch line, `_toml_override_flag` owns the per-shell quoting Codex needs, and
  anything that cannot be composed safely resolves to *no fragment* — costing the
  pane its tools, never its agent. A test-mode process refuses the production path.
- **The two Windows shells disagree about Codex's `-c` overrides, and the bare
  form is not always available.** `_toml_override_flag` is the one owner: cmd
  must see the TOML literal quotes bare (wrapped, the override is silently
  ignored), PowerShell and every POSIX shell must see the outer double quotes
  (bare, Codex exits before it starts). The exception is cmd's own argument
  parsing — an override carrying a space or any of `_CMD_ARGUMENT_SPECIALS`
  cannot cross as one argument at all, so it is quoted there too, which is not
  the silently-ignored case: the child strips those outer quotes before Codex
  parses anything, so it reads exactly what the bare form would have given it.
  Anything rendered into an override therefore avoids a space it does not need —
  `_inline_toml_env_fragment`'s inline table has none, deliberately.
- **The generated URL is one a URL parser reads back.** `url_host()` brackets an
  IPv6 literal, `loopback_base_url()` unwraps a bracketed bind address before
  the wildcard check so `::` still resolves to a dialable host, and the sidecar's
  own `normalize_base_url()` re-brackets the hostname `urlsplit` handed back
  un-bracketed. Both halves have to keep agreeing: one of them alone leaves the
  fix undone a process later, with the sidecar silently on the loopback default
  and unable to reach a GridVibe bound to IPv6 only.
- **The SSH reverse forward is opt-in per pane and costs the tools, never the
  shell.** `sshd` binds the remote host's own loopback; the port lives only for
  that connection; the remote config is written over SFTP at `0600` and named per
  pane; teardown runs off the close path on its own thread because every step is a
  round trip to a host that may be unreachable.
- **The remote config fails closed, because it *is* the token.** Both the file
  and the `~/.gridvibe` directory holding it are narrowed and then read back
  (`_restricted_to_owner`), and an existing directory is checked exactly like a
  new one — a previous run or a permissive `umask` may have left it open, and a
  directory other accounts can list names every pane's config. A mode that could
  not be applied, could not be read back, or still carries `FORBIDDEN_MODE_BITS`
  is the same answer: nobody knows who can read this. The file is removed again,
  the listener is withdrawn and the token revoked, and the pane starts without
  tools. A readable token left on a shared host is the one outcome worse than
  that.
- **The forwarded channel reaches a filter, never GridVibe's port.** On this end
  of a reverse forward sits the whole unauthenticated loopback API, whose only
  guard has ever been "you have to be on this machine", so a socket to GridVibe
  is opened only for a request that survives `web/ssh_tunnel.py`'s filter: one
  request per connection, `POST`, and a target equal to *this pane's own*
  `/mcp/<token>` under `secrets.compare_digest`. The token is a parameter of
  `open_reverse_tunnel`, so a port opened for one pane cannot spend another's,
  and `mcp_path()` is the one spelling `tunnel_url()` also builds the remote
  config from. Framing that cannot prove where the body ends is refused rather
  than normalized, the head and body are bounded, and refusals name nothing — the
  same `404` for a wrong method and a wrong token, and only the target's first
  segment in the log, because the path is a credential. The still-true narrowing
  is what it now is: a remote process reaches this pane's tool surface, acting on
  this machine, and nothing else on the API.
- **The channels that filter are bounded, and handed off at once.** Paramiko
  calls the forward handler on the transport's own packet thread — the thread
  carrying the pane's *shell* — so serving inline froze the terminal and starved
  the very bytes the tunnel exists for; one daemon thread per connection, started
  and returned from. That thread is then a budget: `MAX_FORWARDED_CHANNELS` per
  pane, held by that pane's own handler so one noisy host cannot starve a pane
  connected elsewhere, and a connection arriving with none free is *closed*
  rather than queued or refused — writing a refusal would put the work back on
  the thread the handoff exists to release. A thread that fails to start gives
  its slot back, because a budget that leaks is a tunnel that stops answering.
  The per-request head and body bounds cannot see this: an idle connection that
  sends nothing still costs a slot for `REQUEST_READ_TIMEOUT`.
- **Opening a window and splitting a pane are page work, recorded as intents.**
  The split axis never reaches the server: the page computes the rectangles and
  measures its own refusals off the live terminal. `web/window_intents.py` is in
  memory, TTL-bounded and capped, and exactly one claimant wins so two open pages
  deliver one window. Everything decidable without measuring a pane is decided
  before the intent is recorded. A page reports only its own kind's outcomes, and
  a refusal is relayed with the axis that would have worked — never a silent
  retry on the other axis.
- **The sidecar's wait must exceed the store's worst case, and the relation is
  pinned rather than derived.** `DEFAULT_WAIT_SECONDS` in `splits.py` and
  `windows.py` is above `INTENT_TTL_SECONDS + CLAIM_TTL_SECONDS`, so an expiry the
  sidecar reports is an expiry the store reached — which is what makes
  "the panes and the workspace are untouched" true wherever it is said. The
  sidecar cannot import `web/`, so a test asserts the inequality. The HTTP path
  waits on the store's own condition variable (`wait_for_settled`, reached by
  overriding `read_window_intent` on a `GridVibeClient` subclass) rather than
  re-entering the server, so neither tool module knows which transport it serves.
- **A failed poll is not a failed verb.** Both loops swallow `GridVibeError` and
  keep waiting to the deadline; a wait that *ends* never having read the store
  answers `no_window_available` with the sentence that says so and names
  `list_panes`, never the one claiming nothing happened.
- **Three honest outcomes per intent verb** (`opened`/`blocked`/`no_window_available`,
  `split`/`refused`/`no_window_available`), never a retry and never a pretended
  result. Browser mode answers `no_window_available` for a split because the
  intent poll runs in a native window only; `open_window` has a browser fallback
  because `webbrowser.open` is a real alternative and there is no equivalent for
  "measure this pane".

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
  wrapper for the stylesheet to tint. Known agents use the supplied SVG artwork
  in `docs/images/agent/` through local `<img>` elements; unknown agents fall
  back to the shared terminal SVG. Brand colors live in `tokens.css` and
  `agent-brand.css` applies them to terminal titles and dashboard names. Exact
  foregrounds are retained in both themes, with contrasting backgrounds for
  white/yellow names on light surfaces and near-black OpenCode names. Runtime
  agent changes update both the title's brand key and its icon in place.
- A split is two halves. `split-geometry.js` is the DOM-free, Node-tested rule
  for where the cut lands and what the axis track weights become: the offset is
  chosen by measured width, not by track count, so an odd span and a span whose
  tracks carry unequal weights — what a pane inherits when it absorbs a closed
  neighbour, and what older saved layouts come back as — still halve. The
  rewrite preserves the split span's own weight total, so no other pane moves;
  where another pane's edge falls inside the span, that divider wins and the cut
  goes to the nearest line instead. Keep `terminals.js` a caller: it measures the
  live grid and publishes one weight generation, and the split button and the
  sidecar's `split_pane` intent must keep reaching it through the same handler.

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
