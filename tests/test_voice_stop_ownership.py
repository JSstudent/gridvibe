import threading
import unittest
from unittest.mock import MagicMock, patch

from web import voice


class VoiceStopOwnershipTestCase(unittest.TestCase):
    def setUp(self):
        self.ws = MagicMock()
        self.lock = threading.Lock()
        self.connections = {'pane': self.ws}
        self.locks = {'pane': self.lock}
        for name, value in [('_vosk_ws_connections', self.connections), ('_vosk_session_locks', self.locks)]:
            context = patch.object(voice, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(voice, 'emit')
        self.emit = context.start()
        self.addCleanup(context.stop)

    def test_stop_timeout_cancels_without_send_or_receive(self):
        self.locks['pane'] = MagicMock()
        self.locks['pane'].acquire.return_value = False
        lock = self.locks['pane']
        self.assertFalse(voice._stop_vosk_voice_session('pane'))
        self.ws.send.assert_not_called()
        self.ws.recv.assert_not_called()
        self.ws.close.assert_called_once()
        lock.release.assert_not_called()
        self.assertEqual(self.connections, {})
        self.assertEqual(self.emit.call_args.args[1]['status'], 'error')

    def test_old_audio_error_cannot_remove_restarted_recording(self):
        new_ws, new_lock = MagicMock(), threading.Lock()

        def restart():
            self.connections['pane'] = new_ws
            self.locks['pane'] = new_lock
            raise OSError('old transport ended')

        self.ws.recv.side_effect = restart
        voice._handle_vosk_audio_chunk('pane', b'audio')
        self.assertIs(self.connections['pane'], new_ws)
        self.assertIs(self.locks['pane'], new_lock)
        self.assertFalse(self.lock.locked())
        self.emit.assert_not_called()

    def test_old_stop_result_does_not_reach_new_recording(self):
        new_ws = MagicMock()

        def restart():
            self.connections['pane'] = new_ws
            self.locks['pane'] = threading.Lock()
            return '{"text":"old text"}'

        self.ws.recv.side_effect = restart
        voice._stop_vosk_voice_session('pane')
        self.assertIs(self.connections['pane'], new_ws)
        new_ws.close.assert_not_called()
        self.emit.assert_not_called()
        self.assertFalse(self.lock.locked())

    def test_old_stop_errors_do_not_interrupt_restarted_recording(self):
        for timeout in [True, False]:
            with self.subTest(timeout=timeout):
                old, replacement = MagicMock(), MagicMock()
                lock = MagicMock()
                self.connections['pane'], self.locks['pane'] = old, lock

                def restart(*args, **kwargs):
                    self.connections['pane'] = replacement
                    self.locks['pane'] = threading.Lock()
                    if timeout:
                        return False
                    raise OSError('Old service ended')

                if timeout:
                    lock.acquire.side_effect = restart
                else:
                    lock.acquire.return_value = True
                    old.recv.side_effect = restart
                self.assertFalse(voice._stop_vosk_voice_session('pane'))
                self.emit.assert_not_called()
                self.assertIs(self.connections['pane'], replacement)
                replacement.close.assert_not_called()
                self.assertEqual(lock.release.call_count, 0 if timeout else 1)

    def test_final_text_is_emitted_once_and_all_holds_are_released(self):
        self.ws.recv.return_value = '{"text":"final text"}'
        voice._stop_vosk_voice_session('pane')
        voice._stop_vosk_voice_session('pane')
        self.emit.assert_called_once_with('voice_result', {'session_id': 'pane', 'text': 'final text', 'final': True})
        self.assertFalse(self.lock.locked())
        self.assertEqual(self.connections, {})
