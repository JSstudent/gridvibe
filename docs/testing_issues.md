# GridVibe Testing Issues
Last updated: 2026-10-04

## Open Issues

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
