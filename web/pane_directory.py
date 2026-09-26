"""A directory a caller *stated* for a pane, checked on that pane's own machine.

Two transactions start a shell somewhere a caller named rather than somewhere
the pane was already standing: a split with a stated ``directory`` (the new
pane runs on the source pane's machine) and a gated mode switch with one (the
pane is re-rooted where it runs). Both need the same answer to the same
question -- *does this directory exist where the shell will start?* -- and
neither may answer it with the explorer's containment rule. That rule bounds
what a live Files pane can browse into; it is not a limit on where a pane may
be re-rooted, and a root the pane derived for itself is nobody's choice.

So a stated path is resolved on its own merits, on the right machine:

- an SSH pane asks its own host over SFTP, never this one;
- a local pane whose shell is WSL takes a Linux path (``/home/me``) or a
  Windows one; a ``/mnt/<drive>`` or drive path is read from this host's
  filesystem, and any other Linux path is asked of the distribution itself;
- every other local pane is checked with this host's own ``os.path``.

A refusal is a :class:`StatedDirectoryError` -- a ``ValueError``, so every
route that already maps one onto a ``400`` refuses a missing path the same way
-- and names the path and the machine. SFTP transport failures propagate as
the types in ``_sftp_request_error_types()``, which the routes answer ``500``.
Only a *proven* absence refuses: a WSL check that could not run says nothing
about the path, exactly as an agent preflight's ``check_failed`` says nothing
about the binary, and the shell's own ``cd`` then reports it.
"""

import logging
import os
import re
import subprocess
from typing import Any

from web.explorer import (
    _acquire_ssh_sftp,
    _os_error_is_missing_path,
    _release_ssh_sftp,
    _remote_is_directory,
    _remote_path_clean,
    _remote_path_is_absolute,
)
from web.terminal_io import _local_shell_kind

logger = logging.getLogger(__name__)

#: How long a WSL ``test -d`` may take. A cold distribution boots on first
#: use, which is the slow case; the agent preflight allows the same.
WSL_DIRECTORY_CHECK_TIMEOUT_SECONDS = 8

_WINDOWS_DRIVE_PATH = re.compile(r"^(?P<drive>[A-Za-z]):(?:[\\/](?P<rest>.*))?$")
_WSL_MOUNT_PATH = re.compile(r"^/mnt/(?P<drive>[A-Za-z])(?:/(?P<rest>.*))?$")


class StatedDirectoryError(ValueError):
    """A stated directory refused before anything was created or relaunched."""


def pane_machine_label(pane: Any) -> str:
    """Where a pane's shell runs, in the words a directory refusal uses."""
    if str(getattr(pane, "mode", "") or "") == "ssh":
        host = str(getattr(pane, "host", "") or "").strip() or "the remote host"
        return f"{host} (over SSH)"
    if _local_shell_kind(pane) == "wsl":
        distribution = str(getattr(pane, "distribution", "") or "").strip()
        return (
            f"the WSL distribution {distribution}"
            if distribution
            else "the default WSL distribution"
        )
    return "GridVibe's own machine"


def _refusal(path: str, pane: Any, reason: str) -> StatedDirectoryError:
    return StatedDirectoryError(
        f"The directory {path} {reason} on {pane_machine_label(pane)}, where "
        "this pane's shell runs."
    )


def _resolve_remote(pane: Any, raw: str) -> str:
    path = _remote_path_clean(raw)
    if not _remote_path_is_absolute(path):
        raise _refusal(path, pane, "is not an absolute path")
    client = None
    sftp = None
    try:
        client, sftp = _acquire_ssh_sftp(pane)
        try:
            normalized = sftp.normalize(path)
            is_directory = _remote_is_directory(sftp, normalized)
        except OSError as exc:
            if _os_error_is_missing_path(exc) or "no such file" in str(exc).lower():
                raise _refusal(path, pane, "does not exist") from exc
            raise
    finally:
        _release_ssh_sftp(pane, client, sftp)
    if not is_directory:
        raise _refusal(path, pane, "is not a directory")
    return normalized


def _windows_path_for_wsl_mount(path: str) -> str:
    """``/mnt/c/x`` as ``C:\\x``, or ``""`` for any other Linux path."""
    match = _WSL_MOUNT_PATH.match(path)
    if not match:
        return ""
    rest = (match.group("rest") or "").strip("/").replace("/", os.sep)
    return f"{match.group('drive').upper()}:{os.sep}{rest}"


def wsl_directory_exists(path: str, distribution: str = "") -> bool:
    """Ask a WSL distribution whether ``path`` is a directory there.

    ``True`` when the distribution says so *or when the question could not be
    asked* -- only a completed ``test -d`` answering no is an absence. The path
    travels as one argv entry through ``--exec``, so no shell reads it.
    """
    from web.agents import _find_wsl_executable

    executable = _find_wsl_executable()
    if not executable:
        return True
    command = [executable]
    if distribution:
        command.extend(["--distribution", distribution])
    command.extend(["--exec", "test", "-d", path])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=WSL_DIRECTORY_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("WSL directory check could not run: %s", exc)
        return True
    return result.returncode != 1


def _resolve_wsl(pane: Any, raw: str) -> str:
    path = raw.replace("\\", "/") if raw.startswith("/") else raw
    if _WINDOWS_DRIVE_PATH.match(raw):
        if not os.path.isdir(raw):
            raise _refusal(raw, pane, "does not exist")
        return os.path.normpath(raw)
    if not path.startswith("/"):
        raise _refusal(raw, pane, "is not an absolute path")
    mounted = _windows_path_for_wsl_mount(path)
    if mounted:
        if not os.path.isdir(mounted):
            raise _refusal(path, pane, "does not exist")
        return path
    if not wsl_directory_exists(path, str(getattr(pane, "distribution", "") or "")):
        raise _refusal(path, pane, "does not exist")
    return path


def _resolve_local(pane: Any, raw: str) -> str:
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise _refusal(raw, pane, "is not an absolute path")
    resolved = os.path.normpath(os.path.abspath(expanded))
    if not os.path.exists(resolved):
        raise _refusal(raw, pane, "does not exist")
    if not os.path.isdir(resolved):
        raise _refusal(raw, pane, "is not a directory")
    return resolved


def resolve_stated_directory(pane: Any, directory: Any) -> str:
    """The stated ``directory``, resolved where ``pane``'s shell runs, or a refusal.

    ``pane`` is the pane whose machine the shell will start on -- the source of
    a split, or the pane being re-rooted. Returns the path in the form that
    machine's shell is started with.
    """
    raw = str(directory or "").strip()
    if not raw:
        raise StatedDirectoryError("The stated directory is empty.")
    if str(getattr(pane, "mode", "") or "") == "ssh":
        return _resolve_remote(pane, raw)
    if _local_shell_kind(pane) == "wsl":
        return _resolve_wsl(pane, raw)
    return _resolve_local(pane, raw)
