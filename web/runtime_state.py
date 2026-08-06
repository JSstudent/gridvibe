"""Workspace-shape snapshot persistence for restore-after-restart.

Deep-dive feature 10.5: live shells cannot survive a backend restart by
design, but the workspace *shape* (groups + per-session launch config) can.
Schema v3 stores one slot per workspace id (``workspaces`` dict); with
multi-workspace there is one slot per captured workspace. **Exactly two
writers** capture shape: the autosave timer (``capture_live_workspaces``) and
the user's explicit Save Workspace action (``capture_workspace`` with origin
``"manual"``). Renaming a workspace deliberately does *not* capture — it
changes the live label, and the next capture by either real writer persists it.
A slot also records which group was in front (``active_group_id``) so the
restore reopens the workspace on it rather than on whichever group happens to
be newest. The snapshot never contains passwords — a restored SSH session
re-authenticates with keys or a saved-session password. ``runtime_state.json``
is local state and gitignored.

Everything goes through one :class:`RuntimeStateStore`, which owns the file
path, the process lock, the cross-process file lock, the schema, and the
per-workspace commit metadata. Four ownership rules keep the file honest:

* **Ordering.** Every capture takes a monotonic in-process ticket *and* reads
  the durable per-workspace revision *before* it reads the live manager, so a
  snapshot taken before an explicit close/forget is rejected at commit time
  instead of resurrecting the slot it was meant to remove. The durable
  revisions survive a restart and order two *processes* against each other;
  the tickets order two threads inside one process.
* **Single writer per commit.** The complete read-modify-replace runs under an
  OS-level lock on ``<state>.lock``, so a second GridVibe process cannot
  interleave with (and silently discard) this one's update.
* **Honest acknowledgement.** A failed write raises
  :class:`RuntimeStatePersistenceError` instead of being logged and swallowed,
  so a save/forget route can answer "not stored" rather than claim success for
  data that never reached the disk.
* **Isolation.** The path comes from ``GRIDVIBE_RUNTIME_STATE_PATH`` when set,
  with a ``GRIDVIBE_TEST_MODE`` guard that refuses the canonical production
  path outright — a test run must never share the user's restore file.

A file that cannot be parsed, or that carries a schema version this build does
not understand, is **quarantined** (moved aside with a timestamp) and the
last-good ``<state>.bak`` written before the previous commit is used instead —
never silently converted into an empty state that the next capture overwrites.
"""

import json
import logging
import math
import os
import shutil
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from web.paths import BASE_DIR
from web.workspaces import DEFAULT_WORKSPACE_ID, normalize_workspace_id

logger = logging.getLogger(__name__)

try:  # POSIX advisory locking
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # Windows mandatory byte-range locking
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

# The one file a production process owns. ``RUNTIME_STATE_PATH`` starts there
# but is deliberately overridable: a test process (or a second GridVibe run)
# points ``GRIDVIBE_RUNTIME_STATE_PATH`` at its own file so it can never
# read-modify-replace the user's real workspace snapshots.
PRODUCTION_STATE_PATH = os.path.join(BASE_DIR, "runtime_state.json")
RUNTIME_STATE_PATH = os.environ.get("GRIDVIBE_RUNTIME_STATE_PATH") or PRODUCTION_STATE_PATH

SCHEMA_VERSION = 3
# Versions this build can read. Anything else (notably a *newer* file written
# by a future build) is quarantined rather than reinterpreted or overwritten.
SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3)
RESTORABLE_ORIGINS = ("auto", "manual")
# The restore offer is deliberately permanent (no maximum age), so with N
# workspaces "one slot" would grow into "one slot per workspace ever autosaved"
# and only Forget would ever shrink it. Bound the automatic ones at the single
# write point; a slot the user explicitly saved is pinned, so it keeps its
# permanent-offer promise.
MAX_AUTO_WORKSPACE_SLOTS = 12
# Durable ordering entries for workspaces that no longer have a slot are
# tombstones. They only matter while a pre-clear capture may still be in
# flight, so the newest few are enough and the map stays bounded.
MAX_TOMBSTONES = 64
# How long one process waits for another to finish its read-modify-replace.
STATE_LOCK_TIMEOUT_SECONDS = 10.0
NATIVE_ZOOM_FACTOR_MIN = 0.25
NATIVE_ZOOM_FACTOR_MAX = 5.0


class RuntimeStatePathError(RuntimeError):
    """Raised when this process must not touch the production state file."""


class RuntimeStatePersistenceError(RuntimeError):
    """Raised when an intended runtime-state revision did not reach the disk.

    Callers must treat this as "not stored": a route answers with a retryable
    5xx instead of a success payload, and the autosave timer keeps the last
    good file rather than reporting a commit.
    """


def normalize_native_zoom_factor(value: Any) -> Optional[float]:
    """Normalize the optional desktop session-window zoom stored with a slot."""
    if value is None or value == "":
        return None
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(factor) or not (
        NATIVE_ZOOM_FACTOR_MIN <= factor <= NATIVE_ZOOM_FACTOR_MAX
    ):
        return None
    return round(factor, 3)


# TerminalSession launch fields worth replaying through POST /api/sessions.
# `password` is deliberately absent; ids/status/timestamps are per-run state.
_SESSION_SNAPSHOT_FIELDS = (
    "host",
    "directory",
    "username",
    "port",
    "initial_command",
    "initial_command_mode",
    "agent_selection",
    "custom_agent",
    "agent_auto_mode",
    "title",
    "distribution",
    "use_wsl",
    "use_powershell",
    "startup_mode",
    "explorer_root_directory",
    "explorer_tree_open",
    "explorer_git_open",
    "explorer_search_open",
    "explorer_open_tabs",
    "explorer_active_tab",
    "explorer_tab_views",
    "explorer_md_preset",
    "explorer_md_font",
    "explorer_source_font",
    "explorer_theme",
    "browser_tabs",
    "browser_active_tab",
)


def _snapshot_session(session: Any) -> Dict[str, Any]:
    """Return the replayable launch config for one live session."""
    data = session if isinstance(session, dict) else session.to_dict()
    return {key: data.get(key) for key in _SESSION_SNAPSHOT_FIELDS}


def _snapshot_group(group: Any, sessions: List[Any]) -> Dict[str, Any]:
    """Return one password-free runtime-state group payload."""
    data = group if isinstance(group, dict) else group.to_dict()
    return {
        "group_id": data.get("group_id"),
        "name": data.get("name"),
        "connection_mode": data.get("connection_mode"),
        "layout": data.get("layout"),
        "workspace_layout": data.get("workspace_layout"),
        # No surface_mode: chrome density is a live global setting, so a
        # restore must never replay the value a group launched with.
        "saved_session_id": data.get("saved_session_id"),
        "sessions": [_snapshot_session(session) for session in sessions],
    }


def _looks_like_timestamp_name(name: str) -> bool:
    """True for the legacy auto-generated ``Session HH:MM:SS`` group names."""
    parts = name.rsplit(" ", 1)
    if len(parts) != 2:
        return False
    digits = parts[1].split(":")
    return len(digits) == 3 and all(part.isdigit() for part in digits)


def _derive_workspace_label(groups: List[Dict[str, Any]]) -> str:
    """Compute a stable human-facing label for a captured workspace.

    Precedence: the first group's real name → a session host → a session
    directory basename → a neutral "Workspace". Never a bare timestamp —
    ``Session HH:MM:SS`` names are auto-generated and meaningless a day later.
    """
    for group in groups:
        name = str(group.get("name") or "").strip()
        if name and not _looks_like_timestamp_name(name):
            return name
    for group in groups:
        for session in group.get("sessions") or []:
            host = str(session.get("host") or "").strip()
            if host:
                return host
    for group in groups:
        for session in group.get("sessions") or []:
            directory = str(session.get("directory") or "").strip()
            if directory:
                basename = os.path.basename(directory.rstrip("/\\"))
                if basename:
                    return basename
    return "Workspace"


def _empty_state() -> Dict[str, Any]:
    return {"version": SCHEMA_VERSION, "workspaces": {}, "revisions": {}}


def _slot_is_pinned(slot: Dict[str, Any]) -> bool:
    """True when the user explicitly saved this workspace at some point.

    Pinning is durable metadata separate from ``origin``, which names the
    writer of the *current* shape. A slot the user saved by hand keeps its
    permanent-offer promise even after the timer has refreshed its shape a
    dozen times. ``origin == "manual"`` is the legacy (schema v2) form of the
    same fact and is still honoured for files written before the split.
    """
    return slot.get("manually_saved_at") is not None or slot.get("origin") == "manual"


def _pane_count(groups: List[Dict[str, Any]]) -> int:
    """Total panes across captured groups — safe, shape-only diagnostics."""
    return sum(len(group.get("sessions") or []) for group in groups)


# ==================== File primitives ====================


class _CrossProcessStateLock:
    """Exclusive OS-level lock over one runtime-state file.

    The in-process lock orders threads inside one interpreter; it says nothing
    about a second GridVibe process doing its own read-modify-replace. Both
    would read, both would modify, and the later ``os.replace`` would discard
    the other's update wholesale. A sidecar ``<state>.lock`` (never the state
    file itself, which is replaced rather than written in place) makes the
    complete operation single-writer across processes.

    Degrades to a no-op with one warning where neither locking primitive is
    available — a missing lock must not make GridVibe unable to save at all.
    """

    _unsupported_warned = False

    def __init__(self, state_path: str, timeout: float = STATE_LOCK_TIMEOUT_SECONDS):
        self._lock_path = f"{state_path}.lock"
        self._timeout = timeout
        self._fd: Optional[int] = None

    def __enter__(self) -> "_CrossProcessStateLock":
        if fcntl is None and msvcrt is None:  # pragma: no cover - exotic platform
            if not _CrossProcessStateLock._unsupported_warned:
                _CrossProcessStateLock._unsupported_warned = True
                logger.warning(
                    "No file-locking primitive available; runtime state is "
                    "protected within this process only"
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
                    raise RuntimeStatePersistenceError(
                        "Another process is holding the runtime-state lock "
                        f"({self._lock_path}); the workspace was not saved"
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


def _quarantine_state_file(state_path: str, reason: str) -> None:
    """Move an unreadable/unsupported state file aside, keeping the evidence."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    quarantine_path = f"{state_path}.corrupt-{stamp}"
    if os.path.exists(quarantine_path):
        quarantine_path = f"{quarantine_path}-{uuid.uuid4().hex[:8]}"
    try:
        os.replace(state_path, quarantine_path)
    except OSError as exc:
        logger.error("Could not quarantine unreadable runtime state: %s", exc)
        return
    logger.error(
        "Quarantined unreadable runtime state (%s) as %s",
        reason,
        os.path.basename(quarantine_path),
    )


def _parse_state(data: Any) -> Optional[Dict[str, Any]]:
    """Return migrated v3 state, or ``None`` when the blob is unusable.

    ``None`` means "quarantine this file": either it is not a JSON object, or
    it carries a schema version this build does not understand, or a version
    it does understand with the wrong shape. A *readable but empty* file is
    not corrupt and yields empty state.
    """
    if not isinstance(data, dict):
        return None

    version = data.get("version")
    if version is not None and version not in SUPPORTED_SCHEMA_VERSIONS:
        return None

    if version in (2, 3):
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, dict):
            return None
        state = {
            "version": SCHEMA_VERSION,
            "workspaces": workspaces,
            "revisions": _normalize_revisions(data.get("revisions")),
        }
        if version == 2:
            # v2 conflated "the user saved this" with "the last writer was the
            # user". Slots that carried origin "manual" were pinned, so they
            # gain the durable pin the split introduced.
            for slot in workspaces.values():
                if isinstance(slot, dict) and slot.get("origin") == "manual":
                    slot.setdefault("manually_saved_at", slot.get("saved_at"))
        return state

    # One-time v1 → v3 migration: a legacy single blob wraps into the
    # "default" slot with origin "auto" so an existing user still gets the
    # restore offer once; the next capture rewrites the file at the current
    # schema version.
    groups = data.get("groups")
    if not isinstance(groups, list) or not groups:
        return _empty_state()
    state = _empty_state()
    state["workspaces"][DEFAULT_WORKSPACE_ID] = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "label": _derive_workspace_label(groups),
        "origin": "auto",
        "saved_at": (
            data.get("saved_at")
            if isinstance(data.get("saved_at"), (int, float))
            else time.time()
        ),
        # v1 predates the active-group hint; a restore falls back to the
        # workspace's own group ordering, exactly as it did before.
        "active_group_id": "",
        "groups": groups,
    }
    return state


def _normalize_revisions(value: Any) -> Dict[str, Dict[str, Any]]:
    """Coerce the durable ordering map, dropping hand-edited nonsense."""
    if not isinstance(value, dict):
        return {}
    revisions: Dict[str, Dict[str, Any]] = {}
    for workspace_id, entry in value.items():
        if not isinstance(entry, dict):
            continue
        revision = entry.get("revision")
        if not isinstance(revision, int) or revision < 0:
            continue
        kind = entry.get("kind")
        revisions[str(workspace_id)] = {
            "revision": revision,
            "kind": "clear" if kind == "clear" else "commit",
        }
    return revisions


def _read_state_locked(state_path: str) -> Dict[str, Any]:
    """Read (and migrate) the state file. Caller holds both state locks.

    An unreadable or unsupported file is quarantined and the last-good backup
    written before the previous commit is used in its place, so a single bad
    write can never be laundered into an empty state that the next capture
    overwrites for good.
    """
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError) as exc:
        _quarantine_state_file(state_path, f"unreadable: {exc}")
        return _recover_from_backup(state_path)

    state = _parse_state(data)
    if state is None:
        _quarantine_state_file(
            state_path, f"unsupported schema version {data.get('version')!r}"
            if isinstance(data, dict)
            else "not a JSON object"
        )
        return _recover_from_backup(state_path)
    return state


def _recover_from_backup(state_path: str) -> Dict[str, Any]:
    """Return the last-good backup's state, or empty state when there is none."""
    backup_path = f"{state_path}.bak"
    try:
        with open(backup_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError) as exc:
        logger.error("Last-good runtime state is unusable too: %s", exc)
        return _empty_state()
    state = _parse_state(data)
    if state is None:
        logger.error("Last-good runtime state carries an unsupported schema")
        return _empty_state()
    logger.warning(
        "Recovered runtime state from the last-good backup (%d workspace slots)",
        len(state.get("workspaces", {})),
    )
    return state


def _write_state_locked(state: Dict[str, Any], state_path: str) -> None:
    """Atomically persist the state. Caller holds both state locks.

    Raises :class:`RuntimeStatePersistenceError` when the intended revision did
    not reach the disk — a full disk, a permission error, an antivirus lock, or
    a failed replace must never be reported to the user as a saved workspace.
    The previous file is copied to ``<state>.bak`` first, so a torn or corrupt
    successor always has a last-good predecessor to recover from.
    """
    directory = os.path.dirname(os.path.abspath(state_path)) or "."
    temp_path = os.path.join(
        directory,
        f".{os.path.basename(state_path)}.{uuid.uuid4().hex}.tmp",
    )
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _back_up_current_state(state_path)
        os.replace(temp_path, state_path)
    except Exception as exc:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        logger.error("Could not persist runtime workspace state: %s", exc)
        raise RuntimeStatePersistenceError(
            f"Could not persist the workspace snapshot: {exc}"
        ) from exc


def _back_up_current_state(state_path: str) -> None:
    """Copy the current state file to ``<state>.bak`` (best effort)."""
    if not os.path.exists(state_path):
        return
    try:
        shutil.copyfile(state_path, f"{state_path}.bak")
    except OSError as exc:
        # A missing backup weakens recovery but must not fail the commit.
        logger.debug("Could not refresh the runtime-state backup: %s", exc)


# ==================== The store ====================


class RuntimeStateStore:
    """The one owner of ``runtime_state.json``: path, locks, schema, ordering.

    Constructed with a *path resolver* rather than a path so the process-wide
    default can still be redirected (tests, a second GridVibe run) without any
    caller reaching for a module global. Every public method takes the process
    lock, then the cross-process file lock, for the complete
    read-modify-replace — and never holds ``SessionManager.lock`` while doing
    so: the live snapshot is taken and released first.
    """

    def __init__(self, path_resolver: Optional[Callable[[], str]] = None):
        self._resolve_path = path_resolver or (lambda: RUNTIME_STATE_PATH)
        self._lock = threading.Lock()
        self._ticket_seq = 0
        # workspace id -> (newest in-process ticket committed, "commit"|"clear")
        self._commits: Dict[str, Tuple[int, str]] = {}

    # ---------- path + ordering ----------

    def state_path(self) -> str:
        """Return the state file to use, refusing production state in tests.

        ``GRIDVIBE_TEST_MODE`` marks a process that must never own the user's
        ``runtime_state.json``: a test run that reached the canonical path
        would read-modify-replace a live app's snapshots (and vice versa).
        Failing loudly beats corrupting the file restore-after-restart needs.
        """
        path = self._resolve_path()
        if os.environ.get("GRIDVIBE_TEST_MODE") and os.path.abspath(
            path
        ) == os.path.abspath(PRODUCTION_STATE_PATH):
            raise RuntimeStatePathError(
                "Refusing to use the production runtime_state.json in test mode; "
                "set GRIDVIBE_RUNTIME_STATE_PATH or patch RUNTIME_STATE_PATH."
            )
        return path

    def _next_ticket(self) -> int:
        """Take the monotonic in-process ticket for one capture/clear."""
        with self._lock:
            self._ticket_seq += 1
            return self._ticket_seq

    def observed_revisions(self) -> Dict[str, int]:
        """Read the durable per-workspace revision *before* a live snapshot.

        This is the cross-process half of the ordering rule: the revision a
        capture observed here is compared against the revision on disk at
        commit time, so a close or a newer capture performed by *any* process
        in between is detected even though the in-process tickets know nothing
        about it.
        """
        state_path = self.state_path()
        with self._lock, _CrossProcessStateLock(state_path):
            state = _read_state_locked(state_path)
        return {
            workspace_id: entry["revision"]
            for workspace_id, entry in state.get("revisions", {}).items()
        }

    def _is_stale_locked(
        self,
        workspace_id: str,
        ticket: int,
        origin: str,
        observed_revision: int,
        revisions: Dict[str, Dict[str, Any]],
    ) -> bool:
        """True when this capture must not commit. Caller holds both locks.

        A capture is stale against anything committed after it read the live
        manager — in this process (tickets) or any other (durable revisions):

        * a **clear** always wins — the workspace was explicitly closed or
          forgotten after this snapshot was read, so writing it back would make
          a closed workspace restorable again;
        * a newer **commit** wins over an autosave capture, so an older timer
          tick cannot overwrite the shape a later capture already stored. An
          explicit Save Workspace still wins over a newer autosave: it is the
          user's deliberate act, and only a close/forget may override it.
        """
        last = self._commits.get(workspace_id)
        if last is not None:
            last_ticket, last_kind = last
            if last_ticket > ticket and (last_kind == "clear" or origin != "manual"):
                return True
        entry = revisions.get(workspace_id)
        if entry is not None and entry["revision"] > observed_revision:
            if entry["kind"] == "clear" or origin != "manual":
                return True
        return False

    @staticmethod
    def _bump_revision(
        revisions: Dict[str, Dict[str, Any]],
        workspace_id: str,
        kind: str,
        observed_revision: int = 0,
    ) -> int:
        """Advance one workspace's durable revision and return the new value."""
        entry = revisions.get(workspace_id)
        current = entry["revision"] if entry else 0
        revision = max(current, observed_revision) + 1
        revisions[workspace_id] = {"revision": revision, "kind": kind}
        return revision

    def _record_commit_locked(self, workspace_id: str, ticket: int, kind: str) -> None:
        """Record the newest ticket that reached the file. Caller holds locks."""
        last = self._commits.get(workspace_id)
        newest = max(ticket, last[0]) if last else ticket
        self._commits[workspace_id] = (newest, kind)

    @staticmethod
    def _prune_revisions(state: Dict[str, Any]) -> None:
        """Bound the durable ordering map to live slots plus recent tombstones."""
        revisions = state.get("revisions", {})
        workspaces = state.get("workspaces", {})
        tombstones = [
            (entry["revision"], workspace_id)
            for workspace_id, entry in revisions.items()
            if workspace_id not in workspaces
        ]
        if len(tombstones) <= MAX_TOMBSTONES:
            return
        tombstones.sort(reverse=True)
        for _, workspace_id in tombstones[MAX_TOMBSTONES:]:
            del revisions[workspace_id]

    # ---------- capture ----------

    def _build_slot(
        self,
        workspace_id: str,
        groups: List[Dict[str, Any]],
        previous_slot: Dict[str, Any],
        origin: str,
        label: Optional[str],
        workspace_label: str,
        active_group_id: str,
        saved_at: float,
        native_zoom_factor: Optional[float],
    ) -> Dict[str, Any]:
        """Assemble one stored slot from a captured shape."""
        captured_group_ids = {group["group_id"] for group in groups}
        slot: Dict[str, Any] = {
            "workspace_id": workspace_id,
            "label": (
                str(label or "").strip()
                or workspace_label
                or _derive_workspace_label(groups)
            ),
            # The writer of *this* shape, nothing more. Whether the user ever
            # saved the workspace by hand lives in "manually_saved_at", so a
            # later autosave refresh can be honest about who wrote the shape
            # without silently un-pinning the slot.
            "origin": "manual" if origin == "manual" else "auto",
            "saved_at": saved_at,
            "active_group_id": (
                active_group_id if active_group_id in captured_group_ids else ""
            ),
            "groups": groups,
        }
        manually_saved_at = (
            saved_at
            if origin == "manual"
            else previous_slot.get("manually_saved_at")
            or (previous_slot.get("saved_at") if previous_slot.get("origin") == "manual" else None)
        )
        if isinstance(manually_saved_at, (int, float)):
            slot["manually_saved_at"] = manually_saved_at
        if native_zoom_factor is None:
            native_zoom_factor = normalize_native_zoom_factor(
                previous_slot.get("native_zoom_factor")
            )
        if native_zoom_factor is not None:
            slot["native_zoom_factor"] = native_zoom_factor
        return slot

    def capture_workspace(
        self,
        session_manager: Any,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        origin: str = "auto",
        label: Optional[str] = None,
        active_group_id: Optional[str] = None,
        native_zoom_factor: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """Capture one workspace's shape and persist its slot. See module docs."""
        workspace_id = normalize_workspace_id(workspace_id)
        ticket = self._next_ticket()
        observed = self.observed_revisions().get(workspace_id, 0)
        live_snapshot = session_manager.snapshot_live_workspaces().get(workspace_id)
        if not live_snapshot:
            return None
        groups = [
            _snapshot_group(group, list(group.get("sessions") or []))
            for group in live_snapshot.get("groups") or []
            if isinstance(group, dict) and group.get("sessions")
        ]
        if not groups:
            return None

        if active_group_id is None:
            active_group_id = live_snapshot.get("active_group_id")
        active_group_id = str(active_group_id or "").strip()
        normalized_zoom = normalize_native_zoom_factor(native_zoom_factor)
        workspace_label = str(live_snapshot.get("label") or "").strip()

        state_path = self.state_path()
        with self._lock, _CrossProcessStateLock(state_path):
            state = _read_state_locked(state_path)
            revisions = state.setdefault("revisions", {})
            if self._is_stale_locked(workspace_id, ticket, origin, observed, revisions):
                logger.debug(
                    "Dropped a stale %s capture of workspace %s", origin, workspace_id
                )
                return None
            workspaces = state.setdefault("workspaces", {})
            previous_slot = workspaces.get(workspace_id)
            previous_slot = previous_slot if isinstance(previous_slot, dict) else {}
            slot = self._build_slot(
                workspace_id=workspace_id,
                groups=groups,
                previous_slot=previous_slot,
                origin=origin,
                label=label,
                workspace_label=workspace_label,
                active_group_id=active_group_id,
                saved_at=time.time(),
                native_zoom_factor=normalized_zoom,
            )
            slot["revision"] = self._bump_revision(
                revisions, workspace_id, "commit", observed
            )
            workspaces[workspace_id] = slot
            _evict_excess_auto_slots(workspaces)
            self._prune_revisions(state)
            _write_state_locked(state, state_path)
            self._record_commit_locked(workspace_id, ticket, "commit")
        _log_commit(workspace_id, origin, slot)
        return slot

    def capture_live_workspaces(
        self,
        session_manager: Any,
        origin: str = "auto",
    ) -> Dict[str, Dict[str, Any]]:
        """Capture every non-empty live workspace with one consistent file write.

        The manager serializes live state during one lock hold and returns
        before this acquires the file locks, preserving the documented lock
        order. Existing sibling slots for closed workspaces and saved native
        zoom values are retained. Workspaces closed, forgotten, or captured
        again between this tick's snapshot and its commit are skipped
        individually, so one stale tick can neither resurrect a closed
        workspace nor undo a newer capture.
        """
        ticket = self._next_ticket()
        observed = self.observed_revisions()
        live_snapshots = session_manager.snapshot_live_workspaces()
        if not live_snapshots:
            return {}

        saved_at = time.time()
        state_path = self.state_path()
        with self._lock, _CrossProcessStateLock(state_path):
            state = _read_state_locked(state_path)
            workspaces = state.setdefault("workspaces", {})
            revisions = state.setdefault("revisions", {})
            stored_slots: Dict[str, Dict[str, Any]] = {}
            for workspace_id, snapshot in live_snapshots.items():
                observed_revision = observed.get(workspace_id, 0)
                if self._is_stale_locked(
                    workspace_id, ticket, origin, observed_revision, revisions
                ):
                    logger.debug(
                        "Dropped a stale %s capture of workspace %s",
                        origin,
                        workspace_id,
                    )
                    continue
                groups = [
                    _snapshot_group(group, list(group.get("sessions") or []))
                    for group in snapshot.get("groups") or []
                    if isinstance(group, dict) and group.get("sessions")
                ]
                if not groups:
                    continue
                previous_slot = workspaces.get(workspace_id)
                previous_slot = previous_slot if isinstance(previous_slot, dict) else {}
                slot = self._build_slot(
                    workspace_id=workspace_id,
                    groups=groups,
                    previous_slot=previous_slot,
                    origin=origin,
                    label=None,
                    workspace_label=str(snapshot.get("label") or "").strip(),
                    active_group_id=str(snapshot.get("active_group_id") or "").strip(),
                    saved_at=saved_at,
                    native_zoom_factor=None,
                )
                slot["revision"] = self._bump_revision(
                    revisions, workspace_id, "commit", observed_revision
                )
                workspaces[workspace_id] = slot
                stored_slots[workspace_id] = slot
            if stored_slots:
                _evict_excess_auto_slots(workspaces)
                self._prune_revisions(state)
                _write_state_locked(state, state_path)
                for workspace_id in stored_slots:
                    self._record_commit_locked(workspace_id, ticket, "commit")
        for workspace_id, slot in stored_slots.items():
            _log_commit(workspace_id, origin, slot)
        return stored_slots

    # ---------- read + clear ----------

    def load_restorable_workspace(
        self, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> Optional[Dict[str, Any]]:
        """Return the saved slot iff it has groups and a restorable origin.

        The offer is permanent — there is deliberately no maximum age.
        """
        workspace_id = normalize_workspace_id(workspace_id)
        state_path = self.state_path()
        with self._lock, _CrossProcessStateLock(state_path):
            state = _read_state_locked(state_path)
        slot = state.get("workspaces", {}).get(workspace_id)
        if not isinstance(slot, dict):
            return None
        groups = slot.get("groups")
        if not isinstance(groups, list) or not groups:
            return None
        if slot.get("origin") not in RESTORABLE_ORIGINS:
            return None
        # Re-validate the hint on read: the file is local state a user may edit,
        # and an id naming no stored group must degrade to "no preference",
        # never send the restore looking for a group it will not create.
        active_group_id = str(slot.get("active_group_id") or "").strip()
        stored_group_ids = {
            str(group.get("group_id") or "")
            for group in groups
            if isinstance(group, dict)
        }
        slot["active_group_id"] = (
            active_group_id if active_group_id in stored_group_ids else ""
        )
        # Hand-edited or older local state degrades to "no zoom preference".
        slot["native_zoom_factor"] = normalize_native_zoom_factor(
            slot.get("native_zoom_factor")
        )
        return slot

    def clear_workspace(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
        """Remove one workspace slot, preserving siblings.

        This is the **Forget** action: it deletes the workspace *snapshot* only
        — saved session presets live in a separate store
        (``saved_sessions.json``) and other slots may reference the same
        preset, so they are never touched. The file itself is kept (at the
        current schema version) even when the last slot is removed.
        Idempotent: ``False`` means the slot was already gone.

        The durable tombstone is recorded even when there was nothing to
        delete: a capture already holding a pre-close snapshot of this
        workspace — in this process or another — must be rejected whether or
        not that snapshot had reached the file yet.

        It is only *written* when there is already a state file, though. Group
        close is one of the callers, and a close must never conjure a
        ``runtime_state.json`` the user never saved into (group events are
        deliberately not snapshot writers). With no file on disk there is no
        persisted slot for any process to resurrect from, and the in-process
        ticket still orders this process's own captures against the clear.
        """
        workspace_id = normalize_workspace_id(workspace_id)
        ticket = self._next_ticket()
        state_path = self.state_path()
        with self._lock, _CrossProcessStateLock(state_path):
            had_state_file = os.path.exists(state_path)
            state = _read_state_locked(state_path)
            revisions = state.setdefault("revisions", {})
            self._bump_revision(revisions, workspace_id, "clear")
            existed = workspace_id in state.get("workspaces", {})
            if existed:
                del state["workspaces"][workspace_id]
            self._prune_revisions(state)
            if existed or had_state_file:
                _write_state_locked(state, state_path)
            self._record_commit_locked(workspace_id, ticket, "clear")
        logger.debug(
            "Runtime-state clear workspace=%s existed=%s", workspace_id, existed
        )
        return existed

    def list_restorable_workspaces(self) -> List[Dict[str, Any]]:
        """Return credential-free summaries for all valid restorable slots."""
        state_path = self.state_path()
        with self._lock, _CrossProcessStateLock(state_path):
            state = _read_state_locked(state_path)

        summaries = []
        for workspace_id, slot in state.get("workspaces", {}).items():
            if not isinstance(slot, dict):
                continue
            try:
                normalized_id = normalize_workspace_id(workspace_id)
            except ValueError:
                continue
            groups = slot.get("groups")
            if (
                not isinstance(groups, list)
                or not groups
                or slot.get("origin") not in RESTORABLE_ORIGINS
            ):
                continue
            valid_groups = [group for group in groups if isinstance(group, dict)]
            if not valid_groups:
                continue
            summaries.append(
                {
                    "workspace_id": normalized_id,
                    "label": (
                        str(slot.get("label") or "").strip()
                        or _derive_workspace_label(valid_groups)
                    ),
                    "origin": slot.get("origin"),
                    # When the user last saved this workspace by hand; None for
                    # a slot only ever written by the timer.
                    "manually_saved_at": (
                        slot.get("manually_saved_at")
                        if isinstance(slot.get("manually_saved_at"), (int, float))
                        else (
                            slot.get("saved_at")
                            if slot.get("origin") == "manual"
                            else None
                        )
                    ),
                    "saved_at": slot.get("saved_at"),
                    "group_count": len(valid_groups),
                    "pane_count": _pane_count(valid_groups),
                }
            )
        return sorted(
            summaries,
            key=lambda summary: (
                -(
                    summary["saved_at"]
                    if isinstance(summary["saved_at"], (int, float))
                    else 0
                ),
                summary["workspace_id"],
            ),
        )


def _log_commit(workspace_id: str, origin: str, slot: Dict[str, Any]) -> None:
    """Log one committed capture with shape-only metadata.

    Deliberately free of hosts, directories, commands, and credentials: enough
    to reconstruct *who wrote what, in which order*, and nothing about where
    the user connects.
    """
    logger.debug(
        "Runtime-state commit workspace=%s writer=%s revision=%s pinned=%s "
        "groups=%d panes=%d",
        workspace_id,
        origin,
        slot.get("revision"),
        _slot_is_pinned(slot),
        len(slot.get("groups") or []),
        _pane_count(slot.get("groups") or []),
    )


def _evict_excess_auto_slots(workspaces: Dict[str, Any]) -> None:
    """Keep only the newest ``MAX_AUTO_WORKSPACE_SLOTS`` unpinned slots.

    Applied at the single write point, oldest-first, and only to slots the user
    never explicitly saved — an explicit Save Workspace pins its slot for good,
    whichever writer refreshed its shape last. Caller holds both state locks.
    """
    auto_slots = [
        (workspace_id, slot)
        for workspace_id, slot in workspaces.items()
        if isinstance(slot, dict) and not _slot_is_pinned(slot)
    ]
    if len(auto_slots) <= MAX_AUTO_WORKSPACE_SLOTS:
        return

    auto_slots.sort(
        key=lambda item: (
            item[1].get("saved_at") if isinstance(item[1].get("saved_at"), (int, float)) else 0,
            item[0],
        ),
        reverse=True,
    )
    evicted = [workspace_id for workspace_id, _ in auto_slots[MAX_AUTO_WORKSPACE_SLOTS:]]
    for workspace_id in evicted:
        del workspaces[workspace_id]
    logger.debug("Evicted %d oldest auto workspace slots", len(evicted))


# ==================== Process-wide store + module API ====================

# One owner for this process. It resolves ``RUNTIME_STATE_PATH`` lazily so a
# test (or a second GridVibe run) can redirect the module global and still get
# a store that honours it, without any caller passing a path around.
_default_store = RuntimeStateStore(lambda: RUNTIME_STATE_PATH)


def get_runtime_state_store() -> RuntimeStateStore:
    """Return the process-wide runtime-state store."""
    return _default_store


def capture_workspace(
    session_manager: Any,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
    origin: str = "auto",
    label: Optional[str] = None,
    active_group_id: Optional[str] = None,
    native_zoom_factor: Any = None,
) -> Optional[Dict[str, Any]]:
    """Capture one workspace's shape from the live manager and persist its slot.

    Read-modify-writes only that workspace's slot so sibling slots survive.
    Returns the stored slot dict, or ``None`` when the workspace has no live
    groups (the existing slot, if any, is left untouched — a slot is only ever
    overwritten by a non-empty capture, never cleared by the timer) or when the
    capture lost its ordering race against a close/forget or a newer capture.

    ``active_group_id`` names the group the restore should reopen on; ``None``
    (the autosave timer's case) asks the manager for its live hint. Either way
    it only survives if it names a group this capture actually stored, so a
    restore never targets a group that was skipped for having no sessions.

    ``native_zoom_factor`` is optional desktop-window state. An explicit manual
    save supplies it; captures without one (notably the autosave timer)
    preserve the value already stored in this workspace's slot.

    Raises :class:`RuntimeStatePersistenceError` when the slot could not be
    written — the caller must not report a successful save.
    """
    return _default_store.capture_workspace(
        session_manager,
        workspace_id=workspace_id,
        origin=origin,
        label=label,
        active_group_id=active_group_id,
        native_zoom_factor=native_zoom_factor,
    )


def capture_live_workspaces(
    session_manager: Any,
    origin: str = "auto",
) -> Dict[str, Dict[str, Any]]:
    """Capture every non-empty live workspace with one consistent file write."""
    return _default_store.capture_live_workspaces(session_manager, origin=origin)


def load_restorable_workspace(
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Optional[Dict[str, Any]]:
    """Return the saved slot iff it has groups and a restorable origin."""
    return _default_store.load_restorable_workspace(workspace_id)


def clear_workspace(workspace_id: str = DEFAULT_WORKSPACE_ID) -> bool:
    """Forget one workspace snapshot; ``False`` when there was none to remove."""
    return _default_store.clear_workspace(workspace_id)


def list_restorable_workspaces() -> List[Dict[str, Any]]:
    """Return credential-free summaries for all valid restorable slots."""
    return _default_store.list_restorable_workspaces()
