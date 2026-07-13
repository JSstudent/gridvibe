# GridVibe Testing Issues
Last updated: 2026-07-13

## Open Issues

### Issue ID: ISSUE-2026-021
- Title: Appearance theme changes do not reach open session windows
- Priority: Medium
- Status: Open
- Area: `web/static/js/shared.js`, `web/static/js/launcher.js`, `web/static/js/terminals.js`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `settings`, `launcher`, `session`, `ui`, `theme`, `tests`
- Reported: 2026-07-13

Description:
Changing the Appearance theme to Light or Dark in launcher settings updates the launcher but does not update an already-open session window. The application can therefore display mismatched themes across its two primary windows until the session page is reloaded or its theme is changed separately, making the global appearance setting behave like a launcher-only preference.

Steps to reproduce:
1. Start GridVibe with the launcher and at least one session window open.
2. In launcher App Settings, change Appearance from Dark to Light or from Light to Dark and save.
3. Observe that the launcher applies the selected theme while the open session window remains on its previous theme.

Expected behavior:
The saved Light, Dark, or System appearance preference should be global to GridVibe. Saving it from the launcher should immediately apply the resolved theme to the launcher and every open session window, and newly opened or reconnected session windows should initialize from the same persisted preference. This content-theme synchronization is distinct from the intentionally dark native Windows title-bar policy documented by closed ISSUE-2026-009.

Actual behavior / logs:
Code inspection confirms `saveAppSettings()` in `web/static/js/launcher.js` applies the returned theme locally through `applyAppSettings(data)`, then calls `notifyAppConfigUpdated(data)`. That cross-window payload contains only `workspace.surface_mode`, not `appearance.theme`. The backend `_broadcast_app_config_update()` in `web/api.py` likewise emits only `workspace.surface_mode`, and all BroadcastChannel, storage-event, and Socket.IO handlers in `web/static/js/terminals.js` call `applyAppConfigSurfaceMode()` without applying a theme. Shared `applyTheme()` writes `gridvibe.theme` to local storage, but `initTheme()` does not register a storage listener, so an already-open session document does not react to the launcher write. Existing tests assert surface-mode propagation and basic theme controls, but do not cover live theme synchronization between windows.

### Proposed solution:
Define one normalized app-config update contract containing `appearance.theme` alongside `workspace.surface_mode`, and emit it consistently from both `notifyAppConfigUpdated()` and `_broadcast_app_config_update()`. Replace or extend the session-side surface-only handler with an idempotent app-config handler that calls `applyTheme()` for a valid `system`, `light`, or `dark` preference and continues applying surface mode. Keep BroadcastChannel, storage-event, and Socket.IO delivery paths aligned, avoid rebroadcast or local-storage feedback loops, and make duplicate deliveries harmless. On session-window focus, visibility restoration, or socket reconnect, reconcile against `/api/app-config` or the authoritative stored preference so updates missed while disconnected are recovered. Preserve System-mode media-query behavior and the native-title-bar policy from ISSUE-2026-009. Add focused tests in `tests/test_api.py` for backend payload contents, launcher notification contents, live BroadcastChannel/storage/Socket.IO application, multiple open session windows, duplicate and malformed messages, reconnect/focus reconciliation, Light/Dark/System transitions, and newly opened session initialization.

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

### Issue ID: ISSUE-2026-019
- Title: Add floating waveform indicator while voice input is recording
- Priority: Low
- Status: Open
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `voice`, `ui`, `terminal`, `accessibility`, `tests`
- Reported: 2026-07-13

Description:
Voice capture does not provide a prominent workspace-level indication that the microphone is actively recording. The only visual feedback is a small state change on the microphone button in the active terminal header, which can be easy to miss when recording is triggered by a push-to-talk keybind or when attention is elsewhere in the workspace. Users therefore cannot confidently tell at a glance whether GridVibe is listening.

Steps to reproduce:
1. Enable voice input, configure push-to-talk and a keybind, and open a terminal session.
2. Start voice capture from the terminal microphone control, or press and hold the configured push-to-talk keybind.
3. Observe that the microphone button receives a small red pulse while recording, but no floating waveform or other prominent recording indicator appears over the workspace.

Expected behavior:
Whenever microphone capture is active, whether initiated by a press-and-hold GUI microphone interaction or the configured push-to-talk keybind, GridVibe should show a compact floating waveform-style indicator in a consistent, unobtrusive workspace position. It should appear only after capture actually starts, remain visible for the full recording, clearly identify the recording state, and disappear promptly on release, stop, cancellation, backend error, or teardown.

Actual behavior / logs:
Code inspection confirms `_updateVoiceBtn()` in `web/static/js/terminals.js` only toggles the `recording` class and title on the per-terminal microphone button. `web/static/css/terminals.css` renders that class as a red button with a small pulsing box shadow. The push-to-talk listeners call `_startVoice()` on matching `keydown` and `_stopVoice()` on `keyup`, but neither path creates or synchronizes a page-level recording overlay or waveform. The GUI microphone control is currently wired as a click-to-toggle action rather than a press-and-hold interaction.

### Proposed solution:
Add one reusable, fixed-position voice-recording overlay managed by the shared capture lifecycle in `web/static/js/terminals.js` and styled in `web/static/css/terminals.css`. Show it only once `_startVoice()` has established an active recording state, and hide it from every `_stopVoice()`, permission-denial, backend-error, session-switch, disconnect, and teardown path. Render a clear microphone/`Recording` label plus animated waveform bars; where practical, drive the bars from a bounded `AnalyserNode` attached to the existing audio graph, with a lightweight deterministic animation as a fallback. Make the overlay non-blocking, theme-aware, responsive, and accessible with status semantics, sufficient contrast, and a `prefers-reduced-motion` treatment. Add pointer down/up/cancel handling for the requested hold-to-talk GUI interaction while preserving accessible keyboard activation and defining how it coexists with the existing click-to-toggle behavior. Ensure the single-active-terminal rule prevents duplicate overlays and that rapid key or pointer release during asynchronous startup cannot leave a stale indicator. Add focused rendered-template tests in `tests/test_api.py` for GUI hold and keybind start/stop wiring, successful-start timing, every cleanup path, repeated-key suppression, terminal switching, reduced-motion/accessibility hooks, and absence of duplicate overlays.

### Issue ID: ISSUE-2026-018
- Title: Add a revert action for unstaged Git changes
- Priority: Low
- Status: Open
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `web/api.py`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `git`, `flask`, `tests`
- Reported: 2026-07-13

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

### Issue ID: ISSUE-2026-016
- Title: Markdown preview links do not open explorer tabs
- Priority: Medium
- Status: Open
- Area: `web/static/js/terminals.js`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `markdown`, `terminal`, `tests`
- Reported: 2026-07-13

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

### Proposed solution:
Implement delegated anchor handling on each `.explorer-markdown-preview` after the tab model from ISSUE-2026-014 is available. Classify links before acting: keep same-document fragments inside the active preview; resolve relative Markdown targets against the current file's directory; normalize and validate decoded paths within the Explorer root; and call the shared pinned-tab open/focus primitive rather than the legacy single-file replacement flow. Preserve URL fragments so the destination tab can scroll to a sanitized heading target. Define safe behavior for `http`/`https`, `mailto`, unsupported local formats, missing files, traversal attempts, encoded separators, absolute paths, and SSH explorers; external links must not replace the GridVibe workspace and should use appropriate opener isolation. If ISSUE-2026-015 is implemented, link-opened pinned tabs should serialize identically to tabs opened with the tree `+` action. Add focused tests in `tests/test_api.py` for relative, parent-relative, fragment-only, duplicate, external, missing, unsupported, out-of-root, encoded, local, and SSH targets plus event isolation and tab persistence integration.

### Issue ID: ISSUE-2026-015
- Title: Persist open explorer tabs in saved sessions
- Priority: Low
- Status: Open
- Area: `web/static/js/terminals.js`, `web/static/js/launcher.js`, `web/api.py`, `sessions/manager.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `session`, `launcher`, `settings`, `tests`
- Reported: 2026-07-12

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

### Proposed solution:
After defining the per-pane tab model for ISSUE-2026-014, add bounded saved-session fields for the ordered pinned file paths and active tab identity. Capture them in `buildWorkspaceTerminalEntry()` for active-workspace saves; preserve them when launcher settings load and resave an existing preset even though the launcher does not edit file tabs directly; validate and normalize them in `web/api.py`; and carry them through `TerminalSession` responses so `web/static/js/terminals.js` can restore them after the explorer pane initializes. Store normalized paths relative to the saved explorer root where practical, cap tab counts and path lengths, ignore missing, unsupported, duplicate, or out-of-root files without failing session launch, and never persist file contents or writable state. Add focused tests in `tests/test_api.py` for both save entry points, ordering and active-tab round-trips, backward compatibility, root changes, unsafe paths, missing/unsupported files, deduplication, limits, and local/SSH parity.

### Issue ID: ISSUE-2026-014
- Title: Replace explorer directory view with tabbed file viewer
- Priority: Low
- Status: Open
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `terminal`, `tests`
- Reported: 2026-07-12

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

### Proposed solution:
Refactor the Explorer client state and rendering in `web/static/js/terminals.js` so the Files tree is the navigation surface and the main content is a permanent viewer. Add a per-pane tab model with one reserved dynamic-preview tab plus deduplicated pinned tabs keyed by normalized file path. A normal readable-file click should load into the preview tab; an accessible `+` control on that file's tree row should stop propagation and open or focus a pinned, closable tab. Render the tab strip above the existing editor header, prevent closing the preview tab, define focus selection after closing a pinned tab, preserve appropriate per-tab file/view/search/scroll state, and clear stale tabs safely when the explorer session or root changes. Remove the directory-list/open-folder dependency from the main viewer while retaining tree expansion, Git information, refresh behavior, supported-file validation, local/SSH parity, and the read-only guarantee. Add styles in `web/static/css/terminals.css` and focused rendered-template tests in `tests/test_api.py` for the empty state, preview replacement, `+` event isolation, tab deduplication and closing, active-tab fallback, mode/root resets, refresh, and unsupported files.

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
