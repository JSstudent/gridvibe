"""Bounded directory archive building for Explorer downloads."""

from __future__ import annotations

import contextlib
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any, BinaryIO, List, Tuple

EXPLORER_DIRECTORY_DOWNLOAD_MAX_ENTRIES = 10_000


class ExplorerDirectoryDownloadLimitError(ValueError):
    """Raised before a directory archive can exceed a download bound."""


@dataclass(frozen=True)
class ExplorerDirectoryArchive:
    """A completed, seekable archive whose handle is owned by the caller."""

    handle: BinaryIO
    filename: str
    size: int


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
) -> Tuple[List[str], List[Tuple[str, str, Any]], int]:
    """Snapshot archive paths and reject unsupported or oversized trees."""

    directories: List[str] = []
    files: List[Tuple[str, str, Any]] = []
    pending = [(directory_path, archive_root)]
    content_bytes = 0
    entry_count = 0

    while pending:
        current_path, current_archive_path = pending.pop()
        directories.append(f"{current_archive_path}/")
        current_relative_path = backend.rel_explorer_path(root_path, current_path)
        _root_path, resolved_current_path = backend.resolve_dir(current_relative_path)
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
) -> ExplorerDirectoryArchive:
    """Build one root-confined directory ZIP under hard content/output bounds."""

    root_path, directory_path = backend.resolve_dir(requested_path)
    archive_root = backend.basename(directory_path) or "root"
    directories, files, _known_content_bytes = _directory_archive_plan(
        backend,
        root_path,
        directory_path,
        archive_root,
        max_bytes=max_bytes,
        max_entries=max_entries,
    )

    archive_handle = tempfile.TemporaryFile(mode="w+b")
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
                archive.writestr(directory_name, b"")
            for file_path, archive_path, _stat_info in files:
                relative_path = backend.rel_explorer_path(root_path, file_path)
                _root_path, resolved_file_path = backend.resolve_file(relative_path)
                with contextlib.closing(
                    backend.open_file_stream(resolved_file_path)
                ) as source:
                    with archive.open(archive_path, mode="w", force_zip64=False) as target:
                        while True:
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
        return ExplorerDirectoryArchive(
            handle=archive_handle,
            filename=f"{archive_root}.zip",
            size=archive_size,
        )
    except Exception:
        archive_handle.close()
        raise
