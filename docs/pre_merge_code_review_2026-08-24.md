# Pre-merge code review: `szua_gridvibe_wrk_testing` into `main`

Date: 2026-08-24  
Base: `main` at `5745763b7a10552ab84387e01c826e574ddf465e`  
Reviewed head: `54729cb8c7ee1fb00262946c46f4edb55e0820fd`

This is a point-in-time code audit, not a behavior contract. The review covered executable code, tests, templates, styles, and runtime configuration in `main...HEAD`; documentation was used only to resolve intended behavior.

**Validation pass (2026-08-24, same commit, working tree clean apart from this file).** Every finding below was re-checked against the code, and three were exercised against a running system. The pass changed three things: H-1 is **withdrawn** (its test evidence does not reproduce, and its mechanism is a sub-millisecond race the code deliberately declines to take); H-2 is **re-scoped** (three of its four bullets describe code that is unchanged from `main`); and H-3 is **upgraded from analysis to reproduction** — the out-of-scope discard was executed and the file contents confirmed. Each finding now carries a `Validation` note recording what was verified and how.

## Post-review remediation

**Stages 1 and 2 were implemented, automatically exercised, and manually accepted on 2026-08-24.** H-3, M-1, M-2 and the branch-scoped part of H-2 are resolved in the post-review code. The findings and verdict below remain the point-in-time record for the reviewed commit; they no longer describe the disposition of those findings in the updated tree. M-3 and L-1 remain assigned to Stage 3, and H-2's three pre-existing connection-lifecycle bullets remain separate follow-up hardening — Stage 2 does not claim them.

H-3 remediation:

- Stage All and Unstage All now pass the selected root/Pin/Follow pathspec to Git, including the unborn-`HEAD` unstage form.
- Discard All scopes its complete status read first, then restores only the tracked, non-conflicted worktree paths returned from that scope. Staged content, untracked files, conflicts, and out-of-scope paths remain untouched.
- A narrowed Commit compares the complete repository staged-path set with the complete scoped set and refuses when hidden staged paths exist. Repository-root Commit keeps its normal behavior, while Publish deliberately remains branch-wide.
- The Discard All confirmation now says it affects tracked files in the current Git scope and retains the staged/untracked preservation warning.

M-1 remediation:

- Local and SSH Git runners now report exit-status observation, stdout/stderr truncation, output-limit termination, and overall completion independently. An output ceiling no longer invents `returncode=0`.
- Structural reads and every mutation require a complete result. An incomplete mutation reports that repository state may have changed, requests a fresh state read, and is never retried automatically.
- Repository search and bounded diff are the only callers allowed to consume stdout-limited output, because both have an explicit partial-result contract. Stderr-limited execution and a command/channel ending without a status remain failures.

H-2 remediation (branch-scoped bullet 4 only):

- A parsed working-directory event now publishes only while the exact connection that produced it is still the registry's entry for that session — identity, not session id and not shell kind, so a replacement of the same family is a different shell.
- The current-entry check, the directory comparison and the metadata write are one `connection_lock` → `SessionManager.lock` hold; parsing and residue handling stay lock-free, and the broadcast runs after both locks are released. A discarded event changes no status, replays nothing, and never touches the replacement's metadata.
- Bullets 1–3 are untouched by design. No attempt tokens, registration changes, pump/finalizer rewrites, or shell-switch resequencing were made.

M-2 remediation:

- Reset view captures the pane object and session id before it waits. The mouse teardown follows that pane wherever it now lives — cached and off-screen included, with the pane's own queued output flushed ahead of it — while the redraw and the busy release are refused once the capture is no longer the pane on screen. The busy hold releases the buttons it disabled rather than re-resolving them by slot, so neither pane is left mid-refresh. Clear was given the same treatment; it awaits identically.
- Explorer Git actions capture pane, session id and normalized scope, and re-check all three before every post-`await` write, render, reload and refresh. A replaced pane is not painted and has its Git model marked stale for a fresh load when its group returns; a pane that only changed Follow scope is painted and its current scope loaded immediately. Errors are never attached to a moved pane, busy state is always released on the captured pane, and the already-sent mutation is never cancelled or reissued.

Stage 2 automated acceptance added `tests/test_explorer_git_identity.py` (the real sidebar executed in a Node `vm` across group switches, Follow-scope changes, delayed successes and delayed failures), captured-target coverage in `tests/test_terminal_modes.py`, and retired-connection, replaced-connection, parse/publish-barrier and lock-freedom cases in `tests/test_api.py`. The regression tests were confirmed to fail against the unmodified files first. The full runner reached 2,023 tests with 9 skipped and no failures; Ruff and `git diff --check` passed. The user then completed the shell-switch and both delayed-frontend manual scenarios and accepted the result.

Automated acceptance added real sibling-scope repository coverage for Stage/Unstage/Discard All and narrowed Commit, plus local/SSH coverage for stdout limits, stderr limits, missing completion status, normal non-zero exits, structural reads, mutations, and the explicit search/diff partial paths. The focused Stage 1 suites, Ruff, and `git diff --check` passed. The full runner reached 2,004 tests with 9 skipped; its only two environment-sensitive failures were the withdrawn H-1 process-tree cases, which the fix plan explicitly excludes from Stage 1. The user then completed the disposable-repository manual scenarios and accepted the result.

## Verdict

Do not merge yet — on H-3 alone. A narrowed Git sidebar can irreversibly discard unstaged edits it never showed the user, and that was reproduced end to end, not inferred.

The required test suite **is** green at this commit: two consecutive full runs of `tests/run_tests.py` reported `Ran 1983 tests … OK (skipped=9)`. The original verdict's claim that the suite fails does not hold; see *Verification performed* and *Withdrawn findings*.

Finding count: **0 critical, 2 high, 3 medium, 1 low** (plus 1 withdrawn).

## High severity

### H-2 — A retiring terminal pump can consume or close its replacement shell

Locations: [`web/api.py`](../web/api.py#L2801), [`web/terminal_io.py`](../web/terminal_io.py#L211), [`web/terminal_io.py`](../web/terminal_io.py#L836), [`web/terminal_io.py`](../web/terminal_io.py#L931), [`web/terminal_io.py`](../web/terminal_io.py#L1493)

The shell-switch route closes the old connection and starts a replacement under the same session ID. Connection pumps and finalization are keyed only by that ID:

- `_stream_local_output()` re-reads `ssh_connections[session_id]` on every loop, so an old pump that wakes after replacement registration can start reading the new connection.
- `_finalize_stream(session_id)` unconditionally closes whichever connection currently occupies that key, even if the finalizer belongs to the previous connection.
- Connection registration assigns by session ID without an expected generation, so overlapping connection tasks can overwrite one another without retiring the displaced transport.
- `_observe_terminal_output_cwd()` separately reads and updates session metadata without checking that its connection is still current. Final output from the old shell can therefore restore `current_directory` after the switch deliberately cleared it.

Impact: a shell switch can leave the pane disconnected, produce two readers for one PTY, leak a displaced transport, or persist the previous shell's late cwd as the new shell's observation. The route tests mock both teardown and background startup, so they do not exercise this interleaving. This conflicts with the check-then-act/identity rules for shared state and the cwd guardrail that a dead shell's report must be cleared on retargeting.

Recommendation: give each connection attempt a generation or identity token. Pumps, cwd publication, registration rollback, and finalization should compare that identity under `connection_lock`; only the matching entry may be read, updated, popped, or closed. Keep parsing and emits outside shared locks, preserving the documented lock order.

**Validation — the four bullets are true code facts, but only the fourth belongs to this branch.**

The premise "the *new* shell-switch route" is wrong. `/api/sessions/<session_id>/shell` exists in `main` at `web/api.py:2698` with the identical `_close_ssh_connection` → `PENDING` → `start_background_task(_connect_session)` sequence; this branch changed only the cwd source (`effective_directory` in place of the bare probe) and added the `current_directory=None` clear. `reconnect_session()` has carried the same close-and-restart-under-one-ID pattern for longer still.

Bullets 1–3 are unchanged from `main`:

- the per-loop registry re-read is byte-identical at `main:web/terminal_io.py:591`;
- `_finalize_stream` → `_close_ssh_connection(session_id)` pops whatever occupies the key, as does the SSH error path's `was_stored` check;
- registration is `ssh_connections[session_id] = connection` behind a session-exists check only — no generation.

Bullet 4 **is** branch-introduced and stands: `_observe_terminal_output_cwd()`, `effective_directory()` and `web/terminal_cwd.py` do not exist in `main`. `SessionManager.update_session_metadata` does a literal `setattr` (`sessions/manager.py:1067`), so `current_directory=None` is a real clear that a late pump write can undo.

Both races also need the retiring pump to wake *after* a full replacement shell spawn, which is a wide gap on every path examined.

Conclusion: keep the finding, but it is pre-existing connection-identity hardening with one new symptom, not a defect this branch introduced. Recommend re-filing bullets 1–3 as their own hardening item and keeping only the cwd republication as a merge-scoped concern.

### H-3 — Narrowed Git views can mutate changes outside the selected scope

Locations: [`web/api.py`](../web/api.py#L1644), [`web/explorer.py`](../web/explorer.py#L2344), [`web/explorer.py`](../web/explorer.py#L2906), [`web/explorer.py`](../web/explorer.py#L2926), [`web/explorer.py`](../web/explorer.py#L3066), [`tests/test_api.py`](../tests/test_api.py#L14056)

Pin/Follow scope is applied to status and graph reads through a pathspec, so the sidebar can show only one subdirectory. The three bulk helpers accept `current_path`, but use it only to discover the repository root:

- Stage All runs `git add --all` at the repository root.
- Unstage All resets/removes `.` at the repository root.
- Discard All runs an unscoped porcelain status and restores every returned tracked path.

Consequently, Discard All can irreversibly remove unstaged edits that are not visible in the narrowed sidebar. Stage/Unstage All also change hidden index entries, and Commit subsequently commits any staged entries outside the displayed scope. Passing the anchor into the helper does not make it the action target, so the implementation does not meet the one-selected-scope guardrail in effect.

The real bulk-action tests exercise repository-root scope. The selected-anchor test mocks each helper and verifies only that `current_path` was passed, so it cannot detect the repository-wide filesystem effect.

Recommendation: either apply the selected pathspec consistently to bulk mutations, or explicitly define these controls as repository-wide and surface all affected out-of-scope changes before confirmation. The irreversible action must never discard a file hidden by the current scope.

**Validation — confirmed, and reproduced end to end. This is the finding the verdict rests on.**

The read/mutate asymmetry is exactly as described. Reads narrow: `_get_git_context()` passes `backend.pathspec(repo_root, current_path)` into `git status` (`web/explorer.py:2350`) and `_get_git_repo_summary()` narrows the graph and commit-file log by the same anchor pathspec (`web/explorer.py:2783`). Mutations do not: `add --all` (`:2919`), `reset --quiet HEAD -- .` / `rm --cached -r -- .` (`:2940`, `:2942`), an unscoped `status --porcelain -z` (`:3080`) feeding `restore --worktree` over every returned path (`:3092`), and an unscoped `commit` (`:3116`).

Executed against a live app through the Flask test client — one repository, unstaged edits in `inscope/` and `outscope/`, request scoped to `inscope`:

```text
narrowed sidebar changes: ['inscope/a.txt']
discard-all status: 200
inscope/a.txt  -> 'orig-a\n'
outscope/b.txt -> 'orig-b\n'     <- discarded, and never shown in the sidebar
```

It is reachable from normal UI, not just by hand-built query strings: `explorerGitScopePath()` returns a subdirectory for either Pin or Follow, and `explorerGitRequestUrl()` puts `scope=path` on every action URL including `discard-all`.

Two aggravating details the original text did not record. The confirm copy is not scope-aware — "Discard the unstaged changes in every tracked file?" with the note "Unstaged edits will be lost" — while the button's own enabled/disabled state is driven by the *narrowed* change list, so the control reads as scoped and the sentence beside it does not. And the divergence is genuinely new: `_explorer_git_anchor_paths()` does not exist in `main`, so while the bulk helpers were already repository-wide, until this branch there was no narrowed view for them to contradict.

The test critique is accurate as written: `test_all_git_mutations_share_the_selected_root_or_followed_anchor` patches each helper and asserts only that the anchor arrived as the last positional argument.

## Medium severity

### M-1 — Output truncation is synthesized as Git success

Locations: [`web/explorer.py`](../web/explorer.py#L1141), [`web/explorer.py`](../web/explorer.py#L1286), [`web/explorer.py`](../web/explorer.py#L1594), [`web/explorer_search.py`](../web/explorer_search.py#L258)

Both local and SSH Git runners set `returncode=0` when stdout reaches its ceiling, even though they terminate or close the command before learning its real result. Only repository search consumes `stdout_truncated`; status, graph, commit-file, and mutation callers generally treat the partial result as complete.

A direct probe with the existing test stubs and a real exit status of `7` produced this result on both runners:

```text
{'returncode': 0, 'truncated': True, 'stdout_bytes': 10485760}
```

Impact: a large status/graph can silently omit entries, and a verbose hook or mutation can be reported as successful after being killed mid-command. The output-ceiling guardrail is met for memory use but loses command-result correctness.

Recommendation: represent incomplete output independently from process success. Read-only callers that intentionally support partial data must label it; mutation callers must fail when the command was terminated before a trustworthy exit status was obtained.

**Validation — confirmed, with one correction to what is actually new.**

Reproduced against the real runner with a child process that writes past the ceiling and exits `7`:

```text
truncated:     {'returncode': 0, 'truncated': True,  'stdout_bytes': 262144}
not truncated: {'returncode': 7, 'truncated': False}
```

`stdout_truncated` is read in exactly one place in the tree (`web/explorer_search.py:263`), so every other caller does treat a truncated result as a complete one.

The correction: `returncode=0 if truncated` is **not** new — it stands unchanged in `main` at `web/explorer.py:1118`, on the opt-in bounded branch that only search used. What this branch changed is that *every* Git invocation is now bounded, which extends the synthesis to status, graph, commit-file, diff and all eight mutations. That widening is the regression, and Medium is the right weight for it. Note also that the behaviour is stated deliberately in the runner's own docstring (`web/explorer.py:1210-1212`), so this is a design disagreement to settle rather than an oversight to patch.

### M-2 — Async frontend completions can act on a different pane in the same grid slot

Locations: [`web/static/js/terminals.js`](../web/static/js/terminals.js#L4690), [`web/static/js/terminal-modes.js`](../web/static/js/terminal-modes.js#L81), [`web/static/js/explorer-git-sidebar.js`](../web/static/js/explorer-git-sidebar.js#L1050)

Two new asynchronous paths retain an integer grid index across an acknowledgement or request:

- Reset view captures the original terminal and session, but its delayed writer calls `terminalModeResetWriter(index)`, which resolves `terminals[index]` when the replay acknowledgement or 1.5-second fallback arrives. A group switch in that window writes the mouse-mode teardown into the incoming pane and leaves the original pane armed.
- `performExplorerGitAction()` captures `pane`, `sessionId`, and `scopePath`, but unlike the repository-load paths it never validates them after `fetch`. Navigation while Follow is active can apply an old-scope payload to the current pane; a group switch makes the final render and post-action refresh operate on the pane that now occupies the slot.

Impact: an unrelated terminal can have mouse reporting disabled, or an unrelated/current-scope explorer can be repainted/refreshed from a stale action. Existing Node tests keep the slot and scope stable, so neither replacement race is covered.

Recommendation: capture pane/session/scope identity and validate it immediately before every post-await write. For Reset view, bind the writer to the captured terminal object while still ensuring queued replay for that same pane is flushed.

**Validation — confirmed, both bullets, with one refinement.**

`terminalModeResetWriter(index)` does resolve `terminals[index]` inside the returned closure (`terminals.js:4690`), and that closure is invoked only at the ack or the 1500 ms fallback (`terminal-modes.js:62`). The contrast drawn with the repository-load path is exact: `loadExplorerGitRepo()` re-checks all three of `terminals[index] !== pane`, `sessionIds[index] !== sessionId` and `explorerGitScopePath(pane) !== scopePath` after its `await` (`explorer-git-sidebar.js:934-937`), and `performExplorerGitAction()` checks none of them.

Refinement to the second bullet: the payload writes in `performExplorerGitAction()` go to the *captured* `pane` object, so those land on the pane that issued the action. The wrong-pane exposure is through the index-based calls that follow — `renderExplorerGitPanels(index)`, `loadExplorerPane(index, …)` and `refreshExplorerAfterGitAction(index, …)`, which re-reads `terminals[index]` at `explorer-git-sidebar.js:1109`. The stale-**scope** half applies unconditionally to the captured pane, exactly as described.

### M-3 — RuntimeConfig snapshot adoption is incomplete

Locations: [`web/config.py`](../web/config.py#L258), [`web/terminal_io.py`](../web/terminal_io.py#L1359), [`web/api.py`](../web/api.py#L2689), [`web/api.py`](../web/api.py#L3646), [`web/workspaces.py`](../web/workspaces.py#L727)

The branch correctly publishes one `RuntimeConfigState` reference and converts important multi-field payload builders to `snapshot()`. Several multi-read operations still access the singleton field by field:

- SSH connection timeout and keepalive interval are read from separate generations.
- Split/launch capacity checks and their refusal messages read `max_sessions` repeatedly.
- Voice start reads `voice_enabled`, then later reads `voice_engine`.
- Some voice availability broadcasts read the engine more than once.

Impact: a concurrent App Settings refresh can create a combination no config generation contained, or admit/refuse work against one limit while reporting another. The concurrency tests cover `_public_app_config()` and snapshot publication, not these remaining consumers. This is a direct incomplete application of the config snapshot guardrail.

Recommendation: take one snapshot at the start of each operation that reads more than one config value (including repeated reads of the same value) and use it throughout.

**Validation — confirmed, all four bullets.**

`RuntimeConfig.__getattr__` re-reads `self.__dict__["_state"]` on every attribute access (`web/config.py:482-496`), so each read is genuinely a fresh generation lookup; the class docstring states the "a consumer that reads more than one setting calls `snapshot()` once" rule that these call sites break. Each site verified: `web/terminal_io.py:1364` and `:1370` (two `ssh_config` reads), `web/api.py:2690` and `:2693` (`max_sessions` for the check and again for the refusal message), `web/workspaces.py:727`/`731`/`735` (three reads), `web/api.py:3646` then `:3656` (`voice_enabled` then `voice_engine`), and `web/api.py:3546`/`:3547` (`voice_engine` twice in one broadcast).

Weighting note: a generation is only ever republished by an App Settings refresh, so the exposure window is narrow and the worst outcome is a refusal message quoting a different limit than the one applied. Medium is generous; the guardrail claim itself is correct.

## Low severity

### L-1 — `web/api.py` has regrown around pane-mode orchestration

Location: [`web/api.py`](../web/api.py#L2926)

The branch changes `web/api.py` by +401/-146 lines; the file is now 3,745 lines, and `change_session_mode()` alone spans about 250 lines. It performs cwd resolution, local/SFTP directory validation, repository-root selection, presentation cleanup, connection teardown, and restart orchestration inside one route.

Impact: there is no confirmed runtime failure from size alone, but this crosses the architecture guardrail against regrowing the API monolith and makes identity/rollback rules such as H-2 harder to enforce consistently.

Recommendation: keep HTTP normalization/response mapping in the route and move the terminal/explorer/browser transition transaction into an import-cycle-safe service with explicit transition state and rollback behavior.

**Validation — confirmed, figures exact.** `git diff --numstat main...HEAD` reports `401 146 web/api.py`; the file is 3,745 lines; `change_session_mode()` runs from line 2926 to 3174, 249 lines. For context, `web/workspaces.py` is 1,733 lines against its own documented ~2,200-line extraction trigger.

## Withdrawn findings

### H-1 (withdrawn) — Timed-out Git helpers can survive the shared process-tree teardown

Original locations: [`web/process_bounds.py`](../web/process_bounds.py#L34), [`web/explorer.py`](../web/explorer.py#L1191), [`web/selfupdate.py`](../web/selfupdate.py#L56)

**Claimed:** `terminate_process_tree()` calls `process.poll()` and skips the group/tree kill when the direct child has already exited, leaving a Git transport helper holding inherited stdout/stderr handles; and two tests reproduce the failure on this Windows checkout.

**Withdrawn for two independent reasons.**

*The test evidence does not reproduce.* At the reviewed commit with a clean tree, `tests/run_tests.py` was run twice end to end and reported `Ran 1983 tests … OK (skipped=9)` both times — the same test and skip counts the original report cites, so the same suite was exercised. `ExplorerGitProcessTreeTestCase.test_a_stalled_remote_returns_the_worker_thread_within_the_bound` was additionally run five times in isolation and `ApiRoutesTestCase.test_repo_git_timeout_bounds_a_remote_that_goes_quiet` three times; all passed, the former in ~2.5 s each (the 2 s bound plus its reap, i.e. `git` really does stall against the local listener here), and no temporary directory was left holding a handle.

*The mechanism is a sub-millisecond race the code declines on purpose.* The `poll()` guard is real, but both callers reach `terminate_process_tree()` immediately after a `wait`/`communicate` **timeout** (`web/explorer.py:1268`, `web/selfupdate.py:71`) — at that instant the child is by definition still running, so `poll()` returns `None` and the tree kill fires. The skip is a deliberate, documented trade: a reaped child's pid is free for reuse on POSIX, so `killpg` on it would signal an unrelated process group, and `test_an_already_reaped_child_is_not_tree_killed` pins that choice. Closing the residual window would mean adopting Windows Job Objects and, on POSIX, a supervisor — a large change against a race no run has produced.

Decision: **not actionable, dropped.** Do not re-raise it without a reproduction.

Two observations retained so they are not rediscovered as findings. The two stalled-remote tests are environment-sensitive by construction — they need `git` to actually stall against a loopback listener and `taskkill` to be resolvable on `PATH` (`shutil.which` returning `None` silently no-ops the Windows tree kill) — which is the most likely explanation for the original run's failures, and is test fragility rather than a product defect. Separately, on the **non**-timeout path of `_run_git_command` a helper that outlives a normally-exiting `git` is never tree-killed at all: the drain threads simply block for `PROCESS_REAP_TIMEOUT` and `_close_pipe_if_idle()` declines to close. Neither is being filed.

## Regression guardrail review

| Area | Result | Notes |
| --- | --- | --- |
| Same-origin, host keys, secrets | Pass | No relevant security implementation was changed; new terminal output remains room-scoped. |
| Durable JSON state | Pass | New presentation fields continue through the existing validated saved/runtime stores; no fourth store or direct durable JSON write was introduced. |
| Shared locks and identity | **Watch** | H-2's bullets are true, but three of the four describe code unchanged from `main`; only the new cwd publication was branch-scoped, and Stage 2 resolved that one. The three pre-existing bullets remain open follow-up hardening. |
| Immutable config publication | **Incomplete** | Publication is atomic, but M-3 lists multi-field readers that do not take a snapshot. |
| SSH/SFTP pool reservation | Pass | Verified: `_acquire_ssh_sftp()` reserves under the lock, opens outside it, and commit/cancel match the entry object; `_release_ssh_sftp()` matches on `entry.client is client`. |
| Bounded output/processes | **Incomplete** | Stream and process ceilings hold and the process-tree tests pass (H-1 withdrawn); M-1's truncation-as-success remains. |
| Repaint/tier/worker architecture | Pass | The new DOM-free policy modules have Node-executed behavioral coverage; no CDN dependency or busy polling was introduced. |
| Shell quoting and cwd observation | **Incomplete** at review; resolved in Stage 2 | Hooks use the intended shell-specific mechanisms and parsing stays outside `connection_lock`. A retiring pump could republish `current_directory` after a deliberate clear (H-2, bullet 4); publication is now gated on the exact current connection entry. |
| One selected Git scope | **Fail** | H-3 transports the anchor but does not apply it to the bulk action target; reproduced as out-of-scope data loss. |
| Dead code/wiring | Pass | No confirmed dead endpoint, config key, Socket.IO event, or new UI control was found; the shell-integration setting and new modules are wired and tested. |
| API/module structure | **Watch** | L-1 regrows the API orchestration surface despite substantial frontend extraction being well separated. |
| Styling/dialogs | Pass | Verified: new color use is token-derived, assets remain local, and the diff introduces no `window.prompt`/`confirm`/`alert` call or external asset host. |

## Verification performed

Original pass:

- `.venv\Scripts\python.exe tests\run_tests.py` — reported 1 failure and 1 error after 1,983 tests, both attributed to H-1. **Not reproducible; see H-1 (withdrawn).**
- `.venv\Scripts\python.exe -m ruff check .` — passed.
- `git diff --check main...HEAD` — passed.
- Local and remote Git runner truncation probes using the existing test stubs reproduced M-1.

Validation pass, same commit, tree clean apart from this file:

- `tests/run_tests.py` run twice end to end — `Ran 1983 tests … OK (skipped=9)` both times.
- `ExplorerGitProcessTreeTestCase` run 5× in isolation and `test_repo_git_timeout_bounds_a_remote_that_goes_quiet` 3× in isolation — all passed.
- `python -m ruff check .` — `All checks passed!`; `git diff --check main...HEAD` — clean.
- H-3 reproduced through the Flask test client against a real repository: a scoped sidebar listing one change, followed by a `discard-all` that reverted a file outside that scope.
- M-1 reproduced against the real local runner with a child exiting `7`: truncated → `returncode 0`, not truncated → `returncode 7`.
- M-2, M-3 and L-1 confirmed by reading the cited code paths and comparing them against `main`; line references in each `Validation` note.

No code was modified as part of either pass.
