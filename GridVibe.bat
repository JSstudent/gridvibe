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
echo  GUI: Native window on http://localhost:5050
echo.
echo  The terminal will minimize after launch.
echo  Close the settings window to stop the server.
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

echo  Installing core dependencies...
echo.

"%VENV_PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo  Error: Failed to install core dependencies.
    echo  Manual fix: "%VENV_PYTHON%" -m pip install -r requirements.txt
    echo.
    pause >nul
    exit /b 1
)

echo  Installing optional desktop dependencies...
echo.

"%VENV_PYTHON%" -m pip install -r requirements-desktop.txt
if errorlevel 1 (
    echo  Warning: Failed to install optional desktop dependencies.
    echo  GridVibe will still run, but may fall back to the browser.
    echo  Manual fix: "%VENV_PYTHON%" -m pip install -r requirements-desktop.txt
    echo.
)

echo.
echo  Starting GridVibe...
echo.

:: Hand off the long-running launcher to a minimized console window.
start "GridVibe" /min cmd /c ""%VENV_PYTHON%" "%PROJECT_DIR%\webview_launcher.py""
if errorlevel 1 (
    echo  Error: Failed to start GridVibe.
    echo.
    pause >nul
    exit /b 1
)

exit /b 0

