# Large Source Files and Diff Views: Freeze Analysis and Proposal

**Status:** Analysis and implementation proposal — **verified against the working
tree on 2026-08-17**
**Date:** 2026-08-17 (verification pass same day)
**Scope:** Why opening or interacting with large source files and large diffs
freezes the GridVibe UI, verified against the current code, and a staged plan
to make these views feel dynamic instead of blocking.
**Companion document:** `docs/explorer_performance_research.md` covers the
broader repository-refresh, caching, and hardware-utilization picture. This
document narrows in on the *rendering freeze* the user actually feels, and
updates that doc's claims where the code has since moved.

> **This document is an analysis, not a contract.** Per `CLAUDE.md`, audits and
> analyses are never cited from code, docstrings, tests, or the maintained
> documents. If a rule here survives implementation, write the rule itself into
> the **Regression Guardrails** lists in `CLAUDE.md` *and* `AGENTS.md`; the
> document is a point-in-time record of how the code stood, nothing more.

## Verification summary

Every claim in the original draft was re-checked against the files it names.

| # | Original claim | Verdict |
|---|---|---|
| 1 | One DOM row per line, rebuilt in full every time | **Confirmed** |
| 2 | Whole-document synchronous highlighting | **Confirmed** |
| 3 | Full re-render on nearly every interaction | **Confirmed, and understated** — see finding 9 |
| 4 | Diff: expensive defaults, no tiers, layout thrash on wrap | **Confirmed**, thrash is worse than described |
| 5 | Eager Markdown on both sides of the wire | **Confirmed** |
| 6 | Change marks fetch a full HEAD diff on every source open | **Confirmed**, with a caching nuance the draft missed |
| 7 | No cancellation of superseded work | **Confirmed** |
| 8 | Memory multiplication | **Partly wrong** — the highlight cache holds a *reference*, not a copy |
| — | Line numbers throughout | **Stale by ~60 lines** in `explorer-viewer.js`; corrected below |
| — | Phase 0.6 "`--unified=0` gives hunk coordinates only" | **Wrong** — corrected below (the idea still holds) |
| — | Phase 0.2 `diffMaxChanges` / `diffMaxLineLength` | **Available but misread** — they refuse, they do not degrade |
| — | Phase 1.8 "cap the edit underlay by size" | **Already implemented** — the real defect is different |

The draft's line references were written against a tree without the
`explorer-git-search.js` work, so everything below `explorer-viewer.js:~3600`
had drifted. `explorer-viewer.js` is **8,196 lines** today, not the ~7.7k both
`CLAUDE.md` and `AGENTS.md` still record.

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
(`max_output_bytes=EXPLORER_GIT_DIFF_MAX_BYTES + 1`, `web/explorer.py:2353`),
file previews read at most `EXPLORER_FILE_PREVIEW_MAX_BYTES` = 10 MiB
(`web/explorer.py:63`, `read_explorer_file_preview()` at `web/explorer.py:393`),
and both local and remote reads are chunked (`_GIT_STREAM_CHUNK_BYTES`, 64 KiB)
with process-group kills on overrun. The freezes are overwhelmingly a
**browser main-thread** problem.

## Verified freeze causes, in order of severity

### 1. One DOM row per line, rebuilt in full, every time — CONFIRMED

`renderExplorerSourceLines()` (`explorer-viewer.js:5466`) walks every line
record, pushes one `<div class="explorer-source-line">` (plus gutter span and
`<code>`) per line into an array (`explorer-viewer.js:5512-5517`), joins it into
a single HTML string (`explorer-viewer.js:5528`), and `renderExplorerSource()`
assigns the whole string with `code.innerHTML = ...`
(`explorer-viewer.js:5669`).

Scale math for the worst allowed case (10 MiB preview cap):

- ~200k lines at 50 chars/line → 200k row divs + ~400k child elements, plus
  one `<span>` per highlight token when highlighting is active — easily
  1M+ DOM nodes for one pane.
- The multi-MB HTML string alone takes hundreds of ms to parse; layout and
  style resolution over 200k rows takes seconds.

The 2 MiB "plain preview" escape hatch (`EXPLORER_PLAIN_PREVIEW_THRESHOLD`,
`explorer-viewer.js:48`) only disables *highlighting*: `renderExplorerSource()`
passes `''` as the language when `pane._explorerFilePlain` is set
(`explorer-viewer.js:5665`), which makes the token map `null` and sends every
line through the fallback lexer instead. Per-line rows are still built for every
line, so a 10 MiB log file freezes almost as hard as a highlighted one.

There is no chunking, no virtualization, no `content-visibility` (verified: the
property appears nowhere in `web/static/css/`), and no `requestAnimationFrame`
scheduling of the render itself anywhere in this path.

### 2. Whole-document synchronous highlighting — CONFIRMED

`explorerHighlightDocumentLines()` (`explorer-viewer.js:706`) calls
`window.hljs.highlight(source, ...)` on the **entire file** in one call
(`explorer-viewer.js:723`), then re-parses the highlighted markup through a
`template.innerHTML` round-trip (`explorer-viewer.js:729-730`) and walks the
resulting tree into a per-line run map. For a file just under the 2 MiB
threshold this is a several-hundred-ms to multi-second synchronous block, and
it runs before a single row exists.

The one mitigation that has landed since the earlier research doc: highlight
runs are cached per pane keyed by content + normalized language
(`explorerHighlightDocumentLinesCached()`, `explorer-viewer.js:790-800`), so
search keystrokes and folds reuse the token map — a cached `null` counts as a
hit, so an unsupported or oversized file does not re-attempt every render. The
DOM rebuild itself is not cached and still happens in full.

### 3. Full re-render on nearly every interaction — CONFIRMED

The per-line DOM is rebuilt from scratch on:

- every search keystroke (debounced `EXPLORER_SEARCH_DEBOUNCE_MS` = 160 ms, and
  the range *scan* is cooperative — `explorerFindRangesAsync()`,
  `explorer-viewer.js:429`, 64 KiB strides yielding every 8 ms, capped at 1,000
  matches — but the subsequent `renderExplorerSource()` is a full synchronous
  rebuild, `explorer-viewer.js:6240`);
- folding/unfolding a Markdown section (`explorer-viewer.js:5622-5625`);
- entering *and* exiting edit mode (`explorer-editor.js:433`, then again via
  `applyExplorerSearch` at `explorer-editor.js:452` — two full rebuilds);
- every in-place file refresh and cached-view restore.

While typing in the in-place editor, the edit underlay rebuilds its entire
draft once per animation frame (`paintExplorerEditUnderlay`,
`explorer-edit-overlay.js:169-188`; scheduled by `refreshExplorerEditOverlay`,
`explorer-edit-overlay.js:195-220`) — see finding 10 for what that actually
costs.

### 4. Diff view: expensive defaults, no size tiers, layout thrash on wrap — CONFIRMED

`explorerDiff2HtmlConfig()` (`explorer-viewer.js:3704-3723`) applies the same
configuration to every diff regardless of size:

```js
outputFormat: 'side-by-side',
matching: 'words',
diffStyle: 'char',
highlight: true,
matchingMaxComparisons: 1500,
maxLineLengthHighlight: 10000
```

No `diffMaxChanges`, no `diffMaxLineLength`, no tiered degradation. Diff2Html
parsing, word matching, character-level intraline diffing, Highlight.js, and
`ui.draw()` all run synchronously in `renderExplorerDiffWithDiff2Html()`
(`explorer-viewer.js:4396-4421`) for up to 256 KiB / 4,000 lines of diff
(`EXPLORER_GIT_DIFF_MAX_BYTES`, `EXPLORER_GIT_DIFF_MAX_LINES`,
`web/explorer.py:64-65`).

**Correction to the draft's thrash description.**
`synchroniseExplorerDiffWrappedRows()` (`explorer-viewer.js:3745-3771`) is worse
than "write-read-write". It clears every row's `style.height` up front
(`3750-3752`), then in a single loop **reads** `getBoundingClientRect().height`
for a row pair (`3763`) and **writes** `row.style.height` for that same pair
(`3767`) before moving to the next — so every iteration invalidates the layout
the next iteration reads, forcing a synchronous reflow *per row pair*. It
re-runs on every resize (via `ResizeObserver`, `observeExplorerDiffLayout`) and
after every diff-view search repaint (`explorer-viewer.js:6280`).

The handwritten fallback renderer (`renderExplorerSideBySideDiff`,
`explorer-viewer.js:4487`) has the same one-HTML-string-all-rows shape, so a
Diff2Html failure does not save the main thread either.

**What the draft missed:** the Diff view is not read-only chrome. Per-line and
per-block **undo** buttons are wired onto the rendered rows
(`wireExplorerDiffUndoControls`, `explorer-viewer.js:4173-4230`), against
Diff2Html's `.d2h-diff-tbody > tr` markup when it rendered and against
`.explorer-diff-row` when the fallback did. Any tier that stops producing rows
also stops producing undo. That is a capability loss, not a presentation one,
and it drives the corrected Phase 0.2 below.

### 5. Eager Markdown pipeline, on both sides of the wire — CONFIRMED

Server side, `get_explorer_file_payload()` renders and Bleach-sanitizes the full
Markdown preview on every file GET (`web/explorer.py:3439`, calling
`_render_markdown_preview()` at `web/explorer.py:797`), even when the user never
leaves Source view, with no caching — every refresh re-renders and re-sanitizes.
Scope note: `_is_markdown_file()` gates it, so non-Markdown files pay nothing
here.

Client side, on every file open and every in-place refresh, the preview HTML is
inserted, every code block is re-highlighted, and every Mermaid diagram is
rendered in a sequential `await` loop (`renderExplorerMermaid`,
`explorer-viewer.js:5715-5757`) — regardless of whether the Preview tab is
visible (`explorer-viewer.js:7711-7720` on open, `7825-7833` on in-place
refresh; the panel merely carries `hidden`). A large README with a dozen
diagrams can freeze the pane for seconds on open.

### 6. Change marks fetch a full HEAD diff on every source open — CONFIRMED (with a nuance)

`loadExplorerChangeMarks()` (`explorer-overview.js:135`) fires
`GET .../git/diff?path=...&mode=head` on every file open
(`explorer-viewer.js:7703`) and every in-place refresh, the latter with
`{ force: true }` (`explorer-viewer.js:7824`), solely to compute gutter marks
and change peeks — even if the user never opens the Diff panel. The response
then goes through the full diff parse (`explorerDiffChangeBlocks`,
`explorer-viewer.js:3929`). After every source rebuild,
`applyExplorerChangeMarks()` (`explorer-overview.js:208`) re-walks the rows,
compounding cause #1.

Two mitigations the draft did not credit:

- The fetch is **skipped entirely** when the Diff panel already cached exactly
  this path's HEAD diff (`explorer-overview.js:158-163`), so opening the Diff
  panel first makes the marks free.
- It is gated on being in a Git worktree, outside a commit diff, and with no
  editor open (`explorerChangeMarksEligible`, `explorer-overview.js:65-74`), so
  non-repository panes pay nothing.

### 7. No cancellation of superseded work — CONFIRMED

File loads, diff loads, and change-mark loads carry no `AbortController`; stale
responses are discarded by identity checks after arrival
(`explorer-overview.js:187-195` is the pattern). Rapidly switching files
therefore lets several multi-MB fetches, parses, and renders run to completion
for content nobody is looking at anymore. The pattern exists elsewhere —
verified: `explorer-search.js:132`, `explorer-tree-search.js:136`, and the
download pre-flight at `explorer-viewer.js:6994` are the only three
`AbortController` uses in the whole frontend.

### 8. Memory multiplication — PARTLY WRONG

Per open file a pane does hold the raw content string, the server-rendered
preview HTML string, the diff string, the per-line run map, the assembled HTML
string, and the resulting DOM. But the draft's claim that the highlight cache
"retains a *second* reference to the full content as its key" is not a
multiplier: `pane._explorerHighlightCache = { content, ... }`
(`explorer-viewer.js:797`) stores a JavaScript **reference** to the same string
object, not a copy, and it is overwritten on the next miss. The real
multipliers are the run map (one small object per token) and the assembled HTML
string, which for a 2 MiB file is several MB of transient string before
`innerHTML` even parses it. Keep the finding, drop the string-duplication part.

### 9. NEW — every file open and every in-place refresh builds the rows twice

Not in the draft, and cheap to fix. `renderExplorerFile()` renders the source
rows at `explorer-viewer.js:7699`, then calls `applyExplorerSearch(index)` at
`7755`; with no active query that lands in the final `else` branch, which calls
`renderExplorerSource(index)` again (`explorer-viewer.js:6293`).
`updateExplorerFileInPlace()` does the same thing — `7820`, then `7869`. So the
worst single freeze in the app — opening a large file — currently pays for
itself twice.

It is not a one-line deletion. The second render exists because the search
repaint has to land on the rows that are finally standing, and both change marks
(`applyExplorerChangeMarks`) and the scroll restore
(`restoreExplorerFileScroll`, `explorer-viewer.js:7761`) are sequenced around
it. The fix is to make `renderExplorerSource()` idempotent under a render token
(skip when content, language, search ranges, and fold set are all unchanged
since the last commit), which is the same machinery Phase 1.7 needs anyway.

### 10. NEW — the edit underlay re-tokenizes the whole draft on every frame

The draft says the underlay "rebuilds its entire highlighted draft via
`innerHTML` once per animation frame". It is more than that.
`paintExplorerEditUnderlay()` calls `explorerEditUnderlayHtml(draft, language)`
with the `runs` argument **omitted** (`explorer-edit-overlay.js:182`), and
`renderExplorerSourceLines()` treats `undefined` as "tokenize this yourself"
(`explorer-viewer.js:5487-5489`). So each animation frame while typing runs a
full `hljs.highlight()` over the whole draft, the `template.innerHTML` round
trip, the per-line run map build, the full row-string assembly, *and* the
`innerHTML` commit. This is deliberate and documented
(`explorer-edit-overlay.js:73-95`: caching a moving draft would evict the
viewer's entry), but it makes the per-keystroke cost O(document), not O(edit).

## What is already fine (don't "fix" these)

- Server-side Git reads are bounded at the read with process-group kills
  (`EXPLORER_GIT_MAX_OUTPUT_BYTES` / `EXPLORER_GIT_MAX_STDERR_BYTES`,
  `web/explorer.py:1062-1065`; `_bounded_git_diff` at `web/explorer.py:2340`).
- File preview reads are bounded at 10 MiB, local and SFTP alike.
- The file-state poll is one `stat`; the git-state poll compares a semantic
  revision and repaints nothing when unchanged.
- The in-file search *scan* is chunked, yielding, capped at 1,000 matches, and
  token-cancellable. Only its repaint is the problem.
- Highlight tokenization is cached per pane per content revision, `null`
  included.
- **The edit underlay already has a size cap** — `explorerEditOverlayViable()`
  (`explorer-edit-overlay.js:115-124`) stands the overlay down above
  `EXPLORER_PLAIN_PREVIEW_THRESHOLD`, silently, leaving a bare textarea. The
  draft's Phase 1.8 proposed adding this; it exists.

## Proposed solutions

Ordered by effort-to-impact. Phases 0–2 are days of work each and remove the
majority of felt freezes; phases 3–4 are the durable architecture.

**Standing constraint on all of it — file placement.** Guardrail 6 (both
`CLAUDE.md` and `AGENTS.md`) names `explorer-viewer.js` as the regrowth risk,
and it is now 8,196 lines. Nothing proposed here may grow it. Every item below
lands either in the extracted `explorer-diff.js`, in `explorer-overview.js`, or
in a new DOM-free policy module with a Node-executed test, paired with a
paint-only adapter — the `explorer-git-active.js` / `explorer-git-menu.js` /
`explorer-git-search.js` pattern.

### Phase 0 — Stop the bleeding (protective degradation)

**0.0 — `explorer-diff.js` extraction (prerequisite, no behaviour change).**
`CLAUDE.md`/`AGENTS.md` guardrail 6: *"the next change to the Diff view extracts
`explorer-diff.js`. Same pure-move standard."* Items 0.2 and 0.3 are that
change, so the extraction lands first: every moved line byte-identical,
`explorer-viewer.js` a pure deletion, `test_explorer_source_frame.py` and
`test_explorer_overview.py` passing untouched. Verified as still-current
guardrail text at `CLAUDE.md:309` / `AGENTS.md:107`.

**0.1 — True large-file mode.** Replace the highlight-only plain threshold with
a presentation tier: above a byte *and* line threshold, render bounded plain
chunks instead of per-line rows; disable decorations, folding, occurrence tint,
change marks, and the overview ruler; keep download and edit (the editor already
degrades to a bare textarea above 2 MiB); show an in-pane notice listing what
was turned off. Caps DOM nodes for pathological files at O(1).

- *Guardrails:* the notice is **not** a launcher banner — `showGridVibeNotice`
  is launcher-only and is for events, not states. Copy the existing in-pane
  `role="status"` pattern (`explorerDiffTruncationBannerHtml`,
  `explorer-viewer.js:3725-3732`), styled from `tokens.css` variables.
  New module + Node test (guardrail 6); tier thresholds are constants in the
  module, not `config.json` keys nothing reads (guardrail 5).
- *Open design question, flagged not hidden:* find-in-file. The offset-range
  machinery assumes per-line rows. The Preview and Diff views already search a
  plain element through `markExplorerSearchInElement()`, so that path can be
  reused — but it walks the whole subtree unbounded, which is its own freeze on
  a 10 MiB buffer. Decide before building: either paint only a bounded window of
  matches, or state plainly in the notice that find is unavailable in this tier.
  Do not ship a find that reintroduces the freeze the tier exists to remove.

**0.2 — Diff size tiers (corrected).** The draft proposed `diffMaxChanges` /
`diffMaxLineLength` as a degradation lever. Both keys exist in the pinned
diff2html 3.4.48 bundle — but reading the bundle shows they are a **refusal**
lever: exceeding either sets `isTooBig = true`, zeroes the line counts, empties
`blocks`, and emits `diffTooBigMessage` as the file header. The user gets
"Diff too big to be displayed" and no diff at all — and GridVibe's fallback
guard (`explorer-viewer.js:4409`) still finds a `.d2h-file-wrapper`, so it will
*not* fall through to the handwritten renderer. Do not use them as a tier.

Use GridVibe's own size check, before handing anything to diff2html:

- **small** (current bytes/line counts): config unchanged.
- **medium:** `matching: 'none'`, `diffStyle: 'line'`, `highlight: false`.
  These degrade gracefully inside diff2html — rows, line numbers, side-by-side
  and the undo buttons all survive; only intraline emphasis and syntax colour go.
- **large:** the **handwritten** `renderExplorerSideBySideDiff()` with no
  language passed, so `highlightExplorerCode()` reduces to escaping. This keeps
  `.explorer-diff-row` markup, which means it keeps per-line and per-block undo
  (`explorer-viewer.js:4219+`). Prefer this over the draft's plain `<pre>`,
  which silently removes a mutation affordance from exactly the diffs where it
  is most wanted.
- **at the truncation ceiling:** the existing 256 KiB / 4,000-line banner, plus
  a "load next section" affordance if pagination is ever added. Pagination is a
  new backend read contract — out of Phase 0, and it must be flagged in
  `CLAUDE.md`'s reads list if it lands.

**0.3 — Kill the layout thrash.** Split `synchroniseExplorerDiffWrappedRows()`
into two passes: read every row's height into an array, then write. That is a
pure win with no visible change and should land on its own. Add a row-count
ceiling **only if** two-pass is still too slow — a skipped sync means the two
sides of a wrapped diff visibly stop lining up, which is a real regression, not
a free optimization. The CSS-only alternative (equal-height row pairs via grid
or `align-items: stretch` on a row wrapper) removes the JS pass entirely and is
worth a spike first.

**0.4 — Lazy Markdown.** Server: render `preview_html` only when asked for
(`?preview=1` on the existing file GET, or a separate bounded read route).
Client: insert/highlight/render Mermaid only when the Preview tab is first
selected, and render diagrams lazily as they scroll into view
(`IntersectionObserver`).

- *Hard constraint the draft missed:* `preview_type` is currently derived from
  `preview_html is not None` (`web/explorer.py:3460`), and the client derives
  the whole Preview tab's existence from
  `data.preview_type === 'markdown' && typeof data.preview_html === 'string'`
  (`explorer-viewer.js:7781`). Worse, `get_explorer_file_payload()` is shared by
  the GET route **and by a successful save** (its own docstring), and
  `updateExplorerFileInPlace()` bails to a full rebuild when the preview panel's
  presence flips (`explorer-viewer.js:7785-7787`). So: `preview_type` must
  become an independent, always-present field, and the client's `hasPreview`
  must read *it*, never the HTML string. Get this wrong and every save on a
  Markdown file triggers a full pane rebuild — which
  `tests/test_explorer_save_refresh.py` exists to catch.
- *Guardrails:* a lazily-fetched preview is still a **read**, so the read-only
  contract is unchanged — but `CLAUDE.md`'s enumerated reads list must gain the
  parameter or route (guardrail: keep the maintained docs true). Nothing new is
  persisted; `explorer-persistence.js` must not start storing preview HTML.

**0.5 — AbortController everywhere.** Extend the existing pattern
(`explorer-search.js:132`) to file, diff, and change-mark loads; abort the
previous request at each new open. Keep the post-arrival identity checks — abort
is an optimization, not a correctness mechanism.

- *Guardrail 9:* a deliberate abort must not reach `console.error`. Today
  `loadExplorerChangeMarks()`'s catch logs every failure
  (`explorer-overview.js:180`); an `AbortError` has to be recognised and
  swallowed, or every fast file switch writes a red line to the console.

**0.6 — Zero-context change marks (corrected rationale).** The draft claimed
`git diff --unified=0` yields "hunk coordinates only, kilobytes instead of up to
256 KiB", and proposed fetching hunk text later. That is wrong on both counts:
`-U0` drops *context* lines and keeps every `+`/`-` line, and the change peek
needs exactly those `+`/`-` lines (`explorerDiffChangeBlocks` collects them into
`expected`/`replacement`, `explorer-viewer.js:3957-3965`). No second fetch is
needed, and none should be built.

The change is still worth making, for a smaller and more defensible saving: up
to six context lines per hunk disappear from the payload and from the parse. It
is provably mark-identical — a block is flushed either by a context line
(`explorer-viewer.js:3967`) or by a hunk header (`3949`), and `-U0` converts the
former into the latter — so the gutter, the peeks, and the overview ruler render
exactly as they do now.

- *Constraints:* thread the context count through `_git_diff_args_for_mode()`
  (`web/explorer.py:2379`) as a server-controlled value, never a client string,
  keeping the existing mode allowlist and `_bounded_git_diff` ceiling. The `-U0`
  payload must **not** be written into `pane._explorerDiffContent` or stored
  under `explorerDiffCacheKey(path, '', 'head')` — the Diff panel reads that
  cache and would render a context-free diff. The reverse reuse (marks reading
  the panel's `-U3` diff, `explorer-overview.js:158-163`) stays valid and must
  be kept.

### Phase 1 — Make re-renders incremental

**1.7 — Decouple decoration from DOM rebuild.** Search marks, change marks, and
fold toggles should repaint classes on existing rows instead of re-running
`renderExplorerSource()`. The highlight cache already makes the token map
reusable; what is missing is a row-update path that skips `innerHTML` when the
line content itself has not changed. This is the keystone item — 1.8, 1.9 and
finding 9's fix all depend on it.

**1.8 — The editor's double rebuild (reframed).** The size cap the draft asked
for already exists (see "What is already fine"). Two real items remain:

- The exit-path double render (`explorer-editor.js:433` + `452`) is
  **load-bearing**, not an oversight: the comment at `explorer-editor.js:440-451`
  documents that the selection restore must land on the rows
  `applyExplorerSearch()` builds, not the earlier ones. It can only be collapsed
  *after* 1.7 makes the search repaint non-destructive. Deleting it first
  reintroduces the disappearing-selection bug it was written to fix.
- Finding 10's per-frame whole-draft re-tokenize is the bigger cost. Fix it by
  repainting only the rows the edit touched (1.7's machinery again), or — as a
  cheap interim — give the underlay its own threshold well below
  `EXPLORER_PLAIN_PREVIEW_THRESHOLD`, accepting that mid-size files lose the
  coloured underlay while editing.

**1.9 — Chunk the row build.** Build rows in `requestAnimationFrame` slices
(e.g. 2,000 rows per frame) with a render token so a newer render supersedes an
in-flight one. Converts one 3-second freeze into progressive paint.

- *This is the riskiest Phase 0–1 item.* Six things currently assume the rows
  exist the instant `renderExplorerSource()` returns: `applyExplorerChangeMarks`
  (`explorer-viewer.js:5682`), `wireExplorerMarkdownSectionControls` (`5676`),
  `scheduleExplorerOccurrenceHighlight` (`5679`), `restoreExplorerFileScroll`
  (`7761`), `restoreExplorerSourceSelection` (`explorer-editor.js:457`), and
  `syncExplorerGitActiveRows` (`explorer-viewer.js:7764`). Each has to move to a
  completion callback, and `tests/test_explorer_source_frame.py`,
  `test_explorer_overview.py`, `test_explorer_save_refresh.py` and
  `test_explorer_edit_find.py` will need updating — legitimately, since this
  changes behaviour, but it means the pure-move standard does not apply and the
  diffs must be reviewed as behavioural changes.

### Phase 2 — Move parsing off the main thread

**2.10 — Web Worker pool for Highlight.js.** hljs documents worker usage for
large blocks. A shared pool capped at
`min(max(navigator.hardwareConcurrency - 1, 1), 4)` can tokenize off-thread; the
main thread receives the run map and only builds DOM.

- *Feasible with the vendored build:* the worker `importScripts()` the
  same-origin `/static/vendor/highlight.min.js`. No CDN, no build step, guardrail
  3 intact.
- *Watch item:* the run map is a `Map` of arrays of `{className, text, start}`
  objects. Structured-cloning that for a 2 MiB file can cost as much as the
  tokenization saved. Transfer a flat, compact encoding (parallel typed arrays
  for offsets/lengths plus a class-name dictionary) and rebuild the map on the
  main thread, or the win evaporates.

**2.11 — Worker-side diff parsing.** Note the overlap with 0.2: diff2html's
`ui.draw()` and its highlighting need the DOM, so only the *parse* can move —
which in practice means rendering from GridVibe's own model instead of
diff2html, i.e. the "large" tier from 0.2. Treat 2.11 as "make the large tier's
parse asynchronous", not as a separate architecture.

### Phase 3 — Viewport virtualization (the durable fix)

**3.12** — Render only the visible line window plus overscan. GridVibe's
variable row heights (wrapping, folding, editing) make a custom virtualizer
genuinely hard.

Two obstacles the draft did not weigh before naming CodeMirror 6:

- **No build step.** Every vendored asset is a prebuilt single-file browser
  bundle downloaded from a pinned URL, and `web/static/vendor/README.md` records
  exactly that URL so the file can be replaced verbatim on upgrade. CM6 ships as
  a set of ESM packages that must be bundled. Adopting it means either
  introducing a bundler and a lockfile into a repo that has neither, or
  vendoring a self-built artifact whose provenance that README cannot express.
  Decide that question *before* the proof of concept, not after.
- **It is not a drop-in for one subsystem.** The row DOM CM6 would replace is
  the substrate for change marks and peeks, the overview ruler, the occurrence
  tint, the find, Markdown folds, and the edit overlay — six subsystems with
  their own Node-executed tests. A custom fixed-height virtualizer confined to
  the plain/large tier from 0.1 is a far smaller bet and should be measured
  against CM6 in the same spike.

Any adoption must keep vendored/self-hosted assets, `tokens.css` variables,
presentation persistence, and behavioral test parity — see the companion
research doc for the full checklist.

### Cross-cutting: measurement first

Before and after each phase, instrument with `performance.mark()/measure()`
around fetch/decode, tokenization, HTML build, DOM commit, `draw()`, row-sync,
and Markdown/Mermaid. Log only aggregates (durations, byte/line/node counts —
never paths or contents), matching the shape-only rule lifecycle logging already
follows. `PerformanceObserver` long-task entries are Chromium-only, so they
cover the WebView2 native window and Chrome/Edge browser mode but not Firefox;
`performance.measure` covers all three. Acceptance target: no single UI task
over ~150–200 ms for a 10 MiB source file or a maximum-size diff, and a
loading/degraded presentation visible before any unavoidable work begins.

## What the user sees, stage by stage

The question this document exists to answer honestly. "Nothing" is a valid and
frequent answer — those are the safest items.

| Stage | What the user sees differently |
|---|---|
| **0.0** extraction | **Nothing.** Pure move; if anything changes, it was not a pure move. |
| **0.1** large-file tier | Big win and a visible trade. A 5–10 MiB file **opens in well under a second instead of hanging**, but appears as plain uncoloured text with no line-number gutter, no fold chevrons, no change marks, no occurrence tint, and no ruler. A banner at the top of the pane names each thing that is off. Download and edit still work. Files below the threshold look and behave exactly as today. Find in this tier is the open question above — whatever is decided, the banner says so. |
| **0.2** diff tiers | Medium diffs lose **character-level intraline emphasis and syntax colour**; they keep side-by-side layout, line numbers, and per-line/per-block undo. Large diffs additionally lose diff2html's styling and render through GridVibe's own side-by-side markup — still with undo. Small diffs are untouched. A banner names the tier so the missing emphasis reads as a decision, not a bug. |
| **0.3** two-pass row sync | **Nothing** — wrapped diffs stay aligned, they just stop stuttering. (If a row-count ceiling is added later, above it the two sides of a wrapped diff visibly stop lining up. That is why the ceiling is conditional.) |
| **0.4** lazy Markdown | Opening a Markdown file in Source view gets **noticeably faster**, especially with Mermaid diagrams. The cost lands on the first click of the **Preview** tab, which now has a brief render pause where it used to be instant; diagrams below the fold appear as they scroll into view. Second and later visits to Preview are instant. |
| **0.5** AbortController | **Nothing directly visible** — clicking through files quickly stops backing up work, so the app feels less sluggish. No new errors, no new toasts. |
| **0.6** `-U0` change marks | **Nothing.** Identical gutter marks, identical peeks — provably so. A slightly faster open inside Git repositories. |
| **1.7** incremental decoration | Typing in Find on a large file goes from stuttering to smooth. A quieter side effect worth naming: because rows stop being destroyed and rebuilt, **text selection and the occurrence tint stop flickering/disappearing** on every keystroke and every fold. |
| **1.8** editor repaint | Typing in the in-place editor on a large file stops lagging behind the keyboard. If the interim threshold route is taken instead, mid-size files lose the coloured underlay while editing (bare textarea, exactly as >2 MiB files already do) and regain it on save. |
| **1.9** chunked rows | The most visible change in the plan: a large file **fills in progressively from the top** while the rest of the app stays responsive, instead of the window freezing and then showing everything at once. Scroll restore and the jump to a find match land a beat later than they do now. |
| **2.10** hljs worker | Large files open without blocking, but **syntax colours can arrive a frame or two after the text does** — a brief uncoloured flash on open. Small files should be unaffected (keep them on the synchronous path). |
| **2.11** worker diff parse | Large diffs stop blocking on parse. Presentation is whatever tier 0.2 chose; no additional visible change. |
| **3.12** virtualization | Potentially a large visual change if CodeMirror is adopted — its own gutter, scrollbar, selection and find behaviour, which would have to be themed back to GridVibe's tokens. A confined custom virtualizer would be invisible apart from scrolling that no longer stalls on huge files. |

## Freeze-to-fix map

| Freeze you feel | Root cause | Fix |
|---|---|---|
| Opening a 5–10 MiB file hangs everything for seconds | #1, #2, #9 | Phase 0.1, 0.5; Phase 1.9; Phase 2.10 |
| Typing in find stutters on a large file | #3, #1 | Phase 1.7; Phase 0.1 |
| Opening a big diff hangs; worse with wrap on | #4 | Phase 0.3, then 0.2 (both after the 0.0 extraction) |
| Opening a big Markdown file hangs | #5 | Phase 0.4 |
| Every file open re-parses a HEAD diff in the background | #6 | Phase 0.6, 0.5 |
| Rapidly switching files stays sluggish | #7, #3 | Phase 0.5, 1.9 |
| Editing a large file lags per keystroke | #10 | Phase 1.8 (needs 1.7) |
| Every open pays for two full renders | #9 | render token from Phase 1.7 |

## Risk and readiness

| Item | Guardrail exposure | Risk | Ready to implement? |
|---|---|---|---|
| 0.0 extraction | G6 (pure move) | Low | **Yes** — mechanical, tests must pass untouched |
| 0.1 large-file tier | G6 (new module), G7 (tokens), G8 (in-pane status, not the launcher banner) | Medium | **Yes, after deciding find behaviour** |
| 0.2 diff tiers | G6, G8 (undo affordance preserved) | Medium | **Yes, as corrected** — do not use `diffMaxChanges` as a tier |
| 0.3 two-pass sync | none | Low | **Yes** — smallest change, real win |
| 0.4 lazy Markdown | read-only contract (docs update), save-response shape | Medium | **Yes, with the `preview_type` decoupling** |
| 0.5 AbortController | G9 (no console noise on abort) | Low | **Yes** |
| 0.6 `-U0` marks | server arg allowlist, diff cache separation | Low | **Yes, as corrected** |
| 1.7 incremental decoration | G6 | Medium | Yes — design work, no contract change |
| 1.8 editor repaint | ordering contract in `explorer-editor.js` | Medium | Only after 1.7 |
| 1.9 chunked rows | six synchronous post-render assumptions; four test files | **High** | Only after 1.7, and reviewed as a behaviour change |
| 2.10 hljs worker | G3 (vendored, no CDN) | Medium | Yes — measure the clone cost first |
| 2.11 worker diff | overlaps 0.2 | Medium | Fold into 0.2's large tier |
| 3.12 virtualization | no build step; six dependent subsystems | **High** | Spike only |

Nothing in Phase 0 touches the read-only mutation contract, the concurrency
guardrails, the durable-state stores, or the security posture: every item is
either a client-side presentation change or a narrowing of an existing bounded
read. 0.4 is the only one that changes a response shape, and 0.6 the only one
that changes a Git invocation — both stay inside `_bounded_git_diff` and the
existing mode allowlist.

## Documentation obligations

Whatever ships from here has to leave the maintained documents true:

- `CLAUDE.md` + `AGENTS.md`: the reads list gains the preview parameter/route
  (0.4); guardrail 6's `explorer-viewer.js` line count and the
  "next change extracts `explorer-diff.js`" trigger both need rewriting once
  0.0 lands (and the recorded ~7.7k is already stale at 8,196).
- `README.md` / `CHANGELOG.md`: 0.1 and 0.2 are user-visible degradations with
  banners — they are exactly the kind of change the changelog exists for.
- `web/static/vendor/README.md`: only if Phase 2 or 3 adds an asset.
- Any rule that survives implementation goes into the **Regression Guardrails**
  lists, not into a reference to this document.

## Recommendation

Do **0.3 and 0.5 first** — they are the smallest, carry no visible trade-off,
and 0.3 is a two-line restructure of a loop that currently forces a reflow per
row pair. Then 0.0 (the extraction the guardrail requires), then 0.6, then the
two tiering items 0.1 and 0.2 with their banners. 0.4 last in Phase 0, because
it is the only item that changes a response shape and it has a save-path trap
in it.

Phase 1.7 is the keystone: chunked rendering (1.9), the editor's double rebuild
(1.8), and finding 9's duplicate open render all reduce to "make a repaint
cheaper than a rebuild". Phases 2–3 then remove the ceiling entirely. Measure at
each step so regressions show up in numbers, not in user reports.
