# MCP Agent Model Selection: Implementation Plan

Let a person request, for example, five OpenCode agents using a named DeepSeek
model. The GridVibe MCP agent checks the target environment, presents the exact
model or matching choices, waits for the person's confirmation, then launches
the panes with that model.

This is a working plan, not an implemented feature or a contract. Once shipped,
move the resulting guarantees into the owning contracts and MCP reference.
Baseline: repository inspected on 2026-10-02; no application changes are part
of this document.

## Scope and decisions

| Area | Decision |
| --- | --- |
| Eligible agent CLIs | Exactly `claude`, `codex`, and `opencode` |
| Meaning of capability | Capability to select a model explicitly; separate from CLI installation, MCP support, task support, and account access |
| Entry points | MCP `launch_panes`, `split_pane`, and `set_pane_agent` |
| Selection interface | Conversation with the calling agent; no launcher, pane dropdown, settings, or dashboard model controls |
| Confirmation | Required after lookup, even for one exact match; choosing a displayed candidate counts as confirmation |
| Multiple matches | Show provider, full name/version, and exact launch ID, plus Other and Cancel |
| Other | Accept new wording or an exact ID, look it up again, and ask again; never an unchecked escape hatch |
| No explicit model | Existing launch behavior, without a new model question |
| Other CLIs | Retain existing launches; refuse any explicit model selection and never mark them model-capable |
| Persistence | Preserve the chosen model in the live pane and runtime workspace snapshot; named launcher presets remain model-free in this first version |

Here, Claude, Codex, and OpenCode are **agent CLIs**. A provider is the backend
inside a CLI, such as DeepSeek or another configured OpenCode provider. Support
the three CLIs, rather than restricting OpenCode to one backend. Two backends
offering the same model are two distinct candidates.

Do not add generic CLI arguments, install or connect providers, change global
model defaults, introduce a model preference setting, or add model effort,
variant, service-tier, or permission controls. Model choice never implies
`auto_mode`. Do not add another agent type for each model.

## Required conversation

1. Resolve the requested CLI, pane count, target machine/shell, and working
   directory. For a request without a CLI, ask which of the three to use.
2. Check CLI availability and model capability through `list_agent_types`.
3. Call the proposed `list_agent_models` for the actual destination context.
   Use its data, not remembered model names or a public catalog alone.
4. Present the result and wait for a new user response:
   - One match: "Use [full model name] through [provider], ID [exact ID], for
     all five OpenCode agents?"
   - Several matches: numbered choices with those same facts, Other, and
     Cancel; ask which to use for the stated count.
   - No match: explain that the requested model was not found. Any suggested
     alternatives require a new explicit choice.
   - Check failed: explain what could not be checked; do not launch with a
     guessed ID or default model.
5. Launch only after the person confirms a displayed exact candidate. A choice
   such as "option 2" is sufficient; do not ask a redundant second question.
6. Report the requested model ID and each pane's launch outcome accurately.
   Launching a process does not prove that its first model request succeeded.

"Flash" must not silently resolve to "Pro". Preserve the requested family and
qualifiers when filtering. If the same Flash model is offered through multiple
providers, show each provider separately. Do not select the cheapest, newest,
first, or default candidate automatically. Examples in this plan are illustrative
names, not a maintained model catalog.

Confirmation applies to the model, CLI, destination context, and specified
batch. One confirmation can cover all five identical panes. Changing the model
or provider requires a new choice. A materially different destination requires
a fresh check and confirmation. Cancellation or silence creates no panes.

## Minimal tool additions

### Capability

Add `model_supported: boolean` to the projected `list_agent_types` rows.
One backend owner defines the fixed set `{claude, codex, opencode}` and the
startup flag mapping. Do not infer this capability from `--help`, `mcp_supported`,
or `task_supported`, and do not expose it as a launcher option.

`model_supported` means GridVibe implements explicit model selection for the
CLI. It does not mean every installation/version can enumerate models or every
listed model is accessible to the account. A failed discovery must say so.

### Discovery

Add one read tool, `list_agent_models`, backed by one thin HTTP route and a
backend discovery service. Proposed inputs:

| Input | Meaning |
| --- | --- |
| `agent` | Required; one of the three eligible registry keys |
| `query` | Optional literal search words or exact ID; bounded plain text, never a command |
| `directory` | Optional absolute destination directory; omit to use the same directory resolution as the intended launch |
| `shell` | Optional local shell family, with existing target restrictions |
| `pane_id` | Optional source/target pane for a split or relaunch; omission uses the caller's launch context |

Resolve the target with the existing launch/split/relaunch rules. Never use the
GridVibe host's catalog to answer for WSL or SSH. Project-local configuration
must be read in the directory the agent will actually start in. Discovery
cannot grant mutation permissions or bypass existing pane gates.

Return explicit projected fields: `agent`, a secret-free target description,
effective directory/shell, `status`, `message`, `models`, and `truncated`.
Each candidate contains `model` (the exact launch argument), `display_name`,
`provider`, `source`, and `account_access` (`true`, `false`, or `null`).
Use statuses `ok`, `unsupported`, `unavailable`, and `check_failed`.
`ok` with no matches differs from a failed probe. Never present a truncated
catalog as complete; narrow the query before resolving ambiguity.

Return selectable candidates from the effective configured model catalog.
Exclude disabled/unconfigured providers and known-inaccessible models. Some
CLIs report catalog support without proving account entitlement: return
`account_access: null` and explain that limitation in the confirmation rather
than claiming verified access. Do not perform a billable inference to check it.

### Launch arguments

Add optional `model` and `model_confirmed` to each agent pane in `launch_panes`
and to `split_pane` and `set_pane_agent`. Keep existing schemas strict.

- A nonempty `model` must be an exact ID returned by the target adapter, not an
  alias or free-text search. Require literal boolean `model_confirmed: true`.
- Refuse models on terminals, browsers, explorers, unknown/custom agent commands,
  or any CLI outside the three-key capability set.
- Refuse a confirmation field without a nonempty model. An empty string is not
  a new "use default" operation in this version; omit both fields instead.
- Revalidate the candidate against the effective target catalog before mutation.
  A missing model, changed provider/configuration, failed check, or incomplete
  catalog refuses the request. No fallback to a different model or default.
- Preflight the entire batch before creating a workspace, group, pane, or task
  handoff. Probe once per distinct CLI/destination context within that operation,
  rather than five times for five identical panes.
- A changed target pane during a slow relaunch check refuses at commit under
  the existing identity guard. Model checks run outside shared locks.

Put the complete check/ask/wait rule in discovery and model-taking tool
descriptions and in `gridvibe_mcp/README.md`. `model_confirmed` is the agent's
declaration that it received the user's choice; MCP cannot independently read
the conversation. It can enforce a valid checked model and a required
declaration, but cannot prove that the agent actually asked. Do not describe
this as a user-authenticated approval. A stronger UI-backed approval mechanism
would be a separate scope decision, not part of this minimal change.

No new confirmation-token store, durable approval records, dedicated launch
tool, or changes to existing `override`/`agent_mcp_override` behavior.

## Discovery adapters: verify before implementing the launch surface

| CLI | Startup argument | Discovery approach |
| --- | --- | --- |
| Claude Code | `--model <exact ID>` | First verify a noninteractive, metadata-only source in the installed CLI/Agent SDK initialization API. Claude's documented aliases alone are insufficient to enumerate account-specific exact candidates. |
| Codex | `--model <exact ID>` | Initialize a short-lived stdio `codex app-server`, read `model/list` with bounded pagination, then close it. Start no thread or turn. Verify that configuration and cwd match the forthcoming CLI launch. |
| OpenCode | `--model <provider/model>` | Use `opencode models` in the destination context. Verify how the installed version distinguishes the public catalog from connected/enabled providers; filter through a supported metadata source if needed. |

The startup flags are established; uniform discovery is **not yet established**.
Before claiming completion, prove all three adapters can inspect metadata
without submitting a prompt, invoking tools/hooks, initializing project files,
installing dependencies, changing authentication/configuration, or opening a TUI.
Do not scrape interactive pickers or read credentials into responses.

Prefer existing CLI installations and standard-library protocol handling; do
not add an SDK dependency merely to list models. If Claude requires a new
dependency or an undocumented protocol, record the exact tradeoff before
implementation proceeds. Do not replace a missing adapter with hard-coded
"current" models. Older versions or unsupported target transports return an
actionable `check_failed`/`unavailable` result and refuse model-specific launches.

Bound process lifetime, process-tree cleanup, captured bytes, catalog size,
pagination, and concurrent probes using the existing process/preflight patterns.
Suggested initial ceilings: 15 seconds per probe, four concurrent probes,
1 MiB captured output, 2,000 catalog entries, and 100 returned matches. Mark a
limit hit explicitly; never validate against an incomplete result. Keep the
complete operation under a 40-second deadline, including cleanup, so batches
cannot multiply the per-probe deadline beyond the existing 50-second read
budget. Keep the client's deadline above the server's complete bound. No global
or persistent model cache is required for the first version.

A local check on 2026-10-02 found that `opencode --help` fails with `EEXIST`
for its user configuration directory. That is an environment issue to report,
not something this feature should repair or treat as a successful catalog read.

## Launch composition and state

Store one internal field, `agent_model`, separate from `initial_command` and
`agent_selection`. Leave `initial_command` as the registered CLI binary.
Compose the model argument in `_compose_agent_startup_command`, before any
variable-length MCP config arguments. Preserve title, MCP, task-opening prompt,
conversation, and auto-mode behavior.

Validate the exact ID as a bounded single argument and quote it for the owning
PowerShell/cmd/POSIX shell; refuse controls, whitespace, shell metacharacters,
or leading option prefixes. Use IDs from the adapter and test provider/model
paths and provider-specific IDs. Do not accept user-supplied command fragments.
Do not apply a second model flag to custom commands.

| Transition | Model behavior |
| --- | --- |
| New MCP agent with a confirmed model | Store and compose that exact model |
| New MCP agent without a model | CLI's existing default behavior |
| Same-agent reconnect/runtime workspace restore | Preserve the stored selection; no new interactive choice |
| MCP relaunch stating a new model | Check and confirm before replacing the pane's process |
| Same-agent relaunch omitting model | Preserve the existing model; include it in any existing relaunch confirmation summary |
| Change agent CLI or switch to plain shell | Clear the old model; never carry it into another CLI |
| Split creating a plain terminal or a new agent without a model | Clear any cloned model |
| Save/load named launcher preset | Omit model selection; presets continue to launch the CLI's configured default |

Include `agent_model` in relaunch change/no-op comparisons: a model-only change
must restart the agent even when CLI, shell, and MCP settings are unchanged.
Repeating the same exact model with unchanged settings remains a no-op.

The stored field is the requested startup model, not live telemetry. An agent
may later switch models internally; do not claim the field tracks that switch.
Persist the field in runtime snapshots only, and never persist
`model_confirmed`, catalogs, account details, probe output, or credentials.
Restoring the same pane reuses its previously selected startup model. A new
model-specific MCP request still requires the complete check/confirmation flow.

Only the MCP paths may accept a new explicit model. Launcher and header inputs
must not establish `agent_model`, even if a crafted payload supplies it.
Backend split-intent processing must carry the field unchanged; add invisible
frontend transport plumbing only if the existing page-owned split path would
otherwise drop it. No new selection controls or visual changes.

## Smallest expected code footprint

| Owner | Planned work |
| --- | --- |
| New `web/agent_models.py` | Fixed capability set, adapters, target-aware catalog checking, ID validation; one shared owner |
| `web/agents.py` | Capability row and model fragment in the existing command builder; delegate policy to the new owner |
| `web/api.py` / `web/workspaces.py` | Thin discovery route, reuse target resolution, model preflight before whole-batch creation |
| `gridvibe_mcp/server.py` | One read tool; optional model fields on the three mutation tools; common validation and descriptions |
| `gridvibe_mcp/client.py` | Read wrapper and explicit capability/candidate/pane result projections |
| `web/window_intents.py` and owning split handler | Preserve validated model across the existing split intent; do not delegate validation to the page |
| `web/session_shell.py` / `web/session_modes.py` | Model carry/clear rules, gated relaunch validation, target identity checks |
| `sessions/manager.py` / `web/runtime_state.py` | Live field, metadata/creation allowlists, runtime snapshot round trip |
| `web/saved_sessions.py` | Explicitly keep named presets model-free and avoid accidental field inheritance |

Trace each touched field consumer before editing. Keep routes thin and preserve
the sidecar/backend boundary: discovery lives in the backend; the sidecar
reaches it through HTTP. Both MCP transports use the same dispatch.

## Implementation order and acceptance checks

1. **Prove discovery.** Record supported installed versions, metadata source,
   target/cwd behavior, and account-access limitations for all three adapters.
   Resolve any adapter blocker before advertising the feature as complete.
2. **Add the shared owner and read surface.** Implement capability, discovery,
   bounded execution, projected results, and actionable failures.
3. **Wire the existing mutations and state.** Add model arguments, confirmation
   declaration, complete preflight, command composition, and runtime persistence.
4. **Verify behavior and document the shipped guarantees.** Update the MCP
   reference, engineering contracts, runtime-state guide, and one changelog
   entry; do not add a launcher feature or duplicate the tool reference in README.

Use behavioral fixtures for catalogs and fake bounded transports; automated
checks must not require account credentials or paid model calls. Cover:

- Exactly three capable CLI keys; other CLI launches without models unchanged.
- One match requires confirmation; multiple Flash matches stay distinct by
  provider; Other triggers another lookup; cancel/no reply causes no mutation.
  Review actual conversation behavior manually as well as tool descriptions.
- Missing/false/nonboolean confirmation, partial ID, wrong CLI, wrong pane
  kind, unknown model, failed probe, incomplete catalog, and injection inputs
  refuse without panes, handoffs, teardown, or workspace creation.
- One invalid pane refuses an entire five-pane batch; identical contexts probe
  once within the operation. No silent model fallback.
- Local shells, WSL, SSH, project-local config, and changed/closed targets use
  the right catalog or explicitly refuse unsupported discovery.
- Model arguments compose correctly with MCP, task prompts, title overrides,
  auto mode, and conversation resume across the supported shells.
- Runtime snapshots round-trip the model; agent changes/plain splits clear it;
  named presets omit it; launcher/header payloads cannot set it; a model-only
  relaunch changes the process and an identical selection is a no-op.
- Process timeout/output/pagination caps clean up children; projected tool
  results contain no credentials or raw configuration.
- stdio and HTTP MCP dispatch agree, and the optional MCP SDK remains optional.

Extend the relevant existing suites (`test_agent_types`, `test_mcp_tools`,
`test_mcp_client`, `test_mcp_launch`, `test_mcp_remote`, `test_session_shell`,
`test_runtime_state`, `test_saved_sessions`, and split-intent behavior), adding
`test_agent_models` for the new owner.
Run the affected behavioral tests and repository-wide linter. Broaden to
`make check` only under the repository's existing cross-cutting-change rule.

## References

- [Engineering contracts](engineering_contracts.md#agent-tools-mcp),
  [pane transitions](engineering_contracts.md#pane-transitions), and
  [session state guide](session_state_guideline.md).
- [GridVibe MCP reference](../gridvibe_mcp/README.md#which-agents-a-tool-can-start).
- [Claude CLI model argument](https://code.claude.com/docs/en/cli-reference)
  and [model configuration](https://code.claude.com/docs/en/model-config).
- [Codex app-server model discovery](https://learn.chatgpt.com/docs/app-server#list-models-modellist):
  account/client-dependent catalogs; use returned IDs and bounded pagination.
- [OpenCode CLI](https://opencode.ai/docs/cli/): `--model` takes `provider/model`;
  `models` lists catalog entries and `--refresh` updates the cache.
- [OpenCode provider setup](https://opencode.ai/docs/providers/#deepseek):
  connecting a provider is separate from selecting its model.
