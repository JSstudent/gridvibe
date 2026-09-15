"""MCP over streamable HTTP, for panes whose shell is not on this machine.

The stdio sidecar is a *child of the pane's own shell*, which is why an SSH
pane could never have it: the remote host has no copy of it, no MCP SDK, and
no route back to this machine's loopback. This module removes all three
requirements at once by moving the protocol to where GridVibe already is.

A remote pane's agent is handed a URL instead of a command. Nothing is
installed remotely; the only thing that has to reach this process is TCP,
which arrives through the SSH reverse tunnel ``web/ssh_tunnel.py`` opens for
the session.

**One tool path, not two.** The tools themselves are still
``gridvibe_mcp.server.dispatch`` -- the same synchronous function the stdio
sidecar calls, given the same :class:`~gridvibe_mcp.client.GridVibeClient`
pointed at this server's own loopback address. The extra hop is one local
request and buys the property that matters: a tool cannot behave differently
depending on which transport asked for it. Only ``__main__.py`` imports the
asyncio MCP SDK, so importing the sidecar's *server* half here keeps that SDK
out of the Flask process exactly as before.

**Identity arrives by token, because inheritance cannot reach.** A local pane's
sidecar learns which pane it is from five inherited environment variables. A
remote agent inherits nothing from this machine, so GridVibe mints an opaque
token per pane, bakes it into the URL written on the remote host, and resolves
it back to the same :class:`~gridvibe_mcp.identity.PaneIdentity` the stdio path
builds from the environment. The token dies with the pane.

**This is a deliberate, bounded weakening of the local-bind guarantee, and it
is opt-in per pane.** While a tunnelled pane is open, anything on that remote
host that can reach the forwarded port can spend that pane's token, and the
create-tier tools act on *this* machine. The bounds are: the token names one
pane and is revoked when it closes, it is rejected once its pane is gone, and
it is never written into a saved preset or a snapshot. A reader who does not
tick the box opens no port and mints no token.
"""

import json
import logging
import secrets
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: The protocol revisions this endpoint implements. A client asking for
#: something else is answered with the newest one here rather than refused --
#: the message shapes below have been stable across these, and refusing an
#: unknown-but-newer revision would break a CLI that merely updated.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

SERVER_NAME = "gridvibe"

#: Bytes of entropy behind a pane token. The token is the only thing standing
#: between a process on the remote host and this pane's tools.
_TOKEN_BYTES = 32

#: JSON-RPC error codes, from the spec.
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


class PaneTokenRegistry:
    """Opaque token -> the pane it speaks for.

    In memory only, like the sessions it names: a token that outlived a
    restart would name a pane that no longer exists, and the pane it was
    minted for is gone in the same breath.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens: Dict[str, Dict[str, Any]] = {}
        self._by_session: Dict[str, str] = {}

    def mint(
        self,
        *,
        session_id: str,
        group_id: str = "",
        workspace_id: str = "",
        agent_depth: int = 0,
    ) -> str:
        """Return this pane's token, minting one if it has none.

        Idempotent per pane: a relaunch that re-registers the same pane keeps
        the token already written into the remote config, so the file on the
        remote host does not have to be rewritten to stay true.
        """
        resolved_session = str(session_id or "").strip()
        if not resolved_session:
            return ""
        with self._lock:
            existing = self._by_session.get(resolved_session)
            if existing:
                record = self._tokens.get(existing)
                if record is not None:
                    record.update(
                        group_id=str(group_id or ""),
                        workspace_id=str(workspace_id or ""),
                        agent_depth=max(0, int(agent_depth or 0)),
                    )
                    return existing
            token = secrets.token_urlsafe(_TOKEN_BYTES)
            self._tokens[token] = {
                "session_id": resolved_session,
                "group_id": str(group_id or ""),
                "workspace_id": str(workspace_id or ""),
                "agent_depth": max(0, int(agent_depth or 0)),
                "minted_at": time.time(),
            }
            self._by_session[resolved_session] = token
            return token

    def resolve(self, token: str) -> Dict[str, Any]:
        """Return the pane record behind one token, or ``{}``."""
        resolved = str(token or "").strip()
        if not resolved:
            return {}
        with self._lock:
            record = self._tokens.get(resolved)
            return dict(record) if record else {}

    def revoke(self, session_id: str) -> bool:
        """Drop the token for one pane. Returns whether there was one."""
        resolved = str(session_id or "").strip()
        with self._lock:
            token = self._by_session.pop(resolved, "")
            if not token:
                return False
            self._tokens.pop(token, None)
            return True

    def token_for(self, session_id: str) -> str:
        """The token already minted for one pane, or ""."""
        with self._lock:
            return self._by_session.get(str(session_id or "").strip(), "")

    def clear(self) -> None:
        with self._lock:
            self._tokens.clear()
            self._by_session.clear()


#: The one registry. Read by the endpoint, written by the SSH pane spawn.
pane_tokens = PaneTokenRegistry()


def _identity_for(record: Dict[str, Any], base_url: str):
    """Build the same PaneIdentity the stdio path reads from the environment."""
    from gridvibe_mcp.identity import PaneIdentity

    return PaneIdentity(
        url=base_url,
        session_id=str(record.get("session_id") or ""),
        group_id=str(record.get("group_id") or ""),
        workspace_id=str(record.get("workspace_id") or ""),
        agent_depth=max(0, int(record.get("agent_depth") or 0)),
        # Stated, always: a tunnelled pane's depth came from the pane record
        # rather than from nothing, so the budget applies exactly as it does
        # to a local pane and `whoami` does not report a hand-started agent.
        depth_stated=True,
    )


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result(request_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _negotiated_version(requested: Any) -> str:
    asked = str(requested or "").strip()
    return asked if asked in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION


def handle_message(
    message: Any,
    *,
    record: Dict[str, Any],
    base_url: str,
    max_agent_depth: int,
    version: str = "",
) -> Optional[Dict[str, Any]]:
    """Answer one JSON-RPC message, or ``None`` for a notification.

    Notifications carry no ``id`` and get no reply -- the caller turns that
    into ``202 Accepted`` rather than an empty JSON body, which is what the
    transport asks for.
    """
    if not isinstance(message, dict):
        return _error(None, _INVALID_REQUEST, "A JSON-RPC message must be an object.")

    method = str(message.get("method") or "")
    request_id = message.get("id")
    is_notification = "id" not in message

    if is_notification:
        # `notifications/initialized` and friends: acknowledged by accepting
        # them. Nothing here keeps per-connection state that a notification
        # would advance.
        return None

    if method == "initialize":
        params = message.get("params") or {}
        negotiated = _negotiated_version(
            params.get("protocolVersion") if isinstance(params, dict) else ""
        )
        from gridvibe_version import __version__ as gridvibe_version

        return _result(
            request_id,
            {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": str(gridvibe_version)},
            },
        )

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        from gridvibe_mcp.server import tool_specs

        return _result(request_id, {"tools": tool_specs()})

    if method == "tools/call":
        from gridvibe_mcp.client import GridVibeClient
        from gridvibe_mcp.server import dispatch

        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, _INVALID_REQUEST, "params must be an object.")
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        client = GridVibeClient(base_url)
        try:
            payload = dispatch(
                name,
                arguments,
                client=client,
                identity=_identity_for(record, base_url),
                max_agent_depth=max_agent_depth,
            )
        except Exception:  # pragma: no cover - dispatch answers its own refusals
            logger.exception("MCP tool %s failed over HTTP", name)
            return _error(request_id, _INTERNAL_ERROR, "The tool could not be run.")
        # `dispatch` never raises for an ordinary refusal; it returns an
        # `{"error": ...}` payload. That travels as tool content with
        # `isError`, not as a protocol error -- the agent should read
        # GridVibe's own sentence, the same one the stdio path shows.
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                "isError": bool(isinstance(payload, dict) and payload.get("error")),
            },
        )

    return _error(request_id, _METHOD_NOT_FOUND, f"Unknown method '{method}'.")


def handle_request(
    token: str,
    raw_body: bytes,
    *,
    base_url: str,
    max_agent_depth: int,
) -> Tuple[Any, int]:
    """Answer one POST. Returns ``(payload, status)``; ``None`` means no body.

    A batch is answered with a batch, minus the notifications in it, which is
    what the JSON-RPC spec asks for and what a client that sends one expects.
    """
    record = pane_tokens.resolve(token)
    if not record:
        # Deliberately the same answer for "never existed" and "its pane has
        # closed": the caller learns nothing about which tokens are live.
        return {"error": "Unknown or expired GridVibe MCP token"}, 404

    try:
        message = json.loads(raw_body.decode("utf-8")) if raw_body else None
    except (ValueError, UnicodeDecodeError):
        return _error(None, _PARSE_ERROR, "Request body is not valid JSON."), 400

    if isinstance(message, list):
        replies = [
            reply
            for item in message
            if (
                reply := handle_message(
                    item,
                    record=record,
                    base_url=base_url,
                    max_agent_depth=max_agent_depth,
                )
            )
            is not None
        ]
        return (replies, 200) if replies else (None, 202)

    reply = handle_message(
        message,
        record=record,
        base_url=base_url,
        max_agent_depth=max_agent_depth,
    )
    return (reply, 200) if reply is not None else (None, 202)
