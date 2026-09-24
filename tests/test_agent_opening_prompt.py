"""Where a handed-over task's opening prompt sits on an agent's launch line.

The only thing a task adds to a launch line is one constant GridVibe sentence,
and `web/agents.py`'s composer is the one owner of where it goes. Pinned:

- **Directly after the binary**, for every CLI and every shell family, because
  Claude's ``--mcp-config`` takes a variable number of values and a prompt
  appended after it would be read as a second config path. Copilot takes it
  behind ``-i``.
- **Only beside a non-empty MCP fragment**, never beside a resume, and never
  for a CLI whose registry block is not verified.
- **The line still parses as what it is**: the conversation identity readers
  and the runtime agent detector see the same agent they saw before.
- **The rule a task is refused by** has one owner, shared by every route.
"""

import shlex
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import agent_conversations, mcp_launch, terminal_io  # noqa: E402
from web import agents as web_agents  # noqa: E402
from web.agent_handoffs import HANDOFF_OPENING_PROMPT  # noqa: E402

QUOTED = f'"{HANDOFF_OPENING_PROMPT}"'
TASK_AGENTS = ("claude", "codex", "copilot")


def _pane(agent="claude", **overrides):
    fields = dict(
        session_id="pane-b",
        initial_command=agent,
        initial_command_mode="agent",
        agent_selection=agent,
        agent_auto_mode=False,
        agent_mcp=True,
        mode="wsl",
        use_wsl=False,
        use_powershell=False,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class RegistryTestCase(unittest.TestCase):
    def test_exactly_the_three_mcp_clis_take_a_task(self):
        capable = web_agents.task_capable_agents()

        self.assertEqual(capable, sorted(TASK_AGENTS))
        for key in web_agents.AGENT_REGISTRY:
            with self.subTest(agent=key):
                self.assertEqual(
                    web_agents._agent_accepts_task(key),
                    key in TASK_AGENTS,
                )
                # A task is fetched through the tools, so the one implies the other.
                if web_agents._agent_accepts_task(key):
                    self.assertTrue(web_agents._agent_supports_mcp(key))

    def test_the_launcher_options_publish_the_same_fact(self):
        options = {item["value"]: item for item in web_agents._agent_options()}

        for key in web_agents.AGENT_REGISTRY:
            self.assertEqual(
                options[key]["opening_prompt_supported"], key in TASK_AGENTS
            )
        self.assertFalse(options["other"]["opening_prompt_supported"])

    def test_an_unverified_block_publishes_nothing(self):
        with patch.dict(
            web_agents.AGENT_REGISTRY["claude"],
            {"opening_prompt": {"style": "positional", "verified": False}},
        ):
            self.assertFalse(web_agents._agent_accepts_task("claude"))

    def test_a_flag_template_that_is_not_a_plain_option_resolves_to_nothing(self):
        for flag in ("-i {prompt}; rm -rf /", "$(x) {prompt}", "-i", "{prompt}", "-i  {prompt} --yolo"):
            with self.subTest(flag=flag):
                with patch.dict(
                    web_agents.AGENT_REGISTRY["copilot"],
                    {"opening_prompt": {"flag": flag, "verified": True}},
                ):
                    self.assertEqual(web_agents._agent_opening_prompt_template("copilot"), "")


class TaskRefusalTestCase(unittest.TestCase):
    def test_a_capable_agent_with_the_tools_passes(self):
        for agent in TASK_AGENTS:
            for mcp in (None, True):
                with self.subTest(agent=agent, mcp=mcp):
                    self.assertEqual(web_agents.task_refusal("agent", agent, mcp), "")

    def test_each_refusal_names_what_is_missing(self):
        cases = {
            ("terminal", "claude", None): "set kind to 'agent'",
            ("", "claude", None): "set kind to 'agent'",
            ("agent", "", None): "name the agent",
            ("agent", "nosuchcli", None): "not a known agent CLI",
            ("agent", "grok", None): "no verified way to take an opening prompt",
            ("agent", "claude", False): "leave 'mcp' out or set it to true",
            ("agent", "claude", "yes"): "mcp must be true or false",
        }
        for (kind, agent, mcp), expected in cases.items():
            with self.subTest(kind=kind, agent=agent, mcp=mcp):
                self.assertIn(expected, web_agents.task_refusal(kind, agent, mcp))

    def test_a_capable_list_is_offered_with_an_incapable_agent(self):
        refusal = web_agents.task_refusal("agent", "opencode")

        for agent in TASK_AGENTS:
            self.assertIn(agent, refusal)


class PlacementTestCase(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config_path = Path(temp.name) / ".gridvibe_mcp.json"
        # A real server block, because Codex's launch line inlines it.
        self.config_path.write_text(
            '{"mcpServers": {"gridvibe": {"command": "C:/venv/python.exe", '
            '"args": ["C:/gridvibe/gridvibe_mcp/__main__.py", "--url", '
            '"http://127.0.0.1:5050"]}}}',
            encoding="utf-8",
        )
        patcher = patch.object(mcp_launch, "mcp_config_path", lambda: str(self.config_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _compose(self, pane, *, opening_prompt=True, **kwargs):
        return web_agents._compose_agent_startup_command(
            pane, opening_prompt=opening_prompt, **kwargs
        )

    def _assert_right_after_binary(self, command, agent):
        if agent == "copilot":
            self.assertTrue(command.startswith(f"copilot -i {QUOTED} "), command)
        else:
            self.assertTrue(command.startswith(f"{agent} {QUOTED} "), command)
        self.assertEqual(command.count(QUOTED), 1)

    def test_every_cli_in_every_local_shell_family(self):
        families = {
            "cmd": dict(use_powershell=False, use_wsl=False),
            "powershell": dict(use_powershell=True, use_wsl=False),
            "wsl": dict(use_powershell=False, use_wsl=True),
        }
        for agent in TASK_AGENTS:
            for family, fields in families.items():
                with self.subTest(agent=agent, family=family), patch.object(
                    web_agents.os, "name", "nt"
                ):
                    command = self._compose(
                        _pane(agent, **fields),
                        identity={"GRIDVIBE_SESSION_ID": "pane-b"},
                    )
                    self._assert_right_after_binary(command, agent)

    def test_every_cli_over_an_ssh_tunnel(self):
        for agent in TASK_AGENTS:
            with self.subTest(agent=agent):
                command = self._compose(
                    _pane(agent, mode="ssh"),
                    remote_config_path="/home/u/.gridvibe/mcp-b.json",
                    remote_url="http://127.0.0.1:40001/mcp/TOKEN",
                )
                self._assert_right_after_binary(command, agent)

    def test_claudes_variadic_mcp_config_comes_after_the_prompt(self):
        command = self._compose(_pane("claude", agent_auto_mode=True))

        self.assertLess(command.index(QUOTED), command.index("--mcp-config"))
        self.assertLess(command.index(QUOTED), command.index("--permission-mode"))
        # And the prompt is one argument to a POSIX-ish reader of the line.
        tokens = shlex.split(command)
        self.assertEqual(tokens[1], HANDOFF_OPENING_PROMPT)

    def test_without_the_mcp_fragment_there_is_no_prompt(self):
        """An agent told to call a tool it does not have is what this prevents."""
        self.config_path.unlink()

        self.assertEqual(self._compose(_pane("claude")), "claude")
        self.assertEqual(self._compose(_pane("claude", agent_mcp=False)), "claude")
        # An SSH pane whose tunnel was refused has no fragment either.
        self.assertNotIn(QUOTED, self._compose(_pane("codex", mode="ssh")))

    def test_not_asked_means_not_placed(self):
        self.assertNotIn(QUOTED, self._compose(_pane("codex"), opening_prompt=False))

    def test_an_incapable_cli_gets_no_prompt_even_when_asked(self):
        with patch.dict(
            web_agents.AGENT_REGISTRY["claude"],
            {"opening_prompt": {"style": "positional", "verified": False}},
        ):
            self.assertNotIn(QUOTED, self._compose(_pane("claude")))

    def test_a_custom_command_launches_verbatim(self):
        pane = _pane("claude", initial_command="claude --model opus")

        self.assertEqual(self._compose(pane), "claude --model opus")

    def test_never_beside_a_resume(self):
        conversation_id = "0199a1b2-c3d4-4e5f-8a9b-0123456789ab"
        with patch.object(agent_conversations, "conversation_restore_enabled", return_value=True):
            for agent in ("claude", "codex"):
                with self.subTest(agent=agent):
                    pane = _pane(
                        agent,
                        agent_conversation_provider=agent,
                        agent_conversation_id=conversation_id,
                        agent_conversation_resume=True,
                    )
                    command = self._compose(pane)
                    self.assertTrue(
                        agent_conversations.command_resumes_conversation(command), command
                    )
                    self.assertNotIn(QUOTED, command)

    def test_a_fresh_claude_session_id_is_not_a_resume(self):
        conversation_id = "0199a1b2-c3d4-4e5f-8a9b-0123456789ab"
        with patch.object(agent_conversations, "conversation_restore_enabled", return_value=True):
            pane = _pane(
                "claude",
                agent_conversation_provider="claude",
                agent_conversation_id=conversation_id,
                agent_conversation_resume=False,
            )
            command = self._compose(pane)

        self._assert_right_after_binary(command, "claude")
        self.assertIn(f"--session-id {conversation_id}", command)
        # The identity reader still finds the id behind the prompt.
        self.assertEqual(
            agent_conversations.command_conversation_identity(command),
            ("claude", conversation_id),
        )

    def test_the_line_still_reads_as_the_agent_it_starts(self):
        for agent in TASK_AGENTS:
            with self.subTest(agent=agent):
                command = self._compose(_pane(agent))
                detected = terminal_io._agent_from_terminal_command(command)
                self.assertEqual(detected[0], agent)
                self.assertEqual(
                    agent_conversations.command_conversation_identity(command), ("", "")
                )
                self.assertEqual(agent_conversations.command_thread_id(command), "")

    def test_the_carrier_check_matches_only_the_quoted_constant(self):
        self.assertTrue(web_agents.launch_line_carries_opening_prompt(f"codex {QUOTED}"))
        self.assertFalse(web_agents.launch_line_carries_opening_prompt("codex"))
        self.assertFalse(web_agents.launch_line_carries_opening_prompt(HANDOFF_OPENING_PROMPT))


class GapTestCase(unittest.TestCase):
    """What the pane's own output says when a task could not be announced."""

    def test_each_reason_is_the_one_that_applies(self):
        cases = [
            (_pane(initial_command_mode="command"), False, "no longer set to start an agent"),
            (_pane(initial_command="claude --x"), False, "custom command"),
            (_pane("grok"), False, "grok cannot be handed a task"),
            (_pane(agent_mcp=False), False, "without GridVibe's tools"),
            (_pane(agent_conversation_resume=True), False, "resumed a saved conversation"),
            (_pane(), False, "tool config for this machine was not found"),
            (_pane(mode="ssh"), False, "SSH tunnel"),
            (_pane(mode="ssh"), True, "could not be given GridVibe's tools"),
        ]
        for pane, tunnelled, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, web_agents.opening_prompt_gap(pane, tunnelled=tunnelled))


if __name__ == "__main__":
    unittest.main()
