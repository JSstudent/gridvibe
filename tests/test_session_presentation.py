"""Behavioral coverage for the ordered live-presentation transactions."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sessions.manager import SessionManager
from web import api
from web.session_presentation import apply_group_presentation

PRESENTATION_JS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "js"
    / "session-persistence.js"
)


class GroupPresentationTransactionTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager()
        installation = self.manager.install_session_group(
            sessions_config=[
                {
                    "host": "WSL",
                    "directory": "",
                    "mode": "wsl",
                    "startup_mode": "explorer",
                    "title": "Files",
                },
                {
                    "host": "Browser",
                    "directory": "",
                    "mode": "wsl",
                    "startup_mode": "browser",
                    "initial_command": "http://127.0.0.1:3000/",
                    "browser_tabs": ["http://127.0.0.1:3000/"],
                    "title": "Preview",
                },
            ],
            name="Mixed",
            connection_mode="wsl",
            layout="vertical",
        )
        self.group = installation.group
        self.explorer, self.browser = installation.sessions

    def _payload(self):
        return {
            "workspace_id": "default",
            "group_id": self.group.group_id,
            "expected_revision": 0,
            "pane_order": [self.browser.session_id, self.explorer.session_id],
            "panes": [
                {
                    "session_id": self.browser.session_id,
                    "browser_tabs": ["http://127.0.0.1:3000/new"],
                    "browser_active_tab": 0,
                },
                {
                    "session_id": self.explorer.session_id,
                    "explorer_open_tabs": ["docs/a.md"],
                    "explorer_active_tab": "docs/a.md",
                },
            ],
        }

    def test_transaction_deep_copies_and_snapshots_in_explicit_pane_order(self):
        payload = self._payload()

        response, status = apply_group_presentation(self.manager, payload)
        payload["pane_order"].reverse()
        payload["panes"][1]["explorer_open_tabs"].append("docs/mutated.md")

        self.assertEqual(status, 200)
        self.assertEqual(response["presentation_revision"], 1)
        sessions = self.manager.get_group_sessions(self.group.group_id)
        self.assertEqual(
            [session.session_id for session in sessions],
            [self.browser.session_id, self.explorer.session_id],
        )
        self.assertEqual(self.explorer.explorer_open_tabs, ["docs/a.md"])
        snapshot = self.manager.snapshot_live_workspaces()["default"]["groups"][0]
        self.assertEqual(
            [session["title"] for session in snapshot["sessions"]],
            ["Preview", "Files"],
        )
        self.assertEqual(
            self.browser.initial_command,
            "http://127.0.0.1:3000/new",
        )

    def test_invalid_mode_field_rejects_the_whole_batch_before_mutation(self):
        payload = self._payload()
        payload["panes"][1]["browser_tabs"] = ["http://127.0.0.1:3000/wrong"]
        payload["panes"][1]["browser_active_tab"] = 0

        response, status = apply_group_presentation(self.manager, payload)

        self.assertEqual(status, 400)
        self.assertIn("invalid for explorer", response["error"])
        self.assertEqual(self.group.presentation_revision, 0)
        self.assertEqual(self.group.pane_order, [
            self.explorer.session_id,
            self.browser.session_id,
        ])
        self.assertEqual(self.explorer.explorer_open_tabs, [])
        self.assertEqual(
            self.browser.browser_tabs,
            ["http://127.0.0.1:3000/"],
        )

    def test_missing_duplicate_and_incomplete_id_sets_are_rejected(self):
        missing = self._payload()
        missing["panes"] = missing["panes"][:1]
        duplicate = self._payload()
        duplicate["pane_order"] = [self.browser.session_id, self.browser.session_id]
        for payload in (missing, duplicate):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    apply_group_presentation(self.manager, payload)

        unknown = self._payload()
        unknown["pane_order"][0] = "unknown"
        unknown["panes"][0]["session_id"] = "unknown"
        response, status = apply_group_presentation(self.manager, unknown)
        self.assertEqual(status, 400, response)
        self.assertEqual(self.group.presentation_revision, 0)


class PresentationRouteTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        installation = api.session_manager.install_session_group(
            sessions_config=[
                {
                    "host": "WSL",
                    "directory": "",
                    "mode": "wsl",
                    "startup_mode": "explorer",
                }
            ],
            name="Files",
            connection_mode="wsl",
            layout="single",
        )
        self.group = installation.group
        self.session = installation.sessions[0]

    def test_presentation_update_does_not_trigger_runtime_state_capture(self):
        with patch.object(api, "capture_workspace") as capture:
            response = self.client.post(
                "/api/session-presentation",
                json={
                    "workspace_id": "default",
                    "group_id": self.group.group_id,
                    "expected_revision": 0,
                    "pane_order": [self.session.session_id],
                    "panes": [
                        {
                            "session_id": self.session.session_id,
                            "explorer_open_tabs": ["docs/a.md"],
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        capture.assert_not_called()

    def test_workspace_chrome_uses_a_separate_revisioned_transaction(self):
        legacy = self.client.patch(
            "/api/workspaces/default/ui-state",
            json={"topbar_visible": False},
        )
        self.assertEqual(legacy.status_code, 200, legacy.get_json())
        self.assertEqual(legacy.get_json()["presentation_revision"], 1)

        stale = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 0,
                "topbar_visible": True,
            },
        )
        accepted = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 1,
                "topbar_visible": True,
            },
        )

        self.assertEqual(stale.status_code, 409, stale.get_json())
        self.assertEqual(stale.get_json()["presentation_revision"], 1)
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertEqual(accepted.get_json()["presentation_revision"], 2)
        groups = self.client.get("/api/session-groups").get_json()
        self.assertTrue(groups["topbar_visible"])
        self.assertEqual(groups["workspace_presentation_revision"], 2)


class PresentationQueueTestCase(unittest.TestCase):
    def test_queue_serializes_retries_and_continuous_updates(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        harness = r"""
            const { createPresentationQueue } = require(process.argv[2]);
            const sent = [];
            const pending = [];
            const reconciled = [];
            const fetched = [];
            const queue = createPresentationQueue({
                send(payload) {
                    sent.push(payload);
                    return new Promise((resolve) => pending.push(resolve));
                },
                fetchCurrent(groupId) {
                    fetched.push(groupId);
                    return { presentation_revision: 4, server_value: 'current' };
                },
                onReconciled(groupId) { reconciled.push(groupId); }
            });

            (async () => {
                queue.enqueue('g1', { expected_revision: 0, value: 'a' }, { continuous: true });
                queue.enqueue('g1', { expected_revision: 0, value: 'b' }, { continuous: true });
                await new Promise((resolve) => setTimeout(resolve, 25));
                const beforeFlush = sent.length;
                const flushed = queue.flush('g1');
                await new Promise((resolve) => setImmediate(resolve));
                pending[0]({ status: 409, presentation_revision: 4 });
                await new Promise((resolve) => setImmediate(resolve));
                pending[1]({ status: 200, presentation_revision: 5 });
                await flushed;
                process.stdout.write(JSON.stringify({
                    beforeFlush,
                    values: sent.map((item) => item.value),
                    revisions: sent.map((item) => item.expected_revision),
                    fetched,
                    reconciled,
                    latest: queue.latest('g1'),
                    revision: queue.revision('g1')
                }));
            })();
        """
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "queue.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script_path), str(PRESENTATION_JS)],
                capture_output=True,
                text=True,
                check=True,
            )
        result = json.loads(completed.stdout)

        self.assertEqual(result["beforeFlush"], 0)
        self.assertEqual(result["values"], ["b", "b"])
        self.assertEqual(result["revisions"], [0, 4])
        self.assertEqual(result["fetched"], ["g1"])
        self.assertEqual(result["reconciled"], ["g1"])
        self.assertEqual(result["latest"]["value"], "b")
        self.assertEqual(result["revision"], 5)


if __name__ == "__main__":
    unittest.main()
