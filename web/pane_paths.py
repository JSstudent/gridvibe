"""The one reading of a pane's four durable path facts.

Three stores and two save surfaces used to each decide for themselves what a
pane's path state *was*, and they disagreed: the runtime snapshot folded the
observed working directory into ``directory`` while the reusable-preset merge
deliberately kept the launcher's original one, so the same gesture on the same
pane saved two different locations depending on which product it wrote to.

There are four facts, and each one has exactly one meaning here:

``directory``
    Where the pane is, and therefore where its replacement launches. The
    observation (``current_directory``) when the pane produced one, and the
    recorded directory otherwise -- never a guess, because an absent
    observation is absent, not wrong.
``launch_directory``
    Where the pane was originally built. Nothing moves it; it is retained as a
    record and no longer clamps anything.
``explorer_root_directory``
    The explorer's confinement boundary, saved independently of the launch
    directory: a terminal may work inside a subdirectory while Files is
    confined to a wider project, and both survive the same save.
``explorer_root_configured``
    Whether somebody chose that boundary. Explicit ``False`` is a value, not an
    absence: a root the Terminal -> Files transition derived from where the
    pane happened to be must come back derived, or it becomes a pin on a
    directory nobody picked.

Nothing here reads a live process, opens a channel, or touches disk: capture
runs inside the runtime-state lock hold, so the whole policy is a pure read of
one already-coherent pane snapshot.
"""

from typing import Any, Dict, Optional

#: The four facts of one live pane, in the order they are written.
PANE_PATH_FIELDS = (
    "directory",
    "launch_directory",
    "explorer_root_directory",
    "explorer_root_configured",
)

#: What a reusable preset stores. A root and the flag qualifying it are never
#: split -- that is how a derived root came back indistinguishable from a
#: chosen one -- but ``launch_directory`` is deliberately absent: a preset is a
#: template, and a pane launched from one is built *now*, at the directory the
#: preset gives it, so ``TerminalSession`` records that as its build directory.
#: Only a runtime snapshot replays the field, because only a restore is the
#: same pane coming back.
SAVED_PANE_PATH_FIELDS = (
    "directory",
    "explorer_root_directory",
    "explorer_root_configured",
)


def _text(value: Any) -> str:
    """Return one trimmed path string, treating every absence as ``""``."""
    return str(value or "").strip()


def explorer_root_was_configured(
    record: Dict[str, Any],
    startup_mode: Optional[str] = None,
) -> bool:
    """Read the root-provenance flag, applying the legacy default only if unstated.

    A record that states the flag is believed in both directions -- that is the
    whole point of persisting it, and an explicit ``False`` is the value that
    matters most.

    A record written before the flag existed does not state it, and the pane
    itself is then the only evidence: a root on an *explorer* pane is the
    boundary that pane was built with, so somebody chose it, while a root on a
    terminal, agent or browser pane can only be one an older
    Terminal -> Files transition derived and a snapshot carried back.
    ``startup_mode`` overrides the record's own when the caller has already
    normalized it.
    """
    stated = record.get("explorer_root_configured")
    if isinstance(stated, bool):
        return stated
    mode = str(
        startup_mode
        if startup_mode is not None
        else (record.get("startup_mode") or "")
    )
    return bool(_text(record.get("explorer_root_directory"))) and mode == "explorer"


def capture_pane_paths(pane: Any) -> Dict[str, Any]:
    """Return the four durable path fields of one live pane.

    ``pane`` is a live session or its ``to_dict()``; only already-known values
    are read, so this is safe to call under a lock and cannot block on a remote
    pane.
    """
    data = pane if isinstance(pane, dict) else pane.to_dict()
    recorded = _text(data.get("directory"))
    observed = _text(data.get("current_directory"))
    return {
        "directory": observed or recorded,
        "launch_directory": _text(data.get("launch_directory")) or recorded,
        "explorer_root_directory": _text(data.get("explorer_root_directory")),
        "explorer_root_configured": explorer_root_was_configured(data),
    }


def saved_pane_paths(
    entry: Dict[str, Any],
    startup_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the path fields of one stored or incoming preset entry.

    The saved-preset counterpart of :func:`capture_pane_paths`: a stored entry
    has no live observation to fold in, so ``directory`` is whatever the record
    holds. A legacy entry stating neither added field comes back with the
    documented defaults and is readable exactly as before; historical paths a
    save never recorded cannot be recovered here or anywhere else.
    """
    record = entry if isinstance(entry, dict) else {}
    return {
        "directory": _text(record.get("directory")),
        "explorer_root_directory": _text(record.get("explorer_root_directory")),
        "explorer_root_configured": explorer_root_was_configured(record, startup_mode),
    }
