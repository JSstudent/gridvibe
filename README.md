<p align="center">
  <img src="docs/images/GridVibe.png" alt="GridVibe logo" width="160">
</p>

<h1 align="center">GridVibe</h1>

<p align="center">
  <b>The vibe-coding cockpit.</b><br>
  Spin up a grid of AI agent CLIs and SSH terminals in seconds, talk to them out loud,<br>
  and keep your files, Git, and a live app preview in the same window.
</p>

<p align="center">
  <a href="https://github.com/JSstudent/gridvibe/actions/workflows/ci.yml"><img src="https://github.com/JSstudent/gridvibe/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
</p>

---

## Why GridVibe

| | |
| --- | --- |
| **A grid, not a tab pile** | Pick 1, 2, 3, 4, 6, or 8 panes, pick a target per pane, hit launch. SSH, WSL, PowerShell, cmd, or a local repo — side by side, resizable, splittable. |
| **Agents are a dropdown, not a chore** | Six agent CLIs are first-class pane types, most with an **Auto mode** toggle. GridVibe detects them on the target machine before you launch, so you find out about a missing binary *before* the pane opens. |
| **Talk to your agents** | Fully offline voice input (Vosk or faster-whisper) dictates straight into any terminal — or, with a file open in the explorer's editor, straight into the file. Push-to-talk keybind included. Off by default. |
| **Set it up once** | Save a tab as a preset, save the whole workspace, restart, get it all back — right down to which group you were working in. |

Everything else — the file explorer, the Git sidebar, the browser preview — exists so you never have to leave the grid mid-flow.

## Screenshots

| Launcher | Agents| Terminal Workspace | Browser | App Settings |
| --- | --- | --- | --- | --- |
| ![GridVibe launcher with terminal count, layout, connection, and per-terminal setup controls](docs/images/screenshots/launcher.png) | ![Preset agent setup from a saved configuration](docs/images/screenshots/Agents.png) | ![GridVibe terminal workspace showing a four-pane SSH session group](docs/images/screenshots/workspace.png) | ![GridVibe app browser terminal mode with tabs](docs/images/screenshots/browser_view.png) | ![GridVibe app settings with theme, SSH host-key, and voice options](docs/images/screenshots/settings.PNG) |

## Quick Start

**Python 3.10+ is the only prerequisite.** The launcher scripts create and repair the virtual environment, install dependencies, and start the app.

```powershell
# Windows
.\START_HERE\Start GridVibe.bat
```

```bash
# Linux / macOS
sudo apt install python3 python3-venv python3-pip   # Debian/Ubuntu
chmod +x GridVibe.sh && ./GridVibe.sh
```

```bash
# Manual, any platform
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --upgrade -r requirements.txt
python main.py                   # → http://localhost:5050
```

Both launchers ask for **Desktop** (native window) or **Browser** mode. Core requirements already include `pywinpty` on Windows, so local cmd/PowerShell/WSL panes work in browser mode too. For a native window, also install `requirements-desktop.txt`.

### Getting & updating it

| | Get it | Update it |
| --- | --- | --- |
| **Clone** (recommended) | `git clone https://github.com/JSstudent/gridvibe.git` | The launcher's **Check for updates** button fast-forwards in place |
| **Release ZIP** (no Git) | **Source code (zip)** from [Releases](https://github.com/JSstudent/gridvibe/releases) | Download the next release — in-app update needs a clone, and says so |

A Windows installer that bundles Python is planned for **2.0.0**.

### Run modes

```bash
python main.py                     # browser mode on http://localhost:5050
python main.py --host 0.0.0.0      # bind all interfaces (opt-in)
python main.py --port 8080         # custom port
python webview_launcher.py         # auto: native window, browser fallback
python webview_launcher.py --mode browser|native
```

Browser mode is one browser window per run: the launcher opens in a **new window** of your default browser, and each workspace opens as a tab beside it. Browsers allow a page to open only one tab per click, so restoring several workspaces at once opens the first and reports the rest — allow pop-ups for GridVibe's address to have them all open automatically, or use **Open** in the launcher's Workspaces card.

## Agent CLIs

Pick an agent per pane in the launcher. GridVibe checks whether the binary is on `PATH` **in the target environment** (the remote host for SSH, the chosen distro for WSL, Windows for PowerShell/cmd) and shows install guidance when it isn't.

| Agent | Binary | Auto mode |
| --- | --- | --- |
| Claude Code | `claude` | Yes |
| OpenAI Codex CLI | `codex` | Yes |
| GitHub Copilot CLI | `copilot` | Yes |
| OpenCode CLI | `opencode` | — |
| Kilo CLI | `kilo` | Yes |
| Kimi Code CLI | `kimi` | Yes |

GridVibe does not bundle the CLIs. If everything shows `Missing`, install it and put its folder on `PATH` — for npm-installed agents on Windows that is usually `%APPDATA%\npm` (check with `npm prefix -g`). Restart GridVibe after PATH changes.

## Voice Input

Optional, fully offline, **off by default**. Turn it on in **App Settings** (the gear on either page), pick a backend (`Vosk` or `faster-whisper`), a language, and optionally a capture profile, microphone, and push-to-talk keybind. Then hit the 🎙️ button in any terminal pane's header.

Open a file in the explorer's in-place editor and it gets its own 🎙️, beside **Save** and **Cancel** — dictation lands at the caret, undoes with `Ctrl+Z` in one step, and saves through the same revision check as anything you typed. Save is held while dictation is in flight so the two can't overwrite each other, and leaving edit mode stops the mic. Only one pane records at a time, terminals and editors alike. Push-to-talk reaches the editor only if your keybind uses `Ctrl`, `Alt`, or `Cmd` — a bare letter would be swallowed out of the file you're editing.

If the packages are missing, App Settings says so and offers **Install voice dependencies** — installed into GridVibe's own environment and loaded without a restart. Or do it yourself:

```bash
python -m pip install --upgrade -r requirements-voice.txt
```

Browser mode is the most reliable for microphone permissions. Settings apply live to open workspace tabs. Details: [`docs/voice_guideline.md`](docs/voice_guideline.md).

## Sessions & Workspace

| | |
| --- | --- |
| **Session tabs** | Keep related panes together in draggable tabs. Use `Alt+1`–`Alt+9` to switch, middle-click to close, or broadcast typing to every pane in the active tab. |
| **Saved sessions** | Save a setup as a reusable preset, import one later, or choose **New Session** for a clean start. Stored SSH passwords are encrypted. |
| **Save & restore** | GridVibe autosaves your workspace and also offers **Save Workspace**. After a restart, restore the same tabs, pane layouts, directories, commands, active group, and explorer presentation; passwords are never written to the workspace snapshot. Lowering `max_sessions` does not truncate wider stored presets or rewrite their split geometry; a group that no longer fits is refused with the number to raise the setting to, and the stored preset and snapshot are left untouched. A snapshot damaged outside GridVibe fails as a whole tab rather than restoring one pane short, so the restore chooser's counts always match what a restore starts; window chrome such as top-bar visibility and Markdown appearance falls back to its default instead. |
| **Close & restart** | Voluntary close, manual restart, and update restart share one in-page choice: continue without saving, save every open workspace, or save every open session preset and then every workspace. GridVibe waits for each live workspace window to flush its current presentation; if a requested save fails, GridVibe stays open and leaves the same three choices available. |
| **Multiple workspaces** | Optionally keep separate projects in separate windows, move tabs between them without restarting terminals, and switch with `Alt+W` / `Alt+Shift+W` — including from the launcher, where `Alt+W` goes back to the workspace that opened it. |
| **Updates** | **Check for updates** fast-forwards a Git clone, then uses the same save-or-restart choices as a manual restart. |

Closing a workspace ends its terminals but keeps it available to restore. **Close and forget** removes both the live workspace and its snapshot, while closing only the window leaves its terminals running. Closing the last tab removes an empty workspace.

## File Explorer

Swap any pane between a terminal and a file explorer with one button — same directory, no re-navigation. Works on a local repo folder or a remote host over SFTP.

| | |
| --- | --- |
| **Browse & preview** | Use breadcrumbs, a lazy file tree, draggable file tabs, syntax-coloured source, rendered Markdown and Mermaid, inline images, downloads, and `Ctrl+F` find. Markdown is rendered the first time you select the **Preview** tab rather than on every open, and diagrams draw as they scroll into view. |
| **Edit** | Edit complete UTF-8 text files in place and save with `Ctrl+S`. Saves are atomic, and a conflict prompt protects files changed on disk. With voice input on, the editor gets its own 🎙️ button and dictates at the caret. |
| **Git** | See branch and file status, inspect current or historical diffs, and stage, unstage, commit, publish, or discard changes. The sidebar follows the directory being browsed and names its repository, so an explorer opened above one or several repositories acts on the repository you navigated into. Diff views also support line and block undo. The commit graph has its own find box behind the magnifying glass in the Graph header: type to highlight every matching commit message in the loaded graph, with a match counter and `Enter`/`Shift+Enter` (or the ↑/↓ buttons) to step through the hits — the same controls as the `Ctrl+F` file find — and `Esc` or the magnifier again to put it away. It searches the commit subjects the graph is showing, so it finds what is on screen rather than replacing `git log --grep`. |
| **Very large files & diffs** | Past ~20,000 lines or 4 MiB a file opens in a **large file view**: plain text, no syntax colour, line numbers, folding, change marks or overview ruler, and no find — a notice at the top of the pane names each one. Download and Edit still work. Below that tier, non-trivial Source files paint readable plain rows first and gain syntax colour when a bounded background worker finishes; small files keep the instant synchronous path. Large diffs degrade in two steps, giving up intraline emphasis and then syntax colour, but always keeping side-by-side layout, line numbers, and line and block undo; the largest tier's parse runs in that same background pool. |
| **Search** | Press `Ctrl+Shift+F` for repository-wide search with case, whole-word, regex, file-pattern, scope, and `.gitignore` controls. |
| **Find a file** | Type in the Files tree's filter box to find files and folders anywhere under the root **by name**, with the matched part highlighted in place. Same case, whole-word, and regex toggles; `Enter` opens the first hit, `Esc` clears the filter. |
| **Fold a level** | `Alt`-click a folder's fold arrow in the Files tree to fold or unfold **every folder beside it** at once. The new state mirrors the folder you clicked, so `Alt`-clicking any open top-level folder folds the whole tree in one go. Folding a level also forgets what was open inside it, so those folders reopen clean, and the tree scrolls to keep the folder you clicked in view instead of jumping once its rows disappear. |
| **Restore fidelity** | Saved sessions and workspaces preserve the explorer root, ordered file tabs, Preview/Source/Diff intent, per-panel and directory scroll, wrapping, folds, sidebar width/scroll, Files-tree expansion, Git commit expansion, theme, and workspace-wide Markdown/source appearance. Content-relative scroll and folds restore only when their file, directory, or rendered Diff revision still matches; queries and fetched results are always refetched, never stored. |
| **Manage files** | Create, copy, move, rename, and delete from the context menu. Every write stays inside the explorer root; collisions never overwrite existing files, and deletion requires confirmation. |
| **Select several** | `Ctrl`-click rows to add or remove them, `Shift`-click for a range. Right-click inside the selection and Copy, Cut, Delete, Download, and Copy path act on all of it, with one confirmation and one result for the batch; right-click anywhere else drops back to that single row. A plain click still just opens the file. Rename stays single-entry. |

Uploading, cross-root transfers, and Git checkout, pull, or merge are intentionally left to the terminal.

## Switching a Local Repo Pane's Shell

Launched a pane in cmd and wanted PowerShell — or WSL? Click the pane's 🔄 button: on a Local Repo terminal it's a dropdown with **Reset view** on top and a **Shell** section listing **Command Prompt**, **PowerShell**, **WSL** (default distro) and every detected distro. Picking one restarts that pane's shell in place — same slot, same title, same startup command — starting in the directory the old shell was sitting in. Windows hosts only; SSH, explorer, and browser panes keep the plain one-click reset.

## Browser Preview

Flip a Local Repo pane to a browser preview (the 🌐 button) and watch the app you're building next to the terminal running it.

- **Tabbed** — up to 8 tabs, per-tab close, drag to reorder, **+** opens a blank tab at `http://127.0.0.1:3000`. Each tab keeps its own live frame, so switching or reordering never reloads your app.
- **URL bar** navigates the active tab (http/https only); **Open** kicks it out to a real OS browser tab.
- **Same-origin popups get captured** into new pane tabs instead of escaping — which is what lets you drive GridVibe's own launcher → workspace flow inside a pane. Named window targets reuse their tab rather than stacking up. Cross-origin pages can't be instrumented by anyone, so their popups still open externally.
- **Nested preview is capped one level deep** — a GridVibe page already inside a pane shows a *Nested preview disabled* notice instead of re-embedding itself forever.
- The whole tab strip saves and restores with the workspace and with session presets.

GridVibe does not proxy pages or bypass `X-Frame-Options`/CSP, so sites that block embedding need **Open**.

## Icons & Shortcuts

**Pane header:**

| | Does |
| :---: | --- |
| 🔄 | Reset the view and replay recent output (reloads explorer and browser panes). On a Local Repo terminal it opens a dropdown: **Reset view** plus a **Shell** section that restarts the pane in cmd, PowerShell, or a WSL distro |
| 📁 ⇄ 💻 | Swap between terminal and file explorer at the current directory |
| 🌐 ⇄ 💻 | Swap a Local Repo pane between terminal and browser preview |
| 🪟 | Split side-by-side or stacked. A terminal clones its connection; an explorer or browser pane splits off a terminal instead — for both SSH and Local Repo — rooted where the explorer is currently browsing |
| 🧹 | Clear the display and purge the replay buffer |
| 🎙️ | Start/stop voice input (when enabled) |
| 🌙 ⇄ ☀️ | Toggle an explorer pane between dark and light |
| ⋯ | Overflow menu, shown when the pane is too narrow for the full row |
| ✖️ | Close the pane (confirms first — it's a live session) |

**Explorer bar:**

| | Does |
| :---: | --- |
| 🔄 | Refresh the explorer (`F5`) |
| ⬆️ | Go to the parent directory (or mouse Back) |
| 🗂️ | Files tree sidebar |
| ⎇ | Git changes and history sidebar |
| 🔍 | Repository search sidebar (`Ctrl+Shift+F`) |
| 🖥️ | Reveal the current location in the system file manager (local panes only) |

**Top bar:** theme · max surface · broadcast typing · fullscreen · App Settings · chevron to hide the bar. Plus a `Workspace…` menu and a `Sessions…` menu.

**A hidden top bar comes back on hover.** Rest the pointer on the small handle at the top edge, centred — only that handle triggers it, so the rest of the edge is free — and the bar slides down over the workspace for as long as you are using it, so `Save Session` and `Save Workspace` stay reachable with the bar hidden. It stays while a menu is open and hides again shortly after you move away (or on `Esc`); clicking the handle reveals the bar and puts focus in it, which is the keyboard route in. Fullscreen hides the bar for its duration and reveals it the same way; leaving fullscreen gives back whatever the chevron last said. Nothing about the reveal is saved.

**Session tab line:** the back-to-launcher button sits at the head of the tab line, ahead of the first tab, so it stays reachable with the top bar hidden.

| Shortcut | Action |
| --- | --- |
| ``Alt+` `` | Open the launcher (the key left of `1`) |
| `Alt+1`–`Alt+9` | Switch session group |
| `Alt+W` / `Alt+Shift+W` | Next / previous workspace window (multiple workspaces only) — the window you land in pulses once. In the launcher, `Alt+W` returns to the workspace that opened it, or to whichever workspace is still open if that one has closed |
| `Ctrl+Shift+F` | Terminal scrollback search — or, on an explorer pane, toggle repository search |
| `Ctrl+F` | Find in the open file |
| `Ctrl+Shift+V` | Toggle Markdown rendered preview |
| `Ctrl+S` / `Esc` | Save / cancel in the explorer editor |
| `F5` | Refresh the focused explorer |

Drag the dividers between panes to resize them.

## Configuration

Everything lives in **App Settings** — same dialog from the gear on the launcher *or* the session window, so settings never need a trip back to the launcher. It covers theme, surface mode, terminal font and size, max sessions, shell integration, workspace autosave interval, SSH host-key policy, and all voice options. The one exception is **Multiple workspaces**: it changes what every launch does, so its switch sits in the launcher's Workspaces card instead of the dialog.

On disk, settings load from `config.json` (git-ignored) falling back to `default_config.json`:

```json
{
  "server": { "host": "127.0.0.1", "port": 5050 },
  "appearance": { "theme": "dark" },
  "terminal": { "max_sessions": 16, "font_size": 14, "shell_integration": true },
  "workspace": { "surface_mode": "normal", "autosave_interval_minutes": 5, "multi_workspace_enabled": false },
  "ssh": { "host_key_policy": "auto-add" },
  "explorer_search": { "max_files": 2000, "max_matches": 5000, "timeout_seconds": 20 }
}
```

GridVibe generates a Flask session signing key at startup unless `GRIDVIBE_SECRET_KEY`, `SECRET_KEY`, or `security.secret_key` is set.

### Shell integration

`terminal.shell_integration` (on by default) is what lets GridVibe follow the directory a terminal is *in*, rather than the one it was launched in — which is what the file explorer opens on when you switch a pane over to it.

It adds an invisible escape sequence to the shell's prompt carrying the current directory (`OSC 7` for bash/zsh/WSL and remote shells, `OSC 9;9` for PowerShell and cmd), then reads that out of the terminal's own output. It never types at your prompt to ask, so the answer is still right while a build, a pager, a TUI, or an agent is running — and a pane running an agent reports the directory the agent was started in.

A **local** shell is handed the hook when GridVibe starts it — cmd through its `PROMPT` environment variable, bash through `PROMPT_COMMAND` (forwarded into WSL with `WSLENV`), PowerShell as a `-Command` argument — so nothing is typed and nothing appears in the pane. A **remote** shell cannot be handed anything (`sshd` forwards only what its `AcceptEnv` allows, and writing a file to your server is not something a terminal should do), so SSH panes are sent one short line at startup, which the shell echoes like any other command.

What it costs, stated plainly: that one echoed line on SSH panes; an existing bash/zsh prompt hook is kept and appended to, and PowerShell's `prompt` function is wrapped rather than replaced, but **cmd's `PROMPT` is replaced** with the default `$P$G` plus the sequence. On a local shell whose own startup files set `PROMPT_COMMAND` themselves, the inherited hook is overwritten and GridVibe falls back to reading the OS. Turn the setting off in App Settings to leave every prompt untouched — GridVibe still reads the sequence if your own shell configuration happens to emit it, and otherwise falls back to asking the shell directly when it is idle.

## Security

GridVibe is a local tool, not a public web service: it binds to `127.0.0.1` by default, has no built-in authentication, and should not be exposed to the internet.

- Socket.IO CORS defaults to same-origin, following the address the server actually resolved (so `--host`/`--port` are covered) plus the host each request was addressed to; state-changing cross-origin requests are rejected on the same rule. Set `security.cors_origins` only if you serve GridVibe from another origin — an explicit list is used verbatim and replaces both defaults.
- SSH host keys persist to `.known_hosts`; `ssh.host_key_policy` can be `auto-add` (default), `known-hosts`, or `strict`.
- Saved SSH passwords are Fernet-encrypted; the key lives in `.encryption_key`.

See [`SECURITY.md`](SECURITY.md) for reporting and scope.

## Development

```bash
make check                      # test + lint, run this before handing work back
make test lint fix              # individually

# Windows without make:
python tests/run_tests.py
python -m ruff check .
```

Backend lives in the modular `web/` package (`app.py`, `api.py`, `agents.py`, `terminal_io.py`, `explorer.py`, `explorer_search.py`, `voice.py`, …), session state in `sessions/manager.py`, the voice service in `services/`, and the two pages in `templates/` with assets in `web/static/`. Root-level `api.py`, `session_manager.py`, `cleanup.py`, and `webview_launcher.py` are compatibility shims — edit the canonical modules.

More: [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`CHANGELOG.md`](CHANGELOG.md) · [`docs/logging_guide.md`](docs/logging_guide.md) · [`docs/voice_guideline.md`](docs/voice_guideline.md)

## Local Files

Created at runtime, never committed:

| File | Purpose |
| --- | --- |
| `config.json` | Local runtime configuration override |
| `saved_sessions.json` | Saved launcher presets (encrypted passwords) |
| `runtime_state.json` | Workspace-shape snapshot for restore-after-restart |
| `.known_hosts` | Persisted SSH host keys |
| `.encryption_key` | Fernet key for password encryption |
| `logs/gridvibe.log` | Main rotating log file |

Both JSON state files are written the same careful way: one change at a time under an OS-level `<file>.lock`, committed through a scratch file and an atomic replace, with the previous version kept as `<file>.bak`. A file GridVibe cannot read is moved aside as `<file>.corrupt-<timestamp>` and the backup is loaded in its place, rather than being reported as empty and overwritten. A save that does not reach the disk is reported as a retryable failure, never as success. Those sidecar files are local state and are gitignored alongside the files they protect.

## License

MIT. See [`LICENSE`](LICENSE).
