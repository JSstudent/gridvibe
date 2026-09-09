import shutil
import subprocess
import unittest
from pathlib import Path


class PaneCallbackOwnershipTestCase(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required')
    def test_deferred_pane_callbacks(self):
        result = subprocess.run(
            [shutil.which('node'), str(Path(__file__).with_name('pane_callback_ownership.cjs'))],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
