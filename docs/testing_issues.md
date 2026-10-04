# GridVibe Testing Issues
Last updated: 2026-10-04

## Open Issues

### Issue ID: ISSUE-2026-063
- Title: Pane ID mismatch blocks Save & Close without a synchronization action
- Priority: High
- Status: Open
- Area: `sessions/manager.py`, `web/static/js/terminals.js`, `web/static/js/session-persistence.js`, `web/static/js/lifecycle.js`, `web/lifecycle.py`
- Assignee: Unassigned
- Tags: `session`, `workspace`, `ui`, `socketio`, `reliability`, `tests`
- Reported: 2026-10-04

Description:
The user reports that Save & Close is blocked by a pane identity mismatch.
The close dialog offers another save attempt or continuing without saving,
but provides no explicit way to synchronize the pane list and save the
currently open workspace. The validation and failed-save close gate are
confirmed in code. The operation that originally caused the user's frontend
and backend pane lists to diverge remains unverified; pane splits, closes,
replacements, cached tabs and concurrent updates are investigation targets,
not established causes.

Steps to reproduce:
1. Open GridVibe with a live session group containing at least two panes.
2. In a controlled frontend/backend integration harness, retain the group's
   old frontend pane list while removing or replacing one pane in the backend.
   Use the current presentation revision so the identity check is reached.
   This creates the mismatch deterministically; the user's live trigger is
   still unknown, and this harness reproduction has not yet been executed.
3. Request application close and choose Save open workspaces & close or
   Save open sessions + workspaces & close, causing the presentation flush.
4. Observe the pane ID error and check the dialog for a synchronization action.
   Retry while the mismatch persists and compare with continuing without saving.

Expected behavior:
The failure dialog offers an explicit Synchronize panes & retry save action.
It reconciles the displayed and cached groups with the actual live sessions,
preserves current presentation for surviving panes, shows any membership
changes, and retries the originally selected save-and-close option. Closing
proceeds only after the requested save succeeds. All open groups/workspaces
remain covered, including background tabs and other participating windows.

Actual behavior / logs:
User-reported message on 2026-10-04:

```text
Pane ids do not match the live session group Choose an option to try again or continue without saving.
```

- `SessionManager.apply_group_presentation()` compares `set(pane_order)` with
  the group's live session IDs and returns `outcome: invalid` with the first
  sentence above when they differ. It rejects the update before mutation.
- `flushLivePresentation()` sends presentation for the visible and cached
  groups; `attachFlushResponder()` reports a failed flush acknowledgement
  when this operation fails.
- `lifecycle.js` displays the second sentence through `showFailure()` and
  retains the dialog. Its choices are no save, workspaces, and sessions plus
  workspaces, with a separate cancel action; there is no synchronize button.
- Keeping the application open after a failed requested save is intentional.
  The defect is the missing recovery path. This report confirms the code
  behavior and records the user's live symptom; no independent live UI
  reproduction or attribution of the original mismatch has been performed.

### Proposed solution:
Add an in-page recovery action to the lifecycle failure dialog for a structured
pane-membership mismatch, rather than matching the human-readable error text.
Return bounded mismatch context identifying the affected workspace/group and
the current membership/revision. Investigate how the stale pane list arose
and fix that synchronization path as well as adding recovery.

Reuse the owning window's group/session refresh and the existing presentation
queue to reconcile visible and cached views. Capture current presentation
before refreshing, retain it only for matching live pane identities and modes,
discard stale writes for removed/replaced panes, and initialize newly discovered
live panes from their valid server state. Reconcile order and split geometry
to the resulting complete live membership. Surface added/removed panes to the
user; never silently omit a still-live pane just because its DOM view is absent,
close running sessions, or treat a reused grid slot as the same pane. Preserve
surviving terminals, explorer drafts, scroll and focus during recovery.

After reconciliation, build a fresh whole-group snapshot at the current revision,
flush it through the shared barrier, and retry the user's selected save scope.
Bound recovery attempts and retain an actionable failure if membership changes
again, a group moves/disappears, another window fails, or persistence fails.
Preserve exact membership validation, revision checks, window ownership, manual
save semantics and the successful-save requirement for exit. Saving the current
open state must not become a bypass that captures stale backend presentation.
No durable schema migration is expected; update lifecycle/presentation contracts
and user-facing documentation when the behavior ships.

Add focused backend and behavioral Node/integration coverage for removed,
added and replaced panes, stale cached groups, revision conflicts, changes
during synchronization, repeated retries, multiple windows, both save scopes,
and persistence failure after recovery. Assert that current presentation for
surviving panes is saved, all live panes are accounted for, failed saves leave
the app open, and successful synchronization permits Save & Close. Verify the
reported sequence in browser and native modes once its live trigger is known.

### Issue ID: ISSUE-2026-061
- Title: External file creation in ignored folders leaves explorer listings stale
- Priority: Medium
- Status: Open
- Area: `web/static/js/explorer-git-watch.js`, `web/static/js/explorer-viewer.js`, `web/static/js/explorer-tree.js`, `web/explorer.py`, `web/api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `git`, `reliability`, `tests`
- Reported: 2026-10-04

Description:
Files created by an agent or another external program inside a Git-ignored
folder do not appear automatically in the Files tree or the directory listing
in the Preview tab. The user reported this in `docs/r&d/MCP/flows` in this
repository. Creating a file through GridVibe's own explorer controls updates
the surfaces, so the same directory appears live for UI actions but stale for
agent output. An agent running in a GridVibe terminal still writes directly to
disk and does not invoke the explorer's mutation-completion refresh.

Steps to reproduce:
1. Open a GridVibe Files pane rooted at this repository. Expand
   `docs/r&d/MCP/flows` in the Files tree and open that folder's directory
   listing in the Preview tab. Keep the page visible and let its initial
   background poll establish a baseline.
2. From another editor, a shell, or an agent, create a new text file inside
   that folder. Do not use GridVibe's explorer Create file action.
3. Leave the explorer idle, with focus and the pointer outside its listing
   and tree, and wait beyond several normal local polling intervals.
4. Observe that the new file is absent from both surfaces. Compare with a
   second file created through GridVibe's explorer controls, which triggers
   the explicit refresh and updates the displayed listing.

Expected behavior:
The directory Preview and expanded Files tree should discover externally
created files within a bounded background-refresh interval, including files
in ignored folders and roots outside Git repositories. A file's visibility
in the explorer should not depend on whether Git reports it. Preserve scroll,
expanded folders, filters, active tabs, and any open editor buffer.

Actual behavior / logs:
The live symptom and the successful GridVibe-create comparison were reported
by the user on 2026-10-04; no separate live UI reproduction was performed
during this investigation. The supporting mechanisms were verified in code
and with read-only Git commands:

- `git check-ignore -v -- 'docs/r&d/MCP/flows'
  'docs/r&d/MCP/flows/example.txt'` identifies `.gitignore:86:docs/r&d/`
  for both the folder and an example child path. These paths are ignored,
  rather than ordinary untracked paths reported by Git.
- `git status --porcelain=v2 --untracked-files=all --
  'docs/r&d/MCP/flows'` returns no status entries. The `--untracked-files=all`
  flag in `_get_git_context()` does not include ignored files.
- `explorerGitWatchCheckOne()` uses the selected Git scope's revision for
  the filesystem consumer. It schedules `explorerFsWatchFlushPending()`
  only when that revision changes; there is no independent directory-change
  check. Creating an ignored file therefore cannot supply this signal.
- `explorerFsWatchConsumer()` also requires
  `pane._explorerGitContext.available`, so non-Git roots cannot arm this
  automatic listing/tree refresh either. That broader limitation is
  confirmed by inspection, not a separate user-reported reproduction.
- GridVibe's create action explicitly calls
  `refreshExplorerAfterFilesystemMutation()`, which bypasses the need for
  a background Git change signal. The directory Preview shares the quiet
  filesystem refresh path with the Files tree; this is not a failure of
  the separate open-file content watcher.

### Proposed solution:
Give visible filesystem surfaces a bounded change signal independent of Git.
Extend the explorer read API/backend and page-level watcher to check the
browsed directory and loaded expanded tree directories for membership changes,
including ignored entries and non-Git roots. Use a bounded listing revision
or equivalent metadata check that reliably notices child creation, deletion,
and rename; avoid recursive whole-root scans or relying solely on Git status.
Keep the Git poll for Git decorations and sidebar updates, without letting
its pin/Follow scope restrict filesystem freshness.

Reuse `refreshExplorerFilesystemSurfacesQuiet()` and the quiet tree/listing
helpers when filesystem state changes. Retain visibility suspension, local/SSH
cost bounds, no overlapping requests, interaction deferral, pane/session/root
identity checks after awaits, and preservation of scroll and expansion.
Unchanged directories should cause no DOM writes, and background work must
never replace an editor buffer. No persistence format or migration is needed.

Add behavioral backend and Node regression coverage for external creation,
deletion, and rename inside an ignored folder and a non-Git root; creation
inside an ordinary untracked folder; a Git pin on an unrelated folder/file;
and both the directory Preview and expanded tree updating without an explicit
mutation action. Cover local/SSH parity, unchanged-state no-op behavior,
changes before the first baseline poll, hidden-page suspension, deferred
apply, stale responses after navigation or pane replacement, bounded tree
work, and preservation of an open editor buffer. Verify the reported folder
manually with an agent-created file and compare with GridVibe's Create file
action.

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
