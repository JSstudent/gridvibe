import json
import os
import shutil
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from web import api
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import terminal_io as web_terminal_io
from web import workspaces as web_workspaces
from web.workspaces import normalize_workspace_id, workspace_room


def _js_function_source(script, name):
    """Return one top-level JS function's source, brace-matched, from `script`.

    Lets a test exercise a shipped frontend helper for real instead of
    asserting on its source text: the helpers worth testing this way are pure,
    but the file around them touches `document` at load time.
    """
    start = script.index(f"function {name}(")
    # Keep an `async` qualifier: without it the extracted body's `await`s are a
    # syntax error the moment the harness runs them.
    if script[max(0, start - 6):start] == "async ":
        start -= 6
    depth = 0
    for index in range(script.index("{", start), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _workspace_events(socket_client):
    """Return the session_groups_updated payloads one socket received."""
    return [
        event["args"][0]
        for event in socket_client.get_received()
        if event["name"] == "session_groups_updated"
    ]


class WorkspaceSocketClientMixin:
    """Join one Socket.IO client to a workspace room and clean it up."""

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


class MultiWorkspaceApiTestCase(WorkspaceSocketClientMixin, unittest.TestCase):
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
            _workspace_events(client_a),
            [
                {
                    "workspace_id": self.WORKSPACE_A,
                    "reason": "reordered",
                    "group_id": "group-a",
                }
            ],
        )
        self.assertEqual(_workspace_events(client_b), [])

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
            [event["reason"] for event in _workspace_events(client_a)],
            ["group_closed"],
        )
        self.assertEqual(_workspace_events(client_b), [])

    def test_cleanup_prune_event_is_emitted_after_a_single_session_close(self):
        _group, session = self._group("group-a", self.WORKSPACE_A)
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
            _workspace_events(client_b),
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
        workspaces_js = self._static("js/workspaces.js")
        terminals_js = self._static("js/terminals.js")
        page = self.client.get("/").get_data(as_text=True)

        # One implementation of the window name and URL, in the shared module.
        self.assertIn(
            "return `gridvibe-workspace-${normalizeWorkspaceId(workspaceId)}`;",
            workspaces_js,
        )
        self.assertIn(
            "const params = new URLSearchParams({ workspace: normalizeWorkspaceId(workspaceId) });",
            workspaces_js,
        )
        self.assertIn("await openWorkspaceWindow(resolvedWorkspaceId, {", launcher_js)
        self.assertIn("groupId: workspace.active_group_id", launcher_js)
        self.assertIn(
            "switchToWorkspaceWindow(workspace.workspace_id, {",
            terminals_js,
        )
        self.assertIn("groupId: target.active_group_id", terminals_js)
        # The launcher never hardcodes a window *name*: it is derived from the
        # workspace id in workspaces.js. "View Active Terminals" stays the
        # single-workspace entry point and is hidden with the flag on, where the
        # Workspaces card lists every live workspace with its own Open button.
        self.assertIn('id="viewActiveTerminalsBtn"', page)
        self.assertIn("viewButton.hidden = enabled;", launcher_js)
        self.assertNotIn("gridvibe-workspace-default", page)
        self.assertNotIn("gridvibe-sessions", launcher_js)
        self.assertNotIn("gridvibe-sessions", workspaces_js)


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

        def checked_write(state, state_path):
            write_lock_states.append(api.session_manager.lock._is_owned())
            original_write(state, state_path)

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
                    "manually_saved_at": None,
                    "saved_at": summaries[0]["saved_at"],
                    "group_count": 1,
                    "pane_count": 1,
                }
            ],
        )
        self.assertNotIn("groups", summaries[0])
        self.assertNotIn("sessions", summaries[0])

    def test_autosave_refresh_keeps_the_pin_while_origin_names_the_writer(self):
        """MW-04: pinning is durable; ``origin`` describes the last writer."""
        self._group("group-a", self.WORKSPACE_A, "secret-a")
        manual = web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=self.WORKSPACE_A,
            origin="manual",
        )
        self.assertEqual(manual["origin"], "manual")
        self.assertIsNotNone(manual["manually_saved_at"])

        web_runtime_state.capture_live_workspaces(api.session_manager)

        slot = web_runtime_state.load_restorable_workspace(self.WORKSPACE_A)
        # The timer wrote this shape, and the slot says so...
        self.assertEqual(slot["origin"], "auto")
        # ...but the workspace is still one the user saved by hand, so it keeps
        # its permanent-offer pin and is exempt from the auto-slot cap.
        self.assertEqual(slot["manually_saved_at"], manual["manually_saved_at"])
        self.assertTrue(web_runtime_state._slot_is_pinned(slot))

    def test_a_pinned_slot_survives_the_auto_cap_after_a_timer_refresh(self):
        """The refreshed pin must still beat eviction, not just be recorded."""
        self._group("group-a", self.WORKSPACE_A, "secret-a")
        web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=self.WORKSPACE_A,
            origin="manual",
        )
        web_runtime_state.capture_live_workspaces(api.session_manager)

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        for index in range(web_runtime_state.MAX_AUTO_WORKSPACE_SLOTS + 4):
            workspace_id = f"w{index:011d}"
            state["workspaces"][workspace_id] = {
                "workspace_id": workspace_id,
                "label": f"Slot {index}",
                "origin": "auto",
                "saved_at": 9_000_000_000.0 + index,
                "groups": [{"group_id": f"g{index}", "sessions": [{"host": "h"}]}],
            }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        self._group("group-b", self.WORKSPACE_B, "secret-b")
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_B
        )

        stored = json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"]
        self.assertIn(self.WORKSPACE_A, stored)

    def test_a_legacy_v2_manual_slot_keeps_its_pin_through_migration(self):
        """Files written before the split only carry origin="manual"."""
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "workspaces": {
                        self.WORKSPACE_A: {
                            "workspace_id": self.WORKSPACE_A,
                            "label": "Alpha",
                            "origin": "manual",
                            "saved_at": 1000.0,
                            "groups": [
                                {"group_id": "g1", "sessions": [{"host": "h"}]}
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        summaries = web_runtime_state.list_restorable_workspaces()

        self.assertEqual(summaries[0]["manually_saved_at"], 1000.0)
        slot = web_runtime_state.load_restorable_workspace(self.WORKSPACE_A)
        self.assertTrue(web_runtime_state._slot_is_pinned(slot))

    def test_capture_workspace_enforces_the_auto_slot_cap(self):
        self._group("group-a", self.WORKSPACE_A, "secret-a")
        state = {"version": 2, "workspaces": {}}
        for index in range(web_runtime_state.MAX_AUTO_WORKSPACE_SLOTS):
            workspace_id = f"w{index:011d}"
            state["workspaces"][workspace_id] = {
                "workspace_id": workspace_id,
                "label": f"Slot {index}",
                "origin": "auto",
                "saved_at": 1000.0 + index,
                "groups": [{"group_id": f"g{index}", "sessions": [{"host": "h"}]}],
            }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=self.WORKSPACE_A,
        )

        stored = json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"]
        auto_count = sum(1 for slot in stored.values() if slot["origin"] == "auto")
        self.assertEqual(auto_count, web_runtime_state.MAX_AUTO_WORKSPACE_SLOTS)
        self.assertNotIn("w00000000000", stored)
        self.assertIn(self.WORKSPACE_A, stored)


class _PausedSnapshotManager:
    """Manager proxy that blocks inside ``snapshot_live_workspaces``.

    Lets a test hold a capture at exactly the window MW-02 describes: the live
    shape has been read, the state-file lock has not been taken yet.
    """

    def __init__(self, manager, snapshotted, release):
        self._manager = manager
        self._snapshotted = snapshotted
        self._release = release

    def snapshot_live_workspaces(self):
        snapshot = self._manager.snapshot_live_workspaces()
        self._snapshotted.set()
        if not self._release.wait(10):
            raise AssertionError("paused capture was never released")
        return snapshot


class RuntimeStateCommitOrderingTestCase(unittest.TestCase):
    """MW-02: a capture that snapshotted earlier must not commit later."""

    WORKSPACE_A = "aaaaaaaaaaaa"

    def setUp(self):
        api.session_manager.reset_sessions()
        api.session_manager.create_workspace("Alpha", self.WORKSPACE_A)
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

    def _group(self, group_id):
        api.session_manager.create_group(
            name=group_id,
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id=group_id,
            workspace_id=self.WORKSPACE_A,
        )
        api.session_manager.create_session(
            group_id=group_id,
            host=f"{group_id}.example",
            directory="/srv/app",
        )

    def _stored_workspaces(self):
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"]

    def _run_paused_capture(self, capture):
        """Start `capture` on a manager paused after its live snapshot."""
        snapshotted = threading.Event()
        release = threading.Event()
        result = {}

        def run():
            try:
                result["value"] = capture(
                    _PausedSnapshotManager(api.session_manager, snapshotted, release)
                )
            except BaseException as exc:  # surfaced by the assertions below
                result["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        self.addCleanup(release.set)
        self.addCleanup(worker.join, 10)
        self.assertTrue(snapshotted.wait(10), "capture never reached the live snapshot")
        return release, worker, result

    def _finish(self, release, worker, result):
        release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", result)
        return result.get("value")

    def test_an_autosave_that_snapshotted_before_a_forget_cannot_resurrect_the_slot(self):
        self._group("group-a")
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )
        self.assertIn(self.WORKSPACE_A, self._stored_workspaces())

        release, worker, result = self._run_paused_capture(
            lambda manager: web_runtime_state.capture_live_workspaces(manager)
        )
        # The close happens while the autosave holds its stale live snapshot.
        api.session_manager.remove_group("group-a")
        self.assertTrue(web_runtime_state.clear_workspace(self.WORKSPACE_A))
        stored = self._finish(release, worker, result)

        self.assertNotIn(self.WORKSPACE_A, stored)
        self.assertNotIn(self.WORKSPACE_A, self._stored_workspaces())
        self.assertIsNone(
            web_runtime_state.load_restorable_workspace(self.WORKSPACE_A)
        )

    def test_a_manual_save_that_snapshotted_before_a_forget_is_refused(self):
        self._group("group-a")

        release, worker, result = self._run_paused_capture(
            lambda manager: web_runtime_state.capture_workspace(
                manager, workspace_id=self.WORKSPACE_A, origin="manual"
            )
        )
        api.session_manager.remove_group("group-a")
        web_runtime_state.clear_workspace(self.WORKSPACE_A)
        slot = self._finish(release, worker, result)

        self.assertIsNone(slot)
        self.assertNotIn(self.WORKSPACE_A, self._stored_workspaces())

    def test_an_older_autosave_cannot_overwrite_a_newer_manual_save(self):
        self._group("group-a")

        release, worker, result = self._run_paused_capture(
            lambda manager: web_runtime_state.capture_live_workspaces(manager)
        )
        # A second group joins the workspace and the user saves it explicitly,
        # all while the timer still holds its one-group snapshot.
        self._group("group-b")
        manual = web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A, origin="manual"
        )
        self.assertEqual(len(manual["groups"]), 2)
        stored = self._finish(release, worker, result)

        self.assertNotIn(self.WORKSPACE_A, stored)
        slot = self._stored_workspaces()[self.WORKSPACE_A]
        self.assertEqual(slot["origin"], "manual")
        self.assertEqual(
            [group["group_id"] for group in slot["groups"]], ["group-a", "group-b"]
        )

    def test_a_capture_taken_after_a_forget_still_saves_the_workspace(self):
        self._group("group-a")
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )
        web_runtime_state.clear_workspace(self.WORKSPACE_A)

        slot = web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )

        self.assertIsNotNone(slot)
        self.assertIn(self.WORKSPACE_A, self._stored_workspaces())

    def test_a_forget_of_an_unsaved_workspace_still_blocks_an_older_capture(self):
        """The tombstone must be recorded even when no slot existed yet."""
        self._group("group-a")

        release, worker, result = self._run_paused_capture(
            lambda manager: web_runtime_state.capture_live_workspaces(manager)
        )
        api.session_manager.remove_group("group-a")
        self.assertFalse(web_runtime_state.clear_workspace(self.WORKSPACE_A))
        self._finish(release, worker, result)

        self.assertNotIn(self.WORKSPACE_A, self._stored_workspaces())


class RuntimeStateCrossProcessOrderingTestCase(unittest.TestCase):
    """Phase 1: two store owners on one file must order against each other.

    Each :class:`RuntimeStateStore` keeps its own in-process tickets, so two
    instances sharing a path stand in for two GridVibe processes: only the
    durable per-workspace revisions can order them.
    """

    WORKSPACE_A = "aaaaaaaaaaaa"

    def setUp(self):
        api.session_manager.reset_sessions()
        api.session_manager.create_workspace("Alpha", self.WORKSPACE_A)
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.store_a = web_runtime_state.RuntimeStateStore(lambda: str(self.state_path))
        self.store_b = web_runtime_state.RuntimeStateStore(lambda: str(self.state_path))
        self._group("group-a")

    def _group(self, group_id):
        api.session_manager.create_group(
            name=group_id,
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id=group_id,
            workspace_id=self.WORKSPACE_A,
        )
        api.session_manager.create_session(
            group_id=group_id, host=f"{group_id}.example", directory="/srv/app"
        )

    def _stored(self):
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"]

    def _run_paused_capture(self, store, **kwargs):
        snapshotted = threading.Event()
        release = threading.Event()
        result = {}

        def run():
            try:
                result["value"] = store.capture_workspace(
                    _PausedSnapshotManager(api.session_manager, snapshotted, release),
                    workspace_id=self.WORKSPACE_A,
                    **kwargs,
                )
            except BaseException as exc:  # surfaced by the assertions below
                result["error"] = exc

        worker = threading.Thread(target=run)
        worker.start()
        self.addCleanup(release.set)
        self.addCleanup(worker.join, 10)
        self.assertTrue(snapshotted.wait(10), "capture never reached the snapshot")
        return release, worker, result

    def _finish(self, release, worker, result):
        release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertNotIn("error", result)
        return result.get("value")

    def test_the_other_owners_forget_rejects_a_pre_clear_capture(self):
        self.store_a.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )

        release, worker, result = self._run_paused_capture(self.store_a)
        # The "other process" closes and forgets while A holds its snapshot.
        self.store_b.clear_workspace(self.WORKSPACE_A)
        slot = self._finish(release, worker, result)

        self.assertIsNone(slot)
        self.assertNotIn(self.WORKSPACE_A, self._stored())

    def test_the_other_owners_newer_save_survives_an_older_autosave(self):
        self.store_a.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )

        release, worker, result = self._run_paused_capture(self.store_a, origin="auto")
        self._group("group-b")
        newer = self.store_b.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A, origin="manual"
        )
        self.assertEqual(len(newer["groups"]), 2)
        self._finish(release, worker, result)

        slot = self._stored()[self.WORKSPACE_A]
        self.assertEqual(
            [group["group_id"] for group in slot["groups"]], ["group-a", "group-b"]
        )

    def test_a_durable_tombstone_outlives_the_owner_that_wrote_it(self):
        """Restart case: the rejecting store never saw the clear happen."""
        self.store_a.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )
        observed = self.store_a.observed_revisions()[self.WORKSPACE_A]
        self.store_a.clear_workspace(self.WORKSPACE_A)

        # A brand-new owner (a restarted process) reads the file and still
        # refuses a capture that observed the pre-clear revision.
        restarted = web_runtime_state.RuntimeStateStore(lambda: str(self.state_path))
        with patch.object(
            restarted, "observed_revisions", return_value={self.WORKSPACE_A: observed}
        ):
            slot = restarted.capture_workspace(
                api.session_manager, workspace_id=self.WORKSPACE_A
            )

        self.assertIsNone(slot)
        self.assertNotIn(self.WORKSPACE_A, self._stored())

    def test_a_fresh_capture_after_the_tombstone_still_saves(self):
        """A tombstone orders in-flight captures; it is not permanent."""
        self.store_a.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )
        self.store_b.clear_workspace(self.WORKSPACE_A)

        slot = self.store_a.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )

        self.assertIsNotNone(slot)
        self.assertIn(self.WORKSPACE_A, self._stored())

    def test_the_cross_process_lock_is_exclusive_and_released(self):
        lock = web_runtime_state._CrossProcessStateLock(str(self.state_path))
        blocked = {}

        with lock:
            def contend():
                try:
                    with web_runtime_state._CrossProcessStateLock(
                        str(self.state_path), timeout=0.2
                    ):
                        blocked["acquired"] = True
                except web_runtime_state.RuntimeStatePersistenceError as exc:
                    blocked["error"] = exc

            worker = threading.Thread(target=contend)
            worker.start()
            worker.join(10)

        self.assertNotIn("acquired", blocked)
        self.assertIn("error", blocked)
        # Released on exit: the next owner gets it straight away.
        with web_runtime_state._CrossProcessStateLock(str(self.state_path), timeout=1):
            pass

    def test_the_tombstone_map_stays_bounded(self):
        self.store_a.capture_workspace(
            api.session_manager, workspace_id=self.WORKSPACE_A
        )
        for index in range(web_runtime_state.MAX_TOMBSTONES + 25):
            self.store_a.clear_workspace(f"t{index:011d}")

        revisions = json.loads(self.state_path.read_text(encoding="utf-8"))["revisions"]
        tombstones = [key for key in revisions if key != self.WORKSPACE_A]
        self.assertLessEqual(len(tombstones), web_runtime_state.MAX_TOMBSTONES)
        # The live slot's own ordering entry is never pruned.
        self.assertIn(self.WORKSPACE_A, revisions)


class RuntimeStatePersistenceFailureTestCase(unittest.TestCase):
    """MW-10: an acknowledgement must mean the revision reached the disk."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Files",
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["group_id"]

    def test_a_failed_temporary_write_raises_instead_of_logging_and_returning(self):
        with patch("builtins.open", side_effect=OSError("No space left on device")):
            with self.assertRaises(web_runtime_state.RuntimeStatePersistenceError):
                web_runtime_state._write_state_locked(
                    {"version": 3, "workspaces": {}, "revisions": {}},
                    str(self.state_path),
                )
        self.assertFalse(self.state_path.exists())

    def test_a_failed_replace_leaves_no_temporary_file_behind(self):
        with patch("os.replace", side_effect=OSError("locked by antivirus")):
            with self.assertRaises(web_runtime_state.RuntimeStatePersistenceError):
                web_runtime_state._write_state_locked(
                    {"version": 3, "workspaces": {}, "revisions": {}},
                    str(self.state_path),
                )
        leftovers = [
            entry.name
            for entry in Path(self.temp_dir.name).iterdir()
            if entry.name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])

    def test_manual_save_reports_a_retryable_failure_not_success(self):
        self._launch()

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=web_runtime_state.RuntimeStatePersistenceError("disk full"),
        ):
            response = self.client.post("/api/runtime-state/save", json={})

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["retryable"])
        self.assertNotIn("groups", payload)
        self.assertIsNone(web_runtime_state.load_restorable_workspace())

    def test_forget_reports_a_retryable_failure_not_success(self):
        group_id = self._launch()
        web_runtime_state.capture_workspace(api.session_manager)
        self.client.delete(f"/api/sessions?group={group_id}")
        # The close already forgot the emptied workspace, so re-capture a slot
        # for the failing Forget to act on.
        api.session_manager.reset_sessions()
        self._launch()
        web_runtime_state.capture_workspace(api.session_manager)
        api.session_manager.reset_sessions()

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=web_runtime_state.RuntimeStatePersistenceError("read-only fs"),
        ):
            response = self.client.delete("/api/runtime-state")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["forgotten"])
        self.assertTrue(payload["retryable"])
        # Nothing was removed, so the slot is still offered.
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace())

    def test_autosave_keeps_the_last_good_file_and_logs_one_error_per_streak(self):
        self._launch()
        web_runtime_state.capture_workspace(api.session_manager, origin="manual")
        good_bytes = self.state_path.read_bytes()
        api._workspace_autosave_last_error_at = 0.0
        self.addCleanup(setattr, api, "_workspace_autosave_last_error_at", 0.0)

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=web_runtime_state.RuntimeStatePersistenceError("disk full"),
        ):
            with self.assertLogs(api.logger, level="ERROR") as captured:
                api._run_workspace_autosave_tick()
                api._run_workspace_autosave_tick()
                api._run_workspace_autosave_tick()

        # One structured error for the streak, not one per tick.
        self.assertEqual(len(captured.records), 1)
        self.assertIn("RuntimeStatePersistenceError", captured.output[0])
        self.assertEqual(self.state_path.read_bytes(), good_bytes)

    def test_a_recovered_autosave_reports_the_next_failure_again(self):
        self._launch()
        api._workspace_autosave_last_error_at = 0.0
        self.addCleanup(setattr, api, "_workspace_autosave_last_error_at", 0.0)

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=web_runtime_state.RuntimeStatePersistenceError("disk full"),
        ):
            with self.assertLogs(api.logger, level="ERROR"):
                api._run_workspace_autosave_tick()
        api._run_workspace_autosave_tick()
        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=web_runtime_state.RuntimeStatePersistenceError("disk full"),
        ):
            with self.assertLogs(api.logger, level="ERROR") as second:
                api._run_workspace_autosave_tick()

        self.assertEqual(len(second.records), 1)


class RuntimeStateSchemaRecoveryTestCase(unittest.TestCase):
    """MW-16 (persistence half): a bad file is quarantined, never laundered."""

    def setUp(self):
        api.session_manager.reset_sessions()
        api.session_manager.create_workspace("Alpha", "aaaaaaaaaaaa")
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capture_a_slot(self):
        api.session_manager.create_group(
            name="group-a",
            connection_mode="ssh",
            layout="single",
            terminal_count=1,
            group_id="group-a",
            workspace_id="aaaaaaaaaaaa",
        )
        api.session_manager.create_session(
            group_id="group-a", host="a.example", directory="/srv/app"
        )
        return web_runtime_state.capture_workspace(
            api.session_manager, workspace_id="aaaaaaaaaaaa", origin="manual"
        )

    def _quarantined(self):
        return sorted(
            entry.name
            for entry in Path(self.temp_dir.name).iterdir()
            if ".corrupt-" in entry.name
        )

    def test_a_corrupt_file_is_quarantined_and_the_last_good_state_recovered(self):
        self._capture_a_slot()
        # A second commit copies the first one to the last-good backup, which
        # is the state recovery falls back to.
        self._capture_a_slot()
        backup = json.loads(Path(f"{self.state_path}.bak").read_text(encoding="utf-8"))
        self.state_path.write_text("{ this is not json", encoding="utf-8")

        summaries = web_runtime_state.list_restorable_workspaces()

        self.assertEqual([row["workspace_id"] for row in summaries], ["aaaaaaaaaaaa"])
        self.assertEqual(len(self._quarantined()), 1)
        # Recovery yields the backup's shape, not an empty state.
        self.assertEqual(
            summaries[0]["group_count"],
            len(backup["workspaces"]["aaaaaaaaaaaa"]["groups"]),
        )
        self.assertEqual(
            summaries[0]["saved_at"],
            backup["workspaces"]["aaaaaaaaaaaa"]["saved_at"],
        )

    def test_an_unsupported_future_schema_is_quarantined_not_reinterpreted(self):
        self._capture_a_slot()
        self._capture_a_slot()
        self.state_path.write_text(
            json.dumps({"version": 99, "workspaces": {}, "somethingNew": True}),
            encoding="utf-8",
        )

        slot = web_runtime_state.load_restorable_workspace("aaaaaaaaaaaa")

        self.assertIsNotNone(slot)
        self.assertEqual(len(self._quarantined()), 1)

    def test_a_corrupt_file_with_no_backup_is_still_quarantined(self):
        self.state_path.write_text("<html>not state at all</html>", encoding="utf-8")

        self.assertEqual(web_runtime_state.list_restorable_workspaces(), [])
        self.assertEqual(len(self._quarantined()), 1)
        self.assertFalse(self.state_path.exists())

    def test_a_quarantined_file_is_not_silently_overwritten_by_the_next_capture(self):
        self._capture_a_slot()
        self._capture_a_slot()
        corrupt = "{ truncated"
        self.state_path.write_text(corrupt, encoding="utf-8")

        self._capture_a_slot()

        quarantined = self._quarantined()
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(
            (Path(self.temp_dir.name) / quarantined[0]).read_text(encoding="utf-8"),
            corrupt,
        )

    def test_a_v2_file_still_loads_and_is_rewritten_at_the_current_version(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "workspaces": {
                        "aaaaaaaaaaaa": {
                            "workspace_id": "aaaaaaaaaaaa",
                            "label": "Alpha",
                            "origin": "auto",
                            "saved_at": 1000.0,
                            "groups": [
                                {"group_id": "g1", "sessions": [{"host": "h"}]}
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("aaaaaaaaaaaa"))
        self.assertEqual(self._quarantined(), [])

        self._capture_a_slot()

        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], web_runtime_state.SCHEMA_VERSION)
        self.assertIn("revisions", data)

    def test_capture_logs_shape_metadata_without_connection_details(self):
        with self.assertLogs(web_runtime_state.logger, level="DEBUG") as captured:
            self._capture_a_slot()

        commits = [line for line in captured.output if "Runtime-state commit" in line]
        self.assertEqual(len(commits), 1)
        self.assertIn("writer=manual", commits[0])
        self.assertIn("groups=1", commits[0])
        self.assertIn("panes=1", commits[0])
        self.assertNotIn("a.example", commits[0])
        self.assertNotIn("/srv/app", commits[0])


class RuntimeStateProductionPathGuardTestCase(unittest.TestCase):
    """MW-01: the suite must never own the developer's runtime_state.json."""

    def test_the_suite_resolves_a_state_path_outside_the_project(self):
        self.assertTrue(os.environ.get("GRIDVIBE_TEST_MODE"))
        self.assertNotEqual(
            os.path.abspath(web_runtime_state.RUNTIME_STATE_PATH),
            os.path.abspath(web_runtime_state.PRODUCTION_STATE_PATH),
        )

    def test_reads_and_writes_of_the_production_file_are_refused_in_test_mode(self):
        with patch.object(
            web_runtime_state,
            "RUNTIME_STATE_PATH",
            web_runtime_state.PRODUCTION_STATE_PATH,
        ):
            with self.assertRaises(web_runtime_state.RuntimeStatePathError):
                web_runtime_state.list_restorable_workspaces()
            with self.assertRaises(web_runtime_state.RuntimeStatePathError):
                web_runtime_state.load_restorable_workspace()
            with self.assertRaises(web_runtime_state.RuntimeStatePathError):
                web_runtime_state.clear_workspace("cccccccccccc")

    def test_the_guard_leaves_a_redirected_path_alone(self):
        with TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "runtime_state.json"
            with patch.object(
                web_runtime_state, "RUNTIME_STATE_PATH", str(state_path)
            ):
                self.assertEqual(web_runtime_state.list_restorable_workspaces(), [])
                self.assertFalse(web_runtime_state.clear_workspace("cccccccccccc"))


class MultiWorkspaceStage3TestCase(WorkspaceSocketClientMixin, unittest.TestCase):
    """Stage 3 destination, uniqueness guard, move, rename, and lifetime."""

    WORKSPACE_A = "aaaaaaaaaaaa"

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        for target, attribute, value in (
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        return self.client.post("/api/sessions", json=body)

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    # ── Destination ──

    def test_launch_without_a_destination_still_targets_default(self):
        response = self._launch()

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["workspace_id"], "default")
        self.assertFalse(payload["workspace_created"])

    def test_launch_into_a_new_workspace_creates_and_labels_it(self):
        response = self._launch(new_workspace=True, workspace_label="Reviews")

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        workspace_id = payload["workspace_id"]
        self.assertNotEqual(workspace_id, "default")
        self.assertTrue(payload["workspace_created"])
        workspace = api.session_manager.get_workspace(workspace_id)
        self.assertEqual(workspace.label, "Reviews")
        # A workspace that received a group is no longer "deliberately empty".
        self.assertFalse(workspace.retain_when_empty)
        self.assertEqual(
            [group.group_id for group in api.session_manager.get_workspace_groups("default")],
            [],
        )

    def test_launch_into_an_unknown_workspace_is_rejected(self):
        response = self._launch(workspace_id=self.WORKSPACE_A)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Workspace not found")

    def test_failed_launch_rolls_back_the_workspace_it_created(self):
        before = {workspace.workspace_id for workspace in api.session_manager.get_all_workspaces()}

        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "new_workspace": True,
                "workspace_label": "Doomed",
                "sessions": [
                    {
                        "title": "Broken",
                        "startup_mode": "browser",
                        "initial_command": "ftp://example.test",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        after = {workspace.workspace_id for workspace in api.session_manager.get_all_workspaces()}
        # A dead destination must never linger in the picker.
        self.assertEqual(before, after)

    # ── §6 uniqueness table ──

    def test_preset_not_live_anywhere_launches_normally(self):
        response = self._launch(saved_session_id="alpha", new_workspace=True)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["group_id"], "saved-session-alpha")

    def test_preset_live_in_the_target_workspace_replaces_in_place(self):
        first = self._launch(saved_session_id="alpha")
        self.assertEqual(first.status_code, 201)
        original_session_ids = {
            session["session_id"] for session in first.get_json()["sessions"]
        }

        second = self._launch(saved_session_id="alpha")

        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.get_json()["group_id"], "saved-session-alpha")
        self.assertFalse(
            original_session_ids
            & {session["session_id"] for session in second.get_json()["sessions"]}
        )

    def test_preset_live_in_another_workspace_conflicts_without_stealing(self):
        first = self._launch(saved_session_id="alpha", new_workspace=True)
        self.assertEqual(first.status_code, 201)
        owner_workspace_id = first.get_json()["workspace_id"]
        api.session_manager.rename_workspace(owner_workspace_id, "Reviews")
        live_session_ids = {
            session["session_id"] for session in first.get_json()["sessions"]
        }
        workspaces_before = {
            workspace.workspace_id for workspace in api.session_manager.get_all_workspaces()
        }

        conflict = self._launch(saved_session_id="alpha", workspace_id="default")

        self.assertEqual(conflict.status_code, 409)
        payload = conflict.get_json()
        self.assertEqual(payload["conflict"], "saved_session_live")
        self.assertEqual(payload["saved_session_id"], "alpha")
        self.assertEqual(payload["group_id"], "saved-session-alpha")
        self.assertEqual(payload["workspace_id"], owner_workspace_id)
        self.assertEqual(payload["workspace_label"], "Reviews")
        # The owning workspace's live sessions survive untouched: the conflict
        # is resolved before any destructive replace-in-place.
        self.assertEqual(
            {session.session_id for session in api.session_manager.get_group_sessions("saved-session-alpha")},
            live_session_ids,
        )
        self.assertEqual(
            workspaces_before,
            {workspace.workspace_id for workspace in api.session_manager.get_all_workspaces()},
        )

    def test_conflicting_launch_into_a_new_workspace_rolls_it_back(self):
        self._launch(saved_session_id="alpha")
        before = {workspace.workspace_id for workspace in api.session_manager.get_all_workspaces()}

        conflict = self._launch(saved_session_id="alpha", new_workspace=True)

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            before,
            {workspace.workspace_id for workspace in api.session_manager.get_all_workspaces()},
        )

    # ── Workspace CRUD ──

    def test_create_workspace_is_retained_while_deliberately_empty(self):
        response = self.client.post("/api/workspaces", json={"label": "Scratch"})

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertTrue(payload["retain_when_empty"])
        self.assertEqual(payload["group_count"], 0)
        workspace_id = payload["workspace_id"]

        api.session_manager.clear_disconnected_sessions()

        self.assertIsNotNone(api.session_manager.get_workspace(workspace_id))

    def test_first_group_clears_retention_and_normal_pruning_resumes(self):
        workspace_id = self.client.post("/api/workspaces", json={}).get_json()["workspace_id"]
        launch = self._launch(workspace_id=workspace_id)
        self.assertEqual(launch.status_code, 201)
        self.assertFalse(api.session_manager.get_workspace(workspace_id).retain_when_empty)

        closed = self.client.delete(
            "/api/sessions",
            query_string={"workspace_id": workspace_id, "group": launch.get_json()["group_id"]},
        )

        self.assertEqual(closed.status_code, 200)
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))

    def test_workspace_list_reports_labels_and_group_counts(self):
        self._launch(session_name="Main")
        created = self.client.post("/api/workspaces", json={"label": "Scratch"}).get_json()

        response = self.client.get("/api/workspaces")

        self.assertEqual(response.status_code, 200)
        rows = {row["workspace_id"]: row for row in response.get_json()["workspaces"]}
        self.assertEqual(rows["default"]["group_count"], 1)
        self.assertEqual(rows[created["workspace_id"]]["group_count"], 0)
        self.assertEqual(rows[created["workspace_id"]]["label"], "Scratch")

    def test_rename_changes_the_live_label_without_writing_the_snapshot(self):
        """MW-04: rename is not a third shape writer.

        Only the autosave timer and an explicit Save Workspace may capture
        shape. A rename that recaptured would persist whatever transient shape
        happened to be live when a cosmetic label changed.
        """
        self._launch(session_name="Main")
        with patch.object(
            web_runtime_state, "_write_state_locked"
        ) as write_state:
            response = self.client.patch(
                "/api/workspaces/default", json={"label": "Renamed"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["label"], "Renamed")
        self.assertEqual(api.session_manager.get_workspace("default").label, "Renamed")
        write_state.assert_not_called()

    def test_the_next_real_capture_persists_a_renamed_label(self):
        """The label still becomes durable — through a permitted writer."""
        self._launch(session_name="Main")
        self.client.patch("/api/workspaces/default", json={"label": "Renamed"})

        api._run_workspace_autosave_tick()

        saved_slot = web_runtime_state.load_restorable_workspace("default")
        self.assertEqual(saved_slot["label"], "Renamed")
        self.assertEqual(saved_slot["origin"], "auto")

    def test_rename_rejects_unknown_workspaces_and_missing_labels(self):
        unknown = self.client.patch(
            f"/api/workspaces/{self.WORKSPACE_A}", json={"label": "Nope"}
        )
        missing = self.client.patch("/api/workspaces/default", json={})
        malformed = self.client.patch("/api/workspaces/NOT-AN-ID", json={"label": "x"})

        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(malformed.status_code, 400)

    def test_workspace_label_is_bounded_and_single_line(self):
        response = self.client.post(
            "/api/workspaces", json={"label": f"  spaced\n\tname  {'x' * 200}"}
        )

        label = response.get_json()["label"]
        self.assertEqual(len(label), 80)
        self.assertTrue(label.startswith("spaced name x"))

    # ── Move ──

    def test_move_preserves_every_session_and_notifies_both_rooms(self):
        launch = self._launch(session_name="Main")
        group_id = launch.get_json()["group_id"]
        session_ids = {session["session_id"] for session in launch.get_json()["sessions"]}
        target = self.client.post("/api/workspaces", json={"label": "Reviews"}).get_json()
        default_client = self._socket_client("default")
        target_client = self._socket_client(target["workspace_id"])
        other = api.session_manager.create_workspace("Other")
        other_client = self._socket_client(other.workspace_id)

        response = self.client.post(
            f"/api/session-groups/{group_id}/move",
            json={"target_workspace_id": target["workspace_id"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["moved"])
        self.assertEqual(payload["workspace_id"], target["workspace_id"])
        self.assertEqual(payload["source_workspace_id"], "default")
        self.assertEqual(payload["source_groups"], [])
        # Sessions keep their ids: nothing was recreated, closed, or reconnected.
        self.assertEqual(
            {session.session_id for session in api.session_manager.get_group_sessions(group_id)},
            session_ids,
        )
        self.assertEqual(
            [event["reason"] for event in _workspace_events(default_client)],
            ["moved"],
        )
        self.assertEqual(
            [event["reason"] for event in _workspace_events(target_client)],
            ["moved"],
        )
        self.assertEqual(_workspace_events(other_client), [])

    def test_move_appends_to_the_destination_and_compacts_the_source(self):
        first = self._launch(session_name="First").get_json()["group_id"]
        second = self._launch(session_name="Second").get_json()["group_id"]
        third = self._launch(session_name="Third").get_json()["group_id"]
        target = self.client.post("/api/workspaces", json={}).get_json()["workspace_id"]
        self.client.post(
            f"/api/session-groups/{third}/move", json={"target_workspace_id": target}
        )

        response = self.client.post(
            f"/api/session-groups/{first}/move", json={"target_workspace_id": target}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [group.group_id for group in api.session_manager.get_workspace_groups(target)],
            [third, first],
        )
        source_groups = api.session_manager.get_workspace_groups("default")
        self.assertEqual([group.group_id for group in source_groups], [second])
        self.assertEqual([group.display_order for group in source_groups], [0])

    def test_move_to_a_new_workspace_creates_the_destination(self):
        group_id = self._launch().get_json()["group_id"]

        response = self.client.post(
            f"/api/session-groups/{group_id}/move",
            json={"new_workspace": True, "label": "Split off"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["workspace_created"])
        self.assertEqual(
            api.session_manager.get_workspace(payload["workspace_id"]).label,
            "Split off",
        )

    def test_move_into_the_same_workspace_is_a_reported_no_op(self):
        group_id = self._launch().get_json()["group_id"]

        response = self.client.post(
            f"/api/session-groups/{group_id}/move",
            json={"target_workspace_id": "default"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["moved"])
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, "default")

    def test_move_rejects_unknown_groups_and_destinations(self):
        group_id = self._launch().get_json()["group_id"]

        unknown_group = self.client.post(
            "/api/session-groups/nope/move", json={"target_workspace_id": "default"}
        )
        unknown_workspace = self.client.post(
            f"/api/session-groups/{group_id}/move",
            json={"target_workspace_id": self.WORKSPACE_A},
        )
        malformed_workspace = self.client.post(
            f"/api/session-groups/{group_id}/move",
            json={"target_workspace_id": "NOT-AN-ID"},
        )

        self.assertEqual(unknown_group.status_code, 404)
        self.assertEqual(unknown_workspace.status_code, 400)
        self.assertEqual(malformed_workspace.status_code, 400)
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, "default")

    def test_move_out_of_a_non_default_workspace_prunes_the_empty_source(self):
        launch = self._launch(new_workspace=True)
        group_id = launch.get_json()["group_id"]
        source_workspace_id = launch.get_json()["workspace_id"]

        response = self.client.post(
            f"/api/session-groups/{group_id}/move", json={"target_workspace_id": "default"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["source_workspace_pruned"])
        self.assertIsNone(api.session_manager.get_workspace(source_workspace_id))

    def test_moving_the_active_group_clears_the_sources_front_hint(self):
        group_id = self._launch().get_json()["group_id"]
        self._launch(session_name="Stays")
        api.session_manager.set_active_group("default", group_id)
        target = self.client.post("/api/workspaces", json={}).get_json()["workspace_id"]

        self.client.post(
            f"/api/session-groups/{group_id}/move", json={"target_workspace_id": target}
        )

        self.assertEqual(api.session_manager.get_active_group_id("default"), "")

    # ── Page and menu wiring ──

    def test_terminals_page_ships_the_multi_workspace_menu_items(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", True):
            html = self.client.get("/terminals").get_data(as_text=True)

        for element_id in (
            "renameWorkspaceItem",
            "newWorkspaceItem",
            "openWorkspaceList",
            "moveWorkspaceList",
            "closeWorkspaceWindowItem",
            # Close *window* and close *workspace* are separate verbs with
            # separate persistence effects, so the menu offers both.
            "closeWorkspaceItem",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn('id="workspaceNameModal"', html)
        self.assertIn('id="workspaceContextMenu"', html)
        self.assertIn('id="saveWorkspaceItem"', html)

    def test_workspace_menu_degrades_when_the_flag_is_off(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", False):
            html = self.client.get("/terminals").get_data(as_text=True)

        self.assertIn('id="saveWorkspaceItem"', html)
        for element_id in (
            "renameWorkspaceItem",
            "newWorkspaceItem",
            "closeWorkspaceWindowItem",
            "closeWorkspaceItem",
        ):
            self.assertNotIn(f'id="{element_id}"', html)

    def test_launcher_ships_the_destination_control_behind_the_flag(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", True):
            enabled = self.client.get("/").get_data(as_text=True)
        with patch.object(api.runtime_config, "multi_workspace_enabled", False):
            disabled = self.client.get("/").get_data(as_text=True)

        launcher_js = self._static("js/launcher.js")

        # The destination lives on the Launch button itself (a split CTA), so
        # there is no separate control to link to the launch action.
        self.assertIn('id="launchDestinationBtn"', enabled)
        self.assertIn('id="launchDestinationLabel"', enabled)
        self.assertIn('id="workspaceLiveList"', enabled)
        self.assertIn("function toggleLaunchDestinationMenu(event)", launcher_js)
        # With the flag off the CTA degrades to today's single button: the caret
        # and the destination line are both hidden by syncLaunchDestinationControl.
        self.assertNotIn('id="workspaceLiveList"', disabled)
        self.assertIn("caret.hidden = !enabled;", launcher_js)

    def test_move_and_conflict_flows_confirm_in_page_only(self):
        terminals_js = self._static("js/terminals.js")
        workspaces_js = self._static("js/workspaces.js")

        # A move evicts the cached view and tears down explorer editors, so the
        # dirty-buffer confirm must run first — in page, never window.confirm.
        self.assertIn(
            "!(await confirmDiscardAllExplorerEdits('Moving this session'))",
            terminals_js,
        )
        self.assertIn("async function resolveSavedSessionConflict(conflict)", terminals_js)
        self.assertIn("await openGenericConfirmModal({", terminals_js)
        # One move entry point shared by the menu and the tab context menu.
        self.assertIn("async function moveGroupToWorkspace(groupId, target = {})", workspaces_js)
        self.assertIn("openSessionTabContextMenu(event, group.group_id)", terminals_js)
        for banned in ("window.confirm(", "window.prompt(", "window.alert("):
            self.assertNotIn(banned, workspaces_js)

    def test_move_submenu_names_the_session_it_acts_on(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", True):
            html = self.client.get("/terminals").get_data(as_text=True)
        terminals_js = self._static("js/terminals.js")

        # "Move Session to Workspace" alone does not say which session, so the
        # heading carries the active tab's name and the list its aria-label.
        self.assertIn('id="moveWorkspaceScope"', html)
        self.assertIn("function setMoveWorkspaceScopeLabel(groupId)", terminals_js)
        self.assertIn("setMoveWorkspaceScopeLabel(targetGroupId);", terminals_js)
        self.assertIn("`Move session ${name} to workspace`", terminals_js)

    def test_alt_w_walks_the_workspaces_without_the_menu(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", True):
            html = self.client.get("/terminals").get_data(as_text=True)
        terminals_js = self._static("js/terminals.js")
        workspaces_js = self._static("js/workspaces.js")

        # The shortcut is discoverable from the menu it replaces.
        self.assertIn('<span class="workspace-submenu-hint">Alt+W</span>', html)
        # One ordering for the menu and the shortcut, so they cannot disagree.
        self.assertIn(
            "function nextWorkspaceInCycle(workspaces, currentWorkspaceId, step = 1)",
            workspaces_js,
        )
        self.assertIn("async function cycleWorkspaceWindow(step)", terminals_js)
        self.assertIn("cycleWorkspaceWindow(event.shiftKey ? -1 : 1);", terminals_js)
        # It is gated on the mode and never fires while typing in a real input.
        self.assertIn("if (!isMultiWorkspaceEnabled()) {", terminals_js)
        self.assertIn(
            "if (event.code !== 'KeyW' || isWorkspaceCycleBlockingTarget(event.target)) {",
            terminals_js,
        )
        # xterm must not send Alt+W on to the shell as ESC w.
        self.assertIn("&& event.code === 'KeyW') {", terminals_js)

    def test_alt_w_never_walks_onto_a_workspace_with_no_window(self):
        """The walk must switch windows, never conjure an empty one.

        A live workspace record is not an open window: ``default`` is permanent
        and Workspace ▸ New Workspace retains its workspace while it is empty,
        while a window closes itself as soon as its last group goes. Walking
        onto such a record opened a blank second window from a single-window
        session instead of reporting there was nowhere to go.
        """
        work = {"workspace_id": "aaaaaaaaaaaa", "group_count": 1}
        empty_default = {"workspace_id": "default", "group_count": 0}
        other = {"workspace_id": "bbbbbbbbbbbb", "group_count": 2}
        reserved = {
            "workspace_id": "cccccccccccc",
            "group_count": 0,
            "retain_when_empty": True,
        }

        cycle = self._js_workspace_cycle(
            [
                # The report: one window, plus the empty records around it.
                ([empty_default, work], "aaaaaaaaaaaa", 1),
                ([empty_default, work], "aaaaaaaaaaaa", -1),
                ([empty_default, work, {**other, "group_count": 0}], "aaaaaaaaaaaa", 1),
                # Two real windows still cycle, forwards and backwards.
                ([work, other], "aaaaaaaaaaaa", 1),
                ([work, other], "aaaaaaaaaaaa", -1),
                # An empty record between two windows is stepped over, not into.
                ([work, empty_default, other], "aaaaaaaaaaaa", 1),
                # A deliberately empty workspace is the one empty record that
                # *does* have a window: Workspace ▸ New Workspace opened it.
                ([work, reserved], "aaaaaaaaaaaa", 1),
            ]
        )

        self.assertEqual(
            cycle,
            [
                None,
                None,
                None,
                "bbbbbbbbbbbb",
                "bbbbbbbbbbbb",
                "bbbbbbbbbbbb",
                "cccccccccccc",
            ],
        )

    def _js_workspace_cycle(self, cases):
        """Run the shipped nextWorkspaceInCycle over cases, returning target ids."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        workspaces_js = self._static("js/workspaces.js")
        # The cycle now asks the one shared user-visible predicate, so both
        # functions are loaded: the walk and the rule it walks by ship together.
        source = "\n".join(
            _js_function_source(workspaces_js, name)
            for name in ("isUserVisibleWorkspace", "nextWorkspaceInCycle")
        )
        script = (
            f"{source}\n"
            "const out = JSON.parse(process.argv[2]).map(\n"
            "    ([list, current, step]) => (nextWorkspaceInCycle(list, current, step)\n"
            "        || {}).workspace_id || null\n"
            ");\n"
            "process.stdout.write(JSON.stringify(out));\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "cycle.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script_path), json.dumps(cases)],
                capture_output=True,
                text=True,
                check=True,
            )
        return json.loads(completed.stdout)

    def test_alt_w_still_cycles_from_a_focused_terminal(self):
        terminals_js = self._static("js/terminals.js")

        # xterm's helper textarea is the keyboard target of every focused pane,
        # so the plain editable-target guard would swallow the shortcut exactly
        # when a terminal is highlighted and leave the user stuck in the pane.
        self.assertIn("function isWorkspaceCycleBlockingTarget(target)", terminals_js)
        self.assertIn("target.closest('.xterm-helper-textarea')", terminals_js)

    def test_one_custom_key_event_handler_per_terminal(self):
        terminals_js = self._static("js/terminals.js")

        # xterm keeps a single custom key event handler: a second
        # attachCustomKeyEventHandler call replaces the first, which silently
        # dropped the Alt+W pass-through (xterm cancels the keys it claims with
        # stopPropagation, so the document shortcut never ran) and sent ESC w to
        # the shell instead. There must be exactly one install site.
        self.assertEqual(terminals_js.count("attachCustomKeyEventHandler("), 1)
        self.assertIn("function attachTerminalKeyEventHandler(term)", terminals_js)
        self.assertIn("attachTerminalKeyEventHandler(term);", terminals_js)
        # …and it still carries both the pass-throughs and the clipboard keys.
        self.assertIn("&& event.code === 'KeyW') {", terminals_js)
        self.assertIn("_copyText(term.getSelection());", terminals_js)
        self.assertIn("_pasteToTerminal(index);", terminals_js)

    def test_leaving_a_workspace_drops_this_window_terminal_focus(self):
        terminals_js = self._static("js/terminals.js")

        # Focus survives a window switch, so a pane left focused would swallow
        # the workspace/tab shortcuts the moment the window returns to front.
        self.assertIn(
            "function dropTerminalFocusForWindowSwitch()",
            terminals_js,
        )
        self.assertIn("clearActiveTerminalHighlight();", terminals_js)
        # Clicking straight into another window never reaches the in-app switch
        # path, so the window losing focus drops the highlight too — deferred a
        # tick so focus moving into a browser pane's iframe does not count.
        self.assertIn("window.addEventListener('blur', () => {", terminals_js)
        self.assertIn("if (!document.hasFocus()) {", terminals_js)
        # Every in-app switch path goes through the one wrapper (guardrail 6):
        # the only bare openWorkspaceWindow call left in this page is inside it.
        self.assertIn("async function switchToWorkspaceWindow(workspaceId, options = {})", terminals_js)
        self.assertIn("dropTerminalFocusForWindowSwitch();\n        return openWorkspaceWindow(", terminals_js)
        self.assertEqual(terminals_js.count("openWorkspaceWindow("), 1)
        self.assertEqual(terminals_js.count("switchToWorkspaceWindow("), 6)


class MultiWorkspaceRestoreTestCase(unittest.TestCase):
    """Stage 4 selective restore, Forget/Dismiss, the slot cap, and §9.3."""

    WORKSPACE_A = "aaaaaaaaaaaa"
    WORKSPACE_B = "bbbbbbbbbbbb"

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        for target, attribute, value in (
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_sessions_path)),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _launch(self, workspace_id="default", **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "workspace_id": workspace_id,
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _launch_ssh(self, workspace_id="default", terminal_count=1, **overrides):
        """Launch a group that really names the seeded preset's SSH target.

        The credential half of restore only applies to panes that still name
        the preset's host/username/port, so a fixture that exercises it has to
        be launched the way the preset would launch it.
        """
        body = {
            "connection_mode": "ssh",
            "layout": "single" if terminal_count == 1 else "grid",
            "session_name": overrides.pop("session_name", "Alpha"),
            "workspace_id": workspace_id,
            "sessions": [
                {
                    "host": "preset.example",
                    "username": "ubuntu",
                    "port": 22,
                    "password": "preset-secret",
                    "directory": "/srv",
                    "title": f"Pane {index + 1}",
                }
                for index in range(terminal_count)
            ],
        }
        body.update(overrides)
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _save_slot(self, workspace_id="default", origin="auto", label=None):
        slot = web_runtime_state.capture_workspace(
            api.session_manager,
            workspace_id=workspace_id,
            origin=origin,
            label=label,
        )
        self.assertIsNotNone(slot)
        return slot

    def _close_everything(self):
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()

    def _restore(self, workspace_ids):
        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": workspace_ids}
        )
        return response, response.get_json()

    def _seed_preset(self, name="Alpha", directory=None, terminal_count=1, password="preset-secret"):
        entry = web_saved_sessions.upsert_saved_session(
            config={
                "connection_mode": "ssh",
                "terminal_count": terminal_count,
                "layout": "single" if terminal_count == 1 else "vertical",
                "ssh": {
                    "host": "preset.example",
                    "username": "ubuntu",
                    "password": password,
                    "port": 22,
                    "default_dir": directory or "/srv",
                },
                "terminals": [
                    {"title": f"Pane {index + 1}", "directory": ""}
                    for index in range(terminal_count)
                ],
            },
            name=name,
        )
        return entry

    # ── Summaries ──

    def test_summaries_expose_counts_and_live_conflicts_without_config(self):
        self._launch(session_name="Main")
        self._save_slot()

        response = self.client.get("/api/runtime-state/workspaces")

        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["workspaces"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["workspace_id"], "default")
        self.assertEqual(row["group_count"], 1)
        self.assertEqual(row["pane_count"], 1)
        self.assertTrue(row["live_conflict"])
        self.assertNotIn("groups", row)
        self.assertNotIn("sessions", row)

    # ── R11 / subset restore ──

    def test_subset_restore_opens_only_the_selected_slots(self):
        self._launch(session_name="Main")
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self._launch(workspace_id=self.WORKSPACE_B, session_name="Beta work")
        web_runtime_state.capture_live_workspaces(api.session_manager)
        self._close_everything()

        response, payload = self._restore(["default"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["restored_count"], 1)
        self.assertTrue(payload["workspaces"][0]["restored"])
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)
        self.assertIsNone(api.session_manager.get_workspace(self.WORKSPACE_B))
        # R11: the unselected slot survives and is still offered afterwards.
        remaining = self.client.get("/api/runtime-state/workspaces").get_json()["workspaces"]
        self.assertIn(
            self.WORKSPACE_B,
            [row["workspace_id"] for row in remaining],
        )

    def test_restore_reuses_the_saved_workspace_id_exactly(self):
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self._launch(workspace_id=self.WORKSPACE_B, session_name="Beta work")
        self._save_slot(self.WORKSPACE_B)
        self._close_everything()

        _response, payload = self._restore([self.WORKSPACE_B])

        self.assertTrue(payload["workspaces"][0]["restored"])
        self.assertIsNotNone(api.session_manager.get_workspace(self.WORKSPACE_B))
        self.assertEqual(len(api.session_manager.get_workspace_groups(self.WORKSPACE_B)), 1)

    def test_restore_rejects_a_malformed_request(self):
        empty = self.client.post("/api/runtime-state/restore", json={"workspace_ids": []})
        missing = self.client.post("/api/runtime-state/restore", json={})

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(missing.status_code, 400)

    # ── §9.3 rows ──

    def test_r1_deleted_preset_replays_the_snapshot_with_a_warning(self):
        self._launch(session_name="Main", saved_session_id="gone-forever")
        self._save_slot()
        self._close_everything()

        _response, payload = self._restore(["default"])

        group = payload["workspaces"][0]["groups"][0]
        self.assertTrue(group["started"])
        self.assertEqual(group["warning"], "preset_missing")

    def test_r2_the_captured_shape_wins_over_an_edited_preset(self):
        """MW-03, regression matrix row 2 — the snapshot is the only shape source.

        Restore used to replay a still-existing preset's *current* config
        ("latest preset wins"), so editing a launcher preset rewrote a workspace
        nobody had saved since: the three-pane workspace captured here came back
        as the single `Files` pane the preset had been edited down to.
        """
        preset = self._seed_preset(name="Alpha", terminal_count=3)
        launched = self._launch_ssh(
            session_name="Old name",
            saved_session_id=preset["id"],
            terminal_count=3,
        )
        self._save_slot()
        self._close_everything()
        web_saved_sessions.upsert_saved_session(
            config={
                **preset["config"],
                "terminal_count": 1,
                "layout": "single",
                "ssh": {**preset["config"]["ssh"], "host": "edited.example"},
                "terminals": [{"title": "Files", "directory": ""}],
            },
            name="Edited",
            session_id=preset["id"],
        )

        with patch.object(api.socketio, "start_background_task"):
            _response, payload = self._restore(["default"])

        self.assertTrue(payload["workspaces"][0]["groups"][0]["started"])
        group = api.session_manager.get_group(f"saved-session-{preset['id']}")
        self.assertEqual(group.name, "Old name")
        self.assertEqual(group.terminal_count, 3)
        self.assertEqual(group.layout, launched["layout"])
        sessions = api.session_manager.get_group_sessions(group.group_id)
        self.assertEqual([session.host for session in sessions], ["preset.example"] * 3)
        self.assertEqual(
            [session.title for session in sessions],
            ["Pane 1", "Pane 2", "Pane 3"],
        )

    def test_r3_the_captured_layout_survives_a_preset_pane_count_change(self):
        """The stored split geometry belongs to the stored pane count.

        It used to be dropped (or replaced by the preset's) whenever the preset
        had since gained or lost a pane. With the snapshot authoritative, the
        pane count cannot change under it, so the geometry is simply replayed.
        """
        rects = [{"originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 1}]
        preset = self._seed_preset(name="Alpha", terminal_count=1)
        launched = self._launch_ssh(
            session_name="Alpha",
            saved_session_id=preset["id"],
            workspace_layout={"split_slot_rects": rects},
        )
        self.assertEqual(launched["workspace_layout"]["split_slot_rects"], rects)
        self._save_slot()
        self._close_everything()
        web_saved_sessions.upsert_saved_session(
            config={
                **preset["config"],
                "terminal_count": 2,
                "layout": "vertical",
                "terminals": [
                    {"title": "Pane 1", "directory": ""},
                    {"title": "Pane 2", "directory": ""},
                ],
            },
            name="Alpha",
            session_id=preset["id"],
        )

        with patch.object(api.socketio, "start_background_task"):
            _response, payload = self._restore(["default"])

        self.assertTrue(payload["workspaces"][0]["groups"][0]["started"])
        group = api.session_manager.get_group(f"saved-session-{preset['id']}")
        self.assertEqual(group.terminal_count, 1)
        self.assertEqual(group.workspace_layout, launched["workspace_layout"])

    def test_r4_missing_directory_still_restores_the_group(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        session = state["workspaces"]["default"]["groups"][0]["sessions"][0]
        session["directory"] = str(self.repo_dir / "deleted")
        session["explorer_root_directory"] = session["directory"]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        _response, payload = self._restore(["default"])

        self.assertTrue(payload["workspaces"][0]["restored"])
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)

    def test_r5_and_r12_an_already_live_workspace_is_refused_not_duplicated(self):
        self._launch(session_name="Main")
        self._save_slot()

        _response, payload = self._restore(["default"])

        self.assertFalse(payload["workspaces"][0]["restored"])
        self.assertEqual(payload["workspaces"][0]["reason"], "already_live")
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)

    def test_r12_a_second_restore_of_the_same_slot_is_idempotent(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()

        self._restore(["default"])
        _response, payload = self._restore(["default"])

        self.assertEqual(payload["workspaces"][0]["reason"], "already_live")
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)

    def test_r12_duplicate_ids_in_one_request_are_collapsed(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()

        _response, payload = self._restore(["default", "default"])

        self.assertEqual(len(payload["workspaces"]), 1)

    def test_r6_a_group_whose_preset_is_live_elsewhere_is_skipped(self):
        preset = self._seed_preset(name="Alpha")
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self._launch(workspace_id=self.WORKSPACE_B, saved_session_id=preset["id"])
        self._launch(workspace_id=self.WORKSPACE_B, session_name="Plain")
        self._save_slot(self.WORKSPACE_B)
        # Close only the saved workspace, then bring the preset back up
        # somewhere else so the restore hits the §6 conflict.
        api.session_manager.reset_sessions()
        with patch.object(api.socketio, "start_background_task"):
            self._launch(saved_session_id=preset["id"])

        with patch.object(api.socketio, "start_background_task"):
            _response, payload = self._restore([self.WORKSPACE_B])

        workspace_result = payload["workspaces"][0]
        self.assertTrue(workspace_result["restored"])
        skipped = [group for group in workspace_result["groups"] if group.get("skipped")]
        started = [group for group in workspace_result["groups"] if group["started"]]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["skipped"], "already_live")
        self.assertEqual(skipped[0]["workspace_id"], "default")
        self.assertEqual(len(started), 1)

    def test_r7_partial_success_reports_the_failed_group_and_keeps_the_rest(self):
        self._launch(session_name="Good")
        self._launch(session_name="Bad")
        self._save_slot()
        self._close_everything()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["workspaces"]["default"]["groups"][1]["sessions"] = []
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        _response, payload = self._restore(["default"])

        workspace_result = payload["workspaces"][0]
        self.assertTrue(workspace_result["restored"])
        self.assertEqual([group["started"] for group in workspace_result["groups"]], [True, False])
        self.assertTrue(workspace_result["groups"][1]["error"])

    def test_r8_front_group_hint_falls_back_to_the_first_started_group(self):
        first = self._launch(session_name="First")
        second = self._launch(session_name="Second")
        api.session_manager.set_active_group("default", second["group_id"])
        self._save_slot()
        self._close_everything()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["workspaces"]["default"]["active_group_id"], second["group_id"])
        state["workspaces"]["default"]["groups"][1]["sessions"] = []
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        _response, payload = self._restore(["default"])

        workspace_result = payload["workspaces"][0]
        started = workspace_result["groups"][0]
        self.assertEqual(workspace_result["active_group_id"], started["group_id"])
        self.assertNotEqual(workspace_result["active_group_id"], second["group_id"])
        self.assertNotEqual(started["group_id"], first["group_id"])

    def test_r8_a_started_front_group_is_honoured(self):
        self._launch(session_name="First")
        second = self._launch(session_name="Second")
        api.session_manager.set_active_group("default", second["group_id"])
        self._save_slot()
        self._close_everything()

        _response, payload = self._restore(["default"])

        self.assertEqual(
            payload["workspaces"][0]["active_group_id"],
            api.session_manager.get_active_group_id("default"),
        )
        self.assertEqual(
            api.session_manager.get_group(
                payload["workspaces"][0]["active_group_id"]
            ).name,
            "Second",
        )

    def test_r9_a_blank_label_is_derived_never_a_bare_timestamp(self):
        self._launch(session_name="Session 12:34:56")
        self._save_slot()
        self._close_everything()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["workspaces"]["default"]["label"] = ""
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        rows = self.client.get("/api/runtime-state/workspaces").get_json()["workspaces"]

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["label"])
        self.assertNotEqual(rows[0]["label"], "Session 12:34:56")

    def test_r10_hand_edited_and_legacy_state_degrades_safely(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "workspaces": {
                        "NOT-AN-ID": {"groups": [{"group_id": "x", "sessions": []}], "origin": "auto"},
                        "cccccccccccc": {"groups": [], "origin": "auto"},
                        "dddddddddddd": {"groups": [{"group_id": "y"}], "origin": "unknown"},
                        "eeeeeeeeeeee": {
                            "groups": [{"group_id": "z", "name": "Real", "sessions": []}],
                            "origin": "auto",
                            "native_zoom_factor": 99,
                            "unknown_key": "ignored",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        rows = self.client.get("/api/runtime-state/workspaces").get_json()["workspaces"]

        self.assertEqual([row["workspace_id"] for row in rows], ["eeeeeeeeeeee"])
        self.assertNotIn("unknown_key", rows[0])
        slot = web_runtime_state.load_restorable_workspace("eeeeeeeeeeee")
        self.assertIsNone(slot["native_zoom_factor"])

    def test_a_slot_that_starts_nothing_is_rolled_back(self):
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self._launch(workspace_id=self.WORKSPACE_B, session_name="Beta work")
        self._save_slot(self.WORKSPACE_B)
        self._close_everything()
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["workspaces"][self.WORKSPACE_B]["groups"][0]["sessions"] = []
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        _response, payload = self._restore([self.WORKSPACE_B])

        self.assertFalse(payload["workspaces"][0]["restored"])
        self.assertEqual(payload["workspaces"][0]["reason"], "no_groups_started")
        # A failed restore must not become an `already_live` conflict on retry.
        self.assertIsNone(api.session_manager.get_workspace(self.WORKSPACE_B))

    def test_an_unknown_slot_reports_not_found(self):
        _response, payload = self._restore([self.WORKSPACE_A, "NOT-AN-ID"])

        self.assertEqual(
            [entry["reason"] for entry in payload["workspaces"]],
            ["not_found", "invalid_workspace_id"],
        )

    def test_max_sessions_mid_restore_fails_one_group_not_the_request(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()

        with patch.object(api.runtime_config, "max_sessions", 0):
            response, payload = self._restore(["default"])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["workspaces"][0]["restored"])
        self.assertIn("Maximum", payload["workspaces"][0]["groups"][0]["error"])

    # ── R13 / R14 Forget ──

    def test_r13_forget_is_idempotent_and_preserves_siblings(self):
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        self._launch(session_name="Main")
        self._launch(workspace_id=self.WORKSPACE_B, session_name="Beta work")
        web_runtime_state.capture_live_workspaces(api.session_manager)
        self._close_everything()

        first = self.client.delete(
            "/api/runtime-state", query_string={"workspace_id": self.WORKSPACE_B}
        )
        second = self.client.delete(
            "/api/runtime-state", query_string={"workspace_id": self.WORKSPACE_B}
        )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.get_json()["forgotten"])
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.get_json()["forgotten"])
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    def test_r14_forget_is_refused_while_the_workspace_is_live(self):
        self._launch(session_name="Main")
        self._save_slot()

        response = self.client.delete("/api/runtime-state")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["forgotten"])
        self.assertTrue(response.get_json()["live_conflict"])
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    def test_forget_never_touches_saved_sessions(self):
        preset = self._seed_preset(name="Alpha")
        self._launch(session_name="Alpha", saved_session_id=preset["id"])
        self._save_slot()
        self._close_everything()

        self.client.delete("/api/runtime-state")

        self.assertIn(preset["id"], [entry["id"] for entry in web_saved_sessions.load_saved_sessions()])

    def test_forgetting_the_default_slot_keeps_the_permanent_live_workspace(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()

        response = self.client.delete("/api/runtime-state")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(api.session_manager.get_workspace("default"))

    # ── Emptying `default` forgets its slot ──
    #
    # Every other workspace forgets its shape on losing its last group. The
    # `default` record is permanent, so it is never pruned and its snapshot used
    # to be immortal: a group run there once stayed on offer in the restore
    # chooser forever and came back on the next restore.

    def test_closing_the_last_group_in_default_forgets_its_slot(self):
        launched = self._launch(session_name="Scratch")
        self._save_slot()

        self.client.delete(f"/api/sessions?group={launched['group_id']}")

        self.assertIsNone(web_runtime_state.load_restorable_workspace("default"))
        self.assertIsNotNone(api.session_manager.get_workspace("default"))

    def test_closing_the_last_pane_in_default_forgets_its_slot(self):
        launched = self._launch(session_name="Scratch")
        self._save_slot()
        session_id = launched["sessions"][0]["session_id"]

        self.client.delete(f"/api/sessions/{session_id}")

        self.assertIsNone(web_runtime_state.load_restorable_workspace("default"))

    def test_an_immediate_last_pane_close_forgets_the_default_slot(self):
        """MW-06: an explicit pane close is explicit, grace period or not.

        The group is *not* aged here. It used to survive its own emptying for
        five seconds with nothing scheduled to sweep it afterwards, so closing
        the last pane right after launch left an immortal empty group that kept
        `default` looking occupied and its snapshot unforgettable.
        """
        launched = self._launch(session_name="Scratch")
        self._save_slot()

        response = self.client.delete(
            f"/api/sessions/{launched['sessions'][0]['session_id']}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(api.session_manager.get_group(launched["group_id"]))
        self.assertEqual(api.session_manager.get_workspace_groups("default"), [])
        self.assertIsNone(web_runtime_state.load_restorable_workspace("default"))
        # …and `default` stops being a user-visible workspace, so no ghost
        # destination is left behind (MW-05).
        self.assertEqual(web_workspaces.list_live_workspaces(), [])

    def test_an_immediate_pane_close_keeps_a_group_that_still_has_panes(self):
        """Forcing the owning group must not sweep a group that is still live."""
        launched = self._launch(
            session_name="Scratch",
            sessions=[
                {"directory": str(self.repo_dir), "title": "A", "startup_mode": "explorer"},
                {"directory": str(self.repo_dir), "title": "B", "startup_mode": "explorer"},
            ],
        )
        self._save_slot()

        self.client.delete(f"/api/sessions/{launched['sessions'][0]['session_id']}")

        self.assertIsNotNone(api.session_manager.get_group(launched["group_id"]))
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    def test_closing_one_of_several_groups_in_default_keeps_its_slot(self):
        first = self._launch(session_name="Kept")
        self._launch(session_name="Closed")
        self._save_slot()

        self.client.delete(f"/api/sessions?group={first['group_id']}")

        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    def test_closing_a_sibling_workspace_never_touches_the_default_slot(self):
        """An empty `default` may simply be one this run never used."""
        self._launch(session_name="Saved earlier")
        self._save_slot()
        self._close_everything()
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        launched = self._launch(workspace_id=self.WORKSPACE_B, session_name="Beta work")

        self.client.delete(
            f"/api/sessions?workspace_id={self.WORKSPACE_B}&group={launched['group_id']}"
        )

        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    def test_moving_the_last_group_out_of_default_forgets_its_slot(self):
        launched = self._launch(session_name="Scratch")
        self._save_slot()

        response = self.client.post(
            f"/api/session-groups/{launched['group_id']}/move",
            json={"new_workspace": True, "label": "Elsewhere"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(web_runtime_state.load_restorable_workspace("default"))

    def test_restore_after_restart_still_offers_the_default_slot(self):
        """The promise this narrowing must not break: a teardown that is not an
        explicit per-group close leaves the snapshot on offer."""
        self._launch(session_name="Main")
        self._save_slot()

        self._close_everything()

        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    # ── Slot cap ──

    def test_auto_slot_cap_evicts_oldest_first_and_never_a_manual_slot(self):
        state = {"version": 2, "workspaces": {}}
        for index in range(web_runtime_state.MAX_AUTO_WORKSPACE_SLOTS + 3):
            workspace_id = f"w{index:011d}"
            state["workspaces"][workspace_id] = {
                "workspace_id": workspace_id,
                "label": f"Slot {index}",
                "origin": "manual" if index == 0 else "auto",
                "saved_at": 1000.0 + index,
                "groups": [{"group_id": f"g{index}", "sessions": [{"host": "h"}]}],
            }
        manual_id = "w00000000000"
        oldest_auto = "w00000000001"
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self._launch(session_name="Live")

        web_runtime_state.capture_live_workspaces(api.session_manager)

        stored = json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"]
        auto_count = sum(1 for slot in stored.values() if slot["origin"] == "auto")
        self.assertEqual(auto_count, web_runtime_state.MAX_AUTO_WORKSPACE_SLOTS)
        self.assertIn(manual_id, stored)
        self.assertNotIn(oldest_auto, stored)
        self.assertIn("default", stored)

    # ── Credentials and the shared launch path ──

    def test_restore_never_returns_or_logs_a_credential(self):
        """The password is the one thing a preset still contributes (MW-03)."""
        preset = self._seed_preset(name="Alpha", password="super-secret-pw")
        self._launch_ssh(session_name="Alpha", saved_session_id=preset["id"])
        self._save_slot()
        self._close_everything()

        with patch.object(api.socketio, "start_background_task"), self.assertLogs(
            "web", level="DEBUG"
        ) as logs:
            response, payload = self._restore(["default"])

        body = response.get_data(as_text=True)
        self.assertNotIn("super-secret-pw", body)
        self.assertNotIn("super-secret-pw", "\n".join(logs.output))
        self.assertNotIn("super-secret-pw", self.state_path.read_text(encoding="utf-8"))
        group_result = payload["workspaces"][0]["groups"][0]
        self.assertTrue(group_result["started"])
        self.assertEqual(group_result["warning"], "")
        # The credential still reached the session in-process.
        group_id = f"saved-session-{preset['id']}"
        self.assertEqual(
            api.session_manager.get_group_sessions(group_id)[0].password,
            "super-secret-pw",
        )

    def test_a_preset_pointing_elsewhere_never_lends_its_password(self):
        """MW-03: a credential is matched by target, never by pane position.

        The captured pane keeps the host it was captured with, and the password
        for a machine it no longer names is withheld rather than sent there.
        """
        preset = self._seed_preset(name="Alpha", password="super-secret-pw")
        self._launch_ssh(session_name="Alpha", saved_session_id=preset["id"])
        self._save_slot()
        self._close_everything()
        web_saved_sessions.upsert_saved_session(
            config={
                **preset["config"],
                "ssh": {**preset["config"]["ssh"], "host": "somewhere.else"},
            },
            name="Alpha",
            session_id=preset["id"],
        )

        with patch.object(api.socketio, "start_background_task"):
            _response, payload = self._restore(["default"])

        group_result = payload["workspaces"][0]["groups"][0]
        self.assertTrue(group_result["started"])
        self.assertEqual(group_result["warning"], "credentials_unmatched")
        session = api.session_manager.get_group_sessions(
            f"saved-session-{preset['id']}"
        )[0]
        self.assertEqual(session.host, "preset.example")
        self.assertIsNone(session.password)

    def test_a_deleted_preset_costs_the_credential_not_the_shape(self):
        preset = self._seed_preset(name="Alpha", password="super-secret-pw")
        self._launch_ssh(session_name="Alpha", saved_session_id=preset["id"])
        self._save_slot()
        self._close_everything()
        web_saved_sessions.delete_saved_sessions([preset["id"]])

        with patch.object(api.socketio, "start_background_task"):
            _response, payload = self._restore(["default"])

        group_result = payload["workspaces"][0]["groups"][0]
        self.assertTrue(group_result["started"])
        self.assertEqual(group_result["warning"], "preset_missing")
        session = api.session_manager.get_group_sessions(
            f"saved-session-{preset['id']}"
        )[0]
        self.assertEqual(session.host, "preset.example")
        self.assertIsNone(session.password)

    def test_launch_request_logging_is_redacted(self):
        with self.assertLogs("web.api", level="INFO") as logs:
            self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "wsl",
                    "session_name": "Files",
                    "sessions": [
                        {
                            "directory": str(self.repo_dir),
                            "title": "Files",
                            "startup_mode": "explorer",
                            "password": "never-log-me",
                        }
                    ],
                },
            )

        launch_lines = [line for line in logs.output if "POST /api/sessions" in line]
        self.assertEqual(len(launch_lines), 1)
        self.assertNotIn("never-log-me", launch_lines[0])
        self.assertIn("'credentials_supplied': True", launch_lines[0])

    def test_restore_and_launch_share_one_code_path(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()

        with patch.object(
            web_workspaces, "launch_session_group", wraps=web_workspaces.launch_session_group
        ) as launch:
            self._restore(["default"])

        launch.assert_called_once()
        self.assertTrue(launch.call_args.args[0]["restore"])

    def test_response_reports_a_started_relaunch_not_a_connection(self):
        self._launch(session_name="Main")
        self._save_slot()
        self._close_everything()

        _response, payload = self._restore(["default"])

        self.assertEqual(payload["message"], "Relaunch started")

    def test_launcher_ships_the_restore_chooser_with_three_verbs(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", True):
            html = self.client.get("/").get_data(as_text=True)
        launcher_js = self.client.get("/static/js/launcher.js").get_data(as_text=True)

        # A dialog, not an inline panel: inline, its height reflowed the
        # launcher grid and collapsed the Terminal Setup card.
        self.assertIn('id="workspaceRestoreModal"', html)
        self.assertIn('class="modal-card workspace-restore-card"', html)
        self.assertNotIn('id="workspaceRestorePanel"', html)
        self.assertIn('id="workspaceRestoreSelectedBtn"', html)
        self.assertIn('id="workspaceSavedEntry"', html)
        self.assertIn("onclick=\"dismissWorkspaceRestorePanel()\"", html)
        # Restore and Forget are per row; Dismiss is dialog-level and loses
        # nothing, so the chooser must be reopenable mid-session.
        self.assertIn("async function forgetWorkspaceRow(summary)", launcher_js)
        self.assertIn("function openWorkspaceRestorePanel()", launcher_js)
        # Forgetting is destructive, so it confirms through the shared in-page
        # modal and is marked as such — never a blocked window.confirm
        # (guardrail 4/8).
        self.assertIn("openGenericConfirmModal({", launcher_js)
        self.assertIn("danger: true", launcher_js)
        for blocked in ("window.confirm(", "window.prompt(", "window.alert("):
            self.assertNotIn(blocked, launcher_js)


class RestoreReservationTestCase(unittest.TestCase):
    """MW-08: restore is idempotent under concurrency, not only in sequence.

    `restore_workspace` asked whether the workspace had groups, then separately
    created the record and launched every group. Two requests — a double click,
    two launcher windows, a native window and a browser tab — could both pass
    that check: for a non-default slot the loser hit an uncaught "Workspace
    already exists", and for `default` both proceeded and duplicated the tabs.
    """

    WORKSPACE_B = "bbbbbbbbbbbb"

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, workspace_id="default", session_name="Files"):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": session_name,
                "workspace_id": workspace_id,
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _saved_workspace(self, workspace_id="default"):
        """Launch two groups, capture them, then close everything."""
        if workspace_id != "default":
            api.session_manager.create_workspace("Beta", workspace_id)
        self._launch(workspace_id, "First")
        self._launch(workspace_id, "Second")
        slot = web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=workspace_id
        )
        self.assertIsNotNone(slot)
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()

    def _restore_while_paused(self, workspace_id):
        """Run one restore in a thread, paused inside its first group launch.

        Returns ``(thread, result_box, entered, release)``. The caller races a
        second restore against the paused one and then releases it.
        """
        entered = threading.Event()
        release = threading.Event()
        real_launch = web_workspaces.launch_session_group
        result_box = {}

        def paused_launch(body, *args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5), "the paused launch was never released")
            return real_launch(body, *args, **kwargs)

        def run():
            with patch.object(
                web_workspaces, "launch_session_group", paused_launch
            ):
                result_box["result"] = web_workspaces.restore_workspace(workspace_id)

        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(release.set)
        self.addCleanup(thread.join)
        self.assertTrue(entered.wait(5), "the restore never reached a launch")
        return thread, result_box, release

    def test_a_concurrent_second_restore_of_default_is_refused(self):
        self._saved_workspace("default")
        _thread, first, release = self._restore_while_paused("default")

        # The first restore is mid-flight: its record exists, its groups do
        # not. This is exactly the window the old check-then-act let through.
        second = web_workspaces.restore_workspace("default")

        release.set()
        _thread.join(5)
        self.assertFalse(second["restored"])
        self.assertEqual(second["reason"], "already_restoring")
        self.assertEqual(second["groups"], [])
        self.assertTrue(first["result"]["restored"])
        self.assertEqual(first["result"]["group_count"], 2)
        # One restore, one set of tabs — not two of each.
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 2)

    def test_a_concurrent_second_restore_of_a_named_workspace_is_refused(self):
        """The non-default half: the loser used to raise "Workspace already exists"."""
        self._saved_workspace(self.WORKSPACE_B)
        _thread, first, release = self._restore_while_paused(self.WORKSPACE_B)

        second = web_workspaces.restore_workspace(self.WORKSPACE_B)

        release.set()
        _thread.join(5)
        self.assertEqual(second["reason"], "already_restoring")
        self.assertTrue(first["result"]["restored"])
        self.assertEqual(
            len(api.session_manager.get_workspace_groups(self.WORKSPACE_B)), 2
        )

    def test_the_claim_is_released_after_a_restore_that_started_nothing(self):
        """A failed restore must not leave the workspace permanently claimed."""
        self._saved_workspace("default")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        for group in state["workspaces"]["default"]["groups"]:
            group["sessions"] = []
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        failed = web_workspaces.restore_workspace("default")
        retried = web_workspaces.restore_workspace("default")

        self.assertEqual(failed["reason"], "no_groups_started")
        # Not `already_restoring`: the claim was released on the failure path.
        self.assertEqual(retried["reason"], "no_groups_started")

    def test_the_claim_is_released_when_there_is_no_slot(self):
        self.assertEqual(web_workspaces.restore_workspace("default")["reason"], "not_found")
        self.assertEqual(web_workspaces.restore_workspace("default")["reason"], "not_found")

    def test_a_sequential_second_restore_still_reports_already_live(self):
        """The claim must not mask the plain already-restored answer."""
        self._saved_workspace("default")

        first = web_workspaces.restore_workspace("default")
        second = web_workspaces.restore_workspace("default")

        self.assertTrue(first["restored"])
        self.assertEqual(second["reason"], "already_live")


class SingleWorkspaceRestoreRoutingTestCase(unittest.TestCase):
    """MW-09: one server-owned restore, with `default` as just another id.

    Single-workspace mode used to fetch the raw default slot and loop over
    POST /api/sessions in the browser, re-resolving presets on the way. That
    duplicated the restore policy, could stop half way through a failure, and
    skipped the endpoint's idempotency, its per-group report, and its saved
    label.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, session_name="Files"):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": session_name,
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_the_default_slot_restores_through_the_same_endpoint(self):
        self._launch("Main")
        web_runtime_state.capture_workspace(
            api.session_manager, origin="manual", label="Saved name"
        )
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()

        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )

        self.assertEqual(response.status_code, 200)
        workspace_result = response.get_json()["workspaces"][0]
        self.assertEqual(workspace_result["workspace_id"], "default")
        self.assertTrue(workspace_result["restored"])
        # The saved label is applied by the same path in both modes.
        self.assertEqual(workspace_result["label"], "Saved name")
        self.assertEqual(
            api.session_manager.get_workspace("default").label, "Saved name"
        )
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)

    def test_restoring_into_a_live_default_is_refused_not_duplicated(self):
        """Regression matrix row 10 — the browser loop had no such check."""
        self._launch("Main")
        web_runtime_state.capture_workspace(api.session_manager)

        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )

        workspace_result = response.get_json()["workspaces"][0]
        self.assertFalse(workspace_result["restored"])
        self.assertEqual(workspace_result["reason"], "already_live")
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)

    def test_the_banner_restores_through_the_endpoint_and_respects_live_groups(self):
        """Run the shipped banner code and watch what it actually calls.

        The point of MW-09 is that no restore decision is left in the browser,
        so the check is what the client requests: one call to the server restore
        transaction, and never `/api/sessions` or `/api/saved-sessions`.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        response = self.client.get("/static/js/launcher.js")
        self.assertEqual(response.status_code, 200)
        launcher_js = response.get_data(as_text=True)
        response.close()
        source = "\n".join(
            _js_function_source(launcher_js, name)
            for name in (
                "checkRestorableWorkspace",
                "restorePreviousWorkspace",
                "dismissRestoreBanner",
            )
        )
        script = source + """
let restorableWorkspaceIsOffered = false;
const WORKSPACE_DEFAULT_ID = 'default';
const fetched = [];
const restoreCalls = [];
const opened = [];
let slot = {};

function makeElement() {
    return {
        hidden: true,
        textContent: '',
        disabled: false,
        setAttribute(name, value) { if (name === 'hidden') this.hidden = true; }
    };
}
let elements = {};
const document = { getElementById: id => elements[id] || null };
async function fetch(url) {
    fetched.push(url);
    return { ok: true, json: async () => slot };
}
function isMultiWorkspaceEnabled() { return false; }
async function loadWorkspaceRestoreChooser() { throw new Error('multi-mode chooser'); }
function formatWorkspaceSavedAgo() { return 'just now'; }
const messages = [];
function showMessage(text, kind) { messages.push([text, kind]); }
function normalizeNativeZoomFactor(value) { return value == null ? null : Number(value); }
async function viewActiveTerminals(_event, groupId, zoomFactor) {
    opened.push([groupId, zoomFactor]);
}
let restoreOutcome = {
    workspaces: [{
        workspace_id: 'default',
        restored: true,
        group_count: 2,
        active_group_id: 'live-group-2',
        native_zoom_factor: 1.25,
        groups: [{ started: true }, { started: true }]
    }]
};
async function restoreSavedWorkspaces(workspaceIds) {
    restoreCalls.push(workspaceIds);
    return restoreOutcome;
}

(async () => {
    // A workspace that is already live: restore would only ever be refused,
    // so the banner must not offer it and the action must stay inert.
    elements = { restoreWorkspaceBanner: makeElement(), restoreWorkspaceText: makeElement() };
    slot = { restorable: true, groups: [{ sessions: [{}] }], active_group_count: 2 };
    await checkRestorableWorkspace();
    const liveBannerHidden = elements.restoreWorkspaceBanner.hidden;
    await restorePreviousWorkspace();
    const callsWhileLive = restoreCalls.length;

    // A refused restore keeps the offer and its Retry in front of the user.
    elements = {
        restoreWorkspaceBanner: makeElement(),
        restoreWorkspaceText: makeElement(),
        restoreWorkspaceBtn: makeElement()
    };
    slot = { restorable: true, groups: [{ sessions: [{}] }], active_group_count: 0 };
    restoreOutcome = { workspaces: [{ restored: false, reason: 'already_live' }] };
    await checkRestorableWorkspace();
    await restorePreviousWorkspace();
    const refusedBannerHidden = elements.restoreWorkspaceBanner.hidden;
    const refusedButtonDisabled = elements.restoreWorkspaceBtn.disabled;
    const refusedMessage = messages[messages.length - 1];

    // Nothing live: the banner is offered and replays through the endpoint.
    restoreOutcome = {
        workspaces: [{
            workspace_id: 'default',
            restored: true,
            group_count: 2,
            active_group_id: 'live-group-2',
            native_zoom_factor: 1.25,
            groups: [{ started: true }, { started: true }]
        }]
    };
    elements = {
        restoreWorkspaceBanner: makeElement(),
        restoreWorkspaceText: makeElement(),
        restoreWorkspaceBtn: makeElement()
    };
    slot = {
        restorable: true,
        label: 'Yesterday',
        saved_at: 1,
        active_group_count: 0,
        active_group_id: 'snapshot-group',
        native_zoom_factor: 2,
        groups: [{ sessions: [{}, {}] }, { sessions: [{}] }]
    };
    await checkRestorableWorkspace();
    const offeredBannerHidden = elements.restoreWorkspaceBanner.hidden;
    const bannerText = elements.restoreWorkspaceText.textContent;
    await restorePreviousWorkspace();

    process.stdout.write(JSON.stringify({
        liveBannerHidden,
        callsWhileLive,
        refusedBannerHidden,
        refusedButtonDisabled,
        refusedMessage,
        offeredBannerHidden,
        bannerText,
        fetched,
        restoreCalls,
        opened
    }));
})();
"""
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "restore.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script_path)], capture_output=True, text=True, check=True
            )
        result = json.loads(completed.stdout)

        # Already live: no offer, no request.
        self.assertTrue(result["liveBannerHidden"])
        self.assertEqual(result["callsWhileLive"], 0)
        # Refused: the offer and its re-enabled button stay put, with retry
        # wording on the message (guardrail 8).
        self.assertFalse(result["refusedBannerHidden"])
        self.assertFalse(result["refusedButtonDisabled"])
        self.assertIn("try again", result["refusedMessage"][0])
        self.assertEqual(result["refusedMessage"][1], "error")
        # Offered: the banner names the saved workspace and its shape.
        self.assertFalse(result["offeredBannerHidden"])
        self.assertIn("Yesterday", result["bannerText"])
        self.assertIn("2 sessions (3 panes)", result["bannerText"])
        # One server restore of `default` per attempt, and no client-side
        # orchestration on any of them.
        self.assertEqual(result["restoreCalls"], [["default"], ["default"]])
        self.assertEqual(result["fetched"], ["/api/runtime-state"] * 3)
        for url in result["fetched"]:
            self.assertNotIn("/api/sessions", url)
            self.assertNotIn("/api/saved-sessions", url)
        # The window opens on the group and zoom the *server* resolved, not on
        # a snapshot id the browser tried to map itself.
        self.assertEqual(result["opened"], [["live-group-2", 1.25]])


class DuplicatePresetRestoreTestCase(unittest.TestCase):
    """MW-13: two retained slots referencing one preset get one honest answer.

    A saved preset is live in at most one workspace at a time — that rule is
    what makes relaunching a preset replace its own tab instead of opening a
    second one. Slots are deliberately retained by several close paths, so two
    of them can end up referencing the same preset, and restoring both used to
    mean "whichever the request listed first wins": the second workspace came
    back silently missing that tab.
    """

    WORKSPACE_A = "aaaaaaaaaaaa"
    WORKSPACE_B = "bbbbbbbbbbbb"

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        for target, attribute, value in (
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_sessions_path)),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _preset(self, name):
        return web_saved_sessions.upsert_saved_session(
            config={
                "connection_mode": "wsl",
                "terminal_count": 1,
                "layout": "single",
                "wsl": {"default_dir": str(self.repo_dir)},
                "terminals": [{"title": "Files", "startup_mode": "explorer"}],
            },
            name=name,
        )

    def _launch(self, workspace_id, saved_session_id="", session_name="Files"):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": session_name,
                "workspace_id": workspace_id,
                "saved_session_id": saved_session_id,
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _capture_and_close(self, workspace_id):
        slot = web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=workspace_id
        )
        self.assertIsNotNone(slot)
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()

    def _two_slots_sharing_one_preset(self):
        preset = self._preset("Alpha")
        api.session_manager.create_workspace("A", self.WORKSPACE_A)
        self._launch(self.WORKSPACE_A, preset["id"], "Alpha")
        self._capture_and_close(self.WORKSPACE_A)
        api.session_manager.create_workspace("B", self.WORKSPACE_B)
        self._launch(self.WORKSPACE_B, preset["id"], "Alpha")
        # A second, unattached tab so workspace B is not *only* the shared one.
        self._launch(self.WORKSPACE_B, session_name="Plain")
        self._capture_and_close(self.WORKSPACE_B)
        return preset

    def _restore(self, workspace_ids):
        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": workspace_ids}
        )
        return response, response.get_json()

    def test_restoring_both_slots_is_refused_before_anything_launches(self):
        preset = self._two_slots_sharing_one_preset()

        response, payload = self._restore([self.WORKSPACE_A, self.WORKSPACE_B])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["conflict"], "duplicate_preset_reference")
        self.assertEqual(payload["restored_count"], 0)
        self.assertEqual(payload["workspaces"], [])
        conflict = payload["conflicts"][0]
        self.assertEqual(conflict["saved_session_id"], preset["id"])
        self.assertEqual(conflict["saved_session_name"], "Alpha")
        self.assertEqual(
            sorted(conflict["workspace_ids"]),
            sorted([self.WORKSPACE_A, self.WORKSPACE_B]),
        )
        # Nothing was launched: not the shared tab, and not workspace B's
        # unattached one either. A partial restore is what this replaces.
        self.assertEqual(api.session_manager.get_session_count(), 0)
        self.assertEqual(web_workspaces.list_live_workspaces(), [])
        # Both slots are intact and still on offer.
        for workspace_id in (self.WORKSPACE_A, self.WORKSPACE_B):
            self.assertIsNotNone(
                web_runtime_state.load_restorable_workspace(workspace_id)
            )

    def test_choosing_one_of_them_restores_it_completely(self):
        """Deselecting one side is the explicit choice the 409 asks for."""
        self._two_slots_sharing_one_preset()

        _response, payload = self._restore([self.WORKSPACE_B])

        workspace_result = payload["workspaces"][0]
        self.assertTrue(workspace_result["restored"])
        self.assertEqual(workspace_result["group_count"], 2)
        self.assertEqual(
            len(api.session_manager.get_workspace_groups(self.WORKSPACE_B)), 2
        )
        # The other slot is untouched and still restorable later.
        self.assertIsNotNone(
            web_runtime_state.load_restorable_workspace(self.WORKSPACE_A)
        )

    def test_one_slot_naming_a_preset_twice_is_refused_too(self):
        """Hand-edited state: two groups in one slot would collapse into one."""
        preset = self._preset("Alpha")
        self._launch("default", preset["id"], "Alpha")
        self._launch("default", session_name="Plain")
        self._capture_and_close("default")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        groups = state["workspaces"]["default"]["groups"]
        groups[1]["saved_session_id"] = preset["id"]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        response, payload = self._restore(["default"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["conflicts"][0]["workspace_ids"], ["default", "default"])
        self.assertEqual(api.session_manager.get_session_count(), 0)

    def test_a_preset_live_outside_the_selection_still_skips_only_its_group(self):
        """The incumbent case is different and deliberately stays partial.

        A preset already open in a workspace the request does not name is
        visible to the user and cannot be affected by this restore, so its one
        group is reported as skipped and the rest of the workspace comes back.
        """
        preset = self._preset("Alpha")
        api.session_manager.create_workspace("B", self.WORKSPACE_B)
        self._launch(self.WORKSPACE_B, preset["id"], "Alpha")
        self._launch(self.WORKSPACE_B, session_name="Plain")
        self._capture_and_close(self.WORKSPACE_B)
        self._launch("default", preset["id"], "Alpha")

        _response, payload = self._restore([self.WORKSPACE_B])

        workspace_result = payload["workspaces"][0]
        self.assertTrue(workspace_result["restored"])
        skipped = [group for group in workspace_result["groups"] if group.get("skipped")]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["skipped"], "already_live")
        self.assertEqual(skipped[0]["workspace_id"], "default")
        self.assertEqual(workspace_result["group_count"], 1)


class MultiWorkspaceModeToggleTestCase(unittest.TestCase):
    """Turning the mode on and off from the launcher, and keeping windows in sync.

    The flag was wired end-to-end but had no control: it could only be changed by
    editing config.json and restarting. It became an App Settings checkbox, which
    hid a feature that changes what every launch does, so the control is now the
    switch in the launcher's Workspaces card. It still applies immediately, and
    switching it off has to leave nothing running that no window can reach.
    """

    WORKSPACE_A = "aaaaaaaaaaaa"

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        return self.client.post("/api/sessions", json=body)

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    # ── The launcher control ──

    def test_the_launcher_ships_the_mode_switch_wired_end_to_end(self):
        html = self.client.get("/").get_data(as_text=True)
        launcher_js = self._static("js/launcher.js")
        workspaces_js = self._static("js/workspaces.js")

        # Guardrail 5: a config key must be wired page-to-backend, not half-added.
        self.assertIn('id="multiWorkspaceToggle"', html)
        self.assertIn('onclick="toggleMultiWorkspaceMode()"', html)
        self.assertIn('role="switch"', html)
        self.assertIn("async function toggleMultiWorkspaceMode()", launcher_js)
        self.assertIn("async function setMultiWorkspaceEnabled(enabled", workspaces_js)
        self.assertIn(
            "JSON.stringify({ workspace: { multi_workspace_enabled: next } })",
            workspaces_js,
        )
        self.assertIn(
            "multi_workspace_enabled",
            self.client.get("/api/app-config").get_json()["workspace"],
        )

    def test_the_switch_is_visible_before_the_mode_is_on(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", False):
            html = self.client.get("/").get_data(as_text=True)

        # The point of moving it out of App Settings: the way in must be on
        # screen while the mode is still off. The live-workspace list stays
        # server-gated — there is nothing to list yet.
        self.assertIn('id="multiWorkspaceToggle"', html)
        self.assertIn('id="workspaceDestinationCard"', html)
        self.assertNotIn('id="workspaceLiveList"', html)

    def test_app_settings_no_longer_owns_the_flag(self):
        html = self.client.get("/").get_data(as_text=True)
        app_settings_js = self._static("js/app-settings.js")

        # One surface only (guardrail 6). The dialog must also stop *sending*
        # the key, so saving an unrelated setting cannot move the mode: the
        # backend keeps whatever a payload omits.
        self.assertNotIn('id="appMultiWorkspaceEnabled"', html)
        self.assertNotIn("appMultiWorkspaceEnabled", app_settings_js)
        self.assertNotIn("confirmMultiWorkspaceDisable()", app_settings_js)
        collect = app_settings_js[
            app_settings_js.index("function collectAppSettingsForm()"):
            app_settings_js.index("function notifyAppConfigUpdated(appSettings")
        ]
        # No `multi_workspace_enabled:` key in the object this dialog POSTs.
        self.assertNotIn("multi_workspace_enabled:", collect)
        # The broadcast still carries the flag: that is how the other windows
        # learn the mode changed at all.
        self.assertIn(
            "multi_workspace_enabled: Boolean(appSettings?.workspace?.multi_workspace_enabled)",
            app_settings_js,
        )

    def test_saving_an_unrelated_setting_keeps_the_mode(self):
        with patch.object(api, "save_config"), \
                patch.object(api, "_refresh_runtime_config"), \
                patch.object(api, "socketio"), \
                patch.object(api.runtime_config, "multi_workspace_enabled", True):
            response = self.client.post(
                "/api/app-config", json={"workspace": {"surface_mode": "max"}}
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["workspace"]["multi_workspace_enabled"])

    def test_the_switch_confirms_before_it_closes_live_workspaces(self):
        workspaces_js = self._static("js/workspaces.js")
        launcher_js = self._static("js/launcher.js")

        # Guardrail 8: an irreversible action confirms in page, and the busy
        # state is a class rather than rewritten button markup.
        setter = workspaces_js[
            workspaces_js.index("async function setMultiWorkspaceEnabled(enabled"):
            workspaces_js.index("/* ── Saved-workspace snapshots")
        ]
        self.assertIn("await confirmMultiWorkspaceDisable()", setter)
        self.assertIn("notifyAppConfigUpdated(data)", setter)
        self.assertIn("await applyMultiWorkspaceFlagChange(next", setter)
        self.assertIn("toggle?.classList.add('is-busy')", launcher_js)

    def test_saving_the_toggle_persists_and_broadcasts_it(self):
        with patch.object(api, "save_config") as save_config, \
                patch.object(api, "_refresh_runtime_config"), \
                patch.object(api, "socketio") as socketio:
            response = self.client.post(
                "/api/app-config", json={"workspace": {"multi_workspace_enabled": True}}
            )

        self.assertEqual(response.status_code, 200)
        saved = save_config.call_args[0][0]
        self.assertTrue(saved["workspace"]["multi_workspace_enabled"])
        # Open windows render the mode from server-side markup, so they only
        # learn about a change if the broadcast carries the flag.
        event, payload = socketio.emit.call_args[0][:2]
        self.assertEqual(event, "app_config_updated")
        self.assertIn("multi_workspace_enabled", payload["workspace"])

    def test_a_non_boolean_toggle_value_keeps_the_current_setting(self):
        with patch.object(api.runtime_config, "multi_workspace_enabled", True):
            normalized = api._normalize_app_config_update(
                {"workspace": {"multi_workspace_enabled": "yes"}}
            )

        self.assertTrue(normalized["workspace"]["multi_workspace_enabled"])

    # ── Leaving the mode ──

    def test_leaving_the_mode_closes_every_workspace_but_default(self):
        kept = self._launch(session_name="Main")
        extra = self._launch(session_name="Side", new_workspace=True, workspace_label="Side")
        self.assertEqual(extra.status_code, 201)
        extra_workspace_id = extra.get_json()["workspace_id"]
        extra_session_ids = [
            session["session_id"] for session in extra.get_json()["sessions"]
        ]

        response = self.client.post("/api/workspaces/close-extra")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["closed_count"], 1)
        self.assertEqual(payload["closed"][0]["workspace_id"], extra_workspace_id)
        self.assertEqual(payload["closed"][0]["label"], "Side")
        # Gone entirely — record, group and sessions — so nothing is left
        # running that no window can reach with the flag off.
        self.assertIsNone(api.session_manager.get_workspace(extra_workspace_id))
        for session_id in extra_session_ids:
            self.assertIsNone(api.session_manager.get_session(session_id))
        # The default workspace is permanent and keeps its tabs.
        self.assertEqual(
            [group.group_id for group in api.session_manager.get_workspace_groups("default")],
            [kept.get_json()["group_id"]],
        )

    def test_leaving_the_mode_also_closes_a_deliberately_empty_workspace(self):
        created = self.client.post("/api/workspaces", json={"label": "Empty"}).get_json()
        self.assertTrue(created["retain_when_empty"])

        self.client.post("/api/workspaces/close-extra")

        # retain_when_empty is a promise for the lifetime of the mode, not past it.
        self.assertIsNone(api.session_manager.get_workspace(created["workspace_id"]))

    def test_leaving_the_mode_notifies_only_the_closed_workspaces_rooms(self):
        extra = self._launch(new_workspace=True)
        extra_workspace_id = extra.get_json()["workspace_id"]

        with patch.object(web_terminal_io, "socketio") as socketio:
            self.client.post("/api/workspaces/close-extra")

        rooms = [
            call.kwargs.get("room")
            for call in socketio.emit.call_args_list
            if call.args and call.args[0] == "session_groups_updated"
        ]
        self.assertEqual(rooms, [f"workspace:{extra_workspace_id}"])

    def test_leaving_the_mode_is_idempotent_and_never_touches_saved_slots(self):
        self._launch(new_workspace=True)
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id="default", origin="manual"
        )
        self._launch(session_name="Main")
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id="default", origin="manual"
        )
        before = self.state_path.read_bytes()

        first = self.client.post("/api/workspaces/close-extra")
        second = self.client.post("/api/workspaces/close-extra")

        self.assertEqual(first.get_json()["closed_count"], 1)
        self.assertEqual(second.get_json()["closed_count"], 0)
        # A close writes no snapshot: restorability comes from what autosave or
        # an explicit Save Workspace already captured, which stays byte-identical.
        self.assertEqual(self.state_path.read_bytes(), before)

    # ── Cross-window freshness ──

    def test_the_launcher_refreshes_its_workspace_lists_without_polling(self):
        workspaces_js = self._static("js/workspaces.js")
        launcher_js = self._static("js/launcher.js")
        terminals_js = self._static("js/terminals.js")

        # The launcher has no socket, so terminal windows relay every change
        # that alters a workspace list (guardrail 3: push, never poll).
        self.assertIn("function notifyWorkspacesChanged(reason = '')", workspaces_js)
        self.assertIn("function onWorkspacesChanged(handler)", workspaces_js)
        self.assertIn("notifyWorkspacesChanged(message?.reason", terminals_js)
        self.assertIn("notifyWorkspacesChanged('renamed')", terminals_js)
        self.assertIn("onWorkspacesChanged(() => {", launcher_js)
        self.assertNotIn("setInterval", workspaces_js)

    def test_a_window_ignores_the_broadcast_it_sent_itself(self):
        shared_js = self._static("js/shared.js")
        launcher_js = self._static("js/launcher.js")
        terminals_js = self._static("js/terminals.js")
        workspaces_js = self._static("js/workspaces.js")

        # A BroadcastChannel does deliver to other channel objects in the same
        # document, so a sender sees its own message. Reacting to it reloaded
        # the sending window mid-teardown and left workspaces running.
        self.assertIn("function isOwnBroadcast(message)", shared_js)
        self.assertIn("source: GRIDVIBE_WINDOW_ID", workspaces_js)
        for source in (launcher_js, terminals_js, workspaces_js):
            self.assertIn("isOwnBroadcast(event.data)", source)

    def test_the_workspace_window_names_its_own_workspace(self):
        api.session_manager.rename_workspace("default", "Reviews")

        html = self.client.get("/terminals").get_data(as_text=True)
        terminals_js = self._static("js/terminals.js")

        # Nothing else on screen says which workspace a window is once two are
        # open, so the label leads the session line and the window title.
        self.assertIn('const CURRENT_WORKSPACE_LABEL = "Reviews";', html)
        self.assertIn("`(${currentWorkspaceDisplayLabel()})`", terminals_js)
        self.assertIn("function setCurrentWorkspaceLabel(label)", terminals_js)


class WorkspaceEmptiedRemovalTestCase(unittest.TestCase):
    """Emptying a non-default workspace removes it globally, snapshot included.

    Pruning the live record while the saved slot survived made the launcher
    contradict itself: the workspace was gone from the Workspaces card and still
    offered by *Reopen saved…*, and reopening it resurrected a workspace the user
    had just emptied.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def _saved_slot_ids(self):
        return set(json.loads(self.state_path.read_text("utf-8"))["workspaces"])

    def _capture(self, workspace_id):
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id=workspace_id, origin="manual"
        )

    def test_closing_the_last_group_forgets_the_workspaces_snapshot(self):
        launched = self._launch(new_workspace=True, workspace_label="Offscreen")
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)
        self.assertIn(workspace_id, self._saved_slot_ids())

        response = self.client.delete(
            f"/api/sessions?group={launched['group_id']}"
            f"&workspace_id={workspace_id}"
        )

        self.assertEqual(response.status_code, 200)
        # Gone from both lists, not one: no live record, no restorable slot.
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertNotIn(workspace_id, self._saved_slot_ids())
        self.assertEqual(
            [
                summary["workspace_id"]
                for summary in web_workspaces.list_restorable_workspace_summaries()
            ],
            [],
        )

    def test_closing_the_last_session_of_the_last_group_forgets_it_too(self):
        launched = self._launch(new_workspace=True)
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)
        session_id = launched["sessions"][0]["session_id"]
        # Not aged: an explicit pane close forces its own group through cleanup
        # (MW-06), so this works immediately after launch too.

        response = self.client.delete(f"/api/sessions/{session_id}")

        self.assertEqual(response.status_code, 200)
        # Both close paths prune through the same helper, so both forget.
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertNotIn(workspace_id, self._saved_slot_ids())

    def test_moving_the_last_group_out_forgets_the_source_snapshot(self):
        self._launch(session_name="Main")
        self._capture("default")
        source = self._launch(new_workspace=True, workspace_label="Source")
        source_workspace_id = source["workspace_id"]
        self._capture(source_workspace_id)
        self.assertEqual(
            self._saved_slot_ids(), {"default", source_workspace_id}
        )

        response = self.client.post(
            f"/api/session-groups/{source['group_id']}/move",
            json={"target_workspace_id": "default"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["source_workspace_pruned"])
        self.assertIsNone(api.session_manager.get_workspace(source_workspace_id))
        # Only the emptied source is forgotten; the destination keeps its slot.
        self.assertEqual(self._saved_slot_ids(), {"default"})

    def test_the_permanent_default_workspace_forgets_its_emptied_snapshot(self):
        launched = self._launch()
        self.assertEqual(launched["workspace_id"], "default")
        self._capture("default")

        self.client.delete(f"/api/sessions?group={launched['group_id']}")

        # The *record* is permanent — "default" is never pruned — but the
        # snapshot follows the same rule as every sibling's: closing the last
        # group forgets the shape, or the chooser would keep offering a
        # workspace the user just emptied. Restore-after-restart is unaffected:
        # a process exit never reaches this path.
        self.assertIsNotNone(api.session_manager.get_workspace("default"))
        self.assertNotIn("default", self._saved_slot_ids())

    def test_a_failed_restore_rollback_keeps_the_slot_restorable(self):
        launched = self._launch(new_workspace=True)
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)
        self.client.delete(
            f"/api/sessions?group={launched['group_id']}"
            f"&workspace_id={workspace_id}"
        )
        # The close above forgot it; write a slot back that cannot start.
        state = json.loads(self.state_path.read_text("utf-8"))
        state["workspaces"][workspace_id] = {
            "label": "Broken",
            "origin": "manual",
            "saved_at": time.time(),
            "groups": [{"group_id": "gone", "sessions": []}],
            "active_group_id": "",
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        result = web_workspaces.restore_workspace(workspace_id)

        # Rolling back a restore that started nothing removes the *live* record
        # it just created — it must never forget the snapshot it failed to open.
        self.assertFalse(result["restored"])
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertIn(workspace_id, self._saved_slot_ids())

    def test_the_window_announces_the_removal_before_it_closes(self):
        response = self.client.get("/static/js/terminals.js")
        terminals_js = response.get_data(as_text=True)
        response.close()

        # The room event that would normally relay this races the window
        # teardown, so the emptied window announces the change itself first.
        self.assertIn("notifyWorkspacesChanged('workspace_emptied');", terminals_js)


class WorkspaceVisibilityTestCase(unittest.TestCase):
    """MW-05: one predicate decides what counts as a user-visible workspace.

    `SessionManager` refuses to remove the permanent `default` record, and every
    live-workspace list used to render every record. After the final default
    group closed, the Workspaces card (which filtered to populated workspaces)
    showed nothing while the launch-destination menu still offered an empty
    "Main workspace — 0 sessions": an internal backend container presented as a
    user workspace, and two lists disagreeing about the same state.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _listed_ids(self):
        payload = self.client.get("/api/workspaces").get_json()
        return [workspace["workspace_id"] for workspace in payload["workspaces"]]

    def test_an_emptied_default_leaves_no_ghost_destination(self):
        launched = self._launch()
        self.assertEqual(self._listed_ids(), ["default"])

        self.client.delete(f"/api/sessions?group={launched['group_id']}")

        # Gone from the list every destination menu and card reads …
        self.assertEqual(self._listed_ids(), [])
        self.assertEqual(self.client.get("/api/workspaces").get_json()["count"], 0)
        # … while the internal container itself is untouched, so a launch that
        # names `default` (every single-workspace-mode launch) still works.
        self.assertIsNotNone(api.session_manager.get_workspace("default"))
        self.assertEqual(
            [
                workspace["workspace_id"]
                for workspace in web_workspaces.list_live_workspaces(include_hidden=True)
            ],
            ["default"],
        )
        self.assertEqual(self._launch()["workspace_id"], "default")

    def test_a_launch_in_flight_is_not_yet_a_workspace(self):
        """A destination between creation and its first group is not a workspace.

        `resolve_launch_destination` creates the record before the group exists,
        so listing every record briefly advertised a launch as a destination.
        """
        in_flight = api.session_manager.create_workspace(label="Half launched")

        self.assertNotIn(in_flight.workspace_id, self._listed_ids())
        self.assertFalse(
            web_workspaces.workspace_is_user_visible(in_flight, group_count=0)
        )
        self.assertTrue(
            web_workspaces.workspace_is_user_visible(in_flight, group_count=1)
        )

    def test_a_deliberately_empty_workspace_is_a_real_destination(self):
        """Workspace ▸ New Workspace opens a window, so its record is visible."""
        created = self.client.post("/api/workspaces", json={"label": "Scratch"})
        workspace_id = created.get_json()["workspace_id"]

        listed = self.client.get("/api/workspaces").get_json()["workspaces"]

        self.assertIn(workspace_id, [item["workspace_id"] for item in listed])
        entry = next(item for item in listed if item["workspace_id"] == workspace_id)
        self.assertEqual(entry["group_count"], 0)
        self.assertTrue(entry["retain_when_empty"])

    def test_the_server_and_client_predicates_agree(self):
        """The two halves of the one predicate must not drift apart.

        The API filters server-side; the launcher, the Workspaces card, the
        Open/Move menus and the Alt+W cycle re-apply `isUserVisibleWorkspace`
        to their cached summaries. Running the shipped JS against the same
        fixtures is what keeps "one predicate" true across the boundary.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        fixtures = [
            {"workspace_id": "default", "group_count": 0, "retain_when_empty": False},
            {"workspace_id": "default", "group_count": 2, "retain_when_empty": False},
            {"workspace_id": "aaaaaaaaaaaa", "group_count": 0, "retain_when_empty": False},
            {"workspace_id": "bbbbbbbbbbbb", "group_count": 0, "retain_when_empty": True},
            {"workspace_id": "cccccccccccc", "group_count": 3, "retain_when_empty": True},
        ]
        server = [
            web_workspaces.workspace_is_user_visible(
                SimpleNamespace(**fixture), fixture["group_count"]
            )
            for fixture in fixtures
        ]

        response = self.client.get("/static/js/workspaces.js")
        self.assertEqual(response.status_code, 200)
        workspaces_js = response.get_data(as_text=True)
        response.close()
        source = _js_function_source(workspaces_js, "isUserVisibleWorkspace")
        script = (
            f"{source}\n"
            "process.stdout.write(JSON.stringify(\n"
            "    JSON.parse(process.argv[2]).map(isUserVisibleWorkspace)\n"
            "));\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "visible.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script_path), json.dumps(fixtures)],
                capture_output=True,
                text=True,
                check=True,
            )

        self.assertEqual(server, [False, True, False, True, True])
        self.assertEqual(json.loads(completed.stdout), server)


class WorkspaceCloseActionMatrixTestCase(unittest.TestCase):
    """MW-15/MW-12: each close verb has one documented persistence effect.

    The matrix lives in `web/workspaces.py`. Closing a *window* changes nothing
    live; closing the last *group* removes the workspace and its snapshot;
    *Close live workspace* ends the sessions but leaves the snapshot on offer;
    *Close and forget* removes both. Bulk close-all is the process-wide window
    close and deliberately keeps every snapshot.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _capture(self, workspace_id):
        self.assertIsNotNone(
            web_runtime_state.capture_workspace(
                api.session_manager, workspace_id=workspace_id, origin="manual"
            )
        )

    # ── Close live workspace ──

    def test_close_live_workspace_ends_the_sessions_and_keeps_the_slot(self):
        launched = self._launch(new_workspace=True, workspace_label="Alpha")
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)

        response = self.client.delete(f"/api/workspaces/{workspace_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["closed"])
        self.assertTrue(payload["removed"])
        self.assertFalse(payload["forgotten"])
        self.assertEqual(payload["group_count"], 1)
        self.assertEqual(payload["session_count"], 1)
        # Live: gone entirely, sessions included.
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertIsNone(api.session_manager.get_group(launched["group_id"]))
        self.assertEqual(api.session_manager.get_session_count(), 0)
        # Persisted: this is the restorable verb, unlike closing the last tab.
        self.assertIsNotNone(
            web_runtime_state.load_restorable_workspace(workspace_id)
        )

    def test_close_and_forget_removes_the_slot_too(self):
        launched = self._launch(new_workspace=True, workspace_label="Alpha")
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)

        response = self.client.delete(f"/api/workspaces/{workspace_id}?forget=true")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["forgotten"])
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertIsNone(web_runtime_state.load_restorable_workspace(workspace_id))

    def test_closing_default_empties_it_without_removing_the_container(self):
        launched = self._launch(session_name="Scratch")
        self._capture("default")

        response = self.client.delete("/api/workspaces/default")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["removed"])
        # The record is permanent, but an empty one is not a workspace, so it
        # leaves no ghost destination either (MW-05).
        self.assertIsNotNone(api.session_manager.get_workspace("default"))
        self.assertIsNone(api.session_manager.get_group(launched["group_id"]))
        self.assertEqual(web_workspaces.list_live_workspaces(), [])
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))

    def test_closing_a_workspace_that_is_not_live_reports_it_missing(self):
        unknown = self.client.delete("/api/workspaces/aaaaaaaaaaaa")
        malformed = self.client.delete("/api/workspaces/NOT-AN-ID")

        self.assertEqual(unknown.status_code, 404)
        self.assertTrue(unknown.get_json()["workspace_missing"])
        self.assertEqual(malformed.status_code, 400)

    def test_a_failed_forget_still_reports_the_close_and_stays_retryable(self):
        """The live half already happened, so it is never reported as a no-op."""
        launched = self._launch(new_workspace=True, workspace_label="Alpha")
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)

        with patch.object(
            web_runtime_state,
            "clear_workspace",
            side_effect=web_runtime_state.RuntimeStatePersistenceError("disk full"),
        ):
            response = self.client.delete(f"/api/workspaces/{workspace_id}?forget=true")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertTrue(payload["closed"])
        self.assertFalse(payload["forgotten"])
        self.assertTrue(payload["retryable"])
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        # The slot survived the failure, so the retry has something to remove.
        self.assertIsNotNone(
            web_runtime_state.load_restorable_workspace(workspace_id)
        )

    # ── The verbs it must stay distinct from ──

    def test_closing_the_last_group_still_forgets_the_slot(self):
        launched = self._launch(new_workspace=True, workspace_label="Alpha")
        workspace_id = launched["workspace_id"]
        self._capture(workspace_id)

        response = self.client.delete(
            f"/api/sessions?workspace_id={workspace_id}&group={launched['group_id']}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertIsNone(web_runtime_state.load_restorable_workspace(workspace_id))

    def test_bulk_close_all_sessions_keeps_every_snapshot(self):
        """The process-wide window close: shells end, restore stays possible."""
        self._launch(session_name="Main")
        alpha = self._launch(new_workspace=True, workspace_label="Alpha")
        self._capture("default")
        self._capture(alpha["workspace_id"])

        response = self.client.delete("/api/sessions")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(web_runtime_state.load_restorable_workspace("default"))
        self.assertIsNotNone(
            web_runtime_state.load_restorable_workspace(alpha["workspace_id"])
        )

    # ── The terminal lifecycle of a deliberately empty workspace (MW-12) ──

    def test_an_abandoned_empty_workspace_can_be_deleted(self):
        created = self.client.post("/api/workspaces", json={"label": "Scratch"})
        workspace_id = created.get_json()["workspace_id"]
        # Cleanup deliberately keeps it: that is what made it immortal before.
        api.session_manager.clear_disconnected_sessions()
        self.assertIsNotNone(api.session_manager.get_workspace(workspace_id))

        response = self.client.delete(f"/api/workspaces/{workspace_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["removed"])
        self.assertEqual(payload["group_count"], 0)
        self.assertEqual(payload["session_count"], 0)
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertEqual(web_workspaces.list_live_workspaces(), [])

    def test_deleting_an_empty_workspace_twice_is_not_an_error_state(self):
        created = self.client.post("/api/workspaces", json={})
        workspace_id = created.get_json()["workspace_id"]

        first = self.client.delete(f"/api/workspaces/{workspace_id}")
        second = self.client.delete(f"/api/workspaces/{workspace_id}")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)
        self.assertTrue(second.get_json()["workspace_missing"])

    def test_a_failed_launch_leaves_no_empty_destination_behind(self):
        before = {
            workspace.workspace_id
            for workspace in api.session_manager.get_all_workspaces()
        }

        response = self.client.post(
            "/api/sessions",
            json={"connection_mode": "wsl", "new_workspace": True, "sessions": []},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            {
                workspace.workspace_id
                for workspace in api.session_manager.get_all_workspaces()
            },
            before,
        )

    def test_an_empty_workspace_does_not_survive_a_restart(self):
        """Live records are in-memory only, so a restart is its other terminus."""
        created = self.client.post("/api/workspaces", json={"label": "Scratch"})
        workspace_id = created.get_json()["workspace_id"]

        api.session_manager.reset_sessions()

        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        self.assertEqual(web_workspaces.list_live_workspaces(), [])


class WorkspaceGoneWindowTestCase(unittest.TestCase):
    """A window whose workspace disappeared closes instead of erroring.

    Emptying a non-default workspace removes it globally, but the window that
    emptied it stayed open on "Load error: Workspace not found" over a stale tab
    list, with no way back (``docs/images/zombie_workspace_window.png``). Every
    workspace-scoped read now marks that case machine-readably so the window can
    tell "your workspace is gone" from any other failed request.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": "Files",
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201)
        return response.get_json()

    def _emptied_workspace_id(self):
        launched = self._launch(new_workspace=True, workspace_label="Offscreen")
        workspace_id = launched["workspace_id"]
        self.assertEqual(
            self.client.delete(
                f"/api/sessions?group={launched['group_id']}"
                f"&workspace_id={workspace_id}"
            ).status_code,
            200,
        )
        self.assertIsNone(api.session_manager.get_workspace(workspace_id))
        return workspace_id, launched["group_id"]

    def test_every_workspace_read_marks_a_removed_workspace(self):
        workspace_id, group_id = self._emptied_workspace_id()

        requests = [
            self.client.get(f"/api/session-groups?workspace_id={workspace_id}"),
            self.client.get(f"/api/sessions?workspace_id={workspace_id}"),
            self.client.post(
                "/api/session-groups/order",
                json={"workspace_id": workspace_id, "group_ids": [group_id]},
            ),
            self.client.post(
                "/api/runtime-state/save", json={"workspace_id": workspace_id}
            ),
        ]

        for response in requests:
            self.assertEqual(response.status_code, 400)
            payload = response.get_json()
            self.assertEqual(payload["error"], "Workspace not found")
            self.assertTrue(payload["workspace_missing"])

    def test_another_failure_is_not_reported_as_a_missing_workspace(self):
        launched = self._launch(new_workspace=True)

        # A stale group id from a sibling workspace is a recoverable mismatch —
        # the window reloads its own tabs. Only a gone workspace closes it.
        response = self.client.get(
            f"/api/sessions?workspace_id=default&group={launched['group_id']}"
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error"], "Session group does not belong to workspace")
        self.assertNotIn("workspace_missing", payload)

    def test_the_window_closes_itself_when_its_workspace_is_gone(self):
        response = self.client.get("/static/js/terminals.js")
        terminals_js = response.get_data(as_text=True)
        response.close()

        # One detection point: every refresh path (close, room event, fallback
        # poll, initialLoad) reaches the tab list before anything else.
        self.assertIn("if (data?.workspace_missing) {", terminals_js)
        self.assertIn("await handleWorkspaceGone();", terminals_js)
        self.assertIn(
            "await _closeWindowAfterLastSession('Workspace no longer exists');",
            terminals_js,
        )
        # A window that cannot close itself (a hand-opened browser tab) must not
        # keep re-reading a workspace that will never come back.
        self.assertIn("if (statusRefreshTimer || workspaceGone) return;", terminals_js)
        self.assertIn("if (workspaceGone) return;", terminals_js)


class MultiWorkspaceDialogChromeTestCase(unittest.TestCase):
    """Dialog layering and the launcher/window chrome corrections."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path):
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def test_confirm_and_name_dialogs_lead_the_dialog_stack(self):
        workspaces_css = self._static("css/workspaces.css")

        # Both are the answer to a question another dialog asked (Forget from
        # the restore chooser, the mode toggle from App Settings), and both sit
        # earlier in the document than the dialog asking it — so they were
        # painted behind it and could not be clicked. One rule, loaded last by
        # both pages, puts them above every other shell (App Settings is 12000).
        self.assertIn("#genericConfirmModal,\n#workspaceNameModal {", workspaces_css)
        self.assertIn("z-index: 12100;", workspaces_css)

    def test_view_active_terminals_is_hidden_in_multi_workspace_mode(self):
        launcher_js = self._static("js/launcher.js")

        # It could only ever name one of the live workspaces, right beside the
        # Workspaces card that lists them all with their own Open buttons.
        self.assertIn("viewButton.hidden = enabled;", launcher_js)
        self.assertNotIn("`View ${workspaceDestinationName(target)}`", launcher_js)
        # `.secondary-link` sets `display: inline-flex`, an author rule that
        # beats the user agent's `[hidden] { display: none }` — without this the
        # button stays on screen with the attribute set.
        self.assertIn(
            ".secondary-link[hidden] {\n            display: none;\n        }",
            self._static("css/launcher.css"),
        )

    def test_the_save_confirmation_hands_the_session_line_back(self):
        terminals_js = self._static("js/terminals.js")

        # The confirmation borrows the session line, which is otherwise only
        # rewritten on a tab switch — with a single tab it never was, so the
        # message stayed in the window chrome for the rest of the session.
        self.assertIn("function clearWorkspaceSaveMessage()", terminals_js)
        self.assertIn("clearWorkspaceSaveMessage,\n            WORKSPACE_SAVE_MESSAGE_MS", terminals_js)
        # One function writes the line in both of its shapes, so the deferred
        # restore can never disagree with the grid that is actually on screen.
        self.assertIn("function renderSessionLine()", terminals_js)

    def test_an_unavailable_menu_item_does_not_look_busy_or_clickable(self):
        terminals_css = self._static("css/terminals.css")

        # The checked current workspace under Open Workspace is disabled for
        # good — an hourglass cursor read as "wait, it is loading", and the
        # hover highlight read as "click me".
        self.assertIn(
            ".app-menu-item:disabled {\n            cursor: default;",
            terminals_css,
        )
        self.assertIn(".app-menu-item:hover:not(:disabled),", terminals_css)


class ScratchSessionLaunchTestCase(unittest.TestCase):
    """Repeatable launches of the built-in default ("scratch") session.

    The built-in "Default Session" is a blank form, not a stored preset, so it
    carries no launch identity: every launch mints its own group, and the group
    name — which the launcher derives from the SSH host or the local repository
    folder — is numbered when it is already taken.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        patcher = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_sessions_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self, session_name="10.0.0.5", **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": session_name,
            "saved_session_id": "default-session",
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_the_default_session_can_be_launched_over_and_over(self):
        names = [self._launch()["group"]["name"] for _ in range(3)]

        self.assertEqual(names, ["10.0.0.5", "10.0.0.5 (1)", "10.0.0.5 (2)"])
        self.assertEqual(len(api.session_manager.get_all_groups()), 3)

    def test_the_default_session_never_claims_a_preset_group(self):
        payload = self._launch()

        # A stable "saved-session-default-session" group id made the second
        # launch replace the first in place; a scratch launch owns no preset.
        self.assertNotEqual(payload["group_id"], "saved-session-default-session")
        self.assertEqual(payload["group"]["saved_session_id"], "")

    def test_the_default_session_opens_in_two_workspaces_at_once(self):
        first = self._launch()
        second = self._launch(new_workspace=True, workspace_label="Second")

        self.assertNotEqual(first["workspace_id"], second["workspace_id"])
        self.assertNotEqual(first["group_id"], second["group_id"])

    def test_saving_a_scratch_session_makes_the_next_launch_skip_its_name(self):
        self._launch()
        web_saved_sessions.upsert_saved_session(
            config={"connection_mode": "wsl", "wsl": {"default_dir": str(self.repo_dir)}},
            name="10.0.0.5 (1)",
        )

        self.assertEqual(self._launch()["group"]["name"], "10.0.0.5 (2)")

    def test_a_saved_preset_keeps_its_own_name_verbatim(self):
        self._launch(session_name="Reviews")

        payload = self._launch(session_name="Reviews", saved_session_id="alpha")

        # A preset is live in at most one workspace, so its tab must read as the
        # preset it is rather than as a numbered scratch session.
        self.assertEqual(payload["group"]["name"], "Reviews")
        self.assertEqual(payload["group_id"], "saved-session-alpha")

    def test_a_restore_replays_a_stored_name_without_renumbering(self):
        self._launch(session_name="Reviews")

        payload = self._launch(session_name="Reviews", restore=True)

        self.assertEqual(payload["group"]["name"], "Reviews")

    def test_unique_session_name_skips_taken_names_case_insensitively(self):
        self.assertEqual(
            web_saved_sessions.build_unique_session_name("Repo", ["repo", "REPO (1)"]),
            "Repo (2)",
        )
        self.assertEqual(
            web_saved_sessions.build_unique_session_name("Repo", []),
            "Repo",
        )


class AtomicGroupInstallTestCase(unittest.TestCase):
    """MW-07: a group and all of its panes are published in one lock hold.

    Launch used to create the group, then insert each pane under its own lock
    hold, and a stable group's old panes were torn down before any of that. An
    autosave tick landing between those steps persisted whatever it found — an
    empty group, one pane of three, or a stable group stripped of its old panes
    and not yet given its new ones — as though that were the workspace.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _launch(self, pane_count=2, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": f"Pane {index}",
                    "startup_mode": "explorer",
                }
                for index in range(pane_count)
            ],
        }
        body.update(overrides)
        return self.client.post("/api/sessions", json=body)

    def _observe_during_install(self, launch):
        """Run `launch` with a concurrent snapshot taken mid-install.

        The observer is released after the launch has built its *first* pane —
        the instant at which the group record exists and the rest of its panes
        do not — and then competes for `SessionManager.lock` exactly as the
        autosave timer does. Under the previous implementation that lock was
        released between every pane, so the observer read a group with one pane
        of three. Returns ``(response, observed_snapshot)``.
        """
        manager_type = type(api.session_manager)
        original = manager_type._build_session
        observations = []
        threads = []

        def hooked(manager_self, group_id, **fields):
            session = original(manager_self, group_id, **fields)
            if not threads:
                thread = threading.Thread(
                    target=lambda: observations.append(
                        manager_self.snapshot_live_workspaces()
                    )
                )
                thread.start()
                threads.append(thread)
                # Give the observer every chance to read: it can only be kept
                # out by a lock hold that spans the whole install.
                thread.join(timeout=0.2)
            return session

        with patch.object(manager_type, "_build_session", hooked):
            response = launch()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(len(observations), 1)
        return response, observations[0]

    @staticmethod
    def _pane_counts(snapshot, group_id):
        for workspace in snapshot.values():
            for group in workspace["groups"]:
                if group["group_id"] == group_id:
                    return len(group["sessions"])
        return None

    def test_a_new_group_is_never_snapshotted_without_its_panes(self):
        response, observed = self._observe_during_install(lambda: self._launch(3))

        self.assertEqual(response.status_code, 201, response.get_json())
        group_id = response.get_json()["group_id"]
        # Complete new shape, or not yet there at all. Never an empty group and
        # never a partial one.
        self.assertIn(self._pane_counts(observed, group_id), (None, 3))
        self.assertEqual(
            len(api.session_manager.get_group_sessions(group_id)), 3
        )

    def test_a_replacement_is_seen_as_the_old_shape_or_the_new_one(self):
        first = self._launch(2, saved_session_id="grid")
        self.assertEqual(first.status_code, 201, first.get_json())
        group_id = first.get_json()["group_id"]
        old_session_ids = {
            session["session_id"] for session in first.get_json()["sessions"]
        }

        response, observed = self._observe_during_install(
            lambda: self._launch(3, saved_session_id="grid")
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertEqual(response.get_json()["group_id"], group_id)
        self.assertIn(self._pane_counts(observed, group_id), (2, 3))
        live_session_ids = {
            session.session_id
            for session in api.session_manager.get_group_sessions(group_id)
        }
        self.assertEqual(len(live_session_ids), 3)
        self.assertEqual(live_session_ids & old_session_ids, set())

    def test_a_relaunch_with_no_usable_pane_keeps_the_live_panes(self):
        first = self._launch(2, saved_session_id="grid")
        self.assertEqual(first.status_code, 201, first.get_json())
        group_id = first.get_json()["group_id"]
        live_before = {
            session.session_id
            for session in api.session_manager.get_group_sessions(group_id)
        }

        broken = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "ssh",
                "session_name": "Files",
                "saved_session_id": "grid",
                "sessions": [
                    {
                        "host": "10.0.0.1",
                        "directory": "/srv",
                        "browser_active_tab": "not-a-number",
                    }
                ],
            },
        )

        self.assertEqual(broken.status_code, 400)
        self.assertEqual(
            broken.get_json()["error"], "No valid sessions were created"
        )
        # The old code tore the group's panes down before it discovered it had
        # nothing to put back, leaving an empty group where a working one was.
        self.assertEqual(
            {
                session.session_id
                for session in api.session_manager.get_group_sessions(group_id)
            },
            live_before,
        )

    def test_a_workspace_that_vanished_mid_launch_installs_nothing(self):
        workspace_id = self.client.post(
            "/api/workspaces", json={"label": "Doomed"}
        ).get_json()["workspace_id"]

        original = web_workspaces._prepare_launch_sessions

        def drop_workspace(sessions_config, connection_mode):
            api.session_manager.workspaces.pop(workspace_id, None)
            return original(sessions_config, connection_mode)

        with patch.object(
            web_workspaces, "_prepare_launch_sessions", drop_workspace
        ):
            response = self._launch(2, workspace_id=workspace_id)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(api.session_manager.get_all_groups(), [])
        self.assertEqual(api.session_manager.get_session_count(), 0)


class LaunchGroupIdentityTestCase(unittest.TestCase):
    """MW-11: a preset id maps to exactly one live group id, injectively.

    `_build_launch_group_id` used to collapse every run of non-`[A-Za-z0-9._-]`
    characters to a single `-`, so `a/b` and `a-b` produced one group id. In one
    workspace the second launch destructively replaced the first; across
    workspaces the teardown ran before `create_group` discovered the group
    belonged elsewhere, so the other workspace lost its live sessions to an
    error response.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        for target, attribute, value in (
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_sessions_path)),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _launch(self, saved_session_id, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": f"Preset {saved_session_id}",
            "saved_session_id": saved_session_id,
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        return self.client.post("/api/sessions", json=body)

    def test_the_group_id_encoding_is_injective(self):
        candidates = [
            "a/b",
            "a-b",
            "a_b",
            "a b",
            "a.b",
            "a:b",
            "a//b",
            "a---b",
            "---",
            "session-20260806-094325-19b823",
            "présets/α",
        ]
        group_ids = [
            web_saved_sessions._build_launch_group_id(candidate)
            for candidate in candidates
        ]

        self.assertEqual(len(set(group_ids)), len(candidates))
        self.assertNotIn("", group_ids)

    def test_ids_that_need_no_escaping_keep_the_group_id_they_had(self):
        # Every id `_generate_saved_session_id` mints is in this alphabet, so
        # live groups and saved snapshots from earlier builds are unaffected.
        for saved_session_id in (
            "session-20260806-094325-19b823",
            "alpha",
            "session-dev-grid",
            "v1.2-beta",
        ):
            with self.subTest(saved_session_id=saved_session_id):
                self.assertEqual(
                    web_saved_sessions._build_launch_group_id(saved_session_id),
                    f"saved-session-{saved_session_id}",
                )

    def test_two_presets_that_once_collided_launch_as_two_groups(self):
        first = self._launch("a/b")
        second = self._launch("a-b")

        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(second.status_code, 201, second.get_json())
        self.assertNotEqual(first.get_json()["group_id"], second.get_json()["group_id"])
        self.assertEqual(len(api.session_manager.get_all_groups()), 2)
        for payload in (first.get_json(), second.get_json()):
            self.assertEqual(
                len(api.session_manager.get_group_sessions(payload["group_id"])), 1
            )

    def test_a_colliding_preset_in_another_workspace_tears_nothing_down(self):
        first = self._launch("a/b")
        self.assertEqual(first.status_code, 201, first.get_json())
        first_group_id = first.get_json()["group_id"]
        first_session_id = first.get_json()["sessions"][0]["session_id"]

        second = self._launch("a-b", new_workspace=True, workspace_label="Second")

        self.assertEqual(second.status_code, 201, second.get_json())
        self.assertNotEqual(
            second.get_json()["workspace_id"], first.get_json()["workspace_id"]
        )
        self.assertIsNotNone(api.session_manager.get_session(first_session_id))
        self.assertEqual(
            len(api.session_manager.get_group_sessions(first_group_id)), 1
        )

    def test_a_group_id_owned_by_another_preset_is_refused_before_teardown(self):
        launched = self._launch("a/b")
        group_id = launched.get_json()["group_id"]
        session_id = launched.get_json()["sessions"][0]["session_id"]

        with self.assertRaises(ValueError) as raised:
            api.session_manager.install_session_group(
                [{"host": "10.0.0.9", "directory": "/srv"}],
                name="Impostor",
                connection_mode="ssh",
                layout="single",
                group_id=group_id,
                saved_session_id="somebody-else",
            )

        self.assertIn("already in use", str(raised.exception))
        self.assertIsNotNone(api.session_manager.get_session(session_id))
        self.assertEqual(
            api.session_manager.get_group(group_id).saved_session_id, "a/b"
        )

    def test_a_snapshot_holding_a_legacy_group_id_restores_its_own_shape(self):
        """A slot written before the encoding change restores unchanged.

        Its stored group id is the old lossy form. Restore derives the live id
        from the preset id as always, so the workspace comes back with the
        current id and the *snapshot's* name, pane count, and layout — the
        legacy id is never a reason to rewrite what the user saved.
        """
        launched = self._launch("a/b")
        self.assertEqual(launched.status_code, 201, launched.get_json())
        web_runtime_state.capture_workspace(
            api.session_manager, workspace_id="default", origin="manual"
        )

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        slot = state["workspaces"]["default"]
        slot["groups"][0]["group_id"] = "saved-session-a-b"
        slot["active_group_id"] = "saved-session-a-b"
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        api.session_manager.reset_sessions()

        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        restored = response.get_json()["workspaces"][0]
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["groups"][0]["snapshot_group_id"], "saved-session-a-b")
        self.assertEqual(restored["groups"][0]["name"], "Preset a/b")
        self.assertEqual(restored["groups"][0]["pane_count"], 1)
        live_group_id = restored["groups"][0]["group_id"]
        self.assertEqual(
            live_group_id, web_saved_sessions._build_launch_group_id("a/b")
        )
        # The saved front-group hint still resolves through the snapshot id.
        self.assertEqual(restored["active_group_id"], live_group_id)
        self.assertEqual(
            api.session_manager.get_group(live_group_id).saved_session_id, "a/b"
        )


class LaunchDestinationReservationTestCase(unittest.TestCase):
    """MW-06: a launch destination is held by a reservation, not by the clock.

    `resolve_launch_destination` creates the workspace before the launch has a
    group to put in it. That window used to be covered by a five-second
    wall-clock exemption inside cleanup — the same exemption that let an
    explicitly emptied group outlive its last pane forever.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _launch(self, **overrides):
        body = {
            "connection_mode": "wsl",
            "session_name": "Files",
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": "Files",
                    "startup_mode": "explorer",
                }
            ],
        }
        body.update(overrides)
        return self.client.post("/api/sessions", json=body)

    def _cleanup_mid_launch(self, age_destination_by=0.0):
        """Run a cleanup sweep from inside the launch, after the reservation."""
        original = web_workspaces._prepare_launch_sessions

        def hooked(sessions_config, connection_mode):
            for workspace in api.session_manager.get_all_workspaces():
                if workspace.workspace_id != "default":
                    workspace.created_at -= age_destination_by
            api.session_manager.clear_disconnected_sessions()
            return original(sessions_config, connection_mode)

        return patch.object(web_workspaces, "_prepare_launch_sessions", hooked)

    def test_a_new_destination_survives_a_cleanup_whatever_its_age(self):
        with self._cleanup_mid_launch(age_destination_by=3600):
            response = self._launch(new_workspace=True, workspace_label="Fresh")

        self.assertEqual(response.status_code, 201, response.get_json())
        workspace_id = response.get_json()["workspace_id"]
        self.assertIsNotNone(api.session_manager.get_workspace(workspace_id))
        self.assertEqual(
            len(api.session_manager.get_workspace_groups(workspace_id)), 1
        )

    def test_a_failed_launch_leaves_no_reservation_behind(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "ssh",
                "new_workspace": True,
                "sessions": [
                    {
                        "host": "10.0.0.1",
                        "directory": "/srv",
                        "browser_active_tab": "not-a-number",
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 400)
        # Rolled back, and a released reservation cannot keep a later empty
        # workspace immortal either.
        self.assertEqual(
            [
                workspace.workspace_id
                for workspace in api.session_manager.get_all_workspaces()
            ],
            ["default"],
        )
        stray = api.session_manager.create_workspace("Stray")
        self.assertEqual(
            api.session_manager.clear_disconnected_sessions(),
            [stray.workspace_id],
        )

    def test_an_immediate_last_pane_close_sweeps_its_group_without_aging(self):
        launched = self._launch(new_workspace=True, workspace_label="Quick")
        self.assertEqual(launched.status_code, 201, launched.get_json())
        payload = launched.get_json()

        closed = self.client.delete(
            f"/api/sessions/{payload['sessions'][0]['session_id']}"
        )

        self.assertEqual(closed.status_code, 200)
        self.assertIsNone(api.session_manager.get_group(payload["group_id"]))
        self.assertIsNone(api.session_manager.get_workspace(payload["workspace_id"]))


class ConnectionTargetProposalTestCase(unittest.TestCase):
    """`GET /api/session-targets`: reusable addresses, without the secrets."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.saved_sessions_path = Path(self.temp_dir.name) / "saved_sessions.json"
        patcher = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_sessions_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _save(self, name, config):
        return web_saved_sessions.upsert_saved_session(config=config, name=name)

    def _targets(self):
        response = self.client.get("/api/session-targets")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_no_saved_sessions_yields_two_empty_lists(self):
        self.assertEqual(self._targets(), {"ssh": [], "wsl": []})

    def test_identical_ssh_targets_are_offered_once(self):
        for name in ("Alpha", "Beta"):
            self._save(
                name,
                {
                    "connection_mode": "ssh",
                    "ssh": {
                        "host": "10.0.0.5",
                        "username": "ubuntu",
                        "port": 22,
                        "default_dir": "/srv/app",
                    },
                },
            )

        targets = self._targets()["ssh"]

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["host"], "10.0.0.5")
        self.assertEqual(targets[0]["default_dir"], "/srv/app")

    def test_a_different_directory_on_one_host_is_a_separate_target(self):
        for name, directory in (("Alpha", "/srv/app"), ("Beta", "/srv/other")):
            self._save(
                name,
                {
                    "connection_mode": "ssh",
                    "ssh": {"host": "10.0.0.5", "username": "ubuntu", "port": 22,
                            "default_dir": directory},
                },
            )

        self.assertEqual(
            sorted(target["default_dir"] for target in self._targets()["ssh"]),
            ["/srv/app", "/srv/other"],
        )

    def test_local_repository_paths_are_offered_under_their_own_mode(self):
        self._save(
            "Repo",
            {"connection_mode": "wsl", "wsl": {"default_dir": "/home/me/repo"}},
        )

        targets = self._targets()
        self.assertEqual(targets["ssh"], [])
        self.assertEqual(targets["wsl"][0]["default_dir"], "/home/me/repo")

    def test_saved_passwords_are_flagged_but_never_listed(self):
        self._save(
            "Alpha",
            {
                "connection_mode": "ssh",
                "ssh": {"host": "10.0.0.5", "username": "ubuntu", "port": 22,
                        "password": "hunter2"},
            },
        )

        target = self._targets()["ssh"][0]

        self.assertTrue(target["has_password"])
        self.assertNotIn("password", target)
        self.assertNotIn("hunter2", self.client.get("/api/session-targets").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
