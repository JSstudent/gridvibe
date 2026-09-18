"""Tools for a pane whose shell is on another machine.

The stdio sidecar cannot exist on a remote host -- no copy of it, no MCP SDK,
no route to this machine's loopback. Three pieces remove all three
requirements, and each has a way of failing quietly:

- **The protocol moves to where GridVibe already is.** ``web/mcp_http.py``
  answers MCP over streamable HTTP using the sidecar's *own* synchronous
  ``dispatch``, so a tool cannot behave differently depending on which
  transport asked for it.
- **Identity arrives by token, because inheritance cannot cross a machine.**
  A local sidecar reads five inherited variables; a remote agent inherits
  nothing, so a per-pane token resolves to the same ``PaneIdentity``. A token
  that outlived its pane would be a standing key to this machine's tools, so
  the pane closing revokes it.
- **The route home is the SSH transport that is already open.** No port is
  opened to anything but the remote host's own loopback, and only for a pane
  that asked.

A failure anywhere in that chain costs the pane its tools and nothing else --
never its agent, and never its shell.
"""

import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import agents as web_agents  # noqa: E402
from web import mcp_http, ssh_tunnel  # noqa: E402
from web.window_intents import OPENED, SPLIT, window_intents  # noqa: E402


class _StandInGridVibe:
    """GridVibe's own port, as the filter's local half reaches it.

    Records what actually arrives, which is where the assertions live: a
    request recorded here is one that got past the filter. The drain after the
    reply is how a pipelined second request would show up.
    """

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(8)
        self.port = self._listener.getsockname()[1]
        self.requests = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _address = self._listener.accept()
            except OSError:
                return
            threading.Thread(
                target=self._handle, args=(connection,), daemon=True
            ).start()

    def _handle(self, connection) -> None:
        with connection:
            buffer = bytearray()
            while b"\r\n\r\n" not in buffer:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                buffer.extend(chunk)
            head, _marker, rest = bytes(buffer).partition(b"\r\n\r\n")
            length = 0
            for line in head.decode("latin-1").split("\r\n")[1:]:
                name, _colon, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip() or 0)
            body = bytearray(rest)
            while len(body) < length:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                body.extend(chunk)
            payload = b'{"ok": true}'
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + payload
            )
            trailing = bytearray()
            connection.settimeout(0.2)
            try:
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    trailing.extend(chunk)
            except OSError:
                pass
            self.requests.append(
                (head.decode("latin-1"), bytes(body), bytes(trailing))
            )

    def close(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


class _FakeChannel:
    """One forwarded channel, holding what the remote host sent down it."""

    def __init__(self, request: bytes = b"", *, endless: bytes = b""):
        self._pending = bytearray(request)
        self._endless = endless
        self.sent = bytearray()
        self.closed = False
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        if self._pending:
            chunk = bytes(self._pending[:size])
            del self._pending[:size]
            return chunk
        # `endless` stands in for a caller that never finishes its head.
        return self._endless[:size]

    def sendall(self, data):
        if self.closed:
            raise OSError("channel closed")
        self.sent.extend(data)

    def close(self):
        self.closed = True

    @property
    def reply(self) -> str:
        return bytes(self.sent).decode("latin-1")

    @property
    def status(self) -> int:
        parts = self.reply.split("\r\n", 1)[0].split(" ")
        return int(parts[1]) if len(parts) > 2 and parts[1].isdigit() else 0


class PaneTokenRegistryTestCase(unittest.TestCase):
    """The token is the only thing standing between a remote host and a pane."""

    def setUp(self):
        self.registry = mcp_http.PaneTokenRegistry()

    def test_a_token_resolves_to_the_pane_that_minted_it(self):
        token = self.registry.mint(
            session_id="pane-1", group_id="g1", workspace_id="ws1", agent_depth=2
        )

        record = self.registry.resolve(token)
        self.assertEqual(record["session_id"], "pane-1")
        self.assertEqual(record["group_id"], "g1")
        self.assertEqual(record["workspace_id"], "ws1")
        self.assertEqual(record["agent_depth"], 2)

    def test_minting_twice_for_one_pane_keeps_the_token_already_written(self):
        """The remote config names it, so a second mint must not orphan it."""
        first = self.registry.mint(session_id="pane-1")
        second = self.registry.mint(session_id="pane-1", group_id="g2")

        self.assertEqual(first, second)
        self.assertEqual(self.registry.resolve(first)["group_id"], "g2")

    def test_two_panes_never_share_a_token(self):
        self.assertNotEqual(
            self.registry.mint(session_id="pane-1"),
            self.registry.mint(session_id="pane-2"),
        )

    def test_closing_a_pane_revokes_its_token(self):
        token = self.registry.mint(session_id="pane-1")

        self.assertTrue(self.registry.revoke("pane-1"))
        self.assertEqual(self.registry.resolve(token), {})
        # And revoking again is not an error, because teardown runs twice.
        self.assertFalse(self.registry.revoke("pane-1"))

    def test_the_registry_is_bounded_so_a_missed_revoke_cannot_grow_it(self):
        """The close path is what revokes; this is what a missed close costs.

        A ceiling turns a revoke that never ran into a bounded leak rather
        than a permanent one -- and drops the oldest rather than refusing the
        newest, because a live pane must always be able to mint.
        """
        registry = mcp_http.PaneTokenRegistry(max_tokens=3)

        tokens = [registry.mint(session_id=f"pane-{index}") for index in range(4)]

        self.assertEqual(registry.resolve(tokens[0]), {})
        self.assertTrue(all(registry.resolve(token) for token in tokens[1:]))
        # The evicted pane is forgotten both ways, so it mints afresh rather
        # than being answered with a token the registry no longer holds.
        self.assertNotIn(registry.mint(session_id="pane-0"), tokens)

    def test_there_is_one_lookup_and_minting_again_is_it(self):
        """A second, read-only accessor existed and nothing called it.

        `mint` is already idempotent per pane, which is the need a `token_for`
        would have served -- and a live credential wants one way in, not two
        that have to agree.
        """
        token = self.registry.mint(session_id="pane-1")

        self.assertEqual(self.registry.mint(session_id="pane-1"), token)
        self.assertEqual(
            sorted(
                name
                for name in dir(self.registry)
                if not name.startswith("_")
                and callable(getattr(self.registry, name))
            ),
            ["clear", "mint", "resolve", "revoke"],
        )

    def test_a_pane_with_no_id_mints_nothing(self):
        self.assertEqual(self.registry.mint(session_id=""), "")

    def test_an_unknown_token_resolves_to_nothing(self):
        self.assertEqual(self.registry.resolve("made-up"), {})
        self.assertEqual(self.registry.resolve(""), {})


class HttpEndpointTestCase(unittest.TestCase):
    """The protocol half, driven through the real route."""

    def setUp(self):
        from web import api

        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        mcp_http.pane_tokens.clear()
        self.addCleanup(mcp_http.pane_tokens.clear)
        self.token = mcp_http.pane_tokens.mint(
            session_id="pane-1", group_id="g1", workspace_id="ws1"
        )

    def post(self, body, token=None):
        response = self.client.post(
            f"/mcp/{token or self.token}",
            data=json.dumps(body),
            content_type="application/json",
        )
        return response.status_code, (response.get_json() if response.data else None)

    def test_initialize_answers_with_a_negotiated_version(self):
        status, body = self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
            }
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(body["result"]["serverInfo"]["name"], "gridvibe")
        self.assertIn("tools", body["result"]["capabilities"])

    def test_an_unknown_protocol_version_is_answered_not_refused(self):
        # A CLI that merely updated must not lose its tools.
        _status, body = self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        )

        self.assertEqual(
            body["result"]["protocolVersion"], mcp_http.DEFAULT_PROTOCOL_VERSION
        )

    def test_a_notification_is_accepted_with_no_body(self):
        status, body = self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        self.assertEqual(status, 202)
        self.assertIsNone(body)

    def test_tools_list_is_the_sidecars_own_surface(self):
        from gridvibe_mcp.server import tool_names

        _status, body = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        self.assertEqual(
            [tool["name"] for tool in body["result"]["tools"]], tool_names()
        )

    def test_an_unknown_method_is_a_json_rpc_error_not_a_500(self):
        _status, body = self.post({"jsonrpc": "2.0", "id": 3, "method": "nope"})

        self.assertEqual(body["error"]["code"], -32601)

    def test_an_unknown_token_is_refused(self):
        status, body = self.post(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, token="not-real"
        )

        self.assertEqual(status, 404)
        self.assertIn("token", body["error"].lower())

    def test_a_revoked_token_is_refused_exactly_like_an_unknown_one(self):
        """A closed pane's token must not go on naming this machine's tools."""
        mcp_http.pane_tokens.revoke("pane-1")

        status, body = self.post({"jsonrpc": "2.0", "id": 5, "method": "tools/list"})

        self.assertEqual(status, 404)
        # Same sentence either way: the caller learns nothing about which
        # tokens exist.
        self.assertIn("Unknown or expired", body["error"])

    def test_a_tool_call_runs_as_the_pane_the_token_names(self):
        captured = {}

        def fake_dispatch(name, arguments, *, client, identity, max_agent_depth):
            captured.update(name=name, identity=identity)
            return {"ok": True}

        with patch("gridvibe_mcp.server.dispatch", fake_dispatch):
            _status, body = self.post(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}},
                }
            )

        self.assertEqual(captured["name"], "whoami")
        self.assertEqual(captured["identity"].session_id, "pane-1")
        self.assertEqual(captured["identity"].workspace_id, "ws1")
        # A tunnelled pane's depth came from its record, not from nowhere, so
        # the budget applies exactly as it does to a local pane.
        self.assertTrue(captured["identity"].depth_stated)
        self.assertFalse(body["result"]["isError"])

    def test_a_refused_tool_is_content_not_a_protocol_error(self):
        """The agent should read GridVibe's own sentence, as it does on stdio."""
        with patch(
            "gridvibe_mcp.server.dispatch",
            lambda *a, **k: {"error": "Nope.", "kind": "refused"},
        ):
            _status, body = self.post(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "launch_panes", "arguments": {}},
                }
            )

        self.assertNotIn("error", body)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("Nope.", body["result"]["content"][0]["text"])

    def test_the_stream_half_of_the_transport_answers_a_stated_405(self):
        """This endpoint is the POST half of streamable HTTP, deliberately.

        The transport also describes a `GET` SSE stream and a `DELETE` that
        ends a session; GridVibe sends nothing a client has to stream, and a
        pane's session is the pane. Flask's bare method-not-allowed reads like a
        server that half-implements the spec by accident, so the route answers
        and says which half it is.
        """
        for method in ("get", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(f"/mcp/{self.token}")

                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "POST")
                self.assertIn("POST half", response.get_json()["error"])

    def test_the_405_tells_a_caller_nothing_about_the_token(self):
        """Answered before the registry is consulted, so it leaks no liveness."""
        live = self.client.get(f"/mcp/{self.token}")
        unknown = self.client.get("/mcp/made-up")

        self.assertEqual(live.status_code, unknown.status_code)
        self.assertEqual(live.get_json(), unknown.get_json())

    def test_a_malformed_body_is_a_parse_error(self):
        response = self.client.post(
            f"/mcp/{self.token}", data="{not json", content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], -32700)


class RemoteConfigTestCase(unittest.TestCase):
    """What gets written on the remote host, and where."""

    def test_the_remote_config_names_a_url_and_no_command(self):
        document = ssh_tunnel.remote_mcp_document("http://127.0.0.1:4111/mcp/TOK")
        server = document["mcpServers"]["gridvibe"]

        self.assertEqual(server["url"], "http://127.0.0.1:4111/mcp/TOK")
        # Nothing is installed over there, so there is no command to name.
        self.assertNotIn("command", server)
        self.assertNotIn("args", server)
        # The CLIs disagree about which key they read; an extra one is ignored.
        self.assertEqual(server["type"], "http")
        self.assertEqual(server["transport"], "http")

    def test_the_url_is_the_remote_hosts_own_loopback(self):
        url = ssh_tunnel.tunnel_url(4111, "TOK")

        self.assertTrue(url.startswith("http://127.0.0.1:4111/"))
        self.assertTrue(url.endswith("/mcp/TOK"))

    def test_no_port_or_no_token_is_no_url(self):
        self.assertEqual(ssh_tunnel.tunnel_url(0, "TOK"), "")
        self.assertEqual(ssh_tunnel.tunnel_url(4111, ""), "")

    def test_each_pane_gets_its_own_remote_file(self):
        """One host can hold panes from several GridVibe windows at once."""
        first = ssh_tunnel.remote_config_path("/home/ubuntu", "aaa")
        second = ssh_tunnel.remote_config_path("/home/ubuntu", "bbb")

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("/home/ubuntu/.gridvibe/"))

    def test_a_session_id_cannot_walk_out_of_its_directory(self):
        path = ssh_tunnel.remote_config_path("/home/ubuntu", "../../etc/passwd")

        self.assertTrue(path.startswith("/home/ubuntu/.gridvibe/"))
        self.assertNotIn("..", path)

    PATH = "/home/u/.gridvibe/mcp-a.json"

    @staticmethod
    def _sftp(mode=0o100600):
        """An SFTP channel that accepts the write and reports ``mode`` back."""
        sftp = MagicMock()
        handle = MagicMock()
        sftp.open.return_value.__enter__ = MagicMock(return_value=handle)
        sftp.open.return_value.__exit__ = MagicMock(return_value=False)
        sftp.stat.return_value = SimpleNamespace(st_mode=mode)
        sftp.handle = handle
        return sftp

    def test_the_written_file_is_readable_only_by_its_owner(self):
        sftp = self._sftp()

        written = ssh_tunnel.write_remote_config(
            sftp, self.PATH, "http://127.0.0.1:1/mcp/T"
        )

        self.assertTrue(written)
        # The token is a credential for this pane's tools.
        sftp.chmod.assert_called_once_with(self.PATH, 0o600)
        # And the mode is read back rather than assumed: a `chmod` that
        # answered without applying anything is the case this catches.
        sftp.stat.assert_called_with(self.PATH)
        self.assertIn("mcpServers", sftp.handle.write.call_args.args[0])

    def test_a_write_that_fails_reports_rather_than_raises(self):
        sftp = MagicMock()
        sftp.open.side_effect = OSError("read-only file system")

        self.assertFalse(
            ssh_tunnel.write_remote_config(sftp, "/x/y.json", "http://h/mcp/T")
        )

    def test_permissions_that_cannot_be_applied_take_the_file_with_them(self):
        """The whole point of failing closed.

        The document names this pane's bearer token, and the token is the whole
        of the endpoint's authentication. A host that declines `chmod` -- a
        share mounted from Windows, an exotic SFTP server -- used to leave the
        file lying there at whatever mode the umask chose, with the caller
        told it had succeeded.
        """
        sftp = self._sftp()
        sftp.chmod.side_effect = OSError("operation not supported")

        written = ssh_tunnel.write_remote_config(
            sftp, self.PATH, "http://127.0.0.1:1/mcp/T"
        )

        self.assertFalse(written)
        sftp.remove.assert_called_once_with(self.PATH)

    def test_a_mode_the_host_will_not_confirm_is_a_refusal(self):
        """Unverifiable is refused exactly like unapplied: neither knows who
        can read the file, and the file is a credential."""
        sftp = self._sftp()
        sftp.stat.side_effect = OSError("permission denied")

        self.assertFalse(
            ssh_tunnel.write_remote_config(sftp, self.PATH, "http://h/mcp/T")
        )
        sftp.remove.assert_called_once_with(self.PATH)

    def test_a_file_other_accounts_can_still_read_is_taken_back(self):
        # `chmod` answered, and the mode that came back is group- and
        # world-readable anyway.
        sftp = self._sftp(mode=0o100644)

        self.assertFalse(
            ssh_tunnel.write_remote_config(sftp, self.PATH, "http://h/mcp/T")
        )
        sftp.remove.assert_called_once_with(self.PATH)

    def test_an_existing_directory_is_narrowed_before_anything_is_written(self):
        """`~/.gridvibe` outlives the pane, so finding it is not trusting it.

        A directory the rest of the host can read lists every pane's config
        file, whatever mode the files themselves carry.
        """
        sftp = MagicMock()
        sftp.stat.return_value = SimpleNamespace(st_mode=0o40700)

        self.assertTrue(ssh_tunnel.ensure_remote_directory(sftp, self.PATH))
        sftp.mkdir.assert_not_called()
        sftp.chmod.assert_called_once_with("/home/u/.gridvibe", 0o700)

    def test_a_directory_that_cannot_be_narrowed_stops_the_tunnel(self):
        sftp = MagicMock()
        sftp.stat.return_value = SimpleNamespace(st_mode=0o40777)

        self.assertFalse(ssh_tunnel.ensure_remote_directory(sftp, self.PATH))

    def test_a_created_directory_is_verified_like_an_existing_one(self):
        sftp = MagicMock()
        sftp.stat.side_effect = [OSError("no such file"), SimpleNamespace(st_mode=0o40700)]

        self.assertTrue(ssh_tunnel.ensure_remote_directory(sftp, self.PATH))
        sftp.mkdir.assert_called_once_with("/home/u/.gridvibe", 0o700)

    def test_a_config_that_could_not_be_secured_costs_the_whole_tunnel(self):
        """End to end: the listener is withdrawn, and nothing is recorded.

        The caller revokes the pane's token on a `None` record, so a file that
        could not be protected leaves no live token to have been written into
        it -- and the pane's shell and agent are untouched either way.
        """
        client = MagicMock()
        transport = client.get_transport.return_value
        transport.request_port_forward.return_value = 41234
        sftp = self._sftp()
        sftp.normalize.return_value = "/home/u"
        # The directory is fine; the file is the one that stays readable.
        sftp.stat.side_effect = lambda path: SimpleNamespace(
            st_mode=0o40700 if path.endswith(".gridvibe") else 0o100666
        )
        client.open_sftp.return_value = sftp

        record = ssh_tunnel.establish(
            client,
            session_id="pane-1",
            token="TOK",
            local_host="127.0.0.1",
            local_port=5050,
        )

        self.assertIsNone(record)
        transport.cancel_port_forward.assert_called_once_with("127.0.0.1", 41234)
        sftp.close.assert_called_once()


class ReverseTunnelTestCase(unittest.TestCase):
    """Opening and withdrawing the route home."""

    def test_the_remote_listener_binds_loopback_and_an_assigned_port(self):
        transport = MagicMock()
        transport.request_port_forward.return_value = 41234

        port = ssh_tunnel.open_reverse_tunnel(
            transport, local_host="127.0.0.1", local_port=5050, token="TOK"
        )

        self.assertEqual(port, 41234)
        args = transport.request_port_forward.call_args
        # Loopback on the *remote* host: the forwarded port is for that host's
        # own agent, never for its network.
        self.assertEqual(args.args[0], "127.0.0.1")
        # Port 0 -- sshd assigns one, so two panes never collide.
        self.assertEqual(args.args[1], 0)

    def test_a_refused_forward_costs_the_tools_and_nothing_else(self):
        transport = MagicMock()
        transport.request_port_forward.side_effect = Exception("administratively prohibited")

        self.assertEqual(
            ssh_tunnel.open_reverse_tunnel(
                transport, local_host="127.0.0.1", local_port=5050, token="TOK"
            ),
            0,
        )

    def test_a_listener_with_nothing_to_serve_is_never_opened(self):
        """The token *is* the endpoint: with none, the port answers nothing."""
        transport = MagicMock()

        self.assertEqual(
            ssh_tunnel.open_reverse_tunnel(
                transport, local_host="127.0.0.1", local_port=5050, token=""
            ),
            0,
        )
        transport.request_port_forward.assert_not_called()

    def test_establish_without_a_token_asks_the_remote_host_for_nothing(self):
        client = MagicMock()

        self.assertIsNone(
            ssh_tunnel.establish(
                client,
                session_id="pane-1",
                token="",
                local_host="127.0.0.1",
                local_port=5050,
            )
        )
        client.get_transport.assert_not_called()
        client.open_sftp.assert_not_called()

    def test_the_url_written_remotely_is_the_path_the_filter_accepts(self):
        """One spelling, so the config cannot name something the port refuses."""
        self.assertTrue(
            ssh_tunnel.tunnel_url(41234, "TOK").endswith(ssh_tunnel.mcp_path("TOK"))
        )
        self.assertEqual(ssh_tunnel.mcp_path(""), "")

    def test_the_handler_returns_at_once_instead_of_pumping_inline(self):
        """The regression that froze every remote pane.

        Paramiko calls this handler from `Transport._parse_channel_open` -- on
        the transport's own packet-reading thread, with no thread of its own.
        Pumping inline blocked that thread, and it is the same thread carrying
        the pane's *shell*: the terminal stopped accepting input, the forwarded
        bytes the tunnel existed for were never read, and `cancel_port_forward`
        waited on a reply that blocked thread could never deliver.
        """
        import threading as _threading

        released = _threading.Event()
        pumped = _threading.Event()

        def fake_serve(channel, host, port, expected_path=""):
            pumped.set()
            # Stand in for a long-lived connection: if the handler waited on
            # this, it would never return.
            released.wait(5)

        handler = ssh_tunnel._forward_handler("127.0.0.1", 5050, "/mcp/TOK")
        with patch.object(ssh_tunnel, "_serve_forwarded_channel", fake_serve):
            before = _threading.active_count()
            handler(MagicMock(), ("10.0.0.1", 5), ("127.0.0.1", 41234))
            # The whole point: control is back while the pump is still running.
            self.assertTrue(pumped.wait(5), "the channel was never served")
            self.assertGreater(_threading.active_count(), before)
            released.set()

    def test_a_flood_of_forwarded_connections_is_bounded(self):
        """Anything on the remote host can reach the pane's forwarded port.

        Each accepted connection costs a thread of *this* process for up to
        `REQUEST_READ_TIMEOUT`, whether or not it ever sends a request, so a
        loop of idle connections used to grow threads and memory here without
        tripping any per-request bound. The excess is closed at the door, and
        the budget is a budget rather than a fuse: the pane's next real tool
        call is served as soon as one of its own calls finishes.
        """
        import threading as _threading

        cap = ssh_tunnel.MAX_FORWARDED_CHANNELS
        released = _threading.Event()
        served = []
        lock = _threading.Lock()

        def fake_serve(channel, host, port, expected_path=""):
            with lock:
                served.append(channel)
            released.wait(5)

        def wait_until(count):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with lock:
                    if len(served) >= count:
                        return
                time.sleep(0.01)

        handler = ssh_tunnel._forward_handler("127.0.0.1", 5050, "/mcp/TOK")
        channels = [_FakeChannel() for _ in range(cap + 5)]
        with patch.object(ssh_tunnel, "_serve_forwarded_channel", fake_serve):
            try:
                for channel in channels:
                    handler(channel, ("10.0.0.1", 5), ("127.0.0.1", 41234))
                wait_until(cap)

                with lock:
                    self.assertEqual(len(served), cap)
                # The ones over the ceiling are closed at once rather than
                # queued: a queue is the same exhaustion one level down.
                self.assertTrue(all(channel.closed for channel in channels[cap:]))
                self.assertFalse(any(channel.closed for channel in channels[:cap]))
            finally:
                released.set()

            # The slot comes back with the worker that held it, so the retry
            # below is waiting on those threads and not on a timer.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and any(
                thread.name == "gridvibe-mcp-tunnel" and thread.is_alive()
                for thread in _threading.enumerate()
            ):
                time.sleep(0.01)

            later = _FakeChannel()
            handler(later, ("10.0.0.1", 5), ("127.0.0.1", 41234))
            wait_until(cap + 1)
            with lock:
                self.assertIn(later, served)

    def test_teardown_does_not_block_the_close_path(self):
        """Closing a workspace waits on this; a dead host must not stall it.

        `cancel_port_forward` waits for a reply from the remote, and the close
        path is where GridVibe is holding a workspace open. A remote that has
        gone used to mean the close never returned.
        """
        import threading as _threading

        entered = _threading.Event()
        release = _threading.Event()
        client = MagicMock()

        def never_returns(*_a, **_k):
            entered.set()
            release.wait(5)
            return MagicMock()

        client.get_transport.side_effect = never_returns
        try:
            ssh_tunnel.teardown(
                client, {"remote_port": 1, "remote_path": "", "sftp": None}
            )
            # Returned already, with the remote work still in flight.
            self.assertTrue(entered.wait(5), "teardown never started its work")
        finally:
            release.set()

    def test_teardown_withdraws_the_listener_and_deletes_the_file(self):
        client = MagicMock()
        transport = client.get_transport.return_value
        sftp = MagicMock()

        ssh_tunnel.teardown(
            client,
            {
                "remote_port": 41234,
                "remote_path": "/home/u/.gridvibe/mcp-a.json",
                "sftp": sftp,
            },
        )

        # It still happens -- just not on the caller's thread.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not transport.cancel_port_forward.called:
            time.sleep(0.01)
        transport.cancel_port_forward.assert_called_once_with("127.0.0.1", 41234)
        sftp.remove.assert_called_once_with("/home/u/.gridvibe/mcp-a.json")

    def test_teardown_tolerates_a_connection_already_gone(self):
        client = MagicMock()
        client.get_transport.side_effect = Exception("transport closed")

        # No raise: this runs on the close path, where the transport losing
        # itself first is ordinary.
        ssh_tunnel.teardown(client, {"remote_port": 1, "remote_path": "", "sftp": None})

    def test_teardown_of_nothing_is_a_no_op(self):
        ssh_tunnel.teardown(MagicMock(), None)


class ForwardedRequestFilterTestCase(unittest.TestCase):
    """What a forwarded connection may reach on this machine.

    sshd hands back a raw TCP channel, and the address on this end is
    GridVibe's *whole* loopback HTTP API: saved sessions whose SSH password
    decrypts on the way out, the destroy tier the tool surface deliberately
    does not contain, the ungated twins of every gated tool route, the window
    intents another pane is waiting on. Their only guard has ever been "you
    have to be on this machine", and a byte pump would have handed all of it
    to the remote host with the token guarding exactly one route on it.

    So the channel reaches a filter. These are the things it must not let
    through, and the thing it must.
    """

    TOKEN = "tok-abcdef"

    OTHER_ROUTES = (
        "GET /api/saved-sessions/abc HTTP/1.1",
        "GET /api/sessions HTTP/1.1",
        "DELETE /api/sessions/abc HTTP/1.1",
        "DELETE /api/workspaces/default HTTP/1.1",
        "POST /api/app-config HTTP/1.1",
        "POST /api/sessions/abc/shell HTTP/1.1",
        "POST /api/windows/intents/abc/claim HTTP/1.1",
        "GET /api/health HTTP/1.1",
    )

    def setUp(self):
        self.gridvibe = _StandInGridVibe()
        self.addCleanup(self.gridvibe.close)
        self.path = ssh_tunnel.mcp_path(self.TOKEN)

    def serve(self, request: bytes, **kwargs) -> _FakeChannel:
        channel = _FakeChannel(request, **kwargs)
        ssh_tunnel._serve_forwarded_channel(
            channel, "127.0.0.1", self.gridvibe.port, self.path
        )
        return channel

    def request(self, line: str, headers=(), body: bytes = b"") -> bytes:
        lines = [line, "Host: 127.0.0.1:41234", *headers]
        if body:
            lines.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body

    def test_the_panes_own_tool_call_reaches_gridvibe_and_is_answered(self):
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

        channel = self.serve(
            self.request(
                f"POST {self.path} HTTP/1.1",
                ["Content-Type: application/json", "Accept: application/json"],
                body,
            )
        )

        head, forwarded, _trailing = self.gridvibe.requests[0]
        self.assertTrue(head.startswith(f"POST {self.path} HTTP/1.1"))
        self.assertEqual(forwarded, body)
        # The caller's own headers travel; the filter rewrites only the two
        # that describe the hop.
        self.assertIn("Content-Type: application/json", head)
        self.assertIn("Accept: application/json", head)
        self.assertIn(f"Content-Length: {len(body)}", head)
        self.assertIn("Connection: close", head)
        self.assertIn('{"ok": true}', channel.reply)
        self.assertTrue(channel.closed)

    def test_every_other_route_on_the_port_is_refused_before_a_socket_opens(self):
        for line in self.OTHER_ROUTES:
            with self.subTest(request=line):
                channel = self.serve(self.request(line))

                self.assertEqual(channel.status, 404)
                # Not "reached GridVibe and was rejected" -- never reached it.
                self.assertEqual(self.gridvibe.requests, [])
                # And the refusal names no route and confirms no token, so a
                # caller on the remote host learns nothing by guessing.
                self.assertNotIn("/api", channel.reply)
                self.assertNotIn(self.TOKEN, channel.reply)

    def test_another_panes_token_is_refused_on_this_panes_port(self):
        """One forwarded port is one pane's endpoint, not the MCP endpoint."""
        channel = self.serve(
            self.request(f"POST {ssh_tunnel.mcp_path('tok-other')} HTTP/1.1")
        )

        self.assertEqual(channel.status, 404)
        self.assertEqual(self.gridvibe.requests, [])

    def test_the_right_path_with_another_method_is_refused_the_same_way(self):
        channel = self.serve(self.request(f"GET {self.path} HTTP/1.1"))

        self.assertEqual(channel.status, 404)
        self.assertEqual(self.gridvibe.requests, [])

    def test_a_path_that_is_not_even_text_is_refused_rather_than_raised(self):
        """Refusing is the answer to every malformed request, not an exception."""
        channel = _FakeChannel(
            b"POST /mcp/\xff\xfe HTTP/1.1\r\nHost: x\r\n\r\n"
        )
        ssh_tunnel._serve_forwarded_channel(
            channel, "127.0.0.1", self.gridvibe.port, self.path
        )

        self.assertEqual(channel.status, 404)
        self.assertEqual(self.gridvibe.requests, [])

    def test_a_refusal_is_logged_without_writing_the_token_into_the_log(self):
        """``/mcp/<token>`` is a credential; a wrong verb must not spend it."""
        with self.assertLogs("web.ssh_tunnel", level="WARNING") as logged:
            self.serve(self.request(f"GET {self.path} HTTP/1.1"))

        message = "\n".join(logged.output)
        self.assertIn("GET", message)
        self.assertIn("/mcp/...", message)
        self.assertNotIn(self.TOKEN, message)

    def test_a_request_pipelined_behind_a_valid_one_never_reaches_gridvibe(self):
        """The second half of the one-request rule.

        A caller that can make one legitimate tool call must not be able to
        append a destroy-tier request to it and have the filter forward both.
        """
        body = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
        smuggled = b"DELETE /api/sessions HTTP/1.1\r\nHost: x\r\n\r\n"

        self.serve(
            self.request(f"POST {self.path} HTTP/1.1", body=body) + smuggled
        )

        self.assertEqual(len(self.gridvibe.requests), 1)
        head, forwarded, trailing = self.gridvibe.requests[0]
        self.assertEqual(forwarded, body)
        # Nothing followed the request down the socket -- the bytes were read
        # into a buffer nobody forwards, not held for a second exchange.
        self.assertEqual(trailing, b"")
        self.assertNotIn("DELETE", head)

    def test_a_chunked_body_is_refused_rather_than_streamed(self):
        """A filter that cannot say where the body ends cannot say what follows."""
        channel = self.serve(
            self.request(
                f"POST {self.path} HTTP/1.1",
                ["Transfer-Encoding: chunked"],
            )
            + b"4\r\nping\r\n0\r\n\r\n"
        )

        self.assertEqual(channel.status, 400)
        self.assertEqual(self.gridvibe.requests, [])

    def test_two_content_lengths_are_refused(self):
        channel = self.serve(
            (
                f"POST {self.path} HTTP/1.1\r\n"
                "Host: 127.0.0.1:41234\r\n"
                "Content-Length: 4\r\n"
                "Content-Length: 40\r\n\r\n"
            ).encode("latin-1")
            + b"pingGET /api/sessions HTTP/1.1\r\nHost: x\r\n\r\n"
        )

        self.assertEqual(channel.status, 400)
        self.assertEqual(self.gridvibe.requests, [])

    def test_an_obsolete_folded_header_is_refused_rather_than_rejoined(self):
        channel = self.serve(
            (
                f"POST {self.path} HTTP/1.1\r\n"
                "Host: 127.0.0.1:41234\r\n"
                "X-Thing: one\r\n"
                "\ttwo\r\n\r\n"
            ).encode("latin-1")
        )

        self.assertEqual(channel.status, 400)
        self.assertEqual(self.gridvibe.requests, [])

    def test_a_body_beyond_the_ceiling_is_refused_before_it_is_read(self):
        channel = self.serve(
            (
                f"POST {self.path} HTTP/1.1\r\n"
                "Host: 127.0.0.1:41234\r\n"
                f"Content-Length: {ssh_tunnel.MAX_BODY_BYTES + 1}\r\n\r\n"
            ).encode("latin-1")
        )

        self.assertEqual(channel.status, 413)
        self.assertEqual(self.gridvibe.requests, [])

    def test_a_head_that_never_ends_is_dropped_at_the_ceiling(self):
        """Finishing at all is the assertion: the read is bounded."""
        channel = self.serve(
            f"POST {self.path} HTTP/1.1\r\n".encode("latin-1"),
            endless=b"X-Pad: " + b"0" * 4096 + b"\r\n",
        )

        self.assertEqual(channel.status, 400)
        self.assertEqual(self.gridvibe.requests, [])

    def test_the_request_read_is_bounded_by_a_timeout(self):
        """A channel that opens and says nothing must not hold a thread."""
        channel = self.serve(self.request(f"POST {self.path} HTTP/1.1"))

        self.assertEqual(channel.timeouts[0], ssh_tunnel.REQUEST_READ_TIMEOUT)
        # ...and the reply is not bounded by it: two tools wait 25s on a page.
        self.assertIn(None, channel.timeouts)

    def test_a_gridvibe_that_cannot_be_reached_is_answered_not_dropped(self):
        closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        closed.bind(("127.0.0.1", 0))
        dead_port = closed.getsockname()[1]
        closed.close()

        channel = _FakeChannel(self.request(f"POST {self.path} HTTP/1.1"))
        ssh_tunnel._serve_forwarded_channel(
            channel, "127.0.0.1", dead_port, self.path
        )

        self.assertEqual(channel.status, 502)
        self.assertTrue(channel.closed)

    def test_a_port_opened_with_no_path_serves_nothing(self):
        channel = _FakeChannel(self.request(f"POST {self.path} HTTP/1.1"))
        ssh_tunnel._serve_forwarded_channel(channel, "127.0.0.1", self.gridvibe.port, "")

        self.assertEqual(channel.status, 404)
        self.assertEqual(self.gridvibe.requests, [])


class PaneCloseRevokesItsTokenTestCase(unittest.TestCase):
    """The stated bound, driven through the close path that has to keep it.

    Withdrawing the remote listener is not enough to end a token's life. The
    same ``/mcp/<token>`` is reachable from any process on *this* machine, and
    a config file left on a shared remote host names it in plain text. A token
    that outlives its pane is a standing key to this machine's tools naming a
    pane that is gone -- one that still creates workspaces and still splits.
    """

    def setUp(self):
        from web import api, terminal_io

        self.terminal = terminal_io
        api.app.config["TESTING"] = True
        self.http = api.app.test_client()
        mcp_http.pane_tokens.clear()
        self.addCleanup(mcp_http.pane_tokens.clear)
        self.registry = {}
        for name, value in (
            ("ssh_connections", self.registry),
            ("session_output_buffers", {}),
            ("session_terminal_sizes", {}),
        ):
            context = patch.object(terminal_io, name, value)
            context.start()
            self.addCleanup(context.stop)
        for name in (
            "session_manager",
            "_broadcast_session_status",
            "_evict_pooled_ssh_client",
        ):
            context = patch.object(terminal_io, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)

    def _tunnelled_pane(self, session_id: str = "pane-1") -> str:
        """One open pane with a tunnel and the token its config names."""
        token = mcp_http.pane_tokens.mint(
            session_id=session_id, group_id="g1", workspace_id="ws1"
        )
        self.registry[session_id] = {
            "kind": "ssh",
            "client": MagicMock(),
            "channel": MagicMock(),
            "mcp_tunnel": {"remote_port": 41234, "remote_path": "", "sftp": None},
        }
        return token

    def _call(self, token: str):
        return self.http.post(
            f"/mcp/{token}",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            content_type="application/json",
        )

    def test_closing_a_pane_revokes_the_token_its_tunnel_carried(self):
        token = self._tunnelled_pane()
        self.assertTrue(mcp_http.pane_tokens.resolve(token))

        self.terminal._close_ssh_connection("pane-1")

        self.assertEqual(mcp_http.pane_tokens.resolve(token), {})

    def test_the_endpoint_refuses_it_afterwards_like_a_token_that_never_was(self):
        token = self._tunnelled_pane()
        self.assertEqual(self._call(token).status_code, 200)

        self.terminal._close_ssh_connection("pane-1")

        refused = self._call(token)
        self.assertEqual(refused.status_code, 404)
        self.assertIn("Unknown or expired", refused.get_json()["error"])

    def test_a_relaunch_comes_back_with_a_new_token(self):
        """The one close that is followed by a reconnect: the remote config is
        rewritten for the new port anyway, so the old token has no reason to
        outlive the transport that carried it."""
        first = self._tunnelled_pane()

        self.terminal._close_ssh_connection("pane-1")
        second = mcp_http.pane_tokens.mint(session_id="pane-1")

        self.assertNotEqual(first, second)
        self.assertEqual(mcp_http.pane_tokens.resolve(first), {})
        self.assertTrue(mcp_http.pane_tokens.resolve(second))

    def test_a_close_that_names_another_connection_leaves_the_token_alone(self):
        """A retired attempt closing late must not disarm the live pane."""
        token = self._tunnelled_pane()

        self.terminal._close_ssh_connection(
            "pane-1", expected={"kind": "ssh", "client": MagicMock()}
        )

        self.assertTrue(mcp_http.pane_tokens.resolve(token))

    def test_closing_a_pane_that_never_tunnelled_is_a_no_op(self):
        other = self._tunnelled_pane("pane-1")
        self.registry["local-pane"] = {"kind": "local", "master_fd": None}

        self.terminal._close_ssh_connection("local-pane")

        self.assertTrue(mcp_http.pane_tokens.resolve(other))


class EstablishTunnelTestCase(unittest.TestCase):
    """What opening the tunnel does, and the two cases where it must not.

    It runs on the connect path after the pane is already CONNECTED and the
    reader is already looking at a shell, which is what makes both of these
    quiet: a tunnel opened for an agent that cannot call it, and a tunnel
    recorded on a connection a close has already retired, are both invisible
    from the pane. What is left behind is on the *remote* host -- an sshd
    listener for the life of the transport, a file naming a live token -- plus
    an SFTP channel held by a record nobody will read and a token nothing will
    revoke.
    """

    def setUp(self):
        from web import terminal_io

        self.terminal = terminal_io
        mcp_http.pane_tokens.clear()
        self.addCleanup(mcp_http.pane_tokens.clear)
        self.registry = {}
        context = patch.object(terminal_io, "ssh_connections", self.registry)
        context.start()
        self.addCleanup(context.stop)
        for name in ("session_manager", "_publish_ssh_terminal_output"):
            context = patch.object(terminal_io, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self.session_manager.groups = {}

        self.record = {
            "remote_port": 41234,
            "remote_path": "/home/ubuntu/.gridvibe/mcp-pane-1.json",
            "url": "http://127.0.0.1:41234/mcp/TOK",
            "sftp": MagicMock(),
        }
        for name, kwargs in (("establish", {"return_value": self.record}),
                             ("teardown", {})):
            context = patch.object(ssh_tunnel, name, **kwargs)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self.client = MagicMock()

    def _session(self, agent="claude"):
        return SimpleNamespace(
            agent_mcp=True,
            initial_command_mode="agent",
            agent_selection=agent,
            custom_agent="",
            group_id="g1",
            agent_depth=0,
        )

    def _open_pane(self, session_id="pane-1"):
        connection = {"kind": "ssh", "client": self.client}
        self.registry[session_id] = connection
        return connection

    def _minted_token(self):
        return self.establish.call_args.kwargs["token"]

    def test_a_pane_that_is_still_open_gets_the_record(self):
        connection = self._open_pane()

        self.terminal._establish_mcp_tunnel(
            "pane-1", self._session(), connection, self.client
        )

        self.assertIs(connection["mcp_tunnel"], self.record)
        self.assertTrue(mcp_http.pane_tokens.resolve(self._minted_token()))
        self.teardown.assert_not_called()

    def test_a_close_landing_while_it_opened_withdraws_it(self):
        """The race: `_shutdown_connection` popped an `mcp_tunnel` that was not
        there yet, so without this the record lands on a retired connection and
        nothing ever tears it down."""
        connection = self._open_pane()

        def close_midway(*_args, **_kwargs):
            self.registry.pop("pane-1", None)
            connection["retired"] = True
            return self.record

        self.establish.side_effect = close_midway

        self.terminal._establish_mcp_tunnel(
            "pane-1", self._session(), connection, self.client
        )

        self.assertNotIn("mcp_tunnel", connection)
        self.teardown.assert_called_once_with(self.client, self.record)
        self.assertEqual(mcp_http.pane_tokens.resolve(self._minted_token()), {})

    def test_a_connection_replaced_by_a_reconnect_is_stale_too(self):
        """Retired is not the only way to stop being this pane's connection."""
        connection = self._open_pane()

        def replace_midway(*_args, **_kwargs):
            self.registry["pane-1"] = {"kind": "ssh", "client": MagicMock()}
            return self.record

        self.establish.side_effect = replace_midway

        self.terminal._establish_mcp_tunnel(
            "pane-1", self._session(), connection, self.client
        )

        self.assertNotIn("mcp_tunnel", connection)
        self.teardown.assert_called_once_with(self.client, self.record)

    def test_a_teardown_that_fails_still_revokes_the_token(self):
        connection = self._open_pane()
        self.teardown.side_effect = OSError("the transport has already gone")

        def close_midway(*_args, **_kwargs):
            connection["retired"] = True
            self.registry.pop("pane-1", None)
            return self.record

        self.establish.side_effect = close_midway

        self.terminal._establish_mcp_tunnel(
            "pane-1", self._session(), connection, self.client
        )

        self.assertEqual(mcp_http.pane_tokens.resolve(self._minted_token()), {})

    def test_an_agent_with_no_mechanism_asks_the_remote_host_for_nothing(self):
        """No port, no token, no file -- for five of the eight registered CLIs,
        whose launch line carries nothing whatever this flag says."""
        for agent in ("grok", "hermes", "opencode", "kilo", "kimi"):
            with self.subTest(agent=agent):
                connection = self._open_pane(f"pane-{agent}")

                self.terminal._establish_mcp_tunnel(
                    f"pane-{agent}", self._session(agent), connection, self.client
                )

                self.establish.assert_not_called()
                self.assertNotIn("mcp_tunnel", connection)
                self.client.open_sftp.assert_not_called()

    def test_the_three_that_can_take_one_still_do(self):
        for agent in ("claude", "copilot", "codex"):
            with self.subTest(agent=agent):
                self.establish.reset_mock()
                connection = self._open_pane(f"pane-{agent}")

                self.terminal._establish_mcp_tunnel(
                    f"pane-{agent}", self._session(agent), connection, self.client
                )

                self.assertIs(connection["mcp_tunnel"], self.record)


class InProcessIntentPollTestCase(unittest.TestCase):
    """The intent poll, on the transport where the poll runs inside the server.

    ``dispatch`` runs in the Flask request handler here, so ``split_pane`` and
    ``open_window`` used to spend their whole wait issuing loopback GETs back
    into the server that was already holding a thread for them -- one per half
    second, each taking a second worker thread, for as long as the wait runs.
    Nothing was broken by it, because threading mode hands every request its
    own thread; that is the point. It was a dependency on a tuning knob that
    nothing wrote down.

    The store is in this process. These pin that the poll now waits on it.
    """

    def setUp(self):
        window_intents.reset()
        self.addCleanup(window_intents.reset)
        self.client = mcp_http._in_process_client_type()("http://127.0.0.1:5050")

    def test_the_poll_never_leaves_the_process(self):
        intent = window_intents.open("ws1")
        window_intents.claim(intent["intent_id"], "page-1")
        window_intents.record_result(intent["intent_id"], OPENED)

        with patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("the poll went back out over HTTP"),
        ):
            record = self.client.read_window_intent(intent["intent_id"])

        self.assertEqual(record["state"], OPENED)

    def test_it_answers_exactly_what_the_route_would_have(self):
        """Same store, same projection: a tool cannot tell which way it asked."""
        intent = window_intents.open_split("pane-4", "vertical", group_id="g1")
        window_intents.claim(intent["intent_id"], "page-1")
        window_intents.record_result(
            intent["intent_id"], SPLIT, result={"session_id": "pane-9"}
        )

        waited = self.client.read_window_intent(intent["intent_id"])
        read = window_intents.read(intent["intent_id"])

        for field in ("intent_id", "kind", "state", "detail", "result", "axis"):
            self.assertEqual(waited.get(field), read.get(field), field)

    def test_an_id_the_store_never_had_reads_expired_rather_than_blocking(self):
        started = time.monotonic()

        record = self.client.read_window_intent("not-an-intent")

        self.assertEqual(record["state"], "expired")
        self.assertLess(time.monotonic() - started, 5.0)

    def test_a_waiting_call_wakes_on_the_pages_report(self):
        """Not on a tick: the page settles the intent from another thread and
        the waiter returns with it, well inside the wait it was given."""
        intent = window_intents.open_split("pane-4", "horizontal")
        intent_id = intent["intent_id"]

        def settle():
            time.sleep(0.2)
            window_intents.claim(intent_id, "page-1")
            window_intents.record_result(
                intent_id, SPLIT, result={"session_id": "pane-9"}
            )

        page = threading.Thread(target=settle, daemon=True)
        started = time.monotonic()
        page.start()
        record = self.client.read_window_intent(intent_id)
        page.join(5)

        self.assertEqual(record["state"], SPLIT)
        self.assertEqual(record["result"]["session_id"], "pane-9")
        self.assertLess(time.monotonic() - started, mcp_http.INTENT_WAIT_SECONDS / 2)

    def test_the_whole_split_verb_costs_one_request_rather_than_one_per_tick(self):
        """The verb end to end on this transport: the intent is recorded over
        HTTP, as it always was, and the wait that follows it asks for nothing."""
        from gridvibe_mcp.splits import split_pane

        recorded = window_intents.open_split("pane-4", "vertical")

        def settle():
            time.sleep(0.2)
            window_intents.claim(recorded["intent_id"], "page-1")
            window_intents.record_result(
                recorded["intent_id"], SPLIT, result={"session_id": "pane-9"}
            )

        calls = []

        def only_the_intent(method, path, body=None):
            calls.append((method, path))
            return dict(recorded)

        page = threading.Thread(target=settle, daemon=True)
        page.start()
        with patch.object(self.client, "request", side_effect=only_the_intent):
            result = split_pane(self.client, "pane-4", "vertical")
        page.join(5)

        self.assertEqual(result["status"], SPLIT)
        self.assertEqual(result["pane"]["session_id"], "pane-9")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "POST")


class RemoteLaunchLineTestCase(unittest.TestCase):
    """What the remote shell is actually told to run."""

    def _pane(self, agent="claude"):
        return SimpleNamespace(
            initial_command=agent,
            initial_command_mode="agent",
            agent_selection=agent,
            agent_auto_mode=False,
            agent_mcp=True,
            mode="ssh",
            use_wsl=False,
            use_powershell=False,
        )

    REMOTE = "/home/ubuntu/.gridvibe/mcp-a1.json"
    URL = "http://127.0.0.1:41234/mcp/TOK"

    def test_a_tunnelled_pane_names_the_config_on_its_own_host(self):
        command = web_agents._compose_agent_startup_command(
            self._pane(), remote_config_path=self.REMOTE, remote_url=self.URL
        )

        self.assertEqual(command, f'claude --mcp-config "{self.REMOTE}"')

    def test_codex_is_given_the_url_because_it_cannot_read_that_file(self):
        """Inlining this machine's interpreter into a remote line would be wrong."""
        command = web_agents._compose_agent_startup_command(
            self._pane("codex"), remote_config_path=self.REMOTE, remote_url=self.URL
        )

        self.assertIn(f"mcp_servers.gridvibe.url='{self.URL}'", command)
        self.assertNotIn(self.REMOTE, command)
        self.assertNotIn("python", command.lower())

    def test_copilot_keeps_its_path_marker_remotely_too(self):
        command = web_agents._compose_agent_startup_command(
            self._pane("copilot"), remote_config_path=self.REMOTE, remote_url=self.URL
        )

        self.assertIn(f'--additional-mcp-config "@{self.REMOTE}"', command)

    def test_a_remote_line_is_quoted_for_a_posix_shell(self):
        # The remote shell is bash, whatever this machine runs.
        command = web_agents._compose_agent_startup_command(
            self._pane("codex"), remote_config_path=self.REMOTE, remote_url=self.URL
        )

        self.assertIn('-c "mcp_servers.gridvibe.url=', command)

    def test_no_tunnel_means_no_fragment_rather_than_a_local_path(self):
        command = web_agents._compose_agent_startup_command(self._pane())

        self.assertEqual(command, "claude")

    def test_a_pane_that_did_not_ask_gets_nothing_even_with_a_tunnel(self):
        pane = self._pane()
        pane.agent_mcp = False

        self.assertEqual(
            web_agents._compose_agent_startup_command(
                pane, remote_config_path=self.REMOTE, remote_url=self.URL
            ),
            "claude",
        )


class AgentLaunchDestinationTestCase(unittest.TestCase):
    """Which machine a pane an agent asked for actually opens on.

    The sidecar's launch body used to state a *local* group unconditionally, so
    an agent on an SSH pane got panes opened on GridVibe's own machine holding
    the remote host's paths: a PowerShell pane that could not cd into
    ``/home/ubuntu/...``, and a local-repository explorer rooted at a directory
    that exists only on the other end of the SSH connection.

    The body now names the pane it came from, and GridVibe reads the connection
    off that pane's live session in this process. An agent is never shown its
    own pane's credential, so this is the only place the answer can come from.
    """

    def setUp(self):
        from web import api

        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        self.api = api
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)

    def _origin(self, **overrides):
        """One live pane for the launch to be made from."""
        fields = {
            "host": "saso-workstation",
            "username": "ubuntu",
            "port": 2222,
            "password": "hunter2",
            "mode": "ssh",
            "directory": "/home/ubuntu/4g_core_workspace/test-simulator-4g",
        }
        fields.update(overrides)
        group = self.api.session_manager.create_group(
            name="origin",
            connection_mode="ssh" if fields["mode"] == "ssh" else "wsl",
            layout="single",
            terminal_count=1,
        )
        return self.api.session_manager.create_session(group_id=group.group_id, **fields)

    def _launch(self, body):
        """POST the body the sidecar builds, with nothing actually connecting."""
        with patch.object(self.api.socketio, "start_background_task"), patch.object(
            web_agents, "_agent_preflight_payload", return_value={"status": "present"}
        ):
            response = self.client.post("/api/sessions", json=body)
        return response.status_code, response.get_json()

    def _sidecar_body(self, origin_session_id, panes, **overrides):
        body = {
            # The sidecar's fallback, which a launch from inside a pane must
            # not be decided by.
            "connection_mode": "wsl",
            "origin_session_id": origin_session_id,
            "new_workspace": True,
            "workspace_label": "test",
            "session_name": "test",
            "layout": "split",
            "sessions": panes,
        }
        body.update(overrides)
        return body

    def _agent_pane(self, directory, title="Claude 1"):
        return {
            "title": title,
            "directory": directory,
            "startup_mode": "agent",
            "initial_command_mode": "agent",
            "initial_command": "claude",
            "agent_selection": "claude",
            "agent_mcp": True,
            "agent_depth": 1,
            # What the sidecar stamps on every pane it cannot ask about: a
            # local shell family, chosen by a default rather than by anybody.
            "use_powershell": True,
            "use_wsl": False,
        }

    def test_panes_open_on_the_machine_the_agent_is_already_on(self):
        origin = self._origin()
        directory = "/home/ubuntu/4g_core_workspace/test-simulator-4g"

        status, body = self._launch(
            self._sidecar_body(
                origin.session_id,
                [
                    self._agent_pane(directory),
                    self._agent_pane(directory, title="Claude 2"),
                    {
                        "title": "Explorer",
                        "directory": directory,
                        "startup_mode": "explorer",
                        "initial_command_mode": "explorer",
                        "initial_command": "",
                        "explorer_root_directory": directory,
                        "explorer_root_configured": True,
                    },
                ],
            )
        )

        self.assertEqual(status, 201)
        # An SSH group, though the body asked for a local one.
        self.assertEqual(body["connection_mode"], "ssh")
        panes = [
            self.api.session_manager.get_session(pane["session_id"])
            for pane in body["sessions"]
        ]
        self.assertEqual([pane.mode for pane in panes], ["ssh"] * 3)
        self.assertEqual([pane.host for pane in panes], ["saso-workstation"] * 3)
        self.assertEqual([pane.username for pane in panes], ["ubuntu"] * 3)
        self.assertEqual([pane.port for pane in panes], [2222] * 3)
        # The remote path is now a path on the machine that has it.
        self.assertEqual([pane.directory for pane in panes], [directory] * 3)
        self.assertEqual(panes[2].explorer_root_directory, directory)
        # And no pane carries a local shell family onto a host that has none.
        self.assertFalse(any(pane.use_powershell or pane.use_wsl for pane in panes))
        # The two agents keep the tools they were asked for: a remote pane
        # reaches GridVibe through its own reverse tunnel, not a sidecar.
        self.assertTrue(panes[0].agent_mcp and panes[1].agent_mcp)
        # Stamped one level deeper, so the depth budget still compounds.
        self.assertEqual([panes[0].agent_depth, panes[1].agent_depth], [1, 1])

    def test_the_credential_is_read_here_and_never_travels_through_the_agent(self):
        origin = self._origin()

        status, body = self._launch(
            self._sidecar_body(origin.session_id, [self._agent_pane("/srv/app")])
        )

        self.assertEqual(status, 201)
        # The launched pane can authenticate...
        pane = self.api.session_manager.get_session(body["sessions"][0]["session_id"])
        self.assertEqual(pane.password, "hunter2")
        # ...and it got there without appearing in what the agent sent, or in
        # what it is answered with.
        self.assertNotIn("hunter2", json.dumps(body))

    def test_a_pane_on_this_machine_still_launches_here(self):
        origin = self._origin(
            mode="wsl",
            host="PowerShell",
            username="",
            password=None,
            directory="C:/project",
        )

        status, body = self._launch(
            self._sidecar_body(origin.session_id, [self._agent_pane("C:/project")])
        )

        self.assertEqual(status, 201)
        self.assertEqual(body["connection_mode"], "wsl")
        pane = self.api.session_manager.get_session(body["sessions"][0]["session_id"])
        self.assertEqual(pane.mode, "wsl")
        # The pane family the caller chose is still the caller's to choose --
        # where the host has one to choose. PowerShell exists only on Windows,
        # and the launch service drops a family the host cannot run, so off
        # Windows "local" is the whole of what a local pane carries.
        self.assertEqual(pane.use_powershell, os.name == "nt")
        self.assertIsNone(pane.password)

    def test_a_launch_from_no_pane_at_all_is_the_launcher_unchanged(self):
        status, body = self._launch(
            {
                "connection_mode": "wsl",
                "new_workspace": True,
                "workspace_label": "from-the-launcher",
                "layout": "single",
                "sessions": [self._agent_pane("C:/project")],
            }
        )

        self.assertEqual(status, 201)
        self.assertEqual(body["connection_mode"], "wsl")

    def test_an_origin_pane_that_has_closed_is_refused_rather_than_opened_here(self):
        """Falling back to local is the one wrong answer: it invents a machine."""
        status, body = self._launch(
            self._sidecar_body("pane-that-closed", [self._agent_pane("/srv/app")])
        )

        self.assertEqual(status, 400)
        self.assertIn("no longer open", body["error"])
        self.assertEqual(self.api.session_manager.get_all_sessions(), [])

    def test_a_browser_pane_cannot_follow_an_agent_onto_a_remote_host(self):
        """GridVibe draws a browser pane, so it exists only in a local group."""
        origin = self._origin()

        status, body = self._launch(
            self._sidecar_body(
                origin.session_id,
                [
                    {
                        "title": "Preview",
                        "startup_mode": "browser",
                        "initial_command_mode": "browser",
                        "initial_command": "http://example.test",
                    },
                ],
            )
        )

        self.assertEqual(status, 400)
        self.assertIn("browser pane", body["error"])
        self.assertIn("saso-workstation", body["error"])
        # Refused before anything was built, rather than downgraded to a shell.
        self.assertEqual(
            [
                session.session_id
                for session in self.api.session_manager.get_all_sessions()
            ],
            [origin.session_id],
        )

    def test_the_origin_is_a_launch_time_fact_and_is_never_stored_on_a_pane(self):
        origin = self._origin()

        _status, body = self._launch(
            self._sidecar_body(origin.session_id, [self._agent_pane("/srv/app")])
        )

        pane = self.api.session_manager.get_session(body["sessions"][0]["session_id"])
        self.assertNotIn("origin_session_id", pane.to_dict())


if __name__ == "__main__":
    unittest.main()
