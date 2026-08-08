"""Durable JSON state-file primitives shared by GridVibe's persistent stores.

``runtime_state.json`` (workspace snapshots) and ``saved_sessions.json``
(launcher presets) are both small JSON documents that several threads — and
several GridVibe processes — read, modify and replace. They need the same four
mechanics, and those mechanics existed in only one of the two stores:

* an OS-level sidecar lock, so one complete read-modify-replace wins at a time
  and a second process cannot silently discard this one's update;
* a unique same-directory temporary file plus ``os.replace``, so a reader never
  observes a half-written document and a crash never truncates the live file;
* a ``<file>.bak`` copy taken before every commit, so a torn or corrupt
  successor always has a last-good predecessor to recover from;
* quarantine of an unreadable file instead of laundering it into empty state
  that the next commit would make permanent.

Only the mechanics live here. Schema, migration, ordering and normalization
stay with each store, which is why the lock and the writer take the caller's
exception type and a human label: a failed saved-session write must surface as
a saved-session failure, not as a runtime-state one.
"""

import json
import logging
import os
import shutil
import time
import uuid
from typing import Any, Optional, Type

logger = logging.getLogger(__name__)

try:  # POSIX advisory locking
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # Windows mandatory byte-range locking
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

# How long one process waits for another to finish its read-modify-replace.
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0


class StateFilePersistenceError(RuntimeError):
    """Raised when an intended state revision did not reach the disk.

    Each store subclasses this so a caller can catch its own store's failure,
    while shared middleware (an API error handler, a lifecycle action) can catch
    the base and answer "not stored" for either file.
    """


class CrossProcessFileLock:
    """Exclusive OS-level lock over one state file.

    An in-process lock orders threads inside one interpreter; it says nothing
    about a second GridVibe process doing its own read-modify-replace. Both
    would read, both would modify, and the later ``os.replace`` would discard
    the other's update wholesale. A sidecar ``<file>.lock`` (never the state
    file itself, which is replaced rather than written in place) makes the
    complete operation single-writer across processes.

    Degrades to a no-op with one warning where neither locking primitive is
    available — a missing lock must not make GridVibe unable to save at all.
    """

    _unsupported_warned = False

    #: Exception raised when the lock cannot be taken within the timeout.
    error_type: Type[Exception] = StateFilePersistenceError
    #: Human name used in the timeout message ("the <label> lock").
    label = "state"

    def __init__(self, state_path: str, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS):
        self._lock_path = f"{state_path}.lock"
        self._timeout = timeout
        self._fd: Optional[int] = None

    def __enter__(self) -> "CrossProcessFileLock":
        if fcntl is None and msvcrt is None:  # pragma: no cover - exotic platform
            if not CrossProcessFileLock._unsupported_warned:
                CrossProcessFileLock._unsupported_warned = True
                logger.warning(
                    "No file-locking primitive available; %s is protected "
                    "within this process only",
                    self.label,
                )
            return self
        directory = os.path.dirname(os.path.abspath(self._lock_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._acquire_once(self._fd)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise self.error_type(
                        f"Another process is holding the {self.label} lock "
                        f"({self._lock_path}); nothing was saved"
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return
        try:
            self._release_once(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None

    @staticmethod
    def _acquire_once(fd: int) -> None:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    @staticmethod
    def _release_once(fd: int) -> None:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def quarantine_state_file(state_path: str, reason: str, label: str = "state") -> None:
    """Move an unreadable/unsupported state file aside, keeping the evidence."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    quarantine_path = f"{state_path}.corrupt-{stamp}"
    if os.path.exists(quarantine_path):
        quarantine_path = f"{quarantine_path}-{uuid.uuid4().hex[:8]}"
    try:
        os.replace(state_path, quarantine_path)
    except OSError as exc:
        logger.error("Could not quarantine unreadable %s: %s", label, exc)
        return
    logger.error(
        "Quarantined unreadable %s (%s) as %s",
        label,
        reason,
        os.path.basename(quarantine_path),
    )


def back_up_state_file(state_path: str) -> None:
    """Copy the current state file to ``<file>.bak`` (best effort)."""
    if not os.path.exists(state_path):
        return
    try:
        shutil.copyfile(state_path, f"{state_path}.bak")
    except OSError as exc:
        # A missing backup weakens recovery but must not fail the commit.
        logger.debug("Could not refresh the %s backup: %s", os.path.basename(state_path), exc)


def read_backup_json(state_path: str, label: str = "state") -> Any:
    """Return the last-good ``<file>.bak`` payload, or ``None`` when unusable."""
    backup_path = f"{state_path}.bak"
    try:
        with open(backup_path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.error("Last-good %s is unusable too: %s", label, exc)
        return None


def write_json_atomically(
    payload: Any,
    state_path: str,
    error_type: Type[Exception] = StateFilePersistenceError,
    failure_message: str = "Could not persist the state file",
) -> None:
    """Atomically persist ``payload`` as JSON, backing up the previous file.

    Raises ``error_type`` when the intended revision did not reach the disk — a
    full disk, a permission error, an antivirus lock, or a failed replace must
    never be reported to the user as saved. The temporary file carries a unique
    name in the target directory so two concurrent writers cannot share (and
    corrupt) one scratch path, and it is removed again on failure.
    """
    directory = os.path.dirname(os.path.abspath(state_path)) or "."
    temp_path = os.path.join(
        directory,
        f".{os.path.basename(state_path)}.{uuid.uuid4().hex}.tmp",
    )
    try:
        os.makedirs(directory, exist_ok=True)
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        back_up_state_file(state_path)
        os.replace(temp_path, state_path)
    except Exception as exc:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        logger.error("%s: %s", failure_message, exc)
        raise error_type(f"{failure_message}: {exc}") from exc


def create_file_exclusively(data: bytes, target_path: str, mode: int = 0o600) -> bool:
    """Create ``target_path`` with ``data``, atomically and only if absent.

    Returns ``True`` when this caller created the file and ``False`` when another
    writer got there first. The content is written and flushed to a unique
    same-directory temporary file *before* the target name exists, so a
    concurrent reader can only ever observe the complete file — never the
    zero-byte window an ``open(path, "wb")`` leaves behind.

    The claim itself is one atomic syscall that fails rather than clobbers:
    ``os.rename`` on Windows (which refuses an existing destination) and
    ``os.link`` elsewhere. Neither can overwrite a key or secret another process
    already committed.
    """
    directory = os.path.dirname(os.path.abspath(target_path)) or "."
    temp_path = os.path.join(
        directory,
        f".{os.path.basename(target_path)}.{uuid.uuid4().hex}.tmp",
    )
    os.makedirs(directory, exist_ok=True)
    try:
        with open(temp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:  # pragma: no cover - platform without chmod semantics
            pass
        try:
            if os.name == "nt":
                # Windows' rename refuses an existing destination, which is
                # exactly the create-if-absent semantics wanted here.
                os.rename(temp_path, target_path)
            else:
                os.link(temp_path, target_path)
            return True
        except (FileExistsError, OSError) as exc:
            if not isinstance(exc, FileExistsError) and not os.path.exists(target_path):
                raise
            return False
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass
