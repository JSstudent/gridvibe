"""
Vosk WebSocket speech recognition service for GridVibe.

Standalone WebSocket server that accepts audio streams and returns
transcription results using the Vosk offline speech recognition engine.

Protocol (compatible with vosk-server):
  Client -> Server: {"config": {"sample_rate": 16000}}    (optional)
  Client -> Server: <binary PCM int16 audio data>         (audio chunks)
  Server -> Client: {"partial": "hello wor"}              (partial result)
  Server -> Client: {"text": "hello world"}               (final result)
  Client -> Server: {"eof": 1}                            (end of stream)
  Server -> Client: {"text": "final result"}              (final flush)

Usage:
  python services/vosk_service.py [--port PORT] [--model MODEL_NAME]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from urllib.parse import urlparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from web.config import load_config  # noqa: E402 - Flask-free config module

logger = logging.getLogger("vosk-service")

DEFAULT_PORT = 2700
DEFAULT_MODEL = "vosk-model-en-us-0.22"
DEFAULT_SAMPLE_RATE = 16000


def _load_config_defaults():
    """Read voice_input settings from the layered GridVibe config.

    `voice_input.vosk_service_url` is the single source of truth for the port
    (the API dials the same URL), so the service and the API can no longer
    desync. The removed `vosk_service_port` key is honoured as a deprecated
    fallback when the URL carries no port.
    """
    port = DEFAULT_PORT
    model_name = DEFAULT_MODEL
    try:
        voice = load_config().get("voice_input", {})
    except Exception:
        logger.warning("Could not load GridVibe config; using built-in defaults.")
        return port, model_name

    model_name = voice.get("vosk_model", DEFAULT_MODEL)
    try:
        url_port = urlparse(str(voice.get("vosk_service_url") or "")).port
    except ValueError:
        url_port = None

    legacy_port = voice.get("vosk_service_port")
    if legacy_port is not None:
        logger.warning(
            "voice_input.vosk_service_port is deprecated and will be ignored in a "
            "future release; set the port in voice_input.vosk_service_url instead."
        )

    if url_port:
        port = url_port
    elif legacy_port is not None:
        try:
            port = int(legacy_port)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
    return port, model_name


async def _handle_client(websocket, model, sample_rate_default):
    """Handle a single WebSocket client session."""
    from vosk import KaldiRecognizer

    sample_rate = sample_rate_default
    recognizer = KaldiRecognizer(model, sample_rate)

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if data.get("eof"):
                    result = recognizer.FinalResult()
                    await websocket.send(result)
                    return

                if "config" in data:
                    new_rate = data["config"].get("sample_rate", sample_rate)
                    if new_rate != sample_rate:
                        sample_rate = new_rate
                        recognizer = KaldiRecognizer(model, sample_rate)

            elif isinstance(message, bytes):
                if recognizer.AcceptWaveform(message):
                    await websocket.send(recognizer.Result())
                else:
                    await websocket.send(recognizer.PartialResult())
    except Exception as exc:
        logger.debug("Client disconnected: %s", exc)


async def main(port, model_name):
    import websockets
    from vosk import Model

    logger.info("Loading Vosk model: %s ...", model_name)
    model = Model(model_name=model_name)
    logger.info("Vosk model loaded successfully.")

    async def handler(websocket):
        await _handle_client(websocket, model, DEFAULT_SAMPLE_RATE)

    logger.info("Vosk service listening on ws://localhost:%d", port)
    async with websockets.serve(handler, "localhost", port):
        await asyncio.Future()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    cfg_port, cfg_model = _load_config_defaults()

    parser = argparse.ArgumentParser(
        description="Vosk speech recognition WebSocket service"
    )
    parser.add_argument(
        "--port", type=int, default=cfg_port,
        help=f"WebSocket port (default: {cfg_port})",
    )
    parser.add_argument(
        "--model", default=cfg_model,
        help=f"Vosk model name (default: {cfg_model})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(args.port, args.model))
    except KeyboardInterrupt:
        logger.info("Vosk service stopped.")
    except ImportError as exc:
        logger.error(
            "Missing dependency: %s — install with: pip install vosk websockets", exc
        )
        sys.exit(1)

