"""Which agent CLIs a tool can start, and a launch validated whole before it starts any.

``list_agent_types`` reads every registry agent against the machine a launch
from the asking pane would use -- its SSH host, or this machine under its own
shell family or a stated one -- and reports three separate facts: whether the
CLI can start there, whether it can be given GridVibe's tools, and whether it
can be handed a task. Only three CLIs can take a task; all eight can run.

A launch a tool asks for is refused whole, before any workspace, group or pane
exists, when one of its panes names an agent that cannot start there, asks for
the tools on a CLI that cannot have them, carries a task for a CLI that cannot
take one, or states a local shell family the asking pane cannot honour. Every
such pane is named in the one refusal. The launcher, which shows its own
per-row verdict to a person, keeps opening an absent agent as a terminal.
"""

import json
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tests  # noqa: F401 - redirects durable state away from the real files
from gridvibe_mcp.client import AGENT_TYPES_TIMEOUT_SECONDS
from gridvibe_mcp.identity import read_identity
from gridvibe_mcp.server import build_launch_request, dispatch
from tests.test_mcp_client import StubOpener, client_for
from web import agents as web_agents
from web import api
from web.agent_handoffs import handoffs as store

INSIDE_PANE = {
    "GRIDVIBE_URL": "http://127.0.0.1:5050",
    "GRIDVIBE_SESSION_ID": "pane-1",
    "GRIDVIBE_GROUP_ID": "group-1",
    "GRIDVIBE_WORKSPACE_ID": "ws-1",
    "GRIDVIBE_AGENT_DEPTH": "0",
}

#: What each CLI's binary probe answers in these tests.
_STATUS = {"claude": "installed", "codex": "installed", "kimi": "missing", "grok": "check_failed"}


def _preflight(agent_key, payload):
    status = _STATUS.get(agent_key, "installed")
    return {
        "agent": agent_key,
        "status": status,
        "status_label": status.title(),
        "message": f"{agent_key} is {status}.",
        "target": {"label": "PowerShell"},
        "detection": {"path": f"C:/bin/{agent_key}.exe"},
    }


class _Case(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        store.reset()
        self.addCleanup(store.reset)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests = []

        def recording(agent_key, payload):
            self.requests.append((agent_key, payload))
            return _preflight(agent_key, payload)

        patcher = patch.object(web_agents, "_agent_preflight_payload", side_effect=recording)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pane(self, mode="wsl", **fields):
        group = api.session_manager.create_group(
            name="G", connection_mode=mode, layout="single", terminal_count=1
        )
        values = {"group_id": group.group_id, "host": "PowerShell", "mode": mode,
                  "directory": self.temp.name, "startup_mode": "terminal"}
        values.update(fields)
        return api.session_manager.create_session(**values)

    def _ssh_pane(self):
        return self._pane("ssh", host="example.com", username="ubuntu", port=22,
                          password="hunter2", directory="/srv/app")


# ==================== the read ====================


class AgentTypeRowsTestCase(_Case):
    def test_every_registry_agent_answers_with_three_separate_facts(self):
        rows = {row["key"]: row for row in web_agents.agent_type_rows("wsl", {})}

        self.assertEqual(set(rows), set(web_agents.AGENT_REGISTRY))
        self.assertIs(rows["claude"]["available"], True)
        self.assertIs(rows["kimi"]["available"], False)
        # The check did not run: unknown, and a launch still tries.
        self.assertIsNone(rows["grok"]["available"])
        for key, row in rows.items():
            self.assertEqual(row["mcp_supported"], web_agents._agent_supports_mcp(key))
            self.assertEqual(row["task_supported"], web_agents._agent_accepts_task(key))
        # Running is not the same as taking a task.
        self.assertTrue(rows["claude"]["task_supported"])
        self.assertFalse(rows["opencode"]["task_supported"])
        self.assertNotIn("detection", rows["claude"])

    def test_a_single_probe_failure_answers_check_failed_for_that_agent_only(self):
        def flaky(agent_key, payload):
            if agent_key == "codex":
                raise RuntimeError("probe exploded")
            return _preflight(agent_key, payload)

        with patch.object(web_agents, "_agent_preflight_payload", side_effect=flaky):
            rows = {row["key"]: row for row in web_agents.agent_type_rows("wsl", {})}

        self.assertEqual(rows["codex"]["status"], "check_failed")
        self.assertIs(rows["claude"]["available"], True)


class AgentTypesRouteTestCase(_Case):
    def _get(self, **params):
        return self.client.get("/api/agent-types", query_string=params)

    def test_a_local_pane_is_asked_about_under_its_own_shell_family(self):
        origin = self._pane(use_powershell=True)

        response = self._get(origin_session_id=origin.session_id)

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["count"], len(web_agents.AGENT_REGISTRY))
        self.assertTrue(all(req["terminal"]["use_powershell"] for _key, req in self.requests))
        self.assertIn("GridVibe's own machine", response.get_json()["target"])

    def test_a_stated_family_is_the_one_asked_about(self):
        origin = self._pane(use_powershell=True)

        response = self._get(origin_session_id=origin.session_id, shell="cmd")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertFalse(any(req["terminal"]["use_powershell"] for _key, req in self.requests))

    def test_an_ssh_pane_is_asked_about_on_its_own_host_without_the_credential_leaving(self):
        origin = self._ssh_pane()

        response = self._get(origin_session_id=origin.session_id)

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(all(req["connection_mode"] == "ssh" for _key, req in self.requests))
        self.assertTrue(all(req["ssh"]["host"] == "example.com" for _key, req in self.requests))
        self.assertEqual(response.get_json()["target"], "example.com over SSH")
        self.assertNotIn("hunter2", json.dumps(response.get_json()))

    def test_a_local_family_from_an_ssh_pane_is_refused_like_the_launch(self):
        origin = self._ssh_pane()

        response = self._get(origin_session_id=origin.session_id, shell="powershell")

        self.assertEqual(response.status_code, 400)
        self.assertIn("over SSH", response.get_json()["error"])
        self.assertEqual(self.requests, [])

    def test_a_closed_origin_and_an_unknown_family_are_refused(self):
        self.assertEqual(self._get(origin_session_id="gone").status_code, 400)
        self.assertEqual(self._get(shell="bash").status_code, 400)


# ==================== the launch, validated whole ====================


class ToolLaunchValidationTestCase(_Case):
    def _agent(self, agent, title=None, **fields):
        config = {"title": title or agent, "directory": self.temp.name,
                  "startup_mode": "agent", "initial_command_mode": "agent",
                  "initial_command": agent, "agent_selection": agent}
        config.update(fields)
        return config

    def _launch(self, sessions, origin=None, **body):
        payload = {"connection_mode": "wsl", "new_workspace": True,
                   "workspace_label": "agents", "session_name": "agents",
                   "sessions": sessions, **body}
        if origin is not None:
            payload["origin_session_id"] = origin.session_id
        workspaces_before = len(api.session_manager.get_all_workspaces())
        groups_before = len(api.session_manager.get_all_groups())
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post("/api/sessions", json=payload)
        return response, groups_before, workspaces_before

    def _assert_nothing_launched(self, response, groups_before, workspaces_before):
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(len(api.session_manager.get_all_groups()), groups_before)
        self.assertEqual(len(api.session_manager.get_all_workspaces()), workspaces_before)

    def test_every_unavailable_agent_is_named_and_nothing_launches(self):
        origin = self._pane()

        response, groups, workspaces = self._launch(
            [self._agent("claude"), self._agent("kimi"), self._agent("kimi", title="kimi 2")],
            origin,
        )

        self._assert_nothing_launched(response, groups, workspaces)
        error = response.get_json()["error"]
        self.assertIn("Pane 2 (kimi)", error)
        self.assertIn("Pane 3 (kimi)", error)
        self.assertNotIn("Pane 1", error)

    def test_an_agent_whose_check_could_not_run_still_starts_as_that_agent(self):
        """Available null means "a launch still tries" -- and it does."""
        origin = self._pane()

        response, _groups, _workspaces = self._launch([self._agent("grok")], origin)

        self.assertEqual(response.status_code, 201, response.get_json())
        pane = response.get_json()["sessions"][0]
        self.assertEqual((pane["startup_mode"], pane["initial_command"]), ("agent", "grok"))
        self.assertTrue(any("could not run" in item for item in response.get_json()["warnings"]))

    def test_a_tool_launch_from_no_pane_is_validated_the_same_way(self):
        response, groups, workspaces = self._launch([self._agent("kimi")], tool_launch=True)
        self._assert_nothing_launched(response, groups, workspaces)

    def test_the_launcher_still_opens_an_absent_agent_as_a_terminal(self):
        response, _groups, _workspaces = self._launch([self._agent("kimi")])

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["sessions"][0]["startup_mode"], "terminal")
        self.assertTrue(response.get_json()["warnings"])

    def test_an_unknown_cli_is_refused_rather_than_typed_as_a_command(self):
        origin = self._pane()

        response, groups, workspaces = self._launch([self._agent("nosuchagent")], origin)

        self._assert_nothing_launched(response, groups, workspaces)
        self.assertIn("'nosuchagent' is not a known agent CLI", response.get_json()["error"])

    def test_the_tools_asked_for_on_a_cli_that_cannot_have_them_are_refused(self):
        origin = self._pane()

        response, groups, workspaces = self._launch(
            [self._agent("opencode", agent_mcp=True)], origin
        )

        self._assert_nothing_launched(response, groups, workspaces)
        self.assertIn("opencode cannot be given GridVibe's tools", response.get_json()["error"])

    def test_every_pane_whose_agent_cannot_take_a_task_is_named_together(self):
        origin = self._pane(startup_mode="agent", agent_selection="claude",
                            initial_command="claude", initial_command_mode="agent")

        response, groups, workspaces = self._launch(
            [self._agent("claude", task="Do it."), self._agent("opencode", task="Do it."),
             self._agent("kimi", task="Do it.")],
            origin,
        )

        self._assert_nothing_launched(response, groups, workspaces)
        error = response.get_json()["error"]
        self.assertIn("Pane 2: opencode cannot be handed a task", error)
        self.assertIn("Pane 3: kimi cannot be handed a task", error)
        self.assertIn("Only claude, codex, copilot can be handed a task", error)
        self.assertEqual(store.count(), 0)

    def test_powershell_from_a_wsl_pane_is_refused(self):
        origin = self._pane(use_wsl=True, host="WSL")

        response, groups, workspaces = self._launch(
            [self._agent("claude", use_powershell=True, use_wsl=False)], origin
        )

        self._assert_nothing_launched(response, groups, workspaces)
        self.assertIn("runs in WSL", response.get_json()["error"])

    def test_a_two_by_four_grid_of_available_agents_and_terminals_launches(self):
        """The manual prompt's shape: available agents, plain terminals for the rest."""
        origin = self._pane(use_powershell=True)
        panes = [self._agent(key, use_powershell=True, use_wsl=False)
                 for key in ("claude", "codex", "copilot", "opencode", "kilo", "hermes")]
        panes += [{"title": f"Terminal {n}", "directory": self.temp.name,
                   "startup_mode": "terminal", "initial_command_mode": "command",
                   "use_powershell": True, "use_wsl": False} for n in (7, 8)]
        rects = [{"x": 1 + (i % 4), "y": 1 + (i // 4), "w": 1, "h": 1} for i in range(8)]

        response, _groups, _workspaces = self._launch(
            panes, origin, layout="grid",
            workspace_layout={"split_slot_rects": rects,
                              "split_column_weights": [1, 1, 1, 1],
                              "split_row_weights": [1, 1]},
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        sessions = response.get_json()["sessions"]
        self.assertEqual([s["startup_mode"] for s in sessions], ["agent"] * 6 + ["terminal"] * 2)
        self.assertTrue(all(s["use_powershell"] for s in sessions))
        self.assertEqual(len(response.get_json()["workspace_layout"]["split_slot_rects"]), 8)


class SplitMcpRefusalTestCase(_Case):
    def test_the_tools_on_a_split_cli_that_cannot_have_them_are_refused(self):
        source = self._pane()

        response = self.client.post(
            f"/api/sessions/{source.session_id}/split-intent",
            json={"axis": "vertical", "kind": "agent", "agent": "opencode", "mcp": True},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("opencode cannot be given GridVibe's tools", response.get_json()["error"])


# ==================== the sidecar ====================


class ListAgentTypesToolTestCase(unittest.TestCase):
    def _call(self, arguments, answer=None):
        opener = StubOpener([answer or {
            "agents": [{"key": "claude", "available": True, "task_supported": True,
                        "mcp_supported": True, "password": "leak", "path": "C:/x"}],
            "count": 1,
            "target": "GridVibe's own machine, PowerShell",
        }])
        result = dispatch("list_agent_types", arguments, client=client_for(opener),
                          identity=read_identity(INSIDE_PANE))
        return result, opener

    def test_it_asks_about_this_panes_machine_with_a_longer_deadline(self):
        result, opener = self._call({"shell": "powershell"})

        url = opener.requests[0].full_url
        self.assertIn("/api/agent-types?", url)
        self.assertIn("origin_session_id=pane-1", url)
        self.assertIn("shell=powershell", url)
        self.assertGreaterEqual(opener.timeouts[0], AGENT_TYPES_TIMEOUT_SECONDS)
        self.assertEqual(result["agent_types"][0]["key"], "claude")
        self.assertNotIn("password", json.dumps(result))
        self.assertNotIn("path", result["agent_types"][0])

    def test_an_unknown_shell_is_refused_before_the_wire(self):
        result, opener = self._call({"shell": "bash"})
        self.assertIn("error", result)
        self.assertEqual(opener.requests, [])

    def test_a_tool_launch_says_so(self):
        body = build_launch_request(
            {"panes": [{"kind": "terminal"}]}, identity=read_identity(INSIDE_PANE)
        )
        self.assertIs(body["tool_launch"], True)


class SetPaneModeChangedTestCase(unittest.TestCase):
    def test_a_no_op_is_reported_as_one(self):
        opener = StubOpener([{"session_id": "pane-4", "startup_mode": "terminal",
                              "directory": "/srv/app", "changed": False}])

        result = dispatch("set_pane_mode",
                          {"session_id": "pane-4", "mode": "terminal", "directory": "/srv/app"},
                          client=client_for(opener), identity=read_identity(INSIDE_PANE))

        self.assertIs(result["changed"], False)
        self.assertIn("Nothing was changed", result["note"])
        self.assertNotIn("changed", result["pane"])
        body = json.loads(opener.requests[0].data.decode("utf-8"))
        self.assertNotIn("refresh_cwd", body)

    def test_a_change_carries_no_note(self):
        opener = StubOpener([{"session_id": "pane-4", "startup_mode": "terminal",
                              "directory": "/srv", "changed": True}])

        result = dispatch("set_pane_mode",
                          {"session_id": "pane-4", "mode": "terminal", "directory": "/srv"},
                          client=client_for(opener), identity=read_identity(INSIDE_PANE))

        self.assertIs(result["changed"], True)
        self.assertNotIn("note", result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
