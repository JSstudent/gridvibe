# Proposal: Voice Capture in the File Explorer Edit Mode

Source note: `docs/r&d/todos.txt` item 2 — "voice capture in edit mode in file explorer... (mic button, voice capture on keybind, like we have in the terminal now)".

Status: proposal, not started.
Date: 2026-08-09.

## 1. Goal

When a file is open in the explorer's in-place text editor (Source panel edit mode), the user can dictate text into the textarea:

- a **mic button** in the editor's control group, same look and behavior as the terminal mic (click to toggle, press-and-hold ≥ 350 ms for hold-to-talk, recording overlay, partial preview);
- a **keybind** — reuse the existing user-configured push-to-talk keybind (`voice_prefs.pttKeybind`) so it works while the editor textarea has focus;
- recognized final text is **inserted at the caret** in the textarea, not injected into any session.

## 2. Current state (what already exists)

- Full capture pipeline is pane-generic: `voice-capture-worklet.js` (16 kHz PCM16, 640-sample chunks), `_startVoice/_stopVoice` in `web/static/js/voice-input.js`, backend `voice_start`/`voice_audio`/`voice_stop` in `web/api.py` + `web/voice.py`. `voice_start` does **not** validate session type, so an explorer session id already works server-side.
- The mic button **already renders on explorer panes** (`buildPaneCard`, `web/static/js/terminals.js:5077`) — but recording is silently useless there: the `voice_result` handler (`terminals.js:7973`) calls `_sendToTerminal`, which emits `terminal_input` for the explorer session id, and the backend drops it with a DEBUG "not connected" (`web/api.py:3118`). Today this is a record-to-nowhere button — a latent bug this proposal also fixes.
- Editor insertion pattern already exists: `handleExplorerEditKeydown` (`web/static/js/explorer-editor.js:275`) uses `textarea.setRangeText(..., 'end')` + `handleExplorerEditInput(index)` for Tab. Dictation reuses exactly this.
- Editor controls already have an injection point: `explorerEditorControlsHtml(index)` / `refreshExplorerEditControls` (`explorer-editor.js:95/99`) render the Edit/Save/Cancel group in the file header; CSS `.explorer-editor-action-btn` exists (terminals.css:3187–3230).
- Constraints from `docs/voice_guideline.md`: one recorder per page; prefs machine-local/user-global; direct injection is the documented delivery model (an editor is a natural place to revisit it — see §5).

## 3. Proposed design

### 3.1 Routing — the core change (small)

Branch the single `voice_result` fan-out in `terminals.js`:

- If the pane is an explorer pane with an active `_explorerEdit` state → insert final text into the textarea via `setRangeText(text, selStart, selEnd, 'end')` + `handleExplorerEditInput(index)`; partials → existing `.voice-partial-preview` bubble on the mic button (no incremental textarea writes; with the default Whisper engine there are no partials anyway).
- Else → existing `_sendToTerminal` path, unchanged.

Because `voice_result` is keyed by `session_id` and explorer session ids already live in `sessionIds`, `_voiceIndexForSession` resolves the pane with no new plumbing.

### 3.2 Mic button placement

Two coordinated changes:

1. **Pane-header mic becomes edit-aware.** The already-rendered explorer-pane mic starts capture keyed to the explorer session id; results route per §3.1. When no edit session is active on that pane, the button is disabled with a tooltip ("Open a file for editing to dictate") — it must not record-to-nowhere as it does today.
2. **Editor-header mic.** Add a mic button to `.explorer-editor-actions` via `refreshExplorerEditControls`, reusing `VOICE_MIC_ICON`, the recording overlay, and the disabled/unavailable CSS classes. It shares `_voiceState[index]` with the pane-header mic so the two can never disagree.

Keep `setExplorerEditChromeDisabled` (`explorer-editor.js:138`) from sweeping the mic into its disable list — an in-flight recording must remain stoppable, same rule as zoom/wrap.

### 3.3 Keybind

The existing PTT listeners (`terminals.js:6900/6927`) target `_findPttTerminalIndex()`, which only resolves for plain terminal cards — focus in the editor textarea clears `_focusedTerminalIndex` (`terminals.js:5437`), so PTT can never fire while editing. Extend targeting, not the keybind:

- Generalize the target resolver: if the focused element is an explorer edit textarea (or `_activeExplorerIndex` has an active `_explorerEdit`), PTT toggles/holds capture for that explorer pane.
- Keep the same configured keybind and the one-recorder-per-page rule (`_voiceActiveIndex` already stops any active capture before starting another).
- Add the editable-target guard review: the PTT handler currently has none; for the editor this is deliberate (the keybind must fire while the textarea is focused), matched by `_matchesPttKeybind` exact-combo semantics so normal typing is unaffected.

### 3.4 Backend

No changes. `voice_start` accepts any session id; per-session-id server state means an editor capture cannot collide with a terminal capture on another pane; results are emitted only to the originating socket connection.

## 4. Alternatives considered

- **Mic only in the editor header, hide it on the pane header for explorer panes.** Cleaner conceptually, but the pane-header mic is rendered unconditionally for every pane; removing it for explorers is a special case in `buildPaneCard`, and keeping it (edit-aware) gives a bigger click target consistent with terminals. Rejected as the sole surface; the pane mic stays but becomes edit-aware.
- **Separate editor keybind config.** More settings surface for no real gain; the user's muscle memory is the existing PTT keybind. Rejected.
- **Draft-review step before insertion** (transcript lands in a staging bubble, user confirms). Deviates from the documented direct-injection model; the textarea itself is already a review surface with undo (Ctrl+Z) and dirty tracking, and cancel-with-confirm protects the file. Rejected; noted in the guideline update.

## 5. Edge cases and rules

- **Exit edit mode while recording** (Esc / Save / Cancel): stop capture first — `exitExplorerEditMode`/`cancelExplorerEdit`/save call `_stopVoice(index)` before rebuilding the view; a late `voice_result` for a pane without active `_explorerEdit` is dropped (not sent to `terminal_input`).
- **One recorder per page stays.** Starting editor dictation while a terminal records stops the terminal capture (existing `_toggleVoice` behavior), and vice versa.
- **Vosk partials** render only in the preview bubble; only `final` text touches the textarea. **Whisper** (shipped default) inserts one block on stop.
- **Backend unavailable**: existing `_startVoice` preflight (`/api/voice-status` + toast) covers the editor path unchanged.
- **Insertion semantics**: insert at caret replacing any selection, caret ends after inserted text (`setRangeText(..., 'end')`); `handleExplorerEditInput` updates draft/dirty/Save state, so dictated text is undoable and guarded by the same revision-checked `PUT /api/explorer/<id>/file` save.
- **No new persistence**: voice prefs stay in `voice_prefs`; nothing voice-related enters explorer state or saved presets.

## 6. Implementation outline

1. `web/static/js/terminals.js` — branch `voice_result` handler on explorer-edit state; generalize PTT target resolver; make pane-header mic edit-aware/disabled-when-not-editing.
2. `web/static/js/explorer-editor.js` — mic button in `explorerEditorControlsHtml`/`refreshExplorerEditControls`; stop-capture on exit/save/cancel; keep mic out of `setExplorerEditChromeDisabled`'s disable list.
3. `web/static/js/voice-input.js` — expose the small helpers the editor path needs (e.g. an insertion-target-aware `_toggleVoice`), keep one-recorder rule.
4. CSS — reuse `.voice-btn`/overlay/preview classes; only add editor-group placement rules if needed (tokens only).
5. `docs/voice_guideline.md` — annotate the Transcript Delivery section: editor dictation inserts at caret; direct-injection model unchanged in spirit.
6. Tests:
   - Backend: none expected (no API change); run existing voice suites.
   - Frontend behavioral: if routing logic is extracted DOM-free (preferred — follow the `session-persistence.js`/`explorer-persistence.js` `require()`-able pattern), add Node behavioral tests for "final result → editor insert vs terminal inject" and "late result after edit exit → dropped". No new raw source-text assertions.
7. `CHANGELOG.md` entry on completion.

## 7. Effort estimate

Small-to-medium. No backend work, no new config, no new dependencies. The bulk is frontend wiring in three JS files plus tests. Main risks are focus/keybind subtleties (§3.3) and the stop-on-exit ordering (§5), both covered by the existing `_stopVoice` barrier.
