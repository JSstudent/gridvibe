    /* GridVibe App Settings dialog — shared by the launcher and the session
       workspace. Both pages include templates/partials/app_settings_modal.html
       and this script (after shared.js, before the page script), so machine
       settings can be changed from either window without bouncing through the
       launcher. Declarations resolve as globals, like shared.js.

       Pages customize it by declaring optional hook functions (looked up by
       name at call time, so declaring them after this file loads is fine):
         - appSettingsNotify(message, type)   — page message surface
         - onAppSettingsApplied(data)         — after /api/app-config is applied
         - onAppSettingsSaved(data, payload)  — after a save; `payload` is the
           broadcast message, which the saving window never receives itself
           (BroadcastChannel and storage events skip their own sender), so a
           page that reacts to app-config changes applies it from here. */

    const DEFAULT_APP_SETTINGS = Object.freeze({
        appearance: Object.freeze({
            theme: 'system'
        }),
        workspace: Object.freeze({
            surface_mode: 'normal',
            autosave_interval_minutes: 5,
            multi_workspace_enabled: false
        }),
        ssh: Object.freeze({
            host_key_policy: 'auto-add'
        }),
        terminal: Object.freeze({
            font_family: "Consolas, Monaco, 'Courier New', monospace",
            font_size: 14,
            max_sessions: 4
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

    let appSettings = JSON.parse(JSON.stringify(DEFAULT_APP_SETTINGS));
    let voicePrefs = { ...DEFAULT_VOICE_PREFS };
    let appMicDevices = [];

    function notifyAppSettings(text, type = '') {
        if (typeof appSettingsNotify === 'function') {
            appSettingsNotify(text, type);
        }
    }

    function applyAppSettings(data) {
        const appearance = data?.appearance || {};
        const workspace = data?.workspace || {};
        const ssh = data?.ssh || {};
        const terminal = data?.terminal || {};
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
            ssh: {
                ...DEFAULT_APP_SETTINGS.ssh,
                ...ssh
            },
            terminal: {
                ...DEFAULT_APP_SETTINGS.terminal,
                ...terminal
            },
            voice_input: {
                ...DEFAULT_APP_SETTINGS.voice_input,
                ...voiceInput
            }
        };
        applyTheme(appSettings.appearance.theme);
        if (typeof onAppSettingsApplied === 'function') {
            onAppSettingsApplied(data);
        }
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
        /* OD-13: the free-text font input only shows for the "Custom…" preset. */
        const fontPresetInput = document.getElementById('appTerminalFontPreset');
        const fontCustomField = document.getElementById('appTerminalFontCustomField');
        fontCustomField?.classList.toggle('hidden', fontPresetInput?.value !== 'custom');
        /* Availability follows the engine picked here, not only the saved one. */
        syncVoiceAvailability();
    }

    function syncAppSettingsForm() {
        const appearance = appSettings.appearance || DEFAULT_APP_SETTINGS.appearance;
        const workspace = appSettings.workspace || DEFAULT_APP_SETTINGS.workspace;
        const ssh = appSettings.ssh || DEFAULT_APP_SETTINGS.ssh;
        const terminal = appSettings.terminal || DEFAULT_APP_SETTINGS.terminal;
        const voice = appSettings.voice_input || DEFAULT_APP_SETTINGS.voice_input;
        const themeInput = document.getElementById('appTheme');
        const surfaceModeInput = document.getElementById('appSurfaceMode');
        const sshHostKeyPolicyInput = document.getElementById('appSshHostKeyPolicy');
        const terminalFontFamilyInput = document.getElementById('appTerminalFontFamily');
        const terminalFontSizeInput = document.getElementById('appTerminalFontSize');
        const terminalMaxSessionsInput = document.getElementById('appTerminalMaxSessions');
        const enabledInput = document.getElementById('appVoiceEnabled');
        const engineInput = document.getElementById('appVoiceEngine');
        const languageInput = document.getElementById('appVoiceLanguage');
        const voskModelInput = document.getElementById('appVoskModel');
        const whisperModelInput = document.getElementById('appWhisperModel');
        const whisperDeviceInput = document.getElementById('appWhisperDevice');
        const whisperComputeInput = document.getElementById('appWhisperComputeType');

        if (themeInput) themeInput.value = appearance.theme || DEFAULT_APP_SETTINGS.appearance.theme;
        if (surfaceModeInput) surfaceModeInput.value = workspace.surface_mode === 'max' ? 'max' : 'normal';
        const autosaveIntervalInput = document.getElementById('appWorkspaceAutosaveInterval');
        if (autosaveIntervalInput) {
            const interval = Number(workspace.autosave_interval_minutes);
            autosaveIntervalInput.value = String(
                Number.isFinite(interval)
                    ? Math.min(15, Math.max(1, Math.round(interval)))
                    : DEFAULT_APP_SETTINGS.workspace.autosave_interval_minutes
            );
            syncAutosaveIntervalLabel();
        }
        if (sshHostKeyPolicyInput) {
            sshHostKeyPolicyInput.value = ['auto-add', 'known-hosts', 'strict'].includes(ssh.host_key_policy)
                ? ssh.host_key_policy
                : DEFAULT_APP_SETTINGS.ssh.host_key_policy;
        }
        if (terminalFontFamilyInput) {
            terminalFontFamilyInput.value = String(terminal.font_family || DEFAULT_APP_SETTINGS.terminal.font_family);
        }
        const terminalFontPresetInput = document.getElementById('appTerminalFontPreset');
        if (terminalFontPresetInput) {
            /* A saved stack that matches a preset selects it; anything else is
               shown through the "Custom…" free-text escape hatch (OD-13). */
            const family = String(terminal.font_family || DEFAULT_APP_SETTINGS.terminal.font_family);
            const isPreset = Array.from(terminalFontPresetInput.options)
                .some(option => option.value !== 'custom' && option.value === family);
            terminalFontPresetInput.value = isPreset ? family : 'custom';
        }
        const terminalApplyAllInput = document.getElementById('appTerminalApplyAll');
        if (terminalApplyAllInput) {
            /* One-shot scope modifier (OD-14), never a persisted setting. */
            terminalApplyAllInput.checked = false;
        }
        if (terminalFontSizeInput) {
            const fontSize = Number(terminal.font_size);
            terminalFontSizeInput.value = Number.isFinite(fontSize)
                ? String(fontSize)
                : String(DEFAULT_APP_SETTINGS.terminal.font_size);
        }
        if (terminalMaxSessionsInput) {
            const maxSessions = Number(terminal.max_sessions);
            terminalMaxSessionsInput.value = Number.isFinite(maxSessions)
                ? String(maxSessions)
                : String(DEFAULT_APP_SETTINGS.terminal.max_sessions);
        }
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

    /* Voice backend availability (stage J issue 2): App Settings used to let
       voice input be switched on with none of the optional packages present,
       and the mic button in the workspace then did nothing at all. The dialog
       now reads /api/voice-status, explains what is missing for the engine
       selected in this form, and can install the packages in place. */
    let voiceStatus = null;
    let voiceInstallPolling = false;
    const VOICE_INSTALL_POLL_MS = 2000;

    function selectedVoiceEngine() {
        return document.getElementById('appVoiceEngine')?.value
            || voiceStatus?.engine
            || DEFAULT_APP_SETTINGS.voice_input.engine;
    }

    function voiceEngineAvailable(engine = selectedVoiceEngine()) {
        const engines = voiceStatus?.engines_available;
        /* Unknown (status never loaded, or an older server): never block on a
           probe we could not run. */
        return typeof engines?.[engine] === 'boolean' ? engines[engine] : true;
    }

    function voiceUnavailableMessage(engine = selectedVoiceEngine()) {
        if (voiceStatus?.engine === engine && voiceStatus?.engine_available === false && voiceStatus?.status_message) {
            return voiceStatus.status_message;
        }
        return engine === 'whisper'
            ? 'faster-whisper and numpy are not installed, so voice input cannot start.'
            : 'The vosk and websockets packages are not installed, so voice input cannot start.';
    }

    function setVoiceAvailabilityMessage(text) {
        const message = document.getElementById('appVoiceAvailabilityMessage');
        if (message) {
            message.textContent = text;
        }
    }

    function setVoiceInstallBusy(busy) {
        const button = document.getElementById('installVoiceDepsBtn');
        if (!button) {
            return;
        }
        button.disabled = busy;
        button.classList.toggle('loading', busy);
    }

    function syncVoiceAvailability() {
        const banner = document.getElementById('appVoiceAvailability');
        if (!banner) {
            return;
        }
        if (voiceInstallPolling) {
            banner.classList.remove('hidden');
            return;
        }
        /* Only relevant once voice input is switched on — an off feature with
           uninstalled packages is not a problem to report. */
        const voiceEnabled = Boolean(document.getElementById('appVoiceEnabled')?.checked);
        const available = !voiceEnabled || voiceEngineAvailable();
        banner.classList.toggle('hidden', available);
        if (!available) {
            setVoiceAvailabilityMessage(`${voiceUnavailableMessage()} Install them here — GridVibe loads them without a restart.`);
        }
    }

    async function loadVoiceStatus() {
        const response = await fetch('/api/voice-status');
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || 'Failed to load voice status');
        }
        voiceStatus = data;
        syncVoiceAvailability();
        if (data.install?.status === 'running') {
            watchVoiceInstall();
        }
        return data;
    }

    function voiceInstallDelay() {
        return new Promise(resolve => window.setTimeout(resolve, VOICE_INSTALL_POLL_MS));
    }

    /* pip runs in a worker thread server-side, so progress is polled at a
       human interval (guardrail: no sub-second polling) only while an install
       is actually in flight. */
    async function watchVoiceInstall() {
        if (voiceInstallPolling) {
            return;
        }
        voiceInstallPolling = true;
        setVoiceInstallBusy(true);
        try {
            for (;;) {
                await voiceInstallDelay();
                const response = await fetch('/api/voice-deps-install');
                const state = await response.json();
                if (!response.ok) {
                    throw new Error(state.error || `Install status check failed with status ${response.status}`);
                }
                if (state.status === 'running') {
                    continue;
                }
                voiceInstallPolling = false;
                await loadVoiceStatus().catch(() => {});
                if (state.status === 'success') {
                    setVoiceAvailabilityMessage(state.message || 'Voice dependencies installed.');
                    notifyAppSettings(
                        state.restart_required
                            ? `${state.message} Use Restart GridVibe to finish.`
                            : (state.message || 'Voice dependencies installed.'),
                        state.restart_required ? 'warning' : 'success'
                    );
                } else {
                    const tail = (state.output_tail || []).slice(-1).join(' ');
                    setVoiceAvailabilityMessage(`${state.message || 'Install failed.'}${tail ? ` (${tail})` : ''}`);
                    notifyAppSettings(state.message || 'Voice dependency install failed.', 'error');
                }
                return;
            }
        } catch (error) {
            setVoiceAvailabilityMessage(`Install status unavailable: ${error.message}`);
            notifyAppSettings(`Voice dependency install failed: ${error.message}`, 'error');
        } finally {
            voiceInstallPolling = false;
            setVoiceInstallBusy(false);
        }
    }

    async function installVoiceDependencies() {
        document.getElementById('appVoiceAvailability')?.classList.remove('hidden');
        setVoiceInstallBusy(true);
        setVoiceAvailabilityMessage('Installing voice dependencies with pip. This can take a few minutes.');
        try {
            const response = await fetch('/api/voice-deps-install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `Install request failed with status ${response.status}`);
            }
            await watchVoiceInstall();
        } catch (error) {
            setVoiceInstallBusy(false);
            setVoiceAvailabilityMessage(`Install failed: ${error.message}`);
            notifyAppSettings(`Voice dependency install failed: ${error.message}`, 'error');
        }
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
            appMicDevices.forEach((device, index) => {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.textContent = voiceDeviceLabel(device, index);
                deviceInput.appendChild(option);
            });
            if (
                selectedDeviceId &&
                !appMicDevices.some(device => device.deviceId === selectedDeviceId)
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

    async function refreshAppMicrophones() {
        if (!navigator.mediaDevices?.enumerateDevices) {
            setVoicePrefsStatus('This browser does not expose microphone selection.');
            return;
        }

        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            appMicDevices = devices.filter(device => device.kind === 'audioinput');
            syncVoicePrefsForm();
            if (appMicDevices.length === 0) {
                setVoicePrefsStatus('No audio input devices are currently available.');
                return;
            }
            const hasNamedDevices = appMicDevices.some(device => Boolean(device.label));
            setVoicePrefsStatus(
                hasNamedDevices
                    ? `Microphones refreshed. ${appMicDevices.length} input device${appMicDevices.length === 1 ? '' : 's'} available.`
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

    /* OD-13: the preset dropdown covers the common monospace stacks; the
       "Custom…" option falls through to the original free-text input so no
       configurability is lost. */
    function collectTerminalFontFamily() {
        const preset = document.getElementById('appTerminalFontPreset')?.value || '';
        if (preset && preset !== 'custom') {
            return preset;
        }
        return document.getElementById('appTerminalFontFamily')?.value.trim()
            || DEFAULT_APP_SETTINGS.terminal.font_family;
    }

    function syncAutosaveIntervalLabel() {
        const input = document.getElementById('appWorkspaceAutosaveInterval');
        const value = document.getElementById('appWorkspaceAutosaveIntervalValue');
        if (input && value) {
            value.textContent = input.value;
        }
    }

    function collectAppSettingsForm() {
        return {
            appearance: {
                theme: document.getElementById('appTheme')?.value || DEFAULT_APP_SETTINGS.appearance.theme
            },
            workspace: {
                surface_mode: document.getElementById('appSurfaceMode')?.value === 'max' ? 'max' : 'normal',
                autosave_interval_minutes: Math.min(15, Math.max(1,
                    Number(document.getElementById('appWorkspaceAutosaveInterval')?.value)
                        || DEFAULT_APP_SETTINGS.workspace.autosave_interval_minutes
                ))
                /* multi_workspace_enabled is deliberately absent: the launcher's
                   Workspaces switch owns it, and an omitted key keeps whatever
                   the server already has (web/api.py _normalize_app_config_update),
                   so saving this dialog can never move the mode behind the
                   user's back. */
            },
            ssh: {
                host_key_policy: document.getElementById('appSshHostKeyPolicy')?.value || DEFAULT_APP_SETTINGS.ssh.host_key_policy
            },
            terminal: {
                font_family: collectTerminalFontFamily(),
                font_size: Number(document.getElementById('appTerminalFontSize')?.value)
                    || DEFAULT_APP_SETTINGS.terminal.font_size,
                max_sessions: Number(document.getElementById('appTerminalMaxSessions')?.value)
                    || DEFAULT_APP_SETTINGS.terminal.max_sessions,
                /* OD-14: focused-session-only by default; the checkbox opts a
                   save into pushing font + size to every active session. */
                apply_scope: document.getElementById('appTerminalApplyAll')?.checked ? 'all' : 'session'
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

    function notifyAppConfigUpdated(appSettings, applyScope = 'session') {
        const payload = {
            appearance: {
                theme: normalizeThemePreference(appSettings?.appearance?.theme)
            },
            workspace: {
                surface_mode: appSettings?.workspace?.surface_mode === 'max' ? 'max' : 'normal',
                multi_workspace_enabled: Boolean(appSettings?.workspace?.multi_workspace_enabled)
            },
            terminal: {
                font_family: String(appSettings?.terminal?.font_family || DEFAULT_APP_SETTINGS.terminal.font_family),
                font_size: Number(appSettings?.terminal?.font_size) || DEFAULT_APP_SETTINGS.terminal.font_size,
                apply_scope: applyScope === 'all' ? 'all' : 'session'
            },
            timestamp: Date.now(),
            nonce: Math.random().toString(36).slice(2),
            source: GRIDVIBE_WINDOW_ID
        };

        try {
            const channel = new BroadcastChannel(APP_CONFIG_BROADCAST_CHANNEL);
            channel.postMessage(payload);
            channel.close();
        } catch (_error) {}

        try {
            localStorage.setItem(APP_CONFIG_UPDATE_STORAGE_KEY, JSON.stringify(payload));
        } catch (_error) {}

        return payload;
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
        if (!modal) {
            return;
        }
        modal.classList.remove('visible');
        modal.setAttribute('aria-hidden', 'true');
    }

    async function openAppSettings() {
        try {
            await Promise.all([
                loadAppSettings(),
                loadVoicePrefs()
            ]);
            refreshAppMicrophones().catch(() => {});
            loadVoiceStatus().catch(() => {});
            const modal = document.getElementById('appSettingsModal');
            modal.classList.add('visible');
            modal.setAttribute('aria-hidden', 'false');
        } catch (error) {
            notifyAppSettings(`Could not load app settings: ${error.message}`, 'error');
        }
    }

    async function saveAppSettings() {
        const button = document.getElementById('saveAppSettingsBtn');
        button.disabled = true;
        button.textContent = 'Saving...';

        try {
            const settingsForm = collectAppSettingsForm();

            const [settingsResponse] = await Promise.all([
                fetch('/api/app-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settingsForm)
                }),
                saveVoicePrefs()
            ]);
            const data = await settingsResponse.json();
            if (!settingsResponse.ok) {
                throw new Error(data.error || 'Failed to save app settings');
            }

            applyAppSettings(data);
            const payload = notifyAppConfigUpdated(data, settingsForm.terminal.apply_scope);
            if (typeof onAppSettingsSaved === 'function') {
                onAppSettingsSaved(data, payload);
            }
            notifyAppSettings('App settings saved.', 'success');

            /* Turning voice input on with no backend installed used to save
               happily and then do nothing (stage J issue 2). Offer the install
               right here and keep the dialog open to show its progress. */
            if (settingsForm.voice_input.enabled && !voiceEngineAvailable(settingsForm.voice_input.engine)) {
                const install = await openGenericConfirmModal({
                    title: 'Install voice dependencies?',
                    copy: voiceUnavailableMessage(settingsForm.voice_input.engine),
                    note: 'GridVibe installs them into its own environment and loads them without a restart.',
                    confirmLabel: 'Install now'
                });
                if (install) {
                    installVoiceDependencies();
                    return;
                }
            }

            closeAppSettingsModal();
        } catch (error) {
            notifyAppSettings(`Settings save failed: ${error.message}`, 'error');
        } finally {
            button.disabled = false;
            button.textContent = 'Save Settings';
        }
    }

    /* ── Dialog wiring (the partial is in the document before this script) ── */

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
            refreshAppMicrophones().catch(() => {});
        });
    }

    document.getElementById('appSettingsModal')?.addEventListener('click', event => {
        if (event.target.id === 'appSettingsModal') {
            closeAppSettingsModal();
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape') {
            return;
        }
        if (document.getElementById('appSettingsModal')?.classList.contains('visible')) {
            closeAppSettingsModal();
        }
    });
