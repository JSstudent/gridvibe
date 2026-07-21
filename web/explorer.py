"""Read-only file-explorer and Git-sidebar backends (local + SFTP).

Extracted from web/api.py (deep-dive finding 6.2, building on 6.1's backend
abstraction). Contains session classification, explorer path resolution, the
local and SFTP explorer backends, the single set of Git helpers parameterised
by backend, and the per-session SSH client pool (finding 3.1).
"""

import codecs
import logging
import os
import posixpath
import re
import shlex
import stat as stat_module
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from web.config import runtime_config
from web.hostkeys import (  # noqa: F401 - _load_persistent_host_keys re-exported
    _apply_host_key_policy,
    _load_persistent_host_keys,
)

try:
    import paramiko
except ImportError:  # pragma: no cover - handled at runtime when dependency is missing
    paramiko = None

try:
    import bleach
    import markdown
except ImportError:  # pragma: no cover - dependency declared for runtime installs
    bleach = None
    markdown = None

logger = logging.getLogger(__name__)

# Raised from 1 MiB to 10 MiB (OD-9): the head/tail ranged-read machinery
# already scales, and the client renders previews above ~2 MiB as plain text
# (no syntax highlighting) to keep the viewer responsive.
EXPLORER_FILE_PREVIEW_MAX_BYTES = 10 * 1024 * 1024
EXPLORER_GIT_DIFF_MAX_BYTES = 256 * 1024
EXPLORER_GIT_DIFF_MAX_LINES = 4000
EXPLORER_GIT_LOG_MAX_COMMITS = 60


def _is_explorer_session(session: Any) -> bool:
    """Return whether a session should render as a file explorer pane."""
    return (
        getattr(session, "mode", "") in {"ssh", "wsl"}
        and getattr(session, "startup_mode", "") == "explorer"
    )


def _is_browser_session(session: Any) -> bool:
    """Return whether a session should render as a browser pane."""
    return (
        getattr(session, "mode", "") == "wsl"
        and getattr(session, "startup_mode", "") == "browser"
    )


def _is_remote_explorer_session(session: Any) -> bool:
    """Return whether explorer requests should browse over SSH/SFTP."""
    return getattr(session, "mode", "") == "ssh" and _is_explorer_session(session)


def _explorer_root_directory(session: Any) -> str:
    """Return the stable explorer root, falling back to the session directory."""
    return str(
        getattr(session, "explorer_root_directory", "")
        or getattr(session, "directory", "")
        or ""
    ).strip()


def _default_explorer_candidate_path(session: Any, root_path: str) -> str:
    """Return the default explorer directory when no path is requested."""
    current_raw = str(getattr(session, "directory", "") or "").strip()
    if not current_raw:
        return root_path

    candidate = os.path.realpath(os.path.abspath(os.path.expanduser(current_raw)))
    if not os.path.isdir(candidate):
        return root_path

    try:
        common_path = os.path.commonpath([root_path, candidate])
    except ValueError:
        return root_path

    if os.path.normcase(common_path) != os.path.normcase(root_path):
        return root_path

    return candidate


MARKDOWN_PREVIEW_EXTENSIONS = {".md", ".markdown"}
CODE_PREVIEW_LANGUAGES = {
    ".bash": "shell",
    ".bat": "batch",
    ".c": "c",
    ".cc": "cpp",
    ".cfg": "config",
    ".cmd": "batch",
    ".conf": "config",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".env": "dotenv",
    ".example": "config",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".ini": "ini",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".jsonl": "jsonl",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".log": "log",
    ".lua": "lua",
    ".php": "php",
    ".ps1": "powershell",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".spec": "python",
    ".swift": "swift",
    ".txt": "text",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
CODE_PREVIEW_FILENAMES = {
    ".editorconfig": "ini",
    ".env": "dotenv",
    ".gitattributes": "config",
    ".gitignore": "gitignore",
    ".gitkeep": "text",
    ".python-version": "text",
    "dockerfile": "dockerfile",
    "go.mod": "go",
    "go.sum": "text",
    "go.work": "go",
    "go.work.sum": "text",
    "makefile": "makefile",
}
EXPLORER_BINARY_SAMPLE_BYTES = 4096
EXPLORER_TEXT_CONTROL_BYTES = {7, 8, 9, 10, 12, 13, 27}
MARKDOWN_ALLOWED_TAGS = {
    "a",
    "abbr",
    "blockquote",
    "br",
    "code",
    "dd",
    "del",
    "details",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "input",
    "ins",
    "kbd",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
def _allow_fenced_code_language(tag: str, name: str, value: str) -> bool:
    """Permit only fenced-code language hints (``class="language-*"``) on code."""
    if name != "class":
        return False
    tokens = value.split()
    return bool(tokens) and all(token.startswith("language-") for token in tokens)


MARKDOWN_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "abbr": ["title"],
    "code": _allow_fenced_code_language,
    "img": ["alt", "src", "title"],
    "input": ["checked", "disabled", "type"],
    "td": ["align"],
    "th": ["align"],
}


def _is_markdown_file(path: str) -> bool:
    """Return whether an explorer file should get a Markdown preview."""
    _, extension = os.path.splitext(path.lower())
    return extension in MARKDOWN_PREVIEW_EXTENSIONS


def _is_tail_preview_file(path: str) -> bool:
    """Return whether an oversized preview should retain the file's *tail*.

    Append-oriented files (logs) carry their most relevant content at the end,
    so a truncated preview keeps the newest bytes instead of the oldest. The
    classification reuses the existing language map (``.log`` → ``"log"``) so it
    stays in one place.
    """
    return _explorer_code_language(path) == "log"


def _explorer_code_language(path: str) -> Optional[str]:
    """Return the source language for code files shown in explorer previews."""
    filename = os.path.basename(path).lower()
    if filename in CODE_PREVIEW_FILENAMES:
        return CODE_PREVIEW_FILENAMES[filename]
    if filename.startswith(".env."):
        return "dotenv"
    _, extension = os.path.splitext(path.lower())
    if extension in MARKDOWN_PREVIEW_EXTENSIONS:
        return "markdown"
    return CODE_PREVIEW_LANGUAGES.get(extension)


def _explorer_editor_language(path: str) -> str:
    """Return the editor language or reject unsupported explorer formats."""
    language = _explorer_code_language(path)
    if language is None:
        raise ValueError("Explorer file format is not supported for editor preview")
    return language


# Extensions the read-only image viewer renders inline via an <img> tag. SVG is
# included but served with a locked-down CSP (see the image route) so embedded
# script cannot run even on direct navigation.
EXPLORER_IMAGE_MIMETYPES = {
    ".apng": "image/apng",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _explorer_image_mimetype(path: str) -> Optional[str]:
    """Return the image MIME type for a supported image path, else ``None``."""
    _, extension = os.path.splitext(str(path or "").lower())
    return EXPLORER_IMAGE_MIMETYPES.get(extension)


def _is_explorer_image_file(path: str) -> bool:
    """Return whether an explorer file should render in the inline image viewer."""
    return _explorer_image_mimetype(path) is not None


def _explorer_content_looks_binary(raw_content: bytes) -> bool:
    """Return whether a preview sample should be treated as binary content."""
    sample = raw_content[:EXPLORER_BINARY_SAMPLE_BYTES]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    try:
        # A capped sample can end in the middle of an otherwise valid multibyte
        # character. Incremental decoding validates every complete sequence and
        # defers a trailing partial sequence only when more content follows.
        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(
            sample, final=len(raw_content) <= EXPLORER_BINARY_SAMPLE_BYTES
        )
    except UnicodeDecodeError:
        return True
    control_count = sum(
        1
        for byte in sample
        if byte < 32 and byte not in EXPLORER_TEXT_CONTROL_BYTES
    )
    return control_count / len(sample) > 0.30


def _trim_tail_preview_to_boundary(window: bytes) -> bytes:
    """Drop a leading partial line / broken multibyte char from a tail window.

    A tail read starts at an arbitrary byte offset, so its first line is usually
    incomplete and may begin mid-character. Trimming to the first newline yields a
    clean line + UTF-8 boundary; when there is no usable newline we at least skip
    any leading UTF-8 continuation bytes so decoding never starts mid-character.
    """
    newline = window.find(b"\n")
    if 0 <= newline < len(window) - 1:
        return window[newline + 1:]
    lead = 0
    while lead < len(window) and (window[lead] & 0xC0) == 0x80:
        lead += 1
    return window[lead:]


def read_explorer_file_preview(
    backend: Any,
    file_path: str,
    *,
    total_size: Optional[int],
    tail: bool,
) -> Dict[str, Any]:
    """Read a bounded text preview, keeping the head or tail of the file.

    ``tail`` append-oriented files that exceed the cap keep their newest bytes
    (trimmed to a clean line/UTF-8 boundary); every other oversized file keeps
    its opening bytes. The returned metadata records which end was retained and
    the byte range so the client can message it precisely.
    """
    max_bytes = EXPLORER_FILE_PREVIEW_MAX_BYTES
    if tail and total_size is not None and total_size > max_bytes:
        window = _trim_tail_preview_to_boundary(
            backend.read_file_suffix(file_path, max_bytes, total_size)
        )
        return {
            "bytes": window,
            "truncated": True,
            "preview_mode": "tail",
            "preview_start_byte": total_size - len(window),
            "preview_end_byte": total_size,
            "total_size": total_size,
        }

    raw_content = backend.read_file_prefix(file_path, max_bytes + 1)
    truncated = len(raw_content) > max_bytes
    window = raw_content[:max_bytes]
    return {
        "bytes": window,
        "truncated": truncated,
        "preview_mode": "head",
        "preview_start_byte": 0,
        "preview_end_byte": len(window),
        "total_size": total_size if total_size is not None else len(raw_content),
    }


# GitHub-style admonition callouts (ISSUE-2026-017). Each label maps a
# ``> [!TYPE]`` blockquote to a fixed CSS class + accessible heading. The set is
# closed and backend-owned, so the augmentation below never emits user-derived
# class names.
GITHUB_CALLOUT_LABELS = {
    "note": "Note",
    "tip": "Tip",
    "important": "Important",
    "warning": "Warning",
    "caution": "Caution",
}

# Stroke-style ``currentColor`` icons (deep-dive guardrail 7: no emoji/glyphs).
# Injected only after sanitization into trusted, backend-built markup.
_CALLOUT_ICON_SVG = {
    "note": (
        '<svg class="md-callout-icon" viewBox="0 0 24 24" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<circle cx="12" cy="12" r="9"></circle>'
        '<line x1="12" y1="11" x2="12" y2="16"></line>'
        '<circle cx="12" cy="8" r="0.6" fill="currentColor" stroke="none">'
        "</circle></svg>"
    ),
    "tip": (
        '<svg class="md-callout-icon" viewBox="0 0 24 24" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<path d="M9 18h6"></path><path d="M10 22h4"></path>'
        '<path d="M12 2a7 7 0 0 0-4 12c.5.5 1 1.2 1 2h6c0-.8.5-1.5 1-2a7 7 0 0'
        ' 0-4-12z"></path></svg>'
    ),
    "important": (
        '<svg class="md-callout-icon" viewBox="0 0 24 24" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2'
        ' 2z"></path><line x1="12" y1="8" x2="12" y2="12"></line>'
        '<circle cx="12" cy="15" r="0.6" fill="currentColor" stroke="none">'
        "</circle></svg>"
    ),
    "warning": (
        '<svg class="md-callout-icon" viewBox="0 0 24 24" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7'
        ' 3.9a2 2 0 0 0-3.4 0z"></path>'
        '<line x1="12" y1="9" x2="12" y2="13"></line>'
        '<circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none">'
        "</circle></svg>"
    ),
    "caution": (
        '<svg class="md-callout-icon" viewBox="0 0 24 24" fill="none"'
        ' stroke="currentColor" stroke-width="2" stroke-linecap="round"'
        ' stroke-linejoin="round" aria-hidden="true" focusable="false">'
        '<polygon points="7.9 2 16.1 2 22 7.9 22 16.1 16.1 22 7.9 22 2 16.1 2'
        ' 7.9"></polygon><line x1="12" y1="8" x2="12" y2="12"></line>'
        '<circle cx="12" cy="16" r="0.6" fill="currentColor" stroke="none">'
        "</circle></svg>"
    ),
}

# An innermost ``<blockquote>`` (no nested blockquote inside) produced by the
# Markdown renderer + bleach; nested-blockquote callouts fall through unchanged.
_CALLOUT_BLOCKQUOTE_RE = re.compile(
    r"<blockquote>(?P<inner>(?:(?!</?blockquote>).)*?)</blockquote>",
    re.DOTALL,
)
# The first paragraph of a callout blockquote starts with the ``[!TYPE]`` marker.
_CALLOUT_MARKER_RE = re.compile(
    r"^\s*<p>\s*\[!(?P<type>NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][^\S\n]*"
    r"(?:<br\s*/?>)?\s*(?P<body>.*?)</p>(?P<rest>.*)$",
    re.DOTALL | re.IGNORECASE,
)


def _augment_markdown_callouts(html: str) -> str:
    """Rewrite ``[!TYPE]`` blockquotes into semantic callout blocks.

    Runs on already-sanitized HTML, so the injected ``div``/``span``/icon markup
    and the fixed callout classes are trusted; the blockquote body was cleaned by
    bleach. Non-callout blockquotes are returned untouched.
    """

    def _replace(match: "re.Match[str]") -> str:
        inner = match.group("inner")
        marker = _CALLOUT_MARKER_RE.match(inner)
        if not marker:
            return match.group(0)
        kind = marker.group("type").lower()
        label = GITHUB_CALLOUT_LABELS[kind]
        icon = _CALLOUT_ICON_SVG[kind]
        body = marker.group("body")
        rest = marker.group("rest")
        body_html = f"<p>{body}</p>" if body.strip() else ""
        return (
            f'<div class="md-callout md-callout-{kind}">'
            f'<p class="md-callout-title">{icon}'
            f'<span class="md-callout-label">{label}</span></p>'
            f"{body_html}{rest}</div>"
        )

    return _CALLOUT_BLOCKQUOTE_RE.sub(_replace, html)


def _render_markdown_preview(content: str) -> Optional[str]:
    """Render Markdown to sanitized HTML without interpreting raw HTML input."""
    if markdown is None or bleach is None:
        return None

    renderer = markdown.Markdown(
        extensions=[
            "fenced_code",
            "footnotes",
            "attr_list",
            "def_list",
            "tables",
            "abbr",
            "sane_lists",
        ],
        output_format="html",
    )
    renderer.preprocessors.deregister("html_block")
    renderer.inlinePatterns.deregister("html")
    html = renderer.convert(content)
    sanitized = bleach.clean(
        html,
        tags=MARKDOWN_ALLOWED_TAGS,
        attributes=MARKDOWN_ALLOWED_ATTRIBUTES,
        protocols=bleach.sanitizer.ALLOWED_PROTOCOLS,
        strip=True,
    )
    # Callout augmentation happens after sanitization so it never widens the
    # bleach allowlist; the classes/icons it adds are backend-controlled.
    return _augment_markdown_callouts(sanitized)


def _resolve_explorer_candidate_path(
    session: Any,
    requested_path: Any = "",
    *,
    allow_empty_root: bool = True,
) -> Tuple[str, str]:
    """Resolve an explorer request path while keeping it inside the session root."""
    if not _is_explorer_session(session):
        raise ValueError("Session is not a file explorer pane")

    root_raw = _explorer_root_directory(session)
    if not root_raw:
        raise ValueError("Explorer root directory is not configured")

    root_path = os.path.realpath(os.path.abspath(os.path.expanduser(root_raw)))
    if not os.path.isdir(root_path):
        raise ValueError("Explorer root directory does not exist")

    if requested_path is None:
        if not allow_empty_root:
            raise ValueError("Explorer file path is required")
        candidate = _default_explorer_candidate_path(session, root_path)
    else:
        raw_path = str(requested_path or "").strip()
        if not raw_path:
            if not allow_empty_root:
                raise ValueError("Explorer file path is required")
            candidate = root_path
        elif os.path.isabs(raw_path):
            candidate = os.path.realpath(os.path.abspath(os.path.expanduser(raw_path)))
        else:
            candidate = os.path.realpath(os.path.abspath(os.path.join(root_path, raw_path)))

    try:
        common_path = os.path.commonpath([root_path, candidate])
    except ValueError as exc:
        raise ValueError("Explorer path must stay inside the configured root") from exc

    if os.path.normcase(common_path) != os.path.normcase(root_path):
        raise ValueError("Explorer path must stay inside the configured root")

    return root_path, candidate


def _resolve_explorer_paths(session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve an explorer directory path while keeping it inside the session root."""
    root_path, candidate = _resolve_explorer_candidate_path(session, requested_path)
    if not os.path.isdir(candidate):
        raise ValueError("Explorer path is not a directory")

    return root_path, candidate


def _resolve_explorer_file_path(session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve an explorer file path while keeping it inside the session root."""
    root_path, candidate = _resolve_explorer_candidate_path(
        session,
        requested_path,
        allow_empty_root=False,
    )
    if os.path.isdir(candidate):
        raise ValueError("Explorer path is a directory")
    if not os.path.isfile(candidate):
        raise ValueError("Explorer path is not a file")

    return root_path, candidate


def open_path_in_os_file_manager(abs_path: str) -> None:
    """Reveal an already-resolved local path in the host OS file manager.

    Files are highlighted inside their parent folder; directories open directly.
    This is a read-only side effect (it launches the file manager and never
    mutates the filesystem). Callers must pass a root-confined absolute path
    produced by the explorer path resolvers — never a raw page-supplied value.
    """
    target = str(abs_path or "").strip()
    if not target or not os.path.exists(target):
        raise ValueError("Explorer path is no longer available")

    is_file = os.path.isfile(target)
    # No shell is used (list argv), so the resolved path cannot inject commands.
    if sys.platform == "win32":
        args = ["explorer", f"/select,{target}"] if is_file else ["explorer", target]
    elif sys.platform == "darwin":
        args = ["open", "-R", target] if is_file else ["open", target]
    else:
        # Most Linux file managers have no portable "reveal" flag, so open the
        # containing directory for files.
        args = ["xdg-open", os.path.dirname(target) if is_file else target]

    try:
        # explorer.exe exits non-zero even on success, so fire-and-forget.
        subprocess.Popen(args, close_fds=True)
    except OSError as exc:
        raise ValueError(f"Could not open the file manager: {exc}") from exc


def _relative_explorer_path(root_path: str, path: str) -> str:
    """Return a stable slash-separated explorer path relative to the root."""
    relative = os.path.relpath(path, root_path)
    return "" if relative == "." else relative.replace(os.sep, "/")


def _explorer_entry_payload(root_path: str, entry: os.DirEntry) -> Dict[str, Any]:
    """Return metadata for one explorer entry."""
    try:
        stat_result = entry.stat(follow_symlinks=False)
        is_dir = entry.is_dir(follow_symlinks=False)
    except OSError:
        stat_result = None
        is_dir = False

    return {
        "name": entry.name,
        "path": _relative_explorer_path(root_path, entry.path),
        "type": "directory" if is_dir else "file",
        "size": None if is_dir or stat_result is None else stat_result.st_size,
        "modified": None if stat_result is None else stat_result.st_mtime,
    }


def _empty_explorer_git_context(error: Optional[str] = None) -> Dict[str, Any]:
    """Return a non-fatal empty Git metadata payload for explorer responses."""
    return {
        "available": False,
        "repo_root": None,
        "branch": None,
        "head": None,
        "ahead": None,
        "behind": None,
        "dirty": False,
        "error": error,
    }


def _clean_git_path(path: str) -> str:
    """Normalize Git path output to slash-separated relative paths."""
    return str(path or "").strip().replace("\\", "/").strip("/")


GIT_READ_TIMEOUT = 2.0
GIT_WRITE_TIMEOUT = 15.0


def _run_git_command(
    args: List[str],
    *,
    cwd: str,
    timeout: Optional[float] = None,
    write: bool = False,
) -> subprocess.CompletedProcess:
    """Run an explorer Git command with predictable process settings.

    Reads run with GIT_OPTIONAL_LOCKS=0; writes run with GIT_TERMINAL_PROMPT=0
    so they can never hang on an interactive credential prompt.
    """
    if timeout is None:
        timeout = GIT_WRITE_TIMEOUT if write else GIT_READ_TIMEOUT
    env = os.environ.copy()
    if write:
        env["GIT_TERMINAL_PROMPT"] = "0"
    else:
        env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def _decode_git_output(raw_output: bytes) -> str:
    """Decode Git command output without raising on repository filename bytes."""
    return raw_output.decode("utf-8", errors="replace").strip()


def _parse_git_ahead_behind(value: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse a porcelain v2 branch.ab header value."""
    ahead = None
    behind = None
    for part in value.split():
        if part.startswith("+"):
            try:
                ahead = int(part[1:])
            except ValueError:
                ahead = None
        elif part.startswith("-"):
            try:
                behind = int(part[1:])
            except ValueError:
                behind = None
    return ahead, behind


def _git_status_name(index_status: str, worktree_status: str, record_type: str) -> str:
    """Map porcelain status codes to the compact explorer status set."""
    if record_type == "?":
        return "untracked"
    if record_type == "!":
        return "ignored"
    status_code = f"{index_status}{worktree_status}"
    if record_type == "u" or "U" in status_code:
        return "conflicted"
    if "R" in status_code:
        return "renamed"
    if "A" in status_code:
        return "added"
    if "D" in status_code:
        return "deleted"
    if any(code in status_code for code in ("M", "T")):
        return "modified"
    return "clean"


def _clean_git_entry_status() -> Dict[str, Any]:
    """Return the default Git status metadata for an explorer entry."""
    return {
        "status": "clean",
        "index_status": " ",
        "worktree_status": " ",
        "has_descendant_changes": False,
        "descendant_status": None,
    }


def _git_status_payload(
    path: str,
    index_status: str,
    worktree_status: str,
    record_type: str,
    original_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build per-path Git status metadata from one porcelain record."""
    status = _git_status_name(index_status, worktree_status, record_type)
    payload: Dict[str, Any] = {
        "path": path,
        "status": status,
        "index_status": index_status or " ",
        "worktree_status": worktree_status or " ",
        "has_descendant_changes": status != "clean",
    }
    if original_path:
        payload["original_path"] = original_path
    return payload


def _parse_git_status_porcelain_v2(raw_output: bytes) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Parse NUL-delimited `git status --porcelain=v2 -z --branch` output."""
    branch: Dict[str, Any] = {
        "branch": None,
        "head": None,
        "ahead": None,
        "behind": None,
    }
    statuses: Dict[str, Dict[str, Any]] = {}
    records = raw_output.decode("utf-8", errors="replace").split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# "):
            header = record[2:]
            key, _, value = header.partition(" ")
            if key == "branch.head" and value != "(detached)":
                branch["branch"] = value or None
            elif key == "branch.oid":
                branch["head"] = None if value == "(initial)" else value[:12]
            elif key == "branch.ab":
                branch["ahead"], branch["behind"] = _parse_git_ahead_behind(value)
            continue

        record_type = record[0]
        if record_type in ("?", "!"):
            path = _clean_git_path(record[2:])
            if path:
                statuses[path] = _git_status_payload(path, "?", "?", record_type)
            continue
        if record_type == "1":
            parts = record.split(" ", 8)
            if len(parts) >= 9:
                index_status, worktree_status = parts[1][0], parts[1][1]
                path = _clean_git_path(parts[8])
                if path:
                    statuses[path] = _git_status_payload(path, index_status, worktree_status, record_type)
            continue
        if record_type == "2":
            parts = record.split(" ", 9)
            original_path = records[index] if index < len(records) else ""
            if index < len(records):
                index += 1
            if len(parts) >= 10:
                index_status, worktree_status = parts[1][0], parts[1][1]
                path = _clean_git_path(parts[9])
                if path:
                    statuses[path] = _git_status_payload(
                        path,
                        index_status,
                        worktree_status,
                        record_type,
                        _clean_git_path(original_path),
                    )
            continue
        if record_type == "u":
            parts = record.split(" ", 11)
            if len(parts) >= 12:
                index_status, worktree_status = parts[1][0], parts[1][1]
                path = _clean_git_path(parts[11])
                if path:
                    statuses[path] = _git_status_payload(path, index_status, worktree_status, record_type)

    return branch, statuses


def _repo_relative_git_path(repo_root: str, path: str) -> str:
    """Return a slash-separated path relative to a Git repository root."""
    canonical_repo_root = str(Path(repo_root).expanduser().resolve(strict=False))
    canonical_path = str(Path(path).expanduser().resolve(strict=False))
    relative = os.path.relpath(canonical_path, canonical_repo_root)
    return "" if relative == "." else relative.replace(os.sep, "/")


def _repo_relative_remote_git_path(repo_root: str, path: str) -> str:
    """Return a slash-separated remote path relative to a Git repository root."""
    repo_clean = _remote_path_clean(repo_root).rstrip("/") or "/"
    path_clean = _remote_path_clean(path).rstrip("/") or "/"
    relative = posixpath.relpath(path_clean, repo_clean)
    return "" if relative == "." else relative.replace("\\", "/")


def _git_status_for_entry(
    backend: Any,
    repo_root: str,
    entry_path: str,
    statuses: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Return exact or descendant Git status metadata for one explorer entry."""
    repo_relative = _clean_git_path(backend.repo_relative_path(repo_root, entry_path))
    status = dict(statuses.get(repo_relative, _clean_git_entry_status()))
    descendant_prefix = f"{repo_relative}/" if repo_relative else ""
    if descendant_prefix:
        descendant_status = _aggregate_git_status(
            item.get("status", "clean")
            for path, item in statuses.items()
            if path.startswith(descendant_prefix)
        )
        if descendant_status:
            status["has_descendant_changes"] = True
            status["descendant_status"] = descendant_status
            if status.get("status") == "clean":
                status["status"] = descendant_status
    return status


def _aggregate_git_status(statuses: Any) -> Optional[str]:
    """Return the most useful status badge for a collection of descendant changes."""
    priority = (
        "conflicted",
        "modified",
        "added",
        "deleted",
        "renamed",
        "untracked",
        "ignored",
    )
    status_set = {status for status in statuses if status and status != "clean"}
    for status in priority:
        if status in status_set:
            return status
    return None


REMOTE_GIT_READ_TIMEOUT = 2.0
REMOTE_GIT_WRITE_TIMEOUT = 30.0


def _remote_git_shell_command(args: List[str], cwd: str, *, write: bool = False) -> str:
    """Build a Git command for a remote POSIX-compatible SSH shell.

    Reads run with GIT_OPTIONAL_LOCKS=0; writes run with GIT_TERMINAL_PROMPT=0
    so they can never hang on an interactive credential prompt.
    """
    quoted_args = " ".join(shlex.quote(part) for part in args)
    env_prefix = "GIT_TERMINAL_PROMPT=0" if write else "GIT_OPTIONAL_LOCKS=0"
    return f"{env_prefix} git -C {shlex.quote(cwd)} {quoted_args}"


def _run_remote_git_command(
    client: Any,
    args: List[str],
    *,
    cwd: str,
    timeout: Optional[float] = None,
    write: bool = False,
) -> Any:
    """Run a Git command over SSH and return a subprocess-like result."""
    if timeout is None:
        timeout = REMOTE_GIT_WRITE_TIMEOUT if write else REMOTE_GIT_READ_TIMEOUT
    command = _remote_git_shell_command(args, cwd, write=write)
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdout_data = stdout.read()
    stderr_data = stderr.read()
    if isinstance(stdout_data, str):
        stdout_data = stdout_data.encode("utf-8", errors="replace")
    if isinstance(stderr_data, str):
        stderr_data = stderr_data.encode("utf-8", errors="replace")
    return subprocess.CompletedProcess(
        args=command,
        returncode=stdout.channel.recv_exit_status(),
        stdout=stdout_data,
        stderr=stderr_data,
    )


class _LocalExplorerBackend:
    """Local-filesystem implementation of the explorer backend.

    The backend abstraction exists so every explorer helper and route body is
    written once: backends supply how Git runs (subprocess vs SSH) and how
    paths/files are resolved and read (os vs SFTP); the helpers supply the
    logic. All filesystem access stays read-only.
    """

    remote = False

    def __init__(self, session: Any = None):
        self.session = session

    # -- process execution -------------------------------------------------
    def run_git(
        self,
        args: List[str],
        *,
        cwd: str,
        timeout: Optional[float] = None,
        write: bool = False,
    ) -> subprocess.CompletedProcess:
        return _run_git_command(args, cwd=cwd, timeout=timeout, write=write)

    def request_error_types(self) -> Tuple[type, ...]:
        return (OSError,)

    # -- path handling ------------------------------------------------------
    def canonical_repo_root(self, raw_root: str) -> str:
        return os.path.realpath(os.path.abspath(raw_root))

    def validate_repo_paths(self, repo_root: str, root_path: str, current_path: str) -> Optional[str]:
        try:
            os.path.commonpath([repo_root, root_path])
            os.path.commonpath([repo_root, current_path])
        except ValueError:
            return "Git repository path could not be compared"
        return None

    def repo_relative_path(self, repo_root: str, path: str) -> str:
        return _repo_relative_git_path(repo_root, path)

    def pathspec(self, repo_root: str, scope_path: str) -> str:
        return _repo_relative_git_path(repo_root, scope_path) or "."

    def repo_abs_path(self, repo_root: str, repo_path: str) -> str:
        return os.path.realpath(os.path.abspath(os.path.join(repo_root, repo_path.replace("/", os.sep))))

    def entry_abs_path(self, root_path: str, entry_rel_path: str) -> str:
        return os.path.realpath(os.path.abspath(os.path.join(root_path, entry_rel_path)))

    def path_inside_root(self, root_path: str, abs_path: str) -> bool:
        root_real = os.path.realpath(os.path.abspath(root_path))
        try:
            common_path = os.path.commonpath([root_real, abs_path])
        except ValueError:
            return False
        return os.path.normcase(common_path) == os.path.normcase(root_real)

    def rel_explorer_path(self, root_path: str, abs_path: str) -> str:
        return _relative_explorer_path(os.path.realpath(os.path.abspath(root_path)), abs_path)

    def basename(self, path: str) -> str:
        return os.path.basename(path)

    def file_dirname(self, path: str) -> str:
        return os.path.dirname(path)

    # -- session path resolution ---------------------------------------------
    def resolve_candidate(self, requested_path: Any, *, allow_empty_root: bool = True) -> Tuple[str, str]:
        return _resolve_explorer_candidate_path(
            self.session, requested_path, allow_empty_root=allow_empty_root
        )

    def resolve_diff_path(self, requested_path: Any) -> Tuple[str, str]:
        root_path, file_path = self.resolve_candidate(requested_path, allow_empty_root=False)
        # A deleted file must stay diffable, so only directories are rejected.
        if os.path.isdir(file_path):
            raise ValueError("Explorer path is a directory")
        return root_path, file_path

    def resolve_dir(self, requested_path: Any) -> Tuple[str, str]:
        return _resolve_explorer_paths(self.session, requested_path)

    def resolve_file(self, requested_path: Any) -> Tuple[str, str]:
        return _resolve_explorer_file_path(self.session, requested_path)

    def root_directory(self) -> str:
        root_path = _explorer_root_directory(self.session)
        if not root_path:
            raise ValueError("Explorer root directory is not configured")
        return root_path

    # -- read-only filesystem access ------------------------------------------
    def list_entries(self, root_path: str, directory_path: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        with os.scandir(directory_path) as iterator:
            for entry in iterator:
                entries.append(_explorer_entry_payload(root_path, entry))
        return entries

    def stat_file(self, file_path: str) -> Tuple[Optional[int], Optional[float]]:
        stat_result = os.stat(file_path, follow_symlinks=False)
        return stat_result.st_size, stat_result.st_mtime

    def read_file_prefix(self, file_path: str, max_bytes: int) -> bytes:
        with open(file_path, "rb") as file_handle:
            return file_handle.read(max_bytes)

    def read_file_suffix(self, file_path: str, max_bytes: int, total_size: int) -> bytes:
        start = max(0, int(total_size) - max_bytes)
        with open(file_path, "rb") as file_handle:
            file_handle.seek(start)
            return file_handle.read(max_bytes)

    def parent_explorer_path(self, root_path: str, current_path: str) -> str:
        if os.path.normcase(current_path) == os.path.normcase(root_path):
            return ""
        return _relative_explorer_path(root_path, os.path.dirname(current_path))


class _SftpExplorerBackend:
    """SSH/SFTP implementation of the explorer backend."""

    remote = True

    def __init__(self, session: Any = None, client: Any = None, sftp: Any = None):
        self.session = session
        self.client = client
        self.sftp = sftp

    # -- process execution -------------------------------------------------
    def run_git(
        self,
        args: List[str],
        *,
        cwd: str,
        timeout: Optional[float] = None,
        write: bool = False,
    ) -> Any:
        return _run_remote_git_command(self.client, args, cwd=cwd, timeout=timeout, write=write)

    def request_error_types(self) -> Tuple[type, ...]:
        return _sftp_request_error_types()

    # -- path handling ------------------------------------------------------
    def canonical_repo_root(self, raw_root: str) -> str:
        return _remote_path_clean(raw_root).rstrip("/") or "/"

    def validate_repo_paths(self, repo_root: str, root_path: str, current_path: str) -> Optional[str]:
        if not _remote_path_inside(repo_root, current_path):
            return "Git repository path could not be compared"
        return None

    def repo_relative_path(self, repo_root: str, path: str) -> str:
        return _repo_relative_remote_git_path(repo_root, path)

    def pathspec(self, repo_root: str, scope_path: str) -> str:
        return _repo_relative_remote_git_path(repo_root, scope_path) or "."

    def repo_abs_path(self, repo_root: str, repo_path: str) -> str:
        return _remote_path_join(repo_root, repo_path)

    def entry_abs_path(self, root_path: str, entry_rel_path: str) -> str:
        return _remote_path_join(root_path, entry_rel_path)

    def path_inside_root(self, root_path: str, abs_path: str) -> bool:
        return _remote_path_inside(root_path, abs_path)

    def rel_explorer_path(self, root_path: str, abs_path: str) -> str:
        return _relative_remote_explorer_path(root_path, abs_path)

    def basename(self, path: str) -> str:
        return posixpath.basename(_remote_path_clean(path))

    def file_dirname(self, path: str) -> str:
        return _remote_path_dirname(path)

    # -- session path resolution ---------------------------------------------
    def resolve_candidate(self, requested_path: Any, *, allow_empty_root: bool = True) -> Tuple[str, str]:
        return _resolve_remote_explorer_candidate_path(
            self.sftp, self.session, requested_path, allow_empty_root=allow_empty_root
        )

    def resolve_diff_path(self, requested_path: Any) -> Tuple[str, str]:
        return _resolve_remote_explorer_file_path(self.sftp, self.session, requested_path)

    def resolve_dir(self, requested_path: Any) -> Tuple[str, str]:
        return _resolve_remote_explorer_paths(self.sftp, self.session, requested_path)

    def resolve_file(self, requested_path: Any) -> Tuple[str, str]:
        return _resolve_remote_explorer_file_path(self.sftp, self.session, requested_path)

    def root_directory(self) -> str:
        root_path = _remote_explorer_root_directory(self.session)
        if not root_path:
            raise ValueError("Explorer root directory is not configured")
        return self.sftp.normalize(root_path)

    # -- read-only filesystem access ------------------------------------------
    def list_entries(self, root_path: str, directory_path: str) -> List[Dict[str, Any]]:
        return [
            _remote_explorer_entry_payload(root_path, directory_path, entry)
            for entry in self.sftp.listdir_attr(directory_path)
        ]

    def stat_file(self, file_path: str) -> Tuple[Optional[int], Optional[float]]:
        stat_result = self.sftp.stat(file_path)
        return getattr(stat_result, "st_size", None), getattr(stat_result, "st_mtime", None)

    def read_file_prefix(self, file_path: str, max_bytes: int) -> bytes:
        with self.sftp.open(file_path, "rb") as file_handle:
            return file_handle.read(max_bytes)

    def read_file_suffix(self, file_path: str, max_bytes: int, total_size: int) -> bytes:
        start = max(0, int(total_size) - max_bytes)
        with self.sftp.open(file_path, "rb") as file_handle:
            file_handle.seek(start)
            return file_handle.read(max_bytes)

    def parent_explorer_path(self, root_path: str, current_path: str) -> str:
        if _remote_compare_path(current_path) == _remote_compare_path(root_path):
            return ""
        return _relative_remote_explorer_path(root_path, _remote_path_dirname(current_path))


@contextmanager
def _explorer_backend(session: Any):
    """Yield the Git backend for one explorer session, owning any SSH lifetime."""
    if _is_remote_explorer_session(session):
        client = None
        sftp = None
        try:
            client, sftp = _acquire_ssh_sftp(session)
            yield _SftpExplorerBackend(session, client, sftp)
        finally:
            _release_ssh_sftp(session, client, sftp)
    else:
        yield _LocalExplorerBackend(session)


def _get_git_context(
    backend: Any,
    root_path: str,
    current_path: str,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """Return repository metadata and path statuses for an explorer directory."""
    try:
        rev_parse = backend.run_git(
            ["rev-parse", "--show-toplevel", "--is-inside-work-tree"],
            cwd=current_path,
            timeout=2.0,
        )
    except FileNotFoundError:
        return _empty_explorer_git_context("Git executable was not found"), {}
    except (subprocess.TimeoutExpired, TimeoutError):
        return _empty_explorer_git_context("Git repository detection timed out"), {}
    except Exception as exc:
        return _empty_explorer_git_context(str(exc)), {}

    if rev_parse.returncode != 0:
        return _empty_explorer_git_context(), {}

    rev_lines = _decode_git_output(rev_parse.stdout).splitlines()
    if len(rev_lines) < 2 or rev_lines[1].lower() != "true":
        return _empty_explorer_git_context(), {}

    repo_root = backend.canonical_repo_root(rev_lines[0])
    validation_error = backend.validate_repo_paths(repo_root, root_path, current_path)
    if validation_error:
        return _empty_explorer_git_context(validation_error), {}

    status_args = [
        "status",
        "--porcelain=v2",
        "-z",
        "--branch",
        "--",
        backend.pathspec(repo_root, current_path),
    ]
    try:
        status_result = backend.run_git(status_args, cwd=repo_root, timeout=2.0)
    except (subprocess.TimeoutExpired, TimeoutError):
        context = _empty_explorer_git_context("Git status timed out")
        context["repo_root"] = repo_root
        return context, {}
    except Exception as exc:
        context = _empty_explorer_git_context(str(exc))
        context["repo_root"] = repo_root
        return context, {}

    if status_result.returncode != 0:
        error = _decode_git_output(status_result.stderr) or "Git status failed"
        context = _empty_explorer_git_context(error)
        context["repo_root"] = repo_root
        return context, {}

    branch, statuses = _parse_git_status_porcelain_v2(status_result.stdout)
    context = {
        "available": True,
        "repo_root": repo_root,
        "branch": branch.get("branch"),
        "head": branch.get("head"),
        "ahead": branch.get("ahead"),
        "behind": branch.get("behind"),
        "dirty": any(item.get("status") != "clean" for item in statuses.values()),
        "error": None,
    }
    return context, statuses


def _attach_git_status_to_entries(
    backend: Any,
    root_path: str,
    git_context: Dict[str, Any],
    statuses: Dict[str, Dict[str, Any]],
    entries: List[Dict[str, Any]],
) -> None:
    """Attach per-entry Git metadata to explorer entries."""
    if not git_context.get("available"):
        for entry in entries:
            entry["git"] = _clean_git_entry_status()
        return

    repo_root = str(git_context.get("repo_root") or "")
    for entry in entries:
        entry_path = backend.entry_abs_path(root_path, entry.get("path") or "")
        entry["git"] = _git_status_for_entry(backend, repo_root, entry_path, statuses)


def _append_deleted_git_entries(
    backend: Any,
    root_path: str,
    current_path: str,
    git_context: Dict[str, Any],
    statuses: Dict[str, Dict[str, Any]],
    entries: List[Dict[str, Any]],
) -> None:
    """Add read-only placeholder rows for deleted tracked files in the current directory."""
    if not git_context.get("available"):
        return

    repo_root = str(git_context.get("repo_root") or "")
    current_repo_relative = _clean_git_path(backend.repo_relative_path(repo_root, current_path))
    existing_paths = {entry.get("path") for entry in entries}
    for status_path, status in statuses.items():
        if status.get("status") != "deleted":
            continue
        if current_repo_relative:
            prefix = f"{current_repo_relative}/"
            if not status_path.startswith(prefix):
                continue
            current_relative = status_path[len(prefix):]
        else:
            current_relative = status_path
        if not current_relative or "/" in current_relative:
            continue

        deleted_abs_path = backend.repo_abs_path(repo_root, status_path)
        deleted_explorer_path = backend.rel_explorer_path(root_path, deleted_abs_path)
        if deleted_explorer_path in existing_paths:
            continue
        entries.append(
            {
                "name": current_relative,
                "path": deleted_explorer_path,
                "type": "file",
                "size": None,
                "modified": None,
                "git": dict(status),
                "deleted": True,
            }
        )
        existing_paths.add(deleted_explorer_path)


def _bounded_git_diff(backend: Any, repo_root: str, args: List[str]) -> Tuple[str, bool, int]:
    """Run Git diff and return bounded UTF-8 text output."""
    result = backend.run_git(args, cwd=repo_root, timeout=3.0)
    if result.returncode != 0:
        error = _decode_git_output(result.stderr) or "Git diff failed"
        raise ValueError(error)
    if b"\x00" in result.stdout:
        raise ValueError("Git diff appears to contain binary data")

    truncated = len(result.stdout) > EXPLORER_GIT_DIFF_MAX_BYTES
    diff_bytes = result.stdout[:EXPLORER_GIT_DIFF_MAX_BYTES]
    diff_text = diff_bytes.decode("utf-8", errors="replace")
    lines = diff_text.splitlines(keepends=True)
    if len(lines) > EXPLORER_GIT_DIFF_MAX_LINES:
        diff_text = "".join(lines[:EXPLORER_GIT_DIFF_MAX_LINES])
        truncated = True
    return diff_text, truncated, len(result.stdout)


def _validated_git_commit_ref(value: Any) -> str:
    """Return a short/full Git object id suitable for read-only diff commands."""
    commit = str(value or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise ValueError("Invalid Git commit")
    return commit


def _git_diff_args_for_mode(mode: str, pathspec: str, commit: Optional[str] = None) -> List[str]:
    """Return read-only Git diff arguments for an explorer file."""
    if mode == "worktree":
        return ["diff", "--no-ext-diff", "--no-color", "--", pathspec]
    if mode == "staged":
        return ["diff", "--cached", "--no-ext-diff", "--no-color", "--", pathspec]
    if mode == "head":
        return ["diff", "HEAD", "--no-ext-diff", "--no-color", "--", pathspec]
    if mode == "commit":
        return ["show", "--format=", "--no-ext-diff", "--no-color", commit or "", "--", pathspec]
    raise ValueError("Invalid Git diff mode")


def _parse_git_graph_log(raw_output: bytes) -> List[Dict[str, Any]]:
    """Parse bounded `git log --graph --oneline` output for the diff sidebar."""
    commits: List[Dict[str, Any]] = []
    for raw_line in raw_output.decode("utf-8", errors="replace").split("\n"):
        line = raw_line.rstrip()
        if not line:
            continue
        match = re.match(r"^(?P<graph>[\s*|\\/._-]*?)(?P<hash>[0-9a-fA-F]{7,40})\s+(?P<subject>.*)$", line)
        if not match:
            continue
        commit_hash = match.group("hash") if match else ""
        commits.append(
            {
                "line": line,
                "graph": match.group("graph").rstrip() if match else "",
                "hash": commit_hash,
                "subject": match.group("subject") if match else line,
            }
        )
    return commits[:EXPLORER_GIT_LOG_MAX_COMMITS]


def _parse_git_name_status_log(raw_output: bytes) -> Dict[str, List[Dict[str, Any]]]:
    """Parse `git log --name-status` output grouped by short commit hash."""
    files_by_commit: Dict[str, List[Dict[str, Any]]] = {}
    current_hash = ""
    for raw_line in raw_output.decode("utf-8", errors="replace").split("\n"):
        line = raw_line.strip("\r")
        if not line:
            continue
        if line.startswith("\x1e"):
            current_hash = line[1:].strip()
            if current_hash:
                files_by_commit.setdefault(current_hash, [])
            continue
        if not current_hash:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status_code = parts[0]
        path = _clean_git_path(parts[-1])
        if not path:
            continue
        entry: Dict[str, Any] = {
            "repo_path": path,
            "status_code": status_code,
            "git": _git_status_payload(path, status_code[:1] or " ", " ", "1"),
        }
        if status_code.startswith("R") and len(parts) >= 3:
            entry["original_path"] = _clean_git_path(parts[1])
            entry["git"]["original_path"] = entry["original_path"]
        files_by_commit.setdefault(current_hash, []).append(entry)
    return files_by_commit


def _git_commit_files_log(backend: Any, repo_root: str, pathspec: str) -> Dict[str, List[Dict[str, Any]]]:
    """Return changed files for each displayed commit."""
    result = backend.run_git(
        [
            "log",
            f"--max-count={EXPLORER_GIT_LOG_MAX_COMMITS}",
            "--format=%x1e%h",
            "--name-status",
            "--",
            pathspec,
        ],
        cwd=repo_root,
        timeout=3.0,
    )
    if result.returncode != 0:
        return {}
    return _parse_git_name_status_log(result.stdout)


def _commit_file_payload(
    backend: Any,
    root_path: str,
    repo_root: str,
    file_entry: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Convert one repo-relative commit file to an explorer-visible payload."""
    repo_path = str(file_entry.get("repo_path") or "")
    absolute_path = backend.repo_abs_path(repo_root, repo_path)
    if not backend.path_inside_root(root_path, absolute_path):
        return None
    payload = dict(file_entry)
    payload["path"] = backend.rel_explorer_path(root_path, absolute_path)
    payload["name"] = backend.basename(repo_path)
    return payload


def _attach_commit_files(
    commits: List[Dict[str, Any]],
    files_by_commit: Dict[str, List[Dict[str, Any]]],
    convert_file: Any,
) -> List[Dict[str, Any]]:
    """Attach file lists to graph commits without changing commit order."""
    for commit in commits:
        commit_hash = str(commit.get("hash") or "")
        files = files_by_commit.get(commit_hash, [])
        if not files:
            files = next(
                (items for key, items in files_by_commit.items() if commit_hash and key.startswith(commit_hash)),
                [],
            )
        visible_files = [converted for item in files if (converted := convert_file(item))]
        commit["files"] = visible_files
    return commits


def _bounded_git_graph_log(backend: Any, repo_root: str, pathspec: str) -> List[Dict[str, Any]]:
    """Return a bounded commit graph for the explorer root scope."""
    result = backend.run_git(
        [
            "log",
            "--graph",
            "--decorate",
            "--oneline",
            "--date-order",
            f"--max-count={EXPLORER_GIT_LOG_MAX_COMMITS}",
            "--",
            pathspec,
        ],
        cwd=repo_root,
        timeout=3.0,
    )
    if result.returncode != 0:
        return []
    return _parse_git_graph_log(result.stdout)


def _explorer_git_changed_files(
    backend: Any,
    root_path: str,
    repo_root: str,
    statuses: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return changed Git files visible inside the explorer root."""
    changes: List[Dict[str, Any]] = []
    for repo_path, status in sorted(statuses.items()):
        if status.get("status") == "clean":
            continue
        absolute_path = backend.repo_abs_path(repo_root, repo_path)
        if not backend.path_inside_root(root_path, absolute_path):
            continue
        explorer_path = backend.rel_explorer_path(root_path, absolute_path)
        changes.append(
            {
                "path": explorer_path,
                "repo_path": repo_path,
                "name": backend.basename(repo_path),
                "git": dict(status),
            }
        )
    return changes


def _get_git_repo_summary(backend: Any, root_path: str) -> Dict[str, Any]:
    """Return changed files and a bounded commit graph for an explorer root."""
    git_context, statuses = _get_git_context(backend, root_path, root_path)
    if not git_context.get("available"):
        raise ValueError(git_context.get("error") or "Folder is not inside a Git worktree")

    repo_root = str(git_context["repo_root"])
    root_pathspec = backend.pathspec(repo_root, root_path)
    commits = _bounded_git_graph_log(backend, repo_root, root_pathspec)
    commit_files = _git_commit_files_log(backend, repo_root, root_pathspec)
    return {
        "git": git_context,
        "changes": _explorer_git_changed_files(backend, root_path, repo_root, statuses),
        "commits": _attach_commit_files(
            commits,
            commit_files,
            lambda item: _commit_file_payload(backend, root_path, repo_root, item),
        ),
    }


def _get_git_diff(
    backend: Any,
    root_path: str,
    file_path: str,
    mode: str,
    commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a bounded read-only Git diff for an explorer file."""
    if mode not in {"worktree", "staged", "head", "commit"}:
        raise ValueError("Invalid Git diff mode")
    if mode == "commit":
        commit = _validated_git_commit_ref(commit)

    git_context, _statuses = _get_git_context(backend, root_path, backend.file_dirname(file_path))
    if not git_context.get("available"):
        raise ValueError(git_context.get("error") or "Folder is not inside a Git worktree")

    repo_root = str(git_context["repo_root"])
    pathspec = backend.pathspec(repo_root, file_path)
    try:
        diff_text, truncated, raw_bytes = _bounded_git_diff(
            backend,
            repo_root,
            _git_diff_args_for_mode(mode, pathspec, commit),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git diff timed out") from exc
    return {
        "git": git_context,
        "diff": diff_text,
        "truncated": truncated,
        "byte_count": raw_bytes,
        "line_count": len(diff_text.splitlines()),
    }


def _git_action_repo_root(backend: Any, root_path: str) -> str:
    """Return the repository root for an explorer git mutation, or raise."""
    git_context, _statuses = _get_git_context(backend, root_path, root_path)
    if not git_context.get("available"):
        raise ValueError(git_context.get("error") or "Folder is not inside a Git worktree")
    return str(git_context["repo_root"])


def _git_has_head(backend: Any, repo_root: str) -> bool:
    """Return whether the repository has at least one commit."""
    try:
        result = backend.run_git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo_root, timeout=2.0)
    except Exception:
        return False
    return result.returncode == 0


def _git_stage_path(backend: Any, root_path: str, file_path: str) -> None:
    """Stage one worktree path inside an explorer repository."""
    repo_root = _git_action_repo_root(backend, root_path)
    pathspec = backend.pathspec(repo_root, file_path)
    try:
        result = backend.run_git(["add", "--", pathspec], cwd=repo_root, write=True)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git stage timed out") from exc
    if result.returncode != 0:
        raise ValueError(_decode_git_output(result.stderr) or "Git stage failed")


def _git_unstage_path(backend: Any, root_path: str, file_path: str) -> None:
    """Unstage one path inside an explorer repository."""
    repo_root = _git_action_repo_root(backend, root_path)
    pathspec = backend.pathspec(repo_root, file_path)
    if _git_has_head(backend, repo_root):
        args = ["reset", "--quiet", "HEAD", "--", pathspec]
    else:
        args = ["rm", "--cached", "--quiet", "--", pathspec]
    try:
        result = backend.run_git(args, cwd=repo_root, write=True)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git unstage timed out") from exc
    if result.returncode != 0:
        raise ValueError(_decode_git_output(result.stderr) or "Git unstage failed")


def _git_stage_all_paths(backend: Any, root_path: str) -> None:
    """Stage every working-tree change in an explorer repository.

    Bulk form of _git_stage_path (ISSUE-2026-032): runs ``git add --all``
    scoped to the repository root so modified, deleted, and untracked files
    all land in the index in one action.
    """
    repo_root = _git_action_repo_root(backend, root_path)
    try:
        result = backend.run_git(["add", "--all"], cwd=repo_root, write=True)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git stage all timed out") from exc
    if result.returncode != 0:
        raise ValueError(_decode_git_output(result.stderr) or "Git stage all failed")


_GIT_UNMERGED_STATUS_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


def _git_revert_path(backend: Any, root_path: str, file_path: str) -> None:
    """Discard one tracked file's unstaged worktree changes.

    Runs the equivalent of ``git restore --worktree -- <path>``, which restores
    the worktree copy from the index, so any already-staged version of the file
    is preserved. Untracked and conflicted files are refused rather than being
    silently deleted or overwritten, and a file with no unstaged change is a
    clear error instead of a no-op that would look like a broken action.
    """
    repo_root = _git_action_repo_root(backend, root_path)
    pathspec = backend.pathspec(repo_root, file_path)
    try:
        status = backend.run_git(
            ["status", "--porcelain", "--", pathspec], cwd=repo_root, timeout=5.0
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git revert timed out") from exc
    if status.returncode != 0:
        raise ValueError(_decode_git_output(status.stderr) or "Git revert failed")

    # Decode without stripping so the leading porcelain XY status columns (e.g.
    # " M" for a worktree-only change) survive; _decode_git_output strips.
    raw_status = status.stdout.decode("utf-8", errors="replace")
    entries = [line for line in raw_status.splitlines() if line.strip()]
    if not entries:
        raise ValueError("No unstaged changes to revert")
    code = entries[0][:2]
    if code == "??":
        raise ValueError("Untracked files cannot be reverted")
    if code in _GIT_UNMERGED_STATUS_CODES or "U" in code:
        raise ValueError("Resolve merge conflicts before reverting")
    if code[1:2] in {" ", "."}:
        # Only the index differs (a staged-but-clean-worktree file); there is
        # nothing to discard and reverting must never touch staged content.
        raise ValueError("No unstaged changes to revert")

    try:
        result = backend.run_git(["restore", "--worktree", "--", pathspec], cwd=repo_root, write=True)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git revert timed out") from exc
    if result.returncode != 0:
        raise ValueError(
            _decode_git_output(result.stderr)
            or _decode_git_output(result.stdout)
            or "Git revert failed"
        )


def _git_discardable_worktree_paths(raw_status: str) -> List[str]:
    """Return the tracked paths with discardable unstaged changes.

    Parses NUL-terminated ``git status --porcelain -z`` output (no path
    quoting, so unusual filenames survive) and keeps only entries whose
    worktree column shows a change, excluding untracked and conflicted files
    — the same safety envelope as the single-file revert.
    """
    paths: List[str] = []
    records = iter(str(raw_status or "").split("\0"))
    for record in records:
        if len(record) < 4:
            continue
        code = record[:2]
        path = record[3:]
        if code[0] in "RC":
            # Rename/copy records carry the original path as the next token.
            next(records, None)
        if code == "??" or code in _GIT_UNMERGED_STATUS_CODES or "U" in code:
            continue
        if code[1] in " .":
            # Only the index differs; there is nothing unstaged to discard.
            continue
        paths.append(path)
    return paths


def _git_discard_all_paths(backend: Any, root_path: str) -> None:
    """Discard every tracked file's unstaged worktree changes.

    Bulk form of _git_revert_path (OD-1): restores only tracked,
    non-conflicted worktree changes with ``git restore --worktree``, so
    staged content is preserved and untracked files are left in place —
    never ``git clean``.
    """
    repo_root = _git_action_repo_root(backend, root_path)
    try:
        status = backend.run_git(["status", "--porcelain", "-z"], cwd=repo_root, timeout=5.0)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git discard all timed out") from exc
    if status.returncode != 0:
        raise ValueError(_decode_git_output(status.stderr) or "Git discard all failed")

    paths = _git_discardable_worktree_paths(status.stdout.decode("utf-8", errors="replace"))
    if not paths:
        raise ValueError("No unstaged changes to discard")

    try:
        result = backend.run_git(
            ["restore", "--worktree", "--", *paths], cwd=repo_root, timeout=30.0, write=True
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git discard all timed out") from exc
    if result.returncode != 0:
        raise ValueError(
            _decode_git_output(result.stderr)
            or _decode_git_output(result.stdout)
            or "Git discard all failed"
        )


def _git_commit(backend: Any, root_path: str, message: str) -> None:
    """Commit staged changes inside an explorer repository."""
    commit_message = str(message or "").strip()
    if not commit_message:
        raise ValueError("Commit message is required")
    repo_root = _git_action_repo_root(backend, root_path)
    try:
        result = backend.run_git(["commit", "-m", commit_message], cwd=repo_root, timeout=30.0, write=True)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git commit timed out") from exc
    if result.returncode != 0:
        raise ValueError(
            _decode_git_output(result.stderr)
            or _decode_git_output(result.stdout)
            or "Git commit failed"
        )


def _git_publish(backend: Any, root_path: str) -> None:
    """Push the current branch of an explorer repository, setting upstream if needed."""
    repo_root = _git_action_repo_root(backend, root_path)
    try:
        upstream = backend.run_git(
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            cwd=repo_root,
            timeout=3.0,
        )
        if upstream.returncode == 0:
            push_args = ["push"]
        else:
            branch_result = backend.run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, timeout=3.0)
            branch = _decode_git_output(branch_result.stdout)
            if branch_result.returncode != 0 or not branch or branch == "HEAD":
                raise ValueError("Cannot publish a detached HEAD")
            push_args = ["push", "--set-upstream", "origin", branch]
        result = backend.run_git(push_args, cwd=repo_root, timeout=120.0, write=True)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git publish timed out") from exc
    if result.returncode != 0:
        raise ValueError(
            _decode_git_output(result.stderr)
            or _decode_git_output(result.stdout)
            or "Git publish failed"
        )


_REMOTE_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _remote_path_clean(path: Any) -> str:
    """Normalize remote path separators without applying local OS path rules."""
    return str(path or "").strip().replace("\\", "/")


def _remote_path_is_absolute(path: str) -> bool:
    """Return whether a remote path is absolute on POSIX or Windows SFTP servers."""
    cleaned = _remote_path_clean(path)
    return cleaned.startswith("/") or bool(_REMOTE_WINDOWS_DRIVE_RE.match(cleaned))


def _remote_path_join(base: str, child: str) -> str:
    """Join remote path fragments using SFTP-friendly separators."""
    base = _remote_path_clean(base)
    child = _remote_path_clean(child)
    if not base:
        return child
    if not child:
        return base
    return f"{base.rstrip('/')}/{child.lstrip('/')}"


def _remote_path_dirname(path: str) -> str:
    """Return a remote parent path using POSIX-style separators."""
    cleaned = _remote_path_clean(path).rstrip("/")
    if not cleaned:
        return ""
    if _REMOTE_WINDOWS_DRIVE_RE.match(cleaned) and "/" not in cleaned[3:]:
        return cleaned
    parent = posixpath.dirname(cleaned)
    return parent or ("/" if cleaned.startswith("/") else "")


def _remote_compare_path(path: str) -> str:
    """Return a stable path string for remote containment checks."""
    cleaned = _remote_path_clean(path)
    if cleaned != "/":
        cleaned = cleaned.rstrip("/")
    if _REMOTE_WINDOWS_DRIVE_RE.match(cleaned) or re.match(r"^/[A-Za-z]:", cleaned):
        return cleaned.lower()
    return cleaned


def _remote_path_inside(root_path: str, candidate_path: str) -> bool:
    """Return whether candidate_path is root_path or a descendant of it."""
    root_compare = _remote_compare_path(root_path)
    candidate_compare = _remote_compare_path(candidate_path)
    if root_compare == candidate_compare:
        return True
    if root_compare == "/":
        return candidate_compare.startswith("/")
    return candidate_compare.startswith(f"{root_compare.rstrip('/')}/")


def _relative_remote_explorer_path(root_path: str, path: str) -> str:
    """Return a slash-separated remote explorer path relative to the root."""
    root_clean = _remote_path_clean(root_path).rstrip("/")
    path_clean = _remote_path_clean(path).rstrip("/")
    if _remote_compare_path(root_clean) == _remote_compare_path(path_clean):
        return ""
    relative = posixpath.relpath(path_clean, root_clean or "/")
    return "" if relative == "." else relative.replace("\\", "/")


def _remote_explorer_entry_payload(root_path: str, directory_path: str, entry: Any) -> Dict[str, Any]:
    """Return metadata for one remote explorer entry."""
    mode = getattr(entry, "st_mode", None)
    is_dir = stat_module.S_ISDIR(mode) if mode is not None else False
    entry_path = _remote_path_join(directory_path, getattr(entry, "filename", ""))
    return {
        "name": getattr(entry, "filename", ""),
        "path": _relative_remote_explorer_path(root_path, entry_path),
        "type": "directory" if is_dir else "file",
        "size": None if is_dir else getattr(entry, "st_size", None),
        "modified": getattr(entry, "st_mtime", None),
    }


def _open_ssh_sftp(session: Any) -> Tuple[Any, Any]:
    """Open a fresh SSH/SFTP connection for one explorer session."""
    if paramiko is None:
        raise RuntimeError("Paramiko is not installed. Run `pip install -r requirements.txt`.")

    client = paramiko.SSHClient()
    _apply_host_key_policy(client, paramiko)
    client.connect(
        hostname=session.host,
        port=session.port,
        username=session.username,
        password=session.password or None,
        timeout=runtime_config.ssh_config.get("connection_timeout", 30),
        look_for_keys=not bool(session.password),
        allow_agent=not bool(session.password),
    )
    return client, client.open_sftp()


# Remote explorer requests reuse one live SSH transport per session instead of
# paying a TCP + SSH handshake + auth round-trip on every click. Each request
# still opens its own SFTP channel from the pooled client (paramiko SFTP
# channels are not safe for concurrent use, but opening a channel on a live
# transport is cheap). Only genuine paramiko clients with an active transport
# are pooled; anything else keeps the historical open/close-per-request path.
_ssh_client_pool: Dict[str, Tuple[float, Any]] = {}  # session_id -> (last_used, client)
_ssh_client_pool_lock = threading.Lock()
SSH_CLIENT_POOL_IDLE_TIMEOUT = 60.0


def _ssh_client_transport_active(client: Any) -> bool:
    """Return whether a client is a paramiko SSHClient with a live transport."""
    if paramiko is None or not isinstance(client, paramiko.SSHClient):
        return False
    transport = client.get_transport()
    return transport is not None and transport.is_active()


def _close_ssh_client_quietly(client: Any) -> None:
    """Close one SSH client, swallowing shutdown errors."""
    try:
        client.close()
    except Exception:
        pass


def _reap_idle_pooled_ssh_clients() -> None:
    """Drop pooled SSH clients that have been idle past the timeout."""
    now = time.monotonic()
    stale_clients = []
    with _ssh_client_pool_lock:
        for session_id, (last_used, client) in list(_ssh_client_pool.items()):
            if now - last_used > SSH_CLIENT_POOL_IDLE_TIMEOUT:
                stale_clients.append(client)
                del _ssh_client_pool[session_id]
    for client in stale_clients:
        _close_ssh_client_quietly(client)


def _evict_pooled_ssh_client(session_id: str, client: Any = None) -> None:
    """Drop one pooled SSH client (optionally only when it matches `client`)."""
    with _ssh_client_pool_lock:
        entry = _ssh_client_pool.get(session_id)
        if entry is None or (client is not None and entry[1] is not client):
            return
        del _ssh_client_pool[session_id]
        pooled_client = entry[1]
    _close_ssh_client_quietly(pooled_client)


def _evict_all_pooled_ssh_clients() -> None:
    """Close and forget every pooled SSH client."""
    with _ssh_client_pool_lock:
        clients = [client for _, client in _ssh_client_pool.values()]
        _ssh_client_pool.clear()
    for client in clients:
        _close_ssh_client_quietly(client)


def _acquire_ssh_sftp(session: Any) -> Tuple[Any, Any]:
    """Return (client, sftp), reusing the pooled SSH transport when it is alive."""
    _reap_idle_pooled_ssh_clients()
    session_id = session.session_id
    with _ssh_client_pool_lock:
        entry = _ssh_client_pool.get(session_id)
    if entry is not None:
        client = entry[1]
        if _ssh_client_transport_active(client):
            try:
                sftp = client.open_sftp()
            except Exception:
                _evict_pooled_ssh_client(session_id, client)
            else:
                with _ssh_client_pool_lock:
                    current = _ssh_client_pool.get(session_id)
                    if current is not None and current[1] is client:
                        _ssh_client_pool[session_id] = (time.monotonic(), client)
                return client, sftp
        else:
            _evict_pooled_ssh_client(session_id, client)

    client, sftp = _open_ssh_sftp(session)
    if _ssh_client_transport_active(client):
        with _ssh_client_pool_lock:
            # A concurrent request may have pooled its own client meanwhile;
            # leave that one in place and let release close this one instead.
            if session_id not in _ssh_client_pool:
                _ssh_client_pool[session_id] = (time.monotonic(), client)
    return client, sftp


def _release_ssh_sftp(session: Any, client: Any, sftp: Any) -> None:
    """Finish one explorer request: close the SFTP channel, keep the transport."""
    try:
        if sftp is not None:
            sftp.close()
    except Exception:
        pass
    if client is None:
        return
    session_id = getattr(session, "session_id", "")
    with _ssh_client_pool_lock:
        entry = _ssh_client_pool.get(session_id)
        if entry is not None and entry[1] is client:
            if _ssh_client_transport_active(client):
                _ssh_client_pool[session_id] = (time.monotonic(), client)
                return
            del _ssh_client_pool[session_id]
    _close_ssh_client_quietly(client)


def _sftp_request_error_types() -> Tuple[type, ...]:
    """Return expected connection and SFTP errors for explorer routes."""
    error_types: List[type] = [OSError, RuntimeError]
    ssh_exception = getattr(paramiko, "SSHException", None) if paramiko is not None else None
    if isinstance(ssh_exception, type):
        error_types.append(ssh_exception)
    return tuple(error_types)


def _remote_is_directory(sftp: Any, path: str) -> bool:
    """Return whether a remote path is a directory."""
    attrs = sftp.stat(path)
    mode = getattr(attrs, "st_mode", None)
    return stat_module.S_ISDIR(mode) if mode is not None else False


def _remote_is_file(sftp: Any, path: str) -> bool:
    """Return whether a remote path is a regular file."""
    attrs = sftp.stat(path)
    mode = getattr(attrs, "st_mode", None)
    return stat_module.S_ISREG(mode) if mode is not None else True


def _remote_explorer_root_directory(session: Any) -> str:
    """Return a configured remote root, falling back to a sensible SSH default."""
    return _remote_path_clean(_explorer_root_directory(session) or getattr(session, "directory", "") or "/")


def _remote_default_explorer_candidate_path(sftp: Any, session: Any, root_path: str) -> str:
    """Return the default remote explorer directory when no path is requested."""
    current_raw = _remote_path_clean(getattr(session, "directory", "") or "")
    if not current_raw:
        return root_path

    try:
        candidate = sftp.normalize(current_raw)
        if _remote_is_directory(sftp, candidate) and _remote_path_inside(root_path, candidate):
            return candidate
    except OSError:
        pass
    return root_path


def _resolve_remote_explorer_candidate_path(
    sftp: Any,
    session: Any,
    requested_path: Any = "",
    *,
    allow_empty_root: bool = True,
) -> Tuple[str, str]:
    """Resolve an SSH explorer path while keeping it inside the remote root."""
    if not _is_remote_explorer_session(session):
        raise ValueError("Session is not an SSH file explorer pane")

    root_raw = _remote_explorer_root_directory(session)
    if not root_raw:
        raise ValueError("Explorer root directory is not configured")

    root_path = sftp.normalize(root_raw)
    if not _remote_is_directory(sftp, root_path):
        raise ValueError("Explorer root directory does not exist")

    if requested_path is None:
        if not allow_empty_root:
            raise ValueError("Explorer file path is required")
        candidate = _remote_default_explorer_candidate_path(sftp, session, root_path)
    else:
        raw_path = _remote_path_clean(requested_path)
        if not raw_path:
            if not allow_empty_root:
                raise ValueError("Explorer file path is required")
            candidate_raw = root_path
        elif _remote_path_is_absolute(raw_path):
            candidate_raw = raw_path
        else:
            candidate_raw = _remote_path_join(root_path, raw_path)
        candidate = sftp.normalize(candidate_raw)

    if not _remote_path_inside(root_path, candidate):
        raise ValueError("Explorer path must stay inside the configured root")

    return root_path, candidate


def _resolve_remote_explorer_paths(sftp: Any, session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve a remote explorer directory path inside the configured root."""
    root_path, candidate = _resolve_remote_explorer_candidate_path(sftp, session, requested_path)
    if not _remote_is_directory(sftp, candidate):
        raise ValueError("Explorer path is not a directory")
    return root_path, candidate


def _resolve_remote_explorer_file_path(sftp: Any, session: Any, requested_path: Any = "") -> Tuple[str, str]:
    """Resolve a remote explorer file path inside the configured root."""
    root_path, candidate = _resolve_remote_explorer_candidate_path(
        sftp,
        session,
        requested_path,
        allow_empty_root=False,
    )
    if _remote_is_directory(sftp, candidate):
        raise ValueError("Explorer path is a directory")
    if not _remote_is_file(sftp, candidate):
        raise ValueError("Explorer path is not a file")
    return root_path, candidate
