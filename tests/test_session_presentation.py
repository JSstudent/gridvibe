"""Behavioral coverage for the ordered live-presentation transactions.

Stage 2 of `docs/session_group_persistence_restore_audit_2026-08-07.md` added
the server transaction and the DOM-free client queue; Stage 3 wired the live
page to them. The client half is executed in Node rather than asserted as
source text, and the payload it produces is posted at the real route so the
two halves cannot drift apart silently.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sessions.manager import SessionManager
from web import api
from web import runtime_state as web_runtime_state
from web.session_presentation import apply_group_presentation

PRESENTATION_JS = (
    Path(__file__).resolve().parent.parent
    / "web"
    / "static"
    / "js"
    / "session-persistence.js"
)


class _NodeHarnessMixin:
    """Run one snippet against the real session-persistence.js module."""

    def _run_node(self, harness: str, *args: str):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [node, str(script_path), str(PRESENTATION_JS), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def _build_group_payload(self, descriptor):
        """Return what the page would actually put on the wire for a group."""
        return self._run_node(
            """
            const { buildGroupPresentationPayload } = require(process.argv[2]);
            const descriptor = JSON.parse(process.argv[3]);
            process.stdout.write(
                JSON.stringify(buildGroupPresentationPayload(descriptor))
            );
            """,
            json.dumps(descriptor),
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
        """Stage 3 retired `PATCH /ui-state`; this is now the only chrome writer."""
        self.assertNotIn(
            "/api/workspaces/<workspace_id>/ui-state",
            {str(rule) for rule in api.app.url_map.iter_rules()},
        )
        first = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 0,
                "topbar_visible": False,
            },
        )
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(first.get_json()["presentation_revision"], 1)

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


# ==================== Stage 3 — the page wired to the transaction ====================


class GroupPresentationPayloadTestCase(_NodeHarnessMixin, unittest.TestCase):
    """What the live page puts on the wire for one group.

    The mapping from "what is on screen" to the bounded transaction body is the
    part of Stage 3 that can be wrong quietly: a missing field is simply never
    restored. It lives in the DOM-free module so it can be executed here.
    """

    EXPLORER = {
        "sessionId": "explorer-1",
        "mode": "explorer",
        "explorer": {
            "treeOpen": True,
            "gitOpen": False,
            "searchOpen": True,
            "openTabs": ["docs/a.md", "web/b.js"],
            "activeTab": "web/b.js",
            "tabViews": {
                "web/b.js": {"mode": "source", "scroll": 0.25, "identity": "abc"},
                "__preview__": {"dir": "docs"},
            },
            "mdPreset": "paper",
            "mdFont": "serif",
            "sourceFont": "cascadia-code",
            "theme": "light",
        },
    }
    BROWSER = {
        "sessionId": "browser-1",
        "mode": "browser",
        "browser": {"tabs": ["http://127.0.0.1:3000", "http://127.0.0.1:4000"], "activeTab": 1},
    }
    TERMINAL = {"sessionId": "term-1", "mode": "terminal"}

    def _descriptor(self, **overrides):
        descriptor = {
            "workspaceId": "default",
            "groupId": "g1",
            "revision": 7,
            "panes": [self.BROWSER, self.EXPLORER, self.TERMINAL],
        }
        descriptor.update(overrides)
        return descriptor

    def test_every_in_scope_field_travels_in_visual_pane_order(self):
        payload = self._build_group_payload(self._descriptor())

        self.assertEqual(payload["workspace_id"], "default")
        self.assertEqual(payload["group_id"], "g1")
        self.assertEqual(payload["expected_revision"], 7)
        # Visual order, not the order the sessions happen to have been created.
        self.assertEqual(payload["pane_order"], ["browser-1", "explorer-1", "term-1"])
        by_id = {pane["session_id"]: pane for pane in payload["panes"]}
        self.assertEqual(
            by_id["browser-1"],
            {
                "session_id": "browser-1",
                "browser_tabs": ["http://127.0.0.1:3000", "http://127.0.0.1:4000"],
                "browser_active_tab": 1,
            },
        )
        self.assertEqual(
            by_id["explorer-1"],
            {
                "session_id": "explorer-1",
                "explorer_tree_open": True,
                "explorer_git_open": False,
                "explorer_search_open": True,
                "explorer_open_tabs": ["docs/a.md", "web/b.js"],
                "explorer_active_tab": "web/b.js",
                "explorer_tab_views": self.EXPLORER["explorer"]["tabViews"],
                "explorer_md_preset": "paper",
                "explorer_md_font": "serif",
                "explorer_source_font": "cascadia-code",
                "explorer_theme": "light",
            },
        )
        # A terminal pane holds its place in the order and contributes nothing.
        self.assertEqual(by_id["term-1"], {"session_id": "term-1"})
        # Presentation only: no launch field, no credential, no status.
        for forbidden in ("password", "host", "username", "directory", "initial_command"):
            self.assertNotIn(forbidden, json.dumps(payload))

    def test_custom_split_geometry_rides_along_and_a_standard_layout_does_not(self):
        geometry = {
            "class_name": "layout-split-local",
            "split_slot_rects": [
                {"originSlot": 0, "x": 1, "y": 1, "w": 1, "h": 1},
                {"originSlot": 1, "x": 2, "y": 1, "w": 1, "h": 1},
                {"originSlot": 2, "x": 3, "y": 1, "w": 1, "h": 1},
            ],
            "split_column_weights": [2.0, 1.0, 1.0],
            "split_row_weights": [1.0],
            "original_split_slot_count": 3,
        }

        with_geometry = self._build_group_payload(
            self._descriptor(workspaceLayout=geometry)
        )
        without = self._build_group_payload(self._descriptor())

        self.assertEqual(with_geometry["workspace_layout"], geometry)
        # Omitted, not null: a standard layout must not overwrite stored
        # geometry with a synthesised split that only looks equivalent.
        self.assertNotIn("workspace_layout", without)

    def test_an_unrenderable_pane_sends_nothing_rather_than_a_partial_group(self):
        payload = self._build_group_payload(
            self._descriptor(panes=[self.BROWSER, {"mode": "terminal"}])
        )

        self.assertIsNone(payload)

    def test_a_browser_pane_with_no_strip_yet_cannot_blank_the_stored_one(self):
        payload = self._build_group_payload(
            self._descriptor(
                panes=[{"sessionId": "browser-1", "mode": "browser", "browser": {"tabs": []}}]
            )
        )

        self.assertEqual(payload["panes"], [{"session_id": "browser-1"}])


class PresentationWiringTestCase(_NodeHarnessMixin, unittest.TestCase):
    """The page's payload against the real route, and the exact-save barrier."""

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

        launched = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Mixed",
                "layout": "vertical",
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    },
                    {
                        "title": "Preview",
                        "startup_mode": "browser",
                        "initial_command": "http://127.0.0.1:3000",
                    },
                ],
            },
        )
        self.assertEqual(launched.status_code, 201, launched.get_json())
        self.group_id = launched.get_json()["group_id"]
        self.explorer_id, self.browser_id = [
            session.session_id
            for session in api.session_manager.get_group_sessions(self.group_id)
        ]

    def test_the_pages_own_payload_is_accepted_and_captured_by_save_workspace(self):
        """Stage 3 items 1-3 and 5, end to end. Regression matrix rows 2 and 11.

        The server's last-known state is deliberately older than the client's,
        which is the whole point of SGP-01: a capture that reads only the
        manager records a coherent snapshot of the wrong moment.
        """
        api.session_manager.update_session_metadata(
            self.explorer_id,
            explorer_open_tabs=["docs/old.md"],
            explorer_active_tab="docs/old.md",
        )
        payload = self._build_group_payload(
            {
                "workspaceId": "default",
                "groupId": self.group_id,
                "revision": 0,
                # Dragged: the browser pane is now first.
                "panes": [
                    {
                        "sessionId": self.browser_id,
                        "mode": "browser",
                        "browser": {
                            "tabs": ["http://127.0.0.1:3000", "http://127.0.0.1:8080"],
                            "activeTab": 1,
                        },
                    },
                    {
                        "sessionId": self.explorer_id,
                        "mode": "explorer",
                        "explorer": {
                            "treeOpen": True,
                            "gitOpen": True,
                            "searchOpen": False,
                            "openTabs": ["docs/new.md"],
                            "activeTab": "docs/new.md",
                            "tabViews": {
                                "docs/new.md": {
                                    "mode": "preview",
                                    "scroll": 0.5,
                                    "identity": "id-1",
                                }
                            },
                            "mdPreset": "paper",
                            "mdFont": "serif",
                            "sourceFont": "cascadia-code",
                            "theme": "light",
                        },
                    },
                ],
                "workspaceLayout": {
                    "class_name": "layout-split-local",
                    "split_slot_rects": [
                        {"originSlot": 0, "x": 1, "y": 1, "w": 1, "h": 1},
                        {"originSlot": 1, "x": 2, "y": 1, "w": 1, "h": 1},
                    ],
                    "split_column_weights": [3.0, 1.0],
                    "split_row_weights": [1.0],
                    "original_split_slot_count": 2,
                },
            }
        )

        accepted = self.client.post("/api/session-presentation", json=payload)
        self.assertEqual(accepted.status_code, 200, accepted.get_json())

        saved = self.client.post(
            "/api/runtime-state/save",
            json={"workspace_id": "default", "topbar_visible": False},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())

        slot = json.loads(self.state_path.read_text(encoding="utf-8"))
        stored_group = slot["workspaces"]["default"]["groups"][0]
        self.assertEqual(
            [session["title"] for session in stored_group["sessions"]],
            ["Preview", "Files"],
        )
        self.assertEqual(
            stored_group["workspace_layout"]["split_column_weights"], [3.0, 1.0]
        )
        stored = {
            session["title"]: session for session in stored_group["sessions"]
        }
        self.assertEqual(stored["Files"]["explorer_open_tabs"], ["docs/new.md"])
        self.assertEqual(stored["Files"]["explorer_theme"], "light")
        self.assertTrue(stored["Files"]["explorer_git_open"])
        self.assertEqual(
            stored["Preview"]["browser_tabs"],
            ["http://127.0.0.1:3000", "http://127.0.0.1:8080"],
        )
        self.assertEqual(stored["Preview"]["browser_active_tab"], 1)
        self.assertFalse(slot["workspaces"]["default"]["topbar_visible"])
        self.assertNotIn("password", self.state_path.read_text(encoding="utf-8"))

    def test_the_terminals_page_loads_the_persistence_module(self):
        """Without the module every queue is inert and nothing is enqueued."""
        page = self.client.get("/terminals").get_data(as_text=True)
        asset = self.client.get("/static/js/session-persistence.js")

        self.assertIn("js/session-persistence.js", page)
        self.assertEqual(asset.status_code, 200)
        asset.close()


class PresentationControllerTestCase(_NodeHarnessMixin, unittest.TestCase):
    """The controller the page wires its change events to, executed for real."""

    CONTROLLER_HARNESS = r"""
        const { createPresentationController } = require(process.argv[2]);
        const plan = JSON.parse(process.argv[3]);

        const sentGroup = [];
        const sentWorkspace = [];
        const pending = [];
        let tabs = ['a'];
        let topbarVisible = true;
        let groupFailures = plan.groupFailures || 0;

        const controller = createPresentationController({
            describeGroup: (groupId) => ({
                workspaceId: 'default',
                groupId,
                revision: 0,
                panes: [{
                    sessionId: 's1',
                    mode: 'browser',
                    browser: { tabs: tabs.slice(), activeTab: 0 }
                }]
            }),
            describeWorkspace: () => ({
                workspaceId: 'default',
                revision: 0,
                topbarVisible
            }),
            sendGroup(payload) {
                sentGroup.push(payload.panes[0].browser_tabs.slice());
                if (groupFailures > 0) {
                    groupFailures -= 1;
                    return { status: 503, error: 'disk full' };
                }
                if (plan.holdGroupReplies) {
                    return new Promise((resolve) => pending.push(resolve));
                }
                return { status: 200, presentation_revision: sentGroup.length };
            },
            sendWorkspace(payload) {
                sentWorkspace.push(payload.topbar_visible);
                return { status: 200, presentation_revision: sentWorkspace.length };
            }
        });

        const finish = (extra) => process.stdout.write(JSON.stringify(
            Object.assign({ sentGroup, sentWorkspace }, extra)
        ));
        const tick = () => new Promise((resolve) => setImmediate(resolve));
    """

    def _run_controller(self, body, plan=None):
        return self._run_node(
            self.CONTROLLER_HARNESS + body, json.dumps(plan or {})
        )

    def test_the_flush_barrier_recaptures_the_newest_state_before_resolving(self):
        """Stage 3 item 5: an exact save, not "whatever the server last heard"."""
        result = self._run_controller(
            r"""
            (async () => {
                controller.noteGroupChange('g1');
                await tick();
                const sentBeforeTheUserTypedMore = sentGroup.length;

                // The user opens another tab after that request left.
                tabs = ['a', 'b'];
                const barrier = controller.flush(['g1']);
                pending[0]({ status: 200, presentation_revision: 1 });
                await tick();
                pending[1]({ status: 200, presentation_revision: 2 });
                const flushed = await barrier;

                finish({ sentBeforeTheUserTypedMore, flushed });
            })();
            """,
            {"holdGroupReplies": True},
        )

        self.assertEqual(result["sentBeforeTheUserTypedMore"], 1)
        # The barrier re-captured rather than waiting on the in-flight request.
        self.assertEqual(result["sentGroup"], [["a"], ["a", "b"]])
        self.assertEqual(result["flushed"], ["g1"])
        # Window chrome is part of the same barrier.
        self.assertEqual(result["sentWorkspace"], [True])

    def test_a_failed_write_fails_the_barrier_and_is_repaired_not_abandoned(self):
        """Stage 3 item 6 and regression matrix row 34.

        A dropped write used to leave the server holding the older value for
        the next autosave to commit; a bounded re-enqueue repairs it, and the
        barrier still refuses to report success.
        """
        result = self._run_controller(
            r"""
            (async () => {
                let barrierFailed = false;
                try {
                    await controller.flush(['g1']);
                } catch (error) {
                    barrierFailed = true;
                }
                finish({ barrierFailed });
            })();
            """,
            {"groupFailures": 99},
        )

        self.assertTrue(result["barrierFailed"])
        # One real attempt plus a bounded repair budget, never an endless loop.
        self.assertEqual(len(result["sentGroup"]), 3)

    def test_a_repair_stops_once_the_server_accepts_the_write(self):
        result = self._run_controller(
            r"""
            (async () => {
                await controller.flush(['g1']);
                finish({});
            })();
            """,
            {"groupFailures": 1},
        )

        self.assertEqual(result["sentGroup"], [["a"], ["a"]])

    def test_two_fast_toggles_leave_the_server_holding_the_newer_value(self):
        """Regression matrix row 34, ordering half."""
        result = self._run_controller(
            r"""
            (async () => {
                topbarVisible = false;
                controller.noteWorkspaceChange();
                topbarVisible = true;
                controller.noteWorkspaceChange();
                await controller.flush([]);
                finish({});
            })();
            """
        )

        self.assertEqual(result["sentWorkspace"], [True])

    def test_a_refetched_revision_cannot_overwrite_an_in_flight_write(self):
        """A groups refetch rebases the queue only when it owns no ordering."""
        result = self._run_controller(
            r"""
            (async () => {
                controller.noteGroupChange('g1');
                const rebasedWhileInFlight = controller.setGroupRevision('g1', 40);
                await tick();
                pending[0]({ status: 200, presentation_revision: 1 });
                await tick();
                const rebasedWhenIdle = controller.setGroupRevision('g1', 40);
                finish({
                    rebasedWhileInFlight,
                    rebasedWhenIdle,
                    revision: controller.groupRevision('g1')
                });
            })();
            """,
            {"holdGroupReplies": True},
        )

        self.assertFalse(result["rebasedWhileInFlight"])
        self.assertTrue(result["rebasedWhenIdle"])
        self.assertEqual(result["revision"], 40)


if __name__ == "__main__":
    unittest.main()
