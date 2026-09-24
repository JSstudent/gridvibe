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
*this pane's MCP endpoint*, and the create-tier tools act on *this* machine.
Three things bound it: the port exists only for the life of that pane's
connection, the URL carries a per-pane token (`web/mcp_http.py`) that is
revoked the moment the pane closes, and the forwarded connection reaches a
**filter** rather than GridVibe's port -- one request, and only this pane's
own ``POST /mcp/<token>``. A reader who does not tick MCP opens no port at all.

**Why the filter is load-bearing.** sshd hands back a raw TCP channel, and the
address on this end is GridVibe's whole loopback HTTP API -- saved sessions
with decryptable credentials, the destroy tier the tool surface deliberately
does not contain, the ungated twins of every gated tool route. Their only
guard has always been "you have to be on this machine", and a plain byte pump
would have handed all of it to the remote host with the token guarding exactly
one route on it. So each channel is parsed here first, and a socket to
GridVibe is opened only for a request that survives every check below.
"""

import json
import logging
import secrets
import socket
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: The address sshd is asked to bind on the remote host. Loopback on purpose:
#: the forwarded port is for the pane's own agent, not for the remote network.
REMOTE_BIND_ADDRESS = "127.0.0.1"

#: Seconds a forwarded channel may wait on its local half before being torn
#: down. The local half is GridVibe's own HTTP port, so slow means gone.
LOCAL_CONNECT_TIMEOUT = 10.0

#: Bytes moved per read.
_CHUNK = 32768

#: Bytes of request head (request line plus headers) a forwarded channel may
#: send. A real MCP request's head is a few hundred; this is the ceiling that
#: stops a remote process filling memory with a header it never terminates.
MAX_HEAD_BYTES = 16384

#: Bytes of body. Tool arguments are small JSON, and the reply is what carries
#: size; a caller streaming more than this is not making a tool call.
MAX_BODY_BYTES = 1048576

#: Seconds a channel has to deliver its one request before it is dropped. The
#: *reply* is not bounded by this -- two tools wait on a page for 25s.
REQUEST_READ_TIMEOUT = 30.0

#: Forwarded connections one tunnelled pane may have in flight at once. It is
#: here because *anything* on the remote host can reach that pane's forwarded
#: port, and every accepted connection costs a thread of this process for up to
#: `REQUEST_READ_TIMEOUT` even if it never sends a byte: without a ceiling, a
#: loop opening idle connections turns a pane's tunnel into sustained thread
#: and memory growth here, which the per-request head and body bounds cannot
#: see.
#:
#: Well above what one agent does. A connection carries exactly one request,
#: and the two that wait -- `split_pane` and `open_window` wait on a page --
#: are the only ones that hold a slot for long, so the number to clear is a
#: CLI's parallel tool calls plus those, not one.
#:
#: Per pane rather than per process: one noisy host must not be able to starve
#: the tools of a pane connected to a different one, and the number of tunnels
#: is already bounded by the panes the reader opened with MCP ticked.
MAX_FORWARDED_CHANNELS = 16

#: Headers that describe the hop rather than the request, so the filter states
#: its own instead of forwarding the caller's. ``content-length`` is here
#: because the filter re-derives it from the body it actually read.
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

#: RFC 9110 token characters: a header name outside this set is a malformed
#: request, not something to pass along and let the server puzzle over.
_HEADER_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)

_SUPPORTED_HTTP_VERSIONS = ("HTTP/1.1", "HTTP/1.0")

#: One sentence for every refusal. It names no route and confirms no token:
#: a caller on the remote host learns only that this port is not a way in.
_REFUSAL_SENTENCE = "This port serves one GridVibe MCP endpoint and nothing else."

_REFUSAL_REASONS = {
    400: "Bad Request",
    404: "Not Found",
    413: "Payload Too Large",
    502: "Bad Gateway",
}


def mcp_path(token: str) -> str:
    """The one request path a pane's forwarded port answers, or ``""``."""
    resolved = str(token or "").strip()
    return f"/mcp/{resolved}" if resolved else ""


def _loggable_target(target: str) -> str:
    """The shape of a refused path, never the path.

    ``/mcp/<token>`` *is* a credential, and a legitimate client that gets the
    method wrong would otherwise write this pane's own token into the log. The
    first segment is what diagnoses the refusal -- a stray process on the
    remote host, or a client asking for the wrong verb.
    """
    head = str(target or "").split("?", 1)[0].split("/")
    first = head[1][:32] if len(head) > 1 else ""
    return f"/{first}/..." if len(head) > 2 else f"/{first}"


def _refuse(channel: Any, status: int) -> None:
    """Answer one refused request and let the caller close the channel."""
    body = json.dumps({"error": _REFUSAL_SENTENCE}).encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {_REFUSAL_REASONS.get(status, 'Bad Request')}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("latin-1")
    try:
        channel.sendall(head + body)
    except Exception:
        # The caller is gone, which is the same outcome as being refused.
        pass


def _read_head(channel: Any) -> Optional[Tuple[bytes, bytes]]:
    """Read up to the blank line. Returns ``(head, leftover)`` or ``None``."""
    buffer = bytearray()
    while True:
        marker = buffer.find(b"\r\n\r\n")
        if marker >= 0:
            # The ceiling is on the head itself, not only on the search for
            # it: one long recv can carry a terminator past the limit.
            if marker > MAX_HEAD_BYTES:
                return None
            return bytes(buffer[:marker]), bytes(buffer[marker + 4:])
        if len(buffer) > MAX_HEAD_BYTES:
            return None
        try:
            chunk = channel.recv(_CHUNK)
        except (OSError, socket.error, EOFError):
            return None
        if not chunk:
            return None
        buffer.extend(chunk)


def _parse_head(head: bytes) -> Optional[Tuple[str, str, str, List[Tuple[str, str]]]]:
    """Split one request head, or ``None`` if it is not one request.

    Strict on purpose. Obsolete line folding and a header name outside the
    token set are both refused rather than normalised: this is a filter in
    front of an API whose only other guard is the loopback bind, and the
    cheapest way to be sure nothing is smuggled past it is to forward only
    what parses exactly one way.
    """
    try:
        text = head.decode("latin-1")
    except Exception:  # pragma: no cover - latin-1 decodes every byte
        return None
    lines = text.split("\r\n")
    request_line = lines[0].split(" ")
    if len(request_line) != 3:
        return None
    method, target, version = request_line
    headers: List[Tuple[str, str]] = []
    for line in lines[1:]:
        if not line or line[0] in " \t":
            return None
        name, separator, value = line.partition(":")
        if not separator or not name or not set(name) <= _HEADER_NAME_CHARS:
            return None
        headers.append((name, value.strip()))
    return method, target, version, headers


def _body_length(headers: List[Tuple[str, str]]) -> Optional[int]:
    """The declared body length, or ``None`` for framing this will not carry.

    A ``Transfer-Encoding`` is refused rather than streamed: its end is
    discoverable only by parsing the chunks, and a filter that cannot say
    where the body ends cannot say that nothing follows it.
    """
    lengths = set()
    for name, value in headers:
        lowered = name.lower()
        if lowered == "transfer-encoding":
            return None
        if lowered == "content-length":
            lengths.add(value.strip())
    if not lengths:
        return 0
    if len(lengths) != 1:
        return None
    try:
        length = int(lengths.pop())
    except ValueError:
        return None
    return length if length >= 0 else None


def _read_body(channel: Any, leftover: bytes, length: int) -> Optional[bytes]:
    """Read exactly ``length`` bytes. Anything beyond them is discarded.

    That discard is the second half of the one-request rule: a caller that
    pipelines ``DELETE /api/sessions`` behind a valid tool call has written it
    into a buffer nobody reads.
    """
    body = bytearray(leftover[:length])
    while len(body) < length:
        try:
            chunk = channel.recv(min(_CHUNK, length - len(body)))
        except (OSError, socket.error, EOFError):
            return None
        if not chunk:
            return None
        body.extend(chunk)
    return bytes(body)


def _forwarded_request(
    method: str,
    target: str,
    version: str,
    headers: List[Tuple[str, str]],
    body: bytes,
) -> bytes:
    """Rebuild the one request that is forwarded, from the parts that parsed."""
    lines = [f"{method} {target} {version}"]
    lines.extend(
        f"{name}: {value}"
        for name, value in headers
        if name.lower() not in _HOP_BY_HOP_HEADERS
    )
    lines.append(f"Content-Length: {len(body)}")
    # One request per forwarded connection: GridVibe closes when it has
    # answered, so nothing can arrive behind this on the same socket.
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


def _accepted_request(channel: Any, expected_path: str) -> Optional[bytes]:
    """Read one request and return what may be forwarded, or ``None``.

    Every refusal is answered on the channel before this returns, so the
    caller only has to close.
    """
    if not expected_path:
        _refuse(channel, 404)
        return None
    head = _read_head(channel)
    if head is None:
        _refuse(channel, 400)
        return None
    parsed = _parse_head(head[0])
    if parsed is None:
        _refuse(channel, 400)
        return None
    method, target, version, headers = parsed
    # Method and path together, answered the same way: a caller learns neither
    # which routes exist nor whether it guessed a live token. Compared as
    # bytes because the target is whatever the caller sent, and
    # `compare_digest` raises on a non-ASCII `str` rather than answering False.
    if (
        method != "POST"
        or version not in _SUPPORTED_HTTP_VERSIONS
        or not secrets.compare_digest(
            target.encode("latin-1"), expected_path.encode("latin-1")
        )
    ):
        logger.warning(
            "MCP tunnel refused a forwarded %s %s: this port serves only its "
            "own pane's MCP endpoint",
            method[:16],
            _loggable_target(target),
        )
        _refuse(channel, 404)
        return None
    length = _body_length(headers)
    if length is None:
        _refuse(channel, 400)
        return None
    if length > MAX_BODY_BYTES:
        _refuse(channel, 413)
        return None
    body = _read_body(channel, head[1], length)
    if body is None:
        _refuse(channel, 400)
        return None
    return _forwarded_request(method, target, version, headers, body)


def _close_quietly(closeable: Any) -> None:
    """Close a channel or socket that may already be gone."""
    if closeable is None:
        return
    try:
        closeable.close()
    except Exception:
        pass


def _relay_response(channel: Any, sock: socket.socket) -> None:
    """Move the reply back until GridVibe closes, which it does when done."""
    try:
        while True:
            data = sock.recv(_CHUNK)
            if not data:
                return
            channel.sendall(data)
    except (OSError, socket.error, EOFError):
        # An ordinary end of either half; the caller closes both.
        return
    except Exception:  # pragma: no cover - defensive; this runs on its own thread
        logger.exception("MCP tunnel reply relay failed")


def _serve_forwarded_channel(
    channel: Any, local_host: str, local_port: int, expected_path: str = ""
) -> None:
    """Filter one forwarded connection, then carry its single exchange."""
    sock = None
    try:
        try:
            channel.settimeout(REQUEST_READ_TIMEOUT)
        except Exception:
            pass
        request = _accepted_request(channel, expected_path)
        if request is None:
            return
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
            _refuse(channel, 502)
            return
        # Blocking from here: the request is already in hand and the reply is
        # bounded by GridVibe closing the socket it was told to close.
        sock.settimeout(None)
        try:
            channel.settimeout(None)
        except Exception:
            pass
        sock.sendall(request)
        _relay_response(channel, sock)
    except (OSError, socket.error, EOFError):
        pass
    except Exception:  # pragma: no cover - defensive; this runs on its own thread
        logger.exception("MCP tunnel channel failed")
    finally:
        for closeable in (channel, sock):
            _close_quietly(closeable)


def _forward_handler(local_host: str, local_port: int, expected_path: str = ""):
    """Build the per-channel handler paramiko calls for each connection.

    **It must return immediately.** Paramiko invokes this from
    ``Transport._parse_channel_open`` -- on the transport's own packet-reading
    thread, with no thread of its own -- so anything that blocks here stops
    that transport processing any further SSH packet. That thread also carries
    the pane's *shell*: serving inline froze the terminal (no output, no
    accepted input), starved the very bytes the tunnel was opened to carry, and
    left ``cancel_port_forward`` waiting on a reply the blocked thread could
    never read. One thread per forwarded connection, handed off at once.

    **And bounded.** "A thread per connection" is fine for an agent making a
    tool call and open to abuse from anything else on that remote host, which
    is the same population the filter below exists for: a loop of connections
    that send nothing costs a thread each for `REQUEST_READ_TIMEOUT`. So the
    threads are a budget (`MAX_FORWARDED_CHANNELS`) held by this pane's own
    handler, and a connection arriving with none free is closed rather than
    queued -- the pane's next real tool call is served as soon as one frees.
    """

    slots = threading.BoundedSemaphore(MAX_FORWARDED_CHANNELS)

    def serve(channel: Any) -> None:
        try:
            _serve_forwarded_channel(channel, local_host, local_port, expected_path)
        finally:
            slots.release()

    def handler(channel: Any, origin: Any, server: Any) -> None:
        if not slots.acquire(blocking=False):
            # Closed rather than queued, and rather than answered: a refusal
            # written here would be written on the transport's own packet
            # thread, which is the thread this function exists to release.
            logger.warning(
                "MCP tunnel dropped a forwarded connection: %d are already in "
                "flight for this pane",
                MAX_FORWARDED_CHANNELS,
            )
            _close_quietly(channel)
            return
        try:
            worker = threading.Thread(
                target=serve,
                args=(channel,),
                name="gridvibe-mcp-tunnel",
                daemon=True,
            )
            worker.start()
        except Exception as exc:
            # A thread that could not start never reaches the `finally` that
            # gives the slot back, and a budget that leaks is a tunnel that
            # stops answering.
            slots.release()
            _close_quietly(channel)
            logger.warning("MCP tunnel could not serve a forwarded channel: %s", exc)

    return handler


def open_reverse_tunnel(
    transport: Any,
    *,
    local_host: str,
    local_port: int,
    token: str,
) -> int:
    """Ask the remote sshd to listen and forward back here. Returns its port.

    ``0`` means the request failed, which costs the pane its tools and nothing
    else -- the shell, the agent and every other pane are untouched, so this
    reports rather than raises.

    The token is what the listener is *for*: it fixes the one path this port
    answers, so a port opened for one pane cannot spend another pane's token
    and cannot reach anything else on GridVibe's own port. Without one there
    is nothing to serve, so no listener is opened.
    """
    if transport is None:
        return 0
    expected_path = mcp_path(token)
    if not expected_path:
        return 0
    try:
        remote_port = transport.request_port_forward(
            REMOTE_BIND_ADDRESS,
            0,
            handler=_forward_handler(local_host, local_port, expected_path),
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
    """The URL the remote agent is given, as seen from the remote host.

    Built from :func:`mcp_path`, which is also the path the filter accepts --
    one spelling, so the URL written on the remote host cannot name something
    the forwarded port would refuse.
    """
    path = mcp_path(token)
    if not remote_port or not path:
        return ""
    return f"http://{REMOTE_BIND_ADDRESS}:{int(remote_port)}{path}"


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


#: The bits that must be clear on the remote config and on the directory
#: holding it. The file carries this pane's bearer token, and that token is the
#: whole of the endpoint's authentication -- so a group- or world-readable mode
#: on a shared host hands another account this pane's tool surface, acting on
#: GridVibe's own machine.
FORBIDDEN_MODE_BITS = 0o077


def _restricted_to_owner(
    sftp: Any,
    remote_path: str,
    mode: int,
    *,
    label: str = "",
) -> bool:
    """Apply ``mode`` and read it back. False when it cannot be *proved*.

    Both halves refuse, and for the same reason: a ``chmod`` the remote host
    declined and a mode that could not be read back are equally "nobody here
    knows who can read this", and what is being written is a credential. A host
    whose SFTP implementation can do neither costs the pane its tools -- the
    price every other tunnel failure charges, and never its shell.

    ``label`` replaces the path in every log line, for a file whose path is
    itself not something to log -- a handed-over task's.
    """
    named = label or remote_path
    try:
        sftp.chmod(remote_path, mode)
    except Exception as exc:
        logger.warning("Could not restrict permissions on %s: %s", named, exc)
        return False
    try:
        current = getattr(sftp.stat(remote_path), "st_mode", None)
    except Exception as exc:
        logger.warning("Could not verify permissions on %s: %s", named, exc)
        return False
    try:
        bits = int(current)
    except (TypeError, ValueError):
        logger.warning("The remote host reported no mode for %s", named)
        return False
    if bits & FORBIDDEN_MODE_BITS:
        logger.warning(
            "%s is still reachable by other accounts on the remote host "
            "(mode %o), so it was not left there",
            named,
            bits & 0o777,
        )
        return False
    return True


def write_remote_config(
    sftp: Any,
    remote_path: str,
    url: str,
) -> bool:
    """Place the generated config on the remote host. Returns success.

    Written through the SFTP channel of the pane's own connection, so it needs
    no second authentication and lands as the same user the pane runs as.

    Fails **closed**. The document names this pane's token, so a file whose
    permissions could not be applied or verified is removed again rather than
    left behind: the caller then withdraws the listener and revokes the token,
    and the pane starts without tools. A readable token would instead be a
    standing invitation for every other account on that host, which is the one
    outcome worse than a pane with no tools.
    """
    if not sftp or not remote_path or not url:
        return False
    try:
        with sftp.open(remote_path, "w") as handle:
            handle.write(json.dumps(remote_mcp_document(url), indent=2) + "\n")
    except Exception as exc:
        logger.warning("Could not write the remote MCP config %s: %s", remote_path, exc)
        return False
    if not _restricted_to_owner(sftp, remote_path, 0o600):
        remove_remote_config(sftp, remote_path)
        return False
    return True


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
    """Create the parent directory of the remote config, owner-only. Success?

    Checked even when it was already there. ``~/.gridvibe`` outlives any one
    pane, and a previous run, another tool or a permissive ``umask`` may have
    left it open to the rest of the host -- a directory other accounts can read
    lists every pane's config file, whatever mode the files themselves carry.
    So an existing directory is narrowed and verified exactly like a new one,
    and one that cannot be proved owner-only is not written into.
    """
    parent = remote_path.rsplit("/", 1)[0] if "/" in remote_path else ""
    if not parent:
        return True
    try:
        sftp.stat(parent)
    except Exception:
        try:
            sftp.mkdir(parent, 0o700)
        except Exception as exc:
            logger.warning("Could not create %s on the remote host: %s", parent, exc)
            return False
    return _restricted_to_owner(sftp, parent, 0o700)


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
    if not str(token or "").strip():
        return None

    transport = None
    try:
        transport = client.get_transport()
    except Exception:
        transport = None
    if transport is None:
        return None

    remote_port = open_reverse_tunnel(
        transport, local_host=local_host, local_port=local_port, token=token
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
    # A handed-over task written on this host rode the same SFTP channel, and
    # belongs to the same connection -- so it goes in the same round trip,
    # before the channel closes under it.
    handoff_paths = [str(path) for path in record.get("handoff_paths") or () if path]

    def _release() -> None:
        remove_remote_config(sftp, remote_path)
        for handoff_path in handoff_paths:
            try:
                sftp.remove(handoff_path)
            except Exception:
                logger.debug("Could not remove a remote handoff file", exc_info=True)
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
