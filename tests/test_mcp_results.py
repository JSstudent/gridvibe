"""Handing results back, as the tool surface sees it.

Pinned against a stub opener that fails the test if a refusal ever reaches
the wire:

- **Every report rule visible from here is decided before any HTTP**: its text,
  its size, its status, a caller with no pane; and so is every wait argument.
- **Neither tool names another pane.** ``report_result`` posts to the caller's
  own pane and GridVibe decides who receives it; ``wait_for_results`` reads only
  what is owed to the caller's own pane.
- **Results are built from field lists**, like every other.
- **A tunnelled wait blocks on the store in-process**, and asks the route with
  ``wait=0`` -- never a request held open against the server it runs in.
- **The sidecar's ceilings equal GridVibe's**, since ``web/`` cannot be imported
  from the sidecar.
"""

import json
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp import server as sidecar  # noqa: E402
from gridvibe_mcp.client import (  # noqa: E402
    HANDOFF_FIELDS,
    RESULTS_WAIT_MARGIN_SECONDS,
)
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import dispatch, tool_specs  # noqa: E402
from tests.test_mcp_client import StubOpener, client_for, http_error  # noqa: E402
from web import agent_results, mcp_http  # noqa: E402

INSIDE_PANE = {
    "GRIDVIBE_URL": "http://127.0.0.1:5050",
    "GRIDVIBE_SESSION_ID": "pane-1",
    "GRIDVIBE_GROUP_ID": "group-1",
    "GRIDVIBE_WORKSPACE_ID": "ws-1",
    "GRIDVIBE_AGENT_DEPTH": "1",
}


class RefusingOpener:
    def __init__(self, testcase):
        self.testcase = testcase

    def open(self, request, timeout=None):
        self.testcase.fail(f"a refusal reached the wire: {request.full_url}")


def _spec(name):
    return next(item for item in tool_specs() if item["name"] == name)


def _query(request):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.full_url).query))


class SurfaceTestCase(unittest.TestCase):
    def test_the_sidecar_ceilings_equal_gridvibes(self):
        self.assertEqual(sidecar.MAX_RESULT_CHARS, agent_results.MAX_RESULT_CHARS)
        self.assertEqual(sidecar.RESULTS_MAX_WAIT_SECONDS, agent_results.MAX_WAIT_SECONDS)
        self.assertEqual(sidecar.RESULTS_DEFAULT_WAIT_SECONDS, agent_results.DEFAULT_WAIT_SECONDS)
        self.assertEqual(sidecar.REPORT_STATUSES, agent_results.REPORT_STATUSES)
        self.assertEqual(sidecar.RESULTS_UNTIL, agent_results.UNTIL_CHOICES)
        self.assertEqual(
            _spec("wait_for_results")["inputSchema"]["properties"]["wait_seconds"]["maximum"],
            agent_results.MAX_WAIT_SECONDS,
        )

    def test_neither_tool_takes_a_pane_to_write_to(self):
        report = _spec("report_result")["inputSchema"]
        wait = _spec("wait_for_results")["inputSchema"]

        self.assertEqual(set(report["properties"]), {"result", "status"})
        self.assertEqual(report["required"], ["result"])
        self.assertEqual(set(wait["properties"]) & {"session_id", "session_ids"}, set())
        self.assertIn("you do not name a pane", _spec("report_result")["description"])

    def test_the_descriptions_say_how_to_keep_waiting_and_what_a_report_is(self):
        wait = _spec("wait_for_results")["description"]

        self.assertIn("call it again", wait)
        self.assertIn("'ended'", wait)
        self.assertIn("not the person's", wait)
        self.assertIn("read_handoff", _spec("report_result")["description"])
        self.assertIn("credentials", _spec("report_result")["description"])

    def test_every_task_description_points_at_the_way_back(self):
        for promise in ("report_result", "wait_for_results"):
            self.assertIn(promise, sidecar.TASK_DESCRIPTION)

    def test_the_brief_carries_its_reply_instructions_through(self):
        self.assertIn("reply", HANDOFF_FIELDS)


class ReportRefusalTestCase(unittest.TestCase):
    def _dispatch(self, arguments, environ=INSIDE_PANE):
        return dispatch(
            "report_result",
            arguments,
            client=client_for(RefusingOpener(self)),
            identity=read_identity(environ),
        )

    def test_every_rule_visible_here_is_refused_before_any_http(self):
        cases = {
            "missing": ({}, "needs a 'result'"),
            "not text": ({"result": 5}, "must be text"),
            "empty": ({"result": "  "}, "is empty"),
            "control": ({"result": "a\x07b"}, "U+0007"),
            "too long": ({"result": "a" * (sidecar.MAX_RESULT_CHARS + 1)}, "names the file"),
            "status": ({"result": "ok", "status": "great"}, "'status' must be one of"),
        }
        for label, (arguments, expected) in cases.items():
            with self.subTest(label):
                result = self._dispatch(arguments)
                self.assertEqual(result["kind"], "invalid_arguments")
                self.assertIn(expected, result["error"])

    def test_an_agent_outside_gridvibe_has_nobody_to_report_to(self):
        result = self._dispatch({"result": "Done."}, environ={})

        self.assertIn("nobody to report to", result["error"])


class WaitRefusalTestCase(unittest.TestCase):
    def _dispatch(self, arguments, environ=INSIDE_PANE):
        return dispatch(
            "wait_for_results",
            arguments,
            client=client_for(RefusingOpener(self)),
            identity=read_identity(environ),
        )

    def test_bad_arguments_are_refused_before_any_http(self):
        for arguments in (
            {"pane_ids": "pane-2"},
            {"pane_ids": [2]},
            {"until": "most"},
            {"wait_seconds": -1},
            {"wait_seconds": True},
            {"wait_seconds": "soon"},
            {"include_collected": "yes"},
        ):
            with self.subTest(arguments):
                self.assertEqual(self._dispatch(arguments)["kind"], "invalid_arguments")

    def test_an_agent_outside_gridvibe_is_told_it_handed_nothing_out(self):
        result = self._dispatch({}, environ={})

        self.assertEqual(result["agents"], [])
        self.assertIn("cannot have handed a task", result["message"])


class WireTestCase(unittest.TestCase):
    def test_a_report_posts_to_the_callers_own_pane_and_is_projected(self):
        opener = StubOpener([{
            "recorded": True,
            "revision": 1,
            "status": "failed",
            "chars": 6,
            "replaced_earlier_report": False,
            "reported_to": {"session_id": "pane-0", "title": "Claude 1", "password": "x"},
            "requester_session_id": "pane-0",
        }])

        result = dispatch(
            "report_result",
            {"result": "Broke.\r\n", "status": "FAILED"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/api/sessions/pane-1/handoff-report"))
        self.assertEqual(
            json.loads(request.data),
            {"result": "Broke.\n", "status": "failed"},
        )
        # A pane leaves as a pane: GridVibe's route calls it a session.
        self.assertEqual(result["reported_to"], {"pane_id": "pane-0", "title": "Claude 1"})
        self.assertNotIn("requester_session_id", result)

    def test_a_route_refusal_is_gridvibes_own_sentence(self):
        opener = StubOpener(raises=http_error(409, {"error": agent_results.NOBODY_WAITING_MESSAGE}))

        result = dispatch(
            "report_result",
            {"result": "Done."},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["error"], agent_results.NOBODY_WAITING_MESSAGE)
        self.assertEqual(result["status"], 409)

    def test_a_wait_reads_the_callers_own_pane_and_is_given_its_wait_plus_a_margin(self):
        opener = StubOpener([{
            "complete": False,
            "timed_out": True,
            "counts": {"working": 1, "reported": 1, "ended": 0, "extra": 9},
            "agents": [
                {"session_id": "pane-2", "state": "reported", "result": "Found it.", "password": "x",
                 "handoff_id": "h"},
                {"session_id": "pane-3", "state": "working", "task_state": "read"},
            ],
            "instructions": "Call again.",
            "requester_session_id": "pane-1",
        }])

        result = dispatch(
            "wait_for_results",
            {"pane_ids": ["pane-2", "pane-3"], "until": "any", "wait_seconds": 30,
             "include_collected": True},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        request = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("/api/sessions/pane-1/handoff-reports?", request.full_url)
        self.assertEqual(
            _query(request),
            {"wait": "30", "until": "any", "session_ids": "pane-2,pane-3", "include_collected": "1"},
        )
        self.assertEqual(opener.timeouts[0], 30 + RESULTS_WAIT_MARGIN_SECONDS)
        self.assertEqual(result["agents"][0], {"pane_id": "pane-2", "state": "reported", "result": "Found it."})
        self.assertEqual(result["counts"], {"working": 1, "reported": 1, "ended": 0})
        self.assertNotIn("requester_session_id", result)

    def test_a_wait_above_the_ceiling_is_clamped_not_refused(self):
        opener = StubOpener([{"agents": []}])

        dispatch(
            "wait_for_results",
            {"wait_seconds": 600},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(_query(opener.requests[0])["wait"], "55")
        self.assertEqual(_query(opener.requests[0])["until"], "all")


class TunnelledWaitTestCase(unittest.TestCase):
    def test_the_wait_blocks_on_the_store_and_the_route_is_asked_with_wait_zero(self):
        opener = StubOpener([{"complete": True, "timed_out": False, "waited_seconds": 0.0, "agents": []}])
        client = mcp_http._in_process_client_type()("http://127.0.0.1:5050", opener=opener)

        with patch.object(mcp_http.agent_results, "wait_until_settled", return_value=True) as waited:
            result = client.wait_for_results("pane-1", ["pane-2"], until="any", wait_seconds=30)

        waited.assert_called_once_with("pane-1", ["pane-2"], until="any", timeout=30)
        self.assertEqual(_query(opener.requests[0])["wait"], "0")
        self.assertLess(opener.timeouts[0], 30)
        self.assertIn("waited_seconds", result)


if __name__ == "__main__":
    unittest.main()
