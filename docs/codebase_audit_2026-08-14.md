# GridVibe Codebase Audit — 2026-08-14

Scope: full-repo review against the ten Regression Guardrails in `CLAUDE.md`,
looking for bugs, race conditions, dead code, monolith regrowth, and drift since
`docs/r&d/codebase_audit_2026-08-04.md` (and its 2026-08-05 remediation).

Branch `szua_gridvibe-wrk-test` @ `ece482f`. Working tree clean except one
untracked proposal document (`docs/remote_wireshark_capture_proposal_2026-08-14.md`).

## Baseline

| Check | Result |
|---|---|
| `.venv\Scripts\python.exe tests\run_tests.py` | **1,630 tests, OK** (8 skipped), 99–146 s across two runs |
| `.venv\Scripts\python.exe -m ruff check .` | **All checks passed** |
| `node --check` on all 28 first-party JS files | **All passed** |
| `git diff --check` | **Clean** |
| `TODO`/`FIXME`/`HACK`/`XXX` in first-party source | **Zero** |

Delta since the last audit's baseline (`fdbf27f`): **102 commits, 90 files,
+37,821 / −4,398 lines** — five times the previous inter-audit delta. Feature
work: the whole session/workspace persistence programme (Stages 0–4), the
lifecycle save-or-exit transaction, the in-place editor overlay with find and
occurrence tint, multi-entry explorer selection, the change-overview ruler,
tree file-find, the hover-reveal top bar, the Git commit context menu, and the
one-banner launcher notification surface.

Test suite grew 1,031 → **1,630 tests (+58%)**.

## Verdict

Nothing here is a crash, a data-loss bug, or a security hole, and the two
guardrails that matter most under this much change — **security (1) and
concurrency (2) — are in better shape than at the last audit**, not worse. A
mechanical scan of every `with <lock>` block in `web/` and `sessions/` finds
**zero** `emit`/`socketio.emit` calls under any lock and zero reverse-order
nestings, across code that grew by a third.

The drift is elsewhere, and it has a theme. The previous audit closed fourteen
findings: **nine were code changes and all nine held**; three were explicit
deferrals and all three are still open, unchanged; and **two were rule changes
written into `CLAUDE.md`/`AGENTS.md`, and both have been reverted** — the
narrowed source-text-assertion rule (§5.1) and the `explorer-diff.js` extraction
trigger (§4.1). In the gap left by the second, `explorer-viewer.js` grew another
1,029 lines. `CLAUDE.md` also acquired a broken citation of its own, to an audit
document that does not exist. **The remediations with a test, a lock, or a
deleted line behind them held perfectly; the two that lived only as prose did
not survive ten days.**

The two genuinely new defects are a `git fetch` that can hang a request thread
forever, and a runtime log in which 84 % of every line is paramiko.

---

## 0. Prior audit — remediation verified

Re-checked against the current tree, not against the remediation notes.

| Prior finding | Status now |
|---|---|
| 1.1 `terminal_input` WARNING per keystroke | **Held** — still `logger.debug`; log shows 0 WARNING |
| 2.1 dead `ssh.default_port` key | **Held** — all **27** `default_config.json` leaf keys have a reader |
| 2.2 two unpruned frontend collections | **Held** — prunes still at genuine-teardown doors only |
| 3.1 `update_session_metadata` allowlist gap | **Held** — `explorer_source_font` present |
| 4.1 `explorer-viewer.js` regrowth | **REGRESSED** — rule deleted from `CLAUDE.md`, file +1,029 lines. See §4.1 |
| 4.2 `getStep2DefaultDirectory` duplicated | **Held** — one copy in `shared.js` |
| 4.3 two `getGridMetrics` contracts | **Held** — unified, nullable contract |
| 5.1 source-text assertion rule unfollowable | **REGRESSED (rule text)** — narrowing reverted. See §5.1 |
| 6.1 six rendered text glyphs | **Held for those six** — but the sweep was incomplete. See §6.1 |
| 6.2 two literals in the occurrence-tint block | **Held** — `--gv-match-rgb` still in use |
| 7.1 stale doc citations in source | **Held** — **zero** `docs/r&d/` citations in any tracked source file |
| 7.2 `docs/r&d/` duplication | **Still deferred by decision** |
| 8.1 occurrence-tint DOM walk unbounded | **Still open** — `explorer-viewer.js:6178` unchanged |
| 8.2 download buffers in memory; silent browser-mode failure | **Still open, and the second half is worse.** See §1.2 |

Also confirmed clean and unchanged: derived same-origin CORS
(`web/app.py:130`, re-pointed by `apply_resolved_server_origins`), the
cross-origin write guard (`web/app.py:188`), room-scoped Socket.IO emits, Fernet
password handling, and `set_missing_host_key_policy` reachable **only** through
`web/hostkeys.py:81-85` — three call sites, no direct paramiko use anywhere
else. `.gitignore` now also covers the new state sidecars
(`*.lock`, `*.bak`, `*.corrupt-*`, `..encryption_key.*.tmp`).

---

## 1. Correctness

### 1.1 — Self-update `git fetch` has no timeout and no `GIT_TERMINAL_PROMPT=0` `MEDIUM`

**`web/selfupdate.py:31-37`**

```python
return subprocess.run(
    [git_path, "-C", SELF_UPDATE_REPO_DIR, *args],
    capture_output=True,
    text=True,
    check=False,
)
```

No `timeout=`, and no `env`. `perform_self_update()` drives six git commands
through this runner, one of which is `["fetch", "--all", "--prune"]`
(`:94`) — a network operation against whatever remote the checkout tracks.

Two ways it hangs indefinitely:

1. **Credential prompt.** With no `GIT_TERMINAL_PROMPT=0`, a private remote over
   HTTPS with no cached credential makes git block on a terminal prompt that
   nobody will ever answer — the server has no controlling terminal for the
   user. `web/explorer.py:1032` sets exactly this variable for its own writes,
   and `CLAUDE.md` names the rule: *"Mutating Git commands run with
   `GIT_TERMINAL_PROMPT=0` so they never hang on credential prompts."* The
   self-update runner is the one git caller that does not follow it.
2. **Stalled transport.** An unreachable or half-open remote with no timeout
   parameter simply never returns.

Every other `subprocess.run` in the repo carries a bound —
`web/agents.py` 5 s and 8 s (×5 sites), `web/voice.py`
`VOICE_INSTALL_TIMEOUT_SECONDS`, `web/explorer.py` a caller-supplied `timeout`.
This is the only one without.

**Blast radius.** `async_mode="threading"`, so one hung request does not stall
the event loop — but it permanently parks a Werkzeug worker thread, the
launcher's `fetch('/api/app-update')` (`launcher.js:2442`) has no client-side
timeout either, and the button offers no cancel. Each click leaks another
thread. `perform_self_update()` also runs entirely inside the request, so
`/api/app-update` never returns.

**Fix.** Give `_run_repo_git` a `timeout=` (30 s is generous for `fetch`; 10 s
for the five local commands) and an env with `GIT_TERMINAL_PROMPT=0`, catching
`subprocess.TimeoutExpired` into the existing `AppUpdateError` path so the
launcher banner reports it. Five lines, no behaviour change on the happy path.

> **Resolution (2026-08-14, Stage 1.1).** Fixed, but **the five-line fix above
> does not work on Windows** and the finding understated the problem.
>
> `GIT_TERMINAL_PROMPT=0` and the two bounds landed as described
> (`SELF_UPDATE_NETWORK_TIMEOUT = 30` for `fetch` **and** `pull --ff-only` —
> the audit filed `pull` under "local commands", but it contacts the remote
> too — and `SELF_UPDATE_LOCAL_TIMEOUT = 10` for the rest). Measured against a
> remote that accepts the connection and then goes quiet, that version reported
> its 30 s timeout after **268.8 s**.
>
> `subprocess.run(timeout=…)` bounds the wait *before* the kill, then kills only
> the direct child; on Windows it reaps with an unbounded `communicate()`
> (`Lib/subprocess.py`, `if _mswindows:` branch). `git fetch` passes our
> stdout/stderr pipes to its transport helpers, so a surviving helper keeps the
> write end open and the reader threads block until git gives up on its own.
> The bound was being enforced against the process we launched rather than
> against the handles we read.
>
> `_run_repo_git` therefore drives `Popen` + `communicate(timeout=…)` itself and
> kills the process **group** (`taskkill /F /T` on Windows, `killpg` elsewhere,
> with the group established at spawn), then reaps under
> `SELF_UPDATE_REAP_TIMEOUT = 5`. Same case now returns in **30.2 s** with no
> orphan left running. Regression test:
> `test_repo_git_timeout_bounds_a_remote_that_goes_quiet` in `tests/test_api.py`
> stalls a local listener and asserts the whole call returns well inside the
> minutes the old path took.

### 1.2 — Browser-mode download reports success unconditionally, and multi-select multiplied it `MEDIUM`

**`web/static/js/explorer-viewer.js:6654-6660`**

```js
const link = document.createElement('a');
link.href = url;
link.download = fileName;
document.body.appendChild(link);
link.click();
link.remove();
showTerminalToast(`Downloading ${fileName}…`, 'success');
```

The programmatic anchor cannot observe the server's response, so a `404` (a
stale tree row), a `403`, or the 100 MB refusal all produce a **green success
toast and no file**. The pywebview branch immediately above handles all three
correctly (`:6634-6651`).

The 2026-08-04 audit flagged this as §8.2 and called it *"the more user-visible
of the two and the better next pick."* It was not picked, and `a72a57f` widened
it: `downloadExplorerFiles()` (`:6663`) now issues **N** of these sequentially
for a multi-entry selection, so one batch of nine stale rows yields nine green
toasts and nothing on disk. Guardrail 8 requires failure states to have a retry
affordance; here the failure has no *state*.

**Fix.** Replace the anchor with `fetch` → `blob` → object-URL anchor, so the
status is observable and the existing error toast can carry it. That does buffer
the body client-side; capping the fetch path at a threshold and falling back to
the anchor above it keeps large downloads streaming. Pairs naturally with §8.2.

### 1.3 — `workspace_label_conflict()` is check-then-act across two stores `LOW`

**`web/workspaces.py:69-130`**

The label namespace owner reads live workspaces and then
`list_restorable_workspaces()`, returning a `409` payload or `None`; the caller
then creates or renames. Nothing holds a lock across check and act, and the two
reads are not a single snapshot, so two concurrent creates of the same name can
both pass.

Not raised higher because GridVibe binds to `127.0.0.1`, is single-user by
design, and the consequence is a duplicate label rather than lost state. Worth
recording so the next reader does not assume the function is a mutex.

---

## 2. Logging (guardrail 9)

### 2.1 — 84 % of every log line is paramiko; first-party retention collapsed `MEDIUM`

**`main.py:66-99`** sets `root.setLevel(INFO)` and adds `_SuppressPollLogs` to
`werkzeug` and `_StripAnsiFilter` to the file handler. **No third-party logger
level is set at all**, so paramiko logs at INFO.

Measured on the live log directory:

| File | Total lines | paramiko lines | Not paramiko/werkzeug/engineio |
|---|---|---|---|
| `gridvibe.log.1` (a full, sealed 2 MB rotation) | 19,124 | 16,059 (**84.0 %**) | **298** |
| `gridvibe.log` (live, ~65 min of one session) | 5,219 | 4,410 (**84.5 %**) | **1** |

The rotated file is the stable evidence: it is sealed at exactly 2,097,078 bytes,
so **one whole rotation retains 298 GridVibe lines**. The live file is quoted only
to show the ratio is not an artefact of one session — it was still growing at
roughly 100 lines/minute while this audit was being written.

The lines are all this shape, two per explorer operation:

```
2026-08-14 10:31:00,678  INFO  paramiko.transport.sftp  [chan 2298] Opened sftp connection (server version 3)
2026-08-14 10:31:00,682  INFO  paramiko.transport.sftp  [chan 2298] sftp session closed.
```

Channel 2,298 in about an hour. That is **not** a pooling bug — `web/explorer.py:2855-2861`
documents the design deliberately: the SSH *transport* is pooled per session and
each request opens its own SFTP *channel*, because paramiko SFTP clients are not
safe for concurrent use and a channel on a live transport is cheap. The design is
right. What was never done is muting paramiko's own INFO chatter about it.

**Why it matters more than it looks.** A 2 MB rotation now retains roughly **300
first-party lines**. With `MAX_LOG_BACKUPS = 10` the entire 22 MB log budget
holds on the order of 3,000 GridVibe lines — a few hours of diagnostics, where
before it held a hundred times that. The log's signal is excellent (0 ERROR, 0
WARNING across 19,122 lines); it is simply almost impossible to find. Guardrail 9
exists to keep exactly this out of `logs/gridvibe.log`, and both existing
mechanisms (`_SuppressPollLogs`, the `webview_launcher` demotion) were built for
smaller versions of the same problem.

**Fix.** One line in `setup_logging()`:

```python
logging.getLogger("paramiko").setLevel(logging.WARNING)
```

Genuine SSH failures still surface — paramiko logs auth and transport errors at
WARNING/ERROR. `docs/logging_guide.md` should gain the third-party-level section
alongside its `_SuppressPollLogs` / `_StripAnsiFilter` entries.

> **Resolution (2026-08-14, Stage 1.2).** Fixed as
> `logging.getLogger("paramiko").setLevel(level if debug else logging.WARNING)`
> in `setup_logging()`. The `debug` branch is the one deviation from the
> proposed one-liner: `--debug` keeps the full paramiko stream, so diagnosing an
> SSH problem is a flag rather than a code change. Verified by emitting the two
> `paramiko.transport.sftp` INFO lines against a real file handler — 0 reach the
> file, while a paramiko WARNING and a first-party INFO both do.
> `docs/logging_guide.md` gained the **Third-Party Logger Levels** section, and
> `tests/test_main.py::test_setup_logging_mutes_paramiko_chatter_outside_debug`
> covers both directions.

---

## 3. Dead code (guardrail 5)

The sweep is otherwise clean and worth recording as such: **all 27
`default_config.json` leaf keys have a reader**, **all 24 `RuntimeConfig`
attributes are read outside `web/config.py`**, all **63** HTTP routes are
reachable from the frontend (`git/discard-all` reaches its URL through
`performExplorerGitAction(index, 'discard-all', {})`, not a literal), and
Socket.IO parity is exact — 11 client emits ↔ 13 server handlers, 9 server event
names ↔ 9 client listeners, no orphans either way.

Three symbols are defined and never referenced. The previous audit's sweep found
zero.

### 3.1 — `explorerSelectionFor()` `LOW`

**`web/static/js/explorer-viewer.js:1136`** — introduced by `a72a57f`
(multi-entry selection). Builds a scope and delegates to
`GridVibeExplorerSelection.scopedSelection()`. Zero call sites in any JS file,
template, or test. Its siblings `explorerSelectionScope()` and
`storeExplorerSelection()` are both live, so this is the read half of a pair
whose callers ended up going elsewhere.

### 3.2 — `getExplorerThemeStore()` `LOW`

**`web/static/js/terminals.js:438`** — introduced by `5a61714`. A one-line
pass-through to `GridVibeExplorerThemeStore.readStore(localStorage)`. Its four
neighbours (`hasExplorerThemeOverride`, `getExplorerTheme`, `saveExplorerTheme`,
`normalizeExplorerTheme`) are all called; this one is not.

### 3.3 — `get_runtime_state_store()` `LOW`

**`web/runtime_state.py:1178`** — `return _default_store`. Zero references
anywhere in the repo, tests included. Every consumer reaches the store through
the module-level functions (`capture_workspace`, `clear_workspace`, …).

All three are one-line deletions with no call-site churn.

> **Resolution (2026-08-14, Stage 1.3).** All three deleted. Re-confirmed zero
> references across JS, templates, Python, and tests before removing each one;
> the suite is unchanged apart from growing by the Stage 1.1/1.2 tests.

---

## 4. Architecture / monoliths (guardrail 6)

### The headline numbers

| File | 2026-08-04 (`fdbf27f`) | now (`ece482f`) | Δ |
|---|---|---|---|
| `web/static/js/explorer-viewer.js` | 7,721 | **8,750** | **+1,029 (+13.3 %)** |
| `web/static/js/terminals.js` | 7,644 | 8,214 | +570 (+7.5 %) |
| `web/static/js/launcher.js` | 3,165 | 3,702 | +537 |
| `web/api.py` | 3,007 | 3,418 | +411 |
| `web/explorer.py` | 3,134 | 3,335 | +201 |
| `sessions/manager.py` | 1,087 | 1,640 | +553 |
| `web/workspaces.py` | 1,136 | 1,628 | +492 |
| `web/static/css/terminals.css` | 5,164 | 5,878 | +714 |
| `tests/test_api.py` | 16,215 | 18,056 | +1,841 |
| `tests/test_multi_workspace.py` | 2,261 | **6,177** | **+3,916 (+173 %)** |

**The frontend split rule is working, and the numbers prove it.** First-party JS
went from **25,676 lines across 14 files** to **33,915 across 28**. Fourteen new
modules landed — `explorer-overview.js`, `explorer-selection.js`,
`explorer-edit-find.js`, `explorer-edit-highlight.js`, `explorer-edit-overlay.js`,
`explorer-tree-search.js`, `explorer-git-menu.js`, `explorer-persistence.js`,
`explorer-theme-store.js`, `session-persistence.js`, `lifecycle.js`,
`notice-banner.js`, `topbar-peek.js`, `voice-dictation.js`. **Only 19 % of the
8,239 new frontend lines went into the two monoliths**; 81 % went into new
domain files. That is the guardrail doing its job under the heaviest feature load
in the project's history.

**`web/api.py` did not regrow either**, despite the +411. It went from **80 to 89
route/socket handlers** — 46 lines per new handler, against a standing average of
38. The first route is at `:652`, so the file is ~650 lines of imports and
backwards-compatibility re-exports plus 89 thin delegating handlers. The
previous audit celebrated it being "dead flat"; flat was never the goal, and
constant density under +9 endpoints is the same success.

### 4.1 — `explorer-viewer.js`: the extraction trigger was deleted, not executed `MEDIUM`

The 2026-08-05 remediation recorded, verbatim:

> **§4.1** — the file was **not** split. […] What was adopted is the audit's own
> suggested rule, now in guardrail 6 of both `CLAUDE.md` and `AGENTS.md`: *the
> next diff-view change extracts `explorer-diff.js`; new explorer surfaces that
> are not viewer-core get their own file from the start.*

Guardrail 6 in the current `CLAUDE.md:206` and `AGENTS.md:86` reads:

> `explorer-viewer.js` is the current regrowth risk — it is the largest frontend
> file, and its Diff, Git-sidebar, tab-strip, and Markdown domains are separable.

The second half of the rule survived and worked (see the +81 % new-file ratio
above). **The first half — the only part with a trigger attached — is gone**, and
with it the thing that would have fired. There is no `explorer-diff.js`. The file
is now **8,750 lines, 317 top-level functions**, and the domain interleaving that
made it hard to navigate has got worse:

| Domain | Functions | First fn | Last fn | Span |
|---|---|---|---|---|
| Viewer core / other | 103 | 10 | 8,638 | 8,628 |
| **Diff / commit history** | **49** | **861** | **8,433** | **7,572** |
| Files tree / listing | 40 | 813 | 7,806 | 6,993 |
| Git sidebar | 32 | 829 | 6,822 | 5,993 |
| Markdown / preview | 30 | 373 | 7,703 | 7,330 |
| Find / search | 19 | 409 | 6,519 | 6,110 |
| **Tab strip** | **17*** | **6,778** | **7,826** | **1,048** |
| Syntax highlight | 13 | 266 | 6,238 | 5,972 |
| Selection | 9 | 1,128 | 5,985 | 4,857 |
| Editor glue | 5 | 4,446 | 4,509 | 63 |

*Buckets are assigned by function-name pattern, so the counts are indicative
rather than exact and "Viewer core / other" absorbs whatever the patterns miss.
The **Tab strip** row was hand-verified because Stage 4 depends on it, and the
name-based count understates it: **32 tab-domain functions**, of which **29 sit
in one contiguous band at `6,754–7,830`** — see Stage 4 for the exact shape.

Nine of the ten domains are smeared across 5,000–8,600 lines apiece. The Diff
domain is still the largest separable one (49 functions, up from 42) and still
the least entangled with tab state. **The tab strip is the only domain in the
file that is already blocked rather than smeared**, which makes it the cheapest
cut available.

This is not urgent — 8.7k is well short of the 13.5k that forced the original
`terminals.js` split — but it is now the *second* audit to record the same trend
with no extraction, and the mechanism that was supposed to prevent that was
removed between them.

### 4.2 — `web/workspaces.py` breaks 25 import cycles at call time `LOW`

`web/workspaces.py` (1,628 lines, +492) carries **25 function-level imports**,
against 1 in `runtime_state.py`, 1 in `api.py`, 3 in `explorer.py`, and 0 in
`lifecycle.py`, `session_presentation.py`, and `sessions/manager.py`. They reach
into `web.app`, `web.runtime_state`, `web.saved_sessions`, and `web.terminal_io`
— every one a deferred import to break a module cycle.

Most are load-bearing and correctly commented. One is not: `import os` at
`:480`, inside `_prepare_launch_sessions()`, where `os` is simply absent from the
module header (`:15-19` imports only `logging`, `re`, `time`, `uuid`, `typing`).

Recorded as an architecture signal rather than a defect. `workspaces.py` now sits
at the centre of a cycle with four peers, which is the shape `api.py` had before
its split. The two candidates that would break the most cycles are the
close-action matrix and the launch/restore path.

### 4.3 — `config.json` is a third durable store that bypasses `web/state_files.py` `LOW`

`web/state_files.py` was written to unify four mechanics — cross-process lock,
unique-temp + `os.replace`, `.bak` backup, quarantine — across
`runtime_state.json` and `saved_sessions.json`. Its own docstring says *"those
mechanics existed in only one of the two stores."*

`config.json` is the third such file and got none of them.
`web/config.py:135-151` hand-rolls the write:

```python
temp_path = os.path.join(target_dir, f".{os.path.basename(target_path)}.{uuid.uuid4().hex}.tmp")
with _config_lock:
    with open(temp_path, 'w', encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.replace(temp_path, target_path)
```

The **unique same-directory temp path is correct** (guardrail 2 satisfied), and
`_config_lock` orders threads. Missing: the cross-process lock, `os.fsync`, the
`.bak` copy, and quarantine — `load_config` on a `JSONDecodeError` logs a WARNING
and silently returns defaults (`:122-129`), which is the "laundering it into
empty state" case `state_files.py` calls out by name.

Lowest severity of the three architecture findings because `config.json` is
reconstructible from `default_config.json` and App Settings, and losing it costs
preferences rather than work. But it is a documented shared primitive with a
known third caller that does not use it.

---

## 5. Testing

### 5.1 — The narrowed source-text-assertion rule was reverted; the *practice* improved anyway `MEDIUM` (documentation)

`CLAUDE.md:191` and `AGENTS.md:71` currently read:

> Prefer behavioral tests. Do not add raw JS/HTML/CSS source-text assertions;
> when touching an existing one, convert it to a behavioral or contract-level
> check when practical.

That is the **original** rule, verbatim — the one the 2026-08-04 audit found to
be unfollowable because it forbade the only tool available. The 2026-08-05
remediation explicitly replaced it:

> **§5.1** — the rule is narrowed rather than dropped […] It now names what is
> **allowed** (rendered markup, class and `data-*`/`aria-*` hooks, CSS custom
> properties and selectors, the presence of a named constant) and what is
> **not** (exact signatures, whole statements, implementation literals).

The allowed/not-allowed split is gone from both files. The rule is back to the
state that produced the finding.

**And yet the practice went the other way, decisively.** A real JS test runner
did arrive — Node executed against DOM stubs — and now covers **19 test files
and 14+ first-party JS modules**:

| | |
|---|---|
| `explorer-edit-find` | `test_explorer_edit_find.py` (907) |
| `explorer-edit-highlight` / `-overlay` | `test_explorer_edit_highlight.py` (580) |
| `explorer-selection` | `test_explorer_selection.py` (582) |
| `explorer-fs` (batch) | `test_explorer_fs_batch.py` (691) |
| `explorer-overview` | `test_explorer_overview.py` (587) |
| `explorer-persistence` / `session-persistence` | `test_session_presentation.py` (954), `test_session_persistence_contract.py` (1,608) |
| `notice-banner` | `test_notice_banner.py` (511) |
| `topbar-peek` | `test_topbar_peek.py` (445) |
| `lifecycle` | `test_lifecycle.py` (1,264) |
| `explorer-theme-store` | `test_explorer_theme_store.py` (209) |
| `explorer-tree-search` | `test_explorer_find.py` (535) |
| `explorer-git-menu` | `test_explorer_git_menu.py` (209) |
| `voice-dictation` | `test_voice_dictation.py` (314) |

Measured drift in the forbidden class over 102 commits and +5,757 test lines:

| | `fdbf27f` | now | Δ |
|---|---|---|---|
| Signature-shaped `assertIn("function …")` in `test_api.py` | 268 | 269 | **+1** |
| …in `test_multi_workspace.py` | 18 | 18 | **0** |
| Raw source-text `assertIn(…, html/_js)` in `test_api.py` | 1,224 | 1,252 | +28 |

**+1 signature assertion across 599 new tests.** The problem §5.1 identified was
solved — by building the missing runner, which the remediation had explicitly
declined to do ("that is a real project, not an audit fix"). Someone did the real
project. The rule text simply never caught up, and 296 legacy signature
assertions remain as a standing conversion backlog.

**Fix.** Restore the narrowed rule and add the third clause the tree has earned:
*a DOM-free module gets a Node-executed behavioural test; source-text assertions
are for rendered markup and attribute hooks only.* Left as-is, the rule
contradicts the codebase's own best practice, and the next contributor has to
guess which one is real.

---

## 6. Styling (guardrail 7)

Adoption under +714 lines of `terminals.css` is very good. Of every colour value
**added** since the last audit, seven are hex literals and **all seven sit inside
deliberate token-declaration blocks** in `terminals.css:200-270` (`--git-lane-*`,
`--explorer-icon-*`, `--explorer-callout-*`) or in `tokens.css` itself — i.e.
they define tokens rather than scatter literals, and each block carries a comment
saying so. Both new stylesheets are literal-free: `lifecycle.css` (1 `var()`, 0
hex) and `notice-banner.css` (17 `var()`, 0 hex). `app-settings.css` and
`workspaces.css` remain at zero literals.

The in-place editor's metric-free overlay contract also holds:
`explorer-edit-overlay.js` renders through the same `renderExplorerSourceLines()`
the read-only view uses, and the find/active-match/occurrence tint all paint
through the CSS Custom Highlight API, which cannot set a metric.

### 6.1 — Four rendered text glyphs remain; the 2026-08-05 sweep was incomplete `LOW`

Guardrail 7: *"use stroke-style `currentColor` SVG icons, not emoji or text
glyphs."* The 2026-08-04 sweep fixed six sites and explicitly parked the Git
stage `+` ("kept visually distinct from … the Git stage `+`"). Four are still
the **visible content of a button**:

| Site | Glyph | Role |
|---|---|---|
| `explorer-viewer.js:1060` | `+` | Git row — Stage changes |
| `explorer-viewer.js:1716` | `+` | Git header — Stage all changes |
| `explorer-viewer.js:8225` | `-` | Editor zoom — decrease font size |
| `explorer-viewer.js:8226` | `+` | Editor zoom — increase font size |

All four predate the last audit (verified at `fdbf27f`), so this is **not new
drift** — it is an incomplete previous fix. The zoom pair is the more visible of
the two: `-` and `+` next to a numeric value read as a text control beside a
toolbar of SVG icons. `EXPLORER_GIT_REVERT_ICON` and its neighbours in
`terminal-icons.js` are the pattern to copy, and the six sites fixed in August
are the worked example.

---

## 7. Documentation drift

### 7.1 — `CLAUDE.md` and `AGENTS.md` cite `docs/codebase_audit.md`, which does not exist `MEDIUM`

**`CLAUDE.md:188`, `AGENTS.md:68`**, identical text:

> `docs/codebase_audit.md` is checked in and citable, but it is a **point-in-time
> record, not a live contract**: outside its per-finding **Resolution** blocks it
> describes the code as it stood at the audited commit, and **§3's guardrail
> table** is explicitly the baseline rather than the current state.

`docs/` contains `logging_guide.md`, `testing_issues.md`, `voice_guideline.md`,
two proposal documents, `images/`, and `r&d/`. There is no `codebase_audit.md`,
and no document in the repository has a `§3` guardrail table matching that
description. The nearest thing is `docs/r&d/codebase_audit_2026-08-04.md` —
gitignored, and whose guardrail table is at the end, not §3.

This is precisely the §7.1 failure mode that audit spent a full section fixing
and wrote a rule against — *"A contract that cites a path which does not exist is
worse than one that cites nothing, because the reader stops looking"* — recurring
in the same two files, one paragraph below the rule itself. The rule held
everywhere else: **zero** `docs/r&d/` citations survive in tracked source.

**Fix.** Repoint both paragraphs at **this document**
(`docs/codebase_audit_2026-08-14.md`), which *is* checked in, and correct the
`§3` reference to `§10` (the guardrail scorecard below). That makes the
paragraph true and gives the "cite it for *why* a fix was made" instruction a
real target.

### 7.2 — The repo-layout tree in `CLAUDE.md` is missing five files `LOW`

Present on disk, absent from the layout block:

| Missing | Lines | Added by |
|---|---|---|
| `web/static/js/explorer-tree-search.js` | 403 | `064bec7` (find a file by name) |
| `web/static/js/explorer-git-menu.js` | 86 | `7530163` (commit context menu) |
| `tests/test_explorer_find.py` | 535 | `064bec7` |
| `tests/test_explorer_tree_fold.py` | 347 | `602878c` |
| `tests/test_explorer_git_menu.py` | 209 | `7530163` |

Every other JS module, test file, `web/*.py` module and stylesheet is listed and
correct. Two of the five are exactly the kind of file guardrail 6 wants
contributors to *find* before starting a new one, so the omission works against
the rule that produced them.

### 7.3 — The read-only explorer contract never learned `/api/explorer/<id>/find` `LOW`

`CLAUDE.md:176` enumerates the explorer's reads by name — `download`, `image`,
`git/state`, `file/state`, `entries`, `search`, `reveal` — and its six mutation
families. `GET /api/explorer/<session_id>/find` (`web/api.py:1683`) is missing
from the list.

It is unambiguously a read, and its docstring is careful about it: *"Names only —
file contents are never opened here; that is the `/search` route above. Like
every other explorer read it is a GET, so it stays outside the cross-origin write
guard."* No contract is violated. But the paragraph is the canonical statement of
that contract, and an unlisted route is how the next reviewer concludes the
contract has grown a hole.

### 7.4 — Two proposal documents now live in `docs/`, outside the maintained set `LOW`

`docs/explorer_editor_highlighted_edit_proposal_2026-08-13.md` (647 lines,
tracked) and `docs/remote_wireshark_capture_proposal_2026-08-14.md` (untracked)
sit alongside the four maintained guides. `CLAUDE.md` names the citable set
exactly — `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`,
`docs/logging_guide.md`, `docs/voice_guideline.md`, `docs/testing_issues.md` —
and neither is on it, while `docs/r&d/` is where proposals are declared to live.

Nothing cites either one, so there is no live consequence. The risk is that
`docs/` becomes a second scratch directory that *is* tracked, which reintroduces
the "which copy is authoritative" problem the `docs/r&d/` rule exists to contain.
Either move them under `docs/r&d/` or add a one-line convention to `CLAUDE.md`
saying tracked, dated proposals in `docs/` are historical records and are not
contracts.

---

## 8. Performance (guardrail 3)

No violations. Re-verified across the whole tree: **no CDN or external asset
references** in any first-party HTML/JS/CSS; **exactly one `setInterval`** in
33,915 lines of frontend JS (`terminals.js:8211`, the 15 s socket-down
reconciliation fallback); the explorer git-watch backoff intact; and no
per-request SSH handshakes — `_acquire_ssh_sftp`/`_release_ssh_sftp`
(`explorer.py:2928-2986`) pool the transport with a correct `in_use` refcount, an
idle reaper that skips in-flight clients, and identity checks that make a
concurrently evicted-and-replaced entry close its own handle rather than
decrement someone else's.

Request rates from a 66-minute session: `POST /api/session-presentation` 152
(2.3/min), `entries` 66, `git/diff` 33, `app-config`/`voice-prefs` 81 each. All
event-driven, none polling.

Two observations carried forward from the last audit, both still open, both
still bounded reads:

### 8.1 — The occurrence tint still walks the whole source DOM when there are no matches `LOW`

`explorer-viewer.js:6178-6181` — the `TreeWalker` is bounded on the hit side
(`EXPLORER_OCCURRENCE_MAX_MATCHES = 500`) but its `while (… && walker.nextNode())`
condition only short-circuits once matches accumulate. A selection with no other
occurrences in a 2 MB highlighted file visits every text node. Debounced 90 ms,
non-collapsed selections only, pure read. Deferred on the same reasoning as
before: **still not observed**, and picking a node ceiling without a measurement
risks a visibly missing tint.

### 8.2 — Download still buffers up to 100 MB in memory `LOW`

`web/api.py:1357-1367` — `read_file_prefix(file_path, EXPLORER_DOWNLOAD_MAX_BYTES + 1)`
then `send_file`. Correctly root-confined and correctly capped; unchanged.
Streaming would remove the ceiling concern. See §1.2 for the half of this that
did get worse.

---

## 9. What went right

Recorded so it does not get re-litigated:

- **Concurrency is exemplary under a third more code.** A mechanical scan of
  every `with <lock>` block in `web/` and `sessions/` finds **zero**
  `emit`/`socketio.emit` calls inside any lock hold and **zero** reverse-order
  nestings. The only in-lock blocking work is by design and on dedicated locks
  (`_config_lock` around the config write; `_vosk_process_lock` around a bounded
  `Popen`+`wait(timeout=5)`), never on `SessionManager.lock` or
  `connection_lock`. The `snapshot-under-lock, emit-after` pattern is applied
  consistently — `close_session` (`api.py:2870-2913`), `close_all_sessions`
  (`:2938-2985`), `_emit_session_status` (`terminal_io.py:116-121`) all snapshot,
  release, then broadcast, each with a comment naming the rule.
- **`LifecycleCoordinator` is the best-reasoned new code in the repo.**
  `request_flush()` (`lifecycle.py:343-479`) decides its `expected`,
  pre-acknowledged and emit sets **in one hold**, emits outside every lock, waits
  on a `Condition` with a 5 s deadline, and has separate, separately documented
  paths for a window that leaves (forgotten — `_drop_pending_windows_locked`)
  versus one that disconnects (`client_stale`, pre-acknowledged so the flush
  resolves immediately instead of timing out —`_stale_pending_windows_locked`).
  Every bound is finite: 5 s flush, 120 s stale grace, 60 s decision TTL, 16
  windows per workspace, `_MAX_DECISIONS` cap.
- **`web/state_files.py` is a textbook extraction.** Four mechanics, one module,
  parameterised by the caller's exception type and label so a saved-session
  failure surfaces as a saved-session failure. `create_file_exclusively()` in
  particular gets the encryption-key race right on both platforms —
  `os.rename` on Windows, `os.link` elsewhere, both of which fail rather than
  clobber, with the content fsynced to a unique temp *before* the target name
  exists so a reader can never see a zero-byte key.
- **The frontend split rule worked under the heaviest load in project history.**
  14 new modules, 81 % of 8,239 new lines outside the monoliths (§4).
- **A JS test runner exists now.** 19 test files execute first-party modules in
  Node against DOM stubs, covering 14+ modules, and signature-shaped assertions
  grew by **one** across 599 new tests (§5.1).
- **`web/api.py` held its density** — 80 → 89 handlers at a constant ~40 lines
  each. The backend split is still doing its job.
- **The one-banner launcher rule held exactly.** `showGridVibeNotice` is the sole
  writer, `textContent` never `innerHTML`, `#message` is inert static copy, and
  `notice-banner.css` carries no `position` and no `z-index`.
- **Zero `window.prompt`/`confirm`/`alert`** anywhere in first-party JS or
  templates — the WebView2 guardrail is clean.
- **Zero `docs/r&d/` citations** survive in tracked source. The rule that came out
  of §7.1 worked everywhere except the two files that wrote it (§7.1 above).
- **`CHANGELOG.md` remains extraordinary** — every feature commit carries a
  detailed entry naming the rejected alternatives and the reasoning, and the
  `Unreleased` section reads as genuine release notes rather than a commit dump.
- **Zero `TODO`/`FIXME`/`HACK`/`XXX`** in first-party source.

---

## 10. Guardrail scorecard

| # | Guardrail | Status | Notes |
|---|---|---|---|
| 1 | Security | **PASS** | Derived same-origin CORS + `apply_resolved_server_origins`, cross-origin write guard, room-scoped emits, Fernet secrets, `set_missing_host_key_policy` only in `hostkeys.py` (3 sites). `.gitignore` extended to every new state sidecar. No new XSS surface — the edit overlay reuses the read-only escaping renderer. |
| 2 | Concurrency | **PASS** | Zero emits under any lock, zero reverse-order nestings, across +33 % code. Snapshot-then-emit applied consistently. Every atomic write uses a unique same-directory temp path. `LifecycleCoordinator` bounds every wait. |
| 3 | Performance | **PASS** | No CDN assets, one `setInterval` in 33.9k lines of JS, SSH transport pooled with a correct refcount, no sub-second polling. §8.1/§8.2 remain bounded observations. |
| 4 | Correctness | **DRIFT** | Zero `window.prompt/confirm/alert`; shell quoting correct; CLI flags beat config. But §1.1 — the self-update `git fetch` can hang a worker thread forever with no timeout and no `GIT_TERMINAL_PROMPT=0`. |
| 5 | Dead code | **DRIFT** | All 27 config keys read, all 24 `RuntimeConfig` attrs read, all 63 routes reachable, exact Socket.IO parity. Three definition-only symbols (§3.1–3.3); the previous audit found zero. |
| 6 | Architecture/DRY | **DRIFT** | Split rule working — 81 % of new frontend lines in 14 new modules; `api.py` density constant. But `explorer-viewer.js` +1,029 after its extraction trigger was deleted from `CLAUDE.md` (§4.1); `workspaces.py` at 25 cycle-breaking imports (§4.2); `config.json` bypasses `state_files.py` (§4.3). |
| 7 | Styling | **PASS (minor)** | Every one of the 7 new hex literals sits in a deliberate token block; both new stylesheets are literal-free; the editor overlay's metric-free contract holds. Four legacy `+`/`-` glyph buttons remain from an incomplete earlier sweep (§6.1). |
| 8 | Interaction | **DRIFT** | Shared confirm modal and one-banner launcher rule both intact; busy states toggle classes. But the browser-mode download reports success unconditionally, now ×N under multi-select (§1.2). |
| 9 | Logging | **DRIFT** | 0 ERROR / 0 WARNING, ANSI + poll filters wired, teardown at DEBUG. But 84 % of every log line is paramiko INFO, cutting first-party retention to ~300 lines per 2 MB rotation (§2.1). |
| 10 | New features | **PASS** | Every new surface went through the existing contracts — `RuntimeConfig`, the bounded presentation normalizer, room-scoped emits, vendored assets. No secret persisted into new state; manual snapshots never demoted. |

---

## 11. Staged correction plan

Five stages, ordered so each is independently shippable and the risk rises
monotonically. Stages 1–2 are a single sitting.

### Stage 1 — Stop the bleeding (≈ 20 lines, no behaviour change on any happy path) — **DONE 2026-08-14**

All three landed; see the **Resolution** blocks under §1.1, §2.1 and §3.1–3.3.
One correction to the plan below: 1.1 is not five lines. Bounding the wait
without killing git's *helper* processes leaves the bound unenforced on Windows
— measured at 268.8 s against a 30 s timeout — so the runner now owns its
`Popen` and kills the process group. `pull --ff-only` is a network command and
takes the 30 s bound, not the 10 s one.

| # | Action | File | Why first |
|---|---|---|---|
| 1.1 | Add `timeout=30` for `fetch`, `timeout=10` for the five local commands, and `env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}` to `_run_repo_git`; catch `subprocess.TimeoutExpired` into `AppUpdateError` | `web/selfupdate.py:31-39` | The only unbounded blocking call in the process; leaks a worker thread per click |
| 1.2 | `logging.getLogger("paramiko").setLevel(logging.WARNING)` in `setup_logging()` | `main.py:66-99` | Restores ~100× first-party log retention for one line; real SSH failures still surface at WARNING/ERROR |
| 1.3 | Delete `explorerSelectionFor()`, `getExplorerThemeStore()`, `get_runtime_state_store()` | `explorer-viewer.js:1136`, `terminals.js:438`, `runtime_state.py:1178` | Zero call sites each; three one-line deletions |

**Verify:** `make check`; confirm `git fetch` against an unreachable remote now
returns a `500` inside 30 s; confirm a fresh `logs/gridvibe.log` after an SFTP
explorer session contains no `paramiko.transport.sftp` lines.

**Doc:** add a third-party-logger-level section to `docs/logging_guide.md`
beside `_SuppressPollLogs` and `_StripAnsiFilter`; `CHANGELOG.md` entry for 1.1.

### Stage 2 — Make the instruction files true again (documentation only, zero code)

| # | Action | File |
|---|---|---|
| 2.1 | Repoint the `docs/codebase_audit.md` paragraph at `docs/codebase_audit_2026-08-14.md` and correct `§3` → `§10` | `CLAUDE.md:188`, `AGENTS.md:68` |
| 2.2 | Restore the narrowed source-text-assertion rule (allowed: rendered markup, class and `data-*`/`aria-*` hooks, CSS custom properties, the presence of a named constant; not allowed: exact signatures, whole statements, implementation literals) and add the clause the tree earned: *a DOM-free module gets a Node-executed behavioural test* | `CLAUDE.md:191`, `AGENTS.md:71` |
| 2.3 | Restore an actionable extraction trigger for `explorer-viewer.js` — see Stage 4 for the shape | `CLAUDE.md:206`, `AGENTS.md:86` |
| 2.4 | Add `explorer-tree-search.js`, `explorer-git-menu.js`, `test_explorer_find.py`, `test_explorer_tree_fold.py`, `test_explorer_git_menu.py` to the repo-layout tree | `CLAUDE.md` |
| 2.5 | Add `GET /api/explorer/<id>/find` to the read-only contract's list of reads, beside `search` | `CLAUDE.md:176`, `AGENTS.md` |
| 2.6 | Decide the `docs/` proposal convention: move both proposals to `docs/r&d/`, **or** add one line saying tracked dated proposals in `docs/` are historical records, never contracts | `CLAUDE.md`, `docs/` |

This stage exists as its own step because §4.1 and §5.1 are the direct
consequence of skipping it last time. **Every prose remediation from the
2026-08-04 audit that was not backed by a test or a lock has been reverted; the
ones that were, held.** Doing 2.1–2.3 without doing Stage 5 leaves the same
failure mode in place.

### Stage 3 — Close the two visible user-facing gaps

| # | Action | File | Notes |
|---|---|---|---|
| 3.1 | Replace the programmatic `<a download>` with `fetch` → `blob` → object-URL, routing non-2xx through the existing error toast; keep the anchor as the fallback above a size threshold so large downloads still stream | `explorer-viewer.js:6654-6660` | Fixes §1.2. Test: a `404` from a stale tree row must produce an error toast, not a green one; a 9-file batch of stale rows must produce **one** error summary, not nine successes |
| 3.2 | Replace the four `+`/`-` glyph buttons with `currentColor` SVGs following `EXPLORER_GIT_REVERT_ICON` | `explorer-viewer.js:1060, 1716, 8225, 8226` | Fixes §6.1. Each container needs flex centring and an explicit SVG size — a text glyph centres by `font-size`, an SVG does not (the same correction the six-glyph fix needed) |

**Verify:** the batch-download behavioural test in `tests/test_explorer_fs_batch.py`
gains the partial-failure case; `make check`.

### Stage 4 — Extract from `explorer-viewer.js` (the first real cut)

Take the **tab strip**, not the Diff domain, despite Diff being larger.

- **29 of the domain's 32 functions sit in one contiguous band, `6,754–7,830`**
  — the only domain in the file that is already blocked rather than smeared.
  It runs from `explorerNormalizeTabPath`/`ensureExplorerTabState` through the
  strip itself (`renderExplorerTabStrip`, `wireExplorerTabStripInteractions`,
  drag/reorder, `promoteExplorerPreviewTab`), the activate/close pair, and the
  whole tab-persistence cluster (`explorerPersistableTabView` …
  `restoreExplorerPersistedTabs`).
- Diff is 49 functions across 7,572 lines and touches tab state at both ends;
  moving it first means untangling that coupling in the same commit as the move,
  which is the opposite of low blast radius.
- Cutting the tab strip out first *reduces* the coupling the Diff extraction will
  later have to deal with.

**Two things to expect, so they are not surprises mid-move:**

1. **Three tab-domain stragglers live outside the band** and have to come with
   it: `ensureExplorerTabLineWrap` (`:4589`), `explorerCaptureActiveTabView`
   (`:6480`), `explorerMatchingTabView` (`:6519`).
2. **The band is not pure.** Roughly ten viewer-core and Markdown functions are
   interleaved into it and must *stay* — the breadcrumb renderer (`:6999`), the
   Markdown link cluster (`explorerClassifyLink` → `wireExplorerMarkdownLinks`,
   `:7477–7568`), `explorerEnsureViewerShell` (`:6971`) and the session Markdown
   appearance pair (`:7424`, `:7449`). Three further functions match on a `tab`
   *parameter* rather than the domain (`renderExplorerImage`,
   `renderExplorerFile`, `openExplorerFile`) and are file-render, not tab code.

Deliverable: `web/static/js/explorer-tabs.js` (~900–1,000 lines),
`explorer-viewer.js` down to roughly **7,800** — below where it stood at the
previous audit. Then, in Stage 2's restored guardrail text, write the **next**
trigger with a condition attached rather than an adjective: *"the next change to
the Diff view extracts `explorer-diff.js`."* A rule with a trigger is what was
deleted; a rule without one is what let 1,029 lines land.

**Verify:** `make check`; no new globals (both files must run under the same IIFE
scope contract as the existing split, and the tab functions currently close over
`terminals`/`sessionIds`, so the extraction needs the same accessor treatment
`explorer-selection.js` uses); `test_explorer_source_frame.py` and
`test_explorer_overview.py` must pass **untouched** — a pure move changes no
behaviour, and needing to edit a behavioural test is the signal that it was not
a pure move.

### Stage 5 — Structural follow-ups (schedule, do not rush)

| # | Action | Effort | Value |
|---|---|---|---|
| 5.1 | Route `save_config()` through `web/state_files.py::write_json_atomically`, with quarantine + `.bak` recovery in `load_config()` | S | Closes §4.3; makes the shared primitive cover all three durable stores, as its docstring claims |
| 5.2 | Hoist `import os` to the `web/workspaces.py` module header; audit the other 24 function-level imports and record which are genuinely cycle-breaking | S | Closes the trivial half of §4.2 and produces the map needed for the next split |
| 5.3 | Extract the close-action matrix (or the launch/restore path) out of `web/workspaces.py` | M | Breaks the four-way cycle before `workspaces.py` becomes what `api.py` was |
| 5.4 | Convert the 296 legacy signature-shaped assertions to behavioural or Node-executed checks, module by module, as each module is next touched | L (ongoing) | Turns the restored Stage-2 rule into a shrinking backlog instead of a standing contradiction |
| 5.5 | Stream `send_file` for explorer downloads instead of buffering 100 MB | M | Closes §8.2; naturally follows 3.1, which already reshapes that path |
| 5.6 | Give `explorerOccurrenceRanges()` a node-count ceiling — **only if the cost is ever observed** | S | §8.1, unchanged reasoning: a number with no measurement behind it risks a visibly missing tint |

### Not recommended

- **Splitting `web/api.py`.** It grew 411 lines for 9 new endpoints at constant
  density and is still a thin router. The 2026-08-04 audit was right that the
  backend split worked; nothing has changed that.
- **A big-bang `explorer-viewer.js` split.** Stage 4 takes the one cut that is a
  move rather than a refactor. The remaining nine domains need the coupling
  reduced before they can follow, and doing them together would be a 7,000-line
  diff nobody can review.
- **Adding a node ceiling to the occurrence tint now** (§8.1) — third audit in a
  row, same reasoning, still not observed.
- **Resolving the `docs/r&d/` duplication** (prior §7.2) — deferred by decision
  twice, and §7.1's consequence is now fixed at the citation end. Recorded only
  because §7.4 is the same problem starting again one directory up.
