    /* ─────────────────────────────
       The agent dashboard — every agent running, wherever it runs, and every
       session you might want to get to.

       GridVibe can have several workspace windows open, each with several
       session tabs, each with several panes, and the only way to find out which
       agents are working anywhere is to go and look in every window. This is
       that answer in one surface: every live workspace, the sessions inside it,
       and the agents inside those — what each one is announcing and whether it
       is doing anything.

       **One shared session list, with selected crews beside it.** The
       docked sidebar supplies the list renderer, decorations and lane wires.
       Right-click or ContextMenu / Shift+F10 selects a crew for the board.

       **It lists every session, and it is still about the agents.** Being the
       one place all of them are named at once makes it the fastest way to
       *reach* any of them, so a session with no agent in it is a row here too.
       What keeps that from burying the agents is order and not omission: the
       server sorts every agent-free session to the end of its workspace and
       every agent-free workspace to the end of the tree, and a card with none
       says so in one muted line instead of drawing rows it does not have.

       **It is a dialog, and it was a window.** The window was the wrong shape
       for the question: you ask "what is running elsewhere?" *while* you are
       somewhere, and answering it took the reader out of the window they were
       working in, gave the answer a taskbar entry of its own to find again, and
       needed two chords of its own to get back. So it now opens over the page
       that asked for it and a press outside it puts that page back — on both
       pages, from the same partial and this one module. Five consequences,
       each of which is why something below is not the obvious code:

         · **It polls only while it is open and its page can be seen.** The
           reading is a whole-tree compose every couple of seconds, and a
           background window quietly refetching the state of every workspace
           forever is exactly the cost a panel is supposed to avoid. Opening
           arms the poll and reads once immediately; closing disarms it and
           aborts what is in flight. A dialog now outlives the reader leaving
           the window, so "open" no longer implies "being looked at": a hidden
           document stands the poll down exactly as a shut one does, and coming
           back into view re-arms it and reads once.
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
         · **Acting closes it only when the act lands here.** Every row is a
           way somewhere else, and a dialog still covering the pane it has just
           taken you to is in the way — so a row for the workspace this page
           *is* takes the dialog with it. A row for anywhere else leaves it
           standing: the reader asked for that pane *and* for this list, and
           this surface is no longer covering either. Leaving the window is the
           same answer one level out — the dialog stays up on the window it was
           raised from while the reader works in another one, which is the
           whole reason to raise it on a screen the work is not on. The close
           verbs do not close it either; they end something and leave you here.
         · **There is one of it, across every window.** Every GridVibe window
           carries this dialog, so raising it is a gesture the app has several
           of — and two of them up at once is two readings of one tree drifting
           apart on their own polls. Opening broadcasts a claim and the newest
           open wins; nothing ever refuses to open, so a window that died with
           its dialog up cannot leave the button dead everywhere else.

       The rest is what the reading itself is for:

         · **One request, not N+1.** `/api/dashboard` composes the whole tree
           server-side, ordered and already reduced to agent *panes*. A page
           that asked for workspaces, then groups per workspace, then panes per
           group would render a tree assembled out of several different
           moments, and the moments that disagree are exactly the ones worth
           showing.
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

    let _agentDashboardList = null;
    let _agentDashboardSnapshot = null;
    const _agentDashboardSelectedCrews = new Set();

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

    function dashboardCrewModel() {
        return typeof window !== 'undefined' ? window.GridVibeAgentCrews : undefined;
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

    /* What the pane runs on, which is a fact about the pane and no longer a
       column on the row. It was a chip between the agent's name and its chat
       title, and on a dialog this width that chip was costing the title more
       room than the answer was worth: the shell an agent runs on is looked up
       when something is wrong with it, not scanned down a card. So it is the
       last line of the row's own hover now, beside where the pane is -- asked
       for rather than read. */
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

    /* Whether this pane's agent was handed GridVibe's own tools, as the chip
       that says so. `agent-identity.js`'s rule and not a second reading of
       `agent_mcp` here: the pane header states the same fact about the same
       pane, and the flag is meaningless on a pane that is no longer running an
       agent. Empty for every pane that has none, which draws nothing. */
    function dashboardMcpTag(pane) {
        const identity = dashboardIdentity();
        return identity ? identity.paneAgentMcpTag(pane) : '';
    }

    function dashboardMcpTagTitle(pane) {
        const identity = dashboardIdentity();
        return identity ? identity.paneAgentMcpTagTitle(pane) : '';
    }

    /* The pane header's reading for the shared list's red MCP frame. */
    function dashboardMcpOverride(pane) {
        const identity = dashboardIdentity();
        return Boolean(identity && identity.paneAgentMcpOverride(pane));
    }

    /* What the docked sidebar says about a pane's agent on its mark instead of
       in chips: the frame GridVibe tools put around it (blue, red in override
       mode: the pane header's rule and hover, asked of the same functions) and
       the pin for auto-approval. `mcpTitle` and `autoTitle` are the sentences
       the mark's hover carries, and are empty when there is nothing to say. */
    const DASHBOARD_AUTO_TITLE = 'This agent runs with auto-approval: it acts without asking first';

    function dashboardAgentMarkState(pane) {
        const mcp = Boolean(dashboardMcpTag(pane));
        const auto = Boolean(pane?.agent_auto_mode);
        return {
            mcp,
            override: mcp && dashboardMcpOverride(pane),
            auto,
            mcpTitle: mcp ? dashboardMcpTagTitle(pane) : '',
            autoTitle: auto ? DASHBOARD_AUTO_TITLE : ''
        };
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

    /* `crew` is optional: `dashboardCrewContext()`'s answer, handed in by a
       surface that draws crews. A worker's hover then gains the line naming
       who it is working for, after the chat line and its path and before the
       shell. */
    function dashboardPaneHover(pane, crew) {
        const identity = dashboardIdentity();
        const working = dashboardCrewHoverLine(pane, crew);
        if (!identity) {
            return [dashboardPaneLine(pane), working].filter(Boolean).join('\n');
        }
        const chat = identity.paneChatTooltip(
            pane, Number(pane?.index) || 0, dashboardAgentOptions()
        );
        return [chat, working, dashboardTransportLabel(pane)].filter(Boolean).join('\n');
    }

    /* ── Crews ──

       Which agent handed a task to which is `agent-crews.js`'s reading of the
       payload's `links`; what a row *says* about it is this module's, like
       every other field on a row, so the docked sidebar asks for these by name
       too. None of them is part of a row's markup on the sidebar: they change
       on a report or a new round, and a row is not rebuilt for either. */

    /* The crew index plus every listed pane by session id, which is what the
       worker's hover needs to name its orchestrator. Null when the crew module
       is not on the page. */
    function dashboardCrewContext(snapshot) {
        const model = dashboardCrewModel();
        if (!model) return null;
        const panes = new Map();
        (snapshot?.workspaces || []).forEach(workspace => {
            (workspace?.groups || []).forEach(group => {
                (group?.panes || []).forEach(pane => {
                    const id = String(pane?.session_id || '');
                    if (id) panes.set(id, pane);
                });
            });
        });
        return { crews: model.indexCrews(snapshot), panes };
    }

    /* "Working for <agent> · <its chat line>", plus the round from round 2. */
    function dashboardCrewHoverLine(pane, crew) {
        const link = crew?.crews?.byWorker?.get(String(pane?.session_id || ''));
        const requester = link
            ? crew.panes?.get(String(link.requester_session_id || ''))
            : null;
        if (!requester) return '';
        const round = Number(link.round) || 1;
        return `Working for ${dashboardAgentName(requester)} · ${dashboardPaneLine(requester)}`
            + (round >= 2 ? ` · round ${round}` : '');
    }

    /* A dot with three strokes fanning right: tasks handed out. */
    const DASHBOARD_CREW_GLYPH = '<svg class="dash-crew-glyph" viewBox="0 0 12 12"'
        + ' aria-hidden="true" focusable="false">'
        + '<circle cx="2.5" cy="6" r="1.8" fill="currentColor"/>'
        + '<path d="M4 6 L10 2 M4 6 H10 M4 6 L10 10" stroke="currentColor"'
        + ' stroke-width="1.3" fill="none" stroke-linecap="round"/></svg>';

    /* The orchestrator's chip: how many of the agents it handed tasks to have
       reported, as `reported/total`, counted per worker. The count is drawn and
       the sentence is the chip's hover and, out of flow, the row's accessible
       name, so the fraction is never the only statement of it. Empty on every
       row that handed nothing out. */
    function dashboardCrewChipHtml(pane, crews) {
        const model = dashboardCrewModel();
        if (!model || !crews) return '';
        const { total, reported } = model.crewSummary(crews, pane?.session_id);
        if (!total) return '';
        const words = `Handed ${total === 1 ? 'a task to 1 agent' : `tasks to ${total} agents`}; `
            + `${reported} reported`;
        return `<span class="dash-crew-chip" title="${escHtml(words)}">${DASHBOARD_CREW_GLYPH}`
            + `<span class="dash-crew-count" aria-hidden="true">${reported}/${total}</span>`
            + `<span class="dash-crew-word">${escHtml(words)}</span></span>`;
    }

    /* Waiting on another agent -- an orchestrator in `wait_for_results`, or a
       worker standing by in `wait_for_task` -- in the crew module's words.
       Empty when the pane is not waiting, or the module is not on the page. */
    function dashboardPaneWaitingWord(pane) {
        const model = dashboardCrewModel();
        return model ? model.waitingWord(pane?.waiting) : '';
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
       different question. The transport's own word wins while there is one.
       After it, waiting on another agent wins over the activity reading: an
       agent CLI blocked in a GridVibe tool call can look busy, and it is not
       working -- the server already leaves it out of `totals.working`. */
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
        return dashboardPaneWaitingWord(pane) || dashboardStateWord(pane?.activity);
    }

    /* Which of the state hues the dot wears. Kept separate from the word
       above because an unreachable pane and a quiet one are the same colour
       only by accident. Same order: transport, then waiting, then activity. */
    function dashboardPaneStateKey(pane) {
        const status = String(pane?.status || '');
        if (status === 'error' || status === 'disconnected') {
            return 'error';
        }
        if (status === 'connecting' || status === 'pending') {
            return 'unknown';
        }
        if (dashboardPaneWaitingWord(pane)) {
            return 'waiting';
        }
        return String(pane?.activity?.state || 'unknown');
    }

    /* The state, as one mark at the head of the row.

       It used to be a word and a dot at the far end of the line, and both of
       those were wrong for what the reading is used for. A card is scanned for
       "is anything still going", which is a question a colour answers before a
       word beside it is read at all -- and the word was answering it a second
       time, in the column furthest from where the eye starts, at the cost of
       about a fifth of the line the chat title was being truncated into.

       So the dot leads the row, in front of the mark and the name, and the word
       is no longer drawn. What a colour cannot carry -- how long it has been
       idle, "Connecting", "Disconnected" -- is the indicator's own hover, which
       sits inside the row's and so answers on the dot rather than on the row.
       The word itself stays in the markup for the reader who is not looking at
       it: the row is a button, and its accessible name is its contents. */
    function dashboardActivityHtml(pane) {
        const stateKey = dashboardPaneStateKey(pane);
        const word = dashboardPaneStateWord(pane);
        return `
            <span class="dash-activity dash-state-${escHtml(stateKey)}" title="${escHtml(word)}">
                <span class="dash-state-dot" aria-hidden="true"></span>
                <span class="dash-state-word">${escHtml(word)}</span>
            </span>
        `;
    }

    /* How far along, which was always a separate question from whether anything
       is happening and is now drawn in a separate place. Every agent has a
       state; only the few that speak the progress sequence have a percentage,
       so the bar stays at the end of the row, where there is width for it and
       where it does not widen the one-mark column the dot now owns. Drawn only
       when a percentage was actually published, or when the agent said it is
       busy without saying how far along it is. */
    function dashboardProgressHtml(pane) {
        const activity = pane?.activity;
        const stateKey = dashboardPaneStateKey(pane);
        const progressState = pane?.status === 'connected' && activity?.progress_fresh !== false
            ? String(activity?.progress_state || '') : '';
        const value = Math.max(0, Math.min(100, Number(activity?.progress_value) || 0));
        const determinate = stateKey === 'working' && progressState === 'normal';
        const indeterminate = !determinate
            && stateKey === 'working'
            && ['indeterminate', 'warning', 'normal'].includes(progressState);
        if (!determinate && !indeterminate) {
            return '';
        }
        return `
            <span class="dash-progress dash-progress-${escHtml(progressState || 'normal')}"
                role="progressbar" aria-label="Agent progress" aria-valuemin="0" aria-valuemax="100"
                ${determinate ? `aria-valuenow="${value}"` : ''}>
                <span
                    class="dash-progress-fill${indeterminate ? ' is-indeterminate' : ''}"
                    ${determinate ? `style="width:${Math.max(0, Math.min(100, value))}%"` : ''}
                ></span>
            </span>
            ${determinate ? `<span class="dash-progress-value">${value}%</span>` : ''}
        `;
    }

    function dashboardCloseActions() {
        return typeof window !== 'undefined' ? window.GridVibeDashboardClose : undefined;
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

    /* ── The crew board ──

       The dialog's answer to "how far along is this crew": one box per crew,
       the orchestrator on the left and the agents it handed tasks to in
       columns to its right, one column per depth, a parent centred on its
       children and a fan of wires between them. The board has its own slot;
       the shared list is untouched by board updates.

         · **The dialog is three quarters of the window while a crew exists.**
           That is the most it takes, in width and in height, and a crew larger
           than that scrolls inside the board. The board and
           the list are two panes side by side, each scrolling on its own, and
           on a window too narrow for two they stack. With no selected crew
           the dialog is the compact list column.

         · **It is laid out from the crew index, never from `link_id`.** A new
           round mints a new id for the same pair, and a board keyed by it
           would rebuild every node on every `send_task`. Nodes are named by
           session id and crews by their root, in selection order.
         · **It has its own structure key.** What changes the board's shape --
           who is in which crew and where, whether a pane is still open, which
           agent and session it is -- rebuilds the board and nothing else. What
           a node *says* (its title, its reading, its pill, its round, its age)
           is written into its own slot, the way a row's reading is, so a
           report, a phase change or a new round keeps every node element, the
           caret and the scroll, and only the wires are redrawn.
         · **Only live panes are on it.** The server stops publishing a link
           when either pane closes, so a closed worker is gone from the board,
           its wire and the counts at once; an agent that exits while its pane
           stays open keeps its node, with an `ended` pill. A node is a button
           with a row's own target, so it lands where the row does.
         · **Narrow, it is one column.** Under the session card's own
           breakpoint the grid collapses to one indented column (a container
           query on the board), and the wires switch from fans to lanes: the
           layer is made again for the mode the board is drawn in. */

    /* The width under which the board is one column: `.dash-session`'s own
       container breakpoint, so the board and the cards below it give up their
       wide layout together. Kept equal to the `@container dash-crews` rule. */
    const DASHBOARD_CREW_NARROW_PX = 380;

    /* A report's hover goes on to say whether the orchestrator has collected
       it: the difference lives in words and never in a second look. */
    const DASHBOARD_CREW_PHASE_HOVERS = Object.freeze({
        handed: 'Handed a task it has not read yet',
        working: 'Working on its task',
        done: 'Reported done',
        failed: 'Reported that it failed',
        blocked: 'Reported that it is blocked',
        ended: 'Ended before it reported'
    });

    /* Why an assignment ended, by the results store's end-reason key. An
       agent that exits or is swapped leaves its pane row standing while its
       wire ends, which reads differently from a closed pane, so the pill's
       hover says which it was. */
    const DASHBOARD_CREW_END_REASONS = Object.freeze({
        'connection closed': "Its pane's connection closed before it reported",
        'pane closed': 'Its pane was closed before it reported',
        'pane relaunched': 'Its pane was relaunched before it reported',
        'pane mode changed': 'Its pane was switched to another mode before it reported',
        replaced: 'Its pane was handed a different task before it reported',
        'agent exited': 'Its agent exited before it reported',
        'agent replaced': 'A different agent was started in its pane before it reported',
        undeliverable: 'Its agent started without GridVibe\'s tools, so the task never reached it',
        restarted: 'GridVibe restarted before it reported'
    });

    /* A restored report's clause in place of the collected one: its text went
       with the process that held it, so nothing is left to collect. */
    const DASHBOARD_CREW_RESTORED_REPORT = 'reported before GridVibe restarted; the report was not kept';

    /* What the board last drew: its structure key and what each node and crew
       head said, so an unchanged poll writes nothing. */
    let _agentDashboardBoard = { key: '', nodes: new Map(), heads: new Map() };
    /* The board's wire layer, with the container and mode it was made for. */
    let _agentDashboardCrewWires = null;

    /* Every crew as grid cells. A leaf takes one row and a parent spans its
       children's rows; depth is the column. `indexCrews` makes a forest, so
       the walk always ends. */
    function dashboardCrewBoardModel(snapshot, crew) {
        const roots = crew?.crews?.roots || [];
        if (!roots.length) return [];
        const groups = new Map();
        (snapshot?.workspaces || []).forEach(workspace => {
            (workspace?.groups || []).forEach(group => {
                const id = String(group?.group_id || '');
                if (id) groups.set(id, group);
            });
        });
        return roots.map(root => {
            const nodes = [];
            let row = 1;
            let depths = 1;
            const place = (id, depth, link) => {
                const at = nodes.length;
                nodes.push(null);
                const start = row;
                const children = (crew.crews.byRequester.get(id) || [])
                    .filter(child => crew.panes.has(String(child.worker_session_id || '')));
                if (!children.length) row += 1;
                children.forEach(child => place(String(child.worker_session_id || ''), depth + 1, child));
                depths = Math.max(depths, depth + 1);
                const pane = crew.panes.get(id);
                const groupId = String(pane.group_id || '');
                nodes[at] = {
                    id, depth, link, pane, groupId,
                    group: groups.get(groupId) || null,
                    row: start,
                    span: Math.max(1, row - start)
                };
            };
            place(String(root), 0, null);
            return { root: String(root), depths, nodes };
        });
    }

    /* The board's shape. Never `link_id` and never a phase: a new round or a
       report is something a node says, not a different board. */
    function dashboardCrewBoardKey(board) {
        if (!board.length) return '';
        return JSON.stringify(board.map(crewBox => [crewBox.root, crewBox.depths, crewBox.nodes.map(node => [
            node.id, node.depth, node.row, node.span,
            dashboardAgentGlyphKey(node.pane), dashboardAgentName(node.pane),
            String(node.pane.workspace_id || ''), node.groupId,
            String(node.group?.name || node.group?.group_id || '')
        ])]));
    }

    function dashboardCrewPhaseClasses(link) {
        return `is-${dashboardCrewModel().linkPhase(link)}`;
    }

    /* The pill on a worker's node is its link's phase. The orchestrator's says
       how many it is waiting on, while it is. */
    function dashboardCrewPillHtml(node, crew) {
        const model = dashboardCrewModel();
        if (node.link) {
            const phase = model.linkPhase(node.link);
            let hover = DASHBOARD_CREW_PHASE_HOVERS[phase] || '';
            if (phase === 'ended') {
                const why = DASHBOARD_CREW_END_REASONS[String(node.link.reason || '')];
                hover = why ? `Ended: ${why}` : hover;
            } else if (model.isReportedPhase(phase) && node.link.restored) {
                hover += `; ${DASHBOARD_CREW_RESTORED_REPORT}`;
            } else if (model.isReportedPhase(phase)) {
                hover += model.linkCollected(node.link)
                    ? '; its orchestrator has collected the report'
                    : '; its orchestrator has not collected the report yet';
            }
            return `<span class="dash-crew-pill ${dashboardCrewPhaseClasses(node.link)}"`
                + ` title="${escHtml(hover)}">${escHtml(phase)}</span>`;
        }
        if (node.pane && String(node.pane.waiting || '') === 'crew') {
            const open = (crew.crews.byRequester.get(node.id) || [])
                .filter(link => ['handed', 'working'].includes(model.linkPhase(link))).length;
            return `<span class="dash-crew-pill is-waiting" title="${escHtml(dashboardPaneWaitingWord(node.pane))}">`
                + `waiting on ${open}</span>`;
        }
        return '';
    }

    /* How long since the task was handed, or since it was reported once it
       was, counted from the reading's own clock. */
    function dashboardCrewAgeHtml(link, now) {
        if (!link) return '';
        const reported = String(link.reported_at || '');
        const at = Date.parse(reported || String(link.handed_at || ''));
        if (!Number.isFinite(at) || !Number.isFinite(now)) return '';
        const label = dashboardIdleLabel(Math.max(0, now - at / 1000));
        if (!label) return '';
        return `<span title="${reported ? 'Reported' : 'Handed'} ${escHtml(label)} ago">${escHtml(label)}</span>`;
    }

    /* What a node says, slot by slot: each is compared and written on its own,
       so a new reading rewrites only what changed. */
    function dashboardCrewNodeParts(node, crew, now) {
        const round = Number(node.link?.round) || 1;
        return {
            name: dashboardPaneTitle(node.pane),
            line: node.link?.label || dashboardPaneLine(node.pane),
            hover: dashboardPaneHover(node.pane, crew),
            reading: dashboardActivityHtml(node.pane),
            pill: dashboardCrewPillHtml(node, crew),
            round: node.link && round >= 2 ? `round ${round}` : '',
            age: dashboardCrewAgeHtml(node.link, now)
        };
    }

    /* The crew's head: the whole crew's count and one segment per worker,
       by the phase of its current round. */
    function dashboardCrewHeadParts(crewBox) {
        const model = dashboardCrewModel();
        const workers = crewBox.nodes.filter(node => node.link);
        const reported = workers.filter(node => model.isReportedPhase(model.linkPhase(node.link))).length;
        const root = crewBox.nodes[0];
        return {
            line: dashboardPaneLine(root.pane),
            meta: `${reported} of ${workers.length} reported`,
            segments: workers
                .map(node => `<i class="dash-crew-segment ${dashboardCrewPhaseClasses(node.link)}"></i>`)
                .join('')
        };
    }

    function dashboardCrewSessionHtml(node) {
        const name = String(node.group?.name || node.group?.group_id || '');
        if (!name) return '';
        const colour = dashboardSessionColour();
        const style = colour && node.groupId
            ? ` style="--dash-session-color:${escHtml(colour.sessionColour(node.groupId))}"`
            : '';
        return `<span class="dash-crew-session"${style} title="${escHtml(name)}">${escHtml(name)}</span>`;
    }

    /* One node in its grid cell: a button carrying a row's own target
       attributes, so a press lands on its pane through `openDashboardTarget`;
       its key is its own, so a repaint finds the caret again on the node rather
       than on the row of the same pane. */
    function dashboardCrewNodeHtml(node, parts) {
        const who = node.pane;
        const id = escHtml(node.id);
        const classes = `dash-crew-node${node.depth === 0 ? ' is-root' : ''}`;
        const inner = `
                <span class="dash-crew-node-head">
                    <span class="dash-crew-reading">${parts.reading}</span>
                    <span class="dash-agent-icon" aria-hidden="true">${dashboardAgentGlyphHtml(who)}</span>
                    <span class="dash-crew-name">${escHtml(parts.name)}</span>
                </span>
                <span class="dash-crew-line">${escHtml(parts.line)}</span>
                <span class="dash-crew-node-meta">
                    ${dashboardCrewSessionHtml(node)}
                    <span class="dash-crew-pill-slot">${parts.pill}</span>
                    <span class="dash-crew-round">${escHtml(parts.round)}</span>
                    <span class="dash-crew-age">${parts.age}</span>
                </span>`;
        const cell = `<div class="dash-crew-cell" style="--dash-crew-col:${node.depth + 1};`
            + `--dash-crew-row:${node.row};--dash-crew-span:${node.span};--dash-crew-depth:${node.depth}">`;
        return `${cell}
            <button
                type="button"
                class="${classes}"
                data-agent="${escHtml(dashboardAgentGlyphKey(who))}"
                data-crew-node="${id}"
                data-dashboard-action="pane"
                data-dashboard-key="crew:${id}"
                data-workspace-id="${escHtml(who.workspace_id || '')}"
                data-group-id="${escHtml(who.group_id || '')}"
                data-session-id="${id}"
                title="${escHtml(parts.hover)}"
            >${inner}</button></div>`;
    }

    /* The board, its key, and what every node and head said, from one
       reading. Empty while there is no crew. */
    function dashboardCrewBoardDraw(snapshot, crew) {
        const board = dashboardCrewModel() ? dashboardCrewBoardModel(snapshot, crew) : [];
        const nodes = new Map();
        const heads = new Map();
        if (!board.length) return { key: '', html: '', nodes, heads, depths: 0 };
        const generated = Number(snapshot?.generated_at);
        const now = Number.isFinite(generated) && generated > 0 ? generated : Date.now() / 1000;
        const crewsHtml = board.map(crewBox => {
            const head = dashboardCrewHeadParts(crewBox);
            heads.set(crewBox.root, head);
            const root = crewBox.nodes[0].pane;
            const cells = crewBox.nodes.map(node => {
                const parts = dashboardCrewNodeParts(node, crew, now);
                nodes.set(node.id, parts);
                return dashboardCrewNodeHtml(node, parts);
            }).join('');
            return `
                <div class="dash-crew" data-crew-root="${escHtml(crewBox.root)}"
                    style="--dash-crew-depths:${crewBox.depths}"
                    data-dashboard-key="crew-frame:${escHtml(crewBox.root)}"
                    data-session-id="${escHtml(crewBox.root)}"
                    role="group" tabindex="0" aria-label="Crew diagram"
                    title="Click empty space to highlight this crew. Click again to clear.">
                    <button type="button" class="dash-session-close dash-crew-close"
                        data-dashboard-action="hide-crew" data-crew-id="${escHtml(crewBox.root)}"
                        data-session-id="${escHtml(crewBox.root)}"
                        data-dashboard-key="hide-crew:${escHtml(crewBox.root)}"
                        title="Hide this crew diagram" aria-label="Hide this crew diagram">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12"/></svg>
                    </button>
                    <div class="dash-crew-head" data-agent="${escHtml(dashboardAgentGlyphKey(root))}">
                        <span class="dash-agent-icon" aria-hidden="true">${dashboardAgentGlyphHtml(root)}</span>
                        <span class="dash-crew-title"><span class="dash-crew-agent">${escHtml(dashboardAgentName(root))}</span>`
                + ` · <span class="dash-crew-title-line">${escHtml(head.line)}</span></span>
                        <span class="dash-crew-meta">${escHtml(head.meta)}</span>
                    </div>
                    <span class="dash-crew-segments" aria-hidden="true">${head.segments}</span>
                    <div class="dash-crew-grid">${cells}</div>
                </div>`;
        }).join('');
        const html = `
            <section class="dash-crews" aria-labelledby="agentDashboardCrewsTitle">
                <header class="dash-workspace-head dash-crews-head">
                    <h3 class="dash-workspace-name dash-crews-title" id="agentDashboardCrewsTitle">Crews</h3>
                </header>
                <div class="dash-crews-board">${crewsHtml}</div>
            </section>`;
        return { key: dashboardCrewBoardKey(board), html, nodes, heads,
            depths: Math.max(...board.map(crewBox => crewBox.depths)) };
    }

    /* The "Crews" section, or nothing while no agent has handed a task out.
       `crew` is `dashboardCrewContext()`'s answer for the same reading. */
    function dashboardCrewBoardHtml(snapshot, crew) {
        return dashboardCrewBoardDraw(snapshot, crew === undefined ? dashboardCrewContext(snapshot) : crew).html;
    }

    /* What the dialog says while no agent has handed a task to another: the
       only thing on it once the list is off, so it names what would appear. */
    function dashboardCrewSlotHtml(draw) {
        return draw.html;
    }

    function dashboardWriteSlot(element, selector, property, value, before) {
        if (before !== undefined && value === before) return;
        const slot = element.querySelector?.(selector);
        if (slot) slot[property] = value;
    }

    /* A reading whose rows kept their shape. The board is rebuilt alone when
       its own shape changed, and otherwise written slot by slot. */
    function paintAgentDashboardCrewBoard(body, snapshot, draw) {
        const previous = _agentDashboardBoard;
        if (!draw.key && !previous.key) return;
        const slot = body.querySelector?.('[data-dashboard-crews]');
        if (!slot) {
            return;
        } else if (draw.key !== previous.key) {
            const active = document.activeElement;
            const focusedKey = slot.contains?.(active) ? active?.dataset?.dashboardKey || '' : '';
            const scrollLeft = slot.querySelector?.('.dash-crews-board')?.scrollLeft || 0;
            slot.innerHTML = dashboardCrewSlotHtml(draw);
            if (scrollLeft) {
                const board = slot.querySelector?.('.dash-crews-board');
                if (board) board.scrollLeft = scrollLeft;
            }
            if (focusedKey) {
                const replacement = slot.querySelector?.(`[data-dashboard-key="${focusedKey}"]`)
                    || document.getElementById('agentDashboardListBody')
                        ?.querySelector?.(`[data-dashboard-key="pane:${active?.dataset?.sessionId || ''}"]`)
                    || body.parentElement;
                replacement?.focus?.({ preventScroll: true });
            }
        } else {
            slot.querySelectorAll?.('[data-crew-node]').forEach(element => {
                const id = element.dataset?.crewNode || '';
                const next = draw.nodes.get(id);
                const was = previous.nodes.get(id);
                if (!next) return;
                if (!was || next.hover !== was.hover) element.title = next.hover;
                dashboardWriteSlot(element, '.dash-crew-name', 'textContent', next.name, was?.name);
                dashboardWriteSlot(element, '.dash-crew-line', 'textContent', next.line, was?.line);
                dashboardWriteSlot(element, '.dash-crew-reading', 'innerHTML', next.reading, was?.reading);
                dashboardWriteSlot(element, '.dash-crew-pill-slot', 'innerHTML', next.pill, was?.pill);
                dashboardWriteSlot(element, '.dash-crew-round', 'textContent', next.round, was?.round);
                dashboardWriteSlot(element, '.dash-crew-age', 'innerHTML', next.age, was?.age);
            });
            slot.querySelectorAll?.('[data-crew-root]').forEach(element => {
                const id = element.dataset?.crewRoot || '';
                const next = draw.heads.get(id);
                const was = previous.heads.get(id);
                if (!next) return;
                dashboardWriteSlot(element, '.dash-crew-title-line', 'textContent', next.line, was?.line);
                dashboardWriteSlot(element, '.dash-crew-meta', 'textContent', next.meta, was?.meta);
                dashboardWriteSlot(element, '.dash-crew-segments', 'innerHTML', next.segments, was?.segments);
            });
        }
        _agentDashboardBoard = { key: draw.key, nodes: draw.nodes, heads: draw.heads };
    }

    function agentDashboardWiresPaused() {
        return !agentDashboardDialogOpen() || Boolean(document.hidden);
    }

    function dashboardCrewBoardNarrow(board) {
        const width = Number(board?.clientWidth) || 0;
        return width > 0 && width <= DASHBOARD_CREW_NARROW_PX;
    }

    /* A node by session id. */
    function dashboardCrewEndpoint(container, id) {
        const escape = window.CSS?.escape || (value => String(value).replace(/["\\]/g, '\\$&'));
        return container.querySelector(`[data-crew-node="${escape(id)}"]`);
    }

    function disposeAgentDashboardCrewWires() {
        const wires = _agentDashboardCrewWires;
        if (!wires) return;
        _agentDashboardCrewWires = null;
        wires.observer?.disconnect?.();
        wires.layer.dispose();
    }

    /* The board's fan of wires: one layer, scoped to the board, made again
       whenever the board element is replaced or the board crosses its narrow
       width, because a layer's mode is fixed when it is made. Its own observer
       repaints on a resize; this one only notices the mode changing. */
    function paintAgentDashboardCrewWires(snapshot, hasBoard) {
        const model = dashboardCrewModel();
        const board = hasBoard && model
            ? document.getElementById(AGENT_DASHBOARD_BODY_ID)?.querySelector?.('.dash-crews-board')
            : null;
        if (!board) {
            disposeAgentDashboardCrewWires();
            return;
        }
        const mode = dashboardCrewBoardNarrow(board) ? 'lane' : 'fan';
        let wires = _agentDashboardCrewWires;
        if (wires && (wires.container !== board || wires.mode !== mode)) {
            disposeAgentDashboardCrewWires();
            wires = null;
        }
        if (!wires) {
            wires = {
                container: board,
                mode,
                snapshot: null,
                observer: null,
                layer: model.createWireLayer({ container: board, mode, card: '', endpoint: dashboardCrewEndpoint })
            };
            wires.layer.setPaused(agentDashboardWiresPaused());
            const Observer = window.ResizeObserver;
            if (typeof Observer === 'function') {
                const own = wires;
                own.observer = new Observer(() => {
                    if (_agentDashboardCrewWires !== own || !own.snapshot) return;
                    if ((dashboardCrewBoardNarrow(board) ? 'lane' : 'fan') !== own.mode) {
                        paintAgentDashboardCrewWires(own.snapshot, true);
                    }
                });
                own.observer.observe(board);
            }
            _agentDashboardCrewWires = wires;
        }
        wires.snapshot = snapshot;
        wires.layer.paint(snapshot);
        paintAgentDashboardCrewHighlight();
    }

    /* Permanent sibling slots: a list rebuild cannot replace the crew board. */
    function dashboardBodyHtml() {
        return '<div class="dash-dialog-list agent-sidebar-list" id="agentDashboardList">'
            + '<div class="dash-body agent-sidebar-body" id="agentDashboardListBody" data-dashboard-sessions></div></div>'
            + '<div class="dash-crews-slot" data-dashboard-crews hidden></div>';
    }

    function dashboardDialogList() {
        if (!_agentDashboardList && typeof window.createAgentDashboardList === 'function') {
            _agentDashboardList = window.createAgentDashboardList({
                ids: { shell: 'agentDashboardList', body: 'agentDashboardListBody' },
                inputTarget: () => '',
                notice: setAgentDashboardNotice,
                refresh: refreshAgentDashboard,
                targetOwnsNotice: true,
                crewSelected: root => _agentDashboardSelectedCrews.has(root),
                onCrewToggle: toggleAgentDashboardCrew,
                crewHighlight: dashboardCrewModel()?.highlightState
            });
            _agentDashboardList.wireRows();
        }
        return _agentDashboardList;
    }

    function toggleAgentDashboardCrew(root) {
        const context = dashboardCrewContext(_agentDashboardSnapshot);
        if (!context?.crews?.roots.includes(root)) return false;
        if (_agentDashboardSelectedCrews.has(root)) {
            _agentDashboardSelectedCrews.delete(root);
            dashboardCrewModel()?.highlightState?.clearRoot(root);
        } else {
            _agentDashboardSelectedCrews.add(root);
            dashboardDialogList()?.clearHighlight();
            dashboardCrewModel()?.highlightState?.clearClicks();
            dashboardCrewModel()?.highlightState?.clearRoot(root);
        }
        paintAgentDashboardSnapshot(_agentDashboardSnapshot);
        return true;
    }

    function paintAgentDashboardCrewHighlight() {
        const root = dashboardCrewModel()?.highlightState?.get() || '';
        const slot = document.getElementById(AGENT_DASHBOARD_BODY_ID)?.querySelector?.('[data-dashboard-crews]');
        const crews = [...(slot?.querySelectorAll?.('[data-crew-root]') || [])];
        const visible = crews.some(element => element.dataset?.crewRoot === root);
        slot?.classList?.toggle('is-crew-highlight', visible);
        crews.forEach(element => {
            element.classList?.toggle('is-crew-member', element.dataset?.crewRoot === root);
        });
        _agentDashboardCrewWires?.layer.highlight(visible ? root : '');
    }

    function selectAgentDashboardCrewFrame(frame) {
        const root = frame?.dataset?.crewRoot || '';
        if (!_agentDashboardSelectedCrews.has(root)) return false;
        dashboardCrewModel()?.highlightState?.toggleClick('board-click', root, root);
        return true;
    }

    function paintAgentDashboardSize(draw) {
        const dialog = document.getElementById(AGENT_DASHBOARD_BODY_ID)?.parentElement;
        dialog?.style?.setProperty('--dash-crew-depths', String(draw.depths || 1));
        dialog?.classList?.toggle?.('has-crews', Boolean(draw.key));
    }

    /* The three counts always, once anything is open. The agent count leads
       because it is what the surface is for, and it is stated even at zero:
       "no agents · 3 sessions · 1 workspace" is the whole reading in one line,
       and dropping the zero would leave the reader to work out from the
       absence of a word whether the count was zero or missing. */
    function dashboardTotalsText(snapshot) {
        const totals = snapshot?.totals || {};
        const agents = Number(totals.agents) || 0;
        const sessions = Number(totals.sessions) || 0;
        const workspaces = Number(totals.workspaces) || 0;
        if (!sessions && !workspaces) {
            return 'Nothing running';
        }
        return `${agents ? `${agents} agent${agents === 1 ? '' : 's'}` : 'no agents'} · `
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
        _agentDashboardSnapshot = snapshot;
        if (!_agentDashboardPainted) renderAgentDashboard(dashboardBodyHtml());
        const context = dashboardCrewContext(snapshot);
        const roots = context?.crews?.roots || [];
        dashboardCrewModel()?.highlightState?.reconcile(context?.crews, snapshot?.generated_at);
        for (const root of _agentDashboardSelectedCrews) {
            if (!roots.includes(root)) _agentDashboardSelectedCrews.delete(root);
        }
        const selected = context ? { ...context, crews: { ...context.crews,
            roots: [..._agentDashboardSelectedCrews] } } : null;
        const draw = dashboardCrewBoardDraw(snapshot, selected);
        dashboardDialogList()?.paintSnapshot(snapshot);
        paintAgentDashboardCrewBoard(body, snapshot, draw);
        const slot = body.querySelector?.('[data-dashboard-crews]');
        if (slot) slot.hidden = !draw.key;
        paintAgentDashboardSize(draw);
        // Measure only the selected crews; unrelated links have no endpoints here.
        const selectedSnapshot = { ...snapshot, links: (snapshot.links || []).filter(link =>
            _agentDashboardSelectedCrews.has(context?.crews?.rootOf?.get(String(link.requester_session_id || '')))) };
        paintAgentDashboardCrewWires(selectedSnapshot, Boolean(draw.key));
        paintAgentDashboardCrewHighlight();
        _agentDashboardList?.pause(agentDashboardWiresPaused());
        _agentDashboardStructure = 'ready';
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
        /* Side by side, the two panes scroll and the body does not; stacked,
           it is the other way round. Each is put back wherever it is one. */
        const scrolls = [body, ...body.querySelectorAll?.('[data-dashboard-crews], [data-dashboard-sessions]') || []]
            .map(element => [element.hasAttribute?.('data-dashboard-crews') ? 'crews'
                : element.hasAttribute?.('data-dashboard-sessions') ? 'sessions' : '', element.scrollTop]);
        body.innerHTML = html;
        _agentDashboardPainted = html;
        scrolls.forEach(([which, top]) => {
            const element = which ? body.querySelector?.(`[data-dashboard-${which}]`) : body;
            if (element) element.scrollTop = top;
        });
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

       Two conditions and not one, which is the pair the docked panel already
       asks. The dialog outlives the reader leaving the window — that is what
       makes it readable on a second screen while they work in another one — so
       **open** no longer implies "being looked at", and a minimized window or a
       tab behind another tab has to be asked about separately or it would
       compose the whole tree every two seconds for nobody. Unfocused is not
       hidden: a window sitting in plain sight on another monitor is exactly the
       case this dialog now exists to serve, and it keeps reading. */
    function scheduleAgentDashboardRefresh() {
        /* The board's wires stop with the poll. */
        _agentDashboardCrewWires?.layer.setPaused(agentDashboardWiresPaused());
        _agentDashboardList?.pause(agentDashboardWiresPaused());
        if (_agentDashboardTimer !== null) {
            clearInterval(_agentDashboardTimer);
            _agentDashboardTimer = null;
        }
        if (!agentDashboardDialogOpen() || document.hidden) {
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

       **Both transports, and the message is tagged.** A `BroadcastChannel`
       never delivers to the object that posted, but it *does* deliver to any
       other channel object in the same document — and the publisher below opens
       a fresh one per message while the listener holds one open — so skipping
       our own `source` is load-bearing here rather than tidy. `storage` covers
       what the channel cannot and genuinely never fires in the sending
       document.

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

       **The blur belongs to this window and stops at its edge.** It is the
       `.modal-shell` scrim, which is the page's own and reaches exactly as far
       as the page does. There used to be a second one: a short cross-window
       lease that dimmed every *other* GridVibe page while this dialog had
       focus, on the reasoning that a surface about the other windows is one the
       other windows step back for. That stopped being true when the dialog
       stopped putting itself away on `blur` — the reader now keeps it up
       precisely so they can work in another workspace, and dimming the window
       they are working in is dimming the wrong one. So the lease is gone
       rather than inverted: the one window holding the dialog wears the one
       blur it raised. */

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

    function openAgentDashboardDialog() {
        const shell = agentDashboardShell();
        if (!shell || agentDashboardDialogOpen()) {
            return false;
        }
        _agentDashboardOpener = document.activeElement || null;
        shell.classList.add('visible');
        shell.setAttribute('aria-hidden', 'false');
        /* Before the read: this is the one message that puts another window's
           dialog away, and it costs nothing to be first. */
        publishAgentDashboardClaim();
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
           the focused one, because a close can arrive from outside it: the
           cross-window claim puts this dialog away while the reader is over in
           the window that raised its own. `activeElement` does not move when a
           window is deactivated, so without `hasFocus()` that close would look
           exactly like an in-page dismissal and put the caret on a button in a
           window nobody is looking at — and in a host where `element.focus()`
           raises its window, would pull that window back in front of the one
           the reader just chose. */
        const heldFocus = Boolean(shell.contains?.(document.activeElement))
            && agentDashboardWindowHasFocus();
        shell.classList.remove('visible');
        _agentDashboardList?.clearHighlight();
        shell.setAttribute('aria-hidden', 'true');
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
       it, at the session it names. Whether the dialog goes with it is decided
       by *where* it landed, and that is the backdrop's question one level out.
       A row for the workspace this page already is takes the dialog with it —
       a surface still covering the pane it has just taken you to is in the way.
       A row for anywhere else leaves it standing: the pane the reader asked for
       came up in another window, this one is covering nothing they wanted, and
       having the list up while working somewhere else is what they raised it
       for.

       The workspace this page *is* is also the case the window could not have:
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
                /* Left up, deliberately: the pane that was asked for is in
                   another window now, so this dialog is not in front of it.
                   Only the last action's confirmation goes — it described a
                   press the reader has already moved on from. */
                setAgentDashboardNotice('');
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

       Three ways out, and they are the three every dialog in this app has: the
       × in the title bar, a press on the backdrop, and Escape. Every one of
       them is the reader saying so *on the window the dialog is on*.

       **Leaving the window is not one of them, and that is the change.** It
       used to be: the reasoning was that this is a surface on the window it was
       raised from rather than a window of its own, so clicking across to
       another workspace was the same gesture as clicking beside it, one level
       out. That was the wrong reading of what the dialog is for. It is the one
       surface that names what is running *everywhere*, which makes "keep it up
       while I work somewhere else" — on another monitor, in another workspace —
       the thing most worth doing with it, and a dialog that put itself away the
       moment the reader went and did that could never be used that way. So
       `blur` closes nothing and neither does a hidden document. What a hidden
       document does instead is stand the poll down
       (`scheduleAgentDashboardRefresh` above), which is the cost the dismissal
       was actually paying for; a window in plain sight on another screen is not
       hidden, and keeps reading.

       The backdrop keeps its meaning exactly, and is now the only press that
       carries it: beside the dialog, on the window holding it, is done with it.
       So is landing on a pane in *this* workspace, which is that same press
       arriving somewhere.

       **This makes the cross-window claim above the mechanism rather than a
       backstop.** Raising the dialog in another window is now the only thing
       that puts this one away without the reader touching it, so the claim is
       what keeps "there is one of it" true — in every host, rather than only in
       one that does not report deactivation.

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
    }

    function wireAgentDashboard() {
        const shell = agentDashboardShell();
        const body = document.getElementById(AGENT_DASHBOARD_BODY_ID);
        if (_agentDashboardWired || !shell || !body) {
            return;
        }
        _agentDashboardWired = true;
        dashboardCrewModel()?.highlightState?.subscribe(paintAgentDashboardCrewHighlight);
        body.addEventListener('keydown', event => {
            const frame = event.target?.closest?.('[data-crew-root]');
            if (event.target !== frame || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault();
            selectAgentDashboardCrewFrame(frame);
        });
        /* Delegated: every row is rebuilt whenever the reading changes, so a
           listener on a row would not outlive the reading that drew it. */
        body.addEventListener('click', event => {
            const row = event.target?.closest?.('[data-dashboard-action]');
            if (document.getElementById('agentDashboardListBody')?.contains?.(event.target)) return;
            if (!row) {
                const frame = event.target?.closest?.('[data-crew-root]');
                if (frame && selectAgentDashboardCrewFrame(frame)) return;
                dashboardCrewModel()?.highlightState?.clearClicks();
                return;
            }
            event.preventDefault();
            const action = row.dataset.dashboardAction || '';
            if (action === 'hide-crew') {
                const root = row.dataset.crewId || '';
                if (_agentDashboardSelectedCrews.has(root)) toggleAgentDashboardCrew(root);
                return;
            }
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
        /* Suspended, not dismissed. A document nobody can see keeps its
           dialog and stops reading for it, so coming back into view re-arms the
           poll and reads once rather than sitting out a tick showing the tree
           from before the window was put away. Only `visibilitychange` says
           this: `focus` is a window merely coming to the front, and the dialog
           never stopped reading for that. Both calls are safe on a shut dialog,
           which is why neither is guarded here. */
        document.addEventListener('visibilitychange', () => {
            scheduleAgentDashboardRefresh();
            if (!document.hidden) {
                refreshAgentDashboard();
            }
        });
        /* The native bridge is not there when the first tree is painted, and
           the *window* close verb exists only when it is. The repaint skip
           compares the reading, which has not changed — so the rendered
           structure is dropped explicitly, or the rows would keep their
           browser-mode shape for as long as the page lives. */
        window.addEventListener?.('pywebviewready', () => {
            _agentDashboardStructure = '';
            _agentDashboardList?.invalidate();
            refreshAgentDashboard();
        });
        /* Not a dismissal: the page itself is going away, so this runs
           whatever the dialog's state, and it is the abort that matters. A page
           going into the back/forward cache comes back with these same objects
           and nothing re-creates the list, so the list is only suspended there
           and keeps its subscription to the shared crew highlight. */
        window.addEventListener?.('pagehide', event => {
            clearInterval(_agentDashboardTimer);
            _agentDashboardTimer = null;
            ++_agentDashboardRequestId;
            _agentDashboardController?.abort();
            _agentDashboardController = null;
            if (event?.persisted) {
                _agentDashboardList?.clearHighlight();
                _agentDashboardList?.pause(true);
            } else {
                _agentDashboardList?.dispose();
            }
            disposeAgentDashboardCrewWires();
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
