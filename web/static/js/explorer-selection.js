/* GridVibeExplorerSelection — the explorer's multi-entry selection model.

   A selection is a *transient* view concern: nothing here is persisted, and
   nothing crosses an Explorer session. It exists so one context-menu action can
   name several entries; the mutations themselves stay the bounded, per-entry,
   revision-checked requests the backend already exposes (a batch is N atomic
   calls, never a new endpoint).

   A selection is bound to three things at once — the session id, the pane's
   root revision, and one surface ('preview' or 'tree'). Any of them changing
   drops the selection rather than carrying stale paths or revisions into an
   action: a revision-bound entry that outlived its revision would fail its
   server-side check anyway, and a selection spanning both surfaces has no
   single well-defined paste destination.

   DOM-free and require()-able from Node so the gesture and target rules are
   executed by tests, never asserted as source text. The page's DOM adapter
   lives in explorer-viewer.js; the batch runners live in explorer-fs.js. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerSelection = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Selecting is cheap, but every selected entry becomes one request in a
       batch. The ceiling keeps an accidental "select all" in a huge tree from
       queueing thousands of round trips; it is a UI bound, not a contract. */
    const MAX_SELECTED_ENTRIES = 500;

    /* Above this many files a bulk download asks first: each file is its own
       transfer, and in the native window each one opens its own Save dialog. */
    const DOWNLOAD_CONFIRM_THRESHOLD = 10;

    function baseName(path) {
        const parts = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        return parts.pop() || '';
    }

    /* An entry is only usable as an action target when it carries both a path
       and the revision the server will check it against. Anything else — a
       deleted row, a blank surface, a malformed dataset — is not selectable. */
    function normalizeEntry(entry) {
        if (!entry || typeof entry !== 'object') {
            return null;
        }
        const path = String(entry.path || '');
        const kind = String(entry.kind || '');
        const revision = String(entry.revision || '');
        if (!path || !revision || (kind !== 'file' && kind !== 'directory')) {
            return null;
        }
        return { path, kind, revision, name: baseName(path) };
    }

    function normalizeScope(scope) {
        const surface = String(scope?.surface || '');
        return {
            sessionId: String(scope?.sessionId || ''),
            rootRevision: String(scope?.rootRevision || ''),
            surface: surface === 'tree' ? 'tree' : 'preview'
        };
    }

    function emptySelection(scope) {
        const normalized = normalizeScope(scope);
        return {
            sessionId: normalized.sessionId,
            rootRevision: normalized.rootRevision,
            surface: normalized.surface,
            anchorPath: '',
            entries: []
        };
    }

    function isEmpty(selection) {
        return !selection || !selection.entries || selection.entries.length === 0;
    }

    function selectionSize(selection) {
        return isEmpty(selection) ? 0 : selection.entries.length;
    }

    function selectionPaths(selection) {
        return isEmpty(selection) ? [] : selection.entries.map(entry => entry.path);
    }

    function isSelected(selection, path) {
        const wanted = String(path || '');
        return Boolean(wanted) && selectionPaths(selection).includes(wanted);
    }

    /* A selection survives only while all three bindings still match. This is
       the single gate every caller goes through — there is no partial repair,
       because a half-valid selection is exactly the thing that would send a
       stale revision to a mutation endpoint. */
    function matchesScope(selection, scope) {
        if (isEmpty(selection)) {
            return false;
        }
        const normalized = normalizeScope(scope);
        return (
            selection.sessionId === normalized.sessionId
            && selection.rootRevision === normalized.rootRevision
            && selection.surface === normalized.surface
        );
    }

    /* Returns the selection if it is still valid for `scope`, otherwise null.
       Callers treat null as "act on the clicked row alone". */
    function scopedSelection(selection, scope) {
        return matchesScope(selection, scope) ? selection : null;
    }

    function withEntries(selection, scope, entries, anchorPath) {
        const normalized = normalizeScope(scope);
        const seen = new Set();
        const bounded = [];
        entries.forEach(entry => {
            const candidate = normalizeEntry(entry);
            if (!candidate || seen.has(candidate.path)) {
                return;
            }
            if (bounded.length >= MAX_SELECTED_ENTRIES) {
                return;
            }
            seen.add(candidate.path);
            bounded.push(candidate);
        });
        return {
            sessionId: normalized.sessionId,
            rootRevision: normalized.rootRevision,
            surface: normalized.surface,
            anchorPath: bounded.length ? String(anchorPath || '') : '',
            entries: bounded
        };
    }

    function indexOfPath(ordered, path) {
        for (let i = 0; i < ordered.length; i += 1) {
            if (ordered[i] && String(ordered[i].path || '') === String(path || '')) {
                return i;
            }
        }
        return -1;
    }

    /* Resolve a row click into the next selection plus whether the click should
       still perform its normal action (open the file / enter the folder).

       - plain click        → clears the selection and activates the row, so an
                              explorer with nothing selected behaves exactly as
                              it did before multi-select existed
       - ctrl/meta+click    → toggles that row, never activates
       - shift+click        → selects the anchor→row range, never activates

       Shift with no usable anchor (first gesture, or the anchor scrolled out of
       the filtered list) degrades to a toggle rather than selecting nothing. */
    function applyPointerSelection(selection, gesture, ordered) {
        const scope = normalizeScope(gesture);
        const entry = normalizeEntry(gesture?.entry);
        const additive = Boolean(gesture?.ctrlKey || gesture?.metaKey);
        const ranged = Boolean(gesture?.shiftKey);
        if (!entry) {
            // A row with no revision can still be opened; it just cannot be
            // selected, and it must not silently keep a foreign selection.
            return { selection: null, activate: !additive && !ranged };
        }
        if (!additive && !ranged) {
            return { selection: null, activate: true };
        }
        const current = scopedSelection(selection, scope);
        const rows = Array.isArray(ordered) ? ordered : [];
        if (ranged && current && current.anchorPath) {
            const from = indexOfPath(rows, current.anchorPath);
            const to = indexOfPath(rows, entry.path);
            if (from !== -1 && to !== -1) {
                const start = Math.min(from, to);
                const end = Math.max(from, to);
                const range = rows.slice(start, end + 1);
                // The anchor stays put so a second shift+click re-ranges from
                // the same origin instead of walking the selection along.
                return {
                    selection: withEntries(current, scope, range, current.anchorPath),
                    activate: false
                };
            }
        }
        const existing = current ? current.entries : [];
        if (current && isSelected(current, entry.path)) {
            const remaining = existing.filter(candidate => candidate.path !== entry.path);
            if (!remaining.length) {
                return { selection: null, activate: false };
            }
            // Deselecting the anchor hands the anchor to whatever is left, so a
            // following shift+click still has an origin.
            const anchor = current.anchorPath === entry.path
                ? remaining[remaining.length - 1].path
                : current.anchorPath;
            return {
                selection: withEntries(current, scope, remaining, anchor),
                activate: false
            };
        }
        return {
            selection: withEntries(current || emptySelection(scope), scope, [...existing, entry], entry.path),
            activate: false
        };
    }

    /* Should this keystroke drop the selection?

       Escape is the way out of a selection you no longer want, but it is a
       heavily shared key: a context menu, the in-place editor, a find bar, the
       two search boxes and every dialog and header menu on the page all mean
       something by it. So this only claims an Escape that is unmodified, not
       aimed at a text field, not already handled (`defaultPrevented` — the
       signal the editor and the find bars set), not owed to an open dialog or
       menu (`claimedElsewhere` — the ones that close on Escape without marking
       it), and that actually has a selection to clear. One Escape never costs
       two things, and an Escape with nothing selected stays free for whatever
       else the page wants to do with it. */
    function shouldClearOnEscape(context) {
        if (!context || context.key !== 'Escape') {
            return false;
        }
        if (context.defaultPrevented || context.claimedElsewhere) {
            return false;
        }
        if (context.altKey || context.ctrlKey || context.metaKey || context.shiftKey) {
            return false;
        }
        if (context.editableTarget) {
            return false;
        }
        return Boolean(context.hasSelection);
    }

    /* Resolve what a right-click acts on.

       Right-clicking a row that is part of the live selection acts on the whole
       selection; right-clicking anything else — another row, or blank space —
       collapses to that row alone (or to nothing). This is the standard file
       manager rule, and it is what stops a forgotten selection elsewhere in the
       tree from being swept into a delete the user thought was single. */
    function resolveContextTargets(selection, gesture) {
        const scope = normalizeScope(gesture);
        const entry = normalizeEntry(gesture?.entry);
        if (!entry) {
            return { selection: null, targets: [] };
        }
        const current = scopedSelection(selection, scope);
        if (current && isSelected(current, entry.path)) {
            return { selection: current, targets: current.entries.slice() };
        }
        return { selection: null, targets: [entry] };
    }

    /* Drop paths that a completed mutation removed (deleted, or moved away),
       including anything beneath a removed directory. Returns null once the
       selection empties so callers store one "nothing selected" value. */
    function dropPaths(selection, removedPaths) {
        if (isEmpty(selection)) {
            return null;
        }
        const removed = (removedPaths || [])
            .map(path => String(path || '').replace(/\\/g, '/').replace(/\/+$/, ''))
            .filter(Boolean);
        if (!removed.length) {
            return selection;
        }
        const survives = entry => !removed.some(prefix => (
            entry.path === prefix || entry.path.startsWith(`${prefix}/`)
        ));
        const remaining = selection.entries.filter(survives);
        if (!remaining.length) {
            return null;
        }
        const anchor = remaining.some(entry => entry.path === selection.anchorPath)
            ? selection.anchorPath
            : remaining[remaining.length - 1].path;
        return withEntries(selection, selection, remaining, anchor);
    }

    /* Collapse a target list to its topmost paths, dropping any entry that has
       a selected ancestor.

       This is a correctness requirement of running a batch as N atomic
       requests, not a tidiness pass. Every mutation endpoint checks the target
       against the revision captured when its row rendered, and an entry's
       revision covers its own stat — so removing `dir/child` bumps `dir`'s
       mtime and the later `dir` request fails `entry_changed`, while doing
       `dir` first makes the later `dir/child` request fail `source_missing`.
       Either order reports a spurious failure for work that did happen, because
       a recursive delete/copy/move of the ancestor already covers the
       descendant. Selecting both is a plain thing to do in the tree with a
       folder expanded, so the batch resolves it instead of surfacing it. */
    function topmostTargets(targets) {
        const entries = (targets || []).map(normalizeEntry).filter(Boolean);
        return entries.filter(entry => !entries.some(other => (
            other.path !== entry.path && entry.path.startsWith(`${other.path}/`)
        )));
    }

    /* ── Derived copy ───────────────────────────────────────────────────────
       The labels below are part of the model rather than the DOM adapter so a
       one-entry batch can be proven to read exactly like the single-entry
       action it replaced. */

    function describeTargets(targets) {
        const entries = (targets || []).map(normalizeEntry).filter(Boolean);
        const directories = entries.filter(entry => entry.kind === 'directory').length;
        return {
            count: entries.length,
            directories,
            files: entries.length - directories,
            firstName: entries.length ? entries[0].name : ''
        };
    }

    function targetsLabel(targets) {
        const { count, directories, files, firstName } = describeTargets(targets);
        if (count === 0) {
            return '';
        }
        if (count === 1) {
            return `"${firstName}"`;
        }
        if (directories && files) {
            return `${count} items`;
        }
        if (directories) {
            return `${count} folders`;
        }
        return `${count} files`;
    }

    /* The confirm dialog's path line. It is written with textContent into a
       shared single-line element, so a long batch is summarised rather than
       listed — enough names to recognise what is about to go, then a count. */
    function targetsCopyLine(targets, limit = 4) {
        const entries = (targets || []).map(normalizeEntry).filter(Boolean);
        if (!entries.length) {
            return '';
        }
        if (entries.length === 1) {
            return entries[0].path;
        }
        const shown = entries.slice(0, limit).map(entry => entry.name);
        const rest = entries.length - shown.length;
        return rest > 0 ? `${shown.join(', ')} +${rest} more` : shown.join(', ');
    }

    function deleteConfirmCopy(targets) {
        const { count, directories, files, firstName } = describeTargets(targets);
        if (count === 0) {
            return null;
        }
        if (count === 1) {
            // Byte-identical to the pre-multi-select single-entry wording.
            return {
                title: directories
                    ? `Permanently delete the folder "${firstName}" and all of its contents?`
                    : `Permanently delete "${firstName}"?`,
                confirmLabel: 'Delete'
            };
        }
        const noun = directories && files
            ? `${count} items`
            : (directories ? `${count} folders` : `${count} files`);
        return {
            title: directories
                ? `Permanently delete ${noun}, including every folder's contents?`
                : `Permanently delete ${noun}?`,
            confirmLabel: `Delete ${count}`
        };
    }

    function downloadConfirmCopy(targets) {
        const { count } = describeTargets(targets);
        if (count <= DOWNLOAD_CONFIRM_THRESHOLD) {
            return null;
        }
        return {
            title: `Download ${count} files?`,
            confirmLabel: `Download ${count}`
        };
    }

    /* One line for an N-request batch: what landed, what did not, and the first
       reason. Retry is offered only when *every* failure left the filesystem
       untouched — a partially applied batch must be re-inspected, not replayed. */
    function batchOutcome(verb, results) {
        const rows = Array.isArray(results) ? results : [];
        const succeeded = rows.filter(row => row && row.ok);
        const failed = rows.filter(row => row && !row.ok);
        const firstError = failed.length
            ? String(failed[0].error || 'The filesystem operation failed.')
            : '';
        const retryable = failed.length > 0 && failed.every(row => row.mutated === false);
        if (!failed.length) {
            return {
                ok: true,
                retryable: false,
                message: '',
                toast: `${verb} ${succeeded.length}`
            };
        }
        if (!succeeded.length) {
            return {
                ok: false,
                retryable,
                message: rows.length === 1
                    ? firstError
                    : `None of the ${rows.length} entries could be handled. ${firstError}`,
                toast: ''
            };
        }
        return {
            ok: false,
            retryable,
            message: `${verb} ${succeeded.length} of ${rows.length}; ${failed.length} failed. ${firstError}`,
            toast: ''
        };
    }

    return {
        MAX_SELECTED_ENTRIES,
        DOWNLOAD_CONFIRM_THRESHOLD,
        baseName,
        normalizeEntry,
        emptySelection,
        isEmpty,
        selectionSize,
        selectionPaths,
        isSelected,
        matchesScope,
        scopedSelection,
        applyPointerSelection,
        shouldClearOnEscape,
        resolveContextTargets,
        dropPaths,
        topmostTargets,
        describeTargets,
        targetsLabel,
        targetsCopyLine,
        deleteConfirmCopy,
        downloadConfirmCopy,
        batchOutcome
    };
}));
