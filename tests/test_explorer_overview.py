"""Source-view change marking (Phase 1 of the change-overview plan):
``web/static/js/explorer-overview.js`` owns the HEAD-relative change model
for the open file and paints gutter markers (added / modified bars, deletion
wedges) on the rendered Source rows. There is no JS test runner, so the suite
is contract-level on the served assets — the allowed kinds only: script
wiring and load order, ``data-*`` hooks, named functions and constants, CSS
selectors and custom properties. The behavioural half (that a ``mode=head``
diff's new-side line numbers address the worktree) is covered by the
``git/diff`` tests in ``tests/test_api.py``.
"""

import unittest

import api


class ExplorerOverviewTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _overview(self) -> str:
        return self._static("js/explorer-overview.js")

    def test_terminals_page_loads_the_overview_script_in_order(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn("/static/js/explorer-overview.js", html)
        # After explorer-viewer.js (it calls explorerDiffChangeBlocks and the
        # viewer calls back) and before terminals.js.
        self.assertLess(
            html.index("js/explorer-viewer.js"),
            html.index("js/explorer-overview.js"),
        )
        self.assertLess(
            html.index("js/explorer-overview.js"),
            html.index("js/terminals.js"),
        )

    def test_overview_script_exposes_only_the_four_entry_points(self):
        overview = self._overview()
        for name in (
            "loadExplorerChangeMarks",
            "applyExplorerChangeMarks",
            "refreshExplorerOverview",
            "teardownExplorerOverview",
        ):
            self.assertIn(f"function {name}(", overview)

    def test_change_model_fetches_the_head_diff_once_per_path_and_head(self):
        overview = self._overview()
        # The bounded read-only endpoint, HEAD mode only (marks are
        # HEAD-relative; commit diffs would address the wrong lines).
        self.assertIn("/git/diff?", overview)
        self.assertIn("mode: 'head'", overview)
        # Cached on the pane as key + model, separate from the Diff panel's
        # view-scoped slots; the key is `path \n git-revision`.
        self.assertIn("pane._explorerChangeMarksKey", overview)
        self.assertIn("pane._explorerChangeMarks", overview)
        self.assertIn("_explorerGitContext?.head", overview)
        # A hit short-circuits the fetch; the explicit force flag is what the
        # refresh triggers use after a save / undo / revision change.
        self.assertIn("pane._explorerChangeMarksKey === key", overview)
        self.assertIn("{ force = false } = {}", overview)

    def test_change_model_reuses_the_diff_parser_and_classifies_blocks(self):
        overview = self._overview()
        # The diff-undo parser is reused unchanged, and the block shape
        # (expected = worktree lines, replacement = HEAD lines) classifies
        # into added / modified line marks plus deletion wedges.
        self.assertIn("explorerDiffChangeBlocks(diff)", overview)
        self.assertIn("removed ? 'modified' : 'added'", overview)
        self.assertIn("deletions.push({ atLine: block.line, count: removed })", overview)

    def test_change_model_skips_the_surfaces_that_cannot_carry_marks(self):
        overview = self._overview()
        eligible = overview[
            overview.index("function explorerChangeMarksEligible(pane)"):
            overview.index("function explorerChangeMarksKey(pane)")
        ]
        # Not in a Git worktree, a commit-diff tab, and an active editor all
        # opt out before any request is made.
        self.assertIn("pane._explorerGitContext?.available", eligible)
        self.assertIn("!pane._explorerDiffCommit", eligible)
        self.assertIn("!pane._explorerEdit", eligible)
        # The Diff panel's already-fetched HEAD diff is reused instead of
        # re-fetching, but only on an exact mode-scoped cache-key match.
        self.assertIn("explorerDiffCacheKey(path, '', 'head')", overview)

    def test_gutter_marks_are_emitted_as_data_hooks(self):
        overview = self._overview()
        # Marks land on the rendered rows, addressable by line number, as
        # data-explorer-change hooks the CSS paints — never as inline styles.
        self.assertIn(".explorer-source-line[data-explorer-line]", overview)
        self.assertIn("row.dataset.explorerChange = kind;", overview)
        self.assertIn("row.dataset.explorerChange = 'deleted';", overview)

    def test_gutter_marker_rules_exist_for_all_three_kinds(self):
        css = self._static("css/terminals.css")
        self.assertIn(
            '.explorer-source-line[data-explorer-change="added"] .explorer-source-line-number',
            css,
        )
        self.assertIn(
            '.explorer-source-line[data-explorer-change="modified"] .explorer-source-line-number',
            css,
        )
        self.assertIn(
            '.explorer-source-line[data-explorer-change="deleted"] .explorer-source-line-number::before',
            css,
        )
        # The colours come from the shared tokens, not palette literals.
        self.assertIn("var(--gv-diff-add)", css)
        self.assertIn("var(--gv-diff-modified)", css)
        self.assertIn("var(--gv-diff-delete)", css)

    def test_diff_marker_tokens_are_declared_for_both_themes(self):
        tokens = self._static("css/tokens.css")
        root = tokens[tokens.index(":root {"):]
        root = root[: root.index("}")]
        light = tokens[tokens.index('[data-theme="light"] {'):]
        light = light[: light.index("}")]
        for token in ("--gv-diff-add", "--gv-diff-modified", "--gv-diff-delete"):
            self.assertIn(f"{token}:", root)
            self.assertIn(f"{token}:", light)

    def test_refresh_triggers_are_wired_into_the_existing_render_paths(self):
        viewer = self._static("js/explorer-viewer.js")
        # Marks re-apply (cheap, no fetch) after every Source rebuild.
        render_source = viewer[
            viewer.index("function renderExplorerSource(index, searchRanges = [])"):
            viewer.index("function explorerPreviewBlockLanguage(code)")
        ]
        self.assertIn("applyExplorerChangeMarks(index);", render_source)
        # A full file render kicks off the load; an in-place refresh (save,
        # undo, quiet watcher refresh) forces it past the path + HEAD key.
        self.assertIn("loadExplorerChangeMarks(index);", viewer)
        self.assertIn("loadExplorerChangeMarks(index, { force: true });", viewer)
        # The directory listing and the image viewer drop the cached model.
        self.assertIn("teardownExplorerOverview(index);", viewer)

    def test_git_watch_carries_an_overview_consumer(self):
        watch = self._static("js/explorer-git-watch.js")
        # The existing /git/state poll detects a terminal-made commit and
        # refreshes the marks — no new polling of its own.
        self.assertIn("function explorerOverviewWatchConsumer(pane)", watch)
        self.assertIn("refreshExplorerOverview(index, 'git-revision')", watch)
        self.assertIn("pane._explorerOverviewWatchRevision = revision;", watch)


if __name__ == "__main__":
    unittest.main()
