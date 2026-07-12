/* GridVibeShared — helpers used by both the launcher and terminals pages.
   Loaded before the page scripts; declarations resolve as globals.

   Pages customize the theme helpers by declaring optional hook functions
   (looked up by name at call time, so declaring them after this file loads
   is fine):
     - onThemeApplied(preference, resolved)  — sync page-specific controls
     - onThemeCycled(nextTheme)              — after the toggle advances
     - onThemePersisted(data)                — after /api/app-config accepts
   Pages must call initTheme() once from their own script (after their hook
   declarations are hoisted) instead of self-initialising here.

   The saved-session modal shells stay page-local on purpose: the launcher
   dialog has import/delete modes with a footer action, the terminals dialog
   has an "open now" checkbox — they are different dialogs sharing only the
   card renderer below (finding 6.4). */

    const THEME_STORAGE_KEY = 'gridvibe.theme';
    const APP_CONFIG_BROADCAST_CHANNEL = 'gridvibe.appConfig';
    const APP_CONFIG_UPDATE_STORAGE_KEY = 'gridvibe.appConfigUpdated';
    const SAVED_SESSION_BROADCAST_CHANNEL = 'gridvibe.savedSessions';
    const SAVED_SESSION_UPDATE_STORAGE_KEY = 'gridvibe.savedSessionUpdated';
    function normalizeThemePreference(theme) {
        return ['system', 'light', 'dark'].includes(theme) ? theme : 'system';
    }
    function getStoredTheme() {
        try { return normalizeThemePreference(localStorage.getItem(THEME_STORAGE_KEY)); } catch (_) { return null; }
    }
    function getSystemTheme() {
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    function resolveTheme(theme) {
        const preference = normalizeThemePreference(theme);
        return preference === 'system' ? getSystemTheme() : preference;
    }
    function getConnectionModeLabel(mode) {
        return mode === 'wsl' ? 'Local Repo' : 'SSH';
    }
    function isAbsoluteDirectory(path, mode) {
        const trimmed = String(path || '').trim();
        if (!trimmed) {
            return false;
        }
        if (/^[A-Za-z]:[\\/]/.test(trimmed)) {
            return true;
        }
        if (trimmed.startsWith('\\\\')) {
            return true;
        }
        if (trimmed.startsWith('/') || trimmed.startsWith('~')) {
            return true;
        }
        return mode === 'wsl' && trimmed.startsWith('\\');
    }
    function normalizeComparableDirectory(path, mode) {
        const trimmed = String(path || '').trim();
        if (!trimmed) {
            return '';
        }

        let normalized = trimmed.replace(/\\/g, '/');
        if (normalized !== '/') {
            normalized = normalized.replace(/\/+$/, '');
        }
        if (!normalized && trimmed.startsWith('/')) {
            normalized = '/';
        }
        if (mode === 'wsl') {
            normalized = normalized.toLowerCase();
        }
        return normalized;
    }
    function resolveTerminalDirectory(defaultDir, terminalDir, mode) {
        const baseDirectory = String(defaultDir || '').trim();
        const rawDirectory = String(terminalDir || '').trim();
        if (!rawDirectory) {
            return baseDirectory;
        }
        if (!baseDirectory || !isAbsoluteDirectory(rawDirectory, mode)) {
            return rawDirectory;
        }

        const normalizedDirectory = normalizeComparableDirectory(rawDirectory, mode);
        const normalizedBase = normalizeComparableDirectory(baseDirectory, mode);
        if (
            normalizedDirectory === normalizedBase
            || normalizedBase === '/'
            || normalizedDirectory.startsWith(`${normalizedBase}/`)
        ) {
            return rawDirectory;
        }

        throw new Error('Step 3 directories must stay inside the Step 2 default working directory.');
    }
    function buildLaunchDirectory(defaultDir, terminalDir, mode) {
        const baseDirectory = String(defaultDir || '').trim();
        const rawDirectory = String(terminalDir || '').trim();
        if (!rawDirectory) {
            return baseDirectory;
        }
        if (!baseDirectory) {
            return rawDirectory;
        }
        if (!isAbsoluteDirectory(rawDirectory, mode)) {
            return joinDirectories(baseDirectory, rawDirectory, mode);
        }
        return resolveTerminalDirectory(baseDirectory, rawDirectory, mode);
    }
    function getDirectoryName(path) {
        const trimmed = String(path || '').trim().replace(/[\\/]+$/, '');
        if (!trimmed) {
            return '';
        }

        const parts = trimmed.split(/[\\/]/).filter(Boolean);
        return parts[parts.length - 1] || trimmed;
    }
    function joinDirectories(baseDir, childDir, mode) {
        const rawBase = String(baseDir || '').trim();
        const base = rawBase === '/' ? '/' : rawBase.replace(/[\\/]+$/, '');
        const child = String(childDir || '').trim().replace(/^[\\/]+/, '');
        if (!base) {
            return child;
        }
        if (!child) {
            return base;
        }

        const separator = mode === 'wsl' && base.includes('\\') && !base.includes('/') ? '\\' : '/';
        const normalizedChild = child.replace(/[\\/]+/g, separator);
        return `${base}${separator}${normalizedChild}`;
    }
    function escHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function syncNativeTheme(resolvedTheme) {
        try {
            const bridge = window.pywebview?.api;
            if (bridge?.set_native_theme) {
                bridge.set_native_theme(resolvedTheme).catch(error => {
                    console.error('[GridVibe] native theme sync failed:', error);
                });
            }
        } catch (error) {
            console.error('[GridVibe] native theme sync failed:', error);
        }
    }
    function applyTheme(theme) {
        const preference = normalizeThemePreference(theme);
        const resolved = resolveTheme(preference);
        document.documentElement.setAttribute('data-theme', resolved);
        document.documentElement.setAttribute('data-theme-preference', preference);
        try { localStorage.setItem(THEME_STORAGE_KEY, preference); } catch (_) {}
        syncNativeTheme(resolved);
        if (typeof onThemeApplied === 'function') {
            onThemeApplied(preference, resolved);
        }
    }
    async function persistThemePreference(theme) {
        try {
            const response = await fetch('/api/app-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    appearance: {
                        theme: normalizeThemePreference(theme)
                    }
                })
            });
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            if (data?.appearance?.theme) {
                applyTheme(data.appearance.theme);
                if (typeof onThemePersisted === 'function') {
                    onThemePersisted(data);
                }
            }
        } catch (_error) {}
    }
    function cycleTheme() {
        const current = getStoredTheme() || 'system';
        const nextTheme =
            current === 'system' ? 'light'
            : current === 'light' ? 'dark'
            : 'system';
        applyTheme(nextTheme);
        if (typeof onThemeCycled === 'function') {
            onThemeCycled(nextTheme);
        }
        persistThemePreference(nextTheme);
    }
    function initTheme() {
        applyTheme(getStoredTheme() || 'system');
        window.addEventListener('pywebviewready', () => {
            syncNativeTheme(document.documentElement.getAttribute('data-theme') || resolveTheme(getStoredTheme() || 'system'));
        });
        const mediaQuery = window.matchMedia('(prefers-color-scheme: light)');
        const listener = () => {
            const themePreference = document.documentElement.getAttribute('data-theme-preference') || getStoredTheme() || 'system';
            if (themePreference === 'system') {
                applyTheme('system');
            }
        };
        if (typeof mediaQuery.addEventListener === 'function') {
            mediaQuery.addEventListener('change', listener);
        } else if (typeof mediaQuery.addListener === 'function') {
            mediaQuery.addListener(listener);
        }
    }
    function buildSavedSessionTags(session, currentSavedSessionId) {
        const tags = [];
        const sessionId = String(session?.id || '').trim();
        if (session?.is_default) {
            tags.push('<span class="saved-session-tag">Default</span>');
        }
        if (sessionId && String(currentSavedSessionId || '').trim() === sessionId) {
            tags.push('<span class="saved-session-tag current">Current</span>');
        }
        return tags.join('');
    }
    function buildSavedSessionCard(session, options = {}) {
        const selectable = Boolean(options.selectable);
        const content = `
            <span class="saved-session-content">
                <span class="saved-session-topline">
                    <span class="saved-session-name">${escHtml(session.name)}</span>
                    <span class="saved-session-tags">${buildSavedSessionTags(session, options.currentSavedSessionId)}</span>
                </span>
                <span class="saved-session-meta">
                    ${escHtml(getConnectionModeLabel(session.connection_mode))} • ${escHtml(String(session.terminal_count))} terminal${session.terminal_count === 1 ? '' : 's'} • ${escHtml(session.layout)}
                </span>
                <span class="saved-session-id">Updated ${escHtml(session.updated_at)} • ${escHtml(session.id)}</span>
            </span>
        `;

        if (selectable) {
            return `
                <label class="saved-session-item saved-session-selectable" data-session-id="${escHtml(session.id)}">
                    <input type="checkbox" class="saved-session-checkbox" value="${escHtml(session.id)}">
                    ${content}
                </label>
            `;
        }

        return `
            <button type="button" class="saved-session-item" data-session-id="${escHtml(session.id)}">
                ${content}
            </button>
        `;
    }
