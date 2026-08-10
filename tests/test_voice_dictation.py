"""Behavioral coverage for the two dictation decisions.

`web/static/js/voice-dictation.js` is DOM-free and require()-able, so the
routing rule and the caret string math are executed in Node here rather than
asserted as source text. The routing table is invariant I1/I2/I5 of
`docs/explorer_editor_voice_capture_plan_2026-08-10.md`: a transcript reaches
exactly one place, only while the edit session that started the capture is
still open and not saving, and a pane with neither a terminal nor an editor is
never sent anything.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

VOICE_DICTATION_JS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "js"
    / "voice-dictation.js"
)

NODE = shutil.which("node")


class NodeHarnessMixin:
    """Run one harness against the real module with a JSON case list."""

    def _run_node(self, harness: str, cases):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(VOICE_DICTATION_JS),
                    json.dumps(cases),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "Node.js is required for voice dictation tests")
class ResolveVoiceDeliveryTestCase(NodeHarnessMixin, unittest.TestCase):
    """Every transcript is routed to exactly one destination."""

    def _resolve(self, cases):
        return self._run_node(
            """
            const dictation = require(process.argv[2]);
            const cases = JSON.parse(process.argv[3]);
            process.stdout.write(JSON.stringify(
                cases.map(input => dictation.resolveVoiceDelivery(input))
            ));
            """,
            cases,
        )

    def test_a_bound_open_editor_receives_the_final_transcript(self):
        (result,) = self._resolve(
            [
                {
                    "hasTerm": False,
                    "edit": {"epoch": 7, "saving": False},
                    "epoch": 7,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "editor")

    def test_a_transcript_that_outlives_its_editor_is_dropped(self):
        # Esc / Cancel / tab close / pane close while recording: the edit state
        # is gone but the pane still owes a transcript.
        (result,) = self._resolve(
            [
                {
                    "hasTerm": False,
                    "edit": None,
                    "epoch": 7,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "drop")
        self.assertEqual(result["reason"], "no-edit")

    def test_a_transcript_from_an_earlier_capture_is_dropped(self):
        (result,) = self._resolve(
            [
                {
                    "hasTerm": False,
                    "edit": {"epoch": 8, "saving": False},
                    "epoch": 7,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "drop")
        self.assertEqual(result["reason"], "epoch-mismatch")

    def test_a_transcript_landing_across_a_save_is_dropped(self):
        # The draft was posted at request time and is about to be discarded, so
        # words written into it now would disappear with no error anywhere.
        (result,) = self._resolve(
            [
                {
                    "hasTerm": False,
                    "edit": {"epoch": 7, "saving": True},
                    "epoch": 7,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "drop")
        self.assertEqual(result["reason"], "saving")

    def test_an_unbound_capture_on_a_terminal_pane_still_types(self):
        (result,) = self._resolve(
            [
                {
                    "hasTerm": True,
                    "edit": None,
                    "epoch": None,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "terminal")

    def test_a_pane_with_no_terminal_and_no_editor_receives_nothing(self):
        (result,) = self._resolve(
            [
                {
                    "hasTerm": False,
                    "edit": None,
                    "epoch": None,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "drop")
        self.assertEqual(result["reason"], "no-target")

    def test_a_bound_editor_never_routes_to_the_terminal(self):
        # A pane that has both a terminal and a bound editor is still an editor
        # delivery — dictated text is never emitted as terminal_input (I1).
        (result,) = self._resolve(
            [
                {
                    "hasTerm": True,
                    "edit": {"epoch": 3, "saving": False},
                    "epoch": 3,
                    "isFinal": True,
                    "hasText": True,
                }
            ]
        )
        self.assertEqual(result["target"], "editor")

    def test_partials_preview_in_both_editor_and_terminal_shapes(self):
        editor, terminal = self._resolve(
            [
                {
                    "hasTerm": False,
                    "edit": {"epoch": 2, "saving": False},
                    "epoch": 2,
                    "isFinal": False,
                    "hasText": True,
                },
                {
                    "hasTerm": True,
                    "edit": None,
                    "epoch": None,
                    "isFinal": False,
                    "hasText": True,
                },
            ]
        )
        self.assertEqual(editor["target"], "preview")
        self.assertEqual(terminal["target"], "preview")

    def test_an_empty_transcript_is_dropped_whether_final_or_not(self):
        partial, final = self._resolve(
            [
                {
                    "hasTerm": True,
                    "edit": None,
                    "epoch": None,
                    "isFinal": False,
                    "hasText": False,
                },
                {
                    "hasTerm": True,
                    "edit": None,
                    "epoch": None,
                    "isFinal": True,
                    "hasText": False,
                },
            ]
        )
        self.assertEqual(partial["target"], "drop")
        self.assertEqual(final["target"], "drop")


@unittest.skipUnless(NODE, "Node.js is required for voice dictation tests")
class ComposeDictationInsertTestCase(NodeHarnessMixin, unittest.TestCase):
    """One space only where it belongs, and never a newline."""

    def _compose(self, cases):
        return self._run_node(
            """
            const dictation = require(process.argv[2]);
            const cases = JSON.parse(process.argv[3]);
            process.stdout.write(JSON.stringify(cases.map(input => {
                const composed = dictation.composeDictationInsert(input);
                return {
                    text: composed.text,
                    spacedBefore: composed.spacedBefore,
                    // What the buffer becomes once the caller replaces the
                    // selected range with the composed string.
                    value: (input.before || '') + composed.text + (input.after || '')
                };
            })));
            """,
            cases,
        )

    def test_an_empty_buffer_takes_the_transcript_verbatim(self):
        (result,) = self._compose([{"before": "", "text": "hello world"}])
        self.assertEqual(result["text"], "hello world")
        self.assertFalse(result["spacedBefore"])

    def test_a_caret_after_a_word_gains_one_leading_space(self):
        (result,) = self._compose([{"before": "hello", "text": "world"}])
        self.assertEqual(result["text"], " world")
        self.assertTrue(result["spacedBefore"])

    def test_whitespace_and_openers_before_the_caret_add_nothing(self):
        space, newline, bracket, tab = self._compose(
            [
                {"before": "hello ", "text": "world"},
                {"before": "hello\n", "text": "world"},
                {"before": "call(", "text": "value"},
                {"before": "\t", "text": "value"},
            ]
        )
        self.assertEqual(space["text"], "world")
        self.assertEqual(newline["text"], "world")
        self.assertEqual(bracket["text"], "value")
        self.assertEqual(tab["text"], "value")

    def test_a_transcript_that_supplies_its_own_lead_adds_nothing(self):
        spaced, period, comma = self._compose(
            [
                {"before": "hello", "text": " world"},
                {"before": "hello", "text": ". Next sentence"},
                {"before": "hello", "text": ", then this"},
            ]
        )
        self.assertEqual(spaced["text"], " world")
        self.assertFalse(spaced["spacedBefore"])
        self.assertEqual(period["text"], ". Next sentence")
        self.assertEqual(comma["text"], ", then this")

    def test_replacing_a_selection_leaves_the_rest_of_the_buffer_alone(self):
        (result,) = self._compose(
            [
                {
                    "before": "keep this ",
                    "after": " and this",
                    "selected": "drop me",
                    "text": "new words",
                }
            ]
        )
        # The preceding character is a space, so nothing is prepended, and the
        # surrounding text is byte-identical either side of the replacement.
        self.assertEqual(result["text"], "new words")
        self.assertEqual(result["value"], "keep this new words and this")

    def test_a_multiline_transcript_never_inserts_a_newline(self):
        (result,) = self._compose(
            [{"before": "", "text": "first line\nsecond line\r\nthird"}]
        )
        self.assertNotIn("\n", result["text"])
        self.assertNotIn("\r", result["text"])
        self.assertEqual(result["text"], "first line second line third")

    def test_an_empty_transcript_composes_to_nothing(self):
        (result,) = self._compose([{"before": "hello", "text": ""}])
        self.assertEqual(result["text"], "")
        self.assertFalse(result["spacedBefore"])
        self.assertEqual(result["value"], "hello")


if __name__ == "__main__":
    unittest.main()
