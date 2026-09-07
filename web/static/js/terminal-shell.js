    /* ─────────────────────────────────────────────
       Pane relaunch picker — the header reset control.

       A terminal pane can be relaunched along two independent dimensions, and
       the header's reset button is the dropdown for both:

         · its shell family (cmd / PowerShell / a WSL distro), which only a
           Local Repo pane on a Windows host has to pick from, and
         · the agent CLI its shell starts (claude, codex, opencode, …), which
           an SSH pane has exactly as much as a local one.

       So each shell row carries a right-hand chevron opening that family's
       agent list, and pressing the shell row itself relaunches it plainly —
       the behaviour that row has always had. A pane with no shell family to
       pick (an SSH pane, or a local pane on a POSIX host) gets the agent list
       flat, under its own heading, with "Plain shell" as its first row.

       Both go out as POST /api/sessions/<id>/shell, whose payload states each
       dimension separately: a row that names no shell leaves the pane's shell
       alone, and every row states its agent so that choosing "Plain shell" is
       a choice and not a silence.

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

    function paneShellMenuItemHtml({ label, hint = '', active = false, attrs = '', classes = '' }) {
        return `
            <button
                type="button"
                role="menuitemradio"
                class="pane-shell-menu-item${active ? ' is-active' : ''}${classes ? ` ${classes}` : ''}"
                aria-checked="${active ? 'true' : 'false'}"
                ${attrs}
            >
                <span class="pane-shell-menu-mark">${active ? UI_CHECK_ICON : ''}</span>
                <span class="pane-shell-menu-label">${escHtml(label)}</span>
                ${hint ? `<span class="pane-shell-menu-hint">${escHtml(hint)}</span>` : ''}
            </button>
        `;
    }

    /* One row per relaunch target, so nothing on this side can send a shell
       family without saying what the pane should start under it. */
    function paneShellLaunchAttrs(shellKind, distribution, agentKey) {
        return (
            `data-pane-shell-launch="1"`
            + ` data-pane-shell-kind="${escHtml(shellKind)}"`
            + ` data-pane-shell-distro="${escHtml(distribution)}"`
            + ` data-pane-shell-agent="${escHtml(agentKey)}"`
        );
    }

    function paneShellRowKey(shellKind, distribution) {
        return `${shellKind} ${distribution}`;
    }

    /* The agent radio group for one shell family: "Plain shell" first, then the
       registry's agents. Checked only when that family is the live one, so an
       agent row never claims a pane it is not running in. */
    function paneShellAgentItemsHtml(shellKind, distribution, activeAgent, familyIsActive) {
        const rows = [
            paneShellMenuItemHtml({
                label: 'Plain shell',
                active: familyIsActive && !activeAgent,
                attrs: paneShellLaunchAttrs(shellKind, distribution, '')
            })
        ];
        paneAgentMenuOptions().forEach(option => {
            rows.push(paneShellMenuItemHtml({
                label: option.display_name || option.label || option.value,
                hint: option.value,
                active: familyIsActive && activeAgent === option.value,
                attrs: paneShellLaunchAttrs(shellKind, distribution, option.value)
            }));
        });
        return rows.join('');
    }

    /* A shell family row plus its right-hand chevron, and — while that chevron
       is open — the family's agent list indented under it. */
    function paneShellFamilyRowHtml({ index, label, hint, shellKind, distribution, activeAgent, familyIsActive }) {
        const rowKey = paneShellRowKey(shellKind, distribution);
        const expanded = _expandedShellAgentRows.get(index) === rowKey;
        const agentsLabel = `Agents for ${label}`;
        return `
            <div class="pane-shell-menu-row">
                ${paneShellMenuItemHtml({
                    label,
                    hint,
                    active: familyIsActive,
                    attrs: paneShellLaunchAttrs(shellKind, distribution, '')
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
                ${paneShellAgentItemsHtml(shellKind, distribution, activeAgent, familyIsActive)}
            </div>` : ''}
        `;
    }

    function paneShellMenuWslItemsHtml(index, activeKind, activeDistribution, activeAgent) {
        const rows = [
            paneShellFamilyRowHtml({
                index,
                label: 'WSL',
                hint: 'default distro',
                shellKind: 'wsl',
                distribution: '',
                activeAgent,
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
                familyIsActive: activeKind === option.kind
            })).join('');
            sections = `
                <div class="pane-shell-menu-title">Shell</div>
                ${shellRows}
                ${paneShellMenuWslItemsHtml(index, activeKind, activeDistribution, activeAgent)}
            `;
        } else if (paneSupportsAgentSwitch(session)) {
            /* No shell family to hang the chevrons on, so the agent radio group
               is the whole section — and the pane it relaunches is the one it
               already runs, which is what an unstated shell means. */
            sections = `
                <div class="pane-shell-menu-title">Agent</div>
                ${paneShellAgentItemsHtml('', '', activeAgent, true)}
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
                return;
            }
            if (item.dataset.paneShellLaunch) {
                relaunchSessionShell(index, {
                    shell: item.dataset.paneShellKind || '',
                    distribution: item.dataset.paneShellDistro || '',
                    agent: item.dataset.paneShellAgent || ''
                });
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

    /* True when the row the user pressed is already what the pane runs, so a
       re-selection costs no request and never kills a live shell. The server
       decides the same thing again; this only spares the round trip. */
    function paneRelaunchIsNoop(session, { shell, distribution, agent }) {
        if (shell) {
            if (paneShellKind(session) !== shell) {
                return false;
            }
            if (shell === 'wsl' && String(session?.distribution || '').trim() !== distribution) {
                return false;
            }
        }
        return paneAgentKey(session) === agent;
    }

    /* Relaunch one pane under the shell family and/or agent a menu row named.
       The pane keeps its slot, its stored title and its group, so only the
       process behind it is replaced. An empty `shell` states nothing about the
       shell family — an SSH pane has none to state — while `agent` is always
       stated.

       What the header *prints* is not the stored title, though: an agent pane
       whose title is still the launcher's `Terminal N` placeholder is named
       after its agent, so relaunching a pane onto a different agent (or back
       to a plain shell) changes what that header should say. The stored title
       is untouched either way — a name the user typed keeps winning, and the
       agent's name is still never persisted back. */
    async function relaunchSessionShell(index, { shell = '', distribution = '', agent = '' } = {}) {
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
        if (paneRelaunchIsNoop(session, { shell, distribution, agent })) {
            closeAllPaneShellMenus();
            return;
        }

        const body = { agent };
        if (shell) {
            body.shell = shell;
            body.distribution = distribution;
        }

        _pendingShellSwitchPanes.add(pane);
        renderPaneShellMenu(index);
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
            const ownerIndex = terminals.indexOf(pane);
            if (ownerIndex < 0 || sessionIds[ownerIndex] !== sessionId) return;
            index = ownerIndex;
            closeAllPaneShellMenus();
            syncPaneIdentityChrome(index, data);
            /* The backend cleared the old shell's replay buffer; drop its output
               here too so the fresh shell starts on a clean screen. */
            pane?.term?.reset?.();
            showPlaceholderConnecting(index);
        } catch (error) {
            console.error('[GridVibe Sessions] relaunchSessionShell failed:', error);
            showTerminalToast(error.message || 'Relaunch failed', 'error');
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
