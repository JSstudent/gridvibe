# GridVibe Testing Issues
Last updated: 2026-07-16

## Open Issues

### Issue ID: ISSUE-2026-031
- Title: App Settings action buttons overlap voice settings content
- Priority: Low
- Status: Open
- Area: `web/static/css/launcher.css`, `templates/index.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `settings`, `launcher`, `ui`, `tests`
- Reported: 2026-07-16

Description:
When voice input is enabled in the launcher App Settings modal, the pinned Save Settings and Cancel action buttons overlap the bottom of the voice settings content instead of sitting below a scrollable body. At common desktop window sizes the buttons clip into the microphone section (for example over the "Microphones loaded. Labels may stay generic until microphone permission is granted" hint), obscuring the last controls and looking broken. Because the fields backing the same modal are where new terminal configuration would be added (ISSUE-2026-029), any further growth of this modal will make the overlap worse.

Steps to reproduce:
1. Open GridVibe and open App Settings from the launcher at a normal desktop window size.
2. Enable "Enable voice input" so the full voice settings panel (backend, model, device, compute type, microphone, push-to-talk) is shown.
3. Observe that the Save Settings and Cancel buttons overlap the bottom of the voice content rather than resting below a scrollable settings area.

Expected behavior:
The App Settings body should scroll within the modal's fixed height while the header and the Save Settings / Cancel action row stay pinned and never overlap content. Enabling voice input (or adding more settings later) should extend the scrollable region, not push content under the action buttons.

Actual behavior / logs:
Code inspection confirms `.modal-card` in `web/static/css/launcher.css` is a `grid-template-rows: auto minmax(0, 1fr) auto` layout (header / scroll body / pinned actions), and the base `.settings-grid` scrolls via `overflow: auto; min-height: 0`. However `.app-settings-card .settings-grid` overrides this with `overflow: visible; padding-right: 0`, which disables the scroll region for the App Settings modal. With the card capped by `.app-settings-card { max-height: min(92vh, 860px) }` / `.app-settings-card.voice-enabled { max-height: min(96vh, 900px) }`, the taller voice content overflows its `1fr` track and paints under the pinned `.app-settings-actions` footer. The narrow-width breakpoint restores `.app-settings-card .settings-grid { overflow: auto }`, so the overlap is only visible at wider desktop widths with voice enabled.

### Proposed solution:
Remove or correct the `.app-settings-card .settings-grid { overflow: visible }` override in `web/static/css/launcher.css` so the App Settings body keeps the intended `overflow: auto; min-height: 0` scroll behavior at all widths, keeping the pinned action row out of the content flow. Verify the `max-height` caps and `voice-enabled` variant leave the actions fully visible with voice enabled, and confirm scroll padding does not clip focus rings. Since the same modal is the planned home for new terminal settings (ISSUE-2026-029), keep the body scroll model intact when those fields are added so a longer form does not reintroduce the overlap. Add a focused rendered-template/CSS regression test in `tests/test_api.py` asserting the App Settings body uses a scrollable overflow (not `overflow: visible`) and that the action row is a pinned grid row, covering the voice-enabled state.

### Issue ID: ISSUE-2026-030
- Title: Add user-customizable Markdown preview appearance and presets
- Priority: Low
- Status: Open
- Area: `web/static/css/terminals.css`, `web/static/js/terminals.js`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `markdown`, `settings`, `ui`, `tests`
- Reported: 2026-07-15

Description:
The Explorer Markdown preview renders with a single fixed appearance and offers no way for users to customize how it looks — font family, text size, or background/reading surface — or to pick from ready-made presets. Users who read long documentation in GridVibe cannot switch to a serif or paper/sepia reading surface, a high-contrast surface, or a larger reading font. This is a user-facing customization request and is distinct from ISSUE-2026-017, which covers developer-defined default visual hierarchy and note callouts rather than user-selectable appearance.

Steps to reproduce:
1. Open an Explorer pane and select a Markdown file, then switch from Source to Preview.
2. Inspect the preview toolbar/header for any font, background, theme, or preset controls.
3. Observe that the preview always uses one fixed font and background, with only the shared editor zoom available, and no preset or reading-surface options.

Expected behavior:
Markdown Preview should let the user adjust its appearance — at minimum a small set of presets (for example default, paper/sepia, high-contrast, and a serif/reading variant) and/or explicit font-family and font-size controls. The chosen appearance should apply immediately to the active preview, remain within GridVibe's theme tokens, coexist with light/dark themes and the existing zoom control, persist across previews, and never weaken Markdown sanitization or the read-only guarantee.

Actual behavior / logs:
Code inspection confirms `web/static/css/terminals.css` styles `.explorer-markdown-preview` with fixed padding, font, and background rules, and there is no client control, setting, or CSS-variable hook that lets the user change the preview font, background, or reading surface. `_render_markdown_preview()` in `web/explorer.py` emits sanitized semantic HTML but carries no appearance metadata. No `RuntimeConfig`/`/api/app-config` field or local preference stores a Markdown-preview appearance choice.

### Proposed solution:
Add a compact, accessible appearance control to the Markdown preview surface in `web/static/js/terminals.js` (a preset selector, optionally with font-family and font-size adjusters) and drive it through preset classes plus CSS custom properties on `.explorer-markdown-preview` defined from `tokens.css` in `web/static/css/terminals.css`, so no hardcoded palette literals are introduced. Persist the selection (a bounded local preference or an `/api/app-config`/`RuntimeConfig` field wired end to end), apply it idempotently to newly opened previews, and keep it compatible with light/dark themes, the existing zoom control, narrow panes, and `prefers-reduced-motion`. Preserve the sanitized rendering path in `web/explorer.py` and the read-only contract; do not couple this to the callout work in ISSUE-2026-017 beyond sharing tokens. Add focused tests in `tests/test_api.py` for preset class emission, persisted-preference round-trip and validation, token-only styling, theme/zoom coexistence, and default fallback.

### Issue ID: ISSUE-2026-029
- Title: Settings UI omits terminal config (font family, size, max sessions)
- Priority: Low
- Status: Open
- Area: `templates/index.html`, `web/static/js/launcher.js`, `web/api.py`, `web/config.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `settings`, `launcher`, `terminal`, `flask`, `tests`
- Reported: 2026-07-15

Description:
`default_config.json` exposes a `terminal` block (`max_sessions`, `font_family`, `font_size`) that is loaded into `RuntimeConfig` and applied to the xterm workspace, but the App Settings modal provides no controls for any of it. Users can change theme, workspace surface mode, SSH host-key policy, and voice options from the UI, yet can only change the terminal font family, font size, or maximum session count by hand-editing `config.json` and restarting, which is not discoverable and inconsistent with the other runtime settings.

Steps to reproduce:
1. Launch GridVibe and open App Settings from the launcher.
2. Inspect the available settings fields.
3. Observe controls for Appearance theme, workspace surface mode, SSH host-key policy, and voice input, but none for terminal font family, terminal font size, or maximum sessions, even though these exist in `default_config.json`.

Expected behavior:
App Settings should surface the terminal configuration already backed by `RuntimeConfig` — terminal font family, terminal font size, and maximum sessions — with validated, bounded inputs. Saving should round-trip through `/api/app-config` and `RuntimeConfig`, persist to `config.json`, and apply to the launcher and open/newly launched session windows the same way theme changes already propagate.

Actual behavior / logs:
Code inspection confirms the App Settings modal in `templates/index.html` renders only `appTheme`, `appSurfaceMode`, `appSshHostKeyPolicy`, and the voice fields. `collectAppSettings()`/`saveAppSettings()` in `web/static/js/launcher.js` gather only `appearance`, `workspace`, `ssh`, and `voice_input`. `_normalize_app_config_update()` in `web/api.py` whitelists exactly those sections and never reads or writes a `terminal` block, while `web/config.py` (`RuntimeConfig`) loads `terminal.max_sessions`, `terminal.font_size`, and `terminal.font_family`, and `web/api.py` already passes `terminal_font_size`/`terminal_font_family` into the terminals template. The values are therefore file-only with no UI or API write path.

### Proposed solution:
Add terminal fields to the App Settings modal in `templates/index.html`, extend `collectAppSettings()` in `web/static/js/launcher.js` to include a `terminal` section, and add a validated `terminal` branch to `_normalize_app_config_update()` in `web/api.py` (bounded font size, non-empty font-family string, `max_sessions` clamped to a safe range) that persists through the existing config-save flow. Reuse the established app-config update contract so the change reaches open session windows, and confirm `RuntimeConfig` reload applies the new values. Guard against invalid/oversized input and preserve backward compatibility for configs without user-set terminal fields. Add focused tests in `tests/test_api.py` for normalization bounds, whitelist acceptance, persistence, and modal/collect wiring.

### Issue ID: ISSUE-2026-027
- Title: Closing a terminal resets explorer/browser pane state in the group
- Priority: Medium
- Status: Open
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `file-explorer`, `session`, `ui`, `tests`
- Reported: 2026-07-15

Description:
Closing a single terminal pane in a session group rebuilds the entire visible group, which discards the client-only state of every other pane. Explorer panes lose their open file/preview, expanded Files tree, and open Git sidebar; browser-preview panes reload. Only pane geometry is preserved. The practical impact is that closing one terminal wipes unrelated reference views the user had set up in the same group.

Steps to reproduce:
1. Open a session group with a terminal pane plus an Explorer pane; in the Explorer pane open a file/preview, expand a few Files-tree folders, and open the Git sidebar (optionally add a browser-preview pane at a scrolled URL).
2. Close the terminal pane with its `×` button.
3. Observe that the Explorer pane returns to a plain directory listing with collapsed trees and closed Git sidebar, and any browser pane reloads, even though those panes were not closed.

Expected behavior:
Closing a terminal should remove only that pane and expand its neighbor into the freed space, while every other pane retains its exact state — Explorer panes keep their open file/preview, expanded tree, and Git sidebar; browser panes keep their URL and scroll — matching how a single split add/remove leaves untouched panes alone.

Actual behavior / logs:
Code inspection confirms `closeTerminalPane()` in `web/static/js/terminals.js` computes `restoreRectsBySessionId` via `buildTerminalCloseRectsBySessionId(plan)`, sets `pendingSplitRestore` with only `groupId`, `rectsBySessionId`, and `originalSplitSlotCount`, then calls `await initialLoad()`. `initialLoad()` tears down and reconstructs the whole visible group's grid, so panes are recreated from server session metadata; `pendingSplitRestore` restores only geometry, not the explorer/browser client state, which is never captured.

### Proposed solution:
Avoid the full-grid `initialLoad()` rebuild for a single non-last-pane close in `web/static/js/terminals.js`. Prefer an in-place path that removes only the closed card and reflows neighbor geometry (mirroring the incremental approach used when splitting) so sibling panes and their DOM/state survive untouched. If a rebuild is unavoidable, capture and restore each surviving pane's explorer state (open file/tab, tree-open set, Git sidebar visibility, scroll) and browser state (URL, scroll) across the reload alongside the existing rect restore. Preserve the neighbor-fill/longest-shared-border behavior, the read-only guarantee, and last-pane close semantics. Add focused tests in `tests/test_api.py` covering a terminal close in a mixed group that asserts surviving explorer/browser panes retain open file, expanded tree, Git sidebar, and geometry.

### Issue ID: ISSUE-2026-026
- Title: Voice transcription ignores Broadcast typing and reaches one terminal
- Priority: Medium
- Status: Open
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `voice`, `terminal`, `socketio`, `tests`
- Reported: 2026-07-15

Description:
When Broadcast typing is enabled, keyboard input is mirrored to every plain terminal pane, but voice transcription is not. A final voice transcript is delivered only to the single terminal that was recording, so users who expect voice dictation to broadcast to all panes (the way typing does) silently reach just one terminal.

Steps to reproduce:
1. Open a session group with two or more plain terminal panes and enable Broadcast typing.
2. Start voice capture on one terminal (mic button or push-to-talk), speak, and let the final transcript commit.
3. Observe that only the recording terminal receives the transcribed text, while the other broadcast panes receive nothing.

Expected behavior:
While Broadcast typing is active, a committed voice transcript should be mirrored to every participating plain terminal pane, consistent with how keyboard input is broadcast. Interim (non-final) preview text should remain on the recording pane only, and explorer/browser panes should continue to be excluded.

Actual behavior / logs:
Code inspection confirms the `voice_result` Socket.IO handler in `web/static/js/terminals.js` calls `_sendToTerminal(index, text)` on the final transcript, and `_sendToTerminal()` emits `terminal_input` to the single `sessionIds[index]` only. Unlike `forwardTerminalInput()`, which mirrors to all plain panes when `broadcastInputActive` (skipping panes without a `term`), the voice path has no broadcast branch, and `_voiceActiveIndex` restricts recording to one terminal, so the transcript never fans out.

### Proposed solution:
Route the committed voice transcript through the same broadcast mirroring used for keyboard input. When `broadcastInputActive`, have the `voice_result` final branch fan the text out to every plain terminal pane (reusing `forwardTerminalInput()`'s pane filter that skips the source pane and any pane without a `term`, i.e. explorer/browser panes) and note broadcast activity via `_noteBroadcastActivity()`; keep interim previews on the recording pane only. Ensure a single emit per target with no duplication to the recording pane. Add focused tests in `tests/test_api.py` for broadcast-on final fan-out, broadcast-off single delivery, interim-preview isolation, and explorer/browser exclusion.

### Issue ID: ISSUE-2026-025
- Title: Highlight the currently active terminal pane
- Priority: Low
- Status: Open
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `ui`, `accessibility`, `tests`
- Reported: 2026-07-15

Description:
GridVibe does not visually identify the terminal pane that is currently selected for input. In a multi-pane workspace, users must infer the input target from browser focus alone. This is noticeably inconsistent with Broadcast typing mode, which gives every participating plain terminal pane a clear accent border, and makes it easier to send a command to the wrong terminal.

Steps to reproduce:
1. Open a GridVibe session group with two or more plain terminal panes.
2. Click or type in one terminal pane, then inspect the panes' borders and headers.
3. Observe that the input-target pane has no persistent active/selected visual treatment; enable Broadcast typing to see the existing accent-border treatment applied to all participating panes.

Expected behavior:
Exactly one eligible terminal pane should have a clear, theme-aware active/selected treatment whenever it is the current input target. The treatment should be comparable in prominence to the broadcast border while remaining distinguishable from broadcast mode, and it should update for pointer, keyboard, programmatic focus, terminal search close/return-focus, pane creation, removal, and session-group switching.

Actual behavior / logs:
Code inspection confirms `forwardTerminalInput()` and the terminal-card `pointerdown` listeners update `_focusedTerminalIndex`, which is also used to choose keyboard-search and push-to-talk targets. Neither path adds a class, ARIA state, or other visible marker to the corresponding `.terminal-container`. In contrast, `setBroadcastInput()` toggles `broadcast-input` on `#terminalsGrid`, and the `.broadcast-input .terminal-container:not(.explorer-pane):not(.browser-pane)` CSS rule applies `var(--t-accent)` border and inset shadow to every participating terminal. Existing tests cover broadcast-mode class and CSS hooks but do not cover visible single-pane selection state.

### Proposed solution:
Add one centralized client-side helper in `web/static/js/terminals.js`, such as `setFocusedTerminal(index)`, that validates the target, updates `_focusedTerminalIndex`, and toggles a semantic class (for example, `terminal-active`) plus appropriate accessibility state on the relevant terminal card while clearing it from other panes. Route existing pointer, terminal input, focus, focus-restoration, split, close, and group-switch paths through that helper; define a safe fallback target when the selected pane is removed or no plain terminal remains. Add a token-based active-pane style in `web/static/css/terminals.css` that remains visually distinct when Broadcast typing is enabled, avoids hardcoded colors, and preserves explorer/browser exclusions. Add focused rendered-template and behavioral tests in `tests/test_api.py` for pointer and keyboard selection, focus transitions, group switches, split/close fallback, broadcast coexistence, one-active-pane enforcement, and accessible state updates.

### Issue ID: ISSUE-2026-022
- Title: Closing a terminal expands unrelated panes in complex split layouts
- Priority: Medium
- Status: Open
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `ui`, `resize`, `tests`
- Reported: 2026-07-14

Description:
Closing a terminal pane in a workspace with an asymmetric or multi-neighbor split can resize multiple unrelated panes instead of only expanding the single neighbor that shares the longest border with the closed pane. This disrupts the workspace layout and is especially visible when the user has manually resized panes to specific proportions.

Steps to reproduce:
1. Open a GridVibe session with three or more terminal panes in an asymmetric split — for example, one wide pane below two side-by-side panes (T-shape), or one tall pane beside two stacked panes (L-shape).
2. Optionally, drag the resize handles to set non-uniform pane sizes.
3. Close one of the panes that borders multiple others (e.g., the wide bottom pane that shares border with both top-left and top-right).
4. Observe that more than one remaining pane changes size, rather than only the single neighbor with the longest shared border.

Expected behavior:
Closing a terminal pane should expand exactly one neighbor — the pane that shares the longest border with the closed pane. All other panes should remain at their exact previous sizes and proportions. Only when no single neighbor can absorb the closed rect without overlapping other panes should multiple panes be involved, and even then the set should be minimized to the smallest valid side group.

Actual behavior / logs:
Code inspection confirms `buildTerminalCloseRectsBySessionId` in `web/static/js/terminals.js` has two paths. The primary path (`findTerminalCloseNeighbor` + `canAbsorbClosedRect`) correctly expands only one neighbor, but it requires the union of the neighbor's rect and the closed rect to form a clean non-overlapping rectangle. In complex or asymmetric layouts this condition often fails, and the code falls back to `terminalCloseSideGroups` + `buildTerminalCloseRectsForSideGroup`. In this fallback, every pane in the winning side group is expanded via `expandRectIntoClosedSide` — not only the pane with the longest shared border. For example, closing the bottom pane of a T-shaped layout (two panes above, one wide pane below) causes both top panes to grow downward, even though only the one with the greater shared border was expected to expand. Existing tests in `tests/test_api.py` only assert that the relevant function names appear in the rendered HTML; no behavioral tests cover multi-neighbor close scenarios or verify that non-targeted panes retain their rects.

### Proposed solution:
Refine the side-group fallback in `buildTerminalCloseRectsForSideGroup` (and its callers) so that when a full-side expansion is necessary, preference is given to the single pane in the group with the greatest shared border rather than expanding all contacts uniformly. If expanding only that one pane still satisfies the overlap and area invariants, use the single-pane expansion. Only fall back to the full side-group expansion when the single-pane attempt fails the overlap or area check. Additionally, preserve the existing `splitColumnWeights` and `splitRowWeights` correctly when the grid column or row count changes after a close, so panes outside the expansion retain their user-set proportions. Add focused behavioral tests in `tests/test_api.py` for: T-shaped and L-shaped three-pane closes, four-pane grid closes, asymmetric closes where one candidate has a clearly longer shared border, and confirmation that non-targeted panes' grid column/row assignments and rect values are unchanged after a close.

### Issue ID: ISSUE-2026-020
- Title: Large log previews discard the newest entries
- Priority: Medium
- Status: Open
- Area: `web/api.py`, `web/explorer.py`, `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `logging`, `flask`, `ui`, `tests`
- Reported: 2026-07-13

Description:
Explorer text previews are capped at 1 MiB and retain the beginning of an oversized file. For append-oriented files such as logs, this discards the newest and usually most relevant entries, so users cannot inspect current failures or recent activity from GridVibe. The only workaround is to leave the Explorer and use an external tailing or paging tool.

Steps to reproduce:
1. Create a `.log` file larger than 1 MiB with distinguishable markers near its beginning and end.
2. Open the file from a GridVibe Explorer pane.
3. Observe that the beginning marker is present, the end marker is absent, and the preview is reported as truncated.

Expected behavior:
GridVibe should support a larger but still bounded text-preview limit where practical. When a log or other append-oriented file must be truncated, the preview should discard content from the beginning and retain the newest content at the end. The UI should clearly identify the retained range, and non-log files should have an explicit, predictable head/tail policy rather than silently losing the most useful section.

Actual behavior / logs:
Code inspection confirms `EXPLORER_FILE_PREVIEW_MAX_BYTES` in `web/explorer.py` is fixed at `1024 * 1024`. `get_explorer_file()` in `web/api.py` calls `backend.read_file_prefix(file_path, EXPLORER_FILE_PREVIEW_MAX_BYTES + 1)` and slices `raw_content[:EXPLORER_FILE_PREVIEW_MAX_BYTES]`, so every oversized preview starts at byte zero. `test_explorer_file_truncates_large_text_preview()` in `tests/test_api.py` checks only that truncation occurred and that the returned length equals the cap; it does not verify which end of the file is retained.

### Proposed solution:
Make the preview byte limit a validated, bounded setting or raise it to a measured safe value, while preserving protections against browser stalls and excessive local or SFTP reads. Add a ranged/tail-read operation to the local and SFTP explorer backends and use it for `.log` and any explicitly classified append-oriented formats so truncated previews retain the last configured bytes. Consider exposing a Head/Tail selector for other oversized text files, with a documented default appropriate to their type. Return range metadata such as retained start/end bytes and total size, and update `web/static/js/terminals.js` to show a clear message such as `Showing the last 1 MiB` instead of only a generic truncation warning. Handle UTF-8 and line boundaries so a tail read does not begin with a broken character or misleading partial line. Add focused tests in `tests/test_api.py` for retained end markers, excluded start markers, files exactly at the limit, multibyte and long-line boundaries, local/SFTP parity, range metadata, configured-limit validation, and client messaging, while keeping search and highlighting bounded to the returned preview.

### Issue ID: ISSUE-2026-017
- Title: Improve Markdown preview visual hierarchy and callouts
- Priority: Cosmetic
- Status: Open
- Area: `web/static/css/terminals.css`, `web/explorer.py`, `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `markdown`, `tests`
- Reported: 2026-07-13

Description:
Markdown previews are functional but visually plain, making headings, lists, notes, and other document structure harder to scan than they should be. The weak hierarchy is especially noticeable in longer README and documentation files, where users depend on highlighted titles, distinct bullet levels, and visible note or warning blocks.

Steps to reproduce:
1. Open an Explorer pane and select a Markdown file containing multiple heading levels, nested bullet or numbered lists, blockquotes, and note-like content.
2. Switch from Source to Preview.
3. Observe that the content has basic spacing and code/table treatment, but headings, list levels, and note content have limited visual distinction.

Expected behavior:
Markdown Preview should present a polished, theme-aware document surface with clearly differentiated heading levels, readable paragraph width and spacing, visible ordered/unordered list markers and nesting, styled links and separators, and distinctive accessible callouts for notes, tips, warnings, and important information. The result should remain readable in light and dark themes and at every supported editor zoom level.

Actual behavior / logs:
Code inspection confirms `web/static/css/terminals.css` gives `.explorer-markdown-preview` basic padding, font sizing, and line height; applies only margin and line-height rules to `h1` through `h3`; gives lists only a shared bottom margin; and renders all blockquotes with one muted left-border style. `_render_markdown_preview()` in `web/explorer.py` supports common Markdown extensions and sanitized semantic HTML, but it does not define a dedicated note/callout representation. Existing tests verify sanitized Markdown and fenced-code language hints, not the visual hierarchy or callout styling requested here.

### Proposed solution:
Create a cohesive Markdown preview theme in `web/static/css/terminals.css`: constrain readable line length where space permits; give `h1` through `h6` progressively distinct size, weight, color, spacing, and divider treatment; style nested list indentation and markers; and refine links, horizontal rules, tables, task items, images, inline code, and fenced blocks with the existing explorer theme variables. Define and document one note syntax, preferably GitHub-style `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, and `[!CAUTION]` blockquotes, then extend the sanitized rendering path in `web/explorer.py` or a bounded client postprocessor in `web/static/js/terminals.js` to emit safe semantic callout classes and icons/labels. Maintain sanitization, keyboard selection, search highlighting, zoom behavior, narrow-pane wrapping, and light/dark contrast. Add focused API/template tests for every supported heading/list/callout form, sanitizer restrictions, nested content, theme classes, and regression coverage for existing code blocks, tables, footnotes, and raw-HTML handling.

### Issue ID: ISSUE-2026-013
- Title: Add per-agent auto-mode toggles to terminal settings
- Priority: Low
- Status: Open
- Area: `web/static/js/launcher.js`, `web/api.py`, `sessions/manager.py`, `tests/test_api.py`, `tests/test_session_manager.py`
- Assignee: Unassigned
- Tags: `terminal`, `settings`, `launcher`, `session`, `tests`
- Reported: 2026-07-12

Description:
Agent-mode terminal settings need an auto-mode toggle tied to the selected agent. GridVibe currently launches built-in agents with their base command only, requiring users who want unattended edit/approval behavior to add flags manually or use a custom command instead of the normal agent selection flow.

Steps to reproduce:
1. In the GridVibe launcher, set a terminal's Startup Mode to Agent and select a supported agent such as Claude.
2. Inspect the agent settings, enable the desired launch configuration, and launch the session.
3. Observe that no auto-mode toggle is available and Claude starts as `claude` rather than, for example, `claude --enable-auto-mode`.

Expected behavior:
Agent mode should expose an Auto mode toggle for the corresponding selected agent. When enabled, GridVibe should launch that agent with its registered auto-edit/approval option, such as `claude --enable-auto-mode`; when disabled, it should retain the current base command. The setting should remain scoped per terminal, use only flags supported by the selected agent, and round-trip through launcher drafts, saved sessions, and active workspace Save Session behavior.

Actual behavior / logs:
Code inspection confirms `buildTerminalInitialCommand()` in `web/static/js/launcher.js` returns either the selected built-in agent key or the custom-agent text verbatim. `collectTerminalDrafts()` stores agent selection metadata but has no auto-mode field, `TerminalSession` in `sessions/manager.py` has no corresponding persisted property, and `_run_startup_sequence()` in `web/api.py` sends `session.initial_command` unchanged. The agent settings UI therefore has no way to represent or consistently restore an agent-specific auto-mode choice.

### Proposed solution:
Add a per-terminal boolean such as `agent_auto_mode` to the launcher draft, saved-session normalization, workspace serialization, `TerminalSession`, and API responses. Define supported auto-mode arguments in the existing agent registry or another single backend-owned mapping instead of hard-coding one generic flag for every CLI. Update the agent settings row in `web/static/js/launcher.js` to show an accessible toggle only when the selected built-in agent has a registered option, recompute visibility and help text when the selection changes, and keep custom-agent behavior explicit rather than silently modifying arbitrary commands. Compose the final startup command from the validated selected-agent metadata and mapped argument without duplicating flags, while keeping preflight detection based on the base agent executable. Add focused tests in `tests/test_api.py` and `tests/test_session_manager.py` for enabled/disabled command construction, unsupported and custom agents, save/load/workspace round-trips, SSH and local launches, and backward compatibility for saved sessions without the new field.

## Closed Issues

### Issue ID: ISSUE-2026-014
- Title: Replace explorer directory view with tabbed file viewer
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `terminal`, `tests`
- Reported: 2026-07-12
- Closed: 2026-07-16

Description:
Explorer mode should use its existing file tree for navigation and dedicate the main pane to read-only file viewing. The current main pane duplicates directory-browsing behavior and can display only one opened file at a time, so users cannot keep several reference files open while moving through the tree.

Steps to reproduce:
1. Open a GridVibe pane in Explorer startup mode and expose its Files tree.
2. Select files and directories in the tree and inspect the main explorer pane.
3. Observe that the main pane starts as a directory listing, a file replaces that listing with a single view and Back button, and file-tree rows have no `+` action for opening closable file tabs.

Expected behavior:
Explorer mode should always show a read-only file-viewer workspace. Its first tab should be a permanent, non-closable dynamic preview that initially says `Select a file to view` and updates whenever the user clicks a readable file in the tree. Each readable file row should also have a small `+` button that opens the file in a separate closable tab. The file-tab strip should sit above the existing file header/Back-button row, pinned tabs should remain open while the user browses, and closing the active pinned tab should return focus to a sensible remaining tab, including the permanent preview.

Actual behavior / logs:
Code inspection confirms `renderExplorerDirectoryRows()` in `web/static/js/terminals.js` renders the main pane as a directory/file list, and `wireExplorerDirectoryRows()` navigates folders or replaces that list through `openExplorerFile()`. The Files tree separately renders file rows through `explorerTreeRowHtml()`, but each file has only the main `data-explorer-tree-file` click target; only directories receive a secondary action, currently `Open folder in the explorer list`. `renderExplorerFile()` then replaces the main pane with one editor and Back button. There is no persistent file-tab collection, non-closable preview tab, close action, or empty viewer state.

Resolution:
`explorer-list-<index>` now hosts a stable shell — an `.explorer-tab-strip` above an `.explorer-viewer` body — created lazily by `explorerEnsureViewerShell()`, so the main pane is always the read-only viewer. A per-pane tab model (`pane._explorerTabs` + `pane._explorerActiveTabId`, seeded by `ensureExplorerTabState()`) keeps one permanent non-closable **Preview** tab (`EXPLORER_PREVIEW_TAB_ID`) plus deduplicated pinned tabs keyed by `explorerNormalizeTabPath()` and capped at `EXPLORER_MAX_PINNED_TABS` (12). `explorerAssignOpenTab()` sends a plain tree/file click to the Preview tab and a new `+` control on file rows (`data-explorer-tree-open-tab`) to a pinned tab. `renderExplorerTabStrip()` renders/wires the strip above the file header; `activateExplorerTab()`/`closeExplorerTab()` switch and close tabs (closing the active pinned tab falls back to its left neighbour, ultimately the Preview tab, which cannot be closed). First show routes through `openExplorerViewer()` — the empty **"Select a file to view"** state with the Files tree auto-opened for navigation — and directory browsing still works but lives inside the Preview tab so the Stage-1 directory-search subsystem is unchanged. Covered by `test_terminals_page_explorer_uses_tabbed_file_viewer` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-016
- Title: Markdown preview links do not open explorer tabs
- Priority: Medium
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `markdown`, `terminal`, `tests`
- Reported: 2026-07-13
- Closed: 2026-07-16

Description:
Links rendered inside a Markdown file preview do not participate in Explorer navigation. Relative documentation links are effectively broken in the terminal workspace, and clicking them does not open the linked Markdown file in a new Explorer tab. This is a companion requirement to the tabbed file viewer in ISSUE-2026-014; any pinned link-opened tabs should also use the persistence model tracked in ISSUE-2026-015.

Steps to reproduce:
1. In an Explorer pane, open a Markdown file that contains a relative link to another readable Markdown file, such as `[Guide](docs/guide.md)`.
2. Switch to Preview and click the rendered `Guide` link.
3. Observe that GridVibe does not resolve the target relative to the current file or open it as a new closable Markdown tab in the Explorer pane.

Expected behavior:
Clicking an internal Markdown-to-Markdown link should resolve the target relative to the source document, validate it against the active Explorer root, and open or focus it as a new closable pinned tab using the model defined by ISSUE-2026-014. Fragment links should focus the corresponding heading, duplicate targets should reuse their existing tab, and external or unsupported links should follow an explicit safe behavior without navigating the GridVibe session page away.

Actual behavior / logs:
Code inspection confirms `_render_markdown_preview()` in `web/explorer.py` preserves sanitized `href` attributes on anchors. `renderExplorerFile()` in `web/static/js/terminals.js` assigns that HTML directly to the preview and wires code highlighting, editor tabs, zoom, and search, but it does not install a Markdown-link click handler or resolve link paths against the current file. The browser therefore receives raw relative URLs in the `/terminals` document context, and no Explorer file-tab action is invoked.

Resolution:
`wireExplorerMarkdownLinks()` installs a delegated click handler on each `.explorer-markdown-preview` (in both `renderExplorerFile()` and the in-place refresh path). `explorerClassifyLink()` classifies fragment / external / mailto / unsupported / relative links; `explorerResolveRelativePath()` resolves a relative link against the current file and rejects `..` traversal above the Explorer root plus drive-letter/scheme paths. Fragment-only links scroll to the heading via `explorerScrollPreviewToHeading()`; relative links open a deduplicated pinned tab through the shared primitive (`openExplorerFile(..., { pinned: true })`) and carry their fragment to scroll the destination; external `http(s)` links open isolated via `window.open(..., 'noopener,noreferrer')` and never navigate the session page away, while `mailto:` uses the default handler and unsupported schemes are ignored. Covered by `test_terminals_page_explorer_markdown_links_open_tabs` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-015
- Title: Persist open explorer tabs in saved sessions
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/js/launcher.js`, `web/saved_sessions.py`, `sessions/manager.py`, `web/runtime_state.py`, `tests/test_api.py`, `tests/test_session_manager.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `session`, `launcher`, `settings`, `tests`
- Reported: 2026-07-12
- Closed: 2026-07-16

Description:
The tabbed explorer viewer tracked in ISSUE-2026-014 also needs to participate in saved-session persistence. Without explicit tab state in the saved preset, users who save a configured workspace from the active-session Save Session dropdown or resave it through launcher settings would lose their open reference files and active explorer tab when they relaunch that session.

Steps to reproduce:
1. Open a session containing an Explorer pane and, once the ISSUE-2026-014 tabbed viewer is available, open multiple files as pinned tabs and select one of them.
2. Save the workspace through the active-session Save Session dropdown, or open the preset in the launcher and save its settings again.
3. Relaunch the saved session and observe that the preset schema has no explorer-tab state from which to restore the previously open files and active tab.

Expected behavior:
Both saved-session workflows should retain each Explorer pane's open file tabs, their stable order, and the active tab. Relaunching the preset should safely restore readable tabs under the pane's current explorer root while preserving the permanent dynamic-preview behavior defined by ISSUE-2026-014.

Actual behavior / logs:
Code inspection confirms `buildWorkspaceTerminalEntry()` in `web/static/js/terminals.js` serializes an Explorer pane's directory plus `explorer_tree_open` and `explorer_git_open`, but no open-file or active-tab state. `collectFormConfig()` in `web/static/js/launcher.js` forwards terminal drafts and geometry only, while `_normalize_terminal_entries()` and `_normalize_workspace_layout()` in `web/api.py` have no fields for explorer tabs. `TerminalSession` in `sessions/manager.py` likewise retains explorer root/sidebar state but cannot carry tab metadata into a relaunched workspace. ISSUE-2026-014 defines the new tab model but does not cover saved-session round-tripping.

Resolution:
Two bounded fields — `explorer_open_tabs` (ordered pinned paths) and `explorer_active_tab` — now thread end to end. `explorerSerializeTabs()` / `buildWorkspaceTerminalEntry()` capture them for active-workspace saves; `web/static/js/launcher.js` carries them invisibly through the terminal row dataset (`data-explorer-open-tabs` + `parseExplorerOpenTabsDataset()`) so resaving a preset preserves them without the launcher editing tabs. `web/saved_sessions.py` validates them (`_normalize_explorer_open_tabs` / `_normalize_explorer_active_tab`: forward-slash normalization, `..`/drive/scheme rejection, dedup, `EXPLORER_MAX_OPEN_TABS` cap, active-tab-must-be-open) in `_normalize_terminal_entries` and gates them to explorer panes in `_merge_workspace_session_config`. `TerminalSession` (`sessions/manager.py`) and the runtime-state snapshot (`web/runtime_state.py`) carry them into a relaunched workspace, where `restoreExplorerPersistedTabs()` reopens readable tabs and activates the saved one — missing/out-of-root paths are ignored without failing launch, and no file contents or writable state are persisted. Covered by `test_workspace_save_round_trips_explorer_open_tabs`, `test_normalize_terminal_entries_*`, and `test_terminals_page_explorer_persists_open_tabs` / `test_launcher_round_trips_explorer_open_tabs` in `tests/test_api.py`, plus `test_create_sessions_carries_explorer_open_tabs` in `tests/test_session_manager.py`.

### Issue ID: ISSUE-2026-028
- Title: Explorer file tree has no right-click "Copy path" context menu
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `tests`
- Reported: 2026-07-15
- Closed: 2026-07-16

Description:
The Explorer Files tree and Git file rows provide no right-click context menu, so there is no built-in way to copy a file's path from the tree. Users who want a file's absolute or repository-relative path must retype it or derive it manually, which is slow for deep trees and inconsistent with the copy affordances already available in the terminal.

Steps to reproduce:
1. Open an Explorer pane and expand the Files tree (and open the Git sidebar).
2. Right-click a file row in the tree or a changed-file row in the Git sidebar.
3. Observe that only the browser's native context menu appears; there is no GridVibe "Copy path" action.

Expected behavior:
Right-clicking a Files-tree row (and ideally a Git file row) should open an in-page GridVibe context menu offering at least "Copy path" (absolute) and, where meaningful, "Copy relative path". Selecting an entry should copy the value to the clipboard with a graceful fallback, using a theme-aware, keyboard-accessible menu that dismisses on outside click or Escape, while preserving the read-only guarantee for local explorer panes.

Actual behavior / logs:
Code inspection confirms the only `contextmenu` listener in `web/static/js/terminals.js` is attached to the xterm `term.element` (for terminal copy/paste); the explorer tree and Git rows install no `contextmenu` handler and expose no copy-path action. A clipboard helper already exists (`navigator.clipboard.writeText(...)` with a `_copyTextFallback(text)` path used for terminal copy), but it is not reused for explorer paths.

### Proposed solution:
Add a delegated `contextmenu` handler on the explorer tree and Git-sidebar containers in `web/static/js/terminals.js` that renders an in-page context menu (not `window.prompt`/`confirm`/`alert`, which WebView2 blocks) with Copy path and Copy relative path actions. Resolve absolute and root-relative paths from the existing row dataset, reuse the current clipboard helper plus `_copyTextFallback`, and keep the menu theme-token-styled in `web/static/css/terminals.css`, keyboard-navigable, and dismissible on outside click/Escape. Preserve local/SSH parity and the read-only contract (copy is a read). Add focused rendered-template and behavioral tests in `tests/test_api.py` for menu wiring, absolute/relative path values, keyboard/dismiss behavior, and clipboard fallback.

Resolution:
A delegated `contextmenu` handler (`wireExplorerCopyPathMenu`) on the file-tree and Git panels now opens an in-page `#explorer-ctx-menu` (no `window.prompt/confirm/alert`; WebView2-safe) with **Copy path** (absolute) and **Copy relative path** actions. Rows carry `data-explorer-copy-path`; the absolute path is derived by joining the relative path against the pane's explorer root using the root's separator style, and both actions reuse the existing `_copyText` clipboard helper. The menu is theme-token styled, keyboard navigable (Arrow keys), and dismisses on Escape or outside click, staying within the read-only contract (copy is a read). Covered by `test_terminals_page_explorer_copy_path_menu_is_present` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-024
- Title: File and Git trees lack file type icons
- Priority: Cosmetic
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `git`, `tests`
- Reported: 2026-07-14
- Closed: 2026-07-16

Description:
The Explorer Files tree and Git sidebar do not show recognizable file type icons before filenames. Files in the Files tree all use the same generic document icon, while changed and committed file rows in the Git sidebar have no file icon at all. Compared with the extension-aware leading icons in the provided reference, this makes mixed Python, JavaScript, JSON, Markdown, configuration, and other file lists slower to scan visually.

Steps to reproduce:
1. Open an Explorer pane on a repository containing several recognizable file types, such as `.py`, `.js`, `.json`, `.md`, and configuration files.
2. Expand the Files tree, then open the Git sidebar and inspect changed files or files listed beneath a commit.
3. Observe that Files tree rows share one generic file icon and Git file rows show only Git status plus the filename, without a file type icon before the name.

Expected behavior:
Both the Files tree and every Git file list should place a compact, extension- or filename-aware file type icon immediately before each filename, with a consistent fallback icon for unknown types. Folder icons, Git status badges, indentation, action buttons, and accessible filenames should remain intact.

Actual behavior / logs:
Code inspection confirms `explorerTreeRowHtml()` renders every non-directory entry with the single `EXPLORER_FILE_ICON` constant. `renderExplorerGitFileRows()` renders a Git status badge followed directly by `.explorer-diff-commit-file-path` and does not insert any file icon. GridVibe already has `EXPLORER_LANGUAGE_BY_EXTENSION`, `EXPLORER_LANGUAGE_BY_FILENAME`, and `explorerCodeLanguage()` for classifying common file types, but that classification is not reused by either tree to select an icon. Existing tests verify language detection and generic Explorer/Git rendering hooks but do not cover type-aware icons or consistent placement before filenames.

### Proposed solution:
Add one shared client-side helper in `web/static/js/terminals.js`, such as `explorerFileTypeIconHtml(path)`, that reuses the existing filename and extension classification and returns a stable icon key with a generic-file fallback. Render its output before `.explorer-tree-name` in `explorerTreeRowHtml()` and before `.explorer-diff-commit-file-path` in `renderExplorerGitFileRows()`, including staged, unstaged, conflicted, untracked, deleted, renamed, and commit-history rows. Use locally defined or vendored stroke-style SVG assets with `currentColor`, theme-token-based colors, `aria-hidden="true"`, and no CDN, emoji, or text-glyph dependency; preserve the existing folder icon for directories. Add compact alignment and sizing rules in `web/static/css/terminals.css` so icons do not disrupt tree indentation, Git status badges, ellipsis behavior, or narrow sidebars. Add focused rendered-template tests in `tests/test_api.py` for representative extensions and special filenames, unknown fallbacks, both tree renderers, placement before filenames, accessibility, local/SSH parity, and action-button layout.

Resolution:
Added a shared `explorerFileTypeIconHtml(path)` in `web/static/js/terminals.js` that reuses the existing `explorerCodeLanguage()`/`normalizeExplorerLanguage()` classifiers to map each file to one of ten categories (`code`, `shell`, `data`, `markup`, `style`, `markdown`, `config`, `sql`, `log`, `doc`) and returns a distinct stroke-style `currentColor` SVG glyph with a generic `doc` fallback and `aria-hidden="true"`. It renders before the name in `explorerTreeRowHtml()`, `renderExplorerGitFileRows()` (staged, unstaged, and commit-history rows) and `explorerDirectoryRowHtml()`; folder rows keep `EXPLORER_FOLDER_ICON`. Per-category tints are new `--explorer-icon-*` variables defined once per theme in `web/static/css/terminals.css` (same pattern as `--git-lane-*`, no inline palette literals), and Git rows gained a dedicated icon grid column. The now-unused `EXPLORER_FILE_ICON` constant was removed. Covered by `test_terminals_page_explorer_file_type_icons_are_present` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-023
- Title: Git change rows open source instead of diff view
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `git`, `tests`
- Reported: 2026-07-14
- Closed: 2026-07-16

Description:
Clicking a file under either `Changes` or `Staged Changes` in the Explorer Git sidebar opens the file in its normal source view instead of taking the user directly to the available diff view. This adds an unnecessary navigation step to the primary Git review workflow and makes the changed-file rows behave differently from commit-history file rows, which already open directly into a diff.

Steps to reproduce:
1. Open an Explorer pane rooted in a Git repository and create at least one unstaged tracked change and one staged change.
2. Open the Git sidebar and click a filename under `Changes`, then repeat with a filename under `Staged Changes`.
3. Observe that each file opens with Source selected and requires a second click on Diff before its changes are shown.

Expected behavior:
Clicking a filename under `Changes` or `Staged Changes` should open that file with Diff selected and immediately load the appropriate Git diff. The existing Stage, Unstage, and Open containing folder controls should keep their current independent actions.

Actual behavior / logs:
Code inspection confirms `renderExplorerGitFileRows()` gives non-commit changed-file rows a `data-explorer-git-open-file` action. Its click handler calls `explorerGitOpenFile()`, which invokes `openExplorerFile(index, path)` without the supported `openDiff` option. `renderExplorerFile()` therefore selects `source` as the initial file view; it selects `diff` only when `openDiff` or `diffCommit` is supplied. By contrast, `explorerGitOpenCommitDiff()` already passes `openDiff: true` for commit-history files. Existing rendered-template tests verify that the Git sidebar and diff controls are present but do not assert the initial view selected after clicking staged or unstaged rows.

### Proposed solution:
Update the changed-file click path in `web/static/js/terminals.js` to call the shared `openExplorerFile()` flow with `openDiff: true`. Preserve which section originated the click so tracked files under `Changes` can request the worktree diff and files under `Staged Changes` can request the staged diff through the existing Git diff modes; this avoids showing unrelated hunks when one file has both staged and unstaged changes. Keep event isolation for Stage, Unstage, and Open containing folder buttons, and define graceful behavior for untracked, deleted, renamed, conflicted, and diff-load failure cases without leaving the file viewer unusable. Add focused tests in `tests/test_api.py` for staged, unstaged, and partially staged rows, initial Diff selection, the requested diff mode, commit-history behavior, action-button isolation, and fallback/error handling.

Resolution:
`explorerGitOpenFile()` now opens changed-file rows straight into the diff view with `openDiff: true` and a section-specific `diffMode` — `worktree` for rows under **Changes**, `staged` for rows under **Staged Changes** — passed via `data-explorer-git-diff-mode`. The mode is threaded through `openExplorerFile()` → `renderExplorerFile()` (`pane._explorerDiffMode`) → `loadExplorerDiff()`, whose cache key and `?mode=` query now include it, so a partially staged file never shows the other section's hunks. Commit-history rows keep their existing commit-diff path, and Stage/Unstage/Open-folder buttons keep their isolated actions. Covered by `test_explorer_git_diff_distinguishes_worktree_and_staged` and `test_terminals_page_explorer_git_rows_open_diff_view` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-018
- Title: Add a revert action for unstaged Git changes
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `web/api.py`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `git`, `flask`, `tests`
- Reported: 2026-07-13
- Closed: 2026-07-16

Description:
The Explorer Git sidebar lists unstaged changes but does not provide a way to discard a file's working-tree changes. Users who review an unwanted edit in GridVibe must leave the workspace and run a Git command elsewhere, interrupting the built-in review and staging flow.

Steps to reproduce:
1. Open an Explorer pane rooted in a Git repository and modify a tracked file without staging the latest edit.
2. Open the Git sidebar and locate the file under `Changes`.
3. Observe that the row offers Stage and Open containing folder actions, but no Revert or Discard action.

Expected behavior:
Each eligible tracked file under `Changes` should expose a clearly labeled Revert action. After an explicit confirmation, GridVibe should discard only that file's unstaged working-tree changes, preserve any already staged version of the file, refresh the Git summary and open diff, and report failures without hiding the existing changes. Untracked and conflicted files must not be silently deleted or overwritten; they should either omit the action or use a distinct, strongly confirmed workflow.

Actual behavior / logs:
Code inspection confirms `renderExplorerGitFileRows()` in `web/static/js/terminals.js` renders a `+` Stage button for rows under `Changes` and an Open containing folder button, while the staged section receives a separate Unstage action. The client has `explorerGitStageFile()` and `explorerGitUnstageFile()`, and `web/api.py` exposes stage and unstage routes, but there is no revert/discard/restore control, handler, or API route in `web/static/js/terminals.js`, `web/api.py`, or `web/explorer.py`.

### Proposed solution:
Add an accessible Revert button to eligible tracked rows rendered by `renderExplorerGitFileRows()` and require a confirmation that names the affected path and explains that unstaged edits will be lost. Add a narrowly scoped Explorer Git API route in `web/api.py` backed by a helper in `web/explorer.py` that reuses the existing root/path validation and performs the equivalent of `git restore --worktree -- <pathspec>`, so partially staged files revert to their index version without altering staged content. Return an updated repository summary and refresh any active file/diff view after success. Disable or omit the action for conflicts and untracked files unless a separate deletion workflow with stronger confirmation is deliberately added. Preserve local/SSH parity and add focused tests in `tests/test_api.py` for modified and deleted tracked files, partially staged files, unsafe/out-of-root paths, conflicts, untracked files, cancellation, Git failures, refreshed status, and rendered control wiring.

Resolution:
Added `_git_revert_path()` in `web/explorer.py` (equivalent to `git restore --worktree -- <path>`, run with `GIT_TERMINAL_PROMPT=0`) behind a new `POST /api/explorer/<id>/git/revert` route in `web/api.py`. It restores the worktree copy from the index so any staged version is preserved, and refuses untracked, conflicted, and no-unstaged-change paths (parsing porcelain status without stripping the leading XY columns). The Git sidebar renders a Revert button on eligible tracked rows under **Changes** only (`explorerGitCanRevert` → modified/deleted/renamed), which routes through a new reusable in-page confirm shell (`openGenericConfirmModal` / `#genericConfirmModal`) before calling the route and reloading the open file/diff. Covered by the `test_explorer_git_revert_*` behavioral tests and `test_terminals_page_explorer_git_revert_controls_are_present` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-021
- Title: Appearance theme changes do not reach open session windows
- Priority: Medium
- Status: Closed
- Area: `web/static/js/shared.js`, `web/static/js/launcher.js`, `web/static/js/terminals.js`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `settings`, `launcher`, `session`, `ui`, `theme`, `tests`
- Reported: 2026-07-13
- Closed: 2026-07-14

Description:
Changing the Appearance theme to Light or Dark in launcher settings updates the launcher but does not update an already-open session window. The application can therefore display mismatched themes across its two primary windows until the session page is reloaded or its theme is changed separately, making the global appearance setting behave like a launcher-only preference.

Steps to reproduce:
1. Start GridVibe with the launcher and at least one session window open.
2. In launcher App Settings, change Appearance from Dark to Light or from Light to Dark and save.
3. Observe that the launcher applies the selected theme while the open session window remains on its previous theme.

Expected behavior:
The saved Light, Dark, or System appearance preference should be global to GridVibe. Saving it from the launcher should immediately apply the resolved theme to the launcher and every open session window, and newly opened or reconnected session windows should initialize from the same persisted preference. This content-theme synchronization is distinct from the intentionally dark native Windows title-bar policy documented by closed ISSUE-2026-009.

Actual behavior / logs:
Code inspection confirmed `saveAppSettings()` in `web/static/js/launcher.js` applied the returned theme locally through `applyAppSettings(data)`, then called `notifyAppConfigUpdated(data)` with a cross-window payload containing only `workspace.surface_mode`, not `appearance.theme`. The backend `_broadcast_app_config_update()` in `web/api.py` likewise emitted only `workspace.surface_mode`, and all BroadcastChannel, storage-event, and Socket.IO handlers in `web/static/js/terminals.js` called `applyAppConfigSurfaceMode()` without applying a theme. Shared `applyTheme()` wrote `gridvibe.theme` to local storage, but `initTheme()` registered no storage listener, so an already-open session document did not react to the launcher write.

### Proposed solution:
Define one normalized app-config update contract containing `appearance.theme` alongside `workspace.surface_mode`, and emit it consistently from both `notifyAppConfigUpdated()` and `_broadcast_app_config_update()`. Replace or extend the session-side surface-only handler with an idempotent app-config handler that calls `applyTheme()` for a valid `system`, `light`, or `dark` preference and continues applying surface mode. Keep BroadcastChannel, storage-event, and Socket.IO delivery paths aligned, avoid rebroadcast or local-storage feedback loops, and make duplicate deliveries harmless. On session-window focus, visibility restoration, or socket reconnect, reconcile against `/api/app-config` or the authoritative stored preference so updates missed while disconnected are recovered. Preserve System-mode media-query behavior and the native-title-bar policy from ISSUE-2026-009.

Resolution:
`notifyAppConfigUpdated()` in `web/static/js/launcher.js` and `_broadcast_app_config_update()` in `web/api.py` now emit one normalized payload carrying `appearance.theme` alongside `workspace.surface_mode`. `web/static/js/terminals.js` routes all three delivery paths (BroadcastChannel, storage event, Socket.IO `app_config_updated`) through a new `applyAppConfigUpdate()` handler whose theme step validates against `system`/`light`/`dark` and skips already-applied preferences, so duplicate or malformed deliveries are harmless; the surface-mode step is unchanged. Session windows additionally reconcile the theme against `/api/app-config` on socket reconnect and on window `focus`/`pageshow`, recovering updates missed while disconnected, and `initTheme()` in `web/static/js/shared.js` now applies cross-window `gridvibe.theme` storage writes (loop-safe: an unchanged `setItem` fires no storage event and an already-applied guard skips redundant work). System-mode media-query behavior and the permanently dark native title bar from ISSUE-2026-009 are preserved. Covered by `ThemeSyncTestCase` in `tests/test_api.py` (backend payload, launcher notification contents, all three session-side delivery paths, validation/idempotence, reconnect/focus/pageshow reconciliation, and the shared storage listener).

### Issue ID: ISSUE-2026-019
- Title: Add floating waveform indicator while voice input is recording
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `voice`, `ui`, `terminal`, `accessibility`, `tests`
- Reported: 2026-07-13
- Closed: 2026-07-14

Description:
Voice capture does not provide a prominent workspace-level indication that the microphone is actively recording. The only visual feedback is a small state change on the microphone button in the active terminal header, which can be easy to miss when recording is triggered by a push-to-talk keybind or when attention is elsewhere in the workspace. Users therefore cannot confidently tell at a glance whether GridVibe is listening.

Steps to reproduce:
1. Enable voice input, configure push-to-talk and a keybind, and open a terminal session.
2. Start voice capture from the terminal microphone control, or press and hold the configured push-to-talk keybind.
3. Observe that the microphone button receives a small red pulse while recording, but no floating waveform or other prominent recording indicator appears over the workspace.

Expected behavior:
Whenever microphone capture is active, whether initiated by a press-and-hold GUI microphone interaction or the configured push-to-talk keybind, GridVibe should show a compact floating waveform-style indicator in a consistent, unobtrusive workspace position. It should appear only after capture actually starts, remain visible for the full recording, clearly identify the recording state, and disappear promptly on release, stop, cancellation, backend error, or teardown.

Actual behavior / logs:
Code inspection confirmed `_updateVoiceBtn()` in `web/static/js/terminals.js` only toggled the `recording` class and title on the per-terminal microphone button, rendered by `web/static/css/terminals.css` as a red button with a small pulsing box shadow. The push-to-talk listeners called `_startVoice()` on matching `keydown` and `_stopVoice()` on `keyup`, but neither path created or synchronized a page-level recording overlay or waveform, and the GUI microphone control was wired as click-to-toggle only.

### Proposed solution:
Add one reusable, fixed-position voice-recording overlay managed by the shared capture lifecycle in `web/static/js/terminals.js` and styled in `web/static/css/terminals.css`. Show it only once `_startVoice()` has established an active recording state, and hide it from every `_stopVoice()`, permission-denial, backend-error, session-switch, disconnect, and teardown path. Render a clear microphone/`Recording` label plus animated waveform bars; where practical, drive the bars from a bounded `AnalyserNode` attached to the existing audio graph, with a lightweight deterministic animation as a fallback. Make the overlay non-blocking, theme-aware, responsive, and accessible with status semantics, sufficient contrast, and a `prefers-reduced-motion` treatment. Add pointer down/up/cancel handling for the requested hold-to-talk GUI interaction while preserving accessible keyboard activation and defining how it coexists with the existing click-to-toggle behavior. Ensure the single-active-terminal rule prevents duplicate overlays and that rapid key or pointer release during asynchronous startup cannot leave a stale indicator.

Resolution:
`web/static/js/terminals.js` now manages a single fixed-position `#voiceRecordingOverlay` (`role="status"`, `aria-live="polite"`, mic SVG + `Recording` label + five waveform bars) from the shared capture lifecycle: `_showVoiceRecordingOverlay()` runs only after `_startVoice()` has stored an active recording state (a guard skips the overlay if a rapid release already tore that state down), and `_hideVoiceRecordingOverlay()` runs from `_stopVoice()` — which every stop path funnels through: mic toggle, push-to-talk release, hold release, `voice_status` backend errors, and `_stopAllVoice()` on session switch and grid teardown — plus the `_startVoice()` failure path (permission denial, pipeline errors). The bars are driven by a bounded `AnalyserNode` (fftSize 64) fanned out from the live capture source with a stale-loop token guard, falling back to a deterministic CSS animation when no analyser is available and staying static under `prefers-reduced-motion`. The mic button gained pointer down/up/cancel hold-to-talk (350 ms threshold, pointer capture, and capture-phase click suppression so a completed hold cannot re-toggle) coexisting with the existing click-to-toggle and keyboard activation, and both the hold and the push-to-talk keybind now request a deferred stop when released during asynchronous startup, so no stale capture or indicator survives. Styles in `web/static/css/terminals.css` are theme-aware (`--gv-danger`, `--t-voice-bg`) and non-blocking (`pointer-events: none`). Covered by `VoiceRecordingOverlayTestCase` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-012
- Title: Session tab switching resets terminal scroll positions
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `session`, `ui`, `tests`
- Reported: 2026-07-11
- Closed: 2026-07-11

Description:
Switching between session tabs scrolls every terminal view to the top instead of preserving each pane's current viewport. This disrupts terminal and agent workflows because users lose the output location they were reading whenever they inspect another session and return.

Steps to reproduce:
1. Open GridVibe with at least two session tabs and one or more terminals containing enough output to scroll.
2. Scroll one or more terminal panes away from the top, then switch to another session tab.
3. Switch back and observe that the terminal viewports have moved to the top rather than remaining at their previous positions.

Expected behavior:
Session tab switching should not change any terminal pane's scrollbar or xterm viewport position. Each pane should return at the exact line it displayed before the tab switch, including panes following the live bottom of the buffer.

Actual behavior / logs:
User report: all terminal views scroll to the top whenever session tabs are swapped, which also breaks the visual context of agent terminals. Code inspection shows `templates/terminals.html` already attempts to cache `buffer.active.viewportY` in `captureTerminalViewportState()` before detaching a group and restore it through `restoreTerminalViewportState()` after reattaching the cached group. The reported behavior confirms this preservation path is ineffective in the current tab-switch lifecycle; the exact overwrite point among cached DOM restoration, xterm redraw, fit, resize observation, and deferred scroll restoration remains an investigation target.

### Proposed solution:
Make viewport preservation an invariant of `cacheVisibleGroupView()`, `restoreCachedGroupView()`, and `initialLoad()` in `templates/terminals.html`. Capture every terminal's active-buffer viewport immediately before the visible group is detached, distinguish exact scrolled positions from intentional bottom-following state, and restore only after all DOM reattachment, fit/redraw, and resize work that can alter xterm's viewport has completed. Guard delayed callbacks so a stale group switch cannot overwrite a newer viewport. Add focused regression coverage in `tests/test_api.py` for multiple panes, repeated tab swaps, top/middle/bottom positions, panes receiving output while hidden, and rapid switching.

Resolution:
`templates/terminals.html` now retains each cached terminal viewport until the cached session group has been reattached and its xterm redraw/fit cycle has completed. Restoration explicitly returns bottom-following panes to the live buffer bottom, restores other panes to their captured `viewportY`, and checks the active load token before every immediate or delayed scroll so stale rapid-switch callbacks cannot move the newly active group. `tests/test_api.py` verifies that cached viewport state survives until after redraw, covers both bottom and exact-line restoration paths, and confirms stale-switch guards are present.

### Issue ID: ISSUE-2026-011
- Title: Add refresh control to explorer toolbar
- Priority: Low
- Status: Closed
- Area: `templates/terminals.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `terminal`, `ui`, `tests`
- Reported: 2026-07-11
- Closed: 2026-07-11

Description:
Explorer mode needs a dedicated refresh icon in its own navigation toolbar. Although terminal pane headers expose a refresh action on the right and the existing refresh flow can reload explorer content, the explorer bar itself starts with the parent-directory up-arrow, so refreshing the current directory or open file is not available alongside the explorer navigation controls.

Steps to reproduce:
1. Open a GridVibe pane in explorer mode.
2. Inspect the explorer navigation bar above the directory listing or open file.
3. Observe that the leftmost control is the up-arrow and there is no refresh icon immediately before it.

Expected behavior:
The explorer bar should show an icon-only refresh button on the far left, immediately before the up-arrow. Activating it should refresh the current directory or currently open file through the existing explorer refresh behavior while preserving applicable file scroll state and keeping the explorer read-only.

Actual behavior / logs:
Code inspection confirms both explorer toolbar render paths in `templates/terminals.html` begin with the `explorer-up` button, followed by tree and Git controls, with no explorer-toolbar refresh button. The pane-header `data-terminal-refresh` action exists separately, and `refreshTerminalDisplay()` already delegates explorer sessions to `refreshExplorerPane()`, which refreshes the open file with scroll preservation or force-reloads the current directory.

### Proposed solution:
Add a compact, accessible refresh-icon button to both explorer toolbar construction paths in `templates/terminals.html`, positioned immediately before `explorer-up`. Wire it to the existing `refreshTerminalDisplay(index)` or `refreshExplorerPane(index)` lifecycle so loading/disabled state cannot trigger overlapping refreshes, and keep directory, file, Git-sidebar, tree-sidebar, and scroll-preservation behavior consistent with the existing refresh action. Add focused rendered-template tests in `tests/test_api.py` for button placement, icon-only accessible labeling, dynamic mode-switch rendering, event wiring, and refresh behavior for directory and file views.

Resolution:
`templates/terminals.html` now renders an accessible icon-only refresh control immediately before the up-arrow in both initially created and dynamically mode-switched explorer toolbars. The control reuses `refreshTerminalDisplay()` and the existing explorer refresh lifecycle, shares busy/disabled state with the pane-header actions, preserves open-file scroll state, and refreshes active Tree and Git sidebars. `tests/test_api.py` verifies placement in both render paths, accessible labeling, event wiring, and shared busy-state handling.

### Issue ID: ISSUE-2026-009
- Title: Native title bar theme waits for refocus to repaint
- Priority: Cosmetic
- Status: Closed
- Area: `web/webview_launcher.py`, `templates/index.html`, `templates/terminals.html`, `tests/test_webview_launcher.py`
- Assignee: Unassigned
- Tags: `launcher`, `session`, `windows`, `ui`, `theme`, `tests`
- Reported: 2026-07-04
- Closed: 2026-07-04

Description:
In native pywebview mode, the resizable Windows title bar on both the launcher and session windows can lag behind GridVibe's light/dark theme switch. The app content and title text update to the selected theme, but the native frame background can remain in the previous black or white color until the user clicks away from the window or refocuses it. The practical impact is a visibly mismatched title bar during normal theme toggling, with a manual blur/refocus workaround.

Steps to reproduce:
1. Start GridVibe in native pywebview mode on Windows so the launcher or session window uses the resizable native frame.
2. Toggle GridVibe between light and dark display modes from the launcher window or the sessions window.
3. Observe the native top bar while the window remains focused, then click away from the window or refocus it.

Expected behavior:
The native window title bar background, border, and title text should remain dark regardless of the resolved GridVibe content theme.

Actual behavior / logs:
User report: switching light/dark mode updates the window title area text color, but the native frame top-bar background stays in the previous black or white box until blur/refocus. Code inspection confirmed both `templates/index.html` and `templates/terminals.html` call `bridge.set_native_theme(resolvedTheme)` during theme changes, and `web/webview_launcher.py` stored `_native_theme` before applying Windows DWM caption, text, and border attributes to the registered launcher and session windows.

### Proposed solution:
Keep launcher and session window handling shared through `GridVibeApi.set_native_theme()`, but permanently resolve native frame requests to the dark Windows frame theme instead of swapping DWM attributes for light mode.

Resolution:
`web/webview_launcher.py` now keeps `_native_theme` as `dark` for all `set_native_theme()` calls, patches pywebview's WinForms title-bar theme hook to stay dark during form construction and OS theme-change events, and applies the dark DWM frame attributes again before each native window is shown. `tests/test_webview_launcher.py` verifies that light-mode requests still apply the dark native frame to both registered windows.

### Issue ID: ISSUE-2026-010
- Title: Voice setting changes did not reach open session windows
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `voice`, `settings`, `session`, `ui`, `tests`
- Reported: 2026-07-04
- Closed: 2026-07-04

Description:
Changing launcher voice settings while session windows remained open did not update the open terminal UI. The practical impact was that enabling voice input could leave existing or relaunched session windows without the microphone control until the user fully closed the session group and launched a fresh one.

Steps to reproduce:
1. Launch GridVibe sessions with voice input disabled in app settings.
2. Leave the session window open, return to settings, and enable voice input.
3. Relaunch or return to the existing session window and inspect the terminal pane header.

Expected behavior:
Open session windows should reflect the current voice setting after the setting is saved and the session window regains focus or refreshes its live voice status. Enabling voice should make the microphone control available without requiring the session group to be fully closed and recreated.

Actual behavior / logs:
User report: after changing voice settings, the microphone icon appeared only after a full session close and fresh launch; relaunching while sessions stayed open did not update the visible controls. Code inspection confirmed `web/api.py` refreshed runtime voice globals after `/api/app-config` saves and `/api/voice-status` exposed the live `enabled` state, but `templates/terminals.html` rendered the voice control inside a server-side `{% if voice_enabled %}` block. When a terminal document was first rendered with voice disabled, there was no client-side microphone control to reveal after `/api/voice-status` later reported voice enabled.

### Proposed solution:
Keep the voice control markup available in `templates/terminals.html` regardless of the initial server-rendered voice flag, then hide or disable it from client-side live voice status. Refresh `/api/voice-status` when the session window regains focus or is shown from page cache, and synchronize all existing terminal voice controls after the status payload changes. Add focused template regression coverage in `tests/test_api.py` for pages initially rendered with voice disabled.

Resolution:
`templates/terminals.html` now always emits the per-terminal voice control, hides it when `_voiceServiceStatus.enabled` is false, updates visibility and disabled state after live `/api/voice-status` refreshes, and refreshes voice status on window `focus` and `pageshow`. `tests/test_api.py` now verifies that the terminals page keeps voice controls available for live setting refresh even when the initial rendered voice setting is disabled.

### Issue ID: ISSUE-2026-007
- Title: Opening binary files can freeze explorer mode
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `flask`, `tests`
- Reported: 2026-07-02
- Closed: 2026-07-03

Description:
Opening a binary file from explorer mode can make the explorer pane appear frozen. The user-facing impact is that a normal file-list click can leave the pane stuck or unresponsive instead of quickly declining to preview unsupported binary content and returning the user to a usable explorer state.

Steps to reproduce:
1. Open GridVibe in explorer mode for a directory containing a binary file such as an image, archive, executable, or generated data file.
2. Click the binary file in the explorer list.
3. Observe that the explorer pane can freeze or remain stuck while trying to open the unsupported file.

Expected behavior:
Binary files should be detected quickly and handled as unsupported previews. Explorer mode should show a clear, non-blocking message, keep navigation controls usable, and avoid decoding, highlighting, or rendering binary content as source text.

Actual behavior / logs:
User report: opening a binary causes explorer mode to freeze. Code inspection shows `openExplorerFile()` in `templates/terminals.html` immediately replaces the file list with `Opening file...` before fetching `/api/explorer/<session_id>/file`. The backend in `web/api.py` reads up to `EXPLORER_FILE_PREVIEW_MAX_BYTES + 1`, currently 1 MiB plus one byte, and only rejects content containing `NUL` bytes with `Explorer file appears to be binary`. Existing coverage in `tests/test_api.py` verifies the API rejects a small `abc\\x00def` file, but there is no client-side regression coverage for keeping explorer navigation responsive after a binary open attempt, and binary-like files without `NUL` bytes can still be decoded with replacement characters and routed into the normal source/highlighting path.

### Proposed solution:
Harden binary preview handling in both `web/api.py` and `templates/terminals.html`. Add faster binary detection before the full preview/render path, including extension or MIME hints where available plus byte-sample heuristics beyond only checking for `NUL`. Return a structured unsupported-preview response or a stable 400 error that the client can display without losing the previous directory context. Update `openExplorerFile()` so failed binary opens restore or preserve the directory listing/navigation state instead of leaving only a transient loading/message view, and ensure large local and SSH binaries cannot block the UI thread. Add tests in `tests/test_api.py` for non-NUL binary-like bytes, large binary files, remote binary errors, and client-template behavior that preserves a usable explorer state after an unsupported file open.

Resolution:
Explorer file editor mode now only accepts known preview/source formats from the existing language and filename maps. Unsupported extensions are rejected before preview decoding, known formats are checked for NUL bytes, invalid UTF-8, and excessive control bytes, and SSH/local paths share the same validation. The frontend keeps the directory listing active on failed opens and prepends a non-blocking error notice instead of leaving the pane stuck on an opening message. Tests cover unsupported local and remote formats, non-NUL binary-like content, and the directory-preserving client path.

### Issue ID: ISSUE-2026-005
- Title: Explorer file find blocks terminal UI on large previews
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `terminal`, `ui`, `tests`
- Reported: 2026-07-02
- Closed: 2026-07-03

Description:
The explorer file find flow could become extremely slow on large text files and make the terminal workspace appear frozen while searching. The practical impact was that users inspecting large logs or source files could temporarily lose terminal responsiveness because the search work ran synchronously in the browser UI thread.

Resolution:
`templates/terminals.html` now debounces file search input, cancels stale pending or in-flight searches, scans source previews in bounded chunks with event-loop yields, caps highlighted matches, reuses cached source ranges when navigating previous/next matches, and clears stale search work when file content or pane mode changes. Preview-mode DOM marking is also capped. `web/api.py` preview truncation behavior remains unchanged. `tests/test_api.py` includes rendered-template coverage for the debounce constants, async chunked search, cancellation token wiring, match cap display, and cached range reuse.

### Issue ID: ISSUE-2026-006
- Title: Add find support to explorer directory view
- Priority: Low
- Status: Closed
- Area: `templates/terminals.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `tests`
- Reported: 2026-07-02
- Closed: 2026-07-03

Description:
Explorer mode exposed find controls only after opening a file. Directory/file-list panes did not provide a find field or `Ctrl+F`/`Cmd+F` target for locating files by name, so users browsing large directories had to visually scan the list or use an external shell command.

Resolution:
`templates/terminals.html` now renders a directory-mode find control in explorer bars, stores directory search state separately from file-content search state, filters the currently loaded directory entries by filename, highlights matching name text, supports previous/next/clear actions, shows an empty-result message, and lets `Ctrl+F`/`Cmd+F` focus active directory explorer panes. File-mode source/preview search remains unchanged and the explorer remains read-only. `tests/test_api.py` includes focused template coverage for the directory search UI, keyboard target selection, state separation, empty results, and filename escaping/highlighting hooks.

### Issue ID: ISSUE-2026-008
- Title: Add active workspace Save Session button
- Priority: Low
- Status: Closed
- Area: `templates/terminals.html`, `templates/index.html`, `web/api.py`, `sessions/manager.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `session`, `launcher`, `ui`, `tests`
- Reported: 2026-07-03
- Closed: 2026-07-03

Description:
The terminal workspace did not provide a Save Session action for the currently selected session group after the user customized it. Users could save launcher form presets before launch, but changes made in the live workspace, such as resized panes, split/window arrangement, terminal or explorer mode changes, and agent CLI setup, were not directly captured as a reusable saved session layout.

Resolution:
`templates/terminals.html` now adds a `Save Session` tab button immediately after `+ New Session`. The workspace save flow serializes the active group into the existing launcher preset schema, including pane count, connection mode, terminal titles, directories, startup modes, explorer-selected directories, shell flags, agent command metadata, and sanitized split/resize geometry. `web/api.py` now normalizes optional `workspace_layout` metadata through the saved-session API, attaches it to launched session groups, and returns it through session-group/session responses so relaunched presets can restore layout geometry. `templates/index.html` preserves imported workspace layout metadata and forwards it when launching saved presets. `sessions/manager.py` now retains runtime agent metadata and group workspace layout metadata. Focused API/template/session-manager tests cover button placement, serialization hooks, geometry roundtrip, launch metadata, and agent metadata preservation.

### Issue ID: ISSUE-2026-002
- Title: Add per-terminal close buttons with neighbor expansion
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `web/api.py`, `sessions/manager.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `session`, `ui`, `tests`
- Reported: 2026-07-02
- Closed: 2026-07-03

Description:
Opened terminal panes did not provide a general per-terminal close action. Users could close an entire session group from the session tab, and split branches exposed a limited "Close this split pane" control, but normal terminal panes across connection modes had no close button that removed only that terminal.

Steps to reproduce:
1. Launch a GridVibe session with two or more terminal panes, using any terminal connection mode such as SSH, WSL, PowerShell/local shell, or agent-backed terminal.
2. Open the `/terminals` page and inspect each terminal pane header.
3. Observe that the pane controls include refresh, mode switch, split/unsplit where eligible, clear, and optional voice controls, but no close button that closes only that terminal pane.

Expected behavior:
Every terminal pane type should expose a close button. Closing a terminal should remove that session, keep the remaining session group open, and assign the freed space to the remaining terminal that shares the longest border with the terminal being closed. If the closed terminal is the last pane in the group, the existing group/window close behavior should apply.

Actual behavior / logs:
Code inspection confirmed `templates/terminals.html` rendered pane header controls for refresh, mode switching, split/unsplit, clear, and optional voice input, but no general `data-terminal-close` control. `renderSessionTabs()` created a close button for whole session groups, and `closeSplitPane()` only closed sessions selected by `buildCloseSplitPlan()` for split branches. The backend already exposed `DELETE /api/sessions/<session_id>` and `SessionManager.close_session()`, but the browser UI did not wire a general per-terminal close flow to that endpoint.

Resolution:
`templates/terminals.html` now adds an icon-only red-outlined `data-terminal-close` button at the end of each terminal pane header, including dynamically split panes, and wires it to `closeTerminalPane(index)`. The handler reuses `DELETE /api/sessions/<session_id>`, computes a safe neighbor-fill plan before deleting, and preserves remaining layout geometry by first selecting a single adjacent pane with the longest shared border when it can absorb the closed rectangle. For split shapes where one pane cannot absorb the rectangle without overlap, it expands the valid neighboring side set into the closed space instead of compacting or replacing the whole layout. Last-pane close follows the existing empty-group/window behavior. `tests/test_api.py` includes template coverage for the close control, red styling, end-of-header order, neighbor side-fill helpers, and API coverage that deleting one session updates the remaining group count.

### Issue ID: ISSUE-2026-003
- Title: Explorer mode reopens stale launch directory after shell cd
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `web/api.py`, `sessions/manager.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `file-explorer`, `session`, `tests`
- Reported: 2026-07-02
- Closed: 2026-07-03

Description:
Explorer-mode terminals could lose the user's live shell location after the user switched back to terminal mode and changed directory outside the original launcher-selected directory. The practical impact was that switching back to explorer/browser mode opened the stale launcher directory instead of the directory where the terminal currently was.

Steps to reproduce:
1. Launch a GridVibe terminal pane in explorer mode with a selected starting directory from the launcher.
2. In explorer mode, navigate into a subdirectory, then switch to terminal mode and confirm the terminal starts in that subdirectory.
3. Switch back to explorer mode and confirm the explorer still opens at the subdirectory.
4. Switch back to terminal mode, run `cd ..` until the shell is outside the original launcher-selected directory, then switch back to explorer/browser mode.

Expected behavior:
Switching from terminal mode to explorer/browser mode should open the explorer at the terminal's current working directory. If that directory is outside the previous explorer root, the explorer root should move to the current working directory or another safe root that still displays the current location.

Actual behavior / logs:
User report: after leaving the original launcher-selected directory with `cd ..`, switching back to explorer/browser mode opened the original launcher directory instead of the terminal's current directory. Code inspection showed `switchSessionPaneMode()` in `templates/terminals.html` sent `terminal._session.directory` when switching from terminal to explorer, and that value was session metadata rather than the live shell cwd.

Resolution:
`templates/terminals.html` now sends a `refresh_cwd` hint instead of cached launch metadata when switching a terminal pane into explorer mode. `web/api.py` handles that hint by probing the active terminal shell for its current working directory before closing the connection, then reuses the existing explorer root reset behavior when the refreshed cwd sits outside the previous root. The probe supports SSH/POSIX shells, WSL/POSIX shells, PowerShell, and cmd-style local sessions, with stale session metadata as a fallback if the shell does not answer quickly. `tests/test_api.py` includes template, local, and SSH regression coverage.

### Issue ID: ISSUE-2026-001
- Title: Terminal resize hover handle spans unrelated panes
- Priority: Medium
- Status: Closed
- Area: `templates/terminals.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `ui`, `resize`, `tests`
- Reported: 2026-07-01
- Closed: 2026-07-03

Description:
The terminal grid resize highlight and mouse hit area can extend across the entire terminal grid even when the divider only belongs to a smaller split region. In nested or uneven layouts, hovering near a border can highlight or activate a resize handle associated with a different terminal span, making the visible affordance misleading and increasing the chance of resizing the wrong panes.

Steps to reproduce:
1. Open GridVibe with a multi-pane terminal group and split or arrange panes so at least one terminal occupies only part of the screen width or height.
2. Move the mouse over a divider line near a half-screen terminal or nested split boundary.
3. Observe that the resize highlight/hit area can continue across unrelated panes, and a border belonging to a different terminal span can highlight.

Expected behavior:
Resize handles should be scoped to the shared edge between the panes they resize. Hover highlights and pointer hit areas should stop at the relevant pane boundary and should not appear across unrelated terminals.

Actual behavior / logs:
User report: the terminal resize highlight bar extends across the entire screen regardless of whether the target terminal border is only half-screen, and hovering one border can highlight a divider from a different terminal span. Code inspection confirmed `templates/terminals.html` rendered a fixed `#terminalResizeOverlay` with fixed-position `.terminal-resize-handle` buttons, and `renderResizeHandles()` set vertical handles to `metrics.gridContentHeight` and horizontal handles to `metrics.gridContentWidth`, so each handle spanned the full grid content along the perpendicular axis.

Resolution:
`templates/terminals.html` now computes shared-edge segments for each resize track line and renders one bounded handle per segment. Vertical handles get their `top` and `height` from the overlapping row span, horizontal handles get their `left` and `width` from the overlapping column span, and drag metadata remains tied to the existing axis and line. `tests/test_api.py` includes regression coverage that rejects the previous full-grid handle spans.

### Issue ID: ISSUE-2026-004
- Title: Explorer editor lacks log and JSONL formatting
- Priority: Low
- Status: Closed
- Area: `templates/terminals.html`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `logging`, `tests`
- Reported: 2026-07-02

Description:
The explorer text editor/source view needs basic formatting support for common operational text formats, especially `.log`, `.json`, and `.jsonl` files. Without log-aware and JSONL-aware formatting, users reviewing captured logs, structured events, saved API responses, or run artifacts have to scan raw plain text instead of a minimally structured view.

Steps to reproduce:
1. Open GridVibe in explorer mode for a directory containing `.log`, `.json`, and `.jsonl` files.
2. Open each file in the explorer editor/source view.
3. Observe that `.json` receives basic JSON language recognition, while `.log` and `.jsonl` are treated as unclassified text without line-oriented log or JSONL formatting.

Expected behavior:
The explorer editor should provide basic readable formatting for `.log`, `.json`, and `.jsonl` files. JSON should keep or improve the existing JSON highlighting, JSONL should apply JSON-style highlighting per line without requiring the whole file to be one JSON document, and log files should visually distinguish useful fields such as timestamps, severity levels, and message text where possible.

Actual behavior / logs:
User report: the explorer text editor mode needs basic `.log`, `.json`, and `.jsonl` style formatting. Code inspection confirms `web/api.py` maps `.json` to `json` in `CODE_PREVIEW_LANGUAGES`, but does not map `.jsonl` or `.log`. The client-side `EXPLORER_LANGUAGE_BY_EXTENSION` and `EXPLORER_LANGUAGE_LABELS` in `templates/terminals.html` likewise recognize `.json` but not `.jsonl` or `.log`, and `highlightExplorerCode()` has generic code-token highlighting rather than log-specific or JSONL-specific formatting. Existing editor tests cover code highlighting and file preview behavior, but not these operational text formats.

### Proposed solution:
Extend explorer language detection in `web/api.py` and `templates/terminals.html` to recognize `.jsonl` and `.log` alongside `.json`. Add a lightweight JSONL highlighter that tokenizes each non-empty line as JSON when possible and safely falls back to escaped text for malformed lines. Add a log formatter/highlighter that recognizes common timestamp prefixes and severity labels such as `TRACE`, `DEBUG`, `INFO`, `WARN`, `WARNING`, `ERROR`, and `CRITICAL` without changing the read-only file explorer contract. Keep formatting client-side and bounded by the existing preview size limits. Add focused tests in `tests/test_api.py` for backend language detection, client extension maps, labels, and representative JSONL/log highlighting hooks.
