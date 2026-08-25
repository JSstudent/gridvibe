# Session & Workspace State Guideline

The canonical reference for how GridVibe **saves and restores** sessions and
workspaces. Read it before touching anything that writes a durable file,
captures a workspace, or replays one.

Use this file when you need to:

- add or change a field that survives a restart
- change what Save Workspace, autosave, or close/restart persists
- debug a workspace that came back with the wrong shape, tab, or directory
- understand why a save was refused instead of silently degraded

Live shells never survive a backend restart — that is by design. What survives
is the **shape** of a workspace and the **presentation** of its panes.

---

## The Three Durable Stores (and one cache)

| File | Owner | Holds | Secrets |
|---|---|---|---|
| `runtime_state.json` | `web/runtime_state.py` | Workspace snapshots: one slot per workspace id, each with groups, per-pane launch config, presentation, window chrome | **Never.** `password` is not a captured field, and that is enforced again on read |
| `saved_sessions.json` | `web/saved_session_store.py` (durability) + `web/saved_sessions.py` (schema) | Named launcher presets | SSH passwords, Fernet-encrypted with the key in `.encryption_key` |
| `config.json` | `web/config.py` | Machine-level settings, reached only through `RuntimeConfig` | No |

`localStorage` / `sessionStorage` are **cache, never the record**: theme,
surface mode, explorer theme/appearance mirrors, cross-tab freshness pings, and
the per-window lifecycle id (`gridvibe.lifecycleWindowId`, in `sessionStorage`,
so a reload replaces its own record). Anything that must come back after a
restart lives in one of the three files above.

The three stores are independent. Forgetting a snapshot never deletes a preset;
saving a preset never rewrites a snapshot.

---

## Durability Mechanics — `web/state_files.py`

**Every durable JSON file goes through it.** A fourth store must too.

1. A cross-process sidecar lock (`<file>.lock`) over the whole
   read-modify-replace, so a second GridVibe process cannot silently discard
   this one's update.
2. A unique same-directory temp file, fsynced, then `os.replace` — a reader
   never sees a half-written document.
3. `<file>.bak` written before every commit.
4. A corrupt or unsupported payload is **quarantined** (moved aside with a
   timestamp) and the backup is read in its place — never laundered into empty
   state that the next commit would make permanent. One deliberate exception:
   an `OSError` on *read* is not quarantined; the bytes may be fine and only
   momentarily unreadable.
5. A failed write raises the store's `StateFilePersistenceError` subclass, and
   the caller answers **"not saved"** — a retryable non-2xx, never the value
   echoed back as stored.

---

## Two Layers of State

**Launchable shape** — what it takes to relaunch a pane: host, port, username,
`directory` (where the pane *is*, not where it started), `launch_directory`
(the explorer's widen floor, which is why it is persisted rather than
re-derived), `startup_mode`, `initial_command`, agent selection, WSL/PowerShell
flags, explorer root plus `explorer_root_configured`. The allowlist is
`_SESSION_SNAPSHOT_FIELDS` in `web/runtime_state.py`.

**Presentation** — what the pane looks like: explorer tabs / sidebar / Git
scope, browser tabs, Markdown and font choices, layout geometry. Normalized in
exactly one place, `web/session_presentation.py`
(`PANE_PRESENTATION_FIELDS`), whose client counterpart is
`web/static/js/explorer-persistence.js` (the v2 explorer record). Bounded by
the immutable `MAX_STORED_SESSION_PANES`, never by
`runtime_config.max_sessions` — a lowered preference must not rewrite a wider
snapshot; it is enforced at launch instead, through `capacity_refusal()`.

Workspace **chrome** (`topbar_visible`, `active_group_id`, native zoom,
`md_preset` / `md_font` / `source_font`) is a third, separately revisioned
thing, owned by the workspace rather than by a group.

---

## Live Presentation Sync (browser to server)

The server's copy of what a pane looks like is updated continuously, so a
capture never has to ask the page for it.

```
terminals.js describeGroupPresentation()     the only DOM adapter
  -> session-persistence.js queue            DOM-free, coalescing, revisioned
  -> POST /api/session-presentation          one whole live group
  -> session_presentation.py normalize       type-check, never coerce
  -> SessionManager.apply_group_presentation compare-and-swap, one lock hold
```

Window chrome takes the same path through `POST /api/workspace-presentation`.

- Every write carries `expected_revision`. A `409` returns the current
  revision; the queue rebases and retries — it never overwrites blind.
- Continuous gestures (a drag, a resize) are coalesced behind
  `CONTINUOUS_UPDATE_FLOOR_MS` (1 s); everything else drains on a microtask.
- A revision learned out of band (a groups refetch) is adopted only while
  nothing is queued or in flight.
- The payload carries **no** launch, credential, or status field. The route
  rejects them, and so should the client that builds them.

---

## Capture: Exactly Three Intents

Nothing else writes workspace shape. A rename deliberately does **not**
capture: it changes the live label, and the next real writer picks it up.

| Intent | Entry point | Scope | Origin |
|---|---|---|---|
| Autosave timer | `_run_workspace_autosave_tick()` -> `capture_live_workspaces()` | every non-empty live workspace | `auto` |
| Explicit save | `POST /api/runtime-state/save` (in-window **Save Workspace**) and `POST /api/workspaces/<id>/save` (launcher per-row **Save**) -> `capture_workspace()` | one workspace | `manual` |
| Voluntary exit | `POST /api/lifecycle/prepare` -> `prepare_lifecycle_action()` -> one all-live `capture_live_workspaces()` | every live workspace | `manual` |

Rules that hold for all three:

- **An empty workspace is never captured**, so an idle launcher or a
  just-restarted process can never wipe a restorable slot. A slot is only ever
  overwritten by the next non-empty capture.
- **Ordering is two-layered.** Each capture takes a monotonic in-process ticket
  *and* reads the durable per-workspace revision *before* it reads the live
  manager. A snapshot taken before an explicit close or forget is rejected at
  commit instead of resurrecting the slot it was meant to remove. A clear
  always wins; an explicit save wins over a newer autosave.
- **No lock spans the two halves.** The manager snapshot is taken and released,
  *then* the file locks are acquired. Never hold `SessionManager.lock` while
  writing a state file or emitting.
- `manually_saved_at` pins a slot the user saved by hand, so the auto-slot cap
  (`MAX_AUTO_WORKSPACE_SLOTS`) only ever collects *stale closed* automatic
  slots — never a live workspace, never a pinned one.

Autosave interval: `workspace.autosave_interval_minutes` (default 5, clamped to
1–15). Failures are logged once per streak, not once per tick.

---

## The Flush Handshake

A capture must record **the screen**, not the last thing the server happened to
hear. Every explicit save and the exit transaction therefore flush first.

1. Each open workspace window registers with `LifecycleCoordinator` on join,
   under a **stable per-window id** (sessionStorage), so a reload replaces its
   own record.
2. `request_flush()` emits `lifecycle_flush_requested` room-scoped and waits,
   bounded by `LIFECYCLE_FLUSH_TIMEOUT_SECONDS` (5 s). Emits happen outside the
   coordinator lock and outside `SessionManager.lock`.
3. Each window drains its presentation queue
   (`window.gridvibeFlushLivePresentation`) and answers `lifecycle_flush_ack`
   with its chrome metadata.
4. Only then is the capture taken.

Failure categories are distinct and reported as such: `client_stale` (a socket
that died while the loss is still fresh), `client_timeout`, `client_emit`,
`client_metadata`. A window gone past `LIFECYCLE_STALE_WINDOW_GRACE_SECONDS`
(120 s) is **departed** — its record is dropped rather than blocking every
later save. A window that drops *during* a flush resolves that flush at once
with the same category rather than running out the timeout. A deliberate leave
is forgotten, not reported. Nothing may block saving permanently.

**Chrome is resolved, never refused.** Records for one workspace arrive
oldest-joined first and each field is applied last-writer-wins, so two honest
windows disagreeing can never cost the save. An `active_group_id` naming no
live group drops that one field to the server's own hint. A malformed *type*
(non-boolean `topbar_visible`, out-of-range native zoom) still raises — that is
a broken client, not a disagreement.

**A workspace with no reachable window is reported, never captured from stale
server state.**

---

## Restore

- `GET /api/runtime-state` (one slot) and `GET /api/runtime-state/workspaces`
  (credential-free summaries) offer what is restorable. Eligibility is entirely
  backend-side and the offer is permanent — only Forget removes it.
- `POST /api/runtime-state/restore` relaunches a chosen set server-side
  (`web/workspaces.py::restore_workspaces`). The response reports that a
  relaunch *started*; SSH outcomes keep arriving on `session_status` with their
  existing retry affordance.
- Every slot is **re-validated on read** (`_validate_slot`), so the chooser's
  group and pane counts describe what a restore of that slot would really
  start.
- **The snapshot is the only source of shape.** A preset contributes exactly
  one thing: the SSH password capture deliberately leaves out, and only onto a
  pane that still names that preset's host, username and port. A pane whose
  credential cannot be mapped keeps its captured shape and falls into its
  normal per-pane authentication error and Retry — the shape is never
  substituted or collapsed to make a credential fit.
- A restore is claimed atomically per workspace id (`already_restoring`), and a
  workspace that already has groups is refused (`already_live`) rather than
  duplicating every tab. The slot's exact id is reused, so the next autosave
  refreshes that slot instead of growing a second one.
- Browser mode grants **one named tab per user gesture**, so a multi-workspace
  restore must still attempt every workspace and report refusals once per
  batch.

---

## Never Persisted

Passwords in `runtime_state.json` · fetched explorer data, file contents, dirty
buffers · explorer Search queries and results · the Source and Git find queries
(in-memory; the Source one belongs to the tab and the path it was typed
against) · voice state of any kind · `surface_mode` (chrome density is a live
global setting, so a restore must never replay the value a group launched with)
· anything only meaningful to one page load.

---

## Failure Boundaries

- **Launchable shape fails; window chrome degrades.** One unusable stored pane
  costs its whole group — never a silently dropped pane, never a coerced value
  installed as live state. An invalid `topbar_visible` or appearance value
  falls back to its default instead of costing the user a workspace.
- **Never answer "saved" for a revision that did not reach the disk.** A
  `RuntimeStatePersistenceError` or `SavedSessionsPersistenceError` becomes a
  retryable `503`, not a success toast.
- **Never tear down after a failed *requested* save** unless the user
  explicitly chose the no-save option. Teardown requires the one-use
  `decision_token` returned only on success.
- **Lifecycle logging is shape-only** — ids, revisions, counts, failure
  categories. Never paths, commands, file contents, payloads, or the
  credential-bearing snapshot.

---

## Adding a Persisted Field — Checklist

For a **launch** field (something a relaunch needs):

1. Add it to `TerminalSession` and `to_dict()` (`sessions/manager.py`).
2. Add it to `_SESSION_SNAPSHOT_FIELDS` (`web/runtime_state.py`) — capture and
   the read-side allowlist are the same tuple.
3. Make sure the launch path reads it (`web/workspaces.py`,
   `_prepare_launch_sessions` / `_restore_group_request`).
4. Decide what `None` means. It must mean "not stated by this build", answered
   from the pane, so a snapshot written before the field existed stays
   restorable.

For a **presentation** field:

1. Add it to `PANE_PRESENTATION_FIELDS` and write its normalizer in
   `web/session_presentation.py`. **Type-check, never coerce.**
2. Add it to the mode set (`pane_fields_for_mode`) so it cannot be sent to a
   pane kind that has no business with it.
3. Describe it client-side in `describePanePresentation` (`terminals.js`) — the
   DOM adapter only; any decision belongs in a DOM-free module.
4. Add it to `_SESSION_SNAPSHOT_FIELDS` if it must also survive a restart.
5. Extend the frozen contract test rather than weakening it.

When a **new pane kind** is added, every place that resolves `startup_mode`
must learn it — a missing branch silently degrades the pane to a plain shell
that then *types* its `initial_command` at the prompt.

---

## Verified In Tests

- `tests/test_session_persistence_contract.py` — the frozen snapshot contract.
  Every stage has shipped, so a failure here is a **regression**, and a new
  `expectedFailure` would mean a newly deferred contract.
- `tests/test_session_presentation.py` — the ordered presentation transactions,
  the client queue, and the explorer v2 record (executed in Node).
- `tests/test_lifecycle.py` — the close/restart service, the flush coordinator,
  and the Node client.
- `tests/test_multi_workspace.py` — capture, restore, close/forget, labels, and
  the launcher save path. It is the behavioural cover for `web/workspaces.py`
  and must pass untouched across any refactor of that module.
- `tests/test_saved_session_store.py` — preset and encryption-key durability.
- `tests/test_backend_concurrency_contract.py` — the RuntimeConfig publication,
  workspace-label claim, and pooled-SSH reservation guardrails.
