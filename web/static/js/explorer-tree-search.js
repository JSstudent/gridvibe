    /* ─────────────────────────────────────────────
       Explorer Files-tree name filter — the search box in the tree panel head.
       Finds files and directories by NAME under the pane's explorer root
       (`GET /api/explorer/<id>/find`) and renders the hits as a tree with the
       matched part of each name highlighted. File contents are never read here;
       that is the repository search panel (explorer-search.js).

       The tree builder and the request shape are pure functions exported for
       Node, so the match/nesting rules are executed by tests, not asserted as
       source text. Loaded after explorer-viewer.js; plain global-scope script
       like the other extracted modules.
    ───────────────────────────────────────────── */
    const EXPLORER_TREE_SEARCH_MIN_CHARS = 2;
    const EXPLORER_TREE_SEARCH_DEBOUNCE_MS = 300;

    /* ── DOM-free core (Node-testable) ─────────────────────────────────── */

    /* One find request's query string, as a plain object. */
    function explorerTreeSearchRequestParams(state) {
        const params = { q: String(state?.query ?? '').trim() };
        if (state?.case) params.case = '1';
        if (state?.word) params.word = '1';
        if (state?.regex) params.regex = '1';
        return params;
    }

    function explorerTreeSearchNodeSort(nodes) {
        nodes.sort((left, right) => {
            const leftDir = left.type === 'directory' ? 0 : 1;
            const rightDir = right.type === 'directory' ? 0 : 1;
            if (leftDir !== rightDir) {
                return leftDir - rightDir;
            }
            return left.name.localeCompare(right.name, undefined, { sensitivity: 'base' });
        });
        nodes.forEach(node => explorerTreeSearchNodeSort(node.children));
        return nodes;
    }

    /* Build the result tree from the flat payload: every hit, plus the
       directories between it and the root. An ancestor that is not itself a hit
       is carried as an unmatched directory node — it has to be shown for the
       hit under it to have a place, but it is not a result. */
    function buildExplorerTreeSearchNodes(entries) {
        const roots = [];
        const byPath = new Map();

        const ensure = path => {
            if (!path) {
                return null;
            }
            const existing = byPath.get(path);
            if (existing) {
                return existing;
            }
            const separator = path.lastIndexOf('/');
            const node = {
                path,
                name: separator === -1 ? path : path.slice(separator + 1),
                type: 'directory',
                ranges: [],
                matched: false,
                children: []
            };
            byPath.set(path, node);
            const parent = ensure(separator === -1 ? '' : path.slice(0, separator));
            (parent ? parent.children : roots).push(node);
            return node;
        };

        (Array.isArray(entries) ? entries : []).forEach(entry => {
            const path = String(entry?.path ?? '').replace(/^\/+|\/+$/g, '');
            if (!path) {
                return;
            }
            const node = ensure(path);
            if (!node) {
                return;
            }
            node.matched = true;
            node.type = entry?.type === 'directory' ? 'directory' : 'file';
            node.ranges = (Array.isArray(entry?.ranges) ? entry.ranges : [])
                .map(range => [Number(range?.[0]) || 0, Number(range?.[1]) || 0])
                .filter(range => range[1] > range[0]);
        });

        return explorerTreeSearchNodeSort(roots);
    }

    /* ── Pane state ────────────────────────────────────────────────────── */

    function ensureExplorerTreeSearchState(pane) {
        if (!pane._explorerTreeSearch || typeof pane._explorerTreeSearch !== 'object') {
            pane._explorerTreeSearch = {
                query: '',
                case: false,
                word: false,
                regex: false,
                loading: false,
                error: '',
                payload: null,
                abort: null,
                debounceTimer: null,
                requestSeq: 0
            };
        }
        return pane._explorerTreeSearch;
    }

    function explorerTreeSearchActive(pane) {
        const state = pane?._explorerTreeSearch;
        return Boolean(state) && String(state.query || '').trim().length >= EXPLORER_TREE_SEARCH_MIN_CHARS;
    }

    /* ── Request ───────────────────────────────────────────────────────── */

    async function runExplorerTreeSearch(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        const state = ensureExplorerTreeSearchState(pane);
        state.abort?.abort();
        state.abort = null;
        if (!explorerTreeSearchActive(pane)) {
            state.loading = false;
            state.error = '';
            state.payload = null;
            renderExplorerTreePanel(index);
            return;
        }

        const requestSeq = (state.requestSeq || 0) + 1;
        state.requestSeq = requestSeq;
        const controller = new AbortController();
        state.abort = controller;
        state.loading = true;
        state.error = '';
        renderExplorerTreePanel(index);
        try {
            const params = new URLSearchParams(explorerTreeSearchRequestParams(state));
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/find?${params.toString()}`,
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
        } catch (error) {
            if (error?.name === 'AbortError' || state.requestSeq !== requestSeq) {
                return;
            }
            console.error('[GridVibe Sessions] Explorer tree name search failed:', error);
            state.payload = null;
            state.error = error.message || 'Search failed.';
        } finally {
            if (state.requestSeq === requestSeq) {
                state.loading = false;
                state.abort = null;
                renderExplorerTreePanel(index);
            }
        }
    }

    function scheduleExplorerTreeSearch(index, { delay = EXPLORER_TREE_SEARCH_DEBOUNCE_MS } = {}) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerTreeSearchState(pane);
        clearTimeout(state.debounceTimer);
        state.debounceTimer = setTimeout(() => runExplorerTreeSearch(index), delay);
    }

    function clearExplorerTreeSearch(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerTreeSearchState(pane);
        clearTimeout(state.debounceTimer);
        state.abort?.abort();
        state.abort = null;
        state.query = '';
        state.loading = false;
        state.error = '';
        state.payload = null;
        renderExplorerTreePanel(index);
    }

    /* ── Rendering ─────────────────────────────────────────────────────── */

    function explorerTreeSearchHeadHtml(index) {
        return `
            <div class="explorer-tree-search">
                <input class="explorer-search-input" data-explorer-tree-search-input="${index}"
                       placeholder="Find file or folder" aria-label="Find file or folder by name"
                       spellcheck="false" autocomplete="off">
                <button type="button" class="explorer-search-btn" data-explorer-tree-search-case
                        aria-pressed="false" title="Match case">Aa</button>
                <button type="button" class="explorer-search-btn" data-explorer-tree-search-word
                        aria-pressed="false" title="Match whole word">ab</button>
                <button type="button" class="explorer-search-btn" data-explorer-tree-search-regex
                        aria-pressed="false" title="Use regular expression">.*</button>
            </div>`;
    }

    function explorerTreeSearchNameHtml(name, ranges) {
        const text = String(name ?? '');
        const spans = Array.isArray(ranges) ? ranges : [];
        if (!spans.length) {
            return escHtml(text);
        }
        let html = '';
        let cursor = 0;
        spans.forEach(range => {
            const start = Math.max(cursor, Math.min(Number(range[0]) || 0, text.length));
            const end = Math.max(start, Math.min(Number(range[1]) || 0, text.length));
            if (end <= start) {
                return;
            }
            html += escHtml(text.slice(cursor, start));
            html += `<mark class="explorer-search-match">${escHtml(text.slice(start, end))}</mark>`;
            cursor = end;
        });
        return `${html}${escHtml(text.slice(cursor))}`;
    }

    function explorerTreeSearchRowsHtml(pane, nodes, depth) {
        return nodes.map(node => {
            /* Git status only exists for a directory the browsable tree has
               already loaded; a result row without it simply carries no badge. */
            const loaded = typeof explorerTreeEntryForPath === 'function'
                ? explorerTreeEntryForPath(pane, node.path)
                : null;
            const entry = {
                path: node.path,
                name: node.name,
                type: node.type,
                git: loaded?.git || null,
                entry_kind: loaded?.entry_kind || '',
                revision: loaded?.revision || ''
            };
            const row = explorerTreeRowHtml(pane, entry, depth, {
                nameHtml: node.matched ? explorerTreeSearchNameHtml(node.name, node.ranges) : '',
                staticChevron: true
            });
            if (!node.children.length) {
                return row;
            }
            return `${row}<div class="explorer-tree-children">${
                explorerTreeSearchRowsHtml(pane, node.children, depth + 1)
            }</div>`;
        }).join('');
    }

    function explorerTreeSearchFooterHtml(payload) {
        const truncated = payload?.truncated || {};
        const parts = [];
        if (truncated.results) {
            parts.push(`showing the first ${Number(payload?.total || 0)} matches`);
        }
        if (truncated.scanned) {
            parts.push('stopped at the scan limit');
        }
        if (truncated.deadline) {
            parts.push('stopped at the time limit');
        }
        if (truncated.output) {
            parts.push('stopped at the output limit');
        }
        return parts.length
            ? `<div class="explorer-tree-search-note">${escHtml(parts.join(' · '))}</div>`
            : '';
    }

    function renderExplorerTreeSearchNodes(index) {
        const pane = terminals[index];
        const state = ensureExplorerTreeSearchState(pane);
        if (state.error) {
            return `
                <div class="explorer-diff-sidebar-error">
                    <div>${escHtml(state.error)}</div>
                    <button type="button" class="explorer-search-btn" data-explorer-tree-search-retry>Retry</button>
                </div>`;
        }
        if (!state.payload) {
            return state.loading
                ? '<div class="explorer-tree-loading">Searching…</div>'
                : '';
        }
        const nodes = buildExplorerTreeSearchNodes(state.payload.entries);
        const failure = state.payload.error ? `<div class="explorer-diff-sidebar-error">
            <div>${escHtml(state.payload.error)}</div>
            <button type="button" class="explorer-search-btn" data-explorer-tree-search-retry>Retry</button>
        </div>` : '';
        if (!nodes.length) {
            return failure || `<div class="explorer-tree-empty">No matching files or folders.</div>${explorerTreeSearchFooterHtml(state.payload)}`;
        }
        return `${failure}${explorerTreeSearchRowsHtml(pane, nodes, 0)}${explorerTreeSearchFooterHtml(state.payload)}`;
    }

    function syncExplorerTreeSearchControls(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = ensureExplorerTreeSearchState(pane);
        const input = panel.querySelector(`[data-explorer-tree-search-input="${index}"]`);
        if (input && input.value !== state.query) {
            input.value = state.query;
        }
        panel.classList.toggle('searching', Boolean(state.loading));
        Object.entries({ case: state.case, word: state.word, regex: state.regex })
            .forEach(([key, pressed]) => {
                panel.querySelector(`[data-explorer-tree-search-${key}]`)
                    ?.setAttribute('aria-pressed', pressed ? 'true' : 'false');
            });
    }

    /* Enter opens the first result, so a filter that narrows to one file is a
       type-and-go affordance rather than a type-then-aim one. */
    function openFirstExplorerTreeSearchResult(index) {
        const pane = terminals[index];
        const state = ensureExplorerTreeSearchState(pane);
        const first = (state.payload?.entries || [])[0];
        if (!first) {
            return;
        }
        if (first.type === 'directory') {
            loadExplorerPane(index, first.path);
        } else {
            openExplorerFile(index, first.path);
        }
    }

    function wireExplorerTreeSearchControls(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-tree-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        /* Handlers resolve the pane at event time rather than closing over the
           one wired against: a slot can be showing a different pane by then. */
        const paneState = () => ensureExplorerTreeSearchState(terminals[index]);
        const input = panel.querySelector(`[data-explorer-tree-search-input="${index}"]`);
        if (input && !input.dataset.bound) {
            input.dataset.bound = 'true';
            input.addEventListener('input', () => {
                paneState().query = input.value;
                scheduleExplorerTreeSearch(index);
            });
            input.addEventListener('keydown', event => {
                if (event.key === 'Escape') {
                    event.preventDefault();
                    event.stopPropagation();
                    clearExplorerTreeSearch(index);
                    return;
                }
                if (event.key === 'Enter') {
                    event.preventDefault();
                    openFirstExplorerTreeSearchResult(index);
                }
            });
        }
        [['case', 'case'], ['word', 'word'], ['regex', 'regex']].forEach(([attribute, key]) => {
            const button = panel.querySelector(`[data-explorer-tree-search-${attribute}]`);
            if (!button || button.dataset.bound) {
                return;
            }
            button.dataset.bound = 'true';
            button.addEventListener('click', () => {
                const state = paneState();
                state[key] = !state[key];
                syncExplorerTreeSearchControls(index);
                scheduleExplorerTreeSearch(index, { delay: 0 });
            });
        });
        /* The retry button lives inside the body, which is replaced on every
           render — one delegated listener on the panel outlives all of them. */
        if (!panel.dataset.treeSearchRetryBound) {
            panel.dataset.treeSearchRetryBound = 'true';
            panel.addEventListener('click', event => {
                if (event.target.closest('[data-explorer-tree-search-retry]')) {
                    runExplorerTreeSearch(index);
                }
            });
        }
    }

    /* Node-testable exports: the pure halves only — nothing here touches the
       DOM or the pane registry at load time. */
    if (typeof module === 'object' && module.exports) {
        module.exports = {
            EXPLORER_TREE_SEARCH_MIN_CHARS,
            EXPLORER_TREE_SEARCH_DEBOUNCE_MS,
            buildExplorerTreeSearchNodes,
            explorerTreeSearchRequestParams
        };
    }
