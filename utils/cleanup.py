"""Cache/temp file cleanup utility.

Removes Python bytecode caches and empties log files. Logs are truncated
rather than deleted because the running server holds ``logs/gridvibe.log``
open through a ``RotatingFileHandler`` (deleting it fails on Windows and
silently unlinks the active log on POSIX).
"""

import fnmatch
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

# Directories removed recursively when their *name* matches.
DIR_PATTERNS = ("__pycache__",)

# Files deleted when their name matches.
FILE_PATTERNS = ("*.pyc", "*.pyo")

# Files truncated (not deleted) when their name matches.
TRUNCATE_PATTERNS = ("*.log",)

SKIP_DIRS = {".venv", ".git", "node_modules"}


def _walk_project():
    """Yield (root_path, dirnames, filenames) below PROJECT_ROOT, pruning SKIP_DIRS."""
    for root, dirs, files in os.walk(PROJECT_ROOT, topdown=True):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        yield Path(root), dirs, files


def remove_matching_dirs(patterns, dry_run=True, verbose=True):
    """Recursively remove directories whose name matches any pattern."""
    removed = []
    for root_path, dirs, _files in _walk_project():
        for name in list(dirs):
            if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                continue
            full_path = root_path / name
            dirs.remove(name)  # don't descend into a directory we remove
            try:
                if not dry_run:
                    shutil.rmtree(full_path)
                if verbose:
                    prefix = "[DRY-RUN] Would remove" if dry_run else "Removed"
                    print(f"{prefix}: {full_path}")
                removed.append(full_path)
            except Exception as e:
                print(f"Error removing {full_path}: {e}")
    return removed


def remove_matching_files(patterns, dry_run=True, verbose=True):
    """Delete files whose name matches any pattern."""
    removed = []
    for root_path, _dirs, files in _walk_project():
        for name in files:
            if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                continue
            full_path = root_path / name
            try:
                if not dry_run:
                    full_path.unlink()
                if verbose:
                    prefix = "[DRY-RUN] Would remove" if dry_run else "Removed"
                    print(f"{prefix}: {full_path}")
                removed.append(full_path)
            except Exception as e:
                print(f"Error removing {full_path}: {e}")
    return removed


def truncate_matching_files(patterns, dry_run=True, verbose=True):
    """Empty files whose name matches any pattern, keeping the file itself."""
    truncated = []
    for root_path, _dirs, files in _walk_project():
        for name in files:
            if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                continue
            full_path = root_path / name
            try:
                if not dry_run:
                    with open(full_path, "w", encoding="utf-8"):
                        pass
                if verbose:
                    prefix = "[DRY-RUN] Would truncate" if dry_run else "Truncated"
                    print(f"{prefix}: {full_path}")
                truncated.append(full_path)
            except Exception as e:
                print(f"Error truncating {full_path}: {e}")
    return truncated


def cleanup(dry_run=True, verbose=True):
    if verbose:
        print("=" * 50)
        print("GridVibe Cleanup")
        print("=" * 50)
        if dry_run:
            print("Running in DRY-RUN mode.")
        else:
            print("Running in DESTRUCTIVE mode!")

    if verbose:
        print("\n--- Removing __pycache__ directories ---")
    remove_matching_dirs(DIR_PATTERNS, dry_run=dry_run, verbose=verbose)

    if verbose:
        print("\n--- Removing .pyc/.pyo files ---")
    remove_matching_files(FILE_PATTERNS, dry_run=dry_run, verbose=verbose)

    if verbose:
        print("\n--- Truncating log files ---")
    truncate_matching_files(TRUNCATE_PATTERNS, dry_run=dry_run, verbose=verbose)

    if verbose:
        print("\n" + "=" * 50)
        if dry_run:
            print("Dry run complete. Run with --confirm to apply changes.")
        else:
            print("Cleanup complete!")
        print("=" * 50)


def main():
    dry_run = "--confirm" not in sys.argv
    cleanup(dry_run=dry_run, verbose=True)


if __name__ == "__main__":
    main()
