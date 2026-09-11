# GridVibe MCP Control Plane — Analysis and Phase 0

Status: proposal (r&d scratch — not a contract).
Scope: exposing GridVibe's workspace/session/agent control surface to agents
running inside GridVibe panes, over MCP.

---

## 1. The idea in one paragraph

An agent CLI running in a GridVibe pane today can see one thing: its own
terminal. It cannot tell what workspace it is in, cannot open a sibling pane to
run a build in, cannot start a second agent on a subtask, and cannot tell
whether the agent two panes over is still working. GridVibe already knows all of
that and already exposes it over a local HTTP API — the browser UI is nothing
but a client of it. An MCP server is the adapter that lets an agent be a second
client of the same API, with the same vocabulary the UI uses.

The important framing: **this is an adapter, not a new capability layer.** Almost
everything the first phase needs already exists and is already reachable. The
work is in the three places where it is not.

---

## 2. What already exists

### 2.1 The control plane is the HTTP API

`web/api.py` publishes roughly 80 HTTP routes plus a Socket.IO channel. The
launch path in particular is already the single one — `POST /api/sessions`
delegates to `launch_session_group()` in `web/workspaces.py`, and
restore-after-restart builds its panes through the identical function. An MCP
tool that posts there is not a second launch path; it is a third caller of the
first one.

The routes that matter for agent-facing control:

| Verb | Route | What it gives the MCP |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness + version; server discovery |
| `GET` | `/api/workspaces` | Every user-visible live workspace, with group counts |
| `POST` | `/api/workspaces` | Create one labelled workspace (claims the label) |
| `PATCH`/`DELETE` | `/api/workspaces/<id>` | Rename / close (`?forget=true` drops the snapshot) |
| `POST` | `/api/sessions` | **Launch a group of panes** into a new or named workspace |
| `GET` | `/api/sessions` | Panes, filterable by `workspace_id` / `group_id` |
| `POST` | `/api/sessions/<id>/split` | Append a pane beside an existing one |
| `POST` | `/api/sessions/<id>/mode` | terminal ⇄ explorer ⇄ browser |
| `POST` | `/api/sessions/<id>/shell` | Change shell family / agent CLI in place |
| `DELETE` | `/api/sessions/<id>`, `/api/sessions?group=` | Close a pane / a group |
| `GET` | `/api/dashboard` | **Every agent anywhere, with a working/idle reading** |
| `GET` | `/api/session-groups` | Groups in a workspace |
| `POST` | `/api/session-groups/<id>/move` | Move a tab between workspaces |
| `GET` | `/api/explorer/<id>/…` | Bounded file/Git reads on an explorer pane |

`GET /api/dashboard` deserves a specific note: it is already exactly the
"what are all my agents doing" read an orchestrating agent wants, it is already
composed in one pass over one lock ordering, and it already publishes from a
fixed field list (`PANE_FIELDS` in `web/dashboard.py`) that deliberately excludes
credentials. It is the single highest-value read tool and needs no server work at
all.

### 2.2 A local process can already call it

`_reject_cross_origin_writes()` in `web/app.py` rejects a state-changing request
whose `Origin` header names a netloc outside the allowed set — and returns `None`
(allow) when there is **no** `Origin` header at all. That is deliberate: it is
what lets pywebview and non-CORS callers write. A local Python process using
`urllib.request` sends no `Origin`, so it passes.

There is no authentication on the API. The security model is entirely
"loopback bind + same-origin browser guard".

**This means an MCP wrapper adds no network reachability that does not already
exist.** An agent with shell access in a pane can `curl http://127.0.0.1:5050/api/sessions`
today. What the MCP adds is discoverability, a typed vocabulary, and — this is
the part that needs designing — a place to put a policy that does not exist today.

### 2.3 Pane vocabulary

A launch request's per-pane dict is the whole vocabulary an MCP `launch_panes`
tool needs to speak:

- `connection_mode` (group-level): `"ssh"` or `"wsl"`. `"wsl"` means *local* —
  cmd, PowerShell, or a WSL distro, selected per pane by `use_powershell` /
  `use_wsl` / `distribution`.
- `startup_mode` (per pane): `"terminal"`, `"agent"`, `"explorer"`, `"browser"`.
  `"browser"` is local-only; `"explorer"` works on both transports.
- An agent pane is `startup_mode: "agent"` + `initial_command_mode: "agent"` +
  `agent_selection: "<registry key>"` + `initial_command: "<registry key>"`,
  with an optional `agent_auto_mode` boolean that appends the CLI's own
  auto-approve flag (`web/agents.py::_compose_agent_startup_command`).
- Registry keys, from `agent_registry.json`: `claude`, `codex`, `copilot`,
  `opencode`, `kilo`, `kimi`, `grok`, `hermes`.
- `layout` is normalized against the pane count (2 → vertical/horizontal,
  3 → + split, 4+ → grid), and the count is capped by `terminal.max_sessions`
  (default 16).
- Destination is `workspace_id`, or `new_workspace: true` + `workspace_label`.

---

## 3. The three real gaps

Everything above is free. These three are the actual engineering.

### Gap A — nothing outside a page can open a window

Creating a workspace over HTTP creates a *record*. It does not make anything
appear on screen. Opening a window is `openWorkspaceWindow()` in
`web/static/js/workspaces.js`, which is page-side and forks two ways:

- **Native mode**: `window.pywebview.api.open_workspace_window(...)` — a JS
  bridge method on `GridVibeApi` in `web/webview_launcher.py`. There is no HTTP
  route to it, by design; the bridge is reachable only from a loaded page.
- **Browser mode**: a named `window.open(...)`, which browsers grant only on a
  user gesture. An unattended open is pop-up-blocked, and `workspaces.js`
  already documents and reports that refusal.

So an MCP-created workspace is invisible until something that is already a page
acts on it. This is the one gap the smoke test forces us to close.

**Proposed mechanism — a one-shot window intent, claimed by a page.** The
codebase already has this exact pattern for landing a dashboard row on its own
pane: a stored one-shot request with a TTL, which exactly one window may claim
(`tests/test_dashboard_targeting.py`). Reuse the shape:

1. `POST /api/windows/open {workspace_id, group_id?}` stores an intent with a
   short TTL (~15 s) and returns its id.
2. Open pages poll `GET /api/windows/intents` and
   `POST /api/windows/intents/<id>/claim`. The claim succeeds exactly once; the
   winner calls the existing `openWorkspaceWindow()` and nothing else changes.
3. The MCP tool polls the intent's state and reports one of three honest
   outcomes: `opened`, `no_window_available` (nothing was open to claim it), or
   `blocked` (browser mode refused the pop-up).

Why polling rather than a Socket.IO push: **the launcher page has no Socket.IO
client at all** (`templates/index.html` loads no socket transport; it is
poll-based). The launcher is also the page most likely to be open when an agent
asks for a new window. A slow poll is established practice here — the dashboard
button's working-agent badge already runs one. A socket push into a "control"
room can be added later for the workspace windows that do hold a socket; the
claim endpoint is what keeps a double-delivery from opening two windows.

*Cheaper alternative if this is judged too much for Phase 0*: skip window
opening entirely, have the MCP return "workspace `test` created with 3 panes —
open it from the launcher", and let the user click. It is honest and it costs
nothing, but it fails the smoke test as written.

### Gap B — a pane does not know who it is

For `whoami`-style tools ("split *my* pane", "what workspace am I in", "list my
siblings") the agent needs its own identity. Nothing currently tells it.

The injection point exists and is clean: local panes are spawned with an
environment built by `_local_shell_integration(shell_kind, command, dict(os.environ))`
in `web/terminal_io.py`, which already merges in the shell-integration entries
from `shell_integration_environment()` in `web/terminal_cwd.py` (including the
`WSLENV` forwarding a WSL pane needs for a variable to survive the boundary).
Adding four variables there is a small, local change:

```
GRIDVIBE_URL=http://127.0.0.1:5050
GRIDVIBE_SESSION_ID=<pane id>
GRIDVIBE_GROUP_ID=<group id>
GRIDVIBE_WORKSPACE_ID=<workspace id>
```

**SSH panes are a genuine exception.** `sshd` forwards only what `AcceptEnv`
permits, which is why the remote shell-integration hook is *typed* into the
shell and then scrubbed from the visible buffer (`_arm_ssh_startup_scrub`). The
same route is available for identity, but it is more machinery and it writes into
the user's remote shell. Phase 0 should cover local panes and say plainly that a
remote pane's agent gets no identity yet.

### Gap C — an SSH pane cannot reach the loopback API

An agent on a remote host has no route to `127.0.0.1:5050` on the user's
machine. Three options, none free:

1. **Remote port forward** — GridVibe's own SSH connection requests
   `-R 5050:127.0.0.1:5050` on connect. Cheapest, but puts the unauthenticated
   API on a remote host's loopback, where every user on that host can reach it.
   Needs a token before it is acceptable.
2. **Proxy through the pane's existing channel** — an MCP transport tunnelled
   over the terminal is invasive and fights the PTY.
3. **Out of scope** — remote agents get no MCP.

Phase 0 takes option 3. Option 1 becomes viable once the API has per-client
tokens, which is a Phase 2 conversation.

---

## 4. Architecture

### 4.1 Transport: stdio sidecar, not an embedded endpoint

Two shapes were considered.

**(A) A standalone stdio MCP server, one process per agent, talking to GridVibe
over loopback HTTP.** Each agent CLI spawns its own copy from its own MCP config.

**(B) A streamable-HTTP MCP endpoint mounted on the Flask app itself.**

Recommend **(A)** for Phase 0:

- No new dependency in the base runtime. The MCP SDK lands in
  `requirements-mcp.txt` and only an agent that wants the tools installs it.
  The HTTP client can be stdlib `urllib.request` — `requests` is not currently a
  GridVibe dependency and does not need to become one.
- No async/threading collision. GridVibe runs Flask-SocketIO in `threading`
  async mode; the MCP Python SDK is asyncio-native. Keeping them in separate
  processes avoids an event-loop marriage nobody wants inside the terminal
  server.
- A crashing or slow MCP server cannot take a live terminal with it.
- Per-agent process means per-agent policy is possible later (a spawn budget,
  a read-only profile) without a session concept in the server.

The cost is discovery: the sidecar must find the GridVibe URL. `GRIDVIBE_URL`
from Gap B solves it for panes; a `--url` flag and a `127.0.0.1:5050` default
cover everything else.

**(B) becomes attractive in Phase 2**, when remote panes need reaching, because
one endpoint on the app is far easier to forward and authenticate than N
sidecars.

### 4.2 Layout

```
mcp/
├── __init__.py
├── server.py        # MCP tool definitions + dispatch (thin)
├── client.py        # GridVibe HTTP client: bounded timeouts, typed errors
├── policy.py        # Which tools this process may expose (tiers, budgets)
└── README.md        # How to register it with each agent CLI
requirements-mcp.txt  # mcp>=1.2  (that is the whole file)
tests/test_mcp_client.py
tests/test_mcp_tools.py
```

`server.py` stays thin for the same reason routes do: tool handlers parse
arguments, call `client.py`, and map the result. No policy in the tool handler,
no HTTP in the tool handler.

`client.py` carries the guardrails a route body would: every call bounded by a
timeout, every non-2xx turned into a typed error carrying GridVibe's own
message (the API's refusals are already written as sentences a human can act
on — `"That saved session is already open in another workspace"`, the 409 label
conflict payload — and they should reach the agent verbatim rather than be
re-worded).

### 4.3 Tool surface

Grouped by blast radius. Phase 0 ships the first two tiers; tier 3 is
opt-in and tier 4 is deferred.

**Tier 1 — read (always on)**

| Tool | Backing call |
| --- | --- |
| `gridvibe_status` | `GET /api/health` + `GET /api/workspaces` |
| `list_workspaces` | `GET /api/workspaces` |
| `list_panes` | `GET /api/sessions?workspace_id=…` |
| `list_agents` | `GET /api/dashboard` |
| `whoami` | env vars from Gap B + `GET /api/sessions/<id>` |

**Tier 2 — create (on by default)**

| Tool | Backing call |
| --- | --- |
| `create_workspace` | `POST /api/workspaces` |
| `launch_panes` | `POST /api/sessions` |
| `open_window` | `POST /api/windows/open` (new, Gap A) |
| `split_pane` | `POST /api/sessions/<id>/split` |

**Tier 3 — mutate/destroy (off unless `--allow-destructive`)**

`close_pane`, `close_group`, `close_workspace`, `set_pane_mode`,
`set_pane_shell`, `move_group`.

**Tier 4 — deferred, deliberately**

`send_input` — typing into another pane's terminal. It is the most useful tool
on this list and the most dangerous one, for three separate reasons: it needs a
Socket.IO client or a new HTTP input route (input is socket-only today); it
turns any content an agent reads into a potential command in *another* agent's
shell; and it makes the terminal a side channel between agents that no
GridVibe contract covers. It should ship only with an explicit per-pane opt-in
and its own design note.

### 4.4 What the MCP must never expose

The dashboard's `PANE_FIELDS` allowlist is the precedent to copy literally: a
pane payload names what it is, where it points and what it runs on, and nothing
else. Specifically the MCP must strip, at the client layer rather than per tool:

- `password` and anything derived from it (sessions carry an encrypted SSH
  password; it must not reach a tool result even encrypted),
- explorer view state, browser tab lists, presentation fields — noise that
  inflates every result for no agent-facing value.

Tool results should be JSON-serializable summaries built from an explicit field
list, never a pass-through of `session.to_dict()`.

---

## 5. Security analysis

### What this does not change

The API is already unauthenticated and already reachable by any local process,
including every agent that has a shell in a pane. Adding a loopback-only stdio
sidecar widens no network surface and weakens no existing guardrail: the local
bind stands, the same-origin write guard stands (the sidecar sends no `Origin`,
which is the already-allowed non-browser path), Socket.IO stays room-scoped
because the sidecar does not speak Socket.IO at all, and saved credentials stay
encrypted and unexposed.

### What it does change

It makes the control plane *legible*. An agent that would never have thought to
curl an undocumented local port will readily call a tool named
`close_workspace`. The risk is not new access — it is new likelihood. Three
consequences worth designing for:

1. **Destructive verbs are a real hazard.** `DELETE /api/workspaces/<id>` ends
   live shells. Closing the last group forgets the saved snapshot. An agent that
   misreads an instruction can destroy work that was never committed. Hence
   tier 3 being opt-in, and hence the recommendation that `?forget=true` is
   never exposed as a tool argument at all.
2. **Recursive spawning.** An agent that can start agents can start agents that
   start agents. `terminal.max_sessions` caps a group, not a fleet. Propagate a
   `GRIDVIBE_AGENT_DEPTH` counter in the pane environment, increment it on
   every `launch_panes`, and have the sidecar refuse past a configured depth.
3. **Prompt injection reaches further.** A repository file that talks an agent
   into `launch_panes` now has a lever on the user's machine. This is an
   argument for keeping tier 2 small and tier 4 shut, not an argument against
   the feature — but it should be stated in the MCP README rather than
   discovered.

### Explicitly flagged

Nothing in Phase 0 weakens a Core Guardrail. The *first* thing that would is the
remote port forward in Gap C option 1, which would place the unauthenticated API
on a remote host's loopback. It must not ship without per-client tokens.

---

## 6. Phase 0

### Goal

One agent, in one local pane, can see the workspace tree and build a new
workspace with panes in it — and the user sees the window appear.

### Scope

**In:**

1. `mcp/client.py` — bounded loopback HTTP client, typed errors, field
   allowlists. Stdlib only.
2. `mcp/server.py` — stdio MCP server exposing tier 1 + tier 2.
3. `mcp/policy.py` — tier gating; destructive tools absent unless flagged.
4. `POST /api/windows/open` + `GET /api/windows/intents` +
   `POST /api/windows/intents/<id>/claim` — the one-shot intent store (Gap A).
   TTL-bounded, in-memory, no durable state.
5. `web/static/js/window-intent.js` — the poll + claim + `openWorkspaceWindow()`
   call, loaded on both pages. DOM-free policy (claim decision, TTL, one-claim
   rule) separated from the adapter, per the frontend module convention.
6. Local pane identity env vars (Gap B), injected through
   `_local_shell_integration`.
7. `requirements-mcp.txt`, `mcp/README.md` with a registration snippet per agent
   CLI.
8. Tests: `tests/test_mcp_client.py` (field allowlist, error mapping, timeout
   bound), `tests/test_mcp_tools.py` (tier gating, argument validation,
   `launch_panes` request shape), a Python suite for the intent store
   (claim-once, TTL expiry, unknown id), and a Node suite for the client-side
   claim policy.

**Out:** `send_input`; remote/SSH panes; tier 3 tools on by default; any
authentication scheme; an embedded HTTP MCP endpoint; agent-to-agent messaging.

### Order of work

1. `client.py` + its tests — no MCP protocol involved yet, and it is verifiable
   against a running GridVibe immediately.
2. Tier 1 read tools. At this point an agent can already answer "what is
   running" — useful on its own and a genuine early checkpoint.
3. The intent store + the page-side claim. This is the riskiest piece; doing it
   third means the read half is already banked.
4. Tier 2 create tools.
5. Pane identity env vars + `whoami`.
6. `make check`, README/CHANGELOG.

### Acceptance — the smoke test

Start an agent CLI in a GridVibe pane with the MCP registered, and give it:

> "Open a new workspace called test and open 2 claude agents and an explorer
> terminal on this directory."

The agent should resolve its own directory (from `whoami` or its cwd), then make
two calls.

**Call 1 — `launch_panes`, which posts:**

```json
{
  "new_workspace": true,
  "workspace_label": "test",
  "connection_mode": "wsl",
  "layout": "split",
  "session_name": "test",
  "sessions": [
    { "title": "Claude 1", "directory": "<cwd>",
      "startup_mode": "agent", "initial_command_mode": "agent",
      "initial_command": "claude", "agent_selection": "claude",
      "agent_auto_mode": false, "use_powershell": true },
    { "title": "Claude 2", "directory": "<cwd>",
      "startup_mode": "agent", "initial_command_mode": "agent",
      "initial_command": "claude", "agent_selection": "claude",
      "agent_auto_mode": false, "use_powershell": true },
    { "title": "Explorer", "directory": "<cwd>",
      "startup_mode": "explorer", "explorer_root_directory": "<cwd>" }
  ]
}
```

`201` returns `workspace_id`, `group_id`, the three sessions, and any agent
preflight `warnings` (a missing `claude` binary is reported here rather than
failing the launch).

**Call 2 — `open_window(workspace_id, group_id)`**, which stores the intent; an
open page claims it and calls `openWorkspaceWindow()`.

**Pass looks like:** a workspace named `test` in the launcher's list, a window
holding three panes in a split layout, two of them running `claude` in `<cwd>`
and one showing the file explorer rooted there, and `list_agents` subsequently
reporting two claude panes with an activity reading.

**Known honest failure modes to verify the reporting of**, not to hide:

- A live or saved workspace already named `test` → `409` with a conflict
  payload; the tool must surface GridVibe's own sentence, not retry with
  `test (2)`.
- Browser mode with no user gesture → the pop-up is blocked; the intent must
  report `blocked` and the agent must say the workspace exists and needs opening
  from the launcher.
- No GridVibe page open at all → intent expires; report `no_window_available`.
- `claude` not on PATH → the launch still succeeds and the warning is in the
  response; the panes open at a shell.

---

## 7. Later phases (sketch)

**Phase 1 — observation.** `read_pane_output` (a bounded tail of the existing
replay buffer), richer `list_agents` filtering, explorer reads (search, Git
state, file read) exposed as tools so an agent can inspect a sibling's
repository without an SSH hop. All read-only; no new hazard class.

**Phase 2 — reach and identity.** Per-client tokens on the API, which unlocks
both the remote port forward for SSH panes and a streamable-HTTP MCP endpoint on
the app itself. This is the phase where the security model actually changes and
it deserves its own proposal.

**Phase 3 — coordination.** `send_input` with per-pane opt-in, a structured
handover channel between agent panes, and spawn budgets enforced server-side
rather than per sidecar. Note that GridVibe already has a file-based handover
sketch in r&d; an MCP control plane is the alternative to it, not a layer on top
of it, and picking one before building either is worth doing.

---

## 8. Open questions

1. **Does `open_window` belong in the API at all, or should the MCP shell out to
   the browser?** The sidecar runs on the user's machine and could
   `webbrowser.open(url)` directly — trivial in browser mode, useless in native
   mode. The intent store is the only mechanism that serves both. Confirm native
   mode is the primary target before paying for it.
2. **Should tier 2 be on by default?** Creating a workspace is not destructive,
   but it is visible and it consumes a label in a shared namespace. An
   `--allow-create` flag would make the default profile purely observational.
3. **One sidecar per agent, or one per machine?** Per-agent is proposed.
   Per-machine would make a spawn budget enforceable across agents but
   reintroduces a shared process to keep alive.
4. **Where does the spawn depth live?** Environment (easy, spoofable by the
   agent itself) versus a server-side per-group counter (correct, more work).
5. **Does `launch_panes` deserve preflight-before-launch?**
   `POST /api/agent-preflight` exists and could tell the agent `claude` is
   missing *before* three panes open at bare shells. Costs a round trip per
   launch.
