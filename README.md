# GridVibe

GridVibe is a browser-first terminal workspace for launching and managing multiple SSH or local shell panes from one control surface. It runs in a normal browser or, when `pywebview` is installed, in a native desktop window on Windows.

[![CI](https://github.com/JSstudent/gridvibe/actions/workflows/ci.yml/badge.svg)](https://github.com/JSstudent/gridvibe/actions/workflows/ci.yml)
![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)

## Features

- Multi-session launcher with 1, 2, 3, 4, 6, or 8 panes
- SSH and local shell modes
- Saved launcher presets with encrypted SSH passwords
- Session groups with tabs and drag-to-reorder persistence
- xterm.js terminal panes with resize, refresh, clear, and replay buffer support
- Optional native desktop window through `pywebview`
- Optional offline voice input through Vosk or faster-whisper
- Theme support for system, light, and dark modes

## Quick Start

```bash
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
python main.py
```

Open `http://localhost:5050`.

On Windows, you can also run `GridVibe.bat`. It creates or repairs `.venv`, installs runtime and desktop dependencies, then launches the native window when possible.

## Optional Dependencies

Native desktop window support:

```bash
pip install -r requirements-desktop.txt
```

Offline voice input support:

```bash
pip install -r requirements-voice.txt
```

Development tools:

```bash
pip install -r requirements-dev.txt
```

## Run Modes

```bash
python main.py                  # browser mode on http://localhost:5050
python main.py --host 127.0.0.1 # bind to localhost only
python main.py --port 8080      # custom port
python webview_launcher.py      # native window when pywebview is installed
```

## How It Works

- `main.py` starts Flask + Socket.IO and configures rotating logs.
- `web/api.py` contains HTTP routes, Socket.IO handlers, saved-session handling, SSH/local-shell logic, app settings, and voice integration.
- `sessions/manager.py` tracks in-memory session and session-group state.
- `templates/` contains the launcher and terminal workspace pages.

Live terminal sessions are in memory. If the Python process exits, running sessions end.

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
  "voice_input": {
    "enabled": false,
    "engine": "whisper"
  }
}
```

GridVibe generates a Flask session signing key at startup unless `GRIDVIBE_SECRET_KEY`, `SECRET_KEY`, or a non-empty `security.secret_key` value is supplied through local config.

## Security Considerations

GridVibe is designed as a local desktop/browser tool, not a public web service.

- There is no built-in authentication or multi-user isolation.
- Flask-SocketIO is run with Werkzeug for local usage; do not expose it directly to the internet.
- Socket.IO defaults to wildcard CORS for local browser/native-window usage. Restrict `security.cors_origins` if you bind outside localhost.
- Paramiko currently uses `AutoAddPolicy`, which accepts unknown SSH host keys on first use.
- Saved SSH passwords are encrypted with Fernet before writing to `saved_sessions.json`.
- The Fernet key is stored in `.encryption_key`; Unix-like systems use `0600` permissions, while Windows users should rely on normal profile/account isolation or add stricter ACLs if needed.

See `SECURITY.md` for reporting and scope details.

## Voice Input

Voice input is optional. Install `requirements-voice.txt` before enabling it.

Supported engines:

- `whisper`: local faster-whisper inference inside the app process
- `vosk`: on-demand local WebSocket service started from `services/vosk_service.py`

The full voice implementation contract is in `docs/voice_guideline.md`.

## Development

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

