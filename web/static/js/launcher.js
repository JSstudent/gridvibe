    /* ── Theme management ── */

    async function shutdownBrowserApp() {
        if (!BROWSER_SHUTDOWN_TOKEN) {
            return;
        }
        if (!window.confirm('Close GridVibe and end the browser server?')) {
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
            window.alert(error.message || 'GridVibe could not be closed.');
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

    initTheme();

    window.name = 'gridvibe-launcher';
    const MAX_SESSIONS = Number(document.querySelector('.shell').dataset.maxSessions || 4);
    const COUNT_OPTIONS = [1, 2, 3, 4, 6, 8].filter(value => value <= MAX_SESSIONS);
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
        distribution: '',
        use_wsl: false,
        use_powershell: false
    }));
    const DEFAULT_APP_SETTINGS = Object.freeze({
        appearance: Object.freeze({
            theme: 'system'
        }),
        workspace: Object.freeze({
            surface_mode: 'normal'
        }),
        voice_input: Object.freeze({
            enabled: true,
            engine: 'vosk',
            vosk_model: 'vosk-model-en-us-0.22',
            whisper_model: 'base',
            whisper_device: 'cpu',
            whisper_compute_type: 'int8',
            language: 'en-US'
        })
    });
    const DEFAULT_VOICE_PREFS = Object.freeze({
        profile: 'laptop',
        deviceId: '',
        pttEnabled: false,
        pttKeybind: ''
    });
    const WHISPER_MODEL_OPTIONS = Object.freeze([
        'tiny.en',
        'tiny',
        'base.en',
        'base',
        'small.en',
        'small',
        'medium.en',
        'medium',
        'large-v1',
        'large-v2',
        'large-v3',
        'large',
        'distil-large-v2',
        'distil-medium.en',
        'distil-small.en',
        'distil-large-v3',
        'distil-large-v3.5',
        'large-v3-turbo',
        'turbo'
    ]);

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
    let appSettings = JSON.parse(JSON.stringify(DEFAULT_APP_SETTINGS));
    let voicePrefs = { ...DEFAULT_VOICE_PREFS };
    let launcherMicDevices = [];
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

    function getGridMetrics(count) {
        if (count >= 8) {
            return { columns: 4, rows: 2 };
        }
        if (count >= 6) {
            return { columns: 3, rows: 2 };
        }
        return { columns: 2, rows: 2 };
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


    function getStep2DefaultDirectory(config, modeOverride = '') {
        const mode = modeOverride || config?.connection_mode || connectionMode;
        if (mode === 'wsl') {
            return String(config?.wsl?.default_dir || '').trim();
        }
        return String(config?.ssh?.default_dir || '').trim();
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

    function setUpdateStatus(text, type = '') {
        const status = document.getElementById('updateStatus');
        const quickStatus = document.getElementById('quickUpdateStatus');
        if (!status) {
            return;
        }

        status.textContent = text;
        status.className = `toolbar-status ${type}`.trim();
        if (quickStatus) {
            quickStatus.textContent = text;
            quickStatus.className = `inline-status ${type}`.trim();
        }
    }

    function applyAppSettings(data) {
        const appearance = data?.appearance || {};
        const workspace = data?.workspace || {};
        const voiceInput = data?.voice_input || {};
        appSettings = {
            appearance: {
                ...DEFAULT_APP_SETTINGS.appearance,
                ...appearance
            },
            workspace: {
                ...DEFAULT_APP_SETTINGS.workspace,
                ...workspace
            },
            voice_input: {
                ...DEFAULT_APP_SETTINGS.voice_input,
                ...voiceInput
            }
        };
        applyTheme(appSettings.appearance.theme);
        syncAppSettingsForm();
    }

    function updateAppSettingsVisibility() {
        const enabledInput = document.getElementById('appVoiceEnabled');
        const engineInput = document.getElementById('appVoiceEngine');
        const whisperDeviceInput = document.getElementById('appWhisperDevice');
        const modalCard = document.querySelector('#appSettingsModal .app-settings-card');
        const voiceDetails = document.getElementById('appVoiceSettingsDetails');
        const voskSection = document.getElementById('appVoskSettings');
        const whisperSection = document.getElementById('appWhisperSettings');
        const micSection = document.getElementById('appMicSettings');
        const whisperGpuHint = document.getElementById('appWhisperGpuHint');
        const voiceEnabled = Boolean(enabledInput?.checked);
        const selectedEngine = engineInput?.value || 'vosk';
        const selectedDevice = whisperDeviceInput?.value || 'cpu';
        modalCard?.classList.toggle('voice-enabled', voiceEnabled);
        voiceDetails?.classList.toggle('hidden', !voiceEnabled);
        voskSection?.classList.toggle('hidden', !voiceEnabled || selectedEngine !== 'vosk');
        whisperSection?.classList.toggle('hidden', !voiceEnabled || selectedEngine !== 'whisper');
        micSection?.classList.toggle('hidden', !voiceEnabled);
        whisperGpuHint?.classList.toggle('hidden', !voiceEnabled || selectedEngine !== 'whisper' || selectedDevice !== 'cuda');
    }

    function syncAppSettingsForm() {
        const appearance = appSettings.appearance || DEFAULT_APP_SETTINGS.appearance;
        const workspace = appSettings.workspace || DEFAULT_APP_SETTINGS.workspace;
        const voice = appSettings.voice_input || DEFAULT_APP_SETTINGS.voice_input;
        const themeInput = document.getElementById('appTheme');
        const surfaceModeInput = document.getElementById('appSurfaceMode');
        const enabledInput = document.getElementById('appVoiceEnabled');
        const engineInput = document.getElementById('appVoiceEngine');
        const languageInput = document.getElementById('appVoiceLanguage');
        const voskModelInput = document.getElementById('appVoskModel');
        const whisperModelInput = document.getElementById('appWhisperModel');
        const whisperDeviceInput = document.getElementById('appWhisperDevice');
        const whisperComputeInput = document.getElementById('appWhisperComputeType');

        if (themeInput) themeInput.value = appearance.theme || DEFAULT_APP_SETTINGS.appearance.theme;
        if (surfaceModeInput) surfaceModeInput.value = workspace.surface_mode === 'max' ? 'max' : 'normal';
        if (enabledInput) enabledInput.checked = Boolean(voice.enabled);
        if (engineInput) engineInput.value = voice.engine === 'whisper' ? 'whisper' : 'vosk';
        if (languageInput) languageInput.value = voice.language || DEFAULT_APP_SETTINGS.voice_input.language;
        if (voskModelInput) voskModelInput.value = voice.vosk_model || DEFAULT_APP_SETTINGS.voice_input.vosk_model;
        if (whisperModelInput) {
            const model = WHISPER_MODEL_OPTIONS.includes(voice.whisper_model)
                ? voice.whisper_model
                : DEFAULT_APP_SETTINGS.voice_input.whisper_model;
            whisperModelInput.value = model;
        }
        if (whisperDeviceInput) whisperDeviceInput.value = voice.whisper_device === 'cuda' ? 'cuda' : 'cpu';
        if (whisperComputeInput) whisperComputeInput.value = voice.whisper_compute_type || DEFAULT_APP_SETTINGS.voice_input.whisper_compute_type;

        updateAppSettingsVisibility();
    }

    function normalizeVoicePrefs(data = {}) {
        return {
            profile: data.profile === 'headset' ? 'headset' : DEFAULT_VOICE_PREFS.profile,
            deviceId: typeof data.deviceId === 'string' ? data.deviceId : '',
            pttEnabled: typeof data.pttEnabled === 'boolean' ? data.pttEnabled : DEFAULT_VOICE_PREFS.pttEnabled,
            pttKeybind: typeof data.pttKeybind === 'string' ? data.pttKeybind : ''
        };
    }

    function setVoicePrefsStatus(text, type = '') {
        const status = document.getElementById('appVoicePrefsStatus');
        if (!status) return;
        status.textContent = text;
        status.className = `settings-hint ${type}`.trim();
    }

    function voiceDeviceLabel(device, index) {
        return device.label || `Microphone ${index + 1}`;
    }

    function syncVoicePrefsForm() {
        const profileInput = document.getElementById('appVoiceProfile');
        const deviceInput = document.getElementById('appVoiceDevice');
        const pttEnabledInput = document.getElementById('appVoicePttEnabled');
        const pttKeybindInput = document.getElementById('appVoicePttKeybind');

        if (profileInput) profileInput.value = voicePrefs.profile;
        if (pttEnabledInput) pttEnabledInput.checked = Boolean(voicePrefs.pttEnabled);
        if (pttKeybindInput) pttKeybindInput.value = voicePrefs.pttKeybind || '';

        if (deviceInput) {
            const selectedDeviceId = voicePrefs.deviceId || '';
            deviceInput.innerHTML = '';
            const defaultOption = document.createElement('option');
            defaultOption.value = '';
            defaultOption.textContent = 'Default input';
            deviceInput.appendChild(defaultOption);
            launcherMicDevices.forEach((device, index) => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = voiceDeviceLabel(device, index);
                deviceInput.appendChild(option);
            });
            if (
                selectedDeviceId &&
                !launcherMicDevices.some(device => device.deviceId === selectedDeviceId)
            ) {
                const missingOption = document.createElement('option');
                missingOption.value = selectedDeviceId;
                missingOption.textContent = 'Previously selected device (unavailable)';
                deviceInput.appendChild(missingOption);
            }
            deviceInput.value = selectedDeviceId;
        }
    }

    function collectVoicePrefsForm() {
        return {
            profile: document.getElementById('appVoiceProfile')?.value || DEFAULT_VOICE_PREFS.profile,
            deviceId: document.getElementById('appVoiceDevice')?.value || '',
            pttEnabled: Boolean(document.getElementById('appVoicePttEnabled')?.checked),
            pttKeybind: document.getElementById('appVoicePttKeybind')?.value.trim() || ''
        };
    }

    async function loadVoicePrefs() {
        const response = await fetch('/api/voice-prefs');
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to load microphone settings');
        }
        voicePrefs = normalizeVoicePrefs(data);
        syncVoicePrefsForm();
        return voicePrefs;
    }

    async function saveVoicePrefs() {
        const response = await fetch('/api/voice-prefs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(collectVoicePrefsForm())
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to save microphone settings');
        }
        voicePrefs = normalizeVoicePrefs(data);
        syncVoicePrefsForm();
        return voicePrefs;
    }

    async function refreshLauncherMicrophones() {
        if (!navigator.mediaDevices?.enumerateDevices) {
            setVoicePrefsStatus('This browser does not expose microphone selection.');
            return;
        }

        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            launcherMicDevices = devices.filter(device => device.kind === 'audioinput');
            syncVoicePrefsForm();
            if (launcherMicDevices.length === 0) {
                setVoicePrefsStatus('No audio input devices are currently available.');
                return;
            }
            const hasNamedDevices = launcherMicDevices.some(device => Boolean(device.label));
            setVoicePrefsStatus(
                hasNamedDevices
                    ? `Microphones refreshed. ${launcherMicDevices.length} input device${launcherMicDevices.length === 1 ? '' : 's'} available.`
                    : 'Microphones loaded. Labels may stay generic until microphone permission is granted.'
            );
        } catch (error) {
            setVoicePrefsStatus(`Unable to refresh microphones: ${error.message || error}`);
        }
    }

    function formatAppPttKeybind(event) {
        const parts = [];
        if (event.ctrlKey) parts.push('Ctrl');
        if (event.metaKey) parts.push('Cmd');
        if (event.altKey) parts.push('Alt');
        if (event.shiftKey) parts.push('Shift');
        const key = event.key;
        if (!['Control', 'Meta', 'Alt', 'Shift'].includes(key)) {
            parts.push(key.length === 1 ? key.toUpperCase() : key);
        }
        return parts.join('+');
    }

    function isValidAppPttKeybind(event) {
        const hasCommandKey = event.ctrlKey || event.metaKey;
        const isModifierOnly = ['Control', 'Meta', 'Alt', 'Shift'].includes(event.key);
        return hasCommandKey && !isModifierOnly;
    }

    function collectAppSettingsForm() {
        return {
            appearance: {
                theme: document.getElementById('appTheme')?.value || DEFAULT_APP_SETTINGS.appearance.theme
            },
            workspace: {
                surface_mode: document.getElementById('appSurfaceMode')?.value === 'max' ? 'max' : 'normal'
            },
            voice_input: {
                enabled: Boolean(document.getElementById('appVoiceEnabled')?.checked),
                engine: document.getElementById('appVoiceEngine')?.value || DEFAULT_APP_SETTINGS.voice_input.engine,
                vosk_model: document.getElementById('appVoskModel')?.value.trim() || DEFAULT_APP_SETTINGS.voice_input.vosk_model,
                whisper_model: document.getElementById('appWhisperModel')?.value || DEFAULT_APP_SETTINGS.voice_input.whisper_model,
                whisper_device: document.getElementById('appWhisperDevice')?.value || DEFAULT_APP_SETTINGS.voice_input.whisper_device,
                whisper_compute_type: document.getElementById('appWhisperComputeType')?.value || DEFAULT_APP_SETTINGS.voice_input.whisper_compute_type,
                language: document.getElementById('appVoiceLanguage')?.value.trim() || DEFAULT_APP_SETTINGS.voice_input.language
            }
        };
    }

    function notifyAppConfigUpdated(appSettings) {
        const payload = {
            appearance: {
                theme: normalizeThemePreference(appSettings?.appearance?.theme)
            },
            workspace: {
                surface_mode: appSettings?.workspace?.surface_mode === 'max' ? 'max' : 'normal'
            },
            timestamp: Date.now(),
            nonce: Math.random().toString(36).slice(2)
        };

        try {
            const channel = new BroadcastChannel(APP_CONFIG_BROADCAST_CHANNEL);
            channel.postMessage(payload);
            channel.close();
        } catch (_error) {}

        try {
            localStorage.setItem(APP_CONFIG_UPDATE_STORAGE_KEY, JSON.stringify(payload));
        } catch (_error) {}
    }

    async function loadAppSettings() {
        const response = await fetch('/api/app-config');
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to load app settings');
        }
        applyAppSettings(data);
        return data;
    }

    function closeAppSettingsModal() {
        const modal = document.getElementById('appSettingsModal');
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');
    }

    async function openAppSettings() {
        try {
            await Promise.all([
                loadAppSettings(),
                loadVoicePrefs()
            ]);
            refreshLauncherMicrophones().catch(() => {});
            const modal = document.getElementById('appSettingsModal');
            modal.classList.add('visible');
            modal.setAttribute('aria-hidden', 'false');
        } catch (error) {
            showMessage(`Could not load app settings: ${error.message}`, 'error');
        }
    }

    async function saveAppSettings() {
        const button = document.getElementById('saveAppSettingsBtn');
        button.disabled = true;
        button.textContent = 'Saving...';

        try {
            const [settingsResponse] = await Promise.all([
                fetch('/api/app-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(collectAppSettingsForm())
                }),
                saveVoicePrefs()
            ]);
            const data = await settingsResponse.json();
            if (!settingsResponse.ok) {
                throw new Error(data.error || 'Failed to save app settings');
            }

            applyAppSettings(data);
            notifyAppConfigUpdated(data);
            closeAppSettingsModal();
            setUpdateStatus('App settings saved.', 'success');
            showMessage('App settings saved.', 'success');
        } catch (error) {
            showMessage(`Settings save failed: ${error.message}`, 'error');
        } finally {
            button.disabled = false;
            button.textContent = 'Save Settings';
        }
    }

    function shortCommit(value) {
        return String(value || '').trim().slice(0, 7);
    }

    function getKnownAgentValues() {
        return AGENT_OPTIONS.filter(option => option.value !== 'other').map(option => option.value);
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
                customAgent
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
                commandValue: initialCommand || 'http://127.0.0.1:3000',
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

    function renderAgentOptions(selectedValue) {
        const normalizedValue = String(selectedValue || '').trim().toLowerCase();
        const options = [
            '<option value="" data-base-label="Select agent">Select agent</option>',
            ...AGENT_OPTIONS.map(option => `
                <option
                    value="${escHtml(option.value)}"
                    data-base-label="${escHtml(option.label)}"
                    ${normalizedValue === option.value ? 'selected' : ''}
                >${escHtml(option.label)}</option>
            `)
        ];
        return options.join('');
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
            const selectedAgent = row.querySelector('.t-agent-select')?.value.trim() || '';
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

    function collectTerminalDrafts() {
        const rows = Array.from(document.querySelectorAll('.t-row'));
        if (!rows.length) {
            return DEFAULT_TERMINALS.map(item => ({ ...item }));
        }

        const drafts = rows.map((row, index) => {
            const commandMode = getTerminalCommandMode(row);
            return {
                title: row.querySelector('.t-title')?.value.trim() || `Terminal ${index + 1}`,
                directory: row.querySelector('.t-dir').value.trim(),
                initial_command: buildTerminalInitialCommand(row),
                initial_command_mode: commandMode === 'agent'
                    ? 'agent'
                    : (commandMode === 'explorer' || commandMode === 'browser' ? commandMode : 'command'),
                startup_mode: commandMode === 'agent'
                    ? 'agent'
                    : (commandMode === 'explorer' || commandMode === 'browser' ? commandMode : 'terminal'),
                agent_selection: commandMode === 'agent'
                    ? (row.querySelector('.t-agent-select')?.value.trim() || '')
                    : '',
                custom_agent: commandMode === 'agent'
                    ? (row.querySelector('.t-agent-custom')?.value.trim() || '')
                    : '',
                explorer_tree_open: commandMode === 'explorer' && row.dataset.explorerTreeOpen === 'true',
                explorer_git_open: commandMode === 'explorer' && row.dataset.explorerGitOpen === 'true',
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
            const gridMetrics = option.preview === 'grid'
                ? getGridMetrics(selectedCount)
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
        const selectedAgent = row.querySelector('.t-agent-select')?.value.trim() || '';

        if (startupModeSelect && startupModeSelect.value !== commandMode) {
            startupModeSelect.value = commandMode;
        }

        commandField?.classList.toggle('hidden', commandMode !== 'command');
        agentField?.classList.toggle('hidden', commandMode !== 'agent');
        customAgentField?.classList.toggle('hidden', !(commandMode === 'agent' && selectedAgent === 'other'));
        browserField?.classList.toggle('hidden', commandMode !== 'browser');
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

    function resetTerminalCommandOnModeChange(row, nextMode) {
        const previousMode = getTerminalCommandMode(row);
        const commandInput = row.querySelector('.t-cmd');
        const agentSelect = row.querySelector('.t-agent-select');
        const customAgentInput = row.querySelector('.t-agent-custom');

        if (previousMode === 'command' && nextMode !== 'command' && commandInput) {
            commandInput.value = '';
        }

        if (previousMode === 'agent' && nextMode !== 'agent') {
            if (agentSelect) agentSelect.value = '';
            if (customAgentInput) customAgentInput.value = '';
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
        const select = row?.querySelector('.t-agent-select');
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
        const select = row?.querySelector('.t-agent-select');
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
        if (selectedOption && selectedOption.value) {
            selectedOption.textContent = `${selectedOption.dataset.baseLabel || selectedOption.textContent} · ${label}`;
        }

        summary.className = `agent-preflight-summary ${escHtml(status)}`.trim();
        summaryLabel.textContent = label;
        copy.innerHTML = lines.join('<br>');
        disclosure.classList.add('visible');
        disclosure.open = wasOpen;
    }

    function buildAgentPreflightPayload(row) {
        return {
            agent: row?.querySelector('.t-agent-select')?.value.trim() || '',
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

        const selectedAgent = row.querySelector('.t-agent-select')?.value.trim() || '';
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
            const agentSelect = row.querySelector('.t-agent-select');
            const distributionInput = row.querySelector('.t-distribution');
            const startupModeSelect = row.querySelector('.startup-mode-select');

            startupModeSelect?.addEventListener('change', () => {
                const nextMode = startupModeSelect.value;
                resetTerminalCommandOnModeChange(row, nextMode);
                row.dataset.commandMode = nextMode;
                syncTerminalCommandState(row);
                scheduleAgentPreflight(row, 60);
            });
            agentSelect?.addEventListener('change', () => {
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
        const columns = count >= 3 ? 3 : count;

        container.style.setProperty('--terminal-columns', String(columns));

        container.innerHTML = Array.from({ length: count }, (_, index) => {
            const terminal = usableDrafts[index] || DEFAULT_TERMINALS[index];
            const commandUi = normalizeTerminalCommandUi(terminal);
            if (connectionMode !== 'wsl' && commandUi.mode === 'browser') {
                commandUi.mode = 'terminal';
                commandUi.commandValue = '';
            }
            return `
                <div
                    class="t-row"
                    data-command-mode="${escHtml(commandUi.mode)}"
                    data-explorer-tree-open="${terminal.explorer_tree_open ? 'true' : 'false'}"
                    data-explorer-git-open="${terminal.explorer_git_open ? 'true' : 'false'}"
                >
                    <div class="t-row-head">
                        <span class="t-badge">T${index + 1}</span>
                        <input class="t-title" type="text" value="${escHtml(terminal.title || `Terminal ${index + 1}`)}" placeholder="Terminal ${index + 1}" aria-label="Terminal ${index + 1} title">
                        <span class="t-status-dot"></span>
                    </div>
                    <div class="t-fields">
                        <div class="field">
                            <label>Subdirectory</label>
                            <input class="t-dir" type="text" value="${escHtml(terminal.directory || '')}" placeholder="Optional path inside Step 2 default">
                        </div>
                        <div class="field">
                            <label>Startup Mode</label>
                            <select class="startup-mode-select">
                                <option value="terminal" ${commandUi.mode === 'terminal' ? 'selected' : ''}>Terminal</option>
                                <option value="command" ${commandUi.mode === 'command' ? 'selected' : ''}>Initial Command</option>
                                <option value="agent" ${commandUi.mode === 'agent' ? 'selected' : ''}>Agent</option>
                                <option value="explorer" ${commandUi.mode === 'explorer' ? 'selected' : ''}>File Explorer</option>
                                <option value="browser" ${commandUi.mode === 'browser' ? 'selected' : ''} ${connectionMode === 'wsl' ? '' : 'disabled'}>Browser</option>
                            </select>
                        </div>
                        <div class="field t-command-field ${commandUi.mode === 'command' ? '' : 'hidden'}">
                            <label>Initial Command</label>
                            <input class="t-cmd" type="text" value="${escHtml(commandUi.commandValue)}" placeholder="Blank = shell only">
                        </div>
                        <div class="field t-browser-field ${commandUi.mode === 'browser' ? '' : 'hidden'}">
                            <label>Browser URL</label>
                            <input class="t-browser-url" type="url" value="${escHtml(commandUi.commandValue || 'http://127.0.0.1:3000')}" placeholder="http://127.0.0.1:3000">
                        </div>
                        <div class="field t-agent-field ${commandUi.mode === 'agent' ? '' : 'hidden'}">
                            <label>Agent</label>
                            <select class="t-agent-select">
                                ${renderAgentOptions(commandUi.agentSelection)}
                            </select>
                            <details class="agent-preflight-disclosure">
                                <summary class="agent-preflight-summary">
                                    <span class="agent-preflight-summary-label"></span>
                                </summary>
                                <div class="agent-preflight-copy"></div>
                            </details>
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

    // window.prompt() is a no-op under pywebview's WebView2 backend, so session
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

    async function checkForUpdates() {
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

    function logLauncherWindowAction(action, details = {}) {
        console.info(`[GridVibe Launcher] ${action}`, details);
    }

    async function viewActiveTerminals(event) {
        event.preventDefault();
        logLauncherWindowAction('View Active Terminals clicked', {
            pywebview: Boolean(window.pywebview?.api)
        });

        if (window.pywebview?.api?.focus_session_window) {
            try {
                const result = await window.pywebview.api.focus_session_window();
                logLauncherWindowAction('focus_session_window result', result || {});
                if (result?.ok) {
                    return false;
                }
            } catch (error) {
                console.error('[GridVibe Launcher] focus_session_window failed:', error);
            }
        }

        await openTerminalsIfActive();
        return false;
    }

    async function openTerminalsIfActive() {
        try {
            const resp = await fetch('/api/sessions');
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

            const firstGroupId = data.sessions.find(session => session.group_id)?.group_id || '';
            if (window.pywebview?.api?.open_session_window) {
                try {
                    const result = await window.pywebview.api.open_session_window(firstGroupId);
                    logLauncherWindowAction('open_session_window result', {
                        requested_group_id: firstGroupId || 'all',
                        ...(result || {})
                    });
                    if (result?.ok) {
                        return;
                    }
                } catch (error) {
                    console.error('[GridVibe Launcher] open_session_window failed:', error);
                }
            }

            window.open('/terminals', 'gridvibe-sessions');
            logLauncherWindowAction('Opened browser terminals window fallback', {
                requested_group_id: firstGroupId || 'all'
            });
        } catch {
            showMessage('Could not check active sessions.', 'error');
        }
    }

    async function launchSessions() {
        const config = collectFormConfig();
        const button = document.getElementById('launchBtn');
        const sessions = [];
        const sessionName = buildDefaultSessionName();

        if (config.connection_mode === 'ssh' && !config.ssh.host) {
            showMessage('Enter an SSH host before launching.', 'error');
            return;
        }

        if (config.connection_mode === 'wsl' && !config.wsl.default_dir) {
            showMessage('Select a local repository folder before launching.', 'error');
            return;
        }

        const configuredDefaultDir = getStep2DefaultDirectory(config);
        const launchDefaultDir = configuredDefaultDir || (config.connection_mode === 'ssh' ? '/' : '');

        try {
            config.terminals.slice(0, selectedCount).forEach((terminal, index) => {
                const resolvedDirectory = buildLaunchDirectory(
                    configuredDefaultDir,
                    terminal.directory,
                    config.connection_mode
                ) || launchDefaultDir;

                const common = {
                    title: terminal.title || `Terminal ${index + 1}`,
                    directory: resolvedDirectory,
                    initial_command: terminal.startup_mode === 'explorer' ? null : (terminal.initial_command || null),
                    initial_command_mode: terminal.startup_mode === 'explorer' || terminal.startup_mode === 'browser'
                        ? terminal.startup_mode
                        : (terminal.initial_command_mode === 'agent' ? 'agent' : 'command'),
                    agent_selection: terminal.initial_command_mode === 'agent'
                        ? (terminal.agent_selection || '')
                        : '',
                    custom_agent: terminal.initial_command_mode === 'agent'
                        ? (terminal.custom_agent || '')
                        : '',
                    explorer_tree_open: terminal.startup_mode === 'explorer'
                        ? Boolean(terminal.explorer_tree_open)
                        : false,
                    explorer_git_open: terminal.startup_mode === 'explorer'
                        ? Boolean(terminal.explorer_git_open)
                        : false,
                    startup_mode: terminal.startup_mode === 'explorer' || terminal.startup_mode === 'browser'
                        ? terminal.startup_mode
                        : (terminal.initial_command_mode === 'agent' ? 'agent' : 'terminal')
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
                    use_wsl: ['explorer', 'browser'].includes(common.startup_mode) ? false : Boolean(terminal.use_wsl),
                    use_powershell: ['explorer', 'browser'].includes(common.startup_mode) ? false : Boolean(terminal.use_powershell)
                });
            });
        } catch (error) {
            showMessage(error.message, 'error');
            return;
        }

        const originalButtonHtml = button.innerHTML;
        button.disabled = true;
        button.textContent = 'Launching...';

        try {
            const response = await fetch('/api/sessions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_mode: config.connection_mode,
                    layout: config.layout,
                    workspace_layout: config.workspace_layout,
                    surface_mode: appSettings.workspace?.surface_mode === 'max' ? 'max' : 'normal',
                    saved_session_id: activeSavedSessionId,
                    session_name: sessionName,
                    sessions
                })
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to create sessions');
            }

            const launchWarnings = Array.isArray(data.warnings)
                ? data.warnings.filter(item => String(item || '').trim())
                : [];
            const launchMessage = launchWarnings.length
                ? `Launching ${data.count} ${getConnectionModeLabel(config.connection_mode)} terminals. ${launchWarnings.length === 1 ? launchWarnings[0] : `${launchWarnings.length} startup commands were cleared after preflight failed.`}`
                : `Launching ${data.count} ${getConnectionModeLabel(config.connection_mode)} terminals.`;
            showMessage(launchMessage, launchWarnings.length ? 'warning' : 'success');
            if (data.launch_target === 'web') {
                setTimeout(async () => {
                    const targetUrl = `/terminals?group=${encodeURIComponent(data.group_id)}`;
                    if (window.pywebview?.api?.open_session_window) {
                        try {
                            const result = await window.pywebview.api.open_session_window(data.group_id);
                            logLauncherWindowAction('open_session_window after launch', {
                                requested_group_id: data.group_id,
                                ...(result || {})
                            });
                            if (!result?.ok) {
                                window.open(targetUrl, 'gridvibe-sessions');
                            }
                        } catch (error) {
                            console.error('[GridVibe Launcher] launch window open failed:', error);
                            window.open(targetUrl, 'gridvibe-sessions');
                        }
                    } else {
                        window.open(targetUrl, 'gridvibe-sessions');
                    }
                    button.disabled = false;
                    button.innerHTML = originalButtonHtml;
                }, 450);
            }
        } catch (error) {
            button.disabled = false;
            button.innerHTML = originalButtonHtml;
            showMessage(`Launch failed: ${error.message}`, 'error');
        }
    }

    restoreActiveSavedSessionMeta();
    setupSavedSessionUpdateListeners();
    renderCountOptions();
    renderLayoutOptions();
    renderModeFields();
    buildTerminalRows(selectedCount, DEFAULT_TERMINALS);
    updateTerminalTargetSignature(connectionMode, collectModeInputs());
    loadPersistedConfig(true);
    loadAppSettings().catch(() => {});
    loadVoicePrefs().catch(() => {});
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
        if (selectedCount >= MAX_SESSIONS) {
            showMessage(`Maximum ${MAX_SESSIONS} terminals allowed.`, 'error');
            return;
        }
        const drafts = collectTerminalDrafts();
        const nextCount = selectedCount + 1;
        if (COUNT_OPTIONS.includes(nextCount)) {
            selectedCount = nextCount;
        } else {
            const nextValid = COUNT_OPTIONS.find(n => n > selectedCount);
            if (nextValid) {
                selectedCount = nextValid;
            } else {
                showMessage(`Maximum ${MAX_SESSIONS} terminals allowed.`, 'error');
                return;
            }
        }
        selectedLayout = defaultLayoutForCount(selectedCount);
        layoutChooserOpen = false;
        renderCountOptions();
        renderLayoutOptions();
        buildTerminalRows(selectedCount, drafts);
    }

    document.getElementById('appVoiceProfile')?.addEventListener('change', event => {
        voicePrefs.profile = event.target.value === 'headset' ? 'headset' : 'laptop';
    });
    document.getElementById('appVoiceDevice')?.addEventListener('change', event => {
        voicePrefs.deviceId = event.target.value || '';
    });
    document.getElementById('appVoicePttEnabled')?.addEventListener('change', event => {
        voicePrefs.pttEnabled = Boolean(event.target.checked);
    });
    document.getElementById('appVoicePttKeybind')?.addEventListener('keydown', event => {
        event.preventDefault();
        event.stopPropagation();
        const input = event.currentTarget;
        if (event.key === 'Backspace' || event.key === 'Delete' || event.key === 'Escape') {
            input.value = '';
            voicePrefs.pttKeybind = '';
            return;
        }
        const formatted = formatAppPttKeybind(event);
        if (isValidAppPttKeybind(event)) {
            input.value = formatted;
            voicePrefs.pttKeybind = formatted;
        } else {
            input.value = formatted ? `${formatted}...` : '';
        }
    });
    if (navigator.mediaDevices?.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', () => {
            refreshLauncherMicrophones().catch(() => {});
        });
    }

    document.getElementById('savedSessionsModal').addEventListener('click', event => {
        if (event.target.id === 'savedSessionsModal') {
            closeSavedSessionModal();
        }
    });

    document.getElementById('appSettingsModal').addEventListener('click', event => {
        if (event.target.id === 'appSettingsModal') {
            closeAppSettingsModal();
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

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && document.getElementById('savedSessionsModal').classList.contains('visible')) {
            closeSavedSessionModal();
        }
        if (event.key === 'Escape' && document.getElementById('appSettingsModal').classList.contains('visible')) {
            closeAppSettingsModal();
        }
        if (event.key === 'Escape' && document.getElementById('saveSessionNameModal').classList.contains('visible')) {
            closeSaveSessionNameModal();
        }
    });
