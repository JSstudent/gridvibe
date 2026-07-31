import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sessions.manager import EMPTY_GROUP_GRACE_SECONDS
from web import api
from web import runtime_state as web_runtime_state
from web.workspaces import normalize_workspace_id, workspace_room


class MultiWorkspaceApiTestCase(unittest.TestCase):
    """Stage 2 HTTP, page, and Socket.IO workspace isolation."""

    WORKSPACE_A = "aaaaaaaaaaaa"
    WORKSPACE_B = "bbbbbbbbbbbb"

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        api.session_manager.create_workspace("Alpha", self.WORKSPACE_A)
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self.addCleanup(api.session_manager.reset_sessions)

    def _group(self, group_id, workspace_id):
        group = api.session_manager.create_group(
            name=group_id,
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id=group_id,
            workspace_id=workspace_id,
        )
        session = api.session_manager.create_session(
            group_id=group_id,
            host=f"{group_id}.example",
            directory="/srv/app",
        )
        return group, session

    @staticmethod
    def _workspace_events(socket_client):
        return [
            event["args"][0]
            for event in socket_client.get_received()
            if event["name"] == "session_groups_updated"
        ]

    def _socket_client(self, workspace_id):
        socket_client = api.socketio.test_client(
            api.app,
            flask_test_client=self.client,
        )
        self.addCleanup(socket_client.disconnect)
        socket_client.get_received()
        socket_client.emit("join_workspace", {"workspace_id": workspace_id})
        socket_client.get_received()
        return socket_client

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_workspace_id_normalization_and_room_derivation(self):
        self.assertEqual(normalize_workspace_id(), "default")
        self.assertEqual(normalize_workspace_id(self.WORKSPACE_A), self.WORKSPACE_A)
        self.assertEqual(workspace_room(self.WORKSPACE_A), f"workspace:{self.WORKSPACE_A}")
        for invalid in ("short", "UPPERCASEABC", "../workspace", "a" * 13):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalize_workspace_id(invalid)

    def test_group_and_session_reads_are_partitioned(self):
        group_a, session_a = self._group("group-a", self.WORKSPACE_A)
        self._group("group-b", self.WORKSPACE_B)

        groups_response = self.client.get(
            "/api/session-groups",
            query_string={"workspace_id": self.WORKSPACE_A},
        )
        sessions_response = self.client.get(
            "/api/sessions",
            query_string={
                "workspace_id": self.WORKSPACE_A,
                "group": group_a.group_id,
            },
        )

        self.assertEqual(groups_response.status_code, 200)
        self.assertEqual(
            [group["group_id"] for group in groups_response.get_json()["groups"]],
            [group_a.group_id],
        )
        self.assertEqual(sessions_response.status_code, 200)
        self.assertEqual(
            [session["session_id"] for session in sessions_response.get_json()["sessions"]],
            [session_a.session_id],
        )
        self.assertEqual(
            sessions_response.get_json()["group"]["workspace_id"],
            self.WORKSPACE_A,
        )

    def test_scoped_session_read_serializes_inside_the_ownership_lock(self):
        group, session = self._group("group-a", self.WORKSPACE_A)
        original_to_dict = session.to_dict
        lock_states = []

        def checked_to_dict():
            lock_states.append(api.session_manager.lock._is_owned())
            return original_to_dict()

        with patch.object(session, "to_dict", side_effect=checked_to_dict):
            response = self.client.get(
                "/api/sessions",
                query_string={
                    "workspace_id": self.WORKSPACE_A,
                    "group": group.group_id,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lock_states, [True])

    def test_named_empty_workspace_never_uses_global_launch_fallback(self):
        with patch.dict(
            api.active_launch_options,
            {
                "layout": "grid",
                "connection_mode": "ssh",
                "terminal_count": 16,
            },
            clear=True,
        ):
            response = self.client.get(
                "/api/sessions",
                query_string={"workspace_id": self.WORKSPACE_A},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sessions"], [])
        self.assertIsNone(payload["layout"])
        self.assertIsNone(payload["connection_mode"])
        self.assertEqual(payload["terminal_count"], 0)

    def test_foreign_group_reads_order_and_active_updates_are_rejected(self):
        self._group("group-a", self.WORKSPACE_A)
        self._group("group-b", self.WORKSPACE_B)

        responses = [
            self.client.get(
                "/api/sessions",
                query_string={
                    "workspace_id": self.WORKSPACE_A,
                    "group": "group-b",
                },
            ),
            self.client.get("/api/sessions", query_string={"group": "group-b"}),
            self.client.post(
                "/api/session-groups/order",
                json={
                    "workspace_id": self.WORKSPACE_A,
                    "group_ids": ["group-b"],
                },
            ),
            self.client.post(
                "/api/session-groups/active",
                json={
                    "workspace_id": self.WORKSPACE_A,
                    "group_id": "group-b",
                },
            ),
            self.client.delete(
                "/api/sessions",
                query_string={
                    "workspace_id": self.WORKSPACE_A,
                    "group": "group-b",
                },
            ),
        ]

        self.assertEqual([response.status_code for response in responses], [400] * 5)
        self.assertIsNotNone(api.session_manager.get_group("group-b"))

    def test_active_group_hints_are_independent_through_the_route(self):
        self._group("group-a", self.WORKSPACE_A)
        self._group("group-b", self.WORKSPACE_B)

        first = self.client.post(
            "/api/session-groups/active",
            json={
                "workspace_id": self.WORKSPACE_A,
                "group_id": "group-a",
            },
        )
        second = self.client.post(
            "/api/session-groups/active",
            json={
                "workspace_id": self.WORKSPACE_B,
                "group_id": "group-b",
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            api.session_manager.get_active_group_id(self.WORKSPACE_A),
            "group-a",
        )
        self.assertEqual(
            api.session_manager.get_active_group_id(self.WORKSPACE_B),
            "group-b",
        )

    def test_group_updates_reach_only_the_affected_workspace_room(self):
        self._group("group-a", self.WORKSPACE_A)
        client_a = self._socket_client(self.WORKSPACE_A)
        client_b = self._socket_client(self.WORKSPACE_B)

        api._broadcast_session_groups_updated(
            "reordered",
            group_id="group-a",
            workspace_id=self.WORKSPACE_A,
        )

        self.assertEqual(
            self._workspace_events(client_a),
            [
                {
                    "workspace_id": self.WORKSPACE_A,
                    "reason": "reordered",
                    "group_id": "group-a",
                }
            ],
        )
        self.assertEqual(self._workspace_events(client_b), [])

    def test_closing_last_group_in_one_workspace_preserves_the_other(self):
        self._group("group-a", self.WORKSPACE_A)
        _group_b, session_b = self._group("group-b", self.WORKSPACE_B)
        client_a = self._socket_client(self.WORKSPACE_A)
        client_b = self._socket_client(self.WORKSPACE_B)

        response = self.client.delete(
            "/api/sessions",
            query_string={
                "workspace_id": self.WORKSPACE_A,
                "group": "group-a",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(api.session_manager.get_workspace(self.WORKSPACE_A))
        self.assertIsNotNone(api.session_manager.get_group("group-b"))
        self.assertIsNotNone(api.session_manager.get_session(session_b.session_id))
        self.assertEqual(
            [event["reason"] for event in self._workspace_events(client_a)],
            ["group_closed"],
        )
        self.assertEqual(self._workspace_events(client_b), [])

    def test_cleanup_prune_event_is_emitted_after_a_single_session_close(self):
        _group, session = self._group("group-a", self.WORKSPACE_A)
        api.session_manager.get_workspace(self.WORKSPACE_B).created_at = (
            time.time() - EMPTY_GROUP_GRACE_SECONDS - 1
        )
        client_b = self._socket_client(self.WORKSPACE_B)
        original_broadcast = api._broadcast_session_groups_updated
        broadcast_lock_states = []

        def checked_broadcast(*args, **kwargs):
            broadcast_lock_states.append(api.session_manager.lock._is_owned())
            return original_broadcast(*args, **kwargs)

        with patch.object(
            api,
            "_broadcast_session_groups_updated",
            side_effect=checked_broadcast,
        ):
            response = self.client.delete(f"/api/sessions/{session.session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(broadcast_lock_states, [False, False])
        self.assertIsNone(api.session_manager.get_workspace(self.WORKSPACE_B))
        self.assertEqual(
            self._workspace_events(client_b),
            [
                {
                    "workspace_id": self.WORKSPACE_B,
                    "reason": "workspace_pruned",
                }
            ],
        )

    def test_stale_foreign_group_url_serves_the_workspace_shell_safely(self):
        self._group("group-a", self.WORKSPACE_A)
        self._group("group-b", self.WORKSPACE_B)

        response = self.client.get(
            "/terminals",
            query_string={
                "workspace": self.WORKSPACE_A,
                "group": "group-b",
            },
        )
        terminals_js = self._static("js/terminals.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "if (activeGroupId && !getGroupById(activeGroupId))",
            terminals_js,
        )
        self.assertIn("workspace_id: currentWorkspaceId", terminals_js)
        self.assertIn("socket.emit('join_workspace'", terminals_js)

    def test_browser_window_names_and_urls_are_workspace_scoped(self):
        launcher_js = self._static("js/launcher.js")
        page = self.client.get("/").get_data(as_text=True)

        self.assertIn("`gridvibe-workspace-${resolvedWorkspaceId}`", launcher_js)
        self.assertIn("workspace: resolvedWorkspaceId", launcher_js)
        self.assertIn('target="gridvibe-workspace-default"', page)
        self.assertNotIn("gridvibe-sessions", launcher_js)


class MultiWorkspacePersistenceTestCase(unittest.TestCase):
    """Stage 1 partitioned snapshots and one-write autosave."""

    WORKSPACE_A = "aaaaaaaaaaaa"
    WORKSPACE_B = "bbbbbbbbbbbb"

    def setUp(self):
        api.session_manager.reset_sessions()
        api.session_manager.create_workspace("Alpha", self.WORKSPACE_A)
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state,
            "RUNTIME_STATE_PATH",
            str(self.state_path),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _group(self, group_id, workspace_id, password):
        api.session_manager.create_group(
            name=group_id,
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id=group_id,
            workspace_id=workspace_id,
        )
        api.session_manager.create_session(
            group_id=group_id,
            host=f"{group_id}.example",
            directory="/srv/app",
            password=password,
        )

    def test_one_autosave_tick_writes_all_live_workspaces_once_after_unlock(self):
        self._group("group-a", self.WORKSPACE_A, "secret-a")
        self._group("group-b", self.WORKSPACE_B, "secret-b")
        original_write = web_runtime_state._write_state_locked
        write_lock_states = []

        def checked_write(state):
            write_lock_states.append(api.session_manager.lock._is_owned())
            original_write(state)

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=checked_write,
        ) as write_state:
            api._run_workspace_autosave_tick()

        self.assertEqual(write_state.call_count, 1)
        self.assertEqual(write_lock_states, [False])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(set(state["workspaces"]), {self.WORKSPACE_A, self.WORKSPACE_B})
        self.assertEqual(
            [group["group_id"] for group in state["workspaces"][self.WORKSPACE_A]["groups"]],
            ["group-a"],
        )
        self.assertEqual(
            [group["group_id"] for group in state["workspaces"][self.WORKSPACE_B]["groups"]],
            ["group-b"],
        )
        self.assertNotIn("secret-a", self.state_path.read_text(encoding="utf-8"))
        self.assertNotIn("secret-b", self.state_path.read_text(encoding="utf-8"))

    def test_restorable_summaries_expose_counts_without_launch_config(self):
        self._group("group-a", self.WORKSPACE_A, "secret-a")
        web_runtime_state.capture_live_workspaces(api.session_manager)

        summaries = web_runtime_state.list_restorable_workspaces()

        self.assertEqual(
            summaries,
            [
                {
                    "workspace_id": self.WORKSPACE_A,
                    "label": "Alpha",
                    "origin": "auto",
                    "saved_at": summaries[0]["saved_at"],
                    "group_count": 1,
                    "pane_count": 1,
                }
            ],
        )
        self.assertNotIn("groups", summaries[0])
        self.assertNotIn("sessions", summaries[0])

    def test_autosave_refresh_does_not_demote_a_manual_slot(self):
        self._group("group-a", self.WORKSPACE_A, "secret-a")
        web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=self.WORKSPACE_A,
            origin="manual",
        )

        web_runtime_state.capture_live_workspaces(api.session_manager)

        slot = web_runtime_state.load_restorable_workspace(self.WORKSPACE_A)
        self.assertEqual(slot["origin"], "manual")


if __name__ == "__main__":
    unittest.main()
