"""Persistent SSH known-hosts handling shared by terminal and explorer code.

Extracted from web/api.py (deep-dive finding 6.2). Loading a project-local
known_hosts file keeps paramiko's AutoAddPolicy convenient on first use while
still detecting host-key changes on later connections (finding 1.4).
"""

import logging
import os
from typing import Any

from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

KNOWN_HOSTS_PATH = os.path.join(BASE_DIR, ".known_hosts")


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
