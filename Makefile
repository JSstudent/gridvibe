.DEFAULT_GOAL := help
SHELL := /bin/bash
MAKEFLAGS += --no-print-directory

.PHONY: help venv check lint fix test test-clean run clean clear-logs cleanup

PYTHON ?= $(or $(wildcard .venv/Scripts/python.exe),$(wildcard .venv/bin/python),python)
TEST_RUNNER = tests/run_tests.py

help: ## Show available make targets.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Print .venv activation commands for all environments.
	$(info Activate .venv using the command for your shell:)
	$(info   cmd.exe:    .venv\Scripts\activate.bat)
	$(info   PowerShell: .venv\Scripts\Activate.ps1)
	$(info   Git Bash:   source .venv/Scripts/activate)
	$(info   WSL/Linux:  source .venv/bin/activate)
	@$(PYTHON) -c ""

check: lint test ## Run lint and tests.

test-clean: clear-logs test ## Clear logs and run tests.

# Basic repository validation without extra tooling.
lint: ## Run ruff linter.
	@$(PYTHON) -m ruff check .

fix: ## Run ruff with autofixes.
	@$(PYTHON) -m ruff check --fix .

test: ## Run the unit test suite.
	@$(PYTHON) $(TEST_RUNNER)

run: ## Start the application.
	@$(PYTHON) main.py

clean: ## Remove Python cache directories and .pyc files.
	@$(PYTHON) -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for pattern in ('__pycache__', '.pytest_cache', '.ruff_cache') for path in Path('.').rglob(pattern) if path.is_dir()]; [path.unlink() for path in Path('.').rglob('*.pyc') if path.is_file()]"

clear-logs: ## Truncate log files in logs/.
	@$(PYTHON) -c "from pathlib import Path; log_dir = Path('logs'); log_dir.mkdir(exist_ok=True); [path.write_text('', encoding='utf-8') for path in log_dir.glob('*.log')]"

cleanup: clean clear-logs ## Remove caches and clear logs.

