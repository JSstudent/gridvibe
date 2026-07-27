"""Repository-wide explorer search (docs/r&d/explorer_repository_search_proposal.md).

A bounded, read-only "where does this string appear under this explorer root"
search. `git grep` is the primary engine on both backends (one subprocess /
one SSH round trip, .gitignore-aware, binary-skipping); a Python walk (local)
or a bounded `grep -rIn` (remote) covers roots outside a Git work tree.

Everything except `run_explorer_search()` and the backend `search_lines()`
hooks is a pure function, unit-testable without a route, a repo, or SSH.
Match ranges and snippet windowing are computed here, server-side, so the
frontend only renders — literal/regex/case semantics never drift between the
engine and the highlight.
"""

import fnmatch
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from web.config import runtime_config
from web.explorer import (
    ExplorerRouteError,
    _decode_git_output,
    _explorer_content_looks_binary,
)

SEARCH_QUERY_MAX_CHARS = 512
SEARCH_LINE_WINDOW_CHARS = 400
# Cap on a remote `grep -r` stdout so one command can never stream an
# unbounded result set back over SSH.
SEARCH_REMOTE_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
# Directories neither fallback engine descends into. `git grep` gets this for
# free from `.gitignore`; the walk and remote-grep engines do not, and without
# the deny-list a single `node_modules/` or `.venv/` burns the whole deadline.
SEARCH_EXCLUDE_DIRS = (
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
)


class SearchDeadlineExceeded(Exception):
    """Internal signal: an engine stopped because the search deadline passed."""


@dataclass(frozen=True)
class SearchOptions:
    query: str
    scope: str = ""  # rel path under the explorer root
    case_sensitive: bool = False
    whole_word: bool = False
    regex: bool = False
    include: Tuple[str, ...] = ()
    include_ignored: bool = False


@dataclass(frozen=True)
class SearchLimits:
    max_files: int = 2000
    max_matches: int = 5000
    max_matches_per_file: int = 200
    max_file_bytes: int = 2 * 1024 * 1024
    timeout_seconds: float = 8.0


# One raw engine match: explorer-root-relative path, 1-based line number, and
# the raw line bytes (no trailing newline; may carry a trailing CR).
RawMatch = Tuple[str, int, bytes]


def _flag(args: Any, name: str) -> bool:
    value = args.get(name, "0")
    return str(value).strip().lower() in {"1", "true"}


def parse_search_options(args: Any) -> SearchOptions:
    """Validate the query-string mapping into SearchOptions (400 on bad input)."""
    query = args.get("q", "")
    if not isinstance(query, str):
        query = str(query)
    if not query:
        raise ExplorerRouteError("A search query is required")
    if len(query) > SEARCH_QUERY_MAX_CHARS:
        raise ExplorerRouteError(
            f"Search query exceeds the {SEARCH_QUERY_MAX_CHARS}-character limit"
        )
    scope = args.get("scope", "") or ""
    if not isinstance(scope, str):
        scope = str(scope)
    include_raw: List[str] = []
    getlist = getattr(args, "getlist", None)
    if callable(getlist):
        include_raw = [str(value) for value in getlist("include")]
    else:
        single = args.get("include")
        if single:
            include_raw = [str(single)]
    include = tuple(value.strip() for value in include_raw if value.strip())
    return SearchOptions(
        query=query,
        scope=scope.strip().strip("/"),
        case_sensitive=_flag(args, "case"),
        whole_word=_flag(args, "word"),
        regex=_flag(args, "regex"),
        include=include,
        include_ignored=_flag(args, "ignored"),
    )


def search_limits_from_config() -> SearchLimits:
    """Read the bounded-search limits from RuntimeConfig."""
    return SearchLimits(
        max_files=int(runtime_config.explorer_search_max_files),
        max_matches=int(runtime_config.explorer_search_max_matches),
        max_matches_per_file=int(runtime_config.explorer_search_max_matches_per_file),
        max_file_bytes=int(runtime_config.explorer_search_max_file_bytes),
        timeout_seconds=float(runtime_config.explorer_search_timeout_seconds),
    )


def compile_search_matcher(options: SearchOptions) -> "re.Pattern[str]":
    """Compile the line matcher shared by range computation and the walk engine."""
    source = options.query if options.regex else re.escape(options.query)
    if options.whole_word:
        source = rf"\b(?:{source})\b"
    flags = 0 if options.case_sensitive else re.IGNORECASE
    try:
        return re.compile(source, flags)
    except re.error as exc:
        raise ExplorerRouteError(f"Invalid regular expression: {exc}") from exc


def include_match(rel_path: str, include: Tuple[str, ...]) -> bool:
    """Match one explorer-root-relative path against the include globs."""
    if not include:
        return True
    posix_path = rel_path.replace(os.sep, "/")
    name = posix_path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(posix_path, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in include
    )


def build_git_grep_args(options: SearchOptions, pathspec: str) -> List[str]:
    """Build the `git grep` argv for one bounded search.

    `-z -n` makes records `path NUL lineno NUL content LF` so paths containing
    `:` stay parseable; `-I` skips binaries; `--untracked` keeps ignored files
    out while still finding new files; `--full-name` pins result paths to the
    repository root.
    """
    args = ["grep", "-I", "-z", "-n", "--untracked", "--full-name"]
    args.append("-E" if options.regex else "-F")
    if not options.case_sensitive:
        args.append("-i")
    if options.whole_word:
        args.append("-w")
    args += ["-e", options.query, "--", pathspec]
    return args


def parse_git_grep_z(stream: bytes) -> Iterator[Tuple[str, int, bytes]]:
    """Parse `git grep -z -n` output into (repo-relative path, line, bytes).

    Never raises: undecodable path bytes decode with replacement and malformed
    records are skipped. Pinned by a byte-level fixture test.
    """
    if not stream:
        return
    for record in stream.split(b"\n"):
        if not record:
            continue
        parts = record.split(b"\0", 2)
        if len(parts) != 3:
            continue
        try:
            line_number = int(parts[1])
        except ValueError:
            continue
        path = parts[0].decode("utf-8", errors="replace")
        yield path, line_number, parts[2]


def _git_work_tree_root(backend: Any, cwd: str) -> Optional[str]:
    """Return the canonical repository root enclosing cwd, or None."""
    try:
        rev_parse = backend.run_git(
            ["rev-parse", "--show-toplevel", "--is-inside-work-tree"],
            cwd=cwd,
            timeout=2.0,
        )
    except Exception:
        return None
    if rev_parse.returncode != 0:
        return None
    lines = _decode_git_output(rev_parse.stdout).splitlines()
    if len(lines) < 2 or lines[1].lower() != "true":
        return None
    return backend.canonical_repo_root(lines[0])


def git_grep_matches(
    backend: Any,
    root_path: str,
    scope_path: str,
    options: SearchOptions,
    timeout: float,
) -> Optional[Iterator[RawMatch]]:
    """Run `git grep` for one search, or return None outside a work tree.

    The repository root may be an ancestor of the explorer root, so every
    repo-relative result is converted back to an absolute path, re-checked
    against the explorer root, and only then yielded — a pane rooted at `web/`
    must never see hits from `sessions/`.
    """
    repo_root = _git_work_tree_root(backend, scope_path)
    if repo_root is None:
        return None
    args = build_git_grep_args(options, backend.pathspec(repo_root, scope_path))

    def generate() -> Iterator[RawMatch]:
        try:
            result = backend.run_git(args, cwd=repo_root, timeout=timeout)
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            raise SearchDeadlineExceeded() from exc
        if result.returncode not in (0, 1):
            # 1 means "no matches"; anything else is a real grep failure.
            raise ExplorerRouteError(
                _decode_git_output(result.stderr) or "Repository search failed"
            )
        for repo_path, line_number, line_bytes in parse_git_grep_z(result.stdout):
            abs_path = backend.repo_abs_path(repo_root, repo_path)
            if not backend.path_inside_root(root_path, abs_path):
                continue
            yield backend.rel_explorer_path(root_path, abs_path), line_number, line_bytes

    return generate()


def walk_matches(
    root_path: str,
    scope_path: str,
    options: SearchOptions,
    limits: SearchLimits,
    deadline: float,
) -> Iterator[RawMatch]:
    """Local fallback engine: an os.scandir-style walk + line matcher.

    Honours the explorer's confinement and preview rules: no symlink escapes,
    SEARCH_EXCLUDE_DIRS pruned, binary and oversized files skipped, deadline
    enforced.
    """
    matcher = compile_search_matcher(options)
    # Decoding and matching every line of every file is what makes this engine
    # hit the deadline on log-heavy trees, and almost every file holds no match
    # at all. A literal query can be prefiltered against the whole decoded file
    # in one C-level scan: a literal cannot contain a newline or anchor to one,
    # so "absent from the file" implies "absent from every line" — and \b sees
    # the same non-word neighbour ("\n"/"\r") either way. Regex queries keep the
    # per-line path, where ^/$/\A stay per line. A false positive is harmless;
    # it just falls through to the loop below.
    prefilter = None if options.regex else matcher
    root_real = os.path.realpath(os.path.abspath(root_path))

    def inside_root(candidate: str) -> bool:
        try:
            common = os.path.commonpath([root_real, candidate])
        except ValueError:
            return False
        return os.path.normcase(common) == os.path.normcase(root_real)

    for dirpath, dirnames, filenames in os.walk(scope_path, followlinks=False):
        if time.monotonic() > deadline:
            raise SearchDeadlineExceeded()
        kept = []
        for dirname in dirnames:
            if dirname in SEARCH_EXCLUDE_DIRS:
                continue
            full = os.path.join(dirpath, dirname)
            if os.path.islink(full) and not inside_root(os.path.realpath(full)):
                continue
            kept.append(dirname)
        dirnames[:] = kept
        for filename in filenames:
            if time.monotonic() > deadline:
                raise SearchDeadlineExceeded()
            full = os.path.join(dirpath, filename)
            try:
                real = os.path.realpath(full)
                if not inside_root(real):
                    continue
                if os.path.getsize(real) > limits.max_file_bytes:
                    continue
                with open(real, "rb") as file_handle:
                    data = file_handle.read()
            except OSError:
                continue
            if _explorer_content_looks_binary(data):
                continue
            rel_path = os.path.relpath(real, root_real).replace(os.sep, "/")
            if not include_match(rel_path, options.include):
                continue
            if prefilter is not None and not prefilter.search(
                data.decode("utf-8", errors="replace")
            ):
                continue
            for line_number, line_bytes in enumerate(data.split(b"\n"), 1):
                text = line_bytes.rstrip(b"\r").decode("utf-8", errors="replace")
                if matcher.search(text):
                    yield rel_path, line_number, line_bytes


def build_remote_grep_command(scope_path: str, options: SearchOptions) -> str:
    """Build the bounded `grep -rIn` fallback command for a remote POSIX shell."""
    parts = ["grep", "-rInI"]
    for dirname in SEARCH_EXCLUDE_DIRS:
        parts.append(f"--exclude-dir={dirname}")
    parts.append("-E" if options.regex else "-F")
    if not options.case_sensitive:
        parts.append("-i")
    if options.whole_word:
        parts.append("-w")
    parts += ["-e", options.query, "--", scope_path]
    quoted = " ".join(shlex.quote(part) for part in parts)
    return f"{quoted} 2>/dev/null | head -c {SEARCH_REMOTE_MAX_OUTPUT_BYTES}"


def parse_remote_grep_output(
    backend: Any,
    root_path: str,
    stream: bytes,
) -> Iterator[RawMatch]:
    """Parse `grep -rIn` lines (`abspath:lineno:text`) with root confinement."""
    if not stream:
        return
    text = stream.decode("utf-8", errors="replace")
    lines = text.split("\n")
    # A head-truncated stream ends mid-line; the partial record is unusable.
    if len(stream) >= SEARCH_REMOTE_MAX_OUTPUT_BYTES and lines and not text.endswith("\n"):
        lines = lines[:-1]
    for line in lines:
        if not line:
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        try:
            line_number = int(parts[1])
        except ValueError:
            continue
        abs_path = parts[0]
        if not backend.path_inside_root(root_path, abs_path):
            continue
        rel_path = backend.rel_explorer_path(root_path, abs_path)
        yield rel_path, line_number, parts[2].encode("utf-8", errors="replace")


def window_line(text: str, ranges: List[List[int]]) -> Tuple[str, int, List[List[int]]]:
    """Window one long line around its first match and keep ranges meaningful."""
    if len(text) <= SEARCH_LINE_WINDOW_CHARS:
        return text, 0, ranges
    first = ranges[0][0] if ranges else 0
    half = SEARCH_LINE_WINDOW_CHARS // 2
    start = max(0, min(first - half, len(text) - SEARCH_LINE_WINDOW_CHARS))
    end = start + SEARCH_LINE_WINDOW_CHARS
    adjusted = [
        [max(span_start, start) - start, min(span_end, end) - start]
        for span_start, span_end in ranges
        if span_end > start and span_start < end
    ]
    return text[start:end], start, adjusted


def collect_search_payload(
    raw_matches: Iterator[RawMatch],
    options: SearchOptions,
    limits: SearchLimits,
    deadline: float,
    engine: str,
    started: float,
) -> Dict[str, Any]:
    """Group raw engine matches into the bounded response payload.

    Applies the include filter, the per-file/global match caps, the file cap
    and the deadline, decodes lines, computes match ranges server-side, and
    windows long lines — the frontend renders this verbatim.
    """
    matcher = compile_search_matcher(options)
    files: List[Dict[str, Any]] = []
    index_by_path: Dict[str, Dict[str, Any]] = {}
    total_matches = 0
    truncated = {"files": False, "matches": False, "deadline": False}

    iterator = iter(raw_matches)
    while True:
        try:
            rel_path, line_number, line_bytes = next(iterator)
        except StopIteration:
            break
        except SearchDeadlineExceeded:
            truncated["deadline"] = True
            break
        if time.monotonic() > deadline:
            truncated["deadline"] = True
            break
        rel_path = str(rel_path).replace(os.sep, "/")
        if not include_match(rel_path, options.include):
            continue
        entry = index_by_path.get(rel_path)
        if entry is None:
            if len(files) >= limits.max_files:
                truncated["files"] = True
                break
            entry = {
                "path": rel_path,
                "name": rel_path.rsplit("/", 1)[-1],
                "dir": rel_path.rsplit("/", 1)[0] if "/" in rel_path else "",
                "match_count": 0,
                "truncated": False,
                "matches": [],
            }
            index_by_path[rel_path] = entry
            files.append(entry)
        if total_matches >= limits.max_matches:
            truncated["matches"] = True
            break
        entry["match_count"] += 1
        total_matches += 1
        if len(entry["matches"]) >= limits.max_matches_per_file:
            entry["truncated"] = True
            continue
        text = line_bytes.rstrip(b"\r").decode("utf-8", errors="replace")
        ranges = [list(span) for span in (match.span() for match in matcher.finditer(text))]
        windowed_text, text_offset, windowed_ranges = window_line(text, ranges)
        entry["matches"].append(
            {
                "line": line_number,
                "text": windowed_text,
                "text_offset": text_offset,
                "line_length": len(text),
                "ranges": windowed_ranges,
            }
        )

    return {
        "query": options.query,
        "options": {
            "case": options.case_sensitive,
            "word": options.whole_word,
            "regex": options.regex,
            "scope": options.scope,
            "include": list(options.include),
            "ignored": options.include_ignored,
        },
        "engine": engine,
        "files": files,
        "total_files": len(files),
        "total_matches": total_matches,
        "truncated": truncated,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "error": None,
    }


def run_explorer_search(backend: Any, args: Any) -> Dict[str, Any]:
    """Route handler body: validate, pick an engine, collect the payload."""
    options = parse_search_options(args)
    limits = search_limits_from_config()
    started = time.monotonic()
    deadline = started + limits.timeout_seconds
    root_path, scope_path = backend.resolve_dir(options.scope or None)
    engine, raw_matches = backend.search_lines(
        options,
        root_path=root_path,
        scope_path=scope_path,
        limits=limits,
        deadline=deadline,
    )
    return collect_search_payload(raw_matches, options, limits, deadline, engine, started)
