# GUI todo proposals — options for decision

**Status: scratch / decision document.** Not a contract, not citable. It exists
so the four `(GUI)` notes in `docs/r&d/todos.txt` can be decided before any of
them is built. Once a decision is made, the *rule* goes into the code it governs
(and, where it changes user-visible behaviour, into `README.md` /
`CHANGELOG.md`); this file is then deleted rather than maintained.

Source notes: `docs/r&d/todos.txt` items 3, 4, 5, 6.
Written against the tree at branch `szua_gridvibe_agents-wrk`.

Each item below is: **what the note asks**, **what the code does today** (with
the lines), **what I found that changes the answer**, then **options** and a
**recommendation**. Pick one option per item (or write your own) and it is ready
to implement.

---

## Contents

| # | Note | Recommended option |
| --- | --- | --- |
| 3 | Replace the ``Alt+` `` launcher keybind; add an Edit keybind | **3-B** (``Ctrl+Shift+` ``) + **3-E** (`Ctrl+Shift+E` toggles Edit) |
| 4 | Native desktop mode: hide-all control + minimize cascade | **4-B** (hide/restore all) + **4-D** (opt-in cascade, off by default) |
| 5 | Top-bar dropdowns are hard to reach from the peek handle | **5-A** (move the two menus into the centre column) |
| 6 | Settings needs a configurable keybind section | **6-C** (registry + read-only list now, configurable in a second pass) |

---

## ⚠️ One finding that cuts across items 3, 4 and 6

**On Windows, `AltGr` is delivered to the page as `Ctrl+Alt`.** `event.ctrlKey`
and `event.altKey` are both `true` for every `AltGr` keystroke. On a
Slovenian/Croatian layout — the layout that makes ``Alt+` `` a dead key in the
first place, which is why note 3 exists — `AltGr` is how you type most of the
characters a terminal user types constantly:

| AltGr + | produces |
| --- | --- |
| `Q` | `\` |
| `W` | **pipe** |
| `E` | `€` |
| `C` | **`&`** |
| `F` / `G` | `[` / `]` |
| `B` / `N` | `{` / `}` |
| `V` | `@` |
| `X` | `#` |

Both chords the notes propose land on that list:

- **`Ctrl+Alt+W`** (note 3, open launcher) is `AltGr+W` = **pipe**. Typing a pipe
  in a terminal pane would open the launcher. And it would, because
  `isPaneShortcutBlockingTarget()` (`terminals.js:7256`) deliberately does *not*
  treat xterm's helper textarea as a blocking target — that exemption is what
  makes `Alt+W` work from a focused pane, and it would hand this one through too.
- **`Ctrl+Alt+C`** (note 4, hide all windows) is `AltGr+C` = **`&`**.

This is not a reason to abandon either idea, but it means:

1. Any `Ctrl+Alt+<letter>` binding **must** reject `AltGr` explicitly —
   `event.getModifierState('AltGraph')` is true for a real `AltGr` press and
   false for a genuine `Ctrl+Alt` press, on Chromium/WebView2 and Firefox on
   Windows. That check is cheap and reliable, but it is one more thing every
   handler has to remember, which is an argument for the central registry in
   item 6.
2. Even with the guard, `Ctrl+Alt+<letter>` is a chord some layouts cannot
   *produce* without also producing a character — so the guard makes the binding
   unreachable for the very user it was chosen for. **A `Ctrl+Alt` chord is a
   poor default on this machine.** The options below reflect that.
3. Item 6's validator should carry this as a first-class warning class, not just
   "browser reserved".

---

## Item 3 — Replace the launcher keybind, add an Edit keybind

> *"the alt + dead keybind to bring up launcher must be replaced with something
> else, ctrl+alt+w if possible, add edit key bind ctrl+shift+e if possibble"*

### 3a. Replacing ``Alt+` ``

**Today.** `terminals.js:7283` binds `event.code === 'Backquote'` with `altKey`
and no other modifier, calling `goToSettings()`. `terminals.js:4425` gives xterm
an explicit pass-through so a focused pane hands it up instead of sending
``ESC ` `` to the shell. Documented at `README.md:200`. It was matched on
`event.code` on purpose — precisely so a layout where that key is a dead accent
still reaches the launcher — but on Windows a dead key can be consumed by the
layout composer before the keydown reaches the page, which is the failure you
are seeing.

**Constraint.** Whatever replaces it needs all three of: (a) a pass-through in
the xterm handler, (b) no collision with the browser in browser mode, (c) no
collision with `AltGr` on your layout.

| Option | Chord | Notes |
| --- | --- | --- |
| **3-A** | `Ctrl+Alt+W` (as asked) | Needs the `AltGraph` guard. Even guarded, it is the pipe key on your layout, so the chord is effectively unavailable *to you* — you would be binding a key you cannot press. Free in Chrome/Edge/Firefox otherwise. |
| **3-B** ✅ | ``Ctrl+Shift+` `` | Same physical key you already reach for, so nothing to relearn, and `Ctrl+Shift` is never `AltGr`. Not bound by Chrome, Edge or Firefox. Still matched on `event.code === 'Backquote'`, so layout-independent. Needs the xterm pass-through updated in place. |
| **3-C** | `Ctrl+Shift+L` ("launcher") | Mnemonic, free in Chrome/Edge. **Firefox reserves `Ctrl+Shift+L`**. Native mode unaffected. |
| **3-D** | `Alt+Home` | Unambiguous, no letter-layout interaction at all. Chrome/Firefox bind plain `Alt+Home` to "go to homepage" — the page sees it first and `preventDefault()` stops it, so this works, but it is a chord nobody guesses. |

**Recommendation: 3-B.** It keeps the physical key you already use, is immune to
the `AltGr` problem, and costs one line in the handler plus one in the xterm
pass-through. If you would rather keep `Ctrl+Alt+W` anyway, take **3-A** *with*
the `AltGraph` guard and expect to reach the launcher by the button.

**Open question for you:** should the old ``Alt+` `` keep working alongside the
new chord for a release (a deprecation overlap), or be removed outright? Keeping
both costs nothing but a second `if`; removing it is cleaner and the key is
broken for you anyway. My default would be **remove it**.

### 3b. Adding an Edit keybind

**Today.** Entering the in-place editor is mouse-only: `explorer-editor.js:161`
renders the `.explorer-edit-btn`, `:172` wires its click to
`enterExplorerEditMode(index)`. There is *no* keyboard route in. Leaving is
already bound — `Ctrl+S` saves and `Esc` cancels (`explorer-editor.js:417`,
`:422`, `README.md:206`) — so the asymmetry is real: you can get out with the
keyboard but not in.

`Ctrl+Shift+E` is free in Chrome/Edge. **Firefox binds it to the Network
Monitor** devtools panel, and a page `preventDefault()` does not reliably win
against a devtools accelerator there — so on Firefox in browser mode this may
open devtools instead. Native mode (WebView2) and Chrome/Edge are unaffected.

| Option | Behaviour |
| --- | --- |
| **3-E** ✅ | `Ctrl+Shift+E` **toggles**: enters edit mode on an editable open file, and while editing acts as Cancel. Symmetric with the button, which is itself replaced by Save/Cancel while editing. |
| **3-F** | `Ctrl+Shift+E` **enters only**; `Esc` remains the only way out. Simpler, but a chord that does nothing on its second press reads as broken. |
| **3-G** | `Ctrl+E` instead. Shorter, but Firefox binds it to search and Chrome uses it for the address bar in some configurations. More collision risk for one saved keystroke. |

**Recommendation: 3-E**, with the same shape as the existing `F5` handler
(`terminals.js:7029`): resolve the target through
`findExplorerShortcutTargetIndex()`, no-op when there is no explorer pane, and
when the Edit button would be disabled (`pane._explorerFileEditBlockReason`)
surface that reason on the pane's toast rather than silently doing nothing — the
button already carries a tooltip explaining why, and the keybind should say the
same thing.

**Cost for item 3 overall:** small. Two handlers in `terminals.js`, one xterm
pass-through, one entry each in the `README.md` shortcut table, one CHANGELOG
line. No backend, no persistence, no new module.

---

## Item 4 — Native desktop mode: hide-all control and minimize cascade

> *"NATIVE DESKTOP MODE: hide all button/keybind in workspace/launcher window
> (ctrl+alt+c) AND lets check if possible, minimizing laucnher/workspace
> minimizes all gridvibe windows in DESKTOP mode."*

**Reading.** I read this as *one control (button + `Ctrl+Alt+C`) that hides every
GridVibe window at once*, offered in both the workspace and launcher windows and
only in native mode — plus, separately, *minimizing any one GridVibe window
minimizes them all*. If you actually meant "hide the window chrome/buttons", say
so and I will redo this section; the second half of the note is window
management, which is why I took the first half the same way.

**Today.** The infrastructure is largely there:

- `GridVibeApi` already tracks every window: `_workspace_windows` keyed by
  workspace id, plus the launcher window (`webview_launcher.py:730-816`).
- Per-window minimized state is already tracked, with `minimized`, `restored`,
  `maximized` and `closed` events wired in `register_window()`
  (`webview_launcher.py:1771-1866`).
- `_restore_minimized_window()` (`:99`) already solves the WinForms
  `restore()`-shrinks-a-maximized-window problem with `ShowWindow(SW_RESTORE)`.
- `_bring_to_front()` (`:1247`) already does restore-then-show-then-pulse.
- `_run_on_native_ui_thread()` (`:132`) already exists for marshalling calls onto
  the WinForms UI thread, which any `minimize`/`hide` batch will need.

So this is mostly assembly, not new mechanism. pywebview 6.2+ (the pinned
floor in `requirements-desktop.txt`) exposes `window.minimize()`,
`window.hide()` and `window.show()`; `_restore_minimized_window()` is the safe
counterpart to `minimize()`.

### 4a. The hide-all control

| Option | Behaviour | Trade-off |
| --- | --- | --- |
| **4-A** | **Minimize all** — every GridVibe window to the taskbar. | Reversible from the taskbar by clicking any window. Familiar, and costs nothing. But it leaves N taskbar entries, so it is not really "get out of the way". |
| **4-B** ✅ | **Hide all / restore all** — `window.hide()` on every window, and one deliberate way back. | Genuinely clears the screen *and* the taskbar. Needs a way back, or the app is unreachable — the one real risk in this whole item. |
| **4-C** | **Hide all, with a tray icon** as the way back. | The complete answer, and what an app like this normally does. But it adds a tray dependency (`pystray`, or raw Win32 `Shell_NotifyIcon`) and a per-platform code path — a meaningful new surface for one control. |

**On the way back for 4-B.** Three candidates, in order of how much I like them:

1. **The same chord toggles.** `Ctrl+Alt+C` hides; pressing it again restores.
   Problem: with every window hidden, *no GridVibe window has focus*, so a
   page-level `keydown` handler cannot hear the second press. This needs an
   **OS-level global hotkey** (`RegisterHotKey` on Win32), which is genuinely new
   capability and can fail if another app already owns the chord.
2. **Hide all but one.** The window you pressed it in stays, shrunk to a small
   always-on-top strip, and clicking it restores the rest. No new OS surface, and
   there is always something to click. Less "clear the screen" than a true hide.
3. **Minimize all instead** (fall back to 4-A). The taskbar *is* the way back,
   and it costs nothing.

**Recommendation: 4-B with a global hotkey (`RegisterHotKey`), Windows only, and
an explicit fallback to 4-A (minimize all) wherever the hotkey cannot be
registered** — including a one-time notice saying so, since a hide with no way
back is the worst outcome here and must never happen silently. If you would
rather not take on a global hotkey at all, **4-A** is the honest cheap answer and
I would ship that instead.

**On the chord.** `Ctrl+Alt+C` is `AltGr+C` = `&` on your layout (see the
cross-cutting finding). A *global* hotkey is registered with the OS rather than
read from a keydown event, so `RegisterHotKey(MOD_CONTROL|MOD_ALT, 'C')` fires on
`AltGr+C` too and the `AltGraph` guard is not available at that level. **I would
pick a different chord** — `Ctrl+Shift+H` ("hide") is free at the OS level and
unambiguous. Say if you want `Ctrl+Alt+C` regardless.

**Scope.** Native mode only, in both windows. In browser mode the control is not
rendered at all rather than rendered-and-disabled: a browser tab cannot hide its
own window, and a disabled button with a tooltip is noise on a surface where
every other control works.

### 4b. The minimize cascade

| Option | Behaviour | Trade-off |
| --- | --- | --- |
| **4-D** ✅ | Cascade is **opt-in**, a setting, **off by default**. Minimizing any GridVibe window minimizes the rest. | Honest default: minimizing one window to look at something behind it is an ordinary thing to do, and having four other windows vanish is a surprise. Off by default means nobody meets it unasked. |
| **4-E** | Cascade always on in native mode. | Matches the note literally. But it makes the windows un-independent, which is much of the point of multi-workspace mode. |
| **4-F** | Cascade on the **launcher only** — minimizing the launcher minimizes everything; minimizing a workspace minimizes only itself. | Treats the launcher as the app's "main" window, which it sort of is. Asymmetric, and needs explaining. |

**Feasibility.** Straightforward: the `minimized` event is already wired per
window (`webview_launcher.py:1775`), so the cascade is "on `_handle_minimized`,
minimize every other tracked window". Two things to get right, both known traps
in this file:

- **Re-entrancy.** Minimizing the others fires *their* `minimized` handlers,
  which would each try to minimize everyone again. Needs a suppression flag
  around the batch, the same shape as the existing `_restarting` guard.
- **Restore is the harder half.** Should restoring one window restore them all?
  If yes, the same re-entrancy guard applies on `restored`, and the maximized
  edge case `_handle_maximized` already documents (`:1783`) applies too. **My
  recommendation: cascade the minimize, do *not* cascade the restore** —
  bringing four windows back because you clicked one taskbar entry is a much
  bigger surprise than the minimize, and `_bring_to_front()` already restores the
  one you asked for.

**Recommendation: 4-D**, minimize-only cascade, a checkbox in App Settings
(`workspace` section, native-mode-only, hidden in browser mode), default off.

**Cost for item 4 overall:** medium. New bridge methods on `GridVibeApi`, event
handling changes in `register_window()`, one setting through `RuntimeConfig` +
`/api/app-config` normalization, a button in two templates, and — if you take the
global hotkey — a Win32 `RegisterHotKey` path with a message loop, which is the
single biggest piece of new mechanism in this document. Worth splitting: ship the
cascade (4-D) and a plain minimize-all button (4-A) first, and decide on true
hide + global hotkey afterwards.

---

## Item 5 — Top-bar dropdowns are hard to reach

> *"the dropdown functionality on the top bar in workspace windows is a bit
> clucnky, its hard to reach sesion and workspace dropdowns from the middle
> without loosing the dropdown because we went out of top bar range. Lets move
> the menu dropdowns to the middle buttons on the left and have it all
> centered."*

**Today — and this diagnosis is the whole item.** The two are on opposite sides
of the bar:

- The peek handle that reveals a hidden bar is at **`left: 50%`**, 46px wide, 4px
  tall — dead centre of the top edge (`terminals.css:1697-1710`).
- `Sessions…` and `Workspace…` are the **first things in `.topbar-brand`**, which
  is `justify-self: start` — the far **left** (`terminals.html:52-100`,
  `terminals.css:536-542`).
- The bar is a 3-column grid `minmax(0,1fr) auto minmax(0,1fr)` with the brand in
  column 1 and `.topbar-actions` centred in column 2. **Column 3 is empty.**

So: you reveal the bar at the centre, then travel roughly half the window width
to the left along a 50px-tall strip to reach the menu you wanted.

Retention is *not* the bug — `isRetained()` correctly holds the peek while the
pointer is in the bar, while focus is in it, or while a menu is open
(`topbar-peek.js:64`), and the menu panel is a DOM descendant of `.topbar`, so
moving down into it does not count as leaving. The bug is **the distance**: any
downward drift during that traverse leaves the bar's box, starts the 320ms
`CLOSE_GRACE_MS` countdown, and the bar is gone before you arrive. Your instinct
— put the menus where the handle is — is exactly right.

| Option | Change | Trade-off |
| --- | --- | --- |
| **5-A** ✅ | Move `Sessions…` and `Workspace…` out of `.topbar-brand` into the **centre column**, directly under the peek handle. Icon and session label stay left; the action icons move to the empty column 3. | Travel drops to near zero. Purely markup + CSS — the menu JS (`toggleSessionsMenu`, `reportAppMenuState`) is untouched, and the panels stay DOM descendants of `.topbar`, so retention is unchanged. Cost: the centre gets crowded unless the action icons genuinely move right. |
| **5-B** | Keep the menus where they are and **move the peek handle to the left**, over them. | Smaller diff. But the handle is centred *on purpose* — so it is findable, and so the rest of the top edge stays inert; moving it left makes it easy to hit by accident when reaching for a pane's top-left corner. |
| **5-C** | Keep both positions and **widen the retention area**: an invisible pointer-catching band a few pixels below the revealed bar, so a downward drift is not a leave. | Smallest diff, no layout change. But it is a workaround for a distance problem, and an invisible band swallowing pointer events over the terminal grid is the kind of thing that causes a different complaint later. |
| **5-D** | 5-A **plus** raise `CLOSE_GRACE_MS` from 320ms. | I would not. 320ms is tuned so the bar is gone by the time you have looked back at the pane; a longer grace makes it linger over content. With 5-A the grace never gets a chance to matter. |

**Recommendation: 5-A**, laid out as:

```
[icon] [session label .....]   [Sessions…] [Workspace…]   [theme][surface][broadcast][full][gear]
     column 1 (start)              column 2 (center)                 column 3 (end)
```

That uses the empty third column, keeps the two menus directly under the peek
handle, and leaves the drag region (`-webkit-app-region: drag`) intact — the
buttons already opt out with `no-drag` (`terminals.css:633-637`).

**Things to check when implementing:**

- `.app-menu-panel` is `left: 0` on a `position: relative` parent
  (`terminals.css:572-575`). Centred, the `Workspace…` panel could overflow the
  right edge on a narrow window; it needs a `right: 0` variant or a clamp.
- `body.surface-max` shrinks the bar to 38px and already has per-element rules
  (`terminals.css:1647`, `:1750`); the centred group needs one too.
- `topbar-peek.js`'s `BAR_FOCUS_ID` is `sessionsMenuBtn` — the keyboard route in
  focuses that button. Moving it to the centre makes that route *better*, not
  worse, but re-check the constant rather than assuming.
- `README.md:192` describes the top bar's contents in order; it needs updating.

**Cost:** small-to-medium, and it is all markup/CSS. No behavioural test should
need to change — `tests/test_topbar_peek.py` asserts the policy, which does not
move.

---

## Item 6 — A configurable keybind section in Settings

> *"Settings needs a keybind section with all of the keybinds listed and made
> configurable with proper exclusivity withing gridvibe and warnings when using
> OS browser specific keybinds (Best effort)"*

**Today.** Every keybind is a hardcoded condition inside its own `keydown`
handler. There is no registry, so:

- Nothing can *enumerate* the bindings, which is why the only list of them is the
  hand-maintained table at `README.md:198-208` — and it is already incomplete
  (it omits `Ctrl+Shift+C` copy, `Ctrl+V` paste, and the `Alt`-click fold
  gestures).
- Nothing can detect a conflict. Two handlers matching the same chord both run.
- There is exactly **one** configurable keybind today — the voice push-to-talk
  chord — and it already has every part a general solution needs, at a small
  scale: a capture input (`app-settings.js:715`), a formatter
  (`formatAppPttKeybind`, `:499`), a validator (`isValidAppPttKeybind`, `:511`),
  a matcher (`_matchesPttKeybind`, `voice-input.js:1021`), and durable storage in
  `config.json` under `voice_prefs` via `/api/voice-prefs`
  (`web/voice.py:597-616`).

That last point matters: **the precedent for a config-backed, dialog-edited,
server-persisted keybind already exists and works.** A `keybinds` section is the
same pattern, generalised.

### The inventory (what the section would list)

| Chord | Action | Where it lives | Configurable? |
| --- | --- | --- | --- |
| `Alt+1`–`Alt+9` | Switch session group | `terminals.js:7200` | yes (as a block) |
| `Alt+W` / `Alt+Shift+W` | Next / previous workspace | `terminals.js:7264` | yes |
| `Alt+W` (launcher) | Return to origin workspace | `launcher.js:3836` | follows the above |
| ``Alt+` `` → *item 3* | Open the launcher | `terminals.js:7283` | yes |
| `Ctrl+F` | Find in the open file | `terminals.js:7014` | yes |
| `Ctrl+Shift+F` | Repo search (explorer) / scrollback search (terminal) | `terminals.js:7174`, `:4411` | yes |
| `Ctrl+Shift+V` | Toggle Markdown preview | `terminals.js:8231` | yes |
| `F5` | Refresh the focused explorer | `terminals.js:7029` | yes |
| `Ctrl+Shift+E` → *item 3* | Enter / leave explorer edit | (new) | yes |
| `Ctrl+Alt+C` → *item 4* | Hide all windows (native) | (new) | yes |
| `Ctrl+S` | Save in the explorer editor | `explorer-editor.js:417` | **no** — universal |
| `Esc` | Cancel edit / close menu / drop selection / dismiss peek | 6+ handlers | **no** — shared, narrowly claimed |
| `Ctrl+Shift+C` | Copy terminal selection | `terminals.js:4436` | **no** — terminal convention |
| `Ctrl+V` | Paste to terminal | `terminals.js:4442` | **no** — terminal convention |
| `Tab` | Indent in the editor | `explorer-editor.js:407` | **no** |
| `Enter` / `Shift+Enter` / `↑` `↓` | Step find matches | every find bar | **no** |
| `Alt`+click | Fold sibling level (tree) / collapse all (Git graph) | `explorer-tree.js:519`, `explorer-git-sidebar.js:1663` | **no** — a pointer gesture, not a chord |
| PTT chord | Push-to-talk | `voice-input.js:1021` | **already configurable** |

Roughly **ten** chords are genuinely reconfigurable; the rest are either
conventions you would break by moving them, or not chords at all. The section
should still *list* the fixed ones — an unlisted `Ctrl+Shift+C` is exactly the
kind of thing you go to a keybind dialog to look up.

### Options

| Option | Scope | Cost |
| --- | --- | --- |
| **6-A** | **Reference list only.** A read-only "Keyboard" section in App Settings enumerating every chord above, generated from one registry so it cannot drift from the code. No editing. | Small. One registry module + one settings panel. Solves "listed", not "configurable". |
| **6-B** | **Fully configurable, everything at once.** Registry + capture UI + conflict detection + reserved-chord warnings + `config.json` persistence + every handler rewritten to read from the registry. | Large. Touches ~12 handlers across three files, adds a config section, a new DOM-free module with its own Node tests, and a settings-file migration story. |
| **6-C** ✅ | **Two passes.** *Pass 1* builds the registry and ships 6-A (the list), and rewrites the handlers to read their chord from the registry — the chords keep their current values, so nothing user-visible moves. *Pass 2* adds the capture UI, conflict checking, warnings and persistence on top of a registry that is already proven. | Medium then medium. Each pass is independently shippable, and pass 1 alone kills the drifting `README.md` table. |

**Recommendation: 6-C.** Pass 1 is what makes pass 2 safe: rewriting ten handlers
to read from a registry while *also* introducing user-editable values means a
regression in either half looks like a regression in the other.

### Design sketch (applies to 6-B, or 6-C pass 2)

Per the repo's own architecture rules — a DOM-free, Node-tested policy module
plus a thin adapter:

- **`web/static/js/keybinds.js`** — DOM-free, Node-tested. Owns:
  - the **registry**: `{id, label, chord, default, scope, fixed, surface}` per
    binding, where `scope` is `workspace` / `launcher` / `both` and `surface` is
    `terminal` / `explorer` / `window` (so the dialog can group them the way you
    think about them);
  - `parseChord()` / `formatChord()` — one spelling of a chord, so the dialog,
    the `README.md` table and the matcher cannot disagree;
  - `matchesChord(event, chord)` — the one matcher, **with the `AltGraph`
    rejection built in** so no handler can forget it;
  - `conflicts(registry)` — the "proper exclusivity" the note asks for. Two
    bindings conflict when their chords are equal **and their scopes overlap**;
    `Ctrl+Shift+F` meaning two things on two different pane kinds is *not* a
    conflict, it is the existing (correct) behaviour, and a naive equality check
    would flag it;
  - `reservedWarning(chord, mode)` — best-effort, and honest about being
    best-effort. Three warning classes, not one:
    1. **Layout** — `Ctrl+Alt+<letter>` is `AltGr` on many European layouts (the
       cross-cutting finding above). Warn always.
    2. **Browser** — `Ctrl+W`, `Ctrl+T`, `Ctrl+N`, `Ctrl+Shift+T/N`, `Ctrl+Tab`,
       `Ctrl+1`–`9`, `Ctrl+L`, `Ctrl+D`, `Ctrl+P`, `F1`, `F3`, `F6`, `F11`,
       `F12`, `Ctrl+Shift+I/J/C/E`. **Warn in browser mode only** — in the native
       WebView2 window most of these are free, and warning about them there is a
       false alarm that teaches you to ignore the warnings.
    3. **OS** — `Alt+F4`, `Alt+Tab`, `Ctrl+Alt+Del`, anything with `Win`. Warn
       always; these never reach the page at all.
  - Warnings are **advisory, never a refusal** ("Best effort", per the note). A
    conflict *within* GridVibe is a refusal, because it has one right answer.
- **`config.json` → `keybinds`**, reached through `RuntimeConfig` like every
  other setting, normalized in `/api/app-config` and persisted through
  `web/state_files.py`. Store **only non-default** chords, so a chord whose
  default changes in a later release follows the release rather than being pinned
  by a settings file that recorded the old value.
- **The dialog panel** reuses the push-to-talk capture input's exact interaction
  (`click, press keys`; `Backspace`/`Delete` clears). It already works, is already
  styled, and a second capture idiom in the same dialog would be worse than
  either one alone.
- **A `Reset to defaults` control**, per row and for the whole section. A keybind
  editor with no way back is a way to lock yourself out of your own app.

**Also worth doing in pass 1:** generate the `README.md` shortcut table from the
registry, or add a test asserting the table and the registry agree. It is already
out of date, and it will be out of date again by the next release otherwise.

**Cost for item 6 overall:** the largest of the four, but the most cleanly
splittable. Pass 1 is worth doing on its own merits even if configurability is
never built.

---

## Suggested order

1. **Item 5** — smallest, purely layout, immediately felt, no dependencies.
2. **Item 3** — small, and it settles the two chords item 6 would otherwise have
   to list as "TBD".
3. **Item 6 pass 1** — the registry, with item 3's new chords already in it.
4. **Item 4** — the cascade (4-D) first, then decide on hide-all + global hotkey.
5. **Item 6 pass 2** — configurability, on a registry that has been live for a
   release.

Items 3 and 6 are coupled in one direction only: doing 3 first means the registry
is built once, with the final chords. Doing 6 first means editing it again.

---

## Decisions

Fill in and this is ready to build.

| Item | Chosen | Notes |
| --- | --- | --- |
| 3a — launcher chord | | keep ``Alt+` `` in parallel? y/n |
| 3b — edit chord | | |
| 4a — hide-all | | chord: `Ctrl+Shift+H` or `Ctrl+Alt+C`? |
| 4b — minimize cascade | | cascade the restore too? y/n |
| 5 — top bar layout | | |
| 6 — keybind section | | |
