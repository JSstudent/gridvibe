"""Whole-target MCP closes: gates, ownership, partial results and assignments."""

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OTHER_WORKSPACE = "222222222222"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - isolate durable files
from gridvibe_mcp.identity import read_identity  # noqa: E402
from gridvibe_mcp.server import dispatch  # noqa: E402
from sessions.manager import SessionManager  # noqa: E402
from tests.test_mcp_client import StubOpener, client_for, http_error  # noqa: E402
from web.agent_results import ResultStore  # noqa: E402
from web.mcp_close import close_for_agent  # noqa: E402


class CloseRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager()
        self.manager.create_group("Caller", "local", "single", 1, group_id="caller")
        self.caller = self._pane("caller", title="Caller", startup_mode="agent", agent_selection="codex")
        self.manager.create_workspace("Other", workspace_id=OTHER_WORKSPACE)
        self.manager.create_group("Workers", "local", "vertical", 2, group_id="workers", workspace_id=OTHER_WORKSPACE)
        self.worker = self._pane("workers", title="Worker", created_by_session_id=self.caller.session_id)
        self.second = self._pane("workers", title="Second", created_by_session_id=self.caller.session_id)
        self.effects = []
        self.patches = [
            patch("web.mcp_close.session_manager", self.manager),
            patch("web.pane_gates.session_manager", self.manager),
            patch("web.terminal_io._close_ssh_connection", side_effect=lambda sid, **_: self.effects.append(("teardown", sid))),
            patch("web.terminal_io._broadcast_session_groups_updated", side_effect=lambda *a, **k: self.effects.append(("broadcast", a, k))),
            patch("web.workspaces.forget_pruned_workspaces", side_effect=lambda ids: self.effects.append(("pruned", ids))),
            patch("web.workspaces.forget_emptied_default_workspace", side_effect=lambda sid: self.effects.append(("default", sid))),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def _pane(self, group, **fields):
        return self.manager.create_session(group, host="localhost", directory=".", **fields)

    def close(self, kind, target, override=False):
        return close_for_agent(kind, target, {"requested_by_session_id": self.caller.session_id, "override": override})

    def broadcasts(self):
        return [effect for effect in self.effects if effect[0] == "broadcast"]

    def test_pane_close_reports_just_that_pane(self):
        answer, status = self.close("pane", self.worker.session_id)
        self.assertEqual(status, 200)
        self.assertEqual(answer["closed_session_ids"], [self.worker.session_id])
        self.assertEqual(answer["target"]["name"], "Worker")
        self.assertEqual(answer["closed_group_ids"], [])
        self.assertIsNone(self.manager.get_session(self.worker.session_id))
        self.assertIsNotNone(self.manager.get_session(self.second.session_id))
        _kind, args, kwargs = self.broadcasts()[0]
        self.assertEqual(args, ("session_closed",))
        self.assertEqual(kwargs["group_id"], "workers")
        self.assertEqual(kwargs["closed_session_ids"], [self.worker.session_id])
        self.assertEqual(kwargs["closed_group_ids"], [])

    def test_post_route_uses_the_same_gated_close(self):
        from web import api
        response = api.app.test_client().post(
            f"/api/mcp/close/pane/{self.worker.session_id}",
            json={"requested_by_session_id": self.caller.session_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["closed_session_ids"], [self.worker.session_id])

    def test_self_containing_group_and_workspace_refuse_even_with_override(self):
        for kind, target in (("pane", self.caller.session_id), ("group", "caller"), ("workspace", "default")):
            with self.subTest(kind=kind):
                answer, status = self.close(kind, target, override=True)
                self.assertEqual(status, 403)
                self.assertEqual(answer["gate"], "self")
                self.assertFalse(answer["waivable"])
                self.assertEqual(self.effects, [])

    def test_group_preflights_all_panes_and_names_the_waivable_target(self):
        self.second.created_by_session_id = "someone-else"
        answer, status = self.close("group", "workers")
        self.assertEqual(status, 403)
        self.assertEqual(answer["gate"], "lineage")
        self.assertEqual(answer["confirm"]["target"], {"kind": "group", "id": "workers", "name": "Workers"})
        self.assertEqual(answer["confirm"]["affected_session_ids"], [self.worker.session_id, self.second.session_id])
        self.assertEqual([pane["title"] for pane in answer["confirm"]["affected_panes"]], ["Worker", "Second"])
        self.assertEqual(self.effects, [])
        self.assertIsNotNone(self.manager.get_session(self.worker.session_id))

    def test_running_agent_needs_override_and_workspace_preserves_snapshot(self):
        self.second.startup_mode = "agent"
        self.second.agent_selection = "claude"
        answer, status = self.close("workspace", OTHER_WORKSPACE)
        self.assertEqual(status, 403)
        self.assertEqual(answer["gate"], "mode")
        self.assertEqual(answer["confirm"]["target"]["kind"], "workspace")
        self.assertIn(self.second.session_id, answer["confirm"]["affected_session_ids"])
        answer, status = self.close("workspace", OTHER_WORKSPACE, override=True)
        self.assertEqual(status, 200)
        self.assertEqual(answer["closed_session_ids"], [self.worker.session_id, self.second.session_id])
        self.assertEqual(answer["closed_group_ids"], ["workers"])
        self.assertEqual(answer["closed_workspace_ids"], [OTHER_WORKSPACE])
        self.assertFalse(any(effect[0] in ("pruned", "default") for effect in self.effects))
        _kind, args, kwargs = self.broadcasts()[0]
        self.assertEqual(args, ("workspace_closed",))
        self.assertEqual(
            kwargs["closed_session_ids"],
            [self.worker.session_id, self.second.session_id],
        )
        self.assertEqual(kwargs["closed_group_ids"], ["workers"])

    def test_group_close_ends_only_its_panes_and_forgets_an_emptied_workspace(self):
        answer, status = self.close("group", "workers")
        self.assertEqual(status, 200)
        self.assertEqual(answer["closed_session_ids"], [self.worker.session_id, self.second.session_id])
        self.assertEqual(answer["closed_group_ids"], ["workers"])
        self.assertEqual(answer["closed_workspace_ids"], [OTHER_WORKSPACE])
        self.assertIsNotNone(self.manager.get_session(self.caller.session_id))
        self.assertIn(("pruned", [OTHER_WORKSPACE]), self.effects)
        _kind, args, kwargs = self.broadcasts()[0]
        self.assertEqual(args, ("group_closed",))
        self.assertEqual(kwargs["closed_group_ids"], ["workers"])

    def test_interrupted_group_close_reports_exact_closed_ids(self):
        real_close = self.manager.close_session
        def interrupted(session_id):
            if session_id == self.second.session_id:
                raise RuntimeError("private path C:/internal/credentials.json")
            return real_close(session_id)
        with patch.object(self.manager, "close_session", side_effect=interrupted), \
             patch("web.mcp_close.logger") as close_log:
            answer, status = self.close("group", "workers")
        self.assertEqual(status, 500)
        self.assertTrue(answer["partial"])
        self.assertEqual(answer["closed_session_ids"], [self.worker.session_id])
        self.assertEqual(answer["closed_group_ids"], [])
        self.assertNotIn("C:/internal/credentials.json", answer["error"])
        self.assertNotIn("C:/internal/credentials.json", str(close_log.error.call_args_list))
        self.assertIsNotNone(self.manager.get_session(self.second.session_id))
        _kind, args, kwargs = self.broadcasts()[0]
        self.assertEqual(args, ("session_closed",))
        self.assertEqual(kwargs["group_id"], "workers")
        self.assertEqual(kwargs["closed_session_ids"], [self.worker.session_id])
        self.assertEqual(kwargs["closed_group_ids"], [])

    def test_reentrant_ownership_change_stops_before_closing_the_moved_pane(self):
        real_close = self.manager.close_session
        def move_after_first(session_id):
            result = real_close(session_id)
            if session_id == self.worker.session_id:
                self.second.group_id = "caller"
            return result
        with patch.object(self.manager, "close_session", side_effect=move_after_first):
            answer, status = self.close("group", "workers")
        self.assertEqual(status, 500)
        self.assertEqual(answer["closed_session_ids"], [self.worker.session_id])
        self.assertIsNotNone(self.manager.get_session(self.second.session_id))

    def test_workspace_move_race_is_resolved_from_live_ownership(self):
        outcome = []
        def request_close():
            outcome.append(self.close("workspace", OTHER_WORKSPACE))
        with self.manager.lock:
            thread = threading.Thread(target=request_close)
            thread.start()
            self.manager.groups["workers"].workspace_id = "default"
        thread.join(2)
        self.assertFalse(thread.is_alive())
        answer, status = outcome[0]
        self.assertEqual(status, 200)
        self.assertEqual(answer["closed_session_ids"], [])
        self.assertIsNotNone(self.manager.get_session(self.worker.session_id))


class CloseAssignmentTestCase(unittest.TestCase):
    def test_closing_worker_ends_pending_result_with_reason(self):
        manager = SessionManager()
        manager.create_group("Workers", "local", "vertical", 2, group_id="g")
        caller = manager.create_session("g", host="localhost", directory=".")
        worker = manager.create_session("g", host="localhost", directory=".", created_by_session_id=caller.session_id)
        results = ResultStore()
        results.expect("handoff", requester_session_id=caller.session_id, worker_session_id=worker.session_id)
        with patch("web.mcp_close.session_manager", manager), patch("web.pane_gates.session_manager", manager), \
             patch("web.terminal_io.session_manager", manager), patch("web.terminal_io.agent_results", results), \
             patch("web.terminal_io._broadcast_session_groups_updated"), \
             patch("web.workspaces.forget_pruned_workspaces"), patch("web.workspaces.forget_emptied_default_workspace"):
            answer, status = close_for_agent("pane", worker.session_id, {"requested_by_session_id": caller.session_id})
        self.assertEqual(status, 200, answer)
        row = results.collect(caller.session_id, [worker.session_id])["agents"][0]
        self.assertEqual(row["state"], "ended")
        self.assertIn("closed", row["reason"])


class CloseToolTestCase(unittest.TestCase):
    def test_tool_posts_caller_identity_and_projects_exact_closed_ids(self):
        identity = read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050", "GRIDVIBE_SESSION_ID": "caller", "GRIDVIBE_GROUP_ID": "g"})
        opener = StubOpener([{"closed": True, "closed_session_ids": ["p"], "closed_group_ids": [], "closed_workspace_ids": [], "secret": "omit"}])
        answer = dispatch("close_pane", {"pane_id": "p"}, client=client_for(opener), identity=identity)
        self.assertEqual(answer["closed_pane_ids"], ["p"])
        self.assertNotIn("secret", answer)
        self.assertIn("/api/mcp/close/pane/p", opener.requests[0].full_url)

    def test_http_partial_failure_keeps_exact_ids(self):
        identity = read_identity({"GRIDVIBE_URL": "http://127.0.0.1:5050", "GRIDVIBE_SESSION_ID": "caller", "GRIDVIBE_GROUP_ID": "g"})
        opener = StubOpener([], raises=http_error(500, {
            "error": "Close was interrupted",
            "partial": True,
            "closed_session_ids": ["p1"],
            "closed_group_ids": [],
            "closed_workspace_ids": [],
        }))
        answer = dispatch("close_group", {"group_id": "g2"}, client=client_for(opener), identity=identity)
        self.assertTrue(answer["partial"])
        self.assertEqual(answer["closed_pane_ids"], ["p1"])


if __name__ == "__main__":
    unittest.main()
