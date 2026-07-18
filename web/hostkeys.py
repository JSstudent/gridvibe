"""Persistent SSH known-hosts handling shared by terminal and explorer code.

Extracted from web/api.py (deep-dive finding 6.2). Loading a project-local
known_hosts file keeps paramiko's AutoAddPolicy convenient on first use while
still detecting host-key changes on later connections (finding 1.4).
"""

import logging
import os
from typing import Any, Optional

from web.config import runtime_config
from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

KNOWN_HOSTS_PATH = os.path.join(BASE_DIR, ".known_hosts")
USER_KNOWN_HOSTS_PATH = os.path.join(os.path.expanduser("~"), ".ssh", "known_hosts")


def _load_persistent_host_keys(client: Any) -> None:
    """Load (creating if needed) the project-local known_hosts into a client.

    Because ``load_host_keys`` records the filename, paramiko's AutoAddPolicy
    persists newly accepted keys automatically, and a changed key for a known
    host raises ``BadHostKeyException`` on connect. Failures degrade to the
    historical trust-on-every-use behaviour with a warning.
    """
    try:
        if not os.path.exists(KNOWN_HOSTS_PATH):
            with open(KNOWN_HOSTS_PATH, "a", encoding="utf-8"):
                pass
        client.load_host_keys(KNOWN_HOSTS_PATH)
    except Exception as exc:
        logger.warning(
            "Could not load %s; SSH host key changes will not be detected: %s",
            KNOWN_HOSTS_PATH,
            exc,
        )


class _WarnNewHostKeyPolicy:
    """Accept and persist unknown host keys like AutoAddPolicy, but warn.

    Used by ``ssh.host_key_policy: "known-hosts"`` (finding 10.7). Changed keys
    for already-known hosts still raise ``BadHostKeyException`` on connect —
    that check happens before the missing-key policy is consulted.
    """

    def __init__(self, delegate: Any):
        self._delegate = delegate

    def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
        logger.warning(
            "Accepting previously unseen SSH host key for %s and persisting it "
            "to %s (ssh.host_key_policy=known-hosts).",
            hostname,
            KNOWN_HOSTS_PATH,
        )
        return self._delegate.missing_host_key(client, hostname, key)


def _apply_host_key_policy(client: Any, paramiko_module: Any, policy: Optional[str] = None) -> None:
    """Configure host-key verification on a fresh SSHClient (finding 10.7).

    Policies (``ssh.host_key_policy``):
    - ``auto-add`` (default) — accept unknown keys silently, persist them to the
      project-local ``.known_hosts``, and reject changed keys (finding 1.4).
    - ``known-hosts`` — same, but log a warning whenever a new key is accepted.
    - ``strict`` — reject unknown hosts outright; keys must already exist in the
      project ``.known_hosts`` or the user's ``~/.ssh/known_hosts``.
    """
    resolved = str(policy if policy is not None else runtime_config.ssh_host_key_policy).strip().lower()
    _load_persistent_host_keys(client)
    if resolved == "strict":
        try:
            if os.path.exists(USER_KNOWN_HOSTS_PATH):
                client.load_system_host_keys(USER_KNOWN_HOSTS_PATH)
        except Exception as exc:
            logger.warning("Could not load %s: %s", USER_KNOWN_HOSTS_PATH, exc)
        client.set_missing_host_key_policy(paramiko_module.RejectPolicy())
    elif resolved == "known-hosts":
        client.set_missing_host_key_policy(_WarnNewHostKeyPolicy(paramiko_module.AutoAddPolicy()))
    else:
        client.set_missing_host_key_policy(paramiko_module.AutoAddPolicy())
