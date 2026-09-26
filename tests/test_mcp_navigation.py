"""The sidecar's navigation tools -- `focus_session`, `focus_pane`,
`move_session` -- and the words every tool uses for a session and a pane.

- **A session is a tab, named the way its tab shows it.** Every open tab has a
  name, saved or not; a save renames the live tab at once. A name matching two
  tabs is never guessed between, and a name matching none lists the open ones.
- **A pane is a `pane_id`, both ways.** No tool takes a `session_id`, and no
  result calls a pane a session.

- **A raised window is not a switched tab.** With a group named, `opened` is
  only ever the page's own `activated`; a refusal is `blocked` with the page's
  reason and `window_raised: true`; silence is `no_window_available`. Browser
  mode opens the tab and says it is unverified.
- **`focus_pane` reads where the pane is now**, from the pane and its group's
  live arrangement, never from the caller's spawn-time workspace.
- **`move_session` refuses before the wire** when its arguments cannot name one
  session and one destination, presents every candidate when a name is shared,
  and never reports a failed `show` as a failed move.
"""

import io
import json
import sys
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.client import GridVibeError  # noqa: E402
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import _publish, dispatch, tool_specs  # noqa: E402
from gridvibe_mcp.windows import (  # noqa: E402
    BLOCKED,
    NO_WINDOW_AVAILABLE,
    OPENED,
    open_window,
)
from tests.test_mcp_client import (  # noqa: E402
    StubOpener,
    StubResponse,
    client_for,
    http_error,
)
from tests.test_mcp_tools import INSIDE_PANE, RefusingOpener  # noqa: E402


def _bodies(opener):
    return [
        (request.get_method(), request.full_url, json.loads(request.data) if request.data else None)
        for request in opener.requests
    ]


class NativeActivationTestCase(unittest.TestCase):
    """The second step: the page holding the group has to say it shows it."""

    def _open(self, answers, session_id=""):
        opener = StubOpener(answers)
        result = open_window(
            client_for(opener),
            "ws-2",
            "g-2",
            session_id=session_id,
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        )
        return result, opener

    WINDOW_OPENED = [
        {"window_mode": "native"},
        {"intent_id": "i-1", "state": "pending"},
        {"intent_id": "i-1", "state": "opened"},
    ]

    def test_an_activated_tab_is_opened_with_the_group_confirmed(self):
        result, opener = self._open(self.WINDOW_OPENED + [
            {"intent_id": "a-1", "kind": "activate", "state": "pending"},
            {"intent_id": "a-1", "state": "claimed"},
            {
                "intent_id": "a-1",
                "state": "activated",
                "result": {
                    "active_group_id": "g-2",
                    "session_id": "p-1",
                    "pane_visible": True,
                    "focused": True,
                },
            },
        ], session_id="p-1")

        self.assertEqual(result["status"], OPENED)
        self.assertTrue(result["group_activated"])
        self.assertTrue(result["pane_visible"])
        self.assertTrue(result["focused"])
        self.assertNotIn("note", result)
        self.assertEqual(result["active_group_id"], "g-2")
        self.assertTrue(result["window_raised"])
        # The activation names the pane; the window intent never does.
        posted = [body for method, _url, body in _bodies(opener) if method == "POST"]
        self.assertEqual(posted[0], {"workspace_id": "ws-2", "group_id": "g-2"})
        self.assertEqual(
            posted[1], {"workspace_id": "ws-2", "group_id": "g-2", "session_id": "p-1"}
        )

    def test_a_shown_pane_without_focus_is_opened_and_says_so(self):
        result, _ = self._open(self.WINDOW_OPENED + [
            {"intent_id": "a-1", "kind": "activate", "state": "pending"},
            {
                "intent_id": "a-1",
                "state": "activated",
                "result": {"active_group_id": "g-2", "pane_visible": True, "focused": False},
            },
        ], session_id="p-1")

        self.assertEqual(result["status"], OPENED)
        self.assertTrue(result["pane_visible"])
        self.assertFalse(result["focused"])
        self.assertIn("did not take keyboard focus", result["note"])

    def test_a_tab_the_page_refused_is_blocked_with_its_reason(self):
        result, _ = self._open(self.WINDOW_OPENED + [
            {"intent_id": "a-1", "kind": "activate", "state": "pending"},
            {
                "intent_id": "a-1",
                "state": "blocked",
                "detail": "An open file in this window has unsaved changes.",
                "result": {"active_group_id": "g-0", "focused": False},
            },
        ])

        self.assertEqual(result["status"], BLOCKED)
        self.assertTrue(result["window_raised"])
        self.assertFalse(result["group_activated"])
        self.assertEqual(result["active_group_id"], "g-0")
        self.assertIn("unsaved changes", result["detail"])

    def test_activated_on_another_tab_is_not_reported_as_opened(self):
        result, _ = self._open(self.WINDOW_OPENED + [
            {"intent_id": "a-1", "kind": "activate", "state": "pending"},
            {"intent_id": "a-1", "state": "activated", "result": {"active_group_id": "g-0"}},
        ])

        self.assertEqual(result["status"], BLOCKED)
        self.assertFalse(result["group_activated"])

    def test_no_page_holding_the_tab_is_no_window_available_not_success(self):
        result, _ = self._open(self.WINDOW_OPENED + [
            {"intent_id": "a-1", "kind": "activate", "state": "pending"},
            {"intent_id": "a-1", "state": "expired"},
        ])

        self.assertEqual(result["status"], NO_WINDOW_AVAILABLE)
        self.assertTrue(result["window_raised"])
        self.assertFalse(result["group_activated"])
        self.assertIn("not known", result["detail"])

    def test_a_group_that_moved_between_the_two_steps_is_blocked(self):
        opener = StubOpener(self.WINDOW_OPENED)
        client = client_for(opener)
        original = client.activate_intent

        def moved(*_args, **_kwargs):
            raise GridVibeError("Session group g-2 is in workspace ws-9, not ws-2.", status=409)

        client.activate_intent = moved
        self.addCleanup(setattr, client, "activate_intent", original)

        result = open_window(
            client, "ws-2", "g-2", sleep=lambda _s: None, monotonic=lambda: 0.0
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertIn("ws-9", result["detail"])
        self.assertTrue(result["window_raised"])

    def test_a_window_that_did_not_open_asks_for_no_tab(self):
        result, opener = self._open([
            {"window_mode": "native"},
            {"intent_id": "i-1", "state": "pending"},
            {"intent_id": "i-1", "state": "blocked", "detail": "The window refused."},
        ])

        self.assertEqual(result["status"], BLOCKED)
        self.assertNotIn("window_raised", result)
        self.assertFalse(any("/api/windows/activate" in url for _m, url, _b in _bodies(opener)))

    def test_browser_mode_opens_the_tab_and_says_it_is_unverified(self):
        opened = []
        result = open_window(
            client_for(StubOpener([{"window_mode": "browser"}])),
            "ws-2",
            "g-2",
            session_id="p-1",
            browser_opener=lambda url: opened.append(url) or True,
        )

        self.assertEqual(result["status"], OPENED)
        self.assertFalse(result["verified"])
        self.assertIn("focuses the pane", result["note"])
        self.assertEqual(len(opened), 1)


#: Two workspaces as GridVibe answers them, then each one's tabs. The first tab
#: is an unsaved scratch launch: it has no saved preset, and still a name.
WORKSPACES = {"workspaces": [
    {"workspace_id": "ws-1", "label": "gridvibe", "active_group_id": "g-main"},
    {"workspace_id": "ws-2", "label": "gridvibe_2", "active_group_id": ""},
]}
WS1_GROUPS = {"groups": [
    {"group_id": "g-scratch", "name": "Session 08:41:02", "terminal_count": 2},
    {"group_id": "g-main", "name": "gridvibe_main", "terminal_count": 3},
]}
WS2_GROUPS = {"groups": [
    {"group_id": "g-review", "name": "mcp_stage", "terminal_count": 1},
]}


def _tab_reads():
    return [WORKSPACES, WS1_GROUPS, WS2_GROUPS]


class FocusSessionTestCase(unittest.TestCase):
    """"Bring the gridvibe_main session to the foreground" is one call."""

    def _dispatch(self, args, answers, window_opener=None):
        seen = []

        def opener_fn(client, workspace_id, group_id="", **kwargs):
            seen.append((workspace_id, group_id, kwargs))
            return {"status": OPENED, "group_activated": True, "window_raised": True}

        opener = StubOpener(answers)
        result = dispatch(
            "focus_session",
            args,
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=window_opener or opener_fn,
        )
        return result, seen, opener

    def test_a_session_name_finds_its_tab_and_workspace(self):
        result, seen, _ = self._dispatch({"session_name": "mcp_stage"}, _tab_reads())

        # Found in the other workspace: the caller's own is not assumed.
        self.assertEqual(seen, [("ws-2", "g-review", {})])
        self.assertEqual(result["status"], OPENED)
        self.assertTrue(result["session_activated"])
        self.assertNotIn("group_activated", result)
        self.assertEqual(result["session_name"], "mcp_stage")
        self.assertEqual(result["workspace_label"], "gridvibe_2")
        self.assertEqual(result["group_id"], "g-review")

    def test_an_unsaved_tab_is_found_by_the_name_it_shows(self):
        result, seen, _ = self._dispatch({"session_name": "Session 08:41:02"}, _tab_reads())

        self.assertEqual(seen, [("ws-1", "g-scratch", {})])
        self.assertEqual(result["session_name"], "Session 08:41:02")

    def test_a_name_in_another_case_still_means_that_tab(self):
        _result, seen, _ = self._dispatch({"session_name": "GridVibe_Main"}, _tab_reads())

        self.assertEqual(seen, [("ws-1", "g-main", {})])

    def test_a_group_id_given_as_the_name_is_that_tab(self):
        _result, seen, _ = self._dispatch({"session_name": "g-main"}, _tab_reads())

        self.assertEqual(seen, [("ws-1", "g-main", {})])

    def test_a_shared_name_shows_nothing_and_lists_the_candidates(self):
        answers = [
            WORKSPACES,
            WS1_GROUPS,
            {"groups": [{"group_id": "g-other", "name": "gridvibe_main", "terminal_count": 1}]},
        ]
        result, seen, _ = self._dispatch(
            {"session_name": "gridvibe_main"},
            answers,
            window_opener=lambda *_a, **_k: self.fail("a guess was shown"),
        )

        self.assertEqual(result["kind"], "ambiguous")
        self.assertFalse(result["changed"])
        self.assertIn("Nothing was shown", result["error"])
        self.assertEqual(
            [(item["group_id"], item["workspace_label"]) for item in result["candidates"]],
            [("g-main", "gridvibe"), ("g-other", "gridvibe_2")],
        )

    def test_a_workspace_narrows_a_shared_name_to_one(self):
        answers = [
            WORKSPACES,
            WS1_GROUPS,
            {"groups": [{"group_id": "g-other", "name": "gridvibe_main"}]},
        ]
        _result, seen, _ = self._dispatch(
            {"session_name": "gridvibe_main", "workspace_id": "ws-2"}, answers
        )

        self.assertEqual(seen, [("ws-2", "g-other", {})])

    def test_a_name_no_open_tab_has_lists_the_open_ones_and_a_saved_match(self):
        answers = _tab_reads() + [
            {"sessions": [{"id": "session-1", "name": "Old Layout"}]},
        ]
        result, _seen, _ = self._dispatch(
            {"session_name": "old layout"},
            answers,
            window_opener=lambda *_a, **_k: self.fail("nothing to show"),
        )

        self.assertEqual(result["kind"], "not_found")
        self.assertFalse(result["changed"])
        self.assertIn("saved layout named 'Old Layout'", result["error"])
        self.assertIn("not open", result["error"])
        self.assertEqual(
            [tab["session_name"] for tab in result["sessions"]],
            ["Session 08:41:02", "gridvibe_main", "mcp_stage"],
        )

    def test_a_group_id_is_accepted_instead_of_a_name(self):
        _result, seen, _ = self._dispatch({"group_id": "g-scratch"}, _tab_reads())

        self.assertEqual(seen, [("ws-1", "g-scratch", {})])

    def test_a_page_refusal_is_blocked_and_names_the_session(self):
        result, _seen, _ = self._dispatch(
            {"session_name": "gridvibe_main"},
            _tab_reads(),
            window_opener=lambda *_a, **_k: {
                "status": BLOCKED,
                "window_raised": True,
                "group_activated": False,
                "detail": "An open file in this window has unsaved changes.",
            },
        )

        self.assertEqual(result["status"], BLOCKED)
        self.assertFalse(result["session_activated"])
        self.assertEqual(result["session_name"], "gridvibe_main")

    def test_arguments_that_cannot_name_one_session_never_leave(self):
        for args in ({}, {"session_name": "a", "group_id": "g-1"}):
            with self.subTest(args=args):
                result = dispatch(
                    "focus_session",
                    args,
                    client=client_for(RefusingOpener(self)),
                    identity=read_identity(INSIDE_PANE),
                )
                self.assertEqual(result["kind"], "invalid_arguments")


class FocusPaneTestCase(unittest.TestCase):
    def test_the_pane_is_shown_where_its_session_is_now(self):
        opener = StubOpener([
            {"session_id": "p-7", "group_id": "g-3", "title": "Codex"},
            # The group's live arrangement names its current workspace.
            {"group_id": "g-3", "workspace_id": "ws-moved", "panes": []},
            {"groups": [{"group_id": "g-3", "name": "gridvibe_main"}]},
        ])
        seen = []

        def opener_fn(client, workspace_id, group_id, **kwargs):
            seen.append((workspace_id, group_id, kwargs))
            return {"status": OPENED, "group_activated": True, "focused": True}

        result = dispatch(
            "focus_pane",
            {"pane_id": "p-7"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=opener_fn,
        )

        # Not INSIDE_PANE's spawn-time ws-1.
        self.assertEqual(seen, [("ws-moved", "g-3", {"session_id": "p-7"})])
        self.assertEqual(result["status"], OPENED)
        self.assertEqual(
            (result["workspace_id"], result["group_id"], result["pane_id"]),
            ("ws-moved", "g-3", "p-7"),
        )
        self.assertEqual(result["session_name"], "gridvibe_main")
        self.assertNotIn("session_id", result)

    def test_a_closed_pane_is_gridvibes_refusal_and_nothing_is_shown(self):
        opener = StubOpener(raises=http_error(404, {"error": "Session not found"}))

        result = dispatch(
            "focus_pane",
            {"pane_id": "gone"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=lambda *_a, **_k: self.fail("a closed pane was shown"),
        )

        self.assertEqual(result["status"], 404)

    def test_an_unreadable_workspace_is_refused_rather_than_guessed(self):
        opener = StubOpener([
            {"session_id": "p-7", "group_id": "g-3"},
            {},  # the arrangement answered nothing
        ])

        result = dispatch(
            "focus_pane",
            {"pane_id": "p-7"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=lambda *_a, **_k: self.fail("shown in a guessed workspace"),
        )

        self.assertEqual(result["kind"], "unresolved")
        self.assertFalse(result["changed"])

    def test_focus_pane_needs_a_pane_before_anything_leaves_the_process(self):
        for args in ({}, {"session_id": "p-7"}):
            with self.subTest(args=args):
                result = dispatch(
                    "focus_pane",
                    args,
                    client=client_for(RefusingOpener(self)),
                    identity=read_identity(INSIDE_PANE),
                )
                self.assertEqual(result["kind"], "invalid_arguments")
                self.assertIn("pane_id", result["error"])


class SessionNamesInReadsTestCase(unittest.TestCase):
    """Every read that mentions a session says the name its tab shows."""

    def test_list_workspaces_lists_every_open_tab_by_name(self):
        result = dispatch(
            "list_workspaces",
            {},
            client=client_for(StubOpener(_tab_reads())),
            identity=read_identity(INSIDE_PANE),
        )

        first = result["workspaces"][0]
        self.assertEqual(first["label"], "gridvibe")
        self.assertEqual(first["sessions"], [
            {"session_name": "Session 08:41:02", "group_id": "g-scratch", "pane_count": 2, "active": False},
            {"session_name": "gridvibe_main", "group_id": "g-main", "pane_count": 3, "active": True},
        ])
        self.assertEqual(result["workspaces"][1]["sessions"][0]["session_name"], "mcp_stage")

    def test_a_tab_with_no_name_is_named_by_its_id_as_the_tab_strip_does(self):
        result = dispatch(
            "list_workspaces",
            {},
            client=client_for(StubOpener([
                {"workspaces": [{"workspace_id": "ws-1", "label": "w"}]},
                {"groups": [{"group_id": "g-bare", "name": ""}]},
            ])),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["workspaces"][0]["sessions"][0]["session_name"], "g-bare")

    def test_list_panes_narrows_to_a_named_session_in_another_workspace(self):
        opener = StubOpener(_tab_reads() + [
            {"group_id": "g-review", "workspace_id": "ws-2", "panes": []},
            {"sessions": [{"session_id": "p-9", "group_id": "g-review", "title": "Review"}]},
            WS2_GROUPS,
        ])

        result = dispatch(
            "list_panes",
            {"session_name": "mcp_stage"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        sessions_url = [r.full_url for r in opener.requests if "/api/sessions?" in r.full_url][0]
        self.assertIn("workspace_id=ws-2", sessions_url)
        self.assertIn("group_id=g-review", sessions_url)
        self.assertEqual(result["panes"][0]["pane_id"], "p-9")
        self.assertEqual(result["panes"][0]["session_name"], "mcp_stage")
        self.assertEqual(result["session"]["workspace_label"], "gridvibe_2")

    def test_list_panes_refuses_an_unknown_session_name_and_lists_nothing(self):
        result = dispatch(
            "list_panes",
            {"session_name": "nope"},
            client=client_for(StubOpener(_tab_reads())),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "not_found")
        self.assertIn("No panes were listed", result["error"])

    def test_whoami_names_the_session_this_pane_is_in(self):
        opener = StubOpener([
            {"session_id": "pane-1", "group_id": "group-1", "mode": "wsl", "host": "cmd"},
            {"group_id": "group-1", "workspace_id": "ws-1", "panes": []},
            {"groups": [{"group_id": "group-1", "name": "gridvibe_main"}]},
        ])

        result = dispatch(
            "whoami", {}, client=client_for(opener), identity=read_identity(INSIDE_PANE)
        )

        self.assertEqual(result["session_name"], "gridvibe_main")
        self.assertEqual(result["pane_id"], "pane-1")
        self.assertEqual(result["group_id"], "group-1")

    def test_a_launch_says_the_name_its_tab_actually_got(self):
        opener = StubOpener([{
            "workspace_id": "ws-1",
            "group_id": "g-new",
            "group": {"group_id": "g-new", "name": "gridvibe (1)"},
            "sessions": [{"session_id": "p-1", "group_id": "g-new"}],
        }])

        result = dispatch(
            "launch_panes",
            {"panes": [{"kind": "terminal"}], "session_name": "gridvibe"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["session_name"], "gridvibe (1)")
        self.assertEqual(result["panes"][0]["pane_id"], "p-1")
        self.assertEqual(result["request"]["origin_pane_id"], "pane-1")
        self.assertEqual(len(result["request"]["panes"]), 1)
        self.assertNotIn("sessions", result["request"])


class VocabularyTestCase(unittest.TestCase):
    """A session is a tab and a pane is a pane, in every tool, both ways."""

    def test_no_tool_takes_a_session_id(self):
        for spec in tool_specs():
            with self.subTest(tool=spec["name"]):
                properties = set(spec["inputSchema"].get("properties") or {})
                self.assertEqual(properties & {"session_id", "session_ids"}, set())

    def test_every_pane_tool_takes_a_pane_id(self):
        for name in ("split_pane", "set_pane_agent", "set_pane_mode", "clear_pane", "focus_pane"):
            with self.subTest(tool=name):
                spec = next(item for item in tool_specs() if item["name"] == name)
                self.assertIn("pane_id", spec["inputSchema"]["required"])

    def test_every_tool_that_names_a_session_takes_its_tab_name(self):
        for name in ("focus_session", "move_session", "list_panes", "launch_panes"):
            with self.subTest(tool=name):
                spec = next(item for item in tool_specs() if item["name"] == name)
                self.assertIn("session_name", spec["inputSchema"]["properties"])

    def test_no_result_key_calls_a_pane_a_session(self):
        published = _publish({
            "session_id": "p-1",
            "saved_session_id": "preset-1",
            "group_name": "gridvibe_main",
            "panes": [{"session_id": "p-2", "neighbours": {"above": ["p-1"]}}],
            "handoff": {"from_session_id": "p-0"},
        })

        self.assertEqual(published, {
            "pane_id": "p-1",
            # A saved preset's id is not a pane, and keeps its name.
            "saved_session_id": "preset-1",
            "session_name": "gridvibe_main",
            "panes": [{"pane_id": "p-2", "neighbours": {"above": ["p-1"]}}],
            "handoff": {"from_pane_id": "p-0"},
        })

    def test_the_descriptions_say_what_a_session_is(self):
        for name in ("list_workspaces", "list_panes", "focus_session", "move_session"):
            with self.subTest(tool=name):
                spec = next(item for item in tool_specs() if item["name"] == name)
                self.assertIn("session tab", spec["description"])

    def test_open_window_no_longer_switches_tabs(self):
        spec = next(item for item in tool_specs() if item["name"] == "open_window")

        self.assertEqual(set(spec["inputSchema"]["properties"]), {"workspace_id"})
        self.assertIn("focus_session", spec["description"])


class _FlaskOpener:
    """Answers the sidecar's requests from the real routes, in process."""

    def __init__(self, flask_client):
        self.flask = flask_client

    def open(self, request, timeout=None):
        url = urllib.parse.urlsplit(request.full_url)
        path = url.path + (f"?{url.query}" if url.query else "")
        response = self.flask.open(
            path, method=request.get_method(), data=request.data,
            content_type="application/json",
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url, response.status_code, "error", {}, io.BytesIO(response.data)
            )
        return StubResponse(response.get_json())


class LiveSessionNamesTestCase(unittest.TestCase):
    """Against the real registry: a tab's name needs neither a save nor a restart."""

    def setUp(self):
        from web import api

        self.api = api
        api.app.config["TESTING"] = True
        self.http = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.client = client_for(_FlaskOpener(self.http))

    def _scratch_group(self, name):
        group = self.api.session_manager.create_group(
            name=name, connection_mode="wsl", layout="single", terminal_count=1,
        )
        self.api.session_manager.create_session(
            group.group_id, host="cmd", directory="C:/repo", mode="wsl",
            startup_mode="terminal",
        )
        return group.group_id

    def _focus(self, name):
        seen = []
        result = dispatch(
            "focus_session",
            {"session_name": name},
            client=self.client,
            identity=read_identity(INSIDE_PANE),
            window_opener=lambda _c, ws, group, **_k: seen.append((ws, group)) or {
                "status": OPENED, "group_activated": True,
            },
        )
        return result, seen

    def test_an_unsaved_tab_is_found_by_its_name(self):
        group_id = self._scratch_group("Session 08:41:02")

        result, seen = self._focus("Session 08:41:02")

        self.assertEqual(seen, [("default", group_id)])
        self.assertEqual(result["session_name"], "Session 08:41:02")

    def test_a_save_renames_the_live_tab_at_once(self):
        group_id = self._scratch_group("Session 08:41:02")

        saved = self.http.post("/api/saved-sessions", json={
            "name": "mcp_stage",
            "config": {"connection_mode": "wsl", "terminal_count": 1, "layout": "single"},
            "workspace_only": True,
            "group_id": group_id,
        })
        self.assertEqual(saved.status_code, 201)

        result, seen = self._focus("mcp_stage")

        # The same live tab, under its new name: no restart, no new group id.
        self.assertEqual(seen, [("default", group_id)])
        self.assertEqual(result["session_name"], "mcp_stage")
        listed = dispatch(
            "list_workspaces", {}, client=self.client, identity=read_identity(INSIDE_PANE)
        )
        names = [tab["session_name"] for tab in listed["workspaces"][0]["sessions"]]
        self.assertEqual(names, ["mcp_stage"])


class MoveSessionTestCase(unittest.TestCase):
    def _dispatch(self, args, opener, identity=INSIDE_PANE, window_opener=None):
        return dispatch(
            "move_session",
            args,
            client=client_for(opener),
            identity=read_identity(identity),
            window_opener=window_opener or (lambda *_a, **_k: self.fail("nothing to show")),
        )

    def test_arguments_that_cannot_name_one_session_and_one_destination_never_leave(self):
        cases = [
            {"target_workspace_id": "ws-2"},
            {"group_id": "g-1", "session_name": "Main", "target_workspace_id": "ws-2"},
            {"group_id": "g-1"},
            {"group_id": "g-1", "target_workspace_id": "ws-2", "new_workspace": True},
            {"group_id": "g-1", "target_workspace_id": "ws-2", "target_workspace_label": "x"},
            {"group_id": "g-1", "target_workspace_id": "ws-2", "workspace_label": "X"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._dispatch(args, RefusingOpener(self))
                self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_with_no_pane_cannot_move_a_session(self):
        result = self._dispatch(
            {"group_id": "g-1", "target_workspace_id": "ws-2"},
            RefusingOpener(self),
            identity={"GRIDVIBE_URL": "http://127.0.0.1:5050"},
        )

        self.assertEqual(result["kind"], "invalid_arguments")
        self.assertIn("no pane", result["error"])

    def test_the_move_names_the_caller_and_forwards_only_what_was_stated(self):
        opener = StubOpener([
            {
                "moved": True,
                "group_id": "g-1",
                "group_name": "Main",
                "workspace_id": "ws-2",
                "source_workspace_id": "ws-1",
                "current_workspace_id": "ws-2",
                "groups": [{"group_id": "g-1"}],
            },
        ])

        result = self._dispatch({"group_id": "g-1", "target_workspace_id": "ws-2"}, opener)

        method, url, body = _bodies(opener)[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/api/session-groups/g-1/agent-move"))
        self.assertEqual(
            body, {"requested_by_session_id": "pane-1", "target_workspace_id": "ws-2"}
        )
        self.assertTrue(result["moved"])
        self.assertEqual(result["session_name"], "Main")
        self.assertEqual(result["current_workspace_id"], "ws-2")
        # A field list: the windows' group lists stay out of the answer.
        self.assertNotIn("groups", result)
        self.assertNotIn("shown", result)

    def test_override_and_a_new_workspace_are_forwarded_as_stated(self):
        opener = StubOpener([{"moved": True, "group_id": "g-1"}])

        self._dispatch(
            {"group_id": "g-1", "new_workspace": True, "workspace_label": "Side", "override": True},
            opener,
        )

        self.assertEqual(
            _bodies(opener)[0][2],
            {
                "requested_by_session_id": "pane-1",
                "new_workspace": True,
                "label": "Side",
                "override": True,
            },
        )

    def test_names_on_both_ends_resolve_to_ids(self):
        opener = StubOpener(_tab_reads() + [
            WORKSPACES,
            {"moved": True, "group_id": "g-main", "current_workspace_id": "ws-2"},
        ])

        result = self._dispatch(
            {"session_name": "gridvibe_main", "target_workspace_label": "gridvibe_2"}, opener
        )

        method, url, body = _bodies(opener)[-1]
        self.assertTrue(url.endswith("/api/session-groups/g-main/agent-move"))
        self.assertEqual(body["target_workspace_id"], "ws-2")
        self.assertTrue(result["moved"])

    def test_an_unknown_destination_label_moves_nothing(self):
        opener = StubOpener(_tab_reads() + [WORKSPACES])

        result = self._dispatch(
            {"session_name": "gridvibe_main", "target_workspace_label": "nowhere"}, opener
        )

        self.assertEqual(result["kind"], "not_found")
        self.assertFalse(result["changed"])
        self.assertEqual([w["label"] for w in result["workspaces"]], ["gridvibe", "gridvibe_2"])
        self.assertFalse(any(method == "POST" for method, _u, _b in _bodies(opener)))

    def test_a_shared_name_presents_every_candidate_and_moves_nothing(self):
        opener = StubOpener([
            {"workspaces": [
                {"workspace_id": "ws-1", "label": "gridvibe"},
                {"workspace_id": "ws-2", "label": "gridvibe_2"},
            ]},
            {"groups": [{"group_id": "g-1", "name": "test_session", "terminal_count": 2}]},
            {"groups": [
                {"group_id": "g-2", "name": "test_session", "terminal_count": 1},
                {"group_id": "g-3", "name": "other"},
            ]},
        ])

        result = self._dispatch(
            {"session_name": "test_session", "target_workspace_id": "ws-2"}, opener
        )

        self.assertEqual(result["kind"], "ambiguous")
        self.assertFalse(result["changed"])
        self.assertIn("Nothing was moved", result["error"])
        self.assertEqual([item["group_id"] for item in result["candidates"]], ["g-1", "g-2"])
        self.assertEqual(result["candidates"][1]["workspace_label"], "gridvibe_2")
        self.assertFalse(any(method == "POST" for method, _u, _b in _bodies(opener)))

    def test_a_name_nobody_has_is_not_found_and_moves_nothing(self):
        opener = StubOpener([
            {"workspaces": [{"workspace_id": "ws-1"}]},
            {"groups": []},
        ])

        result = self._dispatch({"session_name": "nope", "target_workspace_id": "ws-2"}, opener)

        self.assertEqual(result["kind"], "not_found")
        self.assertFalse(result["changed"])
        self.assertFalse(any(method == "POST" for method, _u, _b in _bodies(opener)))

    def test_a_gate_refusal_carries_its_question_and_says_nothing_changed(self):
        opener = StubOpener(raises=http_error(403, {
            "error": "[lineage gate] This session group is not this agent's own.",
            "gate": "lineage",
            "waivable": True,
            "changed": False,
            "confirm": {"question": "Move session 'Main' (2 panes, still running) ...?"},
        }))

        result = self._dispatch({"group_id": "g-1", "target_workspace_id": "ws-2"}, opener)

        self.assertEqual(result["gate"], "lineage")
        self.assertTrue(result["waivable"])
        self.assertFalse(result["changed"])
        self.assertIn("Move session", result["confirm"]["question"])

    def test_show_raises_the_destination_after_the_move(self):
        opener = StubOpener([
            {"moved": True, "group_id": "g-1", "workspace_id": "ws-2", "current_workspace_id": "ws-2"},
        ])
        seen = []

        result = self._dispatch(
            {"group_id": "g-1", "target_workspace_id": "ws-2", "show": True},
            opener,
            window_opener=lambda _c, ws, group, **_k: seen.append((ws, group)) or {
                "status": OPENED, "group_activated": True
            },
        )

        self.assertEqual(seen, [("ws-2", "g-1")])
        self.assertTrue(result["moved"])
        self.assertEqual(result["shown"]["status"], OPENED)
        self.assertTrue(result["shown"]["session_activated"])

    def test_a_show_that_failed_is_not_a_failed_move(self):
        opener = StubOpener([
            {"moved": True, "group_id": "g-1", "current_workspace_id": "ws-2"},
        ])

        def unreachable(*_args, **_kwargs):
            raise GridVibeError("GridVibe is not reachable.", kind="unreachable")

        result = self._dispatch(
            {"group_id": "g-1", "target_workspace_id": "ws-2", "show": True},
            opener,
            window_opener=unreachable,
        )

        self.assertTrue(result["moved"])
        self.assertNotIn("error", result)
        self.assertEqual(result["shown"]["kind"], "unreachable")


class NavigationDescriptionTestCase(unittest.TestCase):
    def _spec(self, name):
        return next(item for item in tool_specs() if item["name"] == name)

    def test_move_session_says_nothing_restarts_and_who_it_may_move(self):
        description = self._spec("move_session")["description"]

        self.assertIn("Nothing restarts", description)
        self.assertIn("confirm.question", description)
        self.assertIn("'moved': false", description)

    def test_override_says_only_the_persons_word_justifies_it(self):
        override = self._spec("move_session")["inputSchema"]["properties"]["override"]

        self.assertIn("person explicitly asked", override["description"])

    def test_focus_pane_says_what_blocks_it_and_that_it_types_nothing(self):
        description = self._spec("focus_pane")["description"]

        self.assertIn("unsaved editor", description)
        self.assertIn("Nothing is typed", description)

    def test_focus_pane_says_how_to_find_a_pane_in_a_named_session(self):
        description = self._spec("focus_pane")["description"]

        self.assertIn("list_panes", description)
        self.assertIn("session_name", description)

    def test_focus_session_says_what_blocks_it(self):
        description = self._spec("focus_session")["description"]

        self.assertIn("unsaved editor", description)
        self.assertIn("candidates", description)


if __name__ == "__main__":
    unittest.main()
