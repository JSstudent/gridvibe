# GridVibe – Logging Reference

## Overview

GridVibe writes log output to two destinations simultaneously:

| Destination | Format | Notes |
|---|---|---|
| **stdout** | `%(asctime)s  %(levelname)-8s  %(name)s  %(message)s` | Visible in the terminal that launched the app |
| **`logs/gridvibe.log`** | same | Rotated automatically; see below |

Logging is initialised in `setup_logging()` inside `main.py`.

---

## Log Level

| Launch command | Level |
|---|---|
| `python main.py` | `INFO` |
| `python main.py --debug` | `DEBUG` |

The level applies to the root logger and therefore to every named logger in the application.

---

## Log Rotation

The file handler uses Python's `RotatingFileHandler`:

| Setting | Value |
|---|---|
| Max file size | **2 MB** (`MAX_LOG_SIZE`) |
| Backup count | **10** (`MAX_LOG_BACKUPS`) |
| Naming scheme | `gridvibe.log`, `gridvibe.log.1` … `gridvibe.log.10` |

When `gridvibe.log` reaches 2 MB it is renamed to `.1`, the previous `.1` becomes `.2`, and so on. Once 10 backups exist the oldest is overwritten. Total worst-case disk usage is **≈ 22 MB**.

---

## Noise Suppression

The `werkzeug` logger emits an `INFO` line for every HTTP request. Two families of endpoints are polled by the frontend every few seconds and would flood the log with identical entries:

- `GET /api/sessions` (and `?group=…` variants)
- `GET /api/session-groups`

The `_SuppressPollLogs` filter (attached to `logging.getLogger("werkzeug")` inside `setup_logging`) drops these lines when the response status is `2xx`. All other werkzeug output — errors, non-polling routes — is logged normally.

The filter regex:

```python
_POLL_RE = re.compile(r'"GET /api/(sessions|session-groups)(\?[^ ]*)? HTTP/[\d.]+" 2\d\d')
```

To stop suppressing a route, remove it from the alternation group in `_POLL_RE`.

---

## Log Directory

```
logs/
├── gridvibe.log        ← current file
├── gridvibe.log.1      ← previous
├── gridvibe.log.2
…
└── gridvibe.log.10     ← oldest kept
```

The `logs/` directory is created automatically on first run and is **gitignored**.

---

## Log Format

```
2026-04-17 08:00:01,234  INFO      web.api  Session abc12345 connected
└── asctime ──────────┘  └level─┘  └name─┘  └── message ────────────┘
```

- **asctime** — local time with milliseconds
- **levelname** — left-aligned, padded to 8 characters
- **name** — the Python logger name (usually the module, e.g. `web.api`, `sessions.manager`, `werkzeug`)
- **message** — the log message

---

## Adding Log Statements

Use the standard named-logger pattern — never log directly on the root logger:

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("Detailed trace: %s", value)
logger.info("Session %s connected", session_id)
logger.warning("Config key missing, using default")
logger.error("SSH connect failed: %s", exc)
```

`__name__` resolves to the module path (e.g. `web.api`, `sessions.manager`), which makes it easy to filter log output by component.

---

## Changing Rotation Settings

Edit the constants at the top of `main.py`:

```python
MAX_LOG_SIZE    = 2 * 1024 * 1024   # bytes per file  (currently 2 MB)
MAX_LOG_BACKUPS = 10                 # number of rotated copies kept
```

No other files need to change — `setup_logging()` reads these constants directly.

