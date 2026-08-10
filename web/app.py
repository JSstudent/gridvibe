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


# Bind addresses that add nothing to the derived origin list: loopback is already
# spelled both ways, and a wildcard names no single reachable host at all. The
# request-host fallback in ``SameOriginPolicy`` is what covers the wildcard case.
_UNROUTABLE_BIND_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::", "::1"}


def _configured_cors_origins():
    """Return an explicit ``security.cors_origins`` list, or a falsy value."""
    return runtime_config.app_config.get("security", {}).get("cors_origins")


def _resolve_cors_origins(host=None, port=None):
    """Return Socket.IO CORS origins; defaults to same-origin only.

    The Socket.IO channel accepts terminal input, so a wildcard here would let
    any web page in the user's browser reach live shells. An explicit
    ``security.cors_origins`` list in config (including ``["*"]``) still wins,
    e.g. for reverse-proxy setups.

    ``host``/``port`` carry the *resolved* bind settings from an entry point,
    where an explicit CLI flag beats config (guardrail 4). They fall back to the
    config values, which is all a bare import can know.
    """
    configured = _configured_cors_origins()
    if configured:
        return list(configured)
    server_config = runtime_config.app_config.get("server", {})
    if not isinstance(server_config, dict):
        server_config = {}
    if port is None:
        port = server_config.get("port", 5050)
    if host is None:
        host = server_config.get("host", "127.0.0.1")
    host = str(host).strip()
    origins = [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
    if host and host not in _UNROUTABLE_BIND_HOSTS:
        origins.append(f"http://{host}:{port}")
    return origins


def _request_origin(environ) -> Optional[str]:
    """Return the origin the request was actually addressed to, or None."""
    if not environ or "wsgi.url_scheme" not in environ or "HTTP_HOST" not in environ:
        return None
    scheme = environ.get(
        "HTTP_X_FORWARDED_PROTO", environ["wsgi.url_scheme"]
    ).split(",")[0].strip()
    host = environ.get(
        "HTTP_X_FORWARDED_HOST", environ["HTTP_HOST"]
    ).split(",")[0].strip()
    if not scheme or not host:
        return None
    return f"{scheme}://{host}"


class SameOriginPolicy:
    """Socket.IO origin check that also trusts the request's own host.

    engine.io calls this with ``(origin, environ)``. It is the Socket.IO
    counterpart of ``_allowed_write_origin_netlocs``, which already folds
    ``request.host`` into the allowed set — without that, a wildcard bind
    (``--host 0.0.0.0``) browsed by LAN IP authorises origins nobody uses, and
    the transport dies while HTTP writes keep working. Only installed when
    ``security.cors_origins`` is unset; an explicit list is honoured verbatim.
    """

    def __init__(self, origins):
        self.origins = tuple(origins)

    def __call__(self, origin, environ=None) -> bool:
        if not origin:
            return False
        if origin in self.origins:
            return True
        return origin == _request_origin(environ)


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

# Initialize SocketIO. The origins derived here come from config alone; the entry
# points re-point them at the resolved bind settings via
# ``apply_resolved_server_origins`` before serving.
socketio = SocketIO(app, cors_allowed_origins=_resolve_cors_origins(), async_mode="threading")


def apply_resolved_server_origins(host, port):
    """Re-point the Socket.IO origin check at the resolved bind settings.

    ``socketio`` is built at import time from config values alone, but the entry
    points resolve host/port with CLI flags winning. Left unreconciled the server
    binds one port and authorises origins for another: the polling handshake
    still succeeds (browsers omit ``Origin`` on a same-origin GET) while the
    WebSocket upgrade and every data POST are rejected, so panes connect and then
    carry no terminal traffic. Returns the resolved allowlist.
    """
    configured = _configured_cors_origins()
    origins = _resolve_cors_origins(host, port)
    eio = getattr(getattr(socketio, "server", None), "eio", None)
    if eio is None:  # pragma: no cover - defensive; init_app always builds one
        logger.warning(
            "Socket.IO server unavailable; allowed origins left at import-time defaults"
        )
        return origins
    eio.cors_allowed_origins = list(origins) if configured else SameOriginPolicy(origins)
    logger.info(
        "Socket.IO allowed origins: %s%s",
        ", ".join(str(entry) for entry in origins),
        "" if configured else " (plus the request's own origin)",
    )
    if any(str(entry).strip() == "*" for entry in configured or []):
        # Not refused — a reverse proxy is a legitimate reason to widen this —
        # but named at startup, because the value is easy to add while debugging
        # and easy to forget, and it is the only thing standing between a page
        # the user happens to visit and their live shells (audit F4).
        logger.warning(
            "security.cors_origins is [\"*\"]: any web page can reach Socket.IO "
            "and write to terminals, and the cross-origin write guard is off. "
            "Name the proxy origin explicitly, or clear the setting to derive "
            "same-origin."
        )
    return origins


def _allowed_write_origin_netlocs() -> Optional[set]:
    """Origins allowed to issue state-changing requests; None means allow all."""
    netlocs = set()
    for entry in _configured_cors_origins() or []:
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
