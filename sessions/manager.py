"""
Session Manager for GridVibe.
Manages SSH sessions for web-based terminal display.
"""

import copy
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from web.workspaces import (
    DEFAULT_WORKSPACE_ID,
    generate_workspace_id,
    normalize_workspace_id,
)

logger = logging.getLogger(__name__)

# Pane presentation restored when an already-live workspace window is reopened.
# Connection/process metadata deliberately stays untouched: saving a view must
# never retarget or restart a running terminal.
_SAVED_SESSION_VIEW_FIELDS = {
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
}
_UNCHANGED = object()


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
    username: str = "root"
    port: int = 22
    password: Optional[str] = field(default=None, repr=False)
    initial_command: Optional[str] = None
    initial_command_mode: str = "command"
    agent_selection: str = ""
    custom_agent: str = ""
    agent_auto_mode: bool = False
    title: Optional[str] = None
    mode: str = "ssh"
    distribution: Optional[str] = None
    use_wsl: bool = False
    use_powershell: bool = False
    startup_mode: str = "terminal"
    explorer_root_directory: Optional[str] = None
    explorer_tree_open: bool = False
    explorer_git_open: bool = False
    explorer_search_open: bool = False
    explorer_open_tabs: List[str] = field(default_factory=list)
    explorer_active_tab: str = ""
    explorer_tab_views: Dict[str, Any] = field(default_factory=dict)
    explorer_md_preset: str = ""
    explorer_md_font: str = ""
    explorer_source_font: str = ""
    explorer_theme: str = "dark"
    # Browser panes are tabbed: `browser_tabs` holds one HTTP(S) URL per open
    # tab and `browser_active_tab` indexes into it. `initial_command` stays the
    # active tab's URL so every existing browser-pane reader keeps working.
    browser_tabs: List[str] = field(default_factory=list)
    browser_active_tab: int = 0
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
            "agent_auto_mode": self.agent_auto_mode,
            "title": self.title,
            "mode": self.mode,
            "distribution": self.distribution,
            "use_wsl": self.use_wsl,
            "use_powershell": self.use_powershell,
            "startup_mode": self.startup_mode,
            "explorer_root_directory": self.explorer_root_directory,
            "explorer_tree_open": self.explorer_tree_open,
            "explorer_git_open": self.explorer_git_open,
            "explorer_search_open": self.explorer_search_open,
            "explorer_open_tabs": list(self.explorer_open_tabs),
            "explorer_active_tab": self.explorer_active_tab,
            "explorer_tab_views": dict(self.explorer_tab_views),
            "explorer_md_preset": self.explorer_md_preset,
            "explorer_md_font": self.explorer_md_font,
            "explorer_source_font": self.explorer_source_font,
            "explorer_theme": self.explorer_theme,
            "browser_tabs": list(self.browser_tabs),
            "browser_active_tab": self.browser_active_tab,
            "status": self.status.value,
            "created_at": self.created_at,
            "connected_at": self.connected_at,
            "error_message": self.error_message
        }


@dataclass
class Workspace:
    """One live terminal-window workspace."""
    workspace_id: str
    label: str = ""
    created_at: float = field(default_factory=time.time)
    active_group_id: str = ""
    # Live-only lifecycle hint: a workspace the user deliberately created empty
    # must survive the empty-workspace pruning that closes a workspace emptied
    # by a close or a move. Absence of groups alone cannot tell the two apart.
    # It is never written to runtime_state.json — a saved slot always has groups.
    retain_when_empty: bool = False

    def to_dict(self) -> dict:
        """Convert to a credential-free dictionary."""
        return {
            "workspace_id": self.workspace_id,
            "label": self.label,
            "created_at": self.created_at,
            "active_group_id": self.active_group_id,
            "retain_when_empty": self.retain_when_empty,
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
    workspace_id: str = DEFAULT_WORKSPACE_ID
    saved_session_id: str = ""
    workspace_layout: Optional[Dict[str, Any]] = None
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
            "workspace_id": self.workspace_id,
            "saved_session_id": self.saved_session_id,
            "workspace_layout": self.workspace_layout,
            "created_at": self.created_at,
        }


@dataclass
class GroupInstallation:
    """The published result of one atomic group install.

    ``displaced_session_ids`` are panes the install replaced. Their records are
    already gone from the manager; their transports are the caller's to close,
    *after* every shared lock is released.
    """
    group: SessionGroup
    sessions: List[TerminalSession] = field(default_factory=list)
    displaced_session_ids: List[str] = field(default_factory=list)


class SessionManager:
    """
    Manages terminal sessions for the web frontend.
    Handles SSH connections and provides session lifecycle management.
    """

    def __init__(self):
        """Initialize the session manager."""
        self.sessions: Dict[str, TerminalSession] = {}
        self.groups: Dict[str, SessionGroup] = {}
        self.workspaces: Dict[str, Workspace] = {
            DEFAULT_WORKSPACE_ID: Workspace(workspace_id=DEFAULT_WORKSPACE_ID)
        }
        # Workspace ids currently reserved by a launch in flight, by depth.
        # See reserve_workspace_launch: this replaced the empty-workspace grace
        # period, so cleanup no longer decides by wall clock.
        self._launch_reservations: Dict[str, int] = {}
        # Lock ordering: web/api.py's connection_lock may be held while taking
        # this lock; code holding this lock must never take connection_lock.
        self.lock = threading.RLock()

    def create_group(
        self,
        name: str,
        connection_mode: str,
        layout: str,
        terminal_count: int,
        group_id: Optional[str] = None,
        saved_session_id: str = "",
        workspace_layout: Optional[Dict[str, Any]] = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> SessionGroup:
        """Create one group of launched sessions.

        Workspace chrome density (surface mode) is deliberately *not* stored
        here: it is a single global App Setting (``workspace.surface_mode``)
        that every window reads live, so a group can never pin a stale copy.
        """
        resolved_group_id = str(group_id or uuid.uuid4().hex[:12])
        resolved_workspace_id = normalize_workspace_id(workspace_id)

        with self.lock:
            return self._create_group_locked(
                group_id=resolved_group_id,
                name=name,
                connection_mode=connection_mode,
                layout=layout,
                terminal_count=terminal_count,
                saved_session_id=saved_session_id,
                workspace_layout=workspace_layout,
                workspace_id=resolved_workspace_id,
            )

    def _create_group_locked(
        self,
        *,
        group_id: str,
        name: str,
        connection_mode: str,
        layout: str,
        terminal_count: int,
        saved_session_id: str = "",
        workspace_layout: Optional[Dict[str, Any]] = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> SessionGroup:
        """Publish one group record.

        Caller holds ``self.lock`` and has already normalized ``group_id`` and
        ``workspace_id``. Split out of :meth:`create_group` so
        :meth:`install_session_group` can publish a group and all of its panes
        inside one lock hold.
        """
        resolved_group_id = group_id
        resolved_workspace_id = workspace_id

        if resolved_workspace_id not in self.workspaces:
            raise ValueError("Workspace not found")
        existing_group = self.groups.get(resolved_group_id)
        if (
            existing_group is not None
            and existing_group.workspace_id != resolved_workspace_id
        ):
            raise ValueError("Session group belongs to another workspace")
        next_display_order = (
            existing_group.display_order
            if existing_group is not None
            else max(
                (
                    group.display_order
                    for group in self.groups.values()
                    if group.workspace_id == resolved_workspace_id
                ),
                default=-1,
            )
            + 1
        )

        group = SessionGroup(
            group_id=resolved_group_id,
            name=name or resolved_group_id,
            connection_mode=connection_mode,
            layout=layout,
            terminal_count=terminal_count,
            display_order=next_display_order,
            workspace_id=resolved_workspace_id,
            saved_session_id=str(saved_session_id or "").strip(),
            workspace_layout=workspace_layout,
        )
        self.groups[resolved_group_id] = group
        # The session window switches to a newly launched group, so mirror
        # that here: the hint is then right even before a window reports.
        workspace = self.workspaces[resolved_workspace_id]
        workspace.active_group_id = resolved_group_id
        # The workspace now holds content, so normal empty-workspace
        # pruning applies again from here on.
        workspace.retain_when_empty = False
        return group

    def create_workspace(
        self,
        label: str = "",
        workspace_id: Optional[str] = None,
        retain_when_empty: bool = False,
    ) -> Workspace:
        """Create and return a distinct live workspace.

        ``retain_when_empty`` marks a workspace the user created deliberately
        empty so cleanup does not sweep it before its first group arrives.
        """
        with self.lock:
            if workspace_id is None:
                while True:
                    resolved_workspace_id = generate_workspace_id()
                    if resolved_workspace_id not in self.workspaces:
                        break
            else:
                resolved_workspace_id = normalize_workspace_id(workspace_id)
            if resolved_workspace_id in self.workspaces:
                raise ValueError("Workspace already exists")
            workspace = Workspace(
                workspace_id=resolved_workspace_id,
                label=str(label or "").strip(),
                retain_when_empty=bool(retain_when_empty),
            )
            self.workspaces[resolved_workspace_id] = workspace
            return workspace

    def reserve_workspace_launch(self, workspace_id: str) -> str:
        """Reserve an existing workspace for a launch that is still in flight.

        A launch resolves its destination before it has a group to put in it,
        so the record is briefly empty and a concurrent cleanup would prune it
        out from under the launch. That window used to be covered by a
        five-second wall-clock exemption with no follow-up sweep (MW-06); it is
        now this explicit reservation, taken here and released in the caller's
        ``finally`` through :meth:`release_workspace_launch`.

        A reservation protects the record from cleanup and nothing else: the
        workspace still holds no groups, so it stays out of autosave and out of
        every user-visible workspace list until its first group is installed.

        Returns the reserved workspace id, or ``""`` when it does not exist.
        """
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            if resolved_workspace_id not in self.workspaces:
                return ""
            self._launch_reservations[resolved_workspace_id] = (
                self._launch_reservations.get(resolved_workspace_id, 0) + 1
            )
            return resolved_workspace_id

    def release_workspace_launch(self, workspace_id: str) -> None:
        """Release one reservation taken by :meth:`reserve_workspace_launch`."""
        if not workspace_id:
            return
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            remaining = self._launch_reservations.get(resolved_workspace_id, 0) - 1
            if remaining > 0:
                self._launch_reservations[resolved_workspace_id] = remaining
            else:
                self._launch_reservations.pop(resolved_workspace_id, None)

    def rename_workspace(self, workspace_id: str, label: str) -> Optional[Workspace]:
        """Set one live workspace's display label; ``None`` when unknown."""
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            workspace = self.workspaces.get(resolved_workspace_id)
            if workspace is None:
                return None
            workspace.label = str(label or "").strip()
            return workspace

    def remove_workspace(self, workspace_id: str) -> bool:
        """Remove one empty non-default workspace record.

        This is the rollback for a destination that was created for a launch
        that then failed: a dead workspace must never linger in the picker.
        A workspace that already owns groups is never removed.
        """
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        if resolved_workspace_id == DEFAULT_WORKSPACE_ID:
            return False
        with self.lock:
            if resolved_workspace_id not in self.workspaces:
                return False
            if any(
                group.workspace_id == resolved_workspace_id
                for group in self.groups.values()
            ):
                return False
            del self.workspaces[resolved_workspace_id]
            return True

    def get_workspace(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> Optional[Workspace]:
        """Return one live workspace by normalized id."""
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            return self.workspaces.get(resolved_workspace_id)

    def get_all_workspaces(self) -> List[Workspace]:
        """Return live workspaces in creation order, with default first."""
        with self.lock:
            return sorted(
                self.workspaces.values(),
                key=lambda workspace: (
                    workspace.workspace_id != DEFAULT_WORKSPACE_ID,
                    workspace.created_at,
                    workspace.workspace_id,
                ),
            )

    def set_active_group(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        group_id: Optional[str] = None,
        *,
        require_owned: bool = False,
    ) -> str:
        """Record which group the session window has in front; return the hint.

        Unknown ids are ignored rather than stored, so a stale tab can never
        point a workspace restore at a group that no longer exists.

        The one-argument form remains the legacy ``default`` workspace API.
        """
        if group_id is None:
            candidate = str(workspace_id or "").strip()
            resolved_workspace_id = DEFAULT_WORKSPACE_ID
        else:
            candidate = str(group_id or "").strip()
            resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            workspace = self.workspaces.get(resolved_workspace_id)
            if workspace is None:
                if require_owned:
                    raise ValueError("Workspace not found")
                return ""
            group = self.groups.get(candidate)
            if group is None or group.workspace_id != resolved_workspace_id:
                if require_owned:
                    raise ValueError("Session group does not belong to workspace")
            else:
                workspace.active_group_id = candidate
            return self.get_active_group_id(resolved_workspace_id)

    def get_active_group_id(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> str:
        """Return the active-group hint, or "" once that group is gone."""
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            workspace = self.workspaces.get(resolved_workspace_id)
            if workspace is None:
                return ""
            group = self.groups.get(workspace.active_group_id)
            if group is None or group.workspace_id != resolved_workspace_id:
                workspace.active_group_id = ""
            return workspace.active_group_id

    def _generate_session_id(self) -> str:
        """Return a short session id that is not already in use.

        Caller must hold self.lock so the id stays unique until it is inserted.
        """
        while True:
            session_id = uuid.uuid4().hex[:8]
            if session_id not in self.sessions:
                return session_id

    def _build_session(self, group_id: str, **fields: Any) -> TerminalSession:
        """Construct a PENDING TerminalSession with a fresh id.

        `fields` are TerminalSession dataclass fields (host, directory,
        username, port, ...); defaults come from the dataclass, so a new
        session field is added in one place. Caller must hold self.lock so
        the generated id stays unique until the session is inserted.
        """
        return TerminalSession(
            session_id=self._generate_session_id(),
            group_id=group_id,
            status=SessionStatus.PENDING,
            **fields,
        )

    def create_session(self, group_id: str, **fields: Any) -> TerminalSession:
        """Create a new terminal session (see TerminalSession for fields)."""
        with self.lock:
            session = self._build_session(group_id, **fields)
            self.sessions[session.session_id] = session

        logger.info(f"Created session {session.session_id} for {session.host}")
        return session

    def append_session_to_group(self, group_id: str, **fields: Any) -> Optional[TerminalSession]:
        """Append one session to an existing group and update its count."""
        with self.lock:
            group = self.groups.get(group_id)
            if group is None:
                return None
            session = self._build_session(group_id, **fields)
            self.sessions[session.session_id] = session
            group.terminal_count += 1

        logger.info(
            f"Appended session {session.session_id} to group {group_id} for {session.host}"
        )
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
                session = self.create_session(
                    group_id=group_id,
                    **self._session_launch_fields(config),
                )
                created.append(session)
            except Exception as e:
                logger.error(f"Failed to create session: {e}")
                continue

        return created

    @staticmethod
    def _session_launch_fields(config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize one requested pane into TerminalSession keyword fields.

        Pure: it touches no shared state, so a launch can stage every pane of a
        group before it takes the lock that publishes them together.
        """
        mode = config.get("mode", "ssh")
        return {
            "host": (
                config.get("host")
                or config.get("ip")
                or config.get("hostname")
                or config.get("distribution")
                or "WSL"
            ),
            "directory": config.get("directory", ""),
            "username": config.get("username", "root" if mode == "ssh" else ""),
            "port": config.get("port", 22),
            "password": config.get("password"),
            "initial_command": config.get("initial_command"),
            "initial_command_mode": str(config.get("initial_command_mode") or "command"),
            "agent_selection": str(config.get("agent_selection") or ""),
            "custom_agent": str(config.get("custom_agent") or ""),
            "agent_auto_mode": bool(config.get("agent_auto_mode")),
            "title": config.get("title"),
            "mode": mode,
            "distribution": config.get("distribution"),
            "use_wsl": bool(config.get("use_wsl")),
            "use_powershell": bool(config.get("use_powershell")),
            "startup_mode": str(config.get("startup_mode") or "terminal"),
            "explorer_root_directory": config.get("explorer_root_directory"),
            "explorer_tree_open": bool(config.get("explorer_tree_open")),
            "explorer_git_open": bool(config.get("explorer_git_open")),
            "explorer_search_open": bool(config.get("explorer_search_open")),
            "explorer_open_tabs": list(config.get("explorer_open_tabs") or []),
            "explorer_active_tab": str(config.get("explorer_active_tab") or ""),
            "explorer_tab_views": dict(config.get("explorer_tab_views") or {}),
            "explorer_md_preset": str(config.get("explorer_md_preset") or ""),
            "explorer_md_font": str(config.get("explorer_md_font") or ""),
            "explorer_source_font": str(config.get("explorer_source_font") or ""),
            "explorer_theme": "light" if config.get("explorer_theme") == "light" else "dark",
            "browser_tabs": list(config.get("browser_tabs") or []),
            "browser_active_tab": int(config.get("browser_active_tab") or 0),
        }

    def install_session_group(
        self,
        sessions_config: List[Dict[str, Any]],
        name: str,
        connection_mode: str,
        layout: str,
        group_id: Optional[str] = None,
        saved_session_id: str = "",
        workspace_layout: Optional[Dict[str, Any]] = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> GroupInstallation:
        """Install one complete group and every one of its panes atomically.

        The one group-publication path (MW-07). Launch, stable-group
        replacement, and restore all come through here, and every observer of
        the manager — autosave above all — sees either the complete old shape
        or the complete new one, never a group mid-assembly. Previously the
        group was created, then its panes inserted one at a time, each under
        its own lock hold, so a snapshot taken in between could persist an
        empty or half-populated group as if that were the workspace.

        Staging (normalizing each requested pane) happens before the lock
        because it is pure; validation, replacement, and publication all happen
        inside a single ``self.lock`` hold. Nothing slow runs under the lock:
        the transports of replaced panes are returned as
        ``displaced_session_ids`` for the caller to close afterwards.

        Raises ``ValueError`` — before touching anything — when the workspace
        is gone, when the group id is owned by another workspace or by another
        preset, or when no requested pane is usable.
        """
        resolved_group_id = str(group_id or uuid.uuid4().hex[:12])
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        resolved_saved_session_id = str(saved_session_id or "").strip()

        staged_fields: List[Dict[str, Any]] = []
        for config in sessions_config:
            try:
                staged_fields.append(self._session_launch_fields(config))
            except Exception as exc:
                logger.error(f"Failed to create session: {exc}")
        if not staged_fields:
            raise ValueError("No valid sessions were created")

        with self.lock:
            if resolved_workspace_id not in self.workspaces:
                raise ValueError("Workspace not found")
            existing_group = self.groups.get(resolved_group_id)
            if existing_group is not None:
                if existing_group.workspace_id != resolved_workspace_id:
                    raise ValueError("Session group belongs to another workspace")
                # Runtime ownership, checked before any teardown (MW-11): a
                # group id derived from a preset id must only ever replace the
                # *same* preset's group. Two preset ids that once sanitized to
                # one group id used to make the second launch silently tear
                # down the first preset's live panes.
                if existing_group.saved_session_id != resolved_saved_session_id:
                    raise ValueError("Session group id is already in use")

            displaced_session_ids = self._remove_group_sessions_locked(resolved_group_id)
            group = self._create_group_locked(
                group_id=resolved_group_id,
                name=name,
                connection_mode=connection_mode,
                layout=layout,
                terminal_count=len(staged_fields),
                saved_session_id=resolved_saved_session_id,
                workspace_layout=workspace_layout,
                workspace_id=resolved_workspace_id,
            )
            sessions = []
            for fields in staged_fields:
                session = self._build_session(resolved_group_id, **fields)
                self.sessions[session.session_id] = session
                sessions.append(session)
            group.terminal_count = len(sessions)

        logger.info(
            "Installed session group group_id=%s workspace_id=%s panes=%d displaced=%d",
            group.group_id,
            group.workspace_id,
            len(sessions),
            len(displaced_session_ids),
        )
        return GroupInstallation(
            group=group,
            sessions=sessions,
            displaced_session_ids=displaced_session_ids,
        )

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
        }
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return None

            for field_name, value in updates.items():
                if field_name in allowed_fields:
                    setattr(session, field_name, value)

            return session

    def update_browser_tab_strip(
        self,
        session_id: str,
        *,
        browser_tabs: List[str],
        browser_active_tab: int,
        initial_command: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically update tabs only while the session is still a browser pane."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session.startup_mode != "browser":
                return None
            session.browser_tabs = browser_tabs
            session.browser_active_tab = browser_active_tab
            session.initial_command = initial_command
            return session.to_dict()

    def merge_browser_tabs(
        self,
        session_id: str,
        *,
        browser_url: Optional[str],
        browser_active_tab: Any,
        default_browser_url: str,
    ) -> Optional[Dict[str, Any]]:
        """Atomically merge a single-URL browser request into the live tab strip.

        ``browser_url`` and ``default_browser_url`` are normalized by the web
        boundary. Existing tabs belong to an already-normalized browser
        session, so the read/merge/write transaction can stay inside one lock
        hold without making the session layer depend on web normalization.
        """
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return None

            was_browser = session.startup_mode == "browser"
            resolved_url = (
                browser_url
                or (session.initial_command if was_browser else "")
                or default_browser_url
            )
            browser_tabs = list(session.browser_tabs) if was_browser else []
            if not browser_tabs:
                browser_tabs = [resolved_url]

            requested_active_tab = (
                session.browser_active_tab
                if browser_active_tab is None
                else browser_active_tab
            )
            try:
                active_tab = int(requested_active_tab)
            except (TypeError, ValueError):
                active_tab = 0
            active_tab = max(0, min(len(browser_tabs) - 1, active_tab))
            browser_tabs[active_tab] = resolved_url

            session.host = "Browser"
            session.username = ""
            session.port = 22
            session.password = None
            session.initial_command = resolved_url
            session.initial_command_mode = "browser"
            session.startup_mode = "browser"
            session.browser_tabs = browser_tabs
            session.browser_active_tab = active_tab
            return session.to_dict()

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
                key=lambda group: (
                    self.workspaces.get(
                        group.workspace_id,
                        Workspace(group.workspace_id),
                    ).created_at,
                    group.display_order,
                    group.created_at,
                ),
            )

    def get_workspace_groups(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> List[SessionGroup]:
        """Return one workspace's groups in its independent display order."""
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            return sorted(
                (
                    group
                    for group in self.groups.values()
                    if group.workspace_id == resolved_workspace_id
                ),
                key=lambda group: (group.display_order, group.created_at),
            )

    def update_group_saved_session(
        self,
        group_id: str,
        saved_session_id: str,
        name: Optional[str] = None,
        *,
        layout: Optional[str] = None,
        workspace_layout: Any = _UNCHANGED,
        session_view_updates: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[SessionGroup]:
        """Update a live group's saved target and its reopenable view snapshot.

        ``session_view_updates`` is keyed by live session id. Only presentation
        fields are accepted, and the session must still belong to this group;
        connection/process metadata is never changed by a saved-view refresh.
        The entire check-and-update transaction stays inside one lock hold so a
        concurrent group move or close cannot update the wrong live session.
        """
        with self.lock:
            group = self.groups.get(group_id)
            if not group:
                return None

            group.saved_session_id = str(saved_session_id or "").strip()
            normalized_name = str(name or "").strip()
            if normalized_name:
                group.name = normalized_name
            normalized_layout = str(layout or "").strip()
            if normalized_layout:
                group.layout = normalized_layout
            if workspace_layout is not _UNCHANGED:
                group.workspace_layout = copy.deepcopy(workspace_layout)

            for session_id, updates in (session_view_updates or {}).items():
                session = self.sessions.get(str(session_id or "").strip())
                if session is None or session.group_id != group_id:
                    continue
                for field_name, value in updates.items():
                    if field_name in _SAVED_SESSION_VIEW_FIELDS:
                        setattr(session, field_name, copy.deepcopy(value))
            return group

    def reorder_groups(
        self,
        workspace_id: Any = DEFAULT_WORKSPACE_ID,
        ordered_group_ids: Optional[List[str]] = None,
    ) -> List[SessionGroup]:
        """Persist a display order inside one workspace.

        ``reorder_groups(ids)`` remains the legacy default-workspace form.
        """
        workspace_was_explicit = ordered_group_ids is not None
        if not workspace_was_explicit:
            ordered_group_ids = workspace_id
            resolved_workspace_id = DEFAULT_WORKSPACE_ID
        else:
            resolved_workspace_id = normalize_workspace_id(workspace_id)
        if not isinstance(ordered_group_ids, list):
            raise ValueError("ordered_group_ids must be a list")

        with self.lock:
            if (
                workspace_was_explicit
                and resolved_workspace_id not in self.workspaces
            ):
                raise ValueError("Workspace not found")
            current_groups = self.get_workspace_groups(resolved_workspace_id)
            known_group_ids = {group.group_id for group in current_groups}
            if workspace_was_explicit and any(
                group_id not in known_group_ids
                for group_id in ordered_group_ids
            ):
                raise ValueError(
                    "All groups must belong to the requested workspace"
                )
            if not current_groups:
                return []
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

    def get_workspace_sessions(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> List[TerminalSession]:
        """Return sessions belonging to groups owned by one workspace."""
        resolved_workspace_id = normalize_workspace_id(workspace_id)
        with self.lock:
            ordered_group_ids = [
                group.group_id
                for group in self.get_workspace_groups(resolved_workspace_id)
            ]
            sessions_by_group = {
                group_id: [
                    session
                    for session in self.sessions.values()
                    if session.group_id == group_id
                ]
                for group_id in ordered_group_ids
            }
            return [
                session
                for group_id in ordered_group_ids
                for session in sessions_by_group[group_id]
            ]

    def find_saved_session_group(self, saved_session_id: str) -> Optional[SessionGroup]:
        """Find a live saved-preset-backed group globally."""
        candidate = str(saved_session_id or "").strip()
        if not candidate:
            return None
        with self.lock:
            return next(
                (
                    group
                    for group in self.groups.values()
                    if group.saved_session_id == candidate
                ),
                None,
            )

    def _compact_workspace_order_locked(self, workspace_id: str) -> None:
        """Compact one workspace's display order. Caller holds ``self.lock``."""
        groups = sorted(
            (
                group
                for group in self.groups.values()
                if group.workspace_id == workspace_id
            ),
            key=lambda group: (group.display_order, group.created_at),
        )
        for index, group in enumerate(groups):
            group.display_order = index

    def move_group(
        self,
        group_id: str,
        target_workspace_id: str,
    ) -> Optional[SessionGroup]:
        """Move a group without recreating any of its terminal sessions."""
        resolved_target_id = normalize_workspace_id(target_workspace_id)
        with self.lock:
            group = self.groups.get(str(group_id or "").strip())
            if group is None:
                return None
            if resolved_target_id not in self.workspaces:
                raise ValueError("Workspace not found")
            source_workspace_id = group.workspace_id
            if source_workspace_id == resolved_target_id:
                return group

            next_order = max(
                (
                    candidate.display_order
                    for candidate in self.groups.values()
                    if candidate.workspace_id == resolved_target_id
                ),
                default=-1,
            ) + 1
            group.workspace_id = resolved_target_id
            group.display_order = next_order
            # The destination now holds content: a deliberately empty workspace
            # stops being retained the moment its first group arrives.
            self.workspaces[resolved_target_id].retain_when_empty = False
            source_workspace = self.workspaces.get(source_workspace_id)
            if source_workspace and source_workspace.active_group_id == group.group_id:
                source_workspace.active_group_id = ""
            self._compact_workspace_order_locked(source_workspace_id)
            self._compact_workspace_order_locked(resolved_target_id)
            return group

    def snapshot_live_workspaces(self) -> Dict[str, Dict[str, Any]]:
        """Take one consistent, password-free snapshot of all live workspaces."""
        with self.lock:
            snapshots: Dict[str, Dict[str, Any]] = {}
            for workspace in self.get_all_workspaces():
                groups = []
                for group in self.get_workspace_groups(workspace.workspace_id):
                    sessions = [
                        session.to_dict()
                        for session in self.sessions.values()
                        if session.group_id == group.group_id
                    ]
                    if not sessions:
                        continue
                    group_data = group.to_dict()
                    group_data["sessions"] = sessions
                    groups.append(group_data)
                if not groups:
                    continue
                captured_group_ids = {group["group_id"] for group in groups}
                snapshots[workspace.workspace_id] = {
                    "workspace_id": workspace.workspace_id,
                    "label": workspace.label,
                    "created_at": workspace.created_at,
                    "active_group_id": (
                        workspace.active_group_id
                        if workspace.active_group_id in captured_group_ids
                        else ""
                    ),
                    "groups": groups,
                }
            return snapshots

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
            group = self.groups.get(group_id)
            self._remove_group_sessions_locked(group_id)
            self.groups.pop(group_id, None)
            if group is not None:
                workspace = self.workspaces.get(group.workspace_id)
                if workspace and workspace.active_group_id == group_id:
                    workspace.active_group_id = ""
                self._compact_workspace_order_locked(group.workspace_id)

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
        return session_ids

    def reset_sessions(self):
        """Remove all tracked sessions."""
        with self.lock:
            self.sessions.clear()
            self.groups.clear()
            self.workspaces.clear()
            self._launch_reservations.clear()
            self.workspaces[DEFAULT_WORKSPACE_ID] = Workspace(
                workspace_id=DEFAULT_WORKSPACE_ID
            )

    def get_session_count(self) -> int:
        """Get total number of sessions."""
        with self.lock:
            return len(self.sessions)

    def clear_disconnected_sessions(self) -> List[str]:
        """Remove disconnected sessions, then whatever they emptied.

        There is no wall-clock exemption any more (MW-06). A group is published
        together with all of its panes by :meth:`install_session_group`, so an
        empty group is always a group whose panes have gone and is swept at
        once — an explicitly closed group can no longer outlive its last pane
        by riding a grace period that nothing sweeps afterwards. A workspace
        resolved as a launch destination before its first group exists is
        protected by :meth:`reserve_workspace_launch` instead, and a workspace
        the user created deliberately empty by ``retain_when_empty``.

        Returns non-default workspace ids pruned after their last group left.
        """
        with self.lock:
            disconnected = [
                sid for sid, s in self.sessions.items()
                if s.status == SessionStatus.DISCONNECTED
            ]
            for sid in disconnected:
                del self.sessions[sid]

            active_group_counts: Dict[str, int] = {}
            for session in self.sessions.values():
                active_group_counts[session.group_id] = active_group_counts.get(session.group_id, 0) + 1
            disconnected_groups = [
                group_id for group_id in self.groups
                if group_id not in active_group_counts
            ]
            affected_workspace_ids = {
                self.groups[group_id].workspace_id
                for group_id in disconnected_groups
                if group_id in self.groups
            }
            for group_id in disconnected_groups:
                del self.groups[group_id]
            for workspace_id in affected_workspace_ids:
                self._compact_workspace_order_locked(workspace_id)
                workspace = self.workspaces.get(workspace_id)
                if workspace is not None and workspace.active_group_id in disconnected_groups:
                    workspace.active_group_id = ""
            for group_id, count in active_group_counts.items():
                group = self.groups.get(group_id)
                if group is not None:
                    group.terminal_count = count

            pruned_workspace_ids = []
            live_workspace_ids = {
                group.workspace_id for group in self.groups.values()
            }
            for workspace_id, workspace in list(self.workspaces.items()):
                if workspace_id == DEFAULT_WORKSPACE_ID or workspace_id in live_workspace_ids:
                    continue
                # A deliberately empty workspace (Workspace ▸ New Workspace) is
                # kept until its first group clears the flag; only then does
                # normal empty-workspace pruning apply to it.
                if workspace.retain_when_empty:
                    continue
                # A launch that has resolved this destination but not yet
                # installed its group holds a reservation over it.
                if self._launch_reservations.get(workspace_id, 0) > 0:
                    continue
                del self.workspaces[workspace_id]
                pruned_workspace_ids.append(workspace_id)
            return sorted(pruned_workspace_ids)
