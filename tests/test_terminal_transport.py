import io
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sessions.manager import SessionStatus
from web import terminal_io as terminal


class TerminalTransportTestCase(unittest.TestCase):
    def setUp(self):
        self.registry = {}
        self.session = SimpleNamespace(startup_mode='terminal', status=SessionStatus.CONNECTED)
        for name, value in [('ssh_connections', self.registry), ('session_output_buffers', {})]:
            context = patch.object(terminal, name, value)
            context.start()
            self.addCleanup(context.stop)
        for name in ['session_manager', '_broadcast_session_status', '_evict_pooled_ssh_client']:
            context = patch.object(terminal, name)
            setattr(self, name, context.start())
            self.addCleanup(context.stop)
        self.session_manager.get_session.return_value = self.session
        self.emitted = []
        context = patch.object(terminal.socketio, 'emit', side_effect=lambda event, data, **kw: self.emitted.append(data))
        context.start()
        self.addCleanup(context.stop)

    def test_retired_readers_cannot_publish_or_retire_replacement(self):
        for kind in ['ssh', 'local']:
            for outcome in [b'late output', b'', OSError('retired')]:
                with self.subTest(kind=kind, outcome=outcome):
                    replacement = {'kind': kind, 'channel': MagicMock()}

                    def read(_size):
                        self.registry['pane'] = replacement
                        if isinstance(outcome, Exception):
                            raise outcome
                        return outcome

                    transport = SimpleNamespace(closed=False, settimeout=lambda _: None, recv=read, read1=read)
                    old = {'kind': kind, 'channel': transport, 'stdout': transport}
                    self.registry['pane'] = old
                    self.session_manager.update_session_status.reset_mock()
                    self.emitted.clear()
                    getattr(terminal, '_stream_' + kind + '_output')('pane')
                    self.assertIs(self.registry['pane'], replacement)
                    replacement['channel'].close.assert_not_called()
                    self.session_manager.update_session_status.assert_not_called()
                    self.assertEqual(self.emitted, [])

    def test_utf8_is_incremental_for_ssh_and_local_bytes(self):
        text = 'é € 🚀 \x1b]7;file://host/tmp/🚀\x07'
        raw = text.encode()
        for kind in ['ssh', 'local']:
            for boundary in range(1, len(raw)):
                with self.subTest(kind=kind, boundary=boundary):
                    chunks = iter([raw[:boundary], raw[boundary:], b''])
                    transport = SimpleNamespace(closed=False, settimeout=lambda _: None,
                                                recv=lambda _: next(chunks), read1=lambda _: next(chunks))
                    self.registry['pane'] = {'kind': kind, 'channel': transport, 'stdout': transport}
                    self.emitted.clear()
                    with patch.object(terminal, '_observe_terminal_output_cwd') as observe:
                        getattr(terminal, '_stream_' + kind + '_output')('pane')
                    self.assertEqual(''.join(item['data'] for item in self.emitted), text)
                    self.assertEqual(''.join(call.args[2] for call in observe.call_args_list), text)

    def test_invalid_utf8_and_incomplete_eof_are_visible(self):
        self.registry['pane'] = {'kind': 'local', 'stdout': io.BytesIO(b'A\xffB\xe2')}
        terminal._stream_local_output('pane')
        self.assertEqual(''.join(item['data'] for item in self.emitted), 'A�B�')

    def test_partial_ssh_writes_deliver_exact_unicode_once(self):
        received = bytearray()

        def send(data):
            received.extend(data[:3])
            return min(3, len(data))

        channel = SimpleNamespace(closed=False, send_ready=lambda: True, send=send)
        terminal._send_connection_input({'kind': 'ssh', 'channel': channel}, 'é € 🚀\n')
        self.assertEqual(received.decode(), 'é € 🚀\n')

    def test_zero_write_and_retirement_fail_without_replay(self):
        for retired in [False, True]:
            channel = SimpleNamespace(closed=False, send_ready=lambda: True, send=MagicMock(return_value=0))
            with self.assertRaises(OSError):
                terminal._send_connection_input({'kind': 'ssh', 'channel': channel, 'retired': retired}, 'cmd\n')
            self.assertEqual(channel.send.call_count, 0 if retired else 1)

    def test_busy_ssh_write_has_a_deadline(self):
        channel = SimpleNamespace(closed=False, send_ready=lambda: False)
        started = time.monotonic()
        with patch.object(terminal, 'TERMINAL_WRITE_TIMEOUT', 0.04), self.assertRaises(TimeoutError):
            terminal._send_connection_input({'kind': 'ssh', 'channel': channel}, 'cmd\n')
        self.assertLess(time.monotonic() - started, 0.5)

    def test_concurrent_senders_do_not_interleave(self):
        entered, proceed = threading.Event(), threading.Event()
        received = bytearray()

        def send(data):
            if not entered.is_set():
                entered.set()
                self.assertTrue(proceed.wait(2))
            received.extend(data[:1])
            return 1

        connection = {'kind': 'ssh', 'channel': SimpleNamespace(closed=False, send_ready=lambda: True, send=send)}
        first = threading.Thread(target=terminal._send_connection_input, args=(connection, 'AAA\n'))
        second = threading.Thread(target=terminal._send_connection_input, args=(connection, 'BBB\n'))
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        proceed.set()
        first.join(2)
        second.join(2)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(received, b'AAA\nBBB\n')

    def test_local_fd_partial_writes_are_completed(self):
        received = bytearray()

        def write(fd, data):
            received.extend(data[:2])
            return min(2, len(data))

        with patch.object(terminal.select, 'select', return_value=([], [101], [])), patch.object(terminal.os, 'write', side_effect=write):
            terminal._send_connection_input({'kind': 'local', 'master_fd': 101}, '🚀\n')
        self.assertEqual(received, '🚀\n'.encode())

    def test_overlapping_connector_and_close_invalidate_old_attempt(self):
        first = terminal._begin_connection('pane')
        second = terminal._begin_connection('pane')
        self.assertTrue(first['retired'])
        terminal._connection_status('pane', first, SessionStatus.ERROR)
        terminal._close_ssh_connection('pane', expected=first)
        self.assertIs(self.registry['pane'], second)
        terminal._close_ssh_connection('pane')
        self.assertFalse(terminal._connection_is_current('pane', second))
        self.session_manager.update_session_status.assert_not_called()

    def test_posix_spawn_failure_closes_both_descriptors(self):
        session = SimpleNamespace(distribution='', username='', directory='', initial_command='', use_wsl=False)
        with patch.object(terminal.os, 'name', 'posix'), patch.object(terminal, 'pty') as pty, \
                patch.object(terminal.subprocess, 'Popen', side_effect=FileNotFoundError('shell')), \
                patch.object(terminal.os, 'close') as close, \
                patch.object(terminal, '_local_shell_integration', side_effect=lambda _, cmd, env: (cmd, env)):
            pty.openpty.return_value = (101, 102)
            terminal._connect_local_session('pane', session)
        self.assertCountEqual([call.args[0] for call in close.call_args_list], [101, 102])
        self.assertNotIn('pane', self.registry)
