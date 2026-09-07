/* A short focus lease dims other GridVibe pages while the dashboard is active.
   No native window effects or input interception: a crashed/closed dashboard
   loses its lease, and focusing any host immediately restores that page. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeDashboardFocus = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';
    const CHANNEL = 'gridvibe.dashboardFocus';
    const LEASE_MS = 3500;
    const HEARTBEAT_MS = 1000;

    function start({ dashboard = false, win = window, doc = document } = {}) {
        const owner = `${Date.now()}-${Math.random()}`;
        let channel = null;
        let lease = null;
        let heartbeat = null;
        let expiry = null;
        let disposed = false;
        let focused = Boolean(doc.hasFocus());
        try { channel = new win.BroadcastChannel(CHANNEL); } catch (_) {}

        function paint() {
            doc.body.classList.toggle('dashboard-background-blurred', Boolean(
                !dashboard && !focused && lease?.active && lease.until > Date.now()
            ));
        }

        function accept(message) {
            if (!message || typeof message.owner !== 'string'
                || typeof message.active !== 'boolean' || !Number.isFinite(message.until)) return;
            if (!message.active && lease?.owner !== message.owner) return;
            if (message.active && message.until < (lease?.until || 0)) return;
            lease = { ...message, until: Math.min(message.until, Date.now() + LEASE_MS) };
            win.clearTimeout(expiry);
            paint();
            if (lease.active) expiry = win.setTimeout(paint, Math.max(0, lease.until - Date.now()));
        }

        function publish(active) {
            const message = { owner, active, until: active ? Date.now() + LEASE_MS : 0 };
            accept(message);
            try { win.localStorage.setItem(CHANNEL, JSON.stringify(message)); } catch (_) {}
            try { channel?.postMessage(message); } catch (_) {}
        }

        function synchronize() {
            if (disposed) return;
            paint();
            if (!dashboard) return;
            win.clearInterval(heartbeat);
            heartbeat = null;
            const active = focused && !doc.hidden;
            publish(active);
            if (active) heartbeat = win.setInterval(() => publish(true), HEARTBEAT_MS);
        }

        function onFocus() { focused = true; synchronize(); }
        function onBlur() { focused = false; synchronize(); }
        function onVisibility() {
            focused = !doc.hidden && doc.hasFocus();
            synchronize();
        }
        function onStorage(event) {
            if (event.key !== CHANNEL || !event.newValue) return;
            try { accept(JSON.parse(event.newValue)); } catch (_) {}
        }
        function onMessage(event) { accept(event.data); }
        function onPageHide(event) {
            focused = false;
            synchronize();
            win.clearTimeout(expiry);
            doc.body.classList.remove('dashboard-background-blurred');
            if (!event?.persisted) dispose();
        }
        function dispose() {
            if (disposed) return;
            if (dashboard) publish(false);
            disposed = true;
            win.clearInterval(heartbeat);
            win.clearTimeout(expiry);
            channel?.close();
            win.removeEventListener('focus', onFocus);
            win.removeEventListener('blur', onBlur);
            win.removeEventListener('storage', onStorage);
            win.removeEventListener('pagehide', onPageHide);
            win.removeEventListener('pageshow', onVisibility);
            doc.removeEventListener('visibilitychange', onVisibility);
            doc.body.classList.remove('dashboard-background-blurred');
        }

        if (channel) channel.onmessage = onMessage;
        win.addEventListener('focus', onFocus);
        win.addEventListener('blur', onBlur);
        win.addEventListener('storage', onStorage);
        win.addEventListener('pagehide', onPageHide);
        win.addEventListener('pageshow', onVisibility);
        doc.addEventListener('visibilitychange', onVisibility);
        try { accept(JSON.parse(win.localStorage.getItem(CHANNEL))); } catch (_) {}
        synchronize();
        return { dispose };
    }

    return { start, LEASE_MS, HEARTBEAT_MS };
}));
