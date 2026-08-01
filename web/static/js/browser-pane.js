    /* ─────────────────────────────────────────────
       Browser pane — tabbed preview surface.

       A browser pane holds up to BROWSER_MAX_TABS tabs, each backed by its own
       long-lived <iframe> so switching tabs never reloads the page (the point
       of the pane is to keep an app you are testing alive). The active tab's
       URL is mirrored into the session's `initial_command`, which stays the
       single-URL contract every other reader already uses.

       Same-origin frames get their `window.open` and `target="_blank"` clicks
       captured into new pane tabs, so GridVibe previewing itself keeps its
       launcher → workspace hop inside the pane instead of escaping to the OS
       browser. Cross-origin frames cannot be touched (browser security), so
       those still open externally.

       Loaded before terminals.js; shared pane state (`terminals`,
       `sessionIds`, `isBrowserSession`, `escHtml`, ...) lives in terminals.js.
    ───────────────────────────────────────────── */

    /* Mirrors BROWSER_MAX_TABS in web/saved_sessions.py. */
    const BROWSER_MAX_TABS = 8;
    const BROWSER_DEFAULT_URL = 'http://127.0.0.1:3000';
    const BROWSER_TAB_PERSIST_DEBOUNCE_MS = 400;
    const BROWSER_FRAME_SANDBOX = 'allow-downloads allow-forms allow-modals allow-popups '
        + 'allow-popups-to-escape-sandbox allow-same-origin allow-scripts';

    const browserPersistTimers = new Map();

    function getBrowserSessionUrl(session) {
        const rawUrl = String(session?.initial_command || '').trim();
        if (!rawUrl) {
            return BROWSER_DEFAULT_URL;
        }
        return rawUrl.includes('://') ? rawUrl : `http://${rawUrl}`;
    }

    function normalizeBrowserUrlInput(value) {
        const rawValue = String(value || '').trim();
        if (!rawValue) {
            throw new Error('Enter a browser URL.');
        }
        const candidate = rawValue.includes('://') ? rawValue : `http://${rawValue}`;
        const parsed = new URL(candidate);
        if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.host) {
            throw new Error('Browser panes only support http:// and https:// URLs.');
        }
        return parsed.href;
    }

    /* A readable strip label: host for a bare root, else the last path
       segment, so "/terminals?group=x" reads as "terminals" not the full URL. */
    function browserTabLabelFromUrl(url) {
        try {
            const parsed = new URL(url);
            const segments = parsed.pathname.split('/').filter(Boolean);
            return segments.length ? segments[segments.length - 1] : parsed.host;
        } catch (_) {
            return url;
        }
    }

    function browserMakeTab(url, id, title = '') {
        return { id, url, title: title || browserTabLabelFromUrl(url), windowName: '' };
    }

    /* True when this very GridVibe page is itself being shown inside a GridVibe
       browser pane. Previewing GridVibe in a pane is the point of the feature,
       but the page one level in must not render browser frames of its own:
       pointed back at GridVibe it would re-embed the same workspace — including
       the pane doing the embedding — and each level would spawn the next.
       One level deep stays fully functional; the level below shows a notice. */
    function browserIsNestedPreview() {
        try {
            return Boolean(window.frameElement?.classList.contains('browser-frame'));
        } catch (_) {
            /* Cross-origin parent: `frameElement` throws, and a cross-origin
               parent cannot be a GridVibe pane, so this is not a nested view. */
            return false;
        }
    }

    /* Build the pane's tab list once, from the session's persisted strip (or
       its single URL for panes saved before tabs existed).

       Tab ids are positional (`btab-0`, `btab-1`, ...) rather than drawn from a
       global counter, so the same session always yields the same ids. The card
       markup is rendered from one object and the live pane may be another;
       positional ids keep `browser-frame-${index}-${tab.id}` matching in both.
       `_browserTabSeq` then hands out fresh ids for tabs opened later, so a
       close followed by an open cannot reuse a live id. */
    function browserEnsureTabState(pane, session = null) {
        if (!pane) {
            return null;
        }
        if (Array.isArray(pane._browserTabs) && pane._browserTabs.length) {
            return pane;
        }
        const source = session || pane._session || {};
        const savedTabs = Array.isArray(source.browser_tabs) ? source.browser_tabs : [];
        const tabs = [];
        savedTabs.forEach(rawUrl => {
            if (tabs.length >= BROWSER_MAX_TABS) {
                return;
            }
            try {
                tabs.push(browserMakeTab(normalizeBrowserUrlInput(rawUrl), `btab-${tabs.length}`));
            } catch (_) {
                /* Drop an unusable persisted tab rather than failing the pane. */
            }
        });
        if (!tabs.length) {
            tabs.push(browserMakeTab(getBrowserSessionUrl(source), 'btab-0'));
        }
        pane._browserTabs = tabs;
        pane._browserTabSeq = tabs.length;
        pane._browserActiveTab = Math.max(
            0,
            Math.min(tabs.length - 1, Number(source.browser_active_tab) || 0)
        );
        return pane;
    }

    function browserPaneAt(index) {
        const pane = terminals[index];
        return pane && isBrowserPaneInstance(pane) ? browserEnsureTabState(pane) : null;
    }

    function browserActiveTab(pane) {
        if (!pane?._browserTabs?.length) {
            return null;
        }
        return pane._browserTabs[pane._browserActiveTab] || pane._browserTabs[0];
    }

    /* Shape consumed by Save Workspace and the saved-session launch payload. */
    function browserSerializeTabs(pane, session = null) {
        const source = session || pane?._session || {};
        if (!pane || !Array.isArray(pane._browserTabs) || !pane._browserTabs.length) {
            /* Pane never rendered (a cached/off-screen group): fall back to what
               the session already carries so saving cannot erase its tabs. */
            const savedTabs = Array.isArray(source.browser_tabs) && source.browser_tabs.length
                ? source.browser_tabs.slice(0, BROWSER_MAX_TABS)
                : [getBrowserSessionUrl(source)];
            return {
                tabs: savedTabs,
                active_tab: Math.max(0, Math.min(savedTabs.length - 1, Number(source.browser_active_tab) || 0))
            };
        }
        return {
            tabs: pane._browserTabs.map(tab => tab.url),
            active_tab: Math.max(0, Math.min(pane._browserTabs.length - 1, pane._browserActiveTab))
        };
    }

    /* ── Persistence ──────────────────────────────
       The whole strip is pushed to the session so a restart-restore, a
       sibling-pane close rebuild, and Save Workspace all read the same state.
       Debounced because rapid navigation inside a frame fires repeatedly. */
    function browserCancelPendingPersist(sessionId) {
        if (!sessionId || !browserPersistTimers.has(sessionId)) {
            return false;
        }
        clearTimeout(browserPersistTimers.get(sessionId));
        browserPersistTimers.delete(sessionId);
        return true;
    }

    function browserPersistTabs(index, { immediate = false } = {}) {
        const pane = browserPaneAt(index);
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }

        const existingTimer = browserPersistTimers.get(sessionId);
        if (existingTimer) {
            clearTimeout(existingTimer);
            browserPersistTimers.delete(sessionId);
        }

        const push = async () => {
            browserPersistTimers.delete(sessionId);
            const currentIndex = sessionIds.indexOf(sessionId);
            if (
                currentIndex === -1
                || terminals[currentIndex] !== pane
                || !isBrowserSession(terminals[currentIndex]?._session)
                || isSessionModeSwitchPending(sessionId)
            ) {
                return;
            }
            const snapshot = browserSerializeTabs(pane);
            /* Keep the local session object in step even if the POST fails, so
               a Save Workspace issued right after still sees the live strip. */
            if (pane._session) {
                pane._session.browser_tabs = snapshot.tabs;
                pane._session.browser_active_tab = snapshot.active_tab;
                pane._session.initial_command = snapshot.tabs[snapshot.active_tab];
            }
            try {
                const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/mode`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        startup_mode: 'browser',
                        tabs: snapshot.tabs,
                        active_tab: snapshot.active_tab
                    })
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.error || `status ${response.status}`);
                }
                const data = await response.json();
                if (pane._session) {
                    pane._session = data;
                }
            } catch (error) {
                console.error('[GridVibe Sessions] browser tab persist failed:', error);
            }
        };

        if (immediate) {
            push();
            return;
        }
        browserPersistTimers.set(sessionId, setTimeout(push, BROWSER_TAB_PERSIST_DEBOUNCE_MS));
    }

    /* ── Markup ─────────────────────────────────── */
    function browserFrameHtml(index, tab, isActive) {
        if (browserIsNestedPreview()) {
            /* Same id/class hooks as a real frame so tab switching, closing and
               persistence behave identically — only the live document is
               withheld. `browser-frame-blocked` (not `browser-frame`) also stops
               a deeper page from mistaking this for a pane it sits inside. */
            return `
                <div
                    class="browser-frame-blocked${isActive ? ' active' : ''}"
                    id="browser-frame-${index}-${tab.id}"
                    data-browser-frame="${index}"
                    data-browser-tab-id="${escHtml(tab.id)}"
                    ${isActive ? '' : 'hidden'}
                >
                    <p>Nested preview disabled</p>
                    <p class="browser-frame-blocked-hint">
                        This GridVibe page is already open inside a browser pane.
                        Use <strong>Open</strong> to view ${escHtml(tab.url)} in a real browser tab.
                    </p>
                </div>
            `;
        }
        return `
            <iframe
                class="browser-frame${isActive ? ' active' : ''}"
                id="browser-frame-${index}-${tab.id}"
                data-browser-frame="${index}"
                data-browser-tab-id="${escHtml(tab.id)}"
                title="${escHtml(tab.title || `Browser ${index + 1}`)}"
                src="${escHtml(tab.url)}"
                sandbox="${BROWSER_FRAME_SANDBOX}"
                ${isActive ? '' : 'hidden'}
            ></iframe>
        `;
    }

    function browserTabStripHtml(index, pane) {
        const tabs = pane._browserTabs.map((tab, tabIndex) => `
            <div
                class="browser-tab${tabIndex === pane._browserActiveTab ? ' active' : ''}"
                data-browser-tab="${index}"
                data-browser-tab-index="${tabIndex}"
                data-browser-tab-id="${escHtml(tab.id)}"
                draggable="true"
                role="tab"
                tabindex="0"
                aria-selected="${tabIndex === pane._browserActiveTab ? 'true' : 'false'}"
                title="${escHtml(tab.url)}"
            >
                <span class="browser-tab-label">${escHtml(tab.title || browserTabLabelFromUrl(tab.url))}</span>
                ${pane._browserTabs.length > 1 ? `
                    <button
                        type="button"
                        class="browser-tab-close"
                        data-browser-tab-close="${index}"
                        data-browser-tab-index="${tabIndex}"
                        title="Close tab"
                        aria-label="Close tab"
                    >${UI_CLOSE_ICON}</button>
                ` : ''}
            </div>
        `).join('');

        return `
            <div class="browser-tabstrip" id="browser-tabs-${index}" role="tablist">
                ${tabs}
                <button
                    type="button"
                    class="browser-tab-new"
                    data-browser-tab-new="${index}"
                    title="Open a new tab in this pane"
                    aria-label="Open a new tab in this pane"
                    ${pane._browserTabs.length >= BROWSER_MAX_TABS ? 'disabled' : ''}
                >${UI_PLUS_ICON}</button>
            </div>
        `;
    }

    function browserSurfaceHtml(index, session) {
        /* Normally `terminals[index]` is already the live browser pane (the
           grid builder pushes the pane before building its card). Anything
           else — a stale pane from a previous grid, a not-yet-assigned slot —
           gets a detached state object instead, so this never writes tab state
           onto a pane that is not a browser pane. Positional tab ids keep the
           rendered frame ids matching whichever object ends up live. */
        const livePane = browserPaneAt(index);
        const pane = livePane || browserEnsureTabState({ _session: session }, session);
        const active = browserActiveTab(pane);
        const activeUrl = active ? active.url : getBrowserSessionUrl(session);
        return `
            <div class="browser-surface" id="browser-${index}">
                ${browserTabStripHtml(index, pane)}
                <div class="browser-bar">
                    <input
                        class="browser-url-input"
                        id="browser-url-${index}"
                        data-browser-url="${index}"
                        type="url"
                        value="${escHtml(activeUrl)}"
                        title="${escHtml(activeUrl)}"
                        aria-label="Browser URL"
                    >
                    <button
                        type="button"
                        class="browser-open-btn"
                        id="browser-open-${index}"
                        data-browser-open="${index}"
                        title="Open this URL in an external browser tab"
                    >Open</button>
                </div>
                <div class="browser-frames" id="browser-frames-${index}">
                    ${pane._browserTabs.map((tab, tabIndex) => browserFrameHtml(
                        index, tab, tabIndex === pane._browserActiveTab
                    )).join('')}
                </div>
            </div>
        `;
    }

    /* Full terminal-wrapper contents, used when converting a live pane. */
    function renderBrowserSurface(index, session) {
        return `
            <div class="terminal-surface">
                ${browserSurfaceHtml(index, session)}
            </div>
            <div class="placeholder" id="ph-${index}">
                <div class="spinner"></div>
                <span style="font-size:.78rem">Loading browser...</span>
            </div>
        `;
    }

    /* ── DOM helpers ────────────────────────────── */
    function browserFrameForTab(index, tabId) {
        return document.getElementById(`browser-frame-${index}-${tabId}`);
    }

    function browserActiveFrame(index) {
        const pane = browserPaneAt(index);
        const tab = pane ? browserActiveTab(pane) : null;
        return tab ? browserFrameForTab(index, tab.id) : null;
    }

    function browserSyncUrlInput(index) {
        const pane = browserPaneAt(index);
        const tab = pane ? browserActiveTab(pane) : null;
        const input = document.getElementById(`browser-url-${index}`);
        if (!tab || !input) {
            return;
        }
        input.value = tab.url;
        input.title = tab.url;
    }

    /* Re-render just the strip (tabs and frames keep their DOM and live state
       — only the strip markup and the frames' active/hidden flags change). */
    function browserRenderTabStrip(index) {
        const pane = browserPaneAt(index);
        const strip = document.getElementById(`browser-tabs-${index}`);
        if (!pane || !strip) {
            return;
        }
        strip.outerHTML = browserTabStripHtml(index, pane);
        browserWireTabStrip(index);

        pane._browserTabs.forEach((tab, tabIndex) => {
            const frame = browserFrameForTab(index, tab.id);
            if (!frame) {
                return;
            }
            const isActive = tabIndex === pane._browserActiveTab;
            frame.classList.toggle('active', isActive);
            frame.hidden = !isActive;
        });
        browserSyncUrlInput(index);
    }

    /* ── Tab actions ────────────────────────────── */
    function browserSelectTab(index, tabIndex) {
        const pane = browserPaneAt(index);
        if (!pane || tabIndex < 0 || tabIndex >= pane._browserTabs.length) {
            return;
        }
        if (pane._browserActiveTab === tabIndex) {
            return;
        }
        pane._browserActiveTab = tabIndex;
        browserRenderTabStrip(index);
        browserPersistTabs(index);
    }

    function browserOpenTab(index, url, { activate = true, windowName = '' } = {}) {
        const pane = browserPaneAt(index);
        if (!pane) {
            return;
        }

        /* `window.open(url, 'some-name')` means "reuse the window called that",
           which maps to reusing the tab we opened under that name. Without this
           every click of a button that reuses a named window (GridVibe's own
           "Show active terminals" targets `gridvibe-workspace-<id>`) would stack up
           another tab. */
        if (windowName) {
            const existing = pane._browserTabs.findIndex(tab => tab.windowName === windowName);
            if (existing !== -1) {
                let reuseUrl;
                try {
                    reuseUrl = normalizeBrowserUrlInput(url);
                } catch (_) {
                    return;
                }
                const tab = pane._browserTabs[existing];
                if (tab.url !== reuseUrl) {
                    tab.url = reuseUrl;
                    tab.title = browserTabLabelFromUrl(reuseUrl);
                    const frame = browserFrameForTab(index, tab.id);
                    if (frame && frame.tagName === 'IFRAME') {
                        frame.src = reuseUrl;
                    }
                }
                pane._browserActiveTab = existing;
                browserRenderTabStrip(index);
                browserPersistTabs(index);
                return;
            }
        }

        if (pane._browserTabs.length >= BROWSER_MAX_TABS) {
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Browser panes hold at most ${BROWSER_MAX_TABS} tabs.`;
            }
            return;
        }

        let normalized;
        try {
            normalized = normalizeBrowserUrlInput(url);
        } catch (error) {
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Browser tab rejected: ${error.message}`;
            }
            return;
        }

        pane._browserTabSeq = Math.max(Number(pane._browserTabSeq) || 0, pane._browserTabs.length);
        const tab = browserMakeTab(normalized, `btab-${pane._browserTabSeq}`);
        tab.windowName = windowName;
        pane._browserTabSeq += 1;
        pane._browserTabs.push(tab);
        if (activate) {
            pane._browserActiveTab = pane._browserTabs.length - 1;
        }

        const frames = document.getElementById(`browser-frames-${index}`);
        if (frames) {
            frames.insertAdjacentHTML('beforeend', browserFrameHtml(index, tab, activate));
            browserWireFrame(index, browserFrameForTab(index, tab.id));
        }
        browserRenderTabStrip(index);
        browserPersistTabs(index);
    }

    function browserCloseTab(index, tabIndex) {
        const pane = browserPaneAt(index);
        if (!pane || pane._browserTabs.length <= 1) {
            return;
        }
        if (tabIndex < 0 || tabIndex >= pane._browserTabs.length) {
            return;
        }

        const [removed] = pane._browserTabs.splice(tabIndex, 1);
        browserFrameForTab(index, removed.id)?.remove();
        if (pane._browserActiveTab >= pane._browserTabs.length) {
            pane._browserActiveTab = pane._browserTabs.length - 1;
        } else if (tabIndex < pane._browserActiveTab) {
            pane._browserActiveTab -= 1;
        }
        browserRenderTabStrip(index);
        browserPersistTabs(index);
    }

    /* Drag-reorder one tab before/after another, matching the session-tab and
       explorer-tab strips. Only the strip markup is re-rendered — the frames
       keep their DOM, so moving a tab never reloads the page it is showing.
       The active tab is re-resolved by identity rather than by its old index,
       so the pane keeps showing the same page whichever way the move went.
       Persisted order follows automatically: browserSerializeTabs reads the
       array in order. */
    function browserReorderTab(index, draggedId, targetId, before) {
        const pane = browserPaneAt(index);
        if (!pane || !draggedId || draggedId === targetId) {
            return;
        }
        const tabs = pane._browserTabs;
        const from = tabs.findIndex(tab => tab.id === draggedId);
        if (from === -1 || !tabs.some(tab => tab.id === targetId)) {
            return;
        }

        const activeTab = tabs[pane._browserActiveTab];
        const [dragged] = tabs.splice(from, 1);
        const insertAt = tabs.findIndex(tab => tab.id === targetId) + (before ? 0 : 1);
        tabs.splice(insertAt, 0, dragged);
        pane._browserActiveTab = Math.max(0, tabs.indexOf(activeTab));

        browserRenderTabStrip(index);
        browserPersistTabs(index);
    }

    function browserClearTabDragMarkers(index) {
        document.getElementById(`browser-tabs-${index}`)
            ?.querySelectorAll('.browser-tab')
            .forEach(el => el.classList.remove('dragging', 'drag-before', 'drag-after'));
    }

    function reloadBrowserPane(index) {
        const frame = browserActiveFrame(index);
        const pane = browserPaneAt(index);
        const tab = pane ? browserActiveTab(pane) : null;
        if (!frame || !tab) {
            return;
        }
        frame.src = tab.url;
    }

    function openBrowserPaneExternally(index) {
        const pane = browserPaneAt(index);
        const tab = pane ? browserActiveTab(pane) : null;
        const url = tab ? tab.url : getBrowserSessionUrl(terminals[index]?._session);
        if (!url) {
            return;
        }
        window.open(url, '_blank', 'noopener');
    }

    /* Navigate the active tab. Kept as the single-URL entry point so the
       header reload button and the URL bar behave the same as before tabs. */
    async function navigateBrowserPane(index, value) {
        const pane = browserPaneAt(index);
        const tab = pane ? browserActiveTab(pane) : null;
        const input = document.getElementById(`browser-url-${index}`);
        if (!pane || !tab || !sessionIds[index]) {
            return;
        }

        let nextUrl;
        try {
            nextUrl = normalizeBrowserUrlInput(value);
        } catch (error) {
            if (input) {
                input.value = tab.url;
            }
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Browser URL rejected: ${error.message}`;
            }
            return;
        }

        if (nextUrl === tab.url) {
            return;
        }

        tab.url = nextUrl;
        tab.title = browserTabLabelFromUrl(nextUrl);
        const frame = browserFrameForTab(index, tab.id);
        if (frame) {
            frame.src = nextUrl;
        }
        browserRenderTabStrip(index);
        browserPersistTabs(index, { immediate: true });
    }

    /* ── Same-origin frame hooks ──────────────────
       Only reachable when the framed document is same-origin: any cross-origin
       access below throws SecurityError, which we swallow and leave the frame
       untouched (its popups then behave as the browser decides). */
    function browserFrameWindow(frame) {
        try {
            const win = frame?.contentWindow;
            /* Touching `document` is the cheapest same-origin probe. */
            return win && win.document ? win : null;
        } catch (_) {
            return null;
        }
    }

    function browserResolveFrameUrl(win, rawUrl) {
        try {
            const resolved = new URL(String(rawUrl || ''), win.location.href);
            return ['http:', 'https:'].includes(resolved.protocol) ? resolved.href : '';
        } catch (_) {
            return '';
        }
    }

    function browserHookFrameWindow(index, frame) {
        const win = browserFrameWindow(frame);
        if (!win) {
            return;
        }
        try {
            if (win.__gridvibeBrowserPaneHooked) {
                return;
            }
            win.__gridvibeBrowserPaneHooked = true;

            /* window.open → a new pane tab. Returning null is safe for the
               callers we capture (GridVibe's own launcher ignores the handle);
               anything we cannot turn into an http(s) URL falls through to the
               native implementation. */
            const nativeOpen = typeof win.open === 'function' ? win.open.bind(win) : null;
            win.open = function hookedOpen(url, target, features) {
                const resolved = browserResolveFrameUrl(win, url);
                const name = String(target || '').trim();
                /* `_self`/`_top`/`_parent` navigate an existing window rather
                   than opening one, so they are not ours to capture. */
                if (!resolved || ['_self', '_top', '_parent'].includes(name)) {
                    return nativeOpen ? nativeOpen(url, target, features) : null;
                }
                const reusableName = ['_blank', '_new', ''].includes(name) ? '' : name;
                browserOpenTab(index, resolved, { windowName: reusableName });
                return null;
            };

            /* target="_blank" anchors, captured before the default action. */
            win.document.addEventListener('click', event => {
                const anchor = event.target?.closest?.('a[target="_blank"], a[target="_new"]');
                if (!anchor) {
                    return;
                }
                const resolved = browserResolveFrameUrl(win, anchor.getAttribute('href'));
                if (!resolved) {
                    return;
                }
                event.preventDefault();
                browserOpenTab(index, resolved);
            }, true);
        } catch (_) {
            /* Cross-origin document raced into place — leave the frame alone. */
        }
    }

    /* After each load, adopt the frame's real URL and title for same-origin
       pages so in-frame navigation (links, redirects, history) is reflected in
       the tab strip and persisted. */
    function browserSyncTabFromFrame(index, frame) {
        const pane = browserPaneAt(index);
        const tabId = frame?.dataset?.browserTabId;
        const tab = pane?._browserTabs.find(entry => entry.id === tabId);
        if (!pane || !tab) {
            return;
        }
        const win = browserFrameWindow(frame);
        if (!win) {
            return;
        }
        let changed = false;
        try {
            const href = win.location.href;
            if (href && href !== 'about:blank' && href !== tab.url) {
                tab.url = href;
                changed = true;
            }
            const title = String(win.document.title || '').trim();
            const nextTitle = title || browserTabLabelFromUrl(tab.url);
            if (nextTitle !== tab.title) {
                tab.title = nextTitle;
                changed = true;
            }
        } catch (_) {
            return;
        }
        if (changed) {
            browserRenderTabStrip(index);
            browserPersistTabs(index);
        }
    }

    /* ── Wiring ─────────────────────────────────── */
    function browserWireFrame(index, frame) {
        if (!frame || frame.dataset.bound) {
            return;
        }
        frame.dataset.bound = 'true';
        if (frame.tagName !== 'IFRAME') {
            /* Blocked nested preview: no document will ever load, so clear the
               pane's spinner now instead of leaving it turning forever. */
            document.getElementById(`ph-${index}`)?.remove();
            return;
        }
        frame.addEventListener('load', () => {
            document.getElementById(`ph-${index}`)?.remove();
            browserHookFrameWindow(index, frame);
            browserSyncTabFromFrame(index, frame);
        });
    }

    function browserWireTabStrip(index) {
        const strip = document.getElementById(`browser-tabs-${index}`);
        if (!strip || strip.dataset.bound) {
            return;
        }
        strip.dataset.bound = 'true';

        strip.addEventListener('click', event => {
            const closeButton = event.target.closest(`[data-browser-tab-close="${index}"]`);
            if (closeButton) {
                event.preventDefault();
                event.stopPropagation();
                browserCloseTab(index, Number(closeButton.dataset.browserTabIndex));
                return;
            }
            if (event.target.closest(`[data-browser-tab-new="${index}"]`)) {
                event.preventDefault();
                event.stopPropagation();
                /* Always the default URL, never a copy of the active tab:
                   duplicating a tab that is showing GridVibe itself made the
                   pane re-embed its own workspace, which then re-rendered this
                   same pane and its tabs one level deeper, without end. */
                browserOpenTab(index, BROWSER_DEFAULT_URL);
                return;
            }
            const tab = event.target.closest(`[data-browser-tab="${index}"]`);
            if (tab) {
                event.preventDefault();
                event.stopPropagation();
                browserSelectTab(index, Number(tab.dataset.browserTabIndex));
            }
        });

        strip.addEventListener('keydown', event => {
            if (event.key !== 'Enter' && event.key !== ' ') {
                return;
            }
            const tab = event.target.closest(`[data-browser-tab="${index}"]`);
            if (!tab) {
                return;
            }
            event.preventDefault();
            browserSelectTab(index, Number(tab.dataset.browserTabIndex));
        });

        /* Drag-reorder, delegated on the strip so it survives a re-render.
           Drag events bubble, and the dragged tab is tracked by id rather than
           by index so a mid-drag re-render cannot retarget the move. The pane
           card's own dragover/drop guard on `draggedCard` (set only by a pane
           header dragstart), so a tab drag never starts a pane swap. */
        strip.addEventListener('dragstart', event => {
            const tab = event.target.closest?.(`[data-browser-tab="${index}"]`);
            if (!tab) {
                return;
            }
            const pane = browserPaneAt(index);
            if (pane) {
                pane._browserDraggedTabId = tab.dataset.browserTabId || '';
            }
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                try {
                    event.dataTransfer.setData('text/plain', tab.dataset.browserTabId || '');
                } catch (_) {
                    /* setData throws in some embedded WebViews; the drag still
                       works off the id held on the pane. */
                }
            }
            tab.classList.add('dragging');
        });

        strip.addEventListener('dragend', () => {
            const pane = browserPaneAt(index);
            if (pane) {
                pane._browserDraggedTabId = '';
            }
            browserClearTabDragMarkers(index);
        });

        strip.addEventListener('dragover', event => {
            const draggedId = browserPaneAt(index)?._browserDraggedTabId || '';
            const tab = event.target.closest?.(`[data-browser-tab="${index}"]`);
            if (!draggedId || !tab || tab.dataset.browserTabId === draggedId) {
                return;
            }
            event.preventDefault();
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = 'move';
            }
            const rect = tab.getBoundingClientRect();
            const before = event.clientX < rect.left + rect.width / 2;
            tab.classList.toggle('drag-before', before);
            tab.classList.toggle('drag-after', !before);
        });

        strip.addEventListener('dragleave', event => {
            const tab = event.target.closest?.(`[data-browser-tab="${index}"]`);
            if (tab && !tab.contains(event.relatedTarget)) {
                tab.classList.remove('drag-before', 'drag-after');
            }
        });

        strip.addEventListener('drop', event => {
            const pane = browserPaneAt(index);
            const draggedId = pane?._browserDraggedTabId || '';
            const tab = event.target.closest?.(`[data-browser-tab="${index}"]`);
            if (!draggedId || !tab || tab.dataset.browserTabId === draggedId) {
                return;
            }
            event.preventDefault();
            /* Cleared here, not in `dragend`: the drop re-renders the strip, so
               the drag source (and this delegating listener) is detached before
               `dragend` would fire, which would otherwise leave the pane stuck
               believing a drag is still in progress. */
            pane._browserDraggedTabId = '';
            const rect = tab.getBoundingClientRect();
            const before = event.clientX < rect.left + rect.width / 2;
            browserReorderTab(index, draggedId, tab.dataset.browserTabId || '', before);
        });
    }

    function wireBrowserOnlyControls(card, index) {
        const browserOpenButton = card.querySelector(`[data-browser-open="${index}"]`);
        if (browserOpenButton && !browserOpenButton.dataset.bound) {
            browserOpenButton.dataset.bound = 'true';
            stopHeaderButtonDrag(browserOpenButton);
            browserOpenButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                openBrowserPaneExternally(index);
            });
        }

        const browserUrlInput = card.querySelector(`[data-browser-url="${index}"]`);
        if (browserUrlInput && !browserUrlInput.dataset.bound) {
            browserUrlInput.dataset.bound = 'true';
            browserUrlInput.addEventListener('keydown', event => {
                if (event.key !== 'Enter') {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                navigateBrowserPane(index, browserUrlInput.value);
            });
            browserUrlInput.addEventListener('blur', () => {
                navigateBrowserPane(index, browserUrlInput.value);
            });
        }

        browserWireTabStrip(index);
        card.querySelectorAll(`[data-browser-frame="${index}"]`).forEach(frame => {
            browserWireFrame(index, frame);
        });
    }

    /* Carried through a close-driven grid rebuild alongside the explorer
       snapshot, so a sibling pane closing does not reset the strip. */
    function browserCaptureCloseSnapshot(pane) {
        const snapshot = browserSerializeTabs(pane);
        return {
            type: 'browser',
            browser_url: snapshot.tabs[snapshot.active_tab],
            browser_tabs: snapshot.tabs,
            browser_active_tab: snapshot.active_tab
        };
    }
