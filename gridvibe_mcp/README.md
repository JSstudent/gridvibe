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

### Which CLIs can be handed the sidecar

Three of the eight, and the mechanism differs for each. Every row was checked
against the installed CLI's own `--help`, which is what `"verified": true` in
`agent_registry.json` records.

| CLI | How | Shape |
| --- | --- | --- |
| `claude` | `--mcp-config "<path>"` | `flag` template |
| `copilot` | `--additional-mcp-config "@<path>"` — `@` marks a path rather than inline JSON, and it *augments* `~/.copilot/mcp-config.json` for the session | `flag` template |
| `codex` | `-c mcp_servers.gridvibe.…` overrides — it takes no config file at all | `style: inline_toml` |

The quote opens *before* Copilot's `@`: `@"C:\…"` starts a here-string in
PowerShell and fails to parse.

Codex's overrides are quoted per shell, because the two Windows shells
disagree and no single string serves both — cmd must see the TOML literal
quotes bare (double-quoted, the override is silently ignored), PowerShell and
POSIX shells must see the outer double quotes (bare, Codex exits with *failed
to load bootstrap configuration*). `_toml_override_flag` owns that one rule and
the terminal-title override reads it too.

`grok`, `hermes`, `opencode`, `kilo` and `kimi` publish nothing. Their only
mechanism is an `<agent> mcp add` subcommand that edits the user's own config
permanently — a change that would outlive the pane whose checkbox asked for it,
which is why it is absent rather than pending.

### SSH panes

A remote pane's agent cannot run the sidecar: its host has no copy of it, no
MCP SDK, and no address for this machine's loopback. So it does not run one.
The protocol moves instead of the process.

```
remote agent ──HTTP──▶ 127.0.0.1:<assigned>   (on the remote host)
                          │  sshd reverse forward, on the pane's own transport
                          ▼
                       GridVibe  POST /mcp/<token>   ──▶ the same dispatch()
```

| Piece | Where |
| --- | --- |
| MCP over streamable HTTP | `web/mcp_http.py` — reuses the sidecar's own synchronous `dispatch`, so a tool cannot behave differently by transport |
| Per-pane token | `web/mcp_http.py` → `pane_tokens` — identity cannot cross a machine by inheritance, so it rides in the URL |
| Reverse tunnel + remote config | `web/ssh_tunnel.py` — `request_port_forward` on the pane's existing transport, config placed over SFTP |
| Wiring | `terminal_io._establish_mcp_tunnel`, torn down in `_shutdown_connection` |

Nothing is installed on the remote host. The config written there names a URL,
not a command, which is why Codex gets `-c mcp_servers.gridvibe.url=` rather
than the inline command-and-args form a local pane gets.

**What this widens.** While a tunnelled pane is open, any process on that
remote host that can reach the forwarded port can spend that pane's token, and
the create tier acts on *this* machine. The bounds: sshd binds the remote
host's own loopback (never its network), the port lives only as long as that
connection, the token is refused the moment the pane closes, and a pane whose
box is unticked opens no port and mints no token at all.

## Checking the surface without a running GridVibe

```
make mcp-tools
```
