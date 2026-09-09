import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import launcher_setup as setup

ROOT = Path(__file__).resolve().parent.parent
SH = shutil.which('sh') or ('C:/Program Files/Git/bin/sh.exe' if Path('C:/Program Files/Git/bin/sh.exe').exists() else None)


class LauncherSetupTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'requirements.txt').write_text('Example>=1\n')
        (self.root / 'requirements-desktop.txt').write_text('-r requirements.txt\nDesktop[qt]>=2; platform_system == "Linux"\nDesktop>=2; platform_system != "Linux"\n')
        (self.root / 'requirements-voice.txt').write_text('Voice>=3\n')
        for context in [patch.object(setup.importlib.metadata, 'version', return_value='1.0'), patch.object(setup, 'imports_work', return_value=True)]:
            context.start()
            self.addCleanup(context.stop)

    def test_warm_environment_checks_without_any_installation(self):
        self.assertFalse(setup.check_setup('core', self.root))
        setup.record_setup('core', self.root)
        with patch.object(setup.subprocess, 'run') as process:
            self.assertTrue(setup.check_setup('core', self.root))
        process.assert_not_called()
        setup.imports_work.assert_called_with('core')

    def test_requirements_packages_and_interpreter_changes_invalidate_marker(self):
        setup.record_setup('core', self.root)
        with patch.object(setup.sys, 'executable', str(self.root / 'moved-python')):
            self.assertFalse(setup.check_setup('core', self.root))
        with patch.object(setup.importlib.metadata, 'version', return_value='2.0'):
            self.assertFalse(setup.check_setup('core', self.root))
        (self.root / 'requirements.txt').write_text('Example>=2\n')
        self.assertFalse(setup.check_setup('core', self.root))

    def test_desktop_tracks_included_core_requirements_and_voice_is_independent(self):
        setup.record_setup('desktop', self.root)
        setup.record_setup('voice', self.root)
        self.assertTrue(setup.check_setup('desktop', self.root))
        (self.root / 'requirements.txt').write_text('Example>=2\n')
        self.assertFalse(setup.check_setup('desktop', self.root))
        self.assertTrue(setup.check_setup('voice', self.root))

    def test_failed_import_check_never_records_a_marker(self):
        setup.imports_work.return_value = False
        with self.assertRaises(RuntimeError):
            setup.record_setup('core', self.root)
        self.assertFalse(setup.setup_marker('core', self.root).exists())

    def test_corrupt_marker_and_broken_environment_require_setup(self):
        setup.record_setup('core', self.root)
        setup.imports_work.return_value = False
        self.assertFalse(setup.check_setup('core', self.root))
        setup.imports_work.return_value = True
        setup.setup_marker('core', self.root).write_bytes(b'\xff')
        self.assertFalse(setup.check_setup('core', self.root))


@unittest.skipUnless(SH, 'POSIX shell required')
class PosixLauncherTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.launcher = self.root / 'GridVibe.sh'
        self.launcher.write_bytes((ROOT / 'GridVibe.sh').read_bytes())
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls.txt'
        python = self.bin / 'python3'
        python.write_bytes(b'''#!/bin/sh
printf '%s\\n' "$*" >> "$GRIDVIBE_TEST_CALLS"
if [ "$1" = '-m' ] && [ "$2" = 'venv' ]; then
    mkdir .venv
    mkdir .venv/bin
    cp "$0" .venv/bin/python
    chmod +x .venv/bin/python
elif [ "$1" = '-m' ] && [ "$2" = 'pip' ]; then
    [ "${FAKE_PIP_FAIL:-0}" = 0 ] || exit 1
elif [ "${2:-}" = 'check' ]; then
    [ -f "$GRIDVIBE_TEST_ROOT/setup-$3" ] || exit 1
elif [ "${2:-}" = 'record' ]; then
    touch "$GRIDVIBE_TEST_ROOT/setup-$3"
fi
exit 0
''')
        python.chmod(0o755)

    @staticmethod
    def shell_path(path):
        value = str(path).replace('\\', '/')
        if sys.platform == 'win32':
            return '/' + value[0].lower() + value[2:]
        return value

    def run_launcher(self, answer, option='', **env):
        self.calls.write_text('')
        result = subprocess.run(
            [SH, '-c', 'PATH="$1:$PATH"; export PATH; exec sh "$2" ${3:+"$3"}',
             'launcher-test', self.shell_path(self.bin), self.shell_path(self.launcher), option],
            input=answer.encode(), capture_output=True, timeout=10,
            env={**os.environ, 'GRIDVIBE_TEST_CALLS': self.shell_path(self.calls), 'GRIDVIBE_TEST_ROOT': self.shell_path(self.root), **env},
        )
        return result, self.calls.read_text()

    def test_quit_aliases_and_eof_do_no_setup_or_launch(self):
        for answer in ['3\n', 'q\n', 'Q\n', 'quit\n', 'Quit\n', '']:
            with self.subTest(answer=answer):
                result, calls = self.run_launcher(answer)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, '')

    def test_cold_warm_and_explicit_repair(self):
        result, cold = self.run_launcher('2\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('-m venv', cold)
        self.assertIn('-m pip install', cold)
        self.assertIn('record core', cold)
        self.assertIn('--mode browser', cold)
        result, warm = self.run_launcher('b\n', FAKE_PIP_FAIL='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('-m pip', warm)
        self.assertIn('--mode browser', warm)
        result, repair = self.run_launcher('Browser\n', '--repair')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('-m pip install', repair)

    def test_native_invalid_then_valid_and_optional_setup(self):
        result, calls = self.run_launcher('invalid\n1\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('requirements-desktop.txt', calls)
        self.assertIn('record desktop', calls)
        self.assertIn('--mode native', calls)
        result, calls = self.run_launcher('Native\n', FAKE_PIP_FAIL='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('-m pip', calls)

    def test_failed_install_neither_records_nor_launches(self):
        result, calls = self.run_launcher('2\n', FAKE_PIP_FAIL='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('record core', calls)
        self.assertNotIn('--mode', calls)


@unittest.skipUnless(sys.platform == 'win32', 'Windows command processor required')
class WindowsLauncherTestCase(unittest.TestCase):
    """Execute the batch control flow with inert installers and launch handoff."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'launch project'
        self.root.mkdir()
        source = (ROOT / 'GridVibe.bat').read_text(encoding='utf-8')
        start = source.index('set "BOOTSTRAP_PYTHON="')
        end = source.index('echo  Using bootstrap interpreter:', start)
        source = source[:start] + 'set BOOTSTRAP_PYTHON="%PROJECT_DIR%\\fake-python.cmd"\n' + source[end:]
        source = source.replace('set "VENV_PYTHON=%PROJECT_DIR%\\.venv\\Scripts\\python.exe"',
                                'set "VENV_PYTHON=%PROJECT_DIR%\\fake-python.cmd"')
        source = re.sub(r'(?m)^(\s*)"%VENV_PYTHON%"', r'\1call "%VENV_PYTHON%"', source)
        source = source.replace('if defined DESKTOP_SETUP_OK "%VENV_PYTHON%"',
                                'if defined DESKTOP_SETUP_OK call "%VENV_PYTHON%"')
        source = source.replace('".venv\\Scripts\\python.exe" --version',
                                'call "%PROJECT_DIR%\\fake-python.cmd" --version')
        source = re.sub(r'(?m)^choice /C DBQ.*$', 'cmd /c exit %GRIDVIBE_TEST_CHOICE%', source)
        source = re.sub(r'(?m)^choice /C YN.*$', 'cmd /c exit 1', source)
        source = re.sub(r'(?m)^start "GridVibe".*$', 'call "%PROJECT_DIR%\\\\fake-python.cmd" launch %LAUNCH_MODE%', source)
        source = source.replace('pause >nul', 'rem Test does not wait for a key')
        (self.root / 'GridVibe.bat').write_text(source, encoding='utf-8')
        (self.root / 'fake-python.cmd').write_text(r'''@echo off
>>calls.txt echo %*
if "%~1"=="--version" exit /b 0
if "%~1"=="-m" if "%~2"=="venv" (
    mkdir .venv\Scripts
    type nul >.venv\Scripts\python.exe
    exit /b 0
)
if "%~1"=="-m" if "%~2"=="pip" exit /b %GRIDVIBE_TEST_PIP_STATUS%
if "%~2"=="check" (
    if exist "setup-%~3" exit /b 0
    exit /b 1
)
if "%~2"=="record" (
    type nul >"setup-%~3"
    exit /b 0
)
echo %* | findstr /C:"runtime_config.voice_enabled" >nul
if not errorlevel 1 exit /b %GRIDVIBE_TEST_VOICE_STATUS%
exit /b 0
''', encoding='utf-8')

    def run_launcher(self, choice='2', repair=False, pip_status='0', voice_status='1'):
        calls = self.root / 'calls.txt'
        calls.write_text('')
        command = 'GridVibe.bat' + (' --repair' if repair else '')
        result = subprocess.run(['cmd.exe', '/d', '/c', command], cwd=self.root,
                                capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'GRIDVIBE_TEST_CHOICE': choice,
                                     'GRIDVIBE_TEST_PIP_STATUS': pip_status,
                                     'GRIDVIBE_TEST_VOICE_STATUS': voice_status})
        return result, calls.read_text()

    def test_quit_and_failed_install_never_launch_or_record(self):
        result, calls = self.run_launcher(choice='3')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, '')
        result, calls = self.run_launcher(pip_status='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('-m pip', calls)
        self.assertNotIn('record core', calls)
        self.assertFalse(any(line.startswith('launch ') for line in calls.splitlines()))

    def test_browser_cold_warm_offline_and_repair(self):
        result, cold = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('-m venv', cold)
        self.assertIn('record core', cold)
        self.assertNotIn('record desktop', cold)
        self.assertIn('launch browser', cold)
        result, warm = self.run_launcher(pip_status='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('-m pip', warm)
        self.assertIn('launch browser', warm)
        result, repaired = self.run_launcher(repair=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('-m pip', repaired)
        self.assertIn('record core', repaired)

    def test_desktop_and_voice_setup_are_reused(self):
        result, cold = self.run_launcher(choice='1', voice_status='0')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('record desktop', cold)
        self.assertIn('record voice', cold)
        self.assertIn('launch auto', cold)
        result, warm = self.run_launcher(choice='1', voice_status='0', pip_status='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('-m pip', warm)
        self.assertIn('launch auto', warm)
