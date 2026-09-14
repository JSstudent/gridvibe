"""The loopback client: what it lets through, and how it fails.

Two rules live in the client rather than in a tool handler, so that a tool
added later cannot forget either of them, and both are asserted here against a
stubbed opener rather than a live server:

- **Nothing a session holds reaches a result unless a field list names it.** The
  password case asserts that directly, on a fabricated session that carries one.
- **A refusal carries GridVibe's own sentence, verbatim and unretried.** The
  409 on a taken workspace name is the case that matters: a client that
  paraphrased it, or retried as "test (2)", would be inventing.
"""

import io
import json
import socket
import sys
import unittest
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from gridvibe_mcp.client import (  # noqa: E402
    GridVibeClient,
    GridVibeError,
    normalize_base_url,
    project,
    scrub,
)


class StubResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class StubOpener:
    """Records every request, answers from a queue of canned bodies."""

    def __init__(self, answers=None, raises=None):
        self.answers = list(answers or [])
        self.raises = raises
        self.requests = []
        self.timeouts = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.raises is not None:
            raise self.raises
        return StubResponse(self.answers.pop(0) if self.answers else {})


def http_error(status, body):
    return urllib.error.HTTPError(
        "http://127.0.0.1:5050/api/workspaces",
        status,
        "Conflict",
        {},
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def client_for(opener):
    return GridVibeClient("http://127.0.0.1:5050", timeout=7.5, opener=opener)


class AllowlistTestCase(unittest.TestCase):
    def test_a_password_never_reaches_a_result(self):
        # A pane payload as the server would send it, with the encrypted SSH
        # password a session really carries. Encrypted is still not something an
        # agent needs, so it is dropped at the client rather than per tool.
        opener = StubOpener([{
            "sessions": [{
                "session_id": "pane-1",
                "title": "Claude 1",
                "host": "local",
                "password": "gAAAAABsecret",
                "explorer_open_tabs": ["a.py", "b.py"],
                "browser_tabs": [{"url": "http://example.test"}],
                "agent_selection": "claude",
            }],
        }])

        result = client_for(opener).panes(workspace_id="ws-1")

        serialized = json.dumps(result)
        self.assertNotIn("password", serialized)
        self.assertNotIn("gAAAAABsecret", serialized)
        # Nor the view state, which is noise that inflates every result.
        self.assertNotIn("explorer_open_tabs", serialized)
        self.assertNotIn("browser_tabs", serialized)
        self.assertEqual(result["panes"][0]["agent_selection"], "claude")

    def test_a_forbidden_key_is_dropped_at_any_depth(self):
        cleaned = scrub({
            "outer": {"inner": [{"api_token": "x", "keep": 1}]},
            "encryption_key": "x",
            "keep": 2,
        })

        self.assertEqual(cleaned, {"outer": {"inner": [{"keep": 1}]}, "keep": 2})

    def test_a_field_the_server_did_not_send_is_absent_not_null(self):
        # Projecting must not fabricate keys: a pane payload from an older
        # server should be short, not full of nulls an agent would read as
        # "GridVibe says there is no title".
        self.assertEqual(project({"title": "a"}, ("title", "host")), {"title": "a"})


class FailureTestCase(unittest.TestCase):
    def test_a_conflict_carries_gridvibes_own_sentence_and_is_not_retried(self):
        sentence = 'A workspace named "test" is already open.'
        opener = StubOpener(raises=http_error(409, {"error": sentence}))

        with self.assertRaises(GridVibeError) as caught:
            client_for(opener).create_workspace("test")

        self.assertEqual(str(caught.exception), sentence)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.kind, "http")
        # One request. A client that retried would have made two, and the
        # second would have created "test (2)" behind the user's back.
        self.assertEqual(len(opener.requests), 1)

    def test_a_body_that_will_not_parse_still_reports_the_status(self):
        opener = StubOpener(raises=urllib.error.HTTPError(
            "http://127.0.0.1:5050/api/sessions", 500, "Server Error", {}, io.BytesIO(b"<html>")
        ))

        with self.assertRaises(GridVibeError) as caught:
            client_for(opener).workspaces()

        self.assertIn("500", str(caught.exception))
        self.assertEqual(caught.exception.status, 500)

    def test_an_unreachable_server_is_typed_as_such(self):
        opener = StubOpener(raises=urllib.error.URLError(ConnectionRefusedError(61, "refused")))

        with self.assertRaises(GridVibeError) as caught:
            client_for(opener).health()

        self.assertEqual(caught.exception.kind, "unreachable")
        self.assertIn("127.0.0.1:5050", str(caught.exception))

    def test_a_timeout_is_typed_apart_from_unreachable(self):
        opener = StubOpener(raises=urllib.error.URLError(socket.timeout()))

        with self.assertRaises(GridVibeError) as caught:
            client_for(opener).health()

        self.assertEqual(caught.exception.kind, "timeout")

    def test_every_call_carries_the_clients_deadline(self):
        opener = StubOpener([{"status": "healthy"}])

        client_for(opener).health()

        # The one thing that stops a tool call hanging on a server that has
        # stopped answering: urllib is never asked to wait forever.
        self.assertEqual(opener.timeouts, [7.5])


class TransportTestCase(unittest.TestCase):
    def test_a_write_states_the_origin_the_write_guard_expects(self):
        opener = StubOpener([{"workspace_id": "ws-1", "label": "test"}])

        client_for(opener).create_workspace("test")

        request = opener.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Origin"), "http://127.0.0.1:5050")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"label": "test"})

    def test_a_read_sends_no_body_and_no_origin(self):
        opener = StubOpener([{"workspaces": []}])

        client_for(opener).workspaces()

        request = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header("Origin"))

    def test_the_base_url_is_normalized_once(self):
        for given, expected in (
            ("127.0.0.1:5050", "http://127.0.0.1:5050"),
            ("http://localhost:5050/", "http://localhost:5050"),
            ("", "http://127.0.0.1:5050"),
        ):
            with self.subTest(given=given):
                self.assertEqual(normalize_base_url(given), expected)

    def test_the_dashboard_is_flattened_to_rows_an_agent_can_act_on(self):
        opener = StubOpener([{
            "workspaces": [{
                "workspace_id": "ws-1",
                "label": "test",
                "groups": [{
                    "group_id": "g-1",
                    "name": "test",
                    "panes": [
                        {"session_id": "p-1", "agent_selection": "claude", "activity": "working"},
                        {"session_id": "p-2", "agent_selection": "claude", "activity": "idle"},
                    ],
                }],
            }],
        }])

        result = client_for(opener).agents()

        self.assertEqual(result["count"], 2)
        self.assertEqual(
            [(row["session_id"], row["activity"]) for row in result["agents"]],
            [("p-1", "working"), ("p-2", "idle")],
        )
        # A row names where it is without the reader walking a tree.
        self.assertEqual(result["agents"][0]["workspace_label"], "test")
        self.assertEqual(result["agents"][0]["session_name"], "test")


if __name__ == "__main__":
    unittest.main()
