# Voice Guideline

This document is the canonical reference for GridVibe voice input as it exists in the current codebase.

It replaces the older working notes that previously lived in `docs/audio/`. Those documents are still useful as historical research, but they should now be treated as archived background material, not the source of truth.

## Purpose

Use this file when you need to:

- understand the end-to-end voice path
- change voice UI or browser capture behavior
- change the speech-to-text backend
- debug why voice works differently in browser mode versus `pywebview`
- avoid reintroducing already-solved race conditions or stale assumptions

## Scope

In GridVibe, "voice" means speech-to-text for terminal input.

- The browser captures microphone audio.
- Audio is normalized into 16 kHz mono PCM through an `AudioWorklet`.
- The backend routes that audio to the configured engine.
- Final transcript text is injected into the terminal as typed input.
- Raw microphone audio is not sent into the shell.

## Current Source Of Truth

Primary implementation files:

- `templates/terminals.html`
- `web/static/voice-capture-worklet.js`
- `web/api.py`
- `services/vosk_service.py`
- `web/webview_launcher.py`
- `default_config.json`

Verification coverage lives primarily in:

- `tests/test_api.py`

## Architecture Summary

There are two backend modes behind one frontend capture path:

1. `whisper`
   - Browser streams PCM chunks to Flask-SocketIO.
   - The backend buffers the session audio in memory.
   - `faster-whisper` runs once when recording stops.
   - Result model: final transcript only.

2. `vosk`
   - Browser streams PCM chunks to Flask-SocketIO.
   - Flask proxies each chunk to a per-session Vosk WebSocket connection.
   - Vosk can return partial and final text during the stream.
   - Result model: live partials plus final transcript.

At the UX level, both engines share the same microphone controls, push-to-talk settings, and diagnostics panel.

## Non-Negotiable Product Rules

These are current implementation rules and should be preserved unless deliberately changed:

- Voice is per terminal pane, but only one pane may record at a time.
- Pressing the terminal `Enter` shortcut stops active voice capture before sending `\r`.
- Voice preferences are machine-local and user-local, not part of saved launcher session presets.
- Browser mode is the preferred and more reliable microphone environment.
- `pywebview` support is best-effort, even with permission patches.

## Runtime Configuration

Machine-level voice configuration is loaded from `config.json`, with fallback to `default_config.json`.

Current committed defaults in `default_config.json` are:

```json
{
  "voice_input": {
    "enabled": true,
    "engine": "whisper",
    "vosk_service_url": "ws://localhost:2700",
    "vosk_service_port": 2700,
    "vosk_model": "vosk-model-en-us-0.22",
    "whisper_model": "small",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "language": "en-US"
  }
}
```

Important implication: the repo default is currently `whisper`, not `vosk`.

Machine-level voice config is surfaced in the launcher app settings modal and normalized in `web/api.py`.

Launcher-editable fields:

- `voice_input.enabled`
- `voice_input.engine`
- `voice_input.vosk_model`
- `voice_input.whisper_model`
- `voice_input.whisper_device`
- `voice_input.whisper_compute_type`
- `voice_input.language`

## Persisted Voice Preferences

Per-user voice capture preferences are persisted in two places:

1. Browser `localStorage`
   - Key: `gridvibe.voice.capture.v1`

2. Server-side `config.json`
   - Key: `voice_prefs`
   - Used to restore settings across browser sessions on the same machine

Persisted preference fields:

- `profile`
- `deviceId`
- `pttEnabled`
- `pttKeybind`
- `panelOpen`

These values are not scoped per terminal. They are shared across the terminal workspace.

## UI Contract

Voice controls live in each terminal header.

Per terminal, the UI includes:

- mic toggle button
- mic settings button
- voice panel
- live partial preview above the mic button
- status badge inside the panel

The panel exposes:

- capture profile selector
- microphone device selector
- microphone refresh button
- push-to-talk toggle
- push-to-talk keybind capture field
- requested versus actual capture diagnostics
- browser-supported-constraints and track capabilities diagnostics

The voice panel can auto-restore if it was left open previously.

## Frontend Capture Pipeline

The browser capture path is implemented entirely in `templates/terminals.html` plus `web/static/voice-capture-worklet.js`.

### Capture profiles

Two profile presets exist:

- `headset`
  - `echoCancellation: false`
  - `noiseSuppression: false`
  - `autoGainControl: false`
  - `channelCount: 1`

- `laptop`
  - `echoCancellation: true`
  - `noiseSuppression: true`
  - `autoGainControl: true`
  - `channelCount: 1`

If a specific microphone is selected, the request adds:

- `deviceId: { exact: <selected device> }`

### Capture startup

When recording starts, the frontend:

1. refreshes `/api/voice-status` and stops before opening the microphone if the configured backend is unavailable
2. checks that `getUserMedia` exists
3. checks that `AudioWorkletNode` exists
4. requests microphone access using the selected profile and optional device
5. falls back to the browser default device if the selected device is unavailable
6. in `pywebview`, may fall back to bare `{ audio: true }` if constrained capture fails
7. opens an `AudioContext`
8. loads `voice-capture-worklet.js`
9. creates `MediaStreamSource -> AudioWorkletNode -> muted GainNode -> destination`
10. emits `voice_start`
11. streams binary PCM packets through `voice_audio`

The muted gain node is intentional. The pipeline stays connected to the audio graph without feeding audible mic monitoring back to the user.

### Diagnostics

The UI records and displays:

- requested constraints
- actual track settings from `getSettings()`
- supported browser constraints from `getSupportedConstraints()`
- track capabilities from `getCapabilities()`
- worklet-reported source sample rate, target sample rate, and chunk size

This is important because browser media constraints are requests, not guarantees.

### Stop behavior

When recording stops, the frontend:

1. marks the session as no longer recording
2. flushes the worklet so any partial packet is emitted
3. emits `voice_stop`
4. disconnects nodes
5. stops media tracks
6. closes the `AudioContext`
7. preserves the last diagnostics snapshot in the panel

## Audio Format Contract

The worklet is the format boundary for all voice engines.

Input to worklet:

- Float32 samples
- browser-native sample rate
- mono capture path

Output from worklet:

- signed PCM int16
- 16,000 Hz target sample rate
- mono
- chunk size: 640 samples

That corresponds to about 40 ms per chunk at 16 kHz.

## AudioWorklet Responsibilities

`web/static/voice-capture-worklet.js` is responsible for:

- streaming resampling
- chunk framing
- conversion from Float32 to PCM int16
- flush-on-stop support
- reporting capture format back to the main thread

The resampler behavior is:

- copy if source sample rate already matches target
- weighted averaging when downsampling
- linear interpolation when upsampling

This is materially better than the older script-processor approach described in the archived notes.

## Backend Voice API Contract

REST endpoints:

- `GET /api/voice-status`
- `GET /api/voice-prefs`
- `POST /api/voice-prefs`

Socket.IO events from browser to server:

- `voice_start`
- `voice_audio`
- `voice_stop`

Socket.IO events from server to browser:

- `voice_status`
- `voice_result`

`voice_result` payload:

- `session_id`
- `text`
- `final`

`voice_status` payload commonly includes:

- `session_id`
- `status`
- `message`

Expected statuses today:

- `listening`
- `stopped`
- `error`

## Whisper Engine Guideline

The `whisper` path is implemented inside `web/api.py`.

Behavior:

- lazily loads a singleton `WhisperModel`
- buffers raw PCM bytes per session in `_whisper_audio_buffers`
- does not emit live partials
- transcribes once on `voice_stop`
- maps language tags like `en-US` to `en`
- uses `vad_filter=True`
- uses `beam_size=1` and `best_of=1`
- joins returned segment text into one final string

Design consequence:

- `whisper` is optimized for simple local final transcription, not live dictation feedback

If live partials become a requirement, this engine path will need architectural changes instead of only UI changes.

## Vosk Engine Guideline

The `vosk` path uses two pieces:

1. Flask-side session/proxy logic in `web/api.py`
2. standalone WebSocket recognizer service in `services/vosk_service.py`

### Flask-side Vosk behavior

The backend:

- checks whether the service is already reachable
- starts `services/vosk_service.py` on demand if needed
- waits for the service to accept a real WebSocket handshake
- stores one WebSocket connection per session
- guards per-session send/recv with a lock
- sends `{"config": {"sample_rate": 16000}}` before audio
- forwards binary PCM chunks
- relays partial or final Vosk messages as `voice_result`
- sends `{"eof": 1}` on stop to flush the final result
- can restart the service if the first connection attempt fails

### Vosk service behavior

The Vosk service:

- loads the configured model once on startup
- listens on `localhost` WebSocket port `2700` by default
- creates a recognizer per client session
- accepts optional config messages
- accepts binary PCM audio frames
- returns JSON `partial` or `text` payloads
- returns a final flush after EOF

## Concurrency And Safety Rules

There are a few important implementation details here that should not be casually removed:

- `_vosk_lock` guards the session WebSocket registry.
- `_vosk_session_locks` serialize `send` and `recv` per Vosk voice session.
- `_vosk_process_lock` isolates subprocess lifecycle operations.
- old or leaked Vosk WebSocket handles are explicitly closed before replacement.
- the frontend disables mic buttons on other terminals while one terminal is recording.

These behaviors exist because voice start/stop and proxy I/O had race conditions before the current locking and cleanup logic.

## Native `pywebview` Guideline

`pywebview` is supported, but not treated as the primary mic environment.

Current desktop support includes:

- preference for Edge Chromium on Windows
- `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` setup with media flags
- WebView2 permission patching for microphone and camera access

Important caveat:

- Even with those patches, some managed Windows environments can still block hardware audio access.
- Browser mode remains the expected fallback when microphone capture fails in `pywebview`.

Do not remove the warning language in the UI unless native capture reliability materially changes.

## Transcript Delivery Guideline

Transcript injection behavior is intentionally simple today:

- Vosk partials show only in the preview bubble.
- Final transcript text is sent directly into the terminal input stream.
- There is no draft-review or confirm-before-send step.

That means voice can dictate shell commands directly into the live terminal input.

If this changes in the future, the product decision should be documented clearly because it alters both safety expectations and user workflow.

## Verified Behavior In Tests

The existing test suite already covers key voice behavior. That coverage should be preserved or expanded when refactoring.

Notable verified areas:

- terminals page exposes voice controls, profiles, diagnostics, and worklet wiring
- push-to-talk controls are present in the rendered UI
- `voice-status` reports engine, model, and language correctly
- `voice-prefs` defaults and persistence behavior
- Whisper flow buffers audio and emits a final transcript on stop
- Vosk session startup stores connections safely under lock
- Vosk startup retry closes failed or leaked connections correctly
- Vosk audio proxy handles closed WebSocket races without crashing
- Enter shortcut stops active voice before sending newline

## Known Constraints

These are current design constraints, not necessarily bugs:

- only one active recorder across all terminals
- voice preferences are shared globally, not per terminal or per session group
- `whisper` only emits final text on stop
- Vosk proxying is still chunk-send then response-read, not a full duplex event pump
- transcript text is injected directly into the terminal
- voice is browser-first; desktop embedded mode is less reliable
- the capture path depends on `AudioWorklet`; older browsers without it will not use the upgraded path

## Change Guidelines

If you modify voice behavior, keep these rules in mind:

- Treat `templates/terminals.html`, `web/static/voice-capture-worklet.js`, and `web/api.py` as one coordinated system.
- Any change to audio format must preserve or intentionally update the 16 kHz PCM contract for both engines.
- If you change UI wording around mic reliability, verify it still matches real `pywebview` behavior.
- If you change engine semantics, update both `/api/voice-status` and the terminal panel summary text.
- If you touch Vosk session lifecycle, keep the lock discipline and leaked-handle cleanup.
- If you add a new backend, decide explicitly whether it behaves like Vosk streaming or Whisper batch-finalization.
- If you make voice safer with a draft-confirm flow, document how that affects current direct terminal injection.

## Historical Material

Historical research notes were excluded from the public repository. Treat this file as the source of truth for current voice behavior.
