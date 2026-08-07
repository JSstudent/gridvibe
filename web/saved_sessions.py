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
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from web.config import runtime_config
from web.paths import BASE_DIR
from web.secrets import _decrypt_password, _encrypt_password

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

# Explorer tabbed viewer persistence bounds (ISSUE-2026-015).
EXPLORER_MAX_OPEN_TABS = 12
EXPLORER_MAX_TAB_PATH_LENGTH = 4096

# Per-tab view persistence (item 2.f): mode + scroll fraction + content
# identity + editor zoom per open tab, plus the global Markdown appearance
# (ISSUE-2026-033). Allowlists and bounds mirror the client
# (`EXPLORER_MD_PRESETS` / `EXPLORER_MD_FONTS`, the file-view panel names,
# and `EXPLORER_EDITOR_FONT_MIN/MAX` in web/static/js/terminals.js). The
# reserved `__preview__` key carries the permanent Preview tab's own view state,
# including zoom, path, and Markdown folds.
EXPLORER_TAB_VIEW_MODES = ("source", "preview", "diff")
EXPLORER_DIFF_MODES = ("worktree", "staged")
EXPLORER_MAX_TAB_VIEW_IDENTITY_LENGTH = 64
EXPLORER_MAX_DIFF_COMMIT_LENGTH = 64
EXPLORER_MAX_MARKDOWN_FOLDS = 256
EXPLORER_MAX_MARKDOWN_LINE = 1_000_000
EXPLORER_PREVIEW_TAB_KEY = "__preview__"
EXPLORER_EDITOR_FONT_MIN = 10
EXPLORER_EDITOR_FONT_MAX = 24
EXPLORER_MD_PRESETS = ("default", "paper", "contrast", "vscode")
EXPLORER_MD_FONTS = (
    "system",
    "serif",
    "cascadia-code",
    "jetbrains-mono",
    "courier-new",
)
EXPLORER_SOURCE_FONTS = (
    "default",
    "cascadia-code",
    "jetbrains-mono",
    "courier-new",
)
# Retired options mapped onto their nearest survivor so an older saved session
# keeps its intent: "consolas" was dropped when it rendered identically to
# JetBrains Mono, whose stack fell back to it before the faces were vendored.
EXPLORER_FONT_ALIASES = {"consolas": "jetbrains-mono"}


def _normalize_explorer_tab_path(value: Any) -> str:
    """Normalize one persisted explorer tab path (root-relative, no traversal).

    Returns "" for absolute paths, drive letters, ``..`` traversal, or anything
    over the length cap, so an unsafe or out-of-root entry is dropped rather
    than restored.
    """
    text = str(value or "").replace("\\", "/").strip()
    if not text or len(text) > EXPLORER_MAX_TAB_PATH_LENGTH:
        return ""
    segments: List[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == ".." or ":" in segment:
            return ""
        segments.append(segment)
    return "/".join(segments)


def _normalize_explorer_open_tabs(value: Any) -> List[str]:
    """Bound and de-duplicate the persisted list of open explorer tab paths."""
    if not isinstance(value, list):
        return []
    result: List[str] = []
    seen = set()
    for item in value:
        path = _normalize_explorer_tab_path(item)
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
        if len(result) >= EXPLORER_MAX_OPEN_TABS:
            break
    return result


def _normalize_explorer_active_tab(value: Any, open_tabs: List[str]) -> str:
    """Keep the active tab only when it points at one of the open tabs."""
    path = _normalize_explorer_tab_path(value)
    return path if path in open_tabs else ""


def _normalize_explorer_md_choice(value: Any, allowed: tuple, aliases: dict = None) -> str:
    """Return an allowlisted viewer appearance value, or "" for unset."""
    text = str(value or "").strip()
    text = (aliases or {}).get(text, text)
    return text if text in allowed else ""


def _normalize_explorer_theme(value: Any) -> str:
    """Return the saved per-pane explorer theme; anything but "light" is "dark"."""
    return "light" if str(value or "").strip() == "light" else "dark"


def _normalize_explorer_tab_font_size(value: Any) -> int:
    """Clamp a persisted per-tab editor font size to the client bounds; 0 = unset."""
    try:
        font_size = int(value)
    except (TypeError, ValueError):
        return 0
    if font_size <= 0:
        return 0
    return max(EXPLORER_EDITOR_FONT_MIN, min(EXPLORER_EDITOR_FONT_MAX, font_size))


def _normalize_explorer_line_wrap(raw_view: Dict[str, Any]) -> Dict[str, bool]:
    """Return the per-tab source/preview/diff line-wrap opt-outs.

    Wrapping is on by default, so only an explicit off flag persists — an absent
    key restores wrapped, which is also what tabs saved before wrapping existed
    (and every never-touched tab) get.
    """
    return {
        key: False
        for key in ("wrap_source", "wrap_preview", "wrap_diff")
        if key in raw_view and not raw_view[key]
    }


def _normalize_explorer_markdown_folds(value: Any) -> List[int]:
    """Return bounded, unique Markdown heading line numbers in document order."""
    if not isinstance(value, list):
        return []
    folds: List[int] = []
    seen = set()
    for raw_line in value:
        if isinstance(raw_line, bool):
            continue
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            continue
        if line < 1 or line > EXPLORER_MAX_MARKDOWN_LINE or line in seen:
            continue
        seen.add(line)
        folds.append(line)
        if len(folds) >= EXPLORER_MAX_MARKDOWN_FOLDS:
            break
    return sorted(folds)


def _normalize_explorer_view_identity(value: Any) -> str:
    """Validate one short, opaque content identity token."""
    identity = str(value or "")
    return identity if len(identity) <= EXPLORER_MAX_TAB_VIEW_IDENTITY_LENGTH else ""


def _normalize_explorer_diff_target(raw_view: Dict[str, Any], mode: str) -> Dict[str, str]:
    """Return the bounded Git selector for a persisted Diff view."""
    if mode != "diff":
        return {}
    commit = str(raw_view.get("diff_commit") or "").strip()
    if commit and len(commit) <= EXPLORER_MAX_DIFF_COMMIT_LENGTH and re.fullmatch(
        r"[0-9a-fA-F]{7,64}", commit
    ):
        return {"diff_commit": commit}
    diff_mode = str(raw_view.get("diff_mode") or "").strip()
    return {"diff_mode": diff_mode} if diff_mode in EXPLORER_DIFF_MODES else {}


def _normalize_explorer_view_snapshot(raw_view: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize mode, scroll, identity, and optional Git diff selector."""
    mode = str(raw_view.get("mode") or "")
    if mode not in EXPLORER_TAB_VIEW_MODES:
        return {}
    try:
        scroll = float(raw_view.get("scroll", 0.0))
    except (TypeError, ValueError):
        scroll = 0.0
    if scroll != scroll:  # NaN guard
        scroll = 0.0
    record: Dict[str, Any] = {
        "mode": mode,
        "scroll": max(0.0, min(1.0, scroll)),
        "identity": _normalize_explorer_view_identity(raw_view.get("identity")),
    }
    record.update(_normalize_explorer_diff_target(raw_view, mode))
    return record


def _normalize_explorer_tab_views(value: Any, open_tabs: List[str]) -> Dict[str, Any]:
    """Validate the per-tab view map: mode + scroll + identity + zoom + wrapping.

    Only entries for persisted open tabs survive (plus the reserved Preview
    key, which keeps zoom, line wrapping, Markdown folds, and the tab's own
    separated path — shown file and/or browsed directory); the mode must be a
    known file view, the scroll is clamped to a [0, 1] fraction (OD-4), content
    identities are short opaque tokens, line-wrap flags are booleans, and the
    font size and fold lines are bounded — anything else is dropped rather than
    restored.
    """
    if not isinstance(value, dict):
        return {}
    views: Dict[str, Any] = {}
    for raw_path, raw_view in value.items():
        if not isinstance(raw_view, dict):
            continue
        if str(raw_path) == EXPLORER_PREVIEW_TAB_KEY:
            record = _normalize_explorer_view_snapshot(raw_view)
            font_size = _normalize_explorer_tab_font_size(raw_view.get("font_size"))
            if font_size:
                record["font_size"] = font_size
            record.update(_normalize_explorer_line_wrap(raw_view))
            preview_path = _normalize_explorer_tab_path(raw_view.get("path"))
            if preview_path:
                record["path"] = preview_path
            preview_dir = _normalize_explorer_tab_path(raw_view.get("dir"))
            if preview_dir:
                record["dir"] = preview_dir
            folds = _normalize_explorer_markdown_folds(raw_view.get("folds"))
            fold_identity = _normalize_explorer_view_identity(raw_view.get("fold_identity"))
            if folds and fold_identity:
                record["folds"] = folds
                record["fold_identity"] = fold_identity
            if record and EXPLORER_PREVIEW_TAB_KEY not in views:
                views[EXPLORER_PREVIEW_TAB_KEY] = record
            continue
        path = _normalize_explorer_tab_path(raw_path)
        if not path or path not in open_tabs or path in views:
            continue
        record = _normalize_explorer_view_snapshot(raw_view)
        font_size = _normalize_explorer_tab_font_size(raw_view.get("font_size"))
        if font_size:
            record["font_size"] = font_size
        record.update(_normalize_explorer_line_wrap(raw_view))
        folds = _normalize_explorer_markdown_folds(raw_view.get("folds"))
        fold_identity = _normalize_explorer_view_identity(raw_view.get("fold_identity"))
        if folds and fold_identity:
            record["folds"] = folds
            record["fold_identity"] = fold_identity
        if record:
            views[path] = record
    return views


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
            "explorer_search_open": False,
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


def _normalize_browser_url(value: Any) -> str:
    """Return a browser-pane URL with only HTTP(S) schemes allowed."""
    raw_value = str(value or DEFAULT_BROWSER_URL).strip()
    if not raw_value:
        raise ValueError("Browser panes require an HTTP or HTTPS URL")
    if len(raw_value) > BROWSER_MAX_URL_LENGTH:
        raise ValueError("Browser pane URL is too long")

    candidate = raw_value
    if "://" not in candidate:
        candidate = f"http://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Browser panes only support http:// and https:// URLs")

    return candidate


DEFAULT_BROWSER_URL = "http://127.0.0.1:3000"

# Browser-pane tab persistence bounds. Mirrored by BROWSER_MAX_TABS in
# web/static/js/browser-pane.js — a pane is a preview surface, not a full
# browser, so the strip stays short enough to read at pane width.
BROWSER_MAX_TABS = 8
BROWSER_MAX_URL_LENGTH = 2048


def _normalize_browser_tabs(value: Any, active_url: str = "") -> List[str]:
    """Bound one browser pane's persisted tab URLs, dropping unusable entries.

    Unlike explorer tabs, duplicates are kept: the same URL open twice is a
    legitimate side-by-side comparison. ``active_url`` seeds a single-tab list
    for panes saved before tabs existed, so an upgrade never loses the URL.
    """
    tabs: List[str] = []
    for item in value if isinstance(value, list) else []:
        # `_normalize_browser_url` treats an empty value as "use the default",
        # which is right for the single-URL entry point but wrong here: a blank
        # tab is a broken entry and must be dropped, not become the default.
        if not str(item or "").strip():
            continue
        try:
            tabs.append(_normalize_browser_url(item))
        except ValueError:
            continue
        if len(tabs) >= BROWSER_MAX_TABS:
            break

    if not tabs and active_url:
        try:
            tabs.append(_normalize_browser_url(active_url))
        except ValueError:
            return []
    return tabs


def _normalize_browser_active_tab(value: Any, tabs: List[str]) -> int:
    """Clamp the active tab index into the persisted tab list ("" -> 0)."""
    if not tabs:
        return 0
    try:
        index = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(len(tabs) - 1, index))


def _normalize_workspace_layout(data: Any, terminal_count: int) -> Optional[Dict[str, Any]]:
    """Normalize optional runtime workspace geometry stored with a saved preset."""
    if not isinstance(data, dict):
        return None

    raw_rects = data.get("split_slot_rects")
    if not isinstance(raw_rects, list) or len(raw_rects) != terminal_count:
        return None

    rects = []
    # The frontend lays base cells out on an 8-unit grid (SPLIT_CELL_UNIT), so the
    # densest base (4 columns) spans 4 * 8 = 32 grid lines before any split; keep a
    # comfortable margin above that and scale with a larger configured max_sessions.
    max_grid_line = max(64, runtime_config.max_sessions * 8)
    for index, raw_rect in enumerate(raw_rects):
        if not isinstance(raw_rect, dict):
            return None
        try:
            x = int(raw_rect.get("x", 1))
            y = int(raw_rect.get("y", 1))
            w = int(raw_rect.get("w", 1))
            h = int(raw_rect.get("h", 1))
            origin_slot = int(raw_rect.get("originSlot", index))
        except (TypeError, ValueError):
            return None

        if x < 1 or y < 1 or w < 1 or h < 1:
            return None
        if x + w - 1 > max_grid_line or y + h - 1 > max_grid_line:
            return None

        rects.append(
            {
                "originSlot": max(0, min(runtime_config.max_sessions - 1, origin_slot)),
                "x": x,
                "y": y,
                "w": w,
                "h": h,
            }
        )

    def normalize_weights(values: Any, target_length: int) -> List[float]:
        if not isinstance(values, list):
            return [1.0 for _ in range(target_length)]
        normalized = []
        for index in range(target_length):
            try:
                value = float(values[index])
            except (IndexError, TypeError, ValueError):
                value = 1.0
            normalized.append(max(0.01, min(value, 100.0)))
        return normalized

    column_count = max(rect["x"] + rect["w"] - 1 for rect in rects)
    row_count = max(rect["y"] + rect["h"] - 1 for rect in rects)
    try:
        original_count = int(data.get("original_split_slot_count", terminal_count))
    except (TypeError, ValueError):
        original_count = terminal_count

    return {
        "class_name": "layout-split-local",
        "split_slot_rects": rects,
        "split_column_weights": normalize_weights(data.get("split_column_weights"), column_count),
        "split_row_weights": normalize_weights(data.get("split_row_weights"), row_count),
        "original_split_slot_count": max(1, min(runtime_config.max_sessions, original_count)),
    }


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


def _normalize_terminal_entries(entries: Any, connection_mode: str = "ssh") -> List[Dict[str, Any]]:
    """Ensure the saved terminal list is bounded and complete."""
    normalized = []
    entries = entries if isinstance(entries, list) else []

    for index in range(runtime_config.max_sessions):
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
                "explorer_search_open": bool(entry.get("explorer_search_open")),
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
            if field_name in saved_terminals[index]
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

    terminal_count = max(1, min(runtime_config.max_sessions, terminal_count))
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
        "terminals": _normalize_terminal_entries(data.get("terminals"), connection_mode),
        "workspace_layout": _normalize_workspace_layout(data.get("workspace_layout"), terminal_count),
    }


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
        saved_terminal["explorer_search_open"] = (
            startup_mode == "explorer" and workspace_terminal.get("explorer_search_open", False)
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


def _load_saved_sessions_payload() -> Dict[str, Any]:
    """Load named saved launcher presets and last-used metadata from disk."""
    if not os.path.exists(SAVED_SESSIONS_PATH):
        return {"sessions": [], "last_session": ""}

    try:
        with open(SAVED_SESSIONS_PATH, "r", encoding="utf-8") as file_handle:
            raw = json.load(file_handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to load {SAVED_SESSIONS_PATH}: {exc}")
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


def load_saved_sessions() -> List[Dict[str, Any]]:
    """Load the saved launcher presets list from disk."""
    return _load_saved_sessions_payload()["sessions"]


def _save_saved_sessions_payload(
    entries: List[Dict[str, Any]],
    last_session: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist named saved launcher presets plus last-used metadata to disk."""
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
        if os.path.exists(SAVED_SESSIONS_PATH):
            try:
                os.remove(SAVED_SESSIONS_PATH)
            except OSError as exc:
                logger.warning(f"Failed to remove {SAVED_SESSIONS_PATH}: {exc}")
        return {"sessions": [], "last_session": ""}

    valid_ids = {entry["id"] for entry in normalized_entries}
    valid_ids.add(DEFAULT_SAVED_SESSION_ID)
    if last_session is None:
        last_session_value = normalized_entries[0]["id"] if normalized_entries else ""
    else:
        last_session_value = str(last_session).strip()
        if last_session_value and last_session_value not in valid_ids:
            last_session_value = ""

    encrypted_entries = []
    for entry in normalized_entries:
        normalized = _normalize_saved_session_entry(entry, encrypt_password=True)
        if normalized is not None:
            encrypted_entries.append(normalized)

    with open(SAVED_SESSIONS_PATH, "w", encoding="utf-8") as file_handle:
        json.dump(
            {
                "last_session": last_session_value,
                "sessions": encrypted_entries,
            },
            file_handle,
            indent=2,
        )
    return {"sessions": normalized_entries, "last_session": last_session_value}


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
    """Create or update one named saved session preset."""
    normalized_config = _normalize_session_config(config)
    if str(session_id or "").strip() == DEFAULT_SAVED_SESSION_ID:
        session_id = None
    normalized_name = str(name or session_id or "").strip()
    state = _load_saved_sessions_payload()
    saved_sessions = state["sessions"]
    now = _utc_timestamp()

    if session_id:
        for entry in saved_sessions:
            if entry["id"] == session_id:
                entry["name"] = normalized_name or entry["name"]
                entry["updated_at"] = now
                entry["config"] = normalized_config
                save_saved_sessions(
                    saved_sessions,
                    last_session=entry["id"] if set_last_session else state["last_session"],
                )
                return entry

    entry_id = session_id or _generate_saved_session_id()
    entry = {
        "id": entry_id,
        "name": normalized_name or entry_id,
        "created_at": now,
        "updated_at": now,
        "config": normalized_config,
    }
    saved_sessions.append(entry)
    save_saved_sessions(
        saved_sessions,
        last_session=entry_id if set_last_session else state["last_session"],
    )
    return entry


def set_last_saved_session(session_id: Optional[str]):
    """Persist the last-used saved session id when it still exists."""
    state = _load_saved_sessions_payload()
    target_id = str(session_id or "").strip()
    if not state["sessions"] and target_id != DEFAULT_SAVED_SESSION_ID:
        return state
    return save_saved_sessions(state["sessions"], last_session=target_id)


def delete_saved_sessions(session_ids: List[str]) -> Dict[str, Any]:
    """Delete one or more saved presets."""
    state = _load_saved_sessions_payload()
    target_ids = {str(session_id).strip() for session_id in session_ids if str(session_id).strip()}
    remaining_entries = [entry for entry in state["sessions"] if entry["id"] not in target_ids]

    if not remaining_entries:
        return save_saved_sessions([], last_session="")

    next_last_session = state["last_session"]
    if next_last_session in target_ids or not _find_saved_session_entry(remaining_entries, next_last_session):
        next_last_session = remaining_entries[0]["id"]

    return save_saved_sessions(remaining_entries, last_session=next_last_session)


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
