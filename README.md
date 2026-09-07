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

Choose **Quit** to exit before environment setup; closing the input stream also
exits the POSIX launcher. After a successful setup, an unchanged environment
passes an import check and starts without running pip, including offline.
Changed requirements, interpreter identity, installed package versions, or a
failed import check trigger setup again. Optional desktop packages are checked
when that mode is selected; Windows retains its optional voice-package choice.

To force dependency installation and verification, run the root launcher:

```powershell
.\GridVibe.bat --repair          # Windows, from the project root
```

```bash
./GridVibe.sh --repair           # Linux / macOS
```

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

In **native desktop mode** the launcher and each workspace are real OS windows, so they can be managed as a set: **Minimize all** (`Alt+X`, or the button in either top bar) sends every GridVibe window to the taskbar at once, and App Settings can make minimizing any one of them do the same. Clicking a taskbar entry always brings back just that window.

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
| Grok Build (xAI) | `grok` | Yes |
| Hermes Agent | `hermes` | Yes |

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
| **Save & restore** | GridVibe autosaves your workspace and also offers **Save Workspace**. After a restart, restore the same tabs, pane layouts, commands, active group, and explorer presentation; each pane comes back in the directory it was *working in*, falling back to the one it was launched in if that directory is gone. Passwords are never written to the snapshot. Lowering `max_sessions` never rewrites a stored preset — a group that no longer fits is refused, with the number to raise the setting to. A snapshot damaged outside GridVibe fails as a whole tab rather than restoring one pane short, so the restore chooser's counts always match what a restore starts. |
| **Close & restart** | Voluntary close, manual restart, and update restart share one in-page choice: continue without saving, save every open workspace, or save every session preset and then every workspace. GridVibe waits for each live workspace window to flush what it is showing; if a requested save fails, it stays open with the same three choices. |
| **Multiple workspaces** | Optionally keep separate projects in separate windows, move tabs between them without restarting terminals, and switch with `Alt+W` / `Alt+Shift+W` — including from the launcher, where `Alt+W` goes back to the workspace that opened it. |
| **Updates** | **Check for updates** fast-forwards a Git clone, then uses the same save-or-restart choices as a manual restart. |

Closing a workspace ends its terminals but keeps it available to restore. **Close and forget** removes both the live workspace and its snapshot, while closing only the window leaves its terminals running. Closing the last tab removes an empty workspace.

## Agent Dashboard

The dashboard button — beside the session menu in a workspace window, and in the
launcher's control row — opens a **window of its own** (`Alt+A`) listing **every
agent you have running, in every workspace**. It is one window however many
times you ask for it, and it stays open beside your work: press again, or
`Alt+A` from anywhere, and it comes to the front rather than opening a second
copy. The button carries a badge with the number of agents running right now.

It lists agents and nothing else. Plain terminals, file explorers and browser
panes are already in front of you in the window that holds them; a workspace or
a session tab with no agent in it is not listed at all. Three levels, drawn as
three different things:

- a **workspace** is a titled band across the window;
- a **session tab** is a card inside that band, several across a wide window;
- an **agent** is a row inside the card, tagged with what it is running on —
  `SSH`, `WSL` (with the distro when there is one), `PowerShell` or `cmd` — and
  marked `auto` when it was launched with its agent's own auto-approval flag.

Click any row — an agent, its session or its workspace — to open (or focus) the
window that owns it, at that session tab. The dashboard stays where it is.
While the dashboard has focus, the launcher and workspace pages behind it are
softly blurred so the active window is obvious. Focusing another GridVibe
window removes the blur immediately; closing, hiding, or crashing the dashboard
also releases it automatically.

Each agent row carries two readings, both taken from the pane's own output — no
probe is ever sent, and nothing is typed into a running agent:

- **Which chat is active.** GridVibe reads both terminal tab-title (`OSC 1`) and
  window-title (`OSC 0` / `OSC 2`) announcements and shows the most specific
  useful title. Codex sessions launched by GridVibe request Codex's
  `thread-title`, so renaming or switching the current Codex conversation is
  reflected here. Provider status marks and generic labels such as `Codex` or
  `kimi-code` are removed. If an agent publishes no meaningful chat title, the
  row falls back to a custom pane title and then its directory; GridVibe cannot
  recover a chat name that the agent never emits.
- **Whether it is doing anything.** A pane that has written something in the
  last few seconds reads as *working*; one that has gone quiet reads as *idle*,
  with how long it has been waiting. Title-only and terminal-control updates do
  not count as work. Agents that publish the terminal progress sequence (`OSC
  9;4`) also get a current percentage or error state; stale progress is not
  presented as current. A pane that is not connected says so instead.

A pane running an agent also **names itself after that agent** in its own
header: `Terminal 1` becomes `Claude Code`, `OpenAI Codex CLI`, and so on —
including when you point an open pane at a different agent from its reset menu,
which renames the header on the spot. The agent's own icon sits beside that
name and changes with it. A title you typed yourself always wins, and the
agent's name is never saved as the pane's title.

The dashboard adapts from a full-width desktop view down to a narrow window,
wrapping metadata and activity without horizontal scrolling. Polls are bounded,
cancelled while hidden, and update existing rows in place so a refreshed title
or idle time does not disturb focus, selection, or scroll position. A failed
refresh leaves the last good reading visible and offers a retry.

## File Explorer

Swap any pane between a terminal and a file explorer with one button — same directory, no re-navigation. The explorer roots on the Git repository containing that directory, so the Git sidebar works and you can navigate up to the repository's own root; it never widens above the folder the pane was launched in unless the shell has itself walked out of it. Works on a local repo folder or a remote host over SFTP.

When the pane cannot say where it is — the shell never answered, or the pane is running an agent and so has no prompt to read — the explorer opens on the directory the pane launched in and says so in a dismissible bar naming the folder it actually opened.

| | |
| --- | --- |
| **Browse & preview** | Breadcrumbs, a lazy Files tree, draggable file tabs, syntax-coloured source, rendered Markdown and Mermaid, inline images, downloads, and `Ctrl+F` find. Markdown renders the first time you select **Preview**, and diagrams draw as they scroll into view. |
| **Edit** | Edit complete UTF-8 text files in place and save with `Ctrl+S`. Saves are atomic, a conflict prompt protects files changed on disk, and `Ctrl+Shift+E` opens the editor and closes it again. With voice input on, the editor gets its own 🎙️ and dictates at the caret. |
| **Search** | `Ctrl+Shift+F` for repository-wide search, with case, whole-word, regex, file-pattern, scope, and `.gitignore` controls. |
| **Find a file** | The Files tree's filter box finds files and folders anywhere under the root **by name**, with the matched part highlighted in place. Same case, whole-word, and regex toggles; `Enter` opens the first hit, `Esc` clears. |
| **Fold a level** | `Alt`-click a folder's fold arrow to fold or unfold **every folder beside it** at once — so `Alt`-clicking any open top-level folder folds the whole tree in one go. The tree keeps the folder you clicked in view. |
| **Manage files** | Create, copy, move, rename, and delete from the context menu. Every write stays inside the explorer root, collisions never overwrite an existing file, and deletion asks first. |
| **Upload files** | Send files in through the usual picker — offered anywhere Download is, plus every folder row, the blank space of the tree and the listing, and a button in the explorer bar. Local or SFTP, multi-select, 100 MB per file, one confirmation past ten files and one result for the batch. **An existing file is never replaced**: a name already in use is numbered instead (`report (1).pdf`), and the result says which name it used. |
| **Select several** | `Ctrl`-click rows to add or remove them, `Shift`-click for a range. Copy, Cut, Delete, Download, and Copy path act on the whole selection, with one confirmation and one result; right-click outside it drops back to that single row. Rename and Upload stay single-entry. |
| **Very large files & diffs** | Past ~20,000 lines or 4 MiB a file opens in a plain **large file view** — no syntax colour, folding, change marks, overview ruler, or find — with a notice naming each one; Download and Edit still work. Below that tier, source paints as readable plain rows first and gains colour when a background worker finishes. Large diffs drop intraline emphasis and then syntax colour, but always keep side-by-side layout, line numbers, and line and block undo. |
| **Restore fidelity** | Saved sessions and workspaces bring back the explorer root, ordered file tabs, Preview/Source/Diff intent, scroll, wrapping, folds, sidebar width, Files-tree and commit expansion, theme, and Markdown appearance. Scroll and folds restore only while the file, directory, or diff still matches; queries and fetched results are always refetched, never stored. |

Repository content search and name find use Python regular-expression syntax
with the same case and whole-word rules across Git, local walks, and SSH
fallbacks. Search stops at its configured limits and marks partial results;
remote command failures show an error with a retry action. Emoji before a match
do not shift its highlight.

On Linux/macOS, move or rename is refused if the
filesystem cannot guarantee that a concurrent destination will be preserved.

### Git sidebar

- **Status & history** — repository, branch (or a detached HEAD named the way `git branch` names it), staged and unstaged changes, and the commit graph. Diffs of the working tree or of any commit, with per-line and per-block undo.
- **Actions** — stage, unstage, commit, publish/push, and discard. **Stage All** and **Unstage All** only move entries in and out of the index, so neither asks for confirmation; **Discard All** does.
- **Scope** — the sidebar starts at the explorer root, so browsing into a subfolder never changes what the graph and the actions are about. The Graph header's **pin** fixes the scope to what the pane is showing — it is pressed only while you are browsing the pinned path, and from anywhere else one click moves the pin rather than clearing it — while the **chain** button follows what you browse instead. Both go down to a single file. Every action obeys the selected scope and names it in its tooltip, and **Commit** refuses rather than narrowing if anything staged lies outside it. **Publish** stays branch-wide, since it pushes commits that already exist.
- **Where the scope is** — the repository bar stays frozen at the top of the sidebar and gives the pin and the live Follow a row each, with **Clear pin** on the pin's own row. Both are marked in the Files tree on the row they point at, so you can see the scope without opening the sidebar, and both survive Save Workspace and restart. Switching the pane to a terminal drops them, since the shell can walk to another repository.
- **Scope from a row** — right-click any folder or file, in the Files tree or in the Preview tab's directory listing, for **Pin Git here**, **Unpin Git**, and **Follow Git browsing** — no need to browse there first. Single-entry only, and a row inside no repository shows the entry greyed out with a reason.
- **Graph** — starts at the newest 60 commits in the selected scope and reads 60 more each **Show more**, up to 300. The magnifier opens a find over the commits already loaded — by subject, or by full or abbreviated commit id — with a match counter and `Enter`/`Shift+Enter` (or ↑/↓) stepping. `Alt`-click any commit row or chevron collapses every expanded commit.
- **Commit card** — right-click a commit row for the full message, the author, when it was written (in the author's own time zone, with a relative age), the full object id and any branch or tag decoration, with a copy button beside the message and another beside the id.

Cross-root transfers and Git checkout, pull, or merge are intentionally left to the terminal.

## Relaunching a Pane's Shell or Agent

Launched a pane in cmd and wanted PowerShell — or WSL? Click the pane's 🔄 button: on a Local Repo terminal it's a dropdown with **Reset view** on top and a **Shell** section listing **Command Prompt**, **PowerShell**, **WSL** (default distro) and every detected distro. Picking one restarts that pane's shell in place — same slot, same title — starting in the directory the old shell was sitting in. Windows hosts only.

Each shell row carries a chevron that opens that shell's **agent** list — **Plain shell** plus Claude, Codex, Copilot, OpenCode, Kilo, Kimi, Grok and Hermes — so “this pane, but Codex in WSL” is one click; pressing the shell row itself is the plain relaunch. An **SSH pane**, or a Local Repo pane on a non-Windows host, has no shell family to pick and gets the same agent list flat under an **Agent** heading. **Plain shell** drops a running agent and comes back to an ordinary prompt, and picking what the pane already runs does nothing, so a live shell is never killed by a stray click. Explorer and browser panes keep the plain one-click reset.

The agent's **Auto mode** follows the agent rather than the pane: relaunch the same agent under another shell and it stays on, switch to a different agent and it starts from that agent's plain launch. Set it per pane in the launcher's Terminal Setup as before.

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
| 🔄 | Reset the view and replay recent output (reloads explorer and browser panes). On a terminal it opens a dropdown that also relaunches the pane in another shell (cmd, PowerShell, a WSL distro) and/or under another agent |
| 📁 ⇄ 💻 | Swap between terminal and file explorer at the current directory |
| 🌐 ⇄ 💻 | Swap a Local Repo pane between terminal and browser preview |
| 🪟 | Split side-by-side or stacked. A terminal clones its connection and opens the new pane where it *is*, not where it started; an explorer or browser pane splits off a terminal instead — for both SSH and Local Repo — rooted where the explorer is currently browsing |
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
| 📤 | Upload files into the folder this pane is showing |
| 🖥️ | Reveal the current location in the system file manager (local panes only) |

**Top bar:** theme · max surface · broadcast typing · fullscreen · minimize all windows (native desktop mode only) · keyboard shortcuts · App Settings · chevron to hide the bar. It also carries the save/status line — and when the bar is hidden that message goes to the workspace's toast instead, so it is never reported into a bar you cannot see.

**A hidden top bar comes back on hover.** Rest the pointer on the small handle at the top edge — only that handle triggers it — and the bar slides down over the workspace, so everything on it stays reachable while it is hidden. It stays as long as the pointer or focus is on it and hides shortly after you move away (or on `Esc`); clicking the handle reveals it and puts focus in it. Fullscreen hides the bar for its duration and reveals it the same way. Nothing about the reveal is saved.

**Session tab line:** the menu button, the agent dashboard button and the back-to-launcher button sit at the head of the tab line, ahead of the first tab, so they stay reachable with the top bar hidden.

**One menu holds sessions and workspaces.** The GridVibe button at the head of the tab line opens a two-row menu — **Sessions** and **Workspace** — and pointing at either row opens its items beside it: `Import Session…`, `Save Session`, `Save Session as…` and `Save All Sessions` under the first; `Save Workspace` under the second, joined by `Rename Workspace…`, `New Workspace…`, `Open Workspace`, `Move Session to Workspace` and the two close verbs when multiple workspaces are enabled. Hovering the row is enough, only one section is open at a time, and moving the pointer off the menu closes it.

**The dashboard button is on both pages.** It opens the window described under [Agent Dashboard](#agent-dashboard) — every agent running in every workspace, with a badge counting them. It sits in the session tab line in a workspace window and in the launcher's control row, and `Alt+A` opens it from either.

**Every shortcut is listed in the app.** The keyboard button — in the workspace top bar left of App Settings, and in the launcher's bottom action bar left of the minimize-all control — opens a read-only panel with the whole list, grouped the way you'd look for it. Rows that only apply in one mode say so rather than disappearing. Nothing in it is editable; the one configurable chord in GridVibe is voice push-to-talk, set in App Settings.

| Shortcut | Action |
| --- | --- |
| `Alt+1` – `Alt+9` | Switch session group |
| `Alt+W` / `Alt+Shift+W` | Next / previous workspace window (multiple workspaces only) — the window you land in pulses once |
| `Alt+W` | On the launcher, return to the workspace that opened it, or to whichever workspace is still open if that one has closed |
| `Alt+Q` | Open the launcher (in a workspace window) |
| `Alt+A` | Open the agent dashboard window |
| `Ctrl+Shift+F` | Terminal scrollback search — or, on an explorer pane, toggle repository search |
| `Ctrl+Shift+C` | Copy the terminal selection |
| `Ctrl+V` | Paste into the terminal |
| `Ctrl+F` | Find in the open file |
| `Ctrl+Shift+V` | Toggle the Markdown rendered preview |
| `F5` | Refresh the focused explorer |
| `Enter` / `Shift+Enter`, `↑` / `↓` | Step through find matches, in any find bar |
| `Ctrl+Shift+E` | Edit the open file in place — and, while editing, cancel |
| `Ctrl+S` | Save in the explorer editor |
| `Tab` | Indent in the explorer editor |
| `Esc` | Cancel an edit, drop an explorer selection, or close the open menu |
| `Alt+X` | Minimize every GridVibe window (native desktop mode only) — click any taskbar entry to bring one back |

Mouse: `Alt`+click folds a whole sibling level in the Files tree, or collapses every commit in the Git graph; `Ctrl`+click and `Shift`+click extend the explorer selection.

Drag the dividers between panes to resize them.

## Configuration

Everything lives in **App Settings** — same dialog from the gear on the launcher *or* the session window, so settings never need a trip back to the launcher. It covers theme, surface mode, terminal font and size, max sessions, shell integration, workspace autosave interval, SSH host-key policy, and all voice options — plus, in the native window, whether minimizing one GridVibe window minimizes them all (off by default; the checkbox is not shown in browser mode, where a page has no windows of its own to manage). The one exception is **Multiple workspaces**: it changes what every launch does, so its switch sits in the launcher's Workspaces card instead of the dialog.

On disk, settings load from `config.json` (git-ignored) falling back to `default_config.json`:

```json
{
  "server": { "host": "127.0.0.1", "port": 5050 },
  "appearance": { "theme": "dark" },
  "terminal": { "max_sessions": 16, "font_size": 14, "shell_integration": true },
  "workspace": { "surface_mode": "normal", "autosave_interval_minutes": 5, "multi_workspace_enabled": false, "minimize_cascade": false },
  "ssh": { "host_key_policy": "auto-add" },
  "explorer_search": { "max_files": 2000, "max_matches": 5000, "timeout_seconds": 20 }
}
```

GridVibe generates a Flask session signing key at startup unless `GRIDVIBE_SECRET_KEY`, `SECRET_KEY`, or `security.secret_key` is set.

Settings updates merge with the latest file under a cross-process lock, so
unrelated changes from two GridVibe processes survive. Invalid JSON, invalid
UTF-8, and malformed configuration sections are quarantined and recovered from
a valid `config.json.bak` when available. A failed save is reported and leaves
the running settings unchanged.

### Shell integration

`terminal.shell_integration` (on by default) is what lets GridVibe follow the directory a terminal is *in*, rather than the one it was launched in — which is the directory the file explorer opens on when you switch a pane over.

It adds an invisible escape sequence (`OSC 9;9`) to the shell's prompt and reads the path back out of the terminal's own output. It never types at your prompt to ask, so the answer stays right while a build, a pager, a TUI, or an agent is running — a pane running an agent reports the directory the agent was started in. A shell that already emits the older `OSC 7` convention is still read as before; GridVibe just no longer emits it, since a `file://` URL makes a folder named `100%done` ambiguous.

A **local** shell is handed the hook when GridVibe starts it (cmd's `PROMPT`, bash's `PROMPT_COMMAND`, forwarded into WSL with `WSLENV`, PowerShell as a `-Command` argument), so nothing is typed and nothing shows in the pane. A **remote** shell cannot be handed anything, so SSH panes are sent one short line at startup, which the shell echoes like any other command.

What it costs: that one echoed line on SSH panes, and **cmd's `PROMPT` is replaced** with the default `$P$G` plus the sequence — an existing bash/zsh hook is appended to, and PowerShell's `prompt` function is wrapped rather than replaced. Turn the setting off to leave every prompt untouched; GridVibe then reads the sequence only if your own shell emits it, and otherwise asks the shell when it is idle.

## Security

GridVibe is a local tool, not a public web service: it binds to `127.0.0.1` by default, has no built-in authentication, and should not be exposed to the internet.

- Socket.IO CORS defaults to same-origin, following the address the server actually resolved (so `--host`/`--port` are covered) plus the host each request was addressed to; state-changing cross-origin requests are rejected on the same rule. Set `security.cors_origins` only if you serve GridVibe from another origin — an explicit list is used verbatim and replaces both defaults.
- SSH host keys persist to `.known_hosts`; `ssh.host_key_policy` can be `auto-add` (default), `known-hosts`, or `strict`. The first two accept and save new keys (`known-hosts` also warns); all modes reject changed keys. An unreadable or malformed trust file refuses the connection. Repair access or restore a valid trust file, then reconnect; GridVibe preserves the failed file.
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

Backend lives in the modular `web/` package (`app.py`, `api.py`, `agents.py`, `terminal_io.py`, `explorer.py`, `explorer_search.py`, `voice.py`, …), session state in `sessions/manager.py`, the voice service in `services/`, and the two pages in `templates/` with assets in `web/static/`. The Files-tree DOM/controller lives in `web/static/js/explorer-tree.js`, split from the broader viewer in `explorer-viewer.js`. Root-level `api.py`, `session_manager.py`, `cleanup.py`, and `webview_launcher.py` are compatibility shims — edit the canonical modules.

More: [`CONTRIBUTING.md`](CONTRIBUTING.md) · [`CHANGELOG.md`](CHANGELOG.md) · [`docs/logging_guide.md`](docs/logging_guide.md) · [`docs/voice_guideline.md`](docs/voice_guideline.md) · [`docs/session_state_guideline.md`](docs/session_state_guideline.md)

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

Each of those three JSON state files — `config.json`, `saved_sessions.json` and `runtime_state.json` — is written the same careful way: one change at a time under an OS-level `<file>.lock`, committed through a scratch file and an atomic replace, with the previous version kept as `<file>.bak`. A file GridVibe cannot read is moved aside as `<file>.corrupt-<timestamp>` and the backup is loaded in its place, rather than being reported as empty and overwritten. A save that does not reach the disk is reported as a retryable failure, never as success. Those sidecar files are local state and are gitignored alongside the files they protect. The developer-facing contract for all of it — what is captured, when, and what restore replays — is [`docs/session_state_guideline.md`](docs/session_state_guideline.md).

## License

MIT. See [`LICENSE`](LICENSE).
