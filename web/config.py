"""Configuration loading, persistence, and config-backed runtime settings.

Extracted from web/api.py (deep-dive finding 6.2). `load_config`/`save_config`
handle the two-file merge (config.json overriding default_config.json), and
`RuntimeConfig` holds the settings that the rest of the app reads at runtime;
call `runtime_config.refresh()` after persisting a config change.

`config.json` is GridVibe's third durable JSON store, alongside
`runtime_state.json` and `saved_sessions.json`, and it takes the same four
mechanics from `web/state_files.py`: a cross-process sidecar lock over the
complete replace, a unique same-directory temp file that is fsynced before
`os.replace`, a `<file>.bak` taken on every commit, and quarantine of a corrupt
file rather than laundering it into defaults that the next save would make
permanent. Schema and merge policy stay here; only the mechanics are shared.
"""

import json
import logging
import os
import threading
from typing import Any, Dict, Optional, Tuple

from web.paths import BASE_DIR
from web.state_files import (
    CrossProcessFileLock,
    StateFilePersistenceError,
    quarantine_state_file,
    read_backup_json,
    write_json_atomically,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "default_config.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
_config_lock = threading.RLock()

HOST_KEY_POLICY_OPTIONS = ("auto-add", "known-hosts", "strict")

#: Human label used in quarantine/recovery log lines for this store.
_QUARANTINE_LABEL = "configuration"


class ConfigPersistenceError(StateFilePersistenceError):
    """Raised when an intended configuration change did not reach the disk.

    A caller must treat this as "not saved" rather than echoing the new
    settings back — the same contract the other two stores use, which is why it
    subclasses the shared base.
    """


class _CrossProcessConfigLock(CrossProcessFileLock):
    """Exclusive OS-level lock over one ``config.json``."""

    error_type = ConfigPersistenceError
    label = _QUARANTINE_LABEL

# Bounds for launcher-editable terminal settings (ISSUE-2026-029). The App
# Settings write path and RuntimeConfig.refresh() share these so a hand-edited
# config.json and an API update normalize identically.
TERMINAL_FONT_SIZE_MIN = 6
TERMINAL_FONT_SIZE_MAX = 48
TERMINAL_FONT_FAMILY_MAX_LENGTH = 160
MAX_SESSIONS_MIN = 1
MAX_SESSIONS_MAX = 16
DEFAULT_TERMINAL_FONT_FAMILY = "Consolas, Monaco, 'Courier New', monospace"

# Bounds for the workspace autosave interval (10.5 hardening). The App
# Settings write path and RuntimeConfig.refresh() share these so a hand-edited
# config.json and an API update normalize identically.
AUTOSAVE_INTERVAL_MINUTES_MIN = 1
AUTOSAVE_INTERVAL_MINUTES_MAX = 15
AUTOSAVE_INTERVAL_MINUTES_DEFAULT = 5

# Bounds for the explorer repository search (backend-only caps; the frontend
# learns the effective limits from each response's `truncated` block).
EXPLORER_SEARCH_MAX_FILES_MIN = 1
EXPLORER_SEARCH_MAX_FILES_MAX = 20000
EXPLORER_SEARCH_MAX_FILES_DEFAULT = 2000
EXPLORER_SEARCH_MAX_MATCHES_MIN = 1
EXPLORER_SEARCH_MAX_MATCHES_MAX = 100000
EXPLORER_SEARCH_MAX_MATCHES_DEFAULT = 5000
EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_MIN = 1
EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_MAX = 5000
EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_DEFAULT = 200
EXPLORER_SEARCH_MAX_FILE_BYTES_MIN = 4096
EXPLORER_SEARCH_MAX_FILE_BYTES_MAX = 100 * 1024 * 1024
EXPLORER_SEARCH_MAX_FILE_BYTES_DEFAULT = 2 * 1024 * 1024
EXPLORER_SEARCH_TIMEOUT_SECONDS_MIN = 1
EXPLORER_SEARCH_TIMEOUT_SECONDS_MAX = 60
EXPLORER_SEARCH_TIMEOUT_SECONDS_DEFAULT = 20

WHISPER_MODEL_OPTIONS = {
    "tiny.en",
    "tiny",
    "base.en",
    "base",
    "small.en",
    "small",
    "medium.en",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large",
    "distil-large-v2",
    "distil-medium.en",
    "distil-small.en",
    "distil-large-v3",
    "distil-large-v3.5",
    "large-v3-turbo",
    "turbo",
}


def _load_json_file(path: str) -> Dict[str, Any]:
    """Load one JSON object from disk."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge dictionaries while replacing scalar values and lists."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from file, falling back to default_config.json."""
    target_path = config_path or CONFIG_PATH

    with _config_lock:
        default_config: Dict[str, Any] = {}
        if target_path != DEFAULT_CONFIG_PATH and os.path.exists(DEFAULT_CONFIG_PATH):
            try:
                default_config = _load_json_file(DEFAULT_CONFIG_PATH)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Failed to load default configuration from %s: %s",
                    DEFAULT_CONFIG_PATH,
                    exc,
                )
                logger.debug("Default configuration load failure details", exc_info=True)

        if os.path.exists(target_path):
            try:
                loaded = _load_json_file(target_path)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Failed to load configuration from %s: %s; using default configuration",
                    target_path,
                    exc,
                )
                logger.debug("Configuration load failure details", exc_info=True)
                loaded = _recover_config(target_path, exc)
                if loaded is None:
                    return default_config
            return _merge_dicts(default_config, loaded) if default_config else loaded

        return default_config


def _recover_config(target_path: str, exc: Exception) -> Optional[Dict[str, Any]]:
    """Return the last-good ``config.json`` after an unreadable primary, else None.

    Corrupt *content* is quarantined first, so the evidence survives and the
    next `save_config` cannot overwrite it — without that, a truncated file
    silently becomes "defaults", and the first App Settings save afterwards
    makes the loss permanent. An ``OSError`` is not quarantined: the bytes may
    be perfectly good and only momentarily unreadable (a permission or
    antivirus hold), and moving the file aside would discard settings this
    process simply could not see.
    """
    if isinstance(exc, ValueError):  # json.JSONDecodeError
        quarantine_state_file(target_path, f"unreadable: {exc}", label=_QUARANTINE_LABEL)
    payload = read_backup_json(target_path, label=_QUARANTINE_LABEL)
    if not isinstance(payload, dict):
        return None
    logger.warning("Recovered the configuration from the last-good backup")
    return payload


def save_config(config: Dict[str, Any], config_path: Optional[str] = None):
    """Save configuration to file, durably.

    Raises `ConfigPersistenceError` when the intended revision did not reach
    the disk. The in-process `_config_lock` orders threads (callers hold it
    across their own read-modify-write); the sidecar lock orders whole
    replaces across GridVibe processes, and the atomic writer takes the
    `<file>.bak` `load_config` recovers from.
    """
    target_path = config_path or CONFIG_PATH
    with _config_lock, _CrossProcessConfigLock(target_path):
        write_json_atomically(
            config,
            target_path,
            error_type=ConfigPersistenceError,
            failure_message="Could not persist the configuration",
        )


def resolve_server_settings(
    config: Dict[str, Any],
    *,
    host: Optional[str] = None,
    port: Optional[int] = None,
    debug: Optional[bool] = None,
) -> Tuple[str, int, bool]:
    """Resolve the server host/port/debug settings for an entry point.

    Explicit CLI flags (non-None arguments) beat `config.json` values, which
    beat the built-in defaults — the conventional precedence (finding 4.7).
    Entry points pass ``None`` for flags the user did not supply.
    """
    server_config = config.get("server", {}) if isinstance(config, dict) else {}
    if not isinstance(server_config, dict):
        server_config = {}
    resolved_host = host if host is not None else server_config.get("host", "127.0.0.1")
    resolved_port = port if port is not None else server_config.get("port", 5050)
    resolved_debug = debug if debug is not None else server_config.get("debug", False)
    return str(resolved_host), int(resolved_port), bool(resolved_debug)


def _normalize_surface_mode(value: Any, default: str = "normal") -> str:
    """Normalize workspace chrome density for terminal session windows."""
    normalized = str(value or "").strip().lower()
    if normalized in {"normal", "max"}:
        return normalized
    return default if default in {"normal", "max"} else "normal"


def _clamped_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    """Parse an integer config value into [minimum, maximum], else default."""
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        return default
    return max(minimum, min(maximum, parsed))


class RuntimeConfig:
    """Config-backed runtime settings shared across the app.

    One instance (`runtime_config` below) replaces the former web.api module
    globals; tests patch attributes on that instance.
    """

    def __init__(self):
        self.app_config: Dict[str, Any] = {}
        self.ssh_config: Dict[str, Any] = {}
        self.ssh_host_key_policy = "auto-add"
        self.max_sessions = 4
        self.terminal_font_size = 14
        self.terminal_font_family = "Consolas, Monaco, 'Courier New', monospace"
        self.app_theme = "system"
        self.app_surface_mode = "normal"
        self.multi_workspace_enabled = False
        self.workspace_autosave_interval_minutes = AUTOSAVE_INTERVAL_MINUTES_DEFAULT
        self.explorer_search_max_files = EXPLORER_SEARCH_MAX_FILES_DEFAULT
        self.explorer_search_max_matches = EXPLORER_SEARCH_MAX_MATCHES_DEFAULT
        self.explorer_search_max_matches_per_file = EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_DEFAULT
        self.explorer_search_max_file_bytes = EXPLORER_SEARCH_MAX_FILE_BYTES_DEFAULT
        self.explorer_search_timeout_seconds = EXPLORER_SEARCH_TIMEOUT_SECONDS_DEFAULT
        self.voice_enabled = True
        self.voice_engine = "vosk"
        self.vosk_service_url = "ws://localhost:2700"
        self.vosk_model = "vosk-model-en-us-0.22"
        self.whisper_model = "base"
        self.whisper_device = "cpu"
        self.whisper_compute_type = "int8"
        self.voice_language = "en-US"
        self.vosk_startup_timeout_seconds = 180
        self.refresh()

    def refresh(self):
        """Reload the config-backed settings from disk."""
        self.app_config = load_config()
        self.ssh_config = self.app_config.get("ssh", {})
        host_key_policy = str(self.ssh_config.get("host_key_policy", "auto-add")).strip().lower()
        if host_key_policy not in HOST_KEY_POLICY_OPTIONS:
            host_key_policy = "auto-add"
        self.ssh_host_key_policy = host_key_policy
        terminal_config = self.app_config.get("terminal", {})
        try:
            self.max_sessions = max(
                MAX_SESSIONS_MIN,
                min(MAX_SESSIONS_MAX, int(terminal_config.get("max_sessions", 4))),
            )
        except (ValueError, TypeError):
            self.max_sessions = 4
        try:
            self.terminal_font_size = max(
                TERMINAL_FONT_SIZE_MIN,
                min(TERMINAL_FONT_SIZE_MAX, int(terminal_config.get("font_size", 14))),
            )
        except (ValueError, TypeError):
            self.terminal_font_size = 14
        self.terminal_font_family = str(
            terminal_config.get("font_family", DEFAULT_TERMINAL_FONT_FAMILY)
        ).strip() or DEFAULT_TERMINAL_FONT_FAMILY
        appearance_config = self.app_config.get("appearance", {})
        app_theme = str(appearance_config.get("theme", "system")).strip().lower()
        if app_theme not in {"system", "light", "dark"}:
            app_theme = "system"
        self.app_theme = app_theme

        workspace_config = self.app_config.get("workspace", {})
        self.app_surface_mode = _normalize_surface_mode(workspace_config.get("surface_mode"))
        multi_workspace_enabled = workspace_config.get("multi_workspace_enabled", False)
        self.multi_workspace_enabled = (
            multi_workspace_enabled
            if isinstance(multi_workspace_enabled, bool)
            else False
        )
        try:
            self.workspace_autosave_interval_minutes = max(
                AUTOSAVE_INTERVAL_MINUTES_MIN,
                min(
                    AUTOSAVE_INTERVAL_MINUTES_MAX,
                    int(workspace_config.get(
                        "autosave_interval_minutes", AUTOSAVE_INTERVAL_MINUTES_DEFAULT
                    )),
                ),
            )
        except (ValueError, TypeError):
            self.workspace_autosave_interval_minutes = AUTOSAVE_INTERVAL_MINUTES_DEFAULT

        search_config = self.app_config.get("explorer_search", {})
        if not isinstance(search_config, dict):
            search_config = {}
        self.explorer_search_max_files = _clamped_int(
            search_config.get("max_files", EXPLORER_SEARCH_MAX_FILES_DEFAULT),
            EXPLORER_SEARCH_MAX_FILES_MIN,
            EXPLORER_SEARCH_MAX_FILES_MAX,
            EXPLORER_SEARCH_MAX_FILES_DEFAULT,
        )
        self.explorer_search_max_matches = _clamped_int(
            search_config.get("max_matches", EXPLORER_SEARCH_MAX_MATCHES_DEFAULT),
            EXPLORER_SEARCH_MAX_MATCHES_MIN,
            EXPLORER_SEARCH_MAX_MATCHES_MAX,
            EXPLORER_SEARCH_MAX_MATCHES_DEFAULT,
        )
        self.explorer_search_max_matches_per_file = _clamped_int(
            search_config.get(
                "max_matches_per_file", EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_DEFAULT
            ),
            EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_MIN,
            EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_MAX,
            EXPLORER_SEARCH_MAX_MATCHES_PER_FILE_DEFAULT,
        )
        self.explorer_search_max_file_bytes = _clamped_int(
            search_config.get("max_file_bytes", EXPLORER_SEARCH_MAX_FILE_BYTES_DEFAULT),
            EXPLORER_SEARCH_MAX_FILE_BYTES_MIN,
            EXPLORER_SEARCH_MAX_FILE_BYTES_MAX,
            EXPLORER_SEARCH_MAX_FILE_BYTES_DEFAULT,
        )
        self.explorer_search_timeout_seconds = _clamped_int(
            search_config.get("timeout_seconds", EXPLORER_SEARCH_TIMEOUT_SECONDS_DEFAULT),
            EXPLORER_SEARCH_TIMEOUT_SECONDS_MIN,
            EXPLORER_SEARCH_TIMEOUT_SECONDS_MAX,
            EXPLORER_SEARCH_TIMEOUT_SECONDS_DEFAULT,
        )

        voice_config = self.app_config.get("voice_input", {})
        self.voice_enabled = voice_config.get("enabled", True)
        voice_engine = str(voice_config.get("engine", "vosk")).strip().lower()
        if voice_engine not in {"vosk", "whisper"}:
            voice_engine = "vosk"
        self.voice_engine = voice_engine
        self.vosk_service_url = voice_config.get("vosk_service_url", "ws://localhost:2700")
        self.vosk_model = voice_config.get("vosk_model", "vosk-model-en-us-0.22")
        whisper_model = str(voice_config.get("whisper_model", "base")).strip() or "base"
        if whisper_model not in WHISPER_MODEL_OPTIONS:
            whisper_model = "base"
        self.whisper_model = whisper_model
        self.whisper_device = voice_config.get("whisper_device", "cpu")
        self.whisper_compute_type = voice_config.get("whisper_compute_type", "int8")
        self.voice_language = voice_config.get("language", "en-US")
        try:
            self.vosk_startup_timeout_seconds = max(
                30,
                int(voice_config.get("vosk_startup_timeout_seconds", 180)),
            )
        except (ValueError, TypeError):
            self.vosk_startup_timeout_seconds = 180


runtime_config = RuntimeConfig()
