# GridVibe Launch Mode Selection Proposal

## Summary

GridVibe currently has two startup paths:

- Browser mode: `python main.py`, then the user opens `http://localhost:5050`.
- Native mode: `python webview_launcher.py`, which starts the Flask/Socket.IO server and then opens a `pywebview` window when possible.

The Windows click path `START_HERE\Start GridVibe.bat` calls `GridVibe.bat`, and `GridVibe.bat` currently launches `webview_launcher.py` unconditionally. Because `webview_launcher.py` falls back to the system browser when `pywebview` is unavailable or missing a Linux backend, the user can see native-window startup behavior and browser-mode behavior from the same entry point.

The safest fix is to ask for the launch mode before starting the server, then run exactly one mode-specific path.

## Goals

- Let users choose native `pywebview` mode or browser mode at startup.
- Support the same choice on Windows and Ubuntu/Linux.
- Avoid starting a native window and browser fallback in the same explicit mode.
- Keep browser mode independent of `pywebview` backend quirks.
- Keep direct CLI usage compatible for existing users.

## Non-Goals

- Do not redesign the web UI.
- Do not make browser close detection a requirement. Normal browser mode can keep the local server alive until its owning console/process is closed.
- Do not add new heavyweight GUI dependencies just to show a startup choice.
- Do not make `pywebview` the hidden owner of browser mode.

## Current Behavior

### Windows

`START_HERE\Start GridVibe.bat` is a thin wrapper:

```bat
set "PROJECT_ROOT=%~dp0.."
call "%PROJECT_ROOT%\GridVibe.bat"
```

`GridVibe.bat` then:

1. Finds Python.
2. Creates or repairs `.venv`.
3. Installs `requirements.txt`.
4. Installs `requirements-desktop.txt`.
5. Checks optional voice dependencies and prompts to install them.
6. Starts a minimized console running:

```bat
"%VENV_PYTHON%" "%PROJECT_DIR%\webview_launcher.py"
```

That means the clickable Windows path always enters the native launcher first.

### Ubuntu/Linux

There is no equivalent first-class clickable shell launcher today. The README documents:

```bash
python main.py
```

for browser mode, and:

```bash
python webview_launcher.py
```

for native `pywebview` mode.

The native launcher prefers the Qt backend on Linux. If `pywebview` is present but no Qt/GTK backend can load, it logs help and falls back to the system browser.

## Recommended Design

Add explicit launch modes to the Python launcher layer, then have each platform wrapper ask the user which mode to run.

### Python Contract

Extend `web/webview_launcher.py` with a mode argument:

```bash
python webview_launcher.py --mode auto
python webview_launcher.py --mode native
python webview_launcher.py --mode browser
```

Mode behavior:

| Mode | Behavior |
| --- | --- |
| `auto` | Preserve today's behavior: try `pywebview`, fall back to browser if unavailable. This keeps direct CLI usage compatible. |
| `native` | Strict native mode. Start the server and open `pywebview`. If `pywebview` or its backend fails, show/log the error and exit without opening a browser automatically. |
| `browser` | Strict browser mode. Start the server, wait for `/api/health`, open the default browser, and never create or start a `pywebview` window. |

The Windows Desktop choice uses `--mode auto` to retain native-first startup with browser fallback. The Browser choice uses `--mode browser` so it never creates a native window.

This keeps the familiar desktop fallback while making an explicit Browser selection reliably browser-only.

### Server Ownership

Each selected mode should be owned by one Python process:

- Desktop/auto mode owns the server thread and the `pywebview` lifecycle, or retains the server when it falls back to the browser.
- Browser mode owns the server thread and opens the system browser.

Do not implement browser mode by starting `main.py` in one process and `webview_launcher.py` in another. That creates port conflicts, duplicate shutdown paths, and unclear ownership of voice services and sessions.

### Browser Mode Implementation Detail

Browser mode can reuse the existing internal helpers from `web/webview_launcher.py`:

- `_run_server(...)`
- `_wait_for_server(...)`
- `_open_browser_fallback(...)`, renamed to something mode-neutral such as `_open_browser_mode(...)`

The browser-mode path should not call:

- `webview.create_window(...)`
- `webview.start(...)`
- `_patch_webview2_permissions()`
- `_set_webview2_media_env()`
- `_set_linux_qtwebengine_env()`

Importing `webview` opportunistically is acceptable for backwards compatibility, but browser mode should not depend on it.

## Windows Options

### Option W1: Console `choice` Prompt

Add a `choice` prompt to `GridVibe.bat` after core dependency setup and before desktop dependency installation:

```bat
choice /C DBQ /N /M "Select [D/B/Q]: "
```

Behavior:

- `D`: install `requirements-desktop.txt`, then run `webview_launcher.py --mode auto` for a desktop window with browser fallback.
- `B`: skip desktop dependency installation, then run `webview_launcher.py --mode browser`; the Launcher Setup header shows a Close button that stops the owning Python process.
- `Q`: exit.

Pros:

- No new dependencies.
- Works from `cmd.exe`, Explorer-launched batch files, and locked-down Windows machines.
- Easy to test.

Cons:

- It is a console prompt, not a true popup.

### Option W2: PowerShell Popup With Console Fallback

Use a small PowerShell Windows Forms dialog from `GridVibe.bat`, with a `choice` fallback if PowerShell UI creation fails.

Dialog buttons:

- Desktop Window
- Browser
- Quit

Pros:

- Matches the requested popup behavior on Windows.
- Can fall back to `choice` without blocking users on machines where UI automation or Windows Forms is restricted.

Cons:

- Slightly more script complexity.
- Needs careful quoting between batch and PowerShell.

### Windows Recommendation

Use W1: the console `choice` prompt with Desktop, Browser, and Quit options. It keeps the launcher usable from Explorer, plain consoles, and restricted Windows machines without adding a PowerShell UI dependency. Desktop retains browser fallback; Browser opens only the system default browser.

The Close button is enabled only for the explicit Browser choice. It is not shown in Desktop mode, including when Desktop mode falls back to a browser.

## Ubuntu/Linux Options

### Option L1: Add `GridVibe.sh` With Terminal Prompt

Add a shell launcher:

```bash
./GridVibe.sh
```

Prompt:

```text
Start GridVibe:
1) Native window
2) Browser
3) Quit
```

Behavior:

- Native: create `.venv` if needed, install `requirements.txt` and `requirements-desktop.txt`, then run `python webview_launcher.py --mode native`.
- Browser: create `.venv` if needed, install `requirements.txt`, then run `python webview_launcher.py --mode browser`.
- Quit: exit.

Pros:

- No new distro dependency.
- Works over SSH, terminal emulators, WSL, and minimal Ubuntu installs.
- Keeps browser and native paths explicit.

Cons:

- Not a desktop popup.

### Option L2: `zenity` Popup With Terminal Fallback

If `zenity` is installed and `$DISPLAY` or `$WAYLAND_DISPLAY` is available, show a GTK selection dialog. Otherwise fall back to the terminal prompt from L1.

Pros:

- Gives Ubuntu desktop users a popup.
- Still works on headless or minimal systems.

Cons:

- `zenity` is not guaranteed to be installed.
- Desktop-file launching and terminal ownership vary across Ubuntu desktops.

### Option L3: Add a `.desktop` File

Add a desktop entry that runs `GridVibe.sh`.

Pros:

- Better for packaged releases.
- Can appear as an application launcher on Ubuntu desktops.

Cons:

- Requires install/location assumptions.
- Less useful for source checkouts unless the user installs the desktop file.

### Ubuntu/Linux Recommendation

Use L1: a terminal prompt in `GridVibe.sh`.

The Linux launcher should not require `zenity`, Qt, GTK, or `pywebview` just to ask the mode question. Those dependencies are exactly the class of startup issues this mode selection is meant to avoid.

The `zenity` popup from L2 can be added later if desktop-launch polish becomes important, but the baseline Ubuntu/Linux path should stay a single terminal-friendly script.

## Dependency Flow

Recommended ordering:

1. Locate Python.
2. Create or repair `.venv`.
3. Install `requirements.txt`.
4. Ask for launch mode.
5. If Desktop mode, install `requirements-desktop.txt`.
6. Check optional voice dependencies.
7. Launch the selected mode.

This avoids installing `pywebview` and Qt packages when the user explicitly chooses browser mode.

Voice dependency prompting can stay shared because voice can be used in both modes. Browser mode remains the more reliable microphone path.

## Failure Behavior

Strict native mode:

- If `pywebview` is missing or the backend cannot load, print/log a clear error.
- Do not open a browser automatically.
- Tell the user to rerun and choose Browser, or install/repair desktop dependencies.

Strict browser mode:

- If the server fails to start or `/api/health` does not respond, print/log a clear error.
- Do not attempt to create a native window.

Auto mode:

- Keep native-first browser fallback behavior for direct CLI compatibility and the Windows Desktop choice.

## Implementation Sketch

### Python

1. Add `--mode {auto,native,browser}` to `web/webview_launcher.py`.
2. Split current `main()` into small mode functions:

```text
main()
  parse args/config/logging
  start server thread
  wait for health
  if mode == browser:
      open browser and join server
  else:
      start native window
```

3. Add a boolean or enum to native startup so fallback happens only in `auto` mode.
4. Update tests for:
   - browser mode never calls `webview.create_window`
   - native mode does not call browser fallback when backend is missing
   - auto mode preserves fallback

### Windows

1. Update `GridVibe.bat` to ask for mode before installing desktop dependencies.
2. For Desktop mode, run:

```bat
start "GridVibe" /min cmd /c ""%VENV_PYTHON%" "%PROJECT_DIR%\webview_launcher.py" --mode auto"
```

3. For browser mode, run:

```bat
start "GridVibe Browser Server" /min cmd /c ""%VENV_PYTHON%" "%PROJECT_DIR%\webview_launcher.py" --mode browser"
```

4. Keep `START_HERE\Start GridVibe.bat` as the thin visible wrapper.

### Ubuntu/Linux

1. Add `GridVibe.sh`.
2. Optionally add `START_HERE/Start GridVibe.sh` as a thin visible wrapper.
3. Use POSIX shell where practical.
4. Prefer terminal prompt first; optionally detect `zenity` later.
5. Run:

```bash
"$VENV_PYTHON" "$PROJECT_DIR/webview_launcher.py" --mode native
```

or:

```bash
"$VENV_PYTHON" "$PROJECT_DIR/webview_launcher.py" --mode browser
```

## Preferred Plan

1. Add strict `--mode` support to `web/webview_launcher.py`.
2. Update tests around fallback behavior and browser-mode isolation.
3. Update `GridVibe.bat` with the W1 console `choice` prompt.
4. Add `GridVibe.sh` with the L1 terminal prompt.
5. Update README startup instructions.

This keeps the launch architecture simple: one user choice, one owning process, one server, and only the requested UI surface or surfaces.
