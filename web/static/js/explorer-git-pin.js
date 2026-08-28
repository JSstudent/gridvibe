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
    function explorerGitScopeKind(kind) {
        return kind === 'file' ? 'file' : 'dir';
    }

    function explorerGitPathIsPinned(pinnedPath, path, pinnedKind = 'dir', pathKind = 'dir') {
        if (typeof pinnedPath !== 'string') {
            return false;
        }
        return pinnedPath === String(path === null || path === undefined ? '' : path)
            && explorerGitScopeKind(pinnedKind) === explorerGitScopeKind(pathKind);
    }

    /* How a path is named when it is talked *about* rather than browsed to.
       `''` is the explorer root and has no spelling of its own, so it borrows
       one; a non-string is not a path and gets no label at all. The sidebar's
       scope chip and the pin button's title both read this, so the word for
       the root is written once. */
    function explorerGitPinLabel(path, kind = 'dir') {
        if (path === null || path === undefined) {
            return '';
        }
        const value = String(path);
        if (explorerGitScopeKind(kind) === 'file') {
            return value.replace(/\\/g, '/').split('/').filter(Boolean).pop() || value;
        }
        return value || 'root';
    }

    /* The pin button asks "is the pin *here*", not "is there a pin".

       While the pin was invisible, the button's pressed state was the pane's
       only report that one existed, so it had to be a toggle on that
       existence — which made pinning a second folder a two-step gesture
       (unpin, navigate, pin) that passed through an unpinned scope and
       reloaded the whole graph for it. Now that the Files tree marks the
       pinned row and the repo bar names the scope, *where* the pin is has two
       surfaces that can say so, and the button is free to answer the question
       it is actually next to.

       Three states, one predicate — the same exact-equality one the tree's
       marker is painted from, so the marked row and the pressed button cannot
       disagree:

         here      pressed, clears the pin
         elsewhere not pressed, re-pins here in one write
         none      not pressed, pins here

       `pressed` stays bound to `here` because `aria-pressed` is binary; the
       third state is carried beside it by the caller's class (guardrail 7:
       never by colour alone), without which "no pin" and "pin elsewhere" look
       identical and nothing warns that a click is about to *move* something.

       `clearAvailable` asks "is there a pin", not "is the pin elsewhere", and
       so does not read `browsedPath` at all. It used to be false at the pin,
       on the grounds that the pressed button was already the clear there and
       a second control for one action is noise. But that took the *named*
       control away at exactly the folder a reader is standing in when they
       decide to unpin, leaving only the button -- whose press-to-clear reads
       as a toggle on the pin's existence, which is the reading this button
       was rewritten to stop making everywhere else. So Clear pin is offered
       wherever a pin is: one spelling of "remove this pin", reachable from
       the pinned folder and from anywhere else alike, and the button's own
       clear becomes a shortcut rather than the only way out. */
    function explorerGitPinButtonState(
        pinnedPath,
        browsedPath,
        pinnedKind = 'dir',
        browsedKind = 'dir'
    ) {
        const pinned = typeof pinnedPath === 'string';
        const targetKind = explorerGitScopeKind(browsedKind);
        const targetName = targetKind === 'file' ? 'file' : 'folder';
        const here = pinned && explorerGitPathIsPinned(
            pinnedPath, browsedPath, pinnedKind, targetKind
        );
        if (here) {
            return {
                state: 'here',
                pressed: true,
                title: `Clear pinned Git ${targetName}`,
                clearAvailable: true
            };
        }
        if (pinned) {
            return {
                state: 'elsewhere',
                pressed: false,
                title: `Pin Git to this ${targetName} (pinned: ${explorerGitPinLabel(pinnedPath, pinnedKind)})`,
                clearAvailable: true
            };
        }
        return {
            state: 'none',
            pressed: false,
            title: `Pin Git to the current ${targetName}`,
            clearAvailable: false
        };
    }

    /* What the repo bar says about the scope, as one row per control that
       chose one.

       They used to share a row: the row showed the *effective* scope, so with
       Follow on it named the browsed folder, wore the chain icon -- and still
       carried **Clear pin**, an action about a path that was not on the row.
       Nothing said where the pin was or what Follow was tracking, and the one
       button that could move something pointed at the wrong one of the two.

       So: a pin row whenever there is a pin, a Follow row whenever Follow is
       on, both when both are, and Clear pin only ever on the row that names
       the path it clears -- on that row whenever the pin exists, whether or
       not the pane is browsing it. Follow overrides a pin while it is on
       rather than replacing it -- turning Follow off lands back on the pin --
       so the pin row stays, saying so in its title rather than disappearing
       and reading as a pin that was lost.

       An unpinned, non-following pane is scoped to the explorer root by
       default and gets no row: naming the default on every pane is noise. A
       pin made *at* the root does get one, because a root pin and no pin ask
       the server for exactly the same thing -- without a word for it, pinning
       at the root round-trips perfectly and still reads as though the pin had
       been lost. */
    function explorerGitScopeLines(
        pinnedPath,
        browsedPath,
        following,
        pinnedKind = 'dir',
        browsedKind = 'dir'
    ) {
        const follow = Boolean(following);
        const lines = [];
        if (typeof pinnedPath === 'string') {
            const normalizedPinnedKind = explorerGitScopeKind(pinnedKind);
            const label = explorerGitPinLabel(pinnedPath, normalizedPinnedKind);
            lines.push({
                kind: 'pin',
                scopeKind: normalizedPinnedKind,
                label,
                title: follow
                    ? `Git scope pinned to: ${label} (overridden while Follow is on)`
                    : `Git scope pinned to: ${label}`,
                overridden: follow,
                /* Present wherever the pin is, the pinned folder included:
                   a clear that hides itself where the reader happens to be
                   standing is not something they can reach for. Read off the
                   button's own state so the two surfaces cannot disagree
                   about whether there is anything to clear. */
                clearAvailable: explorerGitPinButtonState(
                    pinnedPath,
                    browsedPath,
                    normalizedPinnedKind,
                    browsedKind
                ).clearAvailable
            });
        }
        if (follow) {
            const normalizedBrowsedKind = explorerGitScopeKind(browsedKind);
            const label = explorerGitPinLabel(
                browsedPath === null || browsedPath === undefined ? '' : String(browsedPath),
                normalizedBrowsedKind
            );
            lines.push({
                kind: 'follow',
                scopeKind: normalizedBrowsedKind,
                label,
                title: `Git scope follows the browsed ${normalizedBrowsedKind === 'file' ? 'file' : 'folder'}: ${label}`,
                overridden: false,
                clearAvailable: false
            });
        }
        return lines;
    }

    /* One path-scoping entry for a Files-tree row or an open explorer tab.
       Multi-selection deliberately gets no entry: a pin is one exact path,
       never a batch operation.  A row outside a worktree keeps the entry in
       place but disabled, so the menu explains why it cannot be selected. */
    function explorerGitScopeMenuItem({
        pinnedPath = null,
        pinnedKind = 'dir',
        targetPath = '',
        targetKind = 'dir',
        targetCount = 1,
        worktreeAvailable = true
    } = {}) {
        if (targetCount !== 1) {
            return null;
        }
        const normalizedKind = explorerGitScopeKind(targetKind);
        const targetName = normalizedKind === 'file' ? 'file' : 'folder';
        const here = explorerGitPathIsPinned(
            pinnedPath, targetPath, pinnedKind, normalizedKind
        );
        if (here) {
            return {
                action: 'unpin',
                label: 'Unpin Git',
                disabled: false,
                title: `Remove the Git pin from this ${targetName}`
            };
        }
        return {
            action: 'pin',
            label: 'Pin Git here',
            disabled: worktreeAvailable === false,
            title: worktreeAvailable === false
                ? `This ${targetName} is not inside a Git worktree`
                : `Pin Git to this ${targetName}`
        };
    }

    function explorerGitFollowMenuItem(following, disabled = false) {
        const active = Boolean(following);
        return {
            action: 'follow',
            label: active ? 'Unfollow Git browsing' : 'Follow Git browsing',
            disabled: Boolean(disabled),
            title: active
                ? 'Return Git to its fixed pinned or explorer-root scope'
                : 'Follow the file or folder shown in Preview'
        };
    }

    return {
        explorerGitPathIsPinned,
        explorerGitScopeKind,
        explorerGitPinLabel,
        explorerGitPinButtonState,
        explorerGitScopeLines,
        explorerGitScopeMenuItem,
        explorerGitFollowMenuItem
    };
}));
