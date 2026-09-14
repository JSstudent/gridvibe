"""The tool surface, and what each tool refuses before it reaches the wire.

Three things are pinned here, and each is a property of the build rather than
of a code path:

- **The registered surface is exactly nine.** The destroy tier is *absent from
  the build*, not flag-gated: a tool that does not exist cannot be talked into
  running by a file an agent reads. The test names those tools so that adding
  one has to be a deliberate edit here too.
- **Argument validation happens before any HTTP.** The refusals run against an
  opener that raises if it is ever opened, so a passing test means nothing left
  the process.
- **The launch request is the one the acceptance scenario states.** The pane
  family is chosen per pane, the group's `connection_mode` only separates local
  from SSH, and every created agent pane is stamped one level deeper.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import (  # noqa: E402
    CREATE_TOOLS,
    READ_TOOLS,
    dispatch,
    tool_names,
    tool_specs,
)
from gridvibe_mcp.windows import (  # noqa: E402
    BLOCKED,
    NO_WINDOW_AVAILABLE,
    OPENED,
    open_window,
)
from tests.test_mcp_client import StubOpener, client_for, http_error  # noqa: E402

#: Every tool this phase deliberately does not build. Naming them is the point:
#: an accidental re-addition has to fail a test that says why it is absent.
ABSENT_TOOLS = (
    "close_pane",
    "close_group",
    "close_workspace",
    "set_pane_mode",
    "set_pane_shell",
    "move_group",
    "send_input",
)

INSIDE_PANE = {
    "GRIDVIBE_URL": "http://127.0.0.1:5050",
    "GRIDVIBE_SESSION_ID": "pane-1",
    "GRIDVIBE_GROUP_ID": "group-1",
    "GRIDVIBE_WORKSPACE_ID": "ws-1",
    "GRIDVIBE_AGENT_DEPTH": "0",
}


class RefusingOpener:
    """Fails the test if a refusal ever reached the network."""

    def __init__(self, testcase):
        self.testcase = testcase

    def open(self, request, timeout=None):
        self.testcase.fail(f"a refusal reached the wire: {request.full_url}")


class ToolSurfaceTestCase(unittest.TestCase):
    def test_the_registered_surface_is_exactly_nine(self):
        names = tool_names()

        self.assertEqual(len(names), 9)
        self.assertEqual(names[:5], list(READ_TOOLS))
        self.assertEqual(names[5:], list(CREATE_TOOLS))

    def test_the_destroy_tier_is_absent_from_the_build(self):
        names = set(tool_names())

        for absent in ABSENT_TOOLS:
            with self.subTest(tool=absent):
                self.assertNotIn(absent, names)

    def test_no_tool_can_ask_gridvibe_to_forget_a_workspace(self):
        # `?forget=true` is never a tool argument in any phase.
        self.assertNotIn("forget", json.dumps(tool_specs()).lower())

    def test_every_tool_publishes_a_closed_schema(self):
        for spec in tool_specs():
            with self.subTest(tool=spec["name"]):
                self.assertTrue(spec["description"].strip())
                self.assertEqual(spec["inputSchema"]["type"], "object")
                # Closed, so a hallucinated argument is refused by the CLI
                # rather than silently carried into a request body.
                self.assertFalse(spec["inputSchema"]["additionalProperties"])

    def test_an_unknown_tool_is_reported_not_raised(self):
        result = dispatch(
            "close_workspace",
            {},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertIn("close_workspace", result["error"])
        self.assertEqual(result["kind"], "unknown_tool")


class ArgumentRefusalTestCase(unittest.TestCase):
    def refuse(self, name, arguments, environ=INSIDE_PANE):
        # `environ` defaults rather than falls back: an *empty* environment is
        # the agent-outside-GridVibe case, and `or` would quietly replace it.
        return dispatch(
            name,
            arguments,
            client=client_for(RefusingOpener(self)),
            identity=read_identity(environ),
        )

    def test_a_workspace_with_no_label_is_refused_before_any_http(self):
        result = self.refuse("create_workspace", {"label": "   "})

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_empty_pane_list_is_refused_before_any_http(self):
        result = self.refuse("launch_panes", {"panes": []})

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_pane_with_no_agent_is_refused_before_any_http(self):
        result = self.refuse("launch_panes", {"panes": [{"kind": "agent"}]})

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("agent", result["error"])

    def test_an_unknown_pane_kind_is_refused_before_any_http(self):
        result = self.refuse("launch_panes", {"panes": [{"kind": "database"}]})

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_outside_gridvibe_must_name_a_workspace(self):
        result = self.refuse(
            "launch_panes",
            {"panes": [{"kind": "terminal"}]},
            environ={},
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("workspace_id", result["error"])

    def test_open_window_needs_a_workspace(self):
        result = self.refuse("open_window", {"workspace_id": ""})

        self.assertEqual(result["kind"], "invalid_arguments")


class LaunchRequestTestCase(unittest.TestCase):
    """The acceptance scenario, as a request body."""

    def launch(self, arguments, environ=None, answer=None):
        opener = StubOpener([answer or {
            "workspace_id": "ws-2",
            "group_id": "g-2",
            "sessions": [],
        }])
        result = dispatch(
            "launch_panes",
            arguments,
            client=client_for(opener),
            identity=read_identity(environ or INSIDE_PANE),
        )
        return result, opener

    def test_the_acceptance_prompt_produces_the_stated_body(self):
        result, opener = self.launch({
            "new_workspace": True,
            "workspace_label": "test",
            "session_name": "test",
            "layout": "split",
            "panes": [
                {"kind": "agent", "title": "Claude 1", "directory": "C:/project",
                 "agent": "claude", "mcp": True},
                {"kind": "agent", "title": "Claude 2", "directory": "C:/project",
                 "agent": "claude", "mcp": True},
                {"kind": "explorer", "title": "Explorer", "directory": "C:/project"},
            ],
        })

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(body["new_workspace"])
        self.assertEqual(body["workspace_label"], "test")
        self.assertEqual(body["session_name"], "test")
        self.assertEqual(body["layout"], "split")
        # "wsl" is GridVibe's historical spelling of *local*, as opposed to
        # "ssh". It does not mean these panes run under WSL.
        self.assertEqual(body["connection_mode"], "wsl")
        self.assertEqual(len(body["sessions"]), 3)

        first = body["sessions"][0]
        self.assertEqual(first["startup_mode"], "agent")
        self.assertEqual(first["initial_command_mode"], "agent")
        self.assertEqual(first["initial_command"], "claude")
        self.assertEqual(first["agent_selection"], "claude")
        self.assertTrue(first["agent_mcp"])
        self.assertFalse(first["agent_auto_mode"])
        # The pane family is per pane, not per group.
        self.assertTrue(first["use_powershell"])
        self.assertFalse(first["use_wsl"])
        # Stamped one level deeper, so the refusal compounds.
        self.assertEqual(first["agent_depth"], 1)

        explorer = body["sessions"][2]
        self.assertEqual(explorer["startup_mode"], "explorer")
        self.assertEqual(explorer["explorer_root_directory"], "C:/project")
        self.assertTrue(explorer["explorer_root_configured"])
        # An explorer pane has no agent and therefore no depth to stamp.
        self.assertNotIn("agent_depth", explorer)

        self.assertEqual(result["workspace_id"], "ws-2")
        self.assertEqual(result["group_id"], "g-2")

    def test_an_unstated_workspace_means_the_one_this_agent_is_in(self):
        _result, opener = self.launch({"panes": [{"kind": "terminal"}]})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["workspace_id"], "ws-1")
        self.assertNotIn("new_workspace", body)

    def test_a_single_pane_launch_does_not_claim_a_split(self):
        _result, opener = self.launch({"panes": [{"kind": "terminal"}]})

        self.assertEqual(json.loads(opener.requests[0].data.decode("utf-8"))["layout"], "single")

    def test_a_shell_family_is_chosen_per_pane(self):
        _result, opener = self.launch({
            "panes": [
                {"kind": "terminal", "shell": "cmd"},
                {"kind": "terminal", "shell": "wsl"},
            ],
        })

        sessions = json.loads(opener.requests[0].data.decode("utf-8"))["sessions"]
        self.assertEqual(
            [(item["use_powershell"], item["use_wsl"]) for item in sessions],
            [(False, False), (False, True)],
        )

    def test_an_agent_at_the_limit_is_refused_with_the_depth_in_the_message(self):
        opener = StubOpener()
        result = dispatch(
            "launch_panes",
            {"panes": [{"kind": "agent", "agent": "claude"}]},
            client=client_for(opener),
            identity=read_identity({**INSIDE_PANE, "GRIDVIBE_AGENT_DEPTH": "2"}),
        )

        self.assertEqual(result["kind"], "depth_limit")
        self.assertEqual(result["agent_depth"], 2)
        self.assertIn("2", result["error"])
        # Refused here, so nothing was asked of GridVibe.
        self.assertEqual(opener.requests, [])

    def test_a_conflict_reaches_the_agent_as_gridvibes_own_sentence(self):
        sentence = 'A workspace named "test" is already open.'
        opener = StubOpener(raises=http_error(409, {"error": sentence}))
        result = dispatch(
            "launch_panes",
            {"new_workspace": True, "workspace_label": "test",
             "panes": [{"kind": "terminal"}]},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["error"], sentence)
        self.assertEqual(result["status"], 409)
        self.assertEqual(len(opener.requests), 1)


class WhoamiTestCase(unittest.TestCase):
    def test_whoami_resolves_this_directory_without_guessing(self):
        opener = StubOpener([{
            "session_id": "pane-1",
            "directory": "C:/project",
            "current_directory": "C:/project/src",
            "password": "gAAAAABsecret",
        }])

        result = dispatch(
            "whoami",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertTrue(result["inside_gridvibe"])
        self.assertEqual(result["session_id"], "pane-1")
        self.assertEqual(result["workspace_id"], "ws-1")
        # Where the pane is *now*, which is what "this directory" means.
        self.assertEqual(result["directory"], "C:/project/src")
        self.assertTrue(result["may_launch_panes"])
        self.assertNotIn("password", json.dumps(result))

    def test_an_agent_outside_gridvibe_gets_a_note_rather_than_a_pane(self):
        opener = StubOpener()

        result = dispatch("whoami", {}, client=client_for(opener), identity=read_identity({}))

        self.assertFalse(result["inside_gridvibe"])
        self.assertEqual(result["directory"], "")
        self.assertIn("not started by GridVibe", result["note"])
        # No pane to ask about, so nothing was asked.
        self.assertEqual(opener.requests, [])

    def test_an_agent_at_the_limit_is_told_before_it_tries(self):
        result = dispatch(
            "whoami",
            {},
            client=client_for(StubOpener([{"session_id": "pane-1"}])),
            identity=read_identity({**INSIDE_PANE, "GRIDVIBE_AGENT_DEPTH": "2"}),
        )

        self.assertFalse(result["may_launch_panes"])
        self.assertIn("2", result["launch_refusal"])


class WindowModeTestCase(unittest.TestCase):
    """The branch is read per call, never cached."""

    def test_browser_mode_opens_a_tab_with_no_gridvibe_page_open(self):
        opened = []
        client = client_for(StubOpener([{"window_mode": "browser"}]))

        result = open_window(
            client,
            "ws-2",
            "g-2",
            browser_opener=lambda url: opened.append(url) or True,
        )

        self.assertEqual(result["status"], OPENED)
        self.assertEqual(result["window_mode"], "browser")
        self.assertEqual(
            opened, ["http://127.0.0.1:5050/terminals?workspace=ws-2&group=g-2"]
        )

    def test_a_browser_that_refuses_is_reported_not_retried(self):
        attempts = []

        result = open_window(
            client_for(StubOpener([{"window_mode": "browser"}])),
            "ws-2",
            browser_opener=lambda url: attempts.append(url) or False,
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertEqual(len(attempts), 1)
        # The workspace exists either way, and that is the thing to say.
        self.assertIn("can be opened from the GridVibe launcher", result["detail"])

    def test_native_mode_waits_for_a_page_to_claim_the_intent(self):
        opener = StubOpener([
            # health, the POST that records the intent, then each poll.
            {"window_mode": "native"},
            {"intent_id": "i-1", "state": "pending", "expires_in": 15.0},
            {"intent_id": "i-1", "state": "pending"},
            {"intent_id": "i-1", "state": "claimed"},
            {"intent_id": "i-1", "state": "opened"},
        ])
        slept = []

        result = open_window(
            client_for(opener),
            "ws-2",
            "g-2",
            sleep=slept.append,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], OPENED)
        self.assertEqual(result["window_mode"], "native")
        self.assertEqual(result["intent_id"], "i-1")
        self.assertEqual(len(slept), 2)

    def test_native_mode_with_nothing_open_reports_no_window_available(self):
        opener = StubOpener([
            {"window_mode": "native"},
            {"intent_id": "i-1", "state": "pending", "expires_in": 15.0},
            {"intent_id": "i-1", "state": "expired"},
        ])

        result = open_window(
            client_for(opener),
            "ws-2",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], NO_WINDOW_AVAILABLE)
        self.assertIn("can be opened from the GridVibe launcher", result["detail"])

    def test_a_native_page_that_refused_is_reported_as_blocked(self):
        opener = StubOpener([
            {"window_mode": "native"},
            {"intent_id": "i-1", "state": "pending", "expires_in": 15.0},
            {"intent_id": "i-1", "state": "blocked", "detail": "The window refused."},
        ])

        result = open_window(
            client_for(opener),
            "ws-2",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("The window refused.", result["detail"])


if __name__ == "__main__":
    unittest.main()
