# GridVibe MCP code review, 2026-09-29

- **Branch:** `szua_gridvibe_mcp-focus` at `9e4fec9`, 5 commits ahead of `main`.
- **Scope:** the whole MCP feature (`gridvibe_mcp/` plus the web-side routes, stores, window intents and frontend it drives), with extra attention on this branch's commits.
- **Method:** three read-only Codex reviewers ran in the `gridvibe mcp review` session, each assigned one aspect. The coordinating agent (Claude) checked every finding against the cited source before compiling it here.

| Reviewer | Aspect | Focused tests run | Result |
| --- | --- | --- | --- |
| A | Security, trust boundary, permission gates | 450 passed, 2 platform skips | 1 high, 2 medium, 1 doc |
| B | Tool surface, contract conformance, tests | 623 passed | 3 medium, 1 doc |
| C | Window/split/resize intents, concurrency, frontend | 231 passed | 1 high, 2 medium, 1 low |

All reviewers ended with a clean `git status`, and no repository files were changed. Every finding below comes with an in-memory reproduction the reviewer ran (and did not add to the repo), unless it is marked otherwise.

## Summary

This branch adds background-tab launches, background splits and resizes, and no-raise `open_window`. These are well covered by behavioural tests, and none of the reviewers found a security-gate regression in the five commits. The review surfaced **2 high** and **7 medium/low** defects:

- **Introduced or left incomplete by this branch:** C2 (a refused background resize destroys the cached pane state), C3 (a visible split can lose its placement when the tab is rebuilt before the response arrives), B3 (the explicit-focus rule never reaches the focus tools' descriptions), and C4 (a delayed focus-steal is never reported).
- **Pre-existing on `main`:** A1 (relaunch gates are not rechecked after preflight), C1 (concurrent native opens create duplicate windows and orphan one of them), A2 (MCP bearer token written to logs), A3 (a stale report can settle a replacement task), B1 (the geometry read shape is not the launch shape), and B2 (explorer and browser panes drop `shell`).

## Findings, most severe first

### HIGH

#### A1 — Relaunch permission gates are not rechecked after slow preflight (pre-existing)
`web/session_shell.py:655–729` → `apply_pane_shell_change` (mutation and teardown at `:454–457`, `:516–526`)

`apply_agent_pane_relaunch` checks the following, then hands off to `apply_pane_shell_change`:
- caller liveness
- target kind and whether an agent is already running there
- lineage
- the standing waiver

That function can spend time on binary detection and cwd probing before it rewrites metadata and closes the transport. Nothing re-runs the gates, and nothing binds the commit to the pane state that was checked.

- **Scenario:** An agent calls `set_pane_agent` without `override` on a plain terminal it created. While detection is pending, the person starts Claude in that terminal. The relaunch then goes ahead: it replaces Claude with Codex and closes the transport, without ever asking the running-agent confirmation. A caller that closes, or loses its standing override, in the same window of time is not rechecked either.
- **Evidence:** An isolated test built on the `AgentRequestedRelaunchTestCase` fixture mutated the target during the preflight callback. The call still returned HTTP 200, the target became Codex, and `close_connection` was called. The coordinator confirmed that no recheck exists between the gate block and the `apply_pane_shell_change` call.
- **Fix:** Pass a guarded commit callback into the shared transition. It captures the target, caller and transport identity, and just before mutation it rechecks liveness, current kind, lineage, the machine restriction and the live waiver. Keep detection, I/O and teardown outside shared locks.
- **Status: fixed 2026-09-30** on `szua_gridvibe-wrk-focus`.
  - `apply_pane_shell_change` takes a `commit_guard`, which runs under `SessionManager.lock` in the same hold as the metadata write.
  - `apply_agent_pane_relaunch` moves its gates into `_check_relaunch_gates`. They run once before preflight and again from that guard, with the waiver log suppressed there (the new `log_waiver` flag on `check_lineage`). The second run re-reads the request, so an override-mode grant is checked as it stands at commit.
  - The commit is bound to the same target and caller records, unchanged `_RELAUNCH_BOUND_FIELDS` and the same caller depth. A gate that now refuses answers as it would have at first, `confirm` block included, attached after the lock is released. A changed target answers 409 even under `override`.
  - **Not done:** transport identity is not part of the binding. Reading the connection needs `connection_lock`, which orders before the manager lock. Checking it would also refuse a pane that was still connecting when it was checked, which is the usual `split_pane` → `set_pane_agent` sequence. A person relaunching the pane to the same configuration during preflight is therefore not detected. This is recorded as a limitation in the contract.
  - **Tests:** four new `AgentRequestedRelaunchTestCase` cases change the pane during detection: the person starts Claude, an override target changes, the caller closes, and the standing waiver is withdrawn. All four fail without the guard. A fifth pins the locked hold and the second, non-logging gate run. `tests.test_session_shell` passes (100), and so do the adjacent gate, MCP, handoff and API suites (1445, 1 skip). Ruff is clean.
  - **Docs:** CHANGELOG, the [Agent tools (MCP)](engineering_contracts.md#agent-tools-mcp) contract and `gridvibe_mcp/README.md` (gates section).
  - **Review of the fix:** a Codex reviewer ran OCR delegate review with escalated permissions, and this time both commands succeeded. `preview` found 7 changed files, 2 of them reviewable, and it covered both. The reviewer also read the other 5 by hand, and it ran the focused suite, ruff and `git diff --check`. With the guard bypassed, the four race tests failed. **No findings.** The coordinator confirmed that nothing the guard calls logs, does I/O or takes `connection_lock` inside the manager-lock hold. The reviewer's only caveat is the undetected transport identity, which is disclosed above.

#### C1 — Concurrent native opens create duplicate windows, and closing the older one unregisters the survivor (pre-existing; the no-raise path inherits it)
`web/webview_launcher.py:1749` (lookup), `:1807` (`create_window`), `:1839` (`_attach_workspace_window`); close cleanup at `:2362–2366`

The lookup of `_workspace_windows[workspace]` and the publication of the new window surround native creation, with no reservation in between. Two intents for the same unopened workspace, claimed by two visible pages, can both see an empty slot. Both then create a window and overwrite the same registry entry. The `workspace:` close handler then pops `_workspace_windows`, `_workspace_window_group_ids`, `_workspace_fullscreen_states` and `_workspace_window_minimized` unconditionally. `_forget_workspace_lifecycle_window` correctly declines to forget the replacement, but those pops do not check which window they are removing.

- **Scenario:** Closing the older duplicate unregisters the live window. The next open then creates a third window, and lifecycle retirement drifts.
- **Evidence:** Two `open_workspace_window(..., False)` calls met at a barrier inside a mocked `create_window`. Both returned `reused:false`, two windows were created, and only one was left in the registry. The coordinator confirmed that the close pops have no identity check.
- **Fix:** Reserve creation per workspace under a short registry lock, with native UI outside the lock. Publish only the captured reservation. Identity-check every close or event cleanup (`slot is window`) before touching the slot or its metadata. This is the ownership contract's "a registry key may name a replacement" rule.
- **Status: fixed 2026-09-30** on `szua_gridvibe-wrk-focus`.
  - `_open_workspace_window` reserves the empty slot through `_reserve_workspace_window`. The reservation is an `Event` in `_workspace_window_openings`, taken under the new `_workspace_window_lock`. `create_window`, registration and the foreground hand-back all run outside the lock. The window is published by `_publish_workspace_window` only while this open's reservation still holds the slot, and a `finally` releases the reservation and wakes any waiters.
  - A concurrent open waits on the reservation, then looks again. It reuses the published window, or takes the reservation itself if creation failed. If the window is still not ready after `WORKSPACE_WINDOW_OPENING_WAIT_SECONDS` (30 s), it answers `{"ok": false, "error": "The workspace window is still opening"}` and does not create a duplicate.
  - `_handle_closed` announces the close through `_forget_workspace_lifecycle_window` as before. It then calls `_drop_workspace_window`, which checks the slot and clears it in one lock hold, together with its group, fullscreen, minimized and pending-zoom state and, through `on_drop`, main()'s `open_windows` kind. If the slot holds a different window, the close returns without touching the slot, `open_windows` or the exit check. The `minimized`, `restored` and `maximized` handlers go through `_record_window_minimized`. It checks that the window is still the registered one and writes the flag in the same hold, and it ignores events from a window that is closed (empty slot) or replaced. The legacy `session` kind now goes through the same path, and so it also drops its pending zoom.
  - **Tests:** a new `WorkspaceWindowOwnershipTestCase` in `tests/test_webview_launcher.py` has eight cases:
    - Two concurrent no-raise opens create one window. The second open is parked on the reservation while `create_window` is held.
    - A failed first creation lets the waiting open create the window.
    - An open that outwaits the reservation is refused without creating a window.
    - Closing a replaced window leaves the successor's slot, group, minimized and fullscreen state alone.
    - Closing the current window still clears everything.
    - A replaced window's minimize, restore and maximize events leave the successor alone.
    - A window reopened during the old close's drop stays counted in `open_windows`.
    - A closed window's late minimize event writes nothing.

    The threaded cases always release and join their threads. Against the unfixed launcher, five of the first six fail; the ordinary close passes both ways. The reopen case also fails with the pre-review close ordering. `tests.test_webview_launcher` and `tests.test_open_window_no_raise` pass (136, 1 skip), and so do `tests.test_api`, `tests.test_multi_workspace` and `tests.test_lifecycle` (1275, 1 skip). Ruff is clean.
  - **Docs:** CHANGELOG and [Workspace lifecycle and windows](engineering_contracts.md#workspace-lifecycle-and-windows).
  - **Not covered:** `close_workspace_window` destroys the window but leaves the slot filled until the `closed` event arrives. An open in that gap still reuses the dying window. This is outside C1 and unchanged.
  - **Review of the fix:** a Codex reviewer ran OCR delegate review with escalated permissions, and both commands succeeded. `preview` found 5 changed files, one of them reviewable (`web/webview_launcher.py`), and it covered that file fully. The reviewer read all 5 by hand, ran the focused suites, ruff and `git diff --check`, and left `git status` unchanged. It found no problems in the reservation, wait, release or timeout path, and found nothing held under the lock that should not be. It reported two findings, both reproduced in memory, which the coordinator confirmed against the source and fixed:
    - **Medium: state events checked identity and wrote the minimized flag as two separate unlocked steps.** pywebview runs each event handler on its own thread. An old window's `minimized` handler could pass the check, then the old window could close and the workspace reopen, and the handler would then mark the replacement minimized. A later focus would then restore a window that was already normal or maximized. **Fixed:** `_record_window_minimized` checks and writes in one hold, and it also ignores events from a closed window whose slot is empty.
    - **Low: the close removed its kind from `open_windows` after the drop had released the lock.** A replacement registered in that gap lost its entry. **Fixed:** the removal runs inside the drop's hold through `on_drop`. A successor can only be published once the slot is empty, so it always registers after the removal.
    - **Caveat:** the threaded tests did not always release and join their threads when an assertion failed. They now do.
    - **Not tested:** no test deterministically interleaves the medium finding's two steps, because the check and the write now share one lock hold and a test has no seam inside it. The late-event test covers the empty-slot half.

### MEDIUM

#### C2 — A refused background resize destroys the cached pane state (introduced by this branch)
`web/static/js/background-tab.js:109–118` (`write` calls `discard(groupId)` before saving), reached from `background-resize.js:223–225`; the discard adapter is at `terminals.js:7518–7521` and `:1384–1417`

`tab.write` drops the cached view first and only then attempts the CAS save. The discard disposes every cached xterm, browser frame and explorer pane. As a result, a 409 or refused resize reports "Nothing changed" after it has already thrown away the terminal scrollback position, the find state and the loaded browser document. A successful resize pays the same cost for no reason.

- **Evidence:** A Node test ran the real `GridVibeBackgroundTab.write`, `discardBackgroundGroupView` and `dropCachedGroupView` against a rejected save. The result was `ok:false`, the xterm was disposed and the cache was gone.
- **Fix:**
  - Give resize a geometry-only cache update that runs after a successful CAS and keeps the pane objects and DOM.
  - On a refusal, keep the cache as it was.
  - On an unknown outcome, reconcile the geometry without discarding runtime-only state.
  - Split keeps its current path, because it genuinely changes the pane set.
- **Contract:** This conflicts with the "preserve scroll/focus" performance guardrail, and with the resize refusal's own "Nothing changed" wording.

#### C3 — A visible split is not held until its response, so a rebuild can lose the captured placement (branch fix incomplete)
`web/static/js/terminals.js:7208`, `:7248–7264`; `background-split.js:274–277`; load barriers at `terminals.js:8597`, `:8611`

A visible split captures its layout and cut, then POSTs without taking the background-tab hold. `placeAfterMove` only takes the hold once the response arrives. If the person leaves and returns, or a refresh rebuilds the tab, `initialLoad` can read the new pane together with the pre-split layout, or read the old pane list. When the response arrives and the same group is visible, the handler adopts the group record and returns success. It neither applies nor saves the captured cut, and it reports no failure.

- **Evidence:** An extended `SPLIT_IN_FLIGHT_HARNESS` run showed `tab.settled('g-1')` resolving while the POST was still pending. After the rebuild, the result was `ok:true` with zero placement saves, the visible list was `['pane-a']`, and the group held `['pane-a','pane-new']`. The existing "grid rebuilt in place" test accepts this outcome.
- **Fix:** Take the hold before the visible split request and keep it until creation and placement are reconciled. Reconcile the captured placement against the current tab generation instead of silently abandoning it.
- **Doc:** The CHANGELOG claim that "leaving during a visible split preserves the requested arrangement" does not cover the case where the person returns or the tab is rebuilt before the response.

#### A2 — Live MCP bearer tokens are written to the application log (pre-existing)
`main.py:45–98` (`_SuppressPollLogs`, `_StripAnsiFilter`, werkzeug at INFO), `web/app.py:205–210`

For SSH panes, the `/mcp/<token>` route (`web/api.py:4826`) puts the credential in the URL path. Werkzeug access records are written at INFO to stdout and `logs/gridvibe.log`, and neither filter redacts them. The cross-origin rejection warning also formats `request.path` verbatim, and the tunnel preserves the `Origin` header. The SSH filter's own refusals are redacted, but the HTTP-server side is not.

- **Coordinator check:** The existing logs do contain werkzeug INFO lines with full POST paths, for example `POST /api/mcp/close/pane/<id>`. No `/mcp/<token>` line is present yet because no SSH-pane MCP traffic has been logged, but the mechanism is confirmed.
- **Scenario:** Logs copied into a support bundle expose a credential that stays usable while the pane is alive.
- **Fix:** Redact `/mcp/<token>` in one logging filter that is attached before any handler formats the record, covering access and error records. Have the origin guard log a redacted route.

#### A3 — A delayed report from a replaced agent can settle the successor's task (pre-existing)
`web/agent_results.py:352–369`, `:573–582`; `web/api.py:3294`; `gridvibe_mcp/client.py:722–728`

A report carries only the pane id, the text and the status. `ResultStore.report` picks the pane's newest accepting assignment, and its only guard is `record.read`. Once the successor has read its task, an in-flight report from the old agent looks the same as one from the successor.

- **Scenario:** Task A's worker starts `report_result`. The pane is relaunched with task B, and B calls `read_handoff`. A's delayed request lands afterwards and is recorded as B's report. The requester then stops waiting, or acts on the wrong outcome.
- **Evidence:** An isolated store test gave the following result: B became `reported` with A's text. `test_agent_results.py:502` stops before B's read.
- **Fix:** `read_handoff` returns an opaque assignment receipt, which the sidecar passes internally with `report_result`. The store commits only on an exact match. Transport-generation checks reject stale remote requests that were already resolved.

#### B1 — The geometry that `list_panes` advertises as reusable is not the shape `launch_panes` accepts (pre-existing)
`gridvibe_mcp/server.py:269–290` (schema: `split_column_weights` / `split_row_weights`, `additionalProperties: false`); `web/pane_geometry.py:381–384` (read: `column_weights` / `row_weights`, plus `class_name`, `implied`, `columns`, `rows`)

The `workspace_layout` description reads "exactly as list_panes reports them", but a direct copy is either refused by a schema-enforcing client or has its weights ignored.

- **Evidence:** A read→launch round trip turned `[1.5, 0.5]` into `[1.0, 1.0]`, so a 75/25 layout is recreated as 50/50.
- **Fix:** Emit a canonical launch-shaped `workspace_layout` in `list_panes`, or adapt the read shape explicitly and document it. Add a read→launch→read round-trip test with unequal weights.

#### B2 — Explorer and browser launch entries drop a stated `shell` without validating it (pre-existing)
`gridvibe_mcp/server.py:1381`, `:1394` (early `return request`) before the shell handling at `:1413`

The schema accepts `shell` on every pane entry, but explorer and browser entries return before the shell is parsed. A valid value is never forwarded as `use_powershell` / `use_wsl`, and an invalid one is never refused. This also bypasses the contract's refusal of shell families the caller's machine cannot honour.

- **Scenario:** An explorer launched as WSL is later switched to a terminal and opens in the default shell instead.
- **Fix:** Parse and forward `shell` before the early returns, or remove it from those kinds and refuse it explicitly. Add tests for all four kinds, including remote and WSL incompatibilities.

#### B3 — The branch's explicit-focus rule does not reach the focus tools' descriptions (introduced by this branch)
`gridvibe_mcp/server.py:120–123` (`VIEW_MOVING_TOOLS` comment), descriptions at `:1209–1227` (`focus_session`) and `:1252–1256` (`focus_pane`)

Commit `9e4fec9` tells agents that only the person asking to focus or show something moves the view. That rule lives in the README, the contract and a Python comment, but agents only see the MCP descriptions. `focus_pane` still gives the example "e.g. after split_pane or launch_panes made it", which invites the exact unrequested focus this branch removes.

- **Coordinator check:** Confirmed by reading both descriptions.
- **Fix:**
  - Add one shared sentence to both focus descriptions and to the `show` argument of `move_session`: "call only when the person asked to see, focus or bring it forward".
  - Qualify the after-create example.
  - Extend `test_every_tool_says_whether_it_may_move_the_persons_view` so it asserts the rule itself. Today it only checks "raise its workspace window".

### LOW

#### C4 — A focus steal that arrives late in the `shown` callback is never reported (this branch; runtime occurrence plausible)
`web/webview_launcher.py:1851–1868`

The `shown` callback calls `_hand_foreground_back` and discards its failure. `focus_moved` is derived only from the immediate call. On a backend that fires the activation event after the immediate check, a refused hand-back leaves the tool reporting a minimized window without `focus_moved`.

- **Evidence:** A mocked delayed-event sequence showed this. The installed WinForms backend shows windows synchronously, so this is the edge case the callback claims to cover.
- **Fix:** Wait for a bounded, identity-checked settlement of the initial show before publishing the result, or stop claiming the delayed case. The engineering contract says "immediate", which is accurate. The CHANGELOG over-promises.

## Contract and doc mismatches

| Where | Mismatch | Finding |
| --- | --- | --- |
| Security guardrail and ownership contract | Gate-before-mutation is not held across relaunch preflight (fixed 2026-09-30) | A1 |
| Security contract | Credentials must not be logged, but `/mcp/<token>` is | A2 |
| Handoff/result contract | Protection against a replaced agent's in-flight report stops before the successor reads its task | A3 |
| Ownership contract | Native close cleanup ignores captured identity (fixed 2026-09-30) | C1 |
| Performance guardrail ("preserve scroll/focus") and the resize refusal's "Nothing changed" | Pre-CAS discard | C2 |
| CHANGELOG, visible split | Omits the return/rebuild-before-response case | C3 |
| CHANGELOG, `focus_moved` | Promises more than the immediate-only check delivers (the contract is accurate) | C4 |
| `workspace_layout` description | "exactly as list_panes reports them" is false | B1 |
| README and contract vs. tool descriptions | Explicit-focus rule is missing from the advertised descriptions | B3 |
| `gridvibe_mcp/README.md:10` | Says nothing under `web/` imports the package; the contract correctly names the function-local `web/mcp_http.py` exception | — |
| `gridvibe_mcp/README.md:729–730` | Says any repeated `Content-Length` is refused, but `web/ssh_tunnel.py:224–234` collapses identical values into a set (no framing bypass, because the request is rebuilt). Refuse all duplicates, or document the normalization | — |

## Test coverage gaps

- ~~Target starts an agent, caller closes, or waiver is revoked during relaunch preflight (A1).~~ Covered 2026-09-30.
- HTTP logging redaction for access, error and origin-rejection records (A2).
- Delayed report from agent A after successor B reads its task (A3).
- Identical duplicated `Content-Length` (the existing test uses conflicting values).
- ~~Two different open intents for one unopened workspace, and closing the older duplicate after the replacement is published (C1).~~ Covered 2026-09-30.
- Background resize against a real cached terminal, browser or explorer view, including CAS rejection. Current resize tests mostly stub `discard` (C2).
- Visible split held across the whole POST, with the tab left and returned to or rebuilt before the response (C3). The existing "rebuilt in place" test accepts the defect.
- Delayed `shown` focus failure (C4).
- Visibility changing while an intent list or claim request is in flight. Only "hidden at tick start" is tested.
- Read→launch round trip with unequal weights (B1). `tests/test_mcp_tools.py:1084` feeds a hand-built, write-shaped record.
- Shell preservation and validation across all four launch kinds (B2).
- An assertion that the explicit-focus rule is actually present in the advertised specs (B3).

## Checked and found sound

**Security (A)**
- Caller identity comes from `PaneIdentity`, never from tool arguments.
- HTTP identity is an opaque per-pane random token. Minting is idempotent, the registry is bounded, and close, relaunch and stale-setup all revoke the token.
- The SSH forward admits only the pane's own `POST /mcp/<token>`. It rejects transfer-encoding, conflicting lengths and obsolete line folding, rebuilds one bounded request, discards pipelined bytes, and caps in-flight channels.
- No tool argument can grant `agent_mcp_override`:
  - Relaunch forwards an allowlist.
  - Launches strip the flag.
  - Splits force it to false.
  - A same-agent relaunch preserves a waiver the person granted.
- Close preflights the whole target under the manager lock, refuses self, containers of self, unrelated panes and running agents, and reports exact partial deltas. Move rechecks its gates in the commit lock.
- Tasks are validated before creation and restricted to the caller's machine. Handoff files are created exclusively in private directories, and delivery falls back to paging.
- Conversation ids and credentials are scrubbed from projections.

**Tool surface (B)**
- `open_window` passes `raise_window=False` end to end, while the focus tools keep raising.
- The no-raise result reports page evidence, or `unknown` where there is none.
- Resize validates bounds and types, rejects booleans, and keeps uncertain answers uncertain.
- Split's axis and kind enums and defaults match the reference.
- `read_handoff` paging and `wait_for_results` `already_returned` semantics are covered by tests.
- Neighbour and position math treats 1-based rects and 0-based indices consistently, and a corner touch does not count as adjacency.
- No new pane kind was introduced, so the `startup_mode` resolvers are untouched.

**Windows and frontend (C)**
- `opened_by` is normalized, stamped, persisted and restored, and it keeps agent-opened tabs from selecting themselves.
- Tab-strip rebuild restores only the tab control that had focus.
- No-raise reuse returns before bring-to-front. New windows are created minimized, with the minimized state pre-marked.
- Split and resize share one per-tab hold, recheck whether the tab is active, and release in `finally`.
- Resize verifies revision, order, rects, weights, the shared divider, minimum sizes and the close generation.
- Intent claims are atomic, and the poll serializes deliveries.
- The changed manager and workspace code stays under the manager lock, keeps emits and native UI outside it, and adds no reverse lock ordering.

## Suggested order of work

1. **A1 (done), C1 (done):** gate recheck at commit, and window-slot reservation plus identity-checked cleanup. Both are ownership-contract violations with user-visible damage.
2. **C2, C3:** fix before merging this branch; both are regressions or incomplete fixes introduced here.
3. **B3:** a one-line description change plus a test assertion; it belongs to this branch's intent.
4. **A2, A3:** credential redaction and assignment receipts.
5. **B1, B2, C4,** plus the two README corrections.

## Process note

Every reviewer's OCR delegation step failed with "Access is denied" (the known Codex sandbox limitation). Each one stopped that workflow and reviewed directly with Git, the source and CodeGraph, so no findings came from OCR.
