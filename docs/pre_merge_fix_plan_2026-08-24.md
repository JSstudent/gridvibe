# Pre-merge three-stage fix plan

Date: 2026-08-24

Updated after Stage 2 implementation, automated acceptance, and manual acceptance on 2026-08-24

Scope: H-3, the branch-scoped part of H-2, plus M-1 through M-3 and L-1

This is a point-in-time implementation plan, not a behavior contract. The code, tests, `AGENTS.md`, `CLAUDE.md`, and other maintained documents remain authoritative after a fix lands.

## Validated finding disposition and current status

The validation pass changes what belongs in a pre-merge fix:

| Finding | Disposition in this plan | Consequence |
| --- | --- | --- |
| H-1 | Withdrawn and excluded | Make no process-tree, Job Object, timeout, or process-group change. The existing stalled-remote tests are green and the documented dead-PID safety trade remains intact. |
| H-2 | Resolved in Stage 2 (re-scoped part only) | A cwd observation now publishes only while the exact connection that produced it is still the registry's entry. The older pump/finalizer/registration issues remain separate hardening work and are not merge blockers. |
| H-3 | Resolved in Stage 1 | Bulk mutations now obey the selected Git scope, and narrowed Commit refuses hidden staged paths. Automated and manual acceptance passed. |
| M-1 | Resolved in Stage 1 | Bounded runners now distinguish completion from truncation; structural reads and mutations reject incomplete results, while search/diff retain explicit partial-result contracts. |
| M-2 | Resolved in Stage 2 | Every post-`await` write in Reset view and in the explorer Git actions names the pane, session and scope it captured; a stale completion is discarded and followed by a fresh load. |
| M-3, L-1 | Confirmed | Retain as scoped correctness/architecture work in descending change-risk order. |

Do not reintroduce H-1 into this plan without a new reliable reproduction. Do not describe H-2 bullets 1–3 as regressions introduced by this branch.

## Objective and sequencing

Resolve the actionable branch concerns in three stages ordered by **implementation and regression risk**, from highest to lowest. Severity and change risk are separate: H-3 is the only validated merge blocker, while M-1 joins it in Stage 1 because changing the shared Git result model affects every explorer Git read and mutation.

| Stage | Status | Change risk | Findings | Why it is ordered here |
| --- | --- | --- | --- | --- |
| 1 | **Complete — accepted 2026-08-24** | Highest | H-3, M-1 | Changes destructive Git mutation scope and the completion semantics consumed by every local/SSH Git caller. |
| 2 | **Complete — accepted 2026-08-24** | Moderate | H-2, branch-scoped symptom only; M-2 | Adds identity checks at backend/frontend asynchronous publication boundaries without redesigning connection registration. |
| 3 | Pending | Lowest when constrained as described | M-3, L-1 | Snapshot adoption is mechanical. The API extraction is characterization-first and behavior-neutral; redesign is excluded. |

Each stage is independently reviewable and must leave its focused tests green before the next stage starts. The validated baseline is already green: two consecutive full runs reported 1,983 tests passing with 9 skipped. A fix must preserve that baseline.

## Safety constraints for every stage

- Preserve route URLs, Socket.IO event names, response shapes, saved/runtime presentation schemas, credential handling, same-origin behavior, SSH host-key policy, and room-scoped terminal output.
- Keep parsing, network work, process waits, `socketio.emit`, and other blocking work outside shared locks. Preserve the lock order `connection_lock` then `SessionManager.lock`, never the reverse.
- Add a failing behavioral regression test before or with each correction. Do not replace a real integration failure with a source-text assertion.
- Preserve output memory ceilings and bounded waits. Correctness must be restored without making a runner unbounded.
- Preserve local and SFTP behavior through the shared explorer backend rather than adding local-only route branches.
- Do not mix opportunistic cleanup, dependency upgrades, schema changes, generic abstractions, or UI redesign into these fixes.
- Stop after a stage if its focused tests or manual acceptance scenarios fail.

---

## Stage 1 — Scoped Git mutations and trustworthy bounded results — Complete

Risk: highest

Findings: H-3 and M-1

### Completion record — 2026-08-24

Status: **implemented, automatically accepted, manually accepted, and documented.** No Stage 2 or Stage 3 work was included.

Delivered code:

- `web/explorer.py` now resolves one selected scope pathspec for bulk Git actions. Stage All, both Unstage All forms, and Discard All use it; Discard All restores only the tracked, non-conflicted worktree paths returned by its complete scoped status read.
- Narrowed Commit compares complete repository-wide and scoped staged-path sets, including cross-scope rename endpoints through `--no-renames`, and refuses without committing when a staged path is hidden. Repository-root Commit is unchanged, and Publish remains branch-wide.
- Local and SSH Git results now carry independent completion, exit-status-observed, stdout-truncated, stderr-truncated, and output-limit-terminated facts. Output limiting never synthesizes success.
- One centralized completeness gate protects structural reads and every mutation. Incomplete mutations report possible repository change, require a refresh, and are not retried. `web/explorer_search.py` and bounded diff opt into the only explicit stdout-partial paths.
- `web/static/js/explorer-git-sidebar.js` now confirms that Discard All affects tracked files in the current Git scope while preserving staged content and untracked files.

Automated acceptance:

- Real repository tests cover sibling-scope byte/index isolation for Stage All, Unstage All, and Discard All; root and narrowed scope; unborn `HEAD`; deletes and renames; staged-plus-unstaged content; untracked and conflicted files; unusual NUL-delimited names; and narrowed/root Commit behavior.
- Local and SSH runner tests cover stdout and stderr ceilings, missing completion status, normal success and non-zero exit, incomplete structural reads and mutations, and the explicit partial search/diff contracts.
- Focused explorer Git, API, search, and process-bound suites passed, as did Ruff and `git diff --check`.
- The full runner reached 2,004 tests with 9 skipped. Its only two environment-sensitive failures were the withdrawn H-1 process-tree cases, which are explicitly excluded from this plan and received no code change.

Manual acceptance:

- The user repeated the disposable narrowed-scope and repository-root scenarios, verified the modal and ordinary below-limit behavior, and accepted Stage 1 on 2026-08-24.

Documentation completed:

- The selected-Git-scope and bounded-output guardrails were updated in `AGENTS.md` and `CLAUDE.md`.
- `CHANGELOG.md` records both user-visible fixes.
- `docs/pre_merge_code_review_2026-08-24.md` records the H-3/M-1 remediation and acceptance without rewriting its original point-in-time findings.

### Problem

#### H-3: narrowed Git controls mutate hidden paths

Pin/Follow applies a pathspec to status, graph, and commit-file reads, so the sidebar can show only one subdirectory. Stage All, Unstage All, Discard All, and Commit still act at repository scope. The mismatch was reproduced end to end: a scoped sidebar showed only an in-scope edit, then Discard All restored both that edit and an out-of-scope edit the user never saw.

The confirmation text compounds the problem. Its wording sounds repository-wide, while button availability is derived from the narrowed list. The safe invariant is: **a mutation presented from a selected Git scope must not mutate or commit a path hidden outside that scope**.

#### M-1: a bounded but incomplete Git command is synthesized as success

The runner's `returncode=0 if truncated` behavior predates this branch on the opt-in search path. This branch makes every Git invocation bounded, extending that deliberate behavior to status, graph, commit-file, diff, and all mutations. Most callers do not inspect `stdout_truncated`, so incomplete structural reads and killed mutations can now be accepted as complete success. Stderr-triggered termination also lacks an explicit incomplete-result signal.

The safe invariant is: **output completeness and process exit status are separate facts; incomplete execution is never implicit success**.

### Pre-fix verification

#### Manual Git scope scenario

Use only a disposable repository with committed `inscope/a.txt` and `outscope/b.txt`:

1. Modify both tracked files and add one untracked file under each directory.
2. Open the repository in an explorer pane and enable Pin or Follow on `inscope/`.
3. Confirm the sidebar lists only the in-scope changes.
4. Capture file hashes plus `git status --porcelain=v2 -z`, `git diff`, and `git diff --cached`.
5. Run Stage All; restore the fixture; run Unstage All; restore it again; then run Discard All after confirmation.
6. Compare the out-of-scope file bytes and index entries before and after every action.
7. Stage one file in each directory, remain scoped to `inscope/`, and attempt Commit.

Expected pre-fix evidence: bulk actions can change hidden index/worktree state, and Discard All can restore `outscope/b.txt` even though the sidebar omitted it. Never use valuable changes for this reproduction.

M-1 has no practical UI-only reproduction without generating excessive Git output. Use the existing bounded-runner stubs or a test child that writes beyond a temporarily small ceiling and exits non-zero. Record the runner's completion metadata, returned code, and caller outcome.

#### Automated reproduction

- Preserve the real Flask/repository H-3 reproduction as a regression test: one request scoped to `inscope/`, with byte-for-byte and index assertions on `outscope/`.
- Extend Stage All and Unstage All tests with the same sibling-scope fixture; the existing mock-only anchor transport test is not sufficient.
- Extend local and SSH runner tests so stdout-limit and stderr-limit termination are distinguishable from normal success/non-zero exit.
- Characterize which existing callers intentionally support partial data. Search and bounded diff may preserve an explicit truncation response; structural repository models and mutations may not.

### Code changes

#### 1. Apply the selected pathspec to every working-tree/index bulk action

Files: `web/explorer.py`, the Git routes in `web/api.py`, `web/static/js/explorer-git-sidebar.js`, and real Git route tests.

- Resolve `repo_root` and the validated selected anchor once, then compute `scope_pathspec = backend.pathspec(repo_root, current_path)` once per helper.
- Stage All: run `git add --all -- <scope_pathspec>`.
- Unstage All:
  - with `HEAD`, run `git reset --quiet HEAD -- <scope_pathspec>`;
  - without `HEAD`, run `git rm --cached -r --quiet -- <scope_pathspec>`.
- Discard All: scope the porcelain status read with `-- <scope_pathspec>`, parse only tracked/non-conflicted worktree changes from that result, and pass only those repo-relative paths to `git restore --worktree -- ...`. Keep staged content and every untracked file untouched.
- Before Commit in a narrowed scope, compare the complete staged-path set with staged paths inside the selected pathspec. If any staged path is outside the scope, refuse without mutation and return an actionable message telling the user to switch scope or unstage those paths. If none are outside, keep the normal commit command. Do not use `git commit -- <pathspec>` because its index/worktree semantics differ from a normal staged commit.
- Keep Publish branch-wide: it pushes existing commits and does not mutate hidden worktree/index paths. Document that narrow exception in the helper.
- Change Discard All confirmation copy to say “tracked files in the current Git scope” and retain the promises that staged content and untracked files are preserved.
- Preserve root confinement and shared local/SFTP path normalization.

Required regression coverage:

- Stage/Unstage/Discard All change the selected scope as intended while the sibling scope's bytes and index entries remain identical.
- Cover root scope, Pin/Follow scope, unborn `HEAD`, deletes, renames, staged-plus-unstaged content, untracked files, conflicts, and unusual filenames already protected by NUL parsing.
- Commit refuses hidden staged paths and succeeds when all staged paths are inside the selected scope.
- The backend receives one validated pathspec in local and remote forms.

#### 2. Represent bounded command completion independently

Files: the local and remote runners and their callers in `web/explorer.py`, plus `web/explorer_search.py` for its existing explicitly partial search response.

- Give both runners equivalent completion metadata, including stdout truncation, stderr truncation, and whether output limiting terminated/closed the command. Update the runner docstring that currently defines truncation as success.
- Preserve an exit status only when it was observed normally. Callers must check completion before interpreting status or parsing output; do not synthesize `returncode=0` for incomplete execution.
- Allow partial output only where the endpoint already has a meaningful explicit partial-result contract, such as bounded search/diff. Preserve their truncation flags.
- Reject incomplete status, graph, commit-file, repository-state, and other structural reads. Partial data must not replace a complete sidebar model.
- Reject every incomplete mutation even if it may already have changed repository state. Return an explicit bounded-output error, request a fresh state read, and never auto-retry the mutation.
- Apply the same policy to stderr-limit termination and SSH channel closure. Keep both streams bounded.
- Centralize the “require complete result” check so mutation helpers cannot omit it.

Required regression coverage:

- Local and SSH results agree for stdout limit, stderr limit, timeout, normal non-zero exit, and normal success.
- Every mutation rejects incomplete execution.
- Structural reads reject incomplete data, while search/diff return only their explicitly marked partial form.
- Existing memory tests continue proving the runners stop reading at the ceiling rather than slice after buffering.

### Post-fix verification

#### Automated acceptance

- The real sibling-directory tests prove that Stage/Unstage/Discard All never change out-of-scope bytes or index entries.
- Narrowed Commit refuses hidden staged paths; root-scoped Commit preserves current behavior.
- Local and SSH output-limit tests prove incomplete results cannot become successful mutations or complete repository models.
- All existing explorer Git, search, process-bound, API, and SFTP backend tests remain green.
- Run the full suite and Ruff before starting Stage 2.

#### Manual acceptance

- Repeat the disposable `inscope/` versus `outscope/` scenario. Only `inscope/` changes; Discard All leaves `outscope/` byte-identical; narrowed Commit refuses hidden staged entries.
- Repeat from repository-root scope. Existing repository-wide Stage/Unstage/Discard/Commit behavior remains available when the whole repository is the selected scope.
- Confirm the Discard All modal accurately names the current scope and still states that staged content and untracked files are preserved.
- Exercise ordinary status, graph, commit-file, diff, search, and all mutations below their output limits to confirm unchanged successful behavior.

### Documentation updates

- Update the one-selected-Git-scope guardrail in `AGENTS.md` and `CLAUDE.md` with the exact bulk pathspec rule and narrowed-scope Commit refusal. Keep Publish documented as branch-wide.
- Update the bounded-output guardrail in both files: an output ceiling creates an incomplete result, never synthetic success; only endpoints with explicit partial-result contracts may consume it.
- Add `CHANGELOG.md` entries for preventing hidden out-of-scope Git mutations and reporting output-limited commands truthfully.
- Update the confirmation/help text where “all” previously implied the whole repository. No general Git UI redesign or `README.md` rewrite is expected.

---

## Stage 2 — Identity-safe cwd and frontend async completion — Complete

Risk: moderate

Findings: H-2 branch-scoped cwd symptom and M-2

### Completion record — 2026-08-24

Status: **implemented, automatically accepted, manually accepted, and documented.** No Stage 3 work was included.

Delivered code:

- `web/terminal_io.py` gained `_publish_observed_cwd()`. Parsing and residue handling remain lock-free; the current-entry check (`ssh_connections.get(session_id) is connection`), the directory comparison and the metadata write are one `connection_lock` → `SessionManager.lock` hold, and `_broadcast_session_status()` runs after both are released. A discarded event changes nothing — no status, no replay, no write to the replacement's metadata. No attempt tokens, registration changes, pump/finalizer rewrites or shell-switch resequencing were made.
- `web/static/js/terminal-modes.js` gained `captureResetTarget()`: the write follows the captured pane object (flushing that pane's own `_pendingOutput` first) wherever it now lives, while `isCurrent()` gates slot work only.
- `web/static/js/terminals.js` captures that target in both `refreshTerminalDisplay()` and `clearTerminalDisplay()`, guards the post-rejoin redraw on `isCurrent()`, and replaces the index-resolved busy setters with `holdTerminalActionState()`, which returns a release bound to the buttons it disabled. `flushPendingOutput()` gained a captured-object twin. `restoreCachedPaneUiState()` runs the fresh Git load a stale action asked for.
- `web/static/js/explorer-git-sidebar.js` captures `{pane, sessionId, scopePath}` before every action and reports a three-valued identity state. A **replaced pane** is not painted at all and has its Git model marked stale (without blanking `_explorerGitRepo`); a **changed scope** is painted and its current scope loaded now. Busy state is always released on the captured pane, errors are never attached to a moved pane, and the already-sent mutation is never cancelled or reissued.

Automated acceptance:

- New `tests/test_explorer_git_identity.py` evaluates the real sidebar in a Node `vm` and covers delayed success and delayed failure across a group switch and a Follow-scope change, the commit draft, the closed-sidebar case, and each part of the identity in isolation — with an unchanged-behaviour control.
- `tests/test_terminal_modes.py` gained a captured-target case: the teardown follows the pane that asked after a group switch, the pane's queue is flushed ahead of it, slot work is refused, and the full replay-then-teardown ordering still holds with the switch in the middle.
- `tests/test_api.py` gained the retired-connection, replaced-connection, barrier (registry change between parse and publication) and lock-freedom cases, plus a `_register_connection` helper; the four pre-existing observation tests and one in `tests/test_multi_workspace.py` now register their connection, which is the only shape a pump ever holds.
- Pre-fix evidence: the three cwd regression tests fail against the unmodified `web/terminal_io.py`, and eight of the nine Git identity tests fail against the unmodified sidebar (the ninth is the control, which passes both ways).
- Full runner: **2,023 tests, 9 skipped, no failures**. Ruff and `git diff --check` clean.

Manual acceptance:

- The user repeated the shell-switch and both delayed-frontend scenarios and accepted Stage 2 on 2026-08-24.

Two deviations from the plan's letter, both in its direction:

- The busy release is bound to the buttons it disabled rather than skipped when stale. Skipping alone would have left the pane that asked stuck on "Refreshing…" inside its cached fragment while the replacement's own busy state was cleared for it. The same rule was applied to Clear, which awaits identically.
- A pane-replaced Git completion gets an explicit fresh load on cached-group return. `restoreCachedGroupView()` re-attaches DOM and reloads nothing on its own, so "let normal activation reload it" was not true without it.

Consequent cleanup: `setTerminalRefreshState`, `setTerminalClearState`, `setTerminalActionState` and `resetTerminalMouseReporting` were left with no callers and removed (guardrail 5), and the four legacy `assertIn("function …")` assertions that pinned their spelling were converted to contract-level checks.

Deliberately untouched: `_track_terminal_agent_input()` also writes `current_directory` from a connection. It is driven by user input rather than a pump, and this stage is scoped to the observation path; the three deferred connection-lifecycle races remain follow-up hardening.

Documentation completed:

- The cwd-observation guardrail in `AGENTS.md` and `CLAUDE.md` now states the publication rule, and explicitly states nothing about connection-attempt generations, pump adoption, or finalizer ownership.
- A new guardrail in both files covers post-`await` identity binding on the frontend.
- `CHANGELOG.md` records both user-visible fixes without claiming the older connection lifecycle is corrected.
- `docs/pre_merge_code_review_2026-08-24.md` records the H-2/M-2 remediation and acceptance without rewriting its original point-in-time findings.

### Problem

#### H-2: a retired connection can republish the cwd after this branch clears it

The general connection registry, local pump, finalizer, and registration races predate this branch. This branch adds terminal-output cwd observation and deliberately clears `current_directory` during retargeting. `_observe_terminal_output_cwd()` can parse final output from the retiring connection and write its directory back after that clear because it does not prove that its passed connection is still the registry's current entry.

This plan addresses only the new invariant: **a cwd observation may publish metadata only while the exact connection that produced it is current**.

The following pre-existing hardening remains outside this pre-merge plan:

- preventing an old local pump from adopting a replacement entry;
- making finalization/SSH error cleanup close only the entry that owns the task;
- reserving connection-attempt generations before slow open and resolving overlapping registration.

Those issues should be handled together in a separate connection-lifecycle change. This stage must not claim to solve them.

#### M-2: delayed frontend work can resolve a reused slot or stale scope

Reset view resolves `terminals[index]` only when its acknowledgement/fallback arrives, so it can write mouse teardown into the pane that later occupies that grid slot.

Explorer Git actions keep their response data on the captured pane object, but post-response render/reload/refresh calls resolve the integer index again and can act on a replacement pane. If the captured pane navigated to a different Follow scope, the old-scope payload can also overwrite its current model.

The safe invariant is: **every post-`await` write must target the captured pane/session/scope, or be discarded and followed by a normal fresh load**.

### Pre-fix verification

#### Automated reproduction

- Add a deterministic cwd test with connection A registered, pause after A's output is parsed, replace/remove A under `connection_lock`, then resume publication. A must not update session metadata or broadcast.
- Preserve a positive control: an event from the current connection updates cwd and broadcasts once.
- Add Node-executed tests that replace the pane/session at the Reset view acknowledgement boundary.
- Add Node-executed Git action tests for both delayed success and delayed failure while:
  - another group replaces the grid slot;
  - the same pane changes Follow scope.

Use barriers, injected acknowledgements/promises, and fake timers rather than sleeps.

#### Manual scenarios

For cwd publication on Windows:

1. Open a disposable Local Repo terminal in a nested directory and start output that keeps the retiring shell active.
2. Switch shell family while output is still winding down.
3. After the replacement prompt appears, wait longer than the stream receive timeout and inspect `cd`/`Get-Location` plus the pane's subsequent explorer seed/save behavior.
4. Record whether the old directory reappears after the switch cleared it.

The race is timing-dependent; the barrier test is authoritative.

For frontend identity:

- With a delayed join acknowledgement, start Reset view and immediately switch group. Observe which terminal receives the teardown.
- With a delayed Git response, change Follow directory or switch group. Observe whether the old action paints, reloads, or refreshes the current pane/scope.

### Code changes

#### 1. Gate cwd publication on exact current-entry identity

Files: `web/terminal_io.py` and focused terminal cwd/stream tests.

- Keep OSC parsing and residue handling outside `connection_lock`.
- Immediately before reading/updating session metadata, acquire `connection_lock` and require `ssh_connections.get(session_id) is connection`.
- Perform the current-directory comparison and metadata update while that current-entry check remains protected, taking `SessionManager.lock` only in the allowed `connection_lock` → manager order.
- Release all shared locks before `_broadcast_session_status()`.
- If the entry was removed or replaced, silently discard the parsed cwd event; do not change status, replay output, or the replacement's metadata.
- Do not add attempt tokens, change registration, rewrite pumps/finalizers, or alter shell-switch sequencing in this stage.

Required regression coverage:

- Current connection publishes one normalized cwd update.
- Removed/replaced connection publishes nothing, including when the registry change occurs after parsing but before metadata publication.
- Emits occur outside both shared locks.
- Existing OSC split-residue, shell normalization, effective-directory, shell-switch clear, and room-scoped output tests remain green.

#### 2. Bind frontend completions to captured identity

Files: `web/static/js/terminals.js`, `web/static/js/terminal-modes.js`, `web/static/js/explorer-git-sidebar.js`, and Node-executed behavioral tests.

- Reset view must create a writer bound to the captured terminal object/session rather than `terminals[index]`. Flush that captured object's `_pendingOutput` before writing teardown so the bytes still land after replay, including while the pane is cached/off-screen.
- After reset completion, redraw or clear slot UI only if the original pane/session still occupies it. Never redraw the incoming group for the old reset.
- For Git actions, capture `pane`, `sessionId`, and normalized `scopePath` before `fetch`. Immediately before every post-`await` captured-state write, render, reload, or refresh, verify all three still match.
- If the same pane changed scope, discard the old payload/error, mark its repository model stale, and load the current scope only if active. If the group changed, mark the captured pane stale and let normal activation reload it.
- Clear busy state on the captured pane in `finally`, but render only when its identity is current.
- Do not cancel or automatically reissue the already-sent server mutation; it remains bound to the scope encoded in its request.

Required regression coverage:

- Replay bytes and teardown land on the original terminal in order after slot replacement; the incoming terminal receives neither.
- Delayed Git success/failure cannot render, reload, or refresh a replacement pane or overwrite a changed Follow scope.
- A stale successful action produces a later fresh load and leaves neither pane permanently busy.
- Preserve acknowledgement fallback, modal behavior, tree/open-file refresh, and Git watch behavior.

### Post-fix verification

#### Automated acceptance

- The deterministic retired-connection test proves late cwd cannot undo a deliberate clear, while a current connection still publishes normally.
- Node tests prove slot/scope replacement is harmless for Reset view and every Git action completion path.
- Existing stream, cwd, shell-switch, terminal-mode, explorer Git, and group-switch suites remain green.
- Run the full suite and Ruff before starting Stage 3. *(Done: 2,023 tests, 9 skipped, no failures; Ruff clean.)*

#### Manual acceptance

- Repeat the shell-switch scenario. The replacement shell remains usable and the retired shell's directory never reappears in saved state or a later explorer seed.
- Repeat both delayed frontend scenarios. The original pane completes safely, the incoming/current pane is untouched, current navigation remains selected, and returning to a cached pane triggers a normal refresh.
- Confirm Reset view still writes teardown after replay and its 1.5-second fallback still works.

Manual verification is not expected to prove the three deferred pre-existing connection-lifecycle races are fixed; they are out of scope.

### Documentation updates

- Update the cwd-observation guardrail in `AGENTS.md` and `CLAUDE.md`: parsing stays outside `connection_lock`, but metadata publication must match the exact current connection and broadcast after releasing locks.
- Do not add a broad connection-attempt generation guarantee to maintained docs; that hardening is not implemented by this stage.
- Add `CHANGELOG.md` entries only for the branch-scoped late-cwd fix and stale frontend async writes. Do not claim the older connection lifecycle is fully corrected.
- No `README.md` change is expected.

---

## Stage 3 — Atomic config reads and bounded mode-service extraction

Risk: lowest only if the extraction remains mechanical

Findings: M-3 and L-1

### Problem

#### M-3: some operations still combine RuntimeConfig generations

Although config publication swaps one immutable `RuntimeConfigState`, SSH timeout/keepalive, capacity checks/error messages, voice start, and some voice availability broadcasts still read the singleton more than once. A concurrent App Settings refresh can combine values no single generation contained.

#### L-1: pane-mode orchestration has regrown inside `web/api.py`

`change_session_mode()` spans about 250 lines and owns validation, cwd/root resolution, presentation cleanup, connection teardown, mutation, restart, and response/error mapping. The low-risk response is not a redesign: characterize existing behavior first, then mechanically move only the transition transaction behind an import-cycle-safe boundary.

### Pre-fix verification

#### Automated characterization

- Add controlled-generation tests whose fake config publishes distinguishable generations. Cover SSH timeout/keepalive, split/launch limit verdict and message, voice enabled/engine selection, and voice availability engine/calculation.
- Build a route characterization matrix for `POST /api/sessions/<id>/mode` before moving code:
  - missing/unsupported sessions and invalid bodies;
  - terminal ↔ explorer, terminal ↔ browser, and explorer/browser → terminal;
  - local, WSL, and SSH panes;
  - configured versus derived explorer root, observed cwd, and cwd-probe failure;
  - SFTP/local validation failures before mutation;
  - presentation fields cleared/preserved per current behavior;
  - connection close, status transitions, background restart, and response shape;
  - failure paths proving no partial mutation where the current route promises refusal.

#### Manual baseline

On one local/WSL pane and one disposable SSH pane, capture response and visible state for terminal → explorer from a changed cwd, explorer → terminal with configured/derived roots, terminal → browser → terminal, invalid explorer directory, and shell/launch-directory persistence.

There is no dependable manual reproduction for a mixed config generation; controlled tests are authoritative for M-3.

### Code changes

#### 1. Finish operation-scoped RuntimeConfig snapshots

Files: `web/terminal_io.py`, `web/api.py`, `web/workspaces.py`, and focused concurrency/config tests.

- Call `runtime_config.snapshot()` once at the start of every listed operation and use that object throughout.
- SSH connect: derive connection timeout and keepalive from one snapshot before the slow call.
- Split/launch: store `max_sessions` once and use it for verdict, log, and refusal text.
- Voice start: use one snapshot for enabled and engine. Voice availability broadcasts use one snapshot for the named engine and availability calculation.
- Convert adjacent repeated/multi-field operations found in the same focused audit; do not mass-rewrite unrelated single-field reads.
- Do not add reader locks. One captured immutable state reference is the intended model.

Required regression coverage:

- An operation cannot read a second generation.
- Capacity messages quote the exact limit used for the verdict.
- Voice enabled/engine and SSH timeout/keepalive each come from one state.

#### 2. Extract only the pane-mode transition transaction

Files: new canonical module `web/session_modes.py` (or the matching existing naming convention), `web/api.py`, and focused mode-transition tests.

- Create an import-cycle-safe service with no Flask request globals and no `jsonify`. Use explicit input/result/error types; retain HTTP parsing/mapping in the route.
- Move the existing transition decision and side-effect sequence plus tightly coupled cwd/root helpers. Leave unrelated shell switching, explorer backend, and workspace orchestration in their current canonical modules.
- Pass side-effect dependencies explicitly where imports would cycle: session manager access, connection close, status broadcast, and background connector startup. The service must not import `web.api`.
- Preserve the Stage 2 current-entry cwd publication guard and the existing connection APIs. Do not absorb the deferred connection-generation hardening into this extraction.
- Validate all fallible inputs first, resolve cwd/root, calculate metadata/presentation changes, then close/restart only when the transition can proceed.
- Return a structured outcome or narrow service error. Keep `web/api.py` to lookup/normalization, one service call, and HTTP response mapping.
- Re-export moved names from `web/api.py` only where compatibility requires it.
- Make no endpoint, response, persistence, presentation-schema, logging-content, or user-visible behavior change.

Scope stop:

- If characterization reveals a behavior change or broad rollback redesign is required, stop and split it into a separate reviewed change.
- Do not extract the whole explorer/terminal subsystem, rename public routes, or introduce a generic framework layer.

### Post-fix verification

#### Automated acceptance

- Controlled-generation tests prove one snapshot per operation.
- The pre-extraction route matrix produces identical status codes, response fields, metadata, presentation cleanup, close/restart calls, and failure atomicity afterward.
- Existing shell-switch, mode-switch, explorer startup/root, cwd, presentation, and multi-workspace suites remain green.
- Ruff reports no import cycle, unused compatibility export, or style regression.

#### Manual acceptance

- Repeat the local/WSL and SSH baseline matrix; responses and visible behavior match the pre-fix capture.
- Refresh App Settings between separate split attempts and confirm each response quotes the enforced limit.
- Toggle voice configuration between separate starts and confirm each start uses one enabled/engine combination.
- Confirm terminal → explorer/browser → terminal preserves shell choice, directory rules, and exactly one replacement start.

### Documentation updates

- Add `web/session_modes.py` to the Important Code sections in `AGENTS.md` and `CLAUDE.md`; update `web/api.py` to retain HTTP/Socket.IO adaptation as its documented role.
- Keep the immutable RuntimeConfig snapshot guardrail synchronized in both maintained files.
- Add only user-relevant fixes to `CHANGELOG.md`; the mechanical extraction needs no user-facing entry by itself.
- Do not change `README.md` unless implementation reveals an unplanned user-visible workflow change.

---

## Final merge gate

After all three stages:

1. Run each stage's focused Python and Node-executed behavioral suites.
2. Run `rtk test .\.venv\Scripts\python.exe tests\run_tests.py` and retain the already-green baseline.
3. Run `rtk ruff check .`.
4. Run `rtk git diff --check`.
5. Repeat the destructive Git verification only in a disposable fixture and retain before/after evidence that out-of-scope files and index entries are unchanged.
6. Review the final diff for accidental route/schema/config/default changes, process-tree changes, new dependencies, broad formatting churn, secret/local-state files, and edits outside the named modules/tests/docs.

The merge gate is satisfied only when H-3 cannot affect hidden paths, incomplete Git results cannot become success, late cwd/frontend completions cannot publish into stale identity, config operations consume one generation, the mode extraction preserves characterized behavior, and the full suite remains green.

H-1 is not part of this gate. The three pre-existing H-2 connection-lifecycle bullets are follow-up hardening, not claims of completion in this pre-merge plan.
