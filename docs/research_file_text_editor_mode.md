# File Explorer Text Editor Mode Research

## Todo item / goal

Todo 5: file explorer should at least support opening and displaying files in a simple text editor mode, including research on possible Markdown and code formatting.

Goal: add a safe, simple, read-only file viewing mode to the existing explorer pane so users can click text files and inspect their contents inside GridVibe without leaving the session grid.

## Current repo observations

- Explorer startup already exists as a first-class local pane type. `web/api.py` normalizes `startup_mode: "explorer"` for WSL/local sessions, clears shell-related options, labels the host as `File Explorer`, marks explorer sessions connected, and skips PTY startup in `create_sessions` around `web/api.py:2937` and `web/api.py:3024`.
- Explorer sessions are identified by `_is_explorer_session(session)` in `web/api.py:2208`; it currently requires `mode == "wsl"` and `startup_mode == "explorer"`.
- Directory path validation is centralized in `_resolve_explorer_paths(session, requested_path)` at `web/api.py:2213`. It resolves the configured root, expands the requested path, checks `os.path.commonpath`, and rejects traversal outside the root. This is the right safety model to reuse for file reads, but it currently rejects non-directories.
- Existing explorer metadata is produced by `_explorer_entry_payload(root_path, entry)` at `web/api.py:2253`, returning name, relative path, type, size, and modified timestamp.
- The only explorer API endpoint today is `GET /api/explorer/<session_id>/entries` at `web/api.py:2686`. It lists directory contents, sorts directories before files, and returns `root`, `path`, `parent_path`, and `entries`.
- Tests already cover explorer launch, no PTY background task, entry listing, and path traversal rejection in `tests/test_api.py:1179`, `tests/test_api.py:1212`, and `tests/test_api.py:1244`.
- The terminal workspace template already renders explorer panes differently from xterm panes. `templates/terminals.html:2166` creates a lightweight explorer pane object, `templates/terminals.html:2347` renders `.explorer-surface`, and `templates/terminals.html:4262` loads entries through the explorer API.
- File rows are currently disabled in `templates/terminals.html:4309`; only directory rows receive click handlers at `templates/terminals.html:4322`.
- Existing explorer research in `docs/file_explorer_startup_mode.md:56` already anticipated `GET /api/explorer/<session_id>/file?path=...`, with read-only, size-limited, text-only previews and clear handling for binary files.

## Implementation possibilities

### 1. Plain escaped text viewer

Add a backend file-read endpoint and render returned text as escaped content in a pane-local `<pre><code>` or read-only `<textarea>`.

Tradeoffs:

- Safest and smallest change.
- No new Python or frontend dependencies.
- Works for Markdown source, code, configs, logs, and plain text.
- Formatting is limited to monospace text, whitespace preservation, optional line numbers, and optional language labels inferred from extension.
- No syntax highlighting or rendered Markdown in the first pass.

### 2. Lightweight code highlighting

Use a frontend highlighter such as Prism or highlight.js, preferably vendored locally instead of CDN-only, and render escaped code blocks with language classes.

Tradeoffs:

- Better code readability.
- Adds static assets and dependency/version maintenance.
- Browser-side highlighting can be slow for large files unless the backend cap remains strict.
- Language detection by extension will be imperfect.

### 3. Markdown preview toggle

For `.md` files, show raw source by default and optionally add a Preview tab using a Markdown parser plus HTML sanitizer.

Tradeoffs:

- Useful for documentation files.
- Must sanitize rendered HTML to avoid XSS from repository content.
- Server-side rendering would require adding dependencies such as Markdown plus Bleach, or client-side rendering would require vendored parser/sanitizer assets.
- Raw Markdown source is safer and still meets the minimum display requirement.

### 4. Full editor widget

Embed CodeMirror or Monaco in read-only mode.

Tradeoffs:

- Best long-term path if GridVibe later needs search, selection helpers, edits, multiple open tabs, dirty-state handling, or diagnostics.
- Larger implementation and asset footprint.
- Overkill for the current "at least support opening and displaying files" milestone.

## Recommended safest/simple approach

Implement option 1 first: a read-only, escaped, size-limited text viewer inside the existing explorer pane.

Use the name "text editor mode" in the UI behavior, but keep it read-only for the first milestone. Avoid Markdown HTML rendering and external editor widgets initially. Treat `.md` and code files the same: show source text with preserved whitespace, a compact file header, optional line numbers, file size/truncation metadata, and a Back button to return to the directory listing.

This approach matches the existing local-only explorer design, keeps the path validation model simple, and creates a stable endpoint that richer formatting can build on later.

## Implementation outline

1. Add a backend helper near `_resolve_explorer_paths`:
   - `_resolve_explorer_file_path(session, requested_path)` or a generalized resolver that accepts expected kind `directory`/`file`.
   - Reuse the same root resolution, `realpath`, `abspath`, `expanduser`, and `commonpath` containment check.
   - Reject empty paths, directories, missing paths, and paths outside root.
   - Use `os.stat(..., follow_symlinks=False)` or equivalent metadata handling consistently with `_explorer_entry_payload`.

2. Add `GET /api/explorer/<session_id>/file?path=...`:
   - Return `404` for missing sessions.
   - Return `400` for non-explorer sessions, directories, paths outside root, binary files, or files above the configured size cap.
   - Read only the first capped byte range, for example 256 KiB or 1 MiB.
   - Detect binary content conservatively, at minimum by rejecting NUL bytes in the sampled bytes.
   - Decode as UTF-8 first with clear fallback behavior. The simplest tolerant behavior is `bytes.decode("utf-8", errors="replace")` plus an `encoding` field of `utf-8`.
   - Return metadata: `session_id`, `root`, `path`, `name`, `size`, `modified`, `encoding`, `truncated`, and `content`.

3. Update the explorer UI in `templates/terminals.html`:
   - Remove `disabled` from file rows and make `.explorer-row.file` clickable.
   - Add `openExplorerFile(index, path)` that fetches `/api/explorer/<session_id>/file?path=...`.
   - Render a file view inside the existing `.explorer-list` area or introduce `.explorer-editor` under `.explorer-surface`.
   - Include a compact toolbar with Back, file name/path, size, and truncated indicator.
   - Render content with `textContent`, not `innerHTML`, or escape every line before assigning `innerHTML`.
   - Store the previous directory path on the pane object so Back returns to the current listing.

4. Add minimal formatting:
   - Monospace font, `white-space: pre`, horizontal scrolling, and line-height tuned for dense reading.
   - Optional line numbers by splitting escaped content into line rows. Keep this optional because very large line counts can increase DOM cost.
   - Extension-to-label mapping for common types: `.py`, `.js`, `.html`, `.css`, `.json`, `.md`, `.txt`, `.yml`, `.yaml`, `.toml`, `.ini`, `.sh`, `.ps1`.
   - For `.md`, show "Markdown source" in the header rather than rendering HTML.

5. Add focused tests:
   - API returns content for a UTF-8 text file inside root.
   - API rejects traversal outside root for file reads.
   - API rejects directories on the file endpoint.
   - API marks large capped reads as truncated, or rejects oversized files if using a hard cap.
   - API returns a clear response for binary/NUL-containing files.
   - Existing explorer entry tests continue to pass.

## Risks/tests to consider

- Path traversal and symlink behavior must stay inside the configured root. File resolution should use the same containment check as entries.
- Large files can freeze the UI if the backend returns too much content or the frontend renders too many line elements.
- Repository files are untrusted input. Render text as text, not HTML. Markdown preview must wait until sanitization is in place.
- Binary detection will never be perfect. Start conservative and return a clear "preview unavailable" response rather than trying to display everything.
- Encoding edge cases are common on Windows projects. UTF-8 with replacement is acceptable for a first read-only viewer, but the response should include the assumed encoding.
- Explorer panes do not have xterm instances. Any refresh, resize, Socket.IO replay, drag/drop, or status code touched by the UI change should continue to branch on `isExplorerSession(session)`.
