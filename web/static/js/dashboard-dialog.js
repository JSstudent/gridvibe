    /* ─────────────────────────────
       The agent dashboard — every agent running, wherever it runs.

       GridVibe can have several workspace windows open, each with several
       session tabs, each with several panes, and the only way to find out which
       agents are working anywhere is to go and look in every window. This is
       that answer in one surface: every workspace holding an agent, the
       sessions inside it, and the agents inside those — what each one is
       announcing and whether it is doing anything.

       **It is a dialog, and it was a window.** The window was the wrong shape
       for the question: you ask "what is running elsewhere?" *while* you are
       somewhere, and answering it took the reader out of the window they were
       working in, gave the answer a taskbar entry of its own to find again, and
       needed two chords of its own to get back. So it now opens over the page
       that asked for it and a press outside it puts that page back — on both
       pages, from the same partial and this one module. Five consequences,
       each of which is why something below is not the obvious code:

         · **It polls only while it is open.** The reading is a whole-tree
           compose every couple of seconds; a dialog that is shut is a dialog
           nobody can see, and a background window quietly refetching the state
           of every workspace forever is exactly the cost a panel is supposed to
           avoid. Opening arms the poll and reads once immediately; closing
           disarms it and aborts what is in flight.
         · **Escape is claimed, and only when this is the top surface.** The
           root is the app's own `.modal-shell`, which is already in
           `EXPLORER_ESCAPE_CLAIM_SELECTOR`, so closing the dashboard cannot
           also drop an explorer pane's selection behind it. And a session ×
           here opens the close prompt *on top* of this dialog, so Escape while
           another shell is open belongs to that shell: one key press must not
           dismiss two surfaces.
         · **A row that names this very workspace lands without opening
           anything.** `openWorkspaceWindow` on the window you are already in
           raises a window that is already raised, so no `focus` event fires and
           the stored target is never claimed — the row would do nothing at all.
           The page that owns the tab applies it directly instead.
         · **Acting closes it, and so does leaving.** Every row is a way
           somewhere else; a dialog still covering the pane it just took you to
           is in the way. The close verbs are the exception — they end something
           and leave you here. And leaving the *window* is the same gesture as
           pressing the backdrop: this surface belongs to the window it was
           raised on, so that window must not be left holding a stale one behind
           whichever window the reader moved to.
         · **There is one of it, across every window.** Every GridVibe window
           carries this dialog, so raising it is a gesture the app has several
           of — and two of them up at once is two readings of one tree drifting
           apart on their own polls. Opening broadcasts a claim and the newest
           open wins; nothing ever refuses to open, so a window that died with
           its dialog up cannot leave the button dead everywhere else.

       The rest is what the reading itself is for:

         · **One request, not N+1.** `/api/dashboard` composes the whole tree
           server-side, already reduced to agents. A page that asked for
           workspaces, then groups per workspace, then panes per group would
           render a tree assembled out of several different moments, and the
           moments that disagree are exactly the ones worth showing.
         · **Sections, not a list.** The dialog has room a dropdown never had,
           so the three levels are three different things on it rather than
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
       `wireDashboard()` (dashboard.js) wires it, because the button and the
       dialog it opens are one feature on both pages.
    ───────────────────────────── */

    const AGENT_DASHBOARD_SHELL_ID = 'agentDashboardShell';
    const AGENT_DASHBOARD_DIALOG_SELECTOR = '.dash-dialog';
    const AGENT_DASHBOARD_BODY_ID = 'agentDashboardBody';
    const AGENT_DASHBOARD_TOTALS_ID = 'agentDashboardTotals';
    const AGENT_DASHBOARD_NOTICE_ID = 'agentDashboardNotice';
    const AGENT_DASHBOARD_REFRESH_BTN_ID = 'agentDashboardRefreshBtn';
    const AGENT_DASHBOARD_CLOSE_BTN_ID = 'agentDashboardCloseBtn';

    /* A dashboard that lags the thing it describes is just a screenshot. */
    const AGENT_DASHBOARD_REFRESH_MS = 2000;
    const AGENT_DASHBOARD_TIMEOUT_MS = 10000;
    /* How long a confirmation stays on the notice line. Only non-errors are
       timed: a failure is a state the reader has to do something about. */
    const AGENT_DASHBOARD_NOTICE_MS = 6000;


    let _agentDashboardTimer = null;
    /* Bumped on every request. A slow answer that lands after a newer one was
       asked for is dropped rather than painted, so the page cannot flick back
       to an older reading. */
    let _agentDashboardRequestId = 0;
    /* What is on screen right now, so an unchanged answer costs no repaint. */
    let _agentDashboardPainted = '';
    let _agentDashboardController = null;
    let _agentDashboardStructure = '';
    let _agentDashboardRows = new Map();
    let _agentDashboardActionNotice = '';
    let _agentDashboardActionTone = 'error';
    let _agentDashboardNoticeTimer = null;
    let _agentDashboardReadNotice = '';
    let _agentDashboardWired = false;
    /* What had focus when the dialog opened, so closing hands it back. A
       node rather than an id: the opener is the button in the page's own
       chrome, and this module has no business knowing which one. */
    let _agentDashboardOpener = null;

    function dashboardAgentOptions() {
        return typeof AGENT_OPTIONS === 'undefined' || !Array.isArray(AGENT_OPTIONS) ? [] : AGENT_OPTIONS;
    }

    function dashboardIdentity() {
        return typeof window !== 'undefined' ? window.GridVibeAgentIdentity : undefined;
    }

    function dashboardGlyphs() {
        return typeof window !== 'undefined' ? window.GridVibeAgentGlyphs : undefined;
    }

    function dashboardSessionColour() {
        return typeof window !== 'undefined' ? window.GridVibeSessionColour : undefined;
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
        if (seconds === null || seconds === undefined) return '';
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

    /* The agent this pane runs, in the registry's own prose, printed on the
       row itself. Never empty: a custom agent with nothing to name is still an
       agent, and a blank name would read as a rendering fault rather than as a
       fact about the pane. */
    function dashboardAgentName(pane) {
        const identity = dashboardIdentity();
        const name = identity ? identity.agentDisplayName(pane, dashboardAgentOptions()) : '';
        return name || 'Agent';
    }

    /* Which mark the row wears, and the mark itself. The glyph module owns what
       an agent GridVibe has not drawn falls back to; this only hands it the
       same registry key the name came from. */
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

    /* Which conversation this row is, and the hover that carries what the line
       had to shorten. Both are `agent-identity.js`'s answer: the rule reads the
       same facts the pane header's naming rule does, and a second copy here is
       how a row and a header come to disagree about the same pane. */
    function dashboardPaneLine(pane) {
        const identity = dashboardIdentity();
        if (!identity) {
            return String(pane?.activity?.title || '').trim() || dashboardPaneTitle(pane);
        }
        return identity.paneChatLine(pane, Number(pane?.index) || 0, dashboardAgentOptions());
    }

    function dashboardPaneHover(pane) {
        const identity = dashboardIdentity();
        if (!identity) {
            return dashboardPaneLine(pane);
        }
        return identity.paneChatTooltip(pane, Number(pane?.index) || 0, dashboardAgentOptions());
    }

    function dashboardStateWord(activity) {
        const state = String(activity?.state || '');
        if (state === 'working') {
            return 'Working';
        }
        if (state === 'error') return 'Error';
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
        const progressState = pane?.status === 'connected' && activity?.progress_fresh !== false
            ? String(activity?.progress_state || '') : '';
        const value = Math.max(0, Math.min(100, Number(activity?.progress_value) || 0));
        const determinate = stateKey === 'working' && progressState === 'normal';
        const indeterminate = !determinate
            && stateKey === 'working'
            && ['indeterminate', 'warning', 'normal'].includes(progressState);
        const bar = determinate || indeterminate
            ? `
                <span class="dash-progress dash-progress-${escHtml(progressState || 'normal')}"
                    role="progressbar" aria-label="Agent progress" aria-valuemin="0" aria-valuemax="100"
                    ${determinate ? `aria-valuenow="${value}"` : ''}>
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

    /* One running pane, as one line — and the whole line, because the agent it
       runs is now stated *on* it rather than over a block of its siblings.

       It was a block: a heading naming the agent and its shell, with one line
       per pane under it. That grouping bought a saving only when several panes
       of one agent sat in one session card, which is the uncommon case; what it
       cost in every other was a two-line entry for one pane, a heading and its
       lines competing for the eye, and — because the block was keyed on the
       agent *and* its shell — a session with three agents drawn as three
       separate stacks. Stating the mark, the name and the shell to the left of
       the title puts all four facts on one line, in one column order, so a card
       is read straight down the titles with the agents scanned in the margin.

       `data-agent` therefore rides the row: it is what tints the mark, and the
       block it used to sit on is gone.

       The session id rides the row because the window this row opens needs it:
       naming the workspace lands in the right window, and naming the group and
       the pane is what lands on the right tab and the right pane inside it. */
    function dashboardAgentRowHtml(pane) {
        return `
            <button
                type="button"
                class="dash-agent"
                data-agent="${escHtml(dashboardAgentGlyphKey(pane))}"
                data-dashboard-action="pane"
                data-dashboard-key="pane:${escHtml(pane?.session_id || '')}"
                data-workspace-id="${escHtml(pane?.workspace_id || '')}"
                data-group-id="${escHtml(pane?.group_id || '')}"
                data-session-id="${escHtml(pane?.session_id || '')}"
                title="${escHtml(dashboardPaneHover(pane))}"
            >
                <span class="dash-agent-icon" aria-hidden="true">${dashboardAgentGlyphHtml(pane)}</span>
                <span class="dash-agent-name">${escHtml(dashboardAgentName(pane))}</span>
                ${dashboardTagHtml(dashboardTransportLabel(pane), 'transport')}
                <span class="dash-agent-line">${escHtml(dashboardPaneLine(pane))}</span>
                ${pane?.agent_auto_mode ? dashboardTagHtml('auto', 'auto') : ''}
                <span class="dash-agent-reading">${dashboardActivityHtml(pane)}</span>
            </button>
        `;
    }

    /* The one glyph on this page that is a control rather than a mark. Stroke
       SVG and not a text ×, so it takes `currentColor` and lines up with the
       title bar's own icon box instead of being centred by font metrics. */
    const DASHBOARD_CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"'
        + ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"'
        + ' focusable="false"><line x1="18" y1="6" x2="6" y2="18"></line>'
        + '<line x1="6" y1="6" x2="18" y2="18"></line></svg>';

    function dashboardCloseActions() {
        return typeof window !== 'undefined' ? window.GridVibeDashboardClose : undefined;
    }

    /* Whether the *window* verb is on this page at all. Asked at render time
       rather than remembered, because the native bridge arrives after the
       first paint — the `pywebviewready` listener below invalidates the
       rendered structure so this question is put again once it has. */
    function dashboardCanCloseWindows() {
        const actions = dashboardCloseActions();
        return Boolean(actions && actions.canCloseWindow());
    }

    /* The two custom properties that carry a session's own hue onto its card.

       The card is a session, and a session already has a colour: the tab strip
       in the workspace window picks one out of ten off the group id, and that
       hue is how a reader finds the tab they mean before they read its name.
       A card drawn in the dialog's accent threw that away and made the reader
       match by name across two windows, so the edge and the name here are the
       tab's own colour — one fact drawn the same in both places, which is the
       whole reason `session-colour.js` is a module rather than a second copy
       of a hash.

       Inline rather than a class: there are ten hues and no stylesheet has any
       business enumerating them, and the value is per row. It is written as a
       style attribute on the card, so both the edge and the heading read it
       from one declaration.

       No module, no colour: the card falls back to the dialog's own border and
       text tokens, which is what it wore before. The same rule the × and the
       band's verbs follow for their controller. */
    function dashboardSessionColourStyle(group) {
        const colour = dashboardSessionColour();
        const groupId = String(group?.group_id || '');
        if (!colour || !groupId) {
            return '';
        }
        return ` style="--dash-session-color:${escHtml(colour.sessionColour(groupId))};`
            + `--dash-session-color-soft:${escHtml(colour.sessionColourRgba(groupId, 0.14))}"`;
    }

    /* One session tab, as a card: its own heading, its own agents, its own
       edge — in its own colour, which is its tab's. The heading is pressable
       for the same reason the agent rows are — it is the way to the tab
       itself, which may hold panes this surface deliberately does not list.

       The × is a control *beside* that heading and never inside it: a button
       inside a button is not a control, and the heading already has an action
       of its own. It ends live shells, so it goes through the same
       three-outcome prompt the session tab's × does. */
    function dashboardSessionHtml(group) {
        const panes = Array.isArray(group?.panes) ? group.panes : [];
        const agents = Number(group?.agent_count) || panes.length;
        const total = Number(group?.pane_count) || panes.length;
        const others = Math.max(0, total - agents);
        const name = String(group?.name || group?.group_id || '');
        const actions = dashboardCloseActions();
        /* No controller, no ×: a control that cannot do the thing it names is
           worse than a heading with no control beside it. The same rule the
           band's two verbs follow, and the same one this file already applies
           to the identity and glyph modules. */
        const closeTitle = actions ? actions.policy.sessionCloseTitle(`"${name}"`) : '';
        const closeButton = actions
            ? `
                    <button
                        type="button"
                        class="dash-session-close"
                        data-dashboard-action="close-session"
                        data-dashboard-key="close-session:${escHtml(group?.group_id || '')}"
                        data-workspace-id="${escHtml(group?.workspace_id || '')}"
                        data-group-id="${escHtml(group?.group_id || '')}"
                        data-session-name="${escHtml(name)}"
                        title="${escHtml(closeTitle)}"
                        aria-label="${escHtml(closeTitle)}"
                    >${DASHBOARD_CLOSE_ICON}</button>`
            : '';
        return `
            <section class="dash-session"${dashboardSessionColourStyle(group)}>
                <header class="dash-session-head">
                    <button
                        type="button"
                        class="dash-session-open"
                        data-dashboard-action="session"
                        data-dashboard-key="session:${escHtml(group?.group_id || '')}"
                        data-workspace-id="${escHtml(group?.workspace_id || '')}"
                        data-group-id="${escHtml(group?.group_id || '')}"
                    >
                        <span class="dash-session-name">${escHtml(name)}</span>
                        ${group?.is_active ? dashboardTagHtml('active', 'active') : ''}
                        <span class="dash-session-meta">
                            ${agents} agent${agents === 1 ? '' : 's'}${others ? ` · ${others} other pane${others === 1 ? '' : 's'}` : ''}
                        </span>
                    </button>${closeButton}
                </header>
                <div class="dash-agents">
                    ${panes.map(dashboardAgentRowHtml).join('')}
                </div>
            </section>
        `;
    }

    /* The band's two close verbs. Words rather than glyphs, and deliberately:
       they differ by whether the sessions survive, which no pair of icons this
       size says and a label plus a title does. The window verb is simply not
       rendered in browser mode (`dashboard-close.js` owns that predicate). */
    function dashboardWorkspaceActionsHtml(workspace, index) {
        const actions = dashboardCloseActions();
        if (!actions) {
            return '';
        }
        const label = dashboardWorkspaceLabel(workspace, index);
        const workspaceId = escHtml(workspace?.workspace_id || '');
        /* What closing the workspace would end, which is every live session in
           it and not only the ones listed here. The agent-scoped count is the
           fallback rather than zero: a band exists because something is
           running in it, and a zero would make the confirmation skip itself. */
        const closes = Number(workspace?.live_group_count)
            || Number(workspace?.group_count)
            || 0;
        const controls = actions.policy
            .workspaceControls(workspace, { native: dashboardCanCloseWindows() })
            .map(control => `
                <button
                    type="button"
                    class="dash-workspace-action${control.danger ? ' is-danger' : ''}"
                    data-dashboard-action="${escHtml(control.action)}"
                    data-dashboard-key="${escHtml(control.action)}:${workspaceId}"
                    data-workspace-id="${workspaceId}"
                    data-workspace-label="${escHtml(label)}"
                    data-group-count="${escHtml(String(closes))}"
                    title="${escHtml(control.title)}"
                >${escHtml(control.label)}</button>
            `)
            .join('');
        return `<div class="dash-workspace-actions">${controls}</div>`;
    }

    /* One workspace, as a titled band across the page, with its sessions stacked
       one under another inside it — full width, every card the same width. They
       used to flow into as many columns as the dialog was wide enough for,
       which made a card's width a function of how many sessions happened to be
       open beside it: two cards side by side truncated every title in both, and
       a third session arriving re-flowed the two the reader was already reading.
       A card is a session and a session is read along its rows, so the width
       goes to the rows. */
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
                    ${dashboardWorkspaceActionsHtml(workspace, index)}
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

    /* One line, one message. A failed read and a failed action are two sources
       and the action wins while it has something to say, because it is the one
       the reader just provoked.

       `tone` is the action slot's alone: a read failure is never anything but
       an error, while an action can succeed *invisibly* — closing a workspace
       window changes nothing on this page — and has to be able to say so. An
       error stays until it is replaced or the action that raised it succeeds;
       anything else is a confirmation rather than a state, so it hands the
       line back on a timer, the same rule the launcher's banner follows. */
    function setAgentDashboardNotice(message, source = 'action', tone = 'error') {
        if (source === 'read') {
            _agentDashboardReadNotice = message;
        } else {
            _agentDashboardActionNotice = message;
            _agentDashboardActionTone = message ? tone : 'error';
            if (_agentDashboardNoticeTimer !== null) {
                clearTimeout(_agentDashboardNoticeTimer);
                _agentDashboardNoticeTimer = null;
            }
            if (message && tone !== 'error') {
                _agentDashboardNoticeTimer = setTimeout(
                    () => setAgentDashboardNotice(''),
                    AGENT_DASHBOARD_NOTICE_MS
                );
            }
        }
        const notice = document.getElementById(AGENT_DASHBOARD_NOTICE_ID);
        if (!notice) {
            return;
        }
        const showingAction = Boolean(_agentDashboardActionNotice);
        notice.textContent = _agentDashboardActionNotice || _agentDashboardReadNotice;
        notice.hidden = !notice.textContent;
        notice.classList.toggle(
            'is-info',
            showingAction && _agentDashboardActionTone !== 'error'
        );
    }

    function paintAgentDashboardSnapshot(snapshot) {
        const body = document.getElementById(AGENT_DASHBOARD_BODY_ID);
        if (!body) return;
        const rows = new Map();
        const structure = JSON.stringify(snapshot.workspaces.map(workspace => ({
            ...workspace,
            groups: workspace.groups.map(group => ({
                ...group,
                panes: group.panes.map(pane => {
                    rows.set(pane.session_id, pane);
                    const { activity, title, directory, status, ...identity } = pane;
                    return identity;
                })
            }))
        })));
        // Ordinary polls update existing rows. Idle ages and title changes must
        // not replace buttons, interrupt a press, or destroy text selections.
        if (structure === _agentDashboardStructure && body.querySelectorAll) {
            body.querySelectorAll('[data-session-id]').forEach(row => {
                const pane = rows.get(row.dataset.sessionId);
                const previous = _agentDashboardRows.get(row.dataset.sessionId);
                if (!pane) return;
                const line = dashboardPaneLine(pane);
                if (!previous || line !== dashboardPaneLine(previous)) {
                    row.querySelector('.dash-agent-line').textContent = line;
                    row.title = line;
                }
                const reading = dashboardActivityHtml(pane);
                if (!previous || reading !== dashboardActivityHtml(previous)) {
                    row.querySelector('.dash-agent-reading').innerHTML = reading;
                }
            });
            _agentDashboardPainted = '';
        } else {
            renderAgentDashboard(dashboardBodyHtml(snapshot));
        }
        _agentDashboardStructure = structure;
        _agentDashboardRows = rows;
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
            body.querySelector?.(`[data-dashboard-key="${focusedKey}"]`)?.focus?.({ preventScroll: true });
        }
        return true;
    }

    /* The one gate every reader passes through, the Refresh button included:
       a shut dialog is not a surface with an old reading on it, it is a surface
       nobody is looking at. It asks one thing, for the reason
       `scheduleAgentDashboardRefresh` states. */
    async function refreshAgentDashboard() {
        if (!agentDashboardDialogOpen()) return;
        const requestId = ++_agentDashboardRequestId;
        _agentDashboardController?.abort();
        const controller = new AbortController();
        _agentDashboardController = controller;
        const timeout = setTimeout(() => controller.abort(), AGENT_DASHBOARD_TIMEOUT_MS);
        let snapshot = null;
        let failure = '';
        try {
            const response = await fetch('/api/dashboard', { signal: controller.signal, cache: 'no-store' });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            snapshot = await response.json();
            if (!snapshot || !Array.isArray(snapshot.workspaces)
                || !snapshot.workspaces.every(workspace => Array.isArray(workspace?.groups)
                    && workspace.groups.every(group => Array.isArray(group?.panes)))) {
                throw new Error('Invalid dashboard response');
            }
        } catch (error) {
            snapshot = null;
            if (requestId === _agentDashboardRequestId) {
                console.error('[GridVibe Dashboard] load failed:', error);
                failure = _agentDashboardStructure
                    ? 'Could not refresh. Showing the last reading. Use Refresh to retry.'
                    : 'Could not read what is running. Use Refresh to retry.';
            }
        } finally {
            clearTimeout(timeout);
            if (_agentDashboardController === controller) _agentDashboardController = null;
        }
        if (requestId !== _agentDashboardRequestId) {
            return;
        }
        setAgentDashboardNotice(failure, 'read');
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
        paintAgentDashboardSnapshot(snapshot);
    }

    /* Stands down entirely while the dialog is shut — a reading nobody can see
       has nothing to keep current, and this one is a whole-tree compose every
       two seconds. Restarted rather than left running, so a closed dialog costs
       exactly nothing.

       One condition and not two. It used to ask about `document.hidden` as
       well, because the dialog could outlive the reader's attention; it cannot
       any more — leaving the window puts it away — so **open** now implies
       visible and focused, and asking twice would state a rule the module no
       longer has. */
    function scheduleAgentDashboardRefresh() {
        if (_agentDashboardTimer !== null) {
            clearInterval(_agentDashboardTimer);
            _agentDashboardTimer = null;
        }
        if (!agentDashboardDialogOpen()) {
            ++_agentDashboardRequestId;
            _agentDashboardController?.abort();
            _agentDashboardController = null;
            return;
        }
        _agentDashboardTimer = setInterval(() => {
            if (!_agentDashboardController) refreshAgentDashboard();
        }, AGENT_DASHBOARD_REFRESH_MS);
    }

    /* ── One dashboard at a time, across every window ──

       Every GridVibe window carries this dialog now, so "open it" is a gesture
       the app has several of — and two windows each holding one up is two
       readings of the same tree, drifting apart on their own polls, with the
       reader's × and Close workspace on both of them. It is one surface, so
       there is one of it: raising it anywhere puts away whichever window had it.

       **A claim is a notice, never a lock, and the newest open always wins.**
       Nothing here asks permission and nothing can refuse to open, which is the
       whole reason it is built this way round: a window that crashed or was
       killed with its dialog up would otherwise leave a claim standing that no
       one can release, and the button would be dead in every other window until
       something expired it. There is no such state to be in. The cost is that
       two opens inside one message round trip could each tell the other to
       close — so a claim is *compared* with this window's own rather than
       obeyed, later wins, and an exact tie is broken on the window id. Both
       halves of that are needed: without the comparison the two dialogs
       annihilate each other, and without the tie-break a coarse clock can make
       two claims genuinely equal.

       **Both transports, and the message is tagged.** The same pair the dim
       lease already rides (`dashboard-focus.js`), for the same reasons and with
       the same caveats: a `BroadcastChannel` never delivers to the object that
       posted, but it *does* deliver to any other channel object in the same
       document — and the publisher below opens a fresh one per message while
       the listener holds one open — so skipping our own `source` is
       load-bearing here rather than tidy. `storage` covers what the channel
       cannot and genuinely never fires in the sending document.

       **Only an open is broadcast.** A close leaves nothing for another window
       to do — it has no dialog up — so a "closed" message would be a second
       piece of cross-window state with no reader and one more way to fall out
       of step. */
    const AGENT_DASHBOARD_CLAIM_CHANNEL = 'gridvibe.dashboardOpen';
    const AGENT_DASHBOARD_CLAIM_STORAGE_KEY = 'gridvibe.dashboardOpen';

    /* This window's own last claim, so an arriving one can be compared with it
       instead of obeyed. Deliberately not cleared on close: a shut dialog
       ignores claims outright, and the next open writes a fresh one. */
    let _agentDashboardClaim = null;
    let _agentDashboardClaimChannel = null;

    /* shared.js's identity for this document, which is what every other
       cross-window message in the app is tagged with. A page that somehow has
       not got it publishes nothing rather than an untaggable claim every window
       including this one would act on. */
    function agentDashboardWindowId() {
        return typeof GRIDVIBE_WINDOW_ID === 'string' ? GRIDVIBE_WINDOW_ID : '';
    }

    function publishAgentDashboardClaim() {
        const source = agentDashboardWindowId();
        if (!source) {
            return null;
        }
        const claim = {
            source,
            at: Date.now(),
            /* `setItem` with an unchanged value fires no storage event, and two
               opens from one window inside a millisecond would otherwise write
               the identical string. */
            nonce: Math.random().toString(36).slice(2)
        };
        _agentDashboardClaim = claim;
        try {
            const channel = new BroadcastChannel(AGENT_DASHBOARD_CLAIM_CHANNEL);
            channel.postMessage(claim);
            channel.close();
        } catch (_error) {}
        try {
            localStorage.setItem(AGENT_DASHBOARD_CLAIM_STORAGE_KEY, JSON.stringify(claim));
        } catch (_error) {}
        return claim;
    }

    /* Later wins, and a tie goes to the higher window id — any total order does,
       as long as both windows compute the same one. */
    function agentDashboardClaimSupersedes(claim) {
        const source = String(claim?.source || '');
        const at = Number(claim?.at);
        if (!source || source === agentDashboardWindowId() || !Number.isFinite(at)) {
            return false;
        }
        const mine = _agentDashboardClaim;
        if (!mine) {
            return true;
        }
        return at === Number(mine.at) ? source > String(mine.source) : at > Number(mine.at);
    }

    function receiveAgentDashboardClaim(claim) {
        if (!agentDashboardDialogOpen() || !agentDashboardClaimSupersedes(claim)) {
            return false;
        }
        closeAgentDashboardDialog();
        return true;
    }

    function wireAgentDashboardExclusivity() {
        try {
            /* Held open for the life of the page. A channel opened per message
               would miss every claim sent while it was shut, which is all of
               them. */
            _agentDashboardClaimChannel = new BroadcastChannel(AGENT_DASHBOARD_CLAIM_CHANNEL);
            _agentDashboardClaimChannel.onmessage = event => {
                receiveAgentDashboardClaim(event?.data);
            };
        } catch (_error) {
            _agentDashboardClaimChannel = null;
        }
        window.addEventListener?.('storage', event => {
            if (event?.key !== AGENT_DASHBOARD_CLAIM_STORAGE_KEY || !event.newValue) {
                return;
            }
            try {
                receiveAgentDashboardClaim(JSON.parse(event.newValue));
            } catch (_error) {}
        });
    }

    /* ── Opening and shutting ──

       The dialog is the app's own `.modal-shell`, so it is shown the way every
       other one is — a class and `aria-hidden` — and inherits both pages'
       scrim and background blur without either of them learning it is here.

       Focus moves into the surface on open and is handed back to whatever had
       it on close, but *only if the dialog still holds it*: a close provoked by
       a press somewhere else must not yank focus off what was pressed. That is
       the same rule the shortcut panel follows.

       The cross-window dim goes with it. It was the standalone window's own
       lease — every other GridVibe page dimmed while the dashboard had focus —
       and it is worth exactly what it was worth before: this surface is about
       the other windows, so the other windows step back while it is up. */

    function agentDashboardShell() {
        return document.getElementById(AGENT_DASHBOARD_SHELL_ID);
    }

    function agentDashboardDialogOpen() {
        return Boolean(agentDashboardShell()?.classList?.contains('visible'));
    }

    /* Absent rather than false when the page cannot answer: every caller uses
       it to decide whether to *move* focus, and declining to move it because a
       stub could not say is the wrong default. */
    function agentDashboardWindowHasFocus() {
        return typeof document.hasFocus === 'function' ? document.hasFocus() : true;
    }

    /* dashboard.js holds the focus lease it started for this page; it is asked
       through a named function rather than reached into, and a page that never
       started one blocks nothing. */
    function setAgentDashboardDim(active) {
        if (typeof markDashboardFocusActive !== 'function') {
            return;
        }
        try {
            markDashboardFocusActive(active);
        } catch (_error) {}
    }

    function openAgentDashboardDialog() {
        const shell = agentDashboardShell();
        if (!shell || agentDashboardDialogOpen()) {
            return false;
        }
        _agentDashboardOpener = document.activeElement || null;
        shell.classList.add('visible');
        shell.setAttribute('aria-hidden', 'false');
        /* Before the dim and before the read: this is the one message that puts
           another window's dialog away, and it costs nothing to be first. */
        publishAgentDashboardClaim();
        setAgentDashboardDim(true);
        /* The surface itself, not the first control in it: the reader opened a
           list to read, and parking the caret on Refresh means the first Enter
           re-reads rather than doing what they came for. */
        shell.querySelector?.(AGENT_DASHBOARD_DIALOG_SELECTOR)?.focus?.({ preventScroll: true });
        scheduleAgentDashboardRefresh();
        refreshAgentDashboard();
        return true;
    }

    function closeAgentDashboardDialog() {
        const shell = agentDashboardShell();
        if (!shell || !agentDashboardDialogOpen()) {
            return false;
        }
        /* Two conditions, not one. The dialog has to still hold the caret —
           a close provoked by a press somewhere else on this page must not
           yank focus off what was pressed — and this window has to still be
           the focused one, because the commonest close now is the reader
           leaving for another window entirely. `activeElement` does not move
           when a window is deactivated, so without `hasFocus()` a departure
           would look exactly like an in-page dismissal and put the caret on a
           button in a window nobody is looking at — and in a host where
           `element.focus()` raises its window, would pull that window back in
           front of the one the reader just chose. */
        const heldFocus = Boolean(shell.contains?.(document.activeElement))
            && agentDashboardWindowHasFocus();
        shell.classList.remove('visible');
        shell.setAttribute('aria-hidden', 'true');
        setAgentDashboardDim(false);
        /* An action's confirmation belongs to the pass that provoked it; a
           reopened dialog reporting a close from four minutes ago would be
           reporting something the reader has no way to place. The read notice
           is left alone — it describes the tree still on screen. */
        setAgentDashboardNotice('');
        scheduleAgentDashboardRefresh();
        if (heldFocus) {
            _agentDashboardOpener?.focus?.({ preventScroll: true });
        }
        _agentDashboardOpener = null;
        return true;
    }

    /* Answers with the state the dialog is now in, not with whether the press
       did anything: the button and the chord are one control and what a caller
       wants back from it is "is it up?". Both halves report a refusal (no
       shell on this page) as a shut dialog, which is what it is. */
    function toggleAgentDashboardDialog() {
        if (agentDashboardDialogOpen()) {
            closeAgentDashboardDialog();
        } else {
            openAgentDashboardDialog();
        }
        return agentDashboardDialogOpen();
    }

    /* ── What a row does ──

       Every row is somewhere else: it opens (or focuses) the window that owns
       it, at the session it names, and the dialog closes behind it — a surface
       still covering the pane it has just taken you to is in the way.

       The workspace this page *is* is the case the window could not have:
       asking `openWorkspaceWindow` to raise the window you are already in
       raises a window that is already raised, so no `focus` event fires, the
       stored target is never claimed and the row does nothing at all. The page
       that owns the tab is asked to land on it directly instead. */
    function dashboardWorkspaceIsHere(workspaceId) {
        if (typeof CURRENT_WORKSPACE_ID === 'undefined'
            || typeof normalizeWorkspaceId !== 'function') {
            return false;
        }
        return normalizeWorkspaceId(workspaceId) === normalizeWorkspaceId(CURRENT_WORKSPACE_ID);
    }

    async function openDashboardTarget({ workspaceId, groupId = '', sessionId = '' }) {
        const resolvedWorkspaceId = String(workspaceId || '');
        if (!resolvedWorkspaceId) {
            return false;
        }
        if (dashboardWorkspaceIsHere(resolvedWorkspaceId)
            && typeof applyWorkspaceFocusTarget === 'function') {
            setAgentDashboardNotice('');
            /* Shut first: the landing focuses a pane, and focusing a pane under
               an open dialog puts the caret somewhere the reader can neither
               see nor type into. */
            closeAgentDashboardDialog();
            applyWorkspaceFocusTarget({ groupId, sessionId });
            return true;
        }
        if (typeof openWorkspaceWindow !== 'function') {
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
                setAgentDashboardNotice('');
                closeAgentDashboardDialog();
                return true;
            }
        } catch (error) {
            console.error('[GridVibe Dashboard] could not open the workspace:', error);
            setAgentDashboardNotice('Could not open that workspace window.');
            return false;
        }
        /* In browser mode the one way this fails is a blocked pop-up, and
           workspaces.js already owns the single wording for it. The dialog
           stays open: it is where the message is. */
        setAgentDashboardNotice(
            typeof WORKSPACE_TAB_BLOCKED_HINT === 'string'
                ? WORKSPACE_TAB_BLOCKED_HINT
                : 'Could not open that workspace window.'
        );
        return false;
    }

    /* ── Dismissal ──

       Four ways out. Three are the ones every dialog in this app has — the ×
       in the title bar, a press on the backdrop, and Escape — and the fourth
       is leaving the window.

       **Leaving is the same gesture as pressing the backdrop, one level out.**
       Every GridVibe window carries this dialog, and it is a surface on the
       window it was raised from rather than a window of its own; clicking
       across to another workspace is the reader saying they are done with it
       exactly as clicking beside it is. Left open, that window sits behind
       whichever one they moved to still showing a tree it has stopped polling
       — a stale answer they did not ask to keep — and comes back to the front
       later still wearing it.

       Two events say it, because neither says it everywhere. `blur` is the
       one that fires when another window takes the focus, which is the case
       the reader is actually in; `visibilitychange` covers a tab that is put
       behind another tab or a window that is minimized. They overlap almost
       always and close is idempotent, so overlapping costs nothing and the
       gap either one leaves is covered.

       This is also why the cross-window claim above is a backstop rather than
       the mechanism: opening the dialog anywhere requires focusing that window
       first, so in any host that reports deactivation the previous one has
       already put itself away before the claim is even sent. The claim is what
       keeps the invariant in a host that does not.

       Escape is the one with a rule. A session × here opens the close prompt
       *on top* of this dialog, and that prompt has an Escape handler of its
       own, so an unguarded one here would dismiss both surfaces on one press —
       the reader cancels a close and loses the list they were working through.
       So the key is answered only while this is the top `.modal-shell` on the
       page. Nothing here calls `preventDefault`: the page's own handlers read
       `defaultPrevented`, and a dialog that claimed the key would silently
       change what Escape means everywhere behind it. */
    function agentDashboardEscapeBelongsHere() {
        if (!agentDashboardDialogOpen()) {
            return false;
        }
        const shell = agentDashboardShell();
        const open = document.querySelectorAll?.('.modal-shell.visible') || [];
        return ![...open].some(other => other !== shell);
    }

    function wireAgentDashboardDismissal(shell) {
        shell.addEventListener('click', event => {
            /* The backdrop and only the backdrop — the same test
               `openGenericConfirmModal` uses for its own. */
            if (event.target === shell) {
                closeAgentDashboardDialog();
            }
        });
        document.getElementById(AGENT_DASHBOARD_CLOSE_BTN_ID)
            ?.addEventListener('click', () => closeAgentDashboardDialog());
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && agentDashboardEscapeBelongsHere()) {
                closeAgentDashboardDialog();
            }
        });
        window.addEventListener?.('blur', () => closeAgentDashboardDialog());
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                closeAgentDashboardDialog();
            }
        });
    }

    function wireAgentDashboard() {
        const shell = agentDashboardShell();
        const body = document.getElementById(AGENT_DASHBOARD_BODY_ID);
        if (_agentDashboardWired || !shell || !body) {
            return;
        }
        _agentDashboardWired = true;
        /* Delegated: every row is rebuilt whenever the reading changes, so a
           listener on a row would not outlive the reading that drew it. */
        body.addEventListener('click', event => {
            const row = event.target?.closest?.('[data-dashboard-action]');
            if (!row) {
                return;
            }
            event.preventDefault();
            const action = row.dataset.dashboardAction || '';
            const actions = dashboardCloseActions();
            /* The close verbs and the open verbs share one listener because
               they share one set of rows; what separates them is the action
               the row states, never which element it is. A close ends something
               and leaves the reader here, so it does not shut the dialog. */
            if (actions?.handles(action)) {
                actions.run(action, {
                    workspaceId: row.dataset.workspaceId || '',
                    groupId: row.dataset.groupId || '',
                    name: row.dataset.sessionName || '',
                    label: row.dataset.workspaceLabel || '',
                    groupCount: Number(row.dataset.groupCount) || 0
                }, row);
                return;
            }
            openDashboardTarget({
                workspaceId: row.dataset.workspaceId,
                groupId: row.dataset.groupId || '',
                sessionId: row.dataset.sessionId || ''
            });
        });
        document.getElementById(AGENT_DASHBOARD_REFRESH_BTN_ID)?.addEventListener('click', () => {
            refreshAgentDashboard();
        });
        wireAgentDashboardDismissal(shell);
        wireAgentDashboardExclusivity();
        /* There is deliberately nothing here for coming *back* to the window.
           A `visibilitychange` that re-armed the poll and a `focus` that read
           once both existed to revive a dialog that had gone quiet while the
           reader was elsewhere, and a dialog no longer survives the reader
           going elsewhere: it was dismissed, not suspended. Reviving one would
           put the stale surface this rule removes back on screen a second time.

           The native bridge is not there when the first tree is painted, and
           the *window* close verb exists only when it is. The repaint skip
           compares the reading, which has not changed — so the rendered
           structure is dropped explicitly, or the rows would keep their
           browser-mode shape for as long as the page lives. */
        window.addEventListener?.('pywebviewready', () => {
            _agentDashboardStructure = '';
            _agentDashboardPainted = '';
            refreshAgentDashboard();
        });
        /* Not a dismissal: the page itself is going away, so this runs
           whatever the dialog's state, and it is the abort that matters. */
        window.addEventListener?.('pagehide', () => {
            clearInterval(_agentDashboardTimer);
            _agentDashboardTimer = null;
            ++_agentDashboardRequestId;
            _agentDashboardController?.abort();
            _agentDashboardController = null;
        });
        window.addEventListener?.('pageshow', event => {
            if (event.persisted && agentDashboardDialogOpen()) {
                /* Frozen pages receive no messages, so a dialog coming back out
                   of the back/forward cache has missed every claim made while
                   it was away and may now be the second one up. It claims
                   again rather than reading anything: it is the surface in
                   front of the reader, so it is the one that should win. */
                publishAgentDashboardClaim();
                scheduleAgentDashboardRefresh();
                refreshAgentDashboard();
            }
        });
    }
