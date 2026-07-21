"""
Launcher for GridVibe browser and native pywebview modes.

The default auto mode preserves the historical native-first behavior and falls
back to the system browser if pywebview is missing.
"""

import argparse
import ctypes
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from main import setup_logging
from web.api import (
    configure_browser_shutdown,
    load_config,
    resolve_server_settings,
    run_server,
    session_manager,
)

try:
    import webview
except ImportError:  # pragma: no cover - optional dependency at runtime
    webview = None

logger = logging.getLogger(__name__)


_NATIVE_FRAME_COLORS = {"caption": "#111827", "text": "#f8fafc", "border": "#1f2937"}


def _apply_windows_dark_frame_attributes(hwnd: int) -> bool:
    """Apply the complete dark DWM frame attribute set to a Windows HWND."""
    colors = _NATIVE_FRAME_COLORS
    applied = [
        _set_dwm_window_attribute(hwnd, 19, 1),
        _set_dwm_window_attribute(hwnd, 20, 1),
        _set_dwm_window_attribute(hwnd, 38, 2),
        _set_dwm_window_attribute(hwnd, 35, _hex_to_colorref(colors["caption"])),
        _set_dwm_window_attribute(hwnd, 36, _hex_to_colorref(colors["text"])),
        _set_dwm_window_attribute(hwnd, 34, _hex_to_colorref(colors["border"])),
    ]
    return any(applied)


def _hex_to_colorref(hex_color: str) -> int:
    """Convert #rrggbb to the COLORREF integer DWM expects."""
    value = str(hex_color or "").strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    red = int(value[0:2], 16)
    green = int(value[2:4], 16)
    blue = int(value[4:6], 16)
    return red | (green << 8) | (blue << 16)


def _resolve_native_window_handle(window) -> int | None:
    """Resolve a pywebview WinForms HWND if it is already available."""
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is None:
        return None
    to_int = getattr(handle, "ToInt32", None)
    if callable(to_int):
        return int(to_int())
    try:
        return int(handle)
    except (TypeError, ValueError):
        return None


def _run_on_native_ui_thread(window, callback):
    """Run a native-window callback on the WinForms UI thread when possible."""
    native = getattr(window, "native", None)
    if native is None:
        return callback()

    try:
        if getattr(native, "InvokeRequired", False):
            from System import Action

            result = {"value": None}

            def _invoke_callback():
                result["value"] = callback()

            native.Invoke(Action(_invoke_callback))
            return result["value"]
    except Exception:
        logger.debug("Native UI-thread invocation failed; running inline", exc_info=True)

    return callback()


def _set_dwm_window_attribute(hwnd: int, attribute: int, value: int) -> bool:
    """Set one DWM window attribute when available."""
    windll = getattr(ctypes, "windll", None)
    dwmapi = getattr(windll, "dwmapi", None) if windll is not None else None
    if dwmapi is None:
        return False

    try:
        raw_value = ctypes.c_int(value)
        result = dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(raw_value),
            ctypes.sizeof(raw_value),
        )
        return result == 0
    except Exception:
        logger.debug("DWM attribute %s update failed", attribute, exc_info=True)
        return False


def _refresh_windows_native_frame(hwnd: int) -> bool:
    """Force Windows to repaint the non-client frame after DWM color changes."""
    windll = getattr(ctypes, "windll", None)
    user32 = getattr(windll, "user32", None) if windll is not None else None
    if user32 is None:
        return False

    try:
        swp_flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(0), 0, 0, 0, 0, swp_flags)
        redraw_flags = 0x0001 | 0x0400 | 0x0100
        redraw_window = getattr(user32, "RedrawWindow", None)
        if redraw_window is not None:
            redraw_window(ctypes.c_void_p(hwnd), None, None, redraw_flags)
        send_message = getattr(user32, "SendMessageW", None)
        if send_message is not None:
            send_message(ctypes.c_void_p(hwnd), 0x0086, 1, 0)
            send_message(ctypes.c_void_p(hwnd), 0x0085, 1, 0)
        dwmapi = getattr(windll, "dwmapi", None) if windll is not None else None
        dwm_flush = getattr(dwmapi, "DwmFlush", None) if dwmapi is not None else None
        if dwm_flush is not None:
            dwm_flush()
        return True
    except Exception:
        logger.debug("Windows native frame refresh failed", exc_info=True)
        return False


def _refresh_native_form(window) -> bool:
    """Invalidate and update the WinForms native form itself."""
    native = getattr(window, "native", None)
    if native is None:
        return False

    refreshed = False
    for method_name in ("Invalidate", "Update", "Refresh"):
        method = getattr(native, method_name, None)
        if callable(method):
            try:
                method()
                refreshed = True
            except Exception:
                logger.debug("Native form %s failed", method_name, exc_info=True)
    return refreshed


def _apply_windows_native_frame_theme(window, theme: str) -> bool:
    """Apply GridVibe's permanent dark colors to a normal Windows native title bar."""
    if sys.platform != "win32":
        return False

    def _apply():
        hwnd = _resolve_native_window_handle(window)
        if not hwnd:
            return False

        changed = _apply_windows_dark_frame_attributes(hwnd)
        if changed:
            _refresh_windows_native_frame(hwnd)
            _refresh_native_form(window)
        return changed

    return _run_on_native_ui_thread(window, _apply)


def _patch_winforms_dark_title_bar() -> bool:
    """Keep pywebview's WinForms title-bar theme hook permanently dark."""
    if sys.platform != "win32" or webview is None:
        return False

    try:
        from webview.platforms import winforms
    except Exception:
        logger.debug("WinForms title-bar dark patch unavailable", exc_info=True)
        return False

    browser_form = getattr(getattr(winforms, "BrowserView", None), "BrowserForm", None)
    if browser_form is None:
        return False
    if getattr(browser_form, "_gridvibe_dark_title_bar_patched", False):
        return True

    def _gridvibe_update_title_bar_theme(self):
        handle = getattr(self, "Handle", None)
        to_int = getattr(handle, "ToInt32", None)
        if not callable(to_int):
            return
        hwnd = int(to_int())
        _apply_windows_dark_frame_attributes(hwnd)

    browser_form.update_title_bar_theme = _gridvibe_update_title_bar_theme
    browser_form._gridvibe_dark_title_bar_patched = True
    return True


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
        self._native_theme = "dark"

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

    def _apply_native_frame_theme(self, window, window_name: str = "window") -> bool:
        """Apply the current native frame theme to a native window frame."""
        applied = _apply_windows_native_frame_theme(window, self._native_theme)
        if applied:
            logger.debug("Applied %s native frame theme to %s", self._native_theme, window_name)
        return applied

    def _apply_native_frame_theme_to_windows(self) -> bool:
        """Apply the current native frame theme to all registered native windows."""
        applied = False
        if self._window is not None:
            applied = self._apply_native_frame_theme(self._window, "launcher") or applied
        if self._session_window is not None:
            applied = self._apply_native_frame_theme(self._session_window, "session") or applied
        return applied

    def set_native_theme(self, _theme=None):
        """Re-apply the permanent dark native frame; the app theme argument is intentionally ignored."""
        applied = self._apply_native_frame_theme_to_windows()
        return {"ok": True, "theme": self._native_theme, "applied": applied}

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

    def save_download(self, download_url, filename=""):
        """Save an in-app file download to disk via a native Save dialog.

        WebView2 silently drops programmatic ``<a download>`` clicks (the same
        class of limitation as ``window.prompt``/``confirm``), so the terminals
        page routes explorer downloads here when running in the native window.
        The bytes are fetched from the local server over its own HTTP endpoint,
        so the endpoint's root-confinement and size-cap checks still apply.
        """
        window = self._session_window or self._window
        if window is None or webview is None:
            return {"ok": False, "error": "Window is not ready"}

        relative_url = str(download_url or "").strip()
        # Only ever fetch the app's own explorer download endpoint — never an
        # arbitrary URL handed in from the page.
        if not relative_url.startswith("/api/explorer/") or "/download" not in relative_url:
            return {"ok": False, "error": "Unsupported download request"}

        safe_name = os.path.basename(str(filename or "").strip()) or "download"

        try:
            selected = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=safe_name,
            )
        except Exception as exc:
            logger.warning("Native save dialog failed: %s", exc)
            return {"ok": False, "error": str(exc)}

        if not selected:
            return {"ok": False, "cancelled": True}
        if isinstance(selected, (list, tuple)):
            destination = str(selected[0] or "").strip()
        else:
            destination = str(selected or "").strip()
        if not destination:
            return {"ok": False, "cancelled": True}

        request_url = f"{self._base_url}{relative_url}"
        try:
            with urlopen(request_url, timeout=120) as response:
                with open(destination, "wb") as handle:
                    shutil.copyfileobj(response, handle)
        except HTTPError as exc:
            message = exc.read().decode("utf-8", errors="ignore") if exc.fp else ""
            try:
                message = json.loads(message).get("error", message)
            except Exception:
                pass
            logger.warning("Download request failed (%s): %s", exc.code, message)
            return {"ok": False, "error": message or f"Download failed ({exc.code})"}
        except (URLError, OSError) as exc:
            logger.warning("Could not save download to %s: %s", destination, exc)
            try:
                if os.path.exists(destination) and os.path.getsize(destination) == 0:
                    os.remove(destination)
            except OSError:
                pass
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "path": destination}

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
            _patch_winforms_dark_title_bar()
            window = webview.create_window(
                "GridVibe Sessions",
                url,
                width=1600,
                height=980,
                min_size=(1180, 720),
                resizable=True,
                frameless=False,
                easy_drag=False,
                background_color="#0d0d0d",
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
        """Close the native session window."""
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
    """Run the shared Flask-SocketIO server entry point."""
    run_server(host, port, debug)


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
    parser.add_argument("--host", default=None, help="Host to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to (default: 5050)")
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable debug mode",
    )
    parser.add_argument("--config", default="config.json", help="Path to configuration file")
    args = parser.parse_args()
    configure_browser_shutdown(args.mode == "browser")

    # Explicit CLI flags win over config values (finding 4.7)
    config = load_config(args.config) if os.path.exists(args.config) else {}
    host, port, debug = resolve_server_settings(
        config, host=args.host, port=args.port, debug=args.debug
    )
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
        return

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
            # No teardown snapshot: restore-after-restart relies on the last
            # autosave tick or explicit Save Workspace (10.5 hardening).
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

        before_show_event = getattr(window.events, "before_show", None)
        if before_show_event is not None:
            before_show_event += lambda *_args: api_bridge._apply_native_frame_theme(window, kind)

        shown_event = getattr(window.events, "shown", None)
        if shown_event is not None:
            shown_event += lambda *_args: api_bridge._apply_native_frame_theme(window, kind)

        window.events.closed += _handle_closed
        api_bridge._apply_native_frame_theme(window, kind)

    api_bridge = GridVibeApi(base_url)
    api_bridge._set_register_window(register_window)
    icon_path = _resolve_icon_path()
    preferred_gui = _preferred_pywebview_gui()
    _set_linux_qtwebengine_env()
    if preferred_gui == "edgechromium":
        _set_webview2_media_env()
        _patch_webview2_permissions()
    _patch_winforms_dark_title_bar()
    window = webview.create_window(
        "GridVibe",
        base_url,
        width=1280,
        height=840,
        min_size=(1024, 700),
        resizable=True,
        frameless=False,
        easy_drag=False,
        background_color="#070b18",
        text_select=True,
        js_api=api_bridge,
    )
    if window is None:
        logger.error("Failed to create pywebview window")
        if args.mode == "auto":
            _open_browser_fallback(base_url, server_thread)
            return
        _exit_after_startup_failure(1)
        return
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
            return
        logger.exception("Native window startup failed")
        if args.mode == "auto":
            _open_browser_fallback(base_url, server_thread)
            return
        _exit_after_startup_failure(1)


if __name__ == "__main__":
    main()
