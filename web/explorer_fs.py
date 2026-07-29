"""Root-confined copy and delete policy shared by local and SFTP explorers.

Backends in :mod:`web.explorer` provide small filesystem mechanics; this module
owns every validation, limit, naming, concurrency, cleanup, and response rule.
No path is passed to a shell, copies stream in bounded chunks, and link leaves
are always handled with lstat/remove semantics rather than followed.
"""

import errno
import logging
import os
import re
import stat
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from web.explorer import (
    ExplorerRouteError,
    _explorer_claim_keys_conflict,
    _explorer_path_claims,
    _fs_revision,
    _fs_root_revision,
)

logger = logging.getLogger(__name__)

EXPLORER_FS_MAX_ENTRIES = 10_000
EXPLORER_FS_MAX_DEPTH = 32
EXPLORER_COPY_MAX_BYTES = 512 * 1024 * 1024
EXPLORER_COPY_CHUNK_BYTES = 1024 * 1024
EXPLORER_COPY_NAME_ATTEMPTS = 100

_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:")
_FILESYSTEM_ERRORS = (OSError, RuntimeError, ValueError)


class ExplorerFsError(ExplorerRouteError):
    """Base mutation error with an explicit retry-safety signal."""

    def __init__(self, message: str, *, mutated: bool = False, **details: Any):
        super().__init__(message, mutated=mutated, **details)


class ExplorerFsUnsupportedEntryError(ExplorerFsError):
    code = "unsupported_entry_type"


class ExplorerFsInvalidDestinationError(ExplorerFsError):
    code = "invalid_destination"


class ExplorerFsProtectedPathError(ExplorerFsError):
    status_code = 403
    code = "protected_path"


class ExplorerFsPermissionError(ExplorerFsError):
    status_code = 403
    code = "permission_denied"


class ExplorerFsSourceMissingError(ExplorerFsError):
    status_code = 404
    code = "source_missing"


class ExplorerFsRootChangedError(ExplorerFsError):
    status_code = 409
    code = "root_changed"


class ExplorerFsEntryChangedError(ExplorerFsError):
    status_code = 409
    code = "entry_changed"


class ExplorerFsDestinationExistsError(ExplorerFsError):
    status_code = 409
    code = "destination_exists"


class ExplorerFsOperationInProgressError(ExplorerFsError):
    status_code = 409
    code = "operation_in_progress"


class ExplorerFsCopyLimitError(ExplorerFsError):
    status_code = 413
    code = "copy_limit_exceeded"


class ExplorerFsIoError(ExplorerFsError):
    status_code = 500
    code = "io_error"


@dataclass(frozen=True)
class _ScannedEntry:
    path: str
    relative_path: str
    depth: int
    stat_info: Dict[str, Any]


def _normalize_rel(requested_path: Any) -> str:
    """Normalize one non-empty client relative path or reject it."""
    if not isinstance(requested_path, str) or not requested_path or "\x00" in requested_path:
        raise ExplorerFsError("A non-empty relative explorer path is required")
    raw = requested_path.replace("\\", "/")
    if raw.startswith("/") or _WINDOWS_ABSOLUTE_RE.match(raw):
        raise ExplorerFsError("Explorer mutation paths must be relative to the configured root")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ExplorerFsError("Explorer mutation paths cannot contain empty, dot, or parent segments")
    return "/".join(parts)


def _normalize_destination(requested_path: Any) -> str:
    """Normalize a destination directory, allowing only ``""`` for the root."""
    if requested_path == "":
        return ""
    return _normalize_rel(requested_path)


def _copy_name_for_attempt(name: str, attempt: int) -> str:
    """Return the original name, then extension-aware ``-Copy`` variants."""
    if attempt <= 0:
        return name
    stem, extension = os.path.splitext(name)
    suffix = "-Copy" if attempt == 1 else f"-Copy-{attempt}"
    return f"{stem}{suffix}{extension}"


def _is_missing_error(exc: BaseException) -> bool:
    return isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) == errno.ENOENT


def _is_exists_error(exc: BaseException) -> bool:
    return isinstance(exc, FileExistsError) or getattr(exc, "errno", None) == errno.EEXIST


def _is_permission_error(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "errno", None) in {
        errno.EACCES,
        errno.EPERM,
    }


def _safe_path_label(relative_path: str) -> str:
    return relative_path or "the explorer root"


def _raise_io_error(
    message: str,
    exc: BaseException,
    *,
    mutated: bool,
    **details: Any,
) -> None:
    """Map expected filesystem failures without reflecting raw remote text."""
    if _is_permission_error(exc):
        raise ExplorerFsPermissionError(message, mutated=mutated, **details) from exc
    raise ExplorerFsIoError(message, mutated=mutated, **details) from exc


def _current_root(backend: Any, supplied_revision: Any) -> Tuple[str, str]:
    if not isinstance(supplied_revision, str) or not supplied_revision:
        raise ExplorerFsError("A root revision is required")
    try:
        root_path = backend.root_directory()
    except (ValueError, OSError, RuntimeError) as exc:
        _raise_io_error("Could not verify the explorer root", exc, mutated=False)
    revision = _fs_root_revision(root_path)
    if supplied_revision != revision:
        raise ExplorerFsRootChangedError(
            "The explorer root changed; refresh before trying again"
        )
    return root_path, revision


def resolve_mutation_target(
    backend: Any, requested_path: Any
) -> Tuple[str, str, str, str, str]:
    """Resolve parent then join a literal leaf, which is never canonicalized."""
    relative_path = _normalize_rel(requested_path)
    parent_relative, _, leaf = relative_path.rpartition("/")
    try:
        root_path, parent_path = backend.resolve_candidate(parent_relative)
    except ValueError as exc:
        raise ExplorerFsError(str(exc)) from exc
    except _FILESYSTEM_ERRORS as exc:
        _raise_io_error("Could not resolve the selected entry", exc, mutated=False)
    try:
        parent_stat = backend.fs_lstat(parent_path)
    except _FILESYSTEM_ERRORS as exc:
        _raise_io_error("Could not inspect the selected entry's parent", exc, mutated=False)
    if parent_stat is None or parent_stat.get("kind") != "directory":
        raise ExplorerFsInvalidDestinationError(
            "The selected entry's parent is no longer a directory"
        )
    if not backend.path_inside_root(root_path, parent_path):
        raise ExplorerFsError("Explorer path must stay inside the configured root")
    return (
        root_path,
        parent_path,
        leaf,
        backend.fs_join(parent_path, leaf),
        relative_path,
    )


def _resolve_destination(backend: Any, requested_path: Any) -> Tuple[str, str, str]:
    relative_path = _normalize_destination(requested_path)
    try:
        root_path, destination_path = backend.resolve_dir(relative_path)
    except ValueError as exc:
        raise ExplorerFsInvalidDestinationError(str(exc)) from exc
    except _FILESYSTEM_ERRORS as exc:
        _raise_io_error("Could not resolve the destination folder", exc, mutated=False)
    if not backend.path_inside_root(root_path, destination_path):
        raise ExplorerFsInvalidDestinationError(
            "The destination must stay inside the configured root"
        )
    return root_path, destination_path, relative_path


def _check_revision(
    backend: Any,
    target_path: str,
    expected_revision: Any,
    relative_path: str,
) -> Dict[str, Any]:
    if not isinstance(expected_revision, str) or not expected_revision:
        raise ExplorerFsError("An entry revision is required")
    try:
        stat_info = backend.fs_lstat(target_path)
    except _FILESYSTEM_ERRORS as exc:
        _raise_io_error(
            f"Could not inspect {_safe_path_label(relative_path)}",
            exc,
            mutated=False,
        )
    if stat_info is None:
        raise ExplorerFsSourceMissingError(
            f"{_safe_path_label(relative_path)} no longer exists"
        )
    if _fs_revision(stat_info) != expected_revision:
        raise ExplorerFsEntryChangedError(
            f"{_safe_path_label(relative_path)} changed; refresh before trying again"
        )
    return stat_info


def _limit_error(message: str, relative_path: str) -> ExplorerFsCopyLimitError:
    return ExplorerFsCopyLimitError(
        message,
        path=relative_path,
        max_entries=EXPLORER_FS_MAX_ENTRIES,
        max_depth=EXPLORER_FS_MAX_DEPTH,
        max_bytes=EXPLORER_COPY_MAX_BYTES,
    )


def _scan_tree(
    backend: Any,
    source_path: str,
    source_stat: Dict[str, Any],
    source_relative: str,
    *,
    allow_special_entries: bool,
    enforce_byte_limit: bool = True,
) -> List[_ScannedEntry]:
    """Iteratively pre-scan one tree before mutation, never following links."""
    scanned: List[_ScannedEntry] = []
    total_bytes = 0
    stack = [(source_path, "", 0, source_stat)]
    while stack:
        path, relative, depth, stat_info = stack.pop()
        display_path = (
            source_relative if not relative else f"{source_relative}/{relative}"
        )
        kind = stat_info.get("kind")
        if depth > EXPLORER_FS_MAX_DEPTH:
            raise _limit_error(
                f"Copy/delete depth exceeds the limit at {display_path}",
                display_path,
            )
        if not allow_special_entries and kind not in {"file", "directory"}:
            raise ExplorerFsUnsupportedEntryError(
                f"Cannot copy unsupported entry type at {display_path}",
                path=display_path,
            )
        scanned.append(_ScannedEntry(path, relative, depth, stat_info))
        if len(scanned) > EXPLORER_FS_MAX_ENTRIES:
            raise _limit_error(
                f"Copy/delete entry count exceeds the limit at {display_path}",
                display_path,
            )
        if kind == "file" and enforce_byte_limit:
            total_bytes += max(0, int(stat_info.get("size") or 0))
            if total_bytes > EXPLORER_COPY_MAX_BYTES:
                raise _limit_error(
                    f"Copy size exceeds the limit at {display_path}",
                    display_path,
                )
        if kind != "directory":
            continue
        try:
            children = backend.fs_listdir(path)
        except _FILESYSTEM_ERRORS as exc:
            _raise_io_error(
                f"Could not inspect {_safe_path_label(display_path)}",
                exc,
                mutated=False,
            )
        for name, child_stat in reversed(children):
            child_relative = f"{relative}/{name}" if relative else name
            stack.append(
                (
                    backend.fs_join(path, name),
                    child_relative,
                    depth + 1,
                    child_stat,
                )
            )
    return scanned


def _join_relative(backend: Any, root_path: str, relative_path: str) -> str:
    result = root_path
    for part in relative_path.split("/"):
        if part:
            result = backend.fs_join(result, part)
    return result


def _apply_metadata_best_effort(
    backend: Any, target_path: str, stat_info: Dict[str, Any], relative_path: str
) -> None:
    mode = stat_info.get("mode")
    if mode is not None:
        try:
            backend.fs_chmod(target_path, stat.S_IMODE(mode))
        except (*_FILESYSTEM_ERRORS, NotImplementedError):
            logger.debug("Explorer copy could not preserve mode for %s", relative_path)
    mtime = stat_info.get("mtime")
    if mtime is not None:
        try:
            backend.fs_utime(target_path, mtime)
        except (*_FILESYSTEM_ERRORS, NotImplementedError):
            logger.debug("Explorer copy could not preserve mtime for %s", relative_path)


def _copy_file_to_handle(
    backend: Any,
    source: _ScannedEntry,
    destination_path: str,
    destination_handle: Any,
    display_path: str,
) -> None:
    expected_size = max(0, int(source.stat_info.get("size") or 0))
    written_bytes = 0
    try:
        current_stat = backend.fs_lstat(source.path)
        if current_stat is None:
            raise ExplorerFsSourceMissingError(f"{display_path} disappeared during copy")
        if current_stat.get("kind") != "file":
            raise ExplorerFsUnsupportedEntryError(
                f"{display_path} changed to an unsupported entry type during copy",
                path=display_path,
            )
        for chunk in backend.fs_read_chunks(source.path, EXPLORER_COPY_CHUNK_BYTES):
            next_written_bytes = written_bytes + len(chunk)
            if next_written_bytes > expected_size:
                raise ExplorerFsEntryChangedError(
                    f"{display_path} changed during copy",
                    path=display_path,
                )
            destination_handle.write(chunk)
            written_bytes = next_written_bytes
        if written_bytes != expected_size:
            raise ExplorerFsEntryChangedError(
                f"{display_path} changed during copy",
                path=display_path,
            )
        flush = getattr(destination_handle, "flush", None)
        if callable(flush):
            flush()
    finally:
        destination_handle.close()
    _apply_metadata_best_effort(
        backend, destination_path, source.stat_info, display_path
    )


def _populate_reserved_directory(
    backend: Any,
    scanned: List[_ScannedEntry],
    destination_root: str,
    source_relative: str,
) -> None:
    ordered = sorted(
        scanned[1:],
        key=lambda entry: (entry.depth, entry.stat_info.get("kind") != "directory"),
    )
    for entry in ordered:
        destination = _join_relative(backend, destination_root, entry.relative_path)
        display = f"{source_relative}/{entry.relative_path}"
        if entry.stat_info.get("kind") == "directory":
            backend.fs_mkdir_exclusive(destination)
            continue
        handle = backend.fs_create_exclusive(destination)
        _copy_file_to_handle(backend, entry, destination, handle, display)

    # Apply directory metadata after their children so their mtimes are useful.
    directories = [
        entry for entry in scanned if entry.stat_info.get("kind") == "directory"
    ]
    for entry in sorted(directories, key=lambda item: item.depth, reverse=True):
        destination = (
            destination_root
            if not entry.relative_path
            else _join_relative(backend, destination_root, entry.relative_path)
        )
        display = (
            source_relative
            if not entry.relative_path
            else f"{source_relative}/{entry.relative_path}"
        )
        _apply_metadata_best_effort(backend, destination, entry.stat_info, display)


def _remove_tree_safely(backend: Any, root_path: str) -> None:
    """Remove one reserved tree iteratively, lstat'ing every leaf."""
    stack = [(root_path, False)]
    while stack:
        path, visited = stack.pop()
        stat_info = backend.fs_lstat(path)
        if stat_info is None:
            continue
        if stat_info.get("kind") != "directory":
            backend.fs_remove_file(path)
            continue
        if visited:
            backend.fs_rmdir(path)
            continue
        stack.append((path, True))
        for name, _child_stat in backend.fs_listdir(path):
            stack.append((backend.fs_join(path, name), False))


def _reserve_copy_destination(
    backend: Any,
    destination_parent: str,
    source_name: str,
    kind: str,
) -> Tuple[str, Optional[Any]]:
    for attempt in range(EXPLORER_COPY_NAME_ATTEMPTS):
        candidate = backend.fs_join(
            destination_parent, _copy_name_for_attempt(source_name, attempt)
        )
        try:
            if kind == "directory":
                backend.fs_mkdir_exclusive(candidate)
                return candidate, None
            return candidate, backend.fs_create_exclusive(candidate)
        except _FILESYSTEM_ERRORS as exc:
            if _is_exists_error(exc):
                continue
            _raise_io_error(
                "Could not reserve a copy destination", exc, mutated=False
            )
    raise ExplorerFsDestinationExistsError(
        "Could not allocate a non-conflicting copy name"
    )


def paste_explorer_entry_payload(
    backend: Any,
    *,
    root_revision: Any,
    source_path: Any,
    source_revision: Any,
    destination_directory: Any,
    session_id: str,
) -> Dict[str, Any]:
    """Copy one file/tree inside a single live explorer root without overwrite."""
    current_root, _revision = _current_root(backend, root_revision)
    (
        source_root,
        _source_parent,
        _source_leaf,
        source_absolute,
        source_relative,
    ) = resolve_mutation_target(backend, source_path)
    destination_root, destination_absolute, _destination_relative = _resolve_destination(
        backend, destination_directory
    )
    if source_root != current_root or destination_root != current_root:
        raise ExplorerFsRootChangedError(
            "The explorer root changed; refresh before trying again"
        )

    source_stat = _check_revision(
        backend, source_absolute, source_revision, source_relative
    )
    source_kind = source_stat.get("kind")
    if source_kind not in {"file", "directory"}:
        raise ExplorerFsUnsupportedEntryError(
            f"Cannot copy unsupported entry type at {source_relative}",
            path=source_relative,
        )
    if backend.fs_path_is_same_or_descendant(source_absolute, destination_absolute):
        raise ExplorerFsInvalidDestinationError(
            "A folder cannot be copied into itself or one of its descendants"
        )

    claim_keys = (
        backend.fs_claim_key(source_absolute),
        backend.fs_claim_key(destination_absolute),
    )
    with _explorer_path_claims(
        claim_keys,
        error_type=ExplorerFsOperationInProgressError,
    ):
        source_stat = _check_revision(
            backend, source_absolute, source_revision, source_relative
        )
        scanned = _scan_tree(
            backend,
            source_absolute,
            source_stat,
            source_relative,
            allow_special_entries=False,
        )
        # Catch a top-level swap during a potentially long scan.
        _check_revision(backend, source_absolute, source_revision, source_relative)

        destination_path: Optional[str] = None
        destination_handle: Optional[Any] = None
        try:
            destination_path, destination_handle = _reserve_copy_destination(
                backend,
                destination_absolute,
                backend.basename(source_absolute),
                source_kind,
            )
            if source_kind == "file":
                _copy_file_to_handle(
                    backend,
                    scanned[0],
                    destination_path,
                    destination_handle,
                    source_relative,
                )
                destination_handle = None
            else:
                _populate_reserved_directory(
                    backend, scanned, destination_path, source_relative
                )
        except ExplorerRouteError:
            if destination_handle is not None:
                try:
                    destination_handle.close()
                except Exception:
                    pass
            if destination_path is not None:
                try:
                    _remove_tree_safely(backend, destination_path)
                except Exception as cleanup_error:
                    label = backend.rel_explorer_path(current_root, destination_path)
                    logger.warning(
                        "Explorer copy cleanup incomplete for %s", label
                    )
                    raise ExplorerFsIoError(
                        "Copy failed and its incomplete destination could not be fully removed",
                        mutated=True,
                        cleanup_incomplete=True,
                        path=label,
                    ) from cleanup_error
            raise
        except _FILESYSTEM_ERRORS as exc:
            if destination_handle is not None:
                try:
                    destination_handle.close()
                except Exception:
                    pass
            cleanup_incomplete = False
            if destination_path is not None:
                try:
                    _remove_tree_safely(backend, destination_path)
                except Exception:
                    cleanup_incomplete = True
                    label = backend.rel_explorer_path(current_root, destination_path)
                    logger.warning(
                        "Explorer copy cleanup incomplete for %s", label
                    )
            if cleanup_incomplete:
                raise ExplorerFsIoError(
                    "Copy failed and its incomplete destination could not be fully removed",
                    mutated=True,
                    cleanup_incomplete=True,
                ) from exc
            _raise_io_error(
                "Copy failed; its reserved destination was removed",
                exc,
                mutated=False,
            )

    destination_relative = backend.rel_explorer_path(current_root, destination_path)
    return {
        "ok": True,
        "source_path": source_relative,
        "destination_path": destination_relative,
        "type": "directory" if source_kind == "directory" else "file",
        "entry_kind": source_kind,
    }


def _contains_protected_git_directory(
    backend: Any, scanned: Iterable[_ScannedEntry]
) -> Optional[str]:
    for entry in scanned:
        if (
            entry.stat_info.get("kind") == "directory"
            and backend.basename(entry.path).lower() == ".git"
        ):
            return entry.relative_path
    return None


def _remove_scanned_tree(
    backend: Any, scanned: List[_ScannedEntry], removal_root: str
) -> None:
    """Remove only pre-scanned relative entries, deepest paths first."""
    for entry in sorted(scanned, key=lambda item: item.depth, reverse=True):
        path = (
            removal_root
            if not entry.relative_path
            else _join_relative(backend, removal_root, entry.relative_path)
        )
        current_stat = backend.fs_lstat(path)
        if current_stat is None:
            continue
        if current_stat.get("kind") == "directory":
            backend.fs_rmdir(path)
        else:
            backend.fs_remove_file(path)


def delete_explorer_entry_payload(
    backend: Any,
    *,
    root_revision: Any,
    path: Any,
    base_revision: Any,
    recursive: Any,
    session_id: str,
) -> Dict[str, Any]:
    """Permanently delete one root-confined entry, never following a link leaf."""
    if not isinstance(recursive, bool):
        raise ExplorerFsError("The recursive flag must be true or false")
    current_root, _revision = _current_root(backend, root_revision)
    target_root, parent_path, _leaf, target_path, relative_path = (
        resolve_mutation_target(backend, path)
    )
    if target_root != current_root:
        raise ExplorerFsRootChangedError(
            "The explorer root changed; refresh before trying again"
        )
    target_stat = _check_revision(
        backend, target_path, base_revision, relative_path
    )
    entry_kind = target_stat.get("kind") or "other"
    if backend.fs_path_is_same_or_descendant(target_path, current_root):
        raise ExplorerFsProtectedPathError("The explorer root cannot be deleted")
    if entry_kind == "directory" and backend.basename(target_path).lower() == ".git":
        raise ExplorerFsProtectedPathError(
            "GridVibe will not delete a .git directory because it contains repository history"
        )

    with _explorer_path_claims(
        (backend.fs_claim_key(target_path),),
        error_type=ExplorerFsOperationInProgressError,
    ):
        target_stat = _check_revision(
            backend, target_path, base_revision, relative_path
        )
        entry_kind = target_stat.get("kind") or "other"
        if entry_kind != "directory":
            try:
                backend.fs_remove_file(target_path)
            except _FILESYSTEM_ERRORS as exc:
                if _is_missing_error(exc):
                    raise ExplorerFsSourceMissingError(
                        f"{relative_path} no longer exists"
                    ) from exc
                _raise_io_error(
                    f"Could not delete {relative_path}", exc, mutated=False
                )
        else:
            try:
                children = backend.fs_listdir(target_path)
            except _FILESYSTEM_ERRORS as exc:
                _raise_io_error(
                    f"Could not inspect {relative_path}", exc, mutated=False
                )
            if not children:
                try:
                    backend.fs_rmdir(target_path)
                except _FILESYSTEM_ERRORS as exc:
                    _raise_io_error(
                        f"Could not delete {relative_path}", exc, mutated=False
                    )
            else:
                if not recursive:
                    raise ExplorerFsError(
                        "The directory is not empty; confirm recursive deletion first"
                    )
                scanned = _scan_tree(
                    backend,
                    target_path,
                    target_stat,
                    relative_path,
                    allow_special_entries=True,
                    enforce_byte_limit=False,
                )
                protected = _contains_protected_git_directory(backend, scanned)
                if protected is not None:
                    protected_path = (
                        relative_path
                        if not protected
                        else f"{relative_path}/{protected}"
                    )
                    raise ExplorerFsProtectedPathError(
                        f"GridVibe will not delete {protected_path} because it contains repository history"
                    )

                removal_root = target_path
                quarantined = False
                quarantine_path = backend.fs_join(
                    parent_path, f".gv-delete-{uuid.uuid4().hex}"
                )
                try:
                    backend.fs_rename(target_path, quarantine_path)
                    removal_root = quarantine_path
                    quarantined = True
                except _FILESYSTEM_ERRORS:
                    logger.debug(
                        "Explorer delete quarantine rename unavailable for %s",
                        relative_path,
                    )
                try:
                    _remove_scanned_tree(backend, scanned, removal_root)
                except _FILESYSTEM_ERRORS as exc:
                    remaining_label = (
                        backend.rel_explorer_path(current_root, removal_root)
                        if quarantined
                        else relative_path
                    )
                    logger.warning(
                        "Explorer delete incomplete for %s", remaining_label
                    )
                    raise ExplorerFsIoError(
                        "Delete stopped before every entry could be removed",
                        mutated=True,
                        partial=True,
                        path=remaining_label,
                    ) from exc

    return {
        "ok": True,
        "deleted_path": relative_path,
        "type": "directory" if entry_kind == "directory" else "file",
        "entry_kind": entry_kind,
    }


__all__ = [
    "EXPLORER_COPY_CHUNK_BYTES",
    "EXPLORER_COPY_MAX_BYTES",
    "EXPLORER_COPY_NAME_ATTEMPTS",
    "EXPLORER_FS_MAX_DEPTH",
    "EXPLORER_FS_MAX_ENTRIES",
    "ExplorerFsCopyLimitError",
    "ExplorerFsError",
    "ExplorerFsInvalidDestinationError",
    "ExplorerFsIoError",
    "ExplorerFsOperationInProgressError",
    "ExplorerFsProtectedPathError",
    "ExplorerFsUnsupportedEntryError",
    "_copy_name_for_attempt",
    "_explorer_claim_keys_conflict",
    "_fs_revision",
    "_fs_root_revision",
    "_normalize_rel",
    "_scan_tree",
    "delete_explorer_entry_payload",
    "paste_explorer_entry_payload",
    "resolve_mutation_target",
]
