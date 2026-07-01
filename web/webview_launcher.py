"""
Launcher for GridVibe browser and native pywebview modes.

The default auto mode preserves the historical native-first behavior and falls
back to the system browser if pywebview is missing.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from main import setup_logging
from web.api import app, load_config, session_manager, socketio

try:
    import webview
except ImportError:  # pragma: no cover - optional dependency at runtime
    webview = None

logger = logging.getLogger(__name__)


def _preferred_pywebview_gui():
    """Choose a stable pywebview backend for platforms with known needs."""
    if sys.platform == "win32":
        return "edgechromium"
    if sys.platform == "linux":
        return "qt"
    return None


def _set_webview2_media_env():
    """Set the WebView2 environment variable for Chromium media flags.

    ``WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`` is the Microsoft-documented way
    to inject Chromium switches into the WebView2 runtime.  These are additive
    with ``AdditionalBrowserArguments`` set by pywebview.

    Only lightweight, well-supported flags are included here.  Aggressive
    sandbox-disabling flags are intentionally omitted because they do not
    resolve ``NotReadableError`` on corporate-managed machines and may
    interfere with future pywebview updates.
    """
    _flags = (
        "--enable-media-stream "
        "--autoplay-policy=no-user-gesture-required"
    )
    existing = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    if "--enable-media-stream" not in existing:
        combined = f"{existing} {_flags}".strip() if existing else _flags
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = combined
        logger.info("Set WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=%s", combined)


def _merge_env_flags(name: str, flags: tuple[str, ...]) -> str:
    """Add missing command-line flags to an environment variable."""
    existing = os.environ.get(name, "")
    parts = existing.split()
    for flag in flags:
        if flag not in parts:
            parts.append(flag)
    combined = " ".join(parts).strip()
    os.environ[name] = combined
    return combined


def _set_linux_qtwebengine_env():
    """Optionally avoid QtWebEngine GPU paths that are unstable in some Linux VMs.

    This is intentionally opt-in. Forcing software/GPU flags can freeze some
    QtWebEngine builds even when the same app works correctly in a browser.
    """
    if sys.platform != "linux":
        return
    if os.environ.get("GRIDVIBE_QTWEBENGINE_GPU_FALLBACK") != "1":
        return

    chromium_flags = _merge_env_flags(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        (
            "--disable-gpu",
            "--disable-features=Vulkan",
        ),
    )
    os.environ.setdefault("QT_OPENGL", "software")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    logger.info(
        "Configured Linux QtWebEngine GPU fallback "
        "QTWEBENGINE_CHROMIUM_FLAGS=%s QT_OPENGL=%s LIBGL_ALWAYS_SOFTWARE=%s",
        chromium_flags,
        os.environ.get("QT_OPENGL"),
        os.environ.get("LIBGL_ALWAYS_SOFTWARE"),
    )


def _patch_webview2_permissions():
    """Patch EdgeChrome for microphone/camera access in WebView2.

    WebView2 denies ``getUserMedia()`` by default unless the host application
    handles the ``PermissionRequested`` event.  pywebview does not wire this up,
    so we monkey-patch ``EdgeChrome.on_webview_ready`` to attach the handler
    after the CoreWebView2 control is initialised.  The patch applies to every
    window created through the EdgeChrome backend (launcher *and* session).

    Note: on corporate-managed machines the embedded WebView2 process may still
    be blocked from opening audio hardware even when permissions are granted.
    Browser mode remains the reliable fallback in those environments.
    """
    try:
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2PermissionKind,
            CoreWebView2PermissionState,
        )
        from webview.platforms import edgechromium
    except Exception:
        logger.debug("edgechromium platform not available; permission patch skipped")
        return False

    _original_on_webview_ready = edgechromium.EdgeChrome.on_webview_ready
    _granted_kinds = frozenset({
        CoreWebView2PermissionKind.Microphone,
        CoreWebView2PermissionKind.Camera,
    })

    def _on_permission_requested(_sender, args):
        if args.PermissionKind in _granted_kinds:
            args.State = CoreWebView2PermissionState.Allow
            logger.info("Auto-granted WebView2 permission: %s", args.PermissionKind)

    def _patched_on_webview_ready(self, sender, args):
        _original_on_webview_ready(self, sender, args)
        if args.IsSuccess:
            try:
                sender.CoreWebView2.PermissionRequested += _on_permission_requested
                logger.info("Attached WebView2 PermissionRequested handler for media access")
            except Exception as exc:
                logger.warning("Could not attach PermissionRequested handler: %s", exc)

    edgechromium.EdgeChrome.on_webview_ready = _patched_on_webview_ready
    logger.info("Patched EdgeChrome for automatic media permission grants")
    return True


def _log_pywebview_runtime(requested_gui: str | None):
    """Log the actual pywebview renderer selected after startup."""
    actual_renderer = getattr(webview, "renderer", None) if webview is not None else None
    logger.info(
        "pywebview runtime initialized requested_gui=%s actual_renderer=%s",
        requested_gui or "default",
        actual_renderer or "unknown",
    )


def _is_missing_linux_pywebview_backend(exc: Exception) -> bool:
    """Detect pywebview's Linux error for missing GTK/Qt Python bindings."""
    if sys.platform != "linux":
        return False
    message = str(exc)
    return (
        "either QT or GTK" in message
        or "GTK cannot be loaded" in message
        or "QT cannot be loaded" in message
    )


def _log_linux_pywebview_backend_help():
    logger.error(
        "pywebview is installed, but no Linux GUI backend could be loaded. "
        "Install the Qt backend in this virtualenv with: "
        "python -m pip install --upgrade -r requirements-desktop.txt. "
        "GTK is also supported, but it requires distro PyGObject/WebKit packages "
        "that are visible to the active Python environment."
    )


def _ignore_linux_job_control_stop_signals():
    """Keep the desktop launcher alive if terminal job control sends stop signals."""
    if sys.platform != "linux":
        return

    configured = []
    for signal_name in ("SIGTSTP", "SIGTTIN", "SIGTTOU"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is None:
            continue
        try:
            signal.signal(signal_value, signal.SIG_IGN)
            configured.append(signal_name)
        except (OSError, ValueError):
            logger.debug("Could not ignore %s for Linux launcher", signal_name)

    if configured:
        logger.info(
            "Ignoring Linux terminal job-control stop signals: %s",
            ", ".join(configured),
        )


def _open_browser_mode(base_url: str, server_thread: threading.Thread):
    logger.info("Opening GridVibe in the system browser at %s", base_url)
    webbrowser.open(base_url)
    try:
        server_thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down after keyboard interrupt")
        session_manager.close_all_sessions()


def _open_browser_fallback(base_url: str, server_thread: threading.Thread):
    logger.warning("Falling back to the system browser at %s", base_url)
    _open_browser_mode(base_url, server_thread)


def _exit_after_startup_failure(code: int = 1):
    try:
        session_manager.close_all_sessions()
    except Exception:
        logger.exception("Failed to close sessions after launcher startup failure")
    os._exit(code)


def _should_exit_after_window_close(kind: str, open_windows: set[str]) -> bool:
    """Treat the launcher/settings window as the owner of the desktop app lifecycle."""
    return kind == "launcher" or not open_windows


class GridVibeApi:
    """Expose native window actions to the pywebview frontend."""

    def __init__(self, base_url: str):
        self._base_url = base_url
        self._window = None
        self._session_window = None
        self._session_window_group_id = ""
        self._is_fullscreen = False
        self._session_is_fullscreen = False
        self._register_window = None
        self._window_minimized = False
        self._session_window_minimized = False
        self._restarting = False

    def _attach_window(self, window):
        """Attach the created pywebview window instance."""
        self._window = window

    def _attach_session_window(self, window, group_id: str = ""):
        """Attach the persistent session window instance."""
        self._session_window = window
        self._session_window_group_id = str(group_id or "").strip()

    def _set_register_window(self, callback):
        """Store the window registration callback shared by the launcher."""
        self._register_window = callback

    def _set_window_minimized(self, window_name: str, minimized: bool):
        """Track whether the named window is currently minimized."""
        if window_name == "launcher":
            self._window_minimized = minimized
        elif window_name == "session":
            self._session_window_minimized = minimized

    def _is_window_minimized(self, window_name: str) -> bool:
        """Return the tracked minimized state for the named window."""
        if window_name == "launcher":
            return self._window_minimized
        if window_name == "session":
            return self._session_window_minimized
        return False

    def _pulse_on_top(self, window, window_name: str) -> bool:
        """Temporarily promote a window to top-most to improve activation reliability."""
        if not hasattr(window, "on_top"):
            logger.info("%s window does not expose on_top; skipping top-most pulse", window_name)
            return False

        try:
            was_on_top = bool(window.on_top)
            if not was_on_top:
                window.on_top = True
                time.sleep(0.05)
                window.on_top = False
                logger.info("Applied temporary top-most pulse to %s window", window_name)
            else:
                logger.info("%s window is already top-most; skipping pulse reset", window_name)
            return True
        except Exception as exc:
            logger.warning("Failed to apply top-most pulse to %s window: %s", window_name, exc)
            return False

    def _should_skip_top_most_pulse(self, window_name: str) -> bool:
        """Avoid the focus workaround only where it is known to cause renderer issues."""
        return sys.platform == "win32" and window_name in {"session", "launcher"}

    def toggle_fullscreen(self):
        """Toggle native fullscreen mode for the current window."""
        if self._window is None:
            return {"ok": False, "error": "Window is not ready"}

        self._window.toggle_fullscreen()
        self._is_fullscreen = not self._is_fullscreen
        return {"ok": True}

    def exit_fullscreen(self):
        """Exit native fullscreen mode when the window is currently fullscreen."""
        if self._window is None:
            return {"ok": False, "error": "Window is not ready"}

        if self._is_fullscreen:
            self._window.toggle_fullscreen()
            self._is_fullscreen = False

        return {"ok": True}

    def get_fullscreen_state(self):
        """Return the tracked native fullscreen state for the current window."""
        return {"ok": True, "is_fullscreen": self._is_fullscreen}

    def toggle_session_fullscreen(self):
        """Toggle native fullscreen mode for the session window."""
        if self._session_window is None:
            return {"ok": False, "error": "Session window is not ready"}

        self._session_window.toggle_fullscreen()
        self._session_is_fullscreen = not self._session_is_fullscreen
        return {"ok": True}

    def exit_session_fullscreen(self):
        """Exit native fullscreen mode for the session window."""
        if self._session_window is None:
            return {"ok": False, "error": "Session window is not ready"}

        if self._session_is_fullscreen:
            self._session_window.toggle_fullscreen()
            self._session_is_fullscreen = False

        return {"ok": True}

    def get_session_fullscreen_state(self):
        """Return the tracked native fullscreen state for the session window."""
        return {"ok": True, "is_fullscreen": self._session_is_fullscreen}

    def select_folder(self, initial_dir=""):
        """Open a native folder picker from the desktop shell."""
        if self._window is None or webview is None:
            return {"ok": False, "error": "Window is not ready"}

        candidate_dir = str(initial_dir or "").strip()
        if not os.path.isdir(candidate_dir):
            candidate_dir = os.path.expanduser("~")

        try:
            selected = self._window.create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=candidate_dir,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not selected:
            return {"ok": False, "cancelled": True}

        if isinstance(selected, (list, tuple)):
            path = str(selected[0] or "").strip()
        else:
            path = str(selected or "").strip()

        return {"ok": True, "path": path}

    def _bring_to_front(self, window, window_name: str = "window"):
        """Bring a pywebview window forward using the safest available call."""
        if window is None:
            logger.warning("Cannot focus missing %s window", window_name)
            return False

        try:
            was_minimized = self._is_window_minimized(window_name)
            logger.info(
                "Bringing %s window to front (tracked_minimized=%s)",
                window_name,
                was_minimized,
            )
            if was_minimized and hasattr(window, "restore"):
                window.restore()
                self._set_window_minimized(window_name, False)
                logger.info("Restored minimized %s window before focus", window_name)

            window.show()

            if self._should_skip_top_most_pulse(window_name):
                logger.info(
                    "Skipping top-most pulse for %s window to avoid WebView2 focus artefacts",
                    window_name,
                )
            else:
                self._pulse_on_top(window, window_name)
            return True
        except Exception as exc:
            logger.exception("Failed to bring %s window to front: %s", window_name, exc)
            return False

    def focus_session_window(self):
        """Focus the existing session window without changing its URL."""
        if self._session_window is None:
            logger.warning("Session window focus requested, but no session window is registered")
            return {"ok": False, "error": "No session window open"}

        logger.info(
            "Focusing existing session window (group=%s)",
            self._session_window_group_id or "unknown",
        )
        if not self._bring_to_front(self._session_window, "session"):
            return {"ok": False, "error": "Failed to focus session window"}

        logger.info("Focused existing session window")
        return {"ok": True}

    def open_session_window(self, group_id: str):
        """Open or focus the dedicated session window."""
        resolved_group_id = str(group_id or "").strip()
        url = f"{self._base_url}/terminals"
        if resolved_group_id:
            url = f"{url}?group={resolved_group_id}"

        try:
            if self._session_window is not None:
                should_retarget = resolved_group_id != self._session_window_group_id
                logger.info(
                    (
                        "Reusing existing session window "
                        "(requested_group=%s current_group=%s)"
                    ),
                    resolved_group_id or "all",
                    self._session_window_group_id or "unknown",
                )
                if should_retarget:
                    logger.info(
                        "Keeping existing session window open; frontend will switch groups via polling"
                    )
                if not self._bring_to_front(self._session_window, "session"):
                    return {"ok": False, "error": "Failed to focus session window"}
                return {"ok": True, "reused": True}

            if webview is None:
                logger.warning("Session window requested without pywebview support")
                return {"ok": False, "error": "pywebview is unavailable"}

            logger.info(
                "Creating session window for group=%s url=%s",
                resolved_group_id or "all",
                url,
            )
            window = webview.create_window(
                "GridVibe Sessions",
                url,
                width=1600,
                height=980,
                min_size=(1180, 720),
                text_select=True,
                js_api=self,
            )
            if window is None:
                logger.error("pywebview.create_window returned None for the session window")
                return {"ok": False, "error": "Failed to create session window"}

            self._attach_session_window(window, resolved_group_id)
            if self._register_window is not None:
                self._register_window(window, "session")
            logger.info(
                "Session window created and registered (group=%s)",
                resolved_group_id or "all",
            )
            return {"ok": True, "reused": False}
        except Exception as exc:
            logger.exception("Failed to open session window for group=%s", resolved_group_id or "all")
            return {"ok": False, "error": str(exc)}

    def open_launcher_window(self):
        """Focus the launcher window without reloading or resizing it."""
        if self._window is None:
            logger.warning("Launcher window focus requested, but no launcher window is registered")
            return {"ok": False, "error": "Launcher window is not ready"}

        try:
            if not self._bring_to_front(self._window, "launcher"):
                return {"ok": False, "error": "Failed to focus launcher window"}
            logger.info("Focused launcher window")
            return {"ok": True}
        except Exception as exc:
            logger.exception("Failed to focus launcher window")
            return {"ok": False, "error": str(exc)}

    def close_session_window(self):
        """Close the session window when the last session is removed."""
        if self._session_window is None:
            logger.warning("close_session_window called but no session window is registered")
            return {"ok": False, "error": "No session window open"}
        try:
            logger.info("Closing session window (last session removed)")
            self._session_window.destroy()
            return {"ok": True}
        except Exception as exc:
            logger.exception("Failed to close session window")
            return {"ok": False, "error": str(exc)}

    def restart_application(self):
        """Relaunch the native app after an update is applied."""
        project_root = str(Path(__file__).resolve().parent.parent)
        try:
            self._restarting = True
            command = _build_restart_command()
            _schedule_process_restart(command, project_root, os.getpid())
            _start_restart_shutdown_thread(api_bridge=self)
            logger.info("Queued GridVibe restart with command=%s", command)
            return {"ok": True, "restarting": True}
        except Exception as exc:
            self._restarting = False
            logger.exception("Failed to restart GridVibe")
            return {"ok": False, "error": str(exc)}


def _resolve_window_host(host: str) -> str:
    """Map wildcard bind hosts to a local URL the GUI can open."""
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _wait_for_server(base_url: str, timeout: float = 20.0) -> bool:
    """Wait until the Flask server responds to the health check."""
    deadline = time.time() + timeout
    health_url = f"{base_url}/api/health"

    while time.time() < deadline:
        try:
            with urlopen(health_url, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except URLError:
            time.sleep(0.2)
        except OSError:
            time.sleep(0.2)

    return False


def _resolve_icon_path() -> str | None:
    """Return the packaged icon path when it exists."""
    project_root = Path(__file__).resolve().parent.parent
    icon_path = project_root / "docs" / "images" / "GridVibe_icon.ico"
    return str(icon_path) if icon_path.is_file() else None


def _build_restart_command() -> list[str]:
    """Recreate the current native-launch command line.

    On Windows the previous ``cmd /c start …`` wrapper mangled quotes when
    the command was relayed through the helper process (two rounds of
    ``list2cmdline`` turned ``"`` into ``\\"`` which ``cmd.exe`` cannot
    parse).  We now return a plain ``[python, script]`` list and let the
    helper's ``DETACHED_PROCESS`` / ``CREATE_NO_WINDOW`` flags handle
    console suppression instead.
    """
    if sys.platform == "win32":
        project_root = Path(__file__).resolve().parent.parent
        launcher_path = project_root / "webview_launcher.py"
        venv_python = project_root / ".venv" / "Scripts" / "python.exe"
        if launcher_path.is_file() and venv_python.is_file():
            return [str(venv_python), str(launcher_path)]

    if getattr(sys, "frozen", False):  # pragma: no cover - packaged runtime
        return [sys.executable, *sys.argv[1:]]

    launcher_path = Path(sys.argv[0]) if sys.argv and sys.argv[0] else Path(__file__)
    return [sys.executable, str(launcher_path.resolve()), *sys.argv[1:]]


def _schedule_process_restart(command: list[str], cwd: str, parent_pid: int):
    """Start a short-lived helper that relaunches GridVibe after this process exits."""
    helper_code = (
        "import os, subprocess, sys, time\n"
        "parent_pid = int(sys.argv[1])\n"
        "cwd = sys.argv[2]\n"
        "command = sys.argv[3:]\n"
        "if sys.platform == 'win32':\n"
        "    import ctypes\n"
        "    wait_handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, parent_pid)\n"
        "    if wait_handle:\n"
        "        try:\n"
        "            ctypes.windll.kernel32.WaitForSingleObject(wait_handle, 15000)\n"
        "        finally:\n"
        "            ctypes.windll.kernel32.CloseHandle(wait_handle)\n"
        "else:\n"
        "    for _ in range(150):\n"
        "        try:\n"
        "            os.kill(parent_pid, 0)\n"
        "        except OSError:\n"
        "            break\n"
        "        time.sleep(0.1)\n"
        "time.sleep(1)\n"
        "spawn_kwargs = {'cwd': cwd}\n"
        "if sys.platform == 'win32':\n"
        "    spawn_kwargs['creationflags'] = (\n"
        "        getattr(subprocess, 'DETACHED_PROCESS', 0)\n"
        "        | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)\n"
        "        | getattr(subprocess, 'CREATE_BREAKAWAY_FROM_JOB', 0)\n"
        "        | getattr(subprocess, 'CREATE_NO_WINDOW', 0)\n"
        "    )\n"
        "else:\n"
        "    spawn_kwargs['start_new_session'] = True\n"
        "subprocess.Popen(command, **spawn_kwargs)\n"
    )
    helper_kwargs = {"cwd": cwd}
    if sys.platform == "win32":
        helper_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        )
    else:
        helper_kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-c", helper_code, str(parent_pid), cwd, *command],
        **helper_kwargs,
    )


def _exit_current_process_after_restart(
    delay_seconds: float = 1.5, api_bridge=None
):
    """Close active sessions and terminate the current process after restart handoff.

    The delay gives pywebview's IPC bridge time to deliver the success
    response to the frontend before the process is torn down.  Destroying
    pywebview windows explicitly releases the WebView2 user-data lock so
    the new instance can start cleanly.
    """
    time.sleep(delay_seconds)
    try:
        session_manager.close_all_sessions()
    except Exception:
        logger.exception("Failed to close sessions during application restart")
    try:
        from web.api import _stop_vosk_service
        _stop_vosk_service()
    except Exception:
        pass
    if api_bridge is not None:
        for win in (api_bridge._session_window, api_bridge._window):
            try:
                if win is not None:
                    win.destroy()
            except Exception:
                pass
        time.sleep(0.5)
    os._exit(0)


def _start_restart_shutdown_thread(
    delay_seconds: float = 1.5, api_bridge=None
):
    """Schedule the current process to exit after a restart has been queued."""
    threading.Thread(
        target=_exit_current_process_after_restart,
        args=(delay_seconds,),
        kwargs={"api_bridge": api_bridge},
        name="gridvibe-restart-exit",
        daemon=True,
    ).start()


def _run_server(host: str, port: int, debug: bool):
    """Run the Flask-SocketIO server."""
    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


def main():
    """Start GridVibe in the selected UI mode."""
    parser = argparse.ArgumentParser(
        description="Launch GridVibe in browser or native pywebview mode"
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "native", "browser"),
        default="auto",
        help=(
            "Launch mode: auto preserves native-first browser fallback behavior; "
            "native requires pywebview; browser opens the system browser only"
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=5050, help="Port to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    args = parser.parse_args()

    config = load_config(args.config) if os.path.exists(args.config) else {}
    host = config.get("server", {}).get("host", args.host)
    port = config.get("server", {}).get("port", args.port)
    debug = config.get("server", {}).get("debug", args.debug)
    window_host = _resolve_window_host(host)
    base_url = f"http://{window_host}:{port}"

    setup_logging(debug)
    logger = logging.getLogger(__name__)
    _ignore_linux_job_control_stop_signals()
    logger.info("Starting GridVibe mode=%s at %s", args.mode, base_url)

    server_thread = threading.Thread(
        target=_run_server,
        args=(host, port, debug),
        name="gridvibe-server",
        daemon=False,
    )
    server_thread.start()

    if not _wait_for_server(base_url):
        logger.error("GridVibe server did not become ready in time")
        raise SystemExit(1)

    if args.mode == "browser":
        _open_browser_mode(base_url, server_thread)
        return

    if webview is None:
        if args.mode == "auto":
            logger.warning("pywebview is unavailable; falling back to the system browser")
            _open_browser_fallback(base_url, server_thread)
            return
        logger.error(
            "pywebview is unavailable. Install desktop dependencies or rerun "
            "GridVibe in browser mode."
        )
        _exit_after_startup_failure(1)

    open_windows = set()

    def register_window(window, kind: str):
        open_windows.add(kind)
        api_bridge._set_window_minimized(kind, False)

        def _handle_minimized(*_args):
            logger.info("GridVibe %s window minimized", kind)
            api_bridge._set_window_minimized(kind, True)

        def _handle_restored(*_args):
            logger.info("GridVibe %s window restored", kind)
            api_bridge._set_window_minimized(kind, False)

        def _handle_closed(*_args):
            logger.info("GridVibe %s window closed", kind)
            open_windows.discard(kind)
            api_bridge._set_window_minimized(kind, False)
            if kind == "session":
                api_bridge._session_window = None
                api_bridge._session_window_group_id = ""
                api_bridge._session_is_fullscreen = False
            else:
                api_bridge._window = None
                api_bridge._is_fullscreen = False

            if api_bridge._restarting:
                return
            if _should_exit_after_window_close(kind, open_windows):
                session_manager.close_all_sessions()
                os._exit(0)

        minimized_event = getattr(window.events, "minimized", None)
        if minimized_event is not None:
            minimized_event += _handle_minimized

        restored_event = getattr(window.events, "restored", None)
        if restored_event is None:
            restored_event = getattr(window.events, "restore", None)
        if restored_event is not None:
            restored_event += _handle_restored

        window.events.closed += _handle_closed

    api_bridge = GridVibeApi(base_url)
    api_bridge._set_register_window(register_window)
    icon_path = _resolve_icon_path()
    preferred_gui = _preferred_pywebview_gui()
    _set_linux_qtwebengine_env()
    if preferred_gui == "edgechromium":
        _set_webview2_media_env()
        _patch_webview2_permissions()
    window = webview.create_window(
        "GridVibe",
        base_url,
        width=1280,
        height=1000,
        min_size=(480, 480),
        text_select=True,
        js_api=api_bridge,
    )
    if window is None:
        logger.error("Failed to create pywebview window")
        raise SystemExit(1)
    api_bridge._attach_window(window)
    register_window(window, "launcher")
    if preferred_gui:
        logger.info("Starting pywebview with preferred GUI backend: %s", preferred_gui)
    try:
        webview.start(
            func=_log_pywebview_runtime,
            args=(preferred_gui,),
            debug=debug,
            icon=icon_path,
            gui=preferred_gui,
        )
    except Exception as exc:
        if _is_missing_linux_pywebview_backend(exc):
            _log_linux_pywebview_backend_help()
            if args.mode == "auto":
                _open_browser_fallback(base_url, server_thread)
                return
            _exit_after_startup_failure(1)
        raise


if __name__ == "__main__":
    main()
