# GridVibe MCP sidecar

A stdio MCP server that gives an agent running in a GridVibe pane nine tools
for seeing and building GridVibe workspaces.

It is a **sibling** of GridVibe, not a part of it. Nothing under `web/` or
`sessions/` imports this package, and this package imports nothing from
GridVibe — it speaks only HTTP to the loopback API. That boundary is what keeps
the asyncio-native MCP SDK out of the threading-mode Flask-SocketIO process.

## Install

```
make mcp-deps
```

Into the interpreter GridVibe itself runs under — that is the one the generated
config names, and `make mcp-deps` installs into the project venv for that reason.
By hand it is `python -m pip install -r requirements-mcp.txt`, run with that
interpreter.

Nothing else installs it: the SDK is optional, so `make check` and a plain
`pip install -r requirements.txt` both leave it out.

## When an agent says `CONNECTION_CLOSED`

```
make mcp-status
```

The CLI reports a sidecar that exited at startup as one line with no cause, and
the sidecar's own diagnosis goes to a stderr nothing displays. `make mcp-status`
walks the chain from the outside, exactly as the CLI would — the generated
config, the interpreter it names, the entry script, whether that interpreter has
a usable SDK, and a real `initialize` handshake against the whole configured
command line — and names the link that is broken. It exits non-zero when one is,
so it also works in CI.

A GridVibe that is not running is reported as a note, not a failure: the sidecar
starts fine without one and fails per tool call.

## How it starts

Nobody starts it by hand, and GridVibe does not start it either. The agent CLI
does, as an ordinary stdio child:

```
pane shell  ──spawns──▶  claude --mcp-config <path>  ──spawns──▶  this sidecar
```

GridVibe writes `<install>/.gridvibe_mcp.json` on every app start, naming this
install's interpreter and the port it actually bound. The file is gitignored,
carries no `env` block, and is identical for every pane.

When the pane closes, the CLI exits and the sidecar exits with it: no orphan,
nothing to supervise.

## Identity

The sidecar takes no identity arguments. It is a grandchild of the pane
process, so the pane's environment is already its environment:

| Variable | Value |
| --- | --- |
| `GRIDVIBE_URL` | `http://127.0.0.1:<port>` |
| `GRIDVIBE_SESSION_ID` | the pane id |
| `GRIDVIBE_GROUP_ID` | the group (tab) id |
| `GRIDVIBE_WORKSPACE_ID` | the workspace id |
| `GRIDVIBE_AGENT_DEPTH` | `0`, or the launching agent's depth + 1 |

An agent started by hand outside GridVibe inherits none of them, and `whoami`
says so rather than guessing. Read tools still work.

## Tools

**read** — `gridvibe_status`, `list_workspaces`, `list_panes`, `list_agents`,
`whoami`.

**create** — `create_workspace`, `launch_panes`, `open_window`, `split_pane`.

**absent** — closing a pane, a group or a workspace; switching a pane's mode or
shell; moving a group; typing into a terminal. These are not written, not
registered, and not flag-gated. A tool that does not exist cannot be talked into
running by a file an agent reads.

## Stated properties, not discoveries

- **Prompt injection reaches further than a terminal.** A repository file that
  talks an agent into `launch_panes` has a lever on the machine GridVibe runs
  on. This is why the create tier is small and the destroy tier is absent
  rather than gated.
- **The depth budget is a guardrail, not a boundary.** `launch_panes` refuses
  past `--max-agent-depth` (default 2) and stamps depth + 1 on the panes it
  creates, so the refusal compounds. The agent it constrains could unset
  `GRIDVIBE_AGENT_DEPTH`. It stops a runaway loop; it does not stop an
  adversary. A server-side per-group counter is the real answer and is a later
  phase.
- **No credential ever reaches a tool result.** Every result is built from an
  explicit field list in `client.py`, and anything whose key looks like a
  secret is dropped at any depth regardless.

## Scope

Phase 0 covers Windows-native local panes. WSL panes receive the variables but
the checkbox waits on verifying that a Linux pane can reach the Windows
interpreter through interop.

**SSH panes get no tools, and cannot be talked into asking for them.** `sshd`
forwards only what `AcceptEnv` permits, and a remote host has no route to the
user's loopback — nor a copy of the generated config, whose path is this
machine's. Enforced in four places rather than one, because the checkbox is not
the only way a pane comes to carry `agent_mcp`:

| Where | What it does |
| --- | --- |
| `launcher.js` → `agentMcpAvailableHere()` | Offers the checkbox only in Local Repo mode |
| `agents.py` → `_compose_agent_startup_command` | Drops the flag for any pane that is not local |
| `session_shell.py` → `apply_pane_shell_change` | Refuses a stated `mcp: true`, mutating nothing |
| `saved_sessions.py` → `_normalize_terminal_entries` | Normalizes the field away on an SSH preset |

The predicate itself is `mcp_launch.pane_can_run_the_sidecar`, and a pane whose
record does not say where its shell runs reads as remote — the answer that
cannot cost the user their agent.

## Checking the surface without a running GridVibe

```
make mcp-tools
```
