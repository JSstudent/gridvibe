# Agent Control of a GridVibe Workspace

**Status: analysis and Phase 0 proposal. Not a contract.** Nothing here is
implemented yet, and nothing in the codebase should cite this file until a
phase actually ships — at which point the rules it establishes move into
`CLAUDE.md`'s guardrail list and this document becomes the guide beside them.

This answers one question: **can an agent running inside a GridVibe pane drive
GridVibe itself** — open a workspace, start other agents in it, hand each one a
task, and report back when the work is done?

The short answer is **yes, and most of it already exists**. GridVibe's entire
launcher is a thin client over an HTTP API that already creates workspaces,
launches N panes, starts a named agent CLI in each one, and applies a saved
layout. What is missing is not the ability to *act* — it is the ability to
**observe**, to **signal completion**, and to **make a window appear**.

---

## 0. The target interaction

The prompt this is designed for:

> *"Review the codebase, analyze this section, prepare a fix/implementation
> plan in three parts, and delegate the tasks to three Codex agents. Start those
> agents in a new workspace with a saved session layout."*

Decomposed, with today's honest status:

| # | Step | Status today |
|---|---|---|
| 1 | Read and analyze the codebase | ✅ The agent already does this — no GridVibe involvement |
| 2 | Write the plan into three task files | ✅ Ordinary file writes |
| 3 | Read a saved session layout | ✅ `GET /api/saved-sessions` |
| 4 | Create a new workspace | ✅ `POST /api/sessions` with `new_workspace: true` |
| 5 | Launch three panes, each running `codex` | ✅ Same call — `startup_mode: "agent"`, `agent_selection: "codex"` |
| 6 | Make that workspace **visible** | ❌ Window opening is client-owned (G1) |
| 7 | Deliver task 1/2/3 into each agent's TUI | ⚠️ Socket.IO `terminal_input` works; no HTTP route, no readiness signal (G2, G4) |
| 8 | Know when each agent has finished | ❌ Nothing reports this (G5) |
| 9 | Raise an OS notification on completion | ❌ No notification code anywhere in the repo (G6) |

Steps 1–5 are available **right now with no code change**. Steps 6–9 are the
Phase 0 work.

---

## 1. What GridVibe already exposes

### 1.1 HTTP — the launcher's own API

The launcher page holds no privileged channel: everything it does, it does over
these routes. Any local process can do the same.

| Route | What it gives an agent |
|---|---|
| `POST /api/sessions` | **The one launch path.** Creates a group of N panes in a chosen or brand-new workspace. Restore uses the same function (`launch_session_group()` in `web/workspaces.py`), so an agent launch cannot drift from a normal one |
| `GET /api/sessions` | Every live session with its group, workspace, mode, status, directory |
| `GET /api/session-groups` | The group/tab structure |
| `POST /api/sessions/<id>/shell` | Relaunch one pane under another shell **and/or another agent** — a ready-made "start Codex in this pane" |
| `POST /api/sessions/<id>/mode` | Swap a pane between terminal / explorer / browser |
| `DELETE /api/sessions/<id>`, `DELETE /api/sessions` | Close one pane or a whole group |
| `POST /api/sessions/<id>/split` | Add a pane beside an existing one |
| `GET/POST /api/workspaces`, `PATCH`, `DELETE` | List, create (labelled), rename, close workspaces |
| `POST /api/workspaces/validate-label` | Check a name is free before committing |
| `GET/POST /api/saved-sessions` | Read and write launcher presets — the "saved session layout" |
| `GET /api/runtime-state/workspaces`, `POST /api/runtime-state/restore` | Restore saved workspace snapshots server-side |
| `POST /api/workspaces/<id>/save` | Save one workspace (**requires a live window to flush**) |
| `GET /api/explorer/<id>/...` | The whole file/Git surface: read, search, diff, stage, commit |
| `POST /api/agent-preflight` | Ask whether `codex`/`claude`/`grok` is actually installed on the target **before** launching |
| `GET /api/health` | Liveness + version — how a tool discovers a server is there |

### 1.2 Socket.IO — the live channel

| Event | Direction | Use to an agent |
|---|---|---|
| `terminal_input` | client → server | **Type into any pane.** No room membership is required and no auth is checked |
| `terminal_output` | server → client | Live output from a joined session |
| `join_session` | client → server | Join a session room; replays the rolling buffer **verbatim** first |
| `session_status` | server → client | Pane connected / errored / disconnected |
| `session_groups_updated` | server → workspace room | Groups changed — this is what makes an open window pick up an agent-created group **live** |
| `app_config_updated` | server → all | Settings changed |

Two consequences worth stating plainly:

- **A Socket.IO client can already do everything a pane's keyboard can do.**
  `python-socketio` and `websocket-client` are *already* in `requirements.txt`
  (for the server and the voice service respectively), so a Phase 0 tool needs
  **no new dependency**.
- **That is also the security story** (§5): the input path has no auth today
  because it is loopback-only and its only client was the app's own page.

### 1.3 The native bridge

In native desktop mode, `web/webview_launcher.py` runs the Flask server in a
**thread of the same process** as the pywebview windows. `GridVibeApi` there
already owns `open_workspace_window()`, `focus_workspace_window()`,
`close_workspace_window()`, `minimize_all_windows()`, fullscreen and zoom.

The catch: **no server-side code can reach that object today.** There is no
registry, no hook, no callback. `POST /api/select-folder` is the closest
precedent and it sidesteps the bridge entirely by opening a `tkinter` dialog
in-process. Wiring a hook is small, and it is the cheapest fix for G1.

### 1.4 Three facts that make this cheap

1. **Sessions are server-side state; a window is only a viewer.** A group of
   three live Codex panes exists whether or not anything is displaying it. This
   is why an agent can build a workspace headlessly and let the user open it
   later.
2. **An open workspace window self-updates.** `session_groups_updated` is
   broadcast to the workspace room on every group change, and `terminals.js`
   answers it with a status refresh. An agent-created group appears in an
   already-open window with no extra mechanism.
3. **A local CLI is not cross-origin.** The write guard in `web/app.py` only
   rejects a request that *carries* a foreign `Origin` header; a request with no
   `Origin` (a CLI, `curl`, pywebview) passes. No new exemption is needed — and
   no accidental loosening either.

---

## 2. The gaps

Seven, in the order they bite.

### G1 — Nothing can make a window appear

`openWorkspaceWindow()` lives in `workspaces.js` and dispatches to
`window.open()` (browser) or `api.open_workspace_window()` (native). Both are
**client-side**, and in browser mode a named `window.open` is a pop-up that
browsers grant only **one per user gesture** — a constraint the restore path
already documents and reports.

*Why it matters:* the agent can build the workspace, but the user has to click
something to see it.

*Cheapest honest fix:* a server → launcher-window Socket.IO event
(`workspace_present_requested`) that the launcher answers by calling the
existing `openWorkspaceWindow()`. In **native** mode that is a real window with
no pop-up problem. In **browser** mode it will be blocked without a prior
gesture, and the honest behaviour is to say so through the existing notice
banner rather than to pretend. A native-only server → bridge hook is the
alternative and is strictly better where it applies.

### G2 — No HTTP route types into a pane

Input is Socket.IO-only. A CLI can hold a socket, but a one-shot tool ("send
this line to that pane") paying for a socket handshake, a `join_session` and a
disconnect per call is the wrong shape, and it makes the tool untestable with
`curl`.

*Fix:* `POST /api/sessions/<id>/input` — bounded, opt-in, token-gated,
delegating to the same `_send_connection_input()` the socket handler uses.

### G3 — No way to read a pane

`_cache_terminal_output()` keeps a rolling buffer per session, but it is only
reachable by joining the session room over Socket.IO. There is **no HTTP read**.

*Why it matters:* an orchestrating agent that cannot read a pane cannot tell
whether the agent in it is at a prompt, mid-answer, crashed, or waiting on a
permission confirmation.

*Fix:* `GET /api/sessions/<id>/output?tail=N` returning the tail of that buffer.
Read-only, cheap, and it reuses the existing buffer wholesale.

### G4 — No readiness signal

An agent CLI takes seconds to boot. Typing a task at `codex` before its TUI is
listening loses the text — or worse, half of it. Nothing in GridVibe reports
"this pane's program is ready for input".

*Fix (Phase 0):* **output quiescence** — poll `GET .../output` until the tail
stops changing for a short settle window, with a timeout. Not a promise about
any specific CLI's UI, which is why it is a *tool-side* heuristic and not a
server contract.

### G5 — No completion signal

The inverse of G4, and the harder half. Parsing an agent TUI's output to decide
it has finished is brittle across six different CLIs and their versions.

*Fix:* **do not parse — have the delegated agent say so.** §3.2.

### G6 — No notifications of any kind

There is no notification code in the repository: no `plyer`, no toast, no
`notify-send`, no Web Notifications call. `showGridVibeNotice()` is an in-page
banner on the launcher only, and the workspace page has a toast — neither
reaches the user when GridVibe is behind another window.

*Fix:* one bounded per-platform subprocess through `web/process_bounds.py`
(`osascript` / `notify-send` / a PowerShell toast), with the in-app banner as
the always-available fallback. No new dependency.

### G7 — A pane does not know who it is

Local panes inherit `os.environ` (plus the shell-integration hook), and nothing
GridVibe-specific is injected. An agent running in a pane cannot answer "which
session am I?" or "what workspace am I in?" without being told.

*Fix:* inject `GRIDVIBE_SESSION_ID`, `GRIDVIBE_WORKSPACE_ID`, `GRIDVIBE_GROUP_ID`
and `GRIDVIBE_URL` into local panes at `_local_shell_integration()` — the seam
that already exists for exactly this kind of environment work, WSL forwarding
(`WSLENV`) included. **SSH panes cannot have this**: `sshd` forwards only what
`AcceptEnv` allows, which is the same constraint that makes the remote prompt
hook a typed line. Phase 0 accepts that asymmetry, and the tool takes explicit
ids when the environment is absent.

---

## 3. Two problems that are not API problems

### 3.1 Getting a task *into* an agent TUI

Typing a multi-paragraph task at an agent CLI is the part most likely to fail,
for reasons that have nothing to do with GridVibe:

- A newline inside the text **submits** in most agent TUIs, so a three-paragraph
  plan is submitted as three truncated prompts.
- Bracketed paste, readline bindings and TUI input widgets each handle a burst
  of characters differently.
- Any escape sequence in the text is interpreted, not inserted.

**The rule: deliver the task as a file, type a one-line pointer.**

The orchestrator writes `<run>/task-1.md` and types exactly one line:

```
Read <run>/task-1.md and implement it. When you are done, run: <notify command>
```

One line, no embedded newline, no escapes, arbitrary task length, and the task
survives the agent restarting or being re-prompted. It also makes the whole
delegation **auditable** — the three tasks are on disk, reviewable before
anything runs.

### 3.2 Knowing an agent is done

Do not scrape the TUI. **Invert it: give the delegated agent a way to report,
and put it in the prompt.** The last sentence of every delegated task is a
command the agent runs when it finishes.

That single primitive does three jobs at once:

- it raises the **OS notification** the user asked for (G6),
- it is the **completion signal** the orchestrator polls for (G5),
- and it works identically for Codex, Claude, Grok, or a human in that pane.

The completion record is a small JSON file per delegated task; the orchestrator
polls it, or simply reports the notification and lets the user decide.

The honest limitation, stated up front: **an agent that crashes, hangs, or
ignores the instruction never reports.** The orchestrator therefore always
carries a timeout and a "check the pane yourself" fallback, and the tail read
from G3 is what makes that fallback useful.

---

## 4. Delivery shape — CLI first, MCP later

Two candidate shapes for the tool surface:

| | **A. CLI (`gridvibe-ctl`)** | **B. MCP server** |
|---|---|---|
| Works with | Every agent CLI, via its shell | Only clients that speak MCP |
| Client config | None — it is a command | Per-client registration |
| Testable by hand | `gridvibe-ctl sessions` | Needs an MCP client |
| Discoverability | `--help` | Rich tool schemas |
| Effort | Low | Medium |
| Testable in this repo's harness | Yes — a plain Python module + unittest | Needs a protocol harness |

**Recommendation: A for Phase 0, B as a thin wrapper in Phase 2.** The reason is
not effort — it is that these workspaces run Claude *and* Codex *and* Grok panes
side by side, and a CLI is the only surface all of them share today. An MCP
server built later calls the same functions; nothing is thrown away.

---

## 5. Security posture

This is the part to get right, because Phase 0 proposes the first HTTP route in
GridVibe's history that **types into a shell**.

GridVibe's stated posture (README, `CLAUDE.md` guardrail 1) is: local tool,
binds `127.0.0.1`, no authentication, not to be exposed. The control surface
must not quietly widen that.

Non-negotiables for Phase 0:

1. **Off by default.** A new `agent_control.enabled` config key, `false` in
   `default_config.json`, reached only through `RuntimeConfig` — never by
   reading the file. Every route 404s while it is off, so an install that never
   turns it on is byte-for-byte as exposed as it is today.
2. **Capability token.** A per-run token minted at startup when the feature is
   enabled, required in an `X-GridVibe-Agent-Token` header — the pattern
   `_browser_shutdown_token` already establishes. The token reaches panes
   through the G7 environment injection, so a pane GridVibe started can act and
   an arbitrary local process cannot.
3. **Loopback enforcement on the input route specifically.** Refuse when
   `request.remote_addr` is not loopback, independently of `--host`. Someone who
   binds `0.0.0.0` for their own convenience must not thereby publish a
   keystroke-injection endpoint.
4. **Bounds.** A maximum input length per call, maximum notification title and
   body lengths, an output tail ceiling. Every subprocess goes through
   `web/process_bounds.py` — the notification path is a subprocess and gets the
   same process-group kill and reap bound as the explorer's Git runner.
5. **No shell string interpolation, ever.** The notification command is an argv
   list; the title and body are arguments, never concatenated into a command
   line.
6. **Shape-only logging** (guardrail 9): session ids, byte counts, outcomes.
   Never the input text, never the task content, never the notification body.

Also worth saying out loud in whatever ships: **this makes an agent able to type
into another agent's shell.** That is the point, and it is also the risk. The
token, the default-off switch and the loopback check are what keep it a
deliberate local capability rather than an ambient one.

---

## 6. Phase 0 — the POC

**Goal:** the target prompt in §0 runs end to end, on one machine, in native
mode, with a local-repo (WSL) workspace. Nothing more.

### 6.1 What ships

| # | Item | File | Notes |
|---|---|---|---|
| 1 | Feature flag + token | `web/config.py`, `default_config.json` | `agent_control.enabled` (default `false`); token minted at startup |
| 2 | Control routes | **new** `web/agent_control.py` | `POST /api/sessions/<id>/input`, `GET /api/sessions/<id>/output`, `POST /api/agent-control/notify`, `POST /api/agent-control/report`. A new module, not `web/api.py` (guardrail 6) |
| 3 | OS notification | same module | `osascript` / `notify-send` / PowerShell toast, bounded via `web/process_bounds.py`, in-app banner fallback |
| 4 | Pane identity | `web/terminal_io.py::_local_shell_integration()` | `GRIDVIBE_SESSION_ID` / `_WORKSPACE_ID` / `_GROUP_ID` / `_URL`, with `WSLENV` forwarding. Local panes only |
| 5 | The tool | **new** `utils/gridvibe_ctl.py` | Stdlib + `python-socketio` (already a dependency) |
| 6 | Window present | `web/agent_control.py` + `workspaces.js` | Server emits `workspace_present_requested`; the launcher answers with the existing `openWorkspaceWindow()`, and reports a browser pop-up refusal through the existing banner |

`gridvibe-ctl` subcommands for Phase 0, and nothing else:

```
gridvibe-ctl health
gridvibe-ctl presets                                  # saved layouts
gridvibe-ctl launch --preset <name> --new-workspace <label> \
                    --agent codex --panes 3 [--present]
gridvibe-ctl sessions [--workspace <id>]
gridvibe-ctl send <session-id> --line "..."           # one line, waits for quiescence
gridvibe-ctl tail <session-id> [--bytes 2000]
gridvibe-ctl notify --title "..." --body "..."        # OS notification
gridvibe-ctl done --task <id> [--status ok|failed]    # completion record + notify
gridvibe-ctl wait --task <id> [--timeout 3600]        # orchestrator side of `done`
```

### 6.2 What Phase 0 explicitly does **not** ship

Naming these is half the value of a Phase 0.

- **No MCP server.** §4.
- **No TUI output parsing.** Completion is reported, never inferred (§3.2).
- **No SSH pane identity.** G7's constraint is real; SSH panes take explicit ids.
- **No browser-mode window-opening guarantee.** It is attempted and the refusal
  is reported — the same honest behaviour the restore path already has.
- **No new durable store.** Completion records go in a run directory, not into
  `runtime_state.json`. A fourth durable store would have to go through
  `web/state_files.py` (guardrail 2), and Phase 0 does not need one.
- **No agent-initiated Git mutation, close, or restart.** The agent may launch
  and observe; destroying live sessions stays a human gesture.
- **No credential handling.** The tool never sends a password; SSH targets in
  Phase 0 come from an existing preset or not at all.

### 6.3 The target prompt, step by step

What actually happens when the user types the §0 prompt at a Claude pane:

1. **Analyze.** Ordinary reading. No GridVibe involvement.
2. **Plan.** The agent writes `task-1.md`, `task-2.md`, `task-3.md` into a run
   directory, each self-contained, each ending with the exact
   `gridvibe-ctl done --task <id>` line the delegate must run.
3. **Preflight.** `gridvibe-ctl` calls `POST /api/agent-preflight` for `codex`
   against the target environment. Missing binary → stop here and say so, rather
   than launching three panes that each print "command not found".
4. **Launch.** `gridvibe-ctl launch --preset "<layout>" --new-workspace
   "Review run 3" --agent codex --panes 3` → one `POST /api/sessions` with
   `new_workspace: true`, the preset's `layout` / `workspace_layout`, and three
   panes each `startup_mode: "agent"`, `agent_selection: "codex"`.
   **`saved_session_id` is deliberately left empty** — a preset-backed group is
   live in at most one workspace (`saved_session_conflict()`), so borrowing the
   *shape* of a saved layout without claiming its *identity* is what lets a
   second and third delegation run coexist with the user's own open session.
5. **Present.** `--present` asks the launcher to open the window. Native: it
   opens. Browser: it is attempted, and a refusal is reported once.
6. **Deliver.** For each pane: wait for output quiescence (G4), then
   `gridvibe-ctl send <id> --line "Read <run>/task-N.md and implement it. When
   you are done, run: gridvibe-ctl done --task <id>"`.
7. **Report.** The orchestrator either returns immediately with the three
   session ids ("three Codex agents are working in *Review run 3*"), or blocks
   on `gridvibe-ctl wait` for all three. Each `done` raises an OS notification;
   the final one says the run is complete.

### 6.4 New config keys

```json
"agent_control": {
  "enabled": false,
  "max_input_chars": 4096,
  "max_output_tail_bytes": 65536,
  "notifications": true
}
```

Every key is read through `RuntimeConfig`, and every key is read by something —
no key ships that nothing consumes (guardrail 5).

### 6.5 Tests

Following the repo's conventions — behavioural, not source-text:

- `tests/test_agent_control.py`
  - every route is a **404 while the flag is off**, and the flag is read from a
    snapshot rather than the file
  - a missing or wrong token is a `403`; a correct one acts
  - a non-loopback `remote_addr` is refused on the input route even with a valid
    token
  - input over `max_input_chars` is refused and **nothing reaches the pane**
  - the output tail is bounded and never exceeds its ceiling
  - the notification path builds an **argv list**, and a title containing
    `; rm -rf ~` arrives as one argument
  - a notification subprocess that hangs is killed by the process-group bound
  - a completion record round-trips through `done` → `wait`
- `tests/test_terminal_cwd.py` (extend) — the identity variables appear in a
  local pane's spawn environment, `WSLENV` carries them into WSL, and an SSH
  pane is **unchanged**
- Node-side: the launcher's answer to `workspace_present_requested` reports a
  blocked pop-up **once**, through the one banner (guardrail 8)

### 6.6 Acceptance criteria

Phase 0 is done when, on one Windows machine in native mode:

1. `agent_control.enabled` is `false` in a fresh checkout and every control
   route 404s.
2. With it on, a Claude pane can run the §0 prompt and three Codex panes open in
   a new, visible workspace with the saved layout's geometry.
3. Each Codex pane receives exactly one line, and it is intact.
4. Finishing a task raises a real OS notification naming the task.
5. The orchestrator reports all three completions, or times out and says which
   pane did not report.
6. `make check` passes, and `logs/gridvibe.log` contains no task text, no typed
   input and no notification body.

---

## 7. Beyond Phase 0

| Phase | Adds | Why later |
|---|---|---|
| 1 | Readiness as a **server** signal rather than a tool-side heuristic, derived from the agent-promotion tracking already in `terminal_io.py` | Needs real data from Phase 0 about how each CLI actually behaves |
| 2 | An MCP server wrapping the same functions | Only worth it once the function set has stopped moving |
| 3 | Delegation as a first-class GridVibe concept: a run visible in the UI, per-pane task badges, a run history | Needs a persisted store, and therefore `web/state_files.py` and a real schema |
| 4 | SSH pane identity via a startup line, agent-to-agent messaging, cross-workspace orchestration | Each carries its own security question |

---

## 8. Open questions

1. **Native or browser first?** Native mode gives real windows and an in-process
   bridge; browser mode has the pop-up constraint. Phase 0 above assumes native.
2. **Where do run artifacts live?** `docs/r&d/<run>/` is gitignored scratch and
   is the natural home, but it is also explicitly *not* citable. A dedicated
   gitignored `runs/` directory may be cleaner.
3. **Should `done` be a server route at all,** or just a file the orchestrator
   watches? A route gives one place to hold state and to raise the notification;
   a file needs no server surface. Phase 0 proposes the route because the
   notification has to be raised by the server process anyway.
4. **How much should the user see?** A delegation run currently shows up as
   "three panes appeared". A visible run indicator is Phase 3, but a minimal
   version may be worth it sooner.
5. **One token or per-pane tokens?** One run token is simpler; per-pane tokens
   would let a delegated agent act only as itself. Phase 0 proposes one.
