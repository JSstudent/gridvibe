# GridVibe Testing Issues
Last updated: 2026-09-20

## Open Issues

### Issue ID: ISSUE-2026-058
- Title: Codex thread names can disappear from agent dashboards
- Priority: Medium
- Status: Open
- Area: `web/agent_activity.py`, `web/terminal_io.py`, `web/agents.py`, `web/static/js/agent-identity.js`
- Assignee: Unassigned
- Tags: `agent`, `dashboard`, `terminal`, `session`, `codex`, `tests`
- Reported: 2026-09-20

Description:
The agent dashboards can label a named Codex conversation as `New session · <location>` even while Codex's own resume picker knows and displays that conversation's user-facing name. GridVibe and Codex are answering from two independent channels: GridVibe reads only the latest OSC terminal title, while Codex's picker and its `To continue this session...` exit guidance read persisted thread metadata. A named thread may still publish a bare UUID (or no usable OSC title), and GridVibe deliberately rejects a bare UUID as an opaque identifier. The result looks intermittent because another unnamed Codex thread may publish useful prose over OSC and display correctly.

Observed locally with Codex CLI 0.155.x. One affected persisted thread carried `thread_name: "Review OCR delegation"` in Codex's `session_index.jsonl`, and Codex's exit guidance said to run `codex resume` and select that named entry. Its GridVibe dashboard row did not show the name. A comparison thread had no session-index name, produced direct `codex resume <uuid>` guidance, and nevertheless displayed a useful dashboard title while running. The exact OSC value for the ended affected process is no longer available; that it published a bare UUID or nothing usable is an inference from the displayed fallback, not a captured fact. Independently, the live dashboard API was observed publishing bare Codex UUIDs for several panes and prose for another, confirming that both OSC forms currently reach GridVibe.

Steps to reproduce:
1. With Codex CLI 0.155.x, launch a built-in Codex agent pane through GridVibe, create or resume a conversation that Codex's resume picker lists with a user-facing name, and let the pane reach its prompt.
2. Confirm the same UUID has a non-empty thread name in Codex's persisted thread listing or through Codex App Server `thread/read`.
3. Open either GridVibe agent dashboard while the pane's OSC `thread-title` is still a bare UUID, or reproduce the input deterministically by passing a whole UUID as the Codex pane's `activity.title` to `paneChatLine()`.
4. Observe that the dashboard says `New session · <location>` rather than the persisted Codex thread name. For comparison, an OSC title containing prose is shown immediately.

Expected behavior:
A Codex conversation that has a user-facing thread name is shown by that name in both agent dashboards. A genuinely unnamed thread keeps the existing `New session · <location>` fallback, and an opaque UUID is never presented as though it were a name.

Actual behavior / logs:
The stream observer accepts OSC 0/1/2, normalizes each title, and `describe_agent_activity()` publishes `tab_title or title` as `activity.title` (`web/agent_activity.py:195-222`, `:322-368`). No Codex thread metadata is consulted. `agentChatTitle()` then rejects whole UUIDs through `isOpaqueIdentifierTitle()` (`web/static/js/agent-identity.js:160-164`, `:303-324`), after which `paneChatLine()` emits the new-session fallback (`:357-374`). Both the dialog and docked sidebar use this same answer, so this is not a disagreement between dashboard renderers.

The exact strings demonstrate where the loss occurs:

```text
activity.title = "<uuid>"
paneChatLine()  = "New session · repo"

activity.title = "Review OCR delegation (<uuid>)"
paneChatLine()  = "Review OCR delegation (<uuid>)"
```

The second form is accepted by the current anchored UUID predicate. Therefore the missing name is not caused by GridVibe stripping a parenthesized UUID; that named string never reached the frontend as `activity.title`.

GridVibe adds `tui.terminal_title=['thread-title']` only when the persisted built-in command is exactly `codex` (`web/agents.py:453-492`). A modified command such as `codex resume --last` is deliberately returned verbatim, so GridVibe-managed and custom resume paths can also differ in whether the conversation-title override is present. The plain `To continue this session...` line is ordinary terminal output, not an OSC event, and has no effect on the dashboard.

### Proposed solution:
Treat a whole Codex UUID observed over OSC as conversation identity to resolve, not as either a display title or proof that the thread is unnamed.

- Add a bounded Codex conversation-name resolver, preferably in the planned `web/agent_conversations.py` owner from `docs/agent_conversation_restore_implementation_plan.md`. Use Codex's supported App Server `thread/read` metadata when available; it returns a stored thread's user-facing `name` without resuming it. Do not parse localized continuation prose, and do not make direct `session_index.jsonl` scraping the primary contract.
- Resolve only when the current connection observes a new whole-title Codex UUID, never during each dashboard poll. Cache positive results by provider/environment/thread UUID and use a short, bounded negative retry schedule because Codex may assign a name after the UUID is first announced.
- Run the lookup in the pane's own execution environment: locally for cmd/PowerShell, inside the selected WSL distribution, and over a bounded secondary channel for SSH. Failure or an unsupported Codex version leaves the existing fallback intact and must not delay terminal output or dashboard reads.
- Keep the raw OSC title, resolved conversation name, and pane title as separate facts. Publish only the resolved human-readable name needed by the dashboard, not the UUID. `agentChatTitle()` should prefer a resolved name, then usable OSC prose; the manually typed pane title and `New session · <location>` remain the later fallbacks.
- Before applying an asynchronous answer, verify that `ssh_connections[session_id]` is still the same connection and that its observed Codex UUID is unchanged. Clear the resolved value on relaunch, provider change, agent exit, or title-floor invalidation so a retired process cannot name its replacement.
- Coordinate the work with the existing Codex OSC identity-capture and restore plan (`docs/agent_conversation_restore_implementation_plan.md:202-219`) so the UUID is recognized once. For GridVibe-composed Codex resume commands, apply the existing terminal-title override after composing the validated resume form; arbitrary custom commands should remain verbatim.

Focused tests: extend `tests/test_agent_activity.py` for UUID observation, bounded resolution, cache/retry behavior, name arrival after an initial miss, and stale-connection rejection; extend `tests/test_agent_identity.py` for resolved-name precedence while preserving UUID rejection and the unnamed fallback; exercise local, WSL and SSH resolver adapters with injected processes/channels and strict time/output ceilings; and add command-composition cases proving a GridVibe-managed Codex resume receives the title override exactly once while an arbitrary custom command remains unchanged. Dashboard-dialog and sidebar tests should assert both surfaces paint the same resolved name and that an unchanged poll still skips repainting.

### Issue ID: ISSUE-2026-057
- Title: An agent cannot re-root a pane above the explorer root the pane derived for itself
- Priority: Medium
- Status: Open
- Area: `web/session_modes.py`, `web/explorer.py`, `gridvibe_mcp/server.py`
- Assignee: Unassigned
- Tags: `mcp`, `explorer`, `terminal`, `cwd`, `transitions`, `tests`
- Reported: 2026-09-17

Description:
`set_pane_mode` publishes a `directory` field, so an agent asked to "make the Files pane a PowerShell terminal one level up" has a tool that looks like it can do exactly that. It cannot. There is no single call that moves a pane to a directory outside the explorer root the pane last derived for itself, and every way the attempt fails is either a refusal naming a rule nobody set or a success that changed nothing. Turning one explorer pane into a PowerShell terminal one directory above its repo took four calls and a reading of `web/session_modes.py` to discover the ordering that works.

Observed on a live workspace (`saved-session-session-20260821-214012-57a685`): pane `b832d655`, an explorer rooted at `...\Tutorial_apps\DiveDeep` with `explorer_root_configured: false`, asked by the agent pane beside it to become a terminal at `...\Tutorial_apps`.

Steps to reproduce:
1. Open a workspace with a Files pane rooted inside a Git repository and an agent pane in the same group, and let the Files pane's own shell history report the repo as its cwd.
2. From the agent pane call `set_pane_mode(session_id=<files pane>, mode="terminal", directory=<the repo's parent>, override=true)`.
   → `400 Explorer path must stay inside the configured root`.
3. Call it again with no `directory`.
   → The pane becomes a terminal, but at the repo, and its host is the stored shell family (here `cmd`, because that explorer carried `use_powershell: false`), so a second `set_pane_agent(shell="powershell", agent="")` is needed to get the shell that was asked for.
4. Repeat step 2 now that the pane is a terminal.
   → `200`, payload returned with `directory` still at the repo. Nothing happened and nothing said so.
5. Call `set_pane_mode(mode="explorer", directory=<the repo's parent>, override=true)` while the pane still has a live shell.
   → `200`, and `directory` comes back as the repo, not the parent.
6. Call the same thing once the pane has no live shell behind it (i.e. it is already an explorer).
   → Now the parent is taken, and only then does step 2 succeed.

Expected behavior:
A stated `directory` is either honored or refused with a sentence naming the rule that actually refused. An agent permitted to change a pane at all can re-point that pane at any directory that exists on the pane's own machine, including one above the root a previous mode derived — root containment is the boundary a *live Files pane browses inside*, not a limit on where a pane may be re-rooted. A call that changes nothing does not answer `200` with a pane payload.

Actual behavior / logs:
Four mechanisms, all confirmed by inspection.

**The clamp reaches a derived root.** The explorer/browser → terminal branch resolves the hand-off directory through `_resolve_pane_terminal_directory()` (`web/session_modes.py:313` → `web/explorer.py:4069`), which calls `_resolve_explorer_candidate_path()` (`web/explorer.py:897`). Containment there is tested against `_explorer_root_directory()` (`web/explorer.py:106`), which answers with the *stored* root whether or not anybody chose it — and `commonpath` rejects any path above it:

```python
if os.path.normcase(common_path) != os.path.normcase(root_path):
    raise ValueError("Explorer path must stay inside the configured root")
```

In the case above that root was derived by the pane's own previous transition (`explorer_root_configured: False`, set at `web/session_modes.py:268-270`), so the refusal names "the configured root" for a root no configuration holds. `_configured_explorer_root_directory()` (`web/explorer.py:115`) exists precisely to distinguish the two and is consulted for what the pane *hands back*, never for containment.

**An observed cwd outranks a stated path.** The terminal → explorer branch overwrites the stated directory unconditionally (`web/session_modes.py:202-205`):

```python
requested_directory = data.get("directory")
cwd_probe = _refresh_pane_cwd(session_id, session, bool(data.get("refresh_cwd")))
if cwd_probe["directory"]:
    requested_directory = cwd_probe["directory"]
```

`_refresh_pane_cwd()` reads an existing observation even when no refresh was requested, by design (`web/session_modes.py:94-133` — "a caller that did not ask for a refresh still gets one"). The MCP dispatcher states `refresh_cwd` only for `mode="explorer"` **with no directory given** (`gridvibe_mcp/server.py:1047-1055`), so the one call that deliberately does not want the probe's answer is the one the probe overrules. That is step 5, and it is also what puts the derived root back on the repo, which is what makes the clamp above refuse.

**A directory-only change is not a transition.** Past the explorer and browser targets, the terminal branch opens with (`web/session_modes.py:309`):

```python
if not (_is_explorer_session(session) or _is_browser_session(session)):
    return session.to_dict()
```

A terminal pane asked to become a terminal at a different directory returns its unchanged record with a `200`. Step 4 is indistinguishable from a success at the tool boundary — `client.switch_pane_mode` builds its result from `PANE_FIELDS` (`gridvibe_mcp/client.py:46`) and the pane simply still reads the old path.

**No other tool fills the gap.** The relaunch transaction accepts `shell`, `agent`, `mcp` and `distribution` only (`web/session_shell.py:110-143`, `:348`) and deliberately re-targets to the observed cwd (`:247-250`), so `set_pane_agent` cannot carry a path. `launch_panes` and `split_pane` both take a `directory`, but they create a pane rather than move one — the workaround an agent is left with is to split a correctly-rooted pane off and ask the user to close the old one, which the destroy tier cannot do either.

### Proposed solution:
Make a stated path authoritative, so the MCP surface can perform the re-root its schema already advertises.

- **Stated wins over probed.** In the explorer branch, consult `cwd_probe` for the directory only when no `directory` was stated, or when `refresh_cwd` was explicitly asked for. The header's own toggle states no directory, so its behaviour — root where the shell is standing — is unchanged; the probe's `requested`/`resolved` reporting stays as it is, because a caller that named a path needs no fallback notice.
- **Containment applies to the pane's selection, not to a stated path.** In `_resolve_pane_terminal_directory()`, resolve an explicitly stated `directory` on its own merits — exists, is a directory, and (for SSH) normalizes through `sftp` on the pane's own host — and keep `_resolve_explorer_candidate_path()` for the *unstated* case, where the path being resolved really is the live explorer's current selection and must stay inside the boundary it was browsing. When a stated path falls outside the old root, leave the pane with no root and `explorer_root_configured: False`, which is the state the explorer branch already produces for a derived root.
- **Let a directory-only change relaunch.** Narrow the early return at `web/session_modes.py:309` so a terminal pane with a stated `directory` that differs from its current one runs the branch's ordinary metadata-update/teardown/restart, and returns unchanged only when no stated field actually differs. The blast radius is one shell the caller is already gated to replace. If that is judged too wide for this transaction, the alternative is a dedicated gated twin (`set_pane_directory` in `session_modes.py`, one MCP tool in `tool_specs()`/`_run()`) under the same self/lineage/agent-running gates from `web/pane_gates.py` — same gates, same wording, no new tier.
- **Fix the sentence either way.** The containment refusal should name the root it tested against and whether it was derived, so a caller that hits it knows whether the fix is a different path or a re-root.
- **Then correct the records.** `gridvibe_mcp/server.py`'s `directory` description ("Omit to use where the pane is standing now") must say that a stated path re-roots the pane, and the Pane transitions contract's "Only a shell-family change retargets the directory" (`docs/engineering_contracts.md:292-296`) needs the stated-directory case added beside it.

Tests: in `tests/test_api.py`, over the agent mode-switch route — a stated directory above a derived root is applied; a stated directory wins over an observed cwd; a stated path that does not exist still answers `400`; a terminal pane given only a new directory relaunches there; and the no-directory call still opens on the observed cwd, so the header toggle's behaviour is pinned against this change. One dispatcher case that a stated `directory` does not set `refresh_cwd` (`gridvibe_mcp/server.py`), and one SSH case that a stated remote path is normalized through `sftp` and refused when missing.

Edge cases: the live explorer's own containment (`web/explorer_fs.py:255`, and `_resolve_explorer_paths()` for every browse/read/transfer route) must not move — this changes only the re-root transition, not what a Files pane can walk into. A root restored from a saved session is replayed verbatim and must stay `explorer_root_configured: True`, so a re-root that drops the root has to write that flag rather than leave a stale `True` behind. A stated path on the wrong machine must be refused rather than silently resolved against the GridVibe host: the local branch's `os.path.isdir()` runs where GridVibe runs, which is correct for a Local Repo pane and wrong for an SSH one, so the remote branch owns its own check.

### Issue ID: ISSUE-2026-055
- Title: Restored pre-2026-07-24 split layouts can never be split side-by-side again
- Priority: Medium
- Status: Open
- Area: `web/static/js/terminals.js`, `web/session_presentation.py`
- Assignee: Unassigned
- Tags: `terminal`, `session`, `layout`, `persistence`, `tests`
- Reported: 2026-09-16

Description:
A saved workspace keeps the grid coordinates it was saved with, and nothing rescales them on restore. Layouts saved before `SPLIT_CELL_UNIT` was introduced (commit `adbabfa`, 2026-07-24) were laid out at **2 grid units per base cell** instead of today's 8, so one vertical split already takes a pane to `w: 1` — the floor of the integer grid. Such a pane can never be split side by side again, in that session or any session restored from it, no matter how wide the window is. The refusal is permanent and invisible: the layout renders normally and the pane can still be stacked, so the reader sees one greyed-out button with a tooltip that names a rule the pane is not actually breaking.

Live example from a restored workspace (`saved-session-session-20260628-205544`, saved 2026-06-28):

```
grid: columns 6, rows 4
pane acd1ae0d  rect {x:1, y:1, w:5, h:4}   relative_area 0.5788
pane 99cc5cba  rect {x:6, y:1, w:1, h:2}   relative_area 0.2106
pane 800b371e  rect {x:6, y:3, w:1, h:2}   relative_area 0.2106
```

The two right-hand panes hold 42% of the window width between them — far more than the 8-column character floor needs — yet neither can be split side by side. A 6×4 bounding box is exactly `getGridMetrics(6)` (3 columns × 2 rows, `web/static/js/shared.js:302`) at the **old** unit of 2; the same six-pane base built today is 24×16, where the same cell would still have three halvings left.

Steps to reproduce:
1. Restore any workspace saved before 2026-07-24 whose layout was split at least once (or synthesize one: save a six-pane split workspace, then divide every `x`/`y`/`w`/`h` in its stored `split_slot_rects` by 4 so the bounding box becomes 6×4, and split the right-hand cell vertically once).
2. Open the workspace so the saved layout is restored, and confirm a pane reports `w: 1` via `GET /api/dashboard` or the `list_panes` MCP tool.
3. Widen or maximise the GridVibe window so the pane is plainly wide enough to halve.
4. Hover the side-by-side split button on that pane.

Expected behavior:
A restored layout should carry the same splitting headroom as one built today, so a pane that is physically wide enough to halve can be split side by side. Where a split genuinely cannot happen, the disabled tooltip should name the rule that actually refused.

Actual behavior / logs:
`getSplitCandidates()` (`web/static/js/terminals.js:4038`) tests the integer grid before it measures anything:

```js
if (rect.w >= 2 && vertical.cols >= MIN_SPLIT_COLS && vertical.rows >= MIN_SPLIT_ROWS) {
    candidates.push('vertical');
}
```

With `rect.w === 1` the axis is dropped before `estimatePaneCharacters()` is consulted, so the character floor never gets a say. `getSplitDisabledReason('vertical')` (`:4422`) takes only the axis and returns a fixed sentence:

> Side-by-side split needs at least 8 columns in each terminal

That names `MIN_SPLIT_COLS`, which is not the rule that refused — the pane has roughly five times that many columns. The same sentence is returned by `splitTerminalPane()` at `:6969`, so it is also what the MCP `split_pane` tool relays as "GridVibe's own reason" (`gridvibe_mcp/splits.py`), giving an agent a false explanation of a permanent refusal.

Nothing rescales a restored rect. `applyWorkspaceLayoutSnapshot()` (`:3975`) maps stored `x`/`y`/`w`/`h` straight through; `normalizeSplitRectMetadata()` (`:3770`) only attaches split-ancestry fields and does not touch the coordinates; the close-reflow restore at `:7851` clones them as-is. Server side, `_normalize_workspace_layout()` (`web/session_presentation.py:640`) only bounds-checks against `MAX_STORED_SESSION_PANES * 8` (= 512 grid lines) and accepts `w: 1` as valid — it never migrates a record.

`split-geometry.js` already names this class of layout in its header ("spans reach odd widths … through layouts saved before the base cell had room to halve") and compensates for odd spans of 3 or more, but `planSplit()` returns its fallback for any span below 2, so a span of exactly 1 is outside what it can rescue.

### Proposed solution:
Rescale a coarse snapshot once, on restore, in `applyWorkspaceLayoutSnapshot()` (`web/static/js/terminals.js:3966`):

- Infer the snapshot's base-cell unit from its own bounding box and `original_split_slot_count`, using `getBaseLayoutSlots()`/`getGridMetrics()` — `unit_old = gridColumns / base.columns`, cross-checked against `gridRows / base.rows`.
- When `unit_old < SPLIT_CELL_UNIT`, multiply every rect by `k = SPLIT_CELL_UNIT / unit_old`: `x' = (x - 1) * k + 1`, `w' = w * k` (same on the other axis). Expand each track weight into `k` tracks of `weight / k` so the rendered proportions are preserved exactly and no divider appears to move.
- Leave the record untouched when the unit cannot be inferred cleanly (bounding box not divisible by the base dimensions, `original_split_slot_count` missing or inconsistent) rather than guessing — a wrong factor silently reproportions the window.
- Clamp so `max(x + w - 1) * k` stays within the persisted ceiling of `MAX_STORED_SESSION_PANES * 8` (512). `_normalize_workspace_layout()` rejects a geometry record all-or-nothing, so overshooting the bound would drop the whole layout on the next save rather than degrade it. A six-pane base at unit 8 is 24×16, so real layouts have ample headroom; the clamp is for hand-edited or malformed records.
- Verify the rescale runs before any capture path (`cloneSplitSlotRects` at `:1230`, the save at `:2253`) so a session never persists a mix of resolutions, and confirm `originalSplitSlotCount` survives it. The close-reflow restore at `:7851` and the group cache at `:1288` clone live rects and should need no change.
- Note in the change that this is a one-way migration: the rewritten coordinates are finer, not structurally different, so an older GridVibe reading the newer save still renders it.

Separately, make the refusal honest. `getSplitCandidates()` (`:4030`) already evaluates the grid-resolution condition and the character-size condition independently — have it report *which* one failed per axis, and thread that through `applySplitButtonState()` (`:4462`) and the `splitTerminalPane()` refusal (`:6969`) so the tooltip and the MCP `split_pane` detail distinguish "this pane has no grid room left to halve" from "this pane is too small on screen". This is worth doing even after the rescale, since a pane can still bottom out after three splits at the current unit.

Tests (extend `tests/test_split_geometry.py`, or a sibling Node test for the restore path):
- A unit-2 six-pane snapshot rescales to a 24×16 box, and every pane's rendered fraction is unchanged (per-span weight sums preserved).
- A restored `w: 1` rect becomes `w: 8`, and `getSplitCandidates()` then offers `vertical`.
- A snapshot already at unit 8 passes through byte-identically (the rescale is idempotent).
- A snapshot whose box would exceed 512 after scaling, and one whose box is not divisible by its base dimensions, are both left untouched rather than clamped into a different layout.
- The disabled-button tooltip and the `splitTerminalPane()` refusal name the grid-resolution rule for a `w: 1` pane and the character rule for a genuinely tiny one.
