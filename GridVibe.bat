@echo off
setlocal enabledelayedexpansion
title GridVibe
color 0B

echo.
echo  ===============================================================
echo                        GRIDVIBE
echo         Multi-Session SSH Terminal Manager
echo  ===============================================================
echo.
echo  Server URL: http://localhost:5050
echo.
echo  The launcher will minimize after startup.
echo  In native mode, closing the settings window stops the server.
echo.

:: %~dp0 is the folder containing this .bat file (the project root)
set "PROJECT_DIR=%~dp0"
:: Remove trailing backslash
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"

echo  Project path: %PROJECT_DIR%
echo.
:: Change to project directory
cd /d "%PROJECT_DIR%"

set "BOOTSTRAP_PYTHON="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3.12"
)
if not defined BOOTSTRAP_PYTHON (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3.13 -c "import sys" >nul 2>&1
        if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3.13"
    )
)
if not defined BOOTSTRAP_PYTHON (
    where py >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3"
)
if not defined BOOTSTRAP_PYTHON (
    where python >nul 2>&1
    if not errorlevel 1 set "BOOTSTRAP_PYTHON=python"
)

if not defined BOOTSTRAP_PYTHON (
    echo  Error: Python was not found on PATH.
    echo  Install Python 3 for Windows, then run this file again.
    echo.
    pause >nul
    exit /b 1
)

echo  Using bootstrap interpreter: %BOOTSTRAP_PYTHON%
echo.

set "NEEDS_VENV_CREATE="
if not exist ".venv\Scripts\python.exe" (
    set "NEEDS_VENV_CREATE=1"
)

if not defined NEEDS_VENV_CREATE (
    ".venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo  Existing .venv is not usable from Windows. Recreating it...
        echo.
        if exist ".venv-wsl-broken" rmdir /s /q ".venv-wsl-broken"
        move ".venv" ".venv-wsl-broken" >nul 2>&1
        if errorlevel 1 (
            echo  Error: Failed to move the broken .venv aside.
            echo  Close any processes using .venv and run GridVibe.bat again.
            echo.
            pause >nul
            exit /b 1
        )
        set "NEEDS_VENV_CREATE=1"
    )
)

if defined NEEDS_VENV_CREATE (
    echo  Creating local Windows virtual environment...
    echo.
    call %BOOTSTRAP_PYTHON% -m venv .venv
    if errorlevel 1 (
        echo  Error: Failed to create .venv.
        echo  Command used: %BOOTSTRAP_PYTHON% -m venv .venv
        echo.
        pause >nul
        exit /b 1
    )
)

set "VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"

echo  Updating Python installer tooling...
echo.

"%VENV_PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo  Error: Failed to update Python installer tooling.
    echo  Manual fix: "%VENV_PYTHON%" -m pip install --upgrade pip setuptools wheel
    echo.
    pause >nul
    exit /b 1
)

echo  Installing core dependencies...
echo.

"%VENV_PYTHON%" -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
if errorlevel 1 (
    echo  Error: Failed to install core dependencies.
    echo  Manual fix: "%VENV_PYTHON%" -m pip install --upgrade -r requirements.txt
    echo.
    pause >nul
    exit /b 1
)

echo  Verifying core dependencies...
echo.

"%VENV_PYTHON%" -c "import _cffi_backend, cryptography.fernet, engineio, flask, flask_socketio, paramiko, socketio; print('Core dependency import check passed.')"
if errorlevel 1 (
    echo  Core dependency import check failed. Reinstalling native wheels...
    echo.
    "%VENV_PYTHON%" -m pip install --upgrade --force-reinstall --no-cache-dir cffi cryptography pynacl bcrypt paramiko
    if errorlevel 1 (
        echo  Error: Failed to repair native core dependencies.
        echo  Manual fix: "%VENV_PYTHON%" -m pip install --upgrade --force-reinstall --no-cache-dir cffi cryptography pynacl bcrypt paramiko
        echo.
        pause >nul
        exit /b 1
    )
    "%VENV_PYTHON%" -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
    if errorlevel 1 (
        echo  Error: Failed to reinstall core dependencies after repair.
        echo.
        pause >nul
        exit /b 1
    )
    "%VENV_PYTHON%" -c "import _cffi_backend, cryptography.fernet, engineio, flask, flask_socketio, paramiko, socketio; print('Core dependency import check passed.')"
    if errorlevel 1 (
        echo  Error: Core dependencies are still not importable after repair.
        echo.
        pause >nul
        exit /b 1
    )
)

echo.
echo  Choose how to start GridVibe:
echo    [D] Desktop window
echo    [B] Browser
echo    [Q] Quit
echo.
choice /C DBQ /N /M " Select [D/B/Q]: "
if errorlevel 3 exit /b 0
if errorlevel 2 (
    set "LAUNCH_MODE=browser"
    goto check_voice_dependencies
)
set "LAUNCH_MODE=auto"

:install_desktop_dependencies
echo  Installing optional desktop dependencies...
echo.

"%VENV_PYTHON%" -m pip install --upgrade --upgrade-strategy eager -r requirements-desktop.txt
if errorlevel 1 (
    echo  Warning: Failed to install optional desktop dependencies.
    echo  GridVibe will still run, but may fall back to the browser.
    echo  Manual fix: "%VENV_PYTHON%" -m pip install --upgrade -r requirements-desktop.txt
    echo.
)

echo  Verifying optional desktop dependencies...
echo.

"%VENV_PYTHON%" -c "import importlib, sys; import webview; importlib.import_module('winpty') if sys.platform == 'win32' else None; print('Desktop dependency import check passed.')"
if errorlevel 1 (
    echo  Desktop dependency import check failed. Reinstalling native desktop wheels...
    echo.
    "%VENV_PYTHON%" -m pip install --upgrade --force-reinstall --no-cache-dir pywebview pywinpty
    if errorlevel 1 (
        echo  Warning: Failed to repair optional desktop dependencies.
        echo  GridVibe will still run, but may fall back to the browser.
        echo.
    ) else (
        "%VENV_PYTHON%" -c "import importlib, sys; import webview; importlib.import_module('winpty') if sys.platform == 'win32' else None; print('Desktop dependency import check passed.')"
        if errorlevel 1 (
            echo  Warning: Optional desktop dependencies are still not importable after repair.
            echo  GridVibe will still run, but may fall back to the browser.
            echo.
        )
    )
)

:check_voice_dependencies
echo  Checking optional voice dependencies...
echo.

"%VENV_PYTHON%" -c "import faster_whisper, numpy, vosk, websockets; print('Voice dependency import check passed.')"
if errorlevel 1 (
    echo.
    echo  Voice input needs optional packages for Vosk and faster-whisper, or an installed voice package needs repair.
    choice /C YN /N /M " Install optional voice dependencies now? [Y/N] "
    if errorlevel 2 (
        echo.
        echo  Skipping voice dependencies. GridVibe will run, but voice input may be unavailable.
        echo  Manual fix: "%VENV_PYTHON%" -m pip install -r requirements-voice.txt
        echo.
    ) else (
        echo.
        echo  Installing optional voice dependencies...
        echo.
        "%VENV_PYTHON%" -m pip install --upgrade --upgrade-strategy eager -r requirements-voice.txt
        if errorlevel 1 (
            echo  Warning: Failed to install optional voice dependencies.
            echo  GridVibe will still run, but voice input may be unavailable.
            echo  Manual fix: "%VENV_PYTHON%" -m pip install --upgrade -r requirements-voice.txt
            echo.
        ) else (
            "%VENV_PYTHON%" -c "import faster_whisper, numpy, vosk, websockets; print('Voice dependency import check passed.')"
            if errorlevel 1 (
                echo  Voice dependency import check failed. Reinstalling native voice wheels...
                echo.
                "%VENV_PYTHON%" -m pip install --upgrade --force-reinstall --no-cache-dir numpy ctranslate2 onnxruntime av faster-whisper vosk websockets
                if errorlevel 1 (
                    echo  Warning: Failed to repair optional voice dependencies.
                    echo  GridVibe will still run, but voice input may be unavailable.
                    echo.
                ) else (
                    "%VENV_PYTHON%" -c "import faster_whisper, numpy, vosk, websockets; print('Voice dependency import check passed.')"
                    if errorlevel 1 (
                        echo  Warning: Optional voice dependencies are still not importable after repair.
                        echo  GridVibe will still run, but voice input may be unavailable.
                        echo.
                    )
                )
            )
        )
    )
)

echo.
echo  Starting GridVibe...
echo.

:: Hand off the long-running launcher to a minimized console window.
start "GridVibe" /min cmd /c ""%VENV_PYTHON%" "%PROJECT_DIR%\webview_launcher.py" --mode %LAUNCH_MODE%"
if errorlevel 1 (
    echo  Error: Failed to start GridVibe.
    echo.
    pause >nul
    exit /b 1
)

exit /b 0
