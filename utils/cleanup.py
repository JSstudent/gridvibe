import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

DESTRUCTIVE_PATTERNS = []

CLEANUP_PATTERNS = [
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.log",
]


SKIP_DIRS = {".venv", ".git", "node_modules"}


def find_and_remove(pattern, is_dir=False, dry_run=True, verbose=True):
    removed = []
    for root, dirs, files in os.walk(PROJECT_ROOT, topdown=True):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        root_path = Path(root)

        for d in list(dirs):
            if d == pattern or (pattern.startswith("*") and d.endswith(pattern[1:])):
                full_path = root_path / d
                try:
                    if dry_run:
                        if verbose:
                            print(f"[DRY-RUN] Would remove: {full_path}")
                        removed.append(full_path)
                    else:
                        shutil.rmtree(full_path)
                        if verbose:
                            print(f"Removed: {full_path}")
                        removed.append(full_path)
                    if is_dir:
                        dirs.remove(d)
                except Exception as e:
                    print(f"Error removing {full_path}: {e}")

        if not pattern.startswith("*"):
            continue

        for f in list(files):
            if f.endswith(pattern[1:]):
                full_path = root_path / f
                try:
                    if dry_run:
                        if verbose:
                            print(f"[DRY-RUN] Would remove: {full_path}")
                        removed.append(full_path)
                    else:
                        full_path.unlink()
                        if verbose:
                            print(f"Removed: {full_path}")
                        removed.append(full_path)
                except Exception as e:
                    print(f"Error removing {full_path}: {e}")

    return removed


def cleanup(dry_run=True, verbose=True):
    if verbose:
        print("=" * 50)
        print("Terminal Flow Cleanup")
        print("=" * 50)
        if dry_run:
            print("Running in DRY-RUN mode.")
        else:
            print("Running in DESTRUCTIVE mode!")

    if verbose:
        print("\n--- Removing __pycache__ directories ---")
    find_and_remove("__pycache__", is_dir=True, dry_run=dry_run, verbose=verbose)

    if verbose:
        print("\n--- Removing .pyc files ---")
    find_and_remove("*.pyc", dry_run=dry_run, verbose=verbose)
    find_and_remove("*.pyo", dry_run=dry_run, verbose=verbose)

    if verbose:
        print("\n--- Removing log files ---")
    find_and_remove("*.log", dry_run=dry_run, verbose=verbose)

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

