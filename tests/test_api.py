import io
import json
import stat
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import api
from gridvibe_version import __version__


class FakeSftp:
    def __init__(self, entries):
        self.entries = entries
        self.closed = False

    def normalize(self, path):
        path = str(path or "/").replace("\\", "/")
        if not path.startswith("/") and not (len(path) >= 2 and path[1] == ":"):
            path = f"/srv/app/{path}"
        while "//" in path:
            path = path.replace("//", "/")
        return path.rstrip("/") or "/"

    def stat(self, path):
        normalized = self.normalize(path)
        if normalized not in self.entries:
            raise OSError("No such file")
        item = self.entries[normalized]
        return SimpleNamespace(
            st_mode=stat.S_IFDIR if item["type"] == "directory" else stat.S_IFREG,
            st_size=len(item.get("content", b"")),
            st_mtime=item.get("modified", 1000),
        )

    def listdir_attr(self, path):
        normalized = self.normalize(path)
        prefix = normalized.rstrip("/") + "/"
        results = []
        for entry_path, item in self.entries.items():
            if not entry_path.startswith(prefix):
                continue
            name = entry_path[len(prefix):]
            if "/" in name or not name:
                continue
            results.append(
                SimpleNamespace(
                    filename=name,
                    st_mode=stat.S_IFDIR if item["type"] == "directory" else stat.S_IFREG,
                    st_size=len(item.get("content", b"")),
                    st_mtime=item.get("modified", 1000),
                )
            )
        return results

    def open(self, path, _mode="rb"):
        normalized = self.normalize(path)
        if normalized not in self.entries:
            raise OSError("No such file")
        return io.BytesIO(self.entries[normalized].get("content", b""))

    def close(self):
        self.closed = True


class ApiRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path_patch = patch.object(
            api,
            "CONFIG_PATH",
            str(self.config_path),
        )
        self.config_path_patch.start()
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        self.saved_sessions_patch = patch.object(
            api,
            "SAVED_SESSIONS_PATH",
            str(self.saved_sessions_path),
        )
        self.saved_sessions_patch.start()
        api._refresh_runtime_config()
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        api.active_launch_options.update(
            {"connection_mode": "ssh", "layout": "grid", "terminal_count": 4}
        )
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()
        with api._agent_detection_cache_lock:
            api._agent_detection_cache.clear()
            api.client_joined_sessions.clear()
        with api._vosk_lock:
            api._vosk_ws_connections.clear()
        with api._whisper_audio_lock:
            api._whisper_audio_buffers.clear()
        api._whisper_model_instance = None
        cfg = api.load_config()
        self._saved_appearance = json.loads(json.dumps(cfg.get("appearance", {})))
        self._saved_voice_input = json.loads(json.dumps(cfg.get("voice_input", {})))
        self._saved_voice_prefs = cfg.pop("voice_prefs", None)
        api.save_config(cfg)
        api._refresh_runtime_config()

    def _create_explorer_session(self, repo_dir: Path) -> str:
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["sessions"][0]["session_id"]

    def tearDown(self):
        api.session_manager.reset_sessions()
        api.active_launch_options.update(
            {"connection_mode": "ssh", "layout": "grid", "terminal_count": 4}
        )
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()
            api.client_joined_sessions.clear()
        with api._vosk_lock:
            api._vosk_ws_connections.clear()
        with api._whisper_audio_lock:
            api._whisper_audio_buffers.clear()
        api._whisper_model_instance = None
        cfg = api.load_config()
        cfg["appearance"] = self._saved_appearance
        cfg["voice_input"] = self._saved_voice_input
        if self._saved_voice_prefs is not None:
            cfg["voice_prefs"] = self._saved_voice_prefs
        else:
            cfg.pop("voice_prefs", None)
        api.save_config(cfg)
        api._refresh_runtime_config()
        self.saved_sessions_patch.stop()
        self.config_path_patch.stop()
        api._refresh_runtime_config()

    def test_health_check_returns_service_metadata(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "status": "healthy",
                "service": "GridVibe",
                "version": __version__,
            },
        )

    def test_windows_launcher_prompts_for_missing_voice_dependencies(self):
        launcher = (Path(api.BASE_DIR) / "GridVibe.bat").read_text(encoding="utf-8")

        self.assertIn("Checking optional voice dependencies", launcher)
        self.assertIn("faster_whisper", launcher)
        self.assertIn("requirements-voice.txt", launcher)
        self.assertIn("choice /C YN", launcher)

    def test_launcher_page_exposes_agent_startup_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("const AGENT_OPTIONS = [", html)
        self.assertIn('data-command-mode="agent"', html)
        self.assertIn("function normalizeTerminalCommandUi(terminal)", html)
        self.assertIn("Custom Agent", html)

    def test_launcher_page_hides_windows_shell_options_on_posix(self):
        with patch.object(api.os, "name", "posix"):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("const LOCAL_WINDOWS_SHELLS_AVAILABLE = false;", html)
        self.assertNotIn("Prefer WSL", html)
        self.assertNotIn("Use PowerShell", html)
        self.assertNotIn("Ubuntu Distro", html)

    def test_launcher_page_shows_windows_shell_options_on_windows(self):
        with patch.object(api.os, "name", "nt"):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("const LOCAL_WINDOWS_SHELLS_AVAILABLE = true;", html)
        self.assertIn("Prefer WSL", html)
        self.assertIn("Use PowerShell", html)
        self.assertIn("Ubuntu Distro", html)

    def test_launcher_page_exposes_agent_preflight_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("/api/agent-preflight", html)
        self.assertIn("function queueAgentPreflight(row)", html)
        self.assertIn("function scheduleAgentPreflight(row, delayMs = 180)", html)
        self.assertIn('class="agent-preflight-disclosure"', html)
        self.assertIn('status-installed', html)
        self.assertIn('\"value\": \"claude\"', html)

    def test_launcher_page_exposes_check_for_updates_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="checkUpdatesBtn"', html)
        self.assertIn('title="Check for updates"', html)
        self.assertIn("async function checkForUpdates()", html)
        self.assertIn("/api/app-update", html)
        self.assertIn("window.pywebview?.api?.restart_application", html)

    def test_launcher_page_exposes_app_settings_modal_and_cog_button(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="appSettingsBtn"', html)
        self.assertIn("function openAppSettings()", html)
        self.assertIn("function saveAppSettings()", html)
        self.assertIn('id="appSettingsModal"', html)
        self.assertIn('/api/app-config', html)
        self.assertIn('id="appTheme"', html)
        self.assertIn('id="appVoiceEngine"', html)
        self.assertIn('id="appWhisperDevice"', html)
        self.assertIn('id="appVoiceProfile"', html)
        self.assertIn('id="appVoiceDevice"', html)
        self.assertIn('id="appVoicePttEnabled"', html)
        self.assertIn('id="appVoicePttKeybind"', html)
        self.assertIn("function refreshLauncherMicrophones()", html)
        self.assertIn('/api/voice-prefs', html)
        self.assertIn('<select id="appWhisperModel">', html)
        self.assertIn('<option value="base">base</option>', html)
        self.assertIn('<option value="large-v3-turbo">large-v3-turbo</option>', html)

    def test_launcher_page_resets_terminal_setup_when_connection_target_changes(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("function resetTerminalSetupIfTargetChanged", html)
        self.assertIn("buildTerminalRows(selectedCount, buildDefaultTerminalDrafts());", html)
        self.assertIn("resetTerminalSetupIfTargetChanged(connectionMode, collectModeInputs());", html)
        self.assertIn("bindModeFieldInteractions();", html)

    def test_terminals_page_empty_state_launch_button_reuses_settings_handler(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(
            '<a href="/" onclick="return goToSettings(event)">Launch terminals →</a>',
            html,
        )

    def test_terminals_page_preserves_fullscreen_for_native_new_session_focus(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        go_to_settings_start = html.index("async function goToSettings(event)")
        open_launcher_index = html.index(
            "window.pywebview?.api?.open_launcher_window",
            go_to_settings_start,
        )
        reset_index = html.index("await resetFullscreenState();", go_to_settings_start)

        self.assertLess(open_launcher_index, reset_index)

    def test_terminals_page_uses_icon_only_settings_button(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('class="btn btn-neutral btn-icon settings-window-btn"', html)
        self.assertIn('aria-label="Open settings"', html)
        self.assertIn('class="vibe-flow-icon"', html)

    def test_terminals_page_exposes_per_terminal_refresh_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-terminal-refresh="${i}"', html)
        self.assertIn("function setTerminalRefreshState(index, refreshing)", html)
        self.assertIn("async function refreshTerminalDisplay(index)", html)

    def test_terminals_page_exposes_session_mode_switch_controls(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-session-mode-toggle="${i}"', html)
        self.assertIn("async function switchSessionPaneMode(index)", html)
        self.assertIn("body.directory = getExplorerSelectedDirectory(index);", html)
        self.assertIn("`/api/sessions/${encodeURIComponent(sessionId)}/mode`", html)
        self.assertIn("hasMatchingSessionViews(sessionIds, terminals, data.sessions)", html)
        self.assertIn("pendingModeSwitchSessionIds.add(sessionId);", html)
        self.assertIn("replaceSessionPaneMode(index, data)", html)
        self.assertIn("return ['ssh', 'wsl'].includes(session?.mode) && session?.startup_mode === 'explorer';", html)
        replace_start = html.index("function replacePaneWithExplorer(index, session)")
        replace_end = html.index("function replacePaneWithTerminal(index, session)", replace_start)
        self.assertIn("loadExplorerPane(index, null, { force: true });", html[replace_start:replace_end])
        self.assertNotIn("loadExplorerPane(index, '', { force: true });", html[replace_start:replace_end])
        switch_start = html.index("async function switchSessionPaneMode(index)")
        switch_end = html.index("async function closeSplitPane(index)", switch_start)
        self.assertNotIn("teardownCurrentGrid();", html[switch_start:switch_end])

    def test_terminals_page_explorer_refresh_requires_initial_navigation_or_force(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        explorer_start = html.index("async function loadExplorerPane(index, path = null")
        explorer_end = html.index("/* ─────────────────────────────────────────────", explorer_start)
        explorer_html = html[explorer_start:explorer_end]

        self.assertIn(
            "async function loadExplorerPane(index, path = null, { force = false, showLoading = true } = {})",
            explorer_html,
        )
        self.assertIn("const isNavigation = path !== null;", explorer_html)
        self.assertIn(
            "if (pane._attached && !force && !isNavigation) {\n            return true;\n        }",
            explorer_html,
        )
        self.assertIn("if (showLoading)", explorer_html)

    def test_terminals_page_manual_refresh_keeps_open_explorer_file(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        explorer_refresh_start = html.index("async function refreshExplorerPane(index)")
        explorer_refresh_end = html.index("async function loadExplorerPane(index, path = null", explorer_refresh_start)
        explorer_refresh_html = html[explorer_refresh_start:explorer_refresh_end]
        refresh_start = html.index("async function refreshTerminalDisplay(index)")
        refresh_end = html.index("logSessionWindowAction('Refreshing terminal display'", refresh_start)
        refresh_html = html[refresh_start:refresh_end]

        self.assertIn(
            "return openExplorerFile(index, pane._explorerFilePath, { showLoading: false, preserveScroll: true });",
            explorer_refresh_html,
        )
        self.assertIn("return loadExplorerPane(index, null, { force: true });", explorer_refresh_html)
        self.assertIn("await refreshExplorerPane(index);", refresh_html)
        self.assertIn("function captureExplorerFileScroll(index)", html)
        self.assertIn("function restoreExplorerFileScroll(index, state)", html)
        self.assertIn("function updateExplorerFileInPlace(index, data, scrollState = null)", html)
        self.assertIn("updateExplorerFileInPlace(index, data, scrollState)", html)
        self.assertIn(".explorer-list.file-view", html)
        self.assertIn("list.classList.add('file-view');", html)
        self.assertIn("listScrollTop: list.scrollTop", html)
        self.assertIn("list.scrollTop = state.listScrollTop || 0;", html)
        self.assertIn("wasAtBottom: maxScrollTop > 0 && panel.scrollTop >= maxScrollTop - 2", html)
        self.assertIn("panel.scrollTop = panelState.wasAtBottom", html)
        self.assertIn("window.setTimeout(applyScroll, 80);", html)
        self.assertIn("async function syncExplorerPane(index)", html)
        self.assertIn("if (pane?._explorerMode === 'file' && pane._explorerFilePath) {\n            return true;\n        }", html)
        self.assertIn("syncExplorerPane(i);", html)
        self.assertIn("syncExplorerPane(index);", html)

    def test_terminals_page_explorer_theme_defaults_to_dark(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("function hasExplorerThemeOverride(key = '')", html)
        self.assertIn('[data-theme="dark"]', html)
        self.assertIn("--explorer-bg: #0f141b;", html)
        self.assertIn("card.dataset.explorerThemeSource = hasExplorerThemeOverride(explorerThemeKey) ? 'override' : 'default';", html)
        self.assertIn("if (card.dataset.explorerThemeSource === 'override')", html)
        self.assertIn("updateExplorerThemeButton(explorerThemeButton, card.dataset.explorerTheme || 'dark');", html)
        self.assertIn("function syncDefaultExplorerThemes()", html)
        self.assertIn("syncDefaultExplorerThemes();", html)
        self.assertNotIn("store.default || currentResolvedTheme()", html)

    def test_terminals_page_explorer_source_view_wraps_and_highlights_code(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("white-space: pre-wrap;", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn("function highlightExplorerCode(content, language, searchRanges = [])", html)
        self.assertIn("const EXPLORER_LANGUAGE_BY_EXTENSION = Object.freeze({", html)
        self.assertIn("'.py': 'python'", html)
        self.assertIn("'.go': 'go'", html)
        self.assertIn("'.c': 'c'", html)

    def test_terminals_page_explorer_file_search_is_client_side_and_safe(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-explorer-search-input="${index}"', html)
        self.assertIn("function explorerFindRanges(content, query)", html)
        self.assertIn("function explorerMarkedEscHtml(text, absoluteStart = 0, searchRanges = [])", html)
        self.assertIn("function markExplorerSearchInElement(root, query, activeIndex = 0)", html)
        self.assertIn("document.createTreeWalker(", html)
        self.assertIn("node.replaceWith(fragment);", html)
        self.assertIn("code.innerHTML = highlightExplorerCode(", html)
        self.assertIn("function findExplorerSearchTargetIndex()", html)
        self.assertIn("event.code !== 'KeyF'", html)
        self.assertNotIn("/api/explorer-search", html)

    def test_terminals_page_exposes_per_terminal_clear_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-terminal-clear="${i}"', html)
        self.assertIn("function setTerminalClearState(index, clearing)", html)
        self.assertIn("async function clearTerminalDisplay(index)", html)

    def test_terminals_page_rebuilds_reused_group_views_when_session_ids_change(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("function hasMatchingSessionIds(existingIds, sessions)", html)
        self.assertIn("function hasMatchingSessionViews(existingIds, existingTerminals, sessions)", html)
        self.assertIn("&& hasMatchingSessionViews(sessionIds, terminals, data.sessions)", html)
        self.assertIn(
            "hasMatchingSessionViews(cached.sessionIds || [], cached.terminals || [], data.sessions)",
            html,
        )
        self.assertIn(
            "const sessionViewsChanged = !hasMatchingSessionViews(sessionIds, terminals, data.sessions);",
            html,
        )

    def test_terminals_page_uses_global_voice_capture_preferences_and_worklet(self):
        with patch.object(api, "voice_engine", "whisper"), patch.object(
            api, "whisper_model", "base"
        ):
            response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-voice-engine="whisper"', html)
        self.assertNotIn('data-terminal-voice-settings="${i}"', html)
        self.assertNotIn('data-terminal-voice-profile="${i}"', html)
        self.assertNotIn('data-terminal-voice-device="${i}"', html)
        self.assertIn("VOICE_CAPTURE_PROFILES = Object.freeze({", html)
        self.assertIn("const VOICE_ENGINE = PAGE_DATASET.voiceEngine || 'vosk';", html)
        self.assertIn("new AudioWorkletNode(audioCtx, 'gridvibe-voice-processor'", html)
        self.assertIn("getSupportedConstraints()", html)
        self.assertIn("getCapabilities()", html)
        self.assertIn("getSettings()", html)
        self.assertIn("voice-capture-worklet.js", html)
        self.assertIn("base", html)

    def test_terminals_page_preflights_voice_backend_before_microphone_start(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        start = html.index("async function _startVoice(index) {")
        end = html.index("if (!navigator.mediaDevices?.getUserMedia)", start)
        startup_html = html[start:end]

        self.assertIn("await _loadVoiceServiceStatus();", startup_html)
        self.assertIn("const backendUnavailableMessage = _voiceBackendUnavailableMessage();", startup_html)
        self.assertIn("_setVoicePanelStatus(index, backendUnavailableMessage);", startup_html)

    def test_terminals_page_server_voice_errors_cleanup_without_stop_echo(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        handler_start = html.index("socket.on('voice_status', async ({ session_id, status, message }) => {")
        handler_end = html.index("return;", handler_start)
        error_handler = html[handler_start:handler_end]

        self.assertIn("await _stopVoice(index, { notifyServer: false });", error_handler)
        self.assertIn("async function _stopVoice(index, { notifyServer = true } = {})", html)

    def test_terminals_page_uses_voice_toggle_without_per_terminal_settings_panel(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-terminal-voice="${i}"', html)
        self.assertNotIn('id="tvoice-panel-toggle-${i}"', html)
        self.assertNotIn('id="tvoice-settings-${i}"', html)
        self.assertNotIn("settings: document.getElementById(`tvoice-settings-${index}`),", html)

    def test_terminals_page_uses_global_push_to_talk_preferences(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('data-terminal-voice-ptt="${i}"', html)
        self.assertNotIn('data-terminal-voice-ptt-keybind="${i}"', html)
        self.assertNotIn('id="tvoice-ptt-toggle-${i}"', html)
        self.assertNotIn('id="tvoice-ptt-keybind-${i}"', html)
        self.assertIn("function _matchesPttKeybind(event, keybind)", html)
        self.assertIn("pttEnabled: false", html)
        self.assertIn("pttKeybind: ''", html)

    def test_terminals_page_removes_enter_and_line_clear_shortcuts(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('class="terminal-action-btn terminal-shortcut-btn"', html)
        self.assertNotIn('data-terminal-enter="${i}"', html)
        self.assertNotIn('data-terminal-clearline="${i}"', html)
        self.assertNotIn("async function _sendEnterShortcut(index)", html)

    def test_terminals_page_places_voice_control_after_clear_button(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        clear_index = html.index('data-terminal-clear="${i}"')
        voice_index = html.index('data-terminal-voice-control="${i}"')

        self.assertLess(clear_index, voice_index)

    def test_terminals_page_refreshes_only_one_terminal_by_replaying_its_buffer(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        refresh_start = html.index("async function refreshTerminalDisplay(index)")
        self.assertIn("terminal.term.reset();", html[refresh_start:])
        self.assertIn("emitTerminalResize(index, true);", html[refresh_start:])
        self.assertIn("socket.emit('leave_session', { session_id: sessionId });", html[refresh_start:])
        self.assertIn("socket.emit('join_session', { session_id: sessionId });", html[refresh_start:])

    def test_terminals_page_uses_updated_session_action_labels_and_styles(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('aria-label="Refresh all"', html)
        self.assertIn('class="refresh-all-icon"', html)
        self.assertNotIn(">Refresh all</button>", html)
        self.assertNotIn("Close Session</button>", html)
        self.assertIn("closeButton.className = 'session-tab-close';", html)
        self.assertIn("closeSessionGroup(group.group_id);", html)
        self.assertIn(".session-tab.settings {", html)
        self.assertIn("border-color: #00c46e;", html)
        self.assertIn("settingsButton.textContent = '+ New Session';", html)

    def test_terminals_page_numbers_session_tabs_and_exposes_safe_shortcut(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(".session-tab-number {", html)
        self.assertIn("sessionGroups.forEach((group, index) => {", html)
        self.assertIn("const tabNumber = index + 1;", html)
        self.assertIn("number.className = 'session-tab-number';", html)
        self.assertIn("tabButton.appendChild(number);", html)
        self.assertIn("function getSessionGroupByNumber(number)", html)
        self.assertIn("return sessionGroups[number - 1] || null;", html)
        self.assertIn("function isEditableShortcutTarget(target)", html)
        self.assertIn("/^[1-9]$/.test(event.key)", html)
        self.assertIn("isEditableShortcutTarget(event.target)", html)
        self.assertIn("switchGroup(targetGroup.group_id);", html)

    def test_terminals_page_uses_icon_only_green_fullscreen_button(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('class="btn btn-success btn-icon"', html)
        self.assertIn('id="fullscreenBtn"', html)
        self.assertIn('title="Enter fullscreen"', html)
        self.assertIn('aria-label="Enter fullscreen"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn('>&#9974;</button>', html)
        self.assertIn("button.innerHTML = active ? '&#10005;' : '&#9974;';", html)
        self.assertIn("button.title = label;", html)
        self.assertIn("button.setAttribute('aria-label', label);", html)
        self.assertIn("button.setAttribute('aria-pressed', active ? 'true' : 'false');", html)

    def test_terminals_page_exposes_max_surface_mode(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        grid_css = html[html.index("#terminalsGrid {"):html.index("/* Layout classes */")]
        self.assertIn("width: 100%;", grid_css)
        self.assertNotIn("min(1800px, 100%)", grid_css)
        self.assertIn('id="surfaceModeBtn"', html)
        self.assertIn('aria-label="Max surface"', html)
        self.assertIn('<rect x="4" y="4" width="16" height="16" rx="2.5"', html)
        self.assertIn("const SURFACE_MODE_STORAGE_KEY = 'gridvibe.terminalSurfaceMode';", html)
        self.assertIn("document.body.classList.toggle('surface-max', active);", html)
        self.assertIn("redrawAttachedTerminals(attachedIndices, { forceResize: true });", html)

    def test_terminals_page_exposes_collapsible_topbar(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('class="topbar" id="terminalTopbar"', html)
        self.assertIn('class="session-bar"', html)
        self.assertIn('id="topbarToggleBtn"', html)
        self.assertIn('aria-controls="terminalTopbar"', html)
        self.assertIn("const TOPBAR_VISIBILITY_STORAGE_KEY = 'gridvibe.terminalTopbarVisibility';", html)
        self.assertIn("document.body.classList.toggle('topbar-collapsed', !shouldShow);", html)
        self.assertIn("path.setAttribute('d', visible ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6');", html)
        self.assertIn("applyTopbarVisibility(getStoredTopbarVisible());", html)

    def test_terminals_page_buttons_use_session_color_frames(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("--session-color", html)
        self.assertIn("--session-color-dim", html)
        self.assertIn("var(--session-color-dim, var(--t-border-tab))", html)
        self.assertIn("var(--session-color, var(--t-accent))", html)
        self.assertIn("tabColourForGroup(activeGroupId)", html)

    def test_terminals_page_clear_sends_shell_command_and_purges_replay_buffer(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        clear_start = html.index("async function clearTerminalDisplay(index)")
        self.assertIn("terminal.term.reset();", html[clear_start:])
        self.assertIn("terminal.term.clear();", html[clear_start:])
        self.assertIn("const clearCommand = getTerminalClearCommand(index);", html[clear_start:])
        self.assertIn("socket.emit('clear_terminal_buffer', { session_id: sessionId });", html[clear_start:])
        self.assertIn("socket.emit('terminal_input', { session_id: sessionId, data: clearCommand });", html[clear_start:])

    def test_terminals_page_redraws_attached_terminals_after_group_switch_rejoin(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("async function redrawAttachedTerminals(indices, { forceResize = false, isCurrent = null } = {})", html)
        self.assertIn("async function redrawAttachedTerminalsLikeFullscreen(indices, { isCurrent = null } = {})", html)

        initial_load_start = html.index("async function initialLoad()")
        join_index = html.index("socket.emit('join_session', { session_id: session.session_id });", initial_load_start)
        redraw_index = html.index("await redrawAttachedTerminalsLikeFullscreen(attachedIndices, {", initial_load_start)
        soft_redraw_index = html.index("await redrawAttachedTerminals(attachedIndices, {", initial_load_start)

        self.assertLess(join_index, redraw_index)
        self.assertLess(soft_redraw_index, redraw_index)
        self.assertIn("if (usingCurrentView || restoredFromCache)", html[initial_load_start:redraw_index])
        self.assertIn("forceResize: false", html[soft_redraw_index:redraw_index])
        self.assertIn("await redrawPass({ dispatchResize: true });", html)
        self.assertIn("await redrawPass({ delayMs: 90, dispatchResize: true });", html)
        self.assertIn("const stillCurrent = () => loadToken === activeLoadToken && requestedGroupId === activeGroupId;", html[initial_load_start:redraw_index])

    def test_terminals_page_caches_group_views_across_switches(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("let cachedGroupViews = new Map();", html)
        self.assertIn("function cacheVisibleGroupView(groupId = visibleGroupId)", html)
        self.assertIn("function restoreCachedGroupView(groupId)", html)
        self.assertIn("cacheVisibleGroupView(visibleGroupId);", html)
        self.assertIn("restoredFromCache = restoreCachedGroupView(requestedGroupId);", html)
        self.assertIn("function captureTerminalViewportState(terminal)", html)
        self.assertIn("function restoreTerminalViewportState(terminal, state)", html)
        self.assertIn("captureCachedPaneUiState();", html)
        self.assertIn("restoreCachedPaneUiState();", html)

    def test_terminals_page_routes_terminal_output_by_session_across_cached_groups(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("let sessionRouteMap = new Map();", html)
        self.assertIn("function resolveSessionTarget(sessionId)", html)
        self.assertIn("const target = resolveSessionTarget(session_id);", html)
        self.assertIn("if (!target.active) {", html)
        self.assertIn("target.terminal.term.write(data);", html)

    def test_create_sessions_requires_sessions_field(self):
        response = self.client.post("/api/sessions", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Missing 'sessions' in request body"},
        )

    def test_create_sessions_rejects_more_than_max_sessions(self):
        session_config = {
            "host": "127.0.0.1",
            "directory": "/tmp",
            "username": "root",
        }
        response = self.client.post(
            "/api/sessions",
            json={"sessions": [session_config] * (api.max_sessions + 1)},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": f"Maximum {api.max_sessions} sessions allowed"},
        )

    def test_voice_status_endpoint_includes_engine_model_and_language(self):
        with patch.object(api, "voice_engine", "whisper"), patch.object(
            api, "whisper_model", "base"
        ):
            response = self.client.get("/api/voice-status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["engine"], "whisper")
        self.assertEqual(payload["model"], "base")
        self.assertEqual(payload["language"], "en-US")
        self.assertIn("service_url", payload)
        self.assertIn("service_running", payload)
        self.assertIn("status_message", payload)

    def test_voice_status_endpoint_reports_vosk_metadata(self):
        with patch.object(api, "voice_engine", "vosk"), patch.object(
            api, "vosk_model", "vosk-model-en-us-0.22"
        ), patch.object(api, "_vosk_service_reachable", return_value=False):
            response = self.client.get("/api/voice-status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["engine"], "vosk")
        self.assertEqual(payload["model"], "vosk-model-en-us-0.22")
        self.assertEqual(payload["service_url"], api.vosk_service_url)
        self.assertFalse(payload["service_running"])

    def test_voice_status_endpoint_reports_missing_whisper_dependency(self):
        with patch.object(api, "voice_engine", "whisper"), patch.object(
            api, "WhisperModel", None
        ), patch.object(api, "np", object()):
            response = self.client.get("/api/voice-status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["engine_available"])
        self.assertIn("faster-whisper", payload["status_message"])
        self.assertIn("pip install -r requirements-voice.txt", payload["status_message"])

    def test_app_config_endpoint_returns_settings_payload(self):
        response = self.client.get("/api/app-config")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("appearance", payload)
        self.assertIn("theme", payload["appearance"])
        self.assertIn("voice_input", payload)
        self.assertIn("engine", payload["voice_input"])
        self.assertIn("whisper_model", payload["voice_input"])

    def test_app_config_endpoint_persists_theme_and_voice_settings(self):
        response = self.client.post(
            "/api/app-config",
            json={
                "appearance": {
                    "theme": "light",
                },
                "voice_input": {
                    "engine": "vosk",
                    "vosk_model": "vosk-model-small-en-us-0.15",
                    "language": "en-GB",
                    "whisper_model": "base",
                    "whisper_device": "cpu",
                    "whisper_compute_type": "int8",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["appearance"]["theme"], "light")
        self.assertEqual(payload["voice_input"]["engine"], "vosk")
        self.assertEqual(payload["voice_input"]["vosk_model"], "vosk-model-small-en-us-0.15")
        self.assertEqual(payload["voice_input"]["language"], "en-GB")
        cfg = api.load_config()
        self.assertEqual(cfg["appearance"]["theme"], "light")
        self.assertEqual(cfg["voice_input"]["engine"], "vosk")
        self.assertEqual(cfg["voice_input"]["vosk_model"], "vosk-model-small-en-us-0.15")

    def test_app_config_endpoint_rejects_unknown_whisper_model(self):
        response = self.client.post(
            "/api/app-config",
            json={
                "voice_input": {
                    "engine": "whisper",
                    "whisper_model": "not-a-model",
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["voice_input"]["whisper_model"], "base")

    def test_agent_preflight_endpoint_returns_installed_when_binary_exists(self):
        with patch.object(
            api,
            "_detect_agent_binary",
            return_value={
                "found": True,
                "path": "/usr/local/bin/codex",
                "command": "command -v codex",
                "error": "",
            },
        ):
            response = self.client.post(
                "/api/agent-preflight",
                json={
                    "agent": "codex",
                    "connection_mode": "wsl",
                    "wsl": {"distribution": "", "username": "", "default_dir": "/tmp"},
                    "terminal": {"use_wsl": False, "use_powershell": False, "distribution": ""},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["agent"], "codex")
        self.assertEqual(payload["status"], "installed")
        self.assertEqual(payload["status_label"], "Installed")
        self.assertEqual(payload["detection"]["path"], "/usr/local/bin/codex")

    def test_agent_preflight_endpoint_returns_missing_with_install_guidance(self):
        with patch.object(
            api,
            "_detect_agent_binary",
            return_value={
                "found": False,
                "path": "",
                "command": "command -v kilo",
                "error": "",
            },
        ), patch.object(
            api,
            "_select_install_option",
            return_value=(
                {"label": "npm", "command": "npm install -g @kilocode/cli", "manual_only": False},
                [],
            ),
        ):
            response = self.client.post(
                "/api/agent-preflight",
                json={
                    "agent": "kilo",
                    "connection_mode": "wsl",
                    "wsl": {"distribution": "", "username": "", "default_dir": "/tmp"},
                    "terminal": {"use_wsl": False, "use_powershell": False, "distribution": ""},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "missing")
        self.assertEqual(payload["install"]["label"], "npm")
        self.assertEqual(payload["install"]["command"], "npm install -g @kilocode/cli")

    def test_agent_preflight_endpoint_treats_wsl_install_prerequisites_as_advisory(self):
        with patch.object(
            api,
            "_detect_agent_binary",
            return_value={
                "found": False,
                "path": "",
                "command": "command -v kilo",
                "error": "",
            },
        ), patch.object(
            api,
            "_select_install_option",
            return_value=(
                {"label": "npm", "command": "npm install -g @kilocode/cli", "manual_only": False},
                ["npm is required for the Linux or WSL install path."],
            ),
        ), patch.object(
            api,
            "_resolve_agent_target",
            return_value={
                "connection_mode": "wsl",
                "environment_key": "wsl_linux",
                "shell_kind": "wsl",
                "distribution": "Ubuntu",
                "username": "",
            },
        ):
            response = self.client.post(
                "/api/agent-preflight",
                json={
                    "agent": "kilo",
                    "connection_mode": "wsl",
                    "wsl": {"distribution": "", "username": "", "default_dir": "/tmp"},
                    "terminal": {"use_wsl": True, "use_powershell": False, "distribution": ""},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "missing")
        self.assertEqual(payload["install"]["command"], "npm install -g @kilocode/cli")
        self.assertEqual(
            payload["missing_prerequisites"],
            ["npm is required for the Linux or WSL install path."],
        )

    def test_agent_preflight_endpoint_returns_manual_install_for_detect_only_ssh_targets(self):
        with patch.object(
            api,
            "_detect_agent_binary",
            return_value={
                "found": False,
                "path": "",
                "command": "command -v claude",
                "error": "",
            },
        ):
            response = self.client.post(
                "/api/agent-preflight",
                json={
                    "agent": "claude",
                    "connection_mode": "ssh",
                    "ssh": {"host": "example.com", "username": "ubuntu", "password": "", "port": 22},
                    "terminal": {"use_wsl": False, "use_powershell": False, "distribution": ""},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "needs_manual_install")
        self.assertEqual(payload["target"]["environment_key"], "ssh")

    def test_posix_detection_command_uses_fast_path_before_interactive_bash(self):
        command = api._build_posix_detection_command("codex")

        self.assertLess(command.index('command -v "$TF_BINARY"'), command.index("bash -ilc"))

    def test_agent_preflight_endpoint_reuses_recent_local_detection(self):
        with patch.object(
            api,
            "_detect_agent_binary",
            return_value={
                "found": True,
                "path": "/usr/local/bin/codex",
                "command": "command -v codex",
                "error": "",
            },
        ) as detect_agent_binary, patch.object(
            api,
            "_select_install_option",
            return_value=({}, []),
        ):
            first_response = self.client.post(
                "/api/agent-preflight",
                json={
                    "agent": "codex",
                    "connection_mode": "wsl",
                    "wsl": {"distribution": "", "username": "", "default_dir": "/tmp"},
                    "terminal": {"use_wsl": False, "use_powershell": False, "distribution": ""},
                },
            )
            second_response = self.client.post(
                "/api/agent-preflight",
                json={
                    "agent": "codex",
                    "connection_mode": "wsl",
                    "wsl": {"distribution": "", "username": "", "default_dir": "/tmp"},
                    "terminal": {"use_wsl": False, "use_powershell": False, "distribution": ""},
                },
            )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(detect_agent_binary.call_count, 1)

    def test_parse_posix_detection_output_accepts_aliases_without_paths(self):
        payload = api._parse_posix_detection_output(
            "copilot",
            "__TF_FOUND__\n__TF_KIND__:alias\n__TF_PATH__:\n__TF_HEAD__:\n",
            "type copilot",
        )

        self.assertTrue(payload["found"])
        self.assertEqual(payload["kind"], "alias")
        self.assertEqual(payload["path"], "alias copilot")

    def test_parse_posix_detection_output_rejects_html_wrapper_payloads(self):
        payload = api._parse_posix_detection_output(
            "claude",
            "__TF_FOUND__\n__TF_KIND__:file\n__TF_PATH__:/home/ubuntu/.local/bin/claude\n__TF_HEAD__:\"<!DOCTYPE html><html lang=\"en-US\">\"\n",
            "type claude",
        )

        self.assertFalse(payload["found"])
        self.assertTrue(payload["failed"])
        self.assertIn("HTML page", payload["error"])

    def test_detect_wsl_command_accepts_alias_probe_output(self):
        completed = SimpleNamespace(returncode=0, stdout="__TF_FOUND__\n__TF_KIND__:alias\n__TF_PATH__:\n__TF_HEAD__:\n", stderr="")

        with patch.object(api.os, "name", "nt"), patch.object(api, "_find_wsl_executable", return_value="wsl.exe"), patch.object(
            api.subprocess,
            "run",
            return_value=completed,
        ) as run_mock:
            detected = api._detect_wsl_command("copilot", "Ubuntu")

        self.assertTrue(detected["found"])
        self.assertEqual(detected["path"], "alias copilot")
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:3], ["wsl.exe", "--distribution", "Ubuntu"])
        self.assertEqual(command[3], "--exec")
        self.assertEqual(command[4], "bash")
        self.assertIn("TF_LOGIN_SHELL", command[-1])
        self.assertIn("-ilc", command[-1])

    def test_detect_ssh_command_uses_interactive_bash_probe(self):
        stdout = MagicMock()
        stderr = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"__TF_FOUND__\n__TF_KIND__:alias\n__TF_PATH__:\n__TF_HEAD__:\n"
        stderr.read.return_value = b""
        client = MagicMock()
        client.exec_command.return_value = (None, stdout, stderr)
        fake_paramiko = SimpleNamespace(
            SSHClient=MagicMock(return_value=client),
            AutoAddPolicy=MagicMock(return_value=object()),
        )

        with patch.object(api, "paramiko", fake_paramiko):
            detected = api._detect_ssh_command(
                "copilot",
                {"host": "example.com", "username": "ubuntu", "password": "", "port": 22},
            )

        self.assertTrue(detected["found"])
        self.assertEqual(detected["path"], "alias copilot")
        self.assertIn("TF_LOGIN_SHELL", client.exec_command.call_args.args[0])
        self.assertIn("-ilc", client.exec_command.call_args.args[0])

    def test_resolve_agent_target_passes_blank_distribution_when_wsl_distro_is_unspecified(self):
        payload = {
            "connection_mode": "wsl",
            "wsl": {"distribution": "", "username": "", "default_dir": "C:/repo"},
            "terminal": {"use_wsl": True, "use_powershell": False, "distribution": ""},
        }

        with patch.object(api.os, "name", "nt"):
            target = api._resolve_agent_target(payload)

        self.assertEqual(target["environment_key"], "wsl_linux")
        self.assertEqual(target["shell_kind"], "wsl")
        self.assertEqual(target["distribution"], "")

    def test_create_sessions_clears_known_agent_command_when_launch_preflight_fails(self):
        sessions_payload = {
            "connection_mode": "ssh",
            "layout": "single",
            "sessions": [
                {
                    "host": "example.com",
                    "username": "ubuntu",
                    "port": 22,
                    "title": "Claude",
                    "directory": "/home/ubuntu/project",
                    "initial_command": "claude",
                }
            ],
        }

        with patch.object(
            api,
            "_agent_preflight_payload",
            return_value={
                "status": "check_failed",
                "message": "claude resolves to an HTML page instead of a working CLI.",
            },
        ), patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(
            body["warnings"],
            ["Claude: claude resolves to an HTML page instead of a working CLI. Startup command cleared."],
        )
        start_task.assert_called_once()
        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.initial_command, "")

    def test_agent_preflight_endpoint_rejects_unknown_agent(self):
        response = self.client.post("/api/agent-preflight", json={"agent": "unknown"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Unknown agent selection"})

    def test_load_config_falls_back_to_default_config_when_local_config_missing(self):
        default_path = Path(self.temp_dir.name) / "default_config.json"
        default_path.write_text(
            json.dumps({"voice_input": {"engine": "whisper", "whisper_model": "base"}}),
            encoding="utf-8",
        )

        missing_path = Path(self.temp_dir.name) / "missing-config.json"
        with patch.object(api, "DEFAULT_CONFIG_PATH", str(default_path)):
            cfg = api.load_config(str(missing_path))

        self.assertEqual(cfg["voice_input"]["engine"], "whisper")
        self.assertEqual(cfg["voice_input"]["whisper_model"], "base")

    def test_voice_prefs_get_returns_defaults(self):
        response = self.client.get("/api/voice-prefs")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["profile"], "laptop")
        self.assertEqual(payload["deviceId"], "")
        self.assertFalse(payload["pttEnabled"])
        self.assertEqual(payload["pttKeybind"], "")

    def test_voice_prefs_post_persists_and_returns_updated(self):
        self.client.post(
            "/api/voice-prefs",
            json={"profile": "headset", "pttEnabled": True, "pttKeybind": "Ctrl+M"},
        )

        response = self.client.get("/api/voice-prefs")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["profile"], "headset")
        self.assertTrue(payload["pttEnabled"])
        self.assertEqual(payload["pttKeybind"], "Ctrl+M")

    def test_voice_prefs_post_rejects_invalid_payload(self):
        response = self.client.post("/api/voice-prefs", data="not json")

        self.assertEqual(response.status_code, 400)

    def test_terminals_page_loads_voice_prefs_from_server(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("_loadVoicePrefsFromServer", html)
        self.assertIn("fetch('/api/voice-prefs'", html)

    def test_app_update_endpoint_returns_self_update_payload(self):
        update_payload = {
            "updated": True,
            "restart_required": True,
            "branch": "main",
            "behind_count": 2,
            "ahead_count": 0,
            "previous_commit": "abc1234",
            "current_commit": "def5678",
            "message": "Updated 'main' from abc1234 to def5678.",
        }

        with patch.object(api, "perform_self_update", return_value=update_payload):
            response = self.client.post("/api/app-update")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), update_payload)

    def test_app_update_endpoint_surfaces_expected_update_errors(self):
        with patch.object(
            api,
            "perform_self_update",
            side_effect=api.AppUpdateError("Local changes are present.", 409),
        ):
            response = self.client.post("/api/app-update")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {"error": "Local changes are present."})

    def test_perform_self_update_reports_when_checkout_is_current(self):
        git_results = [
            SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="origin/main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="0\t0\n", stderr=""),
        ]

        with patch.object(api, "_run_git_command", side_effect=git_results) as mock_git:
            result = api.perform_self_update()

        self.assertFalse(result["updated"])
        self.assertFalse(result["restart_required"])
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["behind_count"], 0)
        self.assertEqual(result["ahead_count"], 0)
        self.assertIn("already up to date", result["message"])
        self.assertNotIn(["pull", "--ff-only"], [call.args[0] for call in mock_git.call_args_list])

    def test_perform_self_update_pulls_fast_forward_updates(self):
        git_results = [
            SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="origin/main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="0\t2\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="abc123456789\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Updating abc..def\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="def987654321\n", stderr=""),
        ]

        with patch.object(api, "_run_git_command", side_effect=git_results) as mock_git:
            result = api.perform_self_update()

        self.assertTrue(result["updated"])
        self.assertTrue(result["restart_required"])
        self.assertEqual(result["branch"], "main")
        self.assertEqual(result["behind_count"], 2)
        self.assertEqual(result["previous_commit"], "abc123456789")
        self.assertEqual(result["current_commit"], "def987654321")
        self.assertIn(["pull", "--ff-only"], [call.args[0] for call in mock_git.call_args_list])

    def test_perform_self_update_rejects_dirty_worktree(self):
        git_results = [
            SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout=" M web/api.py\n", stderr=""),
        ]

        with patch.object(api, "_run_git_command", side_effect=git_results):
            with self.assertRaises(api.AppUpdateError) as context:
                api.perform_self_update()

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("Local changes are present", str(context.exception))

    def test_create_sessions_creates_new_group_without_resetting_existing_state(self):
        api.session_manager.create_group(
            name="Existing",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-existing",
        )
        existing_session = api.session_manager.create_session(
            group_id="group-existing",
            host="old-host",
            directory="/tmp",
        )
        with api.connection_lock:
            api.session_output_buffers[existing_session.session_id] = "stale output"

        sessions_payload = {
            "connection_mode": "ssh",
            "layout": "grid",
            "sessions": [
                {
                    "host": "10.0.0.10",
                    "directory": "/srv/app",
                    "username": "ubuntu",
                    "initial_command": "pwd",
                    "title": "App",
                },
                {
                    "host": "10.0.0.11",
                    "directory": "/srv/worker",
                    "username": "ubuntu",
                    "port": 2222,
                    "title": "Worker",
                },
            ]
        }

        with patch.object(api.os, "name", "nt"), patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["layout"], "vertical")
        self.assertEqual(body["connection_mode"], "ssh")
        self.assertIn("group_id", body)
        self.assertEqual(start_task.call_count, 2)
        self.assertTrue(
            all(call.args[0] is api._connect_session for call in start_task.call_args_list)
        )

        sessions = api.session_manager.get_all_sessions()
        self.assertEqual(len(sessions), 3)
        self.assertEqual(
            {session.host for session in sessions},
            {"old-host", "10.0.0.10", "10.0.0.11"},
        )
        self.assertEqual(len(api.session_manager.get_group_sessions(body["group_id"])), 2)
        with api.connection_lock:
            self.assertEqual(
                api.session_output_buffers,
                {existing_session.session_id: "stale output"},
            )

    def test_create_sessions_reuses_saved_session_group_id_and_replaces_existing_sessions(self):
        initial_payload = {
            "connection_mode": "ssh",
            "layout": "single",
            "saved_session_id": "session-dev-grid",
            "session_name": "dev-grid",
            "sessions": [
                {
                    "host": "10.0.0.10",
                    "directory": "/srv/app",
                    "username": "ubuntu",
                    "title": "App",
                }
            ],
        }

        with patch.object(api.socketio, "start_background_task") as initial_start_task:
            initial_response = self.client.post("/api/sessions", json=initial_payload)

        self.assertEqual(initial_response.status_code, 201)
        initial_body = initial_response.get_json()
        original_session_id = initial_body["sessions"][0]["session_id"]
        original_group_id = initial_body["group_id"]
        self.assertEqual(original_group_id, "saved-session-session-dev-grid")
        self.assertEqual(initial_start_task.call_count, 1)

        with api.connection_lock:
            api.session_output_buffers[original_session_id] = "stale output"

        replacement_payload = {
            "connection_mode": "ssh",
            "layout": "vertical",
            "saved_session_id": "session-dev-grid",
            "session_name": "dev-grid",
            "sessions": [
                {
                    "host": "10.0.0.11",
                    "directory": "/srv/api",
                    "username": "ubuntu",
                    "title": "API",
                },
                {
                    "host": "10.0.0.12",
                    "directory": "/srv/worker",
                    "username": "ubuntu",
                    "title": "Worker",
                },
            ],
        }

        with patch.object(api.socketio, "start_background_task") as replacement_start_task, patch.object(
            api,
            "_close_ssh_connection",
            wraps=api._close_ssh_connection,
        ) as close_connection:
            replacement_response = self.client.post("/api/sessions", json=replacement_payload)

        self.assertEqual(replacement_response.status_code, 201)
        replacement_body = replacement_response.get_json()
        self.assertEqual(replacement_body["group_id"], original_group_id)
        self.assertEqual(replacement_body["layout"], "vertical")
        self.assertEqual(replacement_start_task.call_count, 2)
        self.assertEqual(len(api.session_manager.get_all_groups()), 1)
        self.assertEqual(
            [group.group_id for group in api.session_manager.get_all_groups()],
            [original_group_id],
        )

        sessions = api.session_manager.get_all_sessions()
        self.assertEqual(len(sessions), 2)
        self.assertEqual({session.host for session in sessions}, {"10.0.0.11", "10.0.0.12"})
        self.assertEqual(len(api.session_manager.get_group_sessions(original_group_id)), 2)
        self.assertNotIn(original_session_id, {session.session_id for session in sessions})
        close_connection.assert_any_call(original_session_id, clear_buffer=True)

        with api.connection_lock:
            self.assertNotIn(original_session_id, api.session_output_buffers)

    def test_session_config_falls_back_to_built_in_default_when_no_saved_sessions_exist(self):
        response = self.client.get("/api/session-config")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["connection_mode"], "ssh")
        self.assertEqual(data["terminal_count"], min(4, api.max_sessions))
        self.assertEqual(data["layout"], "grid")
        self.assertEqual(data["ssh"]["username"], "ubuntu")
        self.assertEqual(data["last_session"], "")
        self.assertIsNone(data["saved_session"])
        self.assertFalse(self.saved_sessions_path.exists())

    def test_select_folder_returns_manual_entry_fallback_when_native_picker_unavailable(self):
        with patch.object(
            api,
            "_pick_local_folder",
            side_effect=RuntimeError("Native folder picker support is unavailable"),
        ):
            response = self.client.post("/api/select-folder", json={"initial_dir": "/home/me"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "path": "",
                "selected": False,
                "manual_entry": True,
                "error": "Native folder picker support is unavailable",
            },
        )

    def test_select_folder_returns_selected_path_from_native_picker(self):
        with patch.object(api, "_pick_local_folder", return_value="/home/me/project") as picker:
            response = self.client.post("/api/select-folder", json={"initial_dir": "/home/me"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"path": "/home/me/project", "selected": True})
        picker.assert_called_once_with("/home/me")

    def test_create_sessions_accepts_local_repo_mode_and_tracks_layout(self):
        sessions_payload = {
            "connection_mode": "wsl",
            "layout": "horizontal",
            "sessions": [
                {
                    "directory": "C:/repo",
                    "title": "Dev shell",
                    "initial_command": "pwd",
                    "use_wsl": True,
                }
            ],
        }

        with patch.object(api.os, "name", "nt"), patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["connection_mode"], "wsl")
        self.assertEqual(body["layout"], "single")
        self.assertEqual(body["terminal_count"], 1)
        self.assertEqual(body["launch_target"], "web")
        start_task.assert_called_once()

        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.mode, "wsl")
        self.assertEqual(session.host, "WSL")
        self.assertEqual(session.directory, "C:/repo")
        self.assertTrue(session.use_wsl)

    def test_create_sessions_accepts_local_repo_powershell_mode(self):
        sessions_payload = {
            "connection_mode": "wsl",
            "layout": "horizontal",
            "sessions": [
                {
                    "directory": "C:/repo",
                    "title": "Windows shell",
                    "initial_command": "Get-Location",
                    "use_powershell": True,
                }
            ],
        }

        with patch.object(api.os, "name", "nt"), patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        start_task.assert_called_once()

        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.mode, "wsl")
        self.assertEqual(session.host, "PowerShell")
        self.assertTrue(session.use_powershell)
        self.assertFalse(session.use_wsl)

    def test_create_sessions_ignores_windows_shell_flags_on_posix_local_repo(self):
        sessions_payload = {
            "connection_mode": "wsl",
            "layout": "horizontal",
            "sessions": [
                {
                    "directory": "/home/me/repo",
                    "title": "Linux shell",
                    "initial_command": "pwd",
                    "use_wsl": True,
                    "use_powershell": True,
                    "distribution": "Ubuntu",
                }
            ],
        }

        with patch.object(api.os, "name", "posix"), patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        start_task.assert_called_once()

        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.mode, "wsl")
        self.assertEqual(session.host, "Shell")
        self.assertEqual(session.directory, "/home/me/repo")
        self.assertFalse(session.use_wsl)
        self.assertFalse(session.use_powershell)

    def test_create_sessions_accepts_local_repo_file_explorer_mode(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        sessions_payload = {
            "connection_mode": "wsl",
            "layout": "horizontal",
            "sessions": [
                {
                    "directory": str(repo_dir),
                    "title": "Files",
                    "initial_command": "pwd",
                    "startup_mode": "explorer",
                    "use_wsl": True,
                    "use_powershell": True,
                }
            ],
        }

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        start_task.assert_not_called()

        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.mode, "wsl")
        self.assertEqual(session.host, "File Explorer")
        self.assertEqual(session.startup_mode, "explorer")
        self.assertEqual(session.explorer_root_directory, str(repo_dir))
        self.assertEqual(session.initial_command, "")
        self.assertFalse(session.use_wsl)
        self.assertFalse(session.use_powershell)
        self.assertEqual(session.status, api.SessionStatus.CONNECTED)

    def test_create_sessions_accepts_ssh_file_explorer_mode(self):
        sessions_payload = {
            "connection_mode": "ssh",
            "layout": "horizontal",
            "sessions": [
                {
                    "host": "example.com",
                    "username": "ubuntu",
                    "password": "secret",
                    "port": 2222,
                    "directory": "/srv/app",
                    "title": "Remote Files",
                    "initial_command": "pwd",
                    "startup_mode": "explorer",
                }
            ],
        }

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        start_task.assert_not_called()

        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.mode, "ssh")
        self.assertEqual(session.host, "example.com")
        self.assertEqual(session.username, "ubuntu")
        self.assertEqual(session.port, 2222)
        self.assertEqual(session.startup_mode, "explorer")
        self.assertEqual(session.explorer_root_directory, "/srv/app")
        self.assertEqual(session.initial_command, "")
        self.assertEqual(session.status, api.SessionStatus.CONNECTED)

    def test_switch_explorer_pane_to_terminal_uses_selected_directory(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        selected_dir = repo_dir / "src"
        selected_dir.mkdir(parents=True)
        session_id = self._create_explorer_session(repo_dir)

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post(
                f"/api/sessions/{session_id}/mode",
                json={"startup_mode": "terminal", "directory": "src"},
            )

        self.assertEqual(response.status_code, 200)
        start_task.assert_called_once_with(api._connect_session, session_id)
        session = api.session_manager.get_session(session_id)
        self.assertEqual(session.startup_mode, "terminal")
        self.assertEqual(Path(session.directory), selected_dir.resolve())
        self.assertEqual(Path(session.explorer_root_directory), repo_dir.resolve())
        self.assertEqual(session.status, api.SessionStatus.PENDING)
        self.assertEqual(response.get_json()["startup_mode"], "terminal")

    def test_switch_terminal_pane_to_explorer_closes_connection_and_preserves_shell_choice(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        group = api.session_manager.create_group(
            name="Local",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="Ubuntu",
            directory=str(repo_dir),
            mode="wsl",
            distribution="Ubuntu",
            use_wsl=True,
            startup_mode="terminal",
        )
        api.session_manager.update_session_status(session.session_id, api.SessionStatus.CONNECTED)

        with patch.object(api, "_close_ssh_connection") as close_connection:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "explorer"},
            )

        self.assertEqual(response.status_code, 200)
        close_connection.assert_called_once_with(session.session_id, clear_buffer=True)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.host, "File Explorer")
        self.assertEqual(updated.startup_mode, "explorer")
        self.assertEqual(updated.initial_command, "")
        self.assertEqual(Path(updated.explorer_root_directory), repo_dir.resolve())
        self.assertTrue(updated.use_wsl)
        self.assertEqual(updated.distribution, "Ubuntu")
        self.assertEqual(updated.status, api.SessionStatus.CONNECTED)

    def test_switch_roundtrip_preserves_explorer_root_for_parent_navigation(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        selected_dir = repo_dir / "src"
        selected_dir.mkdir(parents=True)
        (repo_dir / "README.md").write_text("# Root\n", encoding="utf-8")
        (selected_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        with patch.object(api.socketio, "start_background_task"):
            terminal_response = self.client.post(
                f"/api/sessions/{session_id}/mode",
                json={"startup_mode": "terminal", "directory": "src"},
            )
        self.assertEqual(terminal_response.status_code, 200)

        with patch.object(api, "_close_ssh_connection"):
            explorer_response = self.client.post(
                f"/api/sessions/{session_id}/mode",
                json={
                    "startup_mode": "explorer",
                    "directory": str(selected_dir),
                },
            )
        self.assertEqual(explorer_response.status_code, 200)

        current_response = self.client.get(f"/api/explorer/{session_id}/entries")
        self.assertEqual(current_response.status_code, 200)
        current_payload = current_response.get_json()
        self.assertEqual(current_payload["path"], "src")
        self.assertEqual(current_payload["parent_path"], "")
        self.assertEqual(current_payload["root"], str(repo_dir.resolve()))

        parent_response = self.client.get(
            f"/api/explorer/{session_id}/entries",
            query_string={"path": current_payload["parent_path"]},
        )
        self.assertEqual(parent_response.status_code, 200)
        parent_payload = parent_response.get_json()
        self.assertEqual(parent_payload["path"], "")
        self.assertEqual(parent_payload["parent_path"], "")

    def test_local_stream_shutdown_after_explorer_switch_does_not_mark_error(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session_id = self._create_explorer_session(repo_dir)

        class ClosingPty:
            def read(self, _size):
                raise OSError("[WinError 10053] An established connection was aborted")

        with api.connection_lock:
            api.ssh_connections[session_id] = {
                "kind": "local",
                "pty_process": ClosingPty(),
            }

        api._stream_local_output(session_id)

        session = api.session_manager.get_session(session_id)
        self.assertEqual(session.status, api.SessionStatus.CONNECTED)
        with api.connection_lock:
            self.assertNotIn(session_id, api.ssh_connections)

    def test_switch_ssh_terminal_pane_to_explorer_preserves_host(self):
        group = api.session_manager.create_group(
            name="SSH",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app",
            mode="ssh",
            username="ubuntu",
            startup_mode="terminal",
        )
        api.session_manager.update_session_status(session.session_id, api.SessionStatus.CONNECTED)
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/src": {"type": "directory"},
            }
        )
        client = MagicMock()

        with patch.object(api, "_open_ssh_sftp", return_value=(client, fake_sftp)), patch.object(
            api,
            "_close_ssh_connection",
        ) as close_connection:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "explorer", "directory": "/srv/app/src"},
            )

        self.assertEqual(response.status_code, 200)
        close_connection.assert_called_once_with(session.session_id, clear_buffer=True)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.host, "example.com")
        self.assertEqual(updated.username, "ubuntu")
        self.assertEqual(updated.directory, "/srv/app/src")
        self.assertEqual(updated.explorer_root_directory, "/srv/app")
        self.assertEqual(updated.startup_mode, "explorer")
        self.assertEqual(updated.status, api.SessionStatus.CONNECTED)
        client.close.assert_called_once()

    def test_switch_ssh_terminal_to_explorer_falls_back_from_stale_remote_root(self):
        group = api.session_manager.create_group(
            name="SSH",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app/src",
            mode="ssh",
            username="ubuntu",
            startup_mode="terminal",
            explorer_root_directory="/stale/root",
        )
        fake_sftp = FakeSftp({"/srv/app/src": {"type": "directory"}})

        with patch.object(api, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)), patch.object(
            api,
            "_close_ssh_connection",
        ):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "explorer"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.directory, "/srv/app/src")
        self.assertEqual(updated.explorer_root_directory, "/srv/app/src")
        self.assertEqual(updated.startup_mode, "explorer")

    def test_switch_ssh_explorer_pane_to_terminal_uses_selected_remote_directory(self):
        group = api.session_manager.create_group(
            name="SSH",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app/src",
            username="ubuntu",
            mode="ssh",
            startup_mode="explorer",
            explorer_root_directory="/srv/app",
        )
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/src": {"type": "directory"},
            }
        )

        with patch.object(api, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)), patch.object(
            api.socketio,
            "start_background_task",
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "terminal", "directory": "src"},
            )

        self.assertEqual(response.status_code, 200)
        start_task.assert_called_once_with(api._connect_session, session.session_id)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.host, "example.com")
        self.assertEqual(updated.directory, "/srv/app/src")
        self.assertEqual(updated.explorer_root_directory, "/srv/app")
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.status, api.SessionStatus.PENDING)

    def test_explorer_entries_lists_local_directory_inside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        subdir = repo_dir / "src"
        subdir.mkdir(parents=True)
        file_path = repo_dir / "README.md"
        file_path.write_text("# Project\n", encoding="utf-8")
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        session_id = response.get_json()["sessions"][0]["session_id"]

        entries_response = self.client.get(f"/api/explorer/{session_id}/entries")

        self.assertEqual(entries_response.status_code, 200)
        payload = entries_response.get_json()
        self.assertEqual(payload["path"], "")
        self.assertEqual(payload["parent_path"], "")
        self.assertEqual([entry["name"] for entry in payload["entries"]], ["src", "README.md"])
        self.assertEqual(payload["entries"][0]["type"], "directory")
        self.assertEqual(payload["entries"][1]["type"], "file")

    def test_explorer_entries_lists_ssh_directory_inside_root(self):
        group = api.session_manager.create_group(
            name="SSH",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app",
            username="ubuntu",
            mode="ssh",
            startup_mode="explorer",
            explorer_root_directory="/srv/app",
        )
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/src": {"type": "directory"},
                "/srv/app/README.md": {"type": "file", "content": b"# Project\n"},
                "/srv/app/src/main.py": {"type": "file", "content": b"print('ok')\n"},
            }
        )

        with patch.object(api, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            entries_response = self.client.get(f"/api/explorer/{session.session_id}/entries")

        self.assertEqual(entries_response.status_code, 200)
        payload = entries_response.get_json()
        self.assertEqual(payload["root"], "/srv/app")
        self.assertEqual(payload["path"], "")
        self.assertEqual(payload["parent_path"], "")
        self.assertEqual([entry["name"] for entry in payload["entries"]], ["src", "README.md"])

    def test_explorer_entries_rejects_path_outside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        outside_dir = Path(self.temp_dir.name) / "outside"
        outside_dir.mkdir()
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        session_id = response.get_json()["sessions"][0]["session_id"]

        entries_response = self.client.get(
            f"/api/explorer/{session_id}/entries",
            query_string={"path": str(outside_dir)},
        )

        self.assertEqual(entries_response.status_code, 400)
        self.assertIn("inside the configured root", entries_response.get_json()["error"])

    def test_explorer_entries_rejects_ssh_path_outside_root(self):
        group = api.session_manager.create_group(
            name="SSH",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app",
            username="ubuntu",
            mode="ssh",
            startup_mode="explorer",
            explorer_root_directory="/srv/app",
        )
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/etc": {"type": "directory"},
            }
        )

        with patch.object(api, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/entries",
                query_string={"path": "/etc"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("inside the configured root", response.get_json()["error"])

    def test_explorer_file_returns_text_content_inside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "README.md"
        file_path.write_bytes("# Project\n\nHello <GridVibe>\n".encode("utf-8"))
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "README.md"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertEqual(payload["path"], "README.md")
        self.assertEqual(payload["name"], "README.md")
        self.assertEqual(payload["content"], "# Project\n\nHello <GridVibe>\n")
        self.assertEqual(payload["encoding"], "utf-8")
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["size"], file_path.stat().st_size)
        self.assertEqual(payload["preview_type"], "markdown")
        self.assertIn("<h1>Project</h1>", payload["preview_html"])
        self.assertEqual(payload["language"], "markdown")

    def test_explorer_file_returns_ssh_text_content_inside_root(self):
        group = api.session_manager.create_group(
            name="SSH",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app",
            username="ubuntu",
            mode="ssh",
            startup_mode="explorer",
            explorer_root_directory="/srv/app",
        )
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/notes.txt": {"type": "file", "content": b"hello\n"},
            }
        )

        with patch.object(api, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/file",
                query_string={"path": "notes.txt"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["root"], "/srv/app")
        self.assertEqual(payload["path"], "notes.txt")
        self.assertEqual(payload["name"], "notes.txt")
        self.assertEqual(payload["content"], "hello\n")
        self.assertFalse(payload["truncated"])

    def test_explorer_file_returns_sanitized_markdown_preview(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "README.md"
        file_path.write_bytes(
            (
                "# Title\n\n"
                "<script>alert('xss')</script>\n\n"
                "[bad link](javascript:alert(1))\n\n"
                "**Safe bold**\n"
            ).encode("utf-8")
        )
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "README.md"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertEqual(payload["preview_type"], "markdown")
        self.assertIn("<h1>Title</h1>", payload["preview_html"])
        self.assertIn("<strong>Safe bold</strong>", payload["preview_html"])
        self.assertNotIn("<script", payload["preview_html"])
        self.assertNotIn("javascript:", payload["preview_html"])

    def test_explorer_file_does_not_preview_non_markdown_text(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "notes.txt").write_text("# Not markdown\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "notes.txt"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertIsNone(payload["preview_type"])
        self.assertIsNone(payload["preview_html"])
        self.assertIsNone(payload["language"])

    def test_explorer_file_returns_code_language_for_common_source_files(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "script.py").write_text("def main():\n    print('hi')\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "script.py"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertEqual(payload["preview_type"], None)
        self.assertIsNone(payload["preview_html"])
        self.assertEqual(payload["language"], "python")

    def test_explorer_file_rejects_path_outside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        outside_file = Path(self.temp_dir.name) / "secret.txt"
        outside_file.write_text("secret\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": str(outside_file)},
        )

        self.assertEqual(file_response.status_code, 400)
        self.assertIn("inside the configured root", file_response.get_json()["error"])

    def test_explorer_file_rejects_directory(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        src_dir = repo_dir / "src"
        src_dir.mkdir(parents=True)
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "src"},
        )

        self.assertEqual(file_response.status_code, 400)
        self.assertIn("directory", file_response.get_json()["error"])

    def test_explorer_file_truncates_large_text_preview(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "large.log"
        file_path.write_text(
            "a" * (api.EXPLORER_FILE_PREVIEW_MAX_BYTES + 10),
            encoding="utf-8",
        )
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "large.log"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["content"]), api.EXPLORER_FILE_PREVIEW_MAX_BYTES)
        self.assertEqual(payload["size"], file_path.stat().st_size)

    def test_explorer_file_rejects_binary_content(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "image.bin").write_bytes(b"abc\x00def")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "image.bin"},
        )

        self.assertEqual(file_response.status_code, 400)
        self.assertIn("binary", file_response.get_json()["error"])

    def test_create_sessions_uses_cmd_label_for_local_repo_cmd_panes(self):
        sessions_payload = {
            "connection_mode": "wsl",
            "layout": "horizontal",
            "sessions": [
                {
                    "directory": "C:/repo",
                    "title": "Windows shell",
                    "distribution": "Ubuntu",
                    "use_wsl": False,
                    "use_powershell": False,
                }
            ],
        }

        with patch.object(api.os, "name", "nt"), patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        start_task.assert_called_once()

        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.host, "cmd")
        self.assertFalse(session.use_wsl)
        self.assertFalse(session.use_powershell)

    def test_create_sessions_accepts_split_layout_for_three_terminals(self):
        sessions_payload = {
            "connection_mode": "ssh",
            "layout": "split",
            "sessions": [
                {
                    "host": f"10.0.0.{20 + index}",
                    "directory": "/srv/app",
                    "username": "ubuntu",
                    "title": f"Terminal {index + 1}",
                }
                for index in range(3)
            ],
        }

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["layout"], "split")
        self.assertEqual(body["terminal_count"], 3)
        self.assertEqual(start_task.call_count, 3)

    def test_get_session_groups_returns_display_order(self):
        api.session_manager.create_group(
            name="One",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-1",
        )
        api.session_manager.create_group(
            name="Two",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-2",
        )
        api.session_manager.reorder_groups(["group-2", "group-1"])

        response = self.client.get("/api/session-groups")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual([group["group_id"] for group in data["groups"]], ["group-2", "group-1"])
        self.assertEqual([group["display_order"] for group in data["groups"]], [0, 1])

    def test_reorder_session_groups_updates_group_order(self):
        api.session_manager.create_group(
            name="One",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-1",
        )
        api.session_manager.create_group(
            name="Two",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-2",
        )
        api.session_manager.create_group(
            name="Three",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-3",
        )

        response = self.client.post(
            "/api/session-groups/order",
            json={"group_ids": ["group-3", "group-1", "group-2"]},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(
            [group["group_id"] for group in data["groups"]],
            ["group-3", "group-1", "group-2"],
        )
        self.assertEqual(
            [group.group_id for group in api.session_manager.get_all_groups()],
            ["group-3", "group-1", "group-2"],
        )

    def test_reorder_session_groups_requires_non_empty_group_ids(self):
        response = self.client.post("/api/session-groups/order", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "A non-empty 'group_ids' list is required"},
        )

    def test_saved_sessions_roundtrip_returns_named_presets(self):
        payload = {
            "name": "dev-grid",
            "config": {
                "connection_mode": "ssh",
                "terminal_count": 4,
                "layout": "grid",
                "ssh": {
                    "host": "10.0.0.20",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "/srv/dev",
                },
                "wsl": {
                    "distribution": "",
                    "username": "",
                    "default_dir": "~",
                },
                "terminals": [
                    {"title": "Codex", "directory": "/srv/dev", "initial_command": "codex"},
                    {"title": "Copilot", "directory": "/srv/dev", "initial_command": "copilot"},
                    {"title": "Kilo", "directory": "/srv/dev", "initial_command": "kilo"},
                    {"title": "Shell", "directory": "/srv/dev", "initial_command": ""},
                ],
            },
        }

        create_response = self.client.post("/api/saved-sessions", json=payload)
        list_response = self.client.get("/api/saved-sessions")

        self.assertEqual(create_response.status_code, 201)
        self.assertTrue(self.saved_sessions_path.exists())
        created = create_response.get_json()
        self.assertEqual(created["name"], "dev-grid")
        self.assertEqual(created["last_session"], created["id"])
        self.assertEqual(created["saved_session"], {"id": created["id"], "name": "dev-grid"})
        self.assertEqual(created["config"]["terminals"][0]["title"], "Codex")

        listed = list_response.get_json()
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["sessions"][0]["id"], created["id"])
        self.assertEqual(listed["sessions"][0]["layout"], "grid")
        self.assertEqual(listed["default_session"]["id"], api.DEFAULT_SAVED_SESSION_ID)
        self.assertTrue(listed["default_session"]["is_default"])
        self.assertEqual(listed["last_session"], created["id"])
        self.assertEqual(listed["saved_session"], {"id": created["id"], "name": "dev-grid"})

        get_response = self.client.get(f"/api/saved-sessions/{created['id']}")
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.get_json()["config"]["ssh"]["host"], "10.0.0.20")

    def test_saved_sessions_roundtrip_preserves_agent_startup_metadata(self):
        payload = {
            "name": "agent-preset",
            "config": {
                "connection_mode": "ssh",
                "terminal_count": 1,
                "layout": "single",
                "ssh": {
                    "host": "10.0.0.20",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "/srv/dev",
                },
                "wsl": {
                    "distribution": "",
                    "username": "",
                    "default_dir": "",
                },
                "terminals": [
                    {
                        "title": "Agent",
                        "directory": "/srv/dev",
                        "initial_command": "claude-code",
                        "initial_command_mode": "agent",
                        "agent_selection": "other",
                        "custom_agent": "claude-code",
                    }
                ],
            },
        }

        created = self.client.post("/api/saved-sessions", json=payload)

        self.assertEqual(created.status_code, 201)
        created_body = created.get_json()
        terminal = created_body["config"]["terminals"][0]
        self.assertEqual(terminal["initial_command"], "claude-code")
        self.assertEqual(terminal["initial_command_mode"], "agent")
        self.assertEqual(terminal["agent_selection"], "other")
        self.assertEqual(terminal["custom_agent"], "claude-code")

        fetched = self.client.get(f"/api/saved-sessions/{created_body['id']}")
        self.assertEqual(fetched.status_code, 200)
        fetched_terminal = fetched.get_json()["config"]["terminals"][0]
        self.assertEqual(fetched_terminal["initial_command_mode"], "agent")
        self.assertEqual(fetched_terminal["agent_selection"], "other")
        self.assertEqual(fetched_terminal["custom_agent"], "claude-code")

    def test_get_saved_session_returns_virtual_default_session(self):
        response = self.client.get(f"/api/saved-sessions/{api.DEFAULT_SAVED_SESSION_ID}")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["id"], api.DEFAULT_SAVED_SESSION_ID)
        self.assertEqual(data["name"], api.DEFAULT_SAVED_SESSION_NAME)
        self.assertTrue(data["is_default"])
        self.assertEqual(data["config"]["connection_mode"], "ssh")
        self.assertEqual(data["config"]["terminal_count"], min(4, api.max_sessions))

    def test_session_config_returns_last_saved_session_metadata(self):
        payload = {
            "name": "dev-grid",
            "config": {
                "connection_mode": "ssh",
                "terminal_count": 2,
                "layout": "vertical",
                "ssh": {
                    "host": "10.0.0.20",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "/srv/dev",
                },
                "wsl": {
                    "distribution": "",
                    "username": "",
                    "default_dir": "",
                },
                "terminals": [
                    {"title": "Codex", "directory": "/srv/dev", "initial_command": "codex"},
                    {"title": "Shell", "directory": "/srv/dev", "initial_command": ""},
                ],
            },
        }

        created = self.client.post("/api/saved-sessions", json=payload).get_json()
        response = self.client.get("/api/session-config")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["ssh"]["host"], "10.0.0.20")
        self.assertEqual(data["last_session"], created["id"])
        self.assertEqual(data["saved_session"], {"id": created["id"], "name": "dev-grid"})

    def test_persist_session_config_can_select_virtual_default_session(self):
        created = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "dev-grid",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "vertical",
                    "ssh": {
                        "host": "10.0.0.20",
                        "username": "ubuntu",
                        "password": "",
                        "port": 22,
                        "default_dir": "/srv/dev",
                    },
                    "wsl": {
                        "distribution": "",
                        "username": "",
                        "default_dir": "",
                    },
                    "terminals": [
                        {"title": "Codex", "directory": "/srv/dev", "initial_command": "codex"},
                        {"title": "Shell", "directory": "/srv/dev", "initial_command": ""},
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/session-config",
            json={"saved_session_id": api.DEFAULT_SAVED_SESSION_ID},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["last_session"], api.DEFAULT_SAVED_SESSION_ID)
        self.assertEqual(
            data["saved_session"],
            {"id": api.DEFAULT_SAVED_SESSION_ID, "name": api.DEFAULT_SAVED_SESSION_NAME},
        )
        self.assertEqual(data["connection_mode"], "ssh")
        self.assertEqual(data["ssh"]["host"], "")

        saved_state = json.loads(self.saved_sessions_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["last_session"], api.DEFAULT_SAVED_SESSION_ID)
        self.assertEqual(len(saved_state["sessions"]), 1)
        self.assertEqual(saved_state["sessions"][0]["id"], created["id"])

    def test_persist_session_config_updates_last_saved_session_without_storing_unsaved_config(self):
        payload = {
            "name": "dev-grid",
            "config": {
                "connection_mode": "ssh",
                "terminal_count": 1,
                "layout": "single",
                "ssh": {
                    "host": "10.0.0.20",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "/srv/dev",
                },
                "wsl": {
                    "distribution": "",
                    "username": "",
                    "default_dir": "",
                },
                "terminals": [
                    {"title": "Shell", "directory": "/srv/dev", "initial_command": ""}
                ],
            },
        }

        created = self.client.post("/api/saved-sessions", json=payload).get_json()
        response = self.client.post(
            "/api/session-config",
            json={
                "saved_session_id": created["id"],
            },
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["last_session"], created["id"])
        self.assertEqual(data["saved_session"], {"id": created["id"], "name": "dev-grid"})
        self.assertEqual(data["ssh"]["host"], "10.0.0.20")

        saved_state = json.loads(self.saved_sessions_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["last_session"], created["id"])

    def test_persist_session_config_can_clear_last_saved_session(self):
        created = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "dev-grid",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "ssh": {
                        "host": "10.0.0.20",
                        "username": "ubuntu",
                        "password": "",
                        "port": 22,
                        "default_dir": "/srv/dev",
                    },
                    "wsl": {
                        "distribution": "",
                        "username": "",
                        "default_dir": "",
                    },
                    "terminals": [
                        {"title": "Shell", "directory": "/srv/dev", "initial_command": ""}
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/session-config",
            json={"saved_session_id": ""},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["last_session"], "")
        self.assertIsNone(data["saved_session"])
        self.assertEqual(data["connection_mode"], "ssh")
        self.assertEqual(data["ssh"]["host"], "")

        saved_state = json.loads(self.saved_sessions_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["last_session"], "")
        self.assertEqual(len(saved_state["sessions"]), 1)
        self.assertEqual(saved_state["sessions"][0]["id"], created["id"])

    def test_persist_session_config_does_not_create_saved_sessions_file_when_none_exists(self):
        response = self.client.post(
            "/api/session-config",
            json={"saved_session_id": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.saved_sessions_path.exists())
        data = response.get_json()
        self.assertEqual(data["last_session"], "")
        self.assertIsNone(data["saved_session"])

    def test_delete_saved_sessions_requires_ids(self):
        response = self.client.delete("/api/saved-sessions", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "At least one saved session id is required"},
        )

    def test_delete_saved_sessions_falls_back_to_built_in_default_when_all_are_removed(self):
        first = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "first",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "ssh": {
                        "host": "10.0.0.10",
                        "username": "ubuntu",
                        "password": "",
                        "port": 22,
                        "default_dir": "/srv/first",
                    },
                    "wsl": {
                        "distribution": "",
                        "username": "",
                        "default_dir": "",
                    },
                    "terminals": [
                        {"title": "First", "directory": "/srv/first", "initial_command": "pwd"}
                    ],
                },
            },
        ).get_json()
        second = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "second",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "ssh": {
                        "host": "10.0.0.20",
                        "username": "ubuntu",
                        "password": "",
                        "port": 22,
                        "default_dir": "/srv/second",
                    },
                    "wsl": {
                        "distribution": "",
                        "username": "",
                        "default_dir": "",
                    },
                    "terminals": [
                        {"title": "Second", "directory": "/srv/second", "initial_command": "htop"}
                    ],
                },
            },
        ).get_json()

        response = self.client.delete(
            "/api/saved-sessions",
            json={"ids": [first["id"], second["id"]]},
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["last_session"], "")
        self.assertIsNone(data["saved_session"])
        self.assertEqual(data["sessions"], [])
        self.assertEqual(data["config"]["connection_mode"], "ssh")
        self.assertEqual(data["config"]["ssh"]["host"], "")
        self.assertFalse(self.saved_sessions_path.exists())

    def test_session_config_uses_saved_session_layout_when_last_session_points_to_grid_preset(self):
        created = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "grid-session",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 6,
                    "layout": "vertical",
                    "ssh": {
                        "host": "10.0.0.30",
                        "username": "ubuntu",
                        "password": "",
                        "port": 22,
                        "default_dir": "/srv/grid",
                    },
                    "wsl": {
                        "distribution": "",
                        "username": "",
                        "default_dir": "~",
                    },
                    "terminals": [
                        {"title": f"Terminal {index + 1}", "directory": "", "initial_command": ""}
                        for index in range(6)
                    ],
                },
            },
        ).get_json()

        response = self.client.get("/api/session-config")

        self.assertEqual(response.status_code, 200)
        saved = response.get_json()
        self.assertEqual(saved["last_session"], created["id"])
        self.assertEqual(saved["terminal_count"], 6)
        self.assertEqual(saved["layout"], "grid")

    def test_create_sessions_for_eight_terminals_reports_grid_layout(self):
        sessions_payload = {
            "connection_mode": "ssh",
            "layout": "horizontal",
            "sessions": [
                {
                    "host": f"10.0.0.{10 + index}",
                    "directory": "/srv/app",
                    "username": "ubuntu",
                    "title": f"Terminal {index + 1}",
                }
                for index in range(8)
            ],
        }

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["count"], 8)
        self.assertEqual(body["terminal_count"], 8)
        self.assertEqual(body["layout"], "grid")
        self.assertEqual(body["launch_target"], "web")
        self.assertEqual(start_task.call_count, 8)

    def test_resize_connection_for_ssh_uses_channel_resize_pty(self):
        channel = MagicMock()
        connection = {"kind": "ssh", "channel": channel}

        api._resize_connection(connection, cols=132, rows=42)

        channel.resize_pty.assert_called_once_with(width=132, height=42)

    def test_run_startup_sequence_uses_cmd_syntax_for_windows_local_repo(self):
        connection = {"kind": "local", "pty_process": object()}
        session = SimpleNamespace(
            directory='C:\\repo path',
            initial_command='npm run dev',
        )

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "_send_connection_input") as send_input:
                with patch.object(api.time, "sleep") as sleep:
                    api._run_startup_sequence(connection, session)

        self.assertEqual(
            send_input.call_args_list,
            [
                unittest.mock.call(connection, 'cd /d "C:\\repo path"\r'),
                unittest.mock.call(connection, 'npm run dev\r'),
            ],
        )
        sleep.assert_called_once_with(0.15)

    def test_run_startup_sequence_translates_windows_path_for_wsl_shell(self):
        connection = {"kind": "local", "pty_process": object(), "shell_kind": "wsl"}
        session = SimpleNamespace(
            directory='C:\\repo path',
            initial_command='npm run dev',
        )

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "_send_connection_input") as send_input:
                with patch.object(api.time, "sleep") as sleep:
                    api._run_startup_sequence(connection, session)

        self.assertEqual(
            send_input.call_args_list,
            [
                unittest.mock.call(connection, "cd '/mnt/c/repo path'\r"),
                unittest.mock.call(connection, 'npm run dev\r'),
            ],
        )
        self.assertEqual(
            sleep.call_args_list,
            [
                unittest.mock.call(0.25),
                unittest.mock.call(0.15),
            ],
        )

    def test_run_startup_sequence_waits_briefly_before_wsl_initial_command(self):
        connection = {
            "kind": "local",
            "pty_process": object(),
            "shell_kind": "wsl",
            "launch_cwd_applied": True,
        }
        session = SimpleNamespace(directory='C:\\repo path', initial_command='codex')

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "_send_connection_input") as send_input:
                with patch.object(api.time, "sleep") as sleep:
                    api._run_startup_sequence(connection, session)

        self.assertEqual(
            send_input.call_args_list,
            [
                unittest.mock.call(connection, 'codex\r'),
            ],
        )
        sleep.assert_called_once_with(0.25)

    def test_run_startup_sequence_skips_cd_when_launch_cwd_applied(self):
        connection = {
            "kind": "local",
            "pty_process": object(),
            "shell_kind": "cmd",
            "launch_cwd_applied": True,
        }
        session = SimpleNamespace(
            directory='C:\\repo path',
            initial_command='npm run dev',
        )

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "_send_connection_input") as send_input:
                with patch.object(api.time, "sleep") as sleep:
                    api._run_startup_sequence(connection, session)

        self.assertEqual(
            send_input.call_args_list,
            [
                unittest.mock.call(connection, 'npm run dev\r'),
            ],
        )
        sleep.assert_not_called()

    def test_run_startup_sequence_uses_powershell_literal_cd(self):
        connection = {"kind": "local", "pty_process": object(), "shell_kind": "powershell"}
        session = SimpleNamespace(
            directory='C:\\repo path',
            initial_command='npm run dev',
        )

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "_send_connection_input") as send_input:
                with patch.object(api.time, "sleep") as sleep:
                    api._run_startup_sequence(connection, session)

        self.assertEqual(
            send_input.call_args_list,
            [
                unittest.mock.call(connection, "Set-Location -LiteralPath 'C:\\repo path'\r"),
                unittest.mock.call(connection, 'npm run dev\r'),
            ],
        )
        sleep.assert_called_once_with(0.15)

    def test_resolve_local_launch_cwd_uses_existing_non_wsl_directory(self):
        with TemporaryDirectory() as temp_dir:
            self.assertEqual(api._resolve_local_launch_cwd(temp_dir, "cmd"), temp_dir)
            self.assertIsNone(api._resolve_local_launch_cwd(temp_dir, "wsl"))

    def test_find_running_ubuntu_distribution_prefers_default_running_ubuntu(self):
        completed = SimpleNamespace(
            stdout=(
                "  NAME                   STATE           VERSION\n"
                "* Ubuntu-24.04          Running         2\n"
                "  Ubuntu                Running         2\n"
                "  Debian                Running         2\n"
            ),
            stderr="",
        )

        with patch.object(api, "_find_wsl_executable", return_value="wsl.exe"):
            with patch.object(api.subprocess, "run", return_value=completed) as run_command:
                snapshot = api._inspect_wsl_distributions()

        distros = snapshot["distros"]
        ubuntu_names = [d["name"] for d in distros if d["name"].lower().startswith("ubuntu")]
        self.assertIn("Ubuntu-24.04", ubuntu_names)
        run_command.assert_called_once_with(
            ["wsl.exe", "-l", "-v"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
        )

    def test_get_wsl_distros_returns_parsed_local_distributions(self):
        completed = SimpleNamespace(
            stdout=(
                "  NAME                   STATE           VERSION\n"
                "* Ubuntu-24.04          Running         2\n"
                "  Ubuntu                Stopped         2\n"
            ),
            stderr="",
        )

        with patch.object(api, "_find_wsl_executable", return_value="wsl.exe"):
            with patch.object(api.subprocess, "run", return_value=completed):
                response = self.client.get("/api/wsl-distros")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["available"])
        self.assertEqual(body["command"], "wsl -l -v")
        self.assertEqual(body["distros"][0]["name"], "Ubuntu-24.04")
        self.assertEqual(body["distros"][1]["state"], "Stopped")

    def test_build_local_command_uses_wsl_without_distribution_when_blank(self):
        session = SimpleNamespace(use_wsl=True, username="devuser")

        with patch.object(api, "_find_wsl_executable", return_value="wsl.exe"):
            command = api._build_local_command(session, resolved_distribution="")

        self.assertEqual(command[0], "wsl.exe")
        self.assertNotIn("--distribution", command)

    def test_build_local_command_uses_powershell_when_requested(self):
        session = SimpleNamespace(use_wsl=False, use_powershell=True, username="")

        with patch.object(api.os, "name", "nt"):
            command = api._build_local_command(session, resolved_distribution="")

        self.assertEqual(command, ["powershell.exe", "-NoLogo"])

    def test_sanitize_terminal_input_strips_windows_device_attributes_for_cmd_and_powershell(self):
        cmd_connection = {"kind": "local", "pty_process": object(), "shell_kind": "cmd"}
        powershell_connection = {"kind": "local", "pty_process": object(), "shell_kind": "powershell"}

        with patch.object(api.os, "name", "nt"):
            cmd_input = api._sanitize_terminal_input(
                cmd_connection,
                f"{api.WINDOWS_DEVICE_ATTRIBUTES_RESPONSE}dir\r",
            )
            powershell_input = api._sanitize_terminal_input(
                powershell_connection,
                f"{api.WINDOWS_DEVICE_ATTRIBUTES_RESPONSE}Get-Location\r",
            )

        self.assertEqual(cmd_input, "dir\r")
        self.assertEqual(powershell_input, "Get-Location\r")

    def test_sanitize_terminal_input_preserves_wsl_device_attributes(self):
        connection = {"kind": "local", "pty_process": object(), "shell_kind": "wsl"}

        with patch.object(api.os, "name", "nt"):
            sanitized = api._sanitize_terminal_input(
                connection,
                f"{api.WINDOWS_DEVICE_ATTRIBUTES_RESPONSE}pwd\r",
            )

        self.assertEqual(sanitized, f"{api.WINDOWS_DEVICE_ATTRIBUTES_RESPONSE}pwd\r")

    def test_build_local_command_uses_wsl_startup_directory_when_available(self):
        session = SimpleNamespace(use_wsl=True, username="devuser")

        with patch.object(api, "_find_wsl_executable", return_value="wsl.exe"):
            command = api._build_local_command(
                session,
                resolved_distribution="Ubuntu",
                startup_directory="/mnt/c/repo/subdir",
            )

        self.assertEqual(
            command,
            [
                "wsl.exe",
                "--distribution",
                "Ubuntu",
                "--user",
                "devuser",
                "--cd",
                "/mnt/c/repo/subdir",
            ],
        )

    def test_resolve_wsl_distribution_prefers_configured_distribution(self):
        session = SimpleNamespace(use_wsl=True, distribution="Debian")

        distribution = api._resolve_wsl_distribution(session)

        self.assertEqual(distribution, "Debian")

    def test_resolve_wsl_distribution_returns_empty_when_unset(self):
        session = SimpleNamespace(use_wsl=True, distribution="")

        distribution = api._resolve_wsl_distribution(session)

        self.assertEqual(distribution, "")

    def test_connect_local_session_uses_configured_wsl_distribution(self):
        session = SimpleNamespace(
            distribution="Debian",
            username="devuser",
            directory="C:\\repo",
            initial_command=None,
            use_wsl=True,
        )

        fake_process = object()

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_find_wsl_executable", return_value="wsl.exe"
        ):
            with patch.object(api, "WinPtyProcess") as winpty:
                with patch.object(api, "_broadcast_session_status"):
                    with patch.object(api, "_stream_local_output"):
                        with patch.object(api, "_run_startup_sequence"):
                            with patch.object(api, "_drain_until_prompt"):
                                with patch.object(api.session_manager, "update_session_status"):
                                    winpty.spawn.return_value = fake_process
                                    api._connect_local_session("abc123", session)

        winpty.spawn.assert_called_once()
        command_line = winpty.spawn.call_args.args[0]
        self.assertIn("wsl.exe", command_line)
        self.assertIn("--distribution Debian", command_line)
        self.assertIn("--user devuser", command_line)
        self.assertIn('--cd /mnt/c/repo', command_line)

    def test_connect_local_session_uses_powershell_when_requested(self):
        session = SimpleNamespace(
            distribution="Ubuntu",
            username="devuser",
            directory="C:\\repo",
            initial_command=None,
            use_wsl=False,
            use_powershell=True,
        )

        fake_process = object()

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "WinPtyProcess") as winpty:
                with patch.object(api, "_broadcast_session_status"):
                    with patch.object(api, "_stream_local_output"):
                        with patch.object(api, "_run_startup_sequence"):
                            with patch.object(api.session_manager, "update_session_status"):
                                winpty.spawn.return_value = fake_process
                                api._connect_local_session("abc123", session)

        winpty.spawn.assert_called_once()
        command_line = winpty.spawn.call_args.args[0]
        self.assertIn("powershell.exe", command_line)
        self.assertIn("-NoLogo", command_line)

    def test_connect_local_session_requires_pywinpty_on_windows(self):
        session = SimpleNamespace(
            distribution='',
            username='',
            directory='C:\\repo',
            initial_command=None,
        )

        with patch.object(api.os, "name", "nt"):
            with patch.object(api, "WinPtyProcess", None):
                with patch.object(api, "_broadcast_session_status"):
                    api._connect_local_session("abc123", session)

        stored = api.session_manager.get_session("abc123")
        self.assertIsNone(stored)

    def test_get_missing_session_returns_not_found(self):
        response = self.client.get("/api/sessions/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Session not found"})

    def test_split_session_appends_cloned_terminal_to_group(self):
        api.session_manager.create_group(
            name="Manual",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-manual",
        )
        source = api.session_manager.create_session(
            group_id="group-manual",
            host="10.0.0.12",
            directory="/tmp/project",
            username="alice",
            port=2200,
            password="secret",
            initial_command="codex",
            title="Primary",
        )

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post(f"/api/sessions/{source.session_id}/split")

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        created = payload["session"]
        self.assertEqual(created["group_id"], "group-manual")
        self.assertEqual(created["host"], "10.0.0.12")
        self.assertEqual(created["directory"], "/tmp/project")
        self.assertEqual(created["username"], "alice")
        self.assertEqual(created["port"], 2200)
        self.assertIsNone(created["initial_command"])
        self.assertNotIn("password", created)
        self.assertEqual(payload["group"]["terminal_count"], 2)

        stored = api.session_manager.get_session(created["session_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored.password, "secret")
        self.assertIsNone(stored.initial_command)
        start_task.assert_called_once_with(api._connect_session, created["session_id"])

    def test_split_session_rejects_explorer_pane(self):
        api.session_manager.create_group(
            name="Explorer",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
            group_id="group-explorer",
        )
        source = api.session_manager.create_session(
            group_id="group-explorer",
            host="File Explorer",
            directory="/tmp/project",
            mode="wsl",
            startup_mode="explorer",
        )

        response = self.client.post(f"/api/sessions/{source.session_id}/split")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Explorer panes cannot be split"})

    def test_split_session_rejects_group_at_max_sessions(self):
        api.session_manager.create_group(
            name="Full",
            connection_mode="ssh",
            layout="grid",
            terminal_count=api.max_sessions,
            group_id="group-full",
        )
        source = None
        for index in range(api.max_sessions):
            session = api.session_manager.create_session(
                group_id="group-full",
                host=f"10.0.0.{index + 1}",
                directory="/tmp/project",
            )
            source = source or session

        response = self.client.post(f"/api/sessions/{source.session_id}/split")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": f"Maximum {api.max_sessions} sessions allowed"},
        )

    def test_delete_session_closes_and_removes_it(self):
        api.session_manager.create_group(
            name="Manual",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-manual",
        )
        session = api.session_manager.create_session(
            group_id="group-manual",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        response = self.client.delete(f"/api/sessions/{session.session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"message": "Session closed successfully"},
        )
        self.assertIsNone(api.session_manager.get_session(session.session_id))

    def test_join_session_replays_sanitized_buffered_output_to_new_client(self):
        api.session_manager.create_group(
            name="Buffered",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-buffered",
        )
        session = api.session_manager.create_session(
            group_id="group-buffered",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        with api.connection_lock:
            api.session_output_buffers[session.session_id] = (
                "boot"
                f"{api.WINDOWS_DEVICE_ATTRIBUTES_RESPONSE}"
                "\x1b]10;?\x07"
                "\x1b]11;?\x1b\\"
                "prompt"
            )

        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)

        socket_client.emit("join_session", {"session_id": session.session_id})
        events = socket_client.get_received()

        session_status_events = [
            event for event in events if event["name"] == "session_status"
        ]
        terminal_output_events = [
            event for event in events if event["name"] == "terminal_output"
        ]

        self.assertEqual(len(session_status_events), 1)
        self.assertEqual(len(terminal_output_events), 1)
        self.assertEqual(
            terminal_output_events[0]["args"][0],
            {
                "session_id": session.session_id,
                "data": "bootprompt",
            },
        )

    def test_join_session_replays_buffer_only_once_per_socket_client(self):
        api.session_manager.create_group(
            name="Buffered",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-buffered",
        )
        session = api.session_manager.create_session(
            group_id="group-buffered",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        with api.connection_lock:
            api.session_output_buffers[session.session_id] = "bootprompt"

        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)

        socket_client.emit("join_session", {"session_id": session.session_id})
        first_events = socket_client.get_received()
        socket_client.emit("join_session", {"session_id": session.session_id})
        second_events = socket_client.get_received()

        first_terminal_output_events = [
            event for event in first_events if event["name"] == "terminal_output"
        ]
        second_terminal_output_events = [
            event for event in second_events if event["name"] == "terminal_output"
        ]

        self.assertEqual(len(first_terminal_output_events), 1)
        self.assertEqual(len(second_terminal_output_events), 0)

    def test_leave_session_allows_buffer_replay_again(self):
        api.session_manager.create_group(
            name="Buffered",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-buffered",
        )
        session = api.session_manager.create_session(
            group_id="group-buffered",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        with api.connection_lock:
            api.session_output_buffers[session.session_id] = "bootprompt"

        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)

        socket_client.emit("join_session", {"session_id": session.session_id})
        socket_client.get_received()
        socket_client.emit("leave_session", {"session_id": session.session_id})
        socket_client.get_received()
        socket_client.emit("join_session", {"session_id": session.session_id})
        events = socket_client.get_received()

        terminal_output_events = [
            event for event in events if event["name"] == "terminal_output"
        ]

        self.assertEqual(len(terminal_output_events), 1)

    def test_clear_terminal_buffer_drops_replay_output_for_future_joins(self):
        api.session_manager.create_group(
            name="Buffered",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-buffered",
        )
        session = api.session_manager.create_session(
            group_id="group-buffered",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        with api.connection_lock:
            api.session_output_buffers[session.session_id] = "bootprompt"

        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)

        socket_client.emit("clear_terminal_buffer", {"session_id": session.session_id})

        with api.connection_lock:
            self.assertEqual(api.session_output_buffers[session.session_id], "")

        socket_client.get_received()
        socket_client.emit("join_session", {"session_id": session.session_id})
        events = socket_client.get_received()

        terminal_output_events = [
            event for event in events if event["name"] == "terminal_output"
        ]

        self.assertEqual(len(terminal_output_events), 0)

    def test_disconnect_clears_joined_session_tracking(self):
        api.session_manager.create_group(
            name="Buffered",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-buffered",
        )
        session = api.session_manager.create_session(
            group_id="group-buffered",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )

        socket_client.emit("join_session", {"session_id": session.session_id})
        socket_client.get_received()

        with api.connection_lock:
            self.assertEqual(len(api.client_joined_sessions), 1)

        socket_client.disconnect()

        with api.connection_lock:
            self.assertEqual(api.client_joined_sessions, {})

    def test_join_session_evicts_oldest_tracked_client_when_limit_is_exceeded(self):
        api.session_manager.create_group(
            name="Buffered",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-buffered",
        )
        session = api.session_manager.create_session(
            group_id="group-buffered",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        with patch.object(api, "_MAX_TRACKED_SOCKET_CLIENTS", 2):
            first_client = api.socketio.test_client(
                api.app,
                flask_test_client=self.client,
            )
            self.addCleanup(first_client.disconnect)
            first_client.emit("join_session", {"session_id": session.session_id})
            first_client.get_received()
            with api.connection_lock:
                first_client_id = next(iter(api.client_joined_sessions))

            second_client = api.socketio.test_client(
                api.app,
                flask_test_client=self.client,
            )
            self.addCleanup(second_client.disconnect)
            second_client.emit("join_session", {"session_id": session.session_id})
            second_client.get_received()

            third_client = api.socketio.test_client(
                api.app,
                flask_test_client=self.client,
            )
            self.addCleanup(third_client.disconnect)
            third_client.emit("join_session", {"session_id": session.session_id})
            third_client.get_received()

        with api.connection_lock:
            self.assertEqual(len(api.client_joined_sessions), 2)
            self.assertNotIn(first_client_id, api.client_joined_sessions)

    def test_whisper_voice_flow_buffers_audio_and_emits_final_result(self):
        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)

        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            iter(
                [
                    SimpleNamespace(text="hello"),
                    SimpleNamespace(text="terminal flow"),
                ]
            ),
            SimpleNamespace(language="en"),
        )

        with patch.object(api, "_ensure_whisper_model", return_value=mock_model), patch.object(
            api, "_pcm16le_to_float32", return_value="audio-array"
        ):
            socket_client.emit("voice_start", {"session_id": "session-whisper"})
            start_events = socket_client.get_received()

            socket_client.emit(
                "voice_audio",
                {"session_id": "session-whisper", "audio": b"\x00\x01\x02\x03"},
            )
            with api._whisper_audio_lock:
                buffered = bytes(api._whisper_audio_buffers["session-whisper"])

            socket_client.emit("voice_stop", {"session_id": "session-whisper"})
            stop_events = socket_client.get_received()

        self.assertEqual(buffered, b"\x00\x01\x02\x03")
        self.assertIn(
            {
                "name": "voice_status",
                "args": [{"session_id": "session-whisper", "status": "listening"}],
                "namespace": "/",
            },
            start_events,
        )
        mock_model.transcribe.assert_called_once_with(
            "audio-array",
            language="en",
            beam_size=1,
            best_of=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        self.assertIn(
            {
                "name": "voice_result",
                "args": [
                    {
                        "session_id": "session-whisper",
                        "text": "hello terminal flow",
                        "final": True,
                    }
                ],
                "namespace": "/",
            },
            stop_events,
        )
        self.assertIn(
            {
                "name": "voice_status",
                "args": [{"session_id": "session-whisper", "status": "stopped"}],
                "namespace": "/",
            },
            stop_events,
        )
        with api._whisper_audio_lock:
            self.assertNotIn("session-whisper", api._whisper_audio_buffers)

    # ── Theme support tests ──

    def test_launcher_page_includes_theme_css_variables(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn('[data-theme="light"]', html)
        self.assertIn("--bg:", html)
        self.assertIn("--accent:", html)

    def test_launcher_light_theme_overrides_hardcoded_element_backgrounds(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        light_selectors = [
            '[data-theme="light"] body',
            '[data-theme="light"] .count-btn',
            '[data-theme="light"] .field input',
            '[data-theme="light"] .t-row',
            '[data-theme="light"] .t-agent-select',
            '[data-theme="light"] .command-mode-btn',
            '[data-theme="light"] .check-field',
            '[data-theme="light"] .modal-card',
            '[data-theme="light"] .saved-session-item',
            '[data-theme="light"] .action-btn',
        ]
        for selector in light_selectors:
            self.assertIn(selector, html, f"Missing light theme override: {selector}")

    def test_launcher_page_includes_theme_toggle_control(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn('id="themeToggleBtnIndex"', html)
        self.assertIn("cycleTheme()", html)
        self.assertIn('id="themeControl"', html)
        self.assertIn('id="appSettingsBtn"', html)

    def test_launcher_page_includes_theme_js(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn("const THEME_STORAGE_KEY", html)
        self.assertIn("function normalizeThemePreference(", html)
        self.assertIn("function applyTheme(", html)
        self.assertIn("function cycleTheme()", html)
        self.assertIn("prefers-color-scheme", html)

    def test_terminals_page_includes_theme_css_variables(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn('[data-theme="light"]', html)
        self.assertIn("--t-bg:", html)
        self.assertIn("--t-accent:", html)
        self.assertIn("--t-topbar:", html)

    def test_terminals_page_includes_theme_toggle_control(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn('id="themeToggleBtn"', html)
        self.assertIn("cycleTheme()", html)

    def test_terminals_page_includes_theme_js(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("const THEME_STORAGE_KEY", html)
        self.assertIn("function normalizeThemePreference(", html)
        self.assertIn("function applyTheme(", html)
        self.assertIn("function cycleTheme()", html)
        self.assertIn("prefers-color-scheme", html)

    def test_terminals_page_uses_css_variables_for_structural_colors(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("var(--t-bg)", html)
        self.assertIn("var(--t-topbar)", html)
        self.assertIn("var(--t-text)", html)
        self.assertIn("var(--t-border)", html)

    def test_terminals_page_splits_the_longer_pane_dimension(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("grid?.classList.contains('layout-2-vertical')", html)
        self.assertIn("return candidates.includes('horizontal') ? 'horizontal' : '';", html)
        self.assertIn(
            "const preferred = bounds && bounds.height > bounds.width ? 'horizontal' : 'vertical';",
            html,
        )

    def test_terminals_page_exposes_grid_resize_handles(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn('id="terminalResizeOverlay"', html)
        self.assertIn(".terminal-resize-handle", html)
        self.assertIn("let splitColumnWeights = null;", html)
        self.assertIn("let splitRowWeights = null;", html)
        self.assertIn("let activeGridResize = null;", html)
        self.assertIn("function ensureResizableSplitLayout()", html)
        self.assertIn("function renderResizeHandles()", html)

    def test_terminals_page_resize_validation_enforces_minimums(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("const MIN_RESIZE_SURFACE_RATIO = 1 / 8;", html)
        self.assertIn(
            "const minimumSurface = metrics.columnTrackSpace * metrics.rowTrackSpace * MIN_RESIZE_SURFACE_RATIO;",
            html,
        )
        self.assertIn("surface.width * surface.height < minimumSurface", html)
        self.assertIn("Math.floor(availableWidth / cellWidth) >= MIN_SPLIT_COLS", html)
        self.assertIn("Math.floor(availableHeight / cellHeight) >= MIN_SPLIT_ROWS", html)

    def test_terminals_page_resize_drag_refits_and_forces_final_resize(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("window.addEventListener('pointermove', updateGridResize);", html)
        self.assertIn("window.addEventListener('pointerup', finishGridResize);", html)
        self.assertIn("function getResizeTrackGroups(axis, lineIndex)", html)
        self.assertIn("const beforeIndexes = resize.trackGroups.before;", html)
        self.assertIn("resize.affectedIndices.forEach(index => scheduleFit(index));", html)
        self.assertIn("redrawAttachedTerminals(affectedIndices, { forceResize: true });", html)
        self.assertIn("if (activeGridResize) {\n                event.preventDefault();", html)

    def test_terminals_page_cached_group_views_preserve_resize_weights(self):
        response = self.client.get("/terminals")
        html = response.get_data(as_text=True)
        self.assertIn("splitColumnWeights: hasLocalSplitLayout ? cloneSplitTrackWeights(splitColumnWeights) : null", html)
        self.assertIn("splitRowWeights: hasLocalSplitLayout ? cloneSplitTrackWeights(splitRowWeights) : null", html)
        self.assertIn("splitColumnWeights = cached.className === 'layout-split-local'", html)
        self.assertIn("splitRowWeights = cached.className === 'layout-split-local'", html)


# ---------------------------------------------------------------------------
#  Phase 1-3 regression tests (code_review_2026_03_31.md)
# ---------------------------------------------------------------------------


class SshConnectionErrorPathTestCase(unittest.TestCase):
    """Issue 3 — verify the SSH error path does not double-close the client."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        api.session_manager.reset_sessions()
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def tearDown(self):
        api.session_manager.reset_sessions()
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    @patch("web.api.paramiko")
    def test_ssh_connect_failure_closes_client_once(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.SSHException = type("SSHException", (Exception,), {})
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()
        mock_client.connect.side_effect = OSError("Connection refused")

        session = api.session_manager.create_session(
            group_id="grp1", host="127.0.0.1", directory="/tmp",
            username="root", password="pass",
        )

        api._connect_ssh_session(session.session_id, session)

        mock_client.close.assert_called_once()


class VoskStartupTimeoutTestCase(unittest.TestCase):
    """Issue 4 — verify process.wait() is called after kill on timeout."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True

    def tearDown(self):
        self.saved_sessions_patch.stop

    @patch("web.api._wait_for_vosk_ready", return_value=False)
    @patch("web.api._vosk_service_reachable", return_value=False)
    @patch("web.api.subprocess.Popen")
    def test_vosk_timeout_waits_after_kill(self, mock_popen, _mock_reachable, _mock_ready):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 99999
        mock_popen.return_value = mock_process

        original = api._vosk_process
        try:
            api._vosk_process = None
            result = api._ensure_vosk_service()
        finally:
            api._vosk_process = original

        self.assertFalse(result)
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_called_once()


class SshConnectExceptionHandlingTestCase(unittest.TestCase):
    """Issue 11 — verify the SSH connect handler uses narrow exception types."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        api.session_manager.reset_sessions()
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def tearDown(self):
        api.session_manager.reset_sessions()
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    @patch("web.api.paramiko")
    def test_ssh_connect_catches_os_error(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.SSHException = type("SSHException", (Exception,), {})
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()
        mock_client.connect.side_effect = OSError("Network unreachable")

        session = api.session_manager.create_session(
            group_id="grp1", host="127.0.0.1", directory="/tmp",
            username="root", password="pass",
        )
        api._connect_ssh_session(session.session_id, session)

        s = api.session_manager.get_session(session.session_id)
        self.assertEqual(s.status, api.SessionStatus.ERROR)

    @patch("web.api.paramiko")
    def test_ssh_connect_does_not_swallow_unexpected_errors(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.SSHException = type("SSHException", (Exception,), {})
        mock_paramiko.AutoAddPolicy.return_value = MagicMock()
        mock_client.connect.side_effect = RuntimeError("Unexpected bug")

        session = api.session_manager.create_session(
            group_id="grp1", host="127.0.0.1", directory="/tmp",
            username="root", password="pass",
        )

        with self.assertRaises(RuntimeError):
            api._connect_ssh_session(session.session_id, session)


class SessionOutputBufferTestCase(unittest.TestCase):
    """Issue 10 — verify session output buffers are freed on connection close."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def tearDown(self):
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def test_buffer_cleared_when_connection_closed(self):
        session_id = "buf-test"
        with api.connection_lock:
            api.session_output_buffers[session_id] = "some output data"

        api._close_ssh_connection(session_id)

        self.assertNotIn(session_id, api.session_output_buffers)

    def test_buffer_preserved_when_clear_buffer_false(self):
        session_id = "buf-keep"
        with api.connection_lock:
            api.session_output_buffers[session_id] = "keep this"

        api._close_ssh_connection(session_id, clear_buffer=False)

        self.assertIn(session_id, api.session_output_buffers)


class SessionStatusBroadcastRaceTestCase(unittest.TestCase):
    """Issue 7 — verify status emission is serialized with session removal."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api,
            "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        api.session_manager.reset_sessions()

    def tearDown(self):
        api.session_manager.reset_sessions()

    def test_broadcast_holds_session_lock_until_emit_returns(self):
        group = api.session_manager.create_group(
            name="Race",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-race",
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="10.0.0.12",
            directory="/tmp/project",
        )

        emit_started = threading.Event()
        allow_emit = threading.Event()
        remove_done = threading.Event()

        def blocking_emit(event_name, payload, *args, **kwargs):
            self.assertEqual(event_name, "session_status")
            self.assertEqual(payload["session_id"], session.session_id)
            emit_started.set()
            allow_emit.wait(timeout=1)

        def remove_session():
            api.session_manager.remove_group_sessions(group.group_id)
            remove_done.set()

        with patch.object(api.socketio, "emit", side_effect=blocking_emit):
            broadcaster = threading.Thread(
                target=api._broadcast_session_status,
                args=(session.session_id,),
            )
            remover = threading.Thread(target=remove_session)

            broadcaster.start()
            self.assertTrue(emit_started.wait(timeout=1))

            remover.start()
            self.assertFalse(remove_done.wait(timeout=0.1))

            allow_emit.set()
            broadcaster.join(timeout=1)
            remover.join(timeout=1)

        self.assertFalse(broadcaster.is_alive())
        self.assertFalse(remover.is_alive())
        self.assertTrue(remove_done.is_set())
        self.assertIsNone(api.session_manager.get_session(session.session_id))


class VoiceStartRaceTestCase(unittest.TestCase):
    """Issues 8 & 12 — verify voice start cleans up leaked connections."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        with api._vosk_lock:
            api._vosk_ws_connections.clear()
            api._vosk_session_locks.clear()

    def tearDown(self):
        with api._vosk_lock:
            api._vosk_ws_connections.clear()
            api._vosk_session_locks.clear()

    @patch("web.api.emit")
    @patch("web.api._ensure_vosk_service", return_value=True)
    @patch("web.api.ws_client")
    def test_voice_start_stores_connection_under_lock(self, mock_ws_client, _mock_ensure, _mock_emit):
        mock_ws = MagicMock()
        mock_ws_client.create_connection.return_value = mock_ws

        api._start_vosk_voice_session("sess-01")

        with api._vosk_lock:
            self.assertIn("sess-01", api._vosk_ws_connections)
            self.assertIs(api._vosk_ws_connections["sess-01"], mock_ws)

    @patch("web.api.emit")
    @patch("web.api._ensure_vosk_service", return_value=True)
    @patch("web.api.ws_client")
    def test_voice_start_closes_leaked_ws_on_concurrent_store(self, mock_ws_client, _mock_ensure, _mock_emit):
        """Simulate a concurrent writer that stored a connection between our pop and store."""
        leaked_ws = MagicMock()
        new_ws = MagicMock()
        mock_ws_client.create_connection.return_value = new_ws

        with api._vosk_lock:
            api._vosk_ws_connections["sess-02"] = leaked_ws

        api._start_vosk_voice_session("sess-02")

        leaked_ws.close.assert_called()
        with api._vosk_lock:
            self.assertIs(api._vosk_ws_connections["sess-02"], new_ws)

    @patch("web.api.emit")
    @patch("web.api._restart_vosk_service", return_value=True)
    @patch("web.api._ensure_vosk_service", return_value=True)
    @patch("web.api.ws_client")
    def test_retry_closes_first_ws_before_creating_second(self, mock_ws_client, _mock_ensure, _mock_restart, _mock_emit):
        """Issue 12 — first connection stored then fails; retry must close it."""
        first_ws = MagicMock()
        second_ws = MagicMock()

        call_count = 0

        def create_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_ws
            return second_ws

        mock_ws_client.create_connection.side_effect = create_side_effect
        first_ws.send.side_effect = OSError("Vosk down")

        api._start_vosk_voice_session("sess-03")

        first_ws.close.assert_called()
        with api._vosk_lock:
            self.assertIs(api._vosk_ws_connections["sess-03"], second_ws)


class LocalPtyStreamTestCase(unittest.TestCase):
    """Issue 9 — verify PTY stream handles closed fd without crashing."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def tearDown(self):
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    @patch("web.api._broadcast_session_status")
    @patch("web.api.session_manager")
    @patch("select.select", return_value=([999], [], []))
    @patch("os.read", side_effect=OSError(9, "Bad file descriptor"))
    def test_stream_local_output_handles_closed_master_fd(
        self, _mock_read, _mock_select, mock_session_mgr, _mock_broadcast
    ):
        session_id = "pty-fd-test"
        with api.connection_lock:
            api.ssh_connections[session_id] = {
                "process": None,
                "pty_process": None,
                "master_fd": 999,
                "stdout": None,
            }

        mock_session = MagicMock()
        mock_session.status = api.SessionStatus.CONNECTED
        mock_session_mgr.get_session.return_value = mock_session

        # Should exit cleanly without propagating OSError
        api._stream_local_output(session_id)

        # _finalize_stream should set status to DISCONNECTED (not ERROR)
        mock_session_mgr.update_session_status.assert_called_with(
            session_id, api.SessionStatus.DISCONNECTED
        )


class VoiceAudioRaceTestCase(unittest.TestCase):
    """Issue 1 — verify voice audio handles ws closure gracefully."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            api, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        with api._vosk_lock:
            api._vosk_ws_connections.clear()
            api._vosk_session_locks.clear()

    def tearDown(self):
        with api._vosk_lock:
            api._vosk_ws_connections.clear()
            api._vosk_session_locks.clear()

    @patch("web.api.emit")
    def test_voice_audio_handles_ws_closed_between_lock_and_send(self, mock_emit):
        session_id = "voice-race-01"
        mock_ws = MagicMock()
        mock_ws.send.side_effect = ConnectionError("WebSocket is already closed")

        with api._vosk_lock:
            api._vosk_ws_connections[session_id] = mock_ws
            api._vosk_session_locks[session_id] = threading.Lock()

        api._handle_vosk_audio_chunk(session_id, b"\x00\x01\x02\x03")

        mock_ws.close.assert_called()
        with api._vosk_lock:
            self.assertNotIn(session_id, api._vosk_ws_connections)
            self.assertNotIn(session_id, api._vosk_session_locks)

        error_events = [
            call for call in mock_emit.call_args_list
            if call[0][0] == 'voice_status'
        ]
        self.assertTrue(len(error_events) > 0)
        self.assertEqual(error_events[0][0][1]['status'], 'error')

    @patch("web.api.emit")
    def test_voice_audio_dropped_when_no_connection(self, mock_emit):
        api._handle_vosk_audio_chunk("voice-no-conn", b"\x00\x01\x02\x03")

        mock_emit.assert_not_called()
