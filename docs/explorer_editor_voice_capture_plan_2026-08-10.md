# Implementation Plan: Voice Capture in the File Explorer Edit Mode

Implements `docs/explorer_editor_voice_capture_proposal_2026-08-09.md`.

Status: plan, not started.
Date: 2026-08-10.

Scope discipline for this plan: **frontend only, additive, no backend change, no
new config key, no new persisted state, no new dependency.** Every change is
either a new function in an existing domain file, a new `require()`-able pure
module, or a one-line hook at an existing choke point. Nothing on the terminal
dictation path changes behavior.

---

## 1. Corrections to the proposal (found by reading the code)

The proposal is sound in its goal and in most of its mechanics. Four points must
be corrected before implementing, and they simplify the work rather than
enlarging it.

### 1.1 The pane-header mic is not visible on explorer panes

The proposal's §2 and §3.2 rest on "the mic button already renders on explorer
panes … a record-to-nowhere button — a latent bug this proposal also fixes."
The button is in the DOM, but `web/static/css/terminals.css:1743-1748` hides it:

```css
.explorer-pane [data-terminal-clear],
.explorer-pane .voice-control,
.browser-pane [data-terminal-clear],
.browser-pane .voice-control {
    display: none;
}
```

So on an explorer pane the header mic is unclickable, and push-to-talk cannot
reach it either: `_findPttTerminalIndex()` (`voice-input.js:1019`) requires
`_focusedTerminalIndex`, which is only ever set for a *plain* terminal card
(`terminals.js:5337`, guarded by `isPlainTerminalCard`). There is no reachable
record-to-nowhere path today.

**Consequence — this is the single biggest blast-radius reduction available:**

- **Drop §3.2 item 1 entirely.** The pane-header mic stays hidden on explorer
  panes; the CSS rule above is not touched. No `buildPaneCard` change, no
  edit-aware pane-header mic, no shared-state-between-two-mics problem, no new
  disabled-with-tooltip state on a control the user cannot see.
- **The editor-header mic (§3.2 item 2) is the only dictation surface**, and it
  exists *only while an edit session is active* — it is rendered by
  `refreshExplorerEditControls` in the `state` branch, beside Save and Cancel.
  "No editor, no mic" replaces "mic disabled with a tooltip", which is both
  simpler and impossible to get wrong.
- The record-to-nowhere concern is still closed, defensively, by a one-line
  guard in the `voice_result` fan-out (§4.3, R5).

### 1.2 `setRangeText` does not feed the native undo stack

Proposal §5 claims dictated text is "undoable (Ctrl+Z)" because the Tab handler
pattern is reused. In Chromium (and therefore WebView2), `setRangeText` mutates
the value *without* pushing an undo entry, so a `Ctrl+Z` after dictation would
jump past the dictated block to whatever was typed before it. For a Tab
character that is a tolerable quirk; for a paragraph of dictation it is a
data-loss-shaped surprise.

**The insertion helper therefore tries `document.execCommand('insertText', false, text)`
first** — it preserves the native undo stack and fires a real `input` event —
and falls back to `setRangeText(text, start, end, 'end')` + an explicit
`handleExplorerEditInput(index)` when `execCommand` is unavailable, returns
`false`, or throws (it requires the textarea to hold focus). See §4.4.

### 1.3 Saving and dictating must not overlap

Not covered by the proposal. `saveExplorerEdit` posts `state.draft` as captured
at request time and, on success, `clearExplorerEditState` + a view rebuild. A
transcript that lands between those two points is written into a buffer that is
about to be discarded, and the on-disk revision has already advanced — the
dictated words disappear with no error anywhere. The fix is the small state
machine in §3: **Save is unavailable while a capture is bound to that editor,
and a transcript is refused while `state.saving` is true.**

### 1.4 Stale references

`_findPttTerminalIndex`, `_matchesPttKeybind`, `_toggleVoice`, and `_stopVoice`
live in `web/static/js/voice-input.js`, not `terminals.js` (the proposal's §3.3
attributes them to `terminals.js`; only the two top-level PTT key listeners
remain there, at `terminals.js:6900` and `terminals.js:6927`). The remaining
line references in the proposal check out.

---

## 2. Design invariants

These are the rules the implementation must make true. Each one maps to a test
in §7.

| # | Invariant |
|---|---|
| **I1** | Dictated text reaches exactly one place: the textarea of the edit session that started the capture. It is never emitted as `terminal_input`. |
| **I2** | A transcript is applied only if the *same* edit session is still open (same pane, same edit epoch) and is not saving. Otherwise it is dropped and reported once. |
| **I3** | One recorder per page, unchanged. Editor capture and terminal capture use the same `_voiceState` / `_voiceActiveIndex` machinery and stop each other exactly as two terminals do today. |
| **I4** | Leaving edit mode by any route stops the capture. There is one choke point for this, not one hook per exit path. |
| **I5** | Save is unavailable while dictation is in flight, and dictation is refused while a save is in flight. Neither can silently overwrite the other. |
| **I6** | A failed start, a socket drop, a backend error, or a transcript that never arrives all return the editor to a normal, savable state within a bounded time. |
| **I7** | Nothing voice-related is persisted: not in the tab record, not in saved presets, not in `runtime_state.json`, not in `localStorage`. `pane._explorerEdit` is already transient-only and stays that way. |
| **I8** | The terminal dictation path produces byte-identical behavior to today when no explorer editor is involved. |

---

## 3. The state machine

One new field on the existing transient edit state, created in
`enterExplorerEditMode` (`explorer-editor.js:191`):

```js
pane._explorerEdit = {
    …existing fields…,
    voice: null            // null | { epoch, phase, settleTimer }
};
```

`epoch` is a monotonic counter, incremented on every bind, so a late transcript
from a previous capture on the same pane cannot be mistaken for the current one.
`phase` is `'recording'` or `'settling'`.

```
        (no capture)
             │  _startVoice(index) succeeded AND this pane has an active edit
             ▼
    voice = { epoch: n, phase: 'recording' }        ← Save disabled
             │  _stopVoice(index) completed (mic click, hold release,
             │  PTT keyup, backend error, teardown, _stopAllVoice)
             ▼
    voice = { epoch: n, phase: 'settling' }         ← Save still disabled
             │            │
   final result arrives   │  settle timer fires (VOICE_SETTLE_MS = 5000)
   → insert, then clear   │  → clear, no insert
             ▼            ▼
        voice = null                                 ← Save available again
```

`clearExplorerEditState(index)` (`explorer-editor.js:28`) clears `voice` and
cancels the settle timer as part of dropping the edit — that single existing
function is invariant **I4**'s choke point (see §4.2).

Why a `settling` phase rather than clearing on stop: with the shipped Whisper
engine the transcript is produced *after* `voice_stop`, so the window between
"user released the key" and "the words exist" is exactly where a `Ctrl+S` would
lose them. Holding Save for that bounded window is the whole point. The timer
guarantees the hold is bounded even if the result never arrives (I6).

---

## 4. Changes, file by file

Five files. Roughly 200 added lines of implementation, no rewrites.

### 4.1 New file — `web/static/js/voice-dictation.js` (DOM-free, `require()`-able)

Follows the `session-persistence.js` / `explorer-persistence.js` UMD pattern so
`tests/` can execute it in Node instead of asserting source text (guardrail:
*prefer behavioral tests, no raw JS source-text assertions*). It holds every
decision that has a right and a wrong answer, and touches no DOM:

```js
// Where does this transcript go?
resolveVoiceDelivery({ hasTerm, edit, epoch, isFinal, hasText })
    → { target: 'editor' | 'terminal' | 'preview' | 'drop', reason }

// What exactly gets inserted, and where does the caret end up?
composeDictationInsert({ before, after, selected, text })
    → { text, spacedBefore }   // pure string math, no side effects
```

`composeDictationInsert` owns one small piece of dictation polish: when the
character immediately before the caret is a non-whitespace, non-opening-bracket
character and the transcript does not already start with whitespace or closing
punctuation, a single space is prepended, so dictating into the middle of a line
yields `hello world`, not `helloworld`. It never inserts a newline and never
trims the user's own buffer.

`resolveVoiceDelivery` returns `'drop'` with a machine-readable `reason`
(`'no-edit'`, `'epoch-mismatch'`, `'saving'`, `'no-target'`) — the reason is what
the caller turns into the single user-facing toast, and what the tests assert.

Registered in `templates/terminals.html` **before** `voice-input.js` (it has no
dependencies), and **not** added to `templates/index.html` — the launcher has no
explorer panes and does not load `voice-input.js` today.

### 4.2 `web/static/js/explorer-editor.js` — owns the editor voice surface

All new code goes in one clearly delimited section at the end of the file
("Editor dictation"), plus four small touches to existing functions.

**New functions:**

| Function | Responsibility |
|---|---|
| `explorerEditorVoiceHtml(index)` | The mic button markup, rendered only in `refreshExplorerEditControls`'s `state` branch. Classes `explorer-editor-action-btn voice-btn explorer-editor-voice-btn`, `id="explorer-voice-${index}"`, **and `data-terminal-voice="${index}"`** so the existing `_setVoiceBtnsDisabled` sweep governs it for free. Wrapped in `<span class="explorer-editor-voice">` — deliberately **not** `.voice-control`, which `terminals.css:1744` hides inside `.explorer-pane`. Icon: the existing `VOICE_MIC_ICON`. |
| `wireExplorerEditorVoice(index)` | Click → `_toggleVoice(index)`; press-and-hold → the extracted hold-to-talk wiring (§4.5). Called from `wireExplorerEditControls`, so it is re-established on every re-render. |
| `syncExplorerEditorVoiceButton(index)` | Re-applies `.recording`, `disabled`, and title from live `_voiceState` after a re-render, then defers to `_setVoiceBtnsDisabled(_voiceActiveIndex)` for the cross-pane rule. |
| `explorerEditorVoiceTargetIndex()` | The PTT resolver (§4.6). Returns the pane index whose edit textarea holds focus and has an active edit, else `-1`. |
| `explorerEditorNoteVoiceStarted(index)` | Hook called from `_startVoice`'s success tail. Binds `state.voice = { epoch: ++counter, phase: 'recording' }` when that pane has an active edit; a no-op otherwise (so terminals are untouched). Refreshes the controls so Save goes disabled. |
| `explorerEditorNoteVoiceStopped(index)` | Hook called from `_stopVoice`'s tail. Moves `recording → settling` and arms the settle timer. A no-op when nothing is bound. |
| `deliverExplorerDictation(index, text)` | Applies `resolveVoiceDelivery` + `composeDictationInsert`, performs the insertion (§4.4), clears `voice`, refreshes controls. |
| `explorerEditorVoiceSettleExpired(index)` | Timer callback: clears `voice`, refreshes controls, toasts once if a transcript was expected and never arrived. |
| `isExplorerDictationTarget(index)` | Cheap predicate for the `voice_result` branch: pane has an active edit with a bound `voice`. |

**Touches to existing functions (one or two lines each):**

- `enterExplorerEditMode` — add `voice: null` to the state literal.
- `clearExplorerEditState` — cancel the settle timer, and if a capture is still
  bound, fire-and-forget `_stopVoice(index)`. This is **I4**: every teardown route
  already funnels here (`exitExplorerEditMode`, `confirmDiscardExplorerEdit`,
  `confirmDiscardAllExplorerEdits`, `onExplorerSaveSuccess`,
  `explorerReloadEditedFile`), so no exit path needs its own stop call.
  `_stopVoice` sets `state.recording = false` synchronously before its first
  `await`, so the audio pipeline goes quiet in the same tick even though the
  promise is not awaited here (`clearExplorerEditState` is synchronous and must
  stay so — several callers are synchronous).
- `refreshExplorerEditControls` — render the mic in the `state` branch; the
  Save button gains `disabled` while `state.voice` is set, with the title
  `Stop dictation to save`.
- `wireExplorerEditControls` — call `wireExplorerEditorVoice(index)` and
  `syncExplorerEditorVoiceButton(index)`.
- `saveExplorerEdit` — an early return when `state.voice` is set (defence in
  depth behind the disabled button, since `Ctrl+S` reaches the function
  directly): stop the capture, toast `Dictation stopped — press Ctrl+S again to
  save.`, return. Guard placement is **before** `state.saving = true`.

Do **not** add the mic to `setExplorerEditChromeDisabled`'s selector list
(`explorer-editor.js:147-154`) — same rule as zoom and wrap: an in-flight
recording must stay stoppable.

### 4.3 `web/static/js/terminals.js` — two edits, both small

**Edit 1 — the `voice_result` fan-out (`terminals.js:7973`).** Becomes a branch
in front of the existing body; the terminal arm is unchanged:

```js
socket.on('voice_result', ({ session_id, text, final: isFinal }) => {
    const index = _voiceIndexForSession(session_id);
    if (index === -1) return;

    if (isExplorerDictationTarget(index)) {
        if (isFinal && text) {
            deliverExplorerDictation(index, text);   // R1–R4 live in here
            _clearVoicePreview(index);
        } else if (text) {
            _showVoicePreview(index, text);          // partials: bubble only
        }
        return;
    }
    if (!terminals[index]?.term) return;             // R5: never inject into a
                                                     // pane that has no terminal
    …existing _sendToTerminal / broadcastInputToPeers / _clearVoicePreview…
});
```

`R5` is the defensive close of the record-to-nowhere shape described in §1.1:
even though no reachable path produces it today, an explorer or browser pane can
never receive `terminal_input` from a transcript.

**Edit 2 — the PTT target resolver (`terminals.js:6911`).** One expression:

```js
const index = _voiceActiveIndex !== -1
    ? _voiceActiveIndex
    : _findVoiceTargetIndex();        // was _findPttTerminalIndex()
```

`_findVoiceTargetIndex()` is added in `voice-input.js` (§4.5) and is
`explorerEditorVoiceTargetIndex()` first, `_findPttTerminalIndex()` second. When
no explorer editor is focused it returns exactly what it returns today (I8).

Nothing else in `terminals.js` changes. In particular `buildPaneCard`,
`wirePaneControls`, `_wireVoiceHoldToTalk`'s call site, `_stopAllVoice`'s two
call sites (`terminals.js:922`, `terminals.js:4742`) and the focus tracking at
`terminals.js:5430-5442` are all left alone.

### 4.4 The insertion itself

Inside `deliverExplorerDictation`, after `resolveVoiceDelivery` returns
`'editor'`:

1. Resolve `textarea = document.getElementById('explorer-edit-textarea-' + index)`.
   Missing → drop with reason `'no-target'`.
2. Read `selectionStart` / `selectionEnd` **from the textarea directly** — the
   selection survives blur, so a user who clicked elsewhere mid-capture still
   gets the text at the caret they left behind. Focus is never stolen.
3. `composeDictationInsert` produces the final string (selection replaced, space
   prepended when warranted).
4. If `document.activeElement === textarea`, try
   `document.execCommand('insertText', false, composed)` inside `try/catch`. On
   `true`, the browser fires `input`, the existing listener runs
   `handleExplorerEditInput`, and native undo works (§1.2).
5. Otherwise (or on `false`/throw): `textarea.setRangeText(composed, start, end, 'end')`
   followed by an explicit `handleExplorerEditInput(index)`.
6. Clear `state.voice`, cancel the settle timer, `refreshExplorerEditControls(index)`
   so Save comes back enabled with the new dirty state.

Dirty tracking, the tab dirty marker, `beforeunload`, and the revision-checked
`PUT /api/explorer/<id>/file` are all reached through the existing
`handleExplorerEditInput` — dictation is ordinary input from that line onward.

### 4.5 `web/static/js/voice-input.js` — hooks and two generalizations

Deliberately minimal; nothing here changes what a terminal does.

1. **Two optional hook calls**, both guarded so the launcher page (which does not
   load `explorer-editor.js`) is unaffected:
   - at the tail of `_startVoice`'s success block (after `_setVoicePanelStatus`,
     `voice-input.js:523`) — `explorerEditorNoteVoiceStarted?.(index)`;
   - at the tail of `_stopVoice` (after `_setVoicePanelStatus`,
     `voice-input.js:786`) — `explorerEditorNoteVoiceStopped?.(index)`.

   Hooking start/stop rather than the *callers* is what makes every entry point
   correct at once — mic click, press-and-hold, PTT keydown/keyup, the
   `voice_status` error path (`terminals.js:8000`), `_stopAllVoice` on group
   switch and teardown. No caller needs to know dictation exists.

   Written as `typeof explorerEditorNoteVoiceStarted === 'function' && …` rather
   than optional-call syntax on a possibly-undeclared binding, since these are
   top-level function declarations in a later classic script.

2. **`_findVoiceTargetIndex()`** — new, three lines, as described in §4.3.
   `_findPttTerminalIndex` keeps its current body and meaning.

3. **`_wireVoiceHoldToTalk(card, index)` gains an element-based core.** Extract
   the existing body into `_wireVoiceHoldToTalkElements(button, control, index)`
   and leave `_wireVoiceHoldToTalk` as the two-line lookup + delegation it
   already is. Pure move; the editor mic calls the element form. This is the only
   refactor in the plan and it is contained to one self-contained function.

4. **Preview and recording state follow the visible mic.** `_updateVoiceBtn`,
   `_showVoicePreview`, and `_clearVoicePreview` currently address
   `#tvoice-${index}` only, which is `display:none` on an explorer pane — the
   recording ring and the partial bubble would be invisible. Add one resolver:

   ```js
   function _voiceButtonsFor(index) {
       return [
           document.getElementById(`tvoice-${index}`),
           document.getElementById(`explorer-voice-${index}`)
       ].filter(Boolean);
   }
   ```

   and have those three functions iterate it. On a terminal pane the array is
   the single element they use today (I8). `_setVoicePanelStatus` is left alone;
   it only writes a title.

The body-level recording overlay (`_showVoiceRecordingOverlay`) needs no change —
it is fixed-position and pane-independent, so the editor gets it for free.

### 4.6 Push-to-talk: the modifier guard

`explorerEditorVoiceTargetIndex()` engages only when **all** of these hold:

- `_voicePrefs.pttEnabled` and a configured `pttKeybind` (already checked by the
  caller at `terminals.js:6901`);
- `document.activeElement` is an `explorer-edit-textarea-*` textarea whose pane
  has an active edit state;
- **the keybind contains `Ctrl`, `Alt`, or `Cmd`.**

The third condition is the important one and it is new relative to the proposal.
`_matchesPttKeybind` (`voice-input.js:989`) matches a bare printable key exactly —
so a user whose keybind is `V` (or `Shift+V`) would start a recording *and lose
the character* every time they typed a `v` into the file they are editing,
because the PTT handler calls `event.preventDefault()`. Terminal panes have the
same shape today and are left exactly as they are; a text editor is where it
would actually destroy work, so the editor path requires a real modifier. When
the keybind has none, the editor simply has no PTT and the mic button (click or
press-and-hold) still works — a degraded surface, never a corrupted buffer.

Also note `Ctrl+S` as a PTT keybind: the textarea's own keydown handler
(`explorer-editor.js:285`) and the document-level PTT listener would both fire.
The §4.2 `saveExplorerEdit` guard makes the outcome deterministic (capture stops,
nothing is saved, the user presses again) rather than racy, which is the most
this deserves.

### 4.7 CSS — `web/static/css/terminals.css`

Two additions in the existing "In-app editor controls" block
(`terminals.css:3187`), tokens only, no palette literals (guardrail 7):

```css
.explorer-editor-voice { position: relative; display: inline-flex; }
.explorer-editor-voice-btn { min-width: 32px; padding: 0 8px; }
```

`position: relative` on the wrapper is what the existing absolutely-positioned
`.voice-partial-preview` anchors to. The already-shared `.voice-btn.recording`
and `.voice-btn:disabled` rules supply the recording ring and the disabled look,
so no new state styling is written. `.explorer-pane .voice-control` is **not**
modified.

---

## 5. Edge cases

Each row states the trigger, the mechanism that handles it, and the invariant it
protects. Rows marked ▶ are covered by an automated test in §7.

| Trigger | Behavior | Mechanism |
|---|---|---|
| ▶ Esc / Cancel / Save / tab switch / tab close / pane close / file reload / navigate away, while recording | Capture stops; a transcript arriving afterwards is dropped, never injected anywhere | `clearExplorerEditState` fires `_stopVoice`; `resolveVoiceDelivery` returns `'drop'` / `'no-edit'` (I2, I4) |
| ▶ Transcript arrives after a *different* file was opened in the same pane | Dropped — `epoch` no longer matches | epoch check in `resolveVoiceDelivery` (I2) |
| ▶ `Ctrl+S` or Save clicked while recording | Capture stops, nothing saved, one toast: press again to save | `saveExplorerEdit` early return + disabled Save (I5) |
| ▶ Transcript arrives while `state.saving` is true | Dropped with reason `'saving'`, one toast | `resolveVoiceDelivery` (I5) |
| ▶ Transcript never arrives (socket drop, backend hang) | Editor unblocks after 5 s, one toast | settle timer → `explorerEditorVoiceSettleExpired` (I6) |
| Backend unavailable / voice disabled in App Settings | Existing preflight toast, capture never starts, nothing binds | `_startVoice` preflight, unchanged (I6) |
| `_startVoice` throws (no `getUserMedia`, denied permission, no AudioWorklet, pywebview) | Existing catch path runs; the bind hook is in the success tail only, so nothing is left bound | `voice-input.js:533-551` (I6) |
| Backend emits `voice_status: error` mid-capture | `_stopVoice(…, {notifyServer:false})` runs → stop hook → settling → timer clears | existing `terminals.js:7997-8004` + stop hook (I6) |
| ▶ Another pane's mic (or PTT) starts while the editor records | Editor capture stops first, then the new one starts; editor unblocks via settling | existing one-recorder rule in `_toggleVoice` / hold-to-talk, plus the stop hook (I3) |
| Editor mic clicked while a *terminal* records | Same, mirrored | same (I3) |
| Group switch / workspace switch / grid teardown while recording | `_stopAllVoice()` already runs at both call sites; the stop hook settles each bound editor, and the timer clears it even though the pane is off-screen | `terminals.js:922`, `terminals.js:4742` (I4, I6) |
| Window loses focus mid-capture | Capture continues — deliberate, matching terminal PTT today; the floating overlay keeps it visible | no change |
| Focus moves out of the textarea mid-capture (click another pane) | Text still lands at the caret the user left; focus is not stolen | `setRangeText` fallback path, §4.4 step 4 |
| ▶ Insertion with a non-empty selection | Selection is replaced, caret ends after the inserted text | `composeDictationInsert` + `'end'` (I1) |
| ▶ Insertion mid-word / after punctuation / at line start / into an empty buffer | One space added only where it belongs; never a newline | `composeDictationInsert` (I1) |
| `refreshExplorerEditControls` re-renders mid-capture (save error, conflict bar) | Mic is rebuilt, then re-wired and re-synced from live `_voiceState` — the `.recording` ring and hold-to-talk survive | `wireExplorerEditControls` → `wireExplorerEditorVoice` + `syncExplorerEditorVoiceButton` |
| Save conflict (`409`) while a transcript is settling | Save was disabled, so this cannot be reached from a bound capture; a conflict from an earlier save leaves `voice` untouched | §4.2 ordering (I5) |
| Page unload with dictated-but-unsaved text | Existing `beforeunload` guard fires — dictated text is dirty text | `installExplorerEditBeforeUnload` (I7) |
| Vosk partials | Preview bubble on the editor mic only; never written to the textarea | `voice_result` branch, `'preview'` target |
| Whisper (shipped default) | One block inserted on stop; the settling window is exactly what makes this safe | §3 |
| Non-editable file, Diff view, directory listing, browser pane | No mic exists — it is rendered only in the active-edit branch | §4.2 |

---

## 6. Guardrail compliance

Checked against `CLAUDE.md` §Regression Guardrails.

1. **Security** — no origin, CORS, host-key, or secret surface is touched. No new
   route. `voice_start` already accepts any session id and results are emitted to
   the originating socket only (`web/api.py:3263`).
2. **Concurrency** — no server-side change, so no lock is involved. Client-side,
   the single-writer rule for the buffer is the §3 state machine.
3. **Performance** — no new polling, no new socket traffic, no new asset. The
   settle timer is a single bounded `setTimeout` per capture. `_setVoiceBtnsDisabled`
   already sweeps `.voice-btn` on every start/stop; adding at most one button per
   open editor does not change its cost class.
4. **Correctness** — no `window.prompt/confirm/alert`: every user-facing message
   is `showTerminalToast`, and the existing discard confirmations already go
   through `openGenericConfirmModal`. No shell quoting involved.
5. **Dead code** — no new config key, no new endpoint, no new Socket.IO event.
   Every new function has a call site in this plan. The new UMD module has both a
   browser consumer and a Node test consumer.
6. **Architecture / DRY** — new frontend surface goes in its own file
   (`voice-dictation.js`) plus the matching existing domain files
   (`explorer-editor.js` owns the editor, `voice-input.js` owns capture); nothing
   is added to `terminals.js` beyond the two-branch edits, and nothing new lands
   in `explorer-viewer.js` (the flagged regrowth risk). Local and SSH explorer
   panes are identical here — dictation is client-side, so the backend
   abstraction is not involved.
7. **Styling** — `currentColor` stroke SVG (`VOICE_MIC_ICON`), tokens only, two
   new rules that add layout and reuse existing state classes.
8. **Interaction** — the irreversible action (discarding a dirty buffer) keeps
   its existing in-page confirm; failure states toast with a retry that is just
   "press it again"; busy state toggles `disabled` / `.recording` classes rather
   than rewriting markup.
9. **Logging** — reuses `_voiceLog` at its existing sites only; no new
   high-frequency logging, no transcript text logged anywhere.
10. **New features** — builds on the existing contracts; nothing is persisted
    (I7), so saved presets, `runtime_state.json`, and the explorer presentation
    normalizer are all untouched.

Explorer read-only contract: **unchanged.** Dictation writes to an in-memory
textarea buffer; the only filesystem write is the existing revision-checked
`PUT /api/explorer/<id>/file`, reached through the existing Save button.

---

## 7. Tests

Backend: no API change, so no new backend test. Run the existing suites.

New file `tests/test_voice_dictation.py`, following the Node harness pattern in
`tests/test_explorer_theme_store.py` (`@unittest.skipUnless(NODE, …)`,
`require()` the real module, execute rather than assert on source text). No raw
JS/HTML/CSS source-text assertions anywhere.

**Routing (`resolveVoiceDelivery`)**

- final + active edit + matching epoch + not saving → `'editor'`
- final + no edit state → `'drop'` / `'no-edit'` *(the late-result-after-exit case)*
- final + edit present but different epoch → `'drop'` / `'epoch-mismatch'`
- final + `saving: true` → `'drop'` / `'saving'`
- final + no edit + `hasTerm: true` → `'terminal'` *(the unchanged terminal path)*
- final + no edit + `hasTerm: false` → `'drop'` / `'no-target'` *(R5)*
- non-final with text → `'preview'` in both editor and terminal shapes
- non-final without text, and final without text → `'drop'`

**Insertion (`composeDictationInsert`)**

- empty buffer, caret 0 → text verbatim, no leading space
- caret after `hello` → one leading space
- caret after a space, a newline, or `(` → no leading space
- transcript already starting with a space or with `.` / `,` → no space added
- non-empty selection → selection replaced, nothing else altered
- never emits a newline; never mutates the surrounding text

**Python-side**

- Extend `tests/test_api.py` only if the existing voice tests need the new script
  tag reflected in the rendered terminals page (a route-level 200 + presence of
  the module in the page's script list is a contract check, not a source-text
  assertion of behavior).

Manual verification checklist for the PR description (not automatable here):
mic click; press-and-hold ≥ 350 ms; PTT with a modifier keybind while the
textarea has focus; Esc mid-recording; `Ctrl+S` mid-recording; second pane's mic
mid-recording; group switch mid-recording; backend stopped mid-recording;
`Ctrl+Z` after dictation restores the pre-dictation buffer in one step.

Then `make check` (Windows without `make`: `python tests/run_tests.py` and
`python -m ruff check .`).

---

## 8. Documentation

- `docs/voice_guideline.md`
  - **UI Contract** — add the editor mic: "while a file is open in the explorer's
    in-place editor, a mic button sits in the editor's Save/Cancel group; it
    exists only while an edit session is active. The pane-header mic remains
    hidden on explorer and browser panes."
  - **Transcript Delivery Guideline** — the section currently states final text
    is sent into the terminal input stream. Amend to: delivery is chosen by the
    recording pane's state — an active explorer edit session receives the final
    transcript at the caret in its textarea; every other pane keeps the direct
    terminal injection; a pane with no terminal and no active edit receives
    nothing. Record explicitly that the draft-review step was considered and
    rejected (proposal §4), and that dictation is subject to the same
    revision-checked save as typed text.
  - **Non-Negotiable Product Rules** — add: one recorder per page still holds
    across terminals *and* editors; Save and dictation are mutually exclusive on
    the same buffer.
- `README.md` — one line in the explorer/editor feature description.
- `CHANGELOG.md` — user-visible entry on completion.
- `CLAUDE.md` — no change needed. The explorer read-only contract and the
  editor's write exception are unchanged; nothing new is persisted.
- Mark the proposal document as superseded by this plan (one line at its top),
  keeping it as the design rationale of record.

---

## 9. Staging

Four commit-sized steps. Each leaves the tree working, and steps 1–2 are
invisible to the user, so a problem found late can be dropped without unwinding
anything.

| Step | Content | Verifiable by |
|---|---|---|
| **1** | `voice-dictation.js` + `tests/test_voice_dictation.py` + the script tag. Nothing calls it yet. | `make check` — new tests pass, no behavior change |
| **2** | `voice-input.js`: the two guarded hooks, `_findVoiceTargetIndex`, the `_wireVoiceHoldToTalk` extraction, `_voiceButtonsFor`. Hooks resolve to nothing until step 3. | `make check` + terminal dictation unchanged by hand (I8) |
| **3** | `explorer-editor.js` (state machine, mic, insertion) + the two `terminals.js` branches + the CSS. The feature is live here. | manual checklist in §7 |
| **4** | `docs/voice_guideline.md`, `README.md`, `CHANGELOG.md`, proposal superseded note. | review |

**Rollback:** the feature is fully disabled by reverting step 3 alone — steps 1
and 2 are inert without it (the hooks are `typeof`-guarded, `_findVoiceTargetIndex`
falls through to `_findPttTerminalIndex`, `_voiceButtonsFor` returns the single
element it does today).

---

## 10. Out of scope

Called out so they are not smuggled in:

- Making the pane-header mic visible on explorer or browser panes.
- Dictation into the Search field, the URL bar, the tab strip, or any surface
  other than the edit textarea.
- Dictation into a browser pane.
- A separate editor keybind config (proposal §4 — rejected there, still rejected).
- A draft-review or confirm-before-insert step (proposal §4 — rejected).
- Voice commands ("new line", "delete that") — this is dictation, not control.
- Any backend, config, or persistence change.
