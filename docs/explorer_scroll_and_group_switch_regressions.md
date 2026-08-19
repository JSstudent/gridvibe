# Explorer Scroll Stability and Group-Switch Cost After the Large-Content Work

**Status:** Stage 1 implemented and live-browser verified on **2026-08-19**.
Stages 2–4 remain pending. The original analysis was verified against the
working tree at `4eed02b`.
**Scope:** Three regressions reported after
`docs/large_content_freeze_analysis.md` phases 0–2 landed:
1. Markdown Preview scroll position is no longer preserved.
2. The viewer's right-hand scrollbar moves horizontally between
   Source / Preview / edit modes, and the text re-wraps with it.
3. Opening a large file in one session group and switching to another group
   is slow.
**Companion documents:** `docs/large_content_freeze_analysis.md` (what was
built and why), `docs/explorer_performance_research.md` (the broader picture).

> **This document is an analysis, not a contract.** Per `CLAUDE.md`, audits and
> analyses are never cited from code, docstrings, tests, or the maintained
> documents. Any rule that survives implementation is written into the
> **Regression Guardrails** lists in `CLAUDE.md` *and* `AGENTS.md` — not as a
> reference back to this file.

---

## Verdicts

| # | Report | Verdict | Root cause |
|---|---|---|---|
| 1 | Preview scroll not preserved | **Fixed in Stage 1** | The restore now waits for Preview readiness and reapplies from Preview's own arrival path |
| 1b | Same for other open tabs | **Fixed in Stage 1** | Each tab retains revision-checked per-panel offsets across its full re-render |
| 1c | — (not reported, found here) | **Fixed in Stage 1** | In-place refresh installs the captured offsets before starting the replacement Preview request |
| 1d | — (not reported, found here) | **Fixed in Stage 1** | Hidden panels retain their offsets and apply them when shown |
| 2 | Scrollbar jumps between modes | **Confirmed** | The Source panel's scroller is inset by the overview ruler column; the Preview panel's is not — and the ruler column *leaves the layout* in edit mode, in the large tier, and on an empty file |
| 3 | Group switch is slow with a large file open | **Confirmed, four separate costs** | Whole-content re-hash per switch; a 4-pass read/write-interleaved scroll restore; frame-sliced builds that keep running off-screen; an overview-geometry cache poisoned by the detach |

---

## Finding 1 — the Preview scroll is restored before the Preview exists

### Stage 1 implementation update — complete

The original timing diagnosis was correct but incomplete. Live-browser
validation exposed the final overwrite that the DOM stub did not reproduce:

1. a saved Preview target of `640` was applied to the small
   `Rendering preview...` loader;
2. the loader could hold only `57` in the diagnostic window (normally `0` in
   a full-size pane), so the write was clamped;
3. the render's ordinary presentation snapshot then captured that temporary
   loader position as if it were the reader's new position, replacing `640`
   before the Markdown response arrived.

Stage 1 now has one per-panel store and one bounded application path, split
between the DOM-free `explorer-scroll.js` policy and
`explorer-scroll-adapter.js`. A Preview that has not loaded cannot consume its
saved target, and presentation capture retains the stored target instead of
reading the loader. The Preview arrival reapplies after `paintExplorerPreview()`;
view switches apply only the panel just shown; an in-place refresh associates
the old viewport with the replacement bytes before fetching; late responses
are rejected by pane, session, path, content and element identity. Source keeps
its existing source-render queue, and Diff keeps its async arrival behavior.

Lazy Mermaid growth uses the bounded correction option from 1.4 below: a
diagram above an untouched restored position may reapply once after it draws;
the first reader movement cancels that correction.

Verification completed:

- the Node-executed policy and DOM-adapter suite covers delayed Preview
  arrival, the loader-snapshot overwrite, tab/view/group switches, in-place
  refresh, revision filtering, late responses and Mermaid growth;
- the related Preview/save/repaint/group-switch suites pass;
- a live Chrome round trip against the running application held the exact
  offset `640 → switch tab → switch back → 640`;
- JavaScript syntax, Ruff and diff checks pass. The full 1,841-test run retains
  only the two pre-existing Windows Git-timeout failures, unrelated to this
  stage.

### What changed

Before phase 0.4, `preview_html` arrived with the file payload and
`renderExplorerFile()` wrote it into the panel **synchronously**, in the same
task that built the viewer (`git show 8902827^:web/static/js/explorer-viewer.js`,
line 7713: `preview.innerHTML = pane._explorerPreviewHtml;`). By the time
`restoreExplorerFileScroll()` ran, the panel was its full height and the stored
offset landed.

Today the panel is created empty and its content is fetched on demand
(`ensureExplorerPreviewLoaded()`, `explorer-viewer.js:5755`), kicked off at
`explorer-viewer.js:7697`. The restore is queued at `explorer-viewer.js:7740`:

```js
whenExplorerSourceRendered(index, () => restoreExplorerFileScroll(index, effectiveScrollState));
```

`whenExplorerSourceRendered()` waits for the **Source rows**, which have
nothing to do with the Preview panel, and fires long before a network round
trip plus a server-side Markdown render and Bleach sanitize can answer. At that
moment the panel is empty, so in `applyScrollMetrics()`
(`explorer-viewer.js:6620`) `maxScrollTop` is `0` and the offset is clamped to
`0`. The `requestAnimationFrame` ×2 + 80 ms re-applies inside
`restoreExplorerFileScroll()` (`explorer-viewer.js:6721-6726`) all expire well
before the fetch lands.

**The Diff panel has exactly the fix this needs and Preview does not.** Diff
content is also async, so `renderExplorerFile()` stashes
`pane._explorerPendingDiffScroll` (`explorer-viewer.js:7615`) and
`applyExplorerPendingDiffScroll()` (`explorer-viewer.js:6789`) re-applies it
when the diff arrives. There is no `_explorerPendingPreviewScroll`.

### Three further contributors

- **1b — every tab activation pays it.** `activateExplorerTab()`
  (`explorer-tabs.js:664`) captures the outgoing tab's view
  (`explorerCaptureActiveTabView`, `explorer-tabs.js:277`) and then re-renders
  through `openExplorerFile()` → `renderExplorerFile()`. So the restore path
  above is the *only* path a tab switch has, and Preview loses its offset on
  every switch, for every tab.
- **1c — a save does it too.** `updateExplorerFileInPlace()` clears
  `_explorerPreviewHtml` / `_explorerPreviewLoaded`
  (`explorer-viewer.js:7808-7809`), re-fetches at `7827`, and queues its
  restore at `7866` on the same source-render queue. Saving a Markdown file
  while Preview is the active view therefore returns the reader to the top —
  precisely the class of defect `tests/test_explorer_save_refresh.py` exists to
  guard.
- **1e — lazily-drawn Mermaid moves the floor again.** Even once the HTML is
  in, `renderExplorerMermaidLazily()` (`explorer-viewer.js:5472`) draws
  diagrams as they scroll into view, so the panel's `scrollHeight` **grows**
  after paint. An offset re-applied at paint time is still clamped against a
  document shorter than the captured one. This is the same trap
  `large_content_freeze_analysis.md` names for the repaint token — the panel a
  restore lands on is shorter than the panel that was captured.

### 1d — a latent defect the same fix should close

`restoreExplorerFileScroll()` walks **every** `[data-explorer-file-panel]` and
applies its stored metrics (`explorer-viewer.js:6710-6719`). A panel that is
not the active view carries the `hidden` attribute →
`.explorer-editor-panel[hidden] { display: none; }` (`terminals.css:3680`) → no
box, `scrollHeight === 0`, and the write is discarded. Only the panel
`setExplorerFileView()` has just un-hidden (called first, at
`explorer-viewer.js:6696`) can accept an offset.

Nothing re-applies a panel's offset when it later becomes visible:
`setExplorerFileView()` (`explorer-viewer.js:4029`) toggles `hidden`, loads
content and re-runs the find — it never restores scroll. So Source → Preview →
Source inside one file already loses the Source offset unless a render happens
to hold it.

**This is the unifying insight for the whole finding:** a panel's scroll offset
is being treated as something applied *once, at render time*, when it is
actually a property of *showing that panel*. Both the async-content problem and
the hidden-panel problem disappear if the offset is applied when the panel
becomes visible and has content, rather than when the source rows finish.

---

## Finding 2 — the viewer's right edge is not the same width in every mode

### The two scrollers do not line up

- **Source.** `.explorer-source-frame` is a two-column grid
  (`terminals.css:3689`): `grid-template-columns: minmax(0, 1fr) auto`. Column 1
  is `.explorer-source-view` (the scroller, `overflow: auto`,
  `terminals.css:3837`); column 2 is the overview ruler aside
  (`.explorer-source-overview`, 14 px in `ruler` mode via
  `--explorer-overview-ruler-width`, `terminals.css:3725`). The scroller's
  native vertical scrollbar therefore sits **14 px in** from the pane's right
  edge.
- **Preview.** `.explorer-markdown-preview` *is* the scroller
  (`.explorer-editor-panel { overflow: auto }`, `terminals.css:3675`) and spans
  the full width of `.explorer-editor-main`. Its scrollbar sits **at** the
  pane's right edge.

So every Source ⇄ Preview switch moves the visible bar sideways by the ruler
width, and the wrapping measure changes by the same 14 px.

### The ruler leaves the layout, so Source's own width is not stable either

`syncExplorerOverview()` sets `parts.aside.hidden = !geometry`
(`explorer-overview.js:1013`), and `explorerOverviewGeometry()` returns `null`
in three cases (`explorer-overview.js:665-683`):

- `pane._explorerEdit` is set — **the in-place editor**;
- there are no `.explorer-source-line[data-explorer-line]` rows — **an empty
  file, and the large-file tier**, which renders plain chunks and no rows;
- no pane.

Because the frame's second track is `auto`, a `display: none` aside collapses
that track to **0**. Hence the reported symptom exactly: entering edit mode
widens the source view by 14 px, re-wraps the text and slides the scrollbar
right; leaving it undoes both. The large tier does the same thing between
files.

### One more source of the same jank

None of these scrollers set `scrollbar-gutter: stable` (the only occurrence in
the file is `terminals.css:5239`, on the diff's horizontal scroller). So the
appearance or disappearance of the vertical bar itself re-wraps the text —
which a fold, a find that changes the row set, or a tier switch can each
trigger.

### Design note, not a defect

In Source mode there are effectively two vertical indicators side by side: the
scroller's native bar and the ruler aside, which is itself `role="scrollbar"`
(`explorer-overview.js:617-631`). That is a deliberate existing decision and
this document does not propose changing it — only that its width stops moving.

---

## Finding 3 — what a group switch pays for an open large file

`cacheVisibleGroupView()` (`terminals.js:951`) moves the outgoing group's cards
into a `DocumentFragment`; `restoreCachedGroupView()` (`terminals.js:1016`)
appends the incoming one. Four costs scale with the size of an open file.

### 3a — the whole file is re-hashed on every switch (~58 ms per 10 MiB, measured)

`captureCachedPaneUiState()` (`terminals.js:854`) calls
`explorerCaptureActiveTabView(index)` for **every** explorer pane, which calls
`explorerCurrentContentRevisions(pane)` (`explorer-tabs.js:303` →
`explorer-viewer.js:6761`), which calls `explorerFileContentIdentity()`
(`explorer-viewer.js:6748`) — a djb2 loop over every character of the file
(`explorerHashText()`, `explorer-viewer.js:6737`), preceded by a `join()` that
materializes a full second copy of the buffer (V8 flattens the cons string on
the first `charCodeAt`).

Measured in Node on this machine: **57–59 ms for a 10 MiB buffer**, three runs.
If a diff is loaded, `diffContentRevision()` (`explorer-persistence.js:208`)
hashes the rendered patch as well — up to another 256 KiB. This is per pane,
per switch, in both directions.

It is also entirely avoidable: the content is a **stable string reference** on
the pane, so a cached `{path, content, revisions}` compared by `===` is exact
and free. `explorerPreviewRenderToken()` (`explorer-viewer.js:5665`) already
does precisely this for the preview token — the pattern exists a hundred lines
away.

The same call sites are hit again on restore (`explorer-tabs.js:643`,
`explorer-viewer.js:6800`, `explorer-viewer.js:8187`, `explorer-diff.js:1128`).

### 3b — the scroll restore forces layout, four times, interleaving reads and writes

`restoreExplorerFileScroll()` runs `applyScroll()` immediately, then in two
nested `requestAnimationFrame`s, then again on an 80 ms timer
(`explorer-viewer.js:6721-6726`). Each pass touches the list, up to three
sidebar panels and every file panel, and `applyScrollMetrics()` **reads**
`scrollHeight`/`clientHeight` and then **writes** `scrollLeft`/`scrollTop` per
element (`explorer-viewer.js:6620-6640`) — the write invalidating the layout
the next element's read needs. `restoreCachedPaneUiState()` (`terminals.js:879`)
does this for every pane in the incoming group, so the passes interleave
*across* panes as well.

On a freshly attached card whose Source panel holds up to 20,000 rows, that is
the pattern guardrail 3 already names in `synchroniseExplorerDiffWrappedRows()`,
in a different file.

The four passes are themselves a symptom of the same design problem as
Finding 1: they exist because nobody knows when the content will be final, so
the restore is simply repeated until it is likely to have been.

### 3c — frame-sliced builds keep running while the group is off screen

`explorerRunSourceRenderJob()` (`explorer-viewer.js:5273`) and the large tier's
chunk pacer (`explorer-viewer.js:173-234`) stop on exactly two conditions: a
newer render token, or the panel no longer being the container they were
filling. Being **disconnected from the document** is neither. Nothing in
`cacheVisibleGroupView()` cancels or suspends them, so opening a large file and
immediately switching groups leaves a job spending its 8 ms frame budget
(`EXPLORER_SOURCE_RENDER_BUDGET_MS`, `explorer-viewer.js:5271`) appending rows
into a detached tree — competing with the incoming group's attach, fit and
paint. The same applies to a worker highlight result arriving for a pane nobody
is looking at.

### 3d — the detach poisons the overview geometry cache

`explorerOverviewGeometry()` caches on a signature of
`[rows.length, code.scrollHeight, code.clientWidth, wrapped]`
(`explorer-overview.js:688-697`). A `ResizeObserver` on the frame
(`explorer-overview.js:988-991`) fires when the card is disconnected, with a
zero size; the sync then rebuilds and caches a geometry measured at
`scrollHeight = 0`. On re-attach the signature differs again, so the cache
misses and a **full per-row measurement pass** runs — `offsetTop` and
`offsetHeight` for every one of up to 20,000 rows
(`explorer-overview.js:699-712`). Had the cache not been poisoned, the
signature would have matched and the pass would have been skipped entirely.

### 3e — the irreducible part

Re-attaching a card containing tens of thousands of row elements costs a full
style recalculation and layout no matter what else is fixed. There is no
`content-visibility` anywhere in `web/static/css/` (unchanged since the earlier
analysis), so every row is laid out even though at most a screenful is visible.
That is a spike, not a fix — see Stage 4.

### Ruled out

The change watcher does **not** poll detached panes: `explorerGitWatchTick()`
iterates `terminals` (`explorer-git-watch.js:674`), and
`cacheVisibleGroupView()` empties that array (`terminals.js:998`). It is,
however, worth confirming during measurement that a *deferred* file-watch apply
does not land as a full re-render at the moment a group is restored.

---

## Staged fix plan

Stages are independent. Stage 1 landed first to close the reported regression;
Stages 2 and 3 remain separate follow-up work. Stage 3's items 3.2 and 3.3
touch ordering that Stage 1 also touches, so they must build on the completed
Stage 1 path rather than reintroducing a second restore mechanism.

### Stage 1 — complete: a panel's scroll offset is applied when the panel is shown, not when the rows finish

**Goal:** the Preview offset survives a tab switch, a view switch, a save, and
a group switch; Source and Diff keep behaving exactly as they do today.

**1.1 — Complete: give the pane a per-panel offset store and one application point.**
`captureExplorerFileScroll()` remains the reader. The **DOM-free** policy
module `web/static/js/explorer-scroll.js`, with its Node-executed test, now
owns:
- which panel offsets are still valid for a given content revision set (the
  logic `explorerMatchingTabView()` already applies, lifted so both callers
  share it);
- whether an offset is *satisfiable yet* — i.e. whether the target's scroll
  extent can hold it, which is what "the content has arrived" actually means;
- when to stop re-trying (a bounded number of attempts, not a fixed 80 ms).

Implemented as the DOM-free policy plus the separate
`explorer-scroll-adapter.js`; `explorer-viewer.js` contains only the capture and
paint/readiness call sites.

**1.2 — Complete: apply on show.** `setExplorerFileView()` applies the stored offset to
the panel it just un-hid, after `ensureExplorerPreviewLoaded()` /
`loadExplorerDiff()` have had their chance. This alone closes 1d and the
Source ⇄ Preview ⇄ Source case.

**1.3 — Complete: apply on arrival.** `ensureExplorerPreviewLoaded()` re-applies the
stored offset after `paintExplorerPreview()`, exactly as
`applyExplorerPendingDiffScroll()` does for Diff, and for the same reason.
Guard it with the identity checks the fetch already performs
(`explorer-viewer.js:5779-5784`) so a pane that moved on is never scrolled.

**1.4 — Complete: survive lazily-drawn diagrams.** The bounded reapply option
was selected:
- `renderExplorerMermaidLazily()` reapplies once when a diagram above the
  restored offset finishes;
- the correction is bounded and is dropped as soon as the reader scrolls;
- no intrinsic placeholder height or unrelated Preview layout change was
  introduced.

**1.5 — Complete: a restore is not a source-render event.** `renderExplorerFile()` now
queues each panel's restore on that panel's own readiness rather than
unconditionally on `whenExplorerSourceRendered()`. Keep the source queue for
the Source panel — that ordering is load-bearing for the find and the editor's
selection restore (guardrail 3) and must not change.

**Tests — complete.** `tests/test_explorer_scroll.py` executes the DOM-free
policy and the DOM adapter in Node. It covers an offset captured in Preview
surviving a tab switch where the preview fetch resolves *after* the source
rows, the loader/presentation snapshot retaining the pending target, a save on
a Markdown file in Preview view, a hidden panel applying its offset when shown,
and a pane that moved on rejecting a late response.

**What the user sees:** Preview keeps its place across tabs, view switches,
saves and group switches. Source and Diff are unchanged.

**Guardrail exposure:** G6 (new module gets a Node test), G3 (a re-apply must
not become a polling loop — bound the attempts), and the
`whenExplorerSourceRendered()` ordering contract must survive intact.
**Risk:** medium — it touches the restore path four call sites depend on.

### Stage 2 — the viewer's right edge stops moving

**Goal:** the scrollbar sits in the same place, and the text wraps to the same
measure, in Source, Preview and edit mode.

**2.1 — Reserve the ruler track unconditionally.** Change
`.explorer-source-frame`'s second track from `auto` to the mode's width
(`var(--explorer-overview-ruler-width)`). An explicit track keeps its width
even when its only item is `display: none`, so the frame stops re-wrapping when
the aside stands down. `map` mode is not currently reachable
(`data-explorer-overview-mode` is hard-coded `ruler`,
`explorer-overview.js:622`); if it returns, key the track off that same
attribute rather than adding a second source of truth.

**2.2 — Keep the aside in layout and mark it empty.** An empty reserved track
shows the frame's background where the aside's `--explorer-bar-bg` and its 1 px
left border used to be, which is a visible seam. Replace
`parts.aside.hidden = !geometry` with a state class that keeps the strip and
its border and hides only the canvas and the viewport box. `hidden` stays
available for a genuine "no overview column at all" case.

**2.3 — Give Preview the same reserved gutter.** Two options:
- **A (CSS-only, recommended first):** inset the Preview panel's right edge by
  the ruler width so its scrollbar lands where Source's does — a right border
  in `--explorer-bar-bg` with the same 1 px separator, leaving the scroller and
  every `getElementById('explorer-preview-…')` lookup exactly as they are.
- **B (structural, cleaner):** hoist the gutter to `.explorer-editor-main` as a
  two-column grid and move the overview aside there as a sibling of the panels,
  so Source and Preview occupy the identical column by construction. Requires
  updating `explorerOverviewParts()` (`explorer-overview.js:634`, which derives
  the frame from `code.parentElement`) and the `parts.frame.hidden` check.
  Worth doing if `map` mode ever returns; not worth the blast radius now.

**2.4 — `scrollbar-gutter: stable`** on the Source view and the Preview panel,
so the bar appearing or disappearing no longer re-wraps the text.

**2.5 — Optional consistency.** The Diff panel is a full-width scroller of its
own (`.explorer-diff-split`, `terminals.css:4224`) and its bar sits at the pane
edge. Not part of the report; include only if the same gutter can be reserved
without disturbing `synchroniseExplorerDiffWrappedRows()`.

**Tests.** `tests/test_explorer_source_frame.py` already asserts the frame's
CSS block (`display: grid`, `overflow: hidden`, the `[hidden]` rule ordering) —
extend it with the fixed second track and the Preview gutter. Add a Node test
that the aside stands down without leaving the layout.

**What the user sees:** entering and leaving edit mode no longer re-wraps the
text or slides the scrollbar; Source and Preview show the bar in the same
place; large-tier files keep the same measure as small ones.

**Guardrail exposure:** G7 — widths and colours come from the existing custom
properties (reuse `--explorer-overview-ruler-width`, never restate 14 px) and
`tokens.css`, no new palette literals.
**Risk:** low.

### Stage 3 — make a group switch cost what the *visible* group costs

Measure first (below); land the items the numbers justify.

**3.1 — Cache the content identity per pane.** Compare the held `content` and
`path` by reference and reuse the previous revisions object, mirroring
`explorerPreviewRenderToken()`. Exact, not approximate: the string reference
changes whenever the bytes do. Removes ~58 ms per 10 MiB pane per switch, plus
the transient full-buffer copy.

**3.2 — Batch the scroll restore.** One read pass collecting every target's
extents, then one write pass — the two-pass shape guardrail 3 already requires
of the diff row sync. Then replace the fixed four passes with Stage 1's
"satisfiable yet?" predicate, so a restore stops as soon as it has landed
instead of forcing three more layouts on a 20,000-row document.

**3.3 — Suspend off-screen builds.** `cacheVisibleGroupView()` suspends any
in-flight sliced source build or chunk pacer; `restoreCachedGroupView()`
resumes it. **Queued readers must not be dropped** — the contract that a
superseded build hands its queue on (`explorer-viewer.js:5229-5241`) applies
here too: a suspended build whose group is closed while suspended must still
flush, exactly as `explorerAbandonSourceRenderJob()` does.

**3.4 — Don't measure a disconnected frame.** `syncExplorerOverview()` returns
early when `parts.frame.isConnected` is false, alongside the existing
`parts.frame.hidden` check (`explorer-overview.js:1006`). The zero-size
observation then never reaches the cache, the signature still matches on
re-attach, and the per-row measurement pass is skipped.

**Tests.** Node-executed: revisions are computed once for an unchanged buffer;
a suspended build flushes its queued readers when its group is closed while
suspended; a disconnected frame is not measured. Extend
`tests/test_explorer_overview.py` and `tests/test_explorer_repaint.py` rather
than adding a new file where the domain already has one.

**What the user sees:** switching groups with a large file open stops
stuttering. Nothing looks different.

**Guardrail exposure:** G3 (no busy-waiting — the resume is event-driven).
Nothing here touches the backend, so guardrails 1, 2 and the durable stores are
untouched.
**Risk:** low for 3.1 and 3.4, medium for 3.2 and 3.3.

### Stage 4 — spike only: stop laying out rows nobody can see

`content-visibility: auto` + `contain-intrinsic-size` on `.explorer-source-line`
would make attach/detach cost proportional to the viewport rather than to the
document. Two obstacles must be settled **in the spike, before any of it is
written**:

- It fights the overview ruler. `explorerOverviewGeometry()` reads `offsetTop`
  and `offsetHeight` for **every** row; those reads force layout of exactly the
  content `content-visibility` was skipping. The ruler would need a sampled or
  estimated geometry, which changes what it draws.
- It fights Stage 1. Skipped rows are sized by their `contain-intrinsic-size`
  estimate, so `scrollHeight` becomes approximate — and the current restore is
  deliberately exact-offset-first (`explorer-viewer.js:6600-6619`) precisely
  because proportional restores put the reader somewhere they never were.

A cheaper, better-bounded alternative to measure in the same spike: lower the
large-tier thresholds in `explorer-tiers.js` so fewer documents ever build
20,000 row elements at all. That is a constant change, not an architecture.

---

## Measurement

Per `large_content_freeze_analysis.md`'s cross-cutting item, instrument before
and after with `performance.mark()`/`measure()` — aggregates only (durations,
row and byte counts), never paths or contents, matching the shape-only rule:

- `cacheVisibleGroupView()` split into capture (hash), presentation note,
  detach.
- `restoreCachedGroupView()` split into attach, scroll restore, observer
  restore, first paint after.
- `explorerOverviewGeometry()` — cache hit vs. measured pass, and row count.
- `ensureExplorerPreviewLoaded()` — fetch, paint, Mermaid, and the delay
  between the first restore attempt and the content being tall enough to accept
  it. That last number is what proves Stage 1 worked.

Acceptance targets: a group switch with one 10 MiB file open costs no single UI
task over ~150–200 ms; the Preview panel returns to within a pixel of its
captured offset; the Source view's measured content width is identical in
Source, edit and large-tier modes.

---

## Documentation obligations

- `CHANGELOG.md` — Stages 1 and 2 fix behaviour that regressed and are
  user-visible; both belong there.
- `CLAUDE.md` + `AGENTS.md` — any rule that survives implementation goes into
  the **Regression Guardrails** lists as a rule, never as a pointer here. The
  three candidates, phrased as rules:
  - *A panel's scroll offset is applied when the panel is shown and its content
    can hold it, not when some other panel's render finishes.* (Stage 1)
  - *A surface's chrome keeps its width when its content stands down* — a
    reserved track, not an `auto` one that collapses and re-wraps the text
    beside it. (Stage 2)
  - *Work belonging to a detached pane is suspended, and nothing measures a
    disconnected element* — a zero-size observation must never be cached as
    geometry. (Stage 3)
- If Stage 2 option B is taken, `CLAUDE.md`'s one-line description of
  `explorer-overview.js` needs the new parent element.
