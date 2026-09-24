"""The handoff store: a task's life from its split to its agent's first read.

`web/agent_handoffs.py` has no Flask and no I/O, so every rule is driven here
directly, with the clock injected:

- **A task is refused, never repaired.** A control character is named rather
  than stripped, a lone carriage return is one, and a task past the ceiling is
  refused with the ceiling in the sentence rather than truncated.
- **A handle is spent once, and only by its own pane.** A replayed, expired or
  foreign handle is refused, and a foreign attempt does not burn it.
- **A pane holds one handoff, announced once.** Binding a second drops the
  first and its file; a read never changes the brief, only its state.
- **Size decides the delivery**, and a paged read hands over every character.
- **The log names ids, sizes and deliveries -- never the text, never a path.**
"""

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import agent_handoffs  # noqa: E402
from web.agent_handoffs import (  # noqa: E402
    ANNOUNCED,
    FILE,
    HANDOFF_OPENING_PROMPT,
    HANDOFF_UNBOUND_TTL_SECONDS,
    HEAD_CHARS,
    INLINE,
    INLINE_TASK_MAX_CHARS,
    MAX_TASK_BYTES,
    PAGED,
    READ,
    UNDELIVERABLE,
    WAITING,
    HandoffError,
    HandoffStore,
    planned_delivery,
    same_machine,
    validate_task,
)
from web.window_intents import CLAIM_TTL_SECONDS, INTENT_TTL_SECONDS  # noqa: E402

#: Text no log line may ever contain.
SECRET_BRIEF = "Rotate the staging key kept in vault/prod-7 and tell nobody."


class TaskValidationTestCase(unittest.TestCase):
    def test_a_plain_task_is_returned_exactly_as_written(self):
        text = "Review the diff.\n\tThen fix the two bugs.  Keep spacing."

        self.assertEqual(validate_task(text), text)

    def test_crlf_becomes_lf_and_nothing_else_changes(self):
        self.assertEqual(validate_task("one\r\ntwo\r\n"), "one\ntwo\n")

    def test_a_lone_carriage_return_is_refused_by_name(self):
        with self.assertRaises(HandoffError) as raised:
            validate_task("progress\rbar")

        self.assertIn("U+000D", raised.exception.message)
        self.assertIn("at character 8", raised.exception.message)

    def test_every_other_control_character_is_refused_and_never_stripped(self):
        for character, label in (
            ("\x1b", "U+001B ESC"),
            ("\x00", "U+0000"),
            ("\x07", "U+0007"),
            ("\x7f", "U+007F"),
            ("\x85", "U+0085"),
        ):
            with self.subTest(character=repr(character)):
                with self.assertRaises(HandoffError) as raised:
                    validate_task(f"before{character}after")
                self.assertIn(label, raised.exception.message)
                self.assertIn("removes nothing", raised.exception.message)
                self.assertEqual(raised.exception.status_code, 400)

    def test_an_unpaired_surrogate_is_refused_rather_than_failing_later(self):
        # JSON can carry "\ud800"; it could never be written to a file.
        with self.assertRaises(HandoffError) as raised:
            validate_task("x\ud800y")

        self.assertIn("surrogate", raised.exception.message)

    def test_an_empty_or_blank_task_is_refused(self):
        for value in ("", "   ", "\n\t\n"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(HandoffError):
                    validate_task(value)

    def test_a_task_that_is_not_text_is_refused(self):
        for value in (42, ["a"], {"task": "x"}, True):
            with self.subTest(value=value):
                with self.assertRaises(HandoffError) as raised:
                    validate_task(value)
                self.assertIn("must be text", raised.exception.message)

    def test_the_ceiling_is_bytes_and_is_inclusive(self):
        self.assertEqual(len(validate_task("a" * MAX_TASK_BYTES)), MAX_TASK_BYTES)
        with self.assertRaises(HandoffError) as raised:
            validate_task("a" * (MAX_TASK_BYTES + 1))

        message = raised.exception.message
        self.assertIn(f"{MAX_TASK_BYTES:,}", message)
        self.assertIn("Nothing was truncated", message)

    def test_multibyte_text_is_measured_in_bytes_not_characters(self):
        # Three bytes a character: under the character count, over the bytes.
        text = "€" * (MAX_TASK_BYTES // 3 + 1)

        self.assertLess(len(text), MAX_TASK_BYTES)
        with self.assertRaises(HandoffError):
            validate_task(text)

    def test_unicode_text_that_fits_is_accepted_untouched(self):
        text = "Fix the élève table — then \U0001F680 ship it."

        self.assertEqual(validate_task(text), text)


class OpeningPromptConstantTestCase(unittest.TestCase):
    def test_the_sentence_holds_nothing_any_shell_expands(self):
        """Double quotes must read identically in cmd, PowerShell and POSIX."""
        self.assertRegex(HANDOFF_OPENING_PROMPT, r"^[A-Za-z0-9 .,_]+$")
        for forbidden in ('"', "'", "%", "!", "^", "&", "$", "`", "\\", "\n"):
            self.assertNotIn(forbidden, HANDOFF_OPENING_PROMPT)

    def test_it_names_the_tool_and_starts_with_no_subcommand(self):
        self.assertIn("read_handoff", HANDOFF_OPENING_PROMPT)
        first_word = HANDOFF_OPENING_PROMPT.split()[0].lower()
        # Words the three CLIs take as subcommands in first position.
        self.assertNotIn(
            first_word,
            {"exec", "resume", "fork", "mcp", "login", "logout", "apply", "config", "help", "update"},
        )


class DeliveryPlanTestCase(unittest.TestCase):
    def test_the_inline_limit_is_inclusive(self):
        self.assertEqual(planned_delivery(INLINE_TASK_MAX_CHARS), INLINE)
        self.assertEqual(planned_delivery(INLINE_TASK_MAX_CHARS + 1), FILE)

    def test_the_unbound_ttl_outlasts_a_splits_worst_case(self):
        """A page claiming at 14.9 s may report at 34.9 s; its handle must live."""
        self.assertGreater(
            HANDOFF_UNBOUND_TTL_SECONDS, INTENT_TTL_SECONDS + CLAIM_TTL_SECONDS
        )


class SameMachineTestCase(unittest.TestCase):
    @staticmethod
    def _pane(mode, host="", username="", port=22):
        return SimpleNamespace(mode=mode, host=host, username=username, port=port)

    def test_two_local_panes_are_the_same_machine_whatever_their_shell(self):
        self.assertTrue(same_machine(self._pane("wsl", "cmd"), self._pane("wsl", "WSL: Ubuntu")))

    def test_ssh_panes_match_on_host_user_and_port(self):
        base = self._pane("ssh", "Example.com", "ubuntu", 22)

        self.assertTrue(same_machine(base, self._pane("ssh", "example.com", "ubuntu", 22)))
        self.assertFalse(same_machine(base, self._pane("ssh", "example.com", "root", 22)))
        self.assertFalse(same_machine(base, self._pane("ssh", "example.com", "ubuntu", 2222)))
        self.assertFalse(same_machine(base, self._pane("ssh", "other.example.com", "ubuntu", 22)))

    def test_local_and_remote_are_never_the_same_machine(self):
        self.assertFalse(same_machine(self._pane("wsl"), self._pane("ssh", "localhost")))

    def test_a_missing_or_partial_pane_is_never_the_same_machine(self):
        self.assertFalse(same_machine(None, self._pane("wsl")))
        self.assertFalse(same_machine(self._pane("wsl"), None))
        self.assertFalse(same_machine(SimpleNamespace(), SimpleNamespace()))


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = HandoffStore()
        self.now = 1000.0

    def _unbound(self, text="Fix the bug.", source="pane-a"):
        return self.store.create(
            text, source_session_id=source, from_title="Terminal 1",
            from_agent="claude", now=self.now,
        )

    def _bound(self, text="Fix the bug.", session="pane-b", source="pane-a"):
        return self.store.create(
            text, source_session_id=source, session_id=session,
            from_title="Terminal 1", from_agent="claude", now=self.now,
        )


class TakeOnceTestCase(StoreTestCase):
    def test_a_handle_is_taken_once_by_its_own_source(self):
        handoff_id = self._unbound()

        self.store.take(handoff_id, "pane-a", now=self.now + 1)
        with self.assertRaises(HandoffError) as raised:
            self.store.take(handoff_id, "pane-a", now=self.now + 2)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("No pane was added", raised.exception.message)

    def test_a_foreign_source_is_refused_and_does_not_burn_the_handle(self):
        handoff_id = self._unbound()

        with self.assertRaises(HandoffError) as raised:
            self.store.take(handoff_id, "pane-z", now=self.now + 1)
        self.assertIn("different pane", raised.exception.message)

        # Still there for the split it was recorded for.
        self.store.take(handoff_id, "pane-a", now=self.now + 2)

    def test_an_unknown_handle_is_refused(self):
        with self.assertRaises(HandoffError):
            self.store.take("0" * 32, "pane-a", now=self.now)

    def test_an_expired_handle_is_refused_and_gone(self):
        handoff_id = self._unbound()

        with self.assertRaises(HandoffError):
            self.store.take(
                handoff_id, "pane-a", now=self.now + HANDOFF_UNBOUND_TTL_SECONDS
            )
        self.assertEqual(self.store.count(), 0)

    def test_a_handle_just_inside_its_ttl_is_still_taken(self):
        handoff_id = self._unbound()

        self.store.take(
            handoff_id, "pane-a", now=self.now + HANDOFF_UNBOUND_TTL_SECONDS - 0.01
        )

    def test_an_unbound_handoff_is_not_a_pane_fact(self):
        self._unbound()

        self.assertIsNone(self.store.public_state("pane-a"))

    def test_bind_requires_a_taken_handle(self):
        handoff_id = self._unbound()

        with self.assertRaises(HandoffError):
            self.store.bind(handoff_id, "pane-b")
        self.store.take(handoff_id, "pane-a", now=self.now)
        self.store.bind(handoff_id, "pane-b")

        self.assertEqual(self.store.public_state("pane-b")["state"], WAITING)

    def test_a_bound_handoff_never_expires(self):
        self._bound()

        self.store.create("other", source_session_id="x", now=self.now + 10_000)

        self.assertIsNotNone(self.store.pending_for("pane-b"))


class LifecycleTestCase(StoreTestCase):
    def test_waiting_then_announced_then_read(self):
        self._bound()
        pending = self.store.pending_for("pane-b")

        self.assertEqual(self.store.public_state("pane-b"), {
            "state": WAITING, "delivery": None, "from_session_id": "pane-a", "chars": 12,
        })
        self.assertTrue(self.store.announce(pending.handoff_id, delivery=INLINE))
        self.assertEqual(self.store.public_state("pane-b")["state"], ANNOUNCED)
        self.assertIsNone(self.store.pending_for("pane-b"))

        first = self.store.read("pane-b")
        second = self.store.read("pane-b")

        self.assertEqual(first["task"], "Fix the bug.")
        self.assertEqual(first, second)
        self.assertEqual(first["delivery"], INLINE)
        self.assertEqual(first["from"], {"session_id": "pane-a", "title": "Terminal 1", "agent": "claude"})
        self.assertIn("not the person's own words", first["note"])
        self.assertIn("override", first["note"])
        self.assertNotIn("handoff_id", first)
        self.assertEqual(self.store.public_state("pane-b")["state"], READ)

    def test_announce_happens_once(self):
        handoff_id = self._bound()

        self.assertTrue(self.store.announce(handoff_id, delivery=INLINE))
        self.assertFalse(self.store.announce(handoff_id, delivery=INLINE))

    def test_announcing_a_dropped_handoff_removes_the_file_written_for_it(self):
        handoff_id = self._bound()
        removed = []
        self.store.drop(handoff_id, "pane closed")

        announced = self.store.announce(
            handoff_id, delivery=FILE, task_file="/tmp/x", cleanup=lambda: removed.append(1)
        )

        self.assertFalse(announced)
        self.assertEqual(removed, [1])

    def test_an_unknown_delivery_is_a_programming_error(self):
        handoff_id = self._bound()

        with self.assertRaises(ValueError):
            self.store.announce(handoff_id, delivery="carrier-pigeon")

    def test_nothing_bound_reads_as_no_task_rather_than_an_error(self):
        result = self.store.read("pane-q")

        self.assertIsNone(result["handoff"])
        self.assertEqual(result["message"], agent_handoffs.NO_TASK_MESSAGE)

    def test_a_waiting_handoff_is_not_readable_yet(self):
        self._bound()

        result = self.store.read("pane-b")

        self.assertIsNone(result["handoff"])
        self.assertEqual(result["state"], WAITING)
        self.assertNotIn("task", result)
        # And reading did not change it.
        self.assertEqual(self.store.public_state("pane-b")["state"], WAITING)

    def test_an_undeliverable_handoff_says_why_and_hands_nothing_over(self):
        handoff_id = self._bound()

        self.assertTrue(self.store.mark_undeliverable(handoff_id, "codex resumed a conversation"))
        result = self.store.read("pane-b")

        self.assertEqual(result["state"], UNDELIVERABLE)
        self.assertIn("codex resumed a conversation", result["message"])
        self.assertNotIn("task", result)
        self.assertFalse(self.store.announce(handoff_id, delivery=INLINE))

    def test_another_panes_read_never_reaches_this_handoff(self):
        handoff_id = self._bound(session="pane-b")
        self.store.announce(handoff_id, delivery=INLINE)

        self.assertIsNone(self.store.read("pane-c")["handoff"])
        self.assertEqual(self.store.public_state("pane-b")["state"], ANNOUNCED)


class DeliveryTestCase(StoreTestCase):
    def test_a_file_delivery_names_the_file_and_quotes_its_opening(self):
        text = "x" * (INLINE_TASK_MAX_CHARS + 500)
        handoff_id = self._bound(text=text)
        self.store.announce(handoff_id, delivery=FILE, task_file="/home/u/t.md")

        result = self.store.read("pane-b")

        self.assertEqual(result["task_file"], "/home/u/t.md")
        self.assertEqual(result["head"], text[:HEAD_CHARS])
        self.assertEqual(result["chars"], len(text))
        self.assertNotIn("task", result)
        self.assertIn("file tool", result["instructions"])

    def test_a_paged_delivery_hands_over_every_character_in_order(self):
        text = "".join(chr(ord("a") + index % 26) for index in range(INLINE_TASK_MAX_CHARS * 2 + 17))
        handoff_id = self._bound(text=text)
        self.store.announce(handoff_id, delivery=PAGED)

        pages = []
        offset = 0
        while offset is not None:
            page = self.store.read("pane-b", offset)
            self.assertEqual(page["offset"], offset)
            self.assertLessEqual(len(page["task"]), INLINE_TASK_MAX_CHARS)
            pages.append(page["task"])
            offset = page["next_offset"]

        self.assertEqual("".join(pages), text)
        self.assertEqual(len(pages), 3)
        self.assertIsNone(page["next_offset"])
        self.assertNotIn("instructions", page)

    def test_a_paged_offset_out_of_range_is_refused_with_the_range(self):
        handoff_id = self._bound(text="abc")
        self.store.announce(handoff_id, delivery=PAGED)

        for offset in (-1, 3, 99):
            with self.subTest(offset=offset):
                with self.assertRaises(HandoffError) as raised:
                    self.store.read("pane-b", offset)
                self.assertIn("between 0 and 2", raised.exception.message)

    def test_an_offset_that_is_not_a_whole_number_is_refused(self):
        handoff_id = self._bound()
        self.store.announce(handoff_id, delivery=PAGED)

        for offset in ("3", 1.5, True):
            with self.subTest(offset=offset):
                with self.assertRaises(HandoffError):
                    self.store.read("pane-b", offset)

    def test_an_offset_on_a_whole_delivery_is_refused_not_ignored(self):
        handoff_id = self._bound()
        self.store.announce(handoff_id, delivery=INLINE)

        with self.assertRaises(HandoffError) as raised:
            self.store.read("pane-b", 5)
        self.assertIn("only applies", raised.exception.message)
        # Offset 0 is the whole task, stated or not.
        self.assertEqual(self.store.read("pane-b", 0)["task"], "Fix the bug.")


class DroppingTestCase(StoreTestCase):
    def test_drop_removes_the_file_once_and_is_idempotent(self):
        handoff_id = self._bound()
        removed = []
        self.store.announce(handoff_id, delivery=FILE, task_file="/t", cleanup=lambda: removed.append(1))

        self.assertTrue(self.store.drop(handoff_id, "connection closed"))
        self.assertFalse(self.store.drop(handoff_id, "connection closed"))

        self.assertEqual(removed, [1])
        self.assertIsNone(self.store.public_state("pane-b"))

    def test_binding_a_new_task_drops_the_old_one_and_its_file(self):
        old_id = self._bound(text="old brief")
        removed = []
        self.store.announce(old_id, delivery=FILE, task_file="/old", cleanup=lambda: removed.append("old"))

        new_id = self._bound(text="new brief")

        self.assertEqual(removed, ["old"])
        self.assertFalse(self.store.drop(old_id))
        pending = self.store.pending_for("pane-b")
        self.assertEqual(pending.handoff_id, new_id)
        self.assertEqual(pending.text, "new brief")

    def test_forgetting_a_pane_drops_what_was_bound_to_it_and_recorded_against_it(self):
        self._bound(session="pane-b")
        unbound_for_source = self._unbound(source="pane-b")
        self._unbound(source="pane-other")

        self.assertEqual(self.store.forget_session("pane-b"), 2)

        self.assertIsNone(self.store.public_state("pane-b"))
        with self.assertRaises(HandoffError):
            self.store.take(unbound_for_source, "pane-b", now=self.now)
        self.assertEqual(self.store.count(), 1)

    def test_dropping_what_is_bound_leaves_a_split_that_can_still_happen(self):
        """A relaunch of the source pane does not cancel its pending split."""
        self._bound(session="pane-b")
        unbound_for_source = self._unbound(source="pane-b")

        self.assertEqual(self.store.drop_bound("pane-b", "pane relaunched"), 1)

        self.assertIsNone(self.store.public_state("pane-b"))
        self.store.take(unbound_for_source, "pane-b", now=self.now)

    def test_a_failing_cleanup_never_raises_out_of_a_drop(self):
        handoff_id = self._bound()

        def explode():
            raise OSError("disk gone")

        self.store.announce(handoff_id, delivery=FILE, task_file="/t", cleanup=explode)

        self.assertTrue(self.store.drop(handoff_id))


class CeilingTestCase(unittest.TestCase):
    def test_the_oldest_unbound_handoff_is_evicted_first(self):
        store = HandoffStore(max_handoffs=2)
        oldest = store.create("a", source_session_id="s", now=1.0)
        store.create("b", source_session_id="s", now=2.0)

        store.create("c", source_session_id="s", now=3.0)

        with self.assertRaises(HandoffError):
            store.take(oldest, "s", now=4.0)
        self.assertEqual(store.count(), 2)

    def test_a_store_full_of_bound_handoffs_refuses_an_unbound_one(self):
        store = HandoffStore(max_handoffs=2)
        store.create("a", source_session_id="s", session_id="p1", now=1.0)
        store.create("b", source_session_id="s", session_id="p2", now=1.0)

        with self.assertRaises(HandoffError) as raised:
            store.create("c", source_session_id="s", now=2.0)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIsNotNone(store.pending_for("p1"))

    def test_a_handoff_bound_at_birth_is_always_admitted(self):
        """One per pane, and refusing it after a relaunch closed the old shell
        would cost the pane its task for nothing."""
        store = HandoffStore(max_handoffs=1)
        store.create("a", source_session_id="s", session_id="p1", now=1.0)

        store.create("b", source_session_id="s", session_id="p2", now=1.0)

        self.assertIsNotNone(store.pending_for("p2"))


class LoggingTestCase(StoreTestCase):
    def test_no_log_line_carries_the_text_or_the_file_path(self):
        path = "/home/ubuntu/.gridvibe/handoffs/deadbeef.md"
        with self.assertLogs(agent_handoffs.logger, level="DEBUG") as logs:
            unbound = self._unbound(text=SECRET_BRIEF)
            self.store.take(unbound, "pane-a", now=self.now)
            self.store.bind(unbound, "pane-b")
            self.store.announce(unbound, delivery=FILE, task_file=path, cleanup=lambda: None)
            self.store.read("pane-b")
            self.store.drop(unbound, "connection closed")
            other = self._bound(text=SECRET_BRIEF, session="pane-c")
            self.store.mark_undeliverable(other, "no tools")
            self.store.create(SECRET_BRIEF, source_session_id="s", now=self.now)
            self.store.create("x", source_session_id="s", now=self.now + HANDOFF_UNBOUND_TTL_SECONDS + 1)

        joined = "\n".join(logs.output)
        for fragment in (SECRET_BRIEF, "vault/prod-7", path, "deadbeef.md"):
            self.assertNotIn(fragment, joined)
        # What the log *is* for: ids, a size, a delivery, a reason.
        for word in ("created", "taken", "bound", "announced", "read", "dropped", "undeliverable", "expired"):
            self.assertIn(word, joined)
        self.assertTrue(re.search(r"chars=%d" % len(SECRET_BRIEF), joined))
        self.assertIn("delivery=file", joined)


if __name__ == "__main__":
    unittest.main()
