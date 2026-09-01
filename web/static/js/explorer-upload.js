/* GridVibeExplorerUpload — what an upload targets, and what it refuses.

   Upload is download's mirror, so it is offered from every surface download is
   offered from: the file-view and image-view headers, the Files tree, the
   Preview listing (rows *and* blank space), the tab strip, and the Git
   sidebar's worktree rows. The explorer bar's own button is the one addition,
   because the bar is where the pane says what it is showing, and "put a file
   *here*" is the sentence a reader browsing a folder would say first. Those surfaces disagree about almost everything
   except one question — "which folder do the bytes land in?" — and that
   question is answered here, once, for all of them. A second copy of it in the
   header adapter and in the context menu is exactly how a right-click on a
   file would come to upload somewhere the header button does not.

   The rule the surfaces share: a *folder* names itself, a *file* names the
   folder it sits in, and blank space names the folder that surface is showing.
   That is why the answer is a destination and never a path — nothing here can
   name a file, so nothing here can be talked into overwriting one.

   The refusals are the other half. The backend validates every leaf again and
   never overwrites, so nothing here is load-bearing for safety; it exists so a
   file the server would refuse is named *before* N requests go out, rather
   than as failure N of N. Deliberately the same rules as `create`'s literal
   leaf, because they land in the same exclusive create.

   DOM-free and require()-able from Node, so the destination rule and the
   refusals are executed by tests rather than asserted as source text. The
   picker, the requests and the batch report live in explorer-fs.js. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerUpload = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The server's own ceiling, mirrored here only to name an oversized file
       before it is sent rather than after 100 MB of it has been. The server
       enforces it on the bytes that actually arrive; this is a courtesy. */
    const UPLOAD_MAX_BYTES = 100 * 1024 * 1024;

    /* Each file is its own request, so an accidental "select all" in a large
       folder would queue one round trip per entry. The same ceiling the
       multi-entry selection uses, for the same reason. */
    const UPLOAD_MAX_FILES = 500;

    /* Above this many files the batch asks first — the threshold a bulk
       download already lives by, so the two directions cost the same gesture. */
    const UPLOAD_CONFIRM_THRESHOLD = 10;

    const NAME_MAX_CHARS = 255;

    function normalizePath(path) {
        return String(path == null ? '' : path)
            .replace(/\\/g, '/')
            .replace(/^\/+|\/+$/g, '');
    }

    function baseName(path) {
        const parts = normalizePath(path).split('/').filter(Boolean);
        return parts.pop() || '';
    }

    function parentPath(path) {
        const parts = normalizePath(path).split('/').filter(Boolean);
        parts.pop();
        return parts.join('/');
    }

    /* Which folder an upload started from this surface lands in.

       `null` means "this surface cannot name one", and the caller withholds
       the affordance rather than guessing: falling back to the explorer root
       would put files somewhere the user was not looking, which is the one
       outcome an upload must never have. */
    function uploadDestination(context) {
        const surface = String(context?.surface || '');
        const kind = String(context?.kind || '');
        const path = normalizePath(context?.path);
        const listingPath = normalizePath(context?.listingPath);
        if (surface === 'tree-blank') {
            // The tree's blank space is the explorer root, which is a real
            // destination — '' here is an answer, not a missing one.
            return '';
        }
        if (surface === 'preview-blank') {
            return listingPath;
        }
        if (kind === 'directory') {
            return path;
        }
        if (surface === 'preview') {
            /* The listing's own folder rather than the row's parent. They
               agree for every row a listing can show, and reading the folder
               keeps this identical to where Paste puts a clipboard entry from
               the same right-click. */
            return listingPath;
        }
        if (
            surface === 'tree'
            || surface === 'tab'
            || surface === 'git'
            || surface === 'file-view'
            || surface === 'bar'
        ) {
            return path ? parentPath(path) : null;
        }
        return null;
    }

    /* The literal-leaf rules `create` already enforces server-side, in the
       order that produces the most useful sentence first. A file that fails
       one of these is named now instead of arriving as one more red row in a
       batch report. */
    function uploadNameError(name) {
        const value = String(name == null ? '' : name);
        if (!value) {
            return 'has no file name.';
        }
        if (value.includes('\0')) {
            return 'has a name containing NUL.';
        }
        if (value.includes('/') || value.includes('\\')) {
            return 'has a name with a path separator in it.';
        }
        if (value === '.' || value === '..') {
            return 'has a dot or parent name.';
        }
        if (/^[A-Za-z]:/.test(value)) {
            return 'has a drive-qualified name.';
        }
        if (value.length > NAME_MAX_CHARS) {
            return `has a name longer than ${NAME_MAX_CHARS} characters.`;
        }
        if (value.toLowerCase() === '.git') {
            return 'would be named .git.';
        }
        return '';
    }

    function uploadSizeError(size) {
        const bytes = Number(size);
        if (!Number.isFinite(bytes) || bytes < 0) {
            return '';
        }
        if (bytes > UPLOAD_MAX_BYTES) {
            return `is larger than the ${Math.round(UPLOAD_MAX_BYTES / (1024 * 1024))} MB upload limit.`;
        }
        return '';
    }

    /* Split one picked set into what will be sent and what will not.

       `files` are `{name, size}` shapes, never `File` objects, so the browser's
       picker and the native window's bridge can both be planned by the same
       code. Whatever handle the caller needs rides along untouched under
       `source`, and `uploadEntrySource()` below is the only thing that reads
       it back out — an accepted entry is *not* the picked object, and reading
       a handle straight off it is how a request comes to send `undefined`.

       Two files with one name are a real case — the native dialog can be
       pointed at a second folder — and both are sent: the server numbers a
       taken name (`report (1).pdf`) rather than refusing it, so the second one
       lands beside the first exactly as it would have if it had been picked on
       its own. Refusing it here would be this module inventing a restriction
       the server does not have. */
    function uploadPlan(files) {
        const accepted = [];
        const rejected = [];
        (files || []).forEach((file, position) => {
            /* The name as picked, never a basename taken off it. A picker
               hands back a leaf already; anything else arrived by a route
               nobody designed, and quietly rewriting `sub/dir.txt` into
               `dir.txt` would upload a file under a name the user never saw. */
            const name = String(file?.name == null ? '' : file.name);
            const reason = uploadNameError(name) || uploadSizeError(file?.size);
            if (reason) {
                rejected.push({ name: name || `File ${position + 1}`, reason });
                return;
            }
            if (accepted.length >= UPLOAD_MAX_FILES) {
                rejected.push({
                    name,
                    reason: `is past the ${UPLOAD_MAX_FILES}-file limit for one upload.`
                });
                return;
            }
            accepted.push({ name, size: Number(file?.size) || 0, source: file });
        });
        return { accepted, rejected };
    }

    /* One sentence for everything the plan refused, reported before the batch
       runs rather than folded into its result — these files were never sent,
       and saying so beside "uploaded 3 of 3" is what keeps that 3 honest. */
    function uploadRejectionMessage(rejected) {
        const rows = rejected || [];
        if (!rows.length) {
            return '';
        }
        if (rows.length === 1) {
            return `${rows[0].name} ${rows[0].reason}`;
        }
        return `${rows.length} files were not uploaded. ${rows[0].name} ${rows[0].reason}`;
    }

    /* What to add to a successful batch's report when the server had to pick a
       different name.

       A silent rename is the one outcome an upload must not have: the reader
       asked for `report.pdf`, the listing gained `report (1).pdf`, and nothing
       said the two are related. Reads the answers the server actually sent —
       `stored_name` is the name on disk, never one re-derived here.

       Results are the batch runner's rows (`{ok, data}`), so this stays a pure
       function of what came back and can be executed by tests. */
    function uploadRenameNote(results) {
        const renamed = (results || [])
            .filter(row => row && row.ok && row.data && row.data.renamed)
            .map(row => ({
                requested: String(row.data.requested_name || ''),
                stored: String(row.data.stored_name || '')
            }))
            .filter(row => row.requested && row.stored);
        if (!renamed.length) {
            return '';
        }
        if (renamed.length === 1) {
            return `${renamed[0].requested} was kept as ${renamed[0].stored} — the existing file is untouched.`;
        }
        return `${renamed.length} files were numbered to keep the existing ones — the first is ${renamed[0].stored}.`;
    }

    function destinationLabel(destination) {
        const value = normalizePath(destination);
        return value || 'the explorer root';
    }

    /* Past the threshold an upload asks first, and the question names the
       destination: the count is the cheap half of the mistake, and the folder
       is the expensive one. */
    function uploadConfirmCopy(accepted, destination) {
        const count = (accepted || []).length;
        if (count <= UPLOAD_CONFIRM_THRESHOLD) {
            return null;
        }
        return {
            title: `Upload ${count} files?`,
            confirmLabel: `Upload ${count}`,
            copy: `${count} files will be written into ${destinationLabel(destination)}.`
        };
    }

    /* What one accepted entry actually sends, resolved in one place.

       Two transports carry two different handles — a `File` from the browser's
       picker, a path from the native bridge — and an accepted entry wraps the
       picked object rather than being it. Both facts are easy to get wrong at
       the call site and neither is visible until a real file is chosen, so the
       resolution lives here with the rest of the upload's rules and is tested
       by running it. A raw picked object is accepted too, so a retry can pass
       either shape back.

       Returns `null` when the entry carries no usable handle: a request that
       cannot name its bytes must not be built at all. */
    function uploadEntrySource(entry) {
        if (!entry) {
            return null;
        }
        const carrier = entry.source || entry;
        const name = String(entry.name || carrier.name || '');
        const sourcePath = String(carrier.sourcePath || '');
        if (sourcePath) {
            return { name, sourcePath, blob: null };
        }
        return carrier.blob ? { name, sourcePath: '', blob: carrier.blob } : null;
    }

    function uploadMenuLabel(accepted) {
        const count = (accepted || []).length;
        return count > 1 ? `Upload ${count} files` : 'Upload file';
    }

    return {
        UPLOAD_MAX_BYTES,
        UPLOAD_MAX_FILES,
        UPLOAD_CONFIRM_THRESHOLD,
        NAME_MAX_CHARS,
        baseName,
        parentPath,
        destinationLabel,
        uploadDestination,
        uploadNameError,
        uploadSizeError,
        uploadPlan,
        uploadEntrySource,
        uploadRejectionMessage,
        uploadRenameNote,
        uploadConfirmCopy,
        uploadMenuLabel
    };
}));
