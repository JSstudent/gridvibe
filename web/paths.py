"""Filesystem locations shared by GridVibe web modules."""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_kind() -> str:
    """Return 'git' or 'source' for the running installation.

    Part 2 adds a 'frozen' branch for packaged builds.
    """
    return "git" if os.path.isdir(os.path.join(BASE_DIR, ".git")) else "source"
