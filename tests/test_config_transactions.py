import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from web import config


def _config_writer(path, changes, entered, release, attempted):
    from web.api import _normalize_app_config_update

    attempted.set()

    def update(current):
        entered.set()
        if not release.wait(10):
            raise TimeoutError('Test commit barrier was not released')
        return _normalize_app_config_update(changes, config._build_runtime_state(current))

    config.update_config(update, path)


class ConfigTransactionsTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'config.json'
        self.backup = Path(str(self.path) + '.bak')
        self.good = {'appearance': {'theme': 'light'}, 'terminal': {'font_size': 19}}

    def test_invalid_content_recovers_and_cannot_poison_backup(self):
        bodies = [b'\xff', b'[]', b'null', b'42', b'"text"']
        bodies += [json.dumps({name: None}).encode() for name in config._CONFIG_SECTIONS]
        for body in bodies:
            with self.subTest(body=body):
                self.backup.write_text(json.dumps(self.good), encoding='utf-8')
                self.path.write_bytes(body)
                result = config.load_config(str(self.path))
                self.assertEqual(result['appearance']['theme'], 'light')
                self.assertEqual(result['terminal']['font_size'], 19)
                result['terminal']['font_size'] = 21
                config.save_config(result, str(self.path))
                self.assertEqual(json.loads(self.backup.read_text(encoding='utf-8'))['appearance']['theme'], 'light')
                self.assertTrue(list(self.path.parent.glob('config.json.corrupt-*')))

    def test_corrupt_backup_does_not_publish_malformed_sections(self):
        for backup in [b'\xff', b'[]', b'{"ssh":null}']:
            self.path.write_bytes(b'[]')
            self.backup.write_bytes(backup)
            result = config.load_config(str(self.path))
            self.assertIsInstance(result['ssh'], dict)

    def test_invalid_save_is_refused_before_touching_primary_or_backup(self):
        config.save_config(self.good, str(self.path))
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            config.save_config({'ssh': None}, str(self.path))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_refused_refresh_keeps_previous_runtime_generation(self):
        runtime = config.RuntimeConfig()
        before = runtime.snapshot()
        with patch.object(config, 'load_config', return_value={'ssh': None}), self.assertRaises(ValueError):
            runtime.refresh()
        self.assertIs(runtime.snapshot(), before)

    def test_two_processes_merge_against_the_locked_current_file(self):
        config.save_config({'appearance': {'theme': 'dark'}}, str(self.path))
        ctx = multiprocessing.get_context('spawn')
        entered_a, entered_b = ctx.Event(), ctx.Event()
        attempted_a, attempted_b = ctx.Event(), ctx.Event()
        release_a, release_b = ctx.Event(), ctx.Event()
        release_b.set()
        a = ctx.Process(target=_config_writer, args=(str(self.path), {'appearance': {'theme': 'light'}}, entered_a, release_a, attempted_a))
        b = ctx.Process(target=_config_writer, args=(str(self.path), {'terminal': {'font_size': 23}}, entered_b, release_b, attempted_b))
        processes = [a, b]
        try:
            a.start()
            self.assertTrue(entered_a.wait(10))
            b.start()
            self.assertTrue(attempted_b.wait(10))
            self.assertFalse(entered_b.wait(0.2), 'Second process read before the first commit')
            release_a.set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
        finally:
            release_a.set()
            for process in processes:
                if process.pid is not None and process.is_alive():
                    process.terminate()
                    process.join(5)
        result = config.load_config(str(self.path))
        self.assertEqual(result['appearance']['theme'], 'light')
        self.assertEqual(result['terminal']['font_size'], 23)
