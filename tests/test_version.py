"""Version sources must agree — see docs/release_and_installer_plan_2026-07-25.md §A1."""

import re
import unittest
from pathlib import Path

from gridvibe_version import __version__

BASE_DIR = Path(__file__).resolve().parent.parent


class VersionConsistencyTestCase(unittest.TestCase):
    def test_pyproject_version_matches_gridvibe_version(self):
        text = (BASE_DIR / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml has no literal version")
        self.assertEqual(match.group(1), __version__)

    def test_changelog_documents_the_current_version(self):
        text = (BASE_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^## {re.escape(__version__)} - \d{{4}}-\d{{2}}-\d{{2}}$", re.MULTILINE
        )
        self.assertRegex(
            text,
            pattern,
            msg="CHANGELOG.md has no dated section for the current version",
        )
