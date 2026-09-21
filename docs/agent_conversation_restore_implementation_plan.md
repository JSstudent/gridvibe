# Agent Conversation Restore — Phase 1 Implementation Plan

Status: proposed; not implemented.

This plan adds exact conversation restoration for the four agent CLIs whose
identity can be obtained without searching a provider's state directory:
Claude Code, GitHub Copilot CLI, Grok Build, and OpenAI Codex CLI.

The feature belongs to workspace restore. A restored workspace should reopen
the same agent conversation when GridVibe knows its exact provider session ID.
Reusable saved-session presets remain templates and continue to start fresh
conversations.

## Goals

- Give each supported newly launched agent pane a stable, opaque conversation
  identity as early as the provider permits.
- Carry that identity through autosave, explicit Save Workspace, voluntary-exit
  capture, and server-side workspace restore.
- Resume by exact ID; never select "latest" and never infer an ID from a
  human-readable conversation title.
- Work for local, WSL, and SSH panes without reading provider files from the
  GridVibe host.
- Preserve old snapshots: an absent identity means "start fresh", not invalid
  state.
- Fail visibly when a stored conversation no longer exists. Do not silently
  replace it with a new conversation.

## Non-goals

- Persisting conversation identity in `saved_sessions.json` or launching the
  same conversation from a reusable preset.
- OpenCode, Kimi Code CLI, or Hermes Agent state-store/API discovery. Those are
  follow-up adapters because they require identifying a running session among
  provider-owned records.
- Kilo Code `--continue`. It selects the latest conversation for a workspace,
  which is ambiguous when several panes use the same directory.
- Custom agent commands. Existing custom commands remain verbatim and cannot be
  safely rewritten into provider-specific resume commands.
- A dashboard session picker or conversation-search UI.
- Preserving a live process. GridVibe still restores workspace shape by
  launching a new CLI process against the provider's saved conversation.

## Product Decisions

### Workspace snapshots own exact identity

`runtime_state.json` represents the same workspace and panes coming back after
a backend restart. It may therefore carry an exact agent conversation ID.

`saved_sessions.json` represents reusable launch configuration. It must not
carry a conversation ID: launching a preset twice must not attach two panes to
one provider conversation or mutate the same transcript concurrently.

The lifecycle path that saves reusable session presets before exit must omit
conversation identity even when the subsequent workspace capture includes it.

### Titles are observations, not identity

The dashboard conversation line comes from OSC terminal titles. A title may be
provider text, a user-assigned name, a shell title, or an opaque identifier.
It remains presentation only. Phase 1 accepts an OSC value as identity solely
for the Codex-specific strategy below, and only when the whole value is a valid
UUID.

### Exact resume or visible failure

Restore must use an exact provider ID. If the provider has deleted the
conversation, the account or target machine changed, or the installed CLI no
longer supports the recorded command, the CLI's error remains visible and the
pane returns to its shell through the existing agent-exit observation. GridVibe
must not retry as a fresh conversation.

### Identity becomes unknown after an in-agent switch

An agent TUI may let the reader create, resume, or select another conversation
without restarting the process. The launch-time ID is then stale. When
GridVibe observes a known session-switching command, it clears the stored
identity before the command is acted on. A later authoritative observation may
set a new one. Saving while the identity is unknown restores a fresh agent,
which is safer than resuming the wrong transcript.

Phase 1 does not claim to identify a conversation selected from a provider's
interactive picker unless that provider subsequently announces its UUID.

## Provider Capability Matrix

| Provider | New conversation | Workspace restore | Identity source |
| --- | --- | --- | --- |
| Claude Code | `claude --session-id <uuid>` | `claude --resume <uuid>` | UUID assigned by GridVibe |
| GitHub Copilot CLI | `copilot --session-id <uuid>` | `copilot --resume=<uuid>` | UUID assigned by GridVibe |
| Grok Build | `grok --session-id <uuid>` | `grok --resume <uuid>` | UUID assigned by GridVibe |
| OpenAI Codex CLI | ordinary `codex` launch | `codex resume <uuid>` | whole-title UUID from the existing `thread-title` OSC stream |

Provider behavior is versioned external behavior. The registry capability must
be treated as verified metadata, and implementation tests must exercise command
composition without assuming that every agent accepts flags in the same order.

Current references:

- OpenAI Codex CLI: <https://learn.chatgpt.com/docs/developer-commands?surface=cli#codex-resume>
- Claude Code CLI: <https://code.claude.com/docs/en/cli-usage>
- GitHub Copilot CLI: <https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference>
- Grok Build CLI: <https://docs.x.ai/build/cli/reference>

## Data Model

Add two durable launch fields to `TerminalSession`:

```text
agent_conversation_provider: str = ""
agent_conversation_id: str = ""
```

Add one live-only launch decision:

```text
agent_conversation_resume: bool = False
```

Rules:

- `agent_conversation_provider` is a normalized registry key and must equal the
  pane's effective `agent_selection` before its ID can affect a command.
- Phase 1 IDs are canonical lowercase UUID strings. An entirely absent pair is
  backward-compatible and means that no resumable identity was captured. A
  present but incomplete, invalid, oversized, wrong-typed, or
  provider-incompatible pair is an invalid launchable shape and invalidates its
  stored group. Invalid live launch payloads are rejected at the request
  boundary.
- Add the durable pair to `TerminalSession.to_dict()` so workspace snapshots can
  persist it. `agent_conversation_resume` is never durable and is excluded from
  pane dictionaries. A normal launch starts false; restore sets it true.
- Conversation identity is meaningful only while `startup_mode == "agent"` and
  the selected agent still matches the provider. Leaving agent mode, changing
  provider, a failed preflight that clears agent identity, or observed agent
  exit clears all three values.
- IDs are opaque launch metadata. Do not add them to dashboard `PANE_FIELDS`,
  user-facing labels, launch summaries, or routine logs. Review generic session
  serializers and routes before relying on them for transport; the
  workspace-shape API is the only intended public persistence surface. Logs may
  state only whether an identity was assigned, observed, cleared, or requested
  for resume.

Add the two durable fields to `_SESSION_SNAPSHOT_FIELDS`. Do not add them to
saved-session defaults, terminal-entry normalization, preset merging,
presentation fields, dashboard `PANE_FIELDS`, or reusable-preset builders.

## Capability Ownership

Add a small `conversation_restore` capability to the relevant entries in
`agent_registry.json`. It should describe a bounded strategy, not an arbitrary
shell template. Suggested values:

```json
{"strategy": "assigned_uuid", "create_style": "flag", "resume_style": "flag"}
```

and for Codex:

```json
{"strategy": "osc_uuid", "resume_style": "subcommand"}
```

The exact supported styles and provider command shapes remain an allowlisted
Python decision. Registry JSON must never become an evaluator for unrestricted
command fragments.

Create `web/agent_conversations.py` as the domain owner for:

- capability lookup;
- UUID normalization and canonicalization;
- assigning an ID for a supported new launch;
- composing the provider's create/resume command from a validated ID;
- recognizing a Codex UUID title;
- identifying input lines that invalidate the current identity; and
- clearing identity when agent ownership ends.

Keep `web/agents.py::_compose_agent_startup_command()` as the final command
composer. It asks the new module for the provider base command, then applies the
existing Codex title override, auto-mode flag, and MCP registration exactly
once. Custom or already-modified commands remain verbatim and receive no
conversation rewrite.

## Launch and Capture Flow

### Assigned-UUID providers

1. Normal launch resolves the agent provider after preflight.
2. For Claude, Copilot, or Grok with an unmodified built-in command, generate a
   UUID and store provider plus ID on the pending pane.
3. Compose the provider's new-conversation form using that UUID.
4. After the startup command is successfully handed to the pane's current
   connection, flip the live-only decision to resume. A later transport
   reconnect must resume rather than attempt to create the same UUID again.
5. If startup never reaches command delivery, the pane retains create intent so
   an ordinary connection retry can still create it.

The transition in step 4 must verify exact connection and pane ownership. A
retired pump must not change the replacement connection's launch decision.

### Codex OSC discovery

1. Launch Codex normally with the existing
   `tui.terminal_title=['thread-title']` override.
2. The existing stream observer continues to parse OSC title events outside
   shared locks.
3. When the current pane is a built-in Codex agent and the whole announced
   thread title is a UUID, publish the canonical UUID and provider to that exact
   live pane.
4. Perform the same registry-entry identity check used by cwd publication and
   agent retirement. Mutate manager metadata only after the observer has
   released parsing work; broadcast only after locks are released.
5. Human-readable title changes continue to drive the dashboard but do not
   replace the captured UUID.

### Snapshot capture

The runtime snapshot reads the already-known pane fields while holding the
manager lock. It performs no provider command, process inspection, filesystem
read, or network operation. A pane whose identity is still unknown simply
omits empty values through normal snapshot normalization.

## Restore Flow

1. Runtime-state validation accepts a wholly absent pair for backward
   compatibility. If either field is present, both must be present, correctly
   typed, valid, and compatible with the selected provider; otherwise the whole
   group is invalid.
2. `_restore_group_request()` copies the captured pair with the rest of the
   pane shape and marks the launch as resume-only.
3. `_prepare_launch_sessions()` preserves the pair only for an agent pane whose
   effective provider matches it.
4. `TerminalSession` is installed before connection work begins.
5. `_compose_agent_startup_command()` asks the conversation module for the
   provider's exact resume form, then applies existing auto-mode and MCP
   additions.
6. Missing history produces the provider's visible failure. GridVibe does not
   fall back to the ordinary base command.

The snapshot's stored directory remains the pane launch directory for the
restored process. Provider history remains owned by the same target machine and
user account. Restoring against another home directory or an erased provider
store is expected to fail visibly.

## In-Agent Switching and Exit

Extend the existing submitted-input tracker with a narrow, provider-owned list
of commands that can replace conversation identity, such as `/new`, `/clear`,
`/resume`, or `/sessions` where supported. Match only complete submitted lines,
not partial keystrokes or visible agent output.

- Clear identity before an interactive picker or new-conversation command.
- Do not trust an ID merely because the reader typed it; the provider may
  refuse. A later authoritative UUID observation may repopulate it.
- Codex can recover identity when the replacement thread announces a UUID.
- Assigned-UUID providers remain unknown after an in-TUI switch in phase 1.
- `_mark_runtime_agent_exited()` and all transitions out of agent mode clear
  the identity together with the agent metadata and title floor.

This conservative behavior may restore a fresh conversation after an
unobservable in-TUI switch, but it cannot resume a different known-old
conversation by mistake.

## Security and Failure Rules

- Parse IDs with the UUID library and re-serialize the canonical value. Never
  interpolate raw snapshot or request text into a shell command.
- Quote through the target shell's existing quoting helpers even after UUID
  validation; command composition should have one consistent boundary.
- Do not place IDs in dashboard payloads, launch summaries, routine logs, or
  error messages.
- Do not inspect provider state directories while holding `connection_lock` or
  `SessionManager.lock`; phase 1 should not inspect them at all.
- Unsupported providers and custom commands with no identity pair start fresh.
  A present malformed, incomplete, or provider-mismatched pair is rejected and
  must never be silently downgraded to a fresh launch.
- A valid stored pair on an explicit restore is a promise to resume. Failure is
  reported, never converted into a fresh conversation.

## Implementation Steps

1. Add provider capability metadata and `web/agent_conversations.py` with pure
   normalization and command-planning functions.
2. Add the live/durable fields to `TerminalSession`, session creation, and the
   runtime snapshot allowlist and validator.
3. Assign UUIDs during normal built-in Claude, Copilot, and Grok launches.
4. Integrate provider create/resume planning into the existing startup-command
   composer without duplicating auto-mode or MCP composition.
5. Mark assigned identities resume-only after successful command delivery and
   preserve that decision across transport reconnects.
6. Publish Codex UUID titles from the stream observer to the exact current
   pane.
7. Set restore intent in the server-side restore request and preserve it through
   launch preparation.
8. Clear or invalidate identity in agent exit, provider change, mode change,
   failed preflight, and known in-agent switching paths.
9. Confirm every reusable-preset writer and normalizer strips or ignores the
   new fields.
10. Update the state and terminal contracts after behavior is implemented, then
    add a concise user-facing changelog entry.

## Test Plan

Add focused behavioral coverage rather than source inventories.

### Pure provider planning

- Each assigned provider receives a canonical UUID and its correct create
  command.
- Each of the four providers receives its exact resume command.
- Codex accepts a whole UUID title and rejects prose, partial UUIDs, and UUIDs
  from another provider.
- Provider mismatch, custom command, invalid UUID, unsupported provider, and
  non-agent pane produce no rewrite.
- Auto-mode, Codex title configuration, and MCP arguments are present exactly
  once in both create and resume forms.

### Persistence

- `TerminalSession` carries provider and ID.
- Runtime capture and restore round-trip the pair.
- Snapshots written before the fields existed still restore as fresh agents.
- A present wrong-typed, incomplete, malformed, or provider-mismatched stored
  pair invalidates the whole workspace group.
- `saved_sessions.json`, dashboard payloads, lifecycle preset saves, and live
  launch summaries contain neither field.

### Ownership and lifecycle

- A retired connection cannot publish a Codex UUID to its replacement.
- A human-readable title after the UUID does not erase identity.
- Agent exit, mode switch, provider relaunch, failed preflight, `/new`, and an
  interactive resume command clear identity.
- Successful assigned-provider command delivery changes reconnect behavior from
  create to resume; failed delivery does not.
- Restore never falls back to fresh launch after an exact resume failure.

### Integration

- Normal launch and server-side restore use the same command composer.
- Local, WSL, and SSH command plans use the same provider ID while retaining
  their existing shell quoting, MCP transport, and auto-mode behavior.
- A multi-pane workspace restores each pane with its own ID.
- Two launches from the same reusable preset receive distinct fresh IDs and do
  not resume any snapshot conversation.

Likely test owners:

- `tests/test_session_persistence_contract.py`
- `tests/test_multi_workspace.py`
- `tests/test_mcp_launch.py`
- `tests/test_agent_activity.py`
- `tests/test_agent_runtime_exit.py`
- a new focused `tests/test_agent_conversation_restore.py`

Run the Windows equivalent of `make check` before handoff:

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe tests/run_tests.py
```

## Acceptance Criteria

- Saving and restoring a workspace containing active built-in Claude, Copilot,
  Grok, or identified Codex panes resumes each exact conversation.
- Launching or re-launching a saved-session preset always starts a fresh
  conversation.
- An old snapshot with no identity restores with existing behavior.
- A missing provider conversation never silently becomes a new conversation.
- No conversation ID appears in dashboard responses or routine logs.
- Switching conversations inside a TUI cannot leave a known-stale ID eligible
  for capture.
- Existing agent title, activity, auto-mode, MCP, remote transport, reconnect,
  and runtime-exit behavior remains intact.

## Follow-up Phases

- Add authoritative OpenCode discovery through its supported session JSON/API.
- Add Kimi discovery through its supported session index or API without editing
  provider-owned files.
- Add Hermes discovery through its supported session-list interface.
- Reconsider Kilo only when it exposes deterministic exact-ID resume.
- Build the separate agent-dashboard session search/switch feature after every
  participating provider has an authoritative listing adapter.
