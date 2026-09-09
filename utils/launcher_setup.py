"""Check and record a verified launcher environment; never install on a check."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMPORTS = {
    'core': 'import _cffi_backend, cryptography.fernet, engineio, flask, flask_socketio, paramiko, socketio, markdown, bleach, websocket',
    'desktop': 'import webview',
    'voice': 'import faster_whisper, numpy, vosk, websockets',
}
REQUIREMENTS = {'core': 'requirements.txt', 'desktop': 'requirements-desktop.txt', 'voice': 'requirements-voice.txt'}


def setup_fingerprint(group, project_root=PROJECT_ROOT):
    requirements = hashlib.sha256()
    versions = {}
    visited = set()

    def read_requirements(path):
        path = path.resolve()
        if path in visited:
            return
        visited.add(path)
        content = path.read_bytes()
        requirements.update(content)
        for line in content.decode('utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r '):
                read_requirements(path.parent / line[3:].strip())
                continue
            marker = re.search(r'platform_system\s*(==|!=)\s*[\'"]([^\'"]+)', line)
            if marker:
                equal = platform.system() == marker[2]
                if equal != (marker[1] == '=='):
                    continue
            distribution = re.split(r'[<=>!~;\[\s]', line, maxsplit=1)[0]
            versions[distribution] = importlib.metadata.version(distribution)

    read_requirements(project_root / REQUIREMENTS[group])
    return {
        'requirements': requirements.hexdigest(),
        'interpreter': str(Path(sys.executable).resolve()),
        'prefix': str(Path(sys.prefix).resolve()),
        'base_prefix': str(Path(sys.base_prefix).resolve()),
        'version': sys.version,
        'packages': versions,
        'setup_version': 1,
    }


def imports_work(group):
    command = IMPORTS[group]
    if group == 'core' and sys.platform == 'win32':
        command += '; import winpty'
    if group == 'desktop' and sys.platform.startswith('linux'):
        command += '; import qtpy.QtCore'
    try:
        result = subprocess.run([sys.executable, '-c', command], capture_output=True, timeout=30)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def setup_marker(group, project_root=PROJECT_ROOT):
    return project_root / '.venv' / f'.gridvibe-setup-{group}.json'


def check_setup(group, project_root=PROJECT_ROOT):
    try:
        saved = json.loads(setup_marker(group, project_root).read_text(encoding='utf-8'))
        return saved == setup_fingerprint(group, project_root) and imports_work(group)
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError):
        return False


def record_setup(group, project_root=PROJECT_ROOT):
    if not imports_work(group):
        raise RuntimeError(f'{group.capitalize()} dependencies are not importable; setup was not recorded')
    fingerprint = setup_fingerprint(group, project_root)
    marker = setup_marker(group, project_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=marker.name, suffix='.tmp', dir=marker.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(fingerprint, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'record'])
    parser.add_argument('group', choices=IMPORTS)
    args = parser.parse_args(argv)
    if args.action == 'check':
        return 0 if check_setup(args.group) else 1
    try:
        record_setup(args.group)
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError) as exc:
        print(f'Setup verification failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
