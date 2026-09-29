"""`agent_mcp_override` through the runtime workspace snapshot.

A restore is the same pane coming back, so it keeps the grant it was launched
with. A snapshot written before the field existed does not state it, and that
pane comes back without the grant -- the absent key reads `False`, never
"inherit". The record itself holds the grant only beside `agent_mcp` on an
agent pane, which is what makes every path that stops a pane being one drop it.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sessions.manager import SessionManager
from web import agents as web_agents
from web import api
from web import config as web_config
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions

_AGENT_PANE = {
    "title": "Claude",
    "startup_mode": "agent",
    "initial_command_mode": "agent",
    "agent_selection": "claude",
    "initial_command": "claude",
    "agent_mcp": True,
}


class OverrideRecordTestCase(unittest.TestCase):
    """The session record: what it accepts, and what it refuses to keep."""

    def setUp(self):
        self.manager = SessionManager()
        self.group = self.manager.create_group(
            name="Local", connection_mode="wsl", layout="single", terminal_count=1
        )

    def _pane(self, **fields):
        return self.manager.create_session(
            group_id=self.group.group_id,
            host="cmd",
            directory="C:/repo",
            mode="wsl",
            **fields,
        )

    def test_a_pane_defaults_to_no_grant(self):
        session = self._pane(**_AGENT_PANE)

        self.assertIs(session.agent_mcp_override, False)
        self.assertIs(session.to_dict()["agent_mcp_override"], False)

    def test_an_agent_pane_with_the_tools_keeps_a_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.assertIs(session.agent_mcp_override, True)
        self.assertIs(session.to_dict()["agent_mcp_override"], True)

    def test_no_grant_is_kept_without_the_tools_or_off_an_agent_pane(self):
        cases = {
            "no tools": {**_AGENT_PANE, "agent_mcp": False},
            "terminal": {"startup_mode": "terminal", "agent_mcp": True},
            "explorer": {"startup_mode": "explorer", "agent_mcp": True},
        }
        for label, fields in cases.items():
            with self.subTest(case=label):
                session = self._pane(**fields, agent_mcp_override=True)
                self.assertIs(session.agent_mcp_override, False)

    def test_launch_intake_believes_only_a_stated_true(self):
        for value, expected in (
            (True, True),
            (None, False),
            ("true", False),
            (1, False),
        ):
            with self.subTest(value=value):
                fields = self.manager._session_launch_fields(
                    {**_AGENT_PANE, "agent_mcp_override": value}
                )
                self.assertIs(fields["agent_mcp_override"], expected)
        absent = self.manager._session_launch_fields(dict(_AGENT_PANE))
        self.assertIs(absent["agent_mcp_override"], False)

    def test_a_metadata_update_that_removes_the_tools_drops_the_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.manager.update_session_metadata(session.session_id, agent_mcp=False)
        self.assertIs(session.agent_mcp_override, False)

        # It does not come back with the tools.
        self.manager.update_session_metadata(session.session_id, agent_mcp=True)
        self.assertIs(session.agent_mcp_override, False)

    def test_a_metadata_update_that_ends_the_agent_drops_the_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.manager.update_session_metadata(
            session.session_id, startup_mode="explorer"
        )

        self.assertIs(session.agent_mcp_override, False)

    def test_a_browser_switch_drops_the_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.manager.merge_browser_tabs(
            session.session_id,
            browser_url="http://localhost:3000",
            browser_active_tab=None,
            default_browser_url="http://localhost:3000",
        )

        self.assertIs(session.agent_mcp_override, False)

    def test_a_grant_stated_beside_the_agent_it_rides_on_is_kept(self):
        session = self._pane(startup_mode="terminal")

        self.manager.update_session_metadata(
            session.session_id, **_AGENT_PANE, agent_mcp_override=True
        )

        self.assertIs(session.agent_mcp_override, True)


class SnapshotOverrideTestCase(unittest.TestCase):
    """Capture and read-side validation, without a live app."""

    def test_capture_records_the_grant(self):
        snapshot = web_runtime_state._snapshot_session(
            {**_AGENT_PANE, "directory": "C:/repo", "agent_mcp_override": True}
        )

        self.assertIs(snapshot["agent_mcp_override"], True)

    def test_validation_passes_a_stored_grant_through(self):
        stored = web_runtime_state._snapshot_session(
            {**_AGENT_PANE, "directory": "C:/repo", "agent_mcp_override": True}
        )

        validated = web_runtime_state._validate_session(stored)

        self.assertIsNotNone(validated)
        self.assertIs(validated["agent_mcp_override"], True)


class _LiveAppMixin:
    """A real app client over temporary durable state, agents preflighted."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        for module, attribute, value in (
            (web_config, "CONFIG_PATH", str(Path(self.temp_dir.name) / "config.json")),
            (
                web_saved_sessions,
                "SAVED_SESSIONS_PATH",
                str(Path(self.temp_dir.name) / "saved_sessions.json"),
            ),
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
        ):
            patcher = patch.object(module, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, attribute, value in (
            (
                web_agents,
                "_agent_preflight_payload",
                {"status": "installed", "message": "Claude Code is available."},
            ),
        ):
            patcher = patch.object(target, attribute, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        background = patch.object(api.socketio, "start_background_task")
        background.start()
        self.addCleanup(background.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        api.lifecycle_coordinator.reset()
        self.addCleanup(api.session_manager.reset_sessions)
        self.addCleanup(api.lifecycle_coordinator.reset)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _launch(self, **pane):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "workspace_id": "default",
                "session_name": "Agents",
                "layout": "single",
                "sessions": [{**_AGENT_PANE, "directory": str(self.repo_dir), **pane}],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return api.session_manager.get_group_sessions(
            response.get_json()["group_id"]
        )[0]


class RestoreOverrideTestCase(_LiveAppMixin, unittest.TestCase):
    """Launch, capture, restart, restore: the pane comes back as it was."""

    def _save_restart_and_restore(self, edit_stored=None):
        saved = self.client.post(
            "/api/runtime-state/save", json={"workspace_id": "default"}
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        if edit_stored is not None:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            for pane in state["workspaces"]["default"]["groups"][0]["sessions"]:
                edit_stored(pane)
            self.state_path.write_text(json.dumps(state), encoding="utf-8")

        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        group = api.session_manager.get_workspace_groups("default")[0]
        return api.session_manager.get_group_sessions(group.group_id)[0]

    def _stored_panes(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state["workspaces"]["default"]["groups"][0]["sessions"]

    def test_a_granted_pane_is_saved_and_restored_with_its_grant(self):
        self._launch(agent_mcp_override=True)

        session = self._save_restart_and_restore()

        self.assertIs(self._stored_panes()[0]["agent_mcp_override"], True)
        self.assertTrue(session.agent_mcp)
        self.assertIs(session.agent_mcp_override, True)

    def test_a_pane_without_the_grant_comes_back_without_it(self):
        self._launch()

        session = self._save_restart_and_restore()

        self.assertIs(self._stored_panes()[0]["agent_mcp_override"], False)
        self.assertIs(session.agent_mcp_override, False)

    def test_a_snapshot_written_before_the_field_restores_without_the_grant(self):
        self._launch(agent_mcp_override=True)

        session = self._save_restart_and_restore(
            edit_stored=lambda pane: pane.pop("agent_mcp_override", None)
        )

        self.assertTrue(session.agent_mcp)
        self.assertIs(session.agent_mcp_override, False)

    def test_a_hand_edited_truthy_string_restores_without_the_grant(self):
        self._launch(agent_mcp_override=True)

        session = self._save_restart_and_restore(
            edit_stored=lambda pane: pane.update(agent_mcp_override="true")
        )

        self.assertIs(session.agent_mcp_override, False)


class ToolLaunchOverrideTestCase(_LiveAppMixin, unittest.TestCase):
    """A launch an agent asks for from its pane states no grant of its own."""

    def test_a_tool_launch_cannot_state_a_grant(self):
        # The caller holds the grant itself; that still does not let it hand
        # one to the pane it launches.
        caller = self._launch(agent_mcp_override=True)
        self.assertIs(caller.agent_mcp_override, True)

        response = self.client.post(
            "/api/sessions",
            json={
                "workspace_id": "default",
                "layout": "single",
                "origin_session_id": caller.session_id,
                "sessions": [
                    {
                        **_AGENT_PANE,
                        "directory": str(self.repo_dir),
                        "agent_mcp_override": True,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        created = api.session_manager.get_group_sessions(
            response.get_json()["group_id"]
        )[0]
        self.assertTrue(created.agent_mcp)
        self.assertIs(created.agent_mcp_override, False)


if __name__ == "__main__":
    unittest.main()
