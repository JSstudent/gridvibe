"""Unit tests for the explorer repository search engine (web/explorer_search.py).

Covers the `git grep -z` parser, command construction, scope confinement,
caps/truncation flags, line windowing, the local walk fallback, regex
validation, and the RuntimeConfig clamps — all without a route or SSH.
"""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from web import explorer_search
from web.config import RuntimeConfig
from web.explorer import ExplorerRouteError, _LocalExplorerBackend, _remote_git_shell_command

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _options(**overrides):
    base = {"query": "hello"}
    base.update(overrides)
    return explorer_search.SearchOptions(**base)


class ParseSearchOptionsTestCase(unittest.TestCase):
    def test_missing_and_empty_query_raise_400(self):
        for args in ({}, {"q": ""}):
            with self.assertRaises(ExplorerRouteError) as ctx:
                explorer_search.parse_search_options(args)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_query_length_limit(self):
        with self.assertRaises(ExplorerRouteError):
            explorer_search.parse_search_options(
                {"q": "x" * (explorer_search.SEARCH_QUERY_MAX_CHARS + 1)}
            )
        options = explorer_search.parse_search_options(
            {"q": "x" * explorer_search.SEARCH_QUERY_MAX_CHARS}
        )
        self.assertEqual(len(options.query), explorer_search.SEARCH_QUERY_MAX_CHARS)

    def test_flags_and_include(self):
        class Args(dict):
            def getlist(self, name):
                return ["*.py", " ", "web/**"] if name == "include" else []

        options = explorer_search.parse_search_options(
            Args(q="hello", case="1", word="true", regex="0", ignored="1", scope="/sub/")
        )
        self.assertTrue(options.case_sensitive)
        self.assertTrue(options.whole_word)
        self.assertFalse(options.regex)
        self.assertTrue(options.include_ignored)
        self.assertEqual(options.include, ("*.py", "web/**"))
        self.assertEqual(options.scope, "sub")


class GitGrepArgsTestCase(unittest.TestCase):
    def test_literal_case_insensitive(self):
        args = explorer_search.build_git_grep_args(_options(), ".")
        self.assertIn("-F", args)
        self.assertIn("-i", args)
        self.assertNotIn("-E", args)
        self.assertNotIn("-w", args)
        self.assertIn("--untracked", args)
        self.assertEqual(args[-2:], ["--", "."])
        self.assertIn("-e", args)

    def test_regex_case_sensitive_whole_word(self):
        args = explorer_search.build_git_grep_args(
            _options(regex=True, case_sensitive=True, whole_word=True), "web"
        )
        self.assertIn("-E", args)
        self.assertNotIn("-F", args)
        self.assertNotIn("-i", args)
        self.assertIn("-w", args)
        self.assertEqual(args[-2:], ["--", "web"])

    def test_remote_git_grep_command_has_output_cap(self):
        command = _remote_git_shell_command(
            ["grep", "-F", "-e", "hello", "--", "."],
            "/srv/app",
            max_output_bytes=1234,
        )
        self.assertTrue(command.endswith("| head -c 1234"))


class GitGrepZParserTestCase(unittest.TestCase):
    def test_byte_fixture_with_colon_crlf_and_invalid_utf8(self):
        stream = (
            b"web/api.py\0" b"100\0" b"    _explorer_root_directory,\n"
            b"odd:name.py\0" b"7\0" b"crlf line\r\n"
            b"bin/weird.py\0" b"3\0" b"invalid \xff\xfe bytes\n"
        )
        records = list(explorer_search.parse_git_grep_z(stream))
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0], ("web/api.py", 100, b"    _explorer_root_directory,"))
        self.assertEqual(records[1], ("odd:name.py", 7, b"crlf line\r"))
        self.assertEqual(records[2][0], "bin/weird.py")
        self.assertIn(b"\xff\xfe", records[2][2])

    def test_malformed_records_are_skipped(self):
        stream = b"no-nul-here\nok.py\0x\0content\nok.py\0 5\0content\n"
        records = list(explorer_search.parse_git_grep_z(stream))
        self.assertEqual(records, [("ok.py", 5, b"content")])

    def test_empty_stream(self):
        self.assertEqual(list(explorer_search.parse_git_grep_z(b"")), [])


class WindowLineTestCase(unittest.TestCase):
    def test_short_line_passthrough(self):
        text, offset, ranges = explorer_search.window_line("hello world", [[0, 5]])
        self.assertEqual((text, offset, ranges), ("hello world", 0, [[0, 5]]))

    def test_long_line_windows_around_first_match(self):
        needle = "needle"
        haystack = ("x" * 100_000) + needle + ("y" * 100_000)
        start = 100_000
        text, offset, ranges = explorer_search.window_line(haystack, [[start, start + 6]])
        self.assertLessEqual(len(text), explorer_search.SEARCH_LINE_WINDOW_CHARS)
        self.assertEqual(len(ranges), 1)
        span_start, span_end = ranges[0]
        self.assertEqual(text[span_start:span_end], needle)
        self.assertEqual(offset + span_start, start)


class CollectSearchPayloadTestCase(unittest.TestCase):
    def _limits(self, **overrides):
        base = {
            "max_files": 10,
            "max_matches": 50,
            "max_matches_per_file": 3,
            "max_file_bytes": 1024,
            "timeout_seconds": 8,
        }
        base.update(overrides)
        return explorer_search.SearchLimits(**base)

    def _collect(self, raw, options=None, limits=None, deadline=None):
        return explorer_search.collect_search_payload(
            iter(raw),
            options or _options(),
            limits or self._limits(),
            deadline if deadline is not None else time.monotonic() + 10,
            "walk",
            time.monotonic(),
        )

    def test_groups_and_ranges(self):
        payload = self._collect([
            ("a.txt", 1, b"say hello there"),
            ("a.txt", 2, b"hello hello"),
            ("dir/b.txt", 3, b"HELLO"),
        ])
        self.assertEqual(payload["total_files"], 2)
        self.assertEqual(payload["total_matches"], 3)
        first = payload["files"][0]
        self.assertEqual(first["path"], "a.txt")
        self.assertEqual(first["name"], "a.txt")
        self.assertEqual(first["match_count"], 2)
        self.assertEqual(first["matches"][0]["ranges"], [[4, 9]])
        self.assertEqual(first["matches"][1]["ranges"], [[0, 5], [6, 11]])
        second = payload["files"][1]
        self.assertEqual(second["dir"], "dir")
        self.assertEqual(second["matches"][0]["ranges"], [[0, 5]])

    def test_per_file_cap_sets_file_truncated(self):
        raw = [("a.txt", line, b"hello") for line in range(1, 10)]
        payload = self._collect(raw)
        entry = payload["files"][0]
        self.assertEqual(entry["match_count"], 9)
        self.assertEqual(len(entry["matches"]), 3)
        self.assertTrue(entry["truncated"])

    def test_global_match_cap(self):
        raw = [(f"f{index}.txt", 1, b"hello") for index in range(10)]
        payload = self._collect(raw, limits=self._limits(max_matches=4))
        self.assertEqual(payload["total_matches"], 4)
        self.assertEqual(
            [entry["path"] for entry in payload["files"]],
            ["f0.txt", "f1.txt", "f2.txt", "f3.txt"],
        )
        self.assertTrue(all(entry["match_count"] > 0 for entry in payload["files"]))
        self.assertTrue(payload["truncated"]["matches"])

    def test_file_cap(self):
        raw = [(f"f{index}.txt", 1, b"hello") for index in range(10)]
        payload = self._collect(raw, limits=self._limits(max_files=3))
        self.assertEqual(payload["total_files"], 3)
        self.assertTrue(payload["truncated"]["files"])

    def test_deadline_flag(self):
        payload = self._collect(
            [("a.txt", 1, b"hello"), ("a.txt", 2, b"hello")],
            deadline=time.monotonic() - 1,
        )
        self.assertTrue(payload["truncated"]["deadline"])

    def test_engine_deadline_exception_sets_flag(self):
        def raw():
            yield ("a.txt", 1, b"hello")
            raise explorer_search.SearchDeadlineExceeded()

        payload = self._collect(raw())
        self.assertTrue(payload["truncated"]["deadline"])
        self.assertEqual(payload["total_matches"], 1)

    def test_engine_output_exception_sets_flag(self):
        def raw():
            yield ("a.txt", 1, b"hello")
            raise explorer_search.SearchOutputTruncated()

        payload = self._collect(raw())
        self.assertTrue(payload["truncated"]["output"])
        self.assertEqual(payload["total_matches"], 1)

    def test_include_filter(self):
        payload = self._collect(
            [("a.py", 1, b"hello"), ("b.md", 1, b"hello"), ("web/c.py", 1, b"hello")],
            options=_options(include=("*.py",)),
        )
        self.assertEqual([f["path"] for f in payload["files"]], ["a.py", "web/c.py"])

    def test_case_sensitive_matching(self):
        payload = self._collect(
            [("a.txt", 1, b"Hello hello")],
            options=_options(case_sensitive=True),
        )
        self.assertEqual(payload["files"][0]["matches"][0]["ranges"], [[6, 11]])


class WalkEngineTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "sub"))
        with open(os.path.join(self.root, "a.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello one\nhello two\n")
        with open(os.path.join(self.root, "sub", "b.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello three\n")
        with open(os.path.join(self.root, "bin.dat"), "wb") as handle:
            handle.write(b"hello\x00\x01\x02")
        os.makedirs(os.path.join(self.root, ".git"))
        with open(os.path.join(self.root, ".git", "c.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello git\n")

    def _walk(self, **overrides):
        limits = explorer_search.SearchLimits(max_file_bytes=1024)
        deadline = time.monotonic() + 10
        return list(
            explorer_search.walk_matches(
                self.root, self.root, _options(**overrides), limits, deadline
            )
        )

    def test_walk_finds_text_and_skips_binary_and_git(self):
        matches = self._walk()
        paths = sorted({path for path, _line, _raw in matches})
        self.assertIn("a.txt", paths)
        self.assertIn("sub/b.txt", paths)
        self.assertNotIn("bin.dat", paths)
        self.assertFalse(any(path.startswith(".git") for path in paths))

    def test_oversized_file_skipped(self):
        with open(os.path.join(self.root, "big.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello\n" * 500)
        limits = explorer_search.SearchLimits(max_file_bytes=128)
        matches = list(
            explorer_search.walk_matches(
                self.root, self.root, _options(), limits, time.monotonic() + 10
            )
        )
        self.assertNotIn("big.txt", {path for path, _line, _raw in matches})

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_escape_skipped(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello secret\n")
        try:
            os.symlink(outside, os.path.join(self.root, "escape"))
            os.symlink(
                os.path.join(outside, "secret.txt"),
                os.path.join(self.root, "escape.txt"),
            )
        except OSError:
            self.skipTest("symlink creation not permitted")
        matches = self._walk()
        paths = {path for path, _line, _raw in matches}
        self.assertNotIn("escape.txt", paths)
        self.assertFalse(any(path.startswith("escape/") for path in paths))

    def test_heavy_directories_are_pruned(self):
        """The walk shares the remote fallback's deny-list — without it one
        node_modules/ or .venv/ burns the whole deadline."""
        for dirname in ("node_modules", ".venv", "__pycache__"):
            os.makedirs(os.path.join(self.root, dirname))
            with open(
                os.path.join(self.root, dirname, "d.txt"), "w", encoding="utf-8"
            ) as handle:
                handle.write("hello vendored\n")
        paths = {path for path, _line, _raw in self._walk()}
        self.assertIn("a.txt", paths)
        for dirname in ("node_modules", ".venv", "__pycache__"):
            self.assertFalse(
                any(path.startswith(f"{dirname}/") for path in paths), dirname
            )

    def test_literal_prefilter_does_not_change_results(self):
        """The whole-file literal prefilter only skips files with no match at
        all; every line the per-line matcher would yield still comes back."""
        with open(os.path.join(self.root, "quiet.txt"), "w", encoding="utf-8") as handle:
            handle.write("nothing to see\nnor here\n")
        with open(os.path.join(self.root, "edge.txt"), "w", encoding="utf-8") as handle:
            handle.write("tail hello\r\nHELLO lead\n")
        found = {(path, line) for path, line, _raw in self._walk()}
        self.assertFalse(any(path == "quiet.txt" for path, _line in found))
        self.assertIn(("edge.txt", 1), found)
        self.assertIn(("edge.txt", 2), found)
        # Whole-word at a line boundary sees the same non-word neighbour with
        # or without the prefilter.
        word_hits = {(path, line) for path, line, _raw in self._walk(whole_word=True)}
        self.assertIn(("edge.txt", 1), word_hits)

    def test_regex_queries_keep_the_per_line_path(self):
        """`$` is per line, so an anchored regex must still match — a
        whole-file prefilter would have to be MULTILINE to agree."""
        with open(os.path.join(self.root, "anchor.txt"), "w", encoding="utf-8") as handle:
            handle.write("say hello\ntrailing\n")
        matches = self._walk(query="hello$", regex=True)
        self.assertIn(("anchor.txt", 1), {(path, line) for path, line, _raw in matches})


class CompileMatcherTestCase(unittest.TestCase):
    def test_invalid_regex_raises_route_error_400(self):
        with self.assertRaises(ExplorerRouteError) as ctx:
            explorer_search.compile_search_matcher(_options(query="([", regex=True))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid regular expression", str(ctx.exception))

    def test_whole_word_boundaries(self):
        matcher = explorer_search.compile_search_matcher(_options(whole_word=True))
        self.assertIsNone(matcher.search("hellothere"))
        self.assertIsNotNone(matcher.search("say hello there"))


class LocalGitSearchTestCase(unittest.TestCase):
    """End-to-end through the local backend against a real temporary repo."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        os.makedirs(os.path.join(self.root, "web"))
        os.makedirs(os.path.join(self.root, "sessions"))
        with open(os.path.join(self.root, "web", "api.py"), "w", encoding="utf-8") as handle:
            handle.write("explorer hello\n")
        with open(
            os.path.join(self.root, "sessions", "manager.py"), "w", encoding="utf-8"
        ) as handle:
            handle.write("hello from sibling\n")

    def _backend(self, root):
        class Session:
            mode = "wsl"
            startup_mode = "explorer"
            directory = root
            explorer_root_directory = root

        return _LocalExplorerBackend(Session())

    def test_git_grep_engine_used_inside_work_tree(self):
        payload = explorer_search.run_explorer_search(self._backend(self.root), {"q": "hello"})
        self.assertEqual(payload["engine"], "git-grep")
        paths = sorted(entry["path"] for entry in payload["files"])
        self.assertEqual(paths, ["sessions/manager.py", "web/api.py"])

    def test_local_git_grep_stdout_is_bounded(self):
        with open(os.path.join(self.root, "many.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello bounded output\n" * 500)
        backend = self._backend(self.root)
        result = backend.run_git(
            explorer_search.build_git_grep_args(_options(), "."),
            cwd=self.root,
            timeout=2.0,
            max_output_bytes=128,
        )

        self.assertLessEqual(len(result.stdout), 128)
        self.assertTrue(result.stdout_truncated)

    def test_git_grep_output_cap_is_reported_in_payload(self):
        with open(os.path.join(self.root, "many.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello bounded output\n" * 500)
        with patch.object(explorer_search, "SEARCH_GIT_MAX_OUTPUT_BYTES", 128):
            payload = explorer_search.run_explorer_search(
                self._backend(self.root),
                {"q": "hello"},
            )

        self.assertTrue(payload["truncated"]["output"])

    def test_scope_below_repo_root_is_confined(self):
        """An explorer root under the repo root must not see sibling hits."""
        web_root = os.path.join(self.root, "web")
        payload = explorer_search.run_explorer_search(self._backend(web_root), {"q": "hello"})
        self.assertEqual(payload["engine"], "git-grep")
        paths = [entry["path"] for entry in payload["files"]]
        self.assertEqual(paths, ["api.py"])

    def test_scope_parameter_narrows_search(self):
        payload = explorer_search.run_explorer_search(
            self._backend(self.root), {"q": "hello", "scope": "web"}
        )
        paths = [entry["path"] for entry in payload["files"]]
        self.assertEqual(paths, ["web/api.py"])

    def test_scope_outside_root_rejected(self):
        with self.assertRaises(ValueError):
            explorer_search.run_explorer_search(
                self._backend(self.root), {"q": "hello", "scope": ".."}
            )


class RuntimeConfigClampTestCase(unittest.TestCase):
    def test_out_of_range_and_non_numeric_fall_back(self):
        config = RuntimeConfig.__new__(RuntimeConfig)
        with patch("web.config.load_config", return_value={
            "explorer_search": {
                "max_files": 999999999,
                "max_matches": "not-a-number",
                "max_matches_per_file": 0,
                "max_file_bytes": -5,
                "timeout_seconds": 9999,
            }
        }):
            config.refresh()
        self.assertEqual(config.explorer_search_max_files, 20000)
        self.assertEqual(config.explorer_search_max_matches, 5000)
        self.assertEqual(config.explorer_search_max_matches_per_file, 1)
        self.assertEqual(config.explorer_search_max_file_bytes, 4096)
        self.assertEqual(config.explorer_search_timeout_seconds, 60)

    def test_defaults_when_block_missing(self):
        config = RuntimeConfig.__new__(RuntimeConfig)
        with patch("web.config.load_config", return_value={}):
            config.refresh()
        self.assertEqual(config.explorer_search_max_files, 2000)
        self.assertEqual(config.explorer_search_max_matches, 5000)
        self.assertEqual(config.explorer_search_max_matches_per_file, 200)
        self.assertEqual(config.explorer_search_max_file_bytes, 2097152)
        self.assertEqual(config.explorer_search_timeout_seconds, 20)


if __name__ == "__main__":
    unittest.main()
