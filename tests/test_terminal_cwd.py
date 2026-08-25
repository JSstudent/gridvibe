"""Behavioural tests for the terminal working-directory observer.

``web/terminal_cwd.py`` is pure text-in/values-out, so it is tested by running
it against real byte streams rather than by reading its source. The stream side
(when to parse, where the residue lives, what the answer is used for) is
covered by ``tests/test_api.py``.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web.terminal_cwd import (  # noqa: E402
    CWD_EVENT_DIRECTORY,
    CWD_EVENT_SHELL_PID,
    CWD_RESIDUE_MAX_CHARS,
    decode_osc7_target,
    latest_event,
    normalize_observed_cwd,
    parse_cwd_events,
    remote_shell_integration_command,
    shell_integration_arguments,
    shell_integration_environment,
)

ESC = "\x1b"
ST = ESC + "\\"
BEL = "\x07"


def osc7(path: str, host: str = "box") -> str:
    return f"{ESC}]7;file://{host}{path}{ST}"


def osc9(path: str) -> str:
    return f"{ESC}]9;9;{path}{ST}"


class TerminalCwdParserTestCase(unittest.TestCase):
    """Both sequence forms, read out of output the shell already produces."""

    def test_osc7_reports_the_posix_working_directory(self):
        events, residue = parse_cwd_events(f"user@box:~$ cd src\r\n{osc7('/srv/app/src')}")

        self.assertEqual(events, [(CWD_EVENT_DIRECTORY, "/srv/app/src")])
        self.assertEqual(residue, "")

    def test_osc7_accepts_a_bel_terminator(self):
        events, _ = parse_cwd_events(f"{ESC}]7;file://box/srv/app{BEL}")

        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/srv/app")

    def test_osc9_reports_the_windows_working_directory(self):
        events, residue = parse_cwd_events(osc9("C:\\Users\\dev\\repo") + "C:\\Users\\dev\\repo>")

        self.assertEqual(events, [(CWD_EVENT_DIRECTORY, "C:\\Users\\dev\\repo")])
        self.assertEqual(residue, "")

    def test_the_last_sequence_in_a_chunk_wins(self):
        events, _ = parse_cwd_events(osc7("/srv/app") + "cd src\r\n" + osc7("/srv/app/src"))

        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/srv/app/src")

    def test_a_sequence_split_across_two_reads_is_still_read(self):
        """A 4 KiB read boundary is not an observation the pane gets to lose."""
        whole = f"building...{osc7('/srv/app/src')}$ "
        cut = whole.index("app") + 1

        first_events, residue = parse_cwd_events(whole[:cut])
        self.assertEqual(first_events, [])
        self.assertTrue(residue)

        second_events, tail = parse_cwd_events(whole[cut:], residue)
        self.assertEqual(latest_event(second_events, CWD_EVENT_DIRECTORY), "/srv/app/src")
        self.assertEqual(tail, "")

    def test_a_sequence_split_inside_its_terminator_is_still_read(self):
        first_events, residue = parse_cwd_events(f"{ESC}]7;file://box/srv/app{ESC}")
        self.assertEqual(first_events, [])

        second_events, tail = parse_cwd_events("\\$ ", residue)
        self.assertEqual(latest_event(second_events, CWD_EVENT_DIRECTORY), "/srv/app")
        self.assertEqual(tail, "")

    def test_a_completed_sequence_is_reported_once(self):
        events, residue = parse_cwd_events(osc7("/srv/app"))
        self.assertEqual(len(events), 1)

        again, _ = parse_cwd_events("$ ", residue)
        self.assertEqual(again, [])

    def test_a_malformed_sequence_is_ignored(self):
        stream = f"{ESC}]7;{ST}{ESC}]9;9;{ST}{ESC}[0m{ESC}]0;a title{BEL}"

        events, residue = parse_cwd_events(stream)

        self.assertEqual(events, [])
        self.assertEqual(residue, "")

    def test_ordinary_terminal_output_costs_no_residue(self):
        events, residue = parse_cwd_events(f"{ESC}[2J{ESC}[1;1Hhello\r\n")

        self.assertEqual(events, [])
        self.assertEqual(residue, "")

    def test_an_unterminated_sequence_cannot_grow_the_residue(self):
        """A stream that opens a sequence and never closes it is bounded."""
        events, residue = parse_cwd_events(f"{ESC}]7;" + "A" * (CWD_RESIDUE_MAX_CHARS * 4))

        self.assertEqual(events, [])
        self.assertLessEqual(len(residue), CWD_RESIDUE_MAX_CHARS)

        for _ in range(5):
            events, residue = parse_cwd_events("B" * (CWD_RESIDUE_MAX_CHARS * 4), residue)
            self.assertEqual(events, [])
            self.assertLessEqual(len(residue), CWD_RESIDUE_MAX_CHARS)

    def test_a_sequence_after_a_dropped_residue_is_still_read(self):
        _, residue = parse_cwd_events(f"{ESC}]7;" + "A" * (CWD_RESIDUE_MAX_CHARS * 4))

        events, _ = parse_cwd_events(osc7("/srv/app"), residue)

        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/srv/app")

    def test_the_shell_pid_report_is_read_once(self):
        events, _ = parse_cwd_events(f"{ESC}]777;gridvibe-pid;4821{ST}" + osc7("/srv/app"))

        self.assertEqual(latest_event(events, CWD_EVENT_SHELL_PID), "4821")
        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/srv/app")

    def test_a_non_numeric_shell_pid_is_dropped(self):
        events, _ = parse_cwd_events(f"{ESC}]777;gridvibe-pid;$$; rm -rf /{ST}")

        self.assertEqual(events, [])


class TerminalCwdDecodingTestCase(unittest.TestCase):
    """The payload is a URL on one side of the app and a native path on the other."""

    def test_a_file_url_is_percent_decoded(self):
        self.assertEqual(decode_osc7_target("file://box/srv/my%20app"), "/srv/my app")

    def test_a_bare_path_payload_is_accepted(self):
        self.assertEqual(decode_osc7_target("/srv/app"), "/srv/app")

    def test_a_wsl_mount_path_normalizes_to_its_windows_drive(self):
        self.assertEqual(
            normalize_observed_cwd("/mnt/c/Users/dev/repo", "wsl", on_windows=True),
            "C:\\Users\\dev\\repo",
        )

    def test_a_wsl_mount_root_normalizes_to_the_drive_root(self):
        self.assertEqual(normalize_observed_cwd("/mnt/d", "wsl", on_windows=True), "D:\\")

    def test_a_linux_path_is_untouched_off_a_wsl_pane(self):
        self.assertEqual(
            normalize_observed_cwd("/mnt/c/Users/dev", "posix", on_windows=False),
            "/mnt/c/Users/dev",
        )

    def test_a_windows_drive_url_path_loses_its_leading_slash(self):
        self.assertEqual(
            normalize_observed_cwd("/C:/Users/dev", "powershell", on_windows=True),
            "C:\\Users\\dev",
        )

    def test_a_remote_posix_path_survives_a_windows_host(self):
        self.assertEqual(normalize_observed_cwd("/srv/app", "posix", on_windows=True), "/srv/app")


class ShellIntegrationInstallTestCase(unittest.TestCase):
    """How each shell family is given the hook - and which one is typed at."""

    def test_cmd_takes_its_prompt_from_the_environment(self):
        environment = shell_integration_environment("cmd")

        self.assertEqual(list(environment), ["PROMPT"])
        self.assertIn("$e]9;9;$P$e", environment["PROMPT"])
        self.assertTrue(environment["PROMPT"].endswith("$P$G"))

    def test_a_posix_shell_takes_its_hook_from_prompt_command(self):
        environment = shell_integration_environment("posix")

        self.assertEqual(list(environment), ["PROMPT_COMMAND"])
        self.assertIn("]9;9;", environment["PROMPT_COMMAND"])
        self.assertIn("$PWD", environment["PROMPT_COMMAND"])

    def test_a_wsl_shell_adds_its_variable_to_an_existing_wslenv(self):
        environment = shell_integration_environment("wsl", {"WSLENV": "MY_VAR/p"})

        self.assertEqual(environment["WSLENV"], "MY_VAR/p:PROMPT_COMMAND")
        self.assertIn("]9;9;", environment["PROMPT_COMMAND"])

    def test_a_wsl_shell_does_not_repeat_a_variable_wslenv_already_names(self):
        environment = shell_integration_environment(
            "wsl", {"WSLENV": "PROMPT_COMMAND/w:MY_VAR"}
        )

        self.assertEqual(environment["WSLENV"], "PROMPT_COMMAND/w:MY_VAR")

    def test_powershell_takes_its_hook_as_an_argument(self):
        arguments = shell_integration_arguments("powershell")

        self.assertEqual(arguments[:2], ["-NoExit", "-Command"])
        self.assertIn("Copy-Item Function:prompt Function:_GridVibePrompt", arguments[2])
        self.assertIn("(_GridVibePrompt)", arguments[2])
        self.assertIn("]9;9;", arguments[2])
        self.assertEqual(shell_integration_environment("powershell"), {})

    def test_no_other_shell_family_is_given_anything(self):
        self.assertEqual(shell_integration_environment("fish"), {})
        self.assertEqual(shell_integration_arguments("fish"), [])
        self.assertEqual(shell_integration_arguments("cmd"), [])

    def test_the_remote_line_is_one_short_line(self):
        """It is typed at a prompt, so its length is what the reader pays."""
        command = remote_shell_integration_command()

        self.assertNotIn("\n", command)
        self.assertLess(len(command), 256)

    def test_the_remote_line_stays_out_of_shell_history(self):
        self.assertTrue(remote_shell_integration_command().startswith(" "))

    def test_the_remote_line_covers_both_prompt_mechanisms(self):
        command = remote_shell_integration_command()

        self.assertIn("precmd_functions+=(_gv)", command)
        self.assertIn('PROMPT_COMMAND="_gv${PROMPT_COMMAND:+;$PROMPT_COMMAND}"', command)
        self.assertIn("gridvibe-pid", command)

    def test_the_posix_hooks_never_expand_the_path_into_a_printf_format(self):
        """`%` is a legal path character, and printf's format argument eats it.

        Expanding `$PWD` into the format made a directory named
        `/srv/100%done` a conversion specification: bash rendered the sequence
        as `/srv/1000one` and the pane reported a directory that does not
        exist. The path is a data argument in both POSIX hooks, so `%s` is the
        only conversion either format carries for it -- which is also why every
        format here is single-quoted, keeping the shell out of it entirely.
        """
        for name, hook in (
            ("local", shell_integration_environment("posix")["PROMPT_COMMAND"]),
            ("remote", remote_shell_integration_command()),
        ):
            with self.subTest(hook=name):
                formats = re.findall(r"'([^']*)'", hook)
                self.assertTrue(formats, "no single-quoted printf format found")
                for candidate in formats:
                    self.assertNotIn("$PWD", candidate)
                self.assertIn("]9;9;%s", hook)
                self.assertIn('"$PWD"', hook)


class ShellIntegrationRoundTripTestCase(unittest.TestCase):
    """What a real shell would emit from the installed hook, read back."""

    def test_a_bash_prompt_emission_round_trips(self):
        # GridVibe's own POSIX hooks emit OSC 9;9, but a shell that arrives
        # with its own OSC 7 hook installed is still read:
        # printf '\033]7;file://%s%s\033\\' "$HOSTNAME" "$PWD"
        emitted = f"{ESC}]7;file://buildbox/home/dev/project{ST}dev@buildbox:~/project$ "

        events, residue = parse_cwd_events(emitted)

        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/home/dev/project")
        self.assertEqual(residue, "")

    def test_the_posix_hooks_emission_round_trips(self):
        # printf '\033]9;9;%s\033\\' "$PWD"
        emitted = f"{ESC}]9;9;/home/dev/project{ST}dev@buildbox:~/project$ "

        events, residue = parse_cwd_events(emitted)

        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/home/dev/project")
        self.assertEqual(residue, "")

    def test_a_percent_in_the_path_survives_the_posix_hook(self):
        """The whole point of F13: `/srv/100%done` reaches the parser intact."""
        emitted = f"{ESC}]9;9;/srv/100%done{ST}dev@box:/srv/100%done$ "

        events, residue = parse_cwd_events(emitted)

        self.assertEqual(latest_event(events, CWD_EVENT_DIRECTORY), "/srv/100%done")
        self.assertEqual(residue, "")

    def test_a_raw_path_sequence_is_never_percent_decoded(self):
        """A directory literally named `literal%2Fname` is not two segments.

        OSC 7 is a URL and its reader has to decode; OSC 9;9 carries a path,
        and decoding it would invent a separator the filesystem never had.
        `test_a_file_url_is_percent_decoded` still protects the URL side.
        """
        events, _ = parse_cwd_events(osc9("/srv/literal%2Fname"))

        self.assertEqual(
            latest_event(events, CWD_EVENT_DIRECTORY), "/srv/literal%2Fname"
        )

    def test_a_wsl_emission_still_translates_to_its_windows_path(self):
        """The sequence changed; what the WSL translation reads did not."""
        events, _ = parse_cwd_events(osc9("/mnt/c/Users/dev/gv 100%done"))

        self.assertEqual(
            normalize_observed_cwd(
                latest_event(events, CWD_EVENT_DIRECTORY), "wsl", on_windows=True
            ),
            "C:\\Users\\dev\\gv 100%done",
        )

    def test_a_cmd_prompt_emission_round_trips(self):
        # PROMPT=$e]9;9;$P$e\$P$G
        emitted = f"{ESC}]9;9;C:\\repo{ST}C:\\repo>"

        events, _ = parse_cwd_events(emitted)

        self.assertEqual(
            normalize_observed_cwd(
                latest_event(events, CWD_EVENT_DIRECTORY), "cmd", on_windows=True
            ),
            "C:\\repo",
        )


if __name__ == "__main__":
    unittest.main()
