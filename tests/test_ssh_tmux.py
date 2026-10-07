"""Experimental tmux-backed SSH terminals (web/ssh_tmux.py and its consumers).

Behavioural: remote commands run against a fake paramiko client whose exec
channels answer from a script, so every assertion is about what GridVibe sent
to the host and what the pane ended up as.
"""

import json
import shutil
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import api
from sessions.manager import SessionManager, SessionStatus, TerminalSession
from web import config as web_config
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import session_modes as web_session_modes
from web import session_shell as web_session_shell
from web import ssh_tmux
from web import terminal_io as terminal
from web import workspaces as web_workspaces

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


class FakeChannel:
    def __init__(self, transport):
        self.transport = transport
        self.command = None
        self.pty = None
        self.closed = False
        self._output = b""
        self._status = 0

    def settimeout(self, _timeout):
        pass

    def set_combine_stderr(self, _combine):
        pass

    def get_pty(self, **kwargs):
        self.pty = kwargs

    def exec_command(self, command):
        self.command = command
        self.transport.commands.append(command)
        if self.pty is None:
            status, output = self.transport.answer(command)
            self._status = status
            self._output = output.encode()

    def recv(self, size):
        chunk, self._output = self._output[:size], self._output[size:]
        return chunk

    def exit_status_ready(self):
        return True

    def recv_exit_status(self):
        return self._status

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, answer):
        self.answer = answer
        self.commands = []
        self.channels = []

    def is_active(self):
        return True

    def set_keepalive(self, _interval):
        pass

    def open_session(self, timeout=None):
        channel = FakeChannel(self)
        self.channels.append(channel)
        return channel


def scripted(prepare_status=0, **answers):
    """An exec answerer: ``prepare_status`` for the attach-or-create script,
    a substring -> (status, output) map for anything else, else success."""

    def answer(command):
        if "has-session" in command and "new-session" in command:
            return prepare_status, ""
        for needle, result in answers.items():
            if needle.replace("_", "-") in command:
                return result
        return 0, ""

    return answer


def fake_client(answer=None):
    transport = FakeTransport(answer or scripted())
    client = MagicMock()
    client.get_transport.return_value = transport
    return client, transport


class GateAndNamesTestCase(unittest.TestCase):
    def test_gate_reads_the_captured_setting_and_defaults_off(self):
        self.assertFalse(ssh_tmux.tmux_sessions_enabled(SimpleNamespace()))
        self.assertTrue(
            ssh_tmux.tmux_sessions_enabled(SimpleNamespace(ssh_tmux_sessions=True))
        )
        state = web_config._build_runtime_state({"ssh": {"tmux_sessions": "yes"}})
        self.assertFalse(state.ssh_tmux_sessions)
        state = web_config._build_runtime_state({"ssh": {"tmux_sessions": True}})
        self.assertTrue(state.ssh_tmux_sessions)
        with open(web_config.DEFAULT_CONFIG_PATH, encoding="utf-8") as handle:
            self.assertIs(json.load(handle)["ssh"]["tmux_sessions"], False)

    def test_launch_names_are_gated_generated_validated_and_dropped_on_restore(self):
        pane = {"mode": "ssh", "startup_mode": "terminal", "tmux": True}
        self.assertEqual(ssh_tmux.launch_session_name(pane, enabled=False, restore=False), "")
        generated = ssh_tmux.launch_session_name(pane, enabled=True, restore=False)
        self.assertRegex(generated, r"^gv-[0-9a-f]{12}$")

        typed = {**pane, "tmux_session": "work"}
        self.assertEqual(ssh_tmux.launch_session_name(typed, enabled=True, restore=False), "work")
        # A restored snapshot names its session without the option flag.
        restored = {"mode": "ssh", "startup_mode": "terminal", "tmux_session": "gv-0123456789ab"}
        self.assertEqual(
            ssh_tmux.launch_session_name(restored, enabled=True, restore=True),
            "gv-0123456789ab",
        )

        for bad in ("a.b", "a:b", "x y", "$(rm)", "a" * 65):
            with self.subTest(bad=bad):
                invalid = {**pane, "tmux_session": bad}
                with self.assertRaises(ValueError):
                    ssh_tmux.launch_session_name(invalid, enabled=True, restore=False)
                self.assertEqual(
                    ssh_tmux.launch_session_name(invalid, enabled=True, restore=True), ""
                )

        for other in (
            {**typed, "mode": "wsl"},
            {**typed, "startup_mode": "explorer"},
            {"mode": "ssh", "startup_mode": "terminal"},
        ):
            with self.subTest(other=other):
                self.assertEqual(
                    ssh_tmux.launch_session_name(other, enabled=True, restore=False), ""
                )

    def test_preset_keeps_the_option_and_its_name_typed_or_generated(self):
        self.assertEqual(
            ssh_tmux.preset_tmux_fields({"tmux_session": "gv-0123456789ab"}),
            {"tmux": True, "tmux_session": "gv-0123456789ab"},
        )
        self.assertEqual(
            ssh_tmux.preset_tmux_fields({"tmux_session": "work"}),
            {"tmux": True, "tmux_session": "work"},
        )
        self.assertEqual(
            ssh_tmux.preset_tmux_fields({"tmux": True}),
            {"tmux": True, "tmux_session": ""},
        )
        self.assertEqual(ssh_tmux.preset_tmux_fields({}), {"tmux": False, "tmux_session": ""})

        entries = web_saved_sessions._normalize_terminal_entries(
            [
                {"title": "A", "tmux_session": "gv-0123456789ab"},
                {"title": "B", "tmux": True, "tmux_session": "work"},
                {"title": "C"},
            ],
            "ssh",
            minimum_count=3,
        )
        self.assertEqual(
            (entries[0]["tmux"], entries[0]["tmux_session"]), (True, "gv-0123456789ab")
        )
        self.assertEqual((entries[1]["tmux"], entries[1]["tmux_session"]), (True, "work"))
        self.assertNotIn("tmux", entries[2])
        local = web_saved_sessions._normalize_terminal_entries(
            [{"title": "A", "tmux": True}], "wsl", minimum_count=1
        )
        self.assertNotIn("tmux", local[0])

    def test_saving_a_live_workspace_keeps_its_generated_session(self):
        # The preset was saved before launch, so it holds the option with no
        # name; the live pane got a generated one, which the save must keep so
        # the next launch reattaches instead of starting another session.
        base = {
            "connection_mode": "ssh",
            "terminal_count": 1,
            "ssh": {"host": "box", "username": "dev"},
            "terminals": [{"title": "A", "tmux": True, "tmux_session": ""}],
        }
        workspace = {
            "terminal_count": 1,
            "terminals": [{"title": "A", "tmux_session": "gv-0123456789ab"}],
        }
        merged = web_saved_sessions._merge_workspace_session_config(base, workspace)
        saved = merged["terminals"][0]
        self.assertEqual((saved["tmux"], saved["tmux_session"]), (True, "gv-0123456789ab"))
        self.assertEqual(
            ssh_tmux.launch_session_name(
                {"mode": "ssh", "startup_mode": "terminal", **saved},
                enabled=True,
                restore=False,
            ),
            "gv-0123456789ab",
        )


class RemoteCommandsTestCase(unittest.TestCase):
    def test_prepare_maps_exit_statuses_and_quotes_every_argument(self):
        for status, state in [
            (0, ssh_tmux.TMUX_ATTACHED),
            (3, ssh_tmux.TMUX_MISSING),
            (10, ssh_tmux.TMUX_CREATED),
            (11, ssh_tmux.TMUX_CREATED_HOME),
        ]:
            with self.subTest(status=status):
                client, transport = fake_client(scripted(prepare_status=status))
                self.assertEqual(ssh_tmux.prepare(client, "work", "/srv/a b"), state)
                command = transport.commands[-1]
                self.assertTrue(command.startswith("sh -c "))
                self.assertIn("'=work'", command.replace("'\"'\"'", "'"))
                self.assertIn("/srv/a b", command)

        client, _ = fake_client(lambda _command: (1, "server exited unexpectedly"))
        with self.assertRaisesRegex(ssh_tmux.TmuxError, "server exited"):
            ssh_tmux.prepare(client, "work", "")

    def test_every_target_is_an_exact_quoted_match(self):
        client, transport = fake_client(
            scripted(**{"new_window": (0, "%7\n"), "display_message": (0, "/srv/app\n")})
        )
        ssh_tmux.kill(client, "work")
        ssh_tmux.current_path(client, "work")
        ssh_tmux.new_window(client, "work", "/srv/app")
        ssh_tmux.send_line(client, ssh_tmux.session_target("work"), "claude")
        self.assertEqual(ssh_tmux.attach_command("work"), "tmux attach-session -t '=work'")
        for command in transport.commands:
            with self.subTest(command=command):
                unquoted = command.replace("'\"'\"'", "'")
                self.assertRegex(unquoted, r"-t '=work:?'")
                self.assertNotRegex(unquoted, r"-t =work")
        with self.assertRaises(ssh_tmux.TmuxError):
            ssh_tmux.attach_command("bad name")

    def test_new_window_reports_the_pane_and_whether_it_reached_the_directory(self):
        client, _ = fake_client(scripted(**{"new_window": (0, "%7\n")}))
        self.assertEqual(ssh_tmux.new_window(client, "work", "/srv/app"), ("%7", True))
        client, _ = fake_client(scripted(**{"new_window": (11, "%8\n")}))
        self.assertEqual(ssh_tmux.new_window(client, "work", "/gone"), ("%8", False))
        client, _ = fake_client(scripted(**{"new_window": (1, "no server")}))
        with self.assertRaises(ssh_tmux.TmuxError):
            ssh_tmux.new_window(client, "work", "")

    def test_a_dropped_transport_is_a_tmux_error_for_every_caller(self):
        for failure in (OSError("reset"), EOFError(), RuntimeError("SSHException")):
            with self.subTest(failure=failure):
                client = MagicMock()
                client.get_transport.return_value.is_active.return_value = True
                client.get_transport.return_value.open_session.side_effect = failure
                with self.assertRaises(ssh_tmux.TmuxError):
                    ssh_tmux.prepare(client, "work", "")
                self.assertFalse(ssh_tmux.kill(client, "work"))
                self.assertEqual(ssh_tmux.current_path(client, "work"), "")

    def test_the_deadline_bounds_the_whole_command_not_each_read(self):
        clock = {"now": 0.0}
        client, transport = fake_client()

        class Trickle(FakeChannel):
            def recv(self, _size):
                clock["now"] += 1.0  # each read answers inside its own timeout
                return b"x"

        transport.open_session = lambda timeout=None: Trickle(transport)
        with patch.object(ssh_tmux.time, "monotonic", side_effect=lambda: clock["now"]):
            with self.assertRaisesRegex(ssh_tmux.TmuxError, "in time"):
                ssh_tmux._run(client, "tmux ls", timeout=5.0)
        self.assertLessEqual(clock["now"], 6.0)

    def test_a_host_that_never_acknowledges_the_request_is_bounded(self):
        client, transport = fake_client()

        class Silent(FakeChannel):
            def __init__(self, transport):
                super().__init__(transport)
                self.released = threading.Event()

            def exec_command(self, command):
                # Paramiko waits on the acknowledgement until the channel closes.
                self.released.wait(5)
                raise EOFError("Channel closed.")

            def close(self):
                self.closed = True
                self.released.set()

        transport.open_session = lambda timeout=None: Silent(transport)
        with self.assertRaisesRegex(ssh_tmux.TmuxError, "in time"):
            ssh_tmux._run(client, "tmux ls", timeout=0.05)
        with patch.object(ssh_tmux, "TMUX_EXEC_TIMEOUT", 0.05):
            with self.assertRaises(ssh_tmux.TmuxError):
                ssh_tmux.open_attach_channel(client, "work", 80, 24)

    def test_relaunch_intent_is_taken_once(self):
        ssh_tmux.request_new_window("pane", "/srv/app")
        self.assertEqual(ssh_tmux.take_new_window("pane"), "/srv/app")
        self.assertIsNone(ssh_tmux.take_new_window("pane"))
        ssh_tmux.request_new_window("pane")
        ssh_tmux.forget_session("pane")
        self.assertIsNone(ssh_tmux.take_new_window("pane"))


class ConnectTestCase(unittest.TestCase):
    def setUp(self):
        self.registry = {}
        for name, value in [
            ("ssh_connections", self.registry),
            ("session_output_buffers", {}),
            ("session_terminal_sizes", {}),
        ]:
            context = patch.object(terminal, name, value)
            context.start()
            self.addCleanup(context.stop)
        for name in ["session_manager", "_broadcast_session_status", "_evict_pooled_ssh_client"]:
            context = patch.object(terminal, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self.emitted = []
        context = patch.object(
            terminal.socketio, "emit",
            side_effect=lambda event, data, **kw: self.emitted.append(data),
        )
        context.start()
        self.addCleanup(context.stop)
        self.session = SimpleNamespace(
            session_id="pane", host="h", port=22, username="u", password="",
            directory="/srv/app", launch_directory="/srv/app", current_directory=None,
            initial_command="", initial_command_mode="command", distribution="",
            mode="ssh", startup_mode="terminal", status=SessionStatus.CONNECTED,
            tmux_session="work", agent_selection="", custom_agent="",
        )
        self.session_manager.get_session.return_value = self.session
        self.addCleanup(ssh_tmux.forget_session, "pane")

    def _connect(self, enabled=True, answer=None, run_startup=False):
        paramiko = MagicMock()
        paramiko.SSHException = type("SSHException", (Exception,), {})
        client = paramiko.SSHClient.return_value
        transport = FakeTransport(answer or scripted())
        client.get_transport.return_value = transport
        patches = [
            patch.object(terminal, "paramiko", paramiko),
            patch.object(terminal, "_apply_host_key_policy"),
            patch.object(terminal, "_establish_mcp_tunnel"),
            patch.object(terminal, "_stream_ssh_output"),
            patch.object(terminal.runtime_config, "ssh_tmux_sessions", enabled),
        ]
        if not run_startup:
            patches.append(patch.object(terminal, "_run_startup_sequence"))
        for context in patches:
            context.start()
        try:
            terminal._connect_ssh_session("pane", self.session)
        finally:
            for context in reversed(patches):
                context.stop()
        return client, transport

    def test_setting_off_opens_a_plain_shell_even_for_a_named_pane(self):
        client, transport = self._connect(enabled=False)
        client.invoke_shell.assert_called_once()
        self.assertEqual(transport.commands, [])
        self.assertNotIn("tmux_session", self.registry["pane"])

    def test_setting_on_attaches_through_a_pty_exec_channel(self):
        client, transport = self._connect(answer=scripted(prepare_status=0))
        client.invoke_shell.assert_not_called()
        attach = transport.channels[-1]
        self.assertEqual(attach.command, "tmux attach-session -t '=work'")
        self.assertEqual(attach.pty["term"], ssh_tmux.TMUX_CLIENT_TERM)
        connection = self.registry["pane"]
        self.assertEqual(connection["tmux_session"], "work")
        self.assertEqual(connection["tmux_state"], ssh_tmux.TMUX_ATTACHED)
        self.assertIs(connection["channel"], attach)
        # The create step got the launch directory, quoted.
        self.assertIn("/srv/app", transport.commands[0])

    def test_tmux_missing_falls_back_to_a_plain_shell_and_says_so(self):
        client, transport = self._connect(answer=scripted(prepare_status=3))
        client.invoke_shell.assert_called_once()
        self.assertNotIn("tmux_session", self.registry["pane"])
        self.assertTrue(any("tmux was not found" in item.get("data", "") for item in self.emitted))
        # The pane is a plain shell now, so it stops naming a session.
        self.session_manager.clear_tmux_session.assert_called_once_with("pane")

    def test_a_tmux_failure_puts_the_pane_in_error(self):
        self._connect(answer=lambda _command: (1, "lost server"))
        statuses = [
            call.args[1] for call in self.session_manager.update_session_status.call_args_list
        ]
        self.assertIn(SessionStatus.ERROR, statuses)
        self.assertNotIn("pane", self.registry)

    def _startup(
        self, state, *, startup_command="", new_window=None, answer=None, retired=False
    ):
        client, transport = fake_client(answer or scripted(**{"new_window": (0, "%9\n")}))
        self.transport = transport
        channel = MagicMock()
        connection = {
            "kind": "ssh", "client": client, "channel": channel,
            "tmux_session": "work", "tmux_state": state,
            "write_lock": __import__("threading").Lock(),
            "ownership_lock": __import__("threading").RLock(),
        }
        if new_window is not None:
            connection["tmux_new_window"] = new_window
        if retired:
            connection["retired"] = True
        self.registry["pane"] = connection
        with patch.object(
            terminal, "_compose_agent_startup_command", return_value=startup_command
        ), patch.object(terminal, "_note_agent_conversation_command"), patch.object(
            terminal.agent_handoffs, "pending_for", return_value=None
        ), patch.object(terminal.runtime_config, "terminal_shell_integration", True):
            terminal._run_startup_sequence(connection, self.session)
        return channel, transport

    def test_attach_and_terminal_create_type_nothing(self):
        for state in (ssh_tmux.TMUX_ATTACHED, ssh_tmux.TMUX_CREATED):
            with self.subTest(state=state):
                channel, transport = self._startup(state)
                channel.send.assert_not_called()
                channel.sendall.assert_not_called()
                self.assertEqual(transport.commands, [])

    def test_agent_create_sends_the_launch_line_with_send_keys(self):
        self.session.initial_command_mode = "agent"
        channel, transport = self._startup(ssh_tmux.TMUX_CREATED, startup_command="claude")
        channel.sendall.assert_not_called()
        channel.send.assert_not_called()
        self.assertEqual(len(transport.commands), 1)
        self.assertIn("send-keys", transport.commands[0])
        self.assertIn("claude", transport.commands[0])

    def test_a_pane_closed_during_startup_gets_no_tmux_command(self):
        # The close hands the client to the MCP tunnel's teardown, so it still
        # answers -- the retirement flag is what has to stop the launch line.
        self.session.initial_command_mode = "agent"
        for new_window in (None, ""):
            with self.subTest(new_window=new_window):
                with self.assertRaises(OSError):
                    self._startup(
                        ssh_tmux.TMUX_CREATED, startup_command="claude",
                        new_window=new_window, retired=True,
                    )
                self.assertEqual(self.transport.commands, [])

    def test_attached_or_home_created_agent_gets_no_launch_line_and_a_notice(self):
        self.session.initial_command_mode = "agent"
        for state, words in [
            (ssh_tmux.TMUX_ATTACHED, "attached to the existing tmux session work"),
            (ssh_tmux.TMUX_CREATED_HOME, "/srv/app is not available"),
        ]:
            with self.subTest(state=state):
                self.emitted.clear()
                _channel, transport = self._startup(state, startup_command="claude")
                self.assertEqual(transport.commands, [])
                self.assertTrue(any(words in item.get("data", "") for item in self.emitted))

    def test_a_home_fallback_is_reported_without_a_startup_command(self):
        self.session.launch_directory = "/srv/gone"
        for state, new_window, words in [
            (ssh_tmux.TMUX_CREATED_HOME, None, "/srv/gone is not available"),
            (ssh_tmux.TMUX_ATTACHED, "/srv/gone", "/srv/gone is not available"),
        ]:
            with self.subTest(state=state):
                self.emitted.clear()
                self._startup(
                    state, new_window=new_window,
                    answer=scripted(**{"new_window": (11, "%9\n")}),
                )
                notices = [item.get("data", "") for item in self.emitted]
                self.assertTrue(any(words in text for text in notices), notices)
                self.assertTrue(any("home directory" in text for text in notices), notices)

    def test_a_pane_that_reached_its_directory_gets_no_notice(self):
        for state in (ssh_tmux.TMUX_CREATED, ssh_tmux.TMUX_ATTACHED):
            with self.subTest(state=state):
                self.emitted.clear()
                self._startup(state)
                self.assertEqual(self.emitted, [])

    def test_relaunch_opens_a_new_window_and_types_there(self):
        self.session.initial_command_mode = "agent"
        _channel, transport = self._startup(
            ssh_tmux.TMUX_ATTACHED, startup_command="codex", new_window=""
        )
        self.assertIn("new-window", transport.commands[0])
        self.assertIn("send-keys -t %9", transport.commands[1])
        self.assertIn("codex", transport.commands[1])

    def test_tmux_pane_directory_comes_from_tmux_and_is_never_probed(self):
        client, transport = fake_client(scripted(**{"display_message": (0, "/srv/app/src\n")}))
        self.registry["pane"] = {"kind": "ssh", "client": client, "tmux_session": "work"}
        self.session.current_directory = None
        directory, source = terminal.effective_directory("pane", self.session, allow_probe=True)
        self.assertEqual((directory, source), ("/srv/app/src", terminal.CWD_SOURCE_PROCESS))
        self.assertIsNone(terminal._resolve_live_terminal_cwd("pane", self.session))

    def test_end_tmux_session_kills_only_an_attached_session(self):
        client, transport = fake_client()
        self.registry["pane"] = {"kind": "ssh", "client": client, "tmux_session": "work"}
        self.assertTrue(terminal._end_tmux_session("pane"))
        self.assertEqual(transport.commands, ["tmux kill-session -t '=work'"])
        self.registry["pane"] = {"kind": "ssh", "client": client}
        self.assertFalse(terminal._end_tmux_session("pane"))


class SnapshotTestCase(unittest.TestCase):
    def _agent_pane(self):
        return TerminalSession(
            session_id="s1", group_id="g1", host="box", port=2222, username="dev",
            directory="/srv/app", current_directory="/srv/app/deep",
            mode="ssh", startup_mode="agent", initial_command="claude",
            initial_command_mode="agent", agent_selection="claude", agent_mcp=True,
            agent_auto_mode=True, title="Builder", tmux_session="gv-0123456789ab",
        )

    def test_a_tmux_pane_is_captured_as_a_terminal_that_attaches(self):
        with patch.object(web_runtime_state, "tmux_sessions_enabled", return_value=True):
            snapshot = web_runtime_state._snapshot_session(self._agent_pane())
        self.assertEqual(snapshot["tmux_session"], "gv-0123456789ab")
        self.assertEqual(
            (snapshot["host"], snapshot["port"], snapshot["username"], snapshot["title"]),
            ("box", 2222, "dev", "Builder"),
        )
        self.assertEqual(snapshot["directory"], "/srv/app")
        self.assertEqual(snapshot["launch_directory"], "/srv/app")
        self.assertEqual(snapshot["startup_mode"], "terminal")
        self.assertFalse(snapshot["initial_command"])
        self.assertEqual(snapshot["agent_selection"], "")
        self.assertFalse(snapshot["agent_mcp"])
        self.assertFalse(snapshot["agent_auto_mode"])
        self.assertEqual(set(snapshot), set(web_runtime_state._SESSION_SNAPSHOT_FIELDS))

    def test_a_pane_whose_host_had_no_tmux_is_saved_whole(self):
        manager = SessionManager()
        pane = self._agent_pane()
        manager.sessions[pane.session_id] = pane
        manager.clear_tmux_session(pane.session_id)
        with patch.object(web_runtime_state, "tmux_sessions_enabled", return_value=True):
            snapshot = web_runtime_state._snapshot_session(pane)
        self.assertEqual(snapshot["tmux_session"], "")
        self.assertEqual(snapshot["startup_mode"], "agent")
        self.assertEqual(snapshot["initial_command"], "claude")
        self.assertEqual(snapshot["agent_selection"], "claude")

    def test_setting_off_writes_no_name(self):
        with patch.object(web_runtime_state, "tmux_sessions_enabled", return_value=False):
            snapshot = web_runtime_state._snapshot_session(self._agent_pane())
        self.assertEqual(snapshot["tmux_session"], "")
        self.assertEqual(snapshot["startup_mode"], "agent")
        self.assertEqual(snapshot["directory"], "/srv/app/deep")


class HttpTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        for module, name, filename in [
            (web_config, "CONFIG_PATH", "config.json"),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", "saved_sessions.json"),
            (web_runtime_state, "RUNTIME_STATE_PATH", "runtime_state.json"),
        ]:
            context = patch.object(module, name, str(root / filename))
            context.start()
            self.addCleanup(context.stop)
        self.addCleanup(api._refresh_runtime_config)
        api._refresh_runtime_config()
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        with api.connection_lock:
            api.ssh_connections.clear()
        self.addCleanup(api.ssh_connections.clear)

    def _enable(self, enabled=True):
        response = self.client.post("/api/app-config", json={"ssh": {"tmux_sessions": enabled}})
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.get_json()["ssh"]["tmux_sessions"], enabled)

    def _launch(self, *panes):
        with patch.object(api.socketio, "start_background_task"):
            return self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "ssh",
                    "layout": "grid",
                    "sessions": [
                        {"host": "box", "username": "dev", "port": 22, "directory": "/srv", **pane}
                        for pane in panes
                    ],
                },
            )

    def test_setting_round_trips_and_ignores_a_non_boolean(self):
        self.assertFalse(self.client.get("/api/app-config").get_json()["ssh"]["tmux_sessions"])
        self._enable()
        self.assertTrue(api.load_config()["ssh"]["tmux_sessions"])
        refused = self.client.post("/api/app-config", json={"ssh": {"tmux_sessions": "no"}})
        self.assertTrue(refused.get_json()["ssh"]["tmux_sessions"])

    def test_setting_off_ignores_a_requested_session(self):
        response = self._launch({"tmux": True, "tmux_session": "work"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["sessions"][0]["tmux_session"], "")

    def test_launch_names_generates_refuses_invalid_and_refuses_a_shared_session(self):
        self._enable()
        response = self._launch({"tmux": True}, {"tmux_session": "work"}, {"startup_mode": "explorer", "tmux": True})
        self.assertEqual(response.status_code, 201)
        names = [pane["tmux_session"] for pane in response.get_json()["sessions"]]
        self.assertRegex(names[0], r"^gv-[0-9a-f]{12}$")
        self.assertEqual(names[1:], ["work", ""])

        refused = self._launch({"tmux_session": "bad.name"})
        self.assertEqual(refused.status_code, 400)
        shared = self._launch({"tmux_session": "work"})
        self.assertEqual(shared.status_code, 400)
        self.assertIn("already attached", shared.get_json()["error"])
        # Another host's `work` is a different session.
        elsewhere = self._launch({"host": "other", "tmux_session": "work"})
        self.assertEqual(elsewhere.status_code, 201)

    def test_a_tool_launch_never_gets_a_tmux_session(self):
        self._enable()
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                "/api/sessions",
                json={
                    "connection_mode": "ssh",
                    "tool_launch": True,
                    "sessions": [{"host": "box", "username": "dev", "tmux": True}],
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["sessions"][0]["tmux_session"], "")

    def test_settings_and_launcher_markup_carry_the_experimental_option(self):
        modal = (ROOT / "templates/partials/app_settings_modal.html").read_text(encoding="utf-8")
        checkbox = modal[modal.index('id="appSshTmuxSessions"'):]
        self.assertIn("settings-experimental-badge", checkbox[:600])
        launcher = (ROOT / "web/static/js/launcher.js").read_text(encoding="utf-8")
        for hook in ("t-tmux-field", "t-tmux", "t-tmux-session", "tmux_sessions === true"):
            self.assertIn(hook, launcher)
        settings_js = (ROOT / "web/static/js/app-settings.js").read_text(encoding="utf-8")
        self.assertIn("appSshTmuxSessions", settings_js)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for shared launch-field tests")
    def test_the_shared_launch_fields_carry_the_tmux_option(self):
        # Both launch surfaces build each pane's request through
        # buildPaneLaunchFields; a field it leaves out never reaches the server.
        harness = r"""
const fs = require('fs');
const vm = require('vm');
const sandbox = {
    console,
    document: { getElementById: () => null, addEventListener: () => {} }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const build = sandbox.buildPaneLaunchFields;
process.stdout.write(JSON.stringify({
    generated: build({ startup_mode: 'terminal', tmux: true, tmux_session: '' }),
    named: build({ startup_mode: 'agent', tmux: true, tmux_session: ' work ' }),
    off: build({ startup_mode: 'terminal' }),
    explorer: build({ startup_mode: 'explorer', tmux: true, tmux_session: 'x' })
}));
"""
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [shutil.which("node"), str(script_path), str(ROOT / "web/static/js/shared.js")],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        result = json.loads(completed.stdout)
        pick = lambda fields: (fields["tmux"], fields["tmux_session"])  # noqa: E731
        self.assertEqual(pick(result["generated"]), (True, ""))
        self.assertEqual(pick(result["named"]), (True, "work"))
        self.assertEqual(pick(result["off"]), (False, ""))
        self.assertEqual(pick(result["explorer"]), (False, ""))

    def test_close_detaches_unless_asked_to_end_the_session(self):
        self._enable()
        sessions = self._launch({"tmux_session": "one"}, {"tmux_session": "two"}).get_json()["sessions"]
        transports = {}
        for pane in sessions:
            client, transport = fake_client()
            transports[pane["session_id"]] = (client, transport)
            api.ssh_connections[pane["session_id"]] = {
                "kind": "ssh", "client": client, "tmux_session": pane["tmux_session"],
            }
        first, second = (pane["session_id"] for pane in sessions)

        response = self.client.delete(f"/api/sessions/{first}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transports[first][1].commands, [])
        self.assertNotIn("tmux_ended", response.get_json())

        response = self.client.delete(f"/api/sessions/{second}?end_tmux=1")
        self.assertTrue(response.get_json()["tmux_ended"])
        self.assertEqual(transports[second][1].commands, ["tmux kill-session -t '=two'"])
        # Killed before the transport was closed.
        transports[second][0].close.assert_called()

    def test_an_omitted_user_names_the_same_session_as_root(self):
        self._enable()
        with patch.object(api.socketio, "start_background_task"):
            first = self.client.post(
                "/api/sessions",
                json={"connection_mode": "ssh", "sessions": [
                    {"host": "box", "username": "root", "tmux_session": "same"}
                ]},
            )
            second = self.client.post(
                "/api/sessions",
                json={"connection_mode": "ssh", "sessions": [
                    {"host": "box", "tmux_session": "same"}
                ]},
            )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)

    def test_concurrent_launches_cannot_both_claim_one_session(self):
        self._enable()
        barrier = threading.Barrier(2, timeout=10)
        original = web_workspaces._refuse_shared_tmux_sessions

        def check_then_wait(*args, **kwargs):
            original(*args, **kwargs)
            try:
                # The second launch can only reach the check once the first
                # has installed, so it must see the first's pane.
                barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

        statuses = []

        def launch():
            client = api.app.test_client()
            response = client.post(
                "/api/sessions",
                json={"connection_mode": "ssh", "sessions": [
                    {"host": "box", "username": "dev", "tmux_session": "race"}
                ]},
            )
            statuses.append(response.status_code)

        with patch.object(api.socketio, "start_background_task"), patch.object(
            web_workspaces, "_refuse_shared_tmux_sessions", side_effect=check_then_wait
        ):
            threads = [threading.Thread(target=launch) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertEqual(sorted(statuses), [201, 400])
        names = [s.tmux_session for s in api.session_manager.get_all_sessions()]
        self.assertEqual(names.count("race"), 1)

    def test_a_failed_end_still_closes_the_transport(self):
        self._enable()
        pane = self._launch({"tmux_session": "one"}).get_json()["sessions"][0]
        client = MagicMock()
        client.get_transport.return_value.is_active.return_value = True
        client.get_transport.return_value.open_session.side_effect = OSError("reset")
        api.ssh_connections[pane["session_id"]] = {
            "kind": "ssh", "client": client, "tmux_session": "one",
        }
        response = self.client.delete(f"/api/sessions/{pane['session_id']}?end_tmux=1")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["tmux_ended"])
        self.assertNotIn(pane["session_id"], api.ssh_connections)
        client.close.assert_called()

    def test_split_of_a_tmux_pane_gets_its_own_session(self):
        self._enable()
        source = self._launch({"tmux_session": "work"}).get_json()["sessions"][0]
        with patch.object(api.socketio, "start_background_task"):
            response = self.client.post(f"/api/sessions/{source['session_id']}/split", json={})
        self.assertEqual(response.status_code, 201)
        name = response.get_json()["session"]["tmux_session"]
        self.assertRegex(name, r"^gv-[0-9a-f]{12}$")


@unittest.skipUnless(NODE, "Node.js is required for the close-dialog test")
class CloseDialogTestCase(unittest.TestCase):
    """The real `closeTerminalPane`, with the grid swapped under its dialog."""

    STUBS = """
        let resizeIntentInFlight = false;
        let sessionIds = ['pane-A'];
        let terminals = [{ _session: {
            session_id: 'pane-A', mode: 'ssh', tmux_session: 'work',
            startup_mode: 'terminal', host: 'box'
        } }];
        const fetched = [];
        const toasts = [];
        let resolveDialog = null;
        function hasActiveExplorerFilesystemOperation() { return false; }
        function cancelExplorerFilesystemUiForSession() {}
        async function confirmDiscardExplorerEdit() { return true; }
        function openGenericConfirmModal() {
            return new Promise(resolve => { resolveDialog = resolve; });
        }
        function genericConfirmCheckboxChecked() { return true; }
        function previewTerminalClose(index) {
            return { sessionId: sessionIds[index], groupId: 'g', snapshot: {}, closeLastPane: false };
        }
        function showTerminalToast(message) { toasts.push(message); }
        function setWorkspaceSaveMessage(message) { toasts.push(message); }
        function forgetExplorerSessionMarkdownAppearance() {}
        const document = { getElementById: () => null };
        const closeSnapshotsBySessionId = new Map();
        async function fetch(url) {
            fetched.push(url);
            return { ok: true, json: async () => ({ tmux_ended: true }) };
        }
        function stageSessionCloseDelta() { return []; }
        async function initialLoad() {}
        function updateAllSplitButtonStates() {}
    """

    def _run(self, swap: bool):
        source = (ROOT / "web/static/js/terminals.js").read_text(encoding="utf-8")
        begin = source.index("    async function closeTerminalPane(")
        body = source[begin:source.index("    /* What a split is about to extend", begin)]
        driver = f"""
            (async () => {{
                const closing = closeTerminalPane(0);
                await new Promise(resolve => setTimeout(resolve, 0));
                if ({'true' if swap else 'false'}) {{
                    sessionIds = ['pane-B'];
                    terminals = [{{ _session: {{ session_id: 'pane-B', mode: 'ssh' }} }}];
                }}
                resolveDialog(true);
                await closing;
                process.stdout.write(JSON.stringify({{ fetched, toasts }}));
            }})();
        """
        with TemporaryDirectory() as script_dir:
            script = Path(script_dir) / "harness.js"
            script.write_text(self.STUBS + body + driver, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script)], capture_output=True, text=True,
                encoding="utf-8", check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_the_answer_ends_the_session_of_the_pane_it_was_asked_about(self):
        self.assertEqual(self._run(swap=False)["fetched"], ["/api/sessions/pane-A?end_tmux=1"])

    def test_a_pane_swapped_in_under_the_dialog_is_not_closed(self):
        outcome = self._run(swap=True)
        self.assertEqual(outcome["fetched"], [])
        self.assertTrue(any("Nothing was closed" in toast for toast in outcome["toasts"]))


class RelaunchIntentTestCase(unittest.TestCase):
    def tearDown(self):
        ssh_tmux.forget_session("pane")

    def test_relaunch_at_a_directory_asks_the_next_connection_for_a_new_window(self):
        session = SimpleNamespace(mode="ssh", tmux_session="work", startup_mode="terminal")
        effects = MagicMock()
        with patch.object(web_session_modes, "session_manager") as manager, patch.object(
            web_session_modes.agent_handoffs, "drop_bound"
        ):
            manager.get_session.return_value = SimpleNamespace(to_dict=lambda: {})
            effects.start_connector.side_effect = lambda _id: self.assertEqual(
                ssh_tmux.take_new_window("pane"), "/srv/app"
            )
            web_session_modes._relaunch_terminal_at("pane", session, "/srv/app", effects)
        effects.start_connector.assert_called_once()

    def test_a_plain_pane_relaunch_states_no_window(self):
        session = SimpleNamespace(mode="ssh", tmux_session="", startup_mode="terminal")
        effects = MagicMock()
        with patch.object(web_session_modes, "session_manager") as manager, patch.object(
            web_session_modes.agent_handoffs, "drop_bound"
        ):
            manager.get_session.return_value = SimpleNamespace(to_dict=lambda: {})
            web_session_modes._relaunch_terminal_at("pane", session, "/srv/app", effects)
        self.assertIsNone(ssh_tmux.take_new_window("pane"))

    def test_agent_relaunch_of_a_tmux_pane_asks_for_a_window_where_the_pane_is(self):
        session = SimpleNamespace(
            session_id="pane", mode="ssh", tmux_session="work", startup_mode="terminal",
            initial_command_mode="command", initial_command="", agent_selection="",
            custom_agent="", agent_auto_mode=False, agent_mcp=False,
            agent_mcp_override=False, directory="/srv", distribution="",
            to_dict=lambda: {},
        )
        effects = MagicMock(spec=["close_connection", "broadcast_status", "start_connector"])
        seen = []
        effects.start_connector.side_effect = lambda _id: seen.append(
            ssh_tmux.take_new_window("pane")
        )
        with patch.object(web_session_shell, "session_manager") as manager, patch.object(
            web_session_shell, "_refuse_an_agent_that_is_not_installed"
        ), patch.object(web_session_shell, "request_update"):
            manager.get_session.return_value = session
            web_session_shell.apply_pane_shell_change("pane", {"agent": "claude"}, effects)
        self.assertEqual(seen, [""])


class LaunchPreparationTestCase(unittest.TestCase):
    def test_restore_drops_a_shared_name_instead_of_refusing(self):
        live = SimpleNamespace(
            host="box", port=22, username="dev", tmux_session="work", mode="ssh", group_id="g1"
        )
        manager = SimpleNamespace(
            lock=__import__("threading").Lock(), sessions={"s1": live}
        )
        panes = [{"host": "box", "port": 22, "username": "dev", "tmux_session": "work"}]
        with patch.object(web_workspaces, "_manager", return_value=manager):
            web_workspaces._refuse_shared_tmux_sessions(panes, None, restore=True)
            self.assertEqual(panes[0]["tmux_session"], "")
            # The group a relaunch replaces does not count against itself.
            panes = [{"host": "box", "port": 22, "username": "dev", "tmux_session": "work"}]
            web_workspaces._refuse_shared_tmux_sessions(panes, "g1", restore=False)
            self.assertEqual(panes[0]["tmux_session"], "work")


if __name__ == "__main__":
    unittest.main()
