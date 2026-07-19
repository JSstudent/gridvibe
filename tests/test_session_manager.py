import unittest
from types import SimpleNamespace
from unittest.mock import patch

import session_manager as manager_module
from session_manager import EMPTY_GROUP_GRACE_SECONDS, SessionManager, SessionStatus


class SessionManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager()

    def test_create_session_uses_expected_defaults(self):
        session = self.manager.create_session(
            group_id="group-a",
            host="127.0.0.1",
            directory="/tmp/project",
        )

        self.assertEqual(session.group_id, "group-a")
        self.assertEqual(session.host, "127.0.0.1")
        self.assertEqual(session.directory, "/tmp/project")
        self.assertEqual(session.username, "root")
        self.assertEqual(session.port, 22)
        self.assertEqual(session.status, SessionStatus.PENDING)
        self.assertEqual(self.manager.get_session_count(), 1)

    def test_create_and_append_build_equivalent_sessions(self):
        self.manager.create_group(
            name="Group A",
            connection_mode="ssh",
            layout="2x2",
            terminal_count=1,
            group_id="group-a",
        )
        common_fields = {
            "host": "example.com",
            "directory": "/srv/app",
            "username": "alice",
            "port": 2222,
            "explorer_git_open": True,
        }

        created = self.manager.create_session(group_id="group-a", **common_fields)
        appended = self.manager.append_session_to_group(group_id="group-a", **common_fields)

        self.assertIsNotNone(appended)
        created_dict = created.to_dict()
        appended_dict = appended.to_dict()
        for volatile_key in ("session_id", "created_at"):
            created_dict.pop(volatile_key)
            appended_dict.pop(volatile_key)
        self.assertEqual(created_dict, appended_dict)
        self.assertEqual(self.manager.get_group("group-a").terminal_count, 2)

    def test_session_builders_reject_unknown_fields(self):
        with self.assertRaises(TypeError):
            self.manager.create_session(
                group_id="group-a",
                host="example.com",
                directory="/srv/app",
                bogus_field=True,
            )

    def test_create_sessions_accepts_ip_and_hostname_fallbacks(self):
        sessions = self.manager.create_sessions(
            [
                {
                    "ip": "192.168.10.10",
                    "directory": "/srv/api",
                    "username": "alice",
                },
                {
                    "hostname": "worker.internal",
                    "directory": "/srv/worker",
                    "username": "bob",
                },
            ],
            group_id="group-a",
        )

        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(session.group_id == "group-a" for session in sessions))
        self.assertEqual(sessions[0].host, "192.168.10.10")
        self.assertEqual(sessions[1].host, "worker.internal")

    def test_create_sessions_supports_wsl_metadata(self):
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "wsl",
                    "distribution": "Ubuntu-24.04",
                    "directory": "~/repo",
                    "username": "devuser",
                    "title": "Local shell",
                    "use_wsl": True,
                }
            ],
            group_id="group-local",
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].group_id, "group-local")
        self.assertEqual(sessions[0].mode, "wsl")
        self.assertEqual(sessions[0].distribution, "Ubuntu-24.04")
        self.assertEqual(sessions[0].host, "Ubuntu-24.04")
        self.assertTrue(sessions[0].use_wsl)

    def test_create_sessions_supports_powershell_metadata(self):
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "wsl",
                    "directory": "C:\\repo",
                    "title": "Windows shell",
                    "use_powershell": True,
                }
            ],
            group_id="group-local",
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].group_id, "group-local")
        self.assertEqual(sessions[0].mode, "wsl")
        self.assertEqual(sessions[0].host, "WSL")
        self.assertTrue(sessions[0].use_powershell)

    def test_create_sessions_supports_startup_mode_metadata(self):
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "wsl",
                    "directory": "C:\\repo",
                    "title": "Files",
                    "startup_mode": "explorer",
                    "explorer_root_directory": "C:\\repo",
                    "explorer_tree_open": True,
                    "explorer_git_open": True,
                }
            ],
            group_id="group-local",
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].startup_mode, "explorer")
        self.assertEqual(sessions[0].explorer_root_directory, "C:\\repo")
        self.assertTrue(sessions[0].explorer_tree_open)
        self.assertTrue(sessions[0].explorer_git_open)
        self.assertEqual(sessions[0].to_dict()["startup_mode"], "explorer")
        self.assertEqual(sessions[0].to_dict()["explorer_root_directory"], "C:\\repo")
        self.assertTrue(sessions[0].to_dict()["explorer_tree_open"])
        self.assertTrue(sessions[0].to_dict()["explorer_git_open"])

    def test_create_sessions_carries_explorer_open_tabs(self):
        """ISSUE-2026-015: TerminalSession persists open explorer tabs metadata."""
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "wsl",
                    "directory": "C:\\repo",
                    "title": "Files",
                    "startup_mode": "explorer",
                    "explorer_root_directory": "C:\\repo",
                    "explorer_open_tabs": ["docs/a.md", "b.md"],
                    "explorer_active_tab": "b.md",
                }
            ],
            group_id="group-tabs",
        )

        self.assertEqual(sessions[0].explorer_open_tabs, ["docs/a.md", "b.md"])
        self.assertEqual(sessions[0].explorer_active_tab, "b.md")
        self.assertEqual(sessions[0].to_dict()["explorer_open_tabs"], ["docs/a.md", "b.md"])
        self.assertEqual(sessions[0].to_dict()["explorer_active_tab"], "b.md")

    def test_create_sessions_defaults_explorer_tabs_when_absent(self):
        """Backward compatibility: sessions without tab metadata get safe defaults."""
        sessions = self.manager.create_sessions(
            [{"mode": "ssh", "host": "h", "directory": "/repo", "startup_mode": "explorer"}],
            group_id="group-notabs",
        )

        self.assertEqual(sessions[0].explorer_open_tabs, [])
        self.assertEqual(sessions[0].explorer_active_tab, "")
        self.assertEqual(sessions[0].explorer_tab_views, {})
        self.assertEqual(sessions[0].explorer_md_preset, "")
        self.assertEqual(sessions[0].explorer_md_font, "")

    def test_create_sessions_carries_explorer_tab_views_and_md_appearance(self):
        """2.f: per-tab view snapshots and Markdown appearance ride the session."""
        tab_views = {"docs/a.md": {"mode": "preview", "scroll": 0.4, "identity": "abc123"}}
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "wsl",
                    "directory": "C:\\repo",
                    "title": "Files",
                    "startup_mode": "explorer",
                    "explorer_open_tabs": ["docs/a.md"],
                    "explorer_active_tab": "docs/a.md",
                    "explorer_tab_views": tab_views,
                    "explorer_md_preset": "vscode",
                    "explorer_md_font": "serif",
                }
            ],
            group_id="group-tab-views",
        )

        self.assertEqual(sessions[0].explorer_tab_views, tab_views)
        self.assertEqual(sessions[0].explorer_md_preset, "vscode")
        self.assertEqual(sessions[0].explorer_md_font, "serif")
        data = sessions[0].to_dict()
        self.assertEqual(data["explorer_tab_views"], tab_views)
        self.assertEqual(data["explorer_md_preset"], "vscode")
        self.assertEqual(data["explorer_md_font"], "serif")

    def test_create_sessions_supports_agent_metadata(self):
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "ssh",
                    "host": "127.0.0.1",
                    "directory": "/repo",
                    "initial_command": "claude-code",
                    "initial_command_mode": "agent",
                    "agent_selection": "other",
                    "custom_agent": "claude-code",
                }
            ],
            group_id="group-agent",
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].initial_command_mode, "agent")
        self.assertEqual(sessions[0].agent_selection, "other")
        self.assertEqual(sessions[0].custom_agent, "claude-code")
        self.assertEqual(sessions[0].to_dict()["initial_command_mode"], "agent")

    def test_create_sessions_carries_agent_auto_mode(self):
        """ISSUE-2026-013: TerminalSession persists the per-terminal auto-mode toggle."""
        sessions = self.manager.create_sessions(
            [
                {
                    "mode": "ssh",
                    "host": "127.0.0.1",
                    "directory": "/repo",
                    "initial_command": "claude",
                    "initial_command_mode": "agent",
                    "agent_selection": "claude",
                    "agent_auto_mode": True,
                },
                {
                    "mode": "ssh",
                    "host": "127.0.0.1",
                    "directory": "/repo",
                },
            ],
            group_id="group-auto-mode",
        )

        self.assertTrue(sessions[0].agent_auto_mode)
        self.assertTrue(sessions[0].to_dict()["agent_auto_mode"])
        # Backward compatibility: configs without the field default to off.
        self.assertFalse(sessions[1].agent_auto_mode)
        self.assertFalse(sessions[1].to_dict()["agent_auto_mode"])

    def test_create_group_preserves_workspace_layout_metadata(self):
        workspace_layout = {
            "class_name": "layout-split-local",
            "split_slot_rects": [
                {"originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 2},
            ],
        }

        group = self.manager.create_group(
            name="saved",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            workspace_layout=workspace_layout,
        )

        self.assertEqual(group.workspace_layout, workspace_layout)
        self.assertEqual(group.to_dict()["workspace_layout"], workspace_layout)

    def test_create_group_preserves_surface_mode_metadata(self):
        group = self.manager.create_group(
            name="maxed",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            surface_mode="max",
        )

        self.assertEqual(group.surface_mode, "max")
        self.assertEqual(group.to_dict()["surface_mode"], "max")

    def test_update_session_status_marks_connected(self):
        session = self.manager.create_session(
            group_id="group-b",
            host="example.com",
            directory="/home/user",
        )

        updated = self.manager.update_session_status(
            session.session_id,
            SessionStatus.CONNECTED,
        )

        self.assertTrue(updated)
        updated_session = self.manager.get_session(session.session_id)
        self.assertEqual(updated_session.status, SessionStatus.CONNECTED)
        self.assertIsNotNone(updated_session.connected_at)

    def test_clear_disconnected_sessions_removes_sessions_and_empty_groups(self):
        session = self.manager.create_session(
            group_id="group-c",
            host="cleanup.example",
            directory="/opt/service",
        )
        self.manager.create_group(
            name="Cleanup",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-c",
        )
        self.manager.close_session(session.session_id)

        # Age the group past the empty-group grace period so cleanup sweeps it.
        self.manager.get_group("group-c").created_at -= EMPTY_GROUP_GRACE_SECONDS + 1
        self.manager.clear_disconnected_sessions()

        self.assertIsNone(self.manager.get_session(session.session_id))
        self.assertIsNone(self.manager.get_group("group-c"))

    def test_get_all_groups_returns_display_order(self):
        self.manager.create_group(
            name="First",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-a",
        )
        self.manager.create_group(
            name="Second",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-b",
        )
        self.manager.create_group(
            name="Third",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-c",
        )

        reordered = self.manager.reorder_groups(["group-c", "group-a", "group-b"])

        self.assertEqual([group.group_id for group in reordered], ["group-c", "group-a", "group-b"])
        self.assertEqual(
            [group.group_id for group in self.manager.get_all_groups()],
            ["group-c", "group-a", "group-b"],
        )
        self.assertEqual(self.manager.get_group("group-c").display_order, 0)
        self.assertEqual(self.manager.get_group("group-a").display_order, 1)
        self.assertEqual(self.manager.get_group("group-b").display_order, 2)

    def test_reorder_groups_appends_missing_groups_in_existing_order(self):
        self.manager.create_group(
            name="First",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-a",
        )
        self.manager.create_group(
            name="Second",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-b",
        )
        self.manager.create_group(
            name="Third",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-c",
        )

        reordered = self.manager.reorder_groups(["group-b"])

        self.assertEqual([group.group_id for group in reordered], ["group-b", "group-a", "group-c"])

    def test_create_group_reuses_existing_group_id_without_duplication(self):
        original = self.manager.create_group(
            name="Original",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-a",
        )
        self.manager.create_group(
            name="Updated",
            connection_mode="wsl",
            layout="grid",
            terminal_count=4,
            group_id="group-a",
        )

        groups = self.manager.get_all_groups()

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].group_id, "group-a")
        self.assertEqual(groups[0].name, "Updated")
        self.assertEqual(groups[0].connection_mode, "wsl")
        self.assertEqual(groups[0].layout, "grid")
        self.assertEqual(groups[0].terminal_count, 4)
        self.assertEqual(groups[0].display_order, original.display_order)

    def test_append_session_to_group_updates_existing_group_count(self):
        self.manager.create_group(
            name="Split",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-a",
        )

        session = self.manager.append_session_to_group(
            group_id="group-a",
            host="127.0.0.1",
            directory="/tmp/project",
            username="alice",
            title="Terminal 2",
        )

        self.assertIsNotNone(session)
        self.assertEqual(session.group_id, "group-a")
        self.assertEqual(session.username, "alice")
        self.assertEqual(self.manager.get_group("group-a").terminal_count, 2)
        self.assertEqual(len(self.manager.get_group_sessions("group-a")), 1)

    def test_append_session_to_group_rejects_missing_group(self):
        session = self.manager.append_session_to_group(
            group_id="missing",
            host="127.0.0.1",
            directory="/tmp/project",
        )

        self.assertIsNone(session)
        self.assertEqual(self.manager.get_session_count(), 0)

    def test_clear_disconnected_sessions_updates_remaining_group_count(self):
        self.manager.create_group(
            name="Split",
            connection_mode="ssh",
            layout="single",
            terminal_count=2,
            group_id="group-a",
        )
        first = self.manager.create_session(
            group_id="group-a",
            host="127.0.0.1",
            directory="/tmp/one",
        )
        self.manager.create_session(
            group_id="group-a",
            host="127.0.0.2",
            directory="/tmp/two",
        )

        self.manager.close_session(first.session_id)
        self.manager.clear_disconnected_sessions()

        self.assertEqual(self.manager.get_group("group-a").terminal_count, 1)


class EmptyGroupGracePeriodTestCase(unittest.TestCase):
    """Finding 2.2 — cleanup must not delete a freshly created (mid-launch) group."""

    def setUp(self):
        self.manager = SessionManager()

    def _create_group(self, group_id):
        return self.manager.create_group(
            name="Launch",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id=group_id,
        )

    def test_young_empty_group_survives_cleanup(self):
        self._create_group("group-young")

        self.manager.clear_disconnected_sessions()

        self.assertIsNotNone(self.manager.get_group("group-young"))

    def test_old_empty_group_is_removed(self):
        group = self._create_group("group-old")
        group.created_at -= EMPTY_GROUP_GRACE_SECONDS + 1

        self.manager.clear_disconnected_sessions()

        self.assertIsNone(self.manager.get_group("group-old"))

    def test_forced_group_is_removed_despite_grace_period(self):
        self._create_group("group-forced")

        self.manager.clear_disconnected_sessions(force_group_ids={"group-forced"})

        self.assertIsNone(self.manager.get_group("group-forced"))


class SessionIdCollisionTestCase(unittest.TestCase):
    """Finding 2.8 — short session ids are regenerated when they collide."""

    def setUp(self):
        self.manager = SessionManager()

    @staticmethod
    def _fake_uuid(prefix):
        return SimpleNamespace(hex=prefix * 4)

    def test_create_session_regenerates_colliding_id(self):
        fake_ids = [
            self._fake_uuid("deadbeef"),
            self._fake_uuid("deadbeef"),
            self._fake_uuid("cafef00d"),
        ]
        with patch.object(manager_module.uuid, "uuid4", side_effect=fake_ids):
            first = self.manager.create_session(
                group_id="group-a", host="127.0.0.1", directory="/tmp/one",
            )
            second = self.manager.create_session(
                group_id="group-a", host="127.0.0.1", directory="/tmp/two",
            )

        self.assertEqual(first.session_id, "deadbeef")
        self.assertEqual(second.session_id, "cafef00d")
        self.assertEqual(self.manager.get_session_count(), 2)

    def test_append_session_regenerates_colliding_id(self):
        self.manager.create_group(
            name="Group",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-a",
        )
        fake_ids = [
            self._fake_uuid("deadbeef"),
            self._fake_uuid("deadbeef"),
            self._fake_uuid("cafef00d"),
        ]
        with patch.object(manager_module.uuid, "uuid4", side_effect=fake_ids):
            first = self.manager.create_session(
                group_id="group-a", host="127.0.0.1", directory="/tmp/one",
            )
            appended = self.manager.append_session_to_group(
                group_id="group-a", host="127.0.0.1", directory="/tmp/two",
            )

        self.assertEqual(first.session_id, "deadbeef")
        self.assertEqual(appended.session_id, "cafef00d")
        self.assertEqual(self.manager.get_session_count(), 2)
