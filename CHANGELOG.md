# Changelog

All notable changes to GridVibe will be documented in this file.

## 1.1.0 - 2026-06-28

- Improved Linux pywebview startup in VMs by defaulting QtWebEngine to software rendering when launching the native window.
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
