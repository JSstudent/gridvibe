# GridVibe MCP review: revalidation and staged plan, 2026-09-30

- **Revalidated on:** `szua_gridvibe-wrk-focus` at `b4080a9`, 9 commits ahead
  of `main`.
- **Implemented on:** a new branch cut from it, carrying all six stages.
- **Input:** [gridvibe_mcp_code_review_2026-09-29.md](gridvibe_mcp_code_review_2026-09-29.md).
  A1 and C1 are fixed there. This document covers everything that remains.
- **Method:** every open finding was re-read against the current source on this
  branch. No code was changed and no tests were run for this pass. Line numbers
  below are current and replace the review's, which have drifted.

## Revalidation

| ID | Review severity | Status now | Revalidated severity | What changed since the review |
| --- | --- | --- | --- | --- |
| C2 | Medium | **Confirmed** | Medium | Nothing. `write` still calls `discard` before `saveLayout` (`web/static/js/background-tab.js:110–121`), and `tests/test_background_resize.py:388` pins that order. |
| C3 | Medium | **Confirmed** | Medium | Nothing. The visible split still POSTs without a hold (`web/static/js/terminals.js:7268`). A rebuild of the same tab still takes only the record (`:7306–7313`). `tests/test_background_split.py:1452` still accepts this. |
| A2 | Medium | **Confirmed** | Medium | Nothing. No filter rewrites `/mcp/<token>`: `main.py:45–98` has only the poll-suppression and ANSI filters. The origin guard still logs `request.path` verbatim (`web/app.py:206–211`). |
| A3 | Medium | **Confirmed** | Medium | Nothing. `ResultStore.report` still settles the newest accepting assignment with only the `record.read` guard (`web/agent_results.py:352–369`, `:573–582`). **New design constraint:** the HTTP path builds a client per request (`web/mcp_http.py:386`), so a receipt cannot live only in sidecar memory. See stage 5. |
| B1 | Medium | **Confirmed, wider** | Medium | `list_panes` still reads out `column_weights`/`row_weights` (`web/pane_geometry.py:379–385`), while the schema accepts only `split_*` with `additionalProperties: false` (`gridvibe_mcp/server.py:265–295`). **Also:** `list_saved_layouts` and `save_group_layout` project `class_name` (`gridvibe_mcp/client.py:251–257`), which the same strict schema forbids. So a saved preset's geometry cannot be copied into `launch_panes` either. |
| B2 | Medium | **Confirmed, narrower** | **Low** | `shell` is still dropped for explorer and browser entries without validation (`gridvibe_mcp/server.py:1381–1406`). **But** the launcher itself forces `use_wsl`/`use_powershell` to `false` for those rows (`web/static/js/launcher.js:897–902`), so the review's "forward the shell" fix would contradict the UI. What remains is a stated argument dropped silently, and an invalid value accepted. |
| B3 | Medium | **Partly fixed** | Low | `move_session`'s `show` already carries "Only when the person asked…", and the test asserts it (`tests/test_mcp_tools.py:144–150`). Still missing: the rule in the `focus_session` and `focus_pane` descriptions (`gridvibe_mcp/server.py:1210`, `:1250`). `focus_pane` still gives "e.g. after split_pane or launch_panes made it" (`:1254`). |
| C4 | Low | **Confirmed** | Low | Nothing. The `shown` callback discards `_hand_foreground_back`'s answer (`web/webview_launcher.py:1973–1980`). The CHANGELOG (`:21`) and `gridvibe_mcp/README.md:131`, `:386` do not limit `focus_moved` to the immediate check. The contract (`docs/engineering_contracts.md:1953`) does. |
| Doc | — | **Confirmed** | Doc | `gridvibe_mcp/README.md:10` still says nothing under `web/` imports the package, but `web/mcp_http.py:225` does, inside a function. |
| Doc | — | **Confirmed** | Doc/low | `gridvibe_mcp/README.md:737` says a repeated `Content-Length` is refused. `web/ssh_tunnel.py:224–236` collapses identical values through a `set`, and no test sends identical duplicates. |

## Staging

All six stages land on one new branch and run in order. Stages 1–3 come first
because they are regressions or incomplete work from `szua_gridvibe-wrk-focus`.
Stages 4–6 are pre-existing on `main`. Each stage ends in its own commit, so it
can be reviewed and reverted on its own.

| Stage | Findings | Area | Origin |
| --- | --- | --- | --- |
| 1 | C2 | Background tab write (frontend) | `szua_gridvibe-wrk-focus` |
| 2 | C3 | Visible split hold (frontend) | `szua_gridvibe-wrk-focus` |
| 3 | B3, C4 | Tool descriptions and wording | `szua_gridvibe-wrk-focus` |
| 4 | A2 | Log redaction | `main` |
| 5 | A3 | Handoff report receipts | `main` |
| 6 | B1, B2, two README fixes | Tool surface conformance | `main` |

Every stage follows the same close-out:
- Run its focused tests plus the adjacent suites listed, then `ruff check .`.
- Add one CHANGELOG note in the house shape, and make its headline the commit
  subject, if the person asks for commits.
- Update the owning contract section.
- Mark the finding **fixed** in the review document, in the A1/C1 format:
  status, what was done, what was not done, tests, docs.
- Get an independent review of the fix, as was done for A1 and C1.

---

### Stage 1 — C2: a refused background resize keeps the cached view

**Goal:** a resize from behind never disposes the tab's panes. Split keeps
today's path, because it really does change the pane set.

**Change**
1. `web/static/js/background-tab.js`: split the write into two operations.
   - `write(groupId, rev, layout)`: unchanged, discard then save. Used by split
     only. Rename it or document it as the pane-set-changing write.
   - `writeGeometry(groupId, rev, layout)`: save first and discard nothing. It
     answers with the same `{ ok, revision, error, unknown, thrown }` shape. On
     `ok`, it calls a new injected `page.updateGeometry(groupId, layout)`. On
     `thrown` or an unknown outcome, it calls a new injected
     `page.markGeometryStale(groupId)`. On a refusal, it does nothing.
   - Update the module header's "the write" bullet to describe both writes.
2. `web/static/js/background-resize.js:225`: `commit` calls `tab.writeGeometry`.
   The close-epoch, `thrown`, refusal and revision checks stay in the same
   order.
3. `web/static/js/terminals.js`: implement the two page hooks next to
   `discardBackgroundGroupView` (`:7567`).
   - `updateGeometry`: when a cached view exists and is not the painted one,
     write `className = 'layout-split-local'`, `splitSlotRects`,
     `splitColumnWeights`, `splitRowWeights` and the recomputed
     `splitGridColumns`/`splitGridRows` strings into the cache entry. Reuse the
     helper the visible resize uses to turn weights into the template; do not
     add a second formatter. If the cached view was a fixed-class layout, its
     cards carry no split `grid-area`. In that case, drop the view *after* the
     successful write, as the one fallback, and say so in a comment.
   - `markGeometryStale`: flag the cache entry. On restore, the flagged entry
     reads the group's stored presentation and applies its rects and weights to
     the restored grid before first paint, keeping panes, scrollback and
     browser documents. It falls back to a discard only if the stored pane set
     no longer matches the cache.
4. Wire `updateGeometry` and `markGeometryStale` into the
   `GridVibeBackgroundTab.create` call (`terminals.js:7586`).

**Tests** (Node-executed, existing harnesses)
- `tests/test_background_resize.py`:
  - Replace the order assertion at `:388` (`discard:g-2` before `saveLayout`)
    with `saveLayout`, then `updateGeometry`, and no `discard`.
  - Add cases for a 409 refusal, a thrown save and a no-revision answer. Each
    asserts no discard, and the correct hook or no hook.
- `tests/test_background_tab.py`: run the real `write`/`writeGeometry` against a
  rejected save, with a cached view that holds a disposable xterm stub. Assert
  that the xterm is not disposed and the cache is intact. This is the review's
  reproduction, kept as a regression test.
- `tests/test_background_split.py`: unchanged. Split must still discard.

**Validation:** `tests.test_background_tab`, `tests.test_background_resize`,
`tests.test_background_split`, `tests.test_resize_bridge`,
`tests.test_split_geometry`.

**Docs:** the background-tab rule in
[Agent tools (MCP)](engineering_contracts.md#agent-tools-mcp)
(`docs/engineering_contracts.md:1973–1992`). Edit the CHANGELOG note at `CHANGELOG.md:19` in place ("writes it … without …
repainting what is shown"), because it is unreleased. Add no new fix story.

**Headline:** `(fix) A divider moved in a background tab no longer resets that tab's terminals, browser pages or explorer views.`

**Risk:** the stale-restore path is new behaviour on a hot path, namely every
tab switch. Keep it behind the flag, so an unflagged restore takes exactly
today's code.

---

### Stage 2 — C3: a visible split holds its tab until its placement is written

**Goal:** the captured cut is either applied or reported, never dropped silently.

**Change** (`web/static/js/terminals.js`, visible split handler around `:7255–7313`)
1. Take `backgroundTab.hold(source.groupId)` right after `captureSplitPlacement`,
   before the `fetch`. Release it in a `finally` that covers every return.
2. On the response:
   - **Still shown** (`splitSourceStillShown`): paint as today, then release.
   - **Moved to another tab:** `placeAfterMove` as today. Its own hold nests
     inside the outer one, which the hold's `Set` already supports.
   - **Same group, grid rebuilt:** with the hold in place, `initialLoad` waits at
     `backgroundTabSettled` (`:8646`, `:8660`), so a rebuild cannot finish
     before the response. The response therefore finds the grid torn down and
     not yet rebuilt. Route that case to `placeAfterMove` too, instead of
     `adoptSplitGroupRecord` alone. The tab is not painted, so it is placed from
     the captured model exactly like a tab that was left.
3. **Bound the hold.** A POST that never answers would otherwise block every
   load of that tab. Give the split `fetch` an `AbortController` timeout on the
   same order as the server's split timeout. On an abort, release and report
   the outcome as unknown ("read list_panes before retrying").
4. Check that nothing between hold and release awaits `initialLoad` (for
   example the post-paint presentation save). That would deadlock. If such a
   call exists, release before it.

**Tests** (`tests/test_background_split.py`, `SPLIT_IN_FLIGHT_HARNESS`)
- Rewrite `test_a_grid_rebuilt_in_place_gets_no_pane_painted_into_it`
  (`:1452`). A rebuild started while the POST is out waits for it, and the
  result is `ok: true` with exactly one placement save carrying the captured
  cut.
- Add: leave and return before the response. `tab.settled(g-1)` stays pending
  until the placement write lands.
- Add: a POST that times out releases the hold and answers unknown.
- Keep `test_a_tab_is_never_left_held` (`:1085`) green, and extend it to the
  timeout and throw paths.

**Validation:** `tests.test_background_split`, `tests.test_background_tab`,
`tests.test_split_geometry`.

**Docs:** edit the unreleased CHANGELOG paragraph at `CHANGELOG.md:25` in place
so that it covers returning to the tab and rebuilding it before the response.
Update the contract where visible-split placement is described.

**Headline:** `(fix) A split whose tab is left and reopened, or rebuilt, before the new pane arrives still places the pane where it was asked.`

**Not covered:** a full browser reload drops the page's in-memory capture. The
server then holds the pane with the pre-split layout. Record this as a
limitation. A fix would need the server to take the cut, which is out of scope.

---

### Stage 3 — B3 and C4: the advertised text says what the code does

**B3 change** (`gridvibe_mcp/server.py`)
- Add one shared constant, for example `EXPLICIT_FOCUS_RULE = "Call only when
  the person asked to see, focus or bring it forward."`. Append it to the
  `focus_session` and `focus_pane` descriptions.
- Replace "e.g. after split_pane or launch_panes made it" with a person-led
  example ("e.g. 'show me the new review pane'").
- `tests/test_mcp_tools.py:141–143`: also assert
  `assertIn(EXPLICIT_FOCUS_RULE, …)` for each `VIEW_MOVING_TOOLS` entry. Assert
  that no view-moving description contains "after split_pane".

**C4 change:** wording only ([decision 1](#decisions)). The installed WinForms
backend shows windows synchronously. A bounded wait would add latency to every
no-raise open for a case nobody has observed. The code does not change.
- Edit the unreleased CHANGELOG note at `CHANGELOG.md:21` in place: "…if the new
  window takes keyboard focus as it is created, focus is handed back…". Make
  clear that `focus_moved` reports the immediate hand-back.
- `gridvibe_mcp/README.md:131` and `:386`: the same qualification.

**Validation:** `tests.test_mcp_tools`, `tests.test_open_window_no_raise`,
`tests.test_webview_launcher`.

**Headline (B3):** `(fix) The focus tools tell agents to use them only when you ask to see something.`
C4 needs no headline of its own, because it edits an unreleased note.

---

### Stage 4 — A2: MCP tokens are redacted from every log record

**Change**
1. Add one redaction function and one `logging.Filter` subclass. Put them next
   to the token registry (`web/mcp_http.py`) or in a small `utils/` helper, and
   have `main.py` import them. The filter formats the record once
   (`record.getMessage()`) and substitutes `/mcp/<anything up to / ? space or
   quote>` with `/mcp/<redacted>`. It then sets `msg`/`args`, the same technique
   as `_StripAnsiFilter`.
2. Attach it as the **first** filter on **both** handlers in `setup_logging`,
   not on the `werkzeug` logger. A handler filter sees every record, including
   error records and ones propagated from child loggers. Check the other entry
   points (`web/webview_launcher.py`, the `.bat`/`.sh` launchers) for their own
   logging setup, and cover them the same way.
3. `web/app.py:206–211`: log the redacted path, using the same function.
4. Grep for other `request.path`, `request.url` and `request.full_path` log
   sites, and for any exception text that could carry the URL.

**Tests** (a new focused module, e.g. `tests/test_log_redaction.py`)
- A werkzeug-shaped INFO access record for `POST /mcp/<token> HTTP/1.1` 200,
  an ERROR record carrying the path, and the origin-guard warning through the
  Flask test client with a foreign `Origin`. Each goes through the configured
  handlers and is asserted redacted, with the token absent.
- A non-MCP path passes through unchanged. The poll suppression still works.

**Validation:** the new module, `tests.test_mcp_remote`, `tests.test_api`,
then `ruff check .`.

**Docs:** [Security and trust](engineering_contracts.md#security-and-trust)
(credentials never logged, and the filter is the one owner),
`docs/logging_guide.md`, and `gridvibe_mcp/README.md` where the token is
described as a credential (`web/ssh_tunnel.py:140` wording).

**Headline:** `(fix) A remote pane's MCP access token no longer appears in GridVibe's log.`

---

### Stage 5 — A3: a report settles only the assignment its agent read

**Design (recommended)**
- **Receipt.** When `agent_handoffs.read` first moves a handoff to `READ`, the
  result store mints an opaque random receipt on that assignment
  (`secrets.token_urlsafe`). Re-reading returns the same receipt, which keeps
  "reading twice returns the same brief". The receipt is never logged and never
  appears in a dashboard payload or snapshot, under the same rule as
  conversation ids.
- **Commit.** `ResultStore.report(worker, text, status, receipt)` commits only
  when the live assignment's receipt matches exactly, compared with
  `secrets.compare_digest`. A missing or mismatched receipt gets 409, with a
  message that tells a legitimate agent to call `read_handoff` again. The
  existing `UNREAD_TASK_MESSAGE` path stays in place.
- **Stdio sidecar.** `GridVibeClient.read_handoff` removes `receipt` from the
  projected answer and keeps it on the client instance.
  `GridVibeClient.report_result` sends it. The agent never sees it, so it
  cannot misuse it or leak it into a task.
- **HTTP path.** The client is rebuilt per request, so the receipt is held on
  the pane-token record in `pane_tokens` instead. A relaunch revokes and remints
  the token, which also gives the "transport generation" the review asks for. A
  stale in-flight request that already resolved the old token record carries
  the old receipt and is refused.
- **Sidecar restart.** If the agent CLI restarts its MCP server after reading,
  the receipt is lost and the next report is refused. The refusal message says
  to call `read_handoff` again, which re-issues the same receipt. The relaunched
  predecessor cannot do this, because its process is gone.

**Change:** `web/agent_results.py`, `web/agent_handoffs.py` (the read path at
`:582–612`, which already pops `handoff_id` before answering), `web/api.py:3254`
and `:3281`, `gridvibe_mcp/client.py:690–733`, `web/mcp_http.py` (token record).

**Tests**
- `tests/test_agent_results.py`: extend the case at `:502` past B's read.
  A's delayed report, which carries A's receipt, is refused, and B stays
  `working`. B's own report settles B.
- A report with no receipt is refused. A re-read returns the same receipt.
- Client test: the receipt is absent from `read_handoff`'s projected answer and
  is sent by `report_result`.
- HTTP test: the receipt survives across two separate `handle_request` calls on
  one token and is gone after revoke.

**Validation:** `tests.test_agent_results`, `tests.test_agent_handoffs`,
`tests.test_mcp_tools`, `tests.test_mcp_remote`, `tests.test_api`.

**Docs:** the handoff/result section of
[Agent tools (MCP)](engineering_contracts.md#agent-tools-mcp), and
`gridvibe_mcp/README.md` (the handoff section). Replace the old "a report
carries only the pane id" limitation rather than appending to it.

**Headline:** `(fix) A report from an agent that was replaced can no longer settle the task handed to its successor.`

---

### Stage 6 — B1, B2 and the README corrections

**B1 — geometry round-trips**
- Have `list_panes` emit a launch-shaped `workspace_layout`
  (`split_slot_rects`, `split_column_weights`, `split_row_weights`,
  `original_split_slot_count` when known) beside the existing read-friendly
  `geometry`. This is additive, so no reader breaks. Point the schema
  description at that field: "copy list_panes' `workspace_layout`".
- For saved layouts, check whether `_normalize_workspace_layout`
  (`web/workspaces.py:1174`) defaults `class_name`. If it does, stop projecting
  `class_name` in `GEOMETRY_FIELDS`. If it does not, add `class_name` to
  `WORKSPACE_LAYOUT_SCHEMA`. Either way, what `list_saved_layouts` and
  `save_group_layout` return must validate against the launch schema.
- **Tests:** replace the hand-built record at `tests/test_mcp_tools.py:1084`
  with a real read → launch → read round trip at unequal weights
  (`[1.5, 0.5]`). Add a schema-validation assertion: every `workspace_layout`
  a read tool returns is accepted by `WORKSPACE_LAYOUT_SCHEMA`.

**B2 — `shell` on explorer and browser entries**
- Refuse rather than forward, which matches the launcher
  ([decision 2](#decisions)). Parse `shell` before
  the kind branches. If it is stated on an `explorer` or `browser` entry, raise
  `ToolArgumentError("'shell' applies to terminal and agent panes.")`. Change
  the schema description to "Terminal and agent panes only".
- Audit `build_split_pane_request` and the `shell` handling at
  `gridvibe_mcp/server.py:2045`, `:2323` for the same early-return pattern.
- **Tests:** all four kinds × {absent, valid, invalid}. Add a remote-origin
  launch as well. There, a valid `shell` on a terminal or agent entry is still
  dropped, as the README documents (`:783–785`). On an explorer or browser
  entry it is refused, the same as locally. Assert both explicitly.

**README corrections**
- `gridvibe_mcp/README.md:10`: "Nothing under `web/` or `sessions/` imports
  this package at module level. `web/mcp_http.py` imports its client inside a
  function, to serve the tunnelled path."
- `Content-Length`: refuse every repeated header, to match the README and its
  reasoning ([decision 3](#decisions)). In `web/ssh_tunnel.py:224–236`, count occurrences instead of
  collecting a set. Add an identical-duplicate case beside the existing
  conflicting-value test in `tests/test_mcp_remote.py`. The code changes here,
  so this also needs a CHANGELOG note.

**Validation:** `tests.test_mcp_tools`, `tests.test_mcp_remote`, the geometry
tests (`tests.test_pane_geometry` if present, otherwise the module that covers
`web/pane_geometry.py`), `tests.test_api`.

**Headlines**
- `(fix) A layout read with list_panes or list_saved_layouts can be passed straight to launch_panes, weights included.`
- `(fix) launch_panes refuses a shell stated on an explorer or browser pane instead of ignoring it.`
- `(fix) A remote pane's MCP tunnel refuses any request that repeats its Content-Length.`

---

## Not scheduled

These are recorded here so they are not lost. None is a confirmed defect from
the review.

- **A1 limitation:** relaunch binding does not include transport identity.
  This is disclosed in the contract. Closing it needs a lock-order design, not a
  patch.
- **C1 limitation:** `close_workspace_window` leaves the slot filled until the
  `closed` event, so an open in that gap reuses the dying window.
- **Coverage only:** a visibility change while an intent list or claim request
  is in flight. No defect was found; only "hidden at tick start" is tested.

## Decisions

Settled 2026-09-30. In every case the recommended option was taken.

- **Branch:** all six stages land on one new branch.
1. **C4:** correct the wording only. Opening a window does not wait for the
   `shown` event.
2. **B2:** refuse `shell` on explorer and browser entries, matching the
   launcher.
3. **Content-Length:** refuse every repeated header, identical values included,
   matching the README.
