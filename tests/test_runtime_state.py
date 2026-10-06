"""The runtime workspace snapshot: `agent_mcp_override` and `crew_links`.

A restore is the same pane coming back, so it keeps the grant it was launched
with. A snapshot written before the field existed does not state it, and that
pane comes back without the grant -- the absent key reads `False`, never
"inherit". The record itself holds the grant only beside `agent_mcp` on an
agent pane, which is what makes every path that stops a pane being one drop it.

`crew_links` is the slot's record of the agent crew links between its panes,
endpoints as (snapshot group id, pane index). Every capture writes it, so an
autosave after an explicit save keeps it; on read it degrades, never fails the
slot.
"""

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sessions.manager import SessionManager
from web import agents as web_agents
from web import api, crew_history
from web import config as web_config
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import workspaces as web_workspaces
from web.agent_results import MAX_ASSIGNMENTS
from web.agent_results import results as agent_results_store

_AGENT_PANE = {
    "title": "Claude",
    "startup_mode": "agent",
    "initial_command_mode": "agent",
    "agent_selection": "claude",
    "initial_command": "claude",
    "agent_mcp": True,
}


class OverrideRecordTestCase(unittest.TestCase):
    """The session record: what it accepts, and what it refuses to keep."""

    def setUp(self):
        self.manager = SessionManager()
        self.group = self.manager.create_group(
            name="Local", connection_mode="wsl", layout="single", terminal_count=1
        )

    def _pane(self, **fields):
        return self.manager.create_session(
            group_id=self.group.group_id,
            host="cmd",
            directory="C:/repo",
            mode="wsl",
            **fields,
        )

    def test_a_pane_defaults_to_no_grant(self):
        session = self._pane(**_AGENT_PANE)

        self.assertIs(session.agent_mcp_override, False)
        self.assertIs(session.to_dict()["agent_mcp_override"], False)

    def test_an_agent_pane_with_the_tools_keeps_a_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.assertIs(session.agent_mcp_override, True)
        self.assertIs(session.to_dict()["agent_mcp_override"], True)

    def test_no_grant_is_kept_without_the_tools_or_off_an_agent_pane(self):
        cases = {
            "no tools": {**_AGENT_PANE, "agent_mcp": False},
            "terminal": {"startup_mode": "terminal", "agent_mcp": True},
            "explorer": {"startup_mode": "explorer", "agent_mcp": True},
        }
        for label, fields in cases.items():
            with self.subTest(case=label):
                session = self._pane(**fields, agent_mcp_override=True)
                self.assertIs(session.agent_mcp_override, False)

    def test_launch_intake_believes_only_a_stated_true(self):
        for value, expected in (
            (True, True),
            (None, False),
            ("true", False),
            (1, False),
        ):
            with self.subTest(value=value):
                fields = self.manager._session_launch_fields(
                    {**_AGENT_PANE, "agent_mcp_override": value}
                )
                self.assertIs(fields["agent_mcp_override"], expected)
        absent = self.manager._session_launch_fields(dict(_AGENT_PANE))
        self.assertIs(absent["agent_mcp_override"], False)

    def test_a_metadata_update_that_removes_the_tools_drops_the_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.manager.update_session_metadata(session.session_id, agent_mcp=False)
        self.assertIs(session.agent_mcp_override, False)

        # It does not come back with the tools.
        self.manager.update_session_metadata(session.session_id, agent_mcp=True)
        self.assertIs(session.agent_mcp_override, False)

    def test_a_metadata_update_that_ends_the_agent_drops_the_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.manager.update_session_metadata(
            session.session_id, startup_mode="explorer"
        )

        self.assertIs(session.agent_mcp_override, False)

    def test_a_browser_switch_drops_the_grant(self):
        session = self._pane(**_AGENT_PANE, agent_mcp_override=True)

        self.manager.merge_browser_tabs(
            session.session_id,
            browser_url="http://localhost:3000",
            browser_active_tab=None,
            default_browser_url="http://localhost:3000",
        )

        self.assertIs(session.agent_mcp_override, False)

    def test_a_grant_stated_beside_the_agent_it_rides_on_is_kept(self):
        session = self._pane(startup_mode="terminal")

        self.manager.update_session_metadata(
            session.session_id, **_AGENT_PANE, agent_mcp_override=True
        )

        self.assertIs(session.agent_mcp_override, True)


class SnapshotOverrideTestCase(unittest.TestCase):
    """Capture and read-side validation, without a live app."""

    def test_capture_records_the_grant(self):
        snapshot = web_runtime_state._snapshot_session(
            {**_AGENT_PANE, "directory": "C:/repo", "agent_mcp_override": True}
        )

        self.assertIs(snapshot["agent_mcp_override"], True)

    def test_validation_passes_a_stored_grant_through(self):
        stored = web_runtime_state._snapshot_session(
            {**_AGENT_PANE, "directory": "C:/repo", "agent_mcp_override": True}
        )

        validated = web_runtime_state._validate_session(stored)

        self.assertIsNotNone(validated)
        self.assertIs(validated["agent_mcp_override"], True)


class _LiveAppMixin:
    """A real app client over temporary durable state, agents preflighted."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        for module, attribute, value in (
            (web_config, "CONFIG_PATH", str(Path(self.temp_dir.name) / "config.json")),
            (
                web_saved_sessions,
                "SAVED_SESSIONS_PATH",
                str(Path(self.temp_dir.name) / "saved_sessions.json"),
            ),
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
        ):
            patcher = patch.object(module, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, attribute, value in (
            (
                web_agents,
                "_agent_preflight_payload",
                {"status": "installed", "message": "Claude Code is available."},
            ),
        ):
            patcher = patch.object(target, attribute, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        background = patch.object(api.socketio, "start_background_task")
        background.start()
        self.addCleanup(background.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        api.lifecycle_coordinator.reset()
        self.addCleanup(api.session_manager.reset_sessions)
        self.addCleanup(api.lifecycle_coordinator.reset)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()

    def _launch(self, **pane):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "workspace_id": "default",
                "session_name": "Agents",
                "layout": "single",
                "sessions": [{**_AGENT_PANE, "directory": str(self.repo_dir), **pane}],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return api.session_manager.get_group_sessions(
            response.get_json()["group_id"]
        )[0]


class RestoreOverrideTestCase(_LiveAppMixin, unittest.TestCase):
    """Launch, capture, restart, restore: the pane comes back as it was."""

    def _save_restart_and_restore(self, edit_stored=None):
        saved = self.client.post(
            "/api/runtime-state/save", json={"workspace_id": "default"}
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        if edit_stored is not None:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            for pane in state["workspaces"]["default"]["groups"][0]["sessions"]:
                edit_stored(pane)
            self.state_path.write_text(json.dumps(state), encoding="utf-8")

        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        group = api.session_manager.get_workspace_groups("default")[0]
        return api.session_manager.get_group_sessions(group.group_id)[0]

    def _stored_panes(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state["workspaces"]["default"]["groups"][0]["sessions"]

    def test_a_granted_pane_is_saved_and_restored_with_its_grant(self):
        self._launch(agent_mcp_override=True)

        session = self._save_restart_and_restore()

        self.assertIs(self._stored_panes()[0]["agent_mcp_override"], True)
        self.assertTrue(session.agent_mcp)
        self.assertIs(session.agent_mcp_override, True)

    def test_a_pane_without_the_grant_comes_back_without_it(self):
        self._launch()

        session = self._save_restart_and_restore()

        self.assertIs(self._stored_panes()[0]["agent_mcp_override"], False)
        self.assertIs(session.agent_mcp_override, False)

    def test_a_snapshot_written_before_the_field_restores_without_the_grant(self):
        self._launch(agent_mcp_override=True)

        session = self._save_restart_and_restore(
            edit_stored=lambda pane: pane.pop("agent_mcp_override", None)
        )

        self.assertTrue(session.agent_mcp)
        self.assertIs(session.agent_mcp_override, False)

    def test_a_hand_edited_truthy_string_restores_without_the_grant(self):
        self._launch(agent_mcp_override=True)

        session = self._save_restart_and_restore(
            edit_stored=lambda pane: pane.update(agent_mcp_override="true")
        )

        self.assertIs(session.agent_mcp_override, False)


class ToolLaunchOverrideTestCase(_LiveAppMixin, unittest.TestCase):
    """A launch an agent asks for from its pane states no grant of its own."""

    def test_a_tool_launch_cannot_state_a_grant(self):
        # The caller holds the grant itself; that still does not let it hand
        # one to the pane it launches.
        caller = self._launch(agent_mcp_override=True)
        self.assertIs(caller.agent_mcp_override, True)

        response = self.client.post(
            "/api/sessions",
            json={
                "workspace_id": "default",
                "layout": "single",
                "origin_session_id": caller.session_id,
                "sessions": [
                    {
                        **_AGENT_PANE,
                        "directory": str(self.repo_dir),
                        "agent_mcp_override": True,
                    }
                ],
            },
        )

        self.assertEqual(response.status_code, 201, response.get_json())
        created = api.session_manager.get_group_sessions(
            response.get_json()["group_id"]
        )[0]
        self.assertTrue(created.agent_mcp)
        self.assertIs(created.agent_mcp_override, False)


# ==================== crew_links ====================

#: The keys a stored crew link carries: the two coordinates and the public
#: link fields, never a session id, link id, report text, receipt or handoff id.
_STORED_LINK_KEYS = {
    "requester",
    "worker",
    "state",
    "read",
    "status",
    "collected",
    "handed_at",
    "reported_at",
    "reason",
    "round",
    "label",
}


def _link(requester, worker, state="reported", **fields):
    """One dashboard link, as `links_snapshot()` returns it."""
    link = {
        "link_id": f"link-{requester}-{worker}",
        "requester_session_id": requester,
        "worker_session_id": worker,
        "state": state,
        "read": True,
        "status": "done" if state == "reported" else "",
        "collected": False,
        "handed_at": "2026-10-06T01:00:00+00:00",
        "reported_at": "2026-10-06T01:05:00+00:00" if state == "reported" else "",
        "reason": "",
        "round": 1,
        "label": "build",
    }
    link.update(fields)
    return link


def _stored_link(requester, worker, **fields):
    """One stored entry, as a slot holds it."""
    entry = {
        "requester": {"group": requester[0], "pane": requester[1]},
        "worker": {"group": worker[0], "pane": worker[1]},
        "state": "reported",
        "read": True,
        "status": "done",
        "collected": False,
        "handed_at": "2026-10-06T01:00:00+00:00",
        "reported_at": "2026-10-06T01:05:00+00:00",
        "reason": "",
        "round": 1,
        "label": "build",
    }
    entry.update(fields)
    return entry


def _pane(session_id, startup_mode="agent"):
    pane = {"session_id": session_id, "directory": "/srv", "title": session_id}
    if startup_mode == "agent":
        pane.update(_AGENT_PANE)
    else:
        pane["startup_mode"] = startup_mode
    return pane


def _group(group_id, *panes):
    return {
        "group_id": group_id,
        "name": f"Group {group_id}",
        "connection_mode": "wsl",
        "layout": "single" if len(panes) == 1 else "vertical",
        "sessions": list(panes),
    }


class _FakeManager:
    """The one manager call a capture makes, over fixed live workspaces."""

    def __init__(self, workspaces):
        self.workspaces = workspaces

    def snapshot_live_workspaces(self):
        return {
            workspace_id: {
                "workspace_id": workspace_id,
                "label": workspace_id,
                "groups": copy.deepcopy(groups),
            }
            for workspace_id, groups in self.workspaces.items()
        }


class CrewLinksCaptureTestCase(unittest.TestCase):
    """What every capture writes into `crew_links`, against a fake manager."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.store = web_runtime_state.RuntimeStateStore(lambda: str(self.state_path))
        crew_history.reset()
        self.addCleanup(crew_history.reset)
        self.live_links = []
        patcher = patch.object(
            web_runtime_state.agent_results,
            "links_snapshot",
            side_effect=lambda: copy.deepcopy(self.live_links),
        )
        self.links_snapshot = patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = _FakeManager(
            {
                "default": [
                    _group("g1", _pane("lead"), _pane("shell", "terminal")),
                    _group("g2", _pane("helper"), _pane("tester")),
                ],
                "other": [_group("g9", _pane("stranger"))],
            }
        )

    def _stored(self, workspace_id="default"):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state["workspaces"][workspace_id]["crew_links"]

    def test_a_capture_stores_links_as_coordinates_and_autosave_keeps_them(self):
        self.live_links = [_link("lead", "helper"), _link("helper", "tester", round=2)]

        self.store.capture_workspace(self.manager, "default", origin="manual")
        saved = self._stored()
        # The erasure bug: an autosave that rewrote the slot without the field.
        self.store.capture_live_workspaces(self.manager, origin="auto")

        expected = [
            _stored_link(("g1", 0), ("g2", 0)),
            _stored_link(("g2", 0), ("g2", 1), round=2),
        ]
        self.assertEqual(saved, expected)
        self.assertEqual(self._stored(), expected)

    def test_a_voluntary_exit_capture_writes_them_too(self):
        self.live_links = [_link("lead", "helper")]

        self.store.capture_live_workspaces(self.manager, origin="manual")

        self.assertEqual(self._stored(), [_stored_link(("g1", 0), ("g2", 0))])

    def test_a_working_link_is_stored_as_ended_by_a_restart(self):
        self.live_links = [_link("lead", "helper", state="working")]

        self.store.capture_workspace(self.manager, "default")

        [entry] = self._stored()
        self.assertEqual(entry["state"], "ended")
        self.assertEqual(entry["reason"], crew_history.RESTARTED)

    def test_cross_workspace_and_non_agent_endpoints_are_dropped(self):
        self.live_links = [
            _link("lead", "stranger"),
            _link("lead", "shell"),
            _link("lead", "gone"),
            _link("lead", "tester"),
        ]

        self.store.capture_live_workspaces(self.manager)

        self.assertEqual(self._stored(), [_stored_link(("g1", 0), ("g2", 1))])
        self.assertEqual(self._stored("other"), [])

    def test_held_history_is_captured_and_a_live_round_wins_its_pair(self):
        crew_history.install(
            "default",
            [
                {"requester_session_id": "lead", "worker_session_id": "helper",
                 **_stored_link(("g1", 0), ("g2", 0), round=1)},
                {"requester_session_id": "helper", "worker_session_id": "tester",
                 **_stored_link(("g2", 0), ("g2", 1), round=3)},
            ],
        )
        self.live_links = [_link("lead", "helper", state="working", round=2)]

        self.store.capture_workspace(self.manager, "default")

        stored = {(entry["requester"]["group"], entry["requester"]["pane"]): entry
                  for entry in self._stored()}
        self.assertEqual(stored[("g2", 0)]["round"], 3)
        self.assertEqual(stored[("g1", 0)]["round"], 2)
        self.assertEqual(stored[("g1", 0)]["reason"], crew_history.RESTARTED)

    def test_the_stored_file_holds_no_report_receipt_handoff_or_link_id(self):
        self.live_links = [
            _link(
                "lead",
                "helper",
                text="SECRET-REPORT-TEXT",
                receipt="SECRET-RECEIPT",
                handoff_id="SECRET-HANDOFF",
            )
        ]

        self.store.capture_live_workspaces(self.manager)

        raw = self.state_path.read_text(encoding="utf-8")
        for needle in ("SECRET-", "link-lead", "link_id", "handoff_id", "receipt",
                       "session_id\": \"lead"):
            self.assertNotIn(needle, raw)
        for entry in self._stored():
            self.assertEqual(set(entry), _STORED_LINK_KEYS)

    def test_links_are_read_once_per_capture(self):
        self.store.capture_live_workspaces(self.manager)
        self.assertEqual(self.links_snapshot.call_count, 1)

        self.store.capture_workspace(self.manager, "default")
        self.assertEqual(self.links_snapshot.call_count, 2)

    def test_the_cap_keeps_the_newest_links(self):
        panes = [_pane(f"agent-{index}") for index in range(MAX_ASSIGNMENTS + 2)]
        self.manager.workspaces = {"default": [_group("g1", *panes)]}
        self.live_links = [
            _link(f"agent-{index}", f"agent-{index + 1}")
            for index in range(MAX_ASSIGNMENTS + 1)
        ]

        self.store.capture_workspace(self.manager, "default")

        stored = self._stored()
        self.assertEqual(len(stored), MAX_ASSIGNMENTS)
        self.assertEqual(stored[-1]["requester"], {"group": "g1", "pane": MAX_ASSIGNMENTS})


class CrewLinksReadTestCase(unittest.TestCase):
    """`_validate_slot`: a bad block degrades to `[]`; the slot still restores."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.store = web_runtime_state.RuntimeStateStore(lambda: str(self.state_path))

    def _write(self, **slot_fields):
        slot = {
            "workspace_id": "default",
            "label": "Crew",
            "origin": "manual",
            "saved_at": 1000.0,
            "groups": [
                _group("g1", _pane("a"), _pane("b")),
                _group("g2", _pane("c")),
            ],
        }
        slot.update(slot_fields)
        state = {
            "version": web_runtime_state.SCHEMA_VERSION,
            "workspaces": {"default": slot},
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def _read(self):
        slot = self.store.load_restorable_workspace("default")
        self.assertIsNotNone(slot)
        [summary] = self.store.list_restorable_workspaces()
        self.assertEqual((summary["group_count"], summary["pane_count"]), (2, 3))
        return slot["crew_links"]

    def test_a_valid_block_round_trips(self):
        links = [
            _stored_link(("g1", 0), ("g1", 1)),
            _stored_link(("g1", 1), ("g2", 0), state="ended", status="",
                         reported_at="", reason="pane closed"),
        ]
        self._write(crew_links=links)

        self.assertEqual(self._read(), links)

    def test_a_pre_field_slot_reads_as_no_links(self):
        self._write()

        self.assertEqual(self._read(), [])

    def test_a_bad_block_degrades_to_no_links(self):
        oversized = [_stored_link(("g1", 0), ("g1", 1))] * (MAX_ASSIGNMENTS + 1)
        for label, block in (
            ("string", "corrupt"),
            ("object", {"requester": {"group": "g1", "pane": 0}}),
            ("number", 7),
            ("oversized", oversized),
        ):
            with self.subTest(block=label):
                self._write(crew_links=block)
                self.assertEqual(self._read(), [])

    def test_invalid_or_unresolvable_entries_are_dropped(self):
        good = _stored_link(("g1", 0), ("g2", 0))
        self._write(
            crew_links=[
                _stored_link(("g1", 0), ("g1", 1), round="2"),
                _stored_link(("g1", 0), ("g1", 1), read="yes"),
                _stored_link(("g1", 0), ("g1", 1), state="working!"),
                _stored_link(("g1", 0), ("g1", 1), reason="made-up"),
                _stored_link(("g1", 0), ("g1", 1), status="bogus"),
                _stored_link(("g1", 0), ("g1", 2)),
                _stored_link(("g1", 0), ("gone", 0)),
                _stored_link(("g1", 0), ("g1", 0)),
                {"requester": "g1:0", "worker": {"group": "g1", "pane": 1}},
                "not an entry",
                good,
            ]
        )

        self.assertEqual(self._read(), [good])

    def test_a_dropped_group_takes_its_links_with_it(self):
        broken = _group("g2", _pane("c"))
        broken["sessions"][0]["explorer_open_tabs"] = "abc"
        self._write(
            groups=[_group("g1", _pane("a"), _pane("b")), broken],
            crew_links=[
                _stored_link(("g1", 0), ("g2", 0)),
                _stored_link(("g1", 0), ("g1", 1)),
            ],
        )

        slot = self.store.load_restorable_workspace("default")

        self.assertEqual(slot["crew_links"], [_stored_link(("g1", 0), ("g1", 1))])

    def test_a_stored_working_link_reads_as_ended_by_a_restart(self):
        self._write(
            crew_links=[
                _stored_link(("g1", 0), ("g1", 1), state="working", status="",
                             reported_at="")
            ]
        )

        [entry] = self._read()
        self.assertEqual((entry["state"], entry["reason"]), ("ended", "restarted"))


class CrewLinksLiveCaptureTestCase(_LiveAppMixin, unittest.TestCase):
    """The real save, autosave and voluntary-exit paths, then a restore."""

    def setUp(self):
        super().setUp()
        crew_history.reset()
        self.addCleanup(crew_history.reset)

    def _launch_crew(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "workspace_id": "default",
                "session_name": "Crew",
                "layout": "vertical",
                "sessions": [
                    {**_AGENT_PANE, "directory": str(self.repo_dir), "title": "Lead"},
                    {**_AGENT_PANE, "directory": str(self.repo_dir), "title": "Helper"},
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        group_id = response.get_json()["group_id"]
        lead, helper = api.session_manager.get_group_sessions(group_id)
        return group_id, lead.session_id, helper.session_id

    def _stored_links(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state["workspaces"]["default"]["crew_links"]

    def test_every_capture_intent_writes_the_links(self):
        group_id, lead, helper = self._launch_crew()
        expected = [_stored_link((group_id, 0), (group_id, 1))]

        with patch.object(
            web_runtime_state.agent_results,
            "links_snapshot",
            return_value=[_link(lead, helper)],
        ):
            saved = self.client.post(
                "/api/runtime-state/save", json={"workspace_id": "default"}
            )
            self.assertEqual(saved.status_code, 200, saved.get_json())
            self.assertEqual(self._stored_links(), expected)

            web_runtime_state.capture_live_workspaces(api.session_manager, origin="auto")
            self.assertEqual(self._stored_links(), expected)

            prepared = self.client.post(
                "/api/lifecycle/prepare", json={"action": "restart", "save": "workspaces"}
            )
            self.assertEqual(prepared.status_code, 200, prepared.get_json())
            self.assertEqual(self._stored_links(), expected)

    def test_a_corrupt_block_still_restores_the_workspace(self):
        self._launch_crew()
        saved = self.client.post(
            "/api/runtime-state/save", json={"workspace_id": "default"}
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["workspaces"]["default"]["crew_links"] = {"not": "a list"}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )

        self.assertEqual(restored.status_code, 200, restored.get_json())
        [group] = api.session_manager.get_workspace_groups("default")
        self.assertEqual(len(api.session_manager.get_group_sessions(group.group_id)), 2)


class CrewLinksRestoreTestCase(_LiveAppMixin, unittest.TestCase):
    """A restore installs the slot's links as history, keyed to the new panes."""

    def setUp(self):
        super().setUp()
        crew_history.reset()
        self.addCleanup(crew_history.reset)
        agent_results_store.reset()
        self.addCleanup(agent_results_store.reset)

    def _launch_group(self, name, *titles, workspace_id="default", **body):
        response = self.client.post(
            "/api/sessions",
            json={
                **body,
                "connection_mode": "wsl",
                "workspace_id": workspace_id,
                "session_name": name,
                "layout": "single" if len(titles) == 1 else "vertical",
                "sessions": [
                    {**_AGENT_PANE, "directory": str(self.repo_dir), "title": title}
                    for title in titles
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return [
            session.session_id
            for session in api.session_manager.get_group_sessions(
                response.get_json()["group_id"]
            )
        ]

    def _save_crew(self, **testers):
        """Leads (lead, helper) and Testers (tester, reviewer), three links, saved."""
        lead, helper = self._launch_group("Leads", "Lead", "Helper")
        tester, reviewer = self._launch_group("Testers", "Tester", "Reviewer", **testers)
        live = [
            _link(lead, helper),
            _link(helper, tester, state="working", round=2),
            _link(tester, reviewer),
        ]
        with patch.object(
            web_runtime_state.agent_results, "links_snapshot", return_value=live
        ):
            saved = self.client.post(
                "/api/runtime-state/save", json={"workspace_id": "default"}
            )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(len(self._stored_links()), 3)
        # A restart: the process, and every pane and store with it, is gone.
        api.session_manager.reset_sessions()
        crew_history.reset()

    def _stored_links(self):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state["workspaces"]["default"]["crew_links"]

    def _restore(self):
        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["workspaces"][0]

    def _panes(self):
        """Live pane ids by title, for the restored workspace."""
        return {
            session.title: session.session_id
            for group in api.session_manager.get_workspace_groups("default")
            for session in api.session_manager.get_group_sessions(group.group_id)
        }

    @staticmethod
    def _pairs(links):
        return [(link["requester_session_id"], link["worker_session_id"]) for link in links]

    def test_a_restore_installs_history_keyed_to_the_new_panes(self):
        self._save_crew()

        restored = self._restore()

        self.assertTrue(restored["restored"])
        panes = self._panes()
        links = crew_history.snapshot()
        self.assertEqual(
            self._pairs(links),
            [
                (panes["Lead"], panes["Helper"]),
                (panes["Helper"], panes["Tester"]),
                (panes["Tester"], panes["Reviewer"]),
            ],
        )
        self.assertTrue(all(link["restored"] for link in links))
        self.assertEqual(
            (links[1]["state"], links[1]["reason"], links[1]["round"]),
            ("ended", crew_history.RESTARTED, 2),
        )

    def test_restoring_the_workspace_again_replaces_its_history(self):
        self._save_crew()
        self._restore()
        # Gone without a close path running, so the first history is still held.
        api.session_manager.reset_sessions()
        self.assertEqual(crew_history.history.count(), 3)

        self._restore()

        panes = self._panes()
        self.assertEqual(crew_history.history.count(), 3)
        self.assertEqual(
            self._pairs(crew_history.snapshot())[0], (panes["Lead"], panes["Helper"])
        )

    def _assert_only_the_leads_link(self, restored):
        self.assertTrue(restored["restored"])
        self.assertEqual([group["started"] for group in restored["groups"]], [True, False])
        panes = self._panes()
        self.assertNotIn("Tester", panes)
        self.assertEqual(
            self._pairs(crew_history.snapshot()), [(panes["Lead"], panes["Helper"])]
        )

    def test_a_failed_group_drops_its_links(self):
        self._save_crew()
        real_launch = web_workspaces.launch_session_group

        def failing_testers(body):
            if body.get("session_name") == "Testers":
                return {"error": "Relaunch failed"}, 500
            return real_launch(body)

        with patch.object(web_workspaces, "launch_session_group", failing_testers):
            restored = self._restore()

        self._assert_only_the_leads_link(restored)

    def test_a_skipped_group_drops_its_links(self):
        preset = web_saved_sessions.upsert_saved_session(
            config={
                "connection_mode": "wsl",
                "terminal_count": 2,
                "layout": "vertical",
                "terminals": [{"title": "Tester"}, {"title": "Reviewer"}],
            },
            name="Testers",
        )
        self._save_crew(saved_session_id=preset["id"])
        # R6: by restore time the preset is live in a sibling workspace.
        api.session_manager.create_workspace("Other", "bbbbbbbbbbbb")
        self._launch_group(
            "Testers", "Elsewhere", workspace_id="bbbbbbbbbbbb", saved_session_id=preset["id"]
        )

        restored = self._restore()

        self.assertEqual(restored["groups"][1].get("skipped"), "already_live")
        self._assert_only_the_leads_link(restored)

    def test_a_pane_count_mismatch_maps_nothing_for_that_group(self):
        self._save_crew()
        real_launch = web_workspaces.launch_session_group

        def short_leads(body):
            payload, status = real_launch(body)
            if body.get("session_name") == "Leads":
                payload = {**payload, "sessions": payload["sessions"][:1]}
            return payload, status

        with patch.object(web_workspaces, "launch_session_group", short_leads):
            restored = self._restore()

        self.assertTrue(restored["restored"])
        panes = self._panes()
        self.assertEqual(
            self._pairs(crew_history.snapshot()), [(panes["Tester"], panes["Reviewer"])]
        )

    def test_closing_a_restored_pane_drops_its_history(self):
        self._save_crew()
        self._restore()
        panes = self._panes()

        response = self.client.delete(f"/api/sessions/{panes['Helper']}")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            self._pairs(crew_history.snapshot()), [(panes["Tester"], panes["Reviewer"])]
        )

    def test_restored_links_never_enter_the_result_store(self):
        self._save_crew()
        self._restore()
        panes = self._panes()
        self.assertEqual(crew_history.history.count(), 3)

        self.assertEqual(agent_results_store.count(), 0)
        self.assertEqual(agent_results_store.links_snapshot(), [])
        for title in ("Helper", "Tester", "Reviewer"):
            self.assertIsNone(agent_results_store.live_assignment(panes[title]))
        answer = self.client.get(
            f"/api/sessions/{panes['Lead']}/handoff-reports",
            query_string={"session_ids": panes["Helper"], "wait": "0"},
        )
        self.assertEqual(answer.status_code, 200, answer.get_json())
        payload = answer.get_json()
        self.assertEqual(payload["agents"], [])
        self.assertEqual(payload["unknown"], [panes["Helper"]])

    def test_a_failing_link_block_never_fails_the_restore(self):
        self._save_crew()
        crew_history.install(
            "default",
            [{"requester_session_id": "stale-a", "worker_session_id": "stale-b",
              **_stored_link(("g", 0), ("g", 1))}],
        )

        real_install = crew_history.install
        calls = []

        def install(workspace_id, links):
            calls.append(len(links))
            if len(calls) == 1:
                raise RuntimeError("SECRET-DETAIL")
            return real_install(workspace_id, links)

        with patch.object(web_workspaces.crew_history, "install", install), \
                self.assertLogs("web.workspaces", level="WARNING") as logs:
            restored = self._restore()

        self.assertTrue(restored["restored"])
        self.assertEqual([group["started"] for group in restored["groups"]], [True, True])
        self.assertEqual(len(self._panes()), 4)
        # The failed install held three links; the fallback installs none, and
        # the stale history for this workspace id is gone with it.
        self.assertEqual(calls, [3, 0])
        self.assertEqual(crew_history.history.count(), 0)
        self.assertTrue(any("RuntimeError" in line for line in logs.output))
        self.assertFalse(any("SECRET-DETAIL" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
