# Terminal Surface Fit Proposal

Research checked: 2026-06-30

## Objective

Stretch the active terminal workspace so it uses as much of the available
`pywebview` or browser surface as practical, while keeping xterm dimensions
correct and preserving the existing browser-first fallback.

This is primarily a layout and window-state problem. xterm.js should not be
scaled with CSS transforms; it should be fit after the containing element is
laid out so the terminal can report accurate rows and columns back to the PTY.

## Current State

GridVibe already has the important terminal resize primitives:

- `templates/terminals.html` loads `xterm@5.3.0` and `xterm-addon-fit@0.8.0`.
- Each terminal uses `FitAddon.fit()` after it is opened.
- Each terminal wrapper is watched with `ResizeObserver`.
- Browser `resize` events schedule terminal fits.
- After fit, the frontend emits `terminal_resize` with the xterm `cols` and
  `rows`.
- `web/webview_launcher.py` exposes native pywebview fullscreen controls for
  the session window.

The main unused-surface sources are:

- `#terminalsGrid` is capped at `width: min(1800px, 100%)`, so very wide
  displays keep side margins.
- The grid has fixed `10px` padding and `10px` gaps.
- Each terminal has card border, terminal header, and inner terminal padding.
- The topbar and session-tabs permanently consume vertical space.
- The native session window opens at `1600x980` with `min_size=(1180, 720)`,
  but does not start maximized or fullscreen.

## Relevant Platform Capabilities

### xterm.js FitAddon

The xterm FitAddon exists specifically to resize a terminal to its containing
element. Its API exposes `fit()` and `proposeDimensions()` for calculated
terminal rows and columns.

Source:
[xterm.js addon-fit README](https://github.com/xtermjs/xterm.js/tree/master/addons/addon-fit),
[addon-fit typings](https://raw.githubusercontent.com/xtermjs/xterm.js/master/addons/addon-fit/typings/addon-fit.d.ts)

Implication for GridVibe:

- Keep `FitAddon.fit()` as the final sizing step.
- Continue emitting `terminal_resize` only after xterm has the new `cols` and
  `rows`.
- Avoid CSS zoom or transform-based stretching, because that can make visible
  cells diverge from terminal dimensions.

### Browser Fullscreen API

The browser Fullscreen API can place a document element into fullscreen and
exit fullscreen later. MDN notes it is not Baseline because support still varies
in some widely used browsers, and users can exit fullscreen with Esc/F11 or by
switching context.

Source:
[MDN Fullscreen API](https://developer.mozilla.org/en-US/docs/Web/API/Fullscreen_API)

Implication for GridVibe:

- Browser fullscreen is useful as a user-triggered mode.
- It should remain a fallback path for non-pywebview mode.
- The UI must tolerate fullscreen ending outside GridVibe control.

### ResizeObserver

`ResizeObserver` reports changes to an element's dimensions and is widely
available across browsers. MDN also calls out resize-loop risks and recommends
deferring layout-changing work with `requestAnimationFrame` when needed.

Source:
[MDN ResizeObserver](https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver)

Implication for GridVibe:

- The existing wrapper observer is the correct primitive.
- The current debounce plus `requestAnimationFrame` path is directionally right.
- Any new layout mode should rely on the observer and `scheduleFit()` instead of
  trying to manually calculate xterm rows from viewport dimensions.

### pywebview Window Controls

pywebview `create_window()` supports initial `fullscreen=False` and
`maximized=False` parameters, and window instances expose `maximize()`,
`resize()`, `restore()`, `toggle_fullscreen()`, plus window resize/maximize
events.

Source:
[pywebview API](https://pywebview.flowrl.com/guide/api.html)

Implication for GridVibe:

- Native mode can start the session window maximized or fullscreen.
- Native mode can expose a separate "maximize session window" action without
  relying on browser fullscreen.
- `window.events.resized` can be used if pywebview needs an explicit frontend
  resize nudge, but the DOM `ResizeObserver` should still be the source of
  truth for terminal fitting.

## Options

### Option 1: Fill the Browser Surface With CSS

Change the terminal page layout so the grid always consumes the full available
viewport width and minimizes internal spacing.

Candidate changes:

- Change `#terminalsGrid` from `width: min(1800px, 100%)` to `width: 100%`.
- Reduce grid `padding` and `gap` in dense mode.
- Reduce `.terminal-surface` padding in dense mode.
- Optionally reduce terminal header height and button padding.
- Keep `body` and page shell at `height: 100vh; overflow: hidden`.
- Call `scheduleFit()` for attached terminals after toggling dense mode.

Pros:

- Works in both browser and pywebview.
- Low risk because xterm FitAddon and ResizeObserver already exist.
- Preserves current window behavior.
- Gives the biggest immediate improvement on wide monitors.

Cons:

- Does not remove browser chrome in normal browser mode.
- Topbar, tabs, and terminal headers still reserve vertical space unless also
  compacted.

Recommendation:

Implement this first. It fixes the known local cap and makes every later option
more effective.

### Option 2: Add a "Max Surface" Terminal Mode

Add a user-facing mode that hides or compacts GridVibe chrome while active.
This is separate from OS/browser fullscreen.

Candidate behavior:

- Toggle a class on `body`, for example `body.surface-max`.
- Collapse `.session-tabs` into a smaller strip, or hide it when there is only
  one active group.
- Shrink `.topbar` from `50px` to a compact icon row, or hide nonessential
  labels.
- Shrink terminal headers to a compact status/action row.
- Use smaller `gap`, `padding`, and terminal inner padding.
- Persist the preference in local storage or server app settings.

Pros:

- Works in browser and pywebview.
- Gives users more terminal rows without changing native window state.
- Can be combined with fullscreen/maximized mode.

Cons:

- Higher UI polish cost than a CSS width change.
- Hidden chrome needs clear recovery controls and keyboard handling.
- Header controls like refresh, clear, split, voice, and explorer controls need
  a compact layout, not just `display: none`.

Recommendation:

Implement after Option 1 if vertical space remains a real complaint. Treat it as
a deliberate terminal workspace mode, not as an accidental responsive collapse.

### Option 3: Start pywebview Session Windows Maximized

Change the native session window creation in `web/webview_launcher.py` so the
session window starts maximized, or make this configurable.

Candidate changes:

- Add `maximized=True` to the session `webview.create_window(...)` call.
- Alternatively add a launcher/app setting such as
  `desktop.session_window_state = "normal" | "maximized" | "fullscreen"`.
- After native maximize/fullscreen transitions, trigger frontend resize/fits
  through existing resize paths.

Pros:

- Native users immediately get the full available desktop work area.
- Less intrusive than fullscreen because OS taskbar and normal window controls
  remain available.
- pywebview directly supports this.

Cons:

- Applies only to pywebview mode.
- On some Linux/Qt or Windows/WebView2 environments, maximize timing may need a
  post-show fit nudge.
- Users with smaller screens may prefer the current deterministic `1600x980`
  startup size.

Recommendation:

Add as a configurable default after Option 1. Use maximized as the preferred
native default before considering fullscreen-by-default.

### Option 4: Improve Fullscreen Handling

Keep the current fullscreen button, but make the sizing behavior more explicit
and robust after fullscreen state changes.

Candidate changes:

- In browser mode, request fullscreen on the terminal page root or
  `#terminalsGrid` instead of always `document.documentElement`, if testing
  shows better results.
- In pywebview mode, continue using `window.toggle_fullscreen()`.
- Listen to `fullscreenchange` and pywebview fullscreen actions, then run
  `redrawAttachedTerminals(..., { forceResize: true })` or schedule fits after
  two animation frames.
- Consider keyboard shortcut support for enter/exit fullscreen.

Pros:

- Builds on current code.
- Gives the maximum visual surface on both native and browser surfaces.
- Keeps the action explicit and reversible.

Cons:

- Browser Fullscreen API behavior varies and can exit for reasons outside the
  app.
- Fullscreen can be disruptive for multi-window workflows.
- Still needs Option 1/2 CSS cleanup to make good use of the fullscreen space.

Recommendation:

Keep fullscreen as an explicit action, not the default. Harden post-fullscreen
fit behavior after CSS surface changes are made.

### Option 5: Frameless or Kiosk-like pywebview Window

Use pywebview frameless/fullscreen capabilities to create a near-kiosk terminal
surface.

Candidate behavior:

- Create the session window with `frameless=True` and custom drag regions.
- Use `fullscreen=True` for initial native fullscreen in a dedicated mode.
- Provide in-app window controls for close, restore, and fullscreen exit.

Pros:

- Maximizes usable pixels in native mode.
- Can feel like a dedicated terminal appliance.

Cons:

- Native-only.
- More platform-specific window-management risk.
- Requires custom titlebar/accessibility behavior.
- Higher support burden than maximized windows.

Recommendation:

Defer. This should be an experimental mode only if maximized/fullscreen plus CSS
surface cleanup is not enough.

## Recommended Plan

1. Implement full-width terminal grid CSS.
   - Remove the `1800px` cap.
   - Reduce default terminal workspace padding enough to recover visible cells.
   - Verify single, split, grid, and local-split layouts.

2. Add a compact "Max Surface" CSS class.
   - Start with spacing/header compaction.
   - Keep every existing action reachable.
   - Refit all attached terminals after toggling.

3. Add configurable pywebview session window startup state.
   - Default proposal: `maximized` for desktop session windows.
   - Keep normal size available for users who prefer fixed windows.

4. Harden fullscreen refit sequencing.
   - On browser `fullscreenchange`, refit after layout settles.
   - On pywebview fullscreen toggle success, refit after layout settles.

5. Consider frameless/fullscreen-native mode only after validating the above.

## Implementation Notes

2026-06-30:

- Implemented plan item 1 in `templates/terminals.html`.
  - `#terminalsGrid` now uses `width: 100%` instead of the previous 1800px
    cap.
  - Default grid gap/padding and terminal inner padding were reduced so more
    cells fit in the same browser or pywebview surface.
- Implemented plan item 2 in `templates/terminals.html`.
  - Added a persistent "Max surface" terminal-page toggle stored in
    `localStorage` as `gridvibe.terminalSurfaceMode`.
  - The toggle applies `body.surface-max`, compacting the topbar, session tabs,
    terminal headers, grid spacing, and terminal surface padding while keeping
    existing terminal actions reachable.
  - Toggling the mode refits attached xterm instances and emits forced terminal
    resize updates through the existing resize path.

## Acceptance Criteria

- On a wide desktop browser window, terminal grid width uses the full content
  viewport instead of stopping at 1800px.
- In pywebview session mode, the terminal surface can open maximized or be made
  maximized without manually dragging the window.
- After browser resize, pywebview resize, maximize, or fullscreen toggle, xterm
  rows and columns update and the backend receives the new dimensions.
- Terminal content does not blur or visually scale independently from xterm's
  reported geometry.
- All layouts remain usable: single terminal, 2-pane, 3-pane, grid, and
  split-local.
- Session tabs, terminal actions, voice controls, split controls, and explorer
  panes remain reachable in any compact/max-surface mode.

## Risk Notes

- The lowest-risk work is CSS width/padding cleanup because resize plumbing is
  already present.
- The highest-risk work is frameless/kiosk behavior because it replaces native
  window chrome and needs platform-specific polish.
- Browser fullscreen should remain user-triggered because support and lifecycle
  behavior are browser-controlled.
- Native fullscreen and browser fullscreen are separate concepts in this app;
  keep the pywebview bridge path and browser DOM path distinct.
