import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from utils import cleanup


class CleanupTestCase(unittest.TestCase):
    """Deep-dive 4.9 — explicit dir/file patterns; logs truncated, not deleted."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "mod.cpython-310.pyc").write_text("x")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "stale.pyc").write_text("x")
        (self.root / "pkg" / "stale.pyo").write_text("x")
        # A *directory* whose name matches a file pattern must not be
        # rmtree'd (the old walker's dir/file confusion).
        (self.root / "data.pyc").mkdir()
        (self.root / "data.pyc" / "keep.txt").write_text("keep")
        (self.root / "logs").mkdir()
        self.log_file = self.root / "logs" / "gridvibe.log"
        self.log_file.write_text("old log content")
        (self.root / ".venv" / "__pycache__").mkdir(parents=True)

        patcher = patch.object(cleanup, "PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dry_run_changes_nothing(self):
        cleanup.cleanup(dry_run=True, verbose=False)

        self.assertTrue((self.root / "__pycache__").exists())
        self.assertTrue((self.root / "pkg" / "stale.pyc").exists())
        self.assertEqual(self.log_file.read_text(), "old log content")

    def test_real_run_removes_caches_and_truncates_logs(self):
        cleanup.cleanup(dry_run=False, verbose=False)

        self.assertFalse((self.root / "__pycache__").exists())
        self.assertFalse((self.root / "pkg" / "stale.pyc").exists())
        self.assertFalse((self.root / "pkg" / "stale.pyo").exists())
        # The log file survives (it may be held open by the running server)
        # but is emptied.
        self.assertTrue(self.log_file.exists())
        self.assertEqual(self.log_file.read_text(), "")
        # A directory named like a file pattern is untouched.
        self.assertTrue((self.root / "data.pyc" / "keep.txt").exists())
        # Skipped directories are untouched.
        self.assertTrue((self.root / ".venv" / "__pycache__").exists())

    def test_banner_uses_gridvibe_name(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            cleanup.cleanup(dry_run=True, verbose=True)
        output = buffer.getvalue()
        self.assertIn("GridVibe Cleanup", output)
        self.assertNotIn("Terminal Flow", output)


if __name__ == "__main__":
    unittest.main()
