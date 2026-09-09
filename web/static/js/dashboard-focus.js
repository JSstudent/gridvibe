/* A short focus lease dims other GridVibe pages while the dashboard is active.
   No native window effects or input interception: a crashed/closed dashboard
   loses its lease, and focusing any host immediately restores that page.

   Which page owns the lease is not fixed for the life of that page any more.
   The dashboard used to be a window, so a page either was it or was not; it is
   now a dialog that any host page can raise, so `dashboard` is the *initial*
   value of a flag `setDashboardActive()` moves — the page owns the lease while
   its dialog is up and hands it straight back on close. Publishing on the way
   down is the load-bearing half: without it every other window keeps its dim
   until the lease simply expires. */
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
        let owning = Boolean(dashboard);
        let channel = null;
        let lease = null;
        let heartbeat = null;
        let expiry = null;
        let disposed = false;
        let focused = Boolean(doc.hasFocus());
        try { channel = new win.BroadcastChannel(CHANNEL); } catch (_) {}

        function paint() {
            doc.body.classList.toggle('dashboard-background-blurred', Boolean(
                !owning && !focused && lease?.active && lease.until > Date.now()
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
            if (!owning) return;
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
            if (owning) publish(false);
            /* Given up as well as released, so a disposed controller reports
               what it actually holds rather than what it held last. */
            owning = false;
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
        /* The one way in and out of owning the lease. A page that is already in
           the state it is asked for does nothing — the dialog's open and close
           are each idempotent, and republishing on every no-op press would
           reset every other window's expiry for a state that has not moved. */
        function setDashboardActive(active) {
            const next = Boolean(active);
            if (disposed || owning === next) return owning;
            owning = next;
            /* Releasing publishes even from a blurred page: the lease is this
               page's to end, and leaving it to expire dims the others for
               another few seconds after the dialog has gone. */
            if (!owning) {
                win.clearInterval(heartbeat);
                heartbeat = null;
                publish(false);
                paint();
                return owning;
            }
            synchronize();
            return owning;
        }

        return { dispose, setDashboardActive };
    }

    return { start, LEASE_MS, HEARTBEAT_MS };
}));
