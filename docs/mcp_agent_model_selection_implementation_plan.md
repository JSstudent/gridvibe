# MCP Agent Model Selection: Implementation Plan

Prepared on 2026-10-05 against repository HEAD `3453982` and the reviewed
design, which was removed from the tree in `c84064e`; read it with
`git show 2706cfc:docs/mcp_agent_model_selection_plan.md`. This is a plan for
later implementation; none of the feature described here has shipped. Stage 0
evidence is recorded in the
[Stage 0 evidence record](mcp_agent_model_selection_stage0_evidence.md).

**Tracking note.** This plan and its Stage 0 evidence record are tracked in
`docs/` only for the duration of the implementation, so changes to them can be
reviewed in commits. Once the feature ships, both move to `docs/r&d/` and stop
being maintained; neither is a citation of record. Maintained guarantees must
be written into their owning contracts when implementation ships.

## Outcome and scope

Allow MCP callers to discover exact configured model candidates for `claude`,
`codex`, and `opencode`, ask the person to select one, and create or relaunch
panes with that requested startup model. Cover `launch_panes`, `split_pane`
and `set_pane_agent`, preserve the selection in runtime workspace snapshots,
and leave named launcher presets model-free.

Add one discovery tool, one backend route and a shared backend discovery
owner. No model UI, generic argument passthrough, global model preferences,
provider installation, authentication repair, effort controls or inferred auto
mode. A model declaration never waives lineage, self, kind, depth, machine or
override gates. Existing launches that omit model fields retain their behavior.

Read [CLAUDE.md](../CLAUDE.md), the relevant
[engineering contracts](engineering_contracts.md#agent-tools-mcp) and
[session state guide](session_state_guideline.md) before implementation.

## Current implementation and required changes

| Area | Verified baseline | Implementation consequence |
| --- | --- | --- |
| `web/agents.py` | `agent_type_rows` publishes availability/MCP/task/auto-mode capabilities; `_compose_agent_startup_command` preserves the binary and adds conversation, title, MCP and prompt fragments | Publish fixed model capability separately; add one validated model fragment before variadic MCP arguments |
| `web/workspaces.py` | Tool launches are preflighted before destination creation; `agent_availability_target` resolves machine/family rather than effective cwd; module is 2,410 lines | Resolve model context from normalized launch shape; extract launch preparation/orchestration under the existing architecture rule before extending it |
| `gridvibe_mcp/server.py` | Strict schemas, `build_pane_request`, `build_split_pane_request`, `build_launch_request` and one dispatch serve both transports | Add discovery once, validate the paired fields in all mutation builders, and preserve the current tool gates |
| `gridvibe_mcp/client.py` | `agent_types` has a 50 s override; launch, split-intent and relaunch use the ordinary 15 s request budget; published results use allowlists and recursive scrubbing | Add discovery and explicit nested candidate projections; introduce sufficient model-specific mutation deadlines |
| `web/session_shell.py` | Explicit shell/agent/MCP choices restart even when equal; gated relaunch binds target/caller and `_RELAUNCH_BOUND_FIELDS`; ordinary relaunch starts a fresh conversation | Carry/clear model before spawn, add it to the binding, and retain explicit reselection behavior |
| `web/api.py` split paths | `_split_pane_overrides`, `open_split_intent`, and `split_session` validate before mutation; stored split request has an explicit field list; handoffs are taken before append | Validate at recording and final execution, pass the new fields, and resolve the final cwd before taking any handoff |
| Split page/store | Visible and background splits forward the extra request object; store result uses `SPLIT_RESULT_FIELDS`; POST/claim/pending/wait bounds are 20/20/15/40 s | Exercise both page paths, extend result projection, and prove the deadline chain rather than inserting a long probe blindly |
| `sessions/manager.py` / `web/runtime_state.py` | Session construction/metadata and `_SESSION_SNAPSHOT_FIELDS` have explicit field handling; `_restore_group_request` replays snapshot shape | Add and validate `agent_model` through construction, mutation, capture, read validation and restore |
| `web/saved_sessions.py` | Named preset normalizers/merge explicitly build reusable launch shape | Omit model/declaration at every preset boundary; never embed the model in `initial_command` |
| `web/terminal_io.py` / `web/session_modes.py` | Runtime detection/retirement and mode switches change agent identity independently of launch | Remove stale model metadata on retirement, plain/non-agent transitions and unrelated hand-typed agent promotion |
| MCP contract | Prohibits tool-supplied launch bytes; pane-transition wording still contains an older blanket reselection no-op rule | Document the narrow validated-ID exception and align reselection wording with current behavioral tests |

## Stage 0 — prove discovery and budgets

Complete this before exposing new mutation fields. Produce a short evidence
record beside this plan with CLI versions, transport, effective cwd/family,
metadata source, exact-ID mapping, provider/config handling, side effects,
account-access limits, latency and cleanup outcome. Keep it secret-free.

1. Inspect supported metadata mechanisms for the installed versions. Use
   disposable fixture homes/projects first to detect writes, hooks, plugin
   loading, dependency installs, background processes and automatic refresh.
   Do not send an inference prompt or make a paid model request.
2. Prove Claude has a metadata-only source of exact launch IDs under its
   effective backend and managed model restrictions. Alias-only responses or
   SDK initialization that triggers project hooks do not satisfy the design.
   A new SDK dependency or undocumented protocol requires an explicit design
   tradeoff record before adopting it; do not hide it behind a static catalog.
3. Prove Codex stdio initialization and bounded `model/list` pagination without
   starting a thread/turn. Map the `model` field, confirm effective provider/cwd
   and CLI-equivalent client behavior, and select an explicit hidden-model
   policy. Check catalog results without claiming entitlement.
4. Prove OpenCode configured-provider enumeration, optional safe metadata and
   exact `provider/model` IDs under merged configuration and provider policies.
   Include the launch-time config overlay; never use `models --refresh`.
5. Prove local PowerShell/cmd/POSIX, WSL distribution and SSH execution preserve
   the launch environment. Reuse centralized SSH trust and existing resource
   ownership. A transport/version with no safe metadata path returns an
   actionable refusal; it never uses the GridVibe host's catalog as a substitute.
6. Establish bounded admission, four active probes, output limits, cursor-loop
   detection and process-tree teardown. Cover remote channels separately:
   closing an SSH channel alone is not proof the remote process ended.

Initial budget targets are 15 s per discovery probe, 40 s complete discovery or
model-specific launch/relaunch preflight, and 50 s HTTP requests for those
operations. The complete server budget includes queueing, binary/cwd checks
and cleanup; it is not 40 s plus those checks. Leave default-only calls on
their current deadlines. For split recording and final validation, target a
12 s complete budget each, including cleanup. The final page request must also
leave time for geometry persistence and intent reporting within the claim.

Pin these relationships with controlled timing fixtures. If the split targets
cannot be met, settle a revised page POST/claim/wait/CLI tool timeout chain
before Stage 5; update the corresponding constants and tests together. A late
append must never coexist with an expired intent reporting that nothing
happened. Exhausted admission or deadline refuses before mutation.

**Exit:** all three CLI adapters have a demonstrated safe metadata source and
supported-version/transport matrix, or the missing proof is recorded as an
implementation blocker. Unproven adapters stay unavailable for explicit model
selection; do not call the complete three-CLI feature finished.

## Stage 1 — prepare domain boundaries

Apply the architecture extraction rule already triggered in
`web/workspaces.py`: move the launch preparation/orchestration needed here into
a focused backend owner, retaining public wrappers where callers/tests depend
on them. Choose the cut after tracing its imports and effects. Preserve
late-resolved effects and patchability, avoid cycles, and keep the HTTP route
thin. Do not mix behavioral model changes into the extraction.

Introduce one immutable, private destination-context shape reusable by model
discovery and mutation preflight. Capture runtime settings once per operation.
Context includes effective machine/account, shell/distribution, directory and
launch-relevant environment/config overlay, plus captured pane identity when
applicable. Credentials stay backend-only and out of responses, keys printed
in logs and durable state.

**Exit:** `tests.test_multi_workspace` passes unchanged across the extraction;
existing launch/restore/MCP and route-boundary checks pass. This stage adds no
public model capability or unused route.

## Stage 2 — add the shared model service

Create `web/agent_models.py` as the single owner of the three-key capability,
adapter dispatch, bounded execution, catalog normalization, safe model-ID
validation, literal filtering and exact candidate validation. It must not
import the sidecar or Flask routes, or depend on `web/agents.py` in a cycle.
Pass target/config facts from the owning services.

Use operation-scoped deduplication keyed by complete effective context.
Maintain a complete internal catalog separately from the capped response.
Reject duplicate ambiguous launch IDs, malformed metadata, oversized output,
repeated cursors, partial enumeration and unsafe argument syntax. No global
catalog cache. Start with 1 MiB captured output, 2,000 catalog rows, 100 returned
matches and a 256-character safe model token; confirm the alphabet against
provider/deployment fixtures before freezing it.

Return typed `ok`, `unsupported`, `unavailable` and `check_failed` results.
Empty successful matches are distinct from failed discovery. Known denied
models are excluded; unknown entitlement is `account_access: null`.
Project bounded labels/messages and a secret-free target summary. Never
pass through subprocess output, credentials or raw config, including on errors.

**Exit:** new `tests.test_agent_models` uses fixture catalogs and fake bounded
process/transport adapters to cover success, failure, cleanup, concurrency,
context separation, limits, filtering and secret projection without credentials.

## Stage 3 — publish discovery through MCP

Add `model_supported` to backend agent-type rows and `AGENT_TYPE_FIELDS`, and
one thin `GET /api/agent-models` route backed by the new service. Add
`GridVibeClient.agent_models`, explicit candidate/result field lists, a 50 s
request budget and one `list_agent_models` specification/dispatch branch.

Discovery takes required `agent` and `operation` (`launch`, `split`, `relaunch`),
optional bounded `query`, and operation-specific context fields:

| Operation | Context rules |
| --- | --- |
| `launch` | Origin comes from the MCP caller identity; optional absolute `directory` and allowed local `shell`; no target `pane_id` |
| `split` | Required source `pane_id`; optional absolute `directory`; inherit source shell, refuse `shell` |
| `relaunch` | Required target `pane_id`; optional allowed local `shell`; refuse `directory`, since the mutation cannot state it |

Resolve exactly as the intended mutation would. If an omitted directory cannot
be faithfully resolved, return an actionable failed check. Listing models
grants no permission to relaunch that pane. Put the check/show/ask/wait rule in
tool descriptions and the MCP reference. A displayed candidate selection
confirms one stated context and batch; Other requires another lookup.

**Exit:** strict argument rejection, three capability keys, nested projections,
target restrictions, longer read deadline and shared stdio/HTTP dispatch are
covered in `test_agent_types`, `test_mcp_client`, `test_mcp_tools` and
`test_mcp_remote`. Existing agent types remain launchable without model fields.

## Stage 4 — wire whole-batch launch and gated relaunch

Add optional `model` / `model_confirmed` to agent rows in `launch_panes` and
to `set_pane_agent`, with one common paired-field validator. Reject blank IDs,
unpaired declarations, false/nonboolean confirmation, wrong CLI/kind and
unsafe syntax before any request can mutate state. Backend repeats validation;
sidecar checks alone are insufficient.

Normalize public `model` to internal `agent_model`; keep declaration transient.
For launch, preflight every requested candidate after final destination/cwd
normalization but before workspace/group/pane/handoff creation. One bad pane
refuses the whole batch; identical contexts probe once. Bind origin/context
identity across slow checks, and recheck in-memory ownership before publish.

For relaunch, run existing gates first, validate the resolved successor model
outside shared locks, include it in `_RELAUNCH_BOUND_FIELDS`, and use the
existing commit guard before metadata/teardown. Carry a stored selection for
an unstated model on the same CLI; changing CLI or returning to a shell clears
it. A same-agent relaunch into a different machine/family/cwd requires fresh
validation and conversation confirmation of the changed model context; do not
silently carry a target-specific choice into another environment.

Compose one quoted model argument in `_compose_agent_startup_command` before
MCP fragments, retaining conversation/title/auto-mode/task prompt composition.
Keep `initial_command` the registered binary; reject new model selection for
custom commands. An identical explicit reselection still restarts and starts a
fresh conversation, exactly as current relaunch behavior requires. Add the
requested model to any existing relaunch refusal/confirmation summary.

Extend mutation and pane read projections with `agent_model` as requested
startup state. Introduce sufficient HTTP deadlines only on model-taking
operations. Preserve task handoff and pane override behavior unchanged.

**Exit:** tests prove no partial batch mutation, no fallback, safe arguments
across supported shells, immutable settings/context, deduplication, gate/identity
races, carry/clear rules, model-only restart, identical reselection restart,
fresh conversation behavior and requested-model result projection.

## Stage 5 — finish split validation and results

Add the paired model inputs to `split_pane` and `build_split_pane_request`.
Refuse models for unstated/non-agent kinds. At `open_split_intent`, resolve the
new pane's actual cwd and validate before storing an intent or creating a task
handoff. Carry transient fields through the stored `split_request` explicitly.

At `split_session`, re-resolve final cwd and catalog, verify captured source
and caller identity, and refuse changes before `agent_handoffs.take` or
`append_session_to_group`. Keep checks outside shared locks; the final
in-memory guard must cover the append. Omitted model means a fresh default
agent/plain terminal, with no clone inheritance. Backend is the policy owner;
echoed page fields are not independent approval.

Trace `splitTerminalPane`, background split posting and intent-report building.
Their generic forwarding may already carry the request fields; change only
plumbing that actually drops them. Extend `SPLIT_RESULT_FIELDS` and the page's
report to include the backend-requested model. Keep geometry, tab holds,
revisioned presentation, focus and visible-window requirements intact.
Enforce the Stage 0 complete deadlines and honest unknown outcomes.

**Exit:** visible/background splits retain the model and report it; failed or
late discovery adds no pane/handoff, changed/closed identities refuse, hidden
windows keep existing behavior, and timing tests rule out false untouched
reports. Cover `test_api`, `test_background_split`, `test_mcp_launch`,
`test_mcp_tools`, `test_mcp_remote` and existing geometry/intent regressions.

## Stage 6 — persist requested state and complete all clearing paths

Add `agent_model` to `TerminalSession`, `to_dict`, construction and metadata
handling; accept only normalized values valid for the pane's CLI and binary.
Wire `_SESSION_SNAPSHOT_FIELDS`, capture, read validation,
`_prepare_launch_sessions` and `_restore_group_request`. Missing/`null` older
fields restore as no explicit selection. Invalid stored launch shape refuses
the group instead of silently dropping or substituting a model.

Reconnect/restore composes the captured requested model without a new user
question or billable access check, keeping the existing restore error behavior.
Only restore resumes conversations; model state remains useful when the
experimental conversation-restore setting is off. Never persist declarations,
catalogs, output, credentials or entitlement details.

Strip model inputs at launcher/header/preset boundaries. Same-CLI header
reconnect/relaunch may preserve an existing stored selection, but cannot accept
a new one from payload. Clear it in mode transitions, plain splits, agent
change, `_clear_agent_launch_identity`, runtime retirement and promotion of a
different/custom hand-typed launch. Detection must not infer a confirmed
model from process titles or arguments.

**Exit:** `test_runtime_state`, `test_session_persistence_contract`,
`test_multi_workspace`, `test_saved_sessions`, `test_session_shell`,
`test_agent_runtime_exit`, `test_agent_conversation_restore` and
`test_agent_conversations` cover old/new/invalid snapshots, preset omission,
reconnect, identity clearing and refusal preservation.

## Stage 7 — validate and document the shipped behavior

Update only the relevant maintained references:

- `gridvibe_mcp/README.md`: tool surface, conversational confirmation,
  operation-specific context, requested-model reporting, discovery failures
  and capability/account-access distinction.
- `docs/engineering_contracts.md`: validated model-argument exception, pane
  carry/clear and explicit reselection rules, whole-batch preflight, ownership
  and actual enforced bounds. Replace superseded wording rather than appending
  review history.
- `docs/session_state_guideline.md`: model field in runtime snapshots only,
  migration/default rules and clearing behavior.
- `CHANGELOG.md`: one correctly shaped Unreleased feature note for the completed
  behavior. General README receives at most a concise user-facing feature
  bullet/link if needed; it does not duplicate the MCP reference.

Run the behavioral suites affected by each stage and directly adjacent
regressions, then the repository-wide linter. Because final wiring crosses
launch, splits, transitions and durable state, run the complete validation
once at the final integration checkpoint under the cross-cutting-change rule:

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe tests/run_tests.py
```

Use focused `-m unittest tests.test_module` runs while implementing. Node is
required for frontend behavior tests; the optional MCP SDK must remain optional.
Do not use paid inference or require credentials in automated tests.

Manually exercise exact match, ambiguous provider choices, Other, cancellation
and silence; one choice may confirm a five-pane identical batch. Verify tool
descriptions do not conflate model choice with pane replacement approval.
Check failure results contain no raw config, token, credential or process
output, and that all CLIs report process launch rather than proven inference.

**Completion:** all three demonstrated adapters, discovery and mutation tools,
both transports, both split paths, requested-state persistence, clearing rules,
deadline/cleanup checks and maintained docs pass. An unresolved adapter or
transport is reported explicitly; do not silently call partial coverage complete.
No commit, dependency installation or history change is authorized by this
planning task.

## Known limits to retain in the implementation

`model_confirmed` is a caller declaration. It cannot authenticate user approval,
prove an earlier lookup, bind an unchanged ID to an earlier provider endpoint,
or lock external configuration through startup. The server validates current
catalog membership and mutation identity; the caller follows the conversation
and context rule. A stronger receipt/approval system is separate scope.

GridVibe guarantees its requested startup argument and its own refusal to
substitute. CLI-native fallbacks, subagent model choices, managed policies and
later in-session switches can change actual inference behavior. Entitlement
remains unknown unless trustworthy metadata proves it; a successful process
start is not a successful model request.

External CLI details must be rechecked at Stage 0 using the official sources
copied from the reviewed design into the
[Stage 0 evidence record](mcp_agent_model_selection_stage0_evidence.md#9-sources-to-recheck).
The old OpenCode `EEXIST` observation is historical and was not reproduced by
this review. This plan authorizes no repair of that environment.
