# External Development Program Button Proposal

## Summary

Adding a GridVibe button that opens a configured development program is feasible. VS Code should be the default preset, not a hard dependency.

The safest shape is not a raw browser link and not a user-supplied command field. It should be a small allow-listed backend launcher:

1. The UI shows an `Open in IDE` button for eligible local workspaces.
2. The button calls a new Flask endpoint.
3. The backend resolves the active workspace path, validates it, and starts the selected configured program with `subprocess.Popen([...])`.
4. The endpoint returns success, disabled, or a user-facing error such as "VS Code command not found."

This keeps command execution on the trusted local GridVibe process, where paths and app definitions can be validated before anything native is launched.

## External Behavior Research

VS Code supports this use case directly:

- The official VS Code CLI can open a folder/project from the command line with `code .` or `code <path>`.
- It supports `--new-window` and `--reuse-window`.
- It supports opening files and folders by absolute or relative path.
- It also registers `vscode://file/...` URL handling for projects and files.
- If the `code` command is not found, the user needs VS Code's command-line install/path setup.

Source: VS Code Command Line Interface docs, especially "Launching from command line", "Opening Files and Folders", and "Opening VS Code with URLs":
https://code.visualstudio.com/docs/configure/command-line

JetBrains IDEs also support a CLI-launcher model:

- IntelliJ IDEA documents command-line launchers for opening files and projects.
- The launcher can be made available on `PATH` through the installer, shell scripts, symlinks, or JetBrains Toolbox scripts.
- If a file or directory path is passed to the launcher script, IntelliJ IDEA opens that path.

Source: JetBrains IntelliJ IDEA command-line interface docs:
https://www.jetbrains.com/help/idea/working-with-the-ide-features-from-command-line.html

This means GridVibe should use a generic "development app" launcher shape rather than binding the feature to VS Code. The app-specific part is just command/argument configuration.

The VS Code URL scheme is useful as a fallback, but CLI launch is better for GridVibe because the backend can detect availability, pass arguments safely, log failures, and avoid relying on browser protocol-handler prompts.

Agentic development tools are a different class from GUI IDEs:

- OpenAI Codex CLI is documented as a local terminal coding agent; the basic launch command is `codex`.
- Claude Code is documented as available in terminal, IDE, desktop app, and browser, with the terminal launch command `claude`.
- Gemini CLI is documented as a terminal-first AI agent; the basic launch command is `gemini`.
- Aider is documented as AI pair programming in the terminal; users start it from the project directory with `aider`.
- GitHub's retired `gh copilot` extension has been replaced by the newer GitHub Copilot CLI.

Sources:

- OpenAI Codex CLI repository: https://github.com/openai/codex
- Claude Code overview: https://docs.anthropic.com/en/docs/claude-code/overview
- Gemini CLI repository: https://github.com/google-gemini/gemini-cli
- Aider installation docs: https://aider.chat/docs/install.html
- GitHub Copilot CLI docs: https://docs.github.com/en/copilot/how-tos/use-copilot-for-common-tasks/use-copilot-in-the-cli

## Current GridVibe Observations

- `default_config.json` already stores machine-level settings such as `appearance`, `workspace`, and `voice_input`.
- `web/api.py` exposes safe app settings through `_public_app_config()` and persists them with `GET/POST /api/app-config`.
- `templates/index.html` already has a launcher action area beside `Launch Workspace` and app settings controls.
- `templates/terminals.html` has a topbar action cluster with refresh, surface mode, fullscreen, and settings buttons. This is the best first location because it has active session context.
- `POST /api/sessions` prepares each session with `connection_mode`, `directory`, `startup_mode`, and group metadata before handing it to `SessionManager`.
- `sessions/manager.py` stores each terminal session's `directory`, `mode`, `distribution`, `use_wsl`, and `use_powershell`.
- GridVibe already uses `subprocess.run` for detection/preflight flows and `subprocess.Popen` for detached/local processes.
- `agent_registry.json` already models terminal-first agent tools. It currently includes GitHub Copilot CLI, OpenAI Codex CLI, OpenCode CLI, Kilo CLI, and Claude Code.
- `templates/index.html` already renders launcher agent choices from `AGENT_OPTIONS`, including `other` for custom commands.
- `web/api.py` already has agent preflight detection and clears agent startup commands when registry detection fails.

## Tool Classes

Do not put every coding tool behind the same button. The implementation should split tools by how they are meant to run:

| Class | Examples | Best GridVibe action |
|---|---|---|
| GUI IDE/editor | VS Code, Cursor, Windsurf, Zed, JetBrains IDEs, Visual Studio | `Open in IDE` backend app launcher |
| Terminal agent/ADE CLI | Codex, Claude Code, Gemini CLI, Aider, GitHub Copilot CLI, OpenCode, Kilo | Open a GridVibe terminal pane with the configured agent command |
| IDE extension/plugin | Claude Code in VS Code/JetBrains, GitHub Copilot, Cline/Roo-style extensions | Launch the parent IDE; plugin lifecycle stays inside that IDE |
| Cloud/web agent | Codex Web, Claude Code web, Devin-style hosted agents, GitHub Copilot cloud agent | Open external URL or document as future cloud integration; do not treat as local executable |

For v1, `Open in IDE` should stay focused on GUI editors. Agent tools should be handled by the existing agent session path, with a possible convenience button such as `New Agent Pane`.

## Popular Tool Coverage

Recommended first-class coverage:

| Tool | Type | Suggested command/key | Recommended handling |
|---|---|---|---|
| VS Code | GUI IDE/editor | `code` | `Open in IDE` preset |
| Cursor | GUI IDE/editor with agent features | `cursor` when installed | `Open in IDE` preset or custom IDE command |
| Windsurf | GUI IDE/editor with Cascade | `windsurf` when installed | `Open in IDE` preset or custom IDE command |
| Zed | GUI IDE/editor with agent features | `zed` when installed | `Open in IDE` preset or custom IDE command |
| JetBrains IDEs | GUI IDE/editor family | `idea`, `pycharm`, `webstorm`, etc. | `Open in IDE` preset family |
| Visual Studio | GUI IDE/editor | `devenv` or configured absolute path | Custom IDE command, Windows-only |
| OpenAI Codex CLI | Terminal agent/ADE CLI | `codex` | Existing agent registry, `New Agent Pane` |
| Claude Code | Terminal/IDE/desktop/web agent | `claude` | Existing agent registry, `New Agent Pane`; launch parent IDE for plugin use |
| Gemini CLI | Terminal agent/ADE CLI | `gemini` | Add to `agent_registry.json`, `New Agent Pane` |
| Aider | Terminal pair-programming agent | `aider` | Add to `agent_registry.json`, `New Agent Pane` |
| GitHub Copilot CLI | Terminal agent/ADE CLI | `copilot` | Existing agent registry, `New Agent Pane` |
| OpenCode | Terminal agent/ADE CLI | `opencode` | Existing agent registry, `New Agent Pane` |
| Kilo Code CLI | Terminal agent/ADE CLI | `kilo` | Existing agent registry, `New Agent Pane` |

The design should not try to normalize all of these into one command shape. GUI apps need detached process launch; agent CLIs need interactive terminal lifecycle; web/cloud agents need URL or API integration.

## Recommended UX

Add a compact icon button in the active terminals topbar:

- Label/title: `Open in IDE`, or `Open in {selected app label}` when a selected app is configured.
- Default enabled state: enabled only for local repo/session paths that resolve to a local directory.
- On click: launch the selected development app for the active session group's workspace directory.
- Result feedback: brief topbar toast/status message.
- Disabled tooltip: explain why the current workspace cannot be opened locally.

The launcher page can get a secondary shortcut later, but the terminals page should come first because the user usually wants to open the workspace they are actively using.

Add a settings control under App Settings:

- Toggle: enable/disable external development app launcher.
- Select: desired IDE/app, defaulting to VS Code.
- Optional advanced fields: command path and launch arguments for a custom app.
- Optional window behavior for compatible apps: reuse existing window or open a new window.

For a first implementation, keep custom app editing simple and explicit. A select box with built-in presets plus a `Custom command` option is enough.

For agent/ADE CLIs, add a separate convenience path later:

- Button/title: `New Agent Pane`
- Select/default: preferred agent from `agent_registry.json`
- Behavior: create a new GridVibe terminal/session pane in the active workspace and run the selected agent command.
- Examples: `codex`, `claude`, `gemini`, `aider`, `copilot`, `opencode`, `kilo`

This preserves the value of GridVibe: terminal agents remain visible, controllable, and attached to the workspace rather than being hidden behind a detached native process.

## Proposed Config

Add a new config section:

```json
{
  "development_apps": {
    "enabled": true,
    "selected_app": "vscode",
    "apps": {
      "vscode": {
        "label": "VS Code",
        "command": "code",
        "args": ["--reuse-window", "{path}"],
        "allowed_modes": ["wsl"],
        "path_policy": "local_existing_directory"
      },
      "vscode_new_window": {
        "label": "VS Code (new window)",
        "command": "code",
        "args": ["--new-window", "{path}"],
        "allowed_modes": ["wsl"],
        "path_policy": "local_existing_directory"
      },
      "jetbrains_idea": {
        "label": "IntelliJ IDEA",
        "command": "idea",
        "args": ["{path}"],
        "allowed_modes": ["wsl"],
        "path_policy": "local_existing_directory"
      },
      "custom": {
        "label": "Custom IDE",
        "command": "",
        "args": ["{path}"],
        "allowed_modes": ["wsl"],
        "path_policy": "local_existing_directory"
      }
    }
  }
}
```

Notes:

- `command` should be an executable name or absolute executable path from local config, not from a request body.
- `args` may use a small token set such as `{path}`.
- `allowed_modes` should initially mean GridVibe's local repo mode only. The internal mode name is currently `wsl`, but the user-facing label is `Local Repo`.
- Keep app definitions out of saved sessions. They are machine-level preferences like app settings and voice settings.
- The browser may send an app key only if multiple buttons/dropdowns are added later; it must never send a command string.
- `selected_app` is the default app used by the topbar button.

Keep terminal agents in a separate setting that points at `agent_registry.json`:

```json
{
  "agent_launchers": {
    "enabled": true,
    "selected_agent": "codex",
    "launch_mode": "new_pane"
  }
}
```

Notes:

- `selected_agent` should refer to a key in `agent_registry.json`, or `other` plus an explicit custom command handled through the existing agent UI rules.
- The current registry already includes `copilot`, `codex`, `opencode`, `kilo`, and `claude`.
- Add `gemini` and `aider` to `agent_registry.json` as follow-up presets rather than adding them to `development_apps`.
- Agent launcher settings should remain separate from GUI IDE settings because they create GridVibe sessions, not detached native apps.

## Proposed Backend Contract

Add:

```text
GET /api/development-apps
POST /api/development-apps/<app_key>/open
GET /api/agent-launchers
POST /api/agent-launchers/<agent_key>/open
```

`GET /api/development-apps` returns safe metadata:

```json
{
  "enabled": true,
  "selected_app": "vscode",
  "apps": [
    {
      "key": "vscode",
      "label": "VS Code",
      "available": true,
      "disabled_reason": ""
    }
  ]
}
```

The response may include several app presets. It should expose app keys, labels, availability, and disabled reasons, but not raw command internals unless an advanced settings editor explicitly needs them.

`POST /api/development-apps/vscode/open` accepts only context, not a command:

```json
{
  "group_id": "optional-active-group-id",
  "session_id": "optional-active-session-id"
}
```

The server should:

1. Resolve the target session/group.
2. Choose the workspace path from the active local session directory, or the first local session directory in the group.
3. Normalize and validate the path with `Path.resolve()`.
4. Require that the path exists and is a directory for v1.
5. Resolve the executable with `shutil.which(command)` unless an absolute executable path is configured.
6. Launch with `subprocess.Popen(command_list, shell=False, cwd=target_path, ...)`.
7. Return JSON with `ok`, `app`, `app_label`, `path`, and `message`.

If a simplified route is added later, such as `POST /api/development-apps/open`, the backend should use `selected_app` from config. The request should still contain only target context.

`POST /api/agent-launchers/<agent_key>/open` should not call `subprocess.Popen` directly. It should create a normal GridVibe session or split pane with:

```json
{
  "startup_mode": "agent",
  "initial_command_mode": "agent",
  "agent_selection": "codex",
  "initial_command": "codex"
}
```

That lets existing preflight checks, terminal lifecycle, saved-session serialization, and session UI behavior keep working. The backend should reject unknown agent keys unless the request goes through the existing custom-agent path.

For Windows detachment, use creation flags similar to the existing restart helper:

```python
creationflags = (
    getattr(subprocess, "DETACHED_PROCESS", 0)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    | getattr(subprocess, "CREATE_NO_WINDOW", 0)
)
```

For POSIX:

```python
start_new_session=True
```

## Security Requirements

This feature must be treated as local native command execution.

Hard requirements:

- Do not accept arbitrary command strings from the browser.
- Do not use `shell=True`.
- Do not pass request-supplied arguments directly to `Popen`.
- Only allow configured app keys.
- Do not let the browser provide or override `command` or `args`.
- Only expand known placeholders such as `{path}`.
- Validate that the path is local, absolute after resolution, and exists.
- Prefer same-origin checks or an internal launch token for this endpoint.
- Log launch attempts without logging secrets.

The same-origin/token point matters because GridVibe's Socket.IO CORS defaults can be permissive for local workflows. A native-app-launch endpoint should be stricter than normal read-only or terminal-status endpoints.

Agent/ADE CLI security requirements:

- Treat agent launchers as workspace-affecting automation, not just a viewer.
- Reuse the existing agent preflight path before launching.
- Keep the agent attached to a visible GridVibe terminal/session so the user can stop it and inspect output.
- Do not auto-install agent CLIs from this button.
- Do not pass an initial prompt automatically in v1; start the agent and let the user type.
- Preserve each tool's own permission/auth flows instead of bypassing them.

## Remote And WSL Scope

Recommended v1 scope:

- Support Local Repo paths that exist on the host running GridVibe.
- Do not support SSH mode yet.
- Do not support arbitrary remote IDE launch yet.

Future options:

- WSL remote: VS Code supports a `--remote` authority such as WSL targets, but this needs distro/path mapping and extension assumptions.
- SSH remote: VS Code Remote SSH and some IDE remote-development features can open remote authorities, but GridVibe would need a mapping from GridVibe SSH host/user to the user's IDE-specific host aliases.
- URL protocol fallback: `vscode://file/{full path}/` can work from browsers, but it is harder to detect failure and less controllable than backend CLI launch.

## Implementation Outline

1. Add `development_apps` defaults to `default_config.json`.
2. Extend runtime config loading in `web/api.py` with normalized development-app settings.
3. Add helper functions:
   - `_public_development_apps_config()`
   - `_normalize_development_apps_config()`
   - `_resolve_development_app_target_path(group_id, session_id)`
   - `_build_development_app_command(app_config, path)`
   - `_launch_development_app(app_key, path)`
4. Add `GET /api/development-apps`.
5. Add `POST /api/development-apps/<app_key>/open`.
6. Add a topbar button in `templates/terminals.html`.
7. Add frontend JS that sends the active group/session context and displays the result.
8. Add App Settings UI for enabled/selected app plus advanced command/argument override.
9. Add `agent_launchers` settings that reference `agent_registry.json`.
10. Add `gemini` and `aider` presets to `agent_registry.json`.
11. Add a separate `New Agent Pane` flow that creates a normal GridVibe agent session.
12. Add tests around config normalization, path rejection, missing executable handling, agent registry lookup, and `Popen` invocation.

## Test Plan

Backend unit tests:

- App list hides command details and reports `available=false` when the selected command cannot be resolved.
- Unknown app key returns 404.
- Disabled launcher returns a clear disabled response.
- Selected app defaults to VS Code when config is missing.
- Custom app with blank command is unavailable.
- Request cannot override command or args.
- Nonexistent workspace path returns 400.
- File path instead of directory returns 400.
- SSH-only session returns disabled/400 for v1.
- Valid local repo session launches `Popen` with `shell=False`.
- Agent launcher rejects unknown registry keys.
- Agent launcher creates a GridVibe session/pane instead of calling `Popen` directly.
- Gemini and Aider registry presets appear in `AGENT_OPTIONS` after they are added.

Frontend/manual tests:

- Button is visible in the terminal topbar.
- App Settings lets the user choose the desired IDE/app.
- Button disabled state is clear when no active local workspace exists.
- The selected IDE/app opens the active local repo.
- Missing selected command produces a useful message.
- `New Agent Pane` starts the selected terminal agent in the active workspace.
- Existing terminal/session workflows are unaffected.

## Recommended First Version

Build the smallest useful version:

- Built-in presets for VS Code reuse-window, VS Code new-window, and JetBrains-style launcher command.
- One selected app setting, defaulting to VS Code reuse-window.
- One launch target: active local workspace directory.
- One button in `templates/terminals.html`.
- Backend CLI launch only.
- No SSH/remote launch support.
- Minimal editable UI for selected app. Custom command path can be a v1.1 follow-up if we want to keep the first UI smaller.
- Keep Codex/Claude/Gemini/Aider/Copilot/OpenCode/Kilo as terminal-agent launches, not GUI IDE launches.

That delivers the core workflow while keeping the command-execution surface narrow.
