# GridVibe Testing Issues
Last updated: 2026-07-19

## Open Issues

### Issue ID: ISSUE-2026-036
- Title: Restart restore launches password-auth SSH sessions without credentials
- Priority: High
- Status: Open
- Area: `web/static/js/launcher.js`, `web/runtime_state.py`, `web/api.py`, `web/saved_sessions.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `session`, `launcher`, `terminal`, `ssh`, `tests`
- Reported: 2026-07-17

Description:
Restoring the previous workspace after a GridVibe restart cannot reconnect remote SSH sessions that require password authentication. The runtime snapshot correctly excludes passwords, but the restore flow replays its session entries directly through `POST /api/sessions` without rehydrating the encrypted password from the referenced saved-session preset or asking the user to authenticate. GridVibe creates the session groups and reports them as restored even though their SSH connections immediately fail, leaving every affected terminal and remote Explorer pane unusable. Key-authenticated SSH sessions are not necessarily affected.

Steps to reproduce:
1. Save and launch a remote SSH workspace that authenticates with a password, then close GridVibe while the workspace is active so `runtime_state.json` captures its group and `saved_session_id`.
2. Restart GridVibe and click **Restore** in the previous-workspace banner.
3. Observe that the launcher submits the password-free snapshot, reports the group as restored after `POST /api/sessions` returns `201`, and opens the terminal workspace.
4. Observe that each restored SSH terminal or remote Explorer pane fails to connect with `No authentication methods available`.

Expected behavior:
The restart restore flow should keep `runtime_state.json` free of secrets while securely reusing the encrypted password from the matching saved-session preset when one is available. If no reusable credential exists, GridVibe should request authentication in an in-page flow or present a clear retryable failure instead of launching doomed sessions. A group should not be reported as successfully restored merely because its session records were created before the SSH connection result is known.

Actual behavior / logs:
The captured restore requests contain a valid `saved_session_id`, SSH host, username, port, and directories, but no `password`. GridVibe then logs `paramiko.connect ... password=None`, followed by `Failed to connect SSH session ...: No authentication methods available` for every restored remote pane. Agent preflight fails for the same reason and clears the startup command. Despite those asynchronous failures, `POST /api/sessions` returns `201`, so `restorePreviousWorkspace()` increments its success count from `response.ok` and can display `Restored ... from the previous workspace.` Code inspection confirms `_SESSION_SNAPSHOT_FIELDS` in `web/runtime_state.py` deliberately omits `password`; `restorePreviousWorkspace()` in `web/static/js/launcher.js` posts `group.sessions` unchanged; and `create_sessions()` in `web/api.py` retains `saved_session_id` as group metadata but does not resolve it through `load_saved_sessions()` or `_find_saved_session_entry()` to populate the in-memory SSH password. Existing runtime-restore tests cover only a local Explorer group and assert password-free persistence, so they do not exercise password-authenticated SSH restore.

### Proposed solution:
Keep passwords out of `runtime_state.json` and add a server-side restore preparation path that resolves each SSH group's normalized `saved_session_id` through `load_saved_sessions()` / `_find_saved_session_entry()`, decrypts the preset password using the existing `web/secrets.py` flow, and applies it only to the in-memory launch configuration after validating that the preset is still an SSH preset for the same host, username, and port. Do not return the rehydrated password from `/api/runtime-state`, send it back through the browser, or include it in request/session logs. When the saved preset is missing, changed, lacks a password, or does not match the snapshot target, preserve key/agent authentication attempts but expose an in-page credential/retry path rather than silently counting session creation as restore success. Update the launcher/API restore contract so connection failures remain visible and a `201` record-creation response is not presented as proof that remote panes connected. Add focused tests in `tests/test_api.py` for password-free snapshot persistence, successful saved-password rehydration without secret disclosure, key-auth restore, missing/mismatched/decryption-failed credentials, remote Explorer and agent panes, multiple restored groups, and prevention of the current false-success message when SSH authentication fails.

## Closed Issues

### Issue ID: ISSUE-2026-033
- Title: Markdown preview appearance not saved with session presets or restore snapshot
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/saved_sessions.py`, `web/runtime_state.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `markdown`, `session`, `settings`, `tests`
- Reported: 2026-07-16
- Closed: 2026-07-19

Description:
The Markdown preview appearance preference (reading-surface preset and font family, introduced by ISSUE-2026-030) was stored exclusively in two global `localStorage` keys — `gridvibe.mdPreviewPreset` and `gridvibe.mdPreviewFont` — and was not included in the saved-session preset schema, the active-workspace Save Session serialization, or the `runtime_state.json` auto-restore snapshot. A session preset opened on a different machine or after clearing browser storage silently reverted to the default appearance.

Resolution:
Implemented per the proposed solution as part of follow-up item 2.f. `buildWorkspaceTerminalEntry()` now serializes `explorer_md_preset` / `explorer_md_font` (read via `explorerMarkdownAppearance()`) for explorer panes; `_normalize_terminal_entries()` in `web/saved_sessions.py` allowlist-validates both (preset ∈ `{default, paper, contrast, vscode}` — the actual client keys, including the Wave 1 Slate preset — font ∈ `{system, serif, mono}`) and `_merge_workspace_session_config()` gates them to explorer panes; both fields joined `_SESSION_SNAPSHOT_FIELDS` in `web/runtime_state.py` and the `TerminalSession` schema. On restore, `applyExplorerSessionMarkdownAppearance()` applies the saved appearance through `setExplorerMarkdownAppearance()` — which also updates the shared `localStorage` keys, keeping the appearance global as documented — once per session id, so a close-driven pane rebuild never clobbers an appearance changed since launch, and sessions without the fields keep the local preference. The launcher round-trips both fields via row datasets without editing them. Covered by `test_normalize_terminal_entries_validates_tab_views_and_md_appearance`, `test_workspace_save_round_trips_tab_views_and_md_appearance`, `test_terminals_page_persists_tab_views_and_markdown_appearance`, `test_launcher_round_trips_explorer_tab_views_and_markdown_appearance`, and `test_runtime_state_snapshot_includes_tab_views_and_md_appearance` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-034
- Title: Committing via Git sidebar does not reload the Files tree
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`
- Assignee: Unassigned
- Tags: `file-explorer`, `git`, `ui`, `tests`
- Reported: 2026-07-16
- Closed: 2026-07-19

Description:
After a successful commit through the Explorer Git sidebar, the Files tree sidebar was not reloaded and the main pane was not refreshed in tabbed viewer mode: `performExplorerGitAction` updated the Git panel from the backend response but never called `reloadExplorerTree`, and `loadExplorerPane` only ran in the rarely-active directory mode, so the tree and any open file/diff went stale after a commit.

Resolution:
Implemented per the proposed solution, generalised to every worktree-mutating action (follow-up Wave 3 / item 1.a). `performExplorerGitAction()` now routes `stage`, `unstage`, `revert`, `commit`, and the new `stage-all` / `discard-all` endpoints (publish excluded — remote-only) through a shared `refreshExplorerAfterGitAction()` that reloads the Files tree (`reloadExplorerTree` guards internally on `pane._explorerTreeSidebarOpen`), invalidates the cached diff (`_explorerDiffLoaded` / `_explorerDiffCacheKey`), and re-fetches the currently open file in place with scroll preserved — bulk actions refresh whatever file is open, single-path actions only their own path. The revert flow's bespoke reopen was folded into the shared refresh. Covered by `test_terminals_page_git_actions_refresh_tree_and_open_file` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-032
- Title: Git sidebar "Changes" header has no Stage All button
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/api.py`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `git`, `ui`, `flask`, `tests`
- Reported: 2026-07-16
- Closed: 2026-07-19

Description:
The Git sidebar's **Changes** (unstaged) section header contained only a plain title label with no action to stage all uncommitted working-tree changes at once; each file had to be staged individually with its per-row `+` button.

Resolution:
Implemented per the proposed solution (follow-up Wave 3 / item 1.b). The **Changes** header now carries a compact **Stage All** button (`data-explorer-git-stage-all`, styled like the per-row `+` controls, disabled when the unstaged list is empty or a Git action is busy) wired to `explorerGitStageAll(index)` → `POST /api/explorer/<session_id>/git/stage-all` → `_git_stage_all_paths()` in `web/explorer.py` (`git add --all` scoped to the repository root, run with `write=True` so `GIT_TERMINAL_PROMPT=0`). Success returns the updated repository summary and routes through the shared ISSUE-2026-034 refresh. A sibling **Discard All** button (follow-up item 1.c, OD-1) landed in the same header: tracked-worktree-only `git restore --worktree` via `POST .../git/discard-all`, in-page confirm, no `git clean`. Covered by `test_explorer_git_stage_all_stages_every_change`, `test_explorer_git_stage_all_requires_a_repository`, the three discard-all tests, `test_git_discardable_worktree_paths_filters_porcelain_records`, and `test_terminals_page_git_bulk_action_controls_are_present` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-035
- Title: Valid UTF-8 files are rejected when preview sample splits a character
- Priority: Medium
- Status: Closed
- Area: `web/explorer.py`, `web/api.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `flask`, `unicode`, `tests`
- Reported: 2026-07-17
- Closed: 2026-07-18

Description:
GridVibe Explorer could reject a valid UTF-8 text or Markdown file as binary when the fixed 4,096-byte binary-detection sample ended partway through a multibyte character. The file was then unavailable in both Source and Markdown Preview even though its complete contents decoded correctly as UTF-8. Whether a normal text file opened therefore depended on the byte alignment of Unicode characters near the sampling boundary.

Resolution:
Implemented exactly per the proposed solution: `_explorer_content_looks_binary()` in `web/explorer.py` now decodes the 4,096-byte sample through an incremental strict UTF-8 decoder with `final=False` only when `raw_content` holds bytes beyond the sample, so a trailing partial multibyte sequence caused purely by sampling is deferred while invalid bytes inside the sample, NUL bytes, excessive control bytes, and genuinely truncated short files are still rejected (shared local/SFTP path unchanged, NUL/control-byte heuristics intact). Closed with the follow-up Wave 2 regression tests in `tests/test_api.py`: `test_explorer_binary_detection_allows_multibyte_char_crossing_sample_boundary` (three-byte `─` crossing byte 4,096), `test_explorer_binary_detection_rejects_invalid_utf8_inside_oversized_sample` (invalid sequence inside the sample still rejected), `test_explorer_file_accepts_utf8_split_across_sample_boundary` and `test_explorer_file_accepts_utf8_split_across_sample_boundary_remote` (local/SFTP endpoint parity); the pre-existing `test_explorer_binary_detection_rejects_incomplete_utf8_at_content_end` covers the short-file case.

### Issue ID: ISSUE-2026-031
- Title: App Settings action buttons overlap voice settings content
- Priority: Low
- Status: Closed
- Area: `web/static/css/launcher.css`, `templates/index.html`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `settings`, `launcher`, `ui`, `tests`
- Reported: 2026-07-16
- Closed: 2026-07-18

Description:
When voice input was enabled in the launcher App Settings modal, the pinned Save Settings and Cancel action buttons overlapped the bottom of the voice settings content instead of sitting below a scrollable body. At common desktop window sizes the buttons clipped into the microphone section (for example over the "Microphones loaded. Labels may stay generic until microphone permission is granted" hint), obscuring the last controls and looking broken. Because the fields backing the same modal are where new terminal configuration would be added (ISSUE-2026-029), any further growth of this modal would make the overlap worse.

Steps to reproduce:
1. Open GridVibe and open App Settings from the launcher at a normal desktop window size.
2. Enable "Enable voice input" so the full voice settings panel (backend, model, device, compute type, microphone, push-to-talk) is shown.
3. Observe that the Save Settings and Cancel buttons overlap the bottom of the voice content rather than resting below a scrollable settings area.

Expected behavior:
The App Settings body should scroll within the modal's fixed height while the header and the Save Settings / Cancel action row stay pinned and never overlap content. Enabling voice input (or adding more settings later) should extend the scrollable region, not push content under the action buttons.

Actual behavior / logs:
Code inspection confirmed `.modal-card` in `web/static/css/launcher.css` is a `grid-template-rows: auto minmax(0, 1fr) auto` layout (header / scroll body / pinned actions), and the base `.settings-grid` scrolls via `overflow: auto; min-height: 0`. However `.app-settings-card .settings-grid` overrode this with `overflow: visible; padding-right: 0`, which disabled the scroll region for the App Settings modal. With the card capped by `.app-settings-card { max-height: min(92vh, 860px) }` / `.app-settings-card.voice-enabled { max-height: min(96vh, 900px) }`, the taller voice content overflowed its `1fr` track and painted under the pinned `.app-settings-actions` footer. The narrow-width breakpoint restored `.app-settings-card .settings-grid { overflow: auto }`, so the overlap was only visible at wider desktop widths with voice enabled.

Resolution:
The `.app-settings-card .settings-grid { overflow: visible; padding-right: 0 }` override in `web/static/css/launcher.css` was removed, along with the narrow-width breakpoint block that re-restored `overflow: auto` (now redundant). The App Settings body therefore uses the base `.settings-grid` scroll model — `overflow: auto; min-height: 0; padding-right: 4px` — at every width, inside the `.modal-card` grid rows (`auto minmax(0, 1fr) auto`), so the header and the pinned `.app-settings-actions` row stay out of the content flow and taller content (voice enabled, or the ISSUE-2026-029 terminal fields added on top of this fix) extends the scrollable region instead of painting under the buttons. Covered by `test_app_settings_body_keeps_modal_scroll_region` in `tests/test_api.py`, which rejects any `overflow: visible` override on the App Settings grid and asserts the pinned-row modal layout survives.

### Issue ID: ISSUE-2026-029
- Title: Settings UI omits terminal config (font family, size, max sessions)
- Priority: Low
- Status: Closed
- Area: `templates/index.html`, `web/static/js/launcher.js`, `web/static/js/terminals.js`, `web/api.py`, `web/config.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `settings`, `launcher`, `terminal`, `flask`, `tests`
- Reported: 2026-07-15
- Closed: 2026-07-18

Description:
`default_config.json` exposes a `terminal` block (`max_sessions`, `font_family`, `font_size`) that is loaded into `RuntimeConfig` and applied to the xterm workspace, but the App Settings modal provided no controls for any of it. Users could change theme, workspace surface mode, SSH host-key policy, and voice options from the UI, yet could only change the terminal font family, font size, or maximum session count by hand-editing `config.json` and restarting, which is not discoverable and inconsistent with the other runtime settings.

Steps to reproduce:
1. Launch GridVibe and open App Settings from the launcher.
2. Inspect the available settings fields.
3. Observe controls for Appearance theme, workspace surface mode, SSH host-key policy, and voice input, but none for terminal font family, terminal font size, or maximum sessions, even though these exist in `default_config.json`.

Expected behavior:
App Settings should surface the terminal configuration already backed by `RuntimeConfig` — terminal font family, terminal font size, and maximum sessions — with validated, bounded inputs. Saving should round-trip through `/api/app-config` and `RuntimeConfig`, persist to `config.json`, and apply to the launcher and open/newly launched session windows the same way theme changes already propagate.

Actual behavior / logs:
Code inspection confirmed the App Settings modal in `templates/index.html` rendered only `appTheme`, `appSurfaceMode`, `appSshHostKeyPolicy`, and the voice fields. `collectAppSettings()`/`saveAppSettings()` in `web/static/js/launcher.js` gathered only `appearance`, `workspace`, `ssh`, and `voice_input`. `_normalize_app_config_update()` in `web/api.py` whitelisted exactly those sections and never read or wrote a `terminal` block, while `web/config.py` (`RuntimeConfig`) loads `terminal.max_sessions`, `terminal.font_size`, and `terminal.font_family`, and `web/api.py` already passed `terminal_font_size`/`terminal_font_family` into the terminals template. The values were therefore file-only with no UI or API write path.

Resolution:
The App Settings modal in `templates/index.html` gained a **Terminal** section with three bounded, labelled inputs — Font Family (text), Font Size (number, 6–48), and Max Sessions (number, 1–16) — landing on top of the ISSUE-2026-031 scroll fix so the longer form stays scrollable. `DEFAULT_APP_SETTINGS` / `syncAppSettingsForm()` / `collectAppSettingsForm()` in `web/static/js/launcher.js` round-trip a new `terminal` section (`font_family`, `font_size`, `max_sessions`). `_normalize_app_config_update()` in `web/api.py` gained a validated `terminal` branch: the font family is trimmed and falls back to the current runtime value when empty or longer than `TERMINAL_FONT_FAMILY_MAX_LENGTH`, while font size and max sessions are int-parsed with a current-value fallback and clamped to shared bounds defined once in `web/config.py` (`TERMINAL_FONT_SIZE_MIN`/`MAX` = 6/48, `MAX_SESSIONS_MIN`/`MAX` = 1/16). `RuntimeConfig.refresh()` applies the same clamps on load, so a hand-edited `config.json` cannot smuggle out-of-range values, and `_public_app_config()` now returns the `terminal` block. Live propagation reuses the established app-config update contract: `_broadcast_app_config_update()` (Socket.IO) and `notifyAppConfigUpdated()` (BroadcastChannel/storage) carry `terminal.font_family`/`terminal.font_size`, and a new `applyAppConfigTerminalFont()` in `web/static/js/terminals.js` validates the payload and applies the font to every open xterm pane immediately (with a refit); `max_sessions` intentionally applies to new launches only, as the modal hint states. Covered by `test_app_config_returns_terminal_settings`, `test_app_config_persists_terminal_settings`, `test_app_config_clamps_terminal_bounds`, `test_app_config_rejects_invalid_terminal_values`, `test_runtime_config_refresh_clamps_terminal_settings`, `test_app_settings_modal_collects_terminal_fields`, and `test_terminals_page_applies_live_terminal_font_updates` in `tests/test_api.py`, plus the updated broadcast-payload assertion in `test_app_config_endpoint_persists_theme_and_voice_settings`.

### Issue ID: ISSUE-2026-013
- Title: Add per-agent auto-mode toggles to terminal settings
- Priority: Low
- Status: Closed
- Area: `web/static/js/launcher.js`, `web/static/js/terminals.js`, `web/agents.py`, `web/terminal_io.py`, `web/saved_sessions.py`, `web/runtime_state.py`, `agent_registry.json`, `sessions/manager.py`, `tests/test_api.py`, `tests/test_session_manager.py`
- Assignee: Unassigned
- Tags: `terminal`, `settings`, `launcher`, `session`, `tests`
- Reported: 2026-07-12
- Closed: 2026-07-18

Description:
Agent-mode terminal settings needed an auto-mode toggle tied to the selected agent. GridVibe launched built-in agents with their base command only, requiring users who wanted unattended edit/approval behavior to add flags manually or use a custom command instead of the normal agent selection flow.

Steps to reproduce:
1. In the GridVibe launcher, set a terminal's Startup Mode to Agent and select a supported agent such as Claude.
2. Inspect the agent settings, enable the desired launch configuration, and launch the session.
3. Observe that no auto-mode toggle is available and Claude starts as `claude` rather than, for example, `claude --enable-auto-mode`.

Expected behavior:
Agent mode should expose an Auto mode toggle for the corresponding selected agent. When enabled, GridVibe should launch that agent with its registered auto-edit/approval option, such as `claude --enable-auto-mode`; when disabled, it should retain the current base command. The setting should remain scoped per terminal, use only flags supported by the selected agent, and round-trip through launcher drafts, saved sessions, and active workspace Save Session behavior.

Actual behavior / logs:
Code inspection confirmed `buildTerminalInitialCommand()` in `web/static/js/launcher.js` returned either the selected built-in agent key or the custom-agent text verbatim. `collectTerminalDrafts()` stored agent selection metadata but had no auto-mode field, `TerminalSession` in `sessions/manager.py` had no corresponding persisted property, and `_run_startup_sequence()` sent `session.initial_command` unchanged. The agent settings UI therefore had no way to represent or consistently restore an agent-specific auto-mode choice.

Resolution:
Auto-mode flags are registry-owned: entries in `agent_registry.json` may declare an `auto_mode` object (`flag` + `description`) — shipped for `claude` (`--enable-auto-mode`), `codex` (`--full-auto`), and `copilot` (`--allow-all-tools`); `opencode`/`kilo` register none. `_agent_auto_mode_flag()` in `web/agents.py` accepts a flag only if it starts with `-` and contains no whitespace, so a registry typo can never smuggle a second command, and `_agent_options()` exposes it to the launcher as `auto_mode_flag`. The agent settings row in `web/static/js/launcher.js` renders an accessible **Auto mode** checkbox only when the selected built-in agent has a registered flag: `syncTerminalAgentAutoModeState()` recomputes visibility and the `Launches as "<agent> <flag>"` help text on agent-selection and startup-mode changes, and the toggle is cleared when leaving agent mode or when the selection has no flag (custom agents stay explicit and unmodified). The per-terminal boolean `agent_auto_mode` round-trips end to end — launcher drafts (`collectTerminalDrafts()`), saved-session normalization (`_normalize_terminal_entries()` in `web/saved_sessions.py` gates it to agent startup mode), workspace saves (`buildWorkspaceTerminalEntry()` in `web/static/js/terminals.js` + `_merge_workspace_session_config()`), `TerminalSession` / `create_sessions()` / `update_session_metadata()` in `sessions/manager.py`, and the runtime-state snapshot (`_SESSION_SNAPSHOT_FIELDS` in `web/runtime_state.py`) — while the persisted `initial_command` always stays the bare agent key, keeping preflight detection based on the base agent executable. The flag is composed only at launch time: `_run_startup_sequence()` in `web/terminal_io.py` sends `_compose_agent_startup_command(session)` (`web/agents.py`), which appends the registered flag exactly once for a built-in agent in agent mode with the toggle on and returns every other command (custom, non-agent mode, unsupported agent, mismatched base) verbatim, so flags are never duplicated or applied to arbitrary commands. Covered by `test_agent_options_expose_registry_auto_mode_flags`, `test_auto_mode_flag_rejects_malformed_registry_values`, `test_compose_agent_startup_command_variants`, `test_startup_sequence_sends_composed_auto_mode_command`, `test_normalize_terminal_entries_gates_agent_auto_mode`, `test_workspace_merge_carries_agent_auto_mode`, `test_sessions_post_round_trips_agent_auto_mode`, `test_runtime_state_snapshot_includes_agent_auto_mode`, and `test_launcher_wires_the_auto_mode_toggle` in `tests/test_api.py`, plus `test_create_sessions_carries_agent_auto_mode` in `tests/test_session_manager.py`.

### Issue ID: ISSUE-2026-025
- Title: Highlight the currently active terminal pane
- Priority: Low
- Status: Closed
- Area: `web/static/js/terminals.js`, `web/static/css/terminals.css`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `ui`, `accessibility`, `tests`
- Reported: 2026-07-15
- Closed: 2026-07-17

Description:
GridVibe did not visually identify the terminal pane that is currently selected for input. In a multi-pane workspace, users had to infer the input target from browser focus alone. This was noticeably inconsistent with Broadcast typing mode, which gives every participating plain terminal pane a clear accent border, and made it easier to send a command to the wrong terminal.

Steps to reproduce:
1. Open a GridVibe session group with two or more plain terminal panes.
2. Click or type in one terminal pane, then inspect the panes' borders and headers.
3. Observe that the input-target pane has no persistent active/selected visual treatment; enable Broadcast typing to see the existing accent-border treatment applied to all participating panes.

Expected behavior:
Exactly one eligible terminal pane should have a clear, theme-aware active/selected treatment whenever it is the current input target. The treatment should be comparable in prominence to the broadcast border while remaining distinguishable from broadcast mode, and it should update for pointer, keyboard, programmatic focus, terminal search close/return-focus, pane creation, removal, and session-group switching.

Actual behavior / logs:
Code inspection confirmed `forwardTerminalInput()` and the terminal-card `pointerdown` listeners updated `_focusedTerminalIndex`, which is also used to choose keyboard-search and push-to-talk targets. Neither path added a class, ARIA state, or other visible marker to the corresponding `.terminal-container`. In contrast, `setBroadcastInput()` toggles `broadcast-input` on `#terminalsGrid`, and the `.broadcast-input .terminal-container:not(.explorer-pane):not(.browser-pane)` CSS rule applies `var(--t-accent)` border and inset shadow to every participating terminal.

Resolution:
The highlight is driven by *real DOM keyboard focus* — never by terminal output — so it can never disagree with where typing or voice actually lands. A delegated `focusin`/`focusout` pair on `document` (wired once) in `web/static/js/terminals.js` marks whichever plain terminal currently holds focus: `focusin` inside a `.terminal-container` that is not an explorer/browser pane calls `setFocusedTerminal(slot)`, and `focusout` clears the mark (`clearActiveTerminalHighlight()`) unless focus is moving to another plain terminal (whose own `focusin` repaints it). Visual state lives in `paintActiveTerminalCard(index)`, which adds `terminal-active` + `aria-current="true"` to exactly one `tc-<index>` and strips both from every other `.terminal-container` (one-active-pane enforcement); `isPlainTerminalCard()` validates explorer/browser panes out so they are never marked. Critically the highlight is **not** tied to `term.onData`: TUI apps with mouse reporting (vim, opencode, …) emit mouse-move escape sequences through `onData`, so an earlier draft that set focus from input made the highlight follow the mouse into an unfocused pane (visible on a restored split running opencode) — `forwardTerminalInput()` no longer touches selection. `_focusedTerminalIndex` now equals the highlighted pane exactly (or -1 when nothing is selected) and is the single input target for voice / push-to-talk / search; it is cleared on blur to a non-terminal, so a pane that is not visibly selected never silently receives dictation, and push-to-talk (`_findPttTerminalIndex()`) targets the focused terminal only (no fall-back to the first pane). Teardown resets it via `resetFocusedTerminal()` in `teardownCurrentGrid()`. `web/static/css/terminals.css` keeps the token-only `.terminal-container.terminal-active` treatment (a heavier 2px inset `--t-accent` ring plus an accent header rule), distinct from and prominent alongside the 1px broadcast border, with the same `:not(.explorer-pane):not(.browser-pane)` exclusions. Covered by `test_active_terminal_pane_paints_a_single_focused_card`, `test_active_terminal_pane_tracks_real_dom_focus`, `test_push_to_talk_targets_only_the_selected_terminal`, and `test_active_terminal_pane_has_distinct_token_style` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-026
- Title: Voice transcription ignores Broadcast typing and reaches one terminal
- Priority: Medium
- Status: Closed
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `voice`, `terminal`, `socketio`, `tests`
- Reported: 2026-07-15
- Closed: 2026-07-17

Description:
When Broadcast typing was enabled, keyboard input was mirrored to every plain terminal pane, but voice transcription was not. A final voice transcript was delivered only to the single terminal that was recording, so users who expected voice dictation to broadcast to all panes (the way typing does) silently reached just one terminal.

Steps to reproduce:
1. Open a session group with two or more plain terminal panes and enable Broadcast typing.
2. Start voice capture on one terminal (mic button or push-to-talk), speak, and let the final transcript commit.
3. Observe that only the recording terminal receives the transcribed text, while the other broadcast panes receive nothing.

Expected behavior:
While Broadcast typing is active, a committed voice transcript should be mirrored to every participating plain terminal pane, consistent with how keyboard input is broadcast. Interim (non-final) preview text should remain on the recording pane only, and explorer/browser panes should continue to be excluded.

Actual behavior / logs:
Code inspection confirmed the `voice_result` Socket.IO handler in `web/static/js/terminals.js` called `_sendToTerminal(index, text)` on the final transcript, and `_sendToTerminal()` emits `terminal_input` to the single `sessionIds[index]` only. Unlike `forwardTerminalInput()`, which mirrors to all plain panes when `broadcastInputActive` (skipping panes without a `term`), the voice path had no broadcast branch, and `_voiceActiveIndex` restricts recording to one terminal, so the transcript never fanned out.

Resolution:
The keyboard peer fan-out was extracted from `forwardTerminalInput()` into a shared `broadcastInputToPeers(sourceIndex, data)` helper (the `broadcastInputActive` gate, `_noteBroadcastActivity()`, and the explorer/browser-skipping `terminals[otherIndex]?.term` filter now live there once). The `voice_result` final branch delivers the committed transcript to the recording pane via `_sendToTerminal()` and then calls the same `broadcastInputToPeers(index, text)`, so a dictated transcript fans out to every plain pane exactly like typing when Broadcast is on, with no duplicate to the source. Interim (non-final) previews stay isolated to the recording pane (`_showVoicePreview`). Enabling Broadcast typing also focuses a terminal immediately (`setBroadcastInput(true)` → `focusActiveOrDefaultTerminal()`, preferring the sticky `_focusedTerminalIndex`, else the first attached plain terminal), so the user can start typing without first clicking a pane. Covered by `test_voice_transcript_honours_broadcast_typing`, `test_broadcast_enable_focuses_a_terminal_for_immediate_typing`, and the updated `test_input_forwarding_goes_through_the_broadcast_helper` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-020
- Title: Large log previews discard the newest entries
- Priority: Medium
- Status: Closed
- Area: `web/api.py`, `web/explorer.py`, `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `logging`, `flask`, `ui`, `tests`
- Reported: 2026-07-13
- Closed: 2026-07-17

Description:
Explorer text previews are capped at 1 MiB and retain the beginning of an oversized file. For append-oriented files such as logs, this discards the newest and usually most relevant entries, so users cannot inspect current failures or recent activity from GridVibe. The only workaround is to leave the Explorer and use an external tailing or paging tool.

Steps to reproduce:
1. Create a `.log` file larger than 1 MiB with distinguishable markers near its beginning and end.
2. Open the file from a GridVibe Explorer pane.
3. Observe that the beginning marker is present, the end marker is absent, and the preview is reported as truncated.

Expected behavior:
GridVibe should support a bounded text-preview limit that, when a log or other append-oriented file must be truncated, discards content from the beginning and retains the newest content at the end. The UI should clearly identify the retained range, and non-log files should have an explicit, predictable head/tail policy rather than silently losing the most useful section.

Actual behavior / logs:
Code inspection confirmed `EXPLORER_FILE_PREVIEW_MAX_BYTES` in `web/explorer.py` was fixed at `1024 * 1024`, and `get_explorer_file()` in `web/api.py` called `backend.read_file_prefix(file_path, EXPLORER_FILE_PREVIEW_MAX_BYTES + 1)` then sliced `raw_content[:EXPLORER_FILE_PREVIEW_MAX_BYTES]`, so every oversized preview started at byte zero regardless of file type.

Resolution:
`web/explorer.py` gained a `read_file_suffix(file_path, max_bytes, total_size)` ranged/tail read on both the local and SFTP backends, plus a shared `read_explorer_file_preview(backend, file_path, *, total_size, tail)` that picks the head or tail window and returns byte-range metadata. `_is_tail_preview_file()` classifies append-oriented files by reusing the existing language map (`.log` → `"log"`); those, when they exceed the 1 MiB cap, retain their **last** bytes while every other oversized file keeps its opening bytes (unchanged, predictable head policy). A tail window is trimmed to a clean line + UTF-8 boundary by `_trim_tail_preview_to_boundary()` (drop the partial leading line up to the first newline, else skip leading UTF-8 continuation bytes) so a preview never begins mid-character or mid-line. `get_explorer_file()` in `web/api.py` now returns `preview_mode` (`head`/`tail`), `preview_start_byte`, `preview_end_byte`, and `total_size` alongside the existing `truncated`/`size`. In `web/static/js/terminals.js`, `explorerPreviewTruncationLabel()` turns that metadata into a precise message (`Showing the last <N> of <M>` for logs, `Showing the first <N> of <M>` otherwise) in place of the generic `Preview truncated`. Search stays bounded to the returned preview because it already operates on the delivered `content` only. Covered by `test_trim_tail_preview_to_boundary_variants`, `test_explorer_log_preview_retains_tail_and_range_metadata`, `test_explorer_non_log_preview_retains_head`, `test_explorer_preview_at_exact_limit_is_not_truncated`, `test_explorer_log_preview_tail_is_utf8_line_safe`, `test_explorer_remote_log_preview_retains_tail`, and `test_terminals_page_explorer_preview_tail_message` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-022
- Title: Closing a terminal expands unrelated panes in complex split layouts
- Priority: Medium
- Status: Closed
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `ui`, `resize`, `tests`
- Reported: 2026-07-14
- Closed: 2026-07-17

Description:
Closing a terminal pane in a workspace with an asymmetric or multi-neighbor split could resize multiple unrelated panes instead of only expanding the single neighbor that shares the longest border with the closed pane, and it reset every pane's user-set proportions. This disrupted the workspace layout and was especially visible when the user had manually resized panes.

Steps to reproduce:
1. Open a GridVibe session with three or more terminal panes in an asymmetric split — for example, one wide pane below two side-by-side panes (T-shape), or one tall pane beside two stacked panes (L-shape).
2. Optionally, drag the resize handles to set non-uniform pane sizes.
3. Close one of the panes that borders multiple others (e.g., the wide bottom pane that shares border with both top-left and top-right).
4. Observe that more than one remaining pane changes size, and that the surviving panes' manual proportions reset to uniform.

Expected behavior:
Closing a terminal pane should expand exactly one neighbor — the pane that shares the longest border with the closed pane — whenever geometry allows, and only fall back to a minimal side group when a single pane cannot absorb the freed rect. Panes outside the expansion should retain their exact previous sizes and user-set proportions.

Actual behavior / logs:
Code inspection confirmed the side-group fallback (`terminalCloseSideGroups` + `buildTerminalCloseRectsForSideGroup`) expanded *every* pane in the winning side group via `expandRectIntoClosedSide`, not only the pane with the longest shared border. Separately, `closeTerminalPane()`/`closeSplitPane()` set `pendingSplitRestore` with rects only, while the forced rebuild ran `buildGrid()` → `clearSplitSlotGeometry()` (which nulls `splitColumnWeights`/`splitRowWeights`); the restore never re-applied the weights, so all manual proportions reset to uniform after a close.

Resolution:
`buildTerminalCloseRectsForSideGroup()` in `web/static/js/terminals.js` now first attempts to expand only the single contact with the greatest shared border (through a shared `terminalCloseRectsForExpandingContacts()` helper that still enforces the same overlap + area invariants), and falls back to the full side-group expansion only when the single-pane attempt would leave a gap or overlap. Because a valid close preserves the grid's bounding box, the pre-close `splitColumnWeights`/`splitRowWeights` map 1:1 onto the reflowed grid: both close paths now capture them into `pendingSplitRestore` and `initialLoad()` re-applies them before `applySplitSlotGeometry()`, so panes outside the expansion keep their proportions. Covered by `test_terminals_page_close_prefers_single_neighbor_expansion` and `test_terminals_page_close_preserves_split_track_weights` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-027
- Title: Closing a terminal resets explorer/browser pane state in the group
- Priority: Medium
- Status: Closed
- Area: `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `terminal`, `file-explorer`, `session`, `ui`, `tests`
- Reported: 2026-07-15
- Closed: 2026-07-17

Description:
Closing a single terminal pane in a session group rebuilt the entire visible group, discarding the client-only state of every other pane. Explorer panes lost their open file/preview, expanded Files tree, and open Git sidebar; browser-preview panes reloaded to their launch URL. Only pane geometry was preserved, so closing one terminal wiped unrelated reference views the user had set up in the same group.

Steps to reproduce:
1. Open a session group with a terminal pane plus an Explorer pane; in the Explorer pane open a file/preview, expand a few Files-tree folders, and open the Git sidebar (optionally add a browser-preview pane).
2. Close the terminal pane with its `×` button.
3. Observe that the Explorer pane returns to a plain listing with collapsed trees and a closed Git sidebar, and any browser pane reloads, even though those panes were not closed.

Expected behavior:
Closing a terminal should remove only that pane and expand its neighbor into the freed space, while every other pane retains its state — Explorer panes keep their open file/preview, expanded tree, and Git sidebar; browser panes keep their URL.

Actual behavior / logs:
Code inspection confirmed `closeTerminalPane()` set `pendingSplitRestore` with geometry only and called `initialLoad()`, which tears down and reconstructs the whole group's grid from server session metadata. The surviving explorer/browser panes' live client state was never captured, so the rebuild reset explorer panes to the directory-seeded default and reloaded browser panes.

Resolution:
Because a fully general in-place close would require re-indexing every pane's DOM ids and re-wiring index-bound event handlers (effectively a rebuild), the fix captures and restores the surviving panes' live state across the existing rebuild — the sanctioned fallback in the original report. Before the rebuild, `closeTerminalPane()` calls a new `captureSurvivingPaneClientState()` that snapshots each surviving pane by session id (explorer: tree/Git sidebar flags, pinned open tabs + active tab, and the active Preview file/view; browser: live URL) into `pendingCloseClientState`. `initialLoad()` — only for that close-driven rebuild — overlays the explorer fields and browser URL onto the freshly fetched session objects so `buildGrid()`/`restoreExplorerPersistedTabs()` seed them, then routes each close-affected explorer pane through `restoreExplorerPaneFromClose()` (the tabbed viewer entry point plus a Preview-file reopen) instead of the plain-listing default. Non-close loads are unchanged (the snapshot is gated on `groupId` and cleared on consume and in `resetSessionView`). Browser scroll cannot survive a cross-origin iframe reload and is a documented limitation; URL is preserved. Covered by `test_terminals_page_close_preserves_sibling_pane_state` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-017
- Title: Improve Markdown preview visual hierarchy and callouts
- Priority: Cosmetic
- Status: Closed
- Area: `web/static/css/terminals.css`, `web/explorer.py`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `ui`, `markdown`, `tests`
- Reported: 2026-07-13
- Closed: 2026-07-16

Description:
Markdown previews are functional but visually plain, making headings, lists, notes, and other document structure harder to scan than they should be. The weak hierarchy is especially noticeable in longer README and documentation files, where users depend on highlighted titles, distinct bullet levels, and visible note or warning blocks.

Steps to reproduce:
1. Open an Explorer pane and select a Markdown file containing multiple heading levels, nested bullet or numbered lists, blockquotes, and note-like content.
2. Switch from Source to Preview.
3. Observe that the content has basic spacing and code/table treatment, but headings, list levels, and note content have limited visual distinction.

Expected behavior:
Markdown Preview should present a polished, theme-aware document surface with clearly differentiated heading levels, readable paragraph width and spacing, visible ordered/unordered list markers and nesting, styled links and separators, and distinctive accessible callouts for notes, tips, warnings, and important information. The result should remain readable in light and dark themes and at every supported editor zoom level.

Actual behavior / logs:
Code inspection confirmed `web/static/css/terminals.css` gave `.explorer-markdown-preview` basic padding, font sizing, and line height; applied only margin and line-height rules to `h1` through `h3`; gave lists only a shared bottom margin; and rendered all blockquotes with one muted left-border style. `_render_markdown_preview()` in `web/explorer.py` supported common Markdown extensions and sanitized semantic HTML, but defined no dedicated note/callout representation.

Resolution:
`_render_markdown_preview()` in `web/explorer.py` now runs a bounded `_augment_markdown_callouts()` step on the already-`bleach`-sanitized HTML, converting GitHub-style `> [!NOTE]/[!TIP]/[!IMPORTANT]/[!WARNING]/[!CAUTION]` blockquotes into `<div class="md-callout md-callout-{type}">` blocks with an accessible `md-callout-title` (stroke-`currentColor` SVG icon + label). Because the transform runs after sanitization and only injects a closed, backend-owned set of class names and icons, the bleach allowlist is unchanged and callout bodies stay sanitized; plain blockquotes and unknown keywords pass through untouched (adjacent callouts merge in Python-Markdown — a documented authoring edge case that degrades to a plain blockquote). `web/static/css/terminals.css` gives `.explorer-markdown-preview` a cohesive theme: differentiated `h1`–`h6` (dividers on `h1`/`h2`, muted `h5`/`h6`), nested list markers, styled links/rules, tables with a header tint and zebra rows, task-item and image handling, and the callout blocks. Callout accents/tints are new per-theme `--explorer-callout-*` tokens (mirroring the `--git-lane-*` pattern; no inline palette literals), and code/blockquote/table chrome reads shared `--md-preview-*` custom properties so it adapts to the ISSUE-2026-030 presets. Covered by `test_markdown_preview_renders_github_callouts`, `test_markdown_preview_leaves_plain_blockquote_untouched`, `test_markdown_preview_callout_sanitizes_body_and_keeps_nested_content`, `test_explorer_file_endpoint_emits_callout_html`, and `test_terminals_page_markdown_preview_hierarchy_and_callouts` in `tests/test_api.py`.

### Issue ID: ISSUE-2026-030
- Title: Add user-customizable Markdown preview appearance and presets
- Priority: Low
- Status: Closed
- Area: `web/static/css/terminals.css`, `web/static/js/terminals.js`, `tests/test_api.py`
- Assignee: Unassigned
- Tags: `file-explorer`, `markdown`, `settings`, `ui`, `tests`
- Reported: 2026-07-15
- Closed: 2026-07-16

Description:
The Explorer Markdown preview rendered with a single fixed appearance and offered no way for users to customize how it looks — font family, text size, or background/reading surface — or to pick from ready-made presets. Users who read long documentation in GridVibe could not switch to a serif or paper/sepia reading surface, a high-contrast surface, or a larger reading font. This is a user-facing customization request, distinct from ISSUE-2026-017's developer-defined default hierarchy and note callouts.

Steps to reproduce:
1. Open an Explorer pane and select a Markdown file, then switch from Source to Preview.
2. Inspect the preview toolbar/header for any font, background, theme, or preset controls.
3. Observe that the preview always used one fixed font and background, with only the shared editor zoom available, and no preset or reading-surface options.

Expected behavior:
Markdown Preview should let the user adjust its appearance — at minimum a small set of presets (for example default, paper/sepia, high-contrast, and a serif/reading variant) and/or explicit font-family and font-size controls. The chosen appearance should apply immediately to the active preview, remain within GridVibe's theme tokens, coexist with light/dark themes and the existing zoom control, persist across previews, and never weaken Markdown sanitization or the read-only guarantee.

Actual behavior / logs:
Code inspection confirmed `web/static/css/terminals.css` styled `.explorer-markdown-preview` with fixed padding, font, and background rules, and there was no client control, setting, or CSS-variable hook to change the preview font, background, or reading surface. `_render_markdown_preview()` in `web/explorer.py` emitted sanitized semantic HTML but carried no appearance metadata, and no `RuntimeConfig`/`/api/app-config` field or local preference stored a Markdown-preview appearance choice.

Resolution:
The preview surface now exposes two orthogonal, persisted axes driven entirely by CSS custom properties defined from tokens: a **reading-surface preset** (`default`, `paper`, `high-contrast`) and a **font family** (`system`, `serif`, `mono`). A new header control (`data-explorer-md-appearance`, shown only for previewable files) opens an in-page popover (`#explorer-md-menu`, WebView2-safe — no `window.*` — keyboard navigable with Arrow/Escape, radio `aria-checked` items, outside-click and Escape dismiss) mirroring the ISSUE-2026-028 context-menu pattern. `explorerMarkdownAppearance()` reads a bounded `localStorage` preference (`gridvibe.mdPreviewPreset` / `gridvibe.mdPreviewFont`, allowlist-validated with a safe default); `setExplorerMarkdownAppearance()` persists it and applies it live to every open preview through `applyExplorerMarkdownAppearanceToElement()` (`md-preset-*` / `md-font-*` classes remapping `--md-preview-*`). Both preview render paths (`renderExplorerFile()` and `updateExplorerFileInPlace()`) apply the saved appearance idempotently, so it survives reopens and coexists with the existing zoom control, search highlighting, and light/dark themes; paper and high-contrast are intentionally theme-independent reading surfaces with their literals confined to a single token block (no palette literals in rules). The sanitized rendering path and read-only contract are unchanged. Covered by `test_terminals_page_markdown_appearance_presets` in `tests/test_api.py`.

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
Added `_git_revert_path()` in `web/explorer.py` behind a new `POST /api/explorer/<id>/git/revert` route in `web/api.py`. For tracked files it performs the equivalent of `git restore --worktree -- <path>`, preserving any staged version; conflicted and no-unstaged-change paths are refused. A follow-up deliberately added the separate deletion workflow anticipated above: Git status uses `--untracked-files=all` so new directories appear as individual file rows, and an explicitly selected untracked file can be removed with `git clean -f -- <path>` after a stronger permanent-deletion confirmation. Directory paths remain refused, and bulk **Discard All** still never deletes untracked files. The Git sidebar renders the per-row control through `explorerGitCanRevert`, calls the reusable in-page confirm shell (`openGenericConfirmModal` / `#genericConfirmModal`), and reloads the open file/diff after success. Covered by the `test_explorer_git_revert_*` behavioral tests, the untracked-directory expansion test, and `test_terminals_page_explorer_git_revert_controls_are_present` in `tests/test_api.py`.

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
