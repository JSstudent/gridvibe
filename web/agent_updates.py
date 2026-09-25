"""The agent self-update a pane owes its next connection.

The pane header's relaunch menu gives every agent row an update button. It
relaunches the pane exactly as the row would, and the next launch line runs the
agent's registry-defined update command (``_agent_update_command()`` in
``web/agents.py``) before starting the agent -- update, then run, in one press.

The request belongs to one *connection*, not to the pane record: the relaunch
transaction (``web/session_shell.py``) records it here before the replacement
shell starts, and ``_begin_connection()`` in ``web/terminal_io.py`` moves it
onto that connection the moment the connection exists. Its startup sequence
reads it from there, so a connection that fails or is retired first takes the
update with it, and a reconnect's new connection owes nothing. It is never
persisted, so a restored workspace launches the agent plainly. A relaunch that
asks for no update clears whatever an earlier one left behind.

Its own module because both of those import it, and neither may import the
other.
"""

import threading
from typing import Dict

_lock = threading.Lock()
_pending: Dict[str, str] = {}


def request_update(session_id: str, command: str) -> None:
    """Record the update command this pane's next connection runs, or clear it."""
    key = str(session_id or "")
    if not key:
        return
    with _lock:
        if command:
            _pending[key] = command
        else:
            _pending.pop(key, None)


def take_update(session_id: str) -> str:
    """Return and forget the update command owed to this pane, or ``""``."""
    key = str(session_id or "")
    if not key:
        return ""
    with _lock:
        return _pending.pop(key, "")
