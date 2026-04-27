"""Compatibility shim for the relocated web API module."""

import logging
import sys

from web import api as _api

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    _api.run_server(debug=True)
else:
    sys.modules[__name__] = _api

