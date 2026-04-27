"""
GridVibe - Main Entry Point

A Python backend for managing multiple SSH terminal sessions
that can be displayed in a web browser.

Usage:
    python main.py                 # Run with default settings
    python main.py --debug         # Run in debug mode
    python main.py --port 8080     # Run on custom port
"""

import argparse
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web.api import app, load_config, session_manager, socketio

__version__ = "0.1.0"


LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
MAX_LOG_SIZE = 2 * 1024 * 1024  # 2 MB per file
MAX_LOG_BACKUPS = 10

# Matches high-frequency polling GETs that clutter the log (e.g. status polls every 3 s)
_POLL_RE = re.compile(r'"GET /api/(sessions|session-groups)(\?[^ ]*)? HTTP/[\d.]+" 2\d\d')


class _SuppressPollLogs(logging.Filter):
    def filter(self, record):
        return not _POLL_RE.search(record.getMessage())


def setup_logging(debug: bool = False):
    """Configure logging for the application, writing to stdout and logs/gridvibe.log.

    The file handler rotates at MAX_LOG_SIZE and keeps MAX_LOG_BACKUPS numbered copies
    (gridvibe.log.1 … .10), overwriting the oldest once the limit is reached.
    High-frequency polling requests to /api/sessions and /api/session-groups are
    suppressed so they don't flood the log.
    """
    level = logging.DEBUG if debug else logging.INFO

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "gridvibe.log")

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=MAX_LOG_SIZE, backupCount=MAX_LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    # Suppress noisy polling GETs from werkzeug across all handlers
    logging.getLogger("werkzeug").addFilter(_SuppressPollLogs())

    logging.getLogger(__name__).info(f"Log file: {log_file}")


def main():
    """Main entry point for GridVibe."""
    parser = argparse.ArgumentParser(
        description="GridVibe - Multi-Session Terminal Manager"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=5050, help="Port to bind to (default: 5050)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument(
        "--config", default="config.json", help="Path to configuration file"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.debug)
    logger = logging.getLogger(__name__)

    # Load configuration
    config = {}
    if os.path.exists(args.config):
        config = load_config(args.config)
        logger.info(f"Loaded configuration from {args.config}")

    # Get settings from config or args
    host = config.get("server", {}).get("host", args.host)
    port = config.get("server", {}).get("port", args.port)
    debug = config.get("server", {}).get("debug", args.debug)

    # Print startup banner
    logger.info("=" * 50)
    logger.info("GridVibe - Starting...")
    logger.info("=" * 50)
    logger.info(f"Server running on http://{host}:{port}")
    logger.info(f"Debug mode: {debug}")
    logger.info("=" * 50)

    # Run the server
    try:
        socketio.run(app, host=host, port=port, debug=debug, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        session_manager.close_all_sessions()
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

