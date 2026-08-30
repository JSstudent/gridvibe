"""Saved launcher presets: persistence and session-config normalization.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Covers the launcher
session-config normalization helpers, the built-in default preset, and the
``saved_sessions.json`` load/save/upsert/delete flows. ``web.api`` re-exports
every name for backwards compatibility.
"""

import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from web.config import runtime_config
from web.paths import BASE_DIR
from web.saved_session_store import UNCHANGED, SavedSessionStore
from web.secrets import _decrypt_password, _encrypt_password
from web.session_presentation import (  # noqa: F401 - compatibility re-exports
    BROWSER_MAX_TABS,
    BROWSER_MAX_URL_LENGTH,
    DEFAULT_BROWSER_URL,
    EXPLORER_DIFF_MODES,
    EXPLORER_EDITOR_FONT_MAX,
    EXPLORER_EDITOR_FONT_MIN,
    EXPLORER_FONT_ALIASES,
    EXPLORER_MAX_DIFF_COMMIT_LENGTH,
    EXPLORER_MAX_MARKDOWN_FOLDS,
    EXPLORER_MAX_MARKDOWN_LINE,
    EXPLORER_MAX_OPEN_TABS,
    EXPLORER_MAX_TAB_PATH_LENGTH,
    EXPLORER_MAX_TAB_VIEW_IDENTITY_LENGTH,
    EXPLORER_MD_FONTS,
    EXPLORER_MD_PRESETS,
    EXPLORER_PREVIEW_TAB_KEY,
    EXPLORER_SIDEBAR_PANELS,
    EXPLORER_SIDEBAR_WIDTH_MIN,
    EXPLORER_SOURCE_FONTS,
    EXPLORER_TAB_VIEW_MODES,
    MAX_STORED_SESSION_PANES,
    _normalize_browser_active_tab,
    _normalize_browser_tabs,
    _normalize_browser_url,
    _normalize_explorer_active_tab,
    _normalize_explorer_diff_target,
    _normalize_explorer_git_expanded,
    _normalize_explorer_git_pin_kind,
    _normalize_explorer_line_wrap,
    _normalize_explorer_markdown_folds,
    _normalize_explorer_md_choice,
    _normalize_explorer_open_tabs,
    _normalize_explorer_sidebar_width,
    _normalize_explorer_tab_font_size,
    _normalize_explorer_tab_path,
    _normalize_explorer_tab_views,
    _normalize_explorer_theme,
    _normalize_explorer_tree_expanded,
    _normalize_explorer_view_identity,
    _normalize_explorer_view_snapshot,
    _normalize_scroll_map,
    _normalize_workspace_layout,
)

logger = logging.getLogger(__name__)

SAVED_SESSIONS_PATH = os.path.join(BASE_DIR, "saved_sessions.json")
DEFAULT_SAVED_SESSION_ID = "default-session"
DEFAULT_SAVED_SESSION_NAME = "Default Session"

# Scratch launches: a launch that carries no saved-preset identity (the
# built-in "Default Session", or any hand-filled form) is disposable, is named
# after its connection target, and may be repeated. `build_unique_session_name`
# gives the second and later launches a " (n)" suffix so the session tabs stay
# tellable apart, and skips any name a saved preset already owns so a scratch
# session never impersonates a saved one.
SCRATCH_NAME_MAX_SUFFIX = 999

# How many distinct connection targets the launcher's per-mode dropdown offers.
# It is a shortcut list, not a session browser — Import Session is still the way
# to reach an old preset in full.
CONNECTION_TARGET_LIMIT = 20

def _default_terminal_entries():
    """Build default per-terminal settings."""
    return [
        {
            "title": f"Terminal {index + 1}",
            "directory": "",
            "initial_command": "",
            "initial_command_mode": "command",
            "startup_mode": "terminal",
            "agent_selection": "",
            "custom_agent": "",
            "agent_auto_mode": False,
            "explorer_tree_open": False,
            "explorer_git_open": False,
            "explorer_git_follow_browsing": False,
            "explorer_git_pin_active": False,
            "explorer_git_pinned_path": "",
            "explorer_git_pin_kind": "dir",
            "explorer_search_open": False,
            "explorer_sidebar_width": EXPLORER_SIDEBAR_WIDTH_MIN + 80,
            "explorer_sidebar_scroll": {},
            "explorer_tree_expanded": [],
            "explorer_git_expanded": [],
            "explorer_open_tabs": [],
            "explorer_active_tab": "",
            "explorer_tab_views": {},
            "explorer_md_preset": "",
            "explorer_md_font": "",
            "explorer_source_font": "",
            "explorer_theme": "dark",
            "browser_tabs": [],
            "browser_active_tab": 0,
            "distribution": "",
            "use_wsl": False,
            "use_powershell": False,
        }
        for index in range(runtime_config.max_sessions)
    ]


def _normalize_connection_mode(value: Any) -> str:
    """Normalize the requested connection mode."""
    return "wsl" if value == "wsl" else "ssh"


def _normalize_layout(value: Any, count: int) -> str:
    """Normalize the requested layout for a terminal count."""
    if count == 2:
        return value if value in {"vertical", "horizontal"} else "vertical"
    if count == 3:
        return value if value in {"vertical", "horizontal", "split"} else "vertical"
    if count >= 4:
        return "grid"
    return "single"


def _normalize_startup_mode(value: Any, connection_mode: str = "ssh") -> str:
    """Normalize the requested per-pane startup mode."""
    normalized = str(value or "").strip().lower()
    if normalized == "agent":
        return "agent"
    if normalized == "browser" and connection_mode == "wsl":
        return "browser"
    if normalized == "explorer" and connection_mode in {"ssh", "wsl"}:
        return "explorer"
    return "terminal"


def _default_session_config() -> Dict[str, Any]:
    """Default saved setup used by the launcher form."""
    default_count = min(4, runtime_config.max_sessions)
    return {
        "connection_mode": "ssh",
        "terminal_count": default_count,
        "layout": _normalize_layout("grid", default_count),
        "ssh": {
            "host": "",
            "username": "ubuntu",
            "password": "",
            "port": 22,
            "default_dir": "",
        },
        "wsl": {
            "distribution": "",
            "username": "",
            "default_dir": "",
        },
        "terminals": _default_terminal_entries(),
        "workspace_layout": None,
    }


def _default_saved_session_entry() -> Dict[str, Any]:
    """Return the built-in default launcher preset as a virtual saved session."""
    now = _utc_timestamp()
    return {
        "id": DEFAULT_SAVED_SESSION_ID,
        "name": DEFAULT_SAVED_SESSION_NAME,
        "created_at": now,
        "updated_at": now,
        "config": _default_session_config(),
    }


def _normalize_terminal_entries(
    entries: Any,
    connection_mode: str = "ssh",
    minimum_count: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Ensure the stored pane list is schema-bounded and complete.

    ``runtime_config.max_sessions`` is a launch preference, not a persistence
    bound.  Existing extra entries therefore remain readable and survive an
    unrelated preset write after that preference is lowered.
    """
    normalized = []
    entries = entries if isinstance(entries, list) else []
    requested_count = runtime_config.max_sessions if minimum_count is None else minimum_count
    target_count = min(
        MAX_STORED_SESSION_PANES,
        max(1, int(requested_count), len(entries)),
    )

    for index in range(target_count):
        entry = entries[index] if index < len(entries) and isinstance(entries[index], dict) else {}
        use_powershell = bool(entry.get("use_powershell"))
        raw_startup_mode = entry.get("startup_mode")
        if raw_startup_mode is None:
            raw_startup_mode = "agent" if entry.get("initial_command_mode") == "agent" else "terminal"
        startup_mode = _normalize_startup_mode(raw_startup_mode, connection_mode)
        open_tabs = _normalize_explorer_open_tabs(entry.get("explorer_open_tabs"))
        initial_command = str(entry.get("initial_command") or "")
        # Browser tabs only exist for browser panes; the active tab's URL is
        # mirrored into `initial_command` so the single-URL readers (pane
        # launch, `_normalize_browser_url`, the session model) stay authoritative.
        browser_tabs = (
            _normalize_browser_tabs(entry.get("browser_tabs"), initial_command)
            if startup_mode == "browser"
            else []
        )
        browser_active_tab = _normalize_browser_active_tab(
            entry.get("browser_active_tab"), browser_tabs
        )
        if browser_tabs:
            initial_command = browser_tabs[browser_active_tab]
        normalized.append(
            {
                "title": str(entry.get("title") or f"Terminal {index + 1}"),
                "directory": str(entry.get("directory") or ""),
                "initial_command": initial_command,
                "initial_command_mode": startup_mode if startup_mode in {"agent", "explorer", "browser"} else "command",
                "startup_mode": startup_mode,
                "agent_selection": str(entry.get("agent_selection") or ""),
                "custom_agent": str(entry.get("custom_agent") or ""),
                "agent_auto_mode": startup_mode == "agent" and bool(entry.get("agent_auto_mode")),
                "explorer_tree_open": bool(entry.get("explorer_tree_open")),
                "explorer_git_open": bool(entry.get("explorer_git_open")),
                "explorer_git_follow_browsing": bool(
                    entry.get("explorer_git_follow_browsing")
                ),
                "explorer_git_pin_active": bool(entry.get("explorer_git_pin_active")),
                "explorer_git_pinned_path": _normalize_explorer_tab_path(
                    entry.get("explorer_git_pinned_path")
                ),
                "explorer_git_pin_kind": _normalize_explorer_git_pin_kind(
                    entry.get("explorer_git_pin_kind", "dir")
                ),
                "explorer_search_open": bool(entry.get("explorer_search_open")),
                "explorer_sidebar_width": _normalize_explorer_sidebar_width(
                    entry.get("explorer_sidebar_width")
                ) or (EXPLORER_SIDEBAR_WIDTH_MIN + 80),
                "explorer_sidebar_scroll": _normalize_scroll_map(
                    entry.get("explorer_sidebar_scroll"), EXPLORER_SIDEBAR_PANELS
                ),
                "explorer_tree_expanded": _normalize_explorer_tree_expanded(
                    entry.get("explorer_tree_expanded")
                ),
                "explorer_git_expanded": _normalize_explorer_git_expanded(
                    entry.get("explorer_git_expanded")
                ),
                "explorer_open_tabs": open_tabs,
                "explorer_active_tab": _normalize_explorer_active_tab(entry.get("explorer_active_tab"), open_tabs),
                "explorer_tab_views": _normalize_explorer_tab_views(entry.get("explorer_tab_views"), open_tabs),
                "explorer_md_preset": _normalize_explorer_md_choice(entry.get("explorer_md_preset"), EXPLORER_MD_PRESETS),
                "explorer_md_font": _normalize_explorer_md_choice(
                    entry.get("explorer_md_font"), EXPLORER_MD_FONTS, EXPLORER_FONT_ALIASES
                ),
                "explorer_source_font": _normalize_explorer_md_choice(
                    entry.get("explorer_source_font"), EXPLORER_SOURCE_FONTS, EXPLORER_FONT_ALIASES
                ),
                "explorer_theme": _normalize_explorer_theme(entry.get("explorer_theme")),
                "browser_tabs": browser_tabs,
                "browser_active_tab": browser_active_tab,
                "distribution": str(entry.get("distribution") or ""),
                "use_wsl": bool(entry.get("use_wsl")) and not use_powershell,
                "use_powershell": use_powershell,
            }
        )

    return normalized


_LIVE_SESSION_VIEW_FIELDS = (
    "explorer_tree_open",
    "explorer_git_open",
    "explorer_git_follow_browsing",
    "explorer_git_pin_active",
    "explorer_git_pinned_path",
    "explorer_git_pin_kind",
    "explorer_search_open",
    "explorer_sidebar_width",
    "explorer_sidebar_scroll",
    "explorer_tree_expanded",
    "explorer_git_expanded",
    "explorer_open_tabs",
    "explorer_active_tab",
    "explorer_tab_views",
    "explorer_md_preset",
    "explorer_md_font",
    "explorer_source_font",
    "explorer_theme",
    "browser_tabs",
    "browser_active_tab",
)


def build_live_session_view_updates(
    raw_config: Dict[str, Any],
    saved_config: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Pair normalized saved view state with the live pane ids that supplied it.

    ``session_id`` is request-only identity added by the terminals page. The
    saved-session normalizer intentionally strips it before persistence, while
    this helper uses it to refresh an already-live workspace safely by identity
    rather than by a pane's current visual position.

    The **value** comes from the normalized preset, so what is written back to
    the pane is bounded exactly as what was stored. The **field set** comes
    from what the page actually stated, because the normalizer fills every
    absent key with a default: taking the field set from it too meant a pane
    described without its Git pin was handed the *default* pin -- an unpinned
    pane, written onto live state by a save the user asked for to record a
    pinned one. Only the page can say a pin was cleared; silence cannot.
    """
    raw_terminals = raw_config.get("terminals")
    saved_terminals = saved_config.get("terminals")
    if not isinstance(raw_terminals, list) or not isinstance(saved_terminals, list):
        return {}

    updates: Dict[str, Dict[str, Any]] = {}
    for index, raw_terminal in enumerate(raw_terminals):
        if index >= len(saved_terminals):
            break
        if not isinstance(raw_terminal, dict) or not isinstance(saved_terminals[index], dict):
            continue
        session_id = str(raw_terminal.get("session_id") or "").strip()
        if not session_id:
            continue
        updates[session_id] = {
            field_name: saved_terminals[index][field_name]
            for field_name in _LIVE_SESSION_VIEW_FIELDS
            if field_name in saved_terminals[index] and field_name in raw_terminal
        }
    return updates


def _normalize_session_config(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize persisted form state before saving or returning it."""
    data = data or {}
    default_config = _default_session_config()
    connection_mode = _normalize_connection_mode(data.get("connection_mode"))

    try:
        terminal_count = int(data.get("terminal_count", default_config["terminal_count"]))
    except (TypeError, ValueError):
        terminal_count = default_config["terminal_count"]

    terminal_count = max(1, min(MAX_STORED_SESSION_PANES, terminal_count))
    ssh_data = data.get("ssh") if isinstance(data.get("ssh"), dict) else {}
    wsl_data = data.get("wsl") if isinstance(data.get("wsl"), dict) else {}

    try:
        ssh_port = int(ssh_data.get("port", default_config["ssh"]["port"])) # type: ignore
    except (TypeError, ValueError):
        ssh_port = default_config["ssh"]["port"]

    ssh_port = max(1, min(65535, ssh_port))

    return {
        "connection_mode": connection_mode,
        "terminal_count": terminal_count,
        "layout": _normalize_layout(data.get("layout"), terminal_count),
        "ssh": {
            "host": str(ssh_data.get("host") or ""),# type: ignore
            "username": str(ssh_data.get("username") or default_config["ssh"]["username"]),# type: ignore
            "password": str(ssh_data.get("password") or ""), # type: ignore
            "port": ssh_port,
            "default_dir": str(ssh_data.get("default_dir") or ""),# type: ignore
        },
        "wsl": {
            "distribution": str(wsl_data.get("distribution") or ""),# type: ignore
            "username": str(wsl_data.get("username") or ""),# type: ignore
            "default_dir": str(wsl_data.get("default_dir") or default_config["wsl"]["default_dir"]),# type: ignore
        },
        "terminals": _normalize_terminal_entries(
            data.get("terminals"),
            connection_mode,
            minimum_count=max(terminal_count, len(default_config["terminals"])),
        ),
        "workspace_layout": _normalize_workspace_layout(data.get("workspace_layout"), terminal_count),
    }


def apply_live_ssh_credential(
    config: Dict[str, Any],
    credential: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Fill a preset's *empty* SSH password from the live group being saved.

    The browser has no password to send — it never receives one — so a config
    arriving from the terminal page always carries ``ssh.password: ""``.  Saving
    a group that way therefore used to store a credential-free preset, and every
    later launch from it failed on "No authentication methods available".  The
    password the user actually authenticated with was in memory the whole time;
    the server just never looked.

    Two invariants keep this narrow:

    * **Never clobber.**  A preset that already stores a password keeps it, so
      re-saving a workspace can never downgrade a working credential — the same
      property ``_merge_workspace_session_config`` gets from leaving ``ssh``
      alone.
    * **Never mismatch.**  The credential is attached only when the preset still
      names that exact host, user, and port — the same rule
      ``web.workspaces._preset_ssh_credential`` applies when reading one back.

    The caller resolves ``credential`` in-process; it must never reach a
    response body, a runtime snapshot, or the log.
    """
    if not isinstance(credential, dict) or not credential.get("password"):
        return config
    if str(config.get("connection_mode") or "ssh") != "ssh":
        return config

    ssh_config = config.get("ssh") if isinstance(config.get("ssh"), dict) else None
    if not ssh_config or ssh_config.get("password"):
        return config

    try:
        stored_port = int(ssh_config.get("port") or 22)
        live_port = int(credential.get("port") or 22)
    except (TypeError, ValueError):
        return config
    if (
        str(ssh_config.get("host") or "").strip() != str(credential.get("host") or "").strip()
        or str(ssh_config.get("username") or "") != str(credential.get("username") or "")
        or stored_port != live_port
    ):
        return config

    ssh_config["password"] = credential["password"]
    return config


def _merge_workspace_session_config(
    base_config: Dict[str, Any],
    workspace_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply saveable live-workspace state without changing launcher setup fields."""
    base = _normalize_session_config(base_config)
    workspace_input = dict(workspace_config or {})
    workspace_input["connection_mode"] = base["connection_mode"]
    workspace = _normalize_session_config(workspace_input)
    merged = json.loads(json.dumps(base))

    merged["terminal_count"] = workspace["terminal_count"]
    merged["layout"] = workspace["layout"]
    merged["workspace_layout"] = workspace["workspace_layout"]

    for index in range(min(len(merged["terminals"]), len(workspace["terminals"]))):
        saved_terminal = merged["terminals"][index]
        workspace_terminal = workspace["terminals"][index]
        startup_mode = workspace_terminal["startup_mode"]

        saved_terminal["startup_mode"] = startup_mode
        saved_terminal["initial_command_mode"] = (
            startup_mode if startup_mode in {"agent", "explorer", "browser"} else "command"
        )

        # Agent identity and command are required mode metadata. Unlike the
        # terminal directory, these values must follow the live pane so the
        # saved preset can recreate Codex, Claude, or a custom agent pane.
        if startup_mode == "agent":
            agent_selection = workspace_terminal["agent_selection"]
            custom_agent = workspace_terminal["custom_agent"]
            initial_command = workspace_terminal["initial_command"]
            if not initial_command:
                initial_command = custom_agent if agent_selection == "other" else agent_selection
            saved_terminal["agent_selection"] = agent_selection
            saved_terminal["custom_agent"] = custom_agent
            saved_terminal["agent_auto_mode"] = workspace_terminal["agent_auto_mode"]
            saved_terminal["initial_command"] = initial_command
        elif (
            base["terminals"][index]["initial_command_mode"] == "agent"
            or base["terminals"][index]["agent_selection"]
            or base["terminals"][index]["custom_agent"]
        ):
            saved_terminal["agent_selection"] = ""
            saved_terminal["custom_agent"] = ""
            saved_terminal["agent_auto_mode"] = False
            saved_terminal["initial_command"] = ""

        saved_terminal["explorer_tree_open"] = (
            startup_mode == "explorer" and workspace_terminal["explorer_tree_open"]
        )
        saved_terminal["explorer_git_open"] = (
            startup_mode == "explorer" and workspace_terminal["explorer_git_open"]
        )
        saved_terminal["explorer_git_follow_browsing"] = (
            startup_mode == "explorer"
            and workspace_terminal["explorer_git_follow_browsing"]
        )
        saved_terminal["explorer_git_pin_active"] = (
            startup_mode == "explorer" and workspace_terminal["explorer_git_pin_active"]
        )
        saved_terminal["explorer_git_pinned_path"] = (
            workspace_terminal["explorer_git_pinned_path"]
            if startup_mode == "explorer"
            else ""
        )
        saved_terminal["explorer_git_pin_kind"] = (
            workspace_terminal["explorer_git_pin_kind"]
            if startup_mode == "explorer"
            else "dir"
        )
        saved_terminal["explorer_search_open"] = (
            startup_mode == "explorer" and workspace_terminal.get("explorer_search_open", False)
        )
        saved_terminal["explorer_sidebar_width"] = (
            workspace_terminal["explorer_sidebar_width"]
            if startup_mode == "explorer"
            else EXPLORER_SIDEBAR_WIDTH_MIN + 80
        )
        saved_terminal["explorer_sidebar_scroll"] = (
            workspace_terminal["explorer_sidebar_scroll"]
            if startup_mode == "explorer"
            else {}
        )
        saved_terminal["explorer_tree_expanded"] = (
            workspace_terminal["explorer_tree_expanded"]
            if startup_mode == "explorer"
            else []
        )
        saved_terminal["explorer_git_expanded"] = (
            workspace_terminal["explorer_git_expanded"]
            if startup_mode == "explorer"
            else []
        )
        saved_terminal["explorer_open_tabs"] = (
            workspace_terminal["explorer_open_tabs"] if startup_mode == "explorer" else []
        )
        saved_terminal["explorer_active_tab"] = (
            workspace_terminal["explorer_active_tab"] if startup_mode == "explorer" else ""
        )
        saved_terminal["explorer_tab_views"] = (
            workspace_terminal["explorer_tab_views"] if startup_mode == "explorer" else {}
        )
        saved_terminal["explorer_md_preset"] = (
            workspace_terminal["explorer_md_preset"] if startup_mode == "explorer" else ""
        )
        saved_terminal["explorer_md_font"] = (
            workspace_terminal["explorer_md_font"] if startup_mode == "explorer" else ""
        )
        saved_terminal["explorer_source_font"] = (
            workspace_terminal["explorer_source_font"] if startup_mode == "explorer" else ""
        )
        saved_terminal["explorer_theme"] = (
            workspace_terminal["explorer_theme"] if startup_mode == "explorer" else "dark"
        )

        # Browser panes persist their full live tab strip: Save Workspace on a
        # pane with three tabs open must relaunch with those same three tabs and
        # the same one selected. `_normalize_terminal_entries` already mirrored
        # the active tab's URL into `initial_command`, so the single-URL readers
        # follow the tab list rather than a stale configured value.
        if startup_mode == "browser":
            browser_tabs = workspace_terminal["browser_tabs"]
            if not browser_tabs:
                browser_tabs = _normalize_browser_tabs(
                    [],
                    base["terminals"][index]["initial_command"]
                    if base["terminals"][index]["startup_mode"] == "browser"
                    else DEFAULT_BROWSER_URL,
                )
            saved_terminal["browser_tabs"] = browser_tabs
            saved_terminal["browser_active_tab"] = _normalize_browser_active_tab(
                workspace_terminal["browser_active_tab"], browser_tabs
            )
            saved_terminal["initial_command"] = browser_tabs[
                saved_terminal["browser_active_tab"]
            ]
        else:
            saved_terminal["browser_tabs"] = []
            saved_terminal["browser_active_tab"] = 0

        # The pane header's shell dropdown restarts a live Local Repo pane under
        # another shell family, so a workspace save has to follow the shell the
        # pane runs *now*. Without this the launcher-configured shell always won
        # and re-saving a launched session silently reverted a cmd/PowerShell/WSL
        # switch. Explorer and browser panes have no shell, so their flags stay
        # as configured (`buildPaneLaunchFields` zeroes them at launch anyway).
        if base["connection_mode"] == "wsl" and startup_mode in {"terminal", "agent"}:
            saved_terminal["use_wsl"] = workspace_terminal["use_wsl"]
            saved_terminal["use_powershell"] = workspace_terminal["use_powershell"]
            saved_terminal["distribution"] = workspace_terminal["distribution"]

    return _normalize_session_config(merged)


def load_session_config() -> Dict[str, Any]:
    """Load launcher settings plus last-used saved-session metadata."""
    state = _load_saved_sessions_payload()
    last_entry = _find_saved_session_entry(state["sessions"], state["last_session"])
    config = last_entry["config"] if last_entry else _default_session_config()

    config["last_session"] = state["last_session"]
    config["saved_session"] = _saved_session_meta(last_entry) if last_entry else None
    return config


def _utc_timestamp() -> str:
    """Return a stable UTC timestamp string for persisted metadata."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _generate_saved_session_id() -> str:
    """Build a short unique identifier for a saved launcher preset."""
    return f"session-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}"


def _normalize_saved_session_entry(entry: Any, encrypt_password: bool = False) -> Optional[Dict[str, Any]]:
    """Validate and normalize one saved session entry."""
    if not isinstance(entry, dict):
        return None

    session_id = str(entry.get("id") or _generate_saved_session_id()).strip()
    if not session_id:
        session_id = _generate_saved_session_id()

    created_at = str(entry.get("created_at") or _utc_timestamp())
    updated_at = str(entry.get("updated_at") or created_at)
    config = _normalize_session_config(entry.get("config"))

    if encrypt_password and config.get("ssh", {}).get("password"):
        config["ssh"]["password"] = _encrypt_password(config["ssh"]["password"])

    name = str(entry.get("name") or session_id).strip() or session_id

    return {
        "id": session_id,
        "name": name,
        "created_at": created_at,
        "updated_at": updated_at,
        "config": config,
    }


def _saved_payload_is_supported(payload: Any) -> bool:
    """Whether a stored blob is a preset store this build can read.

    A dict is the current shape and a bare list is the pre-``last_session``
    one. Anything else is not a preset store, and reading it as "no saved
    sessions" would let the next successful save overwrite the user's real
    presets — so the store quarantines it and falls back to the backup instead.
    """
    return isinstance(payload, (dict, list))


_saved_session_store = SavedSessionStore(
    # Resolved per call: the module global is redirected per test case, and a
    # store that pinned the path at import would keep writing the old file.
    path_resolver=lambda: SAVED_SESSIONS_PATH,
    is_supported=_saved_payload_is_supported,
)


def _normalize_stored_payload(raw: Any) -> Dict[str, Any]:
    """Normalize one raw stored blob into ``{sessions, last_session}``.

    Passwords come back decrypted, so this is the in-memory representation the
    rest of the module (and the API) works with; :func:`_build_saved_sessions_commit`
    is its exact inverse on the way back to disk.
    """
    if raw is None:
        return {"sessions": [], "last_session": ""}

    has_last_session_field = False
    if isinstance(raw, dict):
        raw_entries = raw.get("sessions", [])
        has_last_session_field = "last_session" in raw
        last_session = str(raw.get("last_session") or "").strip()
    else:
        raw_entries = raw
        last_session = ""

    normalized_entries = []
    seen_ids = set()
    for entry in raw_entries if isinstance(raw_entries, list) else []:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=False)
        if normalized is not None:
            if normalized.get("config", {}).get("ssh", {}).get("password"):
                normalized["config"]["ssh"]["password"] = _decrypt_password(
                    normalized["config"]["ssh"]["password"]
                )
            if normalized["id"] in seen_ids:
                continue
            seen_ids.add(normalized["id"])
            normalized_entries.append(normalized)

    normalized_entries.sort(key=lambda item: item["updated_at"], reverse=True)
    valid_last_session_ids = set(seen_ids)
    valid_last_session_ids.add(DEFAULT_SAVED_SESSION_ID)
    if last_session and last_session not in valid_last_session_ids:
        last_session = ""
    if not has_last_session_field and not last_session and normalized_entries:
        last_session = normalized_entries[0]["id"]
    return {"sessions": normalized_entries, "last_session": last_session}


def _load_saved_sessions_payload() -> Dict[str, Any]:
    """Load named saved launcher presets and last-used metadata from disk."""
    return _normalize_stored_payload(_saved_session_store.read())


def load_saved_sessions() -> List[Dict[str, Any]]:
    """Load the saved launcher presets list from disk."""
    return _load_saved_sessions_payload()["sessions"]


def _build_saved_sessions_commit(
    entries: List[Dict[str, Any]],
    last_session: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(payload to store, in-memory result)`` for one commit.

    Pure: it normalizes, encrypts, and decides the last-used id, but performs no
    I/O. :meth:`SavedSessionStore.transaction` does the writing, which is what
    lets an upsert or a delete read and rewrite the file under one lock hold.
    A ``None`` payload means "no presets left", and an empty preset store is
    represented by the absence of the file rather than by an empty list.
    """
    normalized_entries = []
    seen_ids = set()
    for entry in entries:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=False)
        if normalized is not None:
            if normalized["id"] in seen_ids:
                continue
            seen_ids.add(normalized["id"])
            normalized_entries.append(normalized)

    normalized_entries.sort(key=lambda item: item["updated_at"], reverse=True)
    if not normalized_entries:
        return None, {"sessions": [], "last_session": ""}

    valid_ids = set(seen_ids)
    valid_ids.add(DEFAULT_SAVED_SESSION_ID)
    if last_session is None:
        last_session_value = normalized_entries[0]["id"]
    else:
        last_session_value = str(last_session).strip()
        if last_session_value and last_session_value not in valid_ids:
            last_session_value = ""

    encrypted_entries = []
    for entry in normalized_entries:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=True)
        if normalized is not None:
            encrypted_entries.append(normalized)

    payload = {"last_session": last_session_value, "sessions": encrypted_entries}
    return payload, {"sessions": normalized_entries, "last_session": last_session_value}


def _save_saved_sessions_payload(
    entries: List[Dict[str, Any]],
    last_session: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist named saved launcher presets plus last-used metadata to disk.

    A whole-store replacement: the caller already decided the complete list.
    Prefer a store transaction for read-modify-write changes, so a concurrent
    writer's unrelated preset cannot be read here and dropped there.
    """
    return _saved_session_store.transaction(
        lambda _stored: _build_saved_sessions_commit(entries, last_session)
    )


def save_saved_sessions(entries: List[Dict[str, Any]], last_session: Optional[str] = None):
    """Persist named saved launcher presets to disk."""
    return _save_saved_sessions_payload(entries, last_session=last_session)


def _find_saved_session_entry(entries: List[Dict[str, Any]], session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return one saved session entry by id."""
    target_id = str(session_id or "").strip()
    if not target_id:
        return None
    if target_id == DEFAULT_SAVED_SESSION_ID:
        return _default_saved_session_entry()

    for entry in entries:
        if entry["id"] == target_id:
            return entry
    return None


def _saved_session_meta(entry: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Return minimal metadata for the active saved session."""
    if not entry:
        return None
    return {"id": entry["id"], "name": entry["name"]}


def _saved_session_response(entry: Dict[str, Any], include_config: bool = False) -> Dict[str, Any]:
    """Return a client-friendly saved session payload."""
    config = entry["config"]
    response = {
        "id": entry["id"],
        "name": entry["name"],
        "created_at": entry["created_at"],
        "updated_at": entry["updated_at"],
        "connection_mode": config["connection_mode"],
        "terminal_count": config["terminal_count"],
        "layout": config["layout"],
        "is_default": entry["id"] == DEFAULT_SAVED_SESSION_ID,
    }
    if include_config:
        response["config"] = config
    return response


def _normalize_launch_session_id(value: Any) -> str:
    """Normalize the optional saved-session identifier attached to a launch."""
    return str(value or "").strip()


# Characters a launched-group key carries literally. Everything else is
# escaped as `_<hex>_`, which makes the encoding reversible and therefore
# collision-free. `_` is not in the set precisely so it can be the escape
# marker without ever being ambiguous.
_GROUP_ID_LITERAL_CHAR = re.compile(r"[A-Za-z0-9.-]")


def _encode_group_id_component(value: str) -> str:
    """Escape one string into the launched-group id alphabet, injectively."""
    return "".join(
        char if _GROUP_ID_LITERAL_CHAR.fullmatch(char) else f"_{ord(char):x}_"
        for char in value
    )


def _build_launch_group_id(saved_session_id: Any) -> str:
    """Return the stable launched-group key for one saved-session identifier.

    The mapping is injective (MW-11). It used to replace every run of
    non-`[A-Za-z0-9._-]` characters with a single `-`, so two distinct preset
    ids such as ``a/b`` and ``a-b`` produced the *same* live group id: the
    second launch replaced the first preset's group in place, and across
    workspaces it tore down the other workspace's sessions before
    ``create_group`` noticed the group belonged elsewhere.

    Ids made only of ``[A-Za-z0-9.-]`` — every id ``_generate_saved_session_id``
    mints — encode to themselves, so live groups and saved workspace snapshots
    written by earlier builds keep the ids they already have.
    """
    normalized = _normalize_launch_session_id(saved_session_id)
    if not normalized:
        return ""

    return f"saved-session-{_encode_group_id_component(normalized)}"


def upsert_saved_session(
    config: Dict[str, Any],
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    set_last_session: bool = True,
) -> Dict[str, Any]:
    """Create or update one named saved session preset.

    The read and the write are one store transaction (SGP-05): a second thread
    or process saving an unrelated preset at the same moment can no longer have
    its entry read here and dropped by this write.
    """
    normalized_config = _normalize_session_config(config)
    if str(session_id or "").strip() == DEFAULT_SAVED_SESSION_ID:
        session_id = None
    normalized_name = str(name or session_id or "").strip()
    now = _utc_timestamp()

    def mutate(stored: Any):
        state = _normalize_stored_payload(stored)
        saved_sessions = state["sessions"]

        if session_id:
            for entry in saved_sessions:
                if entry["id"] == session_id:
                    entry["name"] = normalized_name or entry["name"]
                    entry["updated_at"] = now
                    entry["config"] = normalized_config
                    payload, _result = _build_saved_sessions_commit(
                        saved_sessions,
                        last_session=(
                            entry["id"] if set_last_session else state["last_session"]
                        ),
                    )
                    return payload, entry

        entry_id = session_id or _generate_saved_session_id()
        entry = {
            "id": entry_id,
            "name": normalized_name or entry_id,
            "created_at": now,
            "updated_at": now,
            "config": normalized_config,
        }
        saved_sessions.append(entry)
        payload, _result = _build_saved_sessions_commit(
            saved_sessions,
            last_session=entry_id if set_last_session else state["last_session"],
        )
        return payload, entry

    return _saved_session_store.transaction(mutate)


def set_last_saved_session(session_id: Optional[str]):
    """Persist the last-used saved session id when it still exists."""
    def mutate(stored: Any):
        state = _normalize_stored_payload(stored)
        target_id = str(session_id or "").strip()
        if not state["sessions"] and target_id != DEFAULT_SAVED_SESSION_ID:
            # Nothing to point at, and nothing to rewrite: selecting the
            # built-in default on an empty store must not create a file.
            return UNCHANGED, state
        return _build_saved_sessions_commit(state["sessions"], last_session=target_id)

    return _saved_session_store.transaction(mutate)


def delete_saved_sessions(session_ids: List[str]) -> Dict[str, Any]:
    """Delete one or more saved presets."""
    target_ids = {str(session_id).strip() for session_id in session_ids if str(session_id).strip()}

    def mutate(stored: Any):
        state = _normalize_stored_payload(stored)
        remaining_entries = [
            entry for entry in state["sessions"] if entry["id"] not in target_ids
        ]

        if not remaining_entries:
            return _build_saved_sessions_commit([], last_session="")

        next_last_session = state["last_session"]
        if next_last_session in target_ids or not _find_saved_session_entry(
            remaining_entries, next_last_session
        ):
            next_last_session = remaining_entries[0]["id"]

        return _build_saved_sessions_commit(
            remaining_entries, last_session=next_last_session
        )

    return _saved_session_store.transaction(mutate)


def build_unique_session_name(base_name: str, taken_names: Iterable[Any]) -> str:
    """Return ``base_name``, or ``base_name (n)`` when that name is already used.

    The launcher names a scratch session after its connection target, so the
    same SSH host or local repository launched twice would otherwise produce two
    identically named session tabs. ``taken_names`` is every name a new scratch
    session must not collide with: the live session groups *and* the saved
    presets, so promoting one scratch session to a saved one ("10.0.0.5 (1)")
    makes the next scratch launch skip past that number rather than reuse it.

    Comparison is case-insensitive because the names are hostnames and folder
    names, where case is not a meaningful distinction to read a tab strip by.
    """
    base = str(base_name or "").strip()
    if not base:
        return base

    taken = {
        str(name or "").strip().casefold()
        for name in taken_names
        if str(name or "").strip()
    }
    if base.casefold() not in taken:
        return base

    for suffix in range(1, SCRATCH_NAME_MAX_SUFFIX + 1):
        candidate = f"{base} ({suffix})"
        if candidate.casefold() not in taken:
            return candidate

    # Pathological: a thousand live-or-saved sessions on one target. Stay unique
    # rather than silently handing back a colliding name.
    return f"{base} ({uuid.uuid4().hex[:6]})"


def build_connection_target_proposals() -> Dict[str, List[Dict[str, Any]]]:
    """Return the distinct SSH and local-repo targets the saved presets use.

    Feeds the launcher's per-mode target dropdown, so a scratch session can be
    started against an address the user has already saved without retyping the
    host, port, or repository path.

    Deliberately secret-free: a saved SSH password stays behind
    ``GET /api/saved-sessions/<id>`` and is fetched only for the one target the
    user actually picks, so listing the targets never ships every stored
    password at once. ``has_password`` says whether that follow-up fetch is
    worth making.

    A preset contributes whichever blocks it has filled in rather than only the
    one matching its ``connection_mode``, so a preset that carries both an SSH
    host and a local repository path is offered under both modes.
    """
    ssh_targets: List[Dict[str, Any]] = []
    wsl_targets: List[Dict[str, Any]] = []
    seen_ssh = set()
    seen_wsl = set()

    # `load_saved_sessions` is already sorted most-recently-updated first, so
    # the freshest target wins both the de-duplication and the list order.
    for entry in load_saved_sessions():
        config = entry.get("config") or {}
        ssh_config = config.get("ssh") or {}
        wsl_config = config.get("wsl") or {}

        host = str(ssh_config.get("host") or "").strip()
        if host and len(ssh_targets) < CONNECTION_TARGET_LIMIT:
            username = str(ssh_config.get("username") or "").strip() or "ubuntu"
            try:
                port = int(ssh_config.get("port") or 22)
            except (TypeError, ValueError):
                port = 22
            port = max(1, min(65535, port))
            default_dir = str(ssh_config.get("default_dir") or "").strip()
            signature = (host.casefold(), username, port, default_dir)
            if signature not in seen_ssh:
                seen_ssh.add(signature)
                ssh_targets.append(
                    {
                        "session_id": entry["id"],
                        "session_name": entry["name"],
                        "host": host,
                        "username": username,
                        "port": port,
                        "default_dir": default_dir,
                        "has_password": bool(ssh_config.get("password")),
                    }
                )

        repo_dir = str(wsl_config.get("default_dir") or "").strip()
        if repo_dir and len(wsl_targets) < CONNECTION_TARGET_LIMIT:
            distribution = str(wsl_config.get("distribution") or "").strip()
            username = str(wsl_config.get("username") or "").strip()
            signature = (repo_dir.replace("\\", "/").casefold(), distribution, username)
            if signature not in seen_wsl:
                seen_wsl.add(signature)
                wsl_targets.append(
                    {
                        "session_id": entry["id"],
                        "session_name": entry["name"],
                        "default_dir": repo_dir,
                        "distribution": distribution,
                        "username": username,
                    }
                )

    return {"ssh": ssh_targets, "wsl": wsl_targets}
