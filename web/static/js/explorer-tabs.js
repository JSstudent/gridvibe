/* GridVibe explorer tab strip — extracted from explorer-viewer.js by the
   move-only tab-domain split (guardrail 6's standing extraction trigger).

   Owns the explorer's tabbed viewer: the tab records themselves (one permanent
   dynamic "Preview" tab plus deduplicated pinned tabs keyed by normalized
   path), the strip that draws them, its interactions (activate, close,
   middle-click, drag-reorder, Preview promotion, reveal-in-tree), the per-tab
   view snapshot that survives a tab switch, and the saved-session persistence
   that carries all of it across a restore.

   A move, not a rewrite: every function below is byte-for-byte the one that
   stood in explorer-viewer.js, including its four-space indentation, so the
   diff reads as a relocation. Both files are plain classic scripts sharing one
   global scope, so the split costs no accessor plumbing — the viewer still
   calls renderExplorerTabStrip() and this file still calls openExplorerFile()
   exactly as before. Loaded directly after explorer-viewer.js.

   What deliberately stayed behind: explorerEnsureViewerShell() and the
   breadcrumb renderer (viewer chrome that happens to sit in the same band),
   openExplorerViewer(), the Markdown link and appearance clusters, and
   renderExplorerImage/renderExplorerFile/openExplorerFile — those take a `tab`
   argument but are file-render code, not tab code. */

    /* ─────────────────────────────────────────────
       Explorer tabbed viewer (ISSUE-2026-014)
       The main pane is always a read-only viewer with a persistent tab strip:
       one permanent dynamic "Preview" tab plus deduplicated pinned tabs keyed
       by normalized path. The Files tree is the navigation surface.
    ───────────────────────────────────────────── */
    const EXPLORER_PREVIEW_TAB_ID = '__preview__';
    const EXPLORER_MAX_PINNED_TABS = 12;
    const EXPLORER_MAX_TAB_PATH_LENGTH = 4096;

    function explorerBaseName(path) {
        return String(path || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || '';
    }

    /* Normalize a path into a stable dedup key: forward slashes, no leading or
       trailing slash, collapsed separators. Empty for unusable input. */
    function explorerNormalizeTabPath(path) {
        const value = String(path == null ? '' : path).replace(/\\/g, '/').trim();
        if (!value || value.length > EXPLORER_MAX_TAB_PATH_LENGTH) {
            return '';
        }
        return value.replace(/\/{2,}/g, '/').replace(/^\/+/, '').replace(/\/+$/, '');
    }

    function ensureExplorerTabState(pane) {
        if (!Array.isArray(pane._explorerTabs) || !pane._explorerTabs.length) {
            pane._explorerTabs = [{ id: EXPLORER_PREVIEW_TAB_ID, pinned: false, path: '', name: '' }];
        }
        if (!pane._explorerActiveTabId || !pane._explorerTabs.some(tab => tab.id === pane._explorerActiveTabId)) {
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
        }
        return pane._explorerTabs;
    }

    function explorerPreviewTab(pane) {
        ensureExplorerTabState(pane);
        return pane._explorerTabs.find(tab => tab.id === EXPLORER_PREVIEW_TAB_ID) || pane._explorerTabs[0];
    }

    /* Seed consumers that must know the restored browsing directory before the
       async tab restore runs (notably a Git sidebar restored in Follow mode).
       `null` means no directory was persisted; an empty string is a real saved
       explorer-root directory. */
    function explorerPersistedPreviewDirectory(session) {
        const rawViews = session?.explorer_tab_views;
        if (!rawViews || typeof rawViews !== 'object') {
            return null;
        }
        const rawPreview = rawViews[EXPLORER_PREVIEW_TAB_ID];
        if (
            !rawPreview
            || typeof rawPreview !== 'object'
            || !Object.prototype.hasOwnProperty.call(rawPreview, 'dir')
        ) {
            return null;
        }
        return explorerNormalizeTabPath(rawPreview.dir);
    }

    /* A mode-switch response carries the terminal's freshly resolved cwd
       relative to its freshly resolved explorer root. That path must win over
       the saved Preview directory, whose relative value belongs to the root the
       explorer used before the terminal moved. Initial page/workspace restores
       carry no transient override and continue restoring their saved view. */
    function explorerInitialPreviewDirectory(session) {
        if (session && Object.prototype.hasOwnProperty.call(session, 'explorer_open_path')) {
            return explorerNormalizeTabPath(session.explorer_open_path);
        }
        return explorerPersistedPreviewDirectory(session) ?? undefined;
    }

    function explorerFindTab(pane, id) {
        ensureExplorerTabState(pane);
        return pane._explorerTabs.find(tab => tab.id === id) || null;
    }

    function explorerActiveTab(pane) {
        ensureExplorerTabState(pane);
        return explorerFindTab(pane, pane._explorerActiveTabId) || explorerPreviewTab(pane);
    }

    function explorerTabLabel(tab) {
        if (!tab) {
            return 'Preview';
        }
        if (tab.id === EXPLORER_PREVIEW_TAB_ID) {
            return tab.path ? (explorerBaseName(tab.path) || 'Preview') : 'Preview';
        }
        return tab.name || explorerBaseName(tab.path) || 'File';
    }

    function explorerTabUnstagedGit(git) {
        if (!git || typeof git !== 'object') {
            return null;
        }
        const indexCode = git.index_status || ' ';
        const worktreeCode = git.worktree_status || ' ';
        if (git.status === 'untracked' || indexCode === '?' || worktreeCode === '?') {
            return { ...git, status: 'untracked' };
        }
        if (git.status === 'conflicted' || indexCode === 'U' || worktreeCode === 'U') {
            return { ...git, status: 'conflicted' };
        }
        if (explorerGitCodeUnmodified(worktreeCode)) {
            return null;
        }
        const status = explorerGitStatusFromCode(worktreeCode);
        return status === 'clean' ? null : { ...git, status };
    }

    function syncExplorerTabGitFromRepo(index, repo) {
        const pane = terminals[index];
        if (!pane || !repo || !Array.isArray(repo.changes)) {
            return;
        }
        const changesByPath = new Map(repo.changes.map(change => [
            explorerNormalizeTabPath(change.path),
            change.git || null
        ]));
        let badgesChanged = false;
        ensureExplorerTabState(pane).forEach(tab => {
            const path = explorerNormalizeTabPath(tab.path);
            // A tab showing no file (the Preview tab back on a directory
            // listing) has no Git status to report — it must not keep the
            // badge of the file it happened to show last.
            const nextGit = path ? (changesByPath.get(path) || null) : null;
            /* The tab strip is a second DOM rebuild the user did not ask
               for; skip it when the badge map is unchanged (a quiet
               background refresh must not repaint what did not change). */
            if (JSON.stringify(nextGit) !== JSON.stringify(tab.git || null)) {
                tab.git = nextGit;
                badgesChanged = true;
            }
        });
        if (badgesChanged) {
            renderExplorerTabStrip(index);
        }
    }

    /* Create (or reuse) the deduplicated pinned tab for a path without
       deciding what the viewer shows — the caller owns focus. At the cap the
       oldest pinned tab that is not the active one is evicted. */
    function explorerEnsurePinnedTab(pane, path) {
        ensureExplorerTabState(pane);
        const key = explorerNormalizeTabPath(path);
        if (!key) {
            return null;
        }
        const name = explorerBaseName(path);
        let pinnedTab = pane._explorerTabs.find(entry => entry.pinned && explorerNormalizeTabPath(entry.path) === key);
        if (!pinnedTab) {
            const pinnedCount = pane._explorerTabs.filter(entry => entry.pinned).length;
            if (pinnedCount >= EXPLORER_MAX_PINNED_TABS) {
                const oldest = pane._explorerTabs.findIndex(entry => entry.pinned && entry.id !== pane._explorerActiveTabId);
                if (oldest !== -1) {
                    pane._explorerTabs.splice(oldest, 1);
                }
            }
            pinnedTab = { id: key, pinned: true, path, name };
            pane._explorerTabs.push(pinnedTab);
        } else {
            pinnedTab.path = path;
            pinnedTab.name = name;
        }
        return pinnedTab;
    }

    /* Choose (and if needed create) the tab a file should load into. A
       Markdown link pins a deduplicated tab; an explicit `tab` re-renders
       that tab; every other plain click (Files tree, Git sidebar) loads into
       the permanent Preview tab — pinned tabs are never hijacked, even when
       they already show the same path. */
    function explorerAssignOpenTab(pane, path, { pinned = false, tab = '' } = {}) {
        ensureExplorerTabState(pane);
        const name = explorerBaseName(path);

        if (tab) {
            const existing = explorerFindTab(pane, tab);
            if (existing) {
                existing.path = path;
                existing.name = name;
                pane._explorerActiveTabId = existing.id;
                return existing;
            }
        }

        if (pinned) {
            const pinnedTab = explorerEnsurePinnedTab(pane, path);
            if (pinnedTab) {
                pane._explorerActiveTabId = pinnedTab.id;
                return pinnedTab;
            }
        }

        const preview = explorerPreviewTab(pane);
        preview.path = path;
        preview.name = name;
        pane._explorerActiveTabId = preview.id;
        return preview;
    }

    /* Scroll a tab into view in the strip and pulse it. Clicking open-in-new-tab
       on a file that already has a tab deliberately changes no focus, so without this
       the click looks like it did nothing; the pulse points at the tab that
       was already there. Mirrors focusExplorerTreeRow's locate flash. */
    function flashExplorerTab(index, id) {
        const strip = document.getElementById(`explorer-tabs-${index}`);
        if (!strip || !id) {
            return false;
        }
        // Matched by dataset rather than an attribute selector: a tab id is a
        // file path, which is not safe to interpolate into a CSS selector.
        const tabEl = Array.from(strip.querySelectorAll('[data-explorer-tab]'))
            .find(entry => (entry.dataset.explorerTab || '') === id);
        if (!tabEl) {
            return false;
        }
        tabEl.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        tabEl.classList.add('explorer-tab-located');
        window.setTimeout(() => tabEl.classList.remove('explorer-tab-located'), 1200);
        return true;
    }

    /* The tree row's open-in-new-tab control opens a file in a *background*
       tab: the tab joins the
       strip while the viewer keeps showing whatever the user is reading, so
       several files can be queued without losing the current one. Nothing is
       fetched here — the tab carries only its path, and activateExplorerTab
       loads it on first click exactly like a tab restored from a snapshot.
       An already-open path is not re-focused either; it only flashes, so the
       repeated click still answers without moving the viewer. */
    function openExplorerFileInBackgroundTab(index, path, { git = null } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSession(pane._session) || !sessionIds[index]) {
            return false;
        }
        const key = explorerNormalizeTabPath(path);
        const alreadyOpen = Boolean(key) && ensureExplorerTabState(pane)
            .some(tab => tab.pinned && explorerNormalizeTabPath(tab.path) === key);
        const pinnedTab = explorerEnsurePinnedTab(pane, path);
        if (!pinnedTab) {
            return false;
        }
        /* Seed the Git badge from the tree row that opened the tab, so a
           background tab is badged the same as one opened in the foreground
           (renderExplorerFile sets it from the fetched file); a later sidebar
           sync reconciles it. */
        if (git && !pinnedTab.git) {
            pinnedTab.git = git;
        }
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
        // After the strip is rebuilt, so the flash lands on the live element.
        if (alreadyOpen) {
            flashExplorerTab(index, pinnedTab.id);
        }
        return true;
    }

    /* ── Per-tab view state ──
       The wrap opt-outs, the live view snapshot a tab switch captures, and the
       revision filter that decides how much of a restored snapshot still
       applies. Read by the viewer's render paths and by the persistence half
       below. */

    function ensureExplorerTabLineWrap(tab) {
        if (!tab) {
            return { source: true, preview: true, diff: true };
        }
        const current = tab.lineWrap && typeof tab.lineWrap === 'object' ? tab.lineWrap : {};
        tab.lineWrap = {
            source: current.source !== false,
            preview: current.preview !== false,
            diff: current.diff !== false,
        };
        return tab.lineWrap;
    }

    /* Snapshot the currently shown tab's view mode + scroll onto its tab
       record. Must run while the tab's content is still in the DOM, i.e.
       before the active tab id changes or a loading placeholder replaces the
       viewer. The `_explorerRenderedTabId` guard records which tab the viewer
       DOM actually belongs to — with Preview isolation two tabs can show the
       same path, so a path match alone cannot prove the DOM is the active
       tab's (it may be the Preview tab showing the same file in diff mode). */
    function explorerCaptureActiveTabView(index) {
        const pane = terminals[index];
        if (!pane || (pane._explorerMode !== 'file' && pane._explorerMode !== 'directory')) {
            return;
        }
        if (pane._explorerRenderedTabId !== pane._explorerActiveTabId) {
            return;
        }
        const tab = explorerFindTab(pane, pane._explorerActiveTabId);
        if (!tab) {
            return;
        }
        const isFile = pane._explorerMode === 'file';
        if (isFile) {
            if (explorerNormalizeTabPath(tab.path) !== explorerNormalizeTabPath(pane._explorerFilePath)) {
                return;
            }
        } else if (tab.path) {
            return;
        }
        /* The find query belongs to the tab *and the file it was typed
           against*, never to the pane. One pane-wide query meant opening
           anything else re-ran the outgoing file's search over the incoming
           one: marks the reader never asked for, and a scroll-to-first-match
           that overrode the offset the tab restore had just put back. Pairing
           it with the path is what makes the permanent Preview tab — which
           shows a different file on every plain click — drop the query while a
           pinned tab, or a reopen of the same file, keeps it.

           The position travels with the query. A tab switch used to hand back
           the query alone, so returning to the tab re-ran it from the top and
           left the reader on match 1 of 8 with the counter to match — the find
           survived, the place they had walked it to did not. The revision is
           stamped alongside it so a file that moved underneath is re-found
           from its first match rather than reopened at an index that now names
           a different line.

           In-memory only. Nothing here reaches the persisted tab record; the
           snapshot contract stores no Search query or result. */
        if (isFile) {
            const search = pane._explorerSearch;
            const query = String(search?.query || '');
            tab.find = query
                ? {
                    path: explorerNormalizeTabPath(pane._explorerFilePath),
                    query,
                    activeIndex: Math.max(0, Math.floor(Number(search?.activeIndex) || 0)),
                    revision: String(pane._explorerFileRevision || '')
                }
                : null;
        }
        const scroll = captureExplorerFileScroll(index);
        if (!scroll) {
            return;
        }
        tab.view = {
            mode: isFile ? (scroll.activeView || 'source') : 'preview',
            revisions: explorerCurrentContentRevisions(pane),
            diffCommit: isFile && scroll.activeView === 'diff'
                ? String(pane._explorerDiffCommit || '')
                : '',
            diffMode: isFile && scroll.activeView === 'diff'
                ? String(pane._explorerDiffMode || '')
                : '',
            scroll
        };
    }

    /* What a tab hands its find back when its file renders again, and the one
       owner of that decision (renderExplorerFile reads it and does nothing
       else with the record).

       Two pairings, each answering a different way to be wrong. The query is
       paired with the path, so a tab showing another file — the permanent
       Preview tab, most of all — hands back nothing rather than painting the
       outgoing file's marks over the incoming one. The position is paired with
       the revision, so a reader who left on match 3 of 8 comes back to match 3
       of 8, while a file that changed underneath comes back at the first match
       the way a freshly typed query does: the index no longer names the line
       it was standing on. The count itself is not restored — it is recomputed
       against the file that actually rendered, and the index is clamped to it
       there. */
    function explorerRestoredTabFind(tab, path, revision) {
        const find = tab && tab.find;
        const key = explorerNormalizeTabPath(path);
        const query = key && find && explorerNormalizeTabPath(find.path) === key
            ? String(find.query || '')
            : '';
        if (!query) {
            return { query: '', activeIndex: 0 };
        }
        const stored = Math.floor(Number(find.activeIndex) || 0);
        const sameRevision = Boolean(find.revision)
            && find.revision === String(revision || '');
        return {
            query,
            activeIndex: sameRevision && stored > 0 ? stored : 0
        };
    }

    /* Durable intent always returns; revision-bound scroll is filtered panel by
       panel. A changed file keeps Diff/Preview selected without stale offsets. */
    function explorerMatchingTabView(tab, revisions) {
        const view = tab && tab.view;
        if (!view) {
            return null;
        }
        const policy = typeof explorerScrollPolicy === 'function'
            ? explorerScrollPolicy()
            : null;
        if (policy) {
            return policy.resolveTabView(tab, revisions, {
                resolveRecord: (record, current) => (
                    window.GridVibeExplorerPersistence?.resolveRecord(record, current)
                )
            });
        }
        const current = typeof revisions === 'string'
            ? { source: revisions, preview: revisions, diff: revisions, directory: revisions }
            : (revisions || {});
        if (view.persistedRecord) {
            return window.GridVibeExplorerPersistence?.resolveRecord(
                view.persistedRecord,
                current
            ) || null;
        }
        const same = Object.entries(view.revisions || {}).every(
            ([panel, revision]) => !revision || current[panel] === revision
        );
        return {
            ...view,
            scroll: same ? view.scroll : { activeView: view.mode, panels: {}, sidebar: {} },
            folds: same ? Array.from(tab.collapsedLines || []) : []
        };
    }

    /* ── The strip itself ──
       Render, wire, reorder, promote, reveal, and the activate/close pair.
       Everything below this point touches the DOM; everything above it is the
       tab records those functions draw from. */

    function renderExplorerTabStrip(index) {
        const pane = terminals[index];
        const strip = document.getElementById(`explorer-tabs-${index}`);
        if (!pane || !strip) {
            return;
        }
        const tabs = ensureExplorerTabState(pane);
        const activeId = pane._explorerActiveTabId;
        const dirtyEdit = pane._explorerEdit && pane._explorerEdit.dirty ? pane._explorerEdit : null;
        strip.innerHTML = tabs.map(tab => {
            const active = tab.id === activeId;
            const isPreview = tab.id === EXPLORER_PREVIEW_TAB_ID;
            const dirty = Boolean(dirtyEdit && dirtyEdit.tabId === tab.id);
            const label = explorerTabLabel(tab);
            const unstagedGit = explorerTabUnstagedGit(tab.git);
            const gitLabel = explorerGitStatusLabel(unstagedGit);
            const tabStates = [
                dirty ? 'unsaved changes' : '',
                unstagedGit ? `${unstagedGit.status} unstaged` : ''
            ].filter(Boolean);
            const icon = (!isPreview || tab.path) ? explorerFileTypeIconHtml(tab.path || label) : '';
            const gitBadge = unstagedGit ? explorerGitBadgeHtml(unstagedGit) : '';
            const closeButton = isPreview
                ? ''
                : `<button type="button" class="explorer-tab-close" data-explorer-tab-close="${escHtml(tab.id)}" title="Close tab" aria-label="Close ${escHtml(label)}">×</button>`;
            /* A tab that names a file joins the shared path context menu --
               the permanent Preview tab included, because the file it is
               showing names a path exactly as precisely as a pinned tab does,
               and its own Git-scope hook below has always said so by asking
               `tab.path` alone. Withholding copy/download there left one tab
               offering to pin a path it would not spell. A Preview tab showing
               a directory listing names no file and gets neither hook. No
               filesystem context kind is exposed either way, so the tab menu
               stays free of create/move/delete actions. */
            const copyPath = tab.path
                ? ` data-explorer-copy-path="${escHtml(tab.path)}" data-explorer-download-path="${escHtml(tab.path)}"`
                : '';
            const gitScope = tab.path
                ? ` data-explorer-git-scope-path="${escHtml(tab.path)}" data-explorer-git-scope-kind="file" data-explorer-git-scope-surface="tab"`
                : '';
            return `
                <div class="explorer-tab${active ? ' active' : ''}${isPreview ? ' preview' : ''}${dirty ? ' is-dirty' : ''}" role="tab" aria-selected="${active ? 'true' : 'false'}"${tabStates.length ? ' aria-label="' + escHtml(`${label} (${tabStates.join(', ')})`) + '"' : ''} data-explorer-tab="${escHtml(tab.id)}"${copyPath}${gitScope}${isPreview ? '' : ' draggable="true"'} title="${escHtml(`${dirty ? '● ' : ''}${gitLabel ? `${gitLabel} ` : ''}${tab.path || label}`)}">
                    <button type="button" class="explorer-tab-main" data-explorer-tab-open="${escHtml(tab.id)}">
                        ${icon}
                        <span class="explorer-tab-name">${escHtml(label)}</span>
                        ${gitBadge}
                    </button>
                    ${closeButton}
                </div>
            `;
        }).join('');

        strip.querySelectorAll('[data-explorer-tab-open]').forEach(button => {
            button.addEventListener('click', () => activateExplorerTab(index, button.dataset.explorerTabOpen || ''));
        });
        strip.querySelectorAll('[data-explorer-tab-close]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                closeExplorerTab(index, button.dataset.explorerTabClose || '');
            });
        });
        strip.querySelectorAll('[data-explorer-tab]').forEach(tabEl => {
            wireExplorerTabStripInteractions(index, tabEl);
        });
    }

    /* 2.g tab-strip affordances: middle-click closes a pinned tab (same
       guard as the ×), pinned tabs drag-reorder among themselves (OD-6: the
       permanent Preview tab keeps the first slot and is not draggable),
       double-clicking the Preview tab pins its shown file as a background
       tab in the same view mode, and double-clicking a pinned tab locates
       its file in the Files tree. */
    function wireExplorerTabStripInteractions(index, tabEl) {
        const id = tabEl.dataset.explorerTab || '';
        if (id === EXPLORER_PREVIEW_TAB_ID) {
            tabEl.querySelector('.explorer-tab-main')?.addEventListener('dblclick', () => {
                promoteExplorerPreviewTab(index);
            });
            return;
        }
        tabEl.querySelector('.explorer-tab-main')?.addEventListener('dblclick', () => {
            revealExplorerTabInTree(index, id);
        });
        tabEl.addEventListener('mousedown', event => {
            if (event.button === 1) {
                event.preventDefault(); // suppress middle-click autoscroll
            }
        });
        tabEl.addEventListener('auxclick', event => {
            if (event.button === 1) {
                event.preventDefault();
                closeExplorerTab(index, id);
            }
        });
        tabEl.addEventListener('dragstart', event => {
            const pane = terminals[index];
            if (pane) {
                pane._explorerDraggedTabId = id;
            }
            event.dataTransfer.effectAllowed = 'move';
            try {
                event.dataTransfer.setData('text/plain', id);
            } catch (_) {
                /* setData can throw in some embedded WebViews; the drag
                   still works off the pane-held id. */
            }
            tabEl.classList.add('dragging');
        });
        tabEl.addEventListener('dragend', () => {
            const pane = terminals[index];
            if (pane) {
                pane._explorerDraggedTabId = '';
            }
            clearExplorerTabDragMarkers(index);
        });
        tabEl.addEventListener('dragover', event => {
            const draggedId = terminals[index]?._explorerDraggedTabId || '';
            if (!draggedId || draggedId === id) {
                return;
            }
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
            const rect = tabEl.getBoundingClientRect();
            const before = event.clientX < rect.left + rect.width / 2;
            tabEl.classList.toggle('drag-before', before);
            tabEl.classList.toggle('drag-after', !before);
        });
        tabEl.addEventListener('dragleave', () => {
            tabEl.classList.remove('drag-before', 'drag-after');
        });
        tabEl.addEventListener('drop', event => {
            const draggedId = terminals[index]?._explorerDraggedTabId || '';
            if (!draggedId || draggedId === id) {
                return;
            }
            event.preventDefault();
            const rect = tabEl.getBoundingClientRect();
            const before = event.clientX < rect.left + rect.width / 2;
            reorderExplorerPinnedTab(index, draggedId, id, before);
        });
    }

    function clearExplorerTabDragMarkers(index) {
        document.getElementById(`explorer-tabs-${index}`)
            ?.querySelectorAll('.explorer-tab')
            .forEach(el => el.classList.remove('dragging', 'drag-before', 'drag-after'));
    }

    /* 2.g (OD-6): move a pinned tab before/after another pinned tab. Only
       pinned tabs reorder, and the insertion point is clamped behind the
       permanent Preview tab so nothing can land ahead of it. The persisted
       tab order (2.f) follows automatically because explorerSerializeTabs
       reads the array in order. */
    function reorderExplorerPinnedTab(index, draggedId, targetId, before) {
        const pane = terminals[index];
        if (!pane || !draggedId || draggedId === targetId) {
            return;
        }
        ensureExplorerTabState(pane);
        const tabs = pane._explorerTabs;
        const from = tabs.findIndex(tab => tab.pinned && tab.id === draggedId);
        if (from === -1 || !tabs.some(tab => tab.pinned && tab.id === targetId)) {
            return;
        }
        const [dragged] = tabs.splice(from, 1);
        let insertAt = tabs.findIndex(tab => tab.id === targetId) + (before ? 0 : 1);
        const previewPosition = tabs.findIndex(tab => tab.id === EXPLORER_PREVIEW_TAB_ID);
        insertAt = Math.max(insertAt, previewPosition + 1);
        tabs.splice(insertAt, 0, dragged);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    /* 2.g: double-clicking the Preview tab keeps its transient file — the
       shown file gains a pinned tab carrying the same view mode, scroll, and
       zoom. Like the tree's open-in-new-tab control it opens in the
       *background*: the viewer stays
       on Preview showing the same file, so a double-click is a bookmark and
       not a jump. Nothing is fetched — the new tab reloads lazily on its
       first click, restoring the copied view state. An existing pinned tab
       for the path is flashed, never clobbered or activated. */
    function promoteExplorerPreviewTab(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const preview = explorerPreviewTab(pane);
        const path = preview.path || '';
        if (
            !path
            || pane._explorerMode !== 'file'
            || pane._explorerRenderedTabId !== EXPLORER_PREVIEW_TAB_ID
        ) {
            return; // Preview shows a directory or is still loading
        }
        const key = explorerNormalizeTabPath(path);
        const existing = pane._explorerTabs.find(tab => tab.pinned && explorerNormalizeTabPath(tab.path) === key);
        if (existing) {
            flashExplorerTab(index, existing.id);
            return;
        }
        // Fold the live mode + scroll into the Preview record, then copy the
        // full per-tab state onto the new pinned tab. Focus is untouched, so
        // the capture stores against the tab whose DOM is actually shown.
        explorerCaptureActiveTabView(index);
        const pinnedTab = explorerEnsurePinnedTab(pane, path);
        if (!pinnedTab) {
            return;
        }
        /* The promoted tab shows the file the Preview tab was already showing,
           so it inherits that file's Git badge. Without this the new tab
           renders unbadged (no `?` on a brand-new file, no `M` on a modified
           one) until something else re-opens the file or the sidebar syncs. */
        pinnedTab.git = preview.git || null;
        if (preview.view) {
            pinnedTab.view = { ...preview.view };
        }
        if (preview.fontSize) {
            pinnedTab.fontSize = preview.fontSize;
        }
        if (preview.lineWrap) {
            pinnedTab.lineWrap = { ...preview.lineWrap };
        }
        if (preview.preferredMode) {
            pinnedTab.preferredMode = preview.preferredMode;
        }
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    /* 2.g: double-clicking a pinned tab locates its file in the Files tree —
       the same ancestor expansion a tab switch performs, plus a scroll and a
       brief flash so the row can be found again on the tab that is already
       active. The Files sidebar opens when closed: the gesture is a request
       to see the file in the tree, and there is nothing to point at
       otherwise. Read-only — nothing about the file changes. */
    async function revealExplorerTabInTree(index, id) {
        const pane = terminals[index];
        const tab = pane ? explorerFindTab(pane, id) : null;
        const path = tab?.path || '';
        if (!path) {
            return;
        }
        if (!pane._explorerTreeSidebarOpen) {
            // Awaited so the panel's own initial reveal cannot race the
            // ancestor expansion below through the in-flight children guard.
            await setExplorerTreeSidebarOpen(index, true);
        }
        await revealExplorerTreePath(index, path);
        focusExplorerTreeRow(index, path);
    }

    function renderExplorerViewerEmpty(index) {
        const pane = terminals[index];
        const viewer = explorerEnsureViewerShell(index);
        const list = document.getElementById(`explorer-list-${index}`);
        if (!pane || !viewer) {
            return;
        }
        list?.classList.remove('file-view');
        clearExplorerDirectorySearchControls(index);
        const preview = explorerPreviewTab(pane);
        preview.path = '';
        preview.name = '';
        preview.git = null;
        /* Absence means no directory has been loaded yet; an own dirPath of
           '' means the explorer root is the Preview tab's directory. Keeping
           those states distinct is what lets root-directory previews survive
           a workspace restore. */
        delete preview.dirPath;
        pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
        pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
        pane._explorerMode = 'viewer';
        pane._explorerFilePath = '';
        setExplorerFileWatchBaseline(pane, '');
        viewer.innerHTML = '<div class="explorer-empty-viewer"><span>Select a file to view</span></div>';
        renderExplorerTabStrip(index);
    }

    /* Render whatever the active tab should show: its file, the browsed
       directory listing (Preview tab), or the empty state. */
    function renderExplorerActiveTab(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const tab = explorerActiveTab(pane);
        if (tab.path) {
            const diffTarget = explorerTabPersistedDiffTarget(tab);
            openExplorerFile(index, tab.path, { tab: tab.id, ...diffTarget });
            return;
        }
        if (
            pane._explorerMode === 'directory'
            && Array.isArray(pane._explorerEntries)
            && (!tab.dirPath || tab.dirPath === pane._explorerPath)
        ) {
            /* The in-memory listing still belongs to this tab — render it
               without a re-fetch and backfill the tab's own directory path. */
            tab.dirPath = pane._explorerPath;
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
            renderExplorerDirectorySearchControls(index);
            renderExplorerDirectoryRows(index);
            const restoredView = explorerMatchingTabView(
                tab,
                explorerCurrentContentRevisions(pane)
            );
            if (restoredView) {
                restoreExplorerFileScroll(index, restoredView.scroll);
            }
            renderExplorerTabStrip(index);
            return;
        }
        if (
            tab.id === EXPLORER_PREVIEW_TAB_ID
            && Object.prototype.hasOwnProperty.call(tab, 'dirPath')
        ) {
            /* The viewer last rendered another tab, so the pane-global
               directory state no longer describes the Preview tab — re-browse
               the tab's own directory instead of falling through to empty. */
            loadExplorerPane(index, tab.dirPath);
            return;
        }
        renderExplorerViewerEmpty(index);
    }

    async function activateExplorerTab(index, id) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const tab = explorerFindTab(pane, id);
        if (!tab) {
            return;
        }
        if (pane._explorerActiveTabId === tab.id && pane._explorerRenderedTabId === tab.id) {
            // Already shown and its DOM is current: re-rendering would only
            // re-fetch, and would race a Preview-tab double-click promotion.
            return;
        }
        // Switching away from a dirty in-place edit needs confirmation first.
        if (pane._explorerActiveTabId !== tab.id
            && !(await confirmDiscardExplorerEdit(index, 'Switching tabs'))) {
            return;
        }
        // Capture the outgoing tab's mode + scroll while its DOM is intact.
        explorerCaptureActiveTabView(index);
        pane._explorerActiveTabId = tab.id;
        renderExplorerActiveTab(index);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    async function closeExplorerTab(index, id) {
        const pane = terminals[index];
        if (!pane || id === EXPLORER_PREVIEW_TAB_ID) {
            return;
        }
        ensureExplorerTabState(pane);
        const position = pane._explorerTabs.findIndex(tab => tab.id === id);
        if (position === -1) {
            return;
        }
        // Closing the tab that holds a dirty edit discards it — confirm first.
        const edit = explorerEditState(pane);
        if (edit && edit.tabId === id && edit.dirty
            && !(await confirmDiscardExplorerEdit(index, 'Closing this tab'))) {
            return;
        }
        const wasActive = pane._explorerActiveTabId === id;
        pane._explorerTabs.splice(position, 1);
        if (wasActive) {
            /* Closing the tab you are reading falls back to Preview, not to
               whichever pinned tab happened to sit beside it — the neighbour
               is an accident of open order, so landing there means reading a
               file you did not ask for and (because tabs load lazily) paying a
               fetch for it. Preview is the pane's own navigation surface and
               returns to the listing or file it was already holding.
               Re-pointed and rendered here rather than through
               activateExplorerTab: its already-shown guard would short-circuit
               in the one state where the viewer holds Preview while a pinned
               tab is active (a pinned tab whose file failed to open over a
               directory listing), leaving the closed tab in the strip. The
               outgoing tab's view is not captured — it is being discarded with
               the tab, and its record is already gone. */
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            renderExplorerActiveTab(index);
        }
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    /* ── Saved-session tab persistence (ISSUE-2026-015, per-tab views 2.f) ── */

    /* Reduce a tab's live view snapshot to the persisted shape (OD-5, amended
       per user feedback to include zoom): view mode, the primary panel's
       scroll as a fraction of scroll height (OD-4), the content-identity hash
       the restore-side skip rule compares, the tab's editor font size
       (omitted at the default so unzoomed tabs persist nothing), and its
       source/preview/diff line-wrap opt-outs (wrapping defaults on, so only an
       explicit off persists — same reason). */
    function explorerPersistableTabView(tab) {
        if (!tab) {
            return null;
        }
        const view = tab.view;
        let record = view?.persistedRecord
            ? window.GridVibeExplorerPersistence?.normalizeRecord(view.persistedRecord)
            : null;
        const fontSize = tab.fontSize ? clampExplorerEditorFontSize(tab.fontSize) : 0;
        if (!record && view) {
            record = window.GridVibeExplorerPersistence?.buildRecord({
                mode: view.mode,
                diffCommit: view.diffCommit,
                diffMode: view.diffMode,
                revisions: view.revisions,
                scroll: view.scroll,
                fontSize: fontSize || undefined,
                wrap: ensureExplorerTabLineWrap(tab),
                folds: Array.from(tab.collapsedLines || []),
                foldRevision: tab.collapsedIdentity || view.revisions?.source || ''
            }) || null;
        }
        if (!record) return null;
        if (fontSize && fontSize !== EXPLORER_EDITOR_FONT_DEFAULT) record.font_size = fontSize;
        else delete record.font_size;
        record.wrap = { ...ensureExplorerTabLineWrap(tab) };
        const folds = Array.from(tab.collapsedLines || [])
            .filter(line => Number.isInteger(line) && line > 0)
            .sort((left, right) => left - right)
            .slice(0, 256);
        if (folds.length) {
            record.folds = folds;
            const foldRevision = tab.collapsedIdentity || view?.revisions?.source || '';
            if (foldRevision) record.fold_revision = foldRevision;
        } else {
            delete record.folds;
            delete record.fold_revision;
        }
        return record;
    }

    /* Clamped editor font size from one persisted tab view record; 0 = unset. */
    function explorerPersistedTabFontSize(raw) {
        const fontSize = Number(raw && typeof raw === 'object' ? raw.font_size : 0);
        if (!Number.isFinite(fontSize) || fontSize <= 0) {
            return 0;
        }
        return clampExplorerEditorFontSize(fontSize);
    }

    /* Per-tab line-wrap flags from one persisted tab view record. Wrapping is
       on by default, so only an explicit `false` turns it off — which also
       means tabs saved before wrapping existed restore wrapped. */
    function explorerPersistedTabLineWrap(raw) {
        const view = raw && typeof raw === 'object' ? raw : {};
        if (view.version === 2 && view.wrap && typeof view.wrap === 'object') {
            return {
                source: view.wrap.source !== false,
                preview: view.wrap.preview !== false,
                diff: view.wrap.diff !== false,
            };
        }
        return {
            source: view.wrap_source !== false,
            preview: view.wrap_preview !== false,
            diff: view.wrap_diff !== false,
        };
    }

    function explorerPersistedMarkdownFolds(raw) {
        if (!raw || typeof raw !== 'object' || !Array.isArray(raw.folds)) {
            return new Set();
        }
        return new Set(raw.folds
            .map(Number)
            .filter(line => Number.isInteger(line) && line > 0)
            .slice(0, 256));
    }

    function explorerPersistedMarkdownFoldIdentity(raw) {
        if (!raw || typeof raw !== 'object') return '';
        if (raw.version === 2 && typeof raw.fold_revision === 'string') {
            return raw.fold_revision;
        }
        return typeof raw.fold_identity === 'string' ? raw.fold_identity : '';
    }

    /* Inflate one persisted tab view back into the in-memory `tab.view`
       snapshot shape 2.e restores from (clamped fraction-based metrics). */
    function explorerInflatePersistedTabView(raw) {
        const record = window.GridVibeExplorerPersistence?.normalizeRecord(raw);
        if (!record) return null;
        return {
            mode: record.intent.mode,
            diffCommit: record.intent.diff_commit || '',
            diffMode: record.intent.diff_mode || '',
            persistedRecord: record,
            revisions: {},
            scroll: { activeView: record.intent.mode, panels: {}, sidebar: {} }
        };
    }

    function explorerTabPersistedDiffTarget(tab) {
        const view = tab && tab.view;
        if (!view || view.mode !== 'diff') {
            return {};
        }
        if (view.diffCommit) {
            return { diffCommit: view.diffCommit };
        }
        if (view.diffMode) {
            return { diffMode: view.diffMode };
        }
        return {};
    }

    function explorerSerializeTabs(pane) {
        ensureExplorerTabState(pane);
        const openTabs = [];
        const tabViews = {};
        const seen = new Set();
        pane._explorerTabs.forEach(tab => {
            if (!tab.pinned || openTabs.length >= EXPLORER_MAX_PINNED_TABS) {
                return;
            }
            const key = explorerNormalizeTabPath(tab.path);
            if (!key || seen.has(key)) {
                return;
            }
            seen.add(key);
            openTabs.push(tab.path);
            const view = explorerPersistableTabView(tab);
            if (view) {
                tabViews[key] = view;
            }
        });
        /* The Preview tab keeps its own separated path (shown file or browsed
           directory) plus its zoom across saves — stored under the reserved
           tab id, keyed as `path`/`dir` next to `font_size`. */
        const preview = explorerPreviewTab(pane);
        const previewRecord = explorerPersistableTabView(preview) || {};
        const previewPath = explorerNormalizeTabPath(preview.path);
        const previewDir = explorerNormalizeTabPath(preview.dirPath);
        const hasPreviewDir = Object.prototype.hasOwnProperty.call(preview, 'dirPath');
        if (previewPath) {
            previewRecord.path = previewPath;
        }
        if (hasPreviewDir) {
            previewRecord.dir = previewDir;
        }
        if (Object.keys(previewRecord).length) {
            tabViews[EXPLORER_PREVIEW_TAB_ID] = previewRecord;
        }
        const active = explorerActiveTab(pane);
        const activeTab = active && active.pinned ? explorerNormalizeTabPath(active.path) : '';
        return {
            open_tabs: openTabs,
            active_tab: activeTab,
            tab_views: tabViews
        };
    }

    function persistExplorerTabsToSession(index) {
        const pane = terminals[index];
        if (!pane || !pane._session) {
            return;
        }
        const serialized = explorerSerializeTabs(pane);
        pane._session.explorer_open_tabs = serialized.open_tabs;
        pane._session.explorer_active_tab = serialized.active_tab;
        pane._session.explorer_tab_views = serialized.tab_views;
        /* The one funnel every tab, view-mode, wrap, fold and zoom change
           already passes through, so it is also where the group's ordered
           presentation transaction is enqueued (terminals.js owns the queue). */
        notePanePresentationChanged(index);
    }

    /* Restore fell through to nothing showable: browse a directory so the pane
       ends up attached with a live breadcrumb instead of stranded on a bare
       error message (the state that made the path bar inert until the user
       clicked the tree). A saved directory that is itself gone falls back to
       the root, which the session guarantees exists. */
    async function restoreExplorerDirectoryFallback(index, dirPath) {
        if (dirPath && await loadExplorerPane(index, dirPath)) {
            return true;
        }
        const pane = terminals[index];
        if (pane && dirPath) {
            explorerPreviewTab(pane).dirPath = '';
        }
        return loadExplorerPane(index, '');
    }

    /* Paths persisted with a pane are relative to the root it was saved under.
       Relaunching under a different root — an imported session whose directory
       was edited, a moved repo — or deleting the file since makes them dangle,
       so a restored tab that comes back not-found is dropped rather than left
       pointing at a file this explorer does not have. */
    function explorerRestoredPathIsGone(index) {
        return terminals[index]?._explorerOpenErrorCode === 'not_found';
    }

    async function restoreExplorerPersistedTabs(index) {
        const pane = terminals[index];
        if (!pane || pane._explorerTabsRestored) {
            return;
        }
        pane._explorerTabsRestored = true;
        const session = pane._session || {};
        const rawTabs = Array.isArray(session.explorer_open_tabs) ? session.explorer_open_tabs : [];
        const rawViews = session.explorer_tab_views && typeof session.explorer_tab_views === 'object'
            ? session.explorer_tab_views
            : {};
        ensureExplorerTabState(pane);
        /* The Preview tab's view, zoom, shown file, and browsed directory
           persist under its reserved id even when no pinned tabs were saved. */
        const rawPreviewView = rawViews[EXPLORER_PREVIEW_TAB_ID];
        const previewTab = explorerPreviewTab(pane);
        const previewView = explorerInflatePersistedTabView(rawPreviewView);
        if (previewView) {
            previewTab.view = previewView;
        }
        const previewFont = explorerPersistedTabFontSize(rawPreviewView);
        if (previewFont) {
            previewTab.fontSize = previewFont;
        }
        previewTab.lineWrap = explorerPersistedTabLineWrap(rawPreviewView);
        previewTab.collapsedLines = explorerPersistedMarkdownFolds(rawPreviewView);
        previewTab.collapsedIdentity = explorerPersistedMarkdownFoldIdentity(rawPreviewView);
        const savedPreviewPath = explorerNormalizeTabPath(
            rawPreviewView && typeof rawPreviewView === 'object' ? rawPreviewView.path : ''
        );
        const persistedPreviewDir = explorerPersistedPreviewDirectory(session);
        const savedPreviewDir = persistedPreviewDir === null ? '' : persistedPreviewDir;
        const hasSavedPreviewDir = persistedPreviewDir !== null;
        if (hasSavedPreviewDir) {
            previewTab.dirPath = savedPreviewDir;
        } else if (!savedPreviewPath) {
            /* Nothing was persisted for the Preview tab, so it means the
               explorer root. Recording that here is what lets a later switch
               back to Preview — after a restored pinned tab won the viewer —
               browse the root instead of falling through to the empty viewer. */
            previewTab.dirPath = '';
        }
        /* Reopen the Preview tab's own content only when no pinned tab was
           saved as active — an active pinned tab wins the viewer, and the
           seeded path/dirPath above brings the Preview content back whenever
           the user returns to the tab. */
        const restorePreviewContent = async () => {
            if (savedPreviewPath) {
                const opened = await openExplorerFile(index, savedPreviewPath, {
                    tab: EXPLORER_PREVIEW_TAB_ID,
                    ...explorerTabPersistedDiffTarget(previewTab)
                });
                if (opened) {
                    return;
                }
                if (explorerRestoredPathIsGone(index)) {
                    previewTab.path = '';
                    previewTab.name = '';
                    previewTab.git = null;
                }
                await restoreExplorerDirectoryFallback(index, savedPreviewDir);
            } else if (hasSavedPreviewDir) {
                await restoreExplorerDirectoryFallback(index, savedPreviewDir);
            } else {
                /* Nothing persisted at all — a fresh launch or an imported
                   preset. Browse the explorer root so the pane opens on its
                   own listing with a live breadcrumb, instead of an empty
                   viewer above an inert raw path the user had to fix by
                   clicking into the tree and back out to the top. */
                await restoreExplorerDirectoryFallback(index, '');
            }
        };
        if (savedPreviewPath) {
            previewTab.path = savedPreviewPath;
            previewTab.name = explorerBaseName(savedPreviewPath);
        }
        if (!rawTabs.length) {
            await restorePreviewContent();
            return;
        }
        const seen = new Set();
        rawTabs.forEach(raw => {
            const path = String(raw == null ? '' : raw);
            const key = explorerNormalizeTabPath(path);
            if (!key || seen.has(key)) {
                return;
            }
            if (pane._explorerTabs.filter(tab => tab.pinned).length >= EXPLORER_MAX_PINNED_TABS) {
                return;
            }
            seen.add(key);
            const record = { id: key, pinned: true, path, name: explorerBaseName(path) };
            /* 2.f: seed the persisted view mode + scroll fraction and zoom;
               the OD-4 identity check decides on render whether mode/scroll
               still apply (the zoom always does). */
            const view = explorerInflatePersistedTabView(rawViews[key]);
            if (view) {
                record.view = view;
            }
            const fontSize = explorerPersistedTabFontSize(rawViews[key]);
            if (fontSize) {
                record.fontSize = fontSize;
            }
            record.lineWrap = explorerPersistedTabLineWrap(rawViews[key]);
            record.collapsedLines = explorerPersistedMarkdownFolds(rawViews[key]);
            record.collapsedIdentity = explorerPersistedMarkdownFoldIdentity(rawViews[key]);
            pane._explorerTabs.push(record);
        });
        const activeKey = explorerNormalizeTabPath(session.explorer_active_tab || '');
        const activeTab = activeKey
            ? pane._explorerTabs.find(tab => tab.pinned && explorerNormalizeTabPath(tab.path) === activeKey)
            : null;
        if (!activeTab) {
            await restorePreviewContent();
            return;
        }
        /* Opened here rather than through activateExplorerTab so the restore
           can see whether the file actually came back. */
        pane._explorerActiveTabId = activeTab.id;
        renderExplorerTabStrip(index);
        const opened = await openExplorerFile(index, activeTab.path, {
            tab: activeTab.id,
            ...explorerTabPersistedDiffTarget(activeTab)
        });
        if (!opened) {
            if (explorerRestoredPathIsGone(index)) {
                pane._explorerTabs = pane._explorerTabs.filter(tab => tab.id !== activeTab.id);
            }
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            await restorePreviewContent();
        }
        persistExplorerTabsToSession(index);
    }
