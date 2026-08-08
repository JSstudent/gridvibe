"""Fernet key management and saved SSH password encryption."""

import logging
import os
import time

from cryptography.fernet import Fernet

from web.paths import BASE_DIR
from web.state_files import create_file_exclusively

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_PATH = os.path.join(BASE_DIR, ".encryption_key")

# A reader can only lose a race against a *legacy* writer that still creates the
# key file non-atomically; :func:`create_file_exclusively` never publishes a
# partial file. Retry briefly rather than crash the import, then give up loudly.
_KEY_READ_ATTEMPTS = 10
_KEY_READ_RETRY_SECONDS = 0.05


def _read_encryption_key(path: str) -> bytes:
    """Return the stored key, or ``b""`` when the file is absent or incomplete."""
    try:
        with open(path, "rb") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return b""


def _get_encryption_key() -> bytes:
    """Load or generate the encryption key used for stored SSH passwords.

    Two first-start processes used to check-then-write independently: both saw
    no file, both generated a key, and the loser kept a cipher built from a key
    that was no longer on disk. Worse, the write itself created the file empty
    and filled it afterwards, so a process importing this module in that window
    read zero bytes, ``Fernet()`` raised, and GridVibe failed to start at all.

    Creation is therefore exclusive and atomic (SGP-05): the key is written to a
    unique sibling temp file and claimed under the real name with a syscall that
    fails rather than clobbers. The loser of the race reads the winner's whole
    key, so both processes end up with the same one and neither can observe a
    partial file.
    """
    path = ENCRYPTION_KEY_PATH
    key = _read_encryption_key(path)
    if key:
        return key

    created = create_file_exclusively(Fernet.generate_key(), path)
    for attempt in range(_KEY_READ_ATTEMPTS):
        key = _read_encryption_key(path)
        if key:
            if not created:
                logger.info(
                    "Another GridVibe process created the encryption key first; using it"
                )
            return key
        # Only reachable against a legacy non-atomic writer mid-flight.
        if attempt < _KEY_READ_ATTEMPTS - 1:
            time.sleep(_KEY_READ_RETRY_SECONDS)

    raise RuntimeError(
        f"The encryption key at {path} exists but is empty; remove it to let "
        "GridVibe create a new one (stored SSH passwords will need re-entering)"
    )


_cipher = Fernet(_get_encryption_key())


def _encrypt_password(password: str) -> str:
    """Encrypt password for storage."""
    if not password:
        return ""
    return _cipher.encrypt(password.encode()).decode()


def _decrypt_password(encrypted: str) -> str:
    """Decrypt stored password."""
    if not encrypted:
        return ""
    try:
        return _cipher.decrypt(encrypted.encode()).decode()
    except Exception:
        logger.warning(
            "Stored SSH password could not be decrypted (encryption key changed?); ignoring it."
        )
        return ""
