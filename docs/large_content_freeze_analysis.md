# Large Source Files and Diff Views: Freeze Analysis and Proposal

**Status:** Analysis and implementation proposal
**Date:** 2026-08-17
**Scope:** Why opening or interacting with large source files and large diffs
freezes the GridVibe UI, verified against the current code, and a staged plan
to make these views feel dynamic instead of blocking.
**Companion document:** `docs/explorer_performance_research.md` covers the
broader repository-refresh, caching, and hardware-utilization picture. This
document narrows in on the *rendering freeze* the user actually feels, and
updates that doc's claims where the code has since moved.

## What "freeze" means here

The browser UI runs on one main thread. Every step between "file bytes arrive"
and "pixels on screen" — JSON decoding, syntax highlighting, HTML string
assembly, `innerHTML` parsing, DOM creation, and layout — currently runs
synchronously on that thread. While any of these runs, the whole app is dead:
tabs do not switch, terminals do not paint, clicks queue up. A freeze is not
one slow step; it is several multi-hundred-millisecond steps chained into one
uninterruptible task.

The server side is in good shape. Since commit `c21bf59` (ISSUE-2026-040),
every Git stream is bounded *while being read*
(`max_output_bytes=EXPLORER_GIT_DIFF_MAX_BYTES + 1` at `web/explorer.py:2353`),
file previews read at most 10 MiB + 1 byte (`web/explorer.py:394-422`), and
both local and remote reads are chunked with process-group kills on overrun.
The freezes are overwhelmingly a **browser main-thread** problem.

## Verified freeze causes, in order of severity

### 1. One DOM row per line, rebuilt in full, every time

`renderExplorerSourceLines()` (`web/static/js/explorer-viewer.js:5404`) walks
every line record, pushes one
`<div class="explorer-source-line">` (plus gutter span and code element) per
line into an array, joins it into a single HTML string
(`explorer-viewer.js:5450-5466`), and `renderExplorerSource()` assigns the
whole string with `code.innerHTML = ...` (`explorer-viewer.js:5607`).

Scale math for the worst allowed case (10 MiB preview cap):

- ~200k lines at 50 chars/line → 200k row divs + ~400k child elements, plus
  one `<span>` per highlight token when highlighting is active — easily
  1M+ DOM nodes for one pane.
- The multi-MB HTML string alone takes hundreds of ms to parse; layout and
  style resolution over 200k rows takes seconds.

The 2 MiB "plain preview" escape hatch (`EXPLORER_PLAIN_PREVIEW_THRESHOLD`,
`explorer-viewer.js:48`) only disables *highlighting*. Per-line rows are still
built for every line, so a 10 MiB minified-free log file freezes almost as
hard as a highlighted one.

There is no chunking, no virtualization, no `content-visibility`, and no
`requestAnimationFrame` scheduling of the render itself anywhere in this path.

### 2. Whole-document synchronous highlighting

`explorerHighlightDocumentLines()` (`explorer-viewer.js:706`) calls
`window.hljs.highlight(source, ...)` on the **entire file** in one call
(`explorer-viewer.js:723`), then re-parses the highlighted markup through a
`template.innerHTML` round-trip into a per-line run map. For a file just under
the 2 MiB threshold this is a several-hundred-ms to multi-second synchronous
block, and it runs before a single row exists.

The one mitigation that has landed since the earlier research doc: highlight
runs are now cached per pane keyed by content+language
(`explorerHighlightDocumentLinesCached()`, `explorer-viewer.js:790-800`), so
search keystrokes and folds reuse the token map. The DOM rebuild itself is not
cached and still happens in full.

### 3. Full re-render on nearly every interaction

The per-line DOM is rebuilt from scratch on:

- every search keystroke (debounced 160 ms, and the range *scan* is
  cooperative — `explorerFindRangesAsync()`, 64 KiB chunks yielding every
  8 ms — but the subsequent `renderExplorerSource()` is a full synchronous
  rebuild, `explorer-viewer.js:6178`);
- folding/unfolding a Markdown section (`explorer-viewer.js:5562`);
- entering *and* exiting edit mode (`explorer-editor.js:433` and again via
  `applyExplorerSearch` at `explorer-editor.js:452` — two full rebuilds);
- every in-place file refresh and cached-view restore.

While typing in the in-place editor, the edit underlay additionally rebuilds
its entire highlighted draft via `innerHTML` once per animation frame
(`explorer-edit-overlay.js:182,195-220`).

### 4. Diff view: expensive defaults, no size tiers, layout thrash on wrap

`explorerDiff2HtmlConfig()` (`explorer-viewer.js:3642-3661`) applies the same
configuration to every diff regardless of size:

```js
outputFormat: 'side-by-side',
matching: 'words',
diffStyle: 'char',
highlight: true,
matchingMaxComparisons: 1500,
maxLineLengthHighlight: 10000
```

No `diffMaxChanges`, no parse-time `diffMaxLineLength`, no tiered degradation.
Diff2Html parsing, word matching, character-level intraline diffing,
Highlight.js, and `ui.draw()` all run synchronously in
`renderExplorerDiffWithDiff2Html()` (`explorer-viewer.js:4334-4351`) for up to
256 KiB / 4,000 lines of diff. Diff2Html's own documentation recommends
`matching: "none"` for large inputs.

When line wrapping is on, `synchroniseExplorerDiffWrappedRows()`
(`explorer-viewer.js:3683-3709`) clears every row's `style.height`, then loops
over all rows calling `row.getBoundingClientRect().height`
(`explorer-viewer.js:3701`), then writes the matched heights — a textbook
write-read-write layout thrash across thousands of rows. It re-runs on every
resize (via `ResizeObserver`) and after every diff-view search repaint
(`explorer-viewer.js:6218`).

The handwritten fallback renderer (`renderExplorerSideBySideDiff`) has the
same one-HTML-string-all-rows shape, so a Diff2Html failure does not save the
main thread either.

### 5. Eager Markdown pipeline, on both sides of the wire

Server side, `get_explorer_file_payload()` renders and Bleach-sanitizes the
full Markdown preview on every file GET (`web/explorer.py:3439`), even when
the user never leaves Source view, with no caching — every refresh re-renders
and re-sanitizes.

Client side, on every file open and every in-place refresh, the preview HTML
is inserted, every code block is re-highlighted, and every Mermaid diagram is
rendered (sequential awaited `mermaid.render` calls,
`explorer-viewer.js:5653`) — again regardless of whether the Preview tab is
visible (`explorer-viewer.js:7638-7647, 7752-7760`). A large README with a
dozen diagrams can freeze the pane for seconds on open.

### 6. Change marks fetch a full HEAD diff on every source open

`loadExplorerChangeMarks()` (`explorer-overview.js:135`) fires
`GET .../git/diff?path=...&mode=head` on every file open and refresh
(`explorer-viewer.js:7630, 7751`) solely to compute gutter marks and change
peeks — even if the user never opens the Diff panel. The response then goes
through the full diff parse. After every source rebuild,
`applyExplorerChangeMarks()` re-walks all rows with `querySelectorAll`
(`explorer-overview.js:208`), compounding cause #1.

### 7. No cancellation of superseded work

File loads, diff loads, and change-mark loads carry no `AbortController`;
stale responses are discarded by identity checks after arrival. Rapidly
switching files therefore lets several multi-MB fetches, parses, and renders
run to completion for content nobody is looking at anymore. (Repo-wide
search, tree find, and download pre-flight already use `AbortController` —
the pattern exists, it just hasn't reached the file/diff paths.)

### 8. Memory multiplication amplifies every freeze

Per open file, a pane simultaneously holds: the raw content string, the
server-rendered preview HTML string, the diff string, the highlight cache
(which retains a *second* reference to the full content as its key, plus the
per-line run map), the line-record array, the assembled HTML string, and the
resulting DOM. Large allocations and GC pressure from these copies turn
borderline renders into visible hitches.

## What is already fine (don't "fix" these)

- Server-side Git reads are bounded at the read with process-group kills
  (`web/explorer.py:1052-1112`, `_bounded_git_diff` at `web/explorer.py:2340`).
- File preview reads are bounded at 10 MiB + 1, local and SFTP alike.
- The file-state poll is one `stat`; the git-state poll compares a semantic
  revision and repaints nothing when unchanged.
- The in-file search scan is chunked, yielding, capped at 1,000 matches, and
  token-cancellable.
- Highlight tokenization is cached per pane per content revision.

## Proposed solutions

Ordered by effort-to-impact. Phases 0–2 are days of work each and remove the
majority of felt freezes; phases 3–4 are the durable architecture.

### Phase 0 — Stop the bleeding (protective degradation)

1. **True large-file mode.** Replace the highlight-only plain threshold with a
   presentation tier: above a byte *and* line threshold, render a single
   `<pre>` (or a few bounded chunks) instead of per-line rows; disable
   decorations, folding, occurrence tint, and wrapping; keep download and the
   existing chunked text search; show a notice listing what was turned off.
   This one change caps DOM nodes for pathological files at O(1).
2. **Diff size tiers.** Compute tiers from diff bytes/lines before rendering:
   - small: current config unchanged;
   - medium: `matching: 'none'`, `highlight: false`, explicit
     `diffMaxChanges`/`diffMaxLineLength`, no wrapped-row synchronization;
   - large/truncated: plain unified-diff `<pre>` with the truncation banner
     and a "load next section" affordance instead of 4,000 mounted rows.
3. **Kill the layout thrash.** In `synchroniseExplorerDiffWrappedRows()`, skip
   entirely above a row-count ceiling; below it, measure in one pass and write
   in a second pass (or use CSS-only equal-height rows for wrapped diffs).
4. **Lazy Markdown.** Server: render `preview_html` only when requested (a
   `?preview=1` parameter or a separate route), keyed by file revision.
   Client: insert/highlight/render Mermaid only when the Preview tab is first
   selected, and render Mermaid diagrams lazily as they scroll into view.
5. **AbortController everywhere.** Extend the existing pattern to file, diff,
   and change-mark loads; abort the previous request at each new open.
6. **Zero-context change marks.** Fetch gutter marks via
   `git diff --unified=0` (hunk coordinates only, kilobytes instead of up to
   256 KiB) and load full hunk text only when a peek or the Diff panel opens.

**Prerequisite guardrail:** per `AGENTS.md`, the first functional change to
the Diff view must first extract `explorer-diff.js` from `explorer-viewer.js`
as a verified pure move — `test_explorer_source_frame.py` and
`test_explorer_overview.py` passing untouched. The diff tiers above are that
change, so the extraction lands first.

### Phase 1 — Make re-renders incremental

7. **Decouple decoration from DOM rebuild.** Search marks, change marks, and
   fold toggles should repaint classes on existing rows instead of re-running
   `renderExplorerSource()`. The highlight cache already makes the token map
   reusable; what is missing is a row-update path that skips `innerHTML`
   when the line content itself has not changed.
8. **Fix the double rebuild in the editor** (enter + exit paths both trigger
   two full renders) and cap the edit underlay's whole-draft `innerHTML`
   rebuild by size, falling back to plain underlay for large drafts.
9. **Chunk the row build.** Even before virtualization, build rows in
   `requestAnimationFrame` slices (e.g. 2,000 rows per frame) with a render
   token so a newer render supersedes an in-flight one. This converts one
   3-second freeze into progressive paint — the file "fills in" and the UI
   stays alive.

### Phase 2 — Move parsing off the main thread

10. **Web Worker pool for Highlight.js.** hljs explicitly documents worker
    usage for large blocks. A shared pool capped at
    `min(max(navigator.hardwareConcurrency - 1, 1), 4)` can tokenize the whole
    document off-thread; the main thread receives the per-line run map and
    only builds DOM. Workers cannot touch the DOM, so this complements — never
    replaces — phase 1/3.
11. **Worker-side diff parsing** for large diffs (parse unified text into a
    compact model in the worker; render from the model).

### Phase 3 — Viewport virtualization (the durable fix)

12. Render only the visible line window plus overscan. GridVibe's variable row
    heights (wrapping, folding, editing) make a custom virtualizer genuinely
    hard; evaluate **CodeMirror 6** (virtualized viewport, decorations,
    gutters, search, folding, read-only mode, and a merge view with bounded
    diff detail) against a small custom fixed-height virtualizer in a proof of
    concept first. Any adoption must keep vendored/self-hosted assets,
    GridVibe tokens, presentation persistence, and behavioral test parity —
    see the companion research doc for the full checklist.

### Cross-cutting: measurement first

Before and after each phase, instrument with `performance.mark()/measure()`
around fetch/decode, tokenization, HTML build, DOM commit, `draw()`,
row-sync, and Markdown/Mermaid; use `PerformanceObserver` long-task entries.
Log only aggregates (durations, byte/line/node counts — never paths or
contents). Acceptance target: no single UI task over ~150–200 ms for a 10 MiB
source file or a maximum-size diff, and a loading/degraded presentation
visible before any unavoidable work begins.

## Freeze-to-fix map

| Freeze you feel | Root cause | Fix |
|---|---|---|
| Opening a 5–10 MiB file hangs everything for seconds | #1, #2, #8 | Phase 0.1, 0.5; Phase 1.9; Phase 2.10 |
| Typing in find stutters on a large file | #3, #1 | Phase 1.7; Phase 0.1 |
| Opening a big diff hangs; worse with wrap on | #4 | Phase 0.2, 0.3 (+ `explorer-diff.js` extraction first) |
| Opening a big Markdown file hangs | #5 | Phase 0.4 |
| Every file open re-parses a HEAD diff in the background | #6 | Phase 0.6 |
| Rapidly switching files stays sluggish | #7, #3 | Phase 0.5, 1.9 |
| Editing a large file lags per keystroke | #3 (underlay) | Phase 1.8 |

## Recommendation

Do Phase 0 first — it is small, mostly deletion of work, and removes the
worst freezes without new architecture. Land the `explorer-diff.js` pure-move
extraction as its first step so the diff tiers comply with the standing
guardrail. Phase 1 (chunked, supersedeable rendering) is what makes the app
*feel* dynamic even before virtualization arrives; phases 2–3 then remove the
ceiling entirely. Measure at each step so regressions show up in numbers, not
in user reports.
