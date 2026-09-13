    /* ── Theme management ── */
    const SURFACE_MODE_STORAGE_KEY = 'gridvibe.terminalSurfaceMode';
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

    /* App Settings dialog hooks (app-settings.js owns the dialog itself, and
       both pages render it from the same partial). A window never receives its
       own BroadcastChannel/storage notification, so a save made here is
       applied to this window straight from the hook. */
    function appSettingsNotify(text, type = '') {
        showTerminalToast(text, type);
    }

    function onAppSettingsSaved(_data, payload) {
        applyAppConfigUpdate(payload);
    }

    initTheme();

    function normalizeSurfaceMode(mode) {
        return mode === 'max' ? 'max' : 'normal';
    }

    /* Surface mode has exactly one persisted source of truth — the global
       `workspace.surface_mode` App Setting, which every window reads live.
       The toolbar toggle is a *per-window* view override on top of it, stored
       as `<mode>@<global mode it was made against>` so that a later App
       Settings change always wins instead of being shadowed forever by an old
       toggle. (This key used to be written and never read back at all.) */
    function readSurfaceModeOverride(globalMode) {
        try {
            const [mode, base] = String(
                localStorage.getItem(SURFACE_MODE_STORAGE_KEY) || ''
            ).split('@');
            if ((mode === 'max' || mode === 'normal') && base === globalMode) {
                return mode;
            }
        } catch (_) {}
        return '';
    }

    function storeSurfaceModeOverride(mode, globalMode) {
        try {
            localStorage.setItem(SURFACE_MODE_STORAGE_KEY, `${mode}@${globalMode}`);
        } catch (_) {}
    }

    function clearSurfaceModeOverride() {
        try {
            localStorage.removeItem(SURFACE_MODE_STORAGE_KEY);
        } catch (_) {}
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

    function applySurfaceMode(enabled, { refit = false } = {}) {
        const active = Boolean(enabled);
        document.body.classList.toggle('surface-max', active);
        updateSurfaceModeButton(active);
        if (refit) {
            refitAttachedTerminalsForSurfaceMode();
        }
    }

    function surfaceModeIsShowing(mode) {
        return (mode === 'max') === document.body.classList.contains('surface-max');
    }

    function toggleSurfaceMode() {
        const next = document.body.classList.contains('surface-max') ? 'normal' : 'max';
        surfaceModeChangedManually = true;
        storeSurfaceModeOverride(next, currentGlobalSurfaceMode);
        applySurfaceMode(next === 'max', { refit: true });
    }

    /* The global setting changed: it wins everywhere, discarding a per-window
       toggle that was made against the previous value. */
    function adoptGlobalSurfaceMode(mode, { refit = false } = {}) {
        currentGlobalSurfaceMode = normalizeSurfaceMode(mode);
        clearSurfaceModeOverride();
        surfaceModeChangedManually = false;
        if (!surfaceModeIsShowing(currentGlobalSurfaceMode)) {
            applySurfaceMode(currentGlobalSurfaceMode === 'max', { refit });
        }
    }

    function initSurfaceMode() {
        const override = readSurfaceModeOverride(currentGlobalSurfaceMode);
        if (override) {
            surfaceModeChangedManually = true;
        }
        applySurfaceMode((override || currentGlobalSurfaceMode) === 'max');
    }

    /* Every /api/sessions refresh reports the *current* global setting (it used
       to report a copy frozen into the session group at launch, which is why a
       reload or a restore-after-restart reverted the setting). */
    function applyConfiguredSurfaceMode(data, { refit = false } = {}) {
        const mode = normalizeSurfaceMode(data?.surface_mode || currentGlobalSurfaceMode);
        if (mode !== currentGlobalSurfaceMode) {
            /* Setting changed while this window missed the broadcast (hidden
               window, dropped socket) — reconcile against the server. */
            adoptGlobalSurfaceMode(mode, { refit });
            return;
        }
        if (surfaceModeChangedManually || surfaceModeIsShowing(mode)) {
            return;
        }
        applySurfaceMode(mode === 'max', { refit });
    }

    function applyAppConfigSurfaceMode(message) {
        if (!message || typeof message !== 'object' || !message.workspace) {
            return;
        }

        adoptGlobalSurfaceMode(message.workspace.surface_mode, { refit: true });
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
        applyAppConfigMultiWorkspace(message);
        /* Voice enable/engine and the push-to-talk keybind are saved from the
           same App Settings dialog, so re-read both here instead of leaving
           open tabs on boot-time values until a restart (stage J issue 3). */
        _refreshVoiceRuntimeState();
    }

    /* The whole window is reloaded when the mode changes elsewhere: the flag
       reaches the page as server-rendered markup (MULTI_WORKSPACE_ENABLED),
       which the session menu reads when it builds its Workspace section. A
       window whose workspace was closed by that change closes instead
       (workspaces.js). */
    function applyAppConfigMultiWorkspace(message) {
        const enabled = message?.workspace?.multi_workspace_enabled;
        if (typeof enabled !== 'boolean' || enabled === isMultiWorkspaceEnabled()) {
            return;
        }
        reactToMultiWorkspaceFlagChange(enabled, { currentWorkspaceId }).catch(() => {});
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
    async function reconcileAppConfig() {
        try {
            const response = await fetch('/api/app-config');
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            applyAppConfigTheme(data);
            /* Reconciling is not an explicit save, so it goes through the
               change-only path — it must never discard this window's own
               surface toggle when the global setting has not moved. */
            applyConfiguredSurfaceMode(
                { surface_mode: data?.workspace?.surface_mode },
                { refit: true }
            );
        } catch (_error) {}
    }

    function setupAppConfigUpdateListeners() {
        if ('BroadcastChannel' in window) {
            try {
                const channel = new BroadcastChannel(APP_CONFIG_BROADCAST_CHANNEL);
                channel.onmessage = event => {
                    /* A window that saved the settings has already applied them
                       and may still be acting on the change; its own broadcast
                       is not news (shared.js explains the same-document case). */
                    if (!isOwnBroadcast(event.data)) {
                        applyAppConfigUpdate(event.data || {});
                    }
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
        return getStoredWorkspaceTopbarVisible(currentWorkspaceId) ?? true;
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

    /* Window chrome rides the same ordered compare-and-swap transaction as
       group presentation, on its own workspace revision — two fast toggles
       cannot land out of order, and a failed write is repaired instead of being
       left for the next autosave to commit. */
    function reportTopbarVisibility() {
        noteWorkspacePresentationChanged();
    }

    /* A hidden bar is revealed on demand by GridVibeTopbarPeek — theme, max
       surface, broadcast, fullscreen and App Settings are all still up here,
       and so is the save status line. (Sessions and Workspace are not: they
       are one button down in the session tab line, which is why the peek no
       longer has a menu to hold itself open for.) It owns *when*; the page
       owns what that looks like. Two body classes, two meanings:

       - topbar-collapsed is the chevron's persisted choice, and stays the one
         thing every topbar_visible read-back looks at;
       - topbar-hidden is the derived "not in the flow" state, which fullscreen
         also raises for its duration without ever touching the stored value.

       topbar-peek is the transient overlay and is never persisted. */
    const topbarPeek = window.GridVibeTopbarPeek.create({
        getElement: id => document.getElementById(id),
        setTimeout: (fn, ms) => window.setTimeout(fn, ms),
        clearTimeout: handle => window.clearTimeout(handle),
        onChange: ({ hidden, peeking, hiddenChanged }) => {
            document.body.classList.toggle('topbar-hidden', hidden);
            document.body.classList.toggle('topbar-peek', peeking);
            /* The shortcut panel hangs off a button on this bar, so a bar that
               is out of the flow and not being peeked at leaves the panel
               floating over the grid with nothing above it. The chevron,
               fullscreen and a retracting peek all arrive here, which is why
               this is the one place that has to say it. */
            if (hidden && !peeking) {
                closeShortcutsHelp();
            }
            /* Only a flow change resizes anything: the peek is an overlay, so
               a pointer trip to the top edge costs no terminal refit. */
            if (hiddenChanged && gridBuilt) {
                refitAttachedTerminalsForSurfaceMode();
            }
        }
    });

    function applyTopbarVisibility(visible, { persist = false, report = false } = {}) {
        const shouldShow = Boolean(visible);
        document.body.classList.toggle('topbar-collapsed', !shouldShow);
        updateTopbarToggleButton(shouldShow);
        topbarPeek.setCollapsed(!shouldShow);
        if (persist) {
            storeWorkspaceTopbarVisible(currentWorkspaceId, shouldShow);
        }
        if (report) {
            reportTopbarVisibility();
        }
    }

    function toggleTopbarVisibility() {
        applyTopbarVisibility(document.body.classList.contains('topbar-collapsed'), {
            persist: true,
            report: true
        });
    }

    /* The override object lives in GridVibeExplorerThemeStore (DOM-free,
       bounded to live pane keys — SGP-09). The wrappers below keep the local
       call sites unchanged. */
    function liveExplorerThemeKeys() {
        return [...document.querySelectorAll('.explorer-pane')]
            .map(card => card.dataset.explorerThemeKey || '')
            .filter(Boolean);
    }

    function normalizeExplorerTheme(theme) {
        return GridVibeExplorerThemeStore.normalizeTheme(theme);
    }

    function hasExplorerThemeOverride(key = '') {
        return GridVibeExplorerThemeStore.hasOverride(localStorage, key);
    }

    function getExplorerTheme(key = '') {
        return GridVibeExplorerThemeStore.getTheme(localStorage, key);
    }

    function saveExplorerTheme(key, theme) {
        GridVibeExplorerThemeStore.saveTheme(localStorage, key, theme, liveExplorerThemeKeys());
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
        /* The localStorage override is keyed by an ephemeral session id and
           cannot survive a restart; the manager copy can. It is now only a
           same-run cache, bounded to live pane keys on every write (SGP-09). */
        notePanePresentationChanged(index);
    }

    /* ─────────────────────────────────────────────
       State  (socket is initialised AFTER functions
       so that a CDN failure can't kill the whole script)
    ───────────────────────────────────────────── */
    let terminals  = [];
    let sessionIds = [];
    let gridBuilt  = false;
    let _focusedTerminalIndex = -1;
    let _activeExplorerIndex = -1;
    let socket     = null;   // set at the bottom after all defs
    const lifecycleWindowId = getLifecycleWindowId();
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
    const initialRouteParams = new URLSearchParams(window.location.search);
    const currentWorkspaceId = String(CURRENT_WORKSPACE_ID || 'default');
    const workspaceWasExplicit = initialRouteParams.has('workspace');
    /* This window is the arrival end of every workspace swap (workspaces.js
       arms the pulse at the departing end). Wired here, at the one place that
       knows which workspace this page is. */
    watchWorkspaceArrivals(currentWorkspaceId);
    /* And the other half of the same arrival: a row in the agent dashboard
       names a session tab and one pane, not just a window. Wired beside the
       pulse, at the one place that knows which workspace this page is. */
    watchWorkspaceFocusTargets(currentWorkspaceId, applyWorkspaceFocusTarget);
    let activeGroupId = initialRouteParams.get('group') || '';
    let sessionGroups = [];
    let activeLoadToken = 0;
    let knownGroupIds = [];
    let workspaceSaveTargets = new Map();
    /* Live-presentation sync state. Declared here rather than beside the
       adapter below it, because reportTopbarVisibility() sits far earlier in
       the file and a `let` would still be in its temporal dead zone there. */
    let presentationSync;
    let presentationSyncBuilt = false;
    let workspacePresentationRevision = 0;
    let surfaceModeChangedManually = false;
    let currentGlobalSurfaceMode = normalizeSurfaceMode(DEFAULT_SURFACE_MODE);
    let pendingSplitRestore = null;
    /* Live explorer/browser client state captured before a terminal close so the
       forced grid rebuild does not wipe sibling panes (ISSUE-2026-027). */
    let pendingCloseClientState = null;
    let pendingModeSwitchSessionIds = new Set();
    let savedSessionResolver = null;
    let saveSessionAsResolver = null;
    const MAX_SPLIT_TERMINALS = Math.min(16, Number(MAX_SESSIONS || 16));

    function isSessionModeSwitchPending(sessionId) {
        return pendingModeSwitchSessionIds.has(sessionId);
    }

    const MIN_SPLIT_COLS = 8;
    /* A stacked (horizontal) split must leave each half with at least this many
       rows *after* its own header is subtracted, so this floor — not the column
       floor — is what gates stacking. Kept low (4 rows) so a normal wide pane can
       still be stacked two or three deep instead of only side-by-side; a terminal
       narrower/shorter than the floors simply can't be split further. */
    const MIN_SPLIT_ROWS = 4;
    const MIN_RESIZE_SURFACE_RATIO = 1 / 16;
    /* Grid-unit size of one base layout cell. Larger = more headroom to keep
       halving a pane before the integer-grid `>= 2` guard bites, so splitting is
       gated by the real character-size minimum rather than the coordinate
       resolution. A single pane is two cells wide by one tall, so the densest
       base (4 columns) spans 4 * SPLIT_CELL_UNIT grid lines — kept within the
       backend's split-layout coordinate bound (see saved_sessions.py). */
    const SPLIT_CELL_UNIT = 8;
    let splitSlotRects = null;
    let splitColumnWeights = null;
    let splitRowWeights = null;
    let activeGridResize = null;
    let originalSplitSlotCount = 0;

    function isExplorerSession(session) {
        return ['ssh', 'wsl'].includes(session?.mode) && session?.startup_mode === 'explorer';
    }

    function isExplorerPaneInstance(terminal) {
        return terminal?._paneType === 'explorer';
    }

    /* Two page-wide explorer caches are built lazily and were never given
       back: the worker pool (explorer-worker-client.js) — up to four threads
       with a Highlight.js build resident in each — and the line-record cache
       in explorer-viewer.js, which pins whichever documents it last answered
       about. Both outlived the last explorer pane for the life of the page.

       Released only when *no* explorer pane is left anywhere — the visible
       grid and every cached group. The predicate has to be that strict for the
       pool: terminating mid-flight rejects the running jobs as superseded, so
       a pane still on screen would sit on its plain first paint until
       something else happened to repaint it. Neither cache is disabled by
       this, only emptied — the pool respawns on the next request and the
       records rebuild on the next render, so a reopened pane pays one worker
       startup and one document walk. */
    function releaseExplorerResourcesIfIdle() {
        if (terminals.some(isExplorerPaneInstance)) {
            return;
        }
        for (const cached of cachedGroupViews.values()) {
            if ((cached.terminals || []).some(isExplorerPaneInstance)) {
                return;
            }
        }
        (typeof window !== 'undefined' && window.GridVibeExplorerWorkers)?.terminate?.();
        explorerReleaseLineRecordCache();
    }

    function isBrowserSession(session) {
        return session?.mode === 'wsl' && session?.startup_mode === 'browser';
    }

    function isBrowserPaneInstance(terminal) {
        return terminal?._paneType === 'browser';
    }

    /* Browser-pane URL/tab helpers live in browser-pane.js (loaded first):
       getBrowserSessionUrl, normalizeBrowserUrlInput, renderBrowserSurface,
       browserSurfaceHtml, wireBrowserOnlyControls, navigateBrowserPane,
       reloadBrowserPane, openBrowserPaneExternally, browserSerializeTabs. */

    /* What a pane's header prints. The rule -- a title the user typed wins, an
       agent pane is otherwise named after its agent, and only then "Terminal N"
       -- is agent-identity.js's, shared with the dashboard row that lists the
       same pane, so the two can never disagree about what it is called.

       Display only. The persisted title stays whatever the launcher wrote, so
       naming a pane after its agent never turns that name into the pane's own.
    */
    function paneDisplayTitle(session, index) {
        return window.GridVibeAgentIdentity.paneDisplayTitle(
            session,
            index,
            typeof AGENT_OPTIONS === 'undefined' ? [] : AGENT_OPTIONS
        );
    }

    function paneAgentIconHtml(session) {
        const identity = window.GridVibeAgentIdentity;
        if (identity.paneKindForSession(session) !== 'agent') return '';
        return window.GridVibeAgentGlyphs.agentGlyphMarkup(identity.agentKeyForSession(session));
    }

    function syncPaneAgentIcon(icon, session) {
        if (!icon) return;
        const html = paneAgentIconHtml(session);
        if (icon.innerHTML !== html) icon.innerHTML = html;
        icon.hidden = !html;
    }

    function getSessionApiPath(groupId = activeGroupId) {
        const params = new URLSearchParams({ workspace_id: currentWorkspaceId });
        if (groupId) {
            params.set('group', groupId);
        }
        return `/api/sessions?${params.toString()}`;
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

    /* ── Landing on the pane the agent dashboard named ──
       A dashboard row names a workspace, a session tab and one pane. The
       workspace is the window this page already is; the other two are this.

       Two things make it more than one call to `switchGroup`. A window that was
       already open is raised without being reloaded, so the tab has to be
       changed here rather than through the URL the bridge never revisits; and a
       window that was just *created* claims the request while its grid is still
       being built, so the pane it names does not exist yet. The target is
       therefore held and settled again at the end of the load that will produce
       it — under a deadline, because a session closed between the click and the
       arrival must not leave the window waiting for a pane that is never
       coming. */
    const WORKSPACE_FOCUS_TARGET_GRACE_MS = 15000;
    let pendingWorkspaceFocusTarget = null;

    function applyWorkspaceFocusTarget(target) {
        const groupId = String(target?.groupId || '');
        const sessionId = String(target?.sessionId || '');
        if (!groupId && !sessionId) {
            return;
        }
        pendingWorkspaceFocusTarget = {
            groupId,
            sessionId,
            expiresAt: Date.now() + WORKSPACE_FOCUS_TARGET_GRACE_MS
        };
        settleWorkspaceFocusTarget();
    }

    async function settleWorkspaceFocusTarget() {
        const target = pendingWorkspaceFocusTarget;
        if (!target) {
            return;
        }
        if (Date.now() > target.expiresAt) {
            pendingWorkspaceFocusTarget = null;
            return;
        }
        if (target.groupId && target.groupId !== activeGroupId) {
            /* A tab this window has never heard of is not switched to: that is
               how a stale row would blank the grid it landed on. It is left
               standing instead, for the group list this window has not loaded
               yet — or for the deadline. */
            if (!sessionGroups.some(group => group.group_id === target.groupId)) {
                return;
            }
            /* switchGroup runs the whole load and settles again from inside it.
               It also declines — an unsaved editor, a copy in flight — and a
               decline leaves the target standing rather than pretending the
               trip finished. */
            await switchGroup(target.groupId);
            return;
        }
        if (!target.sessionId) {
            pendingWorkspaceFocusTarget = null;
            return;
        }
        const resolved = resolveSessionTarget(target.sessionId);
        if (!resolved || !resolved.active) {
            /* Not painted yet. The load that paints it settles again. */
            return;
        }
        pendingWorkspaceFocusTarget = null;
        focusPaneForArrival(resolved.index);
    }

    /* Real keyboard focus for a terminal, and the highlight a click would give
       for a pane that cannot take it: an explorer or browser pane has no xterm
       to focus, and landing on one still has to *show* which pane was meant. */
    function focusPaneForArrival(index) {
        const card = document.getElementById(`tc-${index}`);
        card?.scrollIntoView?.({ block: 'nearest' });
        const terminal = terminals[index];
        if (terminal?.term && isPlainTerminalCard(card)) {
            try { terminal.term.focus(); } catch (_error) {}
            return;
        }
        card?.focus?.();
    }

    /* ── What a pane calls itself, repainted without a rebuild ──
       A pane's agent can change while the pane stays exactly where it is: one
       that exits hands the terminal back, and one started by hand at the prompt
       takes it over. Both arrive as a status broadcast, and until this existed
       the header went on naming the agent the pane was *launched* with — so a
       Claude session could sit under a "OpenAI Codex CLI" title until something
       else forced a rebuild.

       The two header fields that read from the session record, and nothing
       else: the reset control's affordance is decided by the transport rather
       than by what is running in it, and syncing the shell controls here would
       close a menu the user has open. */
    function syncPaneIdentityChrome(index, session) {
        syncPaneAgentIcon(document.getElementById(`ticon-${index}`), session);
        const nameLabel = document.getElementById(`tname-${index}`);
        if (nameLabel) {
            const identity = window.GridVibeAgentIdentity;
            const key = identity.paneKindForSession(session) === 'agent'
                ? window.GridVibeAgentGlyphs.agentGlyphKey(identity.agentKeyForSession(session)) : '';
            if (key && nameLabel.dataset.agent !== key) nameLabel.dataset.agent = key;
            else if (!key && nameLabel.dataset.agent) delete nameLabel.dataset.agent;
        }
        const title = paneDisplayTitle(session, index);
        if (nameLabel && nameLabel.textContent.trim() !== title) {
            nameLabel.textContent = title;
        }
        const hostLabel = document.getElementById(`thost-${index}`);
        const host = String(session.host || '');
        if (hostLabel && hostLabel.textContent.trim() !== host) {
            hostLabel.textContent = host;
        }
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
            updatePaneHeaderLayout(index);
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
                /* A frame-sliced Source build keeps appending rows into a
                   detached tree otherwise, competing for frames with the
                   group being attached in its place. It resumes on the way
                   back with its position and its queued readers intact. */
                explorerSuspendSourceRenderJob(terminal);
                releaseExplorerRepoSearch(terminal);
                /* The commit card is not suspended, it is closed: it floats on
                   document.body pinned to a row that is about to be detached,
                   so leaving it would hang one group's card over the group
                   arriving in its place. Only this pane's. */
                if (typeof explorerGitCommitCardPane === 'function'
                    && explorerGitCommitCardPane() === terminal) {
                    dismissExplorerGitCommitCard({ restoreFocus: false });
                }
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
                explorerResumeSourceRenderJob(terminal);
                restoreExplorerFileScroll(index, terminal._cachedExplorerScroll);
                resyncExplorerEditorOnAttach(index);
                /* A Git action that completed while this group was cached left
                   the slot alone on purpose — `terminals[index]` belonged to
                   the group that had replaced it — and marked its own pane for
                   the fresh load instead. This is where the pane is back on
                   screen, so this is where that load happens. */
                if (terminal._explorerGitReloadPending) {
                    terminal._explorerGitReloadPending = false;
                    if (terminal._explorerGitSidebarOpen) {
                        loadExplorerGitRepo(index);
                    }
                }
            }
            /* The cards were detached while another group was shown, so any
               voice stop that completed in that window addressed elements no
               document lookup could reach. Re-derive each mic from live
               capture state now they are back. */
            _syncVoiceControls(index);
            if (clearTerminalViewports) {
                terminal._cachedTerminalViewport = null;
            }
            terminal._cachedExplorerScroll = null;
        });
        _setVoiceBtnsDisabled(_voiceActiveIndex);
    }

    /* Which workspace this window is. Nothing else on screen says so once two
       windows are open, so it leads the session line and the window title. */
    let currentWorkspaceLabel = String(
        typeof CURRENT_WORKSPACE_LABEL !== 'undefined' ? CURRENT_WORKSPACE_LABEL : ''
    ).trim();

    function currentWorkspaceDisplayLabel() {
        return currentWorkspaceLabel
            || workspaceDisplayLabel({ workspace_id: currentWorkspaceId });
    }

    function setCurrentWorkspaceLabel(label) {
        currentWorkspaceLabel = String(label || '').trim();
        updateSessionChrome(terminals.length);
    }

    function updateSessionChrome(count, groupId = activeGroupId) {
        const activeGroup = getGroupById(groupId);
        const labelParts = [
            isMultiWorkspaceEnabled() ? `(${currentWorkspaceDisplayLabel()})` : '',
            activeGroup?.name || 'Session',
            `${count} terminal${count !== 1 ? 's' : ''}`,
            activeGroup?.connection_mode === 'wsl' ? 'Local Repo' : 'SSH'
        ].filter(Boolean);
        document.getElementById('sessionLabel').textContent = labelParts.join(' • ');
        const titleParts = [
            isMultiWorkspaceEnabled() ? currentWorkspaceDisplayLabel() : '',
            activeGroup?.name || ''
        ].filter(Boolean);
        document.title = titleParts.length
            ? `GridVibe — ${titleParts.join(' — ')}`
            : 'GridVibe — Terminals';
    }

    /* The session line has two shapes — the live grid's chrome and the empty
       state. Both are written from here so a *deferred* rewrite (the save
       confirmation handing the line back after its timer) can never disagree
       with what is actually on screen. */
    function renderSessionLine() {
        if (!terminals.length) {
            document.getElementById('sessionLabel').textContent =
                sessionGroups.length ? 'No terminals in this session' : 'No sessions';
            return;
        }
        updateSessionChrome(terminals.length);
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
        /* Enqueue the visible state before the cards leave the document: once
           the group is detached its panes can still be serialized, but the
           active tab's live view and theme can only be read from the DOM. */
        noteGroupPresentationChanged(groupId);
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

        (cached.sessionIds || []).forEach(cancelExplorerFilesystemUiForSession);
        (cached.sessionIds || []).forEach(forgetExplorerSessionMarkdownAppearance);
        workspaceSaveTargets.delete(groupId);
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
            /* Closed while suspended: the build will never resume. The pane
               is being discarded, not handed back, so the queue is dropped
               rather than flushed — global `terminals` belongs to the visible
               group here, and those readers re-read `terminals[index]`. */
            if (isExplorerPaneInstance(terminal)) {
                explorerReleasePaneWork(terminal);
            }
            if (isBrowserPaneInstance(terminal)) browserDisposePane(terminal);
            if (terminal?.term) {
                try { terminal.term.dispose(); } catch (_) {}
            }
        });
        cachedGroupViews.delete(groupId);
        presentationController()?.forgetGroup(groupId);
        releaseExplorerResourcesIfIdle();
    }

    /* Tell the backend which group this window has in front, so the workspace
       *autosave timer* can capture it — the timer has no window to ask, and
       without it only an explicit Save Workspace could record where to reopen.
       Fire-and-forget and change-only: a group switch is a user action, never a
       poll. A failure just leaves the restore with no group preference. */
    let reportedActiveGroupId = '';

    function reportActiveSessionGroup(groupId) {
        const normalized = String(groupId || '');
        if (!normalized || normalized === reportedActiveGroupId) {
            return;
        }
        reportedActiveGroupId = normalized;
        fetch('/api/session-groups/active', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                workspace_id: currentWorkspaceId,
                group_id: normalized
            })
        }).catch(() => {
            reportedActiveGroupId = '';
        });
    }

    function syncLocationToGroup(groupId) {
        const url = new URL(window.location.href);
        if (currentWorkspaceId !== 'default' || workspaceWasExplicit) {
            url.searchParams.set('workspace', currentWorkspaceId);
        }
        if (groupId) {
            url.searchParams.set('group', groupId);
        } else {
            url.searchParams.delete('group');
        }
        window.history.replaceState({}, '', url);
        reportActiveSessionGroup(groupId);
    }

    /* The palette and the hash live in `session-colour.js`, because the agent
       dashboard paints the same session on another page and a second copy of
       this mapping is how a card and its tab come to disagree. These two stay
       as the names this file already reads them by. */
    function tabColourForGroup(groupId) {
        return window.GridVibeSessionColour.sessionColour(groupId);
    }

    function hexToRgba(hex, alpha) {
        return window.GridVibeSessionColour.hexToRgba(hex, alpha);
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

    /* notifySavedSessionUpdated() lives in shared.js, beside the two channel
       names it writes to: a third page (the agent dashboard) now saves a
       preset too, and the launcher listens for every one of them. */

    /* ─────────────────────────────────────────────
       Multi-workspace: menus, move, window lifecycle

       This window's workspace identity, filtered loading, cache eviction and
       room joins live here; every workspace API call, destination list and
       window dispatch goes through workspaces.js (guardrail 6).
    ───────────────────────────────────────────── */

    /* Every in-app path that hands focus to another workspace window goes
       through here (guardrail 6), so leaving a workspace always drops this
       window's terminal focus first — the window-blur handler covers the rest
       (clicking another window directly). Without it the pane keeps real DOM
       focus while the window sits in the background and is focused again the
       moment the window comes back, leaving the user typing into the pane
       instead of driving the workspace shortcuts. */
    async function switchToWorkspaceWindow(workspaceId, options = {}) {
        dropTerminalFocusForWindowSwitch();
        /* In browser mode a workspace is a tab this page asks the browser to
           open, and the browser can refuse — one pop-up per user gesture, so a
           switch that had to fetch first may come back empty-handed. Say so
           here, once, for every switch path rather than leaving the user
           looking at a window that did not change. */
        const opened = await openWorkspaceWindow(workspaceId, options);
        if (!opened) {
            showTerminalToast(
                `The workspace tab could not be opened. ${WORKSPACE_TAB_BLOCKED_HINT}`,
                'error'
            );
        }
        return opened;
    }

    /* The Move list always acts on the active session tab, which the heading
       names — "Move Session to Workspace" alone reads as if it were about to
       move whatever the destination entry is. */
    function setMoveWorkspaceScopeLabel(groupId) {
        const group = getGroupById(groupId);
        const name = group ? (group.name || group.group_id) : '';
        const scope = document.getElementById('moveWorkspaceScope');
        if (scope) {
            scope.textContent = name ? `(${name})` : '';
            scope.hidden = !name;
        }
        document.getElementById('moveWorkspaceList')?.setAttribute(
            'aria-label',
            name ? `Move session ${name} to workspace` : 'Move session to workspace'
        );
    }

    async function refreshWorkspaceMenuLists() {
        if (!isMultiWorkspaceEnabled()) {
            return;
        }
        const openList = document.getElementById('openWorkspaceList');
        const moveList = document.getElementById('moveWorkspaceList');
        if (!openList && !moveList) {
            return;
        }

        const workspaces = await fetchLiveWorkspaces();
        const targetGroupId = getActiveWorkspaceGroupId();
        setMoveWorkspaceScopeLabel(targetGroupId);
        renderWorkspaceMenuList(
            openList,
            workspaces.map((workspace, index) => ({
                label: workspaceDisplayLabel(workspace, index),
                current: workspace.workspace_id === currentWorkspaceId,
                disabled: workspace.workspace_id === currentWorkspaceId,
                icon: workspace.workspace_id === currentWorkspaceId ? '' : WORKSPACE_ICONS.window,
                onSelect: () => {
                    closeSessionMenu();
                    switchToWorkspaceWindow(workspace.workspace_id, {
                        groupId: workspace.active_group_id
                    });
                }
            }))
        );

        const moveEntries = workspaces
            .filter(workspace => workspace.workspace_id !== currentWorkspaceId)
            .map((workspace, index) => ({
                label: workspaceDisplayLabel(workspace, index),
                icon: WORKSPACE_ICONS.move,
                disabled: !targetGroupId,
                onSelect: () => {
                    closeSessionMenu();
                    moveSessionGroupToWorkspace(targetGroupId, { workspaceId: workspace.workspace_id });
                }
            }));
        moveEntries.push({
            label: 'New workspace ...',
            icon: WORKSPACE_ICONS.add,
            disabled: !targetGroupId,
            onSelect: () => {
                closeSessionMenu();
                moveSessionGroupToNewWorkspace(targetGroupId);
            }
        });
        renderWorkspaceMenuList(moveList, moveEntries);
    }

    async function renameCurrentWorkspace() {
        const workspaces = await fetchLiveWorkspaces();
        const current = workspaces.find(workspace => workspace.workspace_id === currentWorkspaceId);
        const label = await openWorkspaceNameModal({
            title: 'Rename workspace',
            copy: 'The name is shown in workspace pickers and on the saved snapshot.',
            value: current?.label || '',
            confirmLabel: 'Rename'
        });
        if (label === null) {
            return;
        }
        try {
            const updated = await renameWorkspaceRecord(currentWorkspaceId, label);
            setCurrentWorkspaceLabel(workspaceDisplayLabel(updated));
            /* A rename changes no group, so it produces no room event: tell the
               launcher (and any other window) directly, or their pickers keep
               offering the old name until the next reload. */
            notifyWorkspacesChanged('renamed');
            setWorkspaceSaveMessage(`Workspace renamed to "${workspaceDisplayLabel(updated)}".`, 'success');
        } catch (error) {
            /* A taken name offers its remedy inline (open the live namesake /
               forget the saved one / pick another name) instead of just failing. */
            try {
                if (await resolveWorkspaceNameConflict(error)) {
                    return;
                }
            } catch (actionError) {
                setWorkspaceSaveMessage(`${actionError.message} — try again.`, 'error');
                return;
            }
            setWorkspaceSaveMessage(`Rename failed: ${error.message} — try again.`, 'error');
        }
    }

    async function createAndOpenWorkspace() {
        const label = await openWorkspaceNameModal({
            title: 'New workspace',
            copy: 'Opens an empty workspace window. Import a session or move a tab into it.',
            confirmLabel: 'Create'
        });
        if (label === null) {
            return;
        }
        try {
            const workspace = await createWorkspaceRecord(label);
            notifyWorkspacesChanged('created');
            await switchToWorkspaceWindow(workspace.workspace_id);
        } catch (error) {
            try {
                if (await resolveWorkspaceNameConflict(error)) {
                    return;
                }
            } catch (actionError) {
                setWorkspaceSaveMessage(`${actionError.message} — try again.`, 'error');
                return;
            }
            setWorkspaceSaveMessage(`Could not create the workspace: ${error.message}`, 'error');
        }
    }

    /* Closing the window never closes its sessions: the workspace stays live
       and can be reopened from the launcher.

       One exception, and it is a lifecycle rather than a close (MW-12):
       Workspace ▸ New Workspace reserves a deliberately empty record so
       cleanup cannot sweep it before its first tab arrives, and closing that
       window is the user saying the tab is never coming. Without the release
       an abandoned New Workspace stayed a zero-session launch destination
       until the process restarted. Nothing is live, so nothing is confirmed. */
    async function closeThisWorkspaceWindow() {
        logSessionWindowAction('Closing this workspace window on request');
        if (currentWorkspaceId !== WORKSPACE_DEFAULT_ID && sessionGroups.length === 0) {
            try {
                await closeLiveWorkspace(currentWorkspaceId);
            } catch (error) {
                console.error('[GridVibe Sessions] releasing the empty workspace failed:', error);
            }
        }
        await closeWorkspaceWindow(currentWorkspaceId);
    }

    /* Close live workspace: ends every session here and drops the workspace,
       while whatever autosave or Save Workspace captured stays on offer — the
       verb that closing the last tab does not give you, since that forgets the
       snapshot too. The window has nothing left to show afterwards. */
    async function closeCurrentWorkspace() {
        const workspaces = await fetchLiveWorkspaces();
        const current = workspaces.find(
            workspace => workspace.workspace_id === currentWorkspaceId
        ) || {
            workspace_id: currentWorkspaceId,
            label: currentWorkspaceLabel,
            group_count: sessionGroups.length
        };
        if (!(await confirmCloseLiveWorkspace(current))) {
            return;
        }
        try {
            await closeLiveWorkspace(currentWorkspaceId);
        } catch (error) {
            setWorkspaceSaveMessage(`Close failed: ${error.message} — try again.`, 'error');
            return;
        }
        workspaceGone = true;
        await _closeWindowAfterLastSession('Workspace closed');
    }

    /* This window can outlive its workspace: the last tab closed here, that
       tab's last pane closed, or the last tab pulled out by another window all
       empty a non-default workspace, which is then removed globally (§8). The
       window has nothing left to render and cannot reload — every workspace
       read answers `workspace_missing` — so it closes itself instead of
       lingering as a load error over a stale tab list. */
    let workspaceGone = false;

    async function handleWorkspaceGone() {
        if (workspaceGone) {
            return;
        }
        workspaceGone = true;
        await _closeWindowAfterLastSession('Workspace no longer exists');
    }

    async function moveSessionGroupToNewWorkspace(groupId) {
        const label = await openWorkspaceNameModal({
            title: 'Move to new workspace',
            copy: 'Creates a workspace, moves this session into it, and opens its window.',
            confirmLabel: 'Move'
        });
        if (label === null) {
            return;
        }
        await moveSessionGroupToWorkspace(groupId, { newWorkspace: true, label });
    }

    async function moveSessionGroupToWorkspace(groupId, target) {
        const movingGroupId = String(groupId || '');
        if (!movingGroupId) {
            return;
        }

        /* Moving evicts this window's cached view of the tab, which tears down
           its explorer editors — confirm unsaved buffers first, in page. */
        if (movingGroupId === visibleGroupId
            && !(await confirmDiscardAllExplorerEdits('Moving this session'))) {
            return;
        }

        try {
            const result = await moveGroupToWorkspace(movingGroupId, target);
            if (!result.moved) {
                showTerminalToast('That session is already in this workspace.', '');
                return;
            }

            /* The sessions themselves are untouched — same ids, same processes,
               same SSH connections, same per-session rooms. Only this window's
               cached view of the tab goes away. */
            if (movingGroupId === visibleGroupId) {
                teardownCurrentGrid();
            } else {
                dropCachedGroupView(movingGroupId);
            }
            if (activeGroupId === movingGroupId) {
                activeGroupId = '';
            }
            await switchToWorkspaceWindow(result.workspace_id, { groupId: movingGroupId });

            /* Moving the last group out empties this workspace: the record is
               already pruned server-side, so re-listing it would 400. Close
               this window instead — its sessions live on in the destination. */
            const sourceGroups = Array.isArray(result.source_groups) ? result.source_groups : [];
            if (!sourceGroups.length) {
                await _closeWindowAfterLastSession();
                return;
            }
            await loadSessionGroups();
            await initialLoad();
        } catch (error) {
            console.error('[GridVibe Sessions] move session failed:', error);
            showTerminalToast(`Move failed: ${error.message}`, 'error');
        }
    }

    async function openSessionTabContextMenu(event, groupId) {
        if (!isMultiWorkspaceEnabled()) {
            return;
        }
        event.preventDefault();
        const workspaces = await fetchLiveWorkspaces();
        const entries = workspaces
            .filter(workspace => workspace.workspace_id !== currentWorkspaceId)
            .map((workspace, index) => ({
                label: `Move to ${workspaceDisplayLabel(workspace, index)}`,
                icon: WORKSPACE_ICONS.move,
                onSelect: () => moveSessionGroupToWorkspace(groupId, {
                    workspaceId: workspace.workspace_id
                })
            }));
        entries.push({
            label: 'Move to new workspace ...',
            icon: WORKSPACE_ICONS.add,
            onSelect: () => moveSessionGroupToNewWorkspace(groupId)
        });
        openWorkspaceContextMenu(event, entries);
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

    /* The prompt itself — its markup, its three decisions and what it says —
       is close-session-modal.js, shared with the agent dashboard, which opens
       the same dialog over the same partial. What stays here is the one thing
       that is this page's: which group is being asked about.

       One misclick on a tab's × must not silently kill live terminals
       (sessions are memory-only), so closing a group with ≥1 connected
       terminal asks first, and offers to save the group as a preset on the
       way out. Dead groups close without the dialog. Resolves to one of the
       CLOSE_SESSION_* decisions. */
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

        const skipped = closeSessionPromptSkipDecision(sessions);
        if (skipped) {
            return skipped;
        }

        return openCloseSessionConfirmModal({
            group: getGroupById(groupId),
            connectedCount: closeSessionConnectedCount(sessions),
            totalCount: sessions.length
        });
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
            const startupMode = resolvePaneStartupMode(terminal);
            const {
                use_wsl: resolvedUseWsl,
                use_powershell: resolvedUsePowershell,
                ...paneLaunchFields
            } = buildPaneLaunchFields(terminal, startupMode);
            const resolvedDirectory = buildLaunchDirectory(
                configuredDefaultDir,
                terminal?.directory,
                connectionMode
            ) || launchDefaultDir;
            const common = {
                title: terminal?.title || `Terminal ${index + 1}`,
                directory: resolvedDirectory,
                ...paneLaunchFields
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
                use_wsl: resolvedUseWsl,
                use_powershell: resolvedUsePowershell
            });
        });

        if (!sessions.length) {
            throw new Error('Selected saved session does not contain any terminals.');
        }

        return {
            connection_mode: connectionMode,
            layout: config.layout || (terminalCount >= 4 ? 'grid' : (terminalCount <= 1 ? 'single' : 'vertical')),
            workspace_layout: config.workspace_layout || null,
            saved_session_id: savedSession.id,
            session_name: buildSavedSessionLaunchName(savedSession, config),
            // Import Session lands in *this* window's workspace, not globally.
            workspace_id: currentWorkspaceId,
            sessions
        };
    }

    /* A saved preset is live in at most one workspace at a time (plan §6). When
       the backend reports the conflict, offer the two honest resolutions rather
       than silently stealing the tab or minting a duplicate (guardrail 8). */
    async function resolveSavedSessionConflict(conflict) {
        const label = String(conflict.workspace_label || '').trim() || 'another workspace';
        const moveItHere = await openGenericConfirmModal({
            title: 'Already open elsewhere',
            copy: `This saved session is open in ${label}.`,
            note: 'Move it here, or cancel and open that workspace instead.',
            confirmLabel: 'Move it here'
        });
        if (!moveItHere) {
            await switchToWorkspaceWindow(conflict.workspace_id, { groupId: conflict.group_id });
            setWorkspaceSaveMessage(`Opened ${label}.`, '');
            return false;
        }
        await moveGroupToWorkspace(conflict.group_id, { workspaceId: currentWorkspaceId });
        return true;
    }

    /* One saved preset, credentials included. Returns null rather than throwing:
       every caller has a usable fallback and none of them should lose a launch
       to a failed re-read. */
    async function fetchSavedSession(savedSessionId) {
        const sessionId = String(savedSessionId || '').trim();
        if (!sessionId) {
            return null;
        }
        try {
            const response = await fetch(`/api/saved-sessions/${encodeURIComponent(sessionId)}`);
            if (!response.ok) {
                return null;
            }
            return await response.json();
        } catch (_error) {
            return null;
        }
    }

    async function launchSavedSession(savedSession) {
        const payload = buildSavedSessionLaunchPayload(savedSession);
        const postLaunch = () => fetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        let response = await postLaunch();
        let data = await response.json().catch(() => ({}));
        if (response.status === 409 && data.conflict === 'saved_session_live') {
            if (!(await resolveSavedSessionConflict(data))) {
                return;
            }
            response = await postLaunch();
            data = await response.json().catch(() => ({}));
        }
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
        /* Say so when the preset carries no password. SSH authentication runs
           after this response, so a credential-free launch otherwise reported
           itself as a clean success and only became visible as "No
           authentication methods available" in every pane a moment later, with
           nothing connecting the two. Not an error and never a block — key
           authentication is a legitimate way to have no stored password. */
        if (payload.connection_mode === 'ssh'
            && !payload.sessions.some(session => session.password)) {
            warnings.push('No password is saved for this session — panes will authenticate with an SSH key, or fail.');
        }
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
        if (!(event.target instanceof Element) || !event.target.closest('#sessionMenuRoot')) {
            closeSessionMenu();
        }
    });

    /* A press anywhere else closes the shortcut panel — including a press on
       any other top-bar control, which is outside the panel's own root and so
       needs no rule of its own. */
    document.addEventListener('click', event => {
        if (!(event.target instanceof Element) || !event.target.closest('#shortcutsHelpRoot')) {
            closeShortcutsHelp();
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

    /* The close prompt's own backdrop, buttons and Escape are wired by
       close-session-modal.js, over the same ids on both pages. */

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeSessionMenu();
            closeShortcutsHelp();
            topbarPeek.dismiss();
            if (document.getElementById('savedSessionsModal').classList.contains('visible')) {
                closeSavedSessionModal();
            }
            if (document.getElementById('saveSessionAsModal').classList.contains('visible')) {
                closeSaveSessionAsModal();
            }
        }
    });

    /* The save confirmation borrows the session line, which is otherwise only
       rewritten when the active tab changes. With a single tab nothing ever
       rewrote it, so the confirmation stayed in the window chrome for the rest
       of the session and read as part of the workspace name. It is a
       confirmation, not a state: hand the line back on a timer. */
    const WORKSPACE_SAVE_MESSAGE_MS = 6000;
    let workspaceSaveMessageTimer = null;

    /* One message, one surface, chosen when the message is raised.

       The status string lives in the top bar, which leaves the layout for the
       chevron or for fullscreen — so a message written there while the bar is
       out of the flow is a message nobody sees. It goes to the toast instead,
       which is visible either way.

       Routed on `topbar-hidden` (the flow) and deliberately not on
       `topbar-peek`: a message written into a bar that is only peeking leaves
       320 ms after the pointer does. And a live message never migrates — hiding
       the bar while a status string is up does not move it, and each surface
       dismisses on its own timer. */
    function setWorkspaceSaveMessage(message, type = '') {
        if (!message) {
            return;
        }
        if (document.body.classList.contains('topbar-hidden')) {
            showTerminalToast(message, type === 'error' || type === 'success' ? type : '');
            return;
        }
        const label = document.getElementById('sessionLabel');
        if (!label) {
            return;
        }
        label.textContent = message;
        label.dataset.workspaceSaveStatus = type;
        if (workspaceSaveMessageTimer) {
            clearTimeout(workspaceSaveMessageTimer);
        }
        workspaceSaveMessageTimer = setTimeout(
            clearWorkspaceSaveMessage,
            WORKSPACE_SAVE_MESSAGE_MS
        );
    }

    function clearWorkspaceSaveMessage() {
        if (workspaceSaveMessageTimer) {
            clearTimeout(workspaceSaveMessageTimer);
            workspaceSaveMessageTimer = null;
        }
        const label = document.getElementById('sessionLabel');
        if (label) {
            delete label.dataset.workspaceSaveStatus;
        }
        renderSessionLine();
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

    /* ─────────────────────────────────────────────
       Live presentation synchronisation

       One ordered compare-and-swap transaction per group carries every
       browser-owned presentation field to the manager, which is what autosave
       and Save Workspace actually serialize. The queue itself is DOM-free and
       lives in session-persistence.js; everything below is the adapter that
       describes a group from the live DOM, plus the explicit flush barrier
       Save Workspace (and, later, the lifecycle transaction) awaits.
    ───────────────────────────────────────────── */

    /* The pane's light/dark explorer theme as it is on screen right now: the
       card's dataset while mounted, the value folded into the pane object when
       its group was cached, then whatever the session was launched with. */
    function explorerPaneLiveTheme(terminal, index) {
        const card = index >= 0 ? document.getElementById(`tc-${index}`) : null;
        return normalizeExplorerTheme(
            card?.dataset.explorerTheme
            || terminal?._cachedExplorerTheme
            || terminal?._session?.explorer_theme
            || 'dark'
        );
    }

    /* Custom split geometry only. A standard layout's rectangles are derived
       from its layout name, so synthesising a split snapshot for one would
       replace a plain grid with an equivalent-looking custom split on restore
       for no gain. Omitting the field leaves the stored geometry untouched. */
    function customSplitLayoutSnapshot(groupId) {
        const isVisible = visibleGroupId === groupId && gridBuilt;
        const className = isVisible
            ? document.getElementById('terminalsGrid')?.className
            : cachedGroupViews.get(groupId)?.className;
        return className === 'layout-split-local'
            ? buildActiveWorkspaceLayoutSnapshot(groupId)
            : null;
    }

    function describePanePresentation(terminal) {
        const session = terminal?._session || {};
        const sessionId = session.session_id || '';
        const index = terminals.indexOf(terminal);

        /* Keyed off the session's own startup_mode, not the rendered pane type:
           the server decides which presentation fields a pane may carry, and
           during a mode switch the two disagree for a moment. */
        if (isExplorerSession(session)) {
            if (index !== -1) {
                /* Fold the shown tab's live mode + scroll into its record so the
                   batch reflects what is on screen right now. A cached group was
                   already folded in by cacheVisibleGroupView(). */
                explorerCaptureActiveTabView(index);
            }
            const tabs = explorerSerializeTabs(terminal);
            /* The pane object, not just its slot: this describes cached groups
               too, and a detached pane has no slot in `terminals`. */
            const sidebar = explorerSidebarPresentation(index, terminal);
            const pin = panePinDescriptor(terminal);
            return {
                sessionId,
                mode: 'explorer',
                explorer: {
                    treeOpen: Boolean(terminal?._explorerTreeSidebarOpen),
                    gitOpen: Boolean(terminal?._explorerGitSidebarOpen),
                    gitFollowBrowsing: Boolean(terminal?._explorerGitFollowBrowsing),
                    gitPinActive: pin.active,
                    gitPinnedPath: pin.path,
                    gitPinKind: pin.kind,
                    searchOpen: Boolean(terminal?._explorerSearchSidebarOpen),
                    sidebarWidth: sidebar.width,
                    sidebarScroll: sidebar.scroll,
                    treeExpanded: sidebar.expanded,
                    gitExpanded: sidebar.gitExpanded,
                    openTabs: tabs.open_tabs,
                    activeTab: tabs.active_tab,
                    tabViews: tabs.tab_views,
                    theme: explorerPaneLiveTheme(terminal, index)
                }
            };
        }

        if (isBrowserSession(session)) {
            const strip = browserSerializeTabs(terminal, session);
            return {
                sessionId,
                mode: 'browser',
                browser: { tabs: strip.tabs, activeTab: strip.active_tab }
            };
        }

        return { sessionId, mode: 'terminal' };
    }

    function describeGroupPresentation(groupId) {
        const group = getGroupById(groupId);
        if (!group) {
            return null;
        }
        const panes = getWorkspacePanesInVisualOrder(groupId);
        if (!panes.length) {
            /* A tab this window has never rendered has no browser-owned state
               to contribute; the manager's own copy stays authoritative. */
            return null;
        }
        const descriptor = {
            workspaceId: currentWorkspaceId,
            groupId,
            revision: Number.isInteger(group.presentation_revision)
                ? group.presentation_revision
                : 0,
            panes: panes.map(describePanePresentation)
        };
        const geometry = customSplitLayoutSnapshot(groupId);
        if (geometry) {
            descriptor.workspaceLayout = geometry;
        }
        return descriptor;
    }

    function postPresentation(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
    }

    function presentationController() {
        if (!presentationSyncBuilt) {
            presentationSyncBuilt = true;
            const factory = window.GridVibeSessionPersistence;
            presentationSync = factory
                ? factory.createPresentationController({
                    describeGroup: describeGroupPresentation,
                    describeWorkspace: () => {
                        const appearance = explorerMarkdownAppearance();
                        return {
                            workspaceId: currentWorkspaceId,
                            revision: workspacePresentationRevision,
                            topbarVisible: !document.body.classList.contains('topbar-collapsed'),
                            mdPreset: appearance.preset,
                            mdFont: appearance.font,
                            sourceFont: appearance.sourceFont
                        };
                    },
                    sendGroup: payload => postPresentation('/api/session-presentation', payload),
                    sendWorkspace: payload => postPresentation('/api/workspace-presentation', payload),
                    onError: (scope, error, id) => {
                        console.error(
                            `[GridVibe Sessions] ${scope} presentation sync failed`,
                            id || currentWorkspaceId,
                            error
                        );
                    }
                })
                : null;
        }
        return presentationSync;
    }

    function noteGroupPresentationChanged(groupId, options) {
        const controller = presentationController();
        return controller ? controller.noteGroupChange(groupId, options) : false;
    }

    /* Called from the explorer and browser modules for a pane addressed by its
       grid slot. */
    function notePanePresentationChanged(index, options) {
        const pane = terminals[index];
        if (!pane) {
            return false;
        }
        /* Guardrail 4: a grid slot is not an identity. The pane's own session
           names the group its presentation belongs to; `visibleGroupId` only
           says where the grid is currently pointing, and an answer landing
           during a group switch would bump the incoming group's revision while
           leaving the pane's own group unwritten. They are the same id in
           every ordinary case — this is the one that is not ordinary. */
        const groupId = String(pane._session?.group_id || '').trim()
            || visibleGroupId
            || activeGroupId;
        return noteGroupPresentationChanged(groupId, options);
    }

    function noteWorkspacePresentationChanged(options) {
        const controller = presentationController();
        return controller ? controller.noteWorkspaceChange(options) : false;
    }

    /* Markdown/source appearance is one ordered workspace presentation value. */
    function noteExplorerAppearanceChanged() {
        return noteWorkspacePresentationChanged();
    }

    function adoptPresentationRevisions(payload) {
        const controller = presentationController();
        if (!controller) {
            return;
        }
        if (Number.isInteger(payload?.workspace_presentation_revision)) {
            workspacePresentationRevision = payload.workspace_presentation_revision;
            controller.setWorkspaceRevision(workspacePresentationRevision);
        }
        (Array.isArray(payload?.groups) ? payload.groups : []).forEach(group => {
            controller.setGroupRevision(group.group_id, group.presentation_revision);
        });
    }

    /* The exact-save barrier. Every group this window can describe is
       re-captured and acknowledged before the caller asks the server to write a
       snapshot, so Save Workspace records the screen rather than the last thing
       the server happened to hear. Exposed on `window` because Stage 4's
       lifecycle transaction must reuse this one barrier, not re-derive it. */
    async function flushLivePresentation() {
        const controller = presentationController();
        if (!controller) {
            return { ok: true, flushed: [] };
        }
        const groupIds = sessionGroups
            .map(group => group.group_id)
            .filter(groupId => groupId === visibleGroupId || cachedGroupViews.has(groupId));
        try {
            return { ok: true, flushed: await controller.flush(groupIds) };
        } catch (error) {
            return { ok: false, error };
        }
    }

    window.gridvibeFlushLivePresentation = flushLivePresentation;

    /* The Git pin pair travels across a rebuild at five points on this page.
       `session-persistence.js` owns the mapping in both directions — it is
       loaded ahead of this file and is a hard requirement of the page, so
       these read it directly rather than degrading into a private copy of the
       rule, which is exactly the drift the extraction removes. */
    function panePinDescriptor(pane) {
        return window.GridVibeSessionPersistence.explorerGitPinDescriptor(pane);
    }

    function sessionPinnedPath(session) {
        return window.GridVibeSessionPersistence.explorerGitPinnedPathFromSession(session);
    }

    function sessionPinKind(session) {
        return window.GridVibeSessionPersistence.explorerGitPinKindFromSession(session);
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
        /* Where the pane *is*, not where it started: `current_directory` is the
           observed value (null until something actually observed it), and a
           saved preset that replays the launch directory is what brought an
           agent back in the wrong place.

           An explorer pane used to answer with its *root* here, so one saved
           field meant two different things depending on the pane's mode and a
           pane rooted wider than the folder it was showing could not state
           both. The root now rides in its own field beside the flag that says
           whether anybody chose it, and `directory` means the same thing in
           every mode. */
        const selectedDirectory = session.current_directory || session.directory || '';
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
        const explorerSidebar = startupMode === 'explorer' && terminal
            ? explorerSidebarPresentation(explorerSlot, terminal)
            : {
                width: Number(session.explorer_sidebar_width) || 260,
                scroll: session.explorer_sidebar_scroll || {},
                expanded: session.explorer_tree_expanded || [],
                gitExpanded: session.explorer_git_expanded || []
            };
        const browserTabs = startupMode === 'browser'
            ? browserSerializeTabs(terminal, session)
            : { tabs: [], active_tab: 0 };
        /* Persist the pane's live light/dark explorer theme so a saved session
           relaunches with the same appearance (its localStorage override is
           keyed by session_id and won't survive new session ids). */
        const explorerTheme = startupMode === 'explorer'
            ? explorerPaneLiveTheme(terminal, explorerSlot)
            : '';
        const explorerPin = panePinDescriptor(terminal);

        return {
            /* Request-only identity: the backend strips this before persisting
               the preset and uses it to refresh this exact live pane's view
               state for a later launcher reopen. */
            session_id: session.session_id || '',
            title: session.title || `Terminal ${index + 1}`,
            directory: selectedDirectory,
            /* Saved for explorer panes only, and saved exactly: the relaunch
               replays this root rather than deriving one again, and an
               explicit `false` keeps a derived root from coming back as a pin
               on a directory nobody picked. */
            explorer_root_directory: startupMode === 'explorer'
                ? (session.explorer_root_directory || '')
                : '',
            explorer_root_configured: startupMode === 'explorer'
                && Boolean(session.explorer_root_configured),
            initial_command: startupMode === 'explorer' ? '' : (session.initial_command || ''),
            initial_command_mode: commandMode,
            startup_mode: startupMode,
            agent_selection: commandMode === 'agent' ? (session.agent_selection || '') : '',
            custom_agent: commandMode === 'agent' ? (session.custom_agent || '') : '',
            agent_auto_mode: commandMode === 'agent' ? Boolean(session.agent_auto_mode) : false,
            explorer_tree_open: startupMode === 'explorer' ? Boolean(terminal?._explorerTreeSidebarOpen) : false,
            explorer_git_open: startupMode === 'explorer' ? Boolean(terminal?._explorerGitSidebarOpen) : false,
            explorer_git_follow_browsing: startupMode === 'explorer'
                ? Boolean(terminal?._explorerGitFollowBrowsing)
                : false,
            explorer_git_pin_active: startupMode === 'explorer' && explorerPin.active,
            explorer_git_pinned_path: startupMode === 'explorer' ? explorerPin.path : '',
            explorer_git_pin_kind: startupMode === 'explorer' ? explorerPin.kind : 'dir',
            explorer_search_open: startupMode === 'explorer' ? Boolean(terminal?._explorerSearchSidebarOpen) : false,
            explorer_sidebar_width: explorerSidebar.width,
            explorer_sidebar_scroll: explorerSidebar.scroll,
            explorer_tree_expanded: explorerSidebar.expanded,
            explorer_git_expanded: explorerSidebar.gitExpanded,
            explorer_open_tabs: explorerTabs.open_tabs,
            explorer_active_tab: explorerTabs.active_tab,
            explorer_tab_views: explorerTabs.tab_views,
            explorer_md_preset: mdAppearance ? mdAppearance.preset : '',
            explorer_md_font: mdAppearance ? mdAppearance.font : '',
            explorer_source_font: mdAppearance ? mdAppearance.sourceFont : '',
            explorer_theme: explorerTheme,
            /* Save Workspace captures the pane's whole live tab strip, not just
               the URL it launched with, so a saved browser pane reopens every
               tab that was on screen with the same one selected. */
            browser_tabs: browserTabs.tabs,
            browser_active_tab: browserTabs.active_tab,
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
        /* Step 2's default folder is launcher setup, not a pane location: the
           first pane's explorer boundary when it has one, and where that pane
           is otherwise. The same rule the server-side exit save applies. */
        const firstDirectory = firstSession.explorer_root_directory
            || firstSession.current_directory
            || firstSession.directory
            || '';

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
                /* Read the preset back before launching it. A workspace save
                   resolves the SSH password server-side and deliberately does
                   not echo it, so launching straight from this response would
                   open every pane without the credential it was just saved
                   with. `GET /api/saved-sessions/<id>` is the same fetch the
                   New Session picker uses. */
                await launchSavedSession(await fetchSavedSession(data.id) || data);
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

    async function getCurrentWorkspaceNativeZoomFactor() {
        const api = window.pywebview?.api;
        if (api?.get_workspace_native_zoom) {
            try {
                const result = await api.get_workspace_native_zoom(currentWorkspaceId);
                if (result?.ok) {
                    return normalizeNativeZoomFactor(result.zoom_factor);
                }
            } catch (_) {}
        }
        return getNativeSessionZoomFactor();
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
            /* Barrier first: capture every group this window owns and wait for
               the manager to acknowledge it, so the capture below serializes
               the screen instead of the last state the server happened to hear.
               A failed flush is a failed save — never a success toast over a
               snapshot that is missing the change the user just made. */
            const flushed = await flushLivePresentation();
            if (!flushed.ok) {
                throw new Error(
                    flushed.error?.message || 'Live pane state could not be synchronised'
                );
            }
            const nativeZoomFactor = await getCurrentWorkspaceNativeZoomFactor();
            const response = await fetch('/api/runtime-state/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Name the group this window is on, so the restore reopens here;
                // desktop mode also carries the session window's current zoom.
                body: JSON.stringify({
                    workspace_id: currentWorkspaceId,
                    active_group_id: activeGroupId,
                    native_zoom_factor: nativeZoomFactor,
                    topbar_visible: !document.body.classList.contains('topbar-collapsed')
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Save failed with status ${response.status}`);
            }
            /* Refresh the launcher's live-workspace row while this window is
               still open, so its Open action carries the just-saved front tab. */
            notifyWorkspacesChanged('workspace_saved');
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
        syncSessionMenuState();
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

            /* Middle-click closes the session, matching the explorer tab strip
               (and every tabbed UI). Same path as the ×, so the live-terminal
               confirmation still applies. */
            button.addEventListener('mousedown', event => {
                if (event.button === 1) {
                    event.preventDefault(); // suppress middle-click autoscroll
                }
            });
            button.addEventListener('auxclick', event => {
                if (event.button === 1) {
                    event.preventDefault();
                    closeSessionGroup(group.group_id);
                }
            });

            /* Right-click offers the same moveGroupToWorkspace() call as the
               Workspace ▸ Move Session to Workspace menu. */
            button.addEventListener('contextmenu', event => {
                openSessionTabContextMenu(event, group.group_id);
            });

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
            body: JSON.stringify({
                workspace_id: currentWorkspaceId,
                group_ids: groupIds
            })
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
        /* Settle, not drag: the pointer stream itself writes nothing. */
        noteGroupPresentationChanged(visibleGroupId);
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
        const unit = SPLIT_CELL_UNIT;
        originalSplitSlotCount = Math.max(originalSplitSlotCount, count);
        if (count === 1) {
            return [makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 2 * unit, h: unit })];
        }
        if (count === 2 && layoutClass.includes('horizontal')) {
            return [
                makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: 2 * unit, h: unit }),
                makeSplitLeaf({ originSlot: 1, x: 1, y: 1 + unit, w: 2 * unit, h: unit }),
            ];
        }
        if (count === 2) {
            return [
                makeSplitLeaf({ originSlot: 0, x: 1, y: 1, w: unit, h: unit }),
                makeSplitLeaf({ originSlot: 1, x: 1 + unit, y: 1, w: unit, h: unit }),
            ];
        }

        const base = getBaseLayoutSlots(count, layoutClass);
        return base.slots.map((slot, index) => makeSplitLeaf({
            originSlot: index,
            x: 1 + (slot.col - 1) * unit,
            y: 1 + (slot.row - 1) * unit,
            w: slot.colSpan * unit,
            h: slot.rowSpan * unit,
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
        if (window.innerWidth <= 700 || terminals.length >= MAX_SPLIT_TERMINALS) {
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

    function splitSlotRect(rect, axis) {
        /* A split produces two plain, independent leaves — no shared splitId or
           ancestor chain. Each new pane behaves like any other grid cell: it can
           be split again until it hits the minimum size, and closing it reflows
           its neighbours through the normal terminal-close path. */
        if (axis === 'vertical') {
            const firstWidth = Math.floor(rect.w / 2);
            return [
                makeSplitLeaf({ originSlot: rect.originSlot, x: rect.x, y: rect.y, w: firstWidth, h: rect.h }),
                makeSplitLeaf({ originSlot: rect.originSlot, x: rect.x + firstWidth, y: rect.y, w: rect.w - firstWidth, h: rect.h }),
            ];
        }

        const firstHeight = Math.floor(rect.h / 2);
        return [
            makeSplitLeaf({ originSlot: rect.originSlot, x: rect.x, y: rect.y, w: rect.w, h: firstHeight }),
            makeSplitLeaf({ originSlot: rect.originSlot, x: rect.x, y: rect.y + firstHeight, w: rect.w, h: rect.h - firstHeight }),
        ];
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

    function applySplitButtonState(button, enabled, activeTitle, disabledReason) {
        if (!button) {
            return;
        }
        button.hidden = false;
        button.disabled = !enabled;
        button.title = enabled ? activeTitle : disabledReason;
        button.setAttribute('aria-label', button.title);
    }

    function getSplitDisabledReason(axis) {
        if (window.innerWidth <= 700) {
            return 'Splitting is disabled on narrow screens';
        }
        if (terminals.length >= MAX_SPLIT_TERMINALS) {
            return `Splitting is limited to ${MAX_SPLIT_TERMINALS} terminal panes`;
        }
        return axis === 'horizontal'
            ? `Stacked split needs at least ${MIN_SPLIT_ROWS} rows below each terminal header`
            : `Side-by-side split needs at least ${MIN_SPLIT_COLS} columns in each terminal`;
    }

    /* Explorer and browser panes split into a plain terminal rather than a copy
       of themselves, so their controls say what they actually produce. */
    function splitButtonTitles(session) {
        if (isExplorerSession(session) || isBrowserSession(session)) {
            return {
                vertical: 'Split off a terminal beside this pane',
                horizontal: 'Split off a terminal below this pane'
            };
        }
        return {
            vertical: 'Split into side-by-side panes',
            horizontal: 'Split into stacked panes'
        };
    }

    function updateSplitButtonState(index) {
        const vButton = document.getElementById(`tsplitv-${index}`);
        const hButton = document.getElementById(`tsplith-${index}`);
        if (!vButton && !hButton) {
            return;
        }

        const titles = splitButtonTitles(terminals[index]?._session);
        const grid = document.getElementById('terminalsGrid');
        const card = document.getElementById(`tc-${index}`);
        const visualIndex = grid && card ? Array.from(grid.children).indexOf(card) : -1;
        const rects = visualIndex >= 0 ? ensureSplitSlotRects() : [];
        const rect = rects[visualIndex];
        const candidates = rect ? getSplitCandidates(index, rect) : [];

        applySplitButtonState(
            vButton,
            candidates.includes('vertical'),
            titles.vertical,
            getSplitDisabledReason('vertical')
        );
        applySplitButtonState(
            hButton,
            candidates.includes('horizontal'),
            titles.horizontal,
            getSplitDisabledReason('horizontal')
        );
    }

    function updateAllSplitButtonStates() {
        terminals.forEach((_, index) => {
            updateSplitButtonState(index);
            updatePaneHeaderLayout(index);
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
        attachTerminalKeyEventHandler(term);
        return { term, fitAddon, searchAddon };
    }

    /* xterm keeps exactly ONE custom key event handler per terminal, so every
       key rule has to live in this single install — a second
       `attachCustomKeyEventHandler` call silently replaces the first, which is
       how the clipboard wiring used to drop the shortcut pass-throughs below.

       Returning false means "xterm must not act on this key". That matters for
       more than the shell: xterm cancels the keys it claims with
       `preventDefault()` *and* `stopPropagation()`, so a key it handles never
       reaches the document-level shortcut listeners at all. */
    function paneIndexForTerminal(term) {
        return terminals.findIndex(pane => pane?.term === term);
    }

    function attachTerminalKeyEventHandler(term) {
        term.attachCustomKeyEventHandler(event => {
            if (event.type !== 'keydown') {
                return true;
            }

            /* Hand Ctrl+Shift+F to the document-level search-overlay shortcut
               instead of letting xterm swallow it. */
            if ((event.ctrlKey || event.metaKey)
                && event.shiftKey
                && !event.altKey
                && event.code === 'KeyF') {
                return false;
            }
            /* Same for Alt+W (workspace switch): xterm would send it on to the
               shell as ESC w, so hand it to the document handler instead. */
            if (event.altKey
                && !event.ctrlKey
                && !event.metaKey
                && event.code === 'KeyW') {
                return false;
            }
            /* And for Alt+Q (open launcher), which xterm would otherwise send
               on to the shell as ESC q. */
            if (event.altKey
                && !event.ctrlKey
                && !event.metaKey
                && !event.shiftKey
                && event.code === 'KeyQ') {
                return false;
            }

            /* Ctrl+Shift+C → copy selection */
            if (event.ctrlKey && event.shiftKey && event.code === 'KeyC') {
                _copyText(term.getSelection());
                return false;
            }

            /* Ctrl+V → paste from clipboard */
            if (event.ctrlKey && !event.shiftKey && event.code === 'KeyV') {
                const index = paneIndexForTerminal(term);
                if (index !== -1) {
                    _pasteToTerminal(index);
                }
                return false;
            }

            return true;
        });
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

    /* Take the hold, and get back the release bound to the buttons it just
       disabled. Refresh and Clear both await, and a group switch inside that
       wait hands `trefresh-<index>` to whichever pane took the slot: releasing
       by index there clears somebody else's busy state and leaves the pane
       that asked stuck on “Refreshing…” inside its cached fragment, which is
       exactly where its own card went. The nodes are the identity. */
    function holdTerminalActionState(index, action) {
        const buttons = {
            refreshButton: document.getElementById(`trefresh-${index}`),
            explorerRefreshButton: document.getElementById(`explorer-refresh-${index}`),
            clearButton: document.getElementById(`tclear-${index}`)
        };
        applyTerminalActionState(buttons, action);
        return () => applyTerminalActionState(buttons, '');
    }

    function applyTerminalActionState(buttons, action = '') {
        const { refreshButton, explorerRefreshButton, clearButton } = buttons || {};
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

    /* The clear button also types a real clear command so the shell's own
       scrollback goes with the pane's. Only cmd.exe and PowerShell understand
       `cls`; every POSIX shell wants `clear`. A local ("wsl" mode) pane with
       neither shell flag set is the host's default shell, which is cmd.exe on a
       Windows host but bash/zsh on a Linux or macOS one — hence the host check,
       without which a Linux host answered the clear button with
       "Command 'cls' not found". */
    function getTerminalClearCommand(index) {
        const session = terminals[index]?._session;
        if (session?.mode !== 'wsl') {
            return 'clear\r';
        }
        if (session?.use_powershell) {
            return 'cls\r';
        }
        if (session?.use_wsl) {
            return 'clear\r';
        }
        return localShellModesAvailable() ? 'cls\r' : 'clear\r';
    }

    function flushPendingOutput(index) {
        flushCapturedPendingOutput(terminals[index]);
    }

    /* Same flush, addressed to a pane object rather than to a grid slot, for
       the asynchronous callers that captured one before they awaited. */
    function flushCapturedPendingOutput(terminal) {
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

    async function ensureTerminalReady(index, maxAttempts = 12, isCurrent = null) {
        const terminal = terminals[index];
        if (!terminal?._attached) {
            return false;
        }
        const capturedIsCurrent = typeof isCurrent === 'function' ? isCurrent : () => true;
        return GridVibeTerminalModes.waitForCurrentPaneReady({
            maxAttempts,
            isCurrent: () => terminals[index] === terminal && capturedIsCurrent(),
            attempt: () => fitTerminal(index),
            wait: () => waitForAnimationFrames(1),
            ready: () => Boolean(terminal._fitReady)
        });
    }

    async function ensureAttachedTerminalsReady(indices) {
        /* Explorer and browser panes can be handed in (splitting one produces a
           terminal beside it); they have no xterm to fit, and without the `term`
           guard each would burn the full retry budget failing fitTerminal. */
        const uniqueIndices = [...new Set(indices)]
            .filter(index => Number.isInteger(index) && terminals[index]?._attached && terminals[index]?.term);
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

    /* A pane whose TUI died without unwinding keeps that program's mouse
       reporting armed, and the shell that inherits the prompt gets every
       pointer movement typed at it. GridVibeTerminalModes owns the teardown
       and, for the replaying path, the ordering it has to land in; the page
       owns only the pane it lands on. */
    function terminalModeResetTarget(index) {
        return GridVibeTerminalModes.captureResetTarget({
            pane: terminals[index],
            sessionId: sessionIds[index],
            /* Anything already queued behind a not-yet-fitted pane — the
               replay included — has to be applied first, or the teardown would
               be overwritten by the very bytes it exists to undo. */
            flush: pane => flushCapturedPendingOutput(pane),
            write: (pane, data) => {
                if (pane?.term) {
                    pane.term.write(data);
                }
            },
            currentPane: () => terminals[index],
            currentSessionId: () => sessionIds[index]
        });
    }

    async function refreshTerminalDisplay(index) {
        const terminal = terminals[index];
        const sessionId = sessionIds[index];
        if (!terminal) {
            return false;
        }

        /* Everything below the first await addresses this capture, not the
           slot: a group switch during the rejoin puts another pane in
           `terminals[index]`, and the teardown is owed to the pane that asked
           for it. */
        const resetTarget = terminalModeResetTarget(index);
        const resetTargetIsCurrent = () => resetTarget.isCurrent();
        const releaseBusy = holdTerminalActionState(index, 'refresh');
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
                const ready = await ensureTerminalReady(index, 12, resetTargetIsCurrent);
                if (ready && resetTargetIsCurrent()) {
                    emitTerminalResize(index, true);
                }
            } else {
                attachTerminal(index);
                await ensureTerminalReady(index, 12, resetTargetIsCurrent);
            }

            if (sessionId && socket) {
                /* The rejoin is what replays the server's rolling buffer, and
                   that buffer still holds a dead TUI's `?1003h`. The mode
                   teardown therefore has to be written *after* the replayed
                   bytes land, not after term.reset() — hence the ack-sequenced
                   rejoin rather than two bare emits. */
                await GridVibeTerminalModes.rejoinAndResetAfterReplay({
                    sessionId,
                    emit: (event, payload, ack) => (
                        ack ? socket.emit(event, payload, ack) : socket.emit(event, payload)
                    ),
                    write: data => resetTarget.write(data),
                    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
                    clearTimeout: handle => window.clearTimeout(handle)
                });
                /* The redraw is slot work: it fits and repaints whatever is in
                   `index` now. Never the incoming group, for a reset that
                   belonged to the group it replaced. */
                await redrawAttachedTerminals([index], {
                    forceResize: true,
                    isCurrent: resetTargetIsCurrent
                });
                return false;
            }

            /* No socket, so no replay to wait behind. */
            GridVibeTerminalModes.resetMouseReporting(data => resetTarget.write(data));

            if (terminal._attached && terminal.term.rows > 0) {
                terminal.term.refresh(0, terminal.term.rows - 1);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] refreshTerminalDisplay failed:', error);
        } finally {
            releaseBusy();
        }

        return false;
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

        /* Same capture rule as Reset view above: Clear awaits too, so the
           release has to reach the buttons it disabled rather than whatever is
           in the slot by then. */
        const resetTarget = terminalModeResetTarget(index);
        const resetTargetIsCurrent = () => resetTarget.isCurrent();
        const clearCommand = getTerminalClearCommand(index);
        const releaseBusy = holdTerminalActionState(index, 'clear');
        try {
            logSessionWindowAction('Clearing terminal display', {
                index,
                session_id: sessionId || null,
                attached: Boolean(terminal._attached)
            });

            terminal._pendingOutput = '';
            terminal.term.reset();
            terminal.term.clear();
            /* Clear purges the replay buffer below, so nothing can re-arm what
               the reset cleared and the teardown needs no ordering of its own.
               It is written all the same: the cure is named here rather than
               left as a side effect of term.reset()'s scope. */
            GridVibeTerminalModes.resetMouseReporting(data => resetTarget.write(data));

            if (terminal._attached) {
                const ready = await ensureTerminalReady(index, 12, resetTargetIsCurrent);
                if (ready && resetTargetIsCurrent()) {
                    emitTerminalResize(index, true);
                }
            }

            if (sessionId && socket && terminal._session?.status === 'connected') {
                socket.emit('clear_terminal_buffer', { session_id: sessionId });
                socket.emit('terminal_input', { session_id: sessionId, data: clearCommand });
            } else if (sessionId && socket) {
                socket.emit('clear_terminal_buffer', { session_id: sessionId });
            }
        } catch (error) {
            console.error('[GridVibe Sessions] clearTerminalDisplay failed:', error);
        } finally {
            releaseBusy();
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
        /* A drop settles the visual order; the server enumerates panes by the
           order it was last told, so tell it now rather than at save time. */
        noteGroupPresentationChanged(visibleGroupId);
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
        resetFocusedTerminal();
        sessionIds.forEach(cancelExplorerFilesystemUiForSession);
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
            /* Every close and every non-caching group switch lands here, so
               this is where an explorer pane's outstanding work is given
               back: a frame-sliced Source build would otherwise keep
               appending rows into a detached tree, and its queued readers
               would run against whatever fills the slot next. */
            if (isExplorerPaneInstance(t)) {
                explorerReleasePaneWork(t);
            }
            if (isBrowserPaneInstance(t)) browserDisposePane(t);
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
        visibleGroupId = '';
        releaseExplorerResourcesIfIdle();
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
                _explorerGitSidebarOpen: Boolean(session.explorer_git_open),
                _explorerGitFollowBrowsing: Boolean(session.explorer_git_follow_browsing),
                _explorerGitPinnedPath: sessionPinnedPath(session),
                _explorerGitPinKind: sessionPinKind(session),
                _explorerPath: explorerInitialPreviewDirectory(session),
                _explorerSearchSidebarOpen: Boolean(session.explorer_search_open),
                _explorerSidebarWidth: Number(session.explorer_sidebar_width) || 260,
                _explorerSidebarScroll: session.explorer_sidebar_scroll || {},
                _explorerTreeExpanded: new Set(
                    Array.isArray(session.explorer_tree_expanded)
                        ? session.explorer_tree_expanded
                        : []
                ),
                _explorerDiffExpandedCommits: new Set(
                    Array.isArray(session.explorer_git_expanded)
                        ? session.explorer_git_expanded
                        : []
                )
            };
        }
        if (isBrowserSession(session)) {
            return browserEnsureTabState(
                { _session: session, _paneType: 'browser', _attached: false },
                session
            );
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

    /* Wire the two split controls to their explicit axes and the overflow toggle.
       Shared by the initial pane build, split-clone cards, and mode switches. */
    function wireSplitButtons(card, index) {
        wireCardButton(card, `[data-terminal-split-v="${index}"]`, () => splitTerminalPane(index, 'vertical'));
        wireCardButton(card, `[data-terminal-split-h="${index}"]`, () => splitTerminalPane(index, 'horizontal'));
    }

    function wirePaneMoreButton(card, index) {
        wireCardButton(card, `[data-terminal-actions-more="${index}"]`, () => togglePaneActionsMenu(index));
        /* Selecting any folded action closes the overflow menu. Capture phase so
           it still runs even though the button handlers stopPropagation. */
        const actions = card.querySelector(`#tactions-${index}`);
        if (actions) {
            actions.addEventListener('click', event => {
                if (event.target.closest('.terminal-action-btn')) {
                    closeAllPaneActionMenus();
                }
            }, true);
        }
    }

    function closeAllPaneActionMenus(exceptIndex = -1) {
        document.querySelectorAll('.terminal-container.actions-open').forEach(card => {
            if (card.id === `tc-${exceptIndex}`) {
                return;
            }
            card.classList.remove('actions-open');
            const more = card.querySelector('.terminal-actions-more-btn');
            more?.setAttribute('aria-expanded', 'false');
        });
    }

    function togglePaneActionsMenu(index) {
        const card = document.getElementById(`tc-${index}`);
        if (!card) {
            return;
        }
        const willOpen = !card.classList.contains('actions-open');
        closeAllPaneActionMenus(index);
        card.classList.toggle('actions-open', willOpen);
        const more = card.querySelector('.terminal-actions-more-btn');
        more?.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    }

    /* Fold every header action except close into the overflow menu when the
       inline row no longer fits — the "buttons clip when the pane is tiny" fix.
       Measured against the header's own width so it tracks the real pixel size
       of each pane (a pane below ~1/8 of the surface collapses first). */
    function updatePaneHeaderLayout(index) {
        const card = document.getElementById(`tc-${index}`);
        if (!card) {
            return;
        }
        const header = card.querySelector('.terminal-header');
        const actions = card.querySelector('.terminal-actions');
        if (!header || !actions) {
            return;
        }
        /* Measure with the actions inline; the class is re-applied synchronously
           in the same frame so the intermediate state never paints. */
        card.classList.remove('actions-collapsed');
        const overflowing = header.scrollWidth - header.clientWidth > 1;
        card.classList.toggle('actions-collapsed', overflowing);
        if (!overflowing && card.classList.contains('actions-open')) {
            card.classList.remove('actions-open');
            card.querySelector('.terminal-actions-more-btn')?.setAttribute('aria-expanded', 'false');
        }
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

        /* Every pane of the new grid now exists: drop explorer-theme overrides
           keyed by panes that no longer do (a restart hands out new session
           ids), so the cache cannot accumulate dead entries (SGP-09). */
        GridVibeExplorerThemeStore.pruneStore(localStorage, liveExplorerThemeKeys());

        document.getElementById('emptyState').classList.remove('visible');
        gridBuilt = true;
        visibleGroupId = activeGroupId;
        updateSessionChrome(count, activeGroupId);
        updateAllSplitButtonStates();
        renderResizeHandles();
    }

    /* Two explicit split controls — one for a side-by-side (vertical divider)
       split, one for a stacked (horizontal divider) split — so the axis is the
       user's choice rather than an inferred guess. Every pane kind carries them:
       a terminal splits into a second terminal, and an explorer or browser pane
       splits off a terminal rooted at the directory it is showing. */
    function splitButtonsHtml(index, session) {
        const titles = splitButtonTitles(session);
        return `
            <button
                type="button"
                class="terminal-action-btn terminal-split-btn terminal-split-v-btn"
                id="tsplitv-${index}"
                data-terminal-split-v="${index}"
                title="${escHtml(titles.vertical)}"
                aria-label="${escHtml(titles.vertical)}"
            >
                ${SPLIT_VERTICAL_ICON}
            </button>
            <button
                type="button"
                class="terminal-action-btn terminal-split-btn terminal-split-h-btn"
                id="tsplith-${index}"
                data-terminal-split-h="${index}"
                title="${escHtml(titles.horizontal)}"
                aria-label="${escHtml(titles.horizontal)}"
            >
                ${SPLIT_HORIZONTAL_ICON}
            </button>
        `;
    }

    /* Overflow toggle that reveals the folded header actions on panes too small
       to show them inline. Hidden until the header collapses; the close button
       always stays outside the fold. */
    function paneMoreButtonHtml(index) {
        return `
            <button
                type="button"
                class="terminal-action-btn terminal-actions-more-btn"
                id="tmore-${index}"
                data-terminal-actions-more="${index}"
                title="More actions"
                aria-label="More actions"
                aria-haspopup="true"
                aria-expanded="false"
            >
                ${TERMINAL_MORE_ICON}
            </button>
        `;
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
                        <span class="terminal-agent-icon" id="ticon-${i}" aria-hidden="true" ${session.startup_mode === 'agent' ? '' : 'hidden'}>${paneAgentIconHtml(session)}</span>
                        <span class="terminal-name" id="tname-${i}" ${window.GridVibeAgentIdentity.paneKindForSession(session) === 'agent' ? `data-agent="${window.GridVibeAgentGlyphs.agentGlyphKey(window.GridVibeAgentIdentity.agentKeyForSession(session))}"` : ''}>
                            ${escHtml(paneDisplayTitle(session, i))}
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
                        <div class="terminal-actions" id="tactions-${i}">
                            ${paneResetButtonHtml(i, session)}
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
                            ${splitButtonsHtml(i, session)}
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
                        </div>
                        ${paneMoreButtonHtml(i)}
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
                        ${paneShellMenuHtml(i)}
                    </div>
                </div>
                <div class="terminal-wrapper" id="tw-${i}">
                    <div class="terminal-surface">
                        ${isBrowser ? browserSurfaceHtml(i, session) : (isExplorer ? `
                            <div class="explorer-surface" id="explorer-${i}">
                                <div class="explorer-bar">
                                     <button type="button" class="explorer-refresh" id="explorer-refresh-${i}" data-explorer-refresh="${i}" title="Refresh explorer (F5)" aria-label="Refresh explorer">${TERMINAL_REFRESH_ICON}</button>
                                     <button type="button" class="explorer-up" id="explorer-up-${i}" data-explorer-up="${i}" title="Go to parent directory (Mouse Back)">↑</button>
                                     <button type="button" class="explorer-tree-toggle" id="explorer-tree-toggle-${i}" data-explorer-tree-toggle="${i}" title="Show file tree" aria-label="Show file tree" aria-pressed="false">${EXPLORER_TREE_TOGGLE_ICON}</button>
                                     <button type="button" class="explorer-git-toggle" id="explorer-git-toggle-${i}" data-explorer-git-toggle="${i}" title="Show Git changes and history" aria-label="Show Git changes and history" aria-pressed="false">${EXPLORER_GIT_TOGGLE_ICON}</button>
                                     <button type="button" class="explorer-search-toggle" id="explorer-search-toggle-${i}" data-explorer-search-toggle="${i}" title="Search in folder (Ctrl+Shift+F)" aria-label="Search in folder" aria-pressed="false">${EXPLORER_SEARCH_TOGGLE_ICON}</button>
                                     ${session.mode === 'ssh' ? '' : `<button type="button" class="explorer-os-open" id="explorer-os-open-${i}" data-explorer-os-open="${i}" title="Open current location in system file manager" aria-label="Open current location in system file manager">${EXPLORER_OS_OPEN_ICON}</button>`}
                                     <button type="button" class="explorer-bar-upload" id="explorer-bar-upload-${i}" data-explorer-bar-upload="${i}" title="Upload files into the folder this pane is showing" aria-label="Upload files into the folder this pane is showing">${EXPLORER_UPLOAD_ICON}</button>
                                     <div class="explorer-git-summary" id="explorer-git-${i}" aria-live="polite"></div>
                                     <div class="explorer-path" id="explorer-path-${i}">${escHtml(session.directory || '')}</div>
                                     <div class="explorer-directory-search" id="explorer-directory-search-${i}"></div>
                                 </div>
                                 <div class="explorer-main" id="explorer-main-${i}">
                                     <aside class="explorer-sidebar" id="explorer-sidebar-${i}" hidden>
                                         <div class="explorer-tree-panel" id="explorer-tree-panel-${i}" hidden></div>
                                         <button type="button" class="explorer-sidebar-splitter" id="explorer-sidebar-splitter-${i}-0" data-explorer-sidebar-splitter="${i}" aria-label="Resize explorer sidebar panels" hidden></button>
                                         <div class="explorer-git-panel" id="explorer-git-panel-${i}" hidden></div>
                                         <button type="button" class="explorer-sidebar-splitter" id="explorer-sidebar-splitter-${i}-1" data-explorer-sidebar-splitter="${i}" aria-label="Resize explorer sidebar panels" hidden></button>
                                         <div class="explorer-search-panel" id="explorer-search-panel-${i}" hidden></div>
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
        /* Local Repo terminals open the shell picker here; every other pane kind
           keeps the plain one-click reset (terminal-shell.js owns the split). */
        wireCardButton(card, `[data-terminal-refresh="${i}"]`, () => handlePaneResetButton(i));
        wirePaneShellMenu(card, i);
        /* Browser panes (URL bar, external-open, tab strip, frame hooks) are
           wired as one surface by browser-pane.js; a no-op on other panes. */
        wireBrowserOnlyControls(card, i);
        wireCardButton(card, `[data-session-browser-toggle="${i}"]`, () => switchSessionBrowserMode(i));
        wireCardButton(card, `[data-session-mode-toggle="${i}"]`, () => switchSessionPaneMode(i));
        wireSplitButtons(card, i);
        wirePaneMoreButton(card, i);
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
        wireCardButton(card, `[data-explorer-up="${i}"]`, () => navigateExplorerToParent(i));
        wireCardButton(card, `[data-explorer-git-toggle="${i}"]`, () => toggleExplorerGitSidebar(i));
        wireCardButton(card, `[data-explorer-tree-toggle="${i}"]`, () => toggleExplorerTreeSidebar(i));
        wireCardButton(card, `[data-explorer-search-toggle="${i}"]`, () => toggleExplorerSearchSidebar(i));
        wireCardButton(card, `[data-explorer-os-open="${i}"]`, () => revealExplorerInOs(i));
        wireCardButton(
            card,
            `[data-explorer-bar-upload="${i}"]`,
            () => reportRefusedExplorerUpload(startExplorerPaneUpload(i))
        );
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

    function explorerPaneIndexFromTarget(target) {
        const card = target?.closest?.('.terminal-container');
        const index = terminalCardSlot(card);
        return index !== -1
            && card.classList.contains('explorer-pane')
            && isExplorerSession(terminals[index]?._session)
            ? index
            : -1;
    }

    /* Re-assert the pointer-driven mark by hand. A pane that clears its own
       state by blurring a row (Escape over a multi-entry selection) would
       otherwise leave the resolver below with nothing to answer from, and the
       next press would land on a different pane — or on none. */
    function markActiveExplorerPane(index) {
        _activeExplorerIndex = isExplorerSession(terminals[index]?._session) ? index : -1;
    }

    function findExplorerShortcutTargetIndex(target = document.activeElement) {
        /* Pointer interaction is the source of truth for explorer panes. Many
           explorer controls deliberately prevent mousedown focus so toolbar
           clicks do not steal a selection; in that case document.activeElement
           can still belong to a different pane. */
        if (_activeExplorerIndex !== -1
            && isExplorerSession(terminals[_activeExplorerIndex]?._session)) {
            return _activeExplorerIndex;
        }
        const targetCard = target?.closest?.('.terminal-container');
        if (targetCard) {
            return explorerPaneIndexFromTarget(target);
        }
        const explorerIndexes = terminals
            .map((pane, index) => isExplorerSession(pane?._session) ? index : -1)
            .filter(index => index !== -1);
        return explorerIndexes.length === 1 ? explorerIndexes[0] : -1;
    }

    function navigateExplorerToParent(index) {
        const pane = terminals[index];
        if (!pane || !isExplorerSession(pane._session)) {
            return false;
        }
        const targetPath = pane._explorerMode === 'file'
            ? (pane._explorerPath || '')
            : (pane._explorerParentPath || '');
        loadExplorerPane(index, targetPath);
        return true;
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

    /* Leaving this workspace window: blur whatever pane holds keyboard focus so
       the window is parked with nothing selected. DOM focus survives a window
       switch, so without this the pane that was active when you left is active
       again the instant the window returns to the front, and every keystroke —
       including the workspace and tab shortcuts — goes into it. The blur fires
       focusout, which clears the highlight; the explicit clear covers panes
       that never take real focus (browser/explorer iframes). */
    function dropTerminalFocusForWindowSwitch() {
        const active = document.activeElement;
        if (active instanceof HTMLElement
            && active.closest?.('.terminal-container')) {
            try { active.blur(); } catch (_) {}
        }
        clearActiveTerminalHighlight();
    }

    /* Clicking another window is a workspace switch too — on a second screen it
       is the *usual* one — and it never runs through `switchToWorkspaceWindow`,
       so the drop has to hang off the window losing focus as well. Focus moving
       into a same-page iframe (a browser pane) also fires `blur` here, so the
       decision is deferred one tick and taken only when the document really
       lost focus. */
    window.addEventListener('blur', () => {
        setTimeout(() => {
            if (!document.hasFocus()) {
                dropTerminalFocusForWindowSwitch();
            }
        }, 0);
    });

    /* Full reset on teardown. */
    function resetFocusedTerminal() {
        clearActiveTerminalHighlight();
        _activeExplorerIndex = -1;
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
        _activeExplorerIndex = explorerPaneIndexFromTarget(event.target);
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
                handlePaneResetButton(index);
            });
        }
        wirePaneShellMenu(card, index);

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

        wireSplitButtons(card, index);
        wirePaneMoreButton(card, index);

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
        /* Splitting an explorer or browser pane produces a plain terminal, so
           cloning the source's chrome would carry over its theme toggle, its
           explorer surface and its inverted mode-toggle label. Build the new
           card from the session instead — the same path the initial grid uses. */
        const sourcePane = terminals[sourceIndex];
        if (isExplorerPaneInstance(sourcePane) || isBrowserPaneInstance(sourcePane)) {
            const freshCard = buildPaneCard(session, targetIndex);
            wirePaneControls(freshCard, targetIndex);
            return freshCard;
        }

        const card = sourceCard.cloneNode(true);
        card.classList.remove('dragging', 'drag-target', 'explorer-pane', 'browser-pane', 'actions-collapsed', 'actions-open');
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
        syncPaneAgentIcon(card.querySelector(`#ticon-${targetIndex}`), session);
        if (name) {
            name.textContent = paneDisplayTitle(session, targetIndex);
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
        card.querySelectorAll(`#tsplitv-${targetIndex}, #tsplith-${targetIndex}`).forEach(button => {
            button.disabled = false;
        });

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
                navigateExplorerToParent(index);
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

        const explorerBarUpload = card.querySelector(`[data-explorer-bar-upload="${index}"]`);
        if (explorerBarUpload && !explorerBarUpload.dataset.bound) {
            explorerBarUpload.dataset.bound = 'true';
            explorerBarUpload.draggable = false;
            explorerBarUpload.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerBarUpload.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                reportRefusedExplorerUpload(startExplorerPaneUpload(index));
            });
        }

        const explorerSearchToggle = card.querySelector(`[data-explorer-search-toggle="${index}"]`);
        if (explorerSearchToggle && !explorerSearchToggle.dataset.bound) {
            explorerSearchToggle.dataset.bound = 'true';
            explorerSearchToggle.draggable = false;
            explorerSearchToggle.addEventListener('mousedown', event => {
                event.preventDefault();
                event.stopPropagation();
            });
            explorerSearchToggle.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                toggleExplorerSearchSidebar(index);
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

    /* Every pane kind keeps its split controls, so a mode switch only has to
       make sure they are present — updateAllSplitButtonStates re-labels them for
       the pane's new kind afterwards. */
    function ensureSplitControls(card, index, session) {
        const existing = card.querySelectorAll(`[data-terminal-split-v="${index}"], [data-terminal-split-h="${index}"]`);
        if (existing.length) {
            existing.forEach(button => { button.hidden = false; });
            return;
        }

        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (!modeButton) {
            return;
        }

        modeButton.insertAdjacentHTML('afterend', splitButtonsHtml(index, session));
        wireSplitButtons(card, index);
    }

    function replacePaneWithExplorer(index, session) {
        const card = document.getElementById(`tc-${index}`);
        const wrapper = document.getElementById(`tw-${index}`);
        if (!card || !wrapper) {
            return false;
        }

        const previousTerminal = terminals[index];
        if (isBrowserPaneInstance(previousTerminal)) browserDisposePane(previousTerminal);
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
            _explorerGitSidebarOpen: Boolean(session.explorer_git_open),
            _explorerGitFollowBrowsing: Boolean(session.explorer_git_follow_browsing),
            _explorerGitPinnedPath: sessionPinnedPath(session),
            _explorerGitPinKind: sessionPinKind(session),
            _explorerPath: explorerInitialPreviewDirectory(session),
            _explorerSearchSidebarOpen: Boolean(session.explorer_search_open),
            _explorerSidebarWidth: Number(session.explorer_sidebar_width) || 260,
            _explorerSidebarScroll: session.explorer_sidebar_scroll || {},
            _explorerTreeExpanded: new Set(
                Array.isArray(session.explorer_tree_expanded)
                    ? session.explorer_tree_expanded
                    : []
            ),
            _explorerDiffExpandedCommits: new Set(
                Array.isArray(session.explorer_git_expanded)
                    ? session.explorer_git_expanded
                    : []
            )
        };
        sessionIds[index] = session.session_id;
        setSessionRoute(session.session_id, activeGroupId, index);

        card.classList.add('explorer-pane');
        card.classList.remove('browser-pane');
        card.dataset.explorerThemeKey = explorerThemeKey;
        card.dataset.explorerThemeSource = resolvedExplorerTheme.source;
        card.dataset.explorerTheme = initialExplorerTheme;
        ensureSplitControls(card, index, session);
        const browserModeButton = card.querySelector(`[data-session-browser-toggle="${index}"]`);
        if (browserModeButton) {
            browserModeButton.remove();
        }
        ensureExplorerThemeButton(card, index);
        applyExplorerThemeToCard(card, initialExplorerTheme);
        updateModeToggleButton(card.querySelector(`[data-session-mode-toggle="${index}"]`), true);
        syncPaneIdentityChrome(index, session);
        syncPaneShellControls(index, session);
        wrapper.innerHTML = `
            <div class="terminal-surface">
                <div class="explorer-surface" id="explorer-${index}">
                    <div class="explorer-bar">
                        <button type="button" class="explorer-refresh" id="explorer-refresh-${index}" data-explorer-refresh="${index}" title="Refresh explorer (F5)" aria-label="Refresh explorer">${TERMINAL_REFRESH_ICON}</button>
                        <button type="button" class="explorer-up" id="explorer-up-${index}" data-explorer-up="${index}" title="Go to parent directory (Mouse Back)">↑</button>
                        <button type="button" class="explorer-tree-toggle" id="explorer-tree-toggle-${index}" data-explorer-tree-toggle="${index}" title="Show file tree" aria-label="Show file tree" aria-pressed="false">${EXPLORER_TREE_TOGGLE_ICON}</button>
                        <button type="button" class="explorer-git-toggle" id="explorer-git-toggle-${index}" data-explorer-git-toggle="${index}" title="Show Git changes and history" aria-label="Show Git changes and history" aria-pressed="false">${EXPLORER_GIT_TOGGLE_ICON}</button>
                        <button type="button" class="explorer-search-toggle" id="explorer-search-toggle-${index}" data-explorer-search-toggle="${index}" title="Search in folder (Ctrl+Shift+F)" aria-label="Search in folder" aria-pressed="false">${EXPLORER_SEARCH_TOGGLE_ICON}</button>
                        ${session.mode === 'ssh' ? '' : `<button type="button" class="explorer-os-open" id="explorer-os-open-${index}" data-explorer-os-open="${index}" title="Open current location in system file manager" aria-label="Open current location in system file manager">${EXPLORER_OS_OPEN_ICON}</button>`}
                        <button type="button" class="explorer-bar-upload" id="explorer-bar-upload-${index}" data-explorer-bar-upload="${index}" title="Upload files into the folder this pane is showing" aria-label="Upload files into the folder this pane is showing">${EXPLORER_UPLOAD_ICON}</button>
                        <div class="explorer-git-summary" id="explorer-git-${index}" aria-live="polite"></div>
                        <div class="explorer-path" id="explorer-path-${index}">${escHtml(session.directory || '')}</div>
                        <div class="explorer-directory-search" id="explorer-directory-search-${index}"></div>
                    </div>
                    <div class="explorer-main" id="explorer-main-${index}">
                        <aside class="explorer-sidebar" id="explorer-sidebar-${index}" hidden>
                            <div class="explorer-tree-panel" id="explorer-tree-panel-${index}" hidden></div>
                            <button type="button" class="explorer-sidebar-splitter" id="explorer-sidebar-splitter-${index}-0" data-explorer-sidebar-splitter="${index}" aria-label="Resize explorer sidebar panels" hidden></button>
                            <div class="explorer-git-panel" id="explorer-git-panel-${index}" hidden></div>
                            <button type="button" class="explorer-sidebar-splitter" id="explorer-sidebar-splitter-${index}-1" data-explorer-sidebar-splitter="${index}" aria-label="Resize explorer sidebar panels" hidden></button>
                            <div class="explorer-search-panel" id="explorer-search-panel-${index}" hidden></div>
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
        if (isBrowserPaneInstance(previousTerminal)) browserDisposePane(previousTerminal);
        if (previousTerminal?.term) {
            try { previousTerminal.term.dispose(); } catch (_) {}
        }
        disconnectTerminalObserver(index);
        if (socket && sessionIds[index]) {
            socket.emit('leave_session', { session_id: sessionIds[index] });
        }

        terminals[index] = browserEnsureTabState(
            { _session: session, _paneType: 'browser', _attached: false },
            session
        );
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
        ensureSplitControls(card, index, session);
        const browserButton = ensureBrowserModeButton(card, index, session, true);
        updateBrowserModeToggleButton(browserButton, true);
        syncPaneIdentityChrome(index, session);
        syncPaneShellControls(index, session);
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

        if (typeof browserDisposePane === 'function') browserDisposePane(terminals[index]);
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
        ensureSplitControls(card, index, session);
        const modeButton = card.querySelector(`[data-session-mode-toggle="${index}"]`);
        if (modeButton) {
            modeButton.hidden = false;
        }
        const browserButton = ensureBrowserModeButton(card, index, session, false);
        updateBrowserModeToggleButton(browserButton, false);
        updateModeToggleButton(card.querySelector(`[data-session-mode-toggle="${index}"]`), false);
        syncPaneIdentityChrome(index, session);
        syncPaneShellControls(index, session);
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
        /* Each replacement function refuses a card or wrapper that is not
           there, and that refusal leaves the pane on screen exactly as it was
           — so the same precondition is checked here, before anything is
           released. Past it the outgoing explorer pane is being discarded,
           not suspended: it is about to leave `terminals[index]`, taking its
           frame-sliced build and its in-flight requests with it. */
        if (!document.getElementById(`tc-${index}`) || !document.getElementById(`tw-${index}`)) {
            return false;
        }
        if (isExplorerPaneInstance(terminals[index])) {
            explorerReleasePaneWork(terminals[index]);
        }
        const replaced = isBrowserSession(session)
            ? replacePaneWithBrowser(index, session)
            : (isExplorerSession(session)
                ? replacePaneWithExplorer(index, session)
                : replacePaneWithTerminal(index, session));
        if (replaced) {
            /* The pane's mode decides which presentation fields the transaction
               may carry, so republish the group rather than let a batch built
               for the previous mode be rejected until the next change. */
            noteGroupPresentationChanged(visibleGroupId);
        }
        return replaced;
    }

    /* A cwd probe that could not answer left the explorer on an assumed
       directory. Report it in the pane rather than swallowing it: the silent
       fallback to the launch directory is what made the same gesture open two
       different roots on two different days. Informational, so it carries a
       Dismiss and no retry -- the switch itself succeeded. */
    function showExplorerCwdNotice(index, probe) {
        const surface = document.getElementById(`explorer-${index}`);
        const bar = surface?.querySelector('.explorer-bar');
        if (!surface || !bar) {
            return;
        }
        document.getElementById(`explorer-cwd-bar-${index}`)?.remove();

        const notice = document.createElement('div');
        notice.id = `explorer-cwd-bar-${index}`;
        notice.className = 'explorer-cwd-bar';

        const message = document.createElement('span');
        message.className = 'explorer-fs-bar-message';
        message.setAttribute('role', 'status');
        const opened = String(probe?.directory || '');
        message.textContent = probe?.reason === 'agent_pane'
            ? `This pane is running an agent, so its current directory was not read. Opened at ${opened}.`
            : `The terminal did not answer where it is. Opened at ${opened}.`;
        notice.appendChild(message);

        const actions = document.createElement('span');
        actions.className = 'explorer-fs-bar-actions';
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'explorer-fs-bar-action';
        dismiss.textContent = 'Dismiss';
        dismiss.addEventListener('click', () => notice.remove());
        actions.appendChild(dismiss);
        notice.appendChild(actions);

        bar.insertAdjacentElement('afterend', notice);
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
            } else if (data.cwd_probe && data.cwd_probe.resolved === false) {
                showExplorerCwdNotice(index, data.cwd_probe);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] switchSessionPaneMode failed:', error);
            setWorkspaceSaveMessage(`Mode switch failed: ${error.message}`, 'error');
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
            setWorkspaceSaveMessage(`Browser mode switch failed: ${error.message}`, 'error');
            updateBrowserModeToggleButton(button, switchingToTerminal);
        } finally {
            pendingModeSwitchSessionIds.delete(sessionId);
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
                const sidebar = explorerSidebarPresentation(index);
                const previewTab = explorerPreviewTab(pane);
                const previewActive = pane._explorerActiveTabId === EXPLORER_PREVIEW_TAB_ID;
                const pin = panePinDescriptor(pane);
                stateBySessionId[sessionId] = {
                    type: 'explorer',
                    explorer_tree_open: Boolean(pane._explorerTreeSidebarOpen),
                    explorer_git_open: Boolean(pane._explorerGitSidebarOpen),
                    explorer_git_follow_browsing: Boolean(pane._explorerGitFollowBrowsing),
                    explorer_git_pin_active: pin.active,
                    explorer_git_pinned_path: pin.path,
                    explorer_git_pin_kind: pin.kind,
                    explorer_search_open: Boolean(pane._explorerSearchSidebarOpen),
                    explorer_sidebar_width: sidebar.width,
                    explorer_sidebar_scroll: sidebar.scroll,
                    explorer_tree_expanded: sidebar.expanded,
                    explorer_git_expanded: sidebar.gitExpanded,
                    explorer_open_tabs: tabs.open_tabs,
                    explorer_active_tab: tabs.active_tab,
                    explorer_tab_views: tabs.tab_views,
                    explorer_tab_state: pane._explorerTabs.map(tab => ({
                        id: tab.id,
                        view: tab.view || null,
                        fontSize: tab.fontSize || 0,
                        preferredMode: tab.preferredMode || '',
                        dirPath: tab.dirPath || '',
                        hasDirPath: Object.prototype.hasOwnProperty.call(tab, 'dirPath')
                    })),
                    /* Only the dynamic Preview tab needs an explicit reopen; pinned
                       tabs come back through the persisted-tab path. */
                    explorer_preview_path: previewActive ? (previewTab?.path || '') : '',
                    explorer_preview_view: previewActive ? (pane._explorerLastFileView || '') : '',
                };
            } else if (isBrowserSession(pane._session)) {
                stateBySessionId[sessionId] = browserCaptureCloseSnapshot(pane);
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
                if (saved.hasDirPath) {
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
        const explorerSessionId = sessionIds[index];
        if (hasActiveExplorerFilesystemOperation(index)) {
            showTerminalToast('A copy or delete is finishing. Try closing this pane again shortly.', 'error');
            return;
        }
        cancelExplorerFilesystemUiForSession(explorerSessionId);
        // Closing a pane tears down its explorer viewer — confirm any dirty edit.
        if (!(await confirmDiscardExplorerEdit(index, 'Closing this pane'))) {
            return;
        }
        const plan = buildCloseTerminalPlan(index);
        if (!plan) {
            return;
        }
        const restoreRectsBySessionId = plan.closeLastPane
            ? null
            : buildTerminalCloseRectsBySessionId(plan);
        if (!plan.closeLastPane && !restoreRectsBySessionId) {
            setWorkspaceSaveMessage(
                'Close terminal failed: no neighboring pane can safely fill this layout',
                'error'
            );
            return;
        }
        // Past the confirm and the layout check: this pane is really closing.
        forgetExplorerSessionMarkdownAppearance(plan.sessionId);

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
            setWorkspaceSaveMessage(`Close terminal failed: ${error.message}`, 'error');
        } finally {
            if (button) {
                button.textContent = '×';
                button.disabled = false;
            }
            updateAllSplitButtonStates();
        }
    }

    async function splitTerminalPane(index, axis) {
        const sourceSessionId = sessionIds[index];
        const sourceTerminal = terminals[index];
        const sourceCard = document.getElementById(`tc-${index}`);
        const grid = document.getElementById('terminalsGrid');
        const splitButtons = sourceCard
            ? sourceCard.querySelectorAll(`[data-terminal-split-v="${index}"], [data-terminal-split-h="${index}"]`)
            : [];
        closeAllPaneActionMenus();
        closeAllPaneShellMenus();
        if (!sourceSessionId || !sourceTerminal || !sourceCard || !grid) {
            return;
        }
        if (terminals.length >= MAX_SPLIT_TERMINALS) {
            updateAllSplitButtonStates();
            return;
        }

        const visualIndex = Array.from(grid.children).indexOf(sourceCard);
        if (visualIndex < 0) {
            return;
        }

        const rects = ensureSplitSlotRects();
        const sourceRect = rects[visualIndex];
        const candidates = sourceRect ? getSplitCandidates(index, sourceRect) : [];
        if (!axis || !candidates.includes(axis)) {
            updateSplitButtonState(index);
            return;
        }

        splitButtons.forEach(button => { button.disabled = true; });

        /* An explorer pane splits off a terminal rooted where the user is
           actually browsing, not where the pane was launched. */
        const payload = { axis };
        if (isExplorerSession(sourceTerminal._session)) {
            payload.directory = getExplorerSelectedDirectory(index);
        }

        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sourceSessionId)}/split`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
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
            /* The group gained a pane and a custom rectangle set. Rebase on the
               revision the split response carries, then publish the geometry —
               nothing else does, which is why a split used to revert. */
            presentationController()?.setGroupRevision(
                activeGroupId,
                data.group?.presentation_revision
            );
            noteGroupPresentationChanged(activeGroupId);
            await ensureAttachedTerminalsReady([index, newIndex]);
            emitTerminalResize(index, true);
            emitTerminalResize(newIndex, true);
        } catch (error) {
            console.error('[GridVibe Sessions] splitTerminalPane failed:', error);
            setWorkspaceSaveMessage(`Split failed: ${error.message}`, 'error');
        } finally {
            updateAllSplitButtonStates();
        }
    }

    /* ─────────────────────────────────────────────
       Clipboard helpers (copy / paste)
    ───────────────────────────────────────────── */
    /* Fire-and-forget wrapper over the shared writer: every caller here is a
       menu entry or a Ctrl+C on a selection, none of which reports a result. */
    function _copyText(text) {
        if (!text) return;
        copyTextToClipboard(text);
    }

    function _wireClipboard(index) {
        const term = terminals[index].term;

        /* Auto-copy on mouse selection */
        term.onSelectionChange(() => {
            const sel = term.getSelection();
            if (sel) _copyText(sel);
        });

        /* Copy/paste keys are handled by the terminal's single custom key
           event handler (`attachTerminalKeyEventHandler`) — installing another
           one here would replace it and take the shortcut pass-throughs with it. */

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
        const index = findExplorerShortcutTargetIndex();
        return index !== -1 && isExplorerSearchablePane(terminals[index]) ? index : -1;
    }

    /* The highlighted string, when the current selection sits inside this
       explorer pane and is a single line — the useful case for seeding a find.
       Multi-line selections and selections in other panes are ignored. */
    function explorerSelectionQuery(index) {
        /* A pane with an open in-place editor answers for itself: its selection
           lives in a textarea, where the document selection read below reports
           nothing. explorer-edit-find.js returns null when there is no editor,
           and an object — empty query included — when there is, so exactly one
           of the two speaks. */
        const editSeed = window.explorerEditSelectionSeed?.(index);
        if (editSeed) {
            return editSeed.query;
        }
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

    document.addEventListener('keydown', event => {
        if (event.key !== 'F5' || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.repeat) {
            return;
        }
        const index = findExplorerShortcutTargetIndex();
        if (index === -1) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        refreshTerminalDisplay(index);
    });

    /* Ctrl+Shift+E is the keyboard route into the in-place editor, and while
       editing it is Cancel — the same toggle the header shows, where the Edit
       button is itself replaced by Save/Cancel. Leaving was already bound
       (Ctrl+S saves, Esc cancels) and entering was mouse-only, so the pair was
       asymmetric.

       A file that cannot be edited says why on the toast rather than doing
       nothing: the disabled Edit button carries that same sentence in its
       tooltip, and a chord that silently no-ops reads as broken. The reason
       comes from explorerEditDisabledTooltip() so the two cannot drift.

       On Firefox in browser mode this chord is the Network Monitor and may not
       reach the page; Chrome, Edge and the native window are unaffected. */
    document.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey || event.repeat) {
            return;
        }
        if (event.code !== 'KeyE') {
            return;
        }
        const index = findExplorerShortcutTargetIndex();
        const pane = index === -1 ? null : terminals[index];
        if (!pane) {
            return;
        }
        if (explorerEditState(pane)) {
            event.preventDefault();
            event.stopPropagation();
            /* Focus goes back to the file, not to the Edit button: a control
               focused from the keyboard is painted and shows its tooltip, so
               the button came back looking hovered for a chord pressed in the
               buffer. */
            cancelExplorerEdit(index, { focus: 'source' });
            return;
        }
        /* Not showing a file at all — a directory listing has no Edit button
           either, so there is nothing to report and nothing to claim. */
        if (pane._explorerMode !== 'file') {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (!pane._explorerFileEditable) {
            showTerminalToast(
                explorerEditDisabledTooltip(pane._explorerFileEditBlockReason || ''),
                'error'
            );
            return;
        }
        enterExplorerEditMode(index);
    });

    document.addEventListener('auxclick', event => {
        if (event.button !== 3) {
            return;
        }
        const index = explorerPaneIndexFromTarget(event.target);
        if (index === -1) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        navigateExplorerToParent(index);
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

    /* Ctrl+Shift+F is shared between the explorer repository search panel and
       the terminal scrollback overlay (finding 10.3). The explorer wins only
       when focus is genuinely on an explorer pane; a focused terminal keeps
       the overlay. On an explorer pane the shortcut toggles: it opens and
       seeds the panel, and closes it again when it is already open.
       Deliberately stricter than findExplorerSearchTargetIndex()
       — its "any explorer pane in the group" last resort would steal the
       shortcut from a genuinely focused terminal. */
    function isExplorerRepoSearchablePane(index) {
        const card = document.getElementById(`tc-${index}`);
        return Boolean(
            card?.classList.contains('explorer-pane') && terminals[index] && sessionIds[index]
        );
    }

    function findExplorerRepoSearchTargetIndex() {
        const index = findExplorerShortcutTargetIndex();
        return index !== -1 && isExplorerRepoSearchablePane(index) ? index : -1;
    }

    document.addEventListener('keydown', event => {
        if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey || event.code !== 'KeyF') {
            return;
        }
        const explorerIndex = findExplorerRepoSearchTargetIndex();
        if (explorerIndex !== -1 && toggleExplorerRepoSearchShortcut(explorerIndex, explorerSelectionQuery(explorerIndex))) {
            event.preventDefault();
            event.stopPropagation();
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

    /* Alt+W walks the live workspaces (Alt+Shift+W walks back), so swapping
       windows costs one keystroke instead of a trip through Workspace ▸ Open
       Workspace. Opening a workspace that already has a window only focuses it,
       so a repeated press is a plain cycle and never a second window. The
       in-flight guard keeps a held key from queueing a burst of window opens. */
    let workspaceCycleInFlight = false;

    async function cycleWorkspaceWindow(step) {
        if (workspaceCycleInFlight) {
            return;
        }
        workspaceCycleInFlight = true;
        try {
            const target = nextWorkspaceInCycle(
                await fetchLiveWorkspaces(),
                currentWorkspaceId,
                step
            );
            if (!target) {
                showTerminalToast('No other workspace is open.', '');
                return;
            }
            await switchToWorkspaceWindow(target.workspace_id, {
                groupId: target.active_group_id
            });
        } catch (error) {
            console.error('[GridVibe Sessions] workspace switch failed:', error);
            showTerminalToast(`Could not switch workspace: ${error.message}`, 'error');
        } finally {
            workspaceCycleInFlight = false;
        }
    }

    /* A focused terminal must not block a window-level Alt shortcut: xterm's
       helper textarea is the keyboard target of every focused pane, so the
       plain editable-target guard would swallow the key in exactly the case
       makeTerminal already declines it for (it returns false so these handlers
       can act, and the shell never sees the ESC sequence) — leaving the user
       stuck in the pane. Real inputs (search boxes, name fields, explorer
       controls) still block it. */
    function isPaneShortcutBlockingTarget(target) {
        if (target instanceof Element && target.closest('.xterm-helper-textarea')) {
            return false;
        }
        return isEditableShortcutTarget(target);
    }

    /* minimize-all.js owns Alt+X and its button; this is the page's answer to
       "may the chord fire from here", so the native control obeys the same
       focused-pane rule the Alt+Q and Alt+W handlers below do. */
    function minimizeAllShortcutBlocked(target) {
        return isPaneShortcutBlockingTarget(target);
    }

    document.addEventListener('keydown', event => {
        if (!event.altKey || event.ctrlKey || event.metaKey || event.repeat) {
            return;
        }
        if (event.code !== 'KeyW' || isPaneShortcutBlockingTarget(event.target)) {
            return;
        }
        if (!isMultiWorkspaceEnabled()) {
            return;
        }

        event.preventDefault();
        cycleWorkspaceWindow(event.shiftKey ? -1 : 1);
    });

    /* Alt+Q opens the launcher — the same action as the button at the head of
       the session tab line, and one hand away from the Alt navigation family
       it belongs to (Alt+1..9, Alt+W).

       It replaced Alt+` because that key is a dead accent on several layouts
       (cedilla on Slovenian/Croatian). The launcher still opened — the binding
       matched the physical key — but Windows' own ToUnicode ignores plain Alt
       when it translates the message, so the layout armed its composer as if
       the key had been pressed bare, and the next character typed anywhere in
       the thread (the launcher's own new-workspace field, most often) came out
       accented. That arming happens before any handler runs, so
       preventDefault() could not undo it: the cure is a key that is dead on no
       layout, and Q is one. `!event.ctrlKey` is what keeps AltGr out — AltGr
       reaches the page as Ctrl+Alt, and AltGr+Q types a backslash here — so
       that clause is load-bearing, not boilerplate. Still matched on
       event.code, so the chord stays on the same physical key whatever the
       layout prints on it; README and the button's own tooltip are where it is
       named. */
    document.addEventListener('keydown', event => {
        if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.repeat) {
            return;
        }
        if (event.code !== 'KeyQ' || isPaneShortcutBlockingTarget(event.target)) {
            return;
        }

        event.preventDefault();
        goToSettings();
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
            const index = _voiceActiveIndex !== -1 ? _voiceActiveIndex : _findVoiceTargetIndex();
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
                        entry.explorer_git_follow_browsing = snapshot.explorer_git_follow_browsing;
                        entry.explorer_git_pin_active = snapshot.explorer_git_pin_active;
                        entry.explorer_git_pinned_path = snapshot.explorer_git_pinned_path;
                        entry.explorer_git_pin_kind = snapshot.explorer_git_pin_kind;
                        entry.explorer_search_open = snapshot.explorer_search_open;
                        entry.explorer_sidebar_width = snapshot.explorer_sidebar_width;
                        entry.explorer_sidebar_scroll = snapshot.explorer_sidebar_scroll;
                        entry.explorer_tree_expanded = snapshot.explorer_tree_expanded;
                        entry.explorer_git_expanded = snapshot.explorer_git_expanded;
                        entry.explorer_open_tabs = snapshot.explorer_open_tabs;
                        entry.explorer_active_tab = snapshot.explorer_active_tab;
                        entry.explorer_tab_views = snapshot.explorer_tab_views;
                    } else if (snapshot.type === 'browser') {
                        entry.initial_command = snapshot.browser_url;
                        entry.browser_tabs = snapshot.browser_tabs;
                        entry.browser_active_tab = snapshot.browser_active_tab;
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

            /* The grid this load just produced is what a held dashboard target
               has been waiting for. */
            settleWorkspaceFocusTarget();
        } catch (e) {
            if (loadToken !== activeLoadToken) {
                return;
            }
            if (workspaceGone) {
                /* The close is already under way; a browser tab the user opened
                   by hand cannot close itself, so leave an honest empty state
                   rather than a load error over tabs that no longer exist. */
                sessionGroups = [];
                knownGroupIds = [];
                activeGroupId = '';
                renderSessionTabs();
                label.textContent = 'This workspace was closed.';
                grid.style.display = 'none';
                document.getElementById('emptyState').classList.add('visible');
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
        const response = await fetch(
            `/api/session-groups?workspace_id=${encodeURIComponent(currentWorkspaceId)}`
        );
        const data = await response.json();
        if (!response.ok) {
            /* Every refresh path leads here, so this is the one place that has
               to notice the workspace itself is gone. */
            if (data?.workspace_missing) {
                await handleWorkspaceGone();
            }
            throw new Error(data.error || 'Failed to load session tabs');
        }

        const previousActiveGroupId = activeGroupId;
        const previousGroupIds = knownGroupIds.slice();
        if (typeof data.topbar_visible === 'boolean') {
            /* The refit rides on the flow actually changing, which the peek
               controller reports; nothing to decide here. */
            applyTopbarVisibility(data.topbar_visible, { persist: true });
        }
        setExplorerWorkspaceAppearance({
            preset: data.md_preset,
            font: data.md_font,
            sourceFont: data.source_font
        });
        sessionGroups = Array.isArray(data.groups) ? data.groups : [];
        /* Rebase the presentation queues on the revisions this read just
           observed, so the next change starts from the server's number rather
           than from whatever this window last remembered. */
        adoptPresentationRevisions(data);
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

    /* The one funnel for "is this window fullscreen right now" — every
       fullscreen transition already ends here, so the top bar's auto-hide is
       wired once rather than at each of the four call sites. Fullscreen hides
       the bar for its duration only: the stored topbar_visible is untouched,
       so leaving fullscreen gives back whatever the chevron last said. */
    function updateFullscreenButton() {
        const isBrowserFullscreen = Boolean(document.fullscreenElement);
        const active = isPywebviewAvailable() ? nativeFullscreen : isBrowserFullscreen;
        document.body.classList.toggle('chrome-fullscreen', active);
        topbarPeek.setFullscreen(active);

        const button = document.getElementById('fullscreenBtn');
        if (!button) return;

        const label = active ? 'Exit fullscreen' : 'Enter fullscreen';
        button.innerHTML = active ? FULLSCREEN_EXIT_ICON : FULLSCREEN_ENTER_ICON;
        button.title = label;
        button.setAttribute('aria-label', label);
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }

    async function syncNativeFullscreenState() {
        if (!isPywebviewAvailable()) {
            nativeFullscreen = false;
            updateFullscreenButton();
            return;
        }

        try {
            const api = window.pywebview.api;
            const result = api.get_workspace_fullscreen_state
                ? await api.get_workspace_fullscreen_state(currentWorkspaceId)
                : (
                    api.get_session_fullscreen_state
                        ? await api.get_session_fullscreen_state()
                        : null
                );
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
            if (isPywebviewAvailable()) {
                const api = window.pywebview.api;
                const result = api.exit_workspace_fullscreen
                    ? await api.exit_workspace_fullscreen(currentWorkspaceId)
                    : (
                        api.exit_session_fullscreen
                            ? await api.exit_session_fullscreen()
                            : null
                    );
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
                const api = window.pywebview.api;
                const result = api.toggle_workspace_fullscreen
                    ? await api.toggle_workspace_fullscreen(currentWorkspaceId)
                    : (
                        api.toggle_session_fullscreen
                            ? await api.toggle_session_fullscreen()
                            : null
                    );
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
        renderSessionLine();
        document.title = 'GridVibe — Terminals';
    }

    function logSessionWindowAction(action, details = {}) {
        console.info(`[GridVibe Sessions] ${action}`, details);
    }

    async function goToSettings(event) {
        if (event) {
            event.preventDefault();
        }

        /* The single place a workspace window hands over to the launcher, so
           the launcher's Alt+W return key learns where it came from here and
           nowhere else — and re-learns it every time, so opening the launcher
           again from a different workspace retargets the way back. */
        rememberLauncherOriginWorkspace(currentWorkspaceId);

        /* workspaces.js owns "open or focus the launcher window" for both of
           the windows that ask for it. Native mode keeps this window exactly as
           it is, fullscreen included, so the fullscreen reset is handed over as
           the browser-fallback step rather than run first: a bridge that
           answered would otherwise have left this window un-maximised for
           nothing. */
        logSessionWindowAction('Launcher window requested', {
            preserve_fullscreen: true,
            workspace_id: currentWorkspaceId
        });
        const opened = await openLauncherWindow({
            beforeBrowserFallback: resetFullscreenState,
            originWorkspaceId: currentWorkspaceId
        });
        logSessionWindowAction('Launcher window request finished', { opened });
        return false;
    }

    async function switchGroup(groupId) {
        if (!groupId || groupId === activeGroupId) {
            return;
        }
        if (hasActiveExplorerFilesystemOperationForSessions(sessionIds)) {
            showTerminalToast('A copy or delete is finishing. Try switching sessions again shortly.', 'error');
            return;
        }
        sessionIds.forEach(cancelExplorerFilesystemUiForSession);
        // Switching sessions rebuilds the grid and discards the visible panes'
        // explorer editors — confirm any unsaved changes first.
        if (!(await confirmDiscardAllExplorerEdits('Switching sessions'))) {
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
        if (statusRefreshTimer || workspaceGone) return;
        statusRefreshTimer = setTimeout(() => {
            statusRefreshTimer = null;
            refreshStatuses();
        }, 200);
    }

    async function refreshStatuses() {
        /* Nothing to reconcile against a workspace that no longer exists — the
           window is closing, and every read would 400. */
        if (workspaceGone) return;
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
                syncPaneIdentityChrome(i, session);
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
        const cachedClosingIds = cachedGroupViews.get(groupId)?.sessionIds || [];
        const closingSessionIds = groupId === visibleGroupId ? sessionIds : cachedClosingIds;
        if (hasActiveExplorerFilesystemOperationForSessions(closingSessionIds)) {
            showTerminalToast('A copy or delete is finishing. Try closing this session again shortly.', 'error');
            return;
        }
        closingSessionIds.forEach(cancelExplorerFilesystemUiForSession);

        // Closing the visible group tears down its explorer editors; confirm
        // unsaved changes before the live-terminal close prompt.
        if (groupId === visibleGroupId && !(await confirmDiscardAllExplorerEdits('Closing this session'))) {
            return;
        }

        const decision = await confirmCloseSessionGroup(groupId);
        if (decision === CLOSE_SESSION_CANCEL) {
            return;
        }
        /* A requested save that failed must not cost the terminals it was
           meant to preserve — keep the session and leave the reason on the
           session line, the same way an explicit Save Session reports it. */
        if (decision === CLOSE_SESSION_SAVE_AND_CLOSE) {
            const saved = await saveActiveWorkspaceSession(null, { groupId });
            if (!saved?.ok) {
                return;
            }
        }
        // Past both confirmations, so this close is really happening: drop the
        // per-session/per-group entries that would otherwise outlive it.
        closingSessionIds.forEach(forgetExplorerSessionMarkdownAppearance);
        workspaceSaveTargets.delete(groupId);

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
            /* Closing the last tab of a non-default workspace removes the
               workspace with it, so the reload above legitimately fails —
               loadSessionGroups() has already started closing this window. */
            if (!workspaceGone) {
                console.error('Close session failed:', e);
            }
        }
    }

    async function _closeWindowAfterLastSession(reason = 'Last session closed') {
        logSessionWindowAction(`${reason} — closing window`);
        /* Announce before closing: the room event that would normally relay
           this change races the window teardown, and an emptied non-default
           workspace is removed globally (record *and* saved snapshot), so a
           launcher that missed it would keep listing a workspace that is gone. */
        notifyWorkspacesChanged('workspace_emptied');
        if (isPywebviewAvailable()) {
            try {
                const api = window.pywebview.api;
                const result = api.close_workspace_window
                    ? await api.close_workspace_window(currentWorkspaceId)
                    : (
                        api.close_session_window
                            ? await api.close_session_window()
                            : null
                    );
                if (result?.ok) {
                    return;
                }
            } catch (e) {
                console.error('[GridVibe Sessions] close workspace window failed:', e);
            }
        }
        window.close();
    }

    /* The coding fonts are vendored web fonts (tokens.css), so a pane whose
       terminal font is one of them can be measured against the fallback face
       and then repainted with the real one, leaving the grid a fraction off.
       One re-fit when the document's fonts have settled corrects it; panes on
       an installed font measure the same twice and see nothing. */
    if (document.fonts?.ready) {
        document.fonts.ready.then(() => {
            terminals.forEach((terminal, index) => {
                if (terminal._attached) scheduleFit(index);
            });
        });
    }

    /* ─────────────────────────────────────────────
       Resize
    ───────────────────────────────────────────── */
    window.addEventListener('resize', () => {
        terminals.forEach((terminal, index) => {
            if (terminal._attached) scheduleFit(index);
            updatePaneHeaderLayout(index);
        });
        renderResizeHandles();
    });

    /* Dismiss any open header overflow menu or shell picker on an outside click
       or Escape. */
    document.addEventListener('pointerdown', event => {
        _activeExplorerIndex = explorerPaneIndexFromTarget(event.target);
        if (!event.target.closest('.pane-shell-menu') && !event.target.closest('[data-terminal-refresh]')) {
            closeAllPaneShellMenus();
        }
        if (event.target.closest('.terminal-actions-more-btn') || event.target.closest('.terminal-container.actions-open .terminal-actions')) {
            return;
        }
        closeAllPaneActionMenus();
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeAllPaneActionMenus();
            closeAllPaneShellMenus();
        }
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
        GridVibeLifecycle.attachFlushResponder(socket, {
            workspaceId: currentWorkspaceId,
            flush: flushLivePresentation,
            metadata: async () => ({
                active_group_id: activeGroupId,
                native_zoom_factor: await getCurrentWorkspaceNativeZoomFactor(),
                topbar_visible: !document.body.classList.contains('topbar-collapsed')
            })
        });

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

            const previousStatus = target.terminal._session?.status;
            target.terminal._session = session;
            if (!target.active) {
                return;
            }

            const { index, terminal } = target;
            setStatus(index, session.status);
            syncPaneIdentityChrome(index, session);
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
                /* ...and it keeps its dimensions, which is exactly why the new
                   transport has to be told them. A relaunch (reset menu, mode
                   or shell switch, retry) replaces the PTY behind a pane that
                   was never redrawn, so `emitTerminalResize` would skip the
                   announcement as unchanged and leave a full-screen agent
                   drawing at the PTY's opening geometry until the reader
                   resized the grid by hand. Forgetting the memo makes the
                   fit's own emit land. */
                if (previousStatus && previousStatus !== 'connected') {
                    terminal._lastCols = null;
                    terminal._lastRows = null;
                }
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

        socket.on('session_groups_updated', message => {
            if (message?.workspace_id === currentWorkspaceId) {
                scheduleStatusRefresh();
            }
            /* The launcher is not in any workspace room (it has no socket), so
               its workspace list would otherwise go stale for the whole run.
               Relay the invalidation to it. */
            notifyWorkspacesChanged(message?.reason || 'session_groups_updated');
        });

        /* Voice preferences and backend availability can change from the
           launcher window (or from another workspace tab) at any time. */
        socket.on('voice_prefs_updated', () => {
            _loadVoicePrefsFromServer();
        });

        socket.on('voice_availability_updated', () => {
            _loadVoiceServiceStatus();
        });

        /* Reconcile anything missed while the socket was disconnected.
           Skipped on the first connect — initialLoad() covers boot. */
        let hadSocketConnection = false;
        socket.on('connect', () => {
            socket.emit('join_workspace', {
                workspace_id: currentWorkspaceId,
                window_id: lifecycleWindowId
            });
            if (!hadSocketConnection) {
                hadSocketConnection = true;
                return;
            }
            scheduleStatusRefresh();
            reconcileAppConfig();
        });

        /* ── Voice transcription results ── */
        socket.on('voice_result', ({ session_id, text, final: isFinal }) => {
            const index = _voiceIndexForSession(session_id);
            if (index === -1) return;

            /* Where a transcript goes is decided by the recording pane's own
               state, in one pure rule (voice-dictation.js). A pane with an
               explorer edit session bound to the capture receives the words in
               its buffer; every other pane keeps the direct terminal
               injection; a pane with neither receives nothing, so an explorer
               or browser pane can never be sent terminal_input. */
            const { target, reason } = GridVibeVoiceDictation.resolveVoiceDelivery({
                hasTerm: Boolean(terminals[index]?.term),
                edit: explorerDictationBinding(index),
                epoch: explorerDictationExpectedEpoch(index),
                isFinal: Boolean(isFinal),
                hasText: Boolean(text)
            });

            if (target === 'preview') {
                _showVoicePreview(index, text);
                return;
            }
            if (target === 'editor') {
                deliverExplorerDictation(index, text);
                _clearVoicePreview(index);
                return;
            }
            if (target === 'terminal') {
                /* A committed transcript honours Broadcast typing the same way
                   keyboard input does (ISSUE-2026-026): deliver to the recording
                   pane, then fan out to every other plain pane through the shared
                   broadcast filter. Interim previews above stay on the recording
                   pane only. */
                _sendToTerminal(index, text);
                broadcastInputToPeers(index, text);
                _clearVoicePreview(index);
                return;
            }
            noteExplorerDictationDropped(index, reason);
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
        _refreshVoiceRuntimeState();
        reconcileAppConfig();
    });
    window.addEventListener('pageshow', () => {
        _refreshVoiceRuntimeState();
        reconcileAppConfig();
    });
    window.addEventListener('pagehide', () => {
        if (socket?.connected) {
            socket.emit('leave_workspace', { workspace_id: currentWorkspaceId });
        }
    });
    document.addEventListener('fullscreenchange', updateFullscreenButton);
    /* ─────────────────────────────────────────────
       Boot
    ───────────────────────────────────────────── */
    initSurfaceMode();
    wireSessionMenu();
    wireDashboard();
    topbarPeek.attach();
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
