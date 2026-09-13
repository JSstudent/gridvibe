/* GridVibeDashboardSidebar — the agent dashboard, docked to the workspace.

   The dialog answers "what is running elsewhere?" for the reader who stops to
   ask. This answers the same question for the reader who wants it *while* they
   work: the same tree, in a column down the left of the grid, staying up while
   they type in a pane. That is the whole of the difference, and every decision
   below follows from it.

     · **It is the dialog's reading, not a second one.** What a row is called,
       which mark it wears, what its dot means and how far along its bar is are
       all `dashboard-dialog.js`'s answers, handed in through the runtime. A
       second copy of any of them is how a row here and a row there come to
       disagree about the same pane. What this module owns is the *shape*: a
       column, and the row that fits in one.
     · **The row draws no agent name.** At a sixth of a window the line has
         room for a dot, a mark and a title, and of those three the name is the
         one the mark already answers — every agent GridVibe draws wears its own
         artwork in its own colour. So the name is not *dropped*, it stops being
         a column: it stays in the row's accessible name, out of flow, where a
         reader who is hearing the row still gets it and the title gets the
         width.
     · **It does not close when you leave.** The dialog dismisses itself on the
       reader going elsewhere, because a modal surface left standing over a
       window nobody is looking at is stale. This is chrome: it is *part* of the
       window, it holds its own state per workspace, and it is still there when
       the reader comes back — the way the top bar is.
     · **It polls only while it is open and the window is in front.** Open, it
       is a whole-tree compose on a timer; shut, or behind another window, it
       costs nothing at all. A panel that may be up all day cannot poll as hard
       as a dialog that is up for ten seconds, so its cadence is its own.
     · **Its state is the workspace's, and durable.** Which workspace has the
       panel up is a fact about that workspace window, so it rides the same
       ordered compare-and-swap transaction `topbar_visible` does and is written
       into the workspace snapshot — a restored workspace comes back with the
       panel it was saved with.

   Two halves, the split `minimize-all.js` and `dashboard-close.js` use:
   `policy` is pure — no DOM, no globals, no page — so the markup and the
   open/shut rules are executed by a Node test rather than asserted as source
   text; `create(runtime)` is the adapter and every DOM, network and timer
   touch goes through it.

   Loaded after `dashboard-dialog.js`, whose per-field renderers it is handed,
   and wired by `terminals.js`, which is the page that has a grid to dock to. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (!root) return;
    root.GridVibeDashboardSidebar = api;
    if (!root.document) return;

    const doc = root.document;

    /* The page states this as a top-level `const`, which is a *lexical* global
       and so is deliberately not a property of `window` -- reaching for it
       through `root` would read `undefined` and cache every workspace's panel
       under one key. A bare identifier behind `typeof` is how `agent-identity`
       and the dialog read the page's own constants, and it is what keeps this
       module loadable in Node, where the name does not exist at all. */
    const workspaceId = () => (
        typeof CURRENT_WORKSPACE_ID === 'string' ? CURRENT_WORKSPACE_ID : 'default'
    );

    const controller = api.create({
        getElement: id => doc.getElementById(id),
        setBodyClass: (name, on) => doc.body?.classList?.toggle(name, on),
        activeElement: () => doc.activeElement,
        onVisibilityChange: handler => doc.addEventListener('visibilitychange', handler),
        documentHidden: () => Boolean(doc.hidden),
        setInterval: (fn, ms) => root.setInterval(fn, ms),
        clearInterval: handle => root.clearInterval(handle),
        setTimeout: (fn, ms) => root.setTimeout(fn, ms),
        clearTimeout: handle => root.clearTimeout(handle),
        createAbortController: () => new AbortController(),
        fetchJson: async (url, options) => {
            const response = await root.fetch(url, options);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            return response.json();
        },
        /* Every field on a row is the dialog's answer, asked for by name at
           call time: these are the same functions the dialog paints its own
           rows with, so the two surfaces cannot drift. A page without the
           dialog loaded answers an empty string rather than throwing, which
           renders a row with a blank column instead of no panel at all. */
        render: {
            esc: value => (typeof root.escHtml === 'function' ? root.escHtml(value) : ''),
            activity: pane => (
                typeof root.dashboardActivityHtml === 'function'
                    ? root.dashboardActivityHtml(pane) : ''
            ),
            progress: pane => (
                typeof root.dashboardProgressHtml === 'function'
                    ? root.dashboardProgressHtml(pane) : ''
            ),
            glyph: pane => (
                typeof root.dashboardAgentGlyphHtml === 'function'
                    ? root.dashboardAgentGlyphHtml(pane) : ''
            ),
            glyphKey: pane => (
                typeof root.dashboardAgentGlyphKey === 'function'
                    ? root.dashboardAgentGlyphKey(pane) : 'default'
            ),
            line: pane => (
                typeof root.dashboardPaneLine === 'function'
                    ? root.dashboardPaneLine(pane) : ''
            ),
            hover: pane => (
                typeof root.dashboardPaneHover === 'function'
                    ? root.dashboardPaneHover(pane) : ''
            ),
            agentName: pane => (
                typeof root.dashboardAgentName === 'function'
                    ? root.dashboardAgentName(pane) : ''
            ),
            workspaceLabel: (workspace, index) => (
                typeof root.dashboardWorkspaceLabel === 'function'
                    ? root.dashboardWorkspaceLabel(workspace, index)
                    : `Workspace ${index + 1}`
            ),
            sessionColourStyle: group => (
                typeof root.dashboardSessionColourStyle === 'function'
                    ? root.dashboardSessionColourStyle(group) : ''
            ),
            totals: snapshot => (
                typeof root.dashboardTotalsText === 'function'
                    ? root.dashboardTotalsText(snapshot) : ''
            )
        },
        /* Where a dashboard row lands is `dashboard-dialog.js`'s answer, and
           a second copy of it here is how the same row comes to reach two
           different places from two surfaces. It is the one that knows a row
           naming *this* window must be applied directly rather than asked for
           through a bridge that would raise an already-raised window and fire
           no `focus` event for the stored target to be claimed on. */
        openTarget: target => (
            typeof root.openDashboardTarget === 'function'
                ? root.openDashboardTarget(target)
                : Promise.resolve(false)
        ),
        /* The local cache, so a reload paints the panel the workspace had
           before the server has answered — the same rule the top bar follows. */
        readStored: () => (
            typeof root.getStoredWorkspaceAgentSidebarOpen === 'function'
                ? root.getStoredWorkspaceAgentSidebarOpen(workspaceId())
                : null
        ),
        writeStored: open => {
            if (typeof root.storeWorkspaceAgentSidebarOpen === 'function') {
                root.storeWorkspaceAgentSidebarOpen(workspaceId(), open);
            }
        },
        /* The durable half: one ordered workspace-presentation transaction, the
           same one the top-bar chevron rides. */
        report: () => {
            if (typeof root.noteWorkspacePresentationChanged === 'function') {
                root.noteWorkspacePresentationChanged();
            }
        },
        /* Opening and shutting the column changes how wide every pane is. */
        onLayoutChanged: () => {
            if (typeof root.refitAttachedTerminalsForSurfaceMode === 'function') {
                root.refitAttachedTerminalsForSurfaceMode();
            }
        },
        logError: (message, error) => console.error(message, error)
    });

    root.wireAgentDashboardSidebar = () => controller.wire();
    root.toggleAgentDashboardSidebar = event => {
        event?.preventDefault?.();
        event?.stopPropagation?.();
        return controller.toggle();
    };
    root.applyAgentDashboardSidebar = (open, options) => controller.apply(open, options);
    root.agentDashboardSidebarOpen = () => controller.isOpen();
    root.refreshAgentDashboardSidebar = () => controller.refresh();
}(typeof window !== 'undefined' ? window : null, function () {
    const SHELL_ID = 'agentSidebar';
    const BODY_ID = 'agentSidebarBody';
    const TOTALS_ID = 'agentSidebarTotals';
    const NOTICE_ID = 'agentSidebarNotice';
    const REFRESH_BTN_ID = 'agentSidebarRefreshBtn';
    const CLOSE_BTN_ID = 'agentSidebarCloseBtn';
    const TOGGLE_BTN_ID = 'agentSidebarToggleBtn';
    const TOGGLE_ICON_ID = 'agentSidebarToggleIcon';
    const OPEN_BODY_CLASS = 'agent-sidebar-open';

    /* Slower than the dialog's two seconds, deliberately. The dialog is a
       surface the reader opened to look at and will shut again; this one may
       be up for a working day beside eight live panes, and a whole-tree compose
       at the dialog's cadence would be this feature's whole cost. Four seconds
       still turns an agent's dot over inside the time it takes to notice it. */
    const SIDEBAR_REFRESH_MS = 4000;
    const SIDEBAR_TIMEOUT_MS = 10000;

    /* The two supplied marks. One button, so it shows what the press will *do*
       rather than what the panel currently is: shut, it offers to show; open,
       it offers to hide. */
    const SIDEBAR_SHOW_ICON = '/docs/images/show_sidebar.ico';
    const SIDEBAR_HIDE_ICON = '/docs/images/hide_sidebar.ico';

    /* ── The markup ──

       Three nested levels, exactly as the dialog draws them — a workspace is a
       band, a session is a card, an agent is a row — and every class is the
       dialog's own, so `agent-dashboard.css` dresses both and this feature's
       stylesheet states only what a column changes. */

    /* One pane, one line: the dot, the agent's mark, and what the pane
       announced. `dash-agent-who` and not `dash-agent-name` is the whole
       difference from the dialog's row, and it is a deliberate class of its
       own: the name is out of flow here, the way `dash-state-word` is, so a
       stylesheet cannot accidentally draw it back into the line and a reader
       hearing the row still learns which agent it is. */
    function agentRowHtml(pane, render) {
        const esc = render.esc;
        return `
            <button
                type="button"
                class="dash-agent"
                data-agent="${esc(render.glyphKey(pane))}"
                data-dashboard-action="pane"
                data-dashboard-key="pane:${esc(pane?.session_id || '')}"
                data-workspace-id="${esc(pane?.workspace_id || '')}"
                data-group-id="${esc(pane?.group_id || '')}"
                data-session-id="${esc(pane?.session_id || '')}"
                title="${esc(render.hover(pane))}"
            >
                <span class="dash-agent-reading">${render.activity(pane)}</span>
                <span class="dash-agent-icon" aria-hidden="true">${render.glyph(pane)}</span>
                <span class="dash-agent-who">${esc(render.agentName(pane))}</span>
                <span class="dash-agent-line">${esc(render.line(pane))}</span>
                <span class="dash-agent-progress">${render.progress(pane)}</span>
            </button>
        `;
    }

    /* One session tab, as a card in its own tab's colour — the heading is the
       way to the tab, the rows are the way to the panes inside it. No close
       verbs: this panel is a way *to* things, and the × that ends a session
       lives on the tab it names and in the dialog. */
    function sessionHtml(group, render) {
        const esc = render.esc;
        const panes = Array.isArray(group?.panes) ? group.panes : [];
        const declared = Number(group?.agent_count);
        const agents = Number.isFinite(declared) ? declared : panes.length;
        const total = Number(group?.pane_count) || panes.length;
        const others = Math.max(0, total - agents);
        /* Shorter than the dialog's, because the column is: "· 2 other panes"
           is the half of the line the title would otherwise lose. */
        const meta = agents
            ? `${agents} agent${agents === 1 ? '' : 's'}${others ? ` · ${others} other` : ''}`
            : `${total} pane${total === 1 ? '' : 's'}`;
        return `
            <section class="dash-session${agents ? '' : ' is-quiet'}"${render.sessionColourStyle(group)}>
                <header class="dash-session-head">
                    <button
                        type="button"
                        class="dash-session-open"
                        data-dashboard-action="session"
                        data-dashboard-key="session:${esc(group?.group_id || '')}"
                        data-workspace-id="${esc(group?.workspace_id || '')}"
                        data-group-id="${esc(group?.group_id || '')}"
                    >
                        <span class="dash-session-name">${esc(group?.name || group?.group_id || '')}</span>
                        <span class="dash-session-meta">${esc(meta)}</span>
                    </button>
                </header>
                <div class="dash-agents">
                    ${agents
                        ? panes.map(pane => agentRowHtml(pane, render)).join('')
                        : '<p class="dash-session-none">No active agents</p>'}
                </div>
            </section>
        `;
    }

    function workspaceHtml(workspace, index, render) {
        const esc = render.esc;
        const groups = Array.isArray(workspace?.groups) ? workspace.groups : [];
        const agents = Number(workspace?.agent_count) || 0;
        const meta = agents ? `${agents} agent${agents === 1 ? '' : 's'}` : 'no agents';
        return `
            <section class="dash-workspace${agents ? '' : ' is-quiet'}">
                <header class="dash-workspace-head">
                    <button
                        type="button"
                        class="dash-workspace-open"
                        data-dashboard-action="workspace"
                        data-dashboard-key="workspace:${esc(workspace?.workspace_id || '')}"
                        data-workspace-id="${esc(workspace?.workspace_id || '')}"
                    >
                        <span class="dash-workspace-name">${esc(render.workspaceLabel(workspace, index))}</span>
                    </button>
                    <span class="dash-workspace-meta">${esc(meta)}</span>
                </header>
                <div class="dash-sessions">
                    ${groups.length
                        ? groups.map(group => sessionHtml(group, render)).join('')
                        : '<p class="dash-session-none">No sessions</p>'}
                </div>
            </section>
        `;
    }

    function bodyHtml(snapshot, render) {
        const workspaces = Array.isArray(snapshot?.workspaces) ? snapshot.workspaces : [];
        if (!workspaces.length) {
            return `
                <div class="dash-empty">
                    <p class="dash-empty-title">Nothing is running.</p>
                </div>
            `;
        }
        return workspaces
            .map((workspace, index) => workspaceHtml(workspace, index, render))
            .join('');
    }

    /* Which mark the one control wears and what it says it will do: "Show"
       while it is shut, "Hide" while it is up. */
    function toggleFace(open) {
        return open
            ? { icon: SIDEBAR_HIDE_ICON, label: 'Hide the agent dashboard', pressed: 'true' }
            : { icon: SIDEBAR_SHOW_ICON, label: 'Show the agent dashboard', pressed: 'false' };
    }

    const policy = {
        SIDEBAR_REFRESH_MS,
        SIDEBAR_TIMEOUT_MS,
        SIDEBAR_SHOW_ICON,
        SIDEBAR_HIDE_ICON,
        OPEN_BODY_CLASS,
        agentRowHtml,
        sessionHtml,
        workspaceHtml,
        bodyHtml,
        toggleFace
    };

    function create(runtime) {
        const {
            getElement,
            setBodyClass = () => {},
            activeElement = () => null,
            onVisibilityChange = () => {},
            documentHidden = () => false,
            setInterval: armTimer,
            clearInterval: disarmTimer,
            setTimeout: delay,
            clearTimeout: cancelDelay,
            createAbortController,
            fetchJson,
            render,
            openTarget = () => false,
            readStored = () => null,
            writeStored = () => {},
            report = () => {},
            onLayoutChanged = () => {},
            logError = () => {}
        } = runtime || {};

        let timer = null;
        let requestId = 0;
        let inFlight = null;
        let painted = '';
        let wired = false;

        function shell() { return getElement(SHELL_ID); }
        function body() { return getElement(BODY_ID); }

        function isOpen() {
            return Boolean(shell()?.classList?.contains('visible'));
        }

        function setNotice(message) {
            const notice = getElement(NOTICE_ID);
            if (!notice) return;
            notice.textContent = message || '';
            notice.hidden = !message;
        }

        /* The control is repainted from the state rather than flipped, so the
           two can never fall out of step — including on the first paint, where
           there was no press to derive it from. */
        function syncToggle(open) {
            const button = getElement(TOGGLE_BTN_ID);
            if (!button) return false;
            const face = toggleFace(open);
            button.setAttribute('aria-pressed', face.pressed);
            button.setAttribute('aria-expanded', face.pressed);
            button.setAttribute('aria-label', face.label);
            button.title = face.label;
            const icon = getElement(TOGGLE_ICON_ID);
            icon?.setAttribute?.('src', face.icon);
            return true;
        }

        /* A render replaces the row the caret was on, so it is found again
           afterwards and the scroller is put back where it was. An identical
           reading is dropped outright: this panel is on screen while the reader
           works, and repainting it every four seconds for no new information
           would drop a text selection they are in the middle of. */
        function paint(html) {
            const panel = body();
            if (!panel || html === painted) return false;
            const focused = activeElement();
            const focusedKey = panel.contains?.(focused)
                ? focused?.dataset?.dashboardKey || ''
                : '';
            const scrollTop = panel.scrollTop;
            panel.innerHTML = html;
            painted = html;
            panel.scrollTop = scrollTop;
            if (focusedKey) {
                panel.querySelector?.(`[data-dashboard-key="${focusedKey}"]`)
                    ?.focus?.({ preventScroll: true });
            }
            return true;
        }

        async function refresh() {
            if (!isOpen()) return false;
            const id = ++requestId;
            inFlight?.abort();
            const request = createAbortController();
            inFlight = request;
            const timeout = delay(() => request.abort(), SIDEBAR_TIMEOUT_MS);
            let snapshot = null;
            let failure = '';
            try {
                snapshot = await fetchJson('/api/dashboard', {
                    signal: request.signal,
                    cache: 'no-store'
                });
                if (!snapshot || !Array.isArray(snapshot.workspaces)
                    || !snapshot.workspaces.every(workspace => Array.isArray(workspace?.groups)
                        && workspace.groups.every(group => Array.isArray(group?.panes)))) {
                    throw new Error('Invalid dashboard response');
                }
            } catch (error) {
                snapshot = null;
                if (id === requestId) {
                    logError('[GridVibe Dashboard] sidebar read failed:', error);
                    failure = painted
                        ? 'Could not refresh. Showing the last reading.'
                        : 'Could not read what is running.';
                }
            } finally {
                cancelDelay(timeout);
                if (inFlight === request) inFlight = null;
            }
            if (id !== requestId) return false;
            setNotice(failure);
            /* The last good tree stays behind the notice: a reading from four
               seconds ago beats a blank column, as long as it says it is old. */
            if (!snapshot) return false;
            const totals = getElement(TOTALS_ID);
            if (totals) totals.textContent = render.totals(snapshot);
            paint(bodyHtml(snapshot, render));
            return true;
        }

        /* Restarted rather than left running. Two conditions, unlike the
           dialog's one: this panel does not dismiss itself when the reader
           leaves the window, so "open" does not imply "being looked at" and the
           hidden document has to be asked about separately. */
        function schedule() {
            if (timer !== null) {
                disarmTimer(timer);
                timer = null;
            }
            if (!isOpen() || documentHidden()) {
                ++requestId;
                inFlight?.abort();
                inFlight = null;
                return false;
            }
            timer = armTimer(() => {
                if (!inFlight) refresh();
            }, SIDEBAR_REFRESH_MS);
            return true;
        }

        /* The one place the panel's state is written. `persist` is the local
           cache a reload reads back and `report` is the durable transaction;
           they are separate flags because the boot path and the server's own
           read both apply a value they have just been told and must not send
           it straight back. */
        function apply(open, { persist = false, report: shouldReport = false } = {}) {
            const shouldShow = Boolean(open);
            const changed = shouldShow !== isOpen();
            shell()?.classList?.toggle('visible', shouldShow);
            shell()?.setAttribute?.('aria-hidden', shouldShow ? 'false' : 'true');
            setBodyClass(OPEN_BODY_CLASS, shouldShow);
            syncToggle(shouldShow);
            if (persist) writeStored(shouldShow);
            if (shouldReport) report();
            schedule();
            if (shouldShow) refresh();
            /* Only a real change resizes anything: every attached terminal
               refits, which is the one expensive thing this control does. */
            if (changed) onLayoutChanged();
            return shouldShow;
        }

        function toggle() {
            return apply(!isOpen(), { persist: true, report: true });
        }

        /* A row that could not be reached says so *here*. The routing is the
           dialog's, and so is the wording it reports into its own notice line
           — which is a line nobody can see while this panel is the surface
           that was pressed. */
        async function handleRow(dataset) {
            if (!dataset || !dataset.workspaceId) return false;
            let landed = false;
            try {
                landed = Boolean(await openTarget({
                    workspaceId: dataset.workspaceId,
                    groupId: dataset.groupId || '',
                    sessionId: dataset.sessionId || ''
                }));
            } catch (error) {
                logError('[GridVibe Dashboard] sidebar row failed:', error);
            }
            setNotice(landed ? '' : 'Could not open that workspace.');
            return landed;
        }

        function wire() {
            const panel = body();
            if (wired || !shell() || !panel) return false;
            wired = true;
            /* Delegated: every row is rebuilt whenever the reading changes, so
               a listener on a row would not outlive the reading that drew it. */
            panel.addEventListener('click', event => {
                const row = event.target?.closest?.('[data-dashboard-action]');
                if (!row) return;
                event.preventDefault();
                handleRow(row.dataset);
            });
            getElement(REFRESH_BTN_ID)?.addEventListener('click', () => refresh());
            getElement(CLOSE_BTN_ID)?.addEventListener('click', () => {
                apply(false, { persist: true, report: true });
            });
            /* The panel is chrome and survives the reader leaving, so this is
               the poll standing down rather than the surface going away. */
            onVisibilityChange(() => {
                schedule();
                if (!documentHidden()) refresh();
            });
            /* The cache is what a reload paints from; the server's own value
               arrives with the session-group read and overrides it. */
            const stored = readStored();
            apply(stored === null ? false : stored);
            return true;
        }

        return { wire, apply, toggle, isOpen, refresh, schedule, handleRow, setNotice, syncToggle };
    }

    return { policy, create };
}));
