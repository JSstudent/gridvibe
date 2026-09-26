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
- **A remote pane names the config on its *own* host.** The composed line is
  typed into whatever shell the pane holds. A local pane names the generated
  file here; a tunnelled SSH pane names what its tunnel wrote over there. A
  remote pane with no tunnel gets no fragment at all rather than a path it
  cannot read — which costs the tools, never the agent.
- **A `-c` override is quoted for the shell that will read it.** The two
  Windows shells genuinely disagree, verified against the installed CLI: bare
  single quotes are what cmd must see (wrapping them in double quotes reaches
  Codex as a value it silently declines to apply), and the outer double quotes
  are what PowerShell must see (bare, it eats the brackets itself and Codex
  exits with *failed to load bootstrap configuration*). No single string
  serves both, so the composition reads the pane's shell family -- and bare is
  not available for a value holding a space, which cmd's own command line hands
  on for the child to split into tokens Codex then applies none of.
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
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.client import normalize_base_url  # noqa: E402
from gridvibe_mcp.identity import IDENTITY_VARIABLES  # noqa: E402
from sessions.manager import SessionStatus  # noqa: E402
from web import agents as web_agents  # noqa: E402
from web import (  # noqa: E402
    mcp_launch,
    saved_sessions,
)
from web import terminal_io as terminal  # noqa: E402
from web.agent_session_hooks import PANE_TOKEN_VARIABLE  # noqa: E402
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

    def test_an_ipv6_bind_is_written_as_a_url_that_parses(self):
        """`http://::1:5050` is not a URL.

        Everything that reads one splits the host from the port at the last
        colon, so an explicit `::1` bind produced a config whose address had no
        port at all -- and every pane's sidecar started against something that
        does not exist. Brackets are what separate the two.
        """
        url = mcp_launch.loopback_base_url("::1", 5050)

        self.assertEqual(url, "http://[::1]:5050")
        parsed = urlsplit(url)
        self.assertEqual(parsed.hostname, "::1")
        self.assertEqual(parsed.port, 5050)

    def test_an_already_bracketed_bind_is_not_bracketed_twice(self):
        self.assertEqual(
            mcp_launch.loopback_base_url("[::1]", 5050), "http://[::1]:5050"
        )

    def test_the_ipv6_wildcard_still_names_a_host_a_child_can_reach(self):
        # `::` is every interface, which is not an address to dial.
        self.assertEqual(
            mcp_launch.loopback_base_url("::", 5050), "http://127.0.0.1:5050"
        )
        self.assertEqual(
            mcp_launch.loopback_base_url("[::]", 5050), "http://127.0.0.1:5050"
        )

    def test_the_sidecar_keeps_the_brackets_it_is_handed(self):
        """The two halves have to agree: the sidecar normalizes the URL it is
        given, and rebuilding one from a parsed host drops the brackets unless
        they are put back."""
        self.assertEqual(
            normalize_base_url(mcp_launch.loopback_base_url("::1", 5050)),
            "http://[::1]:5050",
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
        # Exactly the published list, not merely a superset: a sixth GRIDVIBE_*
        # forwarded here and absent from the tuple is drift the sidecar would
        # never report, because it simply would not read it.
        self.assertEqual(
            {name for name in forwarded if name.startswith("GRIDVIBE_")},
            set(IDENTITY_VARIABLES),
        )

    def test_the_spawn_carries_no_gridvibe_variable_the_list_omits(self):
        """The real connector's environment, at the real spawn."""
        with patch.object(
            terminal.runtime_config, "workspace_agent_conversation_restore", False
        ):
            environment = self._spawn_environment()

        self.assertEqual(
            {name for name in environment if name.startswith("GRIDVIBE_")},
            set(IDENTITY_VARIABLES),
        )

        # The one addition is named rather than tolerated: the session-hook
        # token is read by `utils/agent_session_hook.py`, never the sidecar,
        # and only the experimental conversation restore hands it out.
        with patch.object(
            terminal.runtime_config, "workspace_agent_conversation_restore", True
        ):
            environment = self._spawn_environment()
        self.assertEqual(
            {name for name in environment if name.startswith("GRIDVIBE_")},
            set(IDENTITY_VARIABLES) | {PANE_TOKEN_VARIABLE},
        )


class PublishedIdentityListTestCase(unittest.TestCase):
    """`IDENTITY_VARIABLES` is the published list, pinned rather than shared.

    Nothing imports it: the sidecar is a sibling of GridVibe, so
    `pane_identity_environment` states the five names itself and `read_identity`
    states them a third time. The tuple therefore only *claimed* to be the one
    source of truth -- and a sixth variable added on the injector's side alone
    would have been read as `""` by the sidecar forever, with no error
    anywhere. These are the assertions that make the claim true.
    """

    def test_the_injector_writes_exactly_the_published_list(self):
        injected = mcp_launch.pane_identity_environment(
            session_id="pane-1",
            group_id="group-1",
            workspace_id="abc123def456",
            agent_depth=1,
            base_url="http://127.0.0.1:5050",
        )

        self.assertEqual(set(injected), set(IDENTITY_VARIABLES))

    def test_the_wslenv_forward_list_is_the_same_list(self):
        """`wsl.exe` forwards only what WSLENV names, so a name missing here is
        a variable that exists on Windows and not one step across."""
        identity = mcp_launch.pane_identity_environment(session_id="pane-1")

        merged = mcp_launch.apply_pane_identity({}, identity, shell_kind="wsl")

        self.assertEqual(
            set(merged[WSLENV_VARIABLE].split(":")), set(IDENTITY_VARIABLES)
        )


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

    def test_an_ssh_preset_keeps_the_choice_too(self):
        # A remote pane's tools arrive over its own SSH reverse tunnel, so the
        # preset records what was asked for and the connection answers it.
        self.assertTrue(self._preset("ssh")["agent_mcp"])

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
        # it is now one of the things composition reads, and so is the shell
        # family: a `-c` override has to be quoted for the shell that reads it.
        fields = dict(
            initial_command="claude", initial_command_mode="agent",
            agent_selection="claude", agent_auto_mode=False, agent_mcp=False,
            mode="wsl", use_wsl=False, use_powershell=False,
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

    def test_a_remote_pane_with_no_tunnel_gets_no_flag(self):
        # The local config path exists *here*. Typed into a remote shell it
        # resolves against that host's cwd and the CLI exits with "MCP config
        # file not found" before the agent starts, so a pane whose tunnel
        # never opened is given nothing at all.
        pane = self._pane(agent_mcp=True, mode="ssh")

        command = web_agents._compose_agent_startup_command(pane)

        self.assertEqual(command, "claude")
        self.assertNotIn("--mcp-config", command)
        self.assertNotIn(str(self.config_path), command)

    def test_a_pane_with_no_mode_at_all_is_treated_as_remote(self):
        # Absent is not local. A pane record too old or too partial to say
        # where its shell runs gets the answer that cannot break the agent:
        # no local path, and no tunnel was passed, so no fragment.
        pane = self._pane(agent_mcp=True)
        del pane.mode

        self.assertEqual(web_agents._compose_agent_startup_command(pane), "claude")

    def test_an_ssh_pane_keeps_every_other_composed_flag(self):
        # Only the MCP fragment depends on the tunnel -- auto mode is a flag
        # the remote CLI understands perfectly well either way.
        command = web_agents._compose_agent_startup_command(
            self._pane(agent_auto_mode=True, agent_mcp=True, mode="ssh")
        )

        self.assertEqual(command, "claude --permission-mode auto")

    def test_copilot_gets_its_path_marker_inside_the_quotes(self):
        r"""`@"C:\..."` would start a here-string in PowerShell.

        Copilot marks a file path (rather than inline JSON) with a leading
        `@`. Placing it outside the quote is a PowerShell parse error, so the
        quote opens first and the CLI still receives the `@path` it asked for.
        """
        fragment = web_agents._agent_mcp_command_fragment("copilot")

        self.assertEqual(
            fragment, f'--additional-mcp-config "@{self.config_path}"'
        )
        self.assertNotIn('@"', fragment)

    def test_codex_registers_the_sidecar_without_a_config_file(self):
        """Codex takes no config file, so the servers ride in as overrides."""
        self.config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "gridvibe": {
                            "command": r"C:\venv\python.exe",
                            "args": [r"C:\gv\__main__.py", "--url", "http://127.0.0.1:5050"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        cmd_form = web_agents._agent_mcp_command_fragment("codex", shell_family="cmd")
        ps_form = web_agents._agent_mcp_command_fragment("codex", shell_family="powershell")

        # cmd must see the single quotes bare; wrapped in double quotes the
        # override reaches Codex as a value it silently declines to apply.
        # The backslashes survive because a TOML literal string has no escapes.
        self.assertEqual(
            cmd_form,
            r"-c mcp_servers.gridvibe.command='C:\venv\python.exe'"
            r" -c mcp_servers.gridvibe.args=['C:\gv\__main__.py','--url','http://127.0.0.1:5050']",
        )
        # PowerShell must see the outer double quotes; bare, it eats the
        # brackets and Codex refuses to start at all.
        self.assertEqual(
            ps_form,
            "-c \"mcp_servers.gridvibe.command='C:\\venv\\python.exe'\""
            " -c \"mcp_servers.gridvibe.args=["
            "'C:\\gv\\__main__.py','--url','http://127.0.0.1:5050']\"",
        )
        # No config *path* is named: the file's contents travel, not its name.
        self.assertNotIn(str(self.config_path), cmd_form)

    def test_a_spaced_path_reaches_cmd_as_one_argument(self):
        r"""`C:\Program Files` is where Python installs itself by default.

        cmd hands its command line on and the child's own argv parsing ends the
        argument at the space, so the bare form arrived at Codex as two or
        three unrelated tokens -- and Codex applied none of them: no server and
        no error, on exactly the installs most likely to hit it. Quoted, the
        child's parsing strips the double quotes again and Codex reads the
        string the bare form meant to give it.
        """
        interpreter = r"C:\Program Files\venv\python.exe"
        entry = r"C:\My Tools\gv\__main__.py"
        self.config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "gridvibe": {
                            "command": interpreter,
                            "args": [entry, "--url", "http://127.0.0.1:5050"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        fragment = web_agents._agent_mcp_command_fragment("codex", shell_family="cmd")

        self.assertEqual(
            fragment,
            "-c \"mcp_servers.gridvibe.command='" + interpreter + "'\""
            " -c \"mcp_servers.gridvibe.args=['" + entry
            + "','--url','http://127.0.0.1:5050']\"",
        )
        # Single quotes, so it is still a TOML literal string and the
        # backslashes travel through it unescaped.
        self.assertIn("'" + interpreter + "'", fragment)

    def test_a_space_free_path_is_still_handed_to_cmd_bare(self):
        """The quoting above is what a space forces, not a change of mind:
        bare is what Codex applies, verified against the CLI."""
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": "py.exe", "args": ["entry.py"]}}}
            ),
            encoding="utf-8",
        )

        fragment = web_agents._agent_mcp_command_fragment("codex", shell_family="cmd")

        self.assertEqual(
            fragment,
            "-c mcp_servers.gridvibe.command='py.exe'"
            " -c mcp_servers.gridvibe.args=['entry.py']",
        )

    def test_a_spaced_identity_value_is_quoted_rather_than_torn_apart(self):
        """Nothing GridVibe writes into the identity table holds a space today;
        one that did would have been split into tokens the same way."""
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": "py.exe", "args": ["entry.py"]}}}
            ),
            encoding="utf-8",
        )

        fragment = web_agents._agent_mcp_command_fragment(
            "codex",
            shell_family="cmd",
            identity={"GRIDVIBE_SESSION_ID": "pane one"},
        )

        self.assertIn(
            "-c \"mcp_servers.gridvibe.env={GRIDVIBE_SESSION_ID='pane one'}\"",
            fragment,
        )

    def test_codex_also_states_its_pane_identity_inline(self):
        """Codex does not forward the pane's env to the sidecar it spawns.

        Every other CLI reaches the sidecar as an ordinary grandchild and
        inherits the five GRIDVIBE_* variables the pane's own shell carries.
        Codex's own spawn of that child does not, so `whoami` from inside a
        Codex pane reported `inside_gridvibe: false` even though GridVibe
        plainly started the pane. Stating the same variables as a fourth `-c`
        override closes that gap without depending on Codex's own process
        spawn behaviour.
        """
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": "py.exe", "args": ["entry.py"]}}}
            ),
            encoding="utf-8",
        )
        identity = {
            "GRIDVIBE_URL": "http://127.0.0.1:5050",
            "GRIDVIBE_SESSION_ID": "abc123",
            "GRIDVIBE_GROUP_ID": "grp1",
            "GRIDVIBE_WORKSPACE_ID": "ws1",
            "GRIDVIBE_AGENT_DEPTH": "0",
        }

        fragment = web_agents._agent_mcp_command_fragment(
            "codex", shell_family="cmd", identity=identity
        )

        self.assertIn("-c mcp_servers.gridvibe.env=", fragment)
        # No space in the override's *value*: this reaches cmd bare and
        # unquoted (only the leading "-c " flag/value boundary is a space),
        # and cmd's own word-splitting would tear a spaced value apart.
        env_value = fragment.split("mcp_servers.gridvibe.env=", 1)[1]
        self.assertNotIn(" ", env_value)
        for key, value in identity.items():
            self.assertIn(f"{key}='{value}'", fragment)

    def test_no_identity_costs_the_env_fragment_not_the_registration(self):
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": "py.exe", "args": ["entry.py"]}}}
            ),
            encoding="utf-8",
        )

        fragment = web_agents._agent_mcp_command_fragment("codex", shell_family="cmd")

        self.assertNotIn(".env=", fragment)
        self.assertIn("mcp_servers.gridvibe.command", fragment)

    def test_a_remote_codex_pane_never_states_a_local_identity(self):
        # A remote pane's identity is its own tunnel's concern, not this
        # machine's -- inlining it here would be a local pane id on a line a
        # different host runs.
        fragment = web_agents._agent_mcp_command_fragment(
            "codex",
            "/remote/.gridvibe_mcp.json",
            shell_family="posix",
            remote_url="http://127.0.0.1:9",
            identity={"GRIDVIBE_SESSION_ID": "abc123"},
        )

        self.assertNotIn("abc123", fragment)
        self.assertNotIn(".env=", fragment)

    def test_an_identity_value_holding_a_quote_costs_only_the_env_fragment(self):
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": "py.exe", "args": ["entry.py"]}}}
            ),
            encoding="utf-8",
        )

        fragment = web_agents._agent_mcp_command_fragment(
            "codex",
            shell_family="cmd",
            identity={"GRIDVIBE_SESSION_ID": "it's-bad"},
        )

        self.assertNotIn(".env=", fragment)
        self.assertIn("mcp_servers.gridvibe.command", fragment)

    def test_a_value_holding_a_quote_costs_the_fragment_not_the_agent(self):
        # A TOML literal string processes no escapes, so a value carrying a
        # quote of either kind cannot be written this way at all.
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": r"C:\it's\python.exe", "args": []}}}
            ),
            encoding="utf-8",
        )

        self.assertEqual(web_agents._agent_mcp_command_fragment("codex"), "")

    def test_each_local_shell_family_gets_its_own_quoting_end_to_end(self):
        """The pane, not the caller, decides the quoting.

        Pinned to a Windows host because cmd and PowerShell are the two shells
        that disagree, and a local pane can only be either of them there --
        everywhere else `_pane_shell_family` answers `posix` for every pane, so
        the split this test exists to check would not be reachable.
        """
        self.config_path.write_text(
            json.dumps(
                {"mcpServers": {"gridvibe": {"command": "py.exe", "args": ["entry.py"]}}}
            ),
            encoding="utf-8",
        )
        pane = self._pane(
            initial_command="codex", agent_selection="codex", agent_mcp=True
        )

        with patch.object(web_agents.os, "name", "nt"):
            cmd_line = web_agents._compose_agent_startup_command(pane)
            pane.use_powershell = True
            ps_line = web_agents._compose_agent_startup_command(pane)

        self.assertIn("-c mcp_servers.gridvibe.command='py.exe'", cmd_line)
        self.assertIn("-c \"mcp_servers.gridvibe.command='py.exe'\"", ps_line)
        # The title override is the same mechanism and follows the same rule.
        self.assertIn("-c tui.terminal_title=['thread-title']", cmd_line)
        self.assertIn("-c \"tui.terminal_title=['thread-title']\"", ps_line)

    def test_an_agent_whose_only_mechanism_edits_the_users_config_gets_nothing(self):
        """`<agent> mcp add` would outlive the pane that ticked a checkbox."""
        for key in ("grok", "hermes", "opencode", "kilo", "kimi"):
            with self.subTest(agent=key):
                self.assertFalse(web_agents._agent_supports_mcp(key))
                self.assertEqual(web_agents._agent_mcp_command_fragment(key), "")

    def test_the_three_supported_clis_are_the_ones_that_were_verified(self):
        for key in ("claude", "copilot", "codex"):
            with self.subTest(agent=key):
                self.assertTrue(web_agents._agent_supports_mcp(key))
                self.assertTrue(
                    web_agents.AGENT_REGISTRY[key]["mcp"].get("verified")
                )

    def test_the_launcher_is_told_which_agents_publish_a_block(self):
        options = {option["value"]: option for option in web_agents._agent_options()}

        self.assertEqual(options["claude"]["mcp_flag"], "--mcp-config {config}")
        self.assertTrue(options["claude"]["mcp_description"])
        # No block, no checkbox. That is the whole mechanism for the seven CLIs
        # whose MCP support has not been verified against a current release.
        self.assertEqual(options["opencode"]["mcp_flag"], "")
        self.assertEqual(options["other"]["mcp_flag"], "")


class McpFlagGateTestCase(unittest.TestCase):
    """Which panes may carry `agent_mcp` at all.

    The launcher hides the checkbox for a CLI that has no way to be handed a
    server, but the checkbox is not the only way in: `POST /api/sessions` takes
    the flag from any caller, the sidecar's own `launch_panes` is one of them,
    and the split and relaunch routes take it too. A flag on a CLI with no
    mechanism buys nothing and costs something -- the pane header wears an MCP
    tag for a launch line that carries nothing, and an SSH pane opens a reverse
    forward, mints a token and writes a file on the remote host for an agent
    that will never call any of it.

    So it is refused at every write, by the one predicate that knows:
    `_agent_supports_mcp`. Dropped rather than refused, exactly as a stated
    `mcp` on a pane with no agent already is.
    """

    UNSUPPORTED = ("grok", "hermes", "opencode", "kilo", "kimi")

    def _entries(self, agent, mcp=True):
        return saved_sessions._normalize_terminal_entries(
            [
                {
                    "startup_mode": "agent",
                    "agent_selection": agent,
                    "initial_command": agent,
                    "agent_mcp": mcp,
                }
            ],
            minimum_count=1,
        )

    def test_a_launch_body_cannot_ask_for_it_on_a_cli_with_no_mechanism(self):
        for agent in self.UNSUPPORTED:
            with self.subTest(agent=agent):
                self.assertFalse(self._entries(agent)[0]["agent_mcp"])

    def test_a_launch_body_still_gets_it_on_one_that_has_a_mechanism(self):
        for agent in ("claude", "copilot", "codex"):
            with self.subTest(agent=agent):
                self.assertTrue(self._entries(agent)[0]["agent_mcp"])

    def test_a_custom_agent_has_no_mechanism_either(self):
        entries = saved_sessions._normalize_terminal_entries(
            [
                {
                    "startup_mode": "agent",
                    "agent_selection": "",
                    "custom_agent": "my-own-cli",
                    "agent_mcp": True,
                }
            ],
            minimum_count=1,
        )

        self.assertFalse(entries[0]["agent_mcp"])

    def test_a_split_cannot_ask_for_it_either(self):
        """`launch_panes` and `split_pane` are the two a tool can reach."""
        from web import api

        source = SimpleNamespace(
            mode="wsl", host="", username="", password="", port=22,
            directory="", distribution="", use_wsl=False, use_powershell=True,
        )

        with patch.object(api, "_agent_absent_reason", return_value=""):
            # Refused rather than silently dropped: the split adds no pane.
            with self.assertRaises(api.SplitRequestError) as refused:
                api._split_pane_overrides(
                    source, {"kind": "agent", "agent": "grok", "mcp": True}
                )
            allowed = api._split_pane_overrides(
                source, {"kind": "agent", "agent": "claude", "mcp": True}
            )

        self.assertIn("No pane was added", str(refused.exception))
        self.assertTrue(allowed["agent_mcp"])

    def test_a_relaunch_cannot_turn_it_on_for_one(self):
        """The header dropdown's route and `set_pane_agent`'s gated twin."""
        from web import session_shell

        pane = SimpleNamespace(
            session_id="pane-1", mode="wsl", startup_mode="agent",
            agent_selection="claude", custom_agent="", agent_auto_mode=False,
            agent_mcp=False, directory="", distribution="",
            use_wsl=False, use_powershell=False,
        )
        pane.to_dict = lambda: {"session_id": "pane-1"}
        written = {}

        effects = SimpleNamespace(
            close_connection=lambda *a, **k: None,
            broadcast_status=lambda *a, **k: None,
            start_connector=lambda *a, **k: None,
        )
        manager = MagicMock()
        manager.get_session.return_value = pane
        manager.update_session_metadata.side_effect = (
            lambda _session_id, **updates: written.update(updates)
        )

        with patch.object(session_shell, "session_manager", manager),                 patch.object(session_shell, "_refuse_an_agent_that_is_not_installed"):
            session_shell.apply_pane_shell_change(
                "pane-1", {"agent": "grok", "mcp": True}, effects
            )

        self.assertIn("agent_mcp", written)
        self.assertFalse(written["agent_mcp"])


if __name__ == "__main__":
    unittest.main()
