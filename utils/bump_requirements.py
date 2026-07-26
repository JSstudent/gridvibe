"""Rewrite the version floors in ``requirements*.txt`` to the latest PyPI releases.

``make deps-update`` only upgrades the packages *installed in the virtualenv*; it
never touches the requirement files, so a ``>=`` floor stays at whatever version
was current when it was written. This utility closes that gap: it reads every
requirement line with a simple ``>=`` / ``==`` / ``~=`` pin, asks PyPI for the
newest release of that project, and rewrites the pinned version in place.

Environment markers, extras, comments, ``-r`` includes and formatting are left
byte-for-byte alone -- only the version number itself is replaced.

Releases are filtered by their ``requires-python`` metadata against the *lowest*
Python GridVibe supports (``requires-python`` in ``pyproject.toml``, currently
3.10) rather than the interpreter running this script -- otherwise a developer on
a newer Python would raise a floor to a release the supported range cannot
install (``ERROR: No matching distribution found``). Floors are only ever raised,
never lowered.

Usage::

    python utils/bump_requirements.py                  # rewrite all requirements*.txt
    python utils/bump_requirements.py --check          # report only, exit 1 if behind
    python utils/bump_requirements.py --dry-run        # report only, exit 0
    python utils/bump_requirements.py --pre            # consider pre-releases
    python utils/bump_requirements.py --python-version 3.12  # target a newer floor
    python utils/bump_requirements.py requirements.txt # limit to specific files

Exit codes: ``0`` nothing to do (or files rewritten), ``1`` updates available in
``--check`` mode, ``2`` at least one package could not be looked up on PyPI.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "gridvibe-bump-requirements"
DEFAULT_TIMEOUT = 15

# name[extras] <op> version [; marker] [# comment]
REQUIREMENT_RE = re.compile(
    r"^(?P<head>\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?\s*"
    r"(?P<op>>=|==|~=)\s*)"
    r"(?P<version>[0-9][A-Za-z0-9.*+!-]*)"
    r"(?P<tail>\s*(?:;.*|\#.*)?)$"
)

try:  # ``packaging`` ships with setuptools/pip environments; fall back to pip's copy.
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion, Version
except ImportError:  # pragma: no cover - exercised only on minimal environments
    try:
        from pip._vendor.packaging.specifiers import InvalidSpecifier, SpecifierSet
        from pip._vendor.packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover
        print(
            "error: neither 'packaging' nor pip's vendored copy is importable; "
            "install it with: python -m pip install packaging",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


def project_python_floor():
    """Return the lowest Python version ``pyproject.toml`` supports, e.g. ``"3.10"``."""
    try:
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        pyproject = ""
    match = re.search(r"^requires-python\s*=\s*[\"'][^0-9]*([0-9]+\.[0-9]+)", pyproject, re.M)
    if match:
        return match.group(1)
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def supports_python(files, target_python):
    """True when any non-yanked file in a release installs on ``target_python``."""
    for item in files:
        requires = (item.get("requires_python") or "").strip()
        if not requires:
            return True  # No metadata: pip would consider it installable anywhere.
        try:
            if SpecifierSet(requires).contains(target_python, prereleases=True):
                return True
        except (InvalidSpecifier, InvalidVersion):
            return True  # Unparseable metadata is pip's problem, not ours to veto on.
    return False


def discover_requirement_files(paths):
    """Return the requirement files to process, defaulting to root requirements*.txt."""
    if paths:
        return [Path(path) for path in paths]
    return sorted(PROJECT_ROOT.glob("requirements*.txt"))


def fetch_latest_version(name, allow_prerelease=False, timeout=DEFAULT_TIMEOUT, target_python=None):
    """Return the newest version of ``name`` installable on ``target_python``.

    Raises ``LookupError`` when PyPI cannot be reached or offers no usable release.
    """
    target_python = target_python or project_python_floor()
    request = urllib.request.Request(
        PYPI_JSON_URL.format(name=name), headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise LookupError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise LookupError(str(exc)) from exc

    releases = payload.get("releases")
    if not releases:
        raise LookupError("index returned no release list")

    candidates = []
    for raw_version, files in releases.items():
        usable = [item for item in (files or []) if not item.get("yanked")]
        if not usable:  # Withdrawn, fully yanked, or never uploaded.
            continue
        if not supports_python(usable, target_python):
            continue
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.is_prerelease and not allow_prerelease:
            continue
        candidates.append((parsed, raw_version))

    if not candidates:
        raise LookupError(f"no release supports Python {target_python}")
    return max(candidates)[1]


def plan_file(path, allow_prerelease, timeout, cache, failures, target_python=None):
    """Return ``(new_lines, changes)`` for one requirement file."""
    # newline="" keeps each line's own terminator intact, so a CRLF file is not
    # silently reflowed to LF (or vice versa) into a whole-file diff.
    with path.open("r", encoding="utf-8", newline="") as handle:
        lines = handle.read().splitlines(keepends=True)
    new_lines = list(lines)
    changes = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = REQUIREMENT_RE.match(line.rstrip("\r\n"))
        if not match:
            continue

        name = match.group("name")
        current = match.group("version")
        if name not in cache:
            try:
                cache[name] = fetch_latest_version(name, allow_prerelease, timeout, target_python)
            except LookupError as exc:
                cache[name] = None
                failures.append((name, str(exc)))
        latest = cache[name]
        if latest is None or latest == current:
            continue

        try:
            if Version(latest) <= Version(current):
                continue
        except InvalidVersion:
            continue

        ending = line[len(line.rstrip("\r\n")):]
        new_lines[index] = f"{match.group('head')}{latest}{match.group('tail')}{ending}"
        changes.append((name, current, latest))

    return new_lines, changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", help="requirement files (default: requirements*.txt)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report pending bumps without writing, exit 1 if any are found",
    )
    parser.add_argument("--dry-run", action="store_true", help="report pending bumps without writing")
    parser.add_argument(
        "--pre", action="store_true", help="consider pre-releases as upgrade candidates"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="PyPI request timeout in seconds"
    )
    parser.add_argument(
        "--python-version",
        default=None,
        metavar="X.Y",
        help="lowest Python a bumped release must support "
        "(default: requires-python in pyproject.toml)",
    )
    args = parser.parse_args(argv)

    write = not (args.check or args.dry_run)
    target_python = args.python_version or project_python_floor()
    cache = {}
    failures = []
    total = 0

    print(f"Targeting releases installable on Python {target_python}+\n")

    for path in discover_requirement_files(args.files):
        if not path.is_file():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2
        new_lines, changes = plan_file(
            path, args.pre, args.timeout, cache, failures, target_python
        )
        if not changes:
            print(f"{path}: up to date")
            continue

        total += len(changes)
        verb = "updated" if write else "outdated"
        print(f"{path}: {len(changes)} {verb}")
        width = max(len(name) for name, _old, _new in changes)
        for name, old, latest in changes:
            print(f"  {name:<{width}}  {old} -> {latest}")
        if write:
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write("".join(new_lines))

    for name, reason in failures:
        print(f"warning: could not look up {name} on PyPI ({reason})", file=sys.stderr)

    if failures:
        return 2
    if total and args.check:
        return 1
    if total and write:
        print(f"\n{total} requirement(s) rewritten. Reinstall and run the test suite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
