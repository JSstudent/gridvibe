"""Flask + Socket.IO application objects for GridVibe.

Extracted from ``web/api.py`` (deep-dive finding 6.2): the Flask app,
the Socket.IO server, the cross-origin write guard, and the shared
``SessionManager`` singleton live here so route/handler modules can import
them without pulling in the whole API surface. ``web.api`` re-exports every
name for backwards compatibility.
"""

import logging
import os
from typing import Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, request
from flask_socketio import SocketIO

from sessions.manager import SessionManager
from web.config import runtime_config
from web.paths import BASE_DIR

logger = logging.getLogger(__name__)


def _resolve_secret_key() -> bytes | str:
    """Return a session signing key without shipping a static public secret."""
    env_secret = os.environ.get("GRIDVIBE_SECRET_KEY") or os.environ.get("SECRET_KEY")
    if env_secret:
        return env_secret

    configured_secret = runtime_config.app_config.get("security", {}).get("secret_key")
    if isinstance(configured_secret, str) and configured_secret.strip():
        return configured_secret

    return os.urandom(32)


def _resolve_cors_origins():
    """Return Socket.IO CORS origins; defaults to same-origin only.

    The Socket.IO channel accepts terminal input, so a wildcard here would let
    any web page in the user's browser reach live shells. An explicit
    ``security.cors_origins`` list in config (including ``["*"]``) still wins,
    e.g. for reverse-proxy setups.
    """
    configured = runtime_config.app_config.get("security", {}).get("cors_origins")
    if configured:
        return configured
    server_config = runtime_config.app_config.get("server", {})
    port = server_config.get("port", 5050)
    host = str(server_config.get("host", "127.0.0.1")).strip()
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if host and host not in {"127.0.0.1", "localhost", "0.0.0.0", "::", "::1"}:
        origins.append(f"http://{host}:{port}")
    return origins


# Create Flask app
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["SECRET_KEY"] = _resolve_secret_key()
app.config['JSON_SORT_KEYS'] = False
# Static assets (terminals.js/css, …) are cache-busted only by ``?v={{ version }}``,
# which is a fixed app version — so an edited JS/CSS file keeps the same URL and a
# long-lived cache (notably the desktop WebView2 profile, which survives restarts)
# serves the stale copy. On a local, single-user tool the revalidation cost over
# localhost is negligible, so force the client to revalidate every load and always
# pick up edits.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins=_resolve_cors_origins(), async_mode="threading")


def _allowed_write_origin_netlocs() -> Optional[set]:
    """Origins allowed to issue state-changing requests; None means allow all."""
    netlocs = set()
    for entry in runtime_config.app_config.get("security", {}).get("cors_origins") or []:
        entry = str(entry).strip()
        if entry == "*":
            return None
        parsed = urlparse(entry if "//" in entry else f"//{entry}")
        if parsed.netloc:
            netlocs.add(parsed.netloc.lower())
    host = request.host.lower()
    netlocs.add(host)
    netlocs.add(host.replace("127.0.0.1", "localhost", 1))
    netlocs.add(host.replace("localhost", "127.0.0.1", 1))
    return netlocs


@app.before_request
def _reject_cross_origin_writes():
    """Reject cross-origin state-changing requests.

    CORS stops a hostile page from *reading* responses, but "simple"
    cross-origin POSTs still execute server-side. The app's own pages send a
    matching Origin header (or none for non-CORS requests and pywebview),
    while a cross-site fetch/form post always carries the attacker's origin.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin", "").strip()
    if not origin:
        return None
    allowed = _allowed_write_origin_netlocs()
    if allowed is None:
        return None
    if origin.lower() == "null" or urlparse(origin).netloc.lower() not in allowed:
        logger.warning(
            "Rejected cross-origin %s %s from Origin %s",
            request.method,
            request.path,
            origin,
        )
        return jsonify({"error": "Cross-origin request rejected"}), 403
    return None


# Initialize session manager
session_manager = SessionManager()
