    /* ─────────────────────────────────────────────
       The activity dashboard — one button, one panel, both pages.

       GridVibe can have several workspace windows open, each with several
       session tabs, each with several panes, and until now the only way to
       find out what was running anywhere was to go and look in every window.
       This is that answer in one place: every live workspace, the sessions
       inside it, the panes inside those, and — for the panes running an agent
       — what the agent is announcing and whether it is doing anything.

       Four decisions worth stating, because each one is the reason something
       here is *not* built the obvious way:

         · **One request, not N+1.** `/api/dashboard` composes the whole tree
           server-side. A page that asked for workspaces, then groups per
           workspace, then panes per group would render a tree assembled out
           of several different moments, and the moments that disagree are
           exactly the ones worth showing.
         · **Naming is not the server's.** The payload states what a pane *is*;
           what to call it comes from agent-identity.js — the same module the
           pane header uses, so the dashboard row and the pane it points at can
           never disagree about which agent that is.
         · **Polling belongs to the panel, not to the page.** The fast poll
           runs only while the panel is open. A slow one keeps the button's
           badge honest, because a badge that is only correct after you open
           the thing it labels is worse than no badge — and it stands down
           entirely while the document is hidden.
         · **The progress reading is two independent things.** Every pane has a
           state (working / idle / nothing observed), derived from output if
           the agent says nothing else; only an agent that speaks the OSC 9;4
           progress sequence also has a percentage. They are drawn separately
           so a missing percentage never reads as "stalled at 0%".

       Loaded before terminals.js / launcher.js so their handlers can call in.
    ───────────────────────────────────────────── */

    const DASHBOARD_ROOT_ID = 'dashboardRoot';
    const DASHBOARD_BUTTON_ID = 'dashboardBtn';
    const DASHBOARD_PANEL_ID = 'dashboardPanel';
    const DASHBOARD_BADGE_ID = 'dashboardBadge';

    /* While it is showing, a dashboard that lags the thing it describes is
       just a screenshot. While it is closed, the badge is the only consumer
       and a slow tick is plenty. */
    const DASHBOARD_OPEN_REFRESH_MS = 4000;
    const DASHBOARD_BADGE_REFRESH_MS = 30000;

    let _dashboardTimer = null;
    /* Bumped on every request. A slow answer that lands after a newer one was
       asked for is dropped rather than painted, so a panel cannot flick back
       to an older reading. */
    let _dashboardRequestId = 0;
    let _dashboardLatest = null;
    let _releaseDashboardDismissers = null;

    function dashboardIsOpen() {
        return Boolean(document.getElementById(DASHBOARD_ROOT_ID)?.classList?.contains('open'));
    }

    function dashboardAgentOptions() {
        return typeof AGENT_OPTIONS === 'undefined' || !Array.isArray(AGENT_OPTIONS) ? [] : AGENT_OPTIONS;
    }

    function dashboardIdentity() {
        return typeof window !== 'undefined' ? window.GridVibeAgentIdentity : undefined;
    }

    /* The workspace this window *is*, or '' on the launcher, which is not in
       one. It decides whether a row acts here or opens another window. */
    function dashboardCurrentWorkspaceId() {
        return typeof currentWorkspaceId === 'string' ? currentWorkspaceId : '';
    }

    /* ── Staying inside the window ──
       Which side the panel hangs from is each page's own statement
       (`--dash-anchor-*`), because it is a layout fact about where its button
       is. What is *not* a page's business is what happens when that side runs
       out of room: this panel is far wider than the shortcut list, so a button
       near either edge of the window can put a correctly anchored panel partly
       outside it, and a wrong anchor puts it entirely outside. So the anchor
       decides the side and this decides whether the result fits.

       DOM-free: measured numbers in, a nudge and a height cap out. */

    /* Kept off the window edge rather than flush against it -- a panel touching
       the frame reads as clipped even when every row is there. */
    const DASHBOARD_VIEWPORT_MARGIN = 8;

    /* A panel below this is not worth showing; a window too short for it gets a
       scrolling panel instead of a sliver. */
    const DASHBOARD_MIN_PANEL_HEIGHT = 140;

    function dashboardPanelFit({
        panelLeft,
        panelRight,
        panelTop,
        panelBottom,
        viewportWidth,
        viewportHeight,
        opensUp,
        styleCap,
        margin = DASHBOARD_VIEWPORT_MARGIN
    }) {
        let shiftX = 0;
        const overflowRight = panelRight - (viewportWidth - margin);
        if (overflowRight > 0) {
            shiftX -= overflowRight;
        }
        /* Left is corrected second, and against the already-shifted position,
           so a panel wider than the window ends up pinned to the left margin
           rather than centred: its rows are read from their left edge, and
           losing the right of a row costs less than losing the start of one. */
        const overflowLeft = margin - (panelLeft + shiftX);
        if (overflowLeft > 0) {
            shiftX += overflowLeft;
        }

        /* Measured from the panel's own anchored edge, not from the button:
           the gap between the two is the page's (`calc(100% + 6px)`), and a
           budget taken from the button spends that gap twice. Whichever edge
           the anchor pinned holds still however tall the panel is, so it is the
           one fixed point either direction can be measured from. */
        const available = (opensUp ? panelBottom : viewportHeight - panelTop) - margin;
        const capped = Number.isFinite(styleCap) ? Math.min(styleCap, available) : available;
        return {
            shiftX,
            maxHeight: Math.max(DASHBOARD_MIN_PANEL_HEIGHT, capped)
        };
    }

    /* The adapter: measure, then apply. Cleared before measuring so the numbers
       read back are the stylesheet's own and not what the previous fit left
       behind, which is what makes it safe to re-run on every render -- and it
       has to be, because the first paint is a one-line placeholder and the tree
       that replaces it is a different size entirely. */
    function fitDashboardPanel() {
        const button = document.getElementById(DASHBOARD_BUTTON_ID);
        const panel = document.getElementById(DASHBOARD_PANEL_ID);
        if (
            !button
            || !panel
            || typeof window === 'undefined'
            || typeof window.getComputedStyle !== 'function'
            || typeof panel.getBoundingClientRect !== 'function'
            || typeof button.getBoundingClientRect !== 'function'
        ) {
            return;
        }
        panel.style.transform = '';
        panel.style.maxHeight = '';
        const buttonRect = button.getBoundingClientRect();
        const panelRect = panel.getBoundingClientRect();
        const fit = dashboardPanelFit({
            panelLeft: panelRect.left,
            panelRight: panelRect.right,
            panelTop: panelRect.top,
            panelBottom: panelRect.bottom,
            viewportWidth: window.innerWidth,
            viewportHeight: window.innerHeight,
            opensUp: panelRect.bottom <= buttonRect.top + 1,
            styleCap: parseFloat(window.getComputedStyle(panel).maxHeight)
        });
        if (fit.shiftX) {
            panel.style.transform = `translateX(${Math.round(fit.shiftX)}px)`;
        }
        panel.style.maxHeight = `${Math.round(fit.maxHeight)}px`;
    }

    /* ── The reading, as words ── */

    function dashboardWorkspaceLabel(workspace, index) {
        if (typeof workspaceDisplayLabel === 'function') {
            return workspaceDisplayLabel(workspace, index);
        }
        return String(workspace?.label || '') || `Workspace ${index + 1}`;
    }

    function dashboardPaneTitle(pane, index) {
        const identity = dashboardIdentity();
        if (!identity) {
            return String(pane?.title || '') || `Terminal ${index + 1}`;
        }
        return identity.paneDisplayTitle(pane, index, dashboardAgentOptions());
    }

    function dashboardPaneKind(pane) {
        const identity = dashboardIdentity();
        return identity ? identity.paneKindForSession(pane) : 'terminal';
    }

    /* "4m" rather than "247 seconds": the number is only ever read as "has it
       been long", and a rounded one says that faster. */
    function dashboardIdleLabel(seconds) {
        const value = Number(seconds);
        if (!Number.isFinite(value) || value < 0) {
            return '';
        }
        if (value < 60) {
            return `${Math.round(value)}s`;
        }
        if (value < 3600) {
            return `${Math.round(value / 60)}m`;
        }
        return `${Math.round(value / 3600)}h`;
    }

    /* What the row says under its name. An agent that announced a title is
       telling you what it is doing, and that beats repeating its directory;
       everything else falls back to where the pane points. */
    function dashboardPaneNote(pane) {
        const announced = String(pane?.activity?.title || '').trim();
        if (announced) {
            return announced;
        }
        const directory = String(pane?.directory || '').trim();
        const host = String(pane?.host || '').trim();
        if (directory && host) {
            return `${host}: ${directory}`;
        }
        return directory || host;
    }

    function dashboardStateWord(activity) {
        const state = String(activity?.state || '');
        if (state === 'working') {
            return 'Working';
        }
        if (state === 'idle') {
            const idle = dashboardIdleLabel(activity?.idle_seconds);
            return idle ? `Idle ${idle}` : 'Idle';
        }
        return 'No output yet';
    }

    /* The dot always; the bar only when a percentage was actually published,
       or when the agent said it is busy without saying how far along. */
    function dashboardActivityHtml(activity) {
        if (!activity) {
            return '';
        }
        const state = String(activity.state || 'unknown');
        const progressState = String(activity.progress_state || '');
        const value = Number(activity.progress_value) || 0;
        const determinate = progressState === 'normal' && value > 0;
        const indeterminate = !determinate
            && state === 'working'
            && ['indeterminate', 'warning', 'normal'].includes(progressState);
        const bar = determinate || indeterminate
            ? `
                <span class="dashboard-progress dashboard-progress-${escHtml(progressState || 'normal')}">
                    <span
                        class="dashboard-progress-fill${indeterminate ? ' is-indeterminate' : ''}"
                        ${determinate ? `style="width:${Math.max(0, Math.min(100, value))}%"` : ''}
                    ></span>
                </span>
                ${determinate ? `<span class="dashboard-progress-value">${value}%</span>` : ''}
            `
            : '';
        return `
            <span class="dashboard-activity dashboard-state-${escHtml(state)}" title="${escHtml(dashboardStateWord(activity))}">
                <span class="dashboard-state-dot" aria-hidden="true"></span>
                ${bar}
            </span>
        `;
    }

    /* One row shape for all three levels: a name line, an optional note under
       it, an optional tag, and an optional activity reading on the right. */
    function dashboardRowHtml({ kind, key, label, note = '', tag = '', activity = null, dataset = {} }) {
        const attributes = Object.entries(dataset)
            .map(([name, value]) => ` data-${name}="${escHtml(value)}"`)
            .join('');
        return `
            <button
                type="button"
                class="dashboard-row dashboard-row-${escHtml(kind)}"
                data-dashboard-action="${escHtml(kind)}"
                data-dashboard-key="${escHtml(key)}"
                ${attributes}
            >
                <span class="dashboard-row-main">
                    <span class="dashboard-row-name">
                        <span class="dashboard-row-label">${escHtml(label)}</span>
                        ${tag ? `<span class="dashboard-tag${tag === 'active' ? ' dashboard-tag-active' : ''}">${escHtml(tag)}</span>` : ''}
                    </span>
                    ${note ? `<span class="dashboard-row-note">${escHtml(note)}</span>` : ''}
                </span>
                ${dashboardActivityHtml(activity)}
            </button>
        `;
    }

    function dashboardPaneRowHtml(pane, index) {
        const kind = dashboardPaneKind(pane);
        return dashboardRowHtml({
            kind: 'pane',
            key: `pane:${pane.session_id}`,
            label: dashboardPaneTitle(pane, index),
            note: dashboardPaneNote(pane),
            /* Only the kinds that are not an ordinary terminal wear one: a tag
               on every row is a column, and a column of "terminal" says
               nothing. */
            tag: kind === 'terminal' ? '' : kind,
            activity: pane.activity,
            dataset: {
                'workspace-id': pane.workspace_id || '',
                'group-id': pane.group_id || '',
                'session-id': pane.session_id || '',
                'pane-index': String(index)
            }
        });
    }

    function dashboardSessionRowHtml(group) {
        const panes = Array.isArray(group.panes) ? group.panes : [];
        const agents = panes.filter(pane => dashboardPaneKind(pane) === 'agent').length;
        const note = agents
            ? `${panes.length} pane${panes.length === 1 ? '' : 's'} · ${agents} agent${agents === 1 ? '' : 's'}`
            : `${panes.length} pane${panes.length === 1 ? '' : 's'}`;
        return `
            ${dashboardRowHtml({
                kind: 'session',
                key: `session:${group.group_id}`,
                label: String(group.name || group.group_id || ''),
                note,
                tag: group.is_active ? 'active' : '',
                dataset: {
                    'workspace-id': group.workspace_id || '',
                    'group-id': group.group_id || ''
                }
            })}
            ${panes.map(dashboardPaneRowHtml).join('')}
        `;
    }

    function dashboardWorkspaceHtml(workspace, index, currentId) {
        const groups = Array.isArray(workspace.groups) ? workspace.groups : [];
        return `
            <div class="dashboard-workspace">
                ${dashboardRowHtml({
                    kind: 'workspace',
                    key: `workspace:${workspace.workspace_id}`,
                    label: dashboardWorkspaceLabel(workspace, index),
                    note: `${groups.length} session${groups.length === 1 ? '' : 's'} · ${workspace.pane_count} pane${workspace.pane_count === 1 ? '' : 's'}`,
                    tag: workspace.workspace_id === currentId ? 'this window' : '',
                    dataset: { 'workspace-id': workspace.workspace_id || '' }
                })}
                ${groups.map(dashboardSessionRowHtml).join('')}
            </div>
        `;
    }

    function dashboardPanelHtml(snapshot) {
        const workspaces = Array.isArray(snapshot?.workspaces) ? snapshot.workspaces : [];
        const totals = snapshot?.totals || {};
        const currentId = dashboardCurrentWorkspaceId();
        const body = workspaces.length
            ? workspaces.map((workspace, index) => dashboardWorkspaceHtml(workspace, index, currentId)).join('')
            : '<div class="dashboard-empty">Nothing is running yet.</div>';
        return `
            <div class="dashboard-head">
                <span class="dashboard-title">Active work</span>
                <span class="dashboard-totals">
                    ${Number(totals.workspaces) || 0} workspaces ·
                    ${Number(totals.sessions) || 0} sessions ·
                    ${Number(totals.agents) || 0} agents
                </span>
            </div>
            ${body}
        `;
    }

    function dashboardErrorHtml(message) {
        return `
            <div class="dashboard-head"><span class="dashboard-title">Active work</span></div>
            <div class="dashboard-error">${escHtml(message)}</div>
        `;
    }

    /* ── The page ── */

    function setDashboardBadge(snapshot) {
        const badge = document.getElementById(DASHBOARD_BADGE_ID);
        if (!badge) {
            return;
        }
        const count = Number(snapshot?.totals?.agents) || 0;
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.hidden = count <= 0;
    }

    /* A render replaces the row the pointer or the caret was on, so the row is
       found again by its key afterwards. Without it a poll landing mid-read
       would drop the caret back to the top of the document every few seconds. */
    function renderDashboardPanel(html) {
        const panel = document.getElementById(DASHBOARD_PANEL_ID);
        if (!panel) {
            return;
        }
        const focusedKey = panel.contains?.(document.activeElement)
            ? document.activeElement?.dataset?.dashboardKey || ''
            : '';
        panel.innerHTML = html;
        if (focusedKey) {
            panel.querySelector?.(`[data-dashboard-key="${focusedKey}"]`)?.focus?.();
        }
        fitDashboardPanel();
    }

    async function refreshDashboard() {
        const requestId = ++_dashboardRequestId;
        let snapshot = null;
        let failure = '';
        try {
            const response = await fetch('/api/dashboard');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            snapshot = await response.json();
        } catch (error) {
            console.error('[GridVibe Dashboard] load failed:', error);
            failure = 'Could not read what is running.';
        }
        if (requestId !== _dashboardRequestId) {
            return;
        }
        if (snapshot) {
            _dashboardLatest = snapshot;
            setDashboardBadge(snapshot);
        }
        if (!dashboardIsOpen()) {
            return;
        }
        renderDashboardPanel(snapshot ? dashboardPanelHtml(snapshot) : dashboardErrorHtml(failure));
    }

    /* One timer, two cadences. Restarted on every state change rather than
       kept running at the faster rate, so a closed panel costs one request
       every half minute and a hidden document costs none at all. */
    function scheduleDashboardRefresh() {
        if (_dashboardTimer !== null) {
            clearInterval(_dashboardTimer);
            _dashboardTimer = null;
        }
        if (document.hidden) {
            return;
        }
        const interval = dashboardIsOpen() ? DASHBOARD_OPEN_REFRESH_MS : DASHBOARD_BADGE_REFRESH_MS;
        _dashboardTimer = setInterval(refreshDashboard, interval);
    }

    function armDashboardDismissers() {
        _releaseDashboardDismissers?.();
        const onPress = event => {
            if (!document.getElementById(DASHBOARD_ROOT_ID)?.contains?.(event.target)) {
                closeDashboard();
            }
        };
        const onKeydown = event => {
            if (event.key === 'Escape') {
                closeDashboard();
            }
        };
        const onBlur = () => closeDashboard();
        document.addEventListener?.('mousedown', onPress);
        document.addEventListener?.('keydown', onKeydown);
        window?.addEventListener?.('blur', onBlur);
        _releaseDashboardDismissers = () => {
            document.removeEventListener?.('mousedown', onPress);
            document.removeEventListener?.('keydown', onKeydown);
            window?.removeEventListener?.('blur', onBlur);
            _releaseDashboardDismissers = null;
        };
    }

    function closeDashboard() {
        const root = document.getElementById(DASHBOARD_ROOT_ID);
        const button = document.getElementById(DASHBOARD_BUTTON_ID);
        const panel = document.getElementById(DASHBOARD_PANEL_ID);
        if (!root?.classList?.contains('open')) {
            return;
        }
        const hadFocus = Boolean(panel?.contains?.(document.activeElement));
        _releaseDashboardDismissers?.();
        root.classList.remove('open');
        button?.setAttribute('aria-expanded', 'false');
        /* The rows are dropped with the panel: a closed dashboard holding last
           week's tree is a stale answer waiting to be believed. */
        if (panel) {
            panel.innerHTML = '';
            panel.style.transform = '';
            panel.style.maxHeight = '';
        }
        scheduleDashboardRefresh();
        if (hadFocus) {
            button?.focus?.();
        }
    }

    function toggleDashboard(event) {
        event?.preventDefault();
        event?.stopPropagation();
        const root = document.getElementById(DASHBOARD_ROOT_ID);
        const button = document.getElementById(DASHBOARD_BUTTON_ID);
        const panel = document.getElementById(DASHBOARD_PANEL_ID);
        if (!root || !button || !panel) {
            return;
        }
        if (root.classList.contains('open')) {
            closeDashboard();
            return;
        }
        /* The last reading goes up immediately so the panel is never an empty
           box waiting on a request; the refresh below replaces it. */
        panel.innerHTML = _dashboardLatest
            ? dashboardPanelHtml(_dashboardLatest)
            : '<div class="dashboard-empty">Reading…</div>';
        root.classList.add('open');
        button.setAttribute('aria-expanded', 'true');
        /* After the class, because a panel still `display: none` measures
           nothing, and before focus, so the caret lands in a box that is
           already where it will be read. */
        fitDashboardPanel();
        armDashboardDismissers();
        scheduleDashboardRefresh();
        refreshDashboard();
        panel.focus?.();
    }

    /* ── What a row does ──
       A row in this window's own workspace acts here; every other row opens
       (or focuses) the window that owns it. One rule, three depths. */
    function focusDashboardPane(index) {
        const paneIndex = Number(index);
        if (!Number.isInteger(paneIndex) || paneIndex < 0) {
            return;
        }
        document.getElementById(`tc-${paneIndex}`)?.scrollIntoView?.({ block: 'nearest' });
        if (typeof terminals !== 'undefined' && Array.isArray(terminals)) {
            terminals[paneIndex]?.term?.focus?.();
        }
    }

    async function openDashboardTarget({ workspaceId, groupId = '', paneIndex = '' }) {
        const resolvedWorkspaceId = String(workspaceId || '');
        if (!resolvedWorkspaceId) {
            return;
        }
        closeDashboard();
        const isHere = resolvedWorkspaceId === dashboardCurrentWorkspaceId();
        if (isHere && typeof switchGroup === 'function') {
            if (groupId && groupId !== (typeof activeGroupId === 'undefined' ? '' : activeGroupId)) {
                await switchGroup(groupId);
            }
            if (paneIndex !== '') {
                focusDashboardPane(paneIndex);
            }
            return;
        }
        if (typeof openWorkspaceWindow === 'function') {
            await openWorkspaceWindow(resolvedWorkspaceId, { groupId });
        }
    }

    function wireDashboard() {
        const panel = document.getElementById(DASHBOARD_PANEL_ID);
        if (!panel) {
            return;
        }
        /* Delegated: every row is rebuilt on every poll, so a listener on a row
           would not outlive the reading that drew it. */
        panel.addEventListener('click', event => {
            const row = event.target?.closest?.('[data-dashboard-action]');
            if (!row) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            openDashboardTarget({
                workspaceId: row.dataset.workspaceId,
                groupId: row.dataset.groupId || '',
                paneIndex: row.dataset.paneIndex === undefined ? '' : row.dataset.paneIndex
            });
        });
        document.addEventListener?.('visibilitychange', () => {
            if (document.hidden) {
                closeDashboard();
            }
            scheduleDashboardRefresh();
        });
        scheduleDashboardRefresh();
        refreshDashboard();
    }
