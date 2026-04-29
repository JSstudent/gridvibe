import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
from gridvibe_version import __version__


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
                finally:
                    logging.disable(previous_disable)
                    for handler in root_logger.handlers:
                        handler.close()
                    root_logger.handlers.clear()
                    for handler in original_handlers:
                        root_logger.addHandler(handler)
                    root_logger.setLevel(original_level)
