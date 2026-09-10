"""When a pane stops running an agent, and how GridVibe finds out.

An agent CLI owns the terminal while it runs, so it draws no shell prompt: the
hook `web/terminal_cwd.py` installs emits nothing for as long as the agent is
there. The pane's *next* prompt is therefore the shell taking the terminal back,
whatever ended the agent -- an exit, a crash, a kill, or a binary that was never
installed to begin with.

Every case here drives that through the real observer, over the bytes a shell
actually emits, because the two things it replaces were guesses at the same
question: the double-Ctrl+C heuristic cannot tell a quit from two interrupted
turns, and neither heuristic sees an agent closed any other way at all. What is
pinned:

- **The prompt after a typed agent command retires the pane**, whether the
  agent ran for an hour or never started.
- **A prompt drawn while the promotion was still landing counts.** The mark is
  taken before the read that can wait out a remote timeout, and the arm re-asks
  at once, or an instantly failing command on a remote pane is missed.
- **An agent still holding the terminal is left alone** -- output, titles and
  progress are not prompts.
- **A launched pane is not armed by its own startup or by terminal protocol
  traffic.** Its bootstrap output has not been read when the agent command goes
  out, so there is no honest mark to take; the reader's first meaningful input
  is the moment that is both late enough and free. xterm capability replies and
  TUI mouse packets share its input callback but are not that gesture.
- **A retired pump may not retire the pane it no longer owns.**
- **Where the prompt is observed, the keystroke heuristics stand down**, and
  where it is not, they still answer.
- **The title an exited agent left behind stops being published.**
"""

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import terminal_io as terminal  # noqa: E402
from web.agent_activity import AGENT_EVENT_TITLE, apply_agent_events  # noqa: E402

ESC = "\x1b"
#: What the installed hook emits every time the shell draws a prompt.
PROMPT = ESC + "]9;9;C:\\repos\\gridvibe" + ESC + "\\"
#: What cmd prints when the binary is not there.
NOT_RECOGNIZED = "'codex' is not recognized as an internal or external command,\r\n"


class AgentRuntimeExitTestCase(unittest.TestCase):
    def setUp(self):
        self.registry = {}
        for name, value in [("ssh_connections", self.registry), ("session_output_buffers", {})]:
            context = patch.object(terminal, name, value)
            context.start()
            self.addCleanup(context.stop)
        for name in ["session_manager", "_broadcast_session_status"]:
            context = patch.object(terminal, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self.session = SimpleNamespace(
            session_id="pane",
            mode="local",
            startup_mode="terminal",
            agent_selection="",
            custom_agent="",
            initial_command="",
            initial_command_mode="command",
            agent_auto_mode=False,
            directory="C:\\repos\\gridvibe",
        )
        self.session_manager.get_session.return_value = self.session
        self.session_manager.update_session_metadata.side_effect = self.apply_metadata
        # The promotion path reads the pane's directory; an observed source is
        # what keeps it off the probe, which would type at a prompt.
        context = patch.object(
            terminal,
            "effective_directory",
            side_effect=lambda *_: (
                self.session.directory,
                terminal.CWD_SOURCE_SHELL_INTEGRATION,
            ),
        )
        context.start()
        self.addCleanup(context.stop)
        self.connection = {"kind": "local", "shell_kind": "cmd"}
        self.registry["pane"] = self.connection

    def apply_metadata(self, _session_id, **fields):
        for name, value in fields.items():
            setattr(self.session, name, value)
        return self.session

    def output(self, chunk, connection=None):
        """Push one chunk through the observer exactly as the pump does."""
        terminal._observe_terminal_output_cwd("pane", connection or self.connection, chunk)

    def send(self, text, connection=None):
        terminal._track_terminal_agent_input("pane", connection or self.connection, text)

    def age_past_arm_floor(self):
        """Move the pane past the floor under a launched pane's arming."""
        self.connection["startup_finished_at"] = (
            time.monotonic() - terminal.AGENT_RUNTIME_ARM_MIN_AGE_SECONDS - 1
        )

    def launch_agent_pane(self, command="codex"):
        """Open the pane the way a launch does, agent command and all."""
        self.session.startup_mode = "agent"
        self.session.agent_selection = "codex"
        self.session.initial_command = command
        self.session.initial_command_mode = "agent"
        with patch.object(terminal, "_send_connection_input"), \
                patch.object(terminal, "_startup_directories", return_value=("", "")):
            terminal._run_startup_sequence(self.connection, self.session)

    def assertIsAgent(self, expected=True):
        self.assertEqual(self.session.startup_mode, "agent" if expected else "terminal")
        self.assertEqual(self.session.agent_selection, "codex" if expected else "")

    def test_the_prompt_after_a_typed_agent_command_retires_the_pane(self):
        self.output(PROMPT)
        self.send("codex\r")
        self.assertIsAgent()

        # Codex runs: it repaints and it announces a title. It says nothing
        # about a prompt, because it has the terminal.
        self.output(ESC + "]0;Refactor the parser" + ESC + "\\thinking...\r\n")
        self.assertIsAgent()

        # Ctrl+C, Ctrl+C, and cmd draws its prompt again -- from another
        # directory, so this one prompt moves the pane and retires its agent.
        # Both facts, one broadcast.
        self._broadcast_session_status.reset_mock()
        self.output(ESC + "]9;9;C:\\repos" + ESC + "\\")
        self.assertIsAgent(False)
        self.assertEqual(self.session.initial_command, "")
        self.assertEqual(self.session.initial_command_mode, "command")
        self._broadcast_session_status.assert_called_once_with("pane")

    def test_an_agent_that_never_started_is_retired_by_the_same_prompt(self):
        self.output(PROMPT)
        self.send("codex\r")
        self.assertIsAgent()
        self.output(NOT_RECOGNIZED + PROMPT)
        self.assertIsAgent(False)

    def test_a_prompt_drawn_while_the_promotion_landed_retires_it_at_once(self):
        # On a remote pane with no observation, `effective_directory` opens an
        # exec channel and waits out its bounded timeout -- long enough for a
        # missing binary to fail and prompt again. The mark is taken before it.
        def slow_read(*_):
            self.output(NOT_RECOGNIZED + PROMPT)
            return ("/srv/gridvibe", terminal.CWD_SOURCE_PROBE)

        self.output(PROMPT)
        with patch.object(terminal, "effective_directory", side_effect=slow_read):
            self.send("codex\r")
        self.assertIsAgent(False)

    def test_output_that_is_not_a_prompt_leaves_the_agent_alone(self):
        self.output(PROMPT)
        self.send("codex\r")
        for chunk in [
            "esc to interrupt\r\n",
            ESC + "]2;Chat A" + ESC + "\\",
            ESC + "]9;4;1;40" + ESC + "\\",
            ESC + "[2J" + ESC + "[H",
            # OSC 7 with no payload reports no directory, and so no prompt.
            ESC + "]7;" + ESC + "\\",
        ]:
            with self.subTest(chunk=chunk):
                self.output(chunk)
                self.assertIsAgent()

    def test_a_retired_pump_cannot_retire_the_pane_it_no_longer_owns(self):
        self.output(PROMPT)
        self.send("codex\r")
        retired, self.connection = self.connection, {"kind": "local", "shell_kind": "cmd"}
        self.registry["pane"] = self.connection
        self.output(PROMPT, connection=retired)
        self.assertIsAgent()

    def test_a_launched_pane_is_not_armed_by_its_own_startup(self):
        # The regression this rule exists for. `_run_startup_sequence` runs
        # before the pump, so the prompts its own `cd`/hook/marker lines draw --
        # three commands' worth on a remote pane, and the hook emits twice --
        # are still in the transport when it returns. A watch armed there
        # retired a healthy agent a second after it started.
        self.launch_agent_pane()
        self.assertFalse(self.connection.get("agent_runtime_armed"))
        self.output(PROMPT + PROMPT + PROMPT + PROMPT)
        self.assertIsAgent()

    def test_the_readers_first_input_arms_a_launched_pane_and_its_exit_retires_it(self):
        self.launch_agent_pane()
        self.output(PROMPT + PROMPT)
        self.age_past_arm_floor()

        # Ctrl+C into the running agent: the gesture that ends it is the one
        # that arms the watch for it.
        self.send("\x03")
        self.assertTrue(self.connection["agent_runtime_armed"])
        self.assertIsAgent()

        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_input_during_the_startup_burst_arms_nothing_and_the_next_input_does(self):
        self.launch_agent_pane()
        self.send("h")
        self.assertFalse(self.connection.get("agent_runtime_armed"))
        # The burst lands after that input; nothing may retire the pane.
        self.output(PROMPT + PROMPT)
        self.assertIsAgent()

        self.age_past_arm_floor()
        self.send("i")
        self.assertTrue(self.connection["agent_runtime_armed"])
        self.assertIsAgent()

    def test_terminal_protocol_traffic_cannot_arm_a_launched_agent(self):
        """xterm replies and mouse reports are transport, not reader intent.

        The restored pane's first live output can ask xterm for its secondary
        device attributes. xterm answers through the same ``onData`` callback
        as a key, and a TUI's mouse report takes that route too. Arming on either
        let a still-draining bootstrap prompt retire Claude while it was visibly
        running.
        """
        self.launch_agent_pane(command="claude")
        self.age_past_arm_floor()

        for packet in [
            ESC + "[>0;276;0c",  # xterm secondary device attributes
            ESC + "[1;1R",  # cursor position report
            ESC + "[<0;20;5M",  # SGR mouse press
            ESC + "[M" + " *%",  # legacy X10 mouse press + coordinates
            ESC + "]10;rgb:ffff/ffff/ffff" + ESC + "\\",  # colour reply
        ]:
            with self.subTest(packet=packet):
                self.send(packet)
                self.assertFalse(self.connection.get("agent_runtime_armed"))
                self.assertIsAgent()

        # A late prompt from the launch burst is harmless while no reader
        # gesture has armed the pane.
        self.output(PROMPT)
        self.assertIsAgent()

        # Genuine input still arms the same observation and its next prompt
        # still retires the agent.
        self.send("\x03")
        self.assertTrue(self.connection["agent_runtime_armed"])
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_a_launched_terminal_pane_is_never_armed(self):
        self.session.initial_command = "npm run dev"
        with patch.object(terminal, "_send_connection_input"), \
                patch.object(terminal, "_startup_directories", return_value=("", "")):
            terminal._run_startup_sequence(self.connection, self.session)
        self.age_past_arm_floor()
        self.send("ls\r")
        self.assertFalse(self.connection.get("agent_runtime_armed"))

    def test_the_keystroke_guesses_stand_down_where_the_prompt_is_observed(self):
        self.output(PROMPT)
        self.send("codex\r")
        # Two interrupts in quick succession are how Codex quits *and* how a
        # reader interrupts two turns; the pane's own prompt is what decides.
        self.send("\x03")
        self.send("\x03")
        self.assertIsAgent()
        self.send("/exit\r")
        self.assertIsAgent()
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_the_keystroke_guesses_still_answer_for_a_pane_with_no_hook(self):
        # `terminal.shell_integration` off, or a remote shell that would not
        # take the line: no prompt was ever observed, so the guess is all there
        # is and it has to still work.
        self.send("codex\r")
        self.assertIsAgent()
        self.send("\x03")
        self.send("\x03")
        self.assertIsAgent(False)

    def test_the_title_an_exited_agent_left_stops_being_published(self):
        self.output(PROMPT)
        self.send("codex\r")
        self.connection["agent_activity"] = apply_agent_events(
            None, [(AGENT_EVENT_TITLE, "OpenAI Codex CLI")], time.time()
        )
        self.assertEqual(
            terminal.agent_activity_snapshot()["pane"]["title"], "OpenAI Codex CLI"
        )

        self.output(PROMPT)
        self.assertIsAgent(False)
        self.assertEqual(terminal.agent_activity_snapshot()["pane"]["title"], "")

        # What the pane says *next* is a fresh fact and is published again.
        self.connection["agent_activity"] = apply_agent_events(
            self.connection["agent_activity"], [(AGENT_EVENT_TITLE, "Chat B")], time.time() + 1
        )
        self.assertEqual(terminal.agent_activity_snapshot()["pane"]["title"], "Chat B")


if __name__ == "__main__":
    unittest.main()
