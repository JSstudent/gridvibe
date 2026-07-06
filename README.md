# GridVibe

GridVibe is a browser-first workspace for launching and managing multiple SSH terminals, local shell panes, agent panes, and SSH/SFTP or local repository file explorer panes from one control surface. It runs in a normal browser or, when `pywebview` is installed, in a native desktop window on Windows and Linux.

[![CI](https://github.com/JSstudent/gridvibe/actions/workflows/ci.yml/badge.svg)](https://github.com/JSstudent/gridvibe/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

## Screenshots

### Launcher

![GridVibe launcher with terminal count, layout, connection, and per-terminal setup controls](docs/images/screenshots/launcher.png)

### Terminal Workspace

![GridVibe terminal workspace showing a four-pane SSH session group](docs/images/screenshots/workspace.png)

## Using the Workspace

### Terminal Buttons

The terminal workspace has global controls in the top bar, session controls in the tab bar, and per-pane controls in each terminal header.

Top bar controls:

- `Theme` cycles between system, light, and dark mode.
- `Refresh` reloads session status and redraws terminal panes.
- `Max surface` reduces workspace chrome and refits attached terminals so panes get more usable space.
- `Fullscreen` toggles the workspace into and out of fullscreen mode.
- `Settings` returns to the launcher page.

Session bar controls:

- Session tabs show each active session group with a positional number. Drag tabs to reorder them; GridVibe persists the order for the running app state.
- `Alt+1` through `Alt+9` switch to the matching numbered session tab when focus is not inside an editable field.
- `Sessions...` opens the active-workspace session menu for importing saved sessions, saving the current workspace, or saving it as a new preset.
- Each session tab has a close button for closing that session group.
- The chevron next to the session tabs hides or shows the top bar. GridVibe remembers this top-bar visibility preference in the browser.

Per-terminal controls:

- `↻` resets that terminal view and replays the recent output buffer. On file explorer panes, it manually reloads the current directory or the currently open file.
- `📁` switches an SSH or Local Repo terminal pane into a file explorer; `>_` switches a file explorer pane into a terminal opened at the currently selected explorer directory. Explorer panes keep their original root so parent-folder navigation remains available after switching back and forth.
- `⊞` splits a terminal pane by cloning its connection into a new pane in the same session group when the layout and session limit allow it.
- The close control removes one pane and expands the remaining split area. Explorer panes cannot be split.
- `🧹` clears the terminal display and purges its replay buffer.
- `🎤` starts or stops voice input for that terminal when voice input is enabled.

Pane sizing:

- Drag the visible divider between terminal panes to resize shared rows or columns. GridVibe refits xterm panes and resizes the backend PTY after the drag.
- Pane resizing keeps every visible pane above the minimum usable surface and is disabled on narrow mobile-width layouts.

### Saved Sessions

Use `Save Session` and `Import Session` on the launcher to manage reusable session presets before launch. Saved sessions can include SSH, WSL, PowerShell, cmd, Local Repo, agent, and file explorer startup choices.

From the terminal workspace, open `Sessions...` to:

- Import another saved session without returning to the launcher.
- Save the active workspace back to its current saved session.
- Use `Save Session as...` to create a new saved session from the current workspace.

Workspace saves preserve the current pane order, titles, startup modes, selected explorer directories, and split layout. Saved SSH passwords remain encrypted in `saved_sessions.json`.

### File Explorer Panes

In SSH and Local Repo modes, each pane can start as `Initial Command`, `Agent`, or `File Explorer`.

File explorer panes are read-only repository views. Local Repo explorers read from the GridVibe machine; SSH explorers browse the remote host over SFTP. They do not start a PTY, WSL shell, PowerShell process, or interactive SSH terminal. GridVibe validates every requested path against the selected root folder before listing or reading files.

Explorer panes support:

- Directory navigation with parent-folder navigation constrained to the selected root.
- Search/filter in the current directory view.
- Switching the pane into a regular terminal opened at the current explorer directory.
- Manual refresh from the pane header without continuous auto-refresh flicker.
- Folder/file icons, size and modified-time metadata, and per-pane light/dark explorer theme toggling.
- Click-to-open text files in a read-only editor-style viewer with line numbers, wrapped long lines, and per-pane `-`/`+` font-size zoom controls.
- Client-side find inside source, preview, and diff views, including `Ctrl+F`/`Cmd+F` focus, match counts, previous/next controls, `Enter`/`Shift+Enter` navigation, and clear.
- Markdown files with source-gutter chevrons for collapsing heading sections and sanitized rendered preview when Markdown rendering dependencies are installed.
- Lightweight syntax coloring for common source, config, log, JSON Lines, Dockerfile, and environment files.
- Size-limited previews. Binary files, directories, and paths outside the root are rejected.
- Local Repo explorers add read-only Git awareness when `git` is available. SSH explorers use the remote host's `git` command when available. Both support branch/dirty summary, per-entry status badges, directory dirty markers, and a bounded internal old/new Diff panel with added and removed line highlighting for changed tracked files.

File moving, editing, deleting, upload, staging, restoring, checkout, commit, pull, and push actions are not part of the current file explorer implementation.

### Voice and Sound Settings

GridVibe does not play remote audio from terminal sessions. Sound-related settings are for microphone capture used by voice input.

Open `App Settings` from the launcher gear button to choose:

- `Profile`: headset or laptop microphone capture tuning.
- `Microphone`: browser default input or a specific available input device.
- `Push-to-talk`: optional hold-to-record mode with a custom keybind.

Voice input requires optional voice dependencies. On Windows, `GridVibe.bat` checks the `.venv` and prompts to install them when missing. Manual setup:

```bash
python -m pip install --upgrade -r requirements-voice.txt
```

Browser mode is usually the most reliable mode for microphone permissions. Native `pywebview` mode depends on the embedded browser and OS microphone support.

### General Settings

Use the gear button on the launcher page to open `App Settings`. These settings are saved to `config.json` and are not stored inside saved session presets.

- `Enable voice input` shows or hides voice controls and enables the speech-to-text path.
- `Theme` sets system, light, or dark mode.
- `Session Window` sets whether newly opened session windows start in normal or max surface mode.
- `Voice Backend` selects `Vosk` or `faster-whisper`.
- `Language` sets the voice recognition language, such as `en-US`.
- `Vosk Model` sets the local Vosk model folder/name.
- `Whisper Model`, `Device`, and `Compute Type` configure faster-whisper. GPU mode requires a working NVIDIA CUDA setup; use CPU if startup fails.
- `Profile`, `Microphone`, and `Push-to-talk` configure global microphone capture preferences for terminal voice input.

### Agent CLI Detection

GridVibe does not bundle agent CLIs such as Codex, Claude Code, OpenCode, Kilo, or GitHub Copilot CLI. The `Agent` selector checks whether the selected command is available in the target environment:

- SSH sessions are checked on the remote host.
- WSL terminals are checked inside the selected WSL distribution.
- PowerShell and cmd terminals are checked through the Windows environment that launched GridVibe.

If every agent shows `Missing`, confirm the CLI is installed and visible on `PATH` from a fresh terminal. For npm-installed agents on Windows, the global npm shim folder must usually be on your User PATH:

```powershell
npm prefix -g
Get-Command codex, claude, opencode, kilo, copilot -ErrorAction SilentlyContinue
```

The npm prefix is commonly:

```text
C:\Users\<you>\AppData\Roaming\npm
```

On Linux, npm global binaries usually live under the global prefix's `bin` directory. Confirm the path with:

```bash
npm prefix -g
command -v codex claude opencode kilo copilot
```

Common locations include `/usr/local/bin`, `~/.npm-global/bin`, or `<npm prefix -g>/bin`.

After changing PATH, restart your shell, GridVibe, and any native window launchers so they inherit the updated environment.

## Features

- Multi-session launcher with 1, 2, 3, 4, 6, or 8 panes
- SSH, WSL, PowerShell, cmd, and local repository modes
- Per-pane startup modes for normal commands, agent CLIs, and file explorer panes
- Saved launcher and active-workspace presets with encrypted SSH passwords
- Session groups with numbered closable tabs, `Alt+1` through `Alt+9` tab switching, import/save actions, drag-to-reorder persistence, collapsible top bar, and max surface mode
- xterm.js terminal panes with resize, refresh, clear, replay buffer, fullscreen, and drag-resizable dynamic split-pane support
- Local and SSH read-only file explorer panes with directory search, text/Markdown preview, syntax highlighting, per-pane editor font zoom, client-side file/diff search, and read-only Git status/diff awareness
- Optional resizable native desktop window through `pywebview`
- Optional offline voice input through Vosk or faster-whisper
- Theme support for system, light, and dark modes

## Quick Start

### Windows One-Click Launcher

For the easiest Windows start, open:

```powershell
.\START_HERE\Start GridVibe.bat
```

That visible launcher calls the working root launcher, `GridVibe.bat`.

### Windows Install

Use the included launcher for the easiest Windows setup:

```powershell
.\GridVibe.bat
```

`GridVibe.bat` creates or repairs `.venv`, upgrades installer tooling, upgrades runtime and desktop dependencies, verifies native dependency imports, checks optional voice dependencies, prompts to install them when missing, then launches the native window when possible.

Manual Windows setup:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --upgrade -r requirements.txt
python main.py --host 127.0.0.1
```

Open `http://localhost:5050`.

Install optional desktop-window support with:

```powershell
python -m pip install --upgrade -r requirements-desktop.txt
python webview_launcher.py
```

### Linux Install

Install Python 3.10+ and the venv package for your distro first. For Debian or Ubuntu:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

Then run the Linux launcher from the project root:

```bash
chmod +x GridVibe.sh
./GridVibe.sh
```

`GridVibe.sh` creates or repairs `.venv`, installs core dependencies, then asks
whether to start a native window, browser mode, or quit. Browser mode opens
`http://localhost:5050` in your default browser and keeps the server attached to
the launcher process. Native mode also installs `requirements-desktop.txt` and
requires a working `pywebview` backend.

Manual browser setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --upgrade -r requirements.txt
python webview_launcher.py --mode browser
```

Optional native desktop-window support on Linux uses `pywebview`. The desktop
requirements install the Qt backend because it works inside a normal virtualenv:

```bash
python -m pip install --upgrade -r requirements-desktop.txt
python webview_launcher.py --mode native
```

If you see `You must have either QT or GTK with Python extensions installed in
order to use pywebview`, refresh the desktop dependencies in the active venv:

```bash
python -m pip install --upgrade -r requirements-desktop.txt
```

GTK is also supported by pywebview, but on Ubuntu/Debian it depends on distro
PyGObject/WebKit packages such as `python3-gi`, `python3-gi-cairo`,
`gir1.2-gtk-3.0`, and `gir1.2-webkit2-4.1`; those packages must be visible to
the Python environment running GridVibe.

If the native window starts with Qt but logs Mesa/VMware rendering warnings such
as `MESA: error: ZINK: failed to choose pdev` or `VMware: No 3D enabled`, but
then freezes, use browser mode with `python webview_launcher.py --mode browser`. The
native launcher still requests pywebview's Qt backend directly, but GridVibe no
longer forces QtWebEngine GPU/software-rendering flags by default because those
flags can freeze some Qt builds. To opt into that fallback for testing, launch
with `GRIDVIBE_QTWEBENGINE_GPU_FALLBACK=1`.

When launched from an interactive Linux shell, GridVibe also ignores terminal
job-control stop signals so the native window is not suspended with a shell
message like `[1]+  Stopped python webview_launcher.py`.

### Manual Cross-Platform Setup

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install --upgrade -r requirements.txt
python main.py
```

Open `http://localhost:5050`.

On Windows, you can also run `GridVibe.bat`.

## Optional Dependencies

Native desktop window support:

```bash
python -m pip install --upgrade -r requirements-desktop.txt
```

Offline voice input support:

```bash
python -m pip install --upgrade -r requirements-voice.txt
```

On Windows, `GridVibe.bat` performs this check for the project `.venv` and can install the voice packages during startup.

Development tools:

```bash
python -m pip install --upgrade -r requirements-dev.txt
```

## Run Modes

```bash
python main.py                  # browser mode on http://localhost:5050
python main.py --host 0.0.0.0   # opt in to binding on all network interfaces
python main.py --port 8080      # custom port
python webview_launcher.py      # auto mode: native window with browser fallback
python webview_launcher.py --mode browser
python webview_launcher.py --mode native
```

## How It Works

- `main.py` starts Flask + Socket.IO and configures rotating logs.
- `web/api.py` contains HTTP routes, Socket.IO handlers, saved-session handling, SSH/local-shell logic, local and SFTP file explorer APIs, app settings, and voice integration.
- `sessions/manager.py` tracks in-memory session and session-group state.
- `templates/` contains the launcher and terminal workspace pages.

Live terminal sessions and session groups are in memory. If the Python process exits, running sessions end.

## Configuration

Runtime settings load from `config.json` when present, otherwise from `default_config.json`. `config.json` is intentionally ignored by git.

A minimal local override can look like this:

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 5050,
    "debug": false
  },
  "security": {
    "cors_origins": ["*"]
  },
  "appearance": {
    "theme": "system"
  },
  "workspace": {
    "surface_mode": "normal"
  },
  "voice_input": {
    "enabled": false,
    "engine": "whisper"
  }
}
```

GridVibe generates a Flask session signing key at startup unless `GRIDVIBE_SECRET_KEY`, `SECRET_KEY`, or a non-empty `security.secret_key` value is supplied through local config.

## Security Considerations

GridVibe is designed as a local desktop/browser tool, not a public web service. By default it binds to `127.0.0.1`.

- There is no built-in authentication or multi-user isolation.
- Flask-SocketIO is run with Werkzeug for local usage; do not expose it directly to the internet.
- Socket.IO defaults to wildcard CORS for local browser/native-window usage. Restrict `security.cors_origins` if you bind outside localhost.
- Paramiko currently uses `AutoAddPolicy`, which accepts unknown SSH host keys on first use.
- Saved SSH passwords are encrypted with Fernet before writing to `saved_sessions.json`.
- The Fernet key is stored in `.encryption_key`; Unix-like systems use `0600` permissions, while Windows users should rely on normal profile/account isolation or add stricter ACLs if needed.

See `SECURITY.md` for reporting and scope details.

## Voice Input

Voice input is optional. On Windows, `GridVibe.bat` prompts to install `requirements-voice.txt` when the voice packages are missing; for manual setup, install that file before enabling voice.

Supported engines:

- `whisper`: local faster-whisper inference inside the app process
- `vosk`: on-demand local WebSocket service started from `services/vosk_service.py`

The full voice implementation contract is in `docs/voice_guideline.md`.

## Development

`make` targets create `.venv` and install development dependencies before running checks.

```bash
make test
make lint
make fix
make check
```

On Windows without `make`:

```bash
python tests/run_tests.py
python -m ruff check .
```

## Project Layout

```text
gridvibe/
├── main.py
├── web/
├── sessions/
├── services/
├── templates/
├── tests/
├── docs/
├── default_config.json
├── requirements.txt
├── requirements-desktop.txt
├── requirements-voice.txt
├── requirements-dev.txt
├── pyproject.toml
└── GridVibe.bat
```

## Documentation

- `docs/logging_guide.md`
- `docs/voice_guideline.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CHANGELOG.md`

## Local Files

These are created or used at runtime and should not be committed:

| File | Purpose |
| --- | --- |
| `config.json` | Local runtime configuration override |
| `saved_sessions.json` | Saved launcher presets |
| `.encryption_key` | Fernet key used for password encryption |
| `logs/gridvibe.log` | Main rotating log file |

## License

MIT. See `LICENSE`.
