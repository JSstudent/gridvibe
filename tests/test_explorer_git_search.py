"""Behavioral coverage for the Git sidebar's commit find.

`explorer-git-search.js` is DOM-free and require()-able, so the matching, the
active-index clamping and the mark wrapping behind the Graph section's search
box are executed in Node rather than asserted as source text.

The rules under test are the ones the Source find already lives by: matching
is case-insensitive over the text the row actually shows, every occurrence is
counted (so the counter and the highlight agree), prev/next wrap around both
ends instead of sticking, an empty query matches nothing, and the wrapped
markup escapes the subject it highlights.

The find can also switch to commit-id mode without leaking that mode into the
subject matcher. Its collapse-all policy is covered here too: Alt+click can
empty expansion state, but can never turn an empty state into expand-all.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_SEARCH_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-git-search.js"

NODE = shutil.which("node")

COMMITS = [
    {"hash": "6caf331", "subject": "Merge remote-tracking branch"},
    {"hash": "9c28e89", "subject": "(fix) A pane whose TUI opts out"},
    {"hash": "05c588c", "subject": "codebase audit final"},
    {"hash": "8852ab3", "subject": "(fix) opt-in, OPT-out, opt again"},
    # No parsed subject: the row shows the raw log line, so that is the
    # haystack.
    {"hash": "e85eee5", "line": "e85eee5 (fix) optional voice"},
]

HARNESS_PREAMBLE = (
    "const commits = " + json.dumps(COMMITS) + ";\n"
    "const emit = value => console.log(JSON.stringify(value));\n"
)


@unittest.skipUnless(NODE, "Node.js is required for Git commit-search tests")
class ExplorerGitSearchHarness(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            "const git_search = require(" + json.dumps(str(GIT_SEARCH_JS)) + ");\n"
            + HARNESS_PREAMBLE
            + "\n"
            + body
            + "\n"
        )
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "harness.js"
            script.write_text(harness, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(script)],
                capture_output=True,
                text=True,
                # The find's own messages carry em dashes; Node writes UTF-8
                # whatever the host console's codepage is.
                encoding="utf-8",
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])


class ExplorerGitSearchPlanTestCase(ExplorerGitSearchHarness):
    def test_matching_is_case_insensitive_over_every_occurrence(self):
        plan = self._run_node(
            "const plan = git_search.searchPlan(commits, 'OPT', 0);"
            "emit({ count: plan.matchCount, ranges: plan.perCommit });"
        )

        self.assertEqual(plan["count"], 5)
        self.assertEqual(plan["ranges"][0], [])
        self.assertEqual(plan["ranges"][1], [[23, 26]])
        self.assertEqual(plan["ranges"][2], [])
        self.assertEqual(plan["ranges"][3], [[6, 9], [14, 17], [23, 26]])
        self.assertEqual(plan["ranges"][4], [[14, 17]])

    def test_empty_query_matches_nothing(self):
        plan = self._run_node(
            "emit(['', 'fix'].map(query => {"
            "  const plan = git_search.searchPlan(commits, query, 3);"
            "  return { count: plan.matchCount, active: plan.activeIndex };"
            "}));"
        )

        self.assertEqual(plan, [{"count": 0, "active": 0}, {"count": 3, "active": 0}])

    def test_active_index_wraps_past_both_ends(self):
        active = self._run_node(
            "emit([-1, 0, 4, 5, 11].map(index =>"
            "  git_search.searchPlan(commits, 'opt', index).activeIndex));"
        )

        self.assertEqual(active, [4, 0, 4, 0, 1])

    def test_no_match_pins_the_active_index_to_zero(self):
        active = self._run_node(
            "emit(git_search.searchPlan(commits, 'no-such-word', 7).activeIndex);"
        )

        self.assertEqual(active, 0)

    def test_non_list_commits_and_blank_records_are_tolerated(self):
        plan = self._run_node(
            "emit(["
            "  git_search.searchPlan(null, 'opt', 0),"
            "  git_search.searchPlan([null, {}, { subject: 'opt' }], 'opt', 0)"
            "].map(plan => plan.matchCount));"
        )

        self.assertEqual(plan, [0, 1])

    def test_hash_mode_matches_full_prefix_and_mixed_case_ids(self):
        matches = self._run_node(
            "const rows = [{ hash: '6cd9b5d', "
            "  full_hash: '6cd9b5d9fc941010ecd01776990757619ef30fd0' }];"
            "emit(["
            "  '6cd9b5d9fc941010ecd01776990757619ef30fd0',"
            "  '6cd9b5d',"
            "  '6CD9B5D9FC'"
            "].map(query => git_search.searchPlan(rows, query, 0, { mode: 'hash' })"
            ".matchCount));"
        )

        self.assertEqual(matches, [1, 1, 1])

    def test_hash_mode_refuses_non_hex_without_affecting_subject_mode(self):
        plans = self._run_node(
            "const rows = [{ hash: 'abcdef0', subject: 'zzz fixes' }];"
            "emit(['hash', 'subject'].map(mode =>"
            "  { const plan = git_search.searchPlan(rows, 'zzz', 0, { mode });"
            "    return { count: plan.matchCount, emptyText: plan.emptyText }; }));"
        )

        # A query that is not hexadecimal was never an id, so saying it is
        # "not in the loaded graph" invites scrolling for a commit that cannot
        # exist. Two empty results, two messages.
        self.assertEqual(
            plans,
            [
                {
                    "count": 0,
                    "emptyText": "Not a commit id — hexadecimal characters only",
                },
                {"count": 1, "emptyText": ""},
            ],
        )

    def test_hash_empty_state_is_blank_until_a_query_is_entered(self):
        empty_texts = self._run_node(
            "emit(['', 'fff'].map(query =>"
            "  git_search.searchPlan(commits, query, 0, { mode: 'hash' }).emptyText));"
        )

        self.assertEqual(empty_texts, ["", "No commit in the loaded graph"])

    def test_hash_mode_counts_once_per_commit_and_wraps_by_commit(self):
        plan = self._run_node(
            "const rows = ["
            "  { full_hash: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' },"
            "  { full_hash: '1a11111111111111111111111111111111111111' },"
            "  { full_hash: '2222222222222222222222222222222222222222' },"
            "  { full_hash: '333333333333333333333333333333333333333a' }"
            "];"
            "const make = active => git_search.searchPlan(rows, 'a', active, { mode: 'hash' });"
            "const first = make(0);"
            "emit({ counts: first.perCommit.map(ranges => ranges.length),"
            "  total: first.matchCount, wrapped: [make(-1).activeIndex, make(3).activeIndex] });"
        )

        self.assertEqual(plan["counts"], [1, 1, 0, 1])
        self.assertEqual(plan["total"], 3)
        self.assertEqual(plan["wrapped"], [2, 0])


class ExplorerGitSearchVisibilityTestCase(ExplorerGitSearchHarness):
    def test_toggle_opens_a_closed_bar_and_closes_an_open_one(self):
        opened = self._run_node(
            "const shut = { open: false, query: '', activeIndex: 0 };"
            "const open = git_search.nextVisibility(shut, 'toggle');"
            "emit([open.open, git_search.nextVisibility(open, 'toggle').open]);"
        )

        self.assertEqual(opened, [True, False])

    def test_closing_drops_the_query_and_the_active_index(self):
        closed = self._run_node(
            "emit(git_search.nextVisibility("
            "  { open: true, query: 'opt', activeIndex: 3 }, 'close'));"
        )

        self.assertEqual(
            closed,
            {"open": False, "query": "", "activeIndex": 0, "mode": "subject"},
        )

    def test_toggling_shut_drops_the_query_the_same_way(self):
        closed = self._run_node(
            "emit(git_search.nextVisibility("
            "  { open: true, query: 'opt', activeIndex: 3 }, 'toggle'));"
        )

        self.assertEqual(
            closed,
            {"open": False, "query": "", "activeIndex": 0, "mode": "subject"},
        )

    def test_opening_never_invents_a_query(self):
        opened = self._run_node(
            "emit(['toggle', 'open'].map(action =>"
            "  git_search.nextVisibility({ open: false, query: '', activeIndex: 0 }, action)));"
        )

        self.assertEqual(
            opened,
            [
                {"open": True, "query": "", "activeIndex": 0, "mode": "subject"},
                {"open": True, "query": "", "activeIndex": 0, "mode": "subject"},
            ],
        )

    def test_reopening_keeps_a_query_that_survived(self):
        # Nothing in the page leaves a query behind a closed bar today, but the
        # policy must not silently discard one if something ever does.
        reopened = self._run_node(
            "emit(git_search.nextVisibility("
            "  { open: false, query: 'opt', activeIndex: 2 }, 'open'));"
        )

        self.assertEqual(
            reopened,
            {"open": True, "query": "opt", "activeIndex": 2, "mode": "subject"},
        )

    def test_closing_drops_the_query_but_keeps_hash_mode(self):
        closed = self._run_node(
            "emit(git_search.nextVisibility("
            "  { open: true, query: '6cd9', activeIndex: 2, mode: 'hash' }, 'close'));"
        )

        self.assertEqual(
            closed,
            {"open": False, "query": "", "activeIndex": 0, "mode": "hash"},
        )

    def test_the_callers_state_is_never_mutated(self):
        before = self._run_node(
            "const state = { open: true, query: 'opt', activeIndex: 3 };"
            "git_search.nextVisibility(state, 'close');"
            "emit(state);"
        )

        self.assertEqual(before, {"open": True, "query": "opt", "activeIndex": 3})

    def test_a_missing_or_junk_state_opens_shut(self):
        states = self._run_node(
            "emit([null, 'nonsense', undefined].map(state =>"
            "  git_search.nextVisibility(state, 'toggle')));"
        )

        self.assertEqual(
            states,
            [{"open": True, "query": "", "activeIndex": 0, "mode": "subject"}] * 3,
        )


class ExplorerGitSearchMarkupTestCase(ExplorerGitSearchHarness):
    def test_marks_every_match_and_flags_only_the_active_one(self):
        html = self._run_node(
            "const plan = git_search.searchPlan(commits, 'opt', 4);"
            "emit(git_search.markedSubjectHtml("
            "  '(fix) opt-in, OPT-out, opt again', plan.perCommit[3], 2, plan.activeIndex));"
        )

        self.assertEqual(
            html,
            "(fix) <mark class=\"explorer-search-match\">opt</mark>-in, "
            "<mark class=\"explorer-search-match\">OPT</mark>-out, "
            "<mark class=\"explorer-search-match active\">opt</mark> again",
        )

    def test_subject_text_is_escaped_inside_and_outside_marks(self):
        html = self._run_node(
            "emit(git_search.markedSubjectHtml("
            "  'a <b> & \"x\"', [[0, 1]], 0, 1));"
        )

        self.assertEqual(
            html,
            "<mark class=\"explorer-search-match\">a</mark> "
            "&lt;b&gt; &amp; &quot;x&quot;",
        )

    def test_no_ranges_is_the_plain_escaped_subject(self):
        html = self._run_node(
            "emit(git_search.markedSubjectHtml('plain <subject>', [], 0, 0));"
        )

        self.assertEqual(html, "plain &lt;subject&gt;")


class ExplorerGitSearchHashMarkTestCase(ExplorerGitSearchHarness):
    def test_a_commit_without_a_match_gets_no_mark(self):
        mark = self._run_node("emit(git_search.hashMarkClass([], 0, 0));")

        self.assertEqual(mark, "")

    def test_a_matching_commit_is_marked(self):
        mark = self._run_node(
            "emit(git_search.hashMarkClass([[6, 9], [14, 17], [23, 26]], 2, 0));"
        )

        self.assertEqual(mark, " explorer-git-commit-search-hit")

    def test_only_the_commit_holding_the_active_match_is_active(self):
        marks = self._run_node(
            "const ranges = [[6, 9], [14, 17], [23, 26]];"
            "emit([1, 2, 3, 4, 5].map(active =>"
            "  git_search.hashMarkClass(ranges, 2, active)));"
        )

        self.assertEqual(
            marks,
            [
                " explorer-git-commit-search-hit",
                " explorer-git-commit-search-hit active",
                " explorer-git-commit-search-hit active",
                " explorer-git-commit-search-hit active",
                " explorer-git-commit-search-hit",
            ],
        )

    def test_hash_match_inside_the_abbreviation_is_wrapped_and_escaped(self):
        html = self._run_node(
            "emit(["
            "  git_search.markedHashHtml('6cD9b5d9fc94', [1, 4]),"
            "  git_search.markedHashHtml('a<23456789ab', [1, 3])"
            "]);"
        )

        self.assertEqual(
            html,
            [
                '6<mark class="explorer-git-commit-hash-match">cD9</mark>b5d',
                'a<mark class="explorer-git-commit-hash-match">&lt;2</mark>3456',
            ],
        )

    def test_hash_match_past_the_abbreviation_uses_only_the_row_tint(self):
        result = self._run_node(
            "const hash = '1234567abcdef000000000000000000000000000';"
            "const plan = git_search.searchPlan("
            "  [{ full_hash: hash }], 'abc', 0, { mode: 'hash' });"
            "emit({ html: git_search.markedHashHtml(hash, plan.perCommit[0][0]),"
            "  mark: git_search.hashMarkClass(plan.perCommit[0], 0, plan.activeIndex) });"
        )

        self.assertEqual(result["html"], "1234567")
        self.assertNotIn("<mark", result["html"])
        self.assertEqual(result["mark"], " explorer-git-commit-search-hit active")


class ExplorerGitCollapseAllPlanTestCase(ExplorerGitSearchHarness):
    """`changed` is the answer: it is what lets an Alt-click on an
    already-collapsed graph cost neither a render nor a presentation write."""

    def test_collapse_all_reports_a_change_for_a_populated_expansion_set(self):
        plan = self._run_node(
            "emit(git_search.collapseAllPlan(['commit:a', 'commit:b']));"
        )

        self.assertEqual(plan, {"changed": True})

    def test_an_empty_expansion_set_reports_no_change(self):
        plan = self._run_node("emit(git_search.collapseAllPlan([]));")

        self.assertEqual(plan, {"changed": False})

    def test_a_non_list_is_not_a_change_either(self):
        for value in ("emit(git_search.collapseAllPlan());",
                      "emit(git_search.collapseAllPlan(null));"):
            with self.subTest(call=value):
                self.assertEqual(self._run_node(value), {"changed": False})


if __name__ == "__main__":
    unittest.main()
