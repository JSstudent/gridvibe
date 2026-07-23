    /* ── Theme management ── */
    const SURFACE_MODE_STORAGE_KEY = 'gridvibe.terminalSurfaceMode';
    const TOPBAR_VISIBILITY_STORAGE_KEY = 'gridvibe.terminalTopbarVisibility';
    const DEFAULT_SAVED_SESSION_ID = 'default-session';






    /* Theme helpers live in shared.js; this hook adds the terminals-specific
       behaviour (topbar toggle label + default-explorer theme sync). Cycling
       the theme here now persists it via /api/app-config like the launcher. */
    function onThemeApplied(preference) {
        const btn = document.getElementById('themeToggleBtn');
        if (btn) {
            btn.innerHTML = themeToggleButtonHtml(preference);
        }
        syncDefaultExplorerThemes();
    }

    function currentResolvedTheme() {
        return document.documentElement.getAttribute('data-theme') || resolveTheme(getStoredTheme() || 'system');
    }

    initTheme();

    function normalizeSurfaceMode(mode) {
        return mode === 'max' ? 'max' : 'normal';
    }

    function getStoredSurfaceMode() {
        try {
            return localStorage.getItem(SURFACE_MODE_STORAGE_KEY) === 'max';
        } catch (_) {
            return false;
        }
    }

    function updateSurfaceModeButton(enabled) {
        const button = document.getElementById('surfaceModeBtn');
        if (!button) {
            return;
        }
        const label = enabled ? 'Normal surface' : 'Max surface';
        button.classList.toggle('active', enabled);
        button.title = label;
        button.setAttribute('aria-label', label);
        button.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    }

    async function refitAttachedTerminalsForSurfaceMode() {
        const attachedIndices = terminals
            .map((terminal, index) => terminal?._attached ? index : -1)
            .filter(index => index !== -1);
        await redrawAttachedTerminals(attachedIndices, { forceResize: true });
    }

    function applySurfaceMode(enabled, { persist = false, refit = false } = {}) {
        const active = Boolean(enabled);
        document.body.classList.toggle('surface-max', active);
        updateSurfaceModeButton(active);
        if (persist) {
            try {
                localStorage.setItem(SURFACE_MODE_STORAGE_KEY, active ? 'max' : 'normal');
            } catch (_) {}
        }
        if (refit) {
            refitAttachedTerminalsForSurfaceMode();
        }
    }

    function toggleSurfaceMode() {
        surfaceModeChangedManually = true;
        applySurfaceMode(!document.body.classList.contains('surface-max'), {
            persist: true,
            refit: true
        });
    }

    function applyConfiguredSurfaceMode(data, { refit = false } = {}) {
        if (surfaceModeChangedManually) {
            return;
        }

        const groupId = data?.group?.group_id || activeGroupId || '';
        const mode = normalizeSurfaceMode(data?.surface_mode || DEFAULT_SURFACE_MODE);
        const applyKey = `${data?.group?.created_at || ''}:${mode}`;
        if (groupId && surfaceModeAppliedGroups.get(groupId) === applyKey) {
            return;
        }

        applySurfaceMode(mode === 'max', { refit });
        if (groupId) {
            surfaceModeAppliedGroups.set(groupId, applyKey);
        }
    }

    function applyAppConfigSurfaceMode(message) {
        if (!message || typeof message !== 'object' || !message.workspace) {
            return;
        }

        const mode = normalizeSurfaceMode(message.workspace.surface_mode);
        surfaceModeChangedManually = true;
        surfaceModeAppliedGroups.clear();
        applySurfaceMode(mode === 'max', { persist: true, refit: true });
    }

    function applyAppConfigTheme(message) {
        const theme = message?.appearance?.theme;
        if (!['system', 'light', 'dark'].includes(theme)) {
            return;
        }
        /* Idempotent: skip when the preference is already active so duplicate
           deliveries (BroadcastChannel + storage + socket) are harmless. */
        const current = document.documentElement.getAttribute('data-theme-preference');
        if (theme !== current) {
            applyTheme(theme);
        }
    }

    /* One normalized app-config update contract for every delivery path
       (BroadcastChannel, storage event, Socket.IO): apply the global theme
       preference and the workspace surface mode (ISSUE-2026-021). */
    function applyAppConfigUpdate(message) {
        if (!message || typeof message !== 'object') {
            return;
        }
        applyAppConfigTheme(message);
        applyAppConfigSurfaceMode(message);
        applyAppConfigTerminalFont(message);
    }

    /* Per-session font overrides (OD-14): keyed by session-group id — the
       "session" the user launches and switches between as one tab — so the
       active workspace keeps its own font/size across tab switches and pane
       rebuilds. In-memory only — like live sessions themselves, overrides end
       with the process. The single-workspace page uses the '' group key. */
    const groupFontOverrides = new Map();

    function activeFontOverrideGroupKey() {
        return activeGroupId || visibleGroupId || '';
    }

    function applyGroupFontOverride(index) {
        const term = terminals[index]?.term;
        const override = groupFontOverrides.get(activeFontOverrideGroupKey());
        if (!term || !override) {
            return;
        }
        if (override.fontSize) {
            term.options.fontSize = override.fontSize;
        }
        if (override.fontFamily) {
            term.options.fontFamily = override.fontFamily;
        }
    }

    function styleTerminalFont(terminal, fontSize, fontFamily) {
        const term = terminal?.term;
        if (!term) {
            return;
        }
        if (fontSize) {
            term.options.fontSize = fontSize;
        }
        if (fontFamily) {
            term.options.fontFamily = fontFamily;
        }
    }

    /* Apply a launcher-side terminal font change (ISSUE-2026-029) without a
       reload. Sizes outside the server's accepted range are ignored so a
       malformed broadcast can't break rendering. Scope (OD-14): by default
       every terminal pane of the ACTIVE session (group) restyles and the
       change is recorded as that session's override; `apply_scope: 'all'`
       ("Apply to all active sessions") pushes font + size to every session —
       including the cached, currently hidden groups — and drops the
       overrides so all sessions follow the saved global value again. */
    function applyAppConfigTerminalFont(message) {
        const terminalConfig = message?.terminal;
        if (!terminalConfig || typeof terminalConfig !== 'object') {
            return;
        }
        const rawFontSize = Number(terminalConfig.font_size);
        const fontSize = Number.isFinite(rawFontSize) && rawFontSize >= 6 && rawFontSize <= 48
            ? Math.round(rawFontSize)
            : 0;
        const fontFamily = typeof terminalConfig.font_family === 'string'
            ? terminalConfig.font_family.trim()
            : '';
        if (!fontSize && !fontFamily) {
            return;
        }
        const applyToAll = terminalConfig.apply_scope === 'all';
        if (applyToAll) {
            /* The dataset is what future panes read at creation, so the global
               default only advances on the all-sessions path — a session-scoped
               change must not leak into other groups via a later rebuild. */
            if (fontSize) {
                document.body.dataset.terminalFontSize = String(fontSize);
            }
            if (fontFamily) {
                document.body.dataset.terminalFontFamily = fontFamily;
            }
            groupFontOverrides.clear();
            /* Hidden sessions keep live xterm instances in the group cache;
               restyle them too so switching tabs shows the new font. They
               refit on restore (the switch path resets _fitReady). */
            cachedGroupViews.forEach(cached => {
                (cached.terminals || []).forEach(terminal => styleTerminalFont(terminal, fontSize, fontFamily));
            });
        } else {
            const override = groupFontOverrides.get(activeFontOverrideGroupKey()) || {};
            if (fontSize) {
                override.fontSize = fontSize;
            }
            if (fontFamily) {
                override.fontFamily = fontFamily;
            }
            groupFontOverrides.set(activeFontOverrideGroupKey(), override);
        }
        terminals.forEach((terminal, index) => {
            if (!terminal?.term) {
                return;
            }
            styleTerminalFont(terminal, fontSize, fontFamily);
            scheduleFit(index);
        });
    }

    /* Recover app-config changes missed while this window was hidden or its
       socket was disconnected. */
    async function reconcileAppConfigTheme() {
        try {
            const response = await fetch('/api/app-config');
            if (!response.ok) {
                return;
            }
            applyAppConfigTheme(await response.json());
        } catch (_error) {}
    }

    function setupAppConfigUpdateListeners() {
        if ('BroadcastChannel' in window) {
            try {
                const channel = new BroadcastChannel(APP_CONFIG_BROADCAST_CHANNEL);
                channel.onmessage = event => {
                    applyAppConfigUpdate(event.data || {});
                };
            } catch (_error) {}
        }

        window.addEventListener('storage', event => {
            if (event.key !== APP_CONFIG_UPDATE_STORAGE_KEY || !event.newValue) {
                return;
            }

            try {
                applyAppConfigUpdate(JSON.parse(event.newValue));
            } catch (_error) {}
        });
    }

    function getStoredTopbarVisible() {
        try {
            return localStorage.getItem(TOPBAR_VISIBILITY_STORAGE_KEY) !== 'hidden';
        } catch (_) {
            return true;
        }
    }

    function updateTopbarToggleButton(visible) {
        const button = document.getElementById('topbarToggleBtn');
        const path = document.getElementById('topbarTogglePath');
        if (!button) {
            return;
        }
        const label = visible ? 'Hide top bar' : 'Show top bar';
        button.title = label;
        button.setAttribute('aria-label', label);
        button.setAttribute('aria-expanded', visible ? 'true' : 'false');
        if (path) {
            path.setAttribute('d', visible ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6');
        }
    }

    function applyTopbarVisibility(visible, { persist = false, refit = false } = {}) {
        const shouldShow = Boolean(visible);
        document.body.classList.toggle('topbar-collapsed', !shouldShow);
        updateTopbarToggleButton(shouldShow);
        if (persist) {
            try {
                localStorage.setItem(
                    TOPBAR_VISIBILITY_STORAGE_KEY,
                    shouldShow ? 'visible' : 'hidden'
                );
            } catch (_) {}
        }
        if (refit) {
            refitAttachedTerminalsForSurfaceMode();
        }
    }

    function toggleTopbarVisibility() {
        applyTopbarVisibility(document.body.classList.contains('topbar-collapsed'), {
            persist: true,
            refit: true
        });
    }

    const EXPLORER_THEME_STORAGE_KEY = 'gridvibe.explorerTheme';

    function normalizeExplorerTheme(theme) {
        return theme === 'dark' ? 'dark' : 'light';
    }

    function getExplorerThemeStore() {
        try {
            const raw = localStorage.getItem(EXPLORER_THEME_STORAGE_KEY);
            if (!raw) {
                return {};
            }
            if (raw === 'light' || raw === 'dark') {
                return {};
            }
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function hasExplorerThemeOverride(key = '') {
        const store = getExplorerThemeStore();
        return Boolean(key && Object.prototype.hasOwnProperty.call(store, key));
    }

    function getExplorerTheme(key = '') {
        const store = getExplorerThemeStore();
        return normalizeExplorerTheme(
            key && Object.prototype.hasOwnProperty.call(store, key)
                ? store[key]
                : 'dark'
        );
    }

    function saveExplorerTheme(key, theme) {
        if (!key) {
            return;
        }
        const store = getExplorerThemeStore();
        store[key] = normalizeExplorerTheme(theme);
        try {
            localStorage.setItem(EXPLORER_THEME_STORAGE_KEY, JSON.stringify(store));
        } catch (_) {
        }
    }

    /* An explicit per-pane explorer theme carried in a saved/restored session
       config ('light' or 'dark'), or '' when none was persisted. */
    function explorerThemeFromSession(session) {
        const value = session?.explorer_theme;
        return value === 'light' || value === 'dark' ? value : '';
    }

    /* Resolve a pane's initial explorer theme + source. A per-session override
       the user toggled this run wins; otherwise any theme baked into the saved
       session config — light OR dark — is an explicit choice and is treated as
       an override so the pane keeps it independent of the global app theme;
       only a pane with no persisted theme at all falls back to the 'dark'
       default. The explorer theme is deliberately independent of the global
       light/dark theme, so the resolved value is always applied explicitly (a
       pane with no data-explorer-theme would otherwise inherit the global
       theme's --explorer-* tokens). */
    function resolveInitialExplorerTheme(session, key) {
        if (hasExplorerThemeOverride(key)) {
            return { theme: getExplorerTheme(key), source: 'override' };
        }
        const savedTheme = explorerThemeFromSession(session);
        if (savedTheme) {
            return { theme: savedTheme, source: 'override' };
        }
        return { theme: 'dark', source: 'default' };
    }

    function explorerThemeLabel(theme) {
        return normalizeExplorerTheme(theme) === 'dark' ? 'Light explorer theme' : 'Dark explorer theme';
    }

    function updateExplorerThemeButton(button, theme) {
        if (!button) {
            return;
        }
        const normalizedTheme = normalizeExplorerTheme(theme);
        button.innerHTML = normalizedTheme === 'dark' ? THEME_SUN_ICON : THEME_MOON_ICON;
        button.title = explorerThemeLabel(normalizedTheme);
        button.setAttribute('aria-label', explorerThemeLabel(normalizedTheme));
        button.setAttribute('aria-pressed', normalizedTheme === 'dark' ? 'true' : 'false');
    }

    function applyExplorerThemeToCard(card, theme) {
        if (!card) {
            return;
        }
        const normalizedTheme = normalizeExplorerTheme(theme);
        card.dataset.explorerTheme = normalizedTheme;
        const button = card.querySelector('[data-explorer-theme-toggle]');
        updateExplorerThemeButton(button, normalizedTheme);
    }

    function syncDefaultExplorerThemes() {
        document.querySelectorAll('.explorer-pane').forEach(card => {
            if (card.dataset.explorerThemeSource === 'override') {
                return;
            }
            applyExplorerThemeToCard(card, 'dark');
            card.dataset.explorerThemeSource = 'default';
        });
    }

    function toggleExplorerTheme(index) {
        const card = document.getElementById(`tc-${index}`);
        if (!card) {
            return;
        }
        const currentTheme = normalizeExplorerTheme(card.dataset.explorerTheme || 'dark');
        const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
        applyExplorerThemeToCard(card, nextTheme);
        card.dataset.explorerThemeSource = 'override';
        saveExplorerTheme(card.dataset.explorerThemeKey || '', nextTheme);
    }

    /* ─────────────────────────────────────────────
       State  (socket is initialised AFTER functions
       so that a CDN failure can't kill the whole script)
    ───────────────────────────────────────────── */
    let terminals  = [];
    let sessionIds = [];
    let gridBuilt  = false;
    let _focusedTerminalIndex = -1;
    let socket     = null;   // set at the bottom after all defs
    let resizeObservers = [];
    let cachedGroupViews = new Map();
    let sessionRouteMap = new Map();
    let visibleGroupId = '';
    let nativeFullscreen = false;
    let draggedCard = null;
    let draggedSessionTab = null;
    let draggedSessionTabOriginOrder = [];
    let sessionTabDropHandled = false;
    let suppressSessionTabClickUntil = 0;
    let activeGroupId = new URLSearchParams(window.location.search).get('group') || '';
    let sessionGroups = [];
    let activeLoadToken = 0;
    let knownGroupIds = [];
    let workspaceSaveTargets = new Map();
    let surfaceModeChangedManually = false;
    let surfaceModeAppliedGroups = new Map();
    let pendingSplitRestore = null;
    /* Live explorer/browser client state captured before a terminal close so the
       forced grid rebuild does not wipe sibling panes (ISSUE-2026-027). */
    let pendingCloseClientState = null;
    let pendingModeSwitchSessionIds = new Set();
    let savedSessionResolver = null;
    let saveSessionAsResolver = null;
    let closeSessionConfirmResolver = null;
    let genericConfirmResolver = null;
    const MAX_SPLIT_TERMINALS = Math.min(8, Number(MAX_SESSIONS || 8));
    const MIN_SPLIT_COLS = 8;
    const MIN_SPLIT_ROWS = 8;
    const MIN_RESIZE_SURFACE_RATIO = 1 / 8;
    let splitSlotRects = null;
    let splitColumnWeights = null;
    let splitRowWeights = null;
    let activeGridResize = null;
    let originalSplitSlotCount = 0;

    function isRecursiveSplitLayout() {
        return originalSplitSlotCount > 0 && originalSplitSlotCount <= 2;
    }

    function getSplitPaneLimit() {
        return isRecursiveSplitLayout()
            ? MAX_SPLIT_TERMINALS
            : Math.min(MAX_SPLIT_TERMINALS, Math.max(1, originalSplitSlotCount) * 2);
    }

    function isExplorerSession(session) {
        return ['ssh', 'wsl'].includes(session?.mode) && session?.startup_mode === 'explorer';
    }

    function isExplorerPaneInstance(terminal) {
        return terminal?._paneType === 'explorer';
    }

    function isBrowserSession(session) {
        return session?.mode === 'wsl' && session?.startup_mode === 'browser';
    }

    function isBrowserPaneInstance(terminal) {
        return terminal?._paneType === 'browser';
    }

    function getBrowserSessionUrl(session) {
        const rawUrl = String(session?.initial_command || '').trim();
        if (!rawUrl) {
            return 'http://127.0.0.1:3000';
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

    function getSessionApiPath(groupId = activeGroupId) {
        return groupId
            ? `/api/sessions?group=${encodeURIComponent(groupId)}`
            : '/api/sessions';
    }

    function getGroupById(groupId) {
        return sessionGroups.find(group => group.group_id === groupId) || null;
    }

    function getActiveWorkspaceGroupId() {
        const activeTabGroupId = document.querySelector('.session-tab.active[data-group-id]')?.dataset.groupId || '';
        return activeTabGroupId || activeGroupId || '';
    }

    function setSessionRoute(sessionId, groupId, index) {
        if (!sessionId || !groupId || !Number.isInteger(index)) {
            return;
        }
        sessionRouteMap.set(sessionId, { groupId, index });
    }

    function clearSessionRoutes(ids) {
        (ids || []).forEach(sessionId => {
            if (sessionId) {
                sessionRouteMap.delete(sessionId);
            }
        });
    }

    function resolveSessionTarget(sessionId) {
        if (!sessionId) {
            return null;
        }

        const route = sessionRouteMap.get(sessionId);
        if (!route) {
            return null;
        }

        if (route.groupId === visibleGroupId) {
            const terminal = terminals[route.index];
            if (!terminal) {
                return null;
            }
            return {
                groupId: route.groupId,
                index: route.index,
                terminal,
                active: true
            };
        }

        const cached = cachedGroupViews.get(route.groupId);
        const terminal = cached?.terminals?.[route.index];
        if (!terminal) {
            return null;
        }

        return {
            groupId: route.groupId,
            index: route.index,
            terminal,
            active: false
        };
    }

    function clearFitTimers(targetTerminals = terminals) {
        (targetTerminals || []).forEach(terminal => {
            if (!terminal?._fitTimer) {
                return;
            }
            clearTimeout(terminal._fitTimer);
            terminal._fitTimer = null;
        });
    }

    function disconnectObservers(targetObservers = resizeObservers) {
        (targetObservers || []).forEach(observer => observer.disconnect());
    }

    function disconnectTerminalObserver(index) {
        const terminal = terminals[index];
        const observer = terminal?._resizeObserver;
        if (!observer) {
            return;
        }

        observer.disconnect();
        resizeObservers = resizeObservers.filter(item => item !== observer);
        terminal._resizeObserver = null;
        terminal._resizeObserved = false;
    }

    function observeTerminalResize(index) {
        const terminal = terminals[index];
        if (!terminal || terminal._resizeObserved || !('ResizeObserver' in window)) {
            return;
        }

        const wrapper = document.getElementById(`tw-${index}`);
        if (!wrapper) {
            return;
        }

        const observer = new ResizeObserver(() => {
            scheduleFit(index);
            updateSplitButtonState(index);
            renderResizeHandles();
        });
        observer.observe(wrapper);
        resizeObservers.push(observer);
        terminal._resizeObserved = true;
        terminal._resizeObserver = observer;
    }

    function restoreActiveTerminalObservers() {
        resizeObservers = [];
        terminals.forEach((terminal, index) => {
            if (!terminal) {
                return;
            }
            terminal._resizeObserved = false;
            if (terminal._attached) {
                observeTerminalResize(index);
            }
        });
    }

    // xterm renders its scrollbar on an internal .xterm-viewport element. Detaching
    // the grid fragment on a session-tab switch resets that element's scrollTop to 0,
    // and because the pane size is unchanged, neither fit()/resize() nor scrollToBottom()
    // re-syncs it (xterm short-circuits when ydisp is unchanged). So we must capture and
    // restore the viewport element's scrollTop directly, the way the explorer panes do.
    function terminalViewportElement(terminal) {
        return terminal?.term?.element?.querySelector('.xterm-viewport') || null;
    }

    function captureTerminalViewportState(terminal) {
        const buffer = terminal?.term?.buffer?.active;
        if (!buffer) {
            return null;
        }

        const viewportY = Number(buffer.viewportY || 0);
        const baseY = Number(buffer.baseY || 0);
        const viewportEl = terminalViewportElement(terminal);
        const maxScrollTop = viewportEl ? Math.max(0, viewportEl.scrollHeight - viewportEl.clientHeight) : 0;
        const scrollTop = viewportEl ? viewportEl.scrollTop : 0;
        return {
            viewportY,
            baseY,
            wasAtBottom: viewportY >= baseY,
            scrollTop,
            scrollTopRatio: maxScrollTop > 0 ? scrollTop / maxScrollTop : 0
        };
    }

    // How long (ms) after a session-tab switch we keep re-asserting a terminal's
    // saved scroll position. Fits triggered by ResizeObserver, redraw passes and
    // PTY resize echoes land asynchronously well after the initial restore, so a
    // single scroll call loses the race — every fit within this window re-applies
    // the target. After it elapses we release control so normal scrolling works.
    const TERMINAL_VIEWPORT_RESTORE_SETTLE_MS = 400;

    function restoreTerminalViewportState(terminal, state, { isCurrent = null } = {}) {
        if (!terminal?.term || !state) {
            return;
        }

        const stillCurrent = typeof isCurrent === 'function' ? isCurrent : () => true;
        // Stash the target so fitTerminal() re-asserts it after each reflow until
        // the layout settles (see applyTerminalViewportRestore / fitTerminal).
        terminal._viewportRestore = {
            state,
            stillCurrent,
            until: Date.now() + TERMINAL_VIEWPORT_RESTORE_SETTLE_MS
        };
        const applyScroll = () => applyTerminalViewportRestore(terminal);

        applyScroll();
        requestAnimationFrame(() => {
            applyScroll();
            requestAnimationFrame(applyScroll);
        });
        window.setTimeout(applyScroll, 80);
        window.setTimeout(applyScroll, 200);
        window.setTimeout(applyScroll, TERMINAL_VIEWPORT_RESTORE_SETTLE_MS);
    }

    function applyTerminalViewportRestore(terminal) {
        const pending = terminal?._viewportRestore;
        if (!pending || !terminal.term) {
            return;
        }
        if (!pending.stillCurrent() || Date.now() > pending.until) {
            terminal._viewportRestore = null;
            return;
        }
        const buffer = terminal.term?.buffer?.active;
        if (!buffer) {
            return;
        }
        const state = pending.state;
        // Drive xterm's internal ydisp so its model matches...
        if (state.wasAtBottom && typeof terminal.term.scrollToBottom === 'function') {
            terminal.term.scrollToBottom();
        } else if (typeof terminal.term.scrollToLine === 'function') {
            terminal.term.scrollToLine(Math.min(Number(state.viewportY || 0), Number(buffer.baseY || 0)));
        }
        // ...but the API no-ops when ydisp is unchanged, so also force the actual
        // scrollbar element back into position (the detach/reattach reset it to 0).
        const viewportEl = terminalViewportElement(terminal);
        if (viewportEl) {
            const maxScrollTop = Math.max(0, viewportEl.scrollHeight - viewportEl.clientHeight);
            viewportEl.scrollTop = state.wasAtBottom
                ? maxScrollTop
                : Math.min(
                    maxScrollTop,
                    maxScrollTop > 0
                        ? Math.round(maxScrollTop * (state.scrollTopRatio || 0))
                        : (state.scrollTop || 0)
                );
        }
    }

    function captureCachedPaneUiState() {
        terminals.forEach((terminal, index) => {
            if (!terminal) {
                return;
            }
            terminal._cachedTerminalViewport = captureTerminalViewportState(terminal);
            if (isExplorerPaneInstance(terminal)) {
                /* The detached cache has no document-level DOM lookup. Fold the
                   final tab view into the pane and retain its live theme while
                   the card is still mounted so Save All can serialize it. */
                explorerCaptureActiveTabView(index);
                const card = document.getElementById(`tc-${index}`);
                terminal._cachedExplorerTheme = normalizeExplorerTheme(
                    card?.dataset.explorerTheme
                    || terminal._cachedExplorerTheme
                    || terminal._session?.explorer_theme
                    || 'dark'
                );
                terminal._cachedExplorerScroll = captureExplorerFileScroll(index);
            } else {
                terminal._cachedExplorerScroll = null;
            }
        });
    }

    function restoreCachedPaneUiState({ restoreTerminalViewports = true, clearTerminalViewports = true } = {}) {
        terminals.forEach((terminal, index) => {
            if (!terminal) {
                return;
            }
            if (restoreTerminalViewports) {
                restoreTerminalViewportState(terminal, terminal._cachedTerminalViewport);
            }
            if (isExplorerPaneInstance(terminal)) {
                restoreExplorerFileScroll(index, terminal._cachedExplorerScroll);
            }
            if (clearTerminalViewports) {
                terminal._cachedTerminalViewport = null;
            }
            terminal._cachedExplorerScroll = null;
        });
    }

    function updateSessionChrome(count, groupId = activeGroupId) {
        const activeGroup = getGroupById(groupId);
        const labelParts = [
            activeGroup?.name || 'Session',
            `${count} terminal${count !== 1 ? 's' : ''}`,
            activeGroup?.connection_mode === 'wsl' ? 'Local Repo' : 'SSH'
        ].filter(Boolean);
        document.getElementById('sessionLabel').textContent = labelParts.join(' • ');
        document.title = activeGroup?.name
            ? `GridVibe — ${activeGroup.name}`
            : 'GridVibe — Terminals';
    }

    function cacheVisibleGroupView(groupId = visibleGroupId) {
        if (!groupId || !gridBuilt) {
            return;
        }

        _stopAllVoice();
        const grid = document.getElementById('terminalsGrid');
        const fragment = document.createDocumentFragment();

        clearActiveGridResize();
        clearResizeHandles();
        captureCachedPaneUiState();
        clearFitTimers(terminals);
        disconnectObservers(resizeObservers);
        terminals.forEach(terminal => {
            if (!terminal) {
                return;
            }
            terminal._resizeObserved = false;
            terminal._fitReady = false;
        });

        while (grid.firstChild) {
            fragment.appendChild(grid.firstChild);
        }

        const hasLocalSplitLayout = grid.className === 'layout-split-local';
        cachedGroupViews.set(groupId, {
            groupId,
            terminals,
            sessionIds,
            fragment,
            className: grid.className,
            gridColumns: grid.style.getPropertyValue('--grid-columns'),
            gridRows: grid.style.getPropertyValue('--grid-rows'),
            splitGridColumns: grid.style.getPropertyValue('--split-grid-columns'),
            splitGridRows: grid.style.getPropertyValue('--split-grid-rows'),
            splitSlotRects: hasLocalSplitLayout ? cloneSplitSlotRects() : null,
            splitColumnWeights: hasLocalSplitLayout ? cloneSplitTrackWeights(splitColumnWeights) : null,
            splitRowWeights: hasLocalSplitLayout ? cloneSplitTrackWeights(splitRowWeights) : null,
            originalSplitSlotCount
        });

        terminals = [];
        sessionIds = [];
        resizeObservers = [];
        gridBuilt = false;
        visibleGroupId = '';
        grid.className = '';
        grid.style.removeProperty('--grid-columns');
        grid.style.removeProperty('--grid-rows');
        grid.style.removeProperty('--split-grid-columns');
        grid.style.removeProperty('--split-grid-rows');
        grid.style.gridTemplateColumns = '';
        grid.style.gridTemplateRows = '';
        splitSlotRects = null;
        splitColumnWeights = null;
        splitRowWeights = null;
        originalSplitSlotCount = 0;
    }

    function restoreCachedGroupView(groupId) {
        const cached = cachedGroupViews.get(groupId);
        if (!cached) {
            return false;
        }

        const grid = document.getElementById('terminalsGrid');
        grid.innerHTML = '';
        grid.className = cached.className || '';
        if (cached.gridColumns) {
            grid.style.setProperty('--grid-columns', cached.gridColumns);
        } else {
            grid.style.removeProperty('--grid-columns');
        }
        if (cached.gridRows) {
            grid.style.setProperty('--grid-rows', cached.gridRows);
        } else {
            grid.style.removeProperty('--grid-rows');
        }
        if (cached.splitGridColumns) {
            grid.style.setProperty('--split-grid-columns', cached.splitGridColumns);
        } else {
            grid.style.removeProperty('--split-grid-columns');
        }
        if (cached.splitGridRows) {
            grid.style.setProperty('--split-grid-rows', cached.splitGridRows);
        } else {
            grid.style.removeProperty('--split-grid-rows');
        }
        grid.appendChild(cached.fragment);
        grid.style.display = '';

        terminals = cached.terminals || [];
        sessionIds = cached.sessionIds || [];
        splitSlotRects = cached.className === 'layout-split-local'
            ? cloneSplitSlotRects(cached.splitSlotRects)
            : null;
        splitColumnWeights = cached.className === 'layout-split-local'
            ? cloneSplitTrackWeights(cached.splitColumnWeights)
            : null;
        splitRowWeights = cached.className === 'layout-split-local'
            ? cloneSplitTrackWeights(cached.splitRowWeights)
            : null;
        originalSplitSlotCount = Number(cached.originalSplitSlotCount || terminals.length || 0);
        visibleGroupId = groupId;
        gridBuilt = terminals.length > 0;
        terminals.forEach(terminal => {
            if (terminal) {
                terminal._fitReady = false;
                terminal._resizeObserved = false;
            }
        });
        if (splitSlotRects) {
            applySplitSlotGeometry({ fit: false });
        } else {
            renderResizeHandles();
        }
        restoreCachedPaneUiState({
            restoreTerminalViewports: false,
            clearTerminalViewports: false
        });
        restoreActiveTerminalObservers();
        document.getElementById('emptyState').classList.remove('visible');
        return true;
    }

    function dropCachedGroupView(groupId) {
        const cached = cachedGroupViews.get(groupId);
        if (!cached) {
            return;
        }

        clearFitTimers(cached.terminals || []);
        disconnectObservers(cached.resizeObservers || []);
        if (socket) {
            (cached.sessionIds || []).forEach(sessionId => {
                if (sessionId) {
                    socket.emit('leave_session', { session_id: sessionId });
                }
            });
        }
        clearSessionRoutes(cached.sessionIds || []);
        (cached.terminals || []).forEach(terminal => {
            if (terminal?.term) {
                try { terminal.term.dispose(); } catch (_) {}
            }
        });
        cachedGroupViews.delete(groupId);
    }

    function syncLocationToGroup(groupId) {
        const url = new URL(window.location.href);
        if (groupId) {
            url.searchParams.set('group', groupId);
        } else {
            url.searchParams.delete('group');
        }
        window.history.replaceState({}, '', url);
    }

    const TAB_COLOUR_PALETTE = [
        '#ff6b6b', '#ff922b', '#ffd43b', '#69db7c',
        '#38d9a9', '#4dabf7', '#748ffc', '#da77f2',
        '#f783ac', '#a9e34b',
    ];

    function tabColourForGroup(groupId) {
        let hash = 0;
        for (let i = 0; i < groupId.length; i++) {
            hash = (hash * 31 + groupId.charCodeAt(i)) & 0xffffffff;
        }
        return TAB_COLOUR_PALETTE[Math.abs(hash) % TAB_COLOUR_PALETTE.length];
    }

    function hexToRgba(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    function applyTabColour(button, groupId) {
        const colour = tabColourForGroup(groupId);
        button.style.setProperty('--tab-color', colour);
        button.style.setProperty('--tab-color-bg', hexToRgba(colour, 0.14));
        button.style.setProperty('--tab-color-shadow', hexToRgba(colour, 0.45));
        button.style.borderColor = hexToRgba(colour, 0.4);
    }

    function savedSessionIdFromGroupId(groupId) {
        const prefix = 'saved-session-';
        const value = String(groupId || '');
        return value.startsWith(prefix) ? value.slice(prefix.length) : '';
    }

    function getWorkspaceSaveTarget(groupId = activeGroupId) {
        const group = getGroupById(groupId);
        const groupSavedSessionId = String(group?.saved_session_id || '').trim();
        if (groupSavedSessionId) {
            return {
                id: groupSavedSessionId,
                name: group?.name || ''
            };
        }

        const override = workspaceSaveTargets.get(groupId);
        if (override?.id) {
            return override;
        }

        const savedSessionId = savedSessionIdFromGroupId(groupId);
        return {
            id: savedSessionId,
            name: group?.name || ''
        };
    }

    function rememberWorkspaceSaveTarget(groupId, savedSessionId, sessionName) {
        const targetId = String(savedSessionId || '').trim();
        const targetName = String(sessionName || '').trim();
        if (!groupId || !targetId) {
            return;
        }

        workspaceSaveTargets.set(groupId, {
            id: targetId,
            name: targetName
        });

        const group = getGroupById(groupId);
        if (group) {
            group.saved_session_id = targetId;
            if (targetName) {
                group.name = targetName;
            }
            renderSessionTabs();
            updateSessionChrome(getWorkspacePanesInVisualOrder(groupId).length, groupId);
        }
    }

    function notifySavedSessionUpdated(savedSession, options = {}) {
        const sessionId = String(savedSession?.id || '').trim();
        if (!sessionId) {
            return;
        }

        const payload = {
            id: sessionId,
            name: String(savedSession?.name || '').trim(),
            updated_at: String(savedSession?.updated_at || '').trim(),
            activate: Boolean(options.activate),
            timestamp: Date.now(),
            nonce: Math.random().toString(36).slice(2)
        };

        try {
            const channel = new BroadcastChannel(SAVED_SESSION_BROADCAST_CHANNEL);
            channel.postMessage(payload);
            channel.close();
        } catch (_error) {}

        try {
            localStorage.setItem(SAVED_SESSION_UPDATE_STORAGE_KEY, JSON.stringify(payload));
        } catch (_error) {}
    }

    function closeSessionsMenu() {
        const root = document.getElementById('sessionsMenuRoot');
        const button = document.getElementById('sessionsMenuBtn');
        root?.classList.remove('open');
        button?.setAttribute('aria-expanded', 'false');
    }

    function toggleSessionsMenu(event) {
        event?.preventDefault();
        event?.stopPropagation();
        const root = document.getElementById('sessionsMenuRoot');
        const button = document.getElementById('sessionsMenuBtn');
        if (!root || !button) {
            return;
        }

        const shouldOpen = !root.classList.contains('open');
        root.classList.toggle('open', shouldOpen);
        button.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        if (shouldOpen) {
            closeWorkspaceMenu();
        }
    }

    function closeWorkspaceMenu() {
        const root = document.getElementById('workspaceMenuRoot');
        const button = document.getElementById('workspaceMenuBtn');
        root?.classList.remove('open');
        button?.setAttribute('aria-expanded', 'false');
    }

    function toggleWorkspaceMenu(event) {
        event?.preventDefault();
        event?.stopPropagation();
        const root = document.getElementById('workspaceMenuRoot');
        const button = document.getElementById('workspaceMenuBtn');
        if (!root || !button) {
            return;
        }

        const shouldOpen = !root.classList.contains('open');
        root.classList.toggle('open', shouldOpen);
        button.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
        if (shouldOpen) {
            closeSessionsMenu();
        }
    }


    function getStep2DefaultDirectory(config) {
        return config?.connection_mode === 'wsl'
            ? String(config?.wsl?.default_dir || '').trim()
            : String(config?.ssh?.default_dir || '').trim();
    }




    function buildSavedSessionLaunchName(savedSession, config) {
        const sessionId = String(savedSession?.id || '').trim();
        const sessionName = String(savedSession?.name || '').trim();
        if (sessionId && sessionId !== DEFAULT_SAVED_SESSION_ID && sessionName) {
            return sessionName;
        }

        const sshHost = String(config?.ssh?.host || '').trim();
        if (config?.connection_mode === 'ssh' && sshHost) {
            return sshHost;
        }

        const defaultDir = getStep2DefaultDirectory(config);
        const firstTerminalDir = Array.isArray(config?.terminals)
            ? String(config.terminals.find(terminal => terminal?.directory)?.directory || '').trim()
            : '';
        const directoryName = getDirectoryName(defaultDir || firstTerminalDir);
        if (directoryName) {
            return directoryName;
        }

        return `Session ${new Date().toLocaleTimeString()}`;
    }

    function closeSavedSessionModal(result = null) {
        const modal = document.getElementById('savedSessionsModal');
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');
        document.getElementById('savedSessionsList').innerHTML = '';
        document.getElementById('savedSessionsFooterCopy').textContent = '';

        if (savedSessionResolver) {
            const resolver = savedSessionResolver;
            savedSessionResolver = null;
            resolver(result);
        }
    }

    function openSavedSessionModal(sessions) {
        const modal = document.getElementById('savedSessionsModal');
        const list = document.getElementById('savedSessionsList');
        const footerCopy = document.getElementById('savedSessionsFooterCopy');
        list.innerHTML = sessions.map(session => buildSavedSessionCard(session, {
            currentSavedSessionId: savedSessionIdFromGroupId(activeGroupId)
        })).join('');
        footerCopy.textContent = sessions.length
            ? ''
            : 'No saved sessions found.';
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');

        return new Promise(resolve => {
            savedSessionResolver = resolve;
            list.querySelectorAll('.saved-session-item').forEach(button => {
                button.addEventListener('click', () => closeSavedSessionModal({ id: button.dataset.sessionId }));
            });
        });
    }

    function closeSaveSessionAsModal(result = null) {
        const modal = document.getElementById('saveSessionAsModal');
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');
        document.getElementById('saveSessionAsFooterCopy').textContent = '';

        if (saveSessionAsResolver) {
            const resolver = saveSessionAsResolver;
            saveSessionAsResolver = null;
            resolver(result);
        }
    }

    function openSaveSessionAsModal(suggestedName) {
        const modal = document.getElementById('saveSessionAsModal');
        const nameInput = document.getElementById('saveSessionAsName');
        const openNowInput = document.getElementById('saveSessionAsOpenNow');
        nameInput.value = suggestedName || '';
        openNowInput.checked = false;
        document.getElementById('saveSessionAsFooterCopy').textContent = '';
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');

        window.setTimeout(() => {
            nameInput.focus();
            nameInput.select();
        }, 0);

        return new Promise(resolve => {
            saveSessionAsResolver = resolve;
        });
    }

    function closeCloseSessionConfirmModal(result = false) {
        const modal = document.getElementById('closeSessionConfirmModal');
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');

        if (closeSessionConfirmResolver) {
            const resolver = closeSessionConfirmResolver;
            closeSessionConfirmResolver = null;
            resolver(result);
        }
    }

    function openCloseSessionConfirmModal(group, connectedCount, totalCount) {
        const modal = document.getElementById('closeSessionConfirmModal');
        const copy = document.getElementById('closeSessionConfirmCopy');
        const name = group?.name || group?.group_id || 'this session';
        const terminalNoun = totalCount === 1 ? 'terminal' : 'terminals';
        copy.textContent = totalCount > 0
            ? `Close "${name}" and its ${totalCount} ${terminalNoun} (${connectedCount} connected)?`
            : `Close "${name}"?`;
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');

        window.setTimeout(() => {
            document.getElementById('closeSessionConfirmCancel').focus();
        }, 0);

        return new Promise(resolve => {
            closeSessionConfirmResolver = resolve;
        });
    }

    function closeGenericConfirmModal(result = false) {
        const modal = document.getElementById('genericConfirmModal');
        if (!modal) {
            return;
        }
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');
        if (genericConfirmResolver) {
            const resolver = genericConfirmResolver;
            genericConfirmResolver = null;
            resolver(result);
        }
    }

    /* Reusable in-page confirm shell for irreversible actions (WebView2 blocks
       window.confirm). Resolves true on accept, false on cancel/dismiss. */
    function openGenericConfirmModal({ title = 'Are you sure?', copy = '', note = '', confirmLabel = 'Confirm', danger = false } = {}) {
        const modal = document.getElementById('genericConfirmModal');
        if (!modal) {
            return Promise.resolve(false);
        }
        closeGenericConfirmModal(false);
        document.getElementById('genericConfirmTitle').textContent = title;
        document.getElementById('genericConfirmCopy').textContent = copy;
        const noteEl = document.getElementById('genericConfirmNote');
        noteEl.textContent = note || '';
        noteEl.hidden = !note;
        const acceptButton = document.getElementById('genericConfirmAccept');
        acceptButton.textContent = confirmLabel;
        acceptButton.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');

        window.setTimeout(() => {
            document.getElementById('genericConfirmCancel').focus();
        }, 0);

        return new Promise(resolve => {
            genericConfirmResolver = resolve;
        });
    }

    /* One misclick on a tab's × must not silently kill live terminals
       (sessions are memory-only), so closing a group with ≥1 connected
       terminal asks first. Dead groups close without the dialog. */
    async function confirmCloseSessionGroup(groupId) {
        let sessions = [];
        try {
            const response = await fetch(getSessionApiPath(groupId));
            const data = await response.json();
            if (response.ok && Array.isArray(data.sessions)) {
                sessions = data.sessions;
            }
        } catch (_) {
            /* Status lookup failed — fall through and ask, the safe default. */
        }

        const connectedCount = sessions.filter(session => session.status === 'connected').length;
        if (sessions.length > 0 && connectedCount === 0) {
            return true;
        }

        return openCloseSessionConfirmModal(getGroupById(groupId), connectedCount, sessions.length);
    }

    function buildSavedSessionLaunchPayload(savedSession) {
        const config = savedSession?.config || {};
        const sshConfig = config.ssh || {};
        const wslConfig = config.wsl || {};
        const connectionMode = config.connection_mode === 'wsl' ? 'wsl' : 'ssh';
        const configuredCount = Number(config.terminal_count);
        const terminalCount = Math.max(
            1,
            Math.min(
                MAX_SPLIT_TERMINALS,
                Number.isFinite(configuredCount) ? configuredCount : 1
            )
        );
        const terminalConfigs = Array.isArray(config.terminals) ? config.terminals : [];
        const configuredDefaultDir = getStep2DefaultDirectory(config);
        const launchDefaultDir = configuredDefaultDir || (connectionMode === 'ssh' ? '/' : '');
        const sessions = [];

        if (connectionMode === 'ssh' && !String(sshConfig.host || '').trim()) {
            throw new Error('Saved SSH sessions need a host before they can be launched.');
        }
        if (connectionMode === 'wsl' && !String(wslConfig.default_dir || '').trim()) {
            throw new Error('Saved Local Repo sessions need a repository folder before they can be launched.');
        }

        Array.from({ length: terminalCount }, (_, index) => terminalConfigs[index] || {}).forEach((terminal, index) => {
            const startupMode = terminal?.startup_mode === 'explorer'
                ? 'explorer'
                : (terminal?.initial_command_mode === 'agent' || terminal?.startup_mode === 'agent' ? 'agent' : 'terminal');
            const resolvedDirectory = buildLaunchDirectory(
                configuredDefaultDir,
                terminal?.directory,
                connectionMode
            ) || launchDefaultDir;
            const common = {
                title: terminal?.title || `Terminal ${index + 1}`,
                directory: resolvedDirectory,
                initial_command: startupMode === 'explorer' ? null : (terminal?.initial_command || null),
                initial_command_mode: startupMode === 'explorer'
                    ? 'explorer'
                    : (startupMode === 'agent' ? 'agent' : 'command'),
                agent_selection: startupMode === 'agent' ? (terminal?.agent_selection || '') : '',
                custom_agent: startupMode === 'agent' ? (terminal?.custom_agent || '') : '',
                agent_auto_mode: startupMode === 'agent' && Boolean(terminal?.agent_auto_mode),
                explorer_tree_open: startupMode === 'explorer' ? Boolean(terminal?.explorer_tree_open) : false,
                explorer_git_open: startupMode === 'explorer' ? Boolean(terminal?.explorer_git_open) : false,
                explorer_open_tabs: startupMode === 'explorer' && Array.isArray(terminal?.explorer_open_tabs) ? terminal.explorer_open_tabs : [],
                explorer_active_tab: startupMode === 'explorer' ? (terminal?.explorer_active_tab || '') : '',
                explorer_tab_views: startupMode === 'explorer' && terminal?.explorer_tab_views && typeof terminal.explorer_tab_views === 'object'
                    ? terminal.explorer_tab_views
                    : {},
                explorer_md_preset: startupMode === 'explorer' ? (terminal?.explorer_md_preset || '') : '',
                explorer_md_font: startupMode === 'explorer' ? (terminal?.explorer_md_font || '') : '',
                explorer_theme: startupMode === 'explorer' ? (terminal?.explorer_theme || 'dark') : '',
                startup_mode: startupMode
            };

            if (connectionMode === 'ssh') {
                sessions.push({
                    ...common,
                    host: sshConfig.host,
                    username: sshConfig.username || 'ubuntu',
                    password: sshConfig.password || null,
                    port: sshConfig.port || 22
                });
                return;
            }

            sessions.push({
                ...common,
                distribution: terminal?.distribution || wslConfig.distribution || '',
                username: wslConfig.username || '',
                use_wsl: startupMode === 'explorer' ? false : Boolean(terminal?.use_wsl),
                use_powershell: startupMode === 'explorer' ? false : Boolean(terminal?.use_powershell)
            });
        });

        if (!sessions.length) {
            throw new Error('Selected saved session does not contain any terminals.');
        }

        return {
            connection_mode: connectionMode,
            layout: config.layout || (terminalCount >= 4 ? 'grid' : (terminalCount <= 1 ? 'single' : 'vertical')),
            workspace_layout: config.workspace_layout || null,
            surface_mode: normalizeSurfaceMode(DEFAULT_SURFACE_MODE),
            saved_session_id: savedSession.id,
            session_name: buildSavedSessionLaunchName(savedSession, config),
            sessions
        };
    }

    async function launchSavedSession(savedSession) {
        const payload = buildSavedSessionLaunchPayload(savedSession);
        const response = await fetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.error || `Launch failed with status ${response.status}`);
        }

        const groupId = data.group_id;
        if (groupId) {
            dropCachedGroupView(groupId);
            if (visibleGroupId === groupId) {
                teardownCurrentGrid();
            }
            activeGroupId = groupId;
            syncLocationToGroup(activeGroupId);
            await loadSessionGroups();
            renderSessionTabs();
            await initialLoad();
        }

        const warnings = Array.isArray(data.warnings)
            ? data.warnings.filter(item => String(item || '').trim())
            : [];
        setWorkspaceSaveMessage(
            warnings.length
                ? `Launched "${payload.session_name}". ${warnings.length === 1 ? warnings[0] : `${warnings.length} startup commands were cleared after preflight failed.`}`
                : `Launched "${payload.session_name}".`,
            warnings.length ? 'warning' : 'success'
        );
    }

    async function openNewSessionSelector(event) {
        if (event) {
            event.preventDefault();
        }

        try {
            const listResponse = await fetch('/api/saved-sessions');
            const listData = await listResponse.json().catch(() => ({}));
            if (!listResponse.ok) {
                throw new Error(listData.error || 'Failed to load saved sessions');
            }

            const importableSessions = [
                ...(listData.default_session ? [listData.default_session] : []),
                ...(Array.isArray(listData.sessions) ? listData.sessions : [])
            ];
            const selected = await openSavedSessionModal(importableSessions);
            const selectedId = selected?.id;
            if (!selectedId) {
                return false;
            }

            setWorkspaceSaveMessage('Launching saved session...', '');
            const response = await fetch(`/api/saved-sessions/${encodeURIComponent(selectedId)}`);
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load saved session');
            }
            await launchSavedSession(data);
        } catch (error) {
            console.error('[GridVibe Sessions] saved session launch failed:', error);
            setWorkspaceSaveMessage(`Launch failed: ${error.message}`, 'error');
        }

        return false;
    }

    document.addEventListener('click', event => {
        if (!(event.target instanceof Element) || !event.target.closest('#sessionsMenuRoot')) {
            closeSessionsMenu();
        }
        if (!(event.target instanceof Element) || !event.target.closest('#workspaceMenuRoot')) {
            closeWorkspaceMenu();
        }
    });

    document.getElementById('savedSessionsModal').addEventListener('click', event => {
        if (event.target.id === 'savedSessionsModal') {
            closeSavedSessionModal();
        }
    });

    document.getElementById('saveSessionAsModal').addEventListener('click', event => {
        if (event.target.id === 'saveSessionAsModal') {
            closeSaveSessionAsModal();
        }
    });

    document.getElementById('saveSessionAsForm').addEventListener('submit', event => {
        event.preventDefault();
        const suggestedName = document.getElementById('saveSessionAsName').value.trim();
        if (!suggestedName) {
            document.getElementById('saveSessionAsFooterCopy').textContent = 'Session name is required.';
            document.getElementById('saveSessionAsName').focus();
            return;
        }

        closeSaveSessionAsModal({
            name: suggestedName,
            openNow: document.getElementById('saveSessionAsOpenNow').checked
        });
    });

    document.getElementById('closeSessionConfirmModal').addEventListener('click', event => {
        if (event.target.id === 'closeSessionConfirmModal') {
            closeCloseSessionConfirmModal(false);
        }
    });

    document.getElementById('closeSessionConfirmCancel').addEventListener('click', () => {
        closeCloseSessionConfirmModal(false);
    });

    document.getElementById('closeSessionConfirmAccept').addEventListener('click', () => {
        closeCloseSessionConfirmModal(true);
    });

    document.getElementById('genericConfirmModal').addEventListener('click', event => {
        if (event.target.id === 'genericConfirmModal') {
            closeGenericConfirmModal(false);
        }
    });

    document.getElementById('genericConfirmCancel').addEventListener('click', () => {
        closeGenericConfirmModal(false);
    });

    document.getElementById('genericConfirmAccept').addEventListener('click', () => {
        closeGenericConfirmModal(true);
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeSessionsMenu();
            closeWorkspaceMenu();
            if (document.getElementById('savedSessionsModal').classList.contains('visible')) {
                closeSavedSessionModal();
            }
            if (document.getElementById('saveSessionAsModal').classList.contains('visible')) {
                closeSaveSessionAsModal();
            }
            if (document.getElementById('closeSessionConfirmModal').classList.contains('visible')) {
                closeCloseSessionConfirmModal(false);
            }
            if (document.getElementById('genericConfirmModal').classList.contains('visible')) {
                closeGenericConfirmModal(false);
            }
        }
    });

    function setWorkspaceSaveMessage(message, type = '') {
        const label = document.getElementById('sessionLabel');
        if (!label || !message) {
            return;
        }
        label.textContent = message;
        label.dataset.workspaceSaveStatus = type;
    }

    function buildWorkspaceLayoutSnapshotFromState(count, className, rects, columnWeights, rowWeights, baseCount) {
        if (!count || !Array.isArray(rects) || rects.length !== count) {
            return null;
        }

        return {
            class_name: 'layout-split-local',
            split_slot_rects: rects.map((rect, index) => ({
                originSlot: Number.isInteger(Number(rect.originSlot)) ? Number(rect.originSlot) : index,
                x: Math.max(1, Number(rect.x) || 1),
                y: Math.max(1, Number(rect.y) || 1),
                w: Math.max(1, Number(rect.w) || 1),
                h: Math.max(1, Number(rect.h) || 1)
            })),
            split_column_weights: cloneSplitTrackWeights(columnWeights),
            split_row_weights: cloneSplitTrackWeights(rowWeights),
            original_split_slot_count: Math.max(1, Number(baseCount || count) || count)
        };
    }

    function buildActiveWorkspaceLayoutSnapshot(groupId = activeGroupId) {
        if (!groupId) {
            return null;
        }

        if (visibleGroupId === groupId && gridBuilt) {
            const grid = document.getElementById('terminalsGrid');
            const rects = cloneSplitSlotRects(
                grid?.className === 'layout-split-local'
                    ? ensureSplitSlotRects()
                    : fixedLayoutSlotRects(terminals.length, grid?.className || '')
            );
            const size = getSplitGridSize(rects);
            return buildWorkspaceLayoutSnapshotFromState(
                terminals.length,
                grid?.className || '',
                rects,
                normalizeSplitTrackWeights(splitColumnWeights, size.columns),
                normalizeSplitTrackWeights(splitRowWeights, size.rows),
                originalSplitSlotCount || terminals.length
            );
        }

        const cached = cachedGroupViews.get(groupId);
        if (!cached) {
            return null;
        }

        return buildWorkspaceLayoutSnapshotFromState(
            cached.terminals?.length || 0,
            cached.className || '',
            cached.splitSlotRects,
            cached.splitColumnWeights,
            cached.splitRowWeights,
            cached.originalSplitSlotCount || cached.terminals?.length || 0
        );
    }

    function getWorkspacePanesInVisualOrder(groupId = activeGroupId) {
        if (!groupId) {
            return [];
        }

        if (visibleGroupId === groupId && gridBuilt) {
            const grid = document.getElementById('terminalsGrid');
            const cards = Array.from(grid?.children || []);
            if (cards.length) {
                return cards
                    .map(card => terminals[Number(card.dataset.slot)])
                    .filter(Boolean);
            }
            return terminals.filter(Boolean);
        }

        const cached = cachedGroupViews.get(groupId);
        if (!cached) {
            return [];
        }

        const cachedTerminals = cached.terminals || [];
        const cachedCards = Array.from(cached.fragment?.children || []);
        if (cachedCards.length) {
            return cachedCards
                .map(card => cachedTerminals[Number(card.dataset.slot)])
                .filter(Boolean);
        }
        return cachedTerminals.filter(Boolean);
    }

    function buildWorkspaceTerminalEntry(terminal, index, connectionMode) {
        const session = terminal?._session || {};
        const rawStartupMode = String(session.startup_mode || '').trim();
        const startupMode = isExplorerPaneInstance(terminal) || isExplorerSession(session)
            ? 'explorer'
            : (isBrowserPaneInstance(terminal) || isBrowserSession(session)
                ? 'browser'
                : (rawStartupMode === 'agent' ? 'agent' : 'terminal'));
        const commandMode = startupMode === 'agent'
            ? 'agent'
            : (startupMode === 'explorer' || startupMode === 'browser' ? startupMode : 'command');
        const selectedDirectory = startupMode === 'explorer'
            ? (session.explorer_root_directory || session.directory || '')
            : (session.directory || '');
        const explorerSlot = startupMode === 'explorer' && terminal ? terminals.indexOf(terminal) : -1;
        if (explorerSlot !== -1) {
            /* Fold the shown tab's live mode + scroll into its record so the
               serialized tab views reflect what is on screen right now (2.f). */
            explorerCaptureActiveTabView(explorerSlot);
        }
        const explorerTabs = startupMode === 'explorer' && terminal
            ? explorerSerializeTabs(terminal)
            : { open_tabs: [], active_tab: '', tab_views: {} };
        const mdAppearance = startupMode === 'explorer' ? explorerMarkdownAppearance() : null;
        /* Persist the pane's live light/dark explorer theme so a saved session
           relaunches with the same appearance (its localStorage override is
           keyed by session_id and won't survive new session ids). */
        const explorerTheme = startupMode === 'explorer'
            ? normalizeExplorerTheme(
                (explorerSlot !== -1
                    ? document.getElementById(`tc-${explorerSlot}`)?.dataset.explorerTheme
                    : terminal?._cachedExplorerTheme)
                || session.explorer_theme
                || 'dark'
            )
            : '';

        return {
            title: session.title || `Terminal ${index + 1}`,
            directory: selectedDirectory,
            initial_command: startupMode === 'explorer' ? '' : (session.initial_command || ''),
            initial_command_mode: commandMode,
            startup_mode: startupMode,
            agent_selection: commandMode === 'agent' ? (session.agent_selection || '') : '',
            custom_agent: commandMode === 'agent' ? (session.custom_agent || '') : '',
            agent_auto_mode: commandMode === 'agent' ? Boolean(session.agent_auto_mode) : false,
            explorer_tree_open: startupMode === 'explorer' ? Boolean(terminal?._explorerTreeSidebarOpen) : false,
            explorer_git_open: startupMode === 'explorer' ? Boolean(terminal?._explorerGitSidebarOpen) : false,
            explorer_open_tabs: explorerTabs.open_tabs,
            explorer_active_tab: explorerTabs.active_tab,
            explorer_tab_views: explorerTabs.tab_views,
            explorer_md_preset: mdAppearance ? mdAppearance.preset : '',
            explorer_md_font: mdAppearance ? mdAppearance.font : '',
            explorer_theme: explorerTheme,
            distribution: connectionMode === 'wsl' ? (session.distribution || '') : '',
            use_wsl: connectionMode === 'wsl' ? Boolean(session.use_wsl) : false,
            use_powershell: connectionMode === 'wsl' ? Boolean(session.use_powershell) : false
        };
    }

    function buildActiveWorkspaceSessionConfig(groupId = activeGroupId) {
        const group = getGroupById(groupId);
        const groupTerminals = getWorkspacePanesInVisualOrder(groupId);
        const connectionMode = group?.connection_mode === 'wsl' ? 'wsl' : 'ssh';
        const terminalEntries = groupTerminals.map((terminal, index) => (
            buildWorkspaceTerminalEntry(terminal, index, connectionMode)
        ));
        const firstSession = groupTerminals.find(terminal => terminal?._session)?._session || {};
        const firstDirectory = firstSession.explorer_root_directory || firstSession.directory || '';

        return {
            connection_mode: connectionMode,
            terminal_count: terminalEntries.length,
            layout: group?.layout || 'single',
            ssh: {
                host: connectionMode === 'ssh' ? (firstSession.host || '') : '',
                username: connectionMode === 'ssh' ? (firstSession.username || 'ubuntu') : 'ubuntu',
                password: '',
                port: connectionMode === 'ssh' ? (Number(firstSession.port) || 22) : 22,
                default_dir: connectionMode === 'ssh' ? firstDirectory : ''
            },
            wsl: {
                distribution: connectionMode === 'wsl' ? (firstSession.distribution || '') : '',
                username: connectionMode === 'wsl' ? (firstSession.username || '') : '',
                default_dir: connectionMode === 'wsl' ? firstDirectory : ''
            },
            terminals: terminalEntries,
            workspace_layout: buildActiveWorkspaceLayoutSnapshot(groupId)
        };
    }

    async function saveActiveWorkspaceSession(button = null, options = {}) {
        const silent = Boolean(options.silent);
        const targetGroupId = options.groupId || getActiveWorkspaceGroupId();
        if (!targetGroupId) {
            if (!silent) {
                setWorkspaceSaveMessage('No active session group to save.', 'error');
            }
            return { ok: false, error: 'No active session group to save.' };
        }

        const promptForName = Boolean(options.promptForName);
        const createNewSession = Boolean(options.createNewSession);
        const group = getGroupById(targetGroupId);
        const config = buildActiveWorkspaceSessionConfig(targetGroupId);
        if (!config.terminals.length) {
            if (!silent) {
                setWorkspaceSaveMessage('No active terminals to save.', 'error');
            }
            return { ok: false, skipped: true, error: 'No active terminals to save.' };
        }

        const saveTarget = getWorkspaceSaveTarget(targetGroupId);
        const suggestedName = saveTarget.name || group?.name || `Workspace ${new Date().toLocaleTimeString()}`;
        let sessionName = suggestedName;
        let openSavedSessionNow = false;
        if (promptForName) {
            const result = await openSaveSessionAsModal(suggestedName);
            if (!result) {
                return { ok: false, cancelled: true };
            }
            sessionName = result.name;
            openSavedSessionNow = Boolean(result.openNow);
        }

        const savedSessionId = createNewSession ? '' : saveTarget.id;
        const shouldUpdateSourceGroup = !createNewSession;
        const shouldActivateSavedSession = !createNewSession || openSavedSessionNow;
        const previousText = button?.textContent;
        if (button) {
            button.disabled = true;
            button.textContent = promptForName ? 'Saving as...' : 'Saving...';
        }

        try {
            const savePayload = {
                id: savedSessionId || undefined,
                name: sessionName,
                config,
                workspace_only: true,
                source_saved_session_id: saveTarget.id || undefined,
                activate: shouldActivateSavedSession
            };
            if (shouldUpdateSourceGroup) {
                savePayload.group_id = targetGroupId;
            }

            const response = await fetch('/api/saved-sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(savePayload)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Save failed with status ${response.status}`);
            }
            if (shouldUpdateSourceGroup && data.group?.group_id) {
                const savedGroup = getGroupById(data.group.group_id);
                if (savedGroup) {
                    Object.assign(savedGroup, data.group);
                }
            }
            if (shouldUpdateSourceGroup) {
                rememberWorkspaceSaveTarget(
                    targetGroupId,
                    data.id || data.saved_session?.id || savedSessionId,
                    data.name || data.saved_session?.name || sessionName
                );
            }
            notifySavedSessionUpdated(data, { activate: openSavedSessionNow });
            const savedName = data.name || sessionName;
            if (createNewSession && openSavedSessionNow) {
                if (!silent) {
                    setWorkspaceSaveMessage(`Saved session "${savedName}". Opening...`, 'success');
                }
                await launchSavedSession(data);
            } else if (!silent) {
                setWorkspaceSaveMessage(`Saved session "${savedName}".`, 'success');
            }
            return { ok: true, name: savedName };
        } catch (error) {
            console.error('[GridVibe Sessions] workspace save failed:', error);
            if (!silent) {
                setWorkspaceSaveMessage(`Save failed: ${error.message}`, 'error');
            }
            return { ok: false, error: error.message, name: sessionName };
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = previousText || 'Save Session';
            }
        }
    }

    function saveActiveWorkspaceSessionAs(button = null) {
        return saveActiveWorkspaceSession(button, {
            promptForName: true,
            createNewSession: true
        });
    }

    async function saveAllWorkspaceSessions(button = null) {
        const groups = sessionGroups.slice();
        if (!groups.length) {
            setWorkspaceSaveMessage('No sessions to save.', 'error');
            return;
        }

        const previousText = button?.textContent;
        if (button) {
            button.disabled = true;
            button.textContent = 'Saving all...';
        }

        let savedCount = 0;
        const failures = [];
        try {
            for (const group of groups) {
                const result = await saveActiveWorkspaceSession(null, {
                    groupId: group.group_id,
                    silent: true
                });
                if (result?.ok) {
                    savedCount += 1;
                } else if (result && !result.skipped) {
                    failures.push(result.name || group.name || group.group_id);
                }
            }
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = previousText || 'Save All Sessions';
            }
        }

        if (failures.length) {
            setWorkspaceSaveMessage(
                `Saved ${savedCount} session${savedCount === 1 ? '' : 's'}, ${failures.length} failed.`,
                'error'
            );
        } else {
            setWorkspaceSaveMessage(
                `Saved all ${savedCount} session${savedCount === 1 ? '' : 's'}.`,
                'success'
            );
        }
    }

    function updateWorkspaceSaveItemState() {
        const item = document.getElementById('saveWorkspaceItem');
        if (item) {
            item.disabled = !sessionGroups.length;
        }
    }

    async function saveWorkspace(button = null) {
        if (!sessionGroups.length) {
            setWorkspaceSaveMessage('No sessions to save.', 'error');
            return;
        }

        if (button) {
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
        }

        try {
            const response = await fetch('/api/runtime-state/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Save failed with status ${response.status}`);
            }
            const savedLabel = String(data.label || '').trim();
            const savedAt = Number(data.saved_at);
            const savedTime = Number.isFinite(savedAt)
                ? new Date(savedAt * 1000).toLocaleTimeString()
                : '';
            const detail = [savedLabel ? `"${savedLabel}"` : '', savedTime]
                .filter(Boolean)
                .join(' at ');
            setWorkspaceSaveMessage(`Workspace saved${detail ? ` — ${detail}` : ''}.`, 'success');
        } catch (error) {
            console.error('[GridVibe Sessions] workspace save failed:', error);
            setWorkspaceSaveMessage(`Workspace save failed: ${error.message} — try again.`, 'error');
        } finally {
            if (button) {
                button.setAttribute('aria-busy', 'false');
                button.disabled = !sessionGroups.length;
            }
        }
    }

    function renderSessionTabs() {
        updateWorkspaceSaveItemState();
        const container = document.getElementById('sessionTabs');
        if (!container) return;

        container.innerHTML = '';

        sessionGroups.forEach((group, index) => {
            const tabNumber = index + 1;
            const button = document.createElement('div');
            button.className = `session-tab${group.group_id === activeGroupId ? ' active' : ''} draggable`;
            button.dataset.groupId = group.group_id;
            applyTabColour(button, group.group_id);

            const tabButton = document.createElement('button');
            tabButton.className = 'session-tab-main';
            tabButton.type = 'button';
            tabButton.title = tabNumber <= 9
                ? `Alt+${tabNumber}: ${group.name || group.group_id}`
                : `${tabNumber}. ${group.name || group.group_id}`;
            tabButton.setAttribute(
                'aria-label',
                tabNumber <= 9
                    ? `Alt+${tabNumber}: ${group.name || group.group_id} session`
                    : `${tabNumber}. ${group.name || group.group_id} session`
            );
            tabButton.addEventListener('click', event => {
                if (Date.now() < suppressSessionTabClickUntil) {
                    event.preventDefault();
                    return;
                }
                switchGroup(group.group_id);
            });

            const number = document.createElement('span');
            number.className = 'session-tab-number';
            number.textContent = String(tabNumber);
            number.setAttribute('aria-hidden', 'true');

            const label = document.createElement('span');
            label.className = 'session-tab-label';
            label.textContent = group.name || group.group_id;
            tabButton.appendChild(number);
            tabButton.appendChild(label);
            button.appendChild(tabButton);

            const closeButton = document.createElement('button');
            closeButton.className = 'session-tab-close';
            closeButton.type = 'button';
            closeButton.title = `Close ${group.name || group.group_id}`;
            closeButton.setAttribute('aria-label', `Close ${group.name || group.group_id}`);
            closeButton.textContent = '×';
            closeButton.addEventListener('mousedown', event => {
                event.stopPropagation();
            });
            closeButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                closeSessionGroup(group.group_id);
            });
            button.appendChild(closeButton);

            wireSessionTabDragAndDrop(button, container);
            container.appendChild(button);
        });
    }

    function setSessionGroupsOrder(orderedGroupIds) {
        const groupsById = new Map(sessionGroups.map(group => [group.group_id, group]));
        const nextGroups = [];
        const seen = new Set();

        orderedGroupIds.forEach(groupId => {
            if (groupsById.has(groupId) && !seen.has(groupId)) {
                nextGroups.push(groupsById.get(groupId));
                seen.add(groupId);
            }
        });

        sessionGroups.forEach(group => {
            if (!seen.has(group.group_id)) {
                nextGroups.push(group);
            }
        });

        sessionGroups = nextGroups;
    }

    function getSessionGroupOrder() {
        return sessionGroups.map(group => group.group_id);
    }

    function getSessionGroupByNumber(number) {
        return sessionGroups[number - 1] || null;
    }

    function hasSameSessionGroupOrder(left, right) {
        if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
            return false;
        }
        return left.every((groupId, index) => groupId === right[index]);
    }

    function clearSessionTabDragState() {
        const container = document.getElementById('sessionTabs');
        if (container) {
            container.querySelectorAll('.session-tab.dragging, .session-tab.drag-target')
                .forEach(tab => tab.classList.remove('dragging', 'drag-target'));
        }
        draggedSessionTab = null;
        draggedSessionTabOriginOrder = [];
        sessionTabDropHandled = false;
    }

    function getSessionTabAfterElement(container, clientX) {
        const tabs = [...container.querySelectorAll('.session-tab[data-group-id]:not(.dragging)')];
        return tabs.reduce((closest, tab) => {
            const rect = tab.getBoundingClientRect();
            const offset = clientX - rect.left - (rect.width / 2);
            if (offset < 0 && offset > closest.offset) {
                return { offset, element: tab };
            }
            return closest;
        }, { offset: Number.NEGATIVE_INFINITY, element: null }).element;
    }

    async function persistSessionGroupOrder(groupIds) {
        const response = await fetch('/api/session-groups/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_ids: groupIds })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to save session tab order');
        }

        sessionGroups = Array.isArray(data.groups) ? data.groups : [];
        renderSessionTabs();
    }

    function wireSessionTabDragAndDrop(button, container) {
        if (!button || !container) {
            return;
        }

        button.draggable = true;

        button.addEventListener('dragstart', event => {
            draggedSessionTab = button;
            draggedSessionTabOriginOrder = getSessionGroupOrder();
            sessionTabDropHandled = false;
            button.classList.add('dragging');
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', button.dataset.groupId || '');
            }
        });

        button.addEventListener('dragend', () => {
            suppressSessionTabClickUntil = Date.now() + 250;
            if (!sessionTabDropHandled && draggedSessionTabOriginOrder.length > 0) {
                setSessionGroupsOrder(draggedSessionTabOriginOrder);
                renderSessionTabs();
            }
            clearSessionTabDragState();
        });

        button.addEventListener('dragover', event => {
            if (!draggedSessionTab || draggedSessionTab === button) {
                return;
            }

            event.preventDefault();
            container.querySelectorAll('.session-tab.drag-target')
                .forEach(tab => {
                    if (tab !== button) {
                        tab.classList.remove('drag-target');
                    }
                });
            button.classList.add('drag-target');
        });

        button.addEventListener('dragleave', event => {
            const next = event.relatedTarget;
            if (next && button.contains(next)) {
                return;
            }
            button.classList.remove('drag-target');
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        const container = document.getElementById('sessionTabs');
        if (!container) {
            return;
        }

        container.addEventListener('dragover', event => {
            if (!draggedSessionTab) {
                return;
            }

            event.preventDefault();
            const afterTab = getSessionTabAfterElement(container, event.clientX);
            if (!afterTab) {
                container.appendChild(draggedSessionTab);
                return;
            }

            if (afterTab !== draggedSessionTab) {
                container.insertBefore(draggedSessionTab, afterTab);
            }
        });

        container.addEventListener('drop', async event => {
            if (!draggedSessionTab) {
                return;
            }

            event.preventDefault();
            sessionTabDropHandled = true;

            const orderedGroupIds = [...container.querySelectorAll('.session-tab[data-group-id]')]
                .map(tab => tab.dataset.groupId)
                .filter(Boolean);
            const previousOrder = draggedSessionTabOriginOrder.slice();

            container.querySelectorAll('.session-tab.drag-target')
                .forEach(tab => tab.classList.remove('drag-target'));

            if (hasSameSessionGroupOrder(previousOrder, orderedGroupIds)) {
                return;
            }

            setSessionGroupsOrder(orderedGroupIds);
            try {
                await persistSessionGroupOrder(orderedGroupIds);
            } catch (error) {
                console.error('Session tab reorder failed:', error);
                setSessionGroupsOrder(previousOrder);
                renderSessionTabs();
            }
        });
    });

    function getLayoutClass(count, layout) {
        if (count === 1) return 'layout-single';
        if (count === 2) return `layout-2-${layout === 'horizontal' ? 'horizontal' : 'vertical'}`;
        if (count === 3) {
            if (layout === 'horizontal') return 'layout-3-horizontal';
            if (layout === 'split') return 'layout-3-split';
            return 'layout-3-vertical';
        }
        if (count >= 4) return 'layout-grid';
        return '';
    }

    function getGridMetrics(count) {
        if (count >= 8) {
            return { columns: 4, rows: 2 };
        }
        if (count >= 6) {
            return { columns: 3, rows: 2 };
        }
        if (count >= 4) {
            return { columns: 2, rows: 2 };
        }
        return null;
    }

    function cloneSplitSlotRects(rects = splitSlotRects) {
        return Array.isArray(rects)
            ? rects.map(rect => normalizeSplitRectMetadata({
                id: rect.id || makeSplitRectId(),
                originSlot: rect.originSlot ?? 0,
                splitId: rect.splitId || '',
                splitRole: rect.splitRole || '',
                isSplitChild: Boolean(rect.isSplitChild),
                parentRect: rect.parentRect ? cloneSplitRect(rect.parentRect) : null,
                x: rect.x,
                y: rect.y,
                w: rect.w,
                h: rect.h,
                ancestors: cloneSplitAncestors(rect.ancestors || []),
            }))
            : null;
    }

    function cloneSplitTrackWeights(weights) {
        return Array.isArray(weights)
            ? weights.map(weight => Math.max(0.01, Number(weight) || 1))
            : null;
    }

    function clearResizeHandles() {
        const overlay = document.getElementById('terminalResizeOverlay');
        if (overlay) {
            overlay.innerHTML = '';
        }
    }

    function clearActiveGridResize() {
        if (activeGridResize?.fitFrame) {
            cancelAnimationFrame(activeGridResize.fitFrame);
        }
        document.querySelectorAll('.terminal-resize-handle.active')
            .forEach(handle => handle.classList.remove('active'));
        document.body.classList.remove('terminal-grid-resizing');
        document.body.style.cursor = '';
        activeGridResize = null;
    }

    function normalizeSplitTrackWeights(weights, targetLength) {
        const normalized = Array.from({ length: targetLength }, (_, index) => {
            const value = Array.isArray(weights) ? Number(weights[index]) : 1;
            return Number.isFinite(value) && value > 0 ? value : 1;
        });
        return normalized.length ? normalized : [1];
    }

    function initializeSplitTrackWeights(columnCount, rowCount) {
        splitColumnWeights = normalizeSplitTrackWeights(splitColumnWeights, columnCount);
        splitRowWeights = normalizeSplitTrackWeights(splitRowWeights, rowCount);
    }

    function applySplitTrackTemplates(grid) {
        if (!grid) {
            return;
        }
        if (Array.isArray(splitColumnWeights) && splitColumnWeights.length > 0) {
            grid.style.gridTemplateColumns = splitColumnWeights.map(weight => `${Math.max(0.01, weight)}fr`).join(' ');
        } else {
            grid.style.gridTemplateColumns = '';
        }
        if (Array.isArray(splitRowWeights) && splitRowWeights.length > 0) {
            grid.style.gridTemplateRows = splitRowWeights.map(weight => `${Math.max(0.01, weight)}fr`).join(' ');
        } else {
            grid.style.gridTemplateRows = '';
        }
    }

    function getSplitGridSize(rects = splitSlotRects) {
        if (!Array.isArray(rects) || rects.length === 0) {
            return { columns: 1, rows: 1 };
        }
        return {
            columns: Math.max(1, ...rects.map(rect => rect.x + rect.w - 1)),
            rows: Math.max(1, ...rects.map(rect => rect.y + rect.h - 1)),
        };
    }

    function getHandleSlotRects(grid) {
        if (!grid || grid.children.length <= 1) {
            return [];
        }
        if (Array.isArray(splitSlotRects) && splitSlotRects.length === grid.children.length) {
            return splitSlotRects;
        }
        return fixedLayoutSlotRects(grid.children.length, grid.className || '');
    }

    function getResizableGridMetrics(grid, columnWeights = splitColumnWeights, rowWeights = splitRowWeights) {
        if (!grid || !Array.isArray(columnWeights) || !Array.isArray(rowWeights)) {
            return null;
        }
        const bounds = grid.getBoundingClientRect();
        const style = window.getComputedStyle(grid);
        const paddingLeft = parseFloat(style.paddingLeft) || 0;
        const paddingRight = parseFloat(style.paddingRight) || 0;
        const paddingTop = parseFloat(style.paddingTop) || 0;
        const paddingBottom = parseFloat(style.paddingBottom) || 0;
        const columnGap = parseFloat(style.columnGap || style.gap) || 0;
        const rowGap = parseFloat(style.rowGap || style.gap) || 0;
        const gridContentWidth = Math.max(0, bounds.width - paddingLeft - paddingRight);
        const gridContentHeight = Math.max(0, bounds.height - paddingTop - paddingBottom);
        const columnTrackSpace = Math.max(0, gridContentWidth - (columnWeights.length - 1) * columnGap);
        const rowTrackSpace = Math.max(0, gridContentHeight - (rowWeights.length - 1) * rowGap);
        const columnTotal = columnWeights.reduce((sum, weight) => sum + Math.max(0.01, weight), 0) || 1;
        const rowTotal = rowWeights.reduce((sum, weight) => sum + Math.max(0.01, weight), 0) || 1;

        return {
            bounds,
            paddingLeft,
            paddingTop,
            columnGap,
            rowGap,
            gridContentWidth,
            gridContentHeight,
            columnTrackSpace,
            rowTrackSpace,
            columnSizes: columnWeights.map(weight => columnTrackSpace * (Math.max(0.01, weight) / columnTotal)),
            rowSizes: rowWeights.map(weight => rowTrackSpace * (Math.max(0.01, weight) / rowTotal)),
        };
    }

    function sumTrackSpan(sizes, start, span, gap) {
        const trackTotal = sizes.slice(start, start + span).reduce((sum, value) => sum + value, 0);
        return trackTotal + Math.max(0, span - 1) * gap;
    }

    function getPaneCandidateSurface(rect, columnWeights, rowWeights, metrics) {
        return {
            width: sumTrackSpan(metrics.columnSizes, rect.x - 1, rect.w, metrics.columnGap),
            height: sumTrackSpan(metrics.rowSizes, rect.y - 1, rect.h, metrics.rowGap),
        };
    }

    function validateResizeCandidate(axis, candidateWeights) {
        const grid = document.getElementById('terminalsGrid');
        const rects = splitSlotRects;
        if (!grid || !Array.isArray(rects) || rects.length !== grid.children.length) {
            return false;
        }

        const columnWeights = axis === 'vertical' ? candidateWeights : splitColumnWeights;
        const rowWeights = axis === 'horizontal' ? candidateWeights : splitRowWeights;
        const metrics = getResizableGridMetrics(grid, columnWeights, rowWeights);
        if (!metrics || metrics.gridContentWidth <= 0 || metrics.gridContentHeight <= 0) {
            return false;
        }

        const minimumSurface = metrics.columnTrackSpace * metrics.rowTrackSpace * MIN_RESIZE_SURFACE_RATIO;
        return rects.every((rect, visualIndex) => {
            const surface = getPaneCandidateSurface(rect, columnWeights, rowWeights, metrics);
            if (surface.width * surface.height < minimumSurface) {
                return false;
            }

            const card = grid.children[visualIndex];
            const slotIndex = Number(card?.dataset?.slot);
            const terminal = Number.isInteger(slotIndex) ? terminals[slotIndex] : null;
            if (isExplorerPaneInstance(terminal) || isExplorerSession(terminal?._session)) {
                return true;
            }

            const headerHeight = card?.querySelector('.terminal-header')?.getBoundingClientRect()?.height || 34;
            const term = terminal?.term;
            const cell = term?._core?._renderService?.dimensions?.css?.cell || {};
            const cellWidth = Number(cell.width || 8);
            const cellHeight = Number(cell.height || 17);
            const availableWidth = Math.max(0, surface.width - 2);
            const availableHeight = Math.max(0, surface.height - headerHeight - 2);
            return Math.floor(availableWidth / cellWidth) >= MIN_SPLIT_COLS
                && Math.floor(availableHeight / cellHeight) >= MIN_SPLIT_ROWS;
        });
    }

    function hasSharedGridEdge(rects, axis, lineIndex) {
        return getSharedGridEdgeSegments(rects, axis, lineIndex).length > 0;
    }

    function getSharedGridEdgeSegments(rects, axis, lineIndex) {
        const before = rects.filter(rect => (
            axis === 'vertical'
                ? rect.x + rect.w - 1 === lineIndex
                : rect.y + rect.h - 1 === lineIndex
        ));
        const after = rects.filter(rect => (
            axis === 'vertical'
                ? rect.x === lineIndex + 1
                : rect.y === lineIndex + 1
        ));
        const segments = [];
        before.forEach(left => {
            after.forEach(right => {
                const start = axis === 'vertical'
                    ? Math.max(left.y, right.y)
                    : Math.max(left.x, right.x);
                const end = axis === 'vertical'
                    ? Math.min(left.y + left.h, right.y + right.h)
                    : Math.min(left.x + left.w, right.x + right.w);
                if (start < end) {
                    segments.push({ start, end });
                }
            });
        });
        segments.sort((a, b) => a.start - b.start || a.end - b.end);
        return segments.reduce((merged, segment) => {
            const previous = merged[merged.length - 1];
            if (previous && segment.start <= previous.end) {
                previous.end = Math.max(previous.end, segment.end);
            } else {
                merged.push({ ...segment });
            }
            return merged;
        }, []);
    }

    function getSharedGridEdgeSegmentStyle(axis, segment, metrics) {
        if (axis === 'vertical') {
            const startIndex = segment.start - 1;
            const span = segment.end - segment.start;
            return {
                top: metrics.bounds.top + metrics.paddingTop + trackStartOffset(metrics.rowSizes, metrics.rowGap, startIndex),
                size: sumTrackSpan(metrics.rowSizes, startIndex, span, metrics.rowGap),
            };
        }
        const startIndex = segment.start - 1;
        const span = segment.end - segment.start;
        return {
            left: metrics.bounds.left + metrics.paddingLeft + trackStartOffset(metrics.columnSizes, metrics.columnGap, startIndex),
            size: sumTrackSpan(metrics.columnSizes, startIndex, span, metrics.columnGap),
        };
    }

    function trackStartOffset(sizes, gap, startIndex) {
        const trackTotal = sizes.slice(0, startIndex).reduce((sum, value) => sum + value, 0);
        return trackTotal + Math.max(0, startIndex) * gap;
    }

    function lineOffset(sizes, gap, lineIndex) {
        const trackTotal = sizes.slice(0, lineIndex).reduce((sum, value) => sum + value, 0);
        return trackTotal + Math.max(0, lineIndex - 1) * gap + (gap / 2);
    }

    function affectedResizeIndices(axis, lineIndex) {
        const grid = document.getElementById('terminalsGrid');
        if (!grid || !Array.isArray(splitSlotRects)) {
            return terminals.map((_, index) => index);
        }
        return Array.from(grid.children)
            .map((card, visualIndex) => {
                const rect = splitSlotRects[visualIndex];
                const slotIndex = Number(card.dataset.slot);
                if (!rect || !Number.isInteger(slotIndex)) {
                    return -1;
                }
                const containsLineNeighbor = axis === 'vertical'
                    ? rect.x <= lineIndex + 1 && rect.x + rect.w - 1 >= lineIndex
                    : rect.y <= lineIndex + 1 && rect.y + rect.h - 1 >= lineIndex;
                return containsLineNeighbor ? slotIndex : -1;
            })
            .filter(index => index >= 0);
    }

    function getResizeTrackGroups(axis, lineIndex) {
        if (!Array.isArray(splitSlotRects)) {
            return null;
        }
        const beforeRects = splitSlotRects.filter(rect => (
            axis === 'vertical'
                ? rect.x + rect.w - 1 === lineIndex
                : rect.y + rect.h - 1 === lineIndex
        ));
        const afterRects = splitSlotRects.filter(rect => (
            axis === 'vertical'
                ? rect.x === lineIndex + 1
                : rect.y === lineIndex + 1
        ));
        if (beforeRects.length === 0 || afterRects.length === 0) {
            return null;
        }

        const beforeStart = axis === 'vertical'
            ? Math.min(...beforeRects.map(rect => rect.x))
            : Math.min(...beforeRects.map(rect => rect.y));
        const afterEnd = axis === 'vertical'
            ? Math.max(...afterRects.map(rect => rect.x + rect.w - 1))
            : Math.max(...afterRects.map(rect => rect.y + rect.h - 1));
        const makeRange = (start, end) => Array.from(
            { length: Math.max(0, end - start + 1) },
            (_, offset) => start - 1 + offset
        );
        return {
            before: makeRange(beforeStart, lineIndex),
            after: makeRange(lineIndex + 1, afterEnd),
        };
    }

    function ensureResizableSplitLayout() {
        const grid = document.getElementById('terminalsGrid');
        if (!grid || grid.children.length <= 1 || window.innerWidth <= 700) {
            return false;
        }
        ensureSplitSlotRects();
        const size = getSplitGridSize(splitSlotRects);
        initializeSplitTrackWeights(size.columns, size.rows);
        return applySplitSlotGeometry({ fit: false, renderHandles: false });
    }

    function renderResizeHandles() {
        const overlay = document.getElementById('terminalResizeOverlay');
        const grid = document.getElementById('terminalsGrid');
        if (!overlay) {
            return;
        }
        if (activeGridResize) {
            return;
        }
        overlay.innerHTML = '';
        if (!grid || grid.children.length <= 1 || window.innerWidth <= 700) {
            return;
        }

        const rects = getHandleSlotRects(grid);
        const size = getSplitGridSize(rects);
        const columnWeights = normalizeSplitTrackWeights(splitColumnWeights, size.columns);
        const rowWeights = normalizeSplitTrackWeights(splitRowWeights, size.rows);
        const metrics = getResizableGridMetrics(grid, columnWeights, rowWeights);
        if (!metrics || metrics.gridContentWidth <= 0 || metrics.gridContentHeight <= 0) {
            return;
        }

        for (let lineIndex = 1; lineIndex < size.columns; lineIndex++) {
            const segments = getSharedGridEdgeSegments(rects, 'vertical', lineIndex);
            if (segments.length === 0) {
                continue;
            }
            segments.forEach(segment => {
                const segmentStyle = getSharedGridEdgeSegmentStyle('vertical', segment, metrics);
                const handle = document.createElement('button');
                handle.type = 'button';
                handle.className = 'terminal-resize-handle vertical';
                handle.dataset.resizeAxis = 'vertical';
                handle.dataset.resizeLine = String(lineIndex);
                handle.setAttribute('aria-label', 'Resize terminal columns');
                handle.style.left = `${metrics.bounds.left + metrics.paddingLeft + lineOffset(metrics.columnSizes, metrics.columnGap, lineIndex)}px`;
                handle.style.top = `${segmentStyle.top}px`;
                handle.style.height = `${segmentStyle.size}px`;
                handle.addEventListener('pointerdown', startGridResize);
                overlay.appendChild(handle);
            });
        }

        for (let lineIndex = 1; lineIndex < size.rows; lineIndex++) {
            const segments = getSharedGridEdgeSegments(rects, 'horizontal', lineIndex);
            if (segments.length === 0) {
                continue;
            }
            segments.forEach(segment => {
                const segmentStyle = getSharedGridEdgeSegmentStyle('horizontal', segment, metrics);
                const handle = document.createElement('button');
                handle.type = 'button';
                handle.className = 'terminal-resize-handle horizontal';
                handle.dataset.resizeAxis = 'horizontal';
                handle.dataset.resizeLine = String(lineIndex);
                handle.setAttribute('aria-label', 'Resize terminal rows');
                handle.style.left = `${segmentStyle.left}px`;
                handle.style.top = `${metrics.bounds.top + metrics.paddingTop + lineOffset(metrics.rowSizes, metrics.rowGap, lineIndex)}px`;
                handle.style.width = `${segmentStyle.size}px`;
                handle.addEventListener('pointerdown', startGridResize);
                overlay.appendChild(handle);
            });
        }
    }

    function startGridResize(event) {
        const handle = event.currentTarget;
        const axis = handle?.dataset?.resizeAxis;
        const lineIndex = Number(handle?.dataset?.resizeLine);
        if (!axis || !Number.isInteger(lineIndex) || !ensureResizableSplitLayout()) {
            return;
        }

        const grid = document.getElementById('terminalsGrid');
        const metrics = getResizableGridMetrics(grid, splitColumnWeights, splitRowWeights);
        if (!metrics) {
            return;
        }
        const trackGroups = getResizeTrackGroups(axis, lineIndex);
        if (!trackGroups) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        handle.setPointerCapture?.(event.pointerId);
        handle.classList.add('active');
        document.body.classList.add('terminal-grid-resizing');
        document.body.style.cursor = axis === 'vertical' ? 'col-resize' : 'row-resize';

        activeGridResize = {
            axis,
            lineIndex,
            pointerId: event.pointerId,
            startClientX: event.clientX,
            startClientY: event.clientY,
            startColumnWeights: cloneSplitTrackWeights(splitColumnWeights),
            startRowWeights: cloneSplitTrackWeights(splitRowWeights),
            startColumnSizes: metrics.columnSizes.slice(),
            startRowSizes: metrics.rowSizes.slice(),
            trackGroups,
            affectedIndices: affectedResizeIndices(axis, lineIndex),
            handle,
            fitFrame: null,
        };
    }

    function scheduleActiveGridResizeFits() {
        if (!activeGridResize || activeGridResize.fitFrame) {
            return;
        }
        activeGridResize.fitFrame = window.requestAnimationFrame(() => {
            const resize = activeGridResize;
            if (!resize) {
                return;
            }
            resize.fitFrame = null;
            resize.affectedIndices.forEach(index => scheduleFit(index));
        });
    }

    function updateGridResize(event) {
        const resize = activeGridResize;
        if (!resize || event.pointerId !== resize.pointerId) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();

        const isVertical = resize.axis === 'vertical';
        const delta = isVertical
            ? event.clientX - resize.startClientX
            : event.clientY - resize.startClientY;
        const sizes = isVertical ? resize.startColumnSizes : resize.startRowSizes;
        const weights = isVertical ? resize.startColumnWeights : resize.startRowWeights;
        const beforeIndexes = resize.trackGroups.before;
        const afterIndexes = resize.trackGroups.after;
        const beforeStartSize = beforeIndexes.reduce((sum, index) => sum + (sizes[index] || 0), 0) || 1;
        const afterStartSize = afterIndexes.reduce((sum, index) => sum + (sizes[index] || 0), 0) || 1;
        const beforeSize = Math.max(1, beforeStartSize + delta);
        const afterSize = Math.max(1, afterStartSize - delta);
        const beforeScale = beforeSize / beforeStartSize;
        const afterScale = afterSize / afterStartSize;
        const candidateWeights = weights.slice();
        beforeIndexes.forEach(index => {
            candidateWeights[index] = Math.max(0.01, weights[index] * beforeScale);
        });
        afterIndexes.forEach(index => {
            candidateWeights[index] = Math.max(0.01, weights[index] * afterScale);
        });

        if (!validateResizeCandidate(resize.axis, candidateWeights)) {
            return;
        }

        if (isVertical) {
            splitColumnWeights = candidateWeights;
            resize.handle.style.left = `${event.clientX}px`;
        } else {
            splitRowWeights = candidateWeights;
            resize.handle.style.top = `${event.clientY}px`;
        }
        applySplitSlotGeometry({ fit: false, renderHandles: false });
        scheduleActiveGridResizeFits();
    }

    function finishGridResize(event) {
        const resize = activeGridResize;
        if (!resize || event.pointerId !== resize.pointerId) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();

        const affectedIndices = resize.affectedIndices.slice();
        resize.handle?.releasePointerCapture?.(event.pointerId);
        clearActiveGridResize();
        applySplitSlotGeometry({ fit: true });
        redrawAttachedTerminals(affectedIndices, { forceResize: true });
    }

    function cloneSplitRect(rect) {
        return { x: rect.x, y: rect.y, w: rect.w, h: rect.h };
    }

    function cloneSplitAncestors(ancestors = []) {
        return ancestors.map(ancestor => ({
            splitId: ancestor.splitId,
            branch: ancestor.branch,
            parentRect: cloneSplitRect(ancestor.parentRect),
            branchRect: cloneSplitRect(ancestor.branchRect),
        }));
    }

    function makeSplitRectId() {
        return `sr-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    }

    function makeSplitLeaf(rect, ancestors = []) {
        return normalizeSplitRectMetadata({
            id: makeSplitRectId(),
            originSlot: rect.originSlot ?? 0,
            splitId: rect.splitId || '',
            splitRole: rect.splitRole || '',
            isSplitChild: Boolean(rect.isSplitChild),
            parentRect: rect.parentRect ? cloneSplitRect(rect.parentRect) : null,
            ...cloneSplitRect(rect),
            ancestors: cloneSplitAncestors(ancestors),
        });
    }

    function normalizeSplitRectMetadata(rect) {
        const ancestors = cloneSplitAncestors(rect.ancestors || []);
        const lastAncestor = ancestors[ancestors.length - 1];
        if (!lastAncestor) {
            return {
                ...rect,
                splitId: '',
                splitRole: '',
                isSplitChild: false,
                parentRect: null,
                ancestors,
            };
        }

        return {
            ...rect,
            splitId: lastAncestor.splitId || '',
            splitRole: lastAncestor.branch === 'b' ? 'secondary' : 'primary',
            isSplitChild: lastAncestor.branch === 'b',
            parentRect: cloneSplitRect(lastAncestor.parentRect),
            ancestors,
        };
    }

    function getBaseLayoutSlots(count, layoutClass = '') {
        if (count <= 0) {
            return { columns: 1, rows: 1, slots: [] };
        }
        if (count === 1) {
            return {
                columns: 1,
                rows: 1,
                slots: [{ col: 1, row: 1, colSpan: 1, rowSpan: 1 }],
            };
        }
        if (count === 2) {
            if (layoutClass.includes('horizontal')) {
                return {
                    columns: 1,
                    rows: 2,
                    slots: [
                        { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
                        { col: 1, row: 2, colSpan: 1, rowSpan: 1 },
                    ],
                };
            }
            return {
                columns: 2,
                rows: 1,
                slots: [
                    { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
                    { col: 2, row: 1, colSpan: 1, rowSpan: 1 },
                ],
            };
        }
        if (count === 3 && layoutClass.includes('layout-3-split')) {
            return {
                columns: 3,
                rows: 2,
                slots: [
                    { col: 1, row: 1, colSpan: 2, rowSpan: 1 },
                    { col: 1, row: 2, colSpan: 2, rowSpan: 1 },
                    { col: 3, row: 1, colSpan: 1, rowSpan: 2 },
                ],
            };
        }
        if (count === 3 && layoutClass.includes('horizontal')) {
            return {
                columns: 1,
                rows: 3,
                slots: [
                    { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
                    { col: 1, row: 2, colSpan: 1, rowSpan: 1 },
                    { col: 1, row: 3, colSpan: 1, rowSpan: 1 },
                ],
            };
        }
        if (count === 3) {
            return {
                columns: 3,
                rows: 1,
                slots: [
                    { col: 1, row: 1, colSpan: 1, rowSpan: 1 },
                    { col: 2, row: 1, colSpan: 1, rowSpan: 1 },
                    { col: 3, row: 1, colSpan: 1, rowSpan: 1 },
                ],
            };
        }

        const metrics = getGridMetrics(count) || { columns: 2, rows: 2 };
        return {
            columns: metrics.columns,
            rows: metrics.rows,
            slots: Array.from({ length: count }, (_, index) => ({
                col: 1 + (index % metrics.columns),
                row: 1 + Math.floor(index / metrics.columns),
                colSpan: 1,
                rowSpan: 1,
            })),
        };
    }

    function fixedLayoutSlotRects(count, layoutClass = '') {
        originalSplitSlotCount = Math.max(originalSplitSlotCount, count);
        if (count === 1) {
            return [makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 4, h: 2 })];
        }
        if (count === 2 && layoutClass.includes('horizontal')) {
            return [
                makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 4, h: 2 }),
                makeSplitLeaf({ originSlot: 1, x: 1, y: 3, w: 4, h: 2 }),
            ];
        }
        if (count === 2) {
            return [
                makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 2, h: 2 }),
                makeSplitLeaf({ originSlot: 1, x: 3, y: 1, w: 2, h: 2 }),
            ];
        }

        const base = getBaseLayoutSlots(count, layoutClass);
        return base.slots.map((slot, index) => makeSplitLeaf({
            originSlot: index,
            x: 1 + (slot.col - 1) * 2,
            y: 1 + (slot.row - 1) * 2,
            w: slot.colSpan * 2,
            h: slot.rowSpan * 2,
        }));
    }

    function clearSplitSlotGeometry() {
        clearActiveGridResize();
        clearResizeHandles();
        splitSlotRects = null;
        splitColumnWeights = null;
        splitRowWeights = null;
        const grid = document.getElementById('terminalsGrid');
        if (!grid) {
            return;
        }
        grid.style.removeProperty('--split-grid-columns');
        grid.style.removeProperty('--split-grid-rows');
        grid.style.gridTemplateColumns = '';
        grid.style.gridTemplateRows = '';
        Array.from(grid.children).forEach(card => {
            card.style.gridColumn = '';
            card.style.gridRow = '';
        });
    }

    function ensureSplitSlotRects() {
        const grid = document.getElementById('terminalsGrid');
        if (!grid) {
            return [];
        }
        if (!Array.isArray(splitSlotRects) || splitSlotRects.length !== grid.children.length) {
            splitSlotRects = fixedLayoutSlotRects(grid.children.length, grid.className || '');
        }
        return splitSlotRects;
    }

    function applySplitSlotGeometry({ fit = true, renderHandles = true } = {}) {
        const grid = document.getElementById('terminalsGrid');
        if (!grid || !Array.isArray(splitSlotRects) || splitSlotRects.length !== grid.children.length) {
            return false;
        }

        const gridColumns = Math.max(1, ...splitSlotRects.map(rect => rect.x + rect.w - 1));
        const gridRows = Math.max(1, ...splitSlotRects.map(rect => rect.y + rect.h - 1));
        grid.className = 'layout-split-local';
        grid.style.removeProperty('--grid-columns');
        grid.style.removeProperty('--grid-rows');
        grid.style.setProperty('--split-grid-columns', String(gridColumns));
        grid.style.setProperty('--split-grid-rows', String(gridRows));
        initializeSplitTrackWeights(gridColumns, gridRows);
        applySplitTrackTemplates(grid);

        Array.from(grid.children).forEach((card, visualIndex) => {
            const rect = splitSlotRects[visualIndex];
            card.style.gridColumn = `${rect.x} / span ${rect.w}`;
            card.style.gridRow = `${rect.y} / span ${rect.h}`;
            if (fit) {
                const slotIndex = Number(card.dataset.slot);
                if (Number.isInteger(slotIndex)) {
                    scheduleFit(slotIndex);
                }
            }
        });
        updateAllSplitButtonStates();
        if (renderHandles) {
            renderResizeHandles();
        }
        return true;
    }

    function applyWorkspaceLayoutSnapshot(snapshot, expectedCount) {
        if (
            !snapshot
            || !Array.isArray(snapshot.split_slot_rects)
            || snapshot.split_slot_rects.length !== expectedCount
        ) {
            return false;
        }

        const rects = snapshot.split_slot_rects.map((rect, index) => normalizeSplitRectMetadata({
            id: makeSplitRectId(),
            originSlot: Number.isInteger(Number(rect.originSlot)) ? Number(rect.originSlot) : index,
            x: rect.x,
            y: rect.y,
            w: rect.w,
            h: rect.h
        }));
        const size = getSplitGridSize(rects);
        originalSplitSlotCount = Math.max(
            1,
            Number(snapshot.original_split_slot_count || expectedCount) || expectedCount
        );
        splitSlotRects = cloneSplitSlotRects(rects);
        splitColumnWeights = normalizeSplitTrackWeights(snapshot.split_column_weights, size.columns);
        splitRowWeights = normalizeSplitTrackWeights(snapshot.split_row_weights, size.rows);
        return applySplitSlotGeometry({ fit: false });
    }

    function estimatePaneCharacters(index, axis = '') {
        const terminal = terminals[index];
        const term = terminal?.term;
        const wrapper = document.getElementById(`tw-${index}`);
        const card = document.getElementById(`tc-${index}`);
        const wrapperRect = wrapper?.getBoundingClientRect();
        const cardRect = card?.getBoundingClientRect();
        const headerRect = card?.querySelector('.terminal-header')?.getBoundingClientRect();

        let cols = Number(term?.cols || 0);
        let rows = Number(term?.rows || 0);
        const dimensions = term?._core?._renderService?.dimensions?.css?.cell || {};
        const cellWidth = Number(dimensions.width || 8);
        const cellHeight = Number(dimensions.height || 17);

        if (!cols && wrapperRect?.width) {
            cols = Math.floor(wrapperRect.width / cellWidth);
        }
        if (!rows && wrapperRect?.height) {
            rows = Math.floor(wrapperRect.height / cellHeight);
        }

        if (axis === 'vertical' && cols > 0) {
            cols = Math.floor(cols / 2);
        }
        if (axis === 'horizontal' && rows > 0) {
            const duplicatedHeaderHeight = headerRect?.height || 34;
            const availableHeight = cardRect?.height
                ? Math.max(0, (cardRect.height / 2) - duplicatedHeaderHeight)
                : (wrapperRect?.height || 0) / 2;
            rows = Math.floor(availableHeight / cellHeight) || Math.floor(rows / 2);
        }

        return { cols, rows };
    }

    function getSplitCandidates(index, rect) {
        if (
            window.innerWidth <= 700
            || terminals.length >= getSplitPaneLimit()
            || (rect.splitId && !isRecursiveSplitLayout())
            || isExplorerSession(terminals[index]?._session)
        ) {
            return [];
        }

        const vertical = estimatePaneCharacters(index, 'vertical');
        const horizontal = estimatePaneCharacters(index, 'horizontal');
        const candidates = [];
        if (rect.w >= 2 && vertical.cols >= MIN_SPLIT_COLS && vertical.rows >= MIN_SPLIT_ROWS) {
            candidates.push('vertical');
        }
        if (rect.h >= 2 && horizontal.cols >= MIN_SPLIT_COLS && horizontal.rows >= MIN_SPLIT_ROWS) {
            candidates.push('horizontal');
        }
        return candidates;
    }

    function chooseSplitAxis(index, rect) {
        const candidates = getSplitCandidates(index, rect);
        if (candidates.length === 0) {
            return '';
        }
        const grid = document.getElementById('terminalsGrid');
        if (
            grid?.classList.contains('layout-2-vertical')
            && !rect.splitId
            && !rect.ancestors?.length
        ) {
            return candidates.includes('horizontal') ? 'horizontal' : '';
        }
        const card = document.getElementById(`tc-${index}`);
        const bounds = card?.getBoundingClientRect();
        const preferred = bounds && bounds.height > bounds.width ? 'horizontal' : 'vertical';
        return candidates.includes(preferred) ? preferred : '';
    }

    function splitSlotRect(rect, axis) {
        const splitId = makeSplitRectId();
        const parentRect = cloneSplitRect(rect);
        const ancestors = cloneSplitAncestors(rect.ancestors || []);
        if (axis === 'vertical') {
            const firstWidth = Math.floor(rect.w / 2);
            const firstRect = { x: rect.x, y: rect.y, w: firstWidth, h: rect.h };
            const secondRect = { x: rect.x + firstWidth, y: rect.y, w: rect.w - firstWidth, h: rect.h };
            return [
                makeSplitLeaf({
                    ...firstRect,
                    originSlot: rect.originSlot,
                    splitId,
                    splitRole: 'primary',
                    parentRect,
                }, [
                    ...ancestors,
                    { splitId, branch: 'a', parentRect, branchRect: firstRect },
                ]),
                makeSplitLeaf({
                    ...secondRect,
                    originSlot: rect.originSlot,
                    splitId,
                    splitRole: 'secondary',
                    isSplitChild: true,
                    parentRect,
                }, [
                    ...ancestors,
                    { splitId, branch: 'b', parentRect, branchRect: secondRect },
                ]),
            ];
        }

        const firstHeight = Math.floor(rect.h / 2);
        const firstRect = { x: rect.x, y: rect.y, w: rect.w, h: firstHeight };
        const secondRect = { x: rect.x, y: rect.y + firstHeight, w: rect.w, h: rect.h - firstHeight };
        return [
            makeSplitLeaf({
                ...firstRect,
                originSlot: rect.originSlot,
                splitId,
                splitRole: 'primary',
                parentRect,
            }, [
                ...ancestors,
                { splitId, branch: 'a', parentRect, branchRect: firstRect },
            ]),
            makeSplitLeaf({
                ...secondRect,
                originSlot: rect.originSlot,
                splitId,
                splitRole: 'secondary',
                isSplitChild: true,
                parentRect,
            }, [
                ...ancestors,
                { splitId, branch: 'b', parentRect, branchRect: secondRect },
            ]),
        ];
    }

    function scaleSplitValue(value, sourceStart, sourceSize, targetStart, targetSize) {
        if (!sourceSize) {
            return targetStart;
        }
        return targetStart + Math.round(((value - sourceStart) / sourceSize) * targetSize);
    }

    function transformSplitRect(rect, fromRect, toRect) {
        const x1 = scaleSplitValue(rect.x, fromRect.x, fromRect.w, toRect.x, toRect.w);
        const y1 = scaleSplitValue(rect.y, fromRect.y, fromRect.h, toRect.y, toRect.h);
        const x2 = scaleSplitValue(rect.x + rect.w, fromRect.x, fromRect.w, toRect.x, toRect.w);
        const y2 = scaleSplitValue(rect.y + rect.h, fromRect.y, fromRect.h, toRect.y, toRect.h);
        return {
            x: x1,
            y: y1,
            w: Math.max(1, x2 - x1),
            h: Math.max(1, y2 - y1),
        };
    }

    function transformSplitAncestor(ancestor, fromRect, toRect) {
        return {
            ...ancestor,
            parentRect: transformSplitRect(ancestor.parentRect, fromRect, toRect),
            branchRect: transformSplitRect(ancestor.branchRect, fromRect, toRect),
        };
    }

    function buildCloseSplitPlan(index) {
        const grid = document.getElementById('terminalsGrid');
        const card = document.getElementById(`tc-${index}`);
        const visualIndex = grid && card ? Array.from(grid.children).indexOf(card) : -1;
        const rect = visualIndex >= 0 && Array.isArray(splitSlotRects) ? splitSlotRects[visualIndex] : null;
        const targetAncestor = rect?.ancestors?.[rect.ancestors.length - 1];
        if (!grid || !targetAncestor) {
            return null;
        }

        const rectsBySessionId = {};
        Array.from(grid.children).forEach((paneCard, paneVisualIndex) => {
            const slotIndex = Number(paneCard.dataset.slot);
            const sessionId = Number.isInteger(slotIndex) ? sessionIds[slotIndex] : '';
            const paneRect = splitSlotRects[paneVisualIndex];
            if (!sessionId || !paneRect) {
                return;
            }

            const splitIndex = paneRect.ancestors?.findIndex(
                ancestor => ancestor.splitId === targetAncestor.splitId
            );
            if (splitIndex === undefined || splitIndex < 0) {
                rectsBySessionId[sessionId] = cloneSplitSlotRects([paneRect])[0];
                return;
            }

            const splitAncestor = paneRect.ancestors[splitIndex];
            if (splitAncestor.branch === targetAncestor.branch) {
                return;
            }

            const transformedAncestors = paneRect.ancestors
                .map((ancestor, ancestorIndex) => (
                    ancestorIndex > splitIndex
                        ? transformSplitAncestor(ancestor, splitAncestor.branchRect, splitAncestor.parentRect)
                        : ancestor
                ))
                .filter((_, ancestorIndex) => ancestorIndex !== splitIndex);
            const transformedRect = normalizeSplitRectMetadata({
                ...transformSplitRect(paneRect, splitAncestor.branchRect, splitAncestor.parentRect),
                id: paneRect.id || makeSplitRectId(),
                originSlot: paneRect.originSlot,
                ancestors: transformedAncestors,
            });
            rectsBySessionId[sessionId] = cloneSplitSlotRects([transformedRect])[0];
        });

        const sessionIdsToClose = Array.from(grid.children)
            .map((paneCard, paneVisualIndex) => {
                const slotIndex = Number(paneCard.dataset.slot);
                const sessionId = Number.isInteger(slotIndex) ? sessionIds[slotIndex] : '';
                const paneRect = splitSlotRects[paneVisualIndex];
                const splitAncestor = paneRect?.ancestors?.find(
                    ancestor => ancestor.splitId === targetAncestor.splitId
                );
                return splitAncestor?.branch === targetAncestor.branch ? sessionId : '';
            })
            .filter(Boolean);

        if (sessionIdsToClose.length === 0 || Object.keys(rectsBySessionId).length === 0) {
            return null;
        }

        return { sessionIdsToClose, rectsBySessionId, originalSplitSlotCount };
    }

    function splitRectArea(rect) {
        return Math.max(0, Number(rect?.w || 0)) * Math.max(0, Number(rect?.h || 0));
    }

    function splitRectUnion(left, right) {
        const x1 = Math.min(left.x, right.x);
        const y1 = Math.min(left.y, right.y);
        const x2 = Math.max(left.x + left.w, right.x + right.w);
        const y2 = Math.max(left.y + left.h, right.y + right.h);
        return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
    }

    function splitRectsOverlap(left, right) {
        return left.x < right.x + right.w
            && left.x + left.w > right.x
            && left.y < right.y + right.h
            && left.y + left.h > right.y;
    }

    function sharedBorderLength(left, right) {
        if (!left || !right) {
            return 0;
        }

        let longest = 0;
        if (left.x + left.w === right.x || right.x + right.w === left.x) {
            longest = Math.max(
                longest,
                Math.min(left.y + left.h, right.y + right.h) - Math.max(left.y, right.y)
            );
        }
        if (left.y + left.h === right.y || right.y + right.h === left.y) {
            longest = Math.max(
                longest,
                Math.min(left.x + left.w, right.x + right.w) - Math.max(left.x, right.x)
            );
        }
        return Math.max(0, longest);
    }

    function canAbsorbClosedRect(candidateRect, closedRect, otherRects) {
        const union = splitRectUnion(candidateRect, closedRect);
        if (splitRectArea(union) !== splitRectArea(candidateRect) + splitRectArea(closedRect)) {
            return false;
        }
        return !otherRects.some(rect => splitRectsOverlap(union, rect));
    }

    function coveredIntervalLength(intervals) {
        const sorted = intervals
            .map(interval => ({
                start: Math.min(interval.start, interval.end),
                end: Math.max(interval.start, interval.end),
            }))
            .filter(interval => interval.end > interval.start)
            .sort((left, right) => left.start - right.start || left.end - right.end);
        let covered = 0;
        let cursor = null;
        sorted.forEach(interval => {
            if (!cursor || interval.start > cursor.end) {
                covered += interval.end - interval.start;
                cursor = { ...interval };
                return;
            }
            if (interval.end > cursor.end) {
                covered += interval.end - cursor.end;
                cursor.end = interval.end;
            }
        });
        return covered;
    }

    function terminalCloseContacts(closedRect, entry) {
        const rect = entry.rect;
        const contacts = [];
        const yStart = Math.max(closedRect.y, rect.y);
        const yEnd = Math.min(closedRect.y + closedRect.h, rect.y + rect.h);
        const xStart = Math.max(closedRect.x, rect.x);
        const xEnd = Math.min(closedRect.x + closedRect.w, rect.x + rect.w);
        if (rect.x + rect.w === closedRect.x && yEnd > yStart) {
            contacts.push({ ...entry, side: 'left', sharedBorder: yEnd - yStart, start: yStart, end: yEnd });
        }
        if (rect.x === closedRect.x + closedRect.w && yEnd > yStart) {
            contacts.push({ ...entry, side: 'right', sharedBorder: yEnd - yStart, start: yStart, end: yEnd });
        }
        if (rect.y + rect.h === closedRect.y && xEnd > xStart) {
            contacts.push({ ...entry, side: 'top', sharedBorder: xEnd - xStart, start: xStart, end: xEnd });
        }
        if (rect.y === closedRect.y + closedRect.h && xEnd > xStart) {
            contacts.push({ ...entry, side: 'bottom', sharedBorder: xEnd - xStart, start: xStart, end: xEnd });
        }
        return contacts;
    }

    function findTerminalCloseNeighbor(closedRect, candidates) {
        return candidates
            .map(candidate => ({
                ...candidate,
                sharedBorder: sharedBorderLength(closedRect, candidate.rect),
            }))
            .filter(candidate => candidate.sharedBorder > 0)
            .sort((left, right) => (
                right.sharedBorder - left.sharedBorder
                || left.visualIndex - right.visualIndex
            ))[0] || null;
    }

    function terminalCloseSideGroups(closedRect, entries) {
        const sideLengths = {
            left: closedRect.h,
            right: closedRect.h,
            top: closedRect.w,
            bottom: closedRect.w,
        };
        const groupsBySide = new Map();
        entries.flatMap(entry => terminalCloseContacts(closedRect, entry)).forEach(contact => {
            if (!groupsBySide.has(contact.side)) {
                groupsBySide.set(contact.side, []);
            }
            groupsBySide.get(contact.side).push(contact);
        });

        return Array.from(groupsBySide.entries()).map(([side, contacts]) => {
            const coverage = coveredIntervalLength(contacts);
            return {
                side,
                entries: contacts,
                coverage,
                sideLength: sideLengths[side],
                totalSharedBorder: contacts.reduce((total, contact) => total + contact.sharedBorder, 0),
                firstVisualIndex: Math.min(...contacts.map(contact => contact.visualIndex)),
            };
        });
    }

    function expandRectIntoClosedSide(rect, closedRect, side) {
        if (side === 'left') {
            return { ...rect, w: (closedRect.x + closedRect.w) - rect.x, ancestors: [] };
        }
        if (side === 'right') {
            return { ...rect, x: closedRect.x, w: (rect.x + rect.w) - closedRect.x, ancestors: [] };
        }
        if (side === 'top') {
            return { ...rect, h: (closedRect.y + closedRect.h) - rect.y, ancestors: [] };
        }
        if (side === 'bottom') {
            return { ...rect, y: closedRect.y, h: (rect.y + rect.h) - closedRect.y, ancestors: [] };
        }
        return rect;
    }

    /* Expand a chosen subset of side contacts into the closed rect and return
       the resulting rects only when the layout stays gap-free (area invariant)
       and overlap-free; otherwise null so the caller can try another subset. */
    function terminalCloseRectsForExpandingContacts(plan, side, contactsToExpand) {
        const expandingSessionIds = new Set(contactsToExpand.map(entry => entry.sessionId));
        const nextEntries = plan.remainingEntries.map(entry => ({
            ...entry,
            rect: expandingSessionIds.has(entry.sessionId)
                ? expandRectIntoClosedSide(entry.rect, plan.closedRect, side)
                : entry.rect,
        }));
        for (let leftIndex = 0; leftIndex < nextEntries.length; leftIndex += 1) {
            for (let rightIndex = leftIndex + 1; rightIndex < nextEntries.length; rightIndex += 1) {
                if (splitRectsOverlap(nextEntries[leftIndex].rect, nextEntries[rightIndex].rect)) {
                    return null;
                }
            }
        }

        const previousArea = plan.remainingEntries.reduce((total, entry) => total + splitRectArea(entry.rect), 0);
        const nextArea = nextEntries.reduce((total, entry) => total + splitRectArea(entry.rect), 0);
        if (nextArea !== previousArea + splitRectArea(plan.closedRect)) {
            return null;
        }

        const rectsBySessionId = {};
        nextEntries.forEach(entry => {
            rectsBySessionId[entry.sessionId] = cloneSplitSlotRects([entry.rect])[0];
        });
        return rectsBySessionId;
    }

    function buildTerminalCloseRectsForSideGroup(plan, sideGroup) {
        if (!sideGroup || sideGroup.coverage < sideGroup.sideLength) {
            return null;
        }

        /* Prefer expanding only the single contact with the greatest shared
           border, so closing a pane never resizes more neighbours than the
           geometry requires (ISSUE-2026-022). Fall back to the full side group
           only when the single-pane expansion would leave a gap or overlap. */
        const rankedContacts = [...sideGroup.entries].sort((left, right) => (
            right.sharedBorder - left.sharedBorder
            || left.visualIndex - right.visualIndex
        ));
        const singleContact = rankedContacts[0];
        if (singleContact && sideGroup.entries.length > 1) {
            const single = terminalCloseRectsForExpandingContacts(plan, sideGroup.side, [singleContact]);
            if (single) {
                return single;
            }
        }
        return terminalCloseRectsForExpandingContacts(plan, sideGroup.side, sideGroup.entries);
    }

    function buildTerminalCloseRectsBySessionId(plan) {
        const neighbor = findTerminalCloseNeighbor(plan.closedRect, plan.remainingEntries);
        if (neighbor) {
            const otherRects = plan.remainingEntries
                .filter(entry => entry.sessionId !== neighbor.sessionId)
                .map(entry => entry.rect);
            if (canAbsorbClosedRect(neighbor.rect, plan.closedRect, otherRects)) {
                const rectsBySessionId = {};
                plan.remainingEntries.forEach(entry => {
                    const rect = entry.sessionId === neighbor.sessionId
                        ? {
                            ...entry.rect,
                            ...splitRectUnion(entry.rect, plan.closedRect),
                            ancestors: [],
                        }
                        : entry.rect;
                    rectsBySessionId[entry.sessionId] = cloneSplitSlotRects([rect])[0];
                });
                return rectsBySessionId;
            }
        }

        const sideGroups = terminalCloseSideGroups(plan.closedRect, plan.remainingEntries)
            .map(sideGroup => ({
                ...sideGroup,
                rectsBySessionId: buildTerminalCloseRectsForSideGroup(plan, sideGroup),
            }))
            .filter(sideGroup => sideGroup.rectsBySessionId);
        sideGroups.sort((left, right) => (
            right.totalSharedBorder - left.totalSharedBorder
            || left.entries.length - right.entries.length
            || left.firstVisualIndex - right.firstVisualIndex
        ));
        return sideGroups[0]?.rectsBySessionId || null;
    }

    function buildCloseTerminalPlan(index) {
        const grid = document.getElementById('terminalsGrid');
        const card = document.getElementById(`tc-${index}`);
        const sessionId = sessionIds[index];
        if (!grid || !card || !sessionId) {
            return null;
        }

        const cards = Array.from(grid.children);
        const visualIndex = cards.indexOf(card);
        if (visualIndex < 0) {
            return null;
        }

        const activeSessionIds = sessionIds.filter(Boolean);
        if (activeSessionIds.length <= 1) {
            return { sessionId, closeLastPane: true };
        }

        const rects = cloneSplitSlotRects(ensureSplitSlotRects());
        const closedRect = rects?.[visualIndex];
        if (!closedRect) {
            return null;
        }

        const remainingEntries = cards
            .map((paneCard, paneVisualIndex) => {
                const slotIndex = Number(paneCard.dataset.slot);
                const paneSessionId = Number.isInteger(slotIndex) ? sessionIds[slotIndex] : '';
                const rect = rects[paneVisualIndex];
                if (!paneSessionId || paneSessionId === sessionId || !rect) {
                    return null;
                }
                return {
                    sessionId: paneSessionId,
                    visualIndex: paneVisualIndex,
                    slotIndex,
                    rect,
                };
            })
            .filter(Boolean);

        if (remainingEntries.length === 0) {
            return { sessionId, closeLastPane: true };
        }

        return {
            sessionId,
            closeLastPane: false,
            closedRect,
            remainingEntries,
            originalSplitSlotCount,
        };
    }

    function updateSplitButtonState(index) {
        const button = document.getElementById(`tsplit-${index}`);
        if (!button) {
            return;
        }

        if (isExplorerSession(terminals[index]?._session) || isBrowserSession(terminals[index]?._session)) {
            button.hidden = true;
            button.disabled = true;
            return;
        }

        button.hidden = false;
        const grid = document.getElementById('terminalsGrid');
        const card = document.getElementById(`tc-${index}`);
        const visualIndex = grid && card ? Array.from(grid.children).indexOf(card) : -1;
        const rects = visualIndex >= 0 ? ensureSplitSlotRects() : [];
        const rect = rects[visualIndex];
        const axis = rect ? chooseSplitAxis(index, rect) : '';
        const splitLimit = getSplitPaneLimit();
        const disabledReason = window.innerWidth <= 700
            ? 'Splitting is disabled on narrow screens'
            : terminals.length >= splitLimit
                ? `Splitting is limited to ${splitLimit} terminal panes for this launcher layout`
                : 'This pane is already at the smallest split size';

        button.disabled = !axis;
        button.title = axis ? 'Split this terminal pane' : disabledReason;
        button.setAttribute('aria-label', button.title);
    }

    function updateUnsplitButtonState(index) {
        const button = document.getElementById(`tunsplit-${index}`);
        if (!button) {
            return;
        }

        if (isExplorerSession(terminals[index]?._session) || isBrowserSession(terminals[index]?._session)) {
            button.hidden = true;
            button.disabled = true;
            return;
        }

        const grid = document.getElementById('terminalsGrid');
        const card = document.getElementById(`tc-${index}`);
        const visualIndex = grid && card ? Array.from(grid.children).indexOf(card) : -1;
        const rect = visualIndex >= 0 && Array.isArray(splitSlotRects) ? splitSlotRects[visualIndex] : null;
        const canUnsplit = Boolean(rect?.ancestors?.length || rect?.splitId);
        button.hidden = !canUnsplit;
        button.disabled = !canUnsplit;
        button.title = canUnsplit ? 'Close this split pane' : 'This pane is not a split branch';
        button.setAttribute('aria-label', button.title);
    }

    function updateAllSplitButtonStates() {
        terminals.forEach((_, index) => {
            updateSplitButtonState(index);
            updateUnsplitButtonState(index);
        });
    }

    function hasMatchingSessionIds(existingIds, sessions) {
        if (!Array.isArray(existingIds) || !Array.isArray(sessions) || existingIds.length !== sessions.length) {
            return false;
        }

        return existingIds.every((sessionId, index) => sessionId === sessions[index]?.session_id);
    }

    function hasMatchingSessionViews(existingIds, existingTerminals, sessions) {
        if (!hasMatchingSessionIds(existingIds, sessions) || !Array.isArray(existingTerminals)) {
            return false;
        }

        return sessions.every((session, index) => (
            isExplorerPaneInstance(existingTerminals[index]) === isExplorerSession(session)
            && isBrowserPaneInstance(existingTerminals[index]) === isBrowserSession(session)
        ));
    }

    /* ─────────────────────────────────────────────
       Xterm factory
    ───────────────────────────────────────────── */
    function makeTerminal() {
        const _ds = document.body.dataset;
        /* The xterm theme follows the CSS custom properties (finding 7.3), so
           a tokens.css change restyles the terminal canvas automatically. */
        const _css = getComputedStyle(document.body);
        const cssColor = (name, fallback) => (_css.getPropertyValue(name) || '').trim() || fallback;
        const term = new Terminal({
            cursorBlink   : true,
            fontSize      : Number(_ds.terminalFontSize) || 14,
            fontFamily    : _ds.terminalFontFamily || 'Consolas, Monaco, "Courier New", monospace',
            copyOnSelect  : true,
            theme: {
                background          : cssColor('--t-terminal-bg', '#0d0d0d'),
                foreground          : cssColor('--t-terminal-fg', '#e0e0e0'),
                cursor              : cssColor('--t-terminal-cursor', '#00d9ff'),
                selectionBackground : cssColor('--t-terminal-selection', 'rgba(0,217,255,.25)')
            }
        });
        const fitAddon = new FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        /* Search + clickable links (finding 10.3). The addons are vendored like
           xterm itself; the typeof guards keep terminals working if a stale
           cached page misses one of the new vendor scripts. */
        let searchAddon = null;
        if (typeof SearchAddon !== 'undefined') {
            searchAddon = new SearchAddon.SearchAddon();
            term.loadAddon(searchAddon);
        }
        if (typeof WebLinksAddon !== 'undefined') {
            term.loadAddon(new WebLinksAddon.WebLinksAddon((event, uri) => {
                window.open(uri, '_blank', 'noopener');
            }));
        }
        term.attachCustomKeyEventHandler(event => {
            /* Hand Ctrl+Shift+F to the document-level search-overlay shortcut
               instead of letting xterm swallow it. */
            if (event.type === 'keydown'
                && (event.ctrlKey || event.metaKey)
                && event.shiftKey
                && !event.altKey
                && event.code === 'KeyF') {
                return false;
            }
            return true;
        });
        return { term, fitAddon, searchAddon };
    }

    function emitTerminalResize(index, force = false) {
        const sid = sessionIds[index];
        const terminal = terminals[index];
        if (!sid || !socket || !terminal?._attached || !terminal.term || !terminal.term.cols || !terminal.term.rows) {
            return;
        }

        const cols = terminal.term.cols;
        const rows = terminal.term.rows;

        if (cols < MIN_SPLIT_COLS || rows < MIN_SPLIT_ROWS) {
            return;
        }

        // Skip if dimensions haven't changed since last emit
        if (!force && terminal._lastCols === cols && terminal._lastRows === rows) {
            return;
        }
        terminal._lastCols = cols;
        terminal._lastRows = rows;

        socket.emit('terminal_resize', {
            session_id: sid,
            cols,
            rows
        });
    }

    function setTerminalRefreshState(index, refreshing) {
        setTerminalActionState(index, refreshing ? 'refresh' : '');
    }

    function setTerminalClearState(index, clearing) {
        setTerminalActionState(index, clearing ? 'clear' : '');
    }

    function setTerminalActionState(index, action = '') {
        const refreshButton = document.getElementById(`trefresh-${index}`);
        const explorerRefreshButton = document.getElementById(`explorer-refresh-${index}`);
        const clearButton = document.getElementById(`tclear-${index}`);
        const isBusy = Boolean(action);

        if (refreshButton) {
            refreshButton.disabled = isBusy;
            refreshButton.innerHTML = action === 'refresh' ? 'Refreshing…' : TERMINAL_REFRESH_ICON;
        }

        if (explorerRefreshButton) {
            explorerRefreshButton.disabled = isBusy;
            explorerRefreshButton.setAttribute('aria-busy', action === 'refresh' ? 'true' : 'false');
        }

        if (clearButton) {
            clearButton.disabled = isBusy;
            clearButton.innerHTML = action === 'clear' ? 'Clearing…' : TERMINAL_CLEAR_ICON;
        }

    }

    function getTerminalClearCommand(index) {
        const session = terminals[index]?._session;
        if (session?.mode === 'wsl' && session?.use_powershell) {
            return 'cls\r';
        }
        if (session?.mode === 'wsl' && !session?.use_wsl) {
            return 'cls\r';
        }
        return 'clear\r';
    }

    function flushPendingOutput(index) {
        const terminal = terminals[index];
        if (!terminal?._attached || !terminal.term || !terminal._pendingOutput) {
            return;
        }

        const pendingOutput = terminal._pendingOutput;
        terminal._pendingOutput = '';
        terminal.term.write(pendingOutput);
    }

    function fitTerminal(index) {
        const terminal = terminals[index];
        if (!terminal?._attached || !terminal.term || !terminal.fitAddon) {
            return false;
        }

        const wrapper = document.getElementById(`tw-${index}`);
        if (wrapper) {
            const rect = wrapper.getBoundingClientRect();
            if (rect.height < 20 || rect.width < 50) {
                // Layout not ready — reschedule through the debounced path
                terminal._fitReady = false;
                if (!terminal._fitRetries) terminal._fitRetries = 0;
                if (terminal._fitRetries < 10) {
                    terminal._fitRetries++;
                    scheduleFit(index);
                }
                return false;
            }
        }
        terminal._fitRetries = 0;

        terminal.fitAddon.fit();
        if (terminal.term.rows > 0) {
            terminal.term.refresh(0, terminal.term.rows - 1);
        }
        terminal._fitReady = true;
        emitTerminalResize(index);
        flushPendingOutput(index);
        // A fit reflows the buffer and drops the viewport; if a session-tab
        // switch is still restoring this pane's scroll, re-assert it here so the
        // async fits don't win the race and snap the terminal back to the top.
        applyTerminalViewportRestore(terminal);
        return true;
    }

    function scheduleFit(index) {
        const terminal = terminals[index];
        if (!terminal) return;

        if (terminal._fitTimer) {
            clearTimeout(terminal._fitTimer);
        }

        terminal._fitTimer = window.setTimeout(() => {
            terminal._fitTimer = null;
            window.requestAnimationFrame(() => fitTerminal(index));
        }, 60);
    }

    function waitForAnimationFrames(count = 1) {
        return new Promise(resolve => {
            function step() {
                count -= 1;
                if (count <= 0) {
                    resolve();
                    return;
                }
                window.requestAnimationFrame(step);
            }
            window.requestAnimationFrame(step);
        });
    }

    function waitForDelay(ms) {
        return new Promise(resolve => {
            window.setTimeout(resolve, ms);
        });
    }

    async function ensureTerminalReady(index, maxAttempts = 12) {
        const terminal = terminals[index];
        if (!terminal?._attached) {
            return false;
        }

        for (let attempt = 0; attempt < maxAttempts; attempt++) {
            if (fitTerminal(index)) {
                return true;
            }
            await waitForAnimationFrames(1);
        }

        return Boolean(terminals[index]?._fitReady);
    }

    async function ensureAttachedTerminalsReady(indices) {
        const uniqueIndices = [...new Set(indices)]
            .filter(index => Number.isInteger(index) && terminals[index]?._attached);
        if (uniqueIndices.length === 0) {
            return;
        }

        await waitForAnimationFrames(2);
        await Promise.all(uniqueIndices.map(index => ensureTerminalReady(index)));
        await waitForAnimationFrames(1);
        uniqueIndices.forEach(index => fitTerminal(index));
    }

    async function redrawAttachedTerminals(indices, { forceResize = false, isCurrent = null } = {}) {
        const uniqueIndices = [...new Set(indices)]
            .filter(index => Number.isInteger(index) && terminals[index]?._attached);
        if (uniqueIndices.length === 0) {
            return;
        }

        const stillCurrent = typeof isCurrent === 'function' ? isCurrent : () => true;
        if (!stillCurrent()) {
            return;
        }

        // Mirror the repaint users get from fullscreen toggles after a hidden
        // session grid becomes visible again and buffered output has replayed.
        await waitForAnimationFrames(2);
        if (!stillCurrent()) {
            return;
        }

        uniqueIndices.forEach(index => scheduleFit(index));
        await waitForAnimationFrames(2);
        if (!stillCurrent()) {
            return;
        }

        uniqueIndices.forEach(index => {
            const terminal = terminals[index];
            if (!terminal?._attached) {
                return;
            }

            fitTerminal(index);
            if (terminal.term.rows > 0) {
                terminal.term.refresh(0, terminal.term.rows - 1);
            }
            if (forceResize) {
                emitTerminalResize(index, true);
            }
        });
    }

    async function redrawAttachedTerminalsLikeFullscreen(indices, { isCurrent = null } = {}) {
        const uniqueIndices = [...new Set(indices)]
            .filter(index => Number.isInteger(index) && terminals[index]?._attached);
        if (uniqueIndices.length === 0) {
            return;
        }

        const stillCurrent = typeof isCurrent === 'function' ? isCurrent : () => true;

        async function redrawPass({ delayMs = 0, dispatchResize = false } = {}) {
            if (delayMs > 0) {
                await waitForDelay(delayMs);
            }
            if (!stillCurrent()) {
                return;
            }

            if (dispatchResize) {
                window.dispatchEvent(new Event('resize'));
                await waitForAnimationFrames(2);
                if (!stillCurrent()) {
                    return;
                }
            }

            await ensureAttachedTerminalsReady(uniqueIndices);
            if (!stillCurrent()) {
                return;
            }

            await redrawAttachedTerminals(uniqueIndices, {
                forceResize: true,
                isCurrent: stillCurrent
            });
        }

        await redrawPass();
        await redrawPass({ dispatchResize: true });
        await redrawPass({ delayMs: 90, dispatchResize: true });
    }

    async function refreshTerminalDisplay(index) {
        const terminal = terminals[index];
        const sessionId = sessionIds[index];
        if (!terminal) {
            return false;
        }

        setTerminalRefreshState(index, true);
        try {
            if (isBrowserSession(terminal._session)) {
                reloadBrowserPane(index);
                return false;
            }
            if (isExplorerSession(terminal._session)) {
                await refreshExplorerPane(index);
                return false;
            }
            logSessionWindowAction('Refreshing terminal display', {
                index,
                session_id: sessionId || null,
                attached: Boolean(terminal._attached)
            });

            terminal._pendingOutput = '';

            if (terminal._attached) {
                terminal.term.reset();
                terminal.term.clear();
                await ensureTerminalReady(index);
                emitTerminalResize(index, true);
            } else {
                attachTerminal(index);
                await ensureTerminalReady(index);
            }

            if (sessionId && socket) {
                socket.emit('leave_session', { session_id: sessionId });
                socket.emit('join_session', { session_id: sessionId });
                await redrawAttachedTerminals([index], { forceResize: true });
                return false;
            }

            if (terminal._attached && terminal.term.rows > 0) {
                terminal.term.refresh(0, terminal.term.rows - 1);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] refreshTerminalDisplay failed:', error);
        } finally {
            setTerminalRefreshState(index, false);
        }

        return false;
    }

    function reloadBrowserPane(index) {
        const frame = document.getElementById(`browser-frame-${index}`);
        if (!frame) {
            return false;
        }
        frame.src = getBrowserSessionUrl(terminals[index]?._session);
        return true;
    }

    function openBrowserPaneExternally(index) {
        const url = getBrowserSessionUrl(terminals[index]?._session);
        if (!url || url === 'about:blank') {
            return false;
        }
        window.open(url, '_blank', 'noopener');
        return true;
    }

    async function clearTerminalDisplay(index) {
        const terminal = terminals[index];
        const sessionId = sessionIds[index];
        if (!terminal) {
            return false;
        }

        if (isBrowserSession(terminal._session)) {
            return refreshTerminalDisplay(index);
        }

        if (isExplorerSession(terminal._session)) {
            await refreshTerminalDisplay(index);
            return false;
        }

        setTerminalClearState(index, true);
        try {
            logSessionWindowAction('Clearing terminal display', {
                index,
                session_id: sessionId || null,
                attached: Boolean(terminal._attached)
            });

            terminal._pendingOutput = '';
            terminal.term.reset();
            terminal.term.clear();

            if (terminal._attached) {
                await ensureTerminalReady(index);
                emitTerminalResize(index, true);
            }

            if (sessionId && socket && terminal._session?.status === 'connected') {
                const clearCommand = getTerminalClearCommand(index);
                socket.emit('clear_terminal_buffer', { session_id: sessionId });
                socket.emit('terminal_input', { session_id: sessionId, data: clearCommand });
            } else if (sessionId && socket) {
                socket.emit('clear_terminal_buffer', { session_id: sessionId });
            }
        } catch (error) {
            console.error('[GridVibe Sessions] clearTerminalDisplay failed:', error);
        } finally {
            setTerminalClearState(index, false);
        }

        return false;
    }

    /* Clear-button click target: with broadcast input on, clearing one terminal
       clears every plain terminal at once (notes 4) — the same fan-out
       broadcast applies to typed input. Explorer/browser panes (no `term`) keep
       their per-pane refresh behaviour and never trigger the fan-out. */
    function clearTerminalDisplayFromButton(index) {
        const source = terminals[index];
        if (broadcastInputActive && source?.term) {
            terminals.forEach((terminal, otherIndex) => {
                if (terminal?.term && sessionIds[otherIndex]) {
                    clearTerminalDisplay(otherIndex);
                }
            });
            return;
        }
        clearTerminalDisplay(index);
    }

    function clearDragState(){
        document.querySelectorAll('.terminal-container.dragging, .terminal-container.drag-target')
            .forEach(card => card.classList.remove('dragging', 'drag-target'));
        draggedCard = null;
    }

    function swapTerminalCards(cardA, cardB) {
        if (!cardA || !cardB || cardA === cardB || cardA.parentNode !== cardB.parentNode) {
            return;
        }

        const parent = cardA.parentNode;
        const placeholder = document.createElement('div');

        parent.replaceChild(placeholder, cardA);
        parent.replaceChild(cardA, cardB);
        parent.replaceChild(cardB, placeholder);

        if (splitSlotRects) {
            applySplitSlotGeometry({ fit: false });
        }

        const firstIndex = Number(cardA.dataset.slot);
        const secondIndex = Number(cardB.dataset.slot);
        if (Number.isInteger(firstIndex)) {
            scheduleFit(firstIndex);
        }
        if (Number.isInteger(secondIndex)) {
            scheduleFit(secondIndex);
        }
    }

    function wireCardDragAndDrop(card, header) {
        if (!card || !header) {
            return;
        }

        header.draggable = true;
        header.classList.add('draggable');

        header.addEventListener('dragstart', event => {
            if (activeGridResize) {
                event.preventDefault();
                return;
            }
            draggedCard = card;
            card.classList.add('dragging');
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', card.id);
            }
        });

        header.addEventListener('dragend', () => {
            clearDragState();
        });

        card.addEventListener('dragover', event => {
            if (!draggedCard || draggedCard === card) {
                return;
            }

            event.preventDefault();
            document.querySelectorAll('.terminal-container.drag-target')
                .forEach(node => {
                    if (node !== card) {
                        node.classList.remove('drag-target');
                    }
                });
            card.classList.add('drag-target');

            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = 'move';
            }
        });

        card.addEventListener('drop', event => {
            event.preventDefault();
            if (draggedCard && draggedCard !== card) {
                swapTerminalCards(draggedCard, card);
            }
            clearDragState();
        });

        card.addEventListener('dragleave', event => {
            const next = event.relatedTarget;
            if (next && card.contains(next)) {
                return;
            }
            card.classList.remove('drag-target');
        });
    }

    /* ─────────────────────────────────────────────
       Tear down current sessions before switching
    ───────────────────────────────────────────── */
    function teardownCurrentGrid() {
        _stopAllVoice();
        // Leave all active SocketIO rooms so the backend clears
        // client_joined_sessions — buffer will replay on re-join.
        if (socket) {
            sessionIds.forEach(sid => {
                if (sid) socket.emit('leave_session', { session_id: sid });
            });
        }
        clearFitTimers(terminals);
        disconnectObservers(resizeObservers);
        // Dispose xterm instances to free memory
        terminals.forEach(t => {
            if (t && t.term) {
                try { t.term.dispose(); } catch (_) {}
            }
        });
        clearSessionRoutes(sessionIds);
        cachedGroupViews.delete(visibleGroupId);
        clearSplitSlotGeometry();
        resizeObservers = [];
        terminals  = [];
        sessionIds = [];
        gridBuilt  = false;
        resetFocusedTerminal();
        visibleGroupId = '';
    }

    /* ─────────────────────────────────────────────
       Build the grid from a sessions array (once)
    ───────────────────────────────────────────── */
    function createPaneInstance(session) {
        if (isExplorerSession(session)) {
            return {
                _session: session,
                _paneType: 'explorer',
                _attached: false,
                _explorerTreeSidebarOpen: Boolean(session.explorer_tree_open),
                _explorerGitSidebarOpen: Boolean(session.explorer_git_open)
            };
        }
        if (isBrowserSession(session)) {
            return { _session: session, _paneType: 'browser', _attached: false };
        }
        return makeTerminal();
    }

    /* Header buttons must not start a card drag or steal terminal focus. */
    function wireCardButton(card, selector, onClick) {
        const button = card.querySelector(selector);
        if (!button) {
            return null;
        }
        button.draggable = false;
        button.addEventListener('mousedown', event => {
            event.preventDefault();
            event.stopPropagation();
        });
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            onClick(event);
        });
        return button;
    }

    function buildGrid(sessions, layout) {
        const grid  = document.getElementById('terminalsGrid');
        const count = sessions.length;

        if (grid.children.length > 0 || terminals.length > 0 || sessionIds.length > 0) {
            teardownCurrentGrid();
        }
        clearSplitSlotGeometry();
        originalSplitSlotCount = count;
        grid.className = getLayoutClass(count, layout);
        const gridMetrics = getGridMetrics(count);
        if (gridMetrics) {
            grid.style.setProperty('--grid-columns', String(gridMetrics.columns));
            grid.style.setProperty('--grid-rows', String(gridMetrics.rows));
        } else {
            grid.style.removeProperty('--grid-columns');
            grid.style.removeProperty('--grid-rows');
        }
        grid.innerHTML = '';

        sessions.forEach((session, i) => {
            const t = createPaneInstance(session);
            t._session = session;
            terminals.push(t);
            sessionIds.push(null);

            const card = buildPaneCard(session, i);
            wirePaneControls(card, i);
            grid.appendChild(card);
        });

        /* wire up terminal input events now that DOM elements exist */
        terminals.forEach((t, i) => wirePaneInputForwarding(t, i));

        document.getElementById('emptyState').classList.remove('visible');
        gridBuilt = true;
        visibleGroupId = activeGroupId;
        updateSessionChrome(count, activeGroupId);
        updateAllSplitButtonStates();
        renderResizeHandles();
    }

    /* Card DOM for one pane (terminal / explorer / browser); wiring happens
       in wirePaneControls so this stays a pure element builder. */
    function buildPaneCard(session, i) {
        const isExplorer = isExplorerSession(session);
        const isBrowser = isBrowserSession(session);
        const card = document.createElement('div');
        card.className = `terminal-container ${isExplorer ? 'explorer-pane' : ''} ${isBrowser ? 'browser-pane' : ''}`.trim();
        card.id = `tc-${i}`;
        card.dataset.slot = String(i);
        const explorerThemeKey = session.session_id || `${activeGroupId || 'group'}:${i}`;
        if (isExplorer) {
            const resolvedTheme = resolveInitialExplorerTheme(session, explorerThemeKey);
            card.dataset.explorerThemeKey = explorerThemeKey;
            card.dataset.explorerThemeSource = resolvedTheme.source;
            // Always set an explicit theme; leaving it unset makes the pane
            // inherit the global app theme's --explorer-* tokens (the flaky
            // case where a saved-dark pane rendered light under global light).
            card.dataset.explorerTheme = resolvedTheme.theme;
        }
        const sessionColour = tabColourForGroup(activeGroupId);
        card.style.setProperty('--session-color', sessionColour);
        card.style.setProperty('--session-color-dim', hexToRgba(sessionColour, 0.45));
        card.innerHTML = `
                <div class="terminal-header">
                    <div class="terminal-info">
                        <span class="terminal-name" id="tname-${i}">
                            ${escHtml(session.title || `Terminal ${i + 1}`)}
                        </span>
                        <span class="terminal-host" id="thost-${i}">
                            ${escHtml(session.host || '')}
                        </span>
                    </div>
                    <div class="terminal-meta">
                        <div class="terminal-status">
                            <div class="status-dot pending" id="tdot-${i}"></div>
                            <span id="tlabel-${i}">Pending</span>
                        </div>
                        <button
                            type="button"
                            class="terminal-action-btn"
                            id="trefresh-${i}"
                            data-terminal-refresh="${i}"
                            title="${isBrowser ? 'Reload browser pane' : 'Reset this terminal view and replay recent output'}"
                        >
                            ${TERMINAL_REFRESH_ICON}
                        </button>
                        ${!isBrowser ? `
                        <button
                            type="button"
                            class="terminal-action-btn terminal-mode-toggle-btn"
                            id="tmode-${i}"
                            data-session-mode-toggle="${i}"
                            title="${isExplorer ? 'Open terminal in current explorer directory' : 'Open file explorer for this terminal directory'}"
                            aria-label="${isExplorer ? 'Open terminal in current explorer directory' : 'Open file explorer for this terminal directory'}"
                        >
                            ${isExplorer ? TERMINAL_PROMPT_ICON : EXPLORER_MODE_FOLDER_ICON}
                        </button>
                        ` : ''}
                        ${session.mode === 'wsl' && !isExplorer ? `
                            <button
                                type="button"
                                class="terminal-action-btn terminal-browser-toggle-btn"
                                id="tbrowsermode-${i}"
                                data-session-browser-toggle="${i}"
                                title="${isBrowser ? 'Return to terminal' : 'Open browser preview'}"
                                aria-label="${isBrowser ? 'Return to terminal' : 'Open browser preview'}"
                            >
                                ${isBrowser ? TERMINAL_PROMPT_ICON : BROWSER_MODE_GLOBE_ICON}
                            </button>
                        ` : ''}
                        ${!isExplorer && !isBrowser ? `
                            <button
                                type="button"
                                class="terminal-action-btn terminal-split-btn"
                                id="tsplit-${i}"
                                data-terminal-split="${i}"
                                title="Split this terminal pane"
                                aria-label="Split this terminal pane"
                            >
                                ⊞
                            </button>
                            <button
                                type="button"
                                class="terminal-action-btn terminal-unsplit-btn"
                                id="tunsplit-${i}"
                                data-terminal-unsplit="${i}"
                                title="Close this split pane"
                                aria-label="Close this split pane"
                                hidden
                            >
                                ⊟
                            </button>
                        ` : ''}
                        ${isExplorer ? `
                            <button
                                type="button"
                                class="terminal-action-btn explorer-theme-btn"
                                id="texplorertheme-${i}"
                                data-explorer-theme-toggle="${i}"
                                title="Dark explorer theme"
                                aria-label="Dark explorer theme"
                                aria-pressed="false"
                            >
                                ${THEME_MOON_ICON}
                            </button>
                        ` : ''}
                        <button
                            type="button"
                            class="terminal-action-btn"
                            id="tclear-${i}"
                            data-terminal-clear="${i}"
                            title="Clear this terminal and purge its replay buffer"
                        >
                            ${TERMINAL_CLEAR_ICON}
                        </button>
                        <div class="voice-control" data-terminal-voice-control="${i}" ${_voiceServiceStatus.enabled ? '' : 'hidden'}>
                            <button
                                type="button"
                                class="terminal-action-btn voice-btn"
                                id="tvoice-${i}"
                                data-terminal-voice="${i}"
                                title="Voice input (click to start recording)"
                            >
                                ${VOICE_MIC_ICON}
                            </button>
                        </div>
                        <button
                            type="button"
                            class="terminal-action-btn terminal-close-btn"
                            id="tclose-${i}"
                            data-terminal-close="${i}"
                            title="Close this terminal pane"
                            aria-label="Close this terminal pane"
                        >
                            ×
                        </button>
                    </div>
                </div>
                <div class="terminal-wrapper" id="tw-${i}">
                    <div class="terminal-surface">
                        ${isBrowser ? `
                            <div class="browser-surface" id="browser-${i}">
                                <div class="browser-bar">
                                    <input class="browser-url-input" id="browser-url-${i}" data-browser-url="${i}" type="url" value="${escHtml(getBrowserSessionUrl(session))}" title="${escHtml(getBrowserSessionUrl(session))}" aria-label="Browser URL">
                                    <button type="button" class="browser-open-btn" id="browser-open-${i}" data-browser-open="${i}" title="Open this URL in an external browser tab">Open</button>
                                </div>
                                <iframe
                                    class="browser-frame"
                                    id="browser-frame-${i}"
                                    title="${escHtml(session.title || `Browser ${i + 1}`)}"
                                    src="${escHtml(getBrowserSessionUrl(session))}"
                                    sandbox="allow-downloads allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
                                ></iframe>
                            </div>
                        ` : (isExplorer ? `
                            <div class="explorer-surface" id="explorer-${i}">
                                <div class="explorer-bar">
                                     <button type="button" class="explorer-refresh" id="explorer-refresh-${i}" data-explorer-refresh="${i}" title="Refresh explorer" aria-label="Refresh explorer">${TERMINAL_REFRESH_ICON}</button>
                                     <button type="button" class="explorer-up" id="explorer-up-${i}" data-explorer-up="${i}" title="Go to parent directory">↑</button>
                                     <button type="button" class="explorer-tree-toggle" id="explorer-tree-toggle-${i}" data-explorer-tree-toggle="${i}" title="Show file tree" aria-label="Show file tree" aria-pressed="false">${EXPLORER_TREE_TOGGLE_ICON}</button>
                                     <button type="button" class="explorer-git-toggle" id="explorer-git-toggle-${i}" data-explorer-git-toggle="${i}" title="Show Git changes and history" aria-label="Show Git changes and history" aria-pressed="false">${EXPLORER_GIT_TOGGLE_ICON}</button>
                                     ${session.mode === 'ssh' ? '' : `<button type="button" class="explorer-os-open" id="explorer-os-open-${i}" data-explorer-os-open="${i}" title="Open current location in system file manager" aria-label="Open current location in system file manager">${EXPLORER_OS_OPEN_ICON}</button>`}
                                     <div class="explorer-git-summary" id="explorer-git-${i}" aria-live="polite"></div>
                                     <div class="explorer-path" id="explorer-path-${i}">${escHtml(session.directory || '')}</div>
                                     <div class="explorer-directory-search" id="explorer-directory-search-${i}"></div>
                                 </div>
                                 <div class="explorer-main" id="explorer-main-${i}">
                                     <aside class="explorer-sidebar" id="explorer-sidebar-${i}" hidden>
                                         <div class="explorer-tree-panel" id="explorer-tree-panel-${i}" hidden></div>
                                         <button type="button" class="explorer-sidebar-splitter" id="explorer-sidebar-splitter-${i}" data-explorer-sidebar-splitter="${i}" aria-label="Resize file tree and Git panels" hidden></button>
                                         <div class="explorer-git-panel" id="explorer-git-panel-${i}" hidden></div>
                                     </aside>
                                     <button type="button" class="explorer-sidebar-resizer" id="explorer-sidebar-resizer-${i}" data-explorer-sidebar-resizer="${i}" aria-label="Resize explorer sidebar" hidden></button>
                                     <div class="explorer-list" id="explorer-list-${i}">
                                         <div class="explorer-message">Loading directory...</div>
                                     </div>
                                 </div>
                             </div>
                        ` : `<div class="terminal-canvas" id="tcanvas-${i}"></div>`)}
                    </div>
                    <div class="placeholder" id="ph-${i}">
                        <div class="spinner"></div>
                        <span style="font-size:.78rem">${isBrowser ? 'Loading browser…' : (isExplorer ? 'Loading explorer…' : 'Connecting…')}</span>
                    </div>
                </div>
            `;
        return card;
    }

    /* Control wiring for one pane card (delegates to wireCardButton for the
       shared drag/focus-guard behaviour). */
    function wirePaneControls(card, i) {
        wireCardDragAndDrop(card, card.querySelector('.terminal-header'));
        wireCardButton(card, `[data-terminal-refresh="${i}"]`, () => refreshTerminalDisplay(i));
        wireCardButton(card, `[data-browser-open="${i}"]`, () => openBrowserPaneExternally(i));
        const browserUrlInput = card.querySelector(`[data-browser-url="${i}"]`);
        if (browserUrlInput) {
            browserUrlInput.addEventListener('keydown', event => {
                if (event.key !== 'Enter') {
                    return;
                }
                event.preventDefault();
                event.stopPropagation();
                navigateBrowserPane(i, browserUrlInput.value);
            });
            browserUrlInput.addEventListener('blur', () => {
                navigateBrowserPane(i, browserUrlInput.value);
            });
        }
        const browserFrame = card.querySelector(`#browser-frame-${i}`);
        if (browserFrame) {
            browserFrame.addEventListener('load', () => {
                document.getElementById(`ph-${i}`)?.remove();
            });
        }
        wireCardButton(card, `[data-session-browser-toggle="${i}"]`, () => switchSessionBrowserMode(i));
        wireCardButton(card, `[data-session-mode-toggle="${i}"]`, () => switchSessionPaneMode(i));
        wireCardButton(card, `[data-terminal-split="${i}"]`, () => splitTerminalPane(i));
        wireCardButton(card, `[data-terminal-unsplit="${i}"]`, () => closeSplitPane(i));
        const explorerThemeButton = wireCardButton(
            card, `[data-explorer-theme-toggle="${i}"]`, () => toggleExplorerTheme(i)
        );
        if (explorerThemeButton) {
            updateExplorerThemeButton(explorerThemeButton, card.dataset.explorerTheme || 'dark');
        }
        wireCardButton(card, `[data-terminal-clear="${i}"]`, () => clearTerminalDisplayFromButton(i));
        wireCardButton(card, `[data-terminal-close="${i}"]`, () => closeTerminalPane(i));
        wireCardButton(card, `[data-terminal-voice="${i}"]`, event => {
            _voiceLog('Mic button clicked', {
                terminalIndex: i,
                sessionId: sessionIds[i] || null,
                recording: Boolean(_voiceState[i]?.recording),
                activeIndex: _voiceActiveIndex,
                isTrusted: event.isTrusted
            });
            _toggleVoice(i);
        });
        _wireVoiceHoldToTalk(card, i);
        _syncVoiceControls(i);
        wireCardButton(card, `[data-explorer-refresh="${i}"]`, () => refreshTerminalDisplay(i));
        wireCardButton(card, `[data-explorer-up="${i}"]`, () => {
            const pane = terminals[i];
            const targetPath = pane?._explorerMode === 'file'
                ? (pane._explorerPath || '')
                : (pane?._explorerParentPath || '');
            loadExplorerPane(i, targetPath);
        });
        wireCardButton(card, `[data-explorer-git-toggle="${i}"]`, () => toggleExplorerGitSidebar(i));
        wireCardButton(card, `[data-explorer-tree-toggle="${i}"]`, () => toggleExplorerTreeSidebar(i));
        wireCardButton(card, `[data-explorer-os-open="${i}"]`, () => revealExplorerInOs(i));
    }

    /* Forward keystrokes and pointer focus for a terminal pane once its DOM
       elements exist. */
    /* ── Broadcast input (finding 10.4) ──
       When active, keystrokes typed into one plain terminal pane are mirrored
       to every other plain terminal pane in the group (explorer/browser panes
       are skipped — they have no terminal). Frontend-only: the backend's
       terminal_input handler already targets one session per event. */
    let broadcastInputActive = false;
    let _broadcastIdleTimer = null;
    const BROADCAST_IDLE_TIMEOUT_MS = 10 * 60 * 1000;

    function _noteBroadcastActivity() {
        clearTimeout(_broadcastIdleTimer);
        _broadcastIdleTimer = setTimeout(() => setBroadcastInput(false), BROADCAST_IDLE_TIMEOUT_MS);
    }

    function setBroadcastInput(active) {
        broadcastInputActive = Boolean(active);
        const button = document.getElementById('broadcastBtn');
        if (button) {
            button.classList.toggle('active', broadcastInputActive);
            button.setAttribute('aria-pressed', broadcastInputActive ? 'true' : 'false');
            const label = broadcastInputActive
                ? 'Broadcast typing is on — keystrokes go to every terminal pane'
                : 'Broadcast typing to all terminal panes';
            button.title = label;
            button.setAttribute('aria-label', label);
        }
        document.getElementById('terminalsGrid')?.classList.toggle('broadcast-input', broadcastInputActive);
        if (broadcastInputActive) {
            _noteBroadcastActivity();
            /* Enabling broadcast should let the user start typing immediately —
               focus a terminal now so keystrokes are captured without first
               clicking a pane (ISSUE-2026-026 follow-up). */
            focusActiveOrDefaultTerminal();
        } else {
            clearTimeout(_broadcastIdleTimer);
        }
    }

    function toggleBroadcastInput() {
        setBroadcastInput(!broadcastInputActive);
    }

    /* Mirror input into every *other* plain terminal pane while broadcast typing
       is on (explorer/browser panes have no `term` and are skipped). Shared by
       keyboard forwarding and committed voice transcripts (ISSUE-2026-026) so a
       single filter governs both. */
    function broadcastInputToPeers(sourceIndex, data) {
        if (!broadcastInputActive || !socket) {
            return;
        }
        _noteBroadcastActivity();
        sessionIds.forEach((otherSid, otherIndex) => {
            if (otherIndex === sourceIndex || !otherSid || !terminals[otherIndex]?.term) {
                return;
            }
            socket.emit('terminal_input', { session_id: otherSid, data });
        });
    }

    /* Active-terminal selection (ISSUE-2026-025). The highlight is driven by
       *real DOM keyboard focus* — never by terminal output — so it can never
       disagree with where typing (or voice) actually lands. A delegated
       focusin/focusout pair (wired once, below) marks whichever plain terminal
       currently holds focus and clears the mark the moment focus leaves to the
       top bar, dead space, or an explorer/browser pane. `_focusedTerminalIndex`
       tracks that same pane exactly (or -1 when nothing is selected) and is the
       single input target for voice / push-to-talk / search — so a pane that is
       not visibly selected never silently receives voice.

       Critically, the highlight is NOT tied to `term.onData`: TUI apps with
       mouse reporting (vim, opencode, …) emit mouse-move escape sequences
       through `onData`, so driving selection from input would make the
       highlight follow the mouse into an unfocused pane. Focus is the only
       source of truth. */
    function isPlainTerminalCard(card) {
        return Boolean(
            card
            && card.classList.contains('terminal-container')
            && !card.classList.contains('explorer-pane')
            && !card.classList.contains('browser-pane')
        );
    }

    function terminalCardSlot(card) {
        const slot = card ? Number(card.dataset.slot) : NaN;
        return Number.isInteger(slot) ? slot : -1;
    }

    /* Visual only: give exactly one plain terminal card the `terminal-active`
       treatment (plus accessible state) and clear it from every other pane.
       An invalid/missing index clears the highlight from all panes. */
    function paintActiveTerminalCard(index) {
        const targetCard = document.getElementById(`tc-${index}`);
        const isTarget = isPlainTerminalCard(targetCard);
        document.querySelectorAll('.terminal-container.terminal-active').forEach(card => {
            if (card !== targetCard || !isTarget) {
                card.classList.remove('terminal-active');
                card.removeAttribute('aria-current');
            }
        });
        if (isTarget) {
            targetCard.classList.add('terminal-active');
            targetCard.setAttribute('aria-current', 'true');
        }
    }

    /* A plain terminal gained focus: it becomes both the input target and the
       highlighted pane. An invalid target selects nothing. */
    function setFocusedTerminal(index) {
        if (!isPlainTerminalCard(document.getElementById(`tc-${index}`))) {
            clearActiveTerminalHighlight();
            return;
        }
        _focusedTerminalIndex = index;
        paintActiveTerminalCard(index);
        /* Re-light the broadcast ring across panes: the CSS rule also requires
           `broadcast-input`, so if broadcast was turned off while focus sat in
           dead space only this single pane lights up (OD-10). */
        document.getElementById('terminalsGrid')?.classList.add('terminal-focus');
    }

    /* Focus left every terminal (top bar / dead space / explorer / browser):
       nothing is selected, so drop the highlight AND the input target — voice
       and typing both go nowhere until a terminal is focused again. While
       broadcast is on this also drops the all-panes ring until the next
       focusin into a terminal. */
    function clearActiveTerminalHighlight() {
        _focusedTerminalIndex = -1;
        paintActiveTerminalCard(-1);
        document.getElementById('terminalsGrid')?.classList.remove('terminal-focus');
    }

    /* Full reset on teardown. */
    function resetFocusedTerminal() {
        clearActiveTerminalHighlight();
    }

    /* Pick an attached plain terminal to receive keyboard focus, preferring the
       current target, else the first eligible pane. Explorer/browser panes and
       not-yet-attached panes (no `term`) are skipped. */
    function firstAttachedPlainTerminalIndex(preferred = -1) {
        const eligible = i => Boolean(
            terminals[i]?.term
            && !isExplorerSession(terminals[i]._session)
            && !isBrowserSession(terminals[i]._session)
        );
        if (preferred >= 0 && eligible(preferred)) {
            return preferred;
        }
        for (let i = 0; i < terminals.length; i++) {
            if (eligible(i)) {
                return i;
            }
        }
        return -1;
    }

    /* Give a terminal real keyboard focus so typing is captured immediately —
       used when Broadcast typing is enabled so the user can start typing without
       first clicking a pane (the focusin handler then highlights it). */
    function focusActiveOrDefaultTerminal() {
        const index = firstAttachedPlainTerminalIndex(_focusedTerminalIndex);
        if (index !== -1) {
            try { terminals[index].term.focus(); } catch (_) {}
        }
    }

    /* Delegated, wired once: the terminal that holds DOM focus is the active
       pane. focusout only clears when focus is not moving to another plain
       terminal (whose focusin will repaint it). */
    document.addEventListener('focusin', event => {
        const card = event.target?.closest?.('.terminal-container');
        if (isPlainTerminalCard(card)) {
            setFocusedTerminal(terminalCardSlot(card));
        }
    });
    document.addEventListener('focusout', event => {
        const nextCard = event.relatedTarget?.closest?.('.terminal-container');
        if (!isPlainTerminalCard(nextCard)) {
            clearActiveTerminalHighlight();
        }
    });

    function forwardTerminalInput(index, data) {
        /* Selection is focus-driven only — never set it from `onData`, which
           also fires for TUI mouse-tracking sequences and would make the
           highlight follow the mouse into an unfocused pane. */
        if (!socket) {
            return;
        }
        const sid = sessionIds[index];
        if (sid) socket.emit('terminal_input', { session_id: sid, data });
        broadcastInputToPeers(index, data);
    }

    function wirePaneInputForwarding(t, i) {
        if (!t?.term) {
            return;
        }
        t.term.onData(data => forwardTerminalInput(i, data));
    }

    function remapCardIndexAttributes(card, sourceIndex, targetIndex) {
        const sourceSuffix = `-${sourceIndex}`;
        const targetSuffix = `-${targetIndex}`;
        card.querySelectorAll('[id]').forEach(element => {
            if (element.id.endsWith(sourceSuffix)) {
                element.id = `${element.id.slice(0, -sourceSuffix.length)}${targetSuffix}`;
            }
        });
        card.querySelectorAll('*').forEach(element => {
            Array.from(element.attributes).forEach(attribute => {
                if (attribute.name === 'for' && attribute.value.endsWith(sourceSuffix)) {
                    element.setAttribute(attribute.name, `${attribute.value.slice(0, -sourceSuffix.length)}${targetSuffix}`);
                    return;
                }
                if (!attribute.name.startsWith('data-')) {
                    return;
                }
                if (attribute.value === String(sourceIndex)) {
                    element.setAttribute(attribute.name, String(targetIndex));
                }
            });
        });
    }

    function stopHeaderButtonDrag(button) {
        if (!button) {
            return;
        }
        button.draggable = false;
        button.addEventListener('mousedown', event => {
            event.preventDefault();
            event.stopPropagation();
        });
    }

    function wireSplitCardControls(card, index) {
        wireCardDragAndDrop(card, card.querySelector('.terminal-header'));

        const refreshButton = card.querySelector(`[data-terminal-refresh="${index}"]`);
        if (refreshButton) {
            stopHeaderButtonDrag(refreshButton);
            refreshButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                refreshTerminalDisplay(index);
            });
        }

        const modeToggleButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (modeToggleButton) {
            stopHeaderButtonDrag(modeToggleButton);
            modeToggleButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                switchSessionPaneMode(index);
            });
        }

        const browserModeButton = card.querySelector(`[data-session-browser-toggle="${index}"]`);
        if (browserModeButton) {
            stopHeaderButtonDrag(browserModeButton);
            browserModeButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                switchSessionBrowserMode(index);
            });
        }

        const splitButton = card.querySelector(`[data-terminal-split="${index}"]`);
        if (splitButton) {
            stopHeaderButtonDrag(splitButton);
            splitButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                splitTerminalPane(index);
            });
        }

        const unsplitButton = card.querySelector(`[data-terminal-unsplit="${index}"]`);
        if (unsplitButton) {
            stopHeaderButtonDrag(unsplitButton);
            unsplitButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                closeSplitPane(index);
            });
        }

        const clearButton = card.querySelector(`[data-terminal-clear="${index}"]`);
        if (clearButton) {
            stopHeaderButtonDrag(clearButton);
            clearButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                clearTerminalDisplayFromButton(index);
            });
        }

        const closeButton = card.querySelector(`[data-terminal-close="${index}"]`);
        if (closeButton) {
            stopHeaderButtonDrag(closeButton);
            closeButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                closeTerminalPane(index);
            });
        }

        const voiceButton = card.querySelector(`[data-terminal-voice="${index}"]`);
        if (voiceButton) {
            stopHeaderButtonDrag(voiceButton);
            voiceButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                _toggleVoice(index);
            });
        }

        _syncVoiceControls(index);
    }

    function createSplitTerminalCard(sourceCard, session, sourceIndex, targetIndex) {
        const card = sourceCard.cloneNode(true);
        card.classList.remove('dragging', 'drag-target', 'explorer-pane', 'browser-pane');
        card.id = `tc-${targetIndex}`;
        card.dataset.slot = String(targetIndex);
        delete card.dataset.explorerThemeKey;
        delete card.dataset.explorerThemeSource;
        delete card.dataset.explorerTheme;
        remapCardIndexAttributes(card, sourceIndex, targetIndex);

        card.style.gridColumn = '';
        card.style.gridRow = '';
        const sessionColour = tabColourForGroup(activeGroupId);
        card.style.setProperty('--session-color', sessionColour);
        card.style.setProperty('--session-color-dim', hexToRgba(sessionColour, 0.45));

        const name = card.querySelector(`#tname-${targetIndex}`);
        if (name) {
            name.textContent = session.title || `Terminal ${targetIndex + 1}`;
        }
        const host = card.querySelector(`#thost-${targetIndex}`);
        if (host) {
            host.textContent = session.host || '';
        }
        const dot = card.querySelector(`#tdot-${targetIndex}`);
        if (dot) {
            dot.className = `status-dot ${session.status || 'pending'}`;
        }
        const label = card.querySelector(`#tlabel-${targetIndex}`);
        if (label) {
            label.textContent = session.status === 'connected' ? 'Connected' : 'Pending';
        }
        const splitButton = card.querySelector(`#tsplit-${targetIndex}`);
        if (splitButton) {
            splitButton.textContent = '⊞';
            splitButton.disabled = false;
        }
        const unsplitButton = card.querySelector(`#tunsplit-${targetIndex}`);
        if (unsplitButton) {
            unsplitButton.textContent = '⊟';
            unsplitButton.hidden = false;
            unsplitButton.disabled = false;
        }

        const wrapper = card.querySelector(`#tw-${targetIndex}`);
        if (wrapper) {
            wrapper.innerHTML = `
                <div class="terminal-surface">
                    <div class="terminal-canvas" id="tcanvas-${targetIndex}"></div>
                </div>
                <div class="placeholder" id="ph-${targetIndex}">
                    <div class="spinner"></div>
                    <span style="font-size:.78rem">Connecting…</span>
                </div>
            `;
        }

        wireSplitCardControls(card, targetIndex);
        return card;
    }

    function attachSplitTerminalEvents(index) {
        const terminal = terminals[index];
        if (!terminal?.term) {
            return;
        }

        terminal.term.onData(data => forwardTerminalInput(index, data));
    }

    function getExplorerSelectedDirectory(index) {
        const pane = terminals[index];
        if (!pane || !isExplorerSession(pane._session)) {
            return '';
        }

        return pane._explorerPath || '';
    }

    function updateModeToggleButton(button, isExplorer, loading = false) {
        if (!button) {
            return;
        }

        const label = isExplorer
            ? 'Open terminal in current explorer directory'
            : 'Open file explorer for this terminal directory';
        button.disabled = loading;
        button.innerHTML = loading ? '...' : (isExplorer ? TERMINAL_PROMPT_ICON : EXPLORER_MODE_FOLDER_ICON);
        button.title = label;
        button.setAttribute('aria-label', label);
    }

    function updateBrowserModeToggleButton(button, isBrowser, loading = false) {
        if (!button) {
            return;
        }

        const label = isBrowser ? 'Return to terminal' : 'Open browser preview';
        button.disabled = loading;
        button.innerHTML = loading ? '...' : (isBrowser ? TERMINAL_PROMPT_ICON : BROWSER_MODE_GLOBE_ICON);
        button.title = label;
        button.setAttribute('aria-label', label);
    }

    function setTerminalOnlyControlsVisible(card, visible) {
        ['data-terminal-split', 'data-terminal-unsplit'].forEach(attribute => {
            const button = card.querySelector(`[${attribute}]`);
            if (button) {
                button.hidden = !visible;
            }
        });
    }

    function ensureBrowserModeButton(card, index, session, isBrowser = false) {
        let button = card.querySelector(`[data-session-browser-toggle="${index}"]`);
        if (session?.mode !== 'wsl') {
            if (button) {
                button.remove();
            }
            return null;
        }
        if (button) {
            button.hidden = false;
            updateBrowserModeToggleButton(button, isBrowser);
            return button;
        }

        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        const insertTarget = modeButton || card.querySelector(`[data-terminal-refresh="${index}"]`);
        if (!insertTarget) {
            return null;
        }

        insertTarget.insertAdjacentHTML('afterend', `
            <button
                type="button"
                class="terminal-action-btn terminal-browser-toggle-btn"
                id="tbrowsermode-${index}"
                data-session-browser-toggle="${index}"
                title="${isBrowser ? 'Return to terminal' : 'Open browser preview'}"
                aria-label="${isBrowser ? 'Return to terminal' : 'Open browser preview'}"
            >
                ${isBrowser ? TERMINAL_PROMPT_ICON : BROWSER_MODE_GLOBE_ICON}
            </button>
        `);
        button = card.querySelector(`[data-session-browser-toggle="${index}"]`);
        if (button) {
            stopHeaderButtonDrag(button);
            button.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                switchSessionBrowserMode(index);
            });
        }
        return button;
    }

    function renderBrowserSurface(index, session) {
        const url = getBrowserSessionUrl(session);
        return `
            <div class="terminal-surface">
                <div class="browser-surface" id="browser-${index}">
                    <div class="browser-bar">
                        <input class="browser-url-input" id="browser-url-${index}" data-browser-url="${index}" type="url" value="${escHtml(url)}" title="${escHtml(url)}" aria-label="Browser URL">
                        <button type="button" class="browser-open-btn" id="browser-open-${index}" data-browser-open="${index}" title="Open this URL in an external browser tab">Open</button>
                    </div>
                    <iframe
                        class="browser-frame"
                        id="browser-frame-${index}"
                        title="${escHtml(session.title || `Browser ${index + 1}`)}"
                        src="${escHtml(url)}"
                        sandbox="allow-downloads allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
                    ></iframe>
                </div>
            </div>
            <div class="placeholder" id="ph-${index}">
                <div class="spinner"></div>
                <span style="font-size:.78rem">Loading browser...</span>
            </div>
        `;
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

        const browserFrame = card.querySelector(`#browser-frame-${index}`);
        if (browserFrame && !browserFrame.dataset.bound) {
            browserFrame.dataset.bound = 'true';
            browserFrame.addEventListener('load', () => {
                document.getElementById(`ph-${index}`)?.remove();
            });
        }
    }

    function wireExplorerOnlyControls(card, index) {
        const explorerThemeButton = card.querySelector(`[data-explorer-theme-toggle="${index}"]`);
        if (explorerThemeButton && !explorerThemeButton.dataset.bound) {
            explorerThemeButton.dataset.bound = 'true';
            explorerThemeButton.draggable = false;
            updateExplorerThemeButton(explorerThemeButton, card.dataset.explorerTheme || 'dark');
            explorerThemeButton.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerThemeButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                toggleExplorerTheme(index);
            });
        }

        const explorerRefreshButton = card.querySelector(`[data-explorer-refresh="${index}"]`);
        if (explorerRefreshButton && !explorerRefreshButton.dataset.bound) {
            explorerRefreshButton.dataset.bound = 'true';
            explorerRefreshButton.draggable = false;
            explorerRefreshButton.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerRefreshButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                refreshTerminalDisplay(index);
            });
        }

        const explorerUpButton = card.querySelector(`[data-explorer-up="${index}"]`);
        if (explorerUpButton && !explorerUpButton.dataset.bound) {
            explorerUpButton.dataset.bound = 'true';
            explorerUpButton.draggable = false;
            explorerUpButton.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerUpButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                const pane = terminals[index];
                const targetPath = pane?._explorerMode === 'file'
                    ? (pane._explorerPath || '')
                    : (pane?._explorerParentPath || '');
                loadExplorerPane(index, targetPath);
            });
        }

        const explorerGitToggle = card.querySelector(`[data-explorer-git-toggle="${index}"]`);
        if (explorerGitToggle && !explorerGitToggle.dataset.bound) {
            explorerGitToggle.dataset.bound = 'true';
            explorerGitToggle.draggable = false;
            explorerGitToggle.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerGitToggle.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                toggleExplorerGitSidebar(index);
            });
        }

        const explorerTreeToggle = card.querySelector(`[data-explorer-tree-toggle="${index}"]`);
        if (explorerTreeToggle && !explorerTreeToggle.dataset.bound) {
            explorerTreeToggle.dataset.bound = 'true';
            explorerTreeToggle.draggable = false;
            explorerTreeToggle.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerTreeToggle.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                toggleExplorerTreeSidebar(index);
            });
        }

        const explorerOsOpen = card.querySelector(`[data-explorer-os-open="${index}"]`);
        if (explorerOsOpen && !explorerOsOpen.dataset.bound) {
            explorerOsOpen.dataset.bound = 'true';
            explorerOsOpen.draggable = false;
            explorerOsOpen.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerOsOpen.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                revealExplorerInOs(index);
            });
        }
    }

    function ensureExplorerThemeButton(card, index) {
        let button = card.querySelector(`[data-explorer-theme-toggle="${index}"]`);
        if (button) {
            button.hidden = false;
            return button;
        }

        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (!modeButton) {
            return null;
        }

        modeButton.insertAdjacentHTML('afterend', `
            <button
                type="button"
                class="terminal-action-btn explorer-theme-btn"
                id="texplorertheme-${index}"
                data-explorer-theme-toggle="${index}"
                title="Dark explorer theme"
                aria-label="Dark explorer theme"
                aria-pressed="false"
            >
                ${THEME_MOON_ICON}
            </button>
        `);
        button = card.querySelector(`[data-explorer-theme-toggle="${index}"]`);
        return button;
    }

    function ensureModeToggleButton(card, index) {
        let button = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (button) {
            button.hidden = false;
            return button;
        }

        const refreshButton = card.querySelector(`[data-terminal-refresh="${index}"]`);
        if (!refreshButton) {
            return null;
        }

        refreshButton.insertAdjacentHTML('afterend', `
            <button
                type="button"
                class="terminal-action-btn terminal-mode-toggle-btn"
                id="tmode-${index}"
                data-session-mode-toggle="${index}"
                title="Open file explorer for this terminal directory"
                aria-label="Open file explorer for this terminal directory"
            >
                ${EXPLORER_MODE_FOLDER_ICON}
            </button>
        `);
        button = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (button) {
            stopHeaderButtonDrag(button);
            button.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                switchSessionPaneMode(index);
            });
        }
        return button;
    }

    function ensureSplitControls(card, index) {
        let splitButton = card.querySelector(`[data-terminal-split="${index}"]`);
        if (splitButton) {
            splitButton.hidden = false;
            return;
        }

        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (!modeButton) {
            return;
        }

        modeButton.insertAdjacentHTML('afterend', `
            <button
                type="button"
                class="terminal-action-btn terminal-split-btn"
                id="tsplit-${index}"
                data-terminal-split="${index}"
                title="Split this terminal pane"
                aria-label="Split this terminal pane"
            >
                ⊞
            </button>
            <button
                type="button"
                class="terminal-action-btn terminal-unsplit-btn"
                id="tunsplit-${index}"
                data-terminal-unsplit="${index}"
                title="Close this split pane"
                aria-label="Close this split pane"
                hidden
            >
                ⊟
            </button>
        `);

        splitButton = card.querySelector(`[data-terminal-split="${index}"]`);
        if (splitButton) {
            stopHeaderButtonDrag(splitButton);
            splitButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                splitTerminalPane(index);
            });
        }

        const unsplitButton = card.querySelector(`[data-terminal-unsplit="${index}"]`);
        if (unsplitButton) {
            stopHeaderButtonDrag(unsplitButton);
            unsplitButton.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                closeSplitPane(index);
            });
        }
    }

    function replacePaneWithExplorer(index, session) {
        const card = document.getElementById(`tc-${index}`);
        const wrapper = document.getElementById(`tw-${index}`);
        if (!card || !wrapper) {
            return false;
        }

        const previousTerminal = terminals[index];
        if (previousTerminal?.term) {
            try { previousTerminal.term.dispose(); } catch (_) {}
        }
        disconnectTerminalObserver(index);
        if (socket && sessionIds[index]) {
            socket.emit('leave_session', { session_id: sessionIds[index] });
        }

        const explorerThemeKey = session.session_id || `${activeGroupId || 'group'}:${index}`;
        const resolvedExplorerTheme = resolveInitialExplorerTheme(session, explorerThemeKey);
        const initialExplorerTheme = resolvedExplorerTheme.theme;
        terminals[index] = {
            _session: session,
            _paneType: 'explorer',
            _attached: false,
            _explorerTreeSidebarOpen: Boolean(session.explorer_tree_open),
            _explorerGitSidebarOpen: Boolean(session.explorer_git_open)
        };
        sessionIds[index] = session.session_id;
        setSessionRoute(session.session_id, activeGroupId, index);

        card.classList.add('explorer-pane');
        card.classList.remove('browser-pane');
        card.dataset.explorerThemeKey = explorerThemeKey;
        card.dataset.explorerThemeSource = resolvedExplorerTheme.source;
        card.dataset.explorerTheme = initialExplorerTheme;
        setTerminalOnlyControlsVisible(card, false);
        const browserModeButton = card.querySelector(`[data-session-browser-toggle="${index}"]`);
        if (browserModeButton) {
            browserModeButton.remove();
        }
        ensureExplorerThemeButton(card, index);
        applyExplorerThemeToCard(card, initialExplorerTheme);
        const nameLabel = document.getElementById(`tname-${index}`);
        const hostLabel = document.getElementById(`thost-${index}`);
        if (nameLabel) {
            nameLabel.textContent = session.title || `Terminal ${index + 1}`;
        }
        if (hostLabel) {
            hostLabel.textContent = session.host || '';
        }
        updateModeToggleButton(card.querySelector(`[data-session-mode-toggle="${index}"]`), true);
        wrapper.innerHTML = `
            <div class="terminal-surface">
                <div class="explorer-surface" id="explorer-${index}">
                    <div class="explorer-bar">
                        <button type="button" class="explorer-refresh" id="explorer-refresh-${index}" data-explorer-refresh="${index}" title="Refresh explorer" aria-label="Refresh explorer">${TERMINAL_REFRESH_ICON}</button>
                        <button type="button" class="explorer-up" id="explorer-up-${index}" data-explorer-up="${index}" title="Go to parent directory">↑</button>
                        <button type="button" class="explorer-tree-toggle" id="explorer-tree-toggle-${index}" data-explorer-tree-toggle="${index}" title="Show file tree" aria-label="Show file tree" aria-pressed="false">${EXPLORER_TREE_TOGGLE_ICON}</button>
                        <button type="button" class="explorer-git-toggle" id="explorer-git-toggle-${index}" data-explorer-git-toggle="${index}" title="Show Git changes and history" aria-label="Show Git changes and history" aria-pressed="false">${EXPLORER_GIT_TOGGLE_ICON}</button>
                        ${session.mode === 'ssh' ? '' : `<button type="button" class="explorer-os-open" id="explorer-os-open-${index}" data-explorer-os-open="${index}" title="Open current location in system file manager" aria-label="Open current location in system file manager">${EXPLORER_OS_OPEN_ICON}</button>`}
                        <div class="explorer-git-summary" id="explorer-git-${index}" aria-live="polite"></div>
                        <div class="explorer-path" id="explorer-path-${index}">${escHtml(session.directory || '')}</div>
                        <div class="explorer-directory-search" id="explorer-directory-search-${index}"></div>
                    </div>
                    <div class="explorer-main" id="explorer-main-${index}">
                        <aside class="explorer-sidebar" id="explorer-sidebar-${index}" hidden>
                            <div class="explorer-tree-panel" id="explorer-tree-panel-${index}" hidden></div>
                            <button type="button" class="explorer-sidebar-splitter" id="explorer-sidebar-splitter-${index}" data-explorer-sidebar-splitter="${index}" aria-label="Resize file tree and Git panels" hidden></button>
                            <div class="explorer-git-panel" id="explorer-git-panel-${index}" hidden></div>
                        </aside>
                        <button type="button" class="explorer-sidebar-resizer" id="explorer-sidebar-resizer-${index}" data-explorer-sidebar-resizer="${index}" aria-label="Resize explorer sidebar" hidden></button>
                        <div class="explorer-list" id="explorer-list-${index}">
                            <div class="explorer-message">Loading directory...</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="placeholder" id="ph-${index}">
                <div class="spinner"></div>
                <span style="font-size:.78rem">Loading explorer...</span>
            </div>
        `;
        wireExplorerOnlyControls(card, index);
        setStatus(index, session.status);
        loadExplorerPane(index, null, { force: true });
        restoreExplorerSidebarState(index);
        updateAllSplitButtonStates();
        return true;
    }

    function replacePaneWithBrowser(index, session) {
        const card = document.getElementById(`tc-${index}`);
        const wrapper = document.getElementById(`tw-${index}`);
        if (!card || !wrapper) {
            return false;
        }

        const previousTerminal = terminals[index];
        if (previousTerminal?.term) {
            try { previousTerminal.term.dispose(); } catch (_) {}
        }
        disconnectTerminalObserver(index);
        if (socket && sessionIds[index]) {
            socket.emit('leave_session', { session_id: sessionIds[index] });
        }

        terminals[index] = { _session: session, _paneType: 'browser', _attached: false };
        sessionIds[index] = session.session_id;
        setSessionRoute(session.session_id, activeGroupId, index);

        card.classList.add('browser-pane');
        card.classList.remove('explorer-pane');
        delete card.dataset.explorerThemeKey;
        delete card.dataset.explorerThemeSource;
        delete card.dataset.explorerTheme;
        const explorerThemeButton = card.querySelector(`[data-explorer-theme-toggle="${index}"]`);
        if (explorerThemeButton) {
            explorerThemeButton.remove();
        }
        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (modeButton) {
            modeButton.hidden = true;
        }
        setTerminalOnlyControlsVisible(card, false);
        const browserButton = ensureBrowserModeButton(card, index, session, true);
        updateBrowserModeToggleButton(browserButton, true);
        const nameLabel = document.getElementById(`tname-${index}`);
        const hostLabel = document.getElementById(`thost-${index}`);
        if (nameLabel) {
            nameLabel.textContent = session.title || `Terminal ${index + 1}`;
        }
        if (hostLabel) {
            hostLabel.textContent = session.host || '';
        }
        wrapper.innerHTML = renderBrowserSurface(index, session);
        wireBrowserOnlyControls(card, index);
        setStatus(index, session.status);
        updateAllSplitButtonStates();
        return true;
    }

    function replacePaneWithTerminal(index, session) {
        const card = document.getElementById(`tc-${index}`);
        const wrapper = document.getElementById(`tw-${index}`);
        if (!card || !wrapper) {
            return false;
        }

        const terminal = makeTerminal();
        terminal._session = session;
        terminals[index] = terminal;
        sessionIds[index] = session.session_id;
        setSessionRoute(session.session_id, activeGroupId, index);

        card.classList.remove('explorer-pane', 'browser-pane');
        delete card.dataset.explorerThemeKey;
        delete card.dataset.explorerThemeSource;
        delete card.dataset.explorerTheme;
        const explorerThemeButton = card.querySelector(`[data-explorer-theme-toggle="${index}"]`);
        if (explorerThemeButton) {
            explorerThemeButton.remove();
        }
        ensureModeToggleButton(card, index);
        ensureSplitControls(card, index);
        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (modeButton) {
            modeButton.hidden = false;
        }
        const browserButton = ensureBrowserModeButton(card, index, session, false);
        updateBrowserModeToggleButton(browserButton, false);
        const nameLabel = document.getElementById(`tname-${index}`);
        const hostLabel = document.getElementById(`thost-${index}`);
        if (nameLabel) {
            nameLabel.textContent = session.title || `Terminal ${index + 1}`;
        }
        if (hostLabel) {
            hostLabel.textContent = session.host || '';
        }
        updateModeToggleButton(card.querySelector(`[data-session-mode-toggle="${index}"]`), false);
        wrapper.innerHTML = `
            <div class="terminal-surface">
                <div class="terminal-canvas" id="tcanvas-${index}"></div>
            </div>
            <div class="placeholder" id="ph-${index}">
                <div class="spinner"></div>
                <span style="font-size:.78rem">Connecting...</span>
            </div>
        `;
        attachSplitTerminalEvents(index);
        setStatus(index, session.status);
        if (socket) {
            socket.emit('join_session', { session_id: session.session_id });
        }
        if (session.status === 'connected') {
            attachTerminal(index);
        }
        updateAllSplitButtonStates();
        return true;
    }

    function replaceSessionPaneMode(index, session) {
        if (isBrowserSession(session)) {
            return replacePaneWithBrowser(index, session);
        }
        if (isExplorerSession(session)) {
            return replacePaneWithExplorer(index, session);
        }
        return replacePaneWithTerminal(index, session);
    }

    async function switchSessionPaneMode(index) {
        const sessionId = sessionIds[index];
        const terminal = terminals[index];
        const button = document.getElementById(`tmode-${index}`);
        if (!sessionId || !terminal?._session) {
            return;
        }

        const switchingToTerminal = isExplorerSession(terminal._session);
        const targetMode = switchingToTerminal ? 'terminal' : 'explorer';
        const body = { startup_mode: targetMode };
        if (switchingToTerminal) {
            body.directory = getExplorerSelectedDirectory(index);
        } else {
            body.refresh_cwd = true;
        }

        updateModeToggleButton(button, switchingToTerminal, true);
        pendingModeSwitchSessionIds.add(sessionId);

        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/mode`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Mode switch failed with status ${response.status}`);
            }

            if (!replaceSessionPaneMode(index, data)) {
                await initialLoad();
            }
        } catch (error) {
            console.error('[GridVibe Sessions] switchSessionPaneMode failed:', error);
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Mode switch failed: ${error.message}`;
            }
            updateModeToggleButton(button, switchingToTerminal);
        } finally {
            pendingModeSwitchSessionIds.delete(sessionId);
        }
    }

    async function switchSessionBrowserMode(index) {
        const sessionId = sessionIds[index];
        const terminal = terminals[index];
        const button = document.getElementById(`tbrowsermode-${index}`);
        if (!sessionId || !terminal?._session) {
            return;
        }

        const switchingToTerminal = isBrowserSession(terminal._session);
        const targetMode = switchingToTerminal ? 'terminal' : 'browser';
        const body = { startup_mode: targetMode };
        if (!switchingToTerminal) {
            body.url = 'http://127.0.0.1:3000';
        }

        updateBrowserModeToggleButton(button, switchingToTerminal, true);
        pendingModeSwitchSessionIds.add(sessionId);

        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/mode`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Browser mode switch failed with status ${response.status}`);
            }

            if (!replaceSessionPaneMode(index, data)) {
                await initialLoad();
            }
        } catch (error) {
            console.error('[GridVibe Sessions] switchSessionBrowserMode failed:', error);
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Browser mode switch failed: ${error.message}`;
            }
            updateBrowserModeToggleButton(button, switchingToTerminal);
        } finally {
            pendingModeSwitchSessionIds.delete(sessionId);
        }
    }

    async function navigateBrowserPane(index, value) {
        const terminal = terminals[index];
        const sessionId = sessionIds[index];
        const input = document.getElementById(`browser-url-${index}`);
        const frame = document.getElementById(`browser-frame-${index}`);
        if (!terminal?._session || !sessionId || !isBrowserSession(terminal._session)) {
            return false;
        }

        let nextUrl;
        try {
            nextUrl = normalizeBrowserUrlInput(value);
        } catch (error) {
            if (input) {
                input.value = getBrowserSessionUrl(terminal._session);
            }
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Browser URL rejected: ${error.message}`;
            }
            return false;
        }

        if (nextUrl === getBrowserSessionUrl(terminal._session)) {
            if (input) {
                input.value = nextUrl;
                input.title = nextUrl;
            }
            return true;
        }

        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/mode`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ startup_mode: 'browser', url: nextUrl })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Browser navigation failed with status ${response.status}`);
            }
            terminal._session = data;
            if (input) {
                input.value = getBrowserSessionUrl(data);
                input.title = getBrowserSessionUrl(data);
            }
            if (frame) {
                frame.src = getBrowserSessionUrl(data);
            }
            setStatus(index, data.status);
            return true;
        } catch (error) {
            console.error('[GridVibe Sessions] navigateBrowserPane failed:', error);
            if (input) {
                input.value = getBrowserSessionUrl(terminal._session);
                input.title = getBrowserSessionUrl(terminal._session);
            }
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Browser navigation failed: ${error.message}`;
            }
            return false;
        }
    }

    async function closeSplitPane(index) {
        const plan = buildCloseSplitPlan(index);
        if (!plan) {
            updateUnsplitButtonState(index);
            return;
        }

        const button = document.getElementById(`tunsplit-${index}`);
        if (button) {
            button.disabled = true;
            button.textContent = '...';
        }

        try {
            for (const sessionId of plan.sessionIdsToClose) {
                const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
                    method: 'DELETE',
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.error || `Close split failed with status ${response.status}`);
                }
            }

            pendingSplitRestore = {
                groupId: activeGroupId,
                rectsBySessionId: plan.rectsBySessionId,
                originalSplitSlotCount: plan.originalSplitSlotCount,
                splitColumnWeights: cloneSplitTrackWeights(splitColumnWeights),
                splitRowWeights: cloneSplitTrackWeights(splitRowWeights),
            };
            await initialLoad();
        } catch (error) {
            console.error('[GridVibe Sessions] closeSplitPane failed:', error);
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Close split failed: ${error.message}`;
            }
        } finally {
            if (button) {
                button.textContent = '⊟';
            }
            updateAllSplitButtonStates();
        }
    }

    /* Snapshot the live client state of every pane except the one being closed,
       keyed by session id. Closing a terminal forces a full grid rebuild
       (initialLoad), which would otherwise reset sibling explorer panes to a
       plain listing and reload browser panes; re-applying this snapshot after the
       rebuild keeps their open file, tree, Git sidebar and URL (ISSUE-2026-027). */
    function captureSurvivingPaneClientState(closingSessionId) {
        const stateBySessionId = {};
        terminals.forEach((pane, index) => {
            const sessionId = sessionIds[index];
            if (!pane || !sessionId || sessionId === closingSessionId) {
                return;
            }
            if (isExplorerSession(pane._session)) {
                /* 5.a: fold the shown tab's live mode + scroll into its record
                   first, then carry every tab's full view record (mode, scroll
                   metrics, identity, zoom, mode preference) through the rebuild
                   — the disk-shape session fields alone would reset the per-tab
                   state 2.e introduced. */
                explorerCaptureActiveTabView(index);
                const tabs = explorerSerializeTabs(pane);
                const previewTab = explorerPreviewTab(pane);
                const previewActive = pane._explorerActiveTabId === EXPLORER_PREVIEW_TAB_ID;
                stateBySessionId[sessionId] = {
                    type: 'explorer',
                    explorer_tree_open: Boolean(pane._explorerTreeSidebarOpen),
                    explorer_git_open: Boolean(pane._explorerGitSidebarOpen),
                    explorer_open_tabs: tabs.open_tabs,
                    explorer_active_tab: tabs.active_tab,
                    explorer_tab_views: tabs.tab_views,
                    explorer_tab_state: pane._explorerTabs.map(tab => ({
                        id: tab.id,
                        view: tab.view || null,
                        fontSize: tab.fontSize || 0,
                        preferredMode: tab.preferredMode || '',
                        dirPath: tab.dirPath || ''
                    })),
                    /* Only the dynamic Preview tab needs an explicit reopen; pinned
                       tabs come back through the persisted-tab path. */
                    explorer_preview_path: previewActive ? (previewTab?.path || '') : '',
                    explorer_preview_view: previewActive ? (pane._explorerLastFileView || '') : '',
                };
            } else if (isBrowserSession(pane._session)) {
                stateBySessionId[sessionId] = {
                    type: 'browser',
                    browser_url: getBrowserSessionUrl(pane._session),
                };
            }
        });
        return stateBySessionId;
    }

    /* Re-apply one surviving explorer pane's captured state after a close rebuild.
       Tree/Git flags and pinned tabs were overlaid onto the session object, so the
       viewer entry point restores them; the previewed file is reopened only when
       the Preview tab was the active view. */
    function restoreExplorerPaneFromClose(index, snapshot) {
        syncExplorerPane(index);
        /* 5.a: reattach each tab's captured view record (mode + scroll + zoom
           + mode preference) synchronously — the active tab's re-fetch has not
           resolved yet, so its render restores through the 2.e identity check
           instead of falling back to defaults. */
        const pane = terminals[index];
        if (pane && Array.isArray(snapshot.explorer_tab_state)) {
            ensureExplorerTabState(pane);
            snapshot.explorer_tab_state.forEach(saved => {
                const tab = pane._explorerTabs.find(entry => entry.id === saved.id);
                if (!tab) {
                    return;
                }
                if (saved.view) {
                    tab.view = saved.view;
                }
                if (saved.fontSize) {
                    tab.fontSize = saved.fontSize;
                }
                if (saved.preferredMode) {
                    tab.preferredMode = saved.preferredMode;
                }
                if (saved.dirPath) {
                    tab.dirPath = saved.dirPath;
                }
            });
        }
        restoreExplorerSidebarState(index);
        if (snapshot.explorer_preview_path) {
            if (pane && snapshot.explorer_preview_view) {
                pane._explorerLastFileView = snapshot.explorer_preview_view;
            }
            openExplorerFile(index, snapshot.explorer_preview_path, { pinned: false, showLoading: false });
        }
    }

    async function closeTerminalPane(index) {
        const plan = buildCloseTerminalPlan(index);
        if (!plan) {
            return;
        }
        const restoreRectsBySessionId = plan.closeLastPane
            ? null
            : buildTerminalCloseRectsBySessionId(plan);
        if (!plan.closeLastPane && !restoreRectsBySessionId) {
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = 'Close terminal failed: no neighboring pane can safely fill this layout';
            }
            return;
        }

        const button = document.getElementById(`tclose-${index}`);
        if (button) {
            button.disabled = true;
            button.textContent = '...';
        }

        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(plan.sessionId)}`, {
                method: 'DELETE',
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Close terminal failed with status ${response.status}`);
            }

            if (plan.closeLastPane) {
                if (visibleGroupId === activeGroupId) {
                    teardownCurrentGrid();
                }
                activeGroupId = '';
                await loadSessionGroups();
                if (sessionGroups.length === 0) {
                    await _closeWindowAfterLastSession();
                    return;
                }
                await initialLoad();
                return;
            }

            pendingCloseClientState = {
                groupId: activeGroupId,
                stateBySessionId: captureSurvivingPaneClientState(plan.sessionId),
            };
            pendingSplitRestore = {
                groupId: activeGroupId,
                rectsBySessionId: restoreRectsBySessionId,
                originalSplitSlotCount: plan.originalSplitSlotCount,
                splitColumnWeights: cloneSplitTrackWeights(splitColumnWeights),
                splitRowWeights: cloneSplitTrackWeights(splitRowWeights),
            };
            await initialLoad();
        } catch (error) {
            console.error('[GridVibe Sessions] closeTerminalPane failed:', error);
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Close terminal failed: ${error.message}`;
            }
        } finally {
            if (button) {
                button.textContent = '×';
                button.disabled = false;
            }
            updateAllSplitButtonStates();
        }
    }

    async function splitTerminalPane(index) {
        const sourceSessionId = sessionIds[index];
        const sourceTerminal = terminals[index];
        const sourceCard = document.getElementById(`tc-${index}`);
        const grid = document.getElementById('terminalsGrid');
        const button = document.getElementById(`tsplit-${index}`);
        if (!sourceSessionId || !sourceTerminal || !sourceCard || !grid || isExplorerSession(sourceTerminal._session)) {
            return;
        }
        if (terminals.length >= getSplitPaneLimit()) {
            updateAllSplitButtonStates();
            return;
        }

        const visualIndex = Array.from(grid.children).indexOf(sourceCard);
        if (visualIndex < 0) {
            return;
        }

        const rects = ensureSplitSlotRects();
        const sourceRect = rects[visualIndex];
        const axis = sourceRect ? chooseSplitAxis(index, sourceRect) : '';
        if (!axis) {
            updateSplitButtonState(index);
            return;
        }

        if (button) {
            button.disabled = true;
            button.textContent = '...';
        }

        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sourceSessionId)}/split`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ axis })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Split failed with status ${response.status}`);
            }

            const session = data.session;
            if (!session?.session_id) {
                throw new Error('Split response did not include a session');
            }

            const newIndex = terminals.length;
            const terminal = makeTerminal();
            terminal._session = session;
            terminals.push(terminal);
            sessionIds.push(session.session_id);
            setSessionRoute(session.session_id, activeGroupId, newIndex);

            const newCard = createSplitTerminalCard(sourceCard, session, index, newIndex);
            sourceCard.after(newCard);
            const [firstRect, secondRect] = splitSlotRect(sourceRect, axis);
            splitSlotRects.splice(visualIndex, 1, firstRect, secondRect);
            applySplitSlotGeometry({ fit: false });
            attachSplitTerminalEvents(newIndex);

            if (socket) {
                socket.emit('join_session', { session_id: session.session_id });
            }
            if (session.status === 'connected') {
                attachTerminal(newIndex);
            }

            if (data.group) {
                const groupIndex = sessionGroups.findIndex(group => group.group_id === data.group.group_id);
                if (groupIndex >= 0) {
                    sessionGroups[groupIndex] = data.group;
                } else {
                    sessionGroups.push(data.group);
                }
                renderSessionTabs();
            }

            updateSessionChrome(terminals.length, activeGroupId);
            updateAllSplitButtonStates();
            await ensureAttachedTerminalsReady([index, newIndex]);
            emitTerminalResize(index, true);
            emitTerminalResize(newIndex, true);
        } catch (error) {
            console.error('[GridVibe Sessions] splitTerminalPane failed:', error);
            const label = document.getElementById('sessionLabel');
            if (label) {
                label.textContent = `Split failed: ${error.message}`;
            }
        } finally {
            if (button) {
                button.textContent = '⊞';
            }
            updateAllSplitButtonStates();
        }
    }

    /* ─────────────────────────────────────────────
       Clipboard helpers (copy / paste)
    ───────────────────────────────────────────── */
    function _copyText(text) {
        if (!text) return;
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text).catch(() => _copyTextFallback(text));
        } else {
            _copyTextFallback(text);
        }
    }

    function _copyTextFallback(text) {
        const el = document.createElement('textarea');
        el.value = text;
        el.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
        document.body.appendChild(el);
        el.select();
        try { document.execCommand('copy'); } catch (_) {}
        el.remove();
    }

    function _wireClipboard(index) {
        const term = terminals[index].term;

        /* Auto-copy on mouse selection */
        term.onSelectionChange(() => {
            const sel = term.getSelection();
            if (sel) _copyText(sel);
        });

        /* Intercept keys before xterm processes them */
        term.attachCustomKeyEventHandler(e => {
            if (e.type !== 'keydown') return true;

            /* Ctrl+Shift+C → copy selection */
            if (e.ctrlKey && e.shiftKey && e.code === 'KeyC') {
                const sel = term.getSelection();
                _copyText(sel);
                return false;
            }

            /* Ctrl+V → paste from clipboard */
            if (e.ctrlKey && !e.shiftKey && e.code === 'KeyV') {
                _pasteToTerminal(index);
                return false;
            }

            return true;
        });

        /* Handle browser right-click → Paste menu item (fires paste event on textarea) */
        if (term.textarea) {
            term.textarea.addEventListener('paste', e => {
                e.preventDefault();
                e.stopImmediatePropagation();
                const text = e.clipboardData?.getData('text') ?? '';
                if (text) _sendToTerminal(index, text);
            }, true);
        }

        /* Right-click → custom context menu */
        term.element.addEventListener('contextmenu', e => {
            e.preventDefault();
            _showTermCtxMenu(e.clientX, e.clientY, index);
        });
    }

    function _sendToTerminal(index, text) {
        const sid = sessionIds[index];
        if (sid && socket && text) socket.emit('terminal_input', { session_id: sid, data: text });
    }

    function _pasteToTerminal(index) {
        if (!navigator.clipboard) return;
        navigator.clipboard.readText()
            .then(text => _sendToTerminal(index, text))
            .catch(() => {});
    }


    function findExplorerSearchTargetIndex() {
        const activeCard = document.activeElement?.closest?.('.explorer-pane');
        const activeSlot = activeCard ? Number(activeCard.dataset.slot) : -1;
        if (Number.isInteger(activeSlot)
            && isExplorerSearchablePane(terminals[activeSlot])) {
            return activeSlot;
        }

        if (_focusedTerminalIndex !== -1
            && isExplorerSearchablePane(terminals[_focusedTerminalIndex])) {
            return _focusedTerminalIndex;
        }

        for (let i = 0; i < terminals.length; i++) {
            if (isExplorerSearchablePane(terminals[i])) {
                return i;
            }
        }
        return -1;
    }

    /* The highlighted string, when the current selection sits inside this
       explorer pane and is a single line — the useful case for seeding a find.
       Multi-line selections and selections in other panes are ignored. */
    function explorerSelectionQuery(index) {
        const selection = window.getSelection?.();
        if (!selection || selection.isCollapsed || !selection.rangeCount) {
            return '';
        }
        const query = (selection.toString() || '').trim();
        if (!query || query.length > 200 || /[\r\n]/.test(query)) {
            return '';
        }
        const card = document.querySelector(`.explorer-pane[data-slot="${index}"]`);
        if (!card
            || !card.contains(selection.anchorNode)
            || !card.contains(selection.focusNode)) {
            return '';
        }
        return query;
    }

    document.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey || event.code !== 'KeyF') {
            return;
        }
        const index = findExplorerSearchTargetIndex();
        if (index === -1) {
            return;
        }
        if (!focusExplorerSearch(index, explorerSelectionQuery(index))) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
    });

    /* Ctrl+Shift+F opens the scrollback search overlay on the focused terminal
       pane (finding 10.3); makeTerminal's custom key handler keeps xterm from
       consuming the shortcut first. */
    function isTerminalSearchablePane(pane) {
        return Boolean(pane?.term && pane?.searchAddon);
    }

    function findTerminalSearchTargetIndex() {
        const activeCard = document.activeElement?.closest?.('.terminal-container');
        const activeSlot = activeCard ? Number(activeCard.dataset.slot) : -1;
        if (Number.isInteger(activeSlot) && isTerminalSearchablePane(terminals[activeSlot])) {
            return activeSlot;
        }
        if (_focusedTerminalIndex !== -1 && isTerminalSearchablePane(terminals[_focusedTerminalIndex])) {
            return _focusedTerminalIndex;
        }
        return -1;
    }

    function wireTerminalSearchOverlay(index, overlay) {
        const input = overlay.querySelector(`[data-terminal-search-input="${index}"]`);
        const findNext = () => {
            const query = input?.value || '';
            if (query) terminals[index]?.searchAddon?.findNext(query);
        };
        const findPrevious = () => {
            const query = input?.value || '';
            if (query) terminals[index]?.searchAddon?.findPrevious(query);
        };
        input?.addEventListener('keydown', event => {
            event.stopPropagation();
            if (event.key === 'Enter') {
                event.preventDefault();
                if (event.shiftKey) findPrevious(); else findNext();
            } else if (event.key === 'Escape') {
                event.preventDefault();
                closeTerminalSearch(index);
            }
        });
        input?.addEventListener('input', () => {
            const query = input.value || '';
            if (query) {
                terminals[index]?.searchAddon?.findNext(query, { incremental: true });
            }
        });
        overlay.querySelector(`[data-terminal-search-prev="${index}"]`)
            ?.addEventListener('click', findPrevious);
        overlay.querySelector(`[data-terminal-search-next="${index}"]`)
            ?.addEventListener('click', findNext);
        overlay.querySelector(`[data-terminal-search-close="${index}"]`)
            ?.addEventListener('click', () => closeTerminalSearch(index));
    }

    function openTerminalSearch(index) {
        const pane = terminals[index];
        const wrapper = document.getElementById(`tw-${index}`);
        if (!pane || !wrapper || !isTerminalSearchablePane(pane)) {
            return false;
        }
        let overlay = wrapper.querySelector('.terminal-search-overlay');
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.className = 'terminal-search-overlay';
            overlay.innerHTML = `
                <input
                    type="search"
                    class="terminal-search-input"
                    data-terminal-search-input="${index}"
                    placeholder="Find in terminal"
                    autocomplete="off"
                    spellcheck="false"
                    aria-label="Find in terminal scrollback"
                >
                <button type="button" class="explorer-search-btn" data-terminal-search-prev="${index}" title="Previous match (Shift+Enter)" aria-label="Previous match">↑</button>
                <button type="button" class="explorer-search-btn" data-terminal-search-next="${index}" title="Next match (Enter)" aria-label="Next match">↓</button>
                <button type="button" class="explorer-search-btn" data-terminal-search-close="${index}" title="Close search (Escape)" aria-label="Close search">×</button>
            `;
            wrapper.appendChild(overlay);
            wireTerminalSearchOverlay(index, overlay);
        }
        overlay.hidden = false;
        const input = overlay.querySelector('input');
        input?.focus();
        input?.select();
        return true;
    }

    function closeTerminalSearch(index) {
        const wrapper = document.getElementById(`tw-${index}`);
        const overlay = wrapper?.querySelector('.terminal-search-overlay');
        if (overlay) {
            overlay.hidden = true;
        }
        terminals[index]?.searchAddon?.clearDecorations?.();
        /* Returning focus to the terminal re-marks it active via the delegated
           focusin handler. */
        terminals[index]?.term?.focus();
    }

    document.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey || event.code !== 'KeyF') {
            return;
        }
        const index = findTerminalSearchTargetIndex();
        if (index === -1 || !openTerminalSearch(index)) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
    });

    function isEditableShortcutTarget(target) {
        if (!(target instanceof Element)) {
            return false;
        }
        return Boolean(target.closest('input, textarea, select, .voice-ptt-keybind'))
            || target.isContentEditable;
    }

    document.addEventListener('keydown', event => {
        if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
            return;
        }
        if (!/^[1-9]$/.test(event.key) || isEditableShortcutTarget(event.target)) {
            return;
        }

        const targetGroup = getSessionGroupByNumber(Number(event.key));
        if (!targetGroup || targetGroup.group_id === activeGroupId) {
            return;
        }

        event.preventDefault();
        switchGroup(targetGroup.group_id);
    });

    document.addEventListener('keydown', async event => {
        if (!_voicePrefs.pttEnabled || !_voicePrefs.pttKeybind) return;
        if (_pttActive || _pttProcessing) return;
        if (!_matchesPttKeybind(event, _voicePrefs.pttKeybind)) return;

        event.preventDefault();
        _pttActive = true;
        _pttProcessing = true;
        _pttStopRequested = false;

        try {
            const index = _voiceActiveIndex !== -1 ? _voiceActiveIndex : _findPttTerminalIndex();
            if (index === -1) return;
            if (!_voiceState[index]?.recording) {
                await _startVoice(index);
            }
            /* The key was released while the async start was in flight —
               stop immediately so no stale capture/indicator survives. */
            if (_pttStopRequested && _voiceState[index]?.recording) {
                await _stopVoice(index);
            }
        } finally {
            _pttProcessing = false;
            _pttStopRequested = false;
        }
    });

    document.addEventListener('keyup', async event => {
        if (!_pttActive) return;
        if (!_pttKeybindReleasedBy(event, _voicePrefs.pttKeybind)) return;

        event.preventDefault();
        _pttActive = false;
        if (_pttProcessing) {
            _pttStopRequested = true;
            return;
        }
        _pttProcessing = true;

        try {
            if (_voiceActiveIndex !== -1) {
                await _stopVoice(_voiceActiveIndex);
            }
        } finally {
            _pttProcessing = false;
        }
    });


    function _showTermCtxMenu(x, y, index) {
        _dismissTermCtxMenu();
        const term = terminals[index].term;
        const menu = document.createElement('div');
        menu.id = 'term-ctx-menu';

        /* keep menu inside viewport */
        const vw = window.innerWidth, vh = window.innerHeight;
        const mw = 140, mh = 72;
        menu.style.left = `${Math.min(x, vw - mw - 8)}px`;
        menu.style.top  = `${Math.min(y, vh - mh - 8)}px`;

        const items = [
            { label: 'Copy',  shortcut: 'Ctrl+Shift+C', action() {
                _copyText(term.getSelection());
            }},
            { label: 'Paste', shortcut: 'Ctrl+V', action() { _pasteToTerminal(index); }},
        ];

        items.forEach(({ label, shortcut, action }) => {
            const btn = document.createElement('button');
            btn.innerHTML = `<span>${label}</span><span class="shortcut">${shortcut}</span>`;
            btn.addEventListener('mousedown', e => { e.preventDefault(); action(); _dismissTermCtxMenu(); });
            menu.appendChild(btn);
        });

        document.body.appendChild(menu);
        document.addEventListener('mousedown', _dismissTermCtxMenu, { once: true });
    }

    function _dismissTermCtxMenu() {
        document.getElementById('term-ctx-menu')?.remove();
    }

    /* ─────────────────────────────────────────────
       Attach / show xterm into a wrapper
    ───────────────────────────────────────────── */
    function attachTerminal(index) {
        const wrapper = document.getElementById(`tw-${index}`);
        const canvas  = document.getElementById(`tcanvas-${index}`);
        const ph      = document.getElementById(`ph-${index}`);

        if (!wrapper || !canvas || terminals[index]._attached || !terminals[index].term) return;

        if (ph) ph.remove();

        terminals[index].term.open(canvas);
        /* A session (group) with its own font/size (OD-14) keeps it across
           pane rebuilds and splits — new panes join their session's font. */
        applyGroupFontOverride(index);

        _wireClipboard(index);

        terminals[index]._attached = true;
        terminals[index]._fitReady = false;
        terminals[index]._pendingOutput = terminals[index]._pendingOutput || '';
        observeTerminalResize(index);
        scheduleFit(index);
    }

    /* ─────────────────────────────────────────────
       Update status badge for a single terminal
    ───────────────────────────────────────────── */
    function setStatus(index, status) {
        const dot   = document.getElementById(`tdot-${index}`);
        const label = document.getElementById(`tlabel-${index}`);
        if (!dot || !label) return;

        const map = {
            pending      : 'Pending',
            connecting   : 'Connecting…',
            connected    : 'Connected',
            disconnected : 'Disconnected',
            error        : 'Error'
        };

        dot.className   = `status-dot ${status}`;
        label.textContent = map[status] || status;
    }

    /* ─────────────────────────────────────────────
       Initial load — build grid, set up sessions
    ───────────────────────────────────────────── */
    async function initialLoad() {
        const loadToken = ++activeLoadToken;
        const label = document.getElementById('sessionLabel');
        const grid  = document.getElementById('terminalsGrid');
        try {
            label.textContent = 'Loading…';
            await loadSessionGroups();
            if (loadToken !== activeLoadToken) {
                return;
            }
            if (!activeGroupId) {
                await resetSessionView();
                return;
            }

            const requestedGroupId = activeGroupId;
            const resp = await fetch(getSessionApiPath(requestedGroupId));
            if (!resp.ok) throw new Error(`Server returned ${resp.status}`);
            const data = await resp.json();
            if (loadToken !== activeLoadToken || requestedGroupId !== activeGroupId) {
                return;
            }

            if (!data.sessions || data.sessions.length === 0) {
                await resetSessionView();
                return;
            }

            /* When this rebuild is driven by a terminal close, overlay each
               surviving pane's captured explorer/browser state onto the fetched
               session objects so the rebuild seeds and restores it rather than
               resetting siblings (ISSUE-2026-027). */
            const closeClientState = pendingCloseClientState?.groupId === requestedGroupId
                ? pendingCloseClientState.stateBySessionId
                : null;
            if (closeClientState) {
                data.sessions.forEach(entry => {
                    const snapshot = closeClientState[entry.session_id];
                    if (!snapshot) {
                        return;
                    }
                    if (snapshot.type === 'explorer') {
                        entry.explorer_tree_open = snapshot.explorer_tree_open;
                        entry.explorer_git_open = snapshot.explorer_git_open;
                        entry.explorer_open_tabs = snapshot.explorer_open_tabs;
                        entry.explorer_active_tab = snapshot.explorer_active_tab;
                        entry.explorer_tab_views = snapshot.explorer_tab_views;
                    } else if (snapshot.type === 'browser') {
                        entry.initial_command = snapshot.browser_url;
                    }
                });
            }

            applyConfiguredSurfaceMode(data, { refit: gridBuilt });
            const expectedLayoutClass = getLayoutClass(data.sessions.length, data.layout);
            const usingCurrentView = (
                gridBuilt
                && visibleGroupId === requestedGroupId
                && terminals.length === data.sessions.length
                && (grid.className === expectedLayoutClass || grid.className === 'layout-split-local')
                && hasMatchingSessionViews(sessionIds, terminals, data.sessions)
            );
            let restoredFromCache = false;

            if (!usingCurrentView && gridBuilt && visibleGroupId && visibleGroupId !== requestedGroupId) {
                cacheVisibleGroupView(visibleGroupId);
            }

            if (!usingCurrentView) {
                const cached = cachedGroupViews.get(requestedGroupId);
                if (cached) {
                    const cachedMatches = (
                        cached.terminals?.length === data.sessions.length
                        && (cached.className === expectedLayoutClass || cached.className === 'layout-split-local')
                        && hasMatchingSessionViews(cached.sessionIds || [], cached.terminals || [], data.sessions)
                    );
                    if (cachedMatches) {
                        restoredFromCache = restoreCachedGroupView(requestedGroupId);
                    } else {
                        dropCachedGroupView(requestedGroupId);
                    }
                }
            }

            grid.style.display = '';   // make sure it’s visible
            if (!usingCurrentView && !restoredFromCache) {
                buildGrid(data.sessions, data.layout);
                applyWorkspaceLayoutSnapshot(data.workspace_layout, data.sessions.length);
            }

            const attachedIndices = [];
            data.sessions.forEach((session, i) => {
                if (!terminals[i]) {
                    return;
                }
                terminals[i]._session = session;
                setStatus(i, session.status);
                sessionIds[i] = session.session_id;
                setSessionRoute(session.session_id, requestedGroupId, i);

                if (session.status === 'connected' && isBrowserSession(session)) {
                    document.getElementById(`ph-${i}`)?.remove();
                } else if (session.status === 'connected' && isExplorerSession(session)) {
                    const closeSnapshot = closeClientState ? closeClientState[session.session_id] : null;
                    if (closeSnapshot && closeSnapshot.type === 'explorer') {
                        restoreExplorerPaneFromClose(i, closeSnapshot);
                    } else {
                        /* First show goes through the viewer entry point (empty
                           Preview tab + persisted tab/preview restore), never a
                           bare root load — a root fetch racing the restore could
                           resolve last and clobber the Preview tab's own path. */
                        syncExplorerPane(i);
                        restoreExplorerSidebarState(i);
                    }
                } else if (session.status === 'connected') {
                    if (!terminals[i]._attached) {
                        attachTerminal(i);
                    }
                    attachedIndices.push(i);
                } else if (session.status === 'error') {
                    showPlaceholderError(i, session.error_message || 'Connection failed');
                } else if (isRetryableDisconnect(session)) {
                    showPlaceholderDisconnected(i);
                }
            });

            const pendingRestore = pendingSplitRestore?.groupId === requestedGroupId
                ? pendingSplitRestore
                : null;
            if (pendingRestore) {
                const restoredRects = data.sessions
                    .map(session => pendingRestore.rectsBySessionId[session.session_id])
                    .filter(Boolean);
                if (restoredRects.length === data.sessions.length && restoredRects.length > 0) {
                    originalSplitSlotCount = Number(
                        pendingRestore.originalSplitSlotCount || originalSplitSlotCount || data.sessions.length
                    );
                    splitSlotRects = cloneSplitSlotRects(restoredRects);
                    /* A valid close preserves the grid's bounding box, so the
                       pre-close track weights map 1:1 onto the reflowed grid and
                       user-set proportions survive (ISSUE-2026-022). */
                    splitColumnWeights = cloneSplitTrackWeights(pendingRestore.splitColumnWeights);
                    splitRowWeights = cloneSplitTrackWeights(pendingRestore.splitRowWeights);
                    applySplitSlotGeometry({ fit: false });
                }
                pendingSplitRestore = null;
            }
            if (closeClientState) {
                pendingCloseClientState = null;
            }

            updateSessionChrome(data.sessions.length, requestedGroupId);
            const restoredViewportStates = (usingCurrentView || restoredFromCache)
                ? new Map(attachedIndices.map(index => [
                    index,
                    restoredFromCache
                        ? (terminals[index]?._cachedTerminalViewport || captureTerminalViewportState(terminals[index]))
                        : captureTerminalViewportState(terminals[index])
                ]))
                : null;

            if (!usingCurrentView && !restoredFromCache) {
                await ensureAttachedTerminalsReady(attachedIndices);
            }
            if (loadToken !== activeLoadToken || requestedGroupId !== activeGroupId) {
                return;
            }

            if (!usingCurrentView && !restoredFromCache && socket) {
                /* Every pane joins its session room — including explorer and
                   browser panes, which have no output stream but still need
                   the room-scoped session_status updates. */
                data.sessions.forEach(session => {
                    socket.emit('join_session', { session_id: session.session_id });
                });
            }

            const stillCurrent = () => loadToken === activeLoadToken && requestedGroupId === activeGroupId;
            if (usingCurrentView || restoredFromCache) {
                await redrawAttachedTerminals(attachedIndices, {
                    forceResize: false,
                    isCurrent: stillCurrent
                });
                restoredViewportStates?.forEach((state, index) => {
                    restoreTerminalViewportState(terminals[index], state, { isCurrent: stillCurrent });
                });
                if (restoredFromCache) {
                    terminals.forEach(terminal => {
                        if (terminal) {
                            terminal._cachedTerminalViewport = null;
                        }
                    });
                }
            } else {
                await redrawAttachedTerminalsLikeFullscreen(attachedIndices, {
                    isCurrent: stillCurrent
                });
            }
        } catch (e) {
            if (loadToken !== activeLoadToken) {
                return;
            }
            console.error('Initial load failed:', e);
            label.textContent = `Load error: ${e.message}`;
            grid.style.display = 'none';
            document.getElementById('emptyState').classList.add('visible');
        }
    }

    /* ─────────────────────────────────────────────
       Show error / disconnected / retry states inside a terminal pane
    ───────────────────────────────────────────── */
    /* Explorer and browser panes have no live connection to retry. */
    function isRetryableDisconnect(session) {
        return session.status === 'disconnected'
            && !isExplorerSession(session)
            && !isBrowserSession(session);
    }

    function ensurePanePlaceholder(index) {
        let ph = document.getElementById(`ph-${index}`);
        if (!ph) {
            const wrapper = document.getElementById(`tw-${index}`);
            if (!wrapper) return null;
            ph = document.createElement('div');
            ph.className = 'placeholder';
            ph.id = `ph-${index}`;
            wrapper.appendChild(ph);
        }
        return ph;
    }

    function showPlaceholderRetryState(index, { stateClass, title, message }) {
        const ph = ensurePanePlaceholder(index);
        if (!ph) return;
        const renderedState = `${stateClass}|${message}`;
        if (ph.dataset.retryState === renderedState) {
            return;
        }
        ph.dataset.retryState = renderedState;
        ph.classList.remove('ph-error', 'ph-disconnected');
        ph.classList.add(stateClass);
        ph.innerHTML = `
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5">
                <circle cx="12" cy="12" r="10"/>
                <line x1="12" y1="8" x2="12" y2="12"/>
                <line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            <strong>${title}</strong>
            <span style="color:#aaa;word-break:break-all">${escHtml(message)}</span>
            <button type="button" class="btn btn-neutral ph-retry-btn" data-retry-session="${index}">
                Retry connection
            </button>`;
        ph.querySelector(`[data-retry-session="${index}"]`)
            .addEventListener('click', () => retrySessionConnection(index));
    }

    function showPlaceholderError(index, msg) {
        showPlaceholderRetryState(index, {
            stateClass: 'ph-error',
            title: 'Connection Error',
            message: msg
        });
    }

    function showPlaceholderDisconnected(index, msg = 'The connection ended.') {
        showPlaceholderRetryState(index, {
            stateClass: 'ph-disconnected',
            title: 'Disconnected',
            message: msg
        });
    }

    function showPlaceholderConnecting(index) {
        const ph = ensurePanePlaceholder(index);
        if (!ph) return;
        delete ph.dataset.retryState;
        ph.classList.remove('ph-error', 'ph-disconnected');
        ph.innerHTML = `
            <div class="spinner"></div>
            <span style="font-size:.78rem">Connecting…</span>`;
    }

    async function retrySessionConnection(index) {
        const sessionId = sessionIds[index];
        if (!sessionId) return;
        showPlaceholderConnecting(index);
        try {
            const response = await fetch(
                `/api/sessions/${encodeURIComponent(sessionId)}/reconnect`,
                { method: 'POST' }
            );
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Reconnect failed with status ${response.status}`);
            }
            /* Discard the dead connection's output before the fresh stream lands. */
            terminals[index]?.term?.reset?.();
        } catch (e) {
            showPlaceholderError(index, e.message);
        }
    }

    async function loadSessionGroups() {
        const response = await fetch('/api/session-groups');
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to load session tabs');
        }

        const previousActiveGroupId = activeGroupId;
        const previousGroupIds = knownGroupIds.slice();
        sessionGroups = Array.isArray(data.groups) ? data.groups : [];
        knownGroupIds = sessionGroups.map(group => group.group_id);
        previousGroupIds
            .filter(groupId => !knownGroupIds.includes(groupId))
            .forEach(groupId => {
                if (groupId !== visibleGroupId) {
                    dropCachedGroupView(groupId);
                }
            });
        const newestGroupId = sessionGroups.length
            ? sessionGroups[sessionGroups.length - 1].group_id
            : '';
        const hasNewGroup = previousGroupIds.length > 0
            && knownGroupIds.some(groupId => !previousGroupIds.includes(groupId));

        if (activeGroupId && !getGroupById(activeGroupId)) {
            activeGroupId = '';
        }
        if (!activeGroupId && sessionGroups.length > 0) {
            activeGroupId = newestGroupId;
        } else if (hasNewGroup && newestGroupId && activeGroupId !== newestGroupId) {
            activeGroupId = newestGroupId;
        }
        syncLocationToGroup(activeGroupId);
        renderSessionTabs();
        return previousActiveGroupId !== activeGroupId;
    }

    function isPywebviewAvailable() {
        return Boolean(window.pywebview && window.pywebview.api);
    }

    function updateFullscreenButton() {
        const button = document.getElementById('fullscreenBtn');
        if (!button) return;

        const isBrowserFullscreen = Boolean(document.fullscreenElement);
        const active = isPywebviewAvailable() ? nativeFullscreen : isBrowserFullscreen;
        const label = active ? 'Exit fullscreen' : 'Enter fullscreen';
        button.innerHTML = active ? FULLSCREEN_EXIT_ICON : FULLSCREEN_ENTER_ICON;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }

    async function syncNativeFullscreenState() {
        if (!isPywebviewAvailable() || !window.pywebview.api.get_session_fullscreen_state) {
            nativeFullscreen = false;
            updateFullscreenButton();
            return;
        }

        try {
            const result = await window.pywebview.api.get_session_fullscreen_state();
            nativeFullscreen = Boolean(result && result.ok && result.is_fullscreen);
        } catch (error) {
            console.error('Fullscreen state sync failed:', error);
            nativeFullscreen = false;
        } finally {
            updateFullscreenButton();
        }
    }

    async function resetFullscreenState() {
        try {
            if (isPywebviewAvailable() && window.pywebview.api.exit_session_fullscreen) {
                const result = await window.pywebview.api.exit_session_fullscreen();
                if (result && result.ok) {
                    nativeFullscreen = false;
                }
                return;
            }

            if (document.fullscreenElement) {
                await document.exitFullscreen();
            }
        } catch (error) {
            console.error('Fullscreen reset failed:', error);
        } finally {
            nativeFullscreen = false;
            updateFullscreenButton();
        }
    }

    async function toggleFullscreen() {
        try {
            if (isPywebviewAvailable()) {
                const result = await window.pywebview.api.toggle_session_fullscreen
                    ? await window.pywebview.api.toggle_session_fullscreen()
                    : null;
                if (result && result.ok) {
                    nativeFullscreen = !nativeFullscreen;
                    updateFullscreenButton();
                }
                return;
            }

            if (document.fullscreenElement) {
                await document.exitFullscreen();
            } else {
                await document.documentElement.requestFullscreen();
            }
        } catch (error) {
            console.error('Fullscreen toggle failed:', error);
        } finally {
            updateFullscreenButton();
        }
    }

    async function resetSessionView() {
        await resetFullscreenState();
        teardownCurrentGrid();
        document.getElementById('terminalsGrid').className = '';
        document.getElementById('terminalsGrid').innerHTML = '';
        document.getElementById('terminalsGrid').style.display = '';
        document.getElementById('terminalsGrid').style.removeProperty('--grid-columns');
        document.getElementById('terminalsGrid').style.removeProperty('--grid-rows');
        document.getElementById('terminalsGrid').style.removeProperty('--split-grid-columns');
        document.getElementById('terminalsGrid').style.removeProperty('--split-grid-rows');
        document.getElementById('terminalsGrid').style.gridTemplateColumns = '';
        document.getElementById('terminalsGrid').style.gridTemplateRows = '';
        splitSlotRects = null;
        splitColumnWeights = null;
        splitRowWeights = null;
        pendingSplitRestore = null;
        pendingCloseClientState = null;
        clearActiveGridResize();
        clearResizeHandles();
        document.getElementById('emptyState').classList.add('visible');
        document.getElementById('sessionLabel').textContent = sessionGroups.length ? 'No terminals in this session' : 'No sessions';
        document.title = 'GridVibe — Terminals';
    }

    function logSessionWindowAction(action, details = {}) {
        console.info(`[GridVibe Sessions] ${action}`, details);
    }

    async function goToSettings(event) {
        if (event) {
            event.preventDefault();
        }

        if (window.pywebview?.api?.open_launcher_window) {
            try {
                logSessionWindowAction('+ New Session clicked', {
                    pywebview: true,
                    preserve_fullscreen: true
                });
                const result = await window.pywebview.api.open_launcher_window();
                logSessionWindowAction('open_launcher_window result', result || {});
                if (result?.ok) {
                    return false;
                }
            } catch (error) {
                console.error('[GridVibe Sessions] open_launcher_window failed:', error);
            }
        }

        await resetFullscreenState();
        window.open('/', 'gridvibe-launcher');
        logSessionWindowAction('Opened browser launcher window fallback');
        return false;
    }

    async function switchGroup(groupId) {
        if (!groupId || groupId === activeGroupId) {
            return;
        }

        /* Safety: broadcast typing never survives a group switch. */
        setBroadcastInput(false);
        activeGroupId = groupId;
        syncLocationToGroup(activeGroupId);
        renderSessionTabs();
        await initialLoad();
    }

    /* ─────────────────────────────────────────────
       Status refresh (no grid rebuild) — triggered by
       session_groups_updated pushes, with a slow poll
       as fallback while the socket is down
    ───────────────────────────────────────────── */
    let statusRefreshTimer = null;
    function scheduleStatusRefresh() {
        if (statusRefreshTimer) return;
        statusRefreshTimer = setTimeout(() => {
            statusRefreshTimer = null;
            refreshStatuses();
        }, 200);
    }

    async function refreshStatuses() {
        if (!gridBuilt) { initialLoad(); return; }
        try {
            const groupChanged = await loadSessionGroups();
            if (groupChanged) {
                await initialLoad();
                return;
            }
            if (!activeGroupId) {
                await resetSessionView();
                return;
            }

            const resp = await fetch(getSessionApiPath());
            const data = await resp.json();
            if (!data.sessions || data.sessions.length === 0) {
                await resetSessionView();
                return;
            }

            const expectedLayoutClass = getLayoutClass(data.sessions.length, data.layout);
            const sessionViewsChanged = !hasMatchingSessionViews(sessionIds, terminals, data.sessions);
            const currentLayoutClass = document.getElementById('terminalsGrid').className;
            if (
                terminals.length !== data.sessions.length
                || (currentLayoutClass !== expectedLayoutClass && currentLayoutClass !== 'layout-split-local')
                || sessionViewsChanged
            ) {
                await initialLoad();
                return;
            }

            data.sessions.forEach((session, i) => {
                if (!terminals[i]) {
                    return;
                }
                terminals[i]._session = session;
                setStatus(i, session.status);
                sessionIds[i] = session.session_id;
                setSessionRoute(session.session_id, activeGroupId, i);
                if (session.status === 'connected' && isBrowserSession(session)) {
                    document.getElementById(`ph-${i}`)?.remove();
                } else if (session.status === 'connected' && isExplorerSession(session)) {
                    syncExplorerPane(i);
                } else if (session.status === 'connected' && !terminals[i]?._attached) {
                    attachTerminal(i);
                    redrawAttachedTerminals([i], { forceResize: true });
                } else if (session.status === 'error' && !terminals[i]?._attached) {
                    showPlaceholderError(i, session.error_message || 'Connection failed');
                } else if (isRetryableDisconnect(session)) {
                    showPlaceholderDisconnected(i);
                }
            });
        } catch (e) {
            console.error('Refresh failed:', e);
        }
    }

    /* ─────────────────────────────────────────────
       Close session group
    ───────────────────────────────────────────── */
    async function closeSessionGroup(groupId = activeGroupId) {
        if (!groupId) {
            return;
        }

        if (!(await confirmCloseSessionGroup(groupId))) {
            return;
        }

        try {
            const closingGroupId = groupId;
            const closedActiveGroup = activeGroupId === closingGroupId;
            const closedVisibleGroup = visibleGroupId === closingGroupId;
            const response = await fetch(getSessionApiPath(closingGroupId), { method: 'DELETE' });
            if (!response.ok) {
                throw new Error(`Close session failed with status ${response.status}`);
            }

            if (closedVisibleGroup) {
                teardownCurrentGrid();
            } else {
                dropCachedGroupView(closingGroupId);
            }
            if (closedActiveGroup) {
                activeGroupId = '';
            }
            await loadSessionGroups();
            if (sessionGroups.length === 0) {
                await _closeWindowAfterLastSession();
                return;
            }
            if (closedActiveGroup || closedVisibleGroup) {
                await initialLoad();
            }
        } catch (e) {
            console.error('Close session failed:', e);
        }
    }

    async function closeCurrentSession() {
        await closeSessionGroup(activeGroupId);
    }

    async function _closeWindowAfterLastSession() {
        logSessionWindowAction('Last session closed — closing window');
        if (isPywebviewAvailable() && window.pywebview.api.close_session_window) {
            try {
                await window.pywebview.api.close_session_window();
                return;
            } catch (e) {
                console.error('[GridVibe Sessions] close_session_window failed:', e);
            }
        }
        window.close();
    }

    /* ─────────────────────────────────────────────
       Resize
    ───────────────────────────────────────────── */
    window.addEventListener('resize', () => {
        terminals.forEach((terminal, index) => {
            if (terminal._attached) scheduleFit(index);
        });
        renderResizeHandles();
    });
    window.addEventListener('pointermove', updateGridResize);
    window.addEventListener('pointerup', finishGridResize);
    window.addEventListener('pointercancel', finishGridResize);

    /* ─────────────────────────────────────────────
       Helpers
    ───────────────────────────────────────────── */

    document.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || !event.shiftKey
            || event.altKey || event.code !== 'KeyV' || isEditableShortcutTarget(event.target)) {
            return;
        }
        const index = findExplorerMarkdownPreviewTargetIndex();
        if (index === -1) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        setExplorerFileView(
            index,
            activeExplorerFileView(index) === 'preview' ? 'source' : 'preview'
        );
    });


    let _terminalToastTimer = null;

    /* Small transient toast for feedback that has no dedicated surface (e.g.
       a completed download). Auto-dismisses; announced via role="status". */
    function showTerminalToast(message, type = '') {
        let toast = document.getElementById('terminalToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'terminalToast';
            toast.className = 'terminal-toast';
            toast.setAttribute('role', 'status');
            toast.setAttribute('aria-live', 'polite');
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.className = `terminal-toast${type ? ` ${type}` : ''} visible`;
        clearTimeout(_terminalToastTimer);
        _terminalToastTimer = setTimeout(() => {
            toast.classList.remove('visible');
        }, 4000);
    }


    /* ─────────────────────────────────────────────
       Socket init — happens AFTER all function defs
       so that a CDN failure cannot kill the script.
    ───────────────────────────────────────────── */
    try {
        socket = io();

        socket.on('terminal_output', ({ session_id, data }) => {
            const target = resolveSessionTarget(session_id);
            if (!target) return;
            if (pendingModeSwitchSessionIds.has(session_id)) return;
            if (!target.terminal.term) return;

            if (!target.active) {
                if (!target.terminal._attached) {
                    target.terminal._pendingOutput = (target.terminal._pendingOutput || '') + data;
                    return;
                }
                target.terminal.term.write(data);
                return;
            }

            const { index, terminal } = target;
            if (!terminal._attached) attachTerminal(index);
            if (!terminal._fitReady) {
                terminal._pendingOutput = (terminal._pendingOutput || '') + data;
                scheduleFit(index);
                return;
            }
            terminal.term.write(data);
        });

        socket.on('session_status', (session) => {
            const target = resolveSessionTarget(session.session_id);
            if (!target) return;

            target.terminal._session = session;
            if (!target.active) {
                return;
            }

            const { index, terminal } = target;
            setStatus(index, session.status);
            if (
                isExplorerPaneInstance(terminal) !== isExplorerSession(session)
                || isBrowserPaneInstance(terminal) !== isBrowserSession(session)
            ) {
                if (pendingModeSwitchSessionIds.has(session.session_id)) {
                    return;
                }
                initialLoad();
                return;
            }
            if (session.status === 'connected' && isBrowserSession(session)) {
                document.getElementById(`ph-${index}`)?.remove();
            } else if (session.status === 'connected' && isExplorerSession(session)) {
                syncExplorerPane(index);
            } else if (session.status === 'connected' && !terminal._attached) {
                attachTerminal(index);
                redrawAttachedTerminals([index], { forceResize: true });
            } else if (session.status === 'connected') {
                /* A reconnected pane keeps its attached xterm; drop any
                   error/disconnected overlay left behind by the retry flow. */
                document.getElementById(`ph-${index}`)?.remove();
                scheduleFit(index);
            } else if (session.status === 'error') {
                showPlaceholderError(index, session.error_message || 'Connection failed');
            } else if (isRetryableDisconnect(session)) {
                showPlaceholderDisconnected(index);
            }
        });

        socket.on('app_config_updated', (message) => {
            applyAppConfigUpdate(message || {});
        });

        socket.on('session_groups_updated', () => {
            scheduleStatusRefresh();
        });

        /* Reconcile anything missed while the socket was disconnected.
           Skipped on the first connect — initialLoad() covers boot. */
        let hadSocketConnection = false;
        socket.on('connect', () => {
            if (!hadSocketConnection) {
                hadSocketConnection = true;
                return;
            }
            scheduleStatusRefresh();
            reconcileAppConfigTheme();
        });

        /* ── Voice transcription results ── */
        socket.on('voice_result', ({ session_id, text, final: isFinal }) => {
            const index = _voiceIndexForSession(session_id);
            if (index === -1) return;

            if (isFinal && text) {
                /* A committed transcript honours Broadcast typing the same way
                   keyboard input does (ISSUE-2026-026): deliver to the recording
                   pane, then fan out to every other plain pane through the shared
                   broadcast filter. Interim previews above stay on the recording
                   pane only. */
                _sendToTerminal(index, text);
                broadcastInputToPeers(index, text);
                _clearVoicePreview(index);
            } else if (text) {
                _showVoicePreview(index, text);
            }
        });

        socket.on('voice_status', async ({ session_id, status, message }) => {
            _voiceLog('Received voice status from server', {
                sessionId: session_id || null,
                status,
                message: message || ''
            });
            if (status === 'error') {
                console.error('Voice error:', message);
                const index = session_id ? _voiceIndexForSession(session_id) : -1;
                if (index !== -1) await _stopVoice(index, { notifyServer: false });
                if (index !== -1 && message) {
                    _setVoicePanelStatus(index, message);
                }
                return;
            }

            const index = session_id ? _voiceIndexForSession(session_id) : -1;
            if (index === -1) return;

            if (status === 'listening') {
                if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'whisper') {
                    _setVoicePanelStatus(index, 'faster-whisper is ready. Capture is buffering 16 kHz PCM locally and will transcribe when recording stops.');
                } else {
                    _setVoicePanelStatus(index, 'Voice service is listening. Capture is streaming through AudioWorklet resampling at 16 kHz PCM.');
                }
            } else if (status === 'stopped') {
                _setVoicePanelStatus(index, 'Voice service stopped for this terminal. The last capture diagnostics remain visible above.');
            }
        });
    } catch (e) {
        console.warn('Socket.IO unavailable — using polling only:', e.message);
        document.getElementById('sessionLabel').title =
            'Real-time disabled — using polling';
    }

    window.addEventListener('pywebviewready', () => {
        syncNativeFullscreenState();
    });
    window.addEventListener('focus', () => {
        _loadVoiceServiceStatus();
        reconcileAppConfigTheme();
    });
    window.addEventListener('pageshow', () => {
        _loadVoiceServiceStatus();
        reconcileAppConfigTheme();
    });
    document.addEventListener('fullscreenchange', updateFullscreenButton);
    /* ─────────────────────────────────────────────
       Boot
    ───────────────────────────────────────────── */
    applySurfaceMode(normalizeSurfaceMode(DEFAULT_SURFACE_MODE) === 'max');
    applyTopbarVisibility(getStoredTopbarVisible());
    setupAppConfigUpdateListeners();
    updateFullscreenButton();
    _loadVoicePrefsFromServer();
    _loadVoiceServiceStatus();
    initialLoad();
    /* Fallback reconciliation poll: session_status + session_groups_updated
       pushes keep the view current, so only poll while the socket is down. */
    const STATUS_POLL_FALLBACK_MS = 15000;
    setInterval(() => {
        if (socket && socket.connected) return;
        refreshStatuses();
    }, STATUS_POLL_FALLBACK_MS);
