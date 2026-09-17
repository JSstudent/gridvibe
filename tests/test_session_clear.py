"""Clearing one pane from a tool: the gates, the purge, and the honest answer.

The header's Clear button is not a route. It resets the pane's xterm, unwinds
the mouse reporting a crashed TUI left armed, purges the rolling replay buffer,
and types the shell's own clear command -- and only the third of those is
something a process outside a page can do at all.

So `POST /api/sessions/<id>/clear` owns the half it can own and *asks* for the
other, and what this file pins is that the split stays honest:

- **Every gate is checked before the buffer is touched.** Each refusal is
  asserted on the pane *and* on its replay buffer, so a purge that leaked ahead
  of a gate shows up here rather than in production.
- **The answer separates a fact from a request.** `buffer_purged` is something
  GridVibe did; `display_reset_requested` is something the windows were told.
  A pane nobody has open resets no display, and the payload never claims one.
- **The page half is asked for exactly once, in the pane's own room.** A
  broadcast to every socket would reset panes in other workspaces.
- **The service is not a Flask handler.** It is called directly, with no
  request context, and answers with a plain dict or a `ClearTransitionError`
  whose own status the route maps.
"""

import ast
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import api
from web import config as web_config
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import session_clear as web_session_clear

#: Every field the clear must leave alone -- which is all of them. A clear
#: changes no pane metadata at all, so the whole snapshot is the assertion.
_PANE_FIELDS = (
    "host",
    "directory",
    "current_directory",
    "startup_mode",
    "initial_command",
    "initial_command_mode",
    "agent_selection",
    "custom_agent",
    "agent_mcp",
    "status",
)

_WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)


def _module_source(filename):
    with io.open(os.path.join(_WEB_DIR, filename), encoding="utf-8") as handle:
        return handle.read()


def _pane_state(session_id):
    session = api.session_manager.get_session(session_id)
    return {
        name: json.loads(json.dumps(getattr(session, name, None), default=str))
        for name in _PANE_FIELDS
    }


class PaneClearTestCase(unittest.TestCase):
    """Shared fixture: a real app client over temporary durable state."""

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
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def _pane(self, group_id=None, **overrides):
        """One Local Repo pane, with a real directory behind it."""
        if group_id is None:
            group = api.session_manager.create_group(
                name="Local", connection_mode="wsl", layout="single", terminal_count=1
            )
            group_id = group.group_id
        repo_dir = Path(self.temp_dir.name) / overrides.pop("repo_name", "repo")
        repo_dir.mkdir(exist_ok=True)
        fields = {
            "group_id": group_id,
            "host": "cmd",
            "directory": str(repo_dir),
            "mode": "wsl",
            "startup_mode": "terminal",
        }
        fields.update(overrides)
        session = api.session_manager.create_session(**fields)
        api.session_manager.update_session_status(
            session.session_id, api.SessionStatus.CONNECTED
        )
        return api.session_manager.get_session(session.session_id)

    def _agent_pair(self, **overrides):
        """A caller pane and a pane it created, in one group."""
        caller = self._pane(
            repo_name="agent-repo",
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
        )
        target = self._pane(
            group_id=caller.group_id,
            repo_name="target",
            created_by_session_id=caller.session_id,
            **overrides,
        )
        return caller, target

    def _fill_buffer(self, session_id, text="drawn before the clear\r\n"):
        """Put something in the pane's replay buffer, the way output does."""
        api._cache_terminal_output(session_id, text)
        self.assertIn(text, api._get_buffered_terminal_output(session_id))

    def _buffered(self, session_id):
        return api._get_buffered_terminal_output(session_id)

    def _clear(self, session_id, body):
        """POST one clear with the room emit observable."""
        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                f"/api/sessions/{session_id}/clear", json=body
            )
        return response, emit

    # ---------------- the pane that passes ----------------

    def test_a_pane_this_agent_created_is_purged_and_its_windows_told(self):
        caller, target = self._agent_pair()
        self._fill_buffer(target.session_id)

        response, emit = self._clear(
            target.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self._buffered(target.session_id), "")
        emit.assert_called_once_with(
            "terminal_cleared",
            {"session_id": target.session_id},
            room=target.session_id,
        )

    def test_the_answer_keeps_what_happened_apart_from_what_was_asked_for(self):
        caller, target = self._agent_pair()

        response, _emit = self._clear(
            target.session_id, {"requested_by_session_id": caller.session_id}
        )

        payload = response.get_json()
        self.assertEqual(payload["session_id"], target.session_id)
        self.assertTrue(payload["buffer_purged"])
        self.assertTrue(payload["display_reset_requested"])
        # Never a single `cleared: true`: no window may be open at all, and the
        # payload must not claim a display was reset that nobody has on screen.
        self.assertNotIn("cleared", payload)

    def test_a_clear_moves_no_pane_metadata_and_restarts_nothing(self):
        """It erases what a pane has drawn. It is not a relaunch."""
        caller, target = self._agent_pair()
        before = _pane_state(target.session_id)

        with patch.object(api, "_close_ssh_connection") as close_connection, patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{target.session_id}/clear",
                json={"requested_by_session_id": caller.session_id},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(_pane_state(target.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_pane_with_no_live_shell_is_still_purged(self):
        """The buffer is what the next window replays, connection or not."""
        caller, target = self._agent_pair()
        self._fill_buffer(target.session_id)
        api.session_manager.update_session_status(
            target.session_id, api.SessionStatus.DISCONNECTED
        )

        response, _emit = self._clear(
            target.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self._buffered(target.session_id), "")

    # ---------------- the three gates ----------------

    def test_a_pane_the_user_created_is_refused_naming_the_lineage_gate(self):
        caller, _target = self._agent_pair()
        handmade = self._pane(group_id=caller.group_id, repo_name="handmade")
        self._fill_buffer(handmade.session_id)
        before = _pane_state(handmade.session_id)
        buffered = self._buffered(handmade.session_id)

        response, emit = self._clear(
            handmade.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("lineage gate", response.get_json()["error"])
        self.assertEqual(self._buffered(handmade.session_id), buffered)
        self.assertEqual(_pane_state(handmade.session_id), before)
        emit.assert_not_called()

    def test_another_agents_pane_is_refused_naming_the_lineage_gate(self):
        caller, target = self._agent_pair()
        sibling = self._pane(group_id=caller.group_id, repo_name="sibling")
        self._fill_buffer(target.session_id)
        buffered = self._buffered(target.session_id)

        response, emit = self._clear(
            target.session_id, {"requested_by_session_id": sibling.session_id}
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("lineage gate", response.get_json()["error"])
        self.assertEqual(self._buffered(target.session_id), buffered)
        emit.assert_not_called()

    def test_an_explorer_pane_is_refused_naming_the_mode_gate(self):
        """The button refreshes an explorer pane; it does not clear one."""
        caller, target = self._agent_pair(
            startup_mode="explorer",
            initial_command_mode="explorer",
            initial_command="",
        )
        before = _pane_state(target.session_id)

        response, emit = self._clear(
            target.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("mode gate", response.get_json()["error"])
        self.assertEqual(_pane_state(target.session_id), before)
        emit.assert_not_called()

    def test_a_pane_running_an_agent_is_refused_naming_the_mode_gate(self):
        """A clear types at the prompt, and there the prompt is the agent."""
        caller, target = self._agent_pair(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="codex",
            initial_command="codex",
        )
        self._fill_buffer(target.session_id)
        buffered = self._buffered(target.session_id)

        response, emit = self._clear(
            target.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("mode gate", response.get_json()["error"])
        self.assertEqual(self._buffered(target.session_id), buffered)
        emit.assert_not_called()

    def test_the_callers_own_pane_is_refused_before_any_purge(self):
        caller, _target = self._agent_pair()
        self._fill_buffer(caller.session_id)
        buffered = self._buffered(caller.session_id)

        response, emit = self._clear(
            caller.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("self gate", response.get_json()["error"])
        self.assertEqual(self._buffered(caller.session_id), buffered)
        emit.assert_not_called()

    def test_a_request_that_names_no_caller_is_refused(self):
        _caller, target = self._agent_pair()
        self._fill_buffer(target.session_id)
        buffered = self._buffered(target.session_id)

        response, emit = self._clear(target.session_id, {})

        self.assertEqual(response.status_code, 400)
        self.assertIn("requested_by_session_id", response.get_json()["error"])
        self.assertEqual(self._buffered(target.session_id), buffered)
        emit.assert_not_called()

    def test_a_caller_pane_that_has_closed_is_refused(self):
        caller, target = self._agent_pair()
        api.session_manager.close_session(caller.session_id)
        api.session_manager.clear_disconnected_sessions()
        self._fill_buffer(target.session_id)
        buffered = self._buffered(target.session_id)

        response, emit = self._clear(
            target.session_id, {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("lineage gate", response.get_json()["error"])
        self.assertEqual(self._buffered(target.session_id), buffered)
        emit.assert_not_called()

    def test_an_unknown_session_is_a_404_and_touches_nothing(self):
        caller, _target = self._agent_pair()

        response, emit = self._clear(
            "no-such-session", {"requested_by_session_id": caller.session_id}
        )

        self.assertEqual(response.status_code, 404)
        emit.assert_not_called()

    # ---------------- override: waives lineage and the agent refusal ----------

    def test_override_clears_a_pane_the_user_created(self):
        caller, _target = self._agent_pair()
        handmade = self._pane(group_id=caller.group_id, repo_name="handmade")
        self._fill_buffer(handmade.session_id)

        response, emit = self._clear(
            handmade.session_id,
            {"requested_by_session_id": caller.session_id, "override": True},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self._buffered(handmade.session_id), "")
        emit.assert_called_once()

    def test_override_clears_a_pane_running_an_agent(self):
        caller, target = self._agent_pair(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="codex",
            initial_command="codex",
        )
        self._fill_buffer(target.session_id)

        response, _emit = self._clear(
            target.session_id,
            {"requested_by_session_id": caller.session_id, "override": True},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self._buffered(target.session_id), "")

    def test_override_never_waives_the_self_gate(self):
        caller, _target = self._agent_pair()
        self._fill_buffer(caller.session_id)
        buffered = self._buffered(caller.session_id)

        response, emit = self._clear(
            caller.session_id,
            {"requested_by_session_id": caller.session_id, "override": True},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("self gate", response.get_json()["error"])
        self.assertEqual(self._buffered(caller.session_id), buffered)
        emit.assert_not_called()

    def test_override_never_waives_the_mode_gate_on_an_explorer_pane(self):
        """There is no terminal there to clear, whoever asked."""
        caller, target = self._agent_pair(
            startup_mode="explorer",
            initial_command_mode="explorer",
            initial_command="",
        )

        response, emit = self._clear(
            target.session_id,
            {"requested_by_session_id": caller.session_id, "override": True},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("mode gate", response.get_json()["error"])
        emit.assert_not_called()

    def test_a_false_override_changes_nothing(self):
        caller, _target = self._agent_pair()
        handmade = self._pane(group_id=caller.group_id, repo_name="handmade")

        response, _emit = self._clear(
            handmade.session_id,
            {"requested_by_session_id": caller.session_id, "override": False},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("lineage gate", response.get_json()["error"])

    # ---------------- the boundary ----------------

    def test_the_service_is_not_a_flask_handler(self):
        """Called directly, with no request context, like its two siblings."""
        caller, target = self._agent_pair()
        effects = web_session_clear.ClearEffects(
            purge_buffer=MagicMock(),
            broadcast_cleared=MagicMock(),
        )

        payload = web_session_clear.apply_agent_pane_clear(
            target.session_id,
            {"requested_by_session_id": caller.session_id},
            effects,
        )

        self.assertTrue(payload["buffer_purged"])
        effects.purge_buffer.assert_called_once_with(target.session_id)
        effects.broadcast_cleared.assert_called_once_with(target.session_id)

    def test_the_purge_happens_before_the_windows_are_told(self):
        """A window told first would replay the buffer it was told to drop."""
        caller, target = self._agent_pair()
        order = []
        effects = web_session_clear.ClearEffects(
            purge_buffer=lambda session_id: order.append("purge"),
            broadcast_cleared=lambda session_id: order.append("broadcast"),
        )

        web_session_clear.apply_agent_pane_clear(
            target.session_id,
            {"requested_by_session_id": caller.session_id},
            effects,
        )

        self.assertEqual(order, ["purge", "broadcast"])

    def test_the_service_never_imports_the_route_module(self):
        """The reason the effects are parameters in the first place."""
        source = _module_source("session_clear.py")

        self.assertNotIn("import web.api", source)
        self.assertNotIn("from web.api", source)
        self.assertNotIn("from web import api", source)

    def test_the_route_maps_the_services_own_status(self):
        handler = next(
            node
            for node in ast.parse(_module_source("api.py")).body
            if isinstance(node, ast.FunctionDef)
            and node.name == "clear_session_for_agent"
        )
        called = {
            node.func.id
            for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("apply_agent_pane_clear", called)
        self.assertIn("jsonify", called)
        self.assertIn("exc.status_code", ast.unparse(handler))

    def test_the_broadcast_is_room_scoped_to_the_pane(self):
        """Every window showing *this* pane, and no other pane anywhere."""
        with patch.object(api.socketio, "emit") as emit:
            api._broadcast_terminal_cleared("pane-7")

        emit.assert_called_once_with(
            "terminal_cleared", {"session_id": "pane-7"}, room="pane-7"
        )


if __name__ == "__main__":  # pragma: no cover - convenience runner
    unittest.main()
