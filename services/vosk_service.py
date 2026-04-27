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

logger = logging.getLogger("vosk-service")

DEFAULT_PORT = 2700
DEFAULT_MODEL = "vosk-model-en-us-0.22"
DEFAULT_SAMPLE_RATE = 16000


def _load_config_defaults():
    """Read voice_input settings from config.json if available."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
    )
    port = DEFAULT_PORT
    model_name = DEFAULT_MODEL
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            voice = cfg.get("voice_input", {})
            port = voice.get("vosk_service_port", DEFAULT_PORT)
            model_name = voice.get("vosk_model", DEFAULT_MODEL)
        except Exception:
            pass
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    try:
        asyncio.run(main(args.port, args.model))
    except KeyboardInterrupt:
        logger.info("Vosk service stopped.")
    except ImportError as exc:
        logger.error(
            "Missing dependency: %s — install with: pip install vosk websockets", exc
        )
        sys.exit(1)

