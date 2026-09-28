"""Test-package bootstrap: isolate the suite from production local state.

Package-based runners (``python tests/run_tests.py``,
``python -m unittest tests.test_api``, pytest) import this package before test
modules. For top-level ``python -m unittest discover -s tests``, the first
discovered file, ``test_000_bootstrap.py``, imports it before other tests.
This redirects process-wide state before ``web`` is imported.

``web.runtime_state`` resolves its file from ``GRIDVIBE_RUNTIME_STATE_PATH`` at
import time and refuses the canonical project-local ``runtime_state.json``
while ``GRIDVIBE_TEST_MODE`` is set. Individual test cases still patch
``RUNTIME_STATE_PATH`` for per-test isolation; this guarantees the default is
never the user's real restore file, and makes a missed patch fail loudly
instead of silently overwriting their saved workspaces.

``web.saved_sessions`` follows the same rule for ``saved_sessions.json``. A
test that exercises the real save route without its own path patch uses a
temporary preset store instead of adding entries to the user's saved sessions.
The config and encryption key are redirected as well, since tests import the
key owner and some exercise the real App Settings route.

``.gridvibe_mcp.json`` is redirected the same way and for a sharper reason: it
is written by ``run_server``, which a test calls with a fabricated host and
port, and it is written with a plain ``open()`` rather than through
``web/state_files.py`` -- so nothing else stands between the suite and the
developer's real config. A run that reached it repointed every agent pane's
sidecar at an address nothing answers, and the damage outlived the run: only
the next GridVibe start rewrites the file.
"""

import atexit
import os
import shutil
import tempfile

os.environ.setdefault("GRIDVIBE_TEST_MODE", "1")

if not os.environ.get("GRIDVIBE_RUNTIME_STATE_PATH"):
    _state_dir = tempfile.mkdtemp(prefix="gridvibe-test-state-")
    os.environ["GRIDVIBE_RUNTIME_STATE_PATH"] = os.path.join(
        _state_dir, "runtime_state.json"
    )
    atexit.register(shutil.rmtree, _state_dir, ignore_errors=True)

if not os.environ.get("GRIDVIBE_SAVED_SESSIONS_PATH"):
    _saved_sessions_dir = tempfile.mkdtemp(prefix="gridvibe-test-sessions-")
    os.environ["GRIDVIBE_SAVED_SESSIONS_PATH"] = os.path.join(
        _saved_sessions_dir, "saved_sessions.json"
    )
    atexit.register(shutil.rmtree, _saved_sessions_dir, ignore_errors=True)

if not os.environ.get("GRIDVIBE_CONFIG_PATH"):
    _config_dir = tempfile.mkdtemp(prefix="gridvibe-test-config-")
    os.environ["GRIDVIBE_CONFIG_PATH"] = os.path.join(_config_dir, "config.json")
    atexit.register(shutil.rmtree, _config_dir, ignore_errors=True)

if not os.environ.get("GRIDVIBE_ENCRYPTION_KEY_PATH"):
    _key_dir = tempfile.mkdtemp(prefix="gridvibe-test-key-")
    os.environ["GRIDVIBE_ENCRYPTION_KEY_PATH"] = os.path.join(
        _key_dir, ".encryption_key"
    )
    atexit.register(shutil.rmtree, _key_dir, ignore_errors=True)

if not os.environ.get("GRIDVIBE_MCP_CONFIG_PATH"):
    _mcp_dir = tempfile.mkdtemp(prefix="gridvibe-test-mcp-")
    os.environ["GRIDVIBE_MCP_CONFIG_PATH"] = os.path.join(
        _mcp_dir, ".gridvibe_mcp.json"
    )
    atexit.register(shutil.rmtree, _mcp_dir, ignore_errors=True)

if not os.environ.get("GRIDVIBE_CLAUDE_SETTINGS_PATH"):
    # The Claude session-hook settings are written by ``run_server`` the same
    # way, with the same consequence for a run that reached the real file.
    _claude_settings_dir = tempfile.mkdtemp(prefix="gridvibe-test-claude-")
    os.environ["GRIDVIBE_CLAUDE_SETTINGS_PATH"] = os.path.join(
        _claude_settings_dir, ".gridvibe_claude_settings.json"
    )
    atexit.register(shutil.rmtree, _claude_settings_dir, ignore_errors=True)

if not os.environ.get("GRIDVIBE_HANDOFF_DIR"):
    # Handed-over task files are written under the system temp directory, and
    # ``run_server`` sweeps that directory at start. A suite that reached the
    # real one would write into, and sweep, a directory another GridVibe on
    # this machine may be using.
    _handoff_dir = tempfile.mkdtemp(prefix="gridvibe-test-handoffs-")
    os.environ["GRIDVIBE_HANDOFF_DIR"] = _handoff_dir
    atexit.register(shutil.rmtree, _handoff_dir, ignore_errors=True)
