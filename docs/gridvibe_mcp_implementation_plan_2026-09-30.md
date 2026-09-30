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

**Status: implemented 2026-09-30** on `szua_gridvibe-mcp-review` from `2d0b2a5`.
It is uncommitted, pending review.
- **Done:** steps 1–4 as written. `write` and the new `writeGeometry` share one
  `save`. The resize's `commit` uses `writeGeometry`. The page implements
  `updateBackgroundGroupGeometry` and `markBackgroundGroupGeometryStale` and
  wires them into `GridVibeBackgroundTab.create`. A flagged view is reconciled
  in the load's cache check by `adoptStoredGeometryForStaleView`, so an
  unflagged restore takes today's code. A flagged view also makes
  `readBackgroundGroupModel` read the server's record, so a second resize is
  not refused as "page and stored layout differ".
- **Changed from the plan:** a fixed-class cached view is converted to a split
  view, not dropped. The restore's `applySplitSlotGeometry` sets every card's
  grid area for a `layout-split-local` view, which is the same conversion an
  on-screen resize makes.
- **Added after review:** a stale view's old arrangement can no longer be
  published. `customSplitLayoutSnapshot` omits it from presentation captures,
  so the queue's 409 recapture cannot rebase it onto the new revision.
  `saveActiveWorkspaceSession` first reads the server's arrangement into a
  stale view (`settleStaleGroupGeometry`), because the saved-session route
  writes the live group's layout without a revision. The save is refused when
  that read fails. After the second review, the same omission covers a
  background tab while an edit holds it (`backgroundTab.held()`), which
  covers a write whose answer has not arrived yet. The save waits for that
  hold before it settles. After the third review, the view counts its
  geometry writes (`geometryGeneration`). A record read by the settle while a
  later write reached the view, or held it, is read again (up to
  `STALE_GEOMETRY_READ_ATTEMPTS`, 3) and not taken. The save is refused if the
  settle keeps being outraced.
- **Not done:** a pane close in a stale tab still captures its close model
  from the view. That is no regression: before this stage the dropped view
  fell back to the page's group record, which is equally pre-write after an
  unknown answer.
- **Tests:** everything listed below. The page hooks also have a new
  `BackgroundResizeCachedViewTestCase`, lifted from `terminals.js` with the
  real restore functions, the real tab module and the real presentation
  controller. The validation suites pass (143), and so does `tests.test_api`
  (951, 1 skip). Ruff is clean. The review document records the details
  under C2.

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

**Status: implemented 2026-09-30** on `szua_gridvibe-mcp-review` from `1fc190b`.
It is uncommitted. One Codex review (OCR delegate, 2 of 2 reviewable files
covered) found nothing.
- **Done:** steps 1–4. The hold is taken after `captureSplitPlacement` and
  released in a `finally`, and a painted split releases it before its fit
  waits. Every moved-on case with a captured placement goes to
  `placeAfterMove`, a rebuilt tab included. The request is bounded by
  `SPLIT_REQUEST_TIMEOUT_MS` (20 s) and answers unknown on an abort. Nothing
  between hold and release awaits `initialLoad`.
- **Changed from the plan:** step 2's premise holds only for a load that
  reaches its barrier after the hold. A load whose read was already out could
  still rebuild during the POST. `background-tab.js` now counts holds
  (`holdCount`). `initialLoad` reads the tab again when a hold began during its
  read or is still taken, up to `LOAD_HELD_READ_ATTEMPTS` (3) for holds that
  came and went. A rebuild during the POST therefore waits, and the answer
  paints into the grid it read. The page's own presentation write is the
  placement, so the rewritten test asserts that and not a background save. The
  rebuilt-in-place branch is kept for a load past those reads, and it also
  schedules a status refresh. A moved-on split with no captured placement
  now carries a note.
- **Not done:** a split intent has no `unknown` outcome, so a timeout reaches
  the agent as `refused`, with the unknown-outcome sentence. The full-reload
  limitation below is recorded in the contract.
- **Tests:** as listed below, plus the three re-read cases in
  `LoadWaitsForBackgroundSplitTestCase`, and `holdCount` in
  `tests/test_background_tab.py`. The validation suites and the adjacent
  `test_background_resize`, `test_resize_bridge`, `test_pane_connecting_overlay`,
  `test_api`, `test_multi_workspace`, `test_session_modes` and both
  window-intent suites pass (1518, 1 skip). Ruff is clean. The review
  document records the details under C3.

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

**Status: implemented 2026-09-30** on `szua_gridvibe-mcp-review` from `df46b9c`.
It is uncommitted. One Codex review (OCR delegate, 1 of 1 reviewable file
covered, the 4 others read by hand) found nothing.
- **Done:** the B3 change as written. `EXPLICIT_FOCUS_RULE` sits beside
  `VIEW_MOVING_TOOLS` and is carried by both focus descriptions.
  `focus_pane`'s example is "e.g. 'show me the new review pane'". The test
  asserts the rule for each `VIEW_MOVING_TOOLS` entry and rejects "after
  split_pane" and "after launch_panes".
- **Changed from the plan:** the rule follows each description's opening
  example rather than being appended at the end, so a client that shortens a
  long description still shows it. For C4, the README at both places and most
  of the CHANGELOG note had already been qualified by `6def41f`, after this
  plan's revalidation at `b4080a9`. So the README is unchanged. The CHANGELOG
  note was only tightened in place: `focus_moved: true` is tied to "that
  immediate hand-back", and the note says `focus_moved` reports only that
  check. `test_open_window_no_raise` already pinned the logged-not-reported
  refusal from the `shown` callback.
- **Not done:** `move_session`'s `show` keeps its own, already correct
  sentence. There is no bounded `shown` wait ([decision 1](#decisions)).
- **Tests:** `tests.test_mcp_tools`, `tests.test_open_window_no_raise` and
  `tests.test_webview_launcher` pass (265, 1 skip), and so do the adjacent
  `tests.test_mcp_navigation` and `tests.test_mcp_geometry` (61). Ruff is
  clean. The review document records the details under B3 and C4.

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

**Status: implemented 2026-09-30** on `szua_gridvibe-mcp-review` from `4c008cc`.
It is uncommitted. One Codex review (OCR delegate, 5 of 5 reviewable files
covered, the 6 others read by hand) found two medium issues. Both were
confirmed and fixed after the review, so those fixes are unreviewed.
- **Done:** steps 1–4 as written. `web/log_redaction.py` holds
  `redact_mcp_path` and `RedactMcpTokenFilter`. The filter formats the record
  once and, only when a token is present, replaces `msg`/`args`. It also renders
  and redacts the traceback into `exc_text`, and redacts `stack_info`, because
  the formatter appends those after the message. `setup_logging` puts it first
  on both handlers through `install_mcp_token_redaction`, which is idempotent.
  With no argument that call also covers every handler already attached to a
  named logger, which reaches the stderr handlers python-engineio and
  python-socketio put on their server loggers at import (review finding 1).
  The pattern matches every separator that still reaches the route: `/`, `//`
  (a redirect to it) and `%2F`, also double-encoded, case-insensitive (review
  finding 2).
  The native launcher calls `setup_logging`, so it is covered. The two debug
  entry points (`api.py` and `web/api.py` run as scripts) install it on their
  `basicConfig` handlers. The origin guard logs `redact_mcp_path(request.path)`.
- **Changed from the plan:** the helper lives in `web/`, not `utils/`, because
  no `web/` module imports `utils/` and the origin guard in `web/app.py` needs
  it. It imports only the standard library, so `main.py` and `web/app.py` can
  both take it without pulling in the token registry. The pattern is anchored so that
  `/api/mcp/...` (the sidecar's pane-id routes) and the literal `/mcp/<token>`
  placeholder are not rewritten.
- **Not done:** the stdio sidecar (`gridvibe_mcp/__main__.py`) keeps its own
  `basicConfig` on stderr. It never holds a pane token, because it talks to
  `/api/mcp/...` over loopback, so it has nothing to redact. The grep for other
  `request.path`/`request.url`/`request.full_path` log sites found only the
  origin guard. `web/ssh_tunnel.py` already logs only the first segment
  (`_loggable_target`). `web/mcp_launch.py` logs the local base URL, which
  carries no token. A handler a library attaches after `setup_logging` runs is
  not seen; none does today (werkzeug adds its own only when the root has
  none).
- **Tests:** the new `tests/test_log_redaction.py` (18). It sends a
  werkzeug-shaped access record, a coloured 404 record, a bad-request-line
  error, an error record from a child logger whose traceback also carries the
  URL, and the origin guard's warning through the Flask test client, all
  through the real `setup_logging` handlers. It reads back both the console and
  `gridvibe.log`. A non-MCP path and `/api/mcp/...` pass unchanged, and the poll
  suppression still drops `GET /api/sessions`. After review: a real Werkzeug
  server on loopback gets `/mcp/<token>`, `/mcp%2F<token>` and `/mcp//<token>`,
  and its own access lines are read back; a handler attached to a named logger
  before `setup_logging`, and engineio's and socketio's handlers, are checked
  and written through. With the handler install and the call-site redaction
  disabled, 7 of the first 15 fail; against the reviewed version, 10 of the 18
  do. The new module, `tests.test_mcp_remote`, `tests.test_api` and
  `tests.test_main` pass (1075, 1 skip), and so do `tests.test_webview_launcher`
  and `tests.test_mcp_tools` (230, 1 skip). Ruff and `git diff --check` are
  clean.
- **Review:** the reviewer ran `ocr delegate preview` and `rule` with escalated
  permissions, and both succeeded. It reproduced both findings with the real
  app and `setup_logging`, using a synthetic token. (1) engineio's and
  socketio's own stderr handlers emitted an `Invalid session /mcp/<token>`
  record unredacted, before propagation reached the filtered root handlers.
  (2) `POST /mcp%2F<token>` reaches the route (routing decodes `%2F`), and the
  access line kept the token. Both were fixed as above; the fixes are
  unreviewed, by the one-round rule.

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

**Status: implemented 2026-09-30** on `szua_gridvibe-mcp-review` from `aedfa80`.
It is uncommitted. One Codex review (OCR delegate, 5 of 5 reviewable files
covered, the 8 other text files read by hand) found one medium issue. It was
confirmed and fixed after the review, so that fix is unreviewed.
- **Done:** the recommended design as written. `ResultStore.mark_read` mints
  the assignment's receipt (`secrets.token_urlsafe(32)`) on the first read and
  returns the same one on every read after it. `ResultStore.report` takes a
  `receipt` and settles only on an exact match through
  `secrets.compare_digest`. A missing, empty, non-text or different receipt is
  refused 409 with `STALE_RECEIPT_MESSAGE`, which tells a legitimate agent to
  call `read_handoff` again. `UNREAD_TASK_MESSAGE` still answers a report sent
  before the read. The receipt field is `repr=False` and never logged.
  `HandoffStore.read` adds `receipt` to its answer only when an assignment is
  tracked. The report route passes `receipt` from the body.
  `GridVibeClient.read_handoff` keeps the receipt per pane on the client, and
  `receipt` is not in `HANDOFF_FIELDS`, so the agent never sees it.
  `report_result` sends it only when it holds one. On the HTTP path
  `PaneTokenRegistry.remember_receipt` holds it on the live token's record.
  `handle_message` takes a `token`, starts each tool call's client with the
  record's receipt, and writes a newly read one back.
- **Changed from the plan:** nothing in the design. `handle_message` gained a
  `token` keyword, and the registry gained `remember_receipt` (the registry's
  surface test now lists it: it writes to a live record and looks nothing up).
  A request's own record copy also takes the receipt, so a batch that reads and
  then reports works. After review, it takes the receipt only when the token
  took it.
- **Live compatibility:** the running GridVibe keeps the old server code while
  new agents' stdio sidecars load this `gridvibe_mcp/` from disk. Against a
  server that issues no receipt, the new client keeps nothing and posts exactly
  `{"result", "status"}`, which the old route accepts. This is pinned by
  `ReceiptTestCase.test_a_gridvibe_that_issues_no_receipt_is_sent_the_body_it_always_took`.
  It was also checked end to end: the new `gridvibe_mcp` drove `read_handoff`
  then `report_result` through `dispatch` against the Flask app extracted from
  `aedfa80`, and the report was recorded. The Stage 5 reviewer, launched after
  the change, fetched its task and reported through the new client against
  the live old server.
- **Not done:** an agent whose MCP server restarts after reading loses the
  receipt, and its next report is refused until it reads again, as the design
  accepts. A stale in-flight `read_handoff` from the replaced agent still reads
  the successor's brief through the pane-id route. It cannot report with the
  successor's receipt (review fix), but reading the brief is the pre-existing
  behaviour and is not addressed here. Over stdio, the relaunch ends the old
  sidecar's process.
- **Tests:** `tests/test_agent_results.py` covers the A3 case the plan asked
  for. After B's read, A's delayed report with A's receipt is refused, B stays
  `working`, and B's own report settles it. It also covers missing, empty,
  different, non-text and non-ASCII receipts, a re-read returning the same
  receipt, a receipt kept out of rows, logs and `repr`, and no receipt when
  nothing is tracked. Store-level cases report through a `_report` helper that
  re-reads the receipt.
  `tests/test_agent_result_routes.py` covers a receipt required at the route
  and absent from pane and wait payloads, and A3 through the routes.
  `tests/test_mcp_results.py` `ReceiptTestCase` checks that the receipt is off
  the projected answer and sent with the report, the old-server body, a
  receipt-less read keeping the held one, and a later read replacing it.
  `tests/test_mcp_handoff.py` covers the receipt carried across two separate
  `/mcp/<token>` requests and a relaunch's new token starting without it, where
  the stale in-flight report is refused and the successor's report settles.
  It also covers a batch that reads and then reports. After review, it covers
  a revoked token's batch that reads the successor's task and cannot report
  with its receipt. `tests/test_mcp_remote.py` checks that the receipt lives
  on the token and goes with it. With the receipt comparison disabled, the 5
  store, route and tunnel A3 tests fail (7 failures across subtests, 1 error).
  With the pre-review write-back, the post-review batch test fails.
  `tests.test_agent_results`, `tests.test_agent_handoffs`,
  `tests.test_mcp_tools`, `tests.test_mcp_remote` and `tests.test_api` pass
  (1263, 1 skip). With `tests.test_agent_result_routes`,
  `tests.test_agent_handoff_routes`, `tests.test_mcp_results`,
  `tests.test_mcp_handoff`, `tests.test_mcp_close` and
  `tests.test_mcp_client`, 1421 pass (1 skip). Ruff and `git diff --check` are
  clean.
- **Review:** the reviewer ran `ocr delegate preview` and `rule` with escalated
  permissions, and both succeeded. It found one medium issue and reproduced it
  with the real loopback and store. `handle_request` resolves a token once per
  request. A batch whose token was revoked, with the successor's task announced
  before the batch's `read_handoff` ran, read that task through the pane-id
  route. `handle_message` then copied the successor's receipt into the batch's
  record before the (refused) write-back, so the batch's `report_result`
  settled the successor's task. **Fixed:** the record copy takes a receipt
  only when `remember_receipt` succeeds on a live token. A relaunch revokes the
  old token before the new connection announces the new task, so a read that
  finds the successor's task always has a revoked token. The fix is
  unreviewed, by the one-round rule. The reviewer also saw one `tests.test_api`
  error in `test_repo_git_timeout_bounds_a_remote_that_goes_quiet`, a
  `TemporaryDirectory` cleanup `WinError 32`. That code is untouched by this
  stage, and the test passed in the coder's runs.

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
