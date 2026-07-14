"""Configuration loading, persistence, and config-backed runtime settings.

Extracted from web/api.py (deep-dive finding 6.2). `load_config`/`save_config`
handle the two-file merge (config.json overriding default_config.json), and
`RuntimeConfig` holds the settings that the rest of the app reads at runtime;
call `runtime_config.refresh()` after persisting a config change.
"""

import json
import logging
import os
import threading
import uuid
from typing import Any, Dict, Optional, Tuple

from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "default_config.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
_config_lock = threading.RLock()

HOST_KEY_POLICY_OPTIONS = ("auto-add", "known-hosts", "strict")

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
                return default_config
            return _merge_dicts(default_config, loaded) if default_config else loaded

        return default_config


def save_config(config: Dict[str, Any], config_path: Optional[str] = None):
    """Save configuration to file."""
    target_path = config_path or CONFIG_PATH
    target_dir = os.path.dirname(os.path.abspath(target_path)) or "."
    temp_path = os.path.join(target_dir, f".{os.path.basename(target_path)}.{uuid.uuid4().hex}.tmp")
    with _config_lock:
        try:
            with open(temp_path, 'w', encoding="utf-8") as f:
                json.dump(config, f, indent=2)
                f.write("\n")
            os.replace(temp_path, target_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise


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
        self.max_sessions = terminal_config.get("max_sessions", 4)
        try:
            self.terminal_font_size = max(6, int(terminal_config.get("font_size", 14)))
        except (ValueError, TypeError):
            self.terminal_font_size = 14
        self.terminal_font_family = str(
            terminal_config.get("font_family", "Consolas, Monaco, 'Courier New', monospace")
        ).strip() or "Consolas, Monaco, 'Courier New', monospace"
        appearance_config = self.app_config.get("appearance", {})
        app_theme = str(appearance_config.get("theme", "system")).strip().lower()
        if app_theme not in {"system", "light", "dark"}:
            app_theme = "system"
        self.app_theme = app_theme

        workspace_config = self.app_config.get("workspace", {})
        self.app_surface_mode = _normalize_surface_mode(workspace_config.get("surface_mode"))

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
