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

- Keep `pyproject.toml`, `gridvibe_version.py`, and `CHANGELOG.md` in sync for each release.
- Use tags in the form `v0.1.0`.
- Do not publish a release until `python tests/run_tests.py` and `python -m ruff check .` pass.
