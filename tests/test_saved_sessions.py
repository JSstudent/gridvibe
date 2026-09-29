"""`agent_mcp_override` through the reusable-preset product.

Override mode is a standing grant made when an agent pane is launched: that
agent acts on other panes without first asking. It rides beside `agent_mcp`
through every writer of a saved preset, and three rules hold everywhere:

- **Only a stated `true` grants it.** A preset written before the field existed
  has no key, and that reads `False` -- never "inherit" and never a truthy
  string coerced into a grant.
- **It goes with the tools.** A pane without `agent_mcp`, a CLI that cannot be
  handed the tools, and a pane that is not an agent carry no grant.
- **It round-trips.** Import, the terminal page's Save Workspace, the exit
  save's live-group candidate and a relaunch all keep it.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from web import agents as web_agents
from web import api
from web import config as web_config
from web import lifecycle as web_lifecycle
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions

SHARED_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "shared.js"
NODE = shutil.which("node")


def _agent_entry(**overrides):
    """One saved agent pane that asked for GridVibe's tools and the grant."""
    entry = {
        "title": "Claude",
        "startup_mode": "agent",
        "initial_command_mode": "agent",
        "agent_selection": "claude",
        "initial_command": "claude",
        "agent_mcp": True,
        "agent_mcp_override": True,
    }
    entry.update(overrides)
    return entry


def _normalized(entry, connection_mode="wsl"):
    return web_saved_sessions._normalize_terminal_entries(
        [entry], connection_mode, minimum_count=1
    )[0]


class PresetEntryOverrideTestCase(unittest.TestCase):
    """The one normalizer every preset entry passes through."""

    def test_a_stated_grant_is_kept(self):
        self.assertIs(_normalized(_agent_entry())["agent_mcp_override"], True)

    def test_an_ssh_preset_keeps_the_grant_too(self):
        self.assertIs(_normalized(_agent_entry(), "ssh")["agent_mcp_override"], True)

    def test_a_preset_written_before_the_field_reads_false(self):
        entry = _agent_entry()
        del entry["agent_mcp_override"]

        normalized = _normalized(entry)

        self.assertIs(normalized["agent_mcp"], True)
        self.assertIs(normalized["agent_mcp_override"], False)

    def test_only_a_stated_true_grants_it(self):
        for value in (None, "true", "yes", 1, [True], {"on": True}):
            with self.subTest(value=value):
                self.assertIs(
                    _normalized(_agent_entry(agent_mcp_override=value))[
                        "agent_mcp_override"
                    ],
                    False,
                )

    def test_no_grant_without_the_tools(self):
        self.assertIs(
            _normalized(_agent_entry(agent_mcp=False))["agent_mcp_override"], False
        )

    def test_no_grant_for_a_cli_that_cannot_be_handed_the_tools(self):
        entry = _agent_entry(agent_selection="opencode", initial_command="opencode")

        normalized = _normalized(entry)

        self.assertIs(normalized["agent_mcp"], False)
        self.assertIs(normalized["agent_mcp_override"], False)

    def test_no_grant_on_a_pane_that_is_not_an_agent(self):
        for mode in ("terminal", "explorer", "browser"):
            with self.subTest(mode=mode):
                entry = _agent_entry(startup_mode=mode, initial_command_mode=mode)
                self.assertIs(_normalized(entry)["agent_mcp_override"], False)

    def test_a_blank_preset_states_no_grant(self):
        for entry in web_saved_sessions._default_terminal_entries():
            self.assertIs(entry["agent_mcp_override"], False)
        self.assertIs(_normalized({})["agent_mcp_override"], False)


class WorkspaceSaveOverrideTestCase(unittest.TestCase):
    """Saving a live workspace over an existing preset."""

    @staticmethod
    def _config(terminal):
        return {
            "connection_mode": "wsl",
            "terminal_count": 1,
            "layout": "single",
            "wsl": {"distribution": "", "username": "", "default_dir": "C:/repo"},
            "terminals": [{"directory": "C:/repo", **terminal}],
        }

    def test_the_live_grant_replaces_the_presets(self):
        base = self._config(_agent_entry(agent_mcp_override=False))
        live = self._config(_agent_entry())

        merged = web_saved_sessions._merge_workspace_session_config(base, live)

        self.assertIs(merged["terminals"][0]["agent_mcp_override"], True)

    def test_a_live_pane_without_the_grant_clears_the_presets(self):
        base = self._config(_agent_entry())
        live = self._config(_agent_entry(agent_mcp_override=False))

        merged = web_saved_sessions._merge_workspace_session_config(base, live)

        self.assertIs(merged["terminals"][0]["agent_mcp_override"], False)

    def test_a_pane_that_stopped_being_an_agent_clears_the_grant(self):
        base = self._config(_agent_entry())
        live = self._config(
            {"startup_mode": "terminal", "initial_command_mode": "command"}
        )

        merged = web_saved_sessions._merge_workspace_session_config(base, live)

        self.assertIs(merged["terminals"][0]["agent_mcp"], False)
        self.assertIs(merged["terminals"][0]["agent_mcp_override"], False)

    def test_the_exit_saves_live_group_candidate_carries_the_grant(self):
        pane = {
            "session_id": "abc123",
            "directory": "C:/repo",
            "current_directory": None,
            "explorer_root_directory": None,
            "explorer_root_configured": False,
            **_agent_entry(),
        }

        live_config = web_lifecycle._live_group_config(
            {"connection_mode": "wsl", "layout": "single", "sessions": [pane]}
        )
        preset = web_saved_sessions._normalize_session_config(live_config)

        self.assertIs(preset["terminals"][0]["agent_mcp_override"], True)


@unittest.skipUnless(NODE, "Node.js is required for shared launch-field tests")
class PresetLaunchFieldsOverrideTestCase(unittest.TestCase):
    """The page's preset-to-launch mapper, executed rather than read."""

    def test_the_launch_request_carries_only_a_stated_grant_beside_the_tools(self):
        harness = r"""
const fs = require('fs');
const vm = require('vm');

const sandbox = {
    console,
    document: { getElementById: () => null, addEventListener: () => {} }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const agent = { startup_mode: 'agent', agent_selection: 'claude', agent_mcp: true };
const cases = {
    granted: { ...agent, agent_mcp_override: true },
    legacy: { ...agent },
    truthy_string: { ...agent, agent_mcp_override: 'true' },
    no_tools: { ...agent, agent_mcp: false, agent_mcp_override: true },
    terminal: { startup_mode: 'terminal', agent_mcp: true, agent_mcp_override: true }
};
const result = {};
for (const [name, terminal] of Object.entries(cases)) {
    result[name] = sandbox.buildPaneLaunchFields(terminal).agent_mcp_override;
}
process.stdout.write(JSON.stringify(result));
"""
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(SHARED_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")

        self.assertEqual(
            json.loads(completed.stdout),
            {
                "granted": True,
                "legacy": False,
                "truthy_string": False,
                "no_tools": False,
                "terminal": False,
            },
        )


class PresetRoundTripOverrideTestCase(unittest.TestCase):
    """Import a preset, read it back, launch it: the pane holds the grant."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        for module, attribute, filename in (
            (web_config, "CONFIG_PATH", "config.json"),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", "saved_sessions.json"),
            (web_runtime_state, "RUNTIME_STATE_PATH", "runtime_state.json"),
        ):
            patcher = patch.object(
                module, attribute, str(Path(self.temp_dir.name) / filename)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _save_preset(self, terminal):
        created = self.client.post(
            "/api/saved-sessions",
            json={
                "name": "Override preset",
                "config": {
                    "connection_mode": "wsl",
                    "terminal_count": 1,
                    "layout": "single",
                    "wsl": {
                        "distribution": "",
                        "username": "",
                        "default_dir": str(self.repo_dir),
                    },
                    "terminals": [{"directory": str(self.repo_dir), **terminal}],
                },
            },
        )
        self.assertEqual(created.status_code, 201, created.get_json())
        fetched = self.client.get(f"/api/saved-sessions/{created.get_json()['id']}")
        self.assertEqual(fetched.status_code, 200, fetched.get_json())
        return fetched.get_json()["config"]["terminals"][0]

    def _launch(self, terminal):
        with patch.object(
            web_agents,
            "_agent_preflight_payload",
            return_value={"status": "installed", "message": "Claude Code is available."},
        ), patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "workspace_id": "default",
                    "layout": "single",
                    "sessions": [terminal],
                },
            )
        self.assertEqual(response.status_code, 201, response.get_json())
        return api.session_manager.get_group_sessions(
            response.get_json()["group_id"]
        )[0]

    def test_a_granted_preset_launches_a_pane_holding_the_grant(self):
        terminal = self._save_preset(_agent_entry())
        self.assertIs(terminal["agent_mcp_override"], True)

        session = self._launch(terminal)

        self.assertTrue(session.agent_mcp)
        self.assertIs(session.agent_mcp_override, True)
        self.assertIs(session.to_dict()["agent_mcp_override"], True)

    def test_a_preset_file_written_before_the_field_launches_without_it(self):
        terminal = self._save_preset(_agent_entry())
        stored = json.loads(
            Path(web_saved_sessions.SAVED_SESSIONS_PATH).read_text(encoding="utf-8")
        )
        for entry in stored["sessions"]:
            for pane in entry["config"]["terminals"]:
                pane.pop("agent_mcp_override", None)
        Path(web_saved_sessions.SAVED_SESSIONS_PATH).write_text(
            json.dumps(stored), encoding="utf-8"
        )
        legacy = self.client.get("/api/saved-sessions").get_json()
        preset_id = legacy["sessions"][0]["id"]
        terminal = self.client.get(f"/api/saved-sessions/{preset_id}").get_json()[
            "config"
        ]["terminals"][0]
        self.assertIs(terminal["agent_mcp_override"], False)

        session = self._launch(terminal)

        self.assertTrue(session.agent_mcp)
        self.assertIs(session.agent_mcp_override, False)


if __name__ == "__main__":
    unittest.main()
