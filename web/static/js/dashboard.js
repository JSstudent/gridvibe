    /* ─────────────────────────────────────────────
       The agent dashboard button — one button, both pages.

       The dashboard itself is a window (`/dashboard`, dashboard-window.js),
       not a panel inside a page, and this is everything the two host pages
       need to know about it: how to open it, what its chord is, and how many
       agents are running right now.

       Three decisions worth stating:

         · **It is a window because it is about every window.** A panel hangs
           off the page that opened it, has that page's width to work in, and
           dies when that page navigates. The dashboard reads across every
           workspace and belongs to none of them, so — like the launcher — it
           gets an OS window of its own in native mode and a named tab of its
           own in the browser. The name is what makes a second press focus the
           dashboard rather than stack another copy of it.
         · **The badge is the page's only reading.** A slow poll keeps the
           number on the button honest — a badge that is only correct after
           you open the thing it labels is worse than no badge — and it stands
           down entirely while the document is hidden. Everything finer-grained
           than "how many" is the dashboard window's own business.
         · **The chord is matched on `event.code` and excludes Ctrl.** AltGr
           arrives as Ctrl+Alt on Windows, so a chord that did not exclude Ctrl
           would fire while typing an accented character; and matching the
           physical key keeps `Alt+A` on the same key whatever the layout
           prints on it. Both are the rules Alt+Q, Alt+W and Alt+X already
           follow.

       Loaded before terminals.js / launcher.js so their handlers can call in.
    ───────────────────────────────────────────── */

    const DASHBOARD_BUTTON_ID = 'dashboardBtn';
    const DASHBOARD_BADGE_ID = 'dashboardBadge';

    const AGENT_DASHBOARD_URL = '/dashboard';
    /* One dashboard, however many times it is asked for: a named target both
       reuses the browser tab and names the native window. */
    const AGENT_DASHBOARD_WINDOW_NAME = 'gridvibe-agent-dashboard';

    /* `A` for agents. Free on both pages, and it sits with the Alt navigation
       family (Alt+Q launcher, Alt+W workspace, Alt+1..9 sessions) that every
       other "go somewhere" chord already belongs to. `\ea` is unbound in
       readline, so the xterm pass-through claims nothing from a pane. */
    const DASHBOARD_CHORD_CODE = 'KeyA';
    const DASHBOARD_CHORD_LABEL = 'Alt+A';

    /* The button is the only consumer, so a slow tick is plenty. */
    const DASHBOARD_BADGE_REFRESH_MS = 30000;

    let _dashboardBadgeTimer = null;
    /* Bumped on every request. A slow answer that lands after a newer one was
       asked for is dropped rather than painted, so the badge cannot flick back
       to an older count. */
    let _dashboardRequestId = 0;

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

    function dashboardNativeApi() {
        return (typeof window !== 'undefined' ? window.pywebview?.api : null) || null;
    }

    /* The dashboard has an Alt+W of its own now, and it means what the
       launcher's means: back to the workspace you came from. That is a fact
       only the departing window knows, so it is recorded on the way out — the
       same record, through the same function, that the launcher button already
       writes when a workspace window hands over to it. A launcher press writes
       nothing: the launcher is not a workspace, and overwriting the record with
       "nowhere" is how the way back gets lost. */
    function rememberDashboardOriginWorkspace() {
        if (typeof CURRENT_WORKSPACE_ID === 'undefined'
            || typeof rememberLauncherOriginWorkspace !== 'function') {
            return;
        }
        rememberLauncherOriginWorkspace(CURRENT_WORKSPACE_ID);
    }

    /* Native first, browser second — the same order and the same fallback
       `openWorkspaceWindow` uses, because the two windows are the same kind of
       thing and a native bridge that refuses should still land somewhere. */
    async function openAgentDashboardWindow(event) {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        rememberDashboardOriginWorkspace();
        const api = dashboardNativeApi();
        if (api?.open_dashboard_window) {
            try {
                const result = await api.open_dashboard_window();
                if (result?.ok) {
                    return true;
                }
                console.error('[GridVibe Dashboard] native window refused:', result?.error || '');
            } catch (error) {
                console.error('[GridVibe Dashboard] open_dashboard_window failed:', error);
            }
        }
        const opened = window.open(AGENT_DASHBOARD_URL, AGENT_DASHBOARD_WINDOW_NAME);
        if (!opened) {
            console.error('[GridVibe Dashboard] the browser blocked the dashboard tab');
            return false;
        }
        opened.focus?.();
        return true;
    }

    function setDashboardBadge(snapshot) {
        const badge = document.getElementById(DASHBOARD_BADGE_ID);
        if (!badge) {
            return;
        }
        const count = Number(snapshot?.totals?.agents) || 0;
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.hidden = count <= 0;
    }

    async function refreshDashboardBadge() {
        const requestId = ++_dashboardRequestId;
        let snapshot = null;
        try {
            const response = await fetch('/api/dashboard');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            snapshot = await response.json();
        } catch (error) {
            console.error('[GridVibe Dashboard] badge read failed:', error);
            return;
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
            return;
        }
        _dashboardBadgeTimer = setInterval(refreshDashboardBadge, DASHBOARD_BADGE_REFRESH_MS);
    }

    function wireDashboard() {
        if (!document.getElementById(DASHBOARD_BUTTON_ID)) {
            return;
        }
        document.addEventListener?.('keydown', event => {
            if (!dashboardChordMatches(event) || dashboardChordBlocked(event.target)) {
                return;
            }
            event.preventDefault();
            openAgentDashboardWindow();
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
