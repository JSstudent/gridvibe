import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from gridvibe_version import __version__
from web.config import resolve_server_settings


def _log_record(message, args=None):
    return logging.LogRecord(
        name="werkzeug",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


class MainTestCase(unittest.TestCase):
    def test_main_version_matches_public_version_module(self):
        self.assertEqual(main.__version__, __version__)

    def test_setup_logging_creates_gridvibe_log_file(self):
        root_logger = logging.getLogger()
        original_handlers = list(root_logger.handlers)
        original_level = root_logger.level

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(main, "LOG_DIR", temp_dir):
                previous_disable = logging.root.manager.disable
                try:
                    logging.disable(logging.CRITICAL)
                    main.setup_logging(debug=True)
                    log_file = Path(temp_dir) / "gridvibe.log"

                    self.assertTrue(log_file.exists())
                    self.assertEqual(root_logger.level, logging.DEBUG)
                    self.assertEqual(len(root_logger.handlers), 2)
                    # Finding 9.2 — only the file handler strips ANSI codes,
                    # so the console keeps werkzeug's colours.
                    stream_handler, file_handler = root_logger.handlers
                    self.assertTrue(
                        any(
                            isinstance(f, main._StripAnsiFilter)
                            for f in file_handler.filters
                        )
                    )
                    self.assertFalse(
                        any(
                            isinstance(f, main._StripAnsiFilter)
                            for f in stream_handler.filters
                        )
                    )
                finally:
                    logging.disable(previous_disable)
                    for handler in root_logger.handlers:
                        handler.close()
                    root_logger.handlers.clear()
                    for handler in original_handlers:
                        root_logger.addHandler(handler)
                    root_logger.setLevel(original_level)


class ResolveServerSettingsTestCase(unittest.TestCase):
    """Deep-dive 4.7 — explicit CLI flags beat config.json values."""

    def test_cli_flags_beat_config(self):
        config = {"server": {"host": "0.0.0.0", "port": 5050, "debug": True}}
        self.assertEqual(
            resolve_server_settings(config, host="127.0.0.1", port=8080, debug=False),
            ("127.0.0.1", 8080, False),
        )

    def test_config_used_when_flags_absent(self):
        config = {"server": {"host": "0.0.0.0", "port": 6000, "debug": True}}
        self.assertEqual(resolve_server_settings(config), ("0.0.0.0", 6000, True))

    def test_defaults_without_config(self):
        self.assertEqual(resolve_server_settings({}), ("127.0.0.1", 5050, False))

    def test_partial_flags_mix_with_config(self):
        config = {"server": {"port": 6000, "debug": True}}
        self.assertEqual(
            resolve_server_settings(config, port=8080),
            ("127.0.0.1", 8080, True),
        )


class LogPolishTestCase(unittest.TestCase):
    """Deep-dive 9.2/9.3 — ANSI-free log file, voice-status polls suppressed."""

    def test_poll_filter_suppresses_voice_status_requests(self):
        log_filter = main._SuppressPollLogs()
        suppressed = _log_record(
            '127.0.0.1 - - [10/Jul/2026 12:00:00] "GET /api/voice-status HTTP/1.1" 200 -'
        )
        self.assertFalse(log_filter.filter(suppressed))

    def test_poll_filter_keeps_other_requests(self):
        log_filter = main._SuppressPollLogs()
        kept = _log_record(
            '127.0.0.1 - - [10/Jul/2026 12:00:00] "GET /api/app-config HTTP/1.1" 200 -'
        )
        self.assertTrue(log_filter.filter(kept))

    def test_strip_ansi_filter_cleans_werkzeug_style_records(self):
        log_filter = main._StripAnsiFilter()
        # werkzeug passes the coloured request line through record args.
        record = _log_record(
            "%s", args=('"\x1b[35m\x1b[1mPOST /api/app-update HTTP/1.1\x1b[0m" 500 -',)
        )
        self.assertTrue(log_filter.filter(record))
        self.assertEqual(record.getMessage(), '"POST /api/app-update HTTP/1.1" 500 -')

    def test_strip_ansi_filter_leaves_plain_records_alone(self):
        log_filter = main._StripAnsiFilter()
        record = _log_record("plain %s", args=("message",))
        self.assertTrue(log_filter.filter(record))
        self.assertEqual(record.getMessage(), "plain message")
        self.assertEqual(record.args, ("message",))
