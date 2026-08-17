/* GridVibeExplorerGitActive — which Git-sidebar row is the one on screen.

   The sidebar lists the same path many times: once under Staged Changes, once
   under Changes, and once inside every commit that touched it. A path alone
   therefore cannot say which of those rows produced what the viewer is
   showing, so a row is matched on the *diff identity* it would open — a commit
   id for a history row, a diff mode for a change row — and never on the path
   by itself. Matching on the path would light up four rows for one file and
   point at the worktree while a 2019 commit is on screen.

   The reveal is the other half of the same problem. A commit diff that comes
   back with a restored workspace lands inside a commit row, and a collapsed
   commit row cannot show anything: the file is on screen with nothing in the
   sidebar naming it. So the commit that owns the open diff is expanded — once
   per pane per commit, so a row the user deliberately collapses afterwards
   stays collapsed — and only when the loaded graph actually carries that
   commit, which leaves one that has scrolled out of the log to be revealed by
   a later load rather than recorded as already done.

   DOM-free and require()-able from Node so the matching and the reveal
   decision are executed by tests rather than asserted as source text; the
   page's DOM adapter lives in explorer-viewer.js. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerGitActive = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The persisted expansion key. `explorer:` is the wire prefix the server's
       normalizer validates (`explorer:<7-64 hex>`), so it is spelled once here
       rather than at each call site that builds or reads one. */
    const COMMIT_KEY_PREFIX = 'explorer:';
    /* A change row with no explicit mode is an unstaged row, and a pane
       holding no diff mode of its own is showing the same worktree content. */
    const DEFAULT_DIFF_MODE = 'worktree';

    function text(value) {
        return typeof value === 'string' ? value : '';
    }

    function commitKey(hash) {
        const value = text(hash).trim();
        return value ? `${COMMIT_KEY_PREFIX}${value}` : '';
    }

    /* What the viewer is showing, in the sidebar's own vocabulary. A pane that
       is not on a file has no active row at all: a directory listing is not a
       diff, and keeping the highlight on the file it last showed would name
       something that is no longer on screen. */
    function viewerTarget(view) {
        if (!view || text(view.mode) !== 'file') {
            return null;
        }
        const path = text(view.filePath).trim();
        if (!path) {
            return null;
        }
        return {
            path,
            commit: text(view.diffCommit).trim(),
            diffMode: text(view.diffMode).trim()
        };
    }

    /* `row` is one sidebar row's identity: `commitHash` for a history row,
       `diffMode` for a change row. A change row is never active while a commit
       diff is on screen — the worktree copy and a past commit are different
       content for the same path — and a history row is active only for its own
       commit. */
    function rowIsActive(target, row) {
        if (!target || !row) {
            return false;
        }
        if (text(row.path).trim() !== target.path) {
            return false;
        }
        const rowCommit = text(row.commitHash).trim();
        if (rowCommit) {
            return rowCommit === target.commit;
        }
        if (target.commit) {
            return false;
        }
        const rowMode = text(row.diffMode).trim() || DEFAULT_DIFF_MODE;
        return (target.diffMode || DEFAULT_DIFF_MODE) === rowMode;
    }

    /* The commit row itself, so the graph shows which commit is open even when
       the file row inside it is scrolled past. */
    function commitIsActive(target, hash) {
        const value = text(hash).trim();
        return Boolean(target && value && target.commit === value);
    }

    /* `state` is what the pane knows right now: `expanded` (the persisted
       expansion keys), `revealed` (the commit this pane has already revealed,
       which is what stops a render from re-opening a row the user closed), and
       `commits` (the hashes the loaded graph carries).

       Returns `{ expandKey, revealedCommit }`. An empty `expandKey` means
       nothing has to be opened; an empty `revealedCommit` means the pane must
       record nothing, so a target that is not in the graph yet stays eligible
       for the next load rather than being written off. */
    function revealPlan(target, state) {
        const idle = { expandKey: '', revealedCommit: '' };
        const commit = target ? target.commit : '';
        if (!commit || commit === text(state && state.revealed).trim()) {
            return idle;
        }
        const commits = Array.isArray(state && state.commits) ? state.commits : [];
        if (!commits.some(hash => text(hash).trim() === commit)) {
            return idle;
        }
        const key = commitKey(commit);
        const expanded = Array.isArray(state && state.expanded) ? state.expanded : [];
        const alreadyOpen = expanded.some(entry => text(entry).trim() === key);
        return { expandKey: alreadyOpen ? '' : key, revealedCommit: commit };
    }

    return {
        COMMIT_KEY_PREFIX,
        DEFAULT_DIFF_MODE,
        commitKey,
        viewerTarget,
        rowIsActive,
        commitIsActive,
        revealPlan
    };
}));
