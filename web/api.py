"""
Web API for GridVibe frontend integration.
Provides REST endpoints and WebSocket support for terminal sessions.
"""

import contextlib
import io
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

from flask import jsonify, render_template, request, send_file, send_from_directory
from flask_socketio import emit, join_room, leave_room

from gridvibe_version import __version__
from sessions.manager import SessionManager, SessionStatus  # noqa: F401 - re-exported
from web.agents import (  # noqa: F401 - re-exported for backwards compatibility
    AGENT_REGISTRY,
    AGENT_REGISTRY_PATH,
    _agent_detection_cache,
    _agent_detection_cache_key,
    _agent_detection_cache_lock,
    _agent_options,
    _agent_preflight_payload,
    _agent_status_label,
    _agent_target_label,
    _build_agent_preflight_request,
    _build_login_shell_detection_command,
    _build_posix_detection_command,
    _check_install_requirement,
    _contains_html_payload,
    _detect_agent_binary,
    _detect_agent_binary_cached,
    _detect_posix_command,
    _detect_ssh_command,
    _detect_windows_command,
    _detect_wsl_command,
    _find_wsl_executable,
    _inspect_wsl_distributions,
    _load_agent_registry,
    _normalize_agent_key,
    _normalize_ping_target,
    _normalize_port_number,
    _parse_ping_latency_ms,
    _parse_posix_detection_output,
    _parse_wsl_list_output,
    _ping_command,
    _ping_ssh_target,
    _powershell_single_quote,
    _resolve_agent_target,
    _resolve_preflight_wsl_distribution,
    _sanitize_agent_launch_commands,
    _select_install_option,
    _shell_single_quote,
    _tcp_probe_target,
)
from web.app import (  # noqa: F401 - re-exported for backwards compatibility
    _allowed_write_origin_netlocs,
    _reject_cross_origin_writes,
    _resolve_cors_origins,
    _resolve_secret_key,
    app,
    apply_resolved_server_origins,
    session_manager,
    socketio,
)
from web.config import (  # noqa: F401 - compatibility re-exports
    AUTOSAVE_INTERVAL_MINUTES_MAX,
    AUTOSAVE_INTERVAL_MINUTES_MIN,
    HOST_KEY_POLICY_OPTIONS,
    MAX_SESSIONS_MAX,
    MAX_SESSIONS_MIN,
    TERMINAL_FONT_FAMILY_MAX_LENGTH,
    TERMINAL_FONT_SIZE_MAX,
    TERMINAL_FONT_SIZE_MIN,
    WHISPER_MODEL_OPTIONS,
    ConfigPersistenceError,
    RuntimeConfigState,
    _build_runtime_state,
    _config_lock,
    _merge_dicts,
    _normalize_surface_mode,
    load_config,
    resolve_server_settings,  # noqa: F401 - re-exported for the entry points
    runtime_config,
    save_config,
    update_config,
)
from web.dashboard import build_dashboard_snapshot
from web.explorer import (  # noqa: F401 - some names re-exported for backwards compatibility
    EXPLORER_FILE_PREVIEW_MAX_BYTES,
    ExplorerRouteError,
    _acquire_ssh_sftp,
    _append_deleted_git_entries,
    _attach_git_status_to_entries,
    _clean_git_entry_status,
    _configured_explorer_root_directory,
    _evict_all_pooled_ssh_clients,
    _evict_pooled_ssh_client,
    _explorer_backend,
    _explorer_content_looks_binary,
    _explorer_cwd_repo_root,
    _explorer_image_mimetype,
    _explorer_root_directory,
    _fs_root_revision,
    _get_git_context,
    _get_git_diff,
    _get_git_repo_state,
    _get_git_repo_summary,
    _git_commit,
    _git_discard_all_paths,
    _git_publish,
    _git_revert_path,
    _git_stage_all_paths,
    _git_stage_path,
    _git_status_for_entry,
    _git_unstage_all_paths,
    _git_unstage_path,
    _is_browser_session,
    _is_explorer_image_file,
    _is_explorer_session,
    _is_markdown_file,
    _is_remote_explorer_session,
    _is_tail_preview_file,
    _local_path_inside,
    _LocalExplorerBackend,
    _relative_explorer_path,
    _relative_remote_explorer_path,
    _release_ssh_sftp,
    _remote_explorer_root_directory,
    _remote_is_directory,
    _remote_path_clean,
    _remote_path_inside,
    _render_markdown_preview,
    _resolve_explorer_candidate_path,
    _resolve_explorer_open_root,
    _resolve_pane_terminal_directory,
    _resolve_remote_explorer_candidate_path,
    _sftp_request_error_types,
    _SftpExplorerBackend,
    get_explorer_file_payload,
    get_explorer_file_preview_payload,
    get_explorer_file_state_payload,
    normalized_git_log_limit,
    open_path_in_os_file_manager,
    read_explorer_file_preview,
    save_explorer_file_payload,
)
from web.explorer_fs import (
    EXPLORER_UPLOAD_MAX_BYTES,
    create_explorer_entry_payload,
    delete_explorer_entry_payload,
    move_explorer_entry_payload,
    paste_explorer_entry_payload,
    rename_explorer_entry_payload,
    upload_explorer_file_payload,
)
from web.explorer_search import (  # noqa: F401 - re-exported for backwards compatibility
    run_explorer_find,
    run_explorer_search,
)
from web.hostkeys import (  # noqa: F401 - re-exported for backwards compatibility
    _apply_host_key_policy,
    _load_persistent_host_keys,
)
from web.lifecycle import (
    LIFECYCLE_ACTIONS,
    LIFECYCLE_SAVE_CHOICES,
    LIFECYCLE_SAVE_NONE,
    LifecycleValidationError,
    lifecycle_coordinator,
    normalize_workspace_metadata,
    prepare_lifecycle_action,
    prepare_workspace_save,
)
from web.paths import BASE_DIR, install_kind
from web.runtime_state import (  # noqa: F401 - re-exported for backwards compatibility
    RuntimeStatePersistenceError,
    capture_live_workspaces,
    capture_workspace,
    clear_workspace,
    list_restorable_workspaces,
    load_restorable_workspace,
)
from web.saved_session_store import SavedSessionsPersistenceError
from web.saved_sessions import (  # noqa: F401 - re-exported for backwards compatibility
    BROWSER_MAX_TABS,
    DEFAULT_BROWSER_URL,
    DEFAULT_SAVED_SESSION_ID,
    DEFAULT_SAVED_SESSION_NAME,
    SAVED_SESSIONS_PATH,
    _build_launch_group_id,
    _default_saved_session_entry,
    _default_session_config,
    _default_terminal_entries,
    _find_saved_session_entry,
    _generate_saved_session_id,
    _load_saved_sessions_payload,
    _merge_workspace_session_config,
    _normalize_browser_active_tab,
    _normalize_browser_tabs,
    _normalize_browser_url,
    _normalize_connection_mode,
    _normalize_launch_session_id,
    _normalize_layout,
    _normalize_saved_session_entry,
    _normalize_session_config,
    _normalize_startup_mode,
    _normalize_terminal_entries,
    _normalize_workspace_layout,
    _save_saved_sessions_payload,
    _saved_session_meta,
    _saved_session_response,
    _utc_timestamp,
    apply_live_ssh_credential,
    build_connection_target_proposals,
    build_live_session_view_updates,
    build_unique_session_name,
    delete_saved_sessions,
    load_saved_sessions,
    load_session_config,
    save_saved_sessions,
    set_last_saved_session,
    upsert_saved_session,
)
from web.secrets import (  # noqa: F401 - re-exported for backwards compatibility
    _decrypt_password,
    _encrypt_password,
)
from web.selfupdate import (  # noqa: F401 - perform_self_update re-exported for backwards compatibility
    AppUpdateError,
    perform_app_update,
    perform_self_update,
)
from web.session_modes import (  # noqa: F401 - re-exported for backwards compatibility
    ModeTransitionEffects,
    ModeTransitionError,
    _refresh_pane_cwd,
    apply_pane_mode_change,
)
from web.session_presentation import (
    PresentationValidationError,
    apply_group_presentation,
    apply_workspace_presentation,
)
from web.session_shell import (  # noqa: F401 - re-exported for backwards compatibility
    ShellTransitionEffects,
    ShellTransitionError,
    apply_pane_shell_change,
)
from web.terminal_io import (  # noqa: F401 - re-exported for backwards compatibility
    _MAX_TRACKED_SOCKET_CLIENTS,
    _MAX_TRACKED_TERMINAL_COMMAND_LENGTH,
    CWD_SOURCE_LAUNCH,
    LOCAL_SHELL_KINDS,
    SSH_STREAM_RECV_TIMEOUT,
    TERMINAL_OUTPUT_BUFFER_MAX_CHARS,
    WINDOWS_DEVICE_ATTRIBUTES_RESPONSE,
    _agent_from_terminal_command,
    _broadcast_session_groups_updated,
    _broadcast_session_status,
    _build_local_command,
    _cache_terminal_output,
    _clear_client_joined_sessions,
    _clear_terminal_output_buffer,
    _close_all_ssh_connections,
    _close_displaced_sessions,
    _close_ssh_connection,
    _connect_local_session,
    _connect_session,
    _connect_ssh_session,
    _drain_until_prompt,
    _extract_terminal_cwd_from_buffer,
    _finalize_stream,
    _get_buffered_terminal_output,
    _local_shell_display_name,
    _local_shell_kind,
    _mark_runtime_agent_exited,
    _normalize_local_directory,
    _normalize_local_shell_kind,
    _normalize_probed_local_cwd,
    _resize_connection,
    _resolve_live_terminal_cwd,
    _resolve_local_launch_cwd,
    _resolve_wsl_distribution,
    _run_startup_sequence,
    _sanitize_terminal_input,
    _send_connection_input,
    _shutdown_connection,
    _stream_local_output,
    _stream_ssh_output,
    _terminal_cwd_probe_command,
    _track_terminal_agent_input,
    agent_activity_snapshot,
    client_joined_sessions,
    connection_lock,
    effective_directory,
    session_output_buffers,
    ssh_connections,
)
from web.voice import (  # noqa: F401 - re-exported for backwards compatibility
    VOICE_PREFS_VALID_KEYS,
    _active_voice_sessions,
    _active_voice_sessions_lock,
    _default_voice_prefs,
    _ensure_vosk_service,
    _ensure_whisper_model,
    _handle_vosk_audio_chunk,
    _handle_whisper_audio_chunk,
    _load_voice_prefs,
    _pcm16le_to_float32,
    _reload_voice_backends,
    _restart_vosk_service,
    _save_voice_prefs,
    _start_voice_dependency_install,
    _start_vosk_voice_session,
    _start_whisper_voice_session,
    _stop_vosk_service,
    _stop_vosk_voice_session,
    _stop_whisper_voice_session,
    _transcribe_whisper_audio,
    _voice_engine_available,
    _voice_engine_unavailable_message,
    _voice_engines_available,
    _voice_install_status,
    _vosk_engine_available,
    _vosk_lock,
    _vosk_service_packages_available,
    _vosk_service_reachable,
    _vosk_session_locks,
    _vosk_ws_connections,
    _wait_for_vosk_ready,
    _whisper_audio_buffers,
    _whisper_audio_lock,
    _whisper_engine_available,
    _whisper_language_code,
    _whisper_model_lock,
    abandon_client_voice_sessions,
    register_voice_session,
    release_voice_session,
    resolve_voice_session_engine,
)
from web.workspaces import (
    DEFAULT_WORKSPACE_ID,
    WorkspaceRequestError,
    _redacted_launch_summary,
    capacity_refusal,
    close_extra_workspaces,
    close_live_workspace,
    create_labelled_workspace,
    forget_emptied_default_workspace,
    forget_pruned_workspaces,
    launch_session_group,
    list_live_workspaces,
    list_restorable_workspace_summaries,
    move_group_to_workspace,
    normalize_workspace_id,
    normalize_workspace_label,
    public_workspace_payload,
    rename_workspace_label,
    restore_workspaces,
    workspace_has_groups,
    workspace_label,
    workspace_label_conflict,
    workspace_missing_payload,
    workspace_room,
)

try:
    import tkinter as tk
    from tkinter import filedialog
except ImportError:  # pragma: no cover - optional for native folder picker
    tk = None
    filedialog = None

logger = logging.getLogger(__name__)
_browser_shutdown_lock = threading.RLock()
_browser_shutdown_token = ""


def configure_browser_shutdown(enabled: bool) -> str:
    """Enable process shutdown controls only for explicit browser launch mode."""
    global _browser_shutdown_token
    with _browser_shutdown_lock:
        _browser_shutdown_token = uuid.uuid4().hex if enabled else ""
        return _browser_shutdown_token



# ==================== Configuration ====================
# load_config/save_config and the RuntimeConfig singleton moved to
# web/config.py (finding 6.2); the names imported above stay re-exported here
# for backwards compatibility. Runtime settings are read as
# runtime_config.<name> so a refresh (or a test patch) is seen everywhere.


def _refresh_runtime_config():
    """Reload runtime config-backed settings from disk."""
    runtime_config.refresh()


def _active_voice_model_name(settings: Optional[RuntimeConfigState] = None) -> str:
    """Return the currently configured STT model name.

    A caller already holding a captured generation passes it in, so the engine
    and the model it names come from the same one.
    """
    settings = settings if settings is not None else runtime_config.snapshot()
    return settings.whisper_model if settings.voice_engine == "whisper" else settings.vosk_model


def _public_app_config() -> Dict[str, Any]:
    """Return the subset of app config that the launcher can edit safely.

    Built from one captured generation (ISSUE-2026-041): a payload of twelve
    independently read settings could otherwise describe a config file that
    never existed, half from before a concurrent refresh and half from after.
    """
    settings = runtime_config.snapshot()
    return {
        "install_kind": install_kind(),
        "version": __version__,
        "appearance": {
            "theme": settings.app_theme,
        },
        "workspace": {
            "surface_mode": settings.app_surface_mode,
            "autosave_interval_minutes": settings.workspace_autosave_interval_minutes,
            "multi_workspace_enabled": settings.multi_workspace_enabled,
            "minimize_cascade": settings.workspace_minimize_cascade,
        },
        "ssh": {
            "host_key_policy": settings.ssh_host_key_policy,
        },
        "terminal": {
            "font_family": settings.terminal_font_family,
            "font_size": settings.terminal_font_size,
            "max_sessions": settings.max_sessions,
            "shell_integration": settings.terminal_shell_integration,
        },
        "voice_input": {
            "enabled": settings.voice_enabled,
            "engine": settings.voice_engine,
            "vosk_model": settings.vosk_model,
            "whisper_model": settings.whisper_model,
            "whisper_device": settings.whisper_device,
            "whisper_compute_type": settings.whisper_compute_type,
            "language": settings.voice_language,
        }
    }


def _broadcast_app_config_update(apply_scope: str = "session"):
    """Notify open app windows that launcher-editable settings changed.

    ``apply_scope`` tells open workspaces which panes restyle (OD-14): the
    default ``session`` targets only the focused terminal, ``all`` pushes the
    font settings to every active session.
    """
    settings = runtime_config.snapshot()
    socketio.emit(
        "app_config_updated",
        {
            "appearance": {
                "theme": settings.app_theme,
            },
            "workspace": {
                "surface_mode": settings.app_surface_mode,
                "multi_workspace_enabled": settings.multi_workspace_enabled,
            },
            "terminal": {
                "font_family": settings.terminal_font_family,
                "font_size": settings.terminal_font_size,
                "apply_scope": "all" if apply_scope == "all" else "session",
            },
            "timestamp": int(time.time() * 1000),
        },
    )


def _normalize_app_config_update(data: Any, settings=None) -> Dict[str, Any]:
    """Validate and normalize launcher-editable app settings.

    Every omitted field falls back to the *same* captured generation
    (ISSUE-2026-041), so a partial update cannot write back a mixture of two
    configs for the settings the request did not mention.
    """
    settings = settings if settings is not None else runtime_config.snapshot()
    payload = data if isinstance(data, dict) else {}
    appearance = payload.get("appearance")
    if not isinstance(appearance, dict):
        appearance = {}
    theme = str(appearance.get("theme", settings.app_theme)).strip().lower()
    if theme not in {"system", "light", "dark"}:
        theme = settings.app_theme

    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
    surface_mode = _normalize_surface_mode(workspace.get("surface_mode"), settings.app_surface_mode)
    multi_workspace_enabled = workspace.get(
        "multi_workspace_enabled",
        settings.multi_workspace_enabled,
    )
    if not isinstance(multi_workspace_enabled, bool):
        multi_workspace_enabled = settings.multi_workspace_enabled
    minimize_cascade = workspace.get(
        "minimize_cascade",
        settings.workspace_minimize_cascade,
    )
    if not isinstance(minimize_cascade, bool):
        minimize_cascade = settings.workspace_minimize_cascade
    try:
        autosave_interval_minutes = int(
            workspace.get(
                "autosave_interval_minutes",
                settings.workspace_autosave_interval_minutes,
            )
        )
    except (TypeError, ValueError):
        autosave_interval_minutes = settings.workspace_autosave_interval_minutes
    autosave_interval_minutes = max(
        AUTOSAVE_INTERVAL_MINUTES_MIN,
        min(AUTOSAVE_INTERVAL_MINUTES_MAX, autosave_interval_minutes),
    )

    ssh_settings = payload.get("ssh")
    if not isinstance(ssh_settings, dict):
        ssh_settings = {}
    host_key_policy = str(
        ssh_settings.get("host_key_policy", settings.ssh_host_key_policy)
    ).strip().lower()
    if host_key_policy not in HOST_KEY_POLICY_OPTIONS:
        host_key_policy = settings.ssh_host_key_policy

    terminal_settings = payload.get("terminal")
    if not isinstance(terminal_settings, dict):
        terminal_settings = {}
    font_family = str(
        terminal_settings.get("font_family", settings.terminal_font_family)
    ).strip()
    if not font_family or len(font_family) > TERMINAL_FONT_FAMILY_MAX_LENGTH:
        font_family = settings.terminal_font_family
    try:
        font_size = int(terminal_settings.get("font_size", settings.terminal_font_size))
    except (TypeError, ValueError):
        font_size = settings.terminal_font_size
    font_size = max(TERMINAL_FONT_SIZE_MIN, min(TERMINAL_FONT_SIZE_MAX, font_size))
    try:
        max_sessions = int(terminal_settings.get("max_sessions", settings.max_sessions))
    except (TypeError, ValueError):
        max_sessions = settings.max_sessions
    max_sessions = max(MAX_SESSIONS_MIN, min(MAX_SESSIONS_MAX, max_sessions))
    shell_integration = terminal_settings.get(
        "shell_integration", settings.terminal_shell_integration
    )
    if not isinstance(shell_integration, bool):
        shell_integration = settings.terminal_shell_integration

    voice_input = payload.get("voice_input")
    if not isinstance(voice_input, dict):
        voice_input = {}

    engine = str(voice_input.get("engine", settings.voice_engine)).strip().lower()
    if engine not in {"vosk", "whisper"}:
        engine = settings.voice_engine

    whisper_device_value = str(
        voice_input.get("whisper_device", settings.whisper_device)
    ).strip().lower()
    if whisper_device_value not in {"cpu", "cuda"}:
        whisper_device_value = settings.whisper_device

    next_whisper_model = str(
        voice_input.get("whisper_model", settings.whisper_model)
    ).strip() or settings.whisper_model
    if next_whisper_model not in WHISPER_MODEL_OPTIONS:
        next_whisper_model = "base"

    return {
        "appearance": {
            "theme": theme,
        },
        "workspace": {
            "surface_mode": surface_mode,
            "autosave_interval_minutes": autosave_interval_minutes,
            "multi_workspace_enabled": multi_workspace_enabled,
            "minimize_cascade": minimize_cascade,
        },
        "ssh": {
            "host_key_policy": host_key_policy,
        },
        "terminal": {
            "font_family": font_family,
            "font_size": font_size,
            "max_sessions": max_sessions,
            "shell_integration": shell_integration,
        },
        "voice_input": {
            "enabled": bool(voice_input.get("enabled", settings.voice_enabled)),
            "engine": engine,
            "vosk_model": str(voice_input.get("vosk_model", settings.vosk_model)).strip() or settings.vosk_model,
            "whisper_model": next_whisper_model,
            "whisper_device": whisper_device_value,
            "whisper_compute_type": str(
                voice_input.get("whisper_compute_type", settings.whisper_compute_type)
            ).strip() or settings.whisper_compute_type,
            "language": str(voice_input.get("language", settings.voice_language)).strip() or settings.voice_language,
        }
    }


_default_terminal_count = min(4, runtime_config.snapshot().max_sessions)
active_launch_options: Dict[str, Any] = {
    "connection_mode": "ssh",
    "layout": _normalize_layout("grid", _default_terminal_count),
    "terminal_count": _default_terminal_count,
}


folder_dialog_lock = threading.Lock()


def _resolve_group_id() -> str:
    """Return the requested session group id, if any."""
    return str(request.args.get("group") or "").strip()


def _resolve_workspace_id(value: Any = None) -> str:
    """Normalize a workspace id at the HTTP boundary."""
    requested = (
        request.args.get("workspace_id")
        if value is None and "workspace_id" in request.args
        else value
    )
    return normalize_workspace_id(requested)


def _workspace_exists(workspace_id: str) -> bool:
    """Return whether the normalized workspace is live."""
    return session_manager.get_workspace(workspace_id) is not None


def _get_group_response_meta(
    group_id: str,
    *,
    allow_launch_fallback: bool = True,
) -> Dict[str, Any]:
    """Return layout metadata for one session group.

    ``surface_mode`` is always the current global setting, never a per-group
    copy: it used to be frozen into the group at launch, so changing the App
    Setting left every already-launched group (and every workspace restored
    from a snapshot of one) reporting the value it launched with.
    """
    group = session_manager.get_group(group_id)
    if not group:
        launch_options = active_launch_options if allow_launch_fallback else {
            "layout": None,
            "connection_mode": None,
        }
        return {
            "group": None,
            "layout": launch_options["layout"],
            "connection_mode": launch_options["connection_mode"],
            "terminal_count": 0,
            "workspace_layout": None,
            "surface_mode": runtime_config.app_surface_mode,
        }

    return {
        "group": group.to_dict(),
        "layout": group.layout,
        "connection_mode": group.connection_mode,
        "terminal_count": group.terminal_count,
        "workspace_layout": group.workspace_layout,
        "surface_mode": runtime_config.app_surface_mode,
    }


def _pick_local_folder(initial_dir: str = "") -> str:
    """Open a native folder picker and return the selected path."""
    if tk is None or filedialog is None:
        raise RuntimeError("Native folder picker support is unavailable")

    candidate_dir = os.path.expanduser(str(initial_dir or "").strip())
    if candidate_dir and not os.path.isdir(candidate_dir):
        candidate_dir = os.path.dirname(candidate_dir)
    if not candidate_dir or not os.path.isdir(candidate_dir):
        candidate_dir = os.path.expanduser("~")

    with folder_dialog_lock:
        root = tk.Tk()
        root.withdraw()

        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        try:
            root.update_idletasks()
            selected = filedialog.askdirectory(
                parent=root,
                initialdir=candidate_dir,
                mustexist=True,
                title="Select Local Repository",
            )
        finally:
            root.destroy()

    return str(selected or "").strip()


# ==================== HTML Routes ====================

@app.route('/')
def index():
    """Main page with terminal interface."""
    logger.info("GET /")
    with _browser_shutdown_lock:
        browser_shutdown_token = _browser_shutdown_token
    settings = runtime_config.snapshot()
    return render_template(
        'index.html',
        max_sessions=settings.max_sessions,
        agent_options=_agent_options(),
        local_windows_shells_available=os.name == "nt",
        browser_shutdown_enabled=bool(browser_shutdown_token),
        browser_shutdown_token=browser_shutdown_token,
        multi_workspace_enabled=settings.multi_workspace_enabled,
        version=__version__,
    )


@app.route('/terminals')
def terminals_page():
    """Page showing active terminal instances."""
    logger.info("GET /terminals")
    try:
        workspace_id = normalize_workspace_id(request.args.get("workspace"))
    except ValueError as exc:
        return str(exc), 400
    if not _workspace_exists(workspace_id):
        return "Workspace not found", 400
    settings = runtime_config.snapshot()
    return render_template('terminals.html', max_sessions=settings.max_sessions,
                           agent_options=_agent_options(),
                           app_surface_mode=settings.app_surface_mode,
                           workspace_id=workspace_id,
                           workspace_label=workspace_label(workspace_id),
                           multi_workspace_enabled=settings.multi_workspace_enabled,
                           local_windows_shells_available=os.name == "nt",
                           voice_enabled=settings.voice_enabled,
                           voice_engine=settings.voice_engine,
                           voice_model=_active_voice_model_name(settings),
                           voice_language=settings.voice_language,
                           terminal_font_size=settings.terminal_font_size,
                           terminal_font_family=settings.terminal_font_family,
                           version=__version__)


@app.route('/dashboard')
def dashboard_page():
    """The agent dashboard: its own window, in no workspace.

    It reads across every workspace and belongs to none of them, which is why
    it is a page rather than a panel inside one -- and why it takes no
    ``workspace`` argument. Everything it draws comes from ``/api/dashboard``;
    the template is handed only what naming an agent needs.
    """
    logger.info("GET /dashboard")
    settings = runtime_config.snapshot()
    return render_template(
        'dashboard.html',
        agent_options=_agent_options(),
        multi_workspace_enabled=settings.multi_workspace_enabled,
        version=__version__,
    )


@app.route('/docs/images/<path:filename>')
def docs_images(filename: str):
    """Serve bundled documentation images used by the local UI."""
    return send_from_directory(os.path.join(BASE_DIR, "docs", "images"), filename)


# ==================== API Routes ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "GridVibe",
        "version": __version__
    })


def _shutdown_browser_process():
    """Allow the HTTP response to flush, then close sessions and exit."""
    time.sleep(0.2)
    logger.info("Browser mode requested application shutdown")
    try:
        session_manager.close_all_sessions()
    except Exception:
        logger.exception("Failed to close sessions during browser shutdown")
    os._exit(0)


def _schedule_browser_shutdown():
    shutdown_thread = threading.Thread(
        target=_shutdown_browser_process,
        name="gridvibe-browser-shutdown",
        daemon=True,
    )
    shutdown_thread.start()


@app.route('/api/browser-shutdown', methods=['POST'])
def shutdown_browser_application():
    """End GridVibe only when explicit browser mode enabled this endpoint."""
    with _browser_shutdown_lock:
        expected_token = _browser_shutdown_token
    if not expected_token:
        return jsonify({"error": "Browser shutdown is unavailable"}), 404

    provided_token = request.headers.get("X-GridVibe-Shutdown-Token", "")
    if provided_token != expected_token:
        return jsonify({"error": "Invalid shutdown token"}), 403

    decision_token = request.headers.get("X-GridVibe-Lifecycle-Decision", "")
    if not lifecycle_coordinator.consume_decision(decision_token, "close"):
        return jsonify({
            "error": "Choose how to handle current changes before closing GridVibe",
            "lifecycle_decision_required": True,
        }), 409

    logger.info("Accepted browser mode shutdown request")
    _schedule_browser_shutdown()
    return jsonify({"message": "GridVibe is shutting down"}), 202


@app.route('/api/lifecycle/prepare', methods=['POST'])
def prepare_application_lifecycle():
    """Flush and persist the requested process-wide close/restart transaction."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid lifecycle payload"}), 400
    action = str(data.get("action") or "").strip()
    save = str(data.get("save") or "").strip()
    if action not in LIFECYCLE_ACTIONS or save not in LIFECYCLE_SAVE_CHOICES:
        return jsonify({"error": "Unknown lifecycle action or save choice"}), 400

    workspace_metadata = {}
    if save != LIFECYCLE_SAVE_NONE:
        # Snapshot manager membership before any wait, then release its lock.
        # Socket emits and client acknowledgements happen only after that read.
        live_snapshot = session_manager.snapshot_live_workspaces()
        flush_result = lifecycle_coordinator.request_flush(
            live_snapshot,
            lambda workspace_id, request_id: socketio.emit(
                "lifecycle_flush_requested",
                {"request_id": request_id, "workspace_id": workspace_id},
                room=workspace_room(workspace_id),
            ),
        )
        if not flush_result["ok"]:
            logger.warning(
                "Lifecycle %s flush failed: save=%s categories=%s",
                action,
                save,
                sorted(
                    {
                        error.get("category", "client_flush")
                        for error in flush_result["errors"]
                    }
                ),
            )
            return jsonify({
                "action": action,
                "save": save,
                "ready_to_exit": False,
                "retryable": True,
                "saved_sessions": [],
                "saved_workspaces": [],
                "errors": flush_result["errors"],
                "missing_workspaces": flush_result["missing_workspaces"],
            }), 503
        try:
            workspace_metadata = normalize_workspace_metadata(
                flush_result["metadata"],
                session_manager.snapshot_live_workspaces(),
            )
        except LifecycleValidationError as exc:
            return jsonify({
                "action": action,
                "save": save,
                "ready_to_exit": False,
                "retryable": True,
                "saved_sessions": [],
                "saved_workspaces": [],
                "errors": [{"category": "client_metadata", "error": str(exc)}],
            }), 503

    try:
        result = prepare_lifecycle_action(
            session_manager,
            action,
            save,
            workspace_metadata=workspace_metadata,
        )
    except LifecycleValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    if result["ready_to_exit"]:
        result["decision_token"] = lifecycle_coordinator.issue_decision(action)
        return jsonify(result)
    return jsonify(result), 503


@app.route('/api/app-update', methods=['POST'])
def update_application():
    """Check for updates via the path appropriate to this install and report the outcome."""
    try:
        result = perform_app_update()
        logger.info(
            "Application update check completed branch=%s updated=%s behind=%s ahead=%s",
            result.get("branch"),
            result.get("updated"),
            result.get("behind_count"),
            result.get("ahead_count"),
        )
        return jsonify(result)
    except AppUpdateError as exc:
        logger.warning("Application update blocked: %s", exc)
        return jsonify({"error": str(exc)}), exc.status_code
    except Exception as exc:
        logger.exception("Unexpected application update failure")
        return jsonify({"error": f"Unexpected update failure: {exc}"}), 500


@app.route('/api/app-config', methods=['GET'])
def get_app_config():
    """Return launcher-editable application settings."""
    return jsonify(_public_app_config())


@app.route('/api/app-config', methods=['POST'])
def set_app_config():
    """Persist launcher-editable application settings to config.json."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid payload"}), 400

    # OD-14: the apply scope is a one-shot modifier for open windows — it rides
    # the broadcast below but must never be persisted into config.json
    # (_normalize_app_config_update builds the persisted dict without it).
    terminal_payload = data.get("terminal")
    if not isinstance(terminal_payload, dict):
        terminal_payload = {}
    apply_scope = str(terminal_payload.get("apply_scope", "")).strip().lower()

    try:
        update_config(lambda current: _normalize_app_config_update(data, _build_runtime_state(current)))
    except ConfigPersistenceError as exc:
        return jsonify({"error": str(exc), "code": "config_write_failed"}), 500
    _broadcast_app_config_update(apply_scope)
    return jsonify(_public_app_config())


@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get all sessions."""
    workspace_named = "workspace_id" in request.args
    try:
        workspace_id = _resolve_workspace_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    group_id = _resolve_group_id()
    error = ""
    workspace_missing = False
    group_meta = None
    with session_manager.lock:
        if workspace_named and workspace_id not in session_manager.workspaces:
            error = "Workspace not found"
            workspace_missing = True
        group = session_manager.groups.get(group_id) if group_id else None
        if not error and group_id and group is not None and group.workspace_id != workspace_id:
            error = "Session group does not belong to workspace"
        if not error and workspace_named and group_id and group is None:
            error = "Session group does not belong to workspace"

        if error:
            sessions = []
            session_payloads = []
            statuses = []
        else:
            if group_id:
                sessions = session_manager.get_group_sessions(group_id)
            else:
                sessions = session_manager.get_workspace_sessions(workspace_id)
            session_payloads = [session.to_dict() for session in sessions]
            statuses = [session.status.value for session in sessions]
            if group_id:
                group_meta = _get_group_response_meta(
                    group_id,
                    allow_launch_fallback=not workspace_named,
                )
    if error:
        return jsonify(
            workspace_missing_payload() if workspace_missing else {"error": error}
        ), 400
    logger.debug(
        f"GET /api/sessions workspace={workspace_id} "
        f"group={group_id or 'all'} count={len(sessions)} "
        f"statuses={statuses}"
    )
    payload = {
        "sessions": session_payloads,
        "count": len(sessions),
    }
    if group_id:
        payload.update(group_meta)
    elif workspace_named:
        payload.update(
            {
                "group": None,
                "layout": None,
                "connection_mode": None,
                "terminal_count": len(sessions),
                "workspace_layout": None,
                "surface_mode": runtime_config.app_surface_mode,
                "workspace_id": workspace_id,
            }
        )
    else:
        launch_options = active_launch_options
        payload.update(
            {
                "group": None,
                "layout": launch_options["layout"],
                "connection_mode": launch_options["connection_mode"],
                "terminal_count": launch_options["terminal_count"],
                "workspace_layout": None,
                "surface_mode": runtime_config.app_surface_mode,
            }
        )
    return jsonify(payload)


@app.route('/api/explorer/<session_id>/entries', methods=['GET'])
def get_explorer_entries(session_id: str):
    """List entries for a file explorer pane."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args["path"] if "path" in request.args else None

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, current_path = backend.resolve_dir(requested_path)
        entries = backend.list_entries(root_path, current_path)
        git_context, git_statuses = _get_git_context(backend, root_path, current_path)
        _attach_git_status_to_entries(backend, root_path, git_context, git_statuses, entries)
        _append_deleted_git_entries(backend, root_path, current_path, git_context, git_statuses, entries)
        entries.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
        return {
            "root": root_path,
            "root_revision": _fs_root_revision(root_path),
            "path": backend.rel_explorer_path(root_path, current_path),
            "parent_path": backend.parent_explorer_path(root_path, current_path),
            "git": git_context,
            "entries": entries,
        }

    return _explorer_route_response(session, handler)


def _explorer_mutation_json(
    allowed_fields: set,
) -> Any:
    """Return a mutation JSON object or one shared invalid-request response."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (
            jsonify(
                {
                    "error": "Request body must be a JSON object",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )
    unexpected = sorted(set(data) - allowed_fields)
    if unexpected:
        return None, (
            jsonify(
                {
                    "error": f"Unsupported request field: {unexpected[0]}",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )
    return data, None


@app.route('/api/explorer/<session_id>/create', methods=['POST'])
def create_explorer_entry(session_id: str):
    """Create one exact empty file or folder without overwrite."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    data, error_response = _explorer_mutation_json(
        {
            "root_revision",
            "destination_directory",
            "name",
            "entry_kind",
        }
    )
    if error_response is not None:
        return error_response
    if (
        not isinstance(data.get("root_revision"), str)
        or not data.get("root_revision")
        or not isinstance(data.get("destination_directory"), str)
        or not isinstance(data.get("name"), str)
        or not isinstance(data.get("entry_kind"), str)
    ):
        return (
            jsonify(
                {
                    "error": "Create requires a root revision, destination directory, name, and entry kind",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )

    def handler(backend: Any) -> Dict[str, Any]:
        return create_explorer_entry_payload(
            backend,
            root_revision=data["root_revision"],
            destination_directory=data["destination_directory"],
            name=data["name"],
            entry_kind=data["entry_kind"],
            session_id=session_id,
        )

    return _explorer_route_response(session, handler)


# The multipart envelope a browser adds around a 100 MB part is small, but it
# is not nothing, so the request ceiling sits a little above the file ceiling;
# the bytes that actually land are bounded by the payload's own reader.
EXPLORER_UPLOAD_MAX_REQUEST_BYTES = EXPLORER_UPLOAD_MAX_BYTES + (1024 * 1024)


@app.route('/api/explorer/<session_id>/upload', methods=['POST'])
def upload_explorer_file(session_id: str):
    """Write one uploaded file into this pane's explorer root without overwrite.

    Download's mirror on the write side, and one file per request for the same
    reason every other explorer mutation is: a batch is N atomic calls, so a
    partial failure names the file that failed and retries only that one. There
    is deliberately no archive form and no overwrite.

    ``Content-Length`` is checked *before* ``request.files`` is touched, because
    reading the form is what spools the whole body to disk -- a refusal has to
    cost a header read, not a 500 MB temp file.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    declared_request_bytes = request.content_length
    if (
        declared_request_bytes is not None
        and declared_request_bytes > EXPLORER_UPLOAD_MAX_REQUEST_BYTES
    ):
        return (
            jsonify(
                {
                    "error": (
                        "The upload exceeds the "
                        f"{EXPLORER_UPLOAD_MAX_BYTES // (1024 * 1024)} MB limit"
                    ),
                    "code": "upload_too_large",
                    "mutated": False,
                    "max_bytes": EXPLORER_UPLOAD_MAX_BYTES,
                }
            ),
            413,
        )
    upload = request.files.get("file")
    if upload is None:
        return (
            jsonify(
                {
                    "error": "Upload requires one file part named file",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )
    root_revision = request.form.get("root_revision", "")
    destination_directory = request.form.get("destination_directory", "")
    # The client states the leaf so the server never has to guess one out of a
    # browser-supplied filename; when it does not, the part's own name is used
    # and validated by exactly the same literal-leaf rules as Create.
    name = request.form.get("name") or (upload.filename or "")
    if not root_revision:
        return (
            jsonify(
                {
                    "error": "Upload requires a root revision and a destination directory",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )

    def handler(backend: Any) -> Dict[str, Any]:
        return upload_explorer_file_payload(
            backend,
            root_revision=root_revision,
            destination_directory=destination_directory,
            name=name,
            stream=upload.stream,
            declared_size=(
                upload.content_length if upload.content_length else None
            ),
            session_id=session_id,
        )

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/paste', methods=['POST'])
def paste_explorer_entry(session_id: str):
    """Copy one entry inside the same live explorer root without overwrite."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    data, error_response = _explorer_mutation_json(
        {
            "root_revision",
            "source_path",
            "source_revision",
            "destination_directory",
        }
    )
    if error_response is not None:
        return error_response
    required_strings = ("root_revision", "source_path", "source_revision")
    if any(
        not isinstance(data.get(field), str) or not data.get(field)
        for field in required_strings
    ) or not isinstance(data.get("destination_directory"), str):
        return (
            jsonify(
                {
                    "error": "Paste requires root/source revisions, a source path, and a destination directory",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )

    def handler(backend: Any) -> Dict[str, Any]:
        return paste_explorer_entry_payload(
            backend,
            root_revision=data["root_revision"],
            source_path=data["source_path"],
            source_revision=data["source_revision"],
            destination_directory=data["destination_directory"],
            session_id=session_id,
        )

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/move', methods=['POST'])
def move_explorer_entry(session_id: str):
    """Move one entry inside the same live explorer root without overwrite."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    data, error_response = _explorer_mutation_json(
        {
            "root_revision",
            "source_path",
            "source_revision",
            "destination_directory",
        }
    )
    if error_response is not None:
        return error_response
    required_strings = ("root_revision", "source_path", "source_revision")
    if any(
        not isinstance(data.get(field), str) or not data.get(field)
        for field in required_strings
    ) or not isinstance(data.get("destination_directory"), str):
        return (
            jsonify(
                {
                    "error": "Move requires root/source revisions, a source path, and a destination directory",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )

    def handler(backend: Any) -> Dict[str, Any]:
        return move_explorer_entry_payload(
            backend,
            root_revision=data["root_revision"],
            source_path=data["source_path"],
            source_revision=data["source_revision"],
            destination_directory=data["destination_directory"],
            session_id=session_id,
        )

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/rename', methods=['POST'])
def rename_explorer_entry(session_id: str):
    """Rename one entry inside its own folder without overwrite."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    data, error_response = _explorer_mutation_json(
        {
            "root_revision",
            "source_path",
            "source_revision",
            "name",
        }
    )
    if error_response is not None:
        return error_response
    required_strings = ("root_revision", "source_path", "source_revision")
    if any(
        not isinstance(data.get(field), str) or not data.get(field)
        for field in required_strings
    ) or not isinstance(data.get("name"), str):
        return (
            jsonify(
                {
                    "error": "Rename requires root/source revisions, a source path, and a new name",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )

    def handler(backend: Any) -> Dict[str, Any]:
        return rename_explorer_entry_payload(
            backend,
            root_revision=data["root_revision"],
            source_path=data["source_path"],
            source_revision=data["source_revision"],
            name=data["name"],
            session_id=session_id,
        )

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/delete', methods=['POST'])
def delete_explorer_entry(session_id: str):
    """Permanently delete one entry, requiring an explicit recursive flag."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    data, error_response = _explorer_mutation_json(
        {"root_revision", "path", "base_revision", "recursive"}
    )
    if error_response is not None:
        return error_response
    required_strings = ("root_revision", "path", "base_revision")
    if (
        any(
            not isinstance(data.get(field), str) or not data.get(field)
            for field in required_strings
        )
        or not isinstance(data.get("recursive"), bool)
    ):
        return (
            jsonify(
                {
                    "error": "Delete requires root/entry revisions, a path, and a recursive boolean",
                    "code": "invalid_request",
                    "mutated": False,
                }
            ),
            400,
        )

    def handler(backend: Any) -> Dict[str, Any]:
        return delete_explorer_entry_payload(
            backend,
            root_revision=data["root_revision"],
            path=data["path"],
            base_revision=data["base_revision"],
            recursive=data["recursive"],
            session_id=session_id,
        )

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/file', methods=['GET'])
def get_explorer_file(session_id: str):
    """Return a safe, read-only text preview + editor metadata for one file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        return get_explorer_file_payload(backend, requested_path)

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/file/preview', methods=['GET'])
def get_explorer_file_preview(session_id: str):
    """Return the rendered Markdown preview for one explorer file.

    Split out of the file GET so that opening a Markdown file in Source view
    stops rendering and sanitizing a preview nobody asked to see. Same bounded,
    root-confined read as the file payload; the Preview panel asks for this the
    first time it is shown, and again after a save while it is the shown panel.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        return get_explorer_file_preview_payload(backend, requested_path)

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/file', methods=['PUT'])
def save_explorer_file(session_id: str):
    """Atomically replace one explorer text file with edited contents.

    The single bounded exception to the explorer's read-only filesystem
    contract: writes are confined to the session root and guarded by the
    binary-content check, the 10 MiB read/write limit, complete strict-UTF-8
    single-line-ending source, and an optimistic-concurrency revision check
    (web/explorer.py). The app-level cross-origin write guard already covers
    this PUT.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object", "code": "invalid_request"}), 400
    requested_path = data.get("path")
    content = data.get("content")
    base_revision = data.get("base_revision")
    if not isinstance(requested_path, str) or not requested_path.strip():
        return jsonify({"error": "A file path is required", "code": "invalid_request"}), 400
    if not isinstance(content, str):
        return jsonify({"error": "File content must be a string", "code": "invalid_request"}), 400
    if not isinstance(base_revision, str) or not base_revision.strip():
        return jsonify({"error": "A base revision is required", "code": "invalid_request"}), 400

    def handler(backend: Any) -> Dict[str, Any]:
        return save_explorer_file_payload(
            backend, requested_path, content, base_revision, session_id=session_id
        )

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/file/state', methods=['GET'])
def get_explorer_file_state(session_id: str):
    """Return a cheap change token for one open explorer file.

    Polled by the open-file change listener (explorer-git-watch.js) so a file
    edited outside GridVibe refreshes in the viewer. A read, like `download`
    and `search`: one `stat`, no content, ~80-byte body.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")
    known = request.args.get("known", "")

    def handler(backend: Any) -> Dict[str, Any]:
        state = get_explorer_file_state_payload(backend, requested_path)
        return {**state, "changed": state["revision"] != known}

    return _with_no_store(_explorer_route_response(session, handler))


# Downloading is a read, so it stays inside the explorer's read-only contract
# (which covers filesystem *mutations*); the cap keeps one request from serving
# an arbitrarily large remote file.
EXPLORER_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024

# The body is streamed, not buffered, so a 100 MB download costs one chunk of
# memory rather than 100 MB (audit 2026-08-14 §8.2 / Stage 5.5).
EXPLORER_DOWNLOAD_CHUNK_BYTES = 64 * 1024


@app.route('/api/explorer/<session_id>/download', methods=['GET'])
def download_explorer_file(session_id: str):
    """Stream one explorer file as an attachment (read-only; binaries allowed).

    Resolution, the root confinement check, the `stat` and the size cap all run
    *before* any byte of the response is committed, so a refusal is still a
    JSON `400` with headers the client can read. Only the body is deferred: the
    backend (and, for a remote session, its pooled SFTP channel) is handed to
    the generator, which releases it in a `finally` — the WSGI server closes the
    iterable on a completed response and on a client that disconnects mid-file,
    so neither path leaks a pool entry.

    Byte ranges are answered by seeking the handle, because the `send_file`
    path this replaced advertised `Accept-Ranges: bytes` and a browser uses it
    to resume a paused download — exactly the large files this route now
    streams. `Cache-Control: no-cache` is kept for the same continuity reason:
    the bytes are a live file and a re-download must not be served stale.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")
    error_types = (
        _sftp_request_error_types()
        if _is_remote_explorer_session(session)
        else (OSError,)
    )
    with contextlib.ExitStack() as resources:
        try:
            backend = resources.enter_context(_explorer_backend(session))
            _root_path, file_path = backend.resolve_file(requested_path)
            size, _modified = backend.stat_file(file_path)
            if size is not None and size > EXPLORER_DOWNLOAD_MAX_BYTES:
                return jsonify({"error": "File exceeds the 100 MB download limit"}), 400
            filename = backend.basename(file_path) or "download"
            handle = resources.enter_context(
                contextlib.closing(backend.open_file_stream(file_path))
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except error_types as exc:
            return jsonify({"error": str(exc)}), 500

        # A file that grew between the `stat` and the read must not escape the
        # cap, so the reader carries its own ceiling rather than trusting the
        # handle to stop.
        ceiling = (
            EXPLORER_DOWNLOAD_MAX_BYTES
            if size is None
            else min(int(size), EXPLORER_DOWNLOAD_MAX_BYTES)
        )
        start, length, partial = 0, ceiling, False
        requested_range = request.range
        if size is not None and requested_range is not None:
            span = requested_range.range_for_length(ceiling)
            if span is None:
                response = jsonify({"error": "Requested range is not satisfiable"})
                response.headers["Content-Range"] = f"bytes */{ceiling}"
                return response, 416
            start, stop = span
            length, partial = stop - start, True

        # Only a response that is actually going to be streamed takes the hold
        # away from this block; every refusal and every raise above unwinds it.
        held = resources.pop_all()

    def _stream():
        with held:
            if start:
                handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(EXPLORER_DOWNLOAD_CHUNK_BYTES, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk

    response = app.response_class(
        _stream(),
        status=206 if partial else 200,
        mimetype="application/octet-stream",
    )
    response.headers.set("Content-Disposition", "attachment", filename=filename)
    response.headers["Cache-Control"] = "no-cache"
    if size is not None:
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Length"] = str(length)
        if partial:
            response.headers["Content-Range"] = (
                f"bytes {start}-{start + length - 1}/{ceiling}"
            )
    return response


# Inline image previews are a read, so they stay inside the explorer's
# read-only contract. The cap keeps one request from buffering a huge image.
EXPLORER_IMAGE_MAX_BYTES = 25 * 1024 * 1024


@app.route('/api/explorer/<session_id>/image', methods=['GET'])
def get_explorer_image(session_id: str):
    """Serve one explorer image inline for the read-only image viewer."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")
    error_types = (
        _sftp_request_error_types()
        if _is_remote_explorer_session(session)
        else (OSError,)
    )
    try:
        with _explorer_backend(session) as backend:
            _root_path, file_path = backend.resolve_file(requested_path)
            mimetype = _explorer_image_mimetype(file_path)
            if mimetype is None:
                return jsonify({"error": "File is not a supported image"}), 400
            size, _modified = backend.stat_file(file_path)
            if size is not None and size > EXPLORER_IMAGE_MAX_BYTES:
                return jsonify({"error": "Image exceeds the 25 MB preview limit"}), 400
            raw_content = backend.read_file_prefix(file_path, EXPLORER_IMAGE_MAX_BYTES + 1)
            if len(raw_content) > EXPLORER_IMAGE_MAX_BYTES:
                return jsonify({"error": "Image exceeds the 25 MB preview limit"}), 400
            filename = backend.basename(file_path) or "image"
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except error_types as exc:
        return jsonify({"error": str(exc)}), 500
    response = send_file(
        io.BytesIO(raw_content),
        mimetype=mimetype,
        as_attachment=False,
        download_name=filename,
    )
    # <img> rendering never executes script embedded in an SVG, but this route
    # is directly reachable, so lock it down for the direct-navigation case too.
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.route('/api/explorer/<session_id>/reveal', methods=['POST'])
def reveal_explorer_path(session_id: str):
    """Open the host OS file manager at the explorer pane's current path.

    Isolated from the explorer's read-only browsing contract: it only launches
    the local file manager (never mutating files) and is limited to local panes,
    since a remote SSH path has no meaning for the server's file manager.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if _is_remote_explorer_session(session):
        return jsonify({
            "error": "Opening the file manager is only available for local explorer panes"
        }), 400
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")
    try:
        with _explorer_backend(session) as backend:
            _root_path, target_path = backend.resolve_candidate(
                requested_path, allow_empty_root=True
            )
            open_path_in_os_file_manager(target_path)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"session_id": session.session_id, "ok": True})


def _explorer_route_response(session: Any, handler: Any):
    """Run one explorer Git route handler with shared backend and error mapping."""
    error_types = (
        _sftp_request_error_types()
        if _is_remote_explorer_session(session)
        else (OSError,)
    )
    try:
        with _explorer_backend(session) as backend:
            payload = handler(backend)
        return jsonify({"session_id": session.session_id, **payload})
    except ExplorerRouteError as exc:
        # Structured editor errors carry a stable code + status the frontend
        # branches on (stale revision, save-in-progress, too-large, …).
        return jsonify({"error": str(exc), "code": exc.code, **exc.details}), exc.status_code
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except error_types as exc:
        return jsonify({"error": str(exc), "code": "io_error"}), 500


@app.route('/api/explorer/<session_id>/git/diff', methods=['GET'])
def get_explorer_git_diff(session_id: str):
    """Return a bounded read-only Git diff for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    mode = request.args.get("mode", "worktree")
    commit = request.args.get("commit")
    requested_path = request.args.get("path", "")
    # Names a context width from the server's own allowlist
    # (GIT_DIFF_CONTEXT_WIDTHS); absent means Git's default, which is what the
    # Diff panel renders. The Source gutter's change marks pass "zero".
    context = request.args.get("context", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_diff_path(requested_path)
        diff_payload = _get_git_diff(backend, root_path, file_path, mode, commit, context)
        return {
            "root": root_path,
            "path": backend.rel_explorer_path(root_path, file_path),
            "mode": mode,
            **diff_payload,
        }

    return _explorer_route_response(session, handler)


def _with_no_store(result: Any):
    """Set Cache-Control: no-store on a `_explorer_route_response` result."""
    if isinstance(result, tuple):
        response, *rest = result
        response.headers["Cache-Control"] = "no-store"
        return (response, *rest)
    result.headers["Cache-Control"] = "no-store"
    return result


def _explorer_git_anchor_paths(backend: Any) -> Tuple[str, str, str]:
    """Resolve one selected Git path and the directory Git runs from.

    Existing clients omit ``kind`` and therefore keep directory semantics.
    A file scope is resolved with the same root confinement, but its parent is
    handed to repository discovery while the file itself remains the pathspec.
    """
    root_path = backend.root_directory()
    scope_kind = request.args.get("kind", "dir")
    if scope_kind not in {"dir", "file"}:
        raise ValueError("Invalid Git scope kind")
    if request.args.get("scope") != "path":
        return root_path, root_path, root_path
    if scope_kind == "file":
        resolved_root, anchor_path = backend.resolve_file(request.args.get("path", ""))
        return resolved_root, anchor_path, backend.file_dirname(anchor_path)
    resolved_root, anchor_path = backend.resolve_dir(request.args.get("path", ""))
    return resolved_root, anchor_path, anchor_path


def _explorer_git_commit_limit() -> int:
    """Resolve how far back this request wants the commit graph read.

    The Graph's "Show more" is the only caller that asks for anything but the
    default page, and it asks on every route that answers with a repository
    summary — the mutations included, or a stage would collapse an expanded
    graph back to one page. The bound itself belongs to web/explorer.py; an
    out-of-range value is a 400 that reads nothing, never a silent clamp.
    """
    return normalized_git_log_limit(request.args.get("limit"))


@app.route('/api/explorer/<session_id>/git/repo', methods=['GET'])
def get_explorer_git_repo(session_id: str):
    """Return bounded read-only Git repository metadata for the diff sidebar."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/state', methods=['GET'])
def get_explorer_git_state(session_id: str):
    """Return a cheap semantic revision of the explorer Git sidebar state.

    Polled by the Git change listener (explorer-git-watch.js). Deliberately
    skips the commit graph and commit-file log: it exists so an unchanged
    repository costs one `git status` and nothing else.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    known = request.args.get("known", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        state = _get_git_repo_state(backend, root_path, anchor_path, context_dir)
        revision = state["revision"]
        return {"revision": revision, "changed": revision != known}

    return _with_no_store(_explorer_route_response(session, handler))


@app.route('/api/explorer/<session_id>/git/stage', methods=['POST'])
def stage_explorer_git_file(session_id: str):
    """Stage one changed file in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _target_root, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_stage_path(backend, root_path, file_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/unstage', methods=['POST'])
def unstage_explorer_git_file(session_id: str):
    """Unstage one file in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _target_root, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_unstage_path(backend, root_path, file_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/stage-all', methods=['POST'])
def stage_all_explorer_git(session_id: str):
    """Stage every working-tree change in an explorer Git repository (ISSUE-2026-032)."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _git_stage_all_paths(backend, root_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/unstage-all', methods=['POST'])
def unstage_all_explorer_git(session_id: str):
    """Unstage every staged change in an explorer Git repository (index only)."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _git_unstage_all_paths(backend, root_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/discard-all', methods=['POST'])
def discard_all_explorer_git(session_id: str):
    """Discard every tracked file's unstaged worktree changes (OD-1: no git clean)."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _git_discard_all_paths(backend, root_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/revert', methods=['POST'])
def revert_explorer_git_file(session_id: str):
    """Discard one file's unstaged changes, including a selected untracked file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _target_root, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_revert_path(backend, root_path, file_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/commit', methods=['POST'])
def commit_explorer_git(session_id: str):
    """Commit staged changes in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _git_commit(backend, root_path, message, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/publish', methods=['POST'])
def publish_explorer_git(session_id: str):
    """Push the current branch of an explorer Git repository to its remote."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, anchor_path, context_dir = _explorer_git_anchor_paths(backend)
        commit_limit = _explorer_git_commit_limit()
        _git_publish(backend, root_path, anchor_path, context_dir)
        summary = _get_git_repo_summary(
            backend, root_path, anchor_path, context_dir, commit_limit
        )
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/search', methods=['GET'])
def search_explorer(session_id: str):
    """Run a bounded read-only repository-wide search for one explorer pane.

    A search is a read, so it is a GET and stays outside the cross-origin
    write guard; error mapping and SSH lifetime match every other explorer
    route via `_explorer_route_response`.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400

    def handler(backend: Any) -> Dict[str, Any]:
        return run_explorer_search(backend, request.args)

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/find', methods=['GET'])
def find_explorer_entries(session_id: str):
    """Run a bounded read-only file/directory name search for the Files tree.

    Names only — file contents are never opened here; that is the `/search`
    route above. Like every other explorer read it is a GET, so it stays
    outside the cross-origin write guard.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400

    def handler(backend: Any) -> Dict[str, Any]:
        return run_explorer_find(backend, request.args)

    return _explorer_route_response(session, handler)


# ==================== Workspaces (multi-workspace, stage 3) ====================

@app.route('/api/workspaces', methods=['GET'])
def get_workspaces():
    """Return user-visible live workspace summaries with their group counts.

    Filtered through the one ``workspace_is_user_visible`` predicate, so every
    consumer — the Workspaces card, the launch destination menu, the Open/Move
    menus, the Alt+W cycle — sees the same set. An internal container (an empty
    ``default``) and a launch still in flight are not workspaces.
    """
    workspaces = list_live_workspaces()
    return jsonify({"workspaces": workspaces, "count": len(workspaces)})


@app.route('/api/dashboard', methods=['GET'])
def get_dashboard():
    """Return every agent running anywhere, under the workspace and session
    that holds it.

    The one request behind the agent dashboard window, and behind the badge on
    the button that opens it. It exists because the alternative is N+1 requests
    raced against each other: a window asking for workspaces, then a group list
    per workspace, then a pane list per group, would render a tree assembled
    out of several different moments.

    The two shared locks are taken in the allowed order and never nested --
    ``connection_lock`` for the activity snapshot first, released, and only then
    the manager -- so a busy pane's pump thread is never waiting on a dashboard
    poll.
    """
    activity = agent_activity_snapshot()
    return jsonify(build_dashboard_snapshot(session_manager, activity))


@app.route('/api/workspaces', methods=['POST'])
def create_workspace():
    """Create one live workspace, optionally labelled.

    A workspace created here is deliberately empty, so it is marked
    ``retain_when_empty`` until its first group arrives — otherwise cleanup
    could not tell it apart from a workspace emptied by a close or a move.

    The label is *claimed* rather than checked (ISSUE-2026-042): the namespace
    verdict and the create are one decision, so two windows submitting the same
    name produce one workspace and one actionable ``409``.
    """
    data = request.get_json(silent=True) or {}
    label = normalize_workspace_label(data.get("label") or data.get("workspace_label"))
    try:
        workspace = create_labelled_workspace(label, retain_when_empty=True)
    except WorkspaceRequestError as exc:
        return jsonify({"error": str(exc), **exc.payload}), exc.status
    logger.debug("Created workspace %s label=%r", workspace.workspace_id, workspace.label)
    return jsonify(public_workspace_payload(workspace, 0)), 201


@app.route('/api/workspaces/validate-label', methods=['POST'])
def validate_workspace_label():
    """Check whether a new-workspace label is available without creating it.

    The launcher's destination picker is a draft until a session is launched,
    so using ``POST /api/workspaces`` as its validator would leak deliberately
    empty workspaces whenever the user changed their mind. This route reads the
    same namespace owner as create, rename, move, and launch, and returns the
    same actionable ``409`` payload while mutating nothing. The launch route
    checks again when it commits, closing the validation/launch race.
    """
    data = request.get_json(silent=True) or {}
    label = normalize_workspace_label(data.get("label") or data.get("workspace_label"))
    conflict = workspace_label_conflict(label)
    if conflict is not None:
        return jsonify(conflict), 409
    return jsonify({"available": True, "label": label})


@app.route('/api/workspaces/close-extra', methods=['POST'])
def close_extra_live_workspaces():
    """Close every live workspace except ``default`` (leaving multi-workspace).

    The caller confirms first — this ends live shells. Saved snapshots are not
    written or touched here: what the autosave timer or an explicit Save
    Workspace already captured stays on offer in the restore chooser.
    """
    result = close_extra_workspaces()
    logger.info(
        "Closed %d extra workspace(s) leaving multi-workspace mode",
        result["closed_count"],
    )
    return jsonify(result)


@app.route('/api/workspaces/<workspace_id>', methods=['PATCH'])
def rename_workspace(workspace_id: str):
    """Rename one live workspace — the live label only.

    Deliberately **not** a snapshot writer. Only the autosave timer and the
    user's explicit Save Workspace capture workspace shape; a rename that
    recaptured would persist whatever transient shape happened to be live at
    the moment a cosmetic label changed (a half-launched group, a tab the user
    was about to close). The next capture by either real writer picks the new
    label up from the live workspace record, so the saved slot catches up
    within one autosave interval — or immediately, if the user saves.
    """
    data = request.get_json(silent=True) or {}
    try:
        resolved_workspace_id = normalize_workspace_id(workspace_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if "label" not in data:
        return jsonify({"error": "A 'label' is required"}), 400

    # Claimed, not checked (ISSUE-2026-042). The renamed workspace's own live
    # record and its own saved slot are the same identity, never a conflict
    # (SGP-13), which is what the exclusion inside the claim is for.
    try:
        workspace = rename_workspace_label(resolved_workspace_id, data.get("label"))
    except WorkspaceRequestError as exc:
        return jsonify({"error": str(exc), **exc.payload}), exc.status

    groups = session_manager.get_workspace_groups(resolved_workspace_id)
    return jsonify(public_workspace_payload(workspace, len(groups)))


@app.route('/api/workspaces/<workspace_id>', methods=['DELETE'])
def close_workspace(workspace_id: str):
    """Close one live workspace — the *Close live workspace* verb.

    Ends every session in the workspace and removes its record, while leaving
    whatever autosave or **Save Workspace** captured on offer in the restore
    chooser. ``?forget=true`` is the *Close and forget* variant and removes the
    saved slot too; a failed forget answers a retryable 503 and says plainly
    that the close itself already happened.

    This is deliberately a different verb from closing the last group (which
    forgets the slot, because the workspace emptied itself) and from closing a
    window (which changes nothing live). It is also how a deliberately empty
    workspace ends: with nothing to close it just releases the reservation.
    """
    forget = str(request.args.get("forget", "")).strip().lower() in {"1", "true", "yes"}
    payload, status = close_live_workspace(workspace_id, forget=forget)
    return jsonify(payload), status


@app.route('/api/workspaces/<workspace_id>/save', methods=['POST'])
def save_workspace(workspace_id: str):
    """Save one live workspace from the surface that lists them all (SGP-14).

    The launcher's per-row **Save**: the Stage 4 flush handshake scoped to this
    one workspace, then a capture of only its slot. It flushes the owning
    window first or refuses — a workspace with no reachable window is reported,
    never captured from stale server state — and it never writes reusable
    presets and never terminates anything.
    """
    try:
        resolved_workspace_id = normalize_workspace_id(workspace_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    payload, status = prepare_workspace_save(
        session_manager,
        resolved_workspace_id,
        lambda target_id, request_id: socketio.emit(
            "lifecycle_flush_requested",
            {"request_id": request_id, "workspace_id": target_id},
            room=workspace_room(target_id),
        ),
    )
    return jsonify(payload), status


@app.route('/api/session-groups/<group_id>/move', methods=['POST'])
def move_session_group(group_id: str):
    """Move one live session tab to another workspace without restarting it."""
    data = request.get_json(silent=True) or {}
    payload, status = move_group_to_workspace(group_id, data)
    return jsonify(payload), status


@app.route('/api/session-presentation', methods=['POST'])
def update_session_presentation():
    """Compare-and-swap one bounded live group-presentation snapshot."""
    try:
        payload, status = apply_group_presentation(
            session_manager,
            request.get_json(silent=True),
        )
    except PresentationValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload), status


@app.route('/api/workspace-presentation', methods=['POST'])
def update_workspace_presentation():
    """Compare-and-swap bounded workspace-window presentation state."""
    try:
        payload, status = apply_workspace_presentation(
            session_manager,
            request.get_json(silent=True),
        )
    except PresentationValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(payload), status


@app.route('/api/session-groups', methods=['GET'])
def get_session_groups():
    """Return launched groups for the requested workspace."""
    try:
        workspace_id = _resolve_workspace_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not _workspace_exists(workspace_id):
        return jsonify(workspace_missing_payload()), 400
    groups = [
        group.to_dict()
        for group in session_manager.get_workspace_groups(workspace_id)
    ]
    workspace_presentation = session_manager.get_workspace_presentation(workspace_id)
    return jsonify({
        "workspace_id": workspace_id,
        "groups": groups,
        "count": len(groups),
        "topbar_visible": workspace_presentation["topbar_visible"],
        "md_preset": workspace_presentation["md_preset"],
        "md_font": workspace_presentation["md_font"],
        "source_font": workspace_presentation["source_font"],
        "workspace_presentation_revision": workspace_presentation[
            "presentation_revision"
        ],
    })


@app.route('/api/session-groups/order', methods=['POST'])
def reorder_session_groups():
    """Persist the requested session-tab order."""
    data = request.get_json(silent=True) or {}
    group_ids = data.get("group_ids")
    if not isinstance(group_ids, list) or not group_ids:
        return jsonify({"error": "A non-empty 'group_ids' list is required"}), 400

    try:
        workspace_id = _resolve_workspace_id(data.get("workspace_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not _workspace_exists(workspace_id):
        return jsonify(workspace_missing_payload()), 400
    try:
        groups = [
            group.to_dict()
            for group in session_manager.reorder_groups(workspace_id, group_ids)
        ]
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _broadcast_session_groups_updated("reordered", workspace_id=workspace_id)
    return jsonify({
        "workspace_id": workspace_id,
        "groups": groups,
        "count": len(groups),
    })


@app.route('/api/session-groups/active', methods=['POST'])
def set_active_session_group():
    """Record which group the session window has in front.

    A workspace-shape hint, not session state: nothing about the group changes,
    so there is no broadcast. It exists because the autosave *timer* has no
    window to ask — without it, only an explicit Save Workspace could know which
    group to reopen on. An unknown id leaves the previous hint standing.
    """
    data = request.get_json(silent=True) or {}
    try:
        workspace_id = _resolve_workspace_id(data.get("workspace_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    group_id = str(data.get("group_id") or "").strip()
    try:
        active_group_id = session_manager.set_active_group(
            workspace_id,
            group_id,
            require_owned="workspace_id" in data,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "workspace_id": workspace_id,
        "active_group_id": active_group_id,
    })


@app.route('/api/runtime-state', methods=['GET'])
def get_runtime_state():
    """Return the restorable previous-workspace slot, if any (10.5).

    Eligibility is entirely backend-side: the slot is offered whenever it has
    groups and a restorable origin — permanently, and regardless of whether
    groups are currently live (a window may restore into itself).
    """
    try:
        workspace_id = _resolve_workspace_id(request.args.get("workspace_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    slot = load_restorable_workspace(workspace_id)
    active_groups = session_manager.get_workspace_groups(workspace_id)
    return jsonify({
        "restorable": bool(slot),
        "workspace_id": workspace_id,
        "label": slot.get("label") if slot else None,
        "origin": slot.get("origin") if slot else None,
        "saved_at": slot.get("saved_at") if slot else None,
        "groups": slot.get("groups", []) if slot else [],
        # The group the restore should reopen on; "" means no preference.
        "active_group_id": slot.get("active_group_id", "") if slot else "",
        # Optional desktop session-window zoom; null means no preference.
        "native_zoom_factor": slot.get("native_zoom_factor") if slot else None,
        "topbar_visible": slot.get("topbar_visible", True) if slot else True,
        "md_preset": slot.get("md_preset", "default") if slot else "default",
        "md_font": slot.get("md_font", "system") if slot else "system",
        "source_font": slot.get("source_font", "default") if slot else "default",
        "active_group_count": len(active_groups),
    })


@app.route('/api/runtime-state/save', methods=['POST'])
def save_runtime_state():
    """Capture the workspace now (Workspace ▸ Save Workspace), origin manual.

    The saving window names its own front group in ``active_group_id``; it is
    also recorded as the live hint so the next autosave agrees with this save.
    """
    data = request.get_json(silent=True) or {}
    try:
        workspace_id = _resolve_workspace_id(data.get("workspace_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not _workspace_exists(workspace_id):
        return jsonify(workspace_missing_payload()), 400
    if "topbar_visible" in data and not isinstance(data["topbar_visible"], bool):
        return jsonify({"error": "'topbar_visible' must be a boolean"}), 400
    label = str(data.get("label") or "").strip() or None
    active_group_id = session_manager.set_active_group(
        workspace_id,
        data.get("active_group_id"),
    )
    topbar_visible = session_manager.get_topbar_visible(workspace_id)
    if "topbar_visible" in data:
        topbar_visible = session_manager.set_topbar_visible(
            workspace_id,
            data["topbar_visible"],
        )
    try:
        slot = capture_workspace(
            session_manager,
            workspace_id=workspace_id,
            origin="manual",
            label=label,
            active_group_id=active_group_id,
            native_zoom_factor=data.get("native_zoom_factor"),
            topbar_visible=topbar_visible,
        )
    except RuntimeStatePersistenceError as exc:
        # The revision never reached the disk. Never answer 200/"saved": a
        # success toast for data that was not stored is worse than an error.
        logger.error("Workspace save failed for %s: %s", workspace_id, exc)
        return jsonify({
            "saved": False,
            "workspace_id": workspace_id,
            "error": "The workspace snapshot could not be written to disk",
            "retryable": True,
        }), 503
    if slot is None:
        # An empty workspace is never captured, so it never overwrites (or
        # clears) the previously saved slot.
        return jsonify({"saved": False, "workspace_id": workspace_id}), 409
    return jsonify({
        "saved": True,
        "workspace_id": slot["workspace_id"],
        "label": slot["label"],
        "origin": slot["origin"],
        "saved_at": slot["saved_at"],
        "active_group_id": slot["active_group_id"],
        "native_zoom_factor": slot.get("native_zoom_factor"),
        "topbar_visible": slot["topbar_visible"],
        "md_preset": slot["md_preset"],
        "md_font": slot["md_font"],
        "source_font": slot["source_font"],
        "groups": slot["groups"],
    })


@app.route('/api/runtime-state/workspaces', methods=['GET'])
def get_restorable_workspaces():
    """Return every restorable slot as a credential-free summary.

    Summaries only: no launch config and no credential ever leaves the process
    (ISSUE-2026-037). ``live_conflict`` marks a row the endpoint would refuse
    because that workspace already has live tabs.
    """
    summaries = list_restorable_workspace_summaries()
    return jsonify({"workspaces": summaries, "count": len(summaries)})


@app.route('/api/runtime-state/restore', methods=['POST'])
def restore_runtime_state():
    """Relaunch a subset of saved workspaces server-side.

    Unselected slots are untouched and stay on offer. The response reports that
    a relaunch *started*: SSH authentication is asynchronous and its outcome
    keeps arriving through the existing room-scoped ``session_status`` events
    and their retry affordance.
    """
    data = request.get_json(silent=True) or {}
    payload, status = restore_workspaces(data.get("workspace_ids"))
    return jsonify(payload), status


@app.route('/api/runtime-state', methods=['DELETE'])
def dismiss_runtime_state():
    """Forget one saved workspace snapshot (idempotent, sibling-preserving).

    Snapshot only: ``runtime_state.json`` and ``saved_sessions.json`` are
    separate stores, and other slots may reference the same preset, so no saved
    session is ever deleted here. A live workspace is refused — autosave would
    simply re-capture it, and a Forget that comes back is worse than no Forget.
    """
    try:
        workspace_id = _resolve_workspace_id(
            request.args.get("workspace_id")
            or (request.get_json(silent=True) or {}).get("workspace_id")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if workspace_has_groups(workspace_id):
        return jsonify({
            "error": "Close this workspace first",
            "forgotten": False,
            "workspace_id": workspace_id,
            "live_conflict": True,
        }), 409
    try:
        forgotten = clear_workspace(workspace_id)
    except RuntimeStatePersistenceError as exc:
        # The slot may still be on disk, so the Forget did not happen.
        logger.error("Workspace forget failed for %s: %s", workspace_id, exc)
        return jsonify({
            "error": "The workspace snapshot could not be removed from disk",
            "forgotten": False,
            "workspace_id": workspace_id,
            "retryable": True,
        }), 503
    return jsonify({
        "message": "Workspace snapshot cleared" if forgotten else "No saved snapshot to clear",
        "forgotten": forgotten,
        "workspace_id": workspace_id,
    })


# ==================== Workspace autosave (10.5 hardening) ====================

_workspace_autosave_started = False
_workspace_autosave_lock = threading.Lock()
# One structured error per failure streak, not one per tick: a full disk or a
# locked file fails every minute, and a log the user cannot read is no help.
WORKSPACE_AUTOSAVE_ERROR_INTERVAL_SECONDS = 15 * 60
_workspace_autosave_last_error_at = 0.0


def _report_autosave_failure(exc: BaseException) -> None:
    """Log one rate-limited, structured autosave failure.

    Nothing was committed and the previous file is still the last good one, so
    the failure is reported rather than retried: the next tick tries again.
    """
    global _workspace_autosave_last_error_at
    now = time.time()
    if now - _workspace_autosave_last_error_at < WORKSPACE_AUTOSAVE_ERROR_INTERVAL_SECONDS:
        logger.debug("Workspace autosave still failing: %s", exc)
        return
    _workspace_autosave_last_error_at = now
    logger.error(
        "Workspace autosave could not commit (kind=%s): %s — the last good "
        "snapshot is unchanged and the next tick will retry",
        type(exc).__name__,
        exc,
    )


def _run_workspace_autosave_tick() -> None:
    """One autosave pass: capture every live workspace that has groups.

    Empty workspaces are skipped (never cleared), so an idle launcher or a
    just-restarted process can never wipe the restorable slot — a slot is only
    ever overwritten by the next non-empty capture.
    """
    global _workspace_autosave_last_error_at
    try:
        capture_live_workspaces(session_manager, origin="auto")
    except RuntimeStatePersistenceError as exc:
        _report_autosave_failure(exc)
        return
    except Exception as exc:
        _report_autosave_failure(exc)
        logger.debug("Workspace autosave traceback", exc_info=True)
        return
    _workspace_autosave_last_error_at = 0.0


def _workspace_autosave_loop(stop_event: threading.Event) -> None:
    """Daemon loop; re-reads the interval from runtime config each tick."""
    while True:
        interval_minutes = runtime_config.workspace_autosave_interval_minutes
        if stop_event.wait(max(1, interval_minutes) * 60):
            return
        _run_workspace_autosave_tick()


def start_workspace_autosave() -> bool:
    """Start the single workspace-autosave daemon thread (idempotent)."""
    global _workspace_autosave_started
    with _workspace_autosave_lock:
        if _workspace_autosave_started:
            return False
        _workspace_autosave_started = True
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_workspace_autosave_loop,
        args=(stop_event,),
        name="workspace-autosave",
        daemon=True,
    )
    thread.start()
    logger.info("Workspace autosave started (interval from workspace.autosave_interval_minutes)")
    return True


@app.route('/api/session-config', methods=['GET'])
def get_session_config():
    """Load the launcher configuration from the local override or project default."""
    return jsonify(load_session_config())


def _saved_sessions_write_failure(action: str, exc: Exception):
    """Answer a failed ``saved_sessions.json`` commit as retryable, never 200.

    The preset store is the only file that holds an encrypted SSH password, so a
    write that did not reach the disk must not be echoed back as saved. The
    message deliberately carries the failure, not the payload — no preset name,
    host, or secret reaches the client or the log line.
    """
    logger.error("Saved-session %s failed: %s", action, exc)
    return jsonify({
        "saved": False,
        "error": "The saved sessions could not be written to disk",
        "retryable": True,
    }), 503


@app.route('/api/session-config', methods=['POST'])
def persist_session_config():
    """Persist the last-used saved session selection."""
    data = request.get_json(silent=True) or {}
    try:
        set_last_saved_session(data.get("saved_session_id"))
    except SavedSessionsPersistenceError as exc:
        return _saved_sessions_write_failure("selection", exc)
    return jsonify(load_session_config())


@app.route('/api/select-folder', methods=['POST'])
def select_folder():
    """Open a native folder picker for local repo mode."""
    data = request.get_json(silent=True) or {}

    try:
        selected = _pick_local_folder(str(data.get("initial_dir") or ""))
    except RuntimeError as exc:
        if str(exc) == "Native folder picker support is unavailable":
            logger.info("Native folder picker unavailable; local repo path can be entered manually")
            return jsonify({
                "path": "",
                "selected": False,
                "manual_entry": True,
                "error": str(exc),
            })
        logger.error(f"Folder picker failed: {exc}")
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:
        logger.error(f"Folder picker failed: {exc}")
        return jsonify({"error": str(exc)}), 500

    return jsonify({"path": selected, "selected": bool(selected)})


@app.route('/api/wsl-distros', methods=['GET'])
def get_wsl_distros():
    """Return the locally available WSL distros from `wsl -l -v`."""
    snapshot = _inspect_wsl_distributions()
    return jsonify(snapshot)


@app.route('/api/ssh-ping', methods=['POST'])
def ssh_ping():
    """Ping the launcher SSH target from the local machine."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(_ping_ssh_target(data.get("host"), data.get("port", 22)))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route('/api/agent-preflight', methods=['POST'])
def agent_preflight():
    """Run selection-time CLI detection for one configured agent target."""
    data = request.get_json(silent=True) or {}
    agent_key = _normalize_agent_key(data.get("agent"))
    if not agent_key or agent_key == "other":
        return jsonify({"error": "Select a known agent before running preflight."}), 400

    try:
        return jsonify(_agent_preflight_payload(agent_key, data))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route('/api/session-targets', methods=['GET'])
def get_session_targets():
    """Return the distinct connection targets the saved presets already use.

    The launcher's SSH Remote / Local Repo dropdowns prefill Step 2 from this so
    a throwaway session can reuse a known host or repository path without
    loading (and then being tied to) the whole preset. Secret-free by design —
    the picked target's saved password, if any, comes from
    ``GET /api/saved-sessions/<id>``.
    """
    return jsonify(build_connection_target_proposals())


@app.route('/api/saved-sessions', methods=['GET'])
def get_saved_sessions():
    """Return all named saved launcher presets."""
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    sessions = [_saved_session_response(entry) for entry in state["sessions"]]
    return jsonify(
        {
            "sessions": sessions,
            "count": len(sessions),
            "last_session": state["last_session"],
            "saved_session": _saved_session_meta(last_entry),
            "default_session": _saved_session_response(_default_saved_session_entry()),
        }
    )


@app.route('/api/saved-sessions', methods=['POST'])
def create_saved_session():
    """Persist one named saved launcher preset."""
    data = request.get_json(silent=True) or {}
    raw_config = data.get("config") if isinstance(data.get("config"), dict) else {}
    config = _normalize_session_config(raw_config)
    group_id = str(data.get("group_id") or "").strip()
    if data.get("workspace_only") is True:
        state = _load_saved_sessions_payload()
        source_session_id = str(
            data.get("id") or data.get("source_saved_session_id") or ""
        ).strip()
        source_entry = (
            _default_saved_session_entry()
            if source_session_id == DEFAULT_SAVED_SESSION_ID
            else _find_saved_session_entry(state["sessions"], source_session_id)
        )
        if source_entry:
            config = _merge_workspace_session_config(source_entry["config"], raw_config)
        # The browser never sends a password (it does not have one), so a group
        # saved from the terminal page — especially a launcher-form group with
        # no source preset — would otherwise store a credential-free preset and
        # fail every pane on the next restore. Resolved in-process from the live
        # group and encrypted by `upsert_saved_session`; it is not echoed back.
        if group_id:
            config = apply_live_ssh_credential(
                config, session_manager.group_ssh_credential(group_id)
            )
    activate_saved_session = data.get("activate", True) is not False
    try:
        saved_entry = upsert_saved_session(
            config=config,
            name=data.get("name"),
            session_id=data.get("id"),
            set_last_session=activate_saved_session,
        )
    except SavedSessionsPersistenceError as exc:
        return _saved_sessions_write_failure("save", exc)
    live_view_update = {}
    if data.get("workspace_only") is True:
        live_view_update = {
            "layout": saved_entry["config"].get("layout"),
            "workspace_layout": saved_entry["config"].get("workspace_layout"),
            "session_view_updates": build_live_session_view_updates(
                raw_config,
                saved_entry["config"],
            ),
        }
    group = (
        session_manager.update_group_saved_session(
            group_id,
            saved_entry["id"],
            saved_entry["name"],
            **live_view_update,
        )
        if group_id
        else None
    )
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    response = _saved_session_response(saved_entry, include_config=True)
    if data.get("workspace_only") is True:
        # The terminal page reads only the id, name, and live group off this
        # response — it has no password field to repopulate, unlike the launcher
        # form. A workspace save resolves the credential server-side, so echoing
        # it back would ship a password the caller never sent and cannot use.
        response["config"] = {
            **response["config"],
            "ssh": {**response["config"]["ssh"], "password": ""},
        }
    return jsonify(
        {
            **response,
            "last_session": state["last_session"],
            "saved_session": _saved_session_meta(last_entry),
            "activated": activate_saved_session,
            "group": group.to_dict() if group else None,
        }
    ), 201


@app.route('/api/saved-sessions', methods=['DELETE'])
def remove_saved_sessions():
    """Delete one or more saved launcher presets."""
    data = request.get_json(silent=True) or {}
    raw_ids = data.get("ids")

    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"error": "At least one saved session id is required"}), 400

    try:
        state = delete_saved_sessions(raw_ids)
    except SavedSessionsPersistenceError as exc:
        # A delete that did not reach the disk used to answer "updated
        # successfully" while the presets were still there (SGP-05).
        return _saved_sessions_write_failure("delete", exc)
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    return jsonify(
        {
            "message": "Saved sessions updated successfully",
            "deleted_ids": [str(session_id).strip() for session_id in raw_ids if str(session_id).strip()],
            "sessions": [_saved_session_response(entry) for entry in state["sessions"]],
            "count": len(state["sessions"]),
            "last_session": state["last_session"],
            "saved_session": _saved_session_meta(last_entry),
            "config": last_entry["config"] if last_entry else _default_session_config(),
        }
    )


@app.route('/api/saved-sessions/<saved_session_id>', methods=['GET'])
def get_saved_session(saved_session_id: str):
    """Return one named saved launcher preset."""
    if saved_session_id == DEFAULT_SAVED_SESSION_ID:
        return jsonify(_saved_session_response(_default_saved_session_entry(), include_config=True))

    for entry in load_saved_sessions():
        if entry["id"] == saved_session_id:
            return jsonify(_saved_session_response(entry, include_config=True))

    return jsonify({"error": "Saved session not found"}), 404


@app.route('/api/sessions', methods=['POST'])
def create_sessions():
    """Create one or more terminal sessions in the requested workspace.

    Request body:
    {
        "sessions": [
            {
                "host": "server1",
                "directory": "/home/user/repo",
                "username": "ubuntu",
                "password": "",
                "port": 22,
                "initial_command": "codex",
                "title": "Terminal 2"
            }
        ],
        "workspace_id": "<id>",            // or:
        "new_workspace": true,
        "workspace_label": "Reviews"
    }

    Thin route: the launch itself lives in ``web/workspaces.py`` so restore
    builds panes through exactly the same code path.
    """
    def _remember_launch_options(options):
        # Atomic reference swap instead of in-place update so concurrent
        # readers never observe a half-updated layout/count pair.
        global active_launch_options
        active_launch_options = {**active_launch_options, **options}

    data = request.get_json(silent=True)
    logger.info("POST /api/sessions %s", _redacted_launch_summary(data or {}))
    payload, status = launch_session_group(data, on_launch_options=_remember_launch_options)
    return jsonify(payload), status


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id: str):
    """Get a specific session by ID."""
    session = session_manager.get_session(session_id)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    return jsonify(session.to_dict())


@app.route('/api/sessions/<session_id>/split', methods=['POST'])
def split_session(session_id: str):
    """Append one cloned terminal session to the source session's group.

    A terminal pane clones itself. An explorer or browser pane instead splits
    into a plain terminal rooted at the directory it is currently showing, for
    both SSH and Local Repo panes — the pane kind is deliberately not cloned.
    """
    source = session_manager.get_session(session_id)
    if not source:
        return jsonify({"error": "Session not found"}), 404

    group = session_manager.get_group(source.group_id)
    if not group:
        return jsonify({"error": "Session group not found"}), 404

    group_sessions = session_manager.get_group_sessions(group.group_id)
    # One captured limit for the verdict and for the sentence that quotes it:
    # reading it twice let a refresh between them refuse against one cap and
    # then tell the user to raise a different one.
    max_sessions = runtime_config.snapshot().max_sessions
    if len(group_sessions) >= max_sessions:
        return jsonify({
            "error": capacity_refusal(len(group_sessions) + 1, max_sessions)
        }), 400

    host = source.host
    # A terminal pane clones where it *is*, not where it started: splitting a
    # navigated shell used to hand the new pane the launch directory. An
    # explorer or browser pane falls into the branch below, which resolves the
    # directory it is currently showing instead.
    directory, _cwd_source = effective_directory(session_id, source)
    directory = directory or source.directory
    root_directory = (
        source.explorer_root_directory if source.explorer_root_configured else ""
    )
    startup_mode = source.startup_mode

    if _is_explorer_session(source) or _is_browser_session(source):
        data = request.get_json(silent=True) or {}
        try:
            directory, root_directory = _resolve_pane_terminal_directory(
                source,
                data.get("directory", ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except _sftp_request_error_types() as exc:
            return jsonify({"error": str(exc)}), 500
        startup_mode = "terminal"
        if source.mode == "wsl":
            # The pane's host label reads "File Explorer"/browser chrome; the new
            # terminal needs the shell name it is actually going to run.
            host = _local_shell_display_name(
                use_wsl=source.use_wsl,
                use_powershell=source.use_powershell,
                distribution=source.distribution,
            )

    title = f"Terminal {len(group_sessions) + 1}"
    new_session = session_manager.append_session_to_group(
        group_id=group.group_id,
        host=host,
        directory=directory,
        username=source.username,
        port=source.port,
        password=source.password,
        initial_command=None,
        initial_command_mode="command",
        agent_selection="",
        custom_agent="",
        title=title,
        mode=source.mode,
        distribution=source.distribution,
        use_wsl=source.use_wsl,
        use_powershell=source.use_powershell,
        startup_mode=startup_mode,
        explorer_root_directory=root_directory,
        # Stated rather than derived: the clone carries a root only when the
        # source's was configured, so the new pane inherits that pin even
        # though a terminal pane's own root would read as a derived one.
        explorer_root_configured=bool(root_directory),
    )
    if not new_session:
        return jsonify({"error": "Session group not found"}), 404

    logger.info(
        "Split session source_id=%s new_session_id=%s group_id=%s",
        source.session_id,
        new_session.session_id,
        group.group_id,
    )
    socketio.start_background_task(_connect_session, new_session.session_id)
    _broadcast_session_groups_updated(
        "split",
        group_id=group.group_id,
        workspace_id=group.workspace_id,
    )

    return jsonify(
        {
            "session": new_session.to_dict(),
            "group_id": group.group_id,
            "group": group.to_dict(),
            "terminal_count": group.terminal_count,
        }
    ), 201


@app.route('/api/sessions/<session_id>/reconnect', methods=['POST'])
def reconnect_session(session_id: str):
    """Retry the connection of an errored or disconnected session in place."""
    session = session_manager.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    if session.status not in (SessionStatus.ERROR, SessionStatus.DISCONNECTED):
        return jsonify(
            {"error": f"Session is {session.status.value}; only errored or disconnected sessions can reconnect"}
        ), 409

    logger.info("Reconnect requested session_id=%s previous_status=%s", session_id, session.status.value)
    _close_ssh_connection(session_id, clear_buffer=True)
    session_manager.update_session_status(session_id, SessionStatus.PENDING)
    _broadcast_session_status(session_id)
    socketio.start_background_task(_connect_session, session_id)

    return jsonify(session_manager.get_session(session_id).to_dict())


@app.route('/api/sessions/<session_id>/shell', methods=['POST'])
def change_session_shell(session_id: str):
    """Relaunch one terminal pane under another shell family and/or agent.

    HTTP adaptation only: the transaction lives in `web/session_shell.py`. The
    three side effects are resolved here rather than imported there because
    they belong to this module's Socket.IO server and connection registry --
    and looking them up in this body is what keeps them the same patch points
    they have always been.
    """
    try:
        payload = apply_pane_shell_change(
            session_id,
            request.get_json(silent=True) or {},
            ShellTransitionEffects(
                close_connection=_close_ssh_connection,
                broadcast_status=_broadcast_session_status,
                start_connector=lambda pane_session_id: socketio.start_background_task(
                    _connect_session, pane_session_id
                ),
            ),
        )
    except ShellTransitionError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(payload)


@app.route('/api/sessions/<session_id>/mode', methods=['POST'])
def change_session_mode(session_id: str):
    """Switch one pane between terminal, file explorer, and browser modes.

    HTTP adaptation only: the transition itself lives in
    `web/session_modes.py`. The three side effects are resolved here rather
    than imported there because they belong to this module's Socket.IO server
    and connection registry — and looking them up in this body is what keeps
    them the same patch points they have always been.
    """
    try:
        payload = apply_pane_mode_change(
            session_id,
            request.get_json(silent=True) or {},
            ModeTransitionEffects(
                close_connection=_close_ssh_connection,
                broadcast_status=_broadcast_session_status,
                start_connector=lambda pane_session_id: socketio.start_background_task(
                    _connect_session, pane_session_id
                ),
            ),
        )
    except ModeTransitionError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(payload)


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def close_session(session_id: str):
    """Close one pane — the last-pane half of the *Close group* verb.

    Closing the last pane empties its group, and an emptied last group empties
    its workspace: the record goes and its saved snapshot goes with it, exactly
    as closing that tab would. See the close-action matrix in
    ``web/workspaces.py``. Use ``DELETE /api/workspaces/<id>`` for the variant
    that keeps the snapshot restorable.
    """
    with session_manager.lock:
        existing_session = session_manager.sessions.get(session_id)
        group = (
            session_manager.groups.get(existing_session.group_id)
            if existing_session is not None
            else None
        )
        success = session_manager.close_session(session_id)
        if success:
            group_id = existing_session.group_id
            # Closing a pane empties its group, and an empty group is swept at
            # once — there is no grace period left to ride (MW-06). Closing the
            # last pane within five seconds of launch used to remove the
            # session but leave the group behind forever, which kept a
            # workspace alive with no panes in it and its snapshot
            # unforgettable.
            pruned_workspace_ids = session_manager.clear_disconnected_sessions()
            workspace_id = (
                group.workspace_id if group else DEFAULT_WORKSPACE_ID
            )
        else:
            pruned_workspace_ids = []
            group_id = ""
            workspace_id = DEFAULT_WORKSPACE_ID
    if not success:
        return jsonify({"error": "Session not found"}), 404

    _close_ssh_connection(session_id, clear_buffer=True)
    forget_pruned_workspaces(pruned_workspace_ids)
    # Closing the last pane of the last group in `default` empties it just as
    # surely as a prune empties a sibling; its snapshot goes the same way.
    forget_emptied_default_workspace(workspace_id)
    _broadcast_session_groups_updated(
        "session_closed",
        group_id=group_id,
        workspace_id=workspace_id,
    )
    for pruned_workspace_id in pruned_workspace_ids:
        if pruned_workspace_id != workspace_id:
            _broadcast_session_groups_updated(
                "workspace_pruned",
                workspace_id=pruned_workspace_id,
            )

    return jsonify({"message": "Session closed successfully"})


@app.route('/api/sessions', methods=['DELETE'])
def close_all_sessions():
    """Close one group, or every session in the process.

    With ``?group=`` this is the *Close group* verb: the group goes, and if it
    was its workspace's last one the workspace and its snapshot go too.

    Without one it is the process-wide variant of *Close window*: live shells
    end everywhere and **every snapshot is deliberately left on offer**, which
    is what makes restore-after-restart work. See the close-action matrix in
    ``web/workspaces.py``.
    """
    workspace_named = "workspace_id" in request.args
    try:
        workspace_id = _resolve_workspace_id()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    group_id = _resolve_group_id()
    if group_id:
        close_error = ""
        close_status = 400
        with session_manager.lock:
            group = session_manager.groups.get(group_id)
            if group is not None and group.workspace_id != workspace_id:
                close_error = "Session group does not belong to workspace"
            elif workspace_named and group is None:
                close_error = "Session group does not belong to workspace"
            sessions = session_manager.get_group_sessions(group_id)
            if not close_error and not sessions and group is None:
                close_error = "Session group not found"
                close_status = 404
            if not close_error:
                for session in sessions:
                    session_manager.close_session(session.session_id)
                # Its panes are closed, so the group is empty and the sweep
                # takes it immediately (MW-06).
                pruned_workspace_ids = session_manager.clear_disconnected_sessions()
                closed_workspace_id = (
                    group.workspace_id if group else workspace_id
                )
                closed_session_ids = [
                    session.session_id for session in sessions
                ]
            else:
                pruned_workspace_ids = []
                closed_workspace_id = workspace_id
                closed_session_ids = []
        if close_error:
            return jsonify({"error": close_error}), close_status
        for session_id in closed_session_ids:
            _close_ssh_connection(session_id, clear_buffer=True)
        # Closing the last group removes the workspace globally: the live record
        # is already gone, so its saved snapshot must go too or the restore
        # chooser keeps offering a workspace the launcher no longer lists.
        forget_pruned_workspaces(pruned_workspace_ids)
        forget_emptied_default_workspace(closed_workspace_id)
        _broadcast_session_groups_updated(
            "group_closed",
            group_id=group_id,
            workspace_id=closed_workspace_id,
        )
        for pruned_workspace_id in pruned_workspace_ids:
            if pruned_workspace_id != closed_workspace_id:
                _broadcast_session_groups_updated(
                    "workspace_pruned",
                    workspace_id=pruned_workspace_id,
                )
        return jsonify({"message": "Session group closed successfully", "group_id": group_id})

    affected_workspace_ids = [
        workspace.workspace_id
        for workspace in session_manager.get_all_workspaces()
        if session_manager.get_workspace_groups(workspace.workspace_id)
    ] or [DEFAULT_WORKSPACE_ID]
    session_manager.close_all_sessions()
    _close_all_ssh_connections(clear_buffers=True)
    session_manager.reset_sessions()
    for affected_workspace_id in affected_workspace_ids:
        _broadcast_session_groups_updated(
            "all_closed",
            workspace_id=affected_workspace_id,
        )

    return jsonify({"message": "All sessions closed successfully"})


# ==================== WebSocket Events ====================

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info(f"Client connected: {request.sid}") # type: ignore


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info(f"Client disconnected: {request.sid}") # type: ignore
    _clear_client_joined_sessions(request.sid) # type: ignore
    # A recording the client never stopped (closed tab, crash, suspended
    # laptop) is released here; nothing is emitted, the client is gone.
    abandon_client_voice_sessions(request.sid) # type: ignore
    lifecycle_coordinator.disconnect_client(request.sid) # type: ignore


@socketio.on('join_workspace')
def handle_join_workspace(data):
    """Join the room that carries one workspace's group-list updates."""
    try:
        workspace_id = normalize_workspace_id(
            data.get("workspace_id") if isinstance(data, dict) else None
        )
    except ValueError as exc:
        logger.warning(f"join_workspace rejected: {exc}")
        return
    if not _workspace_exists(workspace_id):
        logger.warning(f"join_workspace: workspace not found: {workspace_id}")
        return
    join_room(workspace_room(workspace_id))
    lifecycle_coordinator.join_workspace(
        request.sid, # type: ignore
        workspace_id,
        data.get("window_id") if isinstance(data, dict) else None,
    )


@socketio.on('leave_workspace')
def handle_leave_workspace(data):
    """Leave a workspace update room."""
    try:
        workspace_id = normalize_workspace_id(
            data.get("workspace_id") if isinstance(data, dict) else None
        )
    except ValueError as exc:
        logger.warning(f"leave_workspace rejected: {exc}")
        return
    leave_room(workspace_room(workspace_id))
    lifecycle_coordinator.leave_workspace(request.sid, workspace_id) # type: ignore


@socketio.on('lifecycle_flush_ack')
def handle_lifecycle_flush_ack(data):
    """Accept a room-scoped lifecycle flush result from this socket only."""
    lifecycle_coordinator.acknowledge_flush(request.sid, data) # type: ignore


_TERMINAL_QUERY_RE = re.compile(
    r'\x1b\['       # CSI
    r'[>=]?'        # DA2 (>) or DA3 (=) prefix, optional
    r'(?:0?c|\?[0-9;]*c)'  # Device Attributes request or response
    r'|'
    r'\x1b\[[56]n'  # Device Status Report / Cursor Position Report
    r'|'
    r'\x1b\](?:1[012]);\?(?:\x07|\x1b\\)'  # OSC foreground/background/cursor color query
)


@socketio.on('join_session')
def handle_join_session(data):
    """
    Join a terminal session room.

    Expected data:
    {
        "session_id": "abc123"
    }
    """
    session_id = data.get('session_id')

    if not session_id:
        logger.warning("join_session rejected: missing session_id")
        return

    session = session_manager.get_session(session_id)
    if session:
        emit('session_status', session.to_dict())

    with connection_lock:
        joined_sessions = client_joined_sessions.get(request.sid) # type: ignore
        if joined_sessions is None:
            client_joined_sessions[request.sid] = set() # type: ignore
            if len(client_joined_sessions) > _MAX_TRACKED_SOCKET_CLIENTS:
                oldest_client_id = next(iter(client_joined_sessions))
                client_joined_sessions.pop(oldest_client_id, None)
            joined_sessions = client_joined_sessions[request.sid] # type: ignore
        should_replay_buffer = session_id not in joined_sessions
        if should_replay_buffer:
            joined_sessions.add(session_id)
        join_room(session_id)
        logger.info(f"Client {request.sid} joined session {session_id}") # type: ignore
        # Snapshot the replay buffer while still holding the lock; the emit
        # happens outside it so a slow client cannot stall other terminals.
        # join_room already happened under the lock, so any output cached after
        # this point reaches the client live, after the replay.
        buffered_output = _get_buffered_terminal_output(session_id) if should_replay_buffer else ""

    if buffered_output:
        buffered_output = _TERMINAL_QUERY_RE.sub('', buffered_output)
        emit('terminal_output', {
            'session_id': session_id,
            'data': buffered_output
        })


@socketio.on('leave_session')
def handle_leave_session(data):
    """Leave a terminal session room."""
    session_id = data.get('session_id')

    if session_id:
        with connection_lock:
            joined_sessions = client_joined_sessions.get(request.sid) # type: ignore
            if joined_sessions is not None:
                joined_sessions.discard(session_id)
                if not joined_sessions:
                    client_joined_sessions.pop(request.sid, None) # type: ignore
        leave_room(session_id)
        logger.info(f"Client {request.sid} left session {session_id}")# type: ignore


@socketio.on('clear_terminal_buffer')
def handle_clear_terminal_buffer(data):
    """Clear the rolling replay buffer for one terminal session."""
    session_id = data.get('session_id')

    if not session_id:
        logger.warning("clear_terminal_buffer rejected: missing session_id")
        return

    _clear_terminal_output_buffer(session_id)


@socketio.on('terminal_input')
def handle_terminal_input(data):
    """
    Handle terminal input from web client.

    Expected data:
    {
        "session_id": "abc123",
        "data": "ls -la\n"
    }
    """
    session_id = data.get('session_id')
    input_data = data.get('data', '')

    if not session_id:
        logger.warning("terminal_input rejected: missing session_id")
        return

    with connection_lock:
        connection = ssh_connections.get(session_id)

    if not connection:
        # DEBUG, not WARNING: xterm's onData has no connection guard, so a
        # disconnected pane produces one of these per keystroke (and per TUI
        # mouse-tracking sequence). The condition already surfaces to the user
        # through session_status; here it is diagnostic only.
        logger.debug(f"terminal_input: session {session_id} is not connected")
        return

    try:
        sanitized_input = _sanitize_terminal_input(connection, input_data)
        if not sanitized_input:
            return
        # Send first, track after. The tracker's agent-promotion branch calls
        # effective_directory(), which for a remote pane with a known shell pid
        # and no shell-integration observation opens a fresh exec channel and
        # waits up to the bounded remote-CWD timeout — between the user's Enter
        # and the shell receiving it, on the handler thread. Socket.IO's default
        # async_handlers=True runs later events for the same client on separate
        # threads, so newer input could overtake the blocked keystroke.
        # Nothing in the tracker feeds the send (it only reads the sanitized
        # text), so the order is otherwise behaviour-preserving. One deliberate
        # change: if the send raises, the tracker no longer runs, so a promotion
        # cannot be recorded for input the shell never received.
        _send_connection_input(connection, sanitized_input)
        _track_terminal_agent_input(session_id, connection, sanitized_input)
    except Exception as e:
        logger.error(f"Error sending input: {e}")
        emit('terminal_output', {
            'session_id': session_id,
            'data': f'\r\nError: {str(e)}\r\n'
        })


@socketio.on('terminal_resize')
def handle_terminal_resize(data):
    """Resize the backend PTY to match the visible xterm size."""
    session_id = data.get('session_id')
    cols = data.get('cols')
    rows = data.get('rows')

    if not session_id or cols is None or rows is None:
        return

    with connection_lock:
        connection = ssh_connections.get(session_id)

    if not connection:
        return

    try:
        _resize_connection(connection, cols, rows)
    except Exception as exc:
        logger.warning(f"Failed to resize terminal {session_id}: {exc}")


# ==================== Voice Input (Vosk / faster-whisper) ====================


def _broadcast_voice_install_finished(state: Dict[str, Any]) -> None:
    """Tell open windows that voice availability changed after an install."""
    # One captured generation names the engine and answers whether it is
    # available: reading the setting twice could report one engine's name
    # beside another engine's availability.
    engine = runtime_config.snapshot().voice_engine
    socketio.emit('voice_availability_updated', {
        'engine': engine,
        'engine_available': _voice_engine_available(engine),
        'engines_available': _voice_engines_available(),
        'install': state,
        'timestamp': int(time.time() * 1000),
    })


@app.route('/api/voice-status', methods=['GET'])
def voice_status_endpoint():
    """Check voice input availability and service status."""
    settings = runtime_config.snapshot()
    if settings.voice_engine == "vosk":
        # Probe the endpoint this response is about to name, not whichever one
        # is live by the time the handshake runs.
        service_url = settings.vosk_service_url
        service_running: Optional[bool] = _vosk_service_reachable(
            timeout=1.0, service_url=service_url
        )
    else:
        service_running = None
        service_url = ""
    engine_available = _voice_engine_available(settings.voice_engine, service_running)
    status_message = (
        "Voice backend is available."
        if engine_available
        else _voice_engine_unavailable_message(settings.voice_engine, service_running)
    )
    return jsonify({
        'enabled': settings.voice_enabled,
        'engine': settings.voice_engine,
        'engine_available': engine_available,
        # Per-engine availability so App Settings can annotate the engine the
        # user is picking, not only the one currently saved.
        'engines_available': _voice_engines_available(service_running),
        'ws_client_available': _vosk_engine_available(),
        'vosk_packages_available': _vosk_service_packages_available(),
        'service_running': service_running,
        'service_url': service_url,
        'model': _active_voice_model_name(settings),
        'language': settings.voice_language,
        'startup_timeout_seconds': settings.vosk_startup_timeout_seconds,
        'whisper_device': settings.whisper_device,
        'whisper_compute_type': settings.whisper_compute_type,
        'status_message': status_message,
        'install': _voice_install_status(),
    })


@app.route('/api/voice-deps-install', methods=['GET'])
def voice_deps_install_status():
    """Return the state of the in-app voice dependency install."""
    return jsonify(_voice_install_status())


@app.route('/api/voice-deps-install', methods=['POST'])
def start_voice_deps_install():
    """Install requirements-voice.txt into the running interpreter.

    The recovery path for a machine where the launcher's optional voice
    packages were skipped: without it, enabling voice input in App Settings
    is a silent no-op until GridVibe is closed and re-launched. Same-origin only
    (the cross-origin write guard covers every state-changing request) and it
    installs nothing but the repository's own pinned requirements file.
    """
    state = _start_voice_dependency_install(on_complete=_broadcast_voice_install_finished)
    logger.info("Voice dependency install requested (status=%s)", state.get('status'))
    return jsonify(state), 202


@app.route('/api/voice-prefs', methods=['GET'])
def get_voice_prefs():
    """Return persisted voice preferences."""
    return jsonify(_load_voice_prefs())


@app.route('/api/voice-prefs', methods=['POST'])
def set_voice_prefs():
    """Persist voice preferences to config.json."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Invalid payload'}), 400
    current = _load_voice_prefs()
    for key in VOICE_PREFS_VALID_KEYS:
        if key in data:
            current[key] = data[key]
    _save_voice_prefs(current)
    # Open workspaces read _voicePrefs at event time but only loaded it at boot,
    # so a changed push-to-talk keybind used to need a full restart.
    socketio.emit('voice_prefs_updated', {
        'prefs': current,
        'timestamp': int(time.time() * 1000),
    })
    return jsonify(current)


@socketio.on('voice_start')
def handle_voice_start(data):
    """
    Start voice recognition for a terminal session.
    Uses the configured backend engine and prepares per-session state.
    """
    logger.info("voice_start requested by client %s for session %s",
                request.sid, data.get('session_id'))  # type: ignore[arg-type]
    # One captured generation decides both whether voice may start and which
    # engine starts: a refresh between the two reads could let a disabled
    # config through, or start the engine the previous generation named.
    voice_settings = runtime_config.snapshot()
    if not voice_settings.voice_enabled:
        emit('voice_status', {'status': 'error',
                              'message': 'Voice input is disabled in config'})
        return

    session_id = data.get('session_id')
    if not session_id:
        emit('voice_status', {'status': 'error', 'message': 'Missing session_id'})
        return

    engine = 'whisper' if voice_settings.voice_engine == 'whisper' else 'vosk'
    register_voice_session(request.sid, session_id, engine)  # type: ignore[arg-type]

    if engine == 'whisper':
        _start_whisper_voice_session(session_id)
        return

    _start_vosk_voice_session(session_id)


@socketio.on('voice_audio')
def handle_voice_audio(data):
    """
    Forward or buffer an audio chunk for the configured voice engine.
    Audio is raw PCM int16, 16 kHz mono.
    """
    session_id = data.get('session_id')
    audio = data.get('audio')
    if not session_id or not audio:
        return

    engine = resolve_voice_session_engine(session_id, runtime_config.voice_engine)

    if engine == 'whisper':
        _handle_whisper_audio_chunk(session_id, audio)
        return

    _handle_vosk_audio_chunk(session_id, audio)


@socketio.on('voice_stop')
def handle_voice_stop(data):
    """
    Stop voice recognition for a session.
    Flushes the configured backend and emits any final text.
    """
    session_id = data.get('session_id')
    logger.info("voice_stop requested by client %s for session %s",
                request.sid, session_id)  # type: ignore[arg-type]
    if not session_id:
        return

    engine = release_voice_session(session_id, runtime_config.voice_engine)

    if engine == 'whisper':
        _stop_whisper_voice_session(session_id)
    else:
        if _stop_vosk_voice_session(session_id) is False:
            return

    emit('voice_status', {'session_id': session_id, 'status': 'stopped'})
    logger.info("Voice stopped for session %s", session_id)


# ==================== Main Entry Point ====================

def run_server(
    host: str = "127.0.0.1",
    port: int = 5050,
    debug: bool = False,
    *,
    use_reloader: bool = False,
):
    """Run the Flask-SocketIO server.

    The single server entry point shared by `main.py`, the desktop launcher,
    and the `python api.py` shim (finding 5.7), so flags like
    `allow_unsafe_werkzeug` cannot drift between them.
    """
    logger.info(f"Starting GridVibe server on {host}:{port}")
    # The Socket.IO origins were derived from config at import time; only here is
    # the resolved bind address known, and authorising the wrong one silently
    # kills every terminal's transport (audit F1).
    apply_resolved_server_origins(host, port)
    start_workspace_autosave()
    socketio.run(
        app,
        host=host,
        port=port,
        debug=debug,
        use_reloader=use_reloader,
        allow_unsafe_werkzeug=True,
    )


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    run_server(debug=True)
