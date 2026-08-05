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

- **`map`** (default) — canvas minimap. Per rendered row, draw one band of
  ~1 px-per-character blocks from the cached
  `explorerHighlightDocumentLines()` runs, coloured per token class. Colours are
  resolved once per paint by probing `getComputedStyle` on throwaway
  `.hljs-keyword` / `.hljs-string` / … spans inside the panel, so the explorer
  theme and light/dark both follow automatically and **no palette literal enters
  the JS** (guardrail #7). Canvas is DPR-scaled; width `--explorer-overview-width`
  (default 110 px).
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
| `Alt+F5` / `Shift+Alt+F5` | Next / previous change (VS Code parity); wraps at the ends |
| Hover a change marker | Tooltip `+n −m` for that block |

Accessibility: the `<aside>` gets `role="scrollbar"`, `aria-controls` on the
source view, `aria-orientation="vertical"`, and `aria-valuenow` updated with the
scroll ratio. The viewport box is `aria-hidden` (decorative). Keyboard users are
served by the `Alt+F5` pair, not by focusing the canvas.

## 5. Phases

Each phase is independently shippable and independently reviewable.

| # | Scope | Why this order |
| --- | --- | --- |
| **0** | Memoize `explorerHighlightDocumentLines()` per pane; add the `.explorer-source-frame` wrapper + `explorerPanelScrollTarget` branch; no visible change | Isolates the one refactor with blast radius into a diff that can be verified by "nothing changed" |
| **1** | `explorer-overview.js` skeleton: change model, fetch + cache, classification, gutter markers, refresh triggers, tokens + CSS | Delivers the line-number marking from the screenshot on its own |
| **2** | Overview element in `ruler` mode: geometry helper, change lane, viewport box, click / drag / wheel navigation | Delivers "click through a file for changes" with no canvas-painting risk |
| **3** | `map` mode: canvas glyph paint, colour probing, DPR, Appearance-menu toggle + persistence, automatic ruler fallback | The visual payload; safe to land late because ruler mode already works |
| **4** | Extras: search-match lane, `Alt+F5` navigation, hover tooltips, truncated-diff indicator | Polish |

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

**Manual checklist** (no JS test runner exists):
wrapped and unwrapped source · a Markdown file with folded sections · a >2 MiB
file (ruler fallback) · light and dark explorer themes · each of the four source
fonts · editor mode enter/exit · staged vs unstaged vs partially staged file ·
untracked file · commit-diff tab (no markers) · SSH pane · pane resize · a file
whose diff is truncated.

## 7. Risks and the alternatives that were rejected

| Risk | Mitigation |
| --- | --- |
| Panel-wrapper refactor breaks scroll capture/restore or edit mode | Phase 0 lands it alone; `explorerPanelScrollTarget` already has the identical `diff` precedent |
| Geometry pass costs a long layout on huge files | Uniform fast path when unwrapped; batched single-flush read otherwise; hard row cap with degraded fallback |
| Minimap repaint on every search keystroke | Repaint is gated on content/marks/geometry identity; search only repaints its own 2 px lane |
| A second consumer of `explorerDiffChangeBlocks()` deepens coupling to `explorer-viewer.js` | Accepted, and noted: it strengthens the standing `explorer-diff.js` extraction guardrail. Extracting the diff domain **first** is the cleaner order if that refactor is already planned; this feature does not require it |
| Marks drift from the file after an external edit | The existing `/file/state` watcher already refreshes the open file; marks refresh on the same signal |

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

## 9. Open questions for the maintainer

1. **Default mode** — ship with `map` on by default (matches the screenshot), or
   `ruler` on by default with `map` opt-in? Recommendation: `map`, with the
   automatic ruler fallback carrying the large-file case.
2. **Overview width** — fixed 110 px, or draggable? Recommendation: fixed token,
   revisit if it feels cramped.
3. **Should the overview also appear in the Diff panel?** Out of scope as
   requested ("source view only"), and the diff already has its own scroll
   machinery — but the geometry helper would transfer.
4. **`Alt+F5` binding** — VS Code parity, but confirm it does not collide with a
   terminal-pane binding in `terminals.js`.
