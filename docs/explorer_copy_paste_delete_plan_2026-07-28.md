# Explorer Copy / Paste / Delete — v1 implementation plan

**Date:** 2026-07-28
**Status:** Implemented; automated verification complete
**Supersedes:** `docs/r&d/explorer_copy_paste_delete_proposal_2026-07-25.md` (kept as the
research/decision record; this document is the buildable plan)
**Scope:** The Explorer pane's **Files** sidebar tree and the directory listing in the
permanent **Preview** tab. Local (`_LocalExplorerBackend`) and SSH/SFTP
(`_SftpExplorerBackend`) reach feature parity through one shared policy module.

---

## 1. What this adds

Right-clicking a real file or directory row in either surface opens the existing in-page
menu, extended with:

1. **Copy**
2. **Paste "<name>" into "<folder>"** — or the disabled **Paste — nothing copied**
3. *(separator)*
4. **Copy path**
5. **Copy relative path**
6. *(separator)*
7. **Delete…** (danger-styled, last, never initially focused)

Copy is a session-scoped in-page clipboard (one entry). Paste copies into a root-confined
destination directory, never overwrites, and allocates an extension-aware `-Copy` name on
collision. Delete permanently removes a file, an empty directory, or — after an explicit
confirmation — a directory and all of its contents.

Two new routes, both already covered by the app-level cross-origin write guard:

```
POST /api/explorer/<session_id>/paste
POST /api/explorer/<session_id>/delete
```

### Contract change

This is the **third** bounded exception to the Explorer's read-only filesystem contract,
alongside the guarded Git sidebar actions and the in-place text editor. It must be stated
explicitly in `CLAUDE.md`, `AGENTS.md`, `README.md` and `CHANGELOG.md`. It does **not**
open move, rename, create, upload, or overwrite — those stay prohibited.

---

## 2. Decisions taken since the 2026-07-25 proposal

The proposal left four open questions and specified several mechanisms more elaborate than
a safe v1 needs. Each deviation below is deliberate, and each one removes code without
removing a safety property.

| # | Proposal said | This plan does | Why |
|---|---|---|---|
| D1 | Server-side idempotency registry keyed by `(session_id, operation_id)` | **Dropped.** Client sets the pane-busy flag *synchronously* before the first `await`; the server's ancestor-aware path claim rejects a genuine overlap with `409 operation_in_progress` | A lost Paste response, retried, produces a second visible `-Copy` entry — not data loss, and the user can delete it. A lost Delete response, retried, hits `404 source_missing` — already safe. The registry buys idempotency for a case that is neither destructive nor silent, at the cost of a second concurrency primitive with its own expiry/purge/lifecycle. Retry is only ever offered when the server proves `mutated: false`. |
| D2 | Capability-probed publication: hard-link publish, no-replace rename, SFTP extension contracts, with an exclusive-create *fallback* | **The exclusive-create path is the only path**, identically on both backends: reserve the final name with `O_EXCL` (file) / `mkdir` (directory), then stream into the reserved entry | One code path instead of three plus probes. It already delivers the mandatory property — *never overwrite* — because reservation is atomic. The property it gives up (a partially-written copy is briefly visible to external tools under its final name) is not one GridVibe can guarantee for directories on any portable API anyway. |
| D3 | Reject symlinks/reparse points for both Copy and Delete | **Copy rejects links anywhere in the source tree. Delete unlinks a link without ever following it** — at top level and nested | Rejecting nested links would make any tree containing one undeletable, and a top-level-only rejection is incoherent with that. `unlink`/`remove` on a link is a bounded operation that provably cannot touch the target. Copying a link would need follow-or-recreate semantics v1 does not want. |
| D4 | Opaque `root_revision` as new server state | Kept, implemented as a **pure function**: `sha256(canonical root path)[:16]` | No registry, no lifecycle. A live session's `explorer_root_directory` genuinely can change (`web/api.py:1769-1839` switches a pane into explorer mode with a new root), so the guard is real, but it needs no state. |
| D5 | Entries gain `entry_kind`, `can_copy`, `can_delete`, `revision` | Entries gain **`entry_kind`** and **`revision`** only | `can_copy`/`can_delete` are derivable client-side from `entry_kind` + the existing `deleted` flag. Three fields where one suffices is the shape guardrail 5 warns about. |
| D6 | Q5 open: protect `.git`? | **Yes.** Delete refuses any directory named `.git`, at any depth under the root, plus the root itself | Deleting `.git` destroys history irreversibly even when working files survive. `.hg`/`.svn` are not protected (GridVibe has no Git-equivalent integration for them and the blast radius is the same as any other directory the user explicitly names). Copy of `.git` is allowed — it is a read, and it counts against the copy limits. |
| D7 | Q6 open: what limits? | Module constants in `web/explorer_fs.py`, **not** config keys | A dormant/unwired config key is exactly guardrail 5. Promoting them to an `explorer_fs` config section later is mechanical (`RuntimeConfig` + `/api/app-config` normalization), and the proposal itself said not to add configurable limits that don't flow through both. |
| D8 | Q7 open: keyboard shortcuts? | **Out of v1** for `Ctrl+C`/`Ctrl+V`/`Delete`. Keyboard *menu invocation* is in, for free | The browser fires a real `contextmenu` event for `Shift+F10` and the Menu key, so the existing delegated listener already handles it — only the `clientX/clientY == 0` positioning case needs three lines. Row-selection semantics for the accelerators do not exist yet. |
| D9 | Q8 open: recycle bin? | **No** | Platform-specific, no SFTP equivalent, and a second delete semantics to explain. |
| D10 | (not addressed) | **Save/paste/delete claims become cross-session** and namespaced by host | The current `_explorer_save_claim` is keyed `(session_id, path)`, so two panes on the same root can already race each other's saves. A delete must conflict with a save regardless of which pane issued it. |

Unchanged from the proposal: same-session/same-root only (Q4), `-Copy` naming (Q2), Paste
offered on file rows targeting the containing folder (Q3), recursive delete after explicit
confirmation (Q1), in-memory clipboard, quarantine-rename delete.

---

## 3. Verified baseline (what the code actually looks like today)

Everything below was read at `dead45d`; the plan's edits are expressed against it.

### Backend

| Fact | Location |
|---|---|
| Two backends implement one duck-typed interface; helpers are written once against it | `web/explorer.py:1102` (`_LocalExplorerBackend`), `web/explorer.py:1285` (`_SftpExplorerBackend`) |
| A feature module owns policy; backends expose one hook. This is the pattern to copy | `web/explorer_search.py` + `search_lines()` at `web/explorer.py:1262` and `:1453` |
| `_explorer_backend()` owns SSH lifetime; SFTP reuses the pooled transport | `web/explorer.py:1488`, pool at `:2251` |
| Every route body goes through one error mapper | `_explorer_route_response`, `web/api.py:925` |
| Structured error base with `status_code` + stable `code` | `ExplorerRouteError`, `web/explorer.py:489` |
| Short-held claim set; **no I/O under the lock** | `_explorer_save_claim`, `web/explorer.py:529` |
| The atomic-write precedent, including the paramiko `"x+b"` exclusive-create trick and its caveat | `replace_file`, `web/explorer.py:1219` (local) and `:1385` (remote) |
| The write-route precedent (JSON shape validation in the route, policy in the module) | `PUT /file` at `web/api.py:772` → `save_explorer_file_payload` at `web/explorer.py:2547` |
| Entry payloads carry `name/path/type/size/modified` and **nothing that distinguishes a symlink** — local uses `follow_symlinks=False` but reports every non-directory as `"file"`; remote reads `st_mode` from `listdir_attr` and does the same | `web/explorer.py:787` and `:2212` |
| `resolve_candidate()` **realpaths the whole path**, so it resolves a symlink leaf to its target — unusable as-is for delete | `web/explorer.py:683`, `:1172`, `:1343` |
| A live session's root can change | `web/api.py:1769-1839` |
| `async_mode="threading"` — a long copy occupies one worker thread, not the server | `web/app.py:71` |

### Frontend

| Fact | Location |
|---|---|
| All page scripts share one global scope (no IIFE wrappers); load order is shared → app-settings → terminal-icons → voice-input → explorer-viewer → explorer-editor → explorer-search → terminals | `templates/terminals.html:297-304` |
| Context menu: build, viewport clamp, outside-click, Escape/Arrow keys. **No model for disabled, danger, separator, title, or async actions** | `web/static/js/explorer-viewer.js:1110-1205` |
| Tree rows already carry `data-explorer-copy-path` | `explorerTreeRowHtml`, `:1778` |
| The menu is wired on the tree panel and the Git panel only | `:1843`, `:1213` |
| Preview rows carry `data-explorer-path` and are wired for **click only** — right-click falls through to the browser/WebView menu | `explorerDirectoryRowHtml` `:3454`, `wireExplorerDirectoryRows` `:3485` |
| `#explorer-viewer-<index>` is **recreated**; `#explorer-list-<index>` is the stable ancestor to delegate on | `explorerEnsureViewerShell`, `:4295` |
| Generic confirm shell: single module-level resolver, no owner concept | `openGenericConfirmModal` `web/static/js/terminals.js:1337`, `closeGenericConfirmModal` `:1321` |
| Dirty-edit state and its discard guards, including the edited `path` | `web/static/js/explorer-editor.js:12-80`, state shape at `:189` |
| Toast helper | `showTerminalToast`, `web/static/js/terminals.js:7064` |
| Inline error/action bar precedent to mirror for the failure surface | `explorerEditBarHost` / `showExplorerEditError` / `renderExplorerConflictBar`, `web/static/js/explorer-editor.js:421-483` |
| Pane and group close entry points | `closeTerminalPane` `terminals.js:5731`, `closeSessionGroup` `:6949`, group switch guard `:6862` |
| Guardrail test enumerates the static JS files it scans — a new file must be added to it | `GuardrailAuditFixesTestCase.STATIC_JS`, `tests/test_api.py:10133` |
| Existing test pins `_explorer_save_claim`'s two-argument signature | `tests/test_api.py:5605` |

---

## 4. Backend design

### 4.1 Module layout

```
web/explorer.py        + entry_kind/revision on both entry payloads
                       + generalized ancestor-aware claim registry
                       + ~9 small mutation hooks per backend class
web/explorer_fs.py     NEW — all policy: resolution, validation, limits,
                       naming, copy/delete orchestration, payloads, errors
web/api.py             + two thin routes (JSON shape + session lookup only)
```

`web/explorer_fs.py` mirrors `web/explorer_search.py` exactly: everything except the
backend hooks is a pure function, unit-testable without a route, a repo, or SSH. Nothing
new goes into `web/api.py` beyond the two route bodies (guardrail 6).

### 4.2 Entry payload additions

Both `_explorer_entry_payload()` (`web/explorer.py:787`) and
`_remote_explorer_entry_payload()` (`:2212`) gain two fields. `type` is unchanged, so every
existing consumer keeps working.

```python
"entry_kind": "directory" | "file" | "link" | "other",
"revision":   "<16 hex chars>",   # None when the entry could not be stat'd
```

* Local: classify from `entry.stat(follow_symlinks=False).st_mode` via
  `stat.S_ISDIR/S_ISREG/S_ISLNK`. On Windows a reparse point surfaces as
  `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT` → `"link"`.
* Remote: classify from the `st_mode` already returned by `listdir_attr` (which is an
  `lstat`, so links are visible). No extra round trip.

`revision` comes from one shared pure function so client and server agree:

```python
def _fs_revision(stat_info: dict) -> str:
    """Opaque optimistic-concurrency token for one entry."""
    parts = (stat_info["kind"], stat_info.get("size"), stat_info.get("mode"),
             stat_info.get("mtime_ns"), stat_info.get("dev"), stat_info.get("ino"))
    return hashlib.sha256("|".join("" if p is None else str(p)
                                   for p in parts).encode()).hexdigest()[:16]
```

Local supplies `dev`/`ino`/`mtime_ns`; SFTP supplies only `size`/`mode`/`mtime` (whole
seconds). **Documented limitation:** remote mtime granularity means a same-second
replacement of a same-size file is not detected, and a *directory's* revision describes its
own inode, never its descendants' contents. This is optimistic protection, not a
compare-and-swap.

The entries response gains one top-level field:

```python
"root_revision": _fs_root_revision(root_path)   # sha256(canonical root)[:16]
```

### 4.3 The mutation resolver (the one thing the old resolver cannot do)

`resolve_candidate()` realpaths the *entire* path. Given `notes → /etc/passwd`, resolving
`notes` for a delete yields `/etc/passwd`, and root-containment then rejects it — but for a
link *inside* the root pointing *inside* the root, it would delete the target instead of
the link. Mutations therefore resolve parent-then-leaf:

```python
def resolve_mutation_target(backend, rel_path):
    """(root, parent_abs, leaf, target_abs) — leaf is NEVER canonicalized."""
    rel = _normalize_rel(rel_path)              # slashes, no "", ".", "..", no NUL
    parent_rel, _, leaf = rel.rpartition("/")
    if not leaf or leaf in (".", ".."):
        raise ExplorerFsInvalidRequest(...)
    root, parent_abs = backend.resolve_candidate(parent_rel)   # realpath + containment
    if backend.fs_lstat(parent_abs)["kind"] != "directory":
        raise ExplorerFsInvalidDestination(...)
    if not backend.path_inside_root(root, parent_abs):
        raise ExplorerFsInvalidRequest(...)                    # belt and braces
    return root, parent_abs, leaf, backend.fs_join(parent_abs, leaf)
```

The parent chain is fully canonicalized and containment-checked (so a symlinked ancestor
escaping the root is caught); the leaf is joined literally, so `lstat`/`unlink` act on the
entry the user right-clicked, never on what it points at.

### 4.4 Backend hooks

Nine small methods per backend class, none of which contain policy:

| Method | Local | Remote |
|---|---|---|
| `fs_lstat(path)` → `{kind,size,mode,mtime,mtime_ns,dev,ino}` or `None` | `os.lstat` | `sftp.lstat` |
| `fs_listdir(path)` → `[(name, stat_dict)]` | `os.scandir` (`follow_symlinks=False`) | `sftp.listdir_attr` |
| `fs_join(parent, name)` | `os.path.join` | `_remote_path_join` |
| `fs_mkdir_exclusive(path)` | `os.mkdir` (raises `FileExistsError`) | `sftp.mkdir` |
| `fs_create_exclusive(path)` → write handle | `os.open(O_CREAT\|O_EXCL\|O_WRONLY)` | `sftp.open(path, "x+b")` — the `"x+b"` form from `replace_file`; **no `"wb"` fallback on a final destination name** |
| `fs_read_chunks(path, size)` | buffered `open(..., "rb")` | `sftp.open(..., "rb")` |
| `fs_remove_file(path)` (no follow) | `os.remove` | `sftp.remove` |
| `fs_rmdir(path)` (empty only) | `os.rmdir` | `sftp.rmdir` |
| `fs_rename(src, dst)` (dst is a fresh UUID name) | `os.rename` | `sftp.rename` |
| `fs_chmod` / `fs_utime` (best effort, failures swallowed) | `os.chmod`/`os.utime` | `sftp.chmod`/`sftp.utime` |
| `fs_claim_namespace()` | `"local"` | `f"ssh:{host}:{port}:{username}"` |

`fs_create_exclusive` deliberately has no fallback: `replace_file` can fall back to `"wb"`
because its target is a unique UUID temp name, but here the target *is* the destination and
`"wb"` would truncate whatever an external process just created. A server without
exclusive-create fails the paste with `io_error` and mutates nothing.

### 4.5 Claims

`_explorer_save_claims` (`web/explorer.py:525`) is generalized in place:

* Keys become **namespaced absolute paths**: `f"{backend.fs_claim_namespace()}|{normcase(abs)}"`.
  Two SSH sessions to different hosts can no longer collide by string; two panes on the
  same root now correctly conflict.
* Conflict is **ancestor-aware**: a claim conflicts with an equal key, an ancestor key, or
  a descendant key. Deleting `src/` therefore blocks a concurrent save or paste under it.
* `_explorer_save_claim(session_id, claim_key)` keeps its two-argument signature (the test
  at `tests/test_api.py:5605` passes unchanged); `save_explorer_file_payload`
  (`web/explorer.py:2547`) is updated to pass `backend.fs_claim_key(file_path)` instead of
  the bare path, which is what puts saves and deletes on the same registry.
* Unchanged and non-negotiable: acquisition/release happen under a small mutex,
  **no filesystem or SFTP I/O ever runs while it is held**, and neither `connection_lock`
  nor `SessionManager.lock` is involved (guardrail 2).

Paste claims the destination parent and the source; Delete claims the target.

### 4.6 Limits

```python
EXPLORER_FS_MAX_ENTRIES     = 10_000            # per copy or delete tree
EXPLORER_FS_MAX_DEPTH       = 32
EXPLORER_COPY_MAX_BYTES     = 512 * 1024 * 1024
EXPLORER_COPY_CHUNK_BYTES   = 1024 * 1024       # streamed; never a full-buffer read
EXPLORER_COPY_NAME_ATTEMPTS = 100
```

All four are enforced by a **pre-scan that runs before any mutation**, so a limit violation
is always `413` with `mutated: false`.

### 4.7 Paste

```
POST /api/explorer/<session_id>/paste
{ "root_revision": "...", "source_path": "src/app.py",
  "source_revision": "...", "destination_directory": "archive" }

200 { "session_id": "...", "ok": true, "source_path": "src/app.py",
      "destination_path": "archive/app-Copy.py", "type": "file",
      "entry_kind": "file" }
```

The route's `session_id` identifies **both** source and destination. No second session,
absolute root, or client-chosen destination name is ever accepted.

1. **Guards.** Session is live and still an Explorer session; `root_revision` matches the
   session's current canonical root, else `409 root_changed`.
2. **Resolve.** Source via `resolve_mutation_target`; destination via `resolve_dir` (must
   be an existing real directory inside the root).
3. **Validate.** Source `lstat` exists and its revision matches (`409 entry_changed`);
   source is not the root (`403 protected_path`); source kind is `file` or `directory`
   (link/fifo/socket/device → `400 unsupported_entry_type`); destination is not the source
   and not a descendant of it (`400 invalid_destination`).
4. **Claim** source + destination parent.
5. **Pre-scan** (no mutation): iterative stack walk with `lstat`, no recursion, links never
   followed. Rejects any non-regular/non-directory entry anywhere in the tree, reporting
   its root-relative path. Accumulates entry count, depth and bytes against the limits.
   Re-checks the source revision at the end of the scan.
6. **Reserve the name.** Loop up to `EXPLORER_COPY_NAME_ATTEMPTS`: `app.py`, `app-Copy.py`,
   `app-Copy-2.py`, … (`os.path.splitext` on the basename; a dotfile with no stem/extension
   split keeps its whole name as the base, so `.env` → `.env-Copy`). Reserve with
   `fs_create_exclusive` / `fs_mkdir_exclusive`. `FileExistsError` → next suffix. Attempts
   exhausted → `409 destination_exists`. **Reservation is the no-overwrite guarantee**: it
   is atomic, so a name created between the check and the create loses the race safely.
7. **Populate.** File: stream `EXPLORER_COPY_CHUNK_BYTES` at a time into the reserved
   handle. Directory: walk the pre-scanned list, `fs_mkdir_exclusive` each subdirectory and
   exclusive-create + stream each file. Because the destination root was freshly created and
   is exclusively ours, an `EEXIST` *inside* it means an external process is writing into
   our new tree → abort as `io_error`.
8. **Metadata.** Best-effort `fs_chmod` + `fs_utime`. Failures are swallowed and logged at
   DEBUG. ACLs, ownership, xattrs, sparseness and hard-link identity are not preserved.
9. **On failure**, remove exactly the entry that was reserved (and, for a directory, the
   tree beneath it — walked with `lstat`, links unlinked, never followed). A pre-existing
   destination can never be reached by this cleanup because the reservation would have
   failed. If cleanup itself fails: log the root-relative path at WARNING and return
   `io_error` with `cleanup_incomplete: true`.

**Documented caveat:** between reservation and completion, the destination exists under its
final name and is incompletely populated. External tools can observe it. GridVibe never
reports success and never treats the entry as complete until the copy finishes. Copying a
directory is also not a filesystem snapshot — a source changing mid-copy yields a
mixed-time result.

### 4.8 Delete

```
POST /api/explorer/<session_id>/delete
{ "root_revision": "...", "path": "archive/app.py",
  "base_revision": "...", "recursive": false }

200 { "session_id": "...", "ok": true, "deleted_path": "archive/app.py",
      "type": "file", "entry_kind": "file" }
```

1. **Guards + resolve + revision check**, as for Paste.
2. **Protected paths.** The configured root → `403 protected_path`. Any directory named
   `.git` → `403 protected_path` with a message naming the repository risk.
3. **Kind dispatch.**
   * Non-directory (regular file, link, fifo, socket, device): `fs_remove_file`. Links are
     unlinked, never followed — the target is untouched.
   * Empty directory: `fs_rmdir`.
   * Non-empty directory **without** `recursive: true`: `400 invalid_request` ("directory
     is not empty") — the flag is the server-side proof that the confirmation ran.
   * Non-empty directory with `recursive: true`: pre-scan for the entry/depth limits
     (`413` before any mutation), then quarantine.
4. **Quarantine.** `fs_rename(target, sibling(".gv-delete-<uuid>"))`, then iterative
   depth-first removal. The requested path disappears atomically on common local
   filesystems and on SFTP servers with POSIX rename semantics.
   * Rename fails (Windows open handles, read-only parent, unusual SFTP server) → fall back
     to in-place depth-first removal. Same outcome, weaker atomicity; logged at DEBUG.
   * Removal fails part-way → WARNING with the root-relative quarantine name, and
     `500 io_error` with `partial: true`. The client then offers **Refresh/Inspect**, never
     an automatic retry.
5. Recursive removal never follows a link and never computes a broad path to `rmtree` — it
   walks only the pre-scanned entries under the quarantined root.

### 4.9 Error contract

`web/explorer_fs.py` adds `ExplorerRouteError` subclasses so `_explorer_route_response`
(`web/api.py:925`) maps them with no changes to the mapper. Every error carries
`mutated: true|false`; the frontend offers **Retry** only on `mutated: false`.

| HTTP | `code` | Raised when |
|---|---|---|
| 400 | `invalid_request` | Missing/malformed path, revision or flag; non-empty directory without `recursive` |
| 400 | `unsupported_entry_type` | Copy source (or any entry in its tree) is a link, reparse point, device, socket or FIFO |
| 400 | `invalid_destination` | Destination is not a directory, is the source, or is inside the source |
| 403 | `protected_path` | Explorer root, or a `.git` directory on Delete |
| 403 | `permission_denied` | Filesystem/SFTP refused the operation |
| 404 | `source_missing` | Source disappeared before the mutation |
| 409 | `root_changed` | `root_revision` no longer matches the session's root |
| 409 | `entry_changed` | Entry revision no longer matches |
| 409 | `destination_exists` | `-Copy` suffix attempts exhausted against an external writer |
| 409 | `operation_in_progress` | An overlapping GridVibe claim owns the path or an ancestor/descendant |
| 413 | `copy_limit_exceeded` | Entry count, depth or byte bound exceeded (always pre-mutation) |
| 500 | `io_error` | Anything else, incl. `partial: true` / `cleanup_incomplete: true` |

Error messages use root-relative paths and never carry raw remote exception text that could
contain credentials or command data.

### 4.10 Routes

Both route bodies validate JSON shape, look up the session, confirm it is an Explorer
session, and delegate — same structure as `PUT /file` (`web/api.py:772`):

```python
@app.route('/api/explorer/<session_id>/paste', methods=['POST'])
def paste_explorer_entry(session_id: str):
    session = session_manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    if not _is_explorer_session(session):
        return jsonify({"error": "Session is not a file explorer pane"}), 400
    data = request.get_json(silent=True)
    ...  # shape checks -> 400 invalid_request
    def handler(backend):
        return paste_explorer_entry_payload(backend, ..., session_id=session_id)
    return _explorer_route_response(session, handler)
```

`POST` (not a JSON-body `DELETE`) matches the guarded Git mutation routes and behaves
consistently through browser, WebView2 and the test client. Both are covered by the
existing cross-origin write guard — no change there, and no new Socket.IO emit.

---

## 5. Frontend design

### 5.1 File layout

| File | Change |
|---|---|
| `web/static/js/explorer-fs.js` | **NEW.** Clipboard, action context, menu item construction, paste/delete dispatch, post-mutation refresh, failure bar, lifecycle cancellation |
| `web/static/js/explorer-viewer.js` | Generalize the menu primitives; add row metadata to both renderers; wire the Preview surface |
| `web/static/js/terminals.js` | `openGenericConfirmModal` gains an optional `owner`; add `closeGenericConfirmModalForOwner(owner)`; close/switch flows call the cancellation hook |
| `web/static/css/terminals.css` | Separator, disabled, danger, busy, context-target highlight, failure bar — all from `tokens.css` variables |
| `templates/terminals.html` | Load `explorer-fs.js` after `explorer-search.js`, before `terminals.js` |
| `tests/test_api.py` | Add `js/explorer-fs.js` to `GuardrailAuditFixesTestCase.STATIC_JS` (`:10133`) |

A substantial new frontend surface gets its own file (guardrail 6) — it does not go back
into `terminals.js`, and it does not bloat `explorer-viewer.js` beyond the menu
generalization that genuinely belongs there. All page scripts share one global scope, so
`explorer-fs.js` calls `reloadExplorerTree`, `loadExplorerPane`, `openGenericConfirmModal`,
`showTerminalToast` and the edit-state helpers directly, exactly as `explorer-editor.js`
already does.

### 5.2 Menu generalization (`explorer-viewer.js`)

`showExplorerContextMenu(x, y, items)` (`:1147`) gains a richer item model:

```js
{ label, action, disabled, danger, separatorBefore, title }
```

* Disabled items render as `<button disabled>` with `aria-disabled="true"`, are skipped by
  `_explorerContextMenuKeydown` arrow navigation (`:1123`), and never receive initial focus.
* `separatorBefore` emits a `role="separator"` `<div>`.
* Labels are set with `textContent` (already the case at `:1156`) so a filename containing
  quotes or markup-like text is inert. Long labels ellipsize in CSS; `title` and the
  accessible name carry the full action.
* Initial focus goes to the first **enabled, non-danger** item, so Delete is never focused
  on open.
* On dismiss, focus returns to the invoking row.
* Keyboard invocation: when `event.clientX <= 0 && event.clientY <= 0` (Shift+F10 / Menu
  key), position from the row's `getBoundingClientRect()` instead of the pointer.

`handleExplorerCopyPathMenu`/`wireExplorerCopyPathMenu` are renamed to
`handleExplorerContextMenu`/`wireExplorerContextMenu`. The handler asks
`explorerFilesystemMenuItems(index, rowContext)` (in `explorer-fs.js`) for the mutation
items and appends the existing Copy path / Copy relative path items. **The Git panel keeps
path-copy only** — its rows expose no `data-explorer-context-kind`, so no filesystem item is
built for them.

### 5.3 Row metadata

Both renderers emit the same attributes:

```html
data-explorer-context-path="src/app.py"
data-explorer-context-kind="file"        <!-- server entry_kind, never inferred -->
data-explorer-context-revision="…"
data-explorer-context-surface="tree"     <!-- or "preview" -->
```

* `explorerTreeRowHtml` (`:1778`): add to the existing `.explorer-tree-row` wrapper, which
  already carries `data-explorer-copy-path`.
* `explorerDirectoryRowHtml` (`:3454`): add to the `.explorer-row` button, and add
  `data-explorer-copy-path` so Copy path/Copy relative path work in Preview too.
* Deleted Git placeholder rows (`entry.deleted`) and rows whose `entry_kind` is `link` or
  `other` get **no** mutation items — Copy is omitted, Delete is omitted for `link`/`other`
  only when the entry cannot be deleted (links can, so Delete stays for them; devices and
  sockets are rejected server-side if they somehow appear).
* Kind is never inferred from a CSS class or a filename extension.

### 5.4 Preview wiring

`wireExplorerContextMenu` is called on `#explorer-list-<index>` — **not**
`#explorer-viewer-<index>`, which `explorerEnsureViewerShell` (`:4295`) replaces wholesale.
The `dataset.contextMenuWired` guard then survives every re-render. The existing tree and
Git panels keep their current wiring points.

Right-click binds to the nearest row with `data-explorer-context-path`, adds a temporary
context-target highlight class, and calls `preventDefault()` so the row's normal
open/navigate click never fires. Right-clicking a nested control (**Open in tab**, **Open
folder**) targets the containing row without invoking that control.

### 5.5 Action context and identity

`wireExplorerContextMenu(panel, index)` closes over `index`. The panel's DOM id contains the
index so the node's index is stable, but **the session at that index is not** — group
switches and pane close/rebuild reuse indexes. Every menu invocation therefore snapshots an
immutable context:

```js
{ sessionId: sessionIds[index], paneRef: terminals[index], rootRevision,
  index, path, kind, revision, surface, token }
```

`isExplorerFsActionContextCurrent(ctx)` re-verifies **after every `await`** — before opening
a confirmation, before dispatching, and before applying a response — that
`sessionIds[ctx.index] === ctx.sessionId`, `terminals[ctx.index] === ctx.paneRef`, the pane
is still an Explorer pane on the same `rootRevision`, the token is current, and the session
is not closing. Any failure cancels silently and refreshes nothing.

### 5.6 Clipboard

Page-scope `Map<sessionId, entry>`; one entry per session:

```js
{ sessionId, rootRevision, path, kind, name, revision, copiedAt }
```

* Copy writes only under the immutable session id, so a Files-tree copy pastes in that
  session's Preview and vice versa, and nothing is visible or usable in another session.
* Copy does **not** touch the OS clipboard and does not disturb the value last written by
  **Copy path** (`_copyText`, `terminals.js:5905`).
* Cleared on: session close, root change, and a successful Delete of the source or one of
  its ancestors. Never persisted — not to `saved_sessions.json`, not to
  `runtime_state.json`, not to `localStorage`.
* Reusable after a successful paste. A `404`/`409` on paste clears it and returns the menu
  to **Paste — nothing copied**.

### 5.7 Paste destination

| Right-click target | Destination |
|---|---|
| Directory row (either surface) | Inside that directory |
| File row in Preview | The directory currently shown in Preview |
| File row in the Files tree | The file's root-relative parent |

Labels use the copied name and destination folder name — `Paste "report.md" into
"archive"`, or `Paste "report.md" in containing folder` for a file row, with the full
destination in `title`. With no clipboard entry for **this** session the item is present,
disabled, gray, unfocusable, labeled **Paste — nothing copied**, and titled
`Copy a file or folder in this Explorer session first`. A clipboard in another session
leaks neither its name nor its existence.

Background/empty-surface Paste (Preview background → current directory, tree background →
root) is **deferred** to a follow-up: it needs a hit-target model for the empty listing and
the tree's whitespace, and it is not required for the feature to be usable.

### 5.8 Paste and Delete flows

**Busy.** `pane._explorerFsBusy = { token, label }` is set **synchronously, before the first
`await`**, so a double-click or repeated activation starts at most one operation. It toggles
a CSS class on the pane card (guardrail 8 — never rewrites button markup) and shows
`Copying folder…` / `Deleting…`. Unrelated panes are untouched.

**Delete confirmation** always goes through `openGenericConfirmModal` — no
`window.confirm`/`prompt`/`alert` anywhere, including browser-only paths
(`GuardrailAuditFixesTestCase` enforces this):

* File — `Permanently delete "name"?`
* Empty directory — `Permanently delete the folder "name"?`
* Non-empty directory — `Permanently delete "name" and all of its contents?`

with `confirmLabel: "Delete"`, `danger: true`, the exact root-relative path in the copy, a
note that GridVibe cannot undo this, Cancel focused, and Escape/backdrop as cancellation.

**Owner-scoped modals.** `openGenericConfirmModal` gains an optional `owner`, and a new
`closeGenericConfirmModalForOwner(owner)` resolves *only* that owner's promise. Explorer
uses `explorer-fs:<sessionId>:<token>`. Without this, a session closing while a delete
confirmation is open would resolve whatever confirmation happened to be current.

**Dirty-edit protection.** Before dispatching a Delete, if the pane has a dirty edit
(`explorerEditState(pane).dirty`) whose `path` equals the target or sits under it, run the
existing `confirmDiscardExplorerEdit(index, 'Deleting this folder')` first. A cancelled
discard cancels the Delete. A dirty edit *outside* the target is left alone.

**On Paste success:** reload the tree preserving valid expansion state, refresh Preview if
it shows the destination, invalidate the Git sidebar if open, preserve the directory-search
query and scroll, briefly highlight the new entry, and toast. Because v1 never overwrites,
no open tab can silently change content.

**On Delete success:** close Preview/pinned tabs for the deleted path and its descendants;
if Preview is browsing the deleted directory, navigate to its surviving parent; drop deleted
paths from tree caches and expansion state; reload the tree and the affected Preview
directory; refresh Git if open; clear the clipboard if the source or an ancestor was
deleted; toast.

One shared `refreshExplorerAfterFilesystemMutation(ctx, result)` built on
`reloadExplorerTree` and `loadExplorerPane` does all of the above — no duplicated
local/remote logic, no second refresh implementation.

**On failure:** render an `explorer-fs-bar` in the pane, mirroring `explorerEditBarHost`
(`explorer-editor.js:421`) — message, **Retry** *only* when the response says
`mutated: false`, **Refresh** otherwise, and a dismiss control. An irreversible Delete never
retries automatically.

### 5.9 Lifecycle

`cancelExplorerFilesystemUiForSession(sessionId)` dismisses that session's menu, removes the
target highlight, resolves its owned confirmation as `false`, and clears its clipboard. It
is called from `closeTerminalPane` (`terminals.js:5731`), `closeSessionGroup` (`:6949`),
`teardownCurrentGrid`/`dropCachedGroupView`, and the group-switch path (`:6862`).

`hasActiveExplorerFilesystemOperation(index)` and its group-level counterpart let those same
flows explain "a copy/delete is finishing" and wait, instead of tearing down a pane whose
mutation the server already accepted.

| Event | Behavior |
|---|---|
| Session/pane closes while its menu is open | Dismiss, clear highlight, clear that session's clipboard |
| Session/pane closes while Delete confirmation is open | Owner-scoped resolve as `false`; **no request is sent** |
| User initiates close while Delete confirmation is open | Cancel Delete first, then continue the existing dirty-edit/live-session close flow |
| Session closes after confirm, before fetch | Post-confirm identity check fails; no request is sent |
| Local close requested while a request is in flight | Keep the session open until it settles, with an in-page explanation |
| Session removed externally mid-flight | Suppress stale DOM updates; treat the outcome as unknown (Refresh/Inspect), since an accepted OS/SFTP operation cannot be cancelled |
| Group switched/cached mid-flight | Never update the newly visible pane by index; mark the originating pane stale and refresh on restore |
| Index reused for another pane | Session-id/pane-ref checks prevent the old action or response from touching the new pane |

### 5.10 Styling

New rules in `terminals.css` for separator, disabled item, danger item, pane-busy, the
context-target highlight and the failure bar — every color, radius and spacing value from
`tokens.css` variables, no palette literals (`StyleThemingTestCase` enforces this). Any icon
is a stroke-style `currentColor` SVG; no emoji, no text glyphs
(`GuardrailAuditFixesTestCase.BANNED_GLYPHS`).

---

## 6. Test plan

### `web/explorer_fs.py` unit tests (no route, no repo, no SSH) — new `tests/test_explorer_fs.py`

* `-Copy` name allocation: `report.md` → `report-Copy.md` → `report-Copy-2.md`; `archive` →
  `archive-Copy`; `.env` → `.env-Copy`; `archive.tar.gz` → `archive.tar-Copy.gz` (documented
  behavior of `splitext`).
* Relative-path normalization rejects `""`, `.`, `..`, `a/../..`, embedded NUL, and absolute
  paths.
* `_fs_revision` is stable for identical stat input and changes for each varied field.
* Pre-scan limits: entry count, depth, and byte bounds each raise `413` and report
  `mutated: false`.
* Pre-scan rejects a link/FIFO/socket anywhere in a copy tree and names the offending
  root-relative path.
* Claim conflicts: equal, ancestor, and descendant keys conflict; siblings do not;
  different namespaces with identical path strings do not.

### Local backend + route tests — `tests/test_api.py`

* Copy a regular file: content, returned path, ordinary mode bits preserved.
* Copy a directory recursively without following links.
* Copy into the same parent allocates `-Copy` and leaves the original untouched.
* Reject destination equal to or inside the source.
* Reject source/destination outside the root (including via a symlinked parent).
* Reject the root itself for Copy and Delete.
* **A symlink leaf is never dereferenced:** delete `link → target` removes the link and
  leaves `target` intact.
* Stale `source_revision` / `base_revision` → `409 entry_changed`.
* Stale `root_revision` → `409 root_changed`, checked *before* path resolution.
* Delete a file; delete an empty directory; recursive delete of a non-empty directory only
  with `recursive: true` (without it, `400`).
* Delete refuses `.git` at root and nested → `403 protected_path`.
* **A pre-created destination is never overwritten:** patch the name-allocation loop so an
  external file appears at the chosen name between the check and the create; assert the
  pre-existing content survives and the copy either takes the next suffix or returns `409`.
* **Failed copy cleanup removes only what it reserved:** force a mid-copy failure and assert
  the pre-existing sibling entries are untouched and the reserved path is gone.
* Overlapping claims return `409 operation_in_progress`; unrelated paths proceed.
* A delete claim blocks a concurrent `PUT /file` save under the same tree, and vice versa.
* A missing/non-explorer session returns `404`/`400` before any resolution.
* The body cannot name a different source session, root, or absolute path.
* Both routes are rejected by the cross-origin write guard (extend
  `CrossOriginWriteGuardTestCase`).
* `_explorer_save_claim` still serializes and releases without holding the lock
  (`tests/test_api.py:5605` must keep passing unchanged).

### SFTP parity tests (stubbed `sftp` object, as `SshSftpPoolTestCase` already does)

* File contents stream in bounded chunks — no full-buffer read.
* Directory copy recurses through one request's SFTP channel and reuses the pooled
  transport (no second handshake).
* `fs_create_exclusive` uses `"x+b"` and **never** falls back to `"wb"` on a destination.
* Depth-first removal order; quarantine rename then removal.
* Remote path escapes and remote links are rejected for copy.
* Simulated failures clean up the reserved path; a cleanup failure logs WARNING and returns
  `io_error` with `cleanup_incomplete`.
* Paramiko/SFTP exceptions map to structured codes without leaking raw remote text.

### Frontend contract tests (rendered-page assertions, the existing pattern)

* Tree rows and Preview rows both expose context path, kind, revision, surface; the entries
  response exposes `root_revision`.
* Git rows expose no `data-explorer-context-kind` (no filesystem items).
* Copy path / Copy relative path behavior is unchanged, and Preview now offers them.
* The disabled `Paste — nothing copied` item is rendered non-focusable and click-less.
* Menu labels are built with `textContent`, not interpolated HTML.
* Keyboard navigation skips disabled items; Escape/outside click dismiss; focus returns.
* Delete goes through `openGenericConfirmModal` with `danger: true` and an owner token.
* `closeTerminalPane`, `closeSessionGroup` and the group-switch path call
  `cancelExplorerFilesystemUiForSession`.
* Dirty-edit protection precedes a delete whose target contains the edited path.
* `js/explorer-fs.js` is added to `GuardrailAuditFixesTestCase.STATIC_JS`, so the
  no-`window.confirm`/no-emoji sweeps cover it, and to `ExtractedFrontendAssetsTestCase`.
* No palette literals in the new CSS (`StyleThemingTestCase`).

### Manual matrix

Browser and native WebView2; local and SSH Explorer; light and dark; a narrow sidebar and a
menu opened near each viewport edge; names with spaces, quotes, non-ASCII, leading dots and
multiple extensions; a read-only destination; remote disconnect mid-copy and mid-delete;
copy then navigate elsewhere before pasting; tree→Preview and Preview→tree; copy in one
session and confirm Paste stays disabled in another; open a Delete confirmation and close
the pane/session from every close entry point; double-click Paste; delete an open pinned
file, the active Preview file, and the parent of the browsed directory; Git sidebar refresh
after both operations.

---

## 7. Implementation sequence

Each step is independently reviewable and leaves the tree green.

1. **Entry metadata.** `entry_kind` + `revision` on both entry payloads, `root_revision` on
   the entries response, `_fs_revision`/`_fs_root_revision` pure helpers. Tests first.
2. **Claims.** Generalize `_explorer_save_claim` to namespaced, ancestor-aware keys; move
   `save_explorer_file_payload` onto the namespaced key. Signature and existing test
   unchanged.
3. **Backend hooks.** The ~9 `fs_*` methods on both backend classes, plus `fs_claim_namespace`.
4. **`web/explorer_fs.py`.** Resolver, validation, limits, naming, copy, delete, payloads,
   error classes. Full unit coverage before any route exists.
5. **Routes.** Two thin bodies in `web/api.py` + route/guard tests.
6. **Menu generalization** in `explorer-viewer.js` (disabled/danger/separator/title,
   keyboard skip, focus return, keyboard invocation) with Copy path behavior unchanged.
7. **Row metadata + Preview wiring** on `#explorer-list-<index>`.
8. **`web/static/js/explorer-fs.js`** — clipboard, action context, menu items, dispatch,
   refresh, failure bar.
9. **Lifecycle hooks** — owner-scoped confirm modal, `cancelExplorerFilesystemUiForSession`,
   `hasActiveExplorerFilesystemOperation`, wired into the close/switch flows.
10. **Styling** from tokens.
11. **Docs** — `CLAUDE.md`, `AGENTS.md`, `README.md`, `CHANGELOG.md`, and a pointer from the
    2026-07-25 proposal to this plan.
12. `make check` (or `python tests/run_tests.py` + `python -m ruff check .` on Windows).

---

## 8. Guardrail compliance

| Guardrail | How this plan complies |
|---|---|
| 1. Security | Same-origin defaults and the cross-origin write guard untouched; live session + `root_revision` + source/destination + entry kind all validated server-side; root escapes and link dereferences rejected by the parent-then-leaf resolver; existing SSH pool and host-key policy reused; no content, password or clipboard entry logged or persisted; no new Socket.IO emit |
| 2. Concurrency | Claims are check-then-act under one short mutex hold with **zero I/O under the lock**; no `connection_lock` or `SessionManager.lock` involvement, so the documented ordering is preserved by construction; every async boundary revalidates immutable session/pane identity |
| 3. Performance | Pooled SSH transport; bounded 1 MiB streaming with no full-buffer copies; no polling, busy-wait or sub-second timer; no CDN asset; explicit entry/depth/byte bounds on the synchronous routes |
| 4. Correctness | No shell command is built from any path (Python/SFTP APIs only); `openGenericConfirmModal` with owner-scoped cancellation, never a browser-native dialog; revision + root checks; no overwrite; no new config key, so explicit-CLI-beats-config is unaffected |
| 5. Dead code | Two fields added, both consumed (menu gating + server validation); both routes wired end-to-end; limits are constants, not dormant config keys; `-Copy` naming, claims and error codes all have callers and tests |
| 6. Architecture/DRY | Policy in `web/explorer_fs.py`, mechanics behind the two backend classes — the `explorer_search.py` pattern; `web/api.py` gains only two thin routes; one controller serves both UI surfaces; the new frontend surface gets its own JS file rather than growing `terminals.js` |
| 7. Styling | Separator/disabled/danger/busy/highlight/failure states all from `tokens.css`; stroke-style `currentColor` SVG only |
| 8. Interaction | Paste visibly disabled when empty and names both source and destination when enabled; Delete separated, danger-styled, confirmed with Cancel focused; busy toggles CSS classes; safe failures expose Retry, uncertain irreversible ones expose Refresh/Inspect |
| 9. Logging | Routine claim/cleanup/cancellation at DEBUG; partial deletion, failed cleanup and unknown outcomes at WARNING with root-relative paths; no ANSI, no file contents, no polling noise |
| 10. New features | Builds on the existing backend abstraction, same-origin HTTP routes, pooled SSH and vendored frontend; clipboard state is bounded and memory-only; no secret enters any new state file |

---

## 9. Acceptance criteria

* Right-clicking a real file or directory in the Files tree or the Preview listing opens the
  same GridVibe menu, by pointer or by Shift+F10 / Menu key.
* Copy in one surface pastes through the other within the same Explorer session; a copy in
  session A never enables Paste — or leaks a filename — in session B.
* With nothing copied in this session, Paste is visible, disabled, gray, unfocusable and
  labeled **Paste — nothing copied**.
* Paste never overwrites; collisions take `-Copy`, `-Copy-2`, … and an external process
  winning the chosen name is handled without replacing it.
* Delete is always confirmed, can never target the Explorer root or a `.git` directory, and
  removes a non-empty directory only with the explicit `recursive` flag.
* A symlink is unlinked, never followed; a copy source tree containing one is rejected with
  the offending path named.
* Local and SSH/SFTP follow one shared policy and one response contract.
* Clipboard entries, confirmations, requests, busy state and refreshes stay bound to the
  immutable session/root they originated from; no action survives a reused pane index.
* Dirty edits and open tabs are handled with no silent data loss or stale viewer state.
* Tree, Preview and Git views refresh after every successful mutation.
* Safe failures offer Retry; partial or uncertain failures offer Refresh/Inspect and never
  retry automatically.
* Existing Copy path / Copy relative path behavior is unchanged.
* `make check` passes, including the guardrail sweeps over the new JS file.

---

## 10. Explicitly out of scope for v1

Move, rename, create, upload; overwrite-on-paste; cross-session or cross-host transfer;
`Ctrl+C`/`Ctrl+V`/`Delete` accelerators; OS recycle bin / Move to Trash; multi-select;
drag-and-drop; background jobs with progress and cancellation; server-side idempotency
replay (D1); paste onto an empty-listing or tree background (§5.7); configurable limits
(D7); `.hg`/`.svn` protection (D6).

Each is a clean follow-up on top of this contract, and none of them is required to make
Copy / Paste / Delete safe and usable.

---

## 11. Implementation record

Implemented in the original GridVibe codebase on 2026-07-28.

### Shipped

* `web/explorer_fs.py` now owns the shared local/SFTP mutation policy: strict relative-path
  normalization, parent-then-leaf resolution, root/entry revision validation, bounded
  iterative pre-scans, extension-aware collision naming, exclusive destination
  reservation, 1 MiB streaming, best-effort metadata preservation, reserved-path cleanup,
  `.git`/root protection, quarantine-first recursive deletion, and structured
  `mutated`-aware errors.
* `web/explorer.py` now reports `entry_kind` + opaque entry revisions, exposes the local and
  SFTP `fs_*` mechanics, and uses one backend-namespaced, ancestor-aware claim registry for
  saves, copies, and deletes. The historical `_explorer_save_claim(session_id, claim_key)`
  signature remains intact, and no filesystem/SFTP I/O runs under its mutex.
* `web/api.py` exposes the two thin, same-origin-guarded routes from this plan and adds
  `root_revision` to directory listings. Mutation bodies reject unknown fields and cannot
  select another session, root, absolute path, destination name, or overwrite behavior.
* `web/static/js/explorer-fs.js` provides the transient per-session clipboard, immutable
  action identity, synchronous busy gating, paste/delete dispatch, dirty-edit protection,
  owner-scoped delete confirmation, stale-response suppression, retry/refresh failure bar,
  mutation refresh, clipboard invalidation, and pane/session lifecycle cancellation.
* `explorer-viewer.js` now uses one accessible context-menu model for Files-tree, Preview,
  and Git path-copy rows. Disabled/danger/separator/title states, keyboard navigation,
  focus return, keyboard-invocation positioning, server-supplied row metadata, and stable
  Preview delegation are wired. Git rows remain path-copy-only.
* `terminals.js`, `terminals.css`, and `templates/terminals.html` now provide owner-scoped
  generic confirmation, close/switch waiting for accepted mutations, token-driven busy /
  highlight / error states, and the new controller asset in domain load order.
* `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`, and the superseded 2026-07-25
  proposal now state the third bounded Explorer mutation contract explicitly. Move,
  create, rename, upload, overwrite-on-paste, and the other §10 items remain out of scope.

### Coverage and verification

* Added `tests/test_explorer_fs.py` with policy, local route, in-memory SFTP parity, and
  frontend contract coverage. It exercises naming, normalization, revisions, all three
  pre-scan limits, ancestor-aware/namespaced claims, bounded remote reads, mandatory
  `"x+b"` creation, collision safety, recursive copy/delete, stale root/entry guards,
  failed-copy cleanup, `.git` protection, and save/delete overlap.
* Extended the existing asset, no-native-dialog/no-emoji, and cross-origin guard suites to
  cover `explorer-fs.js` and both mutation routes.
* `make check` passed on Windows: Ruff clean; **766 tests passed, 6 skipped**. Three new
  live-filesystem symlink cases skip because the Windows test account lacks symlink
  privilege; they execute on platforms/accounts that permit symlink creation. Existing
  platform skips account for the remaining three.
* JavaScript syntax checks passed for `explorer-viewer.js`, `explorer-fs.js`, and
  `terminals.js`.

The §6 manual browser/WebView2 × local/SSH × theme/viewport matrix was not executed in this
automated implementation session and remains the release-candidate smoke-test checklist.
