    /* ─────────────────────────────────────────────
       Explorer repository search — third explorer sidebar panel
       (docs/r&d/explorer_repository_search_proposal.md).
       Backend-executed, bounded, read-only search across every file under an
       explorer pane's root, rendered as per-file foldable groups. Loaded after
       explorer-editor.js and before terminals.js; plain global-scope script
       like the other extracted modules.
    ───────────────────────────────────────────── */
    const EXPLORER_SEARCH_TOGGLE_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="10.5" cy="10.5" r="6.25"/>
            <path d="M15.2 15.2 20 20"/>
        </svg>
    `;

    const EXPLORER_REPO_SEARCH_MIN_CHARS = 2;
    const EXPLORER_REPO_SEARCH_EXPAND_MAX_FILES = 10;

    const EXPLORER_SEARCH_ENGINE_LABELS = Object.freeze({
        'git-grep': 'git grep',
        'walk': 'file walk',
        'remote-grep': 'remote grep'
    });

    function ensureExplorerRepoSearchState(pane) {
        if (!pane._explorerRepoSearch || typeof pane._explorerRepoSearch !== 'object') {
            pane._explorerRepoSearch = {
                query: '',
                case: false,
                word: false,
                regex: false,
                scope: '',
                includeText: '',
                loading: false,
                error: '',
                payload: null,
                collapsed: new Set(),
                activeHit: -1,
                abort: null,
                debounceTimer: null,
                requestSeq: 0
            };
        }
        if (!(pane._explorerRepoSearch.collapsed instanceof Set)) {
            pane._explorerRepoSearch.collapsed = new Set();
        }
        return pane._explorerRepoSearch;
    }

    function setExplorerSearchSidebarOpen(index, open) {
        setExplorerSidebarPanelOpen(index, 'search', open);
    }

    function toggleExplorerSearchSidebar(index) {
        const pane = terminals[index];
        setExplorerSearchSidebarOpen(index, !pane?._explorerSearchSidebarOpen);
        if (pane?._explorerSearchSidebarOpen) {
            renderExplorerSearchPanel(index);
        }
    }

    /* Flattened hit list across groups, in payload order — the Enter/Arrow
       navigation model for the panel input. */
    function explorerRepoSearchHits(state) {
        const files = state.payload && Array.isArray(state.payload.files) ? state.payload.files : [];
        const hits = [];
        files.forEach(file => {
            (file.matches || []).forEach(match => {
                hits.push({ path: file.path, line: match.line });
            });
        });
        return hits;
    }

    function explorerRepoSearchParams(index, state) {
        const pane = terminals[index];
        const params = new URLSearchParams();
        params.set('q', state.query);
        if (state.case) params.set('case', '1');
        if (state.word) params.set('word', '1');
        if (state.regex) params.set('regex', '1');
        /* "Current folder" scope re-reads the pane path at run time, so
           navigating between runs narrows correctly. */
        if (state.scope === 'current') {
            const rel = typeof pane?._explorerPath === 'string' ? pane._explorerPath : '';
            if (rel) {
                params.set('scope', rel);
            }
        }
        String(state.includeText || '')
            .split(',')
            .map(value => value.trim())
            .filter(Boolean)
            .forEach(value => params.append('include', value));
        return params;
    }

    async function runExplorerRepoSearch(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        const state = ensureExplorerRepoSearchState(pane);
        state.abort?.abort();
        state.abort = null;
        if (state.query.trim().length < EXPLORER_REPO_SEARCH_MIN_CHARS) {
            state.loading = false;
            state.error = '';
            state.payload = null;
            state.activeHit = -1;
            renderExplorerSearchResults(index);
            return;
        }

        const requestSeq = (state.requestSeq || 0) + 1;
        state.requestSeq = requestSeq;
        const controller = new AbortController();
        state.abort = controller;
        state.loading = true;
        state.error = '';
        renderExplorerSearchResults(index);
        try {
            const params = explorerRepoSearchParams(index, state);
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/search?${params.toString()}`,
                { signal: controller.signal }
            );
            const data = await response.json();
            if (state.requestSeq !== requestSeq) {
                return;
            }
            if (!response.ok) {
                throw new Error(data.error || 'Search failed');
            }
            state.payload = data;
            const files = Array.isArray(data.files) ? data.files : [];
            /* Small result sets arrive expanded; large ones folded shut. */
            state.collapsed = files.length > EXPLORER_REPO_SEARCH_EXPAND_MAX_FILES
                ? new Set(files.map(file => file.path))
                : new Set();
            state.activeHit = files.length ? 0 : -1;
        } catch (error) {
            if (error?.name === 'AbortError' || state.requestSeq !== requestSeq) {
                return;
            }
            console.error('[GridVibe Sessions] Explorer repository search failed:', error);
            state.payload = null;
            state.error = error.message || 'Search failed.';
            state.activeHit = -1;
        } finally {
            if (state.requestSeq === requestSeq) {
                state.loading = false;
                state.abort = null;
                renderExplorerSearchResults(index);
            }
        }
    }

    function scheduleExplorerRepoSearch(index, { delay = EXPLORER_SEARCH_DEBOUNCE_MS } = {}) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerRepoSearchState(pane);
        clearTimeout(state.debounceTimer);
        state.debounceTimer = setTimeout(() => runExplorerRepoSearch(index), delay);
    }

    function explorerSearchHitTextHtml(match) {
        const text = String(match.text ?? '');
        const offset = Number(match.text_offset || 0);
        const ranges = Array.isArray(match.ranges) ? match.ranges : [];
        let html = offset > 0 ? '…' : '';
        let cursor = 0;
        ranges.forEach(range => {
            const start = Math.max(0, Math.min(Number(range[0]) || 0, text.length));
            const end = Math.max(start, Math.min(Number(range[1]) || 0, text.length));
            if (start > cursor) {
                html += escHtml(text.slice(cursor, start));
            }
            html += `<mark class="explorer-search-match">${escHtml(text.slice(start, end))}</mark>`;
            cursor = end;
        });
        html += escHtml(text.slice(cursor));
        if (offset + text.length < Number(match.line_length || offset + text.length)) {
            html += '…';
        }
        return html;
    }

    function explorerSearchGroupHtml(index, file, collapsed, activeKey) {
        const path = String(file.path || '');
        const matches = Array.isArray(file.matches) ? file.matches : [];
        const count = Number(file.match_count || matches.length);
        const countText = file.truncated ? `${matches.length}+` : String(count);
        const hits = collapsed ? '' : `
            <div class="explorer-search-group-hits">
                ${matches.map(match => {
                    const key = `${path}:${match.line}`;
                    return `
                    <button type="button" class="explorer-search-hit${key === activeKey ? ' active' : ''}"
                            data-explorer-search-path="${escHtml(path)}" data-explorer-search-line="${match.line}"
                            title="${escHtml(`${path}:${match.line}`)}">
                        <span class="explorer-search-hit-line">${match.line}</span>
                        <code class="explorer-search-hit-text">${explorerSearchHitTextHtml(match)}</code>
                    </button>`;
                }).join('')}
                ${file.truncated ? `<div class="explorer-search-hit-more">More matches in this file…</div>` : ''}
            </div>`;
        return `
            <div class="explorer-search-group" data-explorer-search-file="${escHtml(path)}">
                <button type="button" class="explorer-search-group-head" data-explorer-search-fold="${escHtml(path)}" aria-expanded="${collapsed ? 'false' : 'true'}">
                    <span class="explorer-search-group-toggle" aria-hidden="true">${collapsed ? '▸' : '▾'}</span>
                    ${explorerFileTypeIconHtml(path)}
                    <span class="explorer-search-group-name">${escHtml(file.name || path)}</span>
                    <span class="explorer-search-group-dir">${escHtml(file.dir || '')}</span>
                    <span class="explorer-search-group-count" title="${countText} match${count === 1 && !file.truncated ? '' : 'es'}">${countText}</span>
                </button>
                ${hits}
            </div>`;
    }

    function renderExplorerSearchResults(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-search-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = ensureExplorerRepoSearchState(pane);
        const results = panel.querySelector('.explorer-search-results');
        const summary = panel.querySelector('.explorer-search-summary');
        const footer = panel.querySelector('.explorer-search-footer');
        if (!results || !summary || !footer) {
            return;
        }
        panel.classList.toggle('loading', state.loading);

        const payload = state.payload;
        if (state.error) {
            summary.textContent = '';
            results.innerHTML = `
                <div class="explorer-diff-sidebar-error">
                    <div>${escHtml(state.error)}</div>
                    <button type="button" class="explorer-search-btn explorer-search-retry" data-explorer-search-retry>Retry</button>
                </div>`;
            footer.textContent = '';
        } else if (state.loading && !payload) {
            summary.textContent = '';
            results.innerHTML = '<div class="explorer-diff-sidebar-empty">Searching…</div>';
            footer.textContent = '';
        } else if (!payload) {
            summary.textContent = '';
            results.innerHTML = `<div class="explorer-diff-sidebar-empty">${
                state.query.trim().length < EXPLORER_REPO_SEARCH_MIN_CHARS
                    ? `Type at least ${EXPLORER_REPO_SEARCH_MIN_CHARS} characters to search this folder.`
                    : 'No results.'
            }</div>`;
            footer.textContent = '';
        } else {
            const files = Array.isArray(payload.files) ? payload.files : [];
            const totalMatches = Number(payload.total_matches || 0);
            summary.textContent = `${totalMatches} result${totalMatches === 1 ? '' : 's'} in ${files.length} file${files.length === 1 ? '' : 's'}`;
            const hits = explorerRepoSearchHits(state);
            const activeHit = hits[state.activeHit] || null;
            const activeKey = activeHit ? `${activeHit.path}:${activeHit.line}` : '';
            results.innerHTML = files.length
                ? files.map(file => explorerSearchGroupHtml(index, file, state.collapsed.has(file.path), activeKey)).join('')
                : '<div class="explorer-diff-sidebar-empty">No matches found.</div>';
            const footerParts = [];
            const engineLabel = EXPLORER_SEARCH_ENGINE_LABELS[payload.engine] || payload.engine || '';
            if (engineLabel) {
                footerParts.push(`engine: ${engineLabel}`);
            }
            const truncated = payload.truncated || {};
            if (truncated.files) {
                footerParts.push(`stopped at ${files.length} files`);
            }
            if (truncated.matches) {
                footerParts.push(`stopped at ${totalMatches} matches`);
            }
            if (truncated.deadline) {
                footerParts.push('stopped at the time limit');
            }
            footer.textContent = footerParts.join(' · ');
        }

        results.querySelectorAll('[data-explorer-search-fold]').forEach(button => {
            button.addEventListener('click', () => {
                const path = button.dataset.explorerSearchFold || '';
                if (state.collapsed.has(path)) {
                    state.collapsed.delete(path);
                } else {
                    state.collapsed.add(path);
                }
                renderExplorerSearchResults(index);
            });
        });
        results.querySelectorAll('[data-explorer-search-path]').forEach(button => {
            button.addEventListener('click', event => {
                const path = button.dataset.explorerSearchPath || '';
                const line = Number(button.dataset.explorerSearchLine || 0);
                activateExplorerSearchHit(index, path, line, {
                    pinned: Boolean(event.ctrlKey || event.metaKey)
                });
            });
        });
        results.querySelector('[data-explorer-search-retry]')?.addEventListener('click', () => {
            runExplorerRepoSearch(index);
        });
    }

    function renderExplorerSearchPanel(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-search-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = ensureExplorerRepoSearchState(pane);
        if (!panel.querySelector('.explorer-search-panel-head')) {
            panel.innerHTML = `
                <div class="explorer-search-panel-head">
                    <input class="explorer-search-input" data-explorer-repo-search-input="${index}"
                           placeholder="Search in folder" aria-label="Search in folder"
                           spellcheck="false" autocomplete="off">
                    <button type="button" class="explorer-search-btn" data-explorer-repo-search-case
                            aria-pressed="false" title="Match case">Aa</button>
                    <button type="button" class="explorer-search-btn" data-explorer-repo-search-word
                            aria-pressed="false" title="Match whole word">ab</button>
                    <button type="button" class="explorer-search-btn" data-explorer-repo-search-regex
                            aria-pressed="false" title="Use regular expression">.*</button>
                </div>
                <div class="explorer-search-panel-options">
                    <button type="button" class="explorer-search-btn explorer-search-scope-btn"
                            data-explorer-repo-search-scope title="Toggle search scope"></button>
                    <input class="explorer-search-include" data-explorer-repo-search-include="${index}"
                           placeholder="include: *.py, web/**" aria-label="Include glob filter"
                           spellcheck="false" autocomplete="off">
                    <button type="button" class="explorer-search-btn" data-explorer-repo-search-expand-all
                            title="Expand all" aria-label="Expand all">+</button>
                    <button type="button" class="explorer-search-btn" data-explorer-repo-search-collapse-all
                            title="Collapse all" aria-label="Collapse all">−</button>
                </div>
                <div class="explorer-search-summary" aria-live="polite"></div>
                <div class="explorer-search-results"></div>
                <div class="explorer-search-footer"></div>`;
            wireExplorerRepoSearchControls(index);
        }
        const input = panel.querySelector(`[data-explorer-repo-search-input="${index}"]`);
        if (input && input.value !== state.query) {
            input.value = state.query;
        }
        const includeInput = panel.querySelector(`[data-explorer-repo-search-include="${index}"]`);
        if (includeInput && includeInput.value !== state.includeText) {
            includeInput.value = state.includeText;
        }
        const toggles = { case: state.case, word: state.word, regex: state.regex };
        Object.entries(toggles).forEach(([key, pressed]) => {
            const button = panel.querySelector(`[data-explorer-repo-search-${key}]`);
            button?.setAttribute('aria-pressed', pressed ? 'true' : 'false');
        });
        const scopeButton = panel.querySelector('[data-explorer-repo-search-scope]');
        if (scopeButton) {
            const currentFolder = state.scope === 'current';
            scopeButton.textContent = currentFolder ? 'folder' : 'root';
            scopeButton.setAttribute('aria-pressed', currentFolder ? 'true' : 'false');
            scopeButton.title = currentFolder
                ? 'Searching the current folder — click to search the whole root'
                : 'Searching the whole root — click to search the current folder';
        }
        renderExplorerSearchResults(index);
    }

    function wireExplorerRepoSearchControls(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-search-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = ensureExplorerRepoSearchState(pane);
        const input = panel.querySelector(`[data-explorer-repo-search-input="${index}"]`);
        if (input && !input.dataset.bound) {
            input.dataset.bound = 'true';
            input.addEventListener('input', () => {
                state.query = input.value;
                state.activeHit = -1;
                scheduleExplorerRepoSearch(index);
            });
            input.addEventListener('keydown', event => {
                handleExplorerRepoSearchKeydown(index, event);
            });
        }
        const includeInput = panel.querySelector(`[data-explorer-repo-search-include="${index}"]`);
        if (includeInput && !includeInput.dataset.bound) {
            includeInput.dataset.bound = 'true';
            includeInput.addEventListener('input', () => {
                state.includeText = includeInput.value;
                scheduleExplorerRepoSearch(index);
            });
            includeInput.addEventListener('keydown', event => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    runExplorerRepoSearch(index);
                }
            });
        }
        ['case', 'word', 'regex'].forEach(key => {
            const button = panel.querySelector(`[data-explorer-repo-search-${key}]`);
            if (button && !button.dataset.bound) {
                button.dataset.bound = 'true';
                button.addEventListener('click', () => {
                    state[key] = !state[key];
                    renderExplorerSearchPanel(index);
                    scheduleExplorerRepoSearch(index, { delay: 0 });
                });
            }
        });
        const scopeButton = panel.querySelector('[data-explorer-repo-search-scope]');
        if (scopeButton && !scopeButton.dataset.bound) {
            scopeButton.dataset.bound = 'true';
            scopeButton.addEventListener('click', () => {
                state.scope = state.scope === 'current' ? '' : 'current';
                renderExplorerSearchPanel(index);
                scheduleExplorerRepoSearch(index, { delay: 0 });
            });
        }
        const expandAll = panel.querySelector('[data-explorer-repo-search-expand-all]');
        if (expandAll && !expandAll.dataset.bound) {
            expandAll.dataset.bound = 'true';
            expandAll.addEventListener('click', () => {
                state.collapsed.clear();
                renderExplorerSearchResults(index);
            });
        }
        const collapseAll = panel.querySelector('[data-explorer-repo-search-collapse-all]');
        if (collapseAll && !collapseAll.dataset.bound) {
            collapseAll.dataset.bound = 'true';
            collapseAll.addEventListener('click', () => {
                const files = state.payload && Array.isArray(state.payload.files) ? state.payload.files : [];
                state.collapsed = new Set(files.map(file => file.path));
                renderExplorerSearchResults(index);
            });
        }
    }

    /* Enter/Shift+Enter steps and activates; Arrows move the highlight only;
       Escape clears, then closes. Panel-scoped — no document listeners. */
    function handleExplorerRepoSearchKeydown(index, event) {
        const pane = terminals[index];
        const state = ensureExplorerRepoSearchState(pane);
        const hits = explorerRepoSearchHits(state);
        if (event.key === 'Enter') {
            if (!hits.length) {
                return;
            }
            event.preventDefault();
            const step = event.shiftKey ? -1 : 1;
            const next = state.activeHit < 0
                ? 0
                : (state.activeHit + step + hits.length) % hits.length;
            state.activeHit = next;
            const hit = hits[next];
            renderExplorerSearchResults(index);
            activateExplorerSearchHit(index, hit.path, hit.line, { pinned: false });
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            if (!hits.length) {
                return;
            }
            event.preventDefault();
            const step = event.key === 'ArrowDown' ? 1 : -1;
            state.activeHit = state.activeHit < 0
                ? 0
                : (state.activeHit + step + hits.length) % hits.length;
            const hit = hits[state.activeHit];
            /* Unfold the group holding the newly highlighted hit. */
            state.collapsed.delete(hit.path);
            renderExplorerSearchResults(index);
            document.querySelector(
                `#explorer-search-panel-${index} [data-explorer-search-path="${CSS.escape(hit.path)}"][data-explorer-search-line="${hit.line}"]`
            )?.scrollIntoView({ block: 'nearest' });
            return;
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopPropagation();
            if (state.query || state.payload) {
                state.query = '';
                state.payload = null;
                state.error = '';
                state.activeHit = -1;
                state.abort?.abort();
                renderExplorerSearchPanel(index);
            } else {
                setExplorerSearchSidebarOpen(index, false);
            }
        }
    }

    async function activateExplorerSearchHit(index, path, line, { pinned = false } = {}) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }
        const state = ensureExplorerRepoSearchState(pane);
        const opened = await openExplorerFile(index, path, { pinned: Boolean(pinned) });
        if (!opened) {
            return;
        }
        scrollExplorerSourceToLine(index, line);
        /* Seed the in-file find with the same query so every other hit in the
           file is highlighted too. */
        focusExplorerSearch(index, state.query);
    }

    function scrollExplorerSourceToLine(index, line) {
        const card = document.getElementById(`tc-${index}`);
        const row = card?.querySelector(`[data-explorer-line="${line}"]`);
        if (!row) {
            return;
        }
        row.scrollIntoView({ block: 'center' });
        row.classList.add('explorer-source-line-flash');
        setTimeout(() => row.classList.remove('explorer-source-line-flash'), 1200);
    }

    /* Ctrl+Shift+F entry point: open the panel, focus the input, and seed it
       from the current selection (the copy → find → paste ergonomics of the
       in-file find). Returns false so the keybinding can fall through to the
       terminal scrollback overlay when this pane has no live search input. */
    function focusExplorerRepoSearch(index, seedQuery = '') {
        const pane = terminals[index];
        if (!pane) {
            return false;
        }
        setExplorerSearchSidebarOpen(index, true);
        renderExplorerSearchPanel(index);
        const input = document.querySelector(`[data-explorer-repo-search-input="${index}"]`);
        if (!input) {
            return false;
        }
        const state = ensureExplorerRepoSearchState(pane);
        if (seedQuery) {
            state.query = seedQuery;
            state.activeHit = -1;
            input.value = seedQuery;
            scheduleExplorerRepoSearch(index, { delay: 0 });
        }
        input.focus();
        input.select();
        return true;
    }
