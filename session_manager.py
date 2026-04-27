"""Compatibility shim for the relocated session manager module."""

import sys

from sessions import manager as _manager

if __name__ == "__main__":
    raise SystemExit("session_manager.py is a library module and is not executable.")
else:
    sys.modules[__name__] = _manager

