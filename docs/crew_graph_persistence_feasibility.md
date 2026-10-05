# Preserving crew graphs across restarts: feasibility and proposal

Status: analysis only, nothing implemented. Proposal for review; once a decision is made, the resulting rules move into `docs/engineering_contracts.md` and this note is removed or archived.

## Verdict

Feasible, medium effort. Persisting the links is easy. Restoring them is the hard part, because nothing stable ties a link to the pane it was drawn between.

## How the graph works today

- The crew graph is not stored. `web/dashboard.py` builds `links` on every poll from `agent_results.links_snapshot()` (`web/agent_results.py`).
- `ResultStore` is in-memory and documented as "Never persisted" with "No Flask, no I/O" in its module docstring.
- A link is `LINK_FIELDS`: `link_id`, `requester_session_id`, `worker_session_id`, `state`, `read`, `status`, `collected`, `handed_at`, `reported_at`, `reason`, `round`, `label`. It never carries report text, the receipt or the handoff id.
- The dashboard drops any link whose requester or worker is not a live agent row (`web/dashboard.py`, the `links` comprehension). That is the deliberate "no ghosts" rule: a closed pane takes its links with it.

## The blocker

- Link endpoints are session ids. A pane id is just the session id (`web/lifecycle.py`, `web/session_presentation.py`).
- Session ids are random per launch (`sessions/manager.py`, `uuid4().hex[:8]`). Restoring a saved layout creates new sessions with new ids, so stored endpoints would match nothing.
- The only persisted per-pane identity found is `agent_conversation_id`. It is experimental, gated by `conversation_restore_enabled()`, set only for some providers, and must never reach a response, log or dashboard payload. It is not usable as a graph key.

## Proposed implementation (simplest and safest)

Principle: persist structure only, restore it as history, and let the existing live-row filter be the safety net.

1. **A stable per-pane key.** Add one persisted field, `crew_pane_key`: random, minted when an agent pane is created, carried through save and restore like any other pane field. Follow the "Adding a Persisted Field" checklist in `docs/session_state_guideline.md`, and make sure every `startup_mode` resolver carries it.
   - Rejected alternative: remap by saved-session id plus slot position. It needs no schema change, but a rearranged layout would attach a link to the wrong pane. A key makes a mismatch impossible.
2. **Store the links with the layout they belong to.** On an explicit workspace save, write the current links into that saved layout entry, endpoints translated from session id to `crew_pane_key`, through `web/state_files.py`. No new file, no new lifecycle: a link lives and dies with its layout.
   - Persist only the structural subset: key pair, `state`, `status`, `round`, `label`, `handed_at`, `reported_at`, `reason`. Never text, receipt or ids.
   - Keep the I/O out of `ResultStore`. A small owner module does the translate-and-write; `ResultStore` only gains a `seed()` entry point.
3. **Restore as inert history.** After a layout restore, map keys to the new session ids and seed the store. Any `working` link is demoted to `ended` with a restore reason, since its worker process is gone. Mark seeded links as restored so the board can render them as history rather than live work.
4. **Fail closed.** A link whose key does not resolve to a live agent row is dropped, which the existing dashboard filter already does. A failed or corrupt read restores no links and does not block the layout restore.
5. **Bounds.** Cap persisted links per layout (reuse `MAX_ASSIGNMENTS`, 256), and validate before mutation.

## Rule and doc changes this would force

- `web/agent_results.py` docstring: replace "Never persisted" with the restored-history rule; keep "never persisted" for report text and receipts.
- `docs/engineering_contracts.md`, "Agent dashboard": state that a restored link is history, never `working`.
- Replace the "no ghosts" comment in `web/dashboard.py` so it names restored links as the one case that survives a restart.
- `docs/session_state_guideline.md`: add `crew_pane_key` and the saved links to the persisted-field list, and to "Never Persisted" what stays out.
- `CHANGELOG.md` entry, tag `(feat)`.

## Tests

- Round trip: save a layout with a crew, restart, and the restored graph shows the same edges with `working` demoted to `ended`.
- A link whose pane was closed before save, or whose key is missing, is dropped.
- A corrupt or oversized links block does not stop the layout restoring.
- Report text, receipt and handoff id never appear in the saved file.
- The new pane field survives save and restore for every pane kind that can be an agent.

## Open questions

- Is the saved layout entry the right home for the links, or does the save path make a separate file simpler? Not checked against `web/saved_sessions.py` in detail.
- Should restored history age out (for example after the next full round), or persist until the layout is overwritten?
- Autosave versus manual save: autosave must not demote a manually saved workspace (Core Guardrails, Lifecycle), so decide which captures write links.

## Rough size

New field plus checklist wiring, one small persistence owner module, `seed()` plus the `ended` demotion, doc updates and tests. Most of the risk is in the key propagation and restore semantics, not the file writing.
