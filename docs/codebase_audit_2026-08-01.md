# GridVibe Codebase Audit — 2026-08-01

Scope: full-repo review for bloat, dead code, optimization opportunities, race
conditions, and adherence to the ten Regression Guardrails in `CLAUDE.md`.

Branch `szua_gridvibe_pre-tag` @ `c34a9cc`. Working tree clean.

## Baseline

| Check | Result |
|---|---|
| `python tests/run_tests.py` | **1006 tests, OK** (7 skipped), 32s |
| `python -m ruff check .` | **All checks passed** |
| `web/api.py` size trend (last 12 commits) | 3053 → 3010 lines — **flat, not regrowing** |

## Remediation completed — 2026-08-02

All actionable findings were addressed except §4.3, which was deliberately left
unchanged: `docs/r&d/` remains an internal, gitignored working archive and its
duplicate documents are outside this cleanup's scope.

| Finding | Completed work |
|---|---|
| **1.1 / 1.2** | `capture_workspace()` now preserves an existing manual pin and enforces the auto-slot cap on every write. Direct capture coverage checks manual/auto behavior and the cap; the real rename route now proves a manual slot remains manual while its label changes. |
| **2.1** | Workspace move result snapshots and source pruning now happen atomically in one re-entrant `SessionManager.lock` hold; broadcasts and snapshot-file cleanup remain outside the lock. |
| **2.2** | Runtime-state atomic writes now use a UUID-suffixed, same-directory temporary file, matching the config writer's cross-process-safe pattern. |
| **3.1** | Removed all six unreferenced JavaScript functions and the source-text assertions that pinned three of them in place. |
| **3.2** | Removed `iter_live_workspaces()` and its backwards-compatibility re-export. |
| **3.3** | Removed the unused `connected` and `workspace_joined` emits plus all seven generic `error` emits; rejected input is logged server-side. The two audit references at old lines 2913/2919 were `voice_status` events whose payload status was `error`, not generic `error` events; those remain because the client consumes `voice_status`. |
| **4.1** | Kept the already-deleted `config_bu.json` removal and tightened `.gitignore` to `config*.json` with `!default_config.json`. |
| **4.2** | Removed the specific dead-code-pinning assertions and added the agent rule that new frontend coverage should be behavioral/contract-level rather than raw source-text checks. A broad rewrite of the existing suite was intentionally not mixed into this cleanup. |
| **4.3** | **Deferred by decision.** No files under `docs/r&d/` were moved or deleted. |
| **5.1** | Moved the generic confirm markup to `templates/partials/generic_confirm_modal.html` and its optional-owner controller to `web/static/js/shared.js`. Both pages retain their page-specific button classes without duplicate markup or behavior. |
| **5.2** | Replaced the remaining checkmark glyph icon with a `currentColor` SVG mask. The legacy stylesheet migration remains opportunistic; `AGENTS.md` and `CLAUDE.md` now require token conversion for legacy blocks as they are touched. |
| **5.3** | Demoted routine focus, top-most pulse, reuse, minimize, restore, and maximize window-state messages from INFO to DEBUG. |
| **6** | Refreshed `CLAUDE.md` and `AGENTS.md` for current modules/tests/shared partials, corrected both broken internal-doc citations, documented the local-only reveal endpoint, and carried the audit's regression rules forward. The `runtime_state.py` writer docstring now describes multi-workspace and all three capture paths accurately. |

Final verification:

| Check | Result |
|---|---|
| `.venv\Scripts\python.exe tests\run_tests.py` | **1008 tests, OK** (7 skipped), 32.2s |
| `.venv\Scripts\python.exe -m ruff check .` | **All checks passed** |
| `node --check` on `shared.js`, `launcher.js`, and `terminals.js` | **All checks passed** |
| `git diff --check` | **Passed** |

The project is in good shape. Nothing below is a crash, a data-loss bug, or a
security hole. The findings are one real behavioural bug, one class of
concurrency documentation drift, a modest amount of dead code, and one guardrail
(styling) that new code follows perfectly while two legacy stylesheets never
migrated.

---

## 1. Correctness

### 1.1 — Renaming a workspace silently demotes its pinned manual save `HIGH`

**`web/runtime_state.py:262`, reached from `web/api.py:1627`**

`MAX_AUTO_WORKSPACE_SLOTS` documents the contract:

> an explicit Save Workspace (origin `"manual"`) is never evicted, so it keeps
> its permanent-offer promise.

`capture_live_workspaces()` honours it — it reads the previous slot and keeps a
manual pin (`web/runtime_state.py:375-379`):

```python
slot_origin = (
    "manual"
    if origin == "manual" or previous_slot.get("origin") == "manual"
    else "auto"
)
```

`capture_workspace()` does not. It reads `previous_slot` only for
`native_zoom_factor` and then hardcodes the origin from its argument:

```python
"origin": "manual" if origin == "manual" else "auto",
```

`PATCH /api/workspaces/<id>` (rename) calls it with `origin="auto"`
(`web/api.py:1627-1632`) to keep the slot's label in step. That write demotes a
pinned manual slot to an evictable auto slot.

Reproduced against the real modules:

```
after manual Save Workspace : manual | label: My Project
after rename (origin=auto)  : auto   | label: Renamed Project
persisted origin            : auto     -> now evictable by _evict_excess_auto_slots

control - capture_live_workspaces(origin='auto') over a manual slot: manual
```

**Impact:** Save Workspace → rename it → the deliberately-pinned snapshot is now
one of the 12 auto slots and can be evicted by the autosave timer without the
user ever being told. Silent loss of a snapshot the user explicitly asked to keep.

**Fix:** give `capture_workspace()` the same previous-origin preservation
`capture_live_workspaces()` already has. Both already read `previous_slot`, so
it is a two-line change at the point where `normalized_zoom` is resolved.

**Test gap:** there is currently no test for the rename capture path at all
(`grep 'def test.*rename_workspace' tests/` → no hits), which is why this got
through.

### 1.2 — Two capture paths, only one enforces the auto-slot cap `LOW`

`_evict_excess_auto_slots()` is called from `capture_live_workspaces()` only
(`web/runtime_state.py:401`). `capture_workspace()` — the other write point —
never evicts, despite the constant's comment claiming eviction happens "at the
single write point". Practical impact is bounded (`capture_workspace` only ever
writes a key for a live workspace, and the next autosave tick re-evicts), so this
is latent inconsistency rather than a live bug. Worth folding into the 1.1 fix.

---

## 2. Concurrency

The lock discipline is genuinely good. `SessionManager.lock` is an `RLock` with
its ordering documented at the declaration (`sessions/manager.py:202-204`) and
mirrored at `connection_lock` (`web/terminal_io.py:76-79`). Every
`socketio.emit` in `web/terminal_io.py` is outside the lock, each with a comment
saying why. `_connect_ssh_session` and `_connect_local_session` both re-validate
the session *inside* `connection_lock` before inserting into the registry
(`terminal_io.py:971-979`, `1073-1081`), closing the connect/close leak window.
Check-then-act transactions that matter — `update_group_saved_session`,
`merge_browser_tabs`, `move_group`, `create_group` — are each a single lock hold.

Two things to tidy:

### 2.1 — Misleading comment in `move_group_to_workspace()` `LOW`

**`web/workspaces.py:630-641`**

```python
# Snapshot both sides under the manager's own lock, then emit outside it.
source_groups = [...]   # acquires + releases the lock
target_groups = [...]   # acquires + releases the lock again
pruned_source = (... and session_manager.remove_workspace(...))  # third acquisition
```

The comment says "under the manager's own lock" (singular); this is three
independent acquisitions. The *destructive* step is safe by luck of good design
elsewhere — `remove_workspace()` re-checks emptiness under its own lock
(`sessions/manager.py:320-329`) and refuses a workspace that gained a group. But
the `source_groups` / `target_groups` lists returned to the client can be stale
relative to the prune decision. Either wrap the three in one `with
session_manager.lock:` (it is re-entrant, so this is safe) or correct the comment.
The current text will mislead the next reader into assuming an atomicity that
isn't there.

### 2.2 — Cross-process temp-file collision in the snapshot writer `LOW`

**`web/runtime_state.py:190`** — `temp_path = f"{RUNTIME_STATE_PATH}.tmp"`, a
fixed name. `_runtime_state_lock` serialises in-process writers, so this is fine
today. `web/config.py:139` solves the same problem with a UUID-suffixed temp
name. Two GridVibe processes against one directory would corrupt each other's
write here but not in `save_config`. Cheap to align.

**Not a finding, checked and clean:** `close_extra_workspaces()`
(`web/workspaces.py:1087-1104`) does its check-then-act in one hold and moves
both the SSH teardown and the emit outside it, exactly as guardrail 2 requires.

---

## 3. Dead code (guardrail 5)

### 3.1 — Six unreferenced JS functions `MEDIUM`

Each appears exactly once in the shipped frontend — its own definition. All six
have a live successor:

| Function | File | Superseded by |
|---|---|---|
| `explorerFindRanges()` | `explorer-viewer.js:409` | `explorerFindRangesAsync()` (line 440) |
| `handleExplorerCopyPathMenu()` | `explorer-viewer.js:1309` | `wireExplorerCopyPathMenu()` |
| `explorerViewerEl()` | `explorer-viewer.js:5668` | — orphan |
| `hasSharedGridEdge()` | `terminals.js:2893` | inlined `getSharedGridEdgeSegments(...).length > 0` |
| `closeCurrentSession()` | `terminals.js:7360` | — orphan |
| `_saveVoicePrefs()` | `voice-input.js:85` | `saveVoicePrefs()` in `app-settings.js:655` |

**Three of them are pinned in place by tests** that assert the function's
*source text* exists — `tests/test_api.py:1305` (`explorerFindRanges`),
`:6147` (`handleExplorerCopyPathMenu`), `:1099`/`:13826` (`closeCurrentSession`).
Deleting the dead code requires deleting the assertions that guard it. See 4.2.

### 3.2 — `iter_live_workspaces()` is dead `MEDIUM`

**`web/runtime_state.py:326-342`** — 17 lines, imported into `web/api.py:154` as
a re-export and never called from anywhere, tests included.
`capture_live_workspaces()` uses `session_manager.snapshot_live_workspaces()`
instead. Delete the function and the re-export.

### 3.3 — Three Socket.IO events with no listener `LOW`

The client registers exactly nine listeners. The server emits three events that
nothing on the client handles:

- `workspace_joined` (`web/api.py:2642`) — appears **once in the entire
  codebase**, at the emit. Pure dead weight.
- `connected` (`web/api.py:2618`)
- `error` (`web/api.py:2636, 2639, 2653, 2682, 2738, 2759, 2766`)

`error` is the interesting one: it is not merely dead, it is a **silent failure
path**. Seven emit sites push messages like `"Missing session_id"` and
`"Session is not connected"` to a client that has no `socket.on('error', …)`.
Every one of those failures is invisible to the user. Either wire a listener
(guardrail 8 wants failure states surfaced) or drop the emits.

---

## 4. Bloat

### 4.1 — `config_bu.json` is a committed personal config backup `MEDIUM`

A tracked 1,059-byte file that nothing reads — only `config.json` and
`default_config.json` are ever loaded (`web/config.py:20-21`). It is someone's
`config.json` backup, and it carries:

- **`"cors_origins": ["*"]`.** A wildcard here defeats *two* defences at once:
  `_resolve_cors_origins()` hands it straight to Socket.IO
  (`web/app.py:46-48`), and `_allowed_write_origin_netlocs()` returns `None` on
  `"*"`, which makes `_reject_cross_origin_writes()` allow everything
  (`web/app.py:79-80, 105-107`). A file named `_bu` invites exactly the
  copy-back-over-`config.json` that would ship that. This is not an active
  vulnerability — it is a loaded footgun committed to the repo.
- Five config keys nothing reads: `ssh.default_username`,
  `terminal.default_rows`, `terminal.default_cols`,
  `voice_input.vosk_service_port`, plus a whole `voice_prefs` block.
- Personal settings (`Cascadia Code`, `pttKeybind: "Ctrl+Dead"`).

`config.json` is correctly gitignored; this backup slipped past because the
ignore rule matches the exact name. **Delete it and add `config*.json` (with a
`!default_config.json` negation) to `.gitignore`.**

### 4.2 — `tests/test_api.py` is 13,971 lines and tests source text `MEDIUM`

The single largest file in the repo — larger than `terminals.js` (6,948) and
43% of all Python. Of its 1,792 `assertIn` calls, **1,241 assert against raw
JS/HTML/CSS source strings** rather than behaviour:

```python
self.assertIn("function explorerFindRanges(content, query, maxMatches = ...)", html)
```

Two concrete costs, both already realised: it pins dead code in place (3.1), and
any rename or reformat of a JS function signature breaks tests that never
exercised the behaviour. This is a deliberate trade the project made to get
frontend coverage without a JS test runner, and it did catch real regressions —
but it has now grown past the point where it constrains refactoring. Worth
capping: no *new* source-text assertions; convert the highest-churn ones to
behavioural checks opportunistically.

### 4.3 — `docs/r&d/` is 1.9 MB with 11 duplicated documents `LOW`

Eleven basenames exist in two or three directories at once across `planed/`,
`planed/done/`, `planed/done/archive/`, `half_done/`, `archive/`, and
`installer/` — including `deep_dive_review_2026-07-10.md`,
`guardrail_audit_2026-07-22.md`, and `release_and_installer_plan_2026-07-25.md`
(three copies). Nothing indicates which copy is authoritative. The directory is
gitignored so none of it ships, but `CLAUDE.md`'s contract text cites these paths
as the specification of record — an ambiguous specification is worse than a
missing one. Collapse to one copy per document.

---

## 5. Guardrail scorecard (audit-time)

| # | Guardrail | Status | Notes |
|---|---|---|---|
| 1 | Security | **PASS** | Same-origin CORS default, cross-origin write guard, room-scoped emits, host-key policy all intact and commented. Only blemish is 4.1, an unread file. |
| 2 | Concurrency | **PASS** | Lock ordering documented at both declarations. Emits consistently outside locks. See 2.1 for a comment fix. |
| 3 | Performance | **PASS** | No CDN assets (`vendor/` is pinned + local). No sub-second polling. Explorer git-watch backs off 5s → 60s. SSH uses a pool. Status poll is a 15s fallback that only runs while the socket is down. |
| 4 | Correctness | **PASS** | Zero `window.prompt/confirm/alert` outside vendored `xterm.min.js`; the three hits are comments citing the guardrail. Shell quoting helpers used correctly; CLI flags beat config (`resolve_server_settings`). |
| 5 | Dead code | **DRIFT** | All 22 `RuntimeConfig` attributes are read; all 56 HTTP routes are reachable from the frontend. But 6 dead JS functions, 1 dead Python function, 3 dead socket events. See §3. |
| 6 | Architecture/DRY | **DRIFT** | `web/api.py` flat at ~3010 lines — the split held. `explorer_fs.py` respects the backend abstraction (65 `backend.*` calls vs 2 direct `os.*`). New surfaces got their own modules. **But see 5.1 below.** |
| 7 | Styling | **DRIFT** | New surfaces are perfect; two legacy stylesheets never migrated. See 5.2. |
| 8 | Interaction | **PASS** | 20 `openGenericConfirmModal` call sites; workspace close confirms; busy states toggle classes. Caveat: the dead `error` socket event (3.3) means some failures surface nowhere. |
| 9 | Logging | **PASS** | `_StripAnsiFilter` + `_SuppressPollLogs` wired in `main.py:88,97`. Current log: 1,892 lines, **zero ERROR**, one WARNING. See 5.3 for a volume note. |
| 10 | New features | **PASS** | Multi-workspace went through `RuntimeConfig`, room-scoped Socket.IO, and the shared launch service; no new secret persistence. |

### 5.1 — `openGenericConfirmModal` is duplicated across both pages

Guardrail 6 says *"JS shared between the two pages goes in `shared.js`"*, and
guardrail 4 mandates this modal **everywhere**, which makes it a shared surface
by definition. It is instead defined twice:

- `web/static/js/launcher.js:1875`
- `web/static/js/terminals.js:1650` (same body, plus an `owner` parameter)

The markup is duplicated too — `templates/index.html:303` and
`templates/terminals.html:305`, both `<div id="genericConfirmModal">`.

The repo already has the right pattern next door: `templates/partials/` holds
`app_settings_modal.html` and `workspace_name_modal.html`. The confirm modal is
the one shared dialog that never got the treatment. Move the markup to a partial
and the function to `shared.js`, keeping `owner` as an optional argument.

### 5.2 — Two legacy stylesheets bypass `tokens.css`

Both pages load `tokens.css` (36 tokens). Measured colour literals per file,
separating custom-property *definitions* from scattered literals:

| Stylesheet | Local token defs | Scattered literals | Tokens used from `tokens.css` |
|---|---|---|---|
| `tokens.css` | 36 | 0 | — |
| **`terminals.css`** | **122** | **179** | 9 |
| **`launcher.css`** | **22** | **158** | 9 |
| `app-settings.css` | 0 | **0** | 18 |
| `workspaces.css` | 0 | **0** | 12 |

`terminals.js` adds 14 more hex literals.

The signal here is encouraging: **every surface written since the guardrail
landed is 100% compliant** — `app-settings.css` and `workspaces.css` define no
local palette and take everything from `tokens.css`. The two legacy page
stylesheets maintain a parallel `--t-*` palette of 144 tokens plus 337 loose
literals, and consume only 9 shared tokens each. `tokens.css` is currently
serving the two newest surfaces and little else.

Not urgent, and a big-bang migration is not worth it. Suggested rule: any block
of `terminals.css`/`launcher.css` you touch converts its literals to
`tokens.css` variables on the way past.

Checked and **not** a finding: the 26 `var(--x)` references that resolve to no
stylesheet declaration (`--tab-color`, `--session-color`, `--grid-columns`,
`--explorer-editor-font-size`, …) are all set from JS via
`element.style.setProperty()` and all carry fallbacks. Correct as written.

Emoji check (guardrail 7): no pictographic emoji anywhere. The hits are typographic
arrows (`→ ↑ ↓ ↔ ↗ ↪`) inside prose strings, plus one `✓` in `launcher.css`. Only
the `✓` is functioning as an icon and worth swapping for an SVG.

### 5.3 — `web.webview_launcher` is the noisiest logger

Of 1,892 log lines, 390 INFO come from `web.webview_launcher` (second only to
werkzeug's 1,128), including 41× *"Skipping top-most pulse …"*, 36× *"Bringing
workspace window to front"*, and 30× *"Keeping existing workspace window open"*.
These are routine window-management events. Guardrail 9's rule is about teardown
at DEBUG rather than ERROR, so this is not a violation — but it is the same
spirit, and `webview_launcher.py` (1,341 lines) is the most heavily-edited module
of the last release. Demoting the routine window-state lines to DEBUG would cut
log volume ~20%.

---

## 6. Documentation drift

`CLAUDE.md`'s repo layout predates the multi-workspace release. Missing from it:

- `web/workspaces.py` (974 lines) — the workspace identity + launch + restore service
- `web/explorer_fs.py` (1,010 lines) — the filesystem mutation policy
- `web/static/js/workspaces.js`, `web/static/js/explorer-fs.js`, `web/static/css/workspaces.css`
- `utils/bump_requirements.py`
- `tests/test_multi_workspace.py`, `test_explorer_fs.py`, `test_explorer_search.py`, `test_bump_requirements.py`, `test_cleanup.py`, `test_version.py`, `test_vosk_service.py`
- `templates/partials/workspace_name_modal.html`

Two guardrail citations point at paths that no longer exist:

- Guardrail preamble cites `docs/deep_dive_review_2026-07-10.md` → actually `docs/r&d/planed/deep_dive_review_2026-07-10.md` (and a second copy under `planed/done/`)
- Guardrail 6 cites `docs/terminals_js_split_plan_2026-07-23.md` → actually `docs/r&d/planed/done/archive/terminals_js_split_plan_2026-07-23.md`

The Key Concepts paragraph enumerates six explorer mutation families but omits
`POST /api/explorer/<id>/reveal` (`web/api.py:1308`), which launches the host OS
file manager. The endpoint is correctly implemented — local panes only, no file
mutation, and its own docstring explains the carve-out — but a contract that
enumerates its exceptions should enumerate all of them.

`web/runtime_state.py`'s module docstring is also stale: it says *"Exactly two
writers exist … and both funnel through `capture_workspace`"* (the autosave timer
actually calls `capture_live_workspaces`, and there are three call sites), and
*"today there is exactly one `"default"` workspace"*.

---

## Recommended order (audit-time)

1. **§1.1** — preserve `origin: "manual"` in `capture_workspace()`, add the
   missing rename-path test. Only user-visible bug here.
2. **§4.1** — delete `config_bu.json`, tighten `.gitignore`.
3. **§3.2 / §3.1** — delete `iter_live_workspaces()`, then the six dead JS
   functions plus the three test assertions pinning them.
4. **§3.3** — decide on `error`: wire a client listener or drop the emits.
   Drop `workspace_joined` and `connected` either way.
5. **§6** — refresh `CLAUDE.md`'s layout and fix the two broken doc paths.
6. **§5.1** — move the confirm modal to `templates/partials/` + `shared.js`.
7. **§2.1, §2.2, §1.2, §4.3, §5.3** — opportunistic tidying.
8. **§5.2** — no dedicated migration; convert literals in blocks you touch.
