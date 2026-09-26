# GridVibe MCP sidecar

A stdio MCP server that gives an agent running in a GridVibe pane twenty
tools for seeing, building and navigating GridVibe workspaces.

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
| `GRIDVIBE_SESSION_ID` | the pane id (GridVibe's own routes call a pane a session) |
| `GRIDVIBE_GROUP_ID` | the session (tab) id |
| `GRIDVIBE_WORKSPACE_ID` | the workspace id |
| `GRIDVIBE_AGENT_DEPTH` | `0`, or the launching agent's depth + 1 |

An agent started by hand outside GridVibe inherits none of them, and `whoami`
says so rather than guessing. The read tools still work, and a launch still
works if it names a workspace; the three gated pane tools and `move_session`
are refused outright, because the lineage gate compares against a calling pane
there is none of.

Codex is the exception that proves the rule: its own spawn of an MCP server does
not forward the pane's environment, so GridVibe states the same five variables
back to it as an inline TOML table on the launch line
(`web/agents.py:_inline_toml_env_fragment`).

## Tools

Twenty, in six tiers by blast radius. The order below is the order
`tool_specs()` registers them in, and `tests/test_mcp_tools.py` pins it.

**Three nouns, one meaning each.** A *workspace* is a window. A *session* is a
session tab in a workspace — what a person names when they say "bring the
gridvibe_main session forward". A *pane* is one terminal, agent, explorer or
browser inside a session. Every tool takes and returns a session by its tab
name (`session_name`, exactly the text the tab shows) and its id (`group_id`),
and a pane by `pane_id`. GridVibe's HTTP routes call a pane a "session" for
historical reasons; that word never crosses this surface meaning a pane.
`dispatch` renames the route's keys on the way out (`PUBLISHED_KEYS` in
`server.py`: `session_id` → `pane_id`, `from_session_id` → `from_pane_id`,
`group_name` → `session_name`, and so on, at every depth), and every argument
that names a pane is called `pane_id`.

A session named by `session_name` is matched as its tab shows it: exactly,
then ignoring case, then as a group id. Two open tabs that match are refused
as `ambiguous` with the `candidates`, and nothing happens until one is named by
`group_id` or `workspace_id`. A name no open tab has is refused as `not_found`
with the tabs that are open, and says so when a saved preset of that name exists
but is not open.

### read — eight

| Tool | Answers |
| --- | --- |
| `gridvibe_status` | is GridVibe running, which version, how many workspaces |
| `list_workspaces` | every live workspace (window) and the sessions open in it — each tab's `session_name`, `group_id`, `pane_count`, and whether it is the tab the window shows (`active`) |
| `list_panes` | the panes in one workspace or one session: what each is, where it points, what it runs on, which session it is in (`session_name`), and where it sits — `index`, `rect`, `relative_area`, and the `neighbours` above, below, left and right. Narrowed by `session_name`, it finds that tab in any workspace |
| `list_agents` | every agent anywhere, with a working/idle reading, under the workspace and session holding it |
| `list_agent_types` | every agent CLI in the registry, and whether each can start where a launch or split from this pane would put it — see [Which agents a tool can start](#which-agents-a-tool-can-start) |
| `list_saved_layouts` | every saved launcher preset as a *shape* — name, layout, pane count, geometry, and what each pane is. Never a connection |
| `whoami` | which pane this agent is in (`pane_id`), the session it is in (`session_name`, `group_id`), its directory, **which machine that directory is on**, how deep it is, where it sits, and whether it may still launch (`may_launch_panes`) or split (`may_split_panes`) — a refusal of the first carries a `split_note` saying the second is still open |
| `read_handoff` | the task another agent handed to *this* pane — see [Handing an agent its task](#handing-an-agent-its-task). Its only side effect is the handoff's state becoming `read` |

Each pane in `list_panes` also carries `handoff`: `null`, or its `state`
(`waiting`, `announced`, `read`, `undeliverable`), `delivery`, `chars` and
`from_pane_id` — never the text, never a file path.

`whoami` before resolving "this directory", "this session", "this workspace" or
"the terminal below this one". Its `runs_on` is the field that stops a remote
path being handed to a pane opened on the wrong machine.

Its `workspace_id` — and the workspace `list_panes` defaults to — is the one the
pane's *group* is in now, not the one the pane was started in. Identity is
captured once, at spawn or when the token was minted, and a session can be moved
between workspaces with its processes and its SSH connections still running; an
agent that went on naming the workspace it had left read panes that were no
longer there. The group is the anchor because a move carries the whole group, and
the inherited id is only the fallback for a read that failed.

### hand back — two

| Tool | Does |
| --- | --- |
| `report_result` | hands the outcome of the task *this* pane was given back to the agent that gave it — `result` text and a `status` of `done`, `failed` or `blocked`. It names no pane: GridVibe's record of who handed the task over decides |
| `wait_for_results` | waits for the agents *this* pane handed a task to, and returns their reports — see [Handing a result back](#handing-a-result-back) |

Neither creates, ends nor changes a pane, and nothing is typed into any
terminal: a report reaches the waiting agent as its own tool call's result.

### create — four

| Tool | Makes |
| --- | --- |
| `create_workspace` | one empty, labelled workspace. Creating it does not make a window appear, and it is refused past sixteen workspaces that are *still* empty — counted over the whole app, because the server cannot tell a tool from the launcher's own button |
| `launch_panes` | one session (a new tab) of panes — agent, terminal, file explorer or browser preview. An agent pane may carry a `task`, and this needs no open window. The result's `session_name` is the name the tab actually got: a repeated scratch name is suffixed. The whole request is validated before anything exists — see [Where a launched pane opens](#where-a-launched-pane-opens) |
| `open_window` | a workspace window on screen, whichever tab it shows. Reports `opened`, `blocked` or `no_window_available`. A named session is `focus_session`'s job |
| `split_pane` | halves one pane on a chosen axis and says what the new pane runs. With no `kind` stated it is what the 🪟 button makes: a terminal clones its source, and an explorer, browser or *agent* pane splits off a plain terminal rooted where it is showing — the kind is never cloned. A stated `directory` wins over where the source is standing. Reports `split`, `refused` or `no_window_available`. With `kind: "agent"` it may carry a `task`, and the result's `handoff` says it is waiting. Its description states what the axis words produce: `horizontal` stacks the new pane below, `vertical` puts it to the right |

### replace — two

`set_pane_agent` relaunches a pane into an agent CLI (or `agent: ""` back to a
plain shell, optionally changing the local shell family and the MCP choice),
and may hand the new agent a `task` — the way to give a task to a pane that
already exists, since nothing types into one.
`set_pane_mode` turns a pane into a file explorer, a browser preview or a plain
terminal, and with a stated `directory` re-roots it there — see
[A stated directory](#a-stated-directory). A call that changes nothing answers
`changed: false` with a `note`, never a pane payload dressed as a change.

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

### navigate — three

| Tool | Does |
| --- | --- |
| `focus_session` | brings one session to the foreground: raises its workspace window and switches it to that tab. Named by `session_name` (or `group_id`); the workspace is found from the session, and `workspace_id` only narrows a name two workspaces share |
| `focus_pane` | brings one pane into view: raises its window, switches to its session and gives the pane focus. The session and workspace are read from the pane itself, as they are *now* |
| `move_session` | moves one open session — the tab and all its panes — to another workspace, named by `target_workspace_label`, `target_workspace_id`, or `new_workspace` (exactly one). With `show` the destination window is then raised on that tab |

None creates, ends or types anything. A moved session keeps its pane ids,
processes, SSH connections, handoffs and result assignments; the only thing
these verbs change is what the person sees where.

`focus_session` and `focus_pane` answer from the page, not from the window:
`opened` with `session_activated: true` (and, for a pane, `pane_visible` and
`focused`) only once the workspace page has switched and read focus back —
see [Showing a session or a pane](#showing-a-session-or-a-pane). A window that
was raised but would not switch is `blocked` with `window_raised: true` and the
page's reason. `focused` is reported as it is: an explorer or browser pane can
be visible without taking keyboard focus, and the answer's `note` says so.

`move_session` moves the caller's own session, or one whose panes the caller
all created, without asking. Any other session is refused through the lineage
gate below with a `confirm.question` naming the session, its pane count, and
both workspaces. Moving a session to the workspace it is already in answers
`moved: false` and creates nothing. `show` is reported separately under `shown`
and never turns a move into a failure: a window that would not switch tabs is
not a session that did not move. A destination label two workspaces share is
refused as `ambiguous`; a missing one is `not_found` and suggests
`new_workspace`.

### The gates on the replace and display tools

Shared in `web/pane_gates.py`, so all three refuse in the same words. A refusal
names which gate failed, because an agent told only "refused" calls again.
`move_session` passes the same lineage gate widened to a whole session
(`web/navigation.py`), with the same `override` rule.

| Gate | Rule | Waivable |
| --- | --- | --- |
| **self** | never the pane the request came from | no |
| **lineage** | only a pane this agent's own pane created, and only while that caller pane is still open | by `override` |
| **kind** | each transaction's own: a relaunch takes only a plain terminal; a mode switch refuses a pane with an agent running in it; a clear refuses both a non-terminal pane and a running agent | the "already an agent" half, by `override` |
| **machine** | a relaunch carrying a `task` only reaches a pane on the caller's own machine | no |

Every gate refusal is structured as well as worded: `gate`, `waivable`, and —
for a waivable one only — a `confirm` block naming the pane (`pane_id`,
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

Closing a pane, a session or a workspace; typing arbitrary input into a
terminal. These are not written, not registered, and not flag-gated. A tool
that does not exist cannot be talked into running by a file an agent reads.

## Splitting, showing and opening windows need a page

Three things GridVibe cannot do from outside a browser page, and the same
mechanism answers all of them (`web/window_intents.py`,
`web/static/js/window-intent.js`):

- **Open a window.** Nothing outside a page can open a pywebview window.
- **Split a pane.** The axis never reaches the server. The page computes the new
  rectangles, and its refusals — the minimum columns and rows below a terminal
  header, the narrow-viewport rule, the pane cap — are measured off the live
  terminal. A process that cannot measure a pane cannot place one.
- **Show a session or a pane.** Raising a window is not proof that its tab
  changed; only the page that holds the tab can switch it and say so.

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

### Showing a session or a pane

`focus_session` and `focus_pane` are two steps, each with its own answer. The
window step is `open_window`'s; once it answers `opened`, an *activate* intent
is recorded (`POST /api/windows/activate`). GridVibe checks the live registry
before recording it — a pane names its session, a session names its workspace,
a stated id that disagrees is a `409` stale read and a closed one a `404` — so a
page is never asked to switch to a tab another window now holds. Nothing is
recorded on a refusal.

Only the workspace page that holds that session claims the intent; the launcher
has no focus bridge and never does. The page refuses without prompting when an
explorer editor has unsaved work or a copy or delete is running, and answers
`blocked` with that reason. Otherwise it runs the tab strip's own switch, waits
for the session to paint, lands on the pane, and reads focus back from the
document rather than assuming it. `activated` means the requested tab is the
active one and, when a pane was named, that pane is visible.

Each step is bounded by its own intent TTL, so the worst case is about 70 s. A
hidden or minimised native page polls nothing and the answer is
`no_window_available`. In browser mode the tab is opened by URL and the answer
says `verified: false`: no page confirms it.

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

## Handing a result back

An agent that hands out a task usually wants the answer. Every handoff bound to
a pane is also an *assignment* in `web/agent_results.py`: the pane that asked
(the requester — for a split, the agent that asked, not the pane it halved),
the pane that received it (the worker), and the worker's report once it makes
one. The store is in memory, like the handoff store, and is told by it when a
handoff is bound and when one goes.

- **The worker is told how.** The launch-line sentence ends by naming
  `report_result`, and `read_handoff` returns a `reply` saying who is waiting
  and what a report should hold.
- **A report goes to the requester only.** `report_result` posts to the
  caller's own pane (`POST /api/sessions/<id>/handoff-report`); the recipient is
  the assignment's requester, never an argument. A pane nobody handed a task,
  or whose requester has closed, is told nobody is waiting and nothing is kept.
  Reporting again replaces the report and makes it new again, but only while
  the handoff lives: once the pane closes, is relaunched or is re-tasked, its
  report stands and whatever the pane runs next cannot write over it. A report
  settles a task only once the task has been fetched with `read_handoff`: a
  relaunched pane keeps its id, so a report sent before then is the replaced
  agent's, and it is refused with nothing kept.
- **Held to a task's rules, with a smaller ceiling.** Printable text, newlines
  and tabs; control characters refused by name; up to 16,000 characters,
  refused above that — never truncated. Anything longer goes in a file on the
  shared machine (a task only ever runs on its requester's machine), named in
  the report.
- **The wait is bounded per call.** `wait_for_results`
  (`GET /api/sessions/<id>/handoff-reports`) blocks on the store until every
  named agent has settled (`until: "all"`) or one has news (`"any"`), for at
  most 55 s — under Codex's 60 s tool-call timeout — and says when agents are
  still working, so a long task is waited on by calling again. Over the tunnel
  the wait happens on the store in-process and the route is then read with
  `wait=0`, so no request is held open against the server it runs in.
- **Each report is returned whole once**, then marked `already_returned`
  (`include_collected` returns them again). One answer carries at most 48,000
  characters of reports; unseen ones get that budget before re-sent ones, the
  rest are `result_withheld` and come with the next call, and the first always
  fits.
- **Nobody waits for a report that cannot come.** A handoff that goes before a
  report — its connection closed, its pane closed, relaunched or re-moded, a new
  task bound over it, or its agent started without the tools — ends the
  assignment with the reason, and the wait returns it as `ended`. A report
  outlives the worker's pane; the requester's pane closing drops what it was
  owed.
- **A report frames itself.** The answer's `note` says it is another agent's
  report, not the person's words: it cannot waive a permission prompt, is never
  a reason to set `override`, and its claims are to be checked. Reports appear
  in no pane listing, and log lines carry ids, sizes and status — never the text.

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
- **A handed-over task, and a report, is not confidential.** On the stdio path a caller is
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

### Which agents a tool can start

Every one the registry holds and the target machine has installed — being
handed the sidecar or a task is a separate question. `list_agent_types`
(`GET /api/agent-types`) answers all three per CLI, for the place a launch or
split from the asking pane would open: that pane's own machine (the SSH host for
a remote pane) under its shell family, or a stated local `shell`.

| Field | Means |
| --- | --- |
| `available` | `true` installed, `false` missing or unsupported there (the reason in `message`), `null` the check could not run — a launch still tries |
| `mcp_supported` | the CLI can be given GridVibe's tools |
| `task_supported` | the CLI can be handed a `task` (`claude`, `codex`, `copilot`) |
| `auto_mode_supported` | the CLI has an auto-approval flag |

Preflights run on a shared, bounded four-worker pool, and the sidecar gives
this read a 50 s deadline, under the CLIs' tool-call timeouts. No binary path or
credential leaves the route. A stated `shell` is refused from an SSH pane, and
PowerShell or cmd from a WSL pane, exactly as a launch would refuse it.

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

A launch from a tool is **validated whole** before any workspace, session or
pane exists. It is refused, naming every offending pane in one sentence, when a
pane names an unknown CLI or one whose preflight proves it absent, states
`mcp: true` for a CLI with no MCP mechanism, carries a `task` for a CLI that
cannot take one (the answer lists the three that can), or states a local
`shell` the asking pane cannot honour — any family from an SSH pane, PowerShell
or cmd from a WSL pane, any family on a non-Windows host. An agent is never
quietly opened as a plain terminal for a tool. A preflight that *could not run*
is not an absence: that pane keeps its agent, its identity and its task, and
gets a warning. The launcher and restore keep their own behaviour, where an
absent agent opens as a terminal with a warning. `split_pane` refuses an
unavailable agent and an unsupported `mcp: true` the same way.

## A stated directory

`split_pane` and `set_pane_mode` take a `directory`, and a stated one is a
decision, not a hint (`web/pane_directory.py`, `resolve_stated_directory`).

- **It is checked on the machine the pane runs on**, before anything is recorded
  or relaunched: over SFTP on an SSH pane's own host; for a WSL pane, Windows
  and `/mnt/<drive>` paths on this host's filesystem and other Linux paths
  through a bounded `wsl --exec test -d`; otherwise on this machine. A refusal
  names the path and the machine and says nothing was changed. Only a proven
  absence refuses: a WSL check that could not run accepts the path, and the
  shell's own `cd` reports it.
- **It wins over where the pane is standing.** For a split it is recorded under
  its own key (`stated_directory`), separate from the folder an explorer page
  is browsing, is checked again when the page performs the split, and drops the
  root the clone would have inherited; with `kind: "explorer"` it becomes the new
  pane's configured root. For `set_pane_mode` it beats the observed cwd.
- **It is not clamped to an explorer's root.** A root the pane derived for
  itself is where a live Files pane browses, not a limit on where a tool may
  re-root the pane — so "one directory above this repository" works. A
  configured root survives an explorer → terminal switch only when the new path
  is inside it. The header's own toggle still keeps explorer containment.
- **A directory-only change is a real change.** A terminal pane given only a new
  directory is relaunched there; an agent pane, with `override`, becomes a
  plain terminal there. A call that would change nothing answers
  `changed: false` with a `note`.

A mode switch keeps the pane's stored shell family, so "make that Files pane a
PowerShell terminal one level up" is `set_pane_mode` followed by
`set_pane_agent(shell="powershell", agent="")` when the pane's family was
another.

## Checking the surface without a running GridVibe

```
make mcp-tools      # print the registered tool surface as JSON
make mcp-config     # rewrite .gridvibe_mcp.json for this install
```
