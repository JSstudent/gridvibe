# GridVibe Testing Issues
Last updated: 2026-10-06

## Open Issues

### Issue ID: ISSUE-2026-064
- Title: Switching session tabs shows stale explorer Git and file-tree state for up to a minute
- Priority: Medium
- Status: Open
- Area: `web/static/js/explorer-git-watch.js`, `web/static/js/terminals.js`, `web/explorer.py`
- Assignee: Unassigned
- Tags: `explorer`, `git`, `performance`, `session-groups`
- Reported: 2026-10-06

Description:
Switching to a session tab does not start an explorer change check. The tab
is usually restored from `cachedGroupViews`, and every explorer path on that
restore re-renders cached state without fetching: `syncExplorerPane` returns
early for an attached pane, `loadExplorerGitRepo` only re-renders when already
loaded for the scope, and `loadExplorerTree` renders the cached children.
Catching up is left to the change watcher, which the swap never wakes —
`explorerGitWatchWake()` runs only on `visibilitychange`, `focus` and
`pageshow`. Large directories and repositories make the delay longer, on a
swap and in steady state.

Steps to reproduce:
1. Open two session tabs: A with only terminal panes, B with a local explorer
   pane (Files tree and Git sidebar open) on a Git worktree.
2. Stay on A for longer than one watch interval.
3. From a terminal, create or modify a file under B's root.
4. Switch to B.

Expected behavior:
The tab shows the new file and its Git badge within about one `git status`
round trip of the switch.

Actual behavior / logs:
B shows the state from when it was left until the next page tick, up to 60 s
later. Causes found in code:
- **Swap timing.** `explorerGitWatchTick` starts `nextDelay` at
  `EXPLORER_GIT_WATCH_MAX_MS` (60 s) and only shortens it for panes it can
  poll in the current `terminals`. A tab with no explorer pane schedules the
  next tick 60 s out. The returning pane's own `_…NextAt` deadlines are
  already past, so the page timer alone sets the wait.
- **Running tick.** Checks run one at a time and are awaited. A check for a
  pane in the tab just left that is waiting on a slow `git status` holds back
  the new tab. A wake during a running tick returns early on
  `explorerGitWatchRunning`, and the running tick then reschedules from its
  stale list.
- **Interval includes refresh work.** The next delay is the measured duration
  × 6 (5 s minimum, 60 s maximum). For the Git check the duration runs from
  before the `/git/state` fetch to `finally`, so it includes the full
  `/git/repo` refetch, the directory re-list and the change-mark refresh. A
  4 s refresh after a change puts the next poll about 24 s out. The directory
  check's duration covers up to 16 sequential `/directory/state` requests plus
  its re-list. From the third consecutive changed poll the interval also
  doubles per poll, up to 60 s.
- **Directories over 4096 entries.** At `EXPLORER_DIRECTORY_STATE_MAX_ENTRIES`
  (4096) the server returns `revision: ""` and the client skips that
  directory, so those directories update only through the Git signal.
- **Git status timeout.** The state poll's `git status --untracked-files=all`
  runs with `timeout=2.0`. In a large Windows worktree it can time out; each
  timeout counts as a failure (10/20/30 s backoff, suspended after 5), and a
  listing whose Git context timed out also drops the Git-driven re-list
  consumer (`explorerFsWatchConsumer` requires `_explorerGitContext.available`).
- **Large expanded trees.** The directory poll checks at most 16 directories
  per pass and advances a rotating window, so with 50 expanded folders a
  change in the last one can take 4 passes at the stretched interval.
- **Deferral while reading.** Applies are held while focus is inside the tree
  or listing, or while the pointer is over a surface scrolled away from the
  top (`explorerFsWatchDeferralActive`). Large trees are usually scrolled.

### Proposed solution:
1. Add a wake a swap can call (for example `explorerGitWatchWakeVisible()`)
   that zeroes `_explorerGitWatchNextAt`, `_explorerFileWatchNextAt` and
   `_explorerDirWatchNextAt` for the current `terminals`, clears the timer and
   starts a tick.
2. Add a rerun-requested flag: a wake that arrives during a running tick sets
   it, and the running tick re-runs immediately instead of scheduling its
   stale delay. Without it, step 1 does nothing whenever a tick is in flight.
3. Call the wake at the end of `initialLoad()` when the view was restored from
   cache or rebuilt (not when the current view was reused). Debounce it by
   about 200 ms, or skip panes checked in the last 1–2 s, so fast tab cycling
   does not queue a burst of `git status` calls.
4. Optionally, let a swap stop a tick that is waiting on a pane no longer
   shown. The stale-response guards (`explorerWatchPaneCurrent`) already
   discard that result.
5. Measure only the state request when computing the adaptive interval, not
   the refresh that follows a detected change.
6. Give the `/git/state` poll a longer timeout than 2 s, or reuse a recent
   `git status` result. Document `core.untrackedCache=true` / `core.fsmonitor`
   as a user-side speed-up for large repositories.
7. Check the browsed directory first in every directory pass, outside the
   16-directory window.

Steps 1–4 keep the watcher contract: only visible panes are polled, at most
one request per pane per check, and an unchanged poll makes no DOM writes.
Tests: a Node-executed watch test that a cache restore wakes the visible
explorer panes once (debounced), that a wake during a running tick causes an
immediate rerun, and that the adaptive delay ignores apply time.

### Issue ID: ISSUE-2026-059
- Title: WSL panes hand a Linux-native agent CLI Windows paths for GridVibe's tools
- Priority: Medium
- Status: Open
- Area: `web/agents.py`, `web/mcp_launch.py`
- Assignee: Unassigned
- Tags: `mcp`, `wsl`, `launcher`, `windows`
- Reported: 2026-10-01

Description:
A local WSL pane (`mode == "wsl"`, `use_wsl=True`) with the GridVibe tools box
ticked composes its agent line for a POSIX shell (`_pane_shell_family` returns
`posix`), but `_agent_mcp_command_fragment` names `mcp_config_path()`, a
**Windows** path (`C:\…\.gridvibe_mcp.json`), and that file's `command` is the
Windows interpreter (`C:\…\.venv\Scripts\python.exe`). That only works when the
CLI on the WSL `PATH` is the Windows binary reached through interop. A CLI
installed natively inside the distro is handed a path it cannot read. For
Claude that costs the agent, not just the tools: Claude exits on a
`--mcp-config` file it cannot find.

Steps to reproduce:
1. On Windows with a WSL2 distro, install an agent CLI natively inside the
   distro (for example Linux `node` plus `npm i -g @anthropic-ai/claude-code`),
   so `command -v claude` resolves to a Linux path rather than `/mnt/c/…`.
2. Start GridVibe, open a local WSL pane, choose Claude, tick the GridVibe tools
   box and launch.
3. The pane runs `claude --mcp-config "C:\…\.gridvibe_mcp.json"`.

Expected behavior:
A WSL pane with the box ticked either starts the agent with working GridVibe
tools or starts it without them. It never gives the agent a launch line it
cannot start from.

Actual behavior / logs:
Verified on 2026-10-01 in Ubuntu-22.04 (WSL2):
- Inside WSL, `test -f "C:\…\.gridvibe_mcp.json"` and
  `test -x "C:\…\.venv\Scripts\python.exe"` both fail.
- `claude` resolves to `/mnt/c/…/npm/claude`, whose shim execs the Windows
  `claude.exe`. `claude --mcp-config "C:\…\.gridvibe_mcp.json" -p …` loaded the
  config without error, so this machine's Claude works only through interop.
- `codex` and `copilot` resolve to the Windows npm shims too, but those run
  `exec node …`, and the distro has no Linux `node`:
  `exec: node: not found`. They fail before MCP matters.
- Claude's handling of a config file it cannot find (shown with stray
  arguments read as config paths):
  `Error: Invalid MCP configuration: MCP config file not found: …`. That is
  what a native Claude would print for the `C:\` path. This step is inferred,
  because no native Linux Claude was installed to run it end to end.

### Proposed solution:
For a WSL pane, translate both paths to their `/mnt/<drive>/…` form (or
`wslpath`) when the CLI that will run is a Linux binary. Point the config's
`command` at an interpreter the distro can execute: the Windows `python.exe`
through interop works and forwards the identity, because `WSLENV` already
names it (`web/mcp_launch.apply_pane_identity`). Investigation target:
GridVibe cannot tell from the line alone whether `claude` in the distro is
the interop shim or a native binary. The fix may need a per-distro probe
(`command -v <binary>` under `/mnt/`), or a WSL-specific generated config
that is valid for both. Until then, a safe interim is to give WSL panes no
MCP fragment for a native CLI, rather than a line that costs the agent.
Tests: composition for a WSL pane names `/mnt/…` paths (or none), and never a
`C:\` path on a `posix` line. The opencode `OPENCODE_CONFIG` prefix (opencode
plan, Stage 2) should follow the same rule when this is fixed.
