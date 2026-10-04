"""ISSUE-2026-063: real client snapshots through the exit flush/capture gate."""

import json
import unittest
from unittest.mock import patch

from tests import test_lifecycle
from tests.test_session_presentation import _NodeHarnessMixin
from web import api
from web import lifecycle as web_lifecycle


class PaneSynchronizationIntegrationTestCase(_NodeHarnessMixin, unittest.TestCase):
    setUp = test_lifecycle.LifecycleRouteTestCase.setUp
    _launch = test_lifecycle.LifecycleRouteTestCase._launch

    def _client_snapshot(self, descriptor, live=None):
        return self._run_node(r"""
            const { createPresentationController, planPaneSynchronization } = require(process.argv[2]);
            let descriptor = JSON.parse(process.argv[3]);
            const live = JSON.parse(process.argv[4]);
            if (live) {
                const original = descriptor.panes;
                const plan = planPaneSynchronization(
                    original.map(pane => pane.sessionId),
                    original.map(pane => ({ _session: pane.session, _paneType: pane.mode })),
                    live.sessions
                );
                descriptor = { ...descriptor, revision: live.group.presentation_revision,
                    panes: plan.entries.map(entry => entry.reused
                        ? original.find(pane => pane.sessionId === entry.session.session_id)
                        : { sessionId: entry.session.session_id, mode: entry.session.startup_mode }) };
            }
            const writes = [];
            const controller = createPresentationController({
                describeGroup: () => descriptor,
                sendGroup: payload => {
                    writes.push(payload);
                    return { status: 200, presentation_revision: payload.expected_revision + 1 };
                }
            });
            (async () => {
                await controller.flush([descriptor.groupId]);
                console.log(JSON.stringify(writes[writes.length - 1]));
            })().catch(error => { console.error(error); process.exit(1); });
        """, json.dumps(descriptor), json.dumps(live))

    def _scenario(self, change, save="workspaces", fail_after_sync=None):
        launched = self._launch()
        group_id = launched["group_id"]
        survivor = api.session_manager.get_group_sessions(group_id)[0]
        old = api.session_manager.create_session(
            group_id, host="WSL", mode="wsl", startup_mode="terminal", directory=str(self.repo_dir), title="Old"
        )
        descriptor = {
            "workspaceId": "default", "groupId": group_id, "revision": 0,
            "panes": [
                {"sessionId": survivor.session_id, "mode": "explorer", "session": survivor.to_dict(),
                 "explorer": {"openTabs": ["current.md"], "activeTab": "current.md", "treeOpen": True}},
                {"sessionId": old.session_id, "mode": "terminal", "session": old.to_dict()},
            ],
        }
        with api.session_manager.lock:
            group = api.session_manager.groups[group_id]
            if change in {"removed", "replaced"}:
                api.session_manager.sessions.pop(old.session_id)
                group.pane_order.remove(old.session_id)
                group.terminal_count -= 1
            if change in {"added", "replaced"}:
                api.session_manager.create_session(
                    group_id, host="WSL", mode="wsl", startup_mode="terminal", directory=str(self.repo_dir), title="New"
                )

        api.lifecycle_coordinator.join_workspace("a", "default", "window-a")
        api.lifecycle_coordinator.join_workspace("b", "default", "window-b")
        emissions = []
        payloads = []
        callback_errors = []

        def acknowledge(_event, request, room=None, **_kwargs):
            self.assertEqual(room, "workspace:default")
            emissions.append(request.get("synchronize", False))
            live = None
            if request.get("synchronize"):
                live = self.client.get(f"/api/sessions?workspace_id=default&group={group_id}").get_json()
            payload = self._client_snapshot(descriptor, live)
            # A revision learned by the stale page reaches the membership check.
            payload["expected_revision"] = group.presentation_revision
            response = self.client.post("/api/session-presentation", json=payload)
            payloads.append(payload)
            data = response.get_json()
            api.lifecycle_coordinator.acknowledge_flush("a", {
                **request, "ok": response.status_code == 200, "error": data.get("error"),
                "code": data.get("code"), "group_id": data.get("group_id"),
            })
            # Every participating window must answer, including one with no
            # currently rendered group. A failed second window still blocks exit.
            api.lifecycle_coordinator.acknowledge_flush("b", {
                **request, "ok": fail_after_sync != "window", "error": "Second window failed",
            })

        def emit(*args, **kwargs):
            try:
                acknowledge(*args, **kwargs)
            except Exception as exc:
                callback_errors.append(exc)
                raise

        with patch.object(api.socketio, "emit", side_effect=emit):
            failed = self.client.post("/api/lifecycle/prepare", json={"action": "close", "save": save})
            self.assertEqual(failed.status_code, 503, failed.get_json())
            self.assertEqual(failed.get_json()["errors"][0]["code"], "pane_membership_mismatch")
            self.assertFalse(failed.get_json()["ready_to_exit"])
            self.assertFalse(self.state_path.exists())
            self.assertEqual(survivor.explorer_open_tabs, [])
            if fail_after_sync == "disk":
                with patch.object(web_lifecycle, "capture_live_workspaces", side_effect=OSError("disk full")):
                    recovered = self.client.post("/api/lifecycle/prepare", json={
                        "action": "close", "save": save, "synchronize": True,
                    })
            else:
                recovered = self.client.post("/api/lifecycle/prepare", json={
                    "action": "close", "save": save, "synchronize": True,
                })

        if callback_errors:
            raise callback_errors[0]
        self.assertEqual(emissions, [False, True])
        self.assertEqual(survivor.explorer_open_tabs, ["current.md"])
        self.assertEqual(set(payloads[-1]["pane_order"]), set(group.pane_order))
        if fail_after_sync:
            self.assertEqual(recovered.status_code, 503)
            self.assertFalse(recovered.get_json()["ready_to_exit"])
            self.assertNotIn("decision_token", recovered.get_json())
            self.assertFalse(self.state_path.exists())
        else:
            self.assertEqual(recovered.status_code, 200, recovered.get_json())
            self.assertTrue(recovered.get_json()["ready_to_exit"])
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            stored = state["workspaces"]["default"]["groups"][0]["sessions"]
            self.assertEqual(len(stored), len(group.pane_order))
            self.assertEqual(stored[0]["explorer_open_tabs"], ["current.md"])
            self.assertEqual(self.saved_path.exists(), save == "sessions+workspaces")

    def test_removed_panes_can_recover_and_save_workspaces(self):
        self._scenario("removed")

    def test_added_panes_are_included_in_sessions_and_workspace_saves(self):
        self._scenario("added", "sessions+workspaces")

    def test_replacements_never_receive_the_removed_panes_presentation(self):
        self._scenario("replaced")

    def test_persistence_failure_after_synchronization_keeps_the_app_open(self):
        self._scenario("removed", fail_after_sync="disk")

    def test_another_window_failure_after_synchronization_keeps_the_app_open(self):
        self._scenario("replaced", fail_after_sync="window")


if __name__ == "__main__":
    unittest.main()
