# Highlighted Edit Mode for the Explorer Text Editor

Status: Stages 0 and 1 shipped 2026-08-13; Stages 2 and 3 not started. Drafted
2026-08-13.

Everything below describes the plan as drafted. Where the shipped code departs
from it, the **Shipped** notes in the Staging section say so — read those
before treating a paragraph here as a description of the code.

## Goal

Remove the harsh visual transition when a file explorer pane enters in-place edit
mode. Today the rendered, syntax-highlighted Source view is replaced wholesale by
an unstyled `<textarea>`: colours, the line-number gutter, and the surrounding
geometry all vanish in one frame.

The approach: paint the **existing** read-only row renderer *behind* a
transparent textarea. The textarea keeps doing all the real editing, so the edit
state machine, the save/conflict flow, and every unsaved-work guard are untouched.

Constraints this proposal holds itself to:

- No new vendored dependency, no bundle step, no second highlighting engine.
- No backend change, no new endpoint, no widening of the read-only explorer
  contract.
- No net growth of `explorer-viewer.js` or `terminals.js`.
- Every mid-edit teardown path behaves exactly as it does today.

## Current Behaviour

Entering edit mode is a single DOM swap. `renderExplorerEditTextarea()`
(`web/static/js/explorer-editor.js`) replaces the entire contents of
`#explorer-code-<index>` with one bare textarea:

```js
code.innerHTML = `<textarea id="explorer-edit-textarea-${index}" class="explorer-source-editor" spellcheck="false" wrap="${wrap}" …></textarea>`;
```

What it replaces is `renderExplorerSourceLines()` in
`web/static/js/explorer-viewer.js`: one `.explorer-source-line` grid row per
line, each holding a number cell and a `<code class="explorer-source-line-code">`
carrying Highlight.js spans.

### What is lost in that frame

| Lost | Owner |
| --- | --- |
| Syntax colours | `explorerHighlightDocumentLines()` (viewer) |
| Line-number gutter | `explorerSourceLineNumberHtml()` (viewer) |
| HEAD change bars + overview ruler column | `applyExplorerChangeMarks()` (`explorer-overview.js`), stood down deliberately by the editor |
| Markdown fold chevrons | the same row builder |
| Search marks / occurrence tint | the search chrome is disabled while editing anyway |

### The geometry jolt

Less obvious than the colour loss, and arguably a bigger part of what reads as
"harsh": the text physically moves. In `web/static/css/terminals.css`:

- read-only rows are a grid, `grid-template-columns: minmax(42px, auto) minmax(0, 1fr)`,
  with `.explorer-source-line-code { padding: 0 14px; }` and no padding on
  `.explorer-source-view` or `.explorer-source-lines`;
- the editor is `.explorer-source-editor { padding: 8px 12px; }` with no gutter.

So on entering edit mode every glyph jumps left by roughly (gutter width + 2px)
and down by 8px.

## Architecture

### Layer stack

Inside `#explorer-code-<index>` — which *is* the `.explorer-source-view` element,
per the card template in `explorer-viewer.js`:

```
.explorer-source-view#explorer-code-N     the scroller, unchanged
└── .explorer-edit-stack                  position: relative
    ├── .explorer-source-lines            the underlay — rendered rows, aria-hidden
    └── textarea.explorer-source-editor   position: absolute; inset: 0
                                          color: transparent; caret-color: <text>
                                          background: transparent; overflow: hidden
```

### Which element scrolls

The decision that removes a whole class of bugs, and the one that keeps the
teardown paths identical.

**Do not** make the textarea the scroller and mirror its offsets onto the
underlay. Scroll mirroring is a per-frame sync that visibly lags on fast wheel
input and drifts at the extremes.

Instead `.explorer-source-view` scrolls *both layers together*, exactly as it
already does in read-only mode:

- the stack's height is the underlay's natural height;
- the textarea fills the stack absolutely with `overflow: hidden`, so it has no
  internal scroll of its own;
- because the textarea is exactly as tall as its content, the browser scrolls the
  nearest scrollable ancestor to reveal the caret — the source view. Caret
  follows typing with no code.

This is the arrangement used by CodeJar and react-simple-code-editor, and it
needs **zero scroll-sync code**.

It also collapses existing machinery rather than adding to it. The scrolling
element becomes the same one in both modes, so:

- `explorerPanelScrollTarget()` (`explorer-viewer.js`) loses its edit-mode
  branch — a **deletion**, and the one change this proposal makes to that file;
- the enter/exit `captureScrollMetrics` / `restoreExplorerEditViewport` dance in
  `enterExplorerEditMode` / `exitExplorerEditMode` becomes near-redundant: the
  scroller is never replaced and the content height no longer changes, so
  scroll position simply survives. It should be kept as a cheap safety net for
  the degraded path (below), not removed.

### What the underlay renders

`renderExplorerSourceLines(draft, language, [], new Set(), runs)` — the same call
the viewer makes, with two deliberate differences:

- **empty search ranges.** Search chrome is already disabled during editing.
- **empty collapsed set.** Markdown fold chevrons are `<button>`s that would sit
  under a covering textarea: unclickable, and a keyboard tab trap. They must not
  be rendered. Folding a buffer being typed into is incoherent regardless.

The underlay is `aria-hidden="true"`; the textarea remains the sole accessible
surface and keeps its existing `aria-label`.

### Free wins from existing contracts

Three things already point this way and need no work:

- Zoom is a CSS custom property on the pane (`--explorer-editor-font-size`, set
  by `applyExplorerEditorFontSize()`) and the source font is
  `--source-view-font` on `.explorer-source-view`. Both layers inherit both
  variables, so the appearance menu and zoom stay in step with no re-sync code.
- Line wrap is the `.wrap-lines` class on that same ancestor, which already
  drives both `.explorer-source-line-code` and `.explorer-source-editor`. If the
  textarea's wrapping is left to CSS rather than baked into its `wrap` attribute
  at render time, toggling wrap mid-edit restyles both layers in one paint.
- Dictation lands through the textarea's own `input` event in both its
  `execCommand` and `setRangeText` paths, so the underlay refresh hooks the
  existing listener and covers both with no change to
  `deliverExplorerDictation()`.

## Isolation and Blast Radius

### New files

| File | Contents |
| --- | --- |
| `web/static/js/explorer-edit-overlay.js` | The whole feature: builds the stack, refreshes the underlay, owns the degraded fallback. |
| `web/static/js/explorer-edit-highlight.js` | DOM-free policy: viability + re-highlight scheduling. Node-testable. |
| `tests/test_explorer_edit_highlight.py` | Node-executed behavioural tests for the above, plus the adapter run against the real row renderer. |

Splitting policy from adapter follows the pattern already used by
`topbar-peek.js`, `explorer-selection.js`, and `notice-banner.js`, and keeps the
only genuinely new logic testable without a DOM.

### Changes to existing files

| File | Change | Direction |
| --- | --- | --- |
| `explorer-editor.js` | 3 call sites: build the stack instead of a bare textarea; refresh the underlay from the existing `input` handler; tear the overlay down in `clearExplorerEditState`. | ~+10 lines |
| `explorer-viewer.js` | Delete the `explorerPanelScrollTarget()` edit-mode branch. | **net negative** |
| `terminals.css` | The stack, transparent textarea, metric parity, `::selection`, uniform gutter width. | additive |
| `templates/terminals.html` | Two `<script>` tags, ordered before `explorer-editor.js`. | 2 lines |

`terminals.js` is not touched at all. `explorer-viewer.js` shrinks. The new
surface lives entirely in its own domain files, per the architecture guardrail.

### Provably untouched

No backend file changes. No new or altered endpoint. `state.draft` is still
`textarea.value`, so the `PUT` payload, the `base_revision` conflict check, the
save claim, and the 10 MiB bound are byte-identical. The read-only explorer
contract is not widened. No new persisted state: editing remains transient
in-memory (`pane._explorerEdit`), off the tab record, out of saved sessions and
`runtime_state.json`.

## Mid-Edit Teardown: Every Path Stays Identical

The overlay is DOM *inside* `#explorer-code-<index>`, owned by the editor for
exactly as long as `pane._explorerEdit` is set. Every existing exit already goes
through one of two choke points — `clearExplorerEditState()` (drop state) or
`exitExplorerEditMode()` (drop state, then `renderExplorerSource()`) — and both
already rebuild or discard that subtree wholesale. Tearing the overlay down means
one added line in `clearExplorerEditState()`, the choke point every route already
funnels through.

| Path | Call site | Behaviour under this proposal |
| --- | --- | --- |
| Cancel / `Escape` | `cancelExplorerEdit` | Unchanged. `exitExplorerEditMode` → `renderExplorerSource` replaces the whole subtree, overlay included. |
| Save success | `onExplorerSaveSuccess` | Unchanged. `clearExplorerEditState` then `updateExplorerFileInPlace` / `renderExplorerFile`. |
| Save conflict / error | `onExplorerSaveError` | Unchanged. Editor stays open, overlay stays; only the inline bar is added, and it is a sibling above `.explorer-editor-body`. |
| Switching tabs | `explorer-viewer.js:7261` | Unchanged; `confirmDiscardExplorerEdit` first. |
| Closing a tab | `explorer-viewer.js:7285` | Unchanged. |
| Opening another file | `explorer-viewer.js:8431` | Unchanged. |
| Refreshing the file | `explorer-viewer.js:8482` | Unchanged. |
| Leaving the file | `explorer-viewer.js:8535` | Unchanged. |
| Filesystem mutation | `explorer-fs.js:1159` | Unchanged. |
| Closing the pane | `terminals.js:6441` | Unchanged. |
| Switching sessions | `terminals.js:7694` | See below. |
| Closing the session | `terminals.js:7798` | See below. |
| Moving the session | `terminals.js:1546` | See below. |

### The group-switch case, specifically

The one path that does **not** rebuild the card is the group switch:
`cacheVisibleGroupView()` (`terminals.js:955`) detaches the whole grid into a
`DocumentFragment` exactly as it stands, and `restoreCachedPaneUiState()`
re-attaches it later.

That is why `confirmDiscardAllExplorerEdits()` exits *every* open editor — clean
ones included — before the switch: a cached card must never come back wearing
Save/Cancel over a read-only panel. This proposal does not change that. By the
time the fragment is detached, `exitAllExplorerEditModes()` has already run and
no overlay exists to cache.

The residual risk is the inverse case — an overlay that survives detachment
because a *future* path detaches without exiting first — and it is why the
overlay must take no layout measurements at build time. See hazard 2.

`resyncExplorerEditorOnAttach()` (`explorer-editor.js`) remains the correct hook
if a re-derive is ever needed on re-attach; it is already called from
`restoreCachedPaneUiState()`.

### Not in scope, and unchanged

`hasAnyDirtyExplorerEdit()` currently has no consumer outside
`explorer-editor.js` — voluntary application exit (`/api/lifecycle/prepare`) does
not prompt for a dirty in-place edit. That is a pre-existing gap. This proposal
neither fixes nor worsens it, and deliberately does not expand into it.

## Hazards

1. **Wrap-column parity is the whole game.** The textarea's content box must be
   *exactly* the width of the underlay's code column, and its wrap rules must
   match `.explorer-source-view.wrap-lines .explorer-source-line-code`
   (`overflow-wrap: anywhere; word-break: break-word`) exactly. One pixel of
   drift and long lines break at different columns, and the layers desynchronise
   progressively down the file — far worse than no highlighting at all.

2. **The gutter is not currently a uniform width, and that blocks the overlay.**
   Each `.explorer-source-line` is its own grid container, so
   `minmax(42px, auto)` sizes each row's gutter from *that row's own* line
   number. Below 1000 lines every number fits the 42px floor and the columns
   agree. At 1000+ the four-digit rows get a wider gutter than the rows above
   them, so the code column starts at a different x. This is an existing, subtle
   misalignment in the read-only view; under an overlay with a single left
   padding it becomes a hard desync.

   Fix, which also repairs the read-only view: compute the gutter width from the
   digit count of the highest line number and publish it once as a CSS variable
   on `.explorer-source-lines`, in `ch` units, replacing the per-row
   `minmax(42px, auto)`. This is arithmetic on the line count — **no layout read,
   no `ResizeObserver`, no measurement while detached.** Keeping the build
   measurement-free is what makes the group-switch detach case safe by
   construction rather than by resync.

3. **Selection paints over the colours.** The textarea's native selection
   rectangle renders above the underlay, hiding the coloured text beneath it.
   Needs an explicit semi-transparent `.explorer-source-editor::selection`
   background taken from `tokens.css` — no palette literals — and an explicit
   `caret-color`, since `color` is transparent.

4. **Per-keystroke re-tokenisation.** `explorerHighlightDocumentLines()` is a
   whole-document Highlight.js pass plus a DOM template walk, bounded by
   `EXPLORER_PLAIN_PREVIEW_THRESHOLD` (2 MiB). Per keystroke on a large file this
   is not free. It must be rAF-coalesced — not an interval, per the performance
   guardrail — and the editor must use **its own cache slot**, never
   `pane._explorerHighlightCache`: writing drafts into that cache would thrash
   the entry that currently makes the post-save re-render free.

5. **Silent degradation.** Oversized file, language not in the pinned build, or a
   Highlight.js throw must fall back to today's bare textarea with no notice and
   no second global sink. The overlay is an enhancement, never a precondition for
   editing — if `explorer-edit-overlay.js` fails to load at all, edit mode must
   still work exactly as it does today.

6. **Tab order.** The underlay must contain no focusable elements. With folds
   suppressed it contains none; this must stay true if the row renderer grows new
   controls.

## Guardrail Check

| Guardrail | Position |
| --- | --- |
| 1 Security | No backend, no endpoint, no auth/host-key/secret surface touched. |
| 2 Concurrency | No server-side work; no locks involved. |
| 3 Performance | rAF coalescing, never an interval or busy-wait. No new network calls, no polling. No CDN — no new asset of any kind. |
| 4 Correctness | No `window.prompt/confirm/alert`; the existing shared confirm modal keeps every discard prompt. |
| 5 Dead code | No new config key, endpoint, or Socket.IO event. Every new function has a call site; one existing branch is deleted. |
| 6 Architecture/DRY | The underlay *is* the viewer's renderer — one highlighter, one token map, no copy. New surface in its own files; `explorer-viewer.js` shrinks; `terminals.js` untouched. |
| 7 Styling | All colours from `tokens.css` variables, including the new `::selection` and `caret-color`. No palette literals, no glyphs. |
| 8 Interaction | No new irreversible action. Discard prompts unchanged. Degradation is silent, so no second notification surface. |
| 9 Logging | No new logging. A Highlight.js throw keeps the existing `console.error` in the viewer. |
| 10 New features | Builds on the existing renderer and CSS variables; persists nothing; editing stays transient in-memory. |

## Staging

### Stage 0 — uniform gutter width

Hazard 2, on its own. Replace the per-row `minmax(42px, auto)` with a computed
`ch`-based variable on `.explorer-source-lines`. Ships independently, fixes a
real (if subtle) read-only misalignment on files over 999 lines, and is a
prerequisite for anything overlaid.

**Shipped.** `explorerSourceGutterWidthCss()` in `explorer-viewer.js` publishes
`--explorer-source-gutter-width` as an inline custom property on the lines
block; `.explorer-source-line`'s first grid track reads it. The formula floors
at the old 42px and adds the fold chevron's width on Markdown, where any row
may become a heading button.

### Stage 1 — geometry parity, no colour

Introduce the stack with an underlay rendered numbers-only. Match padding,
line-height, font, tab-size and wrap between the layers; delete the
`explorerPanelScrollTarget()` edit-mode branch.

Outcome: entering and leaving edit mode no longer moves the text, the gutter
survives, and scroll position survives both transitions exactly. Most of the
perceived harshness is gone here, at zero per-keystroke cost.

**Shipped, with two corrections to the plan above.**

*"Numbers-only" could not be numbers-only.* Row heights are the whole point of
geometry parity, and a wrapped line's height depends on its text. An underlay
of empty code cells would hold every row at one line while the textarea wrapped
long lines to several, and the gutter would desynchronise progressively down
exactly the files where it matters. The underlay therefore renders the real
draft — plainly, with no language passed, so no tokenizer runs and no fold
`<button>` is emitted — and CSS paints it transparent while the textarea still
carries the glyphs. Stage 2 flips which layer is transparent and passes the
language. The cost is that the underlay is rebuilt as the draft changes, which
is why `refreshDecision()` (rAF-coalesced, skipping an unmoved draft) and the
viability bound arrived in Stage 1 rather than Stage 2; the bound reuses the
viewer's existing `EXPLORER_PLAIN_PREVIEW_THRESHOLD` rather than inventing a
second number.

*The `explorerPanelScrollTarget()` edit branch was kept, not deleted.* Deleting
it would have been correct for the overlay and wrong for the degraded path,
where the bare full-height textarea really is the scroller and really does hold
the position Save restores. The branch now asks which of the two is in the
panel (`.explorer-edit-stack` present ⇒ the view scrolls both layers), and
`explorerEditScrollElement()` in `explorer-editor.js` answers the same question
for the enter/exit transfers. `explorer-viewer.js` grew by four lines instead
of shrinking.

Two consequences of rendering rows during an edit that the plan did not name:
`explorerOverviewGeometry()` gained an explicit `pane._explorerEdit` guard,
because "rows exist" had been standing in for "there is a read-only document to
survey"; and the stacked textarea's focus ring moved to the scroller, since its
own outline would be drawn around the whole document and never seen.

### Stage 2 — colour the underlay

Feed `state.draft` through the existing renderer, rAF-coalesced, with the
editor's own token cache. Textarea text becomes transparent.

Outcome: full syntax highlighting while editing, identical to the read-only view
because it is literally the same renderer.

### Stage 3 — optional, later

Find-within-editor, the occurrence tint, and draft-relative change marks.

Each stage is independently shippable and independently revertable. Stage 1 is
useful without Stage 2; Stage 2 does not alter Stage 1's geometry.

## Out of Scope

- **HEAD change bars and the overview ruler stay stood down.** Their model is
  HEAD-versus-disk (`explorer-overview.js`), and a dirty draft is neither.
  After Stage 2 they are the *only* remaining visible loss on entering edit mode,
  instead of one of five.
- Markdown section folding while editing.
- Dirty-edit prompting at application exit (see above).
- Any change to the read-only explorer contract, the `PUT` bounds, the save
  claim, or the conflict flow.
- Any backend change whatsoever.

## Testing

`explorer-edit-highlight.js` owns the only genuinely new decisions, both pure:

- **viability** — given content length, language, and engine availability, is the
  overlay on or off? (Drives the silent fallback.)
- **scheduling** — given previous state and an input event, re-highlight now,
  coalesce, or skip?

Node-executed behavioural tests against a stub, as
`tests/test_explorer_theme_store.py` and `tests/test_topbar_peek.py` already do.
No raw JS/CSS source-text assertions.

Regression coverage to re-run rather than rewrite:
`tests/test_explorer_editor_group_switch.py` already exercises leaving edit mode
across a cached group switch — the single riskiest path here — and must keep
passing untouched.

Alignment itself is not unit-testable. Manual pass across: wrap on and off; all
four source fonts (`default`, Cascadia Code, JetBrains Mono, Courier New); both
zoom extremes; a file over 999 lines (hazard 2); a file with tabs; a file with
CRLF line endings; and a mid-edit group switch with a dirty buffer.
