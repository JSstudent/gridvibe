"""Self-update flow: fetch and fast-forward GridVibe's own git checkout."""

import shutil
import subprocess
from typing import Any, Dict, List

from web.paths import BASE_DIR

SELF_UPDATE_REPO_DIR = BASE_DIR


class AppUpdateError(RuntimeError):
    """Raised when the application update flow cannot complete safely."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _run_repo_git(args: List[str]) -> subprocess.CompletedProcess[str]:
    """Run one git command inside GridVibe's own checkout (self-update only).

    Positional-only usage keeps this distinct from the explorer runner
    `_run_git_command(args, *, cwd=...)`, which requires an explicit cwd —
    mixing the two up previously caused a production TypeError.
    """
    git_path = shutil.which("git")
    if not git_path:
        raise AppUpdateError("Git is not installed or is not available on PATH.", 400)

    try:
        return subprocess.run(
            [git_path, "-C", SELF_UPDATE_REPO_DIR, *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise AppUpdateError(f"Failed to run git {' '.join(args)}: {exc}", 500) from exc


def _git_error_message(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Return the most useful stderr/stdout text from a git command."""
    return (result.stderr or result.stdout or fallback).strip()


def perform_self_update() -> Dict[str, Any]:
    """Fetch and fast-forward the current git checkout when safe."""
    repo_result = _run_repo_git(["rev-parse", "--is-inside-work-tree"])
    if repo_result.returncode != 0 or repo_result.stdout.strip().lower() != "true":
        raise AppUpdateError(
            "This installation is not running from a git checkout.",
            400,
        )

    branch_result = _run_repo_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(branch_result, "Could not determine the current branch."),
            500,
        )

    branch = branch_result.stdout.strip()
    if not branch or branch == "HEAD":
        raise AppUpdateError(
            "This checkout is in detached HEAD mode and cannot self-update safely.",
            409,
        )

    status_result = _run_repo_git(["status", "--porcelain"])
    if status_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(status_result, "Could not inspect the git worktree."),
            500,
        )

    if status_result.stdout.strip():
        raise AppUpdateError(
            "Local changes are present. Commit, stash, or discard them before checking for updates.",
            409,
        )

    upstream_result = _run_repo_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
    )
    if upstream_result.returncode != 0:
        raise AppUpdateError(
            f"Branch '{branch}' does not track a remote branch, so automatic updates are unavailable.",
            400,
        )

    upstream = upstream_result.stdout.strip()

    fetch_result = _run_repo_git(["fetch", "--all", "--prune"])
    if fetch_result.returncode != 0:
        raise AppUpdateError(
            f"Git fetch failed: {_git_error_message(fetch_result, 'Unable to contact the remote repository.')}",
            500,
        )

    count_result = _run_repo_git(["rev-list", "--left-right", "--count", "HEAD...@{u}"])
    if count_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(count_result, "Could not compare the local branch with its upstream."),
            500,
        )

    counts = count_result.stdout.strip().split()
    if len(counts) != 2:
        raise AppUpdateError("Git returned an invalid branch comparison result.", 500)

    try:
        ahead_count = int(counts[0])
        behind_count = int(counts[1])
    except ValueError as exc:
        raise AppUpdateError("Git returned a non-numeric branch comparison result.", 500) from exc

    if behind_count == 0 and ahead_count == 0:
        return {
            "updated": False,
            "restart_required": False,
            "branch": branch,
            "upstream": upstream,
            "ahead_count": 0,
            "behind_count": 0,
            "message": f"GridVibe is already up to date on '{branch}'.",
        }

    if behind_count == 0 and ahead_count > 0:
        return {
            "updated": False,
            "restart_required": False,
            "branch": branch,
            "upstream": upstream,
            "ahead_count": ahead_count,
            "behind_count": 0,
            "message": f"Branch '{branch}' is already ahead of {upstream}; no update was applied.",
        }

    if ahead_count > 0 and behind_count > 0:
        raise AppUpdateError(
            f"Branch '{branch}' has diverged from {upstream}. Resolve the branch state manually before updating.",
            409,
        )

    previous_commit_result = _run_repo_git(["rev-parse", "HEAD"])
    if previous_commit_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(previous_commit_result, "Could not determine the current commit."),
            500,
        )

    previous_commit = previous_commit_result.stdout.strip()

    pull_result = _run_repo_git(["pull", "--ff-only"])
    if pull_result.returncode != 0:
        raise AppUpdateError(
            f"Git pull failed: {_git_error_message(pull_result, 'The remote update could not be applied.')}",
            500,
        )

    current_commit_result = _run_repo_git(["rev-parse", "HEAD"])
    if current_commit_result.returncode != 0:
        raise AppUpdateError(
            _git_error_message(current_commit_result, "Could not determine the updated commit."),
            500,
        )

    current_commit = current_commit_result.stdout.strip()
    return {
        "updated": previous_commit != current_commit,
        "restart_required": previous_commit != current_commit,
        "branch": branch,
        "upstream": upstream,
        "ahead_count": 0,
        "behind_count": behind_count,
        "previous_commit": previous_commit,
        "current_commit": current_commit,
        "message": (
            f"Updated '{branch}' from {previous_commit[:7]} to {current_commit[:7]}."
            if previous_commit != current_commit
            else f"GridVibe is already up to date on '{branch}'."
        ),
    }
