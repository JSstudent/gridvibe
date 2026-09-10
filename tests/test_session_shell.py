"""The pane relaunch transaction: shell family, agent, and the boundary.

`change_session_shell()` used to answer one question inside `web/api.py` --
"restart this Local Repo pane under another shell family". The header's reset
dropdown now offers a second, independent choice on the same rows (which agent
CLI the replacement shell starts), and an SSH pane has that second choice even
though it has no shell family at all, so the transaction moved to
`web/session_shell.py` and the route kept request parsing and status mapping.

The eleven `/shell` cases already in `tests/test_api.py` are the shell half's
characterization and must keep passing untouched. This file pins what the
second dimension added, and what the boundary itself has to hold:

- **Each dimension is a tri-state, not a value with a default.** An unstated
  `shell` leaves the pane's shell alone (that is what an SSH relaunch and an
  agent-only relaunch both send), and an unstated `agent` leaves its agent
  alone (that is what every request looked like before this dimension existed).
  A stated `""` agent is a *choice* of no agent, and only that clears one.
- **A refusal is atomic.** Each one is asserted on the response *and* on the
  whole pane, so a mutation that leaked ahead of a validation shows up here.
- **The service is not a Flask handler**, and the route maps the error's own
  status rather than a fixed 400.
- **The three side effects stay resolvable through `web.api`**, which is why
  `patch.object(api, "_close_ssh_connection")` still reaches the transaction.
"""

import ast
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import api
from web import agents as web_agents
from web import config as web_config
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import session_shell as web_session_shell
from web import terminal_io as web_terminal_io

#: Every field the relaunch is allowed to touch, so a refusal can be asserted
#: on the whole pane rather than on whichever field the test happened to guess.
_PANE_FIELDS = (
    "host",
    "directory",
    "current_directory",
    "distribution",
    "use_wsl",
    "use_powershell",
    "startup_mode",
    "initial_command",
    "initial_command_mode",
    "agent_selection",
    "custom_agent",
    "agent_auto_mode",
    "status",
)

_WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)


def _module_source(filename):
    """Read one `web/` module's source for a structural assertion."""
    with io.open(os.path.join(_WEB_DIR, filename), encoding="utf-8") as handle:
        return handle.read()


def _pane_state(session_id):
    """Snapshot the pane fields a relaunch can move."""
    session = api.session_manager.get_session(session_id)
    return {
        name: json.loads(json.dumps(getattr(session, name), default=str))
        for name in _PANE_FIELDS
    }


class ShellTransitionTestCase(unittest.TestCase):
    """Shared fixture: a real app client over temporary durable state."""

    def setUp(self):
        from tempfile import TemporaryDirectory

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

    def _local_pane(self, **overrides):
        """One Local Repo terminal pane rooted in a real temporary directory."""
        repo_dir = Path(self.temp_dir.name) / overrides.pop("repo_name", "repo")
        repo_dir.mkdir(exist_ok=True)
        group = api.session_manager.create_group(
            name="Local", connection_mode="wsl", layout="single", terminal_count=1
        )
        fields = {
            "group_id": group.group_id,
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
        return api.session_manager.get_session(session.session_id), repo_dir

    def _ssh_pane(self, **overrides):
        """One connected SSH terminal pane."""
        group = api.session_manager.create_group(
            name="SSH", connection_mode="ssh", layout="single", terminal_count=1
        )
        fields = {
            "group_id": group.group_id,
            "host": "example.com",
            "directory": "/srv/app",
            "mode": "ssh",
            "username": "ubuntu",
            "startup_mode": "terminal",
        }
        fields.update(overrides)
        session = api.session_manager.create_session(**fields)
        api.session_manager.update_session_status(
            session.session_id, api.SessionStatus.CONNECTED
        )
        return api.session_manager.get_session(session.session_id)

    def _post_shell(self, session_id, body, os_name="nt", detection=None):
        """POST one relaunch with every side effect observable.

        The agent probe is the one thing stubbed: `detection` stands in for
        what `_detect_agent_binary_cached()` reports about the *agent's own*
        binary, so the real preflight composes the verdict and the real guard
        reads it. Everything it is asked about anything else (an install
        prerequisite such as npm) answers found, so a stubbed absence is the
        agent's and not a prerequisite's. Installed by default: an agent this
        machine happens not to have installed is not a fixture.
        """
        found = dict(detection or {"found": True, "path": "/usr/bin/agent"})
        probes = []

        def _detect(target, binary):
            probes.append((dict(target), binary))
            registry_binaries = {
                str((spec or {}).get("binary") or key)
                for key, spec in web_agents.AGENT_REGISTRY.items()
            }
            if binary not in registry_binaries:
                return {"found": True, "path": f"/usr/bin/{binary}"}
            return dict(found)

        with patch.object(api.os, "name", os_name), patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd", return_value=None
        ), patch.object(
            web_agents, "_detect_agent_binary_cached", side_effect=_detect
        ), patch.object(
            api, "_close_ssh_connection"
        ) as close_connection, patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session_id}/shell", json=body
            )
        self.agent_probes = probes
        return response, close_connection, start_task


class PaneAgentRelaunchTestCase(ShellTransitionTestCase):
    """The dimension the reset dropdown's chevrons added."""

    def test_an_ssh_pane_relaunches_into_an_agent_without_naming_a_shell(self):
        """An SSH pane has no shell family, so its rows state only the agent."""
        session = self._ssh_pane()

        response, close_connection, start_task = self._post_shell(
            session.session_id, {"agent": "claude"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        close_connection.assert_called_once_with(session.session_id, clear_buffer=True)
        start_task.assert_called_once_with(api._connect_session, session.session_id)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "agent")
        self.assertEqual(updated.initial_command_mode, "agent")
        self.assertEqual(updated.agent_selection, "claude")
        self.assertEqual(updated.initial_command, "claude")
        self.assertEqual(updated.status, api.SessionStatus.PENDING)

    def test_an_agent_row_carries_its_shell_family_with_it(self):
        """The chevron's rows are that family's agents, so both are stated."""
        session, _repo = self._local_pane()

        response, _close, _start = self._post_shell(
            session.session_id,
            {"shell": "wsl", "distribution": "Ubuntu", "agent": "codex"},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        updated = api.session_manager.get_session(session.session_id)
        self.assertTrue(updated.use_wsl)
        self.assertEqual(updated.distribution, "Ubuntu")
        self.assertEqual(updated.host, "WSL (Ubuntu)")
        self.assertEqual(updated.startup_mode, "agent")
        self.assertEqual(updated.agent_selection, "codex")

    def test_a_stated_empty_agent_returns_an_agent_pane_to_a_plain_shell(self):
        """The shell row itself is "relaunch this family plainly"."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
            agent_auto_mode=True,
        )

        response, close_connection, _start = self._post_shell(
            session.session_id, {"shell": "cmd", "agent": ""}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        close_connection.assert_called_once()
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.initial_command_mode, "command")
        self.assertEqual(updated.agent_selection, "")
        self.assertEqual(updated.initial_command, "")
        self.assertFalse(updated.agent_auto_mode)

    def test_an_unstated_agent_leaves_the_panes_agent_exactly_as_it_was(self):
        """The shell half's own contract: a shell switch is not an agent change.

        This is the request every caller sent before the second dimension
        existed, and it must still mean what it meant then.
        """
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
            agent_auto_mode=True,
        )

        response, _close, _start = self._post_shell(
            session.session_id, {"shell": "powershell"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        updated = api.session_manager.get_session(session.session_id)
        self.assertTrue(updated.use_powershell)
        self.assertEqual(updated.startup_mode, "agent")
        self.assertEqual(updated.agent_selection, "claude")
        self.assertTrue(updated.agent_auto_mode)

    def test_a_stated_empty_agent_leaves_a_plain_startup_command_alone(self):
        """A startup command is not an agent, so "no agent" is already true.

        A pane launched with `npm run dev` has nothing to clear, and reading
        its command as an agent would silently drop it on the first relaunch.
        """
        session, _repo = self._local_pane(initial_command="npm run dev")
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id, {"shell": "cmd", "agent": ""}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        after = _pane_state(session.session_id)
        self.assertEqual(
            {key: value for key, value in after.items() if key != "status"},
            {key: value for key, value in before.items() if key != "status"},
        )
        self.assertEqual(after["status"], str(api.SessionStatus.PENDING))
        close_connection.assert_called_once_with(
            session.session_id, clear_buffer=True
        )
        start_task.assert_called_once_with(api._connect_session, session.session_id)

    def test_relaunching_the_same_agent_under_another_shell_keeps_auto_mode(self):
        """Auto mode belongs to the agent, and this pane still runs that agent."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
            agent_auto_mode=True,
        )

        response, _close, _start = self._post_shell(
            session.session_id, {"shell": "powershell", "agent": "claude"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        updated = api.session_manager.get_session(session.session_id)
        self.assertTrue(updated.use_powershell)
        self.assertTrue(updated.agent_auto_mode)

    def test_moving_to_a_different_agent_starts_from_its_plain_launch(self):
        """A flag registered for one CLI says nothing about the next one."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
            agent_auto_mode=True,
        )

        response, _close, _start = self._post_shell(
            session.session_id, {"agent": "codex"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.agent_selection, "codex")
        self.assertFalse(updated.agent_auto_mode)

    def test_reselecting_the_running_agent_and_shell_relaunches_it(self):
        """The checked entries remain actions rather than disabled selectors."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
        )
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id, {"shell": "cmd", "agent": "claude"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        after = _pane_state(session.session_id)
        self.assertEqual(
            {key: value for key, value in after.items() if key != "status"},
            {key: value for key, value in before.items() if key != "status"},
        )
        self.assertEqual(after["status"], str(api.SessionStatus.PENDING))
        close_connection.assert_called_once_with(
            session.session_id, clear_buffer=True
        )
        start_task.assert_called_once_with(api._connect_session, session.session_id)

    def test_payload_stating_neither_dimension_is_a_noop(self):
        """Only an explicit shell or agent choice requests a relaunch."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
        )
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id, {}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_an_agent_only_relaunch_keeps_the_panes_observed_directory(self):
        """Only a shell-family change retargets the pane's directory.

        An agent relaunch is a reconnect: the startup sequence replays the
        observed directory with the launch directory as its own fallback, so
        clearing the observation here would send the pane back to where it
        started for no reason.
        """
        session = self._ssh_pane(current_directory="/srv/app/worker")

        response, _close, _start = self._post_shell(
            session.session_id, {"agent": "claude"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.current_directory, "/srv/app/worker")
        self.assertEqual(updated.directory, "/srv/app")

    def test_an_unregistered_agent_is_refused_and_mutates_nothing(self):
        session, _repo = self._local_pane()
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id, {"shell": "cmd", "agent": "definitely-not-an-agent"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("known agent CLI", response.get_json()["error"])
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_an_ssh_pane_still_refuses_a_local_shell_family(self):
        """The agent dimension did not widen the shell one."""
        session = self._ssh_pane()
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id, {"shell": "powershell", "agent": "claude"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Local Repo", response.get_json()["error"])
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_local_pane_on_a_posix_host_may_still_choose_an_agent(self):
        """Naming no shell family is exactly how a POSIX pane asks for one.

        The Windows-only refusal guards the shell dimension, so it must not
        reach a request that never stated one.
        """
        session, _repo = self._local_pane()

        response, close_connection, _start = self._post_shell(
            session.session_id, {"agent": "claude"}, os_name="posix"
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        close_connection.assert_called_once()
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.agent_selection, "claude")

    def test_a_pane_with_no_shell_of_its_own_is_refused(self):
        """`mode` is not updatable metadata, so the pane is built that way."""
        session, _repo = self._local_pane(mode="local")
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id, {"agent": "claude"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no shell to relaunch", response.get_json()["error"])
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()


class PaneAgentPreflightTestCase(ShellTransitionTestCase):
    """The launcher's install check, asked by the pane menu.

    The launcher answers "is this binary there" before a pane opens and, when
    it is not, opens the pane as a plain terminal. The reset dropdown is the
    other way into the same launch and used to ask nothing at all: the pane was
    renamed after the agent, the dashboard listed it, and only the shell's own
    error -- read whenever the reader next looked -- said otherwise. Here the
    same check refuses instead, because the pane the reader is looking at is
    already running and a refusal costs nothing.
    """

    _MISSING = {"found": False, "path": ""}
    _CHECK_FAILED = {
        "found": False,
        "path": "",
        "failed": True,
        "error": "probe blew up",
    }

    def test_an_agent_that_is_not_installed_is_refused_and_mutates_nothing(self):
        session, _repo = self._local_pane()
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_shell(
            session.session_id,
            {"shell": "cmd", "agent": "codex"},
            detection=self._MISSING,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_the_refusal_names_the_agent_the_target_and_what_it_did_not_do(self):
        """The message is the toast, so it has to say all three."""
        session, _repo = self._local_pane()

        response, _close, _start = self._post_shell(
            session.session_id,
            {"shell": "cmd", "agent": "codex"},
            detection=self._MISSING,
        )

        error = response.get_json()["error"]
        self.assertIn("OpenAI Codex CLI", error)
        self.assertIn("cmd", error)
        self.assertIn("left as it is", error)

    def test_the_probe_asks_about_the_shell_family_the_row_would_launch(self):
        """A WSL row's agent lives in that distro, not in the pane's cmd."""
        session, _repo = self._local_pane()

        response, _close, _start = self._post_shell(
            session.session_id,
            {"shell": "wsl", "distribution": "Ubuntu", "agent": "codex"},
            detection=self._MISSING,
        )

        self.assertEqual(response.status_code, 400)
        agent_probes = [probe for probe in self.agent_probes if probe[1] == "codex"]
        self.assertTrue(agent_probes)
        target, _binary = agent_probes[0]
        self.assertEqual(target["environment_key"], "wsl_linux")
        self.assertEqual(target["shell_kind"], "wsl")
        self.assertEqual(target["distribution"], "Ubuntu")

    def test_an_ssh_pane_is_probed_on_its_own_host(self):
        """The pane already holds the target; nothing is re-asked of the client."""
        session = self._ssh_pane(username="deploy", port=2222)

        response, _close, _start = self._post_shell(
            session.session_id, {"agent": "claude"}, detection=self._MISSING
        )

        self.assertEqual(response.status_code, 400)
        target, _binary = next(
            probe for probe in self.agent_probes if probe[1] == "claude"
        )
        self.assertEqual(target["environment_key"], "ssh")
        self.assertEqual(target["host"], "example.com")
        self.assertEqual(target["username"], "deploy")
        self.assertEqual(target["port"], 2222)

    def test_a_check_that_could_not_run_is_not_an_absence(self):
        """`check_failed` says nothing about the binary, so the relaunch stands.

        Refusing here would turn a broken probe into a pane that cannot be
        relaunched at all, and the reader still gets the shell's own error.
        """
        session, _repo = self._local_pane()

        response, close_connection, start_task = self._post_shell(
            session.session_id,
            {"shell": "cmd", "agent": "codex"},
            detection=self._CHECK_FAILED,
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        close_connection.assert_called_once()
        start_task.assert_called_once()
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.agent_selection, "codex")

    def test_returning_a_pane_to_a_plain_shell_probes_nothing(self):
        """There is no binary to look for, and no reason to wait for a probe."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
        )

        response, close_connection, _start = self._post_shell(
            session.session_id, {"shell": "cmd", "agent": ""}, detection=self._MISSING
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        close_connection.assert_called_once()
        self.assertEqual(self.agent_probes, [])

    def test_a_shell_only_relaunch_probes_nothing(self):
        """An unstated agent is not a launch of one, whatever the pane runs."""
        session, _repo = self._local_pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
        )

        response, _close, _start = self._post_shell(
            session.session_id, {"shell": "powershell"}, detection=self._MISSING
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.agent_probes, [])
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.agent_selection, "claude")

    def test_the_pane_menu_and_the_launcher_read_one_verdict(self):
        """Both read the same registry check; a second one could disagree."""
        self.assertIs(
            web_session_shell._agent_absent_reason, web_agents._agent_absent_reason
        )
        launcher_sessions = [
            {
                "title": "Terminal 1",
                "initial_command": "codex",
                "use_wsl": True,
                "distribution": "Ubuntu",
                "startup_mode": "agent",
                "initial_command_mode": "agent",
                "agent_selection": "codex",
            }
        ]
        with patch.object(
            web_agents, "_detect_agent_binary_cached", return_value=dict(self._MISSING)
        ):
            reason = web_agents._agent_absent_reason(
                "codex", "wsl", {"use_wsl": True, "distribution": "Ubuntu"}
            )
            warnings = web_agents._sanitize_agent_launch_commands(
                "wsl", launcher_sessions
            )

        self.assertTrue(reason)
        self.assertTrue(warnings)
        self.assertIn(reason.rstrip(), warnings[0])
        # The launcher has no pane to keep, so it opens one as a terminal; the
        # menu, with the pane already open, keeps it exactly as it was.
        self.assertEqual(launcher_sessions[0]["startup_mode"], "terminal")


class ShellTransitionBoundaryTestCase(ShellTransitionTestCase):
    """What the extraction itself has to hold, beyond behaviour parity."""

    def test_the_service_answers_without_a_request_context(self):
        """No Flask globals cross the boundary: a plain dict comes back."""
        session = self._ssh_pane()
        calls = []
        effects = web_session_shell.ShellTransitionEffects(
            close_connection=lambda *args, **kwargs: calls.append(("close", args, kwargs)),
            broadcast_status=lambda pane_id: calls.append(("broadcast", pane_id)),
            start_connector=lambda pane_id: calls.append(("start", pane_id)),
        )

        with patch.object(
            web_agents,
            "_detect_agent_binary_cached",
            return_value={"found": True, "path": "/usr/bin/claude"},
        ):
            payload = web_session_shell.apply_pane_shell_change(
                session.session_id, {"agent": "claude"}, effects
            )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["agent_selection"], "claude")
        self.assertEqual(
            calls,
            [
                ("close", (session.session_id,), {"clear_buffer": True}),
                ("broadcast", session.session_id),
                ("start", session.session_id),
            ],
        )

    def test_the_service_refuses_with_its_own_status_rather_than_a_response(self):
        """The 404 rather than a 400 on purpose: the status travels with the error."""
        effects = web_session_shell.ShellTransitionEffects(
            close_connection=MagicMock(),
            broadcast_status=MagicMock(),
            start_connector=MagicMock(),
        )

        with self.assertRaises(web_session_shell.ShellTransitionError) as raised:
            web_session_shell.apply_pane_shell_change(
                "no-such-session", {"agent": "claude"}, effects
            )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.message, "Session not found")
        effects.close_connection.assert_not_called()
        effects.broadcast_status.assert_not_called()
        effects.start_connector.assert_not_called()

    def test_the_route_maps_the_services_status_and_not_a_fixed_400(self):
        session = self._ssh_pane()

        def _refuse(*_args, **_kwargs):
            raise web_session_shell.ShellTransitionError("channel closed", 500)

        with patch.object(api, "apply_pane_shell_change", _refuse):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/shell", json={"agent": "claude"}
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "channel closed"})

    def test_the_service_never_imports_the_flask_module_it_was_cut_out_of(self):
        """`web.api` imports `web.session_shell`; the reverse would cycle."""
        tree = ast.parse(_module_source("session_shell.py"))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        self.assertNotIn("web.api", imported)
        self.assertNotIn("api", imported)
        self.assertIs(
            api.apply_pane_shell_change, web_session_shell.apply_pane_shell_change
        )

    def test_the_route_is_http_adaptation_and_nothing_else(self):
        """A line ceiling, so the transaction cannot creep back across it."""
        tree = ast.parse(_module_source("api.py"))
        handler = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "change_session_shell"
        )

        self.assertLess(handler.end_lineno - handler.lineno, 40)
        called = {
            node.func.id
            for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("apply_pane_shell_change", called)
        self.assertIn("jsonify", called)


if __name__ == "__main__":
    unittest.main()
