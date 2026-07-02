# Git Integration for File Explorer Mode R&D

## Goal

Research what Git integration is practical for GridVibe's local file explorer panes and propose a phased implementation plan.

The short answer: Git integration is feasible with the current architecture. The safest first version should be read-only Git awareness: repository detection, branch/ahead-behind summary, per-file status badges, directory-level dirty indicators, and a size-limited diff preview. Commit, stage, restore, checkout, pull, and push actions should remain out of v1.

## Current GridVibe Context

- File explorer panes are represented as local `wsl` sessions with `startup_mode == "explorer"` in `web/api.py`.
- The backend already has root-bound path validation via `_resolve_explorer_candidate_path`, `_resolve_explorer_paths`, and `_resolve_explorer_file_path`.
- Explorer listing and preview are exposed through two read-only routes:
  - `GET /api/explorer/<session_id>/entries`
  - `GET /api/explorer/<session_id>/file`
- `templates/terminals.html` owns the browser-side explorer UI through `loadExplorerPane`, `openExplorerFile`, `refreshExplorerPane`, and related render helpers.
- Explorer rows already carry name, path, type, size, and modified-time metadata, so Git state can be added as more row metadata without changing the session model.
- Existing tests in `tests/test_api.py` cover explorer root safety, directory listing, file preview, binary rejection, large-file truncation, and pane mode switching.
- `README.md` explicitly says Git diff/status views are not part of the current implementation, so this is a known gap rather than a regression.

## External Research Notes

- Git's `status --porcelain` output is designed for scripts. Porcelain v1 is stable and porcelain v2 adds more detailed changed-item records and extensible headers. The `-z` form avoids path quoting problems and uses NUL separators for machine parsing. Source: https://git-scm.com/docs/git-status
- Git's native diff command covers worktree, index, staged, unstaged, commit, and path-limited comparisons. Source: https://git-scm.com/docs/git-diff
- GitPython exposes repository objects, dirty state, untracked files, and diff APIs, but also documents direct fallback to the Git command through `repo.git`. Source: https://gitpython.readthedocs.io/en/stable/tutorial.html
- pygit2 exposes libgit2-backed repository, status, index, working copy, and diff APIs, but it adds a native library dependency surface. Sources: https://www.pygit2.org/index_file.html and https://www.pygit2.org/diff.html
- Dulwich provides a Python Git implementation with a porcelain module that resembles the Git command-line API. Source: https://dulwich.readthedocs.io/en/latest/tutorial/porcelain.html

## Integration Options

### Option 1: Git CLI subprocesses

Use the installed `git` executable from Python subprocess calls. Parse stable machine-readable output.

Useful commands:

```text
git -C <path> rev-parse --show-toplevel --is-inside-work-tree
git -C <path> status --porcelain=v2 -z --branch -- <pathspec>
git -C <path> diff --no-ext-diff -- <pathspec>
git -C <path> diff --cached --no-ext-diff -- <pathspec>
git -C <path> diff --stat --no-ext-diff -- <pathspec>
```

Pros:

- No new Python runtime dependency.
- Matches the user's installed Git behavior on Windows, WSL-backed folders, and normal local repos.
- `status --porcelain` is intended for scripts.
- Easy to start with read-only commands.

Cons:

- Requires Git to be installed and on `PATH`.
- Needs careful subprocess timeouts, output limits, and pathspec handling.
- Parsing porcelain v2 and rename records is extra code.

Recommendation: use this for v1.

### Option 2: GitPython

Use GitPython for repository detection, dirty state, untracked files, and diffs.

Pros:

- Pythonic API.
- Good fit for simple repository metadata.
- Can still call raw Git for missing functionality.

Cons:

- Adds a dependency.
- Some operations still lean on the Git executable.
- For status badges, we still need careful mapping of staged, unstaged, untracked, ignored, and renamed states.

Recommendation: consider only if CLI parsing becomes too noisy.

### Option 3: pygit2

Use libgit2 through pygit2.

Pros:

- Strong Git object, index, status, and diff APIs.
- Avoids parsing CLI text.
- Better suited if GridVibe later needs richer Git internals.

Cons:

- Native dependency risk is high for a lightweight Windows desktop/browser tool.
- Packaging with PyInstaller/native desktop distribution becomes harder.

Recommendation: do not use for v1.

### Option 4: Dulwich

Use a pure-Python Git implementation.

Pros:

- Avoids requiring system Git for some workflows.
- Python-only installation is attractive.

Cons:

- More behavioral differences from native Git are possible.
- The current need is UI status/diff on an existing local checkout, where the user's Git CLI is the source of truth.

Recommendation: keep as a fallback research option, not first implementation.

## Recommended V1 Scope

Build a read-only Git awareness layer for local explorer panes:

1. Detect whether the explorer root or current directory is inside a Git worktree.
2. Return repository metadata with explorer directory listings:
   - `git.available`
   - `git.repo_root`
   - `git.branch`
   - `git.head`
   - `git.ahead`
   - `git.behind`
   - `git.dirty`
3. Add per-entry Git metadata:
   - `git.status`: `modified`, `added`, `deleted`, `renamed`, `untracked`, `ignored`, `conflicted`, or `clean`
   - `git.index_status`: staged status code
   - `git.worktree_status`: unstaged status code
   - `git.has_descendant_changes`: directory aggregate flag
4. Add a diff endpoint for a selected file:
   - `GET /api/explorer/<session_id>/git/diff?path=<path>&mode=worktree|staged|head`
   - Reuse the existing explorer path resolver so the selected path stays inside the configured explorer root.
   - Return text diff only, with byte and line limits.
5. Add lightweight UI affordances:
   - status badge or colored marker in each explorer row;
   - branch/dirty summary in the explorer bar;
   - a `Diff` tab or button in file preview when a tracked file has changes;
   - clear empty/error states when Git is unavailable or the folder is not a repository.

V1 should not mutate repositories. That means no stage, unstage, restore, checkout, commit, pull, or push.

## Backend Design

Add small helper functions in `web/api.py` or a new local module such as `services/git_service.py`. A separate module is cleaner if parsing grows beyond a few helpers.

Proposed helpers:

```python
def get_git_context(root_path: str, current_path: str) -> dict:
    """Return repo metadata and path mapping for an explorer request."""

def get_git_status_for_tree(repo_root: str, scope_path: str) -> dict:
    """Return path-to-status metadata parsed from porcelain output."""

def get_git_diff(repo_root: str, requested_path: str, mode: str) -> dict:
    """Return a bounded text diff for one file or directory path."""
```

Subprocess rules:

- Call `subprocess.run` with an argument list, never through a shell.
- Use `cwd` or `git -C` with validated absolute paths.
- Set a short timeout, probably 2 seconds for status and 3 seconds for diff.
- Use `GIT_OPTIONAL_LOCKS=0` for read-only status checks.
- Use `--no-ext-diff` for diff endpoints.
- Bound stdout and stderr processing to avoid large responses.
- Treat missing Git, timeout, invalid repo, and oversized output as non-fatal explorer metadata errors.

Path rules:

- Continue using `_resolve_explorer_candidate_path` before any Git operation that accepts user-selected paths.
- If Git repo root is outside the explorer root, only show status for paths under the configured explorer root. Do not expose sibling paths outside the explorer root.
- For subdirectory explorer roots, run path-limited Git commands and normalize returned repo-relative paths back to explorer-relative paths.
- Preserve symlink behavior from the current explorer implementation.

Response shape sketch for `entries`:

```json
{
  "session_id": "abc123",
  "root": "C:\\repo",
  "path": "src",
  "git": {
    "available": true,
    "repo_root": "C:\\repo",
    "branch": "main",
    "ahead": 0,
    "behind": 1,
    "dirty": true,
    "error": null
  },
  "entries": [
    {
      "name": "app.py",
      "path": "src/app.py",
      "type": "file",
      "size": 1200,
      "modified": 1710000000,
      "git": {
        "status": "modified",
        "index_status": " ",
        "worktree_status": "M",
        "has_descendant_changes": false
      }
    }
  ]
}
```

## Frontend Design

Add Git state to the existing explorer renderer:

- `loadExplorerPane` should read optional `data.git` and optional `entry.git`.
- Show branch/dirty text in the explorer bar, likely beside the path label.
- Add a compact status marker before or after the file icon:
  - `M` modified
  - `A` added
  - `D` deleted
  - `R` renamed
  - `U` conflicted
  - `?` untracked
  - `!` ignored, only if ignored files are requested later
- Directories should show a subtle aggregate marker when descendants are dirty.
- Keep manual refresh as the first cache invalidation mechanism. Do not add file watchers in v1.
- In file preview, show a `Diff` tab only when the selected file has Git changes or when the diff endpoint returns content.

CSS should stay within the current explorer theme variables and avoid making Git state the dominant visual element.

## Later Phases

### Phase 2: Better Diff Views

- Side-by-side or inline diff rendering.
- Diff stats in row metadata.
- Directory diff summary.
- Filter explorer rows by changed/untracked/clean.

### Phase 3: Explicit Mutating Actions

Only after the read-only layer is stable:

- Stage/unstage selected file.
- Restore selected file with confirmation.
- Commit selected staged changes.
- Stash selected changes.

These actions should have dedicated endpoints, CSRF-conscious request patterns if GridVibe ever binds beyond localhost, confirmation UI, and tests for failure states.

### Phase 4: Remote Awareness

- Fetch status and ahead/behind refresh.
- Pull/push buttons.
- Credential-helper behavior documentation.

Remote actions should not be in early scope. They can block on credentials, mutate history, and surprise users more easily than local status/diff views.

## Risks and Mitigations

- **Large repositories can make status slow.** Use path-limited status, short timeouts, manual refresh, and cache results briefly per session/path.
- **Nested repositories and submodules can confuse aggregation.** Treat submodules as a distinct status type at first and avoid recursing into nested worktrees unless explicitly opened.
- **Explorer root may be inside a larger repo.** Filter all Git output to the explorer root boundary.
- **Git may not be installed.** Return `git.available: false` and keep the explorer fully usable.
- **Diffs can be huge or binary.** Use `--no-ext-diff`, output limits, and text-only responses.
- **Mutating Git actions are risky.** Keep v1 read-only.

## Test Plan

Backend tests:

- Non-repo explorer listing returns normal entries and `git.available: false`.
- Repo explorer listing returns branch metadata and clean statuses.
- Modified, staged, deleted, renamed, untracked, and conflicted fixture states map to expected row metadata.
- Explorer root inside repo only exposes statuses under that root.
- Path outside explorer root is rejected before Git is invoked.
- Missing Git executable and Git timeout return non-fatal Git metadata errors.
- Diff endpoint rejects outside-root paths, directories if unsupported, binary/oversized output, and invalid modes.

Frontend tests:

- `templates/terminals.html` includes Git status marker render hooks.
- Rows with `entry.git.status` render compact status labels.
- Git unavailable/non-repo state does not break directory rendering.
- File preview can show/hide a diff tab based on backend data.

Manual checks:

- Clean repo.
- Dirty repo with staged and unstaged changes.
- Repo with untracked files.
- Explorer root set to a repo subdirectory.
- Very large repo or generated directory.

## Proposed Implementation Order

1. Add backend Git service helpers using Git CLI subprocesses.
2. Add parser tests for porcelain v2 `-z` status fixtures.
3. Add Git metadata to `GET /api/explorer/<session_id>/entries`.
4. Render row badges and branch/dirty summary in `templates/terminals.html`.
5. Add read-only diff endpoint.
6. Add a diff view to file preview.
7. Update README and CHANGELOG when the feature ships.

## Decision

Proceed with a Git CLI based, read-only v1. It fits the current browser-first local explorer architecture, avoids new dependencies, preserves root-bound safety, and gives the highest-value Git UX first: "what changed?" and "show me the diff."
