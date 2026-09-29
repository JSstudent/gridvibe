"""Load test isolation before top-level unittest discovery imports other tests.

``unittest discover -s tests`` imports matching files as top-level modules,
without importing ``tests/__init__.py`` first. Discovery sorts filenames, so
this module runs before any other ``test_*.py`` file and loads the bootstrap.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - set isolated paths before other test imports
