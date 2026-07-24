# In-App Explorer Text Editor — Implementation Proposal

Last updated: 2026-07-24

> **Status: implemented (2026-07-24).** The feature described below is built and
> covered by tests. See [§9 Implementation notes](#9-implementation-notes-what-was-built)
> and [§10 Edge-case coverage](#10-edge-case-coverage) for what shipped, where it
> lives, and how each edge case is handled.

## 1. Decision and outcome

GridVibe will add a bounded in-app editor to the existing Explorer **Source**
view. A user can open an existing text file, choose **Edit**, change its full
contents in a `<textarea>`, and choose **Save** or **Cancel**.

The feature covers both Explorer backends:

- local/WSL Explorer panes through `_LocalExplorerBackend`;
- remote SSH Explorer panes through `_SftpExplorerBackend`.

It works in browser and native-window modes because all reads and writes use the
existing Flask API and Explorer backend abstraction. No desktop bridge or
external process is involved.

The v1 acceptance criteria are:

1. Only an existing, fully loaded, supported UTF-8 text file can enter edit
   mode.
2. The server confines every write to the live Explorer session root.
3. The server refuses oversized, truncated, binary, invalid UTF-8, and
   mixed-line-ending files.
4. A stale editor cannot silently overwrite a file changed after it was read.
5. A successful save replaces the file atomically, preserves its UTF-8 BOM,
   line-ending style, and permission bits, and refreshes Source, Preview, Diff,
   and Git state.
6. Failed saves retain the user's buffer and provide an immediate retry path.
7. Explicit navigation or close actions cannot discard a dirty buffer without
   an in-page confirmation.

This proposal intentionally does not add file creation, rename, delete, upload,
draft recovery, autosave, or a rich-editor dependency.

---

## 2. Current integration points

### 2.1 Backend

`web/explorer.py` already owns the shared local/SFTP abstraction:

| Contract | Local backend | Remote backend |
| --- | --- | --- |
| resolve a root-confined file | `resolve_file()` | `resolve_file()` |
| inspect file metadata | `stat_file()` | `stat_file()` |
| bounded byte reads | `read_file_prefix()` / `read_file_suffix()` | same contract over SFTP |
| Git status after a change | shared `_get_git_context()` helpers | same helpers over SSH |

`GET /api/explorer/<session_id>/file` in `web/api.py` currently:

1. resolves the requested path through the backend;
2. stats it;
3. handles images separately;
4. applies `_explorer_editor_language()`;
5. reads at most `EXPLORER_FILE_PREVIEW_MAX_BYTES` (10 MiB);
6. rejects binary-looking content;
7. returns content, preview metadata, Markdown HTML, and Git metadata.

`_explorer_route_response()` owns the backend context and maps path/validation
errors to `400` and backend I/O errors to `500`. The save route should stay a
thin route beside the existing read route; validation, encoding, revision, and
write behavior belong in `web/explorer.py`.

The app-level cross-origin write guard in `web/app.py` already applies to a new
`PUT` route. It must not be bypassed or weakened.

### 2.2 Frontend

Explorer rendering has been extracted from `terminals.js`. The relevant current
surface is `web/static/js/explorer-viewer.js`:

- `openExplorerFile()` fetches the file payload;
- `renderExplorerFile()` stores active-file state and builds the header/body;
- `renderExplorerSource()` renders highlighted, line-numbered Source markup;
- `setExplorerFileView()` controls Source/Preview/Diff;
- `activateExplorerTab()` and `closeExplorerTab()` manage the tab strip;
- `updateExplorerFileInPlace()` refreshes an already rendered file;
- `refreshExplorerPane()` refreshes the file plus open sidebars.

The shared in-page confirmation modal and toast live in `terminals.js` as
`openGenericConfirmModal()` and `showTerminalToast()`. The scripts are classic
scripts loaded in a fixed order, so an extracted Explorer editor module can use
those functions at interaction time.

The editor is substantial enough to remain out of `terminals.js`. Add
`web/static/js/explorer-editor.js`, load it after `explorer-viewer.js` and before
`terminals.js` in `templates/terminals.html`, and keep only small render and
navigation hooks in the existing files.

---

## 3. API contract

### 3.1 Extend `GET /api/explorer/<session_id>/file`

Keep every existing response field. Add these fields to text-file responses:

```jsonc
{
  "editable": true,
  "edit_block_reason": null,
  "revision": "sha256:7f83b165...",
  "line_ending": "crlf",
  "utf8_bom": false
}
```

Definitions:

- `editable`: authoritative server capability; the UI must not infer it only
  from the extension.
- `edit_block_reason`: `null`, `"truncated"`, `"mixed_line_endings"`, or
  `"unsupported_format"` for the dedicated image response.
- `revision`: SHA-256 of the exact on-disk bytes, prefixed with `sha256:`. It is
  present only when the server loaded the complete file.
- `line_ending`: `"lf"`, `"crlf"`, `"cr"`, `"none"`, or `"mixed"`.
- `utf8_bom`: whether the exact bytes begin with a UTF-8 BOM.

Image responses set `editable: false`, `edit_block_reason:
"unsupported_format"`, and `revision: null`. Unsupported non-image formats
continue to be rejected by `_explorer_editor_language()` as they are today.

For a complete text file, decode with strict UTF-8 after removing an optional
UTF-8 BOM. Invalid UTF-8 anywhere in the file is rejected. Truncated previews
retain the current replacement-decoding behavior because a byte cap can split a
valid final character, but they are never editable.

Mixed line endings are viewable but not editable in v1. Refusing them avoids a
save that silently normalizes most of the file and produces a misleading
whole-file diff.

### 3.2 Add `PUT /api/explorer/<session_id>/file`

Request:

```json
{
  "path": "relative/path/to/file.py",
  "content": "complete textarea contents",
  "base_revision": "sha256:7f83b165..."
}
```

Validation:

- the JSON body must be an object;
- `path` must be a non-empty string;
- `content` must be a string;
- `base_revision` must be a non-empty string;
- the resolved path must be an existing regular file inside the session root;
- the extension/filename must pass `_explorer_editor_language()`;
- the current file and encoded replacement must each be at most
  `EXPLORER_FILE_PREVIEW_MAX_BYTES`;
- the current file must be complete, non-binary, strict UTF-8, and not use mixed
  line endings;
- the current exact-byte revision must match `base_revision`.

The request always represents a full-file replacement. A missing revision is
not an overwrite escape hatch and returns `400`.

Success returns `200` with the same complete payload as the extended GET route,
including regenerated Markdown preview HTML, fresh Git metadata, and the new
revision. Returning the canonical read payload avoids a second client request
and keeps the post-save renderer on one contract.

### 3.3 Error contract

Introduce a small structured route exception in `web/explorer.py`:

```python
class ExplorerRouteError(ValueError):
    status_code = 400
    code = "invalid_request"

    def __init__(self, message: str, **details: Any):
        super().__init__(message)
        self.details = details
```

Specific subclasses:

| Error | HTTP | `code` | Extra response data |
| --- | ---: | --- | --- |
| stale revision | 409 | `file_conflict` | `current_revision` |
| another GridVibe save for the same path is active | 409 | `save_in_progress` | none |
| current or replacement content exceeds 10 MiB | 413 | `file_too_large` | `max_bytes` |
| all other validation/path errors | 400 | `invalid_request` | none |
| local/SFTP I/O failure | 500 | `io_error` | none |

`_explorer_route_response()` catches `ExplorerRouteError` before `ValueError`
and returns:

```json
{
  "error": "File changed on disk since it was opened",
  "code": "file_conflict",
  "current_revision": "sha256:..."
}
```

The frontend branches on `code`, never on human-readable error text.

### 3.4 Conflict flow

The first save always sends the revision returned by GET.

If it receives `file_conflict`, the editor remains open and shows:

- **Reload from disk** — confirm discarding the local buffer, then re-run GET;
- **Overwrite current version** — confirm the overwrite, then retry the same
  buffer using the conflict response's `current_revision`.

The retry still performs a revision check. If the file changes again between
the conflict response and retry, the server returns another `409`. There is no
unconditional `force` flag.

This is optimistic concurrency, not an operating-system compare-and-swap.
External software can still change a file in the narrow interval between the
final revision check and replacement. GridVibe should also prevent two of its
own saves from racing by claiming `(session_id, resolved_path)` in a short-held
module-level set:

1. acquire the claim-set lock;
2. fail with `save_in_progress` if the key exists, otherwise add it;
3. release the lock before any file or network I/O;
4. perform validation and replacement;
5. remove the claim in `finally`, again under the short-held lock.

No filesystem, SFTP, Git, or emit operation runs while that lock is held.

---

## 4. Backend implementation

### 4.1 Shared helpers in `web/explorer.py`

Add:

- `_explorer_file_revision(raw_content: bytes) -> str`;
- `_explorer_line_ending(raw_content: bytes) -> str`;
- `_decode_explorer_text(raw_content: bytes, *, truncated: bool)`;
- `_encode_explorer_edit(content: str, *, line_ending: str, utf8_bom: bool)`;
- `get_explorer_file_payload(backend, requested_path) -> Dict[str, Any]`;
- `save_explorer_file_payload(backend, requested_path, content,
  base_revision, *, session_id) -> Dict[str, Any]`;
- the structured route-error classes and the short-held write-claim helper.

Extract the current GET handler body into `get_explorer_file_payload()` rather
than duplicating file/Markdown/Git response construction. The existing GET route
then becomes session lookup plus one helper call. The PUT route validates JSON
types and calls `save_explorer_file_payload()`.

The save helper performs these operations in order:

1. resolve the file with `backend.resolve_file()`;
2. claim the session/path for this GridVibe save;
3. apply the filename/language gate;
4. stat and fully read the current file with the existing bounded read helper;
5. reject truncation, binary content, invalid UTF-8, and mixed line endings;
6. compute the exact current revision and compare it with `base_revision`;
7. normalize browser textarea line breaks to `\n`;
8. re-encode using the current file's line-ending style and BOM;
9. reject the replacement if its encoded byte length exceeds 10 MiB;
10. return immediately without writing if the exact bytes are unchanged;
11. call `backend.replace_file()` and then build a fresh GET payload;
12. release the write claim in `finally`.

The server derives BOM and line-ending behavior from the current on-disk bytes,
not from client-supplied metadata. Strip one leading `U+FEFF` from the submitted
textarea value before reapplying the file's original BOM so it cannot be
duplicated. When the original file contains no newline (`line_ending: "none"`),
new line breaks entered in the editor are written as LF.

Do not add an app-config key for the edit limit. Reusing
`EXPLORER_FILE_PREVIEW_MAX_BYTES` keeps the invariant simple: an editable file
is exactly one whose complete contents fit through the existing read contract.

### 4.2 Backend method

Add the same method to both backend classes:

```python
def replace_file(self, file_path: str, content_bytes: bytes) -> None:
    """Atomically replace an existing file with complete encoded contents."""
```

`_LocalExplorerBackend.replace_file()`:

1. stat the original without following a new path supplied by the client;
2. create a uniquely named sibling temp file with `tempfile.mkstemp()`;
3. write all bytes, flush, and `os.fsync()` the temp file;
4. apply the original permission bits with `os.fchmod()` where supported;
5. call `os.replace(temp_path, file_path)`;
6. remove the temp file in `finally` if replacement did not consume it.

Creating the temp file in the target directory keeps replacement on the same
filesystem. Never use a predictable `.gvtmp` name.

`_SftpExplorerBackend.replace_file()`:

1. stat the original and retain its permission bits;
2. create a unique sibling temp path;
3. open it with SFTP `"xb"` when supported, falling back to a UUID name plus
   `"wb"` only when exclusive-create mode is unavailable;
4. write and flush the complete byte buffer;
5. apply original permission bits with `sftp.chmod()` when available;
6. replace with `sftp.posix_rename(temp_path, file_path)`;
7. remove the temp path in `finally` on failure.

Do not fall back to opening the destination with `"wb"`: that can truncate the
original before a failed upload completes. If the SFTP server does not support
the OpenSSH POSIX-rename extension, return a clear I/O error and leave the
original untouched. Supporting non-atomic SFTP servers can be evaluated
separately.

Atomic replacement protects file bytes from partial-save corruption. As with
most atomic-save editors, replacement may not preserve extended attributes,
ACLs, ownership, or hard-link identity; v1 explicitly preserves ordinary mode
bits only.

### 4.3 Read-only contract update

This task is the explicit exception allowed by the project instructions. In the
implementation change:

- update the module and backend docstrings in `web/explorer.py`;
- update the Explorer rules in `AGENTS.md` and `CLAUDE.md`;
- update the Explorer description in `README.md`;
- add the user-visible feature to `CHANGELOG.md`.

The revised guarantee should be precise: Explorer filesystem writes are allowed
only through the root-confined, size/format/encoding/revision-guarded file-save
route; all other direct move/delete/upload/create operations remain out of
scope. Existing guarded Git mutations remain unchanged.

---

## 5. Frontend implementation

### 5.1 Module and state model

Add `web/static/js/explorer-editor.js`. It owns edit state, textarea behavior,
save requests, conflict UI, and discard guards.

Each Explorer pane has at most one active edit:

```javascript
pane._explorerEdit = {
    tabId,
    path,
    originalContent, // newline-normalized textarea baseline
    draft,
    baseRevision,
    conflictRevision: '',
    dirty: false,
    saving: false
};
```

Editing is deliberately not stored on the tab record and is never serialized
into saved sessions or `runtime_state.json`. Navigation must finish or discard
the active edit before another tab can render.

`renderExplorerFile()` and `updateExplorerFileInPlace()` additionally retain:

```javascript
pane._explorerFileEditable
pane._explorerFileEditBlockReason
pane._explorerFileRevision
pane._explorerFileLineEnding
pane._explorerFileUtf8Bom
pane._explorerFileTruncated
```

Loading a different file clears edit state only after the discard guard has
allowed the navigation.

### 5.2 Header controls

Add a compact editor action group before Download:

- **Edit** in normal Source mode;
- **Save** and **Cancel** in edit mode.

Define any new icons in `web/static/js/terminal-icons.js` as stroke-style
`currentColor` SVG. Use existing button classes/tokens where possible; add only
editor-specific layout/state classes to `web/static/css/terminals.css`.

For a text file with `editable: false`, keep Edit visible but disabled with a
specific tooltip:

- truncated: `File exceeds the 10 MiB in-place edit limit`;
- mixed line endings: `Mixed line endings are view-only in this version`.

Images keep their dedicated viewer and do not render editor controls.

Entering edit mode:

1. switch to Source and close the Diff split;
2. normalize `pane._explorerFileContent` from CRLF/CR to LF, store that value as
   both `originalContent` and `draft`, and seed the textarea from it (otherwise
   the browser's textarea newline normalization would make a CRLF file appear
   dirty immediately);
3. replace the rendered Source lines with a full-height textarea;
4. set `spellcheck="false"`, `wrap="off"`, and an accessible label;
5. disable Preview/Diff tabs, search, Download, and Markdown appearance;
6. leave zoom enabled and apply the existing per-tab editor font-size variable;
7. focus the textarea without changing its initial selection unexpectedly.

The control is a `<textarea>` in v1. Syntax highlighting and fold controls are
available again immediately after Save or Cancel.

### 5.3 Editing behavior

On every `input` event:

- copy `textarea.value` into `draft`;
- set `dirty = draft !== originalContent`;
- enable Save only when dirty and not saving;
- mark the active tab with the existing token palette (for example a small
  `aria-label`/CSS dirty indicator, not an emoji).

Keyboard behavior:

- `Tab` inserts `\t` at the current selection and preserves selection/caret;
- `Ctrl+S` / `Cmd+S` prevents the browser action and invokes Save;
- `Escape` invokes Cancel and confirms only when dirty.

Save busy state disables Save/Cancel and toggles an `.is-busy` class. It must not
rewrite button markup. Request failure clears busy state, keeps the textarea and
draft intact, shows an inline `role="alert"` message, and leaves Save as the
retry affordance.

### 5.4 Save success

On `200`:

1. clear `pane._explorerEdit`;
2. update the pane from the returned canonical file payload;
3. call `updateExplorerFileInPlace()` when the available Preview/Diff panels did
   not change;
4. otherwise call `renderExplorerFile()` for the same active tab (a clean file
   commonly gains a Diff panel after its first edit);
5. invalidate the diff cache;
6. invalidate/reload the Git sidebar if open;
7. reload the Files tree if open so status badges update;
8. show `Saved <filename>` with `showTerminalToast()`.

Markdown Preview HTML must come from the save response; never render unsanitized
Markdown directly in the browser.

### 5.5 Conflict UI

On `code === "file_conflict"`, render an inline conflict bar in the editor
without losing focusable access to the textarea:

- **Reload from disk** uses `openGenericConfirmModal()` before discarding;
- **Overwrite current version** uses the same modal with explicit overwrite
  wording, then retries with `current_revision`;
- dismissing the bar leaves the buffer and normal Save retry available.

On `save_in_progress`, keep the editor open and show a short `Save again` retry
message. On `file_too_large`, keep the buffer but explain that it no longer fits
the server limit; Download remains disabled during edit because it would
download the old disk version.

### 5.6 Unsaved-work guard

Add:

```javascript
function hasDirtyExplorerEdit(index) { ... }
async function confirmDiscardExplorerEdit(index, actionLabel) { ... }
function hasAnyDirtyExplorerEdit() { ... }
```

`confirmDiscardExplorerEdit()` returns immediately when clean. When dirty it
uses `openGenericConfirmModal()` and clears edit state only after confirmation.
No `window.confirm`, `window.alert`, or `window.prompt` is allowed.

Call the guard before an action mutates tab/path state or replaces the viewer:

- `activateExplorerTab()`;
- `closeExplorerTab()` for the active edited tab;
- `openExplorerFile()` when the target differs or a refresh would replace the
  editor;
- `loadExplorerPane()` and breadcrumb/tree directory navigation;
- `refreshExplorerPane()`;
- `closeTerminalPane()` in `terminals.js`;
- switching or closing a session group when its rendered/cached panes contain a
  dirty edit.

Convert the affected click handlers to await these asynchronous decisions before
changing `_explorerActiveTabId`, removing a tab, rendering a loading state, or
calling a DELETE endpoint.

Install one `beforeunload` listener that calls `preventDefault()` and sets
`returnValue` when any Explorer edit is dirty. This supplies the browser's
standard page-close warning; an in-page modal cannot run during unload.

v1 has no persisted draft. An OS process kill, browser crash, or forced shutdown
before Save loses the in-memory buffer. That limitation must be stated in the
release note rather than implied to be recoverable.

---

## 6. Styling and accessibility

Add editor rules to `web/static/css/terminals.css`:

- `.explorer-source-editor` fills the Source panel, uses the existing editor
  font variable, has no soft wrap, and scrolls in both directions;
- edit controls and conflict/error bars use existing `tokens.css` variables;
- `.is-dirty`, `.is-busy`, success, warning, and error states use token colors
  only;
- keyboard focus remains visible in light and dark themes;
- narrow panes wrap or collapse the action group without covering tabs/search.

Accessibility requirements:

- toolbar buttons have visible labels or explicit `aria-label` and `title`;
- disabled Edit explains why through its title and associated text where
  practical;
- transient success uses the existing polite toast;
- save failures use `role="alert"`;
- the conflict bar is keyboard reachable;
- focus returns to Edit after Save/Cancel and to the textarea after a dismissed
  conflict action.

---

## 7. Tests

### 7.1 Backend and route tests (`tests/test_api.py`)

Add focused coverage for:

- GET returns `editable`, exact-byte SHA-256 revision, line-ending kind, and BOM
  metadata for a complete local file;
- GET reports truncated and mixed-line-ending files as non-editable;
- complete invalid UTF-8 outside the binary sample is rejected;
- local PUT round-trips UTF-8 text and returns the refreshed canonical payload;
- CRLF, CR, LF, final-newline state, and UTF-8 BOM are preserved;
- unchanged content is a successful no-op;
- missing/wrong JSON field types return `400`;
- traversal, directories, missing files, and unsupported extensions return
  `400`;
- current/replacement size overflow returns `413`;
- stale revision returns structured `409` plus `current_revision`;
- retry with the returned revision succeeds, while a second intervening change
  conflicts again;
- simultaneous GridVibe claims for one session/path return `save_in_progress`
  without holding the claim lock during I/O;
- a simulated local write/replace failure leaves the original bytes intact and
  cleans the temp file;
- local replacement preserves permission bits where the platform exposes them;
- mocked SFTP writes to a unique temp, applies mode, calls `posix_rename`, and
  cleans up on failure;
- unsupported remote POSIX rename never opens the destination with `"wb"`;
- a remote save returns the same payload shape and refreshed Git metadata;
- the cross-origin guard rejects an untrusted-origin PUT while same-origin PUT
  reaches the route.

### 7.2 Frontend contract tests (`tests/test_api.py`)

Extend the existing static-source tests to verify:

- `explorer-editor.js` is served and loaded after `explorer-viewer.js` but before
  `terminals.js`;
- editor state is not included in saved-session/runtime snapshot payloads;
- Edit/Save/Cancel wiring, textarea attributes, Tab insertion, and
  `Ctrl/Cmd+S` handling are present;
- Save sends `path`, `content`, and `base_revision`;
- conflict handling branches on `data.code` and retries with
  `current_revision`;
- dirty navigation uses `openGenericConfirmModal()`;
- close-pane, group-switch, group-close, refresh, and tab actions consult the
  discard guard;
- `beforeunload` checks all dirty Explorer panes;
- no frontend file uses `window.prompt`, `window.confirm`, or `window.alert`;
- new icons are stroke-style `currentColor` SVG;
- new CSS colors/radii come from tokens and busy state is class-based.

No `test_webview_launcher.py` changes are expected because the implementation
does not use the native bridge.

### 7.3 Manual verification

Exercise one local and one SSH Explorer:

1. edit and save a clean `.py` file;
2. verify Source, Markdown Preview where applicable, Diff, tree badges, and Git
   sidebar refresh;
3. stage/revert the saved change through the existing Git UI;
4. change the same file externally and verify Reload and Overwrite behavior;
5. simulate a save permission error and confirm the buffer remains retryable;
6. try a truncated file and a mixed-line-ending file;
7. attempt tab switch, refresh, pane close, group switch, and page reload with a
   dirty buffer;
8. repeat at narrow pane widths and in both light and dark themes.

Run the full project checks after implementation:

```text
python tests/run_tests.py
python -m ruff check .
```

---

## 8. Implementation sequence

1. **Read contract:** extract `get_explorer_file_payload()`, add strict complete
   UTF-8 decoding, editability metadata, line-ending detection, and SHA-256
   revisions.
2. **Write contract:** add structured route errors, the short-held save claim,
   both atomic backend methods, `save_explorer_file_payload()`, and the PUT
   route.
3. **Backend tests:** land local/remote, bounds, encoding, conflict, atomicity,
   cleanup, and origin-guard coverage before UI wiring.
4. **Frontend module:** add `explorer-editor.js`, load-order coverage, state,
   controls, textarea behavior, save/conflict flows, and sidebar refresh.
5. **Discard protection:** guard tab/path/refresh/pane/group teardown and add
   `beforeunload`.
6. **Polish:** token-based CSS, currentColor icons, accessibility, narrow-pane
   behavior, and manual local/SSH verification.
7. **Contract/docs:** update `AGENTS.md`, `CLAUDE.md`, `README.md`, and
   `CHANGELOG.md`, then run the full checks.

The implementation is complete only when local and SFTP saves share the same
route/helper path, failed writes demonstrably preserve the original file and
the in-memory buffer, and every deliberate teardown path protects dirty work.

---

## 9. Implementation notes (what was built)

Delivered 2026-07-24, following the sequence in §8. The design above was
implemented essentially as written; this section records where each piece lives
and the few concrete choices made along the way.

### 9.1 Backend — `web/explorer.py`

- **Read metadata.** `get_explorer_file_payload(backend, requested_path)` was
  extracted from the old inline GET handler and now also returns `editable`,
  `edit_block_reason`, `revision`, `line_ending`, and `utf8_bom`. Pure helpers:
  `_explorer_file_revision` (`sha256:` of exact on-disk bytes),
  `_explorer_line_ending` (`lf`/`crlf`/`cr`/`none`/`mixed`), and
  `_explorer_edit_metadata` (decides editability from the complete bytes).
- **Write.** `save_explorer_file_payload(backend, requested_path, content,
  base_revision, *, session_id)` performs the ordered validation → revision
  check → `replace_file` → canonical read payload flow. `_encode_explorer_edit`
  re-applies the file's original BOM and line-ending style (stripping one leading
  `U+FEFF`; `"none"` writes new breaks as LF). An unchanged encoded buffer is a
  no-op that never calls `replace_file`.
- **Atomic replace.** `replace_file` added to **both** backends.
  `_LocalExplorerBackend` uses `tempfile.mkstemp` (unique `.gv-save-*` sibling) +
  `fsync` + `os.chmod` + `os.replace`, cleaning the temp in `finally`.
  `_SftpExplorerBackend` uploads to a unique `.gv-save-<uuid>` sibling
  (exclusive `"xb"`, falling back to `"wb"` only on the already-unique temp
  name), copies mode bits, and swaps with `posix_rename`; a server without
  `posix_rename` raises a clear `RuntimeError` and never opens the destination.
- **Errors + claim.** `ExplorerRouteError` (+`ExplorerFileConflictError` 409,
  `ExplorerSaveInProgressError` 409, `ExplorerFileTooLargeError` 413) carry a
  stable `code`/`status`/`details`. The short-held `_explorer_save_claim`
  context manager holds `_explorer_save_claims_lock` only to add/remove the
  `(session_id, resolved_path)` key — never during file/network I/O.

### 9.2 Backend — `web/api.py`

- `GET /api/explorer/<id>/file` is now a thin route calling
  `get_explorer_file_payload`.
- `PUT /api/explorer/<id>/file` validates JSON field types (object body,
  non-empty `path`, string `content`, non-empty `base_revision`) then calls
  `save_explorer_file_payload`. The app-level cross-origin write guard already
  covers PUT unchanged.
- `_explorer_route_response` catches `ExplorerRouteError` before `ValueError`
  and maps it to `{error, code, **details}` at its `status_code`; I/O failures
  return `500` with `code: "io_error"`.

### 9.3 Frontend

- **New module `web/static/js/explorer-editor.js`** (loaded after
  `explorer-viewer.js`, before `terminals.js`) owns `pane._explorerEdit`, the
  Source-view textarea, save/conflict flows, the inline conflict/error bar, the
  discard guards, and the `beforeunload` handler. Editor state is transient and
  never persisted.
- **`explorer-viewer.js` hooks:** `renderExplorerFile` / `updateExplorerFileInPlace`
  store the editor metadata, inject `explorerEditorControlsHtml`, and call
  `refreshExplorerEditControls`; `renderExplorerTabStrip` renders the dirty-tab
  dot; `activateExplorerTab`, `closeExplorerTab`, `openExplorerFile`,
  `loadExplorerPane`, and `refreshExplorerPane` all `await` the discard guard.
- **`terminals.js` hooks:** `closeTerminalPane`, `switchGroup`, and
  `closeSessionGroup` await the guard (`confirmDiscardExplorerEdit` /
  `confirmDiscardAllExplorerEdits`).
- **Icons** (`terminal-icons.js`): stroke `currentColor` `EXPLORER_EDIT_ICON`,
  `EXPLORER_SAVE_ICON`, `EXPLORER_CANCEL_ICON`.
- **CSS** (`terminals.css`): token-only `.explorer-editor-actions`,
  `.explorer-source-editor`, `.explorer-edit-bar` variants, class-based
  `.is-busy`, and the `.explorer-tab.is-dirty` dot.

### 9.4 Tests and docs

Backend + frontend contract tests added to `tests/test_api.py` (see §10);
`explorer-editor.js` added to the served-asset, load-order, and
`GuardrailAuditFixesTestCase` no-dialog scans. Docs updated in `README.md`,
`CLAUDE.md`, `AGENTS.md`, `CHANGELOG.md`, and the `web/explorer.py` module
docstring. `python tests/run_tests.py` and `python -m ruff check .` pass.

> **Deviation from the proposal:** an invalid-UTF-8 (but non-binary) *complete*
> file reports `edit_block_reason: "unsupported_format"` rather than `null`, so
> the disabled-Edit tooltip is specific instead of generic. Image responses use
> the same `"unsupported_format"` reason as specified.

## 10. Edge-case coverage

Every acceptance criterion (§1) and edge case is exercised. Backend tests are in
`ApiRoutesTestCase`; frontend contract tests assert the wiring in the served JS.

| Edge case | Handling | Test |
| --- | --- | --- |
| Complete UTF-8 file is editable; metadata correct | `editable`, exact-byte `revision`, `line_ending`, `utf8_bom` returned | `test_explorer_file_returns_editor_metadata_for_complete_file` |
| Truncated (>10 MiB) file | view-only, `edit_block_reason: "truncated"`, `revision: null` | `test_explorer_file_reports_truncated_file_as_non_editable` |
| Mixed line endings | view-only, `"mixed_line_endings"` | `test_explorer_file_reports_mixed_line_endings_as_non_editable` |
| Complete invalid UTF-8 beyond the binary sample | view-only, `"unsupported_format"` | `test_explorer_file_rejects_complete_invalid_utf8_for_editing` |
| Image / binary file | view-only image response; PUT on binary rejected | `test_explorer_image_file_reports_unsupported_format_for_editing`, `test_explorer_save_rejects_traversal_and_unsupported` |
| Round-trip save returns refreshed payload | 200 + new revision + updated content | `test_explorer_save_roundtrips_local_utf8_and_returns_payload` |
| Preserve CRLF / CR / no-final-newline / BOM | byte-exact re-encode | `test_explorer_save_preserves_crlf_cr_bom_and_final_newline` |
| Unchanged buffer | 200 no-op, `replace_file` never called | `test_explorer_save_unchanged_content_is_a_noop` |
| Missing / wrong JSON field types | `400 invalid_request` | `test_explorer_save_rejects_bad_json_field_types` |
| Traversal / directory / missing / unsupported ext | `400` | `test_explorer_save_rejects_traversal_and_unsupported` |
| Replacement over the size cap | `413 file_too_large` + `max_bytes` | `test_explorer_save_rejects_oversized_replacement` |
| Stale revision → conflict, retry, re-conflict | `409 file_conflict` + `current_revision`; retry succeeds; second change re-conflicts | `test_explorer_save_stale_revision_conflicts_then_retry_succeeds` |
| Two GridVibe saves racing one file | `save_in_progress`; lock not held during I/O; released after | `test_explorer_save_claim_serializes_and_releases_without_holding_lock` |
| Write failure mid-save | original bytes intact, temp cleaned, `500 io_error` | `test_explorer_save_write_failure_preserves_original_and_cleans_temp` |
| Local mode bits preserved | mode copied to replacement (POSIX only) | `test_local_replace_file_preserves_mode_bits` |
| SFTP atomic write path | unique temp, `xb`, `chmod`, `posix_rename`, no stray remove | `test_sftp_replace_file_writes_temp_applies_mode_and_posix_renames` |
| SFTP server without `posix_rename` | raises; destination never opened (`wb`) | `test_sftp_replace_file_without_posix_rename_never_truncates_destination` |
| Cross-origin PUT | untrusted origin `403`; same-origin reaches the route | `test_explorer_save_rejects_cross_origin_put` |
| Edit/Save/Cancel + textarea + Tab + Ctrl/Cmd+S | wiring + attributes present; save sends `path`/`content`/`base_revision` | `test_terminals_page_explorer_editor_controls_and_wiring` |
| Conflict UI branches on `code`, retries with revision | `file_conflict` branch, Reload/Overwrite, `save_in_progress`/`file_too_large` messaging | `test_terminals_page_explorer_editor_conflict_branches_on_code` |
| **Accidental teardown of a dirty buffer** (terminal buttons, tab switch/close, open another file, tree/breadcrumb nav, refresh, close pane, switch/close session group, page reload) | each awaits the in-page discard confirm; `beforeunload` warns on page close; editor state never serialized | `test_terminals_page_explorer_editor_guards_dirty_teardown` |
| Icons/CSS guardrails | stroke `currentColor` icons, token colors, class-based busy state, no `window.*` dialogs | `test_terminals_page_explorer_editor_icons_and_styles_are_token_driven`, `GuardrailAuditFixesTestCase` |

The user's specific concern — *"user clicks terminal buttons by accident, exits
the app, closes sessions/terminals, etc."* — maps to the teardown-guard row: the
close-pane button (`closeTerminalPane`), the session-group tab **×** and
group-switch (`closeSessionGroup` / `switchGroup`), Explorer tab **×** and tab
switch, directory/file navigation, the Refresh button, and the browser
close/reload are **all** intercepted. In-app actions route through the shared
`openGenericConfirmModal` (WebView2-safe); only the OS-level page unload — where
no in-page modal can run — falls back to the browser's native
`beforeunload` prompt. There is no persisted draft, so a hard process kill
before saving loses the in-memory buffer (stated in the release note).
