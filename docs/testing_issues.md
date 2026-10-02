# GridVibe Testing Issues
Last updated: 2026-10-02

## Open Issues

### Issue ID: ISSUE-2026-060
- Title: Background tab updates can blank the visible session after a cache restore
- Priority: High
- Status: Open
- Area: `web/static/js/terminals.js`
- Assignee: Unassigned
- Tags: `terminal`, `session`, `ui`, `socketio`, `windows`, `tests`
- Reported: 2026-10-02

Description:
The user reports a recurring flash, and sometimes a persistently blank pane
area, when an agent launches a new session tab without switching focus in native
mode. The selected original tab, tab strip and Agent Dashboard sidebar remain
visible, and the agents continue working. The crew-links visual test exposed
the issue again, but the user reports that it predates that feature. Screenshot:
[`new_sess_orig_blanc.png`](images/new_sess_orig_blanc.png).

Investigation reproduced a matching failure in the shared frontend: a refresh
mistakes a terminal's focus class for a layout change, then restores a cached
DOM fragment that an earlier restore already emptied. All pane elements leave
the visible grid while its model still says the grid is built. Subsequent
ordinary refreshes do not repair it. This mechanism does not require native
APIs; browser mode has the same code path, although the reported live incident
was in native mode and browser reproduction has not been performed.

Steps to reproduce:
1. Open a workspace with session tabs A and B and a connected terminal in A.
2. Switch from A to B and back to A, so A has been cached and restored.
3. Click inside A's terminal. Its grid now carries `terminal-focus` as well as
   its layout class.
4. Have an MCP-enabled agent call `launch_panes` into that workspace to create
   a new background tab, without calling `focus_session` or `focus_pane`.
   The resulting `session_groups_updated` event schedules `refreshStatuses()`.
5. Observe whether A's pane area becomes blank while its tab remains selected.
   Leave the window open for another status refresh and check for recovery.

The deterministic isolated reproduction executes the actual
`restoreCachedGroupView()`, `refreshStatuses()` and `initialLoad()` functions
from `terminals.js` using the existing Node harness infrastructure in
`tests/test_pane_connecting_overlay.py`. Model one matching connected pane,
an unchanged active group, and a cache fragment holding its card; make fragment
insertion move its children and make `grid.innerHTML = ''` remove the grid's
children. Restore once, refresh without focus, add `terminal-focus` to the
grid's class string, refresh again, then run one more refresh. This was executed
successfully on 2026-10-02; the live UI steps above still need end-to-end
verification in native and browser modes.

Expected behavior:
A tab opened by an agent joins the tab strip without changing the active tab,
rebuilding its unchanged panes, or interrupting its display and input focus.
Returning to a cached tab restores its panes once. Later refreshes preserve
those mounted panes and never treat a consumed fragment as a detached view.

Actual behavior / logs:
The isolated run returned:

```json
{
  "firstRestore": {"cards": 1, "cacheRetained": true},
  "ordinaryRefresh": 1,
  "focusedRefresh": {
    "cards": 0, "gridBuilt": true, "terminals": 1, "display": ""
  },
  "laterRefresh": 0,
  "rebuilt": 0
}
```

Confirmed code mechanisms, at the time of investigation:

- `setFocusedTerminal()` adds `terminal-focus` to `#terminalsGrid`
  (`terminals.js:6056`); broadcast mode can also add `broadcast-input`.
- `refreshStatuses()` compares the complete `className` to the expected layout
  or exactly `layout-split-local` (`:9671`). A legitimate additional class
  causes it to call `initialLoad()` even when pane IDs and types are unchanged.
  The `usingCurrentView` check in `initialLoad()` repeats the same comparison
  (`:9038`), so it can reject the already-mounted view too.
- `restoreCachedGroupView()` clears the live grid and appends `cached.fragment`
  (`:1346`). Appending a `DocumentFragment` moves its children into the page,
  leaving that fragment empty. The function retains the entry in
  `cachedGroupViews`, including its pane arrays and layout metadata. A later
  load can therefore accept the stale entry as matching and restore it again,
  clearing the grid without inserting any pane cards.
- The second restore still sets `gridBuilt` from `terminals.length` (`:1384`)
  and restores the plain layout class. Later refreshes compare the model and
  class successfully without checking that the pane cards remain mounted.
  This explains the persistent blank result without a server failure. Without
  a matching retained cache, the false layout mismatch can instead rebuild
  the grid, providing a separate explanation for the reported flash.

The live log `logs/gridvibe.log` records successful creation of `crew test`
at `2026-10-02 21:32:15,232` and `crew test nested` at
`2026-10-02 21:32:34,792`. Both creation requests returned HTTP 201. There was
no `/terminals` page reload or server error at either launch, and the original
orchestrator continued collecting handoff results afterward. Frontend load and
refresh exceptions are written to the browser console, so these server logs
cannot establish the exact frontend branch taken during the screenshot's
incident. The reproduced mechanism is confirmed; attributing that specific
live incident to it remains an inference.

### Proposed solution:
Fix both the false invalidation and cache ownership in
`web/static/js/terminals.js`:

- Compare the layout class independently of focus and broadcast classes in
  both `refreshStatuses()` and `initialLoad()`. Apply the same rule to related
  cached-layout checks, including `layout-split-local`; retain the existing
  pane identity/type and geometry-staleness checks.
- Consume the cache entry when its detached DOM and pane state become the
  visible view. Remove the map entry without using `dropCachedGroupView()`:
  that disposal path would destroy the panes now mounted on screen and leave
  their session rooms. A later departure should create a fresh cache entry.
  Preserve viewport restoration, resize observers, session routing and split
  geometry when transferring ownership.
- Ensure a cache restore cannot report success from an empty or inconsistent
  fragment. Make reconciliation detect a built model whose expected pane cards
  are missing and recover through the ordinary load path, without rebuilding
  healthy panes on every refresh. Keep existing load-token and group-identity
  guards for overlapping loads and rapid tab changes.
- Add behavioral Node regression coverage using real fragment move semantics
  and a consistent `className`/`classList` model. Exercise A → B → A, terminal
  focus, background-agent tab arrival, and another refresh; assert that the
  same pane nodes and terminal instances remain mounted, focus is preserved,
  and no unnecessary rebuild or session leave/rejoin occurs. Cover broadcast
  classes, fixed and local split layouts, repeated tab swaps, stale/empty
  cache recovery, and legitimate pane/layout changes that still require a
  rebuild. Extend the agent-opened-tab coverage beyond tab selection alone;
  the existing overlay harness stubs cache restoration and does not reproduce
  fragment consumption by itself.
- Verify the minimal tab-launch sequence manually in native and browser mode,
  then repeat the crew-links test. Confirm that creating both the main crew tab
  and its nested tab leaves the original panes painted without a flash.

No persistence format or migration is required. This issue records the
investigation and fix proposal only; no application fix has been applied.

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
