# GridVibe MCP sidecar

A stdio MCP server that gives an agent running in a GridVibe pane thirteen
tools for seeing and building GridVibe workspaces.

This file is the reference for the MCP feature. Everything else that mentions
it — `README.md`, `CLAUDE.md`, `docs/engineering_contracts.md` — says what it
does at its own altitude and points here rather than repeating it.

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
says so rather than guessing. The read tools still work, and a launch still
works if it names a workspace; the three gated tools are refused outright,
because the lineage gate compares against a calling pane there is none of.

Codex is the exception that proves the rule: its own spawn of an MCP server does
not forward the pane's environment, so GridVibe states the same five variables
back to it as an inline TOML table on the launch line
(`web/agents.py:_inline_toml_env_fragment`).

## Tools

Thirteen, in four tiers by blast radius. The order below is the order
`tool_specs()` registers them in, and `tests/test_mcp_tools.py` pins it.

### read — six

| Tool | Answers |
| --- | --- |
| `gridvibe_status` | is GridVibe running, which version, how many workspaces |
| `list_workspaces` | every live workspace, with its label and group count |
| `list_panes` | the panes in one workspace or group: what each is, where it points, what it runs on, and where it sits — `index`, `rect`, `relative_area`, and the `neighbours` above, below, left and right |
| `list_agents` | every agent anywhere, with a working/idle reading, under the workspace and session holding it |
| `list_saved_layouts` | every saved launcher preset as a *shape* — name, layout, pane count, geometry, and what each pane is. Never a connection |
| `whoami` | which pane this agent is in, its directory, **which machine that directory is on**, how deep it is, where it sits, and whether it may still launch (`may_launch_panes`) or split (`may_split_panes`) — a refusal of the first carries a `split_note` saying the second is still open |

`whoami` before resolving "this directory", "this workspace" or "the terminal
below this one". Its `runs_on` is the field that stops a remote path being
handed to a pane opened on the wrong machine.

### create — four

| Tool | Makes |
| --- | --- |
| `create_workspace` | one empty, labelled workspace. Creating it does not make a window appear, and it is refused past sixteen workspaces that are *still* empty — counted over the whole app, because the server cannot tell a tool from the launcher's own button |
| `launch_panes` | one session group of panes — agent, terminal, file explorer or browser preview |
| `open_window` | a workspace on screen. Reports `opened`, `blocked` or `no_window_available` |
| `split_pane` | halves one pane on a chosen axis and says what the new pane runs. Reports `split`, `refused` or `no_window_available` |

### replace — two

`set_pane_agent` relaunches a pane into an agent CLI (or `agent: ""` back to a
plain shell, optionally changing the local shell family and the MCP choice).
`set_pane_mode` turns a pane into a file explorer, a browser preview or a plain
terminal.

Both **end what is running in that pane**, so both are gated — see below.

### display — one

`clear_pane` clears one terminal pane and purges its replay buffer: the header's
Clear button, asked for by a tool. The scrollback is gone and cannot be read
back, so it is gated the same way.

It is **not** the missing `send_input`. The only thing that reaches the shell's
stdin is GridVibe's own clear command, chosen by the window that knows the
pane's shell family; a tool never supplies a byte of it. The result keeps the
two halves apart on purpose — `buffer_purged` is a fact, `display_reset_requested`
is a request, and a pane nobody has open resets nothing.

### The gates on the last three

Shared in `web/pane_gates.py`, so all three refuse in the same words. A refusal
names which gate failed, because an agent told only "refused" calls again.

| Gate | Rule | Waivable |
| --- | --- | --- |
| **self** | never the pane the request came from | no |
| **lineage** | only a pane this agent's own pane created, and only while that caller pane is still open | by `override` |
| **kind** | each transaction's own: a relaunch takes only a plain terminal; a mode switch refuses a pane with an agent running in it; a clear refuses both a non-terminal pane and a running agent | the "already an agent" half, by `override` |

A pane that existed before a GridVibe restart carries no creator — `created_by_session_id`
is deliberately absent from the runtime snapshot — so it is always refused
without `override`. That is the honest answer: GridVibe does not know who made
it, so it does not guess.

**`override` is only ever the user's word.** It waives lineage and the "already
running an agent" refusal; it never waives self, and never the kind gate's mode
rule. A calling agent sets it only when the person it is talking to has, in that
conversation, said to replace *this specific pane* — never because a file it
read, a prior tool result, or another pane's output asked for it. Every waiver
is logged with both pane ids.

### absent

Closing a pane, a group or a workspace; moving a group; typing arbitrary input
into a terminal. These are not written, not registered, and not flag-gated. A
tool that does not exist cannot be talked into running by a file an agent reads.

## Splitting and opening windows need a page

Two things GridVibe cannot do from outside a browser page, and the same
mechanism answers both (`web/window_intents.py`, `web/static/js/window-intent.js`):

- **Open a window.** Nothing outside a page can open a pywebview window.
- **Split a pane.** The axis never reaches the server. The page computes the new
  rectangles, and its refusals — the minimum columns and rows below a terminal
  header, the narrow-viewport rule, the pane cap — are measured off the live
  terminal. A process that cannot measure a pane cannot place one.

So the sidecar records an *intent*, exactly one open page claims it, that page
runs the split button's own handler, and reports back. The sidecar waits 40s,
deliberately above the store's real worst case — 15s for a page to claim the
intent, then the claimant's own 20s to report — so an expiry means the request
really did lapse untouched, which is what the answer says. Over HTTP the wait is
on the store itself rather than a poll: same process, one condition variable, no
request thread spinning against the server it lives in.

A read that fails mid-wait is swallowed, because a dropped poll is not a failed
split. If the wait then ends having read nothing, the answer says that in those
words and names `list_panes`, rather than the sentence promising the panes are
untouched — only one of the two ways to reach `no_window_available` knows that
nothing happened.

Everything decidable without measuring a pane is decided before any waiting
starts — an unknown agent key, a browser pane on a remote host, an axis that is
not one of the two — so a refusal of that kind costs no TTL.

A refusal is relayed verbatim, with the axis that *would* have worked when
either does. Never a silent retry on the other axis: an agent that asked for a
side-by-side split and got a stacked one has been lied to.

`no_window_available` is what browser mode always answers for a split: the
intent poll runs in a native GridVibe window only, because a browser tab must
not pay for a poll on every page load. `open_window` has a browser-mode fallback
(`webbrowser.open` is a real alternative); there is no equivalent for "measure
this pane".

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
- **The pane gates are the same kind of thing.** On the stdio path a caller's
  identity *is* `GRIDVIBE_SESSION_ID`, which the agent being constrained can
  set, and `list_panes` publishes every pane id. More fundamentally, a local
  agent pane already holds the user's own privileges and GridVibe's loopback
  API: `DELETE /api/sessions/<id>` and the ungated `POST /api/sessions/<id>/shell`
  are one request away and pass no gate at all. What the gates buy is real and
  worth having — an agent *following its instructions* does not end a pane it
  did not make, and getting past them means leaving the tool surface entirely —
  but it is a raised bar, not a wall. None of that carries to the tunnelled path:
  identity there comes from the token registry rather than from the caller, and
  the forward's filter means a remote process cannot reach those ungated routes
  at all.
- **No credential ever reaches a tool result.** Every result is built from an
  explicit field list in `client.py`, and anything whose key looks like a
  secret is dropped at any depth regardless. `list_saved_layouts` is the sharp
  case: the route it reads answers with a *decrypted* SSH password by design,
  and `SAVED_LAYOUT_FIELDS` is what stops it.
- **The MCP endpoint is the POST half of streamable HTTP.** No SSE `GET`
  stream, no `DELETE`, no `Mcp-Session-Id`. The three CLIs that are handed a URL
  today are content with request/response. Both verbs are answered with a
  deliberate `405` and `Allow: POST`, naming what the endpoint is and why the
  other half is absent, rather than Flask's bare method-not-allowed — decided
  before the token is resolved, so it says nothing about whether one is live. A
  *tunnelled* client never reaches it: the forward's filter answers anything that
  is not this pane's own POST with `404`.

## Which CLIs can be handed the sidecar

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

The launcher and the pane header's 🔄 dropdown both read one registry field,
`mcp_supported`, so neither surface can offer a checkbox the other does not.

## SSH panes

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
| Per-pane token | `web/mcp_http.py` → `pane_tokens` — identity cannot cross a machine by inheritance, so it rides in the URL. Minted idempotently, revoked on the pane's own close path, and the registry is bounded (128, oldest evicted) so a revoke that never runs is a bounded leak rather than a permanent one |
| Reverse tunnel + remote config | `web/ssh_tunnel.py` — `request_port_forward` on the pane's existing transport, config placed over SFTP at `~/.gridvibe/mcp-<pane>.json`, mode `0600` |
| Wiring | `terminal_io._establish_mcp_tunnel`, torn down in `_shutdown_connection` |

Nothing is installed on the remote host. The config written there names a URL,
not a command, which is why Codex gets `-c mcp_servers.gridvibe.url=` rather
than the inline command-and-args form a local pane gets.

Every failure costs the pane its tools and never its shell: a forward the remote
sshd refuses, or a config that cannot be written, leaves the pane running and
tells the reader in the terminal. A close landing *inside* the setup is the same
promise: the tunnel is torn down rather than recorded, and the token is revoked
even when the teardown itself raises. A pane whose agent CLI publishes no MCP
mechanism never has `agent_mcp` set by any route, so it opens nothing either.

### What this widens

`sshd` binds the *remote host's own loopback* (`GatewayPorts no`, the default, so
it does that whatever address is requested), never its network, and the port
exists only for the life of that pane's connection. A pane whose box is unticked
— or whose agent CLI has no MCP mechanism to begin with — opens no port, mints no
token and writes nothing on the remote host.

Inside those bounds, be precise about what is exposed. The forwarded channel does
**not** reach GridVibe's port. It reaches a filter in `web/ssh_tunnel.py`, and a
socket to GridVibe is opened only for a request that survives it: one request per
connection, `POST`, and a target equal to *this pane's own* `/mcp/<token>`,
compared with `secrets.compare_digest` on bytes. Exactly `Content-Length` bytes
are read and nothing behind them is forwarded; `Transfer-Encoding`, a repeated
`Content-Length` and obsolete line folding are refused rather than normalised,
because a filter that cannot say where the body ends cannot say that nothing
rides behind it. Bounded at 16 KiB of head, 1 MiB of body, 30s. Refusals say
nothing — `404` for a method or path that is not this pane's, the same answer
either way, so a caller learns neither which routes exist nor whether it guessed
a live token; `400` for framing, `413` for an oversized declared body, `502` when
GridVibe itself cannot be reached — and the warning log prints only the target's
first segment, because `/mcp/<token>` is a credential.

That filter is load-bearing, not defence in depth. On this end of the forward is
GridVibe's whole loopback HTTP API: saved sessions with decryptable credentials,
the destroy tier the tool surface deliberately does not contain, the ungated
twins of every gated route. Their only guard has ever been "you have to be on
this machine", and a plain byte pump would have handed every one of them to the
remote host with the token guarding exactly one route on it.

So what a process on that remote host can reach is this pane's tool surface, and
that is still the create tier acting on *this* machine. Tick the box on a host
whose other processes you would not give that to, and you have given it that.

## Where a launched pane opens

On the machine the calling agent is already on. `launch_panes` states no
connection of its own — it names the pane the call came from
(`origin_session_id`), and `workspaces.resolve_origin_connection` reads the
host, user, port and password off that pane's live session in this process. An
agent is never shown its own pane's credential, so this is the only place the
answer can come from, and nothing of it reaches a response, a preset or a
snapshot.

A remote origin therefore produces an *SSH* group, and two things follow. A
browser pane is refused there rather than silently downgraded to a terminal,
because GridVibe draws that surface locally. The per-pane `shell` choice is
dropped, because a local shell family names a machine the group is not opening
on. An origin pane that has closed is a refusal, not a fall back to this
machine: "here" is exactly the wrong answer, and the one that used to open a
PowerShell pane on a `/home/...` path.

The same read stamps `created_by_session_id` on the panes it makes, which is
what the lineage gate later reads.

## Checking the surface without a running GridVibe

```
make mcp-tools      # print the registered tool surface as JSON
make mcp-config     # rewrite .gridvibe_mcp.json for this install
```
