"""
Web API for GridVibe frontend integration.
Provides REST endpoints and WebSocket support for terminal sessions.
"""

import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, Optional

from flask import jsonify, render_template, request, send_from_directory
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
    session_manager,
    socketio,
)
from web.config import (
    WHISPER_MODEL_OPTIONS,
    _config_lock,
    _merge_dicts,
    _normalize_surface_mode,
    load_config,
    resolve_server_settings,  # noqa: F401 - re-exported for the entry points
    runtime_config,
    save_config,
)
from web.explorer import (  # noqa: F401 - some names re-exported for backwards compatibility
    EXPLORER_FILE_PREVIEW_MAX_BYTES,
    _acquire_ssh_sftp,
    _append_deleted_git_entries,
    _attach_git_status_to_entries,
    _clean_git_entry_status,
    _evict_all_pooled_ssh_clients,
    _evict_pooled_ssh_client,
    _explorer_backend,
    _explorer_content_looks_binary,
    _explorer_editor_language,
    _explorer_root_directory,
    _get_git_context,
    _get_git_diff,
    _get_git_repo_summary,
    _git_commit,
    _git_publish,
    _git_stage_path,
    _git_status_for_entry,
    _git_unstage_path,
    _is_browser_session,
    _is_explorer_session,
    _is_markdown_file,
    _is_remote_explorer_session,
    _release_ssh_sftp,
    _remote_explorer_root_directory,
    _remote_is_directory,
    _remote_path_clean,
    _remote_path_inside,
    _render_markdown_preview,
    _resolve_explorer_candidate_path,
    _resolve_remote_explorer_candidate_path,
    _sftp_request_error_types,
)
from web.hostkeys import (  # noqa: F401 - re-exported for backwards compatibility
    _load_persistent_host_keys,
)
from web.paths import BASE_DIR
from web.saved_sessions import (  # noqa: F401 - re-exported for backwards compatibility
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
from web.selfupdate import AppUpdateError, perform_self_update
from web.terminal_io import (  # noqa: F401 - re-exported for backwards compatibility
    _MAX_TRACKED_SOCKET_CLIENTS,
    _MAX_TRACKED_TERMINAL_COMMAND_LENGTH,
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
    _close_ssh_connection,
    _connect_local_session,
    _connect_session,
    _connect_ssh_session,
    _drain_until_prompt,
    _extract_terminal_cwd_from_buffer,
    _finalize_stream,
    _get_buffered_terminal_output,
    _local_shell_display_name,
    _mark_runtime_agent_exited,
    _normalize_local_directory,
    _normalize_probed_local_cwd,
    _replace_group_sessions,
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
    client_joined_sessions,
    connection_lock,
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
    _restart_vosk_service,
    _save_voice_prefs,
    _start_vosk_voice_session,
    _start_whisper_voice_session,
    _stop_vosk_service,
    _stop_vosk_voice_session,
    _stop_whisper_voice_session,
    _transcribe_whisper_audio,
    _voice_engine_unavailable_message,
    _vosk_engine_available,
    _vosk_lock,
    _vosk_service_reachable,
    _vosk_session_locks,
    _vosk_ws_connections,
    _wait_for_vosk_ready,
    _whisper_audio_buffers,
    _whisper_audio_lock,
    _whisper_engine_available,
    _whisper_language_code,
    _whisper_model_lock,
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


def _active_voice_model_name() -> str:
    """Return the currently configured STT model name."""
    return runtime_config.whisper_model if runtime_config.voice_engine == "whisper" else runtime_config.vosk_model


def _public_app_config() -> Dict[str, Any]:
    """Return the subset of app config that the launcher can edit safely."""
    return {
        "appearance": {
            "theme": runtime_config.app_theme,
        },
        "workspace": {
            "surface_mode": runtime_config.app_surface_mode,
        },
        "voice_input": {
            "enabled": runtime_config.voice_enabled,
            "engine": runtime_config.voice_engine,
            "vosk_model": runtime_config.vosk_model,
            "whisper_model": runtime_config.whisper_model,
            "whisper_device": runtime_config.whisper_device,
            "whisper_compute_type": runtime_config.whisper_compute_type,
            "language": runtime_config.voice_language,
        }
    }


def _broadcast_app_config_update():
    """Notify open app windows that launcher-editable settings changed."""
    socketio.emit(
        "app_config_updated",
        {
            "workspace": {
                "surface_mode": runtime_config.app_surface_mode,
            },
            "timestamp": int(time.time() * 1000),
        },
    )


def _normalize_app_config_update(data: Any) -> Dict[str, Any]:
    """Validate and normalize launcher-editable app settings."""
    payload = data if isinstance(data, dict) else {}
    appearance = payload.get("appearance")
    if not isinstance(appearance, dict):
        appearance = {}
    theme = str(appearance.get("theme", runtime_config.app_theme)).strip().lower()
    if theme not in {"system", "light", "dark"}:
        theme = runtime_config.app_theme

    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        workspace = {}
    surface_mode = _normalize_surface_mode(workspace.get("surface_mode"), runtime_config.app_surface_mode)

    voice_input = payload.get("voice_input")
    if not isinstance(voice_input, dict):
        voice_input = {}

    engine = str(voice_input.get("engine", runtime_config.voice_engine)).strip().lower()
    if engine not in {"vosk", "whisper"}:
        engine = runtime_config.voice_engine

    whisper_device_value = str(
        voice_input.get("whisper_device", runtime_config.whisper_device)
    ).strip().lower()
    if whisper_device_value not in {"cpu", "cuda"}:
        whisper_device_value = runtime_config.whisper_device

    next_whisper_model = str(
        voice_input.get("whisper_model", runtime_config.whisper_model)
    ).strip() or runtime_config.whisper_model
    if next_whisper_model not in WHISPER_MODEL_OPTIONS:
        next_whisper_model = "base"

    return {
        "appearance": {
            "theme": theme,
        },
        "workspace": {
            "surface_mode": surface_mode,
        },
        "voice_input": {
            "enabled": bool(voice_input.get("enabled", runtime_config.voice_enabled)),
            "engine": engine,
            "vosk_model": str(voice_input.get("vosk_model", runtime_config.vosk_model)).strip() or runtime_config.vosk_model,
            "whisper_model": next_whisper_model,
            "whisper_device": whisper_device_value,
            "whisper_compute_type": str(
                voice_input.get("whisper_compute_type", runtime_config.whisper_compute_type)
            ).strip() or runtime_config.whisper_compute_type,
            "language": str(voice_input.get("language", runtime_config.voice_language)).strip() or runtime_config.voice_language,
        }
    }


active_launch_options: Dict[str, Any] = {
    "connection_mode": "ssh",
    "layout": _normalize_layout("grid", min(4, runtime_config.max_sessions)),
    "terminal_count": min(4, runtime_config.max_sessions),
}


folder_dialog_lock = threading.Lock()


def _resolve_group_id() -> str:
    """Return the requested session group id, if any."""
    return str(request.args.get("group") or "").strip()


def _get_group_response_meta(group_id: str) -> Dict[str, Any]:
    """Return layout metadata for one session group."""
    group = session_manager.get_group(group_id)
    if not group:
        launch_options = active_launch_options
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
        "surface_mode": group.surface_mode,
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
    return render_template(
        'index.html',
        max_sessions=runtime_config.max_sessions,
        agent_options=_agent_options(),
        local_windows_shells_available=os.name == "nt",
        browser_shutdown_enabled=bool(browser_shutdown_token),
        browser_shutdown_token=browser_shutdown_token,
        version=__version__,
    )


@app.route('/terminals')
def terminals_page():
    """Page showing active terminal instances."""
    logger.info("GET /terminals")
    return render_template('terminals.html', max_sessions=runtime_config.max_sessions,
                           app_surface_mode=runtime_config.app_surface_mode,
                           voice_enabled=runtime_config.voice_enabled,
                           voice_engine=runtime_config.voice_engine,
                           voice_model=_active_voice_model_name(),
                           voice_language=runtime_config.voice_language,
                           terminal_font_size=runtime_config.terminal_font_size,
                           terminal_font_family=runtime_config.terminal_font_family,
                           version=__version__)


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

    logger.info("Accepted browser mode shutdown request")
    _schedule_browser_shutdown()
    return jsonify({"message": "GridVibe is shutting down"}), 202


@app.route('/api/app-update', methods=['POST'])
def update_application():
    """Check for git updates, apply them when available, and report the outcome."""
    try:
        result = perform_self_update()
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

    with _config_lock:
        current = load_config()
        current = _merge_dicts(current, _normalize_app_config_update(data))
        save_config(current)
    _refresh_runtime_config()
    _broadcast_app_config_update()
    return jsonify(_public_app_config())


@app.route('/api/sessions', methods=['GET'])
def get_sessions():
    """Get all sessions."""
    group_id = _resolve_group_id()
    sessions = (
        session_manager.get_group_sessions(group_id)
        if group_id
        else session_manager.get_all_sessions()
    )
    logger.debug(
        f"GET /api/sessions  group={group_id or 'all'} count={len(sessions)} "
        f"statuses={[s.status.value for s in sessions]}"
    )
    payload = {
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
    }
    if group_id:
        payload.update(_get_group_response_meta(group_id))
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
            "path": backend.rel_explorer_path(root_path, current_path),
            "parent_path": backend.parent_explorer_path(root_path, current_path),
            "git": git_context,
            "entries": entries,
        }

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/file', methods=['GET'])
def get_explorer_file(session_id: str):
    """Return a safe, read-only text preview for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    requested_path = request.args.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_file(requested_path)
        size, modified = backend.stat_file(file_path)
        code_language = _explorer_editor_language(file_path)
        raw_content = backend.read_file_prefix(file_path, EXPLORER_FILE_PREVIEW_MAX_BYTES + 1)

        if _explorer_content_looks_binary(raw_content):
            raise ValueError("Explorer file appears to be binary")

        truncated = len(raw_content) > EXPLORER_FILE_PREVIEW_MAX_BYTES
        preview_bytes = raw_content[:EXPLORER_FILE_PREVIEW_MAX_BYTES]
        content = preview_bytes.decode("utf-8", errors="replace")
        preview_html = _render_markdown_preview(content) if _is_markdown_file(file_path) else None
        git_context, git_statuses = _get_git_context(backend, root_path, backend.file_dirname(file_path))
        file_git = (
            _git_status_for_entry(backend, str(git_context["repo_root"]), file_path, git_statuses)
            if git_context.get("available")
            else _clean_git_entry_status()
        )
        return {
            "root": root_path,
            "path": backend.rel_explorer_path(root_path, file_path),
            "name": backend.basename(file_path),
            "size": size,
            "modified": modified,
            "encoding": "utf-8",
            "truncated": truncated,
            "content": content,
            "preview_type": "markdown" if preview_html is not None else None,
            "preview_html": preview_html,
            "language": code_language,
            "git": file_git,
            "git_context": git_context,
        }

    return _explorer_route_response(session, handler)


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
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except error_types as exc:
        return jsonify({"error": str(exc)}), 500


@app.route('/api/explorer/<session_id>/git/diff', methods=['GET'])
def get_explorer_git_diff(session_id: str):
    """Return a bounded read-only Git diff for one explorer file."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    mode = request.args.get("mode", "worktree")
    commit = request.args.get("commit")
    requested_path = request.args.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_diff_path(requested_path)
        diff_payload = _get_git_diff(backend, root_path, file_path, mode, commit)
        return {
            "root": root_path,
            "path": backend.rel_explorer_path(root_path, file_path),
            "mode": mode,
            **diff_payload,
        }

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/repo', methods=['GET'])
def get_explorer_git_repo(session_id: str):
    """Return bounded read-only Git repository metadata for the diff sidebar."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path = backend.root_directory()
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/stage', methods=['POST'])
def stage_explorer_git_file(session_id: str):
    """Stage one changed file in an explorer Git repository."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json(silent=True) or {}
    requested_path = data.get("path", "")

    def handler(backend: Any) -> Dict[str, Any]:
        root_path, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_stage_path(backend, root_path, file_path)
        summary = _get_git_repo_summary(backend, root_path)
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
        root_path, file_path = backend.resolve_candidate(requested_path, allow_empty_root=False)
        _git_unstage_path(backend, root_path, file_path)
        summary = _get_git_repo_summary(backend, root_path)
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
        root_path = backend.root_directory()
        _git_commit(backend, root_path, message)
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/explorer/<session_id>/git/publish', methods=['POST'])
def publish_explorer_git(session_id: str):
    """Push the current branch of an explorer Git repository to its remote."""
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404

    def handler(backend: Any) -> Dict[str, Any]:
        root_path = backend.root_directory()
        _git_publish(backend, root_path)
        summary = _get_git_repo_summary(backend, root_path)
        return {"root": root_path, **summary}

    return _explorer_route_response(session, handler)


@app.route('/api/session-groups', methods=['GET'])
def get_session_groups():
    """Return all launched groups for the session tabs."""
    groups = [group.to_dict() for group in session_manager.get_all_groups()]
    return jsonify({"groups": groups, "count": len(groups)})


@app.route('/api/session-groups/order', methods=['POST'])
def reorder_session_groups():
    """Persist the requested session-tab order."""
    data = request.get_json(silent=True) or {}
    group_ids = data.get("group_ids")
    if not isinstance(group_ids, list) or not group_ids:
        return jsonify({"error": "A non-empty 'group_ids' list is required"}), 400

    groups = [group.to_dict() for group in session_manager.reorder_groups(group_ids)]
    _broadcast_session_groups_updated("reordered")
    return jsonify({"groups": groups, "count": len(groups)})


@app.route('/api/session-config', methods=['GET'])
def get_session_config():
    """Load the launcher configuration from the local override or project default."""
    return jsonify(load_session_config())


@app.route('/api/session-config', methods=['POST'])
def persist_session_config():
    """Persist the last-used saved session selection."""
    data = request.get_json(silent=True) or {}
    set_last_saved_session(data.get("saved_session_id"))
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
    group_id = str(data.get("group_id") or "").strip()
    activate_saved_session = data.get("activate", True) is not False
    saved_entry = upsert_saved_session(
        config=config,
        name=data.get("name"),
        session_id=data.get("id"),
        set_last_session=activate_saved_session,
    )
    group = (
        session_manager.update_group_saved_session(
            group_id,
            saved_entry["id"],
            saved_entry["name"],
        )
        if group_id
        else None
    )
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    return jsonify(
        {
            **_saved_session_response(saved_entry, include_config=True),
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

    state = delete_saved_sessions(raw_ids)
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
    """
    Create one or more terminal sessions.

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
        ]
    }
    """
    global active_launch_options
    try:
        data = request.get_json()
        logger.info(f"POST /api/sessions  body={data}")

        if not data or 'sessions' not in data:
            logger.warning("Missing 'sessions' in request body")
            return jsonify({"error": "Missing 'sessions' in request body"}), 400

        sessions_config = data['sessions']
        if not isinstance(sessions_config, list) or not sessions_config:
            logger.warning("Empty or invalid sessions list")
            return jsonify({"error": "At least one session is required"}), 400

        if len(sessions_config) > runtime_config.max_sessions:
            logger.warning(f"Too many sessions requested: {len(sessions_config)} > {runtime_config.max_sessions}")
            return jsonify({"error": f"Maximum {runtime_config.max_sessions} sessions allowed"}), 400

        connection_mode = _normalize_connection_mode(data.get("connection_mode"))
        layout = _normalize_layout(data.get("layout"), len(sessions_config))
        workspace_layout = _normalize_workspace_layout(data.get("workspace_layout"), len(sessions_config))
        surface_mode = _normalize_surface_mode(data.get("surface_mode"), runtime_config.app_surface_mode)
        session_name = str(data.get("session_name") or "").strip()
        saved_session_id = _normalize_launch_session_id(data.get("saved_session_id"))
        stable_group_id = _build_launch_group_id(saved_session_id) or None
        prepared_sessions = []

        for config in sessions_config:
            prepared = dict(config)
            prepared["mode"] = connection_mode
            startup_mode = _normalize_startup_mode(
                prepared.get("startup_mode") or prepared.get("initial_command_mode"),
                connection_mode,
            )
            prepared["startup_mode"] = startup_mode

            if connection_mode == "ssh" and startup_mode == "explorer":
                prepared["initial_command"] = ""
                prepared["explorer_root_directory"] = prepared.get("directory") or ""

            if connection_mode == "wsl":
                if startup_mode == "browser":
                    use_powershell = False
                    use_wsl = False
                    prepared["initial_command"] = _normalize_browser_url(
                        prepared.get("initial_command")
                    )
                    prepared["initial_command_mode"] = "browser"
                    prepared["explorer_root_directory"] = None
                    prepared["distribution"] = ""
                    prepared["username"] = ""
                elif startup_mode == "explorer":
                    use_powershell = False
                    use_wsl = False
                    prepared["initial_command"] = ""
                    prepared["explorer_root_directory"] = prepared.get("directory") or ""
                    prepared["distribution"] = ""
                    prepared["username"] = ""
                else:
                    use_powershell = os.name == "nt" and bool(prepared.get("use_powershell"))
                    use_wsl = os.name == "nt" and bool(prepared.get("use_wsl")) and not use_powershell
                prepared["password"] = None
                prepared["port"] = 22
                prepared["host"] = (
                    "Browser"
                    if startup_mode == "browser"
                    else (
                        "File Explorer"
                        if startup_mode == "explorer"
                        else _local_shell_display_name(
                            use_wsl=use_wsl,
                            use_powershell=use_powershell,
                            distribution=prepared.get("distribution"),
                        )
                    )
                )
                prepared["use_wsl"] = use_wsl
                prepared["use_powershell"] = use_powershell

            prepared_sessions.append(prepared)

        launch_warnings = _sanitize_agent_launch_commands(connection_mode, prepared_sessions)

        # Atomic reference swap instead of in-place update so concurrent
        # readers never observe a half-updated layout/count pair.
        active_launch_options = {
            **active_launch_options,
            "connection_mode": connection_mode,
            "layout": layout,
            "terminal_count": len(prepared_sessions),
        }

        if stable_group_id:
            _replace_group_sessions(stable_group_id)

        group = session_manager.create_group(
            name=session_name or f"Session {time.strftime('%H:%M:%S')}",
            connection_mode=connection_mode,
            layout=layout,
            terminal_count=len(prepared_sessions),
            group_id=stable_group_id,
            saved_session_id=saved_session_id,
            workspace_layout=workspace_layout,
            surface_mode=surface_mode,
        )
        logger.info(
            "Created session group group_id=%s saved_session_id=%r name=%r mode=%s layout=%s terminal_count=%d",
            group.group_id,
            saved_session_id,
            group.name,
            connection_mode,
            layout,
            len(prepared_sessions),
        )

        created_sessions = session_manager.create_sessions(
            prepared_sessions,
            group_id=group.group_id,
        )
        logger.info(f"Created {len(created_sessions)} sessions")
        if not created_sessions:
            logger.error("No valid sessions were created")
            session_manager.remove_group(group.group_id)
            return jsonify({"error": "No valid sessions were created"}), 400

        logger.info(
            "Launch summary group_id=%s sessions=%s",
            group.group_id,
            [
                {
                    "session_id": session.session_id,
                    "title": session.title,
                    "host": session.host,
                    "directory": session.directory,
                    "mode": session.mode,
                }
                for session in created_sessions
            ],
        )

        for session in created_sessions:
            if _is_explorer_session(session) or _is_browser_session(session):
                session_manager.update_session_status(session.session_id, SessionStatus.CONNECTED)
                _broadcast_session_status(session.session_id)
                continue
            logger.info(
                "Spawning connection task for session_id=%s mode=%s host=%s group_id=%s",
                session.session_id,
                session.mode,
                session.host,
                session.group_id,
            )
            socketio.start_background_task(_connect_session, session.session_id)

        _broadcast_session_groups_updated("launched")

        return jsonify({
            "sessions": [s.to_dict() for s in created_sessions],
            "count": len(created_sessions),
            "group_id": group.group_id,
            "group": group.to_dict(),
            "layout": layout,
            "connection_mode": connection_mode,
            "terminal_count": len(created_sessions),
            "workspace_layout": workspace_layout,
            "surface_mode": surface_mode,
            "launch_target": "web",
            "warnings": launch_warnings,
        }), 201

    except ValueError as e:
        logger.warning("Invalid session launch request: %s", e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating sessions: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/sessions/<session_id>', methods=['GET'])
def get_session(session_id: str):
    """Get a specific session by ID."""
    session = session_manager.get_session(session_id)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    return jsonify(session.to_dict())


@app.route('/api/sessions/<session_id>/split', methods=['POST'])
def split_session(session_id: str):
    """Append one cloned terminal session to the source session's group."""
    source = session_manager.get_session(session_id)
    if not source:
        return jsonify({"error": "Session not found"}), 404

    if _is_explorer_session(source) or _is_browser_session(source):
        return jsonify({"error": "Explorer and browser panes cannot be split"}), 400

    group = session_manager.get_group(source.group_id)
    if not group:
        return jsonify({"error": "Session group not found"}), 404

    group_sessions = session_manager.get_group_sessions(group.group_id)
    if len(group_sessions) >= runtime_config.max_sessions:
        return jsonify({"error": f"Maximum {runtime_config.max_sessions} sessions allowed"}), 400

    title = f"Terminal {len(group_sessions) + 1}"
    new_session = session_manager.append_session_to_group(
        group_id=group.group_id,
        host=source.host,
        directory=source.directory,
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
        startup_mode=source.startup_mode,
        explorer_root_directory=source.explorer_root_directory,
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
    _broadcast_session_groups_updated("split")

    return jsonify(
        {
            "session": new_session.to_dict(),
            "group_id": group.group_id,
            "group": group.to_dict(),
            "terminal_count": group.terminal_count,
        }
    ), 201


@app.route('/api/sessions/<session_id>/mode', methods=['POST'])
def change_session_mode(session_id: str):
    """Switch one pane between terminal, file explorer, and browser modes."""
    session = session_manager.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404

    if session.mode not in {"ssh", "wsl"}:
        return jsonify({"error": "Pane mode switching is only available for SSH and Local Repo sessions"}), 400

    data = request.get_json(silent=True) or {}
    target_mode = _normalize_startup_mode(data.get("startup_mode"), session.mode)
    if target_mode not in {"terminal", "explorer", "browser"}:
        return jsonify({"error": "startup_mode must be 'terminal', 'explorer', or 'browser'"}), 400

    if target_mode == "browser":
        if session.mode != "wsl":
            return jsonify({"error": "Browser mode is only available for Local Repo sessions"}), 400
        try:
            browser_url = _normalize_browser_url(
                data.get("url")
                or data.get("initial_command")
                or session.initial_command
                or DEFAULT_BROWSER_URL
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        session_manager.update_session_metadata(
            session_id,
            host="Browser",
            username="",
            port=22,
            password=None,
            initial_command=browser_url,
            initial_command_mode="browser",
            startup_mode="browser",
        )
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _close_ssh_connection(session_id, clear_buffer=True)
        _broadcast_session_status(session_id)
        return jsonify(session_manager.get_session(session_id).to_dict())

    if target_mode == "explorer":
        requested_directory = data.get("directory")
        if data.get("refresh_cwd"):
            requested_directory = _resolve_live_terminal_cwd(session_id, session) or requested_directory
        next_directory = session.directory
        root_directory = ""

        if session.mode == "ssh":
            if requested_directory:
                next_directory = _remote_path_clean(requested_directory)
            next_directory = _remote_path_clean(next_directory or "/")
            root_candidate = _remote_explorer_root_directory(session) or next_directory
            client = None
            sftp = None
            try:
                client, sftp = _acquire_ssh_sftp(session)
                next_directory = sftp.normalize(next_directory)
                if not _remote_is_directory(sftp, next_directory):
                    raise ValueError("Explorer root directory does not exist")
                try:
                    root_directory = sftp.normalize(root_candidate)
                    root_is_valid = _remote_is_directory(sftp, root_directory)
                except OSError:
                    root_directory = next_directory
                    root_is_valid = False
                if not root_is_valid or not _remote_path_inside(root_directory, next_directory):
                    root_directory = next_directory
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            except _sftp_request_error_types() as exc:
                return jsonify({"error": str(exc)}), 500
            finally:
                _release_ssh_sftp(session, client, sftp)

            session_manager.update_session_metadata(
                session_id,
                directory=next_directory,
                explorer_root_directory=root_directory,
                initial_command="",
                startup_mode="explorer",
            )
        else:
            if requested_directory:
                next_directory = os.path.abspath(os.path.expanduser(str(requested_directory)))
            if not next_directory or not os.path.isdir(next_directory):
                return jsonify({"error": "Explorer root directory does not exist"}), 400

            root_directory = _explorer_root_directory(session) or next_directory
            root_directory = os.path.realpath(os.path.abspath(os.path.expanduser(root_directory)))
            next_directory = os.path.realpath(os.path.abspath(os.path.expanduser(next_directory)))
            try:
                common_path = os.path.commonpath([root_directory, next_directory])
            except ValueError:
                common_path = ""
            if (
                not os.path.isdir(root_directory)
                or os.path.normcase(common_path) != os.path.normcase(root_directory)
            ):
                root_directory = next_directory

            session_manager.update_session_metadata(
                session_id,
                host="File Explorer",
                directory=next_directory,
                explorer_root_directory=root_directory,
                username="",
                port=22,
                password=None,
                initial_command="",
                startup_mode="explorer",
            )
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        _close_ssh_connection(session_id, clear_buffer=True)
        _broadcast_session_status(session_id)
        return jsonify(session_manager.get_session(session_id).to_dict())

    if not (_is_explorer_session(session) or _is_browser_session(session)):
        return jsonify(session.to_dict())

    next_directory = session.directory
    root_path = _explorer_root_directory(session)
    if _is_browser_session(session):
        next_directory = session.directory
    elif _is_remote_explorer_session(session):
        client = None
        sftp = None
        try:
            client, sftp = _acquire_ssh_sftp(session)
            root_path, selected_directory = _resolve_remote_explorer_candidate_path(
                sftp,
                session,
                data.get("directory", ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except _sftp_request_error_types() as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            _release_ssh_sftp(session, client, sftp)
        next_directory = selected_directory
    else:
        try:
            root_path, selected_directory = _resolve_explorer_candidate_path(
                session,
                data.get("directory", ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not os.path.isdir(selected_directory):
            return jsonify({"error": "Selected explorer path is not a directory"}), 400
        next_directory = selected_directory

    updates = {
        "directory": next_directory,
        "explorer_root_directory": root_path,
        "initial_command": "",
        "initial_command_mode": "command",
        "startup_mode": "terminal",
    }
    if session.mode == "wsl":
        updates["host"] = _local_shell_display_name(
            use_wsl=session.use_wsl,
            use_powershell=session.use_powershell,
            distribution=session.distribution,
        )
    session_manager.update_session_metadata(session_id, **updates)
    session_manager.update_session_status(session_id, SessionStatus.PENDING)
    _broadcast_session_status(session_id)
    socketio.start_background_task(_connect_session, session_id)
    return jsonify(session_manager.get_session(session_id).to_dict())


@app.route('/api/sessions/<session_id>', methods=['DELETE'])
def close_session(session_id: str):
    """Close a specific session."""
    success = session_manager.close_session(session_id)

    if not success:
        return jsonify({"error": "Session not found"}), 404

    _close_ssh_connection(session_id, clear_buffer=True)
    session_manager.clear_disconnected_sessions()
    _broadcast_session_groups_updated("session_closed")

    return jsonify({"message": "Session closed successfully"})


@app.route('/api/sessions', methods=['DELETE'])
def close_all_sessions():
    """Close all sessions."""
    group_id = _resolve_group_id()
    if group_id:
        sessions = session_manager.get_group_sessions(group_id)
        if not sessions and not session_manager.get_group(group_id):
            return jsonify({"error": "Session group not found"}), 404

        for session in sessions:
            session_manager.close_session(session.session_id)
            _close_ssh_connection(session.session_id, clear_buffer=True)

        # The user explicitly closed this group, so it must not survive on the
        # empty-group grace period.
        session_manager.clear_disconnected_sessions(force_group_ids={group_id})
        _broadcast_session_groups_updated("group_closed")
        return jsonify({"message": "Session group closed successfully", "group_id": group_id})

    session_manager.close_all_sessions()
    _close_all_ssh_connections(clear_buffers=True)
    session_manager.reset_sessions()
    _broadcast_session_groups_updated("all_closed")

    return jsonify({"message": "All sessions closed successfully"})


# ==================== WebSocket Events ====================

@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info(f"Client connected: {request.sid}") # type: ignore
    emit('connected', {'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info(f"Client disconnected: {request.sid}") # type: ignore
    _clear_client_joined_sessions(request.sid) # type: ignore


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
        emit('error', {'message': 'Missing session_id'})
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
        emit('error', {'message': 'Missing session_id'})
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
        emit('error', {'message': 'Missing session_id'})
        return

    with connection_lock:
        connection = ssh_connections.get(session_id)

    if not connection:
        emit('error', {'message': 'Session is not connected'})
        return

    try:
        sanitized_input = _sanitize_terminal_input(connection, input_data)
        if not sanitized_input:
            return
        _track_terminal_agent_input(session_id, connection, sanitized_input)
        _send_connection_input(connection, sanitized_input)
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


@app.route('/api/voice-status', methods=['GET'])
def voice_status_endpoint():
    """Check voice input availability and service status."""
    if runtime_config.voice_engine == "vosk":
        service_running: Optional[bool] = _vosk_service_reachable(timeout=1.0)
        service_url = runtime_config.vosk_service_url
        engine_available = _vosk_engine_available()
    else:
        service_running = None
        service_url = ""
        engine_available = _whisper_engine_available()
    status_message = (
        "Voice backend is available."
        if engine_available
        else _voice_engine_unavailable_message(runtime_config.voice_engine)
    )
    return jsonify({
        'enabled': runtime_config.voice_enabled,
        'engine': runtime_config.voice_engine,
        'engine_available': engine_available,
        'ws_client_available': _vosk_engine_available(),
        'service_running': service_running,
        'service_url': service_url,
        'model': _active_voice_model_name(),
        'language': runtime_config.voice_language,
        'startup_timeout_seconds': runtime_config.vosk_startup_timeout_seconds,
        'whisper_device': runtime_config.whisper_device,
        'whisper_compute_type': runtime_config.whisper_compute_type,
        'status_message': status_message,
    })


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
    return jsonify(current)


@socketio.on('voice_start')
def handle_voice_start(data):
    """
    Start voice recognition for a terminal session.
    Uses the configured backend engine and prepares per-session state.
    """
    logger.info("voice_start requested by client %s for session %s",
                request.sid, data.get('session_id'))  # type: ignore[arg-type]
    if not runtime_config.voice_enabled:
        emit('voice_status', {'status': 'error',
                              'message': 'Voice input is disabled in config'})
        return

    session_id = data.get('session_id')
    if not session_id:
        emit('voice_status', {'status': 'error', 'message': 'Missing session_id'})
        return

    engine = 'whisper' if runtime_config.voice_engine == 'whisper' else 'vosk'
    with _active_voice_sessions_lock:
        _active_voice_sessions[session_id] = engine

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

    with _active_voice_sessions_lock:
        engine = _active_voice_sessions.get(session_id, runtime_config.voice_engine)

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

    with _active_voice_sessions_lock:
        engine = _active_voice_sessions.pop(session_id, runtime_config.voice_engine)

    if engine == 'whisper':
        _stop_whisper_voice_session(session_id)
    else:
        _stop_vosk_voice_session(session_id)

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
