"""The tool surface, and what each tool refuses before it reaches the wire.

Four things are pinned here, and each is a property of the build rather than
of a code path:

- **The registered surface is exactly sixteen**, and the tiers that reach an
  existing pane hold exactly three -- two that replace what it is, one that
  erases what it has drawn. The destroy tier is *absent from the build*, not
  flag-gated: a tool that does not exist cannot be talked into running by a
  file an agent reads. The test names those tools so that adding one has to be
  a deliberate edit here too.
- **Argument validation happens before any HTTP.** The refusals run against an
  opener that raises if it is ever opened, so a passing test means nothing left
  the process.
- **The launch request is the one the acceptance scenario states.** The pane
  family is chosen per pane, the group's `connection_mode` only separates local
  from SSH, and every created agent pane is stamped one level deeper.
- **Position is only published where it means something**, and a refusal is
  GridVibe's own sentence rather than a retry.
"""

import json
import sys
import unittest
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp import splits as splits_module  # noqa: E402
from gridvibe_mcp import windows as windows_module  # noqa: E402
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import (  # noqa: E402
    CREATE_TOOLS,
    DISPLAY_TOOLS,
    HANDBACK_TOOLS,
    LAYOUTS,
    PANE_MODES,
    READ_TOOLS,
    RELAUNCH_TOOLS,
    dispatch,
    tool_names,
    tool_specs,
)
from gridvibe_mcp.splits import NO_WINDOW_AVAILABLE as SPLIT_NO_WINDOW  # noqa: E402
from gridvibe_mcp.splits import REFUSED, SPLIT, split_pane  # noqa: E402
from gridvibe_mcp.windows import (  # noqa: E402
    BLOCKED,
    NO_WINDOW_AVAILABLE,
    OPENED,
    open_window,
)
from tests.test_mcp_client import StubOpener, client_for, http_error  # noqa: E402
from web.window_intents import CLAIM_TTL_SECONDS, INTENT_TTL_SECONDS  # noqa: E402

#: Every tool this phase deliberately does not build. Naming them is the point:
#: an accidental re-addition has to fail a test that says why it is absent.
ABSENT_TOOLS = (
    "close_pane",
    "close_group",
    "close_workspace",
    "set_pane_shell",
    "move_group",
    # `clear_pane` is not this one wearing a different name: the only bytes it
    # puts on a shell's stdin are GridVibe's own clear command, chosen by the
    # window that knows the pane's shell family, and no tool argument reaches
    # them.
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
    def test_the_registered_surface_is_exactly_sixteen(self):
        """Seven read, two hand back, four create, two replace, one erases.

        The last two tiers are the only things in this surface that end
        anything, and what bounds them is the gates on GridVibe's own routes
        rather than the tools themselves. `read_handoff` is a read: its only
        side effect is a handoff's state becoming `read`. The hand-back pair
        neither creates nor ends a pane: a report goes to whoever GridVibe
        recorded as asking, and a wait reads only what is owed to the caller.
        """
        names = tool_names()

        self.assertEqual(len(names), 16)
        self.assertEqual(names[:7], list(READ_TOOLS))
        self.assertEqual(READ_TOOLS[-1], "read_handoff")
        self.assertEqual(names[7:9], list(HANDBACK_TOOLS))
        self.assertEqual(names[9:13], list(CREATE_TOOLS))
        self.assertEqual(names[13:15], list(RELAUNCH_TOOLS))
        self.assertEqual(names[15:], list(DISPLAY_TOOLS))

    def test_the_layout_enum_is_the_set_gridvibe_actually_accepts(self):
        """`stack` was never a GridVibe layout, and the two that are were
        missing -- so a two-pane launch could not ask to be stacked."""
        self.assertEqual(
            LAYOUTS, ("single", "vertical", "horizontal", "split", "grid")
        )
        self.assertNotIn("stack", LAYOUTS)

    def test_the_layout_description_says_where_the_name_is_honoured(self):
        """The enum offers five values and no pane count accepts all five.

        It used to describe only the case it does not cover -- `grid` forced at
        four or more -- and stay silent on the two it does: a three-pane launch
        asking for `grid` is rewritten to `vertical`, and so is the two-pane
        default `build_launch_request` itself sends. Silently, because the
        server owns that table; a reader has to be told rather than shown.
        """
        spec = next(
            item for item in tool_specs() if item["name"] == "launch_panes"
        )
        description = spec["inputSchema"]["properties"]["layout"]["description"]

        self.assertIn("two panes", description)
        self.assertIn("three", description)
        self.assertIn("'single'", description)
        self.assertIn("'grid'", description)
        # And that the rewrite is silent, which is the part an agent would
        # otherwise discover by comparing what it asked for with list_panes.
        self.assertIn("rewrites silently", description)

    def test_the_relaunch_tool_says_what_it_ends_and_what_gates_it(self):
        """A description that only said what it does would read as safe."""
        spec = next(
            item for item in tool_specs() if item["name"] == "set_pane_agent"
        )

        self.assertIn("ENDS", spec["description"])
        for gate in ("plain terminal", "created", "own pane"):
            self.assertIn(gate, spec["description"])

    def test_the_mode_tool_says_what_it_ends_and_what_gates_it(self):
        """A pane switched out of terminal mode loses the shell behind it."""
        spec = next(
            item for item in tool_specs() if item["name"] == "set_pane_mode"
        )

        self.assertIn("ENDS", spec["description"])
        for gate in ("created", "own pane", "already running an agent"):
            self.assertIn(gate, spec["description"])
        # The one dimension it deliberately does not own, named so an agent
        # asked for "a plain PowerShell terminal" reaches the right tool.
        self.assertIn("set_pane_agent", spec["description"])
        self.assertEqual(
            spec["inputSchema"]["properties"]["mode"]["enum"], list(PANE_MODES)
        )

    def test_the_clear_tool_says_the_scrollback_is_unrecoverable(self):
        """The thing it destroys cannot be read back, so it has to say so."""
        spec = next(
            item for item in tool_specs() if item["name"] == "clear_pane"
        )

        self.assertIn("GONE", spec["description"])
        for gate in ("created", "own pane", "terminal pane"):
            self.assertIn(gate, spec["description"])
        # Not the absent `send_input` under another name.
        self.assertIn("not a way to type into a terminal", spec["description"])

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
        """Dispatch one launch. The POST is the only request it ever makes."""
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

        body = json.loads(opener.requests[-1].data.decode("utf-8"))
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
        # The pane family is per pane, not per group -- and these panes named
        # none, so the body states none. It used to state PowerShell, which was
        # a choice nobody made being saved into the preset.
        self.assertNotIn("use_powershell", first)
        self.assertNotIn("use_wsl", first)
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

    def test_an_unstated_workspace_is_left_for_gridvibe_to_resolve(self):
        """"Here" is a question only GridVibe can answer without a race.

        The sidecar used to read its own group's workspace and name the answer
        in the launch that followed -- two requests, with a move possible
        between them, so the panes could open in the workspace the group had
        just left. The body now states the pane instead, and GridVibe resolves
        the destination inside the launch itself.
        """
        _result, opener = self.launch({"panes": [{"kind": "terminal"}]})

        body = json.loads(opener.requests[-1].data.decode("utf-8"))
        self.assertNotIn("workspace_id", body)
        self.assertNotIn("new_workspace", body)
        self.assertEqual(body["origin_session_id"], "pane-1")

    def test_a_launch_asks_gridvibe_exactly_once(self):
        """Whether or not the caller named a workspace: nothing is read first,
        so there is no window between what was read and what was launched."""
        for arguments in (
            {"panes": [{"kind": "terminal"}]},
            {"workspace_id": "ws-9", "panes": [{"kind": "terminal"}]},
        ):
            with self.subTest(workspace=arguments.get("workspace_id") or "unstated"):
                _result, opener = self.launch(arguments)

                self.assertEqual(len(opener.requests), 1)
                self.assertTrue(
                    opener.requests[0].full_url.endswith("/api/sessions")
                )

    def test_a_named_workspace_is_still_the_one_that_is_sent(self):
        _result, opener = self.launch(
            {"workspace_id": "ws-9", "panes": [{"kind": "terminal"}]}
        )

        body = json.loads(opener.requests[-1].data.decode("utf-8"))
        self.assertEqual(body["workspace_id"], "ws-9")

    def test_an_agent_with_no_pane_must_name_a_destination(self):
        """Outside GridVibe there is no pane to be "here", and nothing to
        inherit -- so the refusal happens before anything is asked."""
        opener = StubOpener()
        result = dispatch(
            "launch_panes",
            {"panes": [{"kind": "terminal"}]},
            client=client_for(opener),
            identity=read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050"}),
        )

        self.assertIn("workspace_id", result["error"])
        self.assertEqual(opener.requests, [])

    def test_a_single_pane_launch_does_not_claim_a_split(self):
        _result, opener = self.launch({"panes": [{"kind": "terminal"}]})

        self.assertEqual(json.loads(opener.requests[-1].data.decode("utf-8"))["layout"], "single")

    def test_an_unstated_shell_is_left_to_gridvibe(self):
        """An omitted `shell` is not a choice of PowerShell.

        The two keys used to be written unconditionally off a default, so every
        tool-launched pane arrived as a *stated* PowerShell pane and was saved
        as one -- in a workspace whose other panes the user runs as cmd, with
        nothing having asked. Same rule an omitted `kind` already followed on a
        split: say nothing and GridVibe's own default applies.
        """
        _result, opener = self.launch({
            "panes": [{"kind": "terminal"}, {"kind": "agent", "agent": "claude"}],
        })

        for session in json.loads(opener.requests[-1].data.decode("utf-8"))["sessions"]:
            with self.subTest(startup_mode=session["startup_mode"]):
                self.assertNotIn("use_powershell", session)
                self.assertNotIn("use_wsl", session)

    def test_a_shell_family_is_chosen_per_pane(self):
        _result, opener = self.launch({
            "panes": [
                {"kind": "terminal", "shell": "cmd"},
                {"kind": "terminal", "shell": "wsl"},
            ],
        })

        sessions = json.loads(opener.requests[-1].data.decode("utf-8"))["sessions"]
        self.assertEqual(
            [(item["use_powershell"], item["use_wsl"]) for item in sessions],
            [(False, False), (False, True)],
        )

    def test_a_stated_powershell_pane_still_says_so(self):
        """Silence is the only thing that changed; a choice is still carried."""
        _result, opener = self.launch(
            {"panes": [{"kind": "terminal", "shell": "powershell"}]}
        )

        session = json.loads(opener.requests[-1].data.decode("utf-8"))["sessions"][0]
        self.assertTrue(session["use_powershell"])
        self.assertFalse(session["use_wsl"])

    def test_a_launch_from_inside_a_pane_names_the_pane_it_came_from(self):
        """Where, not what -- GridVibe reads the connection, the agent cannot."""
        _result, opener = self.launch({"panes": [{"kind": "terminal"}]})

        body = json.loads(opener.requests[-1].data.decode("utf-8"))
        self.assertEqual(body["origin_session_id"], "pane-1")
        # And nothing resembling a credential travelled with it: an agent is
        # never shown its own pane's password, so it cannot state one.
        self.assertNotIn("password", json.dumps(body))

    def test_an_agent_with_no_pane_names_no_origin_and_falls_back_to_local(self):
        _result, opener = self.launch(
            {"workspace_id": "ws-9", "panes": [{"kind": "terminal"}]},
            environ={"GRIDVIBE_URL": "http://127.0.0.1:5050"},
        )

        body = json.loads(opener.requests[-1].data.decode("utf-8"))
        self.assertNotIn("origin_session_id", body)
        self.assertEqual(body["connection_mode"], "wsl")

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

    def test_whoami_says_which_machine_that_directory_is_on(self):
        """The reading an agent on an SSH pane used to have to guess at."""
        opener = StubOpener([{
            "session_id": "pane-1",
            "host": "saso-workstation",
            "mode": "ssh",
            "current_directory": "/home/ubuntu/4g_core_workspace",
        }])

        result = dispatch(
            "whoami",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["directory"], "/home/ubuntu/4g_core_workspace")
        self.assertEqual(result["host"], "saso-workstation")
        self.assertEqual(result["runs_on"], "remote_host")
        self.assertIn("saso-workstation", result["note"])

    def test_a_pane_on_this_machine_says_so_and_adds_no_note(self):
        opener = StubOpener([{
            "session_id": "pane-1",
            "host": "PowerShell",
            "mode": "wsl",
            "current_directory": "C:/project",
        }])

        result = dispatch(
            "whoami",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["runs_on"], "gridvibe_host")
        self.assertNotIn("note", result)

    def test_a_pane_that_could_not_be_read_states_no_machine_at_all(self):
        """A failed read knows nothing, and guessing local is the old defect."""
        result = dispatch(
            "whoami",
            {},
            client=client_for(StubOpener(raises=http_error(404, {"error": "gone"}))),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertIsNone(result["pane"])
        self.assertNotIn("runs_on", result)
        self.assertNotIn("host", result)

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
        # ...and told, in the same answer, what it may still do. The budget
        # bounds agents, so only a split that *starts* one costs it -- an agent
        # reading `may_launch_panes: false` alone concluded it could create
        # nothing, which is not what `split_pane` does at the limit.
        self.assertTrue(result["may_split_panes"])
        self.assertIn("plain split", result["split_note"])

    def test_a_pane_under_the_limit_says_it_may_do_both(self):
        result = dispatch(
            "whoami",
            {},
            client=client_for(StubOpener([{"session_id": "pane-1"}])),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertTrue(result["may_launch_panes"])
        self.assertTrue(result["may_split_panes"])
        # No refusal to explain, so no note explaining one.
        self.assertNotIn("split_note", result)
        self.assertNotIn("launch_refusal", result)


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


INSIDE_PANE_WITH_GROUP = INSIDE_PANE


def _layout_payload(session_ids, layout="split", advisory=False):
    """What `GET /api/panes/layout` answers for a group, as the sidecar sees it."""
    return {
        "group_id": "group-1",
        "workspace_id": "ws-1",
        "layout": layout,
        "layout_advisory": advisory,
        "terminal_count": len(session_ids),
        "geometry": {
            "class_name": "layout-3-split",
            "implied": True,
            "columns": 2,
            "rows": 2,
            "column_weights": [2.0, 1.0],
            "row_weights": [1.0, 1.0],
            "split_slot_rects": [
                {"originSlot": 0, "x": 1, "y": 1, "w": 1, "h": 1},
                {"originSlot": 1, "x": 1, "y": 2, "w": 1, "h": 1},
                {"originSlot": 2, "x": 2, "y": 1, "w": 1, "h": 2},
            ][: len(session_ids)],
        },
        "panes": [
            {
                "session_id": session_id,
                "index": index,
                "rect": {"x": 1, "y": index + 1, "w": 1, "h": 1},
                "relative_area": 0.3333,
                "neighbours": {
                    "above": session_ids[index - 1: index] if index else [],
                    "below": session_ids[index + 1: index + 2],
                    "left": [],
                    "right": [],
                },
            }
            for index, session_id in enumerate(session_ids)
        ],
    }


class PanePositionTestCase(unittest.TestCase):
    """What a pane can say about where it is, and when it refuses to say."""

    def test_list_panes_carries_a_position_and_a_layout_block(self):
        opener = StubOpener([
            _layout_payload(["pane-1", "pane-2"]),
            {"sessions": [
                {"session_id": "pane-1", "group_id": "group-1", "title": "Terminal 1"},
                {"session_id": "pane-2", "group_id": "group-1", "title": "Terminal 2"},
            ]},
        ])

        result = dispatch(
            "list_panes",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["panes"][0]["index"], 0)
        self.assertEqual(result["panes"][0]["rect"], {"x": 1, "y": 1, "w": 1, "h": 1})
        self.assertEqual(result["panes"][0]["relative_area"], 0.3333)
        self.assertEqual(result["panes"][0]["neighbours"]["below"], ["pane-2"])
        self.assertEqual(result["layout"]["layout"], "split")
        self.assertFalse(result["layout"]["layout_advisory"])
        self.assertFalse(result["layout"]["geometry"]["implied"] is None)

    def test_the_arrangement_read_is_the_callers_own_group_by_default(self):
        """"What is around me" is a question about the panes beside this one."""
        opener = StubOpener([
            _layout_payload(["pane-1"]),
            {"sessions": [{"session_id": "pane-1", "group_id": "group-1"}]},
        ])

        dispatch(
            "list_panes",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertIn("group_id=group-1", opener.requests[0].full_url)

    def test_a_pane_outside_the_arranged_group_states_index_none(self):
        """Array position across groups means nothing, so no number is given."""
        opener = StubOpener([
            _layout_payload(["pane-1"]),
            {"sessions": [
                {"session_id": "pane-1", "group_id": "group-1"},
                {"session_id": "pane-9", "group_id": "group-2"},
            ]},
        ])

        result = dispatch(
            "list_panes",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        stranger = result["panes"][1]
        self.assertIsNone(stranger["index"])
        self.assertNotIn("rect", stranger)
        self.assertNotIn("neighbours", stranger)

    def test_a_workspace_that_is_not_the_callers_own_gets_no_layout_block(self):
        """The arrangement read is the caller's group even here, so it is not
        an answer *about this workspace* and is not published as one.

        Naming a workspace and no group still resolves the caller's own group,
        which is right when that is where the caller is and meaningless when it
        is not: every pane listed comes back `index: None`, and the layout block
        would have described a group that is not in the workspace being listed.
        `LAYOUT_FIELDS` names the group, so a careful reader could have told --
        and a reader who read `layout.layout` as "how this workspace's group is
        arranged" could not.
        """
        opener = StubOpener([
            _layout_payload(["pane-1"]),
            {"sessions": [
                {"session_id": "pane-7", "group_id": "group-2"},
                {"session_id": "pane-8", "group_id": "group-2"},
            ]},
        ])

        result = dispatch(
            "list_panes",
            {"workspace_id": "ws-9"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["count"], 2)
        self.assertEqual([pane["index"] for pane in result["panes"]], [None, None])
        self.assertNotIn("layout", result)

    def test_the_block_survives_a_stranger_beside_a_pane_that_did_match(self):
        """One matched pane is enough: the block describes something here."""
        opener = StubOpener([
            _layout_payload(["pane-1"]),
            {"sessions": [
                {"session_id": "pane-1", "group_id": "group-1"},
                {"session_id": "pane-9", "group_id": "group-2"},
            ]},
        ])

        result = dispatch(
            "list_panes",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["layout"]["group_id"], "group-1")
        self.assertEqual(result["panes"][0]["index"], 0)
        self.assertIsNone(result["panes"][1]["index"])

    def test_a_moved_group_lists_the_workspace_it_is_in_now(self):
        """The read half of the same staleness the launch half had.

        A group moved to another workspace keeps its panes running, so an agent
        launched before the move went on listing the workspace it had left --
        and `index: None` on every pane in it, because the group it was asking
        about was not in the answer.
        """
        opener = StubOpener([
            {"group_id": "group-1", "workspace_id": "ws-moved"},
            {"sessions": [{"session_id": "pane-1", "group_id": "group-1"}]},
        ])

        dispatch(
            "list_panes",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertIn("workspace_id=ws-moved", opener.requests[1].full_url)

    def test_a_stated_workspace_is_never_second_guessed(self):
        opener = StubOpener([
            {"group_id": "group-1", "workspace_id": "ws-moved"},
            {"sessions": []},
        ])

        dispatch(
            "list_panes",
            {"workspace_id": "ws-9"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertIn("workspace_id=ws-9", opener.requests[1].full_url)

    def test_an_agent_with_no_group_gets_panes_and_no_positions(self):
        opener = StubOpener([
            {"sessions": [{"session_id": "pane-1", "group_id": "group-1"}]},
        ])

        result = dispatch(
            "list_panes",
            {"workspace_id": "ws-9"},
            client=client_for(opener),
            identity=read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050"}),
        )

        self.assertIsNone(result["panes"][0]["index"])
        self.assertNotIn("layout", result)
        # One request: nothing was asked about an arrangement nobody named.
        self.assertEqual(len(opener.requests), 1)

    def test_a_geometry_read_that_failed_still_answers_with_the_panes(self):
        """An agent asking what is open gets the panes, minus what was not read.

        That read is now also the one saying which workspace this pane is in,
        and it degrades the same way in both directions: no positions, and the
        workspace the pane was launched in rather than a refusal.
        """
        opener = StubOpener(
            [{"sessions": [{"session_id": "pane-1", "group_id": "group-1"}]}]
        )
        client = client_for(opener)

        # The arrangement is asked for first, and raises; the panes answer.
        original = opener.open
        calls = {"count": 0}

        def failing(request, timeout=None):
            calls["count"] += 1
            if calls["count"] == 1:
                opener.requests.append(request)
                raise http_error(500, {"error": "boom"})
            return original(request, timeout=timeout)

        opener.open = failing
        result = dispatch(
            "list_panes", {}, client=client, identity=read_identity(INSIDE_PANE)
        )

        self.assertEqual(result["count"], 1)
        self.assertIsNone(result["panes"][0]["index"])
        self.assertNotIn("layout", result)
        self.assertIn("workspace_id=ws-1", opener.requests[-1].full_url)

    def test_whoami_says_which_workspace_the_pane_is_in_now(self):
        """The field an agent reads before naming a workspace to any other
        tool, so it must not be the one the pane was launched in."""
        opener = StubOpener([
            {"session_id": "pane-1", "mode": "wsl"},
            {"group_id": "group-1", "workspace_id": "ws-moved"},
        ])

        result = dispatch(
            "whoami",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["workspace_id"], "ws-moved")

    def test_whoami_knows_which_pane_is_below_it(self):
        opener = StubOpener([
            {"session_id": "pane-1", "mode": "wsl", "current_directory": "C:/repo"},
            _layout_payload(["pane-1", "pane-2"]),
        ])

        result = dispatch(
            "whoami",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["index"], 0)
        self.assertEqual(result["neighbours"]["below"], ["pane-2"])
        self.assertEqual(result["relative_area"], 0.3333)
        self.assertEqual(result["layout"]["layout"], "split")

    def test_whoami_outside_a_group_says_nothing_about_position(self):
        result = dispatch(
            "whoami",
            {},
            client=client_for(StubOpener()),
            identity=read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050"}),
        )

        self.assertNotIn("index", result)
        self.assertNotIn("neighbours", result)

    def test_the_dashboard_rows_stop_dropping_the_index(self):
        """`AGENT_FIELDS` used to drop a number the dashboard already computes."""
        opener = StubOpener([{
            "workspaces": [{
                "workspace_id": "ws-1",
                "label": "Work",
                "groups": [{
                    "group_id": "group-1",
                    "name": "Session",
                    "panes": [{
                        "session_id": "pane-2",
                        "index": 1,
                        "title": "Claude 2",
                        "agent_selection": "claude",
                    }],
                }],
            }]
        }])

        result = dispatch(
            "list_agents", {}, client=client_for(opener), identity=read_identity(INSIDE_PANE)
        )

        self.assertEqual(result["agents"][0]["index"], 1)


class LaunchGeometryTestCase(unittest.TestCase):
    """Prompt A's write half: the field the sidecar used to drop."""

    GEOMETRY = {
        "split_slot_rects": [
            {"originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 2},
            {"originSlot": 1, "x": 3, "y": 1, "w": 1, "h": 1},
        ],
        "split_column_weights": [1.4, 1.4, 0.8],
        "split_row_weights": [1, 1],
        "original_split_slot_count": 2,
    }

    def launch(self, arguments):
        # One request: the launch POST. The destination an unstated workspace
        # resolves to is GridVibe's own to decide, inside that same call.
        opener = StubOpener([
            {"workspace_id": "ws-2", "group_id": "g-2", "sessions": []},
        ])
        dispatch(
            "launch_panes",
            arguments,
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )
        return json.loads(opener.requests[-1].data.decode("utf-8"))

    def test_a_stated_geometry_reaches_the_launch_body(self):
        body = self.launch({
            "panes": [{"kind": "terminal"}, {"kind": "terminal"}],
            "workspace_layout": self.GEOMETRY,
        })

        self.assertEqual(body["workspace_layout"], self.GEOMETRY)

    def test_a_launch_that_states_none_sends_none(self):
        body = self.launch({"panes": [{"kind": "terminal"}]})

        self.assertNotIn("workspace_layout", body)

    def test_a_two_pane_launch_can_finally_ask_to_be_stacked(self):
        body = self.launch({
            "panes": [{"kind": "terminal"}, {"kind": "terminal"}],
            "layout": "horizontal",
        })

        self.assertEqual(body["layout"], "horizontal")

    def test_the_layout_gridvibe_never_had_is_refused_before_any_http(self):
        result = dispatch(
            "launch_panes",
            {"panes": [{"kind": "terminal"}], "layout": "stack"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_a_geometry_that_is_not_an_object_is_refused_before_any_http(self):
        result = dispatch(
            "launch_panes",
            {"panes": [{"kind": "terminal"}], "workspace_layout": "wide"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")


class SavedLayoutTestCase(unittest.TestCase):
    """Prompt B's read: shape published, connection never."""

    LIST = {
        "sessions": [
            {
                "id": "preset-1",
                "name": "review",
                "layout": "split",
                "terminal_count": 3,
                "connection_mode": "ssh",
                "is_default": False,
            }
        ]
    }

    #: What `GET /api/saved-sessions/<id>` actually answers: a config whose SSH
    #: password has been decrypted on load, by design.
    DETAIL = {
        "id": "preset-1",
        "name": "review",
        "config": {
            "connection_mode": "ssh",
            "terminal_count": 2,
            "layout": "split",
            "ssh": {
                "host": "build-box",
                "username": "ubuntu",
                "port": 22,
                "password": "hunter2",
                "default_dir": "/srv/app",
            },
            "terminals": [
                {"startup_mode": "agent", "title": "Claude", "agent_selection": "claude",
                 "password": "hunter2", "host": "build-box"},
                {"startup_mode": "explorer", "title": "Files", "agent_selection": ""},
                {"startup_mode": "terminal", "title": "Spare", "agent_selection": ""},
            ],
            "workspace_layout": {
                "class_name": "layout-split-local",
                "split_slot_rects": [{"originSlot": 0, "x": 1, "y": 1, "w": 1, "h": 1}],
                "split_column_weights": [1],
                "split_row_weights": [1],
                "original_split_slot_count": 1,
            },
        },
    }

    def read(self):
        opener = StubOpener([self.LIST, self.DETAIL])
        return dispatch(
            "list_saved_layouts",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

    def test_a_preset_publishes_its_shape(self):
        result = self.read()

        preset = result["layouts"][0]
        self.assertEqual(result["count"], 1)
        self.assertEqual(preset["name"], "review")
        self.assertEqual(preset["layout"], "split")
        self.assertEqual(preset["workspace_layout"]["class_name"], "layout-split-local")
        self.assertEqual(
            [pane["startup_mode"] for pane in preset["panes"]], ["agent", "explorer"]
        )
        self.assertEqual(preset["panes"][0]["agent_selection"], "claude")

    def test_a_preset_never_publishes_a_connection(self):
        """The route it reads answers with a decrypted password, by design."""
        body = json.dumps(self.read())

        self.assertNotIn("hunter2", body)
        self.assertNotIn("password", body)
        self.assertNotIn("build-box", body)
        self.assertNotIn("ubuntu", body)

    def test_only_the_panes_the_preset_counts_are_published(self):
        """The store keeps `max_sessions` blank entries; a preset is not those."""
        result = self.read()

        self.assertEqual(len(result["layouts"][0]["panes"]), 2)

    def test_a_preset_whose_detail_could_not_be_read_still_lists(self):
        opener = StubOpener([self.LIST])
        original = opener.open
        calls = {"count": 0}

        def failing(request, timeout=None):
            calls["count"] += 1
            if calls["count"] > 1:
                raise http_error(404, {"error": "Saved session not found"})
            return original(request, timeout=timeout)

        opener.open = failing
        result = dispatch(
            "list_saved_layouts",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["layouts"][0]["name"], "review")
        self.assertNotIn("panes", result["layouts"][0])

    def test_the_per_preset_reads_are_bounded(self):
        from gridvibe_mcp.client import MAX_SAVED_LAYOUTS

        opener = StubOpener(
            [{"sessions": [
                {"id": f"p-{index}", "name": str(index), "layout": "single",
                 "terminal_count": 1, "is_default": False}
                for index in range(MAX_SAVED_LAYOUTS + 5)
            ]}]
            + [{"config": {"terminal_count": 0, "terminals": []}}] * (MAX_SAVED_LAYOUTS + 5)
        )

        result = dispatch(
            "list_saved_layouts",
            {},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["count"], MAX_SAVED_LAYOUTS)
        self.assertEqual(result["truncated_at"], MAX_SAVED_LAYOUTS)


class SplitPaneTestCase(unittest.TestCase):
    """The axis is a page decision, so the tool is an intent and a poll."""

    def split(self, arguments, environ=None):
        recorded = {}

        def splitter(client, session_id, axis, pane, **kwargs):
            recorded.update(
                {"session_id": session_id, "axis": axis, "pane": pane, **kwargs}
            )
            return {"status": SPLIT}

        result = dispatch(
            "split_pane",
            arguments,
            client=client_for(RefusingOpener(self)),
            identity=read_identity(environ or INSIDE_PANE),
            pane_splitter=splitter,
        )
        return result, recorded

    def test_an_axis_that_is_not_stated_defaults_to_side_by_side(self):
        _result, recorded = self.split({"session_id": "pane-4"})

        self.assertEqual(recorded["axis"], "vertical")
        self.assertEqual(recorded["pane"], {})

    def test_the_split_names_the_pane_that_asked_for_it(self):
        """The lineage stamp the relaunch gate later reads."""
        _result, recorded = self.split({"session_id": "pane-4", "axis": "horizontal"})

        self.assertEqual(recorded["origin_session_id"], "pane-1")

    def test_a_split_can_say_what_the_new_pane_runs(self):
        _result, recorded = self.split({
            "session_id": "pane-4",
            "axis": "horizontal",
            "kind": "agent",
            "agent": "codex",
            "mcp": True,
        })

        self.assertEqual(
            recorded["pane"],
            {"kind": "agent", "agent": "codex", "auto_mode": False, "mcp": True},
        )

    def test_an_unstated_kind_sends_no_kind_at_all(self):
        """Not the same as a stated plain terminal: an explorer pane splits off
        a terminal rooted where it is browsing, and only an absent kind means
        "do what the button does"."""
        _result, recorded = self.split({"session_id": "pane-4"})

        self.assertNotIn("kind", recorded["pane"])

    def test_an_agent_named_without_the_kind_is_refused(self):
        result = dispatch(
            "split_pane",
            {"session_id": "pane-4", "agent": "claude"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
            pane_splitter=lambda *args, **kwargs: self.fail("reached the splitter"),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_pane_with_no_agent_is_refused_before_any_intent(self):
        result = dispatch(
            "split_pane",
            {"session_id": "pane-4", "kind": "agent"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
            pane_splitter=lambda *args, **kwargs: self.fail("reached the splitter"),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_axis_that_is_not_one_of_the_two_is_refused(self):
        result = dispatch(
            "split_pane",
            {"session_id": "pane-4", "axis": "diagonal"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
            pane_splitter=lambda *args, **kwargs: self.fail("reached the splitter"),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_at_the_limit_cannot_split_off_another_agent(self):
        result, recorded = self.split(
            {"session_id": "pane-4", "kind": "agent", "agent": "claude"},
            environ={**INSIDE_PANE, "GRIDVIBE_AGENT_DEPTH": "2"},
        )

        self.assertEqual(result["kind"], "depth_limit")
        self.assertEqual(recorded, {})

    def test_an_agent_at_the_limit_may_still_split_off_a_plain_terminal(self):
        """The budget bounds agents, not panes."""
        result, recorded = self.split(
            {"session_id": "pane-4"},
            environ={**INSIDE_PANE, "GRIDVIBE_AGENT_DEPTH": "2"},
        )

        self.assertEqual(result["status"], SPLIT)
        self.assertEqual(recorded["session_id"], "pane-4")


class _FlakyOpener(StubOpener):
    """Answers from the queue, except on the numbered calls that drop.

    A transport failure in the middle of a wait, which is the case neither poll
    loop used to survive: `read_window_intent` raised, and the exception left
    `split_pane` through `dispatch` as `{"kind": "unreachable"}`.
    """

    def __init__(self, answers=None, drop_on=()):
        super().__init__(answers)
        self.drop_on = set(drop_on)
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        if self.calls in self.drop_on:
            self.requests.append(request)
            raise urllib.error.URLError("connection reset by peer")
        return super().open(request, timeout=timeout)


class DroppedPollTestCase(unittest.TestCase):
    """One unreadable poll is not an answer about the pane.

    The intent is already recorded when the wait starts. A read that fails says
    nothing about whether a page claimed it, so the loop keeps waiting and the
    deadline answers -- the same decision `pane_layout()` takes one file over,
    where a failed geometry read degrades instead of failing `list_panes`.
    """

    def test_a_split_that_settles_after_a_dropped_poll_is_still_a_split(self):
        opener = _FlakyOpener(
            [
                {"intent_id": "s-1", "axis": "vertical", "state": "pending"},
                {"intent_id": "s-1", "state": "split",
                 "result": {"session_id": "pane-9"}},
            ],
            drop_on={2},
        )

        result = split_pane(
            client_for(opener),
            "pane-4",
            "vertical",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], SPLIT)
        self.assertEqual(result["pane"]["session_id"], "pane-9")

    def test_a_wait_that_ends_unreadable_never_claims_the_panes_are_untouched(self):
        """`NO_PAGE_HINT` states a fact, so it is only said when it is one."""
        clock = iter([0.0, 0.0, 99.0])
        opener = _FlakyOpener(
            [{"intent_id": "s-1", "axis": "vertical", "state": "pending"}],
            drop_on={2, 3},
        )

        result = split_pane(
            client_for(opener),
            "pane-4",
            "vertical",
            sleep=lambda _seconds: None,
            monotonic=lambda: next(clock),
        )

        self.assertEqual(result["status"], SPLIT_NO_WINDOW)
        self.assertNotIn("untouched", result["detail"])
        self.assertIn("not known here", result["detail"])
        self.assertIn("list_panes", result["detail"])

    def test_an_expiry_that_was_read_still_says_untouched(self):
        """The distinction is what was read, not that a read once failed."""
        opener = _FlakyOpener(
            [
                {"intent_id": "s-1", "axis": "vertical", "state": "pending"},
                {"intent_id": "s-1", "state": "expired"},
            ],
            drop_on={2},
        )

        result = split_pane(
            client_for(opener),
            "pane-4",
            "vertical",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], SPLIT_NO_WINDOW)
        self.assertIn("untouched", result["detail"])

    def test_a_dropped_poll_never_reaches_the_agent_as_a_failed_call(self):
        """Through `dispatch`, which is where the typed error used to surface."""
        clock = iter([0.0, 0.0, 99.0])
        opener = _FlakyOpener(
            [{"intent_id": "s-1", "axis": "vertical", "state": "pending"}],
            drop_on={2, 3},
        )

        result = dispatch(
            "split_pane",
            {"session_id": "pane-4"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            pane_splitter=lambda client, session_id, axis, pane, **kwargs: split_pane(
                client,
                session_id,
                axis,
                pane,
                sleep=lambda _seconds: None,
                monotonic=lambda: next(clock),
                **kwargs,
            ),
        )

        self.assertEqual(result["status"], SPLIT_NO_WINDOW)
        self.assertNotIn("error", result)

    def test_a_window_wait_that_ends_unreadable_says_so_too(self):
        clock = iter([0.0, 0.0, 99.0])
        opener = _FlakyOpener(
            [{"intent_id": "w-1", "state": "pending"}], drop_on={2, 3}
        )

        result = open_window(
            client_for(opener),
            "ws-1",
            window_mode="native",
            sleep=lambda _seconds: None,
            monotonic=lambda: next(clock),
        )

        self.assertEqual(result["status"], NO_WINDOW_AVAILABLE)
        self.assertIn("not known here", result["detail"])
        # The workspace exists either way, which is the half that was always safe.
        self.assertIn("launcher", result["detail"])


class SplitIntentPollTestCase(unittest.TestCase):
    """The three outcomes, and the one that browser mode always answers."""

    def test_a_claimed_split_reports_the_pane_it_made(self):
        opener = StubOpener([
            {"intent_id": "s-1", "axis": "horizontal", "state": "pending"},
            {"intent_id": "s-1", "state": "claimed"},
            {"intent_id": "s-1", "state": "split",
             "result": {"session_id": "pane-9", "title": "Terminal 3"}},
        ])

        result = split_pane(
            client_for(opener),
            "pane-4",
            "horizontal",
            {"kind": "agent", "agent": "claude"},
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], SPLIT)
        self.assertEqual(result["axis"], "horizontal")
        self.assertEqual(result["pane"]["session_id"], "pane-9")

    def test_a_pane_too_small_is_refused_with_gridvibes_own_sentence(self):
        sentence = (
            "Side-by-side split needs at least 8 columns in each terminal. "
            "A stacked split would work on this pane."
        )
        opener = StubOpener([
            {"intent_id": "s-1", "axis": "vertical", "state": "pending"},
            {"intent_id": "s-1", "state": "refused", "detail": sentence},
        ])

        result = split_pane(
            client_for(opener),
            "pane-4",
            "vertical",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], REFUSED)
        self.assertEqual(result["detail"], sentence)
        # Unretried: exactly the intent and the one poll that settled it.
        self.assertEqual(len(opener.requests), 2)

    def test_no_page_to_claim_it_leaves_the_workspace_untouched(self):
        opener = StubOpener([
            {"intent_id": "s-1", "axis": "vertical", "state": "pending"},
            {"intent_id": "s-1", "state": "expired"},
        ])

        result = split_pane(
            client_for(opener),
            "pane-4",
            "vertical",
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )

        self.assertEqual(result["status"], SPLIT_NO_WINDOW)
        self.assertIn("untouched", result["detail"])

    def test_a_refusal_the_server_decided_never_starts_a_wait(self):
        """An unknown agent is answered by the intent call itself."""
        sentence = "agent must be a known agent CLI"
        opener = StubOpener(raises=http_error(400, {"error": sentence}))

        result = dispatch(
            "split_pane",
            {"session_id": "pane-4", "kind": "agent", "agent": "nope"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["error"], sentence)
        self.assertEqual(result["status"], 400)
        self.assertEqual(len(opener.requests), 1)

    def test_a_pane_that_changed_hands_is_gridvibes_404_not_a_split(self):
        opener = StubOpener(raises=http_error(404, {"error": "Session not found"}))

        result = dispatch(
            "split_pane",
            {"session_id": "pane-gone"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["status"], 404)
        self.assertIn("not found", result["error"])


class _LatePage:
    """A page that takes the whole of what the store allows it.

    The store's worst case is not its 15s claim window: a page that claims at
    14.9s is then entitled to the full 20s claim TTL before it has to report.
    This stands in for that page, reading a clock rather than following a
    script, so the wait is what decides the outcome.
    """

    def __init__(self, clock, claim_at, report_at):
        self.clock = clock
        self.claim_at = claim_at
        self.report_at = report_at

    def split_intent(self, session_id, body):
        return {"intent_id": "s-1", "axis": body["axis"], "state": "pending"}

    def open_window_intent(self, workspace_id, group_id=""):
        return {"intent_id": "w-1", "state": "pending"}

    def read_window_intent(self, intent_id):
        now = self.clock()
        if now >= self.report_at:
            settled = SPLIT if intent_id == "s-1" else OPENED
            return {
                "intent_id": intent_id,
                "state": settled,
                "result": {"session_id": "pane-9"},
            }
        if now >= self.claim_at:
            return {"intent_id": intent_id, "state": "claimed"}
        return {"intent_id": intent_id, "state": "pending"}


class IntentWaitTestCase(unittest.TestCase):
    """How long the sidecar waits, against how long the store may take.

    Both verbs end with a sentence about what did *not* happen -- the split's
    says the panes and the workspace are untouched. That is only true if the
    store gave up first. The sidecar cannot import `web/`, so the relation
    between the two numbers is pinned here, in the one place that can see both.
    """

    def _clock(self):
        ticks = {"now": 0.0}
        return ticks, (lambda: ticks["now"]), (lambda seconds: ticks.__setitem__(
            "now", ticks["now"] + seconds
        ))

    def test_both_waits_outlast_the_store_at_its_slowest(self):
        worst_case = INTENT_TTL_SECONDS + CLAIM_TTL_SECONDS

        for module in (splits_module, windows_module):
            with self.subTest(module=module.__name__):
                self.assertGreater(module.DEFAULT_WAIT_SECONDS, worst_case)

    def test_a_page_that_claims_late_still_settles_inside_the_wait(self):
        ticks, now, rest = self._clock()
        page = _LatePage(now, claim_at=14.9, report_at=34.9)

        result = split_pane(page, "pane-4", "vertical", sleep=rest, monotonic=now)

        self.assertEqual(result["status"], SPLIT)
        self.assertEqual(result["pane"]["session_id"], "pane-9")

    def test_the_wait_that_was_too_short_would_have_reported_it_untouched(self):
        """The regression itself: the page holds a valid claim and goes on to
        make the pane, while the agent is told the workspace is untouched."""
        ticks, now, rest = self._clock()
        page = _LatePage(now, claim_at=14.9, report_at=34.9)

        result = split_pane(
            page, "pane-4", "vertical", wait_seconds=25.0, sleep=rest, monotonic=now
        )

        self.assertEqual(result["status"], SPLIT_NO_WINDOW)
        self.assertIn("untouched", result["detail"])

    def test_the_window_verb_waits_the_same_way(self):
        ticks, now, rest = self._clock()
        page = _LatePage(now, claim_at=14.9, report_at=34.9)

        result = open_window(
            page, "ws1", window_mode="native", sleep=rest, monotonic=now
        )

        self.assertEqual(result["status"], OPENED)


class SetPaneAgentTestCase(unittest.TestCase):
    """The one verb that ends a process, and what the tool refuses itself."""

    def relaunch(self, arguments, environ=None, answer=None):
        opener = StubOpener([answer or {
            "session_id": "pane-4",
            "startup_mode": "agent",
            "agent_selection": "claude",
            "password": "gAAAAsecret",
        }])
        result = dispatch(
            "set_pane_agent",
            arguments,
            client=client_for(opener),
            identity=read_identity(environ or INSIDE_PANE),
        )
        return result, opener

    def test_the_request_names_the_pane_asking_and_the_agent(self):
        result, opener = self.relaunch({"session_id": "pane-4", "agent": "claude"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["requested_by_session_id"], "pane-1")
        self.assertEqual(body["agent"], "claude")
        self.assertEqual(result["pane"]["agent_selection"], "claude")
        # The gated route, never the header dropdown's own.
        self.assertTrue(opener.requests[0].full_url.endswith("/agent-relaunch"))
        self.assertNotIn("password", json.dumps(result))

    def test_an_unstated_mcp_or_shell_is_left_out_of_the_body(self):
        """Each dimension is a tri-state, exactly as the route reads it."""
        _result, opener = self.relaunch({"session_id": "pane-4", "agent": "claude"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("mcp", body)
        self.assertNotIn("shell", body)

    def test_a_stated_mcp_and_shell_travel(self):
        _result, opener = self.relaunch({
            "session_id": "pane-4", "agent": "claude", "mcp": True, "shell": "wsl",
        })

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(body["mcp"])
        self.assertEqual(body["shell"], "wsl")

    def test_an_unstated_override_is_left_out_of_the_body(self):
        """Absent, not `False` -- the server's own default is the same thing."""
        _result, opener = self.relaunch({"session_id": "pane-4", "agent": "claude"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("override", body)

    def test_a_stated_override_travels(self):
        _result, opener = self.relaunch({
            "session_id": "pane-4", "agent": "claude", "override": True,
        })

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(body["override"])

    def test_an_agent_outside_gridvibe_owns_no_panes_and_is_refused(self):
        """The lineage gate compares against a caller, and there is none."""
        result = dispatch(
            "set_pane_agent",
            {"session_id": "pane-4", "agent": "claude"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050"}),
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("created", result["error"])

    def test_a_request_with_no_agent_is_refused_before_any_http(self):
        result = dispatch(
            "set_pane_agent",
            {"session_id": "pane-4"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_at_the_limit_cannot_relaunch_a_pane_into_an_agent(self):
        result = dispatch(
            "set_pane_agent",
            {"session_id": "pane-4", "agent": "claude"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity({**INSIDE_PANE, "GRIDVIBE_AGENT_DEPTH": "2"}),
        )

        self.assertEqual(result["kind"], "depth_limit")

    def test_a_gate_refusal_reaches_the_agent_naming_the_gate(self):
        sentence = (
            "[lineage gate] This pane was not created by an agent, so a tool "
            "does not relaunch it. Split off a new pane instead."
        )
        opener = StubOpener(raises=http_error(403, {"error": sentence}))

        result = dispatch(
            "set_pane_agent",
            {"session_id": "pane-4", "agent": "claude"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        # Verbatim and unretried: an agent told only "refused" calls again.
        self.assertEqual(result["error"], sentence)
        self.assertEqual(result["status"], 403)
        self.assertEqual(len(opener.requests), 1)


class SetPaneModeTestCase(unittest.TestCase):
    """Turning a pane into an explorer, a browser, or back into a terminal."""

    def switch(self, arguments, environ=None, answer=None):
        opener = StubOpener([answer or {
            "session_id": "pane-4",
            "startup_mode": "explorer",
            "directory": "/srv/app",
            "password": "gAAAAsecret",
        }])
        result = dispatch(
            "set_pane_mode",
            arguments,
            client=client_for(opener),
            identity=read_identity(environ or INSIDE_PANE),
        )
        return result, opener

    def test_the_request_names_the_pane_asking_and_the_mode(self):
        result, opener = self.switch({"session_id": "pane-4", "mode": "explorer"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["requested_by_session_id"], "pane-1")
        self.assertEqual(body["startup_mode"], "explorer")
        self.assertEqual(result["pane"]["startup_mode"], "explorer")
        # The gated route, never the header toggle's own.
        self.assertTrue(opener.requests[0].full_url.endswith("/agent-mode-switch"))
        self.assertNotIn("password", json.dumps(result))

    def test_an_explorer_with_no_directory_asks_where_the_pane_is_standing(self):
        """What the header's own toggle asks for, stated rather than assumed."""
        _result, opener = self.switch({"session_id": "pane-4", "mode": "explorer"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(body["refresh_cwd"])
        self.assertNotIn("directory", body)

    def test_a_stated_directory_replaces_the_probe(self):
        """A caller that named a root has already answered the question."""
        _result, opener = self.switch({
            "session_id": "pane-4", "mode": "explorer", "directory": "/srv/app/web",
        })

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["directory"], "/srv/app/web")
        self.assertNotIn("refresh_cwd", body)

    def test_a_terminal_switch_probes_nothing(self):
        """Leaving explorer mode reads the browsed folder, not the shell."""
        _result, opener = self.switch({"session_id": "pane-4", "mode": "terminal"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("refresh_cwd", body)

    def test_a_browser_pane_carries_its_url(self):
        _result, opener = self.switch({
            "session_id": "pane-4", "mode": "browser", "url": "http://localhost:5050",
        })

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body["url"], "http://localhost:5050")

    def test_a_browser_pane_with_no_url_is_refused_before_any_http(self):
        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4", "mode": "browser"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("url", result["error"])

    def test_a_url_on_a_non_browser_mode_is_refused_rather_than_dropped(self):
        """Silently dropping it would open an explorer and report success."""
        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4", "mode": "explorer", "url": "http://x"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("browser", result["error"])

    def test_an_unknown_mode_is_refused_before_any_http(self):
        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4", "mode": "agent"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        for named in PANE_MODES:
            self.assertIn(named, result["error"])

    def test_a_missing_mode_is_refused_before_any_http(self):
        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_unstated_override_is_left_out_of_the_body(self):
        _result, opener = self.switch({"session_id": "pane-4", "mode": "explorer"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("override", body)

    def test_a_stated_override_travels(self):
        _result, opener = self.switch({
            "session_id": "pane-4", "mode": "explorer", "override": True,
        })

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(body["override"])

    def test_a_mode_switch_costs_no_depth_budget(self):
        """It starts no agent, so the budget that bounds agents does not apply."""
        opener = StubOpener([{"session_id": "pane-4", "startup_mode": "explorer"}])

        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4", "mode": "explorer"},
            client=client_for(opener),
            identity=read_identity({**INSIDE_PANE, "GRIDVIBE_AGENT_DEPTH": "2"}),
        )

        self.assertNotIn("error", result)
        self.assertEqual(len(opener.requests), 1)

    def test_an_agent_outside_gridvibe_owns_no_panes_and_is_refused(self):
        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4", "mode": "explorer"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050"}),
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("created", result["error"])

    def test_a_gate_refusal_reaches_the_agent_naming_the_gate(self):
        sentence = (
            "[lineage gate] This pane was created by a different pane. An "
            "agent switches the mode of only the panes it created itself, "
            "unless the user explicitly asked to override this pane."
        )
        opener = StubOpener(raises=http_error(403, {"error": sentence}))

        result = dispatch(
            "set_pane_mode",
            {"session_id": "pane-4", "mode": "explorer"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["error"], sentence)
        self.assertEqual(result["status"], 403)
        self.assertEqual(len(opener.requests), 1)


class ClearPaneTestCase(unittest.TestCase):
    """The Clear button, asked for by a tool, and what its answer may claim."""

    def clear(self, arguments, environ=None, answer=None):
        opener = StubOpener([answer or {
            "session_id": "pane-4",
            "buffer_purged": True,
            "display_reset_requested": True,
            "password": "gAAAAsecret",
        }])
        result = dispatch(
            "clear_pane",
            arguments,
            client=client_for(opener),
            identity=read_identity(environ or INSIDE_PANE),
        )
        return result, opener

    def test_the_request_names_the_pane_asking_and_nothing_else(self):
        result, opener = self.clear({"session_id": "pane-4"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertEqual(body, {"requested_by_session_id": "pane-1"})
        self.assertTrue(opener.requests[0].full_url.endswith("/pane-4/clear"))
        self.assertNotIn("password", json.dumps(result))

    def test_the_answer_keeps_the_purge_and_the_request_apart(self):
        """One is a fact about GridVibe, the other is what windows were told."""
        result, _opener = self.clear({"session_id": "pane-4"})

        self.assertTrue(result["buffer_purged"])
        self.assertTrue(result["display_reset_requested"])
        # Never a single `cleared: true`: a pane nobody has open resets no
        # display, and the field list is what stops the result pretending.
        self.assertNotIn("cleared", result)

    def test_a_missing_session_is_refused_before_any_http(self):
        result = dispatch(
            "clear_pane",
            {},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_unstated_override_is_left_out_of_the_body(self):
        _result, opener = self.clear({"session_id": "pane-4"})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("override", body)

    def test_a_stated_override_travels(self):
        _result, opener = self.clear({"session_id": "pane-4", "override": True})

        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertTrue(body["override"])

    def test_an_agent_outside_gridvibe_owns_no_panes_and_is_refused(self):
        result = dispatch(
            "clear_pane",
            {"session_id": "pane-4"},
            client=client_for(RefusingOpener(self)),
            identity=read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050"}),
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("created", result["error"])

    def test_a_gate_refusal_reaches_the_agent_naming_the_gate(self):
        sentence = (
            "[mode gate] This pane is running an agent, and a clear types at "
            "the prompt -- which here is that agent's own input."
        )
        opener = StubOpener(raises=http_error(403, {"error": sentence}))

        result = dispatch(
            "clear_pane",
            {"session_id": "pane-4"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["error"], sentence)
        self.assertEqual(result["status"], 403)
        self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__":
    unittest.main()
