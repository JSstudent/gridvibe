# Working-Directory Hardening Manual Smoke Test

Last updated: 2026-08-21  
Scope: `docs/working_directory_hardening.md`, stages 1-4 and 3.1.

Use disposable folders and repositories: several checks intentionally stage,
commit, revert, or discard files. Keep **Shell integration** enabled except in
scenario 6.

## Preparation

Create a disposable parent folder containing two Git repositories,
`repo-a` and `repo-b`. Give each repository one committed file and a `src`
subdirectory; add a local/bare `origin` if you want to complete the Publish
check without using a real remote. In GridVibe, prepare:

- one terminal launched on the disposable **parent** folder;
- one File Explorer pane launched directly on that parent folder (an explicitly
  configured root);
- one File Explorer pane launched directly on `repo-a` (another explicitly
  configured root);
- optionally, equivalent WSL and SSH panes for the parity pass.

Record Pass/Fail and any unexpected directory shown in the pane header.

## Scenarios

### 1. A navigated terminal opens at its observed directory

1. In the parent-folder terminal, `cd repo-a/src` and wait for the prompt.
2. Switch the pane to File Explorer mode and open the Git sidebar.

Expected: the current path is `repo-a/src`, the explorer root is `repo-a` (not
the parent and not `src`), and the Git bar names `repo-a`. No cwd fallback notice
appears.

### 2. Derived roots follow the shell back up; configured roots still pin

1. Switch scenario 1 back to a terminal, `cd` to the disposable parent, then
   switch to the explorer again.
2. Separately, use the explicitly configured `repo-a` explorer, switch it to a
   terminal, `cd src`, and switch it back.

Expected: the first pane follows the shell up and opens on the parent. The
second keeps `repo-a` as its root while opening on `src`; a root deliberately
chosen by the user was not converted into a derived root.

### 3. Split, reconnect, agent promotion, and restore keep the place

1. In a terminal at `repo-a/src`, split the pane and run `pwd` (or `Get-Location`)
   in the new pane.
2. Reset/reconnect the original pane and check its first prompt directory.
3. At that same prompt, start a registered agent, save the workspace, restart
   GridVibe, and restore it. Exit the restored agent and inspect the prompt.

Expected: the split, reconnect, and restored agent all use `repo-a/src`. If the
folder is removed before reconnect, the pane falls back to its launch directory
instead of failing to start.

### 4. Git can pin an inner repository or follow browsing

1. In the explorer rooted directly on `repo-a`, open the Git sidebar and
   navigate the main Files view into `src`.
2. In the parent-root File Explorer, navigate into `repo-a`, open the Git
    sidebar, and press the pin beside the Graph search magnifier. Navigate into
    `repo-a/src`, then enable the chain button and navigate back to the parent
    and into `repo-b/src`. Save the workspace, exit GridVibe, restart, and restore
    the workspace before disabling follow and clearing the pin.

Expected: the directly rooted pane continues to show all of `repo-a` while its
Files view moves into `src`. The parent-root pane does not acquire a repository
merely by navigation while both controls are off. Pinning at `repo-a` makes Git
name `repo-a`, and later navigation inside it does not narrow the graph. Once
follow is enabled, Git changes to `repo-b` without closing the sidebar. Turning
follow off after restore returns to the `repo-a` pin; clearing the pin returns to
parent-root scope. The restored sidebar must not briefly or permanently fall
back to that non-Git root. The followed repository must change even when both
repositories have identical branch names, HEADs, and clean status.

### 5. Every Git action stays on the named repository

Make different scratch edits in both repositories. With Follow browsed folder
enabled and the Files view inside `repo-b`, exercise Stage, Unstage, Stage All,
Unstage All, Revert, Discard All, and Commit. Complete Publish against the
disposable remote, or cancel at its confirmation if no remote was prepared.

Expected: only `repo-b` changes, the Git bar continues to name `repo-b`, and
`repo-a` is untouched. Repeat one single-file action after navigating back to
`repo-a`; it must then affect only `repo-a`.

### 6. Observation fallback is visible and never targets an agent

1. Disable **Shell integration**, start a fresh terminal, `cd repo-a/src`, and
   switch to the explorer once at an idle prompt.
2. Start a long-running foreground command and switch again before it returns.
3. Re-enable Shell integration, start a registered agent, and switch that pane
   to the explorer.

Expected: an idle fallback may resolve the directory. If the busy-shell
fallback cannot resolve it, the pane names the directory it assumed instead of
silently pretending it was observed. The agent case never sends a probe into
the agent input and uses the last prompt observation.

## Optional shell parity

Repeat scenarios 1 and 4 with PowerShell, cmd, WSL, and SSH where available.
PowerShell/cmd paths should be native Windows paths, WSL observations should map
to Windows paths for local explorer access, and SSH paths should remain POSIX.
No OSC text should be visible in terminal output; only the one documented SSH
startup hook line may be echoed.

## Pass condition

The hardening pass is complete when scenarios 1-5 pass, scenario 6 reports any
fallback visibly and does not type into an agent, and every available shell in
the optional parity pass behaves consistently.
