# Workspace Restore Hardening — Implementation Outline

Last updated: 2026-07-20

This document plans a rewrite of the **restore-after-restart** mechanism
(deep-dive feature 10.5, `web/runtime_state.py`). The current event-driven
snapshot is still flaky after the [92de535] fix and occasionally surfaces a
stray *"Session HH:MM:SS"* pane on restore. The goal is a single, deterministic
snapshot path built around an **autosave timer + explicit user save**, with a
**permanent** restore offer and a schema that is **future-proof for multiple
workspaces in separate windows**.

The plan is deliberately a *replacement*, not a patch: it enumerates the zombie
code to delete so we are left with exactly one place that writes the restorable
snapshot and one place that reads it.

---

## 1. Where we are today

### 1.1 Persistence — `web/runtime_state.py`

| Function | Role |
| --- | --- |
| `build_workspace_snapshot(session_manager)` | Capture live groups + per-session launch fields → `{version:1, saved_at, groups}` |
| `save_workspace_snapshot(session_manager)` | Atomic (`.tmp` + `os.replace`) write under `_runtime_state_lock` |
| `load_restorable_workspace(max_age=12h)` | Return snapshot only if it has groups **and** `saved_at` is within 12 h |
| `prune_group_from_snapshot(group_id)` | Surgically drop one closed group without rebuilding |
| `clear_runtime_state()` | Delete `runtime_state.json` |

State lives in a single gitignored `runtime_state.json` in `BASE_DIR`.

### 1.2 Triggers — who writes the snapshot, and when

- `web/terminal_io.py::_broadcast_session_groups_updated(reason, group_id)` writes on **every group mutation**:
  - `group_closed` → `prune_group_from_snapshot`
  - `all_closed` → `clear_runtime_state`
  - everything else (`launched`, `split`, `reordered`, `session_closed`) → **full rebuild** from the live manager
- `web/webview_launcher.py::_snapshot_workspace_before_teardown` writes on native window close / keyboard interrupt.

### 1.3 Read / restore path

- Banner markup: `templates/index.html` (`#restoreWorkspaceBanner`).
- `web/static/js/launcher.js`: `checkRestorableWorkspace()` → `GET /api/runtime-state`; `restorePreviousWorkspace()` replays each group via `POST /api/sessions {restore:true}`; `dismissRestoreBanner()` → `DELETE /api/runtime-state`.
- `web/api.py`: `GET /api/runtime-state` gates `restorable = bool(snapshot) and not active_groups`; `DELETE /api/runtime-state` clears the file.

---

## 2. Why it is still flaky

1. **Capture happens at the wrong moments.** The snapshot is rewritten on nearly
   every group event. Mid-session the live set is transiently *smaller or
   half-built* than the workspace the user actually has (a pane being rebuilt, a
   leftover scratch pane, a group mid-launch). A full rebuild at one of those
   instants persists a collapsed/partial shape. [92de535] patched only the
   *close* path (prune-vs-rebuild); every other event still does an
   authoritative full rebuild, so the root class of bug survives.

2. **The "anonymous timestamp session" is a symptom of #1 plus weak naming.**
   When a group is created without a name, `web/api.py` names it
   `Session {HH:MM:SS}` (`create_sessions`, `name=session_name or
   f"Session {time.strftime('%H:%M:%S')}"`, ~api.py:1412), and the client
   `buildDefaultSessionName()` (`launcher.js`) also falls back to a full
   timestamp. A snapshot captured during a transient state can persist such a
   half-formed, timestamp-named group, which then reappears on restore.

3. **No notion of "committed" vs "in-progress."** Many writers race with no
   concept of a snapshot that is *safe to restore*. Teardown, timer-less events,
   and pruning all clobber the same single blob.

4. **12 h age gate** (`RUNTIME_STATE_MAX_AGE_SECONDS`) — the user wants the offer
   to be **permanent**.

5. **Single-blob, single-window assumption.** One file, one snapshot, one global
   `restorable = … and not active_groups` gate. A second workspace window would
   clobber the first's snapshot; there is no workspace identity.

---

## 3. Target behaviour (from the request, decisions resolved)

- A **backend autosave timer** persists the open workspace on a fixed interval
  (**default 5 min**, user-configurable **1–15 min** via a global-settings
  slider — §4.4).
- A **"Workspace..." dropdown** in the sessions window (initially a single
  **Save Workspace** item) persists on demand; the dropdown is the seam for the
  future multi-workspace commands (§6.1).
- Startup offers **only the last saved snapshot** (autosave *or* manual). There
  are exactly **two** writers of the restorable snapshot — the timer and the Save
  Workspace menu item — and **nothing else**.
- **No teardown capture.** *(Decision 1.)* Capturing at window close /
  interrupt is exactly the class of ill-timed write that produced the current
  flakiness, so it is dropped. The last autosave tick or an explicit save is the
  recovery point. This is the single most important robustness change here.
- Because the only unsaved writer is the interval timer, the "at least one
  interval must pass" guarantee is **structural**, not a special case: an `auto`
  snapshot cannot exist until the timer has fired once.
- The restore offer is **permanent** — no age expiry.
- **Single workspace now; multi-workspace skeleton in place.** *(Decision 3.)*
  There is one saved workspace slot that cannot be deleted from the UI — the user
  either restores it or ignores it and builds fresh (via Import Session). The
  slot is only ever *overwritten* by the next non-empty capture. `Delete` and
  workspace naming arrive with the multi-workspace upgrade, but the schema,
  persistence, and endpoints are already keyed per-workspace so that upgrade is
  additive.
- **No zombie code**: exactly one snapshot module, two writers, one reader.

---

## 4. Design

### 4.1 Snapshot schema v2 (multi-workspace)

```jsonc
{
  "version": 2,
  "workspaces": {
    "default": {                     // keyed by workspace_id (one slot per window)
      "workspace_id": "default",
      "label": "buildserver-01",     // stable, human-facing; never a bare timestamp
      "origin": "auto",              // "auto" | "manual"  (no "teardown" — decision 1)
      "saved_at": 1752900300.0,
      "groups": [ /* same group + session launch shape as v1 today */ ]
    }
    // future: other window workspaces live here as sibling keys
  }
}
```

- The per-group / per-session fields are **unchanged** from v1
  (`_SESSION_SNAPSHOT_FIELDS`, group id/name/layout/surface_mode/etc.), so the
  `POST /api/sessions {restore:true}` replay path needs no change.
- Today there is exactly one workspace → the constant id `"default"`. Multiple
  windows later add sibling entries; nothing else in the schema changes.

### 4.2 Restore-eligibility rule

A workspace slot is offered on startup iff **both** of:

1. `groups` is non-empty, and
2. `origin` is `"auto"` or `"manual"`.

That is the whole rule. The **"one interval must pass"** guarantee is structural:
the only unsaved writer is the autosave timer, which cannot produce a slot before
it has fired once, so an unsaved workspace younger than one interval simply has no
snapshot to offer. `manual` is the explicit user override, eligible immediately.
**No maximum age** — the offer is permanent.

### 4.3 Two writers only — the autosave loop + the Save button

Replace all scattered event/teardown writes with **one** capture function driven
by exactly two triggers:

| Trigger | `origin` | Notes |
| --- | --- | --- |
| Backend autosave thread, every `interval` | `auto` | Daemon thread; captures each live workspace that has ≥ 1 group |
| `POST /api/runtime-state/save` (Workspace ▸ Save Workspace) | `manual` | Immediate capture; eligible at once |

Both call the same `capture_workspace(workspace_id, origin)` which:
- builds the group/session shape from the live manager (as today),
- computes a **stable label** (see §4.5),
- read-modify-writes only that workspace's slot (preserving sibling slots).

Group create/split/reorder/close events, **and window teardown**, no longer write
the snapshot at all — the live UI events still fire, but the restorable snapshot
is untouched. The autosave interval is the sampling rate; if the app dies between
ticks we fall back to the last tick (or the user's last manual save). Losing up
to one interval of unsaved shape is the accepted, deterministic trade for
robustness.

### 4.4 Backend autosave thread + interval setting

- Start from the server bootstrap (alongside `run_server` / app init in
  `web/api.py`), daemon, single instance guarded by a module flag.
- **Interval is a user setting.** New `workspace.autosave_interval_minutes` in the
  app-settings block: **default 5**, range **1–15**, exposed as a slider in
  global settings (§6.3). Backend clamps to `[1, 15]` minutes.
  - `default_config.json` gains `"workspace": { …, "autosave_interval_minutes": 5 }`.
  - `_normalize_app_config_update` (`web/api.py`, ~345) validates/clamps it in the
    `workspace` block alongside `surface_mode` (~api.py:358); `runtime_config`
    exposes it; `GET/POST /api/settings` carry it.
  - The thread re-reads the interval each tick (or on a settings-changed signal)
    so moving the slider takes effect without a restart.
- Each tick: for every live workspace (today: the single `default` bucket of all
  groups) with ≥ 1 group, `capture_workspace(id, "auto")`.
- **The timer never clears a slot.** An empty live workspace (e.g. the launcher
  with no session window open, or right after a restart) is simply *skipped* — the
  last saved snapshot is preserved so it stays restorable. A slot is only ever
  *overwritten* by the next non-empty capture, never erased by the timer. *(This
  is what makes "cannot be deleted, just ignore or overwrite" hold — decision 3.)*
- Runs regardless of whether a browser/session window is connected — this is why
  it belongs on the backend, not a `setInterval` in the page.

### 4.5 Killing the "anonymous timestamp session"

Two independent fixes, both required:

1. **Never capture a transient shape** — guaranteed by §4.3 (only the timer and
   the explicit Save write) and §4.2; a half-built group never lives long enough
   to be sampled by the timer, and teardown no longer writes at all.
2. **Never persist or replay a bare-timestamp name.** At capture, derive `label`
   from the group's real name, else host, else directory basename — the same
   precedence the launcher already uses **minus** the timestamp fallback. If none
   exists, use a neutral stable label (e.g. `"Workspace"`), never
   `Session {HH:MM:SS}`. Restore replays the stored `label`; it must **not**
   re-invoke `buildDefaultSessionName()` and mint a fresh timestamp.

### 4.6 Multi-window seam (build now, wire later)

- The snapshot is already keyed by `workspace_id`; the read/write helpers take an
  explicit id (defaulting to `"default"`).
- The one place that still assumes a single workspace is *the mapping from live
  groups → workspace_id*. Today: "all live groups belong to `default`." Encode
  that as a single helper `iter_live_workspaces(session_manager)` yielding
  `(workspace_id, groups)` so that when windows own group sets, only this helper
  changes — the persistence, eligibility, endpoints, and timer are already
  per-workspace.
- The restore endpoint accepts an optional `workspace_id`; the launcher (which
  spawns windows) can later present a chooser across restorable slots. For now it
  reads `default`.

---

## 5. API changes

| Method / route | Change |
| --- | --- |
| `GET /api/runtime-state` | Return the restorable slot for `workspace_id` (default `"default"`): `{restorable, workspace_id, label, origin, saved_at, groups}`. `restorable` uses §4.2 eligibility and **drops** the `not active_groups` gate (a window may restore into itself; the launcher has no active groups so is unaffected). |
| `POST /api/runtime-state/save` | **New.** Body `{workspace_id?, label?}`. Capture now with `origin="manual"`. Returns the saved slot summary. Backs the **Workspace ▸ Save Workspace** menu item. |
| `DELETE /api/runtime-state` | **Skeleton only — not wired to the single-workspace UI** *(decision 3)*. Clears one `workspace_id` slot. Reserved for the multi-workspace upgrade's per-workspace Delete; the single-workspace banner's Dismiss does **not** call it (§6.2). |

`saved_at` stays epoch seconds for the banner's "saved N min ago" copy.

> **Why keep `DELETE` at all now?** So the multi-workspace upgrade is purely
> additive — the endpoint and `clear_workspace(id)` helper are the delete
> primitive, already per-workspace. In single-workspace mode nothing invokes
> them, so the one saved slot is genuinely un-deletable from the UI and only ever
> overwritten by the next capture.

---

## 6. Frontend changes

### 6.1 Workspace menu (sessions window) — dropdown, built for multi-workspace

The save-workspace control is a **"Workspace..." dropdown**, not a bare button —
modelled exactly on the existing **"Sessions..."** menu so the two read as one
pattern and the dropdown is already the anchor for the multi-workspace commands
that land later.

- **First factor a neutral `.app-menu*` CSS base.** The dropdown styling
  currently lives under `.sessions-menu*` (`web/static/css/terminals.css`,
  ~line 502): `.sessions-menu`, `.sessions-menu-toggle`, `.sessions-menu-toggle::after`,
  `.sessions-menu-panel`, `.sessions-menu.open .sessions-menu-panel`,
  `.sessions-menu-item` (+ `:hover`/`:focus`/`:disabled`), and the responsive
  block ~line 582. Rename these rules to a **workspace/session-agnostic
  `.app-menu*`** family (`.app-menu`, `.app-menu-toggle`, `.app-menu-panel`,
  `.app-menu-item`, `.app-menu.open .app-menu-panel`, …) so both dropdowns are the
  same component, not a copy. Update the existing "Sessions..." markup to the new
  classes in the same pass — this is a rename, not a fork, so there is no
  duplicated `sessions-menu`-vs-`workspace-menu` CSS to drift.
- **Markup** (`templates/terminals.html`, in the top bar next to the
  `#sessionsMenuRoot` "Sessions..." menu, ~line 31): give the Workspace dropdown
  the same shape using the neutral base — an `.app-menu` root (`#workspaceMenuRoot`)
  + an `.app-menu-toggle` button (`#workspaceMenuBtn`, label "Workspace...",
  `aria-haspopup="true"` / `aria-expanded`) + an `.app-menu-panel` (`#workspaceMenu`,
  `role="menu"`) of `.app-menu-item` buttons. The re-classed Sessions dropdown
  uses the identical `.app-menu*` classes (its ids stay `#sessionsMenuRoot` etc.).
- **Open/close JS** (`web/static/js/terminals.js`, next to
  `toggleSessionsMenu`/`closeSessionsMenu`, ~line 1046): add the analogous
  `toggleWorkspaceMenu(event)` / `closeWorkspaceMenu()` using the same `.open`
  class + `aria-expanded` toggle, and hook the same outside-click / other-menu
  handling so opening one dropdown closes the other. (The `.open` toggle is
  class-name-agnostic, so the JS is unaffected by the CSS rename.)
- **Initial contents: a single item — "Save Workspace"** →
  `closeWorkspaceMenu(); saveWorkspace(this)` → `POST /api/runtime-state/save`.
  On success show a transient confirmation ("Workspace saved") with the returned
  `label` and time; **disable the item when the workspace is empty** (no live
  groups), matching how the Sessions menu disables its save items.
- **Built as the multi-workspace seam.** The dropdown is where the workspace
  commands from the multi-workspace upgrade (§4.6, §5) attach. Structure the
  markup/JS so each future command is just an appended `.app-menu-item` +
  handler — no restructuring — mirroring how "Sessions..." already holds
  Import / Save / Save-as / Save-all. Planned later items (do **not** build now,
  just leave room):
  - **Save Workspace As...** — capture into a new named `workspace_id` slot.
  - **Import Workspace...** — restore/adopt a saved slot into this window.
  - **Rename Workspace...** — edit the slot `label`.
  - **Delete Workspace** — the per-workspace UI that finally wires
    `DELETE /api/runtime-state` / `clear_workspace(id)` (§5), still un-wired in
    single-workspace mode.

### 6.2 Restore banner (launcher) — permanent, Dismiss is hide-only

- `checkRestorableWorkspace()` / `restorePreviousWorkspace()` keep their
  structure.
- Banner text uses the stored `label` and `saved_at` ("Restore *buildserver-01*
  — saved 12 min ago").
- No client-side age logic; the backend eligibility rule is authoritative.
- **`dismissRestoreBanner()` changes:** it now only **hides the banner
  client-side** for this launcher session — it must **not** call
  `DELETE /api/runtime-state` *(decision 3)*. The saved slot is preserved so the
  user can ignore it, build fresh via **Import Session**, and let the next
  autosave overwrite the slot with the new workspace. (Removing the `fetch(DELETE)`
  from the current `dismissRestoreBanner` is part of the zombie-code cleanup.)

### 6.3 Autosave-interval slider (global settings)

- Add a range slider to the workspace section of global settings (the launcher
  settings panel that already edits `surface_mode`): **1–15 min, step 1, default
  5**, labelled e.g. "Auto-save workspace every N min".
- Persists via the existing `POST /api/settings` under
  `workspace.autosave_interval_minutes`; `loadAppSettings()` hydrates it.

---

## 7. Zombie code to remove (explicit)

Deleting these is a required part of the task — the end state has one writer and
one reader.

- `web/runtime_state.py`
  - `prune_group_from_snapshot` — **delete** (no more per-event pruning).
  - `RUNTIME_STATE_MAX_AGE_SECONDS` and the age branch in the loader — **delete**
    (permanent offer).
  - v1 `build_workspace_snapshot` / `save_workspace_snapshot` /
    `load_restorable_workspace` — **replace** with the v2 `capture_workspace`,
    `load_restorable_workspace(workspace_id)`, `clear_workspace(workspace_id)`
    (multi-workspace skeleton, unused by the single-workspace UI), plus
    `iter_live_workspaces`. Keep atomic-write + lock plumbing.
  - Add a **one-time v1→v2 migration** on load: wrap a legacy `{version:1,…}`
    file into `workspaces.default` with `origin="auto"` so an existing user still
    gets one restore; then it is rewritten as v2.
- `web/terminal_io.py::_broadcast_session_groups_updated`
  - Remove **all** snapshot writes (`prune_group_from_snapshot`,
    `clear_runtime_state`, `save_workspace_snapshot`) and the `reason`-based
    branching around them. **Keep** the `socketio.emit('session_groups_updated')`.
  - Drop the now-unused `web.runtime_state` imports.
- `web/webview_launcher.py` — **delete the teardown capture entirely** *(decision 1)*.
  - Remove `_snapshot_workspace_before_teardown` and every call to it (the
    `_handle_closed` window-close path, `_open_browser_mode`'s
    `KeyboardInterrupt` path).
  - Remove `from web.runtime_state import save_workspace_snapshot`.
  - `close_all_sessions()` / `os._exit` behaviour is unchanged; only the snapshot
    write is removed. There is now **no** snapshot write anywhere in the launcher.
- `web/api.py`
  - Update the `GET` handler per §5 (drop the `not active_groups` gate); add
    `POST …/save`; keep `DELETE` as the multi-workspace skeleton (not UI-wired).
  - Start the autosave thread from server bootstrap; add
    `workspace.autosave_interval_minutes` to `_normalize_app_config_update` +
    `default_config.json` + `runtime_config`.

---

## 8. Concurrency & safety

- Keep `_runtime_state_lock`; every write is **read-modify-write of the
  `workspaces` dict** so writing slot A never drops slot B (timer, request
  thread, and teardown can overlap).
- Retain atomic `.tmp` + `os.replace`.
- Passwords are still never persisted (unchanged `_SESSION_SNAPSHOT_FIELDS`);
  restored SSH panes re-auth via keys or saved-session password.
- The timer skips empty workspaces rather than clearing, so a launcher-idle or
  just-restarted process (zero live groups) can never wipe the restorable slot.

---

## 9. Tests (extend `tests/test_api.py`, `tests/test_webview_launcher.py`)

- v2 schema round-trips; v1→v2 migration produces a restorable `default` slot.
- Eligibility matrix: `auto` eligible; `manual` eligible immediately; empty groups
  never eligible; **no** max-age rejection (a very old slot is still offered).
- Multi-workspace isolation: writing slot A leaves slot B intact;
  `clear_workspace(A)` leaves B intact.
- Autosave thread captures a non-empty workspace and **skips (does not clear)** an
  empty one — the previously saved slot survives an emptied/idle workspace.
- Interval setting: `autosave_interval_minutes` clamps to `[1, 15]`, default 5;
  survives round-trip through `GET/POST /api/settings`.
- `POST …/save` writes `origin="manual"` and is immediately restorable.
- Group create/split/close **and window teardown** no longer mutate
  `runtime_state.json` (guards against reintroducing ill-timed writes — the core
  regression fix).
- Regression: a group with no name is captured with a non-timestamp `label`, and
  restore does not mint `Session HH:MM:SS`.
- Banner Dismiss hides client-side without deleting the slot (no `DELETE` call).
- Preserve the existing agent-auto-mode / explorer-tab-view snapshot-field
  coverage (fields are carried forward unchanged).
- Drop / rewrite the current teardown-snapshot tests in
  `tests/test_webview_launcher.py` (they assert `save_workspace_snapshot` is
  called on close — that behaviour is intentionally removed).

## 10. Resolved decisions

1. **No teardown capture.** *(Resolved.)* Dropped entirely — it is the ill-timed
   write class behind the current flakiness. Only the autosave timer and the
   manual Save button ever write. This is the robustness anchor of the whole
   plan; `web/webview_launcher.py` ends up with **zero** snapshot writes.
2. **Interval = 5 min default, 1–15 min slider in global settings.** *(Resolved.)*
   `workspace.autosave_interval_minutes`, clamped `[1, 15]`, default 5 (§4.4, §6.3).
3. **No Delete in single-workspace mode.** *(Resolved.)* One saved slot that
   cannot be deleted from the UI; the user restores it or ignores it and builds
   fresh, and the next non-empty capture overwrites it. The timer never clears.
   `clear_workspace(id)` + `DELETE /api/runtime-state` exist as the
   **multi-workspace skeleton** (per-workspace, named workspaces, per-slot Delete)
   but are not UI-wired yet.

## 11. Suggested sequence

1. Rewrite `web/runtime_state.py` to the v2 schema + `capture_workspace`,
   `load_restorable_workspace(id)`, `clear_workspace(id)`, `iter_live_workspaces`,
   eligibility, migration. (Largest change; everything else depends on it.)
2. Strip snapshot writes from `_broadcast_session_groups_updated`; **delete** the
   teardown capture in `web/webview_launcher.py`.
3. Add the backend autosave thread + `autosave_interval_minutes` setting
   (`default_config.json`, `_normalize_app_config_update`, `runtime_config`).
4. Update `web/api.py` endpoints (`GET`, new `POST …/save`, skeleton `DELETE`).
5. Frontend: Workspace dropdown (Save Workspace item, built as the multi-workspace
   seam — §6.1) + banner label/time copy + interval slider;
   make Dismiss hide-only (drop its `DELETE` call).
6. Fix the group default-name path so no bare-timestamp label is ever produced.
7. Tests + `make check`; update `CHANGELOG.md` and the 10.5 note in
   `docs/deep_dive_review_2026-07-10.md`.
