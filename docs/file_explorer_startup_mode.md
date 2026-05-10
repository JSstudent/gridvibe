# File Explorer Startup Mode

## Summary

GridVibe can support a file explorer pane opened on the selected local repository directory, next to the existing normal terminal startup and agent startup options. The explorer should be a first-class pane type, not a terminal command workaround.

## Current Flow

The launcher exposes startup choices per terminal. Terminal and agent panes both become PTY-backed sessions: the backend opens an SSH or local shell, changes into the selected directory, optionally sends an initial command, and streams output to xterm.

Explorer panes are different. They should render a filesystem surface rooted at the selected directory and should not start a PTY or SSH shell for the local-only milestone.

## Pane Model

Use a per-pane startup field:

```json
{
  "startup_mode": "terminal"
}
```

Supported values:

- `terminal`: open a normal shell with no initial command.
- `agent`: open a shell and run the selected agent command.
- `explorer`: render a file explorer rooted at the resolved selected directory.

Backward compatibility:

- `initial_command_mode: "agent"` maps to `startup_mode: "agent"`.
- Any other existing setup maps to `startup_mode: "terminal"`.

## First Scope

Start with local repo mode only. Local filesystem browsing from the Python backend is straightforward and avoids the complexity of SSH SFTP, remote permissions, WSL path translation, and remote path validation.

The first version should support:

- list directory contents
- navigate into folders
- navigate to parent directory, constrained to the configured root
- refresh current directory
- show basic file metadata such as name, type, size, and modified time
- click text files to display a read-only preview inside the explorer pane
- show git status and diffs for files inside the selected repository

It should not initially edit files, upload files, delete files, or browse outside the selected root.

## Backend Shape

Explorer panes should carry lightweight session metadata through `startup_mode` or `pane_type`.

Terminal and agent panes continue to use the existing SSH/local shell connection flow. Explorer panes should mark the session connected after validation and skip PTY startup.

Filesystem endpoints:

- `GET /api/explorer/<session_id>/entries?path=...`
- `GET /api/explorer/<session_id>/file?path=...`
- `GET /api/explorer/<session_id>/git/status`
- `GET /api/explorer/<session_id>/git/diff?path=...`

Every endpoint must validate that the requested path resolves inside the configured explorer root. File preview should be read-only, size-limited, and text-only. Binary files should return metadata plus a clear "preview unavailable" response.

Git endpoints should run against the explorer root when it is inside a git worktree. The backend should not assume GridVibe's own checkout is the target repo. It should resolve the pane root, confirm it belongs to a worktree, then run git commands with `git -C <repo-root> ...`.

Recommended git data:

- porcelain status for tracked, modified, deleted, renamed, staged, untracked, and ignored states where useful
- per-file diff for working tree changes
- staged diff when the file has staged changes
- clear responses for files with no diff, binary diffs, or files outside the worktree

## Frontend Shape

In the launcher, expose three startup modes:

- Initial Command
- Agent
- File Explorer

In the terminal workspace, render panes by type:

- terminal and agent panes use the current xterm card
- explorer panes use a file browser inside the same grid card structure

Explorer rows should behave as follows:

- directories are clickable and navigate into that directory
- text files are clickable and open a read-only preview panel
- files with git changes display a compact status badge
- modified files expose a diff view from the preview panel

The preview and diff views should stay inside the pane. They should not open external editors, write to disk, or mutate git state.

## Risks

The main risk is that the terminal workspace historically assumed every pane had an xterm instance. Mixed pane types require careful handling in:

- initial render
- polling refresh
- session status display
- close session/group behavior
- drag-and-drop pane ordering
- replay buffers and Socket.IO joins

File preview adds these risks:

- large files can stall the UI unless preview reads are capped
- binary files must not be forced through text rendering
- encoding detection should be conservative and tolerant of invalid bytes

Git integration adds these risks:

- the selected root may be a subdirectory of a worktree
- untracked files have no normal diff unless handled separately
- staged and unstaged changes can differ for the same file
- git may be unavailable on `PATH`
- repositories can be large, so status and diff calls should be scoped and cached only if needed

SSH explorer support should remain a later phase. It would likely require SFTP via Paramiko, connection reuse decisions, remote path validation, and more error states.

## Recommendation

Implement explorer startup as a first-class local-only pane type. Add clickable read-only file previews next, then layer git status and per-file diffs on top of the same path-validation model.
