/* GridVibe Files tree — extracted from explorer-viewer.js by the
   move-only Files-tree domain split required by architecture guardrail 6.

   Owns tree state and loading, row markup and interaction wiring, directory
   folding and reveal, manual and quiet refresh, and saved-file row refresh.
   Every extracted line below is byte-for-byte the implementation that stood
   in explorer-viewer.js; both files remain classic scripts sharing one global
   scope. Loaded directly after explorer-viewer.js.

   The ordered sidebar registry, shared sidebar presentation, context menu,
   filesystem watcher coordinator, and directory/file viewer remain in
   explorer-viewer.js because they span the Files tree and other surfaces. */

    function ensureExplorerTreeState(pane) {
        if (!(pane._explorerTreeExpanded instanceof Set)) {
            pane._explorerTreeExpanded = new Set();
        }
        if (!(pane._explorerTreeChildren instanceof Map)) {
            pane._explorerTreeChildren = new Map();
        }
        if (!(pane._explorerTreeErrors instanceof Map)) {
            pane._explorerTreeErrors = new Map();
        }
        if (!(pane._explorerTreeLoading instanceof Set)) {
            pane._explorerTreeLoading = new Set();
        }
        return pane;
    }

    /* `refresh` re-reads a directory the tree has already cached. The cached
       rows stay on screen for the whole round trip — a re-read the reader did
       not ask for must not blank the folder they are looking at — so the
       loading placeholder is only rendered when there is nothing to show. */
    async function loadExplorerTreeChildren(index, path, { refresh = false } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return [];
        }

        ensureExplorerTreeState(pane);
        const key = String(path || '');
        const cached = pane._explorerTreeChildren.get(key);
        if (cached && !refresh) {
            return cached;
        }
        if (pane._explorerTreeLoading.has(key)) {
            return [];
        }

        pane._explorerTreeLoading.add(key);
        pane._explorerTreeErrors.delete(key);
        if (!cached) {
            renderExplorerTreePanel(index);
        }
        try {
            const entriesUrl = `/api/explorer/${encodeURIComponent(sessionId)}/entries`;
            // Always send an explicit path (empty === the explorer root) so the tree stays
            // anchored to the configured root. Omitting it makes the backend fall back to the
            // session's current directory, which strands the tree on a subdirectory after the
            // pane re-enters explorer mode from a deeper terminal cwd.
            const response = await fetch(`${entriesUrl}?path=${encodeURIComponent(key)}`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load directory');
            }
            updateExplorerFilesystemRootRevision(index, data.root_revision || '');
            const entries = (Array.isArray(data.entries) ? data.entries : []).filter(entry => !entry.deleted);
            pane._explorerTreeChildren.set(key, entries);
            return entries;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer tree load failed:', error);
            pane._explorerTreeErrors.set(key, error.message || 'Failed to load directory.');
            return [];
        } finally {
            pane._explorerTreeLoading.delete(key);
            renderExplorerTreePanel(index);
        }
    }

    function explorerTreeRowIsActive(pane, entry) {
        const path = entry.path || '';
        if (entry.type === 'directory') {
            return pane._explorerMode !== 'file' && (pane._explorerPath || '') === path;
        }
        return pane._explorerMode === 'file' && (pane._explorerFilePath || '') === path;
    }

    /* Find a tree row's entry record by path in the loaded children of its
       parent directory. Used to carry the row's Git status onto a tab the row
       opens; null whenever that directory is not loaded. */
    function explorerTreeEntryForPath(pane, path) {
        const value = String(path || '');
        if (!value || !(pane?._explorerTreeChildren instanceof Map)) {
            return null;
        }
        const entries = pane._explorerTreeChildren.get(explorerTreeParentPath(value));
        if (!Array.isArray(entries)) {
            return null;
        }
        return entries.find(entry => (entry.path || '') === value) || null;
    }

    /* The directory a tree path sits in; '' for a root-level entry, which is
       also the key its children are cached under. */
    function explorerTreeParentPath(path) {
        const value = String(path || '');
        const separator = value.lastIndexOf('/');
        return separator === -1 ? '' : value.slice(0, separator);
    }

    /* Every directory sharing this path's parent, itself included — the set an
       Alt+click fans a fold out over. Empty whenever the parent's listing is
       not loaded, which leaves the gesture a no-op rather than a guess. */
    function explorerTreeSiblingDirectories(pane, path) {
        const entries = pane._explorerTreeChildren.get(explorerTreeParentPath(path));
        if (!Array.isArray(entries)) {
            return [];
        }
        return entries
            .filter(entry => entry.type === 'directory' && entry.path)
            .map(entry => entry.path);
    }

    /* A Git scope is a state, not a control: the tree *reports* where the pin
       and Follow point and never moves either, so these are markers (<span>)
       and not buttons. Their box and icon match the row's open-in-folder /
       open-in-tab buttons so the row's controls stay on one baseline — an
       SVG does not centre by font metrics the way a text glyph would.

       Two markers rather than one, and a row may wear both: a pin and a live
       Follow are two scopes, exactly as the repo bar's two lines are.
       Collapsing them into one mark would hide the pin that Follow is
       currently overriding — the "the pin was lost" reading the repo bar's
       paired rows exist to prevent — and would leave the reader unable to
       tell which of the two controls put the mark there.

       Zero new persisted state: which row wears which is derived from the
       pane's `_explorerGitPinnedPath` and its live Follow scope every time it
       is asked. */
    const EXPLORER_TREE_PIN_MARK_TITLE = 'Git scope pinned here';
    const EXPLORER_TREE_PIN_ROOT_MARK_TITLE = 'Git scope pinned to the explorer root';
    const EXPLORER_TREE_FOLLOW_MARK_TITLE = 'Git scope follows this row';
    const EXPLORER_TREE_FOLLOW_ROOT_MARK_TITLE = 'Git scope follows the explorer root';

    function explorerTreePinMarkHtml({ root = false } = {}) {
        const title = root ? EXPLORER_TREE_PIN_ROOT_MARK_TITLE : EXPLORER_TREE_PIN_MARK_TITLE;
        return `<span
                class="explorer-tree-pin-mark"
                ${root ? 'data-explorer-tree-pin-root' : ''}
                role="img"
                title="${title}"
                aria-label="${title}"
                ${root ? 'hidden' : ''}
            >${EXPLORER_GIT_PIN_ICON}</span>`;
    }

    function explorerTreeFollowMarkHtml({ root = false } = {}) {
        const title = root
            ? EXPLORER_TREE_FOLLOW_ROOT_MARK_TITLE
            : EXPLORER_TREE_FOLLOW_MARK_TITLE;
        return `<span
                class="explorer-tree-follow-mark"
                ${root ? 'data-explorer-tree-follow-root' : ''}
                role="img"
                title="${title}"
                aria-label="${title}"
                ${root ? 'hidden' : ''}
            >${EXPLORER_GIT_FOLLOW_ICON}</span>`;
    }

    /* The pane's pinned path, or `null` when nothing is pinned. `''` is a real
       pin — the explorer root — so the pin's existence is the field's *type*. */
    function explorerTreePinnedPath(pane) {
        return typeof pane?._explorerGitPinnedPath === 'string'
            ? pane._explorerGitPinnedPath
            : null;
    }

    function explorerTreePinnedKind(pane) {
        return pane?._explorerGitPinKind === 'file' ? 'file' : 'dir';
    }

    function explorerTreeRowIsPinned(pane, path, kind = 'dir') {
        const policy = window.GridVibeExplorerGitPin;
        return Boolean(policy && policy.explorerGitPathIsPinned(
            explorerTreePinnedPath(pane),
            path,
            explorerTreePinnedKind(pane),
            kind
        ));
    }

    /* Where Follow is pointing, or `null` when it is off.

       The tree only *asks*. Which path Follow is on is the Git sidebar's to
       derive — `explorerGitBrowsingScope()` folds the pane's mode, its open
       file, its browsed folder and the reader's row override into one answer
       — and re-deriving any of that here is how a marked row and the repo
       bar's Follow line would come to name two different paths. Guarded the
       same way the sidebar guards its call into this file: with no sidebar on
       the page there is no Follow, so there is nothing to mark. */
    function explorerTreeFollowedScope(pane) {
        if (!pane?._explorerGitFollowBrowsing
            || typeof explorerGitBrowsingScope !== 'function') {
            return null;
        }
        const scope = explorerGitBrowsingScope(pane);
        if (!scope) {
            return null;
        }
        return {
            path: String(scope.path === null || scope.path === undefined ? '' : scope.path),
            kind: scope.kind === 'file' ? 'file' : 'dir'
        };
    }

    /* Exact equality against the followed scope, through the same predicate
       the pin marker and the Graph header's pin button are painted from —
       "is Follow *here*", never "is Follow on". */
    function explorerTreeScopeIsFollowed(followed, path, kind = 'dir') {
        const policy = window.GridVibeExplorerGitPin;
        return Boolean(followed && policy && policy.explorerGitPathIsPinned(
            followed.path,
            path,
            followed.kind,
            kind
        ));
    }

    /* One marker on one row, added or removed only when it disagrees with the
       scope. `anchorSelector` is where the row markup would have put it, so a
       marker arriving by paint lands where a re-render would place it: a
       comma list resolves in document order, which is what keeps the pin
       ahead of Follow when the pin is the one arriving second.

       The `querySelector` calls here are scoped to that row's handful of
       children, not to the row list, so they are lookups and not scans. */
    function applyExplorerTreeRowMark(row, selector, wanted, markHtml, anchorSelector) {
        const mark = row.querySelector(selector);
        if (Boolean(wanted) === Boolean(mark)) {
            return;
        }
        if (mark) {
            mark.remove();
            return;
        }
        /* A row with no open control (a file row in a filtered result tree)
           takes the marker at the end. */
        const anchor = row.querySelector(anchorSelector);
        if (anchor) {
            anchor.insertAdjacentHTML('beforebegin', markHtml);
        } else {
            row.insertAdjacentHTML('beforeend', markHtml);
        }
    }

    /* Move the Git scope markers without rebuilding the tree.

       A pin write changes exactly two rows — the one losing the mark and the
       one gaining it — and a Follow move changes at most two more;
       re-rendering `[data-explorer-tree-body]` to say so would empty the
       panel's scroller, clamping its offset to 0, and the capture-phase
       scroll listener then persists that 0 as the reader's position. So the
       rows are found by **one** walk over the rendered rows rather than a
       `querySelector` per row over the whole list (the repaint guardrail) —
       one walk for both markers, never one walk each — and only the rows that
       disagree are touched.

       Idempotent: a freshly rendered tree already carries the markers in its
       row markup, and running this over it changes nothing. */
    function applyExplorerTreeScopeMarks(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const followed = explorerTreeFollowedScope(pane);
        /* The body lists the root's *children*, so a scope on the explorer
           root itself has no row to carry it. The FILES head stands in for
           the root, and its markers are toggled by attribute rather than added
           and removed, because the head is built once and left alone —
           rebuilding it drops the caret out of the name filter beside it. */
        const rootPinMark = panel.querySelector('[data-explorer-tree-pin-root]');
        if (rootPinMark) {
            rootPinMark.hidden = !explorerTreeRowIsPinned(pane, '', 'dir');
        }
        const rootFollowMark = panel.querySelector('[data-explorer-tree-follow-root]');
        if (rootFollowMark) {
            rootFollowMark.hidden = !explorerTreeScopeIsFollowed(followed, '', 'dir');
        }
        // Both markers are constant markup, so they are built once for the
        // whole walk rather than per row per marker -- the walk runs on every
        // navigation over every rendered row.
        const pinMarkHtml = explorerTreePinMarkHtml();
        const followMarkHtml = explorerTreeFollowMarkHtml();
        panel.querySelectorAll('.explorer-tree-row').forEach(row => {
            /* Read the *scope* attributes, which are the ones the row markup
               derived the markers from — never `data-explorer-context-kind`.
               That one carries `entry_kind` (`file`/`directory`/`link`/`other`,
               and `''` for a filtered row whose parent listing is not cached),
               while the markup asks `entry.type`, which knows only `directory`
               and `file`. Answering the same question from the other field
               made this paint disagree with the render for every filtered file
               row and for every symlink — it stripped the marker the render
               had just placed. One question, one field. */
            const path = row.dataset.explorerGitScopePath || '';
            const kind = row.dataset.explorerGitScopeKind === 'file' ? 'file' : 'dir';
            applyExplorerTreeRowMark(
                row,
                '.explorer-tree-pin-mark',
                explorerTreeRowIsPinned(pane, path, kind),
                pinMarkHtml,
                '.explorer-tree-follow-mark, .explorer-open-folder-btn, .explorer-open-tab-btn'
            );
            applyExplorerTreeRowMark(
                row,
                '.explorer-tree-follow-mark',
                explorerTreeScopeIsFollowed(followed, path, kind),
                followMarkHtml,
                '.explorer-open-folder-btn, .explorer-open-tab-btn'
            );
        });
    }

    /* One tree row. `options.nameHtml` supplies already-escaped markup for the
       name (the filter's match highlight); `options.staticChevron` drops the
       fold control, which is what a filtered result tree wants — its folders
       are always expanded, so an arrow there would toggle nothing. */
    function explorerTreeRowHtml(pane, entry, depth, options = {}) {
        const isDirectory = entry.type === 'directory';
        const path = entry.path || '';
        const expanded = isDirectory && pane._explorerTreeExpanded.has(path);
        const active = explorerTreeRowIsActive(pane, entry);
        const action = isDirectory
            ? `data-explorer-tree-dir="${escHtml(path)}"`
            : `data-explorer-tree-file="${escHtml(path)}"`;
        /* The fold arrow is its own control: it expands/collapses in place and
           never navigates, so browsing the tree can't evict whatever the
           Preview tab is showing. Only the name button opens the target. */
        const indent = `style="padding-left:${7 + depth * EXPLORER_TREE_INDENT_PX}px"`;
        const chevron = isDirectory && !options.staticChevron
            ? `<button
                type="button"
                class="explorer-tree-chevron-btn"
                data-explorer-tree-chevron="${escHtml(path)}"
                aria-expanded="${expanded ? 'true' : 'false'}"
                title="${expanded ? 'Collapse folder (Alt: collapse all at this level)' : 'Expand folder (Alt: expand all at this level)'}"
                aria-label="${expanded ? 'Collapse' : 'Expand'} ${escHtml(entry.name || path)}"
                ${indent}
            >${expanded ? UI_CHEVRON_DOWN_ICON : UI_CHEVRON_RIGHT_ICON}</button>`
            : `<span class="explorer-tree-chevron" aria-hidden="true" ${indent}></span>`;
        const badge = explorerGitStatusLabel(entry.git) ? explorerGitBadgeHtml(entry.git) : '';
        const scopeKind = isDirectory ? 'dir' : 'file';
        const pinMark = explorerTreeRowIsPinned(pane, path, scopeKind)
            ? explorerTreePinMarkHtml()
            : '';
        const followMark = explorerTreeScopeIsFollowed(
            explorerTreeFollowedScope(pane), path, scopeKind
        ) ? explorerTreeFollowMarkHtml() : '';
        const openFolder = isDirectory
            ? `<button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-tree-open-folder="${escHtml(path)}" title="Open folder in the explorer list" aria-label="Open folder in the explorer list">${EXPLORER_OPEN_FOLDER_ICON}</button>`
            : '';
        const openTab = isDirectory
            ? ''
            : `<button type="button" class="explorer-search-btn explorer-open-tab-btn" data-explorer-tree-open-tab="${escHtml(path)}" title="Open in a new tab" aria-label="Open ${escHtml(entry.name || path)} in a new tab">${EXPLORER_OPEN_TAB_ICON}</button>`;

        return `
            <div
                class="explorer-tree-row${active ? ' active' : ''}"
                data-explorer-copy-path="${escHtml(path)}"
                data-explorer-context-path="${escHtml(path)}"
                data-explorer-context-kind="${escHtml(entry.entry_kind || '')}"
                data-explorer-context-revision="${escHtml(entry.revision || '')}"
                data-explorer-context-surface="tree"
                data-explorer-git-scope-path="${escHtml(path)}"
                data-explorer-git-scope-kind="${isDirectory ? 'dir' : 'file'}"
                data-explorer-git-scope-surface="tree"
                ${isDirectory ? '' : `data-explorer-download-path="${escHtml(path)}"`}
            >
                ${chevron}
                <button type="button" class="explorer-tree-main" ${action} title="${escHtml(path)}">
                    ${isDirectory ? EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(entry.name || path)}
                    <span class="explorer-tree-name">${options.nameHtml || escHtml(entry.name || path)}</span>
                </button>
                ${badge}
                ${pinMark}
                ${followMark}
                ${openFolder}
                ${openTab}
            </div>
        `;
    }

    function renderExplorerTreeNodes(pane, path, depth) {
        const indent = `style="padding-left:${10 + depth * EXPLORER_TREE_INDENT_PX}px"`;
        const error = pane._explorerTreeErrors.get(path);
        if (error) {
            return `<div class="explorer-tree-error" ${indent}>${escHtml(error)}</div>`;
        }

        const entries = pane._explorerTreeChildren.get(path);
        // A folder being re-read keeps showing what it has; only a folder with
        // nothing cached yet is worth a placeholder.
        if (!entries) {
            return pane._explorerTreeLoading.has(path)
                ? `<div class="explorer-tree-loading" ${indent}>Loading...</div>`
                : '';
        }
        if (!entries.length) {
            return `<div class="explorer-tree-empty" ${indent}>Empty folder.</div>`;
        }

        return entries.map(entry => {
            const row = explorerTreeRowHtml(pane, entry, depth);
            if (entry.type !== 'directory' || !pane._explorerTreeExpanded.has(entry.path || '')) {
                return row;
            }
            const children = renderExplorerTreeNodes(pane, entry.path || '', depth + 1);
            return `${row}<div class="explorer-tree-children">${children}</div>`;
        }).join('');
    }

    function renderExplorerTreePanel(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        wireExplorerCopyPathMenu(panel, index);

        ensureExplorerTreeState(pane);
        /* The head — "FILES" plus the name filter — is built once and left
           alone: rebuilding it on every render would drop the caret out of the
           filter box on the keystroke that triggered the render. */
        if (!panel.querySelector('.explorer-tree-section')) {
            panel.innerHTML = `
                <div class="explorer-tree-section">
                    <div class="explorer-tree-head">
                        <div class="explorer-tree-title">Files</div>
                        ${explorerTreePinMarkHtml({ root: true })}
                        ${explorerTreeFollowMarkHtml({ root: true })}
                        ${typeof explorerTreeSearchHeadHtml === 'function'
                            ? explorerTreeSearchHeadHtml(index)
                            : ''}
                    </div>
                    <div class="explorer-tree-children" data-explorer-tree-body></div>
                </div>
            `;
            if (typeof wireExplorerTreeSearchControls === 'function') {
                wireExplorerTreeSearchControls(index);
            }
            // The head is built once; nothing renders its markers again, so
            // their state comes from the one paint that owns them.
            applyExplorerTreeScopeMarks(index);
        }
        if (typeof syncExplorerTreeSearchControls === 'function') {
            syncExplorerTreeSearchControls(index);
        }
        const body = panel.querySelector('[data-explorer-tree-body]');
        if (!body) {
            return;
        }
        /* With a filter query typed, the body is the filtered result tree
           instead of the browsable one — same row markup, same click targets. */
        body.innerHTML = (typeof explorerTreeSearchActive === 'function'
            && explorerTreeSearchActive(pane))
            ? renderExplorerTreeSearchNodes(index)
            : renderExplorerTreeNodes(pane, '', 0);
        panel.querySelectorAll('[data-explorer-tree-chevron]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                const path = button.dataset.explorerTreeChevron || '';
                if (event.altKey) {
                    toggleExplorerTreeLevel(index, path);
                } else {
                    toggleExplorerTreeDirectory(index, path);
                }
            });
        });
        panel.querySelectorAll('.explorer-tree-main').forEach(button => {
            button.addEventListener('mousedown', event => {
                if (event.shiftKey) {
                    event.preventDefault();
                }
            });
        });
        panel.querySelectorAll('[data-explorer-tree-dir]').forEach(button => {
            button.addEventListener('click', event => {
                const row = button.closest('.explorer-tree-row');
                if (handleExplorerRowSelectionClick(event, index, 'tree', row)) {
                    return;
                }
                openExplorerTreeDirectory(index, button.dataset.explorerTreeDir || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-file]').forEach(button => {
            button.addEventListener('click', event => {
                const row = button.closest('.explorer-tree-row');
                if (handleExplorerRowSelectionClick(event, index, 'tree', row)) {
                    return;
                }
                openExplorerFile(index, button.dataset.explorerTreeFile || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-open-folder]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                loadExplorerPane(index, button.dataset.explorerTreeOpenFolder || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-open-tab]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                const path = button.dataset.explorerTreeOpenTab || '';
                openExplorerFileInBackgroundTab(index, path, {
                    git: explorerTreeEntryForPath(terminals[index], path)?.git || null
                });
            });
        });
        if (typeof refreshExplorerFilesystemCutSource === 'function') {
            refreshExplorerFilesystemCutSource(index);
        }
        refreshExplorerSelectionHighlight(index);
    }

    /* Fold arrow only: expand or collapse in place. It never touches the
       Preview tab, so the tree can be browsed without losing the open file. */
    async function toggleExplorerTreeDirectory(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        if (pane._explorerTreeExpanded.has(path)) {
            pane._explorerTreeExpanded.delete(path);
            renderExplorerTreePanel(index);
            notePanePresentationChanged(index);
            return;
        }

        pane._explorerTreeExpanded.add(path);
        pane._explorerTreeErrors.delete(path);
        renderExplorerTreePanel(index);
        await loadExplorerTreeChildren(index, path);
        /* A collapse keeps what was open underneath, so re-opening a folder
           can bring expanded descendants back with it. Inside a session their
           listings are still cached and the branch simply reappears; after a
           restore nothing is cached, and only the clicked folder's own listing
           was just fetched — every descendant would come back as an open
           chevron over nothing. Same walk, and free when the cache already
           holds the answer. */
        await hydrateExplorerTreeExpansion(index);
        notePanePresentationChanged(index);
    }

    /* Expanding a whole level is one directory listing per folder, so run a few
       at a time: a wide level over SFTP should not fire a request per folder at
       once. Already-visited folders come back from the children cache free. */
    const EXPLORER_TREE_LEVEL_LOAD_CONCURRENCY = 4;

    async function loadExplorerTreeLevelChildren(index, paths) {
        const queue = paths.slice();
        const workers = [];
        const width = Math.min(EXPLORER_TREE_LEVEL_LOAD_CONCURRENCY, queue.length);
        for (let worker = 0; worker < width; worker += 1) {
            workers.push((async () => {
                while (queue.length) {
                    await loadExplorerTreeChildren(index, queue.shift());
                }
            })());
        }
        await Promise.all(workers);
    }

    /* Drop a folder and everything expanded beneath it, so re-opening it later
       gives a collapsed folder instead of restoring the old subtree. */
    function collapseExplorerTreeSubtree(pane, path) {
        const prefix = `${path}/`;
        pane._explorerTreeExpanded.forEach(value => {
            if (value === path || value.startsWith(prefix)) {
                pane._explorerTreeExpanded.delete(value);
            }
        });
    }

    /* Alt+click on a fold arrow fans the toggle out to every directory sharing
       the clicked one's parent — the Files tree's answer to the Markdown source
       view's fold-all-at-this-level. The new state mirrors the clicked row, so
       Alt+clicking an open root-level folder folds the whole tree in one
       gesture. Collapsing forgets the level's deeper expansions rather than
       just hiding them: "fold everything I opened" should hand back a clean
       tree, not spring the old subtree back on the next click. */
    async function toggleExplorerTreeLevel(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        const siblings = explorerTreeSiblingDirectories(pane, path);
        if (!siblings.length) {
            return;
        }

        if (pane._explorerTreeExpanded.has(path)) {
            siblings.forEach(sibling => collapseExplorerTreeSubtree(pane, sibling));
            renderExplorerTreePanel(index);
            /* Folding a level removes most of the rows under the scroll
               position, and the browser answers a shrunken scroll height by
               clamping scrollTop to the new bottom — so the tree lands
               somewhere unrelated to the folder that was just clicked. The
               clicked row is the one thing the gesture is about, so it becomes
               the anchor. */
            scrollExplorerTreeRowIntoView(index, path);
            notePanePresentationChanged(index);
            return;
        }

        siblings.forEach(sibling => {
            pane._explorerTreeExpanded.add(sibling);
            pane._explorerTreeErrors.delete(sibling);
        });
        renderExplorerTreePanel(index);
        scrollExplorerTreeRowIntoView(index, path);
        await loadExplorerTreeLevelChildren(index, siblings);
        /* Siblings listed above the clicked one insert their children between
           it and the top of the panel, so re-anchor once the level has filled
           in. Both calls leave a row that is already visible alone. */
        scrollExplorerTreeRowIntoView(index, path);
        notePanePresentationChanged(index);
    }

    /* Directory name click: browse it in the Preview tab without changing the
       tree's structure. Expansion and collapse belong exclusively to the fold
       arrow. Preview navigation still reveals its destination by expanding
       ancestors in revealExplorerTreePath(), but never expands the destination
       directory itself. */
    async function openExplorerTreeDirectory(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        await loadExplorerPane(index, path);
    }

    /* Expand every ancestor of the pane's current directory or open file — or
       of an explicit path, which the tab strip's locate-in-tree gesture uses
       to point at a tab's file without depending on what the viewer shows. */
    async function revealExplorerTreePath(index, targetPath = '') {
        const pane = terminals[index];
        if (!pane?._explorerTreeSidebarOpen) {
            return;
        }

        ensureExplorerTreeState(pane);
        const target = targetPath || (pane._explorerMode === 'file'
            ? (pane._explorerFilePath || '')
            : (pane._explorerPath || ''));
        const segments = String(target).split('/').filter(Boolean);
        /* Expand ancestors so the target's own row becomes visible; whether
           the target directory itself expands stays a tree-click decision —
           otherwise navigating on click (2.d) would undo a collapse. */
        segments.pop();

        await loadExplorerTreeChildren(index, '');
        let current = '';
        for (const segment of segments) {
            current = current ? `${current}/${segment}` : segment;
            pane._explorerTreeExpanded.add(current);
            await loadExplorerTreeChildren(index, current);
        }
        renderExplorerTreePanel(index);
        /* Expanding the ancestors is only half the reveal: in a long tree the
           target's row can still sit outside the panel's scrolled viewport,
           which leaves its `.active` highlight off screen. */
        scrollExplorerTreeRowIntoView(index, target);
    }

    /* The tree row for a path (file or directory), or the row marked `.active`
       when no path is given. Matched by iterating the rendered buttons rather
       than with an attribute selector, because paths carry quotes and
       brackets. Null whenever the row is not rendered — a collapsed or still
       loading branch. */
    function explorerTreeRowElement(panel, path) {
        if (!panel) {
            return null;
        }
        if (!path) {
            return panel.querySelector('.explorer-tree-row.active');
        }
        const button = Array
            .from(panel.querySelectorAll('[data-explorer-tree-file], [data-explorer-tree-dir]'))
            .find(entry => (entry.dataset.explorerTreeFile ?? entry.dataset.explorerTreeDir) === path);
        return button?.closest('.explorer-tree-row') || null;
    }

    /* Scroll the tree panel — and only it, which is why this does the maths
       instead of calling `scrollIntoView`, whose `nearest` also scrolls every
       other ancestor — by the minimum needed to show a row, leaving one row of
       margin so the target never lands flush against an edge. A row already in
       view is left alone, so clicking around inside the tree never jumps.
       Returns the row so callers can decorate it. */
    function scrollExplorerTreeRowIntoView(index, path = '') {
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        const row = panel && !panel.hidden ? explorerTreeRowElement(panel, path) : null;
        if (!row) {
            return null;
        }
        const panelBox = panel.getBoundingClientRect();
        const rowBox = row.getBoundingClientRect();
        const margin = Math.min(rowBox.height, Math.max(0, (panelBox.height - rowBox.height) / 2));
        if (rowBox.top < panelBox.top + margin) {
            panel.scrollTop -= (panelBox.top + margin) - rowBox.top;
        } else if (rowBox.bottom > panelBox.bottom - margin) {
            panel.scrollTop += rowBox.bottom - (panelBox.bottom - margin);
        }
        return row;
    }

    /* Scroll a file's tree row into view and flash it. The row's own `.active`
       styling still marks the open file; this only draws the eye to it after
       the tree scrolls. A row that is not rendered (a collapsed or still
       loading branch) is left alone — the expansion above is the visible
       part of the reveal. */
    function focusExplorerTreeRow(index, path) {
        const row = path ? scrollExplorerTreeRowIntoView(index, path) : null;
        if (!row) {
            return false;
        }
        row.classList.add('explorer-tree-located');
        window.setTimeout(() => row.classList.remove('explorer-tree-located'), 1200);
        return true;
    }

    /* Expansion is persisted; the listings behind it are not.

       `_explorerTreeExpanded` comes back from a restore (or from a workspace
       tab the sidebar is being re-opened on) as a set of paths and nothing
       else, because fetched data is never persisted. Every folder in it that
       revealExplorerTreePath() does not happen to walk through therefore
       renders its chevron open above an empty branch: `renderExplorerTreeNodes`
       has no cached children for it and no load is in flight, so it emits
       nothing at all — a tree that reads as folded while every arrow says
       otherwise. Reveal only ever walks the ancestors of the path the pane is
       showing, so the rest of the reader's tree has to be re-read here.

       Breadth-first from the root, so a folder is fetched only once its
       parent's listing has confirmed it is still a directory: an expansion
       naming a folder that was deleted or renamed since the snapshot is
       dropped rather than fetched, turned into an error and cached against a
       row that no longer exists. Bounded for the same reason the watcher's
       refresh is — each node is one `/entries`, one `git status` on a subtree,
       and over SFTP that is a round trip apiece. */
    const EXPLORER_TREE_RESTORE_MAX_NODES = 64;

    async function hydrateExplorerTreeExpansion(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane?._explorerTreeSidebarOpen || !sessionId) {
            return false;
        }
        ensureExplorerTreeState(pane);
        if (!pane._explorerTreeExpanded.size) {
            return false;
        }

        await loadExplorerTreeChildren(index, '');
        if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
            return false;
        }

        const reached = new Set();
        let level = [''];
        let budget = EXPLORER_TREE_RESTORE_MAX_NODES;
        let loaded = false;
        while (level.length && budget > 0) {
            const next = [];
            level.forEach(parent => {
                const entries = pane._explorerTreeChildren.get(parent);
                if (!Array.isArray(entries)) {
                    return;
                }
                entries.forEach(entry => {
                    const path = entry.path || '';
                    if (entry.type === 'directory' && pane._explorerTreeExpanded.has(path)) {
                        reached.add(path);
                        next.push(path);
                    }
                });
            });
            const pending = next
                .filter(path => !pane._explorerTreeChildren.has(path))
                .slice(0, budget);
            if (pending.length) {
                budget -= pending.length;
                loaded = true;
                await loadExplorerTreeLevelChildren(index, pending);
                if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
                    return false;
                }
            }
            level = next;
        }

        /* An expansion whose parent listing is loaded and does not contain it
           is gone from disk, not merely unvisited: keeping it would re-open a
           folder that no longer exists every time the pane is restored. A path
           the walk never reached because the budget ran out is left alone —
           unproven is not stale. */
        let pruned = false;
        [...pane._explorerTreeExpanded].forEach(path => {
            if (reached.has(path)) {
                return;
            }
            const siblings = pane._explorerTreeChildren.get(explorerTreeParentPath(path));
            if (!Array.isArray(siblings)) {
                return;
            }
            if (!siblings.some(entry => entry.type === 'directory' && (entry.path || '') === path)) {
                pane._explorerTreeExpanded.delete(path);
                pruned = true;
            }
        });

        if (!loaded && !pruned) {
            return false;
        }
        renderExplorerTreePanel(index);
        /* Every listing above re-rendered the panel body, and rebuilding the
           body clamps the tree's scroller to 0. The offset the restore already
           applied is therefore gone by the time the branches it belongs to
           exist, so it is put back once here, after the last render. */
        if (typeof restoreExplorerSidebarPresentation === 'function') {
            restoreExplorerSidebarPresentation(index);
        }
        if (pruned) {
            notePanePresentationChanged(index);
        }
        return true;
    }

    async function loadExplorerTree(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        ensureExplorerTreeState(pane);
        renderExplorerTreePanel(index);
        await hydrateExplorerTreeExpansion(index);
        await revealExplorerTreePath(index);
    }

    /* Drop cached children but keep expansion state, then refetch what is visible. */
    async function reloadExplorerTree(index) {
        const pane = terminals[index];
        if (!pane?._explorerTreeSidebarOpen) {
            return;
        }

        ensureExplorerTreeState(pane);
        pane._explorerTreeChildren.clear();
        pane._explorerTreeErrors.clear();
        renderExplorerTreePanel(index);

        const expanded = [...pane._explorerTreeExpanded]
            .sort((left, right) => left.split('/').length - right.split('/').length);
        await loadExplorerTreeChildren(index, '');
        for (const path of expanded) {
            await loadExplorerTreeChildren(index, path);
        }
        resetExplorerFsWatchBaseline(pane);
        renderExplorerTreePanel(index);
        /* A reload means the tree on disk moved under us (a create, a delete, a
           rename). With a filter typed, its result set is what the panel is
           showing, so it has to be re-read too — once, on the same explicit
           trigger, never on a timer. */
        if (typeof explorerTreeSearchActive === 'function' && explorerTreeSearchActive(pane)) {
            await runExplorerTreeSearch(index);
        }
    }

    /* One file's row, re-read in place. Saving changes a file's *contents*, so
       the set of paths the tree draws cannot have moved — only that one row
       can (its Git badge turns a clean file modified, and its filesystem
       revision is what the delete/move guards check). Running the full
       reloadExplorerTree() for that dropped every cached directory, flashed a
       near-empty panel, refetched one request per expanded folder and left the
       reader scrolled back to the top of a tree they had navigated by hand.

       So only the file's own directory is re-read, its rows stay on screen for
       the round trip, every other folder keeps its cache and its expansion,
       and the panel's scroll is put back afterwards — the rebuild that follows
       the response resets it, the same way it resets on any tree render. A
       file whose directory the tree has not loaded has no row to refresh. */
    async function refreshExplorerTreeFileEntry(index, path) {
        const pane = terminals[index];
        const target = String(path || '');
        if (!pane?._explorerTreeSidebarOpen || !target) {
            return;
        }
        ensureExplorerTreeState(pane);
        const cut = target.lastIndexOf('/');
        const parent = cut === -1 ? '' : target.slice(0, cut);
        if (!pane._explorerTreeChildren.has(parent)) {
            return;
        }
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        const viewport = captureScrollMetrics(panel);
        await loadExplorerTreeChildren(index, parent, { refresh: true });
        applyScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`), viewport);
    }

    const EXPLORER_FS_WATCH_MAX_TREE_NODES = 16;

    /* The tree nodes a re-render would actually paint: the root plus every
       expanded directory whose children are cached, shallowest first. Bounded
       because each node costs one `/entries` (one `git status` on a subtree) —
       a deeply expanded tree refreshes its visible top and leaves the rest to
       the manual Refresh, which is strictly better than today's fully stale
       tree. */
    function explorerTreeQuietRefreshKeys(pane) {
        const keys = [''];
        [...pane._explorerTreeExpanded]
            .filter(path => path && pane._explorerTreeChildren.has(path))
            .sort((left, right) => left.split('/').length - right.split('/').length)
            .forEach(path => keys.push(path));
        return keys.slice(0, EXPLORER_FS_WATCH_MAX_TREE_NODES);
    }

    /* Refetch the visible tree nodes into a scratch map, then swap them in one
       render — unlike reloadExplorerTree, the panel never empties, never shows
       `Loading...`, and keeps its scroll offset and expansion state. */
    async function refreshExplorerTreeQuiet(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || !pane._explorerTreeSidebarOpen) {
            return true;
        }
        ensureExplorerTreeState(pane);
        if (!pane._explorerTreeChildren.size) {
            return true; // Never loaded: the watcher does not bootstrap it.
        }
        const fetched = new Map();
        let changed = false;
        for (const key of explorerTreeQuietRefreshKeys(pane)) {
            const data = await explorerFetchEntriesQuiet(index, key);
            if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
                return false;
            }
            if (!data) {
                return false;
            }
            const entries = (Array.isArray(data.entries) ? data.entries : [])
                .filter(entry => !entry.deleted);
            fetched.set(key, entries);
            if (explorerEntriesSignature(entries)
                !== explorerEntriesSignature(pane._explorerTreeChildren.get(key))) {
                changed = true;
            }
        }
        if (!changed) {
            return true;
        }
        const metrics = captureScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`));
        fetched.forEach((entries, key) => {
            pane._explorerTreeChildren.set(key, entries);
            pane._explorerTreeErrors.delete(key);
        });
        renderExplorerTreePanel(index);
        applyScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`), metrics);
        return true;
    }
