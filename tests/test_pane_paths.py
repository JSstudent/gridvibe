"""Where a pane is, and where every save surface says it is.

Four facts (`web/pane_paths.py`): where the pane launches next, where it was
built, the explorer's confinement boundary, and whether anybody chose that
boundary. They used to be decided separately by the runtime snapshot, the
reusable-preset merge and the lifecycle exit save, and those three disagreed --
so the cases here assert them *separately* and assert that the surfaces agree.

The transitions are the other half: a Terminal/Agent -> Files switch now
derives its root from where the pane is standing, so a pane launched on a
parent holding three repositories opens Files on the repository its shell
walked into, and no root it was carrying pins it there afterwards.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import api
from web import config as web_config
from web import lifecycle as web_lifecycle
from web import pane_paths
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import terminal_io as web_terminal_io

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
NODE = shutil.which("node")


class PanePathCaptureTestCase(unittest.TestCase):
    """The pure reading: one pane snapshot in, four facts out."""

    def test_an_observation_is_where_the_pane_launches_next(self):
        captured = pane_paths.capture_pane_paths(
            {
                "directory": "/srv/app",
                "current_directory": "/srv/app/worker",
                "startup_mode": "terminal",
            }
        )

        self.assertEqual(captured["directory"], "/srv/app/worker")
        # Nothing moves the build directory, and an unstated one is the
        # recorded directory -- not the observation that has just passed it.
        self.assertEqual(captured["launch_directory"], "/srv/app")

    def test_an_absent_observation_is_absent_and_not_a_guess(self):
        """A pane nothing has observed answers with what it recorded."""
        captured = pane_paths.capture_pane_paths(
            {"directory": "/srv/app", "current_directory": None}
        )

        self.assertEqual(captured["directory"], "/srv/app")
        self.assertEqual(captured["launch_directory"], "/srv/app")

    def test_the_root_is_captured_beside_the_directory_not_inside_it(self):
        """The two facts a pane browsing below its root has to state."""
        captured = pane_paths.capture_pane_paths(
            {
                "directory": "/srv/app/src",
                "launch_directory": "/srv",
                "explorer_root_directory": "/srv/app",
                "explorer_root_configured": True,
                "startup_mode": "explorer",
            }
        )

        self.assertEqual(captured["directory"], "/srv/app/src")
        self.assertEqual(captured["launch_directory"], "/srv")
        self.assertEqual(captured["explorer_root_directory"], "/srv/app")
        self.assertTrue(captured["explorer_root_configured"])

    def test_an_explicit_false_is_a_value_and_not_an_absence(self):
        """The flag this whole field exists for: a root the pane derived."""
        captured = pane_paths.capture_pane_paths(
            {
                "directory": "/srv/app",
                "explorer_root_directory": "/srv/app",
                "explorer_root_configured": False,
                "startup_mode": "explorer",
            }
        )

        self.assertEqual(captured["explorer_root_directory"], "/srv/app")
        self.assertFalse(captured["explorer_root_configured"])

    def test_a_record_stating_no_flag_falls_back_to_what_the_pane_is(self):
        """The documented legacy default, applied only when unstated.

        A root on an explorer pane is the boundary that pane was built with. A
        root on a terminal pane can only be one an older transition derived and
        a snapshot carried back, and calling that chosen is what pinned a
        restored pane to a directory nobody picked.
        """
        explorer = pane_paths.capture_pane_paths(
            {
                "directory": "/srv/app",
                "explorer_root_directory": "/srv/app",
                "startup_mode": "explorer",
            }
        )
        terminal = pane_paths.capture_pane_paths(
            {
                "directory": "/srv/app",
                "explorer_root_directory": "/srv/app",
                "startup_mode": "terminal",
            }
        )

        self.assertTrue(explorer["explorer_root_configured"])
        self.assertFalse(terminal["explorer_root_configured"])

    def test_a_preset_entry_reads_the_same_way_without_an_observation(self):
        """A stored entry has no live pane behind it; the rules are the same."""
        legacy = pane_paths.saved_pane_paths({"directory": "C:/repo"}, "explorer")
        stated = pane_paths.saved_pane_paths(
            {
                "directory": "C:/repo/src",
                "explorer_root_directory": "C:/repo",
                "explorer_root_configured": False,
            },
            "explorer",
        )

        self.assertEqual(legacy["directory"], "C:/repo")
        self.assertEqual(legacy["explorer_root_directory"], "")
        self.assertFalse(legacy["explorer_root_configured"])
        self.assertEqual(stated["directory"], "C:/repo/src")
        self.assertEqual(stated["explorer_root_directory"], "C:/repo")
        self.assertFalse(stated["explorer_root_configured"])
        # A preset is a template, so it never carries a build directory: a pane
        # launched from one is built now, at the directory the preset gives it.
        self.assertNotIn("launch_directory", stated)


class PresetMergePathTestCase(unittest.TestCase):
    """What an *existing* preset takes from the live group being saved."""

    @staticmethod
    def _base(terminals):
        return {
            "connection_mode": "wsl",
            "terminal_count": len(terminals),
            "layout": "vertical",
            "wsl": {
                "distribution": "Ubuntu",
                "username": "saso",
                "default_dir": "C:/collection",
            },
            "terminals": terminals,
        }

    def test_the_live_root_and_its_provenance_both_reach_the_preset(self):
        merged = web_saved_sessions._merge_workspace_session_config(
            self._base([{"title": "Files", "directory": "C:/collection", "startup_mode": "explorer"}]),
            {
                "terminal_count": 1,
                "layout": "vertical",
                "terminals": [
                    {
                        "title": "Files",
                        "directory": "C:/collection/repo-b/src",
                        "explorer_root_directory": "C:/collection/repo-b",
                        "explorer_root_configured": False,
                        "startup_mode": "explorer",
                    }
                ],
            },
        )

        saved = merged["terminals"][0]
        self.assertEqual(saved["directory"], "C:/collection/repo-b/src")
        self.assertEqual(saved["explorer_root_directory"], "C:/collection/repo-b")
        self.assertFalse(saved["explorer_root_configured"])
        # Launcher setup is not a pane location and is still the preset's own.
        self.assertEqual(merged["wsl"]["default_dir"], "C:/collection")
        self.assertEqual(merged["wsl"]["distribution"], "Ubuntu")

    def test_a_payload_that_states_no_directory_leaves_the_preset_alone(self):
        """"Could not find out" is not "the pane has no directory"."""
        merged = web_saved_sessions._merge_workspace_session_config(
            self._base([{"title": "Shell", "directory": "C:/collection/repo-a", "startup_mode": "terminal"}]),
            {
                "terminal_count": 1,
                "layout": "vertical",
                "terminals": [{"title": "Shell", "startup_mode": "terminal"}],
            },
        )

        self.assertEqual(merged["terminals"][0]["directory"], "C:/collection/repo-a")

    def test_each_pane_keeps_its_own_path_when_the_live_order_changed(self):
        """A merge walks by index, so the live order is what it must follow.

        The reader reordered two panes and saved. Attaching the preset's old
        first path to the new first pane would hand one pane another pane's
        directory -- silently, and durably.
        """
        merged = web_saved_sessions._merge_workspace_session_config(
            self._base(
                [
                    {"title": "A", "directory": "C:/collection/repo-a", "startup_mode": "terminal"},
                    {"title": "B", "directory": "C:/collection/repo-b", "startup_mode": "terminal"},
                ]
            ),
            {
                "terminal_count": 2,
                "layout": "vertical",
                "terminals": [
                    {"title": "B", "directory": "C:/collection/repo-b", "startup_mode": "terminal"},
                    {"title": "A", "directory": "C:/collection/repo-a", "startup_mode": "terminal"},
                ],
            },
        )

        self.assertEqual(
            [terminal["directory"] for terminal in merged["terminals"][:2]],
            ["C:/collection/repo-b", "C:/collection/repo-a"],
        )

    def test_a_pane_that_left_explorer_mode_drops_the_root_with_the_mode(self):
        merged = web_saved_sessions._merge_workspace_session_config(
            self._base(
                [
                    {
                        "title": "Files",
                        "directory": "C:/collection/repo-b",
                        "explorer_root_directory": "C:/collection/repo-b",
                        "explorer_root_configured": True,
                        "startup_mode": "explorer",
                    }
                ]
            ),
            {
                "terminal_count": 1,
                "layout": "vertical",
                "terminals": [
                    {
                        "title": "Shell",
                        "directory": "C:/collection/repo-b/src",
                        "startup_mode": "terminal",
                    }
                ],
            },
        )

        saved = merged["terminals"][0]
        self.assertEqual(saved["startup_mode"], "terminal")
        self.assertEqual(saved["directory"], "C:/collection/repo-b/src")
        self.assertEqual(saved["explorer_root_directory"], "")
        self.assertFalse(saved["explorer_root_configured"])


class LiveSaveSurfaceAgreementTestCase(unittest.TestCase):
    """The snapshot and the exit-save preset read one live pane one way."""

    @staticmethod
    def _pane(**overrides):
        pane = {
            "session_id": "s1",
            "title": "Files",
            "host": "File Explorer",
            "username": "",
            "port": 22,
            "directory": "C:/collection/repo-b/src",
            "current_directory": None,
            "launch_directory": "C:/collection",
            "explorer_root_directory": "C:/collection/repo-b",
            "explorer_root_configured": False,
            "startup_mode": "explorer",
            "initial_command": "",
            "initial_command_mode": "explorer",
            "agent_selection": "",
            "custom_agent": "",
            "agent_auto_mode": False,
            "distribution": "",
            "use_wsl": False,
            "use_powershell": False,
        }
        pane.update(overrides)
        return pane

    def test_the_snapshot_and_the_exit_save_record_the_same_location(self):
        pane = self._pane()

        snapshot = web_runtime_state._snapshot_session(pane)
        live_config = web_lifecycle._live_group_config(
            {"connection_mode": "wsl", "layout": "single", "sessions": [pane]}
        )
        preset = live_config["terminals"][0]

        for field in ("directory", "explorer_root_directory", "explorer_root_configured"):
            with self.subTest(field=field):
                self.assertEqual(snapshot[field], preset[field])
        self.assertEqual(snapshot["directory"], "C:/collection/repo-b/src")
        self.assertEqual(snapshot["explorer_root_directory"], "C:/collection/repo-b")
        # Only the snapshot replays the build directory: a restore is the same
        # pane coming back, a preset launch is a new pane being built.
        self.assertEqual(snapshot["launch_directory"], "C:/collection")

    def test_the_exit_save_follows_a_shell_that_moved(self):
        """The reported failure: an agent saved where it was imported from."""
        pane = self._pane(
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="codex",
            initial_command="codex",
            explorer_root_directory="",
            explorer_root_configured=False,
            directory="C:/collection",
            current_directory="C:/collection/repo-b",
        )

        live_config = web_lifecycle._live_group_config(
            {"connection_mode": "wsl", "layout": "single", "sessions": [pane]}
        )

        self.assertEqual(live_config["terminals"][0]["directory"], "C:/collection/repo-b")
        self.assertEqual(live_config["terminals"][0]["agent_selection"], "codex")
        self.assertEqual(
            web_runtime_state._snapshot_session(pane)["directory"],
            "C:/collection/repo-b",
        )

    def test_the_step_2_default_folder_is_not_a_pane_location(self):
        """It names the group's target: the first pane's root when it has one."""
        live_config = web_lifecycle._live_group_config(
            {"connection_mode": "wsl", "layout": "single", "sessions": [self._pane()]}
        )

        self.assertEqual(live_config["wsl"]["default_dir"], "C:/collection/repo-b")


class PaneRootTransitionTestCase(unittest.TestCase):
    """Terminal/Agent -> Files, and what a saved root may still do afterwards."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        for module, attribute, name in (
            (web_config, "CONFIG_PATH", "config.json"),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", "saved_sessions.json"),
            (web_runtime_state, "RUNTIME_STATE_PATH", "runtime_state.json"),
        ):
            patcher = patch.object(
                module, attribute, str(Path(self.temp_dir.name) / name)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)

        # collection/ holds three independent worktrees plus a plain folder.
        self.collection = Path(self.temp_dir.name) / "collection"
        self.repo_b = self.collection / "repo-b"
        self.nested = self.repo_b / "src"
        self.nested.mkdir(parents=True)
        self.plain = self.collection / "notes"
        self.plain.mkdir()
        self._run_git(self.repo_b, "init")

    def _run_git(self, repo_dir: Path, *args: str):
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def _launch(self, **fields):
        """One live Local Repo pane, built exactly as a launch would build it."""
        payload = {"connection_mode": "wsl", "sessions": [fields]}
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post("/api/sessions", json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["sessions"][0]["session_id"]

    def _open_files(self, session_id, cwd):
        with patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd", return_value=str(cwd)
        ), patch.object(api, "_close_ssh_connection"):
            response = self.client.post(
                f"/api/sessions/{session_id}/mode",
                json={"startup_mode": "explorer", "refresh_cwd": True},
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def _open_terminal(self, session_id):
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                f"/api/sessions/{session_id}/mode",
                json={"startup_mode": "terminal", "directory": ""},
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_a_terminal_inside_a_repository_opens_files_on_that_repository(self):
        """Matrix row 1, from a pane whose configured root is the parent."""
        session_id = self._launch(
            title="Files",
            directory=str(self.collection),
            startup_mode="explorer",
            explorer_root_directory=str(self.collection),
            explorer_root_configured=True,
        )
        self._open_terminal(session_id)

        payload = self._open_files(session_id, self.nested)

        self.assertEqual(payload["explorer_open_path"], "src")
        session = api.session_manager.get_session(session_id)
        self.assertEqual(Path(session.explorer_root_directory), self.repo_b.resolve())
        self.assertEqual(Path(session.directory), self.nested.resolve())
        self.assertFalse(session.explorer_root_configured)

    def test_a_shell_that_walked_up_to_a_plain_folder_roots_there(self):
        """Matrix row 5: the previous repository root must not constrain it."""
        session_id = self._launch(
            title="Shell", directory=str(self.repo_b), startup_mode="terminal"
        )
        self._open_files(session_id, self.nested)
        self._open_terminal(session_id)

        self._open_files(session_id, self.plain)

        session = api.session_manager.get_session(session_id)
        self.assertEqual(Path(session.explorer_root_directory), self.plain.resolve())

    def test_a_restored_root_is_replayed_exactly_and_still_never_pins(self):
        """Matrix row 9, both halves.

        A saved explorer comes back on the root it was saved with, wider than
        the directory it was browsing -- nothing re-derives it. The *next*
        explicit transition out of a terminal derives a fresh one, because a
        persisted root is a record and not a pin.
        """
        session_id = self._launch(
            title="Files",
            directory=str(self.nested),
            startup_mode="explorer",
            explorer_root_directory=str(self.repo_b),
            explorer_root_configured=False,
        )

        restored = api.session_manager.get_session(session_id)
        self.assertEqual(Path(restored.explorer_root_directory), self.repo_b)
        self.assertEqual(Path(restored.directory), self.nested)
        self.assertFalse(restored.explorer_root_configured)

        self._open_terminal(session_id)
        self._open_files(session_id, self.plain)

        session = api.session_manager.get_session(session_id)
        self.assertEqual(Path(session.explorer_root_directory), self.plain.resolve())

    def test_a_saved_root_wider_than_the_directory_survives_a_relaunch(self):
        """Matrix row 11: restore both facts, narrowing neither."""
        session_id = self._launch(
            title="Files",
            directory=str(self.nested),
            startup_mode="explorer",
            explorer_root_directory=str(self.repo_b),
            explorer_root_configured=True,
        )

        session = api.session_manager.get_session(session_id)
        self.assertEqual(Path(session.explorer_root_directory), self.repo_b)
        self.assertEqual(Path(session.directory), self.nested)
        self.assertTrue(session.explorer_root_configured)

    def test_a_save_racing_a_transition_cannot_see_half_a_pane(self):
        """A snapshot reads one lock hold, so the transition must write in one.

        The manager applies a metadata update under its own lock and every
        capture reads under the same lock, so the guarantee reduces to this:
        the mode and all three path fields move together. Split across two
        writes, a save landing between them stores one mode's paths under
        another mode's presentation -- durably, and with nothing to show for it.
        """
        session_id = self._launch(
            title="Shell", directory=str(self.repo_b), startup_mode="terminal"
        )
        real_update = api.session_manager.update_session_metadata
        writes = []

        def record(target_id, **updates):
            writes.append(updates)
            return real_update(target_id, **updates)

        with patch.object(
            api.session_manager, "update_session_metadata", side_effect=record
        ):
            self._open_files(session_id, self.nested)

        path_writes = [
            update for update in writes if "explorer_root_directory" in update
        ]
        self.assertEqual(len(path_writes), 1)
        self.assertEqual(path_writes[0]["startup_mode"], "explorer")
        self.assertEqual(Path(path_writes[0]["directory"]), self.nested.resolve())
        self.assertEqual(
            Path(path_writes[0]["explorer_root_directory"]), self.repo_b.resolve()
        )
        self.assertIs(path_writes[0]["explorer_root_configured"], False)
        self.assertIsNone(path_writes[0]["current_directory"])

    def test_a_pane_outside_its_default_folder_launches_where_it_was_saved(self):
        """Matrix row 10: an exact captured path is not rebased or refused."""
        outside = Path(self.temp_dir.name) / "elsewhere"
        outside.mkdir()

        session_id = self._launch(
            title="Shell", directory=str(outside), startup_mode="terminal"
        )

        self.assertEqual(
            Path(api.session_manager.get_session(session_id).directory), outside
        )

    def test_an_agent_pane_reopens_files_on_its_repository_without_a_probe(self):
        """Matrix rows 7-8: observed, never typed at."""
        session_id = self._launch(
            title="Codex",
            directory=str(self.collection),
            startup_mode="agent",
            agent_selection="codex",
            initial_command="codex",
        )
        api.session_manager.update_session_metadata(
            session_id, current_directory=str(self.nested)
        )

        with patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd"
        ) as probe, patch.object(api, "_close_ssh_connection"):
            response = self.client.post(
                f"/api/sessions/{session_id}/mode",
                json={"startup_mode": "explorer", "refresh_cwd": True},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        probe.assert_not_called()
        session = api.session_manager.get_session(session_id)
        self.assertEqual(Path(session.explorer_root_directory), self.repo_b.resolve())
        self.assertEqual(response.get_json()["explorer_open_path"], "src")


class _FakeSession:
    """The fields the startup sequence reads off a pane."""

    def __init__(self, directory, current_directory=None):
        self.session_id = "s1"
        self.directory = directory
        self.current_directory = current_directory
        self.startup_mode = "agent"
        self.mode = "wsl"


class MissingStartupDirectoryTestCase(unittest.TestCase):
    """A directory that is gone must not become a wrong-directory agent launch."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.present = Path(self.temp_dir.name) / "repo-b"
        self.present.mkdir()
        self.missing = Path(self.temp_dir.name) / "deleted"

    def _run(
        self,
        directory,
        current_directory=None,
        startup_command="claude",
        **connection_fields,
    ):
        connection = {
            "kind": "local",
            "shell_kind": "cmd",
            "pty_process": None,
            **connection_fields,
        }
        typed = []
        published = []
        session = _FakeSession(directory, current_directory)
        with patch.object(
            web_terminal_io,
            "_send_connection_input",
            lambda _connection, data: typed.append(data),
        ), patch.object(
            web_terminal_io,
            "_compose_agent_startup_command",
            lambda _session: startup_command,
        ), patch.object(
            web_terminal_io,
            "_publish_ssh_terminal_output",
            lambda _sid, output, _connection: published.append(output),
        ):
            web_terminal_io._run_startup_sequence(connection, session)
        return typed, published

    def test_a_deleted_directory_stops_the_agent_and_says_so(self):
        typed, published = self._run(
            str(self.present), current_directory=str(self.missing)
        )

        self.assertNotIn("claude\r\n", typed)
        self.assertNotIn("claude\n", typed)
        self.assertEqual(len(published), 1)
        self.assertIn(str(self.missing), published[0])
        self.assertIn("not available", published[0])
        # The shell still falls back, visibly, to the directory the pane
        # recorded -- the reader is not left in a pane that refused to open.
        self.assertTrue(any(str(self.present) in line for line in typed))

    def test_a_directory_that_is_there_starts_the_command_as_before(self):
        typed, published = self._run(str(self.present))

        self.assertIn("claude\n", typed)
        self.assertEqual(published, [])

    def test_a_pane_spawned_in_its_directory_is_never_second_guessed(self):
        """There was no `cd` to fail: the process is already standing there.

        The check exists to catch a `cd` that could not land, and a pane whose
        process opened in the directory has proved it exists. Reading the host
        again could only disagree with the process that is running.
        """
        typed, published = self._run(
            str(self.missing), startup_command="codex", launch_cwd_applied=True
        )

        self.assertEqual(typed, ["codex\n"])
        self.assertEqual(published, [])

    def test_a_pane_on_another_filesystem_is_never_answered_for(self):
        """Neither an SSH host's paths nor WSL's are this host's to judge.

        A WSL pane reports Linux paths -- `/home/me/project` is not missing
        because Windows cannot stat it -- and reading an SSH pane's directory
        would cost a round trip on the reader's own shell channel.
        """
        for connection, shell_kind, label in (
            ({"kind": "ssh"}, "posix", "ssh"),
            ({"kind": "local"}, "wsl", "wsl"),
        ):
            with self.subTest(pane=label):
                self.assertEqual(
                    web_terminal_io._unreachable_local_startup_directory(
                        connection, str(self.missing), shell_kind
                    ),
                    "",
                )


@unittest.skipUnless(NODE, "Node.js is required for the pane-path builder tests")
class PanePathBuilderTestCase(unittest.TestCase):
    """The real page builders, executed rather than read.

    `shared.js` and `terminals.js` are page scripts rather than modules, so
    each case slices the function under test out of the shipped file and runs
    it against the smallest stub it actually reaches. What is pinned is the
    payload a save and a launch really send.
    """

    def _run(self, source: str, body: str):
        script = source + "\n" + body + "\n"
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    @staticmethod
    def _slice(path: Path, start: str, end: str) -> str:
        source = path.read_text(encoding="utf-8")
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    def _shared(self, body: str):
        """The shipped path helpers: absolute/relative, launch dir, pane fields."""
        return self._run(
            self._slice(
                STATIC_JS / "shared.js",
                "    function isAbsoluteDirectory(",
                "    function _copyTextFallbackWrite(",
            ),
            body,
        )

    def test_a_relative_directory_still_resolves_under_the_step_2_folder(self):
        result = self._shared(
            """
            process.stdout.write(JSON.stringify({
                joined: buildLaunchDirectory('C:/collection', 'repo-b', 'wsl'),
                empty: buildLaunchDirectory('C:/collection', '', 'wsl'),
                posix: buildLaunchDirectory('/srv/app', 'services/api', 'ssh')
            }));
            """
        )

        self.assertEqual(result["joined"], "C:/collection/repo-b")
        self.assertEqual(result["empty"], "C:/collection")
        self.assertEqual(result["posix"], "/srv/app/services/api")

    def test_an_exact_captured_path_is_neither_rebased_nor_refused(self):
        """The pane moved out of the folder the preset was created in.

        Rebasing produced `C:/collection/C:/other`, and refusing made a
        correctly saved pane unlaunchable for the ordinary reason that its
        shell had walked somewhere else.
        """
        result = self._shared(
            """
            process.stdout.write(JSON.stringify({
                inside: buildLaunchDirectory('C:/collection', 'C:/collection/repo-b', 'wsl'),
                outside: buildLaunchDirectory('C:/collection', 'C:/other-repo', 'wsl'),
                remote: buildLaunchDirectory('/srv/app', '/var/log', 'ssh')
            }));
            """
        )

        self.assertEqual(result["inside"], "C:/collection/repo-b")
        self.assertEqual(result["outside"], "C:/other-repo")
        self.assertEqual(result["remote"], "/var/log")

    def test_a_launched_explorer_pane_carries_its_root_and_its_provenance(self):
        result = self._shared(
            """
            const derived = buildPaneLaunchFields({
                startup_mode: 'explorer',
                directory: 'C:/collection/repo-b/src',
                explorer_root_directory: 'C:/collection/repo-b',
                explorer_root_configured: false
            });
            const chosen = buildPaneLaunchFields({
                startup_mode: 'explorer',
                explorer_root_directory: 'C:/collection',
                explorer_root_configured: true
            });
            const shell = buildPaneLaunchFields({
                startup_mode: 'terminal',
                explorer_root_directory: 'C:/collection',
                explorer_root_configured: true
            });
            process.stdout.write(JSON.stringify({ derived, chosen, shell }));
            """
        )

        self.assertEqual(
            result["derived"]["explorer_root_directory"], "C:/collection/repo-b"
        )
        self.assertFalse(result["derived"]["explorer_root_configured"])
        self.assertEqual(result["chosen"]["explorer_root_directory"], "C:/collection")
        self.assertTrue(result["chosen"]["explorer_root_configured"])
        # A pane that is not an explorer has no boundary to reproduce.
        self.assertEqual(result["shell"]["explorer_root_directory"], "")
        self.assertFalse(result["shell"]["explorer_root_configured"])

    def _workspace_entry(self, session: dict, explorer: bool):
        """Run the real `buildWorkspaceTerminalEntry` over one live pane."""
        stubs = """
            const terminals = [];
            const SESSION = JSON.parse(process.argv[2]);
            const IS_EXPLORER = process.argv[3] === 'explorer';
            function isExplorerPaneInstance() { return IS_EXPLORER; }
            function isExplorerSession() { return IS_EXPLORER; }
            function isBrowserPaneInstance() { return false; }
            function isBrowserSession() { return false; }
            function explorerCaptureActiveTabView() {}
            function explorerSerializeTabs() {
                return { open_tabs: [], active_tab: '', tab_views: {} };
            }
            function explorerMarkdownAppearance() {
                return { preset: '', font: '', sourceFont: '' };
            }
            function explorerSidebarPresentation() {
                return { width: 260, scroll: {}, expanded: [], gitExpanded: [] };
            }
            function explorerPaneLiveTheme() { return 'dark'; }
            function browserSerializeTabs() { return { tabs: [], active_tab: 0 }; }
            function panePinDescriptor() {
                return { active: false, path: '', kind: 'dir' };
            }
        """
        entry = self._slice(
            STATIC_JS / "terminals.js",
            "    function buildWorkspaceTerminalEntry(",
            "    function buildActiveWorkspaceSessionConfig(",
        )
        script = stubs + entry + (
            "\nprocess.stdout.write(JSON.stringify("
            "buildWorkspaceTerminalEntry({ _session: SESSION }, 0, 'wsl')));\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    json.dumps(session),
                    "explorer" if explorer else "terminal",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_saved_explorer_pane_states_its_folder_and_its_root_separately(self):
        entry = self._workspace_entry(
            {
                "session_id": "s1",
                "title": "Files",
                "startup_mode": "explorer",
                "directory": "C:/collection/repo-b/src",
                "current_directory": None,
                "explorer_root_directory": "C:/collection/repo-b",
                "explorer_root_configured": False,
            },
            explorer=True,
        )

        self.assertEqual(entry["directory"], "C:/collection/repo-b/src")
        self.assertEqual(entry["explorer_root_directory"], "C:/collection/repo-b")
        self.assertFalse(entry["explorer_root_configured"])

    def _launcher_draft(self, row: dict):
        """Run the real `collectTerminalDrafts` over one launcher row."""
        stubs = """
            const ROW = JSON.parse(process.argv[2]);
            const MAX_SESSIONS = 1;
            const DEFAULT_TERMINALS = [{ title: 'Terminal 1', directory: '' }];
            const LOCAL_WINDOWS_SHELLS_AVAILABLE = false;
            const row = {
                dataset: ROW.dataset,
                querySelector(selector) {
                    if (selector === '.t-title') { return { value: ROW.title }; }
                    if (selector === '.t-dir') { return { value: ROW.directory }; }
                    return null;
                }
            };
            const document = { querySelectorAll: () => [row] };
            function getTerminalCommandMode() { return ROW.mode; }
            function buildTerminalInitialCommand() { return ''; }
            function getRowAgentSelection() { return ''; }
            function agentAutoModeFlag() { return false; }
        """
        launcher = STATIC_JS / "launcher.js"
        script = (
            stubs
            + self._slice(launcher, "    function parseStringArrayDataset(", "    /* Which explorer rows")
            + self._slice(launcher, "    function collectTerminalDrafts(", "    function renderCountOptions(")
            + "\nprocess.stdout.write(JSON.stringify(collectTerminalDrafts()[0]));\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), json.dumps(row)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_an_imported_explorer_row_carries_its_root_back_out_of_the_form(self):
        """Re-saving an imported preset from the launcher keeps a wider root.

        The form shows one directory per pane, so without this the root was
        re-derived from the folder the pane happened to be browsing and the
        preset narrowed a little on every round trip.
        """
        draft = self._launcher_draft(
            {
                "mode": "explorer",
                "title": "Files",
                "directory": "repo-b/src",
                "dataset": {
                    "explorerTabsDir": "repo-b/src",
                    "explorerRootDir": "C:/collection/repo-b",
                    "explorerRootConfigured": "false",
                },
            }
        )

        self.assertEqual(draft["directory"], "repo-b/src")
        self.assertEqual(draft["explorer_root_directory"], "C:/collection/repo-b")
        self.assertFalse(draft["explorer_root_configured"])

    def test_editing_the_row_directory_drops_the_root_it_no_longer_describes(self):
        """The same signal that drops the saved tabs and the Git pin.

        Once the reader retargets the row, the captured root describes a
        different pane; the launch derives one from the directory they typed.
        """
        draft = self._launcher_draft(
            {
                "mode": "explorer",
                "title": "Files",
                "directory": "repo-c",
                "dataset": {
                    "explorerTabsDir": "repo-b/src",
                    "explorerRootDir": "C:/collection/repo-b",
                    "explorerRootConfigured": "true",
                },
            }
        )

        self.assertEqual(draft["directory"], "repo-c")
        self.assertEqual(draft["explorer_root_directory"], "")
        self.assertFalse(draft["explorer_root_configured"])

    def test_a_saved_agent_pane_states_where_it_is_and_carries_no_root(self):
        entry = self._workspace_entry(
            {
                "session_id": "s2",
                "title": "Codex",
                "startup_mode": "agent",
                "agent_selection": "codex",
                "initial_command": "codex",
                "directory": "C:/collection",
                "current_directory": "C:/collection/repo-b",
                "explorer_root_directory": "C:/collection",
                "explorer_root_configured": True,
            },
            explorer=False,
        )

        self.assertEqual(entry["directory"], "C:/collection/repo-b")
        self.assertEqual(entry["agent_selection"], "codex")
        self.assertEqual(entry["explorer_root_directory"], "")
        self.assertFalse(entry["explorer_root_configured"])


if __name__ == "__main__":
    unittest.main()
