# Contributing to GridVibe

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\Activate.ps1  # Windows PowerShell
pip install -r requirements-dev.txt
```

Optional extras:

```bash
pip install -r requirements-desktop.txt
pip install -r requirements-voice.txt
```

## Common Commands

```bash
make test
make lint
make fix
make check
```

On Windows without `make`, run:

```bash
python tests/run_tests.py
python -m ruff check .
```

## Pull Requests

- Keep changes focused and easy to review.
- Add or update tests for behavior changes.
- Do not commit `config.json`, `saved_sessions.json`, `.encryption_key`, logs, caches, or virtual environments.
- Do not include private hostnames, credentials, local paths, or screenshots with sensitive terminal output.

## Code Style

- Python 3.10+.
- `ruff` is the linter.
- Use `unittest` for tests.
- Prefer small helpers over large route or event-handler changes when touching shared behavior.

## Release Versioning

- Keep `pyproject.toml`, `gridvibe_version.py`, and `CHANGELOG.md` in sync for each release. This is
  now enforced: `tests/test_version.py` fails if `pyproject.toml`'s literal version disagrees with
  `gridvibe_version.__version__`, or if `CHANGELOG.md` has no dated `## <version> - YYYY-MM-DD`
  section for the current version. A version bump that forgets either one fails `make check`.
- Use annotated tags (`git tag -a`) in the form `v1.2.0`. A published tag is immutable — fix forward
  with a new patch version rather than re-pointing it.
- Do not publish a release until `python tests/run_tests.py` and `python -m ruff check .` pass, on
  the exact commit you are about to tag.
- The full procedure lives in `docs/release_and_installer_plan_2026-07-25.md`: §6 is the per-release
  checklist (roughly ten minutes), §5 covers the everyday Git workflow, and Part 2 covers the
  packaged-installer roadmap for 2.0.0.
