"""Handing results back, end to end through GridVibe's own routes.

The scenario this exists for: an agent splits its own terminal into three
fresh Codex agents, hands each the same task, waits for their reports, and
reads all three. Pinned here through the real split-intent and split routes:

- **Each report reaches the agent that asked** -- which is not the pane a split
  halved when the agent split a neighbour -- and nobody else.
- **The wait holds until they are all in**, answers at once with ``wait=0``,
  and a report arriving mid-wait ends it early.
- **A pane closed before it reported is reported as ended**, and a pane closed
  *after* it reported keeps its report.
- **The requester's pane closing drops what it was owed**, so a late report is
  told nobody is listening.
- **A report settles only a task its agent has read**, so the agent a relaunch
  replaced cannot answer for the new one.
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
from tests.test_agent_handoff_routes import BRIEF, _detect_found, _RouteCase  # noqa: E402
from web import agents as web_agents  # noqa: E402
from web import api  # noqa: E402
from web import terminal_io as web_terminal_io  # noqa: E402
from web.agent_handoffs import INLINE  # noqa: E402
from web.agent_handoffs import handoffs as handoff_store  # noqa: E402
from web.agent_results import ENDED, MAX_RESULT_CHARS, REPORTED, WORKING  # noqa: E402
from web.agent_results import results as result_store  # noqa: E402


class _ResultRouteCase(_RouteCase):
    def setUp(self):
        super().setUp()
        result_store.reset()
        self.addCleanup(result_store.reset)

    def _split_agent(self, caller, source=None, agent="codex", task=BRIEF):
        """Record a split intent with a task and perform it, as the page would."""
        source = source or caller
        with patch.object(web_agents, "_detect_agent_binary_cached", side_effect=_detect_found):
            intent = self.client.post(
                f"/api/sessions/{source.session_id}/split-intent",
                json={
                    "axis": "vertical",
                    "kind": "agent",
                    "agent": agent,
                    "task": task,
                    "origin_session_id": caller.session_id,
                },
            )
        self.assertEqual(intent.status_code, 201, intent.get_json())
        with patch.object(web_agents, "_detect_agent_binary_cached", side_effect=_detect_found), \
                patch.object(web_terminal_io, "_resolve_live_terminal_cwd", return_value=None), \
                patch.object(api.socketio, "start_background_task"):
            split = self.client.post(
                f"/api/sessions/{source.session_id}/split",
                json=intent.get_json()["split_request"],
            )
        self.assertEqual(split.status_code, 201, split.get_json())
        return split.get_json()["session"]["session_id"]

    def _fetch_task(self, pane_id):
        """What the worker's agent does first: its launch line announces the
        brief, and it reads it -- which is what lets its report settle it."""
        pending = handoff_store.pending_for(pane_id)
        if pending is not None:
            handoff_store.announce(pending.handoff_id, delivery=INLINE)
        return self.client.get(f"/api/sessions/{pane_id}/handoff")

    def _report(self, pane_id, result, status=None):
        self._fetch_task(pane_id)
        body = {"result": result}
        if status is not None:
            body["status"] = status
        return self.client.post(f"/api/sessions/{pane_id}/handoff-report", json=body)

    def _wait(self, pane_id, **params):
        params.setdefault("wait", "0")
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return self.client.get(f"/api/sessions/{pane_id}/handoff-reports?{query}")

    def _rows(self, payload):
        return {row["session_id"]: row for row in payload["agents"]}


class ThreeAgentsTestCase(_ResultRouteCase):
    def test_three_codex_agents_split_off_this_terminal_report_back(self):
        caller = self._agent_pane(title="Claude 1")
        workers = [self._split_agent(caller) for _ in range(3)]

        before = self._wait(caller.session_id).get_json()
        for index, worker in enumerate(workers):
            response = self._report(worker, f"Part {index + 1}: implemented and tested.")
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["reported_to"]["session_id"], caller.session_id)
            self.assertEqual(response.get_json()["reported_to"]["title"], "Claude 1")
        after = self._wait(caller.session_id).get_json()
        again = self._wait(caller.session_id).get_json()

        self.assertEqual(before["counts"], {WORKING: 3, REPORTED: 0, ENDED: 0})
        self.assertTrue(before["timed_out"])
        self.assertFalse(before["complete"])
        self.assertIn("still working", before["instructions"])
        self.assertEqual({row["task_state"] for row in before["agents"]}, {"waiting"})
        self.assertTrue(after["complete"])
        self.assertFalse(after["timed_out"])
        self.assertEqual(
            [row["result"] for row in after["agents"]],
            [f"Part {n}: implemented and tested." for n in (1, 2, 3)],
        )
        self.assertEqual([row["session_id"] for row in after["agents"]], workers)
        for row in after["agents"]:
            self.assertEqual((row["agent"], row["pane_open"], row["status"]), ("codex", True, "done"))
            self.assertTrue(row["title"])
        self.assertIn("not the person's own words", after["note"])
        self.assertTrue(all(row["already_returned"] for row in again["agents"]))
        self.assertNotIn("implemented and tested", json.dumps(again))

    def test_the_wait_ends_when_the_last_report_arrives(self):
        caller = self._agent_pane()
        first, second = (self._split_agent(caller) for _ in range(2))
        self.assertEqual(self._report(first, "One done.").status_code, 200)
        self._fetch_task(second)
        # A report through the store rather than a second test client call,
        # so the request under test is the only one in flight.
        timer = threading.Timer(0.2, result_store.report, args=(second, "Two done."))
        timer.start()
        self.addCleanup(timer.cancel)

        started = time.monotonic()
        payload = self._wait(caller.session_id, wait="10").get_json()

        self.assertLess(time.monotonic() - started, 8)
        self.assertTrue(payload["complete"])
        self.assertFalse(payload["timed_out"])
        self.assertEqual(self._rows(payload)[second]["result"], "Two done.")

    def test_until_any_returns_with_the_first_report(self):
        caller = self._agent_pane()
        workers = [self._split_agent(caller) for _ in range(3)]
        self._report(workers[1], "Middle one first.")

        payload = self._wait(caller.session_id, wait="10", until="any").get_json()

        self.assertFalse(payload["timed_out"])
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["counts"][REPORTED], 1)
        self.assertEqual(self._rows(payload)[workers[1]]["result"], "Middle one first.")

    def test_a_wait_names_its_panes(self):
        caller = self._agent_pane()
        first, second = (self._split_agent(caller) for _ in range(2))
        self._report(first, "First.")

        payload = self._wait(caller.session_id, session_ids=f"{first},nobody").get_json()

        self.assertEqual(list(self._rows(payload)), [first])
        self.assertEqual(payload["unknown"], ["nobody"])
        self.assertTrue(payload["complete"])

    def test_splitting_a_neighbour_reports_to_the_agent_that_asked(self):
        group = self._group()
        caller = self._agent_pane(group)
        neighbour = self._pane(group)
        worker = self._split_agent(caller, source=neighbour)

        self._report(worker, "Done beside you.")

        self.assertEqual(self._rows(self._wait(caller.session_id).get_json())[worker]["result"], "Done beside you.")
        self.assertEqual(self._wait(neighbour.session_id).get_json()["agents"], [])

    def test_a_launch_with_tasks_is_waited_on_the_same_way(self):
        caller = self._agent_pane()
        panes = [
            {
                "title": f"codex {index}",
                "directory": self.temp.name,
                "startup_mode": "agent",
                "initial_command_mode": "agent",
                "initial_command": "codex",
                "agent_selection": "codex",
                "agent_mcp": True,
                "task": f"Part {index}.",
            }
            for index in (1, 2)
        ]
        with patch.object(api.socketio, "start_background_task"), patch.object(
            web_agents, "_agent_preflight_payload", return_value={"status": "present"}
        ):
            launched = self.client.post("/api/sessions", json={
                "connection_mode": "wsl",
                "origin_session_id": caller.session_id,
                "new_workspace": True,
                "workspace_label": "results",
                "session_name": "results",
                "layout": "vertical",
                "sessions": panes,
            })
        self.assertEqual(launched.status_code, 201, launched.get_json())
        workers = [row["session_id"] for row in launched.get_json()["sessions"]]
        for worker in workers:
            self._report(worker, f"{worker} reporting.")

        payload = self._wait(caller.session_id).get_json()

        self.assertTrue(payload["complete"])
        self.assertEqual([row["session_id"] for row in payload["agents"]], workers)


class ReportRouteTestCase(_ResultRouteCase):
    def test_an_unknown_pane_is_404_on_both_routes(self):
        self.assertEqual(self._report("nope", "x").status_code, 404)
        self.assertEqual(self._wait("nope").status_code, 404)

    def test_a_pane_nobody_handed_a_task_is_told_so(self):
        pane = self._agent_pane()

        response = self._report(pane.session_id, "Unsolicited.")

        self.assertEqual(response.status_code, 409)
        self.assertIn("No agent is waiting", response.get_json()["error"])

    def test_a_report_before_its_task_was_read_is_refused(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)

        response = self.client.post(f"/api/sessions/{worker}/handoff-report", json={"result": "Early."})

        self.assertEqual(response.status_code, 409)
        self.assertIn("read_handoff", response.get_json()["error"])
        self.assertEqual(self._wait(caller.session_id).get_json()["counts"][WORKING], 1)
        self.assertEqual(self._report(worker, "Read it, then did it.").status_code, 200)

    def test_a_refused_report_records_nothing(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)
        for body, expected in (
            ({"result": "a\x00b"}, "U+0000"),
            ({"result": "a" * (MAX_RESULT_CHARS + 1)}, "names the file"),
            ({"result": "fine", "status": "great"}, "'status'"),
            ({}, "must be text"),
        ):
            with self.subTest(expected):
                response = self.client.post(f"/api/sessions/{worker}/handoff-report", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected, response.get_json()["error"])
        self.assertEqual(self._wait(caller.session_id).get_json()["counts"][WORKING], 1)

    def test_bad_wait_arguments_are_400(self):
        pane = self._agent_pane()
        for params in ({"wait": "soon"}, {"wait": "-1"}, {"until": "most"}):
            with self.subTest(params):
                self.assertEqual(self._wait(pane.session_id, **params).status_code, 400)

    def test_a_report_is_never_published_on_a_pane_read(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)
        self._report(worker, "Secret-ish findings.")

        surfaces = (
            self.client.get("/api/sessions").get_json(),
            self.client.get(f"/api/sessions/{worker}").get_json(),
            self.client.get(f"/api/sessions/{caller.session_id}").get_json(),
        )

        self.assertNotIn("Secret-ish findings.", json.dumps(surfaces))


class PaneLifecycleTestCase(_ResultRouteCase):
    def test_a_worker_closed_before_it_reported_reads_ended(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)

        self.assertEqual(self.client.delete(f"/api/sessions/{worker}").status_code, 200)
        payload = self._wait(caller.session_id, wait="5").get_json()

        row = self._rows(payload)[worker]
        self.assertFalse(payload["timed_out"])
        self.assertEqual(row["state"], ENDED)
        self.assertIn("before it reported", row["reason"])
        self.assertFalse(row["pane_open"])

    def test_a_worker_closed_after_it_reported_keeps_its_report(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)
        self._report(worker, "Done; close me.")

        self.client.delete(f"/api/sessions/{worker}")
        row = self._rows(self._wait(caller.session_id).get_json())[worker]

        self.assertEqual((row["state"], row["result"], row["pane_open"]), (REPORTED, "Done; close me.", False))

    def test_an_announced_task_whose_connection_closes_reads_ended(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)
        pending = handoff_store.pending_for(worker)
        handoff_store.announce(pending.handoff_id, delivery=INLINE)

        web_terminal_io._shutdown_connection({"handoff_id": pending.handoff_id})

        row = self._rows(self._wait(caller.session_id).get_json())[worker]
        self.assertEqual(row["state"], ENDED)
        self.assertIn("connection closed", row["reason"])

    def test_the_requester_closing_drops_what_it_was_owed(self):
        caller = self._agent_pane()
        worker = self._split_agent(caller)

        self.client.delete(f"/api/sessions/{caller.session_id}")
        response = self._report(worker, "Anyone there?")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(result_store.count(), 0)


if __name__ == "__main__":
    unittest.main()
