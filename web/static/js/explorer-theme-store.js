/* GridVibeExplorerThemeStore — the per-pane explorer light/dark override cache.

   The override object in localStorage is a *same-run cache* for state the
   server already owns: the manager holds the acknowledged explorer theme (the
   pane reports every toggle through the ordered presentation transaction), so
   this object only protects live UI state across in-page rebuilds. It is keyed
   by an ephemeral pane key (a session id, or a group:index fallback before the
   session exists), and a restart hands out new session ids — left alone the
   object would accumulate one dead entry per pane per run forever (SGP-09).
   Every write and every grid build therefore re-bounds the object to the keys
   of panes that actually exist.

   DOM-free and require()-able from Node so the bounding rules are executed by
   tests, never asserted as source text. Storage is injected by the caller; the
   page passes window.localStorage. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerThemeStore = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const STORAGE_KEY = 'gridvibe.explorerTheme';

    function normalizeTheme(theme) {
        return theme === 'dark' ? 'dark' : 'light';
    }

    /* The store is a JSON object keyed by pane key. The pre-object format was
       one bare 'light'/'dark' string applying to every pane; it migrates to an
       empty object because there is no pane key to hang it on. */
    function readStore(storage) {
        try {
            const raw = storage.getItem(STORAGE_KEY);
            if (!raw || raw === 'light' || raw === 'dark') {
                return {};
            }
            const parsed = JSON.parse(raw);
            return parsed && typeof parsed === 'object' ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function writeStore(storage, store) {
        try {
            if (!Object.keys(store).length) {
                storage.removeItem(STORAGE_KEY);
                return;
            }
            storage.setItem(STORAGE_KEY, JSON.stringify(store));
        } catch (_) {}
    }

    function boundedStore(storage, liveKeys) {
        const live = new Set(liveKeys || []);
        const store = readStore(storage);
        const bounded = {};
        Object.keys(store).forEach(key => {
            if (live.has(key)) {
                bounded[key] = normalizeTheme(store[key]);
            }
        });
        return bounded;
    }

    function hasOverride(storage, key = '') {
        const store = readStore(storage);
        return Boolean(key && Object.prototype.hasOwnProperty.call(store, key));
    }

    function getTheme(storage, key = '') {
        const store = readStore(storage);
        return normalizeTheme(
            key && Object.prototype.hasOwnProperty.call(store, key)
                ? store[key]
                : 'dark'
        );
    }

    /* Persist one pane's override, dropping every entry whose pane no longer
       exists in the same write. `liveKeys` must include `key`. */
    function saveTheme(storage, key, theme, liveKeys) {
        if (!key) {
            return;
        }
        const live = new Set(liveKeys || []);
        live.add(key);
        const store = boundedStore(storage, [...live]);
        store[key] = normalizeTheme(theme);
        writeStore(storage, store);
    }

    /* Drop entries whose pane no longer exists. Called after a grid build with
       the keys of the cards that were just built, so a restart's dead session
       ids leave the object on the next page load instead of never. */
    function pruneStore(storage, liveKeys) {
        const store = readStore(storage);
        const bounded = boundedStore(storage, liveKeys);
        if (Object.keys(bounded).length !== Object.keys(store).length) {
            writeStore(storage, bounded);
        }
        return bounded;
    }

    return {
        STORAGE_KEY,
        normalizeTheme,
        readStore,
        hasOverride,
        getTheme,
        saveTheme,
        pruneStore
    };
}));
