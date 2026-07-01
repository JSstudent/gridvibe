.DEFAULT_GOAL := help
SHELL := /bin/bash
MAKEFLAGS += --no-print-directory

.PHONY: help venv dev-deps check lint fix test test-clean run clean clear-logs cleanup

VENV_DIR ?= .venv
ifeq ($(OS),Windows_NT)
VENV_PYTHON = $(or $(wildcard $(VENV_DIR)\Scripts\python.exe),$(wildcard $(VENV_DIR)/Scripts/python.exe))
else
VENV_PYTHON = $(wildcard $(VENV_DIR)/bin/python)
endif
BOOTSTRAP_PYTHON ?= python
PYTHON ?= $(or $(VENV_PYTHON),$(BOOTSTRAP_PYTHON))
DEV_DEPS_STAMP = $(VENV_DIR)/.dev-deps.stamp
TEST_RUNNER = tests/run_tests.py

help: ## Show available make targets.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "%-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: dev-deps ## Create .venv, install development dependencies, and print activation commands.
	$(info Activate .venv using the command for your shell:)
	$(info   cmd.exe:    .venv\Scripts\activate.bat)
	$(info   PowerShell: .venv\Scripts\Activate.ps1)
	$(info   Git Bash:   source .venv/Scripts/activate)
	$(info   WSL/Linux:  source .venv/bin/activate)
	@$(PYTHON) -c ""

dev-deps: $(DEV_DEPS_STAMP) ## Create .venv and install development dependencies.

$(DEV_DEPS_STAMP): requirements-dev.txt requirements.txt
	@$(BOOTSTRAP_PYTHON) -c "from pathlib import Path; import subprocess, sys; scripts = Path('$(VENV_DIR)') / ('Scripts' if sys.platform == 'win32' else 'bin'); python = scripts / ('python.exe' if sys.platform == 'win32' else 'python'); subprocess.check_call([sys.executable, '-m', 'venv', '$(VENV_DIR)']) if not python.exists() else None"
	@$(PYTHON) -m pip install --upgrade -r requirements-dev.txt
	@$(PYTHON) -c "from pathlib import Path; Path('$(DEV_DEPS_STAMP)').touch()"

check: dev-deps lint test ## Install dev dependencies, then run lint and tests.

test-clean: clear-logs test ## Clear logs and run tests.

# Basic repository validation without extra tooling.
lint: dev-deps ## Run ruff linter.
	@$(PYTHON) -m ruff check .

fix: dev-deps ## Run ruff with autofixes.
	@$(PYTHON) -m ruff check --fix .

test: dev-deps ## Run the unit test suite.
	@$(PYTHON) $(TEST_RUNNER)

run: ## Start the application.
	@$(PYTHON) main.py

clean: ## Remove Python cache directories and .pyc files.
	@$(PYTHON) -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for pattern in ('__pycache__', '.pytest_cache', '.ruff_cache') for path in Path('.').rglob(pattern) if path.is_dir()]; [path.unlink() for path in Path('.').rglob('*.pyc') if path.is_file()]"

clear-logs: ## Truncate log files in logs/.
	@$(PYTHON) -c "from pathlib import Path; log_dir = Path('logs'); log_dir.mkdir(exist_ok=True); [path.write_text('', encoding='utf-8') for path in log_dir.glob('*.log')]"

cleanup: clean clear-logs ## Remove caches and clear logs.
