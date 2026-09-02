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
| 3 | Replace the ``Alt+` `` launcher keybind; add an Edit keybind | **3-I** (`Alt+Q`) + **3-E** (`Ctrl+Shift+E` toggles Edit) |
| 4 | Native desktop mode: minimize-all control + minimize cascade | **DECIDED — 4-A** (minimize all) + **4-D** (opt-in cascade, off by default) |
| 5 | Top-bar dropdowns are hard to reach from the peek handle | **DECIDED — 5-E** (one menu in the session tab line; top-bar menus removed; status string in the bar, toast when it is hidden) |
| 6 | Settings needs a configurable keybind section | **DECIDED — 6-D** (one top-bar button opening a read-only shortcut panel; the registry, the configurability and the warnings are dropped) |

---

## ⚠️ Two findings that cut across items 3, 4 and 6

### The first: `AltGr` arrives as `Ctrl+Alt`

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
- **`Ctrl+Alt+C`** (note 4, the hide-all control) is `AltGr+C` = **`&`**.

This is not a reason to abandon either idea, but it means:

1. Any `Ctrl+Alt+<letter>` binding **must** reject `AltGr` explicitly —
   `event.getModifierState('AltGraph')` is true for a real `AltGr` press and
   false for a genuine `Ctrl+Alt` press, on Chromium/WebView2 and Firefox on
   Windows. That check is cheap and reliable, but it is one more thing every
   handler has to remember — and since item 6 is now a read-only list rather
   than a central matcher, there is nowhere to remember it *for* them. Every
   binding carries the clause itself.
2. Even with the guard, `Ctrl+Alt+<letter>` is a chord some layouts cannot
   *produce* without also producing a character — so the guard makes the binding
   unreachable for the very user it was chosen for. **A `Ctrl+Alt` chord is a
   poor default on this machine.** The options below reflect that.
3. There is no validator to hand it to. Item 6 no longer builds one, so this
   stays a rule for whoever picks a chord — which is exactly why it is written
   down here rather than left to be rediscovered.

### The second: the layout is **QWERTZ**, so a letter is not a position

`Z` and `Y` are swapped against QWERTY. On a Slovenian layout `Z` sits in the
**top row between `T` and `U`**, and `Y` is the bottom-left key next to `X`. So
any chord picked for where it sits on the keyboard — which is most of the
reasoning in items 3 and 4 — is picked for a *physical key*, and naming it by a
letter silently picks a different one here.

That splits into two rules, and GridVibe already follows the first by accident:

1. **Match a positional chord on `event.code`.** ``Alt+` `` is matched as
   `event.code === 'Backquote'` (`terminals.js:7283`) precisely so a layout where
   that key is a dead accent still reaches the launcher — the same reasoning
   applies to every letter chord chosen for its position. `event.code` is
   QWERTY-named (`KeyZ`, `KeyY`) and reports the **physical** key regardless of
   layout.
2. **Name it to the user by what their layout prints.** `event.code === 'KeyZ'`
   is the bottom-left key everywhere, but on this machine it types `y` — so a
   settings dialog, a `README.md` table or a tooltip that says "Alt+Z" is
   describing a key the user does not have there. `event.key` is what to *show*;
   `event.code` is what to *match*.

The safe way out of the whole problem is to prefer letters that **do not move
between QWERTY and QWERTZ** — everything except `Y` and `Z` — so the code and the
printed name agree and neither rule can be got wrong. That is what item 4's chord
ended up doing.

**For item 6:** no chord is recorded or replayed any more, so the storage half
of this falls away — but the display half does not. The shortcut panel and the
`README.md` table are the two places a chord is *named*, and both must print what
the user's layout prints while the handler beside them keeps matching
`event.code`. The two rules stay; they are now the handler's own discipline and
the panel's own copy rather than a `parseChord()`/`formatChord()` pair.

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
still reaches the launcher.

**The failure is a residue, not a lost event.** The binding works: the launcher
opens. What it *also* does is leave the dead accent armed, so the next character
typed — in the launcher's own new-workspace field, most annoyingly — composes
with it and you get `¸newworkspacename`.

The mechanism is `ToUnicode`, which is what performs dead-key composition on
Windows, and which **ignores plain `Alt`**: only `Ctrl+Alt` (`AltGr`) counts as
a shift-state modifier. So ``Alt+` `` is translated by the layout exactly as a
bare `Backquote` press — on a Slovenian layout that key is dead cedilla (`¸`,
`¨` shifted) — and the accent goes into the layout's composition buffer. Both
things then happen: the page gets its keydown and opens the launcher, *and* the
composer stays armed. That buffer belongs to the keyboard layout for the thread,
so navigating to the launcher in the same window carries it across.

Three consequences, and between them they decide the option:

- **`event.preventDefault()` cannot fix it.** The arming happens during message
  translation, before any handler runs. It is not a default action the page can
  cancel, and there is no page-side flush — so this cannot be patched around on
  the existing chord.
- **The modifier decides it, not the key.** `Ctrl` *without* `Alt` is honoured
  by `ToUnicode`, and with `Ctrl` down the layout bypasses dead-key composition
  and produces nothing at all. The same physical key under a `Ctrl` chord
  therefore arms nothing.
- **`Ctrl+Alt` is the worst available answer**, over and above the pipe
  collision in the cross-cutting finding: it is precisely the shift state that
  keeps the composer engaged, so it reproduces the exact residue this item
  exists to remove.

**Constraint.** Whatever replaces it needs all four of: (a) a pass-through in
the xterm handler, (b) no collision with the browser in browser mode, (c) no
collision with `AltGr` on your layout, (d) no dead-key residue — which (c) does
not imply, and which is the whole point of the item.

**The residue belongs to the key, not to `Alt`.** Worth stating plainly, because
the obvious reaction to the mechanism above is to flee the `Alt` modifier
entirely — and that is an over-correction. `Alt+W` has never left an accent
behind, because `W` is not a dead key. `ToUnicode` ignoring `Alt` only matters
when the key it then translates *is* one. So the fix is to keep `Alt` — which is
already GridVibe's own navigation family (`Alt+1`–`Alt+9` for groups, `Alt+W`
for workspaces) and where the launcher belongs — and move off the dead key.
Ergonomics decides the rest, and it rules out the `Ctrl+Shift` chords: both
modifiers are left-pinky, `` ` `` is the top-left corner, and that is one hand
with no fingers left over.

| Option | Chord | Notes |
| --- | --- | --- |
| **3-I** ✅ | `Alt+Q` | One-handed and travel-free: `Alt` under the left thumb, `Q` on the left pinky. Joins the existing `Alt` navigation family, so the launcher stops being the odd one out. `Q` is a dead key on no layout, so no residue, and it is **unbound in readline**, so a focused pane gives up nothing to it. Requiring `!event.ctrlKey` excludes `AltGr` for free, exactly as the `Alt+W` handler already does. Free in Chrome, Edge and Firefox (their `Alt` accesskeys are `F`/`E`/`V`/`S`/`B`/`T`/`H`/`D`). Needs the xterm pass-through, same one line `Alt+W` has. |
| **3-H** | `Alt+G` | Same shape and an easier reach (index finger, home row) with a "GridVibe" mnemonic, but `\eg` **is** bound in readline (`glob-complete-word`), so the pass-through would take it from every pane. The lesser reach is worth more than the mnemonic. |
| **3-A** | `Ctrl+Alt+W` (as asked) | Rejected twice over. Needs the `AltGraph` guard, and is the pipe key on your layout, so the chord is effectively unavailable *to you*. Worse: `Ctrl+Alt` is the one modifier state `ToUnicode` honours as a shift state, so it is also the one that leaves the dead-key composer armed — it reproduces this very bug on a different key. |
| **3-B** | ``Ctrl+Shift+` `` | Correct on the mechanism (`Ctrl` suppresses composition, so the key arms nothing) and wrong on the hand: two left-pinky modifiers plus the top-left corner key. Listed for the record. |
| **3-C** | `Ctrl+Shift+L` ("launcher") | Mnemonic, free in Chrome/Edge. **Firefox reserves `Ctrl+Shift+L`**. Native mode unaffected. Same two-modifier awkwardness as 3-B. |
| **3-D** | `Alt+Home` | Unambiguous and dead-key-free, but two-handed and a chord nobody guesses. Chrome/Firefox bind plain `Alt+Home` to "go to homepage" — the page sees it first and `preventDefault()` stops it, so it works. |

**Recommendation: 3-I (`Alt+Q`).** One hand, no travel, in the family the
launcher already belongs to, immune to the residue because the key is not a dead
key, and — unlike every other candidate here — it takes nothing away from
anything else. Cost is unchanged from the original estimate: one line in the
handler, one in the xterm pass-through.

**It costs a pane nothing.** `bind -p` reports `\eq` unbound, so the xterm
pass-through claims a chord readline was not using. `Alt+G` would have taken
`glob-complete-word`, and `Alt+W` already takes `copy-region-as-kill` — this one
is free, and that is the tiebreak.

**Two things to note rather than discover later.** `AltGr+Q` is `\` on your
layout, and backslash is typed constantly in Windows paths — the `!event.ctrlKey`
guard the `Alt+W` handler already carries excludes it, but the binding must
never lose that clause. And `Q` sits next to `W`, so `Alt+Q` (launcher) and
`Alt+W` (next workspace) are adjacent one-handed chords; a slip goes to a
neighbouring destination rather than doing damage, and the adjacency arguably
helps the family read as one, but it is the honest downside of this choice.


**Worth stating plainly, since nothing will warn about it:** "is this key a dead
key on the active layout" would have been a fourth warning class had item 6 kept
its validator. It has not, so the guard is the choice itself — `Q` is a dead key
on no layout. It is the one hazard here whose symptom appears *after* the
shortcut has already worked, which is why it went misdiagnosed for so long, and
why a future chord change should re-read this paragraph.

**Open question for you:** should the old ``Alt+` `` keep working alongside the
new chord for a release (a deprecation overlap), or be removed outright? The
residue answers this: keeping it in parallel keeps the bug reachable by the
exact reflex you are trying to unlearn, and a deprecation overlap is for chords
that still work. **Remove it outright.**

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

## Item 4 — Native desktop mode: minimize-all control and minimize cascade

> *"NATIVE DESKTOP MODE: hide all button/keybind in workspace/launcher window
> (ctrl+alt+c) AND lets check if possible, minimizing laucnher/workspace
> minimizes all gridvibe windows in DESKTOP mode."*

**Decided: 4-A + 4-D.** One primitive — minimize every GridVibe window — with
two triggers: a control in each native window, and, when the setting is on,
minimizing any one window. The two halves of the note turned out to want the
same batch, which is what makes this the cheap answer.

**Reading.** I read this as *one control (button + a chord) that gets every
GridVibe window out of the way at once*, offered in both the workspace and
launcher windows and only in native mode — plus, separately, *minimizing any one
GridVibe window minimizes them all*. The second half of the note is window
management, which is why I took the first half the same way, and 4-A is what
settles "get out of the way" as *minimize* rather than *hide*.

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
  the WinForms UI thread, which the minimize batch will need.

So this is mostly assembly, not new mechanism. pywebview 6.2+ (the pinned
floor in `requirements-desktop.txt`) exposes `window.minimize()`, and
`_restore_minimized_window()` is the safe counterpart to it. Nothing here needs
`window.hide()`/`show()` any more — see 4a.

### 4a. The hide-all control — **decided: 4-A (minimize all)**

| Option | Behaviour | Trade-off |
| --- | --- | --- |
| **4-A** ✅ **chosen** | **Minimize all** — every GridVibe window to the taskbar. | Reversible from the taskbar by clicking any window, so the app can never become unreachable. Costs nothing beyond the batch itself. The honest downside stands: it leaves N taskbar entries, so it is "get out of the way" rather than "disappear". |
| **4-B** | **Hide all / restore all** — `window.hide()` on every window, and one deliberate way back. | Genuinely clears the screen *and* the taskbar. But the way back is the problem: with every window hidden **no GridVibe window has focus**, so no page-level `keydown` can hear the second press, and the only true answer is an OS-level global hotkey (`RegisterHotKey` + a message loop) that can itself fail if another app owns the chord. That was the single largest piece of new mechanism in this document. Not taken. |
| **4-C** | **Hide all, with a tray icon** as the way back. | The complete answer, and what an app like this normally does — but it adds a tray dependency (`pystray`, or raw Win32 `Shell_NotifyIcon`) and a per-platform code path for one control. Not taken. |

**One primitive, two triggers.** The control minimizes every GridVibe window;
the cascade (4b) minimizes every GridVibe window. So they are not two features —
they are `minimize_all_windows()` on `GridVibeApi`, marshalled onto the WinForms
UI thread through `_run_on_native_ui_thread()` (`:132`) and wrapped in the one
re-entrancy guard 4b needs anyway. The control calls it directly; the `minimized`
event calls it when the setting is on.

**Nothing hides, so nothing needs a way back.** The taskbar is the way back, and
`_restore_minimized_window()` (`:99`) already handles the
`restore()`-shrinks-a-maximized-window case for whichever entry the user clicks.
That also removes the global hotkey, the message loop, the tray dependency and
the "hidden with no way back" failure mode from the item entirely — not as a
separate decision, but as a consequence of this one.

**On the chord.** Still open, and it is the only thing left to pick here — but
two constraints from elsewhere in this document have loosened, and between them
they change the answer.

*The AltGr guard is available, and it still does not save `Ctrl+Alt+C`.* Because
the window that receives the press is on screen and focused, this is an ordinary
page-level `keydown` in both templates, so `event.getModifierState('AltGraph')`
can reject a real `AltGr` press. But availability is not reachability:
`Ctrl+Alt+C` as the note asks is `AltGr+C` = `&` on your layout (see the
cross-cutting finding), and with the guard in place the chord cannot be produced
on your own keyboard at all — the exact reason item 3 rejected `Ctrl+Alt+W`.

*Browser-reserved chords stop mattering here.* This control is native-mode-only
(see **Scope** below), so the chord only ever fires inside the WebView2 window,
which has no menu bar and therefore no `Alt`-accesskeys. The whole `Alt+<letter>`
family that item 3 had to check against Chrome/Edge/Firefox is free in this one
place, which is what makes a one-handed chord available at all.

*So it should be an `Alt` chord, for the same reasons item 3 gave.* `Ctrl+Shift+H`
is **rejected on the hand**: two left-pinky modifiers plus a right-hand letter is
a two-handed chord for a control whose entire purpose is to be hit in passing.
That is the same test that ruled out 3-B and 3-C, applied consistently.

| Option | Chord | Notes |
| --- | --- | --- |
| **4-I** ✅ | `Alt+X` | One-handed, bottom-left, and **far from the navigation family**: `Alt+Q` (launcher) and `Alt+W` (workspaces) are top-row chords, so a mis-reach cannot land on the one control that moves every window at once. `X` is `X` on **both** QWERTY and QWERTZ, so `event.code === 'KeyX'` and the name shown to the user agree — no second rule to get wrong. `\ex` is unbound in readline (verified with `bind -p`: only `b c d f g l n p r t u y` are taken), so the xterm pass-through claims nothing from a pane. `AltGr+X` is `#` on your layout and `!event.ctrlKey` excludes it, exactly as the `Alt+W` handler already does. The one objection is that `X` reads as *close*; against an action that is fully reversible from the taskbar, that is a smaller cost than either alternative below. |
| **4-H** | `Alt+A` | "**A**ll" is the better mnemonic, `\ea` is equally unbound, and `A` is stable across both layouts. Costs the adjacency: `A` sits directly under `Q` on the same finger, so the slip is launcher ⇄ minimize-everything. A slip between two *navigation* chords is the honest downside item 3 accepted; a slip that sweeps the whole app off screen is a different category, and it is the reason this is second and not first. |
| **4-G** ❌ | `Alt+Z` | **Withdrawn — QWERTZ.** Chosen for the bottom-left corner, which is where `Z` sits on QWERTY and *not* where it sits here: on a Slovenian layout `Z` is top row between `T` and `U`, and the bottom-left key types `y`. Matching `event.code === 'KeyZ'` would get the right physical key and then have to be *called* `Alt+Y` on this machine, and `\ey` is `yank-pop` in readline, so the pass-through would take something from every pane. Both halves fail; see the second cross-cutting finding. |
| **4-J** | `Ctrl+Alt+C` (as the note asks) | Needs the `AltGraph` guard, and with it is unproducible on your layout. Listed because it is what the note said. |
| **4-K** | No chord — the button only | Legitimate: the taskbar is the way back and the control is one click away in both windows. But the note asked for a keybind, and the batch is the same either way, so the chord costs one handler. |

**Recommendation: 4-I (`Alt+X`).** One hand, no travel, nothing taken from a
pane, the same key on both layouts so the matcher and the label cannot disagree,
and positioned so a mis-hit cannot land on it from the two chords you will
actually be using all day. Say the word if you would rather have the `Alt+A`
mnemonic and accept the adjacency.

**Scope.** Native mode only, in both windows. In browser mode the control is not
rendered at all rather than rendered-and-disabled: a browser tab cannot minimize
its own window, and a disabled button with a tooltip is noise on a surface where
every other control works.

### 4b. The minimize cascade — **decided: 4-D**

| Option | Behaviour | Trade-off |
| --- | --- | --- |
| **4-D** ✅ **chosen** | Cascade is **opt-in**, a setting, **off by default**. Minimizing any GridVibe window minimizes the rest. | Honest default: minimizing one window to look at something behind it is an ordinary thing to do, and having four other windows vanish is a surprise. Off by default means nobody meets it unasked; on, it is the same batch the 4-A control runs. |
| **4-E** | Cascade always on in native mode. | Matches the note literally. But it makes the windows un-independent, which is much of the point of multi-workspace mode. Not taken. |
| **4-F** | Cascade on the **launcher only** — minimizing the launcher minimizes everything; minimizing a workspace minimizes only itself. | Treats the launcher as the app's "main" window, which it sort of is. Asymmetric, and needs explaining. Not taken. |

**Feasibility.** Straightforward: the `minimized` event is already wired per
window (`webview_launcher.py:1775`), so the cascade is "on `_handle_minimized`,
when the setting is on, run the same `minimize_all_windows()` batch the control
runs". Two things to get right, both known traps in this file:

- **Re-entrancy.** Minimizing the others fires *their* `minimized` handlers,
  which would each try to minimize everyone again. Needs a suppression flag
  around the batch, the same shape as the existing `_restarting` guard. The
  control needs that flag too, for the same reason — which is the second reason
  the two triggers share one implementation rather than each growing their own.
- **Restore is the harder half, and is deliberately not cascaded.** Bringing
  four windows back because you clicked one taskbar entry is a much bigger
  surprise than the minimize, and `_bring_to_front()` already restores the one
  you asked for. Not cascading it also keeps the `restored` handler and the
  maximized edge case `_handle_maximized` documents (`:1783`) out of the guard's
  scope entirely.

Settings shape: a checkbox in App Settings (`workspace` section,
native-mode-only, hidden in browser mode), through `RuntimeConfig` +
`/api/app-config` normalization like every other setting, default off.

**Cost for item 4 overall:** small-to-medium, and it is now one mechanism rather
than two. One bridge method on `GridVibeApi` (`minimize_all_windows()`, with the
re-entrancy guard), a `minimized` handler change in `register_window()`, one
setting through `RuntimeConfig` + `/api/app-config`, a button in two templates
and a page-level `keydown` in each. No tray dependency, no `RegisterHotKey`, no
message loop. No reason to split it either — the button and the cascade call the
same batch, so shipping them together is less work than shipping them apart.

---

## Item 5 — One session/workspace menu, seated in the tab line

> *"the dropdown functionality on the top bar in workspace windows is a bit
> clucnky, its hard to reach sesion and workspace dropdowns from the middle
> without loosing the dropdown because we went out of top bar range. Lets move
> the menu dropdowns to the middle buttons on the left and have it all
> centered."*

> **Decision:** *"just add a new button left of the launcher pop up button in the
> session tab rows that will have session/workspace dropdown menu with right hand
> chevrons with the same options we have currently in the existing menus. Top bar
> menus can be removed and only the save notifications string like we already
> have next to the existing buttons can remain and be displayed when the top bar
> is displayed, else we just have a little toast on succesfull save/import
> etc..."*

**DECIDED — 5-E.** One menu button immediately **left of the launcher button in
the session tab line**, holding both menus' contents as two chevron-disclosed
sections. The two top-bar menus are **removed**. The save/status string stays
where it is in the top bar, and when the bar is out of the flow the same message
goes to the workspace page's existing toast instead.

**Why this supersedes 5-A.** The diagnosis under 5-A was right — the peek handle
is centred and the menus were at the far left, so reaching them meant traversing
half the window along a 50px strip — but 5-A answered it by moving the menus to
the centre column of a bar that still has to be *revealed* first. This answers it
one level up: the session tab line is always in the flow (it is precisely what
the chevron leaves behind), so the menus stop being reveal-then-traverse and
become one click, and `CLOSE_GRACE_MS` never enters into it at all. It also
retires the layout question 5-A opened — no crowded centre column, no `right: 0`
panel variant, no `body.surface-max` rule for a centred group. The superseded
options are kept at the end for the record.

**Today.**

- `Sessions…` and `Workspace…` are the first two children of `.topbar-brand`
  (`terminals.html:52-155`), which is `justify-self: start` — the far left of a
  3-column grid whose third column is empty (`terminals.css:536-542`).
- Both are `.app-menu` roots with a `.app-menu-toggle` button and an absolutely
  positioned `.app-menu-panel` (`terminals.css:550-620`), driven by
  `toggleSessionsMenu` / `toggleWorkspaceMenu` and their `close*` partners
  (`terminals.js:1315-1367`).
- The session tab line already leads with the launcher button
  (`terminals.html:207-224`, `terminals.css:927-935`) for exactly the reason this
  item now extends: *it stays reachable with the top bar hidden.*
- `#sessionLabel` (`.topbar-sub`, `terminals.html:156`) is the session line
  **and** the transient save-status string — `setWorkspaceSaveMessage()`
  (`terminals.js:2128`) writes it with a `data-workspace-save-status` type and a
  timer, and `clearWorkspaceSaveMessage()` hands the line back through
  `renderSessionLine()`. 22 call sites.
- The toast this decision needs **already exists**: `showTerminalToast()`
  (`terminals.js:8239`) builds `#terminalToast`, `role="status"`,
  `aria-live="polite"`, auto-dismiss at 4 s, with `success` / `error` variants
  styled at `terminals.css:3473`. It is not the launcher's global banner — that
  one is `index.html` only — so nothing here touches guardrail 8's one-surface
  rule for the launcher.

So the mechanism for both halves of this decision is on the shelf; what follows
is placement, one disclosure model, and the six things that hold a reference to
the menus being removed.

### 5e-a. The button and the menu

**Placement.** A new `.session-bar-menu-btn` as the first child of `.session-bar`
(`terminals.html:207`), ahead of the launcher anchor. The launcher button owns
the line's leading gutter today (`margin: 8px 0 8px 12px`,
`terminals.css:927-932`); the new button takes that gutter and the launcher keeps
only the inter-button gap, so the tab strip's start does not move. It needs its
own `body.surface-max` rule beside the launcher's (`terminals.css:1841`) and the
chevron's (`:1849`) — and `body.surface-max .app-menu-toggle` (`:1805`) goes with
the menus it sized.

**Icon, not a word.** Guardrail 7: a stroke-`currentColor` SVG, sized to match
the launcher glyph beside it, with the label on `title`/`aria-label`. Two text
buttons in a strip whose whole job is horizontal room is what the tab line cannot
afford; the two words move inside the panel as the section headings.

**One panel, two sections, chevron disclosure.**

```
[≡] [launcher] [ tab · tab · tab … ]                                   [chevron]
 └─ Sessions                                                    ›
    Workspace                                                   ›
```

Opening a section reveals that section's existing items, indented, and closes the
other — at most one open at a time, exactly as `_expandedShellAgentRows` holds at
most one shell family open per pane menu (`terminal-shell.js:43-46`). Expansion
is a pointer gesture **inside one opening of the menu**: never pane state, never
persisted, reset when the menu closes. That is the same contract the reset
dropdown's chevrons live by, and it is the reason neither needs an invalidation
hook anywhere.

**DECIDED — the chevron is the control, and the row itself does nothing.** The
reset dropdown's shape, kept: opening the menu shows `Sessions` and `Workspace`,
and each carries a **right-hand chevron button** that reveals that section's
options. Pressing a section row is not an action.

That settles the one thing that had to be decided here rather than copied.
`terminal-shell.js` makes the chevron a control *beside* the row because a shell
row has an action of its own — pressing it relaunches that family, so the agent
list cannot be a second meaning for the same press, and a button inside a button
is not a control. These rows have no action, so the same rule lands in the other
place: with the chevron as a real button, **the row must not be a button too**,
or it is a control whose larger half looks pressable and does nothing.

So a section row is a non-interactive heading with one control on it, which the
app already spells twice — `.workspace-submenu-title` (`workspaces.css:62`,
`terminals.html:124`, `:131`) is exactly that, and it is already inside the
Workspace panel this menu absorbs. Mark the row up as a `role="presentation"`
heading, the chevron as its `<button aria-haspopup="menu" aria-expanded>` labelled
for its section, and the revealed items as a `role="group"` — the shape
`pane-shell-menu-sub` already uses (`terminal-shell.js:235`).

**The cost this buys, stated rather than discovered:** the hit target for opening
a section is the chevron alone, so the word `Sessions` is not clickable. That is
what makes the row unambiguous, and it is the reset dropdown's own behaviour, but
it is a smaller target than a full-width row. If it reads as fiddly in use, the
cheap remedy is to widen the chevron button to the row's full height and give it
the trailing third of the row's width — never to make the row a second opener,
which puts the dead half back.

**The two dynamic workspace lists do not move.** `refreshWorkspaceMenuLists()`
(`terminals.js:1417`) renders `#openWorkspaceList` and `#moveWorkspaceList` into
their `.workspace-submenu` blocks, and those stay as they are — nested *inside*
the Workspace section rather than growing chevrons of their own. Two levels of
disclosure is the reachability problem this item exists to remove, and pushing
the change into the workspace-list rendering turns a markup move into a rewrite
of code this item has no quarrel with. The section is still built on open, so
`refreshWorkspaceMenuLists()` is called when **Workspace** is expanded rather
than when the menu opens — a menu opened for `Save Session` should not cost a
`fetchLiveWorkspaces()`.

**Where the code goes.** Guardrail 6: a substantial new frontend surface gets its
own file, and `terminal-shell.js` is the precedent this is closest to — a
DOM-free model plus a thin adapter. `web/static/js/session-menu.js` owns the
menu's model (the section registry, which section is open, what each row sends)
and its markup builder; `terminals.js` keeps the wiring and the handlers the rows
call, which are unchanged functions it already has. Node-tested by *driving* it,
the way `tests/test_terminal_shell_menu.py` drives the reset dropdown through its
own click handler. This is a merge of two existing menus, so the model is small —
but the disclosure state is exactly the kind that is worth executing in a test
rather than reading.

**Panel geometry.** `.app-menu-panel` is `position: absolute; top: calc(100% +
6px); left: 0` on a `position: relative` parent (`terminals.css:572-585`), which
is right for a button at the far left of the line — no `right: 0` variant needed.
Two things do change:

- **Clipping.** `.session-tabs` carries `overflow-x: auto` but `.session-bar`
  does not, so a panel anchored on a *sibling* of the tab strip is not clipped.
  Keep the anchor outside `.session-tabs` — putting the button inside the strip
  would hide the panel and scroll the button away with the tabs.
- **Stacking.** `z-index: 50` was enough inside the top bar; from the session bar
  the panel overlays the grid and must beat the pane-level surfaces —
  `.pane-shell-menu` and `#terminalResizeOverlay` are both `60`
  (`terminals.css:1256`, `:1031`), the pane actions menu is `40` (`:1203`). Well
  above those and well below the modals (`3000`), the peek zone (`3900`) and the
  revealed bar (`4000`): ~`200`.

### 5e-b. What removing the top-bar menus takes with it

Six live references, and the first two are the ones that would ship broken:

1. **`topbar-peek.js`'s `menuOpen` retention loses its only producer.**
   `isRetained()` holds the peek open while `pointerInside || focusInside ||
   menuOpen` (`topbar-peek.js:64`), and `reportAppMenuState()`
   (`terminals.js:396`) is what feeds the third from these two menus. With the
   menus in the always-visible session bar, nothing in the bar opens a menu any
   more. Guardrail 5 says don't ship a callback nothing wires up, so
   `setMenuOpen`, the `menuOpen` state field and their Node coverage come out
   with it — and `reportAppMenuState()` goes entirely.
2. **`BAR_FOCUS_ID = 'sessionsMenuBtn'`** (`topbar-peek.js:44`) names an element
   that will not exist. It is the keyboard route in — activating the handle puts
   focus on the first control in the bar — so it must repoint at
   `themeToggleBtn`, which becomes the first control there. A missing id is a
   silent no-op, which is the worst version of this bug.
3. **`EXPLORER_ESCAPE_CLAIM_SELECTOR`** lists `#sessionsMenuRoot.open` and
   `#workspaceMenuRoot.open` (`explorer-viewer.js:1559-1560`) so that `Esc` over
   an explorer pane closes an open menu instead of clearing the pane's selection.
   One selector for the new root replaces both.
4. **`tests/test_api.py:20447-20448`** asserts `id="workspaceMenuRoot"` and
   `id="workspaceMenuBtn"` in the rendered page.
5. **`README.md:192`** ("Plus a `Workspace…` menu and a `Sessions…` menu") and
   **`:194`** — the hover-reveal paragraph whose stated justification is that
   `Save Session` and `Save Workspace` stay reachable with the bar hidden.
6. **`terminals.css:1805`** (`body.surface-max .app-menu-toggle`) and the
   `.app-menu*` blocks at `:550-620`, which the new panel reuses rather than
   duplicates — the sections and rows should stay `.app-menu-item`.

**The peek's rationale is the honest casualty, and it survives the loss.** Both
the module header (`topbar-peek.js:11-17`) and `README.md:194` justify the whole
feature by the menus: *"hiding the bar to get room took away the controls you
hide it in order to keep using."* After this change the bar holds theme, max
surface, broadcast, fullscreen, App Settings, the session line and the save
status — still worth reaching without un-hiding the bar, but no longer the
argument that was written down. **Keep the peek and rewrite the rationale**; do
not quietly leave a header explaining a mechanism for a reason that has moved out
of the bar. Removing the peek is a bigger decision than this item, and the
save-status string (5e-c) gives the revealed bar a live reason to exist.

### 5e-c. The status string and the toast

**One message, one surface, decided once.**

- **The string stays where it is.** `#sessionLabel` keeps both jobs — session
  line and transient save status — and `setWorkspaceSaveMessage()` /
  `clearWorkspaceSaveMessage()` are unchanged for the visible case. Nothing about
  the 22 call sites changes.
- **A bar that is out of the flow sends the same message to
  `showTerminalToast()`** instead, with the existing `success` / `error` type
  mapping. `setWorkspaceSaveMessage()` is the one place that decides, so no call
  site learns about the split.
- **Route on the flow, not on the peek.** The test is
  `body.classList.contains('topbar-hidden')` — `topbar-peek.js`'s own `isHidden`,
  the chevron **or** fullscreen — and explicitly **not** `topbar-peek`. A message
  written into a bar that is only peeking is a message that leaves 320 ms after
  the pointer does. A hidden-but-peeking bar therefore gets the toast, which is
  visible either way.
- **Never both.** Guardrail 8's rule that one outcome never reports to two
  surfaces is the whole point; the failed-update-reporting-twice bug is what it
  was written for.
- **A live message does not migrate.** Hiding the bar while a status string is up
  does not move it to a toast, and vice versa. The surface is chosen when the
  message is raised, and each dismisses on its own timer
  (`WORKSPACE_SAVE_MESSAGE_MS` / the toast's 4 s).

**Fix `browser-pane.js` while in there.** It writes three error strings straight
into `#sessionLabel` (`browser-pane.js:410`, `:421`, `:537`) — bypassing
`setWorkspaceSaveMessage()`, so no status type, no timer, and the session line is
only restored by whatever calls `renderSessionLine()` next. Once the label can be
hidden, those errors become invisible. Route them through the same helper, which
fixes the existing leak as a side effect.

### Superseded options

Kept because the reasoning still explains why the decided answer is shaped the
way it is.

| Option | Change | Why not |
| --- | --- | --- |
| **5-A** ⛔ | Move the two menus into the top bar's empty centre column, under the peek handle. | Was the recommendation. Shortens the traverse but keeps the menus inside a surface that must be revealed first; 5-E removes the reveal instead of shortening it. |
| **5-B** ⛔ | Move the peek handle left, over the menus. | The handle is centred on purpose — findable, and the rest of the top edge stays inert. Moot once the menus leave the bar. |
| **5-C** ⛔ | Widen the retention area with an invisible band under the revealed bar. | A workaround for a distance problem, and an invisible band swallowing pointer events over the grid causes a different complaint later. |
| **5-D** ⛔ | 5-A plus a longer `CLOSE_GRACE_MS`. | 320 ms is tuned so the bar is gone by the time you have looked back at the pane. With the menus out of the bar the grace never gets a chance to matter. |

**Cost:** medium, and no longer markup-only — that was 5-A's property, not this
one. One new static JS file with its own Node test, a markup move plus one new
button in `terminals.html`, CSS for the button and the panel's new stacking,
`setWorkspaceSaveMessage()` gaining one branch, three `browser-pane.js` writes
rerouted, and the six references above. `tests/test_topbar_peek.py` **does**
change here (the `menuOpen` retention input goes away), which is the one place
this differs from the earlier estimate.

**Settled, no open questions:**

- **Nothing is expanded when the menu opens.** Both sections start collapsed and
  a chevron press reveals one — the reset dropdown's behaviour, where nothing is
  emitted per family until a chevron is pressed.
- **No keybind.** The button is mouse-only — item 3 has already spent the good
  one-handed `Alt` chords and this control is one click away in a bar that is
  always in the flow. With no chord it has no row in item 6's shortcut panel
  either.

---

## Item 6 — A shortcuts button in the top bar

> *"Settings needs a keybind section with all of the keybinds listed and made
> configurable with proper exclusivity withing gridvibe and warnings when using
> OS browser specific keybinds (Best effort)"*

> **Decision:** *"we will be ditching this whole thing and only add a button to
> the top bar which will pop up a tooltip on press with all of the currently
> available shortcuts. That's all. The tooltip should have GridVibe styling and
> be dark/light modes specific."*

**DECIDED — 6-D.** One button in the workspace top bar. Pressing it opens a
panel listing every shortcut GridVibe currently answers to. Nothing is editable
and nothing is persisted: **no registry, no `keybinds` config section, no
capture input, no conflict checker, no reserved-chord warnings, and not one
handler rewritten.** 6-A, 6-B and 6-C are superseded and kept at the end for the
record.

**Why the small answer is the right one.** The note asked for configurability,
exclusivity and best-effort warnings, and the design that satisfies it is by
some distance the largest thing in this document: a registry every handler reads
from, a chord parser/formatter storing a `code` and rendering a `key`, one
matcher with the `AltGraph` rejection built in, a scope-aware conflict checker,
three classes of warning, a config section persisted through `state_files.py`,
and a rewrite of roughly a dozen handlers across three files. What all of that
buys is the ability to *move* about ten chords. Items 3 and 4 have just picked
those chords deliberately — one-handed, dead-key-free, stable across QWERTY and
QWERTZ, and taking nothing from readline — so the thing that was actually
missing was never the ability to move them. It was a way to *see* them without
opening `README.md`. This ships that half and none of the machinery.

**What is given up, stated now rather than rediscovered later:**

- **Chords stay hardcoded.** One that collides with something else on your
  machine can only be changed by editing the source. Items 3 and 4 chose against
  the known collisions, and the cross-cutting findings above are why.
- **Nothing detects a conflict.** Two handlers matching one chord would both
  run. None do today, and the panel makes a new collision *visible* — two rows,
  one chord — which is most of what the checker would have caught anyway.
- **The `AltGr` guard and the `event.code` rule stay per-handler disciplines.**
  There is no single matcher to enforce them, so each binding carries
  `!event.ctrlKey` (or the explicit `getModifierState('AltGraph')` rejection)
  itself.
- **The drift problem is narrowed, not solved.** One hand-maintained list instead
  of two — see 6d-d, the one place this decision spends more than the bare
  minimum, and it is cheap.

**Today.** Every keybind is a hardcoded condition inside its own `keydown`
handler, and nothing about that changes. There is no way to enumerate the
bindings, which is why the only list of them is the hand-maintained table at
`README.md:198-208` — and it is already incomplete (it omits `Ctrl+Shift+C`
copy, `Ctrl+V` paste, and the `Alt`-click fold gestures). The one configurable
keybind in the app — the voice push-to-talk chord (`app-settings.js:715`,
`voice-input.js:1021`, persisted under `voice_prefs`) — stays exactly where it
is and is edited where it is edited today; the panel *lists* it and does not own
it.

### 6d-a. The button

**Placement: `.topbar-actions`, immediately left of App Settings**
(`terminals.html:161`). That group is where the window-level utility controls
already live — theme · max surface · broadcast · fullscreen · App Settings — and
a shortcut reference is one of those rather than something belonging beside the
session line.

**Icon, not a word** (guardrail 7): a stroke-`currentColor` SVG — a keyboard
outline, or a `?` in a rounded square — with an explicit `svg` box matching its
`.btn-icon` neighbours, `title`/`aria-label` "Keyboard shortcuts",
`aria-haspopup="dialog"` and `aria-expanded`. Every other control in that group
is already an icon button, so a text button would both be the odd one out and
cost the bar width it has none of at `body.surface-max`.

**It lives in the top bar, which can be hidden — and that is fine here.** A
shortcut list is looked up deliberately, not hit in passing, so needing the
chevron or the hover peek first is not the reachability problem item 5 exists to
fix. It also gives the revealed bar one more live reason to exist, which item 5
left thinner than it found it (5e-b).

**Scope: the workspace window only.** The launcher answers to `Alt+W` (return to
the origin workspace) and, after item 3, `Alt+Q` — both of which the panel
*lists* — but a second button on the launcher page is not part of this decision.
If it is wanted later it is the same module and the same panel, mounted beside
the launcher's own settings button.

### 6d-b. "Tooltip" means a panel, not a `title`

A native `title` cannot be styled, cannot be themed, cannot hold a table, and
appears on hover after a delay the browser owns — so the note's "tooltip" is a
**popover panel anchored to the button**, and four things follow:

- **Press, not hover.** As the note says. It is a list to be read, so it must
  stay on screen while it is read.
- **Dismissal:** press the button again, `Escape`, a press outside it, or a
  press of any other top-bar control. It is read-only, so nothing is confirmed
  and nothing is lost. It must also close when the top bar leaves the flow
  (chevron, fullscreen, or the peek retracting) — a panel anchored to a bar that
  is out of the flow is a panel floating over the grid with nothing above it.
- **Not a modal.** No `generic_confirm_modal`, no backdrop, no focus trap: it
  changes nothing, so trapping the keyboard inside it would be worse than the
  thing it documents. A non-modal `role="dialog"` labelled by the button, focus
  moved into the panel on open and returned to the button on close.
- **`Esc` is claimed, and the explorer must know.** Add the panel's root to
  `EXPLORER_ESCAPE_CLAIM_SELECTOR` (`explorer-viewer.js:1559-1560`), exactly the
  way the two menus item 5 removes are listed there, so `Esc` over an explorer
  pane closes this panel instead of dropping the pane's selection.

**Geometry.** `.app-menu-panel`'s pattern (`terminals.css:572-589`), mirrored:
absolutely positioned at `top: calc(100% + 6px)` on a `position: relative`
wrapper around the button, but anchored **`right: 0`** — the button sits at the
right end of the bar, so a `left: 0` panel would run off the window edge. It is
wider than a menu because every row is a chord *and* an action, so cap it
(`max-width: min(440px, calc(100vw - 24px))`) and let its body scroll
(`max-height` + `overflow-y: auto`, with `scrollbar-gutter: stable` per guardrail
7) rather than growing taller than the window. Stacking: above the pane-level
surfaces it overlays (`.pane-shell-menu` and `#terminalResizeOverlay` are both
`60`) and below the modals (`3000`), the peek zone (`3900`) and the revealed bar
(`4000`) — ~`200`, the same figure item 5's menu takes, for the same reason.

**Styling, and the dark/light half.** Take every colour from the tokens the
existing surfaces already use rather than picking literals beside it (guardrail
7): `--t-ctx-bg` for the panel, `--t-border-tab` for its rim, `--t-text` /
`--t-text-secondary` / `--t-text-muted` for the rows and group headings, and
`--t-ctx-hover` if a row ever highlights. Those are declared once per theme in
`terminals.css` (`:root` at `:34`, `[data-theme="light"]` at `:110`), so **the
panel is dark/light-specific by construction** — no `prefers-color-scheme`
block, no second palette, nothing to keep in step by hand. The one genuinely new
element is the chord itself: it renders in a `<kbd>`, which this app has never
styled, so it gets exactly one rule — monospace, a `--t-border-tab` border, a
subtle surface off the same tokens, and no palette literal anywhere in it.

### 6d-c. What the panel lists

The inventory is unchanged from the superseded design — gathering it was the
useful half of that work, and it is now the panel's content rather than a
registry's rows.

| Chord | Action | Where it lives |
| --- | --- | --- |
| `Alt+1`–`Alt+9` | Switch session group | `terminals.js:7200` |
| `Alt+W` / `Alt+Shift+W` | Next / previous workspace | `terminals.js:7264` |
| `Alt+W` (launcher) | Return to origin workspace | `launcher.js:3836` |
| `Alt+Q` → *item 3* | Open the launcher | `terminals.js:7283` |
| `Ctrl+F` | Find in the open file | `terminals.js:7014` |
| `Ctrl+Shift+F` | Repo search (explorer) / scrollback search (terminal) | `terminals.js:7174`, `:4411` |
| `Ctrl+Shift+V` | Toggle Markdown preview | `terminals.js:8231` |
| `F5` | Refresh the focused explorer | `terminals.js:7029` |
| `Ctrl+Shift+E` → *item 3* | Enter / leave explorer edit | (new) |
| `Alt+X` → *item 4* | Minimize all GridVibe windows (native only) | (new) |
| `Ctrl+S` | Save in the explorer editor | `explorer-editor.js:417` |
| `Esc` | Cancel edit / close menu / drop selection / dismiss peek | 6+ handlers |
| `Ctrl+Shift+C` | Copy terminal selection | `terminals.js:4436` |
| `Ctrl+V` | Paste to terminal | `terminals.js:4442` |
| `Tab` | Indent in the editor | `explorer-editor.js:407` |
| `Enter` / `Shift+Enter` / `↑` `↓` | Step find matches | every find bar |
| `Alt`+click | Fold sibling level (tree) / collapse all (Git graph) | `explorer-tree.js:519`, `explorer-git-sidebar.js:1663` |
| PTT chord | Push-to-talk (configured in App Settings) | `voice-input.js:1021` |

**Everything is listed, the conventions included.** The superseded design split
this table into "configurable" and "fixed", which is a distinction a registry
needs and a reader does not: an unlisted `Ctrl+Shift+C` is *precisely* what
someone opens a shortcut list to find. The `Alt`+click entries are pointer
gestures rather than chords, so they sit in their own **Mouse** group at the foot
instead of under a heading that calls them shortcuts.

**Grouped the way it is looked up**, not the way it is implemented:
**Navigation**, **Terminal**, **Explorer**, **Editor**, **Window**, **Mouse**.

**Mode-dependent rows are marked, never hidden.** Item 4's minimize-all chord is
native-mode-only and the workspace chords need more than one workspace open; a
panel that filtered itself per mode would be a second place deciding what mode
you are in, and a row that quietly vanishes reads as a missing feature. A
parenthetical — "(native window only)" — is honest and costs nothing.

**And the chord is named by what your layout prints.** The second cross-cutting
finding lands here: the handler matches `event.code`, this panel prints a
name, and on a QWERTZ layout those are not the same letter. Items 3 and 4 chose
chords whose letters do not move between layouts precisely so this list cannot
lie; a future chord that does move must be labelled from `event.key`, not from
the `code` its handler matches.

### 6d-d. One list, in one place

**Where the code goes** (guardrail 6): `web/static/js/shortcuts-help.js`, a
DOM-free array plus a builder, Node-tested by *driving* it the way
`tests/test_terminal_shell_menu.py` drives the reset dropdown. `terminals.js`
keeps only the open/close wiring and the outside-press listener. It is a small
module, but the grouping and the mode annotations are exactly the kind of thing
worth executing in a test rather than reading.

**And that array is the only list.** `README.md`'s table is the one place a chord
is written down twice, and it is already wrong in three rows. So: fill it from
the array once, and add a test asserting the two chord sets agree. That is the
whole of what the registry was going to do about drift, at the price of one
assertion — and without it this decision replaces one stale list with two.

**Cost:** small, and now the smallest of the four items rather than the largest.
One static JS file with its own Node test, one button and one panel in
`terminals.html`, one CSS block, one entry in `EXPLORER_ESCAPE_CLAIM_SELECTOR`,
the `README.md` table completed and pinned by a test, one `CHANGELOG.md` line. No
backend, no config key, no persistence, no new dependency, and not one keybind
handler touched.

### Superseded options

| Option | Scope | Why not |
| --- | --- | --- |
| **6-A** ⛔ | Read-only "Keyboard" section in App Settings, generated from one registry. | The right content on the wrong surface, with one layer of machinery too many: a registry exists to be *read by the handlers*, and if nothing edits the chords the panel can hold the list itself. App Settings is also where you go to change something. |
| **6-B** ⛔ | Fully configurable: registry + capture UI + conflict detection + reserved warnings + persistence + every handler rewritten. | The largest thing in this document, in exchange for moving ten chords that items 3 and 4 have just chosen deliberately. |
| **6-C** ⛔ | Two passes: registry + list now, configurability later. | Was the recommendation. Pass 1 still rewrites ~12 handlers to read from a registry before anything user-visible improves, and pass 2 was never certain to be built — which leaves the cost paid and the benefit unclaimed. |

---

## Suggested order

1. **Item 5** — immediately felt, and independent of the other three. No
   longer the *smallest*: taking the menus out of the top bar drags
   `topbar-peek.js`'s `menuOpen` retention, its focus target, the explorer's
   Escape claim and one rendered-page test with it (5e-b).
2. **Item 3** — small, and it settles two of the chords item 6's panel has to
   print.
3. **Item 4** — the control and the cascade together; they are one batch now,
   so there is nothing left to decide afterwards except the chord.
4. **Item 6** — last, and now the smallest of the four: with 3 and 4 landed the
   panel is written once, with the final chords and the native-only annotation
   already correct.

Item 6 is coupled to 3 and 4 in one direction only, and it is a weaker coupling
than the registry had: a list written before those chords land is a list edited
again afterwards, but nothing about the panel's shape depends on them.

---

## Decisions

Fill in and this is ready to build.

| Item | Chosen | Notes |
| --- | --- | --- |
| 3a — launcher chord | | keep ``Alt+` `` in parallel? y/n |
| 3b — edit chord | | |
| 4a — hide-all | **4-A** — minimize all, control in both native windows | chord still open: **4-I** (`Alt+X`) recommended; `Ctrl+Shift+H` rejected as two-handed, `Alt+Z` withdrawn on QWERTZ |
| 4b — minimize cascade | **4-D** — opt-in setting, off by default, minimize-only | restore is **not** cascaded |
| 5 — session/workspace menu | **5-E** — one chevron-sectioned menu left of the launcher button in the session tab line; both top-bar menus removed | section rows do nothing; each opens on its own right-hand chevron button, both collapsed on open. No keybind. Status string stays in the top bar; the same message goes to `showTerminalToast()` whenever the bar is out of the flow. |
| 6 — keybind section | **6-D** — one top-bar button opening a read-only shortcut panel; the registry, the configurability, the conflict checking and the reserved-chord warnings are all dropped | Press-to-open popover anchored `right: 0` under the button in `.topbar-actions`, left of App Settings; workspace window only. Styled from the existing `--t-*` tokens, so dark/light comes for free. Lists **every** shortcut incl. the fixed conventions, grouped by where you'd look; mode-dependent rows are marked, never hidden. List lives in one new `shortcuts-help.js` array, with a test pinning the `README.md` table to it. |
