"""Behavioral coverage for the explorer's presentation tiers.

``explorer-tiers.js`` is DOM-free and require()-able, so the ladder, the
thresholds, the chunker and the notice each tier carries are executed in Node
rather than asserted as source text.

The rules under test are the ones the tiers exist to keep:

* the two source ceilings are independent — a file trips the tier by lines or
  by bytes, because 200k short lines and one 6 MiB line are different freezes;
* the chunker never materializes the buffer as lines and never loses a byte;
* the notice names every capability the tier removed, and says plainly that
  Find is unavailable rather than leaving a live control that does nothing;
* the diff ladder degrades, it does not refuse: the medium tier's overrides are
  exactly the three keys diff2html degrades gracefully on, and ``diffMaxChanges``
  / ``diffMaxLineLength`` — which make diff2html render "Diff too big to be
  displayed" instead of a diff — never appear.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
TIERS_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-tiers.js"

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for explorer tier tests")
class ExplorerTierHarness(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            "const tiers = require(" + json.dumps(str(TIERS_JS)) + ");\n"
            "const NL = String.fromCharCode(10);\n"
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
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])


class SourceTierTestCase(ExplorerTierHarness):
    def test_either_ceiling_trips_the_tier_on_its_own(self):
        """Lines drive the DOM, bytes drive the parse; neither implies the other.

        `pastByteLimit` is one enormous minified line: no DOM problem at all,
        and still a freeze.
        """
        verdicts = self._run_node(
            "emit({"
            "  small: tiers.sourceTier({ bytes: 1000, lines: 40 }),"
            "  atLineLimit: tiers.sourceTier({ bytes: 1000, lines: tiers.SOURCE_LARGE_MAX_LINES }),"
            "  pastLineLimit: tiers.sourceTier({ bytes: 1000, lines: tiers.SOURCE_LARGE_MAX_LINES + 1 }),"
            "  atByteLimit: tiers.sourceTier({ bytes: tiers.SOURCE_LARGE_MAX_BYTES, lines: 3 }),"
            "  pastByteLimit: tiers.sourceTier({ bytes: tiers.SOURCE_LARGE_MAX_BYTES + 1, lines: 1 })"
            "});"
        )

        self.assertEqual(verdicts["small"], "full")
        self.assertEqual(verdicts["atLineLimit"], "full")
        self.assertEqual(verdicts["pastLineLimit"], "large")
        self.assertEqual(verdicts["atByteLimit"], "full")
        self.assertEqual(verdicts["pastByteLimit"], "large")

    def test_line_count_matches_the_row_renderer_and_ignores_junk(self):
        counts = self._run_node(
            "emit({"
            "  trailing: tiers.lineCount('a' + NL + 'b' + NL),"
            "  untrimmed: tiers.lineCount('a' + NL + 'b'),"
            "  empty: tiers.lineCount(''),"
            "  single: tiers.lineCount('a'),"
            "  blankOnly: tiers.lineCount(NL),"
            "  notAString: tiers.lineCount(null)"
            "});"
        )

        # A trailing newline ends the last line rather than starting an empty
        # one — the same count the per-line renderer would build rows for.
        self.assertEqual(counts["trailing"], 2)
        self.assertEqual(counts["untrimmed"], 2)
        self.assertEqual(counts["empty"], 0)
        self.assertEqual(counts["single"], 1)
        self.assertEqual(counts["blankOnly"], 1)
        self.assertEqual(counts["notAString"], 0)

    def test_chunks_are_bounded_and_lossless(self):
        """The node count stops tracking the file's size, and no byte moves."""
        result = self._run_node(
            "const body = Array.from({ length: 42000 }, (_, i) => 'line ' + i).join(NL) + NL;"
            "const chunks = tiers.sourceChunks(body);"
            "emit({"
            "  count: chunks.length,"
            "  lossless: chunks.join('') === body,"
            "  perChunkLines: chunks.map(chunk => tiers.lineCount(chunk)),"
            "  chunkLimit: tiers.SOURCE_LARGE_CHUNK_LINES"
            "});"
        )

        limit = result["chunkLimit"]
        self.assertEqual(result["count"], 42000 // limit + 1)
        self.assertTrue(result["lossless"])
        self.assertEqual(result["perChunkLines"][:-1], [limit] * (result["count"] - 1))
        self.assertEqual(result["perChunkLines"][-1], 42000 % limit)

    def test_a_buffer_with_no_trailing_newline_still_terminates(self):
        """The chunk walk advances past the last line instead of spinning."""
        result = self._run_node(
            "const body = 'only line, no newline';"
            "const chunks = tiers.sourceChunks(body);"
            "emit({ count: chunks.length, lossless: chunks.join('') === body });"
        )

        self.assertEqual(result["count"], 1)
        self.assertTrue(result["lossless"])

    def test_notice_names_every_lost_capability_and_states_find_plainly(self):
        notice = self._run_node(
            "emit({"
            "  large: tiers.sourceTierNotice({ bytes: 9000000, lines: 240312 }),"
            "  full: tiers.sourceTierNotice({ bytes: 100, lines: 4 })"
            "});"
        )

        self.assertIsNone(notice["full"], "a file inside the tier explains nothing")
        large = notice["large"]
        # Every decoration the tier removes is named, so a missing gutter reads
        # as a decision rather than a bug.
        self.assertEqual(
            large["disabled"],
            [
                "syntax highlighting",
                "line numbers",
                "section folding",
                "change marks",
                "the overview ruler",
            ],
        )
        # Find is the only *control* that goes away, so it is stated plainly on
        # its own rather than buried in that list.
        self.assertEqual(large["findNote"], "Find is unavailable in this view.")
        self.assertNotIn("find", " ".join(large["disabled"]).lower())
        # The line count is reported, grouped, so the reader can see why.
        self.assertIn("240,312 lines", large["detail"])
        # And what survives is named too.
        self.assertIn("Download", large["retained"])
        self.assertIn("Edit", large["retained"])

    def test_the_notice_reads_the_same_metrics_the_switch_does(self):
        """A degraded pane always explains itself, boundary included.

        The tier decides on ``rows`` and the notice reports ``lines``, and the
        two disagree by exactly one for a file ending in a newline. A notice
        that re-derived the tier from ``lines`` therefore went silent at
        precisely the row the switch trips on, leaving the reader a pane with
        no gutter, no marks and no find and nothing saying why.
        """
        verdicts = self._run_node(
            "const at = 'x' + String.fromCharCode(10);"
            "const body = at.repeat(tiers.SOURCE_LARGE_MAX_LINES);"
            "const metrics = tiers.sourceMetrics(body);"
            "emit({"
            "  metrics,"
            "  tier: tiers.sourceTier(metrics),"
            "  notice: tiers.sourceTierNotice(metrics),"
            "  belowTier: tiers.sourceTier(tiers.sourceMetrics(at.repeat(10))),"
            "  belowNotice: tiers.sourceTierNotice(tiers.sourceMetrics(at.repeat(10)))"
            "});"
        )

        # The boundary itself: one more row than the reader-facing count.
        self.assertEqual(verdicts["metrics"]["lines"], 20000)
        self.assertEqual(verdicts["metrics"]["rows"], 20001)
        self.assertEqual(verdicts["tier"], "large")
        self.assertIsNotNone(
            verdicts["notice"],
            "a pane rendered in the large tier must carry its notice",
        )
        self.assertEqual(
            verdicts["notice"]["findNote"], "Find is unavailable in this view."
        )
        # The reader-facing count is still what the notice reports.
        self.assertIn("20,000 lines", verdicts["notice"]["detail"])
        # And a file inside the tier still explains nothing.
        self.assertEqual(verdicts["belowTier"], "full")
        self.assertIsNone(verdicts["belowNotice"])

    def test_the_notice_names_the_ceiling_that_actually_fired(self):
        """Two independent ceilings, so the notice cannot always report lines.

        The byte ceiling exists for the minified bundle, and the minified
        bundle is the case where a line-only notice reads worst: one enormous
        line reported as "1 lines rendered as plain text" — ungrammatical, and
        an answer to a question nobody asked, since the reader's problem there
        is 5 MiB on one line. Each trigger reports itself; lines win when both
        fired, because the row count is what the reader can see on screen.
        """
        reported = self._run_node(
            "const oneHugeLine = 'x'.repeat(5 * 1024 * 1024);"
            "const manyShortLines = ('x' + NL).repeat(30000);"
            "emit({"
            "  byBytes: tiers.sourceTierNotice(tiers.sourceMetrics(oneHugeLine)).detail,"
            "  byLines: tiers.sourceTierNotice(tiers.sourceMetrics(manyShortLines)).detail,"
            "  byBoth: tiers.sourceTierNotice({"
            "    bytes: 5 * 1024 * 1024, lines: 30000, rows: 30001"
            "  }).detail,"
            "  singular: tiers.sourceTierReason({ bytes: 10, lines: 1, rows: 1 }),"
            "  kilobytes: tiers.sourceTierReason({ bytes: 700 * 1024, lines: 1, rows: 1 })"
            "});"
        )

        # The byte-triggered file is described in bytes, not as "1 lines".
        self.assertIn("5.0 MB", reported["byBytes"])
        self.assertNotIn("line", reported["byBytes"])
        # The line-triggered file is still described in lines, grouped.
        self.assertIn("30,000 lines", reported["byLines"])
        # Both ceilings crossed: the visible one is named.
        self.assertIn("30,000 lines", reported["byBoth"])
        # Either way the sentence still says what the tier did.
        for detail in (reported["byBytes"], reported["byLines"]):
            self.assertIn("rendered as plain text", detail)
        # Pluralisation is real, and the byte side has its own units.
        self.assertEqual(reported["singular"], "10 bytes")
        self.assertEqual(reported["kilobytes"], "700.0 KB")

    def test_capability_predicate_agrees_with_the_notice(self):
        """One predicate, so the notice and the switch cannot promise different things."""
        allowed = self._run_node(
            "const caps = ['find', 'download', 'upload', 'edit', 'changeMarks', 'folding'];"
            "emit({"
            "  full: caps.filter(cap => tiers.sourceTierAllows('full', cap)),"
            "  large: caps.filter(cap => tiers.sourceTierAllows('large', cap))"
            "});"
        )

        self.assertEqual(
            allowed["full"],
            ["find", "download", "upload", "edit", "changeMarks", "folding"],
        )
        # Upload is chrome beside the file, not a property of how the Source
        # view rendered it, so the tier costs it nothing — and says so.
        self.assertEqual(allowed["large"], ["download", "upload", "edit"])


class DiffTierTestCase(ExplorerTierHarness):
    def test_ladder_bands_on_either_bytes_or_lines(self):
        verdicts = self._run_node(
            "emit({"
            "  tiny: tiers.diffTier({ bytes: 400, lines: 12 }),"
            "  mediumByBytes: tiers.diffTier({ bytes: tiers.DIFF_SMALL_MAX_BYTES + 1, lines: 12 }),"
            "  mediumByLines: tiers.diffTier({ bytes: 400, lines: tiers.DIFF_SMALL_MAX_LINES + 1 }),"
            "  largeByBytes: tiers.diffTier({ bytes: tiers.DIFF_MEDIUM_MAX_BYTES + 1, lines: 12 }),"
            "  largeByLines: tiers.diffTier({ bytes: 400, lines: tiers.DIFF_MEDIUM_MAX_LINES + 1 })"
            "});"
        )

        self.assertEqual(verdicts["tiny"], "small")
        self.assertEqual(verdicts["mediumByBytes"], "medium")
        self.assertEqual(verdicts["mediumByLines"], "medium")
        self.assertEqual(verdicts["largeByBytes"], "large")
        self.assertEqual(verdicts["largeByLines"], "large")

    def test_ladder_sits_under_the_backend_truncation_ceiling(self):
        """`large` is a band, not an overflow: a truncated diff still renders."""
        limits = self._run_node(
            "emit({"
            "  mediumBytes: tiers.DIFF_MEDIUM_MAX_BYTES,"
            "  mediumLines: tiers.DIFF_MEDIUM_MAX_LINES"
            "});"
        )

        # EXPLORER_GIT_DIFF_MAX_BYTES / _MAX_LINES in web/explorer.py.
        self.assertLess(limits["mediumBytes"], 256 * 1024)
        self.assertLess(limits["mediumLines"], 4000)

    def test_medium_overrides_degrade_and_never_refuse(self):
        overrides = self._run_node(
            "emit({"
            "  small: tiers.diffTierConfigOverrides('small'),"
            "  medium: tiers.diffTierConfigOverrides('medium'),"
            "  large: tiers.diffTierConfigOverrides('large')"
            "});"
        )

        self.assertEqual(overrides["small"], {})
        # The large tier never reaches diff2html at all, so it contributes no
        # configuration — it is rendered by GridVibe's own side-by-side markup.
        self.assertEqual(overrides["large"], {})
        # Exactly the three keys diff2html degrades gracefully on: rows, line
        # numbers, side-by-side and the undo buttons all survive them.
        self.assertEqual(
            overrides["medium"],
            {"matching": "none", "diffStyle": "line", "highlight": False},
        )
        # The refusal levers must never appear: either one makes diff2html
        # render "Diff too big to be displayed" and no diff at all.
        for refusal_lever in ("diffMaxChanges", "diffMaxLineLength"):
            self.assertNotIn(refusal_lever, overrides["medium"])
            self.assertNotIn(refusal_lever, TIERS_JS.read_text(encoding="utf-8").split("*/")[-1])

    def test_both_degraded_tiers_explain_themselves(self):
        notices = self._run_node(
            "emit({"
            "  small: tiers.diffTierNotice('small'),"
            "  medium: tiers.diffTierNotice('medium'),"
            "  large: tiers.diffTierNotice('large')"
            "});"
        )

        self.assertIsNone(notices["small"])
        for tier in ("medium", "large"):
            with self.subTest(tier=tier):
                # Undo is the affordance a tier must never silently remove, so
                # both notices say it is still there.
                self.assertIn("undo", notices[tier]["detail"].lower())
                self.assertTrue(notices[tier]["title"])


if __name__ == "__main__":
    unittest.main()
