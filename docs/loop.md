# Stage implementation loop

Use this workflow for an ordered list of stages from an implementation plan.
“Implement stages 3, 4, and 5 of `<plan>` with a Codex/Claude pair” means
Codex codes and Claude reviews each stage, in that order. Stage numbers identify
sections of the plan; they are not a stage count.

## Inputs

| Input | Rule |
| --- | --- |
| Plan | Use the document named by the user. Read the requested stages and their prerequisites. |
| Stages | Use exactly the ordered stage identifiers the user gives. Run them sequentially. `stage_count` is the number of requested identifiers; divide them into consecutive batches of at most four. |
| Agent pair | The first agent is the coder; the second is the reviewer. Resolve their installed keys and task support with `list_agent_types`. |
| Workspace | Use the coordinator's current workspace from `whoami` unless the user names another one. |
| Commit mode | By default the request to run this loop authorizes one commit per completed stage, and the coordinator goes straight on to the next stage without pausing. It pauses for the user's approval before each commit only when the user explicitly asks to approve stages (for example "commit after each stage I approve"). Approval given mid-run for the remaining stages switches back to the default. |
| Auto mode | Request it explicitly for reviewer panes with `auto_mode: true` when supported. The coder is started later in a prepared terminal with `set_pane_agent`, which has no `auto_mode` parameter; report that coder limitation rather than claiming auto mode. A `task` alone never enables it. |

The agent running this loop is the **coordinator**. It needs a GridVibe pane
with MCP access to hand tasks to coders and collect their reports. A coder
hands the review task to a reviewer and collects that reviewer's report.

## Prepare

1. Read the plan, the target project's instructions, and any technical
   references needed for the requested work. Confirm that each requested stage
   exists and its prerequisites are satisfied.
2. Record Git status and the starting commit. Preserve unrelated changes. If
   existing changes would make the stage diff ambiguous, identify them before
   launching an agent. The coordinator commits after each completed stage, so
   each later stage's starting commit is the previous stage's commit.
3. Check that both chosen agents are available and support MCP task handoffs.
   A handed-off task runs on the requester's machine; use paths valid there.
4. Allow for one reviewer split per stage. If GridVibe refuses a split because
   of its pane limit or window size, use the review fallback below and report
   the actual result.

## Set up the stage sessions

1. Partition the requested stages, in order, into batches of at most four.
   The coordinator calls `launch_panes` once per batch in the chosen workspace,
   with `layout: vertical` and one plain terminal pane per stage. Do not pass
   `workspace_layout` or calculate pane geometry. Give each tab a name based
   on the plan and its batch's stage identifiers, and give each pane a stage
   title. Save every returned session name, group ID, and pane ID as a mapping
   from stage to its prepared terminal. These launches need no visible window.
   GridVibe may normalize a four-pane `vertical` request to `grid`; use the
   returned pane IDs to keep stage assignments stable.
2. Call `focus_session` for the first requested stage's session and check for
   `status: opened` with `session_activated: true`. A failed focus does not
   undo the prepared sessions; it means a later split may need the separate-tab
   fallback.

For stages 3, 4, and 5, create one session with three terminals. For five
stages, create one session with four terminals and another with one. Each
coder later tries to split its assigned terminal downward for a reviewer.

## Run each stage

1. **Start the coder in its prepared terminal.** For the next requested stage,
   use `set_pane_agent` on that stage's terminal pane with the coder agent key
   and a `task`. The coordinator created the terminal, so its lineage allows
   this relaunch. Leave the other stage terminals untouched until their turn.
   `set_pane_agent` has no `auto_mode` field: this coder starts in normal mode.
   If coder auto mode is mandatory, the current MCP surface cannot satisfy
   both that requirement and sequential use of prepared terminals;
   report the limitation before starting the coder.
2. **Implement.** The coder calls `read_handoff`, implements only this stage in
   code and automated tests, and runs relevant automated checks. It leaves
   project documentation outside the plan for a separate pass when the user
   requested code and tests only. It never restarts or kills the GridVibe
   process hosting the agents.
3. **Launch the reviewer below the coder when possible.** The coder calls
   `focus_session` for its stage's session and requires `status: opened` with
   `session_activated: true`. It then calls `split_pane` on its own pane with
   `axis: horizontal`, `kind: agent`, the reviewer key, a review `task`, and
   `auto_mode: true` when supported. `horizontal` places the new pane below.
4. **Use a separate review tab if the split cannot be confirmed.** The target
   session tab must be displayed in a visible native GridVibe window for a
   split. If focus is not confirmed, or the split returns `refused` or
   `no_window_available`, launch the reviewer with the same task using
   `launch_panes` in a new review tab. This route needs no visible window. If
   GridVibe says the split outcome is unknown because the result could not be
   read, call `list_panes` before launching a fallback so the reviewer is not
   created twice. Do not retry the split blindly or switch its axis. In
   browser mode, use the separate review tab directly. Report that its stage
   session then has no reviewer pane below that coder.
5. **Review and repair.** The reviewer calls `read_handoff`, reviews the current
   stage's unstaged diff, and sends findings through `report_result`. Use OCR
   delegate review if the reviewer has it; otherwise perform an independent
   review and name the method. A Codex reviewer in auto mode runs its commands
   as the Windows sandbox user, which cannot traverse the home directory above
   the repository, so `ocr delegate` inside the sandbox always fails with
   `load rules: resolve repo dir …: Access is denied`. Both OCR delegate
   commands only read, so the reviewer runs them with escalated sandbox
   permissions from the start: `ocr delegate preview --format json` (workspace
   mode is the unstaged diff), then `ocr delegate rule --format json <files>`.
   Only a refused escalation falls back to an independent review, and the
   report names that refusal. The coder calls `wait_for_results` until the
   reviewer reports or the assignment ends, checks every finding, fixes valid
   ones, and records why any finding was rejected. Request another review if
   the fixes materially change the code.
6. **Update and report.** The coder updates the stage in the plan at the path
   the user supplied, even if that file is untracked or ignored by Git. It
   calls `report_result` with changed files, automated checks and results,
   review findings and fixes, remaining issues, a suggested commit subject
   following the target project's rules, and the plan's hands-on checks. List
   hands-on checks in the native window as pending; do not claim they passed.
   Leave changes unstaged and uncommitted.
7. **Wait and commit.** The coordinator calls `wait_for_results` until the coder
   reports or the assignment ends. Each call is bounded; repeat while work is
   ongoing. Inspect the report, diff, and Git status, then commit the stage.
   Stage only the files the stage changed, leaving unrelated changes alone, and
   use the coder's suggested subject or the target project's commit rules.
   Every completed stage is committed before the next requested stage begins,
   so the next coder starts from a clean, known commit. Record that commit ID
   for the final summary. If the report is unusable or the checks fail, resolve
   that with the coder first rather than committing. Coder and reviewer agents
   never commit, amend, tag, or push; only the coordinator commits, and it does
   not amend, tag, or push either.

## Finish

After the last requested stage, summarize each stage's implementation,
automated checks, review outcome, plan update, commit ID, and remaining
hands-on checks or limitations. Do not start stages the user did not request.

## Handoffs

Give each coder the plan path, exact stage identifier, reviewer agent, and
starting commit. Include this instruction:

> Implement Stage `<stage>` of `<plan>` in code and automated tests. Read the
> target project's instructions. Keep project documentation outside the plan
> for a separate pass when this is a code-and-tests stage; update this stage
> in the plan even if it is untracked or ignored. Never restart or kill the
> GridVibe process hosting the agents. Hand the current stage's unstaged diff
> to `<reviewer>` using the review procedure above, assess its findings, and
> fix valid ones. Run relevant automated checks. Leave changes unstaged and
> uncommitted. Report with `report_result`, listing the plan's hands-on checks
> as pending.

Give each reviewer the plan path, exact stage identifier, and the stage's
starting commit. Include this instruction:

> Review Stage `<stage>` of `<plan>` against its unstaged diff and repository
> instructions. Use OCR delegate review if available. Run
> `ocr delegate preview --format json` and then
> `ocr delegate rule --format json <files>` with escalated sandbox permissions
> (they only read; inside the sandbox they fail with "Access is denied"). Only
> if that escalation is refused, review independently and say so. Name the
> method. Do not edit files. Send concrete findings, or state that none were
> found, with `report_result`.

The coordinator checks that the reviewer sees only the current stage's diff,
which is why each stage is committed before the next begins. The coordinator
makes each stage commit using that project's conventions. An ignored or
untracked plan update still counts as completed plan work, but it will not
appear in the stage's Git commit.
