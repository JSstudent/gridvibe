# Preserving crew graphs across restarts: feasibility and implementation plan

Status: reviewed against the code on 2026-10-05 (branch `szua_gridvibe-taper`,
after release 1.15.1). Nothing implemented. Once built, the resulting rules move
into `docs/engineering_contracts.md` and `docs/session_state_guideline.md`, and
this note is archived.

## Verdict

Feasible, medium-small effort. Do it **without a new pane field**, and keep
restored links **out of `ResultStore`**.

The first draft of this note got the facts about today's graph right. Its
design rested on three wrong assumptions:

1. **It used the wrong save surface.** It stored links in a "saved layout"
   (`saved_sessions.json`). What comes back after a restart is the runtime
   workspace snapshot (`runtime_state.json`, `web/runtime_state.py`).
2. **It wrote links on explicit save only.** Every capture (autosave, explicit
   save, voluntary exit) rewrites the whole slot. A links block written only
   by explicit save is erased by the next autosave.
3. **It seeded restored links into `ResultStore`.** Seeded links would leak
   into `wait_for_results`, the follow-up gate and eviction.

With those fixed, the blocker the draft named (no stable pane identity) goes
away too. The restore already replays the snapshot's panes in order and gets
back the new session ids, so the snapshot's own coordinates are a stable key.

## Review of the draft's claims

| Draft claim | Verdict | Evidence / correction |
| --- | --- | --- |
| Graph is not stored; built per poll from `links_snapshot()` | Holds | `web/dashboard.py::build_dashboard_snapshot` |
| `ResultStore` docstring: "Never persisted", "No Flask, no I/O" | Holds | `web/agent_results.py` module docstring |
| `LINK_FIELDS` list (12 fields), never text/receipt/handoff id | Holds | `web/agent_results.py` `LINK_FIELDS` |
| Dashboard drops links whose endpoints are not live agent rows | Holds | `compose_dashboard()`; agent row = `startup_mode == "agent"` (`is_agent_pane`) |
| Session ids are random per launch | Holds | `SessionManager._generate_session_id`, `uuid4().hex[:8]`; a restore does not reuse them (group ids are new as well) |
| `agent_conversation_id` is not usable as a graph key | Holds | experimental, gated, captured only when `agent_conversation_resume` |
| "Restoring a saved layout creates new sessions" / store links "in that saved layout entry" | **Wrong surface** | Restart survival is the runtime snapshot: `capture_workspace` / `capture_live_workspaces` → `restore_workspace` → `_restore_group_request`. Presets (`saved_sessions.json`) are templates; per the guideline, a field "that only a restore can honestly replay ... stays out of the preset". The open question about `web/saved_sessions.py` is moot. |
| Write links "on an explicit workspace save" | **Wrong** | `_build_slot` rebuilds the slot from scratch on every capture, and autosave runs every 1–15 min. Links must be written by all three capture intents, or the first autosave after a save erases them. The voluntary-exit capture (the normal path to a restart) is also `manual` origin. |
| Autosave must not demote a manual save, "so decide which captures write links" | **Misapplied** | Demotion is about `manually_saved_at` pinning (`_build_slot`), not about which fields a capture writes. It does not bear on links. |
| Need a new persisted `crew_pane_key`, minted at agent-pane creation | **Unnecessary, and riskier** | `_restore_claimed_workspace` launches each snapshot group's panes in order and gets back `payload["sessions"]` in creation order, so (snapshot group id, pane index) → new session id is exact. A pane-level key would need `TerminalSession`, `to_dict`, `update_session` allowlist, `_SESSION_SNAPSHOT_FIELDS` and preset stripping. It would also have to cover panes that *become* agents later (`set_pane_agent`, mode switch), which "minted when an agent pane is created" misses. |
| "Make sure every `startup_mode` resolver carries it" | **Misapplied** | That checklist item is for new *pane kinds*, not new fields. |
| Rejected alternative: positional remap misattaches after a rearranged layout | **Wrong for this surface** | Links and panes are captured in the same slot write from the same manager snapshot, and a rearrangement is a new capture that rewrites both. Positional coordinates cannot drift from the panes they describe. (The objection holds for presets, which this design does not use.) |
| `ResultStore` gains `seed()` | **Unsafe** | Records in `_records` take part in `wait_for_results` (`_matched_locked`), the follow-up gate (`live_assignment`), `forget_session`, eviction (`MAX_ASSIGNMENTS`) and `collect`. A restored `reported` link has no text and could be "collected" as an empty report. A restored link could also satisfy the follow-up gate and re-grant a capability, the same hazard that keeps `created_by_session_id` out of the snapshot. Records are also keyed by `handoff_id`, which is never persisted. |
| Persist only `state`, `status`, `round`, `label`, `handed_at`, `reported_at`, `reason` | **Incomplete** | The board needs `read` (`linkPhase`) and `collected` (`linkCollected`, pill hover). All `LINK_FIELDS` are already public dashboard data. Persist them all except `link_id`, which is minted fresh on restore. |
| Demote `working` to `ended` with a restore reason | Holds, with additions | `reason` is a *key*: add one (`"restarted"`) to `_ENDED_REASONS` and `DASHBOARD_CREW_END_REASONS` (`dashboard-dialog.js`). A restored uncollected `reported` link also needs handling: its report text is gone, so the hover's "has not collected the report yet" would be false. |
| Cap at `MAX_ASSIGNMENTS` (256) | Holds | Reuse it as the per-slot cap |
| Doc changes listed | Incomplete | Also: the "round ... lives in memory only" line in contracts → Agent dashboard; the "Only live panes are in a crew" header comment in `web/static/js/agent-crews.js`. |

**Not covered by the draft: cross-workspace crews.** An agent can create panes
in another workspace (`open_window`, window intents), so a link can span two
slots. Each slot restores on its own, possibly never. v1 persists only links
whose two endpoints are in the same captured workspace (see "Scope cut").

## How the graph works today

- `ResultStore` (`web/agent_results.py`) holds assignments in memory, keyed by
  `handoff_id`. `links_snapshot()` projects them through `LINK_FIELDS`.
- `build_dashboard_snapshot()` reads the links after releasing the manager
  lock. `compose_dashboard()` keeps only links whose two endpoints are composed
  agent panes ("no ghosts").
- `forget_session()` runs from `web/terminal_io.py` (session gone) and
  `web/mcp_close.py`. A requester's assignments go with its pane; a worker's
  pending one ends as `pane closed`.
- The frontend (`agent-crews.js`) collapses links per (requester, worker) pair.
  The **last in list order** is the current round, and a worker's parent is the
  requester of its newest link.

## Design

Principle: persist what the board already shows, restore it as inert history
in its own store, and let the existing live-row filter stay the only place
that decides visibility.

### 1. Where it lives: the runtime slot

Add one optional top-level slot field, `crew_links`, to the runtime-state
slot. It is written by every capture and validated on read. It goes away with
the slot (forget, clear, auto-slot eviction), so there is no new file and no
new lifecycle.

Each entry is `LINK_FIELDS` minus `link_id`. The two session ids are replaced
by endpoints in snapshot coordinates:

```json
{"requester": {"group": "<snapshot group_id>", "pane": 0},
 "worker":    {"group": "<snapshot group_id>", "pane": 2},
 "state": "reported", "read": true, "status": "done", "collected": false,
 "handed_at": "...", "reported_at": "...", "reason": "", "round": 2,
 "label": "..."}
```

No pane field, no `TerminalSession` change, no preset change. Nothing reaches
the launch body.

### 2. Capture: every intent, outside the locks

In `capture_workspace` and `capture_live_workspaces`, right after
`session_manager.snapshot_live_workspaces()` and **before** taking the
runtime-state file locks, read the links once: live `ResultStore` links plus
held history links (step 4). No store lock is ever held together with the
manager lock or a file lock.

For each workspace being captured, build a session id → (group id, pane index)
map from the same `groups` the slot is built from. Then keep a link only when:

- both endpoints are in that map (same workspace; v1 scope),
- both endpoint panes have `startup_mode == "agent"` in the snapshot (the
  dashboard's rule, applied at write time),
- it is not superseded: for each pair, keep live links over history links, and
  at most the newest link per pair (by list order).

Demote `working` to `ended` with reason key `restarted` at **write** time, so
the file never claims work is in flight. Cap at `MAX_ASSIGNMENTS`, keeping the
newest. The translation is a pure function in a new owner module (step 4) and
`runtime_state.py` only calls it, so `ResultStore` keeps "no I/O".

### 3. Read: validate, degrade, never fail the slot

In `_validate_slot`, validate `crew_links` as **chrome-class** state
("launchable shape fails; window chrome degrades"). The rules:

- An invalid block, or a block over the cap, becomes `[]`.
- An entry that is invalid, or whose endpoint group is not among the
  surviving validated groups or whose pane index is out of range, is dropped.
- Types are checked, never coerced. `state` must be `reported` or `ended`
  (anything else is dropped). `status` goes through the store's own status
  set, `label` through `validate_task_label`, and `reason` must be a known key
  or `""`.

A slot written before the field existed has no `crew_links` and reads as `[]`.

### 4. Restore: a separate history store

New module `web/crew_history.py`, in-memory, no Flask, no I/O, one lock of its
own:

- `install(workspace_id, links)`: takes links already translated to live
  session ids, mints a fresh `link_id` for each, and marks each
  `restored: True`.
- `snapshot()`: the held links, as plain dicts in `LINK_FIELDS` plus
  `restored`.
- `forget_session(session_id)`: drops any link naming that pane. It is called
  beside the two existing `agent_results.forget_session` call sites
  (`web/terminal_io.py`, `web/mcp_close.py`), so history follows the same
  close rule as live links.
- `supersede(pairs)`: drops history for a pair once a live link for it exists.
  It is called from the dashboard read and from capture.
- `reset()` for tests.

In `_restore_claimed_workspace`, after the group loop, use each started
group's `snapshot_group_id` and `payload["sessions"]` to build (group id, pane
index) → new session id. Map a group only when the payload's session count
equals the snapshot group's. Translate the slot's `crew_links`, drop any
endpoint that does not resolve, and `install()`. If anything here fails, log
it shape-only and restore no links; the workspace restore itself never fails
because of it.

History is never in `ResultStore`, so `wait_for_results`, `collect`,
follow-up gating, `live_assignment` and eviction cannot see it. A requester
whose conversation resumed (experimental restore) and asks for results is
correctly told nothing is pending.

### 5. Dashboard and board

- `build_dashboard_snapshot()`: `links = crew_history.snapshot() +
  agent_results.links_snapshot()`, with history **first**, so a live round
  for the same pair is the newest and wins in `indexCrews()`. Apply
  `supersede()` before concatenating. `compose_dashboard()` is unchanged; its
  filter still decides visibility.
- Frontend: add `restarted` to `DASHBOARD_CREW_END_REASONS` ("GridVibe
  restarted before it reported"). For `link.restored`, the pill hover replaces
  the collected/not-collected clause with "reported before GridVibe restarted;
  the report was not kept". `linkPhase()` needs no change.

### Scope cut: cross-workspace links (v1 drops them)

A link between panes in two different workspaces is not persisted in v1, and
this is documented as a limitation. Supporting it later needs only two
things: endpoint refs that are global within a capture (for example the
captured session id under a non-live name), and a pending set in
`crew_history` that resolves when the second workspace restores. The v1 file
format does not have to change for that.

## Implementation plan

Each stage is self-contained, has its own tests and keeps the suite green. Run
the stage's focused tests plus `ruff check .`. Run the full suite once, at the
end.

### Stage 1: history store (pure)

- New `web/crew_history.py` with `install`, `snapshot`, `forget_session`,
  `supersede`, `reset`, the `restarted` key constant, and a pure
  `translate_for_capture(links, pane_coords, agent_panes)` /
  `translate_for_restore(entries, coord_to_session)` pair (validation and
  demotion live here).
- Add `"restarted"` to `_ENDED_REASONS` in `web/agent_results.py`, so the key
  is known in one place.
- Tests: new `tests/test_crew_history.py`. Cover round trip through both
  translations, demotion of `working`, supersede, forget, the cap, and that
  output keys are exactly `LINK_FIELDS` (+ `restored`), never text, receipt or
  handoff id.

#### Implementation notes

Done. `web/crew_history.py` holds `translate_for_capture`,
`translate_for_restore`, a `CrewHistory` class and the module-level `history`
with `install`/`snapshot`/`forget_session`/`supersede`/`reset` bound to it.

- Capture picks, per (requester, worker) pair, the newest link, but a live link
  always beats a `restored` one whatever the input order. Output keeps input
  order and is capped at `MAX_ASSIGNMENTS`, newest kept.
- Both translations share one field validator (types checked, never coerced;
  `working` demoted to `ended`/`restarted`; `reason` must be a known key, which
  adds `undeliverable` and `other` to the `_ENDED_REASONS` keys; `label` goes
  through `validate_task_label`). `translate_for_restore` takes
  (group id, pane index) → session id and drops anything that does not resolve.
- `install(workspace_id, links)` replaces that workspace's held links, rebuilds
  each from `LINK_FIELDS` alone (fresh `link_id`, `restored: True`) and caps
  that workspace at `MAX_ASSIGNMENTS`, newest kept. The cap is per slot, so one
  workspace's install never evicts another's links.
- `supersede(pairs)` takes (requester, worker) session-id pairs.
- `"restarted"` is in `_ENDED_REASONS` with its own sentence; `ResultStore`
  never passes it to `end()`.
- Tests: `tests/test_crew_history.py`; `agent_results`, handoff, dashboard and
  MCP result suites stay green.

### Stage 2: capture and read in runtime state

- `web/runtime_state.py`: read links once per capture before the file locks.
  Write `slot["crew_links"]` through `_build_slot` (a new parameter) in both
  capture paths, and validate it in `_validate_slot`.
- Tests (`tests/test_runtime_state.py`):
  - a capture writes links with coordinates, and autosave keeps them, which
    pins down the erasure bug;
  - a `working` link is stored as `ended`/`restarted`;
  - a cross-workspace link and a non-agent endpoint are dropped;
  - a corrupt, oversized or wrong-typed block degrades to `[]` and the slot
    still restores;
  - a pre-field slot still restores;
  - the stored file contains no report text, receipt, `handoff_id` or
    `link_id`.

  Extend `tests/test_session_persistence_contract.py` if it freezes the slot
  key set.

### Stage 3: restore and close wiring

- `web/workspaces.py::_restore_claimed_workspace`: build the coordinate map,
  translate, `crew_history.install()`, all guarded so it can never fail the
  restore.
- `web/terminal_io.py` and `web/mcp_close.py`: call
  `crew_history.forget_session` beside `agent_results.forget_session`.
- Tests:
  - restore of a slot with links installs history keyed to the new ids;
  - a failed or skipped group drops its links;
  - a count mismatch maps nothing for that group;
  - closing a restored pane drops its history;
  - restored links never appear in `ResultStore` (`count()`,
    `live_assignment`, `wait_for_results`).

### Stage 4: dashboard and board

- `web/dashboard.py::build_dashboard_snapshot`: history first, then live, and
  supersede.
- `web/static/js/dashboard-dialog.js`: the `restarted` reason and the
  restored hover clause.
- Tests:
  - `tests/test_dashboard.py`: restored links pass the live-row filter; a
    restored link to a closed pane is dropped; a live link supersedes history
    for its pair.
  - `tests/test_agent_crews.py` / `tests/test_dashboard_dialog.py`
    (Node-executed): a restored link's phase and hover text; history-then-live
    order makes the live round current.

### Stage 5: docs and changelog

- `web/agent_results.py` docstring: unchanged in substance (the store is
  still never persisted). Add one line pointing at `web/crew_history.py` for
  the restored structure.
- `web/dashboard.py` "no ghosts" comment and the `agent-crews.js` header
  bullet: restored history is the one case that outlives a restart, and only
  between live agent rows.
- `docs/engineering_contracts.md` → Agent dashboard: restored links are
  history, never `working`, never in `ResultStore`, same-workspace only, and
  always on with no setting.
  Rewrite "The count lives in memory only".
- `docs/session_state_guideline.md`: add `crew_links` to the stores table and
  to Restore, and to Never Persisted add "report text, receipts, handoff ids,
  link ids, cross-workspace links".
- `README.md`: one bullet in the Agent Dashboard section ("Crew links survive
  a restart as history").
- `CHANGELOG.md`, top of Unreleased:
  `- **(feat) Agent crew links survive a GridVibe restart as history.** ...`

## Resolved questions

- **Home for the links:** the runtime slot, not presets and not a separate
  file.
- **Which captures write them:** all three. Anything less is erased by the
  next autosave.
- **Ageing out:** history for a pair is dropped once a live link for that pair
  exists, and with its pane. Otherwise it lives as long as its slot. That
  bounds it by the crew's own size, with no timer.
- **Cross-workspace crews (decided 2026-10-05):** not in v1. Only links whose
  two endpoints are in the same captured workspace are persisted, per "Scope
  cut". Cross-workspace support stays a possible follow-up.
- **Setting (decided 2026-10-05):** none. The feature ships on by default with
  no switch, because nothing in it grants a capability. Unlike conversation
  restore, no consumer checks a gate, so no `config.json` key, settings UI or
  `RuntimeConfig` field is added.

## Rough size

One new pure module (~150 lines) and about 40 lines across `runtime_state.py`,
`workspaces.py`, `dashboard.py`, the two close sites and `dashboard-dialog.js`.
Plus tests and docs. Most of the risk is in the capture/restore translation and
in keeping history out of `ResultStore`, and both are covered by pure tests.
