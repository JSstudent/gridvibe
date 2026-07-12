"""Fernet key management and saved SSH password encryption."""

import logging
import os

from cryptography.fernet import Fernet

from web.paths import BASE_DIR

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_PATH = os.path.join(BASE_DIR, ".encryption_key")


def _get_encryption_key() -> bytes:
    """Load or generate encryption key for password storage."""
    if os.path.exists(ENCRYPTION_KEY_PATH):
        with open(ENCRYPTION_KEY_PATH, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(ENCRYPTION_KEY_PATH, "wb") as f:
        f.write(key)
    os.chmod(ENCRYPTION_KEY_PATH, 0o600)
    return key


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
