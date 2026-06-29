import unittest
from pathlib import Path
from unittest.mock import patch

from web import webview_launcher


class _ExplodingWindow:
    def __init__(self):
        self.show_calls = 0
        self.restore_calls = 0
        self.loaded_urls = []
        self.on_top = False

    def show(self):
        self.show_calls += 1

    def restore(self):
        self.restore_calls += 1

    def load_url(self, url):
        self.loaded_urls.append(url)
        raise AssertionError("load_url should not be called")


class WebviewLauncherTestCase(unittest.TestCase):
    def test_launcher_close_exits_even_when_session_window_is_still_open(self):
        self.assertTrue(
            webview_launcher._should_exit_after_window_close("launcher", {"session"})
        )

    def test_session_close_keeps_app_running_while_launcher_is_open(self):
        self.assertFalse(
            webview_launcher._should_exit_after_window_close("session", {"launcher"})
        )

    def test_last_window_close_exits_app(self):
        self.assertTrue(
            webview_launcher._should_exit_after_window_close("session", set())
        )

    def test_bring_to_front_uses_show_only(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        result = api_bridge._bring_to_front(window)

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.restore_calls, 0)
        self.assertFalse(window.on_top)

    def test_bring_to_front_restores_minimized_window_before_show(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._set_window_minimized("session", True)

        result = api_bridge._bring_to_front(window, "session")

        self.assertTrue(result)
        self.assertEqual(window.restore_calls, 1)
        self.assertEqual(window.show_calls, 1)
        self.assertFalse(api_bridge._is_window_minimized("session"))

    def test_bring_to_front_skips_top_most_pulse_for_session_window(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(api_bridge, "_pulse_on_top") as pulse:
            result = api_bridge._bring_to_front(window, "session")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        pulse.assert_not_called()

    def test_bring_to_front_skips_top_most_pulse_for_launcher_window(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(api_bridge, "_pulse_on_top") as pulse:
            result = api_bridge._bring_to_front(window, "launcher")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        pulse.assert_not_called()

    def test_focus_session_window_uses_show_only(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._attach_session_window(window)

        result = api_bridge.focus_session_window()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.restore_calls, 0)
        self.assertEqual(window.loaded_urls, [])

    def test_focus_session_window_restores_minimized_window_without_reload(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._attach_session_window(window)
        api_bridge._set_window_minimized("session", True)

        result = api_bridge.focus_session_window()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(window.restore_calls, 1)
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.loaded_urls, [])

    def test_open_session_window_reuses_existing_window_without_reload(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._attach_session_window(window, "group-2")

        result = api_bridge.open_session_window("group-2")

        self.assertEqual(result, {"ok": True, "reused": True})
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.restore_calls, 0)
        self.assertEqual(window.loaded_urls, [])

    def test_open_session_window_reuses_existing_window_when_group_changes(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._attach_session_window(window, "group-1")

        result = api_bridge.open_session_window("group-2")

        self.assertEqual(result, {"ok": True, "reused": True})
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.restore_calls, 0)
        self.assertEqual(window.loaded_urls, [])
        self.assertEqual(api_bridge._session_window_group_id, "group-1")

    def test_open_launcher_window_shows_when_not_minimized(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._attach_window(window)

        result = api_bridge.open_launcher_window()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.restore_calls, 0)
        self.assertEqual(window.loaded_urls, [])

    def test_open_launcher_window_restores_minimized_launcher_then_shows(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        api_bridge._attach_window(window)
        api_bridge._set_window_minimized("launcher", True)

        result = api_bridge.open_launcher_window()

        self.assertEqual(result, {"ok": True})
        self.assertEqual(window.restore_calls, 1)
        self.assertEqual(window.show_calls, 1)
        self.assertEqual(window.loaded_urls, [])

    def test_build_restart_command_reuses_current_launcher_arguments(self):
        with patch.object(webview_launcher.sys, "platform", "linux"):
            with patch.object(webview_launcher.sys, "executable", "/usr/bin/python3"):
                with patch.object(
                    webview_launcher.sys,
                    "argv",
                    ["/tmp/launcher.py", "--port", "6060", "--debug"],
                ):
                    command = webview_launcher._build_restart_command()

        self.assertEqual(command[0], "/usr/bin/python3")
        self.assertTrue(Path(command[1]).as_posix().endswith("/tmp/launcher.py"))
        self.assertEqual(command[-3:], ["--port", "6060", "--debug"])

    def test_build_restart_command_uses_current_executable_when_frozen(self):
        with patch.object(webview_launcher.sys, "platform", "linux"):
            with patch.object(webview_launcher.sys, "executable", "/tmp/GridVibe.exe"):
                with patch.object(
                    webview_launcher.sys,
                    "argv",
                    ["GridVibe.exe", "--debug"],
                ):
                    with patch.object(webview_launcher.sys, "frozen", True, create=True):
                        command = webview_launcher._build_restart_command()

        self.assertEqual(command, ["/tmp/GridVibe.exe", "--debug"])

    def test_build_restart_command_uses_direct_python_on_windows(self):
        project_root = Path(webview_launcher.__file__).resolve().parent.parent
        expected_python = str(project_root / ".venv" / "Scripts" / "python.exe")
        expected_launcher = str(project_root / "webview_launcher.py")

        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            Path,
            "is_file",
            return_value=True,
        ):
            command = webview_launcher._build_restart_command()

        self.assertEqual(command, [expected_python, expected_launcher])

    def test_resolve_icon_path_uses_gridvibe_icon(self):
        icon_path = webview_launcher._resolve_icon_path()

        self.assertIsNotNone(icon_path)
        self.assertTrue(icon_path.endswith("GridVibe_icon.ico"))
        self.assertTrue(Path(icon_path).is_file())

    def test_missing_linux_pywebview_backend_detection(self):
        exc = Exception(
            "You must have either QT or GTK with Python extensions installed "
            "in order to use pywebview."
        )

        with patch.object(webview_launcher.sys, "platform", "linux"):
            self.assertTrue(webview_launcher._is_missing_linux_pywebview_backend(exc))

    def test_missing_linux_pywebview_backend_detection_ignores_other_platforms(self):
        exc = Exception(
            "You must have either QT or GTK with Python extensions installed "
            "in order to use pywebview."
        )

        with patch.object(webview_launcher.sys, "platform", "win32"):
            self.assertFalse(webview_launcher._is_missing_linux_pywebview_backend(exc))

    def test_restart_application_queues_restart_and_shutdown(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        restart_command = ["/usr/bin/python3", "/tmp/webview_launcher.py", "--debug"]

        with patch.object(
            webview_launcher,
            "_build_restart_command",
            return_value=restart_command,
        ), patch.object(
            webview_launcher,
            "_schedule_process_restart",
        ) as schedule_restart, patch.object(
            webview_launcher,
            "_start_restart_shutdown_thread",
        ) as start_shutdown:
            result = api_bridge.restart_application()

        self.assertEqual(result, {"ok": True, "restarting": True})
        schedule_restart.assert_called_once()
        self.assertEqual(schedule_restart.call_args.args[0], restart_command)
        self.assertEqual(schedule_restart.call_args.args[2], webview_launcher.os.getpid())
        start_shutdown.assert_called_once_with(api_bridge=api_bridge)

    def test_schedule_process_restart_uses_detached_helper_on_windows(self):
        command = [
            "cmd.exe",
            "/c",
            'start "GridVibe" /min cmd /c ""C:\\repo\\.venv\\Scripts\\python.exe" "C:\\repo\\webview_launcher.py""',
        ]
        expected_flags = 0x00000008 | 0x00000200 | 0x01000000

        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher.subprocess,
            "DETACHED_PROCESS",
            0x00000008,
            create=True,
        ), patch.object(
            webview_launcher.subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0x00000200,
            create=True,
        ), patch.object(
            webview_launcher.subprocess,
            "CREATE_BREAKAWAY_FROM_JOB",
            0x01000000,
            create=True,
        ), patch.object(webview_launcher.subprocess, "Popen") as popen:
            webview_launcher._schedule_process_restart(command, "C:\\repo", 1234)

        popen.assert_called_once()
        self.assertEqual(popen.call_args.kwargs["cwd"], "C:\\repo")
        self.assertEqual(popen.call_args.kwargs["creationflags"], expected_flags)
        self.assertNotIn("start_new_session", popen.call_args.kwargs)
        helper_command = popen.call_args.args[0]
        self.assertEqual(helper_command[0], webview_launcher.sys.executable)
        self.assertIn("WaitForSingleObject", helper_command[2])
        self.assertIn("DETACHED_PROCESS", helper_command[2])
