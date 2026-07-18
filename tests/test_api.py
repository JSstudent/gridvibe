import io
import json
import re
import shutil
import socket
import stat
import subprocess
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
            "js/shared.js",
            "js/launcher.js",
            "css/terminals.css",
            "js/terminals.js",
        ):
            marker = f"/static/{asset}"
            if marker in html:
                html += "\n" + self.client.get(marker).get_data(as_text=True)
        return html

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
        self.assertIn("function refreshLauncherMicrophones()", html)
        self.assertIn('/api/voice-prefs', html)
        self.assertIn('<select id="appWhisperModel">', html)
        self.assertIn('<option value="base">base</option>', html)
        self.assertIn('<option value="large-v3-turbo">large-v3-turbo</option>', html)
        self.assertIn("const APP_CONFIG_UPDATE_STORAGE_KEY = 'gridvibe.appConfigUpdated';", html)
        self.assertIn("const APP_CONFIG_BROADCAST_CHANNEL = 'gridvibe.appConfig';", html)
        self.assertIn("function notifyAppConfigUpdated(appSettings)", html)
        self.assertIn("notifyAppConfigUpdated(data);", html)

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
        self.assertIn("const targetGroupId = getActiveWorkspaceGroupId();", save_handler_html)
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
        self.assertIn("session.explorer_root_directory || session.directory", entry_html)
        self.assertNotIn("terminal?._explorerPath", entry_html)
        self.assertIn("Boolean(terminal?._explorerTreeSidebarOpen)", entry_html)
        self.assertIn("Boolean(terminal?._explorerGitSidebarOpen)", entry_html)
        self.assertIn("function restoreExplorerSidebarState(index)", html)
        self.assertIn("_explorerTreeSidebarOpen: Boolean(session.explorer_tree_open)", html)
        self.assertIn("_explorerGitSidebarOpen: Boolean(session.explorer_git_open)", html)
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
        self.assertIn("surface_mode: appSettings.workspace?.surface_mode === 'max' ? 'max' : 'normal'", html)
        self.assertIn("initial_command_mode: terminal.startup_mode === 'explorer'", html)
        self.assertIn("agent_selection: terminal.initial_command_mode === 'agent'", html)
        self.assertIn("data-explorer-tree-open=", html)
        self.assertIn("data-explorer-git-open=", html)
        self.assertIn("explorer_tree_open: terminal.startup_mode === 'explorer'", html)
        self.assertIn("explorer_git_open: terminal.startup_mode === 'explorer'", html)
        self.assertIn('<option value="browser"', html)
        self.assertIn('class="field t-browser-field', html)
        self.assertIn("function normalizeBrowserPaneUrl(value)", html)
        self.assertIn("terminal.startup_mode === 'browser'", html)
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

    def test_terminals_page_uses_icon_only_settings_button(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('class="btn btn-neutral btn-icon settings-window-btn"', html)
        self.assertIn('aria-label="Open settings"', html)
        self.assertIn('class="vibe-flow-icon"', html)

    def test_docs_images_route_serves_gridvibe_icon(self):
        response = self.client.get("/docs/images/GridVibe_icon.ico")

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.get_data()), 0)

    def test_terminals_page_exposes_per_terminal_refresh_control(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn('data-terminal-refresh="${i}"', html)
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
        # Both close paths carry the pre-close track weights into the restore.
        self.assertEqual(
            html.count("splitColumnWeights: cloneSplitTrackWeights(splitColumnWeights),"),
            2,
        )
        self.assertEqual(
            html.count("splitRowWeights: cloneSplitTrackWeights(splitRowWeights),"),
            2,
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
        switch_end = html.index("async function closeSplitPane(index)", switch_start)
        self.assertNotIn("teardownCurrentGrid();", html[switch_start:switch_end])

    def test_terminals_page_exposes_browser_pane_rendering_hooks(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function isBrowserSession(session)", html)
        self.assertIn("function isBrowserPaneInstance(terminal)", html)
        self.assertIn("function getBrowserSessionUrl(session)", html)
        self.assertIn("function normalizeBrowserUrlInput(value)", html)
        self.assertIn("class=\"browser-surface\"", html)
        self.assertIn("class=\"browser-frame\"", html)
        self.assertIn("class=\"browser-url-input\"", html)
        self.assertIn("data-browser-open=\"${i}\"", html)
        self.assertIn("data-session-browser-toggle=\"${i}\"", html)
        self.assertIn("function reloadBrowserPane(index)", html)
        self.assertIn("function openBrowserPaneExternally(index)", html)
        self.assertIn("async function switchSessionBrowserMode(index)", html)
        self.assertIn("async function navigateBrowserPane(index, value)", html)
        self.assertIn("sandbox=\"allow-downloads allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts\"", html)

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
            "refreshed = await openExplorerFile(index, pane._explorerFilePath, { showLoading: false, preserveScroll: true });",
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
        self.assertIn("card.dataset.explorerThemeSource = hasExplorerThemeOverride(explorerThemeKey) ? 'override' : 'default';", html)
        self.assertIn("if (card.dataset.explorerThemeSource === 'override')", html)
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
        self.assertIn("function explorerFindRanges(content, query, maxMatches = EXPLORER_SEARCH_MAX_MATCHES)", html)
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
        self.assertIn("ranges.capped = ranges.length >= maxMatches;", html)
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
        self.assertIn("overflow: scroll;", html)
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
        self.assertIn("function revealExplorerTreePath(index)", html)
        self.assertIn("function reloadExplorerTree(index)", html)
        self.assertIn(
            'wireCardButton(card, `[data-explorer-tree-toggle="${i}"]`, () => toggleExplorerTreeSidebar(i));',
            html,
        )
        self.assertIn(".filter(entry => !entry.deleted)", html)

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

    def test_launcher_round_trips_explorer_open_tabs(self):
        """ISSUE-2026-015: launcher carries open tabs through without editing them."""
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function parseExplorerOpenTabsDataset(value)", html)
        self.assertIn("data-explorer-open-tabs=", html)
        self.assertIn("data-explorer-active-tab=", html)
        self.assertIn("explorer_open_tabs: commandMode === 'explorer'", html)
        self.assertIn("explorer_open_tabs: terminal.startup_mode === 'explorer'", html)

    def test_terminals_page_explorer_sidebar_supports_tree_and_git_together(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("explorer-sidebar-splitter", html)
        self.assertIn("data-explorer-sidebar-splitter", html)
        self.assertIn("function syncExplorerSidebar(index)", html)
        self.assertIn("function applyExplorerSidebarSplit(index)", html)
        self.assertIn("function wireExplorerSidebarSplitter(index)", html)
        self.assertIn("sidebar.classList.toggle('split', treeOpen && gitOpen);", html)
        self.assertIn("main.classList.toggle('tree-open', treeOpen);", html)
        self.assertIn("main.classList.toggle('git-open', gitOpen);", html)
        self.assertIn(
            ".explorer-sidebar.split {\n            grid-template-rows:"
            " var(--explorer-sidebar-tree-height, minmax(0, 1fr)) 6px minmax(0, 1fr);",
            html,
        )

    def test_terminals_page_explorer_diff_search_hooks_are_present(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("} else if (query && view === 'diff') {", html)
        self.assertIn("const diffMarks = markExplorerSearchInElement(diff, query, state.activeIndex || 0);", html)
        self.assertIn("renderExplorerDiff(index);", html)
        self.assertIn("if (activeExplorerFileView(index) === 'diff')", html)
        self.assertIn('data-explorer-file-panel="diff"', html)

    def test_terminals_page_explorer_markdown_source_sections_can_be_collapsed(self):
        response = self.client.get("/terminals")

        self.assertEqual(response.status_code, 200)
        html = self._page_html(response)
        self.assertIn("function explorerMarkdownHeadingLevel(line)", html)
        self.assertIn("function explorerMarkdownHeadingLevels(records)", html)
        self.assertIn("data-explorer-markdown-section", html)
        self.assertIn("function toggleExplorerMarkdownSection(index, lineNumber)", html)
        self.assertIn("function wireExplorerMarkdownSectionControls(index)", html)
        self.assertIn("pane._explorerMarkdownCollapsedLines = new Set();", html)
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
        self.assertIn("const EXPLORER_MD_PRESETS = ['default', 'paper', 'contrast'];", html)
        self.assertIn("const EXPLORER_MD_FONTS = ['system', 'serif', 'mono'];", html)
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
        self.assertIn("--md-preview-surface: var(--md-preset-paper-bg);", html)
        self.assertIn("--md-preset-paper-bg: #f4ecd8;", html)

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
        self.assertIn(".sessions-menu-panel {", html)
        self.assertIn(".sessions-menu-item {", html)
        self.assertIn(">Import Session ...</button>", html)

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
        self.assertIn("applySurfaceMode(normalizeSurfaceMode(DEFAULT_SURFACE_MODE) === 'max');", html)
        self.assertIn("document.body.classList.toggle('surface-max', active);", html)
        self.assertIn("redrawAttachedTerminals(attachedIndices, { forceResize: true });", html)
        self.assertIn("const APP_CONFIG_UPDATE_STORAGE_KEY = 'gridvibe.appConfigUpdated';", html)
        self.assertIn("const APP_CONFIG_BROADCAST_CHANNEL = 'gridvibe.appConfig';", html)
        self.assertIn("function applyAppConfigSurfaceMode(message)", html)
        self.assertIn("surfaceModeChangedManually = true;", html)
        self.assertIn("surfaceModeAppliedGroups.clear();", html)
        self.assertIn("applySurfaceMode(mode === 'max', { persist: true, refit: true });", html)
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
        with patch.object(api.runtime_config, "voice_engine", "whisper"), patch.object(
            api.runtime_config, "whisper_model", "base"
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
                },
                "terminal": {
                    "font_family": api.runtime_config.terminal_font_family,
                    "font_size": api.runtime_config.terminal_font_size,
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

    def test_explorer_git_revert_rejects_untracked_file(self):
        repo_dir = self._init_committed_repo()
        untracked = repo_dir / "scratch.txt"
        untracked.write_text("keep me\n", encoding="utf-8")
        session_id = self._create_explorer_session(repo_dir)

        response = self.client.post(
            f"/api/explorer/{session_id}/git/revert",
            json={"path": "scratch.txt"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("untracked", response.get_json()["error"].lower())
        self.assertTrue(untracked.exists())

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
        self.assertIn("function handleExplorerCopyPathMenu(event, index)", html)
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
        self.assertIn("explorer-git-revert-btn", html)
        self.assertIn("function explorerGitCanRevert(status)", html)
        self.assertIn("['modified', 'deleted', 'renamed'].includes(status", html)
        self.assertIn("action === 'stage' && explorerGitCanRevert(status)", html)
        self.assertIn("async function explorerGitRevertFile(index, path)", html)
        self.assertIn("explorerGitRevertFile(index, button.dataset.explorerGitRevert || '');", html)
        self.assertIn("performExplorerGitAction(index, 'revert', { path })", html)
        # Irreversible action uses the in-page confirm shell, not window.confirm.
        self.assertIn("function openGenericConfirmModal(", html)
        self.assertIn('id="genericConfirmModal"', html)
        self.assertIn("title: 'Discard changes?'", html)
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
        file_path.write_text(body, encoding="utf-8")
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
        self.assertEqual(data["surface_mode"], "max")
        self.assertEqual(data["group"]["surface_mode"], "max")
        self.assertEqual(data["sessions"][0]["initial_command_mode"], "agent")
        self.assertEqual(data["sessions"][0]["agent_selection"], "other")
        self.assertEqual(data["sessions"][0]["custom_agent"], "claude-code")

        listed = self.client.get(f"/api/sessions?group={data['group_id']}")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()["surface_mode"], "max")
        self.assertEqual(
            listed.get_json()["workspace_layout"]["split_slot_rects"][1]["x"],
            3,
        )

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

        with patch.object(web_voice, "_ensure_whisper_model", return_value=mock_model), patch.object(
            web_voice, "_pcm16le_to_float32", return_value="audio-array"
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

    def test_terminals_page_splits_the_longer_pane_dimension(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
        self.assertIn("grid?.classList.contains('layout-2-vertical')", html)
        self.assertIn("return candidates.includes('horizontal') ? 'horizontal' : '';", html)
        self.assertIn(
            "const preferred = bounds && bounds.height > bounds.width ? 'horizontal' : 'vertical';",
            html,
        )

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
        self.assertIn(
            "return getSharedGridEdgeSegments(rects, axis, lineIndex).length > 0;",
            html,
        )
        self.assertNotIn("handle.style.height = `${metrics.gridContentHeight}px`;", html)
        self.assertNotIn("handle.style.width = `${metrics.gridContentWidth}px`;", html)

    def test_terminals_page_resize_validation_enforces_minimums(self):
        response = self.client.get("/terminals")
        html = self._page_html(response)
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
    """Issue 7 — verify status emission is serialized with session removal."""

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
        self.assertIn(f"/static/js/terminals.js?v={__version__}", terminals_html)
        # shared.js must load before each page script so its globals exist first.
        self.assertLess(
            launcher_html.index("js/shared.js"), launcher_html.index("js/launcher.js")
        )
        self.assertLess(
            terminals_html.index("js/shared.js"), terminals_html.index("js/terminals.js")
        )

    def test_extracted_assets_are_served_without_jinja(self):
        for filename in (
            "css/launcher.css",
            "css/terminals.css",
            "js/shared.js",
            "js/launcher.js",
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
        """Finding 6.5 — buildGrid/_startVoice delegate to focused helpers."""
        terminals = self.client.get("/static/js/terminals.js").get_data(as_text=True)

        for name in (
            "createPaneInstance",
            "wireCardButton",
            "buildPaneCard",
            "wirePaneControls",
            "wirePaneInputForwarding",
            "_acquireMicStream",
            "_createVoicePipeline",
            "_wireVoiceWorkletMessages",
            "_teardownVoicePipeline",
        ):
            with self.subTest(helper=name):
                self.assertEqual(
                    len(re.findall(rf"function {re.escape(name)}\(", terminals)), 1
                )

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
        self.assertLess(_function_length(terminals, "async function _startVoice("), 200)


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
        self.assertIn("border-color: var(--bg-deep) transparent", launcher_css)
        self.assertIn('[data-theme="light"] .button-tooltip-bubble::after',
                      launcher_css)

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
        launcher_js = self._static("js/launcher.js")
        notify = launcher_js[
            launcher_js.index("function notifyAppConfigUpdated(appSettings)"):
            launcher_js.index("async function loadAppSettings()")
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
        self.assertIn("async function reconcileAppConfigTheme()", terminals_js)
        reconcile_fn = terminals_js[
            terminals_js.index("async function reconcileAppConfigTheme()"):
            terminals_js.index("function setupAppConfigUpdateListeners()")
        ]
        self.assertIn("fetch('/api/app-config')", reconcile_fn)
        self.assertIn("applyAppConfigTheme(await response.json());", reconcile_fn)
        # reconnect path (guarded by the not-first-connect flag)
        connect_handler = terminals_js[
            terminals_js.index("let hadSocketConnection = false;"):
            terminals_js.index("socket.on('voice_result'")
        ]
        self.assertIn("reconcileAppConfigTheme();", connect_handler)
        # focus / pageshow recovery
        focus_wiring = terminals_js[
            terminals_js.index("window.addEventListener('focus'"):
            terminals_js.index("document.addEventListener('fullscreenchange'")
        ]
        self.assertEqual(focus_wiring.count("reconcileAppConfigTheme();"), 2)

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
        terminals_js = self._static("js/terminals.js")
        self.assertIn("const VOICE_OVERLAY_ID = 'voiceRecordingOverlay';", terminals_js)
        show_fn = terminals_js[
            terminals_js.index("function _showVoiceRecordingOverlay(index)"):
            terminals_js.index("function _hideVoiceRecordingOverlay()")
        ]
        self.assertIn("overlay.setAttribute('role', 'status');", show_fn)
        self.assertIn("overlay.setAttribute('aria-live', 'polite');", show_fn)
        self.assertIn('aria-hidden="true"', show_fn)
        self.assertIn(">Recording</span>", show_fn)
        # reuse the single element instead of stacking duplicates
        self.assertIn("if (!overlay) {", show_fn)
        self.assertIn("document.getElementById(VOICE_OVERLAY_ID)", show_fn)

    def test_overlay_appears_only_after_capture_actually_starts(self):
        terminals_js = self._static("js/terminals.js")
        show_fn = terminals_js[
            terminals_js.index("function _showVoiceRecordingOverlay(index)"):
            terminals_js.index("function _hideVoiceRecordingOverlay()")
        ]
        # rapid-release guard: no overlay when the state was torn down mid-start
        self.assertIn("if (!_voiceState[index]?.recording) {", show_fn)
        start_fn = terminals_js[
            terminals_js.index("async function _startVoice(index)"):
            terminals_js.index("async function _acquireMicStream(")
        ]
        self.assertLess(
            start_fn.index("_voiceState[index] = state;"),
            start_fn.index("_showVoiceRecordingOverlay(index);"),
        )

    def test_overlay_hidden_from_every_cleanup_path(self):
        terminals_js = self._static("js/terminals.js")
        # start failure (permission denial / backend error)
        start_fn = terminals_js[
            terminals_js.index("async function _startVoice(index)"):
            terminals_js.index("async function _acquireMicStream(")
        ]
        catch_block = start_fn[start_fn.index("} catch (err) {"):]
        self.assertIn("_hideVoiceRecordingOverlay();", catch_block)
        # every stop path (toggle, PTT release, hold release, voice_status
        # error, _stopAllVoice on session switch/teardown) funnels here
        stop_fn = terminals_js[
            terminals_js.index("async function _stopVoice(index"):
            terminals_js.index("async function _stopAllVoice()")
        ]
        self.assertIn("_hideVoiceRecordingOverlay();", stop_fn)
        self.assertIn("_stopAllVoice();", terminals_js)

    def test_waveform_uses_analyser_with_fallback_and_stale_loop_guard(self):
        terminals_js = self._static("js/terminals.js")
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
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function _prefersReducedMotion()", terminals_js)
        self.assertIn("prefers-reduced-motion: reduce", terminals_js)
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
        terminals_js = self._static("js/terminals.js")
        self.assertIn("_wireVoiceHoldToTalk(card, i);", terminals_js)
        hold_fn = terminals_js[
            terminals_js.index("function _wireVoiceHoldToTalk(card, index)"):
            terminals_js.index("/* ── Push-to-talk ── */")
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
        terminals_js = self._static("js/terminals.js")
        self.assertIn("let _pttStopRequested = false;", terminals_js)
        keydown_idx = terminals_js.index(
            "if (!_voicePrefs.pttEnabled || !_voicePrefs.pttKeybind) return;"
        )
        keyup_idx = terminals_js.index("if (!_pttActive) return;")
        keydown_block = terminals_js[keydown_idx:keyup_idx]
        self.assertIn(
            "if (_pttStopRequested && _voiceState[index]?.recording) {", keydown_block
        )
        keyup_block = terminals_js[
            keyup_idx:terminals_js.index("function _updateVoiceBtn(index, recording)")
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
            terminals_js.index("async function closeCurrentSession")
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
            launcher_js.index("function applyAppSettings")
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
        launcher_js = self._static("js/launcher.js")
        self.assertIn("host_key_policy", launcher_js)
        self.assertIn("appSshHostKeyPolicy", launcher_js)


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
        terminals_js = self._static("js/terminals.js")
        self.assertIn("function downloadExplorerFile(index)", terminals_js)
        self.assertIn("data-explorer-download", terminals_js)
        self.assertIn("/download?path=", terminals_js)
        terminals_css = self._static("css/terminals.css")
        self.assertIn(".explorer-download-btn", terminals_css)

    def test_native_window_downloads_route_through_the_bridge(self):
        # WebView2 drops anchor downloads, so the native window must use the
        # pywebview save_download bridge instead of the <a download> click.
        terminals_js = self._static("js/terminals.js")
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
        terminals_js = self._static("js/terminals.js")
        ptt_fn = terminals_js[
            terminals_js.index("function _findPttTerminalIndex()"):
            terminals_js.index("function findExplorerSearchTargetIndex()")
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

    def test_launch_persists_a_password_free_snapshot(self):
        self._launch_explorer_group()
        snapshot = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(len(snapshot["groups"]), 1)
        group = snapshot["groups"][0]
        self.assertEqual(group["name"], "Workspace")
        self.assertEqual(group["connection_mode"], "wsl")
        self.assertEqual(len(group["sessions"]), 1)
        session = group["sessions"][0]
        self.assertEqual(session["startup_mode"], "explorer")
        self.assertNotIn("password", session)

    def test_runtime_state_is_only_restorable_without_active_groups(self):
        self._launch_explorer_group()
        response = self.client.get("/api/runtime-state")
        payload = response.get_json()
        self.assertFalse(payload["restorable"])
        self.assertEqual(payload["active_group_count"], 1)

        # simulate a backend restart: in-memory groups vanish, the file stays
        api.session_manager.reset_sessions()
        response = self.client.get("/api/runtime-state")
        payload = response.get_json()
        self.assertTrue(payload["restorable"])
        self.assertEqual(len(payload["groups"]), 1)

    def test_replaying_the_snapshot_relaunches_the_group(self):
        self._launch_explorer_group()
        snapshot = json.loads(self.state_path.read_text(encoding="utf-8"))
        api.session_manager.reset_sessions()

        group = snapshot["groups"][0]
        response = self.client.post(
            "/api/sessions",
            json={
                "sessions": group["sessions"],
                "connection_mode": group["connection_mode"],
                "layout": group["layout"],
                "workspace_layout": group["workspace_layout"],
                "surface_mode": group["surface_mode"],
                "session_name": group["name"],
                "saved_session_id": group["saved_session_id"],
            },
        )
        self.assertEqual(response.status_code, 201)
        restored = response.get_json()
        self.assertEqual(restored["group"]["name"], "Workspace")
        self.assertEqual(restored["sessions"][0]["startup_mode"], "explorer")

    def test_closing_the_group_empties_the_snapshot(self):
        group_id = self._launch_explorer_group()
        response = self.client.delete(f"/api/sessions?group={group_id}")
        self.assertEqual(response.status_code, 200)
        snapshot = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["groups"], [])
        self.assertIsNone(web_runtime_state.load_restorable_workspace())

    def test_stale_or_missing_snapshots_are_not_restorable(self):
        self.assertIsNone(web_runtime_state.load_restorable_workspace())
        stale = {
            "version": 1,
            "saved_at": time.time() - web_runtime_state.RUNTIME_STATE_MAX_AGE_SECONDS - 60,
            "groups": [{"name": "old"}],
        }
        self.state_path.write_text(json.dumps(stale), encoding="utf-8")
        self.assertIsNone(web_runtime_state.load_restorable_workspace())
        self.state_path.write_text("not json", encoding="utf-8")
        self.assertIsNone(web_runtime_state.load_restorable_workspace())

    def test_delete_endpoint_dismisses_the_snapshot(self):
        self._launch_explorer_group()
        api.session_manager.reset_sessions()
        response = self.client.delete("/api/runtime-state")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.state_path.exists())
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
        self.assertIn("fetch('/api/runtime-state', { method: 'DELETE' })", launcher_js)
        launcher_css = self._static("css/launcher.css")
        self.assertIn(".restore-banner", launcher_css)


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
        launcher_css = self._static("css/launcher.css")
        # The override that disabled the scroll region must stay gone.
        self.assertNotIn(
            ".app-settings-card .settings-grid",
            launcher_css,
            "App Settings must not override the shared .settings-grid scroll model",
        )
        settings_grid = re.search(r"\.settings-grid \{(.*?)\}", launcher_css, re.DOTALL)
        self.assertIsNotNone(settings_grid)
        self.assertIn("overflow: auto", settings_grid.group(1))
        self.assertIn("min-height: 0", settings_grid.group(1))
        # Pinned header/body/actions rows: the actions row stays out of the
        # scrollable body, so a taller voice panel can never paint under it.
        modal_card = re.search(r"\n        \.modal-card \{(.*?)\}", launcher_css, re.DOTALL)
        self.assertIsNotNone(modal_card)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", modal_card.group(1))
        self.assertIn(".app-settings-actions", launcher_css)

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

        launcher_js = self._static("js/launcher.js")
        collect = launcher_js[
            launcher_js.index("function collectAppSettingsForm()"):
            launcher_js.index("function notifyAppConfigUpdated(appSettings)")
        ]
        self.assertIn("terminal: {", collect)
        self.assertIn("appTerminalFontFamily", collect)
        self.assertIn("appTerminalFontSize", collect)
        self.assertIn("appTerminalMaxSessions", collect)
        notify = launcher_js[
            launcher_js.index("function notifyAppConfigUpdated(appSettings)"):
            launcher_js.index("async function loadAppSettings()")
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
        self.assertIn("term.options.fontSize", apply_font)
        self.assertIn("term.options.fontFamily", apply_font)
        self.assertIn("scheduleFit(index);", apply_font)

    # ── ISSUE-2026-013 — per-agent auto-mode toggles ──

    def test_agent_options_expose_registry_auto_mode_flags(self):
        options = {item["value"]: item for item in web_agents._agent_options()}
        self.assertEqual(options["claude"]["auto_mode_flag"], "--enable-auto-mode")
        self.assertEqual(options["codex"]["auto_mode_flag"], "--full-auto")
        self.assertEqual(options["copilot"]["auto_mode_flag"], "--allow-all-tools")
        self.assertEqual(options["opencode"]["auto_mode_flag"], "")
        self.assertEqual(options["other"]["auto_mode_flag"], "")

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
        self.assertEqual(compose(session()), "claude --enable-auto-mode")
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
        send.assert_called_once_with(connection, "claude --enable-auto-mode\n")

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

    def test_launcher_wires_the_auto_mode_toggle(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("auto_mode_flag", html)

        launcher_js = self._static("js/launcher.js")
        self.assertIn("function agentAutoModeFlag(agentValue)", launcher_js)
        self.assertIn("function syncTerminalAgentAutoModeState(row, commandMode, selectedAgent)", launcher_js)
        self.assertIn("t-agent-auto-mode", launcher_js)
        self.assertIn("t-agent-auto-field", launcher_js)
        collect = launcher_js[
            launcher_js.index("function collectTerminalDrafts()"):
            launcher_js.index("function renderCountOptions()")
        ]
        self.assertIn("agent_auto_mode:", collect)

        terminals_js = self._static("js/terminals.js")
        entry = terminals_js[
            terminals_js.index("function buildWorkspaceTerminalEntry(terminal, index, connectionMode)"):
            terminals_js.index("function buildActiveWorkspaceSessionConfig(")
        ]
        self.assertIn("agent_auto_mode:", entry)
