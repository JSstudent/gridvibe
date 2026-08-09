"""The one owner of ``saved_sessions.json``: path, locks, durability.

A saved launcher preset used to be written by three independent
load-modify-save pairs (upsert, delete, last-session selection). Two request
threads — or two GridVibe processes — could read the same payload, each replace
it, and the later writer would silently drop the other's unrelated preset. The
write itself truncated the live file in place, so an interrupted save left
invalid JSON, and an unreadable file was laundered into an empty store that the
next successful save made permanent. Every one of those failures takes an
encrypted SSH password with it.

This store gives the preset file the durability ``runtime_state.json`` already
had, through the shared primitives in :mod:`web.state_files`:

* :meth:`SavedSessionStore.transaction` runs one *complete* read-modify-write
  under an in-process lock **and** an OS-level ``<file>.lock``, so an upsert can
  no longer be interleaved with a delete;
* the commit goes to a unique same-directory temporary file and is moved into
  place with ``os.replace``, after the previous file is copied to ``<file>.bak``;
* an unreadable or unsupported payload is quarantined and the last-good backup
  is read in its place, rather than being reported as "no saved sessions";
* a failed write or delete raises :class:`SavedSessionsPersistenceError`, so a
  route answers with a retryable error instead of a false success.

Deliberately schema-free. Normalization, encryption, and the preset schema stay
in :mod:`web.saved_sessions`; this module only moves whole JSON payloads to and
from the disk safely. That keeps the secret-handling in one place: the payload
this store writes already has its passwords encrypted, and the store never logs
or reflects payload contents.
"""

import json
import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Callable, Optional, Tuple

from web.state_files import (
    CrossProcessFileLock,
    StateFilePersistenceError,
    back_up_state_file,
    quarantine_state_file,
    read_backup_json,
    write_json_atomically,
)

logger = logging.getLogger(__name__)

# How long one process waits for another to finish its read-modify-replace.
SAVED_SESSIONS_LOCK_TIMEOUT_SECONDS = 10.0

_QUARANTINE_LABEL = "saved sessions"


class SavedSessionsPersistenceError(StateFilePersistenceError):
    """Raised when an intended saved-preset change did not reach the disk.

    Callers must treat this as "not stored": the route answers with a retryable
    non-2xx instead of echoing the preset back as saved.
    """


class _CrossProcessSavedSessionLock(CrossProcessFileLock):
    """Exclusive OS-level lock over one ``saved_sessions.json``."""

    error_type = SavedSessionsPersistenceError
    label = "saved-sessions"

    def __init__(self, state_path: str, timeout: float = SAVED_SESSIONS_LOCK_TIMEOUT_SECONDS):
        super().__init__(state_path, timeout=timeout)


class _Unchanged:
    """Sentinel payload meaning "this transaction decided not to write"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNCHANGED"


#: Return this as a transaction's payload to commit nothing at all.
UNCHANGED = _Unchanged()


class SavedSessionStore:
    """Locked, atomic read-modify-write access to one saved-preset file.

    Constructed with a *path resolver* rather than a path, because the module
    global it resolves is patched per test case (and could be redirected for a
    second GridVibe run). Resolving late means the store never pins a stale path.
    """

    def __init__(
        self,
        path_resolver: Callable[[], str],
        is_supported: Optional[Callable[[Any], bool]] = None,
        lock_timeout: float = SAVED_SESSIONS_LOCK_TIMEOUT_SECONDS,
    ):
        self._path_resolver = path_resolver
        self._is_supported = is_supported or (lambda payload: True)
        self._lock_timeout = lock_timeout
        self._lock = threading.RLock()
        self._held = threading.local()

    @property
    def path(self) -> str:
        """The file this store currently owns."""
        return self._path_resolver()

    def read(self) -> Any:
        """Return the stored JSON payload, or ``None`` when there is none.

        Recovers from ``<file>.bak`` when the primary file is unreadable or
        carries an unsupported payload, and quarantines the bad file first so
        the evidence survives and the next commit cannot overwrite it.
        """
        path = self.path
        with self._exclusive(path):
            return self._read_locked(path)

    def transaction(self, mutate: Callable[[Any], Tuple[Any, Any]]) -> Any:
        """Run one complete read-modify-write under both locks.

        ``mutate`` receives the raw stored payload (``None`` when the file does
        not exist) and returns ``(payload_to_store, result)``:

        * a payload commits it atomically;
        * :data:`UNCHANGED` commits nothing;
        * ``None`` deletes the file (an empty preset store has no file).

        ``result`` is handed back to the caller. ``mutate`` runs while both locks
        are held, so it must stay pure normalization — no request, no emit, and
        no second call into this store.
        """
        path = self.path
        with self._exclusive(path):
            payload, result = mutate(self._read_locked(path))
            if payload is UNCHANGED:
                return result
            if payload is None:
                self._delete_locked(path)
            else:
                write_json_atomically(
                    payload,
                    path,
                    error_type=SavedSessionsPersistenceError,
                    failure_message="Could not persist the saved sessions",
                )
            return result

    # ==================== internals ====================

    @contextmanager
    def _exclusive(self, path: str):
        """Hold the process lock plus the cross-process file lock.

        Re-entrant per thread: the file lock is a fresh descriptor each time and
        would deadlock against itself, so a nested call reuses the outer hold
        rather than acquiring a second one.
        """
        with self._lock:
            if getattr(self._held, "depth", 0):
                self._held.depth += 1
                try:
                    yield
                finally:
                    self._held.depth -= 1
                return
            with _CrossProcessSavedSessionLock(path, timeout=self._lock_timeout):
                self._held.depth = 1
                try:
                    yield
                finally:
                    self._held.depth = 0

    def _read_locked(self, path: str) -> Any:
        """Read the payload. Caller holds both locks."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            quarantine_state_file(path, f"unreadable: {exc}", label=_QUARANTINE_LABEL)
            return self._recover_from_backup(path)

        if not self._is_supported(payload):
            quarantine_state_file(
                path, "unsupported payload shape", label=_QUARANTINE_LABEL
            )
            return self._recover_from_backup(path)
        return payload

    def _recover_from_backup(self, path: str) -> Any:
        """Return the last-good backup payload, or ``None`` when unusable."""
        payload = read_backup_json(path, label=_QUARANTINE_LABEL)
        if payload is None:
            return None
        if not self._is_supported(payload):
            logger.error("Last-good saved sessions carry an unsupported payload")
            return None
        logger.warning("Recovered the saved sessions from the last-good backup")
        return payload

    @staticmethod
    def _delete_locked(path: str) -> None:
        """Remove the file, keeping a last-good copy. Caller holds both locks.

        A failed removal is an error, not a warning: the old presets are still
        on disk and the caller must not report the deletion as done.
        """
        if not os.path.exists(path):
            return
        back_up_state_file(path)
        try:
            os.remove(path)
        except OSError as exc:
            logger.error("Could not delete the saved sessions file: %s", exc)
            raise SavedSessionsPersistenceError(
                f"Could not delete the saved sessions file: {exc}"
            ) from exc
