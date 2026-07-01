# Browser Pane Mode Research

## Todo Item / Goal

Todo 6: research a browser mode where GridVibe can open a simple browser to test HTTP applications.

The practical interpretation is a new per-pane startup mode for local web app testing: a pane can show an HTTP URL while neighboring panes remain terminals, agents, or file explorers. This is different from the existing top-level GridVibe run mode where `python main.py` opens GridVibe itself in a normal browser.

## Current Repo Observations

- `README.md:227` documents two app run modes: `python main.py` for browser mode at `http://localhost:5050`, and `python webview_launcher.py` for native `pywebview` mode.
- `web/webview_launcher.py:572` starts Flask-SocketIO in a background thread, waits for `/api/health`, then creates the native launcher window. If `pywebview` is unavailable, `web/webview_launcher.py:606` falls back to `webbrowser.open(base_url)`.
- `web/webview_launcher.py:122` defines `GridVibeApi`, which exposes native window actions such as `open_session_window`, `focus_session_window`, folder selection, fullscreen, and restart. Adding browser testing as a pane does not require changing this native window registry.
- `sessions/manager.py:27` stores per-session metadata including `startup_mode`, and `sessions/manager.py:141` already accepts `startup_mode` in `create_session`.
- `web/api.py:369` normalizes startup modes. Today it allows `agent` globally, `explorer` only for Local Repo/WSL mode, otherwise `terminal`.
- `web/api.py:2208` treats `mode == "wsl"` and `startup_mode == "explorer"` as a special file explorer pane. `web/api.py:3024` marks explorer sessions connected without spawning a PTY task.
- `templates/index.html:3271` renders the launcher startup mode segmented control with `Initial Command`, `Agent`, and `File Explorer` for Local Repo mode.
- `templates/index.html:3774` builds the `/api/sessions` payload. Explorer panes use `initial_command: null` and `startup_mode: "explorer"` instead of launching a shell command.
- `templates/terminals.html:1128` has `isExplorerSession(session)`, and `templates/terminals.html:2148` builds each grid card as either an xterm terminal or an explorer pane. This is the best frontend pattern to copy for a browser pane.

## Implementation Possibilities

1. **Iframe browser pane inside the existing terminal grid**
   - Add `startup_mode: "browser"` and render an `<iframe>` in `templates/terminals.html`.
   - Store the target URL in an existing field at first, preferably `initial_command`, or add a clearer field later such as `browser_url`.
   - Tradeoffs: smallest implementation and no new dependency; works well for local dev apps that allow framing. Some apps will not load because of `X-Frame-Options`, CSP `frame-ancestors`, HTTPS/mixed-content rules, auth popups, or service worker/cookie isolation.

2. **Open the URL in the user's external browser**
   - Treat Browser startup mode as metadata plus a button/action that calls `window.open(url, uniqueName)` or uses Python `webbrowser.open`.
   - Tradeoffs: most compatible with real applications and devtools, but it is not embedded in the GridVibe workspace and is harder to keep visually tied to a pane.

3. **Dedicated native `pywebview` browser window**
   - Add a `GridVibeApi.open_browser_window(url)` method using `webview.create_window`.
   - Tradeoffs: closer to a real standalone browser-like window in native mode, but requires window lifecycle tracking similar to session windows and still has embedded-browser limitations. It also does not help normal `main.py` browser mode unless a separate browser fallback is added.

4. **Full automation/testing browser with Playwright**
   - Launch Chromium and optionally expose screenshots, logs, or test scripts.
   - Tradeoffs: powerful for automated testing, but heavy for a first browser mode. It introduces a large dependency, browser installation flow, process management, and a different UX from a simple browser pane.

## Recommended Safest/Simple Approach

Start with option 1: an embedded iframe pane for `http://127.0.0.1`, `http://localhost`, and user-entered HTTP(S) URLs, plus an `Open externally` button for sites that refuse to load in a frame.

This follows the existing file explorer architecture: represent the pane as a session, mark it connected without creating a PTY, and let `templates/terminals.html` render a non-terminal surface in the same grid. It keeps the blast radius small and avoids native-window changes, new packages, or browser automation complexity.

Use a narrow first version:

- Only enable Browser startup mode for Local Repo mode at first.
- Require an explicit URL field, defaulting to `http://127.0.0.1:3000`.
- Allow only `http://` and `https://` URLs.
- Do not proxy remote websites through Flask.
- Do not try to bypass frame protections.

## Implementation Outline

1. Extend startup-mode normalization in `web/api.py:369` to accept `"browser"` for Local Repo mode.
2. Extend terminal config normalization in `web/api.py:414` so browser panes persist in saved sessions. Initially use `initial_command` as the URL string to avoid a data-model migration.
3. Update `templates/index.html` launcher row rendering:
   - add a `Browser` segmented-control button beside `File Explorer` for Local Repo mode;
   - when Browser is selected, show a URL input instead of an initial command input;
   - collect `startup_mode: "browser"` and `initial_command: browserUrl`.
4. In `/api/sessions`, treat browser panes like explorer panes:
   - normalize the URL;
   - set a display host such as `Browser`;
   - skip `_connect_session`;
   - mark the session `CONNECTED` and broadcast status.
5. Add helpers similar to `_is_explorer_session`:
   - backend: `_is_browser_session(session)`;
   - frontend: `isBrowserSession(session)`.
6. Update `templates/terminals.html:2148` grid construction:
   - create a lightweight browser pane object;
   - render a URL bar, reload button, external-open button, and iframe;
   - hide terminal-only actions such as clear buffer and voice controls for browser panes.
7. Keep iframe sandboxing conservative. For local dev apps, likely allow scripts/forms/same-origin/popups, but keep navigation contained to the iframe unless the user clicks `Open externally`.
8. Add tests:
   - `_normalize_startup_mode("browser", "wsl") == "browser"`;
   - SSH mode rejects browser mode back to terminal for v1;
   - `/api/sessions` creates browser sessions without starting `_connect_session`;
   - terminals page includes browser pane rendering hooks.

## Risks / Tests To Consider

- **Frame blocking:** many real apps set `X-Frame-Options` or CSP `frame-ancestors`; the iframe pane should fail gracefully and offer `Open externally`.
- **Security:** do not proxy arbitrary URLs through Flask to bypass frame rules. If a proxy is ever added, it needs origin allowlists, cookie/header stripping, size limits, and SSRF protections.
- **URL validation:** reject empty URLs and schemes other than HTTP(S). Normalize `localhost:3000` to `http://localhost:3000` if desired.
- **Localhost inside remote sessions:** `localhost` means the user's GridVibe machine/browser, not an SSH target. Document this in UI copy or restrict v1 to Local Repo.
- **Terminal controls:** browser panes should not emit `terminal_input`, voice capture, clear, or replay-buffer actions.
- **Lifecycle:** browser panes are session records but have no backend process. Closing the pane/group should remove the session cleanly without PTY cleanup errors.
- **Layout:** iframes need fixed minimum dimensions and responsive sizing inside the existing grid to avoid overlap in 1, 2, 3, 4, 6, and 8 pane layouts.
- **Manual testing:** launch a simple local server such as `python -m http.server 8000`, open it in a Browser pane, test reload, external-open, tab switching, group closing, native `pywebview` mode, and normal `main.py` browser mode.
