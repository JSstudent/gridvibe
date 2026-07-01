# Changelog

All notable changes to GridVibe will be documented in this file.

## Unreleased

- Updated dependency floors and the Windows launcher dependency bootstrap so existing virtualenvs upgrade, verify native imports, and repair stale compiled wheels before startup.
- Enabled read-only File Explorer panes for SSH sessions using SFTP with root-bound remote path validation and terminal/explorer mode switching.
- Added numbered session tabs with `Alt+1` through `Alt+9` switching based on the current tab order, while ignoring editable fields.
- Added drag-resizable terminal pane dividers with xterm refits, backend PTY resize propagation, session-local resize weight caching, and minimum pane-size guards.
- Added per-pane `-`/`+` font-size zoom controls to the read-only file editor view, covering source and Markdown preview panels.

## 1.1.0 - 2026-06-28

- Improved Linux pywebview startup by requesting the Qt backend directly, keeping QtWebEngine GPU fallback flags opt-in, and preventing terminal job-control suspension when launching the native window.
- Reduced repeated agent CLI preflight timeouts by checking local POSIX PATH before interactive shells and caching recent identical local detections.
- Added Local Repo file explorer panes with safe root-bound directory navigation, file metadata, manual refresh, light/dark explorer themes, and terminal-to-explorer pane switching.
- Added Linux desktop dependency coverage for pywebview's Qt backend and a clearer browser fallback when no GTK/Qt backend can be loaded.
- Added read-only text file previews, Markdown source/rendered preview support, sanitized Markdown HTML, code-language hints, binary-file rejection, and size-limited explorer responses.
- Added split-pane support for terminal sessions, including API coverage for appending cloned sessions to an existing group while enforcing session limits.
- Improved saved launcher preset handling and launcher settings so voice capture preferences live in App Settings instead of per-terminal voice panels.
- Improved terminal workspace behavior around explorer refreshes, pane mode switches, session deletion, replay-buffer sanitization, and local stream shutdown handling.
- Added Markdown and HTML sanitization runtime dependencies for explorer previews.
- Updated screenshots, README workspace documentation, and the voice guideline to reflect the current launcher, explorer, and microphone settings flow.
- Expanded API and session-manager tests for explorer path safety, file previews, pane switching, split sessions, saved sessions, voice preferences, and SSH/local-session error handling.
- Updated developer automation so `make check`, `make lint`, `make test`, and `make fix` bootstrap development dependencies through `.venv`.

## 0.1.0 - Initial public release

- Browser-first multi-session terminal workspace.
- SSH and local shell session launch modes.
- Saved presets with encrypted SSH passwords.
- Session groups with tab ordering and replay buffers.
- Optional native desktop window support through pywebview.
- Optional offline voice input through Vosk or faster-whisper.
