"""Handing a result back: the store behind ``report_result`` and ``wait_for_results``.

Pinned here against the store itself, with no Flask and no HTTP:

- **A report reaches the agent that asked, and only it.** An assignment is
  recorded when a handoff is bound; a report settles it; the requester's wait
  returns it whole once, then marks it ``already_returned``.
- **Several agents at once.** ``until="all"`` waits for every one,
  ``until="any"`` returns on the first, and a wait that runs out says so
  without losing a report.
- **Nobody waits for a report that cannot come.** A handoff that goes before
  its report -- its pane closed, relaunched, re-tasked, or its agent could not
  be handed the task -- ends its assignment with the reason.
- **A report is held to the rules a task is**, refused rather than repaired or
  truncated, and a refusal records nothing.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web.agent_handoffs import (  # noqa: E402
    HANDOFF_OPENING_PROMPT,
    INLINE,
    HandoffStore,
)
from web.agent_results import (  # noqa: E402
    ENDED,
    MAX_RESULT_CHARS,
    NOBODY_WAITING_MESSAGE,
    REPORTED,
    WORKING,
    ResultError,
    ResultStore,
    clamp_wait,
    validate_until,
)

REQUESTER = "caller"
WORKERS = ("worker-a", "worker-b", "worker-c")


def _store_with(*workers, requester=REQUESTER):
    store = ResultStore()
    for index, worker in enumerate(workers):
        store.expect(
            f"h-{worker}",
            requester_session_id=requester,
            worker_session_id=worker,
            now=float(index),
        )
    return store


def _rows(payload):
    return {row["session_id"]: row for row in payload["agents"]}


class ReportAndCollectTestCase(unittest.TestCase):
    def test_a_report_is_returned_whole_once_and_then_bookmarked(self):
        store = _store_with("worker-a")

        answer = store.report("worker-a", "Found two bugs in sync.py.", "done")
        first = store.collect(REQUESTER)
        second = store.collect(REQUESTER)

        self.assertEqual(answer["revision"], 1)
        self.assertEqual(answer["requester_session_id"], REQUESTER)
        self.assertFalse(answer["replaced_earlier_report"])
        row = _rows(first)["worker-a"]
        self.assertEqual((row["state"], row["status"]), (REPORTED, "done"))
        self.assertEqual(row["result"], "Found two bugs in sync.py.")
        self.assertTrue(first["complete"])
        self.assertIn("not the person's own words", first["note"])
        again = _rows(second)["worker-a"]
        self.assertNotIn("result", again)
        self.assertTrue(again["already_returned"])
        self.assertNotIn("note", second)

    def test_include_collected_returns_a_report_again(self):
        store = _store_with("worker-a")
        store.report("worker-a", "Done.")
        store.collect(REQUESTER)

        payload = store.collect(REQUESTER, include_collected=True)

        self.assertEqual(_rows(payload)["worker-a"]["result"], "Done.")

    def test_a_second_report_replaces_the_first_and_is_new_again(self):
        store = _store_with("worker-a")
        store.report("worker-a", "First pass.")
        store.collect(REQUESTER)

        answer = store.report("worker-a", "Final answer.", "failed")
        row = _rows(store.collect(REQUESTER))["worker-a"]

        self.assertTrue(answer["replaced_earlier_report"])
        self.assertEqual((row["result"], row["status"], row["revision"]), ("Final answer.", "failed", 2))

    def test_a_pane_nobody_handed_a_task_has_nobody_to_report_to(self):
        store = _store_with("worker-a")

        with self.assertRaises(ResultError) as refused:
            store.report("stranger", "Hello.")

        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(refused.exception.message, NOBODY_WAITING_MESSAGE)
        self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["state"], WORKING)

    def test_another_requesters_reports_are_never_returned(self):
        store = _store_with("worker-a")
        store.expect("h-x", requester_session_id="someone-else", worker_session_id="worker-x")
        store.report("worker-x", "Not for you.")

        payload = store.collect(REQUESTER)

        self.assertEqual(list(_rows(payload)), ["worker-a"])
        self.assertNotIn("Not for you.", str(payload))

    def test_a_refused_report_records_nothing(self):
        store = _store_with("worker-a")
        cases = {
            "not text": (["x"], None, "must be text"),
            "empty": ("   ", None, "is empty"),
            "control": ("a\x1bb", None, "U+001B ESC"),
            "carriage return": ("a\rb", None, "carriage return"),
            "too long": ("a" * (MAX_RESULT_CHARS + 1), None, "names the file"),
            "status": ("fine", "great", "'status' must be one of"),
        }
        for label, (text, status, expected) in cases.items():
            with self.subTest(label):
                with self.assertRaises(ResultError) as refused:
                    store.report("worker-a", text, status)
                self.assertIn(expected, refused.exception.message)
                self.assertEqual(refused.exception.status_code, 400)
                self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["state"], WORKING)

    def test_crlf_is_read_as_lf_and_nothing_else_changes(self):
        store = _store_with("worker-a")
        store.report("worker-a", "line one\r\n\tline two")

        self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["result"], "line one\n\tline two")

    def test_an_unknown_named_pane_is_listed_rather_than_ignored(self):
        store = _store_with("worker-a")

        payload = store.collect(REQUESTER, ["worker-a", "nobody"])

        self.assertEqual(payload["unknown"], ["nobody"])
        self.assertEqual(list(_rows(payload)), ["worker-a"])

    def test_nothing_handed_out_says_so(self):
        payload = ResultStore().collect(REQUESTER)

        self.assertEqual(payload["agents"], [])
        self.assertFalse(payload["complete"])
        self.assertIn("nothing to wait for", payload["message"])

    def test_reports_past_the_budget_wait_for_the_next_call(self):
        store = _store_with(*WORKERS)
        for worker in WORKERS:
            store.report(worker, worker[-1] * 30)

        first = store.collect(REQUESTER, chars_budget=50)
        second = store.collect(REQUESTER, chars_budget=50)

        self.assertEqual([("result" in row) for row in first["agents"]], [True, False, False])
        self.assertTrue(first["agents"][1]["result_withheld"])
        self.assertIn("did not fit", first["instructions"])
        self.assertEqual([("result" in row) for row in second["agents"]], [False, True, False])
        # The first report always fits, however long, or nothing would move.
        long_store = _store_with("worker-a")
        long_store.report("worker-a", "z" * 200)
        self.assertEqual(len(_rows(long_store.collect(REQUESTER, chars_budget=10))["worker-a"]["result"]), 200)

    def test_include_collected_never_starves_an_unseen_report(self):
        """Re-sent reports used to spend the budget first, oldest first, so a
        report the caller had never seen was withheld on every such call."""
        workers = ("w1", "w2", "w3", "w4")
        store = _store_with(*workers)
        for worker in workers[:3]:
            store.report(worker, worker * 10)
        store.collect(REQUESTER)
        store.report("w4", "new " * 10)

        payload = store.collect(REQUESTER, include_collected=True, chars_budget=50)

        rows = _rows(payload)
        self.assertEqual(rows["w4"]["result"], "new " * 10)
        # What did not fit had already been returned once, so nothing is owed.
        self.assertTrue(all(rows[worker].get("already_returned") or "result" in rows[worker]
                            for worker in workers[:3]))
        self.assertNotIn("instructions", payload)

    def test_rows_come_oldest_handed_first(self):
        store = _store_with("worker-c", "worker-a", "worker-b")

        self.assertEqual(
            [row["session_id"] for row in store.collect(REQUESTER)["agents"]],
            ["worker-c", "worker-a", "worker-b"],
        )


class EndingTestCase(unittest.TestCase):
    def test_an_ending_settles_a_pending_assignment_with_its_reason(self):
        store = _store_with("worker-a")

        self.assertTrue(store.end("h-worker-a", "connection closed"))
        row = _rows(store.collect(REQUESTER))["worker-a"]

        self.assertEqual(row["state"], ENDED)
        self.assertIn("connection closed before it reported", row["reason"])

    def test_an_ending_never_undoes_a_report(self):
        store = _store_with("worker-a")
        store.report("worker-a", "Done.")

        self.assertFalse(store.end("h-worker-a", "connection closed"))
        self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["result"], "Done.")

    def test_a_report_takes_no_further_writes_once_its_handoff_goes(self):
        """A pane relaunched after it reported runs an agent that was never
        handed the task; it used to be able to overwrite the report."""
        store = _store_with("worker-a")
        store.report("worker-a", "The original findings.")

        store.end("h-worker-a", "pane relaunched")
        with self.assertRaises(ResultError) as refused:
            store.report("worker-a", "Something else entirely.")

        self.assertEqual(refused.exception.status_code, 409)
        row = _rows(store.collect(REQUESTER))["worker-a"]
        self.assertEqual((row["result"], row["revision"]), ("The original findings.", 1))

    def test_a_closed_workers_report_takes_no_further_writes(self):
        store = _store_with("worker-a")
        store.report("worker-a", "Done.")

        store.forget_session("worker-a")

        with self.assertRaises(ResultError):
            store.report("worker-a", "Again.")

    def test_an_ended_assignment_takes_no_report(self):
        store = _store_with("worker-a")
        store.end("h-worker-a", "pane relaunched")

        with self.assertRaises(ResultError):
            store.report("worker-a", "Too late.")

    def test_the_requesters_pane_closing_drops_what_it_was_owed(self):
        store = _store_with("worker-a", "worker-b")
        store.report("worker-a", "Done.")

        self.assertEqual(store.forget_session(REQUESTER), 2)
        self.assertEqual(store.count(), 0)
        with self.assertRaises(ResultError):
            store.report("worker-b", "Nobody is listening.")

    def test_a_workers_pane_closing_ends_it_and_keeps_a_report(self):
        store = _store_with("worker-a", "worker-b")
        store.report("worker-a", "Done.")

        store.forget_session("worker-a")
        store.forget_session("worker-b")
        rows = _rows(store.collect(REQUESTER))

        self.assertEqual(rows["worker-a"]["result"], "Done.")
        self.assertEqual(rows["worker-b"]["state"], ENDED)
        self.assertIn("closed", rows["worker-b"]["reason"])

    def test_the_ceiling_evicts_a_settled_assignment_before_a_pending_one(self):
        store = ResultStore(max_assignments=2)
        store.expect("h-1", requester_session_id=REQUESTER, worker_session_id="w1", now=1.0)
        store.expect("h-2", requester_session_id=REQUESTER, worker_session_id="w2", now=2.0)
        store.end("h-2", "pane closed")

        store.expect("h-3", requester_session_id=REQUESTER, worker_session_id="w3", now=3.0)

        self.assertEqual(store.state_for("h-1"), WORKING)
        self.assertIsNone(store.state_for("h-2"))
        self.assertEqual(store.state_for("h-3"), WORKING)

    def test_a_handoff_with_no_requester_is_not_tracked(self):
        store = ResultStore()

        self.assertFalse(store.expect("h", requester_session_id="", worker_session_id="w"))
        self.assertEqual(store.count(), 0)


class WaitTestCase(unittest.TestCase):
    def _report_later(self, store, worker, text, delay=0.05):
        timer = threading.Timer(delay, store.report, args=(worker, text))
        timer.start()
        self.addCleanup(timer.cancel)
        return timer

    def test_all_waits_for_every_agent(self):
        store = _store_with(*WORKERS)
        for delay, worker in zip((0.02, 0.05, 0.08), WORKERS):
            self._report_later(store, worker, f"{worker} done", delay)

        started = time.monotonic()
        settled = store.wait_until_settled(REQUESTER, until="all", timeout=5)
        payload = store.collect(REQUESTER)

        self.assertTrue(settled)
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(payload["complete"])
        self.assertEqual(
            [row["result"] for row in payload["agents"]],
            [f"{worker} done" for worker in WORKERS],
        )

    def test_any_returns_on_the_first_report(self):
        store = _store_with(*WORKERS)
        self._report_later(store, "worker-b", "b first")

        settled = store.wait_until_settled(REQUESTER, until="any", timeout=5)
        payload = store.collect(REQUESTER)

        self.assertTrue(settled)
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["counts"], {WORKING: 2, REPORTED: 1, ENDED: 0})
        self.assertIn("still working", payload["instructions"])
        # A report already returned is not news: the next `any` waits again.
        self.assertFalse(store.wait_until_settled(REQUESTER, until="any", timeout=0.05))

    def test_an_ending_wakes_a_waiter_too(self):
        store = _store_with("worker-a")
        timer = threading.Timer(0.05, store.end, args=("h-worker-a", "pane closed"))
        timer.start()
        self.addCleanup(timer.cancel)

        self.assertTrue(store.wait_until_settled(REQUESTER, timeout=5))
        self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["state"], ENDED)

    def test_a_wait_that_runs_out_says_so_and_keeps_what_arrived(self):
        store = _store_with("worker-a", "worker-b")
        store.report("worker-a", "a done")

        settled = store.wait_until_settled(REQUESTER, until="all", timeout=0.05)
        payload = store.collect(REQUESTER)

        self.assertFalse(settled)
        self.assertFalse(payload["complete"])
        self.assertEqual(_rows(payload)["worker-a"]["result"], "a done")

    def test_a_wait_narrowed_to_named_panes_ignores_the_rest(self):
        store = _store_with("worker-a", "worker-b")
        store.report("worker-a", "a done")

        self.assertTrue(store.wait_until_settled(REQUESTER, ["worker-a"], timeout=0.05))
        self.assertEqual(list(_rows(store.collect(REQUESTER, ["worker-a"]))), ["worker-a"])

    def test_nothing_to_wait_for_answers_at_once(self):
        started = time.monotonic()

        self.assertTrue(ResultStore().wait_until_settled(REQUESTER, timeout=5))
        self.assertLess(time.monotonic() - started, 1)

    def test_wait_arguments_are_validated_and_clamped(self):
        self.assertEqual(clamp_wait(None), 45.0)
        self.assertEqual(clamp_wait("0"), 0.0)
        self.assertEqual(clamp_wait(600), 55.0)
        for bad in ("soon", -1, True, float("nan")):
            with self.subTest(bad=bad), self.assertRaises(ResultError):
                clamp_wait(bad)
        self.assertEqual(validate_until(None), "all")
        with self.assertRaises(ResultError):
            validate_until("most")


class HandoffStoreWiringTestCase(unittest.TestCase):
    """The handoff store is what records and ends assignments."""

    def setUp(self):
        self.results = ResultStore()
        self.handoffs = HandoffStore(results=self.results)

    def test_a_handoff_bound_at_birth_is_expected_from_its_pane(self):
        handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")

        self.assertEqual(self.results.state_for(handoff_id), WORKING)
        self.results.report("worker-a", "Reviewed.")
        self.assertEqual(_rows(self.results.collect(REQUESTER))["worker-a"]["result"], "Reviewed.")

    def test_a_split_reports_to_the_agent_that_asked_not_the_pane_it_halved(self):
        handoff_id = self.handoffs.create(
            "Review.", source_session_id="neighbour", requester_session_id=REQUESTER
        )
        self.assertIsNone(self.results.state_for(handoff_id))
        self.handoffs.take(handoff_id, "neighbour")
        self.handoffs.bind(handoff_id, "worker-a")

        self.results.report("worker-a", "Reviewed.")

        self.assertEqual(list(_rows(self.results.collect(REQUESTER))), ["worker-a"])
        self.assertEqual(self.results.collect("neighbour")["agents"], [])

    def test_every_way_a_handoff_goes_ends_its_assignment(self):
        cases = {
            "connection closed": lambda h, pane: self.handoffs.drop(h, "connection closed"),
            "relaunched": lambda h, pane: self.handoffs.drop_bound(pane, "pane relaunched"),
            "closed": lambda h, pane: self.handoffs.forget_session(pane),
            "replaced": lambda h, pane: self.handoffs.create("Other.", source_session_id=REQUESTER, session_id=pane),
            "undeliverable": lambda h, pane: self.handoffs.mark_undeliverable(h, "no tools here"),
        }
        for label, go in cases.items():
            with self.subTest(label):
                pane = f"worker-{label}"
                handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id=pane)
                go(handoff_id, pane)
                self.assertEqual(self.results.state_for(handoff_id), ENDED)

        undeliverable = next(
            row for row in self.results.collect(REQUESTER)["agents"]
            if row["session_id"] == "worker-undeliverable"
        )
        self.assertIn("no tools here", undeliverable["reason"])

    def test_the_assignment_exists_before_the_bind_releases_its_lock(self):
        """Recorded after the lock was released, a drop in that gap ended
        nothing and left an assignment working forever."""
        seen = []
        original = self.results.expect

        def expect_and_check(*args, **kwargs):
            # The handoff store's lock is held: a drop cannot run until the
            # assignment exists.
            seen.append(self.handoffs._lock.locked())
            return original(*args, **kwargs)

        self.results.expect = expect_and_check
        handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")
        self.handoffs.drop(handoff_id, "pane closed")

        self.assertEqual(seen, [True])
        self.assertEqual(self.results.state_for(handoff_id), ENDED)

    def test_a_drop_racing_a_bind_never_leaves_an_assignment_working(self):
        created = []
        dropper = threading.Thread(
            target=lambda: [self.handoffs.drop_bound("worker-a", "pane closed") for _ in range(2000)]
        )
        dropper.start()
        for _ in range(200):
            created.append(
                self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")
            )
        dropper.join()
        self.handoffs.drop_bound("worker-a", "pane closed")

        self.assertEqual(
            {self.results.state_for(handoff_id) for handoff_id in created} - {None},
            {ENDED},
        )

    def test_a_replaced_task_takes_the_next_report(self):
        self.handoffs.create("First.", source_session_id=REQUESTER, session_id="worker-a", now=1.0)
        second = self.handoffs.create("Second.", source_session_id=REQUESTER, session_id="worker-a", now=2.0)

        self.results.report("worker-a", "Did the second.")

        self.assertEqual(self.results.state_for(second), REPORTED)

    def test_the_brief_tells_its_reader_how_to_report_back(self):
        handoff_id = self.handoffs.create(
            "Review.", source_session_id=REQUESTER, session_id="worker-a", from_title="Claude 1"
        )
        self.handoffs.announce(handoff_id, delivery=INLINE)

        payload = self.handoffs.read("worker-a")

        self.assertIn("report_result", payload["reply"])
        self.assertIn("pane Claude 1", payload["reply"])
        self.assertIn(f"{MAX_RESULT_CHARS:,}", payload["reply"])

    def test_the_launch_line_sentence_names_the_report_tool(self):
        self.assertIn("report_result", HANDOFF_OPENING_PROMPT)
        self.assertRegex(HANDOFF_OPENING_PROMPT, r"^[A-Za-z0-9 .,_]+$")

    def test_a_store_without_results_still_hands_tasks_over(self):
        handoffs = HandoffStore()
        handoff_id = handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")

        self.assertTrue(handoffs.drop(handoff_id, "connection closed"))


if __name__ == "__main__":
    unittest.main()
