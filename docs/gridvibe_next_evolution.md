# GridVibe's Next Evolution

**Status:** Research and architectural recommendation  
**Date:** 2026-08-16

## Recommendation

GridVibe should evolve into a **local-first desktop product with an optional
cloud service**, using:

- **Tauri 2** for the desktop shell
- **React, TypeScript, and Vite** for the interface
- **xterm.js** for terminals
- The existing **Python engine bundled as a sidecar initially**
- Rust replacing selected Python services gradually
- A cloud control plane later for accounts, sync, teams, and remote access

GridVibe should not begin this evolution with either a complete rewrite or a
full cloud SaaS implementation.

## What Currently Feels Janky

The primary problem is not Python itself. It is the boundary between the
application, its runtime, and the user:

- Users clone a repository, create a virtual environment, and install Python
  packages.
- The desktop mode starts a Flask server on a background thread and points
  pywebview at localhost (`web/webview_launcher.py`).
- The workspace page loads numerous ordered global scripts
  (`templates/terminals.html`).
- There are 29 first-party JavaScript files totalling roughly 1.5 MB, with the
  two largest around 350 KB each.
- Libraries are manually vendored instead of being managed through a locked
  frontend build (`web/static/vendor/README.md`).

The Python backend, meanwhile, contains much of GridVibe's valuable
engineering: PTYs, SSH/SFTP, WSL, Git operations, file confinement, session
recovery, persistence transactions, host-key handling, lifecycle coordination,
and an extensive test suite. Rewriting all of that simultaneously would trade
visible interface and packaging problems for subtle functional regressions.

A modern GridVibe will also still contain HTML and CSS internally. Tauri,
Electron, Wails, VS Code, and most terminal-oriented desktop products use a
browser engine. The meaningful modernization is a typed UI, a native process
model, packaged assets, IPC, installers, updates, isolation, and reliable state
ownership.

## Proposed GridVibe 2 Architecture

```text
┌──────────────── GridVibe Desktop ────────────────┐
│                                                  │
│  React + TypeScript UI                           │
│  launcher · terminal grid · explorer · settings  │
│                  │                               │
│         typed commands + channels                │
│                  │                               │
│  Tauri/Rust desktop core                         │
│  windows · updater · permissions · credentials   │
│                  │                               │
│       GridVibe Engine compatibility layer        │
│       Python sidecar initially                   │
│  PTY · SSH · WSL · Git · files · voice · state   │
└──────────────────┬───────────────────────────────┘
                   │ outbound encrypted connection
                   ▼
        Optional GridVibe Cloud control plane
        account · license · sync · teams · broker
```

Tauri uses the operating system webview and places privileged functionality in
a Rust core process, communicating with the UI through IPC. On Windows this
means WebView2; macOS uses WKWebView and Linux uses WebKitGTK. This
[process model](https://v2.tauri.app/concept/process-model/) matches GridVibe's
natural division between its UI and terminal engine.

Tauri officially supports bundling Python applications and API servers as
[external sidecars](https://v2.tauri.app/fr/develop/sidecar/). This creates an
incremental migration path instead of demanding an immediate backend rewrite.

For terminal output, GridVibe can use Tauri Channels once the relevant terminal
engine is in Rust. Channels are intended for fast, ordered streams such as child
process output and WebSocket messages. See Tauri's
[frontend channel guidance](https://v2.tauri.app/fr/develop/calling-frontend/).

## Technology Options

### Tauri with React and TypeScript — Recommended

Advantages:

- Signed `.exe`/MSI installers, DMGs, AppImages, and other platform packages
- A supported in-app update path
- No Python prerequisite for users
- Native windows, menus, dialogs, tray integration, shortcuts, and lifecycle
- Permissions constrained per window or webview
- The existing Python engine can ship inside the application
- A gradual move from localhost HTTP to typed IPC
- A smaller distribution than an application that bundles Chromium
- A suitable foundation for a downloadable application with an optional cloud
  account

Tauri recommends Vite for SPA frontends such as React and Svelte in its
[frontend guidance](https://v2.tauri.app/start/frontend/). Its
[distribution tooling](https://v2.tauri.app/distribute/) covers installers,
platform signing, and application stores.

React is preferable to Svelte for this project because GridVibe is a large,
state-heavy developer tool rather than a small interface. React's ecosystem,
testing support, contributor availability, and patterns for imperative
components such as xterm make it the safer long-term choice. The terminal
itself should remain an imperative xterm instance owned by a React component,
rather than forcing every terminal cell into reactive rendering.

### Electron — Strongest Fallback

Electron offers the easiest all-TypeScript terminal rewrite because
[`node-pty`](https://github.com/microsoft/node-pty) supports Windows ConPTY,
Linux, and macOS. Microsoft also maintains an official
[xterm/Electron example](https://github.com/microsoft/node-pty/blob/main/examples/electron/README.md).

Electron bundles one consistent Chromium version, avoiding WebKit differences
across platforms. This is its main advantage over Tauri.

Its costs are:

- Larger application and update packages
- More memory and process overhead
- Chromium and Node security maintenance
- Particularly careful isolation for GridVibe's arbitrary browser-preview
  content

Electron warns that remote content must never receive Node integration and
recommends sandboxing, context isolation, sender validation, and restrictive
navigation policies in its
[security guidance](https://www.electronjs.org/docs/latest/tutorial/security).

If a Tauri prototype exposes unacceptable xterm, multi-window,
browser-preview, or stream-performance problems, Electron is the sensible
fallback—not pywebview.

### Wails with Go — Attractive but Not Advantageous Enough

Wails provides a native webview, Go backend, generated TypeScript bindings, and
desktop packaging. Its design is described in the
[Wails architecture documentation](https://v3.wails.io/concepts/architecture/).

Go would be pleasant for SSH and concurrency, but this path would require
replacing almost the entire Python backend while offering broadly the same
desktop model as Tauri. It does not provide enough additional value to justify
being GridVibe's first choice.

### Packaged Python and pywebview — Transitional Only

Packaging the current Python application and pywebview would produce a usable
installer with the least initial disruption. It would not resolve the deeper
process, transport, frontend-state, and browser-preview boundaries, however.
This is useful as an interim release strategy, not as the target architecture.

### Full Cloud SaaS — A Later Product Line

A cloud-hosted terminal workspace would require container isolation,
persistent volumes, terminal gateways, quotas, image building, network policy,
abuse prevention, billing, and much more operational security. It also would
not naturally support the user's local WSL distributions, files, credentials,
or installed agent CLIs.

This should be treated as a later, separately justified product line rather
than the next technical step.

## Frontend Evolution

The new frontend should not be only a visual reskin of the existing global
JavaScript. It should introduce:

- A package manager and lockfile
- TypeScript with strict checking
- Vite-built static assets
- React components organized by domain
- One typed application store for workspace and session presentation
- Explicit backend adapters rather than direct `fetch()` calls scattered
  through components
- Vitest for pure behavior and Playwright for application flows
- Continued use of the existing Python unit tests for the compatibility engine
- A strict Content Security Policy and no runtime-loaded third-party scripts

GridVibe currently pins xterm 5.3. The frontend migration is an opportunity to
move to the maintained `@xterm/*` packages, including the optional WebGL
renderer. Upstream deprecated the old unscoped package names for security
reasons, as documented in the
[xterm.js releases](https://github.com/xtermjs/xterm.js/releases).

The browser-preview pane must run in an unprivileged webview with no Tauri
commands or terminal access. It must not share the privileged scripting context
of the terminal UI. Tauri's
[capability system](https://v2.tauri.app/security/capabilities/) can constrain
privileges per window and webview.

GridVibe should retain its current preference for fully bundled frontend assets.
The xterm.js security guidance emphasizes that any JavaScript sharing a
terminal's scripting context can potentially observe or manipulate terminal
input and output. See the upstream
[xterm.js security guide](https://xtermjs.org/docs/guides/security/).

## What the SaaS Component Should Mean

GridVibe should not initially turn into terminals running on GridVibe-operated
servers. The sensible SaaS offering is a **control plane around the downloadable
local application**:

- User accounts and subscriptions
- Device registration and revocation
- Encrypted settings and workspace-template sync
- Team-shared launcher presets
- Organization policies
- Remote access to a user-owned GridVibe engine
- Presence and collaborative read-only viewing
- Update channels and release management
- Optional diagnostics and crash reporting
- Later, managed cloud workspaces as a separate premium product

The local agent should continue to own PTYs, files, Git repositories, SSH
credentials, and agent CLIs. It should make an outbound authenticated
connection so users do not need to expose listening ports.

This follows the broad separation used by remote-development products. VS Code
keeps workspace-sensitive execution beside the workspace, whether local, over
SSH, in WSL, or in a remote server. See the
[VS Code remote architecture](https://code.visualstudio.com/api/advanced-topics/remote-extensions/).
It also resembles the separation between a cloud control plane and a
device-owned data plane described in Tailscale's
[control and data plane documentation](https://tailscale.com/docs/concepts/control-data-planes).

Terminal contents and keystrokes should not pass through or be retained by the
cloud service by default. Remote terminal access should only follow an explicit
design for device identity, authorization, encryption, revocation, audit
behavior, and user-visible consent.

## Practical Migration Sequence

### 1. Build a Vertical-Slice Prototype

Create a Tauri and React application that launches the existing Python server
as a bundled sidecar. Port one local terminal, one explorer view, and the
lifecycle close flow.

The prototype should test:

- Eight active terminal panes
- Concurrent output bursts
- Terminal resizing and replay
- Sleep and wake behavior
- Window and process crashes
- Browser-preview isolation
- Installation and first launch on a clean Windows VM
- Complete shutdown without abandoned terminal processes

This is the architecture decision gate. If Tauri fails these tests in a way
that cannot be corrected cleanly, repeat the vertical slice with Electron.

### 2. Modernize the UI While Preserving the API

Introduce a typed client adapter over the existing REST and Socket.IO
contracts. Port the launcher, workspace shell, terminal panes, explorer, and
settings one domain at a time. Keep the existing application functional during
the migration.

The existing presentation and lifecycle contracts should be reused as
behavioral specifications rather than redesigned casually.

### 3. Productize Distribution

Add:

- A signed Windows installer
- Automatic application updates
- Single-instance handling
- Crash-safe application upgrades and data migrations
- OS credential storage
- Diagnostics export
- A clean uninstall path that clearly distinguishes application files from
  user data

Code signing matters because unsigned Windows downloads can trigger SmartScreen
warnings. See Tauri's
[Windows signing guidance](https://v2.tauri.app/distribute/sign/windows/).

### 4. Remove Localhost Progressively

Move window control, configuration, lifecycle, and state operations into Tauri
commands. Move terminal output to ordered Channels. Keep the current
authenticated, loopback-only transport as a compatibility layer until the
equivalent IPC path is proven.

If any localhost service remains during the transition, it should bind a random
loopback port, require a per-launch unguessable token, validate origins, and
never accept non-loopback connections by default.

### 5. Migrate Selected Engine Components

PTY and process ownership are the best first Rust targets. Filesystem and Git
services can follow. SSH should move only after a compatibility prototype proves
that a replacement can preserve Paramiko's behavior and GridVibe's host-key
contract.

Voice recognition can remain an isolated Python sidecar indefinitely. Retaining
Python for a component where its ecosystem is valuable does not undermine the
desktop architecture.

### 6. Add the Cloud Control Plane

Begin with accounts, licensing, encrypted preset sync, and device registration.
Remote access should follow only after its security model has been designed and
tested. Managed cloud workspaces come later and should have their own business
and operational justification.

## Initial Deliverable

The first concrete deliverable should be a **GridVibe 2 technical prototype**:

- Tauri desktop shell
- React and TypeScript frontend
- Bundled Python engine
- One functioning local terminal
- One functioning explorer pane
- Isolated browser-preview proof
- A real Windows installer

This prototype will answer the most expensive architectural questions without
putting the existing working product at risk.
