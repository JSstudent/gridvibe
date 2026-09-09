import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class DashboardFocusTestCase(unittest.TestCase):
    def test_window_focus_lease_and_cleanup(self):
        result = subprocess.run(
            [shutil.which("node"), str(Path(__file__).with_suffix(".cjs"))],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
