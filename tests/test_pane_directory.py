"""A directory a caller *states* for a pane: honoured, on the pane's own machine.

Two transactions start a shell somewhere a caller named: a split with a stated
``directory`` and the gated mode switch with one. Before this, both answered
with the wrong rule. A split from a terminal ignored the path and cloned where
the source stood; a split from an explorer, and every re-root of a Files pane,
resolved it through the explorer's root -- including a root the pane had
derived for itself -- so a parent directory was refused as "outside the
configured root". An observed cwd overwrote a stated explorer root, and a
terminal asked only to move answered ``200`` having done nothing.

Pinned here, each with the refusal left atomic:

- a stated path wins over where the pane is standing, is checked on the machine
  the shell runs on (this host, a WSL distribution, or the SSH host over SFTP),
  and is refused -- naming the path and the machine -- when it is not there;
- a stated path above a derived root is honoured, and the pane leaves with no
  root rather than a stale one; a configured root survives a path inside it;
- the header toggle's own ``directory`` (the folder a live explorer is showing)
  keeps its containment, so browsing a Files pane is bounded exactly as before;
- a terminal given only a directory relaunches there, and says ``changed:
  false`` when it is already standing in it.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import tests  # noqa: F401 - redirects durable state away from the real files
from tests.test_api import FakeSftp
from web import api, pane_directory
from web import config as web_config
from web import explorer as web_explorer
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import terminal_io as web_terminal_io
from web.window_intents import window_intents


class _Fixture(unittest.TestCase):
    """A real app client over temporary durable state and real directories."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        for module, attribute, filename in (
            (web_config, "CONFIG_PATH", "config.json"),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", "saved_sessions.json"),
            (web_runtime_state, "RUNTIME_STATE_PATH", "runtime_state.json"),
        ):
            patcher = patch.object(module, attribute, str(self.root / filename))
            patcher.start()
            self.addCleanup(patcher.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        window_intents.reset()
        self.addCleanup(window_intents.reset)
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()
        # No pane in this file has a live shell to ask where it is.
        patcher = patch.object(web_terminal_io, "_resolve_live_terminal_cwd", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dir(self, *parts):
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _group(self, mode="wsl"):
        return api.session_manager.create_group(
            name="G", connection_mode=mode, layout="single", terminal_count=1
        )

    def _pane(self, group=None, **fields):
        group = group or self._group(fields.get("mode", "wsl"))
        values = {
            "group_id": group.group_id,
            "host": "cmd",
            "directory": str(self._dir("repo")),
            "mode": "wsl",
            "startup_mode": "terminal",
        }
        values.update(fields)
        session = api.session_manager.create_session(**values)
        return api.session_manager.get_session(session.session_id)

    def _ssh_pane(self, group=None, **fields):
        return self._pane(
            group or self._group("ssh"),
            **{
                "host": "example.com",
                "username": "ubuntu",
                "mode": "ssh",
                "directory": "/srv/app",
                **fields,
            },
        )

    def _state(self, session_id):
        session = api.session_manager.get_session(session_id)
        return {
            name: getattr(session, name)
            for name in (
                "directory",
                "current_directory",
                "explorer_root_directory",
                "explorer_root_configured",
                "startup_mode",
                "agent_selection",
                "status",
            )
        }


# ==================== the resolver ====================


class ResolveStatedDirectoryTestCase(_Fixture):
    def test_a_local_directory_that_exists_is_answered_absolute(self):
        target = self._dir("elsewhere")
        pane = self._pane()

        self.assertEqual(
            pane_directory.resolve_stated_directory(pane, str(target)),
            os.path.normpath(str(target)),
        )

    def test_a_missing_local_directory_names_the_path_and_the_machine(self):
        pane = self._pane()
        missing = str(self.root / "gone")

        with self.assertRaises(pane_directory.StatedDirectoryError) as caught:
            pane_directory.resolve_stated_directory(pane, missing)

        self.assertIn(missing, str(caught.exception))
        self.assertIn("does not exist on GridVibe's own machine", str(caught.exception))

    def test_a_relative_path_is_refused_rather_than_resolved_against_this_process(self):
        pane = self._pane()

        with self.assertRaises(pane_directory.StatedDirectoryError) as caught:
            pane_directory.resolve_stated_directory(pane, "repo")

        self.assertIn("is not an absolute path", str(caught.exception))

    def test_a_file_is_not_a_directory(self):
        pane = self._pane()
        target = self.root / "notes.txt"
        target.write_text("x", encoding="utf-8")

        with self.assertRaises(pane_directory.StatedDirectoryError) as caught:
            pane_directory.resolve_stated_directory(pane, str(target))

        self.assertIn("is not a directory", str(caught.exception))

    def test_an_ssh_pane_asks_its_own_host_over_sftp(self):
        pane = self._ssh_pane()
        sftp = FakeSftp({"/srv": {"type": "directory"}, "/srv/app": {"type": "directory"}})

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), sftp)):
            resolved = pane_directory.resolve_stated_directory(pane, "/srv/")

        self.assertEqual(resolved, "/srv")

    def test_an_ssh_pane_refuses_a_path_its_host_does_not_have(self):
        """A Windows path handed to a Linux host is a path on the wrong machine."""
        pane = self._ssh_pane()
        sftp = FakeSftp({"/srv/app": {"type": "directory"}})

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), sftp)):
            with self.assertRaises(pane_directory.StatedDirectoryError) as caught:
                pane_directory.resolve_stated_directory(pane, "C:/Users/me")

        self.assertIn("does not exist on example.com (over SSH)", str(caught.exception))

    @unittest.skipUnless(os.name == "nt", "WSL panes exist only on a Windows host")
    def test_a_wsl_pane_reads_a_mounted_drive_from_this_host(self):
        target = self._dir("mounted")
        pane = self._pane(use_wsl=True)
        drive, rest = os.path.splitdrive(str(target))
        mounted = f"/mnt/{drive[0].lower()}{rest.replace(os.sep, '/')}"

        with patch.object(pane_directory, "wsl_directory_exists") as asked:
            self.assertEqual(pane_directory.resolve_stated_directory(pane, mounted), mounted)
        asked.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "WSL panes exist only on a Windows host")
    def test_a_wsl_pane_asks_the_distribution_about_a_linux_path(self):
        pane = self._pane(use_wsl=True, distribution="Ubuntu")

        with patch.object(pane_directory, "wsl_directory_exists", return_value=True) as asked:
            self.assertEqual(
                pane_directory.resolve_stated_directory(pane, "/home/me"), "/home/me"
            )
        asked.assert_called_once_with("/home/me", "Ubuntu")

        with patch.object(pane_directory, "wsl_directory_exists", return_value=False):
            with self.assertRaises(pane_directory.StatedDirectoryError) as caught:
                pane_directory.resolve_stated_directory(pane, "/home/nobody")
        self.assertIn("the WSL distribution Ubuntu", str(caught.exception))

    def test_a_wsl_check_that_cannot_run_is_not_an_absence(self):
        """Like an agent preflight's check_failed: it says nothing about the path."""
        with patch("web.agents._find_wsl_executable", return_value="wsl.exe"), patch.object(
            pane_directory.subprocess, "run", side_effect=OSError("no wsl")
        ):
            self.assertTrue(pane_directory.wsl_directory_exists("/home/me"))
        with patch("web.agents._find_wsl_executable", return_value="wsl.exe"), patch.object(
            pane_directory.subprocess, "run", return_value=SimpleNamespace(returncode=1)
        ) as run:
            self.assertFalse(pane_directory.wsl_directory_exists("/home/me", "Ubuntu"))
        # One argv entry, after --exec: no shell ever reads the path.
        self.assertEqual(
            run.call_args.args[0][-5:], ["Ubuntu", "--exec", "test", "-d", "/home/me"]
        )


# ==================== split ====================


class SplitAtStatedDirectoryTestCase(_Fixture):
    def _split(self, session_id, body):
        with patch.object(api.socketio, "start_background_task"), patch.object(
            api, "_agent_absent_reason", return_value=""
        ):
            response = self.client.post(f"/api/sessions/{session_id}/split", json=body)
        return response

    def test_a_terminal_split_starts_at_the_stated_directory_not_the_sources_cwd(self):
        source = self._pane()
        api.session_manager.update_session_metadata(
            source.session_id, current_directory=str(self._dir("repo", "src"))
        )
        parent = self.root

        response = self._split(
            source.session_id,
            {"axis": "horizontal", "kind": "agent", "agent": "claude",
             "stated_directory": str(parent)},
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        created = response.get_json()["session"]
        self.assertEqual(created["directory"], os.path.normpath(str(parent)))
        self.assertEqual(created["startup_mode"], "agent")
        # The source pane is untouched.
        self.assertEqual(
            api.session_manager.get_session(source.session_id).current_directory,
            str(self._dir("repo", "src")),
        )

    def test_an_explorer_source_splits_above_its_own_derived_root(self):
        source = self._pane(
            host="File Explorer",
            startup_mode="explorer",
            explorer_root_directory=str(self._dir("repo")),
            explorer_root_configured=False,
        )

        response = self._split(
            source.session_id,
            # The page's own `directory` (the browsed folder) rides beside it
            # and does not win.
            {"axis": "vertical", "directory": "", "stated_directory": str(self.root)},
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        created = response.get_json()["session"]
        self.assertEqual(created["directory"], os.path.normpath(str(self.root)))
        self.assertEqual(created["startup_mode"], "terminal")
        self.assertFalse(created["explorer_root_configured"])

    def test_an_explorer_split_is_rooted_at_the_stated_directory(self):
        source = self._pane()
        target = self._dir("docs")

        response = self._split(
            source.session_id,
            {"axis": "vertical", "kind": "explorer", "stated_directory": str(target)},
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        created = response.get_json()["session"]
        self.assertEqual(created["explorer_root_directory"], os.path.normpath(str(target)))
        self.assertTrue(created["explorer_root_configured"])

    def test_a_missing_stated_directory_adds_no_pane(self):
        source = self._pane()
        before = len(api.session_manager.get_group_sessions(source.group_id))

        response = self._split(
            source.session_id,
            {"axis": "vertical", "stated_directory": str(self.root / "gone")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("does not exist", response.get_json()["error"])
        self.assertIn("No pane was added.", response.get_json()["error"])
        self.assertEqual(len(api.session_manager.get_group_sessions(source.group_id)), before)

    def test_an_ssh_source_splits_at_a_path_on_its_own_host(self):
        source = self._ssh_pane()
        sftp = FakeSftp({"/srv": {"type": "directory"}, "/srv/app": {"type": "directory"}})

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), sftp)):
            response = self._split(
                source.session_id, {"axis": "vertical", "stated_directory": "/srv"}
            )

        self.assertEqual(response.status_code, 201, response.get_json())
        created = response.get_json()["session"]
        self.assertEqual((created["mode"], created["directory"]), ("ssh", "/srv"))

    def test_the_intent_refuses_a_missing_directory_before_recording_anything(self):
        source = self._pane()

        response = self.client.post(
            f"/api/sessions/{source.session_id}/split-intent",
            json={"axis": "vertical", "directory": str(self.root / "gone")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("No split was recorded.", response.get_json()["error"])
        self.assertEqual(window_intents.pending(), [])

    def test_the_intent_forwards_the_stated_path_under_its_own_key(self):
        """So the page's explorer default can never be mistaken for it."""
        source = self._pane(host="File Explorer", startup_mode="explorer",
                            explorer_root_directory=str(self._dir("repo")))

        recorded = self.client.post(
            f"/api/sessions/{source.session_id}/split-intent",
            json={"axis": "vertical", "directory": str(self.root)},
        ).get_json()["split_request"]
        # What the page posts back: its own explorer default merged in first.
        response = self._split(source.session_id, {"directory": "", **recorded})

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(
            response.get_json()["session"]["directory"], os.path.normpath(str(self.root))
        )


# ==================== the gated mode switch ====================


class ReRootByModeSwitchTestCase(_Fixture):
    def _pair(self, **target_fields):
        caller = self._pane()
        target = self._pane(
            api.session_manager.get_group(caller.group_id),
            created_by_session_id=caller.session_id,
            **target_fields,
        )
        api.session_manager.update_session_status(target.session_id, api.SessionStatus.CONNECTED)
        return caller, api.session_manager.get_session(target.session_id)

    def _switch(self, caller, target, **body):
        with patch.object(api, "_close_ssh_connection") as close, patch.object(
            api.socketio, "start_background_task"
        ) as start:
            response = self.client.post(
                f"/api/sessions/{target.session_id}/agent-mode-switch",
                json={"requested_by_session_id": caller.session_id, **body},
            )
        return response, close, start

    def test_a_files_pane_becomes_a_terminal_above_its_derived_root(self):
        """ISSUE-2026-057 step 2: the parent is honoured, not clamped."""
        repo = self._dir("repo")
        caller, target = self._pair(
            host="File Explorer",
            startup_mode="explorer",
            explorer_root_directory=str(repo),
            explorer_root_configured=False,
        )

        response, _close, start = self._switch(
            caller, target, startup_mode="terminal", directory=str(self.root)
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["changed"])
        state = self._state(target.session_id)
        self.assertEqual(state["directory"], os.path.normpath(str(self.root)))
        self.assertEqual(state["startup_mode"], "terminal")
        self.assertEqual(state["explorer_root_directory"], "")
        self.assertFalse(state["explorer_root_configured"])
        start.assert_called_once_with(api._connect_session, target.session_id)

    def test_a_configured_root_survives_a_stated_path_inside_it(self):
        repo = self._dir("repo")
        inner = self._dir("repo", "src")
        caller, target = self._pair(
            host="File Explorer",
            startup_mode="explorer",
            explorer_root_directory=str(repo),
            explorer_root_configured=True,
        )

        response, _close, _start = self._switch(
            caller, target, startup_mode="terminal", directory=str(inner)
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        state = self._state(target.session_id)
        self.assertEqual(state["explorer_root_directory"], str(repo))
        self.assertTrue(state["explorer_root_configured"])

    def test_a_stated_explorer_root_wins_over_the_observed_cwd(self):
        """ISSUE-2026-057 step 5: the observation no longer overrules the caller."""
        other = self._dir("other")
        caller, target = self._pair()
        api.session_manager.update_session_metadata(
            target.session_id, current_directory=str(self._dir("repo", "src"))
        )

        response, close, _start = self._switch(
            caller, target, startup_mode="explorer", directory=str(other)
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        state = self._state(target.session_id)
        self.assertEqual(os.path.normcase(state["directory"]), os.path.normcase(str(other)))
        self.assertEqual(state["startup_mode"], "explorer")
        close.assert_called_once_with(target.session_id, clear_buffer=True)

    def test_a_terminal_given_only_a_directory_relaunches_there(self):
        """ISSUE-2026-057 step 4: no longer a 200 that changed nothing."""
        caller, target = self._pair()

        response, close, start = self._switch(
            caller, target, startup_mode="terminal", directory=str(self.root)
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["changed"])
        state = self._state(target.session_id)
        self.assertEqual(state["directory"], os.path.normpath(str(self.root)))
        self.assertIsNone(state["current_directory"])
        self.assertEqual(state["status"], api.SessionStatus.PENDING)
        close.assert_called_once_with(target.session_id, clear_buffer=True)
        start.assert_called_once_with(api._connect_session, target.session_id)

    def test_a_terminal_already_standing_there_changes_nothing_and_says_so(self):
        caller, target = self._pair()
        before = self._state(target.session_id)

        response, close, start = self._switch(
            caller, target, startup_mode="terminal", directory=target.directory
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["changed"])
        self.assertEqual(self._state(target.session_id), before)
        close.assert_not_called()
        start.assert_not_called()

    def test_an_agent_pane_moved_by_override_becomes_a_plain_terminal_there(self):
        caller, target = self._pair(
            startup_mode="agent",
            initial_command_mode="agent",
            initial_command="claude",
            agent_selection="claude",
        )

        response, _close, start = self._switch(
            caller, target, startup_mode="terminal", directory=str(self.root), override=True
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        state = self._state(target.session_id)
        self.assertEqual((state["startup_mode"], state["agent_selection"]), ("terminal", ""))
        self.assertEqual(state["directory"], os.path.normpath(str(self.root)))
        start.assert_called_once()

    def test_a_missing_directory_is_refused_and_the_pane_is_left_whole(self):
        for fields in (
            {},
            {"host": "File Explorer", "startup_mode": "explorer",
             "explorer_root_directory": str(self._dir("repo"))},
        ):
            with self.subTest(startup_mode=fields.get("startup_mode", "terminal")):
                caller, target = self._pair(**fields)
                before = self._state(target.session_id)

                response, close, start = self._switch(
                    caller, target, startup_mode="terminal", directory=str(self.root / "gone")
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("does not exist", response.get_json()["error"])
                self.assertIn("Nothing was changed.", response.get_json()["error"])
                self.assertEqual(self._state(target.session_id), before)
                close.assert_not_called()
                start.assert_not_called()

    def test_a_missing_explorer_root_on_an_ssh_host_is_a_400_naming_the_host(self):
        group = self._group("ssh")
        caller = self._ssh_pane(group)
        target = self._ssh_pane(group, created_by_session_id=caller.session_id)
        before = self._state(target.session_id)
        sftp = FakeSftp({"/srv/app": {"type": "directory"}})

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), sftp)):
            response, close, _start = self._switch(
                caller, target, startup_mode="explorer", directory="/nowhere"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("example.com (over SSH)", response.get_json()["error"])
        self.assertEqual(self._state(target.session_id), before)
        close.assert_not_called()


class HeaderToggleUnchangedTestCase(_Fixture):
    """The pane header's own route: no stated path, so the old rules hold."""

    def _post(self, session_id, body):
        with patch.object(api, "_close_ssh_connection"), patch.object(
            api.socketio, "start_background_task"
        ):
            return self.client.post(f"/api/sessions/{session_id}/mode", json=body)

    def test_the_toggle_still_opens_files_where_the_shell_is_standing(self):
        pane = self._pane()
        observed = self._dir("repo", "src")
        api.session_manager.update_session_metadata(pane.session_id, current_directory=str(observed))

        response = self._post(pane.session_id, {"startup_mode": "explorer", "refresh_cwd": True})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            os.path.normcase(api.session_manager.get_session(pane.session_id).directory),
            os.path.normcase(str(observed)),
        )
        self.assertNotIn("changed", response.get_json())

    def test_the_browsed_folder_still_stays_inside_the_explorers_root(self):
        pane = self._pane(
            host="File Explorer",
            startup_mode="explorer",
            explorer_root_directory=str(self._dir("repo")),
        )
        before = self._state(pane.session_id)

        response = self._post(pane.session_id, {"startup_mode": "terminal", "directory": str(self.root)})

        self.assertEqual(response.status_code, 400)
        self.assertIn("inside the configured root", response.get_json()["error"])
        self.assertEqual(self._state(pane.session_id), before)

    def test_a_terminal_toggled_to_terminal_is_still_a_quiet_no_op(self):
        pane = self._pane()
        response = self._post(pane.session_id, {"startup_mode": "terminal"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("changed", response.get_json())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
