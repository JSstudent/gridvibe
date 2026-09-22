"""The server half of "may this answer still be sent".

A program asks the terminal about itself by writing a query into its output
(``ESC ] 11 ; ? ST`` for the background colour, ``ESC [ 6 n`` for the cursor)
and the terminal answers by *typing* the reply into its input. The asker waits
a short while for that reply and then goes back to reading keystrokes -- Codex
waits about a tenth of a second -- so a reply that lands afterwards is read as
text the reader typed:

    > ]10;rgb:e0e0/e0e0/e0e0\\]11;rgb:0d0d/0d0d/0d0d\\

The page (`web/static/js/terminal-replies.js`) strips queries off output it
held too long, but it can only measure the time it held them. It cannot see
the socket, a main thread busy restoring a workspace, or the trip back, and
those are exactly what make a restored pane's reply late. The server can: it
reads the query off the pty and receives the answer, on one clock. So each
connection keeps a small ledger of the queries it has read, and a reply that
arrives more than `REPLY_AGE_BUDGET_S` after its query is dropped from the
input instead of being typed into the pane.

A reply is matched only against a query of its own kind that is still in the
ledger. Input that merely looks like a reply -- Shift+F3 is ``ESC [ 1 ; 2 R``,
the same shape as a cursor report -- passes untouched unless a query of that
kind is actually outstanding.

Text in, text out -- no imports from ``web`` -- so the tests drive it directly.
"""

import re
import threading
from typing import List, Optional, Tuple

#: Measured against Codex under ConPTY: a colour reply 80 ms after GridVibe
#: read the query was accepted, one at 100 ms was typed into the composer. The
#: budget sits below that edge, because a reply dropped a little early costs a
#: program only its default guess, and one let through a little late costs the
#: reader a line of garbage in the prompt they are typing into.
REPLY_AGE_BUDGET_S = 0.075

#: A query the page stripped is never answered, so its entry would otherwise
#: stay forever and wait for a matching key. Past this age it is forgotten.
PENDING_QUERY_HORIZON_S = 10.0

MAX_PENDING_QUERIES = 64

#: The longest incomplete sequence carried from one read to the next. Queries
#: are short; anything longer is some other sequence's payload.
MAX_QUERY_RESIDUE = 256

_QUERY_RE = re.compile(
    r"\x1b\](?P<osc>1[012]|[45];[0-9]+);\?(?:\x07|\x1b\\)"
    r"|\x1b\[(?P<da>[>=]?)0?c"
    r"|\x1b\[(?P<dsr>\??[56])n"
    r"|\x1b\[>0?q(?P<xtversion>)"
    r"|\x1b\[(?P<decrqm>\??[0-9]{1,5})\$p"
    r"|\x1bP(?P<dcs>[+$])q[^\x1b\x07]*(?:\x07|\x1b\\)"
)

_REPLY_RE = re.compile(
    r"\x1b\](?P<osc>1[012]|[45];[0-9]+);(?!\?)[^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b\[(?P<da>[?>])[0-9;]*c"
    r"|\x1bP!\|[0-9A-Fa-f]*\x1b\\(?P<da3>)"
    r"|\x1b\[[03]n(?P<dsr5>)"
    r"|\x1b\[(?P<cpr>\??)[0-9]{1,5};[0-9]{1,5}(?:;[0-9]{1,5})?R"
    r"|\x1bP>\|[^\x1b\x07]*(?:\x07|\x1b\\)(?P<xtversion>)"
    r"|\x1b\[(?P<decrpm>\??[0-9]{1,5});[0-9]\$y"
    r"|\x1bP[01](?P<dcs>[+$])r[^\x1b\x07]*(?:\x07|\x1b\\)"
)

_DA_QUERY_KINDS = {"": "da1", ">": "da2", "=": "da3"}
_DA_REPLY_KINDS = {"?": "da1", ">": "da2"}
_DCS_KINDS = {"+": "xtgettcap", "$": "decrqss"}


def _query_kind(match: "re.Match[str]") -> str:
    groups = match.groupdict()
    if groups["osc"] is not None:
        return "osc" + groups["osc"]
    if groups["da"] is not None:
        return _DA_QUERY_KINDS[groups["da"]]
    if groups["dsr"] is not None:
        return {"5": "dsr5", "6": "cpr", "?6": "xcpr"}.get(groups["dsr"], "dsr5")
    if groups["xtversion"] is not None:
        return "xtversion"
    if groups["decrqm"] is not None:
        return "decrqm" + groups["decrqm"]
    return _DCS_KINDS[groups["dcs"]]


def _reply_kind(match: "re.Match[str]") -> str:
    groups = match.groupdict()
    if groups["osc"] is not None:
        return "osc" + groups["osc"]
    if groups["da"] is not None:
        return _DA_REPLY_KINDS[groups["da"]]
    if groups["da3"] is not None:
        return "da3"
    if groups["dsr5"] is not None:
        return "dsr5"
    if groups["cpr"] is not None:
        return "xcpr" if groups["cpr"] else "cpr"
    if groups["xtversion"] is not None:
        return "xtversion"
    if groups["decrpm"] is not None:
        return "decrqm" + groups["decrpm"]
    return _DCS_KINDS[groups["dcs"]]


def split_trailing_sequence(text: str) -> Tuple[str, str]:
    """Split off a trailing escape sequence that has not finished arriving.

    CSI ends at its final byte; OSC and DCS end at BEL or ST. A tail longer
    than `MAX_QUERY_RESIDUE` is never carried.
    """
    floor = max(0, len(text) - MAX_QUERY_RESIDUE)
    start = text.rfind("\x1b", floor)
    if start < 0:
        return text, ""
    if start == len(text) - 1:
        # A lone trailing ESC may be the first half of the ST closing a string
        # that opened earlier; carry that whole string, not just its ESC.
        opener = text.rfind("\x1b", floor, start)
        if opener >= 0 and text[opener + 1:opener + 2] in ("]", "P") and "\x07" not in text[opener:]:
            return text[:opener], text[opener:]
        return text[:start], text[start:]
    tail = text[start:]
    kind = tail[1]
    if kind == "[" and not any(0x40 <= ord(char) <= 0x7E for char in tail[2:]):
        return text[:start], tail
    if kind in ("]", "P") and "\x07" not in tail:
        return text[:start], tail
    return text, ""


class ReplyLedger:
    """What one connection has asked its terminal, and when.

    Written by the connection's pump thread and read by the Socket.IO handler
    thread delivering input, so every access holds the ledger's own lock. The
    lock guards only this list -- no I/O, no emit, no other lock is taken under
    it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: List[Tuple[str, float]] = []
        self._residue = ""

    def note_output(self, text: str, now: float) -> None:
        """Record every query in one chunk of the pane's output."""
        if not text:
            return
        with self._lock:
            if "\x1b" not in text and not self._residue:
                return
            scanned, self._residue = split_trailing_sequence(self._residue + text)
            if "\x1b" not in scanned:
                return
            for match in _QUERY_RE.finditer(scanned):
                self._pending.append((_query_kind(match), now))
            self._forget(now)

    def filter_input(self, text: str, now: float) -> str:
        """Drop from ``text`` every reply whose query is older than the budget."""
        if not text or "\x1b" not in text:
            return text
        with self._lock:
            if not self._pending:
                return text
            self._forget(now)
            kept: List[str] = []
            cursor = 0
            for match in _REPLY_RE.finditer(text):
                asked_at = self._take(_reply_kind(match))
                if asked_at is None or now - asked_at <= REPLY_AGE_BUDGET_S:
                    continue
                kept.append(text[cursor:match.start()])
                cursor = match.end()
            if not cursor:
                return text
            kept.append(text[cursor:])
            return "".join(kept)

    def _take(self, kind: str) -> Optional[float]:
        # The newest query of this kind: a stale entry left by a query the
        # page stripped must not make a fresh reply look late.
        for index in range(len(self._pending) - 1, -1, -1):
            if self._pending[index][0] == kind:
                return self._pending.pop(index)[1]
        return None

    def _forget(self, now: float) -> None:
        horizon = now - PENDING_QUERY_HORIZON_S
        if self._pending and self._pending[0][1] < horizon:
            self._pending = [entry for entry in self._pending if entry[1] >= horizon]
        if len(self._pending) > MAX_PENDING_QUERIES:
            del self._pending[: len(self._pending) - MAX_PENDING_QUERIES]
