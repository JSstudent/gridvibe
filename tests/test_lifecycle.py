"""Behavioral coverage for the shared voluntary close/restart transaction."""

import json
import shutil
import subprocess
import threading
import time
import unittest
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from web import api
from web import lifecycle as web_lifecycle
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web.lifecycle import (
    _MAX_WINDOWS_PER_WORKSPACE,
    LifecycleCoordinator,
    normalize_workspace_metadata,
)
from web.workspaces import workspace_room


class _LifecycleModalParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.choice_values = []
        self.element_ids = set()
        self._choices_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.element_ids.add(element_id)
        if self._choices_depth:
            if tag == "div":
                self._choices_depth += 1
            elif tag == "button":
                self.choice_values.append(attributes.get("data-lifecycle-save"))
        elif tag == "div" and element_id == "lifecycleChoices":
            self._choices_depth = 1

    def handle_endtag(self, tag):
        if self._choices_depth and tag == "div":
            self._choices_depth -= 1


class LifecycleCoordinatorTestCase(unittest.TestCase):
    def test_every_joined_window_acknowledges_before_flush_succeeds(self):
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("client-a", "default")
        coordinator.join_workspace("client-b", "bbbbbbbbbbbb")
        emitted = []

        def emit(workspace_id, request_id):
            emitted.append(workspace_id)
            client_id = "client-a" if workspace_id == "default" else "client-b"
            coordinator.acknowledge_flush(
                client_id,
                {
                    "request_id": request_id,
                    "workspace_id": workspace_id,
                    "ok": True,
                    "metadata": {"topbar_visible": workspace_id == "default"},
                },
            )

        result = coordinator.request_flush(
            {"default", "bbbbbbbbbbbb"}, emit, timeout=0.1
        )

        self.assertTrue(result["ok"])
        self.assertEqual(sorted(emitted), ["bbbbbbbbbbbb", "default"])
        self.assertEqual(set(result["metadata"]), {"default", "bbbbbbbbbbbb"})

    def test_missing_window_ack_is_a_bounded_retryable_failure(self):
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("client-a", "default")

        result = coordinator.request_flush({"default"}, lambda *_args: None, timeout=0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["missing_workspaces"], ["default"])
        self.assertEqual(result["errors"][0]["category"], "client_timeout")

    def test_a_fresh_disconnect_blocks_the_flush_until_its_stable_id_rejoins(self):
        """SGP-12: a fresh loss is still a real window and is still reported."""
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("socket-old", "default", "window-a")
        coordinator.disconnect_client("socket-old")

        stale = coordinator.request_flush({"default"}, lambda *_args: None, timeout=0.1)

        self.assertFalse(stale["ok"])
        self.assertEqual(stale["errors"][0]["category"], "client_stale")

        coordinator.join_workspace("socket-new", "default", "window-a")

        # The reload replaced its own record; no second registration accrues.
        self.assertEqual(len(coordinator._windows), 1)

        def acknowledge(workspace_id, request_id):
            coordinator.acknowledge_flush(
                "socket-new",
                {
                    "request_id": request_id,
                    "workspace_id": workspace_id,
                    "ok": True,
                },
            )

        recovered = coordinator.request_flush({"default"}, acknowledge, timeout=0.1)
        self.assertTrue(recovered["ok"])

    def test_a_window_disconnected_past_the_grace_period_is_dropped(self):
        """SGP-12: a permanent loss stops blocking the flush once departed."""
        coordinator = LifecycleCoordinator(stale_window_grace_seconds=0)
        coordinator.join_workspace("socket-old", "default", "window-a")
        coordinator.disconnect_client("socket-old")

        result = coordinator.request_flush({"default"}, lambda *_args: None, timeout=0.1)

        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])
        # Departed means forgotten: the record is gone, not merely ignored.
        self.assertEqual(coordinator._windows, {})

    def test_a_window_lost_after_the_snapshot_is_stale_not_a_timeout(self):
        """PRV-04: a drop the coordinator already saw must not cost the timeout."""
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("socket-a", "default", "window-a")

        def emit(_workspace_id, _request_id):
            # `request_flush` decided its expected/emit sets before releasing
            # the lock; the window dies on the way to its room.
            coordinator.disconnect_client("socket-a")

        started = time.monotonic()
        result = coordinator.request_flush({"default"}, emit, timeout=5.0)
        elapsed = time.monotonic() - started

        self.assertFalse(result["ok"])
        self.assertEqual(
            [error["category"] for error in result["errors"]], ["client_stale"]
        )
        self.assertLess(elapsed, 1.0)

    def test_a_window_lost_while_the_flush_waits_answers_immediately(self):
        """PRV-04: the same holds for the common case — a drop during the wait."""
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("socket-a", "default", "window-a")
        emitted = threading.Event()

        def emit(_workspace_id, _request_id):
            emitted.set()

        def drop():
            emitted.wait(5.0)
            coordinator.disconnect_client("socket-a")

        dropper = threading.Thread(target=drop)
        dropper.start()
        try:
            started = time.monotonic()
            result = coordinator.request_flush({"default"}, emit, timeout=5.0)
            elapsed = time.monotonic() - started
        finally:
            dropper.join(5.0)

        self.assertFalse(result["ok"])
        self.assertEqual(
            [error["category"] for error in result["errors"]], ["client_stale"]
        )
        self.assertLess(elapsed, 1.0)

    def test_a_window_that_leaves_mid_flush_is_forgotten_not_reported(self):
        """PRV-04: a deliberate departure is not a failed save, and does not wait.

        A window that left *before* the flush was never expected at all, so one
        that leaves during it is released the same way.
        """
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("socket-a", "default", "window-a")
        coordinator.join_workspace("socket-b", "default", "window-b")

        def emit(workspace_id, request_id):
            coordinator.leave_workspace("socket-a", "default")
            coordinator.acknowledge_flush(
                "socket-b",
                {
                    "request_id": request_id,
                    "workspace_id": workspace_id,
                    "ok": True,
                    "metadata": {"topbar_visible": True},
                },
            )

        started = time.monotonic()
        result = coordinator.request_flush({"default"}, emit, timeout=5.0)
        elapsed = time.monotonic() - started

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(len(result["metadata"]["default"]), 1)
        self.assertLess(elapsed, 1.0)

    def test_a_reloaded_window_replaces_its_own_record_without_leave(self):
        """SGP-12: same stable id, no pagehide — one record, not two."""
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("socket-old", "default", "window-a")
        coordinator.disconnect_client("socket-old")

        coordinator.join_workspace("socket-new", "default", "window-a")

        self.assertEqual(len(coordinator._windows), 1)
        self.assertEqual(coordinator.connected_window_count("default"), 1)
        record = coordinator._windows["window-a"]
        self.assertEqual(record["client_id"], "socket-new")
        self.assertTrue(record["connected"])

    def test_window_records_are_bounded_per_workspace(self):
        """SGP-12: even with every guard bypassed, growth is capped."""
        coordinator = LifecycleCoordinator()
        for index in range(_MAX_WINDOWS_PER_WORKSPACE + 3):
            coordinator.join_workspace(f"socket-{index}", "default", f"window-{index}")

        self.assertEqual(len(coordinator._windows), _MAX_WINDOWS_PER_WORKSPACE)
        # The oldest records are evicted first; the newest survive.
        self.assertNotIn("window-0", coordinator._windows)
        self.assertIn(f"window-{_MAX_WINDOWS_PER_WORKSPACE + 2}", coordinator._windows)

    def test_flush_metadata_is_ordered_oldest_window_first(self):
        """PRV-01: join order, not acknowledgement order, decides the winner."""
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("client-old", "default", "window-old")
        coordinator.join_workspace("client-new", "default", "window-new")

        def emit(workspace_id, request_id):
            # The newest window answers first, so arrival order is the reverse
            # of join order; the returned list must still be oldest first.
            for client, visible in (("client-new", True), ("client-old", False)):
                coordinator.acknowledge_flush(
                    client,
                    {
                        "request_id": request_id,
                        "workspace_id": workspace_id,
                        "ok": True,
                        "metadata": {"topbar_visible": visible},
                    },
                )

        result = coordinator.request_flush({"default"}, emit, timeout=1.0)

        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(
            [record["topbar_visible"] for record in result["metadata"]["default"]],
            [False, True],
        )


class LifecycleWindowChromeTestCase(unittest.TestCase):
    """PRV-01: window chrome degrades to a deterministic winner, never fails."""

    snapshots = {
        "default": {
            "groups": [
                {"group_id": "g1", "sessions": [{}]},
                {"group_id": "g2", "sessions": [{}]},
            ],
        }
    }

    def test_disagreeing_windows_resolve_to_the_most_recently_joined(self):
        resolved = normalize_workspace_metadata(
            {
                "default": [
                    {"active_group_id": "g1", "topbar_visible": True},
                    {"active_group_id": "g2", "topbar_visible": False},
                ]
            },
            self.snapshots,
        )

        self.assertEqual(
            resolved["default"],
            {"active_group_id": "g2", "topbar_visible": False},
        )

    def test_each_field_is_owned_by_the_newest_window_that_reported_it(self):
        resolved = normalize_workspace_metadata(
            {
                "default": [
                    {"active_group_id": "g1", "topbar_visible": True},
                    {"active_group_id": "g2"},
                ]
            },
            self.snapshots,
        )

        # The newer window said nothing about the top bar, so the older
        # window's opinion survives rather than being erased.
        self.assertEqual(
            resolved["default"],
            {"active_group_id": "g2", "topbar_visible": True},
        )

    def test_a_group_closed_from_another_window_drops_only_that_field(self):
        resolved = normalize_workspace_metadata(
            {
                "default": [
                    {"active_group_id": "g1", "topbar_visible": True},
                    {"active_group_id": "closed-elsewhere", "topbar_visible": False},
                ]
            },
            self.snapshots,
        )

        self.assertEqual(
            resolved["default"],
            {"active_group_id": "g1", "topbar_visible": False},
        )

    def test_a_stale_tab_hint_alone_leaves_the_hint_to_the_server(self):
        resolved = normalize_workspace_metadata(
            {"default": [{"active_group_id": "closed-elsewhere"}]},
            self.snapshots,
        )

        # Nothing survived, so the capture falls back to the server's own hint
        # instead of recording a group that no longer exists.
        self.assertEqual(resolved, {})

    def test_malformed_client_types_still_fail_the_save(self):
        for broken in (
            {"topbar_visible": "yes"},
            {"native_zoom_factor": 42.0},
        ):
            with self.subTest(broken=broken):
                with self.assertRaises(ValueError):
                    normalize_workspace_metadata(
                        {"default": [{"active_group_id": "g1"}, broken]},
                        self.snapshots,
                    )


class LifecycleRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.saved_path = Path(self.temp_dir.name) / "saved_sessions.json"
        self.state_patch = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        self.saved_patch = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_path)
        )
        self.state_patch.start()
        self.saved_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.saved_patch.stop)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        api.lifecycle_coordinator.reset()
        self.addCleanup(api.session_manager.reset_sessions)
        self.addCleanup(api.lifecycle_coordinator.reset)

    def _launch(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Files",
                "workspace_id": "default",
                "layout": "single",
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

    def test_route_flushes_room_scoped_without_holding_shared_locks(self):
        launched = self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default")

        def acknowledge(event, data, room=None, **_kwargs):
            self.assertEqual(event, "lifecycle_flush_requested")
            self.assertEqual(room, workspace_room("default"))
            self.assertFalse(api.session_manager.lock._is_owned())
            self.assertFalse(api.connection_lock._is_owned())
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": launched["group_id"],
                        "native_zoom_factor": 1.2,
                        "topbar_visible": False,
                    },
                },
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post(
                "/api/lifecycle/prepare",
                json={"action": "restart", "save": "workspaces"},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["ready_to_exit"])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        slot = state["workspaces"]["default"]
        self.assertEqual(slot["active_group_id"], launched["group_id"])
        self.assertEqual(slot["native_zoom_factor"], 1.2)
        self.assertFalse(slot["topbar_visible"])

    def test_two_windows_disagreeing_about_chrome_still_save_on_exit(self):
        """PRV-01: an ordinary second browser tab no longer blocks the save."""
        first = self._launch()
        second = self._launch()
        self.assertNotEqual(first["group_id"], second["group_id"])
        api.lifecycle_coordinator.join_workspace("client-old", "default", "window-old")
        api.lifecycle_coordinator.join_workspace("client-new", "default", "window-new")

        def acknowledge(event, data, room=None, **_kwargs):
            # One room emit reaches both windows: they show different front
            # tabs and disagree about the top bar, and the older one is still
            # pointing at a group the newer one has moved on from.
            api.lifecycle_coordinator.acknowledge_flush(
                "client-new",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": second["group_id"],
                        "topbar_visible": False,
                    },
                },
            )
            api.lifecycle_coordinator.acknowledge_flush(
                "client-old",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": first["group_id"],
                        "topbar_visible": True,
                    },
                },
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post(
                "/api/lifecycle/prepare",
                json={"action": "close", "save": "workspaces"},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["ready_to_exit"])
        self.assertEqual(payload["saved_workspaces"], ["default"])
        slot = json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"][
            "default"
        ]
        # The most recently joined window owns the chrome, both fields.
        self.assertEqual(slot["active_group_id"], second["group_id"])
        self.assertFalse(slot["topbar_visible"])

    def test_a_window_pointing_at_a_group_closed_elsewhere_still_saves(self):
        """PRV-01: a not-yet-processed close is stale chrome, not a bad payload."""
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")
        server_hint = api.session_manager.snapshot_live_workspaces()["default"][
            "active_group_id"
        ]

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": "closed-elsewhere",
                        "topbar_visible": True,
                    },
                },
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post(
                "/api/lifecycle/prepare",
                json={"action": "close", "save": "workspaces"},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["ready_to_exit"])
        slot = json.loads(self.state_path.read_text(encoding="utf-8"))["workspaces"][
            "default"
        ]
        # The stale hint was dropped; the server's own hint took its place.
        self.assertEqual(slot["active_group_id"], server_hint)
        self.assertTrue(slot["topbar_visible"])

    def test_a_broken_client_type_still_fails_the_save_on_exit(self):
        """PRV-01: degrading chrome must not swallow a malformed payload."""
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a",
                {**data, "ok": True, "metadata": {"topbar_visible": "yes"}},
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post(
                "/api/lifecycle/prepare",
                json={"action": "close", "save": "workspaces"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["ready_to_exit"])
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["errors"][0]["category"], "client_metadata")
        self.assertFalse(self.state_path.exists())

    def test_flush_timeout_keeps_the_application_open(self):
        self._launch()
        failed_flush = {
            "ok": False,
            "metadata": {},
            "errors": [
                {
                    "workspace_id": "default",
                    "category": "client_timeout",
                    "error": "Window did not respond",
                }
            ],
            "missing_workspaces": ["default"],
        }
        with patch.object(
            api.lifecycle_coordinator, "request_flush", return_value=failed_flush
        ):
            response = self.client.post(
                "/api/lifecycle/prepare",
                json={"action": "close", "save": "workspaces"},
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["ready_to_exit"])
        self.assertTrue(payload["retryable"])
        self.assertFalse(self.state_path.exists())
        self.assertEqual(len(api.session_manager.get_all_groups()), 1)

    def test_unsaved_ssh_password_is_encrypted_only_in_the_preset(self):
        group = api.session_manager.create_group(
            "Private host",
            "ssh",
            "single",
            1,
            group_id="private-host",
        )
        api.session_manager.create_session(
            group.group_id,
            host="private.example",
            directory="/srv/private",
            username="ubuntu",
            port=22,
            password="live-secret",
            title="Shell",
            mode="ssh",
        )

        with patch.object(web_saved_sessions, "_encrypt_password", return_value="ciphertext"):
            response = self.client.post(
                "/api/lifecycle/prepare",
                json={"action": "close", "save": "sessions+workspaces"},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        presets = self.saved_path.read_text(encoding="utf-8")
        runtime = self.state_path.read_text(encoding="utf-8")
        self.assertIn("ciphertext", presets)
        self.assertNotIn("live-secret", presets)
        self.assertNotIn("password", runtime)
        self.assertNotIn("live-secret", runtime)

    def test_exit_save_repairs_a_preset_saved_without_its_password(self):
        """A credential-free preset must not make a group permanently unrestorable.

        Saving a workspace from the terminal page used to store an empty
        ``ssh.password``. The exit save then merged onto that preset, and the
        merge keeps the base's ``ssh`` block verbatim — so the live password was
        discarded and the group failed every restore from then on.
        """
        empty_preset = web_saved_sessions.upsert_saved_session(
            {
                "connection_mode": "ssh",
                "terminal_count": 1,
                "layout": "single",
                "ssh": {
                    "host": "private.example",
                    "username": "ubuntu",
                    "password": "",
                    "port": 22,
                    "default_dir": "/srv/private",
                },
            },
            name="Private host",
        )
        group = api.session_manager.create_group(
            "Private host",
            "ssh",
            "single",
            1,
            group_id="private-host-repair",
        )
        api.session_manager.create_session(
            group.group_id,
            host="private.example",
            directory="/srv/private",
            username="ubuntu",
            port=22,
            password="live-secret",
            title="Shell",
            mode="ssh",
        )
        api.session_manager.update_group_saved_session(
            group.group_id, empty_preset["id"], empty_preset["name"]
        )

        response = self.client.post(
            "/api/lifecycle/prepare",
            json={"action": "close", "save": "sessions+workspaces"},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        repaired = next(
            entry
            for entry in web_saved_sessions.load_saved_sessions()
            if entry["id"] == empty_preset["id"]
        )
        self.assertEqual(repaired["config"]["ssh"]["password"], "live-secret")
        self.assertNotIn("live-secret", self.state_path.read_text(encoding="utf-8"))

    def test_shared_lifecycle_assets_are_loaded_on_both_pages(self):
        launcher = self.client.get("/").get_data(as_text=True)
        terminals = self.client.get("/terminals?workspace=default").get_data(as_text=True)

        for page in (launcher, terminals):
            self.assertIn('id="lifecycleModal"', page)
            self.assertIn("js/lifecycle.js", page)
            self.assertIn("css/lifecycle.css", page)
            parser = _LifecycleModalParser()
            parser.feed(page)
            self.assertEqual(
                parser.choice_values,
                ["none", "workspaces", "sessions+workspaces"],
            )
            self.assertTrue(
                {"lifecycleRetry", "lifecycleBack", "lifecycleContinue"}.isdisjoint(
                    parser.element_ids
                )
            )

    # ── Per-workspace Save (Stage 4.5, SGP-14) ──

    def _launch_second_workspace(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Second",
                "new_workspace": True,
                "workspace_label": "Second",
                "layout": "single",
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Second",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_workspace_save_flushes_one_window_and_captures_only_its_slot(self):
        launched = self._launch()
        second = self._launch_second_workspace()
        workspace_b = second["workspace_id"]
        api.lifecycle_coordinator.join_workspace("client-b", workspace_b, "window-b")

        def acknowledge_b(event, data, room=None, **_kwargs):
            self.assertEqual(event, "lifecycle_flush_requested")
            self.assertEqual(room, workspace_room(workspace_b))
            api.lifecycle_coordinator.acknowledge_flush(
                "client-b",
                {**data, "ok": True, "metadata": {"topbar_visible": False}},
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge_b):
            saved_b = self.client.post(f"/api/workspaces/{workspace_b}/save")

        self.assertEqual(saved_b.status_code, 200, saved_b.get_json())
        self.assertTrue(saved_b.get_json()["saved"])
        self.assertFalse(saved_b.get_json()["topbar_visible"])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        slot_b = state["workspaces"][workspace_b]
        # A per-workspace save never writes reusable presets.
        self.assertFalse(self.saved_path.exists())

        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")

        def acknowledge_a(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": launched["group_id"],
                        "topbar_visible": True,
                    },
                },
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge_a):
            saved_default = self.client.post("/api/workspaces/default/save")

        self.assertEqual(saved_default.status_code, 200, saved_default.get_json())
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        # The sibling slot is byte-for-byte the slot the first save wrote…
        self.assertEqual(state["workspaces"][workspace_b], slot_b)
        # …and only the target workspace's slot was captured.
        self.assertEqual(
            state["workspaces"]["default"]["active_group_id"], launched["group_id"]
        )
        self.assertTrue(state["workspaces"]["default"]["topbar_visible"])
        self.assertFalse(self.saved_path.exists())

    def test_workspace_save_resolves_two_windows_instead_of_refusing(self):
        """PRV-01: the per-row Save gets the same chrome resolution."""
        first = self._launch()
        second = self._launch()
        api.lifecycle_coordinator.join_workspace("client-old", "default", "window-old")
        api.lifecycle_coordinator.join_workspace("client-new", "default", "window-new")

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-new",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": second["group_id"],
                        "topbar_visible": True,
                    },
                },
            )
            api.lifecycle_coordinator.acknowledge_flush(
                "client-old",
                {
                    **data,
                    "ok": True,
                    "metadata": {
                        "active_group_id": first["group_id"],
                        "topbar_visible": False,
                    },
                },
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["active_group_id"], second["group_id"])
        self.assertTrue(payload["topbar_visible"])

    def test_workspace_save_survives_a_tab_hint_closed_from_another_window(self):
        """PRV-01: a stale front-tab hint costs the field, not the save."""
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")
        server_hint = api.session_manager.snapshot_live_workspaces()["default"][
            "active_group_id"
        ]

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a",
                {
                    **data,
                    "ok": True,
                    "metadata": {"active_group_id": "closed-elsewhere"},
                },
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["active_group_id"], server_hint)

    def test_workspace_save_still_refuses_a_malformed_client_payload(self):
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a",
                {**data, "ok": True, "metadata": {"native_zoom_factor": 42.0}},
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["errors"][0]["category"], "client_metadata")
        self.assertFalse(self.state_path.exists())

    def test_workspace_save_without_a_window_reports_instead_of_guessing(self):
        self._launch()

        response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["retryable"])
        self.assertIn("No reachable window", payload["error"])
        # No capture from the last acknowledged server state: nothing written.
        self.assertFalse(self.state_path.exists())

    def test_workspace_save_with_a_lost_window_reports_instead_of_guessing(self):
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")
        api.lifecycle_coordinator.disconnect_client("client-a")

        response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()["retryable"])
        self.assertFalse(self.state_path.exists())

    def test_workspace_save_reports_an_empty_workspace_without_capturing(self):
        created = self.client.post("/api/workspaces", json={"label": "Scratch"})
        workspace_id = created.get_json()["workspace_id"]
        api.lifecycle_coordinator.join_workspace("client-s", workspace_id, "window-s")

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-s", {**data, "ok": True, "metadata": {}}
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post(f"/api/workspaces/{workspace_id}/save")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.get_json()["saved"])
        # An empty workspace overwrites nothing: no file, no cleared slot.
        self.assertFalse(self.state_path.exists())

    def test_workspace_save_rejects_unknown_and_malformed_workspaces(self):
        unknown = self.client.post("/api/workspaces/bbbbbbbbbbbb/save")
        malformed = self.client.post("/api/workspaces/NOT-AN-ID/save")

        self.assertEqual(unknown.status_code, 404)
        self.assertTrue(unknown.get_json()["workspace_missing"])
        self.assertEqual(malformed.status_code, 400)

    # ── Per-group Save: the close prompt's middle button, from elsewhere ──

    def test_group_save_writes_one_preset_and_links_the_live_group_to_it(self):
        """The agent dashboard's *Save and close* has no live DOM to compose a
        preset from, so the server builds one out of the same live-group shape
        the exit save uses — and links the group to it, so a second save
        updates that preset rather than minting another."""
        launched = self._launch()
        group_id = launched["group_id"]

        response = self.client.post(f"/api/session-groups/{group_id}/save")

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["saved"])
        self.assertEqual(payload["group_id"], group_id)
        self.assertEqual(payload["workspace_id"], "default")
        entries = web_saved_sessions.load_saved_sessions()
        self.assertEqual([entry["id"] for entry in entries], [payload["id"]])
        self.assertEqual(entries[0]["name"], payload["name"])
        # It saves and nothing else: no workspace slot, and nothing closed.
        self.assertFalse(self.state_path.exists())
        self.assertEqual(
            len(api.session_manager.get_group_sessions(group_id)),
            len(launched["sessions"]),
        )

        again = self.client.post(f"/api/session-groups/{group_id}/save")

        self.assertEqual(again.status_code, 200, again.get_json())
        self.assertEqual(again.get_json()["id"], payload["id"])
        self.assertEqual(len(web_saved_sessions.load_saved_sessions()), 1)

    def test_group_save_flushes_the_owning_window_before_it_composes(self):
        """Presentation a window has queued but not sent is the difference
        between saving what the reader sees and saving what the server last
        heard."""
        launched = self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")
        rooms = []

        def acknowledge(event, data, room=None, **_kwargs):
            rooms.append((event, room))
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a", {**data, "ok": True, "metadata": {}}
            )

        with patch.object(api.socketio, "emit", side_effect=acknowledge):
            response = self.client.post(
                f"/api/session-groups/{launched['group_id']}/save"
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            rooms, [("lifecycle_flush_requested", workspace_room("default"))]
        )

    def test_group_save_refuses_when_the_window_it_asked_never_answered(self):
        launched = self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")

        with patch.object(api.socketio, "emit"):
            response = self.client.post(
                f"/api/session-groups/{launched['group_id']}/save"
            )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertFalse(payload["saved"])
        self.assertTrue(payload["retryable"])
        # A refused save writes nothing, so the caller can keep the session and
        # the reader can try again.
        self.assertFalse(self.saved_path.exists())

    def test_group_save_with_no_window_open_composes_from_what_was_synced(self):
        """The one deliberate difference from the per-workspace save. A
        workspace snapshot carries window chrome only a window knows, so it
        refuses; a session preset carries none, and with nothing open there is
        nothing newer to wait for."""
        launched = self._launch()

        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                f"/api/session-groups/{launched['group_id']}/save"
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["saved"])
        # Nothing to flush means nothing was asked to flush.
        emit.assert_not_called()

    def test_group_save_reports_an_unknown_or_empty_group_without_writing(self):
        unknown = self.client.post("/api/session-groups/not-a-group/save")

        self.assertEqual(unknown.status_code, 404)
        self.assertTrue(unknown.get_json()["group_missing"])
        self.assertFalse(self.saved_path.exists())

        launched = self._launch()
        group_id = launched["group_id"]
        # The race the 409 is for: the group's last pane goes while the flush
        # is out, so the group is still registered but the snapshot -- which
        # skips a group with no panes -- no longer carries it.
        with patch.object(
            api.session_manager, "snapshot_lifecycle_workspaces", return_value={}
        ):
            emptied = self.client.post(f"/api/session-groups/{group_id}/save")

        # Empty, not missing — the same distinction the workspace save draws
        # with its own 409 — and an empty group never overwrites a preset with
        # nothing.
        self.assertEqual(emptied.status_code, 409)
        self.assertFalse(emptied.get_json()["saved"])
        self.assertFalse(self.saved_path.exists())

    def test_group_save_keeps_the_live_ssh_credential_out_of_its_answer(self):
        """`snapshot_lifecycle_workspaces` is the one credential-bearing path;
        this route uses it for exactly what it exists for and returns none of
        it."""
        launched = self._launch()
        group_id = launched["group_id"]
        session_id = launched["sessions"][0]["session_id"]
        with api.session_manager.lock:
            api.session_manager.sessions[session_id].password = "live-secret"

        response = self.client.post(f"/api/session-groups/{group_id}/save")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("live-secret", response.get_data(as_text=True))
        self.assertNotIn("live-secret", self.saved_path.read_text(encoding="utf-8"))


class LifecycleDiagnosticsTestCase(unittest.TestCase):
    """Stage 7 item 3 — lifecycle logging carries shape diagnostics only:
    ids, revisions, counts, and failure categories, never paths, payloads,
    or secrets."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.saved_path = Path(self.temp_dir.name) / "saved_sessions.json"
        self.state_patch = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        self.saved_patch = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_path)
        )
        self.state_patch.start()
        self.saved_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.saved_patch.stop)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        api.lifecycle_coordinator.reset()
        self.addCleanup(api.session_manager.reset_sessions)
        self.addCleanup(api.lifecycle_coordinator.reset)

    def _launch(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Files",
                "workspace_id": "default",
                "layout": "single",
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

    @staticmethod
    def _messages(output):
        return [record.getMessage() for record in output.records]

    def test_successful_workspace_save_logs_ids_origin_and_revision(self):
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")

        def acknowledge(event, data, room=None, **_kwargs):
            api.lifecycle_coordinator.acknowledge_flush(
                "client-a", {**data, "ok": True, "metadata": {"topbar_visible": True}}
            )

        with self.assertLogs("web.lifecycle", level="DEBUG") as captured:
            with patch.object(api.socketio, "emit", side_effect=acknowledge):
                response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 200, response.get_json())
        messages = self._messages(captured)
        self.assertTrue(
            any(
                "Workspace default saved from launcher origin=manual revision=" in message
                for message in messages
            ),
            messages,
        )
        # Shape only: the pane's directory never reaches the log.
        self.assertFalse(
            any(str(self.repo_dir) in message for message in messages), messages
        )

    def test_flush_failure_logs_the_category_not_the_client_error_text(self):
        self._launch()
        api.lifecycle_coordinator.join_workspace("client-a", "default", "window-a")
        failed_flush = {
            "ok": False,
            "metadata": {},
            "errors": [
                {
                    "workspace_id": "default",
                    "category": "client_timeout",
                    "error": "flush died near C:\\secret\\window token=hunter2",
                }
            ],
            "missing_workspaces": ["default"],
        }
        with self.assertLogs("web.lifecycle", level="DEBUG") as captured:
            with patch.object(
                api.lifecycle_coordinator, "request_flush", return_value=failed_flush
            ):
                response = self.client.post("/api/workspaces/default/save")

        self.assertEqual(response.status_code, 503)
        messages = self._messages(captured)
        self.assertTrue(any("client_timeout" in message for message in messages), messages)
        self.assertFalse(any("hunter2" in message for message in messages), messages)
        self.assertFalse(any("C:\\secret" in message for message in messages), messages)

    def test_capture_failure_logs_the_exception_category_not_its_text(self):
        self._launch()
        with self.assertLogs("web.lifecycle", level="DEBUG") as captured:
            with patch.object(
                web_lifecycle,
                "capture_live_workspaces",
                side_effect=RuntimeError("write failed at C:\\secret\\runtime_state.json"),
            ):
                response = self.client.post(
                    "/api/lifecycle/prepare",
                    json={"action": "close", "save": "workspaces"},
                )

        self.assertEqual(response.status_code, 503, response.get_json())
        messages = self._messages(captured)
        self.assertTrue(any("category=RuntimeError" in message for message in messages), messages)
        self.assertFalse(any("C:\\secret" in message for message in messages), messages)

    def test_preset_failure_logs_counts_and_scopes_only(self):
        group = api.session_manager.create_group(
            "Private host", "ssh", "single", 1, group_id="private-host",
            workspace_id="default",
        )
        api.session_manager.create_session(
            group.group_id,
            host="private.example",
            directory="/srv/private",
            username="ubuntu",
            port=22,
            password="live-secret",
            title="Shell",
            mode="ssh",
        )
        with self.assertLogs("web.lifecycle", level="DEBUG") as captured:
            with patch.object(
                web_lifecycle,
                "upsert_saved_session",
                side_effect=RuntimeError("disk full at C:\\secret\\saved_sessions.json"),
            ):
                response = self.client.post(
                    "/api/lifecycle/prepare",
                    json={"action": "close", "save": "sessions+workspaces"},
                )

        self.assertEqual(response.status_code, 503, response.get_json())
        messages = self._messages(captured)
        self.assertTrue(
            any(
                "preset save incomplete: saved=0 failed=1 scopes=['session']" in message
                for message in messages
            ),
            messages,
        )
        for forbidden in ("live-secret", "private.example", "/srv/private", "C:\\secret"):
            self.assertFalse(
                any(forbidden in message for message in messages),
                (forbidden, messages),
            )

    def test_window_registry_changes_log_at_debug_with_ids_only(self):
        coordinator = LifecycleCoordinator()
        coordinator.join_workspace("socket-old", "default", "window-a")
        coordinator.disconnect_client("socket-old")

        with self.assertLogs("web.lifecycle", level="DEBUG") as captured:
            coordinator.join_workspace("socket-new", "default", "window-a")
            departed = LifecycleCoordinator(stale_window_grace_seconds=0)
            departed.join_workspace("socket-b", "research", "window-b")
            departed.disconnect_client("socket-b")
            departed.connected_window_count("research")

        messages = self._messages(captured)
        self.assertTrue(
            any("rejoined workspace=default" in message for message in messages), messages
        )
        self.assertTrue(
            any("departed past grace workspace=research" in message for message in messages),
            messages,
        )


@unittest.skipUnless(shutil.which("node"), "Node.js is required for lifecycle client tests")
class LifecycleClientTestCase(unittest.TestCase):
    MODULE = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "lifecycle.js"

    def _run_node(self, source):
        completed = subprocess.run(
            ["node", "-e", source],
            cwd=self.MODULE.parent.parent.parent.parent,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_flush_responder_acks_only_after_the_barrier_and_metadata(self):
        result = self._run_node(
            f"""
            const lifecycle = require({json.dumps(str(self.MODULE))});
            const handlers = {{}};
            const emitted = [];
            const socket = {{
                on(name, handler) {{ handlers[name] = handler; }},
                emit(name, payload) {{ emitted.push({{ name, payload }}); }}
            }};
            let order = [];
            lifecycle.attachFlushResponder(socket, {{
                workspaceId: 'default',
                flush: async () => {{ order.push('flush'); return {{ ok: true }}; }},
                metadata: async () => {{ order.push('metadata'); return {{ topbar_visible: false }}; }}
            }});
            (async () => {{
                await handlers.lifecycle_flush_requested({{ request_id: 'r1', workspace_id: 'default' }});
                console.log(JSON.stringify({{ order, emitted }}));
            }})().catch(error => {{ console.error(error); process.exit(1); }});
            """
        )

        self.assertEqual(result["order"], ["flush", "metadata"])
        acknowledgement = result["emitted"][0]
        self.assertEqual(acknowledgement["name"], "lifecycle_flush_ack")
        self.assertTrue(acknowledgement["payload"]["ok"])
        self.assertFalse(acknowledgement["payload"]["metadata"]["topbar_visible"])

    def test_prepare_can_retry_after_a_retryable_failure(self):
        result = self._run_node(
            f"""
            const lifecycle = require({json.dumps(str(self.MODULE))});
            let calls = 0;
            const fetchImpl = async () => {{
                calls += 1;
                return calls === 1
                    ? {{ ok: false, json: async () => ({{ ready_to_exit: false, retryable: true, errors: [{{ error: 'disk full' }}] }}) }}
                    : {{ ok: true, json: async () => ({{ ready_to_exit: true, decision_token: 'done' }}) }};
            }};
            (async () => {{
                let firstFailed = false;
                try {{ await lifecycle.prepare(fetchImpl, 'restart', 'workspaces'); }}
                catch (error) {{ firstFailed = error.result.retryable; }}
                const second = await lifecycle.prepare(fetchImpl, 'restart', 'workspaces');
                console.log(JSON.stringify({{ calls, firstFailed, token: second.decision_token }}));
            }})().catch(error => {{ console.error(error); process.exit(1); }});
            """
        )

        self.assertEqual(result, {"calls": 2, "firstFailed": True, "token": "done"})

    def test_modal_keeps_one_choice_list_after_failure_and_cancel_closes_it(self):
        result = self._run_node(
            f"""
            const lifecycle = require({json.dumps(str(self.MODULE))});
            const classes = new Set();
            const attributes = {{}};
            const cancelListeners = {{}};
            const makeClassList = () => ({{
                add() {{}}, remove() {{}}, toggle() {{}}
            }});
            const modal = {{
                classList: {{
                    add(...names) {{ names.forEach(name => classes.add(name)); }},
                    remove(...names) {{ names.forEach(name => classes.delete(name)); }},
                    toggle(name, force) {{
                        if (force) classes.add(name);
                        else classes.delete(name);
                    }}
                }},
                setAttribute(name, value) {{ attributes[name] = value; }}
            }};
            const status = {{ textContent: '', classList: makeClassList() }};
            const card = {{ classList: makeClassList() }};
            const choiceButtons = ['none', 'workspaces', 'sessions+workspaces'].map(save => ({{
                dataset: {{ lifecycleSave: save }},
                listeners: {{}},
                textContent: '',
                addEventListener(name, handler) {{ this.listeners[name] = handler; }},
                replaceChildren(value) {{ this.textContent = String(value); }}
            }}));
            const choices = {{
                hidden: false,
                querySelectorAll() {{ return choiceButtons; }},
                querySelector(selector) {{
                    return choiceButtons.find(button => selector.includes(`"${{button.dataset.lifecycleSave}}"`)) || null;
                }}
            }};
            const cancel = {{
                addEventListener(name, handler) {{ cancelListeners[name] = handler; }}
            }};
            global.document = {{
                getElementById(id) {{
                    if (id === 'lifecycleModal') return modal;
                    if (id === 'lifecycleStatus') return status;
                    if (id === 'lifecycleChoices') return choices;
                    if (id === 'lifecycleCancel') return cancel;
                    return null;
                }},
                querySelector(selector) {{
                    return selector === '#lifecycleModal .lifecycle-card' ? card : null;
                }}
            }};

            let cancelled = false;
            let reportFailure;
            const failed = new Promise(resolve => {{ reportFailure = resolve; }});
            (async () => {{
                lifecycle.openModal({{
                    action: 'restart',
                    onReady: async () => {{}},
                    onError: () => reportFailure(),
                    onCancel: () => {{ cancelled = true; }},
                    fetchImpl: async () => ({{
                        ok: false,
                        json: async () => ({{
                            ready_to_exit: false,
                            errors: [{{ error: 'disk full' }}]
                        }})
                    }})
                }});
                const opened = {{
                    visible: classes.has('visible'),
                    ariaHidden: attributes['aria-hidden'],
                    labels: choiceButtons.map(button => button.textContent),
                    status: status.textContent
                }};
                choiceButtons[1].listeners.click();
                await failed;
                const afterFailure = {{
                    visible: classes.has('visible'),
                    choicesHidden: choices.hidden,
                    status: status.textContent
                }};
                cancelListeners.click();
                console.log(JSON.stringify({{
                    opened,
                    afterFailure,
                    cancelled,
                    visibleAfterCancel: classes.has('visible'),
                    ariaHiddenAfterCancel: attributes['aria-hidden']
                }}));
            }})().catch(error => {{ console.error(error); process.exit(1); }});
            """
        )

        self.assertEqual(result["opened"]["visible"], True)
        self.assertEqual(result["opened"]["ariaHidden"], "false")
        self.assertEqual(
            result["opened"]["labels"],
            [
                "Continue without saving current changes",
                "Save open workspaces & restart",
                "Save open sessions + workspaces & restart",
            ],
        )
        self.assertEqual(result["opened"]["status"], "")
        self.assertTrue(result["afterFailure"]["visible"])
        self.assertFalse(result["afterFailure"]["choicesHidden"])
        self.assertIn("disk full", result["afterFailure"]["status"])
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["visibleAfterCancel"])
        self.assertEqual(result["ariaHiddenAfterCancel"], "true")


if __name__ == "__main__":
    unittest.main()
