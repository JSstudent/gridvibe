# Session and workspace persistence — post-implementation verification

Date: 2026-08-09

Scope: verify that `docs/session_group_persistence_restore_audit_2026-08-07.md`
(Stages 0–7, SGP-01…SGP-14) and
`docs/workspace_implementation_audit_2026-08-06.md` (Phases 0–5, MW-01…MW-16)
were shipped correctly — that the closed findings really are closed in the
current tree, that no `## Regression Guardrails` rule was breached on the way,
and that no new race, dead code, or contract drift was introduced.

Method: the shipped source was read end to end along the save/restore path, the
gates were re-run, and every suspected defect was **confirmed by running it**
rather than by reading alone. No production file was changed by this review; the
working tree is clean.

## Verdict

**The restore path itself is solid.** Every claim the two audits close is true in
the current tree, and the properties that matter for "will my workspace come
back the way I left it" all hold: the snapshot is the only shape source, commits
are ordered per workspace across processes, group installation is atomic, reads
are validated through one gate so the chooser's counts equal what a restore
starts, stored shape is decoupled from `max_sessions`, and no password reaches
`runtime_state.json` or a log.

**Six issues were found, none of them in restore correctness.** One is a
high-severity *availability* defect on the save-on-exit path — the same failure
shape as SGP-12, from a different cause, and reachable without a crash. One is
dead code that also hollows out two frozen contract tests. The rest are a
low-impact ordering defect, a diagnostic/latency nit, and two documentation
gaps.

### Gates re-run on this machine

| Gate | Result |
|---|---|
| `.venv\Scripts\python.exe -m ruff check .` | passed, "All checks passed!" |
| `.venv\Scripts\python.exe tests/run_tests.py` | 1,340 tests, `OK (skipped=7)` — no expected failures remain |
| Persistence suites ×3 (contract, multi-workspace, presentation, lifecycle, session-manager, theme-store, saved-session-store) | 386 tests, `OK` on every run; 8.86 s / 8.81 s / 8.71 s |

Non-flaky, and stable for a structural reason worth preserving: the lifecycle
tests drive `LifecycleCoordinator` directly and acknowledge the flush
synchronously inside the `emit` callback, so no success case depends on a thread
winning a race. The only wall-clock timeouts are in the two cases that are
*meant* to time out.

### What was verified as correct

These were checked against the source, not taken from the audit text:

- **No slow work under a shared lock.** A scan of every `with … lock:` block in
  `web/terminal_io.py`, `web/api.py`, `sessions/manager.py`, `web/workspaces.py`,
  `web/lifecycle.py`, and `web/runtime_state.py` found no `socketio.emit`,
  broadcast, SSH teardown, or state-file write inside a `SessionManager.lock` or
  `connection_lock` hold. The only file I/O under a lock is inside
  `RuntimeStateStore`'s *own* process + cross-process file locks, which is the
  design. Captures read the live manager and return before any file lock is
  taken, preserving the documented lock order.
- **No orphan server events.** The seven events emitted from `web/` —
  `app_config_updated`, `lifecycle_flush_requested`, `session_status`,
  `session_groups_updated`, `terminal_output`, `voice_availability_updated`,
  `voice_prefs_updated` — each have exactly one client listener.
- **Capacity is decoupled from the launch preference.** `MAX_STORED_SESSION_PANES
  = 64` (`web/session_presentation.py:69`) bounds stored pane lists, geometry
  origin slots, and grid lines; `runtime_config.max_sessions` appears only at the
  launch/split boundary (`web/workspaces.py:622`, `web/api.py:2479`) and in form
  defaults. `_normalize_terminal_entries()` takes `max(1, preference,
  len(entries))`, so a wider stored preset survives a lowered setting.
- **No native dialogs** (`window.confirm`/`alert`/`prompt`) anywhere in the
  frontend; every mention is a comment explaining why they are banned.
- **No `expectedFailure` left** in `tests/`, and no `TODO`/`FIXME`/`HACK`
  markers in any module the two audits touched.
- **`async_mode="threading"`** (`web/app.py:71`), so the bounded flush wait in a
  request thread is genuinely concurrent with the ack arriving on the socket
  thread — the handshake is not relying on luck.

## Findings

Severity describes user impact, not exploitability. `PRV` = post-implementation
review.

### PRV-01 — High: two windows on one workspace block every save-on-exit and every per-workspace Save

**Evidence.** `normalize_workspace_metadata()` (`web/lifecycle.py:481`) coalesces
the per-window metadata returned by the flush handshake, and refuses to coalesce
a disagreement:

```python
nonempty = [candidate for candidate in candidates if candidate]
if nonempty and any(candidate != nonempty[0] for candidate in nonempty[1:]):
    raise LifecycleValidationError(
        f"Workspace {workspace_id} windows reported conflicting presentation metadata"
    )                                                    # web/lifecycle.py:523-527
```

Both consumers turn that into a retryable `503` that saves nothing:
`POST /api/lifecycle/prepare` (`web/api.py:801-810`) and
`prepare_workspace_save()` (`web/lifecycle.py:736-747`).

Confirmed by running it — two windows on `default` reporting different front
tabs:

```
PROBE two-windows-one-workspace
  RAISED: LifecycleValidationError - Workspace default windows reported
          conflicting presentation metadata
```

**There are three distinct triggers, all ordinary.** The metadata each window
reports is `{active_group_id, native_zoom_factor, topbar_visible}`
(`terminals.js:7860-7864`):

1. two windows showing **different session tabs** — the common case, since
   `activeGroupId` is per window;
2. two windows with **different top-bar states**, because a toggle in one window
   is not pushed to the other;
3. a **single** window reporting an `active_group_id` whose group was closed
   from another window moments earlier — `normalize_workspace_metadata` raises
   "reported an unknown active group" (`web/lifecycle.py:504-507`) before the
   window has processed the `session_groups_updated` broadcast.

**Reachability.** Native mode opens one window per workspace and reuses it, and
the launcher's `window.open(url, workspaceWindowName(id))`
(`workspaces.js:442`) targets a *named* window, so neither surface can create
the second window. A user opening the workspace URL in a second browser tab can
— which is unremarkable behaviour for a browser-first tool, and needs no crash,
no kill, and no network fault.

**Impact.** For as long as both tabs are open, **Save open workspaces**, **Save
open sessions + workspaces**, and the launcher's per-row **Save** all fail with
`503`. Only *Continue without saving* still works, so the escape hatch costs the
unsaved changes — precisely the trap SGP-12 was raised to remove. The message
("Workspace `a1b2c3…` windows reported conflicting presentation metadata") names
an opaque id and no action the user can take.

**Why this is the wrong failure mode, in the audit's own terms.** Two rules
already in the design point the other way, and this code contradicts both:

- **Product decision 7** anticipates exactly this situation — "multiple browser
  windows controlling one live workspace" — and resolves it as *one accepted
  revision is authoritative, reject stale updates with compare-and-swap*. Group
  and workspace *presentation* writes from those same two windows are reconciled
  by CAS without complaint. Only the chrome **metadata** is refused.
- **Stage 6 item 5** states the boundary as **launchable shape fails; window
  chrome degrades** — an invalid stored `topbar_visible` deliberately falls back
  to its default rather than costing a workspace restore. Active-group hint,
  top-bar visibility, and native zoom are that same window chrome, and here a
  disagreement about them costs the entire save.

Note the flush itself is not in doubt: every window acknowledged, so nothing is
being guessed. Only the answer to "which window's chrome should the slot record"
is ambiguous, and that question has cheap, deterministic answers.

**Resolved — Stage A, 2026-08-09.** See "Stage A — implemented" below for the
product line, the code, and the tests.

### PRV-02 — Medium: two server-side explorer resolvers are dead, and two frozen contract tests assert against them

**Evidence.** `resolve_tab_view()` (`web/session_presentation.py:559`) and
`diff_content_revision()` (`web/session_presentation.py:617`) have no production
caller. `git grep` across every `*.py` in the repository finds them referenced
only from `tests/test_session_persistence_contract.py:919` and `:937`.

The behaviour they describe is implemented independently in
`web/static/js/explorer-persistence.js` (`resolveRecord`, `diffContentRevision`),
and *that* is what the shipped explorer calls —
`explorer-viewer.js:5850`, `:5915`, `:5941`, `:6980`, `:7060`.

**Three consequences.**

1. **Guardrail 5 (dead code).** Roughly 75 lines of resolver and identity logic
   ship with nothing reading them.
2. **Guardrail 6 (DRY), in the more dangerous direction.** These are not two
   copies of one algorithm — they are two *different* algorithms answering one
   contract. Python's `diff_content_revision` selects
   `index_revision`/`worktree_revision`/`commit_revision` out of a metadata dict;
   the JavaScript `diffContentRevision` hashes `path + diffCommit + diffMode +
   renderedDiff`. Only the JavaScript one governs what a user sees. A future
   change made to the Python side would be invisible in the product while looking
   like a fix.
3. **Two Stage 5 forcing functions are hollow.**
   `test_changed_content_drops_scroll_and_folds_but_keeps_view_intent` and
   `test_a_staged_diff_identity_tracks_the_index_not_the_working_file` — the
   frozen tests for SGP-03's two headline behaviours — pass without executing a
   line of shipped code. The behaviours themselves *are* covered, in
   `tests/test_session_presentation.py:93-118`, which runs the real JavaScript
   under Node. So this is a weakened contract, not an untested behaviour — but a
   Stage 0 test that cannot fail when the product breaks is worse than no test,
   because it reads as protection.

### PRV-03 — Low: a freshly installed explorer pane keeps the launch config's Markdown appearance instead of the workspace's

**Evidence.** `install_session_group()` calls
`_mirror_workspace_appearance_locked(workspace)` at `sessions/manager.py:918`,
but assigns the new group's `pane_order` only at `:920`. `_create_group_locked`
publishes a fresh `SessionGroup` whose `pane_order` starts empty
(`sessions/manager.py:324-335`), and the mirror iterates `group.pane_order`
(`:621`). The mirror therefore updates every *other* group in the workspace and
skips the very panes it was called for.

Confirmed by running it — workspace authority `paper / serif / jetbrains-mono`,
one explorer pane launched carrying `contrast / system / default`:

```
PROBE appearance-mirror-on-install
  workspace : paper serif jetbrains-mono
  new pane  : contrast system default
  agree     : False
  pane1 after a later install: paper
```

The divergence closes on the next install or appearance write, so it is
transient, but a capture taken in that window writes per-pane aliases that
contradict the workspace-level value inside the same slot.

**Impact is genuinely small, and worth stating so this is not over-fixed.** The
client ignores the per-pane alias whenever the workspace value is known —
`applyExplorerSessionMarkdownAppearance()` returns early if
`workspaceExplorerAppearance` is set (`explorer-viewer.js:6770-6786`) — and
`_validate_slot()` prefers the slot's workspace-level appearance on restore
(`web/runtime_state.py:421-430`). So nothing the user sees is wrong today. What
is wrong is the invariant: Stage 5 documents the per-pane fields as read aliases
kept in step with the workspace authority, and for freshly installed panes they
are not. The latent risk is `workspace_appearance_from_panes()`, the legacy
migration fallback, which would seed a workspace from those contradicting values
if the workspace-level fields were ever absent.

### PRV-04 — Low: a window lost between the flush snapshot and the emit costs a full 5-second timeout and is mislabelled

**Evidence.** `request_flush()` builds `expected`, the pre-acknowledged set, and
the `client_stale` errors inside the coordinator lock
(`web/lifecycle.py:287-326`), then re-reads `self._windows` *outside* that lock
to decide which rooms to emit to:

```python
connected_workspaces = {
    workspace
    for window, workspace in expected
    if self._windows.get(window, {}).get("connected")
}                                                     # web/lifecycle.py:330-334
```

A window connected when `expected` was snapshotted but disconnected by the time
of this read is never emitted to, was not pre-acknowledged, and can never
acknowledge — so the flush waits the full
`LIFECYCLE_FLUSH_TIMEOUT_SECONDS = 5.0` (`web/lifecycle.py:47`) and then reports
`client_timeout` instead of the accurate `client_stale`.

**Impact.** The outcome is still an honest retryable `503`, so this is not a
correctness defect: it is a five-second stall on a close the user is waiting for,
plus a failure category that points a later reader at the wrong cause. The read
itself is safe (a CPython dict `get` is atomic), but it is shared coordinator
state read outside the lock that guards it, which is the kind of thing that stops
being harmless the moment the record shape grows.

### PRV-05 — Low: two shipped user-visible behaviours are missing from `README.md`

Stage 4.5 shipped two things a user meets directly, and `README.md` documents
neither:

- **Workspace-name uniqueness (SGP-13).** Creating, renaming, or launching into a
  workspace with a taken name is refused with a `409` and an inline choice. A
  user who hits that refusal has no documentation of the rule — including the
  parts that are not guessable, such as case- and whitespace-insensitive
  matching, the namespace covering *saved* snapshots as well as live workspaces,
  and empty labels being exempt.
- **The launcher's per-row Save (SGP-14).** README's "Save & restore" row still
  describes only autosave and in-window **Save Workspace**.

Both are correctly recorded in `CHANGELOG.md` (Unreleased), and `CLAUDE.md`
carries the label-namespace contract for agents. The gap is specifically in the
document that tells a *user* what the application does, which CLAUDE.md's working
rules require to be updated for user-visible behaviour.

### PRV-06 — Informational: `CHANGELOG.md`'s 1.8.0 entry states a writer-count contract that is no longer true

`CHANGELOG.md:29` closes with "**the autosave timer and Save Workspace are the
only two writers of workspace shape.**" The current contract is three capture
intents — autosave, explicit Save Workspace, and a successful voluntary
close/restart lifecycle save — as stated in `web/runtime_state.py:5-12` and
`CLAUDE.md:36`.

The sentence sits inside the dated 1.8.0 section and was true when written, and
the Unreleased section describes the lifecycle transaction correctly, so this is
flagged rather than filed as a defect: rewriting release history is worse
practice than leaving it. It is listed because `CHANGELOG.md` is a citable
document and the phrasing reads as a standing promise.

## Staged fix plan

Stages are independent and in priority order. Stage A is the only one that
changes what a user can do; B and C are hygiene with a real long-term cost; D is
documentation.

### Stage A — Make window chrome degrade instead of failing the save (PRV-01)

The one stage that restores a guarantee the product currently loses.

1. Replace the disagreement refusal in `normalize_workspace_metadata()` with a
   deterministic resolution. Preferred rule: take the candidate from the most
   recently joined connected window — `LifecycleCoordinator` already records
   `joined_at` (`web/lifecycle.py:126`), so the ordering exists and needs only to
   be carried into the metadata alongside each candidate. Fall back to the
   server's own `workspace.active_group_id` / `topbar_visible` when no candidate
   survives. This is product decision 7's last-writer-wins applied to chrome, and
   Stage 6 item 5's *chrome degrades* boundary applied where it belongs.
2. Do the same for a stale `active_group_id`: an id naming no live group is a
   window that has not yet seen a close, not a corrupt payload. Drop that one
   field and let the capture fall back to the server hint —
   `RuntimeStateStore._build_slot()` already re-validates the hint against the
   captured groups (`web/runtime_state.py:773-775`), so a wrong value cannot
   reach the slot anyway.
3. Keep raising for genuinely malformed *types* — a non-boolean `topbar_visible`,
   an out-of-range native zoom. Those indicate a broken client rather than a
   legitimate disagreement, and the existing tests pin them.
4. Log the disagreement at DEBUG, shape-only: workspace id, how many windows
   disagreed, and which fields. No group names, no paths.
5. Decide and record one product line in this document: **a workspace's chrome is
   whichever window most recently joined.** Without that sentence the next reader
   has to re-derive why a deterministic winner is acceptable.

Tests: two windows on one workspace reporting different front tabs save
successfully and the slot records the newest window's value; the same for
disagreeing top-bar values; a window reporting a group closed elsewhere no longer
fails the save and the slot falls back to a valid hint; a non-boolean
`topbar_visible` still fails; the per-row launcher Save gets the same four cases.

Exit gate: no combination of *reachable* live windows can make **Save open
workspaces** or a per-row **Save** fail while every window has flushed
successfully.

#### Stage A — implemented (2026-08-09)

**Product line, recorded as required by step 5: a workspace's chrome is
whichever window most recently joined.** Per field, not per window: the newest
window that *supplied* a field owns it, so a window that says nothing about the
top bar does not erase a sibling's opinion. Every field is window chrome —
front tab, top-bar visibility, native zoom — and none of it is launchable shape,
so a disagreement between two honest windows must cost at most a stale-looking
tab, never the save.

Code changed (both files under `web/`; no client change was needed, since the
metadata each window reports is already correct — only the server's reading of
it was wrong):

1. **Join order is carried through the handshake.** `LifecycleCoordinator`
   already recorded `joined_at` per window; `request_flush()` now snapshots it
   into the pending flush (`"joined"`) alongside `"clients"`, inside the same
   lock hold that builds `expected`. `acknowledge_flush()` tags each accepted
   metadata record with its window's `joined_at`, and `request_flush()` returns
   each workspace's records **ordered oldest-joined first**. Acknowledgement
   *arrival* order is therefore irrelevant — the newest window wins even when it
   answers first, which is the common case since it is usually the foreground
   tab.
2. **`normalize_workspace_metadata()` resolves instead of refusing.** The
   disagreement refusal is gone; fields are applied last-writer-wins over the
   ordered records. A workspace whose windows all agree behaves exactly as
   before.
3. **A stale `active_group_id` drops one field, not the save.** An id naming no
   live group is a window that has not yet processed a sibling's close, so that
   single field is skipped and the capture falls back to the server's own hint
   (`capture_live_workspaces`/`capture_workspace` already prefer the snapshot
   hint when the key is absent, and `_build_slot` re-validates it against the
   captured groups). If a stale hint was the *only* thing a workspace's windows
   reported, the workspace is simply omitted from the normalized map.
4. **Malformed types still raise**, unchanged: a non-boolean `topbar_visible`
   and an out-of-range `native_zoom_factor` remain `LifecycleValidationError`,
   still surfacing as a retryable `503` with category `client_metadata` from
   both consumers. A broken client is not a disagreement.
5. **The disagreement is logged at DEBUG, shape-only**: workspace id, how many
   windows answered, which field names disagreed, and how many stale tab hints
   were dropped. No group names, no labels, no paths.

Tests added (`tests/test_lifecycle.py`, 11 new, all behavioral):

- `LifecycleCoordinatorTestCase.test_flush_metadata_is_ordered_oldest_window_first`
  — the newest window acknowledges first and the returned list is still oldest
  first, pinning join order against arrival order.
- New `LifecycleWindowChromeTestCase` covering the resolver directly:
  disagreeing windows resolve to the most recently joined; each field is owned
  by the newest window that reported it; a group closed from another window
  drops only that field; a stale hint alone leaves the hint to the server;
  malformed types still fail.
- `LifecycleRouteTestCase`, through `POST /api/lifecycle/prepare` with two
  windows joined on `default` and one room emit reaching both: disagreeing front
  tabs *and* top-bar states now save, and the slot on disk records the newest
  window's values; a window pointing at a group closed elsewhere saves with the
  server's hint in the slot; a non-boolean `topbar_visible` still returns `503`
  with `client_metadata` and writes nothing.
- The same three cases through the launcher's per-row `POST
  /api/workspaces/<id>/save`, asserting the returned `active_group_id` /
  `topbar_visible` and that a malformed payload leaves `runtime_state.json`
  absent.

Exit gate met. Gates re-run on this machine: `tests/run_tests.py` → 1,351 tests,
`OK (skipped=7)`; `ruff check .` → "All checks passed!". Documentation of the
rule in `README.md` / `CLAUDE.md` / `AGENTS.md` is deliberately left to Stage D,
per that stage's step 4.

### Stage B — Delete the dead resolvers and re-point their contract tests (PRV-02)

Ordered after Stage A because it changes no behaviour, but it should not wait
long: every day the duplicate exists is a day someone can "fix" the wrong copy.

1. Remove `resolve_tab_view()` and `diff_content_revision()` from
   `web/session_presentation.py`, together with any constant left with no other
   reader once they are gone.
2. Re-point `test_changed_content_drops_scroll_and_folds_but_keeps_view_intent`
   and `test_a_staged_diff_identity_tracks_the_index_not_the_working_file` at the
   shipped implementation — run `explorer-persistence.js` under Node, the way
   `tests/test_session_presentation.py:93-118` already does, so the two SGP-03
   forcing functions fail when the product breaks. Do not delete them: they are
   Stage 0 contract tests, and the contract is still the right one. If the Node
   binary is absent they should skip, and the assertions in
   `test_session_presentation.py` remain the always-running floor.
3. Confirm before deleting, the same enumerate-then-remove discipline Stage 3
   used for `update_browser_tab_strip()`: `git grep` each name across `*.py`,
   `*.js`, and `templates/` and record the result in the commit message.

Exit gate: no server-side copy of the explorer resolution or Diff-identity rules
remains, and the two contract tests execute shipped code.

### Stage C — Ordering and diagnostic cleanups (PRV-03, PRV-04)

1. **PRV-03.** In `install_session_group()`, assign `group.pane_order` before
   calling `_mirror_workspace_appearance_locked(workspace)` — both are already
   inside the same lock hold, so this is a statement reorder, not a new
   transaction. Test: a pane launched into a workspace with initialized
   appearance carries the workspace's values immediately, and the legacy seeding
   path (an uninitialized workspace adopting the first valid pane's appearance)
   still works.
2. **PRV-04.** Compute the emit set inside the same `with self._condition` block
   that builds `pending`, and return it alongside — the emit itself must stay
   outside the lock, which is the rule that made the split tempting in the first
   place. A window that drops after that snapshot is then already recorded as
   `client_stale` and pre-acknowledged, so the flush answers immediately with the
   right category instead of stalling for five seconds. Test: a window that
   disconnects between the snapshot and the emit reports `client_stale` and the
   flush returns well inside the timeout.

Exit gate: the appearance mirror covers the panes it was called for, and no
disconnect can turn a flush into a full-timeout wait.

### Stage D — Documentation truth (PRV-05, PRV-06)

1. Add workspace-name uniqueness to `README.md`'s Multiple-workspaces material:
   one non-empty name identifies at most one workspace across live *and* saved
   state, compared case- and whitespace-insensitively; a collision is refused
   with an inline choice; nothing is auto-renamed or auto-forgotten; unnamed
   workspaces are unconstrained.
2. Add the launcher's per-row **Save** to README's "Save & restore" row: it is
   in-window **Save Workspace** invoked from the launcher, it captures one
   workspace, it never writes reusable presets, it never closes anything, and it
   reports rather than guesses when no window is reachable.
3. Add one forward-reference line to `CHANGELOG.md`'s Unreleased section stating
   that voluntary close/restart is now a third capture intent, so a reader who
   finds the 1.8.0 "only two writers" sentence is corrected in the same file. Do
   not edit the 1.8.0 entry.
4. Re-check `CLAUDE.md` and `AGENTS.md` against whatever Stage A decides, since
   both carry the lifecycle contract prose.

Exit gate: `README.md`, `CHANGELOG.md`, `CLAUDE.md`, and `AGENTS.md` describe the
shipped behaviour, including Stage A's resolution rule.

## Guardrail audit

Each rule checked against the shipped persistence/lifecycle surface, not against
the audit text.

| Guardrail | Verdict | Evidence |
|---|---|---|
| 1. Security | pass | The cross-origin write guard is a blanket `before_request` on every non-GET, so the presentation, lifecycle, and per-workspace-save routes are covered without registration. Emits are room-scoped through `workspace_room(...)`. `snapshot_lifecycle_workspaces()` remains the only credential-bearing snapshot and feeds only the preset writer; `_SESSION_SNAPSHOT_FIELDS` omits `password`, and `_validate_session()` re-applies that allowlist on *read*, so a hand-added password cannot re-enter a launch body. Host-key policy untouched. |
| 2. Concurrency | pass | No emit, broadcast, SSH teardown, or state-file write inside `SessionManager.lock` or `connection_lock` (full scan). Captures read the manager and return before taking file locks, preserving the documented order. Every state commit uses a unique `uuid4` same-directory temp path, fsync, backup, then `os.replace`. **One style exception, PRV-04:** `request_flush()` reads `self._windows` outside the coordinator lock — safe today, and Stage C folds it back in. |
| 3. Performance | pass | No new polling. `CONTINUOUS_UPDATE_FLOOR_MS = 1000` floors scroll/zoom coalescing and scroll is folded into the batch at capture time rather than being an event; structural changes batch on a microtask. `setTimeout` appears exactly once in the new frontend modules, as that floor. No CDN assets — every new module is vendored local. |
| 4. Correctness | pass | No `window.confirm`/`alert`/`prompt` anywhere. Shell quoting untouched. The lifecycle dialog is the shared in-page partial and uses the shared `visible` class. |
| 5. Dead code | **fail — PRV-02** | Every server event has exactly one client listener, no config key is unread, and both superseded writers (`update_browser_tab_strip()`, `PATCH /api/workspaces/<id>/ui-state`) are gone. But `resolve_tab_view()` and `diff_content_revision()` ship with no production consumer. |
| 6. Architecture/DRY | **fail — PRV-02** | New backend code went to focused modules and the routes stayed thin; new frontend logic went to new domain files rather than back into `terminals.js`. The failure is the second, divergent implementation of the explorer resolution and Diff-identity rules. **Watch item:** `terminals.js` is 8,059 lines and `explorer-viewer.js` is 8,094 — both back at the size that triggered the original split, and `explorer-viewer.js` has overtaken it. The next substantial addition to either should force a domain extraction, as the Stages 0–4 review already warned. |
| 7. Styling | pass | `lifecycle.css` carries no palette literal; every colour comes from `tokens.css`. |
| 8. Interaction | pass | The lifecycle modal is itself the confirm for close/restart, and a failed save leaves the same three choices active so retry is the same button. Busy state is a class plus `aria-busy`, never rewritten markup. Failures keep the "— try again" wording. |
| 9. Logging | pass | Lifecycle diagnostics are shape-only: ids, revisions, counts, and failure *categories* taken from exception class names rather than exception text, which is what keeps state-file paths out of the log. Window-registry transitions are DEBUG, one-off save outcomes INFO, anomalies WARNING. No ANSI, no per-keystroke path. |
| 10. New features | pass | No new state file; both new writers go through the existing durable machinery. `runtime_state.json` stays password-free. Manual-retention markers survive autosave (`manually_saved_at` carried through `_build_slot`), and native zoom falls back to the stored value only when the owning window supplied none. |

## Re-verification of the closed findings

Spot-checked against the current tree rather than against each finding's own
resolution block. All hold:

**Workspace audit.** MW-01 (`GRIDVIBE_TEST_MODE` guard raises on the production
path), MW-02 (durable `revisions` map plus `_is_stale_locked`), MW-03
(`_restore_group_request` builds from the snapshot; `_preset_ssh_credential` is
the only preset contribution and is target-matched), MW-04 (the rename route
touches the label only; `manually_saved_at` carries the pin while `origin` names
the writer), MW-05 (`workspace_is_user_visible` / `isUserVisibleWorkspace`),
MW-06 (`EMPTY_GROUP_GRACE_SECONDS` and `force_group_ids` absent;
`clear_disconnected_sessions()` takes no arguments), MW-07
(`install_session_group` is the only publication path, staging outside the lock
and publishing inside one hold), MW-08 (`claim_workspace_restore` released in
`finally`), MW-09 (`buildRestoreGroupBody` gone), MW-10 (typed persistence error
→ `503 retryable`), MW-11 (`_encode_group_id_component`), MW-12/MW-15
(`DELETE /api/workspaces/<id>` with `?forget=true`), MW-13
(`_preflight_restore_set`), MW-14 (`_evict_excess_auto_slots` takes the live set
as a protected set), MW-16 (`_validate_slot` is the one gate both read paths
share).

**Persistence audit.** SGP-01/02 (one ordered CAS transaction per group plus one
for chrome; both legacy writers deleted), SGP-03 (versioned v2 records with
intent separated from revision-bound state — shipped in JavaScript; see PRV-02
for the dead server twin), SGP-04 (`_prepare_launch_sessions` preserves a
supplied `explorer_root_directory` for both SSH and WSL explorer panes and still
falls back to `directory`), SGP-05 (`SavedSessionStore` transactions and
exclusive atomic Fernet-key creation via `create_file_exclusively`), SGP-06
(schema ceiling everywhere; `capacity_refusal()` names the number to raise),
SGP-07 (`normalize_pane_presentation_fields` type-checks and rejects at both the
read and the launch boundary; `_session_launch_fields` holds defaults and
overlays rather than coercing), SGP-08 (workspace-scoped appearance on the
existing ordered transaction — see PRV-03 for the mirror ordering), SGP-09
(`explorer-theme-store.js` re-bounds on every write and grid build), SGP-10 (no
source-text assertions remain), SGP-11 (one lifecycle transaction behind all four
voluntary exit surfaces), SGP-12 (`sessionStorage` window id, 120-second grace,
per-workspace bound), SGP-13 (`workspace_label_conflict` is the one namespace
owner, and `resolve_launch_destination` re-checks at commit time), SGP-14
(`prepare_workspace_save` — flush-then-capture or refuse, one slot, presets
untouched).

## Appendix: how each finding was confirmed

PRV-01 and PRV-03 were reproduced by driving the shipped code in a throwaway
process (a temporary `runtime_state.json`, an in-memory `SessionManager`) and
printing what it did; the transcripts are quoted inline above. PRV-02 was
established by a `git grep` sweep of every public name exported by the four new
Python modules and four new JavaScript modules, filtered to those with no
non-test consumer, then confirmed by reading each survivor. PRV-04 was
established by reading `request_flush` and confirming the pre-acknowledgement
path with a probe (a fresh disconnect correctly reports `client_stale` and emits
to nobody). PRV-05 and PRV-06 were established by reading `README.md` and
`CHANGELOG.md` against the shipped routes.
