"""Navigating live sessions for a tool: which tab to show, and where a group lives.

Pinned here, against the real routes and the live registry:

- **An intent names only what is still true.** A group another workspace now
  holds, a pane that closed, or a pane that is not in the group the caller
  read is refused before anything is recorded -- the pending list stays empty.
- **An activation is its own kind, with its own two outcomes** and a result
  that passes through a field list.
- **A tool moves its own group, or one it created, and nothing else unasked.**
  Any other group is a waivable lineage refusal whose question names the
  destination, and a refusal leaves the group exactly where it was. A move
  keeps every pane id.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import api  # noqa: E402
from web.window_intents import (  # noqa: E402
    ACTIVATE_KIND,
    ACTIVATED,
    BLOCKED,
    OPENED,
    SPLIT,
    WindowIntentStore,
    window_intents,
)


class _LiveRegistryTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        window_intents.reset()
        self.addCleanup(window_intents.reset)

    def _workspace(self, label):
        return api.session_manager.create_workspace(label).workspace_id

    def _group(self, workspace_id="default", name="Local", panes=1, created_by=""):
        group = api.session_manager.create_group(
            name=name,
            connection_mode="wsl",
            layout="single" if panes == 1 else "split",
            terminal_count=panes,
            workspace_id=workspace_id,
        )
        sessions = [
            api.session_manager.create_session(
                group.group_id,
                host="cmd",
                directory="C:/repo",
                mode="wsl",
                startup_mode="terminal",
                created_by_session_id=created_by,
            )
            for _ in range(panes)
        ]
        return group.group_id, [session.session_id for session in sessions]


class ActivateIntentStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = WindowIntentStore()

    def test_an_activation_names_the_workspace_the_group_and_the_pane(self):
        intent = self.store.open_activation("ws-1", "g-1", "pane-1", now=0.0)

        self.assertEqual(intent["kind"], ACTIVATE_KIND)
        self.assertEqual(
            (intent["workspace_id"], intent["group_id"], intent["session_id"]),
            ("ws-1", "g-1", "pane-1"),
        )
        self.assertEqual(intent["state"], "pending")

    def test_an_activation_reports_activated_or_blocked_and_nothing_else(self):
        for outcome, accepted in ((ACTIVATED, True), (BLOCKED, True), (OPENED, False), (SPLIT, False)):
            with self.subTest(outcome=outcome):
                intent = self.store.open_activation("ws-1", "g-1", now=0.0)
                self.store.claim(intent["intent_id"], now=0.0)
                recorded, _payload = self.store.record_result(intent["intent_id"], outcome, now=0.0)
                self.assertIs(recorded, accepted)

    def test_a_window_cannot_report_an_activations_verb(self):
        intent = self.store.open("ws-1", now=0.0)
        self.store.claim(intent["intent_id"], now=0.0)

        recorded, _payload = self.store.record_result(intent["intent_id"], ACTIVATED, now=0.0)

        self.assertFalse(recorded)

    def test_a_settled_activation_carries_what_the_page_shows_through_a_field_list(self):
        intent = self.store.open_activation("ws-1", "g-1", "pane-1", now=0.0)
        self.store.claim(intent["intent_id"], now=0.0)

        self.store.record_result(
            intent["intent_id"],
            ACTIVATED,
            result={
                "active_group_id": "g-1",
                "session_id": "pane-1",
                "pane_visible": True,
                "focused": True,
                "password": "hunter2",
            },
            now=0.0,
        )

        read = self.store.read(intent["intent_id"], now=0.0)
        self.assertEqual(
            read["result"],
            {"active_group_id": "g-1", "session_id": "pane-1", "pane_visible": True, "focused": True},
        )


class WindowTargetRouteTestCase(_LiveRegistryTestCase):
    def test_a_group_in_the_named_workspace_is_recorded(self):
        group_id, _ = self._group()

        response = self.client.post(
            "/api/windows/open", json={"workspace_id": "default", "group_id": group_id}
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["group_id"], group_id)

    def test_a_group_another_workspace_holds_is_refused_before_recording(self):
        elsewhere = self._workspace("Elsewhere")
        group_id, _ = self._group(elsewhere)

        response = self.client.post(
            "/api/windows/open", json={"workspace_id": "default", "group_id": group_id}
        )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertFalse(payload["changed"])
        # Where it is now, so the caller can ask for that instead.
        self.assertEqual(payload["workspace_id"], elsewhere)
        self.assertEqual(window_intents.pending(), [])

    def test_a_group_that_is_not_open_is_refused_before_recording(self):
        response = self.client.post(
            "/api/windows/open", json={"workspace_id": "default", "group_id": "gone"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(window_intents.pending(), [])

    def test_an_activation_reads_the_group_and_workspace_off_the_pane(self):
        elsewhere = self._workspace("Elsewhere")
        group_id, (pane,) = self._group(elsewhere)

        response = self.client.post("/api/windows/activate", json={"session_id": pane})

        self.assertEqual(response.status_code, 201)
        intent = response.get_json()
        self.assertEqual(intent["kind"], ACTIVATE_KIND)
        self.assertEqual(
            (intent["workspace_id"], intent["group_id"], intent["session_id"]),
            (elsewhere, group_id, pane),
        )

    def test_a_pane_that_is_not_in_the_stated_group_is_a_stale_read(self):
        group_id, _ = self._group(name="First")
        _other, (pane,) = self._group(name="Second")

        response = self.client.post(
            "/api/windows/activate", json={"group_id": group_id, "session_id": pane}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(window_intents.pending(), [])

    def test_a_group_that_moved_since_it_was_read_is_refused(self):
        group_id, _ = self._group()
        target = self._workspace("Moved to")
        api.session_manager.move_group(group_id, target)

        response = self.client.post(
            "/api/windows/activate", json={"workspace_id": "default", "group_id": group_id}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["workspace_id"], target)
        self.assertEqual(window_intents.pending(), [])

    def test_a_closed_pane_is_refused(self):
        response = self.client.post("/api/windows/activate", json={"session_id": "gone"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(window_intents.pending(), [])

    def test_an_activation_needs_a_group_or_a_pane(self):
        response = self.client.post("/api/windows/activate", json={"workspace_id": "default"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(window_intents.pending(), [])


class AgentMoveRouteTestCase(_LiveRegistryTestCase):
    def setUp(self):
        super().setUp()
        self.caller_group, (self.caller,) = self._group(name="Caller")
        self.target = self._workspace("Reviews")

    def _move(self, group_id, **body):
        body.setdefault("requested_by_session_id", self.caller)
        return self.client.post(f"/api/session-groups/{group_id}/agent-move", json=body)

    def test_the_callers_own_group_moves_and_keeps_its_panes(self):
        response = self._move(self.caller_group, target_workspace_id=self.target)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["moved"])
        self.assertEqual(payload["source_workspace_id"], "default")
        self.assertEqual(payload["current_workspace_id"], self.target)
        self.assertEqual(payload["group_name"], "Caller")
        # A tool is told both ends, not every group in both windows.
        self.assertNotIn("groups", payload)
        self.assertNotIn("source_groups", payload)
        self.assertEqual(api.session_manager.get_group(self.caller_group).workspace_id, self.target)
        self.assertIsNotNone(api.session_manager.get_session(self.caller))

    def test_a_group_the_caller_created_moves(self):
        group_id, panes = self._group(name="Workers", panes=2, created_by=self.caller)

        response = self._move(group_id, target_workspace_id=self.target)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["moved"])
        self.assertEqual(
            [session.session_id for session in api.session_manager.get_group_sessions(group_id)],
            panes,
        )

    def test_someone_elses_group_is_a_waivable_refusal_that_moves_nothing(self):
        group_id, _ = self._group(name="Theirs", panes=2)

        response = self._move(group_id, target_workspace_id=self.target)

        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertEqual(payload["gate"], "lineage")
        self.assertTrue(payload["waivable"])
        self.assertFalse(payload["changed"])
        # The question names both ends: a yes for one destination is not a yes
        # for another.
        question = payload["confirm"]["question"]
        self.assertIn("Theirs", question)
        self.assertIn("Reviews", question)
        self.assertIn("2 panes", question)
        self.assertEqual(payload["confirm"]["group"]["group_id"], group_id)
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, "default")

    def test_a_group_only_partly_created_by_the_caller_is_refused(self):
        group_id, _ = self._group(name="Mixed", created_by=self.caller)
        api.session_manager.create_session(
            group_id, host="cmd", directory="C:/repo", mode="wsl", startup_mode="terminal"
        )

        response = self._move(group_id, target_workspace_id=self.target)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, "default")

    def test_override_moves_someone_elses_group(self):
        group_id, _ = self._group(name="Theirs")

        response = self._move(group_id, target_workspace_id=self.target, override=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, self.target)

    def test_a_caller_that_closed_is_refused_and_override_does_not_help(self):
        group_id, _ = self._group(name="Theirs", created_by="closed-pane")

        response = self._move(
            group_id,
            target_workspace_id=self.target,
            requested_by_session_id="closed-pane",
            override=True,
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["waivable"])
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, "default")

    def test_a_request_that_names_no_caller_is_malformed(self):
        response = self.client.post(
            f"/api/session-groups/{self.caller_group}/agent-move",
            json={"target_workspace_id": self.target},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(api.session_manager.get_group(self.caller_group).workspace_id, "default")

    def test_a_move_with_no_destination_is_refused_rather_than_defaulted(self):
        other = self._workspace("Other")
        api.session_manager.move_group(self.caller_group, other)

        response = self._move(self.caller_group)

        self.assertEqual(response.status_code, 400)
        # Not quietly moved to `default`, which is what an empty destination
        # means to the launcher's transaction.
        self.assertEqual(api.session_manager.get_group(self.caller_group).workspace_id, other)

    def test_a_move_into_the_current_workspace_is_a_reported_no_op(self):
        response = self._move(self.caller_group, target_workspace_id="default")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["moved"])
        self.assertEqual(payload["current_workspace_id"], "default")
        self.assertEqual(len(api.session_manager.get_workspace_groups("default")), 1)

    def test_a_move_into_a_new_workspace_creates_it(self):
        response = self._move(self.caller_group, new_workspace=True, label="Split off")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["workspace_created"])
        self.assertEqual(
            api.session_manager.get_workspace(payload["current_workspace_id"]).label,
            "Split off",
        )

    def _between_gate_and_move(self, change):
        """Run ``change`` after the route's gate, before the transaction moves.

        The destination reservation sits between the two, so wrapping it lands
        exactly in the window the lock-held recheck exists to close.
        """
        original = api.session_manager.reserve_workspace_launch

        def reserve(workspace_id):
            change()
            return original(workspace_id)

        api.session_manager.reserve_workspace_launch = reserve
        self.addCleanup(setattr, api.session_manager, "reserve_workspace_launch", original)

    def _foreign_pane(self, group_id):
        return lambda: api.session_manager.create_session(
            group_id, host="cmd", directory="C:/repo", mode="wsl", startup_mode="terminal"
        )

    def test_a_foreign_pane_joining_after_the_gate_stops_the_move(self):
        group_id, _ = self._group(name="Workers", created_by=self.caller)
        self._between_gate_and_move(self._foreign_pane(group_id))

        response = self._move(group_id, target_workspace_id=self.target)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["changed"])
        self.assertEqual(api.session_manager.get_group(group_id).workspace_id, "default")

    def test_a_group_moved_elsewhere_after_the_gate_is_not_moved_again(self):
        elsewhere = self._workspace("Elsewhere")
        self._between_gate_and_move(
            lambda: api.session_manager.move_group(self.caller_group, elsewhere)
        )

        response = self._move(self.caller_group, target_workspace_id=self.target)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["workspace_id"], elsewhere)
        self.assertEqual(api.session_manager.get_group(self.caller_group).workspace_id, elsewhere)

    def test_a_refused_recheck_rolls_back_a_workspace_it_created(self):
        group_id, _ = self._group(name="Workers", created_by=self.caller)
        before = {item.workspace_id for item in api.session_manager.get_all_workspaces()}
        self._between_gate_and_move(self._foreign_pane(group_id))

        response = self._move(group_id, new_workspace=True, label="Never made")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            {item.workspace_id for item in api.session_manager.get_all_workspaces()}, before
        )

    def test_a_group_that_is_not_open_is_a_404(self):
        response = self._move("gone", target_workspace_id=self.target)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["changed"])

    def test_an_unknown_destination_moves_nothing(self):
        response = self._move(self.caller_group, target_workspace_id="aaaaaaaaaaaa")

        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["changed"])
        self.assertEqual(api.session_manager.get_group(self.caller_group).workspace_id, "default")


if __name__ == "__main__":
    unittest.main()
