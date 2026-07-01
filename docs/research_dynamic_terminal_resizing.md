# Dynamic Terminal Resizing Research

## Todo Item / Goal

Research dynamic terminal resizing by dragging terminal borders with the mouse.

The expected behavior is that users can drag a visible divider between terminal panes, all affected panes resize together, xterm recalculates terminal columns and rows, and the backend PTY is resized to match. A pane must never be resized below the minimum usable surface. For this feature, interpret the requested "1/8 screen terminal surface available" rule as:

- every visible pane must retain at least one eighth of the available `#terminalsGrid` content area; and
- terminal panes must still satisfy the existing terminal usability guard of at least 8 columns by 8 rows.

The area rule prevents a pane from becoming visually insignificant. The 8x8 character rule prevents a pane with enough area but an unusable shape, such as a very wide but one-row terminal.

## Current Repo Observations

- `templates/terminals.html` owns the visible terminal workspace. `#terminalsGrid` is a CSS grid with `gap: 8px`, `padding: 8px`, and fixed layout classes at `templates/terminals.html:464`.
- Fixed launch layouts are expressed as CSS classes such as `layout-2-vertical`, `layout-3-split`, and `layout-grid` at `templates/terminals.html:475`.
- Live split panes already use `layout-split-local`, `--split-grid-columns`, and `--split-grid-rows` at `templates/terminals.html:525`.
- Split geometry is already represented in frontend state as `splitSlotRects` at `templates/terminals.html:1762`. Each visual pane has an integer grid rectangle with `x`, `y`, `w`, `h`, plus split ancestry metadata.
- `fixedLayoutSlotRects()` at `templates/terminals.html:2540` can convert launch layouts into slot rectangles. This is a useful entry point for turning any current layout into a resizable layout.
- `applySplitSlotGeometry()` at `templates/terminals.html:2633` applies `splitSlotRects` back to direct grid children by setting `gridColumn` and `gridRow`, then schedules xterm fits.
- The current split implementation enforces an 8-column by 8-row minimum using `MIN_SPLIT_COLS` and `MIN_SPLIT_ROWS` at `templates/terminals.html:1760`.
- `ResizeObserver` is already attached to terminal wrappers in `observeTerminalResize()` at `templates/terminals.html:1874`. It calls `scheduleFit(index)` and updates split button state when a pane changes size.
- `fitTerminal()` at `templates/terminals.html:3069` calls `fitAddon.fit()`, refreshes xterm, emits a backend resize, and flushes pending output.
- `emitTerminalResize()` at `templates/terminals.html:2992` emits `terminal_resize` only when xterm reports valid dimensions and the size changed.
- The backend resize path already exists. `handle_terminal_resize()` at `web/api.py:4076` calls `_resize_connection()`, and `_resize_connection()` at `web/api.py:1904` clamps to 8-400 columns and 8-200 rows before resizing SSH, WinPTY, or POSIX PTYs.
- Drag swapping currently swaps direct `.terminal-container` children in `swapTerminalCards()` at `templates/terminals.html:3335`. Keeping panes as direct grid children preserves this behavior.
- Session group view caching already preserves `splitSlotRects`, grid class, and grid CSS variables in the cache/restore path around `templates/terminals.html:1987` and `templates/terminals.html:2040`.
- Narrow screens already disable split behavior through `getSplitCandidates()` when `window.innerWidth <= 700` at `templates/terminals.html:2694`. Resizing should probably follow the same restriction for v1.

## Implementation Possibilities

1. **Native CSS `resize` on each terminal card**
   - Add `resize: both` or custom CSS resize affordances to `.terminal-container`.
   - Tradeoffs: not recommended. Native resizing does not coordinate neighboring CSS grid tracks, creates overlap/overflow risks, and does not naturally enforce shared dividers or all-pane constraints.

2. **Dedicated split-pane library**
   - Introduce a layout dependency such as Split.js, GoldenLayout, or a docking workspace library.
   - Tradeoffs: useful for complex docking later, but it conflicts with the current hand-built grid, drag swapping, session caching, and split/unsplit code. It also adds dependency and styling surface for a relatively narrow feature.

3. **Rewrite panes as absolute-positioned rectangles**
   - Stop using CSS grid and position every pane with percentage-based `left`, `top`, `width`, and `height`.
   - Tradeoffs: gives precise control over drag math, but it is a large frontend rewrite. Drag/drop, responsive collapse, cached group views, split geometry, and browser/explorer pane behavior would all need more changes.

4. **Resizable CSS grid lines using the existing `splitSlotRects` model**
   - Keep direct children inside `#terminalsGrid`.
   - Convert any active layout into `layout-split-local`.
   - Keep `splitSlotRects` for pane grid occupancy.
   - Add separate column and row weight arrays, for example `splitColumnWeights` and `splitRowWeights`.
   - Render grid templates as weighted tracks, such as `grid-template-columns: 1fr 1.35fr 0.65fr 1fr`, instead of only `repeat(N, 1fr)`.
   - Add draggable handles on shared grid lines. Dragging a vertical handle adjusts neighboring column weights; dragging a horizontal handle adjusts neighboring row weights.
   - Validate the candidate weights against every pane before applying them.
   - Tradeoffs: this is the smallest implementation that matches the current architecture. It resizes along existing grid lines rather than arbitrary per-pane freeform edges, which is acceptable for a grid/split terminal workspace.

5. **Full split tree with persistent ratios**
   - Replace or supplement `splitSlotRects` with a layout tree where internal nodes have `axis`, `ratio`, and child nodes.
   - Render nested split containers, or flatten the tree into CSS grid rectangles.
   - Tradeoffs: this is the cleanest long-term model for terminal-emulator style splits, but it is more invasive. It would likely require a deliberate migration path for the current `splitSlotRects` behavior.

## Recommended Safest / Simplest Approach

Use option 4: resizable CSS grid lines on top of the existing split geometry.

This keeps the implementation local to `templates/terminals.html`, avoids changing backend session lifecycle code, preserves direct grid children for drag swapping, and reuses the existing xterm fit and backend PTY resize path. It also works for normal fixed launch layouts by converting them to `splitSlotRects` when the first resize interaction begins.

For v1, keep the resize geometry session-window local, like live split geometry today. Do not persist custom pane sizes into saved sessions until the interaction has been proven stable.

## Implementation Status

Implemented the recommended safest/simplest approach in `templates/terminals.html`:

- Added session-window-local `splitColumnWeights`, `splitRowWeights`, and `activeGridResize` state.
- Added a sibling `#terminalResizeOverlay` with vertical and horizontal `.terminal-resize-handle` controls so pane elements remain direct `#terminalsGrid` children.
- Converts fixed launch layouts to `layout-split-local` on the first divider drag and applies weighted CSS grid tracks with inline `gridTemplateColumns` / `gridTemplateRows`.
- Resizes the full track span on each side of a divider, which keeps doubled fixed-layout tracks such as three-pane horizontal, vertical, and split layouts responsive.
- Validates drag candidates against both minimum rules: every visible pane must keep at least one eighth of the grid content surface, and terminal panes must retain at least 8 columns by 8 rows.
- Reuses existing `scheduleFit()`, `fitTerminal()`, `emitTerminalResize()`, and final `redrawAttachedTerminals(..., { forceResize: true })` behavior instead of changing backend resize handling.
- Caches and restores local resize weights with cached session group views, but does not persist custom pane sizes into saved sessions.
- Clears resize state, inline grid templates, and handles during layout teardown and empty-state reset.
- Keeps handles disabled below the existing `700px` narrow-screen breakpoint.

Added HTML-level regression coverage in `tests/test_api.py` for resize handle CSS/state, minimum validation, drag refit/final resize behavior, and cached group resize-weight preservation.

## Proposed Frontend Design

1. Add resize state:
   - `let splitColumnWeights = null;`
   - `let splitRowWeights = null;`
   - `let activeGridResize = null;`

2. Extend `clearSplitSlotGeometry()`, `cacheVisibleGroupView()`, `restoreCachedGroupView()`, and empty-state teardown to clear/cache/restore the new weight arrays.

3. Add `ensureResizableSplitLayout()`:
   - calls `ensureSplitSlotRects()`;
   - switches the grid to `layout-split-local`;
   - initializes `splitColumnWeights` and `splitRowWeights` to arrays of `1`;
   - calls an extended `applySplitSlotGeometry()`.

4. Extend `applySplitSlotGeometry()`:
   - continue applying each pane's `gridColumn` and `gridRow`;
   - set `--split-grid-columns` and `--split-grid-rows` as today for compatibility;
   - also set concrete templates when weights exist, for example:
     - `grid.style.gridTemplateColumns = splitColumnWeights.map(w => \`${w}fr\`).join(' ')`;
     - `grid.style.gridTemplateRows = splitRowWeights.map(w => \`${w}fr\`).join(' ')`;
   - remove those inline templates when returning to fixed layout.

5. Add a handle overlay:
   - create lightweight child elements or an absolutely positioned overlay inside the grid container;
   - vertical handles correspond to grid lines between columns;
   - horizontal handles correspond to grid lines between rows;
   - only show handles for grid lines that are adjacent to at least two visible panes and have a meaningful shared edge;
   - use `pointerdown`, `pointermove`, `pointerup`, and `setPointerCapture()`.

6. On pointer move:
   - calculate the drag delta in grid content pixels, excluding grid padding and gaps;
   - convert the delta into adjacent track weight changes;
   - build candidate column/row weights without mutating live state;
   - validate every pane against the minimum rules;
   - if valid, assign the candidate weights, call `applySplitSlotGeometry({ fit: false })`, and schedule fits for affected panes with `requestAnimationFrame`.

7. On pointer up:
   - run a final `applySplitSlotGeometry({ fit: true })`;
   - call `redrawAttachedTerminals(affectedIndices, { forceResize: true })` or force `emitTerminalResize(index, true)` after `fitTerminal(index)`;
   - clear `activeGridResize`.

8. During drag:
   - set `document.body` cursor and `user-select: none`;
   - suppress terminal card drag/drop while a resize handle is active;
   - keep xterm input focus stable unless the user explicitly clicks inside another terminal.

## Constraint Model

Use a two-stage minimum check before applying candidate weights.

1. Pixel surface check:
   - compute the usable grid content rectangle from `#terminalsGrid.getBoundingClientRect()` minus padding and gaps;
   - compute each pane's candidate pixel width and height from the sum of the tracks it spans plus internal gaps;
   - require `paneWidth * paneHeight >= gridContentWidth * gridContentHeight / 8`.

2. Terminal character check:
   - for terminal panes, estimate available terminal wrapper dimensions by subtracting the card header height and wrapper chrome from the candidate pane size;
   - use xterm's measured cell dimensions from `term._core._renderService.dimensions.css.cell`, with conservative fallbacks similar to `estimatePaneCharacters()`;
   - require at least `MIN_SPLIT_COLS` by `MIN_SPLIT_ROWS`;
   - explorer panes can skip the xterm character check but should still obey the one-eighth pixel surface rule.

This means a drag can be rejected even if the visual area is large enough but the resulting terminal surface would be too short or narrow.

## Implementation Outline

1. Add CSS for resize handles:
   - `.terminal-resize-handle`;
   - axis-specific cursor styles;
   - a subtle hover/active state using existing border/accent variables;
   - disable handles under the same narrow-screen breakpoint used for splitting.

2. Add helpers:
   - `ensureResizableSplitLayout()`;
   - `initializeSplitTrackWeights()`;
   - `cloneSplitTrackWeights()`;
   - `applySplitTrackTemplates(grid)`;
   - `renderResizeHandles()`;
   - `getResizableGridMetrics()`;
   - `validateResizeCandidate(axis, candidateWeights)`;
   - `getPaneCandidateSurface(rect, columnWeights, rowWeights, metrics)`.

3. Call `renderResizeHandles()` after:
   - `buildGrid()`;
   - `applySplitSlotGeometry()`;
   - split/unsplit;
   - drag swap;
   - cached group restore;
   - window resize.

4. Update split creation:
   - when `splitSlotRect()` adds new grid tracks, insert matching default weights;
   - when closing/unsplitting, remove or normalize unused tracks if practical, or rebuild weights from the current max grid line.

5. Update cache/restore:
   - store `splitColumnWeights` and `splitRowWeights` with cached group views;
   - restore them before calling `applySplitSlotGeometry()`.

6. Add frontend HTML tests in `tests/test_api.py`:
   - resize handle CSS exists;
   - resize state variables exist;
   - candidate validation includes the one-eighth surface rule;
   - drag handlers call `scheduleFit()` or `redrawAttachedTerminals()`;
   - cached group views preserve resize weights.

7. Manual test matrix:
   - 2 vertical terminals, drag center divider left/right;
   - 2 horizontal terminals, drag center divider up/down;
   - 3 split layout, drag both shared dividers;
   - 4, 6, and 8 terminal grids, verify no pane can go under one eighth of grid surface;
   - split a pane, resize its new divider, then drag-swap terminals;
   - switch session tabs and verify geometry is restored;
   - browser mode and native `pywebview` mode;
   - window resize after custom pane sizing;
   - explorer panes mixed with terminal panes.

## Risks / Tests To Consider

- **Ambiguous minimum rule:** area alone can permit unusable thin panes. Enforce both one-eighth visual surface and 8x8 terminal cells.
- **More than eight panes:** the current split limit already caps terminal panes through `MAX_SPLIT_TERMINALS`. Keep that cap because a one-eighth minimum implies eight equally sized panes is the practical maximum.
- **Grid gaps and padding:** candidate math must include `gap: 8px` and `padding: 8px`; otherwise the visual minimum and actual DOM size will drift.
- **Nested splits:** a grid-line drag may affect multiple panes sharing that line. This is acceptable for v1 but should be documented in behavior if users expect isolated nested split ratios.
- **Resize performance:** fitting xterm on every pointer move can be expensive. Apply CSS geometry during pointer move, throttle fits to animation frames, and force final backend resize on pointer up.
- **Backend event spam:** `emitTerminalResize()` already deduplicates unchanged dimensions. Keep force-resize only for pointer up, tab restore, and major redraw paths.
- **Drag/drop conflict:** resize handles need to stop propagation so they do not start terminal card drag swapping.
- **Cached hidden groups:** inactive cached DOM fragments may have stale measurements. Re-render handles and refit terminals after restore, not while hidden.
- **Responsive mode:** disable handles below `700px` for v1, matching split behavior, because the mobile layout collapses fixed layouts to one column.
- **Explorer panes:** explorer panes do not emit terminal resize events, but they still need surface constraints and should redraw cleanly.
- **Inline grid templates:** `clearSplitSlotGeometry()` and empty-state reset must remove `gridTemplateColumns` and `gridTemplateRows` inline styles to avoid stale sizing when returning to fixed layouts.

## Longer-Term Follow-Ups

- Persist custom pane sizes in saved sessions by adding a versioned `layout_geometry` field to session group/config data.
- Replace integer `splitSlotRects` plus track weights with a true split tree if isolated nested split resizing becomes important.
- Add keyboard-accessible resizing, such as focused divider handles with arrow-key adjustments.
- Add a reset action that returns the active group to its launcher layout or equal split weights.
