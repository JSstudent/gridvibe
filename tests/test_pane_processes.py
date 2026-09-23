"""What a pane is running, read from the OS rather than from what was typed.

The walk is executed against a table of this file's own making, because the
question it answers -- *is one of these binaries running below that pid* -- has
nothing to do with whatever happens to be running on the machine the suite runs
on, and a test that asked the real table would pass or fail by coincidence. The
one case that does read the machine asserts only the shape of the answer.

What is pinned:

- **The walk is scoped to the pane's own shell.** Agents run outside GridVibe
  too, so a binary somewhere else in the table is not this pane's and a
  machine-wide name search would claim it.
- **It reaches past a wrapper.** Codex sits two levels below the shell on
  Windows (`cmd` -> `node` -> `codex`), so direct children are not enough.
- **The shell itself is not a candidate**, and a pid that is not in the table
  answers nothing.
- **It is bounded** in depth and in nodes visited, and a parent map made cyclic
  by a reused pid terminates.
- **Executable names are compared in one spelling**, so `codex.exe`, a full
  path and `CODEX` are one answer.
- **A child is younger than its parent.** An orphan whose recorded parent pid
  was reused by a pane's shell started before that shell, and is not its child.
  Where a start time is unknown the edge is followed as before.
- **A failure is an empty table**, never an exception into a pump thread.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import pane_processes  # noqa: E402
from web.pane_processes import (  # noqa: E402
    ProcessTable,
    children_index,
    descendant_binary,
    descendant_process,
    normalize_binary,
    process_started_at,
    process_table,
)

AGENTS = ["codex", "claude", "gemini"]

#: A pane's shell (100) with Codex two levels under it, a second pane's shell
#: (200) running nothing, and somebody else's Codex (300) that belongs to
#: neither -- the shape the real machine had when this was written.
TABLE = {
    1: (0, "explorer"),
    100: (1, "cmd"),
    110: (100, "node"),
    120: (110, "codex"),
    130: (120, "node_repl"),
    200: (1, "powershell"),
    210: (200, "git"),
    300: (1, "chatgpt"),
    310: (300, "codex"),
}


class PaneProcessWalkTestCase(unittest.TestCase):
    def setUp(self):
        self.children = children_index(TABLE)

    def walk(self, root, table=None, children=None):
        return descendant_binary(
            TABLE if table is None else table,
            self.children if children is None else children,
            root,
            AGENTS,
        )

    def test_an_agent_below_a_wrapper_is_found(self):
        self.assertEqual(self.walk(100), "codex")

    def test_a_pane_running_nothing_reports_nothing(self):
        self.assertEqual(self.walk(200), "")

    def test_another_processes_agent_is_not_this_panes(self):
        """The rule the whole reading rests on: scope, not a name search."""
        self.assertEqual(self.walk(200), "")
        self.assertEqual(self.walk(300), "codex")
        # And from the root both hang off, the first one found is a real
        # descendant either way -- which is why no caller passes a root that
        # is not a pane's own shell.
        self.assertIn(self.walk(1), {"codex"})

    def test_the_shell_itself_is_not_a_candidate(self):
        table = dict(TABLE)
        table[400] = (1, "codex")
        self.assertEqual(
            descendant_binary(table, children_index(table), 400, AGENTS), ""
        )

    def test_a_pid_the_table_does_not_hold_answers_nothing(self):
        self.assertEqual(self.walk(999999), "")
        self.assertEqual(self.walk(0), "")
        self.assertEqual(self.walk(None), "")

    def test_an_empty_table_or_no_binaries_answers_nothing(self):
        self.assertEqual(descendant_binary({}, {}, 100, AGENTS), "")
        self.assertEqual(self.walk(100, table=TABLE, children={}), "")
        self.assertEqual(descendant_binary(TABLE, self.children, 100, []), "")
        self.assertEqual(descendant_binary(TABLE, self.children, 100, ["", None]), "")

    def test_a_cycle_from_a_reused_pid_terminates(self):
        table = {10: (12, "cmd"), 11: (10, "node"), 12: (11, "node")}
        self.assertEqual(
            descendant_binary(table, children_index(table), 10, AGENTS), ""
        )

    def test_the_walk_stops_at_its_depth_ceiling(self):
        table = {1000: (1, "cmd")}
        for step in range(1, 30):
            table[1000 + step] = (1000 + step - 1, "node")
        deep = 1000 + 29
        table[deep] = (deep - 1, "codex")
        children = children_index(table)
        self.assertEqual(descendant_binary(table, children, 1000, AGENTS), "")
        with patch.object(pane_processes, "PANE_PROCESS_MAX_DEPTH", 40):
            self.assertEqual(descendant_binary(table, children, 1000, AGENTS), "codex")

    def test_the_walk_stops_at_its_visited_ceiling(self):
        table = {2000: (1, "cmd")}
        for step in range(1, 600):
            table[2000 + step] = (2000, "node")
        table[2000 + 599] = (2000, "codex")
        children = children_index(table)
        with patch.object(pane_processes, "PANE_PROCESS_MAX_VISITED", 4):
            self.assertEqual(descendant_binary(table, children, 2000, AGENTS), "")


class ReusedParentPidTestCase(unittest.TestCase):
    """Windows keeps an orphan's parent pid, and a new pane's shell can reuse it.

    Claude Desktop's own `claude.exe`, left running after its launcher exited,
    named pid 100 as its parent; pid 100 is now this pane's `cmd`. Only the
    start times can tell the orphan from a child.
    """

    SHELL_STARTED = 5000.0

    def table(self, orphan_started, starts=None):
        entries = {
            1: (0, "explorer"),
            100: (1, "cmd"),
            150: (100, "claude"),
        }
        known = {1: 1.0, 100: self.SHELL_STARTED, 150: orphan_started}
        known.update(starts or {})
        return ProcessTable(entries, known.get)

    def walk(self, table):
        return descendant_process(table, children_index(table), 100, AGENTS)

    def test_an_orphan_older_than_the_shell_is_not_its_child(self):
        self.assertEqual(self.walk(self.table(self.SHELL_STARTED - 60)), ("", 0))

    def test_a_process_the_shell_started_is_found_with_its_pid(self):
        self.assertEqual(self.walk(self.table(self.SHELL_STARTED + 1)), ("claude", 150))
        # Started in the same clock tick as its parent is still its child.
        self.assertEqual(self.walk(self.table(self.SHELL_STARTED)), ("claude", 150))

    def test_nothing_below_an_orphan_is_the_panes_either(self):
        table = ProcessTable(
            {100: (1, "cmd"), 140: (100, "node"), 150: (140, "claude")},
            {100: self.SHELL_STARTED, 140: self.SHELL_STARTED - 60, 150: self.SHELL_STARTED + 5}.get,
        )
        self.assertEqual(self.walk(table), ("", 0))

    def test_an_unknown_start_is_followed_as_before(self):
        for starts in [{150: None}, {100: None}]:
            with self.subTest(starts=starts):
                self.assertEqual(
                    self.walk(self.table(self.SHELL_STARTED - 60, starts)), ("claude", 150)
                )
        plain = {100: (1, "cmd"), 150: (100, "claude")}
        self.assertEqual(self.walk(plain), ("claude", 150))
        self.assertIsNone(process_started_at(plain, 150))

    def test_a_start_time_is_read_once_and_a_failure_is_unknown(self):
        calls = []

        def reader(pid):
            calls.append(pid)
            if pid == 2:
                raise OSError("access denied")
            return 10.0

        table = ProcessTable({1: (0, "a"), 2: (1, "b")}, reader)
        self.assertEqual(table.started_at(1), 10.0)
        self.assertEqual(table.started_at(1), 10.0)
        self.assertIsNone(table.started_at(2))
        self.assertIsNone(table.started_at(2))
        self.assertEqual(calls, [1, 2])


class ExecutableNameTestCase(unittest.TestCase):
    def test_one_spelling(self):
        for value in [
            "codex",
            "codex.exe",
            "CODEX.EXE",
            "Codex.Exe",
            "codex.cmd",
            "codex.bat",
            r"C:\tools\bin\codex.exe",
            "/usr/local/bin/codex",
            "  codex.exe  ",
        ]:
            with self.subTest(value=value):
                self.assertEqual(normalize_binary(value), "codex")

    def test_nothing_usable(self):
        for value in ["", "   ", None]:
            with self.subTest(value=value):
                self.assertEqual(normalize_binary(value), "")

    def test_a_name_that_only_looks_like_a_suffix_is_kept(self):
        self.assertEqual(normalize_binary("my-codex-wrapper"), "my-codex-wrapper")
        self.assertEqual(normalize_binary("codex.js"), "codex.js")


class ProcessTableTestCase(unittest.TestCase):
    def test_the_machines_own_table_has_the_shape_the_walk_expects(self):
        table = process_table()
        self.assertTrue(table, "the OS should answer for the process running this test")
        self.assertIn(os.getpid(), table)
        parent, name = table[os.getpid()]
        self.assertIsInstance(parent, int)
        self.assertEqual(name, normalize_binary(name))
        self.assertTrue(name)

    def test_this_process_is_found_under_its_own_parent(self):
        """The real table, walked the way a pane's shell is walked."""
        table = process_table()
        parent = table[os.getpid()][0]
        if parent not in table:
            self.skipTest("this process's parent has already exited")
        name = table[os.getpid()][1]
        self.assertEqual(
            descendant_binary(table, children_index(table), parent, [name]), name
        )

    def test_the_machines_own_start_times_order_a_child_after_its_parent(self):
        import time

        table = process_table()
        started = process_started_at(table, os.getpid())
        self.assertIsNotNone(started, "the OS should say when this process started")
        self.assertLessEqual(started, time.time() + 1)
        parent = table[os.getpid()][0]
        parent_started = process_started_at(table, parent) if parent in table else None
        if parent_started is None:
            self.skipTest("this process's parent has already exited")
        self.assertGreaterEqual(started, parent_started)

    def test_an_os_that_will_not_answer_is_an_empty_table(self):
        reader = "_windows_process_table" if os.name == "nt" else "_posix_process_table"
        with patch.object(pane_processes, reader, side_effect=OSError("nope")):
            self.assertEqual(process_table(), {})


if __name__ == "__main__":
    unittest.main()
