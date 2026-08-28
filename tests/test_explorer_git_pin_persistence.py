"""The Git pin record, carried through every route that can lose it.

The pin is one fact in three fields -- ``explorer_git_pin_active``,
``explorer_git_pinned_path`` and ``explorer_git_pin_kind`` -- and every store
must carry them together.
That is precisely what made the "the pin is saved sometimes" report hard to
place: nothing is missing, so the defect has to be an ordering, identity or
resolution one somewhere along a chain that runs client -> transaction -> store
-> restore -> client. The cure is a round trip that is *executed* on each of the
four durable routes rather than a field inventory that is read.

The pin is a **frozen path scope**: it captures a root-relative file or folder
and stays there until it is cleared. It is deliberately not the repository
root -- a pin on a narrower path is the point of it -- so a pin outside any
worktree round-trips faithfully and the sidebar then reports that it has no
worktree. That is the pin working, not the pin being lost, and
``ScopeIsAFrozenPathTestCase`` below is what keeps the two readings from being
confused again.

``explorer_git_expanded`` rides the same chain and is asserted beside the record
on every route, because "which commits were open" is lost the same silent way.
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
from web.session_presentation import (
    EXPLORER_MAX_GIT_EXPANDED,
    PresentationValidationError,
    normalize_pane_presentation,
)

ROOT = Path(__file__).resolve().parent.parent
PERSISTENCE_JS = ROOT / "web" / "static" / "js" / "session-persistence.js"
SIDEBAR_JS = ROOT / "web" / "static" / "js" / "explorer-git-sidebar.js"
NODE = shutil.which("node")

# One pinned explorer pane's worth of Git presentation. Every route below is
# handed exactly this and is expected to give exactly this back.
PINNED_PATH = "web/static/js/pinned.js"
PINNED_KIND = "file"
EXPANDED = ["explorer:abcdef1", "explorer:1234567"]


class _PinRouteTestCase(unittest.TestCase):
    """One live explorer pane, and the durable stores wired to a temp dir."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root_dir = Path(self.temp_dir.name) / "root"
        (self.root_dir / "web" / "static" / "js").mkdir(parents=True)
        (self.root_dir / PINNED_PATH).write_text("pinned\n", encoding="utf-8")
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        self.saved_path = Path(self.temp_dir.name) / "saved_sessions.json"
        for module, attribute, value in (
            (web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.saved_path)),
        ):
            patcher = patch.object(module, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- fixtures ----------------------------------------------------------
    def _launch(self, pane_count=1):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Pinned",
                "layout": "single" if pane_count == 1 else "vertical",
                "sessions": [
                    {
                        "directory": str(self.root_dir),
                        "title": f"Files {index + 1}",
                        "startup_mode": "explorer",
                    }
                    for index in range(pane_count)
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        group_id = response.get_json()["group_id"]
        sessions = api.session_manager.get_group_sessions(group_id)
        return group_id, [session.session_id for session in sessions]

    def _pin(
        self,
        group_id,
        session_ids,
        *,
        path=PINNED_PATH,
        kind=PINNED_KIND,
        revision=0,
    ):
        """Route 1: the live presentation transaction the page actually posts."""
        return self.client.post(
            "/api/session-presentation",
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": revision,
                "pane_order": list(session_ids),
                "panes": [
                    {
                        "session_id": session_id,
                        "explorer_git_open": True,
                        "explorer_git_follow_browsing": False,
                        "explorer_git_pin_active": True,
                        "explorer_git_pinned_path": path,
                        "explorer_git_pin_kind": kind,
                        "explorer_git_expanded": list(EXPANDED),
                    }
                    for session_id in session_ids
                ],
            },
        )

    def assertPinned(
        self,
        session,
        *,
        path=PINNED_PATH,
        kind=PINNED_KIND,
        expanded=EXPANDED,
    ):
        """The pin record *and* expansion, read off a live session object."""
        self.assertTrue(
            session.explorer_git_pin_active,
            "the pin flag did not survive this route",
        )
        self.assertEqual(session.explorer_git_pinned_path, path)
        self.assertEqual(session.explorer_git_pin_kind, kind)
        self.assertEqual(list(session.explorer_git_expanded), list(expanded))


class LivePresentationRouteTestCase(_PinRouteTestCase):
    """Route 1 -- the page's own ordered compare-and-swap."""

    def test_the_pin_pair_reaches_the_session_and_comes_back_out_of_to_dict(self):
        group_id, session_ids = self._launch()
        accepted = self._pin(group_id, session_ids)

        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        session = api.session_manager.get_session(session_ids[0])
        self.assertPinned(session)
        # to_dict() is what the rebuilding page reads back.
        payload = session.to_dict()
        self.assertTrue(payload["explorer_git_pin_active"])
        self.assertEqual(payload["explorer_git_pinned_path"], PINNED_PATH)
        self.assertEqual(payload["explorer_git_pin_kind"], PINNED_KIND)
        self.assertEqual(payload["explorer_git_expanded"], EXPANDED)


class WorkspaceSnapshotRouteTestCase(_PinRouteTestCase):
    """Route 2 -- snapshot to runtime_state.json, restart, restore."""

    def test_the_pin_survives_capture_teardown_and_restore(self):
        group_id, session_ids = self._launch()
        self._pin(group_id, session_ids)

        web_runtime_state.capture_workspace(api.session_manager, origin="manual")
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        pane = stored["workspaces"]["default"]["groups"][0]["sessions"][0]
        self.assertTrue(pane["explorer_git_pin_active"])
        self.assertEqual(pane["explorer_git_pinned_path"], PINNED_PATH)
        self.assertEqual(pane["explorer_git_pin_kind"], PINNED_KIND)
        self.assertEqual(pane["explorer_git_expanded"], EXPANDED)

        # The process ends here; everything below is the next run.
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()

        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        groups = api.session_manager.get_workspace_groups("default")
        self.assertEqual(len(groups), 1)
        sessions = api.session_manager.get_group_sessions(groups[0].group_id)
        self.assertEqual(len(sessions), 1)
        self.assertPinned(sessions[0])


class SavedPresetRouteTestCase(_PinRouteTestCase):
    """Route 3 -- Save Workspace to saved_sessions.json, then relaunch it.

    Unlike the snapshot route, the preset is built from what the *page*
    describes and never from the manager's own copy (SGP-01: a capture that
    reads only the manager records a coherent snapshot of the wrong moment).
    So the payload below is the one ``buildWorkspaceTerminalEntry`` produces,
    pin pair and expansion included -- anything else would be testing a client
    that does not exist.
    """

    def _save_workspace(self, session_id, omit_fields=()):
        terminal = {
            "session_id": session_id,
            "title": "Files 1",
            "directory": str(self.root_dir),
            "startup_mode": "explorer",
            "initial_command_mode": "explorer",
            "explorer_git_open": True,
            "explorer_git_follow_browsing": False,
            "explorer_git_pin_active": True,
            "explorer_git_pinned_path": PINNED_PATH,
            "explorer_git_pin_kind": PINNED_KIND,
            "explorer_git_expanded": list(EXPANDED),
        }
        for field_name in omit_fields:
            terminal.pop(field_name, None)
        return self.client.post(
            "/api/saved-sessions",
            json={
                "name": "Pinned",
                "group_id": self.group_id,
                "workspace_only": True,
                "config": {
                    "connection_mode": "wsl",
                    "terminal_count": 1,
                    "layout": "single",
                    "terminals": [terminal],
                },
            },
        )

    def test_the_pin_survives_the_preset_write_and_the_relaunch(self):
        self.group_id, session_ids = self._launch()
        self._pin(self.group_id, session_ids)

        saved = self._save_workspace(session_ids[0])
        self.assertEqual(saved.status_code, 201, saved.get_json())
        preset = saved.get_json()["config"]["terminals"][0]
        self.assertTrue(preset["explorer_git_pin_active"])
        self.assertEqual(preset["explorer_git_pinned_path"], PINNED_PATH)
        self.assertEqual(preset["explorer_git_pin_kind"], PINNED_KIND)
        self.assertEqual(preset["explorer_git_expanded"], EXPANDED)
        # And on disk, which is what the next run reads.
        on_disk = json.loads(self.saved_path.read_text(encoding="utf-8"))
        stored = on_disk["sessions"][0]["config"]["terminals"][0]
        self.assertTrue(stored["explorer_git_pin_active"])
        self.assertEqual(stored["explorer_git_pinned_path"], PINNED_PATH)
        self.assertEqual(stored["explorer_git_pin_kind"], PINNED_KIND)

        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()

        relaunched = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Pinned",
                "layout": "single",
                "sessions": [stored],
            },
        )
        self.assertEqual(relaunched.status_code, 201, relaunched.get_json())
        sessions = api.session_manager.get_group_sessions(
            relaunched.get_json()["group_id"]
        )
        self.assertPinned(sessions[0])

    def test_a_save_that_never_described_the_pin_cannot_blank_the_live_one(self):
        """The save writes the normalized preset back onto the live panes it
        names, so a pane described without its explorer state used to hand the
        live session the *defaults* the normalizer had filled in for it -- an
        unpinned pane, written by a save the user asked for to record a pinned
        one. A field the page never stated is not a value; it says nothing.

        No real payload from `buildWorkspaceTerminalEntry` omits these, which
        is exactly why it stayed invisible: the failure needs one truncated
        description (an older page, a partially-built pane) and then the pin is
        gone from the live session, not just from the preset.
        """
        self.group_id, session_ids = self._launch()
        self._pin(self.group_id, session_ids)

        saved = self._save_workspace(
            session_ids[0],
            omit_fields=(
                "explorer_git_pin_active",
                "explorer_git_pinned_path",
                "explorer_git_pin_kind",
                "explorer_git_expanded",
            ),
        )
        self.assertEqual(saved.status_code, 201, saved.get_json())

        live = api.session_manager.get_session(session_ids[0])
        self.assertPinned(live)


class CloseDrivenRebuildRouteTestCase(_PinRouteTestCase):
    """Route 4 -- one pane closes and the group is rebuilt around the rest.

    The client overlays each survivor's captured explorer state onto the
    freshly fetched session objects, so what has to hold on this side is that
    the *fetch* it overlays onto already carries the survivor's pin: an overlay
    repairing a blanked field would be papering over a server-side loss, and
    the pane the user never touched would come back unpinned the moment the
    page reloaded for any other reason.
    """

    def test_closing_one_pane_leaves_the_survivors_pin_untouched(self):
        group_id, session_ids = self._launch(pane_count=2)
        self._pin(group_id, session_ids)

        closed = self.client.delete(f"/api/sessions/{session_ids[0]}")
        self.assertEqual(closed.status_code, 200, closed.get_json())

        survivor = api.session_manager.get_session(session_ids[1])
        self.assertPinned(survivor)
        # The rebuild's own fetch -- the payload the overlay is applied to.
        listing = self.client.get("/api/sessions").get_json()
        entry = next(
            item
            for item in listing["sessions"]
            if item["session_id"] == session_ids[1]
        )
        self.assertTrue(entry["explorer_git_pin_active"])
        self.assertEqual(entry["explorer_git_pinned_path"], PINNED_PATH)
        self.assertEqual(entry["explorer_git_pin_kind"], PINNED_KIND)
        self.assertEqual(entry["explorer_git_expanded"], EXPANDED)


class ScopeIsAFrozenPathTestCase(_PinRouteTestCase):
    """The pin is the browsed folder, frozen -- not the repository root.

    Stage 2's decision, pinned here so a later change cannot quietly promote
    the pin to repository level and call it a fix: a pin made from a folder
    with no worktree, and a pin down to file level, both depend on the pin
    naming exactly the path it was made at.
    """

    def test_a_subdirectory_pin_is_stored_as_that_subdirectory(self):
        group_id, session_ids = self._launch()
        self._pin(group_id, session_ids, path="web/static/js", kind="dir")

        session = api.session_manager.get_session(session_ids[0])
        self.assertEqual(session.explorer_git_pinned_path, "web/static/js")

    def test_a_root_pin_is_a_set_pin_with_an_empty_path(self):
        """The ambiguous one: `active` is the whole difference between "pinned
        to the root" and "not pinned", because the path both carry is ''."""
        group_id, session_ids = self._launch()
        self._pin(group_id, session_ids, path="", kind="dir")

        session = api.session_manager.get_session(session_ids[0])
        self.assertTrue(session.explorer_git_pin_active)
        self.assertEqual(session.explorer_git_pinned_path, "")

    def test_a_pin_on_a_folder_with_no_worktree_round_trips_unchanged(self):
        """Faithful re-application is the contract; the sidebar reports the
        missing worktree. Rewriting the pin to something that *does* resolve
        would be the persistence layer inventing a scope nobody chose."""
        group_id, session_ids = self._launch()
        self._pin(group_id, session_ids, path="web/static", kind="dir")

        web_runtime_state.capture_workspace(api.session_manager, origin="manual")
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )

        groups = api.session_manager.get_workspace_groups("default")
        sessions = api.session_manager.get_group_sessions(groups[0].group_id)
        self.assertEqual(sessions[0].explorer_git_pinned_path, "web/static")
        self.assertTrue(sessions[0].explorer_git_pin_active)


class PinRefusalTestCase(_PinRouteTestCase):
    """What must keep failing, and how."""

    def _scope_the_sidebar(self, group_id, session_ids, *, follow):
        """One pinned, optionally following explorer pane, via route 1."""
        response = self.client.post(
            "/api/session-presentation",
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": 0,
                "pane_order": list(session_ids),
                "panes": [
                    {
                        "session_id": session_id,
                        "explorer_git_open": True,
                        "explorer_git_follow_browsing": follow,
                        "explorer_git_pin_active": True,
                        "explorer_git_pinned_path": PINNED_PATH,
                        "explorer_git_pin_kind": PINNED_KIND,
                        "explorer_git_expanded": list(EXPANDED),
                    }
                    for session_id in session_ids
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_switching_to_terminal_mode_clears_the_whole_scope_selection(self):
        """A shell can walk to another root, so neither scope control survives
        it -- deliberate, and asserted so it stays deliberate.

        The pin is meaningless under a root it was not captured under. Follow
        carries no path, but the decision to scope by the browsed folder was
        made about the previous root's tree, and leaving it on brought the pane
        back scoped to a subdirectory of a freshly derived root with the chain
        button pressed and no pin to explain it.
        """
        group_id, session_ids = self._launch()
        self._scope_the_sidebar(group_id, session_ids, follow=True)

        switched = self.client.post(
            f"/api/sessions/{session_ids[0]}/mode", json={"mode": "terminal"}
        )
        self.assertEqual(switched.status_code, 200, switched.get_json())
        session = api.session_manager.get_session(session_ids[0])
        self.assertFalse(session.explorer_git_pin_active)
        self.assertEqual(session.explorer_git_pinned_path, "")
        self.assertEqual(session.explorer_git_pin_kind, "dir")
        self.assertFalse(session.explorer_git_follow_browsing)
        payload = switched.get_json()
        self.assertFalse(payload["explorer_git_pin_active"])
        self.assertEqual(payload["explorer_git_pinned_path"], "")
        self.assertEqual(payload["explorer_git_pin_kind"], "dir")
        self.assertFalse(payload["explorer_git_follow_browsing"])

    def test_follow_alone_does_not_survive_the_round_trip_either(self):
        """The reported case: no pin, only Follow. The pane comes back at root
        scope, so the sidebar the client rebuilds from this payload is scoped
        to the root it just resolved and nothing else."""
        group_id, session_ids = self._launch()
        followed = self.client.post(
            "/api/session-presentation",
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": 0,
                "pane_order": list(session_ids),
                "panes": [
                    {
                        "session_id": session_ids[0],
                        "explorer_git_open": True,
                        "explorer_git_follow_browsing": True,
                        "explorer_git_pin_active": False,
                        "explorer_git_pinned_path": "",
                        "explorer_git_pin_kind": "dir",
                    }
                ],
            },
        )
        self.assertEqual(followed.status_code, 200, followed.get_json())
        self.assertTrue(
            api.session_manager.get_session(session_ids[0]).explorer_git_follow_browsing
        )

        to_terminal = self.client.post(
            f"/api/sessions/{session_ids[0]}/mode", json={"startup_mode": "terminal"}
        )
        self.assertEqual(to_terminal.status_code, 200, to_terminal.get_json())
        back = self.client.post(
            f"/api/sessions/{session_ids[0]}/mode",
            json={"startup_mode": "explorer", "directory": str(self.root_dir)},
        )
        self.assertEqual(back.status_code, 200, back.get_json())
        session = api.session_manager.get_session(session_ids[0])
        self.assertEqual(session.startup_mode, "explorer")
        self.assertFalse(session.explorer_git_follow_browsing)
        self.assertFalse(session.explorer_git_pin_active)
        self.assertFalse(back.get_json()["explorer_git_follow_browsing"])

    def test_a_restore_is_not_a_retargeting_and_keeps_follow(self):
        """The mode switch drops the scope because it re-derives the root; a
        workspace restore hands the same root back, so it restores both."""
        group_id, session_ids = self._launch()
        self._scope_the_sidebar(group_id, session_ids, follow=True)

        web_runtime_state.capture_workspace(api.session_manager, origin="manual")
        self.client.delete("/api/sessions")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())

        groups = api.session_manager.get_workspace_groups("default")
        self.assertEqual(len(groups), 1)
        sessions = api.session_manager.get_group_sessions(groups[0].group_id)
        self.assertEqual(len(sessions), 1)
        self.assertPinned(sessions[0])
        self.assertTrue(sessions[0].explorer_git_follow_browsing)

    def test_a_wrong_typed_pin_flag_fails_the_whole_group_and_mutates_nothing(self):
        """Launchable shape fails; it is never coerced and never dropped."""
        group_id, session_ids = self._launch(pane_count=2)
        self._pin(group_id, session_ids)
        before = [
            api.session_manager.get_session(session_id).to_dict()
            for session_id in session_ids
        ]

        refused = self.client.post(
            "/api/session-presentation",
            json={
                "workspace_id": "default",
                "group_id": group_id,
                "expected_revision": 1,
                "pane_order": list(session_ids),
                "panes": [
                    {"session_id": session_ids[0], "explorer_git_pinned_path": "docs"},
                    {"session_id": session_ids[1], "explorer_git_pin_active": "yes"},
                ],
            },
        )

        self.assertEqual(refused.status_code, 400)
        after = [
            api.session_manager.get_session(session_id).to_dict()
            for session_id in session_ids
        ]
        # The healthy pane in the same batch is not written either.
        self.assertEqual(before, after)

    def test_a_traversing_or_drive_qualified_pin_path_normalizes_to_the_root(self):
        for hostile in ("../outside", "web/../../etc", "C:/Windows", "docs/../.."):
            with self.subTest(path=hostile):
                normalized = normalize_pane_presentation(
                    {"explorer_git_pinned_path": hostile}
                )
                self.assertEqual(normalized["explorer_git_pinned_path"], "")

    def test_a_non_string_pin_path_is_refused_rather_than_stringified(self):
        for hostile in (5, None, ["docs"], {"path": "docs"}):
            with self.subTest(path=hostile):
                with self.assertRaises(PresentationValidationError):
                    normalize_pane_presentation({"explorer_git_pinned_path": hostile})

    def test_an_unknown_pin_kind_is_refused_rather_than_treated_as_a_directory(self):
        for hostile in (None, 5, "folder", "blob", "FILE"):
            with self.subTest(kind=hostile):
                with self.assertRaises(PresentationValidationError):
                    normalize_pane_presentation({"explorer_git_pin_kind": hostile})

    def test_an_older_preset_defaults_the_absent_kind_but_refuses_a_bad_one(self):
        old_entry = web_saved_sessions._normalize_terminal_entries(
            [{"startup_mode": "explorer"}], minimum_count=1
        )[0]
        self.assertEqual(old_entry["explorer_git_pin_kind"], "dir")

        with self.assertRaises(PresentationValidationError):
            web_saved_sessions._normalize_terminal_entries(
                [{"startup_mode": "explorer", "explorer_git_pin_kind": "blob"}],
                minimum_count=1,
            )

    def test_the_expanded_commit_set_is_bounded_not_refused(self):
        """Expansion is cosmetic, so it is trimmed to the ceiling rather than
        costing the pane the pin travelling beside it."""
        oversized = [f"explorer:{index:07x}" for index in range(400)]
        normalized = normalize_pane_presentation(
            {
                "explorer_git_pin_active": True,
                "explorer_git_pinned_path": PINNED_PATH,
                "explorer_git_pin_kind": PINNED_KIND,
                "explorer_git_expanded": oversized,
            }
        )
        self.assertEqual(
            len(normalized["explorer_git_expanded"]), EXPLORER_MAX_GIT_EXPANDED
        )
        self.assertEqual(normalized["explorer_git_pinned_path"], PINNED_PATH)
        self.assertEqual(normalized["explorer_git_pin_kind"], PINNED_KIND)


@unittest.skipUnless(NODE, "Node.js is required for the client pin round trip")
class ClientPinRoundTripTestCase(_PinRouteTestCase):
    """The client's own half, executed rather than pattern-matched.

    The pane's truth about the pin is the *type* of ``_explorerGitPinnedPath``
    -- a string means pinned, ``undefined`` means not -- and that truth has to
    cross the wire as two flat fields and come back as the same type. The
    ambiguous case is a pin made at the explorer root: it stores ``''``, which
    is the same path an unpinned pane carries, so the flag is the whole
    difference. If that survives, everything wider does.

    ``explorerGitScopePath()`` is loaded from the real sidebar module, so this
    ends where the sidebar's own behaviour begins: what the pane's next Git
    request would be scoped to.
    """

    HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const persistence = require(process.argv[2]);

/* The sidebar is not DOM-free, so it is evaluated in a vm against a stub -- the
   technique test_explorer_git_identity.py uses. Only explorerGitScopePath() is
   called here, and it touches nothing but the pane. */
const sandbox = { console, String, Boolean, Object, JSON, URLSearchParams, encodeURIComponent };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);

const mode = process.argv[4];
const input = JSON.parse(process.argv[5]);

if (mode === 'describe') {
    // A live pane, exactly as the page holds one.
    const pane = { _explorerGitFollowBrowsing: false };
    if (input.pinnedPath !== null) {
        pane._explorerGitPinnedPath = input.pinnedPath;
        pane._explorerGitPinKind = input.pinnedKind;
    }
    const pin = persistence.explorerGitPinDescriptor(pane);
    const payload = persistence.buildGroupPresentationPayload({
        workspaceId: input.workspaceId,
        groupId: input.groupId,
        revision: 0,
        panes: [{
            sessionId: input.sessionId,
            mode: 'explorer',
            explorer: {
                gitOpen: true,
                gitFollowBrowsing: false,
                gitPinActive: pin.active,
                gitPinnedPath: pin.path,
                gitPinKind: pin.kind,
                openTabs: [],
                activeTab: ''
            }
        }]
    });
    process.stdout.write(JSON.stringify({
        scopeBefore: sandbox.explorerGitScopePath(pane),
        payload
    }));
} else if (mode === 'rebuild') {
    // The pane the page rebuilds from the session record the server returned.
    const rebuilt = {
        _explorerGitFollowBrowsing: Boolean(input.explorer_git_follow_browsing),
        _explorerGitPinnedPath: persistence.explorerGitPinnedPathFromSession(input),
        _explorerGitPinKind: persistence.explorerGitPinKindFromSession(input)
    };
    process.stdout.write(JSON.stringify({
        scopeAfter: sandbox.explorerGitScopePath(rebuilt),
        scopeKindAfter: sandbox.explorerGitScopeKind(rebuilt),
        pinnedType: typeof rebuilt._explorerGitPinnedPath
    }));
} else {
    const followedFile = {
        _explorerGitFollowBrowsing: true,
        _explorerMode: 'file',
        _explorerFilePath: 'src/app.js',
        _explorerPath: 'src'
    };
    const followedFolder = {
        _explorerGitFollowBrowsing: true,
        _explorerMode: 'directory',
        _explorerPath: 'src'
    };
    process.stdout.write(JSON.stringify({
        file: {
            path: sandbox.explorerGitScopePath(followedFile),
            kind: sandbox.explorerGitScopeKind(followedFile)
        },
        folder: {
            path: sandbox.explorerGitScopePath(followedFolder),
            kind: sandbox.explorerGitScopeKind(followedFolder)
        }
    }));
}
"""

    def _node(self, mode, payload):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(self.HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(PERSISTENCE_JS),
                    str(SIDEBAR_JS),
                    mode,
                    json.dumps(payload),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def _round_trip(self, pinned_path, pinned_kind=PINNED_KIND):
        group_id, session_ids = self._launch()
        described = self._node(
            "describe",
            {
                "workspaceId": "default",
                "groupId": group_id,
                "sessionId": session_ids[0],
                "pinnedPath": pinned_path,
                "pinnedKind": pinned_kind,
            },
        )
        accepted = self.client.post(
            "/api/session-presentation", json=described["payload"]
        )
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        session = api.session_manager.get_session(session_ids[0]).to_dict()
        rebuilt = self._node("rebuild", session)
        return described, rebuilt

    def test_a_subdirectory_pin_returns_the_same_scope_it_started_as(self):
        described, rebuilt = self._round_trip(PINNED_PATH)

        self.assertEqual(described["scopeBefore"], PINNED_PATH)
        self.assertEqual(rebuilt["scopeAfter"], PINNED_PATH)
        self.assertEqual(rebuilt["scopeKindAfter"], PINNED_KIND)

    def test_a_root_pin_comes_back_as_a_pin_and_not_as_no_pin(self):
        """H3's ambiguity, executed: '' and `null` are different scopes and the
        page must not collapse one into the other over a rebuild."""
        described, rebuilt = self._round_trip("", "dir")

        self.assertEqual(described["scopeBefore"], "")
        self.assertEqual(rebuilt["scopeAfter"], "")
        self.assertEqual(rebuilt["scopeKindAfter"], "dir")
        self.assertEqual(rebuilt["pinnedType"], "string")

    def test_an_unpinned_pane_stays_unpinned_rather_than_pinning_itself_to_root(self):
        described, rebuilt = self._round_trip(None, "dir")

        self.assertIsNone(described["scopeBefore"])
        self.assertIsNone(rebuilt["scopeAfter"])
        self.assertEqual(rebuilt["scopeKindAfter"], "dir")
        self.assertEqual(rebuilt["pinnedType"], "undefined")

    def test_follow_uses_the_open_file_and_falls_back_to_the_browsed_folder(self):
        scopes = self._node("scope", {})

        self.assertEqual(scopes["file"], {"path": "src/app.js", "kind": "file"})
        self.assertEqual(scopes["folder"], {"path": "src", "kind": "dir"})


if __name__ == "__main__":
    unittest.main()
