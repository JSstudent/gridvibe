"""Compatibility shim for the relocated cleanup module."""

import sys

from utils import cleanup as _cleanup

if __name__ == "__main__":
    _cleanup.main()
else:
    sys.modules[__name__] = _cleanup

