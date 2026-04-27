import unittest

from session_manager import SessionManager, SessionStatus


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

    def test_update_session_status_marks_connected_and_notifies_callbacks(self):
        session = self.manager.create_session(
            group_id="group-b",
            host="example.com",
            directory="/home/user",
        )
        observed_statuses = []
        self.manager.register_callback(session.session_id, observed_statuses.append)

        updated = self.manager.update_session_status(
            session.session_id,
            SessionStatus.CONNECTED,
        )

        self.assertTrue(updated)
        updated_session = self.manager.get_session(session.session_id)
        self.assertEqual(updated_session.status, SessionStatus.CONNECTED)
        self.assertIsNotNone(updated_session.connected_at)
        self.assertEqual(observed_statuses, [SessionStatus.CONNECTED])

    def test_clear_disconnected_sessions_removes_sessions_callbacks_and_empty_groups(self):
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
        self.manager.register_callback(session.session_id, lambda status: None)
        self.manager.close_session(session.session_id)

        self.manager.clear_disconnected_sessions()

        self.assertIsNone(self.manager.get_session(session.session_id))
        self.assertNotIn(session.session_id, self.manager._session_callbacks)
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

