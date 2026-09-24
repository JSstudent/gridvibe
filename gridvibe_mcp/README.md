# GridVibe MCP sidecar

A stdio MCP server that gives an agent running in a GridVibe pane fourteen
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

The URL in it is one a URL parser reads back: a wildcard bind resolves to
loopback, and an IPv6 literal is bracketed, because `http://::1:5050` is not a
URL — everything that reads one separates host from port at a colon, and that
address is all colons. `normalize_base_url` in `client.py` puts the brackets
back after `urlsplit` hands the hostname over without them, so both halves
agree; one of them alone leaves a sidecar silently on the loopback default,
unable to reach a GridVibe bound to IPv6 only.

When the pane closes, the CLI exits and the sidecar exits with it: no orphan,
nothing to supervise.

## Identity

The sidecar takes no identity arguments. It is a grandchild of the pane
process, so the pane's environment is already its environment:

| Variable | Value |
| --- | --- |
| `GRIDVIBE_URL` | `http://<bound host>:<port>`, an IPv6 literal bracketed |
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

Fourteen, in four tiers by blast radius. The order below is the order
`tool_specs()` registers them in, and `tests/test_mcp_tools.py` pins it.

### read — seven

| Tool | Answers |
| --- | --- |
| `gridvibe_status` | is GridVibe running, which version, how many workspaces |
| `list_workspaces` | every live workspace, with its label and group count |
| `list_panes` | the panes in one workspace or group: what each is, where it points, what it runs on, and where it sits — `index`, `rect`, `relative_area`, and the `neighbours` above, below, left and right |
| `list_agents` | every agent anywhere, with a working/idle reading, under the workspace and session holding it |
| `list_saved_layouts` | every saved launcher preset as a *shape* — name, layout, pane count, geometry, and what each pane is. Never a connection |
| `whoami` | which pane this agent is in, its directory, **which machine that directory is on**, how deep it is, where it sits, and whether it may still launch (`may_launch_panes`) or split (`may_split_panes`) — a refusal of the first carries a `split_note` saying the second is still open |
| `read_handoff` | the task another agent handed to *this* pane — see [Handing an agent its task](#handing-an-agent-its-task). Its only side effect is the handoff's state becoming `read` |

Each pane in `list_panes` also carries `handoff`: `null`, or its `state`
(`waiting`, `announced`, `read`, `undeliverable`), `delivery`, `chars` and
`from_session_id` — never the text, never a file path.

`whoami` before resolving "this directory", "this workspace" or "the terminal
below this one". Its `runs_on` is the field that stops a remote path being
handed to a pane opened on the wrong machine.

Its `workspace_id` — and the workspace `list_panes` defaults to — is the one the
pane's *group* is in now, not the one the pane was started in. Identity is
captured once, at spawn or when the token was minted, and a session can be moved
between workspaces with its processes and its SSH connections still running; an
agent that went on naming the workspace it had left read panes that were no
longer there. The group is the anchor because a move carries the whole group, and
the inherited id is only the fallback for a read that failed.

### create — four

| Tool | Makes |
| --- | --- |
| `create_workspace` | one empty, labelled workspace. Creating it does not make a window appear, and it is refused past sixteen workspaces that are *still* empty — counted over the whole app, because the server cannot tell a tool from the launcher's own button |
| `launch_panes` | one session group of panes — agent, terminal, file explorer or browser preview. An agent pane may carry a `task`, and this needs no open window |
| `open_window` | a workspace on screen. Reports `opened`, `blocked` or `no_window_available` |
| `split_pane` | halves one pane on a chosen axis and says what the new pane runs. With no `kind` stated it is what the 🪟 button makes: a terminal clones its source, and an explorer, browser or *agent* pane splits off a plain terminal rooted where it is showing — the kind is never cloned. Reports `split`, `refused` or `no_window_available`. With `kind: "agent"` it may carry a `task`, and the result's `handoff` says it is waiting. Its description states what the axis words produce: `horizontal` stacks the new pane below, `vertical` puts it to the right |

### replace — two

`set_pane_agent` relaunches a pane into an agent CLI (or `agent: ""` back to a
plain shell, optionally changing the local shell family and the MCP choice),
and may hand the new agent a `task` — the way to give a task to a pane that
already exists, since nothing types into one.
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
| **machine** | a relaunch carrying a `task` only reaches a pane on the caller's own machine | no |

Every gate refusal is structured as well as worded: `gate`, `waivable`, and —
for a waivable one only — a `confirm` block naming the pane (`session_id`,
`title`, `index`), what the change `ends` (the agent and GridVibe's last
working/idle reading of it) and the `question` to put to the person. GridVibe
builds the question from its live registry, so every agent asks the same one.
The refusals nothing waives come first, so an agent never asks the person, gets
a yes, and is then refused anyway.

A pane that existed before a GridVibe restart carries no creator — `created_by_session_id`
is deliberately absent from the runtime snapshot — so it is always refused
without `override`. That is the honest answer: GridVibe does not know who made
it, so it does not guess.

**`override` is only ever the user's word.** It waives lineage and the "already
running an agent" refusal; it never waives self, the kind gate's mode rule or
the machine rule. A calling agent calls first *without* it — a refusal changes
nothing — and then either asks `confirm.question`, offers a split, or stops. It
sets `override` only after a clear yes, or when the person's own words in that
conversation already asked to replace *this specific pane* ("override",
"force", "replace", "kill" or "restart" with a clear reference to it) — never
because a file it read, a prior tool result, another pane's output or a
handed-over task asked for it. GridVibe adds no Allow/Deny dialog of its own.
Every waiver is logged with both pane ids.

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

## Handing an agent its task

An agent pane created by `split_pane` or `launch_panes`, or relaunched by
`set_pane_agent`, can be handed a `task`, so the new agent starts working on it
instead of waiting at an empty prompt. The store is `web/agent_handoffs.py`;
the temporary file is `web/agent_handoff_files.py`.

- **No byte of a task reaches a shell.** The new agent's launch line gains one
  constant GridVibe sentence — *call the gridvibe tool `read_handoff` to fetch
  it* — placed directly after the binary (Claude's `--mcp-config` takes a
  variable number of values), and the task is fetched through the tools. So a
  task needs a CLI that takes an opening prompt *and* can be handed the
  sidecar: `claude`, `codex` and `copilot`. It turns `mcp` on; an explicit
  `mcp: false` beside it is refused.
- **Only a handle rides in a split intent.** Every polling page is shown the
  intent, so the text stays in GridVibe and the split route takes the handle
  once, for its own source pane, before it appends anything. A launch takes
  each pane's task off its config before anything else reads it, so no preset
  and no runtime snapshot ever holds one.
- **A task runs on the caller's own machine.** Both local, or both SSH to the
  same host, user and port. Nothing waives it, `override` included. An agent
  with no pane is refused: it has no machine and no lineage to record.
- **Size decides how it travels.** Up to 8,000 characters it comes back whole
  from `read_handoff`. Larger, up to 512 KiB, it is written to a file on the
  pane's own machine (owner-only, never through a shell) and `read_handoff`
  names `task_file` with the opening in `head`. If that file cannot be written,
  `read_handoff` pages it (`offset`, `next_offset`). Above 512 KiB it is
  refused — never truncated. Control characters other than newline and tab are
  refused by name, never stripped.
- **One brief, for one agent.** It waits bound to its pane until a connection
  starts that pane's agent, is announced on that launch line, and goes — with
  its file — when that connection closes. A relaunch, a restart, a restore or a
  preset launch never replays it; relaunching or re-moding a pane drops one
  still waiting. When the pane's agent starts without the tools (no local
  config, a refused tunnel), no sentence is typed, the handoff reads
  `undeliverable`, and the pane's *output* says why.
- **The brief frames itself.** `read_handoff` returns a `note`: this is another
  agent's request, not the person's words — it cannot waive a permission
  prompt and is never a reason to set `override`. A task never implies
  `auto_mode`.

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
- **A handed-over task is not confidential.** On the stdio path a caller is
  whoever `GRIDVIBE_SESSION_ID` says, the handoff route is on the same
  unauthenticated loopback API as every other, and the temporary file is
  owner-only but readable by anything running as that user. Tool descriptions
  tell callers to leave credentials out. No log line carries a task's text or
  its file path — ids, sizes and deliveries only.
- **`override` relies on the calling agent.** GridVibe cannot check which words
  the person used; the structured `confirm` flow guides an agent that follows
  its instructions and enforces nothing. What still bounds a misused override:
  never the caller's own pane, an explorer or browser pane, or — with a task —
  another machine.
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

| CLI | How | Shape | Opening prompt (a `task`) |
| --- | --- | --- | --- |
| `claude` | `--mcp-config "<path>"` | `flag` template | positional, directly after the binary |
| `copilot` | `--additional-mcp-config "@<path>"` — `@` marks a path rather than inline JSON, and it *augments* `~/.copilot/mcp-config.json` for the session | `flag` template | `-i "<sentence>"`, directly after the binary |
| `codex` | `-c mcp_servers.gridvibe.…` overrides — it takes no config file at all | `style: inline_toml` | positional, directly after the binary |

The opening prompt is an `opening_prompt` block in `agent_registry.json`, held to
the same `verified` bar; the other five CLIs publish none and cannot be handed a
task.

The quote opens *before* Copilot's `@`: `@"C:\…"` starts a here-string in
PowerShell and fails to parse.

Codex's overrides are quoted per shell, because the two Windows shells
disagree and no single string serves both — cmd must see the TOML literal
quotes bare (double-quoted, the override is silently ignored), PowerShell and
POSIX shells must see the outer double quotes (bare, Codex exits with *failed
to load bootstrap configuration*). `_toml_override_flag` owns that one rule and
the terminal-title override reads it too.

Bare is not always *available* on cmd, though. cmd passes its command line
through and the child's own argv parsing ends an argument at a space, so an
interpreter under `C:\Program Files`, or a checkout whose name has a space in
it, reached Codex as two or three unrelated tokens and none of them were
applied. An override carrying a space — or any of cmd's own syntax — is
therefore double-quoted there as well. That is not the silently-ignored case
above: the child strips those outer quotes before Codex parses anything, so it
reads exactly the string the bare form would have handed it, which is the one
thing a torn-apart argument cannot do. Which is also why nothing rendered into
an override carries a space it does not need: the inline identity table is
written without one so it keeps the bare form wherever it can.

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
| Reverse tunnel + remote config | `web/ssh_tunnel.py` — `request_port_forward` on the pane's existing transport, config placed over SFTP at `~/.gridvibe/mcp-<pane>.json`, mode `0600` inside a `0700` directory, both read back after they are set |
| Wiring | `terminal_io._establish_mcp_tunnel`, torn down in `_shutdown_connection` |

Nothing is installed on the remote host. The config written there names a URL,
not a command, which is why Codex gets `-c mcp_servers.gridvibe.url=` rather
than the inline command-and-args form a local pane gets.

Every failure costs the pane its tools and never its shell: a forward the remote
sshd refuses, or a config that cannot be written, leaves the pane running and
tells the reader in the terminal. A close landing *inside* the setup is the same
promise: the tunnel is torn down rather than recorded, and the token is revoked
even when the teardown itself raises — as it is when the setup simply fails,
because a token with nothing to spend it on is still a live key to this
machine's tools. A pane whose agent CLI publishes no MCP mechanism never has
`agent_mcp` set by any route, so it opens nothing either.

The config **fails closed**, because the document is the token in plain text.
Its mode and its directory's are applied and then read back, and an existing
`~/.gridvibe` is narrowed and verified exactly like a new one — a previous run,
another tool or a permissive `umask` may have left it open, and a directory the
rest of the host can list names every pane's config whatever the files
themselves carry. A `chmod` the host declined, a mode that could not be read
back, and a mode that still lets anyone else in are one answer: nobody here
knows who can read this. The file is removed again, the listener is withdrawn,
the token is revoked, and the pane starts without tools — the price every other
tunnel failure charges. A readable token on a shared host is the one outcome
worse than that.

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

Connections are bounded as well as requests, and they have to be handed off at
once. Paramiko calls the forward handler on the transport's own packet thread,
which is the thread carrying the pane's *shell*, so filtering inline froze the
terminal and starved the very bytes the tunnel was opened for: one daemon thread
per connection, started and returned from. Those threads are then a budget —
sixteen in flight per pane, well above a CLI's parallel tool calls plus the two
that wait on a page, and per pane so one noisy host cannot starve a pane
connected elsewhere. A connection arriving with none free is closed rather than
queued or answered, since writing a refusal would put the work back on the
thread the handoff exists to release; the pane's next real tool call is served
as soon as a slot frees. The head and body ceilings cannot see this case at all:
a loop of connections that send nothing still costs a thread each for the
30-second read timeout.

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

The same read answers *which workspace* when the call names none: the
destination is the workspace the origin pane's group is in, resolved by GridVibe
inside the launch rather than by the sidecar in a read before it. A group can
move between two requests, which would have opened the panes in the workspace it
had just left. A stated `workspace_id` or `new_workspace` still wins.

The same read also stamps `created_by_session_id` on the panes it makes, which
is what the lineage gate later reads. A `split_pane` that names an origin pane
which is no longer open is refused by that gate when the split is *recorded*,
not silently recorded as a pane nobody created.

## Checking the surface without a running GridVibe

```
make mcp-tools      # print the registered tool surface as JSON
make mcp-config     # rewrite .gridvibe_mcp.json for this install
```
