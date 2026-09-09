"""How much of a half-arrived OSC sequence one connection carries between reads.

Two observers read the same terminal output stream for their own sequences --
``web/terminal_cwd.py`` for the working directory, ``web/agent_activity.py``
for what an agent pane is announcing about itself. A chunk boundary can fall
anywhere, including inside a sequence, so both have to answer the same question
between reads: *is this trailing fragment still becoming one of my sequences,
or is it ordinary output?*

That answer is one rule and lives here once. Each caller still owns its own
sequence heads and its own ceiling, because those are what the two observers
genuinely differ about; getting the scan itself wrong the second time would
show up as a directory that never updates or a title that never arrives, in
whichever module got the copy that drifted.

Text in, text out -- no imports from ``web`` -- so it is exercised directly by
the tests of both callers.
"""

from typing import Sequence


def is_pending_osc_sequence(tail: str, heads: Sequence[str]) -> bool:
    """True when ``tail`` could still become one of ``heads``' sequences.

    Three states count as pending: the header itself is still arriving
    (the ESC alone, then ``ESC ]``, then ``ESC ] 9 ;``), the payload is still
    open, or it ends on the ESC that is the first half of an ST terminator. A
    payload that already carries its BEL, or a second ESC, is finished or
    malformed and is never carried.
    """
    for head in heads:
        if head.startswith(tail):
            return True
        if tail.startswith(head):
            payload = tail[len(head):]
            if "\x07" in payload:
                return False
            escapes = payload.count("\x1b")
            if escapes == 0:
                return True
            return escapes == 1 and payload.endswith("\x1b")
    return False


def pending_osc_residue(text: str, heads: Sequence[str], max_chars: int) -> str:
    """Return the trailing fragment of a sequence still being written.

    Only the last ``max_chars`` characters are considered, which is what bounds
    the residue: a sequence that has already outgrown the ceiling is dropped
    rather than carried, so a stream that opens one and never closes it costs
    nothing per read.
    """
    window_start = max(0, len(text) - max(0, int(max_chars)))
    index = text.find("\x1b]", window_start)
    while index >= 0:
        tail = text[index:]
        if is_pending_osc_sequence(tail, heads):
            return tail
        index = text.find("\x1b]", index + 2)
    # The ESC arrived without its "]" yet.
    return "\x1b" if text.endswith("\x1b") else ""
