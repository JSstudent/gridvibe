"""The sidecar's navigation tools: `focus_pane`, `move_group`, and the tab step
`open_window` now takes.

- **A raised window is not a switched tab.** With a group named, `opened` is
  only ever the page's own `activated`; a refusal is `blocked` with the page's
  reason and `window_raised: true`; silence is `no_window_available`. Browser
  mode opens the tab and says it is unverified.
- **`focus_pane` reads where the pane is now**, from the pane and its group's
  live arrangement, never from the caller's spawn-time workspace.
- **`move_group` refuses before the wire** when its arguments cannot name one
  group and one destination, presents every candidate when a name is shared,
  and never reports a failed `show` as a failed move.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.client import GridVibeError  # noqa: E402
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import dispatch, tool_specs  # noqa: E402
from gridvibe_mcp.windows import (  # noqa: E402
    BLOCKED,
    NO_WINDOW_AVAILABLE,
    OPENED,
    open_window,
)
from tests.test_mcp_client import StubOpener, client_for, http_error  # noqa: E402
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


class FocusPaneTestCase(unittest.TestCase):
    def test_the_pane_is_shown_where_its_group_is_now(self):
        opener = StubOpener([
            {"session_id": "p-7", "group_id": "g-3", "title": "Codex"},
            # The group's live arrangement names its current workspace.
            {"group_id": "g-3", "workspace_id": "ws-moved", "panes": []},
        ])
        seen = []

        def opener_fn(client, workspace_id, group_id, **kwargs):
            seen.append((workspace_id, group_id, kwargs))
            return {"status": OPENED, "group_activated": True, "focused": True}

        result = dispatch(
            "focus_pane",
            {"session_id": "p-7"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=opener_fn,
        )

        # Not INSIDE_PANE's spawn-time ws-1.
        self.assertEqual(seen, [("ws-moved", "g-3", {"session_id": "p-7"})])
        self.assertEqual(result["status"], OPENED)
        self.assertEqual(
            (result["workspace_id"], result["group_id"], result["session_id"]),
            ("ws-moved", "g-3", "p-7"),
        )

    def test_a_closed_pane_is_gridvibes_refusal_and_nothing_is_shown(self):
        opener = StubOpener(raises=http_error(404, {"error": "Session not found"}))

        result = dispatch(
            "focus_pane",
            {"session_id": "gone"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=lambda *_a, **_k: self.fail("a closed pane was shown"),
        )

        self.assertEqual(result["status"], 404)
        self.assertIn("Session not found", result["error"])

    def test_an_unreadable_workspace_is_refused_rather_than_guessed(self):
        opener = StubOpener([
            {"session_id": "p-7", "group_id": "g-3"},
            {},  # the arrangement answered nothing
        ])

        result = dispatch(
            "focus_pane",
            {"session_id": "p-7"},
            client=client_for(opener),
            identity=read_identity(INSIDE_PANE),
            window_opener=lambda *_a, **_k: self.fail("shown in a guessed workspace"),
        )

        self.assertEqual(result["kind"], "unresolved")
        self.assertFalse(result["changed"])

    def test_focus_pane_needs_a_pane_before_anything_leaves_the_process(self):
        result = dispatch(
            "focus_pane",
            {},
            client=client_for(RefusingOpener(self)),
            identity=read_identity(INSIDE_PANE),
        )

        self.assertEqual(result["kind"], "invalid_arguments")


class MoveGroupTestCase(unittest.TestCase):
    def _dispatch(self, args, opener, identity=INSIDE_PANE, window_opener=None):
        return dispatch(
            "move_group",
            args,
            client=client_for(opener),
            identity=read_identity(identity),
            window_opener=window_opener or (lambda *_a, **_k: self.fail("nothing to show")),
        )

    def test_arguments_that_cannot_name_one_group_and_one_destination_never_leave(self):
        cases = [
            {"target_workspace_id": "ws-2"},
            {"group_id": "g-1", "session_name": "Main", "target_workspace_id": "ws-2"},
            {"group_id": "g-1"},
            {"group_id": "g-1", "target_workspace_id": "ws-2", "new_workspace": True},
            {"group_id": "g-1", "target_workspace_id": "ws-2", "workspace_label": "X"},
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self._dispatch(args, RefusingOpener(self))
                self.assertEqual(result["kind"], "invalid_arguments")

    def test_an_agent_with_no_pane_cannot_move_a_group(self):
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
        self.assertEqual([item["group_id"] for item in result["candidates"]], ["g-1", "g-2"])
        self.assertEqual(result["candidates"][1]["workspace_label"], "gridvibe_2")
        self.assertFalse(any(method == "POST" for method, _u, _b in _bodies(opener)))

    def test_a_unique_name_resolves_to_its_group(self):
        opener = StubOpener([
            {"workspaces": [{"workspace_id": "ws-1", "label": "gridvibe"}]},
            {"groups": [{"group_id": "g-1", "name": "test_session"}]},
            {"moved": True, "group_id": "g-1", "current_workspace_id": "ws-2"},
        ])

        result = self._dispatch(
            {"session_name": "test_session", "target_workspace_id": "ws-2"}, opener
        )

        self.assertTrue(result["moved"])
        self.assertTrue(_bodies(opener)[-1][1].endswith("/api/session-groups/g-1/agent-move"))

    def test_a_name_nobody_has_is_not_found_and_moves_nothing(self):
        opener = StubOpener([
            {"workspaces": [{"workspace_id": "ws-1"}]},
            {"groups": []},
        ])

        result = self._dispatch({"session_name": "nope", "target_workspace_id": "ws-2"}, opener)

        self.assertEqual(result["kind"], "not_found")
        self.assertFalse(result["changed"])

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

    def test_move_group_says_nothing_restarts_and_who_it_may_move(self):
        description = self._spec("move_group")["description"]

        self.assertIn("Nothing restarts", description)
        self.assertIn("confirm.question", description)
        self.assertIn("'moved': false", description)

    def test_override_says_only_the_persons_word_justifies_it(self):
        override = self._spec("move_group")["inputSchema"]["properties"]["override"]

        self.assertIn("person explicitly asked", override["description"])

    def test_focus_pane_says_what_blocks_it_and_that_it_types_nothing(self):
        description = self._spec("focus_pane")["description"]

        self.assertIn("unsaved editor", description)
        self.assertIn("Nothing is typed", description)


if __name__ == "__main__":
    unittest.main()
