# GridVibe MCP improvements: staged implementation plan

## Scope and baseline

This plan covers the eight requests in the [MCP notes](r&d/MCP/mcp_notes.txt). The maintained behavior references are the [MCP sidecar reference](../gridvibe_mcp/README.md) and [engineering contracts](engineering_contracts.md#agent-tools-mcp). The notes are input to this plan, not a replacement for those contracts.

Today, `open_window` accepts a `group_id`, but raising an existing native workspace window does not activate that group. `launch_panes`, `split_pane`, new-agent task handoff, and result collection already exist. Closing, moving, and divider resizing have UI or HTTP paths but no MCP tools. `list_saved_layouts` reads presets but cannot save one. The agent registry contains more CLI types than the three that support MCP task handoff.

**Interpretation of “save ... on this directory”:** save a named GridVibe launcher preset whose panes start in the caller's directory. It does not write a layout file into that directory. A file export can be scoped separately if needed.

| Stage | Related capabilities | Notes |
| --- | --- | --- |
| 1. Navigate live sessions | Activate a group or pane; move a group between workspaces | 2, 5 |
| 2. Create agents at the intended path | Honor a stated split directory; discover and launch installed registry agents | 7, 8 |
| 3. Control geometry | Resize dividers and compose equal panes | 3 |
| 4. Save a reusable layout | Persist live geometry and pane types as a named preset | 6 |
| 5. End resources | Close a pane, group, or workspace after work completes | 4 |
| 6. Conditional backlog | Handoff to an already running agent | 1; deferred until a concrete use case |

Stages 1–5 are the proposed delivery order. Stage 6 has a design gate and is not part of the initial delivery.

## Rules shared by every stage

- Add each tool once to `gridvibe_mcp/server.py` and `gridvibe_mcp/client.py` so stdio and tunneled HTTP use the same dispatch. Keep the sidecar independent of `web/`, and keep its SDK optional.
- Resolve “this pane”, “here”, and “the pane below” from live `whoami`/`list_panes` data. Resolve a named session to one unambiguous live group before a write. Recheck identity and ownership at the server transaction; a group or pane can move or close between the read and write.
- Use explicit response fields and `scrub()` for all tool results. Never return credentials, handoff text, tokens, or internal file paths. Refusals must say whether anything changed and what the caller can do next.
- Keep user-supplied handoff text separate from user authorization. An agent may apply an `override` only from the person's instruction in its own conversation or a clear answer to a structured confirmation, following the existing pane gate contract.
- For each stage, add focused behavioral tests of success, refusal without partial mutation, stale identity, and local/SSH transport where applicable. Update the MCP reference and relevant engineering contract when behavior changes. Run focused tests and the repository linter after implementation.

## Stage 1 — Navigate live sessions

### 1A. Activate the requested group or pane

**Work.** Make `open_window(workspace_id, group_id)` actually switch to the named group when the workspace window is already open. Add a `focus_pane(session_id)` tool for the follow-on request to show and focus a newly created pane; it derives the current group and workspace from that pane. Reuse the frontend's existing group switch and dashboard pane-focus behavior. A page must acknowledge the active group and, for `focus_pane`, the target pane after the switch settles. If unsaved editor work or an active file operation blocks switching, return `blocked` with that reason. If no page can perform the action, return `no_window_available`; merely raising a native window is not proof that the target tab changed. In browser mode, opening a new URL may be reported as opened, but do not claim verified pane focus until a page acknowledges it.

**Implementation touchpoints.** `gridvibe_mcp/windows.py`, `web/window_intents.py`, `web/static/js/window-intent.js`, the existing focus logic in `web/static/js/terminals.js`, and the native bridge in `web/webview_launcher.py`. Check that `group_id` belongs to `workspace_id` before recording an intent, and that a stale pane/group is refused.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Bring the `cIMS_workspace_chss` session to the foreground.” | The workspace window is raised **and that session's tab is active**. The tool returns the resolved workspace/group IDs and confirmed active group. |
| “Split the largest pane in `session_a` vertically, start a Codex agent in the new pane, then switch view there.” | The split creates an agent pane, its launch starts, the containing group becomes active, and the new pane is visibly selected/focused. A launch or focus failure is reported at its own step; no success is claimed for an unseen pane. |
| Repeat the first prompt while that group has an unsaved editor change that blocks switching. | The tool reports `blocked` and leaves the current tab active. |

### 1B. Move a live group between workspaces

**Work.** Add `move_group(group_id, target_workspace_id)` with an optional new-workspace destination, backed by the existing `move_group_to_workspace` transaction. Resolve a session name only if unique; otherwise present candidate groups and require an exact target. Moving the caller's own group is allowed; a different group needs the existing lineage/explicit-user-override pattern with a target-specific confirmation. Return source, destination, `moved`, and the group's current workspace. Keep pane IDs, processes, SSH connections, handoffs, and result assignments intact. After a successful move, use 1A to show the destination group when the prompt asks to switch view. A focus failure must not be described as a move failure.

**Implementation touchpoints.** `web/workspaces.py` and its route in `web/api.py`, the MCP client/dispatcher, and the group list refresh in both affected windows. Reuse live group ownership as the workspace answer; do not trust the pane's spawn-time workspace ID.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Move session `test_session` to the `gridvibe_2` workspace and show it.” | One matching group moves to `gridvibe_2`; its pane IDs and running agents remain the same, the source no longer lists it, and the destination window shows its tab. |
| “Move `test_session` to `gridvibe_2`” when two groups share that label. | The agent presents the two live candidates and changes neither group until one is identified. |
| Move a group to its current workspace. | The tool returns `moved: false` and the current workspace; no duplicate group or window appears. |

## Stage 2 — Create agents at the intended path

### 2A. Honor the stated directory when splitting or re-rooting

**Work.** Make a stated `directory` on `split_pane` override the source pane's observed cwd for the new pane. Validate it on the machine where the new pane will run, including SSH/WSL path handling. Set the new agent's working directory before launch; for a file explorer, set its root deliberately. Fix the related open re-root issue in `set_pane_mode`: a stated path wins over a probed cwd, a directory-only terminal change either relaunches at that path or is explicitly refused, and a derived explorer root does not bar an explicit move to its parent. Preserve containment for browsing inside a live explorer. Refusals name the failed path rule and leave the original pane intact.

**Implementation touchpoints.** `web/api.py` split overrides, `web/session_modes.py`, `web/explorer.py`, `gridvibe_mcp/server.py` directory descriptions, and [pane transitions](engineering_contracts.md#pane-transitions). Track the existing re-root finding as `ISSUE-2026-057` in `docs/testing_issues.md`; close it only after the behavior is verified.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Split the terminal you are in top to bottom, set the new pane's working directory to the parent of this directory, and start Claude there.” | `whoami` identifies the caller's machine and directory. The new Claude pane starts in the parent directory, while the original pane's cwd and process stay unchanged. |
| “Make the Files pane beside me a PowerShell terminal one directory above its current derived root.” | After any required pane-gate confirmation, the pane ends as a PowerShell terminal at the stated parent path. A later `list_panes` shows that path; the agent reports the mode and shell steps it used. |
| Repeat either request with a missing directory or a path on the wrong machine. | The operation is refused before a new pane or relaunch, with the target machine/path reason. |

### 2B. Discover and launch every usable registry agent

**Work.** Add `list_agent_types` as a read tool sourced from `agent_registry.json`, with key, display name, launch availability on the caller's actual machine, and separate `mcp_supported` and `task_supported` flags. Reuse the existing agent preflight rules. Ensure `launch_panes` and `split_pane` accept every **installed and supported** registry CLI, rather than implying all can receive MCP or a task. Validate a whole multi-pane request before creating its group, including capacity, shell compatibility, and requested agent availability. For an eight-cell request, use explicit 2×4 `workspace_layout`; if fewer than eight agents are available, leave remaining cells as plain terminals and report which agents were unavailable. If more than eight qualify, refuse or ask which eight rather than silently dropping one. Do not silently turn an unavailable agent into a shell.

**Implementation touchpoints.** `web/agents.py` preflight/registry, `web/workspaces.py` launch validation, and the MCP tool schema/client. The three CLIs with verified MCP opening prompts may receive a `task`; the others may launch as agents without an MCP handoff.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Split this terminal into a 2 by 4 grid of PowerShell terminals and start every agent type available to you, one type per terminal.” | On a Windows local pane with capacity for eight, the agent first reads the registry/availability, then creates one 2×4 group. Each available type occupies a distinct PowerShell pane; unused cells are plain terminals. The result names unavailable types. |
| “Start one pane for each available agent type and give each the same task.” | The whole request is refused before mutation because some registry agents cannot receive an MCP task. The result names those types and offers a supported-only launch for the user to choose; no pane is falsely reported as tasked. |
| Run the 2×4 PowerShell prompt from an SSH or WSL pane. | The tool refuses the incompatible local shell request, rather than starting panes on the GridVibe host or silently changing shell family. |

## Stage 3 — Control geometry

### 3A. Resize a divider

**Work.** Add a page-backed `resize_divider` tool taking a group, axis, divider identifier, and desired normalized position or adjacent track weights. The page owns measurement and minimum-size validation, using the same live grid calculations as a pointer drag. Extend the bounded, single-claim intent mechanism used for splits; the server must not guess pixel geometry. Return the applied track weights and resulting pane rectangles only after the page persists and acknowledges them. Reject stale layouts, out-of-range weights, narrow viewports, and absent windows without changing geometry. `list_panes` should reflect the settled layout.

**Composition.** To make three equal side-by-side panes from one pane, split it twice and then place the two vertical dividers at one-third and two-thirds. If the page cannot meet minimum widths, explain that constraint instead of calling a 1/2–1/4–1/4 arrangement “equal”. Keep a partial split visibly reported if a later step fails; do not claim an atomic three-pane operation.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Split the terminal below you into three equal vertical panes, then start a Claude agent in each.” | The terminal below is resolved by `list_panes` neighbors. Three side-by-side panes appear in its former region, each approximately one-third of that region, and each starts Claude. Other dividers and panes stay in place. |
| “Move the divider between the two right panes so the middle pane is twice as wide as the right pane.” | Only the selected divider moves. The returned weights and `list_panes` rectangles show the requested ratio within rounding and minimum-size limits. |
| Ask for an impossible width or close the native window before the resize. | The tool returns a specific refusal or `no_window_available`, and reports no applied geometry. |

## Stage 4 — Save a reusable layout

### 4A. Save the current group as a named preset

**Work.** Add `save_group_layout(group_id, name, root_directory?)`. Snapshot the live group's pane order, kinds, shell families, per-pane start directories, layout rectangles/weights, and safe agent selections. With `root_directory`, use the stated directory as the saved start directory for each compatible terminal, agent, and explorer pane; browser URLs stay as they are. Validate the directory on the group's machine before writing. Without it, preserve each pane's current start directory. Reuse the page's presentation flush before an explicit save and the existing saved-session normalization/store; do not synthesize geometry from stale `list_panes` data. Save a reusable launch shape, not active processes, handoff tasks, runtime agent conversations, or decrypted credentials in the tool result. Return the preset ID/name and persisted shape only after the durable write succeeds. A write failure leaves the live group running and reports failure. `list_saved_layouts` must show the new preset, and importing it should reproduce its pane types and geometry.

**Implementation touchpoints.** `web/static/js/session-persistence.js`, `web/lifecycle.py`, `web/saved_sessions.py`, `web/state_files.py`, the saved-session API, and the MCP client/dispatcher. Define the server behavior when no page can flush presentation: either use a known-current revision or refuse with a clear “open the group and retry” message; never claim an unverified layout was saved.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Save the session layout and pane types of the session you are in as `gv_session_2x3`, rooted at this directory.” | The agent resolves the live group and directory, saves a GridVibe preset named `gv_session_2x3`, and `list_saved_layouts` shows its 2×3 geometry and pane kinds. Reopening the preset starts panes at that directory without reviving old processes. |
| Resize one divider, immediately save, then reopen the preset. | The reopened layout uses the latest settled divider position, not an earlier snapshot. |
| Force a preset persistence failure. | The tool reports failure, the old preset remains unchanged, and the live session stays open. |

## Stage 5 — End resources after work completes

### 5A. Close a pane, group, or workspace

**Work.** Add separate `close_pane`, `close_group`, and `close_workspace` tools. Preflight the complete target set before any close, using the shared pane gates and a structured target-specific confirmation for a waivable lineage or running-agent refusal. Never allow a caller to close its own pane, a group containing that pane, or a workspace containing that pane. A group/workspace close must not pass a gate on one pane and then partly close before discovering a refusal on another. Require a fresh server-side ownership check at execution, and report the exact closed IDs if a runtime failure interrupts a close after it starts. `close_workspace` means the existing “close live workspace” behavior, preserving saved snapshots; do not expose `forget` in this stage. Closing an agent that still owes a result ends its assignment with a reason, and the caller can collect reports before closing workers.

**Implementation touchpoints.** `web/pane_gates.py`, `web/workspaces.py`, close routes in `web/api.py`, `web/agent_handoffs.py` and `web/agent_results.py`, then MCP client/dispatcher. No arbitrary terminal input tool is needed.

**Manual verification.**

| Prompt | Expected result |
| --- | --- |
| “Analyze this issue, hand implementation tasks to three fresh Codex agents split from this terminal, wait for their reports, review the staged changes, then close those three Codex panes.” | Three new panes receive tasks. `wait_for_results` returns their reports (or explicit ended/blocked states). The close tools remove exactly those three worker panes after collection; the caller pane and unrelated groups remain open. |
| “Close the session group you are in.” | The tool refuses the self-containing group without mutation. |
| “Close workspace `gridvibe_2`” when it contains a running agent outside the caller's lineage. | A structured confirmation names the workspace and affected panes. Without a clear user authorization, nothing closes; with one, the live workspace closes and any saved preset remains restorable. |

## Stage 6 — Conditional backlog: handoff to a running agent

The notes explicitly defer this until a real use case exists. The current handoff is for a **new** agent started by `launch_panes`, `split_pane`, or `set_pane_agent`; `set_pane_agent` ends the old process and is not a running-agent handoff.

**Design gate.** First record a use case that requires an existing agent rather than a fresh worker. Then add an opt-in inbox/offer protocol bound to the receiver's live pane identity and MCP capability. The receiver must acknowledge and accept the offer through a tool; nothing is typed into its terminal and no task is misrepresented as the person's own instruction. Bound offer size, lifetime, machine scope, and visibility; let the sender observe accepted, declined, expired, or unavailable. When no eligible running receiver exists, create a fresh worker with the existing `task` handoff if the user asked for a fallback. This requires a receiver notification or polling design before implementation, plus an explicit answer for agents that cannot receive MCP tools.

**Manual verification after the design gate is met.**

| Prompt | Expected result |
| --- | --- |
| “Analyze this bug and hand the fix outline to a running Codex agent in this session if one can receive it; otherwise split a new Codex worker from this pane.” | One eligible running receiver gets an offer and explicitly accepts it, or a new worker is created and receives the existing handoff. The sender sees which path happened and never reports delivery solely because a pane was found. |
| Have the running receiver decline or close before accepting. | The offer is declined or ended, no terminal input is injected, and the sender gets a truthful state with an option to create a fresh worker. |

## Completion gate

For each shipped stage, its manual prompts pass in native mode and relevant browser/SSH cases; the new tools appear in `make mcp-tools`; focused backend and frontend tests cover the stated refusals; the MCP reference and engineering contracts match the shipped behavior; and the repository linter passes. Stage 6 remains marked deferred until its use case and receiver protocol are agreed.
