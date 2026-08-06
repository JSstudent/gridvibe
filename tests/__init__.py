"""Test-package bootstrap: isolate the suite from production local state.

Importing any test module imports this package first, whichever runner is used
(``python tests/run_tests.py``, ``python -m unittest tests.test_api``, pytest),
so it is the one place that can redirect process-wide state before ``web`` is
imported.

``web.runtime_state`` resolves its file from ``GRIDVIBE_RUNTIME_STATE_PATH`` at
import time and refuses the canonical project-local ``runtime_state.json``
while ``GRIDVIBE_TEST_MODE`` is set. Individual test cases still patch
``RUNTIME_STATE_PATH`` for per-test isolation; this guarantees the default is
never the user's real restore file, and makes a missed patch fail loudly
instead of silently overwriting their saved workspaces.
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
