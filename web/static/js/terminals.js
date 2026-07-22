    /* ── Theme management ── */
    const SURFACE_MODE_STORAGE_KEY = 'gridvibe.terminalSurfaceMode';
    const TOPBAR_VISIBILITY_STORAGE_KEY = 'gridvibe.terminalTopbarVisibility';
    const DEFAULT_SAVED_SESSION_ID = 'default-session';

    /* Stroke-style button icons (finding 7.2) — replace the old refresh/clear/
       fullscreen text glyphs so every action button shares one SVG icon language. */
    const TERMINAL_REFRESH_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 12a9 9 0 1 1-2.64-6.36"></path><polyline points="21 3 21 9 15 9"></polyline></svg>';
    const TERMINAL_CLEAR_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"></path><path d="M22 21H7"></path><path d="m5 11 9 9"></path></svg>';
    const FULLSCREEN_ENTER_ICON = '<svg class="fullscreen-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="15 3 21 3 21 9"></polyline><polyline points="9 21 3 21 3 15"></polyline><line x1="21" y1="3" x2="14" y2="10"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg>';
    const FULLSCREEN_EXIT_ICON = '<svg class="fullscreen-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="4 14 10 14 10 20"></polyline><polyline points="20 10 14 10 14 4"></polyline><line x1="14" y1="10" x2="21" y2="3"></line><line x1="3" y1="21" x2="10" y2="14"></line></svg>';
    const EXPLORER_DOWNLOAD_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>';
    // Markdown preview appearance control (ISSUE-2026-030): a stroke-style "type"
    // glyph opens the preset/font popover.
    const EXPLORER_MD_APPEARANCE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="4 7 4 4 20 4 20 7"></polyline><line x1="9" y1="20" x2="15" y2="20"></line><line x1="12" y1="4" x2="12" y2="20"></line></svg>';
    /* Guardrail 7 (audit N3): stroke-style currentColor SVGs replace the old
       emoji/text glyphs on the pane-header toggle buttons. */
    const TERMINAL_PROMPT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><polyline points="4 17 10 11 4 5"></polyline><line x1="12" y1="19" x2="20" y2="19"></line></svg>';
    const EXPLORER_MODE_FOLDER_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path></svg>';
    const BROWSER_MODE_GLOBE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>';
    const VOICE_MIC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>';
    const THEME_MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>';
    const THEME_SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>';





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

    /* ─────────────────────────────────────────────
       Voice input — mic capture → Socket.IO → configured STT backend
    ───────────────────────────────────────────── */
    const PAGE_DATASET = document.body?.dataset || {};
    const VOICE_WORKLET_MODULE_URL = PAGE_DATASET.voiceWorkletUrl || '/static/voice-capture-worklet.js';
    const VOICE_ENGINE = PAGE_DATASET.voiceEngine || 'vosk';
    const VOICE_MODEL = PAGE_DATASET.voiceModel || 'unknown';
    const VOICE_LANGUAGE = PAGE_DATASET.voiceLanguage || 'en-US';
    const VOICE_CAPTURE_PROFILES = Object.freeze({
        headset: Object.freeze({
            label: 'Headset',
            constraints: Object.freeze({
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false,
                channelCount: 1
            })
        }),
        laptop: Object.freeze({
            label: 'Laptop',
            constraints: Object.freeze({
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                channelCount: 1
            })
        })
    });
    const VOICE_PREFS_STORAGE_KEY = 'gridvibe.voice.capture.v1';
    const VOICE_TARGET_SAMPLE_RATE = 16000;
    const VOICE_CHUNK_SIZE = 640;
    const VOICE_DEFAULT_STATUS = 'Requested capture settings will be checked against the browser\'s actual applied settings after the microphone starts.';
    const VOICE_LOG_PREFIX = '[GridVibe Voice]';
    const _voiceState = {};
    const _voiceLastDiagnostics = {};
    const _voiceStatusMessages = {};
    let _voiceActiveIndex = -1;   // only one terminal may record at a time
    let _voicePrefs = _loadVoicePrefs();
    let _voiceServiceStatus = {
        enabled: PAGE_DATASET.voiceEnabled === 'true',
        engine: VOICE_ENGINE,
        model: VOICE_MODEL,
        language: VOICE_LANGUAGE,
        engine_available: null,
        service_running: null,
        service_url: ''
    };

    function _defaultVoicePrefs() {
        return {
            profile: 'laptop',
            deviceId: '',
            pttEnabled: false,
            pttKeybind: ''
        };
    }

    function _loadVoicePrefs() {
        const defaults = _defaultVoicePrefs();
        try {
            const raw = window.localStorage?.getItem(VOICE_PREFS_STORAGE_KEY);
            if (!raw) return defaults;
            const parsed = JSON.parse(raw);
            const profile = VOICE_CAPTURE_PROFILES[parsed?.profile]
                ? parsed.profile
                : defaults.profile;
            return {
                profile,
                deviceId: typeof parsed?.deviceId === 'string' ? parsed.deviceId : '',
                pttEnabled: typeof parsed?.pttEnabled === 'boolean' ? parsed.pttEnabled : false,
                pttKeybind: typeof parsed?.pttKeybind === 'string' ? parsed.pttKeybind : ''
            };
        } catch (_) {
            return defaults;
        }
    }

    function _saveVoicePrefs() {
        try {
            window.localStorage?.setItem(VOICE_PREFS_STORAGE_KEY, JSON.stringify(_voicePrefs));
        } catch (_) {}
        fetch('/api/voice-prefs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(_voicePrefs)
        }).catch(() => {});
    }

    async function _loadVoicePrefsFromServer() {
        try {
            const resp = await fetch('/api/voice-prefs');
            if (!resp.ok) return;
            const server = await resp.json();
            const defaults = _defaultVoicePrefs();
            const profile = VOICE_CAPTURE_PROFILES[server?.profile]
                ? server.profile : defaults.profile;
            _voicePrefs = {
                profile,
                deviceId: typeof server?.deviceId === 'string' ? server.deviceId : _voicePrefs.deviceId,
                pttEnabled: typeof server?.pttEnabled === 'boolean' ? server.pttEnabled : _voicePrefs.pttEnabled,
                pttKeybind: typeof server?.pttKeybind === 'string' ? server.pttKeybind : _voicePrefs.pttKeybind
            };
            try {
                window.localStorage?.setItem(VOICE_PREFS_STORAGE_KEY, JSON.stringify(_voicePrefs));
            } catch (_) {}
            terminals.forEach((_, terminalIndex) => _syncVoiceControls(terminalIndex));
        } catch (_) {}
    }

    function _voiceLog(message, details) {
        if (typeof details === 'undefined') {
            console.log(`${VOICE_LOG_PREFIX} ${message}`);
            return;
        }
        console.log(`${VOICE_LOG_PREFIX} ${message}`, details);
    }

    function _getVoiceConstraints(preferences = _voicePrefs, { includeDevice = true } = {}) {
        const profile = VOICE_CAPTURE_PROFILES[preferences.profile] || VOICE_CAPTURE_PROFILES.laptop;
        const constraints = { ...profile.constraints };
        if (includeDevice && preferences.deviceId) {
            constraints.deviceId = { exact: preferences.deviceId };
        }
        return constraints;
    }

    function _getVoicePanelElements(index) {
        return {
            control: document.querySelector(`[data-terminal-voice-control="${index}"]`)
        };
    }

    function _sanitizeVoiceDiagnostic(value) {
        if (value === undefined) return undefined;
        if (value === null) return null;
        if (Array.isArray(value)) {
            return value.map(item => _sanitizeVoiceDiagnostic(item));
        }
        if (typeof value === 'number') {
            return Number.isFinite(value) ? Number(value.toFixed(4)) : String(value);
        }
        if (typeof value === 'object') {
            const sanitized = {};
            Object.keys(value).sort().forEach(key => {
                const normalized = _sanitizeVoiceDiagnostic(value[key]);
                if (normalized !== undefined) {
                    sanitized[key] = normalized;
                }
            });
            return sanitized;
        }
        return value;
    }

    function _formatVoiceDiagnostic(value, fallback) {
        if (value === undefined || value === null) {
            return fallback;
        }
        const sanitized = _sanitizeVoiceDiagnostic(value);
        if (
            typeof sanitized === 'object' &&
            sanitized !== null &&
            !Array.isArray(sanitized) &&
            Object.keys(sanitized).length === 0
        ) {
            return fallback;
        }
        return JSON.stringify(sanitized, null, 2);
    }

    function _voiceSummaryMessage() {
        const parts = [
            `Engine: ${_voiceServiceStatus.engine || VOICE_ENGINE}`,
            `Model: ${_voiceServiceStatus.model || VOICE_MODEL}`,
            `Language: ${_voiceServiceStatus.language || VOICE_LANGUAGE}`,
            'Target PCM: 16 kHz mono'
        ];
        if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'vosk' && typeof _voiceServiceStatus.service_running === 'boolean') {
            parts.push(`Service: ${_voiceServiceStatus.service_running ? 'running' : 'starting on demand'}`);
        }
        if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'whisper') {
            parts.push('Mode: final transcript on stop');
        }
        if (typeof _voiceServiceStatus.startup_timeout_seconds === 'number') {
            parts.push(`Startup wait: ${_voiceServiceStatus.startup_timeout_seconds}s`);
        }
        return parts.join(' • ');
    }

    function _voiceDefaultStatusMessage() {
        if (isPywebviewAvailable()) {
            return 'Native pywebview mode detected. Microphone capture depends on embedded webview support and may fail even when browser mode works. If capture cannot start here, use browser mode.';
        }
        return VOICE_DEFAULT_STATUS;
    }

    function _updateVoiceMeta(index) {
        return _voiceSummaryMessage();
    }

    function _renderVoiceDiagnostics(index, diagnostics) {
        return _formatVoiceDiagnostic(diagnostics, '');
    }

    function _syncVoiceControls(index) {
        const elements = _getVoicePanelElements(index);
        if (!elements.control) return;

        elements.control.hidden = _voiceServiceStatus.enabled === false;
        _updateVoiceMeta(index);
        _renderVoiceDiagnostics(
            index,
            _voiceState[index]?.diagnostics || _voiceLastDiagnostics[index] || {
                requested: _getVoiceConstraints(_voicePrefs, { includeDevice: Boolean(_voicePrefs.deviceId) }),
                supportedConstraints: navigator.mediaDevices?.getSupportedConstraints?.()
            }
        );
        if (!_voiceState[index]) {
            _setVoicePanelStatus(index, _voiceDefaultStatusMessage());
        }
        _updateVoiceBtn(index, Boolean(_voiceState[index]?.recording));
    }

    function _syncVoiceControlsAvailability() {
        document.querySelectorAll('[data-terminal-voice-control]').forEach(control => {
            control.hidden = _voiceServiceStatus.enabled === false;
        });
        _setVoiceBtnsDisabled(_voiceActiveIndex);
    }

    async function _loadVoiceServiceStatus() {
        try {
            const response = await fetch('/api/voice-status');
            if (!response.ok) return;
            const payload = await response.json();
            _voiceServiceStatus = {
                ..._voiceServiceStatus,
                ...payload
            };
            _syncVoiceControlsAvailability();
            terminals.forEach((_, terminalIndex) => _updateVoiceMeta(terminalIndex));
        } catch (err) {
            console.warn('Voice status fetch failed:', err);
        }
    }

    function _voiceBackendUnavailableMessage() {
        if (_voiceServiceStatus.enabled === false) {
            return 'Voice input is disabled in app settings.';
        }

        if (_voiceServiceStatus.engine_available === false) {
            if (_voiceServiceStatus.status_message) {
                return _voiceServiceStatus.status_message;
            }
            if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'whisper') {
                return 'faster-whisper is unavailable. Install optional voice dependencies before starting capture.';
            }
            return 'The configured voice backend is unavailable. Install optional voice dependencies before starting capture.';
        }

        return '';
    }

    function _setVoicePanelStatus(index, message) {
        _voiceStatusMessages[index] = message;
        const btn = document.getElementById(`tvoice-${index}`);
        if (btn && !_voiceState[index]?.recording) {
            btn.title = `Voice input (${message})`;
        }
    }

    function _voiceIndexForSession(sessionId) {
        const renderedIndex = sessionIds.indexOf(sessionId);
        if (renderedIndex !== -1) {
            return renderedIndex;
        }
        return Object.keys(_voiceState)
            .map(key => Number.parseInt(key, 10))
            .find(index => _voiceState[index]?.sessionId === sessionId) ?? -1;
    }

    function _voiceActualSettings(state) {
        return {
            ...state.trackSettings,
            audioContextState: state.audioCtx?.state || 'unknown',
            audioContextSampleRate: state.audioCtx?.sampleRate ?? null,
            requestedProfile: state.profile,
            selectedDeviceId: state.selectedDeviceId || 'default',
            workletChunkSamples: state.format?.chunkSize ?? VOICE_CHUNK_SIZE,
            workletSourceSampleRate: state.format?.sourceSampleRate ?? state.audioCtx?.sampleRate ?? null,
            workletTargetSampleRate: state.format?.targetSampleRate ?? VOICE_TARGET_SAMPLE_RATE
        };
    }

    function _applyVoiceStateDiagnostics(index) {
        const state = _voiceState[index];
        const diagnostics = state
            ? {
                requested: state.requestedConstraints,
                settings: _voiceActualSettings(state),
                supportedConstraints: state.supportedConstraints,
                capabilities: state.capabilities
            }
            : _voiceLastDiagnostics[index];
        if (diagnostics) {
            if (state) {
                state.diagnostics = diagnostics;
            }
            _renderVoiceDiagnostics(index, diagnostics);
        }
    }

    async function _toggleVoice(index) {
        _voiceLog('Toggling voice capture', {
            terminalIndex: index,
            sessionId: sessionIds[index] || null,
            recording: Boolean(_voiceState[index]?.recording),
            activeIndex: _voiceActiveIndex
        });
        if (_voiceState[index]?.recording) {
            await _stopVoice(index);
        } else {
            if (_voiceActiveIndex !== -1 && _voiceActiveIndex !== index) {
                _voiceLog('Stopping active voice capture before switching terminals', {
                    fromTerminalIndex: _voiceActiveIndex,
                    toTerminalIndex: index
                });
                await _stopVoice(_voiceActiveIndex);
            }
            await _startVoice(index);
        }
    }

    function _setVoiceBtnsDisabled(activeIndex) {
        document.querySelectorAll('.voice-btn').forEach(btn => {
            const btnIdx = parseInt(btn.dataset.terminalVoice, 10);
            if (_voiceServiceStatus.enabled === false) {
                btn.disabled = true;
                btn.title = 'Voice input is disabled in app settings';
                return;
            }
            if (activeIndex === -1) {
                btn.disabled = false;
                btn.title = 'Voice input (click to start recording)';
            } else if (btnIdx !== activeIndex) {
                btn.disabled = true;
                btn.title = 'Another terminal is recording';
            } else {
                btn.disabled = false;
            }
        });
    }

    async function _startVoice(index) {
        const sid = sessionIds[index];
        _voiceLog('Starting voice capture', {
            terminalIndex: index,
            sessionId: sid || null,
            hasSocket: Boolean(socket),
            profile: _voicePrefs.profile,
            selectedDeviceId: _voicePrefs.deviceId || ''
        });
        if (!sid || !socket) {
            _voiceLog('Voice start aborted because session or socket is unavailable', {
                terminalIndex: index,
                sessionId: sid || null,
                hasSocket: Boolean(socket)
            });
            return;
        }

        await _loadVoiceServiceStatus();
        const backendUnavailableMessage = _voiceBackendUnavailableMessage();
        if (backendUnavailableMessage) {
            _voiceLog('Voice start aborted because the configured backend is unavailable', {
                terminalIndex: index,
                sessionId: sid,
                engine: _voiceServiceStatus.engine || VOICE_ENGINE,
                message: backendUnavailableMessage
            });
            _setVoicePanelStatus(index, backendUnavailableMessage);
            return;
        }

        if (!navigator.mediaDevices?.getUserMedia) {
            _voiceLog('Voice start aborted because getUserMedia is unavailable', {
                terminalIndex: index
            });
            _setVoicePanelStatus(
                index,
                isPywebviewAvailable()
                    ? 'This pywebview environment does not expose getUserMedia() for microphone capture. Use browser mode for voice input on this machine.'
                    : 'This browser does not expose getUserMedia() for microphone capture.'
            );
            return;
        }

        if (!window.AudioWorkletNode) {
            _voiceLog('Voice start aborted because AudioWorklet is unavailable', {
                terminalIndex: index
            });
            _setVoicePanelStatus(index, 'AudioWorklet is not available in this browser, so the upgraded capture pipeline cannot start.');
            return;
        }

        const requestedConstraints = _getVoiceConstraints(_voicePrefs);
        const supportedConstraints = navigator.mediaDevices.getSupportedConstraints
            ? navigator.mediaDevices.getSupportedConstraints()
            : null;

        _setVoicePanelStatus(index, 'Opening microphone and verifying browser-applied capture settings...');
        _clearVoicePreview(index);

        let stream = null;
        let pipeline = null;
        let acquisition = null;

        try {
            acquisition = await _acquireMicStream(index, sid, requestedConstraints);
            ({ stream } = acquisition);
            const { appliedConstraints, selectedDeviceId, fallbackMessage } = acquisition;

            const track = stream.getAudioTracks()[0];
            const capabilities = track?.getCapabilities ? track.getCapabilities() : null;
            const trackSettings = track?.getSettings ? track.getSettings() : {};
            _voiceLog('Microphone track opened', {
                terminalIndex: index,
                sessionId: sid,
                label: track?.label || '',
                readyState: track?.readyState || 'unknown',
                trackSettings
            });

            pipeline = await _createVoicePipeline(index, sid, stream);
            const { audioCtx, source, workletNode, sink } = pipeline;

            const state = {
                stream,
                audioCtx,
                source,
                workletNode,
                sink,
                track,
                sessionId: sid,
                recording: true,
                requestedConstraints: appliedConstraints,
                selectedDeviceId,
                profile: _voicePrefs.profile,
                supportedConstraints,
                capabilities,
                trackSettings,
                format: {
                    sourceSampleRate: audioCtx.sampleRate,
                    targetSampleRate: VOICE_TARGET_SAMPLE_RATE,
                    chunkSize: VOICE_CHUNK_SIZE
                },
                diagnostics: null,
                pendingFlush: null
            };

            _wireVoiceWorkletMessages(index, sid, workletNode);

            _voiceLog('Emitting voice_start to server', {
                terminalIndex: index,
                sessionId: sid
            });
            socket.emit('voice_start', { session_id: sid });

            source.connect(workletNode);
            workletNode.connect(sink);
            sink.connect(audioCtx.destination);
            if (audioCtx.state === 'suspended') {
                _voiceLog('AudioContext is suspended, attempting resume', {
                    terminalIndex: index,
                    sessionId: sid
                });
                await audioCtx.resume();
            }

            _voiceState[index] = state;
            _voiceActiveIndex = index;
            _voiceLastDiagnostics[index] = {
                requested: appliedConstraints,
                settings: _voiceActualSettings(state),
                supportedConstraints,
                capabilities
            };
            _updateVoiceBtn(index, true);
            _showVoiceRecordingOverlay(index);
            _setVoiceBtnsDisabled(index);
            _applyVoiceStateDiagnostics(index);
            _setVoicePanelStatus(
                index,
                fallbackMessage || 'Capture started with the global microphone settings.'
            );
            _voiceLog('Voice capture started successfully', {
                terminalIndex: index,
                sessionId: sid,
                fallbackUsed: Boolean(fallbackMessage),
                diagnostics: _voiceLastDiagnostics[index]
            });
        } catch (err) {
            console.error(`${VOICE_LOG_PREFIX} Voice input error:`, err, {
                terminalIndex: index,
                sessionId: sid || null,
                requestedConstraints: acquisition?.appliedConstraints ?? requestedConstraints
            });
            await _teardownVoicePipeline(stream, pipeline);
            _hideVoiceRecordingOverlay();
            _updateVoiceBtn(index, false);
            _setVoiceBtnsDisabled(-1);
            const errName = err?.name ? `[${err.name}] ` : '';
            const errMessage = err?.message || String(err);
            _setVoicePanelStatus(
                index,
                isPywebviewAvailable()
                    ? `Voice capture failed: ${errName}${errMessage}. Native pywebview mode is less reliable for microphone capture; if this persists, use browser mode.`
                    : `Voice capture failed: ${errName}${errMessage}`
            );
        }
    }

    /* Open the microphone with the configured constraints, falling back to
       the browser-default device or (under pywebview) unconstrained capture
       when the selected device or profile is not usable. */
    async function _acquireMicStream(index, sid, requestedConstraints) {
        let selectedDeviceId = _voicePrefs.deviceId;
        let appliedConstraints = requestedConstraints;
        let fallbackMessage = '';
        let stream = null;

        try {
            _voiceLog('Requesting microphone access', {
                terminalIndex: index,
                sessionId: sid,
                requestedConstraints
            });
            stream = await navigator.mediaDevices.getUserMedia({ audio: requestedConstraints });
            _voiceLog('Microphone access granted', {
                terminalIndex: index,
                sessionId: sid,
                trackCount: stream.getAudioTracks().length
            });
        } catch (err) {
            if (
                selectedDeviceId &&
                (err?.name === 'NotFoundError' || err?.name === 'OverconstrainedError')
            ) {
                _voiceLog('Selected microphone unavailable, retrying with browser default device', {
                    terminalIndex: index,
                    sessionId: sid,
                    selectedDeviceId,
                    errorName: err?.name,
                    errorMessage: err?.message || String(err)
                });
                fallbackMessage = 'Selected microphone was unavailable, so capture fell back to the browser default input.';
                selectedDeviceId = '';
                appliedConstraints = _getVoiceConstraints({ ..._voicePrefs, deviceId: '' }, { includeDevice: false });
                stream = await navigator.mediaDevices.getUserMedia({ audio: appliedConstraints });
                _voiceLog('Fallback microphone access granted', {
                    terminalIndex: index,
                    sessionId: sid,
                    appliedConstraints
                });
            } else if (isPywebviewAvailable() && err?.name !== 'NotAllowedError') {
                _voiceLog('Constrained capture failed in pywebview, retrying with bare {audio: true}', {
                    terminalIndex: index,
                    sessionId: sid,
                    errorName: err?.name,
                    errorMessage: err?.message || String(err),
                    originalConstraints: appliedConstraints
                });
                try {
                    appliedConstraints = true;
                    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    fallbackMessage = `Capture profile constraints were not supported by this WebView2 environment (${err?.name || 'error'}). Fell back to unconstrained capture.`;
                    _voiceLog('Bare audio capture succeeded in pywebview fallback', {
                        terminalIndex: index,
                        sessionId: sid
                    });
                } catch (bareErr) {
                    _voiceLog('Bare audio capture also failed', {
                        terminalIndex: index,
                        sessionId: sid,
                        errorName: bareErr?.name,
                        errorMessage: bareErr?.message || String(bareErr)
                    });
                    throw bareErr;
                }
            } else {
                throw err;
            }
        }

        return { stream, appliedConstraints, selectedDeviceId, fallbackMessage };
    }

    /* Build the AudioContext → worklet → muted-sink capture pipeline. */
    async function _createVoicePipeline(index, sid, stream) {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)({
            latencyHint: 'interactive'
        });
        _voiceLog('AudioContext created for voice capture', {
            terminalIndex: index,
            sessionId: sid,
            audioContextState: audioCtx.state,
            sampleRate: audioCtx.sampleRate
        });
        await audioCtx.audioWorklet.addModule(VOICE_WORKLET_MODULE_URL);
        _voiceLog('Voice worklet module loaded', {
            terminalIndex: index,
            sessionId: sid,
            moduleUrl: VOICE_WORKLET_MODULE_URL
        });
        const source = audioCtx.createMediaStreamSource(stream);
        const workletNode = new AudioWorkletNode(audioCtx, 'gridvibe-voice-processor', {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            channelCount: 1,
            outputChannelCount: [1],
            processorOptions: {
                targetSampleRate: VOICE_TARGET_SAMPLE_RATE,
                chunkSize: VOICE_CHUNK_SIZE
            }
        });
        const sink = audioCtx.createGain();
        sink.gain.value = 0;
        return { audioCtx, source, workletNode, sink };
    }

    /* Route worklet messages: audio chunks to the server, format reports to
       the diagnostics panel, flush confirmations to the pending waiter. */
    function _wireVoiceWorkletMessages(index, sid, workletNode) {
        workletNode.port.onmessage = event => {
            const currentState = _voiceState[index];
            if (!currentState) return;
            const message = event.data || {};
            if (message.type === 'audio' && currentState.recording) {
                socket.emit('voice_audio', {
                    session_id: sid,
                    audio: message.audio
                });
                return;
            }
            if (message.type === 'format') {
                currentState.format = {
                    sourceSampleRate: message.sourceSampleRate,
                    targetSampleRate: message.targetSampleRate,
                    chunkSize: message.chunkSize
                };
                _voiceLog('Voice worklet reported format', {
                    terminalIndex: index,
                    sessionId: sid,
                    format: currentState.format
                });
                _applyVoiceStateDiagnostics(index);
                return;
            }
            if (
                message.type === 'flush-complete' &&
                currentState.pendingFlush &&
                currentState.pendingFlush.flushId === message.flushId
            ) {
                const { resolve } = currentState.pendingFlush;
                currentState.pendingFlush = null;
                resolve();
            }
        };
    }

    /* Best-effort teardown when capture start fails partway through. */
    async function _teardownVoicePipeline(stream, pipeline) {
        if (pipeline?.workletNode) {
            try { pipeline.workletNode.disconnect(); } catch (_) {}
        }
        if (pipeline?.source) {
            try { pipeline.source.disconnect(); } catch (_) {}
        }
        if (pipeline?.sink) {
            try { pipeline.sink.disconnect(); } catch (_) {}
        }
        if (pipeline?.audioCtx) {
            try { await pipeline.audioCtx.close(); } catch (_) {}
        }
        if (stream) {
            stream.getTracks().forEach(track => {
                try { track.stop(); } catch (_) {}
            });
        }
    }

    async function _flushVoiceWorklet(index, state) {
        if (!state?.workletNode?.port) return;

        const flushId = `flush-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        await new Promise(resolve => {
            const timer = window.setTimeout(() => {
                if (state.pendingFlush?.flushId === flushId) {
                    state.pendingFlush = null;
                }
                resolve();
            }, 200);

            state.pendingFlush = {
                flushId,
                resolve: () => {
                    window.clearTimeout(timer);
                    resolve();
                }
            };

            state.workletNode.port.postMessage({ type: 'flush', flushId });
        });
    }

    async function _stopVoice(index, { notifyServer = true } = {}) {
        const state = _voiceState[index];
        if (!state) return;
        _voiceLog('Stopping voice capture', {
            terminalIndex: index,
            sessionId: state.sessionId || sessionIds[index] || null,
            notifyServer
        });

        state.recording = false;
        _hideVoiceRecordingOverlay();
        await _flushVoiceWorklet(index, state);

        const sid = state.sessionId || sessionIds[index];
        if (notifyServer && sid && socket) {
            socket.emit('voice_stop', { session_id: sid });
        }

        _voiceLastDiagnostics[index] = {
            requested: state.requestedConstraints,
            settings: _voiceActualSettings(state),
            supportedConstraints: state.supportedConstraints,
            capabilities: state.capabilities
        };

        try { state.workletNode.disconnect(); } catch (_) {}
        try { state.source.disconnect(); } catch (_) {}
        try { state.sink.disconnect(); } catch (_) {}
        state.stream.getTracks().forEach(track => {
            try { track.stop(); } catch (_) {}
        });
        try { await state.audioCtx.close(); } catch (_) {}

        delete _voiceState[index];
        _voiceActiveIndex = -1;
        _updateVoiceBtn(index, false);
        _setVoiceBtnsDisabled(-1);
        _clearVoicePreview(index);
        _applyVoiceStateDiagnostics(index);
        _setVoicePanelStatus(index, 'Capture stopped. The last applied browser settings are preserved above for comparison.');
    }

    async function _stopAllVoice() {
        const activeIndexes = Object.keys(_voiceState)
            .map(key => Number.parseInt(key, 10))
            .filter(Number.isInteger);
        for (const index of activeIndexes) {
            await _stopVoice(index);
        }
    }

    /* ── Floating recording indicator (ISSUE-2026-019) ──
       One fixed-position, non-blocking overlay shared by every capture path
       (mic toggle, hold-to-talk, push-to-talk keybind). Shown only after
       _startVoice() has established an active recording state; hidden from
       every _stopVoice(), start-failure, and teardown path. */
    const VOICE_OVERLAY_ID = 'voiceRecordingOverlay';
    const VOICE_OVERLAY_BAR_COUNT = 5;
    const VOICE_OVERLAY_MIC_ICON = '<svg class="voice-overlay-mic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>';
    let _voiceOverlayAnimation = null;

    function _prefersReducedMotion() {
        try {
            return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
        } catch (_) {
            return false;
        }
    }

    function _showVoiceRecordingOverlay(index) {
        if (!_voiceState[index]?.recording) {
            return; /* capture already stopped while startup was in flight */
        }
        let overlay = document.getElementById(VOICE_OVERLAY_ID);
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = VOICE_OVERLAY_ID;
            overlay.className = 'voice-recording-overlay';
            overlay.setAttribute('role', 'status');
            overlay.setAttribute('aria-live', 'polite');
            const bars = Array.from(
                { length: VOICE_OVERLAY_BAR_COUNT },
                () => '<span class="voice-overlay-bar"></span>'
            ).join('');
            overlay.innerHTML = `${VOICE_OVERLAY_MIC_ICON}<span class="voice-overlay-label">Recording</span><span class="voice-overlay-bars" aria-hidden="true">${bars}</span>`;
            document.body.appendChild(overlay);
        }
        overlay.classList.remove('voice-overlay-fallback');
        _startVoiceOverlayAnimation(index, overlay);
    }

    function _hideVoiceRecordingOverlay() {
        _stopVoiceOverlayAnimation();
        document.getElementById(VOICE_OVERLAY_ID)?.remove();
    }

    /* Drive the bars from a small AnalyserNode fanned out from the live
       capture source; fall back to the deterministic CSS animation when the
       analyser cannot be created, and stay static under reduced motion. */
    function _startVoiceOverlayAnimation(index, overlay) {
        _stopVoiceOverlayAnimation();
        if (_prefersReducedMotion()) {
            return;
        }
        const state = _voiceState[index];
        let analyser = null;
        try {
            if (state?.audioCtx && state?.source) {
                analyser = state.audioCtx.createAnalyser();
                analyser.fftSize = 64;
                analyser.smoothingTimeConstant = 0.6;
                state.source.connect(analyser);
            }
        } catch (_) {
            analyser = null;
        }
        if (!analyser) {
            overlay.classList.add('voice-overlay-fallback');
            return;
        }
        const bars = overlay.querySelectorAll('.voice-overlay-bar');
        const data = new Uint8Array(analyser.frequencyBinCount);
        const animation = { frameId: 0, analyser, source: state.source };
        const tick = () => {
            if (_voiceOverlayAnimation !== animation) {
                return; /* a newer capture (or a stop) superseded this loop */
            }
            analyser.getByteFrequencyData(data);
            bars.forEach((bar, barIndex) => {
                const bin = data[Math.floor(((barIndex + 1) * data.length) / (bars.length + 1))] || 0;
                const scale = 0.25 + (bin / 255) * 0.75;
                bar.style.transform = `scaleY(${scale.toFixed(3)})`;
            });
            animation.frameId = window.requestAnimationFrame(tick);
        };
        _voiceOverlayAnimation = animation;
        animation.frameId = window.requestAnimationFrame(tick);
    }

    function _stopVoiceOverlayAnimation() {
        const animation = _voiceOverlayAnimation;
        if (!animation) {
            return;
        }
        _voiceOverlayAnimation = null;
        if (animation.frameId) {
            window.cancelAnimationFrame(animation.frameId);
        }
        try { animation.source.disconnect(animation.analyser); } catch (_) {}
    }

    /* Press-and-hold on the mic button records while held (mirroring the
       push-to-talk keybind); a quick click keeps the existing click-to-toggle
       behaviour. Keyboard activation still fires plain click → toggle. */
    const VOICE_HOLD_TO_TALK_MS = 350;

    function _wireVoiceHoldToTalk(card, index) {
        const button = card.querySelector(`[data-terminal-voice="${index}"]`);
        const control = card.querySelector(`[data-terminal-voice-control="${index}"]`);
        if (!button || !control) {
            return;
        }

        let holdTimer = null;
        let holdActive = false;
        let holdStarting = false;
        let holdStopRequested = false;
        let suppressClick = false;

        const clearHoldTimer = () => {
            if (holdTimer !== null) {
                window.clearTimeout(holdTimer);
                holdTimer = null;
            }
        };

        button.addEventListener('pointerdown', event => {
            if (event.button !== 0 || button.disabled || _voiceState[index]?.recording) {
                return;
            }
            try { button.setPointerCapture(event.pointerId); } catch (_) {}
            holdStopRequested = false;
            clearHoldTimer();
            holdTimer = window.setTimeout(async () => {
                holdTimer = null;
                holdActive = true;
                suppressClick = true;
                holdStarting = true;
                try {
                    if (_voiceActiveIndex !== -1 && _voiceActiveIndex !== index) {
                        await _stopVoice(_voiceActiveIndex);
                    }
                    if (!_voiceState[index]?.recording) {
                        await _startVoice(index);
                    }
                } finally {
                    holdStarting = false;
                }
                if (holdStopRequested) {
                    holdStopRequested = false;
                    if (_voiceState[index]?.recording) {
                        await _stopVoice(index);
                    }
                }
            }, VOICE_HOLD_TO_TALK_MS);
        });

        const endHold = async () => {
            clearHoldTimer();
            if (!holdActive) {
                return;
            }
            holdActive = false;
            if (holdStarting) {
                /* release raced the async start — stop as soon as it settles */
                holdStopRequested = true;
                return;
            }
            if (_voiceState[index]?.recording) {
                await _stopVoice(index);
            }
        };
        button.addEventListener('pointerup', endHold);
        button.addEventListener('pointercancel', endHold);

        /* A completed hold already stopped capture on release; swallow the
           click that follows pointerup so it cannot re-toggle recording.
           Capture phase on the wrapper runs before the button's listener. */
        control.addEventListener('click', event => {
            if (suppressClick) {
                suppressClick = false;
                event.preventDefault();
                event.stopPropagation();
            }
        }, true);
    }

    /* ── Push-to-talk ── */
    let _pttActive = false;
    let _pttProcessing = false;
    let _pttStopRequested = false;

    function _matchesPttKeybind(event, keybind) {
        if (!keybind) return false;
        const parts = keybind.split('+');
        const targetKey = parts[parts.length - 1];
        const needsCtrl = parts.includes('Ctrl');
        const needsCmd = parts.includes('Cmd');
        const needsAlt = parts.includes('Alt');
        const needsShift = parts.includes('Shift');
        if (needsCtrl !== event.ctrlKey) return false;
        if (needsCmd !== event.metaKey) return false;
        if (needsAlt !== event.altKey) return false;
        if (needsShift !== event.shiftKey) return false;
        const eventKey = event.key.length === 1 ? event.key.toUpperCase() : event.key;
        return eventKey === targetKey;
    }

    function _pttKeybindReleasedBy(event, keybind) {
        if (!keybind) return false;
        const parts = keybind.split('+');
        const key = event.key;
        return (key === 'Control' && parts.includes('Ctrl'))
            || (key === 'Meta' && parts.includes('Cmd'))
            || (key === 'Alt' && parts.includes('Alt'))
            || (key === 'Shift' && parts.includes('Shift'))
            || (key.length === 1 ? key.toUpperCase() : key) === parts[parts.length - 1];
    }

    /* Push-to-talk targets the currently selected (focused) terminal only.
       When nothing is selected, voice goes nowhere — consistent with typing,
       and so a not-visibly-selected pane never silently receives dictation. */
    function _findPttTerminalIndex() {
        if (_focusedTerminalIndex !== -1
            && sessionIds[_focusedTerminalIndex]
            && terminals[_focusedTerminalIndex]) {
            return _focusedTerminalIndex;
        }
        return -1;
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

    function _updateVoiceBtn(index, recording) {
        const btn = document.getElementById(`tvoice-${index}`);
        if (!btn) return;
        btn.classList.toggle('recording', recording);
        btn.title = recording
            ? 'Voice input (recording — click to stop)'
            : (_voiceStatusMessages[index]
                ? `Voice input (${_voiceStatusMessages[index]})`
                : 'Voice input (click to start recording)');
    }

    function _showVoicePreview(index, text) {
        const btn = document.getElementById(`tvoice-${index}`);
        if (!btn) return;
        let preview = btn.querySelector('.voice-partial-preview');
        if (!preview) {
            preview = document.createElement('span');
            preview.className = 'voice-partial-preview';
            btn.appendChild(preview);
        }
        preview.textContent = text;
    }

    function _clearVoicePreview(index) {
        const btn = document.getElementById(`tvoice-${index}`);
        if (!btn) return;
        const preview = btn.querySelector('.voice-partial-preview');
        if (preview) preview.remove();
    }

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
    function formatExplorerSize(value) {
        if (value === null || value === undefined) {
            return '';
        }
        const size = Number(value);
        if (!Number.isFinite(size)) {
            return '';
        }
        if (size < 1024) {
            return `${size} B`;
        }
        const units = ['KB', 'MB', 'GB', 'TB'];
        let next = size / 1024;
        for (const unit of units) {
            if (next < 1024) {
                return `${next.toFixed(next >= 10 ? 0 : 1)} ${unit}`;
            }
            next /= 1024;
        }
        return `${next.toFixed(1)} PB`;
    }

    function formatExplorerDate(value) {
        const timestamp = Number(value);
        if (!Number.isFinite(timestamp)) {
            return '';
        }
        return new Date(timestamp * 1000).toLocaleString();
    }

    /* OD-9: previews above ~2 MiB render as plain text (no syntax
       highlighting) so large files stay responsive; the backend cap itself
       is 10 MiB (EXPLORER_FILE_PREVIEW_MAX_BYTES). Compared against the
       decoded character count, which is a close proxy for the byte size. */
    const EXPLORER_PLAIN_PREVIEW_THRESHOLD = 2 * 1024 * 1024;

    const EXPLORER_LANGUAGE_BY_EXTENSION = Object.freeze({
        '.bash': 'shell',
        '.bat': 'batch',
        '.c': 'c',
        '.cc': 'cpp',
        '.cfg': 'config',
        '.cmd': 'batch',
        '.conf': 'config',
        '.cpp': 'cpp',
        '.cs': 'csharp',
        '.css': 'css',
        '.env': 'dotenv',
        '.example': 'config',
        '.go': 'go',
        '.gitattributes': 'config',
        '.gitignore': 'gitignore',
        '.gitkeep': 'text',
        '.h': 'c',
        '.hpp': 'cpp',
        '.html': 'html',
        '.ini': 'ini',
        '.java': 'java',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.json': 'json',
        '.jsonl': 'jsonl',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.log': 'log',
        '.lua': 'lua',
        '.md': 'markdown',
        '.markdown': 'markdown',
        '.php': 'php',
        '.ps1': 'powershell',
        '.py': 'python',
        '.rb': 'ruby',
        '.rs': 'rust',
        '.sh': 'shell',
        '.sql': 'sql',
        '.spec': 'python',
        '.swift': 'swift',
        '.txt': 'text',
        '.toml': 'toml',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.xml': 'xml',
        '.yaml': 'yaml',
        '.yml': 'yaml'
    });

    const EXPLORER_LANGUAGE_BY_FILENAME = Object.freeze({
        '.editorconfig': 'ini',
        '.env': 'dotenv',
        '.gitattributes': 'config',
        '.gitignore': 'gitignore',
        '.gitkeep': 'text',
        '.python-version': 'text',
        'dockerfile': 'dockerfile',
        'go.mod': 'go',
        'go.sum': 'text',
        'go.work': 'go',
        'go.work.sum': 'text',
        'makefile': 'makefile'
    });

    const EXPLORER_LANGUAGE_LABELS = Object.freeze({
        batch: 'Batch source',
        c: 'C source',
        config: 'Config file',
        cpp: 'C++ source',
        csharp: 'C# source',
        css: 'CSS source',
        dockerfile: 'Dockerfile',
        dotenv: 'Environment file',
        gitignore: 'Git ignore file',
        go: 'Go source',
        html: 'HTML source',
        ini: 'INI config',
        java: 'Java source',
        javascript: 'JavaScript source',
        json: 'JSON source',
        jsonl: 'JSON Lines source',
        kotlin: 'Kotlin source',
        log: 'Log file',
        lua: 'Lua source',
        makefile: 'Makefile',
        markdown: 'Markdown source',
        php: 'PHP source',
        powershell: 'PowerShell source',
        python: 'Python source',
        ruby: 'Ruby source',
        rust: 'Rust source',
        shell: 'Shell source',
        sql: 'SQL source',
        swift: 'Swift source',
        text: 'Text file',
        toml: 'TOML source',
        typescript: 'TypeScript source',
        xml: 'XML source',
        yaml: 'YAML source'
    });

    const EXPLORER_CODE_KEYWORDS = Object.freeze({
        batch: ['call', 'do', 'echo', 'else', 'errorlevel', 'exist', 'exit', 'for', 'goto', 'if', 'in', 'not', 'pause', 'rem', 'set', 'shift'],
        c: ['auto', 'break', 'case', 'char', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extern', 'float', 'for', 'goto', 'if', 'inline', 'int', 'long', 'register', 'return', 'short', 'signed', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union', 'unsigned', 'void', 'volatile', 'while'],
        config: ['false', 'no', 'null', 'off', 'on', 'true', 'yes'],
        cpp: ['alignas', 'alignof', 'auto', 'bool', 'break', 'case', 'catch', 'class', 'const', 'constexpr', 'continue', 'decltype', 'default', 'delete', 'do', 'double', 'else', 'enum', 'explicit', 'export', 'extern', 'false', 'float', 'for', 'friend', 'if', 'inline', 'int', 'long', 'namespace', 'new', 'noexcept', 'nullptr', 'operator', 'private', 'protected', 'public', 'return', 'short', 'signed', 'sizeof', 'static', 'struct', 'switch', 'template', 'this', 'throw', 'true', 'try', 'typedef', 'typename', 'union', 'unsigned', 'using', 'virtual', 'void', 'volatile', 'while'],
        csharp: ['abstract', 'as', 'base', 'bool', 'break', 'case', 'catch', 'class', 'const', 'continue', 'decimal', 'default', 'delegate', 'do', 'double', 'else', 'enum', 'event', 'false', 'finally', 'fixed', 'float', 'for', 'foreach', 'if', 'in', 'int', 'interface', 'internal', 'is', 'lock', 'namespace', 'new', 'null', 'object', 'out', 'override', 'private', 'protected', 'public', 'readonly', 'ref', 'return', 'sealed', 'static', 'string', 'struct', 'switch', 'this', 'throw', 'true', 'try', 'typeof', 'using', 'var', 'virtual', 'void', 'while'],
        css: ['align-items', 'background', 'border', 'color', 'display', 'flex', 'font-family', 'font-size', 'grid', 'height', 'margin', 'padding', 'position', 'width'],
        dockerfile: ['ADD', 'ARG', 'CMD', 'COPY', 'ENTRYPOINT', 'ENV', 'EXPOSE', 'FROM', 'HEALTHCHECK', 'LABEL', 'RUN', 'USER', 'VOLUME', 'WORKDIR'],
        dotenv: ['false', 'no', 'off', 'on', 'true', 'yes'],
        gitignore: ['false', 'true'],
        go: ['break', 'case', 'chan', 'const', 'continue', 'default', 'defer', 'else', 'fallthrough', 'for', 'func', 'go', 'goto', 'if', 'import', 'interface', 'map', 'nil', 'package', 'range', 'return', 'select', 'struct', 'switch', 'type', 'var'],
        html: ['DOCTYPE', 'a', 'body', 'button', 'div', 'head', 'html', 'input', 'link', 'meta', 'script', 'span', 'style', 'template'],
        java: ['abstract', 'assert', 'boolean', 'break', 'case', 'catch', 'class', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum', 'extends', 'false', 'final', 'finally', 'float', 'for', 'if', 'implements', 'import', 'instanceof', 'int', 'interface', 'long', 'native', 'new', 'null', 'package', 'private', 'protected', 'public', 'return', 'short', 'static', 'strictfp', 'super', 'switch', 'synchronized', 'this', 'throw', 'throws', 'true', 'try', 'void', 'volatile', 'while'],
        javascript: ['async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'default', 'delete', 'do', 'else', 'export', 'extends', 'false', 'finally', 'for', 'from', 'function', 'if', 'import', 'in', 'instanceof', 'let', 'new', 'null', 'of', 'return', 'static', 'super', 'switch', 'this', 'throw', 'true', 'try', 'typeof', 'undefined', 'var', 'void', 'while', 'yield'],
        json: ['false', 'null', 'true'],
        jsonl: ['false', 'null', 'true'],
        kotlin: ['as', 'break', 'class', 'continue', 'data', 'do', 'else', 'false', 'for', 'fun', 'if', 'in', 'interface', 'is', 'null', 'object', 'package', 'return', 'super', 'this', 'throw', 'true', 'try', 'typealias', 'val', 'var', 'when', 'while'],
        lua: ['and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function', 'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then', 'true', 'until', 'while'],
        php: ['abstract', 'and', 'array', 'as', 'break', 'case', 'catch', 'class', 'clone', 'const', 'continue', 'declare', 'default', 'do', 'echo', 'else', 'elseif', 'extends', 'false', 'final', 'finally', 'for', 'foreach', 'function', 'global', 'if', 'implements', 'include', 'instanceof', 'interface', 'namespace', 'new', 'null', 'or', 'private', 'protected', 'public', 'require', 'return', 'static', 'switch', 'this', 'throw', 'trait', 'true', 'try', 'use', 'var', 'while'],
        powershell: ['begin', 'break', 'catch', 'class', 'continue', 'data', 'do', 'dynamicparam', 'else', 'elseif', 'end', 'false', 'filter', 'finally', 'for', 'foreach', 'from', 'function', 'if', 'in', 'param', 'process', 'return', 'switch', 'throw', 'trap', 'true', 'try', 'until', 'using', 'var', 'while'],
        python: ['and', 'as', 'assert', 'async', 'await', 'break', 'case', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except', 'False', 'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda', 'match', 'None', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'True', 'try', 'while', 'with', 'yield'],
        ruby: ['BEGIN', 'END', 'alias', 'and', 'begin', 'break', 'case', 'class', 'def', 'defined?', 'do', 'else', 'elsif', 'end', 'ensure', 'false', 'for', 'if', 'in', 'module', 'next', 'nil', 'not', 'or', 'redo', 'rescue', 'retry', 'return', 'self', 'super', 'then', 'true', 'undef', 'unless', 'until', 'when', 'while', 'yield'],
        rust: ['as', 'async', 'await', 'break', 'const', 'continue', 'crate', 'dyn', 'else', 'enum', 'extern', 'false', 'fn', 'for', 'if', 'impl', 'in', 'let', 'loop', 'match', 'mod', 'move', 'mut', 'pub', 'ref', 'return', 'self', 'static', 'struct', 'super', 'trait', 'true', 'type', 'unsafe', 'use', 'where', 'while'],
        shell: ['case', 'do', 'done', 'elif', 'else', 'esac', 'export', 'fi', 'for', 'function', 'if', 'in', 'local', 'readonly', 'return', 'select', 'set', 'shift', 'then', 'until', 'while'],
        sql: ['ALTER', 'AND', 'AS', 'ASC', 'BEGIN', 'BY', 'CASE', 'CREATE', 'DELETE', 'DESC', 'DISTINCT', 'DROP', 'ELSE', 'END', 'FROM', 'GROUP', 'HAVING', 'IN', 'INSERT', 'INTO', 'IS', 'JOIN', 'LEFT', 'LIKE', 'LIMIT', 'NOT', 'NULL', 'ON', 'OR', 'ORDER', 'RIGHT', 'SELECT', 'SET', 'TABLE', 'THEN', 'UPDATE', 'VALUES', 'WHEN', 'WHERE'],
        swift: ['Any', 'as', 'associatedtype', 'break', 'case', 'catch', 'class', 'continue', 'default', 'defer', 'do', 'else', 'enum', 'extension', 'false', 'for', 'func', 'guard', 'if', 'import', 'in', 'init', 'inout', 'let', 'nil', 'operator', 'private', 'protocol', 'public', 'return', 'self', 'static', 'struct', 'subscript', 'switch', 'throw', 'true', 'try', 'typealias', 'var', 'where', 'while'],
        typescript: ['abstract', 'any', 'as', 'async', 'await', 'boolean', 'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger', 'declare', 'default', 'delete', 'do', 'else', 'enum', 'export', 'extends', 'false', 'finally', 'for', 'from', 'function', 'if', 'implements', 'import', 'in', 'instanceof', 'interface', 'let', 'module', 'namespace', 'new', 'null', 'number', 'of', 'private', 'protected', 'public', 'readonly', 'return', 'static', 'string', 'super', 'switch', 'this', 'throw', 'true', 'try', 'type', 'typeof', 'undefined', 'var', 'void', 'while', 'yield'],
        xml: ['DOCTYPE'],
        yaml: ['false', 'null', 'true'],
        toml: ['false', 'true']
    });

    const EXPLORER_CODE_BUILTINS = Object.freeze({
        javascript: ['Array', 'Boolean', 'Date', 'Error', 'JSON', 'Map', 'Math', 'Number', 'Object', 'Promise', 'RegExp', 'Set', 'String', 'console', 'document', 'window'],
        python: ['bool', 'dict', 'enumerate', 'float', 'int', 'len', 'list', 'open', 'print', 'range', 'set', 'str', 'tuple'],
        go: ['append', 'bool', 'byte', 'cap', 'close', 'complex64', 'complex128', 'copy', 'delete', 'error', 'int', 'len', 'make', 'new', 'panic', 'print', 'println', 'recover', 'string'],
        shell: ['awk', 'cat', 'cd', 'cp', 'echo', 'grep', 'ls', 'mkdir', 'mv', 'printf', 'rm', 'sed', 'test'],
        powershell: ['Get-ChildItem', 'Get-Content', 'New-Item', 'Remove-Item', 'Select-Object', 'Set-Content', 'Where-Object', 'Write-Host']
    });

    const EXPLORER_C_LIKE_LANGUAGES = new Set(['c', 'cpp', 'csharp', 'css', 'go', 'java', 'javascript', 'kotlin', 'php', 'rust', 'swift', 'typescript']);
    const EXPLORER_HASH_COMMENT_LANGUAGES = new Set(['config', 'dockerfile', 'dotenv', 'gitignore', 'ini', 'makefile', 'python', 'ruby', 'shell', 'powershell', 'yaml', 'toml']);
    const EXPLORER_LOG_LEVELS = new Set(['TRACE', 'DEBUG', 'INFO', 'WARN', 'WARNING', 'ERROR', 'CRITICAL', 'FATAL']);
    const EXPLORER_EDITOR_FONT_MIN = 10;
    const EXPLORER_EDITOR_FONT_MAX = 24;
    const EXPLORER_EDITOR_FONT_DEFAULT = 13;
    const EXPLORER_EDITOR_FONT_STEP = 1;
    const EXPLORER_SEARCH_DEBOUNCE_MS = 160;
    const EXPLORER_SEARCH_MAX_MATCHES = 1000;
    /* Ctrl+scroll zoom bounds for image / mermaid views (notes 3). Minimum is
       the fitted (1×) size — shrinking below what fits isn't meaningful. */
    const EXPLORER_WHEEL_ZOOM_MAX = 8;
    const EXPLORER_WHEEL_ZOOM_STEP = 1.12;
    const EXPLORER_SEARCH_CHUNK_SIZE = 65536;
    const EXPLORER_SEARCH_YIELD_MS = 8;
    const EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT = 64;
    const EXPLORER_TREE_INDENT_PX = 12;
    const EXPLORER_GIT_GRAPH_LANE_COUNT = 6;

    const EXPLORER_GIT_STATUS_LABELS = Object.freeze({
        modified: 'M',
        added: 'A',
        deleted: 'D',
        renamed: 'R',
        conflicted: 'U',
        untracked: '?',
        ignored: '!',
        clean: ''
    });

    function explorerPathExtension(path) {
        const name = String(path || '').toLowerCase();
        const dotIndex = name.lastIndexOf('.');
        return dotIndex >= 0 ? name.slice(dotIndex) : '';
    }

    function explorerPathFilename(path) {
        const parts = String(path || '').toLowerCase().split(/[\\/]/);
        return parts[parts.length - 1] || '';
    }

    function normalizeExplorerLanguage(language) {
        const normalized = String(language || '').toLowerCase().replace(/[^a-z0-9+#-]/g, '');
        if (normalized === 'bat' || normalized === 'cmd') return 'batch';
        if (normalized === 'js') return 'javascript';
        if (normalized === 'ts') return 'typescript';
        if (normalized === 'py') return 'python';
        if (normalized === 'sh' || normalized === 'bash') return 'shell';
        if (normalized === 'ps1') return 'powershell';
        if (normalized === 'md') return 'markdown';
        if (normalized === 'c++') return 'cpp';
        if (normalized === 'c#') return 'csharp';
        return normalized;
    }

    function explorerCodeLanguage(path) {
        const filename = explorerPathFilename(path);
        if (filename && EXPLORER_LANGUAGE_BY_FILENAME[filename]) {
            return EXPLORER_LANGUAGE_BY_FILENAME[filename];
        }
        if (filename.startsWith('.env.')) {
            return 'dotenv';
        }
        return EXPLORER_LANGUAGE_BY_EXTENSION[explorerPathExtension(path)] || '';
    }

    function explorerLanguageClass(language) {
        return normalizeExplorerLanguage(language).replace(/[^a-z0-9-]/g, '');
    }

    function explorerFileTypeLabel(path, language = '') {
        const detectedLanguage = normalizeExplorerLanguage(language) || explorerCodeLanguage(path);
        if (detectedLanguage && EXPLORER_LANGUAGE_LABELS[detectedLanguage]) {
            return EXPLORER_LANGUAGE_LABELS[detectedLanguage];
        }
        return explorerPathExtension(path) === '.txt' ? 'Text file' : 'Text file';
    }

    /* Reuse the existing language classifiers to pick a compact leading icon for
       tree and Git rows. Categories map to token-driven colors + a distinct
       stroke glyph so mixed file lists are quick to scan; unknown types fall
       back to the plain document glyph. */
    const EXPLORER_FILE_ICON_CATEGORY_BY_LANGUAGE = Object.freeze({
        javascript: 'code',
        typescript: 'code',
        python: 'code',
        ruby: 'code',
        go: 'code',
        rust: 'code',
        java: 'code',
        c: 'code',
        cpp: 'code',
        csharp: 'code',
        php: 'code',
        swift: 'code',
        kotlin: 'code',
        lua: 'code',
        shell: 'shell',
        powershell: 'shell',
        batch: 'shell',
        json: 'data',
        jsonl: 'data',
        yaml: 'data',
        toml: 'data',
        ini: 'data',
        html: 'markup',
        xml: 'markup',
        css: 'style',
        markdown: 'markdown',
        config: 'config',
        dotenv: 'config',
        gitignore: 'config',
        dockerfile: 'config',
        makefile: 'config',
        sql: 'sql',
        log: 'log',
        text: 'doc'
    });

    const EXPLORER_FILE_ICON_GLYPHS = Object.freeze({
        doc: '<path d="M6 3.5h7.2L18.5 8.8V19.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M12.8 3.6V8a1 1 0 0 0 1 1h4"/><path d="M8 13h6"/><path d="M8 16h6"/>',
        code: '<path d="M9.3 8.5 6 12l3.3 3.5"/><path d="M14.7 8.5 18 12l-3.3 3.5"/><path d="M13.2 6.5 10.8 17.5"/>',
        shell: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7 9.8 9.6 12 7 14.2"/><path d="M12.5 14.5h4.5"/>',
        data: '<path d="M10.2 4.8c-2 0-2.4 1.2-2.4 3S7.4 11 6 12c1.4 1 1.8 1.4 1.8 3.2s.4 4 2.4 4"/><path d="M13.8 4.8c2 0 2.4 1.2 2.4 3S16.6 11 18 12c-1.4 1-1.8 1.4-1.8 3.2s-.4 4-2.4 4"/>',
        markup: '<path d="M9 8 4.5 12 9 16"/><path d="M15 8 19.5 12 15 16"/>',
        style: '<path d="M9.8 4.5 7.8 19.5"/><path d="M16.2 4.5 14.2 19.5"/><path d="M5.5 9.2h13"/><path d="M5 14.8h13"/>',
        markdown: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M6.5 15V9l2.6 3 2.6-3v6"/><path d="M15.6 9v4.4"/><path d="M13.9 12.1 15.6 14l1.7-1.9"/>',
        config: '<circle cx="12" cy="12" r="3"/><path d="M12 4v2.2M12 17.8V20M4 12h2.2M17.8 12H20M6.3 6.3l1.6 1.6M16.1 16.1l1.6 1.6M17.7 6.3l-1.6 1.6M7.9 16.1l-1.6 1.6"/>',
        sql: '<ellipse cx="12" cy="6" rx="6.3" ry="2.5"/><path d="M5.7 6v6c0 1.4 2.8 2.5 6.3 2.5s6.3-1.1 6.3-2.5V6"/><path d="M5.7 12v6c0 1.4 2.8 2.5 6.3 2.5s6.3-1.1 6.3-2.5v-6"/>',
        log: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M7.5 9h2M7.5 12h2M7.5 15h2"/><path d="M12 9h4.5M12 12h4.5M12 15h3"/>'
    });

    function explorerFileTypeCategory(path, language = '') {
        const detected = normalizeExplorerLanguage(language) || explorerCodeLanguage(path);
        return EXPLORER_FILE_ICON_CATEGORY_BY_LANGUAGE[detected] || 'doc';
    }

    function explorerFileTypeIconHtml(path, language = '') {
        const category = explorerFileTypeCategory(path, language);
        const glyph = EXPLORER_FILE_ICON_GLYPHS[category] || EXPLORER_FILE_ICON_GLYPHS.doc;
        return `<span class="explorer-icon file type-${category}" aria-hidden="true">`
            + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" '
            + `stroke-linecap="round" stroke-linejoin="round" focusable="false">${glyph}</svg></span>`;
    }

    /* A truncated preview keeps either the head or the tail of the file
       (ISSUE-2026-020): logs retain their newest bytes, everything else keeps
       its opening bytes. Report which end, and how much, was retained. */
    function explorerPreviewTruncationLabel(data) {
        if (!data || !data.truncated) {
            return '';
        }
        const start = Number(data.preview_start_byte);
        const end = Number(data.preview_end_byte);
        const retained = (Number.isFinite(start) && Number.isFinite(end))
            ? Math.max(0, end - start)
            : NaN;
        const total = Number(data.total_size);
        const edge = data.preview_mode === 'tail' ? 'last' : 'first';
        const retainedLabel = Number.isFinite(retained) ? formatExplorerSize(retained) : '';
        const totalLabel = Number.isFinite(total) ? formatExplorerSize(total) : '';
        if (retainedLabel && totalLabel) {
            return `Showing the ${edge} ${retainedLabel} of ${totalLabel}`;
        }
        if (retainedLabel) {
            return `Showing the ${edge} ${retainedLabel}`;
        }
        return 'Preview truncated';
    }

    function explorerFileMetaParts(data, fileType) {
        const size = formatExplorerSize(data.size) || 'Unknown size';
        const modified = formatExplorerDate(data.modified);
        const metaParts = [fileType, size, data.encoding || 'utf-8'];
        if (modified) {
            metaParts.push(modified);
        }
        const truncationLabel = explorerPreviewTruncationLabel(data);
        if (truncationLabel) {
            metaParts.push(truncationLabel);
        }
        return metaParts;
    }

    function explorerFindRanges(content, query, maxMatches = EXPLORER_SEARCH_MAX_MATCHES) {
        const source = String(content || '');
        const needle = String(query || '');
        if (!source || !needle) {
            return [];
        }

        const ranges = [];
        const normalizedNeedle = needle.toLowerCase();
        const stride = Math.max(EXPLORER_SEARCH_CHUNK_SIZE, normalizedNeedle.length);
        for (let offset = 0; offset < source.length && ranges.length < maxMatches; offset += stride) {
            const chunkEnd = Math.min(source.length, offset + stride + normalizedNeedle.length - 1);
            const normalizedChunk = source.slice(offset, chunkEnd).toLowerCase();
            let localCursor = 0;
            while (localCursor < normalizedChunk.length && ranges.length < maxMatches) {
                const matchIndex = normalizedChunk.indexOf(normalizedNeedle, localCursor);
                if (matchIndex === -1) {
                    break;
                }
                const absoluteIndex = offset + matchIndex;
                if (absoluteIndex >= offset + stride && chunkEnd < source.length) {
                    break;
                }
                ranges.push({ start: absoluteIndex, end: absoluteIndex + needle.length });
                localCursor = matchIndex + Math.max(needle.length, 1);
            }
        }
        ranges.capped = ranges.length >= maxMatches;
        return ranges;
    }

    async function explorerFindRangesAsync(content, query, token, maxMatches = EXPLORER_SEARCH_MAX_MATCHES) {
        const source = String(content || '');
        const needle = String(query || '');
        if (!source || !needle) {
            return { ranges: [], capped: false, cancelled: Boolean(token?.cancelled) };
        }

        const ranges = [];
        const normalizedNeedle = needle.toLowerCase();
        const stride = Math.max(EXPLORER_SEARCH_CHUNK_SIZE, normalizedNeedle.length);
        let lastYield = performance.now();

        for (let offset = 0; offset < source.length && ranges.length < maxMatches; offset += stride) {
            if (token?.cancelled) {
                return { ranges, capped: false, cancelled: true };
            }

            const chunkEnd = Math.min(source.length, offset + stride + normalizedNeedle.length - 1);
            const normalizedChunk = source.slice(offset, chunkEnd).toLowerCase();
            let localCursor = 0;
            while (localCursor < normalizedChunk.length && ranges.length < maxMatches) {
                const matchIndex = normalizedChunk.indexOf(normalizedNeedle, localCursor);
                if (matchIndex === -1) {
                    break;
                }
                const absoluteIndex = offset + matchIndex;
                if (absoluteIndex >= offset + stride && chunkEnd < source.length) {
                    break;
                }
                ranges.push({ start: absoluteIndex, end: absoluteIndex + needle.length });
                localCursor = matchIndex + Math.max(needle.length, 1);
            }

            if (performance.now() - lastYield >= EXPLORER_SEARCH_YIELD_MS) {
                await new Promise(resolve => window.setTimeout(resolve, 0));
                lastYield = performance.now();
            }
        }

        return {
            ranges,
            capped: ranges.length >= maxMatches,
            cancelled: Boolean(token?.cancelled)
        };
    }

    function explorerMarkedEscHtml(text, absoluteStart = 0, searchRanges = []) {
        const value = String(text || '');
        if (!value || !searchRanges.length) {
            return escHtml(value);
        }

        const absoluteEnd = absoluteStart + value.length;
        let output = '';
        let cursor = 0;
        searchRanges.forEach(range => {
            const start = Math.max(Number(range.start), absoluteStart);
            const end = Math.min(Number(range.end), absoluteEnd);
            if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
                return;
            }
            const localStart = start - absoluteStart;
            const localEnd = end - absoluteStart;
            if (localStart > cursor) {
                output += escHtml(value.slice(cursor, localStart));
            }
            const className = range.active ? 'explorer-search-match active' : 'explorer-search-match';
            output += `<mark class="${className}">${escHtml(value.slice(localStart, localEnd))}</mark>`;
            cursor = localEnd;
        });
        if (cursor < value.length) {
            output += escHtml(value.slice(cursor));
        }
        return output;
    }

    function explorerCodeSpan(className, text, absoluteStart = 0, searchRanges = []) {
        return `<span class="${className}">${explorerMarkedEscHtml(text, absoluteStart, searchRanges)}</span>`;
    }

    function explorerReadStringToken(content, start) {
        const quote = content[start];
        let index = start + 1;
        if (
            (quote === '"' || quote === "'")
            && content.slice(start, start + 3) === quote.repeat(3)
        ) {
            index = start + 3;
            while (index < content.length && content.slice(index, index + 3) !== quote.repeat(3)) {
                index += content[index] === '\\' ? 2 : 1;
            }
            return content.slice(start, Math.min(index + 3, content.length));
        }
        while (index < content.length) {
            if (content[index] === '\\') {
                index += 2;
                continue;
            }
            index += 1;
            if (content[index - 1] === quote) {
                break;
            }
        }
        return content.slice(start, index);
    }

    function explorerLogLevelClass(level) {
        const normalized = String(level || '').toUpperCase();
        if (normalized === 'TRACE' || normalized === 'DEBUG') {
            return normalized.toLowerCase();
        }
        if (normalized === 'WARN' || normalized === 'WARNING') {
            return 'warn';
        }
        if (normalized === 'ERROR' || normalized === 'CRITICAL' || normalized === 'FATAL') {
            return 'error';
        }
        return 'info';
    }

    function highlightExplorerLogLine(line, absoluteStart, searchRanges = []) {
        const timestampPattern = /^(\s*(?:\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|\d{2}:\d{2}:\d{2}(?:[.,]\d+)?|\[[^\]\n]*(?:\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2})[^\]\n]*\]))/;
        const levelPattern = /\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL)\b/i;
        const timestampMatch = line.match(timestampPattern);
        let output = '';
        let cursor = 0;

        if (timestampMatch) {
            const token = timestampMatch[1];
            output += explorerCodeSpan('explorer-log-timestamp', token, absoluteStart, searchRanges);
            cursor = token.length;
        }

        const levelMatch = line.slice(cursor).match(levelPattern);
        if (levelMatch && EXPLORER_LOG_LEVELS.has(levelMatch[1].toUpperCase())) {
            const levelStart = cursor + Number(levelMatch.index || 0);
            if (levelStart > cursor) {
                output += explorerMarkedEscHtml(line.slice(cursor, levelStart), absoluteStart + cursor, searchRanges);
            }
            const level = levelMatch[1];
            output += explorerCodeSpan(
                `explorer-log-level ${explorerLogLevelClass(level)}`,
                level,
                absoluteStart + levelStart,
                searchRanges
            );
            cursor = levelStart + level.length;
        }

        if (cursor < line.length) {
            output += explorerMarkedEscHtml(line.slice(cursor), absoluteStart + cursor, searchRanges);
        }
        return output;
    }

    function highlightExplorerLog(content, searchRanges = []) {
        const source = String(content || '');
        const absoluteStart = Number(arguments[2] || 0);
        let output = '';
        let index = 0;
        while (index < source.length) {
            const newlineIndex = source.indexOf('\n', index);
            const lineEnd = newlineIndex === -1 ? source.length : newlineIndex;
            output += highlightExplorerLogLine(source.slice(index, lineEnd), absoluteStart + index, searchRanges);
            if (newlineIndex === -1) {
                break;
            }
            output += explorerMarkedEscHtml('\n', absoluteStart + lineEnd, searchRanges);
            index = lineEnd + 1;
        }
        return output;
    }

    function highlightExplorerCode(content, language, searchRanges = []) {
        const normalizedLanguage = normalizeExplorerLanguage(language);
        const absoluteStart = Number(arguments[3] || 0);
        if (!normalizedLanguage) {
            return explorerMarkedEscHtml(content, absoluteStart, searchRanges);
        }

        if (normalizedLanguage === 'log') {
            return highlightExplorerLog(content, searchRanges, absoluteStart);
        }

        const keywords = new Set(EXPLORER_CODE_KEYWORDS[normalizedLanguage] || []);
        const builtins = new Set(EXPLORER_CODE_BUILTINS[normalizedLanguage] || []);
        const caseInsensitiveKeywords = normalizedLanguage === 'sql';
        let output = '';
        let index = 0;

        while (index < content.length) {
            const current = content[index];
            const next = content[index + 1] || '';

            if ((normalizedLanguage === 'html' || normalizedLanguage === 'xml') && content.startsWith('<!--', index)) {
                const endIndex = content.indexOf('-->', index + 4);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex + 3);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (EXPLORER_C_LIKE_LANGUAGES.has(normalizedLanguage) && current === '/' && next === '/') {
                const endIndex = content.indexOf('\n', index + 2);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (EXPLORER_C_LIKE_LANGUAGES.has(normalizedLanguage) && current === '/' && next === '*') {
                const endIndex = content.indexOf('*/', index + 2);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex + 2);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (EXPLORER_HASH_COMMENT_LANGUAGES.has(normalizedLanguage) && current === '#') {
                const endIndex = content.indexOf('\n', index + 1);
                const token = content.slice(index, endIndex === -1 ? content.length : endIndex);
                output += explorerCodeSpan('explorer-code-comment', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (current === '"' || current === "'" || (current === '`' && !['json', 'jsonl', 'yaml', 'toml'].includes(normalizedLanguage))) {
                const token = explorerReadStringToken(content, index);
                output += explorerCodeSpan('explorer-code-string', token, absoluteStart + index, searchRanges);
                index += token.length;
                continue;
            }

            if (/[0-9]/.test(current)) {
                const match = content.slice(index).match(/^(0x[\da-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/);
                if (match) {
                    output += explorerCodeSpan('explorer-code-number', match[0], absoluteStart + index, searchRanges);
                    index += match[0].length;
                    continue;
                }
            }

            if (/[A-Za-z_$]/.test(current)) {
                const match = content.slice(index).match(/^[A-Za-z_$][\w$-]*/);
                if (match) {
                    const token = match[0];
                    const keywordToken = caseInsensitiveKeywords ? token.toUpperCase() : token;
                    if (keywords.has(keywordToken)) {
                        output += explorerCodeSpan('explorer-code-keyword', token, absoluteStart + index, searchRanges);
                    } else if (builtins.has(token)) {
                        output += explorerCodeSpan('explorer-code-builtin', token, absoluteStart + index, searchRanges);
                    } else {
                        output += explorerMarkedEscHtml(token, absoluteStart + index, searchRanges);
                    }
                    index += token.length;
                    continue;
                }
            }

            output += explorerMarkedEscHtml(current, absoluteStart + index, searchRanges);
            index += 1;
        }

        return output;
    }

    function renderExplorerMessage(index, message) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (list && viewer) {
            // The placeholder replaces the tab's content: the DOM no longer
            // belongs to any tab, so view captures must skip until re-render.
            if (pane) {
                pane._explorerRenderedTabId = '';
            }
            list.classList.remove('file-view');
            viewer.innerHTML = `<div class="explorer-message">${escHtml(message)}</div>`;
            renderExplorerTabStrip(index);
        }
    }

    function renderExplorerDirectoryOpenError(index, message) {
        const pane = terminals[index];
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !viewer || pane._explorerMode !== 'directory') {
            renderExplorerMessage(index, message);
            return;
        }
        pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
        renderExplorerDirectorySearchControls(index);
        renderExplorerDirectoryRows(index);
        const notice = document.createElement('div');
        notice.className = 'explorer-message explorer-file-open-error';
        notice.textContent = message;
        viewer.prepend(notice);
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
        // Only tracked worktree changes can be discarded with git restore;
        // untracked and conflicted files are intentionally excluded so nothing
        // is silently deleted or overwritten (ISSUE-2026-018).
        return ['modified', 'deleted', 'renamed'].includes(status || '');
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
            let actionButton = '';
            if (action === 'stage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-stage-btn" data-explorer-git-stage="${escHtml(path)}" title="Stage changes" aria-label="Stage changes">+</button>`;
            } else if (action === 'unstage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-unstage-btn" data-explorer-git-unstage="${escHtml(path)}" title="Unstage changes" aria-label="Unstage changes">−</button>`;
            }
            const revertButton = (action === 'stage' && explorerGitCanRevert(status))
                ? `<button type="button" class="explorer-search-btn explorer-git-revert-btn" data-explorer-git-revert="${escHtml(path)}" title="Discard changes (revert)" aria-label="Discard changes (revert)">${EXPLORER_GIT_REVERT_ICON}</button>`
                : '';
            return `
                <div class="explorer-diff-commit-file" title="${escHtml(path)}" data-explorer-copy-path="${escHtml(path)}">
                    ${explorerDiffSidebarStatusHtml(file.git)}
                    ${explorerFileTypeIconHtml(path)}
                    <button type="button" class="explorer-diff-commit-file-path" ${pathAction}>${escHtml(path || file.name || 'Changed file')}</button>
                    <span class="explorer-diff-commit-file-actions">
                        ${revertButton}
                        ${actionButton}
                        <button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-git-open-folder="${escHtml(path)}" title="Open containing folder" aria-label="Open containing folder">↪</button>
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
    function explorerRootDirectory(index) {
        const session = terminals[index]?._session || {};
        return session.explorer_root_directory || session.directory || '';
    }

    function explorerJoinRootPath(root, relativePath) {
        const rel = String(relativePath || '').replace(/^[\\/]+/, '');
        const base = String(root || '');
        if (!base) {
            return rel;
        }
        // Match the root's separator style so a copied Windows path is not
        // mangled with forward slashes (and remote/POSIX roots stay POSIX).
        const usesBackslash = base.includes('\\') && !base.includes('/');
        const separator = usesBackslash ? '\\' : '/';
        const trimmedBase = base.replace(/[\\/]+$/, '') || base;
        if (!rel) {
            return trimmedBase;
        }
        const nativeRel = usesBackslash ? rel.replace(/\//g, '\\') : rel.replace(/\\/g, '/');
        return `${trimmedBase}${separator}${nativeRel}`;
    }

    function dismissExplorerContextMenu() {
        document.getElementById('explorer-ctx-menu')?.remove();
        document.removeEventListener('keydown', _explorerContextMenuKeydown, true);
        document.removeEventListener('mousedown', _explorerContextMenuOutside, true);
    }

    function _explorerContextMenuOutside(event) {
        const menu = document.getElementById('explorer-ctx-menu');
        if (!menu || !menu.contains(event.target)) {
            dismissExplorerContextMenu();
        }
    }

    function _explorerContextMenuKeydown(event) {
        const menu = document.getElementById('explorer-ctx-menu');
        if (!menu) {
            return;
        }
        const items = Array.from(menu.querySelectorAll('button'));
        if (!items.length) {
            return;
        }
        const currentIndex = items.indexOf(document.activeElement);
        if (event.key === 'Escape') {
            event.preventDefault();
            dismissExplorerContextMenu();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            items[(currentIndex + 1) % items.length].focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            items[(currentIndex - 1 + items.length) % items.length].focus();
        } else if (event.key === 'Tab') {
            event.preventDefault();
        }
    }

    function showExplorerContextMenu(x, y, items) {
        dismissExplorerContextMenu();
        const menu = document.createElement('div');
        menu.id = 'explorer-ctx-menu';
        menu.setAttribute('role', 'menu');
        items.forEach(({ label, action }) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'menuitem');
            button.textContent = label;
            button.addEventListener('click', () => {
                action();
                dismissExplorerContextMenu();
            });
            menu.appendChild(button);
        });
        menu.style.visibility = 'hidden';
        document.body.appendChild(menu);

        const rect = menu.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        menu.style.left = `${Math.max(8, Math.min(x, vw - rect.width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, vh - rect.height - 8))}px`;
        menu.style.visibility = 'visible';
        menu.querySelector('button')?.focus();

        // Defer the outside-dismiss listener so the opening interaction does not
        // immediately close the menu.
        window.setTimeout(() => {
            document.addEventListener('mousedown', _explorerContextMenuOutside, true);
        }, 0);
        document.addEventListener('keydown', _explorerContextMenuKeydown, true);
    }

    function handleExplorerCopyPathMenu(event, index) {
        const row = event.target.closest('[data-explorer-copy-path]');
        if (!row) {
            return;
        }
        event.preventDefault();
        const relativePath = row.dataset.explorerCopyPath || '';
        const absolutePath = explorerJoinRootPath(explorerRootDirectory(index), relativePath);
        const items = [
            { label: 'Copy path', action: () => _copyText(absolutePath || relativePath) },
        ];
        if (relativePath) {
            items.push({ label: 'Copy relative path', action: () => _copyText(relativePath) });
        }
        showExplorerContextMenu(event.clientX, event.clientY, items);
    }

    function wireExplorerCopyPathMenu(panel, index) {
        if (!panel || panel.dataset.copyPathMenuWired === 'true') {
            return;
        }
        panel.dataset.copyPathMenuWired = 'true';
        panel.addEventListener('contextmenu', event => handleExplorerCopyPathMenu(event, index));
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
            panel.innerHTML = `<div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>`;
            return;
        }

        const repo = pane._explorerGitRepo || {};
        const git = repo.git || {};
        const errorBanner = pane._explorerGitRepoError
            ? `<div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>`
            : '';
        const changes = Array.isArray(repo.changes) ? repo.changes : [];
        const { staged, unstaged } = splitExplorerGitChanges(changes);
        /* Discard All mirrors the per-row Revert guard: only tracked worktree
           changes count, so an all-untracked list keeps the button disabled. */
        const discardable = unstaged.filter(file => explorerGitCanRevert(file.git && file.git.status));
        const commits = Array.isArray(repo.commits) ? repo.commits : [];
        const expandedCommits = ensureExplorerDiffExpandedCommits(pane);
        const busy = Boolean(pane._explorerGitActionBusy);
        const commitMessage = typeof pane._explorerGitCommitMessage === 'string' ? pane._explorerGitCommitMessage : '';
        const hasUpstream = git.ahead !== null && git.ahead !== undefined;
        const publishLabel = hasUpstream ? 'Push' : 'Publish branch';
        const branchText = explorerGitSummaryText(git) || 'Git';
        const commitRows = commits.length
            ? commits.map(commit => {
                const hash = commit.hash || '';
                const expanded = hash && expandedCommits.has(`explorer:${hash}`);
                return `
                    <button type="button" class="explorer-diff-commit" data-explorer-git-commit-toggle="${escHtml(hash)}" ${hash ? '' : 'disabled'} title="${escHtml(commit.line || '')}" aria-expanded="${expanded ? 'true' : 'false'}">
                        <span class="explorer-diff-commit-graph">${explorerGitGraphHtml(commit.graph)}</span>
                        <span class="explorer-diff-commit-toggle" aria-hidden="true">${expanded ? '▾' : '▸'}</span>
                        <span class="explorer-diff-commit-subject"><span class="explorer-diff-commit-hash">${escHtml(hash ? hash.slice(0, 7) : '')}</span> ${escHtml(commit.subject || commit.line || '')}</span>
                    </button>
                    ${expanded ? `<div class="explorer-diff-commit-files">${renderExplorerGitFileRows(index, commit.files, { emptyText: 'No files recorded for this commit.', commitHash: hash })}</div>` : ''}
                `;
            }).join('')
            : '<div class="explorer-diff-sidebar-empty">No commits in this scope.</div>';

        panel.innerHTML = `
            ${errorBanner}
            <div class="explorer-diff-sidebar-section explorer-git-repo-bar">
                <span class="explorer-git-repo-branch" title="${escHtml(git.repo_root || branchText)}">${escHtml(branchText)}</span>
                <button type="button" class="explorer-git-publish-btn" data-explorer-git-publish ${busy ? 'disabled' : ''} title="Push the current branch to its remote">${escHtml(publishLabel)}</button>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title">Staged Changes</div>
                <div class="explorer-diff-commit-files">
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
                        <button type="button" class="explorer-search-btn explorer-git-stage-btn explorer-git-stage-all-btn" data-explorer-git-stage-all ${(busy || !unstaged.length) ? 'disabled' : ''} title="Stage all changes" aria-label="Stage all changes">+</button>
                    </span>
                </div>
                <div class="explorer-diff-commit-files">
                    ${renderExplorerGitFileRows(index, unstaged, { emptyText: 'No unstaged changes.', action: 'stage' })}
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title">Graph</div>
                ${commitRows}
            </div>
        `;
        const commitMessageInput = panel.querySelector(`#explorer-git-commit-message-${index}`);
        if (commitMessageInput) {
            commitMessageInput.addEventListener('input', () => {
                pane._explorerGitCommitMessage = commitMessageInput.value;
            });
        }
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
                explorerGitRevertFile(index, button.dataset.explorerGitRevert || '');
            });
        });
        const stageAllButton = panel.querySelector('[data-explorer-git-stage-all]');
        if (stageAllButton) {
            stageAllButton.addEventListener('click', () => explorerGitStageAll(index));
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
            button.addEventListener('click', () => {
                const commit = button.dataset.explorerGitCommitToggle || '';
                if (!commit) {
                    return;
                }
                const expanded = ensureExplorerDiffExpandedCommits(pane);
                const key = `explorer:${commit}`;
                if (expanded.has(key)) {
                    expanded.delete(key);
                } else {
                    expanded.add(key);
                }
                renderExplorerGitPanel(index);
            });
        });
    }

    function renderExplorerGitPanels(index) {
        renderExplorerGitPanel(index);
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
        renderExplorerGitPanels(index);
    }

    function syncExplorerSidebar(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !main || !sidebar) {
            return;
        }

        const treeOpen = Boolean(pane._explorerTreeSidebarOpen);
        const gitOpen = Boolean(pane._explorerGitSidebarOpen);
        const anyOpen = treeOpen || gitOpen;
        const treePanel = document.getElementById(`explorer-tree-panel-${index}`);
        const gitPanel = document.getElementById(`explorer-git-panel-${index}`);
        const splitter = document.getElementById(`explorer-sidebar-splitter-${index}`);
        const handle = document.getElementById(`explorer-sidebar-resizer-${index}`);
        const treeButton = document.getElementById(`explorer-tree-toggle-${index}`);
        const gitButton = document.getElementById(`explorer-git-toggle-${index}`);

        main.classList.toggle('tree-open', treeOpen);
        main.classList.toggle('git-open', gitOpen);
        sidebar.hidden = !anyOpen;
        sidebar.classList.toggle('split', treeOpen && gitOpen);
        if (treePanel) {
            treePanel.hidden = !treeOpen;
        }
        if (gitPanel) {
            gitPanel.hidden = !gitOpen;
        }
        if (splitter) {
            splitter.hidden = !(treeOpen && gitOpen);
        }
        if (handle) {
            handle.hidden = !anyOpen;
        }
        if (treeButton) {
            treeButton.setAttribute('aria-pressed', treeOpen ? 'true' : 'false');
        }
        if (gitButton) {
            gitButton.setAttribute('aria-pressed', gitOpen ? 'true' : 'false');
        }

        if (anyOpen) {
            applyExplorerSidebarWidth(index);
            wireExplorerSidebarResize(index);
        }
        if (treeOpen && gitOpen) {
            applyExplorerSidebarSplit(index);
            wireExplorerSidebarSplitter(index);
        }
    }

    function restoreExplorerSidebarState(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        syncExplorerSidebar(index);
        if (pane._explorerTreeSidebarOpen) {
            loadExplorerTree(index);
        }
        if (pane._explorerGitSidebarOpen) {
            loadExplorerGitRepo(index);
        }
    }

    function setExplorerGitSidebarOpen(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerGitSidebarOpen = Boolean(open);
        syncExplorerSidebar(index);
        if (pane._explorerGitSidebarOpen) {
            loadExplorerGitRepo(index);
        }
    }

    function toggleExplorerGitSidebar(index) {
        const pane = terminals[index];
        setExplorerGitSidebarOpen(index, !pane?._explorerGitSidebarOpen);
    }

    function setExplorerTreeSidebarOpen(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerTreeSidebarOpen = Boolean(open);
        syncExplorerSidebar(index);
        if (pane._explorerTreeSidebarOpen) {
            loadExplorerTree(index);
        }
    }

    function toggleExplorerTreeSidebar(index) {
        const pane = terminals[index];
        setExplorerTreeSidebarOpen(index, !pane?._explorerTreeSidebarOpen);
    }

    function applyExplorerSidebarWidth(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        if (!pane || !main) {
            return;
        }
        const width = Math.max(180, Math.min(Number(pane._explorerSidebarWidth || 260), 520));
        pane._explorerSidebarWidth = width;
        main.style.setProperty('--explorer-sidebar-width', `${width}px`);
    }

    function wireExplorerSidebarResize(index) {
        const pane = terminals[index];
        const main = document.getElementById(`explorer-main-${index}`);
        const handle = document.getElementById(`explorer-sidebar-resizer-${index}`);
        if (!pane || !main || !handle) {
            return;
        }
        applyExplorerSidebarWidth(index);
        if (handle.dataset.bound) {
            return;
        }

        handle.dataset.bound = 'true';
        handle.addEventListener('pointerdown', event => {
            event.preventDefault();
            handle.classList.add('dragging');
            handle.setPointerCapture?.(event.pointerId);
            const onMove = moveEvent => {
                const rect = main.getBoundingClientRect();
                const maxWidth = Math.min(520, Math.max(180, rect.width - 280));
                const nextWidth = Math.max(180, Math.min(Math.round(moveEvent.clientX - rect.left), maxWidth));
                pane._explorerSidebarWidth = nextWidth;
                main.style.setProperty('--explorer-sidebar-width', `${nextWidth}px`);
            };
            const onEnd = endEvent => {
                handle.classList.remove('dragging');
                handle.releasePointerCapture?.(endEvent.pointerId);
                window.removeEventListener('pointermove', onMove);
                window.removeEventListener('pointerup', onEnd);
                window.removeEventListener('pointercancel', onEnd);
            };
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onEnd, { once: true });
            window.addEventListener('pointercancel', onEnd, { once: true });
        });
    }

    /* Unset tree height means the two stacked panels share the sidebar evenly. */
    function applyExplorerSidebarSplit(index) {
        const pane = terminals[index];
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        if (!pane || !sidebar) {
            return;
        }
        const stored = Number(pane._explorerSidebarTreeHeight || 0);
        if (!stored) {
            sidebar.style.removeProperty('--explorer-sidebar-tree-height');
            return;
        }
        const height = sidebar.getBoundingClientRect().height;
        const maxTop = Math.max(EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT, height - EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT - 6);
        const top = Math.max(EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT, Math.min(stored, maxTop));
        pane._explorerSidebarTreeHeight = top;
        sidebar.style.setProperty('--explorer-sidebar-tree-height', `${top}px`);
    }

    function wireExplorerSidebarSplitter(index) {
        const pane = terminals[index];
        const sidebar = document.getElementById(`explorer-sidebar-${index}`);
        const splitter = document.getElementById(`explorer-sidebar-splitter-${index}`);
        if (!pane || !sidebar || !splitter || splitter.dataset.bound) {
            return;
        }

        splitter.dataset.bound = 'true';
        splitter.addEventListener('pointerdown', event => {
            event.preventDefault();
            splitter.classList.add('dragging');
            splitter.setPointerCapture?.(event.pointerId);
            const onMove = moveEvent => {
                const rect = sidebar.getBoundingClientRect();
                const maxTop = Math.max(
                    EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT,
                    rect.height - EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT - 6
                );
                const nextTop = Math.max(
                    EXPLORER_SIDEBAR_MIN_PANEL_HEIGHT,
                    Math.min(Math.round(moveEvent.clientY - rect.top), maxTop)
                );
                pane._explorerSidebarTreeHeight = nextTop;
                sidebar.style.setProperty('--explorer-sidebar-tree-height', `${nextTop}px`);
            };
            const onEnd = endEvent => {
                splitter.classList.remove('dragging');
                splitter.releasePointerCapture?.(endEvent.pointerId);
                window.removeEventListener('pointermove', onMove);
                window.removeEventListener('pointerup', onEnd);
                window.removeEventListener('pointercancel', onEnd);
            };
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onEnd, { once: true });
            window.addEventListener('pointercancel', onEnd, { once: true });
        });
    }

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

    async function loadExplorerTreeChildren(index, path) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return [];
        }

        ensureExplorerTreeState(pane);
        const key = String(path || '');
        if (pane._explorerTreeChildren.has(key)) {
            return pane._explorerTreeChildren.get(key);
        }
        if (pane._explorerTreeLoading.has(key)) {
            return [];
        }

        pane._explorerTreeLoading.add(key);
        pane._explorerTreeErrors.delete(key);
        renderExplorerTreePanel(index);
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

    function explorerTreeRowHtml(pane, entry, depth) {
        const isDirectory = entry.type === 'directory';
        const path = entry.path || '';
        const expanded = isDirectory && pane._explorerTreeExpanded.has(path);
        const active = explorerTreeRowIsActive(pane, entry);
        const action = isDirectory
            ? `data-explorer-tree-dir="${escHtml(path)}" aria-expanded="${expanded ? 'true' : 'false'}"`
            : `data-explorer-tree-file="${escHtml(path)}"`;
        const chevron = isDirectory ? (expanded ? '▾' : '▸') : '';
        const badge = explorerGitStatusLabel(entry.git) ? explorerGitBadgeHtml(entry.git) : '';
        const openFolder = isDirectory
            ? `<button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-tree-open-folder="${escHtml(path)}" title="Open folder in the explorer list" aria-label="Open folder in the explorer list">↪</button>`
            : '';
        const openTab = isDirectory
            ? ''
            : `<button type="button" class="explorer-search-btn explorer-open-tab-btn" data-explorer-tree-open-tab="${escHtml(path)}" title="Open in a new tab" aria-label="Open ${escHtml(entry.name || path)} in a new tab">↗</button>`;

        return `
            <div class="explorer-tree-row${active ? ' active' : ''}" data-explorer-copy-path="${escHtml(path)}">
                <button type="button" class="explorer-tree-main" ${action} style="padding-left:${7 + depth * EXPLORER_TREE_INDENT_PX}px" title="${escHtml(path)}">
                    <span class="explorer-tree-chevron" aria-hidden="true">${chevron}</span>
                    ${isDirectory ? EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(entry.name || path)}
                    <span class="explorer-tree-name">${escHtml(entry.name || path)}</span>
                </button>
                ${badge}
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
        if (pane._explorerTreeLoading.has(path)) {
            return `<div class="explorer-tree-loading" ${indent}>Loading...</div>`;
        }

        const entries = pane._explorerTreeChildren.get(path);
        if (!entries) {
            return '';
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
        panel.innerHTML = `
            <div class="explorer-tree-section">
                <div class="explorer-tree-title">Files</div>
                <div class="explorer-tree-children">${renderExplorerTreeNodes(pane, '', 0)}</div>
            </div>
        `;
        panel.querySelectorAll('[data-explorer-tree-dir]').forEach(button => {
            button.addEventListener('click', () => {
                toggleExplorerTreeDirectory(index, button.dataset.explorerTreeDir || '');
            });
        });
        panel.querySelectorAll('[data-explorer-tree-file]').forEach(button => {
            button.addEventListener('click', () => {
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
                openExplorerFile(index, button.dataset.explorerTreeOpenTab || '', { pinned: true });
            });
        });
    }

    async function toggleExplorerTreeDirectory(index, path) {
        const pane = terminals[index];
        if (!pane || !path) {
            return;
        }

        ensureExplorerTreeState(pane);
        /* 2.d: a directory click keeps its expand/collapse toggle AND browses
           the directory in the Preview tab so the user can drill in from the
           main pane. Clicking the directory the Preview tab already shows
           only toggles its expansion. */
        const alreadyShown = pane._explorerMode === 'directory'
            && (pane._explorerPath || '') === path;
        if (pane._explorerTreeExpanded.has(path)) {
            pane._explorerTreeExpanded.delete(path);
            renderExplorerTreePanel(index);
            if (!alreadyShown) {
                await loadExplorerPane(index, path);
            }
            return;
        }

        pane._explorerTreeExpanded.add(path);
        pane._explorerTreeErrors.delete(path);
        renderExplorerTreePanel(index);
        const childrenLoading = loadExplorerTreeChildren(index, path);
        if (!alreadyShown) {
            await loadExplorerPane(index, path);
        }
        await childrenLoading;
    }

    /* Expand every ancestor of the pane's current directory or open file. */
    async function revealExplorerTreePath(index) {
        const pane = terminals[index];
        if (!pane?._explorerTreeSidebarOpen) {
            return;
        }

        ensureExplorerTreeState(pane);
        const target = pane._explorerMode === 'file'
            ? (pane._explorerFilePath || '')
            : (pane._explorerPath || '');
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
    }

    async function loadExplorerTree(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        ensureExplorerTreeState(pane);
        renderExplorerTreePanel(index);
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
        renderExplorerTreePanel(index);
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
        if (!pane || !sessionId || pane._explorerGitRepoLoaded || pane._explorerGitRepoLoading) {
            renderExplorerGitPanels(index);
            return;
        }

        pane._explorerGitRepoLoading = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/git/repo`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git repository');
            }
            pane._explorerGitRepoLoaded = true;
            pane._explorerGitRepo = data;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git repository failed:', error);
            pane._explorerGitRepoError = error.message || 'Failed to load Git repository.';
        } finally {
            pane._explorerGitRepoLoading = false;
            renderExplorerGitPanels(index);
        }
    }

    async function performExplorerGitAction(index, endpoint, body) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || pane._explorerGitActionBusy) {
            return false;
        }
        pane._explorerGitActionBusy = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        let succeeded = false;
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/git/${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body || {}),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Git action failed');
            }
            pane._explorerGitRepo = data;
            pane._explorerGitRepoLoaded = true;
            succeeded = true;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git action failed:', error);
            pane._explorerGitRepoError = error.message || 'Git action failed.';
        } finally {
            pane._explorerGitActionBusy = false;
            renderExplorerGitPanels(index);
        }
        if (succeeded && pane._explorerMode === 'directory') {
            loadExplorerPane(index, null, { force: true, showLoading: false });
        }
        if (succeeded && EXPLORER_GIT_WORKTREE_ENDPOINTS.has(endpoint)) {
            await refreshExplorerAfterGitAction(index, body && body.path ? String(body.path) : '');
        }
        return succeeded;
    }

    /* Git actions that change working-tree or index state — publish only talks
       to the remote, so it never needs the ISSUE-2026-034 refresh below. */
    const EXPLORER_GIT_WORKTREE_ENDPOINTS = new Set([
        'stage', 'unstage', 'revert', 'commit', 'stage-all', 'discard-all',
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
            copy: 'Discard the unstaged changes in every tracked file?',
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

    /* Discarding working-tree edits is irreversible, so it goes through the
       in-page confirm shell (WebView2 blocks window.confirm) before the
       narrow git-restore route runs; a reverted open file reloads in place. */
    async function explorerGitRevertFile(index, path) {
        if (!path) {
            return;
        }
        const confirmed = await openGenericConfirmModal({
            title: 'Discard changes?',
            copy: `Discard the unstaged changes in "${path}"?`,
            note: 'This unstaged edit will be lost. Any staged version of the file is kept.',
            confirmLabel: 'Discard changes',
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
        const committed = await performExplorerGitAction(index, 'commit', { message });
        if (committed) {
            pane._explorerGitCommitMessage = '';
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

    function renderExplorerDiff(index) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-diff-code-${index}`);
        if (!pane || !code) {
            return;
        }
        const diff = pane._explorerDiffContent || '';
        if (!diff) {
            code.innerHTML = '<span class="explorer-diff-empty">No Git diff for selected file.</span>';
            return;
        }
        code.innerHTML = renderExplorerSideBySideDiff(index, diff);
    }

    function explorerDiffLanguage(index) {
        const pane = terminals[index];
        const filePath = pane?._explorerFilePath || '';
        return normalizeExplorerLanguage(pane?._explorerFileLanguage || '') || explorerCodeLanguage(filePath);
    }

    function explorerDiffLineCodeHtml(index, text) {
        return highlightExplorerCode(String(text || ''), explorerDiffLanguage(index)) || '&nbsp;';
    }

    function explorerDiffCellHtml(index, cell, side) {
        if (!cell) {
            return `
                <div class="explorer-diff-cell empty ${side}">
                    <span class="explorer-diff-line-number"></span>
                    <span class="explorer-diff-line-code"></span>
                </div>
            `;
        }
        return `
            <div class="explorer-diff-cell ${escHtml(cell.type || 'context')} ${side}">
                <span class="explorer-diff-line-number">${cell.number ? escHtml(String(cell.number)) : ''}</span>
                <span class="explorer-diff-line-code">${explorerDiffLineCodeHtml(index, cell.text || '')}</span>
            </div>
        `;
    }

    function explorerDiffRowHtml(index, left, right) {
        if (left?.type === 'hunk') {
            return `
                <div class="explorer-diff-row">
                    <div class="explorer-diff-cell hunk">${escHtml(left.text || '')}</div>
                </div>
            `;
        }
        return `
            <div class="explorer-diff-row">
                ${explorerDiffCellHtml(index, left, 'old')}
                ${explorerDiffCellHtml(index, right, 'new')}
            </div>
        `;
    }

    function renderExplorerSideBySideDiff(index, diff) {
        const source = String(diff || '');
        if (!source.trim()) {
            return '<span class="explorer-diff-empty">No Git diff for selected file.</span>';
        }

        const lines = source.split(/\r?\n/);
        const rows = [];
        let oldLine = 0;
        let newLine = 0;
        const pendingDeletes = [];

        const flushDeletes = () => {
            while (pendingDeletes.length) {
                rows.push(explorerDiffRowHtml(index, pendingDeletes.shift(), null));
            }
        };

        lines.forEach(line => {
            const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
            if (hunk) {
                flushDeletes();
                oldLine = Number(hunk[1]);
                newLine = Number(hunk[2]);
                rows.push(explorerDiffRowHtml(index, { type: 'hunk', text: line }, null));
                return;
            }
            if (!oldLine && !newLine) {
                return;
            }
            if (line.startsWith('\\ No newline')) {
                return;
            }
            if (line.startsWith('-') && !line.startsWith('---')) {
                pendingDeletes.push({
                    type: 'delete',
                    number: oldLine,
                    text: line.slice(1)
                });
                oldLine += 1;
                return;
            }
            if (line.startsWith('+') && !line.startsWith('+++')) {
                const right = {
                    type: 'add',
                    number: newLine,
                    text: line.slice(1)
                };
                newLine += 1;
                rows.push(explorerDiffRowHtml(index, pendingDeletes.shift() || null, right));
                return;
            }
            if (line.startsWith(' ')) {
                flushDeletes();
                rows.push(explorerDiffRowHtml(index,
                    { type: 'context', number: oldLine, text: line.slice(1) },
                    { type: 'context', number: newLine, text: line.slice(1) }
                ));
                oldLine += 1;
                newLine += 1;
            }
        });

        flushDeletes();
        return `<div class="explorer-side-by-side-diff">${rows.join('')}</div>`;
    }

    function setExplorerDiffSplit(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        setExplorerFileView(index, open ? 'diff' : (pane._explorerLastFileView || 'source'));
    }

    function toggleExplorerDiffSplit(index) {
        const pane = terminals[index];
        setExplorerDiffSplit(index, !pane?._explorerDiffSplit);
    }

    async function loadExplorerDiff(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const code = document.getElementById(`explorer-diff-code-${index}`);
        const diffPath = pane?._explorerFilePath || '';
        const commit = pane?._explorerDiffCommit || '';
        // Changed-file rows request a section-specific diff (worktree vs staged)
        // so a partially staged file never shows the other section's hunks;
        // commit-history rows and legacy callers fall back to the HEAD diff.
        const diffMode = commit ? 'commit' : (pane?._explorerDiffMode || 'head');
        const cacheKey = explorerDiffCacheKey(diffPath, commit, diffMode);
        if (!pane || !sessionId || !diffPath || !code) {
            renderExplorerDiff(index);
            return;
        }
        if (pane._explorerDiffLoaded && pane._explorerDiffCacheKey === cacheKey) {
            renderExplorerDiff(index);
            applyExplorerPendingDiffScroll(index);
            return;
        }

        code.textContent = 'Loading diff...';
        try {
            const params = new URLSearchParams({
                path: diffPath,
                mode: diffMode
            });
            if (commit) {
                params.set('commit', commit);
            }
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/git/diff?${params.toString()}`
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git diff');
            }
            pane._explorerDiffLoaded = true;
            pane._explorerDiffCacheKey = cacheKey;
            pane._explorerDiffContent = data.diff || '';
            renderExplorerDiff(index);
            applyExplorerPendingDiffScroll(index);
            if (activeExplorerFileView(index) === 'diff') {
                applyExplorerSearch(index);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git diff failed:', error);
            code.innerHTML = `<span class="explorer-diff-empty">${escHtml(error.message || 'Failed to load Git diff.')}</span>`;
        }
    }

    function setExplorerFileView(index, mode) {
        const normalizedMode =
            mode === 'preview' ? 'preview'
            : mode === 'diff' ? 'diff'
            : 'source';
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return;
        }

        const body = list.querySelector('.explorer-editor-body');
        const diffPanel = document.getElementById(`explorer-diff-panel-${index}`);
        const selectedMode = normalizedMode === 'diff' && diffPanel ? 'diff' : normalizedMode;
        const isDiffMode = selectedMode === 'diff';
        if (pane) {
            pane._explorerDiffSplit = isDiffMode;
            if (selectedMode === 'source' || selectedMode === 'preview') {
                pane._explorerLastFileView = selectedMode;
                // Sticky per-tab source/preview preference: the Preview tab
                // carries it across different files (2.e). Diff stays an
                // explicit per-view action, mirroring _explorerLastFileView.
                explorerActiveTab(pane).preferredMode = selectedMode;
            }
        }
        if (body) {
            body.classList.toggle('split-diff', isDiffMode);
        }
        if (diffPanel) {
            diffPanel.hidden = !isDiffMode;
        }
        list.querySelectorAll('[data-explorer-file-view]').forEach(button => {
            const isSelected = button.dataset.explorerFileView === selectedMode;
            button.setAttribute('aria-selected', isSelected ? 'true' : 'false');
            if (button.dataset.explorerDiffToggle) {
                button.setAttribute('aria-pressed', isDiffMode ? 'true' : 'false');
            }
        });
        list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
            panel.hidden = panel.dataset.explorerFilePanel !== selectedMode;
        });
        if (isDiffMode) {
            loadExplorerDiff(index);
            const state = pane ? ensureExplorerSearchState(pane, 'file') : null;
            if (state?.query) {
                applyExplorerSearch(index);
            }
        } else {
            applyExplorerSearch(index);
        }
    }

    function findExplorerMarkdownPreviewTargetIndex() {
        const activePane = document.activeElement?.closest?.('.explorer-pane');
        const candidates = [Number(activePane?.dataset.slot), _focusedTerminalIndex];
        for (let index = 0; index < terminals.length; index += 1) {
            candidates.push(index);
        }
        const seen = new Set();
        for (const index of candidates) {
            if (!Number.isInteger(index) || index < 0 || seen.has(index)) {
                continue;
            }
            seen.add(index);
            if (terminals[index]?._explorerMode === 'file'
                && document.getElementById(`explorer-preview-${index}`)) {
                return index;
            }
        }
        return -1;
    }

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

    function isExplorerSearchablePane(pane) {
        return pane?._explorerMode === 'file' || pane?._explorerMode === 'directory';
    }

    function ensureExplorerSearchState(pane, mode = pane?._explorerMode) {
        const key = mode === 'directory' ? '_explorerDirectorySearch' : '_explorerSearch';
        if (!pane[key]) {
            pane[key] = { query: '', activeIndex: 0, matchCount: 0, matchCapped: false, ranges: [], resultQuery: '' };
        }
        return pane[key];
    }

    function clampExplorerEditorFontSize(value) {
        const fontSize = Number(value);
        if (!Number.isFinite(fontSize)) {
            return EXPLORER_EDITOR_FONT_DEFAULT;
        }
        return Math.min(
            EXPLORER_EDITOR_FONT_MAX,
            Math.max(EXPLORER_EDITOR_FONT_MIN, Math.round(fontSize))
        );
    }

    /* Editor zoom is per explorer tab (2.e): each tab record keeps its own
       font size instead of sharing one pane-global value, so swapping tabs
       restores the zoom each tab was left at. */
    function ensureExplorerEditorFontSize(pane) {
        if (!pane) {
            return EXPLORER_EDITOR_FONT_DEFAULT;
        }
        const tab = explorerActiveTab(pane);
        tab.fontSize = clampExplorerEditorFontSize(
            tab.fontSize || EXPLORER_EDITOR_FONT_DEFAULT
        );
        return tab.fontSize;
    }

    function applyExplorerEditorFontSize(index) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        if (!pane || !list) {
            return;
        }

        const fontSize = ensureExplorerEditorFontSize(pane);
        list.style.setProperty('--explorer-editor-font-size', `${fontSize}px`);

        const value = list.querySelector(`[data-explorer-zoom-value="${index}"]`);
        if (value) {
            value.textContent = `${fontSize}px`;
        }
        const decrease = list.querySelector(`[data-explorer-zoom-decrease="${index}"]`);
        const increase = list.querySelector(`[data-explorer-zoom-increase="${index}"]`);
        if (decrease) {
            decrease.disabled = fontSize <= EXPLORER_EDITOR_FONT_MIN;
        }
        if (increase) {
            increase.disabled = fontSize >= EXPLORER_EDITOR_FONT_MAX;
        }
    }

    function stepExplorerEditorFontSize(index, delta) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const tab = explorerActiveTab(pane);
        const current = ensureExplorerEditorFontSize(pane);
        tab.fontSize = clampExplorerEditorFontSize(
            current + (Number(delta) || 0)
        );
        applyExplorerEditorFontSize(index);
    }

    function wireExplorerEditorZoomControls(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return;
        }
        const decrease = list.querySelector(`[data-explorer-zoom-decrease="${index}"]`);
        const increase = list.querySelector(`[data-explorer-zoom-increase="${index}"]`);
        if (decrease && !decrease.dataset.bound) {
            decrease.dataset.bound = 'true';
            decrease.addEventListener('click', () => {
                stepExplorerEditorFontSize(index, -EXPLORER_EDITOR_FONT_STEP);
            });
        }
        if (increase && !increase.dataset.bound) {
            increase.dataset.bound = 'true';
            increase.addEventListener('click', () => {
                stepExplorerEditorFontSize(index, EXPLORER_EDITOR_FONT_STEP);
            });
        }
        applyExplorerEditorFontSize(index);
    }

    // ── Markdown preview appearance (ISSUE-2026-030) ─────────────────────────
    // Two orthogonal axes: a reading-surface preset and a font family. Both are
    // bounded allowlists persisted in localStorage and applied idempotently to
    // every open preview via preset classes + CSS custom properties (defined
    // from tokens in terminals.css), so no palette literals live in JS.
    const EXPLORER_MD_PRESETS = ['default', 'paper', 'contrast', 'vscode'];
    const EXPLORER_MD_FONTS = [
        'system', 'serif', 'consolas', 'cascadia-code', 'jetbrains-mono', 'courier-new'
    ];
    const EXPLORER_MD_PRESET_DEFAULT = 'default';
    const EXPLORER_MD_FONT_DEFAULT = 'system';
    const EXPLORER_MD_PRESET_KEY = 'gridvibe.mdPreviewPreset';
    const EXPLORER_MD_FONT_KEY = 'gridvibe.mdPreviewFont';
    const EXPLORER_MD_PRESET_LABELS = {
        default: 'Default',
        paper: 'Paper',
        contrast: 'High contrast',
        vscode: 'Slate',
    };
    const EXPLORER_MD_FONT_LABELS = {
        system: 'System',
        serif: 'Serif',
        consolas: 'Consolas',
        'cascadia-code': 'Cascadia Code',
        'jetbrains-mono': 'JetBrains Mono',
        'courier-new': 'Courier New',
    };

    function readExplorerMarkdownPref(key, allowed, fallback) {
        let stored = '';
        try {
            stored = window.localStorage.getItem(key) || '';
        } catch (err) {
            stored = '';
        }
        return allowed.includes(stored) ? stored : fallback;
    }

    function explorerMarkdownAppearance() {
        return {
            preset: readExplorerMarkdownPref(
                EXPLORER_MD_PRESET_KEY, EXPLORER_MD_PRESETS, EXPLORER_MD_PRESET_DEFAULT
            ),
            font: readExplorerMarkdownPref(
                EXPLORER_MD_FONT_KEY, EXPLORER_MD_FONTS, EXPLORER_MD_FONT_DEFAULT
            ),
        };
    }

    function applyExplorerMarkdownAppearanceToElement(preview, appearance) {
        if (!preview) {
            return;
        }
        const { preset, font } = appearance || explorerMarkdownAppearance();
        EXPLORER_MD_PRESETS.forEach(name => preview.classList.remove(`md-preset-${name}`));
        EXPLORER_MD_FONTS.forEach(name => preview.classList.remove(`md-font-${name}`));
        preview.classList.add(`md-preset-${preset}`);
        preview.classList.add(`md-font-${font}`);
        preview.dataset.mdPreset = preset;
        preview.dataset.mdFont = font;
    }

    function applyExplorerMarkdownAppearanceToAll() {
        const appearance = explorerMarkdownAppearance();
        document.querySelectorAll('.explorer-markdown-preview').forEach(preview => {
            applyExplorerMarkdownAppearanceToElement(preview, appearance);
        });
    }

    function setExplorerMarkdownAppearance(patch) {
        const current = explorerMarkdownAppearance();
        const next = {
            preset: EXPLORER_MD_PRESETS.includes(patch?.preset) ? patch.preset : current.preset,
            font: EXPLORER_MD_FONTS.includes(patch?.font) ? patch.font : current.font,
        };
        try {
            window.localStorage.setItem(EXPLORER_MD_PRESET_KEY, next.preset);
            window.localStorage.setItem(EXPLORER_MD_FONT_KEY, next.font);
        } catch (err) {
            // Non-fatal: appearance still applies to the live DOM this session.
        }
        applyExplorerMarkdownAppearanceToAll();
        refreshExplorerMarkdownAppearanceMenu();
        return next;
    }

    function dismissExplorerMarkdownAppearanceMenu() {
        const menu = document.getElementById('explorer-md-menu');
        if (menu) {
            const anchor = document.querySelector('[data-explorer-md-appearance][aria-expanded="true"]');
            anchor?.setAttribute('aria-expanded', 'false');
            menu.remove();
        }
        document.removeEventListener('keydown', _explorerMarkdownMenuKeydown, true);
        document.removeEventListener('mousedown', _explorerMarkdownMenuOutside, true);
    }

    function _explorerMarkdownMenuOutside(event) {
        const menu = document.getElementById('explorer-md-menu');
        const anchor = document.querySelector('[data-explorer-md-appearance][aria-expanded="true"]');
        if (menu && !menu.contains(event.target) && !anchor?.contains(event.target)) {
            dismissExplorerMarkdownAppearanceMenu();
        }
    }

    function _explorerMarkdownMenuKeydown(event) {
        const menu = document.getElementById('explorer-md-menu');
        if (!menu) {
            return;
        }
        const items = Array.from(menu.querySelectorAll('button'));
        if (!items.length) {
            return;
        }
        const currentIndex = items.indexOf(document.activeElement);
        if (event.key === 'Escape') {
            event.preventDefault();
            dismissExplorerMarkdownAppearanceMenu();
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            items[(currentIndex + 1) % items.length].focus();
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            items[(currentIndex - 1 + items.length) % items.length].focus();
        } else if (event.key === 'Tab') {
            event.preventDefault();
        }
    }

    function refreshExplorerMarkdownAppearanceMenu() {
        const menu = document.getElementById('explorer-md-menu');
        if (!menu) {
            return;
        }
        const appearance = explorerMarkdownAppearance();
        menu.querySelectorAll('[data-md-preset]').forEach(button => {
            button.setAttribute('aria-checked', button.dataset.mdPreset === appearance.preset ? 'true' : 'false');
        });
        menu.querySelectorAll('[data-md-font]').forEach(button => {
            button.setAttribute('aria-checked', button.dataset.mdFont === appearance.font ? 'true' : 'false');
        });
    }

    function buildExplorerMarkdownMenuGroup(labelText, options, activeValue, datasetKey, onSelect) {
        const group = document.createElement('div');
        group.className = 'explorer-md-menu-group';
        group.setAttribute('role', 'group');
        group.setAttribute('aria-label', labelText);
        const label = document.createElement('span');
        label.className = 'explorer-md-menu-label';
        label.textContent = labelText;
        group.appendChild(label);
        options.forEach(({ value, label: optionLabel }) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.setAttribute('role', 'menuitemradio');
            button.dataset[datasetKey] = value;
            button.setAttribute('aria-checked', value === activeValue ? 'true' : 'false');
            const text = document.createElement('span');
            text.textContent = optionLabel;
            button.appendChild(text);
            button.addEventListener('click', () => onSelect(value));
            group.appendChild(button);
        });
        return group;
    }

    function showExplorerMarkdownAppearanceMenu(anchor) {
        dismissExplorerMarkdownAppearanceMenu();
        if (!anchor) {
            return;
        }
        const appearance = explorerMarkdownAppearance();
        const menu = document.createElement('div');
        menu.id = 'explorer-md-menu';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', 'Markdown preview appearance');
        menu.appendChild(buildExplorerMarkdownMenuGroup(
            'Theme',
            EXPLORER_MD_PRESETS.map(value => ({ value, label: EXPLORER_MD_PRESET_LABELS[value] })),
            appearance.preset,
            'mdPreset',
            value => setExplorerMarkdownAppearance({ preset: value })
        ));
        menu.appendChild(buildExplorerMarkdownMenuGroup(
            'Font',
            EXPLORER_MD_FONTS.map(value => ({ value, label: EXPLORER_MD_FONT_LABELS[value] })),
            appearance.font,
            'mdFont',
            value => setExplorerMarkdownAppearance({ font: value })
        ));

        menu.style.visibility = 'hidden';
        document.body.appendChild(menu);
        const anchorRect = anchor.getBoundingClientRect();
        const menuRect = menu.getBoundingClientRect();
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        let left = anchorRect.right - menuRect.width;
        let top = anchorRect.bottom + 4;
        if (top + menuRect.height > vh - 8) {
            top = Math.max(8, anchorRect.top - menuRect.height - 4);
        }
        menu.style.left = `${Math.max(8, Math.min(left, vw - menuRect.width - 8))}px`;
        menu.style.top = `${Math.max(8, top)}px`;
        menu.style.visibility = 'visible';
        anchor.setAttribute('aria-expanded', 'true');
        menu.querySelector('button[aria-checked="true"]')?.focus();

        window.setTimeout(() => {
            document.addEventListener('mousedown', _explorerMarkdownMenuOutside, true);
        }, 0);
        document.addEventListener('keydown', _explorerMarkdownMenuKeydown, true);
    }

    function activeExplorerFileView(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        const activeButton = list?.querySelector('[data-explorer-file-view][aria-selected="true"]');
        return activeButton?.dataset.explorerFileView || 'source';
    }

    function decorateExplorerSearchRanges(ranges, activeIndex) {
        return ranges.map((range, rangeIndex) => ({
            ...range,
            active: rangeIndex === activeIndex
        }));
    }

    function ensureExplorerMarkdownCollapsedLines(pane) {
        if (!pane) {
            return new Set();
        }
        const tab = explorerActiveTab(pane);
        if (!(tab.collapsedLines instanceof Set)) {
            tab.collapsedLines = new Set();
        }
        return tab.collapsedLines;
    }

    function explorerMarkdownHeadingLevel(line) {
        const match = String(line || '').match(/^(#{1,6})(?:\s+|$)/);
        return match ? match[1].length : 0;
    }

    function explorerMarkdownFenceMarker(line) {
        const match = String(line || '').match(/^ {0,3}(`{3,}|~{3,})/);
        if (!match) {
            return null;
        }
        return {
            char: match[1][0],
            length: match[1].length
        };
    }

    function explorerSourceLineRecords(content) {
        const source = String(content || '');
        const records = [];
        let lineNumber = 1;
        let index = 0;

        while (index <= source.length) {
            const newlineIndex = source.indexOf('\n', index);
            const lineEnd = newlineIndex === -1 ? source.length : newlineIndex;
            const rawLine = source.slice(index, lineEnd);
            records.push({
                number: lineNumber,
                start: index,
                text: rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine
            });
            if (newlineIndex === -1) {
                break;
            }
            lineNumber += 1;
            index = newlineIndex + 1;
        }

        return records;
    }

    function explorerMarkdownHeadingLevels(records) {
        const levels = new Map();
        let fence = null;
        records.forEach(record => {
            const marker = explorerMarkdownFenceMarker(record.text);
            if (marker) {
                if (fence && marker.char === fence.char && marker.length >= fence.length) {
                    fence = null;
                } else if (!fence) {
                    fence = marker;
                }
                return;
            }
            if (fence) {
                return;
            }
            const level = explorerMarkdownHeadingLevel(record.text);
            if (level) {
                levels.set(record.number, level);
            }
        });
        return levels;
    }

    function explorerSourceLineNumberHtml(record, headingLevel, collapsed) {
        if (!headingLevel) {
            return `<span class="explorer-source-line-number">${record.number}</span>`;
        }
        return `
            <button
                type="button"
                class="explorer-source-line-number"
                data-explorer-markdown-section="${record.number}"
                aria-expanded="${collapsed ? 'false' : 'true'}"
                title="${collapsed ? 'Expand Markdown section (Alt: expand all at this level)' : 'Collapse Markdown section (Alt: collapse all at this level)'}"
            >
                <span class="explorer-source-chevron" aria-hidden="true">${collapsed ? '▸' : '▾'}</span>
                <span>${record.number}</span>
            </button>
        `;
    }

    function renderExplorerSourceLines(content, language, searchRanges = [], collapsedLines = new Set()) {
        const normalizedLanguage = normalizeExplorerLanguage(language);
        const records = explorerSourceLineRecords(content);
        const languageClass = explorerLanguageClass(language);
        const codeClass = languageClass ? ` language-${languageClass}` : '';
        const markdownHeadings = normalizedLanguage === 'markdown'
            ? explorerMarkdownHeadingLevels(records)
            : new Map();
        const allowMarkdownCollapse = normalizedLanguage === 'markdown' && !searchRanges.length;
        const rows = [];
        let hiddenUntilHeadingLevel = 0;

        records.forEach(record => {
            const headingLevel = markdownHeadings.get(record.number) || 0;
            if (allowMarkdownCollapse && hiddenUntilHeadingLevel) {
                if (!headingLevel || headingLevel > hiddenUntilHeadingLevel) {
                    return;
                }
                hiddenUntilHeadingLevel = 0;
            }

            const collapsed = allowMarkdownCollapse && headingLevel && collapsedLines.has(record.number);
            const lineHtml = highlightExplorerCode(record.text, language, searchRanges, record.start);
            // Heading-only Markdown tokeniser (OD-8): the fence-aware heading map
            // already computed for section collapse doubles as the highlighter,
            // so heading lines get a distinct token colour without a full grammar.
            const contentHtml = headingLevel
                ? `<span class="explorer-md-source-heading explorer-md-source-heading-${headingLevel}">${lineHtml}</span>`
                : lineHtml;
            rows.push(`
                <div class="explorer-source-line">
                    ${explorerSourceLineNumberHtml(record, headingLevel, collapsed)}
                    <code class="explorer-source-line-code${codeClass}">${contentHtml || '&nbsp;'}</code>
                </div>
            `);

            if (collapsed) {
                hiddenUntilHeadingLevel = headingLevel;
            }
        });

        return `<div class="explorer-source-lines">${rows.join('')}</div>`;
    }

    function toggleExplorerMarkdownSection(index, lineNumber, { allSameLevel = false } = {}) {
        const pane = terminals[index];
        if (!pane || normalizeExplorerLanguage(pane._explorerFileLanguage || '') !== 'markdown') {
            return;
        }
        const collapsedLines = ensureExplorerMarkdownCollapsedLines(pane);
        if (allSameLevel) {
            // Alt+click fans the toggle out to every heading sharing the clicked
            // heading's level. The new state mirrors the clicked heading: if it
            // was expanded we collapse the whole level, and vice versa.
            const levels = explorerMarkdownHeadingLevels(
                explorerSourceLineRecords(pane._explorerFileContent || '')
            );
            const targetLevel = levels.get(lineNumber);
            if (!targetLevel) {
                return;
            }
            const collapse = !collapsedLines.has(lineNumber);
            levels.forEach((level, number) => {
                if (level !== targetLevel) {
                    return;
                }
                if (collapse) {
                    collapsedLines.add(number);
                } else {
                    collapsedLines.delete(number);
                }
            });
        } else if (collapsedLines.has(lineNumber)) {
            collapsedLines.delete(lineNumber);
        } else {
            collapsedLines.add(lineNumber);
        }
        const tab = explorerActiveTab(pane);
        tab.collapsedIdentity = explorerFileContentIdentity(
            pane._explorerFilePath,
            pane._explorerFileContent,
            pane._explorerDiffCommit,
            pane._explorerDiffMode
        );
        persistExplorerTabsToSession(index);
        const state = ensureExplorerSearchState(pane, 'file');
        if (state.query && activeExplorerFileView(index) === 'source') {
            applyExplorerSearch(index);
        } else {
            renderExplorerSource(index);
        }
    }

    function wireExplorerMarkdownSectionControls(index) {
        const code = document.getElementById(`explorer-code-${index}`);
        if (!code) {
            return;
        }
        code.querySelectorAll('[data-explorer-markdown-section]').forEach(button => {
            if (button.dataset.bound) {
                return;
            }
            button.dataset.bound = 'true';
            button.addEventListener('click', (event) => {
                toggleExplorerMarkdownSection(index, Number(button.dataset.explorerMarkdownSection || 0), {
                    allSameLevel: event.altKey
                });
            });
        });
    }

    function renderExplorerSource(index, searchRanges = []) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-code-${index}`);
        if (!pane || !code) {
            return;
        }

        const language = pane._explorerFilePlain ? '' : (pane._explorerFileLanguage || '');
        code.innerHTML = renderExplorerSourceLines(
            pane._explorerFileContent || '',
            language,
            searchRanges,
            ensureExplorerMarkdownCollapsedLines(pane)
        );
        wireExplorerMarkdownSectionControls(index);
    }

    function explorerPreviewBlockLanguage(code) {
        const match = String(code.className || '').match(/(?:^|\s)language-([\w+#.-]+)/i);
        return match ? normalizeExplorerLanguage(match[1]) : '';
    }

    function highlightExplorerPreviewCode(root) {
        if (!root) {
            return;
        }
        root.querySelectorAll('pre > code').forEach(code => {
            const language = explorerPreviewBlockLanguage(code);
            if (!language) {
                return;
            }
            if (language === 'mermaid') {
                return;
            }
            const pre = code.parentElement;
            pre.classList.add('explorer-preview-code');
            pre.dataset.lang = language.toUpperCase();
            // Plain text/markdown blocks stay unstyled; string/number rules would mislead there.
            if (language === 'text' || language === 'markdown') {
                return;
            }
            code.innerHTML = highlightExplorerCode(code.textContent, language);
        });
    }

    let explorerMermaidRenderId = 0;

    async function renderExplorerMermaid(preview) {
        if (!preview || !window.mermaid) {
            return;
        }
        const blocks = Array.from(preview.querySelectorAll('pre > code.language-mermaid'));
        if (!blocks.length) {
            return;
        }
        window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            theme: currentResolvedTheme() === 'dark' ? 'dark' : 'default',
            suppressErrorRendering: true
        });
        for (const code of blocks) {
            const source = code.textContent || '';
            const pre = code.parentElement;
            const diagram = document.createElement('div');
            diagram.className = 'explorer-mermaid';
            pre.replaceWith(diagram);
            try {
                explorerMermaidRenderId += 1;
                const rendered = await window.mermaid.render(
                    `explorer-mermaid-${explorerMermaidRenderId}`,
                    source
                );
                if (!preview.contains(diagram)) {
                    continue;
                }
                diagram.innerHTML = rendered.svg;
                rendered.bindFunctions?.(diagram);
            } catch (error) {
                diagram.classList.add('explorer-mermaid-error');
                const message = String(error?.message || 'Invalid diagram').split('\n')[0];
                diagram.textContent = `Mermaid diagram error: ${message}`;
                continue;
            }
            /* Ctrl+scroll zooms the rendered diagram (notes 3); double-click
               resets it. Bound on the diagram box so the page-zoom default is
               suppressed only while the pointer is over the diagram. */
            enableExplorerWheelZoom(diagram, diagram.querySelector('svg'));
        }
    }

    /* Ctrl+scroll zoom for a scrollable view (container) around a scalable
       target (an <img> or mermaid <svg>). Double-click restores 1×; while
       zoomed the surface shows a hand cursor and can be dragged to pan.

       Zoom resizes the target's LAYOUT box (explicit px width/height) rather
       than applying a CSS transform. A transform only overflows *visually*, so
       the scroll container never gained a real scroll region and the top/left
       corners stayed unreachable no matter the alignment. A real size change
       gives overflow:auto a true region, so scrollbars and drag-pan reach every
       edge (notes 3 redo). */
    function enableExplorerWheelZoom(container, target) {
        if (!container || !target || container._wheelZoomBound) {
            return;
        }
        container._wheelZoomBound = true;
        let scale = 1;
        let baseW = 0;
        let baseH = 0;

        const applyZoom = () => {
            if (scale <= 1) {
                target.style.width = '';
                target.style.height = '';
                target.style.maxWidth = '';
                target.style.maxHeight = '';
                container.classList.remove('explorer-zoomable');
                return;
            }
            target.style.maxWidth = 'none';
            target.style.maxHeight = 'none';
            target.style.width = `${Math.round(baseW * scale)}px`;
            target.style.height = `${Math.round(baseH * scale)}px`;
            container.classList.add('explorer-zoomable');
        };

        const zoomBy = factor => {
            // Capture the fitted (scale-1) size the first time we grow, so the
            // scale stays relative to what the user actually sees on screen.
            if (scale === 1) {
                const rect = target.getBoundingClientRect();
                baseW = rect.width;
                baseH = rect.height;
            }
            if (!baseW || !baseH) {
                return;
            }
            scale = Math.min(
                EXPLORER_WHEEL_ZOOM_MAX,
                Math.max(1, scale * factor)
            );
            applyZoom();
        };

        container.addEventListener('wheel', event => {
            if (!event.ctrlKey) {
                return;
            }
            event.preventDefault();
            zoomBy(event.deltaY < 0 ? EXPLORER_WHEEL_ZOOM_STEP : 1 / EXPLORER_WHEEL_ZOOM_STEP);
        }, { passive: false });
        container.addEventListener('dblclick', event => {
            if (scale === 1) {
                return;
            }
            event.preventDefault();
            scale = 1;
            applyZoom();
        });

        let dragging = false;
        let startX = 0;
        let startY = 0;
        let startLeft = 0;
        let startTop = 0;
        container.addEventListener('pointerdown', event => {
            if (scale === 1 || event.button !== 0) {
                return;
            }
            dragging = true;
            startX = event.clientX;
            startY = event.clientY;
            startLeft = container.scrollLeft;
            startTop = container.scrollTop;
            container.classList.add('explorer-grabbing');
            container.setPointerCapture?.(event.pointerId);
            event.preventDefault();
        });
        container.addEventListener('pointermove', event => {
            if (!dragging) {
                return;
            }
            container.scrollLeft = startLeft - (event.clientX - startX);
            container.scrollTop = startTop - (event.clientY - startY);
        });
        const endDrag = event => {
            if (!dragging) {
                return;
            }
            dragging = false;
            container.classList.remove('explorer-grabbing');
            container.releasePointerCapture?.(event.pointerId);
        };
        container.addEventListener('pointerup', endDrag);
        container.addEventListener('pointercancel', endDrag);
    }

    function restoreExplorerPreview(index) {
        const pane = terminals[index];
        const preview = document.getElementById(`explorer-preview-${index}`);
        if (pane && preview) {
            preview.innerHTML = pane._explorerPreviewHtml || '';
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            renderExplorerMermaid(preview);
        }
        return preview;
    }

    function markExplorerSearchInElement(root, query, activeIndex = 0, maxMatches = EXPLORER_SEARCH_MAX_MATCHES) {
        if (!root || !query) {
            return [];
        }

        const textNodes = [];
        const walker = document.createTreeWalker(
            root,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode(node) {
                    if (!node.nodeValue) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    if (node.parentElement?.closest('mark.explorer-search-match')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
            }
        );
        while (walker.nextNode()) {
            textNodes.push(walker.currentNode);
        }

        const marks = [];
        const normalizedQuery = query.toLowerCase();
        let capped = false;
        textNodes.forEach(node => {
            if (marks.length >= maxMatches) {
                capped = true;
                return;
            }
            const value = node.nodeValue || '';
            const normalizedValue = value.toLowerCase();
            const localMatches = [];
            let cursor = 0;
            while (cursor < normalizedValue.length && marks.length + localMatches.length < maxMatches) {
                const matchIndex = normalizedValue.indexOf(normalizedQuery, cursor);
                if (matchIndex === -1) {
                    break;
                }
                localMatches.push({
                    start: matchIndex,
                    end: matchIndex + query.length
                });
                cursor = matchIndex + Math.max(query.length, 1);
            }
            capped = capped || marks.length + localMatches.length >= maxMatches;
            if (!localMatches.length) {
                return;
            }

            const fragment = document.createDocumentFragment();
            let localCursor = 0;
            localMatches.forEach(match => {
                if (match.start > localCursor) {
                    fragment.appendChild(document.createTextNode(value.slice(localCursor, match.start)));
                }
                const mark = document.createElement('mark');
                mark.className = marks.length === activeIndex
                    ? 'explorer-search-match active'
                    : 'explorer-search-match';
                mark.textContent = value.slice(match.start, match.end);
                fragment.appendChild(mark);
                marks.push(mark);
                localCursor = match.end;
            });
            if (localCursor < value.length) {
                fragment.appendChild(document.createTextNode(value.slice(localCursor)));
            }
            node.replaceWith(fragment);
        });

        marks.capped = capped;
        return marks;
    }

    function renderExplorerDirectorySearchControls(index) {
        const container = document.getElementById(`explorer-directory-search-${index}`);
        if (!container) {
            return;
        }

        container.classList.add('active');
        container.innerHTML = `
            <input
                type="search"
                class="explorer-search-input"
                data-explorer-search-input="${index}"
                placeholder="Find file"
                autocomplete="off"
                spellcheck="false"
                aria-label="Find files and folders"
            >
            <span class="explorer-search-count" data-explorer-search-count="${index}"></span>
            <button type="button" class="explorer-search-btn" data-explorer-search-prev="${index}" title="Previous match" aria-label="Previous match">↑</button>
            <button type="button" class="explorer-search-btn" data-explorer-search-next="${index}" title="Next match" aria-label="Next match">↓</button>
            <button type="button" class="explorer-search-btn" data-explorer-search-clear="${index}" title="Clear search" aria-label="Clear search">×</button>
        `;
    }

    function clearExplorerDirectorySearchControls(index) {
        const container = document.getElementById(`explorer-directory-search-${index}`);
        if (!container) {
            return;
        }
        container.classList.remove('active');
        container.innerHTML = '';
    }

    function resetExplorerDirectorySearch(pane) {
        const state = ensureExplorerSearchState(pane, 'directory');
        state.query = '';
        state.activeIndex = 0;
        state.matchCount = 0;
        state.matchCapped = false;
    }

    function explorerDirectoryRowHtml(entry, query = '', active = false) {
        const isDirectory = entry.type === 'directory';
        const isDeleted = Boolean(entry.deleted);
        const size = isDirectory ? 'Folder' : (isDeleted ? 'Deleted' : formatExplorerSize(entry.size));
        const modified = formatExplorerDate(entry.modified);
        const name = entry.name || '';
        const normalizedName = name.toLowerCase();
        const normalizedQuery = String(query || '').toLowerCase();
        const matchIndex = normalizedQuery ? normalizedName.indexOf(normalizedQuery) : -1;
        const nameRanges = matchIndex === -1 ? [] : [{
            start: matchIndex,
            end: matchIndex + String(query).length,
            active
        }];

        return `
            <button
                type="button"
                class="explorer-row ${isDirectory ? 'directory' : 'file'}${isDeleted ? ' deleted' : ''}"
                data-explorer-path="${escHtml(entry.path || '')}"
                ${isDeleted ? 'disabled' : ''}
            >
                ${isDirectory ? EXPLORER_FOLDER_ICON : explorerFileTypeIconHtml(name || entry.path)}
                ${explorerGitBadgeHtml(entry.git)}
                <span class="explorer-name">${explorerMarkedEscHtml(name, 0, nameRanges)}</span>
                <span class="explorer-meta">${escHtml(size)}</span>
                <span class="explorer-meta">${escHtml(modified)}</span>
            </button>
        `;
    }

    function wireExplorerDirectoryRows(index) {
        const viewer = document.getElementById(`explorer-viewer-${index}`);
        if (!viewer) {
            return;
        }

        viewer.querySelectorAll('.explorer-row.directory').forEach(button => {
            button.addEventListener('click', () => {
                loadExplorerPane(index, button.dataset.explorerPath || '');
            });
        });
        viewer.querySelectorAll('.explorer-row.file').forEach(button => {
            button.addEventListener('click', () => {
                openExplorerFile(index, button.dataset.explorerPath || '');
            });
        });
    }

    function renderExplorerDirectoryRows(index) {
        const pane = terminals[index];
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !viewer) {
            return;
        }

        const entries = Array.isArray(pane._explorerEntries) ? pane._explorerEntries : [];
        const state = ensureExplorerSearchState(pane, 'directory');
        const query = state.query || '';
        const normalizedQuery = String(query).toLowerCase();
        let visibleEntries = entries;
        if (normalizedQuery) {
            visibleEntries = entries.filter(entry => String(entry.name || '').toLowerCase().includes(normalizedQuery));
        }

        const matchCount = normalizedQuery ? visibleEntries.length : 0;
        state.matchCount = matchCount;
        state.activeIndex = matchCount ? Math.min(Number(state.activeIndex || 0), matchCount - 1) : 0;

        if (!entries.length && !query) {
            viewer.innerHTML = '<div class="explorer-message">Directory is empty.</div>';
        } else if (!visibleEntries.length) {
            viewer.innerHTML = `<div class="explorer-message">No files or folders match "${escHtml(query)}".</div>`;
        } else {
            viewer.innerHTML = visibleEntries
                .map((entry, entryIndex) => explorerDirectoryRowHtml(entry, query, Boolean(normalizedQuery) && entryIndex === state.activeIndex))
                .join('');
            wireExplorerDirectoryRows(index);
        }

        updateExplorerSearchControls(index, query, state.activeIndex || 0, matchCount);
        if (normalizedQuery && matchCount) {
            scrollExplorerSearchMatch(index);
        }
    }

    function updateExplorerSearchControls(index, query, activeIndex, matchCount, capped = false) {
        const input = document.querySelector(`[data-explorer-search-input="${index}"]`);
        const count = document.querySelector(`[data-explorer-search-count="${index}"]`);
        const buttons = document.querySelectorAll(
            `[data-explorer-search-prev="${index}"], [data-explorer-search-next="${index}"]`
        );
        if (input && input.value !== query) {
            input.value = query;
        }
        if (count) {
            count.textContent = query ? `${matchCount ? activeIndex + 1 : 0}/${matchCount}${capped ? '+' : ''}` : '';
            count.title = capped ? `Showing first ${matchCount} matches` : '';
        }
        buttons.forEach(button => {
            button.disabled = matchCount === 0;
        });
    }

    function scrollExplorerSearchMatch(index) {
        const active = document
            .getElementById(`explorer-list-${index}`)
            ?.querySelector('.explorer-search-match.active');
        if (!active) {
            return;
        }
        requestAnimationFrame(() => {
            active.scrollIntoView({ block: 'center', inline: 'nearest' });
        });
    }

    function cancelExplorerSearch(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        if (pane._explorerSearchTimer) {
            window.clearTimeout(pane._explorerSearchTimer);
            pane._explorerSearchTimer = null;
        }
        if (pane._explorerSearchToken) {
            pane._explorerSearchToken.cancelled = true;
            pane._explorerSearchToken = null;
        }
    }

    function scheduleExplorerSearch(index, { resetActive = false, delay = EXPLORER_SEARCH_DEBOUNCE_MS } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return;
        }
        if (pane._explorerMode === 'directory') {
            applyExplorerSearch(index, { resetActive });
            return;
        }

        cancelExplorerSearch(index);
        pane._explorerSearchTimer = window.setTimeout(() => {
            pane._explorerSearchTimer = null;
            applyExplorerSearch(index, { resetActive });
        }, delay);
    }

    async function applyExplorerSearch(index, { resetActive = false } = {}) {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return;
        }

        const state = ensureExplorerSearchState(pane);
        if (resetActive) {
            state.activeIndex = 0;
        }

        if (pane._explorerMode === 'directory') {
            renderExplorerDirectoryRows(index);
            return;
        }

        const query = state.query || '';
        const view = activeExplorerFileView(index);
        let matchCount = 0;
        let capped = false;
        if (query && view === 'source') {
            const cachedRanges = state.resultQuery === query && Array.isArray(state.ranges)
                ? state.ranges
                : null;
            const ranges = cachedRanges || [];
            if (!cachedRanges) {
                cancelExplorerSearch(index);
                const token = { cancelled: false };
                pane._explorerSearchToken = token;
                updateExplorerSearchControls(index, query, 0, 0);
                const result = await explorerFindRangesAsync(pane._explorerFileContent || '', query, token);
                if (token.cancelled || pane._explorerSearchToken !== token) {
                    return;
                }
                pane._explorerSearchToken = null;
                ranges.splice(0, ranges.length, ...result.ranges);
                ranges.capped = result.capped;
                state.ranges = ranges;
                state.resultQuery = query;
            }
            matchCount = ranges.length;
            capped = Boolean(ranges.capped);
            state.activeIndex = matchCount ? Math.min(state.activeIndex || 0, matchCount - 1) : 0;
            renderExplorerSource(index, decorateExplorerSearchRanges(ranges, state.activeIndex));
        } else if (query && view === 'preview') {
            cancelExplorerSearch(index);
            renderExplorerSource(index);
            if (pane._explorerDiffLoaded) {
                renderExplorerDiff(index);
            }
            const preview = restoreExplorerPreview(index);
            if (!preview) {
                state.activeIndex = 0;
                state.matchCount = 0;
                state.matchCapped = false;
                updateExplorerSearchControls(index, query, 0, 0);
                return;
            }
            const previewMarks = markExplorerSearchInElement(preview, query, state.activeIndex || 0);
            matchCount = previewMarks.length;
            capped = Boolean(previewMarks.capped);
            state.activeIndex = matchCount ? Math.min(state.activeIndex || 0, matchCount - 1) : 0;
            if (matchCount && !previewMarks[state.activeIndex]?.classList.contains('active')) {
                previewMarks.forEach((mark, markIndex) => {
                    mark.classList.toggle('active', markIndex === state.activeIndex);
                });
            }
        } else if (query && view === 'diff') {
            cancelExplorerSearch(index);
            renderExplorerSource(index);
            restoreExplorerPreview(index);
            if (pane._explorerDiffLoaded) {
                renderExplorerDiff(index);
            }
            const diff = document.getElementById(`explorer-diff-code-${index}`);
            if (!diff) {
                state.activeIndex = 0;
                state.matchCount = 0;
                state.matchCapped = false;
                updateExplorerSearchControls(index, query, 0, 0);
                return;
            }
            const diffMarks = markExplorerSearchInElement(diff, query, state.activeIndex || 0);
            matchCount = diffMarks.length;
            capped = Boolean(diffMarks.capped);
            state.activeIndex = matchCount ? Math.min(state.activeIndex || 0, matchCount - 1) : 0;
            if (matchCount && !diffMarks[state.activeIndex]?.classList.contains('active')) {
                diffMarks.forEach((mark, markIndex) => {
                    mark.classList.toggle('active', markIndex === state.activeIndex);
                });
            }
        } else {
            cancelExplorerSearch(index);
            state.ranges = [];
            state.resultQuery = '';
            renderExplorerSource(index);
            restoreExplorerPreview(index);
            if (pane._explorerDiffLoaded) {
                renderExplorerDiff(index);
            }
            state.activeIndex = 0;
        }

        state.matchCount = matchCount;
        state.matchCapped = capped;
        updateExplorerSearchControls(index, query, state.activeIndex || 0, matchCount, capped);
        if (query && matchCount) {
            scrollExplorerSearchMatch(index);
        }
    }

    function stepExplorerSearch(index, delta) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerSearchState(pane);
        const matchCount = Number(state.matchCount || 0);
        if (!matchCount) {
            return;
        }
        state.activeIndex = (Number(state.activeIndex || 0) + delta + matchCount) % matchCount;
        applyExplorerSearch(index);
    }

    function clearExplorerSearch(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerSearchState(pane);
        state.query = '';
        state.activeIndex = 0;
        state.matchCount = 0;
        state.matchCapped = false;
        state.ranges = [];
        state.resultQuery = '';
        applyExplorerSearch(index);
        document.querySelector(`[data-explorer-search-input="${index}"]`)?.focus();
    }

    function focusExplorerSearch(index, seedQuery = '') {
        const pane = terminals[index];
        if (!pane || !isExplorerSearchablePane(pane)) {
            return false;
        }
        const input = document.querySelector(`[data-explorer-search-input="${index}"]`);
        if (!input) {
            return false;
        }
        /* Seeding the query with the current editor selection mirrors the
           copy → find → paste sequence (notes 1): highlight text, hit Ctrl+F,
           and it immediately looks that text up instead of reopening the last
           search. */
        if (seedQuery) {
            const state = ensureExplorerSearchState(pane);
            state.query = seedQuery;
            state.activeIndex = 0;
            state.ranges = [];
            state.resultQuery = '';
            state.matchCapped = false;
            input.value = seedQuery;
            scheduleExplorerSearch(index, { resetActive: true, delay: 0 });
        }
        input.focus();
        input.select();
        return true;
    }

    function wireExplorerSearchControls(index) {
        const pane = terminals[index];
        const input = document.querySelector(`[data-explorer-search-input="${index}"]`);
        if (!pane || !input || input.dataset.bound) {
            return;
        }

        input.dataset.bound = 'true';
        const state = ensureExplorerSearchState(pane);
        input.value = state.query || '';
        input.addEventListener('input', () => {
            const nextState = ensureExplorerSearchState(pane);
            nextState.query = input.value;
            nextState.activeIndex = 0;
            nextState.ranges = [];
            nextState.resultQuery = '';
            nextState.matchCapped = false;
            scheduleExplorerSearch(index, { resetActive: true });
        });
        input.addEventListener('keydown', event => {
            if (event.key === 'Enter') {
                event.preventDefault();
                stepExplorerSearch(index, event.shiftKey ? -1 : 1);
            } else if (event.key === 'Escape') {
                event.preventDefault();
                clearExplorerSearch(index);
            }
        });

        document.querySelector(`[data-explorer-search-prev="${index}"]`)?.addEventListener('click', () => {
            stepExplorerSearch(index, -1);
        });
        document.querySelector(`[data-explorer-search-next="${index}"]`)?.addEventListener('click', () => {
            stepExplorerSearch(index, 1);
        });
        document.querySelector(`[data-explorer-search-clear="${index}"]`)?.addEventListener('click', () => {
            clearExplorerSearch(index);
        });
    }

    function explorerPanelScrollTarget(panel) {
        if (!panel) {
            return null;
        }
        // The diff panel wrapper (.explorer-diff-split) is overflow:hidden; the
        // element that actually scrolls is the inner .explorer-diff-content.
        if (panel.dataset.explorerFilePanel === 'diff') {
            return panel.querySelector('.explorer-diff-content') || panel;
        }
        return panel;
    }

    function captureScrollMetrics(el) {
        if (!el) {
            return null;
        }
        const maxScrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
        const maxScrollLeft = Math.max(0, el.scrollWidth - el.clientWidth);
        return {
            scrollLeft: el.scrollLeft,
            scrollLeftRatio: maxScrollLeft > 0 ? el.scrollLeft / maxScrollLeft : 0,
            scrollTop: el.scrollTop,
            scrollTopRatio: maxScrollTop > 0 ? el.scrollTop / maxScrollTop : 0,
            wasAtBottom: maxScrollTop > 0 && el.scrollTop >= maxScrollTop - 2
        };
    }

    function applyScrollMetrics(el, metrics) {
        if (!el || !metrics) {
            return;
        }
        const maxScrollTop = Math.max(0, el.scrollHeight - el.clientHeight);
        const maxScrollLeft = Math.max(0, el.scrollWidth - el.clientWidth);
        el.scrollLeft = Math.min(
            maxScrollLeft,
            maxScrollLeft > 0
                ? Math.round(maxScrollLeft * (metrics.scrollLeftRatio || 0))
                : (metrics.scrollLeft || 0)
        );
        el.scrollTop = metrics.wasAtBottom
            ? maxScrollTop
            : Math.min(
                maxScrollTop,
                maxScrollTop > 0
                    ? Math.round(maxScrollTop * (metrics.scrollTopRatio || 0))
                    : (metrics.scrollTop || 0)
            );
    }

    function captureExplorerFileScroll(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
        }

        const activeButton = list.querySelector('[data-explorer-file-view][aria-selected="true"]');
        const state = {
            activeView: activeButton?.dataset.explorerFileView || 'source',
            listScrollLeft: list.scrollLeft,
            listScrollTop: list.scrollTop,
            panels: {},
            // File tree / Git sidebar panels sit outside the list and are their own
            // overflow:auto scrollers, so capture them too (they reset on reattach).
            sidebar: {
                tree: captureScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`)),
                git: captureScrollMetrics(document.getElementById(`explorer-git-panel-${index}`))
            }
        };
        list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
            const scrollEl = explorerPanelScrollTarget(panel);
            if (!scrollEl) {
                return;
            }
            const maxScrollTop = Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight);
            const maxScrollLeft = Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth);
            state.panels[panel.dataset.explorerFilePanel || 'source'] = {
                scrollLeft: scrollEl.scrollLeft,
                scrollLeftRatio: maxScrollLeft > 0 ? scrollEl.scrollLeft / maxScrollLeft : 0,
                scrollTop: scrollEl.scrollTop,
                scrollTopRatio: maxScrollTop > 0 ? scrollEl.scrollTop / maxScrollTop : 0,
                wasAtBottom: maxScrollTop > 0 && scrollEl.scrollTop >= maxScrollTop - 2
            };
        });
        return state;
    }

    function restoreExplorerFileScroll(index, state) {
        if (!state) {
            return;
        }

        /* Directory listings have no file-view panels; switching modes there
           would clobber stale diff state for no visual effect. */
        const listEl = document.getElementById(`explorer-list-${index}`);
        if (listEl && listEl.querySelector('[data-explorer-file-panel]')) {
            setExplorerFileView(index, state.activeView || 'source');
        }

        const applyScroll = () => {
            const list = document.getElementById(`explorer-list-${index}`);
            if (!list) {
                return;
            }
            list.scrollLeft = state.listScrollLeft || 0;
            list.scrollTop = state.listScrollTop || 0;
            applyScrollMetrics(document.getElementById(`explorer-tree-panel-${index}`), state.sidebar?.tree);
            applyScrollMetrics(document.getElementById(`explorer-git-panel-${index}`), state.sidebar?.git);
            list.querySelectorAll('[data-explorer-file-panel]').forEach(panel => {
                const panelState = state.panels?.[panel.dataset.explorerFilePanel || 'source'];
                const scrollEl = explorerPanelScrollTarget(panel);
                if (panelState && scrollEl) {
                    const maxScrollTop = Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight);
                    const maxScrollLeft = Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth);
                    scrollEl.scrollLeft = Math.min(
                        maxScrollLeft,
                        maxScrollLeft > 0
                            ? Math.round(maxScrollLeft * (panelState.scrollLeftRatio || 0))
                            : (panelState.scrollLeft || 0)
                    );
                    scrollEl.scrollTop = panelState.wasAtBottom
                        ? maxScrollTop
                        : Math.min(
                            maxScrollTop,
                            maxScrollTop > 0
                                ? Math.round(maxScrollTop * (panelState.scrollTopRatio || 0))
                                : (panelState.scrollTop || 0)
                        );
                }
            });
        };

        applyScroll();
        requestAnimationFrame(() => {
            applyScroll();
            requestAnimationFrame(applyScroll);
        });
        window.setTimeout(applyScroll, 80);
    }

    /* ── Per-tab view mode + scroll state (2.e) ──
       Each tab record may carry a `view` snapshot: { mode, identity, scroll }.
       The snapshot is captured when leaving a tab and restored when the tab is
       shown again — but only while the content identity still matches (OD-4:
       scroll is stored as fractions of scroll height, clamped on restore, and
       skipped entirely once the content changed). */

    /* Cheap stable string hash (djb2) for content-identity comparison. */
    function explorerHashText(text) {
        const value = String(text == null ? '' : text);
        let hash = 5381;
        for (let i = 0; i < value.length; i += 1) {
            hash = ((hash << 5) + hash + value.charCodeAt(i)) | 0;
        }
        return (hash >>> 0).toString(36);
    }

    /* Identity of a rendered file view: same path, same content, same diff
       target. Any change (tail-updated log, re-fetch with new bytes) produces
       a different identity, which suppresses scroll restore. */
    function explorerFileContentIdentity(path, content, diffCommit, diffMode) {
        return explorerHashText(
            [path || '', content || '', diffCommit || '', diffMode || ''].join('\u0000')
        );
    }

    function explorerDirectoryContentIdentity(path, entries) {
        return explorerHashText(
            `${path || ''}\u0000${Array.isArray(entries) ? entries.length : 0}`
        );
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
        const scroll = captureExplorerFileScroll(index);
        if (!scroll) {
            return;
        }
        tab.view = {
            mode: isFile ? (scroll.activeView || 'source') : '',
            identity: isFile
                ? explorerFileContentIdentity(
                    pane._explorerFilePath,
                    pane._explorerFileContent,
                    pane._explorerDiffCommit,
                    pane._explorerDiffMode
                )
                : explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries),
            scroll
        };
    }

    /* Return the tab's stored view snapshot when its content identity still
       matches what is about to render, otherwise null (OD-4 skip rule). */
    function explorerMatchingTabView(tab, identity) {
        const view = tab && tab.view;
        if (!view || !view.identity || !view.scroll || view.identity !== identity) {
            return null;
        }
        return view;
    }

    /* Diff content loads asynchronously, after restoreExplorerFileScroll has
       already run; re-apply a stashed diff-panel scroll once it arrives. */
    function applyExplorerPendingDiffScroll(index) {
        const pane = terminals[index];
        const metrics = pane ? pane._explorerPendingDiffScroll : null;
        if (!pane || !metrics) {
            return;
        }
        pane._explorerPendingDiffScroll = null;
        const panel = document.getElementById(`explorer-diff-panel-${index}`);
        applyScrollMetrics(explorerPanelScrollTarget(panel), metrics);
    }

    const EXPLORER_FOLDER_ICON = `
        <span class="explorer-icon folder" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
                <path fill="currentColor" d="M3 6.75A2.75 2.75 0 0 1 5.75 4h4.02c.73 0 1.43.29 1.94.8l1.2 1.2h5.34A2.75 2.75 0 0 1 21 8.75v8.5A2.75 2.75 0 0 1 18.25 20H5.75A2.75 2.75 0 0 1 3 17.25V6.75Zm2.75-1.25c-.69 0-1.25.56-1.25 1.25V8h15v-.25c0-.69-.56-1.25-1.25-1.25h-5.65a.75.75 0 0 1-.53-.22l-1.42-1.42a1.25 1.25 0 0 0-.88-.36H5.75ZM4.5 9.5v7.75c0 .69.56 1.25 1.25 1.25h12.5c.69 0 1.25-.56 1.25-1.25V9.5h-15Z"/>
            </svg>
        </span>
    `;

    const EXPLORER_TREE_TOGGLE_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="5" rx="1.5"/>
            <rect x="14" y="9.5" width="7" height="5" rx="1.5"/>
            <rect x="14" y="16.5" width="7" height="5" rx="1.5"/>
            <path d="M6.5 8v11"/>
            <path d="M6.5 12h7.5"/>
            <path d="M6.5 19h7.5"/>
        </svg>
    `;

    const EXPLORER_GIT_TOGGLE_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5.5" cy="5" r="2.25"/>
            <circle cx="5.5" cy="19" r="2.25"/>
            <circle cx="18.5" cy="5" r="2.25"/>
            <path d="M5.5 7.25v9.5"/>
            <path d="M18.5 7.25v1.5a4 4 0 0 1-4 4h-5a4 4 0 0 0-4 4"/>
        </svg>
    `;

    const EXPLORER_OS_OPEN_ICON = `
        <svg class="explorer-toggle-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
            <polyline points="15 3 21 3 21 9"/>
            <line x1="10" y1="14" x2="21" y2="3"/>
        </svg>
    `;

    const EXPLORER_GIT_REVERT_ICON = `
        <svg class="explorer-btn-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M5 5.5v4.5h4.5"/>
            <path d="M5.4 13.5a7 7 0 1 0 1.7-6.4L5 10"/>
        </svg>
    `;

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

    async function downloadExplorerFile(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || pane._explorerMode !== 'file') {
            return;
        }
        const path = pane._explorerFilePath || '';
        const fileName = pane._explorerFileName || 'download';
        const url = `/api/explorer/${encodeURIComponent(sessionId)}/download?path=${encodeURIComponent(path)}`;

        /* WebView2 silently ignores programmatic <a download> clicks, so in the
           native window route the save through the pywebview bridge (native
           Save dialog + server-side fetch). In the browser the anchor works. */
        if (isPywebviewAvailable() && window.pywebview.api.save_download) {
            try {
                const result = await window.pywebview.api.save_download(url, fileName);
                if (result?.ok) {
                    showTerminalToast(`Saved ${getDownloadBaseName(result.path) || fileName}`, 'success');
                } else if (!result?.cancelled) {
                    showTerminalToast(`Download failed: ${result?.error || 'unknown error'}`, 'error');
                }
            } catch (error) {
                showTerminalToast(`Download failed: ${error?.message || error}`, 'error');
            }
            return;
        }

        const link = document.createElement('a');
        link.href = url;
        link.download = fileName;
        document.body.appendChild(link);
        link.click();
        link.remove();
        showTerminalToast(`Downloading ${fileName}…`, 'success');
    }

    function getDownloadBaseName(fullPath) {
        return String(fullPath || '').split(/[\\/]/).pop() || '';
    }

    /* Open the host OS file manager at whatever path the explorer bar currently
       shows (the open file for a file tab, otherwise the listed directory).
       Fully isolated from the explorer's browsing state — it only asks the
       backend to launch the local file manager and never mutates panes. */
    async function revealExplorerInOs(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        const path = pane._explorerMode === 'file'
            ? (pane._explorerFilePath || '')
            : (pane._explorerPath || '');
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/reveal`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path }),
                }
            );
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                showTerminalToast(
                    `Could not open file manager: ${payload.error || response.statusText}`,
                    'error'
                );
                return;
            }
            showTerminalToast('Opening file location…', 'success');
        } catch (error) {
            showTerminalToast(`Could not open file manager: ${error?.message || error}`, 'error');
        }
    }

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

    /* Choose (and if needed create) the tab a file should load into. A `+`
       action or Markdown link pins a deduplicated tab; an explicit `tab`
       re-renders that tab; every other plain click (Files tree, Git sidebar)
       loads into the permanent Preview tab — pinned tabs are never hijacked,
       even when they already show the same path. */
    function explorerAssignOpenTab(pane, path, { pinned = false, tab = '' } = {}) {
        ensureExplorerTabState(pane);
        const key = explorerNormalizeTabPath(path);
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

        if (pinned && key) {
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
            pane._explorerActiveTabId = pinnedTab.id;
            return pinnedTab;
        }

        const preview = explorerPreviewTab(pane);
        preview.path = path;
        preview.name = name;
        pane._explorerActiveTabId = preview.id;
        return preview;
    }

    function explorerEnsureViewerShell(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
        }
        let viewer = document.getElementById(`explorer-viewer-${index}`);
        if (!viewer) {
            list.innerHTML =
                `<div class="explorer-tab-strip" id="explorer-tabs-${index}" role="tablist" aria-label="Open files"></div>`
                + `<div class="explorer-viewer" id="explorer-viewer-${index}"></div>`;
            viewer = document.getElementById(`explorer-viewer-${index}`);
        }
        return viewer;
    }

    function explorerViewerEl(index) {
        return explorerEnsureViewerShell(index);
    }

    /* ── Preview-header breadcrumb (2.d, OD-3) ──
       Replaces the removed Back button: the explorer bar's path label becomes
       a trail of clickable ancestor segments (root included), each browsing
       that directory in the Preview tab. The final segment — the shown
       directory or file itself — is inert. */
    function renderExplorerPathBreadcrumb(index, path, { root = '', fallbackText = '' } = {}) {
        const label = document.getElementById(`explorer-path-${index}`);
        if (!label) {
            return;
        }
        const segments = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        if (!segments.length && fallbackText) {
            label.textContent = fallbackText;
            label.title = root || '';
            return;
        }
        const rootName = String(root || '').replace(/\\/g, '/').split('/').filter(Boolean).pop() || '/';
        const crumbs = [];
        if (segments.length) {
            crumbs.push(`<button type="button" class="explorer-crumb" data-explorer-crumb="" title="${escHtml(root || '/')}">${escHtml(rootName)}</button>`);
        } else {
            crumbs.push(`<span class="explorer-crumb current" title="${escHtml(root || '/')}">${escHtml(rootName)}</span>`);
        }
        let current = '';
        segments.forEach((segment, position) => {
            current = current ? `${current}/${segment}` : segment;
            if (position === segments.length - 1) {
                crumbs.push(`<span class="explorer-crumb current" title="${escHtml(current)}">${escHtml(segment)}</span>`);
            } else {
                crumbs.push(`<button type="button" class="explorer-crumb" data-explorer-crumb="${escHtml(current)}" title="${escHtml(current)}">${escHtml(segment)}</button>`);
            }
        });
        label.innerHTML = crumbs.join('<span class="explorer-crumb-sep" aria-hidden="true">/</span>');
        label.title = root || '';
        label.querySelectorAll('button[data-explorer-crumb]').forEach(button => {
            button.addEventListener('click', () => {
                loadExplorerPane(index, button.dataset.explorerCrumb || '');
            });
        });
    }

    function renderExplorerTabStrip(index) {
        const pane = terminals[index];
        const strip = document.getElementById(`explorer-tabs-${index}`);
        if (!pane || !strip) {
            return;
        }
        const tabs = ensureExplorerTabState(pane);
        const activeId = pane._explorerActiveTabId;
        strip.innerHTML = tabs.map(tab => {
            const active = tab.id === activeId;
            const isPreview = tab.id === EXPLORER_PREVIEW_TAB_ID;
            const label = explorerTabLabel(tab);
            const icon = (!isPreview || tab.path) ? explorerFileTypeIconHtml(tab.path || label) : '';
            const closeButton = isPreview
                ? ''
                : `<button type="button" class="explorer-tab-close" data-explorer-tab-close="${escHtml(tab.id)}" title="Close tab" aria-label="Close ${escHtml(label)}">×</button>`;
            return `
                <div class="explorer-tab${active ? ' active' : ''}${isPreview ? ' preview' : ''}" role="tab" aria-selected="${active ? 'true' : 'false'}" data-explorer-tab="${escHtml(tab.id)}"${isPreview ? '' : ' draggable="true"'} title="${escHtml(tab.path || label)}">
                    <button type="button" class="explorer-tab-main" data-explorer-tab-open="${escHtml(tab.id)}">
                        ${icon}
                        <span class="explorer-tab-name">${escHtml(label)}</span>
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
       permanent Preview tab keeps the first slot and is not draggable), and
       double-clicking the Preview tab promotes its shown file to a pinned
       tab in the same view mode. */
    function wireExplorerTabStripInteractions(index, tabEl) {
        const id = tabEl.dataset.explorerTab || '';
        if (id === EXPLORER_PREVIEW_TAB_ID) {
            tabEl.querySelector('.explorer-tab-main')?.addEventListener('dblclick', () => {
                promoteExplorerPreviewTab(index);
            });
            return;
        }
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
       shown file becomes a pinned tab carrying the same view mode, scroll,
       and zoom. The already-rendered viewer DOM is handed to the pinned tab
       (nothing is re-fetched); an existing pinned tab for the path is just
       activated, never clobbered. */
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
            activateExplorerTab(index, existing.id);
            return;
        }
        // Fold the live mode + scroll into the Preview record, then copy the
        // full per-tab state onto the new pinned tab.
        explorerCaptureActiveTabView(index);
        const pinnedTab = explorerAssignOpenTab(pane, path, { pinned: true });
        if (preview.view) {
            pinnedTab.view = { ...preview.view };
        }
        if (preview.fontSize) {
            pinnedTab.fontSize = preview.fontSize;
        }
        if (preview.preferredMode) {
            pinnedTab.preferredMode = preview.preferredMode;
        }
        pane._explorerRenderedTabId = pinnedTab.id;
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
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
        preview.dirPath = '';
        pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
        pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
        pane._explorerMode = 'viewer';
        pane._explorerFilePath = '';
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
            openExplorerFile(index, tab.path, { tab: tab.id });
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
                explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)
            );
            if (restoredView) {
                restoreExplorerFileScroll(index, restoredView.scroll);
            }
            renderExplorerTabStrip(index);
            return;
        }
        if (tab.id === EXPLORER_PREVIEW_TAB_ID && tab.dirPath) {
            /* The viewer last rendered another tab, so the pane-global
               directory state no longer describes the Preview tab — re-browse
               the tab's own directory instead of falling through to empty. */
            loadExplorerPane(index, tab.dirPath);
            return;
        }
        renderExplorerViewerEmpty(index);
    }

    function activateExplorerTab(index, id) {
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
        // Capture the outgoing tab's mode + scroll while its DOM is intact.
        explorerCaptureActiveTabView(index);
        pane._explorerActiveTabId = tab.id;
        renderExplorerActiveTab(index);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
    }

    function closeExplorerTab(index, id) {
        const pane = terminals[index];
        if (!pane || id === EXPLORER_PREVIEW_TAB_ID) {
            return;
        }
        ensureExplorerTabState(pane);
        const position = pane._explorerTabs.findIndex(tab => tab.id === id);
        if (position === -1) {
            return;
        }
        const wasActive = pane._explorerActiveTabId === id;
        pane._explorerTabs.splice(position, 1);
        if (wasActive) {
            const fallback = pane._explorerTabs[Math.max(0, position - 1)] || explorerPreviewTab(pane);
            activateExplorerTab(index, fallback.id);
        } else {
            renderExplorerTabStrip(index);
            persistExplorerTabsToSession(index);
        }
    }

    /* One-shot per session id: re-apply the Markdown appearance a saved
       session or restart snapshot carries (ISSUE-2026-033). The set keeps a
       close-driven rebuild of the same session from clobbering an appearance
       the user changed since launch; setExplorerMarkdownAppearance validates
       the values and syncs the shared localStorage keys. */
    const appliedExplorerMdSessions = new Set();
    function applyExplorerSessionMarkdownAppearance(index) {
        const sessionId = sessionIds[index];
        const session = terminals[index]?._session || {};
        const preset = session.explorer_md_preset || '';
        const font = session.explorer_md_font || '';
        if (!sessionId || appliedExplorerMdSessions.has(sessionId) || (!preset && !font)) {
            return;
        }
        appliedExplorerMdSessions.add(sessionId);
        setExplorerMarkdownAppearance({ preset, font });
    }

    /* Entry point when an explorer pane first shows: empty read-only viewer with
       the Files tree opened for navigation, plus any persisted tabs restored. */
    function openExplorerViewer(index) {
        const pane = terminals[index];
        if (!pane) {
            return false;
        }
        ensureExplorerTabState(pane);
        pane._attached = true;
        document.getElementById(`ph-${index}`)?.remove();
        renderExplorerViewerEmpty(index);
        if (!pane._explorerTreeSidebarOpen && !pane._explorerGitSidebarOpen) {
            setExplorerTreeSidebarOpen(index, true);
        }
        applyExplorerSessionMarkdownAppearance(index);
        restoreExplorerPersistedTabs(index);
        return true;
    }

    /* ── Markdown preview link navigation (ISSUE-2026-016) ──
       Relative Markdown links resolve against the current file and open as a
       pinned tab; fragments scroll to the heading; external links open in an
       isolated window without navigating the session page away. */
    function explorerClassifyLink(href) {
        const trimmed = String(href == null ? '' : href).trim();
        if (!trimmed) {
            return { type: 'ignore' };
        }
        if (trimmed.startsWith('#')) {
            return { type: 'fragment', fragment: trimmed.slice(1) };
        }
        if (/^\/\//.test(trimmed)) {
            return { type: 'external', href: `https:${trimmed}` };
        }
        if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) {
            if (/^https?:/i.test(trimmed)) {
                return { type: 'external', href: trimmed };
            }
            if (/^mailto:/i.test(trimmed)) {
                return { type: 'mailto' };
            }
            return { type: 'unsupported' };
        }
        return { type: 'relative', href: trimmed };
    }

    /* Resolve a relative link against the source file, rejecting anything that
       escapes the Explorer root. Returns { path, fragment } or null. */
    function explorerResolveRelativePath(baseFilePath, href) {
        const hashIndex = href.indexOf('#');
        const fragment = hashIndex >= 0 ? href.slice(hashIndex + 1) : '';
        let rel = hashIndex >= 0 ? href.slice(0, hashIndex) : href;
        if (!rel) {
            return { path: explorerNormalizeTabPath(baseFilePath), fragment };
        }
        try {
            rel = decodeURIComponent(rel);
        } catch (_) {
            return null;
        }
        rel = rel.replace(/\\/g, '/');
        const absolute = rel.startsWith('/');
        const baseSegments = absolute
            ? []
            : String(baseFilePath || '').replace(/\\/g, '/').split('/').slice(0, -1);
        const segments = baseSegments.filter(Boolean);
        for (const segment of rel.split('/')) {
            if (segment === '' || segment === '.') {
                continue;
            }
            if (segment === '..') {
                if (!segments.length) {
                    return null;
                }
                segments.pop();
                continue;
            }
            if (segment.includes(':')) {
                return null;
            }
            segments.push(segment);
        }
        const path = segments.join('/');
        return path ? { path, fragment } : null;
    }

    function explorerHeadingSlug(text) {
        return String(text || '')
            .toLowerCase()
            .trim()
            .replace(/[^\w\s-]/g, '')
            .replace(/\s+/g, '-');
    }

    function explorerScrollPreviewToHeading(preview, fragment) {
        if (!preview || !fragment) {
            return;
        }
        let target = null;
        try {
            target = preview.querySelector(`#${CSS.escape(fragment)}`);
        } catch (_) {
            target = null;
        }
        if (!target) {
            const slug = explorerHeadingSlug(fragment);
            target = Array.from(preview.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                .find(heading => explorerHeadingSlug(heading.textContent) === slug) || null;
        }
        if (target) {
            target.scrollIntoView({ block: 'start' });
        }
    }

    function wireExplorerMarkdownLinks(index, preview) {
        if (!preview || preview.dataset.mdLinksBound) {
            return;
        }
        preview.dataset.mdLinksBound = 'true';
        preview.addEventListener('click', event => {
            const anchor = event.target.closest('a[href]');
            if (!anchor || !preview.contains(anchor)) {
                return;
            }
            const info = explorerClassifyLink(anchor.getAttribute('href') || '');
            if (info.type === 'fragment') {
                event.preventDefault();
                explorerScrollPreviewToHeading(preview, info.fragment);
                return;
            }
            if (info.type === 'external') {
                event.preventDefault();
                window.open(info.href, '_blank', 'noopener,noreferrer');
                return;
            }
            if (info.type === 'mailto') {
                return;
            }
            if (info.type !== 'relative') {
                event.preventDefault();
                return;
            }
            event.preventDefault();
            const pane = terminals[index];
            const resolved = explorerResolveRelativePath(pane?._explorerFilePath || '', info.href);
            if (!resolved || !resolved.path) {
                return;
            }
            Promise.resolve(openExplorerFile(index, resolved.path, { pinned: true })).then(opened => {
                if (opened && resolved.fragment) {
                    requestAnimationFrame(() => {
                        const nextPreview = document.getElementById(`explorer-preview-${index}`);
                        if (nextPreview) {
                            explorerScrollPreviewToHeading(nextPreview, resolved.fragment);
                        }
                    });
                }
            });
        });
    }

    /* ── Saved-session tab persistence (ISSUE-2026-015, per-tab views 2.f) ── */

    /* Reduce a tab's live view snapshot to the persisted shape (OD-5, amended
       per user feedback to include zoom): view mode, the primary panel's
       scroll as a fraction of scroll height (OD-4), the content-identity hash
       the restore-side skip rule compares, and the tab's editor font size
       (omitted at the default so unzoomed tabs persist nothing). */
    function explorerPersistableTabView(tab) {
        if (!tab) {
            return null;
        }
        const record = {};
        const view = tab.view;
        if (view && view.mode && view.identity) {
            const panel = view.scroll && view.scroll.panels ? view.scroll.panels[view.mode] : null;
            record.mode = view.mode;
            record.scroll = panel
                ? (panel.wasAtBottom ? 1 : Math.max(0, Math.min(1, panel.scrollTopRatio || 0)))
                : 0;
            record.identity = view.identity;
        }
        const fontSize = tab.fontSize ? clampExplorerEditorFontSize(tab.fontSize) : 0;
        if (fontSize && fontSize !== EXPLORER_EDITOR_FONT_DEFAULT) {
            record.font_size = fontSize;
        }
        if (tab.collapsedLines instanceof Set && tab.collapsedLines.size) {
            record.folds = Array.from(tab.collapsedLines)
                .filter(line => Number.isInteger(line) && line > 0)
                .sort((left, right) => left - right)
                .slice(0, 256);
            if (tab.collapsedIdentity) {
                record.fold_identity = tab.collapsedIdentity;
            }
        }
        return Object.keys(record).length ? record : null;
    }

    /* Clamped editor font size from one persisted tab view record; 0 = unset. */
    function explorerPersistedTabFontSize(raw) {
        const fontSize = Number(raw && typeof raw === 'object' ? raw.font_size : 0);
        if (!Number.isFinite(fontSize) || fontSize <= 0) {
            return 0;
        }
        return clampExplorerEditorFontSize(fontSize);
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
        return raw && typeof raw === 'object' && typeof raw.fold_identity === 'string'
            ? raw.fold_identity
            : '';
    }

    /* Inflate one persisted tab view back into the in-memory `tab.view`
       snapshot shape 2.e restores from (clamped fraction-based metrics). */
    function explorerInflatePersistedTabView(raw) {
        if (!raw || typeof raw !== 'object') {
            return null;
        }
        const mode = ['source', 'preview', 'diff'].includes(raw.mode) ? raw.mode : '';
        const identity = typeof raw.identity === 'string' ? raw.identity : '';
        if (!mode || !identity) {
            return null;
        }
        const fraction = Math.max(0, Math.min(1, Number(raw.scroll) || 0));
        return {
            mode,
            identity,
            scroll: {
                activeView: mode,
                panels: { [mode]: { scrollTopRatio: fraction, wasAtBottom: fraction >= 0.999 } },
                sidebar: {}
            }
        };
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
        if (previewPath) {
            previewRecord.path = previewPath;
        }
        if (previewDir) {
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
    }

    function restoreExplorerPersistedTabs(index) {
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
        /* The Preview tab's zoom, shown file, and browsed directory persist
           under its reserved id even when no pinned tabs were saved. */
        const rawPreviewView = rawViews[EXPLORER_PREVIEW_TAB_ID];
        const previewTab = explorerPreviewTab(pane);
        const previewFont = explorerPersistedTabFontSize(rawPreviewView);
        if (previewFont) {
            previewTab.fontSize = previewFont;
        }
        previewTab.collapsedLines = explorerPersistedMarkdownFolds(rawPreviewView);
        previewTab.collapsedIdentity = explorerPersistedMarkdownFoldIdentity(rawPreviewView);
        const savedPreviewPath = explorerNormalizeTabPath(
            rawPreviewView && typeof rawPreviewView === 'object' ? rawPreviewView.path : ''
        );
        const savedPreviewDir = explorerNormalizeTabPath(
            rawPreviewView && typeof rawPreviewView === 'object' ? rawPreviewView.dir : ''
        );
        if (savedPreviewDir) {
            previewTab.dirPath = savedPreviewDir;
        }
        /* Reopen the Preview tab's own content only when no pinned tab was
           saved as active — an active pinned tab wins the viewer, and the
           seeded path/dirPath above brings the Preview content back whenever
           the user returns to the tab. */
        const restorePreviewContent = () => {
            if (savedPreviewPath) {
                openExplorerFile(index, savedPreviewPath);
            } else if (savedPreviewDir) {
                loadExplorerPane(index, savedPreviewDir);
            } else {
                renderExplorerTabStrip(index);
            }
        };
        if (savedPreviewPath) {
            previewTab.path = savedPreviewPath;
            previewTab.name = explorerBaseName(savedPreviewPath);
        }
        if (!rawTabs.length) {
            restorePreviewContent();
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
            record.collapsedLines = explorerPersistedMarkdownFolds(rawViews[key]);
            record.collapsedIdentity = explorerPersistedMarkdownFoldIdentity(rawViews[key]);
            pane._explorerTabs.push(record);
        });
        const activeKey = explorerNormalizeTabPath(session.explorer_active_tab || '');
        const activeTab = activeKey
            ? pane._explorerTabs.find(tab => tab.pinned && explorerNormalizeTabPath(tab.path) === activeKey)
            : null;
        if (activeTab) {
            activateExplorerTab(index, activeTab.id);
        } else {
            restorePreviewContent();
        }
    }

    /* Read-only inline image viewer (ISSUE-2026 image support). The backend
       returns preview_type "image" with no content; the bytes stream from the
       dedicated /image route. Shares the tab strip, breadcrumb, and download
       button with the text viewer so tabs/refresh/persistence are unchanged. */
    function renderExplorerImage(index, data, { assignedTab = null } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !sessionId || !list || !viewer) {
            return false;
        }

        const path = data.path || '';
        const fileName = data.name || path || 'Image';
        if (assignedTab) {
            pane._explorerRenderedTabId = assignedTab.id;
        }

        cancelExplorerSearch(index);
        const searchState = ensureExplorerSearchState(pane, 'file');
        searchState.ranges = [];
        searchState.resultQuery = '';
        searchState.matchCount = 0;
        searchState.matchCapped = false;
        clearExplorerDirectorySearchControls(index);

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileName = fileName;
        pane._explorerFileContent = '';
        pane._explorerFileLanguage = '';
        pane._explorerFilePlain = false;
        pane._explorerPreviewHtml = '';
        pane._explorerGit = null;
        pane._explorerGitContext = null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = false;
        pane._explorerDiffCommit = '';
        pane._explorerDiffMode = '';
        pane._explorerLastFileView = 'source';
        pane._explorerPendingDiffScroll = null;

        const metaParts = ['Image'];
        const sizeLabel = formatExplorerSize(data.size);
        if (sizeLabel) {
            metaParts.push(sizeLabel);
        }
        const modifiedLabel = formatExplorerDate(data.modified);
        if (modifiedLabel) {
            metaParts.push(modifiedLabel);
        }
        const baseMeta = metaParts.join(' - ');
        const imageUrl = `/api/explorer/${encodeURIComponent(sessionId)}/image?path=${encodeURIComponent(path)}`;

        document.getElementById(`ph-${index}`)?.remove();
        list.classList.add('file-view');
        updateExplorerGitSummary(index, null);
        renderExplorerPathBreadcrumb(index, path, { root: data.root || '', fallbackText: fileName });
        const upButton = document.getElementById(`explorer-up-${index}`);
        if (upButton) {
            upButton.disabled = false;
        }

        viewer.innerHTML = `
            <div class="explorer-editor explorer-image-editor">
                <div class="explorer-editor-header">
                    <div class="explorer-editor-title">
                        <div class="explorer-editor-name" title="${escHtml(path || fileName)}">${escHtml(fileName)}</div>
                        <div class="explorer-editor-meta" data-explorer-image-meta="${index}">${escHtml(baseMeta)}</div>
                    </div>
                    <button type="button" class="explorer-download-btn" data-explorer-download="${index}" title="Download file" aria-label="Download file">${EXPLORER_DOWNLOAD_ICON}</button>
                </div>
                <div class="explorer-editor-body">
                    <div class="explorer-image-view" id="explorer-image-${index}">
                        <img class="explorer-image" alt="${escHtml(fileName)}" src="${escHtml(imageUrl)}">
                    </div>
                </div>
            </div>
        `;

        const downloadButton = list.querySelector(`[data-explorer-download="${index}"]`);
        if (downloadButton) {
            downloadButton.addEventListener('click', () => downloadExplorerFile(index));
        }
        const image = viewer.querySelector('.explorer-image');
        if (image) {
            /* Ctrl+scroll zooms the image (notes 3); double-click resets it. */
            enableExplorerWheelZoom(document.getElementById(`explorer-image-${index}`), image);
            image.addEventListener('load', () => {
                const metaEl = viewer.querySelector(`[data-explorer-image-meta="${index}"]`);
                if (metaEl && image.naturalWidth) {
                    metaEl.textContent = `${baseMeta} - ${image.naturalWidth} × ${image.naturalHeight}`;
                }
            });
            image.addEventListener('error', () => {
                const view = document.getElementById(`explorer-image-${index}`);
                if (view) {
                    view.innerHTML = '<div class="explorer-image-error">Unable to display this image.</div>';
                }
            });
        }

        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
        return true;
    }

    function renderExplorerFile(index, data, { scrollState = null, openDiff = false, diffCommit = '', diffMode = '', tab = '', pinned = false } = {}) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !list || !viewer) {
            return false;
        }
        const assignedTab = explorerAssignOpenTab(pane, data.path || '', { pinned, tab });
        pane._explorerRenderedTabId = assignedTab.id;

        const path = data.path || '';
        const fileName = data.name || path || 'File';
        // Images render in a dedicated read-only viewer (no source/preview/diff/
        // search/zoom), reusing the surrounding tab, breadcrumb, and download
        // plumbing so tabs, refresh, and persistence keep working unchanged.
        if (data.preview_type === 'image') {
            return renderExplorerImage(index, data, { assignedTab });
        }
        const codeLanguage = normalizeExplorerLanguage(data.language) || explorerCodeLanguage(path || fileName);
        const fileType = explorerFileTypeLabel(path || fileName, codeLanguage);
        const hasPreview = data.preview_type === 'markdown' && typeof data.preview_html === 'string';
        const requestedDiffCommit = String(diffCommit || '');
        const requestedDiffMode = requestedDiffCommit ? '' : String(diffMode || '');
        const hasGitDiff = explorerHasGitDiff(data.git) || Boolean(requestedDiffCommit);
        const metaParts = explorerFileMetaParts(data, fileType);
        const previousPath = pane._explorerFilePath || '';
        const contentIdentity = explorerFileContentIdentity(
            path, data.content, requestedDiffCommit, requestedDiffMode
        );
        if (assignedTab.collapsedIdentity !== contentIdentity) {
            assignedTab.collapsedLines = new Set();
            assignedTab.collapsedIdentity = contentIdentity;
        }
        /* 2.e: restore the tab's stored view mode + scroll when the content is
           still what the snapshot was taken from (OD-4 identity check). */
        const restoredTabView = scrollState
            ? null
            : explorerMatchingTabView(
                assignedTab,
                contentIdentity
            );
        const restoredMode = restoredTabView ? restoredTabView.mode : '';
        /* The Preview tab also keeps its sticky source/preview preference
           across *different* files (explicit diff requests and an
           identity-matched snapshot win); files without a Markdown preview
           fall back to source. Scroll does not carry across files. */
        const carriedMode = !restoredTabView
            && assignedTab
            && assignedTab.id === EXPLORER_PREVIEW_TAB_ID
            && !openDiff && !requestedDiffCommit && !requestedDiffMode
            ? assignedTab.preferredMode || ''
            : '';
        const preferredFileView = restoredMode || carriedMode;
        const keepDiffSplit = hasGitDiff && (
            Boolean(openDiff)
            || Boolean(requestedDiffCommit)
            || restoredMode === 'diff'
            || (
                previousPath === path
                && pane._explorerDiffSplit
                && pane._explorerDiffCommit === requestedDiffCommit
                && (pane._explorerDiffMode || '') === requestedDiffMode
            )
        );
        const initialFileView = keepDiffSplit
            ? 'diff'
            : (preferredFileView === 'preview' && hasPreview ? 'preview' : 'source');
        const searchState = ensureExplorerSearchState(pane, 'file');
        if (previousPath && previousPath !== path) {
            cancelExplorerSearch(index);
            searchState.activeIndex = 0;
            searchState.matchCount = 0;
            searchState.matchCapped = false;
            searchState.ranges = [];
            searchState.resultQuery = '';
        }
        clearExplorerDirectorySearchControls(index);

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileName = fileName;
        pane._explorerFileContent = data.content || '';
        pane._explorerFileLanguage = codeLanguage;
        pane._explorerFilePlain = pane._explorerFileContent.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD;
        pane._explorerPreviewHtml = hasPreview ? (data.preview_html || '') : '';
        pane._explorerGit = data.git || null;
        pane._explorerGitContext = data.git_context || null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = keepDiffSplit;
        pane._explorerDiffCommit = requestedDiffCommit;
        pane._explorerDiffMode = requestedDiffMode;
        pane._explorerLastFileView = initialFileView === 'preview'
            ? 'preview'
            : (pane._explorerLastFileView === 'preview' && hasPreview ? 'preview' : 'source');
        // Diff content loads async; stash the restored diff scroll until then.
        pane._explorerPendingDiffScroll = initialFileView === 'diff'
            ? (restoredTabView && restoredTabView.scroll.panels
                ? restoredTabView.scroll.panels.diff || null
                : null)
            : null;
        document.getElementById(`ph-${index}`)?.remove();
        list.classList.add('file-view');
        updateExplorerGitSummary(index, data.git_context || null);

        renderExplorerPathBreadcrumb(index, path, { root: data.root || '', fallbackText: fileName });
        const upButton = document.getElementById(`explorer-up-${index}`);
        if (upButton) {
            upButton.disabled = false;
        }

        viewer.innerHTML = `
            <div class="explorer-editor">
                <div class="explorer-editor-header">
                    <div class="explorer-editor-title">
                        <div class="explorer-editor-name" title="${escHtml(path || fileName)}">${escHtml(fileName)}</div>
                        <div class="explorer-editor-meta">${escHtml(metaParts.join(' - '))}</div>
                    </div>
                    ${(hasPreview || hasGitDiff) ? `
                        <div class="explorer-editor-tabs" role="tablist" aria-label="File view">
                            <button type="button" class="explorer-editor-tab" data-explorer-file-view="source" role="tab" aria-selected="${initialFileView === 'source' ? 'true' : 'false'}">Source</button>
                            ${hasPreview ? `<button type="button" class="explorer-editor-tab" data-explorer-file-view="preview" role="tab" aria-selected="${initialFileView === 'preview' ? 'true' : 'false'}" aria-keyshortcuts="Control+Shift+V Meta+Shift+V" title="Preview (Ctrl+Shift+V)">Preview</button>` : ''}
                            ${hasGitDiff ? `<button type="button" class="explorer-editor-tab" data-explorer-file-view="diff" data-explorer-diff-toggle="${index}" role="tab" aria-selected="${initialFileView === 'diff' ? 'true' : 'false'}" aria-pressed="${keepDiffSplit ? 'true' : 'false'}">Diff</button>` : ''}
                        </div>
                    ` : ''}
                    <div class="explorer-editor-zoom" aria-label="Editor font size controls">
                        <button type="button" class="explorer-zoom-btn" data-explorer-zoom-decrease="${index}" title="Decrease font size" aria-label="Decrease editor font size">-</button>
                        <span class="explorer-zoom-value" data-explorer-zoom-value="${index}"></span>
                        <button type="button" class="explorer-zoom-btn" data-explorer-zoom-increase="${index}" title="Increase font size" aria-label="Increase editor font size">+</button>
                    </div>
                    ${hasPreview ? `<button type="button" class="explorer-md-appearance-btn" data-explorer-md-appearance="${index}" title="Markdown appearance" aria-label="Markdown preview appearance" aria-haspopup="menu" aria-expanded="false">${EXPLORER_MD_APPEARANCE_ICON}</button>` : ''}
                    <button type="button" class="explorer-download-btn" data-explorer-download="${index}" title="Download file" aria-label="Download file">${EXPLORER_DOWNLOAD_ICON}</button>
                    <div class="explorer-editor-search" data-explorer-search="${index}">
                        <input
                            type="search"
                            class="explorer-search-input"
                            data-explorer-search-input="${index}"
                            placeholder="Find"
                            autocomplete="off"
                            spellcheck="false"
                            aria-label="Find in file"
                        >
                        <span class="explorer-search-count" data-explorer-search-count="${index}"></span>
                        <button type="button" class="explorer-search-btn" data-explorer-search-prev="${index}" title="Previous match" aria-label="Previous match">↑</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-next="${index}" title="Next match" aria-label="Next match">↓</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-clear="${index}" title="Clear search" aria-label="Clear search">×</button>
                    </div>
                </div>
                <div class="explorer-editor-body${keepDiffSplit ? ' split-diff' : ''}">
                    <div class="explorer-editor-main">
                        <div class="explorer-source-view explorer-editor-panel" id="explorer-code-${index}" data-explorer-file-panel="source" ${initialFileView === 'source' ? '' : 'hidden'}></div>
                        ${hasPreview ? `<div class="explorer-markdown-preview explorer-editor-panel" id="explorer-preview-${index}" data-explorer-file-panel="preview" ${initialFileView === 'preview' ? '' : 'hidden'}></div>` : ''}
                    </div>
                    ${hasGitDiff ? `<aside class="explorer-diff-split" id="explorer-diff-panel-${index}" data-explorer-file-panel="diff" ${keepDiffSplit ? '' : 'hidden'}><div class="explorer-diff-content" id="explorer-diff-code-${index}"></div></aside>` : ''}
                </div>
            </div>
        `;

        renderExplorerSource(index);
        const preview = document.getElementById(`explorer-preview-${index}`);
        if (preview && hasPreview) {
            preview.innerHTML = pane._explorerPreviewHtml;
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            wireExplorerMarkdownLinks(index, preview);
            applyExplorerMarkdownAppearanceToElement(preview, explorerMarkdownAppearance());
            renderExplorerMermaid(preview);
        }

        const appearanceButton = list.querySelector(`[data-explorer-md-appearance="${index}"]`);
        if (appearanceButton) {
            appearanceButton.addEventListener('click', () => {
                if (document.getElementById('explorer-md-menu')) {
                    dismissExplorerMarkdownAppearanceMenu();
                } else {
                    showExplorerMarkdownAppearanceMenu(appearanceButton);
                }
            });
        }

        const downloadButton = list.querySelector(`[data-explorer-download="${index}"]`);
        if (downloadButton) {
            downloadButton.addEventListener('click', () => {
                downloadExplorerFile(index);
            });
        }
        list.querySelectorAll('[data-explorer-file-view]').forEach(button => {
            button.addEventListener('click', () => {
                if (button.dataset.explorerFileView === 'diff') {
                    toggleExplorerDiffSplit(index);
                } else {
                    setExplorerFileView(index, button.dataset.explorerFileView || 'source');
                }
            });
        });
        if (keepDiffSplit) {
            loadExplorerDiff(index);
        }
        wireExplorerEditorZoomControls(index);
        wireExplorerSearchControls(index);
        applyExplorerSearch(index);
        // An explicit scrollState (in-place refresh) wins; otherwise fall back
        // to the tab's stored snapshot, aligned with the restored view mode.
        const effectiveScrollState = scrollState || (restoredTabView
            ? { ...restoredTabView.scroll, activeView: initialFileView }
            : null);
        restoreExplorerFileScroll(index, effectiveScrollState);
        renderExplorerTabStrip(index);
        persistExplorerTabsToSession(index);
        return true;
    }

    function updateExplorerFileInPlace(index, data, scrollState = null) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const code = document.getElementById(`explorer-code-${index}`);
        if (!pane || !list || !code) {
            return false;
        }

        const path = data.path || '';
        if (path && pane._explorerFilePath && path !== pane._explorerFilePath) {
            return false;
        }

        const hasPreview = data.preview_type === 'markdown' && typeof data.preview_html === 'string';
        const hasGitDiff = explorerHasGitDiff(data.git);
        const preview = document.getElementById(`explorer-preview-${index}`);
        const diffPanel = document.getElementById(`explorer-diff-code-${index}`);
        if (hasPreview !== Boolean(preview) || hasGitDiff !== Boolean(diffPanel)) {
            return false;
        }

        const fileName = data.name || path || 'File';
        const codeLanguage = normalizeExplorerLanguage(data.language) || explorerCodeLanguage(path || fileName);
        const fileType = explorerFileTypeLabel(path || fileName, codeLanguage);
        cancelExplorerSearch(index);
        const searchState = ensureExplorerSearchState(pane, 'file');
        searchState.ranges = [];
        searchState.resultQuery = '';
        searchState.matchCapped = false;
        pane._explorerFileContent = data.content || '';
        pane._explorerFileLanguage = codeLanguage;
        pane._explorerFilePlain = pane._explorerFileContent.length > EXPLORER_PLAIN_PREVIEW_THRESHOLD;
        pane._explorerPreviewHtml = hasPreview ? (data.preview_html || '') : '';
        pane._explorerGit = data.git || null;
        pane._explorerGitContext = data.git_context || null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        renderExplorerSource(index);
        if (preview && hasPreview) {
            preview.innerHTML = pane._explorerPreviewHtml;
            if (!pane._explorerFilePlain) {
                highlightExplorerPreviewCode(preview);
            }
            wireExplorerMarkdownLinks(index, preview);
            applyExplorerMarkdownAppearanceToElement(preview, explorerMarkdownAppearance());
            renderExplorerMermaid(preview);
        }
        if (diffPanel && hasGitDiff) {
            renderExplorerDiff(index);
            if (pane._explorerDiffSplit) {
                loadExplorerDiff(index);
            }
        }
        applyExplorerEditorFontSize(index);
        updateExplorerGitSummary(index, data.git_context || null);

        const nameLabel = list.querySelector('.explorer-editor-name');
        if (nameLabel) {
            nameLabel.textContent = fileName;
            nameLabel.title = path || fileName;
        }
        const metaLabel = list.querySelector('.explorer-editor-meta');
        if (metaLabel) {
            metaLabel.textContent = explorerFileMetaParts(data, fileType).join(' - ');
        }
        renderExplorerPathBreadcrumb(index, path || pane._explorerFilePath, {
            root: data.root || '',
            fallbackText: fileName
        });

        pane._explorerMode = 'file';
        pane._explorerFilePath = path || pane._explorerFilePath;
        applyExplorerSearch(index);
        restoreExplorerFileScroll(index, scrollState);
        return true;
    }

    function renderExplorerCommitDiffFile(index, path, commit) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        const viewer = explorerEnsureViewerShell(index);
        if (!pane || !list || !viewer || !path || !commit) {
            return false;
        }

        const fileName = path.split(/[\\/]/).filter(Boolean).pop() || path;
        const codeLanguage = explorerCodeLanguage(path);
        clearExplorerDirectorySearchControls(index);
        cancelExplorerSearch(index);
        explorerCaptureActiveTabView(index);
        pane._explorerRenderedTabId = explorerAssignOpenTab(pane, path, {}).id;

        pane._attached = true;
        pane._explorerMode = 'file';
        pane._explorerFilePath = path;
        pane._explorerFileContent = '';
        pane._explorerFileLanguage = codeLanguage;
        pane._explorerPreviewHtml = '';
        pane._explorerGit = null;
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        pane._explorerDiffContent = '';
        pane._explorerDiffSplit = true;
        pane._explorerDiffCommit = commit;
        pane._explorerDiffMode = '';
        pane._explorerLastFileView = 'source';
        pane._explorerPendingDiffScroll = null;
        document.getElementById(`ph-${index}`)?.remove();
        list.classList.add('file-view');

        renderExplorerPathBreadcrumb(index, path, { fallbackText: fileName });
        const upButton = document.getElementById(`explorer-up-${index}`);
        if (upButton) {
            upButton.disabled = false;
        }

        viewer.innerHTML = `
            <div class="explorer-editor">
                <div class="explorer-editor-header">
                    <div class="explorer-editor-title">
                        <div class="explorer-editor-name" title="${escHtml(path)}">${escHtml(fileName)}</div>
                        <div class="explorer-editor-meta">${escHtml(`Git commit diff - ${commit.slice(0, 7)}`)}</div>
                    </div>
                    <div class="explorer-editor-tabs" role="tablist" aria-label="File view">
                        <button type="button" class="explorer-editor-tab" data-explorer-file-view="diff" data-explorer-diff-toggle="${index}" role="tab" aria-selected="true" aria-pressed="true">Diff</button>
                    </div>
                    <div class="explorer-editor-search" data-explorer-search="${index}">
                        <input
                            type="search"
                            class="explorer-search-input"
                            data-explorer-search-input="${index}"
                            placeholder="Find"
                            autocomplete="off"
                            spellcheck="false"
                            aria-label="Find in file"
                        >
                        <span class="explorer-search-count" data-explorer-search-count="${index}"></span>
                        <button type="button" class="explorer-search-btn" data-explorer-search-prev="${index}" title="Previous match" aria-label="Previous match">↑</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-next="${index}" title="Next match" aria-label="Next match">↓</button>
                        <button type="button" class="explorer-search-btn" data-explorer-search-clear="${index}" title="Clear search" aria-label="Clear search">×</button>
                    </div>
                </div>
                <div class="explorer-editor-body split-diff">
                    <aside class="explorer-diff-split" id="explorer-diff-panel-${index}" data-explorer-file-panel="diff"><div class="explorer-diff-content" id="explorer-diff-code-${index}"></div></aside>
                </div>
            </div>
        `;

        list.querySelectorAll('[data-explorer-file-view]').forEach(button => {
            button.addEventListener('click', () => {
                if (button.dataset.explorerFileView === 'diff') {
                    setExplorerFileView(index, 'diff');
                }
            });
        });
        wireExplorerSearchControls(index);
        applyExplorerEditorFontSize(index);
        loadExplorerDiff(index);
        renderExplorerTabStrip(index);
        return true;
    }

    async function openExplorerFile(index, path, { showLoading = true, preserveScroll = false, openDiff = false, diffCommit = '', diffMode = '', pinned = false, tab = '' } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !isExplorerSession(pane._session) || !sessionId || !path) {
            return false;
        }

        const wasDirectoryOpen = pane._explorerMode === 'directory';
        const hasDiffTarget = Boolean(openDiff || diffCommit);
        const scrollState = preserveScroll && !hasDiffTarget ? captureExplorerFileScroll(index) : null;
        // Opening another file swaps the shown tab implicitly; keep the
        // outgoing tab's mode + scroll before the loading placeholder renders.
        explorerCaptureActiveTabView(index);
        if (showLoading && !wasDirectoryOpen) {
            renderExplorerMessage(index, 'Opening file...');
        }
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to open file');
            }
            if (
                preserveScroll
                && !hasDiffTarget
                && pane._explorerMode === 'file'
                && pane._explorerFilePath === (data.path || path)
                && updateExplorerFileInPlace(index, data, scrollState)
            ) {
                return true;
            }
            const rendered = renderExplorerFile(index, data, { scrollState, openDiff, diffCommit, diffMode, pinned, tab });
            if (rendered) {
                revealExplorerTreePath(index);
            }
            return rendered;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer file open failed:', error);
            renderExplorerDirectoryOpenError(index, error.message || 'Failed to open file.');
            return false;
        }
    }

    async function refreshExplorerPane(index) {
        const pane = terminals[index];
        const refreshGitSidebar = Boolean(pane?._explorerGitSidebarOpen);
        const refreshTreeSidebar = Boolean(pane?._explorerTreeSidebarOpen);
        if (refreshGitSidebar) {
            invalidateExplorerGitRepo(index);
        }
        let refreshed = false;
        if (pane?._explorerMode === 'file' && pane._explorerFilePath) {
            refreshed = await openExplorerFile(index, pane._explorerFilePath, {
                showLoading: false,
                preserveScroll: true,
                tab: pane._explorerActiveTabId
            });
        } else if (pane?._explorerMode === 'viewer') {
            /* Empty Preview tab: nothing to reload in the viewer body; the tree
               and Git sidebars refresh below. */
            refreshed = true;
        } else {
            refreshed = await loadExplorerPane(index, null, { force: true });
        }
        if (refreshGitSidebar) {
            await loadExplorerGitRepo(index);
        }
        if (refreshTreeSidebar) {
            await reloadExplorerTree(index);
        }
        return refreshed;
    }

    async function syncExplorerPane(index) {
        const pane = terminals[index];
        if (pane?._explorerMode === 'file' && pane._explorerFilePath) {
            return true;
        }
        if (pane?._attached) {
            return true;
        }
        /* First show: the read-only tabbed viewer with an empty Preview tab and
           the Files tree opened for navigation (ISSUE-2026-014). */
        return openExplorerViewer(index);
    }

    async function loadExplorerPane(index, path = null, { force = false, showLoading = true } = {}) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !isExplorerSession(pane._session) || !sessionId) {
            return false;
        }

        const isNavigation = path !== null;
        if (pane._attached && !force && !isNavigation) {
            return true;
        }

        const nextPath = path === null ? (pane._explorerPath || null) : path;
        // Navigation swaps the shown tab to Preview (tree click, breadcrumb,
        // open-folder); keep the outgoing tab's mode + scroll before the
        // loading placeholder guts the viewer (2.e).
        explorerCaptureActiveTabView(index);
        if (showLoading) {
            renderExplorerMessage(index, 'Loading directory...');
        }

        try {
            const entriesUrl = `/api/explorer/${encodeURIComponent(sessionId)}/entries`;
            const response = await fetch(
                nextPath === null ? entriesUrl : `${entriesUrl}?path=${encodeURIComponent(nextPath)}`
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load directory');
            }

            pane._attached = true;
            pane._explorerMode = 'directory';
            pane._explorerFilePath = '';
            pane._explorerFileContent = '';
            pane._explorerFileLanguage = '';
            pane._explorerPreviewHtml = '';
            pane._explorerGit = null;
            pane._explorerGitContext = data.git || null;
            pane._explorerDiffLoaded = false;
            pane._explorerDiffContent = '';
            pane._explorerPath = data.path || '';
            pane._explorerParentPath = data.parent_path || '';
            pane._explorerEntries = Array.isArray(data.entries) ? data.entries : [];
            cancelExplorerSearch(index);
            if (isNavigation) {
                resetExplorerDirectorySearch(pane);
            }
            /* Directory browsing lives in the permanent Preview tab; pinned file
               tabs are untouched by navigation (ISSUE-2026-014). The browsed
               path is recorded on the tab itself (`dirPath`) so swapping to a
               pinned tab and back cannot lose it — the pane-global
               `_explorerPath`/`_explorerMode` fields follow whatever tab was
               rendered last. */
            ensureExplorerTabState(pane);
            pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            pane._explorerRenderedTabId = EXPLORER_PREVIEW_TAB_ID;
            const previewTab = explorerPreviewTab(pane);
            previewTab.path = '';
            previewTab.name = '';
            previewTab.dirPath = pane._explorerPath;
            document.getElementById(`ph-${index}`)?.remove();
            updateExplorerGitSummary(index, data.git || null);
            renderExplorerDirectorySearchControls(index);
            revealExplorerTreePath(index);

            renderExplorerPathBreadcrumb(index, data.path || '', { root: data.root || '' });
            const upButton = document.getElementById(`explorer-up-${index}`);
            const list = document.getElementById(`explorer-list-${index}`);
            if (upButton) {
                upButton.disabled = !data.parent_path && !data.path;
            }
            if (!list) {
                return true;
            }
            list.classList.remove('file-view');
            wireExplorerSearchControls(index);
            applyExplorerSearch(index, { resetActive: true });
            /* Returning to a directory the Preview tab already showed (after a
               pinned tab was active) restores its captured scroll when the
               listing identity still matches (OD-4); a genuinely new directory
               never matches, so navigation always starts at the top. */
            const restoredDirView = explorerMatchingTabView(
                previewTab,
                explorerDirectoryContentIdentity(pane._explorerPath, pane._explorerEntries)
            );
            if (restoredDirView) {
                restoreExplorerFileScroll(index, restoredDirView.scroll);
            }
            renderExplorerTabStrip(index);
            return true;
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer load failed:', error);
            renderExplorerMessage(index, error.message || 'Failed to load directory.');
            return false;
        }
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
