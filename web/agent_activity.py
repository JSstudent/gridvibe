"""What a live pane is announcing about itself, read out of its own output.

The dashboard has to answer two questions about an agent pane that nothing in
GridVibe previously asked: *what is it working on*, and *is it working at all*.
Both answers already travel through the pane's output stream, so this module is
the reading half -- the same shape as ``web/terminal_cwd.py``, and for the same
reasons:

- **Observation never writes to the pane.** Nothing is typed, nothing is
  probed, and no agent is interrupted to be asked how it is doing. Everything
  here comes out of bytes the pane was going to produce anyway.
- **The answer is already known when a route asks.** A dashboard poll reads a
  dictionary, not a round trip with a deadline.

Two sequences carry it, both of them conventions the agent CLIs already speak:

- **OSC 0 / OSC 2** set the window title. An agent announces what it is doing
  there (Claude Code sets it on launch and re-asserts it; a plain shell sets it
  too, which is fine -- the title is "what this pane calls itself", whoever
  wrote it).
- **OSC 9;4**, ConEmu's progress sequence, carries a state and a percentage.
  Claude Code 2.0 emits it; most agents do not, which is exactly why the
  liveness answer below does not depend on it.

So the state is derived from two independent sources and says which it used: a
progress sequence when one was published, and otherwise how recently the pane
wrote *anything*. An agent that has printed nothing for a while is waiting for
you; one repainting a spinner is not. That fallback is what makes the reading
work for every agent rather than only the one that speaks the protocol.

The record is a plain dict owned by the pane's connection entry, which is what
keeps this out of the session record: a live observation is not session state,
it is never saved, never restored, and a relaunched pane starts a fresh one
because it starts a fresh connection.

The one thing that outlives the agent is its title -- Codex sets one and does
not clear it on the way out, and the shell that inherits the pane says nothing
at all -- so the pane's connection carries a floor and the *reader* stops
publishing what was announced before it. :func:`mask_agent_titles` is that half;
raising the floor is ``web/terminal_io.py``'s, at the moment the pane is
retargeted. Clearing the record instead would put a second writer on a dict this
module's whole no-lock design rests on having exactly one.

Text and values in, values out -- no imports from ``web`` except the shared
sequence scanner -- so ``tests/test_agent_activity.py`` executes it directly.
The stream side stays in ``web/terminal_io.py``.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from web.osc_stream import pending_osc_residue

#: Event kinds ``parse_agent_events`` reports.
AGENT_EVENT_TITLE = "title"
AGENT_EVENT_TAB_TITLE = "tab_title"
AGENT_EVENT_PROGRESS = "progress"

#: Upper bound on the split-sequence residue one connection may carry between
#: two reads. Titles are the long payload here, and the ceiling is deliberately
#: a little above the title cap so a title arriving in two chunks still lands.
AGENT_RESIDUE_MAX_CHARS = 2048

#: A title is chrome for one row. Anything longer than this is either a pane
#: painting its whole transcript into the title or a stream that never
#: terminated one, and neither may reach the dashboard at full length.
AGENT_TITLE_MAX_CHARS = 160

_OSC_TITLE_HEADS = ("\x1b]0;", "\x1b]1;", "\x1b]2;")
_OSC_PROGRESS_HEAD = "\x1b]9;4;"
_SEQUENCE_HEADS = _OSC_TITLE_HEADS + (_OSC_PROGRESS_HEAD,)

# A payload runs to the terminator and may contain neither BEL nor ESC, so the
# pattern cannot run away across the rest of a chunk looking for a close.
#
# ``9;4`` and the cwd observer's ``9;9`` share a prefix and nothing else; both
# patterns name their second field, so neither can ever read the other's.
_OSC_EVENT_PATTERN = re.compile(
    r"\x1b\]"
    r"(?:(?P<title_code>[012]);(?P<title>[^\x07\x1b]*)"
    r"|9;4;(?P<progress>[^\x07\x1b]*))"
    r"(?:\x07|\x1b\\)"
)

# A title is one line of text, so every control character in it is noise.
_TITLE_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_TERMINAL_OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)")
_TERMINAL_CSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

#: One character the reader would actually see: neither a control code nor
#: whitespace. It is *searched for* rather than substituted away, because the
#: only question ever asked of it is whether there is any -- and on ordinary
#: output the answer is the chunk's first character.
_VISIBLE_TEXT_PATTERN = re.compile(r"[^\s\x00-\x1f\x7f]")

#: OSC 9;4 states, by the numeric code the sequence carries.
PROGRESS_STATE_NONE = ""
PROGRESS_STATE_NORMAL = "normal"
PROGRESS_STATE_ERROR = "error"
PROGRESS_STATE_INDETERMINATE = "indeterminate"
PROGRESS_STATE_WARNING = "warning"

_PROGRESS_STATES = {
    "0": PROGRESS_STATE_NONE,
    "1": PROGRESS_STATE_NORMAL,
    "2": PROGRESS_STATE_ERROR,
    "3": PROGRESS_STATE_INDETERMINATE,
    "4": PROGRESS_STATE_WARNING,
}

#: Progress states that mean the pane is doing something right now.
_LIVE_PROGRESS_STATES = frozenset(
    {PROGRESS_STATE_NORMAL, PROGRESS_STATE_INDETERMINATE, PROGRESS_STATE_WARNING}
)

#: How recently a pane must have written something to count as working. Agents
#: repaint a spinner several times a second while they think, so this is a
#: generous multiple of a frame -- and still short enough that a pane sitting at
#: a prompt reads as idle rather than busy.
AGENT_WORKING_WINDOW_SECONDS = 3.0

#: How long a published progress state is allowed to drive the liveness answer.
#: A crashed agent never sends the ``0`` that clears its own progress, and a
#: dashboard that reported it as working for the rest of the day would be
#: reporting the sequence rather than the pane. The reading is still shown --
#: it is what the pane last said -- it just stops meaning "now".
AGENT_PROGRESS_STALE_SECONDS = 120.0

#: What ``describe_agent_activity`` reports.
ACTIVITY_WORKING = "working"
ACTIVITY_IDLE = "idle"
ACTIVITY_UNKNOWN = "unknown"
ACTIVITY_ERROR = "error"

#: Which input decided the state, reported alongside it so a reader can tell an
#: observation from an inference (the same rule the cwd observer follows).
ACTIVITY_SOURCE_PROGRESS = "progress"
ACTIVITY_SOURCE_OUTPUT = "output"
ACTIVITY_SOURCE_NONE = "none"


def normalize_agent_title(value: Any) -> str:
    """Return one line of plain text, bounded, or "" for nothing usable."""
    candidate = _TITLE_CONTROL_PATTERN.sub(" ", str(value or ""))
    candidate = " ".join(candidate.split())
    if len(candidate) > AGENT_TITLE_MAX_CHARS:
        candidate = candidate[:AGENT_TITLE_MAX_CHARS].rstrip() + "…"
    return candidate


def has_agent_screen_output(chunk: str, residue: str = "") -> bool:
    """Title reassertions and control-only frames do not mean agent work.

    This only classifies output; the transport still forwards every byte.
    Include the previous residue so the tail of a split title isn't mistaken
    for visible text when it arrives on its own.

    It is also the pump's hot path -- it runs on every chunk of every pane,
    whether or not that pane runs an agent -- so it answers without rewriting
    the chunk wherever it can. Neither sequence pattern can match without its
    own two-character head, so output carrying none is never handed to a
    substitution at all, and colour-heavy output skips the one sequence it
    does not carry. What is left is a search that stops at the first visible
    character rather than a third copy of the text.
    """
    visible = (residue or "") + (chunk or "")
    if "\x1b]" in visible:
        visible = _TERMINAL_OSC_PATTERN.sub("", visible)
    if "\x1b[" in visible:
        visible = _TERMINAL_CSI_PATTERN.sub("", visible)
    return bool(_VISIBLE_TEXT_PATTERN.search(visible))


def parse_progress_payload(payload: Any) -> Optional[Tuple[str, int]]:
    """Read one OSC 9;4 payload into ``(state, value)``.

    ``None`` for a payload that names no state GridVibe knows -- an unknown code
    is left alone rather than guessed at, because guessing would publish a
    progress bar nothing asked for.
    """
    fields = str(payload or "").strip().split(";")
    state = _PROGRESS_STATES.get(fields[0].strip()) if fields else None
    if state is None:
        return None
    if state == PROGRESS_STATE_NONE:
        return PROGRESS_STATE_NONE, 0
    raw_value = fields[1].strip() if len(fields) > 1 else ""
    try:
        value = int(raw_value)
    except ValueError:
        value = 0
    return state, max(0, min(100, value))


def parse_agent_events(chunk: str, residue: str = "") -> Tuple[List[Tuple[str, str]], str]:
    """Read title/progress sequences out of one terminal output chunk.

    Returns every event in the order it appeared plus the residue to hand back
    on the next read. The chunk is only *observed*: the caller still caches and
    replays it verbatim, so a pane whose TUI is mid-frame is unaffected.
    """
    text = (residue or "") + (chunk or "")
    if "\x1b" not in text:
        return [], ""

    events: List[Tuple[str, str]] = []
    for match in _OSC_EVENT_PATTERN.finditer(text):
        progress = match.group("progress")
        if progress is not None:
            events.append((AGENT_EVENT_PROGRESS, progress.strip()))
            continue
        title = match.group("title") or ""
        code = match.group("title_code")
        if code == "1":
            events.append((AGENT_EVENT_TAB_TITLE, title))
        else:
            events.append((AGENT_EVENT_TITLE, title))
            # OSC 0 sets both labels; it also retires an older OSC 1 label.
            if code == "0":
                events.append((AGENT_EVENT_TAB_TITLE, ""))

    return events, pending_osc_residue(text, _SEQUENCE_HEADS, AGENT_RESIDUE_MAX_CHARS)


def blank_agent_activity() -> Dict[str, Any]:
    """Return the record a pane starts with: nothing observed yet."""
    return {
        "title": "",
        "tab_title": "",
        "title_at": 0.0,
        "tab_title_at": 0.0,
        "progress_state": PROGRESS_STATE_NONE,
        "progress_value": 0,
        "progress_at": 0.0,
        "last_output_at": 0.0,
    }


def note_agent_output(record: Optional[Dict[str, Any]], now: float) -> Dict[str, Any]:
    """Record that the pane wrote something at ``now``.

    Visible output lands here; title and control-only updates are excluded by
    the observer. The pane's pump owns this connection-local timestamp.
    """
    updated = dict(record or blank_agent_activity())
    updated["last_output_at"] = float(now)
    return updated


def apply_agent_events(
    record: Optional[Dict[str, Any]],
    events: List[Tuple[str, str]],
    now: float,
) -> Dict[str, Any]:
    """Fold parsed events into a record, the last one of each kind winning.

    A title the pane just cleared is a title it no longer has, so an empty
    payload clears the field rather than being ignored: an agent that sets the
    title on start and clears it on exit must not leave its last task standing
    on the dashboard.
    """
    updated = dict(record or blank_agent_activity())
    for kind, value in events:
        if kind == AGENT_EVENT_TAB_TITLE:
            updated["tab_title"] = normalize_agent_title(value)
            updated["tab_title_at"] = float(now)
            continue
        if kind == AGENT_EVENT_TITLE:
            updated["title"] = normalize_agent_title(value)
            updated["title_at"] = float(now)
            continue
        if kind != AGENT_EVENT_PROGRESS:
            continue
        parsed = parse_progress_payload(value)
        if parsed is None:
            continue
        state, progress_value = parsed
        updated["progress_state"] = state
        updated["progress_value"] = progress_value
        updated["progress_at"] = float(now)
    return updated


def mask_agent_titles(
    record: Optional[Dict[str, Any]],
    floor: float,
) -> Dict[str, Any]:
    """Drop announced titles the pane published before it was retargeted.

    An agent that exits without clearing its own title leaves the last thing it
    said standing in the record, and the shell that inherits the pane has no
    reason to say anything at all -- so "OpenAI Codex CLI" survives the agent
    that wrote it and is then read as a fact about whatever the pane became.
    Clearing the record itself would race the pump thread that owns it (the
    only writer), so the reader is what stops publishing instead: the caller
    raises a floor at the moment of the retargeting, and a title stamped at or
    before it is no longer an observation of the pane that is there now.

    This is the title half of the rule ``_publish_observed_cwd`` follows for the
    directory: a dead shell's last report is not an observation of the live one.
    A title the pane announces *after* the floor is a fresh one and stands, so
    the next agent's first announcement replaces the mask rather than fighting
    it. Returns the record unchanged when there is nothing to mask, so the
    common poll allocates nothing.
    """
    source = record or blank_agent_activity()
    limit = float(floor or 0.0)
    if limit <= 0.0:
        return source
    stale_title = bool(source.get("title")) and float(source.get("title_at") or 0.0) <= limit
    stale_tab = bool(source.get("tab_title")) and float(source.get("tab_title_at") or 0.0) <= limit
    if not stale_title and not stale_tab:
        return source
    masked = dict(source)
    if stale_title:
        masked["title"] = ""
    if stale_tab:
        masked["tab_title"] = ""
    return masked


def describe_agent_activity(
    record: Optional[Dict[str, Any]],
    now: float,
    *,
    working_window: float = AGENT_WORKING_WINDOW_SECONDS,
    progress_stale_after: float = AGENT_PROGRESS_STALE_SECONDS,
) -> Dict[str, Any]:
    """Turn one observation record into the reading the dashboard publishes.

    The state is decided in one place, by two inputs in a fixed order:

    1. a progress state the pane published recently enough to still mean *now*;
    2. otherwise how long ago the pane last wrote anything.

    A pane that has produced nothing at all is ``unknown`` and not ``idle``:
    there is a real difference between an agent waiting for input and a pane
    whose transport never came up, and reporting the second as the first is how
    a dashboard starts lying about panes it cannot see.
    """
    source = record or blank_agent_activity()
    last_output_at = float(source.get("last_output_at") or 0.0)
    progress_at = float(source.get("progress_at") or 0.0)
    progress_state = str(source.get("progress_state") or PROGRESS_STATE_NONE)
    progress_fresh = bool(
        progress_state
        and progress_at > 0.0
        and (now - progress_at) <= float(progress_stale_after)
    )

    if progress_fresh and progress_state in _LIVE_PROGRESS_STATES | {PROGRESS_STATE_ERROR}:
        state = ACTIVITY_ERROR if progress_state == PROGRESS_STATE_ERROR else ACTIVITY_WORKING
        state_source = ACTIVITY_SOURCE_PROGRESS
    elif last_output_at <= 0.0:
        state = ACTIVITY_UNKNOWN
        state_source = ACTIVITY_SOURCE_NONE
    elif (now - last_output_at) <= float(working_window):
        state = ACTIVITY_WORKING
        state_source = ACTIVITY_SOURCE_OUTPUT
    else:
        state = ACTIVITY_IDLE
        state_source = ACTIVITY_SOURCE_OUTPUT

    return {
        "state": state,
        "state_source": state_source,
        "title": str(source.get("tab_title") or source.get("title") or ""),
        "progress_state": progress_state,
        "progress_fresh": progress_fresh,
        # The percentage only means anything while the sequence says it does;
        # an error or indeterminate state carries no number worth painting.
        "progress_value": (
            int(source.get("progress_value") or 0)
            if progress_state == PROGRESS_STATE_NORMAL
            else 0
        ),
        "idle_seconds": (
            round(max(0.0, now - last_output_at), 1) if last_output_at > 0.0 else None
        ),
    }
