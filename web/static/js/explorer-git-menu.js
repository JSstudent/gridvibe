/* GridVibeExplorerGitMenu — what may be copied off a commit row, and what each
   control says about it.

   A commit row is not a filesystem entry: it names an immutable object in the
   repository, not a path under the explorer root. So it does not join the
   multi-entry selection (explorer-selection.js), it is never a batch target,
   and every affordance here is a *read* of data the sidebar already fetched —
   nothing here issues a request or touches anything on disk, which keeps it
   well inside the explorer's read-only contract.

   These two entries used to be a context menu of their own, opened by the
   right-click that also had an instant hover card competing with it for the
   same row. One gesture now answers both: the right-click opens the commit
   card, and these are the copy buttons inside it (explorer-git-graph.js says
   which of the card's rows hosts one). The module survives the move because
   what a click puts on the clipboard is a decision, not a paint — and it is
   the *only* answer to that question, so the card cannot offer a hash the
   menu would have refused.

   DOM-free and require()-able from Node so the affordances and the clipboard
   text they carry are executed by tests rather than asserted as source text.
   The page's DOM adapter lives in explorer-git-sidebar.js. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerGitMenu = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* A commit id is only ever the abbreviated or the full object id git
       printed. Anything else came from a malformed row, and copying it would
       put a value on the clipboard that no git command accepts. */
    const COMMIT_ID_PATTERN = /^[0-9a-fA-F]{7,40}$/;

    function text(value) {
        return typeof value === 'string' ? value : '';
    }

    /* The row carries the short hash it displays and, when the backend supplied
       one, the full object id. Copying prefers the full id — that is what a
       command or an issue reference wants — and falls back to the short one
       rather than offering nothing, because an older payload without
       `full_hash` still identifies the commit unambiguously enough to paste. */
    function commitId(commit) {
        const full = text(commit && commit.fullHash).trim();
        if (COMMIT_ID_PATTERN.test(full)) {
            return full;
        }
        const short = text(commit && commit.hash).trim();
        return COMMIT_ID_PATTERN.test(short) ? short : '';
    }

    /* The displayed subject keeps its `(HEAD -> main, tag: v1.2)` decoration;
       the copied message never does, so this reads `message` — the
       decoration-free subject the backend sends beside it — and nothing else.
       Deriving it by stripping a leading parenthesised group off `subject`
       would be a guess that cannot tell a ref decoration from a subject
       genuinely written as "(fix) …", and it would silently hand over the
       wrong text; a row without a message says so instead. */
    function commitMessage(commit) {
        return text(commit && commit.message).trim();
    }

    /* The card's two copy controls, keyed by the slot each one sits in: the
       card's message line, and whichever row explorer-git-graph.js tagged
       `copy: 'hash'`.

       An affordance whose value is missing is offered *disabled* rather than
       dropped, exactly as the menu entry it replaces was: the card keeps a
       stable shape and a row that lost its data says so, instead of the
       control quietly not being there. `copy` is the caller's clipboard
       function; it is injected so this stays DOM-free and the tests can
       observe the exact string each button would put on the clipboard. */
    function commitCopyActions(commit, copy) {
        const id = commitId(commit);
        const message = commitMessage(commit);
        const copyText = typeof copy === 'function' ? copy : () => {};
        return {
            hash: {
                key: 'hash',
                label: 'Copy commit hash',
                title: id ? `Copy ${id}` : 'This row has no commit id',
                disabled: !id,
                action: () => copyText(id)
            },
            message: {
                key: 'message',
                label: 'Copy commit message',
                title: message ? `Copy "${message}"` : 'This row has no commit message',
                disabled: !message,
                action: () => copyText(message)
            }
        };
    }

    return {
        COMMIT_ID_PATTERN,
        commitId,
        commitMessage,
        commitCopyActions
    };
}));
