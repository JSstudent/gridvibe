import json
import unittest
from pathlib import Path
from unittest.mock import patch

from services import vosk_service

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class VoskServiceConfigTestCase(unittest.TestCase):
    """Deep-dive 4.8 — vosk_service_url is the single source of truth for the port."""

    def test_port_parsed_from_service_url(self):
        config = {
            "voice_input": {
                "vosk_service_url": "ws://localhost:3111",
                "vosk_model": "model-x",
            }
        }
        with patch.object(vosk_service, "load_config", return_value=config):
            self.assertEqual(vosk_service._load_config_defaults(), (3111, "model-x"))

    def test_url_port_wins_over_deprecated_port_key(self):
        config = {
            "voice_input": {
                "vosk_service_url": "ws://localhost:3111",
                "vosk_service_port": 2755,
            }
        }
        with patch.object(vosk_service, "load_config", return_value=config):
            with self.assertLogs("vosk-service", level="WARNING") as logs:
                port, _model = vosk_service._load_config_defaults()
        self.assertEqual(port, 3111)
        self.assertTrue(any("deprecated" in line for line in logs.output))

    def test_deprecated_port_key_used_only_when_url_has_no_port(self):
        config = {
            "voice_input": {
                "vosk_service_url": "ws://localhost",
                "vosk_service_port": 2755,
            }
        }
        with patch.object(vosk_service, "load_config", return_value=config):
            with self.assertLogs("vosk-service", level="WARNING"):
                port, _model = vosk_service._load_config_defaults()
        self.assertEqual(port, 2755)

    def test_defaults_when_config_is_empty(self):
        with patch.object(vosk_service, "load_config", return_value={}):
            self.assertEqual(
                vosk_service._load_config_defaults(),
                (vosk_service.DEFAULT_PORT, vosk_service.DEFAULT_MODEL),
            )

    def test_defaults_when_config_unreadable(self):
        with patch.object(vosk_service, "load_config", side_effect=OSError("boom")):
            with self.assertLogs("vosk-service", level="WARNING"):
                self.assertEqual(
                    vosk_service._load_config_defaults(),
                    (vosk_service.DEFAULT_PORT, vosk_service.DEFAULT_MODEL),
                )

    def test_default_config_no_longer_ships_the_port_key(self):
        with open(PROJECT_ROOT / "default_config.json", encoding="utf-8") as f:
            config = json.load(f)
        voice = config.get("voice_input", {})
        self.assertNotIn("vosk_service_port", voice)
        self.assertIn("vosk_service_url", voice)


if __name__ == "__main__":
    unittest.main()
