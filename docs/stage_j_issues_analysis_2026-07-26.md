# Stage J Verification Issues — Analysis and Fix Plan (2026-07-26)

Issues found during **Stage J — Verify the release** of
`docs/release_and_installer_plan_2026-07-25.md` (notes in `docs/r&d/todos.txt`).
This document records the root-cause analysis and the proposed fix plan.

Issues covered:

1. **Browser-mode local terminals fail without pywinpty** — see `docs/browser_terminal_issue.png`.
2. **Voice silently does nothing when dependencies were declined** at first launch.
3. **Push-to-talk shortcut changes need a full restart** to take effect in open tabs.

---

## Issue 1 — Browser-mode local terminals fail without pywinpty

### Symptom

Start via `GridVibe.bat`, choose **Browser** mode, open a regular local terminal (cmd):
the pane shows *"Connection Error — Interactive Windows local terminals require pywinpty.
Install desktop dependencies with `pip install -r requirements-desktop.txt`."*
(screenshot: `docs/browser_terminal_issue.png`).

### Root cause

- The error is raised in `web/terminal_io.py:986-990` (`_connect_local_session()`):
  on Windows (`os.name == "nt"`), if `WinPtyProcess` is `None`
  (guarded import at `web/terminal_io.py:57-60`), a `RuntimeError` is raised.
- **All** local Windows sessions — cmd, PowerShell, *and* WSL panes — spawn through
  `WinPtyProcess.spawn()` (`web/terminal_io.py:992-993`). There is no fallback, and
  there can't be a cheap one: interactive Windows consoles need a ConPTY, which is
  exactly what pywinpty wraps. POSIX uses stdlib `pty.openpty()`
  (`web/terminal_io.py:1000-1023`), which is why Linux/macOS don't need it.
  In browser mode on Windows, only SSH and explorer sessions work without pywinpty.
- The real bug is **filing**: `pywinpty` lives in `requirements-desktop.txt:7`, and
  `GridVibe.bat:163-166` skips the desktop-install block entirely when Browser is
  chosen. But pywinpty is a **backend** dependency — only `pywebview` is actually
  native-window-only (`web/webview_launcher.py:34-36` guards its import; browser
  mode never imports it).
- The behavior is pinned by `tests/test_api.py:7990`
  (`test_connect_local_session_requires_pywinpty_on_windows`) — keep the guard
  and the error as the last-resort safety net; the fix is to make it unreachable
  in practice.
- Already flagged in the release plan (`release_and_installer_plan_2026-07-25.md:77`,
  `:1268-1271`), and `README.md:68` actively contradicts the code by framing
  desktop requirements as native-window-only.

### Error surface (for reference)

Exception → `web/terminal_io.py:1047-1052` sets `SessionStatus.ERROR` →
`_broadcast_session_status()` emits `session_status` →
`web/static/js/terminals.js:6914` handler → `showPlaceholderError()` →
`showPlaceholderRetryState()` (`terminals.js:6410-6434`) renders "Connection Error"
with a "Retry connection" button wired to `POST /api/sessions/<id>/reconnect`.

### Fix — re-file pywinpty as a core dependency (**implemented 2026-07-26**)

Implemented as proposed: `pywinpty>=3.0.5; platform_system == "Windows"` moved from
`requirements-desktop.txt` into `requirements.txt`; `GridVibe.bat`'s core import check
verifies `winpty` and its repair path reinstalls `pywinpty`; the backend error message
(`web/terminal_io.py:988-990`) and `README.md` now point at the core requirements.
Verified on Ubuntu/Linux: the platform marker makes pip skip the line entirely —
`GridVibe.sh`, the POSIX `pty` path, and both CI matrix OSes are unaffected.

Original proposal:

- Move `pywinpty>=3.0.5; platform_system == "Windows"` from
  `requirements-desktop.txt` into `requirements.txt` (the platform marker makes it
  a no-op on Linux/macOS).
- In `GridVibe.bat`: add `winpty` to the core import check (`:127`); the desktop
  verify block (`:184`) can keep or drop its `winpty` re-check (harmless either way).
- Fix `README.md:68` wording and the stale references in the release plan doc.
- This fixes browser mode, `python main.py`, and manual installs in one move.
  (Bat-only install was considered and rejected: it leaves `python main.py`
  broken on Windows.)

---

## Issue 2 — Voice silently does nothing when dependencies were declined

### Symptom

First launch via `GridVibe.bat`, decline voice dependencies. The app starts
(default config has voice enabled), the user can enable voice input in settings
and click the mic button — but nothing happens. The only recovery is closing
everything, re-running `GridVibe.bat`, and answering yes to the voice prompt.

### Root cause

- Availability is decided by **module-level imports** at `web/voice.py:26-39`
  (`websocket-client`, `numpy`, `faster_whisper`). Capability flags:
  `_vosk_engine_available()` (`web/voice.py:44-46`),
  `_whisper_engine_available()` (`web/voice.py:77-79`). Installing packages into
  the venv later never re-runs those imports — only a process restart fixes it.
- The backend *does* expose availability: `GET /api/voice-status`
  (`web/api.py:2070-2099`) returns `engine_available` and a user-facing
  `status_message` (`_voice_engine_unavailable_message()`, `web/voice.py:82-105`).
- But the frontend ignores it:
  - The launcher settings modal (`templates/index.html:304-410`, handled by
    `web/static/js/launcher.js`) **never fetches `/api/voice-status`**, so the
    "Enable voice input" toggle is neither gated nor annotated.
  - The mic button is only gated on `enabled`, never `engine_available`
    (`voice-input.js:342-360`, `_setVoiceBtnsDisabled`).
  - On mic click, `_startVoice()` (`voice-input.js:362`) does detect the problem
    (`voice-input.js:380-390`) and builds a good message
    (`_voiceBackendUnavailableMessage()`, `voice-input.js:254-270`) — but
    `_setVoicePanelStatus()` (`voice-input.js:272-278`) only writes it to the
    button's `title` tooltip. Nothing visible happens. (The diagnostics
    renderers at `voice-input.js:204-210` now return strings that all callers
    discard.)
- If the gate were bypassed, the backend would emit a `voice_status` error
  (`web/api.py:2122-2148` → `web/voice.py:333-336` / `:415-426`) — backend
  logging exists; the frontend swallows the pre-flight case.
- No runtime install path exists: `GridVibe.bat:203-248`
  (`:check_voice_dependencies`) is the only installer and runs before server start.
  A restart mechanism exists but only for native mode (`restart_application()`,
  `web/webview_launcher.py:795-807`; frontend wrapper `restartApplication()`,
  `launcher.js:2635-2687`, degrading to "restart manually" text in browser mode).

### Proposed fix — availability UX + recovery without a hard reset

1. **Gate:** fetch `/api/voice-status` when opening the launcher settings modal
   (`openAppSettings`, `launcher.js:858-871`); show `status_message` in
   `#appVoicePrefsStatus` (`index.html:410`); mark/disable the voice toggle and
   the mic button when `engine_available === false`.
2. **Feedback:** on mic click with an unavailable backend, show the message
   visibly (toast / `openGenericConfirmModal` — guardrail: no native
   `alert`/`confirm`) instead of tooltip-only.
3. **Recovery (can be deferred):** add a guarded
   `POST /api/install-voice-deps` endpoint that runs
   `sys.executable -m pip install -r requirements-voice.txt`, then prompt
   "restart now" — native mode reuses the existing restart bridge
   (`webview_launcher.py:795`, `launcher.js:2635`); browser mode instructs a
   manual restart.

Steps 1+2 alone already kill the silent-failure confusion; step 3 removes the
"close everything and re-run the bat" dance.

---

## Issue 3 — Push-to-talk shortcut change needs a full restart

### Symptom

With voice deps installed and working: changing the push-to-talk shortcut in
settings and saving does **not** take effect in already-open session/terminal
tabs until a full restart. Enabling voice itself *does* appear to propagate live.

### Root cause

- Open terminal tabs learn about config changes via a Socket.IO
  `app_config_updated` broadcast (`_broadcast_app_config_update`,
  `web/api.py:332-355`, handled at `terminals.js:6954-6956`) plus a
  `BroadcastChannel`/`localStorage` fallback (`launcher.js:814-840` →
  `terminals.js:245-264`). **But the payload carries only
  `appearance.theme`, `workspace.surface_mode`, and `terminal.font_*`** — no
  `voice_input`, no voice prefs. `POST /api/voice-prefs` (`web/api.py:2108-2119`)
  emits nothing at all.
- `_voicePrefs` is loaded **once per page load**: localStorage at module init
  (`voice-input.js:45`) and from the server at boot (`terminals.js:7047` →
  `_loadVoicePrefsFromServer()`, `voice-input.js:96-115`).
- The PTT key handlers (`terminals.js:6046-6092`) already read
  `_voicePrefs.pttKeybind` **at event time** — the binding is dynamic, but the
  object it reads is stale. Nothing re-fetches `/api/voice-prefs` after save.
- Voice *enable* only appears to propagate because open tabs re-fetch
  `/api/voice-status` on window `focus`/`pageshow` (`terminals.js:7031-7038`),
  which un-hides the mic controls (`voice-input.js:231-236`).

### Proposed fix — live-apply voice prefs

- Emit a `voice_prefs_updated` Socket.IO event from `POST /api/voice-prefs`
  (and/or include voice prefs in the existing broadcast payloads).
- Handle it in `terminals.js` (next to `terminals.js:6954`) by re-running
  `_loadVoicePrefsFromServer()` — no re-binding needed since the key handlers
  read at event time.
- Belt-and-braces: also re-fetch prefs in the existing `focus`/`pageshow`
  handler (`terminals.js:7031-7038`), matching how voice-enable propagates.

---

## Implementation order

| # | Fix | Size | Risk |
|---|-----|------|------|
| 1 | Re-file pywinpty into `requirements.txt` (+ bat check, README/doc wording) | Small | Low — no code changes |
| 2 | Live-apply voice prefs (`voice_prefs_updated` event + re-fetch) | Small | Low |
| 3.1–3.2 | Voice availability gating + visible feedback | Medium | Low |
| 3.3 | Runtime `install-voice-deps` endpoint + restart affordance | Medium | Medium — new write endpoint, must respect same-origin write guard and guardrails #5/#8 |

Each fix is independently shippable; 1–3.2 are candidates for the current
release, 3.3 can be deferred without leaving confusing behavior behind.
