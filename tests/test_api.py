import base64
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import api
from gridvibe_version import __version__
from web import agents as web_agents
from web import app as web_app
from web import config as web_config
from web import explorer as web_explorer
from web import hostkeys as web_hostkeys
from web import paths as web_paths
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import selfupdate
from web import terminal_io as web_terminal_io
from web import voice as web_voice


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


class FakeSshStream:
    def __init__(self, data=b"", returncode=0):
        self._data = data
        self.channel = SimpleNamespace(recv_exit_status=lambda: returncode)

    def read(self):
        return self._data


class FakeSshExecClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.commands = []
        self.closed = False

    def exec_command(self, command, timeout=None):
        self.commands.append((command, timeout))
        if not self.responses:
            raise OSError("Unexpected SSH command")
        returncode, stdout, stderr = self.responses.pop(0)
        return (
            None,
            FakeSshStream(stdout, returncode),
            FakeSshStream(stderr, returncode),
        )

    def close(self):
        self.closed = True


class ApiRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path_patch = patch.object(
            web_config,
            "CONFIG_PATH",
            str(self.config_path),
        )
        self.config_path_patch.start()
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        self.saved_sessions_patch = patch.object(
            web_saved_sessions,
            "SAVED_SESSIONS_PATH",
            str(self.saved_sessions_path),
        )
        self.saved_sessions_patch.start()
        api._refresh_runtime_config()
        api.app.config["TESTING"] = True
        api.configure_browser_shutdown(False)
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
        web_voice._whisper_model_instance = None
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

    def _page_html(self, response) -> str:
        """Return page HTML plus its extracted static CSS/JS.

        Finding 3.5 moved the inline styles and scripts to web/static/, so
        content assertions look at the page and its own assets together.
        """
        html = response.get_data(as_text=True)
        for asset in (
            "css/launcher.css",
            "css/app-settings.css",
            "js/shared.js",
            "js/app-settings.js",
            "js/launcher.js",
            "css/terminals.css",
            "js/terminal-icons.js",
            "js/voice-input.js",
            "js/explorer-viewer.js",
            "js/explorer-editor.js",
            "js/explorer-search.js",
            "js/explorer-fs.js",
            "js/explorer-git-watch.js",
            "js/browser-pane.js",
            "js/terminal-shell.js",
            "js/terminals.js",
        ):
            marker = f"/static/{asset}"
            if marker in html:
                html += "\n" + self.client.get(marker).get_data(as_text=True)
        return html

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _run_git(self, repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        return subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def tearDown(self):
        api.configure_browser_shutdown(False)
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
        web_voice._whisper_model_instance = None
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

    def test_browser_shutdown_button_is_hidden_outside_browser_mode(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertNotIn('id="browserCloseBtn"', html)

    def test_browser_shutdown_button_is_rendered_in_browser_mode(self):
        token = api.configure_browser_shutdown(True)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('id="browserCloseBtn"', html)
        self.assertIn("onclick=\"shutdownBrowserApp()\"", html)
        self.assertIn(f'const BROWSER_SHUTDOWN_TOKEN = "{token}";', html)

    def test_browser_shutdown_endpoint_is_unavailable_outside_browser_mode(self):
        with patch.object(api, "_schedule_browser_shutdown") as schedule_shutdown:
            response = self.client.post("/api/browser-shutdown")

        self.assertEqual(response.status_code, 404)
        schedule_shutdown.assert_not_called()

    def test_browser_shutdown_endpoint_rejects_invalid_token(self):
        api.configure_browser_shutdown(True)

        with patch.object(api, "_schedule_browser_shutdown") as schedule_shutdown:
            response = self.client.post(
                "/api/browser-shutdown",
                headers={"X-GridVibe-Shutdown-Token": "invalid"},
            )

        self.assertEqual(response.status_code, 403)
        schedule_shutdown.assert_not_called()

    def test_browser_shutdown_endpoint_schedules_process_exit(self):
        token = api.configure_browser_shutdown(True)

        with patch.object(api, "_schedule_browser_shutdown") as schedule_shutdown:
            response = self.client.post(
                "/api/browser-shutdown",
                headers={"X-GridVibe-Shutdown-Token": token},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {"message": "GridVibe is shutting down"})
        schedule_shutdown.assert_called_once_with()

    def test_browser_shutdown_worker_closes_sessions_and_exits_process(self):
        with patch.object(api.time, "sleep") as sleep, patch.object(
            api.session_manager,
            "close_all_sessions",
        ) as close_all_sessions, patch.object(
            api.os,
            "_exit",
            side_effect=SystemExit(0),
        ) as process_exit:
            with self.assertRaises(SystemExit):
                api._shutdown_browser_process()

        sleep.assert_called_once_with(0.2)
        close_all_sessions.assert_called_once_with()
        process_exit.assert_called_once_with(0)

    def test_windows_launcher_prompts_for_missing_voice_dependencies(self):
        launcher = (Path(api.BASE_DIR) / "GridVibe.bat").read_text(encoding="utf-8")

        self.assertIn("Checking optional voice dependencies", launcher)
        self.assertIn("faster_whisper", launcher)
        self.assertIn("requirements-voice.txt", launcher)
        self.assertIn("choice /C YN", launcher)

    def test_windows_launcher_only_asks_about_voice_when_voice_input_is_enabled(self):
        """Stage J issue 4: the prompt came back on every launch even though
        voice input ships disabled, and a decline was never remembered."""
        launcher = (Path(api.BASE_DIR) / "GridVibe.bat").read_text(encoding="utf-8")

        gate_index = launcher.index("get('voice_input', {}).get('enabled')")
        prompt_index = launcher.index("choice /C YN")
        marker_index = launcher.index('> ".voice-deps-declined"')

        self.assertLess(gate_index, prompt_index)
        self.assertLess(prompt_index, marker_index)
        self.assertEqual(launcher.count("choice /C YN"), 1)
        self.assertIn(":start_gridvibe", launcher)
        self.assertIn('if exist ".voice-deps-declined" (', launcher)

    def test_voice_dependency_decline_marker_is_gitignored(self):
        gitignore = (Path(api.BASE_DIR) / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".voice-deps-declined", gitignore.split())

    def test_windows_launcher_clears_optional_dependency_error_before_start(self):
        launcher = (Path(api.BASE_DIR) / "GridVibe.bat").read_text(encoding="utf-8")

        voice_choice_index = launcher.index("choice /C YN")
        reset_index = launcher.index("cmd /c exit 0", voice_choice_index)
        start_index = launcher.index('start "GridVibe"', reset_index)
        failure_check_index = launcher.index("if errorlevel 1", start_index)

        self.assertLess(voice_choice_index, reset_index)
        self.assertLess(reset_index, start_index)
        self.assertLess(start_index, failure_check_index)

    def test_windows_launcher_selects_desktop_browser_or_quit_after_core_setup(self):
        launcher = (Path(api.BASE_DIR) / "GridVibe.bat").read_text(encoding="utf-8")

        prompt_index = launcher.index("choice /C DBQ")
        core_check_index = launcher.index("Core dependency import check passed.")
        desktop_install_index = launcher.index("Installing optional desktop dependencies")

        self.assertGreater(prompt_index, core_check_index)
        self.assertLess(prompt_index, desktop_install_index)
        self.assertIn('set "LAUNCH_MODE=auto"', launcher)
        self.assertIn('set "LAUNCH_MODE=browser"', launcher)
        self.assertIn("if errorlevel 3 exit /b 0", launcher)
        self.assertIn("--mode %LAUNCH_MODE%", launcher)
        self.assertIn("goto check_voice_dependencies", launcher)

    def test_launcher_page_exposes_agent_startup_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const AGENT_OPTIONS = [", html)
        self.assertIn('class="startup-mode-select"', html)
        self.assertIn('<option value="terminal"', html)
        self.assertIn('<option value="agent"', html)
        self.assertIn('aria-label="Terminal ${index + 1} title"', html)
        self.assertIn("function normalizeTerminalCommandUi(terminal)", html)
        self.assertIn("Custom Agent", html)

    def test_launcher_page_hides_windows_shell_options_on_posix(self):
        with patch.object(api.os, "name", "posix"):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # The shell-option markup moved to static JS (finding 3.5), so hiding
        # is now a runtime gate on the server-rendered constant.
        self.assertIn("const LOCAL_WINDOWS_SHELLS_AVAILABLE = false;", html)
        self.assertIn("${LOCAL_WINDOWS_SHELLS_AVAILABLE ? `", html)

    def test_launcher_page_shows_windows_shell_options_on_windows(self):
        with patch.object(api.os, "name", "nt"):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const LOCAL_WINDOWS_SHELLS_AVAILABLE = true;", html)
        self.assertIn("Prefer WSL", html)
        self.assertIn("Use PowerShell", html)
        self.assertIn("Ubuntu Distro", html)

    def test_launcher_page_exposes_agent_preflight_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("/api/agent-preflight", html)
        self.assertIn("function queueAgentPreflight(row)", html)
        self.assertIn("function scheduleAgentPreflight(row, delayMs = 180)", html)
        self.assertIn('class="agent-preflight-disclosure"', html)
        self.assertIn('status-installed', html)
        self.assertIn('\"value\": \"claude\"', html)

    def test_launcher_page_exposes_ssh_ping_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('id="sshPingBtn"', html)
        self.assertIn('id="sshPingStatus"', html)
        self.assertIn("function initSshPingButton()", html)
        self.assertIn("/api/ssh-ping", html)

    def test_launcher_page_exposes_check_for_updates_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('id="checkUpdatesBtn"', html)
        self.assertIn('title="Check for updates"', html)
        self.assertIn("async function checkForUpdates()", html)
        self.assertIn("/api/app-update", html)
        self.assertIn("window.pywebview?.api?.restart_application", html)

    def test_launcher_page_exposes_app_settings_modal_and_cog_button(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('id="appSettingsBtn"', html)
        self.assertIn("function openAppSettings()", html)
        self.assertIn("function saveAppSettings()", html)
        self.assertIn('id="appSettingsModal"', html)
        self.assertIn('/api/app-config', html)
        self.assertIn('id="appTheme"', html)
        self.assertIn('id="appSurfaceMode"', html)
        self.assertIn('Session Window', html)
        self.assertIn('id="appVoiceEngine"', html)
        self.assertIn('id="appWhisperDevice"', html)
        self.assertIn('id="appVoiceProfile"', html)
        self.assertIn('id="appVoiceDevice"', html)
        self.assertIn('id="appVoicePttEnabled"', html)
        self.assertIn('id="appVoicePttKeybind"', html)
        self.assertIn("function refreshAppMicrophones()", html)
        self.assertIn('/api/voice-prefs', html)
        self.assertIn('<select id="appWhisperModel">', html)
        self.assertIn('<option value="base">base</option>', html)
        self.assertIn('<option value="large-v3-turbo">large-v3-turbo</option>', html)
        self.assertIn("const APP_CONFIG_UPDATE_STORAGE_KEY = 'gridvibe.appConfigUpdated';", html)
        self.assertIn("const APP_CONFIG_BROADCAST_CHANNEL = 'gridvibe.appConfig';", html)
        self.assertIn("function notifyAppConfigUpdated(appSettings, applyScope = 'session')", html)
        self.assertIn("notifyAppConfigUpdated(data, settingsForm.terminal.apply_scope);", html)

    def test_launcher_page_uses_compact_centered_header(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('<h1>Launcher Setup</h1>', html)
        self.assertIn('src="/docs/images/GridVibe_icon.ico"', html)
        self.assertIn('<div class="app-titlebar-right">', html)
        self.assertIn('<span>Session</span>', html)
        self.assertIn('<span>Mode</span>', html)
        self.assertNotIn("Configure your terminal workspace before launch.", html)

    def test_launcher_page_resets_terminal_setup_when_connection_target_changes(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function resetTerminalSetupIfTargetChanged", html)
        self.assertIn("buildTerminalRows(selectedCount, buildDefaultTerminalDrafts());", html)
        self.assertIn("resetTerminalSetupIfTargetChanged(connectionMode, collectModeInputs());", html)
        self.assertIn("bindModeFieldInteractions();", html)

    def test_terminals_page_empty_state_launch_button_reuses_settings_handler(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn(
            '<a href="/" onclick="return goToSettings(event)">Launch terminals →</a>',
            html,
        )

    def test_terminals_page_exposes_session_menu_actions(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('src="/docs/images/GridVibe_icon.ico"', html)
        self.assertIn(">Sessions...</button>", html)
        self.assertIn(">Import Session ...</button>", html)
        self.assertIn(">Save Session</button>", html)
        self.assertIn(">Save Session as ...</button>", html)
        self.assertIn("onclick=\"closeSessionsMenu(); return openNewSessionSelector(event);\"", html)
        self.assertIn("onclick=\"closeSessionsMenu(); saveActiveWorkspaceSession(this);\"", html)
        self.assertIn("onclick=\"closeSessionsMenu(); saveActiveWorkspaceSessionAs(this);\"", html)
        self.assertIn('<div id="savedSessionsModal" class="modal-shell" aria-hidden="true">', html)
        self.assertIn('<div id="saveSessionAsModal" class="modal-shell" aria-hidden="true">', html)
        self.assertIn('<input id="saveSessionAsOpenNow" type="checkbox">', html)
        self.assertIn("<span>Open session now</span>", html)
        self.assertIn("function openNewSessionSelector(event)", html)
        self.assertIn("function buildSavedSessionLaunchPayload(savedSession)", html)
        self.assertIn("await launchSavedSession(data);", html)
        self.assertNotIn("container.appendChild(settingsButton);", html)
        self.assertNotIn("container.appendChild(saveButton);", html)
        self.assertIn("async function saveActiveWorkspaceSession(button = null, options = {})", html)
        self.assertIn("function saveActiveWorkspaceSessionAs(button = null)", html)
        self.assertIn("async function saveAllWorkspaceSessions(button = null)", html)
        self.assertIn("closeSessionsMenu(); saveAllWorkspaceSessions(this);", html)
        self.assertIn('id="saveAllSessionsMenuItem"', html)
        self.assertIn("let workspaceSaveTargets = new Map();", html)
        self.assertIn("function notifySavedSessionUpdated(savedSession, options = {})", html)
        self.assertIn("const SAVED_SESSION_UPDATE_STORAGE_KEY = 'gridvibe.savedSessionUpdated';", html)
        self.assertIn("notifySavedSessionUpdated(data, { activate: openSavedSessionNow });", html)
        self.assertIn("function getWorkspacePanesInVisualOrder(groupId = activeGroupId)", html)
        self.assertIn("function getActiveWorkspaceGroupId()", html)
        self.assertIn("function buildActiveWorkspaceSessionConfig(groupId = activeGroupId)", html)
        self.assertIn("function buildActiveWorkspaceLayoutSnapshot(groupId = activeGroupId)", html)
        self.assertIn("function applyWorkspaceLayoutSnapshot(snapshot, expectedCount)", html)
        self.assertIn("workspace_layout: buildActiveWorkspaceLayoutSnapshot(groupId)", html)
        self.assertIn("applyWorkspaceLayoutSnapshot(data.workspace_layout, data.sessions.length);", html)
        save_config_start = html.index("function buildActiveWorkspaceSessionConfig(groupId = activeGroupId)")
        save_config_end = html.index("async function saveActiveWorkspaceSession", save_config_start)
        save_config_html = html[save_config_start:save_config_end]
        self.assertIn("const groupTerminals = getWorkspacePanesInVisualOrder(groupId);", save_config_html)
        self.assertNotIn("? terminals", save_config_html)
        save_handler_start = html.index("async function saveActiveWorkspaceSession")
        save_handler_end = html.index("function saveActiveWorkspaceSessionAs", save_handler_start)
        save_handler_html = html[save_handler_start:save_handler_end]
        self.assertIn("const targetGroupId = options.groupId || getActiveWorkspaceGroupId();", save_handler_html)
        self.assertIn("const config = buildActiveWorkspaceSessionConfig(targetGroupId);", save_handler_html)
        self.assertIn("const saveTarget = getWorkspaceSaveTarget(targetGroupId);", save_handler_html)
        self.assertIn("const result = await openSaveSessionAsModal(suggestedName);", save_handler_html)
        self.assertIn("activate: shouldActivateSavedSession", save_handler_html)
        self.assertIn("savePayload.group_id = targetGroupId;", save_handler_html)
        self.assertIn("data.group?.group_id", save_handler_html)
        self.assertIn("if (createNewSession && openSavedSessionNow)", save_handler_html)
        self.assertIn("targetGroupId,", save_handler_html)
        save_target_start = html.index("function getWorkspaceSaveTarget")
        save_target_end = html.index("function rememberWorkspaceSaveTarget", save_target_start)
        save_target_html = html[save_target_start:save_target_end]
        self.assertIn("const groupSavedSessionId = String(group?.saved_session_id || '').trim();", save_target_html)
        self.assertLess(
            save_target_html.index("groupSavedSessionId"),
            save_target_html.index("workspaceSaveTargets.get(groupId)"),
        )
        entry_start = html.index("function buildWorkspaceTerminalEntry")
        entry_end = html.index("function buildActiveWorkspaceSessionConfig(groupId = activeGroupId)", entry_start)
        entry_html = html[entry_start:entry_end]
        self.assertIn("session_id: session.session_id || ''", entry_html)
        self.assertIn("session.explorer_root_directory || session.directory", entry_html)
        self.assertNotIn("terminal?._explorerPath", entry_html)
        self.assertIn("Boolean(terminal?._explorerTreeSidebarOpen)", entry_html)
        self.assertIn("Boolean(terminal?._explorerGitSidebarOpen)", entry_html)
        self.assertIn("terminal?._cachedExplorerTheme", entry_html)
        cache_state_start = html.index("function captureCachedPaneUiState()")
        cache_state_end = html.index("function restoreCachedPaneUiState", cache_state_start)
        cache_state_html = html[cache_state_start:cache_state_end]
        self.assertIn("explorerCaptureActiveTabView(index);", cache_state_html)
        self.assertIn("terminal._cachedExplorerTheme = normalizeExplorerTheme(", cache_state_html)
        self.assertIn("function restoreExplorerSidebarState(index)", html)
        self.assertIn("_explorerTreeSidebarOpen: Boolean(session.explorer_tree_open)", html)
        self.assertIn("_explorerGitSidebarOpen: Boolean(session.explorer_git_open)", html)
        self.assertIn("_explorerSearchSidebarOpen: Boolean(session.explorer_search_open)", html)
        self.assertIn("workspace_only: true", save_handler_html)
        self.assertIn("source_saved_session_id: saveTarget.id || undefined", save_handler_html)

    def test_launcher_forwards_saved_workspace_layout_and_agent_metadata(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("let activeWorkspaceLayout = null;", html)
        self.assertIn("function clearActiveWorkspaceLayoutOverride()", html)
        self.assertIn("workspace_layout: workspaceLayout", html)
        self.assertIn("activeWorkspaceLayout = normalized.workspace_layout || null;", html)
        self.assertIn("workspace_layout: config.workspace_layout", html)
        self.assertIn("initial_command_mode: resolvedStartupMode === 'explorer'", html)
        self.assertIn("agent_selection: resolvedStartupMode === 'agent'", html)
        self.assertIn("data-explorer-tree-open=", html)
        self.assertIn("data-explorer-git-open=", html)
        self.assertIn("data-explorer-search-open=", html)
        self.assertIn("explorer_tree_open: resolvedStartupMode === 'explorer'", html)
        self.assertIn("explorer_git_open: resolvedStartupMode === 'explorer'", html)
        self.assertIn("explorer_search_open: resolvedStartupMode === 'explorer'", html)
        self.assertIn('<option value="browser"', html)
        self.assertIn('class="field t-browser-field', html)
        self.assertIn("function normalizeBrowserPaneUrl(value)", html)
        self.assertIn("browser_tabs: resolvedStartupMode === 'browser'", html)
        layout_change_start = html.index("container.querySelectorAll('.layout-btn').forEach")
        layout_change_end = html.index("function renderModeFields", layout_change_start)
        layout_change_html = html[layout_change_start:layout_change_end]
        self.assertIn("clearActiveWorkspaceLayoutOverride();", layout_change_html)
        self.assertLess(
            layout_change_html.index("clearActiveWorkspaceLayoutOverride();"),
            layout_change_html.index("selectedLayout = nextLayout"),
        )

    def test_launcher_refreshes_active_saved_session_after_external_update(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const SAVED_SESSION_UPDATE_STORAGE_KEY = 'gridvibe.savedSessionUpdated';", html)
        self.assertIn("const SAVED_SESSION_BROADCAST_CHANNEL = 'gridvibe.savedSessions';", html)
        self.assertIn("async function refreshActiveSavedSessionFromUpdate(payload)", html)
        self.assertIn("const shouldActivate = Boolean(payload?.activate);", html)
        self.assertIn("(!shouldActivate && sessionId !== activeSavedSessionId)", html)
        self.assertIn("fetch(`/api/saved-sessions/${encodeURIComponent(sessionId)}`)", html)
        self.assertIn("applySessionConfig(data.config);", html)
        self.assertIn("setupSavedSessionUpdateListeners();", html)

    def test_terminals_page_new_session_opens_saved_session_selector_without_launcher(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)

        menu_start = html.index('>Import Session ...</button>')
        go_to_settings_start = html.index("async function goToSettings(event)")
        open_selector_start = html.index("async function openNewSessionSelector(event)")

        self.assertIn("openNewSessionSelector(event)", html[:menu_start])
        self.assertNotIn(
            "window.pywebview?.api?.open_launcher_window",
            html[open_selector_start:go_to_settings_start],
        )

    def test_terminals_page_uses_icon_only_launcher_button(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('class="btn btn-neutral btn-icon settings-window-btn"', html)
        # Settings themselves now open in-page, so this button only opens the
        # launcher window and says so.
        self.assertIn('aria-label="Open launcher"', html)
        self.assertIn('class="vibe-flow-icon"', html)

    def test_terminals_page_opens_app_settings_without_the_launcher(self):
        """The session window carries its own App Settings dialog (todo 1) —
        the shared partial plus the shared module, no launcher round-trip."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('id="appSettingsBtn"', html)
        self.assertIn('onclick="openAppSettings()"', html)
        self.assertIn('id="appSettingsModal"', html)
        self.assertIn('id="appTheme"', html)
        self.assertIn('id="appTerminalFontSize"', html)
        self.assertIn('id="appVoiceEngine"', html)
        self.assertIn("async function openAppSettings()", html)
        self.assertIn("async function saveAppSettings()", html)
        # A window never gets its own broadcast, so the save applies here too.
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function onAppSettingsSaved(_data, payload)", terminals_js)
        self.assertIn("applyAppConfigUpdate(payload);", terminals_js)

    def test_topbar_icons_state_their_own_size(self):
        """An unsized inline SVG falls back to 300x150 and only looks right
        while the button can squeeze it — max surface mode (32x28, no padding)
        blew up the broadcast and settings cogs."""
        html = self.client.get("/terminals").get_data(as_text=True)
        terminals_css = self._static("css/terminals.css")
        for icon in (
            "broadcast-icon",
            "app-settings-icon",
            "surface-mode-icon",
            "refresh-all-icon",
            "fullscreen-icon",
            "vibe-flow-icon",
        ):
            with self.subTest(icon=icon):
                self.assertIn(f'class="{icon}"', html)
                rule = re.search(
                    rf"\.{icon}\b[^{{]*\{{[^}}]*\}}", terminals_css, re.DOTALL
                )
                self.assertIsNotNone(rule)
                self.assertIn("width:", rule.group(0))
                self.assertIn("height:", rule.group(0))

    def test_docs_images_route_serves_gridvibe_icon(self):
        response = self.client.get("/docs/images/GridVibe_icon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.get_data()), 0)

    def test_terminals_page_exposes_per_terminal_refresh_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # The button itself is rendered by terminal-shell.js (it doubles as the
        # local shell picker), still wired per pane slot by terminals.js.
        self.assertIn('data-terminal-refresh="${index}"', html)
        self.assertIn('wireCardButton(card, `[data-terminal-refresh="${i}"]`', html)
        self.assertIn("function setTerminalRefreshState(index, refreshing)", html)
        self.assertIn("async function refreshTerminalDisplay(index)", html)

    def test_terminals_page_explorer_bar_has_refresh_before_up_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        initial_refresh = 'data-explorer-refresh="${i}"'
        initial_up = 'data-explorer-up="${i}"'
        dynamic_refresh = 'data-explorer-refresh="${index}"'
        dynamic_up = 'data-explorer-up="${index}"'
        self.assertEqual(html.count(initial_refresh), 2)
        self.assertEqual(html.count(dynamic_refresh), 2)
        self.assertLess(html.index(initial_refresh), html.index(initial_up))
        self.assertLess(html.index(dynamic_refresh), html.index(dynamic_up))
        self.assertIn('aria-label="Refresh explorer"', html)
        self.assertIn(
            'wireCardButton(card, `[data-explorer-refresh="${i}"]`, () => refreshTerminalDisplay(i));',
            html,
        )
        self.assertIn("refreshTerminalDisplay(index);", html)
        self.assertIn("const explorerRefreshButton = document.getElementById(`explorer-refresh-${index}`);", html)
        self.assertIn("explorerRefreshButton.disabled = isBusy;", html)

    def test_terminals_page_explorer_shortcuts_refresh_and_navigate_parent(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('title="Refresh explorer (F5)"', html)
        self.assertIn('title="Go to parent directory (Mouse Back)"', html)
        self.assertIn("function findExplorerShortcutTargetIndex(", html)
        self.assertIn("function navigateExplorerToParent(index)", html)
        self.assertIn("event.key !== 'F5'", html)
        self.assertIn("refreshTerminalDisplay(index);", html)
        self.assertIn("event.button !== 3", html)
        self.assertIn("const index = explorerPaneIndexFromTarget(event.target);", html)
        self.assertIn("navigateExplorerToParent(index);", html)

    def test_terminals_page_exposes_per_terminal_close_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-terminal-close="${i}"', html)
        self.assertIn('aria-label="Close this terminal pane"', html)
        self.assertIn(".terminal-close-btn", html)
        self.assertIn("border-color: rgba(255, 92, 92, .7);", html)
        self.assertIn("function buildCloseTerminalPlan(index)", html)
        self.assertIn("function findTerminalCloseNeighbor(closedRect, candidates)", html)
        self.assertIn("function terminalCloseSideGroups(closedRect, entries)", html)
        self.assertIn("function buildTerminalCloseRectsForSideGroup(plan, sideGroup)", html)
        self.assertIn("function sharedBorderLength(left, right)", html)
        self.assertIn("async function closeTerminalPane(index)", html)
        self.assertIn(
            'wireCardButton(card, `[data-terminal-close="${i}"]`, () => closeTerminalPane(i));',
            html,
        )
        self.assertIn("closeTerminalPane(index);", html)
        self.assertIn("rectsBySessionId: restoreRectsBySessionId", html)
        self.assertIn("no neighboring pane can safely fill this layout", html)
        self.assertIn("method: 'DELETE'", html)
        close_plan_start = html.index("function buildTerminalCloseRectsBySessionId(plan)")
        close_plan_end = html.index("function buildCloseTerminalPlan(index)", close_plan_start)
        self.assertNotIn("fixedLayoutSlotRects(", html[close_plan_start:close_plan_end])

    def test_terminals_page_close_prefers_single_neighbor_expansion(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn(
            "function terminalCloseRectsForExpandingContacts(plan, side, contactsToExpand)",
            html,
        )
        side_group_start = html.index("function buildTerminalCloseRectsForSideGroup(plan, sideGroup)")
        side_group_end = html.index("function buildTerminalCloseRectsBySessionId(plan)", side_group_start)
        side_group_html = html[side_group_start:side_group_end]
        # The single greatest-shared-border contact is attempted before the whole
        # side group, so a close never resizes more neighbours than required.
        self.assertIn(
            "const single = terminalCloseRectsForExpandingContacts(plan, sideGroup.side, [singleContact]);",
            side_group_html,
        )
        self.assertIn("if (singleContact && sideGroup.entries.length > 1) {", side_group_html)
        self.assertIn(
            "return terminalCloseRectsForExpandingContacts(plan, sideGroup.side, sideGroup.entries);",
            side_group_html,
        )
        # The single-pane result is still validated by the same overlap + area
        # invariants inside the shared helper.
        expand_start = html.index("function terminalCloseRectsForExpandingContacts(plan, side, contactsToExpand)")
        expand_end = html.index("function buildTerminalCloseRectsForSideGroup(plan, sideGroup)", expand_start)
        expand_html = html[expand_start:expand_end]
        self.assertIn("splitRectsOverlap(nextEntries[leftIndex].rect, nextEntries[rightIndex].rect)", expand_html)
        self.assertIn("if (nextArea !== previousArea + splitRectArea(plan.closedRect)) {", expand_html)

    def test_terminals_page_close_preserves_split_track_weights(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # The terminal-close path carries the pre-close track weights into the
        # restore (closing a split pane now goes through this same path).
        self.assertEqual(
            html.count("splitColumnWeights: cloneSplitTrackWeights(splitColumnWeights),"),
            1,
        )
        self.assertEqual(
            html.count("splitRowWeights: cloneSplitTrackWeights(splitRowWeights),"),
            1,
        )
        # initialLoad re-applies them onto the reflowed grid so proportions survive.
        self.assertIn(
            "splitColumnWeights = cloneSplitTrackWeights(pendingRestore.splitColumnWeights);",
            html,
        )
        self.assertIn(
            "splitRowWeights = cloneSplitTrackWeights(pendingRestore.splitRowWeights);",
            html,
        )

    def test_terminals_page_close_preserves_sibling_pane_state(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("let pendingCloseClientState = null;", html)
        self.assertIn("function captureSurvivingPaneClientState(closingSessionId)", html)
        self.assertIn("function restoreExplorerPaneFromClose(index, snapshot)", html)
        # The close path captures surviving pane state before the forced rebuild.
        self.assertIn(
            "stateBySessionId: captureSurvivingPaneClientState(plan.sessionId),",
            html,
        )
        # Explorer siblings keep tree/Git sidebars and open tabs; browser siblings
        # keep their URL — all overlaid onto the fetched session objects.
        self.assertIn("entry.explorer_tree_open = snapshot.explorer_tree_open;", html)
        self.assertIn("entry.explorer_git_open = snapshot.explorer_git_open;", html)
        self.assertIn("entry.explorer_search_open = snapshot.explorer_search_open;", html)
        self.assertIn("entry.explorer_open_tabs = snapshot.explorer_open_tabs;", html)
        self.assertIn("entry.explorer_active_tab = snapshot.explorer_active_tab;", html)
        self.assertIn("entry.initial_command = snapshot.browser_url;", html)
        # Close-affected explorer panes restore through the viewer, not a listing.
        self.assertIn("restoreExplorerPaneFromClose(i, closeSnapshot);", html)
        self.assertIn(
            "openExplorerFile(index, snapshot.explorer_preview_path, { pinned: false, showLoading: false });",
            html,
        )
        # The snapshot is only consumed for its own close-driven rebuild.
        self.assertIn(
            "const closeClientState = pendingCloseClientState?.groupId === requestedGroupId",
            html,
        )

    def test_terminals_page_close_preserves_tab_view_state(self):
        """5.a: per-tab view mode, scroll, and zoom survive a terminal-close rebuild."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        capture = html[
            html.index("function captureSurvivingPaneClientState(closingSessionId)"):
            html.index("function restoreExplorerPaneFromClose(index, snapshot)")
        ]
        # The shown tab's live mode + scroll are folded into its record before
        # the tabs are serialized into the snapshot.
        self.assertLess(
            capture.index("explorerCaptureActiveTabView(index);"),
            capture.index("const tabs = explorerSerializeTabs(pane);"),
        )
        # Full-fidelity tab records ride the snapshot (view + zoom + preference),
        # alongside the disk-shape views for the session overlay.
        self.assertIn("explorer_tab_state: pane._explorerTabs.map(tab => ({", capture)
        self.assertIn("explorer_tab_views: tabs.tab_views,", capture)
        restore = html[
            html.index("function restoreExplorerPaneFromClose(index, snapshot)"):
            html.index("async function closeTerminalPane(index)")
        ]
        # Records are reattached synchronously, before the active tab's async
        # re-fetch resolves, so the 2.e identity check restores mode + scroll.
        self.assertIn("snapshot.explorer_tab_state.forEach(saved => {", restore)
        self.assertIn("tab.view = saved.view;", restore)
        self.assertIn("tab.fontSize = saved.fontSize;", restore)
        self.assertIn("tab.preferredMode = saved.preferredMode;", restore)
        self.assertLess(
            restore.index("snapshot.explorer_tab_state.forEach"),
            restore.index("openExplorerFile(index, snapshot.explorer_preview_path"),
        )
        # The rebuilt session entries also receive the fresh disk-shape views.
        self.assertIn("entry.explorer_tab_views = snapshot.explorer_tab_views;", html)

    def test_terminals_page_exposes_session_mode_switch_controls(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-session-mode-toggle="${i}"', html)
        self.assertIn("async function switchSessionPaneMode(index)", html)
        self.assertIn("body.directory = getExplorerSelectedDirectory(index);", html)
        self.assertIn("body.refresh_cwd = true;", html)
        self.assertNotIn("body.directory = terminal._session.directory;", html)
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
        switch_end = html.index(
            "function captureSurvivingPaneClientState(closingSessionId)", switch_start
        )
        self.assertNotIn("teardownCurrentGrid();", html[switch_start:switch_end])

    def test_terminals_page_exposes_pane_shell_picker(self):
        """The header reset control doubles as the Local Repo shell picker."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # The reset button and its menu are rendered by terminal-shell.js and
        # only the local terminal panes get the dropdown behaviour.
        self.assertIn("${paneResetButtonHtml(i, session)}", html)
        self.assertIn("${paneShellMenuHtml(i)}", html)
        self.assertIn("handlePaneResetButton(i)", html)
        self.assertIn("function paneSupportsShellSwitch(session)", html)
        self.assertIn("&& session.mode === 'wsl'", html)
        self.assertIn("!isExplorerSession(session)", html)
        self.assertIn("!isBrowserSession(session)", html)
        self.assertIn("async function switchSessionShell(index, shellKind, distribution = '')", html)
        self.assertIn("`/api/sessions/${encodeURIComponent(sessionId)}/shell`", html)
        self.assertIn("body: JSON.stringify({ shell: shellKind, distribution })", html)
        self.assertIn("data-pane-shell-kind=\"wsl\" data-pane-shell-distro=\"\"", html)
        self.assertIn("fetch('/api/wsl-distros')", html)
        # Non-switchable panes keep the plain one-click reset.
        reset_start = html.index("function handlePaneResetButton(index)")
        reset_body = html[reset_start:html.index("function syncPaneShellControls(index, session)")]
        self.assertIn("togglePaneShellMenu(index);", reset_body)
        self.assertIn("refreshTerminalDisplay(index);", reset_body)
        # WebView2-safe dismissal + retry affordance for failed distro lookups.
        self.assertIn("closeAllPaneShellMenus();", html)
        self.assertIn("data-pane-shell-distro-retry=\"1\"", html)

    def test_terminals_page_exposes_browser_pane_rendering_hooks(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function isBrowserSession(session)", html)
        self.assertIn("function isBrowserPaneInstance(terminal)", html)
        self.assertIn("function getBrowserSessionUrl(session)", html)
        self.assertIn("function normalizeBrowserUrlInput(value)", html)
        self.assertIn("class=\"browser-surface\"", html)
        self.assertIn("class=\"browser-frame${isActive ? ' active' : ''}\"", html)
        self.assertIn("class=\"browser-url-input\"", html)
        self.assertIn("data-browser-open=\"${index}\"", html)
        self.assertIn("data-session-browser-toggle=\"${i}\"", html)
        self.assertIn("function reloadBrowserPane(index)", html)
        self.assertIn("function openBrowserPaneExternally(index)", html)
        self.assertIn("async function switchSessionBrowserMode(index)", html)
        self.assertIn("async function navigateBrowserPane(index, value)", html)
        self.assertIn(
            "'allow-downloads allow-forms allow-modals allow-popups '",
            html,
        )
        self.assertIn("'allow-popups-to-escape-sandbox allow-same-origin allow-scripts'", html)

    def test_terminals_page_exposes_browser_pane_tab_strip(self):
        """Browser panes are tabbed: one frame per tab, persisted as a strip."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const BROWSER_MAX_TABS = 8;", html)
        self.assertIn("class=\"browser-tabstrip\"", html)
        self.assertIn("data-browser-tab-new=\"${index}\"", html)
        self.assertIn("data-browser-tab-close=\"${index}\"", html)
        self.assertIn("function browserOpenTab(index, url", html)
        self.assertIn("function browserCloseTab(index, tabIndex)", html)
        self.assertIn("function browserSelectTab(index, tabIndex)", html)
        self.assertIn("function browserSerializeTabs(pane, session = null)", html)
        # Same-origin popups become pane tabs instead of escaping to the OS
        # browser; cross-origin access throws and is swallowed.
        self.assertIn("function browserHookFrameWindow(index, frame)", html)
        self.assertIn("win.__gridvibeBrowserPaneHooked", html)
        self.assertIn("a[target=\"_blank\"], a[target=\"_new\"]", html)
        # Save Workspace and the saved-session launch payload carry the strip.
        self.assertIn("browser_tabs: browserTabs.tabs", html)
        self.assertIn("browser_active_tab: browserTabs.active_tab", html)

    def test_compact_action_controls_use_shared_svg_icons(self):
        """F8 — action buttons use currentColor SVGs; search mnemonics stay text."""
        icons = self._static("js/terminal-icons.js")
        search = self._static("js/explorer-search.js")
        browser = self._static("js/browser-pane.js")

        for icon in (
            "UI_CHEVRON_RIGHT_ICON",
            "UI_CHEVRON_DOWN_ICON",
            "UI_PLUS_ICON",
            "UI_MINUS_ICON",
            "UI_CLOSE_ICON",
        ):
            marker = f"const {icon} = '"
            self.assertIn(marker, icons)
            svg = icons[icons.index(marker) + len(marker):]
            svg = svg[:svg.index("';")]
            self.assertIn('stroke="currentColor"', svg)
            self.assertNotIn('fill="#', svg)

        self.assertIn(
            "collapsed ? UI_CHEVRON_RIGHT_ICON : UI_CHEVRON_DOWN_ICON",
            search,
        )
        self.assertIn('aria-label="Expand all">${UI_PLUS_ICON}</button>', search)
        self.assertIn('aria-label="Collapse all">${UI_MINUS_ICON}</button>', search)
        self.assertNotIn("▸", search)
        self.assertNotIn("▾", search)
        self.assertNotIn('aria-label="Expand all">+</button>', search)
        self.assertNotIn('aria-label="Collapse all">−</button>', search)

        self.assertIn(">${UI_CLOSE_ICON}</button>", browser)
        self.assertIn(">${UI_PLUS_ICON}</button>", browser)
        self.assertNotIn(">×</button>", browser)
        self.assertNotIn(">+</button>", browser)

        # These are search-language labels, not stand-ins for graphical actions.
        self.assertIn('title="Match case">Aa</button>', search)
        self.assertIn('title="Match whole word">ab</button>', search)
        self.assertIn('title="Use regular expression">.*</button>', search)

    def test_browser_tab_persist_is_cancelled_and_revalidated_before_post(self):
        """F1 — a stale debounce must not switch a terminal back to browser mode."""
        browser_response = self.client.get("/static/js/browser-pane.js")
        terminals_response = self.client.get("/static/js/terminals.js")
        browser_js = browser_response.get_data(as_text=True)
        terminals_js = terminals_response.get_data(as_text=True)
        browser_response.close()
        terminals_response.close()

        self.assertIn("function browserCancelPendingPersist(sessionId)", browser_js)
        persist_start = browser_js.index("const push = async () => {")
        persist_end = browser_js.index("const snapshot = browserSerializeTabs(pane);", persist_start)
        persist_guard = browser_js[persist_start:persist_end]
        self.assertIn("const currentIndex = sessionIds.indexOf(sessionId);", persist_guard)
        self.assertIn("terminals[currentIndex] !== pane", persist_guard)
        self.assertIn("!isBrowserSession(terminals[currentIndex]?._session)", persist_guard)
        self.assertIn("isSessionModeSwitchPending(sessionId)", persist_guard)

        switch_start = terminals_js.index("async function switchSessionBrowserMode(index)")
        switch_end = terminals_js.index("function captureSurvivingPaneClientState", switch_start)
        switch_body = terminals_js[switch_start:switch_end]
        self.assertLess(
            switch_body.index("browserCancelPendingPersist(sessionId);"),
            switch_body.index("pendingModeSwitchSessionIds.add(sessionId);"),
        )
        close_start = terminals_js.index("async function closeTerminalPane(index)")
        close_end = terminals_js.index("async function splitTerminalPane(index", close_start)
        self.assertIn(
            "browserCancelPendingPersist(plan.sessionId);",
            terminals_js[close_start:close_end],
        )
        group_start = terminals_js.index("async function closeSessionGroup(")
        group_end = terminals_js.index("async function _closeWindowAfterLastSession(", group_start)
        self.assertIn(
            "closingSessionIds.forEach(browserCancelPendingPersist);",
            terminals_js[group_start:group_end],
        )

    def test_browser_pane_new_tab_never_duplicates_the_active_tab(self):
        """A '+' that copied the active URL made a pane showing GridVibe
        re-embed its own workspace, one level deeper on every render."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        new_tab_start = html.index('[data-browser-tab-new="${index}"]`)')
        new_tab_end = html.index("const tab = event.target.closest", new_tab_start)
        handler = html[new_tab_start:new_tab_end]
        self.assertIn("browserOpenTab(index, BROWSER_DEFAULT_URL)", handler)
        # The old duplicating call must be gone from the handler entirely.
        self.assertNotIn("current.url", handler)
        self.assertNotIn("browserActiveTab(pane)", handler)

    def test_browser_pane_blocks_nested_self_embedding(self):
        """A GridVibe page inside a browser pane renders no browser frames."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function browserIsNestedPreview()", html)
        self.assertIn("window.frameElement?.classList.contains('browser-frame')", html)
        self.assertIn("if (browserIsNestedPreview()) {", html)
        self.assertIn("browser-frame-blocked", html)
        # The stand-in must not carry the class the guard looks for, or a page
        # one level deeper would not detect that it is nested.
        self.assertNotIn('class="browser-frame${isActive', html.split("browser-frame-blocked")[0][-400:])

    def test_browser_pane_tabs_drag_reorder(self):
        """Tabs drag-reorder like the session and explorer strips."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('draggable="true"', html)
        self.assertIn("function browserReorderTab(index, draggedId, targetId, before)", html)
        self.assertIn("function browserClearTabDragMarkers(index)", html)
        # Drag state is held as a tab id, not an index, so a re-render mid-drag
        # cannot retarget the move.
        self.assertIn("pane._browserDraggedTabId = tab.dataset.browserTabId", html)
        self.assertIn("data-browser-tab-id=", html)
        # The shown page must survive a reorder: the active tab is re-resolved
        # by identity, never left pointing at whatever slid into its old index.
        self.assertIn("pane._browserActiveTab = Math.max(0, tabs.indexOf(activeTab))", html)
        self.assertIn(".browser-tab.drag-before", html)
        self.assertIn(".browser-tab.drag-after", html)

    def test_browser_pane_reuses_named_window_targets(self):
        """window.open(url, 'name') reuses that tab instead of stacking tabs."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("tab.windowName === windowName", html)
        # _self/_top/_parent navigate an existing window, so they stay native.
        self.assertIn("['_self', '_top', '_parent'].includes(name)", html)
        self.assertIn("['_blank', '_new', ''].includes(name) ? '' : name", html)

    def test_terminals_page_explorer_refresh_requires_initial_navigation_or_force(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
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
        html = self._page_html(response)
        explorer_refresh_start = html.index("async function refreshExplorerPane(index)")
        explorer_refresh_end = html.index("async function loadExplorerPane(index, path = null", explorer_refresh_start)
        explorer_refresh_html = html[explorer_refresh_start:explorer_refresh_end]
        refresh_start = html.index("async function refreshTerminalDisplay(index)")
        refresh_end = html.index("logSessionWindowAction('Refreshing terminal display'", refresh_start)
        refresh_html = html[refresh_start:refresh_end]

        self.assertIn(
            "refreshed = await openExplorerFile(index, pane._explorerFilePath, {\n"
            "                showLoading: false,\n"
            "                preserveScroll: true,\n"
            "                tab: pane._explorerActiveTabId\n"
            "            });",
            explorer_refresh_html,
        )
        self.assertIn("refreshed = await loadExplorerPane(index, null, { force: true });", explorer_refresh_html)
        self.assertIn("const refreshGitSidebar = Boolean(pane?._explorerGitSidebarOpen);", explorer_refresh_html)
        self.assertIn("invalidateExplorerGitRepo(index);", explorer_refresh_html)
        self.assertIn("await loadExplorerGitRepo(index);", explorer_refresh_html)
        self.assertIn("return refreshed;", explorer_refresh_html)
        self.assertIn("await refreshExplorerPane(index);", refresh_html)
        self.assertIn("function captureExplorerFileScroll(index)", html)
        self.assertIn("function restoreExplorerFileScroll(index, state)", html)
        self.assertIn("function updateExplorerFileInPlace(index, data, scrollState = null)", html)
        self.assertIn("updateExplorerFileInPlace(index, data, scrollState)", html)
        self.assertIn(".explorer-list.file-view", html)
        self.assertIn("list.classList.add('file-view');", html)
        self.assertIn("listScrollTop: list.scrollTop", html)
        self.assertIn("list.scrollTop = state.listScrollTop || 0;", html)
        self.assertIn("wasAtBottom: maxScrollTop > 0 && scrollEl.scrollTop >= maxScrollTop - 2", html)
        self.assertIn("scrollEl.scrollTop = panelState.wasAtBottom", html)
        self.assertIn("window.setTimeout(applyScroll, 80);", html)
        self.assertIn("async function syncExplorerPane(index)", html)
        self.assertIn("if (pane?._explorerMode === 'file' && pane._explorerFilePath) {\n            return true;\n        }", html)
        self.assertIn("syncExplorerPane(i);", html)
        self.assertIn("syncExplorerPane(index);", html)

    def test_terminals_page_explorer_theme_defaults_to_dark(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function hasExplorerThemeOverride(key = '')", html)
        self.assertIn('[data-theme="dark"]', html)
        self.assertIn("--explorer-bg: #0f141b;", html)
        self.assertIn("function resolveInitialExplorerTheme(session, key)", html)
        self.assertIn("return { theme: 'dark', source: 'default' };", html)
        self.assertIn("card.dataset.explorerThemeSource = resolvedTheme.source;", html)
        # The theme is applied explicitly so a pane never inherits the global
        # app theme's --explorer-* tokens.
        self.assertIn("card.dataset.explorerTheme = resolvedTheme.theme;", html)
        self.assertIn("updateExplorerThemeButton(explorerThemeButton, card.dataset.explorerTheme || 'dark');", html)
        self.assertIn("function syncDefaultExplorerThemes()", html)
        self.assertIn("syncDefaultExplorerThemes();", html)
        self.assertNotIn("store.default || currentResolvedTheme()", html)

    def test_terminals_page_explorer_source_view_wraps_and_highlights_code(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("white-space: pre-wrap;", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn(".explorer-source-line-number", html)
        self.assertIn("function renderExplorerSourceLines(content, language, searchRanges = [], collapsedLines = new Set())", html)
        self.assertIn("function highlightExplorerCode(content, language, searchRanges = [])", html)
        self.assertIn("code.innerHTML = renderExplorerSourceLines(", html)
        self.assertIn("const EXPLORER_LANGUAGE_BY_EXTENSION = Object.freeze({", html)
        self.assertIn("'.py': 'python'", html)
        self.assertIn("'.go': 'go'", html)
        self.assertIn("'.c': 'c'", html)
        self.assertIn("'.jsonl': 'jsonl'", html)
        self.assertIn("'.log': 'log'", html)
        self.assertIn("'.txt': 'text'", html)
        self.assertIn("'.bat': 'batch'", html)
        self.assertIn("const EXPLORER_LANGUAGE_BY_FILENAME = Object.freeze({", html)
        self.assertIn("'.gitignore': 'gitignore'", html)
        self.assertIn("'dockerfile': 'dockerfile'", html)

    def test_terminals_page_explorer_formats_common_operational_text_files(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("jsonl: 'JSON Lines source'", html)
        self.assertIn("log: 'Log file'", html)
        self.assertIn("dotenv: 'Environment file'", html)
        self.assertIn("batch: 'Batch source'", html)
        self.assertIn("function highlightExplorerLog(content, searchRanges = [])", html)
        self.assertIn("function highlightExplorerLogLine(line, absoluteStart, searchRanges = [])", html)
        self.assertIn("const EXPLORER_LOG_LEVELS = new Set", html)
        self.assertIn("explorer-log-timestamp", html)
        self.assertIn("explorer-log-level", html)

    def test_terminals_page_explorer_editor_has_font_zoom_controls(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("--explorer-editor-font-size", html)
        self.assertIn("const EXPLORER_EDITOR_FONT_MIN = 10;", html)
        self.assertIn("const EXPLORER_EDITOR_FONT_MAX = 24;", html)
        self.assertIn("function applyExplorerEditorFontSize(index)", html)
        self.assertIn("function stepExplorerEditorFontSize(index, delta)", html)
        self.assertIn("function wireExplorerEditorZoomControls(index)", html)
        self.assertIn('data-explorer-zoom-decrease="${index}"', html)
        self.assertIn('data-explorer-zoom-increase="${index}"', html)
        self.assertIn('data-explorer-zoom-value="${index}"', html)
        self.assertIn("wireExplorerEditorZoomControls(index);", html)

    def test_terminals_page_explorer_file_search_is_client_side_and_safe(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-explorer-search-input="${index}"', html)
        self.assertIn("function explorerMarkedEscHtml(text, absoluteStart = 0, searchRanges = [])", html)
        self.assertIn("function markExplorerSearchInElement(root, query, activeIndex = 0, maxMatches = EXPLORER_SEARCH_MAX_MATCHES)", html)
        self.assertIn("document.createTreeWalker(", html)
        self.assertIn("node.replaceWith(fragment);", html)
        self.assertIn("code.innerHTML = renderExplorerSourceLines(", html)
        self.assertIn("function findExplorerSearchTargetIndex()", html)
        self.assertIn("event.code !== 'KeyF'", html)
        self.assertNotIn("/api/explorer-search", html)

    def test_terminals_page_explorer_file_search_is_bounded_and_debounced(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const EXPLORER_SEARCH_DEBOUNCE_MS = 160;", html)
        self.assertIn("const EXPLORER_SEARCH_MAX_MATCHES = 1000;", html)
        self.assertIn("const EXPLORER_SEARCH_CHUNK_SIZE = 65536;", html)
        self.assertIn("async function explorerFindRangesAsync(content, query, token, maxMatches = EXPLORER_SEARCH_MAX_MATCHES)", html)
        self.assertIn("await new Promise(resolve => window.setTimeout(resolve, 0));", html)
        self.assertIn("function cancelExplorerSearch(index)", html)
        self.assertIn("window.clearTimeout(pane._explorerSearchTimer);", html)
        self.assertIn("pane._explorerSearchToken.cancelled = true;", html)
        self.assertIn("function scheduleExplorerSearch(index, { resetActive = false, delay = EXPLORER_SEARCH_DEBOUNCE_MS } = {})", html)
        self.assertIn("scheduleExplorerSearch(index, { resetActive: true });", html)
        self.assertIn("capped: ranges.length >= maxMatches,", html)
        self.assertIn("count.title = capped ? `Showing first ${matchCount} matches` : '';", html)
        self.assertIn("state.resultQuery === query && Array.isArray(state.ranges)", html)
        self.assertIn("state.matchCapped = capped;", html)

    def test_terminals_page_explorer_directory_search_filters_current_entries(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("explorer-directory-search", html)
        self.assertIn('id="explorer-directory-search-${i}"', html)
        self.assertIn('id="explorer-directory-search-${index}"', html)
        self.assertIn("function renderExplorerDirectorySearchControls(index)", html)
        self.assertIn('aria-label="Find files and folders"', html)
        self.assertIn("pane._explorerEntries = Array.isArray(data.entries) ? data.entries : [];", html)
        self.assertIn("function renderExplorerDirectoryRows(index)", html)
        self.assertIn("visibleEntries = entries.filter(entry => String(entry.name || '').toLowerCase().includes(normalizedQuery));", html)
        self.assertIn("No files or folders match", html)
        self.assertIn("explorerMarkedEscHtml(name, 0, nameRanges)", html)
        self.assertIn("wireExplorerDirectoryRows(index);", html)
        self.assertIn("wireExplorerSearchControls(index);", html)
        self.assertIn("applyExplorerSearch(index, { resetActive: true });", html)

    def test_terminals_page_explorer_directory_search_is_keyboard_target(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function isExplorerSearchablePane(pane)", html)
        self.assertIn("pane?._explorerMode === 'file' || pane?._explorerMode === 'directory'", html)
        self.assertIn("isExplorerSearchablePane(terminals[activeSlot])", html)
        self.assertIn("isExplorerSearchablePane(terminals[_focusedTerminalIndex])", html)
        self.assertIn("!pane || !isExplorerSearchablePane(pane)", html)
        self.assertNotIn("!pane || pane._explorerMode !== 'file'", html)

    def test_terminals_page_explorer_directory_search_preserves_file_search_state(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const key = mode === 'directory' ? '_explorerDirectorySearch' : '_explorerSearch';", html)
        self.assertIn("const searchState = ensureExplorerSearchState(pane, 'file');", html)
        self.assertIn("const state = ensureExplorerSearchState(pane, 'directory');", html)
        self.assertIn("clearExplorerDirectorySearchControls(index);", html)

    def test_terminals_page_explorer_file_open_failures_keep_directory_usable(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function renderExplorerDirectoryOpenError(index, message)", html)
        self.assertIn("renderExplorerDirectoryRows(index);", html)
        self.assertIn("viewer.prepend(notice);", html)
        self.assertIn("const wasDirectoryOpen = pane._explorerMode === 'directory';", html)
        self.assertIn("if (showLoading && !wasDirectoryOpen)", html)
        self.assertIn("renderExplorerDirectoryOpenError(index, error.message || 'Failed to open file.');", html)

    def test_terminals_page_explorer_directory_search_clears_on_navigation(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function resetExplorerDirectorySearch(pane)", html)
        self.assertIn("if (isNavigation) {\n                resetExplorerDirectorySearch(pane);\n            }", html)
        self.assertIn("state.query = '';", html)
        self.assertIn("state.matchCount = 0;", html)

    def test_terminals_page_explorer_git_hooks_are_present(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("explorer-git-summary", html)
        self.assertIn("data-explorer-git-toggle", html)
        self.assertIn("explorer-git-panel", html)
        self.assertIn("data-explorer-sidebar-resizer", html)
        self.assertIn("function toggleExplorerGitSidebar(index)", html)
        self.assertIn("function wireExplorerSidebarResize(index)", html)
        self.assertIn("function applyExplorerSidebarWidth(index)", html)
        self.assertIn(
            'wireCardButton(card, `[data-explorer-git-toggle="${i}"]`, () => toggleExplorerGitSidebar(i));',
            html,
        )
        self.assertIn("data-explorer-git-open-folder", html)
        self.assertIn("data-explorer-git-open-commit-diff", html)
        self.assertIn("function explorerGitOpenCommitDiff(index, path, commit)", html)
        self.assertIn("function renderExplorerCommitDiffFile(index, path, commit)", html)
        self.assertIn("function explorerGitBadgeHtml(git)", html)
        self.assertIn("function explorerGitSummaryText(git)", html)
        self.assertIn("function loadExplorerDiff(index)", html)
        self.assertIn("data-explorer-diff-toggle=\"${index}\"", html)
        self.assertIn('data-explorer-file-view="diff"', html)
        self.assertIn(".explorer-editor-tab[aria-pressed=\"true\"]", html)
        self.assertIn("button.dataset.explorerFileView === 'diff'", html)
        self.assertIn("setExplorerFileView(index, open ? 'diff' : (pane._explorerLastFileView || 'source'));", html)
        self.assertIn("function toggleExplorerDiffSplit(index)", html)
        self.assertIn("function renderExplorerSideBySideDiff(index, diff)", html)
        self.assertIn("function loadExplorerGitRepo(index)", html)
        self.assertIn("setExplorerFileView(index, button.dataset.explorerFileView || 'source');", html)
        self.assertNotIn("function loadExplorerDiffSelectedFileForView(index, targetView)", html)
        self.assertNotIn("await loadExplorerDiffSelectedFileForView(", html)
        self.assertNotIn('id="explorer-diff-sidebar-${index}"', html)
        self.assertNotIn('id="explorer-diff-resizer-${index}"', html)
        self.assertNotIn("data-explorer-diff-file", html)
        self.assertNotIn("data-explorer-diff-commit-toggle", html)
        self.assertNotIn("data-explorer-diff-commit-file", html)
        self.assertNotIn("data-explorer-diff-commit", html)
        self.assertIn("/git/repo", html)
        self.assertIn(".explorer-diff-cell.add", html)
        self.assertIn(".explorer-diff-cell.delete", html)
        self.assertIn(".explorer-diff-line-code", html)
        self.assertIn(".explorer-diff-content", html)
        self.assertIn("overflow-x: hidden;", html)
        self.assertIn("overflow-y: auto;", html)
        self.assertIn("white-space: pre-wrap;", html)
        self.assertIn("overflow-wrap: anywhere;", html)
        self.assertIn("tab-size: 4;", html)
        self.assertIn("explorer-diff-split", html)
        self.assertIn("split-diff", html)
        self.assertIn("new URLSearchParams({", html)
        self.assertIn("const diffMode = commit ? 'commit' : (pane?._explorerDiffMode || 'head');", html)
        self.assertIn("mode: diffMode", html)
        self.assertIn("params.set('commit', commit);", html)
        self.assertIn("${explorerGitBadgeHtml(entry.git)}", html)
        self.assertIn("data-explorer-git-stage", html)
        self.assertIn("data-explorer-git-unstage", html)
        self.assertIn("data-explorer-git-commit", html)
        self.assertIn("data-explorer-git-publish", html)
        self.assertIn("function splitExplorerGitChanges(changes)", html)
        self.assertIn("function explorerGitStageFile(index, path)", html)
        self.assertIn("function explorerGitUnstageFile(index, path)", html)
        self.assertIn("async function explorerGitCommit(index)", html)
        self.assertIn("function explorerGitPublish(index)", html)
        self.assertIn("Staged Changes", html)

    def test_terminals_page_vendors_highlightjs_source_highlighting(self):
        """Phase 1 (docs/source_diff_analysis.md): the Source viewer highlights
        the whole document once with the pinned Highlight.js build and keeps the
        handwritten lexer as a fallback."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Assets are vendored and loaded locally, never from a CDN.
        self.assertIn("/static/vendor/highlight.min.js", html)
        self.assertNotIn("cdnjs.cloudflare.com", html)
        # Explicit grammar map (no auto-detection) and whole-document tokenizer.
        self.assertIn("const EXPLORER_HLJS_LANGUAGE = Object.freeze({", html)
        self.assertIn("function explorerHighlightDocumentLines(content, normalizedLanguage)", html)
        self.assertIn("function explorerRenderHighlightedRuns(runs, searchRanges = [])", html)
        self.assertIn("engine.highlight(source, { language: grammar, ignoreIllegal: true })", html)
        # Source rendering prefers the whole-document pass, falling back per line.
        self.assertIn("const highlightedLines = explorerHighlightDocumentLines(content, normalizedLanguage);", html)
        self.assertIn("? explorerRenderHighlightedRuns(highlightedLines.get(record.number), searchRanges)", html)
        self.assertIn(": highlightExplorerCode(record.text, language, searchRanges, record.start);", html)
        # The oversized-file guard is preserved for the highlighter.
        self.assertIn("if (source.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD) {", html)
        # Explorer-scoped token palette for both themes, shared by the Source
        # view and the Diff2Html host so diff code keeps its syntax colours.
        self.assertIn(
            ":is(.explorer-source-line-code, .explorer-diff2html) .hljs-keyword,",
            html,
        )
        self.assertIn(
            '.explorer-pane[data-explorer-theme="dark"] '
            ":is(.explorer-source-line-code, .explorer-diff2html) .hljs-string,",
            html,
        )

    def test_terminals_page_vendors_diff2html_precise_diffs(self):
        """Phase 2 (docs/source_diff_analysis.md): diffs render through the
        pinned Diff2Html build with intraline emphasis, surface truncation, and
        keep the tolerant side-by-side renderer as a fallback."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("/static/vendor/diff2html-ui-base.min.js", html)
        self.assertIn("/static/vendor/diff2html.min.css", html)
        # Diff2Html configuration: char-level intraline + explicit limits.
        self.assertIn("function explorerDiff2HtmlConfig()", html)
        self.assertIn("matching: 'words',", html)
        self.assertIn("diffStyle: 'char',", html)
        self.assertIn("synchronisedScroll: false,", html)
        self.assertIn("matchingMaxComparisons: 1500,", html)
        self.assertIn("maxLineLengthHighlight: 2000", html)
        self.assertIn(
            "new window.Diff2HtmlUI(host, diff, explorerDiff2HtmlConfig(), window.hljs)",
            html,
        )
        # Both wrapped and unwrapped paths prefer Diff2Html; only unavailable
        # vendor assets use the handwritten renderer.
        self.assertIn("function renderExplorerDiffWithDiff2Html(index, code, diff, banner)", html)
        self.assertIn(
            "if (!renderExplorerDiffWithDiff2Html(index, code, diff, banner)) {",
            html,
        )
        self.assertIn("code.innerHTML = banner + renderExplorerSideBySideDiff(index, diff);", html)
        self.assertIn("function renderExplorerSideBySideDiff(index, diff)", html)
        # Truncation is captured from the API and surfaced without blocking.
        self.assertIn("pane._explorerDiffTruncated = Boolean(data.truncated);", html)
        self.assertIn("function explorerDiffTruncationBannerHtml(pane)", html)
        self.assertIn("Diff truncated to 256 KiB / 4,000 lines", html)
        self.assertIn(".explorer-diff-truncated {", html)
        # The diff host inherits the per-tab editor zoom.
        self.assertIn(".explorer-diff2html {", html)
        self.assertIn("font-size: var(--explorer-editor-font-size, .78rem);", html)
        self.assertIn(
            ".explorer-diff2html .d2h-diff-table {\n"
            "            font-size: inherit;",
            html,
        )
        # Original source lines remain unwrapped in two fixed, equal-width
        # panes. A sticky scrollbar controls each side independently.
        self.assertIn(
            ".explorer-diff2html .d2h-code-side-line {\n"
            "            box-sizing: border-box;\n"
            "            width: 100%;\n"
            "            padding: 0 .5em;\n"
            "            white-space: nowrap;",
            html,
        )
        self.assertIn(
            ".explorer-diff2html .d2h-code-line-ctn {\n"
            "            white-space: pre;",
            html,
        )
        self.assertIn("width: 50%;", html)
        self.assertIn("flex: 1 1 50%;", html)
        self.assertIn(
            ".explorer-diff2html .d2h-code-side-linenumber {\n"
            "            position: sticky;\n"
            "            z-index: 2;\n"
            "            left: 0;",
            html,
        )
        self.assertIn("padding: 0 .5em;", html)
        self.assertIn(".explorer-diff-horizontal-scrollbars {", html)
        self.assertIn("position: sticky;", html)
        self.assertIn("function synchroniseExplorerDiffScrollbars(host)", html)
        self.assertIn("function observeExplorerDiffLayout(host)", html)
        self.assertIn("track.dataset.explorerDiffSide = sideIndex === 0 ? 'left' : 'right';", html)
        self.assertIn("side.scrollLeft = track.scrollLeft;", html)
        self.assertIn("observeExplorerDiffLayout(host);", html)

    def test_richer_source_and_diff_vendored_assets_are_served(self):
        """Phase 1/2 assets are served locally and pinned (highlight.js custom
        build includes the extra grammars beyond the common bundle)."""
        for filename in (
            "vendor/highlight.min.js",
            "vendor/diff2html-ui-base.min.js",
            "vendor/diff2html.min.css",
        ):
            with self.subTest(filename=filename):
                response = self.client.get(f"/static/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.get_data()), 1000)
                response.close()
        highlight = self.client.get("/static/vendor/highlight.min.js")
        highlight_body = highlight.get_data(as_text=True)
        highlight.close()
        for grammar in ("powershell", "dockerfile"):
            with self.subTest(grammar=grammar):
                self.assertIn(grammar, highlight_body)

    def test_terminals_page_explorer_file_tree_hooks_are_present(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("data-explorer-tree-toggle", html)
        self.assertIn("explorer-tree-panel", html)
        self.assertIn("data-explorer-tree-dir", html)
        self.assertIn("data-explorer-tree-file", html)
        self.assertIn("data-explorer-tree-open-folder", html)
        self.assertIn("function toggleExplorerTreeSidebar(index)", html)
        self.assertIn("function toggleExplorerTreeDirectory(index, path)", html)
        self.assertIn("function renderExplorerTreePanel(index)", html)
        self.assertIn("function loadExplorerTreeChildren(index, path)", html)
        self.assertIn("function revealExplorerTreePath(index, targetPath = '')", html)
        self.assertIn("function reloadExplorerTree(index)", html)
        self.assertIn(
            'wireCardButton(card, `[data-explorer-tree-toggle="${i}"]`, () => toggleExplorerTreeSidebar(i));',
            html,
        )
        self.assertIn(".filter(entry => !entry.deleted)", html)

    def test_terminals_page_explorer_repo_search_hooks_are_present(self):
        """Repository-wide search: third sidebar panel, magnifier toggle,
        shared Ctrl+Shift+F dispatch (explorer before terminal overlay)."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("data-explorer-search-toggle", html)
        self.assertIn("explorer-search-panel", html)
        self.assertIn("const EXPLORER_SEARCH_TOGGLE_ICON = ", html)
        self.assertIn("function toggleExplorerSearchSidebar(index)", html)
        self.assertIn("function setExplorerSearchSidebarOpen(index, open)", html)
        self.assertIn("function renderExplorerSearchPanel(index)", html)
        self.assertIn("function runExplorerRepoSearch(index)", html)
        self.assertIn("const EXPLORER_REPO_SEARCH_DEBOUNCE_MS = 350;", html)
        self.assertIn(
            "function scheduleExplorerRepoSearch(index, { delay = EXPLORER_REPO_SEARCH_DEBOUNCE_MS } = {})",
            html,
        )
        self.assertIn("function activateExplorerSearchHit(index, path, line,", html)
        self.assertIn("function focusExplorerRepoSearch(index, seedQuery = '')", html)
        self.assertIn("function findExplorerRepoSearchTargetIndex()", html)
        self.assertIn("data-explorer-repo-search-input", html)
        self.assertIn("/api/explorer/${encodeURIComponent(sessionId)}/search?", html)
        self.assertIn(
            'wireCardButton(card, `[data-explorer-search-toggle="${i}"]`, () => toggleExplorerSearchSidebar(i));',
            html,
        )
        # Result activation: source rows carry line identity for scroll+flash.
        self.assertIn('data-explorer-line="${record.number}"', html)
        # Ctrl+Shift+F dispatch tries the explorer target before the terminal
        # scrollback overlay, so a focused explorer pane wins the shared
        # shortcut deterministically.
        dispatch_start = html.index("const explorerIndex = findExplorerRepoSearchTargetIndex();")
        dispatch_end = html.index("const index = findTerminalSearchTargetIndex();", dispatch_start)
        dispatch_html = html[dispatch_start:dispatch_end]
        self.assertIn(
            "toggleExplorerRepoSearchShortcut(explorerIndex, explorerSelectionQuery(explorerIndex))",
            dispatch_html,
        )
        # The shortcut is a toggle: an already-open panel closes from anywhere
        # in the pane, not only while the panel input holds focus.
        self.assertIn(
            "function toggleExplorerRepoSearchShortcut(index, seedQuery = '')",
            html,
        )
        toggle_start = html.index("function toggleExplorerRepoSearchShortcut(index, seedQuery = '')")
        toggle_end = html.index("\n    }", toggle_start)
        self.assertIn(
            "setExplorerSearchSidebarOpen(index, false);",
            html[toggle_start:toggle_end],
        )
        # The .gitignore filter is reachable from the panel, so the backend's
        # `ignored` flag is wired end-to-end rather than dead (guardrail 5).
        self.assertIn("data-explorer-repo-search-ignored", html)
        self.assertIn("if (state.ignored) params.set('ignored', '1');", html)
        self.assertIn("if (truncated.output)", html)
        self.assertIn("stopped at the output limit", html)

    def test_terminals_page_explorer_uses_tabbed_file_viewer(self):
        """ISSUE-2026-014: main pane is a persistent tabbed read-only viewer."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Per-pane tab model: one permanent Preview tab plus pinned tabs.
        self.assertIn("const EXPLORER_PREVIEW_TAB_ID = '__preview__';", html)
        self.assertIn("function ensureExplorerTabState(pane)", html)
        self.assertIn("function renderExplorerTabStrip(index)", html)
        self.assertIn("function activateExplorerTab(index, id)", html)
        self.assertIn("function closeExplorerTab(index, id)", html)
        self.assertIn("function openExplorerViewer(index)", html)
        self.assertIn(
            "function explorerAssignOpenTab(pane, path, { pinned = false, tab = '' } = {})",
            html,
        )
        # The permanent Preview tab cannot be closed.
        self.assertIn("if (!pane || id === EXPLORER_PREVIEW_TAB_ID) {", html)
        # Empty state before any file is selected.
        self.assertIn("Select a file to view", html)
        # Stable shell: tab strip above the file header/viewer body.
        self.assertIn('class="explorer-tab-strip"', html)
        self.assertIn('id="explorer-viewer-${index}"', html)
        self.assertIn("data-explorer-tab-open", html)
        self.assertIn("data-explorer-tab-close", html)
        # A `+` control on each tree file row opens a pinned tab (event-isolated).
        self.assertIn("data-explorer-tree-open-tab", html)
        self.assertIn(
            "openExplorerFile(index, button.dataset.explorerTreeOpenTab || '', { pinned: true });",
            html,
        )
        # First show routes through the viewer, not a directory listing.
        self.assertIn("return openExplorerViewer(index);", html)
        # Styling hooks (token-driven, no palette literals).
        self.assertIn(".explorer-tab-strip {", html)
        self.assertIn(".explorer-empty-viewer {", html)

    def test_terminals_page_explorer_editor_controls_and_wiring(self):
        """docs/text_editor_2026-07-20.md §5: Edit/Save/Cancel wiring + textarea."""
        editor = self._static("js/explorer-editor.js")
        viewer = self._static("js/explorer-viewer.js")

        # renderExplorerFile hosts the editor action group before Download.
        render = viewer[
            viewer.index("function renderExplorerFile(index, data,"):
            viewer.index("function updateExplorerFileInPlace(index, data")
        ]
        self.assertIn("${explorerEditorControlsHtml(index)}", render)
        self.assertLess(
            render.index("${explorerEditorControlsHtml(index)}"),
            render.index('data-explorer-download="${index}"'),
        )
        # Edit / Save / Cancel controls and their handlers exist.
        for hook in (
            "data-explorer-edit=",
            "data-explorer-edit-save=",
            "data-explorer-edit-cancel=",
            "function enterExplorerEditMode(index)",
            "function saveExplorerEdit(index)",
            "function cancelExplorerEdit(index)",
        ):
            self.assertIn(hook, editor)
        # Textarea attributes: spellcheck off, the tab's own Source wrap flag
        # (never `hard`, which would rewrite the saved value), accessible label.
        self.assertIn('class="explorer-source-editor" spellcheck="false" wrap="${wrap}"', editor)
        self.assertIn(
            "const wrap = explorerLineWrapPreference(index, 'source') ? 'soft' : 'off';",
            editor,
        )
        self.assertIn('aria-label="Edit ', editor)
        # Tab inserts a literal tab; Ctrl/Cmd+S saves; Escape cancels.
        self.assertIn("textarea.setRangeText('\\t', start, end, 'end');", editor)
        self.assertIn("(event.ctrlKey || event.metaKey) && (event.key === 's' || event.key === 'S')", editor)
        self.assertIn("if (event.key === 'Escape') {", editor)
        # Save sends the full-file contract.
        self.assertIn("path: state.path", editor)
        self.assertIn("content: state.draft", editor)
        self.assertIn("base_revision: baseRevision", editor)
        # Disabled Edit explains why via specific tooltips.
        self.assertIn("File exceeds the 10 MiB in-place edit limit", editor)
        self.assertIn("Mixed line endings are view-only in this version", editor)
        # Entering edit mode transfers the Source viewport and focuses without
        # letting Chromium scroll the default end-of-file caret into view.
        enter = editor[
            editor.index("function enterExplorerEditMode(index)"):
            editor.index("function renderExplorerEditTextarea(index)")
        ]
        self.assertIn("const sourceViewport = captureScrollMetrics(sourcePanel);", enter)
        self.assertIn("textarea.focus({ preventScroll: true });", enter)
        self.assertIn("restoreExplorerEditViewport(textarea, sourceViewport);", enter)
        self.assertLess(
            enter.index("textarea.setSelectionRange(0, 0);"),
            enter.index("textarea.focus({ preventScroll: true });"),
        )
        # Save captures the textarea (the real edit-mode scroller), allowing
        # the highlighted Source view to return to the same location.
        self.assertIn("panel.querySelector('.explorer-source-editor')", viewer)
        exit_mode = editor[
            editor.index("function exitExplorerEditMode(index)"):
            editor.index("async function cancelExplorerEdit(index)")
        ]
        self.assertIn("const editViewport = captureScrollMetrics(textarea);", exit_mode)
        self.assertIn("restoreExplorerEditViewport(", exit_mode)

    def test_terminals_page_explorer_editor_conflict_branches_on_code(self):
        """§5.5: conflict flow branches on data.code and retries with the revision."""
        editor = self._static("js/explorer-editor.js")
        self.assertIn("const code = data && data.code;", editor)
        self.assertIn("if (code === 'file_conflict') {", editor)
        self.assertIn("state.conflictRevision = (data && data.current_revision) || '';", editor)
        # Retry sends the conflict's current_revision as the new base.
        self.assertIn("const baseRevision = state.conflictRevision || state.baseRevision;", editor)
        # Reload / Overwrite both confirm through the in-page modal first.
        self.assertIn("data-explorer-edit-reload=", editor)
        self.assertIn("data-explorer-edit-overwrite=", editor)
        self.assertIn("save_in_progress", editor)
        self.assertIn("file_too_large", editor)

    def test_terminals_page_explorer_editor_clears_dirty_tab_marker_immediately(self):
        """Save/cancel/discard exits synchronise the transient tab dirty class."""
        editor = self._static("js/explorer-editor.js")
        clear_state = editor[
            editor.index("function clearExplorerEditState(index)"):
            editor.index("async function confirmDiscardExplorerEdit", editor.index("function clearExplorerEditState(index)"))
        ]
        self.assertIn("pane._explorerEdit = null;", clear_state)
        self.assertIn("updateExplorerEditTabDirty(index);", clear_state)
        # Confirmed discard, Cancel, Save success, and conflict reload all share
        # the same immediate state-drop path.
        self.assertGreaterEqual(editor.count("clearExplorerEditState(index);"), 4)

    def test_terminals_page_explorer_editor_guards_dirty_teardown(self):
        """§5.6: every deliberate teardown consults the discard guard."""
        editor = self._static("js/explorer-editor.js")
        viewer = self._static("js/explorer-viewer.js")
        terminals_js = self._static("js/terminals.js")

        # Guard + group guard exist and use the in-page confirm shell only.
        self.assertIn("async function confirmDiscardExplorerEdit(index, actionLabel", editor)
        self.assertIn("async function confirmDiscardAllExplorerEdits(actionLabel", editor)
        self.assertIn("function hasDirtyExplorerEdit(index)", editor)
        self.assertIn("function hasAnyDirtyExplorerEdit()", editor)
        self.assertIn("await openGenericConfirmModal(", editor)

        # Tab / path / directory / refresh teardown in the viewer awaits it.
        self.assertIn("confirmDiscardExplorerEdit(index, 'Switching tabs')", viewer)
        self.assertIn("confirmDiscardExplorerEdit(index, 'Opening another file')", viewer)
        self.assertIn("confirmDiscardExplorerEdit(index, 'Leaving this file')", viewer)
        self.assertIn("confirmDiscardExplorerEdit(index, 'Refreshing')", viewer)
        self.assertIn("&& !(await confirmDiscardExplorerEdit(index, 'Closing this tab'))", viewer)

        # Pane close, group switch, and group close in terminals.js await it.
        self.assertIn("confirmDiscardExplorerEdit(index, 'Closing this pane')", terminals_js)
        self.assertIn("confirmDiscardAllExplorerEdits('Switching sessions')", terminals_js)
        self.assertIn("confirmDiscardAllExplorerEdits('Closing this session')", terminals_js)

        # A page-close beforeunload guard covers every dirty pane.
        self.assertIn("window.addEventListener('beforeunload'", editor)
        self.assertIn("if (hasAnyDirtyExplorerEdit()) {", editor)

        # Editor state is transient: never serialized into saved sessions or the
        # runtime snapshot (the tab-persist payload has no _explorerEdit).
        persist_start = viewer.index("function persistExplorerTabsToSession(index)")
        persist_end = viewer.index("\n    function ", persist_start + 1)
        self.assertNotIn("_explorerEdit", viewer[persist_start:persist_end])

    def test_terminals_page_explorer_editor_icons_and_styles_are_token_driven(self):
        """§6 + guardrail 7: stroke currentColor icons, token colors, class busy state."""
        icons = self._static("js/terminal-icons.js")
        css = self._static("css/terminals.css")
        for icon in ("EXPLORER_EDIT_ICON", "EXPLORER_SAVE_ICON", "EXPLORER_CANCEL_ICON"):
            self.assertIn(icon, icons)
            marker = f"const {icon} = '"
            svg = icons[icons.index(marker) + len(marker):]
            svg = svg[:svg.index("';")]
            self.assertIn('stroke="currentColor"', svg)
            self.assertNotIn("fill=\"#", svg)
        # Editor surface + bars styled from tokens; busy state is a class.
        self.assertIn(".explorer-source-editor {", css)
        self.assertIn("font-size: var(--explorer-editor-font-size", css)
        self.assertIn(".explorer-edit-save-btn.is-busy {", css)
        self.assertIn(".explorer-tab.is-dirty .explorer-tab-name::after {", css)
        # No raw hex palette literals in the new editor rules.
        editor_css = css[css.index("/* ── In-app editor controls"):css.index(".explorer-tab.is-dirty .explorer-tab-name::after")]
        self.assertNotRegex(editor_css, r":\s*#[0-9a-fA-F]{3,6}\b")

    def test_terminals_page_explorer_tabs_show_unstaged_git_status(self):
        """Open tabs mirror only the worktree/unstaged status column."""
        viewer = self._static("js/explorer-viewer.js")
        css = self._static("css/terminals.css")
        helper = viewer[
            viewer.index("function explorerTabUnstagedGit(git)"):
            viewer.index("function syncExplorerTabGitFromRepo", viewer.index("function explorerTabUnstagedGit(git)"))
        ]
        self.assertIn("const worktreeCode = git.worktree_status || ' ';", helper)
        self.assertIn("if (explorerGitCodeUnmodified(worktreeCode)) {", helper)
        self.assertIn("explorerGitStatusFromCode(worktreeCode)", helper)
        self.assertIn("assignedTab.git = data.git || null;", viewer)
        self.assertIn("renderedTab.git = data.git || null;", viewer)
        self.assertIn("syncExplorerTabGitFromRepo(index, data);", viewer)
        self.assertIn("${gitBadge}", viewer)
        self.assertIn(".explorer-tab-main > .explorer-git-badge {", css)

    def test_terminals_page_explorer_diff_line_undo_is_revision_guarded(self):
        """Per-line discard stays inside the existing bounded editor save route."""
        viewer = self._static("js/explorer-viewer.js")
        css = self._static("css/terminals.css")
        line_undo = viewer[
            viewer.index("function explorerDiffShowsOnlyWorktreeChanges(pane)"):
            viewer.index(
                "/* Render the patch with the pinned Diff2Html build",
                viewer.index("function explorerDiffShowsOnlyWorktreeChanges(pane)"),
            )
        ]
        # It is offered only for complete editable unstaged worktree diffs.
        for guard in (
            "pane._explorerDiffMode === 'worktree'",
            "explorerDiffShowsOnlyWorktreeChanges(pane)",
            "!pane._explorerDiffCommit",
            "pane._explorerFileEditable",
            "!pane._explorerFileTruncated",
            "!pane._explorerDiffTruncated",
            "pane._explorerFileRevision",
            "'\\\\ No newline at end of file'",
        ):
            self.assertIn(guard, line_undo)
        # A directly opened tab uses HEAD mode. Permit it only when HEAD and the
        # worktree diff are identical: an unchanged index plus an unstaged edit.
        self.assertIn("const indexCode = git.index_status || ' ';", line_undo)
        self.assertIn("const worktreeCode = git.worktree_status || ' ';", line_undo)
        self.assertIn("explorerGitCodeUnmodified(indexCode)", line_undo)
        self.assertIn("!explorerGitCodeUnmodified(worktreeCode)", line_undo)
        self.assertIn("git.status === 'conflicted'", line_undo)
        # The action confirms in-page and reuses the optimistic full-file save
        # contract; it does not add a patch-accepting Git endpoint.
        self.assertIn("await openGenericConfirmModal({", line_undo)
        self.assertIn("/file`, {", line_undo)
        self.assertIn("const baseRevision = pane._explorerFileRevision;", line_undo)
        self.assertIn("pane._explorerFileRevision !== baseRevision", line_undo)
        self.assertIn("base_revision: baseRevision", line_undo)
        self.assertNotIn("/git/", line_undo)
        self.assertIn("wireExplorerDiffUndoControls(index, host);", viewer)
        self.assertIn("wireExplorerDiffUndoControls(index, code);", viewer)
        self.assertIn(".explorer-diff-undo-line {", css)
        undo_css = css[
            css.index(".explorer-diff-undo-line {"):
            css.index(".explorer-diff-empty {")
        ]
        self.assertIn("border-radius: var(--gv-radius-s);", undo_css)
        self.assertNotRegex(undo_css, r":\s*#[0-9a-fA-F]{3,6}\b")

    def test_terminals_page_explorer_diff_block_undo(self):
        """A contiguous run of changed lines can be undone in one save, through
        the same revision-guarded editor route the per-line undo uses."""
        viewer = self._static("js/explorer-viewer.js")
        css = self._static("css/terminals.css")
        blocks = viewer[
            viewer.index("function explorerDiffChangeBlocks(diff)"):
            viewer.index("function explorerRenderedDiffLine(row, {")
        ]
        # A block is a maximal run of changed lines: a context line ends it.
        self.assertIn("const flushRun = () => {", blocks)
        self.assertIn("startRun().replacement.push(line.slice(1));", blocks)
        self.assertIn("startRun().expected.push(line.slice(1));", blocks)
        self.assertIn("if (line.startsWith(' ')) {", blocks)
        # Applying a block is one splice, guarded by every worktree line the
        # block expects still matching — a stale diff refuses instead of
        # corrupting the file.
        self.assertIn(
            "const stale = expected.some((text, offset) => lines[lineIndex + offset] !== text);",
            viewer,
        )
        self.assertIn("lines.splice(lineIndex, expected.length, ...replacement);", viewer)
        # Single-line blocks keep the existing per-line button only.
        self.assertIn("if (!pane || !block || block.rows < 2) {", viewer)
        # Same bounded save contract as the per-line undo; no Git patch route.
        undo = viewer[
            viewer.index("async function undoExplorerDiffChange(index, actionId)"):
            viewer.index("/* Render the patch with the pinned Diff2Html build")
        ]
        self.assertIn("base_revision: baseRevision", undo)
        self.assertNotIn("/git/", undo)
        # Hover reveal is wired once per rendered container (no listener leak).
        self.assertIn("if (root._explorerDiffBlockHoverWired) {", viewer)
        # Token-driven styling only (Regression Guardrail 7).
        block_css = css[
            css.index(".explorer-diff-undo-block {"):
            css.index(".explorer-diff-empty {")
        ]
        self.assertIn("var(--explorer-btn-bg)", block_css)
        self.assertIn("border-radius: var(--gv-radius-s);", block_css)
        self.assertNotRegex(block_css, r":\s*#[0-9a-fA-F]{3,6}\b")

    def test_terminals_page_explorer_markdown_links_open_tabs(self):
        """ISSUE-2026-016: Markdown preview links resolve and open explorer tabs."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function wireExplorerMarkdownLinks(index, preview)", html)
        self.assertIn("function explorerClassifyLink(href)", html)
        self.assertIn("function explorerResolveRelativePath(baseFilePath, href)", html)
        self.assertIn("function explorerScrollPreviewToHeading(preview, fragment)", html)
        # Relative Markdown links open as a pinned tab.
        self.assertIn("openExplorerFile(index, resolved.path, { pinned: true })", html)
        # Fragment-only links scroll within the current preview.
        self.assertIn("explorerScrollPreviewToHeading(preview, info.fragment);", html)
        # External links open isolated and never navigate the session page away.
        self.assertIn("window.open(info.href, '_blank', 'noopener,noreferrer');", html)
        # mailto is left to the default handler.
        self.assertIn("if (info.type === 'mailto') {", html)
        # Traversal above the Explorer root is rejected.
        self.assertIn("if (!segments.length) {", html)
        self.assertIn("if (segment.includes(':')) {", html)
        # Wired into both the full render and in-place refresh preview paths.
        self.assertEqual(html.count("wireExplorerMarkdownLinks(index, preview);"), 2)

    def test_terminals_page_explorer_persists_open_tabs(self):
        """ISSUE-2026-015: open tabs serialize into and restore from a session."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function explorerSerializeTabs(pane)", html)
        self.assertIn("function persistExplorerTabsToSession(index)", html)
        self.assertIn("function restoreExplorerPersistedTabs(index)", html)
        self.assertIn("explorer_open_tabs: explorerTabs.open_tabs,", html)
        self.assertIn("explorer_active_tab: explorerTabs.active_tab,", html)
        self.assertIn("Array.isArray(session.explorer_open_tabs)", html)
        self.assertIn("restoreExplorerPersistedTabs(index);", html)
        # Bounded pinned-tab count shared with the backend cap.
        self.assertIn("const EXPLORER_MAX_PINNED_TABS = 12;", html)

    def test_terminals_page_persists_tab_views_and_markdown_appearance(self):
        """2.f: per-tab mode/scroll and the Markdown appearance round-trip sessions."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Serialize: the shown tab is captured live, then each pinned tab's view
        # reduces to the persisted shape (mode + scroll fraction + identity, OD-5).
        self.assertIn("function explorerPersistableTabView(tab)", html)
        self.assertIn("explorerCaptureActiveTabView(explorerSlot);", html)
        self.assertIn("explorer_tab_views: explorerTabs.tab_views,", html)
        self.assertIn("explorer_md_preset: mdAppearance ? mdAppearance.preset : '',", html)
        self.assertIn("explorer_md_font: mdAppearance ? mdAppearance.font : '',", html)
        # Restore: persisted views seed the rebuilt tab records; the OD-4
        # identity check decides at render time whether they still apply.
        self.assertIn("function explorerInflatePersistedTabView(raw)", html)
        self.assertIn("const view = explorerInflatePersistedTabView(rawViews[key]);", html)
        # Per-tab editor zoom persists too — including the Preview tab's, under
        # its reserved id — omitted at the default so unzoomed tabs store nothing.
        self.assertIn("function explorerPersistedTabFontSize(raw)", html)
        self.assertIn("fontSize !== EXPLORER_EDITOR_FONT_DEFAULT", html)
        self.assertIn("tabViews[EXPLORER_PREVIEW_TAB_ID] = previewRecord;", html)
        self.assertIn("record.diff_mode = diffMode;", html)
        self.assertIn("record.diff_commit = diffCommit;", html)
        self.assertIn("const previewView = explorerInflatePersistedTabView(rawPreviewView);", html)
        self.assertIn("previewTab.view = previewView;", html)
        self.assertIn("previewTab.fontSize = previewFont;", html)
        self.assertIn("record.fontSize = fontSize;", html)
        self.assertIn("function explorerTabPersistedDiffTarget(tab)", html)
        self.assertIn("...explorerTabPersistedDiffTarget(previewTab)", html)
        # The saved-session relaunch payload carries the new fields through.
        self.assertIn(
            "explorer_tab_views: resolvedStartupMode === 'explorer' && terminal?.explorer_tab_views",
            html,
        )
        # Markdown appearance re-applies once per session id (ISSUE-2026-033) so
        # a close rebuild cannot clobber an appearance changed since launch.
        self.assertIn("function applyExplorerSessionMarkdownAppearance(index)", html)
        self.assertIn("setExplorerMarkdownAppearance({ preset, font });", html)
        self.assertIn("applyExplorerSessionMarkdownAppearance(index);", html)

    def test_terminals_page_preview_tab_keeps_separated_path(self):
        """The Preview tab keeps its own file/directory path across tab swaps
        and persists it into saved sessions."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Directory browsing records the browsed path on the Preview tab itself.
        self.assertIn("previewTab.dirPath = pane._explorerPath;", html)
        # Returning to the Preview tab after a pinned tab was active re-browses
        # the tab's own directory instead of falling through to the empty viewer.
        self.assertIn("loadExplorerPane(index, tab.dirPath);", html)
        # The empty viewer clears the tab's separated path too.
        self.assertIn("preview.dirPath = '';", html)
        # Serialize: the reserved Preview record carries its own path + dir
        # alongside the zoom, so workspace saves keep the Preview content.
        self.assertIn("previewRecord.path = previewPath;", html)
        self.assertIn("previewRecord.dir = previewDir;", html)
        # Restore: the saved Preview path/dir seed the tab record and reopen
        # when no pinned tab was saved as active.
        self.assertIn("previewTab.dirPath = savedPreviewDir;", html)
        self.assertIn("openExplorerFile(index, savedPreviewPath, {", html)
        self.assertIn("...explorerTabPersistedDiffTarget(previewTab)", html)
        self.assertIn("loadExplorerPane(index, savedPreviewDir);", html)
        # The terminal-close snapshot carries dirPath through the rebuild.
        self.assertIn("dirPath: tab.dirPath || ''", html)
        self.assertIn("tab.dirPath = saved.dirPath;", html)
        # First show goes through the viewer entry point — a bare root load
        # racing the restore could resolve last and clobber the Preview path.
        self.assertNotIn("loadExplorerPane(i);", html)

    def test_terminals_page_explorer_tabs_preserve_view_mode_and_scroll(self):
        """2.e: each explorer tab keeps its own view mode + scroll across swaps."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Per-tab snapshot helpers (mode + fraction-based scroll + identity).
        self.assertIn("function explorerCaptureActiveTabView(index)", html)
        self.assertIn("function explorerMatchingTabView(tab, identity)", html)
        self.assertIn(
            "function explorerFileContentIdentity(path, content, diffCommit, diffMode)",
            html,
        )
        self.assertIn("function explorerDirectoryContentIdentity(path, entries)", html)
        # The snapshot lives on the tab record, not in pane-global state.
        self.assertIn("tab.view = {", html)
        # OD-4 skip rule: a stale snapshot (content changed) is never restored.
        self.assertIn("view.identity !== identity", html)
        # Capture runs before the active tab id flips, while the DOM is intact.
        activate = html[html.index("function activateExplorerTab(index, id)"):]
        self.assertLess(
            activate.index("explorerCaptureActiveTabView(index);"),
            activate.index("pane._explorerActiveTabId = tab.id;"),
        )
        # Opening another file (tree click / markdown link) also captures the
        # outgoing tab before the loading placeholder replaces the viewer.
        open_file = html[html.index("async function openExplorerFile(index, path"):]
        self.assertLess(
            open_file.index("explorerCaptureActiveTabView(index);"),
            open_file.index("renderExplorerMessage(index, 'Opening file...');"),
        )
        # Restore drives the initial view mode per tab (source/preview/diff).
        self.assertIn("const restoredMode = restoredTabView ? restoredTabView.mode : '';", html)
        self.assertIn("restoredMode === 'diff'", html)
        self.assertIn("preferredFileView === 'preview' && hasPreview ? 'preview' : 'source'", html)
        # Scroll restore falls back to the tab snapshot and stays aligned with
        # the restored mode; fractions + clamping live in restoreExplorerFileScroll.
        self.assertIn("const effectiveScrollState = scrollState || (restoredTabView", html)
        self.assertIn("{ ...restoredTabView.scroll, activeView: initialFileView }", html)
        # Directory browsing on the Preview tab gets the same treatment — on
        # capture, on the in-memory re-render, and after a re-browse fetch.
        self.assertEqual(
            html.count("explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)"),
            3,
        )
        # Mode switching is skipped when there are no file panels (directory).
        self.assertIn("listEl.querySelector('[data-explorer-file-panel]')", html)

    def test_terminals_page_explorer_diff_scroll_restored_after_async_load(self):
        """2.e: a diff tab's scroll survives the async diff fetch on restore."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function applyExplorerPendingDiffScroll(index)", html)
        self.assertIn("pane._explorerPendingDiffScroll = initialFileView === 'diff'", html)
        # Applied on both the cached and the freshly-fetched diff paths.
        self.assertEqual(html.count("applyExplorerPendingDiffScroll(index);"), 2)

    def test_terminals_page_explorer_preview_tab_isolated_from_pinned_tabs(self):
        """2.e: plain clicks always load the Preview tab, never hijack pinned tabs."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        assign = html[
            html.index("function explorerAssignOpenTab(pane, path"):
            html.index("function explorerEnsureViewerShell(index)")
        ]
        # The active-pinned-tab reuse branch is gone: a plain click can no
        # longer repurpose a pinned tab that happens to show the same path.
        self.assertNotIn("active.pinned", assign)
        self.assertIn("const preview = explorerPreviewTab(pane);", assign)
        self.assertIn("pinned tabs are never hijacked", html)
        # Same-tab refreshes (git actions, pane refresh, the open-file change
        # listener's quiet re-read) pass the active tab explicitly so they are
        # not rerouted into the Preview tab. The two additional uses come from
        # the in-app editor's save-success re-render and conflict "Reload from
        # disk" (explorer-editor.js).
        self.assertEqual(html.count("tab: pane._explorerActiveTabId"), 5)

    def test_terminals_page_explorer_capture_tracks_rendered_tab(self):
        """2.e: a same-path Preview render never overwrites a pinned tab's snapshot."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # The capture only stores when the viewer DOM provably belongs to the
        # active tab — with Preview isolation, two tabs may show the same path.
        self.assertIn("pane._explorerRenderedTabId !== pane._explorerActiveTabId", html)
        # Every render entry point stamps the tab it rendered for.
        self.assertIn("pane._explorerRenderedTabId = assignedTab.id;", html)
        self.assertIn("const commitTab = explorerAssignOpenTab(pane, path, {});", html)
        self.assertIn("pane._explorerRenderedTabId = commitTab.id;", html)
        self.assertEqual(html.count("pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;"), 4)
        # The loading placeholder disowns the DOM until the next render.
        self.assertIn("pane._explorerRenderedTabId = '';", html)

    def test_terminals_page_explorer_zoom_and_mode_are_per_tab(self):
        """2.e: editor font size lives on the tab; Preview keeps its mode preference."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Font size is stored on the tab record, not pane-global.
        self.assertNotIn("_explorerEditorFontSize", html)
        self.assertIn("tab.fontSize = clampExplorerEditorFontSize(", html)
        # The sticky source/preview preference is recorded on explicit mode
        # switches and carried by the Preview tab across different files.
        self.assertIn("explorerActiveTab(pane).preferredMode = selectedMode;", html)
        self.assertIn("assignedTab.id === EXPLORER_PREVIEW_TAB_ID", html)
        self.assertIn("assignedTab.preferredMode || ''", html)
        self.assertIn("const preferredFileView = restoredMode || carriedMode;", html)

    def test_terminals_page_tree_directory_click_browses_in_preview(self):
        """A Files-tree directory *name* click browses it in the Preview tab;
        the fold arrow beside it is a separate expand/collapse-only control."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        toggle = html[
            html.index("async function toggleExplorerTreeDirectory(index, path)"):
            html.index("async function openExplorerTreeDirectory(index, path)")
        ]
        # The fold arrow is its own button and never navigates the Preview tab,
        # so browsing the tree cannot evict the file the pane is showing.
        self.assertIn("data-explorer-tree-chevron", html)
        self.assertIn(
            "toggleExplorerTreeDirectory(index, button.dataset.explorerTreeChevron || '');",
            html,
        )
        self.assertNotIn("loadExplorerPane(", toggle)
        self.assertIn("pane._explorerTreeExpanded.delete(path);", toggle)
        # The name button navigates and expands, but never collapses.
        open_dir = html[
            html.index("async function openExplorerTreeDirectory(index, path)"):
            html.index("async function revealExplorerTreePath(index, targetPath = '')")
        ]
        self.assertEqual(open_dir.count("await loadExplorerPane(index, path);"), 1)
        self.assertNotIn("pane._explorerTreeExpanded.delete(path);", open_dir)
        self.assertIn(
            "openExplorerTreeDirectory(index, button.dataset.explorerTreeDir || '');",
            html,
        )
        # Navigating still reveals the target row, but no longer force-expands
        # the directory itself (that would undo the collapse click).
        reveal = html[
            html.index("async function revealExplorerTreePath(index, targetPath = '')"):
            html.index("function focusExplorerTreeRow(index, path)")
        ]
        self.assertIn("segments.pop();", reveal)
        self.assertNotIn("if (pane._explorerMode === 'file') {", reveal)
        # Directory navigation captures the outgoing tab's mode + scroll
        # before the loading placeholder guts the viewer (2.e parity with
        # openExplorerFile).
        load_pane = html[html.index("async function loadExplorerPane(index, path"):]
        self.assertLess(
            load_pane.index("explorerCaptureActiveTabView(index);"),
            load_pane.index("renderExplorerMessage(index, 'Loading directory...');"),
        )

    def test_terminals_page_explorer_breadcrumb_navigation(self):
        """2.d (OD-3): the path label is a breadcrumb; ancestors browse in Preview."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function renderExplorerPathBreadcrumb(index, path", html)
        # Ancestor segments are buttons that navigate the Preview tab; the
        # shown directory/file itself is inert.
        self.assertIn('data-explorer-crumb=', html)
        self.assertIn("loadExplorerPane(index, button.dataset.explorerCrumb || '');", html)
        self.assertIn('class="explorer-crumb current"', html)
        # One definition + all six render paths (directory listing, file,
        # image viewer, in-place refresh, commit diff, move retarget) use it.
        self.assertEqual(html.count("renderExplorerPathBreadcrumb("), 7)
        # Token-driven styling only (Regression Guardrail 7).
        crumb_css = html[html.index(".explorer-crumb {"):html.index(".explorer-crumb-sep {")]
        self.assertIn("var(--explorer-muted)", crumb_css)
        self.assertIn("var(--t-accent)", crumb_css)
        self.assertNotRegex(crumb_css, r"#[0-9a-fA-F]{3,8}\b")

    def test_terminals_page_tab_strip_drag_middle_click_and_promote(self):
        """2.g: tab drag-reorder (OD-6), middle-click close, Preview double-click pin."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Pinned tabs are draggable; the permanent Preview tab is not.
        self.assertIn("${isPreview ? '' : ' draggable=\"true\"'}", html)
        wire = html[
            html.index("function wireExplorerTabStripInteractions(index, tabEl)"):
            html.index("function clearExplorerTabDragMarkers(index)")
        ]
        # The Preview branch wires only the double-click promotion and returns
        # before the close/drag handlers (same close guard as the ×).
        self.assertLess(
            wire.index("promoteExplorerPreviewTab(index);"),
            wire.index("auxclick"),
        )
        self.assertIn("if (id === EXPLORER_PREVIEW_TAB_ID) {", wire)
        # Middle-click closes a pinned tab (and suppresses autoscroll).
        self.assertEqual(wire.count("event.button === 1"), 2)
        self.assertIn("closeExplorerTab(index, id);", wire)
        # Reorder is pinned-tabs-only and clamps behind the Preview tab (OD-6);
        # the persisted order follows the array (2.f).
        reorder = html[
            html.index("function reorderExplorerPinnedTab(index, draggedId, targetId, before)"):
            html.index("function promoteExplorerPreviewTab(index)")
        ]
        self.assertIn("tab.pinned && tab.id === draggedId", reorder)
        self.assertIn("insertAt = Math.max(insertAt, previewPosition + 1);", reorder)
        self.assertIn("persistExplorerTabsToSession(index);", reorder)
        # Promotion hands the rendered DOM to the new pinned tab with the same
        # view mode / scroll / zoom — no re-fetch — and never clobbers an
        # existing pinned tab for the path.
        promote = html[
            html.index("function promoteExplorerPreviewTab(index)"):
            html.index("function renderExplorerViewerEmpty(index)")
        ]
        self.assertIn("pinnedTab.view = { ...preview.view };", promote)
        self.assertIn("pane._explorerRenderedTabId = pinnedTab.id;", promote)
        self.assertNotIn("openExplorerFile(", promote)
        self.assertIn("activateExplorerTab(index, existing.id);", promote)
        # Activating an already-shown tab is a no-op, so the double-click's
        # leading single-clicks cannot race the promotion with re-fetches.
        self.assertIn(
            "pane._explorerActiveTabId === tab.id && pane._explorerRenderedTabId === tab.id",
            html,
        )

    def test_terminals_page_tab_strip_copy_path_and_locate_in_tree(self):
        """Pinned tabs get the copy-path menu and a locate-in-tree double-click."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # A pinned tab joins the shared copy-path menu (the tree and Git rows
        # carry the same hook); the permanent Preview tab does not, and no
        # context kind is exposed, so the tab menu stays copy-only.
        self.assertIn("const copyPath = (!isPreview && tab.path)", html)
        self.assertIn('data-explorer-copy-path="${escHtml(tab.path)}"', html)
        wire = html[
            html.index("function wireExplorerTabStripInteractions(index, tabEl)"):
            html.index("function clearExplorerTabDragMarkers(index)")
        ]
        # Double-click: the Preview tab still promotes (its branch returns
        # first), every pinned tab locates its file in the Files tree.
        self.assertLess(
            wire.index("promoteExplorerPreviewTab(index);"),
            wire.index("revealExplorerTabInTree(index, id);"),
        )
        reveal = html[
            html.index("async function revealExplorerTabInTree(index, id)"):
            html.index("function renderExplorerViewerEmpty(index)")
        ]
        # Opening the Files panel is awaited so its own reveal cannot race the
        # ancestor expansion through the in-flight children guard.
        self.assertIn("await setExplorerTreeSidebarOpen(index, true);", reveal)
        self.assertIn("await revealExplorerTreePath(index, path);", reveal)
        self.assertIn("focusExplorerTreeRow(index, path);", reveal)
        # Locating is a pure reveal: it never re-opens or re-fetches the file.
        self.assertNotIn("openExplorerFile(", reveal)
        # The reveal targets an explicit path instead of whatever the viewer
        # happens to show, and the panel setters hand back the open promise.
        self.assertIn(
            "async function revealExplorerTreePath(index, targetPath = '')",
            html,
        )
        self.assertIn("return setExplorerSidebarPanelOpen(index, 'tree', open);", html)
        self.assertIn("function focusExplorerTreeRow(index, path)", html)
        self.assertIn("row.scrollIntoView({ block: 'nearest' });", html)
        # Token-driven flash styling only (Regression Guardrail 7).
        located_css = html[html.index(".explorer-tree-row.explorer-tree-located {"):]
        located_css = located_css[:located_css.index("}")]
        self.assertIn("var(--t-accent)", located_css)
        self.assertIn("var(--explorer-row-active)", located_css)
        self.assertNotRegex(located_css, r"#[0-9a-fA-F]{3,8}\b")

    def test_launcher_round_trips_explorer_open_tabs(self):
        """ISSUE-2026-015: launcher carries open tabs through without editing them."""
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Shared with the browser-pane tab strip, hence the neutral name.
        self.assertIn("function parseStringArrayDataset(value)", html)
        self.assertIn("data-explorer-open-tabs=", html)
        self.assertIn("data-explorer-active-tab=", html)
        self.assertIn("explorer_open_tabs: commandMode === 'explorer'", html)
        self.assertIn("explorer_open_tabs: resolvedStartupMode === 'explorer'", html)

    def test_pages_carry_the_active_session_group_through_workspace_restore(self):
        """Both save paths record the front group; the restore reopens on it."""
        terminals_html = self._page_html(self.client.get("/terminals"))
        # Every activeGroupId change reports the front group, so the autosave
        # timer can capture it without a window to ask.
        self.assertIn("function reportActiveSessionGroup(groupId)", terminals_html)
        self.assertIn("fetch('/api/session-groups/active', {", terminals_html)
        self.assertIn("reportActiveSessionGroup(groupId);", terminals_html)
        # An explicit Save Workspace names the saving window's own group and,
        # when desktop mode is active, carries the session window zoom.
        self.assertIn(
            "const nativeZoomFactor = await getCurrentWorkspaceNativeZoomFactor();",
            terminals_html,
        )
        self.assertIn("active_group_id: activeGroupId,", terminals_html)
        self.assertIn("native_zoom_factor: nativeZoomFactor", terminals_html)
        self.assertIn("notifyWorkspacesChanged('workspace_saved');", terminals_html)

        launcher_html = self._page_html(self.client.get("/"))
        self.assertIn(
            "restorableActiveGroupId = String(data.active_group_id || '');",
            launcher_html,
        )
        # Restored groups get fresh ids, so the saved one is resolved to the
        # group the replay actually created before opening the workspace on it.
        self.assertIn(
            "if (restorableActiveGroupId && group.group_id === restorableActiveGroupId) {",
            launcher_html,
        )
        self.assertIn(
            "restorableNativeZoomFactor = normalizeNativeZoomFactor(data.native_zoom_factor);",
            launcher_html,
        )
        self.assertIn("activeGroupId,\n                    nativeZoomFactor", launcher_html)
        # Window dispatch now lives in the shared workspaces.js module, so the
        # launcher only names the workspace and the group to open on.
        self.assertIn(
            "await openWorkspaceWindow(resolvedWorkspaceId, {",
            launcher_html,
        )
        # Reopening a still-live workspace also carries the last focused group;
        # otherwise the terminals page falls back to the newest group.
        self.assertIn("groupId: workspace.active_group_id", launcher_html)
        workspaces_js = self._static("js/workspaces.js")
        self.assertIn(
            "return `gridvibe-workspace-${normalizeWorkspaceId(workspaceId)}`;",
            workspaces_js,
        )
        self.assertIn(
            "const params = new URLSearchParams({ workspace: normalizeWorkspaceId(workspaceId) });",
            workspaces_js,
        )

    def test_launcher_round_trips_explorer_tab_views_and_markdown_appearance(self):
        """2.f: launcher carries tab views + Markdown appearance without editing them."""
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function parseExplorerTabViewsDataset(value)", html)
        self.assertIn("data-explorer-tab-views=", html)
        self.assertIn("data-explorer-md-preset=", html)
        self.assertIn("data-explorer-md-font=", html)
        self.assertIn("data-explorer-theme=", html)
        self.assertIn("explorer_tab_views: commandMode === 'explorer'", html)
        self.assertIn("explorer_tab_views: resolvedStartupMode === 'explorer'", html)
        self.assertIn("explorer_md_preset: resolvedStartupMode === 'explorer'", html)
        self.assertIn("explorer_theme: commandMode === 'explorer' ? (row.dataset.explorerTheme || 'dark') : ''", html)
        self.assertIn("explorer_theme: resolvedStartupMode === 'explorer'", html)

    def test_terminals_page_explorer_sidebar_supports_tree_and_git_together(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("explorer-sidebar-splitter", html)
        self.assertIn("data-explorer-sidebar-splitter", html)
        self.assertIn("function syncExplorerSidebar(index)", html)
        self.assertIn("function applyExplorerSidebarSplit(index)", html)
        self.assertIn("function wireExplorerSidebarSplitter(index)", html)
        # The sidebar is an ordered N-panel registry with indexed splitters;
        # the inline grid-template-rows replaced the old .split/CSS-var split.
        self.assertIn("const EXPLORER_SIDEBAR_PANELS = [", html)
        self.assertIn("function explorerOpenSidebarPanels(pane)", html)
        self.assertIn("explorer-sidebar-splitter-${index}-${n}", html)
        self.assertIn("sidebar.dataset.panels = String(openCount);", html)
        self.assertIn("main.classList.toggle(panel.mainClass, isOpen);", html)
        self.assertIn("sidebar.style.gridTemplateRows = tracks.join(' ');", html)
        self.assertNotIn("--explorer-sidebar-tree-height", html)
        # Splitters live at fixed DOM slots between adjacent registry panels,
        # so visibility and drag targets resolve through the slot map rather
        # than assuming slot n == position n in the open set (Git+Search with
        # Files closed must use slot 1, not slot 0).
        self.assertIn("function explorerSidebarSplitterSlots(pane)", html)
        self.assertIn("splitter.hidden = !visibleSlots.has(n);", html)
        self.assertIn(
            "const slot = explorerSidebarSplitterSlots(pane).find(entry => entry.slot === n);",
            html,
        )
        # Dropping back to a single panel must clear the inline row tracks, so
        # the survivor fills the sidebar instead of keeping its split height.
        sync_start = html.index("function syncExplorerSidebar(index)")
        sync_end = html.index("function restoreExplorerSidebarState(index)", sync_start)
        self.assertIn("applyExplorerSidebarSplit(index);", html[sync_start:sync_end])
        self.assertNotIn("if (openCount >= 2) {", html[sync_start:sync_end])

    def test_terminals_page_explorer_diff_search_hooks_are_present(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("} else if (query && view === 'diff') {", html)
        self.assertIn("const diffMarks = markExplorerSearchInElement(diff, query, state.activeIndex || 0);", html)
        self.assertIn("renderExplorerDiff(index);", html)
        self.assertIn("if (activeExplorerFileView(index) === 'diff')", html)
        self.assertIn('data-explorer-file-panel="diff"', html)

    def test_terminals_page_markdown_and_diff_line_wrap_preferences(self):
        """Source, preview and diff expose per-tab, persisted, gutter-safe wrapping."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-explorer-line-wrap="${index}"', html)
        self.assertIn("applyExplorerLineWrapState(index, selectedMode);", html)
        # Wrapping lives on the active explorer tab (like the editor zoom), not
        # in one workspace-global preference, and saves with the session.
        self.assertIn(
            "const EXPLORER_LINE_WRAP_MODES = Object.freeze(['source', 'preview', 'diff']);",
            html,
        )
        self.assertIn("function setExplorerLineWrapPreference(index, mode, enabled)", html)
        self.assertIn(
            "return ensureExplorerTabLineWrap(explorerActiveTab(pane))[mode];",
            html,
        )
        self.assertIn(
            "ensureExplorerTabLineWrap(explorerActiveTab(pane))[mode] = Boolean(enabled);",
            html,
        )
        # Wrapping defaults ON, so the persisted flag is the opt-out: an absent
        # key restores wrapped.
        self.assertIn("source: current.source !== false,", html)
        self.assertIn("preview: current.preview !== false,", html)
        self.assertIn("diff: current.diff !== false,", html)
        self.assertIn("record.wrap_source = false;", html)
        self.assertIn("record.wrap_preview = false;", html)
        self.assertIn("record.wrap_diff = false;", html)
        self.assertIn("source: view.wrap_source !== false,", html)
        self.assertIn("preview: view.wrap_preview !== false,", html)
        # Unwrapped source keeps one row per line and scrolls sideways; the
        # wrapped variant drops the max-content floor so the code column reflows,
        # and the in-place editor follows the same per-tab flag.
        self.assertIn(".explorer-source-view.wrap-lines .explorer-source-lines {", html)
        self.assertIn(".explorer-source-view.wrap-lines .explorer-source-line-code {", html)
        self.assertIn(".explorer-source-view.wrap-lines .explorer-source-editor {", html)
        self.assertIn("textarea.wrap = wraps.source ? 'soft' : 'off';", html)
        self.assertIn("function explorerPersistedTabLineWrap(raw)", html)
        self.assertIn("record.lineWrap = explorerPersistedTabLineWrap(rawViews[key]);", html)
        # Wrapped diffs retain Diff2Html's intraline markup; paired row heights
        # are synchronized around the fixed middle number gutter.
        self.assertIn(
            "function synchroniseExplorerDiffWrappedRows(host)",
            html,
        )
        self.assertIn("row.style.height = `${maxHeight}px`;", html)
        self.assertIn(
            ".explorer-diff-content.wrap-lines .explorer-diff2html .d2h-code-line-ctn {",
            html,
        )
        self.assertIn("display: inline;", html)
        self.assertIn("white-space: pre-wrap;", html)
        self.assertIn(
            ":is(.d2h-code-side-line ins, .d2h-code-side-line del) {",
            html,
        )
        self.assertIn("max-width: 4em;", html)
        self.assertIn(".explorer-markdown-preview.wrap-lines > * {", html)
        self.assertIn(".explorer-markdown-preview p,", html)
        self.assertIn("text-align: justify;", html)
        self.assertIn("const EXPLORER_LINE_WRAP_ICON =", html)
        self.assertIn('stroke="currentColor"', html)

    def test_terminals_page_explorer_markdown_source_sections_can_be_collapsed(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function explorerMarkdownHeadingLevel(line)", html)
        self.assertIn("function explorerMarkdownHeadingLevels(records)", html)
        self.assertIn("data-explorer-markdown-section", html)
        self.assertIn(
            "function toggleExplorerMarkdownSection(index, lineNumber, { allSameLevel = false } = {})",
            html,
        )
        # Alt+click fans the toggle out to every heading sharing the clicked level;
        # the click handler forwards the modifier and the tooltip advertises it.
        self.assertIn("allSameLevel: event.altKey", html)
        self.assertIn("const targetLevel = levels.get(lineNumber);", html)
        self.assertIn("Alt: collapse all at this level", html)
        self.assertIn("Alt: expand all at this level", html)
        self.assertIn("function wireExplorerMarkdownSectionControls(index)", html)
        self.assertIn("tab.collapsedLines = new Set();", html)
        self.assertIn("record.folds = Array.from(tab.collapsedLines)", html)
        self.assertIn("record.fold_identity = tab.collapsedIdentity;", html)
        self.assertIn("explorerPersistedMarkdownFolds(rawViews[key])", html)
        self.assertIn("persistExplorerTabsToSession(index);", html)
        self.assertIn("wireExplorerMarkdownSectionControls(index);", html)
        self.assertNotIn('data-explorer-source-toggle="${index}"', html)
        self.assertNotIn("function setExplorerMarkdownSourceCollapsed", html)
        self.assertNotIn("pane._explorerSourceCollapsed", html)

    def test_terminals_page_markdown_preview_hierarchy_and_callouts(self):
        """ISSUE-2026-017: preview CSS gives headings/callouts a token-driven theme."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Full heading hierarchy is styled (not just h1-h3 as before).
        self.assertIn(".explorer-markdown-preview h6 {", html)
        # Callout blocks and their title row are styled.
        self.assertIn(".explorer-markdown-preview .md-callout {", html)
        self.assertIn(".explorer-markdown-preview .md-callout-title {", html)
        self.assertIn(".explorer-markdown-preview .md-callout-note {", html)
        self.assertIn(".explorer-markdown-preview .md-callout-caution {", html)
        # Callout accents come from per-theme tokens, not inline palette literals.
        self.assertIn("--md-callout-accent: var(--explorer-callout-note);", html)
        self.assertIn("--explorer-callout-note: #4493f8;", html)
        self.assertIn("--explorer-callout-caution: #cf222e;", html)

    def test_terminals_page_markdown_appearance_presets(self):
        """ISSUE-2026-030: preview offers persisted preset/font appearance controls."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Appearance model + popover control functions exist.
        self.assertIn("function explorerMarkdownAppearance()", html)
        self.assertIn("function setExplorerMarkdownAppearance(patch)", html)
        self.assertIn("function applyExplorerMarkdownAppearanceToElement(preview, appearance)", html)
        self.assertIn("function showExplorerMarkdownAppearanceMenu(anchor)", html)
        # Bounded allowlists and persisted preference keys.
        self.assertIn("const EXPLORER_MD_PRESETS = ['default', 'paper', 'contrast', 'vscode'];", html)
        self.assertIn(
            "'system', 'serif', 'consolas', 'cascadia-code', 'jetbrains-mono', 'courier-new'",
            html,
        )
        self.assertIn("const EXPLORER_MD_PRESET_KEY = 'gridvibe.mdPreviewPreset';", html)
        self.assertIn("const EXPLORER_MD_FONT_KEY = 'gridvibe.mdPreviewFont';", html)
        # Header control is present and gated to previewable files.
        self.assertIn('data-explorer-md-appearance="${index}"', html)
        # Appearance is applied idempotently on both preview render paths.
        self.assertEqual(
            html.count(
                "applyExplorerMarkdownAppearanceToElement(preview, explorerMarkdownAppearance());"
            ),
            2,
        )
        # Preset/font classes and their token-driven surfaces exist in CSS.
        self.assertIn(".explorer-markdown-preview.md-preset-paper {", html)
        self.assertIn(".explorer-markdown-preview.md-preset-contrast {", html)
        self.assertIn(".explorer-markdown-preview.md-font-serif {", html)
        self.assertIn(".explorer-markdown-preview.md-font-cascadia-code {", html)
        self.assertIn(".explorer-markdown-preview.md-font-jetbrains-mono {", html)
        self.assertIn(".explorer-markdown-preview.md-font-courier-new {", html)
        self.assertNotIn(".explorer-markdown-preview.md-font-mono {", html)
        self.assertNotIn(".explorer-markdown-preview.md-font-fira-code {", html)
        self.assertNotIn(".explorer-markdown-preview.md-font-source-code-pro {", html)
        self.assertNotIn(".explorer-markdown-preview.md-font-menlo {", html)
        self.assertIn("--md-preview-surface: var(--md-preset-paper-bg);", html)
        self.assertIn("--md-preset-paper-bg: #f4ecd8;", html)

    def test_terminals_page_renders_mermaid_and_exposes_preview_shortcut(self):
        response = self.client.get("/terminals")
        asset_response = self.client.get("/static/vendor/mermaid.min.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(asset_response.status_code, 200)
        self.assertGreater(len(asset_response.data), 1_000_000)
        html = self._page_html(response)
        self.assertIn("vendor/mermaid.min.js", html)
        self.assertIn("async function renderExplorerMermaid(preview)", html)
        self.assertIn("securityLevel: 'strict'", html)
        self.assertIn("window.mermaid.render(", html)
        self.assertIn("renderExplorerMermaid(preview);", html)
        self.assertIn('title="Preview (Ctrl+Shift+V)"', html)
        self.assertIn('aria-keyshortcuts="Control+Shift+V Meta+Shift+V"', html)
        self.assertIn("event.code !== 'KeyV'", html)
        self.assertIn("function findExplorerMarkdownPreviewTargetIndex()", html)
        self.assertIn(".explorer-markdown-preview .explorer-mermaid svg {", html)

    def test_terminals_page_markdown_slate_preset(self):
        """Wave 1 / 3.a (OD-7): VS Code-style Slate preset (key `vscode`)."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Registered in the allowlist with the user-facing "Slate" label.
        self.assertIn("const EXPLORER_MD_PRESETS = ['default', 'paper', 'contrast', 'vscode'];", html)
        self.assertIn("vscode: 'Slate',", html)
        # Token block + remapping rule, same fixed-surface pattern as paper/contrast.
        self.assertIn(".explorer-markdown-preview.md-preset-vscode {", html)
        self.assertIn("--md-preset-vscode-bg: #1e1e1e;", html)
        self.assertIn("--md-preset-vscode-ink: #d4d4d4;", html)
        self.assertIn("--md-preview-surface: var(--md-preset-vscode-bg);", html)
        self.assertIn("--md-preview-ink: var(--md-preset-vscode-ink);", html)
        self.assertIn("--md-preview-muted: var(--md-preset-vscode-muted);", html)
        self.assertIn("--md-preview-border: var(--md-preset-vscode-border);", html)

    def test_terminals_page_explorer_open_tab_button_is_theme_token_driven(self):
        """Wave 1 / 1.d: tree row "+" control follows the light/dark theme tokens."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # The control still reuses the search-btn markup in the tree row.
        self.assertIn('class="explorer-search-btn explorer-open-tab-btn"', html)
        # ...but carries a distinct ↗ glyph so it never reads as the git stage "+".
        self.assertIn(
            'aria-label="Open ${escHtml(entry.name || path)} in a new tab">↗</button>',
            html,
        )
        # ...but now has its own rule drawing from the theme-aware explorer tokens.
        self.assertIn(".explorer-open-tab-btn {", html)
        self.assertIn(".explorer-open-tab-btn:hover,", html)
        self.assertIn(".explorer-open-tab-btn:focus-visible {", html)
        self.assertIn("border-color: var(--explorer-open-folder-border);", html)
        self.assertIn("background: var(--explorer-open-folder-bg);", html)
        self.assertIn("color: var(--explorer-open-folder-text);", html)
        self.assertIn("background: var(--explorer-open-folder-hover-bg);", html)

    def test_terminals_page_explorer_preview_back_button_removed(self):
        """Wave 1 / 2.a (OD-3): the vestigial single-file Back button is gone."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertNotIn("explorer-editor-back", html)
        self.assertNotIn("data-explorer-editor-back", html)

    def test_terminals_page_markdown_source_headings_are_coloured(self):
        """Wave 1 / 3.b (OD-8): heading-only tokeniser colours Source headings."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        # Heading lines are wrapped using the fence-aware heading level map.
        self.assertIn('class="explorer-md-source-heading explorer-md-source-heading-', html)
        self.assertIn("${lineHtml}</span>`", html)
        # Token-driven colour (shared accent, no palette literal in the rule).
        self.assertIn(".explorer-md-source-heading {", html)
        self.assertIn("color: var(--t-accent);", html)

    def test_terminals_page_go_filenames_frontend_classifier(self):
        """Wave 2 / 2.b (OD-2): frontend classifier mirrors the go.* filenames."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("'go.mod': 'go',", html)
        self.assertIn("'go.sum': 'text',", html)
        self.assertIn("'go.work': 'go',", html)
        self.assertIn("'go.work.sum': 'text',", html)

    def test_terminals_page_plain_preview_threshold(self):
        """Wave 2 / 4.a (OD-9): previews above ~2 MiB render without highlighting."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const EXPLORER_PLAIN_PREVIEW_THRESHOLD = 2 * 1024 * 1024;", html)
        # The flag is captured on both render paths and consulted by the source
        # renderer plus the Markdown preview highlighter.
        self.assertIn(
            "pane._explorerFilePlain = pane._explorerFileContent.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD;",
            html,
        )
        self.assertIn("pane._explorerFilePlain ? '' : (pane._explorerFileLanguage || '')", html)
        self.assertIn("if (!pane._explorerFilePlain) {", html)

    def test_terminals_page_exposes_per_terminal_clear_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-terminal-clear="${i}"', html)
        self.assertIn("function setTerminalClearState(index, clearing)", html)
        self.assertIn("async function clearTerminalDisplay(index)", html)

    def test_terminals_page_rebuilds_reused_group_views_when_session_ids_change(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
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
        with patch.object(api.runtime_config, "voice_engine", "whisper"), patch.object(
            api.runtime_config, "whisper_model", "base"
        ):
            response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
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

    def test_launcher_page_gates_voice_settings_on_backend_availability(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('id="appVoiceAvailability"', html)
        self.assertIn("async function loadVoiceStatus()", html)
        self.assertIn("async function installVoiceDependencies()", html)
        self.assertIn("'/api/voice-deps-install'", html)
        self.assertIn("engines_available", html)
        self.assertIn(".voice-availability {", html)

    def test_terminals_page_applies_voice_pref_changes_without_a_restart(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("socket.on('voice_prefs_updated'", html)
        self.assertIn("socket.on('voice_availability_updated'", html)
        self.assertIn("function _refreshVoiceRuntimeState() {", html)
        self.assertIn("_refreshVoiceRuntimeState();", html)

    def test_terminals_page_surfaces_unavailable_voice_backend_visibly(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function _showVoiceAlert(message) {", html)
        self.assertIn("_showVoiceAlert(backendUnavailableMessage);", html)
        self.assertIn(".voice-btn.unavailable {", html)

    def test_terminals_page_preflights_voice_backend_before_microphone_start(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        start = html.index("async function _startVoice(index) {")
        end = html.index("if (!navigator.mediaDevices?.getUserMedia)", start)
        startup_html = html[start:end]

        self.assertIn("await _loadVoiceServiceStatus();", startup_html)
        self.assertIn("const backendUnavailableMessage = _voiceBackendUnavailableMessage();", startup_html)
        self.assertIn("_setVoicePanelStatus(index, backendUnavailableMessage);", startup_html)

    def test_terminals_page_server_voice_errors_cleanup_without_stop_echo(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        handler_start = html.index("socket.on('voice_status', async ({ session_id, status, message }) => {")
        handler_end = html.index("return;", handler_start)
        error_handler = html[handler_start:handler_end]

        self.assertIn("await _stopVoice(index, { notifyServer: false });", error_handler)
        self.assertIn("async function _stopVoice(index, { notifyServer = true } = {})", html)

    def test_terminals_page_uses_voice_toggle_without_per_terminal_settings_panel(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-terminal-voice="${i}"', html)
        self.assertNotIn('id="tvoice-panel-toggle-${i}"', html)
        self.assertNotIn('id="tvoice-settings-${i}"', html)
        self.assertNotIn("settings: document.getElementById(`tvoice-settings-${index}`),", html)

    def test_terminals_page_keeps_voice_toggle_available_for_live_setting_refresh(self):
        with patch.object(api.runtime_config, "voice_enabled", False):
            response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-voice-enabled="false"', html)
        self.assertIn('data-terminal-voice-control="${i}"', html)
        self.assertIn('data-terminal-voice="${i}"', html)
        self.assertIn(".voice-btn:disabled", html)
        self.assertIn("cursor: default;", html)
        self.assertIn("elements.control.hidden = _voiceServiceStatus.enabled === false;", html)
        self.assertIn("function _syncVoiceControlsAvailability()", html)
        self.assertIn("_syncVoiceControlsAvailability();", html)
        self.assertIn("window.addEventListener('focus'", html)

    def test_terminals_page_uses_global_push_to_talk_preferences(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
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
        html = self._page_html(response)
        self.assertNotIn('class="terminal-action-btn terminal-shortcut-btn"', html)
        self.assertNotIn('data-terminal-enter="${i}"', html)
        self.assertNotIn('data-terminal-clearline="${i}"', html)
        self.assertNotIn("async function _sendEnterShortcut(index)", html)

    def test_terminals_page_places_voice_control_after_clear_button(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        clear_index = html.index('data-terminal-clear="${i}"')
        voice_index = html.index('data-terminal-voice-control="${i}"')
        close_index = html.index('data-terminal-close="${i}"')

        self.assertLess(clear_index, voice_index)
        self.assertLess(voice_index, close_index)

    def test_terminals_page_refreshes_only_one_terminal_by_replaying_its_buffer(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        refresh_start = html.index("async function refreshTerminalDisplay(index)")
        self.assertIn("terminal.term.reset();", html[refresh_start:])
        self.assertIn("emitTerminalResize(index, true);", html[refresh_start:])
        self.assertIn("socket.emit('leave_session', { session_id: sessionId });", html[refresh_start:])
        self.assertIn("socket.emit('join_session', { session_id: sessionId });", html[refresh_start:])

    def test_terminals_page_uses_updated_session_action_labels_and_styles(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('aria-label="Refresh all"', html)
        self.assertIn('class="refresh-all-icon"', html)
        self.assertNotIn(">Refresh all</button>", html)
        self.assertNotIn("Close Session</button>", html)
        self.assertIn("closeButton.className = 'session-tab-close';", html)
        self.assertIn("closeSessionGroup(group.group_id);", html)
        self.assertIn(".app-menu-panel {", html)
        self.assertIn(".app-menu-item {", html)
        self.assertIn(">Import Session ...</button>", html)

    def test_session_tabs_close_on_middle_click(self):
        """Todo 3 — middle-click closes a session tab like an explorer tab,
        through the same closeSessionGroup path (so the live-terminal
        confirmation still runs), with autoscroll suppressed."""
        terminals_js = self._static("js/terminals.js")
        render = terminals_js[
            terminals_js.index("function renderSessionTabs()"):
            terminals_js.index("function setSessionGroupsOrder(orderedGroupIds)")
        ]
        middle_click = render[render.index("button.addEventListener('mousedown'"):]
        self.assertIn("event.button === 1", middle_click)
        self.assertIn("event.preventDefault(); // suppress middle-click autoscroll", middle_click)
        self.assertIn("button.addEventListener('auxclick'", middle_click)
        self.assertIn("closeSessionGroup(group.group_id);", middle_click)

    def test_terminals_page_numbers_session_tabs_and_exposes_safe_shortcut(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
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
        html = self._page_html(response)
        self.assertIn('class="btn btn-success btn-icon"', html)
        self.assertIn('id="fullscreenBtn"', html)
        self.assertIn('title="Enter fullscreen"', html)
        self.assertIn('aria-label="Enter fullscreen"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn('class="fullscreen-icon"', html)
        self.assertIn("button.innerHTML = active ? FULLSCREEN_EXIT_ICON : FULLSCREEN_ENTER_ICON;", html)
        self.assertIn("button.title = label;", html)
        self.assertIn("button.setAttribute('aria-label', label);", html)
        self.assertIn("button.setAttribute('aria-pressed', active ? 'true' : 'false');", html)

    def test_terminals_page_exposes_max_surface_mode(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        grid_css = html[html.index("#terminalsGrid {"):html.index("/* Layout classes */")]
        self.assertIn("width: 100%;", grid_css)
        self.assertNotIn("min(1800px, 100%)", grid_css)
        self.assertIn('id="surfaceModeBtn"', html)
        self.assertIn('aria-label="Max surface"', html)
        self.assertIn('<rect x="4" y="4" width="16" height="16" rx="2.5"', html)
        self.assertIn("const SURFACE_MODE_STORAGE_KEY = 'gridvibe.terminalSurfaceMode';", html)
        self.assertIn("const DEFAULT_SURFACE_MODE =", html)
        self.assertIn("applyConfiguredSurfaceMode(data, { refit: gridBuilt });", html)
        self.assertIn("initSurfaceMode();", html)
        self.assertIn("document.body.classList.toggle('surface-max', active);", html)
        self.assertIn("redrawAttachedTerminals(attachedIndices, { forceResize: true });", html)
        self.assertIn("const APP_CONFIG_UPDATE_STORAGE_KEY = 'gridvibe.appConfigUpdated';", html)
        self.assertIn("const APP_CONFIG_BROADCAST_CHANNEL = 'gridvibe.appConfig';", html)
        self.assertIn("function applyAppConfigSurfaceMode(message)", html)
        self.assertIn("adoptGlobalSurfaceMode(message.workspace.surface_mode, { refit: true });", html)
        self.assertIn("function setupAppConfigUpdateListeners()", html)
        self.assertIn("setupAppConfigUpdateListeners();", html)
        self.assertIn("socket.on('app_config_updated'", html)
        self.assertIn("applyAppConfigUpdate(message || {});", html)

    def test_terminals_page_exposes_collapsible_topbar(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('class="topbar" id="terminalTopbar"', html)
        self.assertIn('class="session-bar"', html)
        self.assertIn('id="topbarToggleBtn"', html)
        self.assertIn('aria-controls="terminalTopbar"', html)
        self.assertIn("const TOPBAR_VISIBILITY_STORAGE_KEY = 'gridvibe.terminalTopbarVisibility';", html)
        self.assertIn("document.body.classList.toggle('topbar-collapsed', !shouldShow);", html)
        self.assertIn("path.setAttribute('d', visible ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6');", html)
        self.assertIn("applyTopbarVisibility(getStoredTopbarVisible());", html)

    def test_terminals_page_centers_topbar_actions_without_custom_window_controls(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);", html)
        self.assertIn("justify-self: center;", html)
        self.assertIn('class="topbar-actions"', html)
        self.assertNotIn('id="sessionWindowControls"', html)
        self.assertNotIn("window.pywebview.api.minimize_session_window", html)
        self.assertNotIn("window.pywebview.api.toggle_session_window_maximize", html)

    def test_terminals_page_buttons_use_session_color_frames(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("--session-color", html)
        self.assertIn("--session-color-dim", html)
        self.assertIn("var(--session-color-dim, var(--t-border-tab))", html)
        self.assertIn("var(--session-color, var(--t-accent))", html)
        self.assertIn("tabColourForGroup(activeGroupId)", html)

    def test_terminals_page_clear_sends_shell_command_and_purges_replay_buffer(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        clear_start = html.index("async function clearTerminalDisplay(index)")
        self.assertIn("terminal.term.reset();", html[clear_start:])
        self.assertIn("terminal.term.clear();", html[clear_start:])
        self.assertIn("const clearCommand = getTerminalClearCommand(index);", html[clear_start:])
        self.assertIn("socket.emit('clear_terminal_buffer', { session_id: sessionId });", html[clear_start:])
        self.assertIn("socket.emit('terminal_input', { session_id: sessionId, data: clearCommand });", html[clear_start:])

    def test_terminals_page_clear_command_matches_shell_family_and_host(self):
        """`cls` is a cmd/PowerShell command; POSIX hosts and WSL panes get `clear`."""
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        start = html.index("function getTerminalClearCommand(index)")
        end = html.index("function flushPendingOutput(index)", start)
        body = html[start:end]
        self.assertIn("if (session?.mode !== 'wsl') {\n            return 'clear\\r';", body)
        self.assertIn("if (session?.use_powershell) {\n            return 'cls\\r';", body)
        self.assertIn("if (session?.use_wsl) {\n            return 'clear\\r';", body)
        self.assertIn("return localShellModesAvailable() ? 'cls\\r' : 'clear\\r';", body)

    def test_terminals_page_redraws_attached_terminals_after_group_switch_rejoin(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
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
        html = self._page_html(response)
        self.assertIn("let cachedGroupViews = new Map();", html)
        self.assertIn("function cacheVisibleGroupView(groupId = visibleGroupId)", html)
        self.assertIn("function restoreCachedGroupView(groupId)", html)
        self.assertIn("cacheVisibleGroupView(visibleGroupId);", html)
        self.assertIn("restoredFromCache = restoreCachedGroupView(requestedGroupId);", html)
        self.assertIn("function captureTerminalViewportState(terminal)", html)
        self.assertIn("function restoreTerminalViewportState(terminal, state, { isCurrent = null } = {})", html)
        self.assertIn("captureCachedPaneUiState();", html)
        self.assertIn("restoreCachedPaneUiState({", html)
        self.assertIn("restoreTerminalViewports: false", html)
        self.assertIn("clearTerminalViewports: false", html)

    def test_terminals_page_restores_viewports_after_cached_group_redraw(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        restore_start = html.index("function restoreTerminalViewportState(terminal, state")
        restore_end = html.index("function captureCachedPaneUiState()", restore_start)
        restore_html = html[restore_start:restore_end]
        self.assertIn("state.wasAtBottom", restore_html)
        self.assertIn("terminal.term.scrollToBottom();", restore_html)
        self.assertIn("terminal.term.scrollToLine", restore_html)
        self.assertIn("pending.stillCurrent()", restore_html)

        initial_load_start = html.index("async function initialLoad()")
        initial_load_end = html.index("/* ─────────────────────────────────────────────", initial_load_start)
        initial_load_html = html[initial_load_start:initial_load_end]
        redraw = initial_load_html.index("await redrawAttachedTerminals(attachedIndices, {")
        restore = initial_load_html.index("restoreTerminalViewportState(terminals[index], state, { isCurrent: stillCurrent });")
        self.assertLess(redraw, restore)
        self.assertIn("terminals[index]?._cachedTerminalViewport || captureTerminalViewportState", initial_load_html)
        self.assertIn("terminal._cachedTerminalViewport = null;", initial_load_html[restore:])

    def test_terminals_page_routes_terminal_output_by_session_across_cached_groups(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("let sessionRouteMap = new Map();", html)
        self.assertIn("function resolveSessionTarget(sessionId)", html)
        self.assertIn("const target = resolveSessionTarget(session_id);", html)
        self.assertIn("if (pendingModeSwitchSessionIds.has(session_id)) return;", html)
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
            json={"sessions": [session_config] * (api.runtime_config.max_sessions + 1)},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": f"Maximum {api.runtime_config.max_sessions} sessions allowed"},
        )

    def test_voice_status_endpoint_includes_engine_model_and_language(self):
        with patch.object(api.runtime_config, "voice_enabled", True), patch.object(
            api.runtime_config, "voice_engine", "whisper"
        ), patch.object(api.runtime_config, "whisper_model", "base"):
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
        with patch.object(api.runtime_config, "voice_engine", "vosk"), patch.object(
            api.runtime_config, "vosk_model", "vosk-model-en-us-0.22"
        ), patch.object(api, "_vosk_service_reachable", return_value=False):
            response = self.client.get("/api/voice-status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["engine"], "vosk")
        self.assertEqual(payload["model"], "vosk-model-en-us-0.22")
        self.assertEqual(payload["service_url"], api.runtime_config.vosk_service_url)
        self.assertFalse(payload["service_running"])

    def test_voice_status_endpoint_reports_missing_whisper_dependency(self):
        with patch.object(api.runtime_config, "voice_engine", "whisper"), patch.object(
            web_voice, "WhisperModel", None
        ), patch.object(web_voice, "np", object()):
            response = self.client.get("/api/voice-status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["engine_available"])
        self.assertIn("faster-whisper", payload["status_message"])
        self.assertIn("pip install -r requirements-voice.txt", payload["status_message"])

    def test_voice_status_endpoint_reports_per_engine_availability(self):
        with patch.object(api.runtime_config, "voice_engine", "vosk"), patch.object(
            api, "_vosk_service_reachable", return_value=False
        ), patch.object(
            web_voice, "_vosk_service_packages_available", return_value=False
        ), patch.object(web_voice, "WhisperModel", object()), patch.object(
            web_voice, "np", object()
        ):
            response = self.client.get("/api/voice-status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["engine_available"])
        self.assertFalse(payload["engines_available"]["vosk"])
        self.assertTrue(payload["engines_available"]["whisper"])
        self.assertIn("vosk and websockets", payload["status_message"])
        self.assertIn("install", payload)

    def test_voice_status_endpoint_trusts_a_running_external_vosk_service(self):
        with patch.object(api.runtime_config, "voice_engine", "vosk"), patch.object(
            api, "_vosk_service_reachable", return_value=True
        ), patch.object(
            web_voice, "_vosk_service_packages_available", return_value=False
        ):
            response = self.client.get("/api/voice-status")

        payload = response.get_json()
        self.assertTrue(payload["engine_available"])
        self.assertTrue(payload["engines_available"]["vosk"])

    def test_voice_prefs_post_broadcasts_live_update(self):
        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                "/api/voice-prefs", json={"pttKeybind": "Ctrl+Shift+V"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["pttKeybind"], "Ctrl+Shift+V")
        events = [call.args[0] for call in emit.call_args_list]
        self.assertIn("voice_prefs_updated", events)
        payload = next(
            call.args[1]
            for call in emit.call_args_list
            if call.args[0] == "voice_prefs_updated"
        )
        self.assertEqual(payload["prefs"]["pttKeybind"], "Ctrl+Shift+V")

    def test_voice_deps_install_endpoint_starts_background_install(self):
        state = {"status": "running", "message": "Installing", "output_tail": []}
        with patch.object(
            api, "_start_voice_dependency_install", return_value=state
        ) as start:
            response = self.client.post("/api/voice-deps-install", json={})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["status"], "running")
        start.assert_called_once_with(
            on_complete=api._broadcast_voice_install_finished
        )

    def test_voice_deps_install_status_endpoint_reports_current_state(self):
        response = self.client.get("/api/voice-deps-install")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn(payload["status"], {"idle", "running", "success", "error"})
        self.assertIn("output_tail", payload)

    def _reset_voice_install_state(self):
        web_voice._set_voice_install_state(
            status="idle",
            message="",
            restart_required=False,
            started_at=None,
            finished_at=None,
            output_tail=[],
        )

    def test_voice_dependency_install_reloads_backends_without_a_restart(self):
        self.addCleanup(self._reset_voice_install_state)
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Successfully installed vosk", stderr=""
        )
        with patch.object(
            web_voice.subprocess, "run", return_value=completed
        ) as run, patch.object(
            web_voice, "_clear_voice_deps_declined_marker"
        ) as clear_marker, patch.object(
            web_voice, "_reload_voice_backends", return_value={"vosk": True, "whisper": True}
        ) as reload_backends:
            web_voice._perform_voice_dependency_install()

        self.assertIn("requirements-voice.txt", run.call_args.args[0][-1])
        clear_marker.assert_called_once_with()
        reload_backends.assert_called_once_with()
        state = web_voice._voice_install_status()
        self.assertEqual(state["status"], "success")
        self.assertFalse(state["restart_required"])

    def test_voice_dependency_install_failure_reports_pip_exit_code(self):
        self.addCleanup(self._reset_voice_install_state)
        completed = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="ERROR: no matching distribution"
        )
        with patch.object(
            web_voice.subprocess, "run", return_value=completed
        ), patch.object(web_voice, "_reload_voice_backends") as reload_backends:
            web_voice._perform_voice_dependency_install()

        reload_backends.assert_not_called()
        state = web_voice._voice_install_status()
        self.assertEqual(state["status"], "error")
        self.assertIn("code 2", state["message"])
        self.assertIn("ERROR: no matching distribution", state["output_tail"])

    def test_app_config_endpoint_returns_settings_payload(self):
        response = self.client.get("/api/app-config")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("appearance", payload)
        self.assertIn("theme", payload["appearance"])
        self.assertIn("workspace", payload)
        self.assertIn("surface_mode", payload["workspace"])
        self.assertIn("voice_input", payload)
        self.assertIn("engine", payload["voice_input"])
        self.assertIn("whisper_model", payload["voice_input"])

    def test_app_config_endpoint_persists_theme_and_voice_settings(self):
        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                "/api/app-config",
                json={
                    "appearance": {
                        "theme": "light",
                    },
                    "workspace": {
                        "surface_mode": "max",
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
        self.assertEqual(payload["workspace"]["surface_mode"], "max")
        self.assertEqual(payload["voice_input"]["engine"], "vosk")
        self.assertEqual(payload["voice_input"]["vosk_model"], "vosk-model-small-en-us-0.15")
        self.assertEqual(payload["voice_input"]["language"], "en-GB")
        cfg = api.load_config()
        self.assertEqual(cfg["appearance"]["theme"], "light")
        self.assertEqual(cfg["workspace"]["surface_mode"], "max")
        self.assertEqual(cfg["voice_input"]["engine"], "vosk")
        self.assertEqual(cfg["voice_input"]["vosk_model"], "vosk-model-small-en-us-0.15")
        emit.assert_called_with(
            "app_config_updated",
            {
                "appearance": {
                    "theme": "light",
                },
                "workspace": {
                    "surface_mode": "max",
                    "multi_workspace_enabled": False,
                },
                "terminal": {
                    "font_family": api.runtime_config.terminal_font_family,
                    "font_size": api.runtime_config.terminal_font_size,
                    # OD-14: no scope in the request → focused-session default.
                    "apply_scope": "session",
                },
                "timestamp": ANY,
            },
        )

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
            web_agents,
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
            web_agents,
            "_detect_agent_binary",
            return_value={
                "found": False,
                "path": "",
                "command": "command -v kilo",
                "error": "",
            },
        ), patch.object(
            web_agents,
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
            web_agents,
            "_detect_agent_binary",
            return_value={
                "found": False,
                "path": "",
                "command": "command -v kilo",
                "error": "",
            },
        ), patch.object(
            web_agents,
            "_select_install_option",
            return_value=(
                {"label": "npm", "command": "npm install -g @kilocode/cli", "manual_only": False},
                ["npm is required for the Linux or WSL install path."],
            ),
        ), patch.object(
            web_agents,
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
            web_agents,
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
            web_agents,
            "_detect_agent_binary",
            return_value={
                "found": True,
                "path": "/usr/local/bin/codex",
                "command": "command -v codex",
                "error": "",
            },
        ) as detect_agent_binary, patch.object(
            web_agents,
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

        with patch.object(api.os, "name", "nt"), patch.object(web_agents, "_find_wsl_executable", return_value="wsl.exe"), patch.object(
            web_agents.subprocess,
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

        with patch.object(web_agents, "paramiko", fake_paramiko):
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
            web_agents,
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

    def test_ssh_ping_endpoint_returns_icmp_success(self):
        completed = SimpleNamespace(returncode=0, stdout="Reply from 10.0.0.20: time=12.3 ms", stderr="")

        with patch.object(web_agents.shutil, "which", return_value="/bin/ping"), patch.object(
            web_agents.subprocess,
            "run",
            return_value=completed,
        ) as run_command:
            response = self.client.post("/api/ssh-ping", json={"host": "10.0.0.20", "port": 2222})

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["reachable"])
        self.assertEqual(body["method"], "icmp")
        self.assertEqual(body["target"], "10.0.0.20")
        self.assertEqual(body["port"], 2222)
        self.assertEqual(body["latency_ms"], 12.3)
        run_command.assert_called_once()

    def test_ssh_ping_endpoint_rejects_blank_host(self):
        response = self.client.post("/api/ssh-ping", json={"host": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Enter an SSH host or IP address before pinging."})

    def test_ssh_ping_falls_back_to_tcp_when_ping_is_unavailable(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.__exit__.return_value = False

        with patch.object(web_agents.shutil, "which", return_value=None), patch.object(
            web_agents.socket,
            "create_connection",
            return_value=connection,
        ) as create_connection:
            response = self.client.post("/api/ssh-ping", json={"host": "example.com", "port": 22})

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["reachable"])
        self.assertEqual(body["method"], "tcp")
        self.assertEqual(body["target"], "example.com")
        create_connection.assert_called_once_with(("example.com", 22), timeout=3.0)

    def test_load_config_falls_back_to_default_config_when_local_config_missing(self):
        default_path = Path(self.temp_dir.name) / "default_config.json"
        default_path.write_text(
            json.dumps({"voice_input": {"engine": "whisper", "whisper_model": "base"}}),
            encoding="utf-8",
        )

        missing_path = Path(self.temp_dir.name) / "missing-config.json"
        with patch.object(web_config, "DEFAULT_CONFIG_PATH", str(default_path)):
            cfg = api.load_config(str(missing_path))

        self.assertEqual(cfg["voice_input"]["engine"], "whisper")
        self.assertEqual(cfg["voice_input"]["whisper_model"], "base")

    def test_load_config_falls_back_to_default_config_when_local_config_is_invalid(self):
        default_path = Path(self.temp_dir.name) / "default_config.json"
        default_path.write_text(
            json.dumps({"appearance": {"theme": "system"}}),
            encoding="utf-8",
        )
        broken_path = Path(self.temp_dir.name) / "broken-config.json"
        broken_path.write_text('{"appearance": {"theme": "dark"}}\n}', encoding="utf-8")

        with patch.object(web_config, "DEFAULT_CONFIG_PATH", str(default_path)):
            with self.assertLogs(web_config.logger, level="WARNING") as logs:
                cfg = api.load_config(str(broken_path))

        self.assertEqual(cfg["appearance"]["theme"], "system")
        self.assertTrue(
            any("using default configuration" in message for message in logs.output),
            logs.output,
        )

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
        html = self._page_html(response)
        self.assertIn("_loadVoicePrefsFromServer", html)
        self.assertIn("fetch('/api/voice-prefs'", html)

    def test_install_kind_reports_git_for_the_dev_checkout(self):
        self.assertEqual(web_paths.install_kind(), "git")

    def test_install_kind_reports_source_without_a_git_directory(self):
        with TemporaryDirectory() as tmp:
            with patch.object(web_paths, "BASE_DIR", tmp):
                self.assertEqual(web_paths.install_kind(), "source")

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

        # perform_app_update() dispatches to perform_self_update() for git
        # checkouts (§A2); patch it where the dispatcher actually looks it up.
        with patch.object(selfupdate, "perform_self_update", return_value=update_payload):
            response = self.client.post("/api/app-update")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), update_payload)

    def test_app_update_endpoint_surfaces_expected_update_errors(self):
        with patch.object(
            selfupdate,
            "perform_self_update",
            side_effect=api.AppUpdateError("Local changes are present.", 409),
        ):
            response = self.client.post("/api/app-update")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json(), {"error": "Local changes are present."})

    def test_app_update_endpoint_reports_source_installs_cannot_self_update(self):
        """§A2 — source-ZIP checkouts get an honest, actionable message."""
        with patch.object(selfupdate, "install_kind", return_value="source"):
            response = self.client.post("/api/app-update")

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("cannot update itself", payload["error"])
        self.assertIn("Download the latest release", payload["error"])

    def test_perform_app_update_dispatches_on_install_kind(self):
        with patch.object(selfupdate, "install_kind", return_value="git"), patch.object(
            selfupdate, "perform_self_update", return_value={"updated": False}
        ) as mock_git_update:
            result = api.perform_app_update()
        mock_git_update.assert_called_once()
        self.assertEqual(result, {"updated": False})

        with patch.object(selfupdate, "install_kind", return_value="source"):
            with self.assertRaises(api.AppUpdateError) as context:
                api.perform_app_update()
        self.assertEqual(context.exception.status_code, 400)

    def test_app_config_reports_install_kind_and_version(self):
        payload = self.client.get("/api/app-config").get_json()
        self.assertEqual(payload["install_kind"], "git")
        self.assertEqual(payload["version"], __version__)

        with patch.object(api, "install_kind", return_value="source"):
            payload = self.client.get("/api/app-config").get_json()
        self.assertEqual(payload["install_kind"], "source")

    def test_perform_self_update_reports_when_checkout_is_current(self):
        git_results = [
            SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="origin/main\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="0\t0\n", stderr=""),
        ]

        with patch.object(selfupdate, "_run_repo_git", side_effect=git_results) as mock_git:
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

        with patch.object(selfupdate, "_run_repo_git", side_effect=git_results) as mock_git:
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

        with patch.object(selfupdate, "_run_repo_git", side_effect=git_results):
            with self.assertRaises(api.AppUpdateError) as context:
                api.perform_self_update()

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("Local changes are present", str(context.exception))

    def test_app_update_fast_forwards_real_checkout(self):
        """POST /api/app-update happy path against a real temp git repo (finding 6.6)."""
        with TemporaryDirectory() as tmp:
            origin = Path(tmp) / "origin"
            clone = Path(tmp) / "clone"
            origin.mkdir()
            (origin / "README.md").write_text("v1\n", encoding="utf-8")
            self._run_git(origin, "init")
            self._run_git(origin, "config", "user.email", "gridvibe@example.invalid")
            self._run_git(origin, "config", "user.name", "GridVibe Test")
            self._run_git(origin, "add", ".")
            self._run_git(origin, "commit", "-m", "initial")
            self._run_git(Path(tmp), "clone", str(origin), str(clone))
            (origin / "README.md").write_text("v2\n", encoding="utf-8")
            self._run_git(origin, "add", ".")
            self._run_git(origin, "commit", "-m", "second")
            expected_commit = self._run_git(origin, "rev-parse", "HEAD").stdout.decode().strip()

            with patch.object(selfupdate, "SELF_UPDATE_REPO_DIR", str(clone)):
                response = self.client.post("/api/app-update")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["updated"])
            self.assertTrue(payload["restart_required"])
            self.assertEqual(payload["behind_count"], 1)
            self.assertEqual(payload["current_commit"], expected_commit)
            clone_head = self._run_git(clone, "rev-parse", "HEAD").stdout.decode().strip()
            self.assertEqual(clone_head, expected_commit)

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
        api._cache_terminal_output(existing_session.session_id, "stale output")

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
                list(api.session_output_buffers),
                [existing_session.session_id],
            )
        self.assertEqual(
            api._get_buffered_terminal_output(existing_session.session_id),
            "stale output",
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
        self.assertEqual(initial_body["group"]["saved_session_id"], "session-dev-grid")
        self.assertEqual(initial_start_task.call_count, 1)

        api._cache_terminal_output(original_session_id, "stale output")

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
            web_terminal_io,
            "_close_ssh_connection",
            wraps=api._close_ssh_connection,
        ) as close_connection:
            replacement_response = self.client.post("/api/sessions", json=replacement_payload)

        self.assertEqual(replacement_response.status_code, 201)
        replacement_body = replacement_response.get_json()
        self.assertEqual(replacement_body["group_id"], original_group_id)
        self.assertEqual(replacement_body["group"]["saved_session_id"], "session-dev-grid")
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
        self.assertEqual(data["terminal_count"], min(4, api.runtime_config.max_sessions))
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

    def test_create_sessions_accepts_local_repo_browser_mode(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        sessions_payload = {
            "connection_mode": "wsl",
            "layout": "horizontal",
            "sessions": [
                {
                    "directory": str(repo_dir),
                    "title": "Preview",
                    "initial_command": "localhost:5173",
                    "startup_mode": "browser",
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
        self.assertEqual(session.host, "Browser")
        self.assertEqual(session.startup_mode, "browser")
        self.assertEqual(session.initial_command_mode, "browser")
        self.assertEqual(session.initial_command, "http://localhost:5173")
        self.assertFalse(session.use_wsl)
        self.assertFalse(session.use_powershell)
        self.assertEqual(session.status, api.SessionStatus.CONNECTED)

    def test_create_sessions_rejects_browser_mode_with_invalid_url(self):
        sessions_payload = {
            "connection_mode": "wsl",
            "sessions": [
                {
                    "directory": self.temp_dir.name,
                    "title": "Preview",
                    "initial_command": "file:///tmp/index.html",
                    "startup_mode": "browser",
                }
            ],
        }

        response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("http:// and https://", response.get_json()["error"])

    def test_create_sessions_browser_mode_keeps_every_tab(self):
        """A saved multi-tab browser pane relaunches with its whole strip."""
        sessions_payload = {
            "connection_mode": "wsl",
            "sessions": [
                {
                    "directory": self.temp_dir.name,
                    "title": "Preview",
                    "initial_command": "http://127.0.0.1:3000",
                    "startup_mode": "browser",
                    "browser_tabs": [
                        "http://127.0.0.1:3000",
                        "http://127.0.0.1:5050/",
                        "localhost:8080",
                    ],
                    "browser_active_tab": 1,
                }
            ],
        }

        response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.startup_mode, "browser")
        self.assertEqual(
            session.browser_tabs,
            ["http://127.0.0.1:3000", "http://127.0.0.1:5050/", "http://localhost:8080"],
        )
        self.assertEqual(session.browser_active_tab, 1)
        # The active tab's URL stays mirrored into the single-URL contract.
        self.assertEqual(session.initial_command, "http://127.0.0.1:5050/")

    def test_create_sessions_browser_mode_drops_unusable_tabs(self):
        """Bad entries are dropped, not launched, and cannot shift the active tab."""
        sessions_payload = {
            "connection_mode": "wsl",
            "sessions": [
                {
                    "directory": self.temp_dir.name,
                    "title": "Preview",
                    "startup_mode": "browser",
                    "browser_tabs": ["file:///tmp/a.html", "http://127.0.0.1:4000", ""],
                    "browser_active_tab": 9,
                }
            ],
        }

        response = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(response.status_code, 201)
        session = api.session_manager.get_all_sessions()[0]
        self.assertEqual(session.browser_tabs, ["http://127.0.0.1:4000"])
        self.assertEqual(session.browser_active_tab, 0)

    def test_switch_browser_pane_replaces_whole_tab_strip(self):
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:3000",
            initial_command_mode="browser",
            browser_tabs=["http://127.0.0.1:3000"],
        )

        with patch.object(api, "_close_ssh_connection"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={
                    "startup_mode": "browser",
                    "tabs": ["http://127.0.0.1:3000", "http://127.0.0.1:5050/"],
                    "active_tab": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(
            updated.browser_tabs,
            ["http://127.0.0.1:3000", "http://127.0.0.1:5050/"],
        )
        self.assertEqual(updated.browser_active_tab, 1)
        self.assertEqual(updated.initial_command, "http://127.0.0.1:5050/")

    def test_switch_browser_pane_navigation_only_moves_active_tab(self):
        """A plain single-URL navigate edits the active tab and leaves siblings."""
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:5050/",
            initial_command_mode="browser",
            browser_tabs=["http://127.0.0.1:3000", "http://127.0.0.1:5050/"],
            browser_active_tab=1,
        )

        with patch.object(api, "_close_ssh_connection"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "browser", "url": "localhost:5173"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(
            updated.browser_tabs,
            ["http://127.0.0.1:3000", "http://localhost:5173"],
        )
        self.assertEqual(updated.browser_active_tab, 1)

    def test_switch_browser_navigation_merges_with_latest_tab_strip(self):
        """F6 — a concurrent strip update cannot be lost by URL navigation."""
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:5050/",
            initial_command_mode="browser",
            browser_tabs=["http://127.0.0.1:3000", "http://127.0.0.1:5050/"],
            browser_active_tab=1,
        )
        original_merge = api.session_manager.merge_browser_tabs

        def merge_after_concurrent_strip_update(session_id, **kwargs):
            api.session_manager.update_browser_tab_strip(
                session_id,
                browser_tabs=["http://127.0.0.1:6000", "http://127.0.0.1:7000"],
                browser_active_tab=0,
                initial_command="http://127.0.0.1:6000",
            )
            return original_merge(session_id, **kwargs)

        with patch.object(
            api.session_manager,
            "merge_browser_tabs",
            side_effect=merge_after_concurrent_strip_update,
        ), patch.object(api, "_close_ssh_connection"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={
                    "startup_mode": "browser",
                    "url": "localhost:5173",
                    "active_tab": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(
            updated.browser_tabs,
            ["http://127.0.0.1:6000", "http://localhost:5173"],
        )
        self.assertEqual(updated.browser_active_tab, 1)
        self.assertEqual(updated.initial_command, "http://localhost:5173")

    def test_switch_pane_away_from_browser_clears_tab_strip(self):
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:5050/",
            initial_command_mode="browser",
            browser_tabs=["http://127.0.0.1:3000", "http://127.0.0.1:5050/"],
            browser_active_tab=1,
        )

        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "terminal"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.browser_tabs, [])
        self.assertEqual(updated.browser_active_tab, 0)

    def test_stale_browser_tab_post_cannot_reenter_browser_mode(self):
        """F1 — only an explicit mode switch may move a terminal into browser mode."""
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:5050/",
            initial_command_mode="browser",
            browser_tabs=["http://127.0.0.1:5050/"],
        )

        with patch.object(api.socketio, "start_background_task"):
            terminal_response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "terminal"},
            )
        stale_response = self.client.post(
            f"/api/sessions/{session.session_id}/mode",
            json={
                "startup_mode": "browser",
                "tabs": ["http://127.0.0.1:3000"],
                "active_tab": 0,
            },
        )

        self.assertEqual(terminal_response.status_code, 200)
        self.assertEqual(stale_response.status_code, 409)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.browser_tabs, [])

        with patch.object(api, "_close_ssh_connection"):
            explicit_response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "browser", "url": "http://127.0.0.1:3000"},
            )
        self.assertEqual(explicit_response.status_code, 200)
        self.assertEqual(
            api.session_manager.get_session(session.session_id).startup_mode,
            "browser",
        )

    def test_normalize_startup_mode_allows_browser_only_for_local_repo(self):
        self.assertEqual(api._normalize_startup_mode("browser", "wsl"), "browser")
        self.assertEqual(api._normalize_startup_mode("browser", "ssh"), "terminal")

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

    def _create_local_terminal_session(self, directory: Path, **overrides):
        """One connected Local Repo terminal pane running cmd by default."""
        group = api.session_manager.create_group(
            name="Local",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
        )
        fields = {
            "host": "cmd",
            "directory": str(directory),
            "mode": "wsl",
            "startup_mode": "terminal",
        }
        fields.update(overrides)
        session = api.session_manager.create_session(group_id=group.group_id, **fields)
        api.session_manager.update_session_status(session.session_id, api.SessionStatus.CONNECTED)
        return session

    def test_switch_pane_shell_restarts_local_terminal_under_powershell(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(repo_dir, initial_command="claude")

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_resolve_live_terminal_cwd", return_value=None
        ), patch.object(api, "_close_ssh_connection") as close_connection, patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "powershell"},
            )

        self.assertEqual(response.status_code, 200)
        close_connection.assert_called_once_with(session.session_id, clear_buffer=True)
        start_task.assert_called_once_with(api._connect_session, session.session_id)
        updated = api.session_manager.get_session(session.session_id)
        self.assertTrue(updated.use_powershell)
        self.assertFalse(updated.use_wsl)
        self.assertEqual(updated.host, "PowerShell")
        self.assertEqual(updated.status, api.SessionStatus.PENDING)
        # The pane keeps its identity: same slot, startup command and mode, so
        # only the shell process is replaced.
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.initial_command, "claude")
        self.assertEqual(response.get_json()["use_powershell"], True)

    def test_switch_pane_shell_selects_named_wsl_distribution(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(repo_dir, use_powershell=True, host="PowerShell")

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_resolve_live_terminal_cwd", return_value=None
        ), patch.object(api, "_close_ssh_connection"), patch.object(
            api.socketio, "start_background_task"
        ):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "wsl", "distribution": "Ubuntu"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertTrue(updated.use_wsl)
        self.assertFalse(updated.use_powershell)
        self.assertEqual(updated.distribution, "Ubuntu")
        self.assertEqual(updated.host, "WSL (Ubuntu)")

    def test_switch_pane_shell_clears_distribution_for_windows_shells(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(
            repo_dir, use_wsl=True, distribution="Ubuntu", host="WSL (Ubuntu)"
        )

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_resolve_live_terminal_cwd", return_value=None
        ), patch.object(api, "_close_ssh_connection"), patch.object(
            api.socketio, "start_background_task"
        ):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "cmd", "distribution": "Ubuntu"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertFalse(updated.use_wsl)
        self.assertFalse(updated.use_powershell)
        self.assertEqual(updated.distribution, "")
        self.assertEqual(updated.host, "cmd")

    def test_switch_pane_shell_carries_live_working_directory(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        nested_dir = repo_dir / "src"
        nested_dir.mkdir(parents=True)
        session = self._create_local_terminal_session(repo_dir)

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_resolve_live_terminal_cwd", return_value=str(nested_dir)
        ), patch.object(api, "_close_ssh_connection"), patch.object(
            api.socketio, "start_background_task"
        ):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "wsl"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(Path(updated.directory), nested_dir)

    def test_switch_pane_shell_keeps_directory_when_probe_fails(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(repo_dir)

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_resolve_live_terminal_cwd", return_value="/home/dev/project"
        ), patch.object(api, "_close_ssh_connection"), patch.object(
            api.socketio, "start_background_task"
        ):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "powershell"},
            )

        self.assertEqual(response.status_code, 200)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(Path(updated.directory), repo_dir)

    def test_switch_pane_shell_reselecting_active_shell_does_not_restart(self):
        """Clicking the shell a pane already runs must not kill a live shell."""
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(
            repo_dir, use_wsl=True, distribution="Ubuntu", host="WSL (Ubuntu)"
        )

        with patch.object(api.os, "name", "nt"), patch.object(
            api, "_close_ssh_connection"
        ) as close_connection, patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "wsl", "distribution": "Ubuntu"},
            )

        self.assertEqual(response.status_code, 200)
        close_connection.assert_not_called()
        start_task.assert_not_called()
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.status, api.SessionStatus.CONNECTED)

    def test_switch_pane_shell_rejects_unknown_shell(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(repo_dir)

        with patch.object(api.os, "name", "nt"), patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "fish"},
            )

        self.assertEqual(response.status_code, 400)
        start_task.assert_not_called()
        self.assertIn("shell must be", response.get_json()["error"])

    def test_switch_pane_shell_rejects_ssh_session(self):
        group = api.session_manager.create_group(
            name="Remote",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/app",
            mode="ssh",
            startup_mode="terminal",
        )

        with patch.object(api.os, "name", "nt"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "powershell"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Local Repo", response.get_json()["error"])

    def test_switch_pane_shell_rejects_explorer_pane(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session_id = self._create_explorer_session(repo_dir)

        with patch.object(api.os, "name", "nt"):
            response = self.client.post(
                f"/api/sessions/{session_id}/shell",
                json={"shell": "powershell"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("terminal mode", response.get_json()["error"])

    def test_switch_pane_shell_rejects_non_windows_hosts(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session = self._create_local_terminal_session(repo_dir, host="Shell")

        with patch.object(api.os, "name", "posix"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell",
                json={"shell": "powershell"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Windows", response.get_json()["error"])

    def test_switch_pane_shell_missing_session_returns_404(self):
        response = self.client.post(
            "/api/sessions/does-not-exist/shell",
            json={"shell": "powershell"},
        )

        self.assertEqual(response.status_code, 404)

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

    def test_switch_terminal_pane_to_explorer_refreshes_live_cwd_outside_previous_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        outside_dir = Path(self.temp_dir.name) / "outside"
        outside_dir.mkdir()
        group = api.session_manager.create_group(
            name="Local",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="Shell",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="terminal",
            explorer_root_directory=str(repo_dir),
        )

        with patch.object(api, "_resolve_live_terminal_cwd", return_value=str(outside_dir)) as resolve_cwd, patch.object(
            api,
            "_close_ssh_connection",
        ):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "explorer", "refresh_cwd": True},
            )

        self.assertEqual(response.status_code, 200)
        resolve_cwd.assert_called_once_with(session.session_id, session)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(Path(updated.directory), outside_dir.resolve())
        self.assertEqual(Path(updated.explorer_root_directory), outside_dir.resolve())
        self.assertEqual(updated.startup_mode, "explorer")

    def test_switch_local_terminal_pane_to_browser_uses_default_url(self):
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
            host="Shell",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="terminal",
            use_wsl=True,
        )
        api.session_manager.update_session_status(session.session_id, api.SessionStatus.CONNECTED)

        with patch.object(api, "_close_ssh_connection") as close_connection:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "browser"},
            )

        self.assertEqual(response.status_code, 200)
        close_connection.assert_called_once_with(session.session_id, clear_buffer=True)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.host, "Browser")
        self.assertEqual(updated.startup_mode, "browser")
        self.assertEqual(updated.initial_command_mode, "browser")
        self.assertEqual(updated.initial_command, "http://127.0.0.1:3000")
        self.assertTrue(updated.use_wsl)
        self.assertEqual(updated.status, api.SessionStatus.CONNECTED)

    def test_switch_browser_pane_updates_url(self):
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:3000",
            initial_command_mode="browser",
        )

        with patch.object(api, "_close_ssh_connection") as close_connection:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "browser", "url": "localhost:5173"},
            )

        self.assertEqual(response.status_code, 200)
        close_connection.assert_called_once_with(session.session_id, clear_buffer=True)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "browser")
        self.assertEqual(updated.initial_command, "http://localhost:5173")
        self.assertEqual(updated.status, api.SessionStatus.CONNECTED)

    def test_switch_browser_pane_to_terminal_restarts_local_terminal(self):
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
            host="Browser",
            directory=str(repo_dir),
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:3000",
            initial_command_mode="browser",
            use_powershell=True,
        )

        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "terminal"},
            )

        self.assertEqual(response.status_code, 200)
        start_task.assert_called_once_with(api._connect_session, session.session_id)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.host, "PowerShell")
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.initial_command_mode, "command")
        self.assertEqual(updated.initial_command, "")
        self.assertEqual(updated.status, api.SessionStatus.PENDING)

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

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(client, fake_sftp)), patch.object(
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

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)), patch.object(
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

    def test_switch_ssh_terminal_to_explorer_refreshes_live_remote_cwd_outside_previous_root(self):
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
            explorer_root_directory="/srv/app",
        )
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/opt/tools": {"type": "directory"},
            }
        )

        with patch.object(api, "_resolve_live_terminal_cwd", return_value="/opt/tools") as resolve_cwd, patch.object(
            web_explorer,
            "_open_ssh_sftp",
            return_value=(MagicMock(), fake_sftp),
        ), patch.object(api, "_close_ssh_connection"):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "explorer", "refresh_cwd": True},
            )

        self.assertEqual(response.status_code, 200)
        resolve_cwd.assert_called_once_with(session.session_id, session)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.directory, "/opt/tools")
        self.assertEqual(updated.explorer_root_directory, "/opt/tools")
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

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)), patch.object(
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
        self.assertIn("git", payload)
        self.assertFalse(payload["git"]["available"])
        self.assertEqual(payload["entries"][1]["git"]["status"], "clean")

    def test_parse_git_porcelain_v2_status_fixture(self):
        raw_status = (
            b"# branch.oid abcdef1234567890\0"
            b"# branch.head main\0"
            b"# branch.ab +2 -1\0"
            b"1 M. N... 100644 100644 100644 old new src/app.py\0"
            b"1 .D N... 100644 100644 100644 old new deleted.txt\0"
            b"2 R. N... 100644 100644 100644 old new R100 new_name.py\0old_name.py\0"
            b"u UU N... 100644 100644 100644 100644 a b c d conflict.txt\0"
            b"? notes.txt\0"
            b"! ignored.log\0"
        )

        branch, statuses = web_explorer._parse_git_status_porcelain_v2(raw_status)

        self.assertEqual(branch["branch"], "main")
        self.assertEqual(branch["head"], "abcdef123456")
        self.assertEqual(branch["ahead"], 2)
        self.assertEqual(branch["behind"], 1)
        self.assertEqual(statuses["src/app.py"]["status"], "modified")
        self.assertEqual(statuses["deleted.txt"]["status"], "deleted")
        self.assertEqual(statuses["new_name.py"]["status"], "renamed")
        self.assertEqual(statuses["new_name.py"]["original_path"], "old_name.py")
        self.assertEqual(statuses["conflict.txt"]["status"], "conflicted")
        self.assertEqual(statuses["notes.txt"]["status"], "untracked")
        self.assertEqual(statuses["ignored.log"]["status"], "ignored")

    def test_explorer_entries_returns_git_metadata_for_local_repo(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        src_dir = repo_dir / "src"
        src_dir.mkdir(parents=True)
        readme = repo_dir / "README.md"
        app_file = src_dir / "app.py"
        obsolete_file = repo_dir / "obsolete.txt"
        readme.write_text("# Project\n", encoding="utf-8")
        app_file.write_text("print('v1')\n", encoding="utf-8")
        obsolete_file.write_text("remove me\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        readme.write_text("# Project\n\nchanged\n", encoding="utf-8")
        app_file.write_text("print('v2')\n", encoding="utf-8")
        added_file = repo_dir / "added.py"
        added_file.write_text("print('new')\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "added.py")
        obsolete_file.unlink()
        (repo_dir / "notes.txt").write_text("untracked\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        entries_response = self.client.get(f"/api/explorer/{session_id}/entries")

        self.assertEqual(entries_response.status_code, 200)
        payload = entries_response.get_json()
        entries = {entry["name"]: entry for entry in payload["entries"]}
        self.assertTrue(payload["git"]["available"])
        self.assertTrue(payload["git"]["dirty"])
        self.assertIsNotNone(payload["git"]["repo_root"])
        self.assertEqual(entries["README.md"]["git"]["status"], "modified")
        self.assertEqual(entries["added.py"]["git"]["status"], "added")
        self.assertEqual(entries["obsolete.txt"]["git"]["status"], "deleted")
        self.assertTrue(entries["obsolete.txt"]["deleted"])
        self.assertEqual(entries["notes.txt"]["git"]["status"], "untracked")
        self.assertEqual(entries["src"]["git"]["status"], "modified")
        self.assertTrue(entries["src"]["git"]["has_descendant_changes"])

    def test_explorer_git_diff_returns_bounded_local_file_diff(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        readme.write_text("# Project\n\nchanged\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        diff_response = self.client.get(
            f"/api/explorer/{session_id}/git/diff",
            query_string={"path": "README.md", "mode": "head"},
        )

        self.assertEqual(diff_response.status_code, 200)
        payload = diff_response.get_json()
        self.assertEqual(payload["path"], "README.md")
        self.assertEqual(payload["mode"], "head")
        self.assertIn("+changed", payload["diff"])
        self.assertFalse(payload["truncated"])

    def test_explorer_git_diff_returns_commit_file_diff(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        readme.write_text("# Project\n\nsecond\n", encoding="utf-8")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "second")
        commit = self._run_git(repo_dir, "rev-parse", "--short=12", "HEAD").stdout.decode().strip()
        session_id = self._create_explorer_session(repo_dir)

        diff_response = self.client.get(
            f"/api/explorer/{session_id}/git/diff",
            query_string={"path": "README.md", "mode": "commit", "commit": commit},
        )

        self.assertEqual(diff_response.status_code, 200)
        payload = diff_response.get_json()
        self.assertEqual(payload["mode"], "commit")
        self.assertIn("+second", payload["diff"])

    def test_explorer_git_repo_returns_changes_and_graph(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        readme.write_text("# Project\n\nchanged\n", encoding="utf-8")
        (repo_dir / "notes.txt").write_text("new\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        repo_response = self.client.get(f"/api/explorer/{session_id}/git/repo")

        self.assertEqual(repo_response.status_code, 200)
        payload = repo_response.get_json()
        self.assertTrue(payload["git"]["available"])
        changes = {change["path"]: change for change in payload["changes"]}
        self.assertEqual(changes["README.md"]["git"]["status"], "modified")
        self.assertEqual(changes["notes.txt"]["git"]["status"], "untracked")
        self.assertTrue(payload["commits"])
        self.assertIn("initial", payload["commits"][0]["line"])
        self.assertEqual(payload["commits"][0]["files"][0]["path"], "README.md")
        self.assertEqual(payload["commits"][0]["files"][0]["git"]["status"], "added")

    def test_explorer_git_repo_expands_untracked_directories_to_files(self):
        repo_dir = self._init_committed_repo()
        nested_dir = repo_dir / "new" / "nested"
        nested_dir.mkdir(parents=True)
        (nested_dir / "first.txt").write_text("first\n", encoding="utf-8")
        (repo_dir / "new" / "second.txt").write_text("second\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(f"/api/explorer/{session_id}/git/repo")

        self.assertEqual(response.status_code, 200)
        changes = {change["path"]: change for change in response.get_json()["changes"]}
        self.assertEqual(set(changes), {"new/nested/first.txt", "new/second.txt"})
        self.assertTrue(all(change["git"]["status"] == "untracked" for change in changes.values()))

    def test_explorer_search_requires_query(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session_id = self._create_explorer_session(repo_dir)

        missing = self.client.get(f"/api/explorer/{session_id}/search")
        empty = self.client.get(f"/api/explorer/{session_id}/search", query_string={"q": ""})

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(empty.status_code, 400)

    def test_explorer_search_rejects_scope_outside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/search",
            query_string={"q": "hello", "scope": ".."},
        )

        self.assertEqual(response.status_code, 400)

    def test_explorer_search_rejects_non_explorer_session(self):
        group = api.session_manager.create_group(
            name="Local",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="localhost",
            directory=str(Path(self.temp_dir.name)),
            mode="wsl",
            startup_mode="terminal",
        )

        response = self.client.get(
            f"/api/explorer/{session.session_id}/search",
            query_string={"q": "hello"},
        )

        self.assertEqual(response.status_code, 400)

    def test_explorer_search_walk_payload_shape(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "notes.txt").write_text("say hello\nhello again\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/search",
            query_string={"q": "hello"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        for key in (
            "query", "options", "engine", "files", "total_files",
            "total_matches", "truncated", "elapsed_ms", "error",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["query"], "hello")
        self.assertEqual(payload["engine"], "walk")
        self.assertEqual(payload["total_files"], 1)
        self.assertEqual(payload["total_matches"], 2)
        self.assertEqual(
            payload["truncated"],
            {"files": False, "matches": False, "deadline": False, "output": False},
        )
        entry = payload["files"][0]
        self.assertEqual(entry["path"], "notes.txt")
        self.assertEqual(entry["name"], "notes.txt")
        self.assertEqual(entry["match_count"], 2)
        self.assertFalse(entry["truncated"])
        match = entry["matches"][0]
        for key in ("line", "text", "text_offset", "ranges"):
            self.assertIn(key, match)
        self.assertEqual(match["line"], 1)
        self.assertEqual(match["text"], "say hello")
        self.assertEqual(match["ranges"], [[4, 9]])

    def test_explorer_search_uses_git_grep_inside_work_tree(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/search",
            query_string={"q": "hello"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["engine"], "git-grep")
        self.assertEqual([entry["path"] for entry in payload["files"]], ["app.py"])

    def test_explorer_search_invalid_regex_returns_400(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "notes.txt").write_text("hello\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/search",
            query_string={"q": "([", "regex": "1"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid regular expression", response.get_json()["error"])

    def test_explorer_search_remote_git_grep(self):
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
        fake_sftp = FakeSftp({"/srv/app": {"type": "directory"}})
        fake_client = FakeSshExecClient(
            [
                (0, b"/srv/app\ntrue\n", b""),
                (0, b"src/main.py\0" b"3\0" b"print('hello')\n", b""),
            ]
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fake_client, fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/search",
                query_string={"q": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["engine"], "git-grep")
        self.assertEqual(payload["total_matches"], 1)
        entry = payload["files"][0]
        self.assertEqual(entry["path"], "src/main.py")
        self.assertEqual(entry["matches"][0]["line"], 3)
        self.assertEqual(entry["matches"][0]["ranges"], [[7, 12]])

    def test_explorer_search_remote_grep_fallback_is_confined(self):
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
        fake_sftp = FakeSftp({"/srv/app": {"type": "directory"}})
        fake_client = FakeSshExecClient(
            [
                # Not a work tree → bounded `grep -rIn` fallback.
                (1, b"", b""),
                (
                    0,
                    b"/srv/app/notes.txt:1:say hello\n"
                    b"/etc/passwd:4:hello outside\n",
                    b"",
                ),
            ]
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fake_client, fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/search",
                query_string={"q": "hello"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["engine"], "remote-grep")
        # The /etc hit is outside the explorer root and never reaches the payload.
        self.assertEqual([entry["path"] for entry in payload["files"]], ["notes.txt"])
        self.assertEqual(payload["total_matches"], 1)


    def test_explorer_git_stage_and_unstage_roundtrip(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        readme.write_text("# Project\n\nchanged\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        stage_response = self.client.post(
            f"/api/explorer/{session_id}/git/stage",
            json={"path": "README.md"},
        )

        self.assertEqual(stage_response.status_code, 200)
        staged = {change["path"]: change for change in stage_response.get_json()["changes"]}
        self.assertEqual(staged["README.md"]["git"]["index_status"], "M")

        unstage_response = self.client.post(
            f"/api/explorer/{session_id}/git/unstage",
            json={"path": "README.md"},
        )

        self.assertEqual(unstage_response.status_code, 200)
        unstaged = {change["path"]: change for change in unstage_response.get_json()["changes"]}
        self.assertEqual(unstaged["README.md"]["git"]["index_status"], ".")
        self.assertEqual(unstaged["README.md"]["git"]["worktree_status"], "M")

    def test_explorer_git_commit_creates_commit(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        readme.write_text("# Project\n\nchanged\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "README.md")
        session_id = self._create_explorer_session(repo_dir)

        commit_response = self.client.post(
            f"/api/explorer/{session_id}/git/commit",
            json={"message": "second commit"},
        )

        self.assertEqual(commit_response.status_code, 200)
        payload = commit_response.get_json()
        self.assertFalse(payload["changes"])
        self.assertIn("second commit", payload["commits"][0]["line"])
        latest = self._run_git(repo_dir, "log", "-1", "--pretty=%s").stdout.decode().strip()
        self.assertEqual(latest, "second commit")

    def test_explorer_git_commit_requires_message(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/commit",
            json={"message": "   "},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.get_json()["error"].lower())

    def test_explorer_git_stage_rejects_path_outside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# Project\n", encoding="utf-8")
        outside_file = Path(self.temp_dir.name) / "outside.txt"
        outside_file.write_text("secret\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/stage",
            json={"path": "../outside.txt"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("inside the configured root", response.get_json()["error"])

    def _init_committed_repo(self, name: str = "repo") -> Path:
        repo_dir = Path(self.temp_dir.name) / name
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        return repo_dir

    def test_explorer_git_diff_distinguishes_worktree_and_staged(self):
        # ISSUE-2026-023: a partially staged file must expose its worktree hunks
        # and its staged hunks separately, never mixed.
        repo_dir = self._init_committed_repo()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\nSTAGED\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "README.md")
        readme.write_text("# Project\nSTAGED\nWORKTREE\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        worktree = self.client.get(
            f"/api/explorer/{session_id}/git/diff",
            query_string={"path": "README.md", "mode": "worktree"},
        )
        staged = self.client.get(
            f"/api/explorer/{session_id}/git/diff",
            query_string={"path": "README.md", "mode": "staged"},
        )

        self.assertEqual(worktree.status_code, 200)
        self.assertEqual(staged.status_code, 200)
        worktree_payload = worktree.get_json()
        staged_payload = staged.get_json()
        self.assertEqual(worktree_payload["mode"], "worktree")
        self.assertIn("+WORKTREE", worktree_payload["diff"])
        self.assertNotIn("+STAGED", worktree_payload["diff"])
        self.assertEqual(staged_payload["mode"], "staged")
        self.assertIn("+STAGED", staged_payload["diff"])
        self.assertNotIn("+WORKTREE", staged_payload["diff"])

    def test_explorer_git_revert_discards_worktree_changes(self):
        # ISSUE-2026-018: discard an unstaged tracked edit.
        repo_dir = self._init_committed_repo()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\n\nunwanted\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "README.md"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Project\n")
        paths = {change["path"] for change in response.get_json()["changes"]}
        self.assertNotIn("README.md", paths)

    def test_explorer_git_revert_preserves_staged_version(self):
        # ISSUE-2026-018: reverting a partially staged file keeps its staged copy
        # and only discards the later worktree edit.
        repo_dir = self._init_committed_repo()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\nSTAGED\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "README.md")
        readme.write_text("# Project\nSTAGED\nWORKTREE\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "README.md"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Project\nSTAGED\n")
        changes = {change["path"]: change for change in response.get_json()["changes"]}
        self.assertEqual(changes["README.md"]["git"]["index_status"], "M")
        self.assertEqual(changes["README.md"]["git"]["worktree_status"], ".")

    def test_explorer_git_revert_restores_deleted_tracked_file(self):
        repo_dir = self._init_committed_repo()
        readme = repo_dir / "README.md"
        readme.unlink()
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "README.md"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(readme.exists())
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Project\n")

    def test_explorer_git_revert_deletes_selected_untracked_file(self):
        repo_dir = self._init_committed_repo()
        untracked_dir = repo_dir / "new"
        untracked_dir.mkdir()
        untracked = untracked_dir / "scratch.txt"
        untracked.write_text("remove me\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "new/scratch.txt"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(untracked.exists())
        self.assertTrue(untracked_dir.exists())
        self.assertNotIn("new/scratch.txt", {
            change["path"] for change in response.get_json()["changes"]
        })

    def test_explorer_git_revert_rejects_untracked_directory(self):
        repo_dir = self._init_committed_repo()
        nested_dir = repo_dir / "new"
        nested_dir.mkdir()
        nested_file = nested_dir / "scratch.txt"
        nested_file.write_text("keep me\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "new"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("untracked directories", response.get_json()["error"].lower())
        self.assertTrue(nested_file.exists())

    def test_explorer_git_revert_rejects_staged_only_file(self):
        # Worktree already matches the index: nothing to discard, and staged
        # content must never be touched.
        repo_dir = self._init_committed_repo()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\nSTAGED\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "README.md")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "README.md"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no unstaged changes", response.get_json()["error"].lower())
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Project\nSTAGED\n")

    def test_explorer_git_revert_rejects_path_outside_root(self):
        repo_dir = self._init_committed_repo()
        outside_file = Path(self.temp_dir.name) / "outside.txt"
        outside_file.write_text("secret\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "../outside.txt"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("inside the configured root", response.get_json()["error"])
        self.assertTrue(outside_file.exists())

    def test_explorer_git_stage_all_stages_every_change(self):
        # Wave 3 / 1.b (ISSUE-2026-032): one action stages modified, deleted,
        # and untracked files alike.
        repo_dir = self._init_committed_repo()
        (repo_dir / "second.txt").write_text("second\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "second.txt")
        self._run_git(repo_dir, "commit", "-m", "second file")
        (repo_dir / "README.md").write_text("# Project\nchanged\n", encoding="utf-8")
        (repo_dir / "second.txt").unlink()
        (repo_dir / "new.txt").write_text("new\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(f"/api/explorer/{session_id}/git/stage-all", json={})

        self.assertEqual(response.status_code, 200)
        changes = {change["path"]: change for change in response.get_json()["changes"]}
        self.assertEqual(changes["README.md"]["git"]["index_status"], "M")
        self.assertEqual(changes["README.md"]["git"]["worktree_status"], ".")
        self.assertEqual(changes["second.txt"]["git"]["index_status"], "D")
        self.assertEqual(changes["new.txt"]["git"]["index_status"], "A")

    def test_explorer_git_stage_all_requires_a_repository(self):
        plain_dir = Path(self.temp_dir.name) / "plain"
        plain_dir.mkdir()
        (plain_dir / "file.txt").write_text("hello\n", encoding="utf-8")
        session_id = self._create_explorer_session(plain_dir)

        response = self.client.post(f"/api/explorer/{session_id}/git/stage-all", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("worktree", response.get_json()["error"].lower())

    def test_explorer_git_discard_all_restores_tracked_worktree_changes(self):
        # Wave 3 / 1.c (OD-1): bulk discard restores modified + deleted tracked
        # files while untracked files are left in place (never git clean).
        repo_dir = self._init_committed_repo()
        (repo_dir / "second.txt").write_text("second\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "second.txt")
        self._run_git(repo_dir, "commit", "-m", "second file")
        readme = repo_dir / "README.md"
        second = repo_dir / "second.txt"
        untracked = repo_dir / "scratch.txt"
        readme.write_text("# Project\nunwanted\n", encoding="utf-8")
        second.unlink()
        untracked.write_text("keep me\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(f"/api/explorer/{session_id}/git/discard-all", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Project\n")
        self.assertTrue(second.exists())
        self.assertEqual(second.read_text(encoding="utf-8"), "second\n")
        self.assertTrue(untracked.exists())
        self.assertEqual(untracked.read_text(encoding="utf-8"), "keep me\n")
        paths = {change["path"] for change in response.get_json()["changes"]}
        self.assertEqual(paths, {"scratch.txt"})

    def test_explorer_git_discard_all_preserves_staged_content(self):
        # OD-1: worktree-only restore — the staged copy survives the bulk discard.
        repo_dir = self._init_committed_repo()
        readme = repo_dir / "README.md"
        readme.write_text("# Project\nSTAGED\n", encoding="utf-8")
        self._run_git(repo_dir, "add", "README.md")
        readme.write_text("# Project\nSTAGED\nWORKTREE\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(f"/api/explorer/{session_id}/git/discard-all", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(readme.read_text(encoding="utf-8"), "# Project\nSTAGED\n")
        changes = {change["path"]: change for change in response.get_json()["changes"]}
        self.assertEqual(changes["README.md"]["git"]["index_status"], "M")
        self.assertEqual(changes["README.md"]["git"]["worktree_status"], ".")

    def test_explorer_git_discard_all_rejects_when_nothing_unstaged(self):
        # A clean-or-untracked-only worktree is a clear error, and the
        # untracked file must survive.
        repo_dir = self._init_committed_repo()
        untracked = repo_dir / "scratch.txt"
        untracked.write_text("keep me\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(f"/api/explorer/{session_id}/git/discard-all", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("no unstaged changes", response.get_json()["error"].lower())
        self.assertTrue(untracked.exists())

    def test_git_discardable_worktree_paths_filters_porcelain_records(self):
        # Parser unit test for the OD-1 safety envelope: only tracked,
        # non-conflicted worktree changes qualify; rename records must consume
        # their original-path token instead of treating it as a record.
        raw = "\0".join([
            " M modified.txt",
            "M  staged-only.txt",
            "MM both.txt",
            "?? untracked.txt",
            "UU conflicted.txt",
            "R  renamed-clean.txt", "old-name.txt",
            "RM renamed-dirty.txt", "old-dirty.txt",
            " D deleted.txt",
            "",
        ])
        self.assertEqual(
            web_explorer._git_discardable_worktree_paths(raw),
            ["modified.txt", "both.txt", "renamed-dirty.txt", "deleted.txt"],
        )

    def test_terminals_page_git_bulk_action_controls_are_present(self):
        # Wave 3 / 1.b + 1.c: Stage All and Discard All live in the Changes
        # header, disabled while busy or when there is nothing to act on, and
        # the irreversible bulk discard confirms through the in-page shell.
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("data-explorer-git-stage-all", html)
        self.assertIn("data-explorer-git-discard-all", html)
        self.assertIn("function explorerGitStageAll(index)", html)
        self.assertIn("performExplorerGitAction(index, 'stage-all', {})", html)
        self.assertIn("async function explorerGitDiscardAll(index)", html)
        self.assertIn("performExplorerGitAction(index, 'discard-all', {})", html)
        self.assertIn("(busy || !unstaged.length) ? 'disabled'", html)
        self.assertIn("(busy || !discardable.length) ? 'disabled'", html)
        self.assertIn("explorerGitCanBulkDiscard(file.git && file.git.status)", html)
        self.assertIn("title: 'Discard all changes?'", html)
        self.assertIn(".explorer-git-section-title", html)
        self.assertIn(".explorer-git-section-actions", html)

    def test_terminals_page_git_actions_refresh_tree_and_open_file(self):
        # Wave 3 / 1.a (ISSUE-2026-034): every worktree-mutating Git action
        # routes through the shared refresh; publish (remote-only) does not.
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("const EXPLORER_GIT_WORKTREE_ENDPOINTS = new Set([", html)
        self.assertIn("'stage', 'unstage', 'revert', 'commit', 'stage-all', 'discard-all',", html)
        self.assertIn("EXPLORER_GIT_WORKTREE_ENDPOINTS.has(endpoint)", html)
        self.assertIn("async function refreshExplorerAfterGitAction(index, actionPath)", html)
        refresh_fn = html[
            html.index("async function refreshExplorerAfterGitAction"):
            html.index("function explorerGitStageFile")
        ]
        self.assertIn("reloadExplorerTree(index);", refresh_fn)
        self.assertIn("pane._explorerDiffLoaded = false;", refresh_fn)
        self.assertIn("actionPath && actionPath !== pane._explorerFilePath", refresh_fn)
        self.assertIn("preserveScroll: true", refresh_fn)
        # Publish stays outside the mutating set and now confirms in-page
        # (Regression Guardrail 4: no window.confirm in WebView2).
        self.assertNotIn("'publish', 'stage'", html)
        self.assertIn("title: 'Publish branch?'", html)
        terminals_js = self.client.get("/static/js/terminals.js").get_data(as_text=True)
        self.assertNotIn("window.confirm(", terminals_js)

    def test_terminals_page_explorer_file_type_icons_are_present(self):
        # ISSUE-2026-024
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function explorerFileTypeIconHtml(path, language", html)
        self.assertIn("function explorerFileTypeCategory(path, language", html)
        self.assertIn("EXPLORER_FILE_ICON_CATEGORY_BY_LANGUAGE", html)
        self.assertIn("EXPLORER_FILE_ICON_GLYPHS", html)
        self.assertIn('class="explorer-icon file type-${category}" aria-hidden="true"', html)
        self.assertIn("|| EXPLORER_FILE_ICON_GLYPHS.doc", html)
        # Rendered in the tree, Git and directory renderers.
        self.assertIn("EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(entry.name || path)", html)
        self.assertIn("EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(name || entry.path)", html)
        self.assertIn("${explorerFileTypeIconHtml(path)}", html)
        # Icon precedes the Git file name.
        self.assertLess(
            html.index("${explorerFileTypeIconHtml(path)}"),
            html.index('class="explorer-diff-commit-file-path"'),
        )
        # Token-driven tints, no inline palette literals.
        self.assertIn(".explorer-icon.type-code { color: var(--explorer-icon-code); }", html)
        self.assertIn(".explorer-icon.type-doc { color: var(--explorer-file); }", html)
        self.assertIn("--explorer-icon-code:", html)
        self.assertIn("--explorer-icon-data:", html)

    def test_terminals_page_explorer_copy_path_menu_is_present(self):
        # ISSUE-2026-028
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("data-explorer-copy-path", html)
        self.assertIn("function wireExplorerCopyPathMenu(panel, index)", html)
        self.assertIn("function showExplorerContextMenu(x, y, items)", html)
        self.assertIn("function explorerJoinRootPath(root, relativePath)", html)
        self.assertIn("label: 'Copy path'", html)
        self.assertIn("label: 'Copy relative path'", html)
        self.assertIn("_copyText(absolutePath || relativePath)", html)
        self.assertIn("_copyText(relativePath)", html)
        self.assertIn("function dismissExplorerContextMenu()", html)
        self.assertIn("_explorerContextMenuKeydown", html)
        self.assertIn("event.key === 'ArrowDown'", html)
        self.assertIn("wireExplorerCopyPathMenu(panel, index);", html)
        self.assertIn("#explorer-ctx-menu", html)

    def test_terminals_page_explorer_git_rows_open_diff_view(self):
        # ISSUE-2026-023
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function explorerGitOpenFile(index, path, diffMode = 'worktree')", html)
        self.assertIn("openExplorerFile(index, path, { openDiff: true, diffMode: mode });", html)
        self.assertIn("data-explorer-git-diff-mode", html)
        self.assertIn("const diffMode = action === 'unstage' ? 'staged' : 'worktree';", html)
        self.assertIn("button.dataset.explorerGitDiffMode || 'worktree'", html)
        # Diff-mode is threaded through the shared open path.
        self.assertIn("pane._explorerDiffMode = requestedDiffMode;", html)
        self.assertIn("const diffMode = commit ? 'commit' : (pane?._explorerDiffMode || 'head');", html)
        # Commit-history rows keep their own diff path; action buttons stay isolated.
        self.assertIn("async function explorerGitOpenCommitDiff(index, path, commit)", html)
        self.assertIn("explorerGitStageFile(index, button.dataset.explorerGitStage || '');", html)

    def test_terminals_page_explorer_git_revert_controls_are_present(self):
        # ISSUE-2026-018
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("data-explorer-git-revert", html)
        self.assertIn("data-explorer-git-revert-status", html)
        self.assertIn("explorer-git-revert-btn", html)
        self.assertIn("function explorerGitCanRevert(status)", html)
        self.assertIn("['modified', 'deleted', 'renamed', 'untracked'].includes(status", html)
        self.assertIn("action === 'stage' && explorerGitCanRevert(status)", html)
        self.assertIn("async function explorerGitRevertFile(index, path, status = '')", html)
        self.assertIn("button.dataset.explorerGitRevertStatus || ''", html)
        self.assertIn("performExplorerGitAction(index, 'revert', { path })", html)
        # Irreversible action uses the in-page confirm shell, not window.confirm.
        self.assertIn("function openGenericConfirmModal(", html)
        self.assertIn('id="genericConfirmModal"', html)
        self.assertIn("title: untracked ? 'Delete untracked file?' : 'Discard changes?'", html)
        self.assertIn("Permanently delete the untracked file", html)
        self.assertIn(".explorer-git-revert-btn", html)

    def test_parse_git_graph_log_skips_connector_only_lines(self):
        commits = web_explorer._parse_git_graph_log(
            b"* a1b2c3d initial\n"
            b"|\\\n"
            b"| * b2c3d4e branch work\n"
            b"|/\n"
        )

        self.assertEqual([commit["hash"] for commit in commits], ["a1b2c3d", "b2c3d4e"])
        self.assertEqual([commit["subject"] for commit in commits], ["initial", "branch work"])

    def test_explorer_git_diff_rejects_invalid_mode_and_outside_root(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# Project\n", encoding="utf-8")
        outside_file = Path(self.temp_dir.name) / "outside.txt"
        outside_file.write_text("secret\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        invalid_mode_response = self.client.get(
            f"/api/explorer/{session_id}/git/diff",
            query_string={"path": "README.md", "mode": "bad"},
        )
        outside_response = self.client.get(
            f"/api/explorer/{session_id}/git/diff",
            query_string={"path": str(outside_file), "mode": "head"},
        )

        self.assertEqual(invalid_mode_response.status_code, 400)
        self.assertIn("Invalid Git diff mode", invalid_mode_response.get_json()["error"])
        self.assertEqual(outside_response.status_code, 400)
        self.assertIn("inside the configured root", outside_response.get_json()["error"])

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

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            entries_response = self.client.get(f"/api/explorer/{session.session_id}/entries")

        self.assertEqual(entries_response.status_code, 200)
        payload = entries_response.get_json()
        self.assertEqual(payload["root"], "/srv/app")
        self.assertEqual(payload["path"], "")
        self.assertEqual(payload["parent_path"], "")
        self.assertEqual([entry["name"] for entry in payload["entries"]], ["src", "README.md"])

    def test_explorer_entries_returns_git_metadata_for_ssh_repo(self):
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
        raw_status = (
            b"# branch.oid abcdef1234567890\0"
            b"# branch.head main\0"
            b"1 .M N... 100644 100644 100644 old new README.md\0"
            b"1 .M N... 100644 100644 100644 old new src/main.py\0"
            b"1 .D N... 100644 100644 100644 old new obsolete.txt\0"
            b"? notes.txt\0"
        )
        fake_client = FakeSshExecClient(
            [
                (0, b"/srv/app\ntrue\n", b""),
                (0, raw_status, b""),
            ]
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fake_client, fake_sftp)):
            entries_response = self.client.get(f"/api/explorer/{session.session_id}/entries")

        self.assertEqual(entries_response.status_code, 200)
        payload = entries_response.get_json()
        entries = {entry["name"]: entry for entry in payload["entries"]}
        self.assertTrue(payload["git"]["available"])
        self.assertTrue(payload["git"]["dirty"])
        self.assertEqual(payload["git"]["branch"], "main")
        self.assertEqual(entries["README.md"]["git"]["status"], "modified")
        self.assertEqual(entries["src"]["git"]["status"], "modified")
        self.assertTrue(entries["src"]["git"]["has_descendant_changes"])
        self.assertEqual(entries["obsolete.txt"]["git"]["status"], "deleted")
        self.assertTrue(entries["obsolete.txt"]["deleted"])

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

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
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

    # ── In-app editor: read metadata (docs/text_editor_2026-07-20.md) ──
    def test_explorer_file_returns_editor_metadata_for_complete_file(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        raw = b"print(1)\nprint(2)\n"
        file_path.write_bytes(raw)
        session_id = self._create_explorer_session(repo_dir)

        payload = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "app.py"}
        ).get_json()

        self.assertTrue(payload["editable"])
        self.assertIsNone(payload["edit_block_reason"])
        self.assertEqual(payload["revision"], web_explorer._explorer_file_revision(raw))
        self.assertTrue(payload["revision"].startswith("sha256:"))
        self.assertEqual(payload["line_ending"], "lf")
        self.assertFalse(payload["utf8_bom"])

    def test_explorer_file_reports_mixed_line_endings_as_non_editable(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "mix.py").write_bytes(b"a=1\r\nb=2\nc=3\n")
        session_id = self._create_explorer_session(repo_dir)

        payload = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "mix.py"}
        ).get_json()

        self.assertFalse(payload["editable"])
        self.assertEqual(payload["edit_block_reason"], "mixed_line_endings")
        self.assertEqual(payload["line_ending"], "mixed")

    def test_explorer_file_reports_truncated_file_as_non_editable(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "big.txt").write_bytes(b"x\n" * 10)
        session_id = self._create_explorer_session(repo_dir)

        with patch.object(web_explorer, "EXPLORER_FILE_PREVIEW_MAX_BYTES", 8):
            payload = self.client.get(
                f"/api/explorer/{session_id}/file", query_string={"path": "big.txt"}
            ).get_json()

        self.assertTrue(payload["truncated"])
        self.assertFalse(payload["editable"])
        self.assertEqual(payload["edit_block_reason"], "truncated")
        self.assertIsNone(payload["revision"])

    def test_explorer_file_rejects_complete_invalid_utf8_for_editing(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        # Valid ASCII in the 4 KiB binary sample, invalid UTF-8 byte afterwards.
        (repo_dir / "weird.txt").write_bytes(b"a" * 5000 + b"\xff\n")
        session_id = self._create_explorer_session(repo_dir)

        payload = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "weird.txt"}
        ).get_json()

        self.assertFalse(payload["editable"])
        self.assertEqual(payload["edit_block_reason"], "unsupported_format")

    def test_explorer_image_file_reports_unsupported_format_for_editing(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        session_id = self._create_explorer_session(repo_dir)

        payload = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "logo.png"}
        ).get_json()

        self.assertEqual(payload["preview_type"], "image")
        self.assertFalse(payload["editable"])
        self.assertEqual(payload["edit_block_reason"], "unsupported_format")
        self.assertIsNone(payload["revision"])

    # ── In-app editor: save round-trips ──
    def test_explorer_save_roundtrips_local_utf8_and_returns_payload(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        file_path.write_bytes(b"print(1)\n")
        session_id = self._create_explorer_session(repo_dir)
        revision = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "app.py"}
        ).get_json()["revision"]

        response = self.client.put(
            f"/api/explorer/{session_id}/file",
            json={"path": "app.py", "content": "print(9)\n", "base_revision": revision},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(file_path.read_bytes(), b"print(9)\n")
        self.assertNotEqual(payload["revision"], revision)
        self.assertEqual(payload["content"], "print(9)\n")
        self.assertTrue(payload["editable"])

    def test_explorer_save_preserves_crlf_cr_bom_and_final_newline(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        session_id = self._create_explorer_session(repo_dir)

        cases = {
            "crlf.py": (b"a=1\r\nb=2\r\n", "a=1\nb=2\nc=3\n", b"a=1\r\nb=2\r\nc=3\r\n"),
            "cr.py": (b"a=1\rb=2\r", "a=1\nb=2\nc=3\n", b"a=1\rb=2\rc=3\r"),
            "nonl.py": (b"a=1", "a=1\nb=2", b"a=1\nb=2"),
            "bom.py": (b"\xef\xbb\xbfx=1\n", "﻿x=9\n", b"\xef\xbb\xbfx=9\n"),
        }
        for name, (initial, edited, expected) in cases.items():
            with self.subTest(name=name):
                path = repo_dir / name
                path.write_bytes(initial)
                revision = self.client.get(
                    f"/api/explorer/{session_id}/file", query_string={"path": name}
                ).get_json()["revision"]
                response = self.client.put(
                    f"/api/explorer/{session_id}/file",
                    json={"path": name, "content": edited, "base_revision": revision},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(path.read_bytes(), expected)

    def test_explorer_save_unchanged_content_is_a_noop(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        file_path.write_bytes(b"print(1)\n")
        session_id = self._create_explorer_session(repo_dir)
        revision = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "app.py"}
        ).get_json()["revision"]

        with patch.object(web_explorer._LocalExplorerBackend, "replace_file") as replace:
            response = self.client.put(
                f"/api/explorer/{session_id}/file",
                json={"path": "app.py", "content": "print(1)\n", "base_revision": revision},
            )

        self.assertEqual(response.status_code, 200)
        replace.assert_not_called()
        self.assertEqual(file_path.read_bytes(), b"print(1)\n")

    def test_explorer_save_rejects_bad_json_field_types(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_bytes(b"x\n")
        session_id = self._create_explorer_session(repo_dir)

        for body in (
            [],
            {"path": "app.py", "content": "x\n"},
            {"path": "", "content": "x\n", "base_revision": "sha256:x"},
            {"path": "app.py", "content": 5, "base_revision": "sha256:x"},
            {"path": "app.py", "content": "x\n", "base_revision": ""},
        ):
            with self.subTest(body=body):
                response = self.client.put(
                    f"/api/explorer/{session_id}/file", json=body
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], "invalid_request")

    def test_explorer_save_rejects_traversal_and_unsupported(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_bytes(b"x\n")
        (repo_dir / "data.bin").write_bytes(b"\x00\x01")
        session_id = self._create_explorer_session(repo_dir)

        for path in ("../escape.py", "app.py/../..", "missing.py", "data.bin"):
            with self.subTest(path=path):
                response = self.client.put(
                    f"/api/explorer/{session_id}/file",
                    json={"path": path, "content": "x\n", "base_revision": "sha256:x"},
                )
                self.assertEqual(response.status_code, 400)

    def test_explorer_save_rejects_oversized_replacement(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        file_path.write_bytes(b"a\n")
        session_id = self._create_explorer_session(repo_dir)
        revision = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "app.py"}
        ).get_json()["revision"]

        with patch.object(web_explorer, "EXPLORER_FILE_PREVIEW_MAX_BYTES", 16):
            response = self.client.put(
                f"/api/explorer/{session_id}/file",
                json={"path": "app.py", "content": "z" * 100, "base_revision": revision},
            )

        self.assertEqual(response.status_code, 413)
        payload = response.get_json()
        self.assertEqual(payload["code"], "file_too_large")
        self.assertEqual(payload["max_bytes"], 16)
        self.assertEqual(file_path.read_bytes(), b"a\n")

    def test_explorer_save_stale_revision_conflicts_then_retry_succeeds(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        file_path.write_bytes(b"v1\n")
        session_id = self._create_explorer_session(repo_dir)
        stale_revision = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "app.py"}
        ).get_json()["revision"]

        # Something else changes the file, so the held revision is now stale.
        file_path.write_bytes(b"external\n")
        conflict = self.client.put(
            f"/api/explorer/{session_id}/file",
            json={"path": "app.py", "content": "mine\n", "base_revision": stale_revision},
        )
        self.assertEqual(conflict.status_code, 409)
        conflict_payload = conflict.get_json()
        self.assertEqual(conflict_payload["code"], "file_conflict")
        current_revision = conflict_payload["current_revision"]
        self.assertEqual(current_revision, web_explorer._explorer_file_revision(b"external\n"))

        # Retrying with the returned revision succeeds (overwrite).
        retry = self.client.put(
            f"/api/explorer/{session_id}/file",
            json={"path": "app.py", "content": "mine\n", "base_revision": current_revision},
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(file_path.read_bytes(), b"mine\n")

        # A second intervening change conflicts again.
        file_path.write_bytes(b"changed-again\n")
        again = self.client.put(
            f"/api/explorer/{session_id}/file",
            json={"path": "app.py", "content": "mine2\n", "base_revision": current_revision},
        )
        self.assertEqual(again.status_code, 409)

    def test_explorer_save_claim_serializes_and_releases_without_holding_lock(self):
        # The claim set blocks a concurrent GridVibe save for the same key while
        # never holding its lock during the (yielded) I/O window.
        with web_explorer._explorer_save_claim("s", "/p"):
            self.assertTrue(web_explorer._explorer_save_claims_lock.acquire(blocking=False))
            web_explorer._explorer_save_claims_lock.release()
            with self.assertRaises(web_explorer.ExplorerSaveInProgressError):
                with web_explorer._explorer_save_claim("s", "/p"):
                    pass
        # Released after the block, so the key is reusable.
        with web_explorer._explorer_save_claim("s", "/p"):
            pass

    def test_explorer_save_write_failure_preserves_original_and_cleans_temp(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        file_path.write_bytes(b"original\n")
        session_id = self._create_explorer_session(repo_dir)
        revision = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "app.py"}
        ).get_json()["revision"]

        with patch("web.explorer.os.replace", side_effect=OSError("disk full")):
            response = self.client.put(
                f"/api/explorer/{session_id}/file",
                json={"path": "app.py", "content": "new\n", "base_revision": revision},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["code"], "io_error")
        self.assertEqual(file_path.read_bytes(), b"original\n")
        self.assertEqual([p.name for p in repo_dir.glob(".gv-save-*")], [])

    @unittest.skipIf(sys.platform == "win32", "POSIX mode bits are not meaningful on Windows")
    def test_local_replace_file_preserves_mode_bits(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "app.py"
        file_path.write_bytes(b"x\n")
        os.chmod(file_path, 0o640)

        web_explorer._LocalExplorerBackend().replace_file(str(file_path), b"y\n")

        self.assertEqual(file_path.read_bytes(), b"y\n")
        self.assertEqual(stat.S_IMODE(os.stat(file_path).st_mode), 0o640)

    def test_sftp_replace_file_writes_temp_applies_mode_and_posix_renames(self):
        calls = []

        class _ModeAwareHandle:
            def __init__(self, mode):
                self.mode = mode
                self.closed = False

            def write(self, content):
                if not any(flag in self.mode for flag in ("w", "a", "+")):
                    raise OSError("File not open for writing")
                calls.append(("write", content))

            def flush(self):
                calls.append(("flush",))

            def close(self):
                self.closed = True

        class _RecordingSftp:
            def stat(self, path):
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)

            def open(self, path, mode="rb"):
                calls.append(("open", path, mode))
                return _ModeAwareHandle(mode)

            def chmod(self, path, mode):
                calls.append(("chmod", path, mode))

            def posix_rename(self, src, dst):
                calls.append(("posix_rename", src, dst))

            def remove(self, path):
                calls.append(("remove", path))

        backend = web_explorer._SftpExplorerBackend(sftp=_RecordingSftp())
        backend.replace_file("/srv/app/notes.txt", b"data\n")

        opens = [c for c in calls if c[0] == "open"]
        self.assertEqual(len(opens), 1)
        self.assertTrue(opens[0][1].startswith("/srv/app/.gv-save-"))
        self.assertEqual(opens[0][2], "x+b")
        self.assertIn(("write", b"data\n"), calls)
        self.assertIn(("chmod", opens[0][1], 0o644), calls)
        rename = next(c for c in calls if c[0] == "posix_rename")
        self.assertEqual(rename[1], opens[0][1])
        self.assertEqual(rename[2], "/srv/app/notes.txt")
        # A successful rename consumes the temp, so it is not also removed.
        self.assertNotIn("remove", [c[0] for c in calls])

    def test_explorer_save_roundtrips_remote_sftp_with_writable_exclusive_temp(self):
        class _RemoteWriteHandle(io.BytesIO):
            def __init__(self, sftp, path, mode):
                super().__init__()
                self.sftp = sftp
                self.path = path
                self.mode = mode

            def write(self, content):
                if not any(flag in self.mode for flag in ("w", "a", "+")):
                    raise OSError("File not open for writing")
                return super().write(content)

            def close(self):
                if not self.closed:
                    self.sftp.entries[self.path] = {
                        "type": "file",
                        "content": self.getvalue(),
                    }
                super().close()

        class _WritableSftp(FakeSftp):
            def __init__(self, entries):
                super().__init__(entries)
                self.open_modes = []
                self.renames = []

            def open(self, path, mode="rb"):
                normalized = self.normalize(path)
                self.open_modes.append((normalized, mode))
                if mode == "rb":
                    if normalized not in self.entries:
                        raise OSError("No such file")
                    return io.BytesIO(self.entries[normalized].get("content", b""))
                if "x" in mode and normalized in self.entries:
                    raise OSError("File exists")
                return _RemoteWriteHandle(self, normalized, mode)

            def chmod(self, path, mode):
                pass

            def posix_rename(self, source, destination):
                source_path = self.normalize(source)
                destination_path = self.normalize(destination)
                self.entries[destination_path] = self.entries.pop(source_path)
                self.renames.append((source_path, destination_path))

            def remove(self, path):
                self.entries.pop(self.normalize(path), None)

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
        fake_sftp = _WritableSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/notes.txt": {"type": "file", "content": b"hello\n"},
            }
        )
        fake_client = FakeSshExecClient([(1, b"", b"")])
        revision = web_explorer._explorer_file_revision(b"hello\n")

        with patch.object(
            web_explorer,
            "_open_ssh_sftp",
            return_value=(fake_client, fake_sftp),
        ):
            response = self.client.put(
                f"/api/explorer/{session.session_id}/file",
                json={
                    "path": "notes.txt",
                    "content": "updated\n",
                    "base_revision": revision,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "updated\n")
        self.assertEqual(fake_sftp.entries["/srv/app/notes.txt"]["content"], b"updated\n")
        exclusive_opens = [
            (path, mode) for path, mode in fake_sftp.open_modes if "x" in mode
        ]
        self.assertEqual(len(exclusive_opens), 1)
        self.assertEqual(exclusive_opens[0][1], "x+b")
        self.assertEqual(
            fake_sftp.renames[0][1],
            "/srv/app/notes.txt",
        )

    def test_sftp_replace_file_without_posix_rename_never_truncates_destination(self):
        opened_paths = []

        class _NoRenameSftp:
            def stat(self, path):
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)

            def open(self, path, mode="rb"):
                opened_paths.append(path)
                return MagicMock()

            def chmod(self, path, mode):
                pass

            def remove(self, path):
                pass

            # Deliberately no posix_rename attribute.

        backend = web_explorer._SftpExplorerBackend(sftp=_NoRenameSftp())
        with self.assertRaises(RuntimeError):
            backend.replace_file("/srv/app/notes.txt", b"data\n")

        # The destination itself is never opened (would truncate the original).
        self.assertNotIn("/srv/app/notes.txt", opened_paths)

    def test_explorer_save_rejects_cross_origin_put(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "app.py").write_bytes(b"x\n")
        session_id = self._create_explorer_session(repo_dir)

        with patch.object(
            web_app, "_allowed_write_origin_netlocs", return_value={"localhost:5050"}
        ):
            blocked = self.client.put(
                f"/api/explorer/{session_id}/file",
                json={"path": "app.py", "content": "y\n", "base_revision": "sha256:x"},
                headers={"Origin": "http://evil.example"},
            )
            self.assertEqual(blocked.status_code, 403)
            same_origin = self.client.put(
                f"/api/explorer/{session_id}/file",
                json={"path": "app.py", "content": "y\n", "base_revision": "sha256:x"},
                headers={"Origin": "http://localhost:5050"},
            )
            # Same-origin reaches the route (stale placeholder revision → 409).
            self.assertNotEqual(same_origin.status_code, 403)

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

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
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

    def test_explorer_file_returns_ssh_git_metadata(self):
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
        fake_client = FakeSshExecClient(
            [
                (0, b"/srv/app\ntrue\n", b""),
                (
                    0,
                    b"# branch.oid abcdef1234567890\0"
                    b"# branch.head main\0"
                    b"1 .M N... 100644 100644 100644 old new notes.txt\0",
                    b"",
                ),
            ]
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fake_client, fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/file",
                query_string={"path": "notes.txt"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["git_context"]["available"])
        self.assertEqual(payload["git_context"]["branch"], "main")
        self.assertEqual(payload["git"]["status"], "modified")

    def test_explorer_git_diff_returns_bounded_ssh_file_diff(self):
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
                "/srv/app/README.md": {"type": "file", "content": b"# Project\n"},
            }
        )
        fake_client = FakeSshExecClient(
            [
                (0, b"/srv/app\ntrue\n", b""),
                (0, b"# branch.oid abcdef1234567890\0# branch.head main\0", b""),
                (0, b"diff --git a/README.md b/README.md\n+changed\n", b""),
            ]
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fake_client, fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/git/diff",
                query_string={"path": "README.md", "mode": "head"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["path"], "README.md")
        self.assertEqual(payload["mode"], "head")
        self.assertIn("+changed", payload["diff"])
        self.assertFalse(payload["truncated"])
        # Unified backend payload shape (finding 6.1): remote diffs report
        # byte_count/line_count exactly like local diffs.
        self.assertEqual(payload["byte_count"], len(b"diff --git a/README.md b/README.md\n+changed\n"))
        self.assertEqual(payload["line_count"], 2)
        self.assertNotIn("raw_bytes", payload)
        self.assertIn("git -C /srv/app diff HEAD", fake_client.commands[-1][0])

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

    def test_explorer_markdown_preview_keeps_fenced_code_language(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "README.md"
        file_path.write_text(
            "```python\nprint(1)\n```\n\ninline `x` text\n",
            encoding="utf-8",
        )
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "README.md"},
        )

        self.assertEqual(file_response.status_code, 200)
        preview_html = file_response.get_json()["preview_html"]
        # Fenced blocks keep their language hint so the client can syntax-highlight.
        self.assertIn('<code class="language-python">', preview_html)
        # Inline code stays classless and is left as plain monospace.
        self.assertIn("inline <code>x</code> text", preview_html)

    def test_explorer_markdown_preview_marks_mermaid_fences_for_client_rendering(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "diagram.md").write_text(
            "```mermaid\nflowchart LR\n  A --> B\n```\n",
            encoding="utf-8",
        )
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "diagram.md"},
        )

        self.assertEqual(response.status_code, 200)
        preview_html = response.get_json()["preview_html"]
        self.assertIn('<code class="language-mermaid">', preview_html)
        self.assertIn("flowchart LR", preview_html)
        self.assertNotIn("<svg", preview_html)

    def test_explorer_markdown_preview_treats_raw_html_as_literal_text(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "README.md"
        file_path.write_text(
            "The feed ends at <img> before this text.\n\n"
            "![Markdown image](https://example.com/image.png)\n",
            encoding="utf-8",
        )
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "README.md"},
        )

        self.assertEqual(file_response.status_code, 200)
        preview_html = file_response.get_json()["preview_html"]
        self.assertIn("The feed ends at &lt;img&gt; before this text.", preview_html)
        self.assertIn(
            '<img alt="Markdown image" src="https://example.com/image.png">',
            preview_html,
        )

    def test_markdown_preview_renders_github_callouts(self):
        """ISSUE-2026-017: [!TYPE] blockquotes become semantic callout blocks."""
        cases = {
            "note": "Note",
            "tip": "Tip",
            "important": "Important",
            "warning": "Warning",
            "caution": "Caution",
        }
        for kind, label in cases.items():
            with self.subTest(kind=kind):
                html = web_explorer._render_markdown_preview(
                    f"> [!{kind.upper()}]\n> Body text for {kind}.\n"
                )
                self.assertIn(f'<div class="md-callout md-callout-{kind}">', html)
                # Accessible label + stroke-style icon in the title row.
                self.assertIn('<p class="md-callout-title">', html)
                self.assertIn(f'<span class="md-callout-label">{label}</span>', html)
                self.assertIn('class="md-callout-icon"', html)
                self.assertIn('stroke="currentColor"', html)
                # Body content is preserved; the raw marker text is consumed.
                self.assertIn(f"Body text for {kind}.", html)
                self.assertNotIn(f"[!{kind.upper()}]", html)
                # The blockquote wrapper is replaced, not kept alongside.
                self.assertNotIn("<blockquote>", html)

    def test_markdown_preview_leaves_plain_blockquote_untouched(self):
        """ISSUE-2026-017: only [!TYPE] blockquotes convert; quotes stay quotes."""
        # A separating paragraph keeps the two blockquotes distinct (adjacent
        # blockquotes otherwise merge into one in Python-Markdown).
        html = web_explorer._render_markdown_preview(
            "> Just an ordinary quote.\n\nMiddle paragraph.\n\n> [!NOTE]\n> A real note.\n"
        )
        self.assertIn("<blockquote>", html)
        self.assertIn("Just an ordinary quote.", html)
        self.assertIn('<div class="md-callout md-callout-note">', html)
        # An unknown admonition keyword is not treated as a callout.
        unknown = web_explorer._render_markdown_preview("> [!HINT]\n> Not supported.\n")
        self.assertNotIn("md-callout", unknown)
        self.assertIn("[!HINT]", unknown)

    def test_markdown_preview_callout_sanitizes_body_and_keeps_nested_content(self):
        """ISSUE-2026-017: callout bodies stay sanitized and keep rich content."""
        html = web_explorer._render_markdown_preview(
            "> [!WARNING]\n"
            "> <script>alert('xss')</script> **stay safe**\n"
            ">\n"
            "> - first\n"
            "> - second\n"
        )
        self.assertIn('<div class="md-callout md-callout-warning">', html)
        # Sanitization (bleach) still runs before augmentation.
        self.assertNotIn("<script", html)
        self.assertIn("<strong>stay safe</strong>", html)
        # Nested list inside the callout is preserved as its body.
        self.assertIn("<li>first</li>", html)
        self.assertIn("<li>second</li>", html)

    def test_explorer_file_endpoint_emits_callout_html(self):
        """ISSUE-2026-017: callouts reach the client through preview_html."""
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text(
            "# Doc\n\n> [!TIP]\n> Helpful hint.\n", encoding="utf-8"
        )
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "README.md"},
        )

        self.assertEqual(file_response.status_code, 200)
        preview_html = file_response.get_json()["preview_html"]
        self.assertIn('<div class="md-callout md-callout-tip">', preview_html)
        self.assertIn("Helpful hint.", preview_html)

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
        self.assertEqual(payload["language"], "text")

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

    def test_explorer_code_language_covers_common_workspace_text_files(self):
        cases = {
            "events.jsonl": "jsonl",
            "system.log": "log",
            "notes.txt": "text",
            "setup.bat": "batch",
            "run.cmd": "batch",
            ".env": "dotenv",
            ".env.local": "dotenv",
            ".gitignore": "gitignore",
            "settings.example": "config",
            "app.conf": "config",
            "build.spec": "python",
            "Dockerfile": "dockerfile",
            "Makefile": "makefile",
        }
        for path, expected_language in cases.items():
            with self.subTest(path=path):
                self.assertEqual(web_explorer._explorer_code_language(path), expected_language)

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

    def test_trim_tail_preview_to_boundary_variants(self):
        # A partial leading line is dropped up to (and including) the first newline.
        self.assertEqual(
            web_explorer._trim_tail_preview_to_boundary(b"rtial line\ncomplete line\n"),
            b"complete line\n",
        )
        # A window whose only newline is the trailing byte keeps its content.
        self.assertEqual(
            web_explorer._trim_tail_preview_to_boundary(b"abc\n"),
            b"abc\n",
        )
        # With no usable newline, leading UTF-8 continuation bytes are skipped so
        # decoding never starts mid-character.
        self.assertEqual(
            web_explorer._trim_tail_preview_to_boundary(b"\xa9\xa9rest of line"),
            b"rest of line",
        )

    def test_explorer_log_preview_retains_tail_and_range_metadata(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        line_bytes = 100
        count = (api.EXPLORER_FILE_PREVIEW_MAX_BYTES // line_bytes) + 500

        def make_line(i):
            return f"L{i:08d}-" + ("x" * 89) + "\n"

        self.assertEqual(len(make_line(0).encode("utf-8")), line_bytes)
        body = "".join(make_line(i) for i in range(count))
        file_path = repo_dir / "app.log"
        file_path.write_text(body, encoding="utf-8")
        total_size = file_path.stat().st_size
        self.assertGreater(total_size, api.EXPLORER_FILE_PREVIEW_MAX_BYTES)
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "app.log"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        content = payload["content"]
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["preview_mode"], "tail")
        # Newest lines are retained; the oldest are discarded.
        self.assertIn(make_line(count - 1).strip(), content)
        self.assertNotIn("L00000000-", content)
        # The tail starts at a clean line boundary, never mid-line.
        self.assertTrue(content.startswith("L"))
        # Range metadata is self-consistent and pins the retained window to the end.
        self.assertEqual(payload["total_size"], total_size)
        self.assertEqual(payload["preview_end_byte"], total_size)
        self.assertEqual(
            payload["preview_start_byte"],
            total_size - len(content.encode("utf-8")),
        )
        self.assertGreater(payload["preview_start_byte"], 0)
        self.assertLessEqual(len(content.encode("utf-8")), api.EXPLORER_FILE_PREVIEW_MAX_BYTES)

    def test_explorer_non_log_preview_retains_head(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "notes.txt"
        file_path.write_text(
            "HEADMARKER\n" + ("y" * api.EXPLORER_FILE_PREVIEW_MAX_BYTES) + "\nTAILMARKER\n",
            encoding="utf-8",
        )
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "notes.txt"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        content = payload["content"]
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["preview_mode"], "head")
        self.assertIn("HEADMARKER", content)
        self.assertNotIn("TAILMARKER", content)
        self.assertEqual(len(content), api.EXPLORER_FILE_PREVIEW_MAX_BYTES)
        self.assertEqual(payload["preview_start_byte"], 0)
        self.assertEqual(payload["preview_end_byte"], api.EXPLORER_FILE_PREVIEW_MAX_BYTES)

    def test_explorer_preview_at_exact_limit_is_not_truncated(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        file_path = repo_dir / "exact.log"
        file_path.write_bytes(b"z" * api.EXPLORER_FILE_PREVIEW_MAX_BYTES)
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "exact.log"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["preview_mode"], "head")
        self.assertEqual(len(payload["content"]), api.EXPLORER_FILE_PREVIEW_MAX_BYTES)
        self.assertEqual(payload["total_size"], api.EXPLORER_FILE_PREVIEW_MAX_BYTES)

    def test_explorer_log_preview_tail_is_utf8_line_safe(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        # Two-byte characters shift byte alignment so the tail cut lands
        # mid-character on some lines; the line-boundary trim must still yield a
        # cleanly decodable preview with no replacement characters.
        def make_line(i):
            return f"café-{i:06d}-" + ("µ" * 40) + "\n"

        count = (api.EXPLORER_FILE_PREVIEW_MAX_BYTES // len(make_line(0).encode("utf-8"))) + 500
        body = "".join(make_line(i) for i in range(count))
        file_path = repo_dir / "unicode.log"
        # Preserve LF endings on every platform so the fixed-size binary sample
        # ends mid-character and exercises the same boundary as Ubuntu CI.
        file_path.write_bytes(body.encode("utf-8"))
        self.assertGreater(file_path.stat().st_size, api.EXPLORER_FILE_PREVIEW_MAX_BYTES)
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "unicode.log"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        content = payload["content"]
        self.assertEqual(payload["preview_mode"], "tail")
        self.assertNotIn("�", content)
        self.assertTrue(content.startswith("café-"))
        self.assertIn(make_line(count - 1).strip(), content)

    def test_explorer_remote_log_preview_retains_tail(self):
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
        line_bytes = 100

        def make_line(i):
            return f"R{i:08d}-" + ("x" * 89) + "\n"

        self.assertEqual(len(make_line(0).encode("utf-8")), line_bytes)
        count = (api.EXPLORER_FILE_PREVIEW_MAX_BYTES // line_bytes) + 500
        body = "".join(make_line(i) for i in range(count)).encode("utf-8")
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/remote.log": {"type": "file", "content": body},
            }
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/file",
                query_string={"path": "remote.log"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        content = payload["content"]
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["preview_mode"], "tail")
        self.assertIn(make_line(count - 1).strip(), content)
        self.assertNotIn("R00000000-", content)
        self.assertEqual(payload["total_size"], len(body))
        self.assertEqual(payload["preview_end_byte"], len(body))

    def test_terminals_page_explorer_preview_tail_message(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function explorerPreviewTruncationLabel(data)", html)
        self.assertIn("data.preview_mode === 'tail' ? 'last' : 'first'", html)
        self.assertIn("`Showing the ${edge} ${retainedLabel} of ${totalLabel}`", html)
        self.assertIn("const truncationLabel = explorerPreviewTruncationLabel(data);", html)

    def test_explorer_file_rejects_binary_content(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "image.log").write_bytes(b"abc\x00def")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "image.log"},
        )

        self.assertEqual(file_response.status_code, 400)
        self.assertIn("binary", file_response.get_json()["error"])

    def test_explorer_file_rejects_non_utf8_binary_content_in_known_format(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "events.log").write_bytes(b"\xff\xfe\xfd\xfc" * 64)
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "events.log"},
        )

        self.assertEqual(file_response.status_code, 400)
        self.assertIn("binary", file_response.get_json()["error"])

    def test_explorer_binary_detection_rejects_incomplete_utf8_at_content_end(self):
        self.assertTrue(web_explorer._explorer_content_looks_binary(b"valid text\xc2"))

    def test_explorer_binary_detection_allows_multibyte_char_crossing_sample_boundary(self):
        """Wave 2 / 2.c (ISSUE-2026-035): a truncated sample may end mid-character."""
        # Byte 4,095 is 0xE2, the first byte of the valid 3-byte sequence for
        # U+2500 (─); the remaining bytes fall just past the 4,096-byte sample.
        content = b"a" * 4095 + "─".encode("utf-8") + b"\n"
        self.assertGreater(len(content), web_explorer.EXPLORER_BINARY_SAMPLE_BYTES)
        content.decode("utf-8")  # The complete file is valid strict UTF-8.
        self.assertFalse(web_explorer._explorer_content_looks_binary(content))

    def test_explorer_binary_detection_rejects_invalid_utf8_inside_oversized_sample(self):
        """Wave 2 / 2.c (ISSUE-2026-035): only a *trailing* partial sequence is deferred."""
        content = b"a" * 100 + b"\xc0\xaf" + b"b" * 5000
        self.assertGreater(len(content), web_explorer.EXPLORER_BINARY_SAMPLE_BYTES)
        self.assertTrue(web_explorer._explorer_content_looks_binary(content))

    def test_explorer_file_accepts_utf8_split_across_sample_boundary(self):
        """Wave 2 / 2.c (ISSUE-2026-035): local endpoint serves the boundary file."""
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        content = b"a" * 4095 + "─".encode("utf-8") + b"\n"
        (repo_dir / "boundary.md").write_bytes(content)
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "boundary.md"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertEqual(payload["language"], "markdown")
        self.assertIn("─", payload["content"])

    def test_explorer_file_accepts_utf8_split_across_sample_boundary_remote(self):
        """Wave 2 / 2.c (ISSUE-2026-035): SFTP endpoint parity with the local path."""
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
        content = b"a" * 4095 + "─".encode("utf-8") + b"\n"
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/boundary.md": {"type": "file", "content": content},
            }
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/file",
                query_string={"path": "boundary.md"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("─", response.get_json()["content"])

    def test_explorer_go_workflow_files_are_editor_eligible(self):
        """Wave 2 / 2.b (OD-2): go.mod and peers resolve to a preview language."""
        self.assertEqual(web_explorer._explorer_editor_language("go.mod"), "go")
        self.assertEqual(web_explorer._explorer_editor_language("go.sum"), "text")
        self.assertEqual(web_explorer._explorer_editor_language("go.work"), "go")
        self.assertEqual(web_explorer._explorer_editor_language("go.work.sum"), "text")

    def test_explorer_file_serves_go_mod(self):
        """Wave 2 / 2.b (OD-2): GET on go.mod no longer 400s and resolves to go."""
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "go.mod"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertEqual(payload["language"], "go")
        self.assertIn("module example.com/demo", payload["content"])

    def test_explorer_preview_cap_is_10_mib(self):
        """Wave 2 / 4.a (OD-9): the 1 MiB cap is raised; a 1.5 MiB file is served whole."""
        self.assertEqual(
            web_explorer.EXPLORER_FILE_PREVIEW_MAX_BYTES,
            10 * 1024 * 1024,
        )
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        body = '{"event":"tick"}\n' * 90000  # ~1.71 MiB, above the old 1 MiB cap.
        (repo_dir / "events.jsonl").write_bytes(body.encode("utf-8"))
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "events.jsonl"},
        )

        self.assertEqual(file_response.status_code, 200)
        payload = file_response.get_json()
        self.assertFalse(payload["truncated"])
        self.assertEqual(payload["content"], body)
        self.assertEqual(payload["total_size"], len(body.encode("utf-8")))

    def test_explorer_file_rejects_unsupported_editor_format(self):
        repo_dir = Path(self.temp_dir.name) / "repo"
        repo_dir.mkdir()
        (repo_dir / "archive.bin").write_bytes(b"plain bytes without nul")
        session_id = self._create_explorer_session(repo_dir)

        file_response = self.client.get(
            f"/api/explorer/{session_id}/file",
            query_string={"path": "archive.bin"},
        )

        self.assertEqual(file_response.status_code, 400)
        self.assertIn("format is not supported", file_response.get_json()["error"])

    def test_explorer_file_rejects_unsupported_remote_editor_format(self):
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
                "/srv/app/archive.bin": {"type": "file", "content": b"plain bytes"},
            }
        )

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)):
            response = self.client.get(
                f"/api/explorer/{session.session_id}/file",
                query_string={"path": "archive.bin"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("format is not supported", response.get_json()["error"])

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

    def test_workspace_save_preserves_launcher_directories_and_connection_setup(self):
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "local-grid",
                "config": {
                    "connection_mode": "wsl",
                    "terminal_count": 2,
                    "layout": "horizontal",
                    "ssh": {"host": "", "username": "ubuntu", "port": 22, "default_dir": ""},
                    "wsl": {
                        "distribution": "Ubuntu",
                        "username": "saso",
                        "default_dir": "C:\\repos\\gridvibe",
                    },
                    "terminals": [
                        {
                            "title": "Shell",
                            "directory": "C:\\repos\\gridvibe",
                            "initial_command": "",
                            "startup_mode": "terminal",
                        },
                        {
                            "title": "Server",
                            "directory": "backend",
                            "initial_command": "python main.py",
                            "startup_mode": "terminal",
                        },
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "vertical",
                    "wsl": {
                        "distribution": "Changed",
                        "username": "changed",
                        "default_dir": "C:\\repos\\gridvibe\\src\\services",
                    },
                    "terminals": [
                        {
                            "title": "Changed title",
                            "directory": "src/services",
                            "startup_mode": "explorer",
                            "explorer_tree_open": True,
                            "explorer_git_open": True,
                        },
                        {
                            "title": "Changed server",
                            "directory": "C:\\other-repo",
                            "startup_mode": "browser",
                            "explorer_tree_open": True,
                            "explorer_git_open": True,
                        },
                    ],
                    "workspace_layout": {
                        "class_name": "layout-split-local",
                        "split_slot_rects": [
                            {"originSlot": 0, "x": 1, "y": 1, "w": 3, "h": 1},
                            {"originSlot": 1, "x": 4, "y": 1, "w": 1, "h": 1},
                        ],
                    },
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        config = response.get_json()["config"]
        self.assertEqual(config["connection_mode"], "wsl")
        self.assertEqual(config["wsl"]["default_dir"], "C:\\repos\\gridvibe")
        self.assertEqual(config["wsl"]["distribution"], "Ubuntu")
        self.assertEqual(config["wsl"]["username"], "saso")
        self.assertEqual(config["terminals"][0]["directory"], "C:\\repos\\gridvibe")
        self.assertEqual(config["terminals"][1]["directory"], "backend")
        self.assertEqual(config["terminals"][0]["title"], "Shell")
        self.assertEqual(config["terminals"][1]["title"], "Server")
        self.assertEqual(config["terminals"][0]["startup_mode"], "explorer")
        self.assertTrue(config["terminals"][0]["explorer_tree_open"])
        self.assertTrue(config["terminals"][0]["explorer_git_open"])
        self.assertEqual(config["terminals"][1]["startup_mode"], "browser")
        self.assertFalse(config["terminals"][1]["explorer_tree_open"])
        self.assertFalse(config["terminals"][1]["explorer_git_open"])
        self.assertEqual(config["terminals"][1]["initial_command"], api.DEFAULT_BROWSER_URL)
        self.assertEqual(config["layout"], "vertical")
        self.assertEqual(config["workspace_layout"]["split_slot_rects"][0]["w"], 3)

    def test_normalize_terminal_entries_bounds_explorer_open_tabs(self):
        """ISSUE-2026-015: normalize/de-dupe/reject unsafe persisted tab paths."""
        entries = [
            {
                "startup_mode": "explorer",
                "explorer_open_tabs": [
                    "docs/a.md",
                    "docs/a.md",          # duplicate dropped
                    "../secret.txt",      # traversal dropped
                    "C:/abs.txt",         # drive-absolute dropped
                    "sub\\b.md",          # backslashes normalized
                    "",                   # empty dropped
                ],
                "explorer_active_tab": "sub/b.md",
            }
        ]

        normalized = web_saved_sessions._normalize_terminal_entries(entries)

        self.assertEqual(normalized[0]["explorer_open_tabs"], ["docs/a.md", "sub/b.md"])
        self.assertEqual(normalized[0]["explorer_active_tab"], "sub/b.md")

    def test_normalize_terminal_entries_drops_active_tab_not_open(self):
        """An active tab that is not among the open tabs falls back to empty."""
        entries = [
            {
                "startup_mode": "explorer",
                "explorer_open_tabs": ["a.md"],
                "explorer_active_tab": "b.md",
            }
        ]

        normalized = web_saved_sessions._normalize_terminal_entries(entries)

        self.assertEqual(normalized[0]["explorer_active_tab"], "")

    def test_normalize_terminal_entries_caps_open_tab_count(self):
        entries = [
            {
                "startup_mode": "explorer",
                "explorer_open_tabs": [f"file{i}.md" for i in range(30)],
            }
        ]

        normalized = web_saved_sessions._normalize_terminal_entries(entries)

        self.assertEqual(
            len(normalized[0]["explorer_open_tabs"]),
            web_saved_sessions.EXPLORER_MAX_OPEN_TABS,
        )

    def test_normalize_terminal_entries_defaults_missing_explorer_tabs(self):
        """Backward compatibility: presets without the field normalize cleanly."""
        normalized = web_saved_sessions._normalize_terminal_entries(
            [{"startup_mode": "explorer"}]
        )

        self.assertEqual(normalized[0]["explorer_open_tabs"], [])
        self.assertEqual(normalized[0]["explorer_active_tab"], "")
        self.assertEqual(normalized[0]["explorer_tab_views"], {})
        self.assertEqual(normalized[0]["explorer_md_preset"], "")
        self.assertEqual(normalized[0]["explorer_md_font"], "")

    def test_normalize_terminal_entries_validates_tab_views_and_md_appearance(self):
        """2.f: per-tab view snapshots and Markdown appearance are allowlist-validated."""
        entries = [
            {
                "startup_mode": "explorer",
                "explorer_open_tabs": ["docs/a.md", "sub/b.md", "c.md", "d.md", "e.md"],
                "explorer_tab_views": {
                    "docs/a.md": {
                        "mode": "preview",
                        "scroll": 0.5,
                        "identity": "abc123",
                        "font_size": 18,
                        "wrap_preview": True,
                        "wrap_diff": False,
                        "folds": [9, 2, 9, 0, "4", True],
                        "fold_identity": "abc123",
                    },
                    "sub\\b.md": {
                        "mode": "diff",
                        "scroll": 7,
                        "identity": "x" * 100,
                        "font_size": 99,
                        "wrap_diff": 1,
                        "diff_mode": "staged",
                    },
                    "c.md": {"mode": "bogus", "scroll": 0.1, "identity": "ok"},
                    "e.md": {"font_size": 12, "wrap_preview": 0, "wrap_source": False},
                    "../escape.md": {"mode": "source", "scroll": 0.2, "identity": "ok"},
                    "not-open.md": {"mode": "source", "scroll": 0.2, "identity": "ok"},
                    "d.md": "not-a-dict",
                    "__preview__": {
                        "mode": "diff",
                        "scroll": 0.5,
                        "identity": "zz",
                        "diff_mode": "worktree",
                        "font_size": 16,
                        "wrap_preview": False,
                        "path": "docs\\a.md",
                        "dir": "../escape",
                        "folds": [3],
                        "fold_identity": "preview-hash",
                    },
                },
                "explorer_md_preset": "vscode",
                "explorer_md_font": "serif",
            },
            {
                "startup_mode": "explorer",
                "explorer_md_preset": "neon",
                "explorer_md_font": "wingdings",
            },
        ]

        normalized = web_saved_sessions._normalize_terminal_entries(entries)

        views = normalized[0]["explorer_tab_views"]
        self.assertEqual(
            views["docs/a.md"],
            {
                "mode": "preview",
                "scroll": 0.5,
                "identity": "abc123",
                "font_size": 18,
                "wrap_diff": False,
                "folds": [2, 4, 9],
                "fold_identity": "abc123",
            },
        )
        # Keys normalize like tab paths; out-of-range scroll fractions clamp to
        # [0, 1]; oversized identity tokens are dropped rather than restored;
        # font sizes clamp to the editor zoom bounds. Line wrapping is on by
        # default, so a truthy flag is the default and persists nothing.
        self.assertEqual(
            views["sub/b.md"],
            {
                "mode": "diff",
                "scroll": 1.0,
                "identity": "",
                "diff_mode": "staged",
                "font_size": 24,
            },
        )
        # A record may carry only a zoom (a zoomed tab whose view was never
        # captured), a falsy wrap flag normalizes to an explicit `False`
        # opt-out, and the reserved Preview key keeps its view, zoom, wrapping,
        # and own separated path — file and browsed directory.
        self.assertEqual(
            views["e.md"],
            {"font_size": 12, "wrap_source": False, "wrap_preview": False},
        )
        self.assertEqual(
            views["__preview__"],
            {
                "mode": "diff",
                "scroll": 0.5,
                "identity": "zz",
                "diff_mode": "worktree",
                "font_size": 16,
                "wrap_preview": False,
                "path": "docs/a.md",
                "folds": [3],
                "fold_identity": "preview-hash",
            },
        )
        # Unknown modes, escaping/unlisted paths, and non-dict records are dropped.
        self.assertEqual(set(views), {"docs/a.md", "sub/b.md", "e.md", "__preview__"})
        self.assertEqual(normalized[0]["explorer_md_preset"], "vscode")
        self.assertEqual(normalized[0]["explorer_md_font"], "serif")
        # Values outside the appearance allowlists fall back to unset.
        self.assertEqual(normalized[1]["explorer_md_preset"], "")
        self.assertEqual(normalized[1]["explorer_md_font"], "")

    def test_normalize_terminal_entries_accepts_terminal_markdown_fonts(self):
        expected = {
            "consolas",
            "cascadia-code",
            "jetbrains-mono",
            "courier-new",
        }

        for font in expected:
            with self.subTest(font=font):
                normalized = web_saved_sessions._normalize_terminal_entries(
                    [{"startup_mode": "explorer", "explorer_md_font": font}]
                )
                self.assertEqual(normalized[0]["explorer_md_font"], font)

    def test_workspace_save_round_trips_tab_views_and_md_appearance(self):
        """2.f / ISSUE-2026-033: view snapshots + appearance persist, gated to explorer panes."""
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "explorer-tab-views",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "vertical",
                    "ssh": {"host": "example.com", "username": "ubuntu", "port": 22, "default_dir": "/repo"},
                    "terminals": [
                        {"title": "Files", "directory": "repo", "startup_mode": "explorer"},
                        {"title": "Shell", "directory": "repo", "startup_mode": "terminal"},
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "vertical",
                    "terminals": [
                        {
                            "title": "Files",
                            "directory": "repo",
                            "startup_mode": "explorer",
                            "explorer_open_tabs": ["docs/a.md"],
                            "explorer_active_tab": "docs/a.md",
                            "explorer_tab_views": {
                                "docs/a.md": {
                                    "mode": "diff",
                                    "scroll": 0.25,
                                    "identity": "id1",
                                    "diff_mode": "staged",
                                    "font_size": 18,
                                }
                            },
                            "explorer_md_preset": "paper",
                            "explorer_md_font": "consolas",
                            "explorer_theme": "light",
                        },
                        {
                            "title": "Shell",
                            "directory": "repo",
                            "startup_mode": "terminal",
                            "explorer_tab_views": {
                                "x.md": {"mode": "source", "scroll": 0.1, "identity": "id2"}
                            },
                            "explorer_md_preset": "paper",
                            "explorer_md_font": "consolas",
                            "explorer_theme": "light",
                        },
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        config = response.get_json()["config"]
        self.assertEqual(
            config["terminals"][0]["explorer_tab_views"],
            {
                "docs/a.md": {
                    "mode": "diff",
                    "scroll": 0.25,
                    "identity": "id1",
                    "diff_mode": "staged",
                    "font_size": 18,
                }
            },
        )
        self.assertEqual(config["terminals"][0]["explorer_md_preset"], "paper")
        self.assertEqual(config["terminals"][0]["explorer_md_font"], "consolas")
        self.assertEqual(config["terminals"][0]["explorer_theme"], "light")
        # Non-explorer panes never carry tab views or a Markdown appearance.
        self.assertEqual(config["terminals"][1]["explorer_tab_views"], {})
        self.assertEqual(config["terminals"][1]["explorer_md_preset"], "")
        self.assertEqual(config["terminals"][1]["explorer_md_font"], "")
        # Theme is gated to explorer panes; a terminal pane falls back to dark.
        self.assertEqual(config["terminals"][1]["explorer_theme"], "dark")

    def test_workspace_save_round_trips_explorer_open_tabs(self):
        """ISSUE-2026-015: active-workspace save persists explorer tabs, gated to explorer panes."""
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "explorer-tabs",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "vertical",
                    "ssh": {"host": "example.com", "username": "ubuntu", "port": 22, "default_dir": "/repo"},
                    "terminals": [
                        {"title": "Files", "directory": "repo", "startup_mode": "explorer"},
                        {"title": "Shell", "directory": "repo", "startup_mode": "terminal"},
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "vertical",
                    "terminals": [
                        {
                            "title": "Files",
                            "directory": "repo",
                            "startup_mode": "explorer",
                            "explorer_open_tabs": ["docs/a.md", "docs/a.md", "../escape.md"],
                            "explorer_active_tab": "docs/a.md",
                        },
                        {
                            "title": "Shell",
                            "directory": "repo",
                            "startup_mode": "terminal",
                            "explorer_open_tabs": ["should-not-persist.md"],
                            "explorer_active_tab": "should-not-persist.md",
                        },
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        config = response.get_json()["config"]
        self.assertEqual(config["terminals"][0]["explorer_open_tabs"], ["docs/a.md"])
        self.assertEqual(config["terminals"][0]["explorer_active_tab"], "docs/a.md")
        # Non-explorer panes never carry file tabs.
        self.assertEqual(config["terminals"][1]["explorer_open_tabs"], [])
        self.assertEqual(config["terminals"][1]["explorer_active_tab"], "")

    def test_workspace_save_as_clones_source_directories_before_applying_modes(self):
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "source",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "ssh": {
                        "host": "example.com",
                        "username": "ubuntu",
                        "port": 2222,
                        "default_dir": "/srv/gridvibe",
                    },
                    "terminals": [
                        {
                            "title": "Shell",
                            "directory": "services/api",
                            "initial_command": "",
                            "startup_mode": "terminal",
                        }
                    ],
                },
            },
        ).get_json()

        copied = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "source copy",
                "source_saved_session_id": original["id"],
                "workspace_only": True,
                "activate": False,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "ssh": {"host": "wrong", "default_dir": "/srv/gridvibe/tmp"},
                    "terminals": [
                        {"directory": "tmp/navigation", "startup_mode": "explorer"}
                    ],
                },
            },
        )

        self.assertEqual(copied.status_code, 201)
        body = copied.get_json()
        self.assertNotEqual(body["id"], original["id"])
        self.assertEqual(body["config"]["ssh"]["host"], "example.com")
        self.assertEqual(body["config"]["ssh"]["port"], 2222)
        self.assertEqual(body["config"]["ssh"]["default_dir"], "/srv/gridvibe")
        self.assertEqual(body["config"]["terminals"][0]["directory"], "services/api")
        self.assertEqual(body["config"]["terminals"][0]["startup_mode"], "explorer")

    def test_workspace_save_preserves_running_agent_identity_and_command(self):
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "agents",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "horizontal",
                    "ssh": {
                        "host": "example.com",
                        "username": "ubuntu",
                        "port": 22,
                        "default_dir": "/srv/gridvibe",
                    },
                    "terminals": [
                        {"title": "Codex", "directory": "", "startup_mode": "terminal"},
                        {"title": "Claude", "directory": "backend", "startup_mode": "terminal"},
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "horizontal",
                    "ssh": {"host": "wrong", "default_dir": "/srv/gridvibe/tmp"},
                    "terminals": [
                        {
                            "directory": "tmp/navigation",
                            "startup_mode": "agent",
                            "initial_command_mode": "agent",
                            "agent_selection": "codex",
                            "initial_command": "codex",
                        },
                        {
                            "directory": "/other/repo",
                            "startup_mode": "agent",
                            "initial_command_mode": "agent",
                            "agent_selection": "other",
                            "custom_agent": "claude-code",
                            "initial_command": "claude-code",
                        },
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        terminals = response.get_json()["config"]["terminals"]
        self.assertEqual(terminals[0]["directory"], "")
        self.assertEqual(terminals[0]["startup_mode"], "agent")
        self.assertEqual(terminals[0]["initial_command_mode"], "agent")
        self.assertEqual(terminals[0]["agent_selection"], "codex")
        self.assertEqual(terminals[0]["initial_command"], "codex")
        self.assertEqual(terminals[1]["directory"], "backend")
        self.assertEqual(terminals[1]["agent_selection"], "other")
        self.assertEqual(terminals[1]["custom_agent"], "claude-code")
        self.assertEqual(terminals[1]["initial_command"], "claude-code")

        terminal_response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 2,
                    "layout": "horizontal",
                    "terminals": [
                        {"directory": "wrong", "startup_mode": "terminal"},
                        {"directory": "also-wrong", "startup_mode": "terminal"},
                    ],
                },
            },
        )

        self.assertEqual(terminal_response.status_code, 201)
        terminal_modes = terminal_response.get_json()["config"]["terminals"]
        self.assertEqual(terminal_modes[0]["directory"], "")
        self.assertEqual(terminal_modes[1]["directory"], "backend")
        for terminal in terminal_modes[:2]:
            self.assertEqual(terminal["startup_mode"], "terminal")
            self.assertEqual(terminal["initial_command_mode"], "command")
            self.assertEqual(terminal["agent_selection"], "")
            self.assertEqual(terminal["custom_agent"], "")
            self.assertEqual(terminal["initial_command"], "")

    def test_workspace_save_follows_a_live_local_shell_switch(self):
        """A pane restarted under another shell saves with the shell it runs now."""
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "local shells",
                "config": {
                    "connection_mode": "wsl",
                    "terminal_count": 3,
                    "layout": "split",
                    "wsl": {"distribution": "Ubuntu", "username": "", "default_dir": "C:/repo"},
                    "terminals": [
                        {
                            "title": "Agent",
                            "startup_mode": "agent",
                            "initial_command_mode": "agent",
                            "agent_selection": "codex",
                            "initial_command": "codex",
                            "use_wsl": True,
                            "distribution": "Ubuntu",
                        },
                        {"title": "Shell", "startup_mode": "terminal"},
                        {
                            "title": "Files",
                            "startup_mode": "explorer",
                            "use_wsl": True,
                            "distribution": "Ubuntu",
                        },
                    ],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "wsl",
                    "terminal_count": 3,
                    "layout": "split",
                    "terminals": [
                        # WSL agent pane restarted under PowerShell.
                        {
                            "startup_mode": "agent",
                            "initial_command_mode": "agent",
                            "agent_selection": "codex",
                            "initial_command": "codex",
                            "use_wsl": False,
                            "use_powershell": True,
                            "distribution": "",
                        },
                        # cmd terminal pane restarted under a named WSL distro.
                        {
                            "startup_mode": "terminal",
                            "use_wsl": True,
                            "use_powershell": False,
                            "distribution": "Debian",
                        },
                        # Explorer panes have no shell; configured flags stand.
                        {"startup_mode": "explorer", "use_wsl": False, "distribution": ""},
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        terminals = response.get_json()["config"]["terminals"]
        self.assertFalse(terminals[0]["use_wsl"])
        self.assertTrue(terminals[0]["use_powershell"])
        self.assertEqual(terminals[0]["distribution"], "")
        self.assertEqual(terminals[0]["initial_command"], "codex")
        self.assertTrue(terminals[1]["use_wsl"])
        self.assertFalse(terminals[1]["use_powershell"])
        self.assertEqual(terminals[1]["distribution"], "Debian")
        self.assertTrue(terminals[2]["use_wsl"])
        self.assertEqual(terminals[2]["distribution"], "Ubuntu")

    def test_workspace_save_keeps_ssh_panes_free_of_local_shell_flags(self):
        """An SSH preset never picks up local shell flags from a workspace save."""
        original = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "remote",
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "ssh": {"host": "example.com", "username": "ubuntu", "port": 22, "default_dir": "/repo"},
                    "terminals": [{"title": "Shell", "startup_mode": "terminal"}],
                },
            },
        ).get_json()

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "id": original["id"],
                "name": original["name"],
                "workspace_only": True,
                "config": {
                    "connection_mode": "ssh",
                    "terminal_count": 1,
                    "layout": "single",
                    "terminals": [
                        {"startup_mode": "terminal", "use_wsl": True, "distribution": "Ubuntu"}
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        terminal = response.get_json()["config"]["terminals"][0]
        self.assertFalse(terminal["use_wsl"])
        self.assertFalse(terminal["use_powershell"])
        self.assertEqual(terminal["distribution"], "")

    def test_save_as_updates_only_the_requesting_session_group_target(self):
        original_group = api.session_manager.create_group(
            name="GridVibe",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-original",
            saved_session_id="gridvibe",
        )
        version_group = api.session_manager.create_group(
            name="GridVibe copy",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-version-2",
            saved_session_id="gridvibe",
        )
        payload = {
            "name": "GridVibe version 2",
            "group_id": version_group.group_id,
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
                    "default_dir": "~",
                },
                "terminals": [
                    {"title": "Shell", "directory": "/srv/dev", "initial_command": ""},
                ],
            },
        }

        response = self.client.post("/api/saved-sessions", json=payload)

        self.assertEqual(response.status_code, 201)
        created = response.get_json()
        self.assertEqual(created["name"], "GridVibe version 2")
        self.assertEqual(created["group"]["group_id"], version_group.group_id)
        self.assertEqual(created["group"]["saved_session_id"], created["id"])
        self.assertEqual(
            api.session_manager.get_group(original_group.group_id).saved_session_id,
            "gridvibe",
        )
        self.assertEqual(
            api.session_manager.get_group(version_group.group_id).saved_session_id,
            created["id"],
        )

    def test_workspace_save_refreshes_live_view_used_by_launcher_reopen(self):
        group = api.session_manager.create_group(
            name="Files",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
            group_id="group-live-reopen",
        )
        session = api.session_manager.create_sessions(
            [
                {
                    "directory": "C:\\repo",
                    "title": "Files",
                    "startup_mode": "explorer",
                    "explorer_open_tabs": ["old.md"],
                    "explorer_active_tab": "old.md",
                    "explorer_theme": "dark",
                }
            ],
            group_id=group.group_id,
        )[0]
        workspace_layout = {
            "split_slot_rects": [
                {"originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 1}
            ],
            "split_column_weights": [1.5, 0.5],
            "split_row_weights": [1],
            "original_split_slot_count": 1,
        }

        response = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "Files",
                "group_id": group.group_id,
                "workspace_only": True,
                "config": {
                    "connection_mode": "wsl",
                    "terminal_count": 1,
                    "layout": "single",
                    "workspace_layout": workspace_layout,
                    "terminals": [
                        {
                            "session_id": session.session_id,
                            "title": "Files",
                            "directory": "C:\\repo",
                            "startup_mode": "explorer",
                            "explorer_tree_open": True,
                            "explorer_git_open": True,
                            "explorer_search_open": True,
                            "explorer_open_tabs": ["README.md"],
                            "explorer_active_tab": "README.md",
                            "explorer_tab_views": {
                                "README.md": {
                                    "mode": "preview",
                                    "scroll": 0.32,
                                    "font_size": 18,
                                }
                            },
                            "explorer_theme": "light",
                        }
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        # Live ids are only correlation data and must never enter saved presets.
        self.assertNotIn("session_id", payload["config"]["terminals"][0])
        reopened = api.session_manager.get_session(session.session_id)
        self.assertTrue(reopened.explorer_tree_open)
        self.assertTrue(reopened.explorer_git_open)
        self.assertTrue(reopened.explorer_search_open)
        self.assertEqual(reopened.explorer_open_tabs, ["README.md"])
        self.assertEqual(reopened.explorer_active_tab, "README.md")
        self.assertEqual(reopened.explorer_tab_views["README.md"]["mode"], "preview")
        self.assertEqual(reopened.explorer_tab_views["README.md"]["scroll"], 0.32)
        self.assertEqual(reopened.explorer_tab_views["README.md"]["font_size"], 18)
        self.assertEqual(reopened.explorer_theme, "light")
        self.assertEqual(
            api.session_manager.get_group(group.group_id).workspace_layout,
            payload["config"]["workspace_layout"],
        )
        saved_workspace = self.client.post(
            "/api/runtime-state/save",
            json={"workspace_id": "default", "active_group_id": group.group_id},
        )
        self.assertEqual(saved_workspace.status_code, 200)
        snapshot_session = saved_workspace.get_json()["groups"][0]["sessions"][0]
        self.assertEqual(snapshot_session["explorer_open_tabs"], ["README.md"])
        self.assertEqual(snapshot_session["explorer_tab_views"]["README.md"]["mode"], "preview")

    def test_save_as_without_activation_preserves_live_group_and_launcher_selection(self):
        active_payload = {
            "name": "GridVibe",
            "config": {
                "connection_mode": "ssh",
                "terminal_count": 1,
                "layout": "single",
                "ssh": {
                    "host": "10.0.0.10",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "/srv/gridvibe",
                },
                "wsl": {
                    "distribution": "",
                    "username": "",
                    "default_dir": "~",
                },
                "terminals": [
                    {"title": "Shell", "directory": "/srv/gridvibe", "initial_command": ""},
                ],
            },
        }
        active_saved = self.client.post("/api/saved-sessions", json=active_payload).get_json()
        group = api.session_manager.create_group(
            name="GridVibe",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-original",
            saved_session_id=active_saved["id"],
        )
        save_as_payload = {
            **active_payload,
            "name": "GridVibe version 2",
            "activate": False,
        }

        response = self.client.post("/api/saved-sessions", json=save_as_payload)

        self.assertEqual(response.status_code, 201)
        created = response.get_json()
        self.assertEqual(created["name"], "GridVibe version 2")
        self.assertFalse(created["activated"])
        self.assertEqual(created["last_session"], active_saved["id"])
        self.assertEqual(created["saved_session"], {"id": active_saved["id"], "name": "GridVibe"})
        self.assertIsNone(created["group"])
        self.assertEqual(
            api.session_manager.get_group(group.group_id).saved_session_id,
            active_saved["id"],
        )
        saved_state = json.loads(self.saved_sessions_path.read_text(encoding="utf-8"))
        self.assertEqual(saved_state["last_session"], active_saved["id"])
        self.assertEqual(len(saved_state["sessions"]), 2)

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

    def test_saved_sessions_roundtrip_preserves_browser_startup_metadata(self):
        payload = {
            "name": "browser-preset",
            "config": {
                "connection_mode": "wsl",
                "terminal_count": 1,
                "layout": "single",
                "ssh": {
                    "host": "",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "",
                },
                "wsl": {
                    "distribution": "",
                    "username": "",
                    "default_dir": self.temp_dir.name,
                },
                "terminals": [
                    {
                        "title": "Preview",
                        "directory": "",
                        "initial_command": "http://127.0.0.1:3000",
                        "initial_command_mode": "browser",
                        "startup_mode": "browser",
                    }
                ],
            },
        }

        created = self.client.post("/api/saved-sessions", json=payload)

        self.assertEqual(created.status_code, 201)
        terminal = created.get_json()["config"]["terminals"][0]
        self.assertEqual(terminal["initial_command"], "http://127.0.0.1:3000")
        self.assertEqual(terminal["initial_command_mode"], "browser")
        self.assertEqual(terminal["startup_mode"], "browser")

        fetched = self.client.get(f"/api/saved-sessions/{created.get_json()['id']}")
        self.assertEqual(fetched.status_code, 200)
        fetched_terminal = fetched.get_json()["config"]["terminals"][0]
        self.assertEqual(fetched_terminal["initial_command_mode"], "browser")
        self.assertEqual(fetched_terminal["startup_mode"], "browser")

    def test_saved_sessions_roundtrip_preserves_workspace_layout_geometry(self):
        payload = {
            "name": "split-workspace",
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
                    {"title": "Left", "directory": "/srv/dev", "initial_command": ""},
                    {"title": "Right", "directory": "/srv/dev", "initial_command": "htop"},
                ],
                "workspace_layout": {
                    "class_name": "layout-split-local",
                    "split_slot_rects": [
                        {"originSlot": 0, "x": 1, "y": 1, "w": 3, "h": 2},
                        {"originSlot": 1, "x": 4, "y": 1, "w": 1, "h": 2},
                    ],
                    "split_column_weights": [2, 2, 2, 1],
                    "split_row_weights": [1, 1],
                    "original_split_slot_count": 2,
                },
            },
        }

        created = self.client.post("/api/saved-sessions", json=payload)

        self.assertEqual(created.status_code, 201)
        layout = created.get_json()["config"]["workspace_layout"]
        self.assertEqual(layout["class_name"], "layout-split-local")
        self.assertEqual(layout["split_slot_rects"][0]["w"], 3)
        self.assertEqual(layout["split_column_weights"], [2.0, 2.0, 2.0, 1.0])

        fetched = self.client.get(f"/api/saved-sessions/{created.get_json()['id']}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(
            fetched.get_json()["config"]["workspace_layout"]["split_row_weights"],
            [1.0, 1.0],
        )

    def test_create_sessions_returns_workspace_layout_and_agent_metadata(self):
        sessions_payload = {
            "connection_mode": "ssh",
            "layout": "vertical",
            # A launch payload may still carry a surface_mode (older saved
            # snapshots do) — it is ignored in favour of the global setting.
            "surface_mode": "max",
            "workspace_layout": {
                "class_name": "layout-split-local",
                "split_slot_rects": [
                    {"originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 2},
                    {"originSlot": 1, "x": 3, "y": 1, "w": 2, "h": 2},
                ],
                "split_column_weights": [3, 1, 1, 3],
                "split_row_weights": [1, 1],
                "original_split_slot_count": 2,
            },
            "sessions": [
                {
                    "host": "10.0.0.20",
                    "directory": "/srv/dev",
                    "username": "ubuntu",
                    "title": "Agent",
                    "initial_command": "claude-code",
                    "initial_command_mode": "agent",
                    "startup_mode": "agent",
                    "agent_selection": "other",
                    "custom_agent": "claude-code",
                },
                {
                    "host": "10.0.0.20",
                    "directory": "/srv/dev",
                    "username": "ubuntu",
                    "title": "Shell",
                },
            ],
        }

        with patch.object(api.socketio, "start_background_task"):
            created = self.client.post("/api/sessions", json=sessions_payload)

        self.assertEqual(created.status_code, 201)
        data = created.get_json()
        self.assertEqual(data["workspace_layout"]["split_column_weights"], [3.0, 1.0, 1.0, 3.0])
        self.assertEqual(data["surface_mode"], api.runtime_config.app_surface_mode)
        self.assertNotIn("surface_mode", data["group"])
        self.assertEqual(data["sessions"][0]["initial_command_mode"], "agent")
        self.assertEqual(data["sessions"][0]["agent_selection"], "other")
        self.assertEqual(data["sessions"][0]["custom_agent"], "claude-code")

        listed = self.client.get(f"/api/sessions?group={data['group_id']}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["surface_mode"], api.runtime_config.app_surface_mode)
        self.assertEqual(
            listed.get_json()["workspace_layout"]["split_slot_rects"][1]["x"],
            3,
        )

    def test_surface_mode_setting_reaches_already_launched_groups(self):
        """A group must never pin the surface mode it launched with.

        It used to be copied into the SessionGroup at launch, so flipping the
        global App Setting left every open group (and every workspace restored
        from a snapshot of one) still reporting the old value — which the
        workspace page re-applied on its next /api/sessions refresh.
        """
        cfg = api.load_config()
        saved_workspace = json.loads(json.dumps(cfg.get("workspace", {})))
        try:
            with patch.object(api.socketio, "start_background_task"):
                created = self.client.post(
                    "/api/sessions",
                    json={
                        "connection_mode": "ssh",
                        "layout": "single",
                        "surface_mode": "normal",
                        "sessions": [{"host": "10.0.0.20", "directory": "/srv/dev"}],
                    },
                )
            self.assertEqual(created.status_code, 201)
            group_id = created.get_json()["group_id"]

            saved = self.client.post(
                "/api/app-config", json={"workspace": {"surface_mode": "max"}}
            )
            self.assertEqual(saved.status_code, 200)

            listed = self.client.get(f"/api/sessions?group={group_id}")
            self.assertEqual(listed.get_json()["surface_mode"], "max")

            # The restorable snapshot must not carry it either, or a restart
            # would replay the stale value into the relaunched group.
            slot = web_runtime_state.capture_workspace(api.session_manager)
            self.assertNotIn("surface_mode", slot["groups"][0])
        finally:
            cfg = api.load_config()
            cfg["workspace"] = saved_workspace
            api.save_config(cfg)
            api._refresh_runtime_config()

    def test_get_saved_session_returns_virtual_default_session(self):
        response = self.client.get(f"/api/saved-sessions/{api.DEFAULT_SAVED_SESSION_ID}")

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["id"], api.DEFAULT_SAVED_SESSION_ID)
        self.assertEqual(data["name"], api.DEFAULT_SAVED_SESSION_NAME)
        self.assertTrue(data["is_default"])
        self.assertEqual(data["config"]["connection_mode"], "ssh")
        self.assertEqual(data["config"]["terminal_count"], min(4, api.runtime_config.max_sessions))

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
            with patch.object(web_terminal_io, "_send_connection_input") as send_input:
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
            with patch.object(web_terminal_io, "_send_connection_input") as send_input:
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
            with patch.object(web_terminal_io, "_send_connection_input") as send_input:
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
            with patch.object(web_terminal_io, "_send_connection_input") as send_input:
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
            with patch.object(web_terminal_io, "_send_connection_input") as send_input:
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

    def test_terminal_cwd_probe_command_uses_wslpath_for_wsl_shell(self):
        command = api._terminal_cwd_probe_command(
            {"shell_kind": "wsl"},
            "__START__",
            "__END__",
        )

        self.assertIn("wslpath -w", command)
        self.assertIn('"$PWD"', command)
        self.assertIn("__START__", command)
        self.assertIn("__END__", command)

    def test_extract_terminal_cwd_from_buffer_uses_last_marker_payload(self):
        marker_start = "__GRIDVIBE_CWD_START__"
        marker_end = "__GRIDVIBE_CWD_END__"
        buffer = (
            f"printf '{marker_start}%s{marker_end}\\n' \"$PWD\"\r\n"
            f"{marker_start}$PWD{marker_end}\r\n"
            f"{marker_start}/srv/current{marker_end}\r\n"
        )

        self.assertEqual(
            api._extract_terminal_cwd_from_buffer(buffer, marker_start, marker_end),
            "/srv/current",
        )

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

        with patch.object(web_agents, "_find_wsl_executable", return_value="wsl.exe"):
            with patch.object(web_agents.subprocess, "run", return_value=completed) as run_command:
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

        with patch.object(web_agents, "_find_wsl_executable", return_value="wsl.exe"):
            with patch.object(web_agents.subprocess, "run", return_value=completed):
                response = self.client.get("/api/wsl-distros")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["available"])
        self.assertEqual(body["command"], "wsl -l -v")
        self.assertEqual(body["distros"][0]["name"], "Ubuntu-24.04")
        self.assertEqual(body["distros"][1]["state"], "Stopped")

    def test_build_local_command_uses_wsl_without_distribution_when_blank(self):
        session = SimpleNamespace(use_wsl=True, username="devuser")

        with patch.object(web_terminal_io, "_find_wsl_executable", return_value="wsl.exe"):
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

    def test_terminal_input_promotes_manually_started_codex_to_agent_metadata(self):
        group = api.session_manager.create_group(
            name="Manual agent",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/gridvibe",
            username="ubuntu",
            startup_mode="terminal",
        )
        connection = {}

        with patch.object(web_terminal_io, "_broadcast_session_status") as broadcast:
            api._track_terminal_agent_input(session.session_id, connection, "co")
            api._track_terminal_agent_input(session.session_id, connection, "dex --full-auto\r")

        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "agent")
        self.assertEqual(updated.initial_command_mode, "agent")
        self.assertEqual(updated.agent_selection, "codex")
        self.assertEqual(updated.custom_agent, "")
        self.assertEqual(updated.initial_command, "codex --full-auto")
        self.assertEqual(connection["_gridvibe_input_line"], "")
        broadcast.assert_called_once_with(session.session_id)

    def test_terminal_input_assigns_claude_to_unassigned_agent_mode(self):
        group = api.session_manager.create_group(
            name="Unassigned agent",
            connection_mode="wsl",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="Local",
            directory="C:\\repos\\gridvibe",
            username="",
            mode="wsl",
            startup_mode="agent",
            initial_command_mode="agent",
        )

        with patch.object(web_terminal_io, "_broadcast_session_status") as broadcast:
            api._track_terminal_agent_input(session.session_id, {}, "claudx\be\r")

        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "agent")
        self.assertEqual(updated.agent_selection, "claude")
        self.assertEqual(updated.initial_command, "claude")
        broadcast.assert_called_once_with(session.session_id)

    def test_terminal_input_returns_codex_to_terminal_mode_on_interrupt(self):
        group = api.session_manager.create_group(
            name="Codex",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/gridvibe",
            username="ubuntu",
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="codex",
            initial_command="codex",
        )

        with patch.object(web_terminal_io, "_broadcast_session_status") as broadcast:
            api._track_terminal_agent_input(session.session_id, {}, "\x03")

        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.initial_command_mode, "command")
        self.assertEqual(updated.agent_selection, "")
        self.assertEqual(updated.initial_command, "")
        broadcast.assert_called_once_with(session.session_id)

    def test_terminal_input_returns_claude_to_terminal_mode_on_exit_command(self):
        group = api.session_manager.create_group(
            name="Claude",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
        )
        session = api.session_manager.create_session(
            group_id=group.group_id,
            host="example.com",
            directory="/srv/gridvibe",
            username="ubuntu",
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
        )

        with patch.object(web_terminal_io, "_broadcast_session_status") as broadcast:
            api._track_terminal_agent_input(session.session_id, {}, "/exit\r")

        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.agent_selection, "")
        self.assertEqual(updated.initial_command, "")
        broadcast.assert_called_once_with(session.session_id)

    def test_agent_command_detection_ignores_non_agent_shell_commands(self):
        self.assertEqual(api._agent_from_terminal_command("sudo codex --help"), ("codex", "sudo codex --help"))
        self.assertEqual(api._agent_from_terminal_command("claude.exe"), ("claude", "claude.exe"))
        self.assertIsNone(api._agent_from_terminal_command("echo codex"))
        self.assertIsNone(api._agent_from_terminal_command("codex-helper"))

    def test_build_local_command_uses_wsl_startup_directory_when_available(self):
        session = SimpleNamespace(use_wsl=True, username="devuser")

        with patch.object(web_terminal_io, "_find_wsl_executable", return_value="wsl.exe"):
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
            web_terminal_io, "_find_wsl_executable", return_value="wsl.exe"
        ):
            with patch.object(web_terminal_io, "WinPtyProcess") as winpty:
                with patch.object(web_terminal_io, "_broadcast_session_status"):
                    with patch.object(web_terminal_io, "_stream_local_output"):
                        with patch.object(web_terminal_io, "_run_startup_sequence"):
                            with patch.object(web_terminal_io, "_drain_until_prompt"):
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
            with patch.object(web_terminal_io, "WinPtyProcess") as winpty:
                with patch.object(web_terminal_io, "_broadcast_session_status"):
                    with patch.object(web_terminal_io, "_stream_local_output"):
                        with patch.object(web_terminal_io, "_run_startup_sequence"):
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
            with patch.object(web_terminal_io, "WinPtyProcess", None):
                with patch.object(web_terminal_io, "_broadcast_session_status"):
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

    def test_split_session_rejects_explorer_and_browser_panes(self):
        api.session_manager.create_group(
            name="Explorer",
            connection_mode="wsl",
            layout="single",
            terminal_count=2,
            group_id="group-explorer",
        )
        explorer = api.session_manager.create_session(
            group_id="group-explorer",
            host="File Explorer",
            directory="/tmp/project",
            mode="wsl",
            startup_mode="explorer",
        )
        browser = api.session_manager.create_session(
            group_id="group-explorer",
            host="Browser",
            directory="/tmp/project",
            mode="wsl",
            startup_mode="browser",
            initial_command="http://127.0.0.1:3000",
            initial_command_mode="browser",
        )

        response = self.client.post(f"/api/sessions/{explorer.session_id}/split")
        browser_response = self.client.post(f"/api/sessions/{browser.session_id}/split")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(browser_response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Explorer and browser panes cannot be split"})
        self.assertEqual(browser_response.get_json(), {"error": "Explorer and browser panes cannot be split"})

    def test_split_session_rejects_group_at_max_sessions(self):
        api.session_manager.create_group(
            name="Full",
            connection_mode="ssh",
            layout="grid",
            terminal_count=api.runtime_config.max_sessions,
            group_id="group-full",
        )
        source = None
        for index in range(api.runtime_config.max_sessions):
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
            {"error": f"Maximum {api.runtime_config.max_sessions} sessions allowed"},
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

    def test_delete_session_updates_remaining_group_count(self):
        group = api.session_manager.create_group(
            name="Manual",
            connection_mode="ssh",
            layout="vertical",
            terminal_count=2,
            group_id="group-manual",
        )
        first = api.session_manager.create_session(
            group_id=group.group_id,
            host="10.0.0.12",
            directory="/tmp/project",
        )
        second = api.session_manager.create_session(
            group_id=group.group_id,
            host="10.0.0.13",
            directory="/tmp/project",
        )

        response = self.client.delete(f"/api/sessions/{first.session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(api.session_manager.get_session(first.session_id))
        self.assertIsNotNone(api.session_manager.get_session(second.session_id))
        self.assertEqual(api.session_manager.get_group(group.group_id).terminal_count, 1)

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

        api._cache_terminal_output(
            session.session_id,
            "boot"
            f"{api.WINDOWS_DEVICE_ATTRIBUTES_RESPONSE}"
            "\x1b]10;?\x07"
            "\x1b]11;?\x1b\\"
            "prompt",
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

        api._cache_terminal_output(session.session_id, "bootprompt")

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

        api._cache_terminal_output(session.session_id, "bootprompt")

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

        api._cache_terminal_output(session.session_id, "bootprompt")

        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)

        socket_client.emit("clear_terminal_buffer", {"session_id": session.session_id})

        self.assertEqual(api._get_buffered_terminal_output(session.session_id), "")

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

        with patch.object(api.runtime_config, "voice_enabled", True), patch.object(
            web_voice, "_ensure_whisper_model", return_value=mock_model
        ), patch.object(web_voice, "_pcm16le_to_float32", return_value="audio-array"):
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
        html = self._page_html(response)
        self.assertIn('[data-theme="light"]', html)
        self.assertIn("--bg:", html)
        self.assertIn("--accent:", html)

    def test_launcher_light_theme_overrides_hardcoded_element_backgrounds(self):
        response = self.client.get("/")
        html = self._page_html(response)
        light_selectors = [
            '[data-theme="light"] body',
            '[data-theme="light"] .header-badge',
            '[data-theme="light"] .count-btn',
            '[data-theme="light"] .field input',
            '[data-theme="light"] .t-row',
            '[data-theme="light"] .t-agent-select',
            '[data-theme="light"] .startup-mode-select',
            '[data-theme="light"] .check-field',
            '[data-theme="light"] .modal-card',
            '[data-theme="light"] .saved-session-item',
            '[data-theme="light"] .action-btn',
        ]
        for selector in light_selectors:
            self.assertIn(selector, html, f"Missing light theme override: {selector}")

    def test_launcher_page_includes_theme_toggle_control(self):
        response = self.client.get("/")
        html = self._page_html(response)
        self.assertIn('id="themeToggleBtnIndex"', html)
        self.assertIn("cycleTheme()", html)
        self.assertIn('id="themeControl"', html)
        self.assertIn('id="appSettingsBtn"', html)

    def test_launcher_page_includes_theme_js(self):
        response = self.client.get("/")
        html = self._page_html(response)
        self.assertIn("const THEME_STORAGE_KEY", html)
        self.assertIn("function normalizeThemePreference(", html)
        self.assertIn("function applyTheme(", html)
        self.assertIn("function syncNativeTheme(", html)
        self.assertIn("bridge.set_native_theme()", html)
        self.assertIn("function cycleTheme()", html)
        self.assertIn("prefers-color-scheme", html)

    def test_terminals_page_includes_theme_css_variables(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn('[data-theme="light"]', html)
        self.assertIn("--t-bg:", html)
        self.assertIn("--t-accent:", html)
        self.assertIn("--t-topbar:", html)
        self.assertIn('[data-theme="light"] .modal-shell', html)
        self.assertIn('[data-theme="light"] .modal-card', html)
        self.assertIn('[data-theme="light"] .saved-session-item', html)
        self.assertIn('[data-theme="light"] .saved-session-name', html)

    def test_terminals_page_saved_session_modal_covers_resize_overlay(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        modal_css = html[html.index(".modal-shell {"):html.index(".settings-window-btn {")]
        resize_css = html[html.index("#terminalResizeOverlay {"):html.index(".terminal-resize-handle {")]

        self.assertIn("z-index: 12000;", modal_css)
        self.assertIn("pointer-events: auto;", modal_css)
        self.assertIn("z-index: 60;", resize_css)

    def test_terminals_page_includes_theme_toggle_control(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn('id="themeToggleBtn"', html)
        self.assertIn("cycleTheme()", html)

    def test_terminals_page_includes_theme_js(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("const THEME_STORAGE_KEY", html)
        self.assertIn("function normalizeThemePreference(", html)
        self.assertIn("function applyTheme(", html)
        self.assertIn("function syncNativeTheme(", html)
        self.assertIn("bridge.set_native_theme()", html)
        self.assertIn("function cycleTheme()", html)
        self.assertIn("prefers-color-scheme", html)

    def test_terminals_page_uses_css_variables_for_structural_colors(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("var(--t-bg)", html)
        self.assertIn("var(--t-topbar)", html)
        self.assertIn("var(--t-text)", html)
        self.assertIn("var(--t-border)", html)

    def test_terminals_page_exposes_two_explicit_split_controls(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn(
            "const MAX_SPLIT_TERMINALS = Math.min(16, Number(MAX_SESSIONS || 16));",
            html,
        )
        # Two split buttons — one per axis — replace the single auto-axis button,
        # each wired to an explicit axis (no inferred guess).
        self.assertIn('data-terminal-split-v="${index}"', html)
        self.assertIn('data-terminal-split-h="${index}"', html)
        self.assertIn("splitTerminalPane(index, 'vertical')", html)
        self.assertIn("splitTerminalPane(index, 'horizontal')", html)
        self.assertIn("async function splitTerminalPane(index, axis)", html)
        # Each axis button enables independently from the per-axis candidates.
        self.assertIn("candidates.includes('vertical'),", html)
        self.assertIn("candidates.includes('horizontal'),", html)
        # The old single-axis auto-picker is gone.
        self.assertNotIn("function chooseSplitAxis", html)
        self.assertNotIn("grid?.classList.contains('layout-2-vertical')", html)

    def test_terminals_page_explains_axis_specific_split_minimums(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("function getSplitDisabledReason(axis)", html)
        self.assertIn(
            "Stacked split needs at least ${MIN_SPLIT_ROWS} rows below each terminal header",
            html,
        )
        self.assertIn(
            "Side-by-side split needs at least ${MIN_SPLIT_COLS} columns in each terminal",
            html,
        )
        self.assertIn("getSplitDisabledReason('vertical')", html)
        self.assertIn("getSplitDisabledReason('horizontal')", html)

    def test_terminals_page_base_layout_leaves_room_for_stacked_splits(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        # Base cells use a coarse-enough grid unit that a single pane can be
        # stacked several times before the integer `>= 2` guard bites, so
        # horizontal (stacked) splits are not capped at a single level.
        self.assertIn("const SPLIT_CELL_UNIT = 8;", html)
        self.assertIn("makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 2 * unit, h: unit })", html)
        self.assertIn("y: 1 + (slot.row - 1) * unit,", html)
        self.assertIn("h: slot.rowSpan * unit,", html)

    def test_terminals_page_folds_header_actions_into_overflow_menu(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        # Foldable actions live in their own group; the ⋯ toggle and the close
        # button stay outside it so close is always reachable.
        self.assertIn('class="terminal-actions" id="tactions-${i}"', html)
        self.assertIn('data-terminal-actions-more="${index}"', html)
        self.assertIn("function updatePaneHeaderLayout(index)", html)
        self.assertIn("function togglePaneActionsMenu(index)", html)
        self.assertIn("function closeAllPaneActionMenus(exceptIndex = -1)", html)
        # Collapse is driven by real header overflow, not a fixed breakpoint.
        self.assertIn("const overflowing = header.scrollWidth - header.clientWidth > 1;", html)
        self.assertIn("card.classList.toggle('actions-collapsed', overflowing);", html)
        # The close button is never inside the foldable actions group.
        actions_open = html.index('class="terminal-actions" id="tactions-${i}"')
        actions_close = html.index("${paneMoreButtonHtml(i)}", actions_open)
        self.assertNotIn('data-terminal-close="${i}"', html[actions_open:actions_close])

    def test_terminals_page_exposes_grid_resize_handles(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn('id="terminalResizeOverlay"', html)
        self.assertIn(".terminal-resize-handle", html)
        self.assertIn("let splitColumnWeights = null;", html)
        self.assertIn("let splitRowWeights = null;", html)
        self.assertIn("let activeGridResize = null;", html)
        self.assertIn("function ensureResizableSplitLayout()", html)
        self.assertIn("function renderResizeHandles()", html)

    def test_terminals_page_bounds_resize_handles_to_shared_edges(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("function getSharedGridEdgeSegments(rects, axis, lineIndex)", html)
        self.assertIn("function getSharedGridEdgeSegmentStyle(axis, segment, metrics)", html)
        self.assertIn("segments.forEach(segment => {", html)
        self.assertIn("handle.style.top = `${segmentStyle.top}px`;", html)
        self.assertIn("handle.style.height = `${segmentStyle.size}px`;", html)
        self.assertIn("handle.style.left = `${segmentStyle.left}px`;", html)
        self.assertIn("handle.style.width = `${segmentStyle.size}px`;", html)
        self.assertNotIn("handle.style.height = `${metrics.gridContentHeight}px`;", html)
        self.assertNotIn("handle.style.width = `${metrics.gridContentWidth}px`;", html)

    def test_terminals_page_resize_validation_enforces_minimums(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("const MIN_RESIZE_SURFACE_RATIO = 1 / 16;", html)
        self.assertIn(
            "const minimumSurface = metrics.columnTrackSpace * metrics.rowTrackSpace * MIN_RESIZE_SURFACE_RATIO;",
            html,
        )
        self.assertIn("surface.width * surface.height < minimumSurface", html)
        self.assertIn("Math.floor(availableWidth / cellWidth) >= MIN_SPLIT_COLS", html)
        self.assertIn("Math.floor(availableHeight / cellHeight) >= MIN_SPLIT_ROWS", html)

    def test_terminals_page_resize_drag_refits_and_forces_final_resize(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("window.addEventListener('pointermove', updateGridResize);", html)
        self.assertIn("window.addEventListener('pointerup', finishGridResize);", html)
        self.assertIn("function getResizeTrackGroups(axis, lineIndex)", html)
        self.assertIn("const beforeIndexes = resize.trackGroups.before;", html)
        self.assertIn("resize.affectedIndices.forEach(index => scheduleFit(index));", html)
        self.assertIn("redrawAttachedTerminals(affectedIndices, { forceResize: true });", html)
        self.assertIn("if (activeGridResize) {\n                event.preventDefault();", html)

    def test_terminals_page_cached_group_views_preserve_resize_weights(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("splitColumnWeights: hasLocalSplitLayout ? cloneSplitTrackWeights(splitColumnWeights) : null", html)
        self.assertIn("splitRowWeights: hasLocalSplitLayout ? cloneSplitTrackWeights(splitRowWeights) : null", html)
        self.assertIn("splitColumnWeights = cached.className === 'layout-split-local'", html)
        self.assertIn("splitRowWeights = cached.className === 'layout-split-local'", html)


class ExplorerGitRevisionTestCase(unittest.TestCase):
    """Explorer Git change listener (explorer_git_change_listener_plan_2026-07-30):
    the semantic revision helper and the GET /api/explorer/<id>/git/state route."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _run_git(self, repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        env = dict(os.environ)
        # Fixed author/committer dates make commit hashes deterministic, so two
        # repos built the same way share a HEAD (and therefore a revision).
        env.update(
            {
                "GIT_AUTHOR_DATE": "2026-07-30T00:00:00Z",
                "GIT_COMMITTER_DATE": "2026-07-30T00:00:00Z",
            }
        )
        return subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def _init_committed_repo(self, name: str = "repo") -> Path:
        repo_dir = Path(self.temp_dir.name) / name
        repo_dir.mkdir()
        (repo_dir / "README.md").write_text("# Project\n", encoding="utf-8")
        self._run_git(repo_dir, "init")
        self._run_git(repo_dir, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo_dir, "config", "user.name", "GridVibe Test")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "initial")
        return repo_dir

    def _create_explorer_session(self, root: Path) -> str:
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(root),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["sessions"][0]["session_id"]

    def _git_state(self, session_id: str, known: str = ""):
        query = f"?known={known}" if known else ""
        return self.client.get(f"/api/explorer/{session_id}/git/state{query}")

    # ── Revision helper ─────────────────────────────────────────────────────

    def _base_context(self) -> dict:
        return {
            "branch": "main",
            "head": "abcdef123456",
            "ahead": 0,
            "behind": 0,
        }

    def _change(self, path, status, index_status=" ", worktree_status=" ", original_path=None):
        git = {
            "status": status,
            "index_status": index_status,
            "worktree_status": worktree_status,
        }
        if original_path:
            git["original_path"] = original_path
        return {"path": path, "git": git}

    def test_revision_is_stable_for_identical_semantic_state(self):
        context = self._base_context()
        changes = [
            self._change("b.py", "modified", ".", "M"),
            self._change("a.py", "untracked", "?", "?"),
        ]
        first = web_explorer._git_repo_revision(context, changes)
        second = web_explorer._git_repo_revision(context, list(reversed(changes)))
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{16}$")

    def test_revision_changes_with_each_visible_state_delta(self):
        context = self._base_context()
        clean = web_explorer._git_repo_revision(context, [])
        variants = {
            "unstaged edit": (context, [self._change("a.py", "modified", ".", "M")]),
            "staged edit": (context, [self._change("a.py", "modified", "M", ".")]),
            "partially staged": (context, [self._change("a.py", "modified", "M", "M")]),
            "untracked file": (context, [self._change("new.py", "untracked", "?", "?")]),
            "deletion": (context, [self._change("a.py", "deleted", ".", "D")]),
            "rename": (
                context,
                [self._change("b.py", "renamed", "R", ".", original_path="a.py")],
            ),
            "conflict": (context, [self._change("a.py", "conflicted", "U", "U")]),
            "commit (HEAD)": ({**context, "head": "123456abcdef"}, []),
            "branch switch": ({**context, "branch": "feature"}, []),
            "ahead change": ({**context, "ahead": 2}, []),
            "behind change": ({**context, "behind": 1}, []),
        }
        seen = {clean}
        for label, (variant_context, variant_changes) in variants.items():
            with self.subTest(variant=label):
                revision = web_explorer._git_repo_revision(variant_context, variant_changes)
                self.assertNotIn(revision, seen)
                seen.add(revision)

    def test_revision_excludes_error_text_and_absolute_paths(self):
        context = {**self._base_context(), "repo_root": "/srv/app", "error": None}
        other = {
            **self._base_context(),
            "repo_root": "C:\\Users\\dev\\app",
            "error": "transient detail",
        }
        changes = [self._change("a.py", "modified", ".", "M")]
        self.assertEqual(
            web_explorer._git_repo_revision(context, changes),
            web_explorer._git_repo_revision(other, changes),
        )

    def test_equal_semantic_state_in_two_roots_shares_revision(self):
        first_repo = self._init_committed_repo("repo-a")
        second_repo = self._init_committed_repo("repo-b")
        for repo_dir in (first_repo, second_repo):
            (repo_dir / "README.md").write_text("# Project\n\nchanged\n", encoding="utf-8")
        first_session = self._create_explorer_session(first_repo)
        second_session = self._create_explorer_session(second_repo)

        first = self._git_state(first_session).get_json()
        second = self._git_state(second_session).get_json()

        self.assertEqual(first["revision"], second["revision"])

    def test_change_outside_subtree_root_does_not_change_revision(self):
        repo_dir = self._init_committed_repo()
        sub_dir = repo_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "inside.txt").write_text("inside\n", encoding="utf-8")
        self._run_git(repo_dir, "add", ".")
        self._run_git(repo_dir, "commit", "-m", "add sub")
        session_id = self._create_explorer_session(sub_dir)

        baseline = self._git_state(session_id).get_json()["revision"]
        (repo_dir / "README.md").write_text("# Project\n\noutside change\n", encoding="utf-8")
        after_outside = self._git_state(session_id).get_json()["revision"]
        self.assertEqual(baseline, after_outside)

        (sub_dir / "inside.txt").write_text("inside\n\nchanged\n", encoding="utf-8")
        after_inside = self._git_state(session_id).get_json()["revision"]
        self.assertNotEqual(baseline, after_inside)

    # ── Route ───────────────────────────────────────────────────────────────

    def test_git_state_first_poll_reports_changed(self):
        repo_dir = self._init_committed_repo()
        session_id = self._create_explorer_session(repo_dir)

        response = self._git_state(session_id)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertRegex(payload["revision"], r"^[0-9a-f]{16}$")
        self.assertTrue(payload["changed"])
        self.assertNotIn("git", payload)
        self.assertNotIn("changes", payload)
        self.assertNotIn("commits", payload)

    def test_git_state_known_revision_reports_unchanged(self):
        repo_dir = self._init_committed_repo()
        session_id = self._create_explorer_session(repo_dir)
        revision = self._git_state(session_id).get_json()["revision"]

        response = self._git_state(session_id, known=revision)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["changed"])
        self.assertEqual(payload["revision"], revision)

    def test_git_state_detects_stage_and_commit(self):
        repo_dir = self._init_committed_repo()
        session_id = self._create_explorer_session(repo_dir)
        revision = self._git_state(session_id).get_json()["revision"]

        (repo_dir / "README.md").write_text("# Project\n\nchanged\n", encoding="utf-8")
        unstaged = self._git_state(session_id, known=revision).get_json()
        self.assertTrue(unstaged["changed"])
        self.assertNotEqual(unstaged["revision"], revision)

        self._run_git(repo_dir, "add", "README.md")
        staged = self._git_state(session_id, known=unstaged["revision"]).get_json()
        self.assertTrue(staged["changed"])
        self.assertNotEqual(staged["revision"], unstaged["revision"])

        self._run_git(repo_dir, "commit", "-m", "second")
        committed = self._git_state(session_id, known=staged["revision"]).get_json()
        self.assertTrue(committed["changed"])
        self.assertNotIn(committed["revision"], {revision, unstaged["revision"], staged["revision"]})

    def test_git_state_sends_no_store_header(self):
        repo_dir = self._init_committed_repo()
        session_id = self._create_explorer_session(repo_dir)

        response = self._git_state(session_id)

        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_git_state_never_builds_commit_graph(self):
        repo_dir = self._init_committed_repo()
        session_id = self._create_explorer_session(repo_dir)
        with patch.object(
            web_explorer, "_bounded_git_graph_log", side_effect=AssertionError("graph log")
        ) as graph_log, patch.object(
            web_explorer, "_git_commit_files_log", side_effect=AssertionError("files log")
        ) as files_log:
            response = self._git_state(session_id)

        self.assertEqual(response.status_code, 200)
        graph_log.assert_not_called()
        files_log.assert_not_called()

    def test_git_state_missing_session_returns_404(self):
        response = self.client.get("/api/explorer/missing/git/state")

        self.assertEqual(response.status_code, 404)

    def test_git_state_non_git_root_returns_400(self):
        plain_dir = Path(self.temp_dir.name) / "plain"
        plain_dir.mkdir()
        session_id = self._create_explorer_session(plain_dir)

        response = self._git_state(session_id)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_git_state_backend_error_returns_500(self):
        repo_dir = self._init_committed_repo()
        session_id = self._create_explorer_session(repo_dir)
        with patch.object(api, "_get_git_repo_state", side_effect=OSError("disk gone")):
            response = self._git_state(session_id)

        self.assertEqual(response.status_code, 500)

    def test_git_repo_and_mutating_routes_carry_matching_revision(self):
        repo_dir = self._init_committed_repo()
        (repo_dir / "README.md").write_text("# Project\n\nchanged\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        repo_response = self.client.get(f"/api/explorer/{session_id}/git/repo")
        self.assertEqual(repo_response.status_code, 200)
        repo_revision = repo_response.get_json()["revision"]
        self.assertRegex(repo_revision, r"^[0-9a-f]{16}$")
        self.assertEqual(repo_revision, self._git_state(session_id).get_json()["revision"])

        stage_response = self.client.post(
            f"/api/explorer/{session_id}/git/stage",
            json={"path": "README.md"},
        )
        self.assertEqual(stage_response.status_code, 200)
        stage_revision = stage_response.get_json()["revision"]
        self.assertRegex(stage_revision, r"^[0-9a-f]{16}$")
        self.assertNotEqual(stage_revision, repo_revision)
        self.assertEqual(stage_revision, self._git_state(session_id).get_json()["revision"])

        stage_all_response = self.client.post(f"/api/explorer/{session_id}/git/stage-all")
        self.assertEqual(stage_all_response.status_code, 200)
        self.assertEqual(stage_all_response.get_json()["revision"], stage_revision)

        commit_response = self.client.post(
            f"/api/explorer/{session_id}/git/commit",
            json={"message": "second"},
        )
        self.assertEqual(commit_response.status_code, 200)
        commit_revision = commit_response.get_json()["revision"]
        self.assertRegex(commit_revision, r"^[0-9a-f]{16}$")
        self.assertNotIn(commit_revision, {repo_revision, stage_revision})
        self.assertEqual(commit_revision, self._git_state(session_id).get_json()["revision"])


class ExplorerFileStateTestCase(unittest.TestCase):
    """Explorer open-file change listener: the cheap stat token helper and the
    GET /api/explorer/<id>/file/state route it polls."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()

    def _create_explorer_session(self, root: Path) -> str:
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(root),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["sessions"][0]["session_id"]

    def _file_state(self, session_id: str, path: str, known: str = ""):
        return self.client.get(
            f"/api/explorer/{session_id}/file/state",
            query_string={"path": path, "known": known},
        )

    def test_state_revision_tracks_size_and_mtime_only(self):
        self.assertEqual(web_explorer._explorer_file_state_revision(12, 1.5), "12:1.500000")
        # A missing stat field never collapses two different files onto one token.
        self.assertEqual(web_explorer._explorer_file_state_revision(None, None), "-:-")
        self.assertNotEqual(
            web_explorer._explorer_file_state_revision(12, 1.5),
            web_explorer._explorer_file_state_revision(13, 1.5),
        )
        self.assertNotEqual(
            web_explorer._explorer_file_state_revision(12, 1.5),
            web_explorer._explorer_file_state_revision(12, 1.75),
        )

    def test_file_payload_carries_the_watch_baseline(self):
        target = self.root / "notes.txt"
        target.write_text("one\n", encoding="utf-8")
        session_id = self._create_explorer_session(self.root)

        payload = self.client.get(
            f"/api/explorer/{session_id}/file", query_string={"path": "notes.txt"}
        ).get_json()

        # Every load carries the baseline, so the client never needs a priming
        # round trip and can never mistake its own write for an external one.
        self.assertEqual(
            payload["state_revision"],
            self._file_state(session_id, "notes.txt").get_json()["revision"],
        )

    def test_file_state_reports_changed_against_a_known_token(self):
        target = self.root / "notes.txt"
        target.write_text("one\n", encoding="utf-8")
        session_id = self._create_explorer_session(self.root)

        first = self._file_state(session_id, "notes.txt")
        self.assertEqual(first.status_code, 200)
        revision = first.get_json()["revision"]
        self.assertTrue(first.get_json()["changed"])
        self.assertEqual(first.get_json()["path"], "notes.txt")

        unchanged = self._file_state(session_id, "notes.txt", known=revision).get_json()
        self.assertFalse(unchanged["changed"])
        self.assertEqual(unchanged["revision"], revision)

        target.write_text("one\ntwo\n", encoding="utf-8")
        changed = self._file_state(session_id, "notes.txt", known=revision).get_json()
        self.assertTrue(changed["changed"])
        self.assertNotEqual(changed["revision"], revision)

    def test_file_state_never_reads_file_contents(self):
        target = self.root / "notes.txt"
        target.write_text("one\n", encoding="utf-8")
        session_id = self._create_explorer_session(self.root)

        # The whole cost argument: watching an open file must not re-read (nor
        # re-render, nor re-hash) it on every poll.
        with patch.object(
            web_explorer, "read_explorer_file_preview", side_effect=AssertionError("read")
        ) as preview, patch.object(
            web_explorer, "_get_git_context", side_effect=AssertionError("git")
        ) as git_context:
            response = self._file_state(session_id, "notes.txt")

        self.assertEqual(response.status_code, 200)
        preview.assert_not_called()
        git_context.assert_not_called()

    def test_file_state_sends_no_store_header(self):
        (self.root / "notes.txt").write_text("one\n", encoding="utf-8")
        session_id = self._create_explorer_session(self.root)

        response = self._file_state(session_id, "notes.txt")

        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_file_state_missing_session_returns_404(self):
        response = self.client.get(
            "/api/explorer/missing/file/state", query_string={"path": "notes.txt"}
        )

        self.assertEqual(response.status_code, 404)

    def test_file_state_missing_file_returns_400(self):
        session_id = self._create_explorer_session(self.root)

        response = self._file_state(session_id, "gone.txt")

        self.assertEqual(response.status_code, 400)

    def test_file_state_refuses_paths_outside_the_root(self):
        (Path(self.temp_dir.name) / "outside.txt").write_text("secret\n", encoding="utf-8")
        session_id = self._create_explorer_session(self.root)

        response = self._file_state(session_id, "../outside.txt")

        self.assertEqual(response.status_code, 400)


class ExplorerGitWatchFrontendTestCase(unittest.TestCase):
    """Explorer Git change listener frontend contract
    (explorer_git_change_listener_plan_2026-07-30 §9)."""

    BANNED_VIEWER_CALLS = (
        "openExplorerFile(",
        "refreshExplorerAfterGitAction(",
        "loadExplorerPane(",
        "reloadExplorerTree(",
    )

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_git_watch_file_is_served_and_loads_before_terminals_js(self):
        watch = self._static("js/explorer-git-watch.js")
        self.assertIn("explorerGitWatchTick", watch)
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn(f"/static/js/explorer-git-watch.js?v={__version__}", html)
        self.assertLess(
            html.index("js/explorer-fs.js"), html.index("js/explorer-git-watch.js")
        )
        self.assertLess(
            html.index("js/explorer-git-watch.js"), html.index("js/terminals.js")
        )

    def test_git_watch_never_reaches_the_viewer_or_tree(self):
        # Invariant 2: the listener has no code path into the file viewer, the
        # editor buffer, or the directory listing.
        watch = self._static("js/explorer-git-watch.js")
        for call in self.BANNED_VIEWER_CALLS:
            with self.subTest(call=call):
                self.assertNotIn(call, watch)

    def test_git_watch_uses_recursive_settimeout_only(self):
        watch = self._static("js/explorer-git-watch.js")
        self.assertIn("setTimeout", watch)
        self.assertNotIn("setInterval", watch)

    def test_git_watch_eligibility_gates_are_wired(self):
        watch = self._static("js/explorer-git-watch.js")
        for gate in (
            "visibilityState",
            "isExplorerPaneInstance",
            "_explorerGitSidebarOpen",
            "_explorerGitRepoLoaded",
            "_explorerGitRevision",
            "_explorerGitRepoLoading",
            "_explorerGitActionBusy",
            "_explorerFsBusy",
            "_explorerEdit",
            "_explorerGitWatchInFlight",
            "_explorerGitWatchSuspended",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, watch)

    def test_git_watch_defers_apply_during_interaction(self):
        watch = self._static("js/explorer-git-watch.js")
        for gate in (
            "function explorerGitWatchDeferralActive(",
            "_explorerGitWatchPending",
            "genericConfirmModal",
            "explorerNameModal",
            "explorer-ctx-menu",
            "_explorerGitComposing",
            "pointerdown",
            ":hover",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, watch)

    def test_git_watch_adaptive_interval_backoff_and_suspension(self):
        watch = self._static("js/explorer-git-watch.js")
        self.assertIn("EXPLORER_GIT_WATCH_BASE_MS = 5000", watch)
        self.assertIn("EXPLORER_GIT_WATCH_REMOTE_BASE_MS = 10000", watch)
        self.assertIn("EXPLORER_GIT_WATCH_DURATION_FACTOR = 6", watch)
        self.assertIn("EXPLORER_GIT_WATCH_MAX_MS = 60000", watch)
        self.assertIn("EXPLORER_GIT_WATCH_MAX_FAILURES = 5", watch)
        self.assertIn("EXPLORER_GIT_WATCH_CHURN_LIMIT = 3", watch)
        self.assertIn("performance.now()", watch)

    def test_quiet_refresh_helper_contract(self):
        viewer = self._static("js/explorer-viewer.js")
        self.assertIn("async function refreshExplorerGitRepoQuiet(index)", viewer)
        quiet_fn = viewer[
            viewer.index("async function refreshExplorerGitRepoQuiet"):
            viewer.index("function applyExplorerGitRepoQuiet")
        ]
        # Forced + quiet: no invalidate (which would flash the Loading
        # placeholder), only a CSS class toggle on the existing panel.
        self.assertNotIn("invalidateExplorerGitRepo", quiet_fn)
        self.assertIn("git-refreshing", quiet_fn)
        self.assertNotIn("_explorerGitRepoLoading = true", quiet_fn)
        self.assertIn("cache: 'no-store'", quiet_fn)
        self.assertIn("function applyExplorerGitRepoQuiet(index, data)", viewer)
        self.assertIn("_explorerGitRevision", viewer)
        # Tab badges re-render only when the badge map actually changed.
        self.assertIn("badgesChanged", viewer)
        css = self._static("css/terminals.css")
        self.assertIn(".explorer-git-panel.git-refreshing", css)

    def test_suspended_watch_renders_muted_pause_line(self):
        viewer = self._static("js/explorer-viewer.js")
        self.assertIn("Live updates paused", viewer)
        self.assertIn("_explorerGitWatchSuspended", viewer)
        css = self._static("css/terminals.css")
        self.assertIn(".explorer-git-watch-paused", css)

    # ── Open-file change listener ───────────────────────────────────────────

    def test_open_file_watch_polls_the_cheap_state_route(self):
        watch = self._static("js/explorer-git-watch.js")
        self.assertIn("/file/state", watch)
        self.assertIn("async function explorerFileWatchCheckOne(index)", watch)
        # Same scheduler, same recursive setTimeout, same identity discipline.
        self.assertNotIn("setInterval", watch)
        self.assertIn("explorerWatchPaneCurrent(index, pane, sessionId)", watch)

    def test_open_file_watch_eligibility_gates_are_wired(self):
        watch = self._static("js/explorer-git-watch.js")
        for gate in (
            "function explorerFileWatchEligible(",
            "_explorerFileStateRevision",
            "_explorerMode !== 'file'",
            "pane._explorerEdit",
            "_explorerDiffUndoBusy",
            "_explorerFileWatchInFlight",
            "_explorerFileWatchSuspended",
            "_explorerFileWatchRefreshing",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, watch)

    def test_open_file_watch_defers_apply_during_interaction(self):
        watch = self._static("js/explorer-git-watch.js")
        for gate in (
            "function explorerFileWatchDeferralActive(",
            "explorerWatchInteractionActive()",
            "_explorerFileWatchPending",
            "getSelection",
            "isCollapsed",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, watch)

    def test_open_file_watch_applies_through_the_quiet_viewer_helper(self):
        viewer = self._static("js/explorer-viewer.js")
        self.assertIn("async function refreshExplorerOpenFileQuiet(index)", viewer)
        quiet_fn = viewer[
            viewer.index("async function refreshExplorerOpenFileQuiet"):
            viewer.index("function applyExplorerGitRepoQuiet")
        ]
        # Quiet: no loading placeholder, no tree/pane reload, and never against
        # an open editor buffer.
        self.assertIn("cache: 'no-store'", quiet_fn)
        self.assertIn("explorerEditState(pane)", quiet_fn)
        self.assertIn("updateExplorerFileInPlace(index, data, scrollState)", quiet_fn)
        self.assertNotIn("renderExplorerMessage", quiet_fn)
        self.assertNotIn("reloadExplorerTree", quiet_fn)
        # The baseline is set from every load/save, so a GridVibe write is never
        # mistaken for an external change.
        self.assertIn("function setExplorerFileWatchBaseline(pane, revision)", viewer)
        self.assertIn("setExplorerFileWatchBaseline(pane, data.state_revision || '')", viewer)

    # ── Filesystem-surface change listener (plan §15) ───────────────────────

    def test_fs_surface_watch_shares_the_single_git_state_poll(self):
        watch = self._static("js/explorer-git-watch.js")
        # One request, two baselines: the listing/tree consumer rides the same
        # /git/state poll the sidebar uses rather than adding an endpoint.
        self.assertEqual(watch.count("/git/state?known="), 1)
        self.assertIn("function explorerFsWatchConsumer(pane)", watch)
        self.assertIn("_explorerFsWatchRevision", watch)
        self.assertIn("refreshExplorerFilesystemSurfacesQuiet(index)", watch)
        # Only panes inside a Git worktree have this consumer — the revision is
        # the change signal, so a non-repository root never polls for it.
        self.assertIn("_explorerGitContext?.available", watch)
        self.assertIn("_explorerTreeSidebarOpen", watch)
        self.assertIn("_explorerMode === 'directory'", watch)
        # Failures back off silently and never advance the baseline.
        self.assertIn("function explorerFsWatchOnFailure(pane)", watch)
        self.assertIn("_explorerFsWatchSuspended", watch)

    def test_fs_surface_watch_defers_apply_during_interaction(self):
        watch = self._static("js/explorer-git-watch.js")
        for gate in (
            "function explorerFsWatchDeferralActive(",
            "explorerWatchInteractionActive()",
            "_explorerFsWatchPending",
            "_explorerFsWatchRefreshing",
            "explorer-tree-panel-",
            ":hover",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, watch)

    def test_fs_surface_quiet_refresh_helper_contract(self):
        viewer = self._static("js/explorer-viewer.js")
        for helper in (
            "function explorerEntriesSignature(entries)",
            "async function refreshExplorerDirectoryQuiet(index)",
            "async function refreshExplorerTreeQuiet(index)",
            "async function refreshExplorerFilesystemSurfacesQuiet(index)",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, viewer)
        quiet_fn = viewer[
            viewer.index("function explorerEntriesSignature(entries)"):
            viewer.index("async function performExplorerGitAction")
        ]
        self.assertIn("cache: 'no-store'", quiet_fn)
        # Quiet: no loading placeholder, no tab/scroll/search reset, and never
        # through the user-initiated load paths.
        for banned in (
            "renderExplorerMessage",
            "loadExplorerPane(",
            "reloadExplorerTree(",
            "openExplorerFile(",
            "explorerCaptureActiveTabView(",
        ):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, quiet_fn)
        # An unchanged listing performs zero DOM writes, and the tree refresh
        # is bounded because every node costs one /entries.
        self.assertIn("explorerEntriesSignature(pane._explorerEntries)", quiet_fn)
        self.assertIn("EXPLORER_FS_WATCH_MAX_TREE_NODES", quiet_fn)
        self.assertIn("captureScrollMetrics(", quiet_fn)

    def test_fs_watch_baseline_is_reset_by_user_initiated_loads(self):
        viewer = self._static("js/explorer-viewer.js")
        self.assertIn("function resetExplorerFsWatchBaseline(pane)", viewer)
        # Every listing/tree load leaves the surfaces current, so the next poll
        # re-bootstraps silently instead of repainting what was just fetched.
        self.assertEqual(viewer.count("resetExplorerFsWatchBaseline(pane);"), 2)

    def test_promoted_preview_tab_keeps_its_git_badge(self):
        viewer = self._static("js/explorer-viewer.js")
        promote_fn = viewer[
            viewer.index("function promoteExplorerPreviewTab(index)"):
            viewer.index("function renderExplorerViewerEmpty(index)")
        ]
        self.assertIn("pinnedTab.git = preview.git || null;", promote_fn)

    def test_empty_diff_falls_back_to_the_file_content_view(self):
        viewer = self._static("js/explorer-viewer.js")
        self.assertIn("function explorerFallbackFromEmptyDiff(index)", viewer)
        fallback = viewer[
            viewer.index("function explorerFallbackFromEmptyDiff(index)"):
            viewer.index("function setExplorerDiffSplit(index, open)")
        ]
        # Commit diffs are history and never bounce; the live diff falls back to
        # the sticky source/preview preference, and only to a panel that exists.
        self.assertIn("pane._explorerDiffCommit", fallback)
        self.assertIn("_explorerLastFileView === 'preview'", fallback)
        self.assertIn("setExplorerFileView(index, preferred)", fallback)
        self.assertIn("setExplorerDiffToggleHidden(index, true)", fallback)
        # Applied on both the cached and the freshly-fetched diff paths.
        self.assertEqual(viewer.count("if (explorerFallbackFromEmptyDiff(index)) {"), 2)

    def test_missing_view_panel_never_hides_every_panel(self):
        viewer = self._static("js/explorer-viewer.js")
        # Selecting a panel that does not exist used to hide all of them and
        # leave an empty viewer — a restored 'diff' scroll state routinely
        # outlives its panel once the file's changes are discarded.
        self.assertIn("function explorerResolveFileView(index, mode)", viewer)
        self.assertIn(
            "const selectedMode = explorerResolveFileView(index, normalizedMode);", viewer
        )
        resolve = viewer[
            viewer.index("function explorerResolveFileView(index, mode)"):
            viewer.index("function setExplorerFileView(index, mode)")
        ]
        self.assertIn("data-explorer-file-panel=", resolve)
        self.assertIn("_explorerLastFileView", resolve)
        self.assertIn("'source', 'preview'", resolve)

    def test_pathless_tab_never_keeps_a_git_badge(self):
        viewer = self._static("js/explorer-viewer.js")
        sync = viewer[
            viewer.index("function syncExplorerTabGitFromRepo(index, repo)"):
            viewer.index("function explorerAssignOpenTab(pane, path")
        ]
        # A Preview tab back on a directory listing shows no file, so it must
        # not keep the badge of the file it happened to show last.
        self.assertIn("const nextGit = path ? (changesByPath.get(path) || null) : null;", sync)
        # Cleared eagerly too, so it does not wait for a Git sidebar sync.
        self.assertIn("previewTab.git = null;", viewer)
        self.assertIn("preview.git = null;", viewer)


# ---------------------------------------------------------------------------
#  Phase 1-3 regression tests (code_review_2026_03_31.md)
# ---------------------------------------------------------------------------


class SshConnectionErrorPathTestCase(unittest.TestCase):
    """Issue 3 — verify the SSH error path does not double-close the client."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH",
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

    @patch("web.terminal_io.paramiko")
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
            web_saved_sessions, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True

    def tearDown(self):
        self.saved_sessions_patch.stop

    @patch("web.voice._wait_for_vosk_ready", return_value=False)
    @patch("web.voice._vosk_service_reachable", return_value=False)
    @patch("web.voice.subprocess.Popen")
    def test_vosk_timeout_waits_after_kill(self, mock_popen, _mock_reachable, _mock_ready):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 99999
        mock_popen.return_value = mock_process

        original = web_voice._vosk_process
        try:
            web_voice._vosk_process = None
            result = api._ensure_vosk_service()
        finally:
            web_voice._vosk_process = original

        self.assertFalse(result)
        mock_process.kill.assert_called_once()
        mock_process.wait.assert_called_once()


class SshConnectExceptionHandlingTestCase(unittest.TestCase):
    """Issue 11 — verify the SSH connect handler uses narrow exception types."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH",
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

    @patch("web.terminal_io.paramiko")
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

    @patch("web.terminal_io.paramiko")
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
            web_saved_sessions, "SAVED_SESSIONS_PATH",
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
        api._cache_terminal_output(session_id, "some output data")

        api._close_ssh_connection(session_id)

        self.assertNotIn(session_id, api.session_output_buffers)

    def test_buffer_preserved_when_clear_buffer_false(self):
        session_id = "buf-keep"
        api._cache_terminal_output(session_id, "keep this")

        api._close_ssh_connection(session_id, clear_buffer=False)

        self.assertIn(session_id, api.session_output_buffers)


class TerminalOutputBufferCacheTestCase(unittest.TestCase):
    """Perf finding 3.2 — rolling output buffer stores chunks and trims exactly."""

    def setUp(self):
        with api.connection_lock:
            api.session_output_buffers.clear()
        self.addCleanup(self._clear_buffers)

    def _clear_buffers(self):
        with api.connection_lock:
            api.session_output_buffers.clear()

    def test_cache_appends_chunks_and_joins_for_replay(self):
        api._cache_terminal_output("buf-a", "hello ")
        api._cache_terminal_output("buf-a", "world")

        self.assertEqual(api._get_buffered_terminal_output("buf-a"), "hello world")

    def test_cache_trims_to_last_max_chars(self):
        limit = api.TERMINAL_OUTPUT_BUFFER_MAX_CHARS
        api._cache_terminal_output("buf-b", "x" * 30000)
        api._cache_terminal_output("buf-b", "y" * 30000)

        buffered = api._get_buffered_terminal_output("buf-b")
        self.assertEqual(len(buffered), limit)
        self.assertEqual(buffered, "x" * (limit - 30000) + "y" * 30000)

    def test_single_oversized_chunk_keeps_only_the_tail(self):
        limit = api.TERMINAL_OUTPUT_BUFFER_MAX_CHARS
        api._cache_terminal_output("buf-c", "a" + "b" * limit)

        self.assertEqual(api._get_buffered_terminal_output("buf-c"), "b" * limit)

    def test_empty_output_is_ignored(self):
        api._cache_terminal_output("buf-d", "")

        with api.connection_lock:
            self.assertNotIn("buf-d", api.session_output_buffers)

    def test_clear_terminal_output_buffer_empties_replay(self):
        api._cache_terminal_output("buf-e", "data")
        api._clear_terminal_output_buffer("buf-e")

        self.assertEqual(api._get_buffered_terminal_output("buf-e"), "")


class SshSftpPoolTestCase(unittest.TestCase):
    """Perf finding 3.1 — explorer SSH transports are pooled per session."""

    def setUp(self):
        api._evict_all_pooled_ssh_clients()
        self.addCleanup(api._evict_all_pooled_ssh_clients)

    def _fake_client(self, active=True):
        if web_explorer.paramiko is None:
            self.skipTest("paramiko is not installed")
        client = web_explorer.paramiko.SSHClient()
        transport = MagicMock()
        transport.is_active.return_value = active
        client.get_transport = MagicMock(return_value=transport)
        client.open_sftp = MagicMock(side_effect=lambda: MagicMock())
        client.close = MagicMock()
        return client

    def test_acquire_reuses_pooled_transport_for_next_request(self):
        session = SimpleNamespace(session_id="pool-1")
        client = self._fake_client()
        first_sftp = MagicMock()

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(client, first_sftp)) as opener:
            got_client, got_sftp = api._acquire_ssh_sftp(session)
            api._release_ssh_sftp(session, got_client, got_sftp)
            second_client, second_sftp = api._acquire_ssh_sftp(session)
            api._release_ssh_sftp(session, second_client, second_sftp)

        opener.assert_called_once()
        self.assertIs(got_client, client)
        self.assertIs(second_client, client)
        # The pooled client served the second request via a fresh SFTP channel.
        client.open_sftp.assert_called_once()
        first_sftp.close.assert_called_once()
        second_sftp.close.assert_called_once()
        client.close.assert_not_called()

    def test_release_closes_clients_that_cannot_be_pooled(self):
        session = SimpleNamespace(session_id="pool-2")
        client = MagicMock()
        sftp = MagicMock()

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(client, sftp)):
            api._acquire_ssh_sftp(session)

        with web_explorer._ssh_client_pool_lock:
            self.assertNotIn("pool-2", web_explorer._ssh_client_pool)

        api._release_ssh_sftp(session, client, sftp)
        sftp.close.assert_called_once()
        client.close.assert_called_once()

    def test_dead_pooled_client_is_replaced_with_a_fresh_connection(self):
        session = SimpleNamespace(session_id="pool-3")
        dead_client = self._fake_client(active=False)
        with web_explorer._ssh_client_pool_lock:
            web_explorer._ssh_client_pool["pool-3"] = (time.monotonic(), dead_client)

        fresh_client = self._fake_client()
        fresh_sftp = MagicMock()
        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fresh_client, fresh_sftp)) as opener:
            got_client, got_sftp = api._acquire_ssh_sftp(session)

        opener.assert_called_once()
        self.assertIs(got_client, fresh_client)
        self.assertIs(got_sftp, fresh_sftp)
        dead_client.close.assert_called_once()

    def test_idle_pooled_clients_are_reaped(self):
        idle_client = self._fake_client()
        with web_explorer._ssh_client_pool_lock:
            web_explorer._ssh_client_pool["pool-idle"] = (
                time.monotonic() - web_explorer.SSH_CLIENT_POOL_IDLE_TIMEOUT - 1,
                idle_client,
            )

        web_explorer._reap_idle_pooled_ssh_clients()

        with web_explorer._ssh_client_pool_lock:
            self.assertNotIn("pool-idle", web_explorer._ssh_client_pool)
        idle_client.close.assert_called_once()

    def test_close_ssh_connection_evicts_the_pool_entry(self):
        client = self._fake_client()
        with web_explorer._ssh_client_pool_lock:
            web_explorer._ssh_client_pool["pool-close"] = (time.monotonic(), client)

        api._close_ssh_connection("pool-close")

        with web_explorer._ssh_client_pool_lock:
            self.assertNotIn("pool-close", web_explorer._ssh_client_pool)
        client.close.assert_called_once()


class SessionGroupsUpdatedBroadcastTestCase(unittest.TestCase):
    """Perf finding 3.4 — group changes are pushed so the UI need not poll."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)

    def _socket_client(self):
        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)
        socket_client.get_received()
        socket_client.emit("join_workspace", {"workspace_id": "default"})
        socket_client.get_received()
        return socket_client

    def _received_reasons(self, socket_client):
        return [
            event["args"][0].get("reason")
            for event in socket_client.get_received()
            if event["name"] == "session_groups_updated"
        ]

    def test_launch_broadcasts_session_groups_updated(self):
        socket_client = self._socket_client()

        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "ssh",
                    "sessions": [
                        {
                            "host": "10.0.0.10",
                            "directory": "/srv/app",
                            "username": "ubuntu",
                            "title": "App",
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertIn("launched", self._received_reasons(socket_client))

    def test_reorder_broadcasts_session_groups_updated(self):
        for group_id, name in (("g-a", "A"), ("g-b", "B")):
            api.session_manager.create_group(
                name=name,
                connection_mode="ssh",
                layout="single",
                terminal_count=1,
                group_id=group_id,
            )
        socket_client = self._socket_client()

        response = self.client.post(
            "/api/session-groups/order",
            json={"group_ids": ["g-b", "g-a"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("reordered", self._received_reasons(socket_client))

    def test_close_session_broadcasts_session_groups_updated(self):
        api.session_manager.create_group(
            name="Solo",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="g-solo",
        )
        session = api.session_manager.create_session(
            group_id="g-solo",
            host="10.0.0.10",
            directory="/srv/app",
        )
        socket_client = self._socket_client()

        response = self.client.delete(f"/api/sessions/{session.session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("session_closed", self._received_reasons(socket_client))

    def test_close_all_broadcasts_session_groups_updated(self):
        socket_client = self._socket_client()

        response = self.client.delete("/api/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertIn("all_closed", self._received_reasons(socket_client))


class SessionStatusRoomScopeTestCase(unittest.TestCase):
    """Deep-dive 1.1 step 3 — session_status is emitted to the session room only."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)

    def _socket_client(self):
        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)
        socket_client.get_received()
        return socket_client

    def test_broadcast_reaches_joined_clients_only(self):
        api.session_manager.create_group(
            name="Scoped",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-scoped",
        )
        session = api.session_manager.create_session(
            group_id="group-scoped",
            host="10.0.0.12",
            directory="/tmp/project",
        )

        joined_client = self._socket_client()
        bystander_client = self._socket_client()
        joined_client.emit("join_session", {"session_id": session.session_id})
        joined_client.get_received()  # drain the join reply

        api._broadcast_session_status(session.session_id)

        joined_events = [
            event for event in joined_client.get_received()
            if event["name"] == "session_status"
        ]
        bystander_events = [
            event for event in bystander_client.get_received()
            if event["name"] == "session_status"
        ]
        self.assertEqual(len(joined_events), 1)
        self.assertEqual(
            joined_events[0]["args"][0]["session_id"], session.session_id
        )
        self.assertEqual(bystander_events, [])


class SharedRunServerTestCase(unittest.TestCase):
    """Deep-dive 5.7 — one server entry point with a consistent flag set."""

    def test_run_server_passes_the_full_flag_set(self):
        with patch.object(api.socketio, "run") as mock_run:
            api.run_server("192.0.2.1", 8080, True)
        mock_run.assert_called_once_with(
            api.app,
            host="192.0.2.1",
            port=8080,
            debug=True,
            use_reloader=False,
            allow_unsafe_werkzeug=True,
        )

    def test_entry_points_share_the_api_run_server(self):
        import main as main_module
        import web.webview_launcher as launcher_module

        self.assertIs(main_module.run_server, api.run_server)
        self.assertIs(launcher_module.run_server, api.run_server)


class SessionStatusBroadcastRaceTestCase(unittest.TestCase):
    """Issue 7, superseded by guardrail audit N4 (2026-07-22).

    The broadcast originally held session_manager.lock through the emit so
    removal was serialized behind it. It now snapshots the payload under the
    lock and emits after release, so one slow client write can no longer
    stall session management. The stale-status window this reopens is
    harmless: the frontend session_status handler drops unknown session ids,
    and the session_groups_updated refresh reconciles the pane set.
    """

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            web_saved_sessions,
            "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api.app.config["TESTING"] = True
        api.session_manager.reset_sessions()

    def tearDown(self):
        api.session_manager.reset_sessions()

    def test_broadcast_snapshot_does_not_block_session_removal(self):
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
            # The manager lock is released before the emit, so removal
            # completes even while the broadcast's client write is stalled.
            self.assertTrue(remove_done.wait(timeout=1))

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
            web_saved_sessions, "SAVED_SESSIONS_PATH",
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

    @patch("web.voice.emit")
    @patch("web.voice._ensure_vosk_service", return_value=True)
    @patch("web.voice.ws_client")
    def test_voice_start_stores_connection_under_lock(self, mock_ws_client, _mock_ensure, _mock_emit):
        mock_ws = MagicMock()
        mock_ws_client.create_connection.return_value = mock_ws

        api._start_vosk_voice_session("sess-01")

        with api._vosk_lock:
            self.assertIn("sess-01", api._vosk_ws_connections)
            self.assertIs(api._vosk_ws_connections["sess-01"], mock_ws)

    @patch("web.voice.emit")
    @patch("web.voice._ensure_vosk_service", return_value=True)
    @patch("web.voice.ws_client")
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

    @patch("web.voice.emit")
    @patch("web.voice._restart_vosk_service", return_value=True)
    @patch("web.voice._ensure_vosk_service", return_value=True)
    @patch("web.voice.ws_client")
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
            web_saved_sessions, "SAVED_SESSIONS_PATH",
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

    @patch("web.terminal_io._broadcast_session_status")
    @patch("web.terminal_io.session_manager")
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

    @patch("web.terminal_io._broadcast_session_status")
    @patch("web.terminal_io.session_manager")
    def test_stream_local_output_fallback_reads_chunks_via_read1(
        self, mock_session_mgr, _mock_broadcast
    ):
        session_id = "stdout-chunk-test"

        class FakeStdout:
            def __init__(self):
                self.read1_sizes = []
                self._chunks = [b"chunk-one", b""]

            def read1(self, size):
                self.read1_sizes.append(size)
                return self._chunks.pop(0)

            def read(self, _size):
                raise AssertionError("byte-at-a-time read used despite read1 being available")

            def close(self):
                pass

        class FakeProcess:
            def poll(self):
                return 0

        stdout = FakeStdout()
        with api.connection_lock:
            api.ssh_connections[session_id] = {
                "kind": "local",
                "process": FakeProcess(),
                "pty_process": None,
                "master_fd": None,
                "stdout": stdout,
            }

        mock_session = MagicMock()
        mock_session.status = api.SessionStatus.CONNECTED
        mock_session_mgr.get_session.return_value = mock_session

        emitted = []
        with patch.object(
            api.socketio, "emit",
            side_effect=lambda _event, payload, **_kw: emitted.append(payload["data"]),
        ):
            api._stream_local_output(session_id)

        self.assertEqual(stdout.read1_sizes, [4096, 4096])
        self.assertEqual(emitted, ["chunk-one"])
        with api.connection_lock:
            self.assertNotIn(session_id, api.ssh_connections)


class SshStreamBlockingRecvTestCase(unittest.TestCase):
    """Deep-dive 3.3 — SSH stream blocks on recv with a timeout instead of 50 ms polling."""

    def setUp(self):
        api.app.config["TESTING"] = True
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def tearDown(self):
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    class FakeChannel:
        def __init__(self, chunks, exit_ready_when_drained=False):
            self._chunks = list(chunks)
            self._exit_ready_when_drained = exit_ready_when_drained
            self.closed = False
            self.timeout = None

        def settimeout(self, value):
            self.timeout = value

        def recv(self, _size):
            if not self._chunks:
                return b""
            item = self._chunks.pop(0)
            if item is socket.timeout:
                raise socket.timeout()
            return item

        def exit_status_ready(self):
            return self._exit_ready_when_drained and not self._chunks

        def close(self):
            self.closed = True

    @patch("web.terminal_io._broadcast_session_status")
    @patch("web.terminal_io.session_manager")
    def test_stream_ssh_output_sets_recv_timeout_and_survives_timeouts(
        self, mock_session_mgr, _mock_broadcast
    ):
        session_id = "ssh-recv-test"
        channel = self.FakeChannel([b"hello ", socket.timeout, b"world"])
        with api.connection_lock:
            api.ssh_connections[session_id] = {"kind": "ssh", "channel": channel}

        mock_session = MagicMock()
        mock_session.status = api.SessionStatus.CONNECTED
        mock_session_mgr.get_session.return_value = mock_session

        emitted = []
        with patch.object(
            api.socketio, "emit",
            side_effect=lambda _event, payload, **_kw: emitted.append(payload["data"]),
        ):
            api._stream_ssh_output(session_id)

        self.assertEqual(channel.timeout, api.SSH_STREAM_RECV_TIMEOUT)
        self.assertEqual("".join(emitted), "hello world")
        # EOF (empty recv) ends the loop and the connection is finalized.
        mock_session_mgr.update_session_status.assert_called_with(
            session_id, api.SessionStatus.DISCONNECTED
        )
        with api.connection_lock:
            self.assertNotIn(session_id, api.ssh_connections)

    @patch("web.terminal_io._broadcast_session_status")
    @patch("web.terminal_io.session_manager")
    def test_stream_ssh_output_exits_on_exit_status_after_timeout(
        self, mock_session_mgr, _mock_broadcast
    ):
        session_id = "ssh-exit-test"
        channel = self.FakeChannel(
            [b"bye", socket.timeout], exit_ready_when_drained=True
        )
        with api.connection_lock:
            api.ssh_connections[session_id] = {"kind": "ssh", "channel": channel}

        mock_session = MagicMock()
        mock_session.status = api.SessionStatus.CONNECTED
        mock_session_mgr.get_session.return_value = mock_session

        emitted = []
        with patch.object(
            api.socketio, "emit",
            side_effect=lambda _event, payload, **_kw: emitted.append(payload["data"]),
        ):
            api._stream_ssh_output(session_id)

        self.assertEqual(emitted, ["bye"])
        with api.connection_lock:
            self.assertNotIn(session_id, api.ssh_connections)


class VendoredFrontendAssetsTestCase(unittest.TestCase):
    """Deep-dive 3.6 — xterm/socket.io are served locally instead of from a CDN."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def test_terminals_page_references_vendored_assets_not_cdn(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn("/static/vendor/xterm.css", html)
        self.assertIn("/static/vendor/xterm.min.js", html)
        self.assertIn("/static/vendor/xterm-addon-fit.min.js", html)
        self.assertIn("/static/vendor/socket.io.min.js", html)

    def test_vendored_assets_are_served(self):
        for filename in (
            "vendor/xterm.css",
            "vendor/xterm.min.js",
            "vendor/xterm-addon-fit.min.js",
            "vendor/socket.io.min.js",
        ):
            with self.subTest(filename=filename):
                response = self.client.get(f"/static/{filename}")
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.get_data()), 1000)
                response.close()


class GuardrailAuditFixesTestCase(unittest.TestCase):
    """Guardrail audit 2026-07-22 (docs/guardrail_audit_2026-07-22.md) —
    findings N1–N4 stay fixed."""

    BANNED_DIALOG_CALLS = ("window.confirm(", "window.alert(", "window.prompt(")
    BANNED_GLYPHS = ("📁", "🌐", "🎤", "☾", "☀", "❌")
    STATIC_JS = (
        "js/shared.js",
        "js/workspaces.js",
        "js/app-settings.js",
        "js/launcher.js",
        "js/terminals.js",
        "js/explorer-viewer.js",
        "js/explorer-editor.js",
        "js/explorer-search.js",
        "js/explorer-fs.js",
        "js/explorer-git-watch.js",
        "js/browser-pane.js",
        "js/terminal-shell.js",
    )

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _get_text(self, path: str) -> str:
        response = self.client.get(path)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_static_js_uses_no_blocking_window_dialogs(self):
        """N1/N2 — WebView2 blocks window.confirm/alert/prompt, so no code
        path may call them (including browser-mode-only ones — N1 happened
        because the pattern was imitated in a path that runs natively)."""
        for filename in self.STATIC_JS:
            body = self._get_text(f"/static/{filename}")
            for call in self.BANNED_DIALOG_CALLS:
                with self.subTest(filename=filename, call=call):
                    self.assertNotIn(call, body)

    def test_launcher_page_ships_generic_confirm_shell(self):
        """N1 — restart/close confirmations go through the in-page shell."""
        launcher_html = self._get_text("/")
        self.assertIn('id="genericConfirmModal"', launcher_html)
        launcher_js = self._get_text("/static/js/launcher.js")
        for caller in ("shutdownBrowserApp", "restartApplication", "checkForUpdates"):
            with self.subTest(caller=caller):
                body = launcher_js[launcher_js.index(f"async function {caller}"):]
                body = body[:body.index("\n    }")]
                self.assertIn("await openGenericConfirmModal", body)

    def test_static_js_uses_no_emoji_glyph_icons(self):
        """N3 — guardrail 7: stroke-style SVG icons, not emoji glyphs."""
        for filename in self.STATIC_JS:
            body = self._get_text(f"/static/{filename}")
            for glyph in self.BANNED_GLYPHS:
                with self.subTest(filename=filename, glyph=glyph):
                    self.assertNotIn(glyph, body)

    def test_broadcast_session_status_emits_outside_manager_lock(self):
        """N4 — snapshot under session_manager.lock, emit after release."""
        session_id = "audit-lock-status"
        fake_session = MagicMock()
        fake_session.to_dict.return_value = {"session_id": session_id}
        lock_owned_during_emit = []

        def fake_emit(_event, _payload, **_kwargs):
            lock_owned_during_emit.append(api.session_manager.lock._is_owned())

        with api.session_manager.lock:
            api.session_manager.sessions[session_id] = fake_session
        try:
            with patch.object(api.socketio, "emit", side_effect=fake_emit):
                api._broadcast_session_status(session_id)
        finally:
            with api.session_manager.lock:
                api.session_manager.sessions.pop(session_id, None)

        self.assertEqual(lock_owned_during_emit, [False])


class ExtractedFrontendAssetsTestCase(unittest.TestCase):
    """Deep-dive 3.5/6.4 — inline CSS/JS moved to cacheable static files."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def test_pages_reference_versioned_static_assets(self):
        launcher_html = self.client.get("/").get_data(as_text=True)
        terminals_html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn(f"/static/css/launcher.css?v={__version__}", launcher_html)
        self.assertIn(f"/static/js/shared.js?v={__version__}", launcher_html)
        self.assertIn(f"/static/js/launcher.js?v={__version__}", launcher_html)
        self.assertIn(f"/static/css/terminals.css?v={__version__}", terminals_html)
        self.assertIn(f"/static/js/shared.js?v={__version__}", terminals_html)
        # The App Settings dialog is shared by both pages (todo 1).
        for page_html in (launcher_html, terminals_html):
            self.assertIn(f"/static/css/app-settings.css?v={__version__}", page_html)
            self.assertIn(f"/static/js/app-settings.js?v={__version__}", page_html)
            # It reads shared.js helpers (applyTheme, the broadcast keys), so
            # it loads after shared.js and before the page script.
            self.assertLess(
                page_html.index("js/shared.js"), page_html.index("js/app-settings.js")
            )
        self.assertLess(
            launcher_html.index("js/app-settings.js"),
            launcher_html.index("js/launcher.js"),
        )
        self.assertLess(
            terminals_html.index("js/app-settings.js"),
            terminals_html.index("js/terminals.js"),
        )
        self.assertIn(f"/static/js/terminal-icons.js?v={__version__}", terminals_html)
        self.assertIn(f"/static/js/voice-input.js?v={__version__}", terminals_html)
        self.assertIn(f"/static/js/explorer-viewer.js?v={__version__}", terminals_html)
        self.assertIn(f"/static/js/explorer-editor.js?v={__version__}", terminals_html)
        self.assertIn(f"/static/js/explorer-fs.js?v={__version__}", terminals_html)
        self.assertIn(f"/static/js/terminals.js?v={__version__}", terminals_html)
        # shared.js must load before each page script so its globals exist first.
        self.assertLess(
            launcher_html.index("js/shared.js"), launcher_html.index("js/launcher.js")
        )
        self.assertLess(
            terminals_html.index("js/shared.js"), terminals_html.index("js/terminals.js")
        )
        # Extracted terminals.js modules (2026-07-23 split) load after shared.js
        # and before terminals.js, which owns the shared state and boot.
        self.assertLess(
            terminals_html.index("js/shared.js"),
            terminals_html.index("js/terminal-icons.js"),
        )
        self.assertLess(
            terminals_html.index("js/terminal-icons.js"),
            terminals_html.index("js/voice-input.js"),
        )
        self.assertLess(
            terminals_html.index("js/voice-input.js"),
            terminals_html.index("js/explorer-viewer.js"),
        )
        # explorer-editor.js loads after explorer-viewer.js (reuses its render
        # hooks) and before terminals.js (which owns the shared boot).
        self.assertLess(
            terminals_html.index("js/explorer-viewer.js"),
            terminals_html.index("js/explorer-editor.js"),
        )
        # explorer-search.js (repository search panel) loads after
        # explorer-editor.js and before terminals.js.
        self.assertIn(f"/static/js/explorer-search.js?v={__version__}", terminals_html)
        self.assertLess(
            terminals_html.index("js/explorer-editor.js"),
            terminals_html.index("js/explorer-search.js"),
        )
        self.assertLess(
            terminals_html.index("js/explorer-search.js"),
            terminals_html.index("js/explorer-fs.js"),
        )
        # explorer-git-watch.js (Git change listener) loads after the explorer
        # modules and before terminals.js, which owns the shared state it reads.
        self.assertIn(f"/static/js/explorer-git-watch.js?v={__version__}", terminals_html)
        self.assertLess(
            terminals_html.index("js/explorer-fs.js"),
            terminals_html.index("js/explorer-git-watch.js"),
        )
        self.assertLess(
            terminals_html.index("js/explorer-git-watch.js"),
            terminals_html.index("js/terminals.js"),
        )
        # browser-pane.js (tabbed browser preview surface) loads after the
        # explorer modules and before terminals.js.
        self.assertIn(f"/static/js/browser-pane.js?v={__version__}", terminals_html)
        self.assertLess(
            terminals_html.index("js/explorer-fs.js"),
            terminals_html.index("js/browser-pane.js"),
        )
        # terminal-shell.js (pane shell picker) is the last module before
        # terminals.js, which calls into it while building pane headers.
        self.assertIn(f"/static/js/terminal-shell.js?v={__version__}", terminals_html)
        self.assertLess(
            terminals_html.index("js/browser-pane.js"),
            terminals_html.index("js/terminal-shell.js"),
        )
        self.assertLess(
            terminals_html.index("js/terminal-shell.js"),
            terminals_html.index("js/terminals.js"),
        )

    def test_extracted_assets_are_served_without_jinja(self):
        for filename in (
            "css/launcher.css",
            "css/terminals.css",
            "css/app-settings.css",
            "js/shared.js",
            "js/app-settings.js",
            "js/launcher.js",
            "js/terminal-icons.js",
            "js/voice-input.js",
            "js/explorer-viewer.js",
            "js/explorer-editor.js",
            "js/explorer-search.js",
            "js/explorer-fs.js",
            "js/explorer-git-watch.js",
            "js/browser-pane.js",
            "js/terminal-shell.js",
            "js/terminals.js",
        ):
            with self.subTest(filename=filename):
                response = self.client.get(f"/static/{filename}")
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertGreater(len(body), 1000)
                self.assertNotIn("{{", body)
                self.assertNotIn("{%", body)
                response.close()

    def test_shared_helpers_are_not_redefined_by_page_scripts(self):
        """Finding 6.4 — reconciled helpers exist once, in shared.js."""
        shared = self.client.get("/static/js/shared.js").get_data(as_text=True)
        launcher = self.client.get("/static/js/launcher.js").get_data(as_text=True)
        terminals = self.client.get("/static/js/terminals.js").get_data(as_text=True)

        shared_helpers = (
            "escHtml",
            "joinDirectories",
            "syncNativeTheme",
            "applyTheme",
            "persistThemePreference",
            "cycleTheme",
            "initTheme",
            "buildSavedSessionTags",
            "buildSavedSessionCard",
            "normalizeThemePreference",
            "getStoredTheme",
            "resolveTheme",
            "buildLaunchDirectory",
            "resolveTerminalDirectory",
            "resolvePaneStartupMode",
            "buildPaneLaunchFields",
        )
        for name in shared_helpers:
            with self.subTest(helper=name):
                self.assertEqual(shared.count(f"function {name}("), 1)
                self.assertNotIn(f"function {name}(", launcher)
                self.assertNotIn(f"function {name}(", terminals)

        # Each page supplies its theme hook and starts the shared init once.
        for page in (launcher, terminals):
            self.assertIn("function onThemeApplied(", page)
            self.assertIn("initTheme();", page)

    def test_saved_session_launch_fields_use_shared_pane_helpers(self):
        shared = self.client.get("/static/js/shared.js").get_data(as_text=True)
        launcher = self.client.get("/static/js/launcher.js").get_data(as_text=True)
        terminals = self.client.get("/static/js/terminals.js").get_data(as_text=True)

        self.assertIn("function resolvePaneStartupMode(terminal)", shared)
        self.assertIn("function buildPaneLaunchFields(terminal, startupMode =", shared)
        for page_name, page in (("launcher", launcher), ("terminals", terminals)):
            with self.subTest(page=page_name):
                self.assertIn("const startupMode = resolvePaneStartupMode(terminal);", page)
                self.assertIn(
                    "} = buildPaneLaunchFields(terminal, startupMode);",
                    page,
                )
                self.assertNotIn("savedStartupMode", page)

    def test_launcher_button_and_dead_display_fixes_locked_in(self):
        """Findings 4.3/4.6 — launch button restores its markup; dead node gone."""
        launcher = self.client.get("/static/js/launcher.js").get_data(as_text=True)
        # 4.6 — the wsl_default_dir_display element no longer exists anywhere.
        self.assertNotIn("wsl_default_dir_display", launcher)
        # 4.3 — the button never renames itself to the old 'Launch Terminals'
        # label; since 8.2 it toggles a .loading class instead of rewriting
        # its markup at all.
        self.assertNotIn("Launch Terminals", launcher)
        self.assertIn("setLaunchButtonLoading", launcher)

    def test_terminals_joins_rooms_for_every_pane(self):
        """Finding 1.1 step 3 — room-scoped session_status requires explorer
        and browser panes to join their session rooms like terminal panes."""
        terminals = self.client.get("/static/js/terminals.js").get_data(as_text=True)
        join_calls = terminals.count("socket.emit('join_session'")
        self.assertGreaterEqual(join_calls, 4)
        # The initial-load join loop must not filter sessions by pane type.
        load_join = terminals[terminals.index("data.sessions.forEach(session => {"):]
        load_join = load_join[:load_join.index("});")]
        self.assertNotIn("isExplorerSession", load_join)
        self.assertNotIn("isBrowserSession", load_join)
        self.assertIn("socket.emit('join_session'", load_join)

    def test_terminals_monster_functions_are_decomposed(self):
        """Finding 6.5 — buildGrid/_startVoice delegate to focused helpers.

        The voice helpers moved to voice-input.js in the 2026-07-23 split, so
        they are asserted against that file; the grid helpers stay in
        terminals.js.
        """
        terminals = self.client.get("/static/js/terminals.js").get_data(as_text=True)
        voice = self.client.get("/static/js/voice-input.js").get_data(as_text=True)

        grid_helpers = (
            "createPaneInstance",
            "wireCardButton",
            "buildPaneCard",
            "wirePaneControls",
            "wirePaneInputForwarding",
        )
        voice_helpers = (
            "_acquireMicStream",
            "_createVoicePipeline",
            "_wireVoiceWorkletMessages",
            "_teardownVoicePipeline",
        )
        for name in grid_helpers:
            with self.subTest(helper=name, file="terminals.js"):
                self.assertEqual(
                    len(re.findall(rf"function {re.escape(name)}\(", terminals)), 1
                )
        for name in voice_helpers:
            with self.subTest(helper=name, file="voice-input.js"):
                self.assertEqual(
                    len(re.findall(rf"function {re.escape(name)}\(", voice)), 1
                )
                self.assertNotIn(f"function {name}(", terminals)

        def _function_length(source: str, header: str) -> int:
            lines = source.splitlines()
            start = next(i for i, line in enumerate(lines) if header in line)
            end = next(
                i for i, line in enumerate(lines[start + 1:], start + 1)
                if line == "    }"
            )
            return end - start + 1

        # The orchestrators must stay thin; the old versions were ~445/~308
        # lines and this is the regression guard against regrowing them.
        self.assertLess(_function_length(terminals, "function buildGrid("), 60)
        self.assertLess(_function_length(voice, "async function _startVoice("), 200)


class VoiceAudioRaceTestCase(unittest.TestCase):
    """Issue 1 — verify voice audio handles ws closure gracefully."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_patch = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH",
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

    @patch("web.voice.emit")
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

    @patch("web.voice.emit")
    def test_voice_audio_dropped_when_no_connection(self, mock_emit):
        api._handle_vosk_audio_chunk("voice-no-conn", b"\x00\x01\x02\x03")

        mock_emit.assert_not_called()


class CorsOriginDefaultsTestCase(unittest.TestCase):
    """Finding 1.1 — Socket.IO CORS must default to same-origin, not '*'."""

    def test_defaults_to_same_origin_when_not_configured(self):
        config = {"security": {"cors_origins": []}, "server": {"host": "127.0.0.1", "port": 5050}}
        with patch.object(api.runtime_config, "app_config", config):
            origins = api._resolve_cors_origins()

        self.assertEqual(origins, ["http://127.0.0.1:5050", "http://localhost:5050"])

    def test_defaults_use_configured_port(self):
        config = {"security": {}, "server": {"host": "localhost", "port": 8080}}
        with patch.object(api.runtime_config, "app_config", config):
            origins = api._resolve_cors_origins()

        self.assertEqual(origins, ["http://127.0.0.1:8080", "http://localhost:8080"])

    def test_non_loopback_host_is_included(self):
        config = {"server": {"host": "192.168.1.20", "port": 5050}}
        with patch.object(api.runtime_config, "app_config", config):
            origins = api._resolve_cors_origins()

        self.assertIn("http://192.168.1.20:5050", origins)

    def test_explicit_configuration_wins(self):
        config = {"security": {"cors_origins": ["https://example.com"]}}
        with patch.object(api.runtime_config, "app_config", config):
            origins = api._resolve_cors_origins()

        self.assertEqual(origins, ["https://example.com"])

    def test_explicit_wildcard_is_honored(self):
        config = {"security": {"cors_origins": ["*"]}}
        with patch.object(api.runtime_config, "app_config", config):
            origins = api._resolve_cors_origins()

        self.assertEqual(origins, ["*"])


class CrossOriginWriteGuardTestCase(unittest.TestCase):
    """Finding 1.2 — state-changing routes must reject cross-origin requests."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path_patch = patch.object(
            web_config, "CONFIG_PATH",
            str(Path(self.temp_dir.name) / "config.json"),
        )
        self.config_path_patch.start()
        self.addCleanup(self.config_path_patch.stop)
        self.saved_sessions_patch = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH",
            str(Path(self.temp_dir.name) / "saved_sessions.json"),
        )
        self.saved_sessions_patch.start()
        self.addCleanup(self.saved_sessions_patch.stop)
        api._refresh_runtime_config()
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()

    def tearDown(self):
        api.session_manager.reset_sessions()
        api._refresh_runtime_config()

    def test_cross_origin_post_is_rejected(self):
        response = self.client.post(
            "/api/sessions",
            json={"connection_mode": "ssh", "sessions": []},
            headers={"Origin": "http://evil.example"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("Cross-origin", response.get_json()["error"])

    def test_cross_origin_explorer_mutations_are_rejected(self):
        for action in ("paste", "delete"):
            with self.subTest(action=action):
                response = self.client.post(
                    f"/api/explorer/missing/{action}",
                    json={},
                    headers={"Origin": "http://evil.example"},
                )
                self.assertEqual(response.status_code, 403)

    def test_cross_origin_delete_is_rejected(self):
        response = self.client.delete(
            "/api/sessions",
            headers={"Origin": "http://evil.example"},
        )

        self.assertEqual(response.status_code, 403)

    def test_null_origin_is_rejected(self):
        response = self.client.delete(
            "/api/sessions",
            headers={"Origin": "null"},
        )

        self.assertEqual(response.status_code, 403)

    def test_same_origin_write_is_allowed(self):
        response = self.client.delete(
            "/api/sessions",
            headers={"Origin": "http://localhost"},
        )

        self.assertEqual(response.status_code, 200)

    def test_loopback_alias_origin_is_allowed(self):
        # Test client host is "localhost"; 127.0.0.1 must count as the same origin.
        response = self.client.delete(
            "/api/sessions",
            headers={"Origin": "http://127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200)

    def test_write_without_origin_header_is_allowed(self):
        response = self.client.delete("/api/sessions")

        self.assertEqual(response.status_code, 200)

    def test_get_requests_are_not_guarded(self):
        response = self.client.get(
            "/api/sessions",
            headers={"Origin": "http://evil.example"},
        )

        self.assertEqual(response.status_code, 200)

    def test_configured_extra_origin_is_allowed(self):
        config = {"security": {"cors_origins": ["http://proxy.example:8443"]}}
        with patch.object(api.runtime_config, "app_config", config):
            response = self.client.delete(
                "/api/sessions",
                headers={"Origin": "http://proxy.example:8443"},
            )

        self.assertEqual(response.status_code, 200)

    def test_configured_wildcard_allows_cross_origin(self):
        config = {"security": {"cors_origins": ["*"]}}
        with patch.object(api.runtime_config, "app_config", config):
            response = self.client.delete(
                "/api/sessions",
                headers={"Origin": "http://evil.example"},
            )

        self.assertEqual(response.status_code, 200)


class KnownHostsPersistenceTestCase(unittest.TestCase):
    """Finding 1.4 — SSH clients load/persist a project-local known_hosts file."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.known_hosts_path = str(Path(self.temp_dir.name) / ".known_hosts")
        self.known_hosts_patch = patch.object(
            web_hostkeys, "KNOWN_HOSTS_PATH", self.known_hosts_path,
        )
        self.known_hosts_patch.start()
        self.addCleanup(self.known_hosts_patch.stop)
        api.session_manager.reset_sessions()

    def tearDown(self):
        api.session_manager.reset_sessions()

    def test_creates_and_loads_known_hosts_file(self):
        client = MagicMock()

        api._load_persistent_host_keys(client)

        self.assertTrue(Path(self.known_hosts_path).exists())
        client.load_host_keys.assert_called_once_with(self.known_hosts_path)

    def test_load_failure_is_non_fatal(self):
        client = MagicMock()
        client.load_host_keys.side_effect = OSError("file locked")

        api._load_persistent_host_keys(client)  # must not raise

    @patch("web.explorer.paramiko")
    def test_open_ssh_sftp_loads_known_hosts(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        session = SimpleNamespace(
            session_id="s1", host="host", port=22, username="u", password="p",
        )

        web_explorer._open_ssh_sftp(session)

        mock_client.load_host_keys.assert_called_once_with(self.known_hosts_path)

    @patch("web.terminal_io.paramiko")
    def test_connect_ssh_session_loads_known_hosts(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.SSHException = type("SSHException", (Exception,), {})
        mock_client.connect.side_effect = OSError("Connection refused")
        session = api.session_manager.create_session(
            group_id="grp-kh", host="127.0.0.1", directory="/tmp",
            username="root", password="pass",
        )

        api._connect_ssh_session(session.session_id, session)

        mock_client.load_host_keys.assert_called_once_with(self.known_hosts_path)


class ConnectCloseToctouTestCase(unittest.TestCase):
    """Finding 2.3 — a close during connect must not leak a live connection."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.known_hosts_patch = patch.object(
            web_hostkeys, "KNOWN_HOSTS_PATH",
            str(Path(self.temp_dir.name) / ".known_hosts"),
        )
        self.known_hosts_patch.start()
        self.addCleanup(self.known_hosts_patch.stop)
        api.session_manager.reset_sessions()
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def tearDown(self):
        api.session_manager.reset_sessions()
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    @patch("web.terminal_io.paramiko")
    def test_connection_discarded_when_session_removed_mid_connect(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.SSHException = type("SSHException", (Exception,), {})
        session = api.session_manager.create_session(
            group_id="grp-race", host="127.0.0.1", directory="/tmp",
            username="root", password="pass",
        )
        channel = MagicMock()

        def invoke_shell(**_kwargs):
            # Simulate a concurrent DELETE landing after the shell is opened
            # but before the connection registry insert.
            with api.session_manager.lock:
                del api.session_manager.sessions[session.session_id]
            return channel

        mock_client.invoke_shell.side_effect = invoke_shell

        api._connect_ssh_session(session.session_id, session)

        with api.connection_lock:
            self.assertNotIn(session.session_id, api.ssh_connections)
            self.assertNotIn(session.session_id, api.session_output_buffers)
        channel.close.assert_called()
        mock_client.close.assert_called()

    @patch("web.terminal_io.paramiko")
    def test_connection_registered_when_session_still_exists(self, mock_paramiko):
        mock_client = MagicMock()
        mock_paramiko.SSHClient.return_value = mock_client
        mock_paramiko.SSHException = type("SSHException", (Exception,), {})
        session = api.session_manager.create_session(
            group_id="grp-ok", host="127.0.0.1", directory="/tmp",
            username="root", password="pass",
        )

        with patch.object(web_terminal_io, "_run_startup_sequence"), \
                patch.object(web_terminal_io, "_stream_ssh_output"):
            api._connect_ssh_session(session.session_id, session)

        with api.connection_lock:
            self.assertIn(session.session_id, api.ssh_connections)
        self.assertEqual(
            api.session_manager.get_session(session.session_id).status,
            api.SessionStatus.CONNECTED,
        )


class EmitOutsideConnectionLockTestCase(unittest.TestCase):
    """Finding 2.4 — terminal output is emitted without holding connection_lock."""

    def setUp(self):
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()
        self.addCleanup(self._clear_state)

    def _clear_state(self):
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def test_stream_ssh_output_emits_without_connection_lock(self):
        session_id = "emit-lock-ssh"

        class FakeChannel:
            def __init__(self):
                self.closed = False
                self._chunks = [b"hello"]

            def settimeout(self, _timeout):
                pass

            def recv(self, _size):
                if self._chunks:
                    return self._chunks.pop(0)
                return b""

            def exit_status_ready(self):
                return True

            def close(self):
                self.closed = True

        with api.connection_lock:
            api.ssh_connections[session_id] = {"kind": "ssh", "channel": FakeChannel()}

        lock_owned_during_emit = []

        def fake_emit(_event, payload, **_kwargs):
            lock_owned_during_emit.append(api.connection_lock._is_owned())

        with patch.object(api.socketio, "emit", side_effect=fake_emit):
            api._stream_ssh_output(session_id)

        self.assertEqual(lock_owned_during_emit, [False])
        self.assertEqual(api._get_buffered_terminal_output(session_id), "")

    def test_drain_until_prompt_emits_without_connection_lock(self):
        session_id = "emit-lock-drain"

        class FakePty:
            def __init__(self):
                self._chunks = ["booted"]

            def read(self, _size):
                return self._chunks.pop(0) if self._chunks else ""

        lock_owned_during_emit = []

        def fake_emit(_event, payload, **_kwargs):
            lock_owned_during_emit.append(api.connection_lock._is_owned())

        with patch.object(api.socketio, "emit", side_effect=fake_emit):
            api._drain_until_prompt(session_id, {"pty_process": FakePty()}, timeout=1.0)

        self.assertEqual(lock_owned_during_emit, [False])
        self.assertEqual(api._get_buffered_terminal_output(session_id), "booted")


class AgentDetectionCacheLockTestCase(unittest.TestCase):
    """Finding 2.5 — slow detection probes run outside the cache lock."""

    def setUp(self):
        with api._agent_detection_cache_lock:
            api._agent_detection_cache.clear()
        self.addCleanup(self._clear_cache)

    def _clear_cache(self):
        with api._agent_detection_cache_lock:
            api._agent_detection_cache.clear()

    def test_probe_runs_unlocked_and_result_is_cached(self):
        target = {
            "environment_key": "windows_native",
            "shell_kind": "cmd",
            "distribution": "",
            "host": "",
            "port": 22,
        }
        lock_states = []

        def fake_probe(_target, _binary):
            lock_states.append(api._agent_detection_cache_lock.locked())
            return {"found": True, "path": "C:/bin/claude"}

        with patch.object(web_agents, "_detect_agent_binary", side_effect=fake_probe) as probe:
            first = api._detect_agent_binary_cached(target, "claude")
            second = api._detect_agent_binary_cached(target, "claude")

        self.assertEqual(lock_states, [False])
        probe.assert_called_once()
        self.assertEqual(first, second)
        self.assertTrue(first["found"])


class VoiceEngineSwitchTestCase(unittest.TestCase):
    """Finding 2.6 — audio/stop route to the engine the recording started with."""

    def setUp(self):
        with api._active_voice_sessions_lock:
            api._active_voice_sessions.clear()
        self.addCleanup(self._clear_state)

    def _clear_state(self):
        with api._active_voice_sessions_lock:
            api._active_voice_sessions.clear()

    @patch("web.api.emit")
    def test_stop_routes_to_engine_recorded_at_start(self, _mock_emit):
        with api.app.test_request_context("/"):
            api.request.sid = "client-1"  # type: ignore[attr-defined]

            with patch.object(api.runtime_config, "voice_enabled", True), \
                    patch.object(api.runtime_config, "voice_engine", "whisper"), \
                    patch.object(api, "_start_whisper_voice_session") as start_whisper:
                api.handle_voice_start({"session_id": "sess-voice"})
            start_whisper.assert_called_once_with("sess-voice")

            # The user switches the engine mid-recording; audio and stop must
            # still route to the engine the recording started with.
            with patch.object(api.runtime_config, "voice_engine", "vosk"), \
                    patch.object(api, "_handle_whisper_audio_chunk") as whisper_audio, \
                    patch.object(api, "_handle_vosk_audio_chunk") as vosk_audio:
                api.handle_voice_audio({"session_id": "sess-voice", "audio": b"pcm"})
            whisper_audio.assert_called_once_with("sess-voice", b"pcm")
            vosk_audio.assert_not_called()

            with patch.object(api.runtime_config, "voice_engine", "vosk"), \
                    patch.object(api, "_stop_whisper_voice_session") as stop_whisper, \
                    patch.object(api, "_stop_vosk_voice_session") as stop_vosk:
                api.handle_voice_stop({"session_id": "sess-voice"})
            stop_whisper.assert_called_once_with("sess-voice")
            stop_vosk.assert_not_called()

        with api._active_voice_sessions_lock:
            self.assertNotIn("sess-voice", api._active_voice_sessions)

    @patch("web.api.emit")
    def test_audio_without_recorded_session_uses_configured_engine(self, _mock_emit):
        with api.app.test_request_context("/"):
            with patch.object(api.runtime_config, "voice_engine", "vosk"), \
                    patch.object(api, "_handle_vosk_audio_chunk") as vosk_audio:
                api.handle_voice_audio({"session_id": "sess-unknown", "audio": b"pcm"})
            vosk_audio.assert_called_once_with("sess-unknown", b"pcm")


class AgentInputTrackingLockTestCase(unittest.TestCase):
    """Finding 2.10 — tracking state mutates under connection_lock and still works."""

    def setUp(self):
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)

    def test_double_interrupt_marks_agent_exited(self):
        session = api.session_manager.create_session(
            group_id="grp-agent", host="local", directory="/tmp",
            startup_mode="agent", agent_selection="claude",
        )
        connection = {}

        with patch.object(web_terminal_io, "_mark_runtime_agent_exited", return_value=True) as mark:
            api._track_terminal_agent_input(session.session_id, connection, "\x03")
            mark.assert_not_called()
            api._track_terminal_agent_input(session.session_id, connection, "\x03")
            mark.assert_called_once_with(session.session_id, "interrupt")

    def test_typed_line_is_reconstructed_across_events(self):
        session = api.session_manager.create_session(
            group_id="grp-line", host="local", directory="/tmp",
        )
        connection = {}

        api._track_terminal_agent_input(session.session_id, connection, "cla")
        api._track_terminal_agent_input(session.session_id, connection, "ude")
        self.assertEqual(connection["_gridvibe_input_line"], "claude")

        with patch.object(web_terminal_io, "_agent_from_terminal_command", return_value=("claude", "claude")), \
                patch.object(web_terminal_io, "_broadcast_session_status"):
            api._track_terminal_agent_input(session.session_id, connection, "\r")

        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "agent")
        self.assertEqual(updated.agent_selection, "claude")
        self.assertEqual(connection["_gridvibe_input_line"], "")


class RuntimeConfigExtractionTestCase(unittest.TestCase):
    """Finding 6.2 — runtime config lives in web/config.py behind RuntimeConfig."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.config_path_patch = patch.object(
            web_config, "CONFIG_PATH", str(self.config_path)
        )
        self.config_path_patch.start()
        self.addCleanup(self.config_path_patch.stop)
        self.addCleanup(api._refresh_runtime_config)

    def _write_config(self, config):
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def test_api_reexports_the_config_module_objects(self):
        self.assertIs(api.load_config, web_config.load_config)
        self.assertIs(api.save_config, web_config.save_config)
        self.assertIs(api.runtime_config, web_config.runtime_config)
        self.assertIs(api.WHISPER_MODEL_OPTIONS, web_config.WHISPER_MODEL_OPTIONS)
        self.assertIs(api._config_lock, web_config._config_lock)

    def test_refresh_runtime_config_reloads_settings_from_disk(self):
        self._write_config(
            {
                "terminal": {"max_sessions": 6},
                "appearance": {"theme": "dark"},
                "voice_input": {"engine": "whisper", "whisper_model": "small"},
            }
        )

        api._refresh_runtime_config()

        self.assertEqual(api.runtime_config.max_sessions, 6)
        self.assertEqual(api.runtime_config.app_theme, "dark")
        self.assertEqual(api.runtime_config.voice_engine, "whisper")
        self.assertEqual(api.runtime_config.whisper_model, "small")

    def test_refresh_normalizes_invalid_values(self):
        self._write_config(
            {
                "appearance": {"theme": "neon"},
                "workspace": {"surface_mode": "gigantic"},
                "voice_input": {
                    "engine": "siri",
                    "whisper_model": "not-a-model",
                    "vosk_startup_timeout_seconds": 5,
                },
            }
        )

        api._refresh_runtime_config()

        self.assertEqual(api.runtime_config.app_theme, "system")
        self.assertEqual(api.runtime_config.app_surface_mode, "normal")
        self.assertEqual(api.runtime_config.voice_engine, "vosk")
        self.assertEqual(api.runtime_config.whisper_model, "base")
        self.assertEqual(api.runtime_config.vosk_startup_timeout_seconds, 30)

    def test_routes_read_the_shared_runtime_config_instance(self):
        api.app.config["TESTING"] = True
        client = api.app.test_client()
        with patch.object(api.runtime_config, "max_sessions", 2):
            response = client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "sessions": [{"directory": "/tmp"}] * 3,
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(), {"error": "Maximum 2 sessions allowed"}
        )


class ExplorerModuleExtractionTestCase(unittest.TestCase):
    """Finding 6.2 — explorer/git backends extracted to web/explorer.py."""

    def test_api_reexports_the_explorer_module_objects(self):
        for name in (
            "_explorer_backend",
            "_acquire_ssh_sftp",
            "_release_ssh_sftp",
            "_evict_pooled_ssh_client",
            "_evict_all_pooled_ssh_clients",
            "_is_explorer_session",
            "_is_remote_explorer_session",
            "_get_git_diff",
            "_get_git_repo_summary",
            "_git_commit",
            "_git_publish",
            "EXPLORER_FILE_PREVIEW_MAX_BYTES",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(web_explorer, name))

    def test_host_keys_helper_is_shared_between_terminal_and_explorer_code(self):
        self.assertIs(
            api._load_persistent_host_keys,
            web_hostkeys._load_persistent_host_keys,
        )
        self.assertIs(
            web_explorer._load_persistent_host_keys,
            web_hostkeys._load_persistent_host_keys,
        )


class FinalModuleSplitTestCase(unittest.TestCase):
    """Finding 6.2 — final tranches: app/saved_sessions/agents/terminal_io/voice."""

    def test_app_module_owns_the_flask_and_socketio_singletons(self):
        self.assertIs(api.app, web_app.app)
        self.assertIs(api.socketio, web_app.socketio)
        self.assertIs(api.session_manager, web_app.session_manager)
        self.assertIs(api._resolve_cors_origins, web_app._resolve_cors_origins)
        self.assertIs(api._reject_cross_origin_writes, web_app._reject_cross_origin_writes)

    def test_api_reexports_the_saved_sessions_module_objects(self):
        for name in (
            "SAVED_SESSIONS_PATH",
            "DEFAULT_SAVED_SESSION_ID",
            "DEFAULT_BROWSER_URL",
            "_normalize_session_config",
            "_merge_workspace_session_config",
            "_load_saved_sessions_payload",
            "load_session_config",
            "load_saved_sessions",
            "save_saved_sessions",
            "upsert_saved_session",
            "delete_saved_sessions",
            "set_last_saved_session",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(web_saved_sessions, name))

    def test_api_reexports_the_agents_module_objects(self):
        for name in (
            "AGENT_REGISTRY",
            "AGENT_REGISTRY_PATH",
            "_agent_detection_cache",
            "_agent_detection_cache_lock",
            "_agent_options",
            "_agent_preflight_payload",
            "_detect_agent_binary_cached",
            "_find_wsl_executable",
            "_inspect_wsl_distributions",
            "_ping_ssh_target",
            "_powershell_single_quote",
            "_sanitize_agent_launch_commands",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(web_agents, name))

    def test_api_reexports_the_terminal_io_module_objects(self):
        for name in (
            "ssh_connections",
            "session_output_buffers",
            "client_joined_sessions",
            "connection_lock",
            "TERMINAL_OUTPUT_BUFFER_MAX_CHARS",
            "SSH_STREAM_RECV_TIMEOUT",
            "WINDOWS_DEVICE_ATTRIBUTES_RESPONSE",
            "_broadcast_session_status",
            "_broadcast_session_groups_updated",
            "_cache_terminal_output",
            "_close_ssh_connection",
            "_close_all_ssh_connections",
            "_connect_session",
            "_replace_group_sessions",
            "_run_startup_sequence",
            "_send_connection_input",
            "_stream_ssh_output",
            "_stream_local_output",
            "_track_terminal_agent_input",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(web_terminal_io, name))

    def test_api_reexports_the_voice_module_objects(self):
        for name in (
            "VOICE_PREFS_VALID_KEYS",
            "_active_voice_sessions",
            "_active_voice_sessions_lock",
            "_vosk_ws_connections",
            "_vosk_lock",
            "_whisper_audio_buffers",
            "_ensure_vosk_service",
            "_stop_vosk_service",
            "_ensure_whisper_model",
            "_start_vosk_voice_session",
            "_start_whisper_voice_session",
            "_stop_vosk_voice_session",
            "_stop_whisper_voice_session",
            "_vosk_engine_available",
            "_whisper_engine_available",
            "_load_voice_prefs",
            "_save_voice_prefs",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(web_voice, name))

    def test_moved_functions_are_defined_in_their_new_modules(self):
        for func, module_name in (
            (api._normalize_session_config, "web.saved_sessions"),
            (api._agent_preflight_payload, "web.agents"),
            (api._connect_ssh_session, "web.terminal_io"),
            (api._start_vosk_voice_session, "web.voice"),
            (api._reject_cross_origin_writes, "web.app"),
        ):
            with self.subTest(module=module_name):
                self.assertEqual(func.__module__, module_name)

    def test_entry_point_contract_still_importable_from_web_api(self):
        # main.py and web/webview_launcher.py import exactly these names.
        for name in (
            "app",
            "socketio",
            "session_manager",
            "load_config",
            "configure_browser_shutdown",
            "_stop_vosk_service",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(api, name))


class DeadCodeSweepTestCase(unittest.TestCase):
    """Deep-dive step 7 — dead-code sweep (findings 5.1–5.6) and 10.2 font wiring."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    # ── 5.2: /api/sessions/active endpoint removed ─────────────────────────

    def test_sessions_active_endpoint_removed(self):
        response = self.client.get("/api/sessions/active")
        self.assertEqual(response.status_code, 404)

    # ── 5.3: SessionManager callback registry removed ──────────────────────

    def test_session_manager_has_no_callback_registry(self):
        from sessions.manager import SessionManager
        mgr = SessionManager()
        self.assertFalse(hasattr(mgr, "_session_callbacks"))
        self.assertFalse(hasattr(mgr, "register_callback"))
        self.assertFalse(hasattr(mgr, "_notify_callbacks"))

    # ── 5.4: get_active_session_count removed ──────────────────────────────

    def test_get_active_session_count_removed(self):
        from sessions.manager import SessionManager
        self.assertFalse(hasattr(SessionManager, "get_active_session_count"))

    # ── 5.4: section-title SVGs removed from launcher ──────────────────────

    def test_section_title_svgs_removed_from_launcher(self):
        html = self.client.get("/").get_data(as_text=True)
        import re as _re
        section_title_blocks = _re.findall(
            r'<div class="section-title">.*?</div>', html, _re.DOTALL
        )
        for block in section_title_blocks:
            self.assertNotIn("<svg", block,
                             "section-title should not contain hidden SVGs")

    # ── 5.6: t-menu-btn removed from launcher card HTML ────────────────────

    def test_t_menu_btn_removed(self):
        launcher_js = self.client.get("/static/js/launcher.js").get_data(as_text=True)
        self.assertNotIn("t-menu-btn", launcher_js)
        launcher_css = self.client.get("/static/css/launcher.css").get_data(as_text=True)
        self.assertNotIn(".t-menu-btn", launcher_css)

    # ── 10.2: terminal font settings wired through to terminals page ────────

    def test_terminal_font_settings_in_terminals_page_body(self):
        orig_size = api.runtime_config.terminal_font_size
        orig_family = api.runtime_config.terminal_font_family
        api.runtime_config.terminal_font_size = 18
        api.runtime_config.terminal_font_family = "JetBrains Mono, monospace"
        try:
            html = self.client.get("/terminals").get_data(as_text=True)
        finally:
            api.runtime_config.terminal_font_size = orig_size
            api.runtime_config.terminal_font_family = orig_family
        self.assertIn('data-terminal-font-size="18"', html)
        self.assertIn("JetBrains Mono", html)

    def test_makeTerminal_reads_font_from_dataset(self):
        terminals_js = self.client.get("/static/js/terminals.js").get_data(as_text=True)
        # Dataset properties are read.
        self.assertIn("terminalFontSize", terminals_js)
        self.assertIn("terminalFontFamily", terminals_js)
        # The old hardcoded literals are gone.
        self.assertNotIn("fontSize      : 13", terminals_js)


class StyleThemingTestCase(unittest.TestCase):
    """Deep-dive step 8 — style/theming (findings 7.1, 7.2, 7.3)."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    # ── 7.1: shared design tokens ───────────────────────────────────────────

    def test_tokens_stylesheet_defines_shared_palette(self):
        tokens = self._static("css/tokens.css")
        for token in (
            "--gv-bg-app", "--gv-accent", "--gv-accent-hover", "--gv-accent-soft",
            "--gv-success", "--gv-success-hover", "--gv-danger", "--gv-warning",
            "--gv-radius-s", "--gv-radius-m", "--gv-radius-l",
        ):
            self.assertIn(f"{token}:", tokens)
        self.assertIn('[data-theme="light"]', tokens)

    def test_both_pages_load_tokens_before_their_page_stylesheet(self):
        for route, page_css in (("/", "css/launcher.css"),
                                ("/terminals", "css/terminals.css")):
            html = self.client.get(route).get_data(as_text=True)
            self.assertIn("css/tokens.css", html)
            self.assertLess(html.index("css/tokens.css"), html.index(page_css))

    def test_page_palettes_map_onto_shared_tokens(self):
        launcher_css = self._static("css/launcher.css")
        for mapping in ("--accent: var(--gv-accent)",
                        "--accent-strong: var(--gv-accent-hover)",
                        "--danger: var(--gv-danger)",
                        "--success: var(--gv-success)",
                        "--warning: var(--gv-warning)"):
            self.assertIn(mapping, launcher_css)
        terminals_css = self._static("css/terminals.css")
        for mapping in ("--t-bg: var(--gv-bg-app)",
                        "--t-accent: var(--gv-accent)",
                        "--t-accent-hover: var(--gv-accent-hover)",
                        "--t-success: var(--gv-success)",
                        "--t-success-hover: var(--gv-success-hover)"):
            self.assertIn(mapping, terminals_css)

    def test_old_divergent_palette_literals_are_gone(self):
        launcher_css = self._static("css/launcher.css")
        self.assertNotIn("#4cc9f0", launcher_css)
        self.assertNotIn("rgba(76, 201, 240", launcher_css)
        terminals_css = self._static("css/terminals.css")
        self.assertNotIn("#18b66a", terminals_css)
        self.assertNotIn("#14955a", terminals_css)

    def test_terminal_canvas_stays_dark_in_light_theme(self):
        terminals_css = self._static("css/terminals.css")
        light_block = terminals_css.split('[data-theme="light"]', 1)[1]
        for pinned in ("--t-terminal-bg: #0d0d0d",
                       "--t-terminal-fg: #e0e0e0",
                       "--t-terminal-cursor: #00d9ff"):
            self.assertIn(pinned, light_block)

    # ── 7.2: one SVG icon language instead of emoji/text glyphs ─────────────

    def test_launcher_page_has_no_emoji_button_glyphs(self):
        html = self.client.get("/").get_data(as_text=True)
        for glyph in ("\U0001f4be", "\U0001f4c2", "\U0001f5d1", "\U0001f319", "↻"):
            self.assertNotIn(glyph, html)

    def test_terminals_page_has_no_emoji_or_text_glyph_buttons(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertNotIn("\U0001f319", html)
        self.assertNotIn("&#9974;", html)
        terminals_js = self._static("js/terminals.js")
        self.assertNotIn("\U0001f9f9", terminals_js)
        self.assertNotIn("↻", terminals_js)
        self.assertNotIn("&#9974;", terminals_js)
        for icon in ("TERMINAL_REFRESH_ICON", "TERMINAL_CLEAR_ICON",
                     "FULLSCREEN_ENTER_ICON", "FULLSCREEN_EXIT_ICON"):
            self.assertIn(icon, terminals_js)

    def test_theme_toggle_uses_shared_svg_helper(self):
        shared_js = self._static("js/shared.js")
        self.assertIn("function themeToggleButtonHtml", shared_js)
        self.assertIn("theme-toggle-icon", shared_js)
        for page_js in ("js/launcher.js", "js/terminals.js"):
            body = self._static(page_js)
            self.assertIn("themeToggleButtonHtml", body)
            self.assertNotIn("\U0001f319", body)

    # ── 7.3: theme-ignoring hardcoded colors replaced with tokens ───────────

    def test_settings_window_icon_uses_current_color(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        for literal in ("#06263a", "#5eefff", "#63f6ff", "#6dfcff", "#4fd6ff"):
            self.assertNotIn(literal, html)
        terminals_css = self._static("css/terminals.css")
        block = re.search(r"\.settings-window-btn \{.*?\}", terminals_css,
                          re.DOTALL).group(0)
        self.assertIn("color: var(--t-accent)", block)

    def test_browser_close_button_uses_danger_token(self):
        launcher_css = self._static("css/launcher.css")
        for literal in ("#ff8a94", "#ff6b75", "rgba(181, 35, 49"):
            self.assertNotIn(literal, launcher_css)
        block = re.search(r"\.browser-close-btn \{.*?\}", launcher_css,
                          re.DOTALL).group(0)
        self.assertIn("var(--danger)", block)

    def test_tooltip_arrow_follows_bubble_token(self):
        launcher_css = self._static("css/launcher.css")
        self.assertNotIn("rgba(16, 21, 39", launcher_css)
        # The tooltip moved into the shared App Settings stylesheet, where the
        # arrow and the bubble read the same theme-aware token instead of one
        # literal per theme.
        settings_css = self._static("css/app-settings.css")
        self.assertNotIn("rgba(16, 21, 39", settings_css)
        self.assertIn("background: var(--gv-dialog-tooltip-bg);", settings_css)
        self.assertIn(
            "border-color: var(--gv-dialog-tooltip-bg) transparent", settings_css
        )

    def test_xterm_theme_derived_from_css_variables(self):
        terminals_js = self._static("js/terminals.js")
        for var_name in ("--t-terminal-bg", "--t-terminal-fg",
                         "--t-terminal-cursor", "--t-terminal-selection"):
            self.assertIn(var_name, terminals_js)
        self.assertNotIn("background          : '#0d0d0d'", terminals_js)
        self.assertNotIn("cursor              : '#00d9ff'", terminals_js)


class ThemeSyncTestCase(unittest.TestCase):
    """ISSUE-2026-021 — appearance theme changes reach open session windows."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    # ── backend contract ─────────────────────────────────────────────────────

    def test_backend_broadcast_carries_theme_alongside_surface_mode(self):
        cfg = api.load_config()
        saved_appearance = json.loads(json.dumps(cfg.get("appearance", {})))
        saved_workspace = json.loads(json.dumps(cfg.get("workspace", {})))
        try:
            with patch.object(api.socketio, "emit") as emit:
                response = self.client.post(
                    "/api/app-config",
                    json={
                        "appearance": {"theme": "dark"},
                        "workspace": {"surface_mode": "normal"},
                    },
                )
            self.assertEqual(response.status_code, 200)
            emit.assert_called_once()
            event, payload = emit.call_args[0]
            self.assertEqual(event, "app_config_updated")
            self.assertEqual(payload["appearance"]["theme"], "dark")
            self.assertEqual(payload["workspace"]["surface_mode"], "normal")
        finally:
            cfg = api.load_config()
            cfg["appearance"] = saved_appearance
            cfg["workspace"] = saved_workspace
            api.save_config(cfg)
            api._refresh_runtime_config()

    # ── launcher-side contract ───────────────────────────────────────────────

    def test_launcher_notification_payload_includes_theme(self):
        app_settings_js = self._static("js/app-settings.js")
        notify = app_settings_js[
            app_settings_js.index("function notifyAppConfigUpdated(appSettings"):
            app_settings_js.index("async function loadAppSettings()")
        ]
        self.assertIn(
            "theme: normalizeThemePreference(appSettings?.appearance?.theme)",
            notify,
        )
        self.assertIn(
            "surface_mode: appSettings?.workspace?.surface_mode === 'max' ? 'max' : 'normal'",
            notify,
        )

    # ── session-side application ─────────────────────────────────────────────

    def test_terminals_app_config_handler_applies_theme_and_surface_mode(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function applyAppConfigUpdate(message)", terminals_js)
        self.assertIn("applyAppConfigTheme(message);", terminals_js)
        self.assertIn("applyAppConfigSurfaceMode(message);", terminals_js)

    def test_terminals_wires_all_three_delivery_paths_to_the_handler(self):
        terminals_js = self._static("js/terminals.js")
        # BroadcastChannel
        self.assertIn("applyAppConfigUpdate(event.data || {});", terminals_js)
        # storage event
        self.assertIn("applyAppConfigUpdate(JSON.parse(event.newValue));", terminals_js)
        # Socket.IO
        self.assertIn("socket.on('app_config_updated'", terminals_js)
        self.assertIn("applyAppConfigUpdate(message || {});", terminals_js)
        # the old surface-only wiring must not linger on any path
        self.assertNotIn("applyAppConfigSurfaceMode(event.data || {});", terminals_js)
        self.assertNotIn("applyAppConfigSurfaceMode(message || {});", terminals_js)

    def test_theme_application_is_validated_and_idempotent(self):
        terminals_js = self._static("js/terminals.js")
        theme_fn = terminals_js[
            terminals_js.index("function applyAppConfigTheme(message)"):
            terminals_js.index("function applyAppConfigUpdate(message)")
        ]
        self.assertIn("['system', 'light', 'dark'].includes(theme)", theme_fn)
        self.assertIn("data-theme-preference", theme_fn)
        self.assertIn("if (theme !== current)", theme_fn)
        self.assertIn("applyTheme(theme);", theme_fn)

    def test_terminals_reconciles_theme_on_reconnect_focus_and_pageshow(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("async function reconcileAppConfig()", terminals_js)
        reconcile_fn = terminals_js[
            terminals_js.index("async function reconcileAppConfig()"):
            terminals_js.index("function setupAppConfigUpdateListeners()")
        ]
        self.assertIn("fetch('/api/app-config')", reconcile_fn)
        self.assertIn("applyAppConfigTheme(data);", reconcile_fn)
        # Surface mode reconciles through the change-only path, so a recovery
        # fetch never discards this window's own surface toggle.
        self.assertIn("applyConfiguredSurfaceMode(", reconcile_fn)
        self.assertNotIn("applyAppConfigSurfaceMode(", reconcile_fn)
        # reconnect path (guarded by the not-first-connect flag)
        connect_handler = terminals_js[
            terminals_js.index("let hadSocketConnection = false;"):
            terminals_js.index("socket.on('voice_result'")
        ]
        self.assertIn("reconcileAppConfig();", connect_handler)
        # focus / pageshow recovery
        focus_wiring = terminals_js[
            terminals_js.index("window.addEventListener('focus'"):
            terminals_js.index("document.addEventListener('fullscreenchange'")
        ]
        self.assertEqual(focus_wiring.count("reconcileAppConfig();"), 2)

    def test_shared_init_theme_reacts_to_cross_window_storage_writes(self):
        shared_js = self._static("js/shared.js")
        init_theme = shared_js[
            shared_js.index("function initTheme()"):
            shared_js.index("function buildSavedSessionTags(")
        ]
        self.assertIn("event.key !== THEME_STORAGE_KEY", init_theme)
        self.assertIn("normalizeThemePreference(event.newValue)", init_theme)
        # loop-safety guard: only apply when the preference actually changed
        self.assertIn(
            "preference !== document.documentElement.getAttribute('data-theme-preference')",
            init_theme,
        )
        # System-mode media-query behaviour must survive the new listener
        self.assertIn("prefers-color-scheme: light", init_theme)


class VoiceRecordingOverlayTestCase(unittest.TestCase):
    """ISSUE-2026-019 — floating waveform indicator while voice is recording."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_overlay_markup_is_accessible_and_singleton(self):
        # Voice code moved to voice-input.js in the 2026-07-23 split.
        voice_js = self._static("js/voice-input.js")
        self.assertIn("const VOICE_OVERLAY_ID = 'voiceRecordingOverlay';", voice_js)
        show_fn = voice_js[
            voice_js.index("function _showVoiceRecordingOverlay(index)"):
            voice_js.index("function _hideVoiceRecordingOverlay()")
        ]
        self.assertIn("overlay.setAttribute('role', 'status');", show_fn)
        self.assertIn("overlay.setAttribute('aria-live', 'polite');", show_fn)
        self.assertIn('aria-hidden="true"', show_fn)
        self.assertIn(">Recording</span>", show_fn)
        # reuse the single element instead of stacking duplicates
        self.assertIn("if (!overlay) {", show_fn)
        self.assertIn("document.getElementById(VOICE_OVERLAY_ID)", show_fn)

    def test_overlay_appears_only_after_capture_actually_starts(self):
        voice_js = self._static("js/voice-input.js")
        show_fn = voice_js[
            voice_js.index("function _showVoiceRecordingOverlay(index)"):
            voice_js.index("function _hideVoiceRecordingOverlay()")
        ]
        # rapid-release guard: no overlay when the state was torn down mid-start
        self.assertIn("if (!_voiceState[index]?.recording) {", show_fn)
        start_fn = voice_js[
            voice_js.index("async function _startVoice(index)"):
            voice_js.index("async function _acquireMicStream(")
        ]
        self.assertLess(
            start_fn.index("_voiceState[index] = state;"),
            start_fn.index("_showVoiceRecordingOverlay(index);"),
        )

    def test_overlay_hidden_from_every_cleanup_path(self):
        voice_js = self._static("js/voice-input.js")
        # start failure (permission denial / backend error)
        start_fn = voice_js[
            voice_js.index("async function _startVoice(index)"):
            voice_js.index("async function _acquireMicStream(")
        ]
        catch_block = start_fn[start_fn.index("} catch (err) {"):]
        self.assertIn("_hideVoiceRecordingOverlay();", catch_block)
        # every stop path (toggle, PTT release, hold release, voice_status
        # error, _stopAllVoice on session switch/teardown) funnels here
        stop_fn = voice_js[
            voice_js.index("async function _stopVoice(index"):
            voice_js.index("async function _stopAllVoice()")
        ]
        self.assertIn("_hideVoiceRecordingOverlay();", stop_fn)
        # _stopAllVoice() is invoked from the teardown paths in terminals.js.
        terminals_js = self._static("js/terminals.js")
        self.assertIn("_stopAllVoice();", terminals_js)

    def test_waveform_uses_analyser_with_fallback_and_stale_loop_guard(self):
        terminals_js = self._static("js/voice-input.js")
        animation_fn = terminals_js[
            terminals_js.index("function _startVoiceOverlayAnimation(index, overlay)"):
            terminals_js.index("function _stopVoiceOverlayAnimation()")
        ]
        self.assertIn("createAnalyser()", animation_fn)
        self.assertIn("getByteFrequencyData(data)", animation_fn)
        self.assertIn("requestAnimationFrame(tick)", animation_fn)
        # stale loops from a superseded capture must stop themselves
        self.assertIn("_voiceOverlayAnimation !== animation", animation_fn)
        # deterministic fallback when no analyser is available
        self.assertIn("voice-overlay-fallback", animation_fn)
        stop_animation_fn = terminals_js[
            terminals_js.index("function _stopVoiceOverlayAnimation()"):
            terminals_js.index("const VOICE_HOLD_TO_TALK_MS")
        ]
        self.assertIn("cancelAnimationFrame", stop_animation_fn)
        self.assertIn("animation.source.disconnect(animation.analyser);", stop_animation_fn)

    def test_reduced_motion_disables_the_animations(self):
        voice_js = self._static("js/voice-input.js")
        self.assertIn("function _prefersReducedMotion()", voice_js)
        self.assertIn("prefers-reduced-motion: reduce", voice_js)
        terminals_css = self._static("css/terminals.css")
        reduced_block = terminals_css[
            terminals_css.index("@media (prefers-reduced-motion: reduce)"):
        ]
        self.assertIn("animation: none !important;", reduced_block)

    def test_overlay_styles_are_fixed_nonblocking_and_theme_aware(self):
        terminals_css = self._static("css/terminals.css")
        overlay_block = re.search(
            r"\.voice-recording-overlay \{.*?\}", terminals_css, re.DOTALL
        ).group(0)
        self.assertIn("position: fixed;", overlay_block)
        self.assertIn("pointer-events: none;", overlay_block)
        self.assertIn("var(--gv-danger)", overlay_block)
        self.assertIn("var(--t-voice-bg)", overlay_block)
        # centered over the workspace, with a slight pulse
        self.assertIn("top: 50%;", overlay_block)
        self.assertIn("left: 50%;", overlay_block)
        self.assertIn("transform: translate(-50%, -50%);", overlay_block)
        self.assertIn("animation: voice-overlay-pulse", overlay_block)
        self.assertIn("@keyframes voice-overlay-pulse", terminals_css)
        self.assertIn(".voice-overlay-bar", terminals_css)
        self.assertIn("@keyframes voice-overlay-bounce", terminals_css)

    def test_hold_to_talk_pointer_wiring_preserves_click_toggle(self):
        # The wiring call site stays in terminals.js; the helper moved to
        # voice-input.js (2026-07-23 split).
        terminals_js = self._static("js/terminals.js")
        self.assertIn("_wireVoiceHoldToTalk(card, i);", terminals_js)
        voice_js = self._static("js/voice-input.js")
        hold_fn = voice_js[
            voice_js.index("function _wireVoiceHoldToTalk(card, index)"):
            voice_js.index("/* ── Push-to-talk ── */")
        ]
        self.assertIn("addEventListener('pointerdown'", hold_fn)
        self.assertIn("addEventListener('pointerup', endHold);", hold_fn)
        self.assertIn("addEventListener('pointercancel', endHold);", hold_fn)
        self.assertIn("setPointerCapture(event.pointerId);", hold_fn)
        self.assertIn("VOICE_HOLD_TO_TALK_MS", hold_fn)
        # a completed hold swallows the trailing click so it cannot re-toggle
        self.assertIn("suppressClick", hold_fn)
        self.assertIn("event.stopPropagation();", hold_fn)
        # release during the async start stops capture once it settles
        self.assertIn("holdStopRequested = true;", hold_fn)

    def test_push_to_talk_rapid_release_cannot_leave_a_stale_recording(self):
        # The PTT state and helpers moved to voice-input.js; the key listeners
        # (top-level executing statements) stay in terminals.js.
        voice_js = self._static("js/voice-input.js")
        self.assertIn("let _pttStopRequested = false;", voice_js)
        terminals_js = self._static("js/terminals.js")
        keydown_idx = terminals_js.index(
            "if (!_voicePrefs.pttEnabled || !_voicePrefs.pttKeybind) return;"
        )
        keyup_idx = terminals_js.index("if (!_pttActive) return;")
        keydown_block = terminals_js[keydown_idx:keyup_idx]
        self.assertIn(
            "if (_pttStopRequested && _voiceState[index]?.recording) {", keydown_block
        )
        keyup_block = terminals_js[
            keyup_idx:terminals_js.index("function _showTermCtxMenu(x, y, index)")
        ]
        self.assertIn("_pttStopRequested = true;", keyup_block)


class UxInteractionButtonsTestCase(unittest.TestCase):
    """Deep-dive step 9 — UX/interaction gaps (findings 8.1–8.5)."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _make_session(self, status, error_message=None):
        api.session_manager.create_group(
            name="Retry",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-retry",
        )
        session = api.session_manager.create_session(
            group_id="group-retry",
            host="10.0.0.5",
            directory="/srv",
        )
        api.session_manager.update_session_status(session.session_id, status, error_message)
        return session

    # ── 8.4: POST /api/sessions/<id>/reconnect ──────────────────────────────

    def test_reconnect_unknown_session_returns_404(self):
        response = self.client.post("/api/sessions/missing/reconnect")
        self.assertEqual(response.status_code, 404)

    def test_reconnect_rejects_sessions_that_are_not_errored_or_disconnected(self):
        session = self._make_session(api.SessionStatus.CONNECTED)
        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post(f"/api/sessions/{session.session_id}/reconnect")
        self.assertEqual(response.status_code, 409)
        self.assertIn("connected", response.get_json()["error"])
        start_task.assert_not_called()
        self.assertEqual(
            api.session_manager.get_session(session.session_id).status,
            api.SessionStatus.CONNECTED,
        )

    def test_reconnect_resets_errored_session_and_restarts_connect(self):
        session = self._make_session(api.SessionStatus.ERROR, "Authentication failed")
        api._cache_terminal_output(session.session_id, "stale output")
        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post(f"/api/sessions/{session.session_id}/reconnect")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "pending")
        self.assertIsNone(body["error_message"])
        self.assertEqual(api._get_buffered_terminal_output(session.session_id), "")
        start_task.assert_called_once_with(api._connect_session, session.session_id)

    def test_reconnect_accepts_disconnected_sessions(self):
        session = self._make_session(api.SessionStatus.DISCONNECTED)
        with patch.object(api.socketio, "start_background_task") as start_task:
            response = self.client.post(f"/api/sessions/{session.session_id}/reconnect")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "pending")
        start_task.assert_called_once_with(api._connect_session, session.session_id)

    # ── 8.4 frontend: retry affordance in error/disconnected placeholders ───

    def test_placeholders_offer_retry_and_call_reconnect_endpoint(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function showPlaceholderDisconnected", terminals_js)
        self.assertIn("async function retrySessionConnection(index)", terminals_js)
        self.assertIn("/reconnect", terminals_js)
        self.assertIn("ph-retry-btn", terminals_js)
        # explorer/browser panes have no live connection, so no retry overlay
        self.assertIn("function isRetryableDisconnect(session)", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".placeholder .ph-retry-btn", terminals_css)
        self.assertIn("pointer-events: auto", terminals_css)
        self.assertIn(".placeholder.ph-disconnected", terminals_css)

    # ── 8.1: closing a session tab asks for confirmation ────────────────────

    def test_terminals_page_ships_close_session_confirm_modal(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn('id="closeSessionConfirmModal"', html)
        self.assertIn('id="closeSessionConfirmAccept"', html)
        self.assertIn('id="closeSessionConfirmCancel"', html)

    def test_close_session_group_gates_on_confirmation(self):
        terminals_js = self._static("js/terminals.js")
        close_fn = terminals_js[
            terminals_js.index("async function closeSessionGroup"):
            terminals_js.index("async function _closeWindowAfterLastSession")
        ]
        self.assertIn("await confirmCloseSessionGroup(groupId)", close_fn)
        confirm_fn = terminals_js[
            terminals_js.index("async function confirmCloseSessionGroup"):
            terminals_js.index("function buildSavedSessionLaunchPayload")
        ]
        # groups with no connected terminals close without the dialog
        self.assertIn("session.status === 'connected'", confirm_fn)
        self.assertIn("connectedCount === 0", confirm_fn)
        # Escape / backdrop / Cancel all resolve to "keep the session"
        self.assertIn("closeCloseSessionConfirmModal(false)", terminals_js)
        self.assertIn("closeCloseSessionConfirmModal(true)", terminals_js)

    # ── 8.2: launch CTA keeps its structure and gains a spinner ─────────────

    def test_launch_button_uses_loading_class_not_text_mutation(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('class="action-btn-label"', html)
        self.assertIn('class="action-btn-spinner"', html)
        launcher_js = self._static("js/launcher.js")
        self.assertIn("function setLaunchButtonLoading(button, loading)", launcher_js)
        self.assertNotIn("button.textContent = 'Launching...'", launcher_js)
        self.assertNotIn("originalButtonHtml", launcher_js)
        launcher_css = self._static("css/launcher.css")
        self.assertIn(".action-btn.loading .arrow { display: none; }", launcher_css)
        self.assertIn(".action-btn.loading .action-btn-spinner", launcher_css)

    # ── 8.3: one update-status area with an auto-clear ──────────────────────

    def test_update_status_renders_in_one_place_and_auto_clears(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="updateStatus"', html)
        self.assertIn('id="quickUpdateStatus"', html)
        launcher_js = self._static("js/launcher.js")
        self.assertNotIn("getElementById('updateStatus')", launcher_js)
        set_fn = launcher_js[
            launcher_js.index("function setUpdateStatus"):
            launcher_js.index("function shortCommit")
        ]
        self.assertIn("quickUpdateStatus", set_fn)
        self.assertIn("6000", set_fn)
        launcher_css = self._static("css/launcher.css")
        self.assertNotIn(".toolbar-status", launcher_css)

    # ── 8.5: save-settings button keeps only the custom tooltip ─────────────

    def test_save_settings_button_has_single_tooltip(self):
        html = self.client.get("/").get_data(as_text=True)
        btn_start = html.index('id="saveAppSettingsBtn"')
        button_tag = html[html.rindex("<button", 0, btn_start):html.index(">", btn_start) + 1]
        self.assertNotIn("title=", button_tag)
        self.assertIn('aria-describedby="saveAppSettingsTip"', button_tag)
        self.assertIn('id="saveAppSettingsTip"', html)



class HostKeyPolicyTestCase(unittest.TestCase):
    """Deep-dive 10.7 — configurable SSH host-key verification policy."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.json"
        patcher = patch.object(web_config, "CONFIG_PATH", str(self.config_path))
        patcher.start()
        self.addCleanup(patcher.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        self.known_hosts = Path(self.temp_dir.name) / ".known_hosts"
        kh_patcher = patch.object(web_hostkeys, "KNOWN_HOSTS_PATH", str(self.known_hosts))
        kh_patcher.start()
        self.addCleanup(kh_patcher.stop)

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_runtime_config_normalizes_host_key_policy(self):
        for raw, expected in (
            ("strict", "strict"),
            ("KNOWN-HOSTS", "known-hosts"),
            ("auto-add", "auto-add"),
            ("nonsense", "auto-add"),
            (None, "auto-add"),
        ):
            with self.subTest(raw=raw):
                ssh_config = {} if raw is None else {"host_key_policy": raw}
                self.config_path.write_text(json.dumps({"ssh": ssh_config}), encoding="utf-8")
                api._refresh_runtime_config()
                self.assertEqual(api.runtime_config.ssh_host_key_policy, expected)

    def test_default_config_ships_auto_add_policy(self):
        default_config = json.loads(
            Path(web_config.DEFAULT_CONFIG_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(default_config["ssh"]["host_key_policy"], "auto-add")

    def test_auto_add_policy_keeps_todays_behaviour(self):
        client = MagicMock()
        fake_paramiko = MagicMock()
        api._apply_host_key_policy(client, fake_paramiko, "auto-add")
        client.set_missing_host_key_policy.assert_called_once_with(
            fake_paramiko.AutoAddPolicy.return_value
        )
        client.load_host_keys.assert_called_once_with(str(self.known_hosts))
        client.load_system_host_keys.assert_not_called()

    def test_known_hosts_policy_warns_then_delegates_to_auto_add(self):
        client = MagicMock()
        fake_paramiko = MagicMock()
        api._apply_host_key_policy(client, fake_paramiko, "known-hosts")
        policy = client.set_missing_host_key_policy.call_args[0][0]
        self.assertIsInstance(policy, web_hostkeys._WarnNewHostKeyPolicy)
        with self.assertLogs("web.hostkeys", level="WARNING") as logs:
            policy.missing_host_key(client, "host.example", object())
        self.assertTrue(any("host.example" in line for line in logs.output))
        fake_paramiko.AutoAddPolicy.return_value.missing_host_key.assert_called_once()

    def test_strict_policy_rejects_unknown_hosts_and_loads_user_known_hosts(self):
        user_known_hosts = Path(self.temp_dir.name) / "user_known_hosts"
        user_known_hosts.write_text("", encoding="utf-8")
        ukh_patcher = patch.object(
            web_hostkeys, "USER_KNOWN_HOSTS_PATH", str(user_known_hosts)
        )
        ukh_patcher.start()
        self.addCleanup(ukh_patcher.stop)
        client = MagicMock()
        fake_paramiko = MagicMock()
        api._apply_host_key_policy(client, fake_paramiko, "strict")
        client.set_missing_host_key_policy.assert_called_once_with(
            fake_paramiko.RejectPolicy.return_value
        )
        client.load_host_keys.assert_called_once_with(str(self.known_hosts))
        client.load_system_host_keys.assert_called_once_with(str(user_known_hosts))

    def test_policy_defaults_to_runtime_config_value(self):
        self.config_path.write_text(
            json.dumps({"ssh": {"host_key_policy": "strict"}}), encoding="utf-8"
        )
        api._refresh_runtime_config()
        client = MagicMock()
        fake_paramiko = MagicMock()
        missing_user_path = str(Path(self.temp_dir.name) / "missing")
        with patch.object(web_hostkeys, "USER_KNOWN_HOSTS_PATH", missing_user_path):
            api._apply_host_key_policy(client, fake_paramiko)
        client.set_missing_host_key_policy.assert_called_once_with(
            fake_paramiko.RejectPolicy.return_value
        )
        client.load_system_host_keys.assert_not_called()

    def test_all_three_ssh_entry_points_share_the_policy_helper(self):
        self.assertIs(
            web_terminal_io._apply_host_key_policy,
            web_hostkeys._apply_host_key_policy,
        )
        self.assertIs(
            web_explorer._apply_host_key_policy,
            web_hostkeys._apply_host_key_policy,
        )
        self.assertIs(
            web_agents._apply_host_key_policy,
            web_hostkeys._apply_host_key_policy,
        )

    def test_app_config_endpoint_round_trips_host_key_policy(self):
        response = self.client.get("/api/app-config")
        self.assertEqual(response.get_json()["ssh"]["host_key_policy"], "auto-add")

        response = self.client.post(
            "/api/app-config", json={"ssh": {"host_key_policy": "strict"}}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ssh"]["host_key_policy"], "strict")
        self.assertEqual(api.load_config()["ssh"]["host_key_policy"], "strict")
        self.assertEqual(api.runtime_config.ssh_host_key_policy, "strict")

        # invalid values keep the current policy instead of weakening it
        response = self.client.post(
            "/api/app-config", json={"ssh": {"host_key_policy": "yolo"}}
        )
        self.assertEqual(response.get_json()["ssh"]["host_key_policy"], "strict")

    def test_launcher_ships_host_key_policy_select(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="appSshHostKeyPolicy"', html)
        for value in ("auto-add", "known-hosts", "strict"):
            self.assertIn(f'value="{value}"', html)
        app_settings_js = self._static("js/app-settings.js")
        self.assertIn("host_key_policy", app_settings_js)
        self.assertIn("appSshHostKeyPolicy", app_settings_js)


class ExplorerDownloadTestCase(unittest.TestCase):
    """Deep-dive 10.6 — explorer file download stays inside the read-only contract."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def _create_local_explorer_session(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(self.root),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["sessions"][0]["session_id"]

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_download_returns_attachment_including_binary_files(self):
        payload = b"\x00\x01binary\xffdata"
        (self.root / "artifact.bin").write_bytes(payload)
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/download?path=artifact.bin"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), payload)
        disposition = response.headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("artifact.bin", disposition)
        response.close()

    def test_download_unknown_session_returns_404(self):
        response = self.client.get("/api/explorer/missing/download?path=x")
        self.assertEqual(response.status_code, 404)

    def test_download_rejects_paths_outside_the_root(self):
        (self.root / "inside.txt").write_text("ok", encoding="utf-8")
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/download?path=../outside.txt"
        )
        self.assertEqual(response.status_code, 400)

    def test_download_rejects_files_over_the_size_cap(self):
        (self.root / "big.log").write_bytes(b"x" * 64)
        session_id = self._create_local_explorer_session()
        with patch.object(api, "EXPLORER_DOWNLOAD_MAX_BYTES", 16):
            response = self.client.get(
                f"/api/explorer/{session_id}/download?path=big.log"
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("limit", response.get_json()["error"])

    def test_download_never_touches_git_or_write_helpers(self):
        (self.root / "read.txt").write_text("read only", encoding="utf-8")
        session_id = self._create_local_explorer_session()
        with patch.object(web_explorer, "_run_git_command") as run_git:
            response = self.client.get(
                f"/api/explorer/{session_id}/download?path=read.txt"
            )
        self.assertEqual(response.status_code, 200)
        run_git.assert_not_called()
        response.close()

    def test_file_viewer_ships_download_button(self):
        # Explorer viewer moved to explorer-viewer.js (2026-07-23 split).
        terminals_js = self._static("js/explorer-viewer.js")
        self.assertIn("function downloadExplorerFile(index)", terminals_js)
        self.assertIn("data-explorer-download", terminals_js)
        self.assertIn("/download?path=", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".explorer-download-btn", terminals_css)

    def test_native_window_downloads_route_through_the_bridge(self):
        # WebView2 drops anchor downloads, so the native window must use the
        # pywebview save_download bridge instead of the <a download> click.
        terminals_js = self._static("js/explorer-viewer.js")
        download_fn = terminals_js[
            terminals_js.index("async function downloadExplorerFile"):
            terminals_js.index("function getDownloadBaseName")
        ]
        self.assertIn("isPywebviewAvailable()", download_fn)
        self.assertIn("window.pywebview.api.save_download", download_fn)
        # both paths give a visible result the user asked for
        self.assertIn("showTerminalToast", download_fn)

    def test_download_shows_a_success_toast(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function showTerminalToast(message, type", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".terminal-toast", terminals_css)
        self.assertIn(".terminal-toast.success", terminals_css)

    def test_reveal_opens_the_confined_path_in_the_file_manager(self):
        (self.root / "note.txt").write_text("hi", encoding="utf-8")
        session_id = self._create_local_explorer_session()
        with patch.object(api, "open_path_in_os_file_manager") as open_path:
            response = self.client.post(
                f"/api/explorer/{session_id}/reveal",
                json={"path": "note.txt"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        # The launcher receives the resolved, root-confined absolute path. Its
        # platform-specific subprocess may instead open the parent on Linux.
        open_path.assert_called_once_with(str((self.root / "note.txt").resolve()))

    def test_reveal_unknown_session_returns_404(self):
        response = self.client.post("/api/explorer/missing/reveal", json={"path": ""})
        self.assertEqual(response.status_code, 404)

    def test_reveal_rejects_paths_outside_the_root(self):
        session_id = self._create_local_explorer_session()
        with patch.object(web_explorer.subprocess, "Popen") as popen:
            response = self.client.post(
                f"/api/explorer/{session_id}/reveal",
                json={"path": "../outside.txt"},
            )
        self.assertEqual(response.status_code, 400)
        popen.assert_not_called()

    def test_reveal_never_touches_git_or_write_helpers(self):
        (self.root / "read.txt").write_text("read only", encoding="utf-8")
        session_id = self._create_local_explorer_session()
        with patch.object(web_explorer, "_run_git_command") as run_git, \
                patch.object(web_explorer.subprocess, "Popen"):
            response = self.client.post(
                f"/api/explorer/{session_id}/reveal",
                json={"path": "read.txt"},
            )
        self.assertEqual(response.status_code, 200)
        run_git.assert_not_called()

    def test_explorer_bar_ships_os_open_button(self):
        explorer_js = self._static("js/explorer-viewer.js")
        self.assertIn("function revealExplorerInOs(index)", explorer_js)
        self.assertIn("/reveal", explorer_js)
        # The toolbar button markup is emitted by the pane builder in terminals.js.
        terminals_js = self._static("js/terminals.js")
        self.assertIn("data-explorer-os-open", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".explorer-os-open", terminals_css)

    # A 1x1 transparent PNG — smallest valid image bytes for the viewer tests.
    _PNG_BYTES = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )

    def test_file_route_returns_image_metadata_instead_of_binary_error(self):
        (self.root / "pic.png").write_bytes(self._PNG_BYTES)
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/file?path=pic.png"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["preview_type"], "image")
        self.assertEqual(payload["name"], "pic.png")
        self.assertEqual(payload["content"], "")

    def test_image_route_serves_bytes_inline_with_mimetype(self):
        (self.root / "pic.png").write_bytes(self._PNG_BYTES)
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/image?path=pic.png"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertNotIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertEqual(response.get_data(), self._PNG_BYTES)
        response.close()

    def test_image_route_locks_down_svg_with_csp(self):
        (self.root / "vector.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8"
        )
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/image?path=vector.svg"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/svg+xml")
        self.assertIn("default-src 'none'", response.headers.get("Content-Security-Policy", ""))
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        response.close()

    def test_image_route_rejects_non_image_files(self):
        (self.root / "notes.txt").write_text("hi", encoding="utf-8")
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/image?path=notes.txt"
        )
        self.assertEqual(response.status_code, 400)

    def test_image_route_rejects_paths_outside_the_root(self):
        session_id = self._create_local_explorer_session()
        response = self.client.get(
            f"/api/explorer/{session_id}/image?path=../secret.png"
        )
        self.assertEqual(response.status_code, 400)

    def test_image_route_unknown_session_returns_404(self):
        response = self.client.get("/api/explorer/missing/image?path=x.png")
        self.assertEqual(response.status_code, 404)

    def test_image_viewer_ships_in_frontend_assets(self):
        terminals_js = self._static("js/explorer-viewer.js")
        self.assertIn("function renderExplorerImage(index, data", terminals_js)
        self.assertIn("preview_type === 'image'", terminals_js)
        self.assertIn("/image?path=", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".explorer-image-view", terminals_css)


class TerminalSearchWebLinksTestCase(unittest.TestCase):
    """Deep-dive 10.3 — terminal scrollback search + clickable links."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_terminals_page_loads_vendored_addons(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn("/static/vendor/xterm-addon-search.min.js", html)
        self.assertIn("/static/vendor/xterm-addon-web-links.min.js", html)

    def test_vendored_addons_are_served(self):
        search_js = self._static("vendor/xterm-addon-search.min.js")
        self.assertIn("SearchAddon", search_js)
        links_js = self._static("vendor/xterm-addon-web-links.min.js")
        self.assertIn("WebLinksAddon", links_js)

    def test_make_terminal_loads_search_and_web_links_addons(self):
        terminals_js = self._static("js/terminals.js")
        make_terminal = terminals_js[
            terminals_js.index("function makeTerminal"):
            terminals_js.index("function emitTerminalResize")
        ]
        self.assertIn("new SearchAddon.SearchAddon()", make_terminal)
        self.assertIn("new WebLinksAddon.WebLinksAddon(", make_terminal)
        self.assertIn("attachCustomKeyEventHandler", make_terminal)
        self.assertIn("searchAddon", make_terminal)

    def test_search_overlay_wiring_and_shortcut(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function openTerminalSearch(index)", terminals_js)
        self.assertIn("function closeTerminalSearch(index)", terminals_js)
        self.assertIn("function findTerminalSearchTargetIndex()", terminals_js)
        self.assertIn("findPrevious", terminals_js)
        self.assertIn("{ incremental: true }", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".terminal-search-overlay", terminals_css)
        self.assertIn(".terminal-search-input", terminals_css)


class BroadcastInputTestCase(unittest.TestCase):
    """Deep-dive 10.4 — broadcast typing to all plain terminal panes."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_topbar_ships_broadcast_toggle(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn('id="broadcastBtn"', html)
        self.assertIn("toggleBroadcastInput()", html)
        self.assertIn('aria-pressed="false"', html)

    def test_input_forwarding_goes_through_the_broadcast_helper(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function forwardTerminalInput(index, data)", terminals_js)
        # both onData wiring sites (grid build + split panes) share the helper
        self.assertEqual(
            terminals_js.count("onData(data => forwardTerminalInput("), 2
        )
        # the peer fan-out lives in a shared helper reused by keyboard + voice
        self.assertIn(
            "function broadcastInputToPeers(sourceIndex, data)", terminals_js
        )
        forward_fn = terminals_js[
            terminals_js.index("function forwardTerminalInput"):
            terminals_js.index("function wirePaneInputForwarding")
        ]
        self.assertIn("broadcastInputToPeers(index, data)", forward_fn)
        # explorer/browser panes are skipped (no `term`) in the shared helper
        peer_fn = terminals_js[
            terminals_js.index("function broadcastInputToPeers"):
            terminals_js.index("function setFocusedTerminal")
        ]
        self.assertIn("terminals[otherIndex]?.term", peer_fn)
        self.assertIn("if (!broadcastInputActive || !socket)", peer_fn)

    def test_broadcast_auto_disables_on_group_switch_and_idle(self):
        terminals_js = self._static("js/terminals.js")
        self.assertIn("BROADCAST_IDLE_TIMEOUT_MS = 10 * 60 * 1000", terminals_js)
        switch_fn = terminals_js[
            terminals_js.index("async function switchGroup"):
            terminals_js.index("Status refresh (no grid rebuild)")
        ]
        self.assertIn("setBroadcastInput(false)", switch_fn)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".broadcast-btn.active", terminals_css)
        self.assertIn("#terminalsGrid.broadcast-input", terminals_css)

    def test_active_terminal_pane_paints_a_single_focused_card(self):
        """ISSUE-2026-025: paintActiveTerminalCard marks exactly one plain
        terminal card and clears the rest (one-active-pane enforcement)."""
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function paintActiveTerminalCard(index)", terminals_js)
        paint_fn = terminals_js[
            terminals_js.index("function paintActiveTerminalCard(index)"):
            terminals_js.index("function setFocusedTerminal(index)")
        ]
        # marks the target with a semantic class + accessible state …
        self.assertIn("targetCard.classList.add('terminal-active')", paint_fn)
        self.assertIn("targetCard.setAttribute('aria-current', 'true')", paint_fn)
        # … and clears it from every other pane (one-active-pane enforcement)
        self.assertIn(
            "document.querySelectorAll('.terminal-container.terminal-active')", paint_fn
        )
        self.assertIn("card.classList.remove('terminal-active')", paint_fn)
        self.assertIn("card.removeAttribute('aria-current')", paint_fn)
        # explorer/browser panes are never valid input targets
        plain_fn = terminals_js[
            terminals_js.index("function isPlainTerminalCard(card)"):
            terminals_js.index("function terminalCardSlot(card)")
        ]
        self.assertIn("!card.classList.contains('explorer-pane')", plain_fn)
        self.assertIn("!card.classList.contains('browser-pane')", plain_fn)

    def test_active_terminal_pane_tracks_real_dom_focus(self):
        """ISSUE-2026-025: the highlight is driven by actual keyboard focus via
        a delegated focusin/focusout pair, so it can never disagree with where
        typing lands; focus leaving to dead space / non-terminal clears it."""
        terminals_js = self._static("js/terminals.js")
        # delegated focus wiring (one active pane == the focused terminal)
        self.assertIn("document.addEventListener('focusin', event =>", terminals_js)
        self.assertIn("document.addEventListener('focusout', event =>", terminals_js)
        focusin = terminals_js[
            terminals_js.index("document.addEventListener('focusin', event =>"):
            terminals_js.index("document.addEventListener('focusout', event =>")
        ]
        self.assertIn("event.target?.closest?.('.terminal-container')", focusin)
        self.assertIn("setFocusedTerminal(terminalCardSlot(card))", focusin)
        focusout = terminals_js[
            terminals_js.index("document.addEventListener('focusout', event =>"):
            terminals_js.index("function forwardTerminalInput")
        ]
        # only clear when focus is NOT moving to another plain terminal
        self.assertIn("event.relatedTarget?.closest?.('.terminal-container')", focusout)
        self.assertIn("if (!isPlainTerminalCard(nextCard))", focusout)
        self.assertIn("clearActiveTerminalHighlight();", focusout)
        # selection is NEVER driven by terminal output: forwardTerminalInput
        # (wired to `onData`, which also fires for TUI mouse-tracking sequences)
        # must not touch the active-pane state, or the highlight would follow the
        # mouse into an unfocused pane.
        forward_fn = terminals_js[
            terminals_js.index("function forwardTerminalInput"):
            terminals_js.index("function wirePaneInputForwarding")
        ]
        self.assertNotIn("setFocusedTerminal", forward_fn)
        # clearing selection drops the input target too (no invisible target)
        clear_fn = terminals_js[
            terminals_js.index("function clearActiveTerminalHighlight()"):
            terminals_js.index("function resetFocusedTerminal()")
        ]
        self.assertIn("_focusedTerminalIndex = -1;", clear_fn)
        # teardown fully resets
        self.assertIn("function resetFocusedTerminal()", terminals_js)
        teardown = terminals_js[
            terminals_js.index("function teardownCurrentGrid()"):
            terminals_js.index("function teardownCurrentGrid()") + 900
        ]
        self.assertIn("resetFocusedTerminal();", teardown)

    def test_push_to_talk_targets_only_the_selected_terminal(self):
        """ISSUE-2026-026 follow-up: voice/PTT go to the focused (highlighted)
        terminal only — never to a stale 'last selected' pane when nothing is
        selected (consistent with typing)."""
        voice_js = self._static("js/voice-input.js")
        ptt_fn = voice_js[
            voice_js.index("function _findPttTerminalIndex()"):
            voice_js.index("function _updateVoiceBtn(index, recording)")
        ]
        self.assertIn("return _focusedTerminalIndex;", ptt_fn)
        # no fall-back scan to the first terminal when nothing is selected
        self.assertNotIn("for (let i = 0", ptt_fn)

    def test_broadcast_enable_focuses_a_terminal_for_immediate_typing(self):
        """ISSUE-2026-026 follow-up: enabling Broadcast typing focuses a terminal
        so the user can type immediately without first clicking a pane."""
        terminals_js = self._static("js/terminals.js")
        set_broadcast = terminals_js[
            terminals_js.index("function setBroadcastInput(active)"):
            terminals_js.index("function toggleBroadcastInput()")
        ]
        self.assertIn("focusActiveOrDefaultTerminal();", set_broadcast)
        focus_default = terminals_js[
            terminals_js.index("function focusActiveOrDefaultTerminal()"):
            terminals_js.index("function focusActiveOrDefaultTerminal()") + 400
        ]
        # prefers the sticky target, focuses a real attached terminal
        self.assertIn(
            "firstAttachedPlainTerminalIndex(_focusedTerminalIndex)", focus_default
        )
        self.assertIn("terminals[index].term.focus()", focus_default)

    def test_active_terminal_pane_has_distinct_token_style(self):
        """ISSUE-2026-025: the active-pane treatment is token-based and stays
        distinguishable from broadcast typing."""
        terminals_css = self._static("css/terminals.css")
        self.assertIn(
            ".terminal-container.terminal-active:not(.explorer-pane):not(.browser-pane)",
            terminals_css,
        )
        active_rule_start = terminals_css.index(
            ".terminal-container.terminal-active:not(.explorer-pane):not(.browser-pane)"
        )
        active_rule = terminals_css[active_rule_start:active_rule_start + 600]
        # token-driven accent (no palette literals) …
        self.assertIn("var(--t-accent)", active_rule)
        # … a heavier 2px ring than the broadcast 1px inset border …
        self.assertIn("inset 0 0 0 2px var(--t-accent)", active_rule)
        # … plus a header accent rule that broadcast does not paint (distinct).
        self.assertIn("inset 0 -2px 0 var(--t-accent)", active_rule)

    def test_voice_transcript_honours_broadcast_typing(self):
        """ISSUE-2026-026: a committed voice transcript fans out to every plain
        pane through the same broadcast filter keyboard input uses; interim
        previews stay on the recording pane only."""
        terminals_js = self._static("js/terminals.js")
        handler = terminals_js[
            terminals_js.index("socket.on('voice_result'"):
            terminals_js.index("socket.on('voice_status'")
        ]
        # final branch: deliver to recorder + fan out via the shared helper
        self.assertIn("_sendToTerminal(index, text);", handler)
        self.assertIn("broadcastInputToPeers(index, text);", handler)
        self.assertIn("_clearVoicePreview(index);", handler)
        # interim (non-final) previews are isolated to the recording pane
        self.assertIn("_showVoicePreview(index, text);", handler)
        self.assertLess(
            handler.index("broadcastInputToPeers(index, text)"),
            handler.index("_showVoicePreview(index, text)"),
        )
        # the voice path reuses the *same* peer helper as keyboard forwarding
        self.assertEqual(
            terminals_js.count("broadcastInputToPeers(index, "), 2
        )

    def test_broadcast_highlight_drops_when_focus_leaves_terminals(self):
        """Wave 4 / 6.a (OD-10): the all-panes broadcast ring only paints while
        a terminal actually holds focus; clicking into dead space or an
        explorer/browser pane drops every highlight, and the next focusin
        re-lights per the broadcast state at that moment."""
        terminals_css = self._static("css/terminals.css")
        # The broadcast ring rule requires the focus-tracking class …
        self.assertIn(
            "#terminalsGrid.broadcast-input.terminal-focus .terminal-container:not(.explorer-pane):not(.browser-pane)",
            terminals_css,
        )
        # … and the old unconditional (focus-free) rule is gone.
        self.assertNotIn(
            "#terminalsGrid.broadcast-input .terminal-container",
            terminals_css,
        )
        terminals_js = self._static("js/terminals.js")
        focus_fns = terminals_js[
            terminals_js.index("function setFocusedTerminal(index)"):
            terminals_js.index("function resetFocusedTerminal()")
        ]
        # focusin into a terminal re-lights; leaving to dead space clears.
        self.assertIn("classList.add('terminal-focus')", focus_fns)
        self.assertIn("classList.remove('terminal-focus')", focus_fns)
        # ISSUE-2026-026 stays intact: enabling broadcast still focuses a pane.
        set_broadcast = terminals_js[
            terminals_js.index("function setBroadcastInput(active)"):
            terminals_js.index("function toggleBroadcastInput()")
        ]
        self.assertIn("focusActiveOrDefaultTerminal();", set_broadcast)


class RuntimeStateRestoreTestCase(unittest.TestCase):
    """Deep-dive 10.5 — workspace-shape snapshot + restore after restart."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _launch_explorer_group(self, name="Workspace"):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": name,
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["group_id"]

    def test_capture_persists_a_password_free_v2_slot(self):
        self._launch_explorer_group()
        slot = web_runtime_state.capture_workspace(api.session_manager)
        self.assertIsNotNone(slot)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        stored = data["workspaces"]["default"]
        self.assertEqual(stored["workspace_id"], "default")
        self.assertEqual(stored["origin"], "auto")
        self.assertEqual(stored["label"], "Workspace")
        self.assertEqual(len(stored["groups"]), 1)
        group = stored["groups"][0]
        self.assertEqual(group["name"], "Workspace")
        self.assertEqual(group["connection_mode"], "wsl")
        self.assertEqual(len(group["sessions"]), 1)
        session = group["sessions"][0]
        self.assertEqual(session["startup_mode"], "explorer")
        self.assertNotIn("password", session)

    def test_v1_file_migrates_to_a_restorable_default_slot(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "saved_at": time.time(),
                    "groups": [{"group_id": "g1", "name": "buildserver-01"}],
                }
            ),
            encoding="utf-8",
        )
        slot = web_runtime_state.load_restorable_workspace()
        self.assertIsNotNone(slot)
        self.assertEqual(slot["workspace_id"], "default")
        self.assertEqual(slot["origin"], "auto")
        self.assertEqual(slot["label"], "buildserver-01")
        self.assertEqual(slot["groups"][0]["group_id"], "g1")

    def test_eligibility_auto_and_manual_with_no_max_age(self):
        self._launch_explorer_group()
        web_runtime_state.capture_workspace(api.session_manager, origin="auto")
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace())
        web_runtime_state.capture_workspace(api.session_manager, origin="manual")
        slot = web_runtime_state.load_restorable_workspace()
        self.assertEqual(slot["origin"], "manual")
        # The offer is permanent: a years-old slot is still restorable.
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data["workspaces"]["default"]["saved_at"] = time.time() - 10 * 365 * 24 * 3600
        self.state_path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace())

    def test_empty_missing_or_unknown_slots_are_not_restorable(self):
        self.assertIsNone(web_runtime_state.load_restorable_workspace())
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "workspaces": {
                        "default": {
                            "workspace_id": "default",
                            "label": "Empty",
                            "origin": "auto",
                            "saved_at": time.time(),
                            "groups": [],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertIsNone(web_runtime_state.load_restorable_workspace())
        self.state_path.write_text("not json", encoding="utf-8")
        self.assertIsNone(web_runtime_state.load_restorable_workspace())
        self.assertIsNone(
            web_runtime_state.load_restorable_workspace("cccccccccccc")
        )

    def test_capture_and_clear_preserve_sibling_slots(self):
        workspace_a = "aaaaaaaaaaaa"
        workspace_b = "bbbbbbbbbbbb"
        api.session_manager.create_workspace("A", workspace_a)
        api.session_manager.create_workspace("B", workspace_b)
        group_a = self._launch_explorer_group("A")
        api.session_manager.move_group(group_a, workspace_a)
        group_b = self._launch_explorer_group("B")
        api.session_manager.move_group(group_b, workspace_b)
        web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=workspace_a,
        )
        web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=workspace_b,
        )
        # Overwriting slot A must leave slot B intact.
        before = json.loads(self.state_path.read_text(encoding="utf-8"))
        sibling_before = before["workspaces"][workspace_b]
        web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=workspace_a,
            origin="manual",
        )
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(set(data["workspaces"]), {workspace_a, workspace_b})
        self.assertEqual(data["workspaces"][workspace_a]["origin"], "manual")
        self.assertEqual(data["workspaces"][workspace_b], sibling_before)
        # Clearing slot A must leave slot B intact, and the file stays v2.
        web_runtime_state.clear_workspace(workspace_a)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(set(data["workspaces"]), {workspace_b})

    def test_autosave_tick_captures_a_live_workspace(self):
        self._launch_explorer_group()
        self.assertFalse(self.state_path.exists())
        api._run_workspace_autosave_tick()
        slot = web_runtime_state.load_restorable_workspace()
        self.assertIsNotNone(slot)
        self.assertEqual(slot["origin"], "auto")
        self.assertEqual(len(slot["groups"]), 1)

    def test_autosave_tick_skips_an_empty_workspace_without_clearing(self):
        self._launch_explorer_group()
        api._run_workspace_autosave_tick()
        before = self.state_path.read_text(encoding="utf-8")
        # The workspace goes idle (e.g. launcher only); the tick must skip it
        # and leave the previously saved slot restorable.
        api.session_manager.reset_sessions()
        api._run_workspace_autosave_tick()
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace())

    def test_runtime_state_is_restorable_even_with_active_groups(self):
        self._launch_explorer_group()
        web_runtime_state.capture_workspace(api.session_manager)
        payload = self.client.get("/api/runtime-state").get_json()
        self.assertTrue(payload["restorable"])
        self.assertEqual(payload["workspace_id"], "default")
        self.assertEqual(payload["origin"], "auto")
        self.assertEqual(payload["label"], "Workspace")
        self.assertEqual(len(payload["groups"]), 1)
        self.assertEqual(payload["active_group_count"], 1)

    def test_manual_native_zoom_survives_later_autosave(self):
        """Save Workspace records desktop zoom; the timer must not erase it."""
        self._launch_explorer_group()
        response = self.client.post(
            "/api/runtime-state/save",
            json={"native_zoom_factor": 1.25},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["native_zoom_factor"], 1.25)

        api._run_workspace_autosave_tick()
        slot = web_runtime_state.load_restorable_workspace()
        self.assertEqual(slot["native_zoom_factor"], 1.25)
        payload = self.client.get("/api/runtime-state").get_json()
        self.assertEqual(payload["native_zoom_factor"], 1.25)

    def test_invalid_stored_native_zoom_degrades_to_no_preference(self):
        self._launch_explorer_group()
        web_runtime_state.capture_workspace(api.session_manager)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data["workspaces"]["default"]["native_zoom_factor"] = 99
        self.state_path.write_text(json.dumps(data), encoding="utf-8")

        slot = web_runtime_state.load_restorable_workspace()
        self.assertIsNone(slot["native_zoom_factor"])
        payload = self.client.get("/api/runtime-state").get_json()
        self.assertIsNone(payload["native_zoom_factor"])

    def test_save_endpoint_captures_a_manual_slot_immediately_restorable(self):
        self._launch_explorer_group()
        response = self.client.post("/api/runtime-state/save", json={})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["workspace_id"], "default")
        self.assertEqual(payload["origin"], "manual")
        self.assertEqual(payload["label"], "Workspace")
        slot = web_runtime_state.load_restorable_workspace()
        self.assertEqual(slot["origin"], "manual")

    def test_save_endpoint_refuses_an_empty_workspace_without_clearing(self):
        self._launch_explorer_group()
        self.client.post("/api/runtime-state/save", json={})
        before = self.state_path.read_text(encoding="utf-8")
        api.session_manager.reset_sessions()
        response = self.client.post("/api/runtime-state/save", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)

    def test_autosave_captures_the_reported_front_group(self):
        """The timer has no window to ask, so it uses the reported hint."""
        first = self._launch_explorer_group("First")
        self._launch_explorer_group("Second")
        # Launching switches the workspace to the newest group; going back to
        # the first one must be what a later timed save records.
        response = self.client.post(
            "/api/session-groups/active", json={"group_id": first}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["active_group_id"], first)

        api._run_workspace_autosave_tick()
        slot = web_runtime_state.load_restorable_workspace()
        self.assertEqual(slot["active_group_id"], first)
        payload = self.client.get("/api/runtime-state").get_json()
        self.assertEqual(payload["active_group_id"], first)

    def test_manual_save_records_the_saving_windows_front_group(self):
        first = self._launch_explorer_group("First")
        second = self._launch_explorer_group("Second")
        response = self.client.post(
            "/api/runtime-state/save", json={"active_group_id": first}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["active_group_id"], first)
        self.assertEqual(
            web_runtime_state.load_restorable_workspace()["active_group_id"], first
        )
        # The manual save also updates the live hint, so the next timed save
        # agrees with it instead of reverting to the newest group.
        api._run_workspace_autosave_tick()
        self.assertEqual(
            web_runtime_state.load_restorable_workspace()["active_group_id"], first
        )
        self.assertNotEqual(first, second)

    def test_unknown_or_closed_front_group_degrades_to_no_preference(self):
        first = self._launch_explorer_group("First")
        self._launch_explorer_group("Second")
        self.client.post("/api/session-groups/active", json={"group_id": first})
        # An id naming no live group leaves the standing hint alone rather than
        # blanking it (a stale tab must not lose the real answer).
        response = self.client.post(
            "/api/session-groups/active", json={"group_id": "does-not-exist"}
        )
        self.assertEqual(response.get_json()["active_group_id"], first)

        # Once that group is closed the hint is gone, and a capture falls back
        # to no preference rather than naming a group it did not store.
        self.assertEqual(
            self.client.delete(f"/api/sessions?group={first}").status_code, 200
        )
        slot = web_runtime_state.capture_workspace(api.session_manager)
        self.assertEqual(slot["active_group_id"], "")

    def test_stored_front_group_is_revalidated_against_the_slots_groups(self):
        """A hand-edited id naming no stored group must not reach the restore."""
        self._launch_explorer_group()
        web_runtime_state.capture_workspace(api.session_manager)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data["workspaces"]["default"]["active_group_id"] = "ghost-group"
        self.state_path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(
            web_runtime_state.load_restorable_workspace()["active_group_id"], ""
        )

    def test_group_events_do_not_write_the_snapshot(self):
        group_id = self._launch_explorer_group()
        # Launching a group is a pure UI event now — no snapshot write.
        self.assertFalse(self.state_path.exists())
        response = self.client.delete(f"/api/sessions?group={group_id}")
        self.assertEqual(response.status_code, 200)
        # Closing the group must not write (or clear) the snapshot either.
        self.assertFalse(self.state_path.exists())

    def test_group_close_preserves_the_previously_saved_slot(self):
        group_id = self._launch_explorer_group()
        web_runtime_state.capture_workspace(api.session_manager)
        before = self.state_path.read_text(encoding="utf-8")
        response = self.client.delete(f"/api/sessions?group={group_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace())

    def test_capture_label_ignores_timestamp_group_names(self):
        # No session_name: the live group gets an auto "Session HH:MM:SS" name,
        # but the captured label must never be that bare timestamp.
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        slot = web_runtime_state.capture_workspace(api.session_manager)
        self.assertIsNotNone(slot)
        self.assertNotRegex(slot["label"], r"\d{2}:\d{2}:\d{2}")
        self.assertTrue(slot["label"])

    def test_restore_launch_never_mints_a_timestamp_group_name(self):
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "restore": True,
                    "sessions": [
                        {
                            "directory": str(self.repo_dir),
                            "title": "Files",
                            "startup_mode": "explorer",
                        }
                    ],
                },
            )
        self.assertEqual(response.status_code, 201)
        name = response.get_json()["group"]["name"]
        self.assertEqual(name, "Workspace")
        self.assertNotRegex(name, r"\d{2}:\d{2}:\d{2}")

    def test_replaying_the_snapshot_relaunches_the_group(self):
        self._launch_explorer_group()
        slot = web_runtime_state.capture_workspace(api.session_manager)
        api.session_manager.reset_sessions()

        group = slot["groups"][0]
        response = self.client.post(
            "/api/sessions",
            json={
                "sessions": group["sessions"],
                "connection_mode": group["connection_mode"],
                "layout": group["layout"],
                "workspace_layout": group["workspace_layout"],
                "session_name": group["name"],
                "saved_session_id": group["saved_session_id"],
                "restore": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        restored = response.get_json()
        self.assertEqual(restored["group"]["name"], "Workspace")
        self.assertEqual(restored["sessions"][0]["startup_mode"], "explorer")

    def test_restore_launch_skips_agent_preflight_clearing(self):
        """Bug 2: a restore replays the workspace verbatim, so a cold post-restart
        agent probe must not clear the command and drop its auto-mode flag."""
        with patch.object(api.socketio, "start_background_task"), patch.object(
            web_agents, "_sanitize_agent_launch_commands"
        ) as sanitize:
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "session_name": "Restored",
                    "restore": True,
                    "sessions": [
                        {
                            "directory": str(self.repo_dir),
                            "title": "Agent",
                            "startup_mode": "agent",
                            "initial_command": "claude",
                            "initial_command_mode": "agent",
                            "agent_selection": "claude",
                            "agent_auto_mode": True,
                        }
                    ],
                },
            )
        self.assertEqual(response.status_code, 201)
        sanitize.assert_not_called()
        self.assertEqual(response.get_json()["warnings"], [])
        session = response.get_json()["sessions"][0]
        self.assertEqual(session["initial_command"], "claude")
        self.assertTrue(session["agent_auto_mode"])

    def test_normal_launch_still_runs_agent_preflight_clearing(self):
        with patch.object(api.socketio, "start_background_task"), patch.object(
            web_agents, "_sanitize_agent_launch_commands", return_value=[]
        ) as sanitize:
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "session_name": "Fresh",
                    "sessions": [
                        {
                            "directory": str(self.repo_dir),
                            "title": "Agent",
                            "startup_mode": "agent",
                            "initial_command": "claude",
                            "initial_command_mode": "agent",
                            "agent_selection": "claude",
                        }
                    ],
                },
            )
        self.assertEqual(response.status_code, 201)
        sanitize.assert_called_once()

    def test_delete_endpoint_clears_only_that_workspace_slot(self):
        self._launch_explorer_group()
        web_runtime_state.capture_workspace(api.session_manager)
        # Forget is refused while the workspace is live (the next autosave
        # would simply re-capture it), so close it first.
        live_response = self.client.delete("/api/runtime-state")
        self.assertEqual(live_response.status_code, 409)
        self.assertFalse(live_response.get_json()["forgotten"])
        self.client.delete("/api/sessions")
        response = self.client.delete("/api/runtime-state")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["forgotten"])
        self.assertIsNone(web_runtime_state.load_restorable_workspace())
        # The file itself stays (v2 skeleton); only the slot is removed.
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["workspaces"], {})
        payload = self.client.get("/api/runtime-state").get_json()
        self.assertFalse(payload["restorable"])

    def test_launcher_ships_restore_banner(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="restoreWorkspaceBanner"', html)
        self.assertIn("restorePreviousWorkspace()", html)
        self.assertIn("dismissRestoreBanner()", html)
        launcher_js = self._static("js/launcher.js")
        self.assertIn("async function checkRestorableWorkspace()", launcher_js)
        self.assertIn("async function restorePreviousWorkspace()", launcher_js)
        launcher_css = self._static("css/launcher.css")
        self.assertIn(".restore-banner", launcher_css)

    def test_restore_banner_keeps_a_content_sized_row(self):
        """Todo 2 — the banner used to land in .app-frame's 1fr row and grow
        with the window. Rows are placed explicitly so its row is content
        sized, and its margins line it up with the columns below."""
        launcher_css = self._static("css/launcher.css")
        app_frame = re.search(r"\n        \.app-frame \{(.*?)\}", launcher_css, re.DOTALL)
        self.assertIsNotNone(app_frame)
        self.assertIn("grid-template-rows: auto auto 1fr", app_frame.group(1))
        for placement in (
            ".app-titlebar { grid-row: 1; }",
            ".restore-banner { grid-row: 2; }",
            ".shell { grid-row: 3; }",
        ):
            self.assertIn(placement, launcher_css)
        # The declaration block, not the one-line grid-row placement above it.
        banner = re.search(
            r"\.restore-banner \{[^}]*display: flex[^}]*\}", launcher_css, re.DOTALL
        )
        self.assertIsNotNone(banner)
        # 20px .shell padding + 10px .column padding = the card edge.
        self.assertIn("margin: 14px 30px 0;", banner.group(0))
        self.assertNotIn("min-height", banner.group(0))

    def test_dismiss_restore_banner_is_hide_only(self):
        """Decision 3: Dismiss only hides the banner client-side — it must not
        call DELETE /api/runtime-state, so the saved slot stays restorable."""
        launcher_js = self._static("js/launcher.js")
        start = launcher_js.index("function dismissRestoreBanner()")
        end = launcher_js.index("function restorePreviousWorkspace", start)
        body = launcher_js[start:end]
        self.assertNotIn("fetch(", body)
        self.assertNotIn("DELETE", body)

    def test_restore_replays_current_saved_preset(self):
        """"Latest preset wins": a preset-backed group is restored from the
        saved session's *current* config (so Save All Sessions then Save
        Workspace restores the edited state), while an ad-hoc group replays the
        snapshot verbatim. Launch and restore share buildSessionsFromConfig so
        both build panes identically."""
        launcher_js = self._static("js/launcher.js")
        # One shared expansion, reused by launch and restore (no copy-paste).
        self.assertIn("function buildSessionsFromConfig(config, count)", launcher_js)
        self.assertIn("sessions = buildSessionsFromConfig(config, selectedCount)", launcher_js)
        # Restore resolves a group's current preset before replaying it.
        self.assertIn("async function buildRestoreGroupBody(group)", launcher_js)
        self.assertIn("await buildRestoreGroupBody(group)", launcher_js)
        self.assertIn("buildSessionsFromConfig(config, config.terminal_count)", launcher_js)
        self.assertIn("/api/saved-sessions/${encodeURIComponent(savedId)}", launcher_js)
        # The blank built-in default is never treated as a real preset, and a
        # missing/deleted preset falls back to the verbatim snapshot body.
        self.assertIn("savedId === DEFAULT_SESSION_ID", launcher_js)
        start = launcher_js.index("async function buildRestoreGroupBody(group)")
        end = launcher_js.index("function dismissRestoreBanner()", start)
        body = launcher_js[start:end]
        self.assertIn("return snapshotBody", body)

    def test_terminals_page_ships_workspace_save_menu(self):
        """The Workspace... dropdown's Save Workspace item posts to
        /api/runtime-state/save and is disabled when no groups are live."""
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn('id="workspaceMenuRoot"', html)
        self.assertIn('id="workspaceMenuBtn"', html)
        self.assertIn(">Workspace...</button>", html)
        self.assertIn('id="saveWorkspaceItem"', html)
        self.assertIn(">Save Workspace</button>", html)
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function toggleWorkspaceMenu(event)", terminals_js)
        self.assertIn("async function saveWorkspace(", terminals_js)
        self.assertIn("/api/runtime-state/save", terminals_js)
        self.assertIn("native_zoom_factor: nativeZoomFactor", terminals_js)
        self.assertIn("item.disabled = !sessionGroups.length;", terminals_js)
        shared_js = self._static("js/shared.js")
        self.assertIn("function normalizeNativeZoomFactor(value)", shared_js)
        self.assertIn("async function getNativeSessionZoomFactor()", shared_js)
        launcher_js = self._static("js/launcher.js")
        self.assertIn("body: JSON.stringify({ native_zoom_factor: nativeZoomFactor })", launcher_js)
        workspaces_js = self._static("js/workspaces.js")
        self.assertIn("api.open_workspace_window(", workspaces_js)
        self.assertIn("resolvedWorkspaceId,\n                    groupId,", workspaces_js)


class SettingsLauncherConfigTestCase(unittest.TestCase):
    """Stage 7 — ISSUE-2026-031 / ISSUE-2026-029 / ISSUE-2026-013."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "config.json"
        patcher = patch.object(web_config, "CONFIG_PATH", str(self.config_path))
        patcher.start()
        # LIFO cleanup: unpatch first, then refresh from the real config so
        # later test classes see the on-disk settings again.
        self.addCleanup(api._refresh_runtime_config)
        self.addCleanup(patcher.stop)
        api._refresh_runtime_config()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    # ── ISSUE-2026-031 — App Settings body scrolls under pinned actions ──

    def test_app_settings_body_keeps_modal_scroll_region(self):
        # The dialog is shared by both pages, so its CSS lives in its own
        # stylesheet now (todo 1).
        settings_css = self._static("css/app-settings.css")
        # The override that disabled the scroll region must stay gone.
        self.assertNotIn(
            ".app-settings-card .settings-grid",
            settings_css,
            "App Settings must not override the shared .settings-grid scroll model",
        )
        settings_grid = re.search(
            r"#appSettingsModal \.settings-grid \{(.*?)\}", settings_css, re.DOTALL
        )
        self.assertIsNotNone(settings_grid)
        self.assertIn("overflow: auto", settings_grid.group(1))
        self.assertIn("min-height: 0", settings_grid.group(1))
        # Pinned header/body/actions rows: the actions row stays out of the
        # scrollable body, so a taller voice panel can never paint under it.
        modal_card = re.search(
            r"#appSettingsModal \.modal-card \{(.*?)\}", settings_css, re.DOTALL
        )
        self.assertIsNotNone(modal_card)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", modal_card.group(1))
        self.assertIn(".app-settings-actions", settings_css)

    # ── ISSUE-2026-029 — terminal settings in App Settings ──

    def test_app_config_returns_terminal_settings(self):
        payload = self.client.get("/api/app-config").get_json()
        self.assertIn("terminal", payload)
        self.assertEqual(
            payload["terminal"]["font_family"], api.runtime_config.terminal_font_family
        )
        self.assertEqual(
            payload["terminal"]["font_size"], api.runtime_config.terminal_font_size
        )
        self.assertEqual(
            payload["terminal"]["max_sessions"], api.runtime_config.max_sessions
        )

    def test_multi_workspace_flag_is_wired_through_runtime_and_both_pages(self):
        response = self.client.post(
            "/api/app-config",
            json={"workspace": {"multi_workspace_enabled": True}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["workspace"]["multi_workspace_enabled"])
        self.assertTrue(api.runtime_config.multi_workspace_enabled)
        self.assertTrue(
            api.load_config()["workspace"]["multi_workspace_enabled"]
        )
        self.assertIn(
            "const MULTI_WORKSPACE_ENABLED = true;",
            self.client.get("/").get_data(as_text=True),
        )
        self.assertIn(
            "const MULTI_WORKSPACE_ENABLED = true;",
            self.client.get("/terminals").get_data(as_text=True),
        )

    def test_app_config_persists_terminal_settings(self):
        response = self.client.post(
            "/api/app-config",
            json={
                "terminal": {
                    "font_family": "Cascadia Mono, monospace",
                    "font_size": 18,
                    "max_sessions": 6,
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["terminal"]["font_family"], "Cascadia Mono, monospace")
        self.assertEqual(payload["terminal"]["font_size"], 18)
        self.assertEqual(payload["terminal"]["max_sessions"], 6)
        cfg = api.load_config()
        self.assertEqual(cfg["terminal"]["font_family"], "Cascadia Mono, monospace")
        self.assertEqual(cfg["terminal"]["font_size"], 18)
        self.assertEqual(cfg["terminal"]["max_sessions"], 6)
        self.assertEqual(api.runtime_config.terminal_font_size, 18)
        self.assertEqual(api.runtime_config.terminal_font_family, "Cascadia Mono, monospace")
        self.assertEqual(api.runtime_config.max_sessions, 6)

    def test_app_config_clamps_terminal_bounds(self):
        response = self.client.post(
            "/api/app-config",
            json={"terminal": {"font_size": 200, "max_sessions": 99}},
        )
        payload = response.get_json()
        self.assertEqual(payload["terminal"]["font_size"], web_config.TERMINAL_FONT_SIZE_MAX)
        self.assertEqual(payload["terminal"]["max_sessions"], web_config.MAX_SESSIONS_MAX)

        response = self.client.post(
            "/api/app-config",
            json={"terminal": {"font_size": 1, "max_sessions": 0}},
        )
        payload = response.get_json()
        self.assertEqual(payload["terminal"]["font_size"], web_config.TERMINAL_FONT_SIZE_MIN)
        self.assertEqual(payload["terminal"]["max_sessions"], web_config.MAX_SESSIONS_MIN)

    def test_app_config_round_trips_autosave_interval(self):
        payload = self.client.get("/api/app-config").get_json()
        self.assertEqual(payload["workspace"]["autosave_interval_minutes"], 5)

        response = self.client.post(
            "/api/app-config",
            json={"workspace": {"autosave_interval_minutes": 10}},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["workspace"]["autosave_interval_minutes"], 10)
        cfg = api.load_config()
        self.assertEqual(cfg["workspace"]["autosave_interval_minutes"], 10)
        self.assertEqual(api.runtime_config.workspace_autosave_interval_minutes, 10)

    def test_app_config_clamps_autosave_interval(self):
        response = self.client.post(
            "/api/app-config",
            json={"workspace": {"autosave_interval_minutes": 99}},
        )
        self.assertEqual(
            response.get_json()["workspace"]["autosave_interval_minutes"],
            web_config.AUTOSAVE_INTERVAL_MINUTES_MAX,
        )

        response = self.client.post(
            "/api/app-config",
            json={"workspace": {"autosave_interval_minutes": 0}},
        )
        self.assertEqual(
            response.get_json()["workspace"]["autosave_interval_minutes"],
            web_config.AUTOSAVE_INTERVAL_MINUTES_MIN,
        )

    def test_runtime_config_refresh_normalizes_autosave_interval(self):
        self.config_path.write_text(
            json.dumps({"workspace": {"autosave_interval_minutes": "nope"}}),
            encoding="utf-8",
        )
        api._refresh_runtime_config()
        self.assertEqual(
            api.runtime_config.workspace_autosave_interval_minutes,
            web_config.AUTOSAVE_INTERVAL_MINUTES_DEFAULT,
        )

    def test_app_config_rejects_invalid_terminal_values(self):
        before_family = api.runtime_config.terminal_font_family
        before_size = api.runtime_config.terminal_font_size
        before_sessions = api.runtime_config.max_sessions

        response = self.client.post(
            "/api/app-config",
            json={
                "terminal": {
                    "font_family": "x" * (web_config.TERMINAL_FONT_FAMILY_MAX_LENGTH + 1),
                    "font_size": "not-a-number",
                    "max_sessions": None,
                }
            },
        )

        payload = response.get_json()
        self.assertEqual(payload["terminal"]["font_family"], before_family)
        self.assertEqual(payload["terminal"]["font_size"], before_size)
        self.assertEqual(payload["terminal"]["max_sessions"], before_sessions)

        response = self.client.post("/api/app-config", json={"terminal": {"font_family": "   "}})
        self.assertEqual(response.get_json()["terminal"]["font_family"], before_family)

    def test_runtime_config_refresh_clamps_terminal_settings(self):
        self.config_path.write_text(
            json.dumps({"terminal": {"max_sessions": "nope", "font_size": 900}}),
            encoding="utf-8",
        )
        api._refresh_runtime_config()
        self.assertEqual(api.runtime_config.max_sessions, 4)
        self.assertEqual(
            api.runtime_config.terminal_font_size, web_config.TERMINAL_FONT_SIZE_MAX
        )

    def test_app_settings_modal_collects_terminal_fields(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="appTerminalFontFamily"', html)
        self.assertIn('id="appTerminalFontSize"', html)
        self.assertIn('id="appTerminalMaxSessions"', html)

        app_settings_js = self._static("js/app-settings.js")
        collect = app_settings_js[
            app_settings_js.index("function collectTerminalFontFamily()"):
            app_settings_js.index("function notifyAppConfigUpdated(appSettings")
        ]
        self.assertIn("terminal: {", collect)
        self.assertIn("appTerminalFontFamily", collect)
        self.assertIn("appTerminalFontSize", collect)
        self.assertIn("appTerminalMaxSessions", collect)
        notify = app_settings_js[
            app_settings_js.index("function notifyAppConfigUpdated(appSettings"):
            app_settings_js.index("async function loadAppSettings()")
        ]
        self.assertIn("terminal: {", notify)

    def test_terminals_page_applies_live_terminal_font_updates(self):
        terminals_js = self._static("js/terminals.js")
        apply_update = terminals_js[
            terminals_js.index("function applyAppConfigUpdate(message)"):
            terminals_js.index("function applyAppConfigTerminalFont(message)")
        ]
        self.assertIn("applyAppConfigTerminalFont(message);", apply_update)
        apply_font = terminals_js[
            terminals_js.index("function applyAppConfigTerminalFont(message)"):
            terminals_js.index("/* Recover app-config changes missed")
        ]
        self.assertIn("document.body.dataset.terminalFontSize", apply_font)
        self.assertIn("document.body.dataset.terminalFontFamily", apply_font)
        self.assertIn("styleTerminalFont(terminal, fontSize, fontFamily);", apply_font)
        self.assertIn("scheduleFit(index);", apply_font)
        # The shared styler is what writes the xterm options.
        style_fn = terminals_js[
            terminals_js.index("function styleTerminalFont(terminal, fontSize, fontFamily)"):
            terminals_js.index("function applyAppConfigTerminalFont(message)")
        ]
        self.assertIn("term.options.fontSize", style_fn)
        self.assertIn("term.options.fontFamily", style_fn)

    # ── Wave 4 / 7.c — font presets + per-session apply scope (OD-13/OD-14) ──

    def test_app_config_broadcast_carries_apply_scope_without_persisting_it(self):
        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                "/api/app-config",
                json={"terminal": {"font_size": 16, "apply_scope": "all"}},
            )

        self.assertEqual(response.status_code, 200)
        emit.assert_called_once()
        _event, payload = emit.call_args[0]
        self.assertEqual(payload["terminal"]["apply_scope"], "all")
        # OD-14: the scope is a one-shot modifier — never persisted to
        # config.json (Regression Guardrail 5) and never in the public config.
        self.assertNotIn("apply_scope", api.load_config().get("terminal", {}))
        self.assertNotIn("apply_scope", response.get_json()["terminal"])

    def test_app_config_broadcast_defaults_to_session_scope(self):
        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                "/api/app-config", json={"terminal": {"font_size": 15}}
            )
        self.assertEqual(response.status_code, 200)
        _event, payload = emit.call_args[0]
        self.assertEqual(payload["terminal"]["apply_scope"], "session")

        # Unknown scopes collapse to the safe focused-session default.
        with patch.object(api.socketio, "emit") as emit:
            self.client.post(
                "/api/app-config", json={"terminal": {"apply_scope": "everything"}}
            )
        _event, payload = emit.call_args[0]
        self.assertEqual(payload["terminal"]["apply_scope"], "session")

    def test_app_settings_modal_offers_font_presets_with_custom_escape(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="appTerminalFontPreset"', html)
        # Each preset option previews in its own family (OD-13).
        self.assertIn("value=\"'JetBrains Mono', Consolas, monospace\"", html)
        self.assertIn("style=\"font-family: 'JetBrains Mono', Consolas, monospace\"", html)
        self.assertNotIn(">Fira Code</option>", html)
        self.assertNotIn(">Source Code Pro</option>", html)
        self.assertNotIn(">Menlo</option>", html)
        # "Custom…" keeps the free-text escape hatch alive.
        self.assertIn('<option value="custom">Custom…</option>', html)
        self.assertIn('id="appTerminalFontCustomField"', html)
        self.assertIn('id="appTerminalApplyAll"', html)

        app_settings_js = self._static("js/app-settings.js")
        self.assertIn("function collectTerminalFontFamily()", app_settings_js)
        self.assertIn(
            "apply_scope: document.getElementById('appTerminalApplyAll')?.checked ? 'all' : 'session'",
            app_settings_js,
        )
        # Presets hide the free-text input; "Custom…" reveals it.
        self.assertIn(
            "fontCustomField?.classList.toggle('hidden', fontPresetInput?.value !== 'custom')",
            app_settings_js,
        )
        # The one-shot scope checkbox resets whenever the form re-syncs, and
        # the saved scope rides the cross-window notification.
        self.assertIn("terminalApplyAllInput.checked = false;", app_settings_js)
        self.assertIn(
            "notifyAppConfigUpdated(data, settingsForm.terminal.apply_scope);",
            app_settings_js,
        )
        self.assertIn("apply_scope: applyScope === 'all' ? 'all' : 'session'", app_settings_js)

    def test_terminals_page_applies_scoped_font_updates(self):
        # OD-14: default scope restyles every terminal of the ACTIVE session
        # (group) and records a per-group override; 'all' pushes to every
        # session — including cached hidden groups — and drops the overrides.
        terminals_js = self._static("js/terminals.js")
        self.assertIn("const groupFontOverrides = new Map();", terminals_js)
        self.assertIn("function applyGroupFontOverride(index)", terminals_js)
        apply_font = terminals_js[
            terminals_js.index("function applyAppConfigTerminalFont(message)"):
            terminals_js.index("/* Recover app-config changes missed")
        ]
        self.assertIn("terminalConfig.apply_scope === 'all'", apply_font)
        self.assertIn("groupFontOverrides.clear();", apply_font)
        self.assertIn("groupFontOverrides.set(activeFontOverrideGroupKey(), override);", apply_font)
        # Session scope restyles the whole visible group, not a focused pane.
        self.assertNotIn("_focusedTerminalIndex", apply_font)
        self.assertIn("terminals.forEach((terminal, index)", apply_font)
        # 'all' reaches the hidden sessions' live xterms in the group cache …
        self.assertIn("cachedGroupViews.forEach(cached => {", apply_font)
        # … and only the all-sessions path moves the new-pane default.
        dataset_updates = apply_font.index("document.body.dataset.terminalFontSize")
        self.assertGreater(dataset_updates, apply_font.index("if (applyToAll) {"))
        # A session keeps its override when panes are rebuilt or split.
        attach_fn = terminals_js[
            terminals_js.index("function attachTerminal(index)"):
            terminals_js.index("Update status badge for a single terminal")
        ]
        self.assertIn("applyGroupFontOverride(index);", attach_fn)

    # ── ISSUE-2026-013 — per-agent auto-mode toggles ──

    def test_agent_options_expose_registry_auto_mode_flags(self):
        options = {item["value"]: item for item in web_agents._agent_options()}
        self.assertEqual(options["claude"]["auto_mode_flag"], "--permission-mode auto")
        self.assertEqual(
            options["codex"]["auto_mode_flag"],
            "--sandbox workspace-write --ask-for-approval on-request",
        )
        self.assertEqual(options["copilot"]["auto_mode_flag"], "--allow-all-tools")
        self.assertEqual(options["kimi"]["auto_mode_flag"], "--auto-approve")
        self.assertEqual(options["kilo"]["auto_mode_flag"], "--yolo")
        self.assertEqual(options["opencode"]["auto_mode_flag"], "")
        self.assertEqual(options["other"]["auto_mode_flag"], "")

    def test_agent_options_expose_registry_auto_mode_descriptions(self):
        """Wave 4 / 7.b: every flag-carrying agent surfaces its helper text."""
        options = {item["value"]: item for item in web_agents._agent_options()}
        for key in ("claude", "codex", "copilot", "kimi", "kilo"):
            with self.subTest(agent=key):
                self.assertTrue(options[key]["auto_mode_description"])
        self.assertEqual(options["opencode"]["auto_mode_description"], "")
        self.assertEqual(options["other"]["auto_mode_description"], "")

    def test_agent_registry_includes_kimi_entry(self):
        """Wave 2 / 7.a (OD-11): Kimi Code CLI is registered with --auto-approve."""
        entry = web_agents.AGENT_REGISTRY.get("kimi")
        self.assertIsInstance(entry, dict)
        self.assertEqual(entry["binary"], "kimi")
        self.assertEqual(entry["display_name"], "Kimi Code CLI")
        self.assertEqual(entry["auto_mode"]["flag"], "--auto-approve")
        self.assertTrue(entry["auto_mode"]["description"])
        self.assertIn("kimi --version", entry["verify"])
        environments = entry["environments"]
        for key in ("windows_native", "wsl_linux", "ssh"):
            with self.subTest(environment=key):
                self.assertTrue(environments[key]["supported"])
        # Install commands confirmed against the official docs (OD-11).
        windows_commands = [
            option["command"]
            for option in environments["windows_native"]["install_options"]
        ]
        self.assertIn(
            "Invoke-RestMethod https://code.kimi.com/install.ps1 | Invoke-Expression",
            windows_commands,
        )
        linux_commands = [
            option["command"]
            for option in environments["wsl_linux"]["install_options"]
        ]
        self.assertIn("curl -LsSf https://code.kimi.com/install.sh | bash", linux_commands)
        self.assertIn("uv tool install --python 3.13 kimi-cli", linux_commands)

    def test_auto_mode_flag_rejects_malformed_registry_values(self):
        with patch.dict(
            web_agents.AGENT_REGISTRY,
            {"badflag": {"auto_mode": {"flag": "rm -rf /"}}},
        ):
            self.assertEqual(web_agents._agent_auto_mode_flag("badflag"), "")
        with patch.dict(
            web_agents.AGENT_REGISTRY,
            {"nodash": {"auto_mode": {"flag": "yolo"}}},
        ):
            self.assertEqual(web_agents._agent_auto_mode_flag("nodash"), "")
        # Shell metacharacters must never survive validation, even when the
        # value rides behind a real-looking option token.
        for smuggled in ("--go; rm -rf /", "--x $(whoami)", "--x `id`", "--a|b", "--a  b"):
            with patch.dict(
                web_agents.AGENT_REGISTRY,
                {"smuggle": {"auto_mode": {"flag": smuggled}}},
            ):
                self.assertEqual(web_agents._agent_auto_mode_flag("smuggle"), "")

    def test_auto_mode_flag_allows_multi_token_option_values(self):
        with patch.dict(
            web_agents.AGENT_REGISTRY,
            {"multi": {"auto_mode": {"flag": "--sandbox workspace-write --ask-for-approval on-request"}}},
        ):
            self.assertEqual(
                web_agents._agent_auto_mode_flag("multi"),
                "--sandbox workspace-write --ask-for-approval on-request",
            )

    def test_compose_agent_startup_command_variants(self):
        def session(**overrides):
            base = {
                "initial_command": "claude",
                "initial_command_mode": "agent",
                "agent_selection": "claude",
                "agent_auto_mode": True,
            }
            base.update(overrides)
            return SimpleNamespace(**base)

        compose = web_agents._compose_agent_startup_command
        self.assertEqual(compose(session()), "claude --permission-mode auto")
        self.assertEqual(compose(session(agent_auto_mode=False)), "claude")
        self.assertEqual(
            compose(session(initial_command="opencode", agent_selection="opencode")),
            "opencode",
        )
        # A custom command never gains flags, even with the toggle persisted.
        self.assertEqual(
            compose(
                session(
                    initial_command="my-agent --custom",
                    agent_selection="other",
                    custom_agent="my-agent --custom",
                )
            ),
            "my-agent --custom",
        )
        self.assertEqual(
            compose(session(initial_command_mode="command")), "claude"
        )
        self.assertEqual(compose(session(initial_command="")), "")

    def test_startup_sequence_sends_composed_auto_mode_command(self):
        connection = {"kind": "ssh", "shell_kind": "posix"}
        session = SimpleNamespace(
            directory="",
            initial_command="claude",
            initial_command_mode="agent",
            agent_selection="claude",
            agent_auto_mode=True,
        )
        with patch.object(web_terminal_io, "_send_connection_input") as send:
            web_terminal_io._run_startup_sequence(connection, session)
        send.assert_called_once_with(connection, "claude --permission-mode auto\n")

        session.agent_auto_mode = False
        with patch.object(web_terminal_io, "_send_connection_input") as send:
            web_terminal_io._run_startup_sequence(connection, session)
        send.assert_called_once_with(connection, "claude\n")

    def test_normalize_terminal_entries_gates_agent_auto_mode(self):
        normalized = web_saved_sessions._normalize_terminal_entries(
            [
                {
                    "startup_mode": "agent",
                    "agent_selection": "claude",
                    "initial_command": "claude",
                    "agent_auto_mode": True,
                },
                {"startup_mode": "terminal", "agent_auto_mode": True},
                {"startup_mode": "agent", "agent_selection": "claude"},
            ]
        )
        self.assertTrue(normalized[0]["agent_auto_mode"])
        self.assertFalse(normalized[1]["agent_auto_mode"])
        # Backward compatibility: presets without the field default to off.
        self.assertFalse(normalized[2]["agent_auto_mode"])

    def test_workspace_merge_carries_agent_auto_mode(self):
        base = {
            "connection_mode": "wsl",
            "terminal_count": 1,
            "terminals": [
                {
                    "startup_mode": "agent",
                    "agent_selection": "claude",
                    "initial_command": "claude",
                    "agent_auto_mode": False,
                }
            ],
        }
        workspace = {
            "terminal_count": 1,
            "terminals": [
                {
                    "startup_mode": "agent",
                    "agent_selection": "claude",
                    "initial_command": "claude",
                    "agent_auto_mode": True,
                }
            ],
        }
        merged = web_saved_sessions._merge_workspace_session_config(base, workspace)
        self.assertTrue(merged["terminals"][0]["agent_auto_mode"])

        workspace["terminals"][0] = {"startup_mode": "terminal"}
        merged = web_saved_sessions._merge_workspace_session_config(base, workspace)
        self.assertFalse(merged["terminals"][0]["agent_auto_mode"])

    def test_workspace_merge_persists_live_browser_tabs(self):
        """Save Workspace captures the pane's live strip, not its launch URL."""
        base = {
            "connection_mode": "wsl",
            "terminal_count": 1,
            "terminals": [
                {
                    "startup_mode": "browser",
                    "initial_command": "http://127.0.0.1:3000",
                    "browser_tabs": ["http://127.0.0.1:3000"],
                }
            ],
        }
        workspace = {
            "terminal_count": 1,
            "terminals": [
                {
                    "startup_mode": "browser",
                    "initial_command": "http://127.0.0.1:5050/",
                    "browser_tabs": [
                        "http://127.0.0.1:5050/",
                        "http://127.0.0.1:5050/terminals",
                    ],
                    "browser_active_tab": 1,
                }
            ],
        }

        merged = web_saved_sessions._merge_workspace_session_config(base, workspace)

        terminal = merged["terminals"][0]
        self.assertEqual(
            terminal["browser_tabs"],
            ["http://127.0.0.1:5050/", "http://127.0.0.1:5050/terminals"],
        )
        self.assertEqual(terminal["browser_active_tab"], 1)
        self.assertEqual(terminal["initial_command"], "http://127.0.0.1:5050/terminals")

    def test_workspace_merge_clears_browser_tabs_when_pane_leaves_browser_mode(self):
        base = {
            "connection_mode": "wsl",
            "terminal_count": 1,
            "terminals": [
                {
                    "startup_mode": "browser",
                    "initial_command": "http://127.0.0.1:3000",
                    "browser_tabs": ["http://127.0.0.1:3000"],
                }
            ],
        }
        workspace = {"terminal_count": 1, "terminals": [{"startup_mode": "terminal"}]}

        merged = web_saved_sessions._merge_workspace_session_config(base, workspace)

        self.assertEqual(merged["terminals"][0]["browser_tabs"], [])
        self.assertEqual(merged["terminals"][0]["browser_active_tab"], 0)

    def test_normalize_terminal_entries_upgrades_pre_tabs_browser_pane(self):
        """A preset saved before tabs existed becomes a one-tab strip."""
        normalized = web_saved_sessions._normalize_terminal_entries(
            [{"startup_mode": "browser", "initial_command": "http://127.0.0.1:5050/"}],
            connection_mode="wsl",
        )
        self.assertEqual(normalized[0]["browser_tabs"], ["http://127.0.0.1:5050/"])
        self.assertEqual(normalized[0]["browser_active_tab"], 0)
        self.assertEqual(normalized[0]["initial_command"], "http://127.0.0.1:5050/")

    def test_normalize_terminal_entries_bounds_browser_tabs(self):
        many = [f"http://127.0.0.1:{4000 + index}" for index in range(20)]
        normalized = web_saved_sessions._normalize_terminal_entries(
            [{"startup_mode": "browser", "browser_tabs": many, "browser_active_tab": 19}],
            connection_mode="wsl",
        )
        self.assertEqual(
            len(normalized[0]["browser_tabs"]), web_saved_sessions.BROWSER_MAX_TABS
        )
        self.assertEqual(
            normalized[0]["browser_active_tab"],
            web_saved_sessions.BROWSER_MAX_TABS - 1,
        )

    def test_sessions_post_round_trips_agent_auto_mode(self):
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        with patch.object(api, "_sanitize_agent_launch_commands", return_value=[]), patch.object(
            api.socketio, "start_background_task"
        ):
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "sessions": [
                        {
                            "directory": self.temp_dir.name,
                            "title": "Agent",
                            "startup_mode": "agent",
                            "initial_command_mode": "agent",
                            "initial_command": "claude",
                            "agent_selection": "claude",
                            "agent_auto_mode": True,
                        }
                    ],
                },
            )
        self.assertEqual(response.status_code, 201)
        session = response.get_json()["sessions"][0]
        self.assertTrue(session["agent_auto_mode"])
        # The persisted command stays the base agent key so preflight and
        # saved sessions keep matching on the executable.
        self.assertEqual(session["initial_command"], "claude")

    def test_runtime_state_snapshot_includes_agent_auto_mode(self):
        self.assertIn("agent_auto_mode", web_runtime_state._SESSION_SNAPSHOT_FIELDS)

    def test_runtime_state_snapshot_includes_tab_views_and_md_appearance(self):
        """2.f: restart restore replays per-tab views and the Markdown appearance."""
        self.assertIn("explorer_tab_views", web_runtime_state._SESSION_SNAPSHOT_FIELDS)
        self.assertIn("explorer_md_preset", web_runtime_state._SESSION_SNAPSHOT_FIELDS)
        self.assertIn("explorer_md_font", web_runtime_state._SESSION_SNAPSHOT_FIELDS)
        self.assertIn("explorer_theme", web_runtime_state._SESSION_SNAPSHOT_FIELDS)

    def test_launcher_wires_the_auto_mode_toggle(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("auto_mode_flag", html)
        self.assertIn("auto_mode_description", html)

        launcher_js = self._static("js/launcher.js")
        self.assertIn("function agentAutoModeFlag(agentValue)", launcher_js)
        self.assertIn("function agentAutoModeDescription(agentValue)", launcher_js)
        self.assertIn("function syncTerminalAgentAutoModeState(row, commandMode, selectedAgent)", launcher_js)
        self.assertIn("t-agent-auto-mode", launcher_js)
        self.assertIn("t-agent-auto-field", launcher_js)
        collect = launcher_js[
            launcher_js.index("function collectTerminalDrafts()"):
            launcher_js.index("function renderCountOptions()")
        ]
        self.assertIn("agent_auto_mode:", collect)

        # 7.b (OD-12 case a): the shared pane-field builder carries the toggle
        # into buildSessionsFromConfig, which serves both launch and restore.
        launch = launcher_js[
            launcher_js.index("function buildSessionsFromConfig(config, count)"):
            launcher_js.index("async function launchSessions()")
        ]
        self.assertIn("buildPaneLaunchFields(terminal, startupMode)", launch)
        shared_js = self._static("js/shared.js")
        self.assertIn(
            "agent_auto_mode: resolvedStartupMode === 'agent'",
            shared_js,
        )

        # The help line shows the composed command plus the registry description.
        sync = launcher_js[
            launcher_js.index("function syncTerminalAgentAutoModeState(row, commandMode, selectedAgent)"):
            launcher_js.index("function resetTerminalCommandOnModeChange(")
        ]
        self.assertIn('`Launches as "${selectedAgent} ${flag}".', sync)
        self.assertIn("agentAutoModeDescription(selectedAgent)", sync)

        terminals_js = self._static("js/terminals.js")
        entry = terminals_js[
            terminals_js.index("function buildWorkspaceTerminalEntry(terminal, index, connectionMode)"):
            terminals_js.index("function buildActiveWorkspaceSessionConfig(")
        ]
        self.assertIn("agent_auto_mode:", entry)
