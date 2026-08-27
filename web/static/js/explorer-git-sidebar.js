/* GridVibe Git sidebar — extracted from explorer-viewer.js by the
   move-only Git-domain split required by architecture guardrail 6.

   Owns Git status presentation, repository loading and quiet repaint,
   commit graph/search/active-row painting, and every sidebar Git action.
   The extracted functions below are byte-for-byte the implementations that
   stood in explorer-viewer.js; both files remain classic scripts sharing one
   global scope. Loaded directly after explorer-viewer.js. */

    function explorerGitRequestUrl(sessionId, endpoint, scopePath, extra = {}) {
        const params = new URLSearchParams();
        if (scopePath !== null && scopePath !== undefined) {
            params.set('scope', 'path');
            params.set('path', String(scopePath || ''));
        }
        Object.entries(extra || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value) !== '') {
                params.set(key, String(value));
            }
        });
        const query = params.toString();
        return `/api/explorer/${encodeURIComponent(sessionId)}/git/${endpoint}${query ? `?${query}` : ''}`;
    }

    function explorerGitScopePath(pane) {
        if (pane?._explorerGitFollowBrowsing) {
            return String(pane._explorerPath || '');
        }
        return typeof pane?._explorerGitPinnedPath === 'string'
            ? pane._explorerGitPinnedPath
            : null;
    }

    /* A scope as a plain string: `null` (no pin, not following) and ''
       (pinned at the explorer root) both go out as the root and are therefore
       the same load identity, even though they are different pane states. */
    function explorerGitScopeIdentity(scopePath) {
        return scopePath === null || scopePath === undefined ? '' : String(scopePath);
    }

    function explorerGitRequestedScope(pane) {
        return explorerGitScopeIdentity(explorerGitScopePath(pane));
    }

    /* Two different questions, and answering both with one field is what made
       a pin look permanently unloaded.

       `_explorerGitAnchorPath` is the load identity: "which scope is the model
       on this pane the model *for*". It is compared against the scope the next
       load would request, so it has to be the scope that *was* requested.
       `data.anchor_path` is the server's answer — the resolved, root-relative
       spelling of that scope — and it is a different string whenever the
       request's spelling was not already canonical. Storing the answer and
       comparing it to the question meant such a pane never counted as loaded:
       every render refetched the whole repository, and the early return that
       exists to stop that never fired once.

       The server's answer is still worth keeping — it is what the sidebar can
       show the reader — so it gets its own field rather than overwriting the
       identity. */
    function explorerGitNoteLoadedScope(pane, requestedScopePath, data) {
        if (!pane) {
            return;
        }
        pane._explorerGitAnchorPath = explorerGitScopeIdentity(requestedScopePath);
        pane._explorerGitResolvedAnchor = String(data?.anchor_path || '');
    }

    function explorerGitStatusLabel(git) {
        if (!git || typeof git !== 'object') {
            return '';
        }
        const status = git.status || 'clean';
        if (status === 'clean' && git.has_descendant_changes) {
            return '*';
        }
        return EXPLORER_GIT_STATUS_LABELS[status] || '';
    }

    function explorerGitStatusTitle(git) {
        if (!git || typeof git !== 'object') {
            return 'Git status unavailable';
        }
        const status = git.status || 'clean';
        if (git.has_descendant_changes && git.descendant_status) {
            return `Directory contains ${git.descendant_status} Git changes`;
        }
        if (status === 'clean' && git.has_descendant_changes) {
            return 'Directory contains Git changes';
        }
        return `Git status: ${status}`;
    }

    function explorerGitBadgeHtml(git) {
        const label = explorerGitStatusLabel(git);
        const status = git?.status || 'clean';
        const className = label ? status : 'clean';
        return `<span class="explorer-git-badge ${escHtml(className)}" title="${escHtml(explorerGitStatusTitle(git))}">${escHtml(label)}</span>`;
    }

    function explorerHasGitDiff(git) {
        if (!git || typeof git !== 'object') {
            return false;
        }
        return ['modified', 'added', 'deleted', 'renamed', 'conflicted'].includes(git.status || '');
    }

    function explorerGitSummaryText(git) {
        if (!git || typeof git !== 'object') {
            return '';
        }
        if (!git.available) {
            return git.error ? 'Git unavailable' : 'No Git repo';
        }
        const parts = [git.branch || (git.head ? git.head.slice(0, 7) : 'Git')];
        if (Number(git.ahead || 0) > 0) {
            parts.push(`↑${git.ahead}`);
        }
        if (Number(git.behind || 0) > 0) {
            parts.push(`↓${git.behind}`);
        }
        if (git.dirty) {
            parts.push('*');
        }
        return parts.join(' ');
    }

    function explorerGitBranchLabel(git) {
        return explorerGitSummaryText(git) || 'Git';
    }

    /* What the repo bar says about the scope every action in this panel acts
       on. An unpinned, non-following pane is scoped to the explorer root by
       default and gets no word for it — naming the default on every pane is
       noise. A pin made *at* the root does get one, because a root pin and no
       pin ask the server for exactly the same thing: without a word for it,
       pinning at the root round-trips perfectly and still reads as though the
       pin had been lost. */
    function explorerGitScopeLabel(scopePath) {
        return window.GridVibeExplorerGitPin.explorerGitPinLabel(scopePath);
    }

    /* The pin button's three states, read from the module the Files tree's
       marker is painted from (explorer-git-pin.js) so the pressed button and
       the marked row cannot disagree. Reading the pane here rather than in
       the module keeps the module free of pane shape: what it is handed is a
       pinned path and a browsed path.

       Delegated outright, with no local fallback: a second copy of the state
       table -- or of the word for the root -- is exactly the disagreement the
       module exists to prevent, and it is a page script loaded before this
       one. */
    function explorerGitPinState(pane) {
        return window.GridVibeExplorerGitPin.explorerGitPinButtonState(
            typeof pane?._explorerGitPinnedPath === 'string' ? pane._explorerGitPinnedPath : null,
            String(pane?._explorerPath || '')
        );
    }

    function updateExplorerGitSummary(index, git) {
        const summary = document.getElementById(`explorer-git-${index}`);
        if (!summary) {
            return;
        }
        const text = explorerGitSummaryText(git);
        summary.textContent = text;
        summary.title = git?.error || (git?.repo_root || text);
    }

    function explorerDiffCacheKey(path, commit, mode = '') {
        return `${String(path || '')}\n${String(commit || '')}\n${String(mode || '')}`;
    }

    function explorerDiffSidebarStatusHtml(git) {
        return explorerGitBadgeHtml(git || { status: 'clean' });
    }

    function explorerParentDirectory(path) {
        const cleaned = String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const slashIndex = cleaned.lastIndexOf('/');
        return slashIndex > 0 ? cleaned.slice(0, slashIndex) : '';
    }

    function explorerGitOpenFile(index, path, diffMode = 'worktree') {
        if (!path) {
            return;
        }
        // Changed-file rows jump straight to the diff view (ISSUE-2026-023).
        // 'worktree' shows unstaged hunks, 'staged' shows the indexed hunks, so
        // a partially staged file never surfaces the other section's changes.
        const mode = diffMode === 'staged' ? 'staged' : 'worktree';
        openExplorerFile(index, path, { openDiff: true, diffMode: mode });
    }

    async function explorerGitOpenCommitDiff(index, path, commit) {
        if (!path || !commit) {
            return;
        }
        const opened = await openExplorerFile(index, path, {
            showLoading: true,
            openDiff: true,
            diffCommit: commit
        });
        if (!opened) {
            renderExplorerCommitDiffFile(index, path, commit);
        }
    }

    function explorerGitOpenFolder(index, path) {
        loadExplorerPane(index, explorerParentDirectory(path));
    }

    function explorerGitGraphLane(character, position) {
        // `git log --graph` gives each branch a two-character column, and a diagonal
        // always belongs to the column it is reaching towards, not the one it starts in.
        const column = character === '/' || character === '\\' ? (position + 1) / 2 : position / 2;
        return Math.floor(column) % EXPLORER_GIT_GRAPH_LANE_COUNT;
    }

    function explorerGitGraphHtml(graph) {
        const characters = Array.from(typeof graph === 'string' && graph ? graph : '*');
        return characters.map((character, position) => {
            if (character === ' ') {
                return ' ';
            }
            const lane = explorerGitGraphLane(character, position);
            const nodeClass = character === '*' ? ' node' : '';
            return `<span class="explorer-diff-commit-graph-lane${nodeClass}" data-git-lane="${lane}">${escHtml(character)}</span>`;
        }).join('');
    }

    function explorerGitStatusFromCode(code) {
        switch (code) {
            case 'M':
            case 'T':
                return 'modified';
            case 'A':
                return 'added';
            case 'D':
                return 'deleted';
            case 'R':
                return 'renamed';
            case 'C':
                return 'added';
            case 'U':
                return 'conflicted';
            case '?':
                return 'untracked';
            case '!':
                return 'ignored';
            default:
                return 'clean';
        }
    }

    function explorerGitCodeUnmodified(code) {
        // Porcelain v2 uses '.' for an unchanged index/worktree position; clean rows use ' '.
        return !code || code === ' ' || code === '.';
    }

    function splitExplorerGitChanges(changes) {
        const staged = [];
        const unstaged = [];
        (Array.isArray(changes) ? changes : []).forEach(file => {
            const git = file.git || {};
            const indexCode = git.index_status || ' ';
            const worktreeCode = git.worktree_status || ' ';
            if (git.status === 'conflicted') {
                unstaged.push({ ...file, git: { ...git, status: 'conflicted' } });
                return;
            }
            if (git.status === 'untracked' || indexCode === '?') {
                unstaged.push({ ...file, git: { ...git, status: 'untracked' } });
                return;
            }
            if (!explorerGitCodeUnmodified(indexCode)) {
                staged.push({ ...file, git: { ...git, status: explorerGitStatusFromCode(indexCode) } });
            }
            if (!explorerGitCodeUnmodified(worktreeCode)) {
                unstaged.push({ ...file, git: { ...git, status: explorerGitStatusFromCode(worktreeCode) } });
            }
        });
        return { staged, unstaged };
    }

    function explorerGitCanRevert(status) {
        // Tracked changes are restored from the index. A single explicitly
        // selected untracked file can also be discarded after confirmation.
        return ['modified', 'deleted', 'renamed', 'untracked'].includes(status || '');
    }

    function explorerGitCanBulkDiscard(status) {
        // Bulk discard deliberately keeps untracked files. Removing a new file
        // requires the narrower per-row action and its explicit warning.
        return ['modified', 'deleted', 'renamed'].includes(status || '');
    }

    function explorerGitFileLabelHtml(path, fallbackName) {
        /* Change rows lead with the file name and trail the muted directory, so
           the file being changed stays legible at the sidebar's narrow width.
           The directory is the part allowed to ellipsis away. */
        const cleaned = String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const slashIndex = cleaned.lastIndexOf('/');
        const name = (slashIndex >= 0 ? cleaned.slice(slashIndex + 1) : cleaned)
            || fallbackName
            || 'Changed file';
        const directory = slashIndex > 0 ? cleaned.slice(0, slashIndex) : '';
        const directoryHtml = directory
            ? `<span class="explorer-diff-commit-file-dir">${escHtml(directory)}</span>`
            : '';
        return `<span class="explorer-diff-commit-file-name">${escHtml(name)}</span>${directoryHtml}`;
    }

    function renderExplorerGitFileRows(index, files, options = {}) {
        const entries = Array.isArray(files) ? files : [];
        if (!entries.length) {
            return `<div class="explorer-diff-sidebar-empty">${escHtml(options.emptyText || 'No files.')}</div>`;
        }
        const commitHash = options.commitHash || '';
        const action = options.action || '';
        // Staged rows diff against HEAD (index hunks); everything else shows the
        // worktree hunks so a partially staged file never leaks the wrong side.
        const diffMode = action === 'unstage' ? 'staged' : 'worktree';
        return entries.map(file => {
            const path = file.path || file.repo_path || '';
            const status = (file.git && file.git.status) || '';
            const pathAction = commitHash
                ? `data-explorer-git-open-commit-diff="${escHtml(path)}" data-explorer-git-commit="${escHtml(commitHash)}"`
                : `data-explorer-git-open-file="${escHtml(path)}" data-explorer-git-diff-mode="${escHtml(diffMode)}"`;
            /* Stage/unstage draw their plus and minus with the shared
               UI_PLUS_ICON / UI_MINUS_ICON — the same pair the search panel's
               expand/collapse and the browser pane's new tab already use —
               rather than the `+`/`−` text they used to carry beside the SVG
               revert and open-folder buttons on the same row. A glyph centres
               itself by `font-size` and an SVG does not, so both buttons (and
               the editor's zoom pair, which made the same move) carry flex
               centring and an explicit icon box in terminals.css. */
            let actionButton = '';
            if (action === 'stage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-stage-btn" data-explorer-git-stage="${escHtml(path)}" title="Stage changes" aria-label="Stage changes">${UI_PLUS_ICON}</button>`;
            } else if (action === 'unstage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-unstage-btn" data-explorer-git-unstage="${escHtml(path)}" title="Unstage changes" aria-label="Unstage changes">${UI_MINUS_ICON}</button>`;
            }
            const discardLabel = status === 'untracked'
                ? 'Delete untracked file'
                : 'Discard changes (revert)';
            const revertButton = (action === 'stage' && explorerGitCanRevert(status))
                ? `<button type="button" class="explorer-search-btn explorer-git-revert-btn" data-explorer-git-revert="${escHtml(path)}" data-explorer-git-revert-status="${escHtml(status)}" title="${discardLabel}" aria-label="${discardLabel}">${EXPLORER_GIT_REVERT_ICON}</button>`
                : '';
            /* Download reads the worktree, so it is only offered where the
               worktree copy is the file the row names: not for deleted files
               and not for history rows (those show a past commit's version). */
            const downloadPath = (path && !commitHash && status !== 'deleted')
                ? ` data-explorer-download-path="${escHtml(path)}"`
                : '';
            /* The row's own diff identity, so the active-row paint can tell
               this row from the other three that name the same path without
               reading the action attributes off the button inside it. */
            const rowIdentity = ` data-explorer-git-row-commit="${escHtml(commitHash)}" data-explorer-git-row-mode="${escHtml(commitHash ? '' : diffMode)}"`;
            return `
                <div class="explorer-diff-commit-file" title="${escHtml(path)}" data-explorer-copy-path="${escHtml(path)}"${downloadPath}${rowIdentity}>
                    ${explorerFileTypeIconHtml(path)}
                    <button type="button" class="explorer-diff-commit-file-path" ${pathAction}>${explorerGitFileLabelHtml(path, file.name)}</button>
                    ${explorerDiffSidebarStatusHtml(file.git)}
                    <span class="explorer-diff-commit-file-actions">
                        ${revertButton}
                        ${actionButton}
                        <button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-git-open-folder="${escHtml(path)}" title="Open containing folder" aria-label="Open containing folder">${EXPLORER_OPEN_FOLDER_ICON}</button>
                    </span>
                </div>
            `;
        }).join('');
    }

    /* ─────────────────────────────────────────────
       Explorer copy-path context menu (ISSUE-2026-028)
       Delegated on the tree + Git panels so right-clicking any file row offers
       an in-page (WebView2-safe) Copy path / Copy relative path menu. Copy is a
       read, so this stays inside the read-only explorer contract.
    ───────────────────────────────────────────── */
    function ensureExplorerGitCommitSearchState(pane) {
        if (!pane._explorerGitCommitSearch) {
            pane._explorerGitCommitSearch = {
                query: '', activeIndex: 0, open: false, mode: 'subject'
            };
        }
        return pane._explorerGitCommitSearch;
    }

    function explorerGitCommitSearchCountText(state, plan) {
        if (!state.query) {
            return '';
        }
        if (plan.emptyText) {
            return '';
        }
        return `${plan.matchCount ? plan.activeIndex + 1 : 0}/${plan.matchCount}`;
    }

    function explorerGitCommitMessageFocusState(index) {
        const input = document.getElementById(`explorer-git-commit-message-${index}`);
        if (!input || document.activeElement !== input) {
            return null;
        }
        return {
            start: typeof input.selectionStart === 'number' ? input.selectionStart : 0,
            end: typeof input.selectionEnd === 'number' ? input.selectionEnd : 0
        };
    }

    function restoreExplorerGitCommitMessageFocus(index, state) {
        if (!state) {
            return;
        }
        const input = document.getElementById(`explorer-git-commit-message-${index}`);
        if (!input) {
            return;
        }
        input.focus();
        try {
            input.setSelectionRange(state.start, state.end);
        } catch (error) {
            // A textarea supports selection, but focus restoration must stay
            // harmless if a test double or browser implementation does not.
        }
    }

    /* Paint only, for the same reason as paintExplorerGitActiveRows below:
       the panel carries the commit-message textarea and the search input
       itself, so a keystroke must repaint the subject spans in place rather
       than re-render the panel and take the caret with it. */
    function paintExplorerGitCommitSearch(index, { scroll = false } = {}) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const policy = window.GridVibeExplorerGitSearch;
        if (!pane || !panel || !policy) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        const mode = state.mode === 'hash' ? 'hash' : 'subject';
        const repo = pane._explorerGitRepo || {};
        const commits = Array.isArray(repo.commits) ? repo.commits : [];
        const plan = policy.searchPlan(commits, state.query, state.activeIndex, { mode });
        state.activeIndex = plan.activeIndex;
        let ordinal = 0;
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach((row, rowIndex) => {
            const subjectEl = row.querySelector('.explorer-diff-commit-subject');
            const commit = commits[rowIndex];
            if (!subjectEl || !commit) {
                return;
            }
            const hash = commit.full_hash || commit.hash || '';
            const shortHash = commit.hash || hash;
            const ranges = plan.perCommit[rowIndex] || [];
            const subjectHtml = mode === 'subject' && ranges.length
                ? policy.markedSubjectHtml(
                    policy.commitSubject(commit), ranges, ordinal, plan.activeIndex
                )
                : escHtml(policy.commitSubject(commit));
            const hashHtml = mode === 'hash' && ranges.length
                ? policy.markedHashHtml(hash, ranges[0])
                : escHtml(shortHash.slice(0, 7));
            const hashMark = policy.hashMarkClass(ranges, ordinal, plan.activeIndex);
            ordinal += ranges.length;
            subjectEl.innerHTML =
                `<span class="explorer-diff-commit-hash${hashMark}">${hashHtml}</span> `
                + subjectHtml;
        });
        const count = panel.querySelector('[data-explorer-git-commit-search-count]');
        if (count) {
            count.textContent = explorerGitCommitSearchCountText(state, plan);
        }
        const empty = panel.querySelector('[data-explorer-git-commit-search-empty]');
        if (empty) {
            empty.textContent = plan.emptyText || '';
            empty.hidden = !state.open || !plan.emptyText;
        }
        const modeButton = panel.querySelector('[data-explorer-git-commit-search-mode]');
        if (modeButton) {
            const hashMode = mode === 'hash';
            modeButton.setAttribute('aria-pressed', hashMode ? 'true' : 'false');
            modeButton.title = hashMode ? 'Search commit subjects' : 'Search commit ids';
            modeButton.setAttribute('aria-label', modeButton.title);
        }
        panel.querySelectorAll(
            '[data-explorer-git-commit-search-prev], [data-explorer-git-commit-search-next]'
        ).forEach(button => {
            button.disabled = plan.matchCount === 0;
        });
        if (scroll && plan.matchCount) {
            const active = panel.querySelector(
                '.explorer-diff-commit .explorer-search-match.active, '
                + '.explorer-diff-commit-hash.explorer-git-commit-search-hit.active'
            );
            if (active) {
                requestAnimationFrame(() => {
                    scrollExplorerGitCommitRowIntoView(panel, active);
                });
            }
        }
    }

    /* Scroll the Git panel — vertically, and only it, which is why this does
       the maths instead of calling `scrollIntoView` on the match: `nearest`
       walks the horizontal axis too, and the subject span is an
       `overflow: hidden` scroller, so scrolling the mark into view shifted the
       whole subject left and pushed the hash out of the row. Same minimum-
       scroll maths as scrollExplorerTreeRowIntoView: a row already in view is
       left alone. */
    function scrollExplorerGitCommitRowIntoView(panel, mark) {
        const row = mark.closest('.explorer-diff-commit');
        if (!row) {
            return;
        }
        const panelBox = panel.getBoundingClientRect();
        const rowBox = row.getBoundingClientRect();
        const margin = Math.min(rowBox.height, Math.max(0, (panelBox.height - rowBox.height) / 2));
        if (rowBox.top < panelBox.top + margin) {
            panel.scrollTop -= (panelBox.top + margin) - rowBox.top;
        } else if (rowBox.bottom > panelBox.bottom - margin) {
            panel.scrollTop += rowBox.bottom - (panelBox.bottom - margin);
        }
    }

    function stepExplorerGitCommitSearch(index, delta) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        state.activeIndex = Number(state.activeIndex || 0) + delta;
        paintExplorerGitCommitSearch(index, { scroll: true });
    }

    function clearExplorerGitCommitSearch(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        state.query = '';
        state.activeIndex = 0;
        paintExplorerGitCommitSearch(index);
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const input = panel?.querySelector('[data-explorer-git-commit-search-input]');
        if (input) {
            input.value = '';
            input.focus();
        }
    }

    /* Show or hide the bar for the state the pane already holds. Never moves
       focus: a background re-render (explorer-git-watch.js) calls this too,
       and a sidebar that grabs the caret every few seconds is worse than one
       with no find at all. The toggle handler below focuses explicitly. */
    function applyExplorerGitCommitSearchVisibility(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        const bar = panel.querySelector('.explorer-git-commit-search');
        if (bar) {
            bar.hidden = !state.open;
        }
        const toggle = panel.querySelector('[data-explorer-git-commit-search-toggle]');
        if (toggle) {
            toggle.setAttribute('aria-expanded', state.open ? 'true' : 'false');
        }
        const input = panel.querySelector('[data-explorer-git-commit-search-input]');
        if (input && input.value !== state.query) {
            input.value = state.query;
        }
    }

    /* `action` is 'toggle' (the magnifier) or 'close' (Escape). Closing drops
       the query, so the marks it painted have to come off with it — hence the
       repaint after the visibility change. */
    function setExplorerGitCommitSearchOpen(index, action) {
        const pane = terminals[index];
        const policy = window.GridVibeExplorerGitSearch;
        if (!pane || !policy) {
            return;
        }
        pane._explorerGitCommitSearch = policy.nextVisibility(
            ensureExplorerGitCommitSearchState(pane), action
        );
        applyExplorerGitCommitSearchVisibility(index);
        paintExplorerGitCommitSearch(index);
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const open = pane._explorerGitCommitSearch.open;
        const target = open
            ? panel?.querySelector('[data-explorer-git-commit-search-input]')
            : panel?.querySelector('[data-explorer-git-commit-search-toggle]');
        target?.focus();
    }

    async function toggleExplorerGitFollowBrowsing(index) {
        const pane = terminals[index];
        if (!pane || pane._explorerGitActionBusy || pane._explorerGitRepoLoading) {
            return false;
        }
        pane._explorerGitFollowBrowsing = !Boolean(pane._explorerGitFollowBrowsing);
        invalidateExplorerGitRepo(index);
        notePanePresentationChanged(index);
        await loadExplorerGitRepo(index);
        return true;
    }

    /* Everything outside the Git panel that reports where the pin is, painted
       from one place so those surfaces cannot drift apart: today the Files
       tree's marker, which moves on exactly the same events the panel's own
       pin affordances do.

       Paint-only and attribute-level by construction. Re-rendering the Git
       panel for a pin move is not an option — it carries the commit-message
       textarea and the commit-search input, and a re-render takes the caret —
       and re-rendering the tree body would reset its scroll. */
    function refreshExplorerPinAffordances(index) {
        if (typeof applyExplorerTreePinMark === 'function') {
            applyExplorerTreePinMark(index);
        }
        applyExplorerGitPinButtonState(index);
    }

    /* The pin button, painted attribute-only.

       Two things move it. A pin write, which the tree marker answers beside
       it; and plain navigation, which changes `pinnedHere` while leaving the
       Git model untouched — with Follow off nothing reloads, so without this
       the button would keep a stale pressed state and a stale title until the
       next load.

       Attributes and a class, never a re-render: the panel carries the
       commit-message textarea and the commit-search input, and re-rendering
       it takes the caret. The repo bar's Clear pin is toggled by `hidden`
       rather than added and removed for the same reason the tree's root
       marker is — it must be able to appear on a navigation that renders
       nothing. */
    function applyExplorerGitPinButtonState(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = explorerGitPinState(pane);
        const button = panel.querySelector('[data-explorer-git-pin-toggle]');
        if (button) {
            button.setAttribute('aria-pressed', state.pressed ? 'true' : 'false');
            button.title = state.title;
            button.setAttribute('aria-label', state.title);
            button.classList.toggle('is-pinned-elsewhere', state.state === 'elsewhere');
        }
        const scopeClear = panel.querySelector('[data-explorer-git-scope-clear]');
        if (scopeClear) {
            scopeClear.hidden = !state.clearAvailable;
        }
    }

    /* One writer for the pin, so the toggle and the error panel's explicit
       Clear pin cannot drift into two slightly different clears. `null` means
       "no pin"; any string (including '') is a pinned path. */
    async function setExplorerGitPinnedScope(index, pinnedPath) {
        const pane = terminals[index];
        if (!pane || pane._explorerGitActionBusy || pane._explorerGitRepoLoading) {
            return false;
        }
        if (pinnedPath === null) {
            delete pane._explorerGitPinnedPath;
        } else {
            pane._explorerGitPinnedPath = String(pinnedPath);
        }
        /* Before the load, not after it: the marker reports a pane field that
           has already moved, so making the reader wait out a repository round
           trip to see it would be reporting the request rather than the state.
           Synchronous, so no identity re-check is owed — nothing has awaited
           yet and this is still the pane the gesture was made on. */
        refreshExplorerPinAffordances(index);
        invalidateExplorerGitRepo(index);
        notePanePresentationChanged(index);
        await loadExplorerGitRepo(index);
        return true;
    }

    /* Pin *here*: clear only when the pin is the folder being browsed,
       otherwise pin this folder — including when a pin already exists
       somewhere else, which is one write and not an unpin followed by a pin.
       Two writes would mean two invalidate + load round trips, a visible
       flash at the intermediate root scope, and two presentation writes for
       one gesture. Never an ancestor match, so this can never clear a pin the
       user made in another folder. */
    function toggleExplorerGitPinHere(index) {
        const pane = terminals[index];
        return setExplorerGitPinnedScope(
            index,
            explorerGitPinState(pane).state === 'here'
                ? null
                : String(pane?._explorerPath || '')
        );
    }

    function clearExplorerGitPinnedScope(index) {
        return setExplorerGitPinnedScope(index, null);
    }

    function renderExplorerGitPanel(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        wireExplorerCopyPathMenu(panel, index);
        if (pane._explorerGitRepoLoading) {
            panel.innerHTML = '<div class="explorer-diff-sidebar-empty">Loading repository...</div>';
            return;
        }
        if (pane._explorerGitRepoError && !pane._explorerGitRepo) {
            const following = Boolean(pane._explorerGitFollowBrowsing);
            const pinned = typeof pane._explorerGitPinnedPath === 'string';
            const pinState = explorerGitPinState(pane);
            /* A pin is faithfully re-applied on restore, including one made in
               a folder that is not inside any worktree — that is the pin
               working, not the pin being lost. But a bare "Folder is not
               inside a Git worktree" names no folder, and the pinned one may
               be nowhere near where the pane is now browsing, so the message
               reads as a bug in the pane rather than as a scope the user
               chose. Name the scope, and put the one action that resolves it
               next to it. In-pane and role="status": a pin is a state that
               lasts as long as the pin does, not an event for the launcher's
               banner. */
            const pinnedScopeNotice = pinned
                ? `
                <div class="explorer-git-scope-notice" role="status">
                    <span class="explorer-git-scope-notice-text">Pinned Git folder: <span class="explorer-git-scope-notice-path">${escHtml(explorerGitScopeLabel(pane._explorerGitPinnedPath))}</span></span>
                    <button type="button" class="explorer-git-clear-pin-btn" data-explorer-git-clear-pin>Clear pin</button>
                </div>`
                : '';
            panel.innerHTML = `
                <div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>
                ${pinnedScopeNotice}
                <div class="explorer-diff-sidebar-section">
                    <div class="explorer-diff-sidebar-title explorer-git-section-title">
                        <span>Graph</span>
                        <span class="explorer-git-section-actions">
                            <button type="button" class="explorer-search-btn explorer-git-pin-toggle${pinState.state === 'elsewhere' ? ' is-pinned-elsewhere' : ''}" data-explorer-git-pin-toggle aria-pressed="${pinState.pressed ? 'true' : 'false'}" title="${escHtml(pinState.title)}" aria-label="${escHtml(pinState.title)}">${EXPLORER_GIT_PIN_ICON}</button>
                            <button type="button" class="explorer-search-btn explorer-git-follow-toggle" data-explorer-git-follow-toggle aria-pressed="${following ? 'true' : 'false'}" title="${following ? 'Use fixed Git folder' : 'Follow browsed folder for Git'}" aria-label="${following ? 'Use fixed Git folder' : 'Follow browsed folder for Git'}">${EXPLORER_GIT_FOLLOW_ICON}</button>
                            <button type="button" class="explorer-search-btn explorer-git-commit-search-toggle" disabled title="Search commit messages" aria-label="Search commit messages">${EXPLORER_GIT_SEARCH_ICON}</button>
                        </span>
                    </div>
                </div>`;
            panel.querySelector('[data-explorer-git-pin-toggle]')?.addEventListener('click', () => {
                toggleExplorerGitPinHere(index);
            });
            panel.querySelector('[data-explorer-git-follow-toggle]')?.addEventListener('click', () => {
                toggleExplorerGitFollowBrowsing(index);
            });
            panel.querySelector('[data-explorer-git-clear-pin]')?.addEventListener('click', () => {
                clearExplorerGitPinnedScope(index);
            });
            return;
        }

        const repo = pane._explorerGitRepo || {};
        const git = repo.git || {};
        const errorBanner = pane._explorerGitRepoError
            ? `<div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>`
            : '';
        /* The change listener (explorer-git-watch.js) suspends itself after
           repeated failures; it must never make the sidebar look broken, so
           this is one muted line above the last good state — Refresh re-arms. */
        const watchPausedBanner = pane._explorerGitWatchSuspended
            ? '<div class="explorer-diff-sidebar-empty explorer-git-watch-paused">Live updates paused — use Refresh</div>'
            : '';
        const changes = Array.isArray(repo.changes) ? repo.changes : [];
        const { staged, unstaged } = splitExplorerGitChanges(changes);
        /* Discard All remains tracked-only even though a confirmed per-row
           action may now remove one explicitly selected untracked file. */
        const discardable = unstaged.filter(file => explorerGitCanBulkDiscard(file.git && file.git.status));
        const commits = Array.isArray(repo.commits) ? repo.commits : [];
        const expandedCommits = ensureExplorerDiffExpandedCommits(pane);
        const busy = Boolean(pane._explorerGitActionBusy);
        const commitMessage = typeof pane._explorerGitCommitMessage === 'string' ? pane._explorerGitCommitMessage : '';
        const hasUpstream = git.ahead !== null && git.ahead !== undefined;
        const publishLabel = hasUpstream ? 'Push' : 'Publish branch';
        const repoName = String(git.repo_name || '').trim();
        const repoBranchText = explorerGitBranchLabel(git);
        const following = Boolean(pane._explorerGitFollowBrowsing);
        const pinState = explorerGitPinState(pane);
        const scopeLines = window.GridVibeExplorerGitPin.explorerGitScopeLines(
            typeof pane._explorerGitPinnedPath === 'string' ? pane._explorerGitPinnedPath : null,
            String(pane._explorerPath || ''),
            following
        );
        const commitSearch = ensureExplorerGitCommitSearchState(pane);
        const commitSearchMode = commitSearch.mode === 'hash' ? 'hash' : 'subject';
        const searchPolicy = window.GridVibeExplorerGitSearch;
        const searchPlan = searchPolicy
            ? searchPolicy.searchPlan(
                commits, commitSearch.query, commitSearch.activeIndex, { mode: commitSearchMode }
            )
            : { perCommit: commits.map(() => []), matchCount: 0, activeIndex: 0, emptyText: '' };
        commitSearch.activeIndex = searchPlan.activeIndex;
        let searchOrdinal = 0;
        const commitRows = commits.length
            ? commits.map((commit, commitIndex) => {
                const hash = commit.hash || '';
                const searchableHash = commit.full_hash || hash;
                const expanded = hash && expandedCommits.has(
                    window.GridVibeExplorerGitActive.commitKey(hash)
                );
                const subject = searchPolicy
                    ? searchPolicy.commitSubject(commit)
                    : (commit.subject || commit.line || '');
                const ranges = searchPlan.perCommit[commitIndex] || [];
                const subjectHtml = commitSearchMode === 'subject' && ranges.length
                    ? searchPolicy.markedSubjectHtml(subject, ranges, searchOrdinal, searchPlan.activeIndex)
                    : escHtml(subject);
                const hashHtml = commitSearchMode === 'hash' && ranges.length
                    ? searchPolicy.markedHashHtml(searchableHash, ranges[0])
                    : escHtml(hash ? hash.slice(0, 7) : '');
                const hashMark = searchPolicy
                    ? searchPolicy.hashMarkClass(ranges, searchOrdinal, searchPlan.activeIndex)
                    : '';
                searchOrdinal += ranges.length;
                const rowTitle = `${commit.line || ''}${expanded ? ' (Alt: collapse all)' : ''}`;
                return `
                    <button type="button" class="explorer-diff-commit" data-explorer-git-commit-toggle="${escHtml(hash)}" data-explorer-git-commit-full="${escHtml(commit.full_hash || '')}" data-explorer-git-commit-message="${escHtml(commit.message || '')}" ${hash ? '' : 'disabled'} title="${escHtml(rowTitle)}" aria-expanded="${expanded ? 'true' : 'false'}">
                        <span class="explorer-diff-commit-graph">${explorerGitGraphHtml(commit.graph)}</span>
                        <span class="explorer-diff-commit-toggle" aria-hidden="true">${expanded ? UI_CHEVRON_DOWN_ICON : UI_CHEVRON_RIGHT_ICON}</span>
                        <span class="explorer-diff-commit-subject"><span class="explorer-diff-commit-hash${hashMark}">${hashHtml}</span> ${subjectHtml}</span>
                    </button>
                    ${expanded ? `<div class="explorer-diff-commit-files">${renderExplorerGitFileRows(index, commit.files, { emptyText: 'No files recorded for this commit.', commitHash: hash })}</div>` : ''}
                `;
            }).join('')
            : '<div class="explorer-diff-sidebar-empty">No commits in this scope.</div>';

        panel.innerHTML = `
            ${errorBanner}
            ${watchPausedBanner}
            <div class="explorer-diff-sidebar-section explorer-git-repo-bar">
                <div class="explorer-git-repo-details">
                    ${repoName ? `
                    <div class="explorer-git-repo-line explorer-git-repo-root" title="${escHtml(git.repo_root || repoName)}">
                        <span class="explorer-git-repo-icon">${EXPLORER_FOLDER_ICON}</span>
                        <span class="explorer-git-repo-text">${escHtml(repoName)}</span>
                    </div>` : ''}
                    <div class="explorer-git-repo-line explorer-git-repo-branch" title="${escHtml(repoBranchText)}">
                        <span class="explorer-git-repo-icon">${EXPLORER_GIT_TOGGLE_ICON}</span>
                        <span class="explorer-git-repo-text">${escHtml(repoBranchText)}</span>
                    </div>
                    ${scopeLines.map(line => `
                    <div class="explorer-git-repo-line explorer-git-repo-scope explorer-git-repo-scope-${line.kind}${line.overridden ? ' is-overridden' : ''}" title="${escHtml(line.title)}">
                        <span class="explorer-git-repo-icon">${line.kind === 'follow' ? EXPLORER_GIT_FOLLOW_ICON : EXPLORER_GIT_PIN_ICON}</span>
                        <span class="explorer-git-repo-text">${escHtml(line.label)}</span>
                        ${line.kind === 'pin' ? `<button type="button" class="explorer-git-clear-pin-btn explorer-git-scope-clear-btn" data-explorer-git-clear-pin data-explorer-git-scope-clear ${line.clearAvailable ? '' : 'hidden'} title="Clear the pinned Git folder: ${escHtml(line.label)}" aria-label="Clear the pinned Git folder: ${escHtml(line.label)}">Clear pin</button>` : ''}
                    </div>`).join('')}
                </div>
                <button type="button" class="explorer-git-publish-btn" data-explorer-git-publish ${busy ? 'disabled' : ''} title="Push the current branch to its remote">${escHtml(publishLabel)}</button>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Staged Changes</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-unstage-btn explorer-git-unstage-all-btn" data-explorer-git-unstage-all ${(busy || !staged.length) ? 'disabled' : ''} title="Unstage all changes" aria-label="Unstage all changes">${UI_MINUS_ICON}</button>
                    </span>
                </div>
                <div class="explorer-diff-commit-files explorer-git-change-list">
                    ${renderExplorerGitFileRows(index, staged, { emptyText: 'No staged changes.', action: 'unstage' })}
                </div>
                <div class="explorer-git-commit-box">
                    <textarea class="explorer-git-commit-message" id="explorer-git-commit-message-${index}" rows="2" placeholder="Message (commits staged changes)" ${busy ? 'disabled' : ''}>${escHtml(commitMessage)}</textarea>
                    <button type="button" class="explorer-git-commit-btn" data-explorer-git-commit ${(busy || !staged.length) ? 'disabled' : ''} title="Commit staged changes">Commit</button>
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Changes</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-revert-btn explorer-git-discard-all-btn" data-explorer-git-discard-all ${(busy || !discardable.length) ? 'disabled' : ''} title="Discard all changes" aria-label="Discard all changes">${EXPLORER_GIT_REVERT_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-stage-btn explorer-git-stage-all-btn" data-explorer-git-stage-all ${(busy || !unstaged.length) ? 'disabled' : ''} title="Stage all changes" aria-label="Stage all changes">${UI_PLUS_ICON}</button>
                    </span>
                </div>
                <div class="explorer-diff-commit-files explorer-git-change-list">
                    ${renderExplorerGitFileRows(index, unstaged, { emptyText: 'No unstaged changes.', action: 'stage' })}
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Graph</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-pin-toggle${pinState.state === 'elsewhere' ? ' is-pinned-elsewhere' : ''}" data-explorer-git-pin-toggle aria-pressed="${pinState.pressed ? 'true' : 'false'}" ${busy ? 'disabled' : ''} title="${escHtml(pinState.title)}" aria-label="${escHtml(pinState.title)}">${EXPLORER_GIT_PIN_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-follow-toggle" data-explorer-git-follow-toggle aria-pressed="${following ? 'true' : 'false'}" ${busy ? 'disabled' : ''} title="${following ? 'Use fixed Git folder' : 'Follow browsed folder for Git'}" aria-label="${following ? 'Use fixed Git folder' : 'Follow browsed folder for Git'}">${EXPLORER_GIT_FOLLOW_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-commit-search-toggle" data-explorer-git-commit-search-toggle aria-expanded="${commitSearch.open ? 'true' : 'false'}" title="Search commit messages" aria-label="Search commit messages">${EXPLORER_GIT_SEARCH_ICON}</button>
                    </span>
                </div>
                <div class="explorer-git-commit-search" ${commitSearch.open ? '' : 'hidden'}>
                    <input
                        type="search"
                        class="explorer-search-input"
                        data-explorer-git-commit-search-input
                        placeholder="Search commits"
                        autocomplete="off"
                        spellcheck="false"
                        aria-label="Search commit messages"
                        value="${escHtml(commitSearch.query)}"
                    >
                    <span class="explorer-search-count" data-explorer-git-commit-search-count>${explorerGitCommitSearchCountText(commitSearch, searchPlan)}</span>
                    <button type="button" class="explorer-search-btn explorer-git-commit-search-mode" data-explorer-git-commit-search-mode aria-pressed="${commitSearchMode === 'hash' ? 'true' : 'false'}" title="${commitSearchMode === 'hash' ? 'Search commit subjects' : 'Search commit ids'}" aria-label="${commitSearchMode === 'hash' ? 'Search commit subjects' : 'Search commit ids'}">${EXPLORER_GIT_HASH_ICON}</button>
                    <button type="button" class="explorer-search-btn" data-explorer-git-commit-search-prev ${searchPlan.matchCount ? '' : 'disabled'} title="Previous match" aria-label="Previous match">↑</button>
                    <button type="button" class="explorer-search-btn" data-explorer-git-commit-search-next ${searchPlan.matchCount ? '' : 'disabled'} title="Next match" aria-label="Next match">↓</button>
                    <button type="button" class="explorer-search-btn" data-explorer-git-commit-search-clear title="Clear search" aria-label="Clear search">×</button>
                </div>
                <span class="explorer-git-commit-search-empty" data-explorer-git-commit-search-empty role="status" aria-live="polite" ${(commitSearch.open && searchPlan.emptyText) ? '' : 'hidden'}>${escHtml(searchPlan.emptyText || '')}</span>
                ${commitRows}
            </div>
        `;
        const commitMessageInput = panel.querySelector(`#explorer-git-commit-message-${index}`);
        if (commitMessageInput) {
            commitMessageInput.addEventListener('input', () => {
                pane._explorerGitCommitMessage = commitMessageInput.value;
            });
        }
        const commitSearchInput = panel.querySelector('[data-explorer-git-commit-search-input]');
        if (commitSearchInput) {
            commitSearchInput.addEventListener('input', () => {
                const state = ensureExplorerGitCommitSearchState(pane);
                state.query = commitSearchInput.value;
                state.activeIndex = 0;
                paintExplorerGitCommitSearch(index);
            });
            commitSearchInput.addEventListener('keydown', event => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    stepExplorerGitCommitSearch(index, event.shiftKey ? -1 : 1);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    setExplorerGitCommitSearchOpen(index, 'close');
                }
            });
        }
        panel.querySelector('[data-explorer-git-commit-search-toggle]')?.addEventListener('click', () => {
            setExplorerGitCommitSearchOpen(index, 'toggle');
        });
        panel.querySelector('[data-explorer-git-commit-search-mode]')?.addEventListener('click', () => {
            const state = ensureExplorerGitCommitSearchState(pane);
            state.mode = state.mode === 'hash' ? 'subject' : 'hash';
            state.activeIndex = 0;
            paintExplorerGitCommitSearch(index);
        });
        panel.querySelector('[data-explorer-git-follow-toggle]')?.addEventListener('click', () => {
            toggleExplorerGitFollowBrowsing(index);
        });
        panel.querySelector('[data-explorer-git-pin-toggle]')?.addEventListener('click', () => {
            toggleExplorerGitPinHere(index);
        });
        /* The one always-reachable clear. The button no longer clears a pin
           you have navigated away from, so without this a pin on a folder
           that is collapsed, deleted, or outside the current root would be
           unclearable. Same writer as the button; no modifier gesture, since
           Alt already means level-fold here and an invisible gesture is not
           an affordance. */
        panel.querySelector('[data-explorer-git-clear-pin]')?.addEventListener('click', () => {
            clearExplorerGitPinnedScope(index);
        });
        panel.querySelector('[data-explorer-git-commit-search-prev]')?.addEventListener('click', () => {
            stepExplorerGitCommitSearch(index, -1);
        });
        panel.querySelector('[data-explorer-git-commit-search-next]')?.addEventListener('click', () => {
            stepExplorerGitCommitSearch(index, 1);
        });
        panel.querySelector('[data-explorer-git-commit-search-clear]')?.addEventListener('click', () => {
            clearExplorerGitCommitSearch(index);
        });
        panel.querySelectorAll('[data-explorer-git-stage]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitStageFile(index, button.dataset.explorerGitStage || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-unstage]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitUnstageFile(index, button.dataset.explorerGitUnstage || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-revert]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitRevertFile(
                    index,
                    button.dataset.explorerGitRevert || '',
                    button.dataset.explorerGitRevertStatus || '',
                );
            });
        });
        const stageAllButton = panel.querySelector('[data-explorer-git-stage-all]');
        if (stageAllButton) {
            stageAllButton.addEventListener('click', () => explorerGitStageAll(index));
        }
        const unstageAllButton = panel.querySelector('[data-explorer-git-unstage-all]');
        if (unstageAllButton) {
            unstageAllButton.addEventListener('click', () => explorerGitUnstageAll(index));
        }
        const discardAllButton = panel.querySelector('[data-explorer-git-discard-all]');
        if (discardAllButton) {
            discardAllButton.addEventListener('click', () => explorerGitDiscardAll(index));
        }
        const publishButton = panel.querySelector('[data-explorer-git-publish]');
        if (publishButton) {
            publishButton.addEventListener('click', () => explorerGitPublish(index));
        }
        const commitButton = panel.querySelector('[data-explorer-git-commit]');
        if (commitButton) {
            commitButton.addEventListener('click', () => explorerGitCommit(index));
        }
        panel.querySelectorAll('[data-explorer-git-open-file]').forEach(button => {
            button.addEventListener('click', () => {
                explorerGitOpenFile(
                    index,
                    button.dataset.explorerGitOpenFile || '',
                    button.dataset.explorerGitDiffMode || 'worktree',
                );
            });
        });
        panel.querySelectorAll('[data-explorer-git-open-commit-diff]').forEach(button => {
            button.addEventListener('click', () => {
                explorerGitOpenCommitDiff(
                    index,
                    button.dataset.explorerGitOpenCommitDiff || '',
                    button.dataset.explorerGitCommit || '',
                );
            });
        });
        panel.querySelectorAll('[data-explorer-git-open-folder]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitOpenFolder(index, button.dataset.explorerGitOpenFolder || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach(button => {
            button.addEventListener('mousedown', event => {
                if (event.altKey) {
                    /* Keep the textarea active until the click handler captures
                       its selection. The render below replaces that textarea. */
                    event.preventDefault();
                }
            });
            button.addEventListener('click', event => {
                const commit = button.dataset.explorerGitCommitToggle || '';
                if (!commit) {
                    return;
                }
                const expanded = ensureExplorerDiffExpandedCommits(pane);
                if (event.altKey) {
                    const focusState = explorerGitCommitMessageFocusState(index);
                    const plan = window.GridVibeExplorerGitSearch.collapseAllPlan(
                        Array.from(expanded)
                    );
                    if (plan.changed) {
                        expanded.clear();
                        renderExplorerGitPanel(index);
                        notePanePresentationChanged(index);
                        restoreExplorerGitCommitMessageFocus(index, focusState);
                    }
                    return;
                }
                const key = window.GridVibeExplorerGitActive.commitKey(commit);
                /* Collapsing the commit holding the open diff is final: this
                   pane has already recorded it as revealed, so the reveal in
                   syncExplorerGitActiveRows() never re-opens it. */
                if (expanded.has(key)) {
                    expanded.delete(key);
                } else {
                    expanded.add(key);
                }
                renderExplorerGitPanel(index);
                notePanePresentationChanged(index);
            });
        });
        paintExplorerGitActiveRows(index);
    }

    /* The viewer's identity in the sidebar's vocabulary. `_explorerDiffCommit`
       is what separates a past commit's copy of a path from the worktree's. */
    function explorerGitViewerTarget(pane) {
        return window.GridVibeExplorerGitActive.viewerTarget({
            mode: pane?._explorerMode,
            filePath: pane?._explorerFilePath,
            diffCommit: pane?._explorerDiffCommit,
            diffMode: pane?._explorerDiffMode
        });
    }

    /* Paint only — a class toggle over rows already on screen (guardrail 8),
       never a markup rewrite: the sidebar carries a commit-message textarea,
       and re-rendering it on every file open would take the caret with it. */
    function paintExplorerGitActiveRows(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const model = window.GridVibeExplorerGitActive;
        const target = explorerGitViewerTarget(pane);
        let activeRow = null;
        panel.querySelectorAll('.explorer-diff-commit-file[data-explorer-copy-path]').forEach(row => {
            const active = model.rowIsActive(target, {
                path: row.dataset.explorerCopyPath || '',
                commitHash: row.dataset.explorerGitRowCommit || '',
                diffMode: row.dataset.explorerGitRowMode || ''
            });
            row.classList.toggle('active', active);
            if (active) {
                row.setAttribute('aria-current', 'true');
                activeRow = activeRow || row;
            } else {
                row.removeAttribute('aria-current');
            }
        });
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach(button => {
            button.classList.toggle(
                'active',
                model.commitIsActive(target, button.dataset.explorerGitCommitToggle || '')
            );
        });
        /* Only after a reveal, so an ordinary repaint never fights the scroll
           position the sidebar was restored (or scrolled by hand) to. */
        if (pane._explorerGitScrollActiveIntoView) {
            pane._explorerGitScrollActiveIntoView = false;
            activeRow?.scrollIntoView({ block: 'nearest' });
        }
    }

    /* Reveal + paint. Re-renders the panel only when a commit actually had to
       be opened, which happens at most once per pane per commit. */
    function syncExplorerGitActiveRows(index) {
        const pane = terminals[index];
        if (!pane || !document.getElementById(`explorer-git-panel-${index}`)) {
            return;
        }
        const expanded = ensureExplorerDiffExpandedCommits(pane);
        const plan = window.GridVibeExplorerGitActive.revealPlan(explorerGitViewerTarget(pane), {
            expanded: Array.from(expanded),
            revealed: pane._explorerGitRevealedCommit || '',
            commits: (pane._explorerGitRepo?.commits || []).map(commit => commit.hash || '')
        });
        if (!plan.revealedCommit) {
            paintExplorerGitActiveRows(index);
            return;
        }
        pane._explorerGitRevealedCommit = plan.revealedCommit;
        pane._explorerGitScrollActiveIntoView = true;
        if (!plan.expandKey) {
            paintExplorerGitActiveRows(index);
            return;
        }
        expanded.add(plan.expandKey);
        // Opening the row is expansion state like any other, so it is saved
        // like any other — the whole point is that it survives the next restore.
        renderExplorerGitPanel(index);
        notePanePresentationChanged(index);
    }

    function renderExplorerGitPanels(index) {
        renderExplorerGitPanel(index);
        syncExplorerGitActiveRows(index);
    }

    function invalidateExplorerGitRepo(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerGitRepoLoaded = false;
        pane._explorerGitRepoLoading = false;
        pane._explorerGitRepoError = '';
        pane._explorerGitRepo = null;
        pane._explorerGitAnchorPath = '';
        pane._explorerGitResolvedAnchor = '';
        renderExplorerGitPanels(index);
    }

    function ensureExplorerDiffExpandedCommits(pane) {
        if (!(pane?._explorerDiffExpandedCommits instanceof Set)) {
            pane._explorerDiffExpandedCommits = new Set();
        }
        return pane._explorerDiffExpandedCommits;
    }

    async function loadExplorerGitRepo(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const scopePath = explorerGitScopePath(pane);
        const requestedAnchorPath = explorerGitScopeIdentity(scopePath);
        /* Like compared with like: both sides are the scope that was, or would
           be, *requested* — never the resolved spelling the server answers with. */
        const loadedForPath = pane?._explorerGitAnchorPath === requestedAnchorPath;
        if (!pane || !sessionId || (pane._explorerGitRepoLoaded && loadedForPath) || pane._explorerGitRepoLoading) {
            renderExplorerGitPanels(index);
            return;
        }

        pane._explorerGitRepoLoading = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        try {
            const response = await fetch(
                explorerGitRequestUrl(sessionId, 'repo', scopePath)
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git repository');
            }
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || explorerGitScopePath(pane) !== scopePath
            ) {
                return;
            }
            pane._explorerGitRepoLoaded = true;
            pane._explorerGitRepo = data;
            explorerGitNoteLoadedScope(pane, requestedAnchorPath, data);
            pane._explorerGitRevision = typeof data.revision === 'string' ? data.revision : '';
            // A user-initiated load re-arms a suspended change-listener watch.
            pane._explorerGitWatchSuspended = false;
            syncExplorerTabGitFromRepo(index, data);
        } catch (error) {
            if (
                terminals[index] === pane
                && sessionIds[index] === sessionId
                && explorerGitScopePath(pane) === scopePath
            ) {
                console.error('[GridVibe Sessions] Explorer Git repository failed:', error);
                pane._explorerGitRepoError = error.message || 'Failed to load Git repository.';
            }
        } finally {
            pane._explorerGitRepoLoading = false;
            if (terminals[index] === pane && sessionIds[index] === sessionId) {
                renderExplorerGitPanels(index);
                if (
                    pane._explorerGitSidebarOpen
                    && explorerGitScopePath(pane) !== scopePath
                ) {
                    loadExplorerGitRepo(index);
                }
            }
        }
    }

    async function refreshExplorerGitRepoQuiet(index) {
        /* Background variant of loadExplorerGitRepo for the Git change
           listener (explorer-git-watch.js): forced (no _explorerGitRepoLoaded
           early return, no invalidate — the last good panel stays on screen),
           quiet (only a git-refreshing class on the panel, never the Loading
           placeholder), and identity-checked (a stale pane/session id yields
           null). Returns the fresh payload, or null on failure/staleness —
           the pane keeps its last good _explorerGitRepo either way. */
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const scopePath = explorerGitScopePath(pane);
        if (!pane || !sessionId || pane._explorerGitRepoLoading || pane._explorerGitRepoRefreshing) {
            return null;
        }
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        pane._explorerGitRepoRefreshing = true;
        panel?.classList.add('git-refreshing');
        try {
            const response = await fetch(
                explorerGitRequestUrl(sessionId, 'repo', scopePath),
                { cache: 'no-store' }
            );
            const data = await response.json();
            if (!response.ok) {
                return null;
            }
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || explorerGitScopePath(pane) !== scopePath
            ) {
                return null;
            }
            return data;
        } catch (error) {
            return null;
        } finally {
            pane._explorerGitRepoRefreshing = false;
            panel?.classList.remove('git-refreshing');
        }
    }

    function applyExplorerGitRepoQuiet(index, data, requestedScopePath) {
        /* Swap a quietly fetched Git payload into the sidebar in place: one
           panel render, scroll/focus preserved, tab badges re-rendered only
           when the badge map actually changed (syncExplorerTabGitFromRepo
           guards that itself). The commit draft, expanded commits, sidebar
           sizing and open state all live in pane state and survive the render. */
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel || !data) {
            return false;
        }
        const scrollTop = panel.scrollTop;
        const active = document.activeElement;
        const focusState = active && panel.contains(active) && typeof active.selectionStart === 'number'
            ? { id: active.id, start: active.selectionStart, end: active.selectionEnd }
            : null;
        pane._explorerGitRepo = data;
        pane._explorerGitRepoLoaded = true;
        explorerGitNoteLoadedScope(
            pane,
            requestedScopePath === undefined
                ? explorerGitRequestedScope(pane)
                : requestedScopePath,
            data
        );
        pane._explorerGitRevision = typeof data.revision === 'string' ? data.revision : '';
        syncExplorerTabGitFromRepo(index, data);
        renderExplorerGitPanel(index);
        panel.scrollTop = Math.min(scrollTop, panel.scrollHeight);
        if (focusState && focusState.id) {
            const target = panel.querySelector(`#${CSS.escape(focusState.id)}`);
            if (target) {
                target.focus();
                try {
                    target.setSelectionRange(focusState.start, focusState.end);
                } catch (error) {
                    /* The replacement node is not selectable; focus is enough. */
                }
            }
        }
        return true;
    }

    /* What a Git action is allowed to still do when its answer arrives.

       The request itself is already bound -- its scope rode in the URL and the
       server acted on that scope -- so nothing here cancels or reissues it.
       What needs binding is everything the *answer* does, because none of the
       three things it addresses survives an await on its own: `terminals[i]`
       is a grid slot and a group switch rehouses it, the session id goes with
       it, and Follow browsing moves the captured pane's own scope while the
       request is in flight. The three are one identity, and the two ways it
       can break need different answers -- hence a state rather than a
       boolean. */
    const EXPLORER_GIT_IDENTITY_CURRENT = 'current';
    const EXPLORER_GIT_IDENTITY_SCOPE_CHANGED = 'scope-changed';
    const EXPLORER_GIT_IDENTITY_PANE_REPLACED = 'pane-replaced';

    function explorerGitCaptureIdentity(index) {
        const pane = terminals[index];
        return { pane, sessionId: sessionIds[index], scopePath: explorerGitScopePath(pane) };
    }

    function explorerGitIdentityState(index, identity) {
        if (!identity || !identity.pane) {
            return EXPLORER_GIT_IDENTITY_PANE_REPLACED;
        }
        if (terminals[index] !== identity.pane || sessionIds[index] !== identity.sessionId) {
            return EXPLORER_GIT_IDENTITY_PANE_REPLACED;
        }
        if (explorerGitScopePath(identity.pane) !== identity.scopePath) {
            return EXPLORER_GIT_IDENTITY_SCOPE_CHANGED;
        }
        return EXPLORER_GIT_IDENTITY_CURRENT;
    }

    function explorerGitIdentityIsCurrent(index, identity) {
        return explorerGitIdentityState(index, identity) === EXPLORER_GIT_IDENTITY_CURRENT;
    }

    /* The mutation landed; only its answer is stale. Deliberately *not*
       invalidateExplorerGitRepo(): that also drops `_explorerGitRepo`, which
       is the model the pane's panel is still painted from -- blanking a
       background pane's sidebar to report that something else finished is a
       worse answer than a slightly old one. The pane simply stops counting as
       loaded, so the next load refetches instead of short-circuiting on the
       anchor path it already holds, and `_explorerGitReloadPending` is what
       gets that load run when the pane comes back on screen. */
    function explorerGitMarkModelStale(pane) {
        if (!pane) {
            return;
        }
        pane._explorerGitRepoLoaded = false;
        pane._explorerGitAnchorPath = '';
        pane._explorerGitResolvedAnchor = '';
        pane._explorerGitReloadPending = true;
    }

    async function performExplorerGitAction(index, endpoint, body) {
        const identity = explorerGitCaptureIdentity(index);
        const { pane, sessionId, scopePath } = identity;
        if (!pane || !sessionId || pane._explorerGitActionBusy) {
            return false;
        }
        pane._explorerGitActionBusy = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        let succeeded = false;
        try {
            const response = await fetch(explorerGitRequestUrl(sessionId, endpoint, scopePath), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body || {}),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Git action failed');
            }
            if (explorerGitIdentityIsCurrent(index, identity)) {
                pane._explorerGitRepo = data;
                pane._explorerGitRepoLoaded = true;
                explorerGitNoteLoadedScope(
                    pane, explorerGitScopeIdentity(scopePath), data
                );
                pane._explorerGitRevision = typeof data.revision === 'string' ? data.revision : '';
                // A successful GridVibe Git action is authoritative: it re-arms a
                // suspended change-listener watch and resets its baseline.
                pane._explorerGitWatchSuspended = false;
                syncExplorerTabGitFromRepo(index, data);
                succeeded = true;
            } else {
                explorerGitMarkModelStale(pane);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git action failed:', error);
            // The error belongs to the scope it was raised for. Painting it onto
            // a pane that has since moved would attach it to a repository model
            // the user never asked this of.
            if (explorerGitIdentityIsCurrent(index, identity)) {
                pane._explorerGitRepoError = error.message || 'Git action failed.';
            } else {
                explorerGitMarkModelStale(pane);
            }
        } finally {
            // Always released on the captured pane -- a busy flag left set is a
            // pane that can never act again -- but painted only where it is
            // still the pane on screen.
            pane._explorerGitActionBusy = false;
            const state = explorerGitIdentityState(index, identity);
            if (state !== EXPLORER_GIT_IDENTITY_PANE_REPLACED) {
                renderExplorerGitPanels(index);
                if (state === EXPLORER_GIT_IDENTITY_SCOPE_CHANGED && pane._explorerGitSidebarOpen) {
                    // Same pane, different scope: the fresh load is owed here
                    // and now, because this pane is the one on screen.
                    pane._explorerGitReloadPending = false;
                    loadExplorerGitRepo(index);
                }
            }
        }
        if (!succeeded || !explorerGitIdentityIsCurrent(index, identity)) {
            return succeeded;
        }
        if (pane._explorerMode === 'directory') {
            loadExplorerPane(index, null, { force: true, showLoading: false });
        }
        if (EXPLORER_GIT_WORKTREE_ENDPOINTS.has(endpoint)) {
            await refreshExplorerAfterGitAction(index, body && body.path ? String(body.path) : '');
        }
        return succeeded;
    }

    /* Git actions that change working-tree or index state — publish only talks
       to the remote, so it never needs the ISSUE-2026-034 refresh below. */
    const EXPLORER_GIT_WORKTREE_ENDPOINTS = new Set([
        'stage', 'unstage', 'revert', 'commit', 'stage-all', 'unstage-all', 'discard-all',
    ]);

    /* ISSUE-2026-034: after a mutating Git action the Files tree and the open
       file/diff must not go stale. The tree reload guards internally on
       pane._explorerTreeSidebarOpen; the open file re-fetches in place (which
       drops the cached diff and re-pulls it when a diff view is showing).
       Single-path actions only refresh the file they touched — bulk actions
       (commit / stage-all / discard-all) can affect any open path. */
    async function refreshExplorerAfterGitAction(index, actionPath) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        reloadExplorerTree(index);
        if (pane._explorerMode !== 'file' || !pane._explorerFilePath) {
            return;
        }
        if (actionPath && actionPath !== pane._explorerFilePath) {
            return;
        }
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        await openExplorerFile(index, pane._explorerFilePath, {
            showLoading: false,
            preserveScroll: true,
            tab: pane._explorerActiveTabId
        });
    }

    function explorerGitStageFile(index, path) {
        if (!path) {
            return;
        }
        performExplorerGitAction(index, 'stage', { path });
    }

    function explorerGitStageAll(index) {
        performExplorerGitAction(index, 'stage-all', {});
    }

    /* Bulk form of the per-row Revert (OD-1): worktree restore of tracked
       files only — staged content is preserved and untracked files are never
       deleted (no git clean). Irreversible, so it goes through the in-page
       confirm shell. */
    async function explorerGitDiscardAll(index) {
        const confirmed = await openGenericConfirmModal({
            title: 'Discard all changes?',
            copy: 'Discard unstaged changes in tracked files in the current Git scope?',
            note: 'Unstaged edits will be lost. Staged versions and untracked files are kept.',
            confirmLabel: 'Discard all',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        performExplorerGitAction(index, 'discard-all', {});
    }

    function explorerGitUnstageFile(index, path) {
        if (!path) {
            return;
        }
        performExplorerGitAction(index, 'unstage', { path });
    }

    /* Bulk form of the per-row Unstage: index-only, so nothing in the
       worktree moves and Stage All puts it straight back — no confirm, unlike
       the irreversible Discard All beside it. */
    function explorerGitUnstageAll(index) {
        performExplorerGitAction(index, 'unstage-all', {});
    }

    /* Discarding working-tree edits is irreversible, so it goes through the
       in-page confirm shell (WebView2 blocks window.confirm) before the
       narrow discard route runs; a reverted open file reloads in place. */
    async function explorerGitRevertFile(index, path, status = '') {
        if (!path) {
            return;
        }
        const untracked = status === 'untracked';
        const confirmed = await openGenericConfirmModal({
            title: untracked ? 'Delete untracked file?' : 'Discard changes?',
            copy: untracked
                ? `Permanently delete the untracked file "${path}"?`
                : `Discard the unstaged changes in "${path}"?`,
            note: untracked
                ? 'This new file is not tracked by Git and cannot be restored after deletion.'
                : 'This unstaged edit will be lost. Any staged version of the file is kept.',
            confirmLabel: untracked ? 'Delete file' : 'Discard changes',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        /* A reverted open file reloads in place via the shared post-action
           refresh (ISSUE-2026-034). */
        performExplorerGitAction(index, 'revert', { path });
    }

    async function explorerGitCommit(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const message = String(pane._explorerGitCommitMessage || '').trim();
        if (!message) {
            pane._explorerGitRepoError = 'Commit message is required.';
            renderExplorerGitPanels(index);
            const input = document.getElementById(`explorer-git-commit-message-${index}`);
            if (input) {
                input.focus();
            }
            return;
        }
        const identity = explorerGitCaptureIdentity(index);
        const committed = await performExplorerGitAction(index, 'commit', { message });
        if (!committed) {
            return;
        }
        // The draft belongs to the pane, so it is cleared on the pane. The
        // render is slot work and waits on the same identity every other
        // post-await paint does.
        pane._explorerGitCommitMessage = '';
        if (explorerGitIdentityIsCurrent(index, identity)) {
            renderExplorerGitPanels(index);
        }
    }

    /* Publishing is outward-facing, so it confirms through the in-page shell
       (WebView2 blocks window.confirm — Regression Guardrail 4). */
    async function explorerGitPublish(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const git = (pane._explorerGitRepo && pane._explorerGitRepo.git) || {};
        const branch = git.branch || 'this branch';
        const confirmed = await openGenericConfirmModal({
            title: 'Publish branch?',
            copy: `Publish ${branch} to its remote?`,
            confirmLabel: 'Publish',
        });
        if (!confirmed) {
            return;
        }
        performExplorerGitAction(index, 'publish', {});
    }
