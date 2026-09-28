# GridVibe Testing Issues
Last updated: 2026-09-28

## Open Issues

### Issue ID: ISSUE-2026-059
- Title: Agents cannot split a pane in a background session tab
- Priority: Medium
- Status: Open
- Area: `web/static/js/window-intent.js`, `web/static/js/terminals.js`, `gridvibe_mcp/splits.py`
- Assignee: Unassigned
- Tags: `mcp`, `session`, `terminal`, `windows`, `tests`
- Reported: 2026-09-28

Description:
An agent using the GridVibe MCP `split_pane` tool cannot split a pane in a session tab that its native workspace window is not currently showing. The agent can keep running in that tab, but the split request has no page claimant until the user selects the tab. The current `no_window_available` response says no GridVibe window was open even when the correct window is visible on another tab. This is a documented limitation of the current split flow, not evidence that the server rejected a valid split. The behavior was reported by a user and confirmed by code inspection; no live split was executed during this investigation.

Steps to reproduce:
1. In native mode, open a workspace window with two session tabs. Put an MCP-enabled agent pane in the second tab, in a grid and viewport large enough for a vertical split.
2. Select the first tab while the agent in the second tab continues running.
3. Have that agent call `split_pane` with its own `pane_id` and `axis: "vertical"`. Leave the window visible and the first tab selected while the tool waits.
4. Observe that the second tab gains no pane and the request ends as `no_window_available`, with a message saying no GridVibe window was open. Select the second tab and retry to compare.

Expected behavior:
An open native workspace window can complete a valid split in the target session without requiring that session to be selected by the user. If a background split cannot be performed safely, the tool should promptly state the actual tab or layout constraint instead of reporting that no window exists.

Actual behavior / logs:
Code inspection shows `POST /api/sessions/<id>/split-intent` records the target pane, group, and workspace (`web/api.py:3670-3774`). The native page polls intents, but `window-intent.js:95-106` treats a split as actionable only when `splitBridge.owns(sessionId)` succeeds. That bridge checks only `sessionIds`, the currently rendered group's pane list (`terminals.js:7193-7198`); switching groups caches the old grid and clears `sessionIds` (`terminals.js:1244-1302`). The background pane is therefore never claimed. The intent expires, and `gridvibe_mcp/splits.py` returns `no_window_available` with `NO_PAGE_HINT`, whose text incorrectly assumes there was no open window. Split eligibility and geometry currently rely on live DOM and terminal measurements (`terminals.js:4131-4207`), which explains why simply broadening the ownership check is insufficient. A hidden or minimized native page and browser mode are separate limitations.

### Proposed solution:
Support split intents for a group held by the workspace page even when another group is displayed. Choose and document a safe way to measure and update background layouts: a group-scoped offscreen measurement path or a shared layout planner using current viewport, terminal cell metrics, stored geometry, and presentation revision. Keep the existing pane cap, narrow-viewport and minimum-cell refusals, creator/lineage gates, and one-claim/one-result intent semantics. Recheck the target pane and group after asynchronous work, and reconcile the new pane and geometry with the cached view so returning to the tab neither loses the split nor overwrites it with stale presentation. If an interim implementation switches tabs to use the existing handler, check the existing unsaved-editor and in-flight file-operation blockers, avoid stealing OS focus, and restore the prior tab only if the user has not switched tabs meanwhile. Update the `no_window_available` wording or return a distinct refusal when a window exists but the target tab cannot be handled, and update `gridvibe_mcp/README.md` and the MCP contract when behavior changes. Add focused Node tests for a split targeting a background group, concurrent tab switches, stale pane/group ownership, refusal/expiry wording, and geometry restoration, plus a sidecar/API test for the reported result.
