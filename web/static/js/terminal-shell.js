    /* ─────────────────────────────────────────────
       Pane shell picker — the header reset control.

       Local Repo panes can change their shell family (cmd / PowerShell / a WSL
       distro) without being relaunched, so the header's reset button doubles as
       a dropdown: "Reset view" keeps the old one-item behaviour and the Shell
       section restarts the pane's shell in place through
       POST /api/sessions/<id>/shell.

       SSH, explorer and browser panes have no shell family to pick, so their
       reset button stays a plain one-click reset. Loaded before terminals.js so
       buildPaneCard/wirePaneControls can call into it.
    ───────────────────────────────────────────── */

    /* Shell families offered on Windows hosts. WSL entries are appended from
       the detected distro list (plus one "default distro" entry). */
    const LOCAL_SHELL_MODE_OPTIONS = [
        { kind: 'cmd', label: 'Command Prompt', hint: 'cmd.exe' },
        { kind: 'powershell', label: 'PowerShell', hint: 'powershell.exe' }
    ];

    /* 'idle' → 'loading' → 'ready' | 'error'; distros are fetched once per
       window, the first time a shell menu opens, and cached here. */
    let _wslDistroState = 'idle';
    let _wslDistroNames = [];
    const _pendingShellSwitchIndexes = new Set();

    function localShellModesAvailable() {
        return typeof LOCAL_SHELL_MODES_AVAILABLE !== 'undefined' && Boolean(LOCAL_SHELL_MODES_AVAILABLE);
    }

    /* Mirrors the backend's _local_shell_kind precedence (WSL beats PowerShell). */
    function paneShellKind(session) {
        if (session?.use_wsl) {
            return 'wsl';
        }
        return session?.use_powershell ? 'powershell' : 'cmd';
    }

    function paneSupportsShellSwitch(session) {
        return Boolean(
            localShellModesAvailable()
            && session
            && session.mode === 'wsl'
            && !isExplorerSession(session)
            && !isBrowserSession(session)
        );
    }

    function paneShellResetTitle(session) {
        if (isBrowserSession(session)) {
            return 'Reload browser pane';
        }
        if (paneSupportsShellSwitch(session)) {
            return 'Reset this terminal view or switch its shell';
        }
        return 'Reset this terminal view and replay recent output';
    }

    /* The reset control itself stays a direct child of .terminal-actions so the
       header fold and the buttons inserted after it keep working unchanged. */
    function paneResetButtonHtml(index, session) {
        const switchable = paneSupportsShellSwitch(session);
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
                aria-label="Terminal reset and shell"
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

    function paneShellMenuWslItemsHtml(activeKind, activeDistribution) {
        const rows = [
            paneShellMenuItemHtml({
                label: 'WSL',
                hint: 'default distro',
                active: activeKind === 'wsl' && !activeDistribution,
                attrs: 'data-pane-shell-kind="wsl" data-pane-shell-distro=""'
            })
        ];

        _wslDistroNames.forEach(name => {
            rows.push(paneShellMenuItemHtml({
                label: `WSL · ${name}`,
                active: activeKind === 'wsl' && activeDistribution === name,
                attrs: `data-pane-shell-kind="wsl" data-pane-shell-distro="${escHtml(name)}"`
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
        const busy = _pendingShellSwitchIndexes.has(index);

        const shellRows = LOCAL_SHELL_MODE_OPTIONS.map(option => paneShellMenuItemHtml({
            label: option.label,
            hint: option.hint,
            active: activeKind === option.kind,
            attrs: `data-pane-shell-kind="${option.kind}"`
        })).join('');

        menu.classList.toggle('is-busy', busy);
        menu.innerHTML = `
            <button type="button" role="menuitem" class="pane-shell-menu-item" data-pane-shell-reset="1">
                <span class="pane-shell-menu-mark">${TERMINAL_REFRESH_ICON}</span>
                <span class="pane-shell-menu-label">Reset view</span>
            </button>
            <div class="pane-shell-menu-sep" role="separator"></div>
            <div class="pane-shell-menu-title">Shell</div>
            ${shellRows}
            ${paneShellMenuWslItemsHtml(activeKind, activeDistribution)}
        `;
    }

    function closeAllPaneShellMenus(exceptIndex = -1) {
        document.querySelectorAll('.pane-shell-menu:not([hidden])').forEach(menu => {
            const index = Number(menu.dataset.paneShellMenu);
            if (index === exceptIndex) {
                return;
            }
            menu.hidden = true;
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
            renderPaneShellMenu(index);
            ensureWslDistrosLoaded();
        }
        menu.hidden = !willOpen;
        document.getElementById(`trefresh-${index}`)?.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    }

    /* One entry point for the header reset button: a dropdown on local terminal
       panes, the plain reset everywhere else. */
    function handlePaneResetButton(index) {
        if (paneSupportsShellSwitch(terminals[index]?._session)) {
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
        const switchable = paneSupportsShellSwitch(session);
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
            const item = event.target.closest('.pane-shell-menu-item');
            if (!item || _pendingShellSwitchIndexes.has(index)) {
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
            if (item.dataset.paneShellKind) {
                switchSessionShell(index, item.dataset.paneShellKind, item.dataset.paneShellDistro || '');
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

    /* Restart one local pane under another shell. The pane keeps its slot,
       title and startup command, so only its shell process is replaced. */
    async function switchSessionShell(index, shellKind, distribution = '') {
        const sessionId = sessionIds[index];
        const session = terminals[index]?._session;
        if (!sessionId || !paneSupportsShellSwitch(session)) {
            return;
        }
        if (
            paneShellKind(session) === shellKind
            && (shellKind !== 'wsl' || String(session?.distribution || '').trim() === distribution)
        ) {
            closeAllPaneShellMenus();
            return;
        }

        _pendingShellSwitchIndexes.add(index);
        renderPaneShellMenu(index);
        try {
            const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/shell`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ shell: shellKind, distribution })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || `Shell switch failed with status ${response.status}`);
            }

            closeAllPaneShellMenus();
            const pane = terminals[index];
            if (pane) {
                pane._session = data;
            }
            const hostLabel = document.getElementById(`thost-${index}`);
            if (hostLabel) {
                hostLabel.textContent = data.host || '';
            }
            /* The backend cleared the old shell's replay buffer; drop its output
               here too so the fresh shell starts on a clean screen. */
            pane?.term?.reset?.();
            showPlaceholderConnecting(index);
        } catch (error) {
            console.error('[GridVibe Sessions] switchSessionShell failed:', error);
            showTerminalToast(error.message || 'Shell switch failed', 'error');
        } finally {
            _pendingShellSwitchIndexes.delete(index);
            if (!paneShellMenuElement(index)?.hidden) {
                renderPaneShellMenu(index);
            }
        }
    }
