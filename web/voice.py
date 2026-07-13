"""Voice input backends: Vosk WebSocket proxy and faster-whisper.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Owns the vosk-service
subprocess lifecycle, per-session Vosk WebSocket connections, the lazily
(re)built faster-whisper model, audio buffering/transcription, and voice
preference persistence. The Socket.IO ``voice_*`` handlers stay in
``web.api`` and delegate here; ``web.api`` re-exports every name for
backwards compatibility.
"""

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from flask_socketio import emit

from web.config import _config_lock, load_config, runtime_config, save_config
from web.paths import BASE_DIR

try:
    import websocket as ws_client
except ImportError:  # pragma: no cover - optional for voice input
    ws_client = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional for faster-whisper input handling
    np = None

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - optional for faster-whisper voice input
    WhisperModel = None

logger = logging.getLogger(__name__)


def _vosk_engine_available() -> bool:
    """Return True when the websocket-client package is importable."""
    return ws_client is not None


def _whisper_language_code(language: Any) -> Optional[str]:
    """Normalize app language strings such as en-US to faster-whisper codes."""
    if not isinstance(language, str):
        return None

    normalized = language.strip().lower()
    if not normalized or normalized == "auto":
        return None

    return normalized.split("-", 1)[0].split("_", 1)[0] or None


_vosk_process = None
_vosk_ws_connections: Dict[str, Any] = {}
_vosk_lock = threading.Lock()
_vosk_session_locks: Dict[str, threading.Lock] = {}
_vosk_process_lock = threading.Lock()
_whisper_model_instance = None
_whisper_model_params = None
_whisper_model_lock = threading.Lock()
_whisper_audio_buffers: Dict[str, bytearray] = {}
_whisper_audio_lock = threading.Lock()
# Engine each active recording started with, so audio/stop keep routing to it
# even if the user switches engines in App Settings mid-recording.
_active_voice_sessions: Dict[str, str] = {}
_active_voice_sessions_lock = threading.Lock()


def _whisper_engine_available() -> bool:
    """Return True when faster-whisper and numpy are importable."""
    return WhisperModel is not None and np is not None


def _voice_engine_unavailable_message(engine: Optional[str] = None) -> str:
    """Return a user-facing reason the configured voice backend cannot start."""
    selected_engine = engine or runtime_config.voice_engine
    if selected_engine == "whisper":
        missing = []
        if WhisperModel is None:
            missing.append("faster-whisper")
        if np is None:
            missing.append("numpy")
        if missing:
            return (
                "Cannot start faster-whisper because missing package(s): "
                + " and ".join(missing)
                + ". Install optional voice dependencies with: "
                "pip install -r requirements-voice.txt"
            )
        return "faster-whisper is not available."

    if ws_client is None:
        return (
            "Cannot start Vosk because websocket-client is not installed. "
            "Install optional voice dependencies with: pip install -r requirements-voice.txt"
        )
    return "Voice backend is not available."


def _ensure_whisper_model():
    """Load the configured faster-whisper model lazily, rebuilding it when settings change."""
    global _whisper_model_instance
    global _whisper_model_params

    if WhisperModel is None:
        raise RuntimeError(
            "faster-whisper is not installed. Install it with: pip install faster-whisper"
        )
    if np is None:
        raise RuntimeError(
            "numpy is not installed. Install it with: pip install numpy"
        )

    with _whisper_model_lock:
        wanted_params = (runtime_config.whisper_model, runtime_config.whisper_device, runtime_config.whisper_compute_type)
        if _whisper_model_instance is None or _whisper_model_params != wanted_params:
            logger.info(
                "Loading faster-whisper model %s on %s (%s)",
                runtime_config.whisper_model,
                runtime_config.whisper_device,
                runtime_config.whisper_compute_type,
            )
            _whisper_model_instance = WhisperModel(
                runtime_config.whisper_model,
                device=runtime_config.whisper_device,
                compute_type=runtime_config.whisper_compute_type,
            )
            _whisper_model_params = wanted_params
        return _whisper_model_instance


def _pcm16le_to_float32(audio_bytes: bytes):
    """Convert raw PCM16LE bytes to normalized float32 mono samples."""
    if np is None:
        raise RuntimeError(
            "numpy is required to decode PCM audio for faster-whisper"
        )
    if not audio_bytes:
        return np.array([], dtype=np.float32)

    pcm = np.frombuffer(audio_bytes, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def _transcribe_whisper_audio(audio_bytes: bytes) -> str:
    """Run a final transcription pass over the buffered session audio."""
    model = _ensure_whisper_model()
    audio_array = _pcm16le_to_float32(audio_bytes)
    language = _whisper_language_code(runtime_config.voice_language)
    segments, _info = model.transcribe(
        audio_array,
        language=language,
        beam_size=1,
        best_of=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    return " ".join(
        segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()
    ).strip()


def _vosk_service_reachable(timeout=2.0):
    """Return True when the configured Vosk WebSocket endpoint accepts a handshake."""
    if ws_client is None:
        return False

    ws = None
    try:
        ws = ws_client.create_connection(runtime_config.vosk_service_url, timeout=timeout)
        return True
    except Exception:
        return False
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass


def _ensure_vosk_service():
    """Start vosk-service subprocess if not already running."""
    global _vosk_process

    with _vosk_process_lock:
        if _vosk_service_reachable(timeout=1.5):
            if _vosk_process is None:
                logger.info("Using already-running vosk-service at %s", runtime_config.vosk_service_url)
            return True

        if _vosk_process is not None and _vosk_process.poll() is None:
            if _wait_for_vosk_ready(_vosk_process, timeout=runtime_config.vosk_startup_timeout_seconds):
                return True

        # Clean up a dead process handle
        _vosk_process = None

        vosk_script = os.path.join(BASE_DIR, "services", "vosk_service.py")
        if not os.path.exists(vosk_script):
            logger.error("vosk_service.py not found at %s", vosk_script)
            return False

        try:
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                [sys.executable, vosk_script],
                creationflags=creation_flags,
            )
            _vosk_process = process

            # Poll until the service accepts a real WebSocket handshake.
            if not _wait_for_vosk_ready(process, timeout=runtime_config.vosk_startup_timeout_seconds):
                if process.poll() is None:
                    logger.error(
                        "vosk-service not ready after %ss; model download/load may still be in progress or the service may be hung",
                        runtime_config.vosk_startup_timeout_seconds,
                    )
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except Exception:
                        pass
                else:
                    logger.error(
                        "vosk-service exited before becoming ready (code %s)",
                        process.returncode,
                    )
                _vosk_process = None
                return False
            logger.info("vosk-service started (PID %d)", process.pid)
            return True
        except Exception as exc:
            logger.error("Failed to start vosk-service: %s", exc)
            return False


def _wait_for_vosk_ready(process, timeout=30):
    """Block until vosk-service accepts a WebSocket connection, or timeout."""
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False  # process exited
        if _vosk_service_reachable(timeout=1.5):
            return True
        time.sleep(0.5)
    return False


def _restart_vosk_service():
    """Kill the vosk-service and start a fresh one."""
    global _vosk_process
    with _vosk_process_lock:
        if _vosk_process is not None:
            try:
                _vosk_process.kill()
                _vosk_process.wait(timeout=3)
            except Exception:
                pass
            _vosk_process = None
    return _ensure_vosk_service()


def _stop_vosk_service():
    """Terminate the vosk-service subprocess and close all voice connections."""
    global _vosk_process
    with _vosk_lock:
        for ws in _vosk_ws_connections.values():
            try:
                ws.close()
            except Exception:
                pass
        _vosk_ws_connections.clear()
        _vosk_session_locks.clear()

    with _vosk_process_lock:
        if _vosk_process is not None and _vosk_process.poll() is None:
            _vosk_process.terminate()
            try:
                _vosk_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _vosk_process.kill()
            logger.info("vosk-service stopped")
        _vosk_process = None

    with _whisper_audio_lock:
        _whisper_audio_buffers.clear()


atexit.register(_stop_vosk_service)


VOICE_PREFS_VALID_KEYS = {'profile', 'deviceId', 'pttEnabled', 'pttKeybind'}


def _default_voice_prefs() -> Dict[str, Any]:
    return {
        'profile': 'laptop',
        'deviceId': '',
        'pttEnabled': False,
        'pttKeybind': '',
    }


def _load_voice_prefs() -> Dict[str, Any]:
    cfg = load_config()
    stored = cfg.get('voice_prefs', {})
    defaults = _default_voice_prefs()
    return {k: stored.get(k, v) for k, v in defaults.items()}


def _save_voice_prefs(prefs: Dict[str, Any]):
    with _config_lock:
        cfg = load_config()
        cfg['voice_prefs'] = prefs
        save_config(cfg)


def _start_vosk_voice_session(session_id: str):
    """Start a Vosk-backed voice session."""
    if ws_client is None:
        emit('voice_status', {'status': 'error',
                              'message': 'websocket-client package not installed'})
        return

    if not _ensure_vosk_service():
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': 'Voice service unavailable — is vosk installed?',
        })
        return

    with _vosk_lock:
        old_ws = _vosk_ws_connections.pop(session_id, None)
        _vosk_session_locks.pop(session_id, None)
    if old_ws:
        try:
            old_ws.close()
        except Exception:
            pass

    try:
        ws = ws_client.create_connection(runtime_config.vosk_service_url, timeout=5)
        ws.send(json.dumps({"config": {"sample_rate": 16000}}))
        with _vosk_lock:
            leaked = _vosk_ws_connections.pop(session_id, None)
            _vosk_ws_connections[session_id] = ws
            _vosk_session_locks[session_id] = threading.Lock()
        if leaked:
            try:
                leaked.close()
            except Exception:
                pass
        emit('voice_status', {'session_id': session_id, 'status': 'listening'})
        logger.info("Voice started for session %s using Vosk", session_id)
    except Exception as exc:
        logger.warning("vosk-service connection failed, restarting: %s", exc)
        try:
            ws.close()
        except Exception:
            pass
        with _vosk_lock:
            stale = _vosk_ws_connections.pop(session_id, None)
            _vosk_session_locks.pop(session_id, None)
        if stale:
            try:
                stale.close()
            except Exception:
                pass
        if _restart_vosk_service():
            try:
                ws = ws_client.create_connection(runtime_config.vosk_service_url, timeout=5)
                ws.send(json.dumps({"config": {"sample_rate": 16000}}))
                with _vosk_lock:
                    leaked = _vosk_ws_connections.pop(session_id, None)
                    _vosk_ws_connections[session_id] = ws
                    _vosk_session_locks[session_id] = threading.Lock()
                if leaked:
                    try:
                        leaked.close()
                    except Exception:
                        pass
                emit('voice_status', {'session_id': session_id,
                                      'status': 'listening'})
                logger.info("Voice started for session %s using Vosk (after restart)",
                            session_id)
                return
            except Exception as retry_exc:
                logger.error("Retry also failed: %s", retry_exc)
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': f'Cannot reach voice service: {exc}',
        })


def _start_whisper_voice_session(session_id: str):
    """Start a faster-whisper-backed voice session."""
    with _whisper_audio_lock:
        _whisper_audio_buffers[session_id] = bytearray()

    try:
        _ensure_whisper_model()
    except Exception as exc:
        with _whisper_audio_lock:
            _whisper_audio_buffers.pop(session_id, None)
        logger.error("Cannot start faster-whisper for session %s: %s", session_id, exc)
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': f'Cannot start faster-whisper: {exc}',
        })
        return

    emit('voice_status', {'session_id': session_id, 'status': 'listening'})
    logger.info("Voice started for session %s using faster-whisper", session_id)


def _handle_vosk_audio_chunk(session_id: str, audio: Any):
    """Forward an audio chunk to vosk-service and relay the transcription result."""
    with _vosk_lock:
        ws = _vosk_ws_connections.get(session_id)
        session_lock = _vosk_session_locks.get(session_id)
    if not ws or not session_lock:
        return

    if not session_lock.acquire(timeout=2):
        return
    try:
        raw = audio if isinstance(audio, bytes) else bytes(audio)
        ws.send(raw, opcode=0x2)  # OPCODE_BINARY
        result_str = ws.recv()
        if result_str:
            parsed = json.loads(result_str)
            text = parsed.get('text', '')
            partial = parsed.get('partial', '')
            if text:
                emit('voice_result', {
                    'session_id': session_id,
                    'text': text,
                    'final': True,
                })
            elif partial:
                emit('voice_result', {
                    'session_id': session_id,
                    'text': partial,
                    'final': False,
                })
    except Exception as exc:
        logger.error("Voice audio proxy error for %s: %s", session_id, exc)
        with _vosk_lock:
            _vosk_ws_connections.pop(session_id, None)
            _vosk_session_locks.pop(session_id, None)
        try:
            ws.close()
        except Exception:
            pass
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': 'Voice service connection lost',
        })
    finally:
        session_lock.release()


def _handle_whisper_audio_chunk(session_id: str, audio: Any):
    """Buffer raw PCM audio for later faster-whisper transcription."""
    raw = audio if isinstance(audio, bytes) else bytes(audio)
    with _whisper_audio_lock:
        buffer = _whisper_audio_buffers.get(session_id)
        if buffer is None:
            buffer = bytearray()
            _whisper_audio_buffers[session_id] = buffer
        buffer.extend(raw)


def _stop_vosk_voice_session(session_id: str):
    """Stop a Vosk-backed voice session and flush final text."""
    with _vosk_lock:
        ws = _vosk_ws_connections.pop(session_id, None)
        session_lock = _vosk_session_locks.pop(session_id, None)

    if not ws:
        return

    acquired = session_lock.acquire(timeout=5) if session_lock else True
    try:
        ws.send('{"eof": 1}')
        result_str = ws.recv()
        if result_str:
            parsed = json.loads(result_str)
            text = parsed.get('text', '')
            if text:
                emit('voice_result', {
                    'session_id': session_id,
                    'text': text,
                    'final': True,
                })
    except Exception as exc:
        logger.debug("Error during voice_stop flush: %s", exc)
    finally:
        try:
            ws.close()
        except Exception:
            pass
        if session_lock and acquired:
            session_lock.release()


def _stop_whisper_voice_session(session_id: str):
    """Stop a faster-whisper-backed voice session and emit final text."""
    with _whisper_audio_lock:
        audio = bytes(_whisper_audio_buffers.pop(session_id, bytearray()))

    if not audio:
        return

    try:
        text = _transcribe_whisper_audio(audio)
    except Exception as exc:
        logger.error("faster-whisper transcription failed for %s: %s", session_id, exc)
        emit('voice_status', {
            'session_id': session_id,
            'status': 'error',
            'message': f'faster-whisper transcription failed: {exc}',
        })
        return

    if text:
        emit('voice_result', {
            'session_id': session_id,
            'text': text,
            'final': True,
        })
