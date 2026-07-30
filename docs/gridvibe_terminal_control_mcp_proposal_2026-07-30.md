# GridVibe Terminal Control MCP

Status: feasibility draft  
Date: 2026-07-30  
Scope: expose GridVibe session, terminal, delegation, and handoff controls to an
agent launched inside a GridVibe terminal.

## Decision Summary

This is feasible and fits GridVibe's current architecture, with one important
qualification:

- exposing existing terminal and session state is high-feasibility;
- letting an agent safely create and control child terminals is
  medium-to-high-feasibility;
- reliable cross-agent delegation and handoff needs a small task/lifecycle
  layer in addition to raw terminal input;
- agents running on remote SSH hosts should not be supported in the first
  version because they cannot safely reach a loopback-only MCP endpoint on the
  GridVibe host without a tunnel.

The recommended product shape is a **GridVibe tools** control beside the
existing **Auto mode** checkbox in terminal setup. It should not be another
agent selection and it should not be implied by Auto mode.

The three concerns are independent:

1. **Agent** selects which CLI GridVibe launches.
2. **Auto mode** controls that CLI's own approval behavior.
3. **GridVibe tools** controls what that agent may observe or change in
   GridVibe.

The recommended implementation is an authenticated, loopback-only
Streamable HTTP MCP server owned by the running GridVibe process. Each enabled
agent launch receives an ephemeral MCP configuration and a capability token
scoped to that controller session. MCP tool handlers call a shared
`SessionControlService`; they do not call Flask routes or imitate a browser
Socket.IO client.

## Why This Is a Good Fit

GridVibe already has most of the lower-level machinery:

- `sessions/manager.py` owns `TerminalSession`, `SessionGroup`, and the
  thread-safe `SessionManager`.
- `web/terminal_io.py` owns live PTY/SSH connection state, terminal input,
  output replay buffers, reconnect/close plumbing, and room-scoped output.
- `web/api.py` already exposes session list, launch, split, reconnect, shell
  change, pane mode change, close, terminal input, and terminal resize
  behavior.
- `web/agents.py` and `agent_registry.json` already implement registry-driven
  agent selection, preflight, and safe Auto mode launch-flag composition.
- `web/saved_sessions.py`, workspace capture, and launcher normalization
  already round-trip agent startup metadata.

The missing pieces are therefore an authorization boundary, a reusable
control service, an MCP transport, per-agent launch adapters, cursor-based
terminal output, and task lifecycle semantics.

## What "Entire Terminal Functionality" Should Mean

The MCP should eventually cover the complete **session control** lifecycle,
but it should not expose every internal/UI operation literally.

| Existing behavior | MCP equivalent | Notes |
|---|---|---|
| List groups/sessions | `workspace_get` | Safe metadata only |
| Get one session | `terminal_get` | Never includes a password |
| Launch a group | `group_launch` | Normalized config, quotas, capability policy |
| Split a pane | `terminal_split` | Creates a controller-owned child where applicable |
| Reconnect | `terminal_reconnect` | Existing status preconditions |
| Send terminal input | `terminal_write` | Most sensitive non-close operation |
| Read terminal output | `terminal_read`/`terminal_wait` | Cursor-based, bounded |
| Change shell | `terminal_change_shell` | Local sessions only; restarts connection |
| Change pane mode | `terminal_change_mode` | Existing terminal/explorer/browser rules |
| Close session/group/all | `terminal_close`/`group_close` | Destructive confirmation required |
| Launch an agent | `agent_launch` | Registry and preflight driven |
| Saved-session launch | Later `preset_list`/`preset_launch` | Never returns decrypted credentials |

UI-only mechanics should remain UI-owned:

- terminal resize follows the visible xterm geometry;
- Socket.IO room join/leave is a browser delivery concern;
- replay-buffer clearing is replaced by MCP cursors;
- pane layout, active tab, and visual group ordering do not help an agent
  perform a delegated task and need not become MCP tools initially.

This boundary exposes the useful behavior without leaking GridVibe's transport
implementation or turning presentation state into agent authority.

## Important Architectural Constraint

An agent CLI is launched inside a terminal PTY. A conventional local MCP
server started by that agent is a separate child process, so it cannot directly
access GridVibe's in-memory `SessionManager` or connection registry.

Three integration shapes are possible:

| Option | Description | Assessment |
|---|---|---|
| In-process Streamable HTTP MCP | GridVibe hosts MCP on a private loopback port; agents connect with a scoped token | **Recommended** |
| Stdio bridge plus private control API | The agent starts a small MCP bridge which calls back into GridVibe | Viable fallback |
| Wrap the existing REST/Socket.IO surface | MCP handlers behave like a browser client | Reject |

The existing web API is the wrong internal boundary because it mixes request
normalization, UI response shapes, broadcasts, and domain operations.
Socket.IO terminal input also has browser-room assumptions and no
agent-capability model. Reusing those endpoints would create a second,
partially trusted frontend rather than a proper internal control plane.

## Recommended Architecture

```text
GridVibe launcher
    |
    | creates controller session + capability
    | generates temporary agent-specific MCP config
    v
Selected agent CLI inside a GridVibe PTY
    |
    | Streamable HTTP MCP + capability header
    v
Loopback-only GridVibe MCP server
    |
    | validates controller, scope, target, quota, confirmation
    v
SessionControlService
    |                       |
    v                       v
SessionManager         terminal_io/output journal
    |
    v
normal room-scoped Socket.IO updates to the UI
```

### 1. Shared session control service

Add a canonical backend module such as `web/session_control.py`.

It should contain the reusable application operations currently assembled in
Flask route handlers:

- list/get groups and sessions;
- create a group or append/split a session;
- reconnect a session;
- send terminal input and interrupt;
- switch shell or pane mode;
- close a session or group;
- create an agent session from a normalized agent launch request;
- publish state changes after locks are released.

Both `web/api.py` and the MCP handlers should call this service. This prevents
the MCP work from duplicating route logic or regrowing `web/api.py`.

The service must preserve the documented lock order:

1. `connection_lock` may be taken before `SessionManager.lock`;
2. `SessionManager.lock` must never be held while taking `connection_lock`;
3. no Socket.IO emit, MCP response, wait, or other slow operation may occur
   while either shared lock is held.

### 2. MCP host

Add a module such as `web/mcp_control.py` which:

- starts and stops with GridVibe;
- binds to `127.0.0.1` on an ephemeral or dedicated private port;
- uses Streamable HTTP;
- authenticates every request with an opaque capability;
- maps tools to `SessionControlService`;
- never exposes SSH passwords, encryption material, or raw connection objects;
- applies response-size limits before returning terminal output.

Running MCP on a separate loopback listener is preferable to mounting it under
the main Flask app:

- it does not weaken or complicate the existing browser same-origin guard;
- it can use the official Python MCP server stack without forcing ASGI
  semantics into the Flask-SocketIO app;
- it is impossible to expose accidentally through a configured public
  GridVibe web bind unless a future feature explicitly adds that support.

The first implementation spike should verify that the chosen MCP Python
package and server lifecycle coexist cleanly with Flask-SocketIO's current
`threading` async mode and with packaged Windows builds.

### 3. Capability authority

Add an in-memory capability registry. A capability should contain:

- random token hash, never the plaintext token;
- controller `session_id`;
- controller `group_id`;
- permission profile;
- allowed target set or target rule;
- child sessions created by this controller;
- creation and expiry time;
- revocation state;
- delegation depth and launch quota;
- optional confirmation leases.

Capabilities should be:

- generated only after the controller session exists;
- revoked when the controller closes, disconnects permanently, or GridVibe
  exits;
- reissued after workspace restore rather than serialized;
- excluded from `TerminalSession.to_dict()`, saved sessions,
  `runtime_state.json`, logs, URLs, and command-line arguments.

If a client requires a temporary config file containing the token, create it
outside the repository with user-only permissions, delete it when the
controller exits, and revoke the token even if deletion fails.

### 4. Agent launch adapters

Do not put arbitrary MCP command fragments into `agent_registry.json`.
Instead, extend registry entries with a validated strategy enum and implement
the renderers in Python.

Example conceptual metadata:

```json
{
  "mcp": {
    "supported": true,
    "launch_strategy": "claude_mcp_config",
    "supported_environments": ["windows_native", "wsl_linux"]
  }
}
```

Possible strategy names include:

- `codex_config`;
- `claude_mcp_config`;
- `copilot_additional_mcp_config`;
- `kimi_mcp_config`;
- `opencode_config`;
- `kilo_config`.

The renderer should generate a temporary config and safely compose the
per-launch argument using the target shell's quoting rules. The current
`_compose_agent_startup_command()` only receives a session, while correct
config-path quoting depends on `cmd`, PowerShell, POSIX, or WSL. The MCP work
should therefore refactor launch composition to receive the resolved shell
kind instead of appending an unquoted path to the current command.

Auto mode remains an independently applied registry flag.

### 5. Dedicated frontend surface

The launcher row should add:

- **GridVibe tools** checkbox;
- permission scope selector shown when enabled;
- concise description of what the selected scope allows;
- unsupported-environment or unsupported-agent explanation;
- MCP preflight status beside the existing agent CLI preflight.

Recommended scopes:

| Scope | Default abilities |
|---|---|
| Off | No GridVibe MCP connection |
| Observe | Read group/session state and bounded output |
| Delegate | Observe plus create and manage controller-owned child agent sessions |
| Group control | Control siblings in the same group, excluding the controller itself |

`Delegate` is the recommended default when the feature is enabled. It gives an
agent useful orchestration powers without granting raw input access to every
terminal the user already had open.

This is a substantial new frontend domain. Keep the launcher toggle wiring
small in `launcher.js`, but put runtime authorization prompts, operation
status, and agent-control UI behavior in a dedicated file such as
`web/static/js/agent-control.js`, not `terminals.js`.

## Tool Surface

Avoid one tool per existing HTTP route in the first release. Start with a
small, composable tool set, then add specialized orchestration tools once the
control boundary is proven.

### Phase 1: observation and output

| Tool | Purpose | Policy |
|---|---|---|
| `workspace_get` | Return controller-visible groups, terminals, and task summaries | Read-only |
| `terminal_get` | Return safe metadata for one terminal | Read-only |
| `terminal_read` | Return bounded output after a cursor | Read-only |
| `terminal_wait` | Wait for output/status after a cursor without polling | Read-only |

`terminal_read` should default to text with ANSI control sequences removed,
return a continuation cursor, and report when older output was truncated.
Raw output may be an explicit opt-in format.

The current 50,000-character replay buffer is sufficient for a browser
replay, but it has no stable cursor. Add a bounded output journal using deque
chunks shaped like `(sequence, text)`. Appending must remain O(1); joining is
only done for the requested bounded slice.

`terminal_wait` should use a per-session condition/event notified by the
output pump. It must not implement sub-second polling and must not wait while
holding `connection_lock` or `SessionManager.lock`.

### Phase 2: terminal control

| Tool | Purpose | Default restriction |
|---|---|---|
| `terminal_write` | Send exact keystrokes/input | Owned children only |
| `terminal_interrupt` | Send one interrupt to a live terminal | Owned children only |
| `terminal_split` | Append a plain terminal to an allowed group | Session quota |
| `terminal_reconnect` | Reconnect an errored/disconnected terminal | Allowed targets |
| `terminal_change_shell` | Restart a local pane under another shell | Confirmation |
| `terminal_change_mode` | Switch terminal/explorer/browser mode | Confirmation |
| `terminal_close` | Close a terminal | In-page confirmation |
| `group_close` | Close a whole group | In-page confirmation |

Raw `terminal_write` is equivalent to granting command execution in another
shell. MCP annotations and client-side approval prompts are useful, but they
are not a sufficient security boundary because an agent may also be launched
in Auto mode.

Recommended enforcement:

- controller session is never a valid write target;
- controller-owned child terminals may be written without per-call UI prompts
  under `Delegate`;
- existing sibling terminals require `Group control` and an explicit
  short-lived user-approved lease;
- closing a live session or group always uses the existing in-page generic
  confirmation pattern;
- workspace-wide control is deferred.

A destructive tool can create a pending operation and return
`confirmation_required` plus an operation ID. The session UI displays the
request with `openGenericConfirmModal(...)`; the agent can call
`operation_wait` to receive the result. No browser `confirm()`/`alert()` or
busy polling is introduced.

### Phase 3: delegation and handoff

Raw terminal tools alone are not a reliable handoff protocol. Terminal output
does not prove that an agent accepted a task, and injecting text into an
interactive TUI before it is ready is race-prone.

Add semantic tools:

| Tool | Purpose |
|---|---|
| `agent_launch` | Create a controller-owned agent terminal with a task envelope |
| `agent_message` | Send a follow-up message to an owned agent |
| `task_inbox` | Return tasks assigned to the calling controller |
| `task_accept` | Acknowledge and lease a task |
| `task_update` | Record progress, notes, changed files, tests, or a blocker |
| `task_complete` | Complete or fail a task with a structured result |
| `task_wait` | Wait for task state changes without polling |
| `task_handoff` | Assign remaining work and context to another agent terminal |

Suggested task state machine:

```text
queued -> accepted -> running -> completed
                         |-----> blocked
                         |-----> failed
queued/running ----------------> cancelled
```

Suggested task envelope:

```json
{
  "task_id": "opaque-id",
  "parent_task_id": null,
  "objective": "Implement the bounded change",
  "acceptance_criteria": ["Tests pass", "No public API regression"],
  "context": "Concise handoff context",
  "files_changed": [],
  "tests": [],
  "blockers": [],
  "assigned_session_id": "target-session",
  "delegation_depth": 1
}
```

The MCP server's initialization instructions should tell an enabled agent to
check `task_inbox` on startup and acknowledge an assigned task. `agent_launch`
should also pass a short agent-specific startup prompt containing the task ID.
This gives both an explicit prompt and a machine-readable acknowledgement.

Add:

- idempotency keys so a retried `agent_launch` does not create duplicates;
- maximum delegation depth;
- per-controller child and total-session quotas;
- cycle detection for handoffs;
- task deadlines/lease expiry;
- cancellation propagation rules.

Initially keep task state in memory. Persistence can be added later after the
schema and recovery behavior are proven. If persisted, task records must
contain no passwords, auth tokens, or captured terminal output.

This semantic task layer can eventually replace the polling/watcher approach
described in `docs/r&d/orchestrator.md`. That document is still useful as a
handoff-schema reference, but MCP tools plus event-driven task waits are a
better GridVibe-native control plane than a five-second `HANDOVER.json`
watcher.

## Agent Compatibility

As of 2026-07-30, every built-in GridVibe agent family has documented MCP
support. The remaining compatibility work is reliable **per-launch injection**
without modifying the user's permanent global config.

| Agent | Documented MCP support | Useful per-launch mechanism | Assessment |
|---|---|---|---|
| Codex | Stdio and Streamable HTTP | Config/CLI MCP support; exact ephemeral override needs spike | Supported, adapter validation required |
| Claude Code | Stdio and HTTP | `--mcp-config` JSON file/string | Strong fit |
| GitHub Copilot CLI | Stdio, HTTP, SSE | `--additional-mcp-config` | Strong fit |
| Kimi Code CLI | Stdio and HTTP | `--mcp-config-file` or `--mcp-config` | Strong fit |
| Kilo CLI | Local and remote MCP config | Trusted env/config injection needs spike | Supported, adapter validation required |
| OpenCode | Local and remote MCP config | Config/env injection needs version spike | Supported, adapter validation required |

Official references:

- Codex MCP:
  https://developers.openai.com/codex/mcp/
- Claude Code MCP:
  https://code.claude.com/docs/en/mcp
- Claude Code CLI `--mcp-config`:
  https://code.claude.com/docs/en/cli-usage
- GitHub Copilot CLI MCP:
  https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers
- Kimi Code CLI MCP:
  https://moonshotai.github.io/kimi-cli/en/customization/mcp.html
- Kilo CLI MCP:
  https://kilo.ai/docs/automate/mcp/using-in-cli
- OpenCode MCP:
  https://opencode.ai/v2/docs/mcp-servers

Custom agents should show GridVibe tools as unsupported unless the user has
selected a known launch adapter. Do not guess how to inject MCP into an
arbitrary custom command.

## Local, WSL, and SSH Feasibility

### Native local agents

This is the best first target. The agent can reach a loopback-only MCP server
in the GridVibe process and load a temporary config file.

### WSL agents

MCP itself is supported, but transport to a Windows loopback listener varies
with WSL networking mode.

The implementation spike should test:

1. direct WSL access to the generated Windows-host endpoint;
2. a Windows stdio bridge launched through WSL interoperability;
3. path translation and quoting for temporary config files.

Do not bind the MCP server to all interfaces merely to make WSL convenient.

### SSH agents

An agent running on the remote machine sees that machine's loopback, not the
GridVibe host's loopback. Initial UI behavior should therefore be:

- GridVibe tools unavailable for SSH agent sessions;
- clear explanation in the launcher;
- ordinary agent launch remains available.

A later phase may create a capability-scoped SSH reverse tunnel or a mutually
authenticated remote MCP endpoint. That work needs a separate security
review. It must preserve SSH host-key policy and must never put GridVibe's
general terminal-control endpoint on an unauthenticated network bind.

## Security Model

The MCP is a privileged control plane, not merely another convenience API.

Required controls:

1. Bind to loopback only.
2. Use at least 256 bits of random capability entropy.
3. Store only token hashes in the authority.
4. Scope every token to a controller, group, operation profile, quota, and
   expiry.
5. Deny self-targeting by default.
6. Revoke on controller close and GridVibe shutdown.
7. Never serialize or log tokens.
8. Never return passwords or secret-bearing internal objects.
9. Redact capability/config payloads from startup logging.
10. Use temporary files outside the repository with restrictive permissions.
11. Keep destructive in-page confirmation even when the agent is in Auto
    mode.
12. Rate-limit tool calls and bound all output.
13. Enforce `runtime_config.max_sessions` and delegation quotas server-side.
14. Treat terminal output as potentially prompt-injected content and identify
    it as untrusted tool output in MCP descriptions.
15. Audit control actions at INFO without logging terminal input, tokens, or
    full output.

Recommended audit fields:

- timestamp;
- controller session ID;
- tool name;
- target session/group ID;
- result category;
- confirmation operation ID where relevant;
- task ID and agent type for delegation;
- duration.

Do not log raw task context by default because it may contain proprietary
source details or secrets copied from terminal output.

## Correctness and Concurrency Requirements

### Output delivery

- Append chunks without copying the complete buffer.
- Return a stable cursor and a `truncated_before_cursor` indicator.
- Limit characters and chunks per call.
- Notify waiters after releasing the journal mutation lock.
- Do not use a polling loop for `terminal_wait` or `task_wait`.

### Session lifecycle

- Revalidate the controller and target immediately before each mutation.
- Make create/handoff operations idempotent.
- Treat a disconnected controller as revoked, not merely hidden.
- If an owned child is manually closed, report a terminal task failure rather
  than silently recreating it.
- Preserve room-scoped Socket.IO status/output emissions.

### Shared locks

- Snapshot manager state under `SessionManager.lock`.
- Snapshot/pop live connections under `connection_lock`.
- Perform PTY writes, shutdowns, emits, MCP serialization, and waits after
  shared locks are released.
- Preserve `connection_lock -> SessionManager.lock` as the only permitted
  nested order.

## Data Model Changes

Persist only the user's launch preference:

```text
agent_gridvibe_tools: false
agent_gridvibe_scope: "delegate"
```

These fields would need normalization and round-trip coverage in:

- `TerminalSession`;
- `SessionManager.create_sessions()`;
- saved launcher sessions;
- workspace capture/restore;
- launcher default/draft normalization;
- `/api/sessions` create and response tests.

Do **not** persist:

- capability tokens;
- MCP endpoint ports;
- generated config paths;
- controller-owned child IDs as durable authority;
- terminal output cursors.

The runtime capability registry should be rebuilt for restored enabled agent
sessions only when the agent itself is relaunched.

## Proposed Modules

```text
web/
  session_control.py       shared application operations
  mcp_control.py           MCP lifecycle, auth, and tool registration
  agent_mcp.py             capability authority + launch config adapters
  terminal_io.py           existing connections; gains cursor journal hooks
  agents.py                registry/preflight; delegates MCP launch rendering
  static/js/
    agent-control.js       runtime requests, confirmation, status UI
```

The exact split may change during the spike, but MCP code should not be added
directly to `web/api.py`, and substantial UI behavior should not be put back
into `terminals.js`.

## Delivery Plan

### Stage 0: technical spike

Goal: prove one safe vertical slice with a native local Codex or Claude
controller.

- Add a minimal loopback MCP server.
- Inject a temporary one-session MCP config.
- Implement `workspace_get`, `terminal_read`, and a no-op diagnostic tool.
- Add ephemeral capability issuance/revocation.
- Confirm no token appears in process arguments, logs, saved sessions, or
  runtime state.
- Verify source and packaged Windows launches.
- Verify the MCP server stops cleanly with GridVibe.

Exit criteria:

- the selected agent sees GridVibe tools automatically;
- it can list only its permitted group;
- closing the controller immediately invalidates the MCP connection;
- normal GridVibe browser/WebView behavior is unchanged.

### Stage 1: observe and delegate-owned sessions

- Add launcher checkbox and `Observe`/`Delegate` scopes.
- Add cursor-based terminal output and event-driven waits.
- Add `agent_launch`, `agent_message`, and child ownership.
- Support one or two well-validated agent adapters first.
- Enforce quotas, self-exclusion, idempotency, and token redaction.

Exit criteria:

- a controller launches a child agent in another pane;
- the child receives and acknowledges a structured task;
- the controller can wait for output/task state without polling;
- the controller cannot write to pre-existing sibling terminals.

### Stage 2: task lifecycle and handoff

- Add task registry and task state tools.
- Add completion, blocker, cancellation, and handoff records.
- Add agent-specific startup prompt adapters.
- Add depth/cycle controls and failure propagation.
- Add UI task/operation status and retry affordances.

Exit criteria:

- Agent A can delegate to Agent B;
- Agent B acknowledges, reports completion/blocker, and returns structured
  context;
- Agent A receives the result without scraping terminal output;
- crashes and manual closes produce explicit failed/blocked task states.

### Stage 3: broader terminal control

- Add `Group control`.
- Add approved leases for existing sibling terminals.
- Add reconnect, shell change, mode change, session close, and group close.
- Route the equivalent Flask handlers through `SessionControlService`.

Exit criteria:

- MCP and UI operations have identical validation and state transitions;
- destructive calls use in-page confirmation;
- lock-order and room-scope regression tests pass.

### Stage 4: remaining agent and environment adapters

- Validate Codex, Claude, Copilot, Kimi, Kilo, and OpenCode.
- Add WSL transport/quoting support.
- Decide separately whether SSH reverse tunneling is worth the security and
  maintenance cost.

## Test Plan

### Unit tests

- capability scope, expiry, revocation, and token hashing;
- controller self-target denial;
- owned-child and sibling permission rules;
- idempotency and quota enforcement;
- safe MCP response serialization;
- output cursor, truncation, bounds, and waiter notification;
- task state machine, handoff depth, cycles, and cancellation;
- launch adapter quoting for cmd, PowerShell, POSIX, and WSL;
- saved-session/runtime-state preference round trips without secret state.

### Integration tests

- MCP initialize/list-tools/call-tool with valid and invalid capabilities;
- actual temporary-config launch for each supported CLI adapter;
- controller close while a tool call is waiting;
- simultaneous output append/read;
- manual close of a delegated child;
- confirmation accepted, rejected, expired, and UI unavailable;
- max-session race between UI and MCP launches;
- Socket.IO events remain room-scoped.

### Security regression tests

- no capability in `TerminalSession.to_dict()`;
- no capability in saved sessions, workspace snapshots, or logs;
- non-loopback connection rejected;
- forged controller/target IDs rejected;
- expired confirmation lease rejected;
- Auto mode does not bypass server-side control scope or destructive
  confirmation;
- SSH sessions cannot receive a local-only capability.

Run the full project checks after implementation:

```text
python tests/run_tests.py
python -m ruff check .
```

## Main Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Auto mode auto-approves MCP calls | Agent gains more control than expected | Independent GridVibe scope and server-side enforcement |
| Raw terminal input is arbitrary execution | Existing shell can be commandeered | Owned-child default; sibling lease; self-target denial |
| TUI readiness is guessed from terminal text | Delegated prompt is lost or corrupts UI | Task inbox + acknowledgement + agent launch adapter |
| MCP token leaks in args/logs/state | Any local process/user may reuse authority | Temp config/env indirection, redaction, hash-only registry, expiry |
| Duplicate tool retry launches terminals twice | Session sprawl and duplicate work | Required idempotency keys |
| Output reading becomes expensive | Busy terminals degrade all sessions | Sequenced deque journal and bounded reads |
| MCP and HTTP validations drift | Different behavior by control path | Shared `SessionControlService` |
| Remote SSH exposes control plane | Host compromise or network access | No SSH support initially; separate tunnel/security design |
| Agent CLI config formats change | Launch adapters break | Registry capability flags, adapter preflight, focused tests |

## Open Product Decisions

1. Should enabling GridVibe tools default to `Observe` or `Delegate`?
   Recommendation: `Delegate`, with very explicit copy that it can create and
   control child agent terminals.

2. May a controller launch a different agent family?
   Recommendation: yes, but only from known registry entries that pass
   preflight.

3. Should delegated tasks survive a GridVibe restart?
   Recommendation: no in the first version. Mark interrupted tasks failed on
   shutdown; design durable recovery after the state model is proven.

4. Should a controller read existing sibling terminal output?
   Recommendation: metadata only under `Delegate`; full output requires
   `Group control`.

5. Should workspace-wide control exist?
   Recommendation: defer it. Group-scoped authority is easier to understand
   and matches GridVibe's existing room/group boundaries.

6. Is SSH agent control a launch requirement?
   Recommendation: no. Ship local native support first, then WSL, and evaluate
   SSH only with a separate threat model.

## Final Recommendation

Proceed with a Stage 0 spike.

The feature should be framed as a **capability-scoped GridVibe control plane**,
not merely "turn the existing session API into MCP." The distinction is what
makes delegation useful without granting an Auto mode agent unrestricted
access to every live shell.

The most valuable first end-to-end result is:

1. enable **GridVibe tools → Delegate** on a local agent;
2. let that controller inspect its group;
3. launch one owned child agent with a structured task;
4. receive an acknowledgement and completion result;
5. revoke all authority when the controller closes.

If that vertical slice is reliable, the remaining terminal management
operations are incremental additions to the same control service and
authorization model.
