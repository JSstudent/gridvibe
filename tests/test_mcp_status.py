"""The diagnostic behind ``make mcp-status``, checked against real processes.

The failure it exists to name is a silent one. An agent CLI whose stdio child
exits immediately reports ``CONNECTION_CLOSED`` and no cause, and the pane
otherwise looks healthy -- so every check here runs the thing it is describing
rather than asking a module what it would do:

- **The SDK is checked in the interpreter the config names**, which is not the
  one running the tests. GridVibe writes ``sys.executable`` as of the moment it
  started, and an install whose venv was rebuilt since has a config naming an
  interpreter that no longer has the SDK. Spawned, so the answer is that
  interpreter's.
- **The handshake is the real one.** A sidecar that imports fine and still
  fails to serve is exactly the case an import check would pass.
- **A child that hangs is killed, not waited on.** The report is the one place
  a hung sidecar must not itself hang.
- **GridVibe being down is a note, not a failure.** The sidecar starts fine
  without it; the tools fail per call. Reporting that as broken would send a
  reader looking for a problem in the wrong half.
"""

import json
import sys
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from utils import mcp_status  # noqa: E402

#: A stand-in whose only job is to not be a Python interpreter with the SDK.
_NO_SDK = [sys.executable, "-c", "raise SystemExit(0)"]


class _OneRouteHandler(BaseHTTPRequestHandler):
    """A server that publishes exactly one route, like GridVibe's own.

    The point is the 404: a probe aimed at a route the server does not have
    is indistinguishable, from the outside, from a server that is not there.
    """

    route = "/api/health"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own spelling
        if self.path != self.route:
            self.send_error(404)
            return
        body = b'{"status": "ok", "window_mode": "browser"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve_one_route(case, route):
    """Run ``_OneRouteHandler`` on a free loopback port; return its base URL."""
    handler = type("_Handler", (_OneRouteHandler,), {"route": route})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Reverse order, so the loop is asked to stop and joined *before* its
    # socket is closed under it.
    case.addCleanup(server.server_close)
    case.addCleanup(thread.join, 5)
    case.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_address[1]}"


class StatusChecksTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / ".gridvibe_mcp.json"

    def _write(self, document):
        self.config_path.write_text(json.dumps(document), encoding="utf-8")
        return patch.object(mcp_status, "mcp_config_path", lambda: str(self.config_path))

    def test_the_real_sidecar_answers_its_own_handshake(self):
        """The whole chain, run as the agent CLI would run it."""
        state, detail = mcp_status.check_handshake(
            {
                "command": sys.executable,
                "args": [str(PROJECT_ROOT / "gridvibe_mcp" / "__main__.py")],
            }
        )

        self.assertEqual(state, mcp_status.OK, detail)
        self.assertIn("gridvibe", detail)

    def test_this_interpreter_reports_the_sdk_it_actually_has(self):
        state, detail = mcp_status.check_sdk(sys.executable)

        # The suite runs under the interpreter the sidecar tests need, so the
        # positive case here is a real spawn of a real import.
        self.assertEqual(state, mcp_status.OK, detail)
        self.assertTrue(detail.startswith("mcp "), detail)

    def test_a_missing_sdk_is_named_with_the_command_that_fixes_it(self):
        """The bug's own shape: the config names a venv without the SDK.

        Driven through `_run` because the negative case cannot be produced by
        spawning the interpreter running these tests -- it has the SDK. The
        strings below are what a real spawn of an SDK-less interpreter
        returned, not an invented shape.
        """
        spawn = (1, "", "ModuleNotFoundError: No module named 'mcp'\n")
        with patch.object(mcp_status, "_run", lambda *a, **k: spawn):
            state, detail = mcp_status.check_sdk("python")

        self.assertEqual(state, mcp_status.BROKEN)
        self.assertIn("make mcp-deps", detail)

    def test_a_2x_sdk_is_named_as_the_wrong_major_rather_than_missing(self):
        """An SDK that imports and still cannot serve.

        The 2.x SDK replaced the low-level decorators the sidecar registers
        through, so it imports cleanly and registers nothing -- which reaches a
        user as the same `CONNECTION_CLOSED` a missing SDK does.
        """
        with patch.object(mcp_status, "_run", lambda *a, **k: (0, "2.1.0 False\n", "")):
            state, detail = mcp_status.check_sdk("python")

        self.assertEqual(state, mcp_status.BROKEN)
        self.assertIn("2.1.0", detail)
        self.assertIn("1.x", detail)

    def test_an_interpreter_that_does_not_exist_is_reported_not_raised(self):
        state, detail = mcp_status.check_sdk(
            str(Path(self.temp_dir.name) / "no-such-python")
        )

        self.assertEqual(state, mcp_status.BROKEN)
        self.assertIn("could not run", detail)

    def test_a_child_that_never_answers_is_killed_rather_than_waited_on(self):
        # A real child that really never writes: the only way this returns is
        # the timeout path killing it. Shortened to keep the suite quick --
        # the bound being readable at call time is what makes that possible.
        with patch.object(mcp_status, "SPAWN_TIMEOUT", 1.0):
            state, detail = mcp_status.check_handshake(
                {
                    "command": sys.executable,
                    "args": ["-c", "import time; time.sleep(30)"],
                }
            )

        self.assertEqual(state, mcp_status.BROKEN)
        self.assertIn("no answer within", detail)

    def test_a_process_that_exits_without_speaking_is_the_reported_failure(self):
        """The exact shape of the bug: a sidecar that dies at startup."""
        state, detail = mcp_status.check_handshake(
            {"command": _NO_SDK[0], "args": _NO_SDK[1:]}
        )

        self.assertEqual(state, mcp_status.BROKEN)
        self.assertIn("no initialize answer", detail)

    def test_a_config_that_was_never_written_stops_the_walk_there(self):
        with patch.object(
            mcp_status, "mcp_config_path", lambda: str(self.config_path)
        ):
            findings = mcp_status.collect()

        # One cause, one finding -- not five consequences of it.
        self.assertEqual(len(findings), 1)
        name, state, detail = findings[0]
        self.assertEqual((name, state), ("generated config", mcp_status.BROKEN))
        self.assertIn("make mcp-config", detail)

    def test_an_interpreter_that_has_gone_stops_the_walk_there(self):
        gone = str(Path(self.temp_dir.name) / "removed-venv" / "python.exe")
        with self._write({"mcpServers": {"gridvibe": {"command": gone, "args": []}}}):
            findings = mcp_status.collect()

        self.assertEqual([state for _n, state, _d in findings][-1], mcp_status.BROKEN)
        self.assertEqual(findings[-1][0], "sidecar interpreter")

    def test_a_config_naming_no_gridvibe_server_is_refused(self):
        with self._write({"mcpServers": {"something-else": {"command": "python"}}}):
            findings = mcp_status.collect()

        self.assertIn("'gridvibe'", findings[0][2])

    def test_gridvibe_being_down_is_a_note_and_never_a_failure(self):
        # Port 1 on loopback: nothing is listening, and nothing will be.
        state, detail = mcp_status.check_reachable("http://127.0.0.1:1")

        self.assertEqual(state, mcp_status.NOTE)
        self.assertIn("nothing is listening", detail)

    def test_a_running_gridvibe_is_reported_as_answering(self):
        """The up case, and the one nothing pinned.

        A note never fails the run, so a probe aimed at a route GridVibe does
        not publish reported every healthy server as a dead one -- quietly,
        and in the one line of the diagnostic meant to say the server is fine.
        """
        url = _serve_one_route(self, mcp_status.PROBE_PATH)

        state, detail = mcp_status.check_reachable(url)

        self.assertEqual(state, mcp_status.OK)
        self.assertIn(url, detail)

    def test_a_route_gridvibe_does_not_publish_reads_as_a_dead_server(self):
        """Why the probe has to name a real route: a 404 is an ``HTTPError``,
        which is a ``URLError``, which is the same branch as no server."""
        url = _serve_one_route(self, "/api/a-route-that-does-not-exist")

        state, detail = mcp_status.check_reachable(url)

        self.assertEqual(state, mcp_status.NOTE)
        self.assertIn("nothing is listening", detail)

    def test_the_probed_route_is_one_gridvibe_actually_publishes(self):
        """The assertion the fix needs: the probe's path is in the real app's
        URL map, so renaming the route breaks this rather than the report."""
        from web import api

        published = {rule.rule for rule in api.app.url_map.iter_rules()}

        self.assertIn(mcp_status.PROBE_PATH, published)

    def test_the_url_probed_is_the_one_baked_into_the_args(self):
        self.assertEqual(
            mcp_status.configured_url({"args": ["entry.py", "--url", "http://x:9"]}),
            "http://x:9",
        )
        self.assertEqual(mcp_status.configured_url({"args": ["entry.py"]}), "")
        self.assertEqual(mcp_status.configured_url({}), "")


class StatusExitCodeTestCase(unittest.TestCase):
    """What CI and `make mcp-deps` read."""

    def test_a_broken_chain_exits_one_and_names_each_broken_link(self):
        findings = [
            ("generated config", mcp_status.OK, "path"),
            ("mcp SDK", mcp_status.BROKEN, "not installed"),
            ("GridVibe", mcp_status.NOTE, "down"),
        ]
        with patch.object(mcp_status, "collect", lambda: findings), redirect_stdout(
            StringIO()
        ) as report:
            self.assertEqual(mcp_status.main([]), 1)
            self.assertEqual(mcp_status.main(["--json"]), 1)

        # The broken link is named, not just counted.
        self.assertIn("mcp SDK", report.getvalue())

    def test_a_chain_whose_only_blemish_is_a_note_exits_zero(self):
        findings = [
            ("generated config", mcp_status.OK, "path"),
            ("GridVibe", mcp_status.NOTE, "not running"),
        ]
        with patch.object(mcp_status, "collect", lambda: findings), redirect_stdout(
            StringIO()
        ):
            self.assertEqual(mcp_status.main([]), 0)


if __name__ == "__main__":
    unittest.main()
