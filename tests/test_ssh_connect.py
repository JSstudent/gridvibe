"""Bounded SSH logins (web/ssh_connect.py) and the call sites that use it.

A workspace restore connects every SSH pane at once; past sshd's default
``MaxStartups`` the host drops logins before the banner. These tests drive the
gate with clients whose ``connect()`` blocks, so what they measure is how many
logins one host actually has in flight.
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from web import agents as web_agents
from web import explorer as web_explorer
from web import ssh_connect
from web import terminal_io as terminal


class BlockingClient:
    """A client whose connect() waits for the test, counting who is inside."""

    def __init__(self, tracker):
        self.tracker = tracker

    def connect(self, **kwargs):
        with self.tracker["lock"]:
            self.tracker["inside"] += 1
            self.tracker["peak"] = max(self.tracker["peak"], self.tracker["inside"])
            self.tracker["entered"].release()
        try:
            if self.tracker.get("fail"):
                raise OSError("refused")
            self.tracker["release"].wait(5)
        finally:
            with self.tracker["lock"]:
                self.tracker["inside"] -= 1


def tracker():
    return {
        "lock": threading.Lock(),
        "inside": 0,
        "peak": 0,
        "entered": threading.Semaphore(0),
        "release": threading.Event(),
    }


class GateTestCase(unittest.TestCase):
    def setUp(self):
        context = patch.object(ssh_connect, "_gates", {})
        context.start()
        self.addCleanup(context.stop)

    def _start(self, state, count, host="box", port=22):
        threads = []
        for _ in range(count):
            thread = threading.Thread(
                target=lambda: _swallow(
                    ssh_connect.connect_ssh_client,
                    BlockingClient(state), hostname=host, port=port, username="u",
                ),
                daemon=True,
            )
            thread.start()
            threads.append(thread)
        return threads

    def test_one_host_never_has_more_logins_in_flight_than_the_limit(self):
        state = tracker()
        threads = self._start(state, 10)
        for _ in range(ssh_connect.MAX_CONCURRENT_HANDSHAKES):
            self.assertTrue(state["entered"].acquire(timeout=5))
        # The rest are waiting for a slot, not connecting.
        self.assertFalse(state["entered"].acquire(timeout=0.2))
        state["release"].set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(state["peak"], ssh_connect.MAX_CONCURRENT_HANDSHAKES)

    def test_another_host_is_not_held_up_by_a_busy_one(self):
        busy = tracker()
        busy_threads = self._start(busy, ssh_connect.MAX_CONCURRENT_HANDSHAKES + 2, host="busy")
        for _ in range(ssh_connect.MAX_CONCURRENT_HANDSHAKES):
            self.assertTrue(busy["entered"].acquire(timeout=5))
        other = tracker()
        other_threads = self._start(other, 1, host="other")
        self.assertTrue(other["entered"].acquire(timeout=5))
        # The same host on another port is another sshd.
        other_port = tracker()
        port_threads = self._start(other_port, 1, host="BUSY", port=2222)
        self.assertTrue(other_port["entered"].acquire(timeout=5))
        for state in (busy, other, other_port):
            state["release"].set()
        for thread in busy_threads + other_threads + port_threads:
            thread.join(5)

    def test_a_failed_login_gives_its_slot_back(self):
        failing = tracker()
        failing["fail"] = True
        for thread in self._start(failing, ssh_connect.MAX_CONCURRENT_HANDSHAKES * 2):
            thread.join(5)
        state = tracker()
        threads = self._start(state, ssh_connect.MAX_CONCURRENT_HANDSHAKES)
        for _ in range(ssh_connect.MAX_CONCURRENT_HANDSHAKES):
            self.assertTrue(state["entered"].acquire(timeout=5))
        state["release"].set()
        for thread in threads:
            thread.join(5)


def _swallow(function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except OSError:
        pass


class CallSitesTestCase(unittest.TestCase):
    """Every SSH login GridVibe makes holds a slot while it connects."""

    def setUp(self):
        context = patch.object(ssh_connect, "_gates", {})
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(ssh_connect, "MAX_CONCURRENT_HANDSHAKES", 1)
        context.start()
        self.addCleanup(context.stop)
        self.held_during_connect = []

    def _paramiko(self):
        paramiko = MagicMock()
        paramiko.SSHException = type("SSHException", (Exception,), {})

        def connect(**kwargs):
            gate = ssh_connect._gate(kwargs["hostname"], kwargs["port"])
            free = gate.acquire(blocking=False)
            if free:
                gate.release()
            self.held_during_connect.append(not free)
            raise OSError("stop after connect")

        paramiko.SSHClient.return_value.connect.side_effect = connect
        return paramiko

    def test_a_terminal_pane_login(self):
        session = SimpleNamespace(
            host="box", port=22, username="u", password="pw", directory="/",
            initial_command="", tmux_session="",
        )
        with patch.object(terminal, "paramiko", self._paramiko()), \
                patch.object(terminal, "_apply_host_key_policy"), \
                patch.object(terminal, "_begin_connection", return_value={}), \
                patch.object(terminal, "_connection_status"), \
                patch.object(terminal, "_close_ssh_connection"):
            terminal._connect_ssh_session("pane", session)
        self.assertEqual(self.held_during_connect, [True])

    def test_an_explorer_login(self):
        session = SimpleNamespace(host="box", port=22, username="u", password="pw")
        with patch.object(web_explorer, "paramiko", self._paramiko()), \
                patch.object(web_explorer, "_apply_host_key_policy"):
            with self.assertRaises(OSError):
                web_explorer._open_ssh_sftp(session)
        self.assertEqual(self.held_during_connect, [True])

    def test_a_remote_agent_detection_login(self):
        with patch.object(web_agents, "paramiko", self._paramiko()), \
                patch.object(web_agents, "_apply_host_key_policy"):
            web_agents._detect_ssh_command(
                "codex", {"host": "box", "username": "u", "password": "pw", "port": 22}
            )
        self.assertEqual(self.held_during_connect, [True])


if __name__ == "__main__":
    unittest.main()
