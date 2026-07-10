"""
Session Manager for GridVibe.
Manages SSH sessions for web-based terminal display.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionStatus(Enum):
    """Status of a terminal session."""
    PENDING = "pending"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


@dataclass
class TerminalSession:
    """Represents a terminal session."""
    session_id: str
    group_id: str
    host: str
    directory: str
    username: str
    port: int = 22
    password: Optional[str] = field(default=None, repr=False)
    initial_command: Optional[str] = None
    initial_command_mode: str = "command"
    agent_selection: str = ""
    custom_agent: str = ""
    title: Optional[str] = None
    mode: str = "ssh"
    distribution: Optional[str] = None
    use_wsl: bool = False
    use_powershell: bool = False
    startup_mode: str = "terminal"
    explorer_root_directory: Optional[str] = None
    status: SessionStatus = SessionStatus.PENDING
    created_at: float = field(default_factory=time.time)
    connected_at: Optional[float] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "group_id": self.group_id,
            "host": self.host,
            "directory": self.directory,
            "username": self.username,
            "port": self.port,
            "initial_command": self.initial_command,
            "initial_command_mode": self.initial_command_mode,
            "agent_selection": self.agent_selection,
            "custom_agent": self.custom_agent,
            "title": self.title,
            "mode": self.mode,
            "distribution": self.distribution,
            "use_wsl": self.use_wsl,
            "use_powershell": self.use_powershell,
            "startup_mode": self.startup_mode,
            "explorer_root_directory": self.explorer_root_directory,
            "status": self.status.value,
            "created_at": self.created_at,
            "connected_at": self.connected_at,
            "error_message": self.error_message
        }


@dataclass
class SessionGroup:
    """Represents one launched terminal group shown as a session tab."""
    group_id: str
    name: str
    connection_mode: str
    layout: str
    terminal_count: int
    display_order: int = 0
    saved_session_id: str = ""
    workspace_layout: Optional[Dict[str, Any]] = None
    surface_mode: str = "normal"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "group_id": self.group_id,
            "name": self.name,
            "connection_mode": self.connection_mode,
            "layout": self.layout,
            "terminal_count": self.terminal_count,
            "display_order": self.display_order,
            "saved_session_id": self.saved_session_id,
            "workspace_layout": self.workspace_layout,
            "surface_mode": self.surface_mode,
            "created_at": self.created_at,
        }


class SessionManager:
    """
    Manages terminal sessions for the web frontend.
    Handles SSH connections and provides session lifecycle management.
    """

    def __init__(self):
        """Initialize the session manager."""
        self.sessions: Dict[str, TerminalSession] = {}
        self.groups: Dict[str, SessionGroup] = {}
        self.lock = threading.RLock()   # RLock: re-entrant so callbacks can be called while lock is held
        self._session_callbacks: Dict[str, List[Callable]] = {}

    def create_group(
        self,
        name: str,
        connection_mode: str,
        layout: str,
        terminal_count: int,
        group_id: Optional[str] = None,
        saved_session_id: str = "",
        workspace_layout: Optional[Dict[str, Any]] = None,
        surface_mode: str = "normal",
    ) -> SessionGroup:
        """Create one group of launched sessions."""
        resolved_group_id = str(group_id or uuid.uuid4().hex[:12])

        with self.lock:
            existing_group = self.groups.get(resolved_group_id)
            next_display_order = (
                existing_group.display_order
                if existing_group is not None
                else max((group.display_order for group in self.groups.values()), default=-1) + 1
            )

            group = SessionGroup(
                group_id=resolved_group_id,
                name=name or resolved_group_id,
                connection_mode=connection_mode,
                layout=layout,
                terminal_count=terminal_count,
                display_order=next_display_order,
                saved_session_id=str(saved_session_id or "").strip(),
                workspace_layout=workspace_layout,
                surface_mode=surface_mode if surface_mode in {"normal", "max"} else "normal",
            )
            self.groups[resolved_group_id] = group

        return group

    def create_session(
        self,
        group_id: str,
        host: str,
        directory: str,
        username: str = "root",
        port: int = 22,
        password: Optional[str] = None,
        initial_command: Optional[str] = None,
        initial_command_mode: str = "command",
        agent_selection: str = "",
        custom_agent: str = "",
        title: Optional[str] = None,
        mode: str = "ssh",
        distribution: Optional[str] = None,
        use_wsl: bool = False,
        use_powershell: bool = False,
        startup_mode: str = "terminal",
        explorer_root_directory: Optional[str] = None,
    ) -> TerminalSession:
        """
        Create a new terminal session.

        Args:
            host: Hostname or IP address to connect to
            directory: Working directory
            username: SSH username
            port: SSH port
            password: SSH password
            initial_command: Command to run after changing directory
            title: Human-friendly terminal title
            mode: Connection mode ("ssh" or "wsl")
            distribution: WSL distribution name when applicable
            use_wsl: Whether to prefer launching the local shell via WSL
            use_powershell: Whether to launch the local shell via Windows PowerShell

        Returns:
            TerminalSession object
        """
        session_id = str(uuid.uuid4())[:8]

        session = TerminalSession(
            session_id=session_id,
            group_id=group_id,
            host=host,
            directory=directory,
            username=username,
            port=port,
            password=password,
            initial_command=initial_command,
            initial_command_mode=initial_command_mode,
            agent_selection=agent_selection,
            custom_agent=custom_agent,
            title=title,
            mode=mode,
            distribution=distribution,
            use_wsl=use_wsl,
            use_powershell=use_powershell,
            startup_mode=startup_mode,
            explorer_root_directory=explorer_root_directory,
            status=SessionStatus.PENDING
        )

        with self.lock:
            self.sessions[session_id] = session

        logger.info(f"Created session {session_id} for {host}")
        return session

    def append_session_to_group(
        self,
        group_id: str,
        host: str,
        directory: str,
        username: str = "root",
        port: int = 22,
        password: Optional[str] = None,
        initial_command: Optional[str] = None,
        initial_command_mode: str = "command",
        agent_selection: str = "",
        custom_agent: str = "",
        title: Optional[str] = None,
        mode: str = "ssh",
        distribution: Optional[str] = None,
        use_wsl: bool = False,
        use_powershell: bool = False,
        startup_mode: str = "terminal",
        explorer_root_directory: Optional[str] = None,
    ) -> Optional[TerminalSession]:
        """Append one session to an existing group and update its count."""
        session_id = str(uuid.uuid4())[:8]
        session = TerminalSession(
            session_id=session_id,
            group_id=group_id,
            host=host,
            directory=directory,
            username=username,
            port=port,
            password=password,
            initial_command=initial_command,
            initial_command_mode=initial_command_mode,
            agent_selection=agent_selection,
            custom_agent=custom_agent,
            title=title,
            mode=mode,
            distribution=distribution,
            use_wsl=use_wsl,
            use_powershell=use_powershell,
            startup_mode=startup_mode,
            explorer_root_directory=explorer_root_directory,
            status=SessionStatus.PENDING,
        )

        with self.lock:
            group = self.groups.get(group_id)
            if group is None:
                return None
            self.sessions[session_id] = session
            group.terminal_count += 1

        logger.info(f"Appended session {session_id} to group {group_id} for {host}")
        return session

    def create_sessions(
        self,
        sessions_config: List[Dict[str, Any]],
        group_id: str,
    ) -> List[TerminalSession]:
        """
        Create multiple terminal sessions.

        Args:
            sessions_config: List of session configurations

        Returns:
            List of TerminalSession objects
        """
        created = []

        for config in sessions_config:
            try:
                mode = config.get("mode", "ssh")
                session = self.create_session(
                    group_id=group_id,
                    host=(
                        config.get("host")
                        or config.get("ip")
                        or config.get("hostname")
                        or config.get("distribution")
                        or "WSL"
                    ),
                    directory=config.get("directory", ""),
                    username=config.get("username", "root" if mode == "ssh" else ""),
                    port=config.get("port", 22),
                    password=config.get("password"),
                    initial_command=config.get("initial_command"),
                    initial_command_mode=str(config.get("initial_command_mode") or "command"),
                    agent_selection=str(config.get("agent_selection") or ""),
                    custom_agent=str(config.get("custom_agent") or ""),
                    title=config.get("title"),
                    mode=mode,
                    distribution=config.get("distribution"),
                    use_wsl=bool(config.get("use_wsl")),
                    use_powershell=bool(config.get("use_powershell")),
                    startup_mode=str(config.get("startup_mode") or "terminal"),
                    explorer_root_directory=config.get("explorer_root_directory"),
                )
                created.append(session)
            except Exception as e:
                logger.error(f"Failed to create session: {e}")
                continue

        return created

    def get_session(self, session_id: str) -> Optional[TerminalSession]:
        """Get a session by ID."""
        with self.lock:
            return self.sessions.get(session_id)

    def update_session_metadata(self, session_id: str, **updates: Any) -> Optional[TerminalSession]:
        """Update mutable session metadata without replacing the session id."""
        allowed_fields = {
            "host",
            "directory",
            "username",
            "port",
            "password",
            "initial_command",
            "initial_command_mode",
            "agent_selection",
            "custom_agent",
            "title",
            "distribution",
            "use_wsl",
            "use_powershell",
            "startup_mode",
            "explorer_root_directory",
        }
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return None

            for field_name, value in updates.items():
                if field_name in allowed_fields:
                    setattr(session, field_name, value)

            return session

    def get_all_sessions(self) -> List[TerminalSession]:
        """Get all sessions."""
        with self.lock:
            return list(self.sessions.values())

    def get_group(self, group_id: str) -> Optional[SessionGroup]:
        """Get a session group by ID."""
        with self.lock:
            return self.groups.get(group_id)

    def get_all_groups(self) -> List[SessionGroup]:
        """Get all known session groups."""
        with self.lock:
            return sorted(
                self.groups.values(),
                key=lambda group: (group.display_order, group.created_at),
            )

    def update_group_saved_session(
        self,
        group_id: str,
        saved_session_id: str,
        name: Optional[str] = None,
    ) -> Optional[SessionGroup]:
        """Update the saved-session target metadata for one launched group."""
        with self.lock:
            group = self.groups.get(group_id)
            if not group:
                return None

            group.saved_session_id = str(saved_session_id or "").strip()
            normalized_name = str(name or "").strip()
            if normalized_name:
                group.name = normalized_name
            return group

    def reorder_groups(self, ordered_group_ids: List[str]) -> List[SessionGroup]:
        """Persist a new display order for known groups."""
        with self.lock:
            current_groups = self.get_all_groups()
            if not current_groups:
                return []

            known_group_ids = {group.group_id for group in current_groups}
            next_order = []
            seen = set()

            for group_id in ordered_group_ids:
                if group_id in known_group_ids and group_id not in seen:
                    next_order.append(group_id)
                    seen.add(group_id)

            for group in current_groups:
                if group.group_id not in seen:
                    next_order.append(group.group_id)

            for index, group_id in enumerate(next_order):
                self.groups[group_id].display_order = index

            return [self.groups[group_id] for group_id in next_order]

    def get_group_sessions(self, group_id: str) -> List[TerminalSession]:
        """Get sessions belonging to one group."""
        with self.lock:
            return [s for s in self.sessions.values() if s.group_id == group_id]

    def get_active_sessions(self) -> List[TerminalSession]:
        """Get all active (connected) sessions."""
        with self.lock:
            return [
                s for s in self.sessions.values()
                if s.status == SessionStatus.CONNECTED
            ]

    def update_session_status(
        self,
        session_id: str,
        status: SessionStatus,
        error_message: Optional[str] = None
    ) -> bool:
        """
        Update session status.

        Args:
            session_id: Session ID
            status: New status
            error_message: Error message if status is ERROR

        Returns:
            True if session was found and updated
        """
        with self.lock:
            if session_id in self.sessions:
                session = self.sessions[session_id]
                session.status = status
                session.error_message = error_message

                if status == SessionStatus.CONNECTED:
                    session.connected_at = time.time()

                # Notify callbacks
                self._notify_callbacks(session_id, status)

                return True

        return False

    def close_session(self, session_id: str) -> bool:
        """
        Close a session.

        Args:
            session_id: Session ID to close

        Returns:
            True if session was found and closed
        """
        with self.lock:
            if session_id in self.sessions:
                session = self.sessions[session_id]
                session.status = SessionStatus.DISCONNECTED

                # Notify callbacks
                self._notify_callbacks(session_id, SessionStatus.DISCONNECTED)

                logger.info(f"Closed session {session_id}")
                return True

        return False

    def close_all_sessions(self):
        """Close all sessions."""
        with self.lock:
            session_ids = list(self.sessions.keys())

        for session_id in session_ids:
            self.close_session(session_id)

    def close_group_sessions(self, group_id: str):
        """Close all sessions in one group."""
        with self.lock:
            session_ids = [
                session_id
                for session_id, session in self.sessions.items()
                if session.group_id == group_id
            ]

        for session_id in session_ids:
            self.close_session(session_id)

    def remove_group(self, group_id: str):
        """Remove one group and its callback registrations once it is fully closed."""
        with self.lock:
            self._remove_group_sessions_locked(group_id)
            self.groups.pop(group_id, None)

    def remove_group_sessions(self, group_id: str) -> List[str]:
        """Remove tracked sessions for one group while keeping the group entry."""
        with self.lock:
            return self._remove_group_sessions_locked(group_id)

    def _remove_group_sessions_locked(self, group_id: str) -> List[str]:
        """Remove tracked sessions for one group. Caller must hold self.lock."""
        session_ids = [
            session_id
            for session_id, session in self.sessions.items()
            if session.group_id == group_id
        ]
        for session_id in session_ids:
            self.sessions.pop(session_id, None)
            self._session_callbacks.pop(session_id, None)
        return session_ids

    def reset_sessions(self):
        """Remove all tracked sessions and callbacks."""
        with self.lock:
            self.sessions.clear()
            self.groups.clear()
            self._session_callbacks.clear()

    def register_callback(
        self,
        session_id: str,
        callback: Callable[[SessionStatus], None]
    ):
        """Register a callback for session status changes."""
        with self.lock:
            if session_id not in self._session_callbacks:
                self._session_callbacks[session_id] = []
            self._session_callbacks[session_id].append(callback)

    def _notify_callbacks(self, session_id: str, status: SessionStatus):
        """Notify registered callbacks of status change.
        NOTE: always called while self.lock is already held — do not re-acquire."""
        callbacks = list(self._session_callbacks.get(session_id, []))
        for callback in callbacks:
            try:
                callback(status)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def get_session_count(self) -> int:
        """Get total number of sessions."""
        with self.lock:
            return len(self.sessions)

    def get_active_session_count(self) -> int:
        """Get number of active sessions."""
        return len(self.get_active_sessions())

    def clear_disconnected_sessions(self):
        """Remove disconnected sessions from the manager."""
        with self.lock:
            disconnected = [
                sid for sid, s in self.sessions.items()
                if s.status == SessionStatus.DISCONNECTED
            ]
            for sid in disconnected:
                del self.sessions[sid]

            # Also clean up callbacks
            for sid in disconnected:
                if sid in self._session_callbacks:
                    del self._session_callbacks[sid]

            active_group_counts: Dict[str, int] = {}
            for session in self.sessions.values():
                active_group_counts[session.group_id] = active_group_counts.get(session.group_id, 0) + 1
            disconnected_groups = [
                group_id for group_id in self.groups
                if group_id not in active_group_counts
            ]
            for group_id in disconnected_groups:
                del self.groups[group_id]
            for group_id, count in active_group_counts.items():
                group = self.groups.get(group_id)
                if group is not None:
                    group.terminal_count = count
