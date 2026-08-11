"""Unit tests for the explorer file/directory name search.

Covers the option parser, the bounded local walk, the remote `find` command
and its parser (including root confinement), the payload's name-only matching
and highlight ranges, the caps/truncation flags, and — in Node, against the
real module — the client's result-tree builder.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from web import explorer_search
from web.explorer import ExplorerRouteError, _LocalExplorerBackend

TREE_SEARCH_JS = (
    Path(__file__).resolve().parent.parent
    / "web" / "static" / "js" / "explorer-tree-search.js"
)
NODE = shutil.which("node")


def _options(**overrides):
    base = {"query": "audit"}
    base.update(overrides)
    return explorer_search.FindOptions(**base)


def _collect(entries, **overrides):
    options = _options(**overrides)
    limits = explorer_search.SearchLimits()
    return explorer_search.collect_find_payload(
        iter(entries), options, limits, time.monotonic() + 10, "walk", time.monotonic()
    )


class ParseFindOptionsTestCase(unittest.TestCase):
    def test_missing_and_empty_query_raise_400(self):
        for args in ({}, {"q": ""}):
            with self.assertRaises(ExplorerRouteError) as ctx:
                explorer_search.parse_find_options(args)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_query_length_limit(self):
        with self.assertRaises(ExplorerRouteError):
            explorer_search.parse_find_options(
                {"q": "x" * (explorer_search.FIND_QUERY_MAX_CHARS + 1)}
            )
        options = explorer_search.parse_find_options(
            {"q": "x" * explorer_search.FIND_QUERY_MAX_CHARS}
        )
        self.assertEqual(len(options.query), explorer_search.FIND_QUERY_MAX_CHARS)

    def test_flags(self):
        options = explorer_search.parse_find_options(
            {"q": "api", "case": "1", "word": "true", "regex": "0"}
        )
        self.assertTrue(options.case_sensitive)
        self.assertTrue(options.whole_word)
        self.assertFalse(options.regex)

    def test_query_semantics_share_the_content_searchs_matcher(self):
        """Same flags must mean the same thing in both searches."""
        matcher = explorer_search.compile_search_matcher(
            _options(query="api", whole_word=True)
        )
        self.assertIsNone(matcher.search("rapid.py"))
        self.assertIsNotNone(matcher.search("api.py"))


class CollectFindPayloadTestCase(unittest.TestCase):
    def test_matches_names_not_paths(self):
        """A directory whose *name* matches is a hit; a file that merely lives
        under it is not — otherwise one matching folder floods the result."""
        payload = _collect(
            [("audit", True), ("audit/notes.md", False), ("docs/audit.md", False)],
            query="audit",
        )
        self.assertEqual(
            [entry["path"] for entry in payload["entries"]],
            ["audit", "docs/audit.md"],
        )

    def test_ranges_locate_the_match_inside_the_name(self):
        payload = _collect([("docs/codebase_audit.md", False)], query="audit")
        entry = payload["entries"][0]
        self.assertEqual(entry["name"], "codebase_audit.md")
        self.assertEqual(entry["dir"], "docs")
        self.assertEqual(entry["type"], "file")
        start, end = entry["ranges"][0]
        self.assertEqual(entry["name"][start:end], "audit")

    def test_case_insensitive_by_default_and_sensitive_on_request(self):
        entries = [("README.md", False)]
        self.assertEqual(len(_collect(entries, query="readme")["entries"]), 1)
        self.assertEqual(
            len(_collect(entries, query="readme", case_sensitive=True)["entries"]), 0
        )

    def test_regex_query(self):
        payload = _collect(
            [("a.py", False), ("b.js", False)], query=r"\.py$", regex=True
        )
        self.assertEqual([entry["path"] for entry in payload["entries"]], ["a.py"])

    def test_zero_width_match_filters_in_without_painting(self):
        payload = _collect([("a.py", False)], query="^", regex=True)
        self.assertEqual(payload["entries"][0]["ranges"], [])

    def test_directories_sort_before_files_within_a_parent(self):
        payload = _collect(
            [("docs/audit.md", False), ("docs/auditing", True), ("audit.md", False)],
            query="audit",
        )
        self.assertEqual(
            [entry["path"] for entry in payload["entries"]],
            ["audit.md", "docs/auditing", "docs/audit.md"],
        )

    def test_result_cap_sets_truncated(self):
        entries = [(f"audit{n}.md", False) for n in range(12)]
        with patch.object(explorer_search, "FIND_MAX_RESULTS", 5):
            payload = _collect(entries, query="audit")
        self.assertEqual(payload["total"], 5)
        self.assertTrue(payload["truncated"]["results"])

    def test_engine_exceptions_set_their_flags(self):
        def failing(exception):
            def generate():
                yield ("audit.md", False)
                raise exception()

            return generate()

        for exception, flag in (
            (explorer_search.SearchDeadlineExceeded, "deadline"),
            (explorer_search.SearchScanLimitExceeded, "scanned"),
            (explorer_search.SearchOutputTruncated, "output"),
        ):
            payload = _collect(failing(exception), query="audit")
            self.assertTrue(payload["truncated"][flag], flag)
            # Whatever the engine did manage to yield is still reported.
            self.assertEqual(payload["total"], 1)

    def test_invalid_regex_is_a_400(self):
        with self.assertRaises(ExplorerRouteError) as ctx:
            _collect([("a.py", False)], query="([", regex=True)
        self.assertEqual(ctx.exception.status_code, 400)


class WalkNamesTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "docs", "images"))
        for path in (
            ("README.md",),
            ("docs", "audit.md"),
            ("docs", "images", "shot.png"),
        ):
            with open(os.path.join(self.root, *path), "w", encoding="utf-8") as handle:
                handle.write("x")

    def _walk(self):
        return list(explorer_search.walk_names(self.root, time.monotonic() + 10))

    def test_reports_files_and_directories_relative_to_the_root(self):
        found = dict(self._walk())
        self.assertEqual(found["README.md"], False)
        self.assertEqual(found["docs"], True)
        self.assertEqual(found["docs/images"], True)
        self.assertEqual(found["docs/images/shot.png"], False)

    def test_breadth_first_so_truncation_keeps_the_shallow_tree(self):
        paths = [path for path, _is_dir in self._walk()]
        self.assertLess(paths.index("docs"), paths.index("docs/audit.md"))
        self.assertLess(paths.index("docs/audit.md"), paths.index("docs/images/shot.png"))

    def test_heavy_directories_are_named_but_not_descended(self):
        for dirname in (".git", "node_modules", ".venv", "__pycache__"):
            os.makedirs(os.path.join(self.root, dirname))
            with open(
                os.path.join(self.root, dirname, "vendored.js"), "w", encoding="utf-8"
            ) as handle:
                handle.write("x")
        paths = [path for path, _is_dir in self._walk()]
        for dirname in (".git", "node_modules", ".venv", "__pycache__"):
            self.assertIn(dirname, paths)
            self.assertNotIn(f"{dirname}/vendored.js", paths)

    def test_scan_cap_raises_the_scan_signal(self):
        for n in range(30):
            with open(os.path.join(self.root, f"f{n}.txt"), "w", encoding="utf-8") as handle:
                handle.write("x")
        with patch.object(explorer_search, "FIND_MAX_SCANNED_ENTRIES", 10):
            with self.assertRaises(explorer_search.SearchScanLimitExceeded):
                self._walk()

    def test_depth_cap_stops_descending(self):
        deep = self.root
        for level in range(4):
            deep = os.path.join(deep, f"level{level}")
            os.makedirs(deep)
        with patch.object(explorer_search, "FIND_MAX_DEPTH", 2):
            paths = [path for path, _is_dir in self._walk()]
        self.assertIn("level0/level1", paths)
        self.assertNotIn("level0/level1/level2", paths)

    def test_passed_deadline_raises(self):
        with self.assertRaises(explorer_search.SearchDeadlineExceeded):
            list(explorer_search.walk_names(self.root, time.monotonic() - 1))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlinked_directory_is_never_descended(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        with open(os.path.join(outside, "secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("x")
        try:
            os.symlink(outside, os.path.join(self.root, "escape"))
        except OSError:
            self.skipTest("symlink creation not permitted")
        paths = [path for path, _is_dir in self._walk()]
        self.assertNotIn("escape/secret.txt", paths)


class RemoteFindCommandTestCase(unittest.TestCase):
    def test_command_prunes_marks_kinds_and_caps_output(self):
        command = explorer_search.build_remote_find_command("/srv/project dir")
        self.assertIn("'/srv/project dir'", command)  # quoted for the remote shell
        self.assertIn("-prune", command)
        self.assertIn("-name node_modules", command)
        self.assertIn("-type d -print", command)
        self.assertIn("'!' -type d -print", command)
        self.assertIn("sed 's|^|d |'", command)
        self.assertIn("sed 's|^|f |'", command)
        self.assertIn(f"head -c {explorer_search.FIND_REMOTE_MAX_OUTPUT_BYTES}", command)
        self.assertIn(f"-maxdepth {explorer_search.FIND_MAX_DEPTH}", command)


class RemoteFindParserTestCase(unittest.TestCase):
    class Backend:
        """The two path hooks `parse_remote_find_output` needs, POSIX-style."""

        @staticmethod
        def path_inside_root(root_path, abs_path):
            return abs_path == root_path or abs_path.startswith(f"{root_path}/")

        @staticmethod
        def rel_explorer_path(root_path, abs_path):
            return abs_path[len(root_path):].lstrip("/")

    def _parse(self, stream, root="/srv/app"):
        return list(
            explorer_search.parse_remote_find_output(self.Backend(), root, stream)
        )

    def test_kinds_confinement_and_root_row(self):
        stream = (
            b"d /srv/app\n"
            b"d /srv/app/docs\n"
            b"f /srv/app/docs/audit.md\n"
            b"f /etc/passwd\n"
        )
        # The root itself carries no relative path, and a path outside the
        # explorer root never becomes a result.
        self.assertEqual(
            self._parse(stream),
            [("docs", True), ("docs/audit.md", False)],
        )

    def test_malformed_records_are_skipped(self):
        self.assertEqual(self._parse(b"\nx /srv/app/a\nd\nf /srv/app/b\n"), [("b", False)])

    def test_truncated_tail_record_is_dropped(self):
        stream = b"f /srv/app/a\nf /srv/app/partial"
        with patch.object(explorer_search, "FIND_REMOTE_MAX_OUTPUT_BYTES", len(stream)):
            self.assertEqual(self._parse(stream), [("a", False)])

    def test_empty_stream(self):
        self.assertEqual(self._parse(b""), [])


class LocalFindEndToEndTestCase(unittest.TestCase):
    """Through the local backend, from query string to payload."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, "docs"))
        os.makedirs(os.path.join(self.root, "web"))
        for path in (("docs", "codebase_audit.md"), ("web", "api.py"), ("README.md",)):
            with open(os.path.join(self.root, *path), "w", encoding="utf-8") as handle:
                handle.write("audit mentioned in the contents only\n")

    def _backend(self, root):
        class Session:
            mode = "wsl"
            startup_mode = "explorer"
            directory = root
            explorer_root_directory = root

        return _LocalExplorerBackend(Session())

    def test_finds_by_name_and_ignores_matching_contents(self):
        payload = explorer_search.run_explorer_find(
            self._backend(self.root), {"q": "audit"}
        )
        self.assertEqual(payload["engine"], "walk")
        self.assertEqual(
            [entry["path"] for entry in payload["entries"]], ["docs/codebase_audit.md"]
        )
        self.assertFalse(any(payload["truncated"].values()))

    def test_root_confinement(self):
        """A pane rooted at web/ must not see docs/."""
        payload = explorer_search.run_explorer_find(
            self._backend(os.path.join(self.root, "web")), {"q": "a"}
        )
        self.assertEqual([entry["path"] for entry in payload["entries"]], ["api.py"])


@unittest.skipUnless(NODE, "Node.js is required for client tree-search tests")
class TreeSearchResultTreeTestCase(unittest.TestCase):
    """The client turns the flat payload back into a tree — executed, not read."""

    def _run_node(self, harness):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TREE_SEARCH_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_hits_are_nested_under_generated_ancestor_directories(self):
        result = self._run_node(
            """
            const treeSearch = require(process.argv[2]);
            const nodes = treeSearch.buildExplorerTreeSearchNodes([
                { path: 'docs/images/audit.png', name: 'audit.png', type: 'file', ranges: [[0, 5]] }
            ]);
            const docs = nodes[0];
            const images = docs.children[0];
            process.stdout.write(JSON.stringify({
                roots: nodes.length,
                docs: { path: docs.path, matched: docs.matched, type: docs.type },
                images: { path: images.path, matched: images.matched },
                hit: {
                    path: images.children[0].path,
                    matched: images.children[0].matched,
                    ranges: images.children[0].ranges
                }
            }));
            """
        )
        # The two ancestors exist so the hit has a place, but neither is a
        # result: an unmatched ancestor gets no highlight.
        self.assertEqual(result["roots"], 1)
        self.assertEqual(result["docs"], {"path": "docs", "matched": False, "type": "directory"})
        self.assertEqual(result["images"], {"path": "docs/images", "matched": False})
        self.assertEqual(
            result["hit"],
            {"path": "docs/images/audit.png", "matched": True, "ranges": [[0, 5]]},
        )

    def test_a_matched_directory_keeps_its_own_hit_when_it_is_also_an_ancestor(self):
        result = self._run_node(
            """
            const treeSearch = require(process.argv[2]);
            const nodes = treeSearch.buildExplorerTreeSearchNodes([
                { path: 'audit/deep.md', name: 'deep.md', type: 'file', ranges: [] },
                { path: 'audit', name: 'audit', type: 'directory', ranges: [[0, 5]] }
            ]);
            process.stdout.write(JSON.stringify({
                path: nodes[0].path,
                matched: nodes[0].matched,
                type: nodes[0].type,
                ranges: nodes[0].ranges,
                children: nodes[0].children.map(child => child.path)
            }));
            """
        )
        self.assertEqual(result["matched"], True)
        self.assertEqual(result["type"], "directory")
        self.assertEqual(result["ranges"], [[0, 5]])
        self.assertEqual(result["children"], ["audit/deep.md"])

    def test_siblings_sort_directories_first_then_by_name(self):
        result = self._run_node(
            """
            const treeSearch = require(process.argv[2]);
            const nodes = treeSearch.buildExplorerTreeSearchNodes([
                { path: 'b.md', type: 'file', ranges: [] },
                { path: 'Zed.md', type: 'file', ranges: [] },
                { path: 'a.md', type: 'file', ranges: [] },
                { path: 'lib', type: 'directory', ranges: [] }
            ]);
            process.stdout.write(JSON.stringify(nodes.map(node => node.path)));
            """
        )
        self.assertEqual(result, ["lib", "a.md", "b.md", "Zed.md"])

    def test_degenerate_entries_are_dropped_and_ranges_normalized(self):
        result = self._run_node(
            """
            const treeSearch = require(process.argv[2]);
            const nodes = treeSearch.buildExplorerTreeSearchNodes([
                { path: '', type: 'file' },
                { path: '/leading/', type: 'file', ranges: [[2, 2], ['1', '3'], null] },
                null
            ]);
            process.stdout.write(JSON.stringify(nodes.map(node => ({
                path: node.path, ranges: node.ranges
            }))));
            """
        )
        # Empty and zero-width spans paint nothing, so they are dropped.
        self.assertEqual(result, [{"path": "leading", "ranges": [[1, 3]]}])

    def test_request_params_carry_only_the_toggles_that_are_on(self):
        result = self._run_node(
            """
            const treeSearch = require(process.argv[2]);
            process.stdout.write(JSON.stringify({
                plain: treeSearch.explorerTreeSearchRequestParams({ query: '  audit  ' }),
                flagged: treeSearch.explorerTreeSearchRequestParams({
                    query: 'audit', case: true, word: false, regex: true
                })
            }));
            """
        )
        self.assertEqual(result["plain"], {"q": "audit"})
        self.assertEqual(result["flagged"], {"q": "audit", "case": "1", "regex": "1"})


@unittest.skipUnless(NODE, "Node.js is required for client tree-search tests")
class TreeSearchRenderTestCase(unittest.TestCase):
    """The rendered result rows, executed in Node against a stubbed pane.

    The module is evaluated in a vm with the globals it borrows from the
    explorer stubbed out, so the highlight and the nesting are run rather than
    matched as source text.
    """

    HARNESS_PRELUDE = """
        const fs = require('fs');
        const vm = require('vm');
        const pane = { _explorerTreeSearch: { query: 'audit', payload: null } };
        const rows = [];
        const sandbox = {
            console,
            terminals: [pane],
            sessionIds: ['session-1'],
            document: { getElementById: () => null },
            escHtml: text => String(text)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
            /* Stands in for the real row builder: records what the filter asked
               it to paint, at what depth. */
            explorerTreeRowHtml: (paneArg, entry, depth, options) => {
                rows.push({ path: entry.path, type: entry.type, depth,
                            nameHtml: options.nameHtml, staticChevron: options.staticChevron });
                return `<row>${entry.path}</row>`;
            },
            explorerTreeEntryForPath: () => null,
            renderExplorerTreePanel: () => {}
        };
        vm.createContext(sandbox);
        vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
    """

    def _render(self, entries_json):
        harness = self.HARNESS_PRELUDE + f"""
            pane._explorerTreeSearch.payload = {{ entries: {entries_json},
                truncated: {{}}, total: 1 }};
            const html = sandbox.renderExplorerTreeSearchNodes(0);
            process.stdout.write(JSON.stringify({{ rows, html }}));
        """
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TREE_SEARCH_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_hit_is_highlighted_and_nested_under_a_plain_ancestor(self):
        result = self._render(
            """[{ path: 'docs/audit.md', type: 'file', ranges: [[0, 5]] }]"""
        )
        ancestor, hit = result["rows"]
        self.assertEqual((ancestor["path"], ancestor["depth"]), ("docs", 0))
        # An ancestor is scaffolding, not a result: no highlight of its own.
        self.assertEqual(ancestor["nameHtml"], "")
        self.assertEqual((hit["path"], hit["depth"]), ("docs/audit.md", 1))
        self.assertEqual(
            hit["nameHtml"], '<mark class="explorer-search-match">audit</mark>.md'
        )
        # Result folders are always open, so no row carries a fold control.
        self.assertTrue(all(row["staticChevron"] for row in result["rows"]))

    def test_names_are_escaped_around_the_highlight(self):
        result = self._render(
            """[{ path: '<b>audit.md', type: 'file', ranges: [[3, 8]] }]"""
        )
        self.assertEqual(
            result["rows"][0]["nameHtml"],
            '&lt;b&gt;<mark class="explorer-search-match">audit</mark>.md',
        )

    def test_no_matches_reports_instead_of_rendering_an_empty_tree(self):
        result = self._render("[]")
        self.assertEqual(result["rows"], [])
        self.assertIn("No matching files or folders.", result["html"])


if __name__ == "__main__":
    unittest.main()
