# Source View Change Overview — implementation plan

Status: **proposal, not yet implemented.** Target surface: the explorer file
viewer's **Source** panel only (Preview and Diff are untouched).

## 1. What is being built

Two coupled affordances, both driven by one change model:

1. **Gutter change markers** — a coloured bar on the line-number column of every
   line that is added / modified relative to `HEAD`, plus a wedge marker where
   lines were deleted.
2. **A right-hand overview bar** ("minimap") — a narrow, full-height column to
   the right of the source text that renders the whole file at a glance, carries
   the same change markers in a dedicated lane, shows the current viewport as a
   box, and is **clickable and draggable** so a click on a marker jumps straight
   to that change. It behaves as a scrollbar for the source panel.

Result: open a changed file in Source view and step through its changes without
leaving Source view or switching to the Diff panel.

## 2. Feasibility summary

**Everything needed already exists; no backend change is required.**

| Need | Already in the repo |
| --- | --- |
| Per-file diff, bounded and read-only | `GET /api/explorer/<id>/git/diff?path=…&mode=head` — `web/api.py:1358`, `web/explorer.py:2384`, capped at 256 KiB / 4 000 lines (`EXPLORER_GIT_DIFF_MAX_BYTES`, `EXPLORER_GIT_DIFF_MAX_LINES`) |
| Unified-diff → change blocks with new-side line numbers | `explorerDiffChangeBlocks()` — `web/static/js/explorer-viewer.js:2933` (built for diff-undo; already emits `{ line, oldLine, expected[], replacement[] }`) |
| Whole-document syntax tokens per line | `explorerHighlightDocumentLines()` — `explorer-viewer.js:686`, returns `Map<lineNumber, [{ className, text, start }]>` |
| Rows addressable by line number | `.explorer-source-line[data-explorer-line="N"]` — `explorer-viewer.js:4433` |
| "Jump here and flash" affordance | `.explorer-source-line-flash` keyframes — `terminals.css:2415` |
| Change detection without new polling | the existing `/git/state` + `/file/state` watchers in `explorer-git-watch.js` |
| Font / theme / wrap plumbing for the panel | `applyExplorerSourceFontToElement()`, `applyExplorerLineWrapState()`, `--source-view-font`, `data-explorer-theme` |

So the work is entirely frontend: one new static JS file, one CSS block, small
edits to the viewer's render path, and the template/test asset lists.

## 3. Constraints discovered during research

These are the things that will break a naive implementation. Each has a design
answer in §4.

1. **Line wrapping is ON by default** (`explorer-viewer.js:3935` — `source: current.source !== false`).
   Row heights are therefore *not* uniform, so `line → y` cannot be
   `lineNumber × lineHeight`. All mapping must go through real row geometry.
2. **`#explorer-code-N` is itself the scroll container**
   (`.explorer-source-view { overflow: auto }`, `terminals.css:3462`). An
   overview bar cannot live inside it — it would scroll away. The panel needs a
   non-scrolling frame around it.
3. **`renderExplorerSource()` rebuilds the whole panel on every search keystroke**
   (`explorer-viewer.js:4562`, called from `applyExplorerSearch`). Markers must
   re-apply cheaply after each rebuild, and the minimap must *not* repaint on
   keystrokes.
4. **Markdown section folding removes rows from the DOM**
   (`renderExplorerSourceLines` skips collapsed rows, `explorer-viewer.js:4415`).
   Geometry is over *rendered* rows, not over all lines; a fold invalidates it.
5. **The in-place editor replaces the panel's contents with a `<textarea>`**
   (`explorer-editor.js:226`). Marks describe the saved file, so both surfaces
   hide while editing and refresh after save.
6. **A commit-history diff shows a past commit while the Source panel shows the
   worktree.** New-side line numbers from `mode=commit` do not map onto the
   displayed text. Markers are HEAD-relative only.
7. **Untracked files have no HEAD blob**, so `git diff HEAD -- path` is empty and
   the file legitimately gets no markers (same as VS Code). A *staged* add
   (`status === 'added'`) does produce an all-added diff and marks fully.
8. **The diff can be truncated** at 4 000 lines. The overview must say so rather
   than silently under-reporting changes.
9. **Guardrail #6 (`CLAUDE.md`)**: new explorer surfaces that are not viewer-core
   get their own file. `explorer-viewer.js` is already the largest frontend file
   (7 796 lines) and is flagged as the regrowth risk. → new file, no exceptions.
10. **Guardrail #7**: no hardcoded palette literals. Diff-marker colours become
    tokens in `tokens.css`; minimap glyph colours are *read back* from computed
    style so they follow the explorer theme automatically.

## 4. Architecture

### 4.1 New file — `web/static/js/explorer-overview.js`

Loaded in `templates/terminals.html` **after** `explorer-viewer.js` and before
`terminals.js`. Static files here are plain concatenation-era globals (no IIFE,
no module system), so the new file can call `explorerDiffChangeBlocks()` and the
viewer can call back into it; declaration order does not matter because every
call happens at runtime.

Responsibilities:

- fetch + cache the HEAD diff for the open file (change model)
- classify blocks into `added` / `modified` / `deleted`
- apply gutter marks to rendered rows
- own the overview element: geometry, canvas paint, viewport box, pointer
  interaction, and next/previous-change navigation

Nothing else may reach into it except through four entry points:
`loadExplorerChangeMarks(index)`, `applyExplorerChangeMarks(index)`,
`refreshExplorerOverview(index, reason)`, `teardownExplorerOverview(index)`.

### 4.2 Change model

```
GET /api/explorer/<id>/git/diff?path=<p>&mode=head
        ↓ explorerDiffChangeBlocks(diff)           (reused, unchanged)
blocks: [{ line, oldLine, expected[], replacement[] }]
        ↓ classify
marks:  Map<newLineNumber, 'added' | 'modified'>   +  deletions: [{ atLine, count }]
```

Classification per block:

| `expected` (+) | `replacement` (−) | mark |
| --- | --- | --- |
| >0 | 0 | `added` on lines `line … line+n-1` |
| >0 | >0 | `modified` on lines `line … line+n-1` |
| 0 | >0 | `deleted` — a wedge anchored *above* line `block.line` |

Cache key: `path \n git-revision`, held on the pane as
`_explorerChangeMarksKey` / `_explorerChangeMarks`. Deliberately **separate**
from `_explorerDiffContent` / `_explorerDiffCacheKey`, which are view-scoped and
mode-specific (`worktree` / `staged` / `commit`) and would otherwise fight over
the same slot. If `loadExplorerDiff` has already cached a `head`-mode diff for
the same path, reuse it instead of re-fetching.

Skipped entirely (no request) when: not in a Git worktree
(`!_explorerGitContext?.available`), `_explorerDiffCommit` is set (commit-diff
tab, constraint 6), the file is not a text/source view, or the editor is active.

### 4.3 Refresh triggers — no new polling

Guardrail #3 forbids new sub-second or duplicate polls. The marks refresh
piggybacks on signals that already exist:

| Signal | Hook |
| --- | --- |
| File opened | `openExplorerFile` tail, after `renderExplorerSource(index)` |
| File changed on disk | existing `/file/state` watcher → `refreshExplorerOpenFileQuiet` (`explorer-git-watch.js:439`) |
| Repo revision changed (commit/stage from a terminal) | existing `/git/state` watcher — add an *overview* consumer alongside `explorerGitWatchSidebarConsumer` / `explorerFsWatchConsumer` (`explorer-git-watch.js:137,154`) |
| GridVibe Git action (stage / unstage / discard / commit) | the existing post-action refresh path |
| In-place save | editor save success handler |
| Diff-undo | `undoExplorerDiffChange` completion (`explorer-viewer.js:3321`) |

The overview consumer must obey the same eligibility gates (in-flight, suspended,
`_explorerGitActionBusy`, `_explorerFsBusy`, `_explorerEdit`).

### 4.4 Markup change — a frame around the source panel

Current (`explorer-viewer.js:7311`):

```html
<div class="explorer-editor-main">
  <div class="explorer-source-view explorer-editor-panel" id="explorer-code-N"
       data-explorer-file-panel="source"></div>
  …preview…
</div>
```

Proposed:

```html
<div class="explorer-editor-main">
  <div class="explorer-source-frame explorer-editor-panel"
       data-explorer-file-panel="source">
    <div class="explorer-source-view" id="explorer-code-N"></div>
    <aside class="explorer-source-overview" data-explorer-overview="N" hidden>
      <canvas class="explorer-overview-canvas"></canvas>
      <div class="explorer-overview-viewport" aria-hidden="true"></div>
    </aside>
  </div>
  …preview…
</div>
```

`.explorer-source-frame { display: grid; grid-template-columns: minmax(0,1fr) auto;
overflow: hidden; }`; the inner view keeps `overflow: auto`. The overview takes
real layout width (it is not an overlay), so it never hides code and never
fights the horizontal scrollbar.

**This is the one refactor with blast radius, and it is deliberately the same
pattern the Diff panel already uses.** `data-explorer-file-panel="source"` moves
to the frame, so `explorerPanelScrollTarget()` (`explorer-viewer.js:5440`) gains
a `source` branch returning `.explorer-source-view` — exactly as it already does
for `diff` (`panel.querySelector('.explorer-diff-content')`). Full audit list:

- `explorer-viewer.js` — 15 references to `explorer-code-${index}` (all resolve
  the inner scroller; unchanged)
- `explorer-viewer.js:5447` `explorerPanelScrollTarget` — add the `source` branch
- `explorer-viewer.js:3735` `setExplorerFileView` panel show/hide — now toggles
  the frame (correct: hides the overview with it)
- `explorer-viewer.js:4107` / `3984` — font + wrap classes stay on the inner
  `.explorer-source-view` (they key off that class, not the panel attribute)
- `explorer-editor.js:188,218,303` — 3 references, all to the inner scroller
- `terminals.css` — `.explorer-editor-panel { overflow: auto }` no longer applies
  to the source scroller; the frame overrides it to `hidden`
- `tests/test_api.py:2203,11679` — `[data-explorer-file-panel]` assertions still
  hold

### 4.5 Geometry — the hard part

A `geometry(index)` helper returns `{ contentHeight, top(line), height(line) }`
for **rendered** rows.

- **Fast path** (line wrap off, or wrap on with no row taller than one line):
  read one row's height once → pure arithmetic, zero per-row DOM reads.
- **Wrapped path**: one uninterrupted batched pass reading `offsetTop` /
  `offsetHeight` of every `.explorer-source-line` into a `Float64Array` (a single
  layout flush, no interleaved writes — a few ms even at 20 000 rows).
- Cap `EXPLORER_OVERVIEW_MAX_ROWS = 20000`; above it fall back to a uniform
  approximation and mark the overview approximate.

Invalidated (and rebuilt on the next `requestAnimationFrame`) by: panel resize
(`ResizeObserver` on the frame — the pattern already used at
`explorer-viewer.js:2837`), font-size / font-family change, wrap toggle, a
Markdown fold toggle, and any content re-render.

Everything paints in **content-proportional space** — `y = top(line) /
contentHeight × canvasHeight` — so glyph rows, change markers and the viewport
box stay aligned with each other whether or not lines wrap.

### 4.6 Overview rendering

Three modes, selectable from the existing Appearance menu (the
`gridvibe.sourceViewFont` localStorage pattern at `explorer-viewer.js:3900`
generalises to `gridvibe.sourceOverview` = `map` | `ruler` | `off`):

- **`map`** — the default. Canvas minimap. Per rendered row, draw one band of
  ~1 px-per-character blocks from the cached
  `explorerHighlightDocumentLines()` runs, coloured per token class. Colours are
  resolved once per paint by probing `getComputedStyle` on throwaway
  `.hljs-keyword` / `.hljs-string` / … spans inside the panel, so the explorer
  theme and light/dark both follow automatically and **no palette literal enters
  the JS** (guardrail #7). Canvas is DPR-scaled; width is the fixed token
  `--explorer-overview-width` (110 px) — **not** user-draggable, so there is no
  new per-pane width to persist, capture in the scroll state, or restore.
- **`ruler`** — no glyphs, just the marker lanes and the viewport box. Automatic
  fallback when the file is above `EXPLORER_PLAIN_PREVIEW_THRESHOLD`
  (`_explorerFilePlain`), when the tokenizer returns `null`, or above the row cap.
- **`off`** — no overview; gutter markers stay.

Lanes, left to right: change lane (4 px) · glyph area · search-match lane (2 px,
Phase 4).

Repaint discipline: full repaint only on content / marks / geometry / theme
change. Scroll events move the viewport box via a CSS transform inside one
`requestAnimationFrame` — no repaint, no per-scroll layout read.

**Note on the current double tokenization:** `renderExplorerSourceLines` calls
`explorerHighlightDocumentLines()` on every render, including every search
keystroke. The minimap needs the same map, so memoize it on the pane by
`content + language` and have both readers share it. This is a net win for
search-heavy use even before the minimap exists, and is worth landing first.

### 4.7 Interaction

| Gesture | Behaviour |
| --- | --- |
| Click on the overview | Centre that content position in the source view |
| Drag (pointer capture) | Continuous scrub; `pointercancel` / `lostpointercapture` release |
| Wheel over the overview | Forwarded to the source scroller |
| Click a change marker | Scroll the change into view and flash the row via `.explorer-source-line-flash` |
| `Ctrl+E` / `Ctrl+Q` | Next / previous change; wraps at the ends |
| Hover a change marker | Tooltip `+n −m` for that block |

`Alt`+letter was rejected: `Alt` already drives workspace and session-group
switching (`Alt+W`, `Alt+1`…`Alt+9`), so a mistyped `Alt+E` sits one key away
from throwing the user into another workspace. `Ctrl` carries no navigation role
in GridVibe.

Accessibility: the `<aside>` gets `role="scrollbar"`, `aria-controls` on the
source view, `aria-orientation="vertical"`, and `aria-valuenow` updated with the
scroll ratio. The viewport box is `aria-hidden` (decorative). Keyboard users are
served by the `Ctrl+E` / `Ctrl+Q` pair, not by focusing the canvas. Both buttons
in the header control (Phase 4) carry
`aria-keyshortcuts="Control+E"` / `aria-keyshortcuts="Control+Q"` and a matching
`title`, following the Preview tab's existing precedent
(`explorer-viewer.js:7280`).

#### Ctrl+E / Ctrl+Q collision audit

**In-app: clear.** Every `ctrlKey` and `altKey` handler in the codebase was
checked and neither chord is taken:

| Existing binding | Where | Conflict |
| --- | --- | --- |
| `Ctrl+F` — find in file | `terminals.js:6243` | No |
| `Ctrl+Shift+F` — repo search overlay | `terminals.js:6403` | No |
| `Ctrl+Shift+V` — Markdown preview | `terminals.js:7398` | No |
| `Ctrl+S` — save in the in-place editor | `explorer-editor.js:278` | No |
| `Ctrl+Shift+C` / `Ctrl+V` — xterm copy/paste | `terminals.js:3921,3927` | No |
| `Ctrl`+click — open search hit in a pinned tab | `explorer-search.js:312` | No — mouse |
| `Alt+1`…`Alt+9`, `Alt+W` — group / workspace switch | `terminals.js:6429,6492` | No — this is what the change of binding avoids |
| Push-to-talk keybind | `voice-input.js:999` | **Soft** — defaults to empty (`web/voice.py:563`) but is user-configurable, so a user could bind `Ctrl+Q` |

Two guards follow from that audit:

1. **Pane scope.** The handler only fires when the focused element is inside an
   explorer *source* panel that has an open file with marks — so it cannot reach
   a terminal pane. That matters more for `Ctrl` than it did for `Alt`: in a
   shell, `Ctrl+Q` is XON (resume output after `Ctrl+S`) and `Ctrl+E` is
   end-of-line in emacs-mode readline. xterm's custom key handler
   (`terminals.js:3905`) only diverts `Ctrl+Shift+F` and `Alt+W` to the document,
   so both keys keep reaching the shell untouched while a terminal has focus —
   and the shortcut must never claim them there.
2. **Push-to-talk yields first.** If the configured `pttKeybind` is the same
   chord, the explorer handler returns without acting; the user's own binding
   wins. `isEditableShortcutTarget` (`terminals.js:6420`) is reused so the chord
   is also inert inside inputs, textareas and the keybind recorder.

**Browser-level: one genuine hazard, and it is worse than the `Alt` case.**
In native-window mode (WebView2) there is no browser UI and both chords are free.
In *browser* mode:

- `Ctrl+E` focuses the address/search bar in Chrome, Edge and Firefox. It is not
  on Chrome's reserved list, so `preventDefault()` on the keydown suppresses it.
  Low risk.
- **`Ctrl+Q` quits Firefox** (and Chrome on Linux) — a whole-application quit,
  not a tab action. Whether a page can suppress it is not reliable across
  versions; Firefox gates it behind the `browser.quitShortcut.disabled` pref
  precisely because pages cannot be trusted to. Losing every live terminal
  session to a mistyped shortcut is the exact failure the in-page close-confirm
  guardrail exists to prevent.

Mitigation, in order of preference:

1. Ship `Ctrl+E` / `Ctrl+Q` as decided, and **verify `Ctrl+Q` under Firefox and
   Chrome-on-Linux during Phase 4's manual pass**. If `preventDefault()` holds,
   nothing more is needed — Chrome/Edge on Windows and WebView2 (the primary
   surfaces) are unaffected either way.
2. If it does not hold, keep `Ctrl+E` for next and move *previous* to
   `Ctrl+Shift+E`. This keeps one memorable key, costs nothing on the common
   surface, and never risks a browser quit.

This is a decision to make with test evidence in Phase 4, not up front; nothing
in Phases 0–3 depends on it.

## 5. Phases

Each phase is independently shippable and independently reviewable.

| # | Scope | Why this order |
| --- | --- | --- |
| **0** | Memoize `explorerHighlightDocumentLines()` per pane; add the `.explorer-source-frame` wrapper + `explorerPanelScrollTarget` branch; no visible change | Isolates the one refactor with blast radius into a diff that can be verified by "nothing changed" |
| **1** | `explorer-overview.js` skeleton: change model, fetch + cache, classification, gutter markers, refresh triggers, tokens + CSS | Delivers the line-number marking from the screenshot on its own |
| **2** | Overview element in `ruler` mode: geometry helper, change lane, viewport box, click / drag / wheel navigation | Delivers "click through a file for changes" with no canvas-painting risk |
| **3** | `map` mode: canvas glyph paint, colour probing, DPR, Appearance-menu toggle + persistence, automatic ruler fallback | The visual payload; safe to land late because ruler mode already works |
| **4** | Extras: search-match lane, `Ctrl+E` / `Ctrl+Q` navigation, hover tooltips, truncated-diff indicator | Polish |

Rough effort: Phase 0 small, 1 medium, 2 medium, 3 medium–large, 4 small.

## 6. Test plan

Backend is unchanged, so tests are behavioral where behaviour exists and
contract-level on served assets, per the `CLAUDE.md` testing rule.

**Behavioral (real):**
- `GET /api/explorer/<id>/git/diff?mode=head` against a temp repo built with the
  existing `_run_git` helper (`tests/test_api.py:230`): a file with one added
  block and one deleted block returns hunk headers whose new-side line numbers
  match the worktree file. This is the contract the whole feature stands on.
- The same endpoint for an untracked file returns an empty diff (documents
  constraint 7 rather than leaving it as folklore).
- Truncation: a file with >4 000 changed lines reports `truncated: true`.

**Contract-level on served assets (allowed kinds only — class names, `data-*`
hooks, `aria-*`, CSS custom properties and selectors, named constants):**
- `templates/terminals.html` serves `js/explorer-overview.js`, and the file is
  added to `_page_html`'s asset list (`tests/test_api.py:211`)
- `.explorer-source-frame`, `.explorer-source-overview`,
  `.explorer-overview-canvas`, `.explorer-overview-viewport` selectors exist in
  `terminals.css`
- `data-explorer-overview=` and `data-explorer-change=` hooks are emitted
- `.explorer-source-line[data-explorer-change="added"]` (and `modified`,
  `deleted`) rules exist
- `--gv-diff-add` / `--gv-diff-modified` / `--gv-diff-delete` are declared in
  `tokens.css` for both `:root` and `[data-theme="light"]`
- `--explorer-overview-width` custom property is declared
- `role="scrollbar"` and `aria-orientation="vertical"` are present on the aside
- named constants `EXPLORER_OVERVIEW_MAX_ROWS` and the
  `gridvibe.sourceOverview` storage key are present
- `aria-keyshortcuts="Control+E"` / `aria-keyshortcuts="Control+Q"` are declared
  on the next/previous-change controls

**Manual checklist** (no JS test runner exists):
wrapped and unwrapped source · a Markdown file with folded sections · a >2 MiB
file (ruler fallback) · light and dark explorer themes · each of the four source
fonts · editor mode enter/exit · staged vs unstaged vs partially staged file ·
untracked file · commit-diff tab (no markers) · SSH pane · pane resize · a file
whose diff is truncated · `Ctrl+E` / `Ctrl+Q` in **both** browser mode and
native-window mode · **`Ctrl+Q` under Firefox specifically — confirm the browser
does not quit** (§4.7 mitigation 2 if it does) · `Ctrl+Q` typed into a focused
terminal pane still reaches the shell as XON · a push-to-talk keybind set to
`Ctrl+Q` still wins.

## 7. Risks and the alternatives that were rejected

| Risk | Mitigation |
| --- | --- |
| Panel-wrapper refactor breaks scroll capture/restore or edit mode | Phase 0 lands it alone; `explorerPanelScrollTarget` already has the identical `diff` precedent |
| Geometry pass costs a long layout on huge files | Uniform fast path when unwrapped; batched single-flush read otherwise; hard row cap with degraded fallback |
| Minimap repaint on every search keystroke | Repaint is gated on content/marks/geometry identity; search only repaints its own 2 px lane |
| A second consumer of `explorerDiffChangeBlocks()` deepens coupling to `explorer-viewer.js` | Accepted, and noted: it strengthens the standing `explorer-diff.js` extraction guardrail. Extracting the diff domain **first** is the cleaner order if that refactor is already planned; this feature does not require it |
| Marks drift from the file after an external edit | The existing `/file/state` watcher already refreshes the open file; marks refresh on the same signal |
| `Ctrl+Q` quits the browser in Firefox / Chrome-on-Linux, killing every live session | Unaffected on WebView2 and Chrome/Edge-on-Windows; verified in Phase 4, with `Ctrl+Shift+E` as the drop-in replacement for *previous* if `preventDefault()` does not hold (§4.7) |

**Rejected alternatives:**
- *Scaled DOM clone* (`transform: scale(0.1)` on a copy of the rows) — doubles
  the DOM for every open file; unusable at explorer file sizes.
- *Absolute overlay minimap over the source text* — needs compensating padding,
  overlaps the native scrollbar and wrapped text, and has to be re-hidden per
  panel switch. The grid frame is less code and more robust.
- *A new backend endpoint returning pre-computed marks* — the bounded diff
  endpoint already exists and the parser already exists; a new route would add
  server surface for zero gain and would need its own read-only-contract note.
- *A dedicated poll for marks* — forbidden by guardrail #3; the two existing
  watchers already carry the signal.

## 8. Documentation to update when this lands

- `CHANGELOG.md` — user-visible feature entry
- `README.md` — the explorer/source-view feature list
- `CLAUDE.md` — add `explorer-overview.js` to the repo-layout tree and to
  guardrail #6's list of split-out frontend domain files
- The read-only explorer contract in `CLAUDE.md` needs **no change**: the feature
  adds no mutation, and `git/diff` is already an established read.

## 9. Settled decisions

Resolved by the maintainer; treat these as fixed for the implementation.

1. **Default mode: `map`.** The minimap is on by default, matching the reference
   screenshot. The automatic `ruler` fallback (§4.6) carries the large-file and
   unsupported-language cases, and `ruler` / `off` stay available from the
   Appearance menu.
2. **Overview width: fixed.** `--explorer-overview-width: 110px`, not draggable.
   No new per-pane width state to persist or restore.
3. **Diff panel: no overview.** Source view only, as specified. The geometry
   helper in §4.5 would transfer if that ever changes, so keep it free of
   source-panel-specific assumptions — but build nothing for the Diff panel now.
4. **Change navigation: `Ctrl+E` (next) / `Ctrl+Q` (previous).** `Alt`+letter was
   rejected because `Alt` already drives workspace and session-group switching,
   making a misfire costly. Audited clear of every in-app binding; see §4.7 for
   the two guards (pane scope, push-to-talk yields) and for the one real
   browser-level hazard — **`Ctrl+Q` quits Firefox** — which Phase 4 must verify
   and, if `preventDefault()` does not hold, resolve by moving *previous* to
   `Ctrl+Shift+E`.
