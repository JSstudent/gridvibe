"""A remote pane's MCP token never reaches GridVibe's log.

``POST /mcp/<token>`` carries the credential in its path. These tests send the
records that can name that path -- werkzeug's access and error lines (from a
real server too, with every separator that reaches the route), an error record
with a traceback, a child logger's record, a record through a handler a library
attached to its own logger, and the cross-origin guard's warning -- through the
handlers ``main.setup_logging`` installs, and read back what the console and
``logs/gridvibe.log`` actually received.
"""

import http.client
import io
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.serving import make_server

import main
from web import api
from web import config as web_config
from web import saved_sessions as web_saved_sessions
from web.log_redaction import (
    REDACTED_MCP_PATH,
    RedactMcpTokenFilter,
    install_mcp_token_redaction,
    redact_mcp_path,
)

# The shape `secrets.token_urlsafe(32)` mints.
TOKEN = "Zk3q_9vX-2mB7rT4wLp0aN8cYh1sQeU6jD5fGiKoRtM"


class RedactMcpPathTestCase(unittest.TestCase):
    def test_the_token_segment_is_redacted_wherever_it_appears(self):
        cases = {
            f"/mcp/{TOKEN}": REDACTED_MCP_PATH,
            f"POST /mcp/{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
            f'"POST /mcp/{TOKEN}?x=1 HTTP/1.1" 200 -': f'"POST {REDACTED_MCP_PATH}?x=1 HTTP/1.1" 200 -',
            f"http://127.0.0.1:43123/mcp/{TOKEN}": f"http://127.0.0.1:43123{REDACTED_MCP_PATH}",
            f"('GET /mcp/{TOKEN}')": f"('GET {REDACTED_MCP_PATH}')",
            f"/mcp/{TOKEN}/extra": f"{REDACTED_MCP_PATH}/extra",
            # Separators that still reach (or redirect to) the route.
            f"POST /mcp%2F{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
            f"POST /mcp%2f{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
            f"POST /mcp%252F{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
            f"POST /mcp//{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
            f"POST /mcp/%2F{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
            f"POST /MCP/{TOKEN} HTTP/1.1": f"POST {REDACTED_MCP_PATH} HTTP/1.1",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(redact_mcp_path(text), expected)
                self.assertNotIn(TOKEN, redact_mcp_path(text))

    def test_other_paths_pass_through_unchanged(self):
        for text in (
            "POST /api/mcp/close/pane/abc123 HTTP/1.1",
            "GET /api/sessions HTTP/1.1",
            "the route is POST /mcp/<token>",
            REDACTED_MCP_PATH,
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual(redact_mcp_path(text), text)

    def test_none_reads_as_empty(self):
        self.assertEqual(redact_mcp_path(None), "")


def _record(msg, args=None, *, level=logging.INFO, name="werkzeug", exc_info=None):
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )


class RedactMcpTokenFilterTestCase(unittest.TestCase):
    def test_a_record_without_a_token_is_left_as_it_came(self):
        args = ("127.0.0.1", "GET /api/sessions HTTP/1.1", "200")
        record = _record('%s - - "%s" %s', args)

        self.assertTrue(RedactMcpTokenFilter().filter(record))

        self.assertEqual(record.msg, '%s - - "%s" %s')
        self.assertEqual(record.args, args)

    def test_a_malformed_record_is_passed_on_without_raising(self):
        record = _record("%s and %s", ("only one",))

        self.assertTrue(RedactMcpTokenFilter().filter(record))

    def test_install_puts_the_filter_first_once(self):
        handler = logging.StreamHandler(io.StringIO())
        other = logging.Filter()
        handler.addFilter(other)

        install_mcp_token_redaction([handler])
        install_mcp_token_redaction([handler])

        self.assertIsInstance(handler.filters[0], RedactMcpTokenFilter)
        self.assertEqual(
            sum(isinstance(f, RedactMcpTokenFilter) for f in handler.filters), 1
        )
        self.assertIs(handler.filters[1], other)


class _ConfiguredHandlers:
    """Install the real `setup_logging` handlers, console and file, for a test."""

    def install_handlers(self):
        root = logging.getLogger()
        original_handlers = list(root.handlers)
        original_level = root.level
        previous_disable = logging.root.manager.disable
        werkzeug_logger = logging.getLogger("werkzeug")
        werkzeug_filters = list(werkzeug_logger.filters)
        paramiko_level = logging.getLogger("paramiko").level
        # setup_logging also filters handlers other loggers already carry.
        other_filters = {
            handler: list(handler.filters)
            for logger in list(logging.root.manager.loggerDict.values())
            if isinstance(logger, logging.Logger)
            for handler in logger.handlers
        }

        temp_dir = tempfile.TemporaryDirectory()
        self.log_file = Path(temp_dir.name) / "gridvibe.log"
        self.console = io.StringIO()

        def restore():
            for handler in root.handlers:
                handler.close()
            root.handlers.clear()
            for handler in original_handlers:
                root.addHandler(handler)
            root.setLevel(original_level)
            werkzeug_logger.filters[:] = werkzeug_filters
            for handler, filters in other_filters.items():
                handler.filters[:] = filters
            logging.getLogger("paramiko").setLevel(paramiko_level)
            logging.disable(previous_disable)
            temp_dir.cleanup()

        self.addCleanup(restore)
        logging.disable(logging.NOTSET)
        with patch.object(main, "LOG_DIR", temp_dir.name), patch.object(
            main.sys, "stdout", self.console
        ):
            main.setup_logging(debug=False)

    def written(self):
        for handler in logging.getLogger().handlers:
            handler.flush()
        return self.console.getvalue(), self.log_file.read_text(encoding="utf-8")

    def assert_redacted(self, *needles):
        for output in self.written():
            self.assertNotIn(TOKEN, output)
            for needle in needles:
                self.assertIn(needle, output)


def _use_temp_config(test):
    """The API's config and saved sessions in a throwaway directory."""
    temp_dir = tempfile.TemporaryDirectory()
    test.addCleanup(temp_dir.cleanup)
    for target, name in (
        (web_config, "CONFIG_PATH"),
        (web_saved_sessions, "SAVED_SESSIONS_PATH"),
    ):
        patcher = patch.object(
            target, name, str(Path(temp_dir.name) / f"{name.lower()}.json")
        )
        patcher.start()
        test.addCleanup(patcher.stop)
    api._refresh_runtime_config()
    test.addCleanup(api._refresh_runtime_config)
    api.app.config["TESTING"] = True
    test.client = api.app.test_client()


class ConfiguredHandlersTestCase(_ConfiguredHandlers, unittest.TestCase):
    """Records go through the real `setup_logging` handlers, console and file."""

    def setUp(self):
        self.install_handlers()

    def test_the_filter_is_first_on_both_handlers(self):
        handlers = logging.getLogger().handlers
        self.assertEqual(len(handlers), 2)
        for handler in handlers:
            self.assertIsInstance(handler.filters[0], RedactMcpTokenFilter)

    def test_a_werkzeug_access_record_is_redacted(self):
        # werkzeug's `log_request` shape: a prefix, then '"%s" %s %s'.
        logging.getLogger("werkzeug").info(
            '127.0.0.1 - - [30/Sep/2026 17:00:00] "%s" %s %s',
            f"POST /mcp/{TOKEN} HTTP/1.1",
            "200",
            "-",
        )

        self.assert_redacted(f'"POST {REDACTED_MCP_PATH} HTTP/1.1" 200 -')

    def test_a_coloured_werkzeug_record_is_redacted_on_both_handlers(self):
        logging.getLogger("werkzeug").info(
            '127.0.0.1 - - [30/Sep/2026 17:00:00] "%s" %s %s',
            f"\x1b[33mPOST /mcp/{TOKEN} HTTP/1.1\x1b[0m",
            "404",
            "-",
        )

        console, log_file = self.written()
        self.assertNotIn(TOKEN, console)
        self.assertNotIn(TOKEN, log_file)
        # The console keeps werkzeug's colours; the file strips them.
        self.assertIn("\x1b[33m", console)
        self.assertNotIn("\x1b[", log_file)
        self.assertIn(REDACTED_MCP_PATH, log_file)

    def test_a_werkzeug_bad_request_line_is_redacted(self):
        logging.getLogger("werkzeug").error(
            "127.0.0.1 - - [30/Sep/2026 17:00:00] code 400, message Bad request syntax (%r)",
            f"POST /mcp/{TOKEN}",
        )

        self.assert_redacted(f"('POST {REDACTED_MCP_PATH}')")

    def test_an_error_record_and_its_traceback_are_redacted(self):
        # Flask's `log_exception` shape, from a child logger, with the URL also
        # inside the exception text the formatter appends.
        try:
            raise RuntimeError(f"upstream refused http://127.0.0.1:5050/mcp/{TOKEN}")
        except RuntimeError:
            logging.getLogger("web.app.child").error(
                f"Exception on /mcp/{TOKEN} [POST]", exc_info=True
            )

        self.assert_redacted(
            f"Exception on {REDACTED_MCP_PATH} [POST]",
            f"upstream refused http://127.0.0.1:5050{REDACTED_MCP_PATH}",
            "Traceback",
        )

    def test_a_non_mcp_record_passes_through_unchanged(self):
        logging.getLogger("werkzeug").info(
            '127.0.0.1 - - [30/Sep/2026 17:00:00] "%s" %s %s',
            "POST /api/mcp/close/pane/abc123 HTTP/1.1",
            "200",
            "-",
        )

        for output in self.written():
            self.assertIn('"POST /api/mcp/close/pane/abc123 HTTP/1.1" 200 -', output)

    def test_poll_suppression_still_holds(self):
        logging.getLogger("werkzeug").info(
            '127.0.0.1 - - [30/Sep/2026 17:00:00] "%s" %s %s',
            "GET /api/sessions HTTP/1.1",
            "200",
            "-",
        )

        for output in self.written():
            self.assertNotIn("/api/sessions", output)


class OriginGuardTestCase(_ConfiguredHandlers, unittest.TestCase):
    """The cross-origin guard names a redacted path, through the test client."""

    def setUp(self):
        _use_temp_config(self)

    def test_the_call_site_logs_the_redacted_path(self):
        # Captured on the logger itself, before any handler filter runs.
        with self.assertLogs("web.app", level="WARNING") as captured:
            response = self.client.post(
                f"/mcp/{TOKEN}",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Origin": "http://evil.example"},
            )

        self.assertEqual(response.status_code, 403)
        output = "\n".join(captured.output)
        self.assertIn(f"Rejected cross-origin POST {REDACTED_MCP_PATH}", output)
        self.assertNotIn(TOKEN, output)

    def test_the_rejection_warning_reaches_the_log_redacted(self):
        self.install_handlers()
        response = self.client.post(
            f"/mcp/{TOKEN}",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Origin": "http://evil.example"},
        )

        self.assertEqual(response.status_code, 403)
        self.assert_redacted(f"Rejected cross-origin POST {REDACTED_MCP_PATH}")


class RealAccessLogTestCase(_ConfiguredHandlers, unittest.TestCase):
    """Requests to a real Werkzeug server on loopback; its own access lines."""

    def setUp(self):
        _use_temp_config(self)
        self.install_handlers()
        self.server = make_server("127.0.0.1", 0, api.app, threaded=True)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)

    def post(self, path):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=10
        )
        try:
            connection.request(
                "POST",
                path,
                body=b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    def test_every_request_line_that_reaches_the_route_is_redacted(self):
        # The plain route and an encoded separator both reach the MCP handler
        # (an unknown token answers 404 there); a doubled slash redirects to it.
        statuses = {
            path: self.post(path)
            for path in (f"/mcp/{TOKEN}", f"/mcp%2F{TOKEN}", f"/mcp//{TOKEN}")
        }

        self.assertEqual(statuses[f"/mcp/{TOKEN}"], 404)
        self.assertEqual(statuses[f"/mcp%2F{TOKEN}"], 404)
        self.assertEqual(statuses[f"/mcp//{TOKEN}"], 308)
        for output in self.written():
            self.assertNotIn(TOKEN, output)
            self.assertEqual(output.count(f"POST {REDACTED_MCP_PATH} HTTP/1.1"), 3)


class LibraryHandlerTestCase(_ConfiguredHandlers, unittest.TestCase):
    """A handler a library attached to its own logger before setup_logging."""

    def test_a_handler_attached_before_setup_is_filtered_first(self):
        stream = io.StringIO()
        library_logger = logging.getLogger("tests.log_redaction.library")
        handler = logging.StreamHandler(stream)
        library_logger.addHandler(handler)
        self.addCleanup(library_logger.removeHandler, handler)

        self.install_handlers()
        library_logger.error("Invalid session /mcp/%s", TOKEN)

        self.assertIsInstance(handler.filters[0], RedactMcpTokenFilter)
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertIn(f"Invalid session {REDACTED_MCP_PATH}", stream.getvalue())
        self.assert_redacted(f"Invalid session {REDACTED_MCP_PATH}")

    def test_engineio_and_socketio_stderr_handlers_are_filtered(self):
        # Both libraries attach a stderr handler to their server logger when
        # the app's SocketIO is built, which importing `web.api` does.
        self.install_handlers()
        for name in ("engineio.server", "socketio.server"):
            with self.subTest(logger=name):
                handlers = logging.getLogger(name).handlers
                self.assertTrue(handlers)
                for handler in handlers:
                    self.assertIsInstance(handler.filters[0], RedactMcpTokenFilter)

        handler = logging.getLogger("engineio.server").handlers[0]
        stream = io.StringIO()
        previous = handler.setStream(stream)
        self.addCleanup(handler.setStream, previous)
        logging.getLogger("engineio.server").error(
            "Invalid session %s (further occurrences of this error will be "
            "logged with level INFO)",
            f"/mcp/{TOKEN}",
        )

        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertIn(f"Invalid session {REDACTED_MCP_PATH}", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
