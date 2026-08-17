# Backend Findings Validation and Staged Implementation Plan

**Review date:** 2026-08-17  
**Code revision reviewed:** `5745763b7a10`  
**Scope:** current Python/Flask-SocketIO backend, its relevant frontend consumers, tests, and the Regression Guardrails in `AGENTS.md` and `CLAUDE.md`.

> This is a point-in-time review and implementation aid, not a behavior contract. The live contracts remain the code, tests, `AGENTS.md`, `CLAUDE.md`, and the maintained user documentation. Future code and documentation must not cite this review as proof of current behavior.

## Validation outcome — 2026-08-17

This review's own findings were re-checked against the tree and Stage 0 has shipped. Every disposition below held **except one**, corrected in place throughout this document:

- **The self-update Windows cleanup gap does not reproduce and is not a defect.** `tests/test_api.py::ApiRoutesTestCase::test_repo_git_timeout_bounds_a_remote_that_goes_quiet` passes in isolation (6 consecutive runs) and in the full suite on Windows 11 / Python 3.14. `web/selfupdate.py::_run_repo_git()` already implements the complete Guardrail 4 pattern — `GIT_TERMINAL_PROMPT=0`, an owned process group, `_terminate_process_tree()`, and a second bounded reap under `SELF_UPDATE_REAP_TIMEOUT`. The `WinError 32` observed during the review was environment-specific (an antivirus or indexer holding the temporary checkout during `rmtree`), not a code defect. **The repository-wide gate was green:** 1,685 tests, 0 failures, 8 skipped. Stage 1 item 1 and the Stage 0 bullet that depended on it are struck below.
- Everything else validated. Findings 7, 8, and 11 are real and were each reproduced by a deterministic test; findings 1, 3, 5, 9, 10, and 12 are invalid as this review states, and the reasoning for each was confirmed against the code; finding 2's corrected failure mode (a spurious failure, not an SFTP leak) is exactly right.
- The code reviewed at `5745763b7a10` is byte-identical to the tree Stage 0 was written against — the only commit since (`c891a6c`) added documentation.

**Stage 0 is complete.** Its output is four registered issues in `docs/testing_issues.md` — the maintained, citable home for them — plus the tests that pin each one:

| Issue | Finding | Tests |
|---|---|---|
| ISSUE-2026-040 | 11, Git output/process bounds | `tests/test_git_process_bounds.py` (12 pinned, 6 controls) |
| ISSUE-2026-041 | 7, RuntimeConfig publication | `tests/test_backend_concurrency_contract.py` |
| ISSUE-2026-042 | 8, workspace-label claims | `tests/test_backend_concurrency_contract.py` |
| ISSUE-2026-043 | 2, pooled SSH reservation | `tests/test_backend_concurrency_contract.py` |

Read-side root confinement (finding 9) gained the defense-in-depth cover this review asked for, in `tests/test_explorer_fs.py`: it passes on the current implementation and fails (`200 != 400`) when containment is degraded to unresolved-path comparison, verified by running it that way. Per `CLAUDE.md`, the tests cite the issue IDs and never this document.

## Executive summary

The source analysis contains useful leads, but its severity and coverage conclusions do not match the current tree:

- **Valid and actionable:** RuntimeConfig is published field-by-field (finding 7), workspace-label claims are knowingly non-atomic (finding 8), and ordinary explorer Git commands have opt-in rather than universal output/process bounds (finding 11).
- **Partly valid or conditional:** the SSH pool has a narrower acquire/reaper race, but not the claimed SFTP leak (finding 2); the lifecycle grace behavior is real but intentional and needs a browser reproduction before it is changed (finding 4); re-export cleanup may be possible only after a compatibility inventory, not a blanket dead-code sweep (finding 6).
- **Not valid or already addressed:** findings 1, 3, 5, 9, 10, and 12.
- **Claimed test gaps:** none of the six gaps is real. All six areas have behavioral coverage, although several live in broad integration suites rather than same-named test files.

The highest-risk work is finding 11. It is not only an output-memory concern: the current explorer Git runner also conflicts with Guardrail 4's requirements that every Git invocation set `GIT_TERMINAL_PROMPT=0`, own a bounded process-group shutdown, and use a bounded reap. ~~Full-suite verification also exposed a related existing Windows failure in the otherwise well-tested self-update Git runner.~~ *(Withdrawn — see the validation outcome above; the self-update runner is correct and its test passes.)* Findings 7 and 8 are next because Guardrail 2 explicitly requires atomic multi-value snapshots and check-then-act operations.

The report's “before production deployment” framing should not be read as a readiness assessment. GridVibe's documented security posture is local, same-origin, single-user operation without multi-user authentication or isolation. The work below improves that product, but does not redefine its threat model.

## Finding-by-finding validation

### 1. Lock ordering violation risk — not valid as stated

`web.terminal_io._broadcast_session_status()` takes `SessionManager.lock` only long enough to fetch the session and build a plain dictionary. `Session.to_dict()` in `sessions/manager.py` copies dataclass fields and never touches `connection_lock`. The `socketio.emit()` occurs after the manager lock is released.

`web.api.handle_join_session()` fetches the session before taking `connection_lock`. Its call to `_get_buffered_terminal_output()` re-enters the same `threading.RLock`; it does not take `SessionManager.lock`. The alleged manager-lock-to-connection-lock cycle therefore does not exist.

This path is aligned with the lock ordering and emit-outside-lock portions of Guardrail 2. `join_room()` remains inside the `connection_lock` replay handoff; that is a separate lock-hold consideration, not evidence of the claimed inversion. It should only be refactored with a behavioral replay-order test, because moving it casually can create an output gap between room join and buffer snapshot.

**Disposition:** no lock-order fix for this finding.

### 2. SSH connection-pool race — partially valid, wrong failure mode

The claimed SFTP-channel leak does not occur. If the pool entry is evicted or replaced while `client.open_sftp()` runs, `_acquire_ssh_sftp()` returns the client/channel as uncounted; `_release_ssh_sftp()` always closes the SFTP channel and closes an unpooled client.

There is a narrower real race. An existing pool entry is read under `_ssh_client_pool_lock`, but its `in_use` count is incremented only after `open_sftp()` returns. An idle reaper can remove and close that transport during the open. That can cause a spurious request failure even though release still prevents a leak.

Moving `open_sftp()` under the pool lock would perform network work while holding a shared lock and is not aligned with Guardrails 2 and 3. The guardrail-aligned fix is a two-phase reservation: increment the selected entry under the lock, open the SFTP channel outside it, and roll back or evict by entry identity on failure.

**Disposition:** implement the corrected reservation fix in Stage 3.

### 3. Inconsistent explorer error handling — obsolete

The current tree already has structured `ExplorerRouteError` types and stable codes:

- missing local and remote paths return `404` with `code: "not_found"` through `ExplorerPathNotFoundError`;
- stale editor revisions return `409` with `code: "file_conflict"` and the current revision;
- overlapping writes, oversized files, filesystem conflicts, permissions, root changes, and cross-device moves have stable codes and appropriate statuses;
- the frontend branches on the codes it needs, including `file_conflict`, `save_in_progress`, and filesystem mutation codes.

Plain `ValueError` remains appropriate for requests such as an invalid Git diff mode or a path of the wrong kind. Converting every validation failure into a new public code would expand the API contract without a demonstrated client consumer, contrary to Guardrail 5.

**Disposition:** no blanket error-class conversion.

### 4. Stale window records blocking saves — behavior confirmed, defect not established

`LIFECYCLE_STALE_WINDOW_GRACE_SECONDS` is 120 seconds. A freshly disconnected registered window intentionally returns `client_stale`; a window disconnected past the grace period is removed. A window that drops during a flush resolves immediately as `client_stale`, while a deliberate `leave_workspace` is forgotten rather than reported. These cases are covered in `tests/test_lifecycle.py` and are part of the maintained lifecycle contract.

The mobile-Safari premise was not supported by a reproduction, trace, or failing test. Reducing the grace period trades one failure mode for another: a temporarily disconnected window could be omitted from a save just before it reconnects with newer presentation state. That would weaken the lifecycle transaction rather than clearly align it with a guardrail.

If Safari reliably drops the Socket.IO `pagehide` emit, the safer direction is an unload-appropriate, same-origin signal keyed to the stable window id, while retaining the crash/reconnect grace. That work is conditional on reproduction.

**Disposition:** Stage 4 only if the browser failure is reproduced.

### 5. Unbounded terminal output-buffer growth — not valid as stated

Each replay buffer is capped by `TERMINAL_OUTPUT_BUFFER_MAX_CHARS`. More importantly, cleanup is not join-only:

- `_finalize_stream()` calls `_close_ssh_connection()`;
- SSH and local-shell startup failures call `_close_ssh_connection()`;
- reconnect, mode switch, pane close, group close, workspace close, and global shutdown clear buffers;
- `_close_ssh_connection()` removes the buffer by default.

Existing tests cover clearing, preservation only when explicitly requested, exact character trimming, close behavior, and stream finalization. A failed connection before registry insertion has no replay buffer to leak.

**Disposition:** no finalizer/connection-close change.

### 6. Dead re-exports — not valid as a blanket cleanup

`web/api.py` intentionally delegates to canonical modules and re-exports selected objects for backward compatibility. This is stated in both repository instruction files and is pinned by extraction/identity tests. Several examples named as “likely unused” are used directly by routes, canonical-module code, or tests; others remain compatibility exports even when `web/api.py` does not call them.

Guardrail 5 does require removing genuinely dead surfaces, but a tool such as vulture cannot distinguish an intentional compatibility export from dead code. Any cleanup needs an explicit export inventory, known consumer search, and a deprecation/removal decision. It must edit the canonical module when behavior changes and preserve the API facade until compatibility is deliberately dropped.

**Disposition:** optional compatibility audit after the correctness work; no bulk removal.

### 7. RuntimeConfig mutation race — valid with corrected scope

The report's autosave example is incorrect: production refreshes are driven by app-config persistence, and that route already serializes load/merge/save/refresh with `_config_lock`. Disk state is protected by `web/state_files.py`.

The remaining issue is publication. `RuntimeConfig.refresh()` assigns `app_config`, section dictionaries, and many derived attributes sequentially. Readers do not acquire `_config_lock`, and multi-field consumers such as app-config payloads and broadcasts read several attributes independently. A concurrent request can therefore observe a mixture of old and new settings. A lock added only around `refresh()` would not fix readers that do not take it.

This is directly within Guardrail 2's atomic multi-value snapshot rule. The fix should normalize into a complete immutable state off-lock, publish that state in one atomic reference swap, and let multi-field consumers read one captured state object. Existing direct-attribute access and test patching need a compatibility strategy.

**Disposition:** implement in Stage 2A.

### 8. Workspace-label conflict check — valid and guardrail-relevant

`workspace_label_conflict()` explicitly documents that it is a check, not a mutex. The create and rename routes, and the launch-into-new path, check availability and then call separately locked manager mutations. Two concurrent requests can both pass and create duplicate non-empty labels.

That local/single-user tradeoff conflicts with the stronger current contracts: `AGENTS.md` and `CLAUDE.md` say a non-empty label identifies at most one live or saved workspace, and Guardrail 2 requires check-then-act under one lock hold.

The repair must not hold `SessionManager.lock` while reading a durable file. It needs a workspace service operation that snapshots saved-label conflicts outside the manager lock, then atomically rechecks the live namespace and performs the live create/rename under the manager lock. All mutating entry points must use that owner. Concurrency tests must cover create/create, create/rename, rename/rename, and launch-into-new; empty labels remain unconstrained.

Cross-process saved-slot races need an explicit policy. If uniqueness is required across two concurrently running GridVibe processes, the claim must also participate in the runtime-state sidecar transaction without violating lock ordering. If the contract is process-local for live state, that boundary should be stated rather than implied.

**Disposition:** implement in Stage 2B after the lock/transaction design is fixed in tests.

### 9. Symlink escape — invalid; the reasoning is reversed

Local explorer candidates are canonicalized with `realpath()` before containment is checked. For a link under the root that targets outside it, `commonpath([root, resolved_target])` is not the root, and the equality check rejects the path. A common path of `/` does not pass; it fails the equality check.

Checking only unresolved paths, as recommended in the source analysis, would permit the escape. The mutation layer also uses `lstat`/entry-kind policy and has symlink escape coverage. Search and find explicitly avoid descending through symlinked directories.

A direct read-route test for an in-root symlink targeting an out-of-root file would be worthwhile as defense-in-depth, but the implementation should not be changed to unresolved containment.

**Disposition:** preserve the implementation; add a focused regression test in Stage 0.

### 10. Repeated JSON parsing — invalid

The reviewed environment uses Flask 3.1.3. `Request.get_json()` defaults to `cache=True`; `silent=True` selects silent error handling but does not bypass the cache. `update_workspace_presentation()` calls it once in any case.

Changing to non-silent `get_json()` is not a performance fix: it changes malformed or non-JSON request behavior to Flask's exception response (including `415` for the wrong media type). Accessing the private `_cached_json` attribute would create unnecessary framework coupling.

**Disposition:** no change.

### 11. Missing Git output and process bounds — valid, broader than reported

`_run_git_command()` accepts an optional `max_output_bytes`, but ordinary status, graph, commit-file, diff, and mutation paths omit it. Explorer search opts into the cap; `_bounded_git_diff()` currently slices only after the complete output has already been captured. The bounded local path caps stdout but still accumulates stderr without a limit. The remote runner uses complete `stdout.read()` and `stderr.read()` calls unless a caller adds a shell-side stdout `head` pipeline.

There are also direct Guardrail 4 conflicts:

- read-only Git commands set `GIT_OPTIONAL_LOCKS=0` but not `GIT_TERMINAL_PROMPT=0`;
- the unbounded branch uses `subprocess.run(timeout=...)`;
- the bounded branch kills only the direct process, performs unbounded `wait()`/thread joins, and does not own/terminate a process group;
- remote stderr is unbounded and the shell pipeline does not provide a complete local-style truncation/exit-status contract.

This work must preserve the shared local/remote explorer backend abstraction (Guardrail 6) and the existing bounded-search and diff semantics. No frontend Diff change is required; if implementation does touch the Diff view, the `explorer-diff.js` extraction trigger in Guardrail 6 applies.

**Disposition:** highest priority, Stage 1.

### 12. Duplicate normalization logic — invalid

The three claimed duplicate definition sets do not exist. `_normalize_connection_mode()`, `_normalize_layout()`, and `_normalize_startup_mode()` are defined in `web/saved_sessions.py`. `web/runtime_state.py` imports the applicable launch-shape normalizers inside `_validate_group()` rather than redefining them. `web/session_presentation.py` owns different, strict presentation-field normalizers and is already the canonical owner used by runtime-state pane validation.

Creating `web/normalizers.py` would not consolidate the three definition sets claimed in the report, because those sets do not exist. Any future move would need its own import-cycle and compatibility analysis, and could blur the deliberate distinction between launch-shape normalization and strict presentation validation.

**Disposition:** no extraction.

## Test-coverage claims

| Claimed gap | Current behavioral coverage | Verdict |
|---|---|---|
| `web/hostkeys.py` | `KnownHostsPersistenceTestCase` and `HostKeyPolicyTestCase` in `tests/test_api.py`, including all three policies and shared callers | Covered |
| `web/secrets.py` | `tests/test_saved_session_store.py` covers exclusive concurrent key creation, valid Fernet keys, non-clobbering, plaintext exclusion, and password round-trip | Covered |
| `web/state_files.py` | `tests/test_saved_session_store.py`, `tests/test_multi_workspace.py`, and `ConfigDurabilityTestCase` cover sidecar exclusion, atomic replace, backup, quarantine/recovery, and failure propagation | Covered |
| `web/selfupdate.py` | `tests/test_api.py` covers clean/current, fast-forward, dirty refusal, real checkout update, prompt suppression, timeouts, process-tree termination, and bounded reap. The real stalled-remote Windows test passes; the cleanup failure reported during this review did not reproduce. | Covered |
| faster-whisper path | `tests/test_api.py` covers availability, model selection, buffering, transcription flow, disconnect cleanup, recording caps, and engine dispatch | Covered |
| explorer cross-device move | `tests/test_explorer_fs.py::test_move_cross_device_is_stable_and_retry_safe` checks `EXDEV`, stable `cross_device_move`, no mutation, and retry safety | Covered |

Test organization by behavior is intentional; a module does not need a same-named test file to be covered.

## Guardrail alignment summary

| Work item | Guardrails | Assessment |
|---|---|---|
| Explorer Git runner | 3, 4, 6 | Current output/process/prompt handling needs hardening; highest priority |
| Self-update Git timeout cleanup | 4 | Already correct: process group, tree kill, and bounded reap. Withdrawn as a work item; it is the reference pattern the explorer runner should reuse |
| RuntimeConfig publication | 2, 10 | Durable writes are correct; in-memory multi-field publication is not atomic |
| Workspace-label claim | 2 | Current check-then-act is knowingly non-atomic and conflicts with the uniqueness contract |
| SSH pool reservation | 2, 3 | Pooling is correct, but reservation must precede slow channel open without holding the lock during I/O |
| Lifecycle stale grace | 2 and lifecycle contract | Current behavior is bounded and tested; changing the duration alone is not justified |
| Re-export cleanup | 5, 6 | Compatibility exports are documented consumers, not automatically dead code |
| Explorer root confinement | 1 | Current resolved-path containment is correct and should be preserved |

## Staged implementation plan

### Stage 0 — pin the corrected problem statements — **complete**

1. Add focused failing tests for the verified issues before changing behavior:
   - local and remote Git stdout/stderr caps, prompt suppression, timeout, process-group termination, and bounded reap;
   - ~~the existing real stalled-remote self-update test releases the checkout before `_run_repo_git()` returns on Windows~~ *(withdrawn — it already does; the test passes)*;
   - RuntimeConfig readers never see a mixed generation;
   - concurrent non-empty workspace-label claims produce one winner and one actionable `409`;
   - an SFTP client selected for channel open cannot be reaped until that open is committed or rolled back.
2. Add the defense-in-depth read-route symlink escape test. It should pass on the current implementation and fail if containment is changed to unresolved-path checking.
3. Keep the existing error-code, lifecycle, buffer-finalization, re-export identity, normalization, and JSON behavior tests unchanged; they are evidence against regressions disguised as fixes.

**Exit criteria:** each real issue has a deterministic failing test; the symlink test passes without production changes.

**Met.** 17 pinned tests carry `@unittest.expectedFailure`, each verified to fail for its intended reason rather than incidentally; 11 undecorated controls pin behaviour the fixes must preserve. Removing a decorator is part of the fix that clears it — an unexpected success fails the run, so a stale decorator is loud. The suite is 1,716 tests, green, with `ruff` clean. Two notes on making the pins genuinely deterministic:

- The stalled-remote case reproduces only against an `https://` remote. Git speaks `git://` in-process, so that URL kills the direct child and proves nothing; `https://` forks `git-remote-https`, which is the helper that inherits our pipes. The call is made on a worker thread with a bounded join so an unbounded runner fails in 8 s instead of parking the suite for minutes, and socket cleanup is ordered to run before the checkout is removed so a failing run leaves no git process behind.
- Symlink creation needs a privilege an ordinary Windows account lacks, which would have silently skipped read-side containment on GridVibe's most common platform. The directory-escape tests use a junction where a symlink is refused — no privilege needed, and `realpath` resolves it identically — so the escape is asserted rather than skipped. The file-symlink variant still skips there, which is honest: a junction is directories only.

### Stage 1 — harden Git process execution — **complete**

1. ~~First close the self-update Windows cleanup gap found by verification.~~ *(Withdrawn — `_run_repo_git()` already bounds termination, pipe closure, and reap correctly. Reuse it as the reference pattern instead of changing it.)*
2. Make explorer output limits a backend invariant rather than an opt-in caller feature. Define a safe global stdout/stderr ceiling and retain smaller per-operation limits where semantics require them.
3. Set `GIT_TERMINAL_PROMPT=0` for every local and remote Git command. Read-only commands should additionally retain `GIT_OPTIONAL_LOCKS=0`.
4. Replace the explorer's local `subprocess.run()` and direct-process kill paths with an owned `Popen` process group, concurrent bounded draining of both streams, group termination on timeout/truncation, and a second bounded reap. Reuse the corrected self-update process-tree pattern where its semantics fit instead of creating a weaker variant.
5. Replace remote whole-stream reads with a deadline-aware bounded channel drain for stdout and stderr. Closing or timing out the channel must not leave a pooled transport counted forever.
6. Preserve `stdout_truncated`, Git exit status, UTF-8 replacement behavior, search truncation reporting, and the existing public response shapes.

**Exit criteria:** huge stdout, huge stderr, a stalled explorer Git process with a surviving helper, and an SSH channel that never finishes all terminate within test bounds; normal local/remote status, graph, diff, search, mutation, and self-update tests still pass; every `expectedFailure` in `tests/test_git_process_bounds.py` is removed in the same change that makes it pass.

**Met.** All twelve decorators came off in the same change; `tests/test_git_process_bounds.py` is 21 tests, none decorated. The gate is 1,719 tests, green, 9 skipped, 5 expected failures — the five that remain belong to Stages 2A/2B/3 in `tests/test_backend_concurrency_contract.py`. `ruff` clean. The shipped shape is recorded in `docs/testing_issues.md` under ISSUE-2026-040 (now closed); three decisions are worth carrying forward:

- **The self-update pattern was moved, not copied.** `web/process_bounds.py` now owns `new_process_group()` and `terminate_process_tree()` and both runners import it, which is what item 4 meant by "reuse … instead of creating a weaker variant". The move added one rule: a child `poll()` reports as already reaped is not tree-killed, because a dead parent's pid cannot name its orphans and on POSIX is free to name a stranger.
- **The remote `| head -c N` pipeline had to go, not grow.** Item 5's bounded drain replaces it rather than joining it: a pipeline reports *head's* exit status, so bounding the remote diff that way would have turned a failed remote command into an empty successful one. That is also why the Diff view could never opt into a cap before. `_remote_git_shell_command()` no longer takes `max_output_bytes`; the drain carries the whole bound.
- **The diff's `byte_count` is now the bytes read, not the bytes Git would have produced.** `_bounded_git_diff()` asks for `EXPLORER_GIT_DIFF_MAX_BYTES + 1`, which drops its peak from the repository's diff size to 256 KiB; `truncated` distinguishes the two, and no frontend reads the field. Everything else item 6 lists — `stdout_truncated`, the exit status, UTF-8 replacement, search truncation reporting, response shapes — is unchanged, and no Diff-view frontend change was needed, so the `explorer-diff.js` trigger did not fire.

### Stage 2A — publish RuntimeConfig atomically

1. Normalize the complete next runtime configuration into an immutable state object or equivalent private snapshot without mutating the published state.
2. Publish the completed generation with one reference swap.
3. Make every multi-field response/broadcast capture one generation before building its payload. A lock only around `refresh()` is insufficient.
4. Preserve existing direct attribute reads and the test suite's scoped attribute patching, or migrate those patches in the same change.
5. Keep persistence under `_config_lock` and `web/state_files.py`; do not introduce a second durable path.

**Exit criteria:** a barrier-driven refresh/read test cannot produce a mixed payload; config durability and App Settings tests remain green.

### Stage 2B — make workspace-label claims atomic

1. Introduce one workspace-service owner for “check live namespace and create/rename” under `SessionManager.lock`.
2. Snapshot saved-slot conflicts without holding the manager lock; do not perform durable-file I/O inside a shared manager lock.
3. Route direct create, rename, and launch-into-new through the atomic owner. Keep the validation endpoint advisory and recheck at commit.
4. Decide and test the cross-process boundary for saved-slot collisions. If cross-process uniqueness is required, integrate the claim with the runtime-state sidecar transaction and document the lock order before implementation.
5. Preserve actionable `409` payloads, case/whitespace-insensitive comparison, self-exclusion on rename, and unconstrained empty labels.

**Exit criteria:** deterministic concurrent tests allow exactly one conflicting non-empty claim while empty-label concurrency remains allowed; no manager lock is held across file I/O or Socket.IO work.

### Stage 3 — reserve pooled SSH clients before channel open

1. Under `_ssh_client_pool_lock`, select the exact entry and increment its reservation count before releasing the lock.
2. Run `open_sftp()` outside the lock.
3. On success, commit last-used metadata by entry identity; on failure, release the reservation or evict only the matching failed client.
4. Keep release idempotent with replacement/loser paths and close every SFTP channel exactly once.

**Exit criteria:** a barrier-controlled open/reaper race cannot close the selected transport mid-open; existing reuse, idle-reap, concurrent-holder, loser, and teardown tests remain green.

### Stage 4 — conditional lifecycle improvement

Do not change the 120-second grace period without a reproducible Safari case.

If reproduced:

1. Capture browser/version, navigation type, Socket.IO state, whether `pagehide` ran, and whether the server received `leave_workspace`.
2. Prefer a same-origin unload signal suitable for page teardown, addressed to the stable lifecycle window id, so a deliberate departure can be forgotten immediately.
3. Preserve stale treatment for crashes, network loss, suspension, and genuine reconnects; do not authorize save from stale server presentation.
4. Add browser-independent client tests plus coordinator tests for delivered beacon, missing beacon, reload/rejoin, loss during flush, and multi-window workspaces.

**Exit criteria:** the reproduced deliberate-close case no longer blocks, while an unannounced disconnect still produces `client_stale` until grace expires.

### Stage 5 — optional compatibility cleanup

After correctness work, inventory every `web/api.py` re-export by internal use, test/patch use, documented external compatibility, and true absence. Remove only individually proven dead names, with an explicit compatibility decision. Do not combine this with the Git or concurrency changes, and do not create a generic normalizers module as part of the sweep.

## Verification and delivery

Each implementation stage should run its focused behavioral tests first, followed by the repository-wide checks:

```text
python tests/run_tests.py
python -m ruff check .
```

On systems with `make`, the final equivalent is `make check`. Any user-visible behavior or contract change should update the appropriate maintained documentation and `CHANGELOG.md`; this point-in-time review itself must not become the cited contract.

For this validation pass, 156 focused tests covering lifecycle, saved-session durability/secrets, explorer filesystem mutations, SSH/SFTP pooling, output-buffer cleanup, status-broadcast locking, RuntimeConfig extraction, module re-exports, host-key policy, and config durability completed successfully (4 skipped for unavailable platform capabilities). Ruff completed successfully.

The full runner executed 1,685 tests and reported one error (8 skipped): `ApiRoutesTestCase.test_repo_git_timeout_bounds_a_remote_that_goes_quiet` raised `WinError 32` while immediately deleting its temporary checkout after the timeout path.

**Re-verified 2026-08-17 and withdrawn.** That test passes in isolation (6 consecutive runs) and in the full suite on the same Windows 11 / Python 3.14 environment, both before and after Stage 0. The gate was green at the reviewed revision: 1,685 tests, 0 failures, 8 skipped, `ruff` clean. `WinError 32` on an immediate `rmtree` of a just-released checkout is an environment artifact — an antivirus or search indexer holding the directory — and not evidence about `_run_repo_git()`, which already owns a process group, kills the tree, and reaps under a second bound. After Stage 0 the gate is 1,716 tests, green, 9 skipped, 17 expected failures.
