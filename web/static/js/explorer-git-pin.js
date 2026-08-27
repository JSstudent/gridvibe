/* GridVibeExplorerGitPin — where the Git scope pin is, as a policy.

   Two surfaces have to answer "is the pin *here*": the Files tree, which marks
   the pinned row, and the Graph header's pin button. Answering it twice is how
   a marked row and a pressed button come to disagree, so the predicate lives
   here once and both surfaces hold paint-only adapters
   (explorer-tree.js and explorer-git-sidebar.js).

   DOM-free and require()-able from Node so the rule is executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerGitPin = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Exact string equality, never an ancestor or prefix match: a pin on a
       parent folder is not a pin on this one, and treating it as one would
       mark the wrong row and let a control here act on a pin the user made
       somewhere else.

       `''` is the explorer root and is a real pin, so the *existence* of a pin
       is the type of `pinnedPath` — a string — and never its truthiness. */
    function explorerGitPathIsPinned(pinnedPath, path) {
        if (typeof pinnedPath !== 'string') {
            return false;
        }
        return pinnedPath === String(path === null || path === undefined ? '' : path);
    }

    return { explorerGitPathIsPinned };
}));
