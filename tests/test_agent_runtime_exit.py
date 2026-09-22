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
- **A relaunch does not fall between the two threads that observe it.** The
  prompt that ends an agent is read on the pane's pump thread and the command
  that starts the next one on the Socket.IO handler's, and nothing orders them:
  quit an agent and retype it straight away and either can land first. Whichever
  does, the pane ends up running the agent the reader asked for -- a command
  submitted before the prompt was read is held and applied by it, and a prompt
  the retired agent's own exit had already bought is absorbed once rather than
  spent retiring the agent that had just started.
- **A command GridVibe never saw typed is still found.** The submitted-line
  reader rebuilds the command from keystrokes with escape sequences stripped, so
  a command recalled from history (Up, Enter), completed with Tab or edited in
  place is one it never sees -- and a pane running an agent nobody submitted
  used to stay a terminal until it was relaunched. The OS is asked what is
  running under the pane's own shell, and the pane is promoted on the answer.
- **Retirement stays the prompt's.** The process reading may only promote: it
  cannot see into a WSL distribution or onto a remote host, and a reading that
  answers "no agent" for a pane it cannot see would retire agents that are
  running. Where it *can* answer, a prompt drawn while the agent is still there
  no longer retires it.
- **A promotion that infers the agent writes no startup command of its own.**
  `initial_command` is persisted and typed verbatim at the shell when the pane
  comes back, so only a line a shell was *seen* to run may reach it -- and a
  pane that already has a startup command keeps it, because a reading about
  what is running now is not entitled to replace what the reader set.
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
#: The pid of the pane's own shell in the tables below.
SHELL_PID = 100
#: What the OS says when the pane's shell is running Codex, two levels down --
#: the shape the real machine has on Windows (`cmd` -> `node` -> `codex`).
AGENT_TABLE = {
    1: (0, "explorer"),
    SHELL_PID: (1, "cmd"),
    110: (SHELL_PID, "node"),
    120: (110, "codex"),
    # Somebody else's Codex, which is not this pane's.
    300: (1, "chatgpt"),
    310: (300, "codex"),
}
#: And when it is sitting at its prompt.
IDLE_TABLE = {1: (0, "explorer"), SHELL_PID: (1, "cmd"), 300: (1, "chatgpt"), 310: (300, "codex")}
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

    def give_the_pane_a_shell(self, pid=SHELL_PID):
        """A local pane's shell process, which is what the walk starts from."""
        self.connection["pty_process"] = SimpleNamespace(pid=pid)

    def machine(self, table):
        """What the OS says is running, for the rest of this test.

        The per-pane throttle is switched off with it: it is a ceiling on how
        often the OS is asked, not part of any answer, and one test pins it on
        its own.
        """
        for context in [
            patch.object(terminal, "process_table", return_value=table),
            patch.object(terminal, "PANE_AGENT_READING_TTL_SECONDS", 0.0),
        ]:
            context.start()
            self.addCleanup(context.stop)

    def announce(self, title):
        """Push one OSC 0 title through the observer, exactly as the pump does."""
        terminal._observe_agent_activity(
            "pane", self.connection, ESC + "]0;" + title + ESC + "\\"
        )

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

    def test_a_relaunch_typed_before_the_exit_prompt_is_read_still_promotes(self):
        """The reported defect: Ctrl+C out of Codex, retype it, land nowhere.

        The command lands on the Socket.IO handler's thread and the prompt that
        ended the previous agent on the pane's pump thread, and a quick relaunch
        puts them within a few hundred milliseconds of each other. When the
        command is first the pane still reads as an agent, so the line was
        dropped as conversation input -- and the prompt that followed then
        demoted a pane that was by that point running Codex again. Nothing
        promotes a pane but a submitted command, so it stayed a terminal for the
        rest of its life: no agent name in the header, no row on the dashboard.
        """
        self.output(PROMPT)
        self.send("codex\r")
        self.assertIsAgent()

        # Ctrl+C, Ctrl+C, and the relaunch typed while Codex is still tearing
        # down. Nothing of its exit has been read yet, so the pane still reads
        # as the agent that is leaving.
        self.send("\x03")
        self.send("\x03")
        self.send("codex\r")

        # The prompt lands, and it is the proof that the line was a command.
        self.output(PROMPT)
        self.assertIsAgent()
        self.assertEqual(self.session.initial_command, "codex")
        self.assertEqual(self.session.initial_command_mode, "agent")

    def test_the_relaunched_agent_is_watched_like_any_other(self):
        self.output(PROMPT)
        self.send("codex\r")
        self.send("\x03")
        self.send("\x03")
        self.send("codex resume 01a08612-11ad-7673-989b-4110ba7f8494\r")
        self.output(PROMPT)
        self.assertIsAgent()
        # The registered binary, not the held line. Nothing proved the shell
        # ran that line, and `initial_command` is typed at the shell on restore.
        self.assertEqual(self.session.initial_command, "codex")
        self.assertEqual(self.session.initial_command_mode, "agent")

        # It holds the terminal, and its own next prompt is what retires it.
        self.output(ESC + "]0;my-project" + ESC + "\\working\r\n")
        self.assertIsAgent()
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_a_relaunch_survives_the_prompt_the_previous_agent_left_in_flight(self):
        """A pane with no hook: the guess retires it before the shell prompts.

        `terminal.shell_integration` off, or a remote shell that would not take
        the line -- the double Ctrl+C is all there is, and it is read at the
        keystroke rather than at the prompt. So the shell's own prompt is still
        coming when the relaunch is promoted, and it used to be spent retiring
        the agent that had just started rather than the one that had left.
        """
        self.send("codex\r")
        self.assertIsAgent()
        self.send("\x03")
        self.send("\x03")
        self.assertIsAgent(False)

        self.send("codex\r")
        self.assertIsAgent()
        self.output(PROMPT)
        self.assertIsAgent()

        # One prompt, and only one: the relaunched agent's own exit still ends
        # it, or the absorb would have cost the pane its watch instead.
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_a_relaunch_survives_a_stray_interrupts_extra_prompt(self):
        """Mashed Ctrl+C: the shell answers the one the agent did not take."""
        self.output(PROMPT)
        self.send("\x03")
        self.send("codex\r")
        self.send("\x03")
        self.send("\x03")
        self.send("\x03")
        self.output(PROMPT)
        self.assertIsAgent(False)

        self.send("codex\r")
        self.assertIsAgent()
        self.output(PROMPT)
        self.assertIsAgent()

    def test_a_relaunch_long_after_the_exit_owes_the_shell_nothing(self):
        """The absorb is bounded: whatever the shell was going to draw, it has.

        A relaunch that is not quick is promoted against a mark that already
        counts every prompt there was, so its first prompt is its own outcome
        and a binary that is not installed still retires it at once -- the same
        reading `test_a_prompt_drawn_while_the_promotion_landed_retires_it_at_once`
        pins for a first launch.
        """
        with patch.object(terminal, "AGENT_RETIRED_PROMPT_ABSORB_SECONDS", 0.0):
            self.output(PROMPT)
            self.send("codex\r")
            self.output(PROMPT)
        self.assertIsAgent(False)

        self.send("codex\r")
        self.assertIsAgent()
        self.output(NOT_RECOGNIZED + PROMPT)
        self.assertIsAgent(False)

    def test_a_later_submitted_line_retires_a_held_relaunch(self):
        """A reader still talking to the agent has answered the question."""
        self.output(PROMPT)
        self.send("codex\r")
        self.send("\x03")
        self.send("claude\r")
        self.send("what does this repo do\r")
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_a_held_relaunch_does_not_wait_forever(self):
        self.output(PROMPT)
        self.send("codex\r")
        self.send("\x03")
        with patch.object(terminal, "AGENT_RELAUNCH_PENDING_SECONDS", -1.0):
            self.send("codex\r")
            self.output(PROMPT)
        self.assertIsAgent(False)

    def test_prose_said_to_an_agent_is_not_a_relaunch(self):
        """The first word of a sentence is not a command because a CLI is called that.

        "claude can you double check this" parses as an invocation of `claude`,
        and an agent that exited shortly after would have relabelled the pane
        from the reader's prose -- and, because `initial_command` is persisted
        and typed at the shell on restore, saved the sentence as the pane's
        startup command and run it. A relaunch follows a quit; prose does not.
        """
        self.output(PROMPT)
        self.send("codex\r")
        self.send("claude can you double check this\r")
        self.output(PROMPT)

        self.assertIsAgent(False)
        self.assertEqual(self.session.initial_command, "")
        self.assertEqual(self.session.agent_selection, "")

    def test_an_interrupted_agent_still_does_not_take_prose_as_its_command(self):
        """And when the reader *did* interrupt, what is held is the binary."""
        self.output(PROMPT)
        self.send("codex\r")
        self.send("\x03")
        self.send("claude can you double check this\r")
        self.output(PROMPT)

        self.assertEqual(self.session.agent_selection, "claude")
        self.assertEqual(self.session.initial_command, "claude")
        self.assertNotIn("double check", self.session.initial_command)

    def test_a_prompt_spent_on_a_running_agent_retires_a_held_line(self):
        """That prompt is the answer to what the hold was waiting on.

        The agent is still there, so the line was said to it -- and a hold left
        standing would be applied by some later, unrelated prompt instead.
        """
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.send("codex\r")
        self.send("\x03")
        self.send("claude\r")

        self.machine(AGENT_TABLE)
        self.output(PROMPT)
        self.assertIsAgent()

        # Codex really exits now. The held `claude` is gone, so this prompt
        # retires the pane rather than relabelling it.
        self.machine(IDLE_TABLE)
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_only_a_prompt_applies_a_held_relaunch(self):
        """A guess cannot tell a quit from an interrupted turn, so it may not
        promote on top of one.

        A pane with no hook reads `/exit` as the end of its agent, and the
        reader may well have typed the word `codex` into the conversation
        first. Relaunching on that reading would label the pane with an agent
        that is not there on the strength of two guesses at once.
        """
        self.send("codex\r")
        self.send("claude\r")
        self.send("/exit\r")
        self.assertIsAgent(False)

        # And the held line is gone rather than waiting for the next prompt.
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_the_relaunched_agents_own_title_outlives_the_one_it_replaced(self):
        """The floor is the moment the reader relaunched, not the moment the
        prompt was read.

        A demotion raises the floor where it stands, which on a quick relaunch
        is *after* the new agent has announced itself -- so the title it had
        already published was masked as though the agent that left had written
        it, and the pane's row went blank until the live agent re-announced.
        """
        self.output(PROMPT)
        self.send("codex\r")
        self.announce("old-chat")
        self.assertEqual(terminal.agent_activity_snapshot()["pane"]["title"], "old-chat")

        self.send("\x03")
        self.send("\x03")
        self.send("codex\r")
        self.announce("my-project")
        self.output(PROMPT)

        self.assertIsAgent()
        self.assertEqual(
            terminal.agent_activity_snapshot()["pane"]["title"], "my-project"
        )

    def test_a_command_recalled_from_history_is_found_anyway(self):
        """The reported defect, and the reason the process reading exists.

        Up, Enter is the fastest relaunch there is, and GridVibe cannot see it:
        the arrow key is stripped as an escape sequence before the line is
        rebuilt, so nothing is submitted and nothing promotes the pane. It then
        ran Codex while calling itself a terminal -- no name in its header, no
        row on the dashboard -- and because only a submitted command promotes a
        pane, waiting never helped. Relaunching the pane was the only way back.
        """
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.machine(AGENT_TABLE)

        self.send("\x1b[A")
        self.send("\r")
        self.assertIsAgent(False)

        self.assertEqual(terminal.reconcile_pane_agents(), 1)
        self.assertIsAgent()
        self.assertTrue(self.connection["agent_runtime_armed"])

        # And the pass is quiet once it has nothing left to say.
        self.assertEqual(terminal.reconcile_pane_agents(), 0)
        self.assertIsAgent()

    def test_a_tab_completed_command_is_found_anyway(self):
        """`cod`, Tab, Enter submits `codex` and reconstructs `cod\t`."""
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.machine(AGENT_TABLE)

        self.send("cod")
        self.send("\t")
        self.send("\r")
        self.assertIsAgent(False)

        self.assertEqual(terminal.reconcile_pane_agents(), 1)
        self.assertIsAgent()

    def test_the_pass_leaves_a_panes_own_startup_command_alone(self):
        """A reading about what is running now may not rewrite what a pane runs.

        `initial_command` is persisted and replayed at the shell, so a pane
        opened with `npm run dev` that is observed running an agent keeps it --
        it is relabelled, and comes back running what the reader set. Taking it
        would be worse than useless: the retirement that follows clears the
        agent's launch line, so the pane would lose its own for good.
        """
        self.session.initial_command = "npm run dev"
        self.session.initial_command_mode = "command"
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.machine(AGENT_TABLE)

        self.assertEqual(terminal.reconcile_pane_agents(), 1)
        self.assertIsAgent()
        self.assertEqual(self.session.initial_command, "npm run dev")
        self.assertEqual(self.session.initial_command_mode, "command")

        # And the retirement takes the agent, not the pane's own command.
        self.machine(IDLE_TABLE)
        self.output(PROMPT)
        self.assertIsAgent(False)
        self.assertEqual(self.session.initial_command, "npm run dev")
        self.assertEqual(self.session.initial_command_mode, "command")

    def test_the_agents_own_launch_line_is_still_cleared_when_it_retires(self):
        self.output(PROMPT)
        self.send("codex\r")
        self.assertEqual(self.session.initial_command_mode, "agent")
        self.output(PROMPT)
        self.assertIsAgent(False)
        self.assertEqual(self.session.initial_command, "")
        self.assertEqual(self.session.initial_command_mode, "command")

    def test_the_pass_leaves_a_pane_running_nothing_alone(self):
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.machine(IDLE_TABLE)
        self.send("ls\r")
        self.assertEqual(terminal.reconcile_pane_agents(), 0)
        self.assertIsAgent(False)

    def test_the_pass_answers_for_no_pane_it_cannot_see(self):
        """An empty answer is "no idea", so it may never be read as "no agent".

        A WSL pane's processes are in another kernel's table and a remote
        pane's on another machine, and a pane whose transport never came up has
        no shell to ask about. None of them is promoted here -- and none is
        retired here either, which is the half that would break them.
        """
        self.machine(AGENT_TABLE)
        for description, connection in [
            ("wsl", {"kind": "local", "shell_kind": "wsl", "pty_process": SimpleNamespace(pid=SHELL_PID)}),
            ("remote", {"kind": "ssh", "shell_kind": "bash", "shell_pid": str(SHELL_PID)}),
            ("no transport", {"kind": "local", "shell_kind": "cmd"}),
        ]:
            with self.subTest(pane=description):
                self.registry["pane"] = self.connection = dict(connection)
                self.assertEqual(terminal.reconcile_pane_agents(), 0)
                self.assertIsAgent(False)

    def test_the_pass_leaves_a_pane_that_is_not_a_terminal_alone(self):
        self.give_the_pane_a_shell()
        self.machine(AGENT_TABLE)
        for mode in ["explorer", "browser", "agent"]:
            with self.subTest(startup_mode=mode):
                self.session.startup_mode = mode
                self.assertEqual(terminal.reconcile_pane_agents(), 0)
                self.assertEqual(self.session.startup_mode, mode)

    def test_a_prompt_does_not_retire_an_agent_that_is_still_running(self):
        """Where the OS can be asked, it settles what a prompt cannot.

        A prompt is the shell saying it has the terminal back, and it was the
        only reading there was -- so any prompt drawn while an agent was still
        running retired a pane that was working perfectly well.
        """
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.send("codex\r")
        self.assertIsAgent()

        self.machine(AGENT_TABLE)
        self.output(PROMPT)
        self.assertIsAgent()
        self.assertTrue(self.connection["agent_runtime_armed"])

        # And when it really has gone, the next prompt retires it as always.
        self.machine(IDLE_TABLE)
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_the_recovered_agent_is_retired_by_its_own_prompt(self):
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.machine(AGENT_TABLE)
        self.send("\x1b[A")
        self.send("\r")
        terminal.reconcile_pane_agents()
        self.assertIsAgent()

        self.machine(IDLE_TABLE)
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_a_pane_whose_shell_cannot_be_read_is_still_retired_by_its_prompt(self):
        """The reading is an addition to the prompt rule, never a gate on it."""
        self.output(PROMPT)
        self.send("codex\r")
        self.machine({})
        self.output(PROMPT)
        self.assertIsAgent(False)

    def test_one_panes_reading_is_not_bought_twice_in_a_row(self):
        """A shell drawing prompts in a loop may not buy a snapshot for each."""
        self.output(PROMPT)
        self.give_the_pane_a_shell()
        self.send("codex\r")
        with patch.object(terminal, "process_table", return_value=AGENT_TABLE) as table:
            self.output(PROMPT)
            self.output(PROMPT)
            self.output(PROMPT)
        self.assertEqual(table.call_count, 1)
        self.assertIsAgent()

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
