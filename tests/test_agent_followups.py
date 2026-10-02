"""Handing a running agent its next task: ``send_task`` and ``wait_for_task``.

The scenario this exists for is a back-and-forth with one agent: an agent
splits off a worker, hands it a task, reads its report, answers, and the
*same* worker -- not a relaunched one -- carries on. Pinned here:

- **The same agent keeps going.** A follow-up is bound to the connection whose
  agent read the last task; nothing is relaunched and nothing is typed. Rounds
  that were collected collapse, so a wait lists the worker once.
- **Only the agent that asked, only after its report, only to the agent that
  read it.** Every other case is refused with the store and the connection as
  they were, and ``override`` changes none of it.
- **A worker standing by is woken by the follow-up**, and is never left standing
  by for nobody: its requester closing, its connection closing, or a task it
  has not reported on ends the wait with the reason.
- **The sidecar keeps the receipt** a follow-up read carries, refuses what it
  can before any HTTP, and a tunnelled wait blocks on the store in-process.
"""

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.client import FOLLOWUP_FIELDS, HANDOFF_FIELDS  # noqa: E402
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import dispatch, tool_specs  # noqa: E402
from tests.test_agent_result_routes import _ResultRouteCase  # noqa: E402
from tests.test_mcp_client import StubOpener, client_for  # noqa: E402
from tests.test_mcp_results import INSIDE_PANE, RefusingOpener, _query  # noqa: E402
from web import agent_followups, api, mcp_http  # noqa: E402
from web import terminal_io as web_terminal_io  # noqa: E402
from web.agent_handoffs import (  # noqa: E402
    FOLLOWUP_STALE_MESSAGE,
    INLINE,
    INLINE_TASK_MAX_CHARS,
    NEXT_NONE,
    NEXT_TASK,
    NEXT_TIMED_OUT,
    NO_FOLLOWUP_MESSAGE,
    PAGED,
    READ,
    REPORT_FIRST_MESSAGE,
    HandoffError,
    HandoffStore,
)
from web.agent_handoffs import handoffs as handoff_store  # noqa: E402
from web.agent_results import (  # noqa: E402
    MAX_WAIT_SECONDS,
    REPORTED,
    WORKING,
    ResultStore,
)


def _until(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ==================== the stores ====================


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.results = ResultStore()
        self.store = HandoffStore(results=self.results)

    def _read_task(self, session_id="worker", requester="caller", text="First."):
        """A task bound, announced and read: the agent is working on it."""
        handoff_id = self.store.create(text, source_session_id=requester, session_id=session_id)
        self.store.announce(handoff_id, delivery=INLINE)
        receipt = self.store.read(session_id)["receipt"]
        return handoff_id, receipt

    def _followup(self, previous, text="Next.", session_id="worker", requester="caller"):
        return self.store.create_followup(
            text,
            session_id=session_id,
            previous_handoff_id=previous,
            source_session_id=requester,
        )


class CreateFollowupTestCase(_StoreCase):
    def test_a_followup_is_born_announced_and_its_predecessors_report_stands(self):
        first, receipt = self._read_task()
        self.results.report("worker", "Guess: 7.", None, receipt)

        view = self._followup(first, "Wrong. Guess again.")
        read = self.store.read("worker")

        self.assertEqual(read["task"], "Wrong. Guess again.")
        self.assertEqual(read["delivery"], INLINE)
        self.assertNotEqual(read["receipt"], receipt)
        self.assertEqual(self.results.state_for(first), REPORTED)
        self.assertEqual(self.results.state_for(view.handoff_id), WORKING)
        self.assertEqual(self.store.count(), 1)

    def test_a_long_followup_is_delivered_in_pages_since_no_file_is_written(self):
        first, receipt = self._read_task()
        self.results.report("worker", "Done.", None, receipt)

        self._followup(first, "x" * (INLINE_TASK_MAX_CHARS + 5))
        read = self.store.read("worker")

        self.assertEqual(read["delivery"], PAGED)
        self.assertEqual(read["next_offset"], INLINE_TASK_MAX_CHARS)

    def test_a_followup_onto_a_task_that_moved_on_is_refused(self):
        first, _receipt = self._read_task()
        unread = self.store.create("Unread.", source_session_id="caller", session_id="other")
        self.store.announce(unread, delivery=INLINE)

        for label, previous, session_id in (
            ("unknown", "nope", "worker"),
            ("another pane's", first, "other"),
            ("not read yet", unread, "other"),
        ):
            with self.subTest(label):
                with self.assertRaises(HandoffError) as caught:
                    self._followup(previous, session_id=session_id)
                self.assertEqual(caught.exception.message, FOLLOWUP_STALE_MESSAGE)
        self.assertEqual(self.store.public_state("worker")["state"], READ)
        self.assertEqual(self.store.count(), 2)


class WaitForNextTaskTestCase(_StoreCase):
    def test_a_followup_wakes_the_agent_standing_by(self):
        first, receipt = self._read_task()
        self.results.report("worker", "Done.", None, receipt)
        outcome = {}
        waiter = threading.Thread(
            target=lambda: outcome.update(answer=self.store.wait_for_next_task("worker", 10))
        )
        waiter.start()
        self.assertTrue(_until(lambda: self.store.awaiting_task(["worker"])["worker"]))

        started = time.monotonic()
        self._followup(first)
        waiter.join(5)

        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(outcome["answer"], (NEXT_TASK, ""))
        self.assertFalse(self.store.awaiting_task(["worker"])["worker"])

    def test_a_wait_with_nothing_coming_says_why_at_once(self):
        _first, _receipt = self._read_task()

        cases = (
            ("working on a read task", "worker", REPORT_FIRST_MESSAGE),
            ("never handed one", "stranger", NO_FOLLOWUP_MESSAGE),
        )
        for label, session_id, message in cases:
            with self.subTest(label):
                started = time.monotonic()
                self.assertEqual(
                    self.store.wait_for_next_task(session_id, 10), (NEXT_NONE, message)
                )
                self.assertLess(time.monotonic() - started, 1)

    def test_an_unread_task_is_returned_at_once(self):
        handoff_id = self.store.create("First.", source_session_id="caller", session_id="worker")
        self.store.announce(handoff_id, delivery=INLINE)

        self.assertEqual(self.store.wait_for_next_task("worker", 10), (NEXT_TASK, ""))

    def test_the_wait_runs_out_while_a_followup_can_still_come(self):
        _first, receipt = self._read_task()
        self.results.report("worker", "Done.", None, receipt)

        self.assertEqual(self.store.wait_for_next_task("worker", 0.05), (NEXT_TIMED_OUT, ""))

    def test_the_requester_closing_ends_the_wait(self):
        _first, receipt = self._read_task()
        self.results.report("worker", "Done.", None, receipt)
        threading.Timer(0.1, self.results.forget_session, args=("caller",)).start()

        started = time.monotonic()
        answer = self.store.wait_for_next_task("worker", 10)

        self.assertEqual(answer, (NEXT_NONE, NO_FOLLOWUP_MESSAGE))
        self.assertLess(time.monotonic() - started, 4)

    def test_the_connection_closing_ends_the_wait(self):
        first, receipt = self._read_task()
        self.results.report("worker", "Done.", None, receipt)
        threading.Timer(0.1, self.store.drop, args=(first, "connection closed")).start()

        started = time.monotonic()
        answer = self.store.wait_for_next_task("worker", 10)

        self.assertEqual(answer, (NEXT_NONE, NO_FOLLOWUP_MESSAGE))
        self.assertLess(time.monotonic() - started, 4)


class SupersededAssignmentTestCase(unittest.TestCase):
    def setUp(self):
        self.results = ResultStore()

    def _round(self, handoff_id, collect):
        self.results.expect(handoff_id, requester_session_id="caller", worker_session_id="worker")
        receipt = self.results.mark_read(handoff_id)
        self.results.report("worker", f"{handoff_id} done.", None, receipt)
        self.results.end(handoff_id, "replaced")
        if collect:
            self.results.collect("caller")

    def test_a_collected_round_goes_when_the_next_one_starts(self):
        self._round("h1", collect=True)
        self.results.expect("h2", requester_session_id="caller", worker_session_id="worker")

        self.assertIsNone(self.results.state_for("h1"))
        self.assertEqual(self.results.count(), 1)

    def test_an_uncollected_report_stays_until_it_is_returned(self):
        self._round("h1", collect=False)
        self.results.expect("h2", requester_session_id="caller", worker_session_id="worker")

        rows = self.results.collect("caller")["agents"]

        self.assertEqual([row["state"] for row in rows], [REPORTED, WORKING])
        self.assertEqual(rows[0]["result"], "h1 done.")

    def test_another_requesters_rounds_are_left_alone(self):
        self._round("h1", collect=True)
        self.results.expect("h2", requester_session_id="someone", worker_session_id="worker")

        self.assertEqual(self.results.state_for("h1"), REPORTED)


# ==================== through the routes ====================


class _FollowupRouteCase(_ResultRouteCase):
    def _start_worker(self, caller, task="Think of a number from 1 to 10."):
        """Split off a worker whose agent is running on a live connection and
        has read its task, as the startup sequence and its first call leave it."""
        worker = self._split_agent(caller, task=task)
        pending = handoff_store.pending_for(worker)
        handoff_store.announce(pending.handoff_id, delivery=INLINE)
        connection = {"handoff_id": pending.handoff_id}
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections[worker] = connection
        receipt = self.client.get(f"/api/sessions/{worker}/handoff").get_json()["receipt"]
        return worker, connection, receipt

    def _report_with(self, worker, result, receipt):
        return self.client.post(
            f"/api/sessions/{worker}/handoff-report",
            json={"result": result, "receipt": receipt},
        )

    def _send(self, caller_id, worker, task="Next.", **extra):
        body = {"requested_by_session_id": caller_id, "task": task, **extra}
        return self.client.post(f"/api/sessions/{worker}/handoff-followup", json=body)

    def _next(self, worker, wait="0"):
        return self.client.get(f"/api/sessions/{worker}/handoff-next?wait={wait}")


class GuessingGameTestCase(_FollowupRouteCase):
    def test_the_same_agent_guesses_until_it_is_right(self):
        """The game from the report: guess, hear "wrong", guess again -- one
        worker, one connection, every round."""
        caller = self._agent_pane(title="Claude 1")
        worker, connection, receipt = self._start_worker(caller)
        self.assertEqual(self._report_with(worker, "7", receipt).status_code, 200)

        for guess in ("3", "4", None):
            rows = self._wait(caller.session_id).get_json()["agents"]
            self.assertEqual(len(rows), 1)
            self.assertEqual((rows[0]["session_id"], rows[0]["state"]), (worker, REPORTED))
            answer = "Wrong. Guess again." if guess else "Right. You are done."

            sent = self._send(caller.session_id, worker, answer)
            task = self._next(worker).get_json()

            self.assertEqual(sent.status_code, 200, sent.get_json())
            self.assertTrue(sent.get_json()["handed"])
            self.assertNotIn(answer, json.dumps(sent.get_json()))
            self.assertEqual(task["task"], answer)
            self.assertEqual(task["from"]["session_id"], caller.session_id)
            # Never relaunched: the same connection carries every round.
            self.assertIs(web_terminal_io.ssh_connections[worker], connection)
            if guess:
                reported = self._report_with(worker, guess, task["receipt"])
                self.assertEqual(reported.status_code, 200, reported.get_json())

        rows = self._wait(caller.session_id).get_json()["agents"]
        self.assertEqual([row["state"] for row in rows], [WORKING])
        self.assertEqual(handoff_store.count(), 1)

    def test_a_report_says_how_to_stand_by(self):
        caller = self._agent_pane()
        worker, _connection, receipt = self._start_worker(caller)

        response = self._report_with(worker, "7", receipt).get_json()

        self.assertIn("wait_for_task", response["instructions"])

    def test_an_agent_standing_by_receives_the_task_as_its_calls_result(self):
        caller = self._agent_pane()
        worker, _connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)
        self._wait(caller.session_id)
        outcome = {}
        waiter = threading.Thread(
            target=lambda: outcome.update(answer=agent_followups.next_task(worker, 10))
        )
        waiter.start()
        self.assertTrue(_until(lambda: handoff_store.awaiting_task([worker])[worker]))

        row = self._wait(caller.session_id).get_json()["agents"][0]
        sent = self._send(caller.session_id, worker, "Wrong. Guess again.").get_json()
        waiter.join(5)

        self.assertTrue(row["standing_by"])
        self.assertTrue(sent["agent_standing_by"])
        self.assertNotIn("note", sent)
        self.assertEqual(outcome["answer"]["task"], "Wrong. Guess again.")
        self.assertTrue(outcome["answer"]["receipt"])

    def test_an_agent_not_standing_by_is_still_handed_the_task_and_the_sender_told(self):
        caller = self._agent_pane()
        worker, _connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)

        sent = self._send(caller.session_id, worker).get_json()
        read = self.client.get(f"/api/sessions/{worker}/handoff").get_json()

        self.assertFalse(sent["agent_standing_by"])
        self.assertIn("wait_for_task", sent["note"])
        self.assertEqual(read["task"], "Next.")

    def test_an_uncollected_report_is_still_returned_after_a_followup(self):
        caller = self._agent_pane()
        worker, _connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)

        self.assertEqual(self._send(caller.session_id, worker).status_code, 200)
        rows = self._wait(caller.session_id).get_json()["agents"]

        self.assertEqual([(row["state"], row.get("result")) for row in rows], [(REPORTED, "7"), (WORKING, None)])

    def test_a_long_followup_travels_in_pages(self):
        caller = self._agent_pane()
        worker, _connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)

        sent = self._send(caller.session_id, worker, "y" * (INLINE_TASK_MAX_CHARS + 1)).get_json()

        self.assertEqual(sent["delivery"], PAGED)
        self.assertIsNotNone(self._next(worker).get_json()["next_offset"])


class FollowupRefusalTestCase(_FollowupRouteCase):
    def _reported_worker(self):
        caller = self._agent_pane()
        worker, connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)
        return caller, worker, connection

    def _assert_nothing_changed(self, worker, connection, before):
        self.assertEqual(connection["handoff_id"], before)
        self.assertEqual(handoff_store.count(), 1)
        self.assertEqual(handoff_store.public_state(worker)["state"], READ)

    def test_only_the_agent_that_handed_the_task_may_follow_up_override_or_not(self):
        caller, worker, connection = self._reported_worker()
        before = connection["handoff_id"]
        stranger = self._agent_pane(title="Someone else")

        for extra in ({}, {"override": True}):
            with self.subTest(extra=extra):
                response = self._send(stranger.session_id, worker, **extra)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json()["gate"], "lineage")
                self.assertFalse(response.get_json()["waivable"])
                self.assertIn("different pane", response.get_json()["error"])
        self._assert_nothing_changed(worker, connection, before)

    def test_an_agent_still_working_is_not_handed_another_task(self):
        caller = self._agent_pane()
        worker, connection, _receipt = self._start_worker(caller)
        before = connection["handoff_id"]

        response = self._send(caller.session_id, worker)

        self.assertEqual(response.status_code, 409)
        self.assertIn("wait_for_results", response.get_json()["error"])
        self._assert_nothing_changed(worker, connection, before)

    def test_refusals_before_any_rule(self):
        caller, worker, connection = self._reported_worker()
        before = connection["handoff_id"]
        cases = (
            ({"requested_by_session_id": caller.session_id, "task": "a\x1bb"}, 400, "U+001B"),
            ({"requested_by_session_id": caller.session_id}, 400, "must be text"),
            ({"task": "Next."}, 400, "requested_by_session_id is required"),
        )
        for body, status, expected in cases:
            with self.subTest(expected):
                response = self.client.post(f"/api/sessions/{worker}/handoff-followup", json=body)
                self.assertEqual(response.status_code, status)
                self.assertIn(expected, response.get_json()["error"])
        self._assert_nothing_changed(worker, connection, before)

    def test_self_and_a_pane_with_no_agent_are_refused(self):
        caller, worker, connection = self._reported_worker()
        terminal = self._pane()

        own = self._send(caller.session_id, caller.session_id)
        plain = self._send(caller.session_id, terminal.session_id)

        self.assertEqual((own.status_code, own.get_json()["gate"]), (403, "self"))
        self.assertEqual((plain.status_code, plain.get_json()["gate"]), (403, "mode"))
        self.assertEqual(self._send(caller.session_id, "nope").status_code, 404)

    def test_a_connection_that_no_longer_holds_the_read_task_is_refused(self):
        caller, worker, connection = self._reported_worker()
        before = connection["handoff_id"]
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections.pop(worker)

        response = self._send(caller.session_id, worker)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], FOLLOWUP_STALE_MESSAGE)
        self._assert_nothing_changed(worker, connection, before)

    def test_a_closed_connection_ends_the_conversation(self):
        caller, worker, connection = self._reported_worker()

        web_terminal_io._shutdown_connection(connection)
        response = self._send(caller.session_id, worker)

        self.assertEqual(response.status_code, 403)
        self.assertIn("No agent you handed a task to", response.get_json()["error"])
        self.assertEqual(handoff_store.count(), 0)

    def test_a_followup_handed_over_is_dropped_with_its_connection(self):
        caller, worker, connection = self._reported_worker()
        self._send(caller.session_id, worker)

        web_terminal_io._shutdown_connection(connection)
        row = self._wait(caller.session_id).get_json()["agents"][-1]

        self.assertEqual(row["state"], "ended")
        self.assertIn("connection closed", row["reason"])


class AgentLifetimeTestCase(_FollowupRouteCase):
    """A handoff belongs to the agent it was announced to, not to the
    connection: an agent that exits, or is swapped at the same shell, takes
    its task -- and the conversation -- with it."""

    def setUp(self):
        super().setUp()
        broadcast = patch.object(web_terminal_io, "_broadcast_session_status")
        broadcast.start()
        self.addCleanup(broadcast.stop)

    def _reported_worker(self):
        caller = self._agent_pane()
        worker, connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)
        self._wait(caller.session_id)
        return caller, worker, connection

    def test_an_agent_restarted_by_hand_is_not_handed_the_conversation(self):
        """The reviewer's case: the agent exits and the person types `claude`
        again. Same connection, same pane -- a different agent."""
        caller, worker, connection = self._reported_worker()

        self.assertTrue(web_terminal_io._mark_runtime_agent_exited(worker, "shell prompt"))
        api.session_manager.update_session_metadata(
            worker, startup_mode="agent", agent_selection="claude"
        )
        sent = self._send(caller.session_id, worker)
        read = self.client.get(f"/api/sessions/{worker}/handoff").get_json()

        self.assertEqual(sent.status_code, 403)
        self.assertIn("No agent you handed a task to", sent.get_json()["error"])
        self.assertIsNone(read["handoff"])
        self.assertNotIn("handoff_id", connection)
        self.assertEqual(handoff_store.count(), 0)
        self.assertEqual(self._next(worker).get_json()["message"], NO_FOLLOWUP_MESSAGE)

    def test_an_agent_that_exits_before_reporting_reads_ended(self):
        caller = self._agent_pane()
        worker, _connection, _receipt = self._start_worker(caller)

        web_terminal_io._mark_runtime_agent_exited(worker, "exit command")
        row = self._wait(caller.session_id).get_json()["agents"][0]

        self.assertEqual(row["state"], "ended")
        self.assertEqual(row["reason"], "its agent exited before it reported")

    def test_a_followup_handed_over_goes_when_its_agent_exits(self):
        caller, worker, _connection = self._reported_worker()
        self.assertEqual(self._send(caller.session_id, worker).status_code, 200)

        web_terminal_io._mark_runtime_agent_exited(worker, "end-of-input")
        row = self._wait(caller.session_id).get_json()["agents"][-1]

        self.assertEqual(row["state"], "ended")
        self.assertIn("its agent exited", row["reason"])

    def test_an_agent_swapped_for_another_at_the_same_shell_drops_the_task(self):
        caller, worker, connection = self._reported_worker()
        connection["agent_relaunch_pending"] = {
            "agent_selection": "codex",
            "at": time.monotonic(),
            "submitted_at": time.time(),
        }

        self.assertTrue(web_terminal_io._promote_pending_agent_relaunch(worker, connection, 1))
        sent = self._send(caller.session_id, worker)

        self.assertEqual(api.session_manager.get_session(worker).agent_selection, "codex")
        self.assertEqual(sent.status_code, 403)
        self.assertNotIn("handoff_id", connection)

    def test_a_task_still_working_ends_with_the_swap_named(self):
        caller = self._agent_pane()
        worker, connection, _receipt = self._start_worker(caller)

        web_terminal_io._retire_agent_handoff(connection, "agent replaced")
        row = self._wait(caller.session_id).get_json()["agents"][0]

        self.assertEqual(row["reason"], "a different agent was started in its pane before it reported")


class NextTaskRouteTestCase(_FollowupRouteCase):
    def test_a_wait_with_nothing_yet_says_to_call_again(self):
        caller = self._agent_pane()
        worker, _connection, receipt = self._start_worker(caller)
        self._report_with(worker, "7", receipt)

        payload = self._next(worker).get_json()

        self.assertTrue(payload["timed_out"])
        self.assertIsNone(payload["handoff"])
        self.assertIn("again", payload["instructions"])
        self.assertIn("waited_seconds", payload)

    def test_a_wait_nobody_can_answer_says_so(self):
        pane = self._agent_pane()

        payload = self._next(pane.session_id, wait="10").get_json()

        self.assertEqual(payload["message"], NO_FOLLOWUP_MESSAGE)
        self.assertNotIn("timed_out", payload)
        self.assertLess(payload["waited_seconds"], 5)

    def test_bad_arguments_and_unknown_panes(self):
        pane = self._agent_pane()

        self.assertEqual(self._next(pane.session_id, wait="soon").status_code, 400)
        self.assertEqual(self._next("nope").status_code, 404)


# ==================== the tool surface ====================


def _spec(name):
    return next(item for item in tool_specs() if item["name"] == name)


class FollowupToolSurfaceTestCase(unittest.TestCase):
    def test_the_schemas(self):
        send = _spec("send_task")["inputSchema"]
        wait = _spec("wait_for_task")["inputSchema"]

        self.assertEqual(set(send["properties"]), {"pane_id", "task"})
        self.assertEqual(send["required"], ["pane_id", "task"])
        self.assertEqual(set(wait["properties"]), {"wait_seconds"})
        self.assertEqual(wait["properties"]["wait_seconds"]["maximum"], MAX_WAIT_SECONDS)

    def test_the_descriptions_point_at_each_other(self):
        self.assertIn("wait_for_task", _spec("send_task")["description"])
        self.assertIn("send_task", _spec("wait_for_task")["description"])
        self.assertIn("send_task", _spec("set_pane_agent")["description"])

    def test_send_task_refuses_before_any_http(self):
        cases = {
            "no pane": ({"task": "Next."}, INSIDE_PANE, "needs a 'pane_id'"),
            "no task": ({"pane_id": "pane-2"}, INSIDE_PANE, "needs a 'task'"),
            "control": ({"pane_id": "pane-2", "task": "a\x07b"}, INSIDE_PANE, "U+0007"),
            "not a pane": ({"pane_id": "pane-2", "task": "Next."}, {}, "not started by GridVibe"),
        }
        for label, (arguments, environ, expected) in cases.items():
            with self.subTest(label):
                result = dispatch(
                    "send_task",
                    arguments,
                    client=client_for(RefusingOpener(self)),
                    identity=read_identity(environ),
                )
                self.assertIn(expected, result["error"])

    def test_send_task_names_the_caller_and_publishes_only_its_fields(self):
        opener = StubOpener([{
            "handed": True,
            "session_id": "pane-2",
            "delivery": "inline",
            "chars": 5,
            "agent_standing_by": True,
            "instructions": "Collect its report with wait_for_results.",
            "handoff_id": "secret-handle",
        }])

        result = dispatch(
            "send_task",
            {"pane_id": "pane-2", "task": "Next."},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        request = opener.requests[0]
        self.assertTrue(request.full_url.endswith("/api/sessions/pane-2/handoff-followup"))
        self.assertEqual(json.loads(request.data), {"requested_by_session_id": "pane-1", "task": "Next."})
        self.assertEqual(result["pane_id"], "pane-2")
        self.assertNotIn("handoff_id", result)
        self.assertLessEqual(set(result) - {"pane_id"}, set(FOLLOWUP_FIELDS))

    def test_wait_for_task_keeps_the_receipt_for_the_next_report(self):
        opener = StubOpener([
            {"delivery": "inline", "task": "Guess again.", "chars": 12, "receipt": "r-next", "waited_seconds": 1.0},
            {"recorded": True, "revision": 1, "status": "done", "chars": 1},
        ])
        client = client_for(opener)
        identity = read_identity(INSIDE_PANE)

        task = dispatch("wait_for_task", {"wait_seconds": 20}, client=client, identity=identity)
        dispatch("report_result", {"result": "4"}, client=client, identity=identity)

        self.assertEqual(task["task"], "Guess again.")
        self.assertNotIn("r-next", json.dumps(task))
        self.assertNotIn("receipt", HANDOFF_FIELDS)
        self.assertEqual(_query(opener.requests[0])["wait"], "20")
        self.assertTrue(opener.requests[0].full_url.split("?")[0].endswith("/pane-1/handoff-next"))
        self.assertEqual(json.loads(opener.requests[1].data)["receipt"], "r-next")

    def test_a_tunnelled_wait_stands_by_on_the_store_and_asks_with_wait_zero(self):
        opener = StubOpener([{"handoff": None, "timed_out": True, "waited_seconds": 0.0}])
        client = mcp_http._in_process_client_type()("http://127.0.0.1:5050", opener=opener)

        with patch.object(
            mcp_http.agent_handoffs, "wait_for_next_task", return_value=(NEXT_TIMED_OUT, "")
        ) as waited:
            result = client.wait_for_task("pane-1", 30)

        waited.assert_called_once_with("pane-1", 30)
        self.assertEqual(_query(opener.requests[0])["wait"], "0")
        self.assertLess(opener.timeouts[0], 30)
        self.assertIn("waited_seconds", result)


if __name__ == "__main__":
    unittest.main()
