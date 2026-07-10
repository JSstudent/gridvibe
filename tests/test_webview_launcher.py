import os
import signal
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from web import webview_launcher


class _ExplodingWindow:
    def __init__(self):
        self.show_calls = 0
        self.restore_calls = 0
        self.minimize_calls = 0
        self.maximize_calls = 0
        self.destroy_calls = 0
        self.loaded_urls = []
        self.on_top = False

    def show(self):
        self.show_calls += 1

    def restore(self):
        self.restore_calls += 1

    def minimize(self):
        self.minimize_calls += 1

    def maximize(self):
        self.maximize_calls += 1

    def destroy(self):
        self.destroy_calls += 1

    def load_url(self, url):
        self.loaded_urls.append(url)
        raise AssertionError("load_url should not be called")


class _FakeThread:
    def __init__(self):
        self.started = False
        self.joined = False

    def start(self):
        self.started = True

    def join(self):
        self.joined = True


class _FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self):
        self.events = Mock(
            minimized=_FakeEvent(),
            restored=_FakeEvent(),
            before_show=_FakeEvent(),
            shown=_FakeEvent(),
            closed=_FakeEvent(),
        )


class _FakeHandle:
    def __init__(self, hwnd=1234):
        self.hwnd = hwnd

    def ToInt32(self):
        return self.hwnd


class _FakeNative:
    def __init__(self, hwnd=1234):
        self.Handle = _FakeHandle(hwnd)


class WebviewLauncherTestCase(unittest.TestCase):
    def test_launcher_close_exits_even_when_session_window_is_still_open(self):
        self.assertTrue(
            webview_launcher._should_exit_after_window_close("launcher", {"session"})
        )

    def test_session_close_keeps_app_running_while_launcher_is_open(self):
        self.assertFalse(
            webview_launcher._should_exit_after_window_close("session", {"launcher"})
        )

    def test_preferred_pywebview_gui_uses_qt_on_linux(self):
        with patch.object(webview_launcher.sys, "platform", "linux"):
            self.assertEqual(webview_launcher._preferred_pywebview_gui(), "qt")

    def test_browser_mode_opens_browser_without_pywebview(self):
        fake_thread = _FakeThread()
        fake_webview = Mock()

        with patch.object(
            webview_launcher.sys,
            "argv",
            ["webview_launcher.py", "--mode", "browser"],
        ), patch.object(
            webview_launcher.os.path,
            "exists",
            return_value=False,
        ), patch.object(
            webview_launcher,
            "setup_logging",
        ), patch.object(
            webview_launcher,
            "_wait_for_server",
            return_value=True,
        ), patch.object(
            webview_launcher.threading,
            "Thread",
            return_value=fake_thread,
        ), patch.object(
            webview_launcher,
            "webview",
            fake_webview,
        ), patch.object(
            webview_launcher,
            "configure_browser_shutdown",
        ) as configure_shutdown, patch.object(
            webview_launcher.webbrowser, "open"
        ) as browser_open:
            webview_launcher.main()

        self.assertTrue(fake_thread.started)
        self.assertTrue(fake_thread.joined)
        browser_open.assert_called_once_with("http://127.0.0.1:5050")
        fake_webview.create_window.assert_not_called()
        fake_webview.start.assert_not_called()
        configure_shutdown.assert_called_once_with(True)

    def test_auto_mode_preserves_browser_fallback_when_pywebview_is_missing(self):
        fake_thread = _FakeThread()

        with patch.object(
            webview_launcher.sys,
            "argv",
            ["webview_launcher.py"],
        ), patch.object(
            webview_launcher.os.path,
            "exists",
            return_value=False,
        ), patch.object(
            webview_launcher,
            "setup_logging",
        ), patch.object(
            webview_launcher,
            "_wait_for_server",
            return_value=True,
        ), patch.object(
            webview_launcher.threading,
            "Thread",
            return_value=fake_thread,
        ), patch.object(
            webview_launcher,
            "webview",
            None,
        ), patch.object(
            webview_launcher,
            "configure_browser_shutdown",
        ) as configure_shutdown, patch.object(
            webview_launcher.webbrowser, "open"
        ) as browser_open:
            webview_launcher.main()

        self.assertTrue(fake_thread.started)
        self.assertTrue(fake_thread.joined)
        browser_open.assert_called_once_with("http://127.0.0.1:5050")
        configure_shutdown.assert_called_once_with(False)

    def test_auto_mode_falls_back_when_native_window_creation_fails(self):
        fake_thread = _FakeThread()
        fake_webview = Mock()
        fake_webview.create_window.return_value = None

        with patch.object(
            webview_launcher.sys,
            "argv",
            ["webview_launcher.py", "--mode", "auto"],
        ), patch.object(
            webview_launcher.os.path,
            "exists",
            return_value=False,
        ), patch.object(
            webview_launcher,
            "setup_logging",
        ), patch.object(
            webview_launcher,
            "_wait_for_server",
            return_value=True,
        ), patch.object(
            webview_launcher.threading,
            "Thread",
            return_value=fake_thread,
        ), patch.object(
            webview_launcher,
            "webview",
            fake_webview,
        ), patch.object(
            webview_launcher,
            "_preferred_pywebview_gui",
            return_value=None,
        ), patch.object(
            webview_launcher,
            "_set_linux_qtwebengine_env",
        ), patch.object(webview_launcher.webbrowser, "open") as browser_open:
            webview_launcher.main()

        self.assertTrue(fake_thread.started)
        self.assertTrue(fake_thread.joined)
        browser_open.assert_called_once_with("http://127.0.0.1:5050")
        fake_webview.start.assert_not_called()

    def test_native_mode_missing_pywebview_exits_without_browser_fallback(self):
        fake_thread = _FakeThread()

        with patch.object(
            webview_launcher.sys,
            "argv",
            ["webview_launcher.py", "--mode", "native"],
        ), patch.object(
            webview_launcher.os.path,
            "exists",
            return_value=False,
        ), patch.object(
            webview_launcher,
            "setup_logging",
        ), patch.object(
            webview_launcher,
            "_wait_for_server",
            return_value=True,
        ), patch.object(
            webview_launcher.threading,
            "Thread",
            return_value=fake_thread,
        ), patch.object(
            webview_launcher,
            "webview",
            None,
        ), patch.object(
            webview_launcher.session_manager,
            "close_all_sessions",
        ), patch.object(webview_launcher.webbrowser, "open") as browser_open, patch.object(
            webview_launcher.os,
            "_exit",
            side_effect=SystemExit(1),
        ) as os_exit:
            with self.assertRaises(SystemExit):
                webview_launcher.main()

        self.assertTrue(fake_thread.started)
        self.assertFalse(fake_thread.joined)
        browser_open.assert_not_called()
        os_exit.assert_called_once_with(1)

    def test_native_launcher_window_uses_resizable_native_frame(self):
        fake_thread = _FakeThread()
        fake_webview = Mock()
        fake_webview.create_window.return_value = _FakeWindow()

        with patch.object(
            webview_launcher.sys,
            "argv",
            ["webview_launcher.py", "--mode", "native"],
        ), patch.object(
            webview_launcher.os.path,
            "exists",
            return_value=False,
        ), patch.object(
            webview_launcher,
            "setup_logging",
        ), patch.object(
            webview_launcher,
            "_wait_for_server",
            return_value=True,
        ), patch.object(
            webview_launcher.threading,
            "Thread",
            return_value=fake_thread,
        ), patch.object(
            webview_launcher,
            "webview",
            fake_webview,
        ), patch.object(
            webview_launcher,
            "_preferred_pywebview_gui",
            return_value=None,
        ), patch.object(
            webview_launcher,
            "_set_linux_qtwebengine_env",
        ):
            webview_launcher.main()

        fake_webview.create_window.assert_called_once()
        self.assertTrue(fake_webview.create_window.call_args.kwargs["resizable"])
        self.assertFalse(fake_webview.create_window.call_args.kwargs["frameless"])
        self.assertFalse(fake_webview.create_window.call_args.kwargs["easy_drag"])
        self.assertEqual(
            fake_webview.create_window.call_args.kwargs["background_color"],
            "#070b18",
        )
        fake_webview.start.assert_called_once()
        self.assertEqual(len(fake_webview.create_window.return_value.events.before_show.handlers), 1)
        self.assertEqual(len(fake_webview.create_window.return_value.events.shown.handlers), 1)

    def test_session_window_uses_resizable_native_frame(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        fake_webview = Mock()
        fake_webview.create_window.return_value = _ExplodingWindow()

        with patch.object(webview_launcher, "webview", fake_webview):
            result = api_bridge.open_session_window("group-1")

        self.assertEqual(result, {"ok": True, "reused": False})
        fake_webview.create_window.assert_called_once()
        self.assertTrue(fake_webview.create_window.call_args.kwargs["resizable"])
        self.assertFalse(fake_webview.create_window.call_args.kwargs["frameless"])
        self.assertFalse(fake_webview.create_window.call_args.kwargs["easy_drag"])
        self.assertEqual(
            fake_webview.create_window.call_args.kwargs["background_color"],
            "#0d0d0d",
        )

    def test_hex_to_colorref_converts_rgb_to_windows_colorref(self):
        self.assertEqual(webview_launcher._hex_to_colorref("#112233"), 0x332211)

    def test_apply_windows_native_frame_theme_enforces_dark_frame(self):
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher,
            "_resolve_native_window_handle",
            return_value=1234,
        ), patch.object(
            webview_launcher,
            "_set_dwm_window_attribute",
            return_value=True,
        ) as set_dwm, patch.object(
            webview_launcher,
            "_refresh_windows_native_frame",
            return_value=True,
        ) as refresh:
            result = webview_launcher._apply_windows_native_frame_theme(window, "light")

        self.assertTrue(result)
        self.assertIn(call(1234, 19, 1), set_dwm.call_args_list)
        self.assertIn(call(1234, 20, 1), set_dwm.call_args_list)
        self.assertIn(call(1234, 38, 2), set_dwm.call_args_list)
        self.assertIn(
            call(1234, 35, webview_launcher._hex_to_colorref("#111827")),
            set_dwm.call_args_list,
        )
        refresh.assert_called_once_with(1234)

    def test_set_native_theme_keeps_registered_windows_dark(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        launcher_window = _ExplodingWindow()
        session_window = _ExplodingWindow()
        launcher_window.native = _FakeNative(111)
        session_window.native = _FakeNative(222)
        api_bridge._attach_window(launcher_window)
        api_bridge._attach_session_window(session_window)

        with patch.object(
            webview_launcher,
            "_apply_windows_native_frame_theme",
            return_value=True,
        ) as apply_theme:
            result = api_bridge.set_native_theme("light")

        self.assertEqual(result, {"ok": True, "theme": "dark", "applied": True})
        self.assertEqual(
            apply_theme.call_args_list,
            [
                call(launcher_window, "dark"),
                call(session_window, "dark"),
            ],
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

    def test_bring_to_front_skips_top_most_pulse_for_session_window_on_windows(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "win32"):
            with patch.object(api_bridge, "_pulse_on_top") as pulse:
                result = api_bridge._bring_to_front(window, "session")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        pulse.assert_not_called()

    def test_bring_to_front_pulses_session_window_on_linux(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "linux"):
            with patch.object(api_bridge, "_pulse_on_top") as pulse:
                result = api_bridge._bring_to_front(window, "session")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        pulse.assert_called_once_with(window, "session")

    def test_bring_to_front_skips_top_most_pulse_for_launcher_window_on_windows(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "win32"):
            with patch.object(api_bridge, "_pulse_on_top") as pulse:
                result = api_bridge._bring_to_front(window, "launcher")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        pulse.assert_not_called()

    def test_bring_to_front_pulses_launcher_window_on_linux(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "linux"):
            with patch.object(api_bridge, "_pulse_on_top") as pulse:
                result = api_bridge._bring_to_front(window, "launcher")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        pulse.assert_called_once_with(window, "launcher")

    def test_bring_to_front_pulse_toggles_on_top(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "linux"):
            result = api_bridge._bring_to_front(window, "session")

        self.assertTrue(result)
        self.assertEqual(window.show_calls, 1)
        self.assertFalse(window.on_top)

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

    @unittest.skipUnless(
        all(hasattr(signal, name) for name in ("SIGTSTP", "SIGTTIN", "SIGTTOU")),
        "job-control signals are not available on this platform",
    )
    def test_linux_job_control_stop_signals_are_ignored(self):
        with patch.object(webview_launcher.sys, "platform", "linux"), patch.object(
            webview_launcher.signal,
            "signal",
        ) as signal_mock:
            webview_launcher._ignore_linux_job_control_stop_signals()

            signal_mock.assert_any_call(signal.SIGTSTP, signal.SIG_IGN)
            signal_mock.assert_any_call(signal.SIGTTIN, signal.SIG_IGN)
            signal_mock.assert_any_call(signal.SIGTTOU, signal.SIG_IGN)

    def test_linux_job_control_stop_signals_ignore_non_linux_platforms(self):
        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher.signal,
            "signal",
        ) as signal_mock:
            webview_launcher._ignore_linux_job_control_stop_signals()

            signal_mock.assert_not_called()

    def test_linux_qtwebengine_env_is_opt_in(self):
        with patch.object(webview_launcher.sys, "platform", "linux"), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            webview_launcher._set_linux_qtwebengine_env()

            self.assertNotIn("QTWEBENGINE_CHROMIUM_FLAGS", os.environ)
            self.assertNotIn("QT_OPENGL", os.environ)
            self.assertNotIn("LIBGL_ALWAYS_SOFTWARE", os.environ)

    def test_linux_qtwebengine_env_can_enable_gpu_fallback(self):
        with patch.object(webview_launcher.sys, "platform", "linux"), patch.dict(
            os.environ,
            {"GRIDVIBE_QTWEBENGINE_GPU_FALLBACK": "1"},
            clear=True,
        ):
            webview_launcher._set_linux_qtwebengine_env()

            flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].split()
            self.assertIn("--disable-gpu", flags)
            self.assertIn("--disable-features=Vulkan", flags)
            self.assertNotIn("--use-gl=swiftshader", flags)
            self.assertEqual(os.environ["QT_OPENGL"], "software")
            self.assertNotIn("QT_QUICK_BACKEND", os.environ)
            self.assertEqual(os.environ["LIBGL_ALWAYS_SOFTWARE"], "1")

    def test_linux_qtwebengine_env_preserves_existing_values(self):
        with patch.object(webview_launcher.sys, "platform", "linux"), patch.dict(
            os.environ,
            {
                "QTWEBENGINE_CHROMIUM_FLAGS": "--foo --disable-gpu",
                "QT_OPENGL": "desktop",
                "QT_QUICK_BACKEND": "opengl",
                "LIBGL_ALWAYS_SOFTWARE": "0",
                "GRIDVIBE_QTWEBENGINE_GPU_FALLBACK": "1",
            },
            clear=False,
        ):
            webview_launcher._set_linux_qtwebengine_env()

            flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].split()
            self.assertEqual(flags.count("--disable-gpu"), 1)
            self.assertIn("--foo", flags)
            self.assertIn("--disable-features=Vulkan", flags)
            self.assertNotIn("--use-gl=swiftshader", flags)
            self.assertEqual(os.environ["QT_OPENGL"], "desktop")
            self.assertEqual(os.environ["QT_QUICK_BACKEND"], "opengl")
            self.assertEqual(os.environ["LIBGL_ALWAYS_SOFTWARE"], "0")

    def test_linux_qtwebengine_env_ignores_non_linux_platforms(self):
        with patch.object(webview_launcher.sys, "platform", "win32"), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            webview_launcher._set_linux_qtwebengine_env()

            self.assertNotIn("QTWEBENGINE_CHROMIUM_FLAGS", os.environ)
            self.assertNotIn("QT_OPENGL", os.environ)

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
