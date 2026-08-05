    /* ── Theme management ── */

    async function shutdownBrowserApp() {
        if (!BROWSER_SHUTDOWN_TOKEN) {
            return;
        }
        const confirmed = await openGenericConfirmModal({
            title: 'Close GridVibe?',
            copy: 'Close GridVibe and end the browser server?',
            confirmLabel: 'Close GridVibe',
            danger: true
        });
        if (!confirmed) {
            return;
        }

        const button = document.getElementById('browserCloseBtn');
        if (button) {
            button.disabled = true;
            button.textContent = 'Closing...';
        }

        try {
            const response = await fetch('/api/browser-shutdown', {
                method: 'POST',
                headers: {
                    'X-GridVibe-Shutdown-Token': BROWSER_SHUTDOWN_TOKEN
                }
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.error || 'GridVibe could not be closed.');
            }
        } catch (error) {
            if (button) {
                button.disabled = false;
                button.textContent = 'Close';
            }
            showMessage(error.message || 'GridVibe could not be closed.', 'error');
        }
    }





    function updateThemeControls(theme) {
        const preference = normalizeThemePreference(theme);
        const btn = document.getElementById('themeToggleBtnIndex');
        const select = document.getElementById('appTheme');
        if (btn) {
            btn.innerHTML = themeToggleButtonHtml(preference);
        }
        if (select && select.value !== preference) {
            select.value = preference;
        }
    }

    /* Theme helpers live in shared.js; these hooks add the launcher-specific
       behaviour (settings controls + appSettings mirror). */
    function onThemeApplied(preference) {
        updateThemeControls(preference);
    }

    function onThemeCycled(nextTheme) {
        if (appSettings?.appearance) {
            appSettings.appearance.theme = nextTheme;
        }
    }

    function onThemePersisted(data) {
        if (appSettings?.appearance && data?.appearance?.theme) {
            appSettings.appearance.theme = data.appearance.theme;
        }
    }

    /* App Settings dialog hooks (app-settings.js owns the dialog itself). */
    function appSettingsNotify(text, type = '') {
        showMessage(text, type);
    }

    function onAppSettingsApplied(data) {
        installKind = data?.install_kind === 'source' ? 'source' : 'git';
    }

    function onAppSettingsSaved() {
        setUpdateStatus('App settings saved.', 'success');
    }

    initTheme();

    window.name = 'gridvibe-launcher';
    const MAX_SESSIONS = Number(document.querySelector('.shell').dataset.maxSessions || 4);
    const COUNT_OPTIONS = [1, 2, 3, 4, 6, 8].filter(value => value <= MAX_SESSIONS);
    /* What the launcher will actually go up to: the count ladder's top, which
       is 8 unless a lower configured max_sessions trims the ladder. It is
       usually *below* MAX_SESSIONS, because a count needs a layout preset in
       LAYOUT_COPY to be offerable at all. */
    const LAUNCHER_MAX_TERMINALS = COUNT_OPTIONS[COUNT_OPTIONS.length - 1];
    /* Fallback address for a browser pane that has no saved URL of its own —
       mirrors BROWSER_DEFAULT_URL in browser-pane.js, which the launcher page
       does not load. */
    const DEFAULT_BROWSER_PANE_URL = 'http://127.0.0.1:3000';
    const DEFAULT_TERMINALS = Array.from({ length: MAX_SESSIONS }, (_, index) => ({
        title: `Terminal ${index + 1}`,
        directory: '',
        initial_command: '',
        initial_command_mode: 'command',
        startup_mode: 'terminal',
        agent_selection: '',
        custom_agent: '',
        explorer_tree_open: false,
        explorer_git_open: false,
        explorer_search_open: false,
        explorer_open_tabs: [],
        explorer_active_tab: '',
        explorer_tab_views: {},
        explorer_md_preset: '',
        explorer_md_font: '',
        explorer_source_font: '',
        explorer_theme: 'dark',
        browser_tabs: [],
        browser_active_tab: 0,
        distribution: '',
        use_wsl: false,
        use_powershell: false
    }));
    let selectedCount = COUNT_OPTIONS.includes(4) ? 4 : COUNT_OPTIONS[COUNT_OPTIONS.length - 1];
    let selectedLayout = defaultLayoutForCount(selectedCount);
    let layoutChooserOpen = false;
    let connectionMode = 'ssh';
    let savedSessionResolver = null;
    let saveSessionNameResolver = null;
    let savedSessionModalMode = 'import';
    let activeSavedSessionId = '';
    let activeSavedSessionName = '';
    let activeWorkspaceLayout = null;
    let cachedWslDistros = null;
    let lastTerminalSetupTargetSignature = '';
    let installKind = 'git';
    const agentPreflightRequestState = new WeakMap();
    const agentPreflightTimerState = new WeakMap();
    const ACTIVE_SAVED_SESSION_STORAGE_KEY = 'gridvibe.activeSavedSession';
    const DEFAULT_SESSION_ID = 'default-session';
    let savedSessionUpdateChannel = null;
    let lastSavedSessionUpdateToken = '';

    const SSH_FIELDS = `
        <div class="form-grid">
            <div class="field span-2">
                <label>Host — IP Address or Hostname</label>
                <div class="host-ping-row">
                    <input type="text" id="ssh_host" placeholder="192.168.1.100 or myserver.local" autocomplete="off">
                    <button type="button" class="ghost-btn ssh-ping-btn" id="sshPingBtn">Ping</button>
                </div>
                <div class="ssh-ping-status" id="sshPingStatus" role="status" aria-live="polite"></div>
            </div>
            <div class="field">
                <label>Username</label>
                <input type="text" id="ssh_username" value="ubuntu" placeholder="ubuntu">
            </div>
            <div class="field">
                <label>Password</label>
                <div class="password-input-wrapper">
                    <input type="password" id="ssh_password" placeholder="Leave blank for key auth">
                    <button type="button" class="show-password-btn" id="show_ssh_password" aria-label="Show password">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                        </svg>
                    </button>
                </div>
            </div>
            <div class="field">
                <label>SSH Port</label>
                <input type="number" id="ssh_port" value="22" min="1" max="65535">
            </div>
            <div class="field">
                <label>Default Working Directory</label>
                <input type="text" id="ssh_default_dir" placeholder="/home/ubuntu/project">
            </div>
        </div>
        <p class="mode-note">Use one SSH target for all panes, then override title, directory, or command per terminal on the right.</p>
    `;

    const WSL_FIELDS = `
        <div class="form-grid">
            <div class="field span-2">
                <label>Local Repository</label>
                <input type="hidden" id="wsl_distribution" value="">
                <input type="hidden" id="wsl_username" value="">
                <div class="path-picker">
                    <input type="text" id="wsl_default_dir" value="" placeholder="/home/you/project" autocomplete="off" spellcheck="false">
                    <button type="button" class="ghost-btn picker-btn" onclick="browseLocalRepo()">Browse</button>
                    <button type="button" class="ghost-btn picker-btn" onclick="clearLocalRepo()">Clear</button>
                </div>
            </div>
        </div>
        <p class="mode-note">Local Repo starts each pane in the selected folder. In browser mode, type or paste the full path if Browse is unavailable. On Windows, enable WSL per terminal and optionally type a distro name such as Ubuntu; blank uses your preferred/default WSL distro.</p>
    `;

    const LAYOUT_COPY = {
        1: {
            single: { label: 'Single Terminal', note: 'One focused pane', preview: 'single' }
        },
        2: {
            vertical: { label: 'Vertical Split', note: 'Two side-by-side panes', preview: 'two-vertical' },
            horizontal: { label: 'Horizontal Split', note: 'Two stacked panes', preview: 'two-horizontal' }
        },
        3: {
            vertical: { label: 'Vertical Split', note: 'Three side-by-side panes', preview: 'three-vertical' },
            horizontal: { label: 'Horizontal Split', note: 'Three stacked panes', preview: 'three-horizontal' },
            split: { label: 'Mixed Split', note: 'One tall pane plus two stacked panes', preview: 'three-split' }
        },
        4: {
            grid: { label: 'Grid Layout', note: 'Fixed 2 x 2 arrangement', preview: 'grid' }
        },
        6: {
            grid: { label: 'Grid Layout', note: 'Fixed 3 x 2 arrangement', preview: 'grid' }
        },
        8: {
            grid: { label: 'Grid Layout', note: 'Fixed 4 x 2 arrangement', preview: 'grid' }
        }
    };

    document.querySelectorAll('.mode-btn').forEach(button => {
        button.addEventListener('click', () => {
            if (button.dataset.mode === connectionMode) {
                return;
            }
            document.querySelectorAll('.mode-btn').forEach(item => item.classList.remove('active'));
            button.classList.add('active');
            connectionMode = button.dataset.mode;
            renderModeFields();
            resetTerminalSetupIfTargetChanged(connectionMode, collectModeInputs());
            updateHeaderBadges();
        });
    });


    function defaultLayoutForCount(count) {
        if (count <= 1) {
            return 'single';
        }
        return count >= 4 ? 'grid' : 'vertical';
    }

    function buildDefaultTerminalDrafts() {
        return DEFAULT_TERMINALS.map(terminal => ({ ...terminal }));
    }

    function buildConnectionTargetSignature(mode = connectionMode, values = collectModeInputs()) {
        const inputs = values || collectModeInputs();
        if (mode === 'wsl') {
            return JSON.stringify({
                mode: 'wsl',
                distribution: String(inputs?.wsl?.distribution || '').trim(),
                username: String(inputs?.wsl?.username || '').trim(),
                default_dir: String(inputs?.wsl?.default_dir || '').trim()
            });
        }

        return JSON.stringify({
            mode: 'ssh',
            host: String(inputs?.ssh?.host || '').trim(),
            username: String(inputs?.ssh?.username || '').trim(),
            port: String(inputs?.ssh?.port || '').trim(),
            default_dir: String(inputs?.ssh?.default_dir || '').trim()
        });
    }

    function updateTerminalTargetSignature(mode = connectionMode, values = collectModeInputs()) {
        lastTerminalSetupTargetSignature = buildConnectionTargetSignature(mode, values);
    }

    function resetTerminalSetup() {
        buildTerminalRows(selectedCount, buildDefaultTerminalDrafts());
    }

    function clearActiveWorkspaceLayoutOverride() {
        activeWorkspaceLayout = null;
    }

    function resetTerminalSetupIfTargetChanged(mode = connectionMode, values = collectModeInputs()) {
        const nextSignature = buildConnectionTargetSignature(mode, values);
        if (!lastTerminalSetupTargetSignature) {
            lastTerminalSetupTargetSignature = nextSignature;
            return false;
        }

        if (nextSignature === lastTerminalSetupTargetSignature) {
            return false;
        }

        lastTerminalSetupTargetSignature = nextSignature;
        resetTerminalSetup();
        return true;
    }


    function buildDefaultSessionName() {
        const isDefaultSelection = !activeSavedSessionId || activeSavedSessionId === DEFAULT_SESSION_ID;
        if (!isDefaultSelection && activeSavedSessionName) {
            return activeSavedSessionName;
        }

        const config = collectFormConfig();
        const sshHost = config.ssh.host.trim();
        if (config.connection_mode === 'ssh' && sshHost) {
            return sshHost;
        }

        const defaultDir = config.connection_mode === 'wsl'
            ? config.wsl.default_dir.trim()
            : '';
        const firstTerminalDir = config.terminals.find(terminal => terminal.directory.trim())?.directory?.trim() || '';
        const directoryName = getDirectoryName(defaultDir || firstTerminalDir);
        if (directoryName) {
            return directoryName;
        }

        const now = new Date();
        const parts = [
            now.getFullYear(),
            String(now.getMonth() + 1).padStart(2, '0'),
            String(now.getDate()).padStart(2, '0'),
            String(now.getHours()).padStart(2, '0'),
            String(now.getMinutes()).padStart(2, '0'),
            String(now.getSeconds()).padStart(2, '0')
        ];
        const randomPart = Math.random().toString(16).slice(2, 6);
        return `session-${parts.join('')}-${randomPart}`;
    }





    function getDisplaySubdirectory(directory, defaultDir, mode) {
        const rawDirectory = String(directory || '').trim();
        const baseDirectory = String(defaultDir || '').trim();
        if (!rawDirectory || !baseDirectory || !isAbsoluteDirectory(rawDirectory, mode)) {
            return rawDirectory;
        }

        const normalizedDirectory = normalizeComparableDirectory(rawDirectory, mode);
        const normalizedBase = normalizeComparableDirectory(baseDirectory, mode);
        if (!normalizedBase) {
            return rawDirectory;
        }
        if (normalizedDirectory === normalizedBase) {
            return '';
        }

        const prefix = normalizedBase === '/' ? '/' : `${normalizedBase}/`;
        if (!normalizedDirectory.startsWith(prefix)) {
            return rawDirectory;
        }

        const relativePart = rawDirectory.trim().replace(/\\/g, '/').slice(prefix.length);
        if (!relativePart) {
            return '';
        }
        return mode === 'wsl' && baseDirectory.includes('\\') && !baseDirectory.includes('/')
            ? relativePart.replace(/\//g, '\\')
            : relativePart;
    }

    function normalizeTerminalsForDisplay(terminals, defaultDir, mode) {
        return (Array.isArray(terminals) ? terminals : DEFAULT_TERMINALS).map((terminal, index) => ({
            ...DEFAULT_TERMINALS[index],
            ...(terminal || {}),
            directory: getDisplaySubdirectory(terminal?.directory || '', defaultDir, mode)
        }));
    }



    function persistActiveSavedSessionMeta() {
        try {
            const payload = {
                id: activeSavedSessionId,
                name: activeSavedSessionName
            };
            window.sessionStorage.setItem(ACTIVE_SAVED_SESSION_STORAGE_KEY, JSON.stringify(payload));
        } catch (_error) {
            // Ignore storage failures in restricted browser contexts.
        }
    }

    function restoreActiveSavedSessionMeta() {
        try {
            const raw = window.sessionStorage.getItem(ACTIVE_SAVED_SESSION_STORAGE_KEY);
            if (!raw) {
                return;
            }

            const parsed = JSON.parse(raw);
            activeSavedSessionId = String(parsed?.id || '').trim();
            activeSavedSessionName = String(parsed?.name || '').trim();
        } catch (_error) {
            activeSavedSessionId = '';
            activeSavedSessionName = '';
        }
    }

    function showMessage(text, type = '') {
        const message = document.getElementById('message');
        message.textContent = text;
        message.className = `message ${type}`.trim();
    }

    let updateStatusClearTimer = null;

    function setUpdateStatus(text, type = '') {
        const status = document.getElementById('quickUpdateStatus');
        if (!status) {
            return;
        }

        status.textContent = text;
        status.className = `inline-status ${type}`.trim();

        window.clearTimeout(updateStatusClearTimer);
        updateStatusClearTimer = null;
        if (text) {
            updateStatusClearTimer = window.setTimeout(() => {
                status.textContent = '';
                status.className = 'inline-status';
            }, 6000);
        }
    }

    function shortCommit(value) {
        return String(value || '').trim().slice(0, 7);
    }

    function getKnownAgentValues() {
        return AGENT_OPTIONS.filter(option => option.value !== 'other').map(option => option.value);
    }

    function agentAutoModeFlag(agentValue) {
        const normalized = String(agentValue || '').trim().toLowerCase();
        if (!normalized || normalized === 'other') {
            return '';
        }
        const option = AGENT_OPTIONS.find(item => item.value === normalized);
        return String(option?.auto_mode_flag || '').trim();
    }

    function agentAutoModeDescription(agentValue) {
        const normalized = String(agentValue || '').trim().toLowerCase();
        if (!normalized || normalized === 'other') {
            return '';
        }
        const option = AGENT_OPTIONS.find(item => item.value === normalized);
        return String(option?.auto_mode_description || '').trim();
    }

    function normalizeTerminalCommandUi(terminal) {
        const startupMode = String(terminal?.startup_mode || '').trim();
        const initialCommandMode = String(terminal?.initial_command_mode || '').trim();
        const initialCommand = String(terminal?.initial_command || '').trim();
        const rawMode = startupMode || initialCommandMode;
        const savedMode = rawMode === 'agent' || initialCommandMode === 'agent'
            ? 'agent'
            : (rawMode === 'browser' || initialCommandMode === 'browser'
                ? 'browser'
                : (rawMode === 'explorer' || initialCommandMode === 'explorer'
                    ? 'explorer'
                    : (initialCommand ? 'command' : 'terminal')));
        const knownAgents = getKnownAgentValues();
        let mode = savedMode;
        let agentSelection = String(terminal?.agent_selection || '').trim().toLowerCase();
        let customAgent = String(terminal?.custom_agent || '').trim();
        let commandValue = initialCommand;
        const agentAutoMode = Boolean(terminal?.agent_auto_mode);

        if (mode === 'agent') {
            if (!agentSelection) {
                if (knownAgents.includes(initialCommand.toLowerCase())) {
                    agentSelection = initialCommand.toLowerCase();
                } else if (initialCommand) {
                    agentSelection = 'other';
                    customAgent = customAgent || initialCommand;
                }
            }

            if (agentSelection !== 'other' && !knownAgents.includes(agentSelection)) {
                agentSelection = customAgent ? 'other' : '';
            }

            if (agentSelection === 'other' && !customAgent && initialCommand) {
                customAgent = initialCommand;
            }

            return {
                mode,
                commandValue,
                agentSelection,
                customAgent,
                agentAutoMode: agentAutoMode && Boolean(agentAutoModeFlag(agentSelection))
            };
        }

        if (mode === 'explorer') {
            return {
                mode,
                commandValue: '',
                agentSelection: '',
                customAgent: ''
            };
        }

        if (mode === 'browser') {
            return {
                mode,
                commandValue: initialCommand || DEFAULT_BROWSER_PANE_URL,
                agentSelection: '',
                customAgent: ''
            };
        }

        if (mode === 'terminal') {
            return {
                mode,
                commandValue: '',
                agentSelection: '',
                customAgent: ''
            };
        }

        if (knownAgents.includes(initialCommand.toLowerCase())) {
            return {
                mode: 'agent',
                commandValue: '',
                agentSelection: initialCommand.toLowerCase(),
                customAgent: ''
            };
        }

        return {
            mode: 'command',
            commandValue,
            agentSelection,
            customAgent
        };
    }

    /* The startup-mode select carries the agent choice inline — plain mode
       values plus "agent:<name>" entries in an Agent optgroup — so one
       dropdown answers "what does this pane run" in a single pick. The
       saved-session payload keeps its separate startup_mode/agent_selection
       fields; only the control is combined. */
    function startupSelectValue(mode, agentSelection) {
        return mode === 'agent'
            ? `agent:${String(agentSelection || '').trim().toLowerCase()}`
            : mode;
    }

    function parseStartupSelection(value) {
        const raw = String(value || '').trim();
        if (raw.startsWith('agent:')) {
            return { mode: 'agent', agent: raw.slice('agent:'.length) };
        }
        return { mode: raw, agent: '' };
    }

    function getRowAgentSelection(row) {
        return parseStartupSelection(row?.querySelector('.startup-mode-select')?.value).agent;
    }

    function renderStartupModeOptions(commandUi) {
        const mode = commandUi.mode;
        const agent = String(commandUi.agentSelection || '').trim().toLowerCase();
        const agentOptions = AGENT_OPTIONS.map(option => `
            <option
                value="agent:${escHtml(option.value)}"
                data-base-label="${escHtml(option.label)}"
                ${mode === 'agent' && agent === option.value ? 'selected' : ''}
            >${escHtml(option.label)}</option>
        `).join('');
        /* The hidden "agent:" placeholder exists so a draft saved in agent
           mode without a chosen agent still has an option to select; it is
           not offered in the open dropdown. */
        return `
            <option value="terminal" ${mode === 'terminal' ? 'selected' : ''}>Terminal</option>
            <option value="command" ${mode === 'command' ? 'selected' : ''}>Initial Command</option>
            <option value="explorer" ${mode === 'explorer' ? 'selected' : ''}>File Explorer</option>
            <option value="browser" ${mode === 'browser' ? 'selected' : ''} ${connectionMode === 'wsl' ? '' : 'disabled'}>Browser</option>
            <optgroup label="Agent">
                <option value="agent:" hidden ${mode === 'agent' && !agent ? 'selected' : ''}>Select agent…</option>
                ${agentOptions}
            </optgroup>
        `;
    }

    function getTerminalCommandMode(row) {
        const mode = row?.dataset?.commandMode;
        if (mode === 'terminal') {
            return 'terminal';
        }
        if (mode === 'agent') {
            return 'agent';
        }
        if (mode === 'explorer') {
            return 'explorer';
        }
        if (mode === 'browser') {
            return 'browser';
        }
        return 'command';
    }

    function buildTerminalInitialCommand(row) {
        const commandMode = getTerminalCommandMode(row);
        if (commandMode === 'terminal' || commandMode === 'explorer') {
            return '';
        }
        if (commandMode === 'browser') {
            return normalizeBrowserPaneUrl(row.querySelector('.t-browser-url')?.value || '');
        }
        if (commandMode === 'agent') {
            const selectedAgent = getRowAgentSelection(row);
            if (selectedAgent === 'other') {
                return row.querySelector('.t-agent-custom')?.value.trim() || '';
            }
            return selectedAgent;
        }

        return row.querySelector('.t-cmd')?.value.trim() || '';
    }

    function normalizeBrowserPaneUrl(value) {
        const rawValue = String(value || '').trim();
        if (!rawValue) {
            throw new Error('Enter a browser URL before launching.');
        }

        const candidate = rawValue.includes('://') ? rawValue : `http://${rawValue}`;
        let parsed;
        try {
            parsed = new URL(candidate);
        } catch {
            throw new Error('Enter a valid HTTP or HTTPS URL for each browser pane.');
        }

        if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.host) {
            throw new Error('Browser panes only support http:// and https:// URLs.');
        }

        return parsed.href;
    }

    /* Explorer file tabs are not editable in the launcher form; they are
       carried invisibly through the terminal row dataset so resaving a preset
       preserves them (ISSUE-2026-015). */
    function parseStringArrayDataset(value) {
        if (!value) {
            return [];
        }
        try {
            const parsed = JSON.parse(value);
            return Array.isArray(parsed) ? parsed.filter(item => typeof item === 'string') : [];
        } catch (_) {
            return [];
        }
    }

    function parseExplorerTabViewsDataset(value) {
        if (!value) {
            return {};
        }
        try {
            const parsed = JSON.parse(value);
            return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
        } catch (_) {
            return {};
        }
    }

    function collectTerminalDrafts() {
        const rows = Array.from(document.querySelectorAll('.t-row'));
        if (!rows.length) {
            return DEFAULT_TERMINALS.map(item => ({ ...item }));
        }

        const drafts = rows.map((row, index) => {
            const commandMode = getTerminalCommandMode(row);
            const initialCommand = buildTerminalInitialCommand(row);
            const directory = row.querySelector('.t-dir').value.trim();
            /* Persisted explorer tab paths are relative to the root the pane
               was saved under, and the row's directory is what selects that
               root. Editing it after importing a saved session retargets the
               pane, so paths captured under the old root would reopen as
               missing files — drop them and let the relaunched pane start on
               its own root instead. */
            const explorerTabsMatchRoot = directory === (row.dataset.explorerTabsDir || '');
            /* Browser rows only expose the active URL as an input; the rest of
               the tab strip rides along in the dataset so importing a saved
               multi-tab pane and re-saving it keeps every tab. The visible
               input is authoritative for the active slot — otherwise editing
               the URL here would be overwritten by the stale stored tab. */
            const browserActiveTab = Number(row.dataset.browserActiveTab) || 0;
            const browserTabs = commandMode === 'browser'
                ? parseStringArrayDataset(row.dataset.browserTabs)
                : [];
            if (commandMode === 'browser' && initialCommand) {
                if (browserActiveTab < browserTabs.length) {
                    browserTabs[browserActiveTab] = initialCommand;
                } else {
                    browserTabs.push(initialCommand);
                }
            }
            return {
                title: row.querySelector('.t-title')?.value.trim() || `Terminal ${index + 1}`,
                directory,
                initial_command: initialCommand,
                initial_command_mode: commandMode === 'agent'
                    ? 'agent'
                    : (commandMode === 'explorer' || commandMode === 'browser' ? commandMode : 'command'),
                startup_mode: commandMode === 'agent'
                    ? 'agent'
                    : (commandMode === 'explorer' || commandMode === 'browser' ? commandMode : 'terminal'),
                agent_selection: commandMode === 'agent' ? getRowAgentSelection(row) : '',
                custom_agent: commandMode === 'agent'
                    ? (row.querySelector('.t-agent-custom')?.value.trim() || '')
                    : '',
                agent_auto_mode: commandMode === 'agent'
                    && Boolean(agentAutoModeFlag(getRowAgentSelection(row)))
                    && Boolean(row.querySelector('.t-agent-auto-mode')?.checked),
                explorer_tree_open: commandMode === 'explorer' && row.dataset.explorerTreeOpen === 'true',
                explorer_git_open: commandMode === 'explorer' && row.dataset.explorerGitOpen === 'true',
                explorer_search_open: commandMode === 'explorer' && row.dataset.explorerSearchOpen === 'true',
                explorer_open_tabs: commandMode === 'explorer' && explorerTabsMatchRoot
                    ? parseStringArrayDataset(row.dataset.explorerOpenTabs)
                    : [],
                explorer_active_tab: commandMode === 'explorer' && explorerTabsMatchRoot
                    ? (row.dataset.explorerActiveTab || '')
                    : '',
                explorer_tab_views: commandMode === 'explorer' && explorerTabsMatchRoot
                    ? parseExplorerTabViewsDataset(row.dataset.explorerTabViews)
                    : {},
                explorer_md_preset: commandMode === 'explorer' ? (row.dataset.explorerMdPreset || '') : '',
                explorer_md_font: commandMode === 'explorer' ? (row.dataset.explorerMdFont || '') : '',
                explorer_source_font: commandMode === 'explorer' ? (row.dataset.explorerSourceFont || '') : '',
                explorer_theme: commandMode === 'explorer' ? (row.dataset.explorerTheme || 'dark') : '',
                /* Browser rows only expose the active URL as an input; the rest
                   of the tab strip rides along in the dataset so importing a
                   saved multi-tab pane and re-saving it keeps every tab. */
                browser_tabs: browserTabs,
                browser_active_tab: commandMode === 'browser'
                    ? Math.max(0, Math.min(browserTabs.length - 1, browserActiveTab))
                    : 0,
                distribution: LOCAL_WINDOWS_SHELLS_AVAILABLE ? (row.querySelector('.t-distribution')?.value.trim() || '') : '',
                use_wsl: LOCAL_WINDOWS_SHELLS_AVAILABLE && commandMode !== 'explorer' && commandMode !== 'browser'
                    ? Boolean(row.querySelector('.t-use-wsl')?.checked)
                    : false,
                use_powershell: LOCAL_WINDOWS_SHELLS_AVAILABLE && commandMode !== 'explorer' && commandMode !== 'browser'
                    ? Boolean(row.querySelector('.t-use-powershell')?.checked)
                    : false
            };
        });

        while (drafts.length < MAX_SESSIONS) {
            drafts.push({ ...DEFAULT_TERMINALS[drafts.length] });
        }

        return drafts;
    }

    function renderCountOptions() {
        const countGrid = document.getElementById('countGrid');
        countGrid.innerHTML = COUNT_OPTIONS.map(count => {
            const isSelected = count === selectedCount;
            const isLayoutOpen = layoutChooserOpen && isSelected;
            return `
                <div class="count-option ${isSelected ? 'active' : ''}">
                    <button type="button" class="count-btn ${isSelected ? 'active' : ''}" data-count="${count}">
                        <div class="count-meta">
                            <span class="count-value">${count}</span>
                        </div>
                        <span class="count-label">${count === 1 ? 'terminal' : 'terminals'}</span>
                    </button>
                    <button
                        type="button"
                        class="count-layout-toggle ${isLayoutOpen ? 'active' : ''}"
                        data-count="${count}"
                        aria-label="Choose layout for ${count} terminal${count === 1 ? '' : 's'}"
                        aria-controls="layoutPanel"
                        aria-expanded="${isLayoutOpen ? 'true' : 'false'}"
                    ></button>
                </div>
            `;
        }).join('');

        const indicator = document.getElementById('countIndicator');
        if (indicator) {
            indicator.textContent = `Selected: ${selectedCount} terminal${selectedCount === 1 ? '' : 's'}`;
        }

        countGrid.querySelectorAll('.count-btn').forEach(button => {
            button.addEventListener('click', () => {
                const nextCount = Number(button.dataset.count);
                if (nextCount === selectedCount) {
                    if (layoutChooserOpen) {
                        layoutChooserOpen = false;
                        renderCountOptions();
                        renderLayoutOptions();
                    }
                    return;
                }

                const drafts = collectTerminalDrafts();
                selectedCount = nextCount;
                selectedLayout = defaultLayoutForCount(nextCount);
                clearActiveWorkspaceLayoutOverride();
                layoutChooserOpen = false;
                renderCountOptions();
                renderLayoutOptions();
                buildTerminalRows(selectedCount, drafts);
            });
        });

        countGrid.querySelectorAll('.count-layout-toggle').forEach(button => {
            button.addEventListener('click', () => {
                const nextCount = Number(button.dataset.count);
                const drafts = collectTerminalDrafts();
                const wasOpenForCount = layoutChooserOpen && nextCount === selectedCount;
                if (nextCount !== selectedCount) {
                    selectedCount = nextCount;
                    selectedLayout = defaultLayoutForCount(nextCount);
                    clearActiveWorkspaceLayoutOverride();
                    buildTerminalRows(selectedCount, drafts);
                }

                layoutChooserOpen = !wasOpenForCount;
                renderCountOptions();
                renderLayoutOptions();
            });
        });
    }

    function renderLayoutOptions() {
        const options = LAYOUT_COPY[selectedCount];
        const panel = document.getElementById('layoutPanel');
        const container = document.getElementById('layoutOptions');
        const keys = Object.keys(options);
        if (panel) {
            panel.hidden = !layoutChooserOpen;
        }
        const layoutIndicator = document.getElementById('layoutIndicator');
        if (layoutIndicator) {
            const activeOption = options[selectedLayout] || options[keys[0]];
            layoutIndicator.textContent = activeOption
                ? `${selectedCount} terminal${selectedCount === 1 ? '' : 's'} - ${activeOption.label}`
                : 'Choose pane arrangement';
        }
        container.className = `layout-grid${keys.length === 1 ? ' single' : ''}`;
        container.innerHTML = keys.map(key => {
            const option = options[key];
            // The preview draws a grid for any count the grid option offers, so
            // it keeps a 2×2 floor where the shared helper has no grid shape.
            const gridMetrics = option.preview === 'grid'
                ? (getGridMetrics(selectedCount) || { columns: 2, rows: 2 })
                : null;
            const previewStyle = gridMetrics
                ? ` style="--preview-columns:${gridMetrics.columns}; --preview-rows:${gridMetrics.rows};"`
                : '';
            return `
                <button type="button" class="layout-btn ${selectedLayout === key ? 'active' : ''}" data-layout="${key}">
                    <div class="layout-preview ${option.preview}"${previewStyle}>
                        ${Array.from({ length: selectedCount }, () => '<span class="pane"></span>').join('')}
                    </div>
                    <div class="layout-copy">
                        <strong>${option.label}</strong>
                        <span>${option.note}</span>
                    </div>
                </button>
            `;
        }).join('');

        container.querySelectorAll('.layout-btn').forEach(button => {
            button.addEventListener('click', () => {
                const nextLayout = button.dataset.layout;
                clearActiveWorkspaceLayoutOverride();
                selectedLayout = nextLayout;
                layoutChooserOpen = false;
                renderCountOptions();
                renderLayoutOptions();
            });
        });
    }

    function renderModeFields() {
        const container = document.getElementById('modeFields');
        const previous = collectModeInputs();
        container.innerHTML = connectionMode === 'ssh' ? SSH_FIELDS : WSL_FIELDS;
        applyModeInputs(previous);
        initShowPasswordButton();
        initSshPingButton();
        bindModeFieldInteractions();
    }

    function initShowPasswordButton() {
        const passwordInput = document.getElementById('ssh_password');
        const showButton = document.getElementById('show_ssh_password');
        if (!passwordInput || !showButton) return;

        const togglePassword = (show) => {
            passwordInput.type = show ? 'text' : 'password';
        };

        showButton.addEventListener('mousedown', () => togglePassword(true));
        showButton.addEventListener('mouseup', () => togglePassword(false));
        showButton.addEventListener('mouseleave', () => togglePassword(false));

        showButton.addEventListener('touchstart', (e) => { e.preventDefault(); togglePassword(true); });
        showButton.addEventListener('touchend', (e) => { e.preventDefault(); togglePassword(false); });
    }

    function setSshPingStatus(text, type = '') {
        const status = document.getElementById('sshPingStatus');
        if (!status) {
            return;
        }
        status.textContent = text;
        status.className = `ssh-ping-status ${type}`.trim();
    }

    function initSshPingButton() {
        const button = document.getElementById('sshPingBtn');
        const hostInput = document.getElementById('ssh_host');
        const portInput = document.getElementById('ssh_port');
        if (!button || !hostInput) {
            return;
        }

        hostInput.addEventListener('input', () => setSshPingStatus(''));
        portInput?.addEventListener('input', () => setSshPingStatus(''));

        button.addEventListener('click', async () => {
            const host = hostInput.value.trim();
            const port = Number(portInput?.value) || 22;
            if (!host) {
                setSshPingStatus('Enter a host or IP address before pinging.', 'error');
                hostInput.focus();
                return;
            }

            button.disabled = true;
            button.textContent = 'Pinging...';
            setSshPingStatus(`Checking ${host}...`);
            try {
                const response = await fetch('/api/ssh-ping', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ host, port })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Ping failed.');
                }
                setSshPingStatus(data.message || (data.reachable ? 'Target is reachable.' : 'Target is not reachable.'), data.reachable ? 'success' : 'error');
            } catch (error) {
                setSshPingStatus(error.message || 'Ping failed.', 'error');
            } finally {
                button.disabled = false;
                button.textContent = 'Ping';
            }
        });
    }

    function collectModeInputs() {
        return {
            ssh: {
                host: document.getElementById('ssh_host')?.value ?? '',
                username: document.getElementById('ssh_username')?.value ?? 'ubuntu',
                password: document.getElementById('ssh_password')?.value ?? '',
                port: document.getElementById('ssh_port')?.value ?? '22',
                default_dir: document.getElementById('ssh_default_dir')?.value ?? ''
            },
            wsl: {
                distribution: document.getElementById('wsl_distribution')?.value ?? '',
                username: document.getElementById('wsl_username')?.value ?? '',
                default_dir: document.getElementById('wsl_default_dir')?.value ?? ''
            }
        };
    }

    function setLocalRepoPath(path) {
        const input = document.getElementById('wsl_default_dir');
        if (!input) {
            return;
        }

        const normalized = String(path ?? '').trim();
        const resolved = normalized === '~' ? '' : normalized;
        input.value = resolved;
        input.title = resolved;
    }

    function applyModeInputs(values) {
        if (document.getElementById('ssh_host')) {
            document.getElementById('ssh_host').value = values.ssh.host ?? '';
            document.getElementById('ssh_username').value = values.ssh.username ?? 'ubuntu';
            document.getElementById('ssh_password').value = values.ssh.password ?? '';
            document.getElementById('ssh_port').value = values.ssh.port ?? '22';
            document.getElementById('ssh_default_dir').value = values.ssh.default_dir ?? '';
        }

        if (document.getElementById('wsl_default_dir')) {
            document.getElementById('wsl_distribution').value = values.wsl.distribution ?? '';
            document.getElementById('wsl_username').value = values.wsl.username ?? '';
            setLocalRepoPath(values.wsl.default_dir ?? '');
        }
    }

    function bindModeFieldInteractions() {
        const targetFieldIds = connectionMode === 'wsl'
            ? ['wsl_distribution', 'wsl_username', 'wsl_default_dir']
            : ['ssh_host', 'ssh_username', 'ssh_port', 'ssh_default_dir'];
        const preflightFieldIds = connectionMode === 'wsl'
            ? ['wsl_distribution', 'wsl_username', 'wsl_default_dir']
            : ['ssh_host', 'ssh_username', 'ssh_password', 'ssh_port', 'ssh_default_dir'];

        targetFieldIds.forEach(fieldId => {
            const field = document.getElementById(fieldId);
            field?.addEventListener('change', () => {
                resetTerminalSetupIfTargetChanged(connectionMode, collectModeInputs());
            });
        });

        preflightFieldIds.forEach(fieldId => {
            const field = document.getElementById(fieldId);
            field?.addEventListener('change', () => refreshVisibleAgentPreflights());
        });
    }

    async function browseLocalRepo() {
        const currentPath = document.getElementById('wsl_default_dir')?.value ?? '';

        try {
            let selectedPath = '';

            if (window.pywebview?.api?.select_folder) {
                const result = await window.pywebview.api.select_folder(currentPath);
                if (!result?.ok) {
                    if (result?.cancelled) {
                        return;
                    }
                    throw new Error(result?.error || 'Folder picker is unavailable');
                }
                selectedPath = String(result.path || '').trim();
            } else {
                const response = await fetch('/api/select-folder', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ initial_dir: currentPath })
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Folder picker is unavailable');
                }
                if (data.manual_entry) {
                    throw new Error(data.error || 'Native folder picker support is unavailable');
                }
                selectedPath = String(data.path || '').trim();
            }

            if (!selectedPath) {
                return;
            }

            setLocalRepoPath(selectedPath);
            resetTerminalSetupIfTargetChanged('wsl', collectModeInputs());
        } catch (error) {
            const message = String(error?.message || '');
            if (message.includes('Native folder picker support is unavailable')) {
                const input = document.getElementById('wsl_default_dir');
                input?.focus();
                showMessage('Native folder picker is unavailable in browser mode. Type or paste the local repository path.', 'info');
                return;
            }
            showMessage(`Folder selection failed: ${error.message}`, 'error');
        }
    }

    function clearLocalRepo() {
        setLocalRepoPath('');
        resetTerminalSetupIfTargetChanged('wsl', collectModeInputs());
    }

    function toggleInlineTip(button) {
        const field = button.closest('.field');
        const tip = field?.querySelector('.inline-tip');
        if (!tip) {
            return;
        }

        const isVisible = tip.classList.toggle('visible');
        button.setAttribute('aria-expanded', String(isVisible));
    }

    function selectSuggestedDistro(button) {
        const distroName = String(button?.dataset?.distro || '').trim();
        if (!distroName) {
            return;
        }

        const field = button.closest('.t-distribution-field');
        const input = field?.querySelector('.t-distribution');
        if (!input) {
            return;
        }

        input.value = distroName;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
        input.select();
    }

    function renderWslDistrosTip(data) {
        if (!data?.available) {
            return `Could not inspect distros automatically. Run <code>${escHtml(data?.command || 'wsl -l -v')}</code> in cmd or PowerShell.`;
        }

        if (Array.isArray(data.distros) && data.distros.length > 0) {
            const items = data.distros.map(distro => {
                const details = [
                    distro.state || 'Unknown state',
                    distro.version ? `WSL ${distro.version}` : '',
                    distro.default ? 'default' : ''
                ].filter(Boolean).join(' • ');
                return `
                    <li>
                        <button
                            type="button"
                            class="tip-link-btn"
                            data-distro="${escHtml(distro.name || 'Unnamed distro')}"
                            onclick="selectSuggestedDistro(this)"
                        >${escHtml(distro.name || 'Unnamed distro')}</button>
                        ${details ? ` <span>${escHtml(details)}</span>` : ''}
                    </li>
                `;
            }).join('');
            return `Fetched from <code>${escHtml(data.command || 'wsl -l -v')}</code>.<ul>${items}</ul>`;
        }

        if (data.raw_output) {
            return `No distros were parsed from <code>${escHtml(data.command || 'wsl -l -v')}</code>.<pre>${escHtml(data.raw_output)}</pre>`;
        }

        return `No WSL distros were reported by <code>${escHtml(data.command || 'wsl -l -v')}</code>.`;
    }

    async function toggleUbuntuDistroTip(button) {
        const field = button.closest('.field');
        const tip = field?.querySelector('.inline-tip');
        if (!tip) {
            return;
        }

        if (tip.classList.contains('visible')) {
            tip.classList.remove('visible');
            button.setAttribute('aria-expanded', 'false');
            return;
        }

        tip.classList.add('visible');
        button.setAttribute('aria-expanded', 'true');
        tip.innerHTML = 'Checking local WSL distros...';

        try {
            if (!cachedWslDistros) {
                const response = await fetch('/api/wsl-distros');
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to inspect WSL distros');
                }
                cachedWslDistros = data;
            }

            tip.innerHTML = renderWslDistrosTip(cachedWslDistros);
        } catch (error) {
            tip.innerHTML = `Could not inspect distros automatically. Run <code>wsl -l -v</code> in cmd or PowerShell.`;
        }
    }

    function syncTerminalWslState(row) {
        const commandMode = getTerminalCommandMode(row);
        const shellField = row.querySelector('.t-shell-field');
        const shellDisabled = commandMode === 'explorer' || commandMode === 'browser';
        const shouldShowDistribution = connectionMode === 'wsl'
            && !shellDisabled
            && Boolean(row.querySelector('.t-use-wsl')?.checked);
        const distributionField = row.querySelector('.t-distribution-field');
        shellField?.classList.toggle('hidden', connectionMode !== 'wsl' || shellDisabled);
        if (!distributionField) {
            return;
        }

        distributionField.classList.toggle('hidden', !shouldShowDistribution);
        if (!shouldShowDistribution) {
            distributionField.querySelector('.inline-tip')?.classList.remove('visible');
            distributionField.querySelector('.tip-btn')?.setAttribute('aria-expanded', 'false');
        }
    }

    function syncTerminalCommandState(row) {
        const commandMode = getTerminalCommandMode(row);
        const commandField = row.querySelector('.t-command-field');
        const agentField = row.querySelector('.t-agent-field');
        const customAgentField = row.querySelector('.t-agent-custom-field');
        const browserField = row.querySelector('.t-browser-field');
        const startupModeSelect = row.querySelector('.startup-mode-select');
        const selection = parseStartupSelection(startupModeSelect?.value);
        const selectedAgent = commandMode === 'agent' ? selection.agent : '';

        if (startupModeSelect && selection.mode !== commandMode) {
            startupModeSelect.value = startupSelectValue(commandMode, selectedAgent);
        }

        commandField?.classList.toggle('hidden', commandMode !== 'command');
        agentField?.classList.toggle('hidden', commandMode !== 'agent');
        customAgentField?.classList.toggle('hidden', !(commandMode === 'agent' && selectedAgent === 'other'));
        browserField?.classList.toggle('hidden', commandMode !== 'browser');
        syncTerminalAgentAutoModeState(row, commandMode, selectedAgent);
        if (commandMode !== 'agent') {
            clearAgentPreflight(row);
        }
        if (commandMode === 'explorer' || commandMode === 'browser') {
            const wslCheckbox = row.querySelector('.t-use-wsl');
            const powershellCheckbox = row.querySelector('.t-use-powershell');
            if (wslCheckbox) wslCheckbox.checked = false;
            if (powershellCheckbox) powershellCheckbox.checked = false;
        }
        syncTerminalWslState(row);
    }

    function syncTerminalAgentAutoModeState(row, commandMode, selectedAgent) {
        const autoField = row.querySelector('.t-agent-auto-field');
        if (!autoField) {
            return;
        }
        const flag = agentAutoModeFlag(selectedAgent);
        const available = commandMode === 'agent' && Boolean(flag);
        autoField.classList.toggle('hidden', !available);
        /* The explanation hides behind the check-field's ? tip; it updates
           in place while open, and closes when auto mode goes away. */
        const help = row.querySelector('.t-agent-auto-help');
        if (help) {
            const description = available ? agentAutoModeDescription(selectedAgent) : '';
            help.textContent = available
                ? `Launches as "${selectedAgent} ${flag}".${description ? ` ${description}` : ''}`
                : '';
            if (!available) {
                help.classList.remove('visible');
                autoField.querySelector('.tip-btn')?.setAttribute('aria-expanded', 'false');
            }
        }
        if (!available) {
            const checkbox = autoField.querySelector('.t-agent-auto-mode');
            if (checkbox) {
                checkbox.checked = false;
            }
        }
    }

    function resetTerminalCommandOnModeChange(row, nextMode) {
        const previousMode = getTerminalCommandMode(row);
        const commandInput = row.querySelector('.t-cmd');
        const customAgentInput = row.querySelector('.t-agent-custom');

        if (previousMode === 'command' && nextMode !== 'command' && commandInput) {
            commandInput.value = '';
        }

        /* The agent choice itself needs no clearing: it lives in the same
           select, so picking a non-agent mode already replaced it. */
        if (previousMode === 'agent' && nextMode !== 'agent') {
            if (customAgentInput) customAgentInput.value = '';
            const autoModeCheckbox = row.querySelector('.t-agent-auto-mode');
            if (autoModeCheckbox) autoModeCheckbox.checked = false;
        }
    }

    function _resetAgentOptionLabels(select) {
        if (!select) {
            return;
        }

        Array.from(select.options).forEach(option => {
            option.textContent = option.dataset.baseLabel || option.textContent;
        });
    }

    function _clearAgentStatusClasses(select) {
        if (!select) {
            return;
        }

        select.classList.remove(
            'status-installed',
            'status-missing',
            'status-unsupported_here',
            'status-missing_prerequisite',
            'status-needs_manual_install',
            'status-target_incomplete',
            'status-check_failed'
        );
    }

    function clearAgentPreflight(row) {
        const select = row?.querySelector('.startup-mode-select');
        const disclosure = row?.querySelector('.agent-preflight-disclosure');
        const summary = row?.querySelector('.agent-preflight-summary');
        const summaryLabel = row?.querySelector('.agent-preflight-summary-label');
        const copy = row?.querySelector('.agent-preflight-copy');
        const timerId = agentPreflightTimerState.get(row);
        if (timerId) {
            clearTimeout(timerId);
            agentPreflightTimerState.delete(row);
        }

        _clearAgentStatusClasses(select);
        _resetAgentOptionLabels(select);
        if (select) {
            select.title = '';
        }
        if (summary) {
            summary.className = 'agent-preflight-summary';
        }
        if (summaryLabel) {
            summaryLabel.textContent = '';
        }
        if (copy) {
            copy.innerHTML = '';
        }
        if (disclosure) {
            disclosure.open = false;
            disclosure.classList.remove('visible');
        }
    }

    function renderAgentPreflight(row, payload) {
        const select = row?.querySelector('.startup-mode-select');
        const disclosure = row?.querySelector('.agent-preflight-disclosure');
        const summary = row?.querySelector('.agent-preflight-summary');
        const summaryLabel = row?.querySelector('.agent-preflight-summary-label');
        const copy = row?.querySelector('.agent-preflight-copy');
        if (!select || !disclosure || !summary || !summaryLabel || !copy) {
            return;
        }

        const status = String(payload?.status || '').trim();
        const label = String(payload?.status_label || 'Unknown').trim();
        const message = String(payload?.message || '').trim();
        const warning = String(payload?.warning || '').trim();
        const installLabel = String(payload?.install?.label || '').trim();
        const installCommand = String(payload?.install?.command || '').trim();
        const targetLabel = String(payload?.target?.label || '').trim();
        const prerequisite = Array.isArray(payload?.missing_prerequisites) && payload.missing_prerequisites.length
            ? String(payload.missing_prerequisites[0] || '').trim()
            : '';
        const selectedOption = select.options[select.selectedIndex] || null;
        const wasOpen = disclosure.open;

        const lines = [];
        if (message) {
            lines.push(`<strong>${escHtml(message)}</strong>`);
        }
        if (targetLabel) {
            lines.push(`Target: <code>${escHtml(targetLabel)}</code>`);
        }
        if (prerequisite) {
            lines.push(`Prerequisite: ${escHtml(prerequisite)}`);
        }
        if (installCommand) {
            const installPrefix = installLabel ? `${escHtml(installLabel)}: ` : 'Install: ';
            lines.push(`${installPrefix}<code>${escHtml(installCommand)}</code>`);
        }
        if (warning) {
            lines.push(escHtml(warning));
        }

        _clearAgentStatusClasses(select);
        _resetAgentOptionLabels(select);
        select.classList.add(`status-${status}`);
        select.title = [message, targetLabel ? `Target: ${targetLabel}` : '', prerequisite, installCommand ? `${installLabel || 'Install'}: ${installCommand}` : '', warning]
            .filter(Boolean)
            .join('\n');
        /* Only agent entries carry data-base-label; the plain mode options
           must never grow a status suffix. */
        if (selectedOption && selectedOption.dataset.baseLabel) {
            selectedOption.textContent = `${selectedOption.dataset.baseLabel} · ${label}`;
        }

        summary.className = `agent-preflight-summary ${escHtml(status)}`.trim();
        summaryLabel.textContent = label;
        copy.innerHTML = lines.join('<br>');
        disclosure.classList.add('visible');
        disclosure.open = wasOpen;
    }

    function buildAgentPreflightPayload(row) {
        return {
            agent: getRowAgentSelection(row),
            connection_mode: connectionMode,
            ssh: {
                host: document.getElementById('ssh_host')?.value.trim() || '',
                username: document.getElementById('ssh_username')?.value.trim() || 'ubuntu',
                password: document.getElementById('ssh_password')?.value || '',
                port: Number(document.getElementById('ssh_port')?.value) || 22
            },
            wsl: {
                distribution: document.getElementById('wsl_distribution')?.value.trim() || '',
                username: document.getElementById('wsl_username')?.value.trim() || '',
                default_dir: document.getElementById('wsl_default_dir')?.value.trim() || ''
            },
            terminal: {
                distribution: row?.querySelector('.t-distribution')?.value.trim() || '',
                use_wsl: Boolean(row?.querySelector('.t-use-wsl')?.checked),
                use_powershell: Boolean(row?.querySelector('.t-use-powershell')?.checked)
            }
        };
    }

    function scheduleAgentPreflight(row, delayMs = 180) {
        if (!row) {
            return;
        }

        const timerId = agentPreflightTimerState.get(row);
        if (timerId) {
            clearTimeout(timerId);
        }

        const nextTimer = window.setTimeout(() => {
            agentPreflightTimerState.delete(row);
            void queueAgentPreflight(row);
        }, delayMs);
        agentPreflightTimerState.set(row, nextTimer);
    }

    async function queueAgentPreflight(row) {
        if (!row) {
            return;
        }

        if (getTerminalCommandMode(row) !== 'agent') {
            clearAgentPreflight(row);
            return;
        }

        const selectedAgent = getRowAgentSelection(row);
        if (!selectedAgent || selectedAgent === 'other') {
            clearAgentPreflight(row);
            return;
        }

        const requestId = (agentPreflightRequestState.get(row) || 0) + 1;
        agentPreflightRequestState.set(row, requestId);
        renderAgentPreflight(row, {
            status: 'target_incomplete',
            status_label: 'Checking',
            message: 'Checking agent CLI availability...',
            target: {
                label: ''
            },
            install: {
                label: '',
                command: ''
            },
            missing_prerequisites: []
        });

        try {
            const response = await fetch('/api/agent-preflight', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildAgentPreflightPayload(row))
            });
            const data = await response.json();
            if (agentPreflightRequestState.get(row) !== requestId) {
                return;
            }
            if (!response.ok) {
                throw new Error(data.error || 'Agent preflight failed');
            }
            renderAgentPreflight(row, data);
        } catch (error) {
            if (agentPreflightRequestState.get(row) !== requestId) {
                return;
            }
            renderAgentPreflight(row, {
                status: 'check_failed',
                status_label: 'Check failed',
                message: error.message || 'Agent preflight failed.',
                target: {
                    label: ''
                },
                install: {
                    label: '',
                    command: ''
                },
                missing_prerequisites: []
            });
        }
    }

    function refreshVisibleAgentPreflights() {
        document.querySelectorAll('.t-row').forEach(row => {
            if (getTerminalCommandMode(row) !== 'agent') {
                clearAgentPreflight(row);
                return;
            }
            scheduleAgentPreflight(row, 120);
        });
    }

    function handleTerminalShellToggle(row, shellType) {
        const wslCheckbox = row.querySelector('.t-use-wsl');
        const powershellCheckbox = row.querySelector('.t-use-powershell');

        if (shellType === 'wsl' && wslCheckbox?.checked && powershellCheckbox) {
            powershellCheckbox.checked = false;
        }

        if (shellType === 'powershell' && powershellCheckbox?.checked && wslCheckbox) {
            wslCheckbox.checked = false;
        }

        syncTerminalWslState(row);
        scheduleAgentPreflight(row, 120);
    }

    function bindTerminalRowInteractions() {
        document.querySelectorAll('.t-row').forEach(row => {
            const wslCheckbox = row.querySelector('.t-use-wsl');
            const powershellCheckbox = row.querySelector('.t-use-powershell');
            const distributionInput = row.querySelector('.t-distribution');
            const startupModeSelect = row.querySelector('.startup-mode-select');

            startupModeSelect?.addEventListener('change', () => {
                const nextMode = parseStartupSelection(startupModeSelect.value).mode;
                resetTerminalCommandOnModeChange(row, nextMode);
                row.dataset.commandMode = nextMode;
                syncTerminalCommandState(row);
                scheduleAgentPreflight(row, 60);
            });
            wslCheckbox?.addEventListener('change', () => handleTerminalShellToggle(row, 'wsl'));
            powershellCheckbox?.addEventListener('change', () => handleTerminalShellToggle(row, 'powershell'));
            distributionInput?.addEventListener('change', () => scheduleAgentPreflight(row, 120));
            syncTerminalCommandState(row);
            syncTerminalWslState(row);
            scheduleAgentPreflight(row, 30);
        });
    }

    function buildTerminalRows(count, drafts = DEFAULT_TERMINALS) {
        const container = document.getElementById('terminalRows');
        const usableDrafts = drafts.length ? drafts : DEFAULT_TERMINALS;
        /* Rows are rebuilt from scratch on every count change and import, so
           the fold state (view-only, never part of the draft payload) has to
           be snapshotted here and re-applied by index or it would reset. */
        const previousFoldState = Array.from(container.querySelectorAll('.t-row'))
            .map(row => row.classList.contains('t-row-collapsed'));

        container.innerHTML = Array.from({ length: count }, (_, index) => {
            const terminal = usableDrafts[index] || DEFAULT_TERMINALS[index];
            const commandUi = normalizeTerminalCommandUi(terminal);
            if (connectionMode !== 'wsl' && commandUi.mode === 'browser') {
                commandUi.mode = 'terminal';
                commandUi.commandValue = '';
            }
            const rowCollapsed = index < previousFoldState.length
                ? previousFoldState[index]
                : count >= 3;
            return `
                <div
                    class="t-row${rowCollapsed ? ' t-row-collapsed' : ''}"
                    data-command-mode="${escHtml(commandUi.mode)}"
                    data-explorer-tree-open="${terminal.explorer_tree_open ? 'true' : 'false'}"
                    data-explorer-git-open="${terminal.explorer_git_open ? 'true' : 'false'}"
                    data-explorer-search-open="${terminal.explorer_search_open ? 'true' : 'false'}"
                    data-explorer-open-tabs="${escHtml(JSON.stringify(Array.isArray(terminal.explorer_open_tabs) ? terminal.explorer_open_tabs : []))}"
                    data-explorer-tabs-dir="${escHtml(terminal.directory || '')}"
                    data-explorer-active-tab="${escHtml(terminal.explorer_active_tab || '')}"
                    data-explorer-tab-views="${escHtml(JSON.stringify(terminal.explorer_tab_views && typeof terminal.explorer_tab_views === 'object' ? terminal.explorer_tab_views : {}))}"
                    data-explorer-md-preset="${escHtml(terminal.explorer_md_preset || '')}"
                    data-explorer-md-font="${escHtml(terminal.explorer_md_font || '')}"
                    data-explorer-source-font="${escHtml(terminal.explorer_source_font || '')}"
                    data-explorer-theme="${escHtml(terminal.explorer_theme || 'dark')}"
                    data-browser-tabs="${escHtml(JSON.stringify(Array.isArray(terminal.browser_tabs) ? terminal.browser_tabs : []))}"
                    data-browser-active-tab="${escHtml(String(Number(terminal.browser_active_tab) || 0))}"
                >
                    <div class="t-row-head" onclick="onTerminalRowHeadClick(event)">
                        <span class="t-badge">T${index + 1}</span>
                        <input class="t-title" type="text" value="${escHtml(terminal.title || `Terminal ${index + 1}`)}" placeholder="Terminal ${index + 1}" aria-label="Terminal ${index + 1} title">
                        <button
                            type="button"
                            class="t-row-fold-btn"
                            onclick="toggleTerminalRowFold(this)"
                            aria-expanded="${rowCollapsed ? 'false' : 'true'}"
                            aria-label="Fold Terminal ${index + 1} settings"
                            title="Fold Terminal ${index + 1} settings"
                        >
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m6 9 6 6 6-6"></path></svg>
                        </button>
                        <span class="t-status-dot"></span>
                    </div>
                    <div class="t-fields">
                        <div class="field">
                            <label>Subdirectory</label>
                            <input class="t-dir" type="text" value="${escHtml(terminal.directory || '')}" placeholder="Relative to Step 2 folder" title="Optional path inside the Step 2 default folder">
                        </div>
                        <div class="field">
                            <label>Startup Mode</label>
                            <select class="startup-mode-select">
                                ${renderStartupModeOptions(commandUi)}
                            </select>
                        </div>
                        <div class="field t-command-field ${commandUi.mode === 'command' ? '' : 'hidden'}">
                            <label>Initial Command</label>
                            <input class="t-cmd" type="text" value="${escHtml(commandUi.mode === 'command' ? commandUi.commandValue : '')}" placeholder="Blank = shell only">
                        </div>
                        <div class="field t-browser-field ${commandUi.mode === 'browser' ? '' : 'hidden'}">
                            <label>Browser URL</label>
                            <!-- Each mode seeds only its own input. commandValue is the
                                 draft's single initial_command, so in agent mode it holds
                                 the agent name ("claude") — piping it into the hidden URL
                                 box left the pane pointed at http://claude/ the moment the
                                 user switched to Browser. -->
                            <input class="t-browser-url" type="url" value="${escHtml(commandUi.mode === 'browser' ? (commandUi.commandValue || DEFAULT_BROWSER_PANE_URL) : DEFAULT_BROWSER_PANE_URL)}" placeholder="${escHtml(DEFAULT_BROWSER_PANE_URL)}">
                        </div>
                        <div class="field t-agent-field ${commandUi.mode === 'agent' ? '' : 'hidden'}">
                            <details class="agent-preflight-disclosure">
                                <summary class="agent-preflight-summary">
                                    <span class="agent-preflight-summary-label"></span>
                                </summary>
                                <div class="agent-preflight-copy"></div>
                            </details>
                            <label class="check-field t-agent-auto-field ${commandUi.mode === 'agent' && agentAutoModeFlag(commandUi.agentSelection) ? '' : 'hidden'}">
                                <input class="t-agent-auto-mode" type="checkbox" ${commandUi.agentAutoMode ? 'checked' : ''} aria-label="Launch agent in auto mode">
                                <span class="check-copy">
                                    <strong>Auto mode</strong>
                                </span>
                                <button
                                    type="button"
                                    class="tip-btn"
                                    aria-expanded="false"
                                    aria-label="Explain auto mode"
                                    onclick="toggleInlineTip(this)"
                                >?</button>
                            </label>
                            <div class="inline-tip t-agent-auto-help"></div>
                        </div>
                        <div class="field t-agent-custom-field ${commandUi.mode === 'agent' && commandUi.agentSelection === 'other' ? '' : 'hidden'}">
                            <label>Custom Agent</label>
                            <input class="t-agent-custom" type="text" value="${escHtml(commandUi.customAgent)}" placeholder="Enter agent command">
                        </div>
                        ${LOCAL_WINDOWS_SHELLS_AVAILABLE ? `
                        <div class="field t-shell-field ${connectionMode === 'wsl' && commandUi.mode !== 'explorer' && commandUi.mode !== 'browser' ? '' : 'hidden'}">
                            <div class="field-label-row">
                                <label>Shell</label>
                                <button
                                    type="button"
                                    class="tip-btn"
                                    aria-expanded="false"
                                    aria-label="Show WSL shell tip"
                                    onclick="toggleInlineTip(this)"
                                >?</button>
                            </div>
                            <div class="check-stack">
                                <label class="check-field">
                                    <input class="t-use-wsl" type="checkbox" ${terminal.use_wsl ? 'checked' : ''}>
                                    <span class="check-copy">
                                        <strong>Prefer WSL</strong>
                                    </span>
                                </label>
                                <label class="check-field">
                                    <input class="t-use-powershell" type="checkbox" ${terminal.use_powershell ? 'checked' : ''}>
                                    <span class="check-copy">
                                        <strong>Use PowerShell</strong>
                                    </span>
                                </label>
                            </div>
                            <div class="inline-tip">Leave both off for cmd. WSL and PowerShell are mutually exclusive per pane.</div>
                        </div>
                        <div class="field t-distribution-field ${connectionMode === 'wsl' && terminal.use_wsl ? '' : 'hidden'}">
                            <div class="field-label-row">
                                <label>Ubuntu Distro</label>
                                <button
                                    type="button"
                                    class="tip-btn"
                                    aria-expanded="false"
                                    aria-label="Show Ubuntu distro tip"
                                    onclick="toggleUbuntuDistroTip(this)"
                                >?</button>
                            </div>
                            <input class="t-distribution" type="text" value="${escHtml(terminal.distribution || '')}" placeholder="Ubuntu">
                            <div class="inline-tip">Checking local WSL distros...</div>
                        </div>
                        ` : ''}
                    </div>
                </div>
            `;
        }).join('');

        bindTerminalRowInteractions();
    }

    function toggleTerminalRowFold(button) {
        const row = button.closest('.t-row');
        if (!row) {
            return;
        }
        const collapsed = row.classList.toggle('t-row-collapsed');
        button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        if (!collapsed) {
            /* Opening a card should show the whole card, not leave it half
               under the panel fold. Wait a frame so the grid has its final
               height before measuring; a card taller than the panel viewport
               pins its top instead. */
            requestAnimationFrame(() => {
                const wrap = row.closest('.rows-wrap');
                const block = wrap && row.offsetHeight >= wrap.clientHeight ? 'start' : 'nearest';
                row.scrollIntoView({ block, behavior: 'smooth' });
            });
        }
    }

    /* The whole head is a fold target so the chevron isn't the only way in;
       clicks on the title input (or the chevron itself, which already
       toggles) fall through to their own handlers. */
    function onTerminalRowHeadClick(event) {
        if (event.target.closest('input, button')) {
            return;
        }
        const button = event.currentTarget.querySelector('.t-row-fold-btn');
        if (button) {
            toggleTerminalRowFold(button);
        }
    }

    /* Panel fold state is a per-browser view preference, so it lives in
       localStorage (same pattern as the theme key in shared.js) and never in
       the saved-session payload. */
    const LAUNCHER_PANEL_FOLD_KEYS = {
        terminalSetupCard: 'gv_fold_terminal_setup',
        workspaceDestinationCard: 'gv_fold_workspaces'
    };

    /* No `terminal-setup-folded` marker on the column any more: its rows are
       content-sized whether or not a card is folded, so folding one is already
       just a shorter row. */
    function syncLauncherPanelFoldChrome(card, folded) {
        const button = card.querySelector('.card-fold-btn');
        if (button) {
            button.setAttribute('aria-expanded', folded ? 'false' : 'true');
        }
    }

    function toggleLauncherCardFold(cardId) {
        const card = document.getElementById(cardId);
        if (!card) {
            return;
        }
        const folded = card.classList.toggle('card-folded');
        syncLauncherPanelFoldChrome(card, folded);
        const storageKey = LAUNCHER_PANEL_FOLD_KEYS[cardId];
        if (storageKey) {
            try { localStorage.setItem(storageKey, folded ? '1' : '0'); } catch (_error) {}
        }
    }

    function restoreLauncherPanelFolds() {
        Object.entries(LAUNCHER_PANEL_FOLD_KEYS).forEach(([cardId, storageKey]) => {
            const card = document.getElementById(cardId);
            if (!card) {
                return;
            }
            let folded = false;
            try { folded = localStorage.getItem(storageKey) === '1'; } catch (_error) {}
            card.classList.toggle('card-folded', folded);
            syncLauncherPanelFoldChrome(card, folded);
        });
    }

    function collectFormConfig() {
        const modeInputs = collectModeInputs();
        const workspaceLayout = Array.isArray(activeWorkspaceLayout?.split_slot_rects)
            && activeWorkspaceLayout.split_slot_rects.length === selectedCount
            ? activeWorkspaceLayout
            : null;
        return {
            connection_mode: connectionMode,
            terminal_count: selectedCount,
            layout: selectedLayout,
            ssh: {
                host: modeInputs.ssh.host.trim(),
                username: modeInputs.ssh.username.trim() || 'ubuntu',
                password: modeInputs.ssh.password,
                port: Number(modeInputs.ssh.port) || 22,
                default_dir: modeInputs.ssh.default_dir.trim()
            },
            wsl: {
                distribution: modeInputs.wsl.distribution.trim(),
                username: modeInputs.wsl.username.trim(),
                default_dir: modeInputs.wsl.default_dir.trim()
            },
            terminals: collectTerminalDrafts(),
            workspace_layout: workspaceLayout
        };
    }

    function applySessionConfig(config) {
        const normalized = config || {};
        const count = COUNT_OPTIONS.includes(Number(normalized.terminal_count))
            ? Number(normalized.terminal_count)
            : selectedCount;

        connectionMode = normalized.connection_mode === 'wsl' ? 'wsl' : 'ssh';
        selectedCount = count;
        selectedLayout = LAYOUT_COPY[count]?.[normalized.layout]
            ? normalized.layout
            : defaultLayoutForCount(count);
        activeWorkspaceLayout = normalized.workspace_layout || null;
        layoutChooserOpen = false;

        document.querySelectorAll('.mode-btn').forEach(button => {
            button.classList.toggle('active', button.dataset.mode === connectionMode);
        });

        renderCountOptions();
        renderLayoutOptions();
        renderModeFields();

        applyModeInputs({
            ssh: normalized.ssh || {},
            wsl: normalized.wsl || {}
        });

        const defaultDir = getStep2DefaultDirectory(normalized, connectionMode);
        buildTerminalRows(
            selectedCount,
            normalizeTerminalsForDisplay(normalized.terminals || DEFAULT_TERMINALS, defaultDir, connectionMode)
        );
        updateTerminalTargetSignature(connectionMode, collectModeInputs());
    }

    function setActiveSavedSession(meta = null) {
        activeSavedSessionId = String(meta?.id || '').trim();
        activeSavedSessionName = String(meta?.name || '').trim();
        persistActiveSavedSessionMeta();
        if (typeof updateHeaderBadges === 'function') updateHeaderBadges();
    }

    function savedSessionUpdateToken(payload) {
        const sessionId = String(payload?.id || '').trim();
        if (!sessionId) {
            return '';
        }
        return [
            sessionId,
            String(payload?.updated_at || ''),
            String(payload?.nonce || payload?.timestamp || '')
        ].join(':');
    }

    async function refreshActiveSavedSessionFromUpdate(payload) {
        const sessionId = String(payload?.id || '').trim();
        const shouldActivate = Boolean(payload?.activate);
        if (!sessionId || (!shouldActivate && sessionId !== activeSavedSessionId)) {
            return;
        }

        const updateToken = savedSessionUpdateToken(payload);
        if (updateToken && updateToken === lastSavedSessionUpdateToken) {
            return;
        }
        lastSavedSessionUpdateToken = updateToken;

        try {
            const response = await fetch(`/api/saved-sessions/${encodeURIComponent(sessionId)}`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to refresh saved session');
            }

            setActiveSavedSession(data);
            applySessionConfig(data.config);
            showMessage(`Refreshed active session "${data.name}".`, 'success');
        } catch (error) {
            showMessage(`Refresh failed: ${error.message}`, 'error');
        }
    }

    function handleSavedSessionUpdateMessage(message) {
        refreshActiveSavedSessionFromUpdate(message);
    }

    function setupSavedSessionUpdateListeners() {
        if ('BroadcastChannel' in window) {
            try {
                savedSessionUpdateChannel = new BroadcastChannel(SAVED_SESSION_BROADCAST_CHANNEL);
                savedSessionUpdateChannel.onmessage = event => {
                    handleSavedSessionUpdateMessage(event.data || {});
                };
            } catch (_error) {
                savedSessionUpdateChannel = null;
            }
        }

        window.addEventListener('storage', event => {
            if (event.key !== SAVED_SESSION_UPDATE_STORAGE_KEY || !event.newValue) {
                return;
            }

            try {
                handleSavedSessionUpdateMessage(JSON.parse(event.newValue));
            } catch (_error) {}
        });
    }

    async function persistLastUsedConfig(savedSessionId = activeSavedSessionId) {
        const response = await fetch('/api/session-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                saved_session_id: String(savedSessionId || '').trim()
            })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to persist settings');
        }

        setActiveSavedSession(data.saved_session);
        return data;
    }

    async function saveCurrentConfig() {
        try {
            const payload = collectFormConfig();
            const suggestedName = buildDefaultSessionName();
            const result = await openSaveSessionNameModal(suggestedName);
            if (result === null) {
                return;
            }

            const sessionName = String(result.name || '').trim() || suggestedName;
            const shouldOverwriteActiveSession = Boolean(activeSavedSessionId) && (
                sessionName === activeSavedSessionName || sessionName === activeSavedSessionId
            );
            const response = await fetch('/api/saved-sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: shouldOverwriteActiveSession ? activeSavedSessionId : undefined,
                    name: sessionName,
                    config: payload
                })
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to save settings');
            }

            setActiveSavedSession(data.saved_session || data);
            showMessage(`Saved session "${data.name}".`, 'success');
            applySessionConfig(data.config);
        } catch (error) {
            showMessage(`Save failed: ${error.message}`, 'error');
        }
    }

    async function loadPersistedConfig(silent = false) {
        try {
            const response = await fetch('/api/session-config');
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to import settings');
            }

            setActiveSavedSession(data.saved_session);
            applySessionConfig(data);
            if (!silent) {
                showMessage('Imported startup session settings.', 'success');
            }
        } catch (error) {
            if (!silent) {
                showMessage(`Import failed: ${error.message}`, 'error');
            }
        }
    }

    function closeSaveSessionNameModal(result = null) {
        const modal = document.getElementById('saveSessionNameModal');
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');

        if (saveSessionNameResolver) {
            const resolver = saveSessionNameResolver;
            saveSessionNameResolver = null;
            resolver(result);
        }
    }

    // window.prompt is a no-op under pywebview's WebView2 backend, so session
    // naming has to go through this modal instead.
    function openSaveSessionNameModal(suggestedName) {
        const modal = document.getElementById('saveSessionNameModal');
        const nameInput = document.getElementById('saveSessionNameInput');
        nameInput.value = suggestedName || '';
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');

        window.setTimeout(() => {
            nameInput.focus();
            nameInput.select();
        }, 0);

        return new Promise(resolve => {
            saveSessionNameResolver = resolve;
        });
    }

    function closeSavedSessionModal(result = null) {
        const modal = document.getElementById('savedSessionsModal');
        const primaryAction = document.getElementById('savedSessionsPrimaryAction');
        const footerCopy = document.getElementById('savedSessionsFooterCopy');

        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');
        document.getElementById('savedSessionsList').innerHTML = '';
        primaryAction.hidden = true;
        primaryAction.disabled = false;
        primaryAction.textContent = '';
        primaryAction.onclick = null;
        footerCopy.textContent = '';
        savedSessionModalMode = 'import';

        if (savedSessionResolver) {
            const resolver = savedSessionResolver;
            savedSessionResolver = null;
            resolver(result);
        }
    }

    function openSavedSessionModal(sessions, mode = 'import') {
        const modal = document.getElementById('savedSessionsModal');
        const list = document.getElementById('savedSessionsList');
        const title = document.getElementById('savedSessionsTitle');
        const copy = title.nextElementSibling;
        const primaryAction = document.getElementById('savedSessionsPrimaryAction');
        const footerCopy = document.getElementById('savedSessionsFooterCopy');
        const isDeleteMode = mode === 'delete';

        savedSessionModalMode = mode;
        title.textContent = isDeleteMode ? 'Delete Saved Sessions' : 'Import Saved Session';
        copy.textContent = isDeleteMode
            ? 'Tick the saved sessions you want to remove. If you delete all of them, the launcher falls back to the built-in default.'
            : 'Pick one saved launcher setup to load into the form.';
        footerCopy.textContent = isDeleteMode
            ? 'Select one or more sessions to delete.'
            : '';
        list.innerHTML = sessions.map(session => buildSavedSessionCard(session, {
            selectable: isDeleteMode,
            currentSavedSessionId: activeSavedSessionId
        })).join('');

        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');

        return new Promise(resolve => {
            savedSessionResolver = resolve;
            if (isDeleteMode) {
                const syncSelectionState = () => {
                    const selectedIds = Array.from(list.querySelectorAll('.saved-session-checkbox:checked'))
                        .map(input => input.value);

                    list.querySelectorAll('.saved-session-selectable').forEach(item => {
                        const checkbox = item.querySelector('.saved-session-checkbox');
                        item.classList.toggle('selected', Boolean(checkbox?.checked));
                    });

                    primaryAction.disabled = selectedIds.length === 0;
                    footerCopy.textContent = selectedIds.length > 0
                        ? `${selectedIds.length} session${selectedIds.length === 1 ? '' : 's'} selected for deletion.`
                        : 'Select one or more sessions to delete.';
                };

                primaryAction.hidden = false;
                primaryAction.textContent = 'Delete Selected';
                primaryAction.disabled = true;
                primaryAction.onclick = () => {
                    const selectedIds = Array.from(list.querySelectorAll('.saved-session-checkbox:checked'))
                        .map(input => input.value);
                    closeSavedSessionModal({ ids: selectedIds });
                };

                list.querySelectorAll('.saved-session-checkbox').forEach(input => {
                    input.addEventListener('change', syncSelectionState);
                });
                syncSelectionState();
                return;
            }

            list.querySelectorAll('.saved-session-item').forEach(button => {
                button.addEventListener('click', () => closeSavedSessionModal({ id: button.dataset.sessionId }));
            });
        });
    }

    async function importSavedSession() {
        try {
            const listResponse = await fetch('/api/saved-sessions');
            const listData = await listResponse.json();
            if (!listResponse.ok) {
                throw new Error(listData.error || 'Failed to load saved sessions');
            }

            const importableSessions = [
                ...(listData.default_session ? [listData.default_session] : []),
                ...(Array.isArray(listData.sessions) ? listData.sessions : [])
            ];

            const selected = await openSavedSessionModal(importableSessions, 'import');
            const selectedId = selected?.id;
            if (!selectedId) {
                return;
            }

            const response = await fetch(`/api/saved-sessions/${encodeURIComponent(selectedId)}`);
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load saved session');
            }

            setActiveSavedSession(data);
            applySessionConfig(data.config);
            await persistLastUsedConfig(data.id);
            showMessage(`Imported session "${data.name}".`, 'success');
        } catch (error) {
            showMessage(`Import failed: ${error.message}`, 'error');
        }
    }

    async function deleteSavedSessions() {
        try {
            const listResponse = await fetch('/api/saved-sessions');
            const listData = await listResponse.json();
            if (!listResponse.ok) {
                throw new Error(listData.error || 'Failed to load saved sessions');
            }

            if (!listData.sessions || listData.sessions.length === 0) {
                showMessage('No saved sessions found yet.', 'error');
                return;
            }

            setActiveSavedSession(listData.saved_session);
            const selection = await openSavedSessionModal(listData.sessions, 'delete');
            const selectedIds = Array.isArray(selection?.ids) ? selection.ids.filter(Boolean) : [];
            if (!selectedIds.length) {
                return;
            }

            const response = await fetch('/api/saved-sessions', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ids: selectedIds })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to delete saved sessions');
            }

            setActiveSavedSession(data.saved_session);
            if (data.config) {
                applySessionConfig(data.config);
            }

            showMessage(`Deleted ${selectedIds.length} saved session${selectedIds.length === 1 ? '' : 's'}.`, 'success');
        } catch (error) {
            showMessage(`Delete failed: ${error.message}`, 'error');
        }
    }

    const RELEASES_URL = 'https://github.com/JSstudent/gridvibe/releases';

    async function checkForUpdates() {
        // Source-ZIP checkouts have no git remote to check; explain that
        // up front instead of hitting the endpoint just to surface a 400.
        if (installKind === 'source') {
            const confirmed = await openGenericConfirmModal({
                title: 'This copy cannot self-update',
                copy: 'This copy was extracted from a source archive, so it cannot update itself. Download the latest release, or clone the repository to enable in-app updates.',
                confirmLabel: 'Open Releases page'
            });
            if (confirmed) {
                window.open(RELEASES_URL, '_blank', 'noopener,noreferrer');
            }
            return;
        }

        const button = document.getElementById('checkUpdatesBtn');
        button.disabled = true;
        button.classList.add('loading');
        setUpdateStatus('Checking the git remote for new commits...');

        try {
            const response = await fetch('/api/app-update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Update check failed');
            }

            const updateSummary = data.message || (
                data.updated
                    ? `Updated ${data.branch || 'current branch'} to ${shortCommit(data.current_commit)}.`
                    : 'GridVibe is already up to date.'
            );

            if (data.updated && data.restart_required) {
                setUpdateStatus(`${updateSummary} Restarting GridVibe...`, 'success');
                showMessage(`${updateSummary} Restarting GridVibe...`, 'success');

                if (window.pywebview?.api?.restart_application) {
                    try {
                        const restartResult = await window.pywebview.api.restart_application();
                        if (restartResult?.ok) {
                            return;
                        }

                        const restartError = restartResult?.error || 'Automatic restart failed.';
                        button.disabled = false;
                        button.classList.remove('loading');
                        setUpdateStatus(`${updateSummary} ${restartError} Restart GridVibe manually.`, 'error');
                        showMessage(`${updateSummary} ${restartError} Restart GridVibe manually.`, 'error');
                        return;
                    } catch (error) {
                        button.disabled = false;
                        button.classList.remove('loading');
                        setUpdateStatus(`${updateSummary} ${error.message} Restart GridVibe manually.`, 'error');
                        showMessage(`${updateSummary} ${error.message} Restart GridVibe manually.`, 'error');
                        return;
                    }
                }

                button.disabled = false;
                button.classList.remove('loading');
                setUpdateStatus(`${updateSummary} Restart GridVibe to load the latest version.`, 'success');
                showMessage(`${updateSummary} Restart GridVibe to load the latest version.`, 'success');
                return;
            }

            button.disabled = false;
            button.classList.remove('loading');
            setUpdateStatus(updateSummary, 'success');
            showMessage(updateSummary, 'success');
        } catch (error) {
            button.disabled = false;
            button.classList.remove('loading');
            setUpdateStatus(error.message, 'error');
            showMessage(`Update failed: ${error.message}`, 'error');
        }
    }

    async function saveWorkspaceForRestart() {
        /* Best-effort workspace capture before a restart. A 409 means the
           workspace is empty (nothing live to save), which is not an error —
           the restart still proceeds. */
        try {
            const nativeZoomFactor = await getNativeSessionZoomFactor();
            const response = await fetch('/api/runtime-state/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ native_zoom_factor: nativeZoomFactor })
            });
            if (response.ok || response.status === 409) {
                return true;
            }
            const data = await response.json().catch(() => ({}));
            throw new Error(data.error || `Save failed with status ${response.status}`);
        } catch (error) {
            console.error('[GridVibe Launcher] workspace save before restart failed:', error);
            return false;
        }
    }

    async function restartApplication() {
        const button = document.getElementById('restartAppBtn');

        // In-page confirm, not window.confirm: the native WebView2 window
        // blocks the browser dialog, which made this button a silent no-op
        // in the desktop app (guardrail audit finding N1).
        const confirmed = await openGenericConfirmModal({
            title: 'Restart GridVibe?',
            copy: 'Save the workspace and restart GridVibe?',
            note: 'Live shells do not survive a restart.',
            confirmLabel: 'Save & Restart',
            danger: true
        });
        if (!confirmed) {
            return;
        }

        button.disabled = true;
        button.classList.add('loading');
        setUpdateStatus('Saving workspace...');

        const saved = await saveWorkspaceForRestart();
        const savePrefix = saved ? 'Workspace saved.' : 'Workspace save failed.';

        if (!window.pywebview?.api?.restart_application) {
            // Browser mode (or no native bridge): there is nothing to relaunch.
            button.disabled = false;
            button.classList.remove('loading');
            setUpdateStatus(`${savePrefix} Restart GridVibe manually to reload the app.`, saved ? 'success' : 'error');
            showMessage(`${savePrefix} Restart GridVibe manually to reload the app.`, saved ? 'success' : 'error');
            return;
        }

        setUpdateStatus(`${savePrefix} Restarting GridVibe...`, 'success');
        showMessage(`${savePrefix} Restarting GridVibe...`, 'success');

        try {
            const restartResult = await window.pywebview.api.restart_application();
            if (restartResult?.ok) {
                return;
            }
            const restartError = restartResult?.error || 'Automatic restart failed.';
            button.disabled = false;
            button.classList.remove('loading');
            setUpdateStatus(`${savePrefix} ${restartError} Restart GridVibe manually.`, 'error');
            showMessage(`${savePrefix} ${restartError} Restart GridVibe manually.`, 'error');
        } catch (error) {
            button.disabled = false;
            button.classList.remove('loading');
            setUpdateStatus(`${savePrefix} ${error.message} Restart GridVibe manually.`, 'error');
            showMessage(`${savePrefix} ${error.message} Restart GridVibe manually.`, 'error');
        }
    }

    function logLauncherWindowAction(action, details = {}) {
        console.info(`[GridVibe Launcher] ${action}`, details);
    }

    /* "The" active terminals only means something while there is one window to
       open. With the flag on, the Workspaces card lists every live workspace
       with its own Open button, so this button is hidden there rather than
       renamed after one of them — see syncLaunchDestinationControl(). It stays
       the single-workspace entry point, and the flag-off restore banner passes
       its own workspace id. */
    async function viewActiveTerminals(
        event,
        preferredGroupId = '',
        nativeZoomFactor = null,
        workspaceId = null
    ) {
        event.preventDefault();
        const normalizedZoomFactor = normalizeNativeZoomFactor(nativeZoomFactor);
        const resolvedWorkspaceId = String(
            workspaceId || WORKSPACE_DEFAULT_ID
        );
        logLauncherWindowAction('View Active Terminals clicked', {
            pywebview: Boolean(window.pywebview?.api)
        });

        /* A plain focus deliberately leaves the session window's URL alone, so
           it cannot honour a requested group — a restore goes straight to
           open_session_window, which retargets an existing window. A zoomed
           restore also needs that path so the native window receives its zoom. */
        if (!preferredGroupId && normalizedZoomFactor === null) {
            if (await focusWorkspaceWindow(resolvedWorkspaceId)) {
                logLauncherWindowAction('focused existing workspace window', {
                    workspace_id: resolvedWorkspaceId
                });
                return false;
            }
        }

        await openTerminalsIfActive(
            preferredGroupId,
            normalizedZoomFactor,
            resolvedWorkspaceId
        );
        return false;
    }

    async function openTerminalsIfActive(
        preferredGroupId = '',
        nativeZoomFactor = null,
        workspaceId = 'default'
    ) {
        const resolvedWorkspaceId = String(workspaceId || 'default');
        try {
            const resp = await fetch(
                `/api/sessions?workspace_id=${encodeURIComponent(resolvedWorkspaceId)}`
            );
            const data = await resp.json();
            logLauncherWindowAction('Fetched sessions before opening terminals', {
                count: Array.isArray(data.sessions) ? data.sessions.length : 0,
                groups: Array.isArray(data.sessions)
                    ? [...new Set(data.sessions.map(session => session.group_id).filter(Boolean))]
                    : []
            });
            if (!data.sessions || data.sessions.length === 0) {
                showMessage('No active sessions to display.', 'info');
                return;
            }

            /* A workspace restore names the group that was in front when the
               workspace was saved; otherwise fall back to the first live one. */
            const liveGroupIds = new Set(
                data.sessions.map(session => session.group_id).filter(Boolean)
            );
            const targetGroupId = liveGroupIds.has(preferredGroupId)
                ? preferredGroupId
                : (data.sessions.find(session => session.group_id)?.group_id || '');
            await openWorkspaceWindow(resolvedWorkspaceId, {
                groupId: targetGroupId,
                nativeZoomFactor
            });
            logLauncherWindowAction('opened workspace window', {
                workspace_id: resolvedWorkspaceId,
                requested_group_id: targetGroupId || 'all'
            });
        } catch {
            showMessage('Could not check active sessions.', 'error');
        }
    }

    /* ── Restore previous workspace (feature 10.5) ──
       The backend snapshots the workspace shape on an autosave timer and on
       Workspace ▸ Save Workspace; after a restart the launcher offers to replay
       the last saved snapshot through the normal launch path. The offer is
       permanent — Dismiss only hides it for this launcher session.
       Passwords are never persisted — restored SSH panes use key auth or fail
       into the error placeholder (which has a Retry button). */
    let restorableWorkspaceGroups = [];
    /* Snapshot id of the group that was in front when the workspace was saved.
       Restored groups are minted with fresh ids, so it is resolved to the newly
       created group by position during the replay below. */
    let restorableActiveGroupId = '';
    let restorableNativeZoomFactor = null;

    async function checkRestorableWorkspace() {
        /* With N workspaces a single banner stops being coherent — dismissing
           "the banner" would hide every saved workspace at once. The chooser
           takes over; the banner remains the single-workspace fallback so the
           flag off keeps today's behaviour exactly. */
        if (isMultiWorkspaceEnabled()) {
            await loadWorkspaceRestoreChooser({ autoOpen: true });
            return;
        }
        const banner = document.getElementById('restoreWorkspaceBanner');
        if (!banner) return;
        const response = await fetch('/api/runtime-state');
        const data = await response.json();
        if (!response.ok || !data.restorable || !Array.isArray(data.groups) || !data.groups.length) {
            return;
        }
        restorableWorkspaceGroups = data.groups;
        restorableActiveGroupId = String(data.active_group_id || '');
        restorableNativeZoomFactor = normalizeNativeZoomFactor(data.native_zoom_factor);
        const groupCount = data.groups.length;
        const paneCount = data.groups.reduce(
            (total, group) => total + (Array.isArray(group.sessions) ? group.sessions.length : 0),
            0
        );
        const text = document.getElementById('restoreWorkspaceText');
        if (text) {
            const label = String(data.label || '').trim();
            const savedAgo = formatWorkspaceSavedAgo(data.saved_at);
            const target = label ? `Restore ${label}` : 'Restore previous workspace';
            const when = savedAgo ? ` — saved ${savedAgo}` : '';
            text.textContent = `${target}${when}? ${groupCount} session${groupCount === 1 ? '' : 's'} (${paneCount} pane${paneCount === 1 ? '' : 's'}) will relaunch with the same layout. Live shells do not survive a restart.`;
        }
        banner.hidden = false;
    }

    /* Build the POST /api/sessions body for one restored group. When the group
       was launched from a still-existing *named* saved session, replay that
       preset's current config ("latest preset wins" — Save All Sessions then
       Save Workspace restores the edited state without re-importing). Otherwise
       replay the workspace snapshot verbatim. The blank built-in default is
       never treated as a preset — its config holds no real host/directory. */
    async function buildRestoreGroupBody(group) {
        const snapshotBody = {
            sessions: Array.isArray(group.sessions) ? group.sessions : [],
            connection_mode: group.connection_mode,
            layout: group.layout,
            workspace_layout: group.workspace_layout,
            session_name: group.name,
            saved_session_id: group.saved_session_id || '',
            // Replay the workspace verbatim: a cold post-restart agent probe
            // must not silently clear a command that was working.
            restore: true
        };

        const savedId = String(group.saved_session_id || '').trim();
        if (!savedId || savedId === DEFAULT_SESSION_ID) {
            return snapshotBody;
        }

        try {
            const response = await fetch(`/api/saved-sessions/${encodeURIComponent(savedId)}`);
            if (!response.ok) return snapshotBody;
            const preset = await response.json();
            const config = preset?.config;
            if (!config || !Array.isArray(config.terminals)) return snapshotBody;
            return {
                ...snapshotBody,
                sessions: buildSessionsFromConfig(config, config.terminal_count),
                connection_mode: config.connection_mode,
                layout: config.layout,
                workspace_layout: config.workspace_layout || null,
                session_name: preset.name || group.name
            };
        } catch (_error) {
            return snapshotBody;
        }
    }

    function dismissRestoreBanner() {
        /* Hide-only: the saved slot is preserved (single-workspace mode has no
           Delete); the next autosave or manual save overwrites it. */
        const banner = document.getElementById('restoreWorkspaceBanner');
        if (banner) banner.hidden = true;
        restorableWorkspaceGroups = [];
        restorableActiveGroupId = '';
        restorableNativeZoomFactor = null;
    }

    async function restorePreviousWorkspace() {
        if (!restorableWorkspaceGroups.length) return;
        const button = document.getElementById('restoreWorkspaceBtn');
        if (button) {
            button.disabled = true;
            button.textContent = 'Restoring…';
        }
        let restored = 0;
        /* Live id of the group that was in front when the workspace was saved,
           resolved as the replay creates it — the restore opens the workspace
           on this group instead of on whichever one ends up newest. */
        let activeGroupId = '';
        const nativeZoomFactor = restorableNativeZoomFactor;
        try {
            for (const group of restorableWorkspaceGroups) {
                const response = await fetch('/api/sessions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(await buildRestoreGroupBody(group))
                });
                if (!response.ok) continue;
                restored += 1;
                if (restorableActiveGroupId && group.group_id === restorableActiveGroupId) {
                    const created = await response.json().catch(() => ({}));
                    activeGroupId = String(created.group_id || '');
                }
            }
            document.getElementById('restoreWorkspaceBanner')?.setAttribute('hidden', '');
            restorableWorkspaceGroups = [];
            restorableActiveGroupId = '';
            restorableNativeZoomFactor = null;
            if (restored > 0) {
                showMessage(`Restored ${restored} session${restored === 1 ? '' : 's'} from the previous workspace.`, 'success');
                await viewActiveTerminals(
                    { preventDefault: () => {} },
                    activeGroupId,
                    nativeZoomFactor
                );
            } else {
                showMessage('Could not restore the previous workspace.', 'error');
            }
        } catch (error) {
            showMessage(`Workspace restore failed: ${error.message}`, 'error');
        } finally {
            if (button) {
                button.disabled = false;
                button.textContent = 'Restore';
            }
        }
    }

    /* ─────────────────────────────────────────────
       Multi-workspace: launch destination and the saved-workspace chooser.

       Every workspace API call and window dispatch goes through workspaces.js;
       this file keeps only form collection and launch orchestration.
    ───────────────────────────────────────────── */

    /* The destination is part of the Launch control itself (the split button),
       not a separate field the user has to connect to it: `workspaceDestination`
       is what the caret menu sets and what the CTA both names and launches into.
       `WORKSPACE_NEW_DESTINATION` means "a workspace this launch creates". */
    let workspaceDestination = '';
    let workspaceDestinationLabelDraft = '';
    let liveWorkspaceCache = [];
    let restorableWorkspaceSummaries = [];
    let workspaceRestoreInFlight = false;

    function findLiveWorkspace(workspaceId) {
        const index = liveWorkspaceCache
            .findIndex(workspace => workspace.workspace_id === workspaceId);
        return index === -1
            ? null
            : { workspace: liveWorkspaceCache[index], index };
    }

    /* An explicit choice always wins — including "New workspace", which is a
       real answer and not the absence of one (`workspaceDestination` is "" until
       the user picks). Without a choice: the single live workspace when exactly
       one exists, otherwise a new one. */
    function resolveWorkspaceDestination() {
        if (workspaceDestination === WORKSPACE_NEW_DESTINATION) {
            return WORKSPACE_NEW_DESTINATION;
        }
        if (workspaceDestination && findLiveWorkspace(workspaceDestination)) {
            return workspaceDestination;
        }
        /* Only workspaces that actually have tabs count here: the permanent
           empty "default" record must not make a single real workspace look
           like two and push the default answer to "New workspace". */
        const populated = liveWorkspaceCache
            .filter(workspace => (Number(workspace.group_count) || 0) > 0);
        if (populated.length === 1) {
            return populated[0].workspace_id;
        }
        return WORKSPACE_NEW_DESTINATION;
    }

    function workspaceDestinationName(workspaceId = resolveWorkspaceDestination()) {
        if (workspaceId === WORKSPACE_NEW_DESTINATION) {
            return workspaceDestinationLabelDraft || 'New workspace';
        }
        const found = findLiveWorkspace(workspaceId);
        return found ? workspaceDisplayLabel(found.workspace, found.index) : 'New workspace';
    }

    /* ── The workspace mode switch (card 04) ──
       Multiple workspaces changes what every launch does, so it is a switch on
       the launch surface rather than a checkbox in App Settings. The behaviour
       is unchanged: turning it off still confirms, closes every workspace but
       the main one, and reloads — all of that stays in workspaces.js. */
    const MULTI_WORKSPACE_STATE_COPY = Object.freeze({
        on: 'On — launches can go to their own window',
        off: 'Off — everything runs in one window'
    });
    let multiWorkspaceToggleInFlight = false;

    function syncMultiWorkspaceToggle(enabled = isMultiWorkspaceEnabled()) {
        const toggle = document.getElementById('multiWorkspaceToggle');
        if (!toggle) {
            return;
        }
        toggle.classList.toggle('is-on', Boolean(enabled));
        toggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
        const state = document.getElementById('multiWorkspaceToggleState');
        if (state) {
            state.textContent = enabled ? MULTI_WORKSPACE_STATE_COPY.on : MULTI_WORKSPACE_STATE_COPY.off;
        }
    }

    async function toggleMultiWorkspaceMode() {
        const toggle = document.getElementById('multiWorkspaceToggle');
        if (multiWorkspaceToggleInFlight) {
            return;
        }
        const next = !isMultiWorkspaceEnabled();
        multiWorkspaceToggleInFlight = true;
        /* Busy state is a class, never rewritten markup (guardrail 8). The
           switch moves straight away because a successful change reloads this
           page; only a declined confirm or a failure puts it back. */
        toggle?.classList.add('is-busy');
        syncMultiWorkspaceToggle(next);
        try {
            const applied = await setMultiWorkspaceEnabled(next);
            if (applied !== next) {
                syncMultiWorkspaceToggle(applied);
                showMessage('Multiple workspaces stays on.', '');
            }
        } catch (error) {
            syncMultiWorkspaceToggle();
            showMessage(`Could not change the workspace mode: ${error.message}`, 'error');
        } finally {
            multiWorkspaceToggleInFlight = false;
            toggle?.classList.remove('is-busy');
        }
    }

    /* The CTA carries the destination so there is nothing to link it to. With
       the flag off it stays exactly today's single-workspace button. */
    function syncLaunchDestinationControl() {
        const caret = document.getElementById('launchDestinationBtn');
        const destinationLabel = document.getElementById('launchDestinationLabel');
        const viewButton = document.getElementById('viewActiveTerminalsBtn');
        const enabled = isMultiWorkspaceEnabled();
        if (caret) {
            caret.hidden = !enabled;
            /* The split seam only exists when the caret does. */
            document.getElementById('launchSplit')?.classList.toggle('has-destination', enabled);
        }
        if (destinationLabel) {
            destinationLabel.hidden = !enabled;
            destinationLabel.textContent = enabled ? `into ${workspaceDestinationName()}` : '';
        }
        if (viewButton) {
            /* With the flag on this button could only ever name *one* of the
               live workspaces, right next to the Workspaces card that lists all
               of them with their own Open buttons — a second, worse copy of the
               same control. Hide it; card 04 is the entry point there. */
            viewButton.hidden = enabled;
        }
    }

    function setWorkspaceDestination(workspaceId, labelDraft = '') {
        workspaceDestination = workspaceId;
        workspaceDestinationLabelDraft = String(labelDraft || '').trim();
        syncLaunchDestinationControl();
    }

    async function chooseNewWorkspaceDestination() {
        const label = await openWorkspaceNameModal({
            title: 'Launch into a new workspace',
            copy: 'The name is shown in workspace pickers and on the saved snapshot. Leave it blank for an automatic name.',
            value: workspaceDestinationLabelDraft,
            confirmLabel: 'Use this name'
        });
        if (label === null) {
            return;
        }
        setWorkspaceDestination(WORKSPACE_NEW_DESTINATION, label);
    }

    function toggleLaunchDestinationMenu(event) {
        event.preventDefault();
        const entries = liveWorkspaceCache.map((workspace, index) => {
            const count = Number(workspace.group_count) || 0;
            const isCurrent = workspace.workspace_id === resolveWorkspaceDestination();
            return {
                label: `${workspaceDisplayLabel(workspace, index)} — ${count} session${count === 1 ? '' : 's'}`,
                current: isCurrent,
                icon: isCurrent ? '' : WORKSPACE_ICONS.window,
                onSelect: () => setWorkspaceDestination(workspace.workspace_id)
            };
        });
        entries.push({
            label: 'New workspace ...',
            icon: WORKSPACE_ICONS.add,
            current: resolveWorkspaceDestination() === WORKSPACE_NEW_DESTINATION
                && liveWorkspaceCache.length > 0,
            onSelect: () => chooseNewWorkspaceDestination()
        });
        openWorkspaceContextMenu(event, entries);
    }

    async function refreshWorkspaceDestinations() {
        if (!isMultiWorkspaceEnabled()) {
            return;
        }

        /* A workspace is only "live" for launch purposes once it has tabs — a
           just-created empty one is still a valid destination, so both are
           listed and only the default differs. */
        liveWorkspaceCache = await fetchLiveWorkspaces();
        syncLaunchDestinationControl();

        const list = document.getElementById('workspaceLiveList');
        const empty = document.getElementById('workspaceLiveEmpty');
        if (!list) {
            return;
        }
        const openWorkspaces = liveWorkspaceCache
            .filter(workspace => (Number(workspace.group_count) || 0) > 0);
        if (empty) {
            empty.hidden = openWorkspaces.length > 0;
        }
        list.innerHTML = '';
        openWorkspaces.forEach(workspace => {
            const index = liveWorkspaceCache.indexOf(workspace);
            const row = document.createElement('div');
            row.className = 'workspace-live-row';
            const count = Number(workspace.group_count) || 0;
            const name = document.createElement('div');
            name.className = 'workspace-live-name';
            name.innerHTML = `${WORKSPACE_ICONS.window}<span>${escHtml(workspaceDisplayLabel(workspace, index))}</span>`;
            const meta = document.createElement('span');
            meta.className = 'workspace-live-meta';
            meta.textContent = `${count} session${count === 1 ? '' : 's'}`;
            const openButton = document.createElement('button');
            openButton.type = 'button';
            openButton.className = 'ghost-btn';
            openButton.textContent = 'Open';
            openButton.addEventListener('click', async () => {
                if (!(await focusWorkspaceWindow(workspace.workspace_id))) {
                    await openWorkspaceWindow(workspace.workspace_id, {
                        groupId: workspace.active_group_id
                    });
                }
            });
            row.append(name, meta, openButton);
            list.appendChild(row);
        });
    }

    /* The destination fields POST /api/sessions expects, or {} with the flag
       off so a single-workspace launch keeps targeting "default". */
    function collectWorkspaceDestination() {
        if (!isMultiWorkspaceEnabled()) {
            return {};
        }
        const choice = resolveWorkspaceDestination();
        if (choice === WORKSPACE_NEW_DESTINATION) {
            return {
                new_workspace: true,
                workspace_label: workspaceDestinationLabelDraft
            };
        }
        return { workspace_id: choice };
    }

    /* ── Saved-workspace chooser (Restore / Forget / Dismiss) ── */

    function updateWorkspaceSavedEntry() {
        const entry = document.getElementById('workspaceSavedEntry');
        const label = document.getElementById('workspaceSavedEntryLabel');
        if (!entry || !label) {
            return;
        }
        entry.hidden = !isMultiWorkspaceEnabled() || restorableWorkspaceSummaries.length === 0;
        /* Named for what it does, not for what it lists: the panel above it is
           the *live* workspaces, so "Saved workspaces" alone read as a duplicate
           of that list rather than as the way back into the restore chooser. */
        label.textContent = `Reopen saved… (${restorableWorkspaceSummaries.length})`;
    }

    function renderWorkspaceRestoreRows() {
        const list = document.getElementById('workspaceRestoreList');
        if (!list) {
            return;
        }
        list.innerHTML = '';
        restorableWorkspaceSummaries.forEach(summary => {
            const row = document.createElement('div');
            row.className = `workspace-restore-row${summary.live_conflict ? ' is-disabled' : ''}`;
            row.dataset.workspaceId = summary.workspace_id;

            const label = document.createElement('label');
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'workspace-restore-checkbox';
            checkbox.value = summary.workspace_id;
            checkbox.disabled = Boolean(summary.live_conflict);
            checkbox.checked = !summary.live_conflict;

            const text = document.createElement('span');
            text.className = 'workspace-restore-text';
            const name = document.createElement('span');
            name.className = 'workspace-restore-name';
            name.textContent = String(summary.label || '').trim() || 'Workspace';
            const meta = document.createElement('span');
            meta.className = 'workspace-restore-meta';
            meta.textContent = describeRestorableWorkspace(summary);
            text.append(name, meta);
            if (summary.live_conflict) {
                const reason = document.createElement('span');
                reason.className = 'workspace-restore-reason';
                reason.textContent = 'Already open — close this workspace first';
                text.appendChild(reason);
            }
            label.append(checkbox, text);

            const forget = document.createElement('button');
            forget.type = 'button';
            forget.className = 'ghost-btn danger-btn';
            forget.textContent = 'Forget';
            forget.disabled = Boolean(summary.live_conflict);
            forget.title = summary.live_conflict
                ? 'Close this workspace first'
                : 'Delete this saved workspace snapshot';
            forget.addEventListener('click', () => forgetWorkspaceRow(summary));

            row.append(label, forget);
            list.appendChild(row);
        });
    }

    /* A dialog, not an inline panel: the chooser is as tall as the number of
       saved workspaces, and inline it reflowed (and squeezed) the whole
       launcher grid behind it. */
    function setWorkspaceRestoreModalVisible(visible) {
        const modal = document.getElementById('workspaceRestoreModal');
        if (!modal) {
            return;
        }
        modal.classList.toggle('visible', visible);
        modal.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }

    function isWorkspaceRestoreModalVisible() {
        return Boolean(document.getElementById('workspaceRestoreModal')?.classList.contains('visible'));
    }

    async function loadWorkspaceRestoreChooser({ autoOpen = false } = {}) {
        if (!document.getElementById('workspaceRestoreModal')) {
            return;
        }
        restorableWorkspaceSummaries = await fetchRestorableWorkspaces();
        updateWorkspaceSavedEntry();
        renderWorkspaceRestoreRows();
        if (!restorableWorkspaceSummaries.length) {
            setWorkspaceRestoreModalVisible(false);
            return;
        }
        if (autoOpen) {
            setWorkspaceRestoreModalVisible(true);
        }
    }

    function openWorkspaceRestorePanel() {
        setWorkspaceRestoreModalVisible(true);
        loadWorkspaceRestoreChooser().catch(() => {});
    }

    /* Dialog-level "not now": every slot survives on disk and the chooser
       reopens from the Reopen saved… entry in the Workspaces card. */
    function dismissWorkspaceRestorePanel() {
        setWorkspaceRestoreModalVisible(false);
    }

    async function forgetWorkspaceRow(summary) {
        const confirmed = await openGenericConfirmModal({
            title: 'Forget this saved workspace?',
            copy: `"${String(summary.label || 'Workspace')}" will no longer be offered after a restart.`,
            note: 'Forget removes this saved workspace snapshot. Your saved sessions are not affected.',
            confirmLabel: 'Forget',
            danger: true
        });
        if (!confirmed) {
            return;
        }
        try {
            await forgetSavedWorkspace(summary.workspace_id);
        } catch (error) {
            showMessage(error.message, 'error');
        }
        await loadWorkspaceRestoreChooser();
        if (!restorableWorkspaceSummaries.length) {
            dismissWorkspaceRestorePanel();
        }
    }

    async function restoreSelectedWorkspaces() {
        const panel = document.querySelector('#workspaceRestoreModal .workspace-restore-card');
        if (!panel || workspaceRestoreInFlight) {
            return;
        }
        const workspaceIds = [...panel.querySelectorAll('.workspace-restore-checkbox')]
            .filter(checkbox => checkbox.checked && !checkbox.disabled)
            .map(checkbox => checkbox.value);
        if (!workspaceIds.length) {
            showMessage('Select at least one workspace to restore.', 'info');
            return;
        }

        /* Single-flight in the UI; the endpoint is also idempotent by workspace
           id, so a double click can never duplicate a workspace's tabs. */
        workspaceRestoreInFlight = true;
        panel.classList.add('busy');
        let restoreStarted = false;
        try {
            const result = await restoreSavedWorkspaces(workspaceIds);
            const restored = (result.workspaces || []).filter(entry => entry.restored);
            restoreStarted = restored.length > 0;
            for (const entry of restored) {
                // Only workspaces whose relaunch actually started get a window.
                await openWorkspaceWindow(entry.workspace_id, {
                    groupId: entry.active_group_id,
                    nativeZoomFactor: entry.native_zoom_factor
                });
            }
            const failed = (result.workspaces || []).filter(entry => !entry.restored);
            if (restored.length) {
                showMessage(
                    `Relaunch started for ${restored.length} workspace${restored.length === 1 ? '' : 's'}.`
                    + (failed.length ? ` ${failed.length} could not be restored.` : ''),
                    failed.length ? 'warning' : 'success'
                );
            } else {
                showMessage('Could not restore the selected workspaces.', 'error');
            }
        } catch (error) {
            showMessage(`Workspace restore failed: ${error.message} — try again.`, 'error');
        } finally {
            workspaceRestoreInFlight = false;
            panel.classList.remove('busy');
            await loadWorkspaceRestoreChooser();
            await refreshWorkspaceDestinations();
            /* A restored row stays listed (now marked already open), so the
               dialog would sit over the launcher after the windows opened.
               Close it once anything started; leave it up if nothing did, so
               the failure and its retry are still in front of the user. */
            if (!restorableWorkspaceSummaries.length || restoreStarted) {
                dismissWorkspaceRestorePanel();
            }
        }
    }

    /* Expand one launcher config — the live form state or a saved preset — into
       the per-pane `sessions` array that POST /api/sessions expects. Shared by
       the Launch button and workspace restore so both build panes identically;
       restore reuses it to replay a group's *current* saved preset. */
    function buildSessionsFromConfig(config, count) {
        const sessions = [];
        const configuredDefaultDir = getStep2DefaultDirectory(config, connectionMode);
        const launchDefaultDir = configuredDefaultDir || (config.connection_mode === 'ssh' ? '/' : '');

        (Array.isArray(config.terminals) ? config.terminals : []).slice(0, count).forEach((terminal, index) => {
            const startupMode = resolvePaneStartupMode(terminal);
            const {
                use_wsl: resolvedUseWsl,
                use_powershell: resolvedUsePowershell,
                ...paneLaunchFields
            } = buildPaneLaunchFields(terminal, startupMode);
            const resolvedDirectory = buildLaunchDirectory(
                configuredDefaultDir,
                terminal.directory,
                config.connection_mode
            ) || launchDefaultDir;

            const common = {
                title: terminal.title || `Terminal ${index + 1}`,
                directory: resolvedDirectory,
                ...paneLaunchFields
            };

            if (config.connection_mode === 'ssh') {
                sessions.push({
                    ...common,
                    host: config.ssh.host,
                    username: config.ssh.username || 'ubuntu',
                    password: config.ssh.password || null,
                    port: config.ssh.port || 22
                });
                return;
            }

            sessions.push({
                ...common,
                distribution: terminal.distribution || config.wsl.distribution || '',
                username: config.wsl.username || '',
                use_wsl: resolvedUseWsl,
                use_powershell: resolvedUsePowershell
            });
        });

        return sessions;
    }

    async function launchSessions() {
        const config = collectFormConfig();
        const button = document.getElementById('launchBtn');
        const sessionName = buildDefaultSessionName();

        if (config.connection_mode === 'ssh' && !config.ssh.host) {
            showMessage('Enter an SSH host before launching.', 'error');
            return;
        }

        if (config.connection_mode === 'wsl' && !config.wsl.default_dir) {
            showMessage('Select a local repository folder before launching.', 'error');
            return;
        }

        let sessions;
        try {
            sessions = buildSessionsFromConfig(config, selectedCount);
        } catch (error) {
            showMessage(error.message, 'error');
            return;
        }

        setLaunchButtonLoading(button, true);

        try {
            const response = await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_mode: config.connection_mode,
                    layout: config.layout,
                    workspace_layout: config.workspace_layout,
                    saved_session_id: activeSavedSessionId,
                    session_name: sessionName,
                    ...collectWorkspaceDestination(),
                    sessions
                })
            });

            const data = await response.json();
            if (response.status === 409 && data.conflict === 'saved_session_live') {
                /* Plan §6: a saved preset is live in at most one workspace.
                   Explain it and offer the two real resolutions instead of
                   silently stealing the tab (guardrail 8). */
                setLaunchButtonLoading(button, false);
                const openIt = await openGenericConfirmModal({
                    title: 'Already open elsewhere',
                    copy: `"${String(data.workspace_label || 'Another workspace')}" already has this saved session open.`,
                    note: 'Open that workspace, or cancel and pick a different saved session.',
                    confirmLabel: 'Open it'
                });
                if (openIt) {
                    await openWorkspaceWindow(data.workspace_id, { groupId: data.group_id });
                }
                return;
            }
            if (!response.ok) {
                throw new Error(data.error || 'Failed to create sessions');
            }
            /* Point the CTA at what was just launched, so a follow-up launch
               defaults to the same window instead of silently making another. */
            if (data.workspace_id) {
                setWorkspaceDestination(String(data.workspace_id));
            }
            await refreshWorkspaceDestinations();

            const launchWarnings = Array.isArray(data.warnings)
                ? data.warnings.filter(item => String(item || '').trim())
                : [];
            const launchMessage = launchWarnings.length
                ? `Launching ${data.count} ${getConnectionModeLabel(config.connection_mode)} terminals. ${launchWarnings.length === 1 ? launchWarnings[0] : `${launchWarnings.length} startup commands were cleared after preflight failed.`}`
                : `Launching ${data.count} ${getConnectionModeLabel(config.connection_mode)} terminals.`;
            showMessage(launchMessage, launchWarnings.length ? 'warning' : 'success');
            if (data.launch_target === 'web') {
                setTimeout(async () => {
                    const workspaceId = String(data.workspace_id || 'default');
                    logLauncherWindowAction('open workspace window after launch', {
                        workspace_id: workspaceId,
                        requested_group_id: data.group_id
                    });
                    await openWorkspaceWindow(workspaceId, { groupId: data.group_id });
                    setLaunchButtonLoading(button, false);
                }, 450);
            }
        } catch (error) {
            setLaunchButtonLoading(button, false);
            showMessage(`Launch failed: ${error.message}`, 'error');
        }
    }

    /* Toggle the launch CTA's loading state via classes instead of rewriting
       the button's markup, so the label/arrow structure survives a launch. */
    function setLaunchButtonLoading(button, loading) {
        button.disabled = loading;
        button.classList.toggle('loading', loading);
        const label = button.querySelector('.action-btn-label');
        if (label) {
            label.textContent = loading ? 'Launching…' : 'Launch Workspace';
        }
    }

    /* The workspace lists are live state owned by the terminal windows, so they
       must not go stale here while the app runs: opening or closing a tab,
       moving one, or renaming a workspace all invalidate this launcher's view.
       Terminal windows announce those changes (workspaces.js) and the launcher
       re-reads both lists — no polling, and no reliance on a page reload. */
    onWorkspacesChanged(() => {
        if (!isMultiWorkspaceEnabled()) {
            return;
        }
        refreshWorkspaceDestinations().catch(() => {});
        loadWorkspaceRestoreChooser({
            autoOpen: isWorkspaceRestoreModalVisible()
        }).catch(() => {});
    });

    /* The launcher has no Socket.IO connection, so App Settings changes made in
       a workspace window reach it through the same broadcast pair. Only the
       multi-workspace mode changes this page's markup. */
    function setupLauncherAppConfigListeners() {
        const handle = message => {
            const enabled = message?.workspace?.multi_workspace_enabled;
            if (typeof enabled === 'boolean' && enabled !== isMultiWorkspaceEnabled()) {
                reactToMultiWorkspaceFlagChange(enabled).catch(() => {});
            }
        };
        try {
            const channel = new BroadcastChannel(APP_CONFIG_BROADCAST_CHANNEL);
            channel.onmessage = event => {
                /* Never react to this window's own save: it is already applying
                   the change itself, and reloading here would cut off the
                   workspace teardown that has to finish first. */
                if (!isOwnBroadcast(event.data)) {
                    handle(event.data || {});
                }
            };
        } catch (_error) {}
        window.addEventListener('storage', event => {
            if (event.key !== APP_CONFIG_UPDATE_STORAGE_KEY || !event.newValue) {
                return;
            }
            try {
                handle(JSON.parse(event.newValue));
            } catch (_error) {}
        });
    }

    setupLauncherAppConfigListeners();
    syncMultiWorkspaceToggle();
    syncLaunchDestinationControl();

    restoreActiveSavedSessionMeta();
    setupSavedSessionUpdateListeners();
    renderCountOptions();
    renderLayoutOptions();
    renderModeFields();
    buildTerminalRows(selectedCount, DEFAULT_TERMINALS);
    restoreLauncherPanelFolds();
    updateTerminalTargetSignature(connectionMode, collectModeInputs());
    loadPersistedConfig(true);
    loadAppSettings().catch(() => {});
    loadVoicePrefs().catch(() => {});
    checkRestorableWorkspace().catch(() => {});
    refreshWorkspaceDestinations().catch(() => {});
    updateHeaderBadges();

    function updateHeaderBadges() {
        const modeBadge = document.getElementById('headerModeBadge');
        const sessionBadge = document.getElementById('headerSessionName');
        if (modeBadge) {
            modeBadge.textContent = connectionMode === 'ssh' ? 'SSH Remote' : 'Local Repo';
        }
        if (sessionBadge) {
            sessionBadge.textContent = activeSavedSessionName || 'Current Session';
        }
    }

    function addTerminalFromButton() {
        /* COUNT_OPTIONS is sorted ascending, so the next offered count is
           simply the first entry above the current one — and the ceiling is
           the ladder's top rather than MAX_SESSIONS. The two are not the same
           number: MAX_SESSIONS is the backend's per-group limit (16 by
           default) while the launcher only has layout presets up to 8, so
           quoting it announced "Maximum 16 terminals allowed" from a button
           that stops adding at 8. */
        const nextValid = COUNT_OPTIONS.find(count => count > selectedCount);
        if (!nextValid) {
            showMessage(`Maximum ${LAUNCHER_MAX_TERMINALS} terminals allowed.`, 'error');
            return;
        }
        const drafts = collectTerminalDrafts();
        selectedCount = nextValid;
        selectedLayout = defaultLayoutForCount(selectedCount);
        layoutChooserOpen = false;
        renderCountOptions();
        renderLayoutOptions();
        buildTerminalRows(selectedCount, drafts);
    }

    document.getElementById('savedSessionsModal').addEventListener('click', event => {
        if (event.target.id === 'savedSessionsModal') {
            closeSavedSessionModal();
        }
    });

    document.getElementById('saveSessionNameModal').addEventListener('click', event => {
        if (event.target.id === 'saveSessionNameModal') {
            closeSaveSessionNameModal();
        }
    });

    document.getElementById('saveSessionNameForm').addEventListener('submit', event => {
        event.preventDefault();
        closeSaveSessionNameModal({ name: document.getElementById('saveSessionNameInput').value });
    });

    document.getElementById('workspaceRestoreModal')?.addEventListener('click', event => {
        /* Clicking the backdrop is "not now", never a restore or a forget. */
        if (event.target.id === 'workspaceRestoreModal' && !workspaceRestoreInFlight) {
            dismissWorkspaceRestorePanel();
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && document.getElementById('savedSessionsModal').classList.contains('visible')) {
            closeSavedSessionModal();
        }
        if (event.key === 'Escape' && document.getElementById('saveSessionNameModal').classList.contains('visible')) {
            closeSaveSessionNameModal();
        }
        if (event.key === 'Escape' && isWorkspaceRestoreModalVisible() && !workspaceRestoreInFlight) {
            dismissWorkspaceRestorePanel();
        }
    });
