# GridVibe Testing Issues
Last updated: 2026-09-29

## Open Issues

### Issue ID: ISSUE-2026-059
- Title: Dashboard does not highlight the focused agent terminal
- Priority: Low
- Status: Open
- Area: `web/static/js/terminals.js`, `web/static/js/dashboard-sidebar.js`, `web/static/js/dashboard-dialog.js`, `web/static/css/agent-dashboard.css`
- Assignee: Unassigned
- Tags: `agent`, `dashboard`, `terminal`, `ui`, `accessibility`, `tests`
- Reported: 2026-09-29

Description:
Clicking an agent terminal gives its pane the active border treatment, but its corresponding dashboard row does not reflect that selection. With several agents running, users cannot identify their current input target from the dashboard. The dashboard should highlight the focused agent consistently with the terminal's existing border highlight. This is a separate gap from closed ISSUE-2026-025, which added the terminal-pane highlight itself.

Steps to reproduce:
1. Open a GridVibe workspace with at least two agent terminal panes and open the docked Agent Dashboard sidebar.
2. Click inside the first agent terminal so it has keyboard focus and its pane border is highlighted.
3. Inspect its dashboard row, then click inside the other agent terminal and compare the rows again.
4. Observe that the terminal border follows focus, while neither dashboard row identifies the focused agent.

Expected behavior:
The dashboard row for the focused agent receives a clear, theme-aware highlight comparable to its terminal's active border. The highlight moves immediately when focus moves to another agent terminal and clears when no agent terminal is focused. It identifies the input target independently of the agent's working/idle indicator, row hover, and active session-group chip.

Actual behavior / logs:
User-reported behavior is supported by code inspection; no live UI reproduction was performed. In `web/static/js/terminals.js`, `setFocusedTerminal()` and `clearActiveTerminalHighlight()` update `_focusedTerminalIndex` and call `paintActiveTerminalCard()`, which toggles `terminal-active` and `aria-current` only on terminal cards. `dashboardAgentRowHtml()` in `dashboard-dialog.js` and `agentRowHtml()` in `dashboard-sidebar.js` render `class="dash-agent"` without terminal-focus state. The shared dashboard stylesheet provides row hover and `:focus-visible` treatments, which indicate interaction with the dashboard button itself rather than focus in its agent terminal.

### Proposed solution:
Connect dashboard row decoration to the existing terminal focus lifecycle using stable session identity rather than grid slot alone. Update the docked sidebar immediately on focus changes and reapply the decoration after dashboard refreshes or rebuilds; share the focus-reading rule with the dialog wherever it can observe the same state. Use theme tokens consistent with the active terminal border and expose an accessible current-state indication without replacing row buttons or moving focus.

Clear stale decoration on terminal blur, focus entering a non-agent pane or other page control, pane removal, session-tab switching, grid teardown, and workspace-window switching. Keep focus separate from output/activity and broadcast typing: agent output or mouse-report sequences must not select a dashboard row, and broadcast borders must not mark every agent as the current input target. Focus is transient UI state; no saved-session or runtime-snapshot migration is needed. If focus is exposed across windows, bind it to its originating window and clear expired or departed owners.

Add focused behavioral coverage in `tests/test_dashboard_sidebar.py`, `tests/test_dashboard_dialog.py`, and the existing terminal-focus tests for agent A-to-B focus, programmatic dashboard navigation to a pane, clearing focus, non-agent targets, poll/rebuild preservation, and removal or replacement of the selected pane. Include light/dark styling checks and verify that the agent activity indicator remains independent.
