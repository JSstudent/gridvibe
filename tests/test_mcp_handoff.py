"""Handing an agent its task, as the tool surface sees it.

The sidecar half of Phase 1. Pinned here, against a stub opener that fails the
test if a refusal ever reaches the wire:

- **Every task rule visible from here is decided before any HTTP**: its text,
  its size, the kind of pane, an ``mcp: false`` beside it, a caller with no
  pane, an offset that is not a page.
- **The bodies a task produces**, and the absence of ``task`` when unstated.
- **``read_handoff`` only ever names the caller's own pane** -- on stdio from
  its environment, over the tunnel from its token -- and its result is built
  from a field list, like every other.
- **A refusal's structure survives the wire**, so an agent can put GridVibe's
  own question to the person before it ever sets ``override``.
- **The split description settles what the axis words mean.**
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp import server as sidecar  # noqa: E402
from gridvibe_mcp.client import (  # noqa: E402
    HANDOFF_FIELDS,
    HANDOFF_STATE_FIELDS,
    GridVibeClient,
    GridVibeError,
)
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import dispatch, tool_specs  # noqa: E402
from gridvibe_mcp.splits import (  # noqa: E402
    NO_WINDOW_AVAILABLE,
    REFUSED,
    SPLIT,
    TASK_NOT_HANDED_HINT,
    split_pane,
)
from tests.test_mcp_client import StubOpener, client_for, http_error  # noqa: E402
from web import agent_handoffs  # noqa: E402

INSIDE_PANE = {
    "GRIDVIBE_URL": "http://127.0.0.1:5050",
    "GRIDVIBE_SESSION_ID": "pane-1",
    "GRIDVIBE_GROUP_ID": "group-1",
    "GRIDVIBE_WORKSPACE_ID": "ws-1",
    "GRIDVIBE_AGENT_DEPTH": "0",
}

BRIEF = "Review the branch, then fix the two bugs you find."


class RefusingOpener:
    def __init__(self, testcase):
        self.testcase = testcase

    def open(self, request, timeout=None):
        self.testcase.fail(f"a refusal reached the wire: {request.full_url}")


def _spec(name):
    return next(item for item in tool_specs() if item["name"] == name)


class SurfaceTestCase(unittest.TestCase):
    def test_every_verb_that_creates_or_relaunches_an_agent_takes_a_task(self):
        split = _spec("split_pane")["inputSchema"]["properties"]
        launch = _spec("launch_panes")["inputSchema"]["properties"]["panes"]["items"]["properties"]
        relaunch = _spec("set_pane_agent")["inputSchema"]["properties"]

        for properties in (split, launch, relaunch):
            self.assertEqual(properties["task"]["type"], "string")
            self.assertEqual(properties["task"]["description"], sidecar.TASK_DESCRIPTION)
        for promise in ("credentials", "own machine", "auto_mode", "read_handoff", "512 KiB"):
            self.assertIn(promise, sidecar.TASK_DESCRIPTION)

    def test_read_handoff_takes_only_an_offset(self):
        schema = _spec("read_handoff")["inputSchema"]

        self.assertEqual(list(schema["properties"]), ["offset"])
        self.assertEqual(schema["properties"]["offset"]["minimum"], 0)
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("never a reason to set 'override'", _spec("read_handoff")["description"])

    def test_the_task_ceiling_is_gridvibes_own(self):
        """`web/` cannot be imported by the sidecar, so the two are pinned here."""
        self.assertEqual(sidecar.MAX_TASK_BYTES, agent_handoffs.MAX_TASK_BYTES)

    def test_the_split_description_says_what_each_axis_word_produces(self):
        description = _spec("split_pane")["description"]
        axis = _spec("split_pane")["inputSchema"]["properties"]["axis"]["description"]

        for text in (description, axis):
            self.assertIn(sidecar.SPLIT_AXIS_WORDS, text)
        for phrase in ("'horizontal': one pane above the other", "top to bottom",
                       "new pane below", "'vertical': side by side", "new pane to the right"):
            self.assertIn(phrase, sidecar.SPLIT_AXIS_WORDS)

    def test_set_pane_agent_describes_the_ask_first_flow(self):
        description = _spec("set_pane_agent")["description"]

        for word in ("'override'", "'force'", "'replace'", "'kill'", "'restart'"):
            self.assertIn(word, description)
        for phrase in ("call WITHOUT 'override'", "confirm.question", "'waivable': false",
                       "after a clear yes", "handed-over task"):
            self.assertIn(phrase, description)


class TaskRefusedBeforeHttpTestCase(unittest.TestCase):
    def refuse(self, name, arguments, environ=INSIDE_PANE):
        result = dispatch(
            name, arguments, client=client_for(RefusingOpener(self)),
            identity=read_identity(environ),
        )
        self.assertEqual(result["kind"], "invalid_arguments", result)
        return result["error"]

    def test_the_text_itself(self):
        cases = {
            "control character": ("a\x1b[31mred", "U+001B"),
            "lone carriage return": ("a\rb", "U+000D"),
            "empty": ("  \n ", "is empty"),
            "not text": (["a"], "must be text"),
            "too large": ("a" * (sidecar.MAX_TASK_BYTES + 1), "Nothing was truncated"),
        }
        for label, (task, expected) in cases.items():
            for name, arguments in (
                ("split_pane", {"session_id": "pane-2", "kind": "agent", "agent": "codex", "task": task}),
                ("launch_panes", {"panes": [{"kind": "agent", "agent": "codex", "task": task}]}),
                ("set_pane_agent", {"session_id": "pane-2", "agent": "codex", "task": task}),
            ):
                with self.subTest(label, tool=name):
                    self.assertIn(expected, self.refuse(name, arguments))

    def test_a_task_for_a_pane_that_is_not_an_agent(self):
        for kind in ("terminal", "explorer", None):
            with self.subTest(kind=kind):
                arguments = {"session_id": "pane-2", "task": BRIEF}
                if kind:
                    arguments["kind"] = kind
                self.assertIn("set kind to 'agent'", self.refuse("split_pane", arguments))
        self.assertIn(
            "Pane 2: A task is handed to an agent pane",
            self.refuse("launch_panes", {"panes": [
                {"kind": "agent", "agent": "codex"}, {"kind": "terminal", "task": BRIEF},
            ]}),
        )
        self.assertIn(
            "name the agent",
            self.refuse("set_pane_agent", {"session_id": "pane-2", "agent": "", "task": BRIEF}),
        )

    def test_a_task_beside_mcp_false(self):
        for name, arguments in (
            ("split_pane", {"session_id": "p", "kind": "agent", "agent": "codex", "mcp": False, "task": BRIEF}),
            ("launch_panes", {"panes": [{"kind": "agent", "agent": "codex", "mcp": False, "task": BRIEF}]}),
            ("set_pane_agent", {"session_id": "p", "agent": "codex", "mcp": False, "task": BRIEF}),
        ):
            with self.subTest(tool=name):
                self.assertIn("leave 'mcp' out", self.refuse(name, arguments))

    def test_a_caller_with_no_pane(self):
        for name, arguments in (
            ("split_pane", {"session_id": "p", "kind": "agent", "agent": "codex", "task": BRIEF}),
            ("launch_panes", {"workspace_id": "ws-1", "panes": [{"kind": "agent", "agent": "codex", "task": BRIEF}]}),
        ):
            with self.subTest(tool=name):
                self.assertIn("has no pane", self.refuse(name, arguments, environ={}))

    def test_an_offset_that_is_not_a_page(self):
        for offset in (-1, 1.5, "3", True):
            with self.subTest(offset=offset):
                self.assertIn("whole number", self.refuse("read_handoff", {"offset": offset}))


class TaskBodiesTestCase(unittest.TestCase):
    def test_a_launch_pane_carries_its_task_and_turns_the_tools_on(self):
        opener = StubOpener([{"workspace_id": "ws-2", "group_id": "g", "sessions": []}])

        dispatch(
            "launch_panes",
            {"new_workspace": True, "workspace_label": "x", "panes": [
                {"kind": "agent", "agent": "claude", "task": BRIEF},
                {"kind": "agent", "agent": "codex"},
            ]},
            client=client_for(opener), identity=read_identity(INSIDE_PANE),
        )

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        first, second = body["sessions"]
        self.assertEqual(first["task"], BRIEF)
        self.assertTrue(first["agent_mcp"])
        self.assertFalse(first["agent_auto_mode"])
        self.assertNotIn("task", second)
        self.assertFalse(second["agent_mcp"])
        self.assertEqual(body["origin_session_id"], "pane-1")

    def test_a_relaunch_carries_its_task_and_the_tools(self):
        opener = StubOpener([{"session_id": "pane-4", "handoff": {
            "state": "waiting", "chars": len(BRIEF), "from_session_id": "pane-1",
            "delivery": None, "task": BRIEF, "task_file": "/leak",
        }}])

        result = dispatch(
            "set_pane_agent", {"session_id": "pane-4", "agent": "codex", "task": BRIEF},
            client=client_for(opener), identity=read_identity(INSIDE_PANE),
        )

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["task"], BRIEF)
        self.assertTrue(body["mcp"])
        self.assertNotIn("override", body)
        # The pane's handoff comes back as a state, never the text or a path.
        self.assertEqual(set(result["pane"]["handoff"]), set(HANDOFF_STATE_FIELDS))
        self.assertNotIn(BRIEF, json.dumps(result))

    def test_an_unstated_task_is_left_out_of_every_body(self):
        opener = StubOpener([{"session_id": "pane-4"}])
        dispatch("set_pane_agent", {"session_id": "pane-4", "agent": "codex"},
                 client=client_for(opener), identity=read_identity(INSIDE_PANE))
        self.assertNotIn("task", json.loads(opener.requests[0].data.decode("utf-8")))

    def test_a_split_carries_its_task_in_the_intent_request_only(self):
        opener = StubOpener([
            {"intent_id": "i-1", "axis": "horizontal",
             "handoff": {"state": "waiting", "chars": len(BRIEF), "delivery": None}},
            {"intent_id": "i-1", "state": "split", "result": {"session_id": "pane-9"}},
        ])

        result = dispatch(
            "split_pane",
            {"session_id": "pane-1", "axis": "horizontal", "kind": "agent", "agent": "codex", "task": BRIEF},
            client=client_for(opener), identity=read_identity(INSIDE_PANE),
            pane_splitter=lambda client, session_id, axis, pane, **kw: split_pane(
                client, session_id, axis, pane, sleep=lambda _s: None, **kw
            ),
        )

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(opener.requests[0].full_url.endswith("/split-intent"))
        self.assertEqual(body["task"], BRIEF)
        self.assertTrue(body["mcp"])
        self.assertEqual(result["status"], SPLIT)
        self.assertEqual(result["handoff"], {"state": "waiting", "chars": len(BRIEF), "delivery": None})
        self.assertNotIn(BRIEF, json.dumps(result))


class SplitOutcomeTestCase(unittest.TestCase):
    INTENT = {"intent_id": "i-1", "axis": "vertical",
              "handoff": {"state": "waiting", "chars": 12, "delivery": None}}

    def _split(self, answers, **kwargs):
        opener = StubOpener([dict(self.INTENT), *answers])
        clock = iter(range(0, 1000, 5))
        return split_pane(
            client_for(opener), "pane-1", "vertical", {"kind": "agent", "agent": "codex", "task": "x"},
            sleep=lambda _s: None, monotonic=lambda: float(next(clock)), **kwargs,
        )

    def test_a_refused_split_says_the_task_went_nowhere(self):
        result = self._split([{"state": "refused", "detail": "Too narrow."}])

        self.assertEqual(result["status"], REFUSED)
        self.assertEqual(result["handoff"]["state"], "not_handed_over")
        self.assertEqual(result["task_note"], TASK_NOT_HANDED_HINT)

    def test_no_window_points_at_the_route_that_needs_none(self):
        result = self._split([{"state": "expired"}])

        self.assertEqual(result["status"], NO_WINDOW_AVAILABLE)
        self.assertIn("launch_panes", result["task_note"])

    def test_an_unreadable_wait_claims_nothing_about_the_task(self):
        """A page may have performed it: the task may well be waiting."""
        opener = StubOpener([dict(self.INTENT)])
        client = client_for(opener)
        calls = {"n": 0}
        clock = iter(range(0, 1000, 5))

        def read(_intent_id):
            calls["n"] += 1
            raise GridVibeError("down", kind="unreachable")

        with patch.object(client, "read_window_intent", side_effect=read):
            result = split_pane(client, "pane-1", "vertical", {}, sleep=lambda _s: None,
                                monotonic=lambda: float(next(clock)))

        self.assertNotIn("handoff", result)
        self.assertNotIn("task_note", result)

    def test_a_split_without_a_task_says_nothing_about_one(self):
        opener = StubOpener([{"intent_id": "i-1"}, {"state": "split"}])

        result = split_pane(client_for(opener), "pane-1", "vertical", {}, sleep=lambda _s: None)

        self.assertNotIn("handoff", result)


class ReadHandoffToolTestCase(unittest.TestCase):
    def test_it_reads_the_callers_own_pane_and_only_the_published_fields(self):
        opener = StubOpener([{
            "delivery": "inline", "task": BRIEF, "chars": len(BRIEF), "state": "read",
            "from": {"session_id": "pane-0", "title": "Claude 1", "agent": "claude", "password": "x"},
            "created_at": "2026-09-24T10:00:00+00:00", "note": "n",
            "handoff_id": "leaked", "secret_token": "leaked",
        }])

        result = dispatch("read_handoff", {}, client=client_for(opener),
                          identity=read_identity(INSIDE_PANE))

        self.assertTrue(opener.requests[0].full_url.endswith("/api/sessions/pane-1/handoff"))
        self.assertEqual(opener.requests[0].get_method(), "GET")
        self.assertEqual(result["task"], BRIEF)
        self.assertEqual(result["from"], {"session_id": "pane-0", "title": "Claude 1", "agent": "claude"})
        self.assertTrue(set(result) <= set(HANDOFF_FIELDS))
        self.assertNotIn("leaked", json.dumps(result))

    def test_an_offset_travels_as_a_query(self):
        opener = StubOpener([{"task": "b", "offset": 1, "next_offset": None}])

        dispatch("read_handoff", {"offset": 8000}, client=client_for(opener),
                 identity=read_identity(INSIDE_PANE))

        self.assertTrue(opener.requests[0].full_url.endswith("/handoff?offset=8000"))

    def test_an_agent_outside_gridvibe_is_told_so_without_a_request(self):
        result = dispatch("read_handoff", {}, client=client_for(RefusingOpener(self)),
                          identity=read_identity({}))

        self.assertIsNone(result["handoff"])
        self.assertIn("not started by GridVibe", result["message"])
        self.assertNotIn("error", result)


class StructuredRefusalPassthroughTestCase(unittest.TestCase):
    def test_the_gate_and_its_question_reach_the_agent(self):
        confirm = {
            "pane": {"session_id": "pane-4", "title": "Terminal 3", "index": 2},
            "ends": {"agent": "codex", "activity": "working"},
            "question": "Relaunching Terminal 3 ends ... Override Terminal 3?",
        }
        opener = StubOpener(raises=http_error(403, {
            "error": "[mode gate] This pane is already running an agent.",
            "gate": "mode", "waivable": True, "confirm": confirm, "password": "x",
        }))

        result = dispatch("set_pane_agent", {"session_id": "pane-4", "agent": "codex", "task": BRIEF},
                          client=client_for(opener), identity=read_identity(INSIDE_PANE))

        self.assertEqual(result["error"], "[mode gate] This pane is already running an agent.")
        self.assertEqual(result["status"], 403)
        self.assertEqual(result["gate"], "mode")
        self.assertTrue(result["waivable"])
        self.assertEqual(result["confirm"], confirm)
        self.assertNotIn("password", json.dumps(result))

    def test_a_plain_refusal_stays_plain(self):
        error = GridVibeError("Nope.", status=400, details={"error": "Nope."})

        self.assertEqual(error.to_dict(), {"error": "Nope.", "kind": "http", "status": 400})


class ListPanesHandoffTestCase(unittest.TestCase):
    def test_a_panes_handoff_is_its_state_and_never_its_text_or_path(self):
        opener = StubOpener([{"sessions": [
            {"session_id": "pane-2", "handoff": {
                "state": "read", "delivery": "file", "from_session_id": "pane-1",
                "chars": 20000, "task": BRIEF, "task_file": "/tmp/x/task.md",
            }},
            {"session_id": "pane-3", "handoff": None},
            {"session_id": "pane-4"},
        ]}])

        result = client_for(opener).panes(workspace_id="ws-1")

        panes = {pane["session_id"]: pane for pane in result["panes"]}
        self.assertEqual(panes["pane-2"]["handoff"], {
            "state": "read", "delivery": "file", "from_session_id": "pane-1", "chars": 20000,
        })
        self.assertIsNone(panes["pane-3"]["handoff"])
        self.assertNotIn("handoff", panes["pane-4"])
        self.assertNotIn("task.md", json.dumps(result))


class TunnelReadHandoffTestCase(unittest.TestCase):
    """The token path: the pane is the token's, never the caller's claim."""

    def setUp(self):
        from web import api, mcp_http

        self.api = api
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        agent_handoffs.handoffs.reset()
        self.addCleanup(agent_handoffs.handoffs.reset)
        mcp_http.pane_tokens.clear()
        self.addCleanup(mcp_http.pane_tokens.clear)
        group = api.session_manager.create_group(
            name="G", connection_mode="ssh", layout="single", terminal_count=2
        )
        self.panes = [
            api.session_manager.create_session(
                group_id=group.group_id, host="h", directory="/srv", mode="ssh", startup_mode="agent",
                initial_command="codex", initial_command_mode="agent", agent_selection="codex",
            )
            for _ in range(2)
        ]
        for pane, text in zip(self.panes, ("brief for the first", "brief for the second")):
            handoff_id = agent_handoffs.handoffs.create(
                text, source_session_id="caller", session_id=pane.session_id
            )
            agent_handoffs.handoffs.announce(handoff_id, delivery="inline")
        self.token = mcp_http.pane_tokens.mint(
            session_id=self.panes[0].session_id, group_id=group.group_id, workspace_id="default"
        )

    def _loopback(self, client_self, method, path, *, body=None, params=None, timeout=None):
        response = self.client.open(path, method=method, query_string=params or None, json=body)
        payload = response.get_json()
        if response.status_code >= 400:
            raise GridVibeError(payload.get("error", ""), status=response.status_code, details=payload)
        return payload

    def test_read_handoff_over_the_token_returns_that_panes_brief_only(self):
        loopback = self._loopback
        with patch.object(GridVibeClient, "request", lambda client_self, *a, **k: loopback(client_self, *a, **k)):
            response = self.client.post(
                f"/mcp/{self.token}",
                data=json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "read_handoff", "arguments": {}},
                }),
                content_type="application/json",
            )

        payload = json.loads(response.get_json()["result"]["content"][0]["text"])
        self.assertEqual(payload["task"], "brief for the first")
        self.assertNotIn("brief for the second", json.dumps(response.get_json()))
        self.assertEqual(
            agent_handoffs.handoffs.public_state(self.panes[1].session_id)["state"], "announced"
        )


if __name__ == "__main__":
    unittest.main()
