"""Bounded directory archive building for Explorer downloads."""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, BinaryIO, List, Optional, Tuple

EXPLORER_DIRECTORY_DOWNLOAD_MAX_ENTRIES = 10_000
EXPLORER_DIRECTORY_DOWNLOAD_TIMEOUT_SECONDS = 30.0
EXPLORER_PREPARED_ARCHIVE_TTL_SECONDS = 120.0
EXPLORER_PREPARED_ARCHIVE_MAX_ENTRIES = 4
EXPLORER_PREPARED_ARCHIVE_MAX_BYTES = 200 * 1024 * 1024


class ExplorerDirectoryDownloadLimitError(ValueError):
    """Raised before a directory archive can exceed a download bound."""


class ExplorerDirectoryDownloadTimeoutError(ValueError):
    """Raised when building a directory archive spends its time budget."""


@dataclass(frozen=True)
class ExplorerDirectoryArchive:
    """A completed, seekable archive whose handle is owned by the caller."""

    handle: BinaryIO
    filename: str
    size: int
    path: str
    etag: str

    def cleanup(self) -> None:
        try:
            self.handle.close()
        finally:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass


@dataclass
class _PreparedArchiveRecord:
    session_id: str
    path: str
    filename: str
    size: int
    etag: str
    expires_at: float
    active: int = 0


@dataclass(frozen=True)
class PreparedExplorerDirectoryArchive:
    token: str
    handle: BinaryIO
    filename: str
    size: int
    etag: str


class PreparedDirectoryArchiveStore:
    """Short-lived immutable ZIPs handed from a checked probe to an anchor."""

    def __init__(
        self,
        *,
        ttl_seconds: float = EXPLORER_PREPARED_ARCHIVE_TTL_SECONDS,
        max_entries: int = EXPLORER_PREPARED_ARCHIVE_MAX_ENTRIES,
        max_bytes: int = EXPLORER_PREPARED_ARCHIVE_MAX_BYTES,
    ):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, _PreparedArchiveRecord]" = OrderedDict()
        self._ttl_seconds = max(1.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._max_bytes = max(1, int(max_bytes))
        self._total_bytes = 0

    @staticmethod
    def _unlink(paths: List[str]) -> None:
        for path in paths:
            try:
                os.unlink(path)
            except (FileNotFoundError, PermissionError):
                pass

    def _discard_locked(self, token: str) -> Optional[str]:
        record = self._entries.pop(token, None)
        if record is None:
            return None
        self._total_bytes -= record.size
        return record.path

    def _evictions_locked(self, now: float, incoming_size: int = 0) -> List[str]:
        paths = []
        for token, record in list(self._entries.items()):
            if record.expires_at <= now and record.active == 0:
                path = self._discard_locked(token)
                if path:
                    paths.append(path)
        while (
            len(self._entries) >= self._max_entries
            or self._total_bytes + incoming_size > self._max_bytes
        ):
            victim = next(
                (token for token, record in self._entries.items() if record.active == 0),
                None,
            )
            if victim is None:
                break
            path = self._discard_locked(victim)
            if path:
                paths.append(path)
        return paths

    def prepare(self, session_id: str, archive: ExplorerDirectoryArchive) -> str:
        """Take ownership of ``archive`` and return its unguessable URL token."""
        archive.handle.close()
        now = time.monotonic()
        token = secrets.token_urlsafe(24)
        with self._lock:
            evicted = self._evictions_locked(now, archive.size)
            if (
                len(self._entries) >= self._max_entries
                or self._total_bytes + archive.size > self._max_bytes
            ):
                accepted = False
            else:
                self._entries[token] = _PreparedArchiveRecord(
                    session_id=str(session_id),
                    path=archive.path,
                    filename=archive.filename,
                    size=archive.size,
                    etag=archive.etag,
                    expires_at=now + self._ttl_seconds,
                )
                self._total_bytes += archive.size
                accepted = True
        self._unlink(evicted)
        if not accepted:
            self._unlink([archive.path])
            raise ExplorerDirectoryDownloadLimitError(
                "Too many directory downloads are already prepared"
            )
        return token

    def acquire(
        self, token: str, session_id: str
    ) -> Optional[PreparedExplorerDirectoryArchive]:
        now = time.monotonic()
        with self._lock:
            evicted = self._evictions_locked(now)
            record = self._entries.get(str(token or ""))
            if (
                record is None
                or record.session_id != str(session_id)
                or record.expires_at <= now
            ):
                record = None
            else:
                record.active += 1
                self._entries.move_to_end(str(token))
        self._unlink(evicted)
        if record is None:
            return None
        try:
            handle = open(record.path, "rb")
        except OSError:
            self.release(str(token))
            return None
        return PreparedExplorerDirectoryArchive(
            token=str(token),
            handle=handle,
            filename=record.filename,
            size=record.size,
            etag=record.etag,
        )

    def release(self, token: str) -> None:
        now = time.monotonic()
        with self._lock:
            record = self._entries.get(str(token or ""))
            if record is not None and record.active:
                record.active -= 1
            evicted = self._evictions_locked(now)
        self._unlink(evicted)

    def clear(self) -> None:
        with self._lock:
            paths = [record.path for record in self._entries.values() if record.active == 0]
            active = OrderedDict(
                (token, record)
                for token, record in self._entries.items()
                if record.active
            )
            self._entries = active
            self._total_bytes = sum(record.size for record in active.values())
        self._unlink(paths)


prepared_directory_archives = PreparedDirectoryArchiveStore()


def _check_archive_deadline(backend: Any, deadline: Optional[float]) -> None:
    if deadline is None:
        return
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise ExplorerDirectoryDownloadTimeoutError(
            "Directory archive took too long to prepare"
        )
    set_timeout = getattr(backend, "set_io_timeout", None)
    if callable(set_timeout):
        set_timeout(remaining)


class _BoundedArchiveFile:
    """Seekable file facade that limits the archive's maximum extent."""

    def __init__(self, handle: BinaryIO, max_bytes: int):
        self._handle = handle
        self._max_bytes = max_bytes

    def write(self, data: bytes) -> int:
        if self._handle.tell() + len(data) > self._max_bytes:
            raise ExplorerDirectoryDownloadLimitError(
                "Directory archive exceeds the 100 MB download limit"
            )
        return self._handle.write(data)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def _archive_entry_name(parent: str, name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Directory contains an invalid entry name")
    return f"{parent}/{name}"


def _directory_archive_plan(
    backend: Any,
    root_path: str,
    directory_path: str,
    archive_root: str,
    *,
    max_bytes: int,
    max_entries: int,
    deadline: Optional[float],
) -> Tuple[List[str], List[Tuple[str, str, Any]], int]:
    """Snapshot archive paths and reject unsupported or oversized trees."""

    directories: List[str] = []
    files: List[Tuple[str, str, Any]] = []
    pending = [(directory_path, archive_root)]
    content_bytes = 0
    entry_count = 0

    while pending:
        _check_archive_deadline(backend, deadline)
        current_path, current_archive_path = pending.pop()
        directories.append(f"{current_archive_path}/")
        current_relative_path = backend.rel_explorer_path(root_path, current_path)
        _root_path, resolved_current_path = backend.resolve_dir(current_relative_path)
        _check_archive_deadline(backend, deadline)
        children = sorted(
            backend.fs_listdir(resolved_current_path), key=lambda item: item[0]
        )
        child_directories = []
        for name, stat_info in children:
            entry_count += 1
            if entry_count > max_entries:
                raise ExplorerDirectoryDownloadLimitError(
                    f"Directory contains more than {max_entries} entries"
                )
            archive_path = _archive_entry_name(current_archive_path, name)
            entry_path = backend.fs_join(resolved_current_path, name)
            kind = stat_info.get("kind")
            if kind == "directory":
                child_directories.append((entry_path, archive_path))
                continue
            if kind != "file":
                raise ValueError(
                    f"Directory contains an unsupported entry: {archive_path}"
                )
            raw_size = stat_info.get("size")
            if raw_size is not None:
                size = int(raw_size)
                if size < 0:
                    raise ValueError(f"Directory entry has an invalid size: {archive_path}")
                content_bytes += size
                if content_bytes > max_bytes:
                    raise ExplorerDirectoryDownloadLimitError(
                        "Directory contents exceed the 100 MB download limit"
                    )
            files.append((entry_path, archive_path, stat_info))
        pending.extend(reversed(child_directories))

    return directories, files, content_bytes


def build_explorer_directory_archive(
    backend: Any,
    requested_path: Any,
    *,
    max_bytes: int,
    chunk_bytes: int,
    max_entries: int = EXPLORER_DIRECTORY_DOWNLOAD_MAX_ENTRIES,
    deadline: Optional[float] = None,
) -> ExplorerDirectoryArchive:
    """Build one root-confined directory ZIP under hard content/output bounds."""

    _check_archive_deadline(backend, deadline)
    root_path, directory_path = backend.resolve_dir(requested_path)
    archive_root = backend.basename(directory_path) or "root"
    directories, files, _known_content_bytes = _directory_archive_plan(
        backend,
        root_path,
        directory_path,
        archive_root,
        max_bytes=max_bytes,
        max_entries=max_entries,
        deadline=deadline,
    )

    archive_handle = tempfile.NamedTemporaryFile(mode="w+b", delete=False)
    temporary_path = archive_handle.name
    bounded_handle = _BoundedArchiveFile(archive_handle, max_bytes)
    content_bytes = 0
    try:
        with zipfile.ZipFile(
            bounded_handle,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=False,
        ) as archive:
            for directory_name in directories:
                _check_archive_deadline(backend, deadline)
                archive.writestr(directory_name, b"")
            for file_path, archive_path, _stat_info in files:
                _check_archive_deadline(backend, deadline)
                relative_path = backend.rel_explorer_path(root_path, file_path)
                _root_path, resolved_file_path = backend.resolve_file(relative_path)
                with contextlib.closing(
                    backend.open_file_stream(resolved_file_path)
                ) as source:
                    with archive.open(archive_path, mode="w", force_zip64=False) as target:
                        while True:
                            _check_archive_deadline(backend, deadline)
                            remaining = max_bytes - content_bytes
                            chunk = source.read(min(chunk_bytes, remaining + 1))
                            if not chunk:
                                break
                            if len(chunk) > remaining:
                                raise ExplorerDirectoryDownloadLimitError(
                                    "Directory contents exceed the 100 MB download limit"
                                )
                            content_bytes += len(chunk)
                            target.write(chunk)
        archive_handle.seek(0, 2)
        archive_size = archive_handle.tell()
        archive_handle.seek(0)
        digest = hashlib.sha256()
        while True:
            _check_archive_deadline(backend, deadline)
            chunk = archive_handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
        archive_handle.seek(0)
        return ExplorerDirectoryArchive(
            handle=archive_handle,
            filename=f"{archive_root}.zip",
            size=archive_size,
            path=str(archive_handle.name),
            etag=digest.hexdigest(),
        )
    except Exception:
        archive_handle.close()
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
