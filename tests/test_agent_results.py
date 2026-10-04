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
- **Only the agent that read the task answers it.** A pane relaunched with a
  new task keeps its id, so the id cannot tell the replaced agent's late
  report from its successor's. The read mints a receipt, and a report settles
  its assignment only with that receipt: one sent before the task was read,
  without a receipt, or with the replaced agent's, is refused.
"""

import json
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
    LINK_FIELDS,
    MAX_RESULT_CHARS,
    MAX_WAIT_SECONDS,
    NOBODY_WAITING_MESSAGE,
    REPORTED,
    STALE_RECEIPT_MESSAGE,
    UNREAD_TASK_MESSAGE,
    WAIT_GRACE_SECONDS,
    WORKING,
    ResultError,
    ResultStore,
    clamp_wait,
    validate_until,
)

REQUESTER = "caller"
WORKERS = ("worker-a", "worker-b", "worker-c")


def _store_with(*workers, requester=REQUESTER):
    """Each worker has fetched its task, as an agent that reports always has."""
    store = ResultStore()
    for index, worker in enumerate(workers):
        store.expect(
            f"h-{worker}",
            requester_session_id=requester,
            worker_session_id=worker,
            now=float(index),
        )
        store.mark_read(f"h-{worker}")
    return store


def _report(store, worker, text, status=None):
    """Report as the worker's own agent: with the receipt its read was given.

    Asked for again through ``mark_read`` -- a re-read returns the same
    receipt -- so a case about something else need not carry it around.
    """
    return store.report(worker, text, status, receipt=store.mark_read(f"h-{worker}"))


def _rows(payload):
    return {row["session_id"]: row for row in payload["agents"]}


class TaskLabelRoundTestCase(unittest.TestCase):
    def test_labels_survive_binding_reporting_and_collection_but_stay_out_of_private_reads(self):
        results = ResultStore()
        handoffs = HandoffStore(results=results)
        label = "Review the parser"
        task = "Private brief: inspect token handling."
        report = "Private result: two issues."
        with self.assertLogs("web", level="INFO") as logs:
            first = handoffs.create(task, label=label, source_session_id=REQUESTER)
            handoffs.take(first, REQUESTER)
            handoffs.bind(first, "worker")
            handoffs.announce(first, delivery=INLINE)
            read = handoffs.read("worker")
            receipt = read["receipt"]
            results.report("worker", report, None, receipt)
            collected = results.collect(REQUESTER)
            self.assertEqual(results.links_snapshot()[0]["label"], label)
            handoffs.create_followup("Next task.", label="Second review", session_id="worker",
                                     previous_handoff_id=first, source_session_id=REQUESTER)
        joined = "\n".join(logs.output)
        for private in (label, "Second review", task, report, receipt):
            self.assertNotIn(private, joined)
        self.assertIn(f"label_chars={len(label)}", joined)
        self.assertIn(f"label_chars={len('Second review')}", joined)
        for payload in (handoffs.public_state("worker"), read, collected):
            self.assertNotIn(label, json.dumps(payload))
            self.assertNotIn('"label"', json.dumps(payload))
        link = results.links_snapshot()[0]
        self.assertEqual((link["round"], link["label"]), (2, "Second review"))
        self.assertEqual(set(link), set(LINK_FIELDS))
        for private in (task, report, receipt, first):
            self.assertNotIn(private, json.dumps(link))

    def test_a_followup_does_not_inherit_a_collected_or_evicted_rounds_label(self):
        for capacity in (1, 256):
            with self.subTest(capacity=capacity):
                results = ResultStore(max_assignments=capacity)
                handoffs = HandoffStore(results=results)
                first = handoffs.create("First.", label="First label", source_session_id=REQUESTER,
                                        session_id="worker")
                handoffs.announce(first, delivery=INLINE)
                receipt = handoffs.read("worker")["receipt"]
                results.report("worker", "Done.", None, receipt)
                results.collect(REQUESTER)
                if capacity == 1:
                    results.expect("other", requester_session_id="another", worker_session_id="other")
                handoffs.create_followup("Next.", session_id="worker", previous_handoff_id=first,
                                         source_session_id=REQUESTER)
                link = next(row for row in results.links_snapshot() if row["worker_session_id"] == "worker")
                self.assertEqual((link["round"], link["label"]), (2, ""))


class ReportAndCollectTestCase(unittest.TestCase):
    def test_a_report_is_returned_whole_once_and_then_bookmarked(self):
        store = _store_with("worker-a")

        answer = _report(store, "worker-a", "Found two bugs in sync.py.", "done")
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
        _report(store, "worker-a", "Done.")
        store.collect(REQUESTER)

        payload = store.collect(REQUESTER, include_collected=True)

        self.assertEqual(_rows(payload)["worker-a"]["result"], "Done.")

    def test_a_second_report_replaces_the_first_and_is_new_again(self):
        store = _store_with("worker-a")
        _report(store, "worker-a", "First pass.")
        store.collect(REQUESTER)

        answer = _report(store, "worker-a", "Final answer.", "failed")
        row = _rows(store.collect(REQUESTER))["worker-a"]

        self.assertTrue(answer["replaced_earlier_report"])
        self.assertEqual((row["result"], row["status"], row["revision"]), ("Final answer.", "failed", 2))

    def test_a_pane_nobody_handed_a_task_has_nobody_to_report_to(self):
        store = _store_with("worker-a")

        with self.assertRaises(ResultError) as refused:
            _report(store, "stranger", "Hello.")

        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(refused.exception.message, NOBODY_WAITING_MESSAGE)
        self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["state"], WORKING)

    def test_a_task_nobody_has_read_takes_no_report(self):
        store = ResultStore()
        store.expect("h-a", requester_session_id=REQUESTER, worker_session_id="worker-a")

        with self.assertRaises(ResultError) as refused:
            store.report("worker-a", "Done, supposedly.")

        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(refused.exception.message, UNREAD_TASK_MESSAGE)
        self.assertEqual(store.state_for("h-a"), WORKING)
        receipt = store.mark_read("h-a")
        self.assertTrue(receipt)
        self.assertEqual(store.report("worker-a", "Done.", receipt=receipt)["revision"], 1)

    def test_another_requesters_reports_are_never_returned(self):
        store = _store_with("worker-a")
        store.expect("h-x", requester_session_id="someone-else", worker_session_id="worker-x")
        store.report("worker-x", "Not for you.", receipt=store.mark_read("h-x"))

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
                    _report(store, "worker-a", text, status)
                self.assertIn(expected, refused.exception.message)
                self.assertEqual(refused.exception.status_code, 400)
                self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["state"], WORKING)

    def test_crlf_is_read_as_lf_and_nothing_else_changes(self):
        store = _store_with("worker-a")
        _report(store, "worker-a", "line one\r\n\tline two")

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
            _report(store, worker, worker[-1] * 30)

        first = store.collect(REQUESTER, chars_budget=50)
        second = store.collect(REQUESTER, chars_budget=50)

        self.assertEqual([("result" in row) for row in first["agents"]], [True, False, False])
        self.assertTrue(first["agents"][1]["result_withheld"])
        self.assertIn("did not fit", first["instructions"])
        self.assertEqual([("result" in row) for row in second["agents"]], [False, True, False])
        # The first report always fits, however long, or nothing would move.
        long_store = _store_with("worker-a")
        _report(long_store, "worker-a", "z" * 200)
        self.assertEqual(len(_rows(long_store.collect(REQUESTER, chars_budget=10))["worker-a"]["result"]), 200)

    def test_include_collected_never_starves_an_unseen_report(self):
        """Re-sent reports used to spend the budget first, oldest first, so a
        report the caller had never seen was withheld on every such call."""
        workers = ("w1", "w2", "w3", "w4")
        store = _store_with(*workers)
        for worker in workers[:3]:
            _report(store, worker, worker * 10)
        store.collect(REQUESTER)
        _report(store, "w4", "new " * 10)

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
        _report(store, "worker-a", "Done.")

        self.assertFalse(store.end("h-worker-a", "connection closed"))
        self.assertEqual(_rows(store.collect(REQUESTER))["worker-a"]["result"], "Done.")

    def test_a_report_takes_no_further_writes_once_its_handoff_goes(self):
        """A pane relaunched after it reported runs an agent that was never
        handed the task; it used to be able to overwrite the report."""
        store = _store_with("worker-a")
        _report(store, "worker-a", "The original findings.")

        store.end("h-worker-a", "pane relaunched")
        with self.assertRaises(ResultError) as refused:
            _report(store, "worker-a", "Something else entirely.")

        self.assertEqual(refused.exception.status_code, 409)
        row = _rows(store.collect(REQUESTER))["worker-a"]
        self.assertEqual((row["result"], row["revision"]), ("The original findings.", 1))

    def test_a_closed_workers_report_takes_no_further_writes(self):
        store = _store_with("worker-a")
        _report(store, "worker-a", "Done.")

        store.forget_session("worker-a")

        with self.assertRaises(ResultError):
            _report(store, "worker-a", "Again.")

    def test_an_ended_assignment_takes_no_report(self):
        store = _store_with("worker-a")
        store.end("h-worker-a", "pane relaunched")

        with self.assertRaises(ResultError):
            _report(store, "worker-a", "Too late.")

    def test_the_requesters_pane_closing_drops_what_it_was_owed(self):
        store = _store_with("worker-a", "worker-b")
        _report(store, "worker-a", "Done.")

        self.assertEqual(store.forget_session(REQUESTER), 2)
        self.assertEqual(store.count(), 0)
        with self.assertRaises(ResultError):
            _report(store, "worker-b", "Nobody is listening.")

    def test_a_workers_pane_closing_ends_it_and_keeps_a_report(self):
        store = _store_with("worker-a", "worker-b")
        _report(store, "worker-a", "Done.")

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
        timer = threading.Timer(delay, _report, args=(store, worker, text))
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
        _report(store, "worker-a", "a done")

        settled = store.wait_until_settled(REQUESTER, until="all", timeout=0.05)
        payload = store.collect(REQUESTER)

        self.assertFalse(settled)
        self.assertFalse(payload["complete"])
        self.assertEqual(_rows(payload)["worker-a"]["result"], "a done")

    def test_a_wait_narrowed_to_named_panes_ignores_the_rest(self):
        store = _store_with("worker-a", "worker-b")
        _report(store, "worker-a", "a done")

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

    def _fetch(self, handoff_id, pane="worker-a"):
        """The pane's agent starts and reads its task, as the launch line tells it to."""
        self.handoffs.announce(handoff_id, delivery=INLINE)
        return self.handoffs.read(pane)

    def test_a_handoff_bound_at_birth_is_expected_from_its_pane(self):
        handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")

        self.assertEqual(self.results.state_for(handoff_id), WORKING)
        receipt = self._fetch(handoff_id)["receipt"]
        self.results.report("worker-a", "Reviewed.", receipt=receipt)
        self.assertEqual(_rows(self.results.collect(REQUESTER))["worker-a"]["result"], "Reviewed.")

    def test_a_split_reports_to_the_agent_that_asked_not_the_pane_it_halved(self):
        handoff_id = self.handoffs.create(
            "Review.", source_session_id="neighbour", requester_session_id=REQUESTER
        )
        self.assertIsNone(self.results.state_for(handoff_id))
        self.handoffs.take(handoff_id, "neighbour")
        self.handoffs.bind(handoff_id, "worker-a")
        receipt = self._fetch(handoff_id)["receipt"]

        self.results.report("worker-a", "Reviewed.", receipt=receipt)

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
            # assignment exists. Probe from another thread because a Condition
            # uses a reentrant lock and has no locked() method before Python 3.14.
            def probe_lock():
                acquired = self.handoffs._lock.acquire(blocking=False)
                if acquired:
                    self.handoffs._lock.release()
                seen.append(not acquired)

            probe = threading.Thread(target=probe_lock)
            probe.start()
            probe.join(timeout=2)
            self.assertFalse(probe.is_alive())
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
        receipt = self._fetch(second)["receipt"]

        self.results.report("worker-a", "Did the second.", receipt=receipt)

        self.assertEqual(self.results.state_for(second), REPORTED)

    def test_the_agent_a_relaunch_replaced_cannot_answer_the_new_task(self):
        """A relaunch keeps the pane's id, so the replaced agent's report --
        still in flight when the new task was bound -- used to settle it."""
        first = self.handoffs.create("First.", source_session_id=REQUESTER, session_id="worker-a", now=1.0)
        first_receipt = self._fetch(first)["receipt"]
        second = self.handoffs.create("Second.", source_session_id=REQUESTER, session_id="worker-a", now=2.0)

        with self.assertRaises(ResultError) as refused:
            self.results.report("worker-a", "Did the first.", receipt=first_receipt)

        self.assertEqual(refused.exception.message, UNREAD_TASK_MESSAGE)
        self.assertEqual(self.results.state_for(first), ENDED)
        self.assertEqual(self.results.state_for(second), WORKING)
        read = self._fetch(second)
        self.assertEqual(read["task"], "Second.")
        self.results.report("worker-a", "Did the second.", receipt=read["receipt"])
        self.assertEqual(self.results.state_for(second), REPORTED)

    def test_the_replaced_agents_late_report_cannot_settle_a_task_its_successor_read(self):
        """A3. Once the successor has read its task the pane's assignment is
        read, so a late report from the agent the relaunch replaced -- which
        carries only its own receipt -- used to be recorded as the successor's."""
        first = self.handoffs.create("First.", source_session_id=REQUESTER, session_id="worker-a", now=1.0)
        first_receipt = self._fetch(first)["receipt"]
        second = self.handoffs.create("Second.", source_session_id=REQUESTER, session_id="worker-a", now=2.0)
        second_receipt = self._fetch(second)["receipt"]

        with self.assertRaises(ResultError) as refused:
            self.results.report("worker-a", "Did the first.", receipt=first_receipt)

        self.assertEqual(refused.exception.status_code, 409)
        self.assertEqual(refused.exception.message, STALE_RECEIPT_MESSAGE)
        self.assertIn("read_handoff", refused.exception.message)
        self.assertNotEqual(first_receipt, second_receipt)
        self.assertEqual(self.results.state_for(second), WORKING)
        self.assertEqual(self.results.collect(REQUESTER)["counts"][WORKING], 1)

        self.results.report("worker-a", "Did the second.", receipt=second_receipt)

        self.assertEqual(self.results.state_for(second), REPORTED)
        row = _rows(self.results.collect(REQUESTER))["worker-a"]
        self.assertEqual(row["result"], "Did the second.")

    def test_a_read_task_takes_no_report_without_its_receipt(self):
        handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")
        receipt = self._fetch(handoff_id)["receipt"]

        for label, offered in {
            "missing": None,
            "empty": "",
            "different": receipt[:-1] + ("A" if receipt[-1] != "A" else "B"),
            "not text": ["x"],
            "not ascii": receipt + "\u00e9",
        }.items():
            with self.subTest(label), self.assertRaises(ResultError) as refused:
                self.results.report("worker-a", "Done.", receipt=offered)
            self.assertEqual(refused.exception.message, STALE_RECEIPT_MESSAGE)
        self.assertEqual(self.results.state_for(handoff_id), WORKING)

    def test_reading_again_returns_the_same_receipt(self):
        """What an agent whose tools restarted does when its report is refused."""
        handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")

        first = self._fetch(handoff_id)
        again = self.handoffs.read("worker-a")

        self.assertTrue(first["receipt"])
        self.assertEqual(again["receipt"], first["receipt"])
        self.assertEqual(again["task"], first["task"])
        self.results.report("worker-a", "Done.", receipt=again["receipt"])
        self.assertEqual(self.results.state_for(handoff_id), REPORTED)

    def test_a_receipt_never_reaches_a_collected_row_or_a_log(self):
        handoff_id = self.handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")
        with self.assertLogs("web", level="DEBUG") as logs:
            receipt = self._fetch(handoff_id)["receipt"]
            self.results.report("worker-a", "Done.", receipt=receipt)
            payload = self.results.collect(REQUESTER)

        self.assertNotIn(receipt, json.dumps(payload))
        self.assertNotIn(receipt, "\n".join(logs.output))
        self.assertNotIn(receipt, repr(self.results._records[handoff_id]))

    def test_a_handoff_nobody_waits_on_is_read_without_a_receipt(self):
        handoffs = HandoffStore(results=None)
        handoff_id = handoffs.create("Review.", source_session_id=REQUESTER, session_id="worker-a")
        handoffs.announce(handoff_id, delivery=INLINE)

        self.assertNotIn("receipt", handoffs.read("worker-a"))

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


def _walk(value):
    """Every key and every string value, at any depth."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, str):
        yield value


class LinksSnapshotTestCase(unittest.TestCase):
    """What the dashboard is told about who handed a task to whom."""

    def test_a_link_carries_exactly_its_fields(self):
        store = ResultStore()
        store.expect(
            "h-a",
            requester_session_id=REQUESTER,
            worker_session_id="worker-a",
        )

        (link,) = store.links_snapshot()

        self.assertEqual(tuple(link), LINK_FIELDS)
        self.assertEqual(link["requester_session_id"], REQUESTER)
        self.assertEqual(link["worker_session_id"], "worker-a")
        self.assertEqual(link["state"], WORKING)
        self.assertFalse(link["read"])
        self.assertEqual(link["status"], "")
        self.assertFalse(link["collected"])
        self.assertTrue(link["handed_at"])
        self.assertEqual(link["reported_at"], "")
        self.assertEqual(link["reason"], "")
        self.assertEqual(link["round"], 1)
        self.assertRegex(link["link_id"], r"^[0-9a-f]{16}$")

    def test_no_text_receipt_or_handoff_id_at_any_depth(self):
        handoffs = HandoffStore(results=ResultStore())
        store = handoffs.results
        handoff_id = handoffs.create("Secret brief.", source_session_id=REQUESTER, session_id="worker-a")
        handoffs.announce(handoff_id, delivery=INLINE)
        receipt = handoffs.read("worker-a")["receipt"]
        store.report("worker-a", "Secret report.", receipt=receipt)

        links = store.links_snapshot()
        found = set(_walk(links))

        for forbidden_key in ("text", "receipt", "handoff_id", "result"):
            self.assertNotIn(forbidden_key, found)
        for forbidden_value in (receipt, handoff_id, "Secret report.", "Secret brief."):
            self.assertNotIn(forbidden_value, found)
            self.assertNotIn(forbidden_value, json.dumps(links))
        self.assertNotEqual(links[0]["link_id"], handoff_id)

    def test_a_link_id_is_stable_across_reads_and_settling(self):
        store = _store_with("worker-a")
        first = store.links_snapshot()[0]["link_id"]
        _report(store, "worker-a", "Done.")
        store.collect(REQUESTER)

        self.assertEqual(store.links_snapshot()[0]["link_id"], first)

    def test_links_come_oldest_handed_first(self):
        store = ResultStore()
        store.expect("h-b", requester_session_id=REQUESTER, worker_session_id="worker-b", now=2.0)
        store.expect("h-a", requester_session_id=REQUESTER, worker_session_id="worker-a", now=1.0)

        self.assertEqual(
            [link["worker_session_id"] for link in store.links_snapshot()],
            ["worker-a", "worker-b"],
        )

    def test_a_settled_link_names_its_outcome(self):
        store = _store_with("worker-a", "worker-b")
        _report(store, "worker-a", "Stuck.", status="blocked")
        store.end("h-worker-b", "pane relaunched")

        reported, ended = store.links_snapshot()

        self.assertEqual((reported["state"], reported["status"]), (REPORTED, "blocked"))
        self.assertTrue(reported["reported_at"])
        self.assertTrue(reported["read"])
        # The key, never the sentence the requester is shown.
        self.assertEqual((ended["state"], ended["reason"]), (ENDED, "pane relaunched"))

    def test_an_end_reason_is_always_a_key(self):
        store = _store_with("worker-a", "worker-b", "worker-c")
        store.end("h-worker-a", "its agent could not be handed the task: no tools", key="undeliverable")
        store.end("h-worker-b", "something new")
        store.forget_session("worker-c")

        self.assertEqual(
            [link["reason"] for link in store.links_snapshot()],
            ["undeliverable", "other", "pane closed"],
        )

    def test_an_uncollected_round_and_its_follow_up_are_both_listed(self):
        store = _store_with("worker-a")
        _report(store, "worker-a", "Round one.")
        store.end("h-worker-a", "replaced")
        store.expect(
            "h-2", requester_session_id=REQUESTER, worker_session_id="worker-a",
            continues="h-worker-a", now=5.0,
        )

        links = store.links_snapshot()

        self.assertEqual([link["round"] for link in links], [1, 2])
        self.assertEqual([link["state"] for link in links], [REPORTED, WORKING])
        self.assertNotEqual(links[0]["link_id"], links[1]["link_id"])

    def test_a_link_goes_with_its_requesters_pane(self):
        store = _store_with("worker-a")
        store.forget_session(REQUESTER)

        self.assertEqual(store.links_snapshot(), [])


class RoundTestCase(unittest.TestCase):
    """Only a follow-up advances the round, and it is read before the drop."""

    def _followup(self, store, previous, handoff_id, now):
        store.end(previous, "replaced")
        store.expect(
            handoff_id, requester_session_id=REQUESTER, worker_session_id="worker-a",
            continues=previous, now=now,
        )
        store.mark_read(handoff_id)

    def test_a_task_that_starts_an_agent_is_round_one(self):
        store = _store_with("worker-a")

        self.assertEqual(store.links_snapshot()[0]["round"], 1)

    def test_a_follow_up_is_the_round_it_continues_plus_one(self):
        store = _store_with("worker-a")
        _report(store, "worker-a", "One.")
        self._followup(store, "h-worker-a", "h-2", 5.0)

        self.assertEqual(store.links_snapshot()[-1]["round"], 2)

    def test_the_round_survives_the_continued_round_being_dropped_by_the_same_call(self):
        store = _store_with("worker-a")
        _report(store, "worker-a", "One.")
        store.collect(REQUESTER)
        self._followup(store, "h-worker-a", "h-2", 5.0)

        links = store.links_snapshot()

        # The collected round went in that very `expect`, after its round was read.
        self.assertEqual([link["round"] for link in links], [2])

    def test_a_follow_up_whose_round_was_evicted_is_round_two(self):
        store = ResultStore()
        store.expect(
            "h-2", requester_session_id=REQUESTER, worker_session_id="worker-a",
            continues="h-gone",
        )

        self.assertEqual(store.links_snapshot()[0]["round"], 2)


class WaitingReadingTestCase(unittest.TestCase):
    """Which requesters are waiting on their crew, for the dashboard."""

    def test_the_grace_window_is_pinned(self):
        self.assertEqual(WAIT_GRACE_SECONDS, 5.0)
        self.assertLess(WAIT_GRACE_SECONDS, MAX_WAIT_SECONDS)

    def test_an_open_wait_counts_while_it_blocks(self):
        store = _store_with("worker-a")
        thread = threading.Thread(
            target=store.wait_until_settled, args=(REQUESTER,), kwargs={"timeout": 5}
        )
        thread.start()
        try:
            deadline = time.monotonic() + 2
            while REQUESTER not in store.waiting_requesters() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(store.waiting_requesters(), {REQUESTER})
            self.assertEqual(store._waits_open, {REQUESTER: 1})
        finally:
            _report(store, "worker-a", "Done.")
            thread.join(5)
        self.assertEqual(store._waits_open, {})

    def test_a_wait_that_ended_still_counts_for_the_grace_window(self):
        store = _store_with("worker-a")
        store.wait_until_settled(REQUESTER, timeout=0.01)
        ended = time.monotonic()

        self.assertIn(REQUESTER, store.waiting_requesters(ended + WAIT_GRACE_SECONDS - 0.5))
        self.assertNotIn(REQUESTER, store.waiting_requesters(ended + WAIT_GRACE_SECONDS + 0.5))

    def test_a_wait_zero_read_does_not_open_the_grace_window(self):
        store = _store_with("worker-a")
        store.wait_until_settled(REQUESTER, timeout=0)

        self.assertEqual(store.waiting_requesters(), frozenset())
        self.assertEqual(store._waits_open, {})

    def test_a_requesters_pane_closing_ends_its_grace(self):
        store = _store_with("worker-a")
        store.wait_until_settled(REQUESTER, timeout=0.01)
        store.forget_session(REQUESTER)

        self.assertEqual(store.waiting_requesters(), frozenset())


if __name__ == "__main__":
    unittest.main()
