import io
import os
import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from urllib.error import HTTPError

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
            maximized=_FakeEvent(),
            before_show=_FakeEvent(),
            shown=_FakeEvent(),
            loaded=_FakeEvent(),
            closing=_FakeEvent(),
            closed=_FakeEvent(),
        )
        self.evaluate_js = Mock()
        self.destroy = Mock()


class _FakeHandle:
    def __init__(self, hwnd=1234):
        self.hwnd = hwnd

    def ToInt32(self):
        return self.hwnd


class _FakeNative:
    def __init__(self, hwnd=1234):
        self.Handle = _FakeHandle(hwnd)


class _OrderedWindow(_ExplodingWindow):
    """An _ExplodingWindow that records when it was shown or restored."""

    def __init__(self, order):
        super().__init__()
        self.order = order

    def show(self):
        self.order.append("show")
        super().show()

    def restore(self):
        self.order.append("restore")
        super().restore()


class _FakeGeometryWindow:
    """A window that answers pywebview's geometry properties and move()."""

    def __init__(self, x, y, width, height):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.moves = []

    def move(self, x, y):
        self.moves.append((x, y))
        self.x = x
        self.y = y


class _FakeZoomWebview:
    def __init__(self):
        self.ZoomFactor = 1.0


class _FakeZoomNative:
    def __init__(self):
        self.webview = _FakeZoomWebview()


class WebviewLauncherTestCase(unittest.TestCase):
    def setUp(self):
        webview_launcher.lifecycle_coordinator.reset()

    def tearDown(self):
        webview_launcher.lifecycle_coordinator.reset()

    def test_launcher_close_exits_even_when_session_window_is_still_open(self):
        self.assertTrue(
            webview_launcher._should_exit_after_window_close("launcher", {"session"})
        )

    def test_session_close_keeps_app_running_while_launcher_is_open(self):
        self.assertFalse(
            webview_launcher._should_exit_after_window_close("session", {"launcher"})
        )

    def test_every_remaining_window_is_a_reason_to_keep_running(self):
        """The agent dashboard used to be the one exception -- a window that
        described the others and was no reason on its own to stay up. It is a
        dialog on those windows now, so there is no window kind left that the
        app may close itself out from under."""
        self.assertFalse(
            webview_launcher._should_exit_after_window_close(
                "session", {"workspace:abc123def456"}
            )
        )
        self.assertTrue(
            webview_launcher._should_exit_after_window_close("session", set())
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
            webview_launcher, "_open_browser_window"
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
            webview_launcher, "_open_browser_window"
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
        ), patch.object(webview_launcher, "_open_browser_window") as browser_open:
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
        ), patch.object(webview_launcher, "_open_browser_window") as browser_open, patch.object(
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
        ), patch.object(
            webview_launcher,
            "_request_native_close_prompt",
        ) as request_native_close_prompt:
            webview_launcher.main()
            closing = fake_webview.create_window.return_value.events.closing.handlers
            self.assertEqual(len(closing), 1)
            self.assertFalse(closing[0]())
            self.assertFalse(closing[0]())

        fake_webview.create_window.assert_called_once()
        self.assertTrue(fake_webview.create_window.call_args.kwargs["resizable"])
        self.assertFalse(fake_webview.create_window.call_args.kwargs["frameless"])
        self.assertFalse(fake_webview.create_window.call_args.kwargs["easy_drag"])
        self.assertTrue(fake_webview.create_window.call_args.kwargs["zoomable"])
        self.assertEqual(
            fake_webview.create_window.call_args.kwargs["background_color"],
            "#070b18",
        )
        fake_webview.start.assert_called_once()
        self.assertEqual(len(fake_webview.create_window.return_value.events.before_show.handlers), 1)
        self.assertEqual(len(fake_webview.create_window.return_value.events.shown.handlers), 1)
        request_native_close_prompt.assert_called_once()
        self.assertIs(
            request_native_close_prompt.call_args.args[0],
            fake_webview.create_window.return_value,
        )
        fake_webview.create_window.return_value.evaluate_js.assert_not_called()

    def test_native_close_prompt_defers_synchronous_javascript_to_worker(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        api_bridge._close_prompt_pending = True
        window = _FakeWindow()
        threads = []

        class DeferredThread:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.started = False
                threads.append(self)

            def start(self):
                self.started = True

        with patch.object(
            webview_launcher.threading,
            "Thread",
            DeferredThread,
        ):
            webview_launcher._request_native_close_prompt(window, api_bridge)

        window.evaluate_js.assert_not_called()
        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0].started)
        self.assertTrue(threads[0].kwargs["daemon"])
        self.assertEqual(threads[0].kwargs["name"], "gridvibe-native-close-prompt")

        threads[0].kwargs["target"]()

        window.evaluate_js.assert_called_once_with(
            "window.gridvibeRequestLifecycleClose && "
            "window.gridvibeRequestLifecycleClose()"
        )

    def test_maximized_event_clears_stale_minimized_flag(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
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
        ), patch.object(
            webview_launcher,
            "GridVibeApi",
            return_value=api_bridge,
        ):
            webview_launcher.main()

        events = fake_webview.create_window.return_value.events
        self.assertEqual(len(events.maximized.handlers), 1)
        api_bridge._set_window_minimized("launcher", True)
        events.maximized.handlers[0]()
        self.assertFalse(api_bridge._is_window_minimized("launcher"))

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
        self.assertTrue(fake_webview.create_window.call_args.kwargs["zoomable"])
        self.assertEqual(
            fake_webview.create_window.call_args.kwargs["background_color"],
            "#0d0d0d",
        )

    def test_session_native_zoom_round_trip_uses_native_control(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        window.native = _FakeZoomNative()
        window.native.webview.ZoomFactor = 1.5
        api_bridge._attach_session_window(window)

        self.assertEqual(
            api_bridge.get_session_native_zoom(),
            {"ok": True, "zoom_factor": 1.5},
        )
        self.assertEqual(
            webview_launcher._set_native_window_zoom(window, "1.25"),
            1.25,
        )
        self.assertEqual(window.native.webview.ZoomFactor, 1.25)
        self.assertIsNone(webview_launcher._set_native_window_zoom(window, 99))

    def test_open_existing_session_window_applies_restored_zoom(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _ExplodingWindow()
        window.native = _FakeZoomNative()
        api_bridge._attach_session_window(window, "group-1")

        result = api_bridge.open_session_window("group-1", 1.75)

        self.assertEqual(result, {"ok": True, "reused": True})
        self.assertEqual(window.native.webview.ZoomFactor, 1.75)
        self.assertEqual(window.show_calls, 1)

    def test_new_session_window_applies_restored_zoom_after_load(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _FakeWindow()
        window.native = _FakeZoomNative()
        fake_webview = Mock()
        fake_webview.create_window.return_value = window

        with patch.object(webview_launcher, "webview", fake_webview):
            result = api_bridge.open_session_window("group-1", 1.4)

        self.assertEqual(result, {"ok": True, "reused": False})
        self.assertEqual(api_bridge._pending_session_native_zoom_factor, 1.4)
        self.assertEqual(len(window.events.loaded.handlers), 1)
        window.events.loaded.handlers[0]()
        self.assertEqual(window.native.webview.ZoomFactor, 1.4)
        self.assertIsNone(api_bridge._pending_session_native_zoom_factor)

    def test_two_workspace_ids_create_distinct_native_windows_and_urls(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        first_window = _ExplodingWindow()
        second_window = _ExplodingWindow()
        fake_webview = Mock()
        fake_webview.create_window.side_effect = [first_window, second_window]

        with patch.object(webview_launcher, "webview", fake_webview):
            first = api_bridge.open_workspace_window("aaaaaaaaaaaa", "group-a")
            second = api_bridge.open_workspace_window("bbbbbbbbbbbb", "group-b")

        self.assertEqual(first, {"ok": True, "reused": False})
        self.assertEqual(second, {"ok": True, "reused": False})
        self.assertIs(
            api_bridge._workspace_windows["aaaaaaaaaaaa"],
            first_window,
        )
        self.assertIs(
            api_bridge._workspace_windows["bbbbbbbbbbbb"],
            second_window,
        )
        urls = [entry.args[1] for entry in fake_webview.create_window.call_args_list]
        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:5050/terminals?workspace=aaaaaaaaaaaa&group=group-a",
                "http://127.0.0.1:5050/terminals?workspace=bbbbbbbbbbbb&group=group-b",
            ],
        )

    def test_workspace_window_reuse_and_close_are_scoped_by_id(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        first_window = _ExplodingWindow()
        second_window = _ExplodingWindow()
        api_bridge._attach_workspace_window("aaaaaaaaaaaa", first_window, "group-a")
        api_bridge._attach_workspace_window("bbbbbbbbbbbb", second_window, "group-b")

        reused = api_bridge.open_workspace_window("aaaaaaaaaaaa", "group-other")
        closed = api_bridge.close_workspace_window("aaaaaaaaaaaa")

        self.assertEqual(reused, {"ok": True, "reused": True})
        self.assertEqual(closed, {"ok": True})
        self.assertEqual(first_window.show_calls, 1)
        self.assertEqual(first_window.destroy_calls, 1)
        self.assertEqual(second_window.show_calls, 0)
        self.assertEqual(second_window.destroy_calls, 0)
        self.assertIs(
            api_bridge._workspace_windows["bbbbbbbbbbbb"],
            second_window,
        )

    def test_workspace_fullscreen_and_zoom_target_only_requested_window(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        first_window = Mock()
        first_window.native = _FakeZoomNative()
        first_window.native.webview.ZoomFactor = 1.25
        second_window = Mock()
        second_window.native = _FakeZoomNative()
        second_window.native.webview.ZoomFactor = 1.75
        api_bridge._attach_workspace_window("aaaaaaaaaaaa", first_window)
        api_bridge._attach_workspace_window("bbbbbbbbbbbb", second_window)

        toggled = api_bridge.toggle_workspace_fullscreen("bbbbbbbbbbbb")
        zoom = api_bridge.get_workspace_native_zoom("bbbbbbbbbbbb")

        self.assertEqual(toggled, {"ok": True})
        second_window.toggle_fullscreen.assert_called_once_with()
        first_window.toggle_fullscreen.assert_not_called()
        self.assertEqual(zoom, {"ok": True, "zoom_factor": 1.75})
        self.assertFalse(
            api_bridge.get_workspace_fullscreen_state("aaaaaaaaaaaa")[
                "is_fullscreen"
            ]
        )
        self.assertTrue(
            api_bridge.get_workspace_fullscreen_state("bbbbbbbbbbbb")[
                "is_fullscreen"
            ]
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

    def test_set_native_theme_always_dark_regardless_of_argument(self):
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
            # Argument is intentionally ignored — frame is always dark.
            result = api_bridge.set_native_theme("light")
            result_no_arg = api_bridge.set_native_theme()

        self.assertEqual(result, {"ok": True, "theme": "dark", "applied": True})
        self.assertEqual(result_no_arg, {"ok": True, "theme": "dark", "applied": True})
        for c in apply_theme.call_args_list:
            self.assertIn(c, [
                call(launcher_window, "dark"),
                call(session_window, "dark"),
            ])

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

    def test_restore_minimized_window_prefers_sw_restore_on_windows(self):
        window = _ExplodingWindow()
        fake_user32 = Mock()
        fake_windll = Mock(user32=fake_user32)

        with patch.object(
            webview_launcher.sys, "platform", "win32"
        ), patch.object(
            webview_launcher, "_resolve_native_window_handle", return_value=1234
        ), patch.object(
            webview_launcher.ctypes, "windll", fake_windll, create=True
        ):
            result = webview_launcher._restore_minimized_window(window)

        self.assertTrue(result)
        fake_user32.ShowWindow.assert_called_once()
        self.assertEqual(fake_user32.ShowWindow.call_args.args[1], 9)  # SW_RESTORE
        self.assertEqual(window.restore_calls, 0)

    def test_restore_minimized_window_falls_back_without_native_handle(self):
        window = _ExplodingWindow()

        with patch.object(webview_launcher.sys, "platform", "win32"):
            result = webview_launcher._restore_minimized_window(window)

        self.assertTrue(result)
        self.assertEqual(window.restore_calls, 1)

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

    def test_plan_window_placement_only_moves_a_window_off_the_wrong_screen(self):
        """Where the launcher belongs when a workspace asks for it.

        The monitor the request came from is the one the user is sitting in
        front of, so a launcher parked on another one is brought over and
        centred on the window that asked. A launcher already on that monitor is
        left exactly where the user put it: bringing a window up is not a
        request to move it around the desk.
        """
        primary = (0, 0, 1920, 1080)
        # The work area is the monitor minus the taskbar: a window centred over
        # a maximized workspace window must not end up underneath it.
        primary_work = (0, 0, 1920, 1040)
        anchor = (100, 100, 1100, 900)

        # Already on the anchor's screen — including a window straddling the
        # edge, which is judged by where its middle is.
        self.assertIsNone(
            webview_launcher.plan_window_placement(
                anchor, (200, 200, 1000, 700), primary, primary_work
            )
        )
        self.assertIsNone(
            webview_launcher.plan_window_placement(
                anchor, (-200, 200, 600, 700), primary, primary_work
            )
        )

        # On the monitor to the left: centred on the anchor.
        self.assertEqual(
            webview_launcher.plan_window_placement(
                anchor, (-1800, 100, -1000, 700), primary, primary_work
            ),
            (200, 200),
        )

        # Centred on a workspace window low on that screen, then held inside
        # the work area rather than pushed under the taskbar.
        self.assertEqual(
            webview_launcher.plan_window_placement(
                (960, 600, 1920, 1080), (-1800, 100, -1000, 700), primary, primary_work
            ),
            (1040, 440),
        )

        # Nothing fits: a window wider and taller than the work area takes its
        # corner instead of hanging off two edges.
        self.assertEqual(
            webview_launcher.plan_window_placement(
                anchor, (-4000, 100, -1800, 1300), primary, primary_work
            ),
            (0, 0),
        )

    def test_placement_falls_back_to_pywebview_screens_off_windows(self):
        """The portable half: pywebview's own geometry and screen list.

        Windows has the Win32 reader, which speaks one physical coordinate
        space for every monitor. Everywhere else the move goes through
        pywebview, and the screen list is what answers "is it already there" —
        without it the launcher would be dragged to the middle of the workspace
        window on every single press.
        """
        screens = [
            SimpleNamespace(x=0, y=0, width=1920, height=1080),
            SimpleNamespace(x=-1920, y=0, width=1920, height=1080),
        ]
        anchor = _FakeGeometryWindow(100, 100, 1000, 800)
        launcher = _FakeGeometryWindow(-1800, 100, 800, 600)

        with patch.object(webview_launcher, "webview", SimpleNamespace(screens=screens)):
            moved = webview_launcher.place_window_on_anchor_screen(
                launcher, anchor, "launcher"
            )
            self.assertTrue(moved)
            self.assertEqual(launcher.moves, [(200, 200)])

            # Same screen as the workspace window: left alone.
            already = _FakeGeometryWindow(300, 300, 800, 600)
            self.assertFalse(
                webview_launcher.place_window_on_anchor_screen(already, anchor, "launcher")
            )
            self.assertEqual(already.moves, [])

    def test_placement_never_raises_out_of_the_bridge_call(self):
        """A window that cannot be placed still gets brought up.

        Placement is a courtesy on top of focusing the launcher; a window with
        no geometry to read, or no anchor at all, must cost the focus call
        nothing.
        """
        self.assertFalse(
            webview_launcher.place_window_on_anchor_screen(_ExplodingWindow(), None)
        )
        with patch.object(webview_launcher, "webview", SimpleNamespace(screens=[])):
            self.assertFalse(
                webview_launcher.place_window_on_anchor_screen(
                    _ExplodingWindow(), _ExplodingWindow()
                )
            )

    def test_open_launcher_window_comes_up_on_the_screen_that_asked(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        launcher = _ExplodingWindow()
        workspace = _ExplodingWindow()
        api_bridge._attach_window(launcher)
        api_bridge._workspace_windows["aaaaaaaaaaaa"] = workspace

        with patch.object(webview_launcher, "place_window_on_anchor_screen") as place:
            result = api_bridge.open_launcher_window("aaaaaaaaaaaa")

        self.assertEqual(result, {"ok": True})
        # Placed onto the window that asked, and still focused: the launcher
        # keeps its size and its loaded page either way.
        place.assert_called_once_with(launcher, workspace, "launcher")
        self.assertEqual(launcher.show_calls, 1)
        self.assertEqual(launcher.loaded_urls, [])

    def test_open_launcher_window_stays_put_without_a_workspace_to_go_to(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        launcher = _ExplodingWindow()
        api_bridge._attach_window(launcher)

        with patch.object(webview_launcher, "place_window_on_anchor_screen") as place:
            # No id (the request came from somewhere that is not a workspace),
            # an id whose window is closed, and an unusable id.
            self.assertEqual(api_bridge.open_launcher_window(), {"ok": True})
            self.assertEqual(api_bridge.open_launcher_window(""), {"ok": True})
            self.assertEqual(api_bridge.open_launcher_window("bbbbbbbbbbbb"), {"ok": True})
            self.assertEqual(api_bridge.open_launcher_window("not a workspace"), {"ok": True})

        place.assert_not_called()
        self.assertEqual(launcher.show_calls, 4)

    def test_minimized_launcher_is_placed_after_it_is_restored(self):
        """Order matters both ways round.

        A window that is up is moved before it is shown, so it never appears on
        the monitor it is leaving. A minimized one has no position to move until
        it has been restored, so that one is placed afterwards.
        """
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        order = []
        launcher = _OrderedWindow(order)
        workspace = _ExplodingWindow()
        api_bridge._attach_window(launcher)
        api_bridge._workspace_windows["aaaaaaaaaaaa"] = workspace

        def _record_placement(*_args, **_kwargs):
            order.append("place")
            return True

        with patch.object(
            webview_launcher, "place_window_on_anchor_screen", _record_placement
        ):
            api_bridge.open_launcher_window("aaaaaaaaaaaa")
            self.assertEqual(order, ["place", "show"])

            order.clear()
            api_bridge._set_window_minimized("launcher", True)
            api_bridge.open_launcher_window("aaaaaaaaaaaa")
            self.assertEqual(order, ["restore", "show", "place"])

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
        decision = webview_launcher.lifecycle_coordinator.issue_decision("restart")

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
            result = api_bridge.restart_application(decision)

        self.assertEqual(result, {"ok": True, "restarting": True})
        schedule_restart.assert_called_once()
        self.assertEqual(schedule_restart.call_args.args[0], restart_command)
        self.assertEqual(schedule_restart.call_args.args[2], webview_launcher.os.getpid())
        start_shutdown.assert_called_once_with(api_bridge=api_bridge)

    def test_native_close_requires_and_consumes_one_lifecycle_decision(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = _FakeWindow()
        api_bridge._attach_window(window)

        refused = api_bridge.approve_application_close("")
        self.assertFalse(refused["ok"])
        self.assertTrue(refused["lifecycle_decision_required"])
        window.destroy.assert_not_called()

        decision = webview_launcher.lifecycle_coordinator.issue_decision("close")
        approved = api_bridge.approve_application_close(decision)

        self.assertEqual(approved, {"ok": True})
        window.destroy.assert_called_once_with()
        replay = api_bridge.approve_application_close(decision)
        self.assertFalse(replay["ok"])

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


class _MinimizableWindow:
    """A window that records minimize calls and exposes no `native` attribute.

    No `native` means `_run_on_native_ui_thread` runs the callback inline,
    which is exactly the non-Windows path and keeps the batch's own ordering
    observable.
    """

    def __init__(self, on_minimize=None):
        self.minimize_calls = 0
        self._on_minimize = on_minimize

    def minimize(self):
        self.minimize_calls += 1
        if self._on_minimize is not None:
            self._on_minimize()


class MinimizeAllWindowsTestCase(unittest.TestCase):
    """Item 4-A: one batch, two triggers.

    The control and the cascade are the same `minimize_all_windows()` call, so
    everything that could make it misbehave — re-entrancy, an echo of its own
    `minimized` events, a window already down — is asserted once, here.
    """

    def _bridge_with(self, workspaces=1, launcher=True):
        bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        windows = {}
        for index in range(workspaces):
            workspace_id = "default" if index == 0 else f"aaaaaaaaaa{index:02d}"
            window = _MinimizableWindow()
            bridge._attach_workspace_window(workspace_id, window)
            windows[f"workspace:{workspace_id}"] = window
        if launcher:
            window = _MinimizableWindow()
            bridge._attach_window(window)
            windows["launcher"] = window
        return bridge, windows

    def test_the_batch_minimizes_every_window_and_records_each_as_minimized(self):
        bridge, windows = self._bridge_with(workspaces=2)

        result = bridge.minimize_all_windows()

        self.assertTrue(result["ok"])
        self.assertEqual(result["minimized"], 3)
        for name, window in windows.items():
            self.assertEqual(window.minimize_calls, 1, name)
            # Tracked immediately, so the `minimized` events this batch
            # provokes reach a handler that can tell they are its own echo.
            self.assertTrue(bridge._is_window_minimized(name), name)

    def test_a_window_already_down_is_left_alone(self):
        bridge, windows = self._bridge_with(workspaces=2)
        bridge._set_window_minimized("workspace:aaaaaaaaaa01", True)

        result = bridge.minimize_all_windows()

        self.assertEqual(result["minimized"], 2)
        self.assertEqual(windows["workspace:aaaaaaaaaa01"].minimize_calls, 0)
        self.assertEqual(windows["launcher"].minimize_calls, 1)

    def test_a_batch_provoked_from_inside_itself_runs_once(self):
        bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        reentries = []

        def _reenter():
            reentries.append(bridge.minimize_all_windows())

        first = _MinimizableWindow(on_minimize=_reenter)
        second = _MinimizableWindow()
        bridge._attach_workspace_window("default", first)
        bridge._attach_window(second)

        result = bridge.minimize_all_windows()

        self.assertEqual(result["minimized"], 2)
        self.assertEqual(first.minimize_calls, 1)
        self.assertEqual(second.minimize_calls, 1)
        self.assertEqual([answer["suppressed"] for answer in reentries], [True])
        # The guard is released again, or the control would work exactly once.
        self.assertFalse(bridge._minimizing_all)

    def test_a_window_that_cannot_minimize_does_not_stop_the_rest(self):
        bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        stubborn = _MinimizableWindow()
        stubborn.minimize = Mock(side_effect=RuntimeError("no"))
        healthy = _MinimizableWindow()
        bridge._attach_workspace_window("default", stubborn)
        bridge._attach_window(healthy)

        result = bridge.minimize_all_windows()

        self.assertTrue(result["ok"])
        self.assertEqual(result["minimized"], 1)
        self.assertEqual(healthy.minimize_calls, 1)
        self.assertFalse(bridge._is_window_minimized("workspace:default"))

    def test_the_cascade_setting_is_read_from_runtime_config(self):
        bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        with patch.object(
            webview_launcher.runtime_config, "workspace_minimize_cascade", True
        ):
            self.assertTrue(bridge._minimize_cascade_enabled())
        with patch.object(
            webview_launcher.runtime_config, "workspace_minimize_cascade", False
        ):
            self.assertFalse(bridge._minimize_cascade_enabled())


class MinimizeCascadeEventTestCase(unittest.TestCase):
    """4-D: the cascade is the `minimized` event's second trigger.

    Off by default, minimize-only, and never re-entered by the events its own
    batch produces.
    """

    def _register(self, api_bridge, window):
        """Run the real `register_window` from main() against one fake window."""
        fake_thread = _FakeThread()
        fake_webview = Mock()
        fake_webview.create_window.return_value = window

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
        ), patch.object(
            webview_launcher,
            "GridVibeApi",
            return_value=api_bridge,
        ):
            webview_launcher.main()
        return window

    def _api_with_registered_launcher(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = self._register(api_bridge, _FakeWindow())
        return api_bridge, window, window.events.minimized.handlers[0]

    def test_minimizing_one_window_leaves_the_others_alone_by_default(self):
        api_bridge, _window, on_minimized = self._api_with_registered_launcher()
        workspace = _MinimizableWindow()
        api_bridge._attach_workspace_window("default", workspace)

        with patch.object(
            webview_launcher.runtime_config, "workspace_minimize_cascade", False
        ):
            on_minimized()

        self.assertEqual(workspace.minimize_calls, 0)
        self.assertTrue(api_bridge._is_window_minimized("launcher"))

    def test_with_the_setting_on_one_minimize_takes_the_rest_down(self):
        api_bridge, _window, on_minimized = self._api_with_registered_launcher()
        first = _MinimizableWindow()
        second = _MinimizableWindow()
        api_bridge._attach_workspace_window("default", first)
        api_bridge._attach_workspace_window("aaaaaaaaaa02", second)

        with patch.object(
            webview_launcher.runtime_config, "workspace_minimize_cascade", True
        ):
            on_minimized()

        self.assertEqual(first.minimize_calls, 1)
        self.assertEqual(second.minimize_calls, 1)

    def test_the_batchs_own_echo_never_cascades_again(self):
        api_bridge, _window, on_minimized = self._api_with_registered_launcher()
        workspace = _MinimizableWindow()
        api_bridge._attach_workspace_window("default", workspace)

        with patch.object(
            webview_launcher.runtime_config, "workspace_minimize_cascade", True
        ):
            on_minimized()
            # The event the batch itself provoked, delivered after the batch
            # released its re-entrancy flag: the window is already tracked
            # minimized, so it is an echo and not a new gesture.
            on_minimized()

        self.assertEqual(workspace.minimize_calls, 1)

    def test_restore_is_not_cascaded(self):
        api_bridge, window, _on_minimized = self._api_with_registered_launcher()
        workspace = _MinimizableWindow()
        api_bridge._attach_workspace_window("default", workspace)
        api_bridge._set_window_minimized("workspace:default", True)
        api_bridge._set_window_minimized("launcher", True)

        window.events.restored.handlers[0]()

        # Bringing four windows back because one taskbar entry was clicked is
        # the bigger surprise, so the other windows stay down.
        self.assertFalse(api_bridge._is_window_minimized("launcher"))
        self.assertTrue(api_bridge._is_window_minimized("workspace:default"))


class TeardownNoSnapshotTestCase(unittest.TestCase):
    """10.5 hardening — teardown must never write the workspace snapshot.

    Only the autosave timer and the explicit Save Workspace action capture the
    workspace; window close and Ctrl+C intentionally persist nothing.
    """

    def test_launcher_has_no_snapshot_capture_path(self):
        self.assertFalse(
            hasattr(webview_launcher, "_snapshot_workspace_before_teardown"),
            "the teardown snapshot capture was intentionally removed",
        )
        self.assertFalse(
            hasattr(webview_launcher, "save_workspace_snapshot"),
            "the launcher must not import any snapshot writer",
        )

    def test_keyboard_interrupt_closes_sessions_without_snapshotting(self):
        server_thread = Mock()
        server_thread.join.side_effect = KeyboardInterrupt
        with patch.object(webview_launcher, "_open_browser_window"), patch.object(
            webview_launcher.session_manager, "close_all_sessions"
        ) as close_all, patch(
            "web.runtime_state.capture_workspace"
        ) as capture:
            webview_launcher._open_browser_mode("http://127.0.0.1:5050", server_thread)
        close_all.assert_called_once()
        capture.assert_not_called()


class BrowserModeWindowTestCase(unittest.TestCase):
    """Browser mode is one browser window per app run.

    The launcher tab and the tab each workspace opens belong together, so
    starting GridVibe must not drop the launcher into whatever window happens
    to be in front. `webbrowser.open(url, new=1)` cannot express that on
    Windows (the default controller is `os.startfile`), so the default
    browser's own new-window flag is used when the family is recognised.
    """

    def test_a_known_default_browser_is_launched_with_its_new_window_flag(self):
        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher,
            "_windows_default_browser_executable",
            return_value=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ), patch.object(webview_launcher.subprocess, "Popen") as popen, patch.object(
            webview_launcher.webbrowser, "open"
        ) as browser_open:
            self.assertTrue(webview_launcher._open_browser_window("http://127.0.0.1:5050"))

        browser_open.assert_not_called()
        self.assertEqual(
            popen.call_args.args[0],
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "--new-window",
                "http://127.0.0.1:5050",
            ],
        )

    def test_firefox_gets_its_own_flag_spelling(self):
        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher,
            "_windows_default_browser_executable",
            return_value=r"C:\Program Files\Mozilla Firefox\firefox.exe",
        ):
            command = webview_launcher._new_window_browser_command("http://x/")

        self.assertEqual(command[1], "-new-window")

    def test_an_unknown_browser_falls_back_to_the_plain_open(self):
        """Never guess a flag: an unrecognised browser gets the stdlib open,
        which still asks for a new window everywhere it can honour it."""
        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher,
            "_windows_default_browser_executable",
            return_value=r"C:\Tools\SomeBrowser\somebrowser.exe",
        ), patch.object(webview_launcher.subprocess, "Popen") as popen, patch.object(
            webview_launcher.webbrowser, "open", return_value=True
        ) as browser_open:
            self.assertTrue(webview_launcher._open_browser_window("http://127.0.0.1:5050"))

        popen.assert_not_called()
        browser_open.assert_called_once_with("http://127.0.0.1:5050", new=1)

    def test_a_browser_that_will_not_start_still_opens_the_app(self):
        with patch.object(webview_launcher.sys, "platform", "win32"), patch.object(
            webview_launcher,
            "_windows_default_browser_executable",
            return_value=r"C:\gone\chrome.exe",
        ), patch.object(
            webview_launcher.subprocess, "Popen", side_effect=OSError("gone")
        ), patch.object(
            webview_launcher.webbrowser, "open", return_value=True
        ) as browser_open:
            self.assertTrue(webview_launcher._open_browser_window("http://127.0.0.1:5050"))

        browser_open.assert_called_once_with("http://127.0.0.1:5050", new=1)

    def test_posix_leaves_the_new_window_request_to_the_stdlib_controller(self):
        """The Unix controllers already map new=1 onto --new-window/-new-window,
        so there is no second implementation of that table for them."""
        with patch.object(webview_launcher.sys, "platform", "linux"):
            self.assertEqual(webview_launcher._new_window_browser_command("http://x/"), [])

    def test_browser_mode_opens_a_window_rather_than_a_bare_url(self):
        server_thread = Mock()
        with patch.object(webview_launcher, "_open_browser_window") as open_window:
            webview_launcher._open_browser_mode("http://127.0.0.1:5050", server_thread)

        open_window.assert_called_once_with("http://127.0.0.1:5050")
        server_thread.join.assert_called_once()


class UploadBridgeTestCase(unittest.TestCase):
    """Native-window explorer upload bridge — save_download's mirror.

    The page cannot read a local path, so the launcher opens the picker and
    posts the bytes. Everything worth asserting here is a boundary: which URLs
    it will post to, that the body is streamed rather than assembled, and that
    a failure is reported with the server's own verdict about whether anything
    was written.
    """

    def _make_api_with_window(self, dialog_result):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = Mock()
        window.create_file_dialog.return_value = dialog_result
        api_bridge._attach_session_window(window)
        return api_bridge, window

    def _write(self, directory, name, payload):
        path = os.path.join(directory, name)
        Path(path).write_bytes(payload)
        return path

    def test_picker_returns_names_and_sizes_for_every_readable_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = self._write(tmp, "a.txt", b"aaa")
            second = self._write(tmp, "b.bin", b"bbbb")
            missing = os.path.join(tmp, "gone.txt")
            api_bridge, window = self._make_api_with_window([first, second, missing])

            with patch.object(webview_launcher, "webview", Mock()):
                result = api_bridge.select_upload_files()

            self.assertTrue(result["ok"])
            self.assertEqual(
                [(row["name"], row["size"]) for row in result["files"]],
                [("a.txt", 3), ("b.bin", 4)],
            )
            self.assertTrue(window.create_file_dialog.call_args.kwargs["allow_multiple"])

    def test_a_cancelled_picker_is_the_users_answer_not_a_failure(self):
        api_bridge, _ = self._make_api_with_window(None)
        with patch.object(webview_launcher, "webview", Mock()):
            result = api_bridge.select_upload_files()
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])

    def test_it_posts_only_to_this_apps_own_upload_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write(tmp, "a.txt", b"x")
            api_bridge, _ = self._make_api_with_window(None)
            for url in ("/api/app-config", "http://evil.example/api/explorer/a/upload",
                        "/api/explorer/abc/download"):
                with self.subTest(url=url):
                    with patch.object(webview_launcher, "urlopen") as urlopen:
                        result = api_bridge.upload_file(url, source, {})
                    self.assertFalse(result["ok"])
                    self.assertIs(result["mutated"], False)
                    urlopen.assert_not_called()

    def test_the_body_is_streamed_and_carries_the_fields_the_route_expects(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write(tmp, "logo.bin", b"\x00\x01\x02\x03payload")
            api_bridge, _ = self._make_api_with_window(None)
            answer = Mock()
            answer.status = 200
            answer.read.return_value = b'{"destination_path": "docs/logo.bin"}'
            answer.__enter__ = Mock(return_value=answer)
            answer.__exit__ = Mock(return_value=False)

            sent = {}

            def _drain(request, timeout=None):
                # Drained here, inside the request, because that is where the
                # real client reads it — the source handle is closed by the
                # time upload_file returns, which is the point of streaming.
                body = request.data
                sent["request"] = request
                sent["streamed"] = not isinstance(body, (bytes, bytearray))
                sent["length"] = body.length
                chunks = []
                while True:
                    chunk = body.read(7)
                    if not chunk:
                        break
                    chunks.append(chunk)
                sent["bytes"] = b"".join(chunks)
                return answer

            with patch.object(webview_launcher, "urlopen", side_effect=_drain):
                result = api_bridge.upload_file(
                    "/api/explorer/abc/upload",
                    source,
                    {
                        "root_revision": "rev-1",
                        "destination_directory": "docs",
                        "name": "logo.bin",
                    },
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["destination_path"], "docs/logo.bin")
            request = sent["request"]
            self.assertEqual(
                request.full_url, "http://127.0.0.1:5050/api/explorer/abc/upload"
            )
            self.assertTrue(
                request.get_header("Content-type").startswith("multipart/form-data; boundary=")
            )
            # The body is a reader, never a bytes: a 100 MB file must not be
            # held in this process for the length of the POST.
            self.assertTrue(sent["streamed"])
            self.assertEqual(int(request.get_header("Content-length")), sent["length"])
            drained = sent["bytes"]
            self.assertEqual(len(drained), sent["length"])
            self.assertIn(b'name="root_revision"\r\n\r\nrev-1', drained)
            self.assertIn(b'name="destination_directory"\r\n\r\ndocs', drained)
            self.assertIn(b'filename="logo.bin"', drained)
            self.assertIn(b"\x00\x01\x02\x03payload", drained)

    def test_a_file_past_the_ceiling_never_opens_a_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write(tmp, "big.bin", b"x" * 64)
            api_bridge, _ = self._make_api_with_window(None)
            with patch.object(webview_launcher, "EXPLORER_UPLOAD_MAX_BYTES", 8):
                with patch.object(webview_launcher, "urlopen") as urlopen:
                    result = api_bridge.upload_file(
                        "/api/explorer/abc/upload", source, {"name": "big.bin"}
                    )
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "upload_too_large")
            urlopen.assert_not_called()

    def test_a_server_refusal_is_relayed_with_its_own_mutation_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._write(tmp, "a.txt", b"x")
            api_bridge, _ = self._make_api_with_window(None)
            body = io.BytesIO(
                b'{"error": "a.txt already exists here", '
                b'"code": "destination_exists", "mutated": false}'
            )
            http_error = HTTPError(
                "http://127.0.0.1:5050/x", 409, "Conflict", {}, body
            )
            with patch.object(webview_launcher, "urlopen", side_effect=http_error):
                result = api_bridge.upload_file(
                    "/api/explorer/abc/upload", source, {"name": "a.txt"}
                )

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], 409)
            self.assertEqual(result["code"], "destination_exists")
            # Retry-safe because the server said so — never because the bridge
            # assumed it.
            self.assertIs(result["mutated"], False)

    def test_a_missing_source_is_refused_without_a_request(self):
        api_bridge, _ = self._make_api_with_window(None)
        with patch.object(webview_launcher, "urlopen") as urlopen:
            result = api_bridge.upload_file(
                "/api/explorer/abc/upload", "/no/such/file.txt", {"name": "file.txt"}
            )
        self.assertFalse(result["ok"])
        self.assertIs(result["mutated"], False)
        urlopen.assert_not_called()

    def test_a_file_that_shrinks_mid_send_fails_instead_of_sending_short(self):
        # Content-Length is already on the wire, so a short body would hang the
        # connection and a padded one would upload bytes nobody chose.
        body = webview_launcher._MultipartUploadBody(b"", io.BytesIO(b"ab"), 8, b"--end")

        self.assertEqual(body.read(8), b"ab")
        with self.assertRaises(OSError):
            body.read(8)


class SaveDownloadBridgeTestCase(unittest.TestCase):
    """Native-window explorer download bridge (WebView2 blocks anchor downloads)."""

    def _make_api_with_window(self, dialog_result):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        window = Mock()
        window.create_file_dialog.return_value = dialog_result
        api_bridge._attach_session_window(window)
        return api_bridge, window

    def test_rejects_urls_outside_the_explorer_download_endpoint(self):
        api_bridge, window = self._make_api_with_window("C:/tmp/out.bin")
        with patch.object(webview_launcher, "webview", Mock()):
            result = api_bridge.save_download("/api/app-config", "x")
        self.assertFalse(result["ok"])
        window.create_file_dialog.assert_not_called()

    def test_cancelled_dialog_returns_cancelled(self):
        api_bridge, _ = self._make_api_with_window(None)
        with patch.object(webview_launcher, "webview", Mock()):
            result = api_bridge.save_download(
                "/api/explorer/abc/download?path=a.txt", "a.txt"
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])

    def test_workspace_id_targets_the_calling_workspace_dialog(self):
        api_bridge = webview_launcher.GridVibeApi("http://127.0.0.1:5050")
        first_window = Mock()
        second_window = Mock()
        second_window.create_file_dialog.return_value = None
        api_bridge._attach_workspace_window("aaaaaaaaaaaa", first_window)
        api_bridge._attach_workspace_window("bbbbbbbbbbbb", second_window)

        with patch.object(webview_launcher, "webview", Mock()):
            result = api_bridge.save_download(
                "/api/explorer/abc/download?path=a.txt",
                "a.txt",
                "bbbbbbbbbbbb",
            )

        self.assertTrue(result["cancelled"])
        first_window.create_file_dialog.assert_not_called()
        second_window.create_file_dialog.assert_called_once()

    def test_saves_fetched_bytes_to_the_chosen_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "saved.bin")
            api_bridge, window = self._make_api_with_window(dest)
            fake_webview = Mock()
            payload = b"\x00binary\xffcontent"
            with patch.object(webview_launcher, "webview", fake_webview), patch.object(
                webview_launcher, "urlopen", return_value=io.BytesIO(payload)
            ) as urlopen:
                result = api_bridge.save_download(
                    "/api/explorer/abc/download?path=x.bin", "x.bin"
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["path"], dest)
            self.assertEqual(Path(dest).read_bytes(), payload)
            window.create_file_dialog.assert_called_once_with(
                fake_webview.SAVE_DIALOG, save_filename="x.bin"
            )
            urlopen.assert_called_once()
            self.assertIn(
                "http://127.0.0.1:5050/api/explorer/abc/download",
                urlopen.call_args.args[0],
            )

    def test_http_error_surfaces_the_server_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "saved.bin")
            api_bridge, _ = self._make_api_with_window(dest)
            body = io.BytesIO(b'{"error": "File exceeds the 100 MB download limit"}')
            http_error = HTTPError(
                "http://127.0.0.1:5050/x", 400, "Bad Request", {}, body
            )
            with patch.object(webview_launcher, "webview", Mock()), patch.object(
                webview_launcher, "urlopen", side_effect=http_error
            ):
                result = api_bridge.save_download(
                    "/api/explorer/abc/download?path=big.log", "big.log"
                )
            self.assertFalse(result["ok"])
            self.assertIn("100 MB", result["error"])
            self.assertFalse(os.path.exists(dest))
