    /* ─────────────────────────────────────────────
       Pane relaunch picker — the header reset control.

       A terminal pane can be relaunched along three independent dimensions,
       and the header's reset button is the dropdown for all of them:

         · its shell family (cmd / PowerShell / a WSL distro), which only a
           Local Repo pane on a Windows host has to pick from,
         · the agent CLI its shell starts (claude, codex, opencode, …), which
           an SSH pane has exactly as much as a local one, and
         · whether that agent is handed GridVibe's own tools (MCP), which an
           SSH pane also has — its tools arrive over a reverse forward on the
           transport its shell is already running on, so the choice belongs to
           a remote pane as much as to a local one.

       So each shell row carries a right-hand chevron opening that family's
       agent list, and pressing the shell row itself relaunches it plainly —
       the behaviour that row has always had. A pane with no shell family to
       pick (an SSH pane, or a local pane on a POSIX host) gets the agent list
       flat, under its own heading, with "Plain shell" as its first row.

       The third dimension uses that same shape one level down: an agent whose
       CLI can be handed the sidecar carries an "MCP" button beside its row,
       and pressing it starts that agent with GridVibe tools while the row
       itself starts it plainly. Two one-press actions rather than a modifier
       the reader has to set first — and an inline control rather than a
       flyout, which is what keeps the menu inside the window on a pane docked
       against its right edge.

       Every agent whose registry entry publishes an update command also
       carries an update icon beside its row: the same relaunch, with the
       agent's own update command run first, so one press updates it and then
       starts it.

       All of them go out as POST /api/sessions/<id>/shell, whose payload
       states each dimension separately: a row that names no shell leaves the
       pane's shell alone, and every row states its agent *and* its MCP choice
       so that "Plain shell", and plain-agent rows, are choices rather than
       silences — which is also the only way back off GridVibe tools.

       Explorer and browser panes have no shell at all, so their reset button
       stays a plain one-click reset. Loaded before terminals.js so
       buildPaneCard/wirePaneControls can call into it.
    ───────────────────────────────────────────── */

    /* Shell families offered on Windows hosts. WSL entries are appended from
       the detected distro list (plus one "default distro" entry). */
    const LOCAL_SHELL_MODE_OPTIONS = [
        { kind: 'cmd', label: 'Command Prompt', hint: 'cmd.exe' },
        { kind: 'powershell', label: 'PowerShell', hint: 'powershell.exe' }
    ];

    /* Pane modes that run a shell of their own, and so can be relaunched. */
    const PANE_MODES_WITH_A_SHELL = ['ssh', 'wsl'];

    /* 'idle' → 'loading' → 'ready' | 'error'; distros are fetched once per
       window, the first time a shell menu opens, and cached here. */
    let _wslDistroState = 'idle';
    let _wslDistroNames = [];
    const _pendingShellSwitchPanes = new Set();
    /* index → the shell row whose agent list is open, at most one per menu.
       Dropped when the menu closes: an expansion is a pointer gesture inside
       one opening of the menu, not pane state. */
    const _expandedShellAgentRows = new Map();

    function localShellModesAvailable() {
        return typeof LOCAL_SHELL_MODES_AVAILABLE !== 'undefined' && Boolean(LOCAL_SHELL_MODES_AVAILABLE);
    }

    /* The registry-backed agent list the launcher renders, minus its free-text
       "other" entry: composing a custom command needs an input field, and this
       menu has none. */
    function paneAgentMenuOptions() {
        if (typeof AGENT_OPTIONS === 'undefined' || !Array.isArray(AGENT_OPTIONS)) {
            return [];
        }
        return AGENT_OPTIONS.filter(option => option && option.value && option.value !== 'other');
    }

    /* Mirrors the backend's _local_shell_kind precedence (WSL beats PowerShell). */
    function paneShellKind(session) {
        if (session?.use_wsl) {
            return 'wsl';
        }
        return session?.use_powershell ? 'powershell' : 'cmd';
    }

    /* The agent a pane runs now, or '' for a plain shell. A startup command is
       not an agent: only a promoted or launched agent pane names one. */
    function paneAgentKey(session) {
        if (session?.startup_mode !== 'agent') {
            return '';
        }
        return String(session?.agent_selection || session?.custom_agent || '').trim().toLowerCase();
    }

    /* Whether this CLI can be handed the sidecar at launch at all — the same
       question the launcher's MCP checkbox asks, off the same registry field.
       Deliberately not `mcp_flag` truthiness: Codex supports MCP and publishes
       no flag string, because its servers ride in as `-c` overrides the server
       composes per shell. The CLIs that publish neither can only register a
       server by editing the user's own config, which a per-pane press has no
       business doing, so they get no button. */
    function paneAgentSupportsMcp(option) {
        return Boolean(option?.mcp_supported);
    }

    /* Whether the pane's agent is running with GridVibe tools. Meaningless
       without an agent, so a plain shell reports false however the flag was
       left by a preset written before the pane was sent back to one. */
    function paneAgentMcp(session) {
        return Boolean(paneAgentKey(session) && session?.agent_mcp);
    }

    function paneIsRelaunchable(session) {
        return Boolean(
            session
            && PANE_MODES_WITH_A_SHELL.includes(session.mode)
            && !isExplorerSession(session)
            && !isBrowserSession(session)
        );
    }

    function paneSupportsShellSwitch(session) {
        return Boolean(
            localShellModesAvailable()
            && paneIsRelaunchable(session)
            && session.mode === 'wsl'
        );
    }

    function paneSupportsAgentSwitch(session) {
        return Boolean(paneIsRelaunchable(session) && paneAgentMenuOptions().length);
    }

    /* One predicate behind the button's affordance and its menu: a pane with
       neither dimension to offer keeps the plain one-click reset. */
    function paneHasResetMenu(session) {
        return paneSupportsShellSwitch(session) || paneSupportsAgentSwitch(session);
    }

    function paneShellResetTitle(session) {
        if (isBrowserSession(session)) {
            return 'Reload browser pane';
        }
        if (paneSupportsShellSwitch(session)) {
            return 'Reset this terminal view, or relaunch its shell or agent';
        }
        if (paneSupportsAgentSwitch(session)) {
            return 'Reset this terminal view, or relaunch it with an agent';
        }
        return 'Reset this terminal view and replay recent output';
    }

    /* The reset control itself stays a direct child of .terminal-actions so the
       header fold and the buttons inserted after it keep working unchanged. */
    function paneResetButtonHtml(index, session) {
        const switchable = paneHasResetMenu(session);
        const title = paneShellResetTitle(session);
        return `
            <button
                type="button"
                class="terminal-action-btn${switchable ? ' has-shell-menu' : ''}"
                id="trefresh-${index}"
                data-terminal-refresh="${index}"
                title="${escHtml(title)}"
                aria-label="${escHtml(title)}"
                ${switchable ? 'aria-haspopup="menu" aria-expanded="false"' : ''}
            >
                ${TERMINAL_REFRESH_ICON}
            </button>
        `;
    }

    /* Rendered empty for every pane and filled on open, so a pane that later
       becomes (or stops being) a local terminal needs no DOM surgery. */
    function paneShellMenuHtml(index) {
        return `
            <div
                class="pane-shell-menu"
                id="tshellmenu-${index}"
                data-pane-shell-menu="${index}"
                role="menu"
                aria-label="Terminal reset, shell and agent"
                hidden
            ></div>
        `;
    }

    function paneShellMenuElement(index) {
        return document.querySelector(`[data-pane-shell-menu="${index}"]`);
    }

    /* An agent row wears the same mark and brand colour the pane header and the
       dashboard row paint that agent in, so the menu names an agent the way the
       rest of the page already does. `data-agent` sits on the row rather than
       on the label, which is what agent-brand.css keys the colour off. */
    function paneShellAgentIconHtml(agentKey) {
        const glyphs = typeof window !== 'undefined' ? window.GridVibeAgentGlyphs : undefined;
        return glyphs ? glyphs.agentGlyphMarkup(agentKey) : '';
    }

    function paneShellAgentBrandKey(agentKey) {
        const glyphs = typeof window !== 'undefined' ? window.GridVibeAgentGlyphs : undefined;
        return glyphs ? glyphs.agentGlyphKey(agentKey) : '';
    }

    function paneShellMenuItemHtml({ label, hint = '', active = false, attrs = '', classes = '', icon = '', agent = '' }) {
        return `
            <button
                type="button"
                role="menuitemradio"
                class="pane-shell-menu-item${active ? ' is-active' : ''}${classes ? ` ${classes}` : ''}"
                aria-checked="${active ? 'true' : 'false'}"
                ${agent ? `data-agent="${escHtml(agent)}"` : ''}
                ${attrs}
            >
                <span class="pane-shell-menu-mark">${active ? UI_CHECK_ICON : ''}</span>
                ${icon ? `<span class="pane-shell-menu-icon">${icon}</span>` : ''}
                <span class="pane-shell-menu-label">${escHtml(label)}</span>
                ${hint ? `<span class="pane-shell-menu-hint">${escHtml(hint)}</span>` : ''}
            </button>
        `;
    }

    /* One row per relaunch target, so nothing on this side can send a shell
       family without saying what the pane should start under it — or start an
       agent without saying whether it gets GridVibe tools. */
    function paneShellLaunchAttrs(shellKind, distribution, agentKey, mcp) {
        return (
            `data-pane-shell-launch="1"`
            + ` data-pane-shell-kind="${escHtml(shellKind)}"`
            + ` data-pane-shell-distro="${escHtml(distribution)}"`
            + ` data-pane-shell-agent="${escHtml(agentKey)}"`
            + ` data-pane-shell-mcp="${mcp ? '1' : '0'}"`
        );
    }

    /* The update button an agent row carries in place of its old command-name
       hint: the same relaunch as the row, with the registry's update command
       run first — update, then start, in one press. It keeps GridVibe tools
       when it is updating the agent the pane already runs with them, so
       updating is never also a way off the tools. An agent that publishes no
       update command gets no button. */
    function paneShellAgentUpdateHtml(option, label, shellKind, distribution, keepMcp) {
        const command = String(option?.update_command || '').trim();
        if (!command) {
            return '';
        }
        const title = `Update ${label} (${command}), then start it`;
        return `
            <button
                type="button"
                role="menuitem"
                class="pane-shell-menu-update"
                title="${escHtml(title)}"
                aria-label="${escHtml(title)}"
                ${paneShellLaunchAttrs(shellKind, distribution, option.value, keepMcp)}
                data-pane-shell-update="1"
            >${AGENT_UPDATE_ICON}</button>
        `;
    }

    function paneShellRowKey(shellKind, distribution) {
        return `${shellKind} ${distribution}`;
    }

    /* The agent radio group for one shell family: "Plain shell" first, then the
       registry's agents. Checked only when that family is the live one, so an
       agent row never claims a pane it is not running in.

       An agent whose CLI can take the sidecar is a pair rather than a row: the
       row starts it plainly and the "MCP" button beside it starts it with
       GridVibe tools, and exactly one of the two wears the check — so the pair
       reports which of the two the pane is actually running, and either press
       is the way off the other. Agents with no published MCP mechanism get the
       bare row, the same way a pane with no shell family gets no chevron. */
    function paneShellAgentItemsHtml(shellKind, distribution, activeAgent, familyIsActive, activeMcp) {
        const rows = [
            paneShellMenuItemHtml({
                label: 'Plain shell',
                active: familyIsActive && !activeAgent,
                icon: TERMINAL_PROMPT_ICON,
                attrs: paneShellLaunchAttrs(shellKind, distribution, '', false)
            })
        ];
        paneAgentMenuOptions().forEach(option => {
            const label = option.display_name || option.label || option.value;
            const isLive = familyIsActive && activeAgent === option.value;
            const plainRow = paneShellMenuItemHtml({
                label,
                active: isLive && !activeMcp,
                icon: paneShellAgentIconHtml(option.value),
                agent: paneShellAgentBrandKey(option.value),
                attrs: paneShellLaunchAttrs(shellKind, distribution, option.value, false)
            });
            const updateButton = paneShellAgentUpdateHtml(
                option, label, shellKind, distribution, isLive && activeMcp
            );
            if (!paneAgentSupportsMcp(option)) {
                rows.push(updateButton
                    ? `<div class="pane-shell-menu-row">${plainRow}${updateButton}</div>`
                    : plainRow);
                return;
            }
            const toolsLabel = `${label} with GridVibe tools`;
            rows.push(`
                <div class="pane-shell-menu-row">
                    ${plainRow}
                    ${updateButton}
                    <button
                        type="button"
                        role="menuitemradio"
                        class="pane-shell-menu-mcp${isLive && activeMcp ? ' is-active' : ''}"
                        aria-checked="${isLive && activeMcp ? 'true' : 'false'}"
                        title="${escHtml(toolsLabel)}"
                        aria-label="${escHtml(toolsLabel)}"
                        ${paneShellLaunchAttrs(shellKind, distribution, option.value, true)}
                    >MCP</button>
                </div>
            `);
        });
        return rows.join('');
    }

    /* A shell family row plus its right-hand chevron, and — while that chevron
       is open — the family's agent list indented under it. */
    function paneShellFamilyRowHtml({ index, label, hint, shellKind, distribution, activeAgent, activeMcp, familyIsActive }) {
        const rowKey = paneShellRowKey(shellKind, distribution);
        const expanded = _expandedShellAgentRows.get(index) === rowKey;
        const agentsLabel = `Agents for ${label}`;
        return `
            <div class="pane-shell-menu-row">
                ${paneShellMenuItemHtml({
                    label,
                    hint,
                    active: familyIsActive,
                    attrs: paneShellLaunchAttrs(shellKind, distribution, '', false)
                })}
                <button
                    type="button"
                    class="pane-shell-menu-expand${expanded ? ' is-expanded' : ''}"
                    data-pane-shell-expand="${escHtml(rowKey)}"
                    aria-expanded="${expanded ? 'true' : 'false'}"
                    title="${escHtml(agentsLabel)}"
                    aria-label="${escHtml(agentsLabel)}"
                >${UI_CHEVRON_RIGHT_ICON}</button>
            </div>
            ${expanded ? `
            <div class="pane-shell-menu-sub" role="group" aria-label="${escHtml(agentsLabel)}">
                ${paneShellAgentItemsHtml(shellKind, distribution, activeAgent, familyIsActive, activeMcp)}
            </div>` : ''}
        `;
    }

    function paneShellMenuWslItemsHtml(index, activeKind, activeDistribution, activeAgent, activeMcp) {
        const rows = [
            paneShellFamilyRowHtml({
                index,
                label: 'WSL',
                hint: 'default distro',
                shellKind: 'wsl',
                distribution: '',
                activeAgent,
                activeMcp,
                familyIsActive: activeKind === 'wsl' && !activeDistribution
            })
        ];

        _wslDistroNames.forEach(name => {
            rows.push(paneShellFamilyRowHtml({
                index,
                label: `WSL · ${name}`,
                hint: '',
                shellKind: 'wsl',
                distribution: name,
                activeAgent,
                activeMcp,
                familyIsActive: activeKind === 'wsl' && activeDistribution === name
            }));
        });

        if (_wslDistroState === 'loading') {
            rows.push('<div class="pane-shell-menu-note">Detecting WSL distros…</div>');
        } else if (_wslDistroState === 'error') {
            rows.push(`
                <button type="button" class="pane-shell-menu-item pane-shell-menu-retry" data-pane-shell-distro-retry="1">
                    <span class="pane-shell-menu-mark"></span>
                    <span class="pane-shell-menu-label">Retry distro detection</span>
                </button>
            `);
        }

        return rows.join('');
    }

    function renderPaneShellMenu(index) {
        const menu = paneShellMenuElement(index);
        if (!menu) {
            return;
        }

        const session = terminals[index]?._session;
        const activeKind = paneShellKind(session);
        const activeDistribution = String(session?.distribution || '').trim();
        const activeAgent = paneAgentKey(session);
        const activeMcp = paneAgentMcp(session);
        const busy = _pendingShellSwitchPanes.has(terminals[index]);

        let sections = '';
        if (paneSupportsShellSwitch(session)) {
            const shellRows = LOCAL_SHELL_MODE_OPTIONS.map(option => paneShellFamilyRowHtml({
                index,
                label: option.label,
                hint: option.hint,
                shellKind: option.kind,
                distribution: '',
                activeAgent,
                activeMcp,
                familyIsActive: activeKind === option.kind
            })).join('');
            sections = `
                <div class="pane-shell-menu-title">Shell</div>
                ${shellRows}
                ${paneShellMenuWslItemsHtml(index, activeKind, activeDistribution, activeAgent, activeMcp)}
            `;
        } else if (paneSupportsAgentSwitch(session)) {
            /* No shell family to hang the chevrons on, so the agent radio group
               is the whole section — and the pane it relaunches is the one it
               already runs, which is what an unstated shell means. An SSH pane
               lands here, and its agents keep their MCP buttons: a remote
               pane's tools ride its own transport home. */
            sections = `
                <div class="pane-shell-menu-title">Agent</div>
                ${paneShellAgentItemsHtml('', '', activeAgent, true, activeMcp)}
            `;
        }

        menu.classList.toggle('is-busy', busy);
        menu.innerHTML = `
            <button type="button" role="menuitem" class="pane-shell-menu-item" data-pane-shell-reset="1">
                <span class="pane-shell-menu-mark">${TERMINAL_REFRESH_ICON}</span>
                <span class="pane-shell-menu-label">Reset view</span>
            </button>
            ${sections ? `<div class="pane-shell-menu-sep" role="separator"></div>${sections}` : ''}
        `;
    }

    function closeAllPaneShellMenus(exceptIndex = -1) {
        document.querySelectorAll('.pane-shell-menu:not([hidden])').forEach(menu => {
            const index = Number(menu.dataset.paneShellMenu);
            if (index === exceptIndex) {
                return;
            }
            menu.hidden = true;
            _expandedShellAgentRows.delete(index);
            document.getElementById(`trefresh-${index}`)?.setAttribute('aria-expanded', 'false');
        });
    }

    function togglePaneShellMenu(index) {
        const menu = paneShellMenuElement(index);
        if (!menu) {
            return;
        }
        const willOpen = menu.hidden;
        closeAllPaneShellMenus(index);
        if (willOpen) {
            _expandedShellAgentRows.delete(index);
            renderPaneShellMenu(index);
            ensureWslDistrosLoaded();
        }
        menu.hidden = !willOpen;
        document.getElementById(`trefresh-${index}`)?.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    }

    /* One entry point for the header reset button: a dropdown on panes with a
       shell or an agent to relaunch, the plain reset everywhere else. */
    function handlePaneResetButton(index) {
        if (paneHasResetMenu(terminals[index]?._session)) {
            togglePaneShellMenu(index);
            return;
        }
        closeAllPaneShellMenus();
        refreshTerminalDisplay(index);
    }

    /* Keep the reset button's affordance in step with a pane that changed kind
       (terminal ↔ explorer ↔ browser) without being rebuilt. */
    function syncPaneShellControls(index, session) {
        const button = document.getElementById(`trefresh-${index}`);
        closeAllPaneShellMenus();
        if (!button) {
            return;
        }
        const switchable = paneHasResetMenu(session);
        const title = paneShellResetTitle(session);
        button.classList.toggle('has-shell-menu', switchable);
        button.title = title;
        button.setAttribute('aria-label', title);
        if (switchable) {
            button.setAttribute('aria-haspopup', 'menu');
            button.setAttribute('aria-expanded', 'false');
        } else {
            button.removeAttribute('aria-haspopup');
            button.removeAttribute('aria-expanded');
        }
    }

    function wirePaneShellMenu(card, index) {
        const menu = card.querySelector(`[data-pane-shell-menu="${index}"]`);
        if (!menu) {
            return;
        }
        /* Delegated: the menu's rows are re-rendered on every open. */
        menu.addEventListener('mousedown', event => {
            event.preventDefault();
            event.stopPropagation();
        });
        menu.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            if (_pendingShellSwitchPanes.has(terminals[index])) {
                return;
            }
            /* The chevron expands its row's agent list and does nothing else:
               it is a control beside the row, never a second meaning for it. */
            const expander = event.target.closest('[data-pane-shell-expand]');
            if (expander) {
                const rowKey = expander.dataset.paneShellExpand;
                if (_expandedShellAgentRows.get(index) === rowKey) {
                    _expandedShellAgentRows.delete(index);
                } else {
                    _expandedShellAgentRows.set(index, rowKey);
                }
                renderPaneShellMenu(index);
                return;
            }
            /* Every relaunch target states its whole payload, so one lookup
               covers the rows and the MCP buttons seated beside them alike —
               the button is a second target on the row, never a second class
               of row. */
            const launch = event.target.closest('[data-pane-shell-launch]');
            if (launch) {
                relaunchSessionShell(index, {
                    shell: launch.dataset.paneShellKind || '',
                    distribution: launch.dataset.paneShellDistro || '',
                    agent: launch.dataset.paneShellAgent || '',
                    mcp: launch.dataset.paneShellMcp === '1',
                    update: launch.dataset.paneShellUpdate === '1'
                });
                return;
            }
            const item = event.target.closest('.pane-shell-menu-item');
            if (!item) {
                return;
            }
            if (item.dataset.paneShellReset) {
                closeAllPaneShellMenus();
                refreshTerminalDisplay(index);
                return;
            }
            if (item.dataset.paneShellDistroRetry) {
                _wslDistroState = 'idle';
                ensureWslDistrosLoaded();
                renderPaneShellMenu(index);
            }
        });
    }

    async function ensureWslDistrosLoaded() {
        if (_wslDistroState === 'loading' || _wslDistroState === 'ready') {
            return;
        }
        _wslDistroState = 'loading';
        try {
            const response = await fetch('/api/wsl-distros');
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `WSL distro lookup failed with status ${response.status}`);
            }
            _wslDistroNames = (Array.isArray(data.distros) ? data.distros : [])
                .map(distro => String(distro?.name || '').trim())
                .filter(Boolean);
            _wslDistroState = data.available === false && !_wslDistroNames.length ? 'error' : 'ready';
        } catch (error) {
            console.error('[GridVibe Sessions] WSL distro lookup failed:', error);
            _wslDistroNames = [];
            _wslDistroState = 'error';
        }
        /* Fill in whichever menu is still open now that the list resolved. */
        document.querySelectorAll('.pane-shell-menu:not([hidden])').forEach(menu => {
            renderPaneShellMenu(Number(menu.dataset.paneShellMenu));
        });
    }

    /* Relaunch one pane under the shell family, agent and tools a menu row
       named. The pane keeps its slot, its stored title and its group, so only
       the process behind it is replaced. An empty `shell` states nothing about
       the shell family — an SSH pane has none to state — while `agent` and
       `mcp` are always stated, so a plain row is the way off GridVibe tools
       rather than a silence the server reads as "leave it alone". Pressing an
       already-selected row still relaunches the pane: the check mark describes
       what will start, not a disabled state selector.

       What the header *prints* is not the stored title, though: an agent pane
       whose title is still the launcher's `Terminal N` placeholder is named
       after its agent, so relaunching a pane onto a different agent (or back
       to a plain shell) changes what that header should say. The stored title
       is untouched either way — a name the user typed keeps winning, and the
       agent's name is still never persisted back. */
    async function relaunchSessionShell(index, { shell = '', distribution = '', agent = '', mcp = false, update = false } = {}) {
        const sessionId = sessionIds[index];
        const pane = terminals[index];
        const session = pane?._session;
        if (_pendingShellSwitchPanes.has(pane)) return;
        if (!sessionId || !paneIsRelaunchable(session)) {
            return;
        }
        if (shell && !paneSupportsShellSwitch(session)) {
            return;
        }
        const body = { agent, mcp: Boolean(agent) && Boolean(mcp) };
        /* Only ever sent as true: an update is a one-shot action, and every
           request that leaves it out is simply not asking for one. */
        if (agent && update) {
            body.update = true;
        }
        if (shell) {
            body.shell = shell;
            body.distribution = distribution;
        }

        _pendingShellSwitchPanes.add(pane);
        renderPaneShellMenu(index);
        /* The spinner goes up before the request, never after it. The new
           transport is started while the route is still writing its response —
           a local shell is marked connected inside that same request — so a
           connected `session_status` can reach the page first, and that event
           is the one thing that takes the overlay off an attached pane: an
           overlay raised behind it stays up for the life of the window
           (ISSUE-2026-053). Clearing the pane's xterm here is the same race in
           reverse: the backend has already dropped the old shell's replay
           buffer, so a reset awaited first wipes what the *new* shell has
           already drawn. */
        globalThis.GridVibeTerminalReplies?.clearTerminalQueryResidue?.(pane);
        pane?.term?.reset?.();
        showPlaceholderConnecting(index);
        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/shell`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Relaunch failed with status ${response.status}`);
            }

            pane._session = data;

            /* When the successor is a plain shell, the reset above disarmed
               mouse reporting and then the agent
               that was dying while this request was in flight went on emitting
               — a full-screen TUI re-asserts `\x1b[?1003h` on every redraw —
               so bytes that re-arm the mode are written to the pane *after*
               the reset that cleared it. The plain shell that has now
               inherited the prompt would get every pointer movement typed at
               it, which is the pane GridVibeTerminalModes exists to rescue;
               the difference here is that GridVibe caused it, so nobody should
               have to notice and press Reset view.

               Written once more now that the request is answered and the old
               shell is gone. A teardown draws nothing, so unlike a second
               `term.reset()` it cannot wipe what the new shell has already
               drawn — which is the race the pre-fetch reset above is placed
               where it is to avoid. Owed to the pane, never to the slot: the
               capture flushes that pane's own queue first and follows it even
               if the grid has since handed its slot to somebody else. A new
               agent owns its mouse mode and must not have it torn down after
               its connector has already started. */
            if (!agent) {
                const resetTarget = GridVibeTerminalModes.captureResetTarget({
                    pane,
                    sessionId,
                    flush: target => flushCapturedPendingOutput(target),
                    write: (target, payload) => {
                        if (target?.term) {
                            target.term.write(payload);
                        }
                    }
                });
                GridVibeTerminalModes.resetMouseReporting(payload => resetTarget.write(payload));
            }

            const ownerIndex = terminals.indexOf(pane);
            if (ownerIndex < 0 || sessionIds[ownerIndex] !== sessionId) return;
            index = ownerIndex;
            closeAllPaneShellMenus();
            syncPaneIdentityChrome(index, data);
        } catch (error) {
            console.error('[GridVibe Sessions] relaunchSessionShell failed:', error);
            showTerminalToast(error.message || 'Relaunch failed', 'error');
            /* Nothing was relaunched: the pane is still running what it was, so
               the spinner comes back off and the pane wears whatever its own
               session record calls for, rather than a "Connecting…" overlay
               over a live shell. */
            const failedIndex = terminals.indexOf(pane);
            if (failedIndex >= 0 && sessionIds[failedIndex] === sessionId) {
                syncPanePlaceholder(failedIndex);
            }
        } finally {
            _pendingShellSwitchPanes.delete(pane);
            const ownerIndex = terminals.indexOf(pane);
            if (ownerIndex >= 0 && sessionIds[ownerIndex] === sessionId
                && !paneShellMenuElement(ownerIndex)?.hidden) {
                index = ownerIndex;
                renderPaneShellMenu(index);
            }
        }
    }
