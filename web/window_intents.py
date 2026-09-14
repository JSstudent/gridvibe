"""The native-mode intent store: "somebody please open this window".

Nothing outside a page can open a pywebview window, and the MCP sidecar is not
a page. So it leaves an *intent* here, an open GridVibe page picks it up, and
the page reports back what happened.

In-memory and TTL-bounded. No durable state, no file, nothing that survives a
restart -- an intent nobody claimed within its TTL is not worth remembering.

The claim endpoint is the whole point of the design: two open pages polling the
same pending intent would otherwise both open the workspace, and the user would
get two windows. Exactly one claimant wins.
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

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

#: What a page may report back. Anything else is refused.
OUTCOMES = (OPENED, BLOCKED)


class WindowIntentStore:
    """Every intent currently worth remembering, and who claimed which."""

    def __init__(
        self,
        *,
        ttl_seconds: float = INTENT_TTL_SECONDS,
        claim_ttl_seconds: float = CLAIM_TTL_SECONDS,
        max_intents: int = MAX_INTENTS,
    ) -> None:
        self._lock = threading.Lock()
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
        """Record one intent and return it."""
        moment = time.monotonic() if now is None else float(now)
        intent_id = uuid.uuid4().hex[:16]
        record = {
            "intent_id": intent_id,
            "workspace_id": str(workspace_id or "").strip(),
            "group_id": str(group_id or "").strip(),
            "state": PENDING,
            "created_at": moment,
            "expires_at": moment + self.ttl_seconds,
            "claimed_by": "",
            "detail": "",
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
            return True, self._public(record, moment)

    def record_result(
        self,
        intent_id: str,
        outcome: str,
        detail: str = "",
        *,
        now: Optional[float] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """The claimant reports what happened."""
        moment = time.monotonic() if now is None else float(now)
        resolved = str(outcome or "").strip().lower()
        if resolved not in OUTCOMES:
            return False, {
                "error": f"A window result must be one of: {', '.join(OUTCOMES)}.",
            }
        with self._lock:
            record = self._intents.get(str(intent_id or ""))
            if record is None:
                return False, {"error": "No such window request.", "state": EXPIRED}
            record["state"] = resolved
            record["detail"] = str(detail or "")[:240]
            # Keep a settled intent readable just long enough for the sidecar's
            # next poll to see it, rather than expiring it out from under them.
            record["expires_at"] = moment + self.claim_ttl_seconds
            return True, self._public(record, moment)

    # ---------------- reads ----------------

    def pending(self, *, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Unclaimed intents. Almost always empty."""
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
        return {
            "intent_id": record["intent_id"],
            "workspace_id": record["workspace_id"],
            "group_id": record["group_id"],
            "state": record["state"],
            "detail": record["detail"],
            "expires_in": max(0.0, round(record["expires_at"] - moment, 3)),
        }


#: The one store the routes read and write.
window_intents = WindowIntentStore()
