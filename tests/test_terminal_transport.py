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
        self.session = SimpleNamespace(mode='wsl', startup_mode='terminal', status=SessionStatus.CONNECTED)
        for name, value in [('ssh_connections', self.registry), ('session_output_buffers', {}),
                            ('session_terminal_sizes', {})]:
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

    def test_replacement_waits_for_own_publication_without_blocking_other_panes(self):
        entered, proceed, replaced = threading.Event(), threading.Event(), threading.Event()
        old = terminal._begin_connection('pane')
        failures = []

        def emit(*args, **kwargs):
            entered.set()
            if not proceed.wait(2):
                failures.append('Timed out waiting for publication release')
            if self.registry.get('pane') is not old:
                failures.append('Published after replacement')

        def replace():
            terminal._begin_connection('pane')
            replaced.set()

        with patch.object(terminal.socketio, 'emit', side_effect=emit):
            publisher = threading.Thread(target=terminal._publish_ssh_terminal_output, args=('pane', 'old', old))
            replacer = threading.Thread(target=replace)
            publisher.start()
            try:
                self.assertTrue(entered.wait(2))
                replacer.start()
                self.assertFalse(replaced.wait(0.05))
                # A different pane must still be able to acquire the shared lock.
                unrelated = threading.Thread(target=terminal._begin_connection, args=('other',))
                unrelated.start()
                unrelated.join(1)
                self.assertFalse(unrelated.is_alive())
            finally:
                proceed.set()
                publisher.join(2)
                if replacer.ident is not None:
                    replacer.join(2)
            self.assertTrue(replaced.is_set())
            self.assertEqual(failures, [])
        terminal._publish_ssh_terminal_output('pane', 'late', old)
        self.assertEqual(self.emitted, [])

    def test_nonterminal_modes_refuse_a_pending_connection(self):
        for mode in ['explorer', 'browser']:
            with self.subTest(mode=mode):
                self.session.startup_mode = mode
                self.assertIsNone(terminal._begin_connection('pane'))
                self.assertEqual(self.registry, {})

    def test_delivered_input_cannot_promote_a_replacement(self):
        old = terminal._begin_connection('pane')
        replacement = terminal._begin_connection('pane')
        terminal._track_terminal_agent_input('pane', old, 'codex\n')
        self.session_manager.update_session_metadata.assert_not_called()
        self.assertIs(self.registry['pane'], replacement)

    def _local_session(self):
        return SimpleNamespace(distribution='', username='', directory='', initial_command='',
                               use_wsl=False, use_powershell=False, mode='wsl',
                               startup_mode='terminal', status=SessionStatus.CONNECTED)

    def _spawn_local_shell(self, session_id='pane'):
        """Run the Windows local connector far enough to spawn, and report the spawn."""
        winpty = MagicMock()
        with patch.object(terminal.os, 'name', 'nt'),                 patch.object(terminal, 'WinPtyProcess', winpty),                 patch.object(terminal, '_local_shell_integration', side_effect=lambda _, cmd, env: (cmd, env)),                 patch.object(terminal, '_drain_until_prompt'),                 patch.object(terminal, '_run_startup_sequence'),                 patch.object(terminal, '_stream_local_output'):
            terminal._connect_local_session(session_id, self._local_session())
        return winpty.spawn.call_args

    def _open_ssh_shell(self, session_id='pane'):
        """Run the SSH connector far enough to open a shell, and report the call."""
        paramiko = MagicMock()
        client = paramiko.SSHClient.return_value
        session = SimpleNamespace(host='h', port=22, username='u', password='', directory='',
                                  initial_command='', distribution='', mode='ssh',
                                  startup_mode='terminal', status=SessionStatus.CONNECTED)
        with patch.object(terminal, 'paramiko', paramiko),                 patch.object(terminal, '_apply_host_key_policy'),                 patch.object(terminal, '_run_startup_sequence'),                 patch.object(terminal, '_stream_ssh_output'):
            terminal._connect_ssh_session(session_id, session)
        return client.invoke_shell.call_args

    def test_a_pane_with_no_reported_size_opens_its_pty_at_the_default(self):
        """Nothing has been fitted yet at first launch, so the default stands."""
        default = (terminal.DEFAULT_TERMINAL_COLS, terminal.DEFAULT_TERMINAL_ROWS)

        self.assertEqual(terminal._terminal_size_for('pane'), default)
        self.assertEqual(self._open_ssh_shell().kwargs['width'], default[0])
        self.assertEqual(self._open_ssh_shell().kwargs['height'], default[1])
        self.registry.clear()
        self.assertEqual(self._spawn_local_shell().kwargs['dimensions'], default[::-1])

    def test_a_replacement_pty_opens_at_the_size_the_pane_is_already_drawn_at(self):
        """The relaunch case: the pane on screen is not redrawn, so the new PTY
        cannot start at the default and wait to be told -- a full-screen agent
        draws its first frame before any resize could arrive."""
        terminal._record_terminal_size('pane', 100, 40)

        shell = self._open_ssh_shell()
        self.assertEqual((shell.kwargs['width'], shell.kwargs['height']), (100, 40))

        self.registry.clear()
        self.assertEqual(self._spawn_local_shell().kwargs['dimensions'], (40, 100))

    def test_a_size_reported_with_no_connection_is_what_the_next_pty_opens_at(self):
        """A relaunching pane has no transport to resize at all, and that is
        precisely the window its replacement's geometry is decided in."""
        self.assertEqual(self.registry, {})

        terminal._record_terminal_size('pane', 132, 42)

        self.assertEqual(terminal._terminal_size_for('pane'), (132, 42))
        self.assertEqual(self._spawn_local_shell().kwargs['dimensions'], (42, 132))

    def test_a_reported_size_is_clamped_before_any_pty_is_opened(self):
        for reported, expected in [((4, 2), (8, 8)), ((4000, 900), (400, 200))]:
            with self.subTest(reported=reported):
                terminal._record_terminal_size('pane', *reported)
                self.assertEqual(terminal._terminal_size_for('pane'), expected)

    def test_a_relaunch_keeps_the_recorded_size_and_a_closed_pane_drops_it(self):
        terminal._record_terminal_size('pane', 100, 40)
        self.registry['pane'] = {'kind': 'local'}

        # A relaunch closes the transport with the session -- and the pane --
        # still there.
        terminal._close_ssh_connection('pane')
        self.assertEqual(terminal._terminal_size_for('pane'), (100, 40))

        self.registry['pane'] = {'kind': 'local'}
        self.session_manager.get_session.return_value = None
        terminal._close_ssh_connection('pane')
        self.assertEqual(
            terminal._terminal_size_for('pane'),
            (terminal.DEFAULT_TERMINAL_COLS, terminal.DEFAULT_TERMINAL_ROWS),
        )

    def test_posix_shells_are_started_inside_a_correctly_sized_pty(self):
        """The ioctl has to land on the master before the shell is started:
        after it, the shell has already read the geometry it was born with."""
        terminal._record_terminal_size('pane', 100, 40)
        order = []
        fcntl = MagicMock()
        fcntl.ioctl.side_effect = lambda *args: order.append(('ioctl', args[2]))
        with (
            patch.object(terminal.os, 'name', 'posix'),
            patch.object(terminal, 'pty') as pty,
            patch.object(terminal, 'fcntl', fcntl),
            patch.object(terminal, 'termios') as termios,
            patch.object(terminal.subprocess, 'Popen', side_effect=lambda *a, **kw: order.append(('spawn',))),
            patch.object(terminal.os, 'close'),
            # Windows Python 3.11 lacks this POSIX API; provide it for the simulation.
            patch.object(terminal.os, 'set_blocking', create=True),
            patch.object(terminal, '_local_shell_integration', side_effect=lambda _, cmd, env: (cmd, env)),
            patch.object(terminal, '_run_startup_sequence'),
            patch.object(terminal, '_stream_local_output'),
        ):
            pty.openpty.return_value = (101, 102)
            terminal._connect_local_session('pane', self._local_session())

        self.assertEqual([step[0] for step in order], ['ioctl', 'spawn'])
        self.assertEqual(order[0][1], terminal.struct.pack('HHHH', 40, 100, 0, 0))
        self.assertEqual(fcntl.ioctl.call_args.args[:2], (101, termios.TIOCSWINSZ))

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
