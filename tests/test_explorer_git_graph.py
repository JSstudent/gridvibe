"""Behavioral coverage for the Git sidebar's commit card and page ladder.

`explorer-git-graph.js` is DOM-free and require()-able, so both policies are
executed in Node rather than asserted as source text.

The card's rules: it reads the author, the authored date and the object id off
the row the sidebar already loaded, renders the timestamp in the *author's*
own offset (so a reader in another zone never sees a commit move across a date
boundary), and drops a row it has nothing to report for instead of printing an
"unknown" that reads as a recorded fact. It also says which of its rows hosts
a copy control — and only that; what the control copies is
`explorer-git-menu.js`'s answer, and the card that paints both is covered in
`test_explorer_git_card.py`.

The ladder's rules: every number comes from the server's answer, never from a
constant mirrored on this side; the button disappears at the end of the
history and at the ceiling, and each of those two ends says which it was, so a
control that stops appearing reads as an answer rather than as a breakage.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
GIT_GRAPH_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-git-graph.js"

NODE = shutil.which("node")

# 12:00 UTC on 30 August 2026, the clock every relative age below is read
# against. Fixed, so the assertions do not drift with the calendar.
NOW = 1788091200000

COMMIT = {
    "hash": "6caf331",
    "full_hash": "6caf3319ab1d4e5f60718293a4b5c6d7e8f90123",
    "subject": "(HEAD -> main) (fix) hold the scroll",
    "message": "(fix) hold the scroll",
    "refs": "HEAD -> main",
    "author": "Ada Lovelace",
    "authored_at": "2026-08-28T14:03:11+02:00",
}


@unittest.skipUnless(NODE, "Node.js is required for Git graph-policy tests")
class ExplorerGitGraphHarness(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            "const graph = require(" + json.dumps(str(GIT_GRAPH_JS)) + ");\n"
            "const commit = " + json.dumps(COMMIT) + ";\n"
            "const NOW = " + str(NOW) + ";\n"
            "const emit = value => console.log(JSON.stringify(value));\n"
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
                # The card's separators and the ceiling notice carry em dashes
                # and middots; Node writes UTF-8 whatever the console codepage.
                encoding="utf-8",
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])


class ExplorerGitCommitCardTestCase(ExplorerGitGraphHarness):
    def test_card_reports_the_four_fields_a_row_has_no_room_for(self):
        card = self._run_node(
            "emit(graph.commitCard(commit, { now: NOW, expanded: false }));"
        )

        # The decoration is on the row's subject already, so the card leads
        # with the message the author actually wrote.
        self.assertEqual(card["message"], "(fix) hold the scroll")
        self.assertEqual(
            [(row["label"], row["value"]) for row in card["rows"]],
            [
                ("Author", "Ada Lovelace"),
                ("Date", "28 Aug 2026, 14:03  ·  2 days ago"),
                ("Commit", "6caf3319ab1d4e5f60718293a4b5c6d7e8f90123"),
                ("Refs", "HEAD -> main"),
            ],
        )
        # The object id is the only value set in the row font.
        self.assertEqual(
            [row["label"] for row in card["rows"] if row.get("mono")],
            ["Commit"],
        )

    def test_a_field_the_log_did_not_carry_is_dropped_not_blanked(self):
        # A shorter card, never "Author: unknown" -- which reads as a fact the
        # repository recorded rather than as a field nobody wrote.
        labels = self._run_node(
            "const bare = { hash: 'abc1234', message: 'no metadata' };"
            "emit(graph.commitCard(bare, { now: NOW }).rows.map(row => row.label));"
        )

        self.assertEqual(labels, ["Commit"])

    def test_the_timestamp_is_the_authors_own_wall_clock_not_the_readers(self):
        # Same instant, two offsets. Converting to the reader's zone would move
        # the second one back a day for anyone west of it, which is exactly
        # what `git log` does not do.
        dates = self._run_node(
            "emit(["
            "  '2026-08-28T14:03:11+02:00',"
            "  '2026-08-28T00:30:00+13:00',"
            "  '2026-08-28T00:30:00Z',"
            "  'not-a-date'"
            "].map(iso => graph.absoluteCommitDate(iso)));"
        )

        self.assertEqual(
            dates,
            ["28 Aug 2026, 14:03", "28 Aug 2026, 00:30", "28 Aug 2026, 00:30", ""],
        )

    def test_relative_age_steps_through_the_units_and_never_runs_backwards(self):
        ages = self._run_node(
            "const at = seconds =>"
            "  new Date(NOW - seconds * 1000).toISOString();"
            "emit(["
            "  graph.relativeCommitDate(at(10), NOW),"
            "  graph.relativeCommitDate(at(60), NOW),"
            "  graph.relativeCommitDate(at(7200), NOW),"
            "  graph.relativeCommitDate(at(86400), NOW),"
            "  graph.relativeCommitDate(at(1209600), NOW),"
            "  graph.relativeCommitDate(at(7889400), NOW),"
            "  graph.relativeCommitDate(at(63115200), NOW),"
            "  graph.relativeCommitDate(at(-9000), NOW),"
            "  graph.relativeCommitDate('nonsense', NOW)"
            "]);"
        )

        self.assertEqual(
            ages,
            [
                "just now",
                "1 minute ago",
                "2 hours ago",
                "1 day ago",
                "2 weeks ago",
                "3 months ago",
                "2 years ago",
                # A commit dated in the future is not "-3 hours ago".
                "just now",
                "",
            ],
        )

    def test_the_hint_names_the_gesture_the_row_answers_to(self):
        hints = self._run_node(
            "emit([false, true].map(expanded =>"
            "  graph.commitCard(commit, { now: NOW, expanded }).hint));"
        )

        self.assertNotEqual(hints[0], hints[1])
        # The old native title's "(Alt: collapse all)" lives here now, and only
        # on a row where the gesture does something.
        self.assertNotIn("Alt", hints[0])
        self.assertIn("Alt-click", hints[1])

    def test_the_button_label_carries_the_same_facts_as_one_line(self):
        # The card is only on screen while the reader keeps it there, so the
        # row's own accessible name has to say everything the card does.
        summary = self._run_node(
            "emit(graph.commitCard(commit, { now: NOW }).summary);"
        )

        self.assertIn("(fix) hold the scroll", summary)
        self.assertIn("Ada Lovelace", summary)
        self.assertIn("28 Aug 2026", summary)

    def test_the_object_id_is_the_one_row_that_hosts_a_copy_control(self):
        # Tagged, not found by label: the adapter must never match on a word
        # the card also prints. The message line's control is structural, so it
        # needs no tag of its own.
        rows = self._run_node(
            "emit(graph.commitCard(commit, { now: NOW }).rows"
            "  .map(row => [row.label, row.copy || null]));"
        )

        self.assertEqual(
            rows,
            [
                ["Author", None],
                ["Date", None],
                ["Commit", "hash"],
                ["Refs", None],
            ],
        )

    def test_a_row_carrying_only_a_short_hash_still_hosts_the_control(self):
        # The control is offered on whatever id the log carried; whether it is
        # usable is explorer-git-menu.js's call, not this one's.
        rows = self._run_node(
            "const bare = { hash: 'abc1234', message: 'no metadata' };"
            "emit(graph.commitCard(bare, { now: NOW }).rows"
            "  .map(row => [row.label, row.value, row.copy || null]));"
        )

        self.assertEqual(rows, [["Commit", "abc1234", "hash"]])

    def test_a_commit_with_no_id_at_all_hosts_no_copy_control(self):
        # The row is dropped, so there is nothing for a control to sit on --
        # the card is shorter rather than carrying a control over nothing.
        rows = self._run_node(
            "const bare = { author: 'Ada', message: 'no id' };"
            "emit(graph.commitCard(bare, { now: NOW }).rows"
            "  .map(row => row.copy || null));"
        )

        self.assertEqual(rows, [None])


class ExplorerGitCardPlacementTestCase(ExplorerGitGraphHarness):
    """Which side of its row the card opens on.

    The panel is a scroller, so a card opened from one of the last rows would
    be clipped by its bottom edge -- an affordance nobody can read.
    """

    def _place(self, row_top, row_bottom, card_height, view=(0, 400), gap=2):
        return self._run_node(
            "emit(graph.cardPlacement({"
            f"  rowTop: {row_top}, rowBottom: {row_bottom},"
            f"  viewTop: {view[0]}, viewBottom: {view[1]},"
            f"  cardHeight: {card_height}, gap: {gap}"
            "}));"
        )

    def test_a_row_with_room_under_it_opens_downward(self):
        # The default, because that is where the eye is already travelling.
        self.assertEqual(self._place(40, 58, 120), "below")

    def test_a_row_near_the_bottom_flips_above_instead_of_being_clipped(self):
        self.assertEqual(self._place(330, 348, 120), "above")

    def test_an_exact_fit_below_is_still_below(self):
        # 400 - 348 = 52, and the card plus its 2px overlap is exactly 52.
        self.assertEqual(self._place(330, 348, 50), "below")
        self.assertEqual(self._place(330, 348, 51), "above")

    def test_a_card_taller_than_the_panel_takes_the_roomier_side(self):
        # Neither side fits, so it loses the least rather than defaulting.
        self.assertEqual(self._place(300, 318, 900), "above")
        self.assertEqual(self._place(40, 58, 900), "below")

    def test_a_scrolled_panel_is_measured_by_its_own_box_not_the_page(self):
        # The same row geometry, once with the panel starting at the top of the
        # window and once with it pushed down: only the panel's own edges
        # decide, so a sidebar below a tall header behaves identically.
        self.assertEqual(self._place(330, 348, 120, view=(0, 400)), "above")
        self.assertEqual(self._place(330, 348, 120, view=(300, 700)), "below")

class ExplorerGitPagePlanTestCase(ExplorerGitGraphHarness):
    @staticmethod
    def _repo(loaded, limit, has_more, page=60, ceiling=300):
        return {
            "commits": [{"hash": f"{n:07d}"} for n in range(loaded)],
            "commit_limit": limit,
            "commit_page": page,
            "commit_limit_max": ceiling,
            "commit_has_more": has_more,
        }

    def _plan(self, repo, options="{}"):
        return self._run_node(
            "emit(graph.pagePlan(" + json.dumps(repo) + ", " + options + "));"
        )

    def test_a_full_page_with_more_behind_it_offers_the_next_page(self):
        plan = self._plan(self._repo(60, 60, True))

        self.assertTrue(plan["visible"])
        self.assertTrue(plan["canLoadMore"])
        # The step and the ceiling come from the payload, never from a constant
        # mirrored on this side of the boundary.
        self.assertEqual(plan["nextLimit"], 120)
        self.assertEqual(plan["label"], "Show 60 more")

    def test_the_last_step_is_clipped_to_the_servers_ceiling(self):
        plan = self._plan(self._repo(280, 280, True, page=60, ceiling=300))

        self.assertEqual(plan["nextLimit"], 300)
        self.assertEqual(plan["label"], "Show 20 more")

    def test_a_graph_inside_its_first_page_says_nothing_at_all(self):
        plan = self._plan(self._repo(12, 60, False))

        # Nothing a reader could do about it, so no control and no note.
        self.assertFalse(plan["visible"])
        self.assertEqual(plan["detail"], "")

    def test_an_expanded_graph_that_ran_out_says_so_instead_of_vanishing(self):
        plan = self._plan(self._repo(137, 180, False))

        self.assertTrue(plan["visible"])
        self.assertFalse(plan["canLoadMore"])
        self.assertFalse(plan["atCeiling"])
        self.assertEqual(plan["detail"], "All 137 commits in this scope.")

    def test_the_ceiling_is_reported_as_a_bound_not_as_the_end_of_history(self):
        plan = self._plan(self._repo(300, 300, True, ceiling=300))

        self.assertTrue(plan["visible"])
        self.assertFalse(plan["canLoadMore"])
        self.assertTrue(plan["atCeiling"])
        self.assertIsNone(plan["nextLimit"])
        self.assertIn("300", plan["detail"])
        self.assertIn("stops here", plan["detail"])

    def test_a_read_in_flight_disables_the_button_without_hiding_it(self):
        plan = self._plan(self._repo(60, 60, True), '{ loading: true }')

        self.assertTrue(plan["visible"])
        self.assertFalse(plan["canLoadMore"])
        self.assertEqual(plan["label"], "Loading more...")

    def test_a_payload_without_the_page_fields_offers_nothing(self):
        # A summary from before the ladder existed, and the sidebar's own empty
        # model: neither may render a control that cannot load anything.
        plans = self._run_node(
            "emit([{ commits: [{ hash: 'a' }] }, null, {}].map(repo =>"
            "  graph.pagePlan(repo, {})));"
        )

        self.assertEqual([plan["visible"] for plan in plans], [False, False, False])
        self.assertEqual([plan["nextLimit"] for plan in plans], [None, None, None])


if __name__ == "__main__":
    unittest.main()
