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

from gridvibe_version import __version__ as _GRIDVIBE_VERSION
from web.api import load_config, resolve_server_settings, run_server, session_manager

__version__ = _GRIDVIBE_VERSION

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
MAX_LOG_SIZE = 2 * 1024 * 1024  # 2 MB per file
MAX_LOG_BACKUPS = 10

# Matches reconciliation GETs from the terminals page (push-triggered status
# refreshes plus its slow fallback poll) and the voice-status checks fired on
# window focus that would otherwise clutter the log
_POLL_RE = re.compile(
    r'"GET /api/(sessions|session-groups|voice-status)(\?[^ ]*)? HTTP/[\d.]+" 2\d\d'
)


class _SuppressPollLogs(logging.Filter):
    def filter(self, record):
        return not _POLL_RE.search(record.getMessage())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class _StripAnsiFilter(logging.Filter):
    """Strip ANSI colour codes (e.g. werkzeug's coloured status lines) so the
    log file stays grep-friendly. Attached to the file handler only, and the
    console handler emits first, so terminal output keeps its colours."""

    def filter(self, record):
        message = record.getMessage()
        if "\x1b[" in message:
            record.msg = _ANSI_RE.sub("", message)
            record.args = None
        return True


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
    file_handler.addFilter(_StripAnsiFilter())

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
        "--host", default=None, help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port to bind to (default: 5050)"
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable debug mode",
    )
    parser.add_argument(
        "--config", default="config.json", help="Path to configuration file"
    )

    args = parser.parse_args()

    # Load configuration; explicit CLI flags win over config values
    config = load_config(args.config) if os.path.exists(args.config) else {}
    host, port, debug = resolve_server_settings(
        config, host=args.host, port=args.port, debug=args.debug
    )

    # Setup logging
    setup_logging(debug)
    logger = logging.getLogger(__name__)
    if config:
        logger.info(f"Loaded configuration from {args.config}")

    # Print startup banner
    logger.info("=" * 50)
    logger.info("GridVibe - Starting...")
    logger.info("=" * 50)
    logger.info(f"Server running on http://{host}:{port}")
    logger.info(f"Debug mode: {debug}")
    logger.info("=" * 50)

    # Run the server
    try:
        run_server(host, port, debug)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        session_manager.close_all_sessions()
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
