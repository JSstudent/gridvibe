/* Quiet directory refresh, rendered membership baselines and asynchronous
   ownership. Rendering remains in explorer-viewer.js; classic-script helpers
   resolve at call time. Loaded after the viewer and tree. */

    /* ── Directory-revision baselines ───────────────────────────────────────
       These are the *independent*, Git-free change signal (ISSUE-2026-061): a
       fingerprint the backend computes from the raw directory child list, before
       Git decorations or synthetic deleted entries, so a child created/deleted
       or renamed outside GridVibe — inside an ignored folder or a non-Git root
       included — advances it where a Git status would not. Each /entries load
       records its returned `directory_revision` here, keyed by relative path,
       so the directory-state poll always compares against the filesystem the
       surface actually rendered. Runtime-only; a root change drops the whole
       map. */

    function explorerDirectoryBaselines(pane, surface = 'listing') {
        if (!pane) {
            return null;
        }
        const field = surface === 'tree' ? '_explorerTreeDirRevisions' : '_explorerDirRevisions';
        if (!(pane[field] instanceof Map)) {
            pane[field] = new Map();
        }
        return pane[field];
    }

    function clearExplorerDirectoryBaselines(pane) {
        if (pane) {
            pane._explorerDirRevisions = new Map();
            pane._explorerTreeDirRevisions = new Map();
            pane._explorerDirWatchPending = null;
            pane._explorerDirectoryEpoch = (pane._explorerDirectoryEpoch || 0) + 1;
            pane._explorerTreeEpoch = (pane._explorerTreeEpoch || 0) + 1;
            pane._explorerRootEpoch = (pane._explorerRootEpoch || 0) + 1;
            if (pane._explorerTreeChildren instanceof Map) pane._explorerTreeChildren = new Map();
            if (pane._explorerTreeErrors instanceof Map) pane._explorerTreeErrors = new Map();
        }
    }

    function explorerDirectoryBaselineRevision(pane, path, surface = 'listing') {
        if (!pane) {
            return undefined;
        }
        const key = String(path || '');
        const baselines = explorerDirectoryBaselines(pane, surface);
        return baselines.has(key) ? baselines.get(key) : undefined;
    }

    function recordExplorerDirectoryRevision(pane, path, revision, surface = 'listing') {
        if (!pane) {
            return;
        }
        const baselines = explorerDirectoryBaselines(pane, surface);
        const key = String(path || '');
        const next = typeof revision === 'string' && revision ? revision : undefined;
        if (next === undefined) {
            baselines.delete(key);
        } else {
            baselines.set(key, next);
            // A fresh authoritative listing re-arms a directory watch suspended
            // after repeated failures, exactly as a new file baseline re-arms
            // the open-file watch.
            if (!pane._explorerFsWatchRefreshing) {
                pane._explorerDirWatchFailures = 0;
                pane._explorerDirWatchSuspended = false;
            }
        }
    }

    function explorerDirectoryRenderedRevisions(pane, path) {
        const revisions = [];
        if (pane._explorerMode === 'directory' && (pane._explorerPath || '') === path) {
            revisions.push(explorerDirectoryBaselineRevision(pane, path));
        }
        if (pane._explorerTreeSidebarOpen && pane._explorerTreeChildren?.has(path)
            && (path === '' || pane._explorerTreeExpanded?.has(path))) {
            revisions.push(explorerDirectoryBaselineRevision(pane, path, 'tree'));
        }
        return revisions;
    }

    function captureExplorerDirectoryOwner(index) {
        const pane = terminals[index];
        if (!pane || !sessionIds[index]) return null;
        return {
            pane, sessionId: sessionIds[index], root: pane._explorerRootRevision || '',
            rootEpoch: pane._explorerRootEpoch || 0,
            epoch: pane._explorerDirectoryEpoch || 0,
            path: pane._explorerPath || '', mode: pane._explorerMode,
            tab: pane._explorerActiveTabId, entries: pane._explorerEntries,
            tree: pane._explorerTreeChildren, treeEpoch: pane._explorerTreeEpoch || 0,
            expanded: pane._explorerTreeExpanded,
            expansion: JSON.stringify([...(pane._explorerTreeExpanded || [])].sort()),
            treeOpen: pane._explorerTreeSidebarOpen
        };
    }

    function explorerDirectoryOwnerCurrent(index, owner, { tree = false } = {}) {
        if (!owner) return false;
        const pane = owner.pane;
        return terminals[index] === pane && sessionIds[index] === owner.sessionId
            && (pane._explorerRootRevision || '') === owner.root
            && (pane._explorerRootEpoch || 0) === owner.rootEpoch
            && (pane._explorerDirectoryEpoch || 0) === owner.epoch
            && (pane._explorerPath || '') === owner.path && pane._explorerMode === owner.mode
            && pane._explorerActiveTabId === owner.tab
            && (!tree || (pane._explorerTreeChildren === owner.tree
                && (pane._explorerTreeEpoch || 0) === owner.treeEpoch
                && pane._explorerTreeSidebarOpen === owner.treeOpen
                && pane._explorerTreeExpanded === owner.expanded
                && JSON.stringify([...(pane._explorerTreeExpanded || [])].sort()) === owner.expansion));
    }

    function explorerDirectoryApplyAllowed(index, owner, tree = false) {
        return explorerDirectoryOwnerCurrent(index, owner, { tree })
            && document.visibilityState === 'visible'
            && !owner.pane._explorerFsBusy && !owner.pane._explorerGitActionBusy
            && !explorerFsWatchDeferralActive(index);
    }

    /* Baseline reset for the filesystem-surface listener. Clearing the
       Git-decoration baseline (and re-arming) is separate from the independent
       directory baselines above, which this must never erase: a user-initiated
       load re-records them from the /entries it just read. */
    function resetExplorerFsWatchBaseline(pane) {
        if (!pane) {
            return;
        }
        pane._explorerFsWatchRevision = '';
        pane._explorerFsWatchPending = null;
        pane._explorerFsWatchFailures = 0;
        pane._explorerFsWatchSuspended = false;
        // Pending membership work may still belong to another rendered surface.
        pane._explorerDirWatchFailures = 0;
        pane._explorerDirWatchSuspended = false;
    }

    /* Everything the listing and tree rows actually render, hashed. A poll
       that fires for a change outside the browsed directory (or outside the
       expanded tree) therefore costs one fetch and zero repaints. */
    function explorerEntriesSignature(entries) {
        if (!Array.isArray(entries)) {
            return '';
        }
        return explorerHashText(JSON.stringify(entries.map(entry => [
            entry.path || '',
            entry.name || '',
            entry.type || '',
            entry.entry_kind || '',
            entry.deleted ? '1' : '',
            entry.size == null ? '' : String(entry.size),
            entry.modified == null ? '' : String(entry.modified),
            entry.revision || '',
            entry.git?.status || '',
            entry.git?.index_status || '',
            entry.git?.worktree_status || ''
        ])));
    }

    async function explorerFetchEntriesQuiet(index, path) {
        const sessionId = sessionIds[index];
        if (!sessionId) {
            return null;
        }
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/entries?path=${encodeURIComponent(path || '')}`,
                { cache: 'no-store' }
            );
            const data = await response.json();
            return response.ok ? data : null;
        } catch (error) {
            return null;
        }
    }

    /* Re-list the browsed directory in place. Only `_explorerEntries` and the
       rows change; the Preview tab, its Find query, and the list scroll offset
       are all preserved, so a new file simply appears where it belongs. */
    async function refreshExplorerDirectoryQuiet(index, owner = captureExplorerDirectoryOwner(index)) {
        if (!explorerDirectoryOwnerCurrent(index, owner)) return false;
        const { pane, sessionId } = owner;
        if (!pane || !sessionId || pane._explorerMode !== 'directory') {
            return true; // Nothing to re-list is not a failure.
        }
        const path = pane._explorerPath || '';
        const data = await explorerFetchEntriesQuiet(index, path);
        if (!data) {
            return false;
        }
        if (!explorerDirectoryApplyAllowed(index, owner)
            || (data.root_revision || '') !== owner.root
            || pane._explorerEntries !== owner.entries) {
            return false;
        }
        const entries = Array.isArray(data.entries) ? data.entries : [];
        if (explorerEntriesSignature(entries) === explorerEntriesSignature(pane._explorerEntries)) {
            recordExplorerDirectoryRevision(pane, path, data.directory_revision || '');
            return true;
        }
        const list = document.getElementById(`explorer-list-${index}`);
        const scrollTop = list ? list.scrollTop : 0;
        const scrollLeft = list ? list.scrollLeft : 0;
        const previous = {
            entries: pane._explorerEntries, parent: pane._explorerParentPath,
            git: pane._explorerGitContext
        };
        pane._explorerEntries = entries;
        pane._explorerParentPath = data.parent_path || '';
        pane._explorerGitContext = data.git || null;
        try {
            updateExplorerGitSummary(index, data.git || null);
            renderExplorerDirectoryRows(index);
        } catch (error) {
            pane._explorerEntries = previous.entries;
            pane._explorerParentPath = previous.parent;
            pane._explorerGitContext = previous.git;
            return false;
        }
        recordExplorerDirectoryRevision(pane, path, data.directory_revision || '');
        if (list) {
            list.scrollTop = Math.min(scrollTop, Math.max(0, list.scrollHeight - list.clientHeight));
            list.scrollLeft = scrollLeft;
        }
        return true;
    }

    /* One entry point for the change listener. Returns false on any failure or
       staleness so the watcher can back off without advancing its baseline —
       the surfaces keep their last good contents either way. `dirs`, when set,
       scopes the tree half to exactly the directories whose state changed (the
       independent directory poll's dirty set); the Git-decoration consumer
       passes nothing and keeps the bounded shallowest-first plan. */
    async function refreshExplorerFilesystemSurfacesQuiet(index, options) {
        const owner = captureExplorerDirectoryOwner(index);
        const pane = owner?.pane;
        if (!pane || pane._explorerFsWatchRefreshing) {
            return false;
        }
        pane._explorerFsWatchRefreshing = true;
        try {
            const dirs = options?.dirs;
            const listing = !dirs || dirs.includes(owner.path)
                ? await refreshExplorerDirectoryQuiet(index, owner) : true;
            if (!listing || !explorerDirectoryApplyAllowed(index, owner, true)) return false;
            const tree = await refreshExplorerTreeQuiet(index, dirs, owner);
            return listing && tree;
        } catch (error) {
            return false;
        } finally {
            pane._explorerFsWatchRefreshing = false;
        }
    }
