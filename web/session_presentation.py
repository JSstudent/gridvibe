"""Canonical normalization and transactions for live session presentation.

This module is deliberately import-cycle safe: it knows the presentation
schema and operates on a manager passed by the caller, but imports neither the
Flask application nor :mod:`sessions.manager`.  Saved presets, live updates,
and later runtime-state validation can therefore share the same field rules.
"""

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from web.config import runtime_config


class PresentationValidationError(ValueError):
    """Raised when a presentation payload is not a bounded snapshot."""


# Explorer tabbed-viewer bounds. Keep these definitions here: this is the one
# import-cycle-safe owner of presentation-field normalization.
EXPLORER_MAX_OPEN_TABS = 12
EXPLORER_MAX_TAB_PATH_LENGTH = 4096
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
EXPLORER_FONT_ALIASES = {"consolas": "jetbrains-mono"}

DEFAULT_BROWSER_URL = "http://127.0.0.1:3000"
BROWSER_MAX_TABS = 8
BROWSER_MAX_URL_LENGTH = 2048

PANE_PRESENTATION_FIELDS = frozenset(
    {
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
)

_EXPLORER_FIELDS = frozenset(
    field for field in PANE_PRESENTATION_FIELDS if not field.startswith("browser_")
)
_BROWSER_FIELDS = frozenset(
    field for field in PANE_PRESENTATION_FIELDS if field.startswith("browser_")
)
_EXPLORER_BOOL_FIELDS = frozenset(
    {"explorer_tree_open", "explorer_git_open", "explorer_search_open"}
)
_EXPLORER_STRING_FIELDS = frozenset(
    {
        "explorer_active_tab",
        "explorer_md_preset",
        "explorer_md_font",
        "explorer_source_font",
        "explorer_theme",
    }
)
_VIEW_FIELDS = frozenset(
    {
        "mode",
        "scroll",
        "identity",
        "diff_commit",
        "diff_mode",
        "font_size",
        "wrap_source",
        "wrap_preview",
        "wrap_diff",
        "path",
        "dir",
        "folds",
        "fold_identity",
    }
)


def normalize_topbar_visible(value: Any) -> Optional[bool]:
    """Normalize optional workspace top-bar visibility without coercion."""
    return value if isinstance(value, bool) else None


def _normalize_explorer_tab_path(value: Any) -> str:
    """Normalize one root-relative explorer tab path."""
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
    """Bound and de-duplicate explorer tab paths."""
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
    path = _normalize_explorer_tab_path(value)
    return path if path in open_tabs else ""


def _normalize_explorer_md_choice(
    value: Any,
    allowed: tuple,
    aliases: Optional[dict] = None,
) -> str:
    text = str(value or "").strip()
    text = (aliases or {}).get(text, text)
    return text if text in allowed else ""


def _normalize_explorer_theme(value: Any) -> str:
    return "light" if str(value or "").strip() == "light" else "dark"


def _normalize_explorer_tab_font_size(value: Any) -> int:
    try:
        font_size = int(value)
    except (TypeError, ValueError):
        return 0
    if font_size <= 0:
        return 0
    return max(EXPLORER_EDITOR_FONT_MIN, min(EXPLORER_EDITOR_FONT_MAX, font_size))


def _normalize_explorer_line_wrap(raw_view: Dict[str, Any]) -> Dict[str, bool]:
    return {
        key: False
        for key in ("wrap_source", "wrap_preview", "wrap_diff")
        if key in raw_view and not raw_view[key]
    }


def _normalize_explorer_markdown_folds(value: Any) -> List[int]:
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
    identity = str(value or "")
    return identity if len(identity) <= EXPLORER_MAX_TAB_VIEW_IDENTITY_LENGTH else ""


def _normalize_explorer_diff_target(
    raw_view: Dict[str, Any],
    mode: str,
) -> Dict[str, str]:
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
    mode = str(raw_view.get("mode") or "")
    if mode not in EXPLORER_TAB_VIEW_MODES:
        return {}
    try:
        scroll = float(raw_view.get("scroll", 0.0))
    except (TypeError, ValueError):
        scroll = 0.0
    if not math.isfinite(scroll):
        scroll = 0.0
    record: Dict[str, Any] = {
        "mode": mode,
        "scroll": max(0.0, min(1.0, scroll)),
        "identity": _normalize_explorer_view_identity(raw_view.get("identity")),
    }
    record.update(_normalize_explorer_diff_target(raw_view, mode))
    return record


def _normalize_explorer_tab_views(
    value: Any,
    open_tabs: List[str],
) -> Dict[str, Any]:
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
            raw_preview_dir = raw_view.get("dir")
            preview_dir = _normalize_explorer_tab_path(raw_preview_dir)
            if preview_dir or ("dir" in raw_view and raw_preview_dir == ""):
                record["dir"] = preview_dir
            folds = _normalize_explorer_markdown_folds(raw_view.get("folds"))
            fold_identity = _normalize_explorer_view_identity(
                raw_view.get("fold_identity")
            )
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


def _normalize_browser_url(value: Any) -> str:
    raw_value = str(value or DEFAULT_BROWSER_URL).strip()
    if not raw_value:
        raise ValueError("Browser panes require an HTTP or HTTPS URL")
    if len(raw_value) > BROWSER_MAX_URL_LENGTH:
        raise ValueError("Browser pane URL is too long")
    candidate = raw_value if "://" in raw_value else f"http://{raw_value}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Browser panes only support http:// and https:// URLs")
    return candidate


def _normalize_browser_tabs(value: Any, active_url: str = "") -> List[str]:
    tabs: List[str] = []
    for item in value if isinstance(value, list) else []:
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
    if not tabs:
        return 0
    try:
        index = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(len(tabs) - 1, index))


def _normalize_workspace_layout(
    data: Any,
    terminal_count: int,
) -> Optional[Dict[str, Any]]:
    """Normalize current live geometry; storage-cap decoupling is Stage 6."""
    if not isinstance(data, dict):
        return None
    raw_rects = data.get("split_slot_rects")
    if not isinstance(raw_rects, list) or len(raw_rects) != terminal_count:
        return None
    rects = []
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
                "originSlot": max(
                    0, min(runtime_config.max_sessions - 1, origin_slot)
                ),
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
        "split_column_weights": normalize_weights(
            data.get("split_column_weights"), column_count
        ),
        "split_row_weights": normalize_weights(
            data.get("split_row_weights"), row_count
        ),
        "original_split_slot_count": max(
            1, min(runtime_config.max_sessions, original_count)
        ),
    }


def _require_view_types(value: Dict[str, Any]) -> None:
    unknown = set(value) - _VIEW_FIELDS
    if unknown:
        raise PresentationValidationError(
            f"Unknown explorer view field: {sorted(unknown)[0]}"
        )
    string_fields = {
        "mode",
        "identity",
        "diff_commit",
        "diff_mode",
        "path",
        "dir",
        "fold_identity",
    }
    for field_name in string_fields & value.keys():
        if not isinstance(value[field_name], str):
            raise PresentationValidationError(f"'{field_name}' must be a string")
    if "scroll" in value and (
        isinstance(value["scroll"], bool)
        or not isinstance(value["scroll"], (int, float))
    ):
        raise PresentationValidationError("'scroll' must be a number")
    if "font_size" in value and (
        isinstance(value["font_size"], bool)
        or not isinstance(value["font_size"], int)
    ):
        raise PresentationValidationError("'font_size' must be an integer")
    for field_name in {"wrap_source", "wrap_preview", "wrap_diff"} & value.keys():
        if not isinstance(value[field_name], bool):
            raise PresentationValidationError(f"'{field_name}' must be a boolean")
    if "folds" in value:
        folds = value["folds"]
        if not isinstance(folds, list) or any(
            isinstance(line, bool) or not isinstance(line, int) for line in folds
        ):
            raise PresentationValidationError("'folds' must be an integer list")


def normalize_pane_presentation(data: Any) -> Dict[str, Any]:
    """Return one strict, bounded, presentation-only pane update.

    Wrong types and unknown fields are rejected instead of being fed through
    Python's truthy/list/dict coercions. Values with the right shape are
    bounded by the same field normalizers saved presets use.
    """
    if not isinstance(data, dict):
        raise PresentationValidationError("Each pane must be an object")
    unknown = set(data) - PANE_PRESENTATION_FIELDS
    if unknown:
        raise PresentationValidationError(
            f"Unknown presentation field: {sorted(unknown)[0]}"
        )
    normalized: Dict[str, Any] = {}
    for field_name in _EXPLORER_BOOL_FIELDS & data.keys():
        if not isinstance(data[field_name], bool):
            raise PresentationValidationError(f"'{field_name}' must be a boolean")
        normalized[field_name] = data[field_name]
    for field_name in _EXPLORER_STRING_FIELDS & data.keys():
        if not isinstance(data[field_name], str):
            raise PresentationValidationError(f"'{field_name}' must be a string")

    open_tabs: Optional[List[str]] = None
    if "explorer_open_tabs" in data:
        raw_tabs = data["explorer_open_tabs"]
        if not isinstance(raw_tabs, list) or any(
            not isinstance(path, str) for path in raw_tabs
        ):
            raise PresentationValidationError(
                "'explorer_open_tabs' must be a string list"
            )
        open_tabs = _normalize_explorer_open_tabs(raw_tabs)
        normalized["explorer_open_tabs"] = open_tabs
        normalized["explorer_active_tab"] = _normalize_explorer_active_tab(
            data.get("explorer_active_tab"), open_tabs
        )
    elif "explorer_active_tab" in data or "explorer_tab_views" in data:
        raise PresentationValidationError(
            "Explorer active tab and views require 'explorer_open_tabs'"
        )

    if "explorer_tab_views" in data:
        raw_views = data["explorer_tab_views"]
        if not isinstance(raw_views, dict):
            raise PresentationValidationError("'explorer_tab_views' must be an object")
        for raw_path, raw_view in raw_views.items():
            if not isinstance(raw_path, str) or not isinstance(raw_view, dict):
                raise PresentationValidationError(
                    "Explorer tab views must map strings to objects"
                )
            _require_view_types(raw_view)
        normalized["explorer_tab_views"] = _normalize_explorer_tab_views(
            raw_views, open_tabs or []
        )

    if "explorer_md_preset" in data:
        normalized["explorer_md_preset"] = _normalize_explorer_md_choice(
            data["explorer_md_preset"], EXPLORER_MD_PRESETS
        )
    if "explorer_md_font" in data:
        normalized["explorer_md_font"] = _normalize_explorer_md_choice(
            data["explorer_md_font"], EXPLORER_MD_FONTS, EXPLORER_FONT_ALIASES
        )
    if "explorer_source_font" in data:
        normalized["explorer_source_font"] = _normalize_explorer_md_choice(
            data["explorer_source_font"],
            EXPLORER_SOURCE_FONTS,
            EXPLORER_FONT_ALIASES,
        )
    if "explorer_theme" in data:
        normalized["explorer_theme"] = _normalize_explorer_theme(
            data["explorer_theme"]
        )

    if "browser_tabs" in data:
        raw_browser_tabs = data["browser_tabs"]
        if not isinstance(raw_browser_tabs, list) or any(
            not isinstance(url, str) for url in raw_browser_tabs
        ):
            raise PresentationValidationError("'browser_tabs' must be a string list")
        browser_tabs = _normalize_browser_tabs(raw_browser_tabs)
        if raw_browser_tabs and not browser_tabs:
            raise PresentationValidationError("'browser_tabs' has no valid URL")
        active = data.get("browser_active_tab", 0)
        if isinstance(active, bool) or not isinstance(active, int):
            raise PresentationValidationError(
                "'browser_active_tab' must be an integer"
            )
        normalized["browser_tabs"] = browser_tabs
        normalized["browser_active_tab"] = _normalize_browser_active_tab(
            active, browser_tabs
        )
    elif "browser_active_tab" in data:
        raise PresentationValidationError(
            "'browser_active_tab' requires 'browser_tabs'"
        )
    return normalized


def normalize_group_presentation(data: Any) -> Dict[str, Any]:
    """Validate and normalize one exact live-group presentation transaction."""
    if not isinstance(data, dict):
        raise PresentationValidationError("Presentation payload must be an object")
    allowed = {
        "workspace_id",
        "group_id",
        "expected_revision",
        "pane_order",
        "layout",
        "workspace_layout",
        "panes",
    }
    unknown = set(data) - allowed
    if unknown:
        raise PresentationValidationError(f"Unknown field: {sorted(unknown)[0]}")

    workspace_id = data.get("workspace_id")
    group_id = data.get("group_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise PresentationValidationError("A non-empty 'workspace_id' is required")
    if not isinstance(group_id, str) or not group_id.strip():
        raise PresentationValidationError("A non-empty 'group_id' is required")
    revision = data.get("expected_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PresentationValidationError(
            "'expected_revision' must be a non-negative integer"
        )

    pane_order = data.get("pane_order")
    panes = data.get("panes")
    if not isinstance(pane_order, list) or not pane_order:
        raise PresentationValidationError("A non-empty 'pane_order' list is required")
    if any(not isinstance(item, str) or not item.strip() for item in pane_order):
        raise PresentationValidationError("Pane ids must be non-empty strings")
    if len(set(pane_order)) != len(pane_order):
        raise PresentationValidationError("'pane_order' contains duplicate ids")
    if not isinstance(panes, list) or len(panes) != len(pane_order):
        raise PresentationValidationError("'panes' must contain every ordered pane")

    pane_updates: Dict[str, Dict[str, Any]] = {}
    for pane in panes:
        if not isinstance(pane, dict):
            raise PresentationValidationError("Each pane must be an object")
        pane_id = pane.get("session_id")
        if not isinstance(pane_id, str) or not pane_id.strip():
            raise PresentationValidationError("Each pane needs a 'session_id'")
        if pane_id in pane_updates:
            raise PresentationValidationError("'panes' contains duplicate ids")
        pane_updates[pane_id] = normalize_pane_presentation(
            {key: value for key, value in pane.items() if key != "session_id"}
        )
    if set(pane_updates) != set(pane_order):
        raise PresentationValidationError(
            "'panes' and 'pane_order' must name the same sessions"
        )

    normalized: Dict[str, Any] = {
        "workspace_id": workspace_id.strip(),
        "group_id": group_id.strip(),
        "expected_revision": revision,
        "pane_order": list(pane_order),
        "pane_updates": pane_updates,
    }
    if "layout" in data:
        layout = data["layout"]
        if not isinstance(layout, str) or layout not in {
            "single",
            "vertical",
            "horizontal",
            "split",
            "grid",
        }:
            raise PresentationValidationError("Unknown group layout")
        normalized["layout"] = layout
    if "workspace_layout" in data:
        if data["workspace_layout"] is None:
            normalized["workspace_layout"] = None
        else:
            geometry = _normalize_workspace_layout(data["workspace_layout"], len(panes))
            if geometry is None:
                raise PresentationValidationError("Invalid workspace layout geometry")
            normalized["workspace_layout"] = geometry
    return normalized


def apply_group_presentation(manager: Any, data: Any) -> Tuple[Dict[str, Any], int]:
    """Normalize outside locks, then ask the manager for one atomic CAS update."""
    normalized = normalize_group_presentation(data)
    result = manager.apply_group_presentation(**normalized)
    outcome = result.pop("outcome")
    if outcome == "ok":
        return result, 200
    if outcome == "conflict":
        return result, 409
    if outcome == "not_found":
        return result, 404
    return result, 400


def normalize_workspace_presentation(data: Any) -> Dict[str, Any]:
    """Validate the separately revisioned workspace-window presentation."""
    if not isinstance(data, dict):
        raise PresentationValidationError(
            "Workspace presentation payload must be an object"
        )
    allowed = {"workspace_id", "expected_revision", "topbar_visible"}
    unknown = set(data) - allowed
    if unknown:
        raise PresentationValidationError(f"Unknown field: {sorted(unknown)[0]}")
    workspace_id = data.get("workspace_id")
    revision = data.get("expected_revision")
    topbar_visible = normalize_topbar_visible(data.get("topbar_visible"))
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise PresentationValidationError("A non-empty 'workspace_id' is required")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PresentationValidationError(
            "'expected_revision' must be a non-negative integer"
        )
    if topbar_visible is None:
        raise PresentationValidationError("'topbar_visible' must be a boolean")
    return {
        "workspace_id": workspace_id.strip(),
        "expected_revision": revision,
        "topbar_visible": topbar_visible,
    }


def apply_workspace_presentation(
    manager: Any,
    data: Any,
) -> Tuple[Dict[str, Any], int]:
    """Apply workspace chrome with the same compare-and-swap discipline."""
    normalized = normalize_workspace_presentation(data)
    result = manager.apply_workspace_presentation(**normalized)
    outcome = result.pop("outcome")
    if outcome == "ok":
        return result, 200
    if outcome == "conflict":
        return result, 409
    if outcome == "not_found":
        return result, 404
    return result, 400


def pane_fields_for_mode(startup_mode: str) -> frozenset:
    """Return fields valid for one pane mode; used under manager membership checks."""
    if startup_mode == "explorer":
        return _EXPLORER_FIELDS
    if startup_mode == "browser":
        return _BROWSER_FIELDS
    return frozenset()


def deep_copy_presentation(value: Any) -> Any:
    """Name the snapshot-copy boundary used by the manager transaction."""
    return copy.deepcopy(value)
