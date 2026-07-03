# GridVibe Testing Issues
Last updated: 2026-07-03

## Open Issues

### Issue ID: ISSUE-2026-007
- Title: Opening binary files can freeze explorer mode
- Priority: Medium
- Status: Open
- Area: `templates/terminals.html`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `flask`, `tests`
- Reported: 2026-07-02

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

### Issue ID: ISSUE-2026-005
- Title: Explorer file find blocks terminal UI on large previews
- Priority: Medium
- Status: Open
- Area: `templates/terminals.html`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `terminal`, `ui`, `tests`
- Reported: 2026-07-02

Description:
The explorer file find flow can become extremely slow on large text files and make the terminal workspace appear frozen while searching. The practical impact is that users inspecting large logs or source files can temporarily lose terminal responsiveness because the search work runs synchronously in the browser UI thread.

Steps to reproduce:
1. Open GridVibe in explorer mode and open a large text/log/source file.
2. Use the file find input to search for a common token, especially one with many matches.
3. Continue typing or navigate between matches while observing terminal pane responsiveness.

Expected behavior:
Searching within large file previews should remain responsive. Typing in the find box, navigating matches, and terminal input/output rendering should not visibly freeze the workspace; long searches should be bounded, debounced, chunked, cancellable, or moved off the UI thread.

Actual behavior / logs:
User report: the search function is extremely slow on large files and causes the entire terminal to freeze while searching. Code inspection shows `web/api.py` can send up to `EXPLORER_FILE_PREVIEW_MAX_BYTES` of text content per opened file, currently 1 MiB. In `templates/terminals.html`, each find input event updates state and calls `applyExplorerSearch()`, which calls `explorerFindRanges()` to lowercase the full preview content and collect every match, then calls `renderExplorerSource()` / `highlightExplorerCode()` to rebuild the full source HTML with search markup. These operations are synchronous on the main browser thread and have no debounce, match cap, cancellation, worker handoff, or incremental rendering.

### Proposed solution:
Make explorer file search bounded and asynchronous enough for large previews. In `templates/terminals.html`, debounce input, cancel stale searches, cap or progressively count matches, and avoid re-rendering the full file on every keystroke when possible. Consider chunked search with `requestIdleCallback`/`setTimeout`, a Web Worker for large content, or virtualized line rendering so highlighting only touches visible content plus nearby matches. Keep `web/api.py` preview truncation behavior intact unless a smaller preview or explicit large-file warning is chosen. Add regression tests in `tests/test_api.py` for the presence of debounce/cancellation or worker wiring, match-cap behavior, truncated-file messaging, and preservation of existing source/preview search controls.

## Closed Issues

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
