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
import sys
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

    def test_the_written_file_is_readable_only_by_its_owner(self):
        sftp = MagicMock()
        handle = MagicMock()
        sftp.open.return_value.__enter__ = MagicMock(return_value=handle)
        sftp.open.return_value.__exit__ = MagicMock(return_value=False)

        written = ssh_tunnel.write_remote_config(
            sftp, "/home/u/.gridvibe/mcp-a.json", "http://127.0.0.1:1/mcp/T"
        )

        self.assertTrue(written)
        # The token is a credential for this pane's tools.
        sftp.chmod.assert_called_once_with("/home/u/.gridvibe/mcp-a.json", 0o600)
        self.assertIn("mcpServers", handle.write.call_args.args[0])

    def test_a_write_that_fails_reports_rather_than_raises(self):
        sftp = MagicMock()
        sftp.open.side_effect = OSError("read-only file system")

        self.assertFalse(
            ssh_tunnel.write_remote_config(sftp, "/x/y.json", "http://h/mcp/T")
        )


class ReverseTunnelTestCase(unittest.TestCase):
    """Opening and withdrawing the route home."""

    def test_the_remote_listener_binds_loopback_and_an_assigned_port(self):
        transport = MagicMock()
        transport.request_port_forward.return_value = 41234

        port = ssh_tunnel.open_reverse_tunnel(
            transport, local_host="127.0.0.1", local_port=5050
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
                transport, local_host="127.0.0.1", local_port=5050
            ),
            0,
        )

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

        def fake_serve(channel, host, port):
            pumped.set()
            # Stand in for a long-lived connection: if the handler waited on
            # this, it would never return.
            released.wait(5)

        handler = ssh_tunnel._forward_handler("127.0.0.1", 5050)
        with patch.object(ssh_tunnel, "_serve_forwarded_channel", fake_serve):
            before = _threading.active_count()
            handler(MagicMock(), ("10.0.0.1", 5), ("127.0.0.1", 41234))
            # The whole point: control is back while the pump is still running.
            self.assertTrue(pumped.wait(5), "the channel was never served")
            self.assertGreater(_threading.active_count(), before)
            released.set()

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


if __name__ == "__main__":
    unittest.main()
