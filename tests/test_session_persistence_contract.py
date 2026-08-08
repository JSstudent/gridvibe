"""Stage 0 of the session-group persistence and restore audit (2026-08-07).

`docs/session_group_persistence_restore_audit_2026-08-07.md` Stage 0 asks for
the snapshot contract to be frozen as executable tests **before** any
production change, and for every one of them to assert the *target* behaviour
rather than the current defect. A suite that encoded today's bug would have to
be inverted later, which destroys its value as a stable contract and misleads
anyone bisecting through the stages.

Each test is therefore written against the behaviour its stage must deliver and
carries `@unittest.expectedFailure` until that stage lands. **Removing the
decorator is part of the stage, not a separate cleanup.** An unexpected success
fails the run, so a decorator left behind after a stage ships is loud rather
than silent.

The module also freezes the *names* the later stages must use, as module-level
constants below. They are provisional in exactly one sense: a stage may rename
one, but only by changing the constant here in the same commit — never by
adding a second parallel surface.

Stage 0 item → test case:

1. Save Workspace captures the client snapshot      `SaveWorkspaceFlushBarrierTestCase`
2. Out-of-order browser presentation writes         `BrowserPresentationOrderingTestCase`
3. Pane reorder + split-track resize round trip     `PaneOrderAndSplitGeometryTestCase`
4. Explorer rooted at a parent, viewing a child     `ExplorerRootRestoreTestCase`
5. The full explorer presentation fixture           `ExplorerPresentationFixtureTestCase`
6. Lowering `max_sessions` is non-destructive       `LaunchCapacityNondestructiveTestCase`
7. Malformed nested presentation state              `MalformedPresentationValidationTestCase`
8. The close/restart action matrix                  `LifecycleActionMatrixTestCase`
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from web import api
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions

# ── The surfaces Stage 2 and Stage 4 must build (audit "Recommended architecture") ──

#: One bounded, ordered group-presentation transaction (Stage 2). The body is
#: the audit's payload: workspace/group identity, an expected revision, pane
#: order, layout/geometry, and presentation-only pane fields.
PRESENTATION_ROUTE = "/api/session-presentation"

#: The import-cycle-safe home of the one canonical presentation normalizer,
#: shared by saved presets, runtime-state read validation, the live
#: presentation route, and launch preparation (SGP-07).
PRESENTATION_MODULE = "web.session_presentation"

#: The DOM-free frontend capture/queue module (Stage 2). It must be
#: `require()`-able from Node so its ordering rules can be executed, not
#: asserted as source text.
PRESENTATION_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "session-persistence.js"

#: The one lifecycle preparation service behind explicit close, manual restart,
#: update restart, and native launcher close (Stage 4).
LIFECYCLE_ROUTE = "/api/lifecycle/prepare"

#: The three save choices the lifecycle modal offers, plus Cancel (which never
#: reaches the server).
LIFECYCLE_SAVE_NONE = "none"
LIFECYCLE_SAVE_WORKSPACES = "workspaces"
LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES = "sessions+workspaces"


#: The Stage 0 item 5 explorer fixture: one pane carrying every field the
#: audit's product decision 1 places inside the snapshot boundary. Preview plus
#: pinned tabs, an active Diff tab, per-panel horizontal *and* vertical scroll,
#: zoom, per-view wrap opt-outs, Markdown folds, theme/fonts, all three sidebar
#: open flags, sidebar width, and Files-tree expansion.
#:
#: Deliberately absent, per product decisions 2-4: dirty editor buffers, Git
#: commit drafts, and any search/file-find query, result, or selection.
EXPLORER_PRESENTATION_FIXTURE = {
    "explorer_tree_open": True,
    "explorer_git_open": True,
    "explorer_search_open": True,
    "explorer_sidebar_width": 320,
    "explorer_tree_expanded": ["docs", "docs/guides", "web/static"],
    "explorer_open_tabs": ["docs/guides/intro.md", "web/static/js/shared.js"],
    "explorer_active_tab": "web/static/js/shared.js",
    "explorer_md_preset": "compact",
    "explorer_md_font": "serif",
    "explorer_source_font": "mono",
    "explorer_theme": "light",
    "explorer_tab_views": {
        # Preview keeps its own browsed directory and its own scroll.
        "__preview__": {
            "version": 2,
            "intent": {"mode": "preview"},
            "dir": "docs/guides",
            "font_size": 16,
            "wrap": {"source": True, "preview": True, "diff": False},
            "scroll": {"directory": {"x": 0.0, "y": 0.25}},
        },
        "docs/guides/intro.md": {
            "version": 2,
            "intent": {"mode": "preview"},
            "content_revision": "sha256:intro",
            "font_size": 18,
            "wrap": {"source": False, "preview": True, "diff": False},
            "scroll": {
                "source": {"x": 0.1, "y": 0.30},
                "preview": {"x": 0.0, "y": 0.60},
            },
            "folds": [12, 44],
        },
        "web/static/js/shared.js": {
            "version": 2,
            # The active tab is a staged Diff: intent is durable, and its
            # identity must track the *rendered* diff, not the working file.
            "intent": {"mode": "diff", "diff_mode": "staged"},
            "content_revision": "sha256:index-abc",
            "font_size": 14,
            "wrap": {"source": False, "preview": True, "diff": False},
            "scroll": {
                "source": {"x": 0.0, "y": 0.10},
                "diff": {"x": 0.20, "y": 0.40},
            },
        },
    },
}


class _PersistencePathsMixin:
    """Redirect both persistence products at a per-test temporary directory."""

    def _isolate_state(self):
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

    def _launch_explorer(self, panes=1, workspace_id="default", **overrides):
        """Launch one local explorer group; explorer panes need no real shell."""
        body = {
            "connection_mode": "wsl",
            "session_name": overrides.pop("session_name", "Files"),
            "workspace_id": workspace_id,
            "layout": "single" if panes == 1 else "grid",
            "sessions": [
                {
                    "directory": str(self.repo_dir),
                    "title": f"Files {index + 1}",
                    "startup_mode": "explorer",
                }
                for index in range(panes)
            ],
        }
        body.update(overrides)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def _session_ids(self, group_id):
        return [
            session.session_id
            for session in api.session_manager.get_group_sessions(group_id)
        ]

    def _save_workspace(self, workspace_id="default", **body):
        return self.client.post(
            "/api/runtime-state/save",
            json={"workspace_id": workspace_id, **body},
        )

    def _stored_slot(self, workspace_id="default"):
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return state["workspaces"][workspace_id]

    def _stored_sessions(self, workspace_id="default", group_index=0):
        return self._stored_slot(workspace_id)["groups"][group_index]["sessions"]


# ==================== Item 1 — SGP-01 (Stage 2 + Stage 3) ====================


class SaveWorkspaceFlushBarrierTestCase(_PersistencePathsMixin, unittest.TestCase):
    """Save Workspace must be a point-in-time snapshot, not "last heard".

    SGP-01: autosave and Save Workspace serialize only `SessionManager`, while
    the browser owns explorer tabs/views, pane order, and split geometry. The
    durable file can be internally consistent and still be an out-of-date
    picture of the screen. Stage 2 adds the canonical presentation transaction;
    Stage 3 makes Save Workspace await its flush.

    Regression matrix rows 1-3 and 11.
    """

    def setUp(self):
        self._isolate_state()

    def test_save_workspace_captures_the_acknowledged_client_snapshot(self):
        """Stage 2 backend transaction; Stage 3 wires the actual flush. Matrix row 11."""
        launched = self._launch_explorer()
        group_id = launched["group_id"]
        session_id = self._session_ids(group_id)[0]
        # What the server last heard: an older tab strip.
        api.session_manager.update_session_metadata(
            session_id,
            explorer_open_tabs=["docs/old.md"],
            explorer_active_tab="docs/old.md",
        )

        # What the user actually has on screen, delivered through the one
        # ordered presentation transaction.
        accepted = self.client.post(
            PRESENTATION_ROUTE,
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": 0,
                "pane_order": [session_id],
                "panes": [
                    {
                        "session_id": session_id,
                        "explorer_open_tabs": ["docs/new.md"],
                        "explorer_active_tab": "docs/new.md",
                    }
                ],
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.get_json())

        saved = self._save_workspace()

        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(
            self._stored_sessions()[0]["explorer_open_tabs"], ["docs/new.md"]
        )

    def test_presentation_route_refuses_launch_and_credential_fields(self):
        """Stage 2 rule 6: presentation only — never a launch or a secret."""
        launched = self._launch_explorer()
        group_id = launched["group_id"]
        session_id = self._session_ids(group_id)[0]

        for forbidden in ("password", "host", "username", "directory", "initial_command"):
            with self.subTest(field=forbidden):
                response = self.client.post(
                    PRESENTATION_ROUTE,
                    json={
                        "workspace_id": "default",
                        "group_id": group_id,
                        "expected_revision": 0,
                        "pane_order": [session_id],
                        "panes": [{"session_id": session_id, forbidden: "nope"}],
                    },
                )
                self.assertEqual(response.status_code, 400, response.get_json())

    def test_presentation_route_rejects_a_foreign_session_id(self):
        """Stage 2 rule 1: unknown/cross-group ids never reach the manager."""
        first = self._launch_explorer(session_name="One")
        second = self._launch_explorer(session_name="Two")
        foreign_id = self._session_ids(second["group_id"])[0]

        response = self.client.post(
            PRESENTATION_ROUTE,
            json={
                "workspace_id": "default",
                "group_id": first["group_id"],
                "expected_revision": 0,
                "pane_order": [foreign_id],
                "panes": [{"session_id": foreign_id, "explorer_open_tabs": []}],
            },
        )

        self.assertEqual(response.status_code, 400, response.get_json())

    def test_a_stale_revision_is_refused_with_the_current_one(self):
        """Stage 2 rule 7 / product decision 7. Matrix row 13."""
        launched = self._launch_explorer()
        group_id = launched["group_id"]
        session_id = self._session_ids(group_id)[0]

        def push(revision, tabs):
            return self.client.post(
                PRESENTATION_ROUTE,
                json={
                    "workspace_id": "default",
                    "group_id": group_id,
                    "expected_revision": revision,
                    "pane_order": [session_id],
                    "panes": [
                        {"session_id": session_id, "explorer_open_tabs": tabs}
                    ],
                },
            )

        first = push(0, ["a.md"])
        self.assertEqual(first.status_code, 200, first.get_json())
        current_revision = first.get_json()["presentation_revision"]

        stale = push(0, ["stale.md"])

        self.assertEqual(stale.status_code, 409, stale.get_json())
        # The loser learns where to rebase from, and the newer state survives.
        self.assertEqual(stale.get_json()["presentation_revision"], current_revision)
        session = api.session_manager.get_session(session_id)
        self.assertEqual(session.explorer_open_tabs, ["a.md"])


# ==================== Item 2 — SGP-02 (Stage 2 + Stage 3) ====================


class BrowserPresentationOrderingTestCase(_PersistencePathsMixin, unittest.TestCase):
    """A late response must regress neither server nor client state.

    SGP-02: `browserPersistTabs()` debounces 400 ms, tracks only pending
    timers, and assigns the server payload back over `pane._session`. Two
    in-flight writes can therefore complete out of order and the *older* one
    wins twice — on the server and in the browser.

    Regression matrix rows 12 and 14.
    """

    def setUp(self):
        self._isolate_state()

    def test_an_older_tab_strip_cannot_overwrite_a_newer_one_on_the_server(self):
        """Stage 2 ordered server transaction. Matrix row 12, server half."""
        launched = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Preview",
                "sessions": [
                    {
                        "startup_mode": "browser",
                        "initial_command": "http://127.0.0.1:3000/",
                        "title": "Preview",
                    }
                ],
            },
        )
        self.assertEqual(launched.status_code, 201, launched.get_json())
        payload = launched.get_json()
        group_id = payload["group_id"]
        session_id = self._session_ids(group_id)[0]

        def push(revision, tabs):
            return self.client.post(
                PRESENTATION_ROUTE,
                json={
                    "workspace_id": "default",
                    "group_id": group_id,
                    "expected_revision": revision,
                    "pane_order": [session_id],
                    "panes": [
                        {
                            "session_id": session_id,
                            "browser_tabs": tabs,
                            "browser_active_tab": 0,
                        }
                    ],
                },
            )

        newer = push(0, ["http://127.0.0.1:3000/b"])
        self.assertEqual(newer.status_code, 200, newer.get_json())

        # "A" arrives after "B": same expected_revision, older content.
        older = push(0, ["http://127.0.0.1:3000/a"])

        self.assertEqual(older.status_code, 409, older.get_json())
        session = api.session_manager.get_session(session_id)
        self.assertEqual(session.browser_tabs, ["http://127.0.0.1:3000/b"])

    def test_the_client_queue_keeps_one_write_in_flight_and_ignores_late_replies(self):
        """Stage 2. Matrix row 12, client half.

        Executed for real in Node: the audit requires the queue's ordering
        rules to live in a DOM-free module so they can be run rather than
        asserted as source text.
        """
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        self.assertTrue(
            PRESENTATION_JS.exists(),
            f"Stage 2 must add {PRESENTATION_JS.name} as a require()-able, DOM-free module",
        )

        harness = """
            const { createPresentationQueue } = require(process.argv[2]);
            const sent = [];
            const pending = [];
            const queue = createPresentationQueue({
                send(payload) {
                    sent.push(payload);
                    return new Promise((resolve) => pending.push(resolve));
                }
            });

            (async () => {
                queue.enqueue('g1', { browser_tabs: ['a'] });
                queue.enqueue('g1', { browser_tabs: ['b'] });
                await new Promise((resolve) => setImmediate(resolve));
                const inFlightAfterTwoEnqueues = sent.length;

                // The first request answers last, carrying the older strip.
                pending[0]({ browser_tabs: ['a'] });
                await new Promise((resolve) => setImmediate(resolve));

                process.stdout.write(JSON.stringify({
                    inFlightAfterTwoEnqueues,
                    coalescedPayload: sent[0].browser_tabs,
                    latest: queue.latest('g1').browser_tabs
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

        # One request in flight per group, whatever the enqueue rate.
        self.assertEqual(result["inFlightAfterTwoEnqueues"], 1)
        # …and it carries the coalesced newest snapshot, not the first one.
        self.assertEqual(result["coalescedPayload"], ["b"])
        # A late reply describing the older strip never regresses local state.
        self.assertEqual(result["latest"], ["b"])


# ==================== Item 3 — SGP-01 (Stage 3) ====================


class PaneOrderAndSplitGeometryTestCase(_PersistencePathsMixin, unittest.TestCase):
    """A dragged pane order and a resized split must survive a restart.

    SGP-01: pane visual order lives in the DOM while the server enumerates
    sessions in dictionary insertion order, and custom split rectangles/weights
    are built only for Save Session. Both revert after a restore today.

    Regression matrix row 10.
    """

    def setUp(self):
        self._isolate_state()

    @staticmethod
    def _split_layout(column_weights):
        return {
            "class_name": "layout-split-local",
            "split_slot_rects": [
                {"originSlot": 0, "x": 1, "y": 1, "w": 1, "h": 1},
                {"originSlot": 1, "x": 2, "y": 1, "w": 1, "h": 1},
            ],
            "split_column_weights": column_weights,
            "split_row_weights": [1.0],
            "original_split_slot_count": 2,
        }

    def test_reordered_panes_and_resized_tracks_round_trip_through_a_restart(self):
        """Stage 2 backend transaction; Stage 3 wires DOM capture. Matrix row 10."""
        launched = self._launch_explorer(
            panes=2,
            layout="split",
            workspace_layout=self._split_layout([1.0, 1.0]),
        )
        group_id = launched["group_id"]
        first, second = self._session_ids(group_id)

        accepted = self.client.post(
            PRESENTATION_ROUTE,
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": 0,
                # Dragged: the second card is now on the left.
                "pane_order": [second, first],
                "layout": "split",
                # Resized: the left track is now twice the right one.
                "workspace_layout": self._split_layout([2.0, 1.0]),
                "panes": [{"session_id": second}, {"session_id": first}],
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.get_json())

        saved = self._save_workspace()
        self.assertEqual(saved.status_code, 200, saved.get_json())

        stored_group = self._stored_slot()["groups"][0]
        self.assertEqual(
            [session["title"] for session in stored_group["sessions"]],
            ["Files 2", "Files 1"],
        )
        self.assertEqual(
            stored_group["workspace_layout"]["split_column_weights"], [2.0, 1.0]
        )

        # …and the restore replays that order and geometry, not the launch one.
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        restored_group = api.session_manager.get_workspace_groups("default")[0]
        self.assertEqual(
            [
                session.title
                for session in api.session_manager.get_group_sessions(
                    restored_group.group_id
                )
            ],
            ["Files 2", "Files 1"],
        )
        self.assertEqual(
            restored_group.workspace_layout["split_column_weights"], [2.0, 1.0]
        )


# ==================== Item 4 — SGP-04 (Stage 5) ====================


class ExplorerRootRestoreTestCase(_PersistencePathsMixin, unittest.TestCase):
    """A pane rooted at a parent must not be narrowed to its current directory.

    SGP-04: `explorer_root_directory` is captured, then `_prepare_launch_sessions()`
    overwrites it with `directory` for both SSH and local explorer panes, so a
    pane rooted at `/srv/app` while browsing `/srv/app/src` comes back rooted at
    the child and can no longer navigate up.

    Regression matrix row 9.
    """

    def setUp(self):
        self._isolate_state()
        self.child_dir = self.repo_dir / "src"
        self.child_dir.mkdir()

    def _write_slot(self, session_overrides, workspace_id="default"):
        state = {
            "version": web_runtime_state.SCHEMA_VERSION,
            "workspaces": {
                workspace_id: {
                    "workspace_id": workspace_id,
                    "label": "Alpha",
                    "origin": "manual",
                    "saved_at": 1000.0,
                    "manually_saved_at": 1000.0,
                    "active_group_id": "g1",
                    "groups": [
                        {
                            "group_id": "g1",
                            "name": "Files",
                            "connection_mode": "wsl",
                            "layout": "single",
                            "sessions": [
                                {
                                    "title": "Files",
                                    "startup_mode": "explorer",
                                    **session_overrides,
                                }
                            ],
                        }
                    ],
                }
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        return workspace_id

    @unittest.expectedFailure
    def test_restore_keeps_a_root_wider_than_the_current_directory(self):
        """Stage 5 item 1. Matrix row 9."""
        workspace_id = self._write_slot(
            {
                "directory": str(self.child_dir),
                "explorer_root_directory": str(self.repo_dir),
            }
        )

        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": [workspace_id]}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        group = api.session_manager.get_workspace_groups(workspace_id)[0]
        session = api.session_manager.get_group_sessions(group.group_id)[0]
        self.assertEqual(session.explorer_root_directory, str(self.repo_dir))
        # The captured working directory is unchanged, and still inside the root.
        self.assertEqual(session.directory, str(self.child_dir))

    def test_a_missing_root_still_falls_back_to_the_captured_directory(self):
        """Stage 5 item 1: preserving a root must not break older slots.

        The one test here that already passes, and deliberately has no
        `expectedFailure`: it pins the fallback Stage 5 must keep while it
        stops overwriting a root that *was* captured.
        """
        workspace_id = self._write_slot({"directory": str(self.child_dir)})

        response = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": [workspace_id]}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        group = api.session_manager.get_workspace_groups(workspace_id)[0]
        session = api.session_manager.get_group_sessions(group.group_id)[0]
        self.assertEqual(session.explorer_root_directory, str(self.child_dir))


# ==================== Item 5 — SGP-03 / SGP-08 (Stage 5) ====================


class ExplorerPresentationFixtureTestCase(_PersistencePathsMixin, unittest.TestCase):
    """The agreed explorer fixture must survive every persistence path.

    SGP-03: `explorerPersistableTabView()` reduces rich per-panel metrics to a
    single vertical fraction, sidebar width and expanded tree paths never leave
    the live pane object, and mode intent shares one content-identity gate with
    scroll — so an external edit can drop a user's durable Preview/Diff choice.

    Regression matrix rows 4-7.
    """

    def setUp(self):
        self._isolate_state()

    @unittest.expectedFailure
    def test_the_canonical_normalizer_preserves_every_in_boundary_field(self):
        """Stage 5 items 2-4 through the SGP-07 canonical normalizer."""
        module = __import__(PRESENTATION_MODULE, fromlist=["normalize_pane_presentation"])

        normalized = module.normalize_pane_presentation(EXPLORER_PRESENTATION_FIXTURE)

        for field_name, expected in EXPLORER_PRESENTATION_FIXTURE.items():
            with self.subTest(field=field_name):
                self.assertEqual(normalized[field_name], expected)

    @unittest.expectedFailure
    def test_the_fixture_round_trips_through_save_workspace_and_restore(self):
        """Stage 3 + Stage 5. Matrix rows 2, 4, 5, 7."""
        launched = self._launch_explorer()
        group_id = launched["group_id"]
        session_id = self._session_ids(group_id)[0]

        accepted = self.client.post(
            PRESENTATION_ROUTE,
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": 0,
                "pane_order": [session_id],
                "panes": [{"session_id": session_id, **EXPLORER_PRESENTATION_FIXTURE}],
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.get_json())

        saved = self._save_workspace()
        self.assertEqual(saved.status_code, 200, saved.get_json())

        stored = self._stored_sessions()[0]
        for field_name, expected in EXPLORER_PRESENTATION_FIXTURE.items():
            with self.subTest(field=field_name):
                self.assertEqual(stored[field_name], expected)

        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        group = api.session_manager.get_workspace_groups("default")[0]
        session = api.session_manager.get_group_sessions(group.group_id)[0].to_dict()
        for field_name, expected in EXPLORER_PRESENTATION_FIXTURE.items():
            with self.subTest(field=field_name):
                self.assertEqual(session[field_name], expected)

    @unittest.expectedFailure
    def test_changed_content_drops_scroll_and_folds_but_keeps_view_intent(self):
        """Stage 5 items 3 and 5. Matrix row 6.

        A durable mode preference is not a content-relative coordinate: when
        the file changes underneath, Diff must stay Diff while its stale
        scroll and folds are discarded.
        """
        module = __import__(PRESENTATION_MODULE, fromlist=["resolve_tab_view"])

        stored = EXPLORER_PRESENTATION_FIXTURE["explorer_tab_views"][
            "web/static/js/shared.js"
        ]
        resolved = module.resolve_tab_view(stored, content_revision="sha256:index-xyz")

        self.assertEqual(resolved["intent"], {"mode": "diff", "diff_mode": "staged"})
        self.assertEqual(resolved.get("scroll"), {})
        self.assertEqual(resolved.get("folds"), [])

    @unittest.expectedFailure
    def test_a_staged_diff_identity_tracks_the_index_not_the_working_file(self):
        """Stage 5 item 6.

        The current identity is path + working-file content + the word
        `staged`, so an index change that leaves the working file untouched
        looks identical and restores scroll into different diff content.
        """
        module = __import__(PRESENTATION_MODULE, fromlist=["diff_content_revision"])

        unchanged_worktree = {"path": "a.js", "worktree_revision": "sha256:same"}
        before = module.diff_content_revision(
            {**unchanged_worktree, "index_revision": "sha256:index-1"}, diff_mode="staged"
        )
        after = module.diff_content_revision(
            {**unchanged_worktree, "index_revision": "sha256:index-2"}, diff_mode="staged"
        )

        self.assertNotEqual(before, after)

    @unittest.expectedFailure
    def test_markdown_appearance_is_one_workspace_scoped_value(self):
        """SGP-08 / product decision 5, Stage 5 item 8.

        `localStorage` is per origin, so it can only express an
        application-global value. The workspace record must be the authority.
        """
        self._launch_explorer()

        response = self.client.post(
            "/api/workspaces/default/appearance",
            json={"md_preset": "compact", "md_font": "serif", "source_font": "mono"},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        workspace = api.session_manager.get_workspace("default")
        self.assertEqual(workspace.md_preset, "compact")
        self.assertEqual(workspace.md_font, "serif")
        self.assertEqual(workspace.source_font, "mono")


# ==================== Item 6 — SGP-06 (Stage 6 items 1-3) ====================


class LaunchCapacityNondestructiveTestCase(_PersistencePathsMixin, unittest.TestCase):
    """A runtime preference must never destroy stored data.

    SGP-06: `_normalize_terminal_entries()` iterates `runtime_config.max_sessions`
    and `_normalize_workspace_layout()` clamps `originSlot` to it. Every load
    normalizes through those functions and every later save rewrites the
    normalized list, so lowering the setting truncates presets on the next
    unrelated write and silently rewrites snapshot geometry on the runtime-state
    *read* path.

    These items are a hard prerequisite of Stage 4: **Save open sessions +
    workspaces** writes a preset for every live group, which would otherwise
    turn a lowered preference into one-click truncation of every one of them.

    Regression matrix rows 20 and 21.
    """

    def setUp(self):
        self._isolate_state()

    @staticmethod
    def _wide_config(count):
        return {
            "connection_mode": "ssh",
            "terminal_count": count,
            "layout": "grid",
            "ssh": {
                "host": "wide.example",
                "username": "ubuntu",
                "password": "",
                "port": 22,
                "default_dir": "/srv",
            },
            "terminals": [
                {"title": f"Pane {index + 1}", "directory": "/srv"}
                for index in range(count)
            ],
            "workspace_layout": {
                "class_name": "layout-split-local",
                "split_slot_rects": [
                    {"originSlot": index, "x": index + 1, "y": 1, "w": 1, "h": 1}
                    for index in range(count)
                ],
                "split_column_weights": [1.0] * count,
                "split_row_weights": [1.0],
                "original_split_slot_count": count,
            },
        }

    @unittest.expectedFailure
    def test_lowering_max_sessions_leaves_a_wider_preset_byte_for_byte(self):
        """Stage 6 items 1-2. Matrix row 20."""
        with patch.object(api.runtime_config, "max_sessions", 8):
            web_saved_sessions.upsert_saved_session(self._wide_config(8), name="Wide")
        before = self.saved_sessions_path.read_text(encoding="utf-8")

        with patch.object(api.runtime_config, "max_sessions", 4):
            # A read must be a faithful read of the file…
            entries = web_saved_sessions.load_saved_sessions()
            wide = next(entry for entry in entries if entry["name"] == "Wide")
            self.assertEqual(wide["config"]["terminal_count"], 8)
            self.assertEqual(
                [
                    terminal["title"]
                    for terminal in wide["config"]["terminals"][:8]
                ],
                [f"Pane {index + 1}" for index in range(8)],
            )
            # …and an unrelated write must not make a truncation durable.
            web_saved_sessions.upsert_saved_session(
                self._wide_config(2), name="Narrow"
            )

        after = json.loads(self.saved_sessions_path.read_text(encoding="utf-8"))
        stored_wide = next(
            entry for entry in after["sessions"] if entry["name"] == "Wide"
        )
        self.assertEqual(stored_wide["config"]["terminal_count"], 8)
        self.assertEqual(
            [
                rect["originSlot"]
                for rect in stored_wide["config"]["workspace_layout"]["split_slot_rects"]
            ],
            list(range(8)),
        )
        self.assertIn("Pane 8", before)
        self.assertIn("Pane 8", json.dumps(after))

    @unittest.expectedFailure
    def test_lowering_max_sessions_does_not_rewrite_snapshot_geometry(self):
        """Stage 6 item 3, runtime-state read path. Matrix row 21.

        Worse than truncation because it is a silent value rewrite: a group
        still *inside* the current cap restores with corrupted geometry rather
        than failing visibly, and the next autosave commits the corruption.
        """
        stored_slots = [0, 1, 2, 7]
        state = {
            "version": web_runtime_state.SCHEMA_VERSION,
            "workspaces": {
                "default": {
                    "workspace_id": "default",
                    "label": "Alpha",
                    "origin": "manual",
                    "saved_at": 1000.0,
                    "groups": [
                        {
                            "group_id": "g1",
                            "name": "Wide",
                            "connection_mode": "wsl",
                            "layout": "split",
                            "workspace_layout": {
                                "class_name": "layout-split-local",
                                "split_slot_rects": [
                                    {
                                        "originSlot": slot,
                                        "x": index + 1,
                                        "y": 1,
                                        "w": 1,
                                        "h": 1,
                                    }
                                    for index, slot in enumerate(stored_slots)
                                ],
                                "split_column_weights": [1.0] * 4,
                                "split_row_weights": [1.0],
                                "original_split_slot_count": 4,
                            },
                            "sessions": [
                                {"directory": "/srv", "title": f"Pane {index + 1}"}
                                for index in range(4)
                            ],
                        }
                    ],
                }
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        with patch.object(api.runtime_config, "max_sessions", 4):
            slot = web_runtime_state.load_restorable_workspace("default")

        self.assertIsNotNone(slot)
        self.assertEqual(
            [
                rect["originSlot"]
                for rect in slot["groups"][0]["workspace_layout"]["split_slot_rects"]
            ],
            stored_slots,
        )

    @unittest.expectedFailure
    def test_an_oversized_group_fails_with_an_actionable_capacity_error(self):
        """Stage 6 items 6-7 / product decision 6. Matrix row 20."""
        state = {
            "version": web_runtime_state.SCHEMA_VERSION,
            "workspaces": {
                "default": {
                    "workspace_id": "default",
                    "label": "Alpha",
                    "origin": "manual",
                    "saved_at": 1000.0,
                    "groups": [
                        {
                            "group_id": "g1",
                            "name": "Wide",
                            "connection_mode": "wsl",
                            "layout": "grid",
                            "sessions": [
                                {
                                    "directory": str(self.repo_dir),
                                    "title": f"Pane {index + 1}",
                                    "startup_mode": "explorer",
                                }
                                for index in range(6)
                            ],
                        }
                    ],
                }
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_path.read_text(encoding="utf-8")

        with patch.object(api.runtime_config, "max_sessions", 4):
            response = self.client.post(
                "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
            )

        payload = response.get_json()
        errors = json.dumps(payload)
        # Actionable: it names the number to raise the setting to.
        self.assertIn("6", errors)
        self.assertIn("max_sessions", errors)
        # Non-destructive: the slot is untouched and still on offer.
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)


# ==================== Item 7 — SGP-07 (Stage 2 + Stage 6 items 4-5) ====================


class MalformedPresentationValidationTestCase(_PersistencePathsMixin, unittest.TestCase):
    """A corrupt group must fail as a group, never restore partially.

    SGP-07: `_validate_session()` copies the allowlist without checking types,
    so `install_session_group()` decides the outcome by whether a defensive
    coercion happens to raise. `dict("x")` and `int("abc")` raise and the pane
    is silently dropped; `list("abc")` succeeds and installs `['a','b','c']` as
    live state, which the next autosave then makes durable.

    Regression matrix rows 22 and 23.
    """

    #: The audit's table, in order: the two coercions that raise (pane dropped,
    #: group launches smaller) and the one that does not (garbage installed).
    MALFORMED_FIELDS = (
        ("explorer_tab_views", "x"),
        ("browser_active_tab", "abc"),
        ("explorer_open_tabs", "abc"),
    )

    def setUp(self):
        self._isolate_state()

    def _write_slot(self, groups, workspace_id="default"):
        state = {
            "version": web_runtime_state.SCHEMA_VERSION,
            "workspaces": {
                workspace_id: {
                    "workspace_id": workspace_id,
                    "label": "Alpha",
                    "origin": "manual",
                    "saved_at": 1000.0,
                    "groups": groups,
                }
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        return workspace_id

    @staticmethod
    def _group(group_id="g1", panes=2, bad_field=None, bad_value=None):
        sessions = [
            {"directory": "/srv", "title": f"Pane {index + 1}", "startup_mode": "terminal"}
            for index in range(panes)
        ]
        if bad_field is not None:
            sessions[-1][bad_field] = bad_value
        return {
            "group_id": group_id,
            "name": "Group",
            "connection_mode": "wsl",
            "layout": "single" if panes == 1 else "vertical",
            "sessions": sessions,
        }

    @unittest.expectedFailure
    def test_a_malformed_pane_makes_the_whole_group_unrestorable(self):
        """Stage 6 items 4-5. Matrix rows 22 and 23.

        Including the silently coercible `explorer_open_tabs: "abc"`, which a
        fix that only converts dropped panes into group failures would miss.
        """
        for field_name, value in self.MALFORMED_FIELDS:
            with self.subTest(field=field_name):
                self._write_slot([self._group(bad_field=field_name, bad_value=value)])

                self.assertIsNone(web_runtime_state.load_restorable_workspace("default"))
                self.assertEqual(web_runtime_state.list_restorable_workspaces(), [])

    @unittest.expectedFailure
    def test_the_chooser_count_matches_what_a_restore_would_really_start(self):
        """Invariant 6: a partial group is never advertised as exact."""
        self._write_slot(
            [
                self._group(group_id="good", panes=2),
                self._group(
                    group_id="bad",
                    panes=2,
                    bad_field="explorer_open_tabs",
                    bad_value="abc",
                ),
            ]
        )

        rows = self.client.get("/api/runtime-state/workspaces").get_json()["workspaces"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["group_count"], 1)
        self.assertEqual(rows[0]["pane_count"], 2)

    def test_the_canonical_normalizer_rejects_wrong_types_instead_of_coercing(self):
        """Stage 2 live boundary: type-check, never `list()`/`dict()`/`int()` coercion."""
        module = __import__(PRESENTATION_MODULE, fromlist=["normalize_pane_presentation"])

        for field_name, value in self.MALFORMED_FIELDS:
            with self.subTest(field=field_name):
                with self.assertRaises(ValueError):
                    module.normalize_pane_presentation({field_name: value})


# ==================== Item 8 — SGP-11 (Stage 4) ====================


class LifecycleActionMatrixTestCase(_PersistencePathsMixin, unittest.TestCase):
    """Close and restart must expose one contract with one set of effects.

    SGP-11: **Save & Restart** omits `workspace_id`, so it captures only the
    default workspace and never touches `saved_sessions.json`; it restarts even
    after the capture fails; the update-triggered restart bypasses that helper
    entirely; and both shutdown paths exit without a new snapshot.

    Regression matrix rows 28-32.
    """

    WORKSPACE_B = "bbbbbbbbbbbb"

    def setUp(self):
        self._isolate_state()

    def _two_live_workspaces(self):
        first = self._launch_explorer(session_name="Alpha")
        api.session_manager.create_workspace("Beta", self.WORKSPACE_B)
        second = self._launch_explorer(
            session_name="Beta work", workspace_id=self.WORKSPACE_B
        )
        return first, second

    def _prepare(self, action, save):
        return self.client.post(LIFECYCLE_ROUTE, json={"action": action, "save": save})

    @unittest.expectedFailure
    def test_without_saving_writes_neither_file_and_keeps_the_restore_point(self):
        """Stage 4 row 1. Matrix row 28."""
        self._two_live_workspaces()
        web_saved_sessions.upsert_saved_session(
            {"connection_mode": "wsl", "terminal_count": 1}, name="Existing"
        )
        self.assertEqual(self._save_workspace().status_code, 200)
        state_before = self.state_path.read_text(encoding="utf-8")
        presets_before = self.saved_sessions_path.read_text(encoding="utf-8")

        for action in ("close", "restart"):
            with self.subTest(action=action):
                response = self._prepare(action, LIFECYCLE_SAVE_NONE)

                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertTrue(response.get_json()["ready_to_exit"])
                self.assertEqual(
                    self.state_path.read_text(encoding="utf-8"), state_before
                )
                self.assertEqual(
                    self.saved_sessions_path.read_text(encoding="utf-8"), presets_before
                )

    @unittest.expectedFailure
    def test_save_open_workspaces_covers_every_live_workspace(self):
        """Stage 4 row 2, item 3. Matrix row 29.

        "Open" is process-wide, not "the window that pressed the button" and
        not "whatever `_resolve_workspace_id` defaults to".
        """
        self._two_live_workspaces()
        presets_before = (
            self.saved_sessions_path.read_text(encoding="utf-8")
            if self.saved_sessions_path.exists()
            else ""
        )

        response = self._prepare("restart", LIFECYCLE_SAVE_WORKSPACES)

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["ready_to_exit"])
        self.assertEqual(
            sorted(payload["saved_workspaces"]), sorted(["default", self.WORKSPACE_B])
        )
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(set(state["workspaces"]), {"default", self.WORKSPACE_B})
        # Workspace-only never touches the reusable presets.
        self.assertEqual(
            self.saved_sessions_path.read_text(encoding="utf-8")
            if self.saved_sessions_path.exists()
            else "",
            presets_before,
        )

    @unittest.expectedFailure
    def test_sessions_plus_workspaces_saves_presets_first_and_links_them(self):
        """Stage 4 rows 3, item 5 / product decisions 11-12. Matrix row 30."""
        first, second = self._two_live_workspaces()

        response = self._prepare("close", LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES)

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertTrue(payload["ready_to_exit"])
        self.assertEqual(len(payload["saved_sessions"]), 2)

        presets = json.loads(self.saved_sessions_path.read_text(encoding="utf-8"))
        preset_ids = {entry["id"] for entry in presets["sessions"]}
        self.assertEqual(preset_ids, set(payload["saved_sessions"]))

        # Every captured group references the preset identity just written…
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        for slot in state["workspaces"].values():
            for group in slot["groups"]:
                self.assertIn(group["saved_session_id"], preset_ids)
        # …while the workspace snapshot stays password-free.
        self.assertNotIn("password", self.state_path.read_text(encoding="utf-8"))

    @unittest.expectedFailure
    def test_a_failed_requested_save_never_reports_ready_to_exit(self):
        """Stage 4 item 6 / SGP-11. Matrix row 31.

        Today the status text says the save failed and the restart bridge is
        called anyway, terminating every live shell.
        """
        self._two_live_workspaces()

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=OSError("disk full"),
        ):
            response = self._prepare("restart", LIFECYCLE_SAVE_WORKSPACES)

        self.assertIn(response.status_code, (200, 503), response.data)
        payload = response.get_json()
        self.assertFalse(payload["ready_to_exit"])
        self.assertTrue(payload["retryable"])
        self.assertTrue(payload["errors"])
        # Nothing was torn down while the user still has a decision to make.
        self.assertEqual(len(api.session_manager.get_all_workspaces()), 2)

    @unittest.expectedFailure
    def test_partial_combined_failure_keeps_the_successful_preset_writes(self):
        """Product decision 12: no promised atomicity across two files."""
        self._two_live_workspaces()

        with patch.object(
            web_runtime_state,
            "_write_state_locked",
            side_effect=OSError("disk full"),
        ):
            response = self._prepare(
                "close", LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES
            )

        self.assertIn(response.status_code, (200, 503), response.data)
        payload = response.get_json()
        self.assertFalse(payload["ready_to_exit"])
        # Presets committed first are not rolled back…
        self.assertTrue(payload["saved_sessions"])
        self.assertTrue(self.saved_sessions_path.exists())
        # …and the partial result is reported rather than glossed over.
        self.assertTrue(payload["errors"])
        self.assertEqual(payload["saved_workspaces"], [])

    @unittest.expectedFailure
    def test_every_exit_surface_uses_the_same_lifecycle_contract(self):
        """Stage 4 item 7 / product decision 10. Matrix row 32.

        The manual restart button, the update-triggered restart, the explicit
        browser close button, and the native launcher X all route here, so
        none of them can grow its own persistence behaviour.
        """
        self._two_live_workspaces()

        for action in ("restart", "close"):
            for save in (
                LIFECYCLE_SAVE_NONE,
                LIFECYCLE_SAVE_WORKSPACES,
                LIFECYCLE_SAVE_SESSIONS_AND_WORKSPACES,
            ):
                with self.subTest(action=action, save=save):
                    response = self._prepare(action, save)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    payload = response.get_json()
                    self.assertEqual(payload["action"], action)
                    self.assertEqual(payload["save"], save)

        # An unknown verb is refused rather than defaulting to a destructive one.
        unknown = self._prepare("restart", "everything")
        self.assertEqual(unknown.status_code, 400, unknown.get_json())

    @unittest.expectedFailure
    def test_browser_shutdown_refuses_to_tear_down_before_a_lifecycle_decision(self):
        """Stage 4 item 7 / product decision 10. Matrix row 32.

        The explicit browser close button is a *voluntary* exit, so it owes the
        user the choice modal. Today it confirms in the page, closes every
        session, and exits without capturing anything — the one path that can
        still lose current changes while looking deliberate.

        `_schedule_browser_shutdown` is patched out: the assertion is that it
        was never reached, and a real exit would take the test runner with it.
        """
        token = api.configure_browser_shutdown(True)
        self.addCleanup(api.configure_browser_shutdown, False)
        self._two_live_workspaces()
        self.assertEqual(self._save_workspace().status_code, 200)
        before = self.state_path.read_text(encoding="utf-8")

        with patch.object(api, "_schedule_browser_shutdown") as schedule:
            response = self.client.post(
                "/api/browser-shutdown",
                headers={"X-GridVibe-Shutdown-Token": token},
            )

        # Refused, with the reason the caller needs to open the shared modal.
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["lifecycle_decision_required"], True)
        schedule.assert_not_called()
        # Nothing was captured behind the user's back either.
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)
        self.assertEqual(len(api.session_manager.get_all_workspaces()), 2)


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    unittest.main()
