# Text Editing and Drag/Drop for File Explorer Mode

## Goal

Research practical ways to add write-oriented file explorer features to GridVibe and recommend the safest initial path.

The short answer: both text editing and drag/drop are feasible, but they should not be introduced as broad filesystem mutation features. The safest first version is an explicit, opt-in local text save flow for already-open supported text files. Drag/drop should start later as a narrow upload/copy action into the current explorer directory, with clear confirmation and no move/delete behavior.

## Current GridVibe Context

- `README.md` currently describes file explorer panes as read-only repository views and explicitly excludes file moving, editing, deleting, upload, staging, restore, checkout, commit, pull, and push actions.
- Explorer panes are session-backed non-terminal panes. The frontend renders them in `templates/terminals.html` and the backend serves directory/file data from `web/api.py`.
- The backend already has local and remote root-bound path resolution:
  - `_resolve_explorer_candidate_path`
  - `_resolve_explorer_paths`
  - `_resolve_explorer_file_path`
  - `_resolve_remote_explorer_candidate_path`
  - `_resolve_remote_explorer_paths`
  - `_resolve_remote_explorer_file_path`
- The current write boundary is clean. Explorer routes are read-only:
  - `GET /api/explorer/<session_id>/entries`
  - `GET /api/explorer/<session_id>/file`
  - `GET /api/explorer/<session_id>/git/diff`
  - `GET /api/explorer/<session_id>/git/repo`
- Text preview safety is already bounded by known editor languages, binary rejection, UTF-8 decoding, and `EXPLORER_FILE_PREVIEW_MAX_BYTES`.
- The current editor surface is not an editor widget. `renderExplorerFile()` stores `pane._explorerFileContent`, then `renderExplorerSource()` renders escaped, highlighted source lines into a scrollable `div`.
- Client-side find, Markdown preview, diff view, font zoom, Git badges, and refresh all assume the file view is a rendered read-only source panel.
- The explorer row CSS already contains `.explorer-row.drag-over` and `.explorer-row.move-target`, but there is no filesystem drag/drop behavior wired today. Existing drag behavior elsewhere is for session tabs and terminal panes.

## External Research Notes

- MDN's file drag/drop guidance shows the browser-native path for accepting files from the OS: listen for `dragover` and `drop`, prevent default browser opening/downloading behavior for file drops, read file entries from `DataTransferItem.getAsFile()` or `DataTransfer.files`, and provide a normal file input fallback. Source: https://developer.mozilla.org/en-US/docs/Web/API/HTML_Drag_and_Drop_API/File_drag_and_drop
- MDN notes that `DataTransfer.files` is only available during `drop` and `paste` events because the data store is protected outside those events. Source: https://developer.mozilla.org/en-US/docs/Web/API/DataTransfer/files
- MDN Fetch examples support simple JSON or `FormData` requests from the browser to Flask endpoints. For text saves, JSON is enough; for drag/drop uploads, `FormData` maps better to one or more `File` objects. Source: https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch
- CodeMirror 6 is a capable browser editor with modular state, view, commands, undo, line numbers, and language extensions, but its own guide points out that setup usually expects bundling or a module loader. Source: https://codemirror.net/docs/guide/
- Paramiko's SFTP API supports file-like upload flows such as `putfo`, which would make remote upload/save possible later, but it adds more failure modes than local writes: SSH reconnects, remote permissions, path normalization, partial writes, and remote atomic replace semantics. Source: https://docs.paramiko.org/en/stable/api/sftp.html

## Text Editing Options

### Option 1: Minimal textarea-backed edit mode

Add an `Edit` toggle to the existing file view. In read mode, keep the current highlighted source renderer. In edit mode, show a plain `<textarea>` containing `pane._explorerFileContent`, with `Save`, `Revert`, and dirty-state UI.

Pros:

- Smallest frontend change.
- No new JavaScript dependency or bundler change.
- Easy to keep bounded to supported UTF-8 text files already accepted by the backend preview route.
- Works with the existing `fetch` and unittest/template testing style.

Cons:

- No syntax-aware editing, multiline selection helpers, or robust large-file editing.
- Line numbers and highlighting disappear while editing unless custom overlay work is added.
- Search would need to target the textarea separately or be disabled in edit mode for v1.

Recommendation: best v1.

### Option 2: `contenteditable` source lines

Make the existing rendered lines editable in place.

Pros:

- Preserves the current visual source layout.
- Could keep line-number UI visible.

Cons:

- Harder to keep text extraction, paste handling, undo, selection, IME input, indentation, and search behavior correct.
- Syntax-highlighted nested spans make editing semantics brittle.
- Higher regression risk than a textarea.

Recommendation: avoid for v1.

### Option 3: CodeMirror editor

Replace or augment the source view with CodeMirror 6.

Pros:

- Mature editing surface with undo, selection, keymaps, line numbers, viewporting, and language extensions.
- Better long-term base for real text editor mode.

Cons:

- Introduces a frontend packaging/dependency decision that the current template-heavy app does not appear to have.
- Adds integration work for theming, pane lifecycle, resize, search, Markdown preview, diff tabs, and tests.
- Too much for a safe first write-enabled slice.

Recommendation: revisit after a minimal write path proves useful.

## Text Save API Proposal

Add a new local-only endpoint first:

```text
PUT /api/explorer/<session_id>/file
```

Request body:

```json
{
  "path": "relative/file.txt",
  "content": "new UTF-8 text",
  "base_modified": 1710000000.0,
  "base_size": 1234
}
```

Behavior:

- Require an explorer session.
- Reject remote SSH explorer sessions in v1 with a clear `400` such as `Remote editing is not supported yet`.
- Resolve `path` through `_resolve_explorer_file_path`.
- Re-run `_explorer_editor_language(path)` so only supported text formats are editable.
- Reject content larger than a new save limit, initially no larger than the existing preview limit.
- Reject embedded NUL and binary-looking content.
- If `base_modified` and `base_size` do not match current stat data, return `409 Conflict` with the latest metadata and do not write.
- Write through a temporary file in the same directory, then `os.replace()` into place for local atomic replacement.
- Preserve file permissions when practical by copying the previous mode onto the temp file before replacement.
- Return the same shape as `GET /file` so the frontend can call `updateExplorerFileInPlace()`.

This keeps the save path close to the existing read path and avoids creating new files, renames, deletes, remote writes, or directory mutations in the first pass.

## Text Editor UI Proposal

Add a narrow edit flow to `renderExplorerFile()`:

- Show an `Edit` button only when `data.truncated` is false, the file has a supported editor language, and the file was loaded from a local explorer pane.
- On `Edit`, replace the source panel with a textarea and show `Save` and `Revert`.
- Keep `Preview` and `Diff` tabs read-only. If the user enters edit mode from `Source`, stay on `Source`.
- Disable refresh/reload navigation or ask for confirmation while the textarea is dirty.
- On `Save`, send the textarea value with the file's original `modified` and `size`.
- On `409`, keep the unsaved textarea content and show a non-blocking conflict message with a `Reload` action.
- On successful save, update `pane._explorerFileContent`, reset dirty state, rerender source, refresh Git metadata, and keep scroll position.

V1 should not attempt autosave, save-on-blur, format-on-save, multi-file editing, file creation, rename, or remote save.

## Drag/Drop Options

### Option 1: OS file upload into current directory

Allow users to drag one or more files from the operating system into the current explorer directory. Treat the operation as upload/copy into that directory.

Pros:

- Uses browser-native `DataTransfer.files` and `FormData`.
- Clear user mental model: dropping external files copies them into the shown folder.
- Does not require implementing internal file moves.

Cons:

- Mutates the filesystem and can overwrite files if not carefully guarded.
- Directory drag/drop support is inconsistent and more complex than file drops.
- Remote SSH upload needs SFTP write support and careful partial-upload handling.

Recommendation: best first drag/drop feature after local saves are stable.

### Option 2: Internal file/folder move by dragging explorer rows

Make explorer rows draggable and drop them onto folders to move files within the explorer root.

Pros:

- Natural file manager behavior.
- Existing row UI can expose target highlighting.

Cons:

- Move/rename is more dangerous than upload because a bad drop can relocate source files.
- Needs undo or strong confirmation, collision handling, cross-directory refresh, Git implications, and directory recursion rules.
- Higher risk of surprising users given the current read-only contract.

Recommendation: do not start here.

### Option 3: Drop text into the editor

Allow dropping text snippets into the edit textarea.

Pros:

- Browser textarea behavior mostly covers this automatically.
- Low backend impact.

Cons:

- Not the file explorer drag/drop feature users usually expect.
- Can conflict with OS file drops unless scoped carefully.

Recommendation: allow default textarea text drops, but do not count this as v1 drag/drop.

## Drag/Drop Upload API Proposal

Add only after the local save path lands:

```text
POST /api/explorer/<session_id>/upload
```

Request:

- `multipart/form-data`
- `path`: target directory relative to the explorer root
- `files`: one or more uploaded files
- `overwrite`: default `false`

Behavior:

- Start local-only.
- Resolve `path` through `_resolve_explorer_paths`.
- Accept files only, not directories, in v1.
- Reject absolute filenames and sanitize browser-provided names with `os.path.basename`.
- Reject path separators in uploaded filenames for v1.
- Reject hidden overwrite unless `overwrite: true` is explicitly supplied after a collision response.
- Enforce per-file and total upload size limits.
- Write each file through a same-directory temp file and `os.replace()` only when the final target is valid.
- Return refreshed `entries` for the target directory.

Frontend behavior:

- Wire `dragenter`, `dragover`, `dragleave`, and `drop` only on directory-mode explorer lists.
- Prevent the browser's default file-open behavior for file drops over GridVibe explorer panes.
- Set `dropEffect = "copy"` for valid file drops.
- Show a concise drop target state on the current directory, not on individual files.
- On collision, show a confirmation for the specific filenames before retrying with overwrite enabled.
- Keep internal pane/tab dragging isolated from file upload events.

## Recommended Phased Plan

### Phase 1: Local text save, no drag/drop

Implement local-only text editing for non-truncated supported text files.

This is the safest meaningful write feature because it reuses the current file preview contract, root-bound path validation, UTF-8 text assumptions, and per-file UI. It also creates the backend write primitives and conflict handling needed before larger filesystem mutations.

### Phase 2: Local file upload by drag/drop

Add OS file drop/upload into the current local explorer directory.

Keep this copy-only, file-only, current-directory-only, and collision-safe. Do not support internal moves, folder drops, recursive uploads, remote uploads, or overwrite-by-default.

### Phase 3: Remote editing/upload

Extend save/upload to SSH explorers after local behavior and tests are stable.

Remote support should use SFTP, explicit timeouts, same-directory temp names, best-effort atomic rename where supported, cleanup on failures, and clear errors for permission or connection failures.

### Phase 4: Rich editor or internal moves

Only after the simple write model has proven stable:

- Consider CodeMirror if users need true editor ergonomics.
- Consider internal drag-to-move only with confirmation, collision handling, and tests for source/destination safety.

## Risk Controls

- Keep write features opt-in and visible. The current explorer contract is read-only; users should not accidentally mutate files.
- Do not enable writes for truncated files.
- Do not use stale previews as overwrite authority. Check size/modified metadata before save.
- Keep all paths rooted through the existing explorer path resolvers.
- Avoid symlink-following surprises. Preserve the current `follow_symlinks=False` posture where possible and document the intended behavior before implementing writes through symlinked files.
- Never infer overwrite from a drop. Require explicit confirmation.
- Avoid recursive directory drops in v1.
- Keep Git actions read-only. Editing may naturally change Git status, but do not add stage/restore/commit as part of this work.

## Focused Test Plan

- Local `PUT /file` saves a supported text file inside the explorer root and returns updated content/metadata.
- Local `PUT /file` rejects paths outside the root.
- Local `PUT /file` rejects unsupported extension, binary-looking content, oversized content, directories, and missing paths.
- Local `PUT /file` returns `409` when `base_modified` or `base_size` is stale.
- Remote `PUT /file` returns a clear unsupported error in v1.
- Template includes edit/save/revert controls only for eligible local non-truncated files.
- Dirty edit state protects back, refresh, and tab close flows.
- Upload endpoint rejects outside-root target directories, absolute filenames, nested filenames, oversized files, and collisions without overwrite.
- Directory-mode drag/drop wiring prevents default browser file opening only for file drops and does not interfere with existing pane/tab dragging.

## Initial Recommendation

Start with Phase 1 only: a local, textarea-backed edit mode plus `PUT /api/explorer/<session_id>/file`.

This gives GridVibe practical editing with the smallest change surface. It avoids new dependencies, keeps SSH writes out of the first pass, preserves the read-only Git integration, and creates a safer foundation for drag/drop upload later.
