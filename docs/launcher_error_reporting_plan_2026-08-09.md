# Launcher Error Reporting — Analysis & Proposal (2026-08-09)

Status: **proposal / not implemented**. Scratch paper under `docs/r&d/` — do not cite
from code or maintained docs. If this lands, the contract goes into `CLAUDE.md` and the
code that owns it.

Scope: the launcher window only (`templates/index.html`, `web/static/js/launcher.js`,
plus the shared dialog partials it includes). The workspace page (`terminals.html`) is
touched only where it already owns a helper the launcher should share.

---

## 1. What the launcher has today

There are **five** independent ways a message reaches the user, none of which knows about
the others.

| # | Surface | Element | Owner | Where it appears on screen | Auto-clears |
|---|---------|---------|-------|---------------------------|-------------|
| 1 | Global message line | `#message` (`.message`) | `showMessage()` — `launcher.js:402` | **Inside card 01 "Terminal Layout"**, top of the *left* column | Never |
| 2 | Update status line | `#quickUpdateStatus` (`.inline-status`) | `setUpdateStatus()` — `launcher.js:410` | Bottom of the *right* column, under the icon row | Yes, 6 s |
| 3 | Restore banner | `#restoreWorkspaceBanner` | inline handlers | Full-width strip under the titlebar | On action |
| 4 | Per-row inline status | `.agent-preflight-*` | `launcher.js:1511-1600` | In the terminal row that caused it | n/a |
| 5 | In-dialog field error | `#workspaceNameError`, `.settings-hint`, lifecycle `is-error` | `workspaces.js:759`, `app-settings.js:235`, `lifecycle.js:69` | Inside the open dialog | n/a |

`showMessage()` is called **48 times** in `launcher.js` and is the sink for essentially
every failure the page can produce — SSH validation, launch failure, save/close/restore of
workspaces, saved-session import/delete, folder picker, workspace-mode toggle, App Settings
load/save (via `appSettingsNotify` → `showMessage`, `launcher.js:73`), and browser shutdown.

### 1.1 The reported symptom, precisely

**a) Errors land in an unrelated panel.** `#message` is markup inside the
`terminal-layout-card` (`index.html:57`). Its resting text is *"Choose the terminal count
and layout for the workspace."* — genuinely the layout card's helper copy. Every unrelated
error overwrites that helper line. So "Launch failed", "Could not save the workspace",
"Settings save failed" all appear inside **step 01, Terminal Layout**, in the opposite
column from the control that failed. Nothing about the panel says "errors appear here".

**b) The failed-update error is reported twice.** `checkForUpdates()`
(`launcher.js:2461-2466`) writes the same failure to **both** surfaces:

```js
setUpdateStatus(error.message, 'error');            // bottom-right, fades after 6 s
showMessage(`Update failed: ${error.message}`, 'error');  // top-left, permanent
```

Two visually different copies of one failure, in two corners, with two lifetimes and two
different prefixes. The same doubling exists for the *success* path (`2451/2452`,
`2459/2460`) and in `requestRestartLifecycle()` (`2474-2476`, `2493-2497`). App Settings
save is a third variant: it writes success to `setUpdateStatus` (`launcher.js:82`) but
failure to `showMessage` (`app-settings.js:687` → `appSettingsNotify`) — the same feature
reports its two outcomes in two different places.

**c) Errors are reported behind an open modal.** Several failures raised while a dialog is
open write to `#message`, which the modal backdrop covers:

- `restoreSelectedWorkspaces()` — `launcher.js:3213`, `3241`, `3246`, with the restore
  dialog explicitly left open when nothing started (`3255-3260`).
- App Settings load/save failures — `app-settings.js:637`, `687`, raised while the App
  Settings dialog is the front surface.
- `forget`/`close` from the restore chooser — `launcher.js:3193`.

The user sees the dialog do nothing, and the explanation is painted on a panel they cannot
see.

**d) Errors never expire, successes do — inconsistently.** `#message` has no timer, so a
failure from twenty minutes ago still reads as current state, and a later success silently
replaces a still-unresolved error. `#quickUpdateStatus` clears itself after 6 s, so a real
update failure can vanish before it is read.

**e) Severity is colour-only.** `.message.error` / `.info` / `.warning` / `.success` differ
only by text colour (`launcher.css:539-542`). No icon, no role, no `aria-live` — a screen
reader is never told anything changed, and the four states are indistinguishable to a
colour-blind user.

**f) No error is actionable.** The retry affordance guardrail (guardrail 8) is met only by
appending the words "— try again" to the string (`launcher.js:2968`, `2996`, `3193`,
`3249`). There is no retry control, and no way to copy a long failure message.

**g) There is a toast helper — on the wrong page.** `showTerminalToast()`
(`terminals.js:7832`, CSS `terminals.css:3145`) is exactly the transient, `role="status"`
surface the launcher lacks, but it lives in the workspace page's monolith and the launcher
does not load it. Guardrail 6 (shared JS goes in `shared.js`) is already being violated by
its location.

---

## 2. Root cause

There is no notion of *where a message belongs*. `showMessage` is a global sink that
happens to be parented by whichever card the markup sits in, and `setUpdateStatus` is a
second sink that was added next to the update button because the first one was in the wrong
place. Callers pick a sink ad hoc, and the update flow picks both.

The launcher already demonstrates the correct model in two places — agent preflight reports
inside its own row, and the workspace-name dialog reports beside its own field. Those are
right because the message has an owner. The fix is to make that the rule rather than the
exception, and to give ownerless messages **one** predictable home.

---

## 3. Proposed model — "report where it happened, or in the one place that's always visible"

Three tiers, with a rule for choosing between them that a reviewer can apply mechanically.

### Tier 1 — Contextual (preferred when an owner exists)

If the failure belongs to a specific field, row, or dialog that is on screen, it is
reported **there**, and nowhere else.

- Form validation → beside/under the offending input, plus focus moved to it.
  (`'Enter an SSH host before launching.'`, `'Select a local repository folder…'`)
- A per-workspace row action (Save / Close / Open) → in that row.
- A dialog's own action → in that dialog's status slot, which stays until dismissed.
- Agent preflight → unchanged; it is already correct.

**Rule: a message raised while a modal is open never goes anywhere but that modal.**

### Tier 2 — Global notification centre (the single universal spot)

One surface for everything without an on-screen owner, or for outcomes that outlive the
control that caused them (launch, update, restart, restore, import/delete, app-level
failures).

Proposal: a **stacked toast region, top-right, below the titlebar**, `role="status"` for
info/success and `role="alert"` for error/warning, with:

- an icon per severity (stroke `currentColor`, per guardrail 7) so severity is not
  colour-only;
- **success/info auto-dismiss (~5 s); warning/error persist until dismissed** — the
  asymmetry is the point: a failure must never disappear before it is read;
- a manual dismiss on every entry;
- an optional **action button** on the entry (`Retry`, `Open Releases`, `Show details`) so
  guardrail 8's retry affordance becomes a control instead of the words "try again";
- an optional collapsed **details** block for long server errors;
- a cap of ~3 visible entries, oldest collapsing into a "+N more" line, so a burst cannot
  cover the launcher.

The helper lives in `shared.js` as `showGridVibeNotice({ text, type, action, details })`,
so the workspace page can adopt it later and `showTerminalToast` can be folded into it
(guardrail 6). New CSS goes in a shared `notices.css` (or a `tokens.css`-driven block in
`workspaces.css` — see open question Q4), using tokens only.

### Tier 3 — Blocking dialog (unchanged)

Reserved for cases already using `openGenericConfirmModal` — a decision is required
("Already open elsewhere", "This copy cannot self-update"). No new use.

### What happens to the two existing sinks

- `#message` **loses its error/warning role entirely** and reverts to what its position
  claims it is: the Terminal Layout card's static helper copy. Optionally it keeps
  *contextual* success feedback about layout/count, or is made purely static (open
  question Q2).
- `#quickUpdateStatus` **is removed**, and update/restart/settings outcomes go to Tier 2
  only. This is the direct fix for the double report. The update button keeps its existing
  `.loading` class as the in-progress signal, so the "Checking the git remote…" progress
  line does not need a permanent slot.

### Call-site mapping (all 48 `showMessage` calls)

| Current call site | Tier |
|---|---|
| `launchSessions()` host/folder validation — `3323-3331` | 1 — field |
| `buildSessionsFromConfig` throw — `3337` | 1 — the offending terminal row |
| `launchSessions()` launch failure / warnings — `3405`, `3419` | 2 |
| `checkForUpdates()` all outcomes — `2451-2465` | 2 (single call) |
| `requestRestartLifecycle()` — `2474-2496` | 2 (single call) |
| `shutdownBrowserApp()` onError — `31` | 2 |
| Workspace row Save / Close — `2962-2998` | 1 — the row |
| Restore chooser errors — `3193`, `3213`, `3235-3251` | 1 — the restore dialog |
| `restorePreviousWorkspace()` — `2681-2703` | 2 (banner is dismissed by then) |
| Saved-session save / import / delete / new — `2089-2406` | 2, except failures raised with the saved-sessions modal open → Tier 1 |
| Folder picker — `1277`, `1280` | 1 — the folder field |
| Connection target prefill — `1158`, `1185` | 2 (info) |
| Multi-workspace toggle — `2807`, `2811` | 1 — the toggle card |
| `appSettingsNotify` — `73` | 1 — App Settings dialog status slot |
| `viewActiveTerminals` — `2569`, `2590` | 2 |
| Max-terminals refusal — `3523` | 1 — near the Add Terminal button |

---

## 4. Implementation sketch

1. **`shared.js`** — add the notice centre: a container created on demand, an entry
   builder, severity icons, the dismiss/auto-dismiss policy, and the optional action and
   details. Keep it DOM-light and side-effect free on load so it is Node-testable in the
   style of `session-persistence.js`.
2. **Shared CSS** — `notices.css`, tokens only, included by both pages.
3. **`launcher.js`** — replace `showMessage`/`setUpdateStatus` with `showGridVibeNotice`
   for Tier 2, and add small contextual helpers (`setFieldError(inputId, text)`,
   `setRowNotice(row, text, type)`, `setDialogNotice(modalId, text, type)`) for Tier 1.
   Delete the double-report pairs.
4. **Dialogs** — give the saved-sessions and workspace-restore modals a status slot in
   their partial/markup, matching the one the workspace-name modal already has.
5. **`index.html`** — remove `#quickUpdateStatus`; leave `#message` as static helper copy
   (or repurpose per Q2).
6. **`app-settings.js`** — route `notifyAppSettings` to the dialog's own status slot when
   the dialog is open, Tier 2 when it is not; stop the launcher-only success/failure split.
7. **Tests** — behavioural, no source-text assertions (working rules). Candidates:
   - a Node test over the DOM-free notice policy: severity → auto-dismiss or not, cap and
     overflow, action invocation, dismissal;
   - a test that a failure raised with a modal open resolves to the modal's slot, not the
     global centre;
   - a regression test that one update failure produces exactly **one** notice.
8. **Docs** — `CHANGELOG.md` (user-visible), and a one-paragraph contract in `CLAUDE.md`
   under Regression Guardrails 8 stating the three tiers and the "one message, one
   surface" rule.

Rough size: ~250 new shared lines, ~50 call-site edits in `launcher.js`, one CSS file, two
partial edits. No backend change.

---

## 5. Open questions

**Q1 — Placement of the global notice centre.**
Top-right under the titlebar, bottom-right above the action bar, or a full-width strip
under the titlebar (reusing the restore-banner slot)? Top-right is conventional and never
overlaps the Launch CTA; a full-width strip is louder and matches the existing restore
banner, but pushes the layout down when it appears. **Which do you want?**
Me: reusing the restore banner slot. Must not break layout tho (width/height important). 

**Q2 — What becomes of `#message`?**
(a) Pure static helper copy for the Terminal Layout card, never written by JS; (b) it keeps
*layout-related* feedback only (count/layout changes); or (c) delete the line entirely and
let the card's section head carry the hint. (a) is the smallest change.
Me: (a)

**Q3 — Should errors persist until dismissed?**
Proposal is: success/info fade, warning/error stay until dismissed. Alternative: everything
fades but errors also append to a "recent problems" log reachable from the titlebar.
Persist-until-dismissed is simpler but can accumulate if the user ignores it. **Confirm the
asymmetry is what you want.**
Me: Persist-until-dismissed, but dismiss is not a blocker or effect on current behaviour for anything. if the user doesnt dismiss or a new error/warning/success/info comes up we just overwrite/replace in the banner. Lets make it very simple. Its notmeant to be a debuging tool. more of a visible notification.

**Q4 — One shared notice system, or launcher-only for now?**
Doing it in `shared.js` and folding `showTerminalToast` in is the guardrail-6-correct
answer and gives both pages one behaviour — but it means touching `terminals.js` and the
workspace page's visual behaviour in the same change. Launcher-only is a smaller, safer
diff that leaves a second toast implementation alive. **Scope it to the launcher now with a
shared-by-construction helper, or migrate both pages in one go?**
Me: Scope it to the launcher now with a shared-by-construction helper

**Q5 — How much of the raw error do we show?**
Server errors can be long (git output, SSH tracebacks). Options: (a) show the full message;
(b) show a one-line summary with a "Show details" expander; (c) summary + "Copy details".
(b) or (c) is better UX but adds surface. **Also: is a "Copy" affordance wanted so failures
can be pasted into an issue report?**
(a) We have a full width banner, we could also wrap in that. Lets have a nice and clear full message. Some of the messages can be made better/shorter/clearer

**Q6 — Should failures also be logged?**
Currently launcher failures exist only as on-screen text; nothing reaches
`logs/gridvibe.log`. Do you want client-side failures echoed to `console.error` only, or a
lightweight POST to a server log endpoint? (The latter is new backend surface and a new
endpoint — flagged because guardrail 5 forbids shipping endpoints nothing reads.)
Me: client-side failures echoed to `console.error` only

**Q7 — Do contextual (Tier 1) errors *also* get a global notice?**
Proposal is **no** — exactly one surface per message, which is the whole point of the fix.
But an error in a folded card (Terminal Setup can be folded, `index.html:161-171`) or in an
off-screen row would then be invisible. Option: Tier 1 only, plus auto-unfold/scroll-into-
view of the owning card. **Confirm no duplication, and whether auto-unfold is acceptable.**
Me: The top banner error display will be fine. The offending panel can be highlighted green/red (Only if this is simple to do)

**Q8 — Scope of this change.**
Fix only the reported bugs (double update report, errors-behind-modals, the misplaced
`#message`), or land the full three-tier model in one pass? The former is maybe a third of
the work and leaves the architecture as-is.
Me: outline a plan for a full implementation with the decisions i made, Lets have a solid non flaky, non buggy error reporting, we can expand later

---

## 6. Decisions locked (from §5)

Sections 3 and 4 above are the *original* proposal and are now superseded for pass 1 by
this section and §7. The three-tier model in §3 stays on the roadmap; **pass 1 ships Tier 2
only, as a single-slot banner, and routes everything through it.**

| # | Decision |
|---|---|
| D1 | **One banner, full width, directly under the titlebar**, styled and inset like the existing restore banner. It gets its **own grid row**, so it and the restore banner can never overlap. |
| D2 | **Layout is never allowed to break.** The banner is content-sized in an `auto` row, with a hard `max-height` + internal scroll so no message length can eat the page. |
| D3 | **Exactly one message on screen at a time.** A new notice of any severity replaces the current one. No stacking, no queue, no "+N more", no history. |
| D4 | `error` / `warning` persist until dismissed or replaced. `success` / `info` auto-dismiss after 6 s (and can also be dismissed or replaced). Dismissal is cosmetic — it never blocks, cancels, or changes any behaviour. |
| D5 | **Full message text, wrapped**, no truncation, no "Show details" expander, no Copy button. Message copy gets a cleanup pass instead. |
| D6 | No action/Retry buttons in pass 1. The only control is the dismiss ×. |
| D7 | Severity is icon + colour + border, never colour alone. `role="status"` + `aria-live` so it is announced. |
| D8 | Errors are echoed to `console.error` only. No server logging, no new endpoint. |
| D9 | `#message` becomes **static helper copy** for the Terminal Layout card; JS never writes it again. `#quickUpdateStatus` is **deleted**. |
| D10 | Every current `showMessage` / `setUpdateStatus` call site routes to the banner. Contextual (Tier 1) reporting is deferred, **except** a cheap `is-invalid` outline + focus on the two launch-validation fields. |
| D11 | The banner is **shared by construction** (its own page-agnostic module, no launcher-specific ids) but **only the launcher wires it up** in pass 1. `showTerminalToast` is left alone. |

### The banner's shape (D1/D2), precisely

```
┌─ titlebar ─────────────────────────────────────────────────────────┐
├─ NOTICE BANNER  (grid-row 2, hidden when empty) ───────────────────┤
│ [!]  Update failed: could not reach the git remote.            [×] │
├─ restore banner (grid-row 3, unchanged) ───────────────────────────┤
│ ...                                                                │
├─ .shell (grid-row 4, 1fr) ─────────────────────────────────────────┤
```

- Horizontal insets `margin: 14px 30px 0` — **identical to `.restore-banner`**, so it lines
  up with the two columns rather than running full-bleed.
- Row is `auto`: height is content-driven and can never grow with the window. This is the
  exact failure the comment at `launcher.css:255-258` already warns about for the restore
  banner; the same explicit-row treatment prevents it here.
- One line at typical length; wraps to at most **4 lines** (`max-height: 6.5rem;
  overflow-y: auto`), with `overflow-wrap: anywhere` so an unbroken path or git blob can
  never force horizontal overflow.
- `position: relative; z-index: 12500` — above every current modal layer (highest today is
  `12200`, `workspaces.css:110`). The element stays **in normal flow**, so nothing moves
  when a dialog opens; the high z-index only changes paint order, which is what makes an
  error raised behind an open dialog readable. **This is the whole fix for symptom (c) —
  no conditional repositioning, nothing to go flaky.**
- Show/hide is the `hidden` attribute plus a `[hidden] { display: none }` rule, matching
  `.restore-banner`. When hidden the row collapses to 0 and the layout is byte-identical to
  today's.

---

## 7. Pass 1 — implementation plan

Ordered so that every step leaves the tree working and `make check` green.

### Step 1 — `web/static/js/notice-banner.js` (new file, ~150 lines)

Its own module per guardrail 6, not a bolt-on to `shared.js`. UMD wrapper matching
`explorer-theme-store.js` so Node can `require()` it.

Two halves, deliberately separated so the policy is testable without a DOM:

**(a) DOM-free policy** — no `document`, no timers:

```js
noticePolicy.normalizeType(value)   // -> 'error' | 'warning' | 'success' | 'info'
noticePolicy.normalizeText(value)   // trim, collapse runs of whitespace, cap ~600 chars
noticePolicy.isPersistent(type)     // error/warning -> true (D4)
noticePolicy.autoDismissMs(type)    // 0 for persistent, 6000 otherwise
noticePolicy.shouldEchoToConsole(type)  // error -> true (D8)
noticePolicy.nextState(current, incoming) // replace semantics (D3), returns the new slot
```

`nextState` is where the "solid, non-flaky" requirement lives: it is a pure function of
(current slot, incoming notice) and is the single owner of replacement. No other code
decides what is on screen.

**(b) DOM adapter** — `GridVibeNotice.show({ text, type })`, `.dismiss()`, `.clear()`:

- Binds to an existing element by id (`gvNoticeBanner`) — **it never creates markup**, so
  there is no race between a notice fired during page init and the DOM being ready. If the
  element is absent, `show()` is a no-op that still console-echoes.
- One module-scope timer handle; **every** `show()` clears it before setting a new one.
  This is the classic double-timer bug and the reason the timer is not per-notice.
- Sets `textContent` (never `innerHTML`) so a server error containing `<` can't inject.
- Swaps a severity class on the root (`is-error` / `is-warning` / `is-success` / `is-info`)
  and toggles which of the four inline SVG icons is `hidden` — icons are static markup in
  the partial, so the adapter only flips attributes (guardrail 8: classes, not markup).
- `role="status"`, `aria-live="polite"` for success/info; flips to `aria-live="assertive"`
  for error/warning.
- `console.error(text)` when `shouldEchoToConsole(type)`.

Public global: `showGridVibeNotice(text, type)` — a thin alias so call sites read like the
`showMessage(text, type)` they replace, which keeps the ~50-site diff mechanical.

### Step 2 — `templates/partials/notice_banner.html` (new partial, ~25 lines)

Static markup: the banner root (`id="gvNoticeBanner" hidden`), four `currentColor` stroke
SVG icons (alert-triangle for error, alert-circle for warning, check-circle for success,
info for info — all `hidden` except the active one), a `<span id="gvNoticeText">`, and the
dismiss button (an existing `×`-style ghost icon button, `aria-label="Dismiss notification"`).

A partial rather than inline markup so `terminals.html` can include it later without a
copy-paste (guardrail 6, and D11's "shared by construction").

### Step 3 — `web/static/css/notice-banner.css` (new file, ~70 lines)

Tokens only, both themes, per guardrail 7 — `--gv-danger`, `--gv-warning`, `--gv-success`,
`--gv-accent`, `--gv-radius-m`. Structure mirrors `.restore-banner` (flex, space-between,
same margins/padding/radius) so the two banners read as one family. Severity variants set
`border-color` + a soft tinted background + icon colour. Contains the `max-height` /
`overflow-y` / `overflow-wrap` guards from D2 and the `z-index: 12500`.

Separate file rather than a block in `launcher.css` because it is page-agnostic and
`terminals.html` will link it in pass 2.

### Step 4 — `templates/index.html`

1. `<link>` `notice-banner.css` after `launcher.css`.
2. `{% include 'partials/notice_banner.html' %}` immediately after `</header>`, before the
   restore banner.
3. **Delete** `<div id="quickUpdateStatus" class="inline-status"></div>` (line 311).
4. Leave `#message` in place with its helper text (D9) — markup unchanged, it simply stops
   being written.
5. `<script src=".../notice-banner.js">` before `launcher.js`.

### Step 5 — `web/static/css/launcher.css`

1. `.app-frame { grid-template-rows: auto auto auto 1fr; }` and re-number:
   `.app-titlebar { grid-row: 1 }`, `.gv-notice-banner { grid-row: 2 }`,
   `.restore-banner { grid-row: 3 }`, `.shell { grid-row: 4 }`. Keep the existing explanatory
   comment and extend it to cover the second optional row.
2. Delete the `.inline-status*` rules (873-884) — dead once `#quickUpdateStatus` is gone
   (guardrail 5).
3. Keep `.message` as the muted helper style; **delete `.message.error` / `.success` /
   `.info` / `.warning`** (539-542) so nothing can re-colour it, plus the
   `.terminal-card.card-folded > .message` rule stays as-is.
4. Migrate the touched `.message` / banner literals to tokens where any remain (guardrail 7).

### Step 6 — `web/static/js/launcher.js` (the mechanical pass)

1. `showMessage()` (402-406) → **delete the body, re-point at the banner**:
   ```js
   function showMessage(text, type = '') { showGridVibeNotice(text, type); }
   ```
   Keeping the name as a one-line shim means the ~48 call sites do not all have to change
   in the same commit, and reviewers can see routing separately from copy edits. It is
   removed in favour of direct `showGridVibeNotice` calls at the end of this step (a
   find/replace), so no permanent alias is left behind.
2. `setUpdateStatus()` (408-427) → **delete entirely**, along with `updateStatusClearTimer`.
3. **Delete the duplicate calls** — this is the reported bug:
   - `checkForUpdates()` 2451/2452, 2459/2460, 2464/2465 → one call each.
   - `requestRestartLifecycle()` 2474-2476, 2483, 2493-2497 → one call each.
   - `onAppSettingsSaved()` (line 82) → banner, so App Settings success and failure finally
     land in the same place.
4. Message-copy cleanup (D5), applied as rules rather than 48 ad-hoc rewrites:
   - One prefix per outcome family, present exactly once. Today `Update failed:` is added
     by the caller *and* the server message often already says it.
   - Drop every trailing `— try again` / `— try again.` string (2968, 2996, 3193, 3249,
     3403). With no retry control in pass 1 it is noise; the control it stands in for
     arrives in pass 2.
   - Sentence case, one sentence where possible, always ending in a period.
   - Never interpolate an empty `error.message`; fall back to a fixed sentence.
5. **Field highlight (D10, the one contextual crumb)** — `launchSessions()` 3323-3331:
   raise the banner *and* add `is-invalid` to `#ssh_host` / the folder field, then
   `.focus()` it. Clear `is-invalid` on the field's next `input` event. Two fields, one
   class, ~15 lines. `.is-invalid { border-color: var(--gv-danger) }` goes in
   `launcher.css` next to the existing field styles.

### Step 7 — `web/static/js/app-settings.js`

`notifyAppSettings` already delegates to the page hook, and the launcher hook already
forwards to `showMessage` → now the banner. The only change is removing the
success/failure split introduced by `onAppSettingsSaved` → `setUpdateStatus` (handled in
Step 6.3). Because the banner paints above the dialog (D1's z-index), App Settings failures
become readable **without** giving the dialog its own status slot — that slot is pass 2.

No change to `workspaces.js` (its in-dialog field error is already correct) or to
`lifecycle.js` (its in-dialog status is already correct). Both keep reporting where they do
today; the banner does not compete with them.

### Step 8 — Tests

`tests/test_notice_banner.py`, Node-executed, in the style of `test_explorer_theme_store.py`
(`require()` the module, stub globals, assert behaviour — no source-text assertions).

Policy half (pure, no DOM):
1. `normalizeType` maps unknown/empty/garbage to `info`, never throws.
2. `isPersistent`/`autoDismissMs` — error and warning persist (0 ms); success and info
   return 6000.
3. `nextState` replaces, never accumulates: N successive notices leave exactly one slot.
4. An error followed by a success leaves the **success** (D3 — no "errors win" surprise).
5. `normalizeText` collapses whitespace and caps length without cutting mid-escape.
6. `shouldEchoToConsole` is true only for `error`.

Adapter half (jsdom-free: a hand-stubbed element object, as the existing Node harnesses do):
7. `show()` with no bound element is a no-op that still echoes — no throw during early page
   load.
8. Two `show()` calls in the same tick leave exactly one pending timer (the double-timer
   regression).
9. A persistent notice schedules **no** timer.
10. `dismiss()` hides the element and clears the timer; a later auto-dismiss tick cannot
    un-hide or re-fire.
11. Text is written via `textContent`, so markup in a server error is inert.

Regression test tying it to the reported bug:
12. A simulated failed update produces **exactly one** notice (drive `checkForUpdates`'s
    error path against a stubbed fetch in the vm harness that `test_explorer_theme_store.py`
    already uses for `shared.js`, and count `show()` invocations).

Then `make check` (or `python tests/run_tests.py` + `python -m ruff check .` on Windows).

### Step 9 — Docs

- `CHANGELOG.md` — user-visible: one notification banner for launcher messages; update
  failures no longer reported twice; messages readable over open dialogs.
- `CLAUDE.md` — extend Regression Guardrail 8 with the contract: *the launcher has exactly
  one global notification surface; a message is shown once, in one place; error/warning
  persist until dismissed or replaced, success/info auto-dismiss; never reintroduce a second
  status sink.* Add `notice-banner.js` / `notice-banner.css` /
  `partials/notice_banner.html` to the repo-layout tree and `test_notice_banner.py` to the
  tests list.
- `README.md` — no change (no new user-facing feature or setting).

### Acceptance criteria

1. With the banner hidden, the launcher's layout is pixel-identical to today's.
2. No message length, and no window size, changes the banner's row from content-sized —
   a 2 KB git error scrolls inside it and the shell below keeps its space.
3. A failed update produces one banner, one time, with one prefix.
4. An error raised with App Settings or the restore chooser open is **readable over the
   dialog**, and dismissing it does not touch the dialog.
5. A second notice replaces the first; there is never more than one on screen.
6. An error stays until dismissed or replaced; a success clears itself after 6 s.
7. Nothing writes `#message` or `#quickUpdateStatus` — both are gone or inert.
8. Every notice's severity is distinguishable with colour vision disabled (icon + shape).
9. `make check` green.

### Explicitly out of scope for pass 1 (pass 2 backlog)

Per-field / per-row / per-dialog contextual reporting (Tier 1); Retry and Copy actions;
details expander; stacking or history; folding `showTerminalToast` into the shared module
and wiring the banner into `terminals.html`; server-side logging of client failures.

### Small confirms (not blockers — I will proceed with the stated default)

- **C1.** D4's asymmetry: success/info fade at 6 s, error/warning stay. §5 Q3 confirmed
  "persist until dismissed", and replacement semantics; the fade for *success* is my read of
  "non-intrusive". Default: as written.
- **C2.** The dismiss × is the only control. Default: yes.
- **C3.** The restore banner keeps its own row and styling and is **not** folded into the
  notification banner — it is a persistent offer with two actions, not a notification.
  Default: leave it alone.