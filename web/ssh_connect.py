"""Bounded SSH logins: how many handshakes one host sees from GridVibe at once.

OpenSSH starts refusing new connections at random once more than its
``MaxStartups`` (default ``10:30:100``) are still unauthenticated; the client
sees its socket closed before the protocol banner ("Error reading SSH protocol
banner", WinError 10053 on Windows). A workspace restore connects every SSH
pane of every group at the same moment, so one host could be handed a dozen
logins together and drop some of them.

Every GridVibe SSH login goes through :func:`connect_ssh_client`, which lets at
most ``MAX_CONCURRENT_HANDSHAKES`` per host and port be in flight; the rest
wait their turn. A slot covers only ``connect()`` -- handshake and
authentication -- and is released when it returns, which is when sshd stops
counting the connection. Each holder is bounded by its own connect timeout, so
a wait is too.
"""

import threading
from typing import Any, Dict, Tuple

#: Comfortably under sshd's default start of 10, leaving room for the person's
#: own clients and the explorer's pooled connections to the same host.
MAX_CONCURRENT_HANDSHAKES = 4

_gates: Dict[Tuple[str, str], threading.BoundedSemaphore] = {}
_gates_lock = threading.Lock()


def _gate(hostname: Any, port: Any) -> threading.BoundedSemaphore:
    key = (str(hostname or "").strip().lower(), str(port or 22).strip())
    with _gates_lock:
        gate = _gates.get(key)
        if gate is None:
            gate = _gates[key] = threading.BoundedSemaphore(MAX_CONCURRENT_HANDSHAKES)
        return gate


def connect_ssh_client(client: Any, *, hostname: Any, port: Any, **kwargs: Any) -> None:
    """``client.connect(...)``, waiting for a free login slot on that host."""
    with _gate(hostname, port):
        client.connect(hostname=hostname, port=port, **kwargs)
