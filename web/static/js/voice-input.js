    /* ─────────────────────────────────────────────
       Voice input — extracted from terminals.js per
       docs/terminals_js_split_plan_2026-07-23.md (Phase 2, move-only).
       Mic capture → Socket.IO → STT, recording overlay, hold/push-to-talk.
       Loaded before terminals.js; the top-level PTT key listeners and all
       shared terminal state remain in terminals.js.
    ───────────────────────────────────────────── */
    /* ─────────────────────────────────────────────
       Voice input — mic capture → Socket.IO → configured STT backend
    ───────────────────────────────────────────── */
    const PAGE_DATASET = document.body?.dataset || {};
    const VOICE_WORKLET_MODULE_URL = PAGE_DATASET.voiceWorkletUrl || '/static/voice-capture-worklet.js';
    const VOICE_ENGINE = PAGE_DATASET.voiceEngine || 'vosk';
    const VOICE_MODEL = PAGE_DATASET.voiceModel || 'unknown';
    const VOICE_LANGUAGE = PAGE_DATASET.voiceLanguage || 'en-US';
    const VOICE_CAPTURE_PROFILES = Object.freeze({
        headset: Object.freeze({
            label: 'Headset',
            constraints: Object.freeze({
                echoCancellation: false,
                noiseSuppression: false,
                autoGainControl: false,
                channelCount: 1
            })
        }),
        laptop: Object.freeze({
            label: 'Laptop',
            constraints: Object.freeze({
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
                channelCount: 1
            })
        })
    });
    const VOICE_PREFS_STORAGE_KEY = 'gridvibe.voice.capture.v1';
    const VOICE_TARGET_SAMPLE_RATE = 16000;
    const VOICE_CHUNK_SIZE = 640;
    const VOICE_DEFAULT_STATUS = 'Requested capture settings will be checked against the browser\'s actual applied settings after the microphone starts.';
    const VOICE_LOG_PREFIX = '[GridVibe Voice]';
    const _voiceState = {};
    const _voiceLastDiagnostics = {};
    const _voiceStatusMessages = {};
    let _voiceActiveIndex = -1;   // only one terminal may record at a time
    let _voicePrefs = _loadVoicePrefs();
    let _voiceServiceStatus = {
        enabled: PAGE_DATASET.voiceEnabled === 'true',
        engine: VOICE_ENGINE,
        model: VOICE_MODEL,
        language: VOICE_LANGUAGE,
        engine_available: null,
        service_running: null,
        service_url: ''
    };

    function _defaultVoicePrefs() {
        return {
            profile: 'laptop',
            deviceId: '',
            pttEnabled: false,
            pttKeybind: ''
        };
    }

    function _loadVoicePrefs() {
        const defaults = _defaultVoicePrefs();
        try {
            const raw = window.localStorage?.getItem(VOICE_PREFS_STORAGE_KEY);
            if (!raw) return defaults;
            const parsed = JSON.parse(raw);
            const profile = VOICE_CAPTURE_PROFILES[parsed?.profile]
                ? parsed.profile
                : defaults.profile;
            return {
                profile,
                deviceId: typeof parsed?.deviceId === 'string' ? parsed.deviceId : '',
                pttEnabled: typeof parsed?.pttEnabled === 'boolean' ? parsed.pttEnabled : false,
                pttKeybind: typeof parsed?.pttKeybind === 'string' ? parsed.pttKeybind : ''
            };
        } catch (_) {
            return defaults;
        }
    }

    function _saveVoicePrefs() {
        try {
            window.localStorage?.setItem(VOICE_PREFS_STORAGE_KEY, JSON.stringify(_voicePrefs));
        } catch (_) {}
        fetch('/api/voice-prefs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(_voicePrefs)
        }).catch(() => {});
    }

    async function _loadVoicePrefsFromServer() {
        try {
            const resp = await fetch('/api/voice-prefs');
            if (!resp.ok) return;
            const server = await resp.json();
            const defaults = _defaultVoicePrefs();
            const profile = VOICE_CAPTURE_PROFILES[server?.profile]
                ? server.profile : defaults.profile;
            _voicePrefs = {
                profile,
                deviceId: typeof server?.deviceId === 'string' ? server.deviceId : _voicePrefs.deviceId,
                pttEnabled: typeof server?.pttEnabled === 'boolean' ? server.pttEnabled : _voicePrefs.pttEnabled,
                pttKeybind: typeof server?.pttKeybind === 'string' ? server.pttKeybind : _voicePrefs.pttKeybind
            };
            try {
                window.localStorage?.setItem(VOICE_PREFS_STORAGE_KEY, JSON.stringify(_voicePrefs));
            } catch (_) {}
            terminals.forEach((_, terminalIndex) => _syncVoiceControls(terminalIndex));
        } catch (_) {}
    }

    function _voiceLog(message, details) {
        if (typeof details === 'undefined') {
            console.log(`${VOICE_LOG_PREFIX} ${message}`);
            return;
        }
        console.log(`${VOICE_LOG_PREFIX} ${message}`, details);
    }

    function _getVoiceConstraints(preferences = _voicePrefs, { includeDevice = true } = {}) {
        const profile = VOICE_CAPTURE_PROFILES[preferences.profile] || VOICE_CAPTURE_PROFILES.laptop;
        const constraints = { ...profile.constraints };
        if (includeDevice && preferences.deviceId) {
            constraints.deviceId = { exact: preferences.deviceId };
        }
        return constraints;
    }

    function _getVoicePanelElements(index) {
        return {
            control: document.querySelector(`[data-terminal-voice-control="${index}"]`)
        };
    }

    function _sanitizeVoiceDiagnostic(value) {
        if (value === undefined) return undefined;
        if (value === null) return null;
        if (Array.isArray(value)) {
            return value.map(item => _sanitizeVoiceDiagnostic(item));
        }
        if (typeof value === 'number') {
            return Number.isFinite(value) ? Number(value.toFixed(4)) : String(value);
        }
        if (typeof value === 'object') {
            const sanitized = {};
            Object.keys(value).sort().forEach(key => {
                const normalized = _sanitizeVoiceDiagnostic(value[key]);
                if (normalized !== undefined) {
                    sanitized[key] = normalized;
                }
            });
            return sanitized;
        }
        return value;
    }

    function _formatVoiceDiagnostic(value, fallback) {
        if (value === undefined || value === null) {
            return fallback;
        }
        const sanitized = _sanitizeVoiceDiagnostic(value);
        if (
            typeof sanitized === 'object' &&
            sanitized !== null &&
            !Array.isArray(sanitized) &&
            Object.keys(sanitized).length === 0
        ) {
            return fallback;
        }
        return JSON.stringify(sanitized, null, 2);
    }

    function _voiceSummaryMessage() {
        const parts = [
            `Engine: ${_voiceServiceStatus.engine || VOICE_ENGINE}`,
            `Model: ${_voiceServiceStatus.model || VOICE_MODEL}`,
            `Language: ${_voiceServiceStatus.language || VOICE_LANGUAGE}`,
            'Target PCM: 16 kHz mono'
        ];
        if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'vosk' && typeof _voiceServiceStatus.service_running === 'boolean') {
            parts.push(`Service: ${_voiceServiceStatus.service_running ? 'running' : 'starting on demand'}`);
        }
        if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'whisper') {
            parts.push('Mode: final transcript on stop');
        }
        if (typeof _voiceServiceStatus.startup_timeout_seconds === 'number') {
            parts.push(`Startup wait: ${_voiceServiceStatus.startup_timeout_seconds}s`);
        }
        return parts.join(' • ');
    }

    function _voiceDefaultStatusMessage() {
        if (isPywebviewAvailable()) {
            return 'Native pywebview mode detected. Microphone capture depends on embedded webview support and may fail even when browser mode works. If capture cannot start here, use browser mode.';
        }
        return VOICE_DEFAULT_STATUS;
    }

    function _updateVoiceMeta(index) {
        return _voiceSummaryMessage();
    }

    function _renderVoiceDiagnostics(index, diagnostics) {
        return _formatVoiceDiagnostic(diagnostics, '');
    }

    function _syncVoiceControls(index) {
        const elements = _getVoicePanelElements(index);
        if (!elements.control) return;

        elements.control.hidden = _voiceServiceStatus.enabled === false;
        _updateVoiceMeta(index);
        _renderVoiceDiagnostics(
            index,
            _voiceState[index]?.diagnostics || _voiceLastDiagnostics[index] || {
                requested: _getVoiceConstraints(_voicePrefs, { includeDevice: Boolean(_voicePrefs.deviceId) }),
                supportedConstraints: navigator.mediaDevices?.getSupportedConstraints?.()
            }
        );
        if (!_voiceState[index]) {
            _setVoicePanelStatus(index, _voiceDefaultStatusMessage());
        }
        _updateVoiceBtn(index, Boolean(_voiceState[index]?.recording));
    }

    function _syncVoiceControlsAvailability() {
        document.querySelectorAll('[data-terminal-voice-control]').forEach(control => {
            control.hidden = _voiceServiceStatus.enabled === false;
        });
        _setVoiceBtnsDisabled(_voiceActiveIndex);
    }

    async function _loadVoiceServiceStatus() {
        try {
            const response = await fetch('/api/voice-status');
            if (!response.ok) return;
            const payload = await response.json();
            _voiceServiceStatus = {
                ..._voiceServiceStatus,
                ...payload
            };
            _syncVoiceControlsAvailability();
            terminals.forEach((_, terminalIndex) => _updateVoiceMeta(terminalIndex));
        } catch (err) {
            console.warn('Voice status fetch failed:', err);
        }
    }

    function _voiceBackendUnavailableMessage() {
        if (_voiceServiceStatus.enabled === false) {
            return 'Voice input is disabled in app settings.';
        }

        if (_voiceServiceStatus.engine_available === false) {
            if (_voiceServiceStatus.status_message) {
                return _voiceServiceStatus.status_message;
            }
            if ((_voiceServiceStatus.engine || VOICE_ENGINE) === 'whisper') {
                return 'faster-whisper is unavailable. Install optional voice dependencies before starting capture.';
            }
            return 'The configured voice backend is unavailable. Install optional voice dependencies before starting capture.';
        }

        return '';
    }

    function _setVoicePanelStatus(index, message) {
        _voiceStatusMessages[index] = message;
        const btn = document.getElementById(`tvoice-${index}`);
        if (btn && !_voiceState[index]?.recording) {
            btn.title = `Voice input (${message})`;
        }
    }

    function _voiceIndexForSession(sessionId) {
        const renderedIndex = sessionIds.indexOf(sessionId);
        if (renderedIndex !== -1) {
            return renderedIndex;
        }
        return Object.keys(_voiceState)
            .map(key => Number.parseInt(key, 10))
            .find(index => _voiceState[index]?.sessionId === sessionId) ?? -1;
    }

    function _voiceActualSettings(state) {
        return {
            ...state.trackSettings,
            audioContextState: state.audioCtx?.state || 'unknown',
            audioContextSampleRate: state.audioCtx?.sampleRate ?? null,
            requestedProfile: state.profile,
            selectedDeviceId: state.selectedDeviceId || 'default',
            workletChunkSamples: state.format?.chunkSize ?? VOICE_CHUNK_SIZE,
            workletSourceSampleRate: state.format?.sourceSampleRate ?? state.audioCtx?.sampleRate ?? null,
            workletTargetSampleRate: state.format?.targetSampleRate ?? VOICE_TARGET_SAMPLE_RATE
        };
    }

    function _applyVoiceStateDiagnostics(index) {
        const state = _voiceState[index];
        const diagnostics = state
            ? {
                requested: state.requestedConstraints,
                settings: _voiceActualSettings(state),
                supportedConstraints: state.supportedConstraints,
                capabilities: state.capabilities
            }
            : _voiceLastDiagnostics[index];
        if (diagnostics) {
            if (state) {
                state.diagnostics = diagnostics;
            }
            _renderVoiceDiagnostics(index, diagnostics);
        }
    }

    async function _toggleVoice(index) {
        _voiceLog('Toggling voice capture', {
            terminalIndex: index,
            sessionId: sessionIds[index] || null,
            recording: Boolean(_voiceState[index]?.recording),
            activeIndex: _voiceActiveIndex
        });
        if (_voiceState[index]?.recording) {
            await _stopVoice(index);
        } else {
            if (_voiceActiveIndex !== -1 && _voiceActiveIndex !== index) {
                _voiceLog('Stopping active voice capture before switching terminals', {
                    fromTerminalIndex: _voiceActiveIndex,
                    toTerminalIndex: index
                });
                await _stopVoice(_voiceActiveIndex);
            }
            await _startVoice(index);
        }
    }

    function _setVoiceBtnsDisabled(activeIndex) {
        document.querySelectorAll('.voice-btn').forEach(btn => {
            const btnIdx = parseInt(btn.dataset.terminalVoice, 10);
            if (_voiceServiceStatus.enabled === false) {
                btn.disabled = true;
                btn.title = 'Voice input is disabled in app settings';
                return;
            }
            if (activeIndex === -1) {
                btn.disabled = false;
                btn.title = 'Voice input (click to start recording)';
            } else if (btnIdx !== activeIndex) {
                btn.disabled = true;
                btn.title = 'Another terminal is recording';
            } else {
                btn.disabled = false;
            }
        });
    }

    async function _startVoice(index) {
        const sid = sessionIds[index];
        _voiceLog('Starting voice capture', {
            terminalIndex: index,
            sessionId: sid || null,
            hasSocket: Boolean(socket),
            profile: _voicePrefs.profile,
            selectedDeviceId: _voicePrefs.deviceId || ''
        });
        if (!sid || !socket) {
            _voiceLog('Voice start aborted because session or socket is unavailable', {
                terminalIndex: index,
                sessionId: sid || null,
                hasSocket: Boolean(socket)
            });
            return;
        }

        await _loadVoiceServiceStatus();
        const backendUnavailableMessage = _voiceBackendUnavailableMessage();
        if (backendUnavailableMessage) {
            _voiceLog('Voice start aborted because the configured backend is unavailable', {
                terminalIndex: index,
                sessionId: sid,
                engine: _voiceServiceStatus.engine || VOICE_ENGINE,
                message: backendUnavailableMessage
            });
            _setVoicePanelStatus(index, backendUnavailableMessage);
            return;
        }

        if (!navigator.mediaDevices?.getUserMedia) {
            _voiceLog('Voice start aborted because getUserMedia is unavailable', {
                terminalIndex: index
            });
            _setVoicePanelStatus(
                index,
                isPywebviewAvailable()
                    ? 'This pywebview environment does not expose getUserMedia() for microphone capture. Use browser mode for voice input on this machine.'
                    : 'This browser does not expose getUserMedia() for microphone capture.'
            );
            return;
        }

        if (!window.AudioWorkletNode) {
            _voiceLog('Voice start aborted because AudioWorklet is unavailable', {
                terminalIndex: index
            });
            _setVoicePanelStatus(index, 'AudioWorklet is not available in this browser, so the upgraded capture pipeline cannot start.');
            return;
        }

        const requestedConstraints = _getVoiceConstraints(_voicePrefs);
        const supportedConstraints = navigator.mediaDevices.getSupportedConstraints
            ? navigator.mediaDevices.getSupportedConstraints()
            : null;

        _setVoicePanelStatus(index, 'Opening microphone and verifying browser-applied capture settings...');
        _clearVoicePreview(index);

        let stream = null;
        let pipeline = null;
        let acquisition = null;

        try {
            acquisition = await _acquireMicStream(index, sid, requestedConstraints);
            ({ stream } = acquisition);
            const { appliedConstraints, selectedDeviceId, fallbackMessage } = acquisition;

            const track = stream.getAudioTracks()[0];
            const capabilities = track?.getCapabilities ? track.getCapabilities() : null;
            const trackSettings = track?.getSettings ? track.getSettings() : {};
            _voiceLog('Microphone track opened', {
                terminalIndex: index,
                sessionId: sid,
                label: track?.label || '',
                readyState: track?.readyState || 'unknown',
                trackSettings
            });

            pipeline = await _createVoicePipeline(index, sid, stream);
            const { audioCtx, source, workletNode, sink } = pipeline;

            const state = {
                stream,
                audioCtx,
                source,
                workletNode,
                sink,
                track,
                sessionId: sid,
                recording: true,
                requestedConstraints: appliedConstraints,
                selectedDeviceId,
                profile: _voicePrefs.profile,
                supportedConstraints,
                capabilities,
                trackSettings,
                format: {
                    sourceSampleRate: audioCtx.sampleRate,
                    targetSampleRate: VOICE_TARGET_SAMPLE_RATE,
                    chunkSize: VOICE_CHUNK_SIZE
                },
                diagnostics: null,
                pendingFlush: null
            };

            _wireVoiceWorkletMessages(index, sid, workletNode);

            _voiceLog('Emitting voice_start to server', {
                terminalIndex: index,
                sessionId: sid
            });
            socket.emit('voice_start', { session_id: sid });

            source.connect(workletNode);
            workletNode.connect(sink);
            sink.connect(audioCtx.destination);
            if (audioCtx.state === 'suspended') {
                _voiceLog('AudioContext is suspended, attempting resume', {
                    terminalIndex: index,
                    sessionId: sid
                });
                await audioCtx.resume();
            }

            _voiceState[index] = state;
            _voiceActiveIndex = index;
            _voiceLastDiagnostics[index] = {
                requested: appliedConstraints,
                settings: _voiceActualSettings(state),
                supportedConstraints,
                capabilities
            };
            _updateVoiceBtn(index, true);
            _showVoiceRecordingOverlay(index);
            _setVoiceBtnsDisabled(index);
            _applyVoiceStateDiagnostics(index);
            _setVoicePanelStatus(
                index,
                fallbackMessage || 'Capture started with the global microphone settings.'
            );
            _voiceLog('Voice capture started successfully', {
                terminalIndex: index,
                sessionId: sid,
                fallbackUsed: Boolean(fallbackMessage),
                diagnostics: _voiceLastDiagnostics[index]
            });
        } catch (err) {
            console.error(`${VOICE_LOG_PREFIX} Voice input error:`, err, {
                terminalIndex: index,
                sessionId: sid || null,
                requestedConstraints: acquisition?.appliedConstraints ?? requestedConstraints
            });
            await _teardownVoicePipeline(stream, pipeline);
            _hideVoiceRecordingOverlay();
            _updateVoiceBtn(index, false);
            _setVoiceBtnsDisabled(-1);
            const errName = err?.name ? `[${err.name}] ` : '';
            const errMessage = err?.message || String(err);
            _setVoicePanelStatus(
                index,
                isPywebviewAvailable()
                    ? `Voice capture failed: ${errName}${errMessage}. Native pywebview mode is less reliable for microphone capture; if this persists, use browser mode.`
                    : `Voice capture failed: ${errName}${errMessage}`
            );
        }
    }

    /* Open the microphone with the configured constraints, falling back to
       the browser-default device or (under pywebview) unconstrained capture
       when the selected device or profile is not usable. */
    async function _acquireMicStream(index, sid, requestedConstraints) {
        let selectedDeviceId = _voicePrefs.deviceId;
        let appliedConstraints = requestedConstraints;
        let fallbackMessage = '';
        let stream = null;

        try {
            _voiceLog('Requesting microphone access', {
                terminalIndex: index,
                sessionId: sid,
                requestedConstraints
            });
            stream = await navigator.mediaDevices.getUserMedia({ audio: requestedConstraints });
            _voiceLog('Microphone access granted', {
                terminalIndex: index,
                sessionId: sid,
                trackCount: stream.getAudioTracks().length
            });
        } catch (err) {
            if (
                selectedDeviceId &&
                (err?.name === 'NotFoundError' || err?.name === 'OverconstrainedError')
            ) {
                _voiceLog('Selected microphone unavailable, retrying with browser default device', {
                    terminalIndex: index,
                    sessionId: sid,
                    selectedDeviceId,
                    errorName: err?.name,
                    errorMessage: err?.message || String(err)
                });
                fallbackMessage = 'Selected microphone was unavailable, so capture fell back to the browser default input.';
                selectedDeviceId = '';
                appliedConstraints = _getVoiceConstraints({ ..._voicePrefs, deviceId: '' }, { includeDevice: false });
                stream = await navigator.mediaDevices.getUserMedia({ audio: appliedConstraints });
                _voiceLog('Fallback microphone access granted', {
                    terminalIndex: index,
                    sessionId: sid,
                    appliedConstraints
                });
            } else if (isPywebviewAvailable() && err?.name !== 'NotAllowedError') {
                _voiceLog('Constrained capture failed in pywebview, retrying with bare {audio: true}', {
                    terminalIndex: index,
                    sessionId: sid,
                    errorName: err?.name,
                    errorMessage: err?.message || String(err),
                    originalConstraints: appliedConstraints
                });
                try {
                    appliedConstraints = true;
                    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    fallbackMessage = `Capture profile constraints were not supported by this WebView2 environment (${err?.name || 'error'}). Fell back to unconstrained capture.`;
                    _voiceLog('Bare audio capture succeeded in pywebview fallback', {
                        terminalIndex: index,
                        sessionId: sid
                    });
                } catch (bareErr) {
                    _voiceLog('Bare audio capture also failed', {
                        terminalIndex: index,
                        sessionId: sid,
                        errorName: bareErr?.name,
                        errorMessage: bareErr?.message || String(bareErr)
                    });
                    throw bareErr;
                }
            } else {
                throw err;
            }
        }

        return { stream, appliedConstraints, selectedDeviceId, fallbackMessage };
    }

    /* Build the AudioContext → worklet → muted-sink capture pipeline. */
    async function _createVoicePipeline(index, sid, stream) {
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)({
            latencyHint: 'interactive'
        });
        _voiceLog('AudioContext created for voice capture', {
            terminalIndex: index,
            sessionId: sid,
            audioContextState: audioCtx.state,
            sampleRate: audioCtx.sampleRate
        });
        await audioCtx.audioWorklet.addModule(VOICE_WORKLET_MODULE_URL);
        _voiceLog('Voice worklet module loaded', {
            terminalIndex: index,
            sessionId: sid,
            moduleUrl: VOICE_WORKLET_MODULE_URL
        });
        const source = audioCtx.createMediaStreamSource(stream);
        const workletNode = new AudioWorkletNode(audioCtx, 'gridvibe-voice-processor', {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            channelCount: 1,
            outputChannelCount: [1],
            processorOptions: {
                targetSampleRate: VOICE_TARGET_SAMPLE_RATE,
                chunkSize: VOICE_CHUNK_SIZE
            }
        });
        const sink = audioCtx.createGain();
        sink.gain.value = 0;
        return { audioCtx, source, workletNode, sink };
    }

    /* Route worklet messages: audio chunks to the server, format reports to
       the diagnostics panel, flush confirmations to the pending waiter. */
    function _wireVoiceWorkletMessages(index, sid, workletNode) {
        workletNode.port.onmessage = event => {
            const currentState = _voiceState[index];
            if (!currentState) return;
            const message = event.data || {};
            if (message.type === 'audio' && currentState.recording) {
                socket.emit('voice_audio', {
                    session_id: sid,
                    audio: message.audio
                });
                return;
            }
            if (message.type === 'format') {
                currentState.format = {
                    sourceSampleRate: message.sourceSampleRate,
                    targetSampleRate: message.targetSampleRate,
                    chunkSize: message.chunkSize
                };
                _voiceLog('Voice worklet reported format', {
                    terminalIndex: index,
                    sessionId: sid,
                    format: currentState.format
                });
                _applyVoiceStateDiagnostics(index);
                return;
            }
            if (
                message.type === 'flush-complete' &&
                currentState.pendingFlush &&
                currentState.pendingFlush.flushId === message.flushId
            ) {
                const { resolve } = currentState.pendingFlush;
                currentState.pendingFlush = null;
                resolve();
            }
        };
    }

    /* Best-effort teardown when capture start fails partway through. */
    async function _teardownVoicePipeline(stream, pipeline) {
        if (pipeline?.workletNode) {
            try { pipeline.workletNode.disconnect(); } catch (_) {}
        }
        if (pipeline?.source) {
            try { pipeline.source.disconnect(); } catch (_) {}
        }
        if (pipeline?.sink) {
            try { pipeline.sink.disconnect(); } catch (_) {}
        }
        if (pipeline?.audioCtx) {
            try { await pipeline.audioCtx.close(); } catch (_) {}
        }
        if (stream) {
            stream.getTracks().forEach(track => {
                try { track.stop(); } catch (_) {}
            });
        }
    }

    async function _flushVoiceWorklet(index, state) {
        if (!state?.workletNode?.port) return;

        const flushId = `flush-${Date.now()}-${Math.random().toString(16).slice(2)}`;
        await new Promise(resolve => {
            const timer = window.setTimeout(() => {
                if (state.pendingFlush?.flushId === flushId) {
                    state.pendingFlush = null;
                }
                resolve();
            }, 200);

            state.pendingFlush = {
                flushId,
                resolve: () => {
                    window.clearTimeout(timer);
                    resolve();
                }
            };

            state.workletNode.port.postMessage({ type: 'flush', flushId });
        });
    }

    async function _stopVoice(index, { notifyServer = true } = {}) {
        const state = _voiceState[index];
        if (!state) return;
        _voiceLog('Stopping voice capture', {
            terminalIndex: index,
            sessionId: state.sessionId || sessionIds[index] || null,
            notifyServer
        });

        state.recording = false;
        _hideVoiceRecordingOverlay();
        await _flushVoiceWorklet(index, state);

        const sid = state.sessionId || sessionIds[index];
        if (notifyServer && sid && socket) {
            socket.emit('voice_stop', { session_id: sid });
        }

        _voiceLastDiagnostics[index] = {
            requested: state.requestedConstraints,
            settings: _voiceActualSettings(state),
            supportedConstraints: state.supportedConstraints,
            capabilities: state.capabilities
        };

        try { state.workletNode.disconnect(); } catch (_) {}
        try { state.source.disconnect(); } catch (_) {}
        try { state.sink.disconnect(); } catch (_) {}
        state.stream.getTracks().forEach(track => {
            try { track.stop(); } catch (_) {}
        });
        try { await state.audioCtx.close(); } catch (_) {}

        delete _voiceState[index];
        _voiceActiveIndex = -1;
        _updateVoiceBtn(index, false);
        _setVoiceBtnsDisabled(-1);
        _clearVoicePreview(index);
        _applyVoiceStateDiagnostics(index);
        _setVoicePanelStatus(index, 'Capture stopped. The last applied browser settings are preserved above for comparison.');
    }

    async function _stopAllVoice() {
        const activeIndexes = Object.keys(_voiceState)
            .map(key => Number.parseInt(key, 10))
            .filter(Number.isInteger);
        for (const index of activeIndexes) {
            await _stopVoice(index);
        }
    }

    /* ── Floating recording indicator (ISSUE-2026-019) ──
       One fixed-position, non-blocking overlay shared by every capture path
       (mic toggle, hold-to-talk, push-to-talk keybind). Shown only after
       _startVoice() has established an active recording state; hidden from
       every _stopVoice(), start-failure, and teardown path. */
    const VOICE_OVERLAY_ID = 'voiceRecordingOverlay';
    const VOICE_OVERLAY_BAR_COUNT = 5;
    const VOICE_OVERLAY_MIC_ICON = '<svg class="voice-overlay-mic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>';
    let _voiceOverlayAnimation = null;

    function _prefersReducedMotion() {
        try {
            return Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches);
        } catch (_) {
            return false;
        }
    }

    function _showVoiceRecordingOverlay(index) {
        if (!_voiceState[index]?.recording) {
            return; /* capture already stopped while startup was in flight */
        }
        let overlay = document.getElementById(VOICE_OVERLAY_ID);
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.id = VOICE_OVERLAY_ID;
            overlay.className = 'voice-recording-overlay';
            overlay.setAttribute('role', 'status');
            overlay.setAttribute('aria-live', 'polite');
            const bars = Array.from(
                { length: VOICE_OVERLAY_BAR_COUNT },
                () => '<span class="voice-overlay-bar"></span>'
            ).join('');
            overlay.innerHTML = `${VOICE_OVERLAY_MIC_ICON}<span class="voice-overlay-label">Recording</span><span class="voice-overlay-bars" aria-hidden="true">${bars}</span>`;
            document.body.appendChild(overlay);
        }
        overlay.classList.remove('voice-overlay-fallback');
        _startVoiceOverlayAnimation(index, overlay);
    }

    function _hideVoiceRecordingOverlay() {
        _stopVoiceOverlayAnimation();
        document.getElementById(VOICE_OVERLAY_ID)?.remove();
    }

    /* Drive the bars from a small AnalyserNode fanned out from the live
       capture source; fall back to the deterministic CSS animation when the
       analyser cannot be created, and stay static under reduced motion. */
    function _startVoiceOverlayAnimation(index, overlay) {
        _stopVoiceOverlayAnimation();
        if (_prefersReducedMotion()) {
            return;
        }
        const state = _voiceState[index];
        let analyser = null;
        try {
            if (state?.audioCtx && state?.source) {
                analyser = state.audioCtx.createAnalyser();
                analyser.fftSize = 64;
                analyser.smoothingTimeConstant = 0.6;
                state.source.connect(analyser);
            }
        } catch (_) {
            analyser = null;
        }
        if (!analyser) {
            overlay.classList.add('voice-overlay-fallback');
            return;
        }
        const bars = overlay.querySelectorAll('.voice-overlay-bar');
        const data = new Uint8Array(analyser.frequencyBinCount);
        const animation = { frameId: 0, analyser, source: state.source };
        const tick = () => {
            if (_voiceOverlayAnimation !== animation) {
                return; /* a newer capture (or a stop) superseded this loop */
            }
            analyser.getByteFrequencyData(data);
            bars.forEach((bar, barIndex) => {
                const bin = data[Math.floor(((barIndex + 1) * data.length) / (bars.length + 1))] || 0;
                const scale = 0.25 + (bin / 255) * 0.75;
                bar.style.transform = `scaleY(${scale.toFixed(3)})`;
            });
            animation.frameId = window.requestAnimationFrame(tick);
        };
        _voiceOverlayAnimation = animation;
        animation.frameId = window.requestAnimationFrame(tick);
    }

    function _stopVoiceOverlayAnimation() {
        const animation = _voiceOverlayAnimation;
        if (!animation) {
            return;
        }
        _voiceOverlayAnimation = null;
        if (animation.frameId) {
            window.cancelAnimationFrame(animation.frameId);
        }
        try { animation.source.disconnect(animation.analyser); } catch (_) {}
    }

    /* Press-and-hold on the mic button records while held (mirroring the
       push-to-talk keybind); a quick click keeps the existing click-to-toggle
       behaviour. Keyboard activation still fires plain click → toggle. */
    const VOICE_HOLD_TO_TALK_MS = 350;

    function _wireVoiceHoldToTalk(card, index) {
        const button = card.querySelector(`[data-terminal-voice="${index}"]`);
        const control = card.querySelector(`[data-terminal-voice-control="${index}"]`);
        if (!button || !control) {
            return;
        }

        let holdTimer = null;
        let holdActive = false;
        let holdStarting = false;
        let holdStopRequested = false;
        let suppressClick = false;

        const clearHoldTimer = () => {
            if (holdTimer !== null) {
                window.clearTimeout(holdTimer);
                holdTimer = null;
            }
        };

        button.addEventListener('pointerdown', event => {
            if (event.button !== 0 || button.disabled || _voiceState[index]?.recording) {
                return;
            }
            try { button.setPointerCapture(event.pointerId); } catch (_) {}
            holdStopRequested = false;
            clearHoldTimer();
            holdTimer = window.setTimeout(async () => {
                holdTimer = null;
                holdActive = true;
                suppressClick = true;
                holdStarting = true;
                try {
                    if (_voiceActiveIndex !== -1 && _voiceActiveIndex !== index) {
                        await _stopVoice(_voiceActiveIndex);
                    }
                    if (!_voiceState[index]?.recording) {
                        await _startVoice(index);
                    }
                } finally {
                    holdStarting = false;
                }
                if (holdStopRequested) {
                    holdStopRequested = false;
                    if (_voiceState[index]?.recording) {
                        await _stopVoice(index);
                    }
                }
            }, VOICE_HOLD_TO_TALK_MS);
        });

        const endHold = async () => {
            clearHoldTimer();
            if (!holdActive) {
                return;
            }
            holdActive = false;
            if (holdStarting) {
                /* release raced the async start — stop as soon as it settles */
                holdStopRequested = true;
                return;
            }
            if (_voiceState[index]?.recording) {
                await _stopVoice(index);
            }
        };
        button.addEventListener('pointerup', endHold);
        button.addEventListener('pointercancel', endHold);

        /* A completed hold already stopped capture on release; swallow the
           click that follows pointerup so it cannot re-toggle recording.
           Capture phase on the wrapper runs before the button's listener. */
        control.addEventListener('click', event => {
            if (suppressClick) {
                suppressClick = false;
                event.preventDefault();
                event.stopPropagation();
            }
        }, true);
    }

    /* ── Push-to-talk ── */
    let _pttActive = false;
    let _pttProcessing = false;
    let _pttStopRequested = false;

    function _matchesPttKeybind(event, keybind) {
        if (!keybind) return false;
        const parts = keybind.split('+');
        const targetKey = parts[parts.length - 1];
        const needsCtrl = parts.includes('Ctrl');
        const needsCmd = parts.includes('Cmd');
        const needsAlt = parts.includes('Alt');
        const needsShift = parts.includes('Shift');
        if (needsCtrl !== event.ctrlKey) return false;
        if (needsCmd !== event.metaKey) return false;
        if (needsAlt !== event.altKey) return false;
        if (needsShift !== event.shiftKey) return false;
        const eventKey = event.key.length === 1 ? event.key.toUpperCase() : event.key;
        return eventKey === targetKey;
    }

    function _pttKeybindReleasedBy(event, keybind) {
        if (!keybind) return false;
        const parts = keybind.split('+');
        const key = event.key;
        return (key === 'Control' && parts.includes('Ctrl'))
            || (key === 'Meta' && parts.includes('Cmd'))
            || (key === 'Alt' && parts.includes('Alt'))
            || (key === 'Shift' && parts.includes('Shift'))
            || (key.length === 1 ? key.toUpperCase() : key) === parts[parts.length - 1];
    }

    /* Push-to-talk targets the currently selected (focused) terminal only.
       When nothing is selected, voice goes nowhere — consistent with typing,
       and so a not-visibly-selected pane never silently receives dictation. */
    function _findPttTerminalIndex() {
        if (_focusedTerminalIndex !== -1
            && sessionIds[_focusedTerminalIndex]
            && terminals[_focusedTerminalIndex]) {
            return _focusedTerminalIndex;
        }
        return -1;
    }
    function _updateVoiceBtn(index, recording) {
        const btn = document.getElementById(`tvoice-${index}`);
        if (!btn) return;
        btn.classList.toggle('recording', recording);
        btn.title = recording
            ? 'Voice input (recording — click to stop)'
            : (_voiceStatusMessages[index]
                ? `Voice input (${_voiceStatusMessages[index]})`
                : 'Voice input (click to start recording)');
    }

    function _showVoicePreview(index, text) {
        const btn = document.getElementById(`tvoice-${index}`);
        if (!btn) return;
        let preview = btn.querySelector('.voice-partial-preview');
        if (!preview) {
            preview = document.createElement('span');
            preview.className = 'voice-partial-preview';
            btn.appendChild(preview);
        }
        preview.textContent = text;
    }

    function _clearVoicePreview(index) {
        const btn = document.getElementById(`tvoice-${index}`);
        if (!btn) return;
        const preview = btn.querySelector('.voice-partial-preview');
        if (preview) preview.remove();
    }
