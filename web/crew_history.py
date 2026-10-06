"""Crew links restored after a restart: inert history, kept out of ``ResultStore``.

``web/agent_results.py`` holds the live assignments between agent panes and is
never persisted. A restart ends every one of them, but the board a person was
looking at is worth showing again: who handed a task to whom, and how each round
ended. This module is that memory. It holds two things:

* **Pure translations** between the dashboard's link shape and the form stored
  in a runtime-state slot. :func:`translate_for_capture` turns live and held
  links into entries whose endpoints are snapshot coordinates (group id, pane
  index), because session ids do not survive a restart.
  :func:`translate_for_restore` validates entries read back from a slot and maps
  those coordinates to the new session ids.
* **A small in-memory store** of the restored links, installed after a
  workspace restore and read by the dashboard beside the live ones.

A restored link is history, never work in flight. A ``working`` link is stored
as ``ended`` with the ``restarted`` key, and restored links are never put in
``ResultStore``, so ``wait_for_results``, ``collect``, the follow-up gate and
eviction cannot see them. They carry ``restored: True`` and a fresh ``link_id``,
and never the report text, the receipt or the handoff id, which are not
:data:`~web.agent_results.LINK_FIELDS`.

No Flask, no I/O, and one lock of its own that is never held across a call out.
"""

import logging
import secrets
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from web.agent_handoffs import validate_task_label
from web.agent_results import (
    _ENDED_REASONS,
    ENDED,
    LINK_FIELDS,
    MAX_ASSIGNMENTS,
    REPORT_STATUSES,
    REPORTED,
    WORKING,
)

logger = logging.getLogger(__name__)

#: The end-reason key a ``working`` link is stored with: the process that held
#: the work went away. Also a key of ``agent_results._ENDED_REASONS``.
RESTARTED = "restarted"

#: Every end-reason key a stored link may carry, or ``""`` for a reported one.
#: ``undeliverable`` and ``other`` are keys the store hands out that have no
#: sentence of their own in ``_ENDED_REASONS``.
KNOWN_REASONS = frozenset(_ENDED_REASONS) | {"undeliverable", "other", ""}

#: The states a stored link may be in. ``working`` is never stored.
STORED_STATES = (REPORTED, ENDED)

#: Fields that are public text on a stored link, bounded so a hand-edited file
#: cannot grow the slot.
_MAX_TIMESTAMP_CHARS = 64
_MAX_ROUND = 1_000_000
_MAX_GROUP_ID_CHARS = 128

#: The :data:`~web.agent_results.LINK_FIELDS` a stored entry carries as they are:
#: everything but the link id (minted fresh on restore) and the two session ids
#: (replaced by coordinates).
_PLAIN_FIELDS = tuple(
    name
    for name in LINK_FIELDS
    if name not in ("link_id", "requester_session_id", "worker_session_id")
)

Coordinate = Tuple[str, int]


def _plain_fields(link: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The stored fields of one link, checked and normalised, or ``None``.

    Types are checked, never coerced: a field of the wrong type refuses the
    whole link. ``working`` becomes ``ended`` with :data:`RESTARTED`.
    """
    state = link.get("state")
    if state == WORKING:
        state = ENDED
        reason = RESTARTED
    else:
        reason = link.get("reason", "")
    if state not in STORED_STATES:
        return None
    if not isinstance(reason, str) or reason not in KNOWN_REASONS:
        return None
    status = link.get("status", "")
    if not isinstance(status, str) or (status and status not in REPORT_STATUSES):
        return None
    read = link.get("read", False)
    collected = link.get("collected", False)
    if not isinstance(read, bool) or not isinstance(collected, bool):
        return None
    stamps = []
    for name in ("handed_at", "reported_at"):
        stamp = link.get(name, "")
        if not isinstance(stamp, str) or len(stamp) > _MAX_TIMESTAMP_CHARS:
            return None
        stamps.append(stamp)
    round_number = link.get("round", 1)
    if (
        isinstance(round_number, bool)
        or not isinstance(round_number, int)
        or not 1 <= round_number <= _MAX_ROUND
    ):
        return None
    label = link.get("label", "")
    if not isinstance(label, str):
        return None
    if label:
        try:
            label = validate_task_label(label, task="-")
        except Exception:
            return None
    return {
        "state": state,
        "read": read,
        "status": status,
        "collected": collected,
        "handed_at": stamps[0],
        "reported_at": stamps[1],
        "reason": reason,
        "round": round_number,
        "label": label,
    }


def _valid_coordinate(value: Any) -> Optional[Coordinate]:
    if not isinstance(value, Mapping):
        return None
    group = value.get("group")
    pane = value.get("pane")
    if not isinstance(group, str) or not group or len(group) > _MAX_GROUP_ID_CHARS:
        return None
    if isinstance(pane, bool) or not isinstance(pane, int) or pane < 0:
        return None
    return group, pane


def _pair_key(link: Mapping[str, Any]) -> Tuple[str, str]:
    return (
        str(link.get("requester_session_id") or ""),
        str(link.get("worker_session_id") or ""),
    )


def translate_for_capture(
    links: Iterable[Mapping[str, Any]],
    pane_coords: Mapping[str, Coordinate],
    agent_panes: Iterable[str],
) -> List[Dict[str, Any]]:
    """Stored entries for the links of one captured workspace.

    ``links`` are dashboard links (live and held history, oldest first);
    ``pane_coords`` maps each session id in the workspace to its (snapshot group
    id, pane index); ``agent_panes`` are the session ids whose pane is an agent.
    A link is kept only when both endpoints are agent panes of this workspace.
    Per (requester, worker) pair only the newest link survives, a live link
    winning over a restored one whatever their order. The result keeps the
    input's order and holds at most :data:`MAX_ASSIGNMENTS` entries, the newest.
    Links that cannot be stored are skipped, never raised.
    """
    agents = frozenset(str(item) for item in agent_panes)
    chosen: Dict[Tuple[str, str], Tuple[int, Mapping[str, Any]]] = {}
    for position, link in enumerate(links):
        if not isinstance(link, Mapping):
            continue
        requester, worker = _pair_key(link)
        if not requester or not worker or requester == worker:
            continue
        if requester not in agents or worker not in agents:
            continue
        if requester not in pane_coords or worker not in pane_coords:
            continue
        current = chosen.get((requester, worker))
        if current is not None and not current[1].get("restored") and link.get("restored"):
            continue
        chosen[(requester, worker)] = (position, link)
    entries: List[Dict[str, Any]] = []
    for _position, link in sorted(chosen.values(), key=lambda item: item[0]):
        fields = _plain_fields(link)
        if fields is None:
            continue
        requester, worker = _pair_key(link)
        entry: Dict[str, Any] = {
            "requester": _coordinate_entry(pane_coords[requester]),
            "worker": _coordinate_entry(pane_coords[worker]),
        }
        entry.update(fields)
        entries.append(entry)
    return entries[-MAX_ASSIGNMENTS:]


def _coordinate_entry(coordinate: Coordinate) -> Dict[str, Any]:
    return {"group": str(coordinate[0]), "pane": int(coordinate[1])}


def translate_for_restore(
    entries: Iterable[Any],
    coord_to_session: Mapping[Coordinate, str],
) -> List[Dict[str, Any]]:
    """Links, in the form :func:`install` takes, for the entries of one slot.

    ``coord_to_session`` maps (snapshot group id, pane index) to the session
    id the restore gave that pane. An entry that is not valid, or whose
    endpoint does not resolve, is dropped -- never raised, never coerced -- so
    a damaged block restores what it can. ``working`` is demoted here as well,
    so a slot that claims work is in flight still restores as history.
    """
    links: List[Dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        requester_at = _valid_coordinate(entry.get("requester"))
        worker_at = _valid_coordinate(entry.get("worker"))
        if requester_at is None or worker_at is None:
            continue
        requester = coord_to_session.get(requester_at)
        worker = coord_to_session.get(worker_at)
        if not requester or not worker or requester == worker:
            continue
        fields = _plain_fields(entry)
        if fields is None:
            continue
        link: Dict[str, Any] = {
            "requester_session_id": str(requester),
            "worker_session_id": str(worker),
        }
        link.update(fields)
        links.append(link)
    return links[-MAX_ASSIGNMENTS:]


class CrewHistory:
    """The restored links held in memory, by the workspace that restored them."""

    def __init__(self, *, max_links: int = MAX_ASSIGNMENTS) -> None:
        self.max_links = max(1, int(max_links))
        self._lock = threading.Lock()
        self._by_workspace: Dict[str, List[Dict[str, Any]]] = {}

    def install(self, workspace_id: str, links: Iterable[Mapping[str, Any]]) -> int:
        """Hold a workspace's restored links, replacing what it held before.

        ``links`` carry live session ids, as :func:`translate_for_restore`
        returns them. Each is rebuilt from :data:`LINK_FIELDS` alone with a
        fresh ``link_id`` and ``restored: True``, so nothing else a caller
        passes can be held. One workspace holds at most ``max_links``, the
        newest; another workspace's links are never evicted to make room.
        Returns how many this workspace now holds.
        """
        held: List[Dict[str, Any]] = []
        for link in links:
            if not isinstance(link, Mapping):
                continue
            requester, worker = _pair_key(link)
            if not requester or not worker:
                continue
            fields = _plain_fields(link)
            if fields is None:
                continue
            held.append(
                {
                    "link_id": secrets.token_hex(8),
                    "requester_session_id": requester,
                    "worker_session_id": worker,
                    **fields,
                    "restored": True,
                }
            )
        key = str(workspace_id or "")
        with self._lock:
            self._by_workspace.pop(key, None)
            if held:
                self._by_workspace[key] = held[-self.max_links :]
            count = len(self._by_workspace.get(key, ()))
        logger.info("Crew history installed workspace=%s links=%d", key, count)
        return count

    def snapshot(self) -> List[Dict[str, Any]]:
        """Every held link as a dashboard link, copies, oldest installed first."""
        with self._lock:
            return [dict(link) for links in self._by_workspace.values() for link in links]

    def forget_session(self, session_id: str) -> int:
        """A pane has gone: drop every held link that names it."""
        resolved = str(session_id or "")
        if not resolved:
            return 0
        return self._drop(
            lambda link: resolved in (link["requester_session_id"], link["worker_session_id"])
        )

    def supersede(self, pairs: Iterable[Tuple[str, str]]) -> int:
        """A live link now exists for these (requester, worker) pairs: drop their history."""
        wanted = {(str(requester or ""), str(worker or "")) for requester, worker in pairs}
        wanted.discard(("", ""))
        if not wanted:
            return 0
        return self._drop(lambda link: _pair_key(link) in wanted)

    def count(self) -> int:
        with self._lock:
            return sum(len(links) for links in self._by_workspace.values())

    def reset(self) -> None:
        with self._lock:
            self._by_workspace.clear()

    def _drop(self, doomed) -> int:
        removed = 0
        with self._lock:
            for key in list(self._by_workspace):
                links = self._by_workspace[key]
                kept = [link for link in links if not doomed(link)]
                removed += len(links) - len(kept)
                if kept:
                    self._by_workspace[key] = kept
                else:
                    del self._by_workspace[key]
        if removed:
            logger.info("Crew history dropped links=%d", removed)
        return removed


#: The one history the restore, the close paths and the dashboard share.
history = CrewHistory()

install = history.install
snapshot = history.snapshot
forget_session = history.forget_session
supersede = history.supersede
reset = history.reset
