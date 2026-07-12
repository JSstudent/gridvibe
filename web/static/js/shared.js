/* GridVibeShared — helpers used by both the launcher and terminals pages.
   Loaded before the page scripts; declarations resolve as globals.
   Only byte-identical duplicates were lifted here (finding 6.4); helpers
   that drifted between the pages stay page-local until reconciled. */

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
