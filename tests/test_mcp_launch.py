"""The GridVibe side of the MCP: the generated config, the flag, the identity.

Four properties, each of which has a way of silently not happening:

- **The generated config names *this* install.** The interpreter GridVibe is
  actually running under and the port it actually bound — not a constant, and
  not something committed. A second start rewrites it rather than appending, so
  a moved install or a changed port self-heals.
- **Pane identity survives `terminal.shell_integration` being off.** That
  setting is a kill switch for the *prompt hook*, because the hook mutates the
  user's prompt. A user who turns it off has said nothing about MCP, and the
  injection point that would have been obvious returns unchanged when it is
  off. This is the regression the call-site injection exists to prevent.
- **Two callers extend the same `WSLENV`.** `wsl.exe` forwards only what that
  variable names, and the prompt hook was already writing it. Each caller
  writing its own value drops the other's.
- **The flag is composed only when the pane asks and the agent publishes one.**
  Seven of the eight registered CLIs publish no MCP block, and their checkbox
  is simply absent — the same thing `opencode` already does for Auto mode.
- **And only on a pane whose shell is on this machine.** The composed line is
  typed into whatever shell the pane holds. On an SSH pane that shell is on
  another host, where the Windows config path resolves against the remote cwd
  and the agent refuses to start at all — so the pane costs the user their
  agent, not just its tools.
"""

import io
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.identity import IDENTITY_VARIABLES  # noqa: E402
from sessions.manager import SessionStatus  # noqa: E402
from web import agents as web_agents  # noqa: E402
from web import (  # noqa: E402
    mcp_launch,
    saved_sessions,
)
from web import terminal_io as terminal  # noqa: E402
from web.terminal_cwd import (  # noqa: E402
    WSLENV_VARIABLE,
    merge_wslenv,
    shell_integration_environment,
)


class GeneratedConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = str(Path(self.temp_dir.name) / ".gridvibe_mcp.json")

    def read(self):
        with io.open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_config_names_this_interpreter_and_this_port(self):
        mcp_launch.write_mcp_config("127.0.0.1", 5051, path=self.path)

        document = self.read()
        server = document["mcpServers"]["gridvibe"]
        self.assertEqual(server["command"], sys.executable)
        self.assertIn("--url", server["args"])
        self.assertEqual(server["args"][server["args"].index("--url") + 1],
                         "http://127.0.0.1:5051")
        # The sidecar entry has to be a real file, or the CLI spawns nothing.
        self.assertTrue(os.path.exists(server["args"][0]))

    def test_the_config_carries_no_env_block(self):
        mcp_launch.write_mcp_config("127.0.0.1", 5050, path=self.path)

        # Identity arrives by inheritance and the URL is in `args`, so the file
        # is identical for every pane and one file serves them all.
        self.assertNotIn("env", self.read()["mcpServers"]["gridvibe"])

    def test_a_second_start_rewrites_rather_than_appends(self):
        mcp_launch.write_mcp_config("127.0.0.1", 5050, path=self.path)
        mcp_launch.write_mcp_config("127.0.0.1", 5099, path=self.path)

        document = self.read()
        self.assertEqual(len(document["mcpServers"]), 1)
        self.assertIn("http://127.0.0.1:5099",
                      document["mcpServers"]["gridvibe"]["args"])

    def test_a_wildcard_bind_becomes_a_loopback_url(self):
        # `0.0.0.0` names no reachable host for a child process on this machine.
        self.assertEqual(
            mcp_launch.loopback_base_url("0.0.0.0", 5050), "http://127.0.0.1:5050"
        )

    def test_a_write_that_fails_costs_the_checkbox_and_nothing_else(self):
        unwritable = str(Path(self.temp_dir.name) / "missing" / "dir" / "config.json")

        self.assertEqual(mcp_launch.write_mcp_config("127.0.0.1", 5050, path=unwritable), "")

    def test_the_suite_can_never_write_the_developers_own_config(self):
        """The file is written by `run_server`, which the suite calls.

        `run_server` takes a host and a port and writes them straight into the
        real `.gridvibe_mcp.json` with a plain `open()` -- no `state_files.py`
        lock, backup or redirection between the suite and the developer's
        install. A run that reached it repointed every agent pane's sidecar at
        whatever address that test passed, and only the next GridVibe start put
        it back.
        """
        self.assertTrue(os.environ.get("GRIDVIBE_TEST_MODE"))
        self.assertNotEqual(
            os.path.abspath(mcp_launch.mcp_config_path()),
            os.path.abspath(mcp_launch.PRODUCTION_MCP_CONFIG_PATH),
        )

    def test_test_mode_without_a_redirect_refuses_rather_than_writing(self):
        # A missed redirect fails loudly instead of quietly owning the real
        # file -- the same guarantee `runtime_state` already makes.
        with patch.dict(os.environ, {"GRIDVIBE_MCP_CONFIG_PATH": ""}):
            with self.assertRaises(RuntimeError):
                mcp_launch.mcp_config_path()

    def test_the_generated_file_is_gitignored(self):
        with io.open(PROJECT_ROOT / ".gitignore", encoding="utf-8") as handle:
            ignored = handle.read()

        # It names this install's absolute paths; committing it publishes them.
        self.assertIn(mcp_launch.MCP_CONFIG_FILENAME, ignored)


class PaneIdentityEnvironmentTestCase(unittest.TestCase):
    """The five variables, at the spawn that carries them."""

    WORKSPACE_ID = "abc123def456"

    def setUp(self):
        self.registry = {}
        for name, value in (
            ("ssh_connections", self.registry),
            ("session_output_buffers", {}),
            ("session_terminal_sizes", {}),
        ):
            patcher = patch.object(terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("session_manager", "_broadcast_session_status"):
            patcher = patch.object(terminal, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.session = self._session()
        self.session_manager.get_session.return_value = self.session
        self.session_manager.groups = {
            "group-1": SimpleNamespace(workspace_id=self.WORKSPACE_ID)
        }
        patcher = patch.object(mcp_launch, "_server_base_url", "http://127.0.0.1:5050")
        patcher.start()
        self.addCleanup(patcher.stop)

    #: What a pane inherits before GridVibe writes anything. Deliberately
    #: carries neither the prompt hook nor any GRIDVIBE_* variable, so every
    #: one observed at the spawn was put there by the code under test.
    BASE_ENVIRONMENT = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": "C:\\Windows"}

    def _session(self, **overrides):
        fields = dict(
            distribution="", username="", directory="", initial_command="",
            use_wsl=False, use_powershell=False, mode="wsl", startup_mode="terminal",
            status=SessionStatus.CONNECTED, group_id="group-1", agent_depth=0,
        )
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def _spawn_environment(self, shell_integration=True):
        """Run the Windows local connector to the spawn, and report its env.

        The connector spawns from ``os.environ``, so the suite supplies a bare
        one. Not tidiness: GridVibe's own suite is routinely run *inside* a
        GridVibe pane, and that pane's shell already exports the prompt hook
        and the five identity variables. Inheriting them leaves "the hook is
        off" unprovable -- the value is there either way -- and the assertion
        then fails for the one reason that says nothing about the code.
        """
        winpty = MagicMock()
        self.session_manager.get_session.return_value = self.session
        with patch.dict(terminal.os.environ, self.BASE_ENVIRONMENT, clear=True), \
                patch.object(terminal.os, "name", "nt"), \
                patch.object(terminal, "WinPtyProcess", winpty), \
                patch.object(terminal.runtime_config, "terminal_shell_integration",
                             shell_integration), \
                patch.object(terminal, "_drain_until_prompt"), \
                patch.object(terminal, "_run_startup_sequence"), \
                patch.object(terminal, "_stream_local_output"):
            terminal._connect_local_session("pane-1", self.session)
        return winpty.spawn.call_args.kwargs["env"]

    def test_a_pane_carries_its_own_identity_into_every_child(self):
        environment = self._spawn_environment()

        self.assertEqual(environment["GRIDVIBE_SESSION_ID"], "pane-1")
        self.assertEqual(environment["GRIDVIBE_GROUP_ID"], "group-1")
        self.assertEqual(environment["GRIDVIBE_WORKSPACE_ID"], self.WORKSPACE_ID)
        self.assertEqual(environment["GRIDVIBE_URL"], "http://127.0.0.1:5050")
        self.assertEqual(environment["GRIDVIBE_AGENT_DEPTH"], "0")

    def test_identity_survives_the_prompt_hook_being_switched_off(self):
        """§1.3, as a regression.

        `terminal.shell_integration` is a kill switch for the prompt hook,
        because the hook mutates the user's prompt. A user who turns it off has
        said nothing about MCP, and would otherwise silently get panes whose
        agents cannot tell what workspace they are in -- with no error anywhere.
        """
        environment = self._spawn_environment(shell_integration=False)

        for name in IDENTITY_VARIABLES:
            with self.subTest(variable=name):
                self.assertIn(name, environment)
        # The hook itself is genuinely off, which is what the setting asked
        # for. (`PROMPT` exists in any inherited cmd environment; what must be
        # absent is GridVibe's own hook value.)
        hook = shell_integration_environment("cmd")["PROMPT"]
        self.assertNotEqual(environment.get("PROMPT"), hook)
        self.assertEqual(self._spawn_environment().get("PROMPT"), hook)

    def test_an_agent_launched_pane_carries_the_depth_it_was_stamped_with(self):
        self.session = self._session(agent_depth=1)

        self.assertEqual(self._spawn_environment()["GRIDVIBE_AGENT_DEPTH"], "1")

    def test_a_wsl_pane_forwards_both_the_hook_and_the_identity(self):
        self.session = self._session(use_wsl=True, distribution="Ubuntu")

        environment = self._spawn_environment()

        forwarded = set(environment[WSLENV_VARIABLE].split(":"))
        # `wsl.exe` forwards only what WSLENV names; each caller writing its own
        # value would drop the other's.
        self.assertIn("PROMPT_COMMAND", forwarded)
        for name in IDENTITY_VARIABLES:
            with self.subTest(variable=name):
                self.assertIn(name, forwarded)


class MergeWslenvTestCase(unittest.TestCase):
    def test_an_existing_entry_is_kept_including_its_flag_suffix(self):
        merged = merge_wslenv("PATH/l:PROMPT_COMMAND", ("PROMPT_COMMAND", "GRIDVIBE_URL"))

        self.assertEqual(merged, "PATH/l:PROMPT_COMMAND:GRIDVIBE_URL")

    def test_a_name_is_never_listed_twice(self):
        merged = merge_wslenv("GRIDVIBE_URL", ("GRIDVIBE_URL", "GRIDVIBE_URL"))

        self.assertEqual(merged, "GRIDVIBE_URL")

    def test_the_prompt_hook_still_writes_its_own_entry(self):
        environment = shell_integration_environment("wsl", {WSLENV_VARIABLE: "PATH/l"})

        self.assertEqual(environment[WSLENV_VARIABLE], "PATH/l:PROMPT_COMMAND")


class SavedPresetTestCase(unittest.TestCase):
    """What a preset may carry forward.

    The third way in, after the launcher checkbox and the relaunch route. A
    preset is read back long after it was written, on an install whose venv,
    port and connection mode may all have moved -- so the local-only rule is
    enforced where the entry is normalized rather than trusted from the file.
    """

    def _preset(self, connection_mode, **overrides):
        entry = dict(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
            initial_command="claude",
            agent_mcp=True,
        )
        entry.update(overrides)
        return saved_sessions._normalize_terminal_entries(
            [entry], connection_mode, minimum_count=1
        )[0]

    def test_a_local_preset_keeps_the_choice(self):
        self.assertTrue(self._preset("wsl")["agent_mcp"])

    def test_an_ssh_preset_carries_no_mcp_however_it_was_written(self):
        # A preset hand-edited, or written by a build before the rule existed,
        # still reads back as False rather than launching a broken agent.
        self.assertFalse(self._preset("ssh")["agent_mcp"])

    def test_a_non_agent_preset_carries_no_mcp_either(self):
        # The pre-existing rule, still held: no CLI, nothing to register with.
        self.assertFalse(
            self._preset("wsl", startup_mode="terminal", initial_command_mode="command")["agent_mcp"]
        )


class FlagCompositionTestCase(unittest.TestCase):
    """What ends up on the pane's launch line."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / ".gridvibe_mcp.json"
        self.config_path.write_text("{}", encoding="utf-8")
        patcher = patch.object(
            mcp_launch, "mcp_config_path", lambda: str(self.config_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pane(self, **overrides):
        # `mode="wsl"` is the *local* family (cmd/PowerShell/WSL), which is the
        # only place the sidecar can run. Stated rather than defaulted, because
        # it is now one of the things composition reads.
        fields = dict(
            initial_command="claude", initial_command_mode="agent",
            agent_selection="claude", agent_auto_mode=False, agent_mcp=False,
            mode="wsl",
        )
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def test_a_pane_that_asked_gets_the_flag_pointing_at_the_generated_config(self):
        command = web_agents._compose_agent_startup_command(self._pane(agent_mcp=True))

        self.assertEqual(command, f'claude --mcp-config "{self.config_path}"')
        # Additive: `--strict-mcp-config` would silently cost the user every
        # MCP server they had registered, inside GridVibe panes only.
        self.assertNotIn("--strict-mcp-config", command)

    def test_a_pane_that_did_not_ask_gets_nothing(self):
        self.assertEqual(
            web_agents._compose_agent_startup_command(self._pane()), "claude"
        )

    def test_an_agent_with_no_registry_block_gets_nothing(self):
        # `opencode` publishes neither an auto_mode nor an mcp block today.
        pane = self._pane(
            initial_command="opencode", agent_selection="opencode", agent_mcp=True
        )

        self.assertEqual(web_agents._compose_agent_startup_command(pane), "opencode")

    def test_a_missing_config_file_costs_the_flag_rather_than_the_agent(self):
        self.config_path.unlink()

        # Pointing a CLI at a config that is not there costs the user their
        # agent, which is worse than quietly having no tools.
        self.assertEqual(
            web_agents._compose_agent_startup_command(self._pane(agent_mcp=True)),
            "claude",
        )

    def test_a_registry_flag_that_is_not_a_plain_option_resolves_to_nothing(self):
        for flag in ("--mcp-config; rm -rf /", "$(evil) {config}", "--mcp-config"):
            with self.subTest(flag=flag):
                with patch.dict(
                    web_agents.AGENT_REGISTRY["claude"],
                    {"mcp": {"flag": flag, "description": "x"}},
                ):
                    self.assertEqual(web_agents._agent_mcp_flag("claude"), "")

    def test_auto_mode_and_mcp_compose_together(self):
        command = web_agents._compose_agent_startup_command(
            self._pane(agent_auto_mode=True, agent_mcp=True)
        )

        self.assertIn("--permission-mode auto", command)
        self.assertIn("--mcp-config", command)

    def test_an_ssh_pane_gets_no_flag_however_the_field_was_set(self):
        # The config path exists *here*. Typed into a remote shell it resolves
        # against that host's cwd, and the CLI exits with
        # "MCP config file not found" before the agent ever starts.
        pane = self._pane(agent_mcp=True, mode="ssh")

        command = web_agents._compose_agent_startup_command(pane)

        self.assertEqual(command, "claude")
        self.assertNotIn("--mcp-config", command)
        self.assertNotIn(str(self.config_path), command)

    def test_a_pane_with_no_mode_at_all_is_treated_as_remote(self):
        # Absent is not local. A pane record too old or too partial to say
        # where its shell runs gets the answer that cannot break the agent.
        pane = self._pane(agent_mcp=True)
        del pane.mode

        self.assertEqual(web_agents._compose_agent_startup_command(pane), "claude")

    def test_an_ssh_pane_keeps_every_other_composed_flag(self):
        # The refusal is the MCP fragment alone -- auto mode is a flag the
        # remote CLI understands perfectly well.
        command = web_agents._compose_agent_startup_command(
            self._pane(agent_auto_mode=True, agent_mcp=True, mode="ssh")
        )

        self.assertEqual(command, "claude --permission-mode auto")

    def test_the_launcher_is_told_which_agents_publish_a_block(self):
        options = {option["value"]: option for option in web_agents._agent_options()}

        self.assertEqual(options["claude"]["mcp_flag"], "--mcp-config {config}")
        self.assertTrue(options["claude"]["mcp_description"])
        # No block, no checkbox. That is the whole mechanism for the seven CLIs
        # whose MCP support has not been verified against a current release.
        self.assertEqual(options["opencode"]["mcp_flag"], "")
        self.assertEqual(options["other"]["mcp_flag"], "")


if __name__ == "__main__":
    unittest.main()
