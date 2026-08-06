# Source View Change Overview — implementation plan

Status: **Phases 0–2 implemented** (see §5). **Phases 3–4 are deferred pending
further study** — the shipped ruler column and gutter markers already carry the
feature, so the `map` mode and the extras are on hold, not queued.
**Phase 5 — the clickable gutter change marker and its change peek — is the next
phase to implement; it is planned in §10.** Phase numbers are now identity, not
order: Phase 5 depends only on Phases 0–2 and nothing in 3 or 4.

Target surface: the explorer file viewer's **Source** panel only (Preview and
Diff are untouched).

### Phase 2 — implemented

Landed, delivering the overview column in `ruler` mode — the file is now
navigable by its changes without leaving Source view — verified by the new
`ExplorerOverviewColumnTestCase` in `tests/test_explorer_overview.py`. No
backend surface was added, so there is no new behavioral test: `git/diff`
already carries the whole feature and is covered by the Phase 1 tests in
`tests/test_api.py`.

- Markup per §4.4: `explorerOverviewHtml(index)` (a fifth entry point on
  `explorer-overview.js`, existing only so the frame's render can ask for the
  column's markup) emits the `<aside class="explorer-source-overview"
  data-explorer-overview="N" data-explorer-overview-mode="ruler" hidden>` with
  its `<canvas class="explorer-overview-canvas">` and
  `<div class="explorer-overview-viewport" aria-hidden="true">` into
  `.explorer-source-frame`'s second grid column, beside — never over — the
  scroller. Accessibility per §4.7: `role="scrollbar"`,
  `aria-controls="explorer-code-N"`, `aria-orientation="vertical"`, and
  `aria-valuenow` updated with the scroll ratio on every viewport frame.
- Geometry per §4.5: `explorerOverviewGeometry()` measures **rendered** rows
  (`.explorer-source-line[data-explorer-line]`) into `Float64Array` tops /
  heights plus a `line → row` index, all expressed in the scroll container's
  own coordinate space (`0 … code.scrollHeight`) so the change lane and the
  viewport box are aligned by construction. One uninterrupted read pass (two
  rects, then `offsetTop`/`offsetHeight` per row, no interleaved writes);
  unwrapped rows take the uniform fast path from a single measurement, and
  above `EXPLORER_OVERVIEW_MAX_ROWS = 20000` the same uniform stand-in is the
  deliberate degradation (`approximate: true`). Cached against a signature of
  `rowCount : scrollHeight : clientWidth : wrapOn`, which moves with content,
  font, folds, wrap and resize — so a search keystroke re-renders the rows and
  re-uses the measurement.
- Change lane: `paintExplorerOverview()` DPR-scales the canvas and fills a
  4 px lane from the *same* `_explorerChangeMarks` model the gutter uses —
  added/modified as row-height bands, a deletion as a thin rule on the
  boundary (falling back to the last rendered row above it when the following
  row is folded away, exactly as the gutter wedge does). Colours are read back
  from `--gv-diff-add` / `--gv-diff-modified` / `--gv-diff-delete` via
  `getComputedStyle` on the live element, so the explorer theme and light/dark
  follow automatically and no palette literal enters the JS (guardrail 7); a
  test asserts the file contains no hex literal at all.
- Interaction per §4.7: click or drag anywhere on the track centres that
  content position (pointer-captured, released on `pointerup`,
  `pointercancel` *and* `lostpointercapture`); a wheel over the column is
  forwarded to the scroller through a non-passive listener; a click within the
  change lane jumps to that change and flashes the row through the existing
  `scrollExplorerSourceToLine()`. `Ctrl+E` / `Ctrl+Q` and hover tooltips stay
  in Phase 4.
- Repaint discipline per §4.6: the lane repaints only on a content / marks /
  geometry / size change — `applyExplorerChangeMarks()` now schedules one
  coalesced `requestAnimationFrame` sync after the gutter pass, and a
  `ResizeObserver` on the frame covers pane resize. A **scroll** moves the box
  by CSS transform inside its own separate frame: no repaint, no layout read
  beyond the scroller's metrics, and the scroll listener is passive.
- Standing down: the column leaves the layout (`hidden`) whenever there is
  nothing to survey — an empty file, a panel switched to Preview/Diff (whose
  offsets all read 0, so it is skipped before measuring), or the in-place
  editor's textarea. `renderExplorerEditTextarea()` re-applies the marks for
  exactly that reason; the cached model survives, so leaving the editor costs
  no refetch. `teardownExplorerOverview()` now also disconnects the
  `ResizeObserver` and cancels both queued frames.
- CSS: `.explorer-source-overview` / `[hidden]` / `[data-explorer-overview-mode="ruler"]`
  / `.is-scrubbing`, `.explorer-overview-canvas` and `.explorer-overview-viewport`
  in `terminals.css`, with `--explorer-overview-width: 110px` and
  `--explorer-overview-ruler-width: 14px` declared on `.explorer-source-frame`
  (settled decision 2: fixed, not draggable, so there is no per-pane width to
  persist or restore). Surfaces and hues come from the existing explorer
  variables (`--explorer-bar-bg`, `--explorer-row-border`,
  `--explorer-row-active`).

Not done in Phase 2 (later phases, unchanged from the plan below): `map` mode
and its canvas glyph paint, colour probing and Appearance-menu toggle +
persistence (Phase 3 — the mode attribute is already the CSS hook it will
flip); the search-match lane, `Ctrl+E` / `Ctrl+Q` navigation, hover tooltips
and the truncated-diff indicator (Phase 4). `CHANGELOG.md` / `README.md` /
`CLAUDE.md` updates stay deferred per §8 until the full feature lands.

### Phase 1 — implemented

Landed, delivering the gutter change markers on their own (no overview element
yet), verified by `tests/test_explorer_overview.py` (contract-level) and three
new behavioral `git/diff` tests in `tests/test_api.py`:

- New `web/static/js/explorer-overview.js`, loaded in `templates/terminals.html`
  after `explorer-git-watch.js` and before `terminals.js`, exposing exactly the
  four planned entry points: `loadExplorerChangeMarks(index)`,
  `applyExplorerChangeMarks(index)`, `refreshExplorerOverview(index, reason)`,
  `teardownExplorerOverview(index)`.
- Change model per §4.2: fetches `git/diff?mode=head` (reusing the Diff panel's
  cached HEAD diff on an exact mode-scoped cache-key match), parses with the
  unchanged `explorerDiffChangeBlocks()`, classifies blocks into `added` /
  `modified` line marks and `deleted` wedges (`{ atLine, count }`), and caches
  on the pane as `_explorerChangeMarksKey` (`path \n git-revision`, revision =
  `_explorerGitContext.head`) + `_explorerChangeMarks`. The model records the
  endpoint's `truncated` flag for the Phase 4 indicator. Skipped (no request)
  outside a Git worktree, on commit-diff tabs, in the editor, and when no
  Source panel exists (image viewer).
- Gutter marks: `applyExplorerChangeMarks` runs at the tail of
  `renderExplorerSource()`, so search keystrokes, wrap toggles and Markdown
  folds re-apply the cached model with one `querySelectorAll` pass and no
  fetch. Rows carry `data-explorer-change="added|modified|deleted"`; a
  deletion wedges the row below the boundary, or — `explorer-source-change-after`
  — the last surviving row for an end-of-file deletion. Boundaries hidden by a
  Markdown fold get no wedge until unfolded. A line mark is never overwritten
  by a wedge.
- Refresh triggers per §4.3, all piggybacking on existing signals:
  `renderExplorerFile` kicks off the load (file open, GridVibe Git actions,
  panel-structure-changing saves/undos) after resetting the marks key;
  `updateExplorerFileInPlace` force-loads (quiet `/file/state` refresh,
  in-place save, diff-undo — content moved while HEAD usually did not);
  `explorer-git-watch.js` gained `explorerOverviewWatchConsumer` on the shared
  `/git/state` poll (silent bootstrap, same eligibility gates, covers commits
  made in a terminal) calling `refreshExplorerOverview(index, 'git-revision')`;
  `loadExplorerPane` and the image viewer call `teardownExplorerOverview`.
- CSS + tokens: `data-explorer-change` gutter-bar rules and the deletion-wedge
  triangle live in `terminals.css` next to `.explorer-source-line-number`;
  `--gv-diff-add` / `--gv-diff-modified` / `--gv-diff-delete` are declared in
  `tokens.css` for both `:root` and `[data-theme="light"]` (green/red match the
  existing diff-cell bases; modified is blue, matching the reference IDE).
- New behavioral tests (`tests/test_api.py`): `mode=head` hunk new-side line
  numbers address the worktree file (one added + one deleted block), an
  untracked file returns an empty diff, and a >4 000-line diff reports
  `truncated: true`.

Not done in Phase 1 (later phases, unchanged from the plan below): the
`<aside class="explorer-source-overview">` element and its geometry/canvas/
interaction (Phase 2 ruler, Phase 3 map + Appearance-menu toggle), the
search-match lane, `Ctrl+E` / `Ctrl+Q` navigation, hover tooltips, and the
truncated-diff indicator (Phase 4). `CHANGELOG.md` / `README.md` / `CLAUDE.md`
updates stay deferred per §8 until the full feature lands.

### Phase 0 — implemented

Landed, with no user-visible change, verified by `tests/test_explorer_source_frame.py`:

- `explorerHighlightDocumentLinesCached(pane, content, normalizedLanguage)` in
  `explorer-viewer.js` memoizes the whole-document token map on the pane
  (`pane._explorerHighlightCache`), keyed by content + normalized language;
  `renderExplorerSource()` passes the cached map into
  `renderExplorerSourceLines()`, which only tokenizes inline when no map is
  provided (an explicit `null` — unsupported language, oversized file,
  Highlight.js failure — is a valid cached miss and does not re-tokenize).
  Search keystrokes, wrap toggles and Markdown folds now reuse the map.
- The Source panel markup is wrapped in
  `.explorer-source-frame.explorer-editor-panel`, which carries
  `data-explorer-file-panel="source"`; the inner `.explorer-source-view` keeps
  `id="explorer-code-N"` and remains the scroll container, so all existing
  `explorer-code-N` lookups are unchanged.
- `explorerPanelScrollTarget()` gained a `source` branch returning the inner
  `.explorer-source-view` (or the edit textarea inside it), mirroring the
  existing `diff` branch.
- `terminals.css` gained `.explorer-source-frame { display: grid;
  grid-template-columns: minmax(0, 1fr) auto; overflow: hidden; }`, placed
  after `.explorer-editor-panel` so its `overflow` override wins while
  `.explorer-editor-panel[hidden]` still hides the frame.

Not done in Phase 0 (later phases, unchanged from the plan below): the
`<aside class="explorer-source-overview">` element, gutter markers, the
overview modes, and the Appearance-menu toggle.

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

*(Phase 2 added a fifth, `explorerOverviewHtml(index)`, so the source frame's
render can ask this module for the column's own markup rather than the viewer
spelling it out — the alternative was the overview's markup contract living in
`explorer-viewer.js`, which is the file this split exists to keep out of.)*

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
| Click a **gutter** change marker | Toggle that block's change peek open (Phase 5, §10). Deliberately *not* the same as clicking the overview marker above: the column stays a scrollbar, the gutter is where a change is inspected |

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
| **0** ✅ | Memoize `explorerHighlightDocumentLines()` per pane; add the `.explorer-source-frame` wrapper + `explorerPanelScrollTarget` branch; no visible change | Isolates the one refactor with blast radius into a diff that can be verified by "nothing changed" |
| **1** ✅ | `explorer-overview.js` skeleton: change model, fetch + cache, classification, gutter markers, refresh triggers, tokens + CSS | Delivers the line-number marking from the screenshot on its own |
| **2** ✅ | Overview element in `ruler` mode: geometry helper, change lane, viewport box, click / drag / wheel navigation | Delivers "click through a file for changes" with no canvas-painting risk |
| **3** ⏸ | `map` mode: canvas glyph paint, colour probing, DPR, Appearance-menu toggle + persistence, automatic ruler fallback | The visual payload; safe to land late because ruler mode already works. **Deferred pending further study** |
| **4** ⏸ | Extras: search-match lane, `Ctrl+E` / `Ctrl+Q` navigation, hover tooltips, truncated-diff indicator | Polish. **Deferred pending further study** |
| **5** ▶ | Gutter change peek: the change marker becomes a button, a click opens that block's diff inline under the change (§10) | **Next.** Reuses the Phase 1 model whole and the Phase 0 frame; touches neither the overview column nor anything Phases 3–4 own |

Rough effort: Phase 0 small, 1 medium, 2 medium, 3 medium–large, 4 small,
5 medium.

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

Phase 5 carries its own test plan (§10.7); it adds no backend surface, so it
adds no behavioral test.

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

**With Phase 5**, not "when the full feature lands" — Phases 3–4 are deferred
pending further study, so Phase 5 is the last phase in flight and the shipped
behaviour (gutter markers, the ruler overview, and the change peek) is what the
docs must describe. Leaving them silent until an indefinitely deferred phase is
the stale-contract failure `CLAUDE.md` warns about.

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

## 10. Phase 5 — the gutter change peek (planned, next)

### 10.1 What is being built

The gutter change marking from Phase 1 becomes **clickable**. A click on the
coloured bar (or the deletion wedge) beside a line opens a **change peek**: a
compact diff of *that block alone*, inserted inline into the Source view
directly under the change, showing the HEAD lines the block replaced above the
worktree lines that replaced them. Click again — or the peek's close button —
and it collapses.

Why this and not "switch to the Diff tab": the Diff panel shows the whole file's
patch in a split pane and costs the reader their place in the source. The peek
answers "what did this line used to be?" without leaving the line.

Everything it renders is already in memory: the Phase 1 model is parsed from
`git/diff?mode=head` and each block already carries its worktree lines
(`expected`) and the HEAD lines they replaced (`replacement`). **No fetch, no
backend surface, no new endpoint** — the peek is a second view of a model the
pane already holds.

### 10.2 New constraints (beyond §3)

1. **The line-number cell is already a `<button>` on Markdown headings**
   (`explorerSourceLineNumberHtml`, `explorer-viewer.js:4410`) — it toggles the
   section fold. A marker button *inside* it would be a nested button (invalid
   HTML, and browsers break it), and hijacking the existing button would cost
   the fold. → the marker is a row-level element, never a child of the gutter
   cell (§10.3).
2. **`renderExplorerSource()` rewrites `#explorer-code-N`'s `innerHTML` on every
   search keystroke, wrap toggle and Markdown fold** (constraint 3). An open
   peek is destroyed by each one. → the peek is *state on the pane*, re-inserted
   by the same tail pass that re-applies the gutter marks; it is never the
   source of truth for itself.
3. **An inline peek changes row geometry, and the Phase 2 geometry pass has a
   uniform fast path** that computes `tops[i] = origin + i × uniformHeight`
   (`explorer-overview.js:369`). That assumption — contiguous, equal-height rows
   — is exactly what an inserted element breaks, and it is the path taken
   whenever wrapping is *off*. → the fast path is disabled while a peek is open
   (§10.5).
4. **Unwrapped source scrolls horizontally inside a `min-width: max-content`
   container** (`terminals.css:3565`). A block-level peek in that flow is as wide
   as the file's longest line and slides out of view sideways. → the peek is
   `position: sticky; left: 0` at the scroller's own width (§10.6).
5. **A row can be both marked and a deletion boundary.** Phase 1 already
   resolves this — a line mark is never overwritten by a wedge
   (`explorer-overview.js:224`) — so a row carries at most one marker, and the
   peek inherits that rule rather than inventing a second one.
6. **One tab stop per changed *line* would be unusable** on a file with 200
   changed lines. → focusable marker per *block*, not per row (§10.4).

### 10.3 Where the code goes

All of it in **`web/static/js/explorer-overview.js`** (currently 705 lines),
plus one CSS block in `terminals.css` and two tokens in `tokens.css`.

- The peek is the same domain as the change model — it renders the very blocks
  the model already parses, and it must re-apply on the same signal the gutter
  marks do. Splitting it out would mean exporting the model, the eligibility
  gates and the render hook to a second file for no separation gained.
- `explorer-viewer.js` is **not touched**. The marker buttons are injected by
  the existing gutter pass, not by `renderExplorerSourceLines()`, which keeps
  the largest frontend file out of the diff entirely (guardrail 6).
- **The five entry points in §4.1 are unchanged.** No module outside
  `explorer-overview.js` learns that the peek exists;
  `teardownExplorerOverview()` gains the peek state to clear, that is all.
- Standing note for the next maintainer: if this file passes ~1200 lines, the
  peek (render + interaction, self-contained behind the model) is its natural
  extraction into `explorer-change-peek.js`. It is not one at ~950.

### 10.4 The marker button

Injected by `applyExplorerChangeMarkGutter()` for every row it marks — the pass
already walks exactly those rows, so this is one `appendChild` per marked row
and no second query.

```html
<div class="explorer-source-line" data-explorer-line="42" data-explorer-change="modified">
  <button type="button" class="explorer-change-marker"
          data-explorer-change-marker="3"        <!-- block id -->
          tabindex="-1"                          <!-- 0 on the block's first row -->
          aria-expanded="false"
          aria-controls="explorer-change-peek-2"
          title="Show change: +2 −1"
          aria-label="Show change at line 42: 2 added, 1 removed"></button>
  <span class="explorer-source-line-number">42</span>
  <code class="explorer-source-line-code">…</code>
</div>
```

- **Position, not flow.** `.explorer-source-line[data-explorer-change]` gets
  `position: relative`; the button is `position: absolute; left: 0; top: 0;
  bottom: 0; width: 8px` — over the gutter's left edge, outside the row's two
  grid columns, so neither the fold button nor the grid template is disturbed
  (constraint 1). The 4 px coloured bar becomes the button's own background
  rather than the current `box-shadow: inset 4px` on the number cell; the
  remaining 4 px is invisible hit slop, because a 4 px target is not one.
- **Deletion wedge:** same button, `data-explorer-change-marker-kind="deleted"`,
  sized to the wedge (8 × 10 px) and pinned to the row's top edge — or its
  bottom, under the existing `.explorer-source-change-after` rule, for an
  end-of-file deletion. The wedge triangle moves from the number cell's
  `::before` onto the button's, so both markers are painted by one element.
- **One tab stop per block** (constraint 6): every row of a block gets a button
  (the whole bar is clickable), but only the block's first rendered row carries
  `tabindex="0"`. Both fire the same block.
- **Hover** raises the button's opacity; the bar itself is always at full
  strength, so the marking looks exactly as it does today until the pointer
  arrives.

Clicks are handled by **one delegated listener on `#explorer-code-N`**, wired
once per element behind a `dataset` guard (the same pattern as
`wireExplorerOverview`). The container survives every `innerHTML` rewrite, so
the listener does too and the per-render cost stays at zero.

### 10.5 The model, extended

`explorerChangeMarksModel()` currently discards the blocks after classifying
them. It keeps them instead:

```
{ marks, deletions, truncated,          // unchanged, byte for byte
  blocks:     [{ id, kind, line, oldLine, expected[], replacement[] }],
  blockByLine: Map<renderedLine, blockId> }
```

`kind` is the classification already computed (`added` / `modified` /
`deleted`); `blockByLine` maps every marked line — and every deletion's anchor
line — to its block, so the delegated click resolves a row to a block with one
`Map` lookup and no search. The overview column's lane and marker hit-testing
read `marks` / `deletions` exactly as they do now: **Phase 2 is untouched.**

**Peek identity is `{ line, oldLine }`, not the block id.** Ids are positional
and shift whenever a block is added above; the start-line pair survives an
unrelated edit elsewhere in the file and fails cleanly when the block itself is
gone. After every model load the peek re-resolves against the new blocks and
**closes if nothing matches** — which is the honest outcome when the change the
reader was looking at has just been reverted, saved over, or committed away.

State on the pane: `pane._explorerChangePeek = { line, oldLine }` (or `null`).
Cleared by `teardownExplorerOverview()` alongside the model.

**Geometry (constraint 3):** `explorerOverviewGeometry()`'s `uniform` flag gains
`&& !peekOpen`, so an open peek forces the measured per-row pass. `scrollHeight`
is already in the cache signature, so the insertion invalidates the cache by
itself; only the *path* needed fixing. Above `EXPLORER_OVERVIEW_MAX_ROWS` the
uniform approximation still stands in — that file is already flagged
`approximate: true`, and one peek's worth of drift on a 20 000-row file is
within what that flag already promises.

### 10.6 The peek

```html
<div class="explorer-change-peek" id="explorer-change-peek-2"
     role="region" aria-label="Change at line 42">
  <div class="explorer-change-peek-head">
    <span class="explorer-change-peek-stat">+2 −1</span>
    <span class="explorer-change-peek-range">HEAD 38–38 → 42–43</span>
    <button type="button" class="explorer-change-peek-close" aria-label="Close change peek">…</button>
  </div>
  <div class="explorer-change-peek-body">
    <div class="explorer-change-peek-line old"><span class="explorer-change-peek-number">38</span><code>…</code></div>
    <div class="explorer-change-peek-line new"><span class="explorer-change-peek-number">42</span><code>…</code></div>
  </div>
</div>
```

- **Unified, not side by side.** The Source panel is one narrow column and may
  be half a split pane; two 50 % columns of code there are unreadable. Old lines
  stack above new lines, tinted, which is also how the block reads in the model.
- **Line numbers are real:** `block.oldLine + i` for the HEAD lines,
  `block.line + i` for the worktree lines — so the peek's numbers agree with the
  Diff panel's and with the gutter it hangs off.
- **Syntax highlighting reuses `highlightExplorerCode(text,
  explorerDiffLanguage(index))`** — the same pair the Diff panel's fallback
  renderer already uses (`explorer-viewer.js:3490`), so the peek highlights
  identically and no second lexer path appears. Text is escaped through
  `escHtml` from `shared.js`.
- **Insertion point:** after the block's last rendered row for `added` /
  `modified`; before the anchor row for a `deleted` block (where the removed
  lines used to be), or after it when the anchor is the end-of-file fallback.
  The insertion is therefore always at or below the row the reader clicked, so
  nothing above the click moves and **no scroll compensation is needed**. The
  peek then gets `scrollIntoView({ block: 'nearest' })` in case it opened past
  the bottom edge.
- **Width (constraint 4):** `position: sticky; left: 0` with
  `width: var(--explorer-source-viewport-width)`, a custom property the existing
  coalesced sync writes on the scroller from `code.clientWidth`. One property
  write in a frame that already runs on resize; wrapped and unwrapped mode then
  agree, and the peek never widens the `max-content` container.
- **Cap:** `EXPLORER_CHANGE_PEEK_MAX_LINES = 200`. Beyond it the peek renders
  the first 200 lines and a footer — "… 340 more lines" plus a button that calls
  `setExplorerFileView(index, 'diff')`, handing a genuinely large block to the
  panel built for it.
- **Re-render survival:** `applyExplorerChangeMarks()` ends by re-inserting the
  open peek (constraint 2). If the block's rows are no longer rendered — a
  Markdown fold closed over them — the peek closes rather than floating loose.
  It also closes when the editor opens, on the same eligibility gate the marks
  already use.
- **Repaint:** opening or closing changes `scrollHeight`, so it ends with the
  existing `scheduleExplorerOverviewSync(index)` — the overview column's
  viewport box and lane stay in step through the machinery already there.

**Interaction and accessibility**

| Gesture | Behaviour |
| --- | --- |
| Click the marker | Toggle that block's peek; opening a different block replaces the open one (one peek per pane) |
| `Enter` / `Space` on a focused marker | Same — it is a real `<button>` |
| Close button | Closes and returns focus to the marker that opened it |
| `Escape` **while focus is inside the peek** | Closes it. Scoped to the peek, never a document-level handler, so `Ctrl+F`'s own `Escape` is untouched |
| Click the overview column's marker | **Unchanged** — jump and flash (§4.7). The column stays a scrollbar |

`aria-expanded` on the marker tracks the peek, `aria-controls` points at its id,
and the peek is a labelled `role="region"`, so a screen reader announces the
change rather than an anonymous block of code appearing.

**CSS and tokens (guardrail 7)**

New block in `terminals.css` next to the existing `data-explorer-change` rules.
Add/delete tints come from **two new tokens**, `--gv-diff-add-bg` /
`--gv-diff-delete-bg`, declared in `tokens.css` for `:root` and
`[data-theme="light"]` beside the existing `--gv-diff-*` trio. While that block
is open, the legacy literals it sits next to —
`.explorer-diff-cell.add { background: rgba(34, 197, 94, .18) }` and its
`.delete` twin (`terminals.css:4050`) — migrate to the same two tokens, which is
the "touch a legacy block, migrate its literals" half of guardrail 7 and makes
the peek and the Diff panel share one palette by construction. Surfaces and
borders come from the existing `--explorer-*` variables. No hex or `rgba()`
literal enters the JS; the existing "no hex literal in this file" test keeps it
that way.

### 10.7 Test plan

Backend unchanged → **no new behavioral test**; `git/diff?mode=head` is already
covered by the three Phase 1 tests in `tests/test_api.py`. Everything below is
contract-level on served assets, in `tests/test_explorer_overview.py`
(new `ExplorerChangePeekTestCase`), restricted to the kinds `CLAUDE.md` allows —
class names, `data-*` hooks, `aria-*`, CSS selectors and custom properties, and
named constants:

- `.explorer-change-marker` and `data-explorer-change-marker=` are emitted, and
  `data-explorer-change-marker-kind="deleted"` for the wedge variant
- the marker carries `aria-expanded`, `aria-controls` and `tabindex`
- `.explorer-change-peek`, `-head`, `-body`, `-line`, `-number`, `-close`
  selectors exist in `terminals.css`, with `.old` / `.new` variants
- `.explorer-source-line[data-explorer-change] { position: relative }` and the
  absolutely positioned marker rule exist (the containment contract §10.4
  stands on)
- `--gv-diff-add-bg` / `--gv-diff-delete-bg` are declared for **both** themes,
  and `.explorer-diff-cell.add` / `.delete` no longer carry an `rgba(` literal
- `--explorer-source-viewport-width` is declared and consumed by the peek rule
- named constant `EXPLORER_CHANGE_PEEK_MAX_LINES` is present
- the peek re-render is wired into `applyExplorerChangeMarks` (the pass that
  runs after every Source rebuild), and `teardownExplorerOverview` clears
  `_explorerChangePeek`
- the geometry fast path is gated on the peek: `uniform` is no longer computed
  from `wrapped` and the row cap alone
- the existing "no hex literal in `explorer-overview.js`" and "five entry
  points" assertions still pass unchanged — the second is the guard that this
  phase adds no public surface

**Manual checklist** (added to §6's): wrapped and unwrapped source · a peek open
while typing in `Ctrl+F` (survives the re-render) · a peek open during a pane
resize · a Markdown fold closing over an open peek · a block on a Markdown
heading row (fold button and marker both work) · a deletion-only block · a
deletion at end of file · a >200-line block (cap + "open in Diff") · light and
dark explorer themes · each of the four source fonts · entering the in-place
editor with a peek open · saving in the editor (peek re-resolves or closes) ·
committing from a terminal while a peek is open · an SSH pane · unwrapped source
scrolled fully right (peek stays pinned at the left edge).

### 10.8 Explicitly not in Phase 5

- **No revert/undo button in the peek.** The Diff panel's block undo
  (`undoExplorerDiffChange`, its `explorerCanUndoDiffLine` gate and the
  registered-action bookkeeping) would transplant onto the peek, and the block
  shape is already the right argument — but that is a *mutation* surface with
  its own eligibility rules, and this phase is a viewing affordance. Deferred
  deliberately, with the hook named here so the next phase does not have to
  rediscover it.
- **No change to the overview column's click behaviour** — a marker there still
  jumps and flashes (§4.7).
- **No peek in the Diff panel or the Preview panel** — settled decision 3 holds.
- **Nothing from Phases 3–4**: no `map` mode, no `Ctrl+E` / `Ctrl+Q`, no
  search-match lane, no truncated-diff banner. A truncated diff simply has no
  marker past the cut, and therefore no peek — no extra handling needed.

### 10.9 Risks

| Risk | Mitigation |
| --- | --- |
| Inline insertion breaks the overview's uniform geometry path (silently misplacing every mark below the peek on unwrapped files) | The one real coupling in this phase, called out in §10.2/§10.5: `uniform` is gated on the peek, and the manual pass checks unwrapped source specifically |
| The peek is destroyed by a search keystroke | It is pane state re-applied by the same tail pass as the gutter marks; the manual pass types into `Ctrl+F` with a peek open |
| Nested `<button>` on a Markdown heading row | The marker is a row-level absolute element, never a child of the gutter cell; tested by the containment rule and checked manually on a heading row |
| A tab stop per changed line | One focusable marker per block; the rest are `tabindex="-1"` |
| `explorer-overview.js` grows into the next monolith | ~250 lines onto 705, with the extraction point (`explorer-change-peek.js`) named in §10.3 before it is needed |
| Peek points at a block that no longer exists after a save or commit | Identity is `{ line, oldLine }`, re-resolved on every model load, closing when unmatched — never silently showing a stale diff |
