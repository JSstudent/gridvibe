    /* ─────────────────────────────────────────────
       The agent dashboard window — every agent running, wherever it runs.

       GridVibe can have several workspace windows open, each with several
       session tabs, each with several panes, and until now the only way to
       find out which agents were working anywhere was to go and look in every
       window. This is that answer in one window: every workspace holding an
       agent, the sessions inside it, and the agents inside those — what each
       one is announcing and whether it is doing anything.

       Five decisions worth stating, because each is the reason something here
       is *not* built the obvious way:

         · **One request, not N+1.** `/api/dashboard` composes the whole tree
           server-side, already reduced to agents. A page that asked for
           workspaces, then groups per workspace, then panes per group would
           render a tree assembled out of several different moments, and the
           moments that disagree are exactly the ones worth showing.
         · **Sections, not a list.** A window has room a dropdown never had, so
           the three levels are three different things on the page rather than
           three indent depths: a workspace is a titled band, a session is a
           card inside it, an agent is a row inside that. The nesting is then
           visible without counting pixels of padding — which was the whole
           complaint about the panel this replaced.
         · **Naming is not the server's.** The payload states what a pane *is*;
           what to call it, and what to tag it with, come from
           agent-identity.js — the same module the pane header uses, so the
           dashboard row and the pane it points at can never disagree about
           which agent that is or what it is running on.
         · **One block per agent, not one per pane.** Four Claude panes in one
           session used to be four identical name-and-tag headings with one
           useful line each. The agent is named once, with its own mark and the
           shell it runs on set small beside it, and what the reader came for —
           what each of those panes is *actually doing* — gets the whole width
           and the larger type underneath.
         · **The mark says which agent, not that this is an agent.** Every row
           carrying the same glyph told the reader nothing they could not
           already see, so the mark comes from agent-glyphs.js, keyed by the
           same registry key the name is. An agent GridVibe has not drawn falls
           back to the terminal chip rather than to nothing.
         · **A row lands on its own pane.** A row names a workspace, a session
           tab and one pane; the native bridge raises an already-open workspace
           window without retargeting it, so the last two used to be dropped and
           every row arrived wherever that window was left. The target is stored
           through workspaces.js and claimed by the window that arrives.
         · **A repaint that changes nothing is not performed.** The poll runs
           every few seconds and most ticks say the same thing; re-writing the
           tree anyway would drop the caret, kill a text selection and jump the
           scroll for no new information. The composed markup is compared with
           what is already on screen and an identical answer is dropped.
         · **The progress reading is two independent things.** Every agent has
           a state (working / idle / nothing observed), derived from output if
           the agent says nothing else; only one that speaks the OSC 9;4
           progress sequence also has a percentage. They are drawn separately
           so a missing percentage never reads as "stalled at 0%".

       Loaded after shared.js and workspaces.js, whose `openWorkspaceWindow` is
       how a row reaches the window that owns it, and after agent-identity.js
       and agent-glyphs.js, which are what a row is named and marked from.
    ───────────────────────────────────────────── */

    const AGENT_DASHBOARD_BODY_ID = 'agentDashboardBody';
    const AGENT_DASHBOARD_TOTALS_ID = 'agentDashboardTotals';
    const AGENT_DASHBOARD_NOTICE_ID = 'agentDashboardNotice';

    /* A dashboard that lags the thing it describes is just a screenshot. */
    const AGENT_DASHBOARD_REFRESH_MS = 4000;


    let _agentDashboardTimer = null;
    /* Bumped on every request. A slow answer that lands after a newer one was
       asked for is dropped rather than painted, so the page cannot flick back
       to an older reading. */
    let _agentDashboardRequestId = 0;
    /* What is on screen right now, so an unchanged answer costs no repaint. */
    let _agentDashboardPainted = '';

    function dashboardAgentOptions() {
        return typeof AGENT_OPTIONS === 'undefined' || !Array.isArray(AGENT_OPTIONS) ? [] : AGENT_OPTIONS;
    }

    function dashboardIdentity() {
        return typeof window !== 'undefined' ? window.GridVibeAgentIdentity : undefined;
    }

    function dashboardGlyphs() {
        return typeof window !== 'undefined' ? window.GridVibeAgentGlyphs : undefined;
    }

    /* ── The reading, as words ── */

    function dashboardWorkspaceLabel(workspace, index) {
        if (typeof workspaceDisplayLabel === 'function') {
            return workspaceDisplayLabel(workspace, index);
        }
        return String(workspace?.label || '') || `Workspace ${index + 1}`;
    }

    /* The pane's own index in its group, which the payload carries across the
       agent filter precisely so the "Terminal N" fallback still counts from
       the position the window would show. */
    function dashboardPaneTitle(pane) {
        const index = Number(pane?.index) || 0;
        const identity = dashboardIdentity();
        if (!identity) {
            return String(pane?.title || '') || `Terminal ${index + 1}`;
        }
        return identity.paneDisplayTitle(pane, index, dashboardAgentOptions());
    }

    function dashboardTransportLabel(pane) {
        const identity = dashboardIdentity();
        return identity ? identity.paneTransportLabel(pane) : '';
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

    /* The agent this pane runs, in the registry's own prose — the heading its
       panes are gathered under. Never empty: a custom agent with nothing to
       name is still an agent, and a group with a blank heading would read as a
       rendering fault rather than as a fact about the pane. */
    function dashboardAgentName(pane) {
        const identity = dashboardIdentity();
        const name = identity ? identity.agentDisplayName(pane, dashboardAgentOptions()) : '';
        return name || 'Agent';
    }

    /* Which mark the heading wears, and the mark itself. The glyph module owns
       what an agent GridVibe has not drawn falls back to; this only hands it
       the same registry key the name came from. */
    function dashboardAgentKey(pane) {
        const identity = dashboardIdentity();
        return identity ? identity.agentKeyForSession(pane) : '';
    }

    function dashboardAgentGlyphKey(pane) {
        const glyphs = dashboardGlyphs();
        return glyphs ? glyphs.agentGlyphKey(dashboardAgentKey(pane)) : 'default';
    }

    function dashboardAgentGlyphHtml(pane) {
        const glyphs = dashboardGlyphs();
        return glyphs ? glyphs.agentGlyphMarkup(dashboardAgentKey(pane)) : '';
    }

    /* The one line a pane gets, and the reason this window exists: *which*
       conversation this is. Three sources, in the order of how much each was
       chosen by somebody.

       A title the user typed always wins — the rule agent-identity.js applies
       to the pane header, and this is the only place that name still appears
       now that the heading above says which agent the pane runs. Otherwise the
       agent is announcing what it is working on, and that is the answer.
       Otherwise the line falls back to where the pane points.

       The host is only worth naming for a remote pane. A local pane's `host`
       field holds the shell it started — "PowerShell", "cmd" — which is
       exactly what the heading's tag already says, and a line that also printed
       it would spend half of itself saying it twice. */
    function dashboardPaneLine(pane) {
        const identity = dashboardIdentity();
        const typed = String(pane?.title || '').trim();
        if (typed && identity && !identity.isGenericPaneTitle(typed)) {
            return typed;
        }
        const announced = String(pane?.activity?.title || '').trim();
        if (announced) {
            return announced;
        }
        const directory = String(pane?.directory || '').trim();
        const isRemote = String(pane?.mode || '').toLowerCase() === 'ssh';
        const host = isRemote ? String(pane?.host || '').trim() : '';
        if (directory && host) {
            return `${host}: ${directory}`;
        }
        return directory || host || dashboardPaneTitle(pane);
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

    /* A pane that is not connected has nothing to say about what its agent is
       doing, and saying "no output yet" about it would be answering a
       different question. The transport's own word wins while there is one. */
    function dashboardPaneStateWord(pane) {
        const status = String(pane?.status || '');
        if (status === 'error') {
            return 'Failed';
        }
        if (status === 'disconnected') {
            return 'Disconnected';
        }
        if (status === 'connecting' || status === 'pending') {
            return 'Connecting';
        }
        return dashboardStateWord(pane?.activity);
    }

    /* Which of the three state hues the dot wears. Kept separate from the word
       above because an unreachable pane and a quiet one are the same colour
       only by accident. */
    function dashboardPaneStateKey(pane) {
        const status = String(pane?.status || '');
        if (status === 'error' || status === 'disconnected') {
            return 'error';
        }
        if (status === 'connecting' || status === 'pending') {
            return 'unknown';
        }
        return String(pane?.activity?.state || 'unknown');
    }

    /* The dot and its word always; the bar only when a percentage was actually
       published, or when the agent said it is busy without saying how far
       along it is. */
    function dashboardActivityHtml(pane) {
        const activity = pane?.activity;
        const stateKey = dashboardPaneStateKey(pane);
        const word = dashboardPaneStateWord(pane);
        const progressState = String(activity?.progress_state || '');
        const value = Number(activity?.progress_value) || 0;
        const determinate = stateKey !== 'error' && progressState === 'normal' && value > 0;
        const indeterminate = !determinate
            && stateKey === 'working'
            && ['indeterminate', 'warning', 'normal'].includes(progressState);
        const bar = determinate || indeterminate
            ? `
                <span class="dash-progress dash-progress-${escHtml(progressState || 'normal')}">
                    <span
                        class="dash-progress-fill${indeterminate ? ' is-indeterminate' : ''}"
                        ${determinate ? `style="width:${Math.max(0, Math.min(100, value))}%"` : ''}
                    ></span>
                </span>
                ${determinate ? `<span class="dash-progress-value">${value}%</span>` : ''}
            `
            : '';
        return `
            <span class="dash-activity dash-state-${escHtml(stateKey)}">
                ${bar}
                <span class="dash-state-word">${escHtml(word)}</span>
                <span class="dash-state-dot" aria-hidden="true"></span>
            </span>
        `;
    }

    function dashboardTagHtml(label, modifier = '') {
        return label
            ? `<span class="dash-tag${modifier ? ` dash-tag-${escHtml(modifier)}` : ''}">${escHtml(label)}</span>`
            : '';
    }

    /* One running pane, as one line. Everything that is true of the *agent*
       rather than of this pane has moved up into the heading above, so what is
       left is what distinguishes this pane from its siblings: what it is doing,
       whether it was launched with its agent's own auto-approval flag — the
       one property of a running agent worth knowing from across the room, and
       a per-pane one — and how it is getting on.

       The session id rides the row because the window this row opens needs it:
       naming the workspace lands in the right window, and naming the group and
       the pane is what lands on the right tab and the right pane inside it. */
    function dashboardAgentRowHtml(pane) {
        return `
            <button
                type="button"
                class="dash-agent"
                data-dashboard-action="pane"
                data-dashboard-key="pane:${escHtml(pane?.session_id || '')}"
                data-workspace-id="${escHtml(pane?.workspace_id || '')}"
                data-group-id="${escHtml(pane?.group_id || '')}"
                data-session-id="${escHtml(pane?.session_id || '')}"
            >
                <span class="dash-agent-line">${escHtml(dashboardPaneLine(pane))}</span>
                ${pane?.agent_auto_mode ? dashboardTagHtml('auto', 'auto') : ''}
                ${dashboardActivityHtml(pane)}
            </button>
        `;
    }

    /* Panes gathered under the agent they run, in the order they first appear.

       The key is the agent *and* what it runs on, because a heading is a claim
       about every line under it: two Claude panes on two different shells are
       two different machines as far as their work is concerned, and folding
       them under one "PowerShell" heading would state something false. Order is
       first appearance rather than anything sorted, so a pane stays where the
       window that owns it would put it. */
    function dashboardAgentGroups(panes) {
        const groups = [];
        const byKey = new Map();
        (Array.isArray(panes) ? panes : []).forEach(pane => {
            const name = dashboardAgentName(pane);
            const transport = dashboardTransportLabel(pane);
            const glyphKey = dashboardAgentGlyphKey(pane);
            const key = JSON.stringify([glyphKey, name, transport]);
            let group = byKey.get(key);
            if (!group) {
                group = { key, name, transport, glyphKey, glyph: dashboardAgentGlyphHtml(pane), panes: [] };
                byKey.set(key, group);
                groups.push(group);
            }
            group.panes.push(pane);
        });
        return groups;
    }

    /* One agent, once, however many panes are running it. The heading is set
       small deliberately: it is the label on a block the reader has already
       found by its mark, and every pixel it gives up goes to the lines under
       it, which are what they came to read. */
    function dashboardAgentGroupHtml(group) {
        return `
            <div class="dash-agent-group" data-agent="${escHtml(group.glyphKey)}">
                <div class="dash-agent-head">
                    <span class="dash-agent-icon" aria-hidden="true">${group.glyph}</span>
                    <span class="dash-agent-title">${escHtml(group.name)}</span>
                    ${dashboardTagHtml(group.transport, 'transport')}
                </div>
                ${group.panes.map(dashboardAgentRowHtml).join('')}
            </div>
        `;
    }

    /* One session tab, as a card: its own heading, its own agents, its own
       edge. The heading is pressable for the same reason the agent rows are —
       it is the way to the tab itself, which may hold panes this surface
       deliberately does not list. */
    function dashboardSessionHtml(group) {
        const panes = Array.isArray(group?.panes) ? group.panes : [];
        const agents = Number(group?.agent_count) || panes.length;
        const total = Number(group?.pane_count) || panes.length;
        const others = Math.max(0, total - agents);
        return `
            <section class="dash-session">
                <button
                    type="button"
                    class="dash-session-head"
                    data-dashboard-action="session"
                    data-dashboard-key="session:${escHtml(group?.group_id || '')}"
                    data-workspace-id="${escHtml(group?.workspace_id || '')}"
                    data-group-id="${escHtml(group?.group_id || '')}"
                >
                    <span class="dash-session-name">${escHtml(String(group?.name || group?.group_id || ''))}</span>
                    ${group?.is_active ? dashboardTagHtml('active', 'active') : ''}
                    <span class="dash-session-meta">
                        ${agents} agent${agents === 1 ? '' : 's'}${others ? ` · ${others} other pane${others === 1 ? '' : 's'}` : ''}
                    </span>
                </button>
                <div class="dash-agents">
                    ${dashboardAgentGroups(panes).map(dashboardAgentGroupHtml).join('')}
                </div>
            </section>
        `;
    }

    /* One workspace, as a titled band across the page. Its sessions are laid
       out in a grid inside it, so a wide window shows several side by side
       instead of one very long column. */
    function dashboardWorkspaceHtml(workspace, index) {
        const groups = Array.isArray(workspace?.groups) ? workspace.groups : [];
        const agents = Number(workspace?.agent_count) || 0;
        return `
            <section class="dash-workspace">
                <header class="dash-workspace-head">
                    <button
                        type="button"
                        class="dash-workspace-open"
                        data-dashboard-action="workspace"
                        data-dashboard-key="workspace:${escHtml(workspace?.workspace_id || '')}"
                        data-workspace-id="${escHtml(workspace?.workspace_id || '')}"
                    >
                        <span class="dash-workspace-name">${escHtml(dashboardWorkspaceLabel(workspace, index))}</span>
                    </button>
                    <span class="dash-workspace-meta">
                        ${groups.length} session${groups.length === 1 ? '' : 's'} · ${agents} agent${agents === 1 ? '' : 's'}
                    </span>
                </header>
                <div class="dash-sessions">
                    ${groups.map(dashboardSessionHtml).join('')}
                </div>
            </section>
        `;
    }

    function dashboardBodyHtml(snapshot) {
        const workspaces = Array.isArray(snapshot?.workspaces) ? snapshot.workspaces : [];
        if (!workspaces.length) {
            return `
                <div class="dash-empty">
                    <p class="dash-empty-title">No agents are running.</p>
                    <p class="dash-empty-note">
                        Launch a pane with an agent, or point an open pane at one from its
                        reset menu, and it appears here.
                    </p>
                </div>
            `;
        }
        return workspaces.map((workspace, index) => dashboardWorkspaceHtml(workspace, index)).join('');
    }

    function dashboardTotalsText(snapshot) {
        const totals = snapshot?.totals || {};
        const agents = Number(totals.agents) || 0;
        const sessions = Number(totals.sessions) || 0;
        const workspaces = Number(totals.workspaces) || 0;
        if (!agents) {
            return 'Nothing running';
        }
        return `${agents} agent${agents === 1 ? '' : 's'} · `
            + `${sessions} session${sessions === 1 ? '' : 's'} · `
            + `${workspaces} workspace${workspaces === 1 ? '' : 's'}`;
    }

    /* ── The page ── */

    function setAgentDashboardNotice(message) {
        const notice = document.getElementById(AGENT_DASHBOARD_NOTICE_ID);
        if (!notice) {
            return;
        }
        notice.textContent = message;
        notice.hidden = !message;
    }

    /* A render replaces the row the pointer or the caret was on, so the row is
       found again by its key afterwards, and the scroller is put back where it
       was. Both matter more here than they did in the dropdown: this window
       stays open while you read it. */
    function renderAgentDashboard(html) {
        const body = document.getElementById(AGENT_DASHBOARD_BODY_ID);
        if (!body || html === _agentDashboardPainted) {
            return false;
        }
        const focusedKey = body.contains?.(document.activeElement)
            ? document.activeElement?.dataset?.dashboardKey || ''
            : '';
        const scrollTop = body.scrollTop;
        body.innerHTML = html;
        _agentDashboardPainted = html;
        body.scrollTop = scrollTop;
        if (focusedKey) {
            body.querySelector?.(`[data-dashboard-key="${focusedKey}"]`)?.focus?.();
        }
        return true;
    }

    async function refreshAgentDashboard() {
        const requestId = ++_agentDashboardRequestId;
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
        if (requestId !== _agentDashboardRequestId) {
            return;
        }
        setAgentDashboardNotice(failure);
        if (!snapshot) {
            /* The last good tree stays on screen behind the notice: a reading
               from four seconds ago beats an empty page, as long as the page
               says the reading is old. */
            return;
        }
        const totals = document.getElementById(AGENT_DASHBOARD_TOTALS_ID);
        if (totals) {
            totals.textContent = dashboardTotalsText(snapshot);
        }
        renderAgentDashboard(dashboardBodyHtml(snapshot));
    }

    /* Stands down entirely while the window is hidden — a dashboard nobody can
       see has nothing to keep current. */
    function scheduleAgentDashboardRefresh() {
        if (_agentDashboardTimer !== null) {
            clearInterval(_agentDashboardTimer);
            _agentDashboardTimer = null;
        }
        if (document.hidden) {
            return;
        }
        _agentDashboardTimer = setInterval(refreshAgentDashboard, AGENT_DASHBOARD_REFRESH_MS);
    }

    /* ── What a row does ──
       This window is in no workspace, so every row is somewhere else: it opens
       (or focuses) the window that owns it, at the session it names. The
       dashboard stays open behind it — it is a place you come back to. */
    async function openDashboardTarget({ workspaceId, groupId = '', sessionId = '' }) {
        const resolvedWorkspaceId = String(workspaceId || '');
        if (!resolvedWorkspaceId || typeof openWorkspaceWindow !== 'function') {
            return false;
        }
        /* Stored before the window is asked for, because that is the only order
           that works for the case the URL cannot cover: a workspace window that
           is already open is raised, not reloaded, so the tab and the pane have
           to be waiting for it to claim when it comes to the front. */
        if (typeof requestWorkspaceFocusTarget === 'function') {
            requestWorkspaceFocusTarget(resolvedWorkspaceId, { groupId, sessionId });
        }
        try {
            if (await openWorkspaceWindow(resolvedWorkspaceId, { groupId })) {
                return true;
            }
        } catch (error) {
            console.error('[GridVibe Dashboard] could not open the workspace:', error);
            setAgentDashboardNotice('Could not open that workspace window.');
            return false;
        }
        /* In browser mode the one way this fails is a blocked pop-up, and
           workspaces.js already owns the single wording for it. */
        setAgentDashboardNotice(
            typeof WORKSPACE_TAB_BLOCKED_HINT === 'string'
                ? WORKSPACE_TAB_BLOCKED_HINT
                : 'Could not open that workspace window.'
        );
        return false;
    }

    /* ── Getting out of here ──
       This window is about workspaces without being in one, which is exactly
       the launcher's standing, so it answers the launcher's two chords the same
       way: Alt+W goes back to the workspace you came from, Alt+Q opens the
       launcher. Without them the dashboard was the one GridVibe window you
       could only leave with the mouse.

       Both are matched on `event.code` and exclude Ctrl, the rule every Alt
       chord in the app follows: AltGr arrives as Ctrl+Alt on Windows, so a
       chord that did not exclude Ctrl would fire while typing an accented
       character, and matching the physical key keeps the chord on the same key
       whatever the layout prints on it. Shift is read the way each host page
       reads it: there is no cycle to run backwards from a window that is not a
       workspace, so Alt+Shift+W still means "go back", while Alt+Q is a single
       destination and takes no modifier.

       Neither is gated on multi-workspace being enabled. The launcher gates its
       Alt+W because it is the workspace *walk* seen from outside; this is "put
       the window I came from back in front", which is worth the same whether
       there is one workspace or six — and this window only exists when
       something is running in one. */
    const DASHBOARD_WORKSPACE_CHORD_CODE = 'KeyW';
    const DASHBOARD_LAUNCHER_CHORD_CODE = 'KeyQ';

    let _dashboardWorkspaceReturnInFlight = false;

    function dashboardBlockedTabHint() {
        return typeof WORKSPACE_TAB_BLOCKED_HINT === 'string'
            ? WORKSPACE_TAB_BLOCKED_HINT
            : 'Could not open that window.';
    }

    /* The in-flight guard keeps a held key from queueing a burst of opens —
       the same guard, for the same reason, as the launcher's. */
    async function returnToDashboardOriginWorkspace() {
        if (_dashboardWorkspaceReturnInFlight || typeof returnToOriginWorkspace !== 'function') {
            return;
        }
        _dashboardWorkspaceReturnInFlight = true;
        try {
            const { outcome } = await returnToOriginWorkspace();
            if (outcome === WORKSPACE_RETURN_NONE) {
                setAgentDashboardNotice('No workspace is open to switch back to.');
            } else if (outcome === WORKSPACE_RETURN_BLOCKED) {
                setAgentDashboardNotice(dashboardBlockedTabHint());
            } else {
                setAgentDashboardNotice('');
            }
        } catch (error) {
            console.error('[GridVibe Dashboard] workspace return failed:', error);
            setAgentDashboardNotice('Could not switch to a workspace window.');
        } finally {
            _dashboardWorkspaceReturnInFlight = false;
        }
    }

    async function openDashboardLauncherWindow() {
        if (typeof openLauncherWindow !== 'function') {
            return;
        }
        if (await openLauncherWindow()) {
            setAgentDashboardNotice('');
            return;
        }
        setAgentDashboardNotice(dashboardBlockedTabHint());
    }

    function dashboardWindowChordMatches(event, code, { allowShift = false } = {}) {
        if (!event || !event.altKey || event.ctrlKey || event.metaKey || event.repeat) {
            return false;
        }
        if (event.shiftKey && !allowShift) {
            return false;
        }
        return event.code === code;
    }

    function wireAgentDashboardChords() {
        document.addEventListener?.('keydown', event => {
            if (dashboardWindowChordMatches(event, DASHBOARD_WORKSPACE_CHORD_CODE, { allowShift: true })) {
                event.preventDefault();
                returnToDashboardOriginWorkspace();
                return;
            }
            if (dashboardWindowChordMatches(event, DASHBOARD_LAUNCHER_CHORD_CODE)) {
                event.preventDefault();
                openDashboardLauncherWindow();
            }
        });
    }

    function wireAgentDashboard() {
        const body = document.getElementById(AGENT_DASHBOARD_BODY_ID);
        if (!body) {
            return;
        }
        /* Delegated: every row is rebuilt whenever the reading changes, so a
           listener on a row would not outlive the reading that drew it. */
        body.addEventListener('click', event => {
            const row = event.target?.closest?.('[data-dashboard-action]');
            if (!row) {
                return;
            }
            event.preventDefault();
            openDashboardTarget({
                workspaceId: row.dataset.workspaceId,
                groupId: row.dataset.groupId || '',
                sessionId: row.dataset.sessionId || ''
            });
        });
        document.getElementById('agentDashboardRefreshBtn')?.addEventListener('click', () => {
            refreshAgentDashboard();
        });
        document.addEventListener('visibilitychange', () => {
            scheduleAgentDashboardRefresh();
            if (!document.hidden) {
                refreshAgentDashboard();
            }
        });
        wireAgentDashboardChords();
        scheduleAgentDashboardRefresh();
        refreshAgentDashboard();
    }
