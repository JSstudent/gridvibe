import ctypes
import errno
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from web import explorer
from web.rename_noreplace import rename_noreplace


class RenameNoReplaceTestCase(unittest.TestCase):
    def test_racing_destination_survives_directory_and_hardlink_fallback(self):
        for directory in [True, False]:
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as tmp:
                source, destination = Path(tmp, 'source'), Path(tmp, 'destination')
                if directory:
                    source.mkdir()
                    (source / 'owned.txt').write_text('source')
                else:
                    source.write_text('source')

                def exclusive(old, new):
                    # Another program wins after GridVibe's last path check.
                    if directory:
                        destination.mkdir()
                        (destination / 'external.txt').write_text('external')
                    else:
                        destination.write_text('external')
                    raise FileExistsError(errno.EEXIST, 'Destination exists', new)

                backend = explorer._LocalExplorerBackend(SimpleNamespace())
                with patch.object(explorer.os, 'name', 'posix'), \
                        patch.object(explorer.os, 'link', side_effect=OSError(errno.EPERM, 'no hardlinks')), \
                        patch.object(explorer, 'rename_noreplace', side_effect=exclusive), \
                        patch.object(explorer.os, 'rename') as overwrite:
                    with self.assertRaises(FileExistsError):
                        backend.fs_rename_noreplace(str(source), str(destination))
                overwrite.assert_not_called()
                self.assertEqual((source / 'owned.txt' if directory else source).read_text(), 'source')
                self.assertEqual((destination / 'external.txt' if directory else destination).read_text(), 'external')

    def test_native_flags_and_errno_are_preserved(self):
        for platform, symbol, expected_flags in [('linux', 'renameat2', 1), ('darwin', 'renamex_np', 4)]:
            with self.subTest(platform=platform):
                function = MagicMock(return_value=-1)
                library = SimpleNamespace(**{symbol: function})
                with patch('web.rename_noreplace.sys.platform', platform), \
                        patch('web.rename_noreplace.ctypes.CDLL', return_value=library), \
                        patch('web.rename_noreplace.ctypes.get_errno', return_value=errno.EEXIST):
                    with self.assertRaises(FileExistsError):
                        rename_noreplace('/source', '/destination')
                self.assertEqual(function.call_args.args[-1], expected_flags)
                self.assertIs(function.restype, ctypes.c_int)

    def test_unsupported_platform_refuses_without_overwriting(self):
        with patch('web.rename_noreplace.sys.platform', 'unsupported'), \
                patch('web.rename_noreplace.ctypes.CDLL', return_value=SimpleNamespace()):
            with self.assertRaisesRegex(OSError, 'cannot safely rename'):
                rename_noreplace('/source', '/destination')

    @unittest.skipUnless(sys.platform.startswith('linux') or sys.platform == 'darwin', 'POSIX native rename')
    def test_real_exclusive_rename_and_collision_for_files_and_directories(self):
        for directory in [True, False]:
            with tempfile.TemporaryDirectory() as tmp:
                source, destination = Path(tmp, 'source'), Path(tmp, 'destination')
                if directory:
                    source.mkdir()
                    destination.mkdir()
                else:
                    source.write_text('source')
                    destination.write_text('external')
                with self.assertRaises(FileExistsError):
                    rename_noreplace(str(source), str(destination))
                self.assertTrue(source.exists() and destination.exists())
                if directory:
                    destination.rmdir()
                else:
                    destination.unlink()
                rename_noreplace(str(source), str(destination))
                self.assertFalse(source.exists())
                self.assertTrue(destination.exists())
