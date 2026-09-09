    /* ─────────────────────────────
       The agent dashboard button — one button, both pages.

       The dashboard itself is a dialog on this page (`dashboard-dialog.js`
       over `partials/agent_dashboard_dialog.html`), and this is everything the
       two host pages need to know about it: how to bring it up, what its chord
       is, and how many agents are running right now.

       Three decisions worth stating:

         · **It is a dialog, and it was a window.** A window was the wrong shape
           for the question. You ask "what is running elsewhere?" *while* you
           are somewhere, and a separate window answered it by taking you out of
           the window you were in, leaving a taskbar entry to find again and
           needing two chords of its own to get back from. It now opens over the
           page that asked and a press outside it puts that page back — which
           also means there is no second copy to stack, no named target to keep
           unique, and no pop-up for a browser to block.
         · **The badge counts agents that are *working*, not agents that are
           open.** How many agent panes exist is something the reader already
           knows — they opened them — so a badge tallying those is a number that
           is always on and never means anything. What it is for is the one
           thing you cannot see from here: an agent somewhere else has started
           doing something, or has stopped. So it reads `totals.working`, and
           **no working agent hides the badge** rather than showing a zero,
           which makes its absence a reading too. The count comes off the
           payload rather than being derived here, so the number on the button
           is a tally of the dots in the list it labels (`web/dashboard.py`).
         · **The badge is the page's only reading while the dialog is shut.** A
           slow poll keeps it honest — a badge that is only correct after you
           open the thing it labels is worse than no badge — and it stands down
           entirely while the document is hidden. Everything finer-grained than
           "how many are working" is the dialog's own business, and the dialog's
           own poll runs only while it is open.
         · **The chord is matched on `event.code` and excludes Ctrl.** AltGr
           arrives as Ctrl+Alt on Windows, so a chord that did not exclude Ctrl
           would fire while typing an accented character; and matching the
           physical key keeps `Alt+A` on the same key whatever the layout prints
           on it. Both are the rules Alt+Q, Alt+W and Alt+X already follow. It
           toggles rather than only opening, because a chord that brings a
           surface up and cannot take it away is half a control.

       Loaded before terminals.js / launcher.js so their handlers can call in.
    ───────────────────────────── */

    const DASHBOARD_BUTTON_ID = 'dashboardBtn';
    const DASHBOARD_BADGE_ID = 'dashboardBadge';

    /* `A` for agents. Free on both pages, and it sits with the Alt navigation
       family (Alt+Q launcher, Alt+W workspace, Alt+1..9 sessions) that every
       other "go somewhere" chord already belongs to. `\ea` is unbound in
       readline, so the xterm pass-through claims nothing from a pane. */
    const DASHBOARD_CHORD_CODE = 'KeyA';
    const DASHBOARD_CHORD_LABEL = 'Alt+A';

    /* The button is the only consumer, so a slow tick is plenty. */
    const DASHBOARD_BADGE_REFRESH_MS = 5000;
    const DASHBOARD_BADGE_TIMEOUT_MS = 10000;

    let _dashboardBadgeTimer = null;
    /* Bumped on every request. A slow answer that lands after a newer one was
       asked for is dropped rather than painted, so the badge cannot flick back
       to an older count. */
    let _dashboardRequestId = 0;
    let _dashboardBadgeController = null;
    let _dashboardWired = false;
    /* The cross-window dim's lease for this page, held here because this is
       what starts it. `dashboard-dialog.js` turns it on and off through
       `markDashboardFocusActive` below rather than reaching for the handle. */
    let _dashboardFocusLease = null;

    function dashboardChordMatches(event) {
        if (!event || !event.altKey || event.ctrlKey || event.metaKey) return false;
        if (event.shiftKey || event.repeat) return false;
        return event.code === DASHBOARD_CHORD_CODE;
    }

    /* Each page already answers "may a chord fire from here" for Alt+X, under
       one name; the dashboard chord obeys the same answer rather than growing
       a second, subtly different one. A page that has not defined it blocks
       nothing. */
    function dashboardChordBlocked(target) {
        if (typeof minimizeAllShortcutBlocked !== 'function') {
            return false;
        }
        try {
            return Boolean(minimizeAllShortcutBlocked(target));
        } catch (_error) {
            return false;
        }
    }

    /* While the dashboard is up, every *other* GridVibe window dims — the same
       lease the standalone window published, still worth what it was worth: a
       surface about the other windows is one the other windows step back for.
       A page that never started a lease answers this harmlessly. */
    function markDashboardFocusActive(active) {
        _dashboardFocusLease?.setDashboardActive?.(Boolean(active));
    }

    /* The button, the chord and (on the workspace page) the session menu all
       want the same thing, so they all call this rather than each deciding
       what a press means. */
    function openAgentDashboard(event) {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        if (typeof toggleAgentDashboardDialog !== 'function') {
            return false;
        }
        return toggleAgentDashboardDialog();
    }

    function setDashboardBadge(snapshot) {
        const badge = document.getElementById(DASHBOARD_BADGE_ID);
        if (!badge) {
            return;
        }
        const count = Number(snapshot?.totals?.working) || 0;
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.hidden = count <= 0;
    }

    async function refreshDashboardBadge() {
        if (document.hidden) return;
        const requestId = ++_dashboardRequestId;
        _dashboardBadgeController?.abort();
        const controller = new AbortController();
        _dashboardBadgeController = controller;
        const timeout = setTimeout(() => controller.abort(), DASHBOARD_BADGE_TIMEOUT_MS);
        let snapshot = null;
        try {
            const response = await fetch('/api/dashboard', { signal: controller.signal, cache: 'no-store' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            snapshot = await response.json();
            /* The field the badge paints, and only that one: validating
               `agents` while painting `working` is how a badge comes to draw
               `0` off a payload that never carried the number. */
            if (!Number.isInteger(snapshot?.totals?.working) || snapshot.totals.working < 0) {
                throw new Error('Invalid dashboard count');
            }
        } catch (error) {
            if (requestId === _dashboardRequestId) {
                console.error('[GridVibe Dashboard] badge read failed:', error);
                const badge = document.getElementById(DASHBOARD_BADGE_ID);
                if (badge) { badge.textContent = '?'; badge.hidden = false; }
            }
            return;
        } finally {
            clearTimeout(timeout);
            if (_dashboardBadgeController === controller) _dashboardBadgeController = null;
        }
        if (requestId !== _dashboardRequestId) {
            return;
        }
        setDashboardBadge(snapshot);
    }

    /* Restarted rather than left running, so a hidden document costs nothing
       at all: a background window polling for a badge nobody can see is the
       one cost this button has no excuse for. */
    function scheduleDashboardBadgeRefresh() {
        if (_dashboardBadgeTimer !== null) {
            clearInterval(_dashboardBadgeTimer);
            _dashboardBadgeTimer = null;
        }
        if (document.hidden) {
            ++_dashboardRequestId;
            _dashboardBadgeController?.abort();
            _dashboardBadgeController = null;
            return;
        }
        _dashboardBadgeTimer = setInterval(() => {
            if (!_dashboardBadgeController) refreshDashboardBadge();
        }, DASHBOARD_BADGE_REFRESH_MS);
    }

    function wireDashboard() {
        if (_dashboardWired || !document.getElementById(DASHBOARD_BUTTON_ID)) {
            return;
        }
        _dashboardWired = true;
        _dashboardFocusLease = window.GridVibeDashboardFocus?.start() || null;
        /* The button and the surface it opens are one feature, so one call
           wires both and a page carrying neither wires nothing. */
        if (typeof wireAgentDashboard === 'function') {
            wireAgentDashboard();
        }
        window.addEventListener?.('focus', () => refreshDashboardBadge());
        window.addEventListener?.('pagehide', () => {
            clearInterval(_dashboardBadgeTimer);
            _dashboardBadgeTimer = null;
            ++_dashboardRequestId;
            _dashboardBadgeController?.abort();
            _dashboardBadgeController = null;
        });
        window.addEventListener?.('pageshow', event => {
            if (event.persisted) { scheduleDashboardBadgeRefresh(); refreshDashboardBadge(); }
        });
        document.addEventListener?.('keydown', event => {
            if (!dashboardChordMatches(event) || dashboardChordBlocked(event.target)) {
                return;
            }
            event.preventDefault();
            openAgentDashboard();
        });
        document.addEventListener?.('visibilitychange', () => {
            scheduleDashboardBadgeRefresh();
            if (!document.hidden) {
                /* Back in front after any length of absence: read once now
                   rather than showing a count from before it was hidden until
                   the next tick comes round. */
                refreshDashboardBadge();
            }
        });
        scheduleDashboardBadgeRefresh();
        refreshDashboardBadge();
    }
