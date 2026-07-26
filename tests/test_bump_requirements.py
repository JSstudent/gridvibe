"""Tests for utils/bump_requirements.py (offline -- PyPI responses are stubbed)."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils import bump_requirements  # noqa: E402


def _release(*versions, requires_python="", yanked=False):
    return {
        version: [{"requires_python": requires_python, "yanked": yanked}] for version in versions
    }


class BumpRequirementsTestCase(unittest.TestCase):
    def _write(self, text):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8", newline=""
        )
        handle.write(text)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def _plan(self, text, latest):
        """Rewrite ``text`` with a stubbed PyPI cache; return the resulting file body."""
        path = self._write(text)
        new_lines, changes = bump_requirements.plan_file(
            path, False, 5, dict(latest), [], target_python="3.10"
        )
        return "".join(new_lines), changes

    def test_requires_python_filters_out_incompatible_releases(self):
        """A release that needs a newer Python than the project floor is never chosen."""
        payload = {
            "releases": {
                **_release("2.4.6", requires_python=">=3.11"),
                **_release("2.5.1", requires_python=">=3.12"),
                **_release("2.2.6", requires_python=">=3.10"),
            }
        }
        with mock.patch.object(bump_requirements, "urllib") as urllib_mock:
            urllib_mock.request.urlopen.return_value.__enter__.return_value = None
            with mock.patch.object(bump_requirements.json, "load", return_value=payload):
                self.assertEqual(
                    bump_requirements.fetch_latest_version("numpy", target_python="3.10"), "2.2.6"
                )
                self.assertEqual(
                    bump_requirements.fetch_latest_version("numpy", target_python="3.11"), "2.4.6"
                )
                self.assertEqual(
                    bump_requirements.fetch_latest_version("numpy", target_python="3.12"), "2.5.1"
                )

    def test_supports_python_accepts_missing_and_unparseable_metadata(self):
        self.assertTrue(bump_requirements.supports_python([{"requires_python": ""}], "3.10"))
        self.assertTrue(bump_requirements.supports_python([{"requires_python": "nonsense"}], "3.10"))
        self.assertFalse(bump_requirements.supports_python([{"requires_python": ">=3.99"}], "3.10"))

    def test_prereleases_and_yanked_releases_are_skipped(self):
        payload = {"releases": {**_release("2.4.6"), **_release("2.5.0rc1")}}
        payload["releases"].update(_release("2.6.0", yanked=True))
        with mock.patch.object(bump_requirements, "urllib") as urllib_mock:
            urllib_mock.request.urlopen.return_value.__enter__.return_value = None
            with mock.patch.object(bump_requirements.json, "load", return_value=payload):
                self.assertEqual(
                    bump_requirements.fetch_latest_version("numpy", target_python="3.10"), "2.4.6"
                )
                self.assertEqual(
                    bump_requirements.fetch_latest_version(
                        "numpy", allow_prerelease=True, target_python="3.10"
                    ),
                    "2.5.0rc1",
                )

    def test_extras_markers_and_comments_survive_a_bump(self):
        body, changes = self._plan(
            '# header\n-r requirements.txt\npywebview[qt]>=6.2.1; platform_system == "Linux"\n',
            {"pywebview": "7.0.0"},
        )
        self.assertEqual(changes, [("pywebview", "6.2.1", "7.0.0")])
        self.assertEqual(
            body,
            '# header\n-r requirements.txt\npywebview[qt]>=7.0.0; platform_system == "Linux"\n',
        )

    def test_floors_are_never_lowered(self):
        body, changes = self._plan("Flask>=3.1.3\n", {"Flask": "2.0.0"})
        self.assertEqual(changes, [])
        self.assertEqual(body, "Flask>=3.1.3\n")

    def test_crlf_line_endings_are_preserved(self):
        body, _changes = self._plan("ruff>=0.15.22\r\n", {"ruff": "0.16.0"})
        self.assertEqual(body, "ruff>=0.16.0\r\n")

    def test_project_python_floor_matches_pyproject(self):
        self.assertEqual(bump_requirements.project_python_floor(), "3.10")


if __name__ == "__main__":
    unittest.main()
