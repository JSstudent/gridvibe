#!/usr/bin/env sh

set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
cd "$PROJECT_DIR" || exit 1

fail() {
    printf '%s\n' "Error: $*" >&2
    exit 1
}

info() {
    printf '%s\n' "$*"
}

find_python() {
    if command -v python3 >/dev/null 2>&1; then
        printf '%s\n' "python3"
        return 0
    fi
    if command -v python >/dev/null 2>&1; then
        printf '%s\n' "python"
        return 0
    fi
    return 1
}

prompt_mode() {
    while :; do
        printf '\n%s\n' "Start GridVibe:" >&2
        printf '%s\n' "1) Native window" >&2
        printf '%s\n' "2) Browser" >&2
        printf '%s\n' "3) Quit" >&2
        printf '%s' "Choose 1, 2, or 3: " >&2

        if ! IFS= read -r choice; then
            printf '\n'
            exit 1
        fi

        case "$choice" in
            1|n|N|native|Native)
                printf '%s\n' "native"
                return 0
                ;;
            2|b|B|browser|Browser)
                printf '%s\n' "browser"
                return 0
                ;;
            3|q|Q|quit|Quit)
                exit 0
                ;;
            *)
                printf '%s\n' "Please choose 1, 2, or 3." >&2
                ;;
        esac
    done
}

BOOTSTRAP_PYTHON=$(find_python) || fail "Python 3 was not found on PATH."

"$BOOTSTRAP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || fail "GridVibe requires Python 3.10 or newer."

info ""
info "==============================================================="
info "                      GRIDVIBE"
info "       Multi-Session SSH Terminal Manager"
info "==============================================================="
info ""
info "Project path: $PROJECT_DIR"
info "Using bootstrap interpreter: $BOOTSTRAP_PYTHON"
info ""

if [ ! -x "$PROJECT_DIR/.venv/bin/python" ]; then
    if [ -d "$PROJECT_DIR/.venv" ]; then
        backup="$PROJECT_DIR/.venv-nonlinux-$(date +%Y%m%d%H%M%S)"
        info "Existing .venv is not usable from Linux. Moving it to:"
        info "  $backup"
        mv "$PROJECT_DIR/.venv" "$backup" || fail "Failed to move the unusable .venv aside."
    elif [ -e "$PROJECT_DIR/.venv" ]; then
        fail ".venv exists but is not a directory."
    fi

    info "Creating local Linux virtual environment..."
    "$BOOTSTRAP_PYTHON" -m venv "$PROJECT_DIR/.venv" || fail "Failed to create .venv."
fi

VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

info "Updating Python installer tooling..."
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel \
    || fail "Failed to update Python installer tooling."

info "Installing core dependencies..."
"$VENV_PYTHON" -m pip install --upgrade --upgrade-strategy eager -r "$PROJECT_DIR/requirements.txt" \
    || fail "Failed to install core dependencies."

info "Verifying core dependencies..."
"$VENV_PYTHON" -c 'import _cffi_backend, cryptography.fernet, engineio, flask, flask_socketio, paramiko, socketio; print("Core dependency import check passed.")' \
    || fail "Core dependency import check failed."

LAUNCH_MODE=$(prompt_mode)

if [ "$LAUNCH_MODE" = "native" ]; then
    info ""
    info "Installing optional desktop dependencies..."
    "$VENV_PYTHON" -m pip install --upgrade --upgrade-strategy eager -r "$PROJECT_DIR/requirements-desktop.txt" \
        || fail "Failed to install desktop dependencies. Rerun and choose Browser, or repair requirements-desktop.txt installation."

    info "Verifying optional desktop dependencies..."
    "$VENV_PYTHON" -c 'import webview; print("Desktop dependency import check passed.")' \
        || fail "Desktop dependency import check failed. Rerun and choose Browser, or repair requirements-desktop.txt installation."
fi

info ""
info "Starting GridVibe in $LAUNCH_MODE mode..."
exec "$VENV_PYTHON" "$PROJECT_DIR/webview_launcher.py" --mode "$LAUNCH_MODE"
