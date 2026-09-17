"""The page-intent store: "somebody with a page please do this".

Two things GridVibe cannot do from outside a page, and the same store answers
both:

* **Open a window.** Nothing outside a page can open a pywebview window, and
  the MCP sidecar is not a page.
* **Split a pane.** The split *axis* never reaches the server. The page
  computes the new rectangles, and its refusals — the minimum columns and rows
  below a terminal header, the narrow-viewport rule, the pane cap — are
  measured off the live terminal. A process that cannot measure a pane cannot
  place one, so it leaves an intent and the page that can measure performs the
  split with the button's own handler.

In-memory and TTL-bounded. No durable state, no file, nothing that survives a
restart -- an intent nobody claimed within its TTL is not worth remembering.

The claim endpoint is the whole point of the design: two open pages polling the
same pending intent would otherwise both act on it, and the user would get two
windows or two panes. Exactly one claimant wins.
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: How long a page has to notice an intent. Short: the sidecar is holding a
#: tool call open while it waits, and an unclaimed intent means no page is
#: there to claim it, which is an answer rather than something to wait out.
INTENT_TTL_SECONDS = 15.0

#: How long a claimant has to report back before the claim is abandoned. A page
#: that claimed and then died must not strand the intent in `claimed` forever.
CLAIM_TTL_SECONDS = 20.0

#: A ceiling, so a sidecar in a retry loop cannot grow the store without bound.
MAX_INTENTS = 32

PENDING = "pending"
CLAIMED = "claimed"
OPENED = "opened"
BLOCKED = "blocked"
EXPIRED = "expired"

#: A split's own two outcomes. `SPLIT` doubles as the kind name and the success
#: outcome, in two different fields -- "what was asked for" and "what happened".
SPLIT = "split"
REFUSED = "refused"

WINDOW_KIND = "window"
SPLIT_KIND = SPLIT

#: What a page may report back, per kind. Anything else is refused: a page that
#: reported `opened` on a split would be reporting something it did not do.
OUTCOMES_BY_KIND: Dict[str, Tuple[str, ...]] = {
    WINDOW_KIND: (OPENED, BLOCKED),
    SPLIT_KIND: (SPLIT, REFUSED),
}

#: Back-compat: the window kind's outcomes, which is what this name always
#: meant. Read by nothing here; kept because it is the store's published set.
OUTCOMES = OUTCOMES_BY_KIND[WINDOW_KIND]

#: A settled split reports the pane it made. Bounded to a field list for the
#: same reason every other published payload is: the store forwards a result,
#: it does not become a second place a session is described.
SPLIT_RESULT_FIELDS = (
    "session_id",
    "group_id",
    "title",
    "startup_mode",
    "agent_selection",
    "index",
)


class WindowIntentStore:
    """Every intent currently worth remembering, and who claimed which."""

    def __init__(
        self,
        *,
        ttl_seconds: float = INTENT_TTL_SECONDS,
        claim_ttl_seconds: float = CLAIM_TTL_SECONDS,
        max_intents: int = MAX_INTENTS,
    ) -> None:
        # A condition rather than a plain lock, so a reader in *this* process
        # can wait for an intent to settle instead of asking over HTTP. It is
        # still the one lock every access takes -- `with self._lock` is
        # unchanged everywhere -- and the two writes that settle an intent
        # wake whoever is waiting. See `wait_for_settled`.
        self._lock = threading.Condition()
        self._intents: Dict[str, Dict[str, Any]] = {}
        self.ttl_seconds = float(ttl_seconds)
        self.claim_ttl_seconds = float(claim_ttl_seconds)
        self.max_intents = int(max_intents)

    # ---------------- writes ----------------

    def open(
        self,
        workspace_id: str,
        group_id: str = "",
        *,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record one "open this workspace" intent and return it."""
        return self._record(
            {
                "kind": WINDOW_KIND,
                "workspace_id": str(workspace_id or "").strip(),
                "group_id": str(group_id or "").strip(),
            },
            now=now,
        )

    def open_split(
        self,
        session_id: str,
        axis: str,
        *,
        group_id: str = "",
        workspace_id: str = "",
        split_request: Optional[Mapping[str, Any]] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Record one "split this pane" intent and return it.

        ``split_request`` is the request body the claiming page posts back to
        ``POST /api/sessions/<id>/split`` verbatim. It is built server-side at
        the moment the intent is recorded — including the creator stamp — so a
        page forwards a validated request rather than composing one.
        """
        return self._record(
            {
                "kind": SPLIT_KIND,
                "workspace_id": str(workspace_id or "").strip(),
                "group_id": str(group_id or "").strip(),
                "session_id": str(session_id or "").strip(),
                "axis": str(axis or "").strip().lower(),
                "split_request": dict(split_request or {}),
            },
            now=now,
        )

    def _record(
        self,
        fields: Dict[str, Any],
        *,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        moment = time.monotonic() if now is None else float(now)
        intent_id = uuid.uuid4().hex[:16]
        record: Dict[str, Any] = {
            "intent_id": intent_id,
            "state": PENDING,
            "created_at": moment,
            "expires_at": moment + self.ttl_seconds,
            "claimed_by": "",
            "detail": "",
            "result": None,
            **fields,
        }
        with self._lock:
            self._prune(moment)
            if len(self._intents) >= self.max_intents:
                oldest = min(self._intents.values(), key=lambda item: item["created_at"])
                self._intents.pop(oldest["intent_id"], None)
            self._intents[intent_id] = record
            return self._public(record, moment)

    def claim(
        self,
        intent_id: str,
        claimant: str = "",
        *,
        now: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Claim one pending intent. Succeeds for exactly one caller."""
        moment = time.monotonic() if now is None else float(now)
        with self._lock:
            self._prune(moment)
            record = self._intents.get(str(intent_id or ""))
            if record is None:
                return False, {"error": "No such window request.", "state": EXPIRED}
            if record["state"] != PENDING:
                return False, {
                    "error": "Another GridVibe window already took this request.",
                    "state": record["state"],
                }
            record["state"] = CLAIMED
            record["claimed_by"] = str(claimant or "")[:64]
            record["expires_at"] = moment + self.claim_ttl_seconds
            # A claim is not a settlement, but it moves the deadline a waiter
            # is sleeping against, so the waiter has to re-read it.
            self._lock.notify_all()
            return True, self._public(record, moment)

    def record_result(
        self,
        intent_id: str,
        outcome: str,
        detail: str = "",
        result: Optional[Mapping[str, Any]] = None,
        *,
        now: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """The claimant reports what happened.

        The allowed outcomes are the ones this intent's *kind* has: a page
        reporting `opened` on a split would be reporting something else's work.
        """
        moment = time.monotonic() if now is None else float(now)
        resolved = str(outcome or "").strip().lower()
        with self._lock:
            record = self._intents.get(str(intent_id or ""))
            if record is None:
                return False, {"error": "No such window request.", "state": EXPIRED}
            allowed = OUTCOMES_BY_KIND.get(record.get("kind") or WINDOW_KIND, OUTCOMES)
            if resolved not in allowed:
                return False, {
                    "error": f"A result must be one of: {', '.join(allowed)}.",
                }
            record["state"] = resolved
            record["detail"] = str(detail or "")[:240]
            if isinstance(result, Mapping):
                record["result"] = {
                    key: result.get(key)
                    for key in SPLIT_RESULT_FIELDS
                    if key in result
                }
            # Keep a settled intent readable just long enough for the sidecar's
            # next poll to see it, rather than expiring it out from under them.
            record["expires_at"] = moment + self.claim_ttl_seconds
            self._lock.notify_all()
            return True, self._public(record, moment)

    # ---------------- reads ----------------

    def pending(self, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Unclaimed intents, of every kind. Almost always empty."""
        moment = time.monotonic() if now is None else float(now)
        with self._lock:
            self._prune(moment)
            return [
                self._public(record, moment)
                for record in self._intents.values()
                if record["state"] == PENDING
            ]

    def read(self, intent_id: str, *, now: Optional[float] = None) -> Dict[str, Any]:
        """The sidecar's poll target. An intent that has gone reads expired."""
        moment = time.monotonic() if now is None else float(now)
        with self._lock:
            self._prune(moment)
            record = self._intents.get(str(intent_id or ""))
            if record is None:
                return {"intent_id": str(intent_id or ""), "state": EXPIRED, "detail": ""}
            return self._public(record, moment)

    def wait_for_settled(
        self,
        intent_id: str,
        timeout: float,
    ) -> Dict[str, Any]:
        """:meth:`read`, but for a caller that would otherwise poll it.

        The stdio sidecar polls this store over HTTP every half second, which
        costs GridVibe one cheap GET per tick and is fine: that loop runs in
        the sidecar's own process. The *same* loop runs inside a Flask request
        handler when a remote pane's agent calls `split_pane` or `open_window`
        over `POST /mcp/<token>` (`web/mcp_http.py`), and there it was issuing
        up to fifty loopback requests back into the server that was already
        holding a worker thread for it -- re-entrancy that only worked because
        `async_mode="threading"` gives every request a new thread.

        The store is in the same process as that handler, so it can be waited
        on directly. This blocks until the intent settles, goes, or the wait
        runs out, and answers exactly what :meth:`read` would have answered at
        that moment -- the caller cannot tell which way it was asked.

        ``timeout`` is an upper bound, not the answer's schedule: the wait also
        ends at the intent's own expiry, because a pruned intent reads
        ``expired`` and there is nothing further to wait for.
        """
        resolved = str(intent_id or "")
        with self._lock:
            deadline = time.monotonic() + max(0.0, float(timeout))
            while True:
                moment = time.monotonic()
                self._prune(moment)
                record = self._intents.get(resolved)
                if record is None:
                    return {"intent_id": resolved, "state": EXPIRED, "detail": ""}
                if record["state"] not in (PENDING, CLAIMED):
                    return self._public(record, moment)
                remaining = min(deadline, record["expires_at"]) - moment
                if remaining <= 0:
                    return self._public(record, moment)
                self._lock.wait(remaining)

    def reset(self) -> None:
        with self._lock:
            self._intents.clear()

    # ---------------- internals ----------------

    def _prune(self, moment: float) -> None:
        """Drop what has expired. Called under the lock by every access."""
        for intent_id, record in list(self._intents.items()):
            if record["expires_at"] <= moment:
                del self._intents[intent_id]

    @staticmethod
    def _public(record: Dict[str, Any], moment: float) -> Dict[str, Any]:
        kind = str(record.get("kind") or WINDOW_KIND)
        payload = {
            "intent_id": record["intent_id"],
            "kind": kind,
            "workspace_id": record.get("workspace_id", ""),
            "group_id": record.get("group_id", ""),
            "state": record["state"],
            "detail": record["detail"],
            "expires_in": max(0.0, round(record["expires_at"] - moment, 3)),
        }
        if kind == SPLIT_KIND:
            payload["session_id"] = record.get("session_id", "")
            payload["axis"] = record.get("axis", "")
            payload["split_request"] = dict(record.get("split_request") or {})
        if record.get("result") is not None:
            payload["result"] = dict(record["result"])
        return payload


#: The one store the routes read and write.
window_intents = WindowIntentStore()
