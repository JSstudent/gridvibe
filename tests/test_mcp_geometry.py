"""A resize is a single page intent and only an acknowledged layout is success."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401
from gridvibe_mcp.geometry import resize_divider  # noqa: E402
from gridvibe_mcp.identity import PaneIdentity  # noqa: E402
from gridvibe_mcp.server import dispatch, tool_names  # noqa: E402
from web import api  # noqa: E402
from web.window_intents import window_intents  # noqa: E402


class ResizeRouteTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        window_intents.reset()
        self.addCleanup(api.session_manager.reset_sessions)
        self.addCleanup(window_intents.reset)
        self.group = api.session_manager.create_group(
            name="Geometry", connection_mode="wsl", layout="vertical", terminal_count=2,
        )
        for _ in range(2):
            api.session_manager.create_session(
                group_id=self.group.group_id, host="cmd", directory="C:/repo",
                mode="wsl", startup_mode="terminal",
            )

    def _post(self, **overrides):
        body = {"axis": "vertical", "line_index": 1, "position": 0.6,
                "expected_revision": self.group.presentation_revision}
        body.update(overrides)
        return self.client.post(
            f"/api/session-groups/{self.group.group_id}/resize-intent", json=body,
        )

    def test_record_claim_and_acknowledge_one_resize(self):
        layout = self.client.get(
            f"/api/panes/layout?group_id={self.group.group_id}"
        ).get_json()
        self.assertEqual(layout["presentation_revision"], self.group.presentation_revision)
        recorded = self._post()
        self.assertEqual(recorded.status_code, 201)
        intent = recorded.get_json()
        self.assertEqual(intent["kind"], "resize")
        self.assertEqual(intent["position"], 0.6)
        intent_id = intent["intent_id"]
        self.assertEqual(self.client.post(
            f"/api/windows/intents/{intent_id}/claim", json={}
        ).status_code, 200)
        self.assertEqual(self.client.post(
            f"/api/windows/intents/{intent_id}/claim", json={}
        ).status_code, 409)
        result = {"group_id": self.group.group_id, "revision": 1,
                  "column_weights": [1.2, 0.8], "row_weights": [1],
                  "panes": [{"session_id": "p", "index": 0,
                             "rect": {"x": 1, "y": 1, "w": 1, "h": 1,
                                      "secret": "hidden"}, "password": "hidden"}]}
        ack = self.client.post(f"/api/windows/intents/{intent_id}/result",
                               json={"outcome": "resized", "result": result})
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.get_json()["result"]["column_weights"], [1.2, 0.8])
        self.assertNotIn("hidden", str(ack.get_json()))

    def test_invalid_and_stale_requests_record_nothing(self):
        for changes in ({"position": 0}, {"position": 1.2},
                        {"line_index": 0}, {"expected_revision": 99}):
            with self.subTest(changes=changes):
                self.assertGreaterEqual(self._post(**changes).status_code, 400)
                self.assertEqual(window_intents.pending(), [])
        layout = self.client.get(
            f"/api/panes/layout?group_id={self.group.group_id}"
        ).get_json()
        self.assertEqual(layout["geometry"]["column_weights"], [1.0, 1.0])


class ResizeToolTestCase(unittest.TestCase):
    def test_surface_and_argument_validation(self):
        self.assertIn("resize_divider", tool_names())
        for bad in ({"position": 1}, {"line_index": 0},
                    {"expected_revision": -1}):
            args = {"group_id": "g", "axis": "vertical", "line_index": 1,
                    "position": 0.5, "expected_revision": 0, **bad}
            result = dispatch("resize_divider", args, client=object(),
                              identity=PaneIdentity())
            self.assertEqual(result["kind"], "invalid_arguments")

    def test_success_refusal_and_expiry(self):
        class Client:
            def __init__(self, state):
                self.state = state
                self.body = None

            def resize_intent(self, group_id, body):
                self.body = body
                return {"intent_id": "intent-1"}

            def read_window_intent(self, intent_id):
                return {"state": self.state, "detail": "Too narrow", "result": {
                    "group_id": "g", "revision": 2,
                    "column_weights": [1.2, 0.8], "row_weights": [1],
                    "panes": [{"session_id": "p", "index": 0,
                               "rect": {"x": 1, "y": 1, "w": 1, "h": 1},
                               "password": "hidden"}],
                    "password": "hidden",
                }}

        success = Client("resized")
        result = resize_divider(success, "g", "vertical", 1, 0.6, 1)
        self.assertEqual(result["status"], "resized")
        self.assertEqual(success.body["expected_revision"], 1)
        self.assertNotIn("password", str(result))
        refused = resize_divider(Client("refused"), "g", "vertical", 1, 0.6, 1)
        self.assertEqual(refused["status"], "refused")
        self.assertFalse(refused["changed"])
        expired = resize_divider(Client("expired"), "g", "vertical", 1, 0.6, 1)
        self.assertEqual(expired["status"], "no_window_available")
        self.assertIn("read list_panes", expired["detail"])
        self.assertNotIn("changed", expired)

    def test_dispatch_calls_same_transport_independent_helper(self):
        with patch("gridvibe_mcp.server.resize_divider_for", return_value={"status": "resized"}) as call:
            result = dispatch("resize_divider", {
                "group_id": "g", "axis": "vertical", "line_index": 1,
                "position": 0.5, "expected_revision": 0,
            }, client=object(), identity=PaneIdentity())
        self.assertEqual(result["status"], "resized")
        self.assertEqual(call.call_args.args[1:], ("g", "vertical", 1, 0.5, 0))
