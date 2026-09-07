"""What a pane is announcing about itself, read out of a real output stream.

`web/agent_activity.py` is text-in/values-out, so every case here runs the real
parser over the bytes an agent actually writes rather than asserting on its
source.

What is pinned, and why each one is a way the reading could go quietly wrong:

- **Both title sequences and both terminators.** Agents differ on OSC 0 vs OSC 2
  and on BEL vs ST; reading only one pairing would work perfectly against one
  agent and report nothing at all for another.
- **OSC 9;4 and OSC 9;9 share a prefix and nothing else.** The cwd observer and
  this one read the same stream, so each parser is checked against the other's
  sequence: a progress reading that lands in `current_directory`, or a
  directory that paints a progress bar, is the failure this prevents.
- **A sequence split across two reads still lands**, and the residue that makes
  that work is bounded — a stream that opens a sequence and never closes it may
  not grow a per-connection buffer.
- **The state says which input decided it.** Liveness falls back to output
  cadence precisely because most agents publish no progress at all, and a
  published state that has gone stale stops meaning "now" — a crashed agent
  never sends the `0` that would clear its own progress.
- **"Nothing observed" is not "idle".** A pane whose transport never came up
  and an agent waiting for you are different answers.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.agent_activity import (  # noqa: E402
    ACTIVITY_IDLE,
    ACTIVITY_SOURCE_NONE,
    ACTIVITY_SOURCE_OUTPUT,
    ACTIVITY_SOURCE_PROGRESS,
    ACTIVITY_UNKNOWN,
    ACTIVITY_WORKING,
    AGENT_EVENT_PROGRESS,
    AGENT_EVENT_TITLE,
    AGENT_RESIDUE_MAX_CHARS,
    AGENT_TITLE_MAX_CHARS,
    PROGRESS_STATE_ERROR,
    PROGRESS_STATE_INDETERMINATE,
    PROGRESS_STATE_NONE,
    PROGRESS_STATE_NORMAL,
    apply_agent_events,
    blank_agent_activity,
    describe_agent_activity,
    normalize_agent_title,
    note_agent_output,
    parse_agent_events,
    parse_progress_payload,
)
from web.terminal_cwd import CWD_EVENT_DIRECTORY, parse_cwd_events  # noqa: E402

ESC = "\x1b"
BEL = "\x07"
ST = ESC + "\\"


def title_sequence(text: str, code: str = "0", terminator: str = BEL) -> str:
    return f"{ESC}]{code};{text}{terminator}"


def progress_sequence(payload: str, terminator: str = BEL) -> str:
    return f"{ESC}]9;4;{payload}{terminator}"


class AgentTitleParserTestCase(unittest.TestCase):
    def test_osc0_and_osc2_both_report_a_title(self):
        for code in ("0", "2"):
            with self.subTest(code=code):
                events, _ = parse_agent_events(title_sequence("Claude Code", code))
                self.assertEqual(events, [(AGENT_EVENT_TITLE, "Claude Code")])

    def test_both_terminators_are_accepted(self):
        for terminator in (BEL, ST):
            with self.subTest(terminator=repr(terminator)):
                events, _ = parse_agent_events(title_sequence("codex", "2", terminator))
                self.assertEqual(events, [(AGENT_EVENT_TITLE, "codex")])

    def test_the_icon_name_sequence_is_not_a_title(self):
        # OSC 1 sets the icon name, which is not what a pane calls itself.
        events, residue = parse_agent_events(f"{ESC}]1;iconified{BEL}")
        self.assertEqual(events, [])
        self.assertEqual(residue, "")

    def test_the_last_title_in_a_chunk_wins(self):
        events, _ = parse_agent_events(
            title_sequence("first") + "output" + title_sequence("second")
        )
        self.assertEqual(
            events,
            [(AGENT_EVENT_TITLE, "first"), (AGENT_EVENT_TITLE, "second")],
        )
        record = apply_agent_events(None, events, 10.0)
        self.assertEqual(record["title"], "second")

    def test_a_title_split_across_two_reads_still_lands(self):
        events, residue = parse_agent_events(f"{ESC}]0;Claude: fixing")
        self.assertEqual(events, [])
        self.assertTrue(residue)
        events, residue = parse_agent_events(f" the tests{BEL}rest of the frame", residue)
        self.assertEqual(events, [(AGENT_EVENT_TITLE, "Claude: fixing the tests")])
        self.assertEqual(residue, "")

    def test_an_unterminated_sequence_cannot_grow_the_residue(self):
        _, residue = parse_agent_events(f"{ESC}]0;" + "A" * (AGENT_RESIDUE_MAX_CHARS * 4))
        self.assertLessEqual(len(residue), AGENT_RESIDUE_MAX_CHARS)
        for _ in range(4):
            _, residue = parse_agent_events("B" * (AGENT_RESIDUE_MAX_CHARS * 4), residue)
            self.assertLessEqual(len(residue), AGENT_RESIDUE_MAX_CHARS)

    def test_plain_output_carries_no_residue(self):
        events, residue = parse_agent_events("just text, no escapes at all")
        self.assertEqual(events, [])
        self.assertEqual(residue, "")


class AgentTitleNormalizationTestCase(unittest.TestCase):
    def test_control_characters_and_runs_of_space_are_flattened(self):
        self.assertEqual(
            normalize_agent_title("Claude\r\n  Code\tworking"),
            "Claude Code working",
        )

    def test_a_long_title_is_bounded(self):
        title = normalize_agent_title("x" * (AGENT_TITLE_MAX_CHARS * 3))
        self.assertLessEqual(len(title), AGENT_TITLE_MAX_CHARS + 1)
        self.assertTrue(title.endswith("…"))

    def test_clearing_the_title_clears_the_record(self):
        record = apply_agent_events(None, [(AGENT_EVENT_TITLE, "Claude Code")], 1.0)
        self.assertEqual(record["title"], "Claude Code")
        record = apply_agent_events(record, [(AGENT_EVENT_TITLE, "")], 2.0)
        self.assertEqual(record["title"], "")


class AgentProgressParserTestCase(unittest.TestCase):
    def test_every_documented_state_is_read(self):
        cases = {
            "0": (PROGRESS_STATE_NONE, 0),
            "1;40": (PROGRESS_STATE_NORMAL, 40),
            "2;10": (PROGRESS_STATE_ERROR, 10),
            "3": (PROGRESS_STATE_INDETERMINATE, 0),
        }
        for payload, expected in cases.items():
            with self.subTest(payload=payload):
                self.assertEqual(parse_progress_payload(payload), expected)

    def test_an_out_of_range_percentage_is_clamped(self):
        self.assertEqual(parse_progress_payload("1;900"), (PROGRESS_STATE_NORMAL, 100))
        self.assertEqual(parse_progress_payload("1;-5"), (PROGRESS_STATE_NORMAL, 0))

    def test_an_unreadable_payload_publishes_nothing(self):
        # An unknown state code is left alone rather than guessed at: guessing
        # paints a progress bar nothing asked for.
        for payload in ("9;10", "", "busy"):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_progress_payload(payload))
        record = apply_agent_events(None, [(AGENT_EVENT_PROGRESS, "9;10")], 5.0)
        self.assertEqual(record["progress_state"], PROGRESS_STATE_NONE)
        self.assertEqual(record["progress_at"], 0.0)

    def test_a_progress_sequence_is_read_from_a_live_stream(self):
        events, _ = parse_agent_events(
            "frame" + progress_sequence("1;72") + "more frame"
        )
        self.assertEqual(events, [(AGENT_EVENT_PROGRESS, "1;72")])
        record = apply_agent_events(None, events, 7.0)
        self.assertEqual(record["progress_state"], PROGRESS_STATE_NORMAL)
        self.assertEqual(record["progress_value"], 72)


class AgentAndCwdObserversTestCase(unittest.TestCase):
    """The two readers of one stream, checked against each other's sequences."""

    def test_the_cwd_sequence_is_not_read_as_progress(self):
        chunk = f"{ESC}]9;9;C:{chr(92)}work{BEL}"
        events, _ = parse_agent_events(chunk)
        self.assertEqual(events, [])

    def test_the_progress_sequence_is_not_read_as_a_directory(self):
        events, _ = parse_cwd_events(progress_sequence("1;40"))
        self.assertEqual(events, [])

    def test_one_chunk_carrying_both_is_read_correctly_by_each(self):
        chunk = (
            title_sequence("Claude Code")
            + f"{ESC}]9;9;/srv/app{BEL}"
            + progress_sequence("3")
        )
        agent_events, agent_residue = parse_agent_events(chunk)
        cwd_events, cwd_residue = parse_cwd_events(chunk)
        self.assertEqual(
            agent_events,
            [(AGENT_EVENT_TITLE, "Claude Code"), (AGENT_EVENT_PROGRESS, "3")],
        )
        self.assertEqual(cwd_events, [(CWD_EVENT_DIRECTORY, "/srv/app")])
        self.assertEqual(agent_residue, "")
        self.assertEqual(cwd_residue, "")


class AgentActivityStateTestCase(unittest.TestCase):
    def test_a_pane_that_has_produced_nothing_is_unknown_and_not_idle(self):
        reading = describe_agent_activity(blank_agent_activity(), 100.0)
        self.assertEqual(reading["state"], ACTIVITY_UNKNOWN)
        self.assertEqual(reading["state_source"], ACTIVITY_SOURCE_NONE)
        self.assertIsNone(reading["idle_seconds"])

    def test_recent_output_reads_as_working(self):
        record = note_agent_output(None, 100.0)
        reading = describe_agent_activity(record, 101.0, working_window=3.0)
        self.assertEqual(reading["state"], ACTIVITY_WORKING)
        self.assertEqual(reading["state_source"], ACTIVITY_SOURCE_OUTPUT)

    def test_output_that_stopped_reads_as_idle_with_its_age(self):
        record = note_agent_output(None, 100.0)
        reading = describe_agent_activity(record, 160.0, working_window=3.0)
        self.assertEqual(reading["state"], ACTIVITY_IDLE)
        self.assertEqual(reading["state_source"], ACTIVITY_SOURCE_OUTPUT)
        self.assertEqual(reading["idle_seconds"], 60.0)

    def test_a_published_progress_state_outranks_a_silent_stream(self):
        record = apply_agent_events(
            note_agent_output(None, 10.0), [(AGENT_EVENT_PROGRESS, "3")], 10.0
        )
        reading = describe_agent_activity(record, 40.0, working_window=3.0)
        self.assertEqual(reading["state"], ACTIVITY_WORKING)
        self.assertEqual(reading["state_source"], ACTIVITY_SOURCE_PROGRESS)

    def test_a_stale_progress_state_stops_meaning_now(self):
        # A crashed agent never sends the 0 that clears its own progress.
        record = apply_agent_events(
            note_agent_output(None, 10.0), [(AGENT_EVENT_PROGRESS, "3")], 10.0
        )
        reading = describe_agent_activity(
            record, 400.0, working_window=3.0, progress_stale_after=120.0
        )
        self.assertEqual(reading["state"], ACTIVITY_IDLE)
        self.assertEqual(reading["state_source"], ACTIVITY_SOURCE_OUTPUT)
        # Still reported: it is what the pane last said.
        self.assertEqual(reading["progress_state"], PROGRESS_STATE_INDETERMINATE)

    def test_a_percentage_is_only_published_while_the_state_carries_one(self):
        record = apply_agent_events(None, [(AGENT_EVENT_PROGRESS, "1;55")], 10.0)
        self.assertEqual(describe_agent_activity(record, 11.0)["progress_value"], 55)
        record = apply_agent_events(record, [(AGENT_EVENT_PROGRESS, "3")], 12.0)
        self.assertEqual(describe_agent_activity(record, 13.0)["progress_value"], 0)

    def test_a_cleared_progress_state_hands_liveness_back_to_the_stream(self):
        record = apply_agent_events(
            note_agent_output(None, 10.0), [(AGENT_EVENT_PROGRESS, "1;80")], 10.0
        )
        record = apply_agent_events(record, [(AGENT_EVENT_PROGRESS, "0")], 11.0)
        reading = describe_agent_activity(record, 60.0, working_window=3.0)
        self.assertEqual(reading["state"], ACTIVITY_IDLE)
        self.assertEqual(reading["progress_state"], PROGRESS_STATE_NONE)

    def test_the_announced_title_travels_with_the_reading(self):
        record = apply_agent_events(
            note_agent_output(None, 5.0), [(AGENT_EVENT_TITLE, "codex: refactoring")], 5.0
        )
        self.assertEqual(
            describe_agent_activity(record, 5.5)["title"], "codex: refactoring"
        )

    def test_updates_replace_the_record_rather_than_editing_it(self):
        # The pump thread writes without a lock and the dashboard reads under
        # one, so a reader must never meet a half-updated record.
        first = note_agent_output(None, 1.0)
        second = note_agent_output(first, 2.0)
        third = apply_agent_events(second, [(AGENT_EVENT_TITLE, "x")], 3.0)
        self.assertIsNot(first, second)
        self.assertIsNot(second, third)
        self.assertEqual(first["last_output_at"], 1.0)
        self.assertEqual(second["title"], "")


if __name__ == "__main__":
    unittest.main()
