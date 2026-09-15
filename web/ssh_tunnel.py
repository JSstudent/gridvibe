"""The route home that an SSH pane's agent would otherwise not have.

GridVibe binds loopback, which is the whole reason a remote pane could never
have MCP: the remote host has no address for ``127.0.0.1:5050`` on *this*
machine. An SSH **reverse** forward is the one channel that already exists
between the two -- the same transport the pane's shell is running on -- so it
carries the MCP endpoint rather than opening anything new to the network.

``sshd`` is asked to listen on the *remote* host's own loopback and hand every
connection back down the existing transport. With ``GatewayPorts no`` (the
default) sshd binds loopback whatever address is requested, so the forwarded
port is reachable only from the remote host itself, never from its network.

**What this widens, stated plainly.** While a tunnelled pane is open, any
process on that remote host that can reach the forwarded port can speak to
GridVibe's MCP endpoint, and the create-tier tools act on *this* machine. Two
things bound it: the port exists only for the life of that pane's connection,
and the URL carries a per-pane token (`web/mcp_http.py`) that is refused the
moment the pane closes. A reader who does not tick MCP opens no port at all.
"""

import json
import logging
import select
import socket
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: The address sshd is asked to bind on the remote host. Loopback on purpose:
#: the forwarded port is for the pane's own agent, not for the remote network.
REMOTE_BIND_ADDRESS = "127.0.0.1"

#: Seconds a forwarded channel may wait on its local half before being torn
#: down. The local half is GridVibe's own HTTP port, so slow means gone.
LOCAL_CONNECT_TIMEOUT = 10.0

#: Bytes moved per pump iteration.
_CHUNK = 32768


def _pump(channel: Any, sock: socket.socket) -> None:
    """Move bytes both ways until either side closes.

    One loop over ``select`` rather than a thread per direction: this already
    has a thread of its own (see :func:`_forward_handler`), and a second one
    per direction doubles the teardown paths for no gain.
    """
    try:
        while True:
            readable, _, errored = select.select([channel, sock], [], [channel, sock], 1.0)
            if errored:
                break
            if channel in readable:
                data = channel.recv(_CHUNK)
                if not data:
                    break
                sock.sendall(data)
            if sock in readable:
                data = sock.recv(_CHUNK)
                if not data:
                    break
                channel.sendall(data)
    except (OSError, socket.error, EOFError):
        # An ordinary end of either half. The finally below is the only
        # cleanup that matters.
        pass
    except Exception:  # pragma: no cover - defensive; this runs on its own thread
        logger.exception("MCP tunnel pump failed")
    finally:
        for closeable in (channel, sock):
            try:
                closeable.close()
            except Exception:
                pass


def _serve_forwarded_channel(
    channel: Any, local_host: str, local_port: int
) -> None:
    """Connect the local half and move bytes until one side closes."""
    try:
        sock = socket.create_connection(
            (local_host, local_port), timeout=LOCAL_CONNECT_TIMEOUT
        )
    except (OSError, socket.error) as exc:
        logger.warning(
            "MCP tunnel could not reach GridVibe at %s:%s: %s",
            local_host,
            local_port,
            exc,
        )
        try:
            channel.close()
        except Exception:
            pass
        return
    # Blocking on both halves; the `select` in `_pump` is what makes that safe.
    sock.settimeout(None)
    channel.settimeout(None)
    _pump(channel, sock)


def _forward_handler(local_host: str, local_port: int):
    """Build the per-channel handler paramiko calls for each connection.

    **It must return immediately.** Paramiko invokes this from
    ``Transport._parse_channel_open`` -- on the transport's own packet-reading
    thread, with no thread of its own -- so anything that blocks here stops
    that transport processing any further SSH packet. That thread also carries
    the pane's *shell*: pumping inline froze the terminal (no output, no
    accepted input), starved the very bytes the tunnel was opened to carry, and
    left ``cancel_port_forward`` waiting on a reply the blocked thread could
    never read. One thread per forwarded connection, handed off at once.
    """

    def handler(channel: Any, origin: Any, server: Any) -> None:
        worker = threading.Thread(
            target=_serve_forwarded_channel,
            args=(channel, local_host, local_port),
            name="gridvibe-mcp-tunnel",
            daemon=True,
        )
        worker.start()

    return handler


def open_reverse_tunnel(
    transport: Any,
    *,
    local_host: str,
    local_port: int,
) -> int:
    """Ask the remote sshd to listen and forward back here. Returns its port.

    ``0`` means the request failed, which costs the pane its tools and nothing
    else -- the shell, the agent and every other pane are untouched, so this
    reports rather than raises.
    """
    if transport is None:
        return 0
    try:
        remote_port = transport.request_port_forward(
            REMOTE_BIND_ADDRESS,
            0,
            handler=_forward_handler(local_host, local_port),
        )
    except Exception as exc:
        logger.warning("Could not open the MCP reverse tunnel: %s", exc)
        return 0
    resolved = int(remote_port or 0)
    if resolved:
        logger.info(
            "MCP reverse tunnel open: remote %s:%d -> %s:%d",
            REMOTE_BIND_ADDRESS,
            resolved,
            local_host,
            local_port,
        )
    return resolved


def close_reverse_tunnel(transport: Any, remote_port: int) -> None:
    """Withdraw the remote listener. Safe to call on a transport already gone."""
    if transport is None or not remote_port:
        return
    try:
        transport.cancel_port_forward(REMOTE_BIND_ADDRESS, int(remote_port))
    except Exception as exc:
        logger.debug("MCP reverse tunnel teardown reported %s", exc)


def tunnel_url(remote_port: int, token: str) -> str:
    """The URL the remote agent is given, as seen from the remote host."""
    if not remote_port or not token:
        return ""
    return f"http://{REMOTE_BIND_ADDRESS}:{int(remote_port)}/mcp/{token}"


def remote_mcp_document(url: str) -> Dict[str, Any]:
    """The config written on the remote host.

    Streamable HTTP rather than a stdio command, which is the point: nothing
    is installed on the remote host, so there is no command for it to name.
    Both ``type`` and ``transport`` are stated because the CLIs disagree about
    which key they read, and an extra key is ignored by the one that does not.
    """
    return {
        "mcpServers": {
            "gridvibe": {
                "type": "http",
                "transport": "http",
                "url": url,
            }
        }
    }


def write_remote_config(
    sftp: Any,
    remote_path: str,
    url: str,
) -> bool:
    """Place the generated config on the remote host. Returns success.

    Written through the SFTP channel of the pane's own connection, so it needs
    no second authentication and lands as the same user the pane runs as.
    """
    if not sftp or not remote_path or not url:
        return False
    try:
        with sftp.open(remote_path, "w") as handle:
            handle.write(json.dumps(remote_mcp_document(url), indent=2) + "\n")
        try:
            # The token is a credential for this pane's tools; nobody else on
            # a shared host needs to read it.
            sftp.chmod(remote_path, 0o600)
        except Exception:
            logger.debug("Could not restrict permissions on %s", remote_path)
        return True
    except Exception as exc:
        logger.warning("Could not write the remote MCP config %s: %s", remote_path, exc)
        return False


def remove_remote_config(sftp: Any, remote_path: str) -> None:
    """Delete the config again. A leftover file names a port that is gone."""
    if not sftp or not remote_path:
        return
    try:
        sftp.remove(remote_path)
    except Exception as exc:
        logger.debug("Could not remove the remote MCP config %s: %s", remote_path, exc)


def remote_config_path(home: str, session_id: str) -> str:
    """Where the config lives on the remote host.

    Under the user's home and named for the pane, because one remote host can
    hold panes from several GridVibe windows at once and each carries its own
    token and its own port.
    """
    base = str(home or "").rstrip("/") or "."
    safe = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")
    if not safe:
        return ""
    return f"{base}/.gridvibe/mcp-{safe}.json"


def ensure_remote_directory(sftp: Any, remote_path: str) -> bool:
    """Create the parent directory of the remote config if it is missing."""
    parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
    if not parent:
        return True
    try:
        sftp.stat(parent)
        return True
    except Exception:
        pass
    try:
        sftp.mkdir(parent, 0o700)
        return True
    except Exception as exc:
        logger.warning("Could not create %s on the remote host: %s", parent, exc)
        return False


def resolve_remote_home(sftp: Any) -> str:
    """The remote user's home directory, or "" when it cannot be read."""
    try:
        return str(sftp.normalize(".") or "")
    except Exception as exc:
        logger.debug("Could not resolve the remote home directory: %s", exc)
        return ""


def establish(
    client: Any,
    *,
    session_id: str,
    token: str,
    local_host: str,
    local_port: int,
) -> Optional[Dict[str, Any]]:
    """Open the tunnel and place the config. Returns the record, or ``None``.

    Every failure costs the pane its tools and nothing else: the shell has
    already been opened by the caller and is never torn down for this.
    """
    transport = None
    try:
        transport = client.get_transport()
    except Exception:
        transport = None
    if transport is None:
        return None

    remote_port = open_reverse_tunnel(
        transport, local_host=local_host, local_port=local_port
    )
    if not remote_port:
        return None

    url = tunnel_url(remote_port, token)
    sftp = None
    try:
        sftp = client.open_sftp()
        remote_path = remote_config_path(resolve_remote_home(sftp), session_id)
        if not remote_path or not ensure_remote_directory(sftp, remote_path):
            raise OSError("no writable location for the remote MCP config")
        if not write_remote_config(sftp, remote_path, url):
            raise OSError("the remote MCP config could not be written")
    except Exception as exc:
        logger.warning("[%s] MCP tunnel setup failed: %s", session_id, exc)
        close_reverse_tunnel(transport, remote_port)
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        return None

    return {
        "remote_port": remote_port,
        "remote_path": remote_path,
        "url": url,
        "sftp": sftp,
    }


def teardown(client: Any, record: Optional[Dict[str, Any]]) -> None:
    """Undo :func:`establish` without blocking the caller.

    Every step is a round trip to a host that may already be unreachable, and
    this runs on the *close* path -- where a workspace is being held open
    waiting for it, and where `cancel_port_forward` waits for a reply that a
    wedged transport can never deliver. So the remote work is handed to a
    short-lived daemon thread and the caller returns at once.

    Nothing here is load-bearing: closing the transport cancels the forward
    regardless, and the config file is named per pane and rewritten on that
    pane's next connect, so a leftover is stale for nobody.
    """
    if not record:
        return

    sftp = record.get("sftp")
    remote_path = str(record.get("remote_path") or "")
    remote_port = int(record.get("remote_port") or 0)

    def _release() -> None:
        remove_remote_config(sftp, remote_path)
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        transport = None
        try:
            transport = client.get_transport() if client is not None else None
        except Exception:
            transport = None
        close_reverse_tunnel(transport, remote_port)

    threading.Thread(
        target=_release, name="gridvibe-mcp-tunnel-teardown", daemon=True
    ).start()
