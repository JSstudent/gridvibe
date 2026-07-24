# GridVibe Guardrail & Modularity Audit — 2026-07-22

Post-implementation review pass over the whole codebase, checking that the
Regression Guardrails in `CLAUDE.md` (distilled from
`docs/r&d/planed/deep_dive_review_2026-07-10.md`) are still respected after the
large volume of changes since that review landed, and assessing whether the
current structure is ready for the four planned features:

- `docs/source_and_diff_view_proposal_2026-07-22.md`
- `docs/r&d/planed/majors/text_editor_2026-07-20.md`
- `docs/r&d/planed/majors/github_tags_and_releases.md`
- `docs/r&d/planed/majors/installer_and_release_plan_2026-07-20.md`

**Scope note:** analysis only — no code was changed by this audit.

> **♻️ Findings resolved 2026-07-22 (same day, follow-up pass).** N1–N4 are
> fixed with regression tests (`GuardrailAuditFixesTestCase` in
> `tests/test_api.py`); N5 is intentionally untouched (it is Phase 2 of the
> source-and-diff proposal, out of this pass's scope); N6 got its guardrail
> (CLAUDE.md/AGENTS.md rule 6 now forbids growing `terminals.js` further) with
> the actual split deferred to the start of the source-and-diff work, per §5
> item 5's own sequencing. Guardrail rules 2, 4, and 6 were tightened in both
> `CLAUDE.md` and `AGENTS.md`. Per-finding resolution notes below.

**Validation state at audit time:** `python tests/run_tests.py` → 630 tests,
OK (1 skipped); `python -m ruff check .` → clean. All deep-dive regression
test cases (`CorsOriginDefaultsTestCase`, `CrossOriginWriteGuardTestCase`,
`SshSftpPoolTestCase`, `SessionStatusRoomScopeTestCase`, etc.) pass.

---

## 1. Verdict summary

The modular split has held. `web/api.py` is down to ~2,240 lines and genuinely
delegates to `agents.py` / `config.py` / `explorer.py` / `hostkeys.py` /
`terminal_io.py` / `voice.py` / etc.; the root shims are intact and re-export
the canonical modules; the explorer backend abstraction, SSH client pool,
vendored frontend assets, tokens.css, and room-scoped Socket.IO emits are all
in place and covered by tests.

**One real regression was found (finding N1: the native "Save workspace &
restart" button is gated on `window.confirm`, which the native WebView2 window
blocks — the button silently no-ops in the desktop app), plus four smaller
guardrail deviations and one architecture risk (`terminals.js` has regrown
into a 13.5k-line monolith — the same disease `web/api.py` was cured of, and
the file where three of the four planned features land).**

All four planned features have their backend/frontend seams present; none is
structurally blocked. Details in §4.

---

## 2. Guardrail-by-guardrail status

| # | Guardrail | Status | Evidence / notes |
|---|---|---|---|
| 1 | Security defaults | ✅ Pass | `security.cors_origins: []` in `default_config.json` → same-origin derivation; cross-origin write guard active; `session_status` emits `room=session_id` (`web/terminal_io.py:93`); host-key handling centralized in `web/hostkeys.py` (`auto-add` / `known-hosts` / `strict`, applied at all four SSH entry points); Fernet key handling isolated in `web/secrets.py`; **no** explorer filesystem-write routes exist (contract intact). |
| 2 | Concurrency | ✅ Pass, one note | Lock-ordering comment present at `web/terminal_io.py:75-79`; every output-pump emit sits *outside* `connection_lock` with an explanatory comment at each site. **Note N4:** `_broadcast_session_status` emits while holding `session_manager.lock` — same class of problem 2.4 fixed for `connection_lock`. |
| 3 | Performance | ✅ Pass | Per-session SSH client pool in `web/explorer.py:1887-1989` (fresh SFTP channel per request, idle reaping, evicted on close); deque output buffer; the status poll is a 15 s **fallback only while the socket is down** (`terminals.js:13496-13502`); zero CDN references — xterm 5.3.0 (+fit/search/web-links addons), socket.io 4.7.2, and Mermaid 11.15.0 are vendored under `web/static/vendor/` with a version/license README. |
| 4 | Correctness | ⚠️ One regression | Shell quoting correct: `_powershell_single_quote` for the PowerShell branch, `shlex.quote` for POSIX (`web/terminal_io.py:443-446`); no `window.prompt` anywhere. **But** `window.confirm`/`window.alert` re-entered `launcher.js` — findings N1 (High) and N2 (Low). |
| 5 | Dead code | ✅ Pass | Every key in `default_config.json` is read through `RuntimeConfig` (`terminal.*`, `ssh.keepalive_interval`, `ssh.host_key_policy`, `workspace.*`, `voice_input.*` all wired); no orphan endpoints found. |
| 6 | Architecture / DRY | ✅ Backend, ⚠️ frontend | Backend split is healthy; explorer logic goes through the `_LocalExplorerBackend`/`_SftpExplorerBackend` abstraction; shims re-export only. **Frontend: `terminals.js` is 13,502 lines** — finding N6. |
| 7 | Styling / tokens | ⚠️ Leftovers | `tokens.css` exists and both page stylesheets map onto it; JS reads colors via `cssColor('--t-…', fallback)` (token-first — compliant). **But** 11 emoji/text glyphs remain in `terminals.js` UI — finding N3. `TAB_COLOUR_PALETTE` (8 hex literals, `terminals.js:980-982`) is a functional identification palette, not themed styling — acceptable, noted for completeness. |
| 8 | Interaction | ✅ Pass | Close-session in-page confirm modal, retry affordances, and `.loading` class toggles all present per changelog and locked in by tests (`test_launcher_button_and_dead_display_fixes_locked_in` etc.). Exception is N1's confirm, counted under guardrail 4. |
| 9 | Logging | ✅ Pass | Stream teardown demoted to DEBUG with explicit intentional-close checks before any ERROR (`web/terminal_io.py:507-518`); ANSI strip filter and poll-log filter in `main.py`. Remaining `logger.error` sites sampled are genuine failures. |
| 10 | New-feature contracts | ✅ Pass | Recent features (workspace restore v2, image viewer, reveal-in-file-manager, font presets) all went through `RuntimeConfig` + `/api/app-config`, vendored assets, the shared explorer backend, and room-scoped Socket.IO; no passwords/secrets in new state files (`runtime_state.json` schema v2 explicitly excludes them). |

---

## 3. New findings

### N1 — Native "Save workspace & restart" silently no-ops (`window.confirm` in the native path) — **High**

**Location:** `web/static/js/launcher.js:2570` (`restartApplication`), button at
`templates/index.html:150`. Introduced with the "Save workspace & restart from
the launcher" feature (commit `5084dc4`).

```js
if (!window.confirm('Save the workspace and restart GridVibe? Live shells do not survive a restart.')) {
    return;
}
```

The confirm runs *before* the `window.pywebview?.api?.restart_application`
branch — i.e. it executes in the native WebView2 window, where `window.confirm`
is blocked (the same defect class as deep-dive 4.4, and the exact reason the
Git *Publish* confirmation was moved to the in-page confirm shell per
ISSUE-2026-032/034). In the desktop app the confirm returns falsy, the function
returns, and the restart button does nothing with no error. In browser mode it
works — which is the mode where the feature only degrades to "restart
manually", so the button currently works everywhere *except* the one mode it
was built for.

**Proposed fix:** replace `window.confirm` with the launcher's in-page confirm
shell (mirror the pattern used by the Git Publish / Discard All confirmations
in `terminals.js`, or promote that confirm helper into `shared.js` and use it
from both pages). Add a locked-in test asserting `launcher.js` contains no
`window.confirm` on the restart path.

> **✅ Implemented (2026-07-22).** The launcher page gained its own
> `genericConfirmModal` shell (`templates/index.html`, mirroring the terminals
> page's markup with launcher-idiomatic `ghost-btn`/`danger-btn` buttons) and
> promise-based `openGenericConfirmModal` / `closeGenericConfirmModal` helpers
> in `launcher.js` (backdrop click, Cancel, and Escape all resolve `false`).
> `restartApplication` now awaits the shell ("Restart GridVibe?" / "Save &
> Restart", danger-styled) instead of `window.confirm`, so the button works in
> the native window. Locked in by
> `GuardrailAuditFixesTestCase.test_launcher_page_ships_generic_confirm_shell`
> and `…test_static_js_uses_no_blocking_window_dialogs`.

### N2 — `window.confirm` / `window.alert` in the browser-close flow — **Low**

**Location:** `web/static/js/launcher.js:7` and `:33` (`shutdownBrowserApp`).

Functionally safe today: the function early-returns when
`BROWSER_SHUTDOWN_TOKEN` is empty, and the token is only issued in browser
mode, so these dialogs never execute under WebView2. Still a letter-of-the-rule
violation ("never use `window.prompt`/`confirm`/`alert`") and a copy-paste
hazard — N1 is exactly what happens when this pattern gets imitated in a path
that *does* run natively. Migrate to the in-page confirm/status affordances
when this code is next touched (batches naturally with N1 if the confirm
helper moves to `shared.js`).

> **✅ Implemented (2026-07-22).** Batched with N1: `shutdownBrowserApp` now
> awaits the same `genericConfirmModal` shell ("Close GridVibe?"), and the
> failure `window.alert` became `showMessage(…, 'error')` on the launcher's
> existing status line. No `window.confirm`/`alert`/`prompt` calls remain in
> any static JS (enforced by the N1 test).

### N3 — Emoji glyphs remain in the terminals UI — **Low** (guardrail 7)

**Location:** `web/static/js/terminals.js` — 11 occurrences: 📁 (mode toggle,
`:4311`, `:4921`, `:5200`), 🌐 (browser toggle, `:4323`, `:4933`, `:4976`),
🎤 (voice button, `:4379`), ☾/☀ (theme toggles, `:407`, `:4359`, `:5172`),
❌ (browser-pane load-error label, `:7578`). `launcher.js` and the templates
are clean.

The deep-dive 7.2 fix converted the glyphs known at the time; these are either
leftovers or arrived with newer features (explorer/browser mode toggles, voice,
theme toggle). Guardrail 7 calls for stroke-style `currentColor` SVG icons.
Convert to the existing Feather-style inline-SVG pattern; the `&gt;_` text
glyph on the same toggles can move into the SVG treatment at the same time.

> **✅ Implemented (2026-07-22).** Six new stroke-style `currentColor` icon
> constants in `terminals.js` (`TERMINAL_PROMPT_ICON`,
> `EXPLORER_MODE_FOLDER_ICON`, `BROWSER_MODE_GLOBE_ICON`, `VOICE_MIC_ICON`,
> `THEME_MOON_ICON`, `THEME_SUN_ICON`) replace all 11 emoji sites, including
> the `>_` text glyph on the mode/browser toggles; the ❌ in the load-error
> label was simply dropped (it is a text label, not an icon). The existing
> `.terminal-action-btn svg` rule sizes them; `.voice-btn.recording`'s `color`
> transition carries through `currentColor` unchanged. Locked in by
> `GuardrailAuditFixesTestCase.test_static_js_uses_no_emoji_glyph_icons`.

### N4 — `socketio.emit` while holding `session_manager.lock` — **Low/Medium** (guardrail 2 class)

**Location:** `web/terminal_io.py:90-93` (`_broadcast_session_status`):

```python
with session_manager.lock:
    session = session_manager.sessions.get(session_id)
    if session:
        socketio.emit('session_status', session.to_dict(), room=session_id)
```

The guardrail's letter bans emits under `connection_lock`, but the rationale —
a slow/blocked client write stalling every thread waiting on a shared lock —
applies equally to the manager's RLock, which sits on the launch, close,
status-update, and per-keystroke (`get_session`) paths. `_broadcast_session_status`
is called from status changes, reconnects, and agent-tracking promotions, so a
wedged websocket could briefly serialize session management.

**Proposed fix (3 lines):** snapshot under the lock, emit after:

```python
with session_manager.lock:
    session = session_manager.sessions.get(session_id)
    payload = session.to_dict() if session else None
if payload:
    socketio.emit('session_status', payload, room=session_id)
```

No lock-ordering impact (nothing here takes `connection_lock`).

> **✅ Implemented (2026-07-22).** As proposed: `_broadcast_session_status` in
> `web/terminal_io.py` snapshots `session.to_dict()` under
> `session_manager.lock` and emits after the lock is released. Guardrail 2 in
> `CLAUDE.md`/`AGENTS.md` now covers *both* shared locks. Covered by
> `GuardrailAuditFixesTestCase.test_broadcast_session_status_emits_outside_manager_lock`.
>
> **Supersession note:** the old behaviour was deliberately locked in by
> `SessionStatusBroadcastRaceTestCase` ("Issue 7 — status emission is
> serialized with session removal"), which asserted that a session removal
> *blocks* until a stalled emit returns — the exact degradation N4 targets.
> The serialization was defensive, not load-bearing: the frontend
> `session_status` handler drops unknown session ids
> (`resolveSessionTarget(...) → return`), and the `session_groups_updated`
> debounced refresh reconciles the pane set, so a stale status for a
> just-removed session is a no-op — the same accepted-trade-off shape as
> deep-dive 2.4's replay window. That test was rewritten
> (`test_broadcast_snapshot_does_not_block_session_removal`) to assert the
> new contract, with the supersession recorded in its class docstring.

### N5 — Git-diff `truncated` flag not surfaced in the UI — **Info** (already planned)

Backend returns it (`web/explorer.py:1316-1321`, caps at
`EXPLORER_GIT_DIFF_MAX_BYTES = 256 KiB` / `EXPLORER_GIT_DIFF_MAX_LINES = 4000`);
the frontend only handles *preview* truncation (`terminals.js:8335-8357`), not
diff truncation. This is exactly §4.2/§5.3 of the source-and-diff proposal
(Phase 2 adds the truncation banner) — recorded here as a known gap, **not** a
regression. No action needed outside that proposal.

### N6 — `terminals.js` has regrown a monolith — **Medium** (architecture; blocks nothing, taxes everything)

**Current frontend footprint:** `terminals.js` 13,502 lines, `launcher.js`
3,108, `shared.js` 284, `terminals.css` 3,731. The templates stayed slim
(~280/~420 lines) — the extraction held — but the JS that left
`terminals.html` has kept growing in one file. This is the same class of
problem as deep-dive 6.2 (`web/api.py` at 7.3k lines), one layer up.

Why it matters *now*: three of the four planned features land almost entirely
in this file — the Highlight.js source pipeline, the Diff2Html diff renderer,
and the text editor's edit-mode state machine. Each adds hundreds of lines to
an already-hard-to-review surface, and the explorer viewer code
(`highlightExplorerCode`, `renderExplorerSourceLines`,
`renderExplorerSideBySideDiff`, tab/view state, search marking) is a coherent,
extractable unit with a narrow interface to the rest of the page.

**Recommendation:** before Phase 1 of the source-and-diff proposal, split the
explorer viewer into its own static file (e.g.
`web/static/js/explorer-viewer.js`, loaded after `shared.js` and before
`terminals.js`, same `?v={{ version }}` cache-busting). This is a move, not a
rewrite — the extraction pattern (and its test harness: `_page_html` +
`ExtractedFrontendAssetsTestCase`) already exists from 3.5/6.4. Doing it first
means the two big frontend features are reviewed as changes to a ~4k-line
domain file rather than diffs buried in a 14k-line page controller.

> **⚠️ Guardrail added; split deferred (2026-07-22).** Rule 6 in
> `CLAUDE.md`/`AGENTS.md` now explicitly forbids growing `terminals.js`
> further — substantial new frontend surfaces go in their own static JS file.
> The extraction itself is deliberately **not** done in this pass: it is the
> scheduled opening move of the source-and-diff implementation (§5 item 5) and
> was scoped out of the findings-only fix round.
>
> **✅ Split landed (2026-07-23), Phases 0–2 of
> `docs/terminals_js_split_plan_2026-07-23.md`.** `terminals.js` dropped from
> 13,510 to 7,193 lines via three move-only extractions, each loaded after
> `shared.js` and before `terminals.js` with `?v={{ version }}` cache-busting:
> `terminal-icons.js` (SVG button icons), `voice-input.js` (~1,030 lines: mic
> capture, recording overlay, hold/push-to-talk), and the audit's mandated
> `explorer-viewer.js` (~5,280 lines: file-type classifier, syntax highlight,
> Git diff, source/markdown render, image/mermaid viewer, tabbed viewer,
> breadcrumb, per-tab view/scroll state, saved-tab persistence). All shared
> state, socket init, boot, and the top-level key listeners stayed in
> `terminals.js`. Every moved line is byte-identical; `ExtractedFrontendAssetsTestCase`
> now covers all four files and their load order. Phase 3 (`terminal-grid.js`)
> is optional and remains deferred. The three planned features now land in the
> ~5k-line `explorer-viewer.js` domain file, exactly as this finding asks.

---

## 4. Readiness for the planned features

### 4.1 Richer Source & Diff views (`source_and_diff_view_proposal_2026-07-22.md`) — **Ready**

Every anchor the proposal names exists where it says:

- Language classifier: `EXPLORER_LANGUAGE_BY_EXTENSION` (`terminals.js:8048`)
  + filename map; explicit-language policy already in place.
- Rendering entry points to replace: `highlightExplorerCode` (`:8578`),
  `renderExplorerSourceLines` (`:10602`), `renderExplorerSideBySideDiff`
  (`:9964`).
- Vendored-asset policy proven: `web/static/vendor/` with pinned versions and a
  license README; tests already lock vendored assets in — adding the custom
  Highlight.js build and `diff2html-ui-base` follows an established groove.
- Backend diff caps + `truncated` flag already returned (N5).
- ~2 MiB plain-text fallback for large sources already active (per the 10 MiB
  preview-cap changelog entry).

**Pre-work recommended:** the N6 extraction. **Gap to close during Phase 2:**
N5 (truncation banner).

### 4.2 Explorer text editor (`text_editor_2026-07-20.md`) — **Ready**

The plan's premise — "the hard parts already exist and are reused wholesale" —
checks out against current code:

- Backend abstraction: `_LocalExplorerBackend` (`web/explorer.py:930`) /
  `_SftpExplorerBackend` (`:1052`), both with `resolve_file` /
  `read_file_prefix`; backend chosen via the `_explorer_backend` context
  manager which owns the pooled SSH lifetime (`:1164-1169`).
- Route wrapper: `_explorer_route_response` (`web/api.py:917`), dispatching all
  explorer routes with the ValueError→400 mapping the write route will reuse.
- Guards to reuse: `_explorer_editor_language` (`web/explorer.py:256`),
  `read_explorer_file_preview` + `EXPLORER_FILE_PREVIEW_MAX_BYTES = 10 MiB`
  (`:47`, `:333`), binary detection.
- Atomic-write pattern to mirror: `runtime_state.py:141-146`
  (`.tmp` + `os.replace`) — exactly the §3.2 template.
- Read-only contract currently *intact* (no `write_file` methods, no
  PUT/DELETE explorer routes), so the plan's §6 explicit-contract-change step
  is still the real gate, as designed.

**One caution:** the frontend half (edit-mode overlay, dirty tracking, conflict
bar) lands in `terminals.js` — same N6 argument; the editor becomes much easier
to build against an extracted explorer-viewer module. The plan's §4.6 dirty
confirms must use the in-page confirm shell — N1 is a live reminder of why.

### 4.3 GitHub tags & releases (`github_tags_and_releases.md`) — **Ready (procedural)**

- Version sources in sync: `pyproject.toml` = `gridvibe_version.py` = `1.1.0`.
- `CHANGELOG.md` has a rich `Unreleased` section ready to become `1.2.0`
  (including the prominent reverse-proxy CORS migration warning the doc asks
  for in the notes).
- `CONTRIBUTING.md`, `Makefile`, and `.github/workflows/ci.yml` (push/PR to
  `main`, ubuntu+windows matrix) match the doc's description; no tag/release
  trigger exists yet, as the doc states.
- Version already exposed at runtime via `GET /api/health`
  (`web/api.py:570-577`).

Nothing in the codebase blocks cutting `v1.2.0` whenever the release decision
is made. Optional early win: the installer plan's Stage A (single-sourcing the
version from `gridvibe_version.py` via `dynamic = ["version"]`) is independent
and would remove the two-file sync requirement before the first tag.

### 4.4 Installer & release automation (`installer_and_release_plan_2026-07-20.md`) — **Seams in place, expected gaps only**

The refactors since the deep dive made this plan's landing zones clean:

- **Single path authority:** `web/paths.py` `BASE_DIR` is the sole root, and
  every state/config file derives from it through exactly one named constant
  (`CONFIG_PATH`, `SAVED_SESSIONS_PATH`, `RUNTIME_STATE_PATH`,
  `KNOWN_HOSTS_PATH`, `ENCRYPTION_KEY_PATH`, `AGENT_REGISTRY_PATH`,
  `SELF_UPDATE_REPO_DIR`, log path in `main.py`). Introducing `DATA_DIR` (§7)
  is therefore a centralized, low-risk change — flip the constants, add the
  frozen-vs-source detection and the one-time migration.
- **Updater seam:** `web/selfupdate.py` is an isolated 184-line module with a
  single entry point (`perform_self_update`) and its own error type
  (`AppUpdateError`) — the §6.1 install-type dispatch
  (`perform_release_update` for frozen builds) bolts on without touching the
  git flow. It currently has **no** `sys.frozen` awareness (expected; that *is*
  the plan).
- **Restart machinery:** `web/webview_launcher.py` already handles
  `sys.frozen` in its restart-command builder, as §6.3 assumes.
- **Voice exclusion:** the heavy voice stack is already isolated in
  `requirements-voice.txt`, matching the §5.1 excludes list.
- **Icon:** `docs/images/GridVibe_icon.ico` present.

Expected (planned, not blocking) gaps: no `packaging/` spec, no release
workflow, no `DATA_DIR`, no release-aware updater, `pyproject.toml` version
still static.

---

## 5. Recommended actions, in order

1. ~~**Fix N1** (native restart confirm)~~ — ✅ done 2026-07-22.
2. ~~**Fix N4** (emit outside `session_manager.lock`)~~ — ✅ done 2026-07-22.
3. ~~**Convert N3's emoji glyphs** to the Feather-style SVG pattern~~ — ✅ done
   2026-07-22.
4. ~~**Migrate N2's browser-close dialogs**~~ — ✅ done 2026-07-22 (batched
   with N1).
5. **Extract the explorer viewer from `terminals.js` (N6)** as the opening move
   of the source-and-diff work — it de-risks both that proposal and the text
   editor. *(Guardrail added 2026-07-22 so the file cannot grow further in the
   meantime; the split itself lands with the source-and-diff work.)*
6. Optionally land **installer Stage A** (version single-sourcing +
   `/api/version`) ahead of the first tagged release, since it simplifies the
   release process described in `github_tags_and_releases.md`.

No guardrail requires re-litigation: the deep-dive fixes are intact, tested,
and none of the four planned features needs an architectural change to land.
