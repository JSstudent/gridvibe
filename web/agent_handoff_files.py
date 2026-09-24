"""The temporary file a large handed-over task travels in.

A task above ``INLINE_TASK_MAX_CHARS`` is not pushed through ``read_handoff``
whole, because the receiving CLI may shorten a large tool result. GridVibe
writes it to a file on the *pane's own machine* instead, and ``read_handoff``
answers with that file's path; the agent opens it with its own file tool, which
pages a large file by itself.

**Written by GridVibe, never through a shell.** A local pane's file is an
ordinary file write and an SSH pane's is an SFTP write on the pane's own
transport, so no length, space or newline in the task can break anything.

**Owner-only, and gone with its handoff.** A local file sits in its own
``0700`` directory as ``0600`` (the Windows user temp directory is already
per-user); a remote one is narrowed and read back exactly as the MCP config
is, and a mode that cannot be proved owner-only is removed again -- the task
then falls back to paged delivery, so a failed write costs the file and never
the task. A crash leaves files behind, so GridVibe sweeps entries older than
:data:`SWEEP_AGE_SECONDS` at start (and a remote host's on its next write):
another install may share the directory and still own newer ones.

Nothing here logs a path: the handoff's id and "the handoff file" are what a
log line may name.
"""

import logging
import os
import re
import shutil
import stat
import tempfile
import time
from typing import Any, Callable, Optional, Tuple

from web.agent_handoffs import HandoffView

logger = logging.getLogger(__name__)

LOCAL_DIRECTORY_NAME = "gridvibe-handoffs"
LOCAL_FILE_NAME = "task.md"

#: Under the remote user's home, beside the tunnel's MCP config.
REMOTE_DIRECTORY = ".gridvibe/handoffs"

#: Old enough that no live GridVibe -- this one or another install sharing the
#: directory -- can still own the entry.
SWEEP_AGE_SECONDS = 24 * 60 * 60

_HANDOFF_ID = re.compile(r"^[0-9a-f]{32}$")
_REMOTE_FILE = re.compile(r"^[0-9a-f]{32}\.md$")

#: Every bit that lets another account read, write or enter.
_FOREIGN_MODE_BITS = 0o077


def handoff_document(view: HandoffView) -> str:
    """A three-line header, a blank line, then the task exactly as written."""
    origin = view.from_title or "another pane"
    agent = f", agent {view.from_agent}" if view.from_agent else ""
    return (
        f"Handed over by GridVibe from pane {origin} ({view.source_session_id}{agent})\n"
        f"Created: {view.created_at}\n"
        f"Note: {view.note}\n"
        "\n"
        f"{view.text}"
    )


#: Replaces the whole directory, for the test suite: it writes and sweeps
#: here, and must never touch a directory a real GridVibe is using.
HANDOFF_DIR_VARIABLE = "GRIDVIBE_HANDOFF_DIR"


def local_handoff_root() -> str:
    """Where this machine's handoff files live.

    ``realpath`` so a Windows 8.3 short name in the temp path (``S891A~1``)
    becomes the long one a WSL shell can open under ``/mnt/<drive>``.
    """
    override = str(os.environ.get(HANDOFF_DIR_VARIABLE) or "").strip()
    if override:
        return os.path.realpath(override)
    return os.path.join(os.path.realpath(tempfile.gettempdir()), LOCAL_DIRECTORY_NAME)


def _owned_private_directory(path: str) -> bool:
    """Create ``path`` if needed and prove only this account can use it.

    On Windows the user temp directory is already per-user and POSIX modes say
    nothing, so existence is the whole check. Elsewhere the directory must be
    a real directory (not a symlink), owned by this account, and narrowed to
    ``0700`` -- a shared ``/tmp`` is exactly where another account could have
    made it first.
    """
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    if os.name == "nt":
        return True
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return False
    try:
        if info.st_mode & _FOREIGN_MODE_BITS:
            os.chmod(path, 0o700)
        return not (os.lstat(path).st_mode & _FOREIGN_MODE_BITS)
    except OSError:
        return False


def write_local_handoff(
    handoff_id: str,
    document: str,
    *,
    root: Optional[str] = None,
) -> Optional[Tuple[str, Callable[[], None]]]:
    """Write one task file on this machine. Returns ``(path, cleanup)``.

    ``None`` on any failure, with nothing left behind: the caller falls back to
    paged delivery.
    """
    if not _HANDOFF_ID.match(str(handoff_id or "")):
        return None
    base = root or local_handoff_root()
    if not _owned_private_directory(base):
        logger.warning("Handoff %s: the local handoff directory is not private", handoff_id)
        return None
    directory = os.path.join(base, handoff_id)

    def cleanup() -> None:
        shutil.rmtree(directory, ignore_errors=True)

    try:
        os.mkdir(directory, 0o700)
    except OSError as exc:
        logger.warning("Handoff %s: could not create its directory (%s)", handoff_id, exc.strerror)
        return None
    path = os.path.join(directory, LOCAL_FILE_NAME)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(document.encode("utf-8"))
        if os.name != "nt" and os.stat(path).st_mode & _FOREIGN_MODE_BITS:
            raise PermissionError("the handoff file is readable by other accounts")
    except (OSError, UnicodeEncodeError) as exc:
        logger.warning(
            "Handoff %s: could not write the handoff file (%s)",
            handoff_id,
            getattr(exc, "strerror", None) or type(exc).__name__,
        )
        cleanup()
        return None
    logger.info("Handoff %s: wrote the local handoff file", handoff_id)
    return path, cleanup


def sweep_local_handoffs(
    *,
    root: Optional[str] = None,
    now: Optional[float] = None,
    max_age: float = SWEEP_AGE_SECONDS,
) -> int:
    """Remove handoff directories a crash left behind. Returns how many.

    Only entries named like a handoff, and only older than ``max_age``: a newer
    one may belong to another GridVibe install sharing this temp directory.
    """
    base = root or local_handoff_root()
    moment = time.time() if now is None else float(now)
    try:
        info = os.lstat(base)
    except OSError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        return 0
    if os.name != "nt" and hasattr(os, "getuid") and info.st_uid != os.getuid():
        return 0
    removed = 0
    try:
        names = os.listdir(base)
    except OSError:
        return 0
    for name in names:
        if not _HANDOFF_ID.match(name):
            continue
        entry = os.path.join(base, name)
        try:
            entry_info = os.lstat(entry)
        except OSError:
            continue
        if not stat.S_ISDIR(entry_info.st_mode):
            continue
        if moment - entry_info.st_mtime <= max_age:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        if not os.path.exists(entry):
            removed += 1
    if removed:
        logger.info("Swept %d stale local handoff file(s)", removed)
    return removed


def write_remote_handoff(sftp: Any, handoff_id: str, document: str) -> str:
    """Write one task file on an SSH pane's host. Returns its path, or ``""``.

    Over the SFTP channel the pane's tunnel already holds, so it lands as the
    user the pane runs as. Fails closed exactly like the MCP config: a file
    whose owner-only mode cannot be proved is removed again, and ``""`` sends
    the task to paged delivery.
    """
    from web.ssh_tunnel import (
        _restricted_to_owner,
        ensure_remote_directory,
        resolve_remote_home,
    )

    if sftp is None or not _HANDOFF_ID.match(str(handoff_id or "")):
        return ""
    home = resolve_remote_home(sftp).rstrip("/")
    if not home:
        return ""
    directory = f"{home}/{REMOTE_DIRECTORY}"
    path = f"{directory}/{handoff_id}.md"
    # Both levels, each narrowed and read back: `~/.gridvibe` outlives any one
    # pane, and a directory other accounts can list names every handoff in it.
    if not ensure_remote_directory(sftp, directory):
        return ""
    if not ensure_remote_directory(sftp, path):
        return ""
    try:
        with sftp.open(path, "wb") as handle:
            handle.write(document.encode("utf-8"))
    except Exception as exc:
        logger.warning(
            "Handoff %s: could not write the remote handoff file (%s)",
            handoff_id,
            type(exc).__name__,
        )
        remove_remote_handoff(sftp, path)
        return ""
    if not _restricted_to_owner(sftp, path, 0o600, label="the handoff file"):
        remove_remote_handoff(sftp, path)
        return ""
    sweep_remote_handoffs(sftp, directory, path)
    logger.info("Handoff %s: wrote the remote handoff file", handoff_id)
    return path


def remove_remote_handoff(sftp: Any, path: str) -> None:
    if sftp is None or not path:
        return
    try:
        sftp.remove(path)
    except Exception:
        logger.debug("Could not remove a remote handoff file", exc_info=True)


def sweep_remote_handoffs(
    sftp: Any,
    directory: str,
    reference_path: str,
    *,
    max_age: float = SWEEP_AGE_SECONDS,
) -> int:
    """Remove this host's stale handoff files, judged by *that host's* clock.

    ``reference_path`` is the file just written, whose modification time is
    the remote host's "now" -- so a clock that disagrees with this machine's
    cannot sweep a live file or keep a dead one.
    """
    try:
        now = float(sftp.stat(reference_path).st_mtime)
        entries = sftp.listdir_attr(directory)
    except Exception:
        return 0
    removed = 0
    for entry in entries or []:
        name = str(getattr(entry, "filename", "") or "")
        if not _REMOTE_FILE.match(name):
            continue
        modified = getattr(entry, "st_mtime", None)
        if modified is None or now - float(modified) <= max_age:
            continue
        try:
            sftp.remove(f"{directory}/{name}")
            removed += 1
        except Exception:
            continue
    if removed:
        logger.info("Swept %d stale remote handoff file(s)", removed)
    return removed
