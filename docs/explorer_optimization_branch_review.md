# Code Review — `szua_gridvibe-opt` → `szua_gridvibe_wrk-opt`

**Status:** Review complete, 2026-08-20. **Findings 1–5 verified and fixed,
2026-08-20** — each re-confirmed against the code first, then fixed with a test
that was checked to fail without the fix ([What was fixed](#what-was-fixed)).
**Finding 6 is deferred by decision:** both its halves turn on landing an
interaction inside a build window, which cannot be settled from the code, so it
stays open until its [verification scenario](#verification-scenario) is run in a
browser. **Findings 7–15 verified and closed, 2026-08-20** — each re-confirmed
against the code first, then fixed, with a test checked to fail without the fix
([Findings 7–15](#findings-715)). Finding 15 asked for no
change and got none; one claim in Finding 7 was wrong and is corrected there.
**Range reviewed:** `origin/szua_gridvibe-opt` (`e9eb029`) … `szua_gridvibe_wrk-opt`
(`db8d6f8`) — 11 commits, linear (the `-opt` tip *is* the merge base).
**Scope:** Code only. `CHANGELOG.md`, `README.md`,
`docs/large_content_freeze_analysis.md` and
`docs/explorer_scroll_and_group_switch_regressions.md` were read for intent but
their wording is **not** reviewed here, by request. One exception is made for a
*source-file header comment* that is now untrue (Finding 11), because in this
repository those headers are the contract of record for a module.
**Companion documents:** `docs/explorer_performance_research.md` (the plan),
`docs/large_content_freeze_analysis.md` (what was built),
`docs/explorer_scroll_and_group_switch_regressions.md` (the three regressions
fixed in stages 1–3).

> **This document is an analysis, not a contract.** Per `CLAUDE.md`, audits and
> analyses are never cited from code, docstrings, tests, or the maintained
> documents. Any rule that survives a fix is written into the **Regression
> Guardrails** lists in `CLAUDE.md` *and* `AGENTS.md` — never as a reference
> back to this file.

---

## What was reviewed

```
 33 files changed, 10,059 insertions(+), 1,451 deletions(-)
```

Source under review (documentation and tests excluded):

| Area | Files | Net |
|---|---|---|
| Backend | `web/explorer.py`, `web/api.py` | +119 / −11 |
| New DOM-free policy modules | `explorer-tiers.js`, `explorer-repaint.js`, `explorer-scroll.js`, `explorer-worker-core.js`, `explorer-worker-client.js` | +1,429 |
| New adapters / entry points | `explorer-diff.js`, `explorer-scroll-adapter.js`, `explorer-worker.js` | +1,441 |
| Reworked | `explorer-viewer.js` (+1,609/−1,274), `explorer-edit-overlay.js`, `explorer-editor.js`, `explorer-overview.js`, `explorer-tabs.js`, `terminals.js` | +1,893 / −47 |
| Styling / page | `terminals.css`, `templates/terminals.html` | +145 / −7 |

Method: per-function structural diff of `explorer-viewer.js` against its
merge-base copy (291 → 302 top-level functions: 37 removed to `explorer-diff.js`,
48 added, 18 changed in place), then a read of every changed and new function,
plus targeted Node executions of the DOM-free policies to confirm four specific
behaviours.

Baseline health on the branch tip, as it stood when the review was written and
before any fix (see [Verification status](#verification-status) for the numbers
after the Findings 1-5 fixes):

```
python tests/run_tests.py   → Ran 1852 tests … OK (skipped=9)
python -m ruff check .      → All checks passed!
```

---

## Verdict summary

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | Commit-diff view inherits the previous file's `large` tier, so its Find bar is present but inert **and** it suppresses the browser's own Ctrl+F | Medium | **Confirmed · Fixed** |
| 2 | Markdown Preview is fetched **twice** on every first visit; the first request is aborted by the second | Medium | **Confirmed · Fixed** |
| 3 | With an active find query, the first Preview visit reports **0 matches** — the lazy loader never re-applies the search | Medium | **Confirmed · Fixed** |
| 4 | The large-file tier silently **drops a blank line** at any chunk boundary that lands on one (`<pre>` swallows a leading newline) | Medium-low | **Confirmed · Fixed** |
| 5 | `resolveTabView()` leaks legacy `listScrollLeft/Top` past a revision mismatch, so a new directory listing restores the **previous** directory's scroll | Medium-low | **Confirmed · Fixed** |
| 6 | Source has no arrival-path scroll restore; the pending-content refusal exists for Preview but not for Source, in both directions | Low | **Deferred** — timing-dependent; [verification scenario](#verification-scenario) below |
| 7 | A highlight-worker failure caches a permanent "plain" miss for the open buffer, so that file stays wholly uncoloured (no fallback lexer, no re-render) | Low | **Confirmed · Fixed** — one correction, see below |
| 8 | `explorerDiffWorkerCore()` is dereferenced without the optional chaining every other looked-up policy uses | Low | **Confirmed · Fixed** |
| 9 | Byte-triggered tier notice reads "**1 lines** rendered as plain text" on a minified single-line file | Low | **Confirmed · Fixed** — re-reproduced in Node |
| 10 | `explorerOverviewStoodDown()` still tests `aside.hidden`, which nothing sets any more | Low (info) | **Confirmed · Fixed** — term dropped |
| 11 | `explorer-diff.js`'s header still claims a byte-for-byte move; 15 of its 44 functions have since changed | Low | **Confirmed · Fixed** — re-verified mechanically |
| 12 | Worker pool is never terminated and disables itself page-wide on one worker error; `terminate()`/`size` have no callers | Low (info) | **Confirmed · Fixed** — both halves |
| 13 | `_explorerLineRecordCache` is a 2-entry **global**: 3+ file panes thrash it, and it pins two whole documents for the page's life | Low (info) | **Confirmed · Fixed** |
| 14 | The lazy preview payload carries no revision, so Source and Preview can describe different bytes | Low (info) | **Confirmed · Fixed** |
| 15 | The editor underlay's "splice" path still allocates a whole row model per animation frame | Low (info) | **Confirmed · No change** — the finding itself asks for none |

Nothing found rises to a security, durability, or data-loss defect. Findings 1–5
are user-visible behaviour changes; 6–15 are robustness, resource and accuracy
items. Everything except Finding 6 is now closed.

**Findings 1–5 are fixed.** Each was re-confirmed against the code before being
touched — the three code-path findings by tracing every call site, the two
Node-executed ones by re-running the reproduction against the real policy — and
each fix carries a test that was checked to fail with the fix reverted. See
[What was fixed](#what-was-fixed).

**Findings 7–15 are closed too**, on the same standard and in a later pass —
see [Findings 7–15](#findings-715). Eight were fixed and
Finding 15 was confirmed as needing no change, which is what it asked for.
Finding 6 remains deferred by decision.

---

## Finding 1 — the commit-diff view inherits the previous file's tier, and its Find dies with it

**Severity:** Medium · **Files:** `web/static/js/explorer-viewer.js`
**Status: confirmed, fixed.** Both missing sites now reset the tier.

`_explorerSourceTier` is a **pane**-level field, but only three of the five
places that repoint a pane at new content maintain it:

```
web/static/js/explorer-viewer.js:7698   applyExplorerSourceTier(pane, '');                          // renderExplorerImage
web/static/js/explorer-viewer.js:7907   applyExplorerSourceTier(pane, pane._explorerFileContent);   // renderExplorerFile
web/static/js/explorer-viewer.js:8129   applyExplorerSourceTier(pane, pane._explorerFileContent);   // updateExplorerFileInPlace
```

`renderExplorerCommitDiffFile()` (`explorer-viewer.js:8200`) and the directory
branch of `loadExplorerPane()` (`:8457`) both clear `_explorerFileContent` and
neither recomputes the tier.

For the directory branch this is currently harmless — `applyExplorerSearch()`
guards on `pane._explorerMode === 'file'`. For the commit-diff view it is not,
because that view sets `pane._explorerMode = 'file'`:

```js
// renderExplorerCommitDiffFile
pane._explorerMode = 'file';
pane._explorerFileContent = '';
//  … no applyExplorerSourceTier(pane, '')
```

and it renders the find bar **unconditionally** (`:8258`), unlike
`renderExplorerFile()`, which now gates it on `findAvailable`.

**Failure scenario.** Open a file over the large tier's ceiling (>20,000 rendered
rows or >4 MiB) in an explorer pane, then click any commit row in the Git
sidebar for the same pane. The commit-diff header still shows the Find control,
but every keystroke reaches:

```js
if (pane._explorerMode === 'file' && !explorerPaneAllowsFind(pane)) {
    return;                      // applyExplorerSearch, explorer-viewer.js:6315
}
```

so nothing is marked and the counter never updates. Worse, `focusExplorerSearch()`
succeeds (the input exists), so the global Ctrl+F handler calls
`event.preventDefault()` — the browser's own find is suppressed too. The reader
gets a control that does nothing *and* loses the fallback. It self-heals only on
the next ordinary file open.

**Suggested fix.** Call `applyExplorerSourceTier(pane, '')` in
`renderExplorerCommitDiffFile()` (and, for symmetry and to keep the field honest,
in `loadExplorerPane()`'s directory branch). A stricter alternative is to make
`explorerPaneSourceTier()` derive from `pane._explorerFileContent` rather than
from a cached field, but the cache exists precisely because counting lines is
`O(bytes)`; resetting at the two missing sites is the cheaper answer.

**Fixed as suggested.** `applyExplorerSourceTier(pane, '')` now runs in
`renderExplorerCommitDiffFile()` beside the other pane fields it repoints, and
in `loadExplorerPane()`'s directory branch for the same reason — the field
describes the buffer and the buffer is empty. The cached-field design is
unchanged.

*Confirmed before fixing.* All five repoint sites were read; the guard in
`applyExplorerSearch()` and `focusExplorerSearch()`'s unconditional success on
a present input were both traced, so the finding's two halves — an inert
control *and* a suppressed browser find — were verified rather than assumed.

*Test.* `tests/test_explorer_large_file_tier.py::test_a_commit_diff_does_not_inherit_the_previous_files_tier`
runs the real `renderExplorerCommitDiffFile()` in Node against the harness's
DOM stub, on a pane genuinely left at the `large` tier by a 40,000-line file.
It asserts the pane refuses Find beforehand, that the header renders the bar,
and that the bar it renders is one the find will answer. With the reset
reverted it fails on `tierAfter == 'large'`.

---

## Finding 2 — the Markdown Preview is fetched twice on every first visit

**Severity:** Medium · **Files:** `web/static/js/explorer-viewer.js`
**Status: confirmed, fixed.** The preview loader now carries the same join.

`loadExplorerDiff()` gained an explicit in-flight join for exactly this problem,
with a comment naming it:

```js
/* … Opening a file with the Diff panel up asks for the same patch twice inside
   one frame … and the second call aborted the first and refetched the identical
   URL, so every click on a changed-file row cost two requests (one always
   cancelled) and two server-side `git diff` runs. */
const inFlight = pane._explorerDiffLoadInFlight;
if (inFlight && inFlight.key === cacheKey) { await inFlight.promise; return; }
```

`ensureExplorerPreviewLoaded()` has no such join. Its only re-entry guard is
`pane._explorerPreviewLoaded`, which is set *after* the response lands.

**Trace (view switch).** `setExplorerFileView(index, 'preview')`:

1. `ensureExplorerPreviewLoaded(index)` → `explorerRequestSignal(pane, 'preview')`
   creates controller #1, `fetch` starts, the function suspends.
2. Three lines later, the same function calls `applyExplorerSearch(index, {scroll})`,
   which runs synchronously (no `await` precedes the branch) into
   `restoreExplorerPreview(index)`.
3. `restoreExplorerPreview()` sees `!pane._explorerPreviewLoaded` and calls
   `ensureExplorerPreviewLoaded(index)` again → `explorerRequestSignal(pane, 'preview')`
   **aborts controller #1** and starts fetch #2.

The same pair occurs on the file-open path: `renderExplorerFile()` calls
`ensureExplorerPreviewLoaded()` when `initialFileView === 'preview'` (`:8022`) and
then `applyExplorerSearch(index, {scroll: false})` at its tail.

Nothing breaks — request #1 rejects with `AbortError` and is swallowed — but the
server renders and Bleach-sanitizes the document twice (Flask has no client-abort
cancellation, so the abandoned render runs to completion), which is one of the
two costs this whole change set exists to remove. It also writes
`'Rendering preview...'` into the panel twice.

**Suggested fix.** Give the preview loader the same shape as the diff loader:
store `pane._explorerPreviewLoadInFlight = { path, promise }`, join a matching
path, supersede a different one.

**Fixed as suggested, with the identity widened by one field.** The record is
`{ path, content, promise }` and a join requires both to match. Keying on the
path alone would let an editor save — same path, different bytes — join the
load it was meant to supersede, handing the reader a render of the bytes they
had just replaced. The staleness check inside the loader already compares
exactly this pair, so the join now agrees with it.

*Confirmed before fixing.* The double entry was traced through both paths the
finding names — `setExplorerFileView()` and `renderExplorerFile()` — and
`applyExplorerSearch()` was re-read to confirm no `await` precedes its branch
dispatch, so the second call really does land inside the first's flight. One
correction to the finding's scope: the no-query `else` branch also reaches
`restoreExplorerPreview()`, so this fired on every first Preview visit, not
only on one with an active find.

*Test.* `tests/test_explorer_scroll.py::test_the_first_preview_visit_costs_exactly_one_request`
counts `fetch` calls across a real first Preview visit driven through the real
loader. It asserts one request, that the second caller genuinely arrived and
was joined rather than skipped, and that the join record is released when the
load settles. It fails at two requests with the join reverted.

---

## Finding 3 — with a find query active, the first Preview visit reports 0 matches

**Severity:** Medium · **Files:** `web/static/js/explorer-viewer.js`
**Status: confirmed, fixed.** Preview now has the arrival hook Diff had.

`loadExplorerDiff()` re-applies the find once its content arrives:

```js
applyExplorerPendingDiffScroll(index);
if (activeExplorerFileView(index) === 'diff') {
    applyExplorerSearch(index);
}
```

`ensureExplorerPreviewLoaded()` has no equivalent:

```js
const painted = paintExplorerPreview(index);
requestExplorerPanelScrollRestore(index, 'preview');
return painted;
```

**Failure scenario.** Open a Markdown file, type a query in Find (Source view
marks it normally), then click **Preview** for the first time for that file.
`applyExplorerSearch()`'s preview branch runs *before* the fetch lands:

```js
const preview = restoreExplorerPreview(index);   // returns the loader element
…
const previewMarks = markExplorerSearchInElement(preview, query, …);
matchCount = previewMarks.length;                // 0 — the panel says "Rendering preview..."
```

so the counter is set to `0/0` and no marks are painted. When the HTML arrives,
`paintExplorerPreview()` replaces the subtree and nothing re-runs the search. The
reader has to retype or step the query to get marks. Later visits to the same
file are correct, because `_explorerPreviewLoaded` short-circuits to a
synchronous paint.

There is a cosmetic sub-case in the same window: `markExplorerSearchInElement()`
is run against the literal string `Rendering preview...`, so a query such as
`render` briefly reports a match that is the loader text.

**Suggested fix.** Mirror the diff loader — after `paintExplorerPreview()`, if
`activeExplorerFileView(index) === 'preview'`, call `applyExplorerSearch(index)`.
Fixing Finding 2 first removes the second, redundant entry into this path.

**Fixed as suggested**, in that order, with two conditions on the call. It is
skipped when no query is typed, so an arrival never repaints a panel on behalf
of a find nobody ran; and it passes `scroll: false`, because the reader did not
ask to be moved — they switched panels, and the panel finished loading. The
cosmetic sub-case goes with it: the marks over `Rendering preview...` are
replaced along with the subtree that carried them.

*Test.* `tests/test_explorer_scroll.py::test_an_active_find_is_re_applied_to_the_preview_that_arrives`
asserts the query is re-applied exactly once after the paint, and that a panel
with no query is left alone. It fails at zero re-applications with the hook
reverted.

---

## Finding 4 — the large-file tier drops a blank line at a chunk boundary

**Severity:** Medium-low · **Files:** `web/static/js/explorer-viewer.js`, `web/static/js/explorer-tiers.js`
**Status: confirmed, fixed.** Sacrificial newline, and the test now measures
the parsed text rather than the markup string.

The tier's DOM unit is one `<pre>` per chunk:

```js
function explorerLargeSourceChunkHtml(chunk) {
    return `<pre class="explorer-source-chunk">${escHtml(chunk)}</pre>`;
}
```

`sourceChunks()` cuts *after* a newline, so a chunk begins with the first
character of the next line — and when that line is empty, the chunk begins with
a newline. Reproduced against the real policy:

```
$ node -e "…sourceChunks(file with a blank line at line 5000)…"
chunks: 4
0 startsWithNewline= false "line 0\nline 1\n"
1 startsWithNewline= true  "\nline 5001\nlin"     ← blank line 5000 is this chunk's first character
2 startsWithNewline= false "line 10000\nlin"
lossless(join)= true
```

The HTML parser discards a U+000A immediately following a `<pre>` start tag (HTML
Standard, "the *in body* insertion mode" special-cases `pre`/`listing`/`textarea`),
and `innerHTML` / `insertAdjacentHTML` use the same fragment-parsing algorithm.
So chunk 1 renders as `line 5001…` and the blank line is gone: the pane's text is
no longer byte-faithful to the file, and selecting/copying the pane loses that
line. For a 10 MiB log with a normal share of blank lines this happens a handful
of times per file.

**Why the tests do not catch it.** `tests/test_explorer_large_file_tier.py`
asserts losslessness by regexing the chunk bodies out of the **generated HTML
string**:

```python
/<pre class="explorer-source-chunk">([\s\S]*?)<\/pre>/g
…
lossless: chunkBodies.join('').replace(/&amp;/g, '&') === body
```

That verifies the markup, not the parsed DOM — and the fixture
(`'line ' + i`) contains no blank lines, so even a DOM-based assertion would pass
with it.

**Suggested fix.** Emit a sacrificial newline the parser will eat —
`` `<pre class="explorer-source-chunk">\n${escHtml(chunk)}</pre>` `` — or drop
`<pre>` for a `<div>` carrying the existing `white-space: pre`. Either way, add a
fixture with a blank line at a chunk boundary and assert on `textContent`, not on
the markup string.

**Fixed with the sacrificial newline**, not the `<div>`: `<pre>` is what the
tier's CSS and its selection behaviour are built on, and the one-character
change disturbs neither.

*Confirmed before fixing.* The boundary was re-reproduced against the real
`sourceChunks()` — a 12,000-line file with line 5000 blank cuts into four
chunks, of which chunk 1 begins with the newline, and the join is still
lossless. The parser half is the HTML Standard's `pre`/`listing`/`textarea`
special case in the *in body* insertion mode, which `innerHTML` and
`insertAdjacentHTML` both run.

*Test.* The Node harness in `tests/test_explorer_large_file_tier.py` gained a
`blanks` fixture that puts an empty line on **every** chunk cut, and its
losslessness measure now applies that one parser rule to each chunk body before
reassembling the file. This suite has no DOM to parse with, so the rule is
modelled explicitly and named as such rather than left implied. The old
markup-string measure survives as `losslessMarkup`, and the new case asserts it
is now **false** while the parsed text is lossless — the markup deliberately no
longer reproduces the file on its own, which is exactly the trap the original
assertion fell into. Reverting the fix fails the case.

---

## Finding 5 — a new directory listing restores the previous directory's scroll

**Severity:** Medium-low · **Files:** `web/static/js/explorer-scroll.js`, `web/static/js/explorer-viewer.js`
**Status: confirmed, fixed.** Both spellings of the listing offset now drop
together.

The old `explorerMatchingTabView()` replaced the whole scroll state on a revision
mismatch:

```js
scroll: same ? view.scroll : { activeView: view.mode, panels: {}, sidebar: {} },
```

The new `resolveTabView()` filters panel by panel and spreads the rest through:

```js
const scroll = { ...(view.scroll || {}), activeView: view.mode, panels, sidebar: … };
if (view.scroll?.directory && matches('directory')) { scroll.directory = view.scroll.directory; }
else { delete scroll.directory; }
```

`captureExplorerFileScroll()` writes *two* representations of the listing offset —
the `directory` metrics **and** the legacy top-level `listScrollLeft` /
`listScrollTop`. Only the first is filtered. Executed against the real policy:

```
$ node -e "…resolveTabView(tab with revisions {directory:'OLD'}, {directory:'NEW'})…"
{ "activeView": "preview", "listScrollLeft": 42, "listScrollTop": 1234, "panels": {}, "sidebar": {} }
```

`restoreExplorerFileScroll()` then falls back to exactly those fields:

```js
{ el: list, metrics: state.directory || { scrollLeft: state.listScrollLeft || 0,
                                          scrollTop:  state.listScrollTop  || 0 } }
```

and `applyScrollMetrics()` now prefers an exact offset over a ratio, so the stale
`1234` is applied verbatim.

**Failure scenario.** Browse to a long directory in the Preview tab, scroll down,
open a subdirectory. `loadExplorerPane()`'s tail calls
`explorerMatchingTabView(previewTab, …)` under a comment that states the intended
contract:

```
listing identity still matches (OD-4); a genuinely new directory
never matches, so navigation always starts at the top.
```

That is no longer what happens — the new listing opens at the old listing's
offset (clamped to the new extent). Before this branch the same path applied `0`.

**Suggested fix.** Drop `listScrollLeft` / `listScrollTop` alongside `directory`
in `resolveTabView()` (they describe the same scroller), or stop spreading
`view.scroll` and build the result from the fields the policy actually validates.

**Fixed with the first option.** The second is the better shape but a wider
change: the spread also carries fields the policy does not name, and
enumerating them turns a filter into an allowlist every future field has to be
added to. The two legacy keys are deleted in the same branch that deletes
`directory`, under a comment stating they are one scroller in two spellings.

*Confirmed before fixing.* Re-reproduced against the real policy — a tab whose
`directory` revision no longer matches returned `listScrollLeft: 42,
listScrollTop: 1234` with `directory` correctly dropped. Both halves of the
mechanism were re-read: `captureExplorerFileScroll()` writing the pair, and
`restoreExplorerFileScroll()` falling back to it as an *exact* offset, which
`applyScrollMetrics()` now prefers over a ratio.

*Test.* `tests/test_explorer_scroll.py::test_a_new_listing_drops_both_spellings_of_the_old_listings_offset`
runs the real `resolveTabView()` over a listing tab carrying both spellings,
and asserts a matching revision keeps both while a new directory keeps neither.

---

## Finding 6 — Source has no arrival-path scroll restore, and the pending-content rule is applied in only one direction

**Severity:** Low · **Files:** `web/static/js/explorer-scroll-adapter.js`, `web/static/js/explorer-viewer.js`
**Status: deferred by decision, 2026-08-20.** Not fixed, and not dismissed.

Both halves turn on landing an interaction inside a build window, which is a
property of a running browser rather than of the code. The asymmetry described
below is real and reads plainly in the source; whether it ever costs the reader
anything cannot be settled from the source alone. Applying the symmetry fix
blind would add guards and an arrival hook to the one part of this machinery
whose ordering this review specifically checked and found sound, against a
defect nobody has yet observed. It stays open until the
[verification scenario](#verification-scenario) is run in a browser — and the
sub-case at the end of this section, the deferred restore reasserting a view
the reader has moved away from, is deferred with it.

The design states that a panel whose content is still being built is neither
captured from nor restored into. Three of the four halves implement it; one does
not, in each direction.

*Restore.* `requestExplorerPanelScrollRestore()` refuses only for Preview:

```js
if (mode === 'preview' && !pane._explorerPreviewLoaded) {
    return false;
}
```

There is no `mode === 'source' && pane._explorerSourceRenderJob` counterpart, and
`explorerFinishSourceRender()` never re-requests a panel restore — Preview
(`ensureExplorerPreviewLoaded` → `requestExplorerPanelScrollRestore(index, 'preview')`)
and Diff (`applyExplorerPendingDiffScroll`) each have an arrival hook; Source has
none. Source instead relies entirely on `whenExplorerSourceRendered()`, which the
two rebuild entry points do use correctly — `renderExplorerFile()` and
`updateExplorerFileInPlace()` both call `renderExplorerSource(index)`
*synchronously* before queueing the restore, so the job exists and the callback
is queued. **That path is sound; I checked it specifically.**

The gap is the third caller. `setExplorerFileView()` ends with a bare
`requestExplorerPanelScrollRestore(index, selectedMode)`. Opening a large file
straight into Diff still starts the Source build (`renderExplorerSource(index)`
runs regardless of which panel is shown); clicking **Source** while that build is
in flight requests a restore whose only bound is `restorePlan`'s six
animation-frame attempts. A frame-sliced build of a 20,000-row document takes
considerably more than six frames, so the sequence exhausts and the final attempt
applies a clamped offset with no later correction.

*Capture.* `captureExplorerFileScroll()` refuses to read a Source panel whose
build is running, with a long comment explaining why:

```js
const contentPending = mode === 'preview'
    ? !pane?._explorerPreviewLoaded
    : mode === 'source' && Boolean(pane?._explorerSourceRenderJob);
```

`rememberExplorerPanelScroll()` — the other capture entry point, called from
`setExplorerFileView()` on every view switch — has no such guard and *writes* what
it reads into the same store:

```js
function rememberExplorerPanelScroll(index, mode) {
    const metrics = captureScrollMetrics(explorerPanelScrollTarget(panel));
    if (!metrics) return null;
    storeExplorerPanelMetrics(index, mode, metrics);   // unconditional
```

In practice a swap build keeps the on-screen rows and their offset, so the read is
usually honest; a first paint legitimately reads 0. But the two capture paths
disagree about the same rule, and the one without the guard is the one that
overwrites the stored value.

**Suggested fix.** Add the Source pending check to
`requestExplorerPanelScrollRestore()` and to `rememberExplorerPanelScroll()`, and
call `requestExplorerPanelScrollRestore(index, 'source')` from
`explorerFinishSourceRender()` so Source has the same arrival hook Preview and
Diff have. That makes the rule symmetric and removes the reliance on callers
ordering `renderExplorerSource()` before `whenExplorerSourceRendered()`.

### Verification scenario

This is the one finding whose confidence is *Medium*, because both halves depend
on landing an interaction inside a build window. The scenario below makes that
window deterministic instead of relying on how fast the tester clicks.

#### Fixture

The file has to be big enough that the row build is frame-sliced, but small
enough that it does **not** fall into the large tier (which renders plain chunks
and has no per-line rows to restore onto). The band is `CHUNK_MIN_ROWS = 4000` <
rows ≤ `SOURCE_LARGE_MAX_LINES = 20000`, and ≤ 4 MiB. 15,000 lines sits
comfortably inside it, and the pacing constants make the window predictable:

```
$ node -e "…chunkPlan(15000, {async:true})…"
{"chunked":true,"size":2000,"slices":8}          ← ≥ 8 animation frames to build
$ node -e "…MAX_PANEL_SCROLL_RESTORE_ATTEMPTS…"
6                                                ← 1 synchronous + 5 rAF attempts
```

Eight frames of build against six restore attempts is the whole of Scenario A:
the sequence is guaranteed to run out before the rows exist, on any machine.

Generate a Markdown fixture inside an explorer root — Markdown so the Preview
panel exists without needing Git state, and with headings so the model's
fence-aware heading scan is exercised too:

```bash
python -c "open('big.md','w',encoding='utf-8').write(''.join(
    (f'## Section {i//200}\n' if i % 200 == 0 else f'Line {i} lorem ipsum dolor sit amet.\n')
    for i in range(15000)))"
```

#### Console probe

`explorer-viewer.js` and `terminals.js` are classic scripts with no IIFE, so
their top-level bindings are reachable from DevTools. Note that `terminals` is
declared with `let`, so it lives in the global *lexical* environment — evaluate
it bare in the console; `window.terminals` is `undefined` and is not evidence of
anything.

```js
const i = 0;                                   // the explorer card's data-slot
const pane = () => terminals[i];
const view = () => document.querySelector(
    `#explorer-list-${i} [data-explorer-file-panel="source"] .explorer-source-view`);

const probe = () => ({
    building: Boolean(pane()._explorerSourceRenderJob),
    rows:     view()?.querySelectorAll('.explorer-source-line').length,
    target:   pane()._explorerPanelScrollStore?.panels?.source?.metrics?.scrollTop,
    actual:   view()?.scrollTop,
    shown:    activeExplorerFileView(i)
});

// Resolves on the first frame a Source build is in flight.
const whenBuilding = () => new Promise(resolve => {
    const tick = () => (pane()._explorerSourceRenderJob ? resolve() : requestAnimationFrame(tick));
    tick();
});
```

`target` is the offset the pane is holding for Source; `actual` is where the
scroller really is. The defect in each half is a divergence between them that
never closes.

#### Scenario B — the capture half (run this one first; it is the more damaging)

The stored target is destroyed rather than merely applied late, and there is no
recovery, so this is the half worth confirming before any fix is written.

1. Open `big.md` in an explorer pane, Source view.
2. Scroll to roughly 85–90% of the document. **Not** the last two pixels —
   `captureScrollMetrics()` sets `wasAtBottom` at `scrollTop >= maxScrollTop - 2`,
   and `wasAtBottom` wins outright in `applyScrollMetrics()`, which would mask the
   defect.
3. Open a second file in the same pane and pin it, then switch back to `big.md`'s
   tab. Switching away runs `explorerCaptureActiveTabView()`, which is what puts
   the mid-document offset into `tab.view.scroll.panels.source`.
4. Before clicking back, arm the probe:

   ```js
   whenBuilding().then(() => {
       const before = probe();
       setExplorerFileView(i, 'preview');       // what a hand-click does
       console.log({ before, after: probe() });
   });
   ```

5. Click `big.md`'s tab. `renderExplorerFile()` runs
   `setExplorerPanelScrollState()` early — so the target is installed — then
   starts the chunked build, then queues the restore behind it.

**Expected if the defect is real.** `before.target` is the mid-document offset
(some thousands of px) while `before.actual` is `0`, because a first-paint
chunked build empties the scroller. `after.target` is `0`: `setExplorerFileView()`
called `rememberExplorerPanelScroll(index, 'source')`, which read the collapsed
scroller and wrote it over the pending target. Switching back to Source
afterwards lands at line 1 and stays there — `storeExplorerPanelMetrics()` also
bumps the previous entry's `sequence`, which cancels the retry the earlier
restore had queued, so nothing corrects it.

**Expected if it is not.** `after.target` still holds the mid-document offset.

This is exactly the hazard `captureExplorerFileScroll()` already documents at
length and guards against with `contentPending`; the scenario is asking whether
the *other* capture entry point needs the same guard.

#### Scenario A — the restore half

1. Same fixture and same steps 1–3 as above, but leave the tab's stored view on
   **Diff** rather than Source: with `big.md` open, add an uncommitted change so
   the Diff panel exists, switch to Diff, then switch tabs away and back. (If
   arranging Git state is inconvenient, Preview works the same way — the point is
   only that `initialFileView` is not `source`, so the Source build runs for a
   hidden panel.)
2. Arm the probe:

   ```js
   whenBuilding().then(() => {
       setExplorerFileView(i, 'source');        // click "Source" mid-build
       requestAnimationFrame(() => setTimeout(() => console.log(probe()), 400));
   });
   ```

3. Click `big.md`'s tab.

**Expected if the defect is real.** The logged `probe()` shows `building: false`,
`rows: 15001`, and `actual` short of `target` — the six-attempt sequence ran out
at roughly frame 5 of an 8-frame build, applied a clamped offset on its final
attempt, and nothing re-requested a restore once the rows finished.

**Expected if it is not.** `actual` equals `target` (within a pixel or two).

If the divergence is small on a fast machine, widen the build window with
DevTools → Performance → CPU throttling at 6×, which lowers the number of rows
each 8 ms frame budget can emit and stretches the build well past a second. The
frame arithmetic above says the sequence should exhaust even unthrottled, so a
*pass* here without throttling is worth re-running throttled before concluding
the finding is wrong.

#### Also watch for: the deferred restore reasserting the view

In both scenarios, when the build finishes, the queued
`whenExplorerSourceRendered()` callback runs
`restoreExplorerFileScroll(index, effectiveScrollState)`, whose first act is:

```js
setExplorerFileView(index, restoredMode, { captureScroll: false });
```

with `restoredMode = state.activeView`, captured before the reader touched
anything. So a panel switch made *during* the build looks likely to be undone
when the build lands — the pane snapping from Preview (or Diff) back to the mode
it opened in, seconds after the reader chose otherwise.

I have not separated this from Finding 6 because it shares the same root — the
deferred restore does not know the reader moved — and because it is the same
timing window. If it reproduces, it deserves its own entry with its own fix
(re-read the live view mode at restore time, or drop the mode switch when the
reader has changed it since capture), because the suggested fix above does not
address it.

---

## Finding 7 — a highlight-worker failure leaves the open file permanently uncoloured

**Severity:** Low · **Files:** `web/static/js/explorer-viewer.js`

On a worker rejection the pending job caches a "plain" miss and returns:

```js
pane._explorerHighlightCache = { content, language: normalizedLanguage, lines: null, plain: true };
```

and the cache read maps that to the pending sentinel:

```js
if (cache && cache.content === content && cache.language === normalizedLanguage) {
    return cache.plain ? EXPLORER_HIGHLIGHT_PENDING : cache.lines;
}
```

`EXPLORER_HIGHLIGHT_PENDING` renders `explorerMarkedEscHtml()` — plain escaped
text with no colour and, deliberately, no fall-through to the per-line fallback
lexer. There is also no re-render after the failure, so the rows already on
screen are what the reader keeps.

The effect is bounded but real: the file that was open when the worker died stays
wholly uncoloured for as long as it is open. (A *different* file recovers,
because `_failWorker()` disables the pool and `workers.canHighlight()` then
returns false, routing the next file to the synchronous path.)

This also contradicts `CLAUDE.md`, which states "worker failure leaves a usable
fallback-coloured view". The code's own comment on the sentinel argues the
opposite — a 1.5 MiB minified line must not be handed to the handwritten lexer —
and that argument is sound for *that* file. It is not sound for a 100 KiB Python
file, which is the common case above the 64 KiB worker floor.

**Suggested fix.** On failure, cache the miss but re-render once through
`explorerHighlightDocumentLinesCached()` when the buffer is under the 2 MiB plain
threshold; keep the plain result above it. Then reconcile whichever behaviour
survives with the `CLAUDE.md` sentence.

---

**Fixed, and one claim in this finding is wrong.** The sentence attributed to
`CLAUDE.md` — "worker failure leaves a usable fallback-coloured view" — is not
in `CLAUDE.md`; that file says nothing about the worker at all, so there was no
contract to reconcile. The code behaviour is real regardless, and the reasoning
about *which* file recovers was right.

The fix also drops the size condition the suggestion carried, because that
condition cannot fire: the worker path is entered only when `source.length <=
EXPLORER_PLAIN_PREVIEW_THRESHOLD`, so every buffer that can reach this `catch`
is already one the synchronous path is allowed to tokenize. A failed job now
does exactly what a page with no worker support does —
`explorerHighlightDocumentLinesCached()` on this thread, the same staleness
handshake the success path uses, one re-render. That removes the third cache
state as well: with nothing writing `plain: true`, the read that mapped it back
to the sentinel went with it, so a cache hit means one thing again.

*Confirmed before fixing.* Both halves were traced — the write in the `catch`
and the read returning `EXPLORER_HIGHLIGHT_PENDING` for it — and the recovery
asymmetry was confirmed at its source: `_failWorker()` disables the pool, so
`canHighlight()` routes the *next* file down the synchronous path while the open
one keeps the cached miss.

*Test.* `tests/test_explorer_source_frame.py::test_a_failed_highlight_job_recolours_the_buffer_that_was_open`
rejects a real job through the real cache gate, with the whole-document
tokenizer and the re-render as spies. It asserts the first paint is still the
plain sentinel and tokenizes nothing, that the failure tokenizes once and
re-renders once, and that every later ask returns colours rather than the
sentinel. Reverted, it fails at zero tokenizations.

---

## Finding 8 — `explorerDiffWorkerCore()` is dereferenced without a guard

**Severity:** Low · **File:** `web/static/js/explorer-diff.js`

Every other looked-up policy in this change set degrades if its module is
missing — `explorerTierPolicy()?.`, `explorerRepaintPolicy()?.`,
`explorerScrollPolicy()` with an explicit null branch, `workers?.available?.()` —
each with a comment saying "so a page that somehow loaded without it degrades to
today's behaviour instead of throwing". The worker *core* is the exception:

```js
web/static/js/explorer-diff.js:855   const model = explorerDiffWorkerCore().parseSideBySideDiff(diff);
web/static/js/explorer-diff.js:892   const model = explorerDiffWorkerCore().parseSideBySideDiff(diff);
web/static/js/explorer-diff.js:1010  explorerDiffWorkerCore().parseSideBySideDiff(diff)
```

`explorerDiffWorkerCore()` returns `null` when `window.GridVibeExplorerWorkerCore`
is absent, so a failed `explorer-worker-core.js` load turns the fallback diff
renderer — the one reached whenever Diff2Html is unavailable — into a
`TypeError`. Since `explorer-worker-core.js` is now a hard dependency of the
non-worker path, either the guard or the comment elsewhere is wrong.

**Suggested fix.** Either guard these three sites and keep the old inline parse as
the last resort, or state in the module header that `explorer-worker-core.js` is a
hard dependency of `explorer-diff.js` and drop the pretence of optionality.

---

**Fixed with the guard, not the header note.** The suggestion's first option
offered to "keep the old inline parse as the last resort" — there is no inline
parse left to keep; it moved into `explorer-worker-core.js`, which is what made
that module a dependency of the fallback in the first place. So
`explorerParsedDiffModel()` wraps the lookup and returns `null`, and the three
call sites paint an explicit message.

The message reuses the existing `.explorer-diff-empty` rule (guardrail 7) but
deliberately not its sentence: "No Git diff for selected file" would be a lie a
reader acts on by going to look for changes that are there.

*Test.* `tests/test_explorer_workers.py::test_a_missing_worker_core_degrades_the_diff_instead_of_throwing`
drives both entry points — the large tier's no-worker branch and the small-tier
fallback that runs when Diff2Html is absent too — with no core on the page.
Reverted, it fails with exactly the predicted `TypeError: Cannot read properties
of null (reading 'parseSideBySideDiff')`.

---

## Finding 9 — "1 lines" in the byte-triggered tier notice

**Severity:** Low · **File:** `web/static/js/explorer-tiers.js`

The tier has two independent triggers, but the notice only ever reports lines:

```
$ node -e "…sourceTierNotice(sourceMetrics('x'.repeat(5*1024*1024)))…"
detail: "1 lines rendered as plain text so the pane stays responsive."
```

A minified bundle is the exact case the byte ceiling exists for, and it is the
case where the notice reads worst — both ungrammatical and uninformative, since
the reader's problem is 5 MiB on one line, not "1 line".

**Suggested fix.** Report the trigger that fired: lines when `rows` crossed,
size when `bytes` crossed (`formatExplorerSize()` already exists in the viewer, or
inline the same arithmetic to keep the policy DOM-free). Pluralise either way.

---

**Fixed as suggested**, and re-reproduced first: `sourceTierNotice()` on a
5 MiB single-line buffer really did read "1 lines rendered as plain text".

`sourceTierReason()` names the ceiling that fired — lines when `rows` crossed,
`formatBytes()` when only the byte ceiling did — and lines win when both
crossed, because the row count is the one the reader can see on screen. The byte
formatter is local to the policy rather than borrowed from the viewer's
`formatExplorerSize()`, which would have made a DOM-free, Node-executable module
depend on the page.

*Test.* `tests/test_explorer_tiers.py::test_the_notice_names_the_ceiling_that_actually_fired`
covers each trigger separately and both together, plus singular/plural and the
byte units.

---

## Finding 10 — a dead branch in the overview's stand-down predicate

**Severity:** Low (informational) · **File:** `web/static/js/explorer-overview.js`

`explorerOverviewHtml()` no longer emits `hidden`, and the one writer only toggles
a class:

```js
function setExplorerOverviewStoodDown(aside, stoodDown) {
    aside.classList.toggle('is-empty', stoodDown);
    …
}
```

so the first half of the reader is unreachable:

```js
function explorerOverviewStoodDown(aside) {
    return aside.hidden || aside.classList.contains('is-empty');
}
```

The CSS comment reserves `hidden` for "a genuine 'no overview column at all'"
case, so this reads as deliberate forward-compatibility rather than an oversight.
Flagged only because guardrail 5 treats "nothing reads it" as the test, and a
reader of this function today cannot tell which it is.

**Suggested fix.** Either drop the `aside.hidden` term, or note in the function
comment that it exists for a state nothing sets yet.

---

**Fixed with the first option** — the `aside.hidden` term is dropped, so the
predicate reads exactly what its one writer writes. Guardrail 5 makes "who reads
it" the test, and after this change nothing does. The CSS
`.explorer-source-overview[hidden]` rule stays as the reserved state it
documents; no JavaScript consults `hidden` any more.

*Test.* The existing `STAND_DOWN_HARNESS` in `tests/test_explorer_overview.py`
now records what the predicate answers beside what the writer wrote, asserts the
two agree in every state it drives, and adds a state with `hidden` set behind
the writer's back. Reverted, that last case fails.

---

## Finding 11 — `explorer-diff.js`'s header no longer describes the file

**Severity:** Low · **File:** `web/static/js/explorer-diff.js`

The header states:

```
A move, not a rewrite: every function below is byte-for-byte the one that
stood in explorer-viewer.js, including its four-space indentation, so the
diff reads as a relocation.
```

That was true at `8902827`. Two later commits changed the file in place.
Comparing each top-level function against the merge-base `explorer-viewer.js`:

```
explorer-diff.js top-level functions: 44
NOT byte-identical to old viewer (15):
  explorerDiff2HtmlConfig, explorerDiffTierBannerHtml,
  synchroniseExplorerDiffWrappedRows, explorerDiffHunkStart,
  explorerDiffChangeBlocks, renderExplorerDiffWithDiff2Html,
  explorerDiffWorkerClient, explorerDiffWorkerCore,
  paintExplorerSideBySideDiff, renderExplorerLargeDiff, renderExplorerDiff,
  explorerDiffLanguage, renderExplorerSideBySideDiffModel,
  renderExplorerSideBySideDiff, loadExplorerDiff
```

The extraction itself still satisfies guardrail 6 — the move happened first and
`test_explorer_source_frame.py` / `test_explorer_overview.py` were not required to
change for it. Only the standing claim is now false, and in this repository a
module header is where a contract lives.

**Suggested fix.** Rewrite the paragraph to say the file *began* as a pure move
and now owns the tiered renderers and the worker-backed large-diff parse.

---

**Fixed as suggested**, after re-verifying the claim mechanically rather than
taking the earlier count on trust. Comparing each top-level function against the
merge-base `explorer-viewer.js`: 14 of 42 differ — 7 changed in place and 7 that
never stood in the viewer at all. (This pass's extractor finds 42 functions
where the original review found 44; the conclusion is the same either way, and
the rewritten header no longer makes a claim that depends on the number.)

The paragraph now says the file *began* as a pure move, marks that as history
rather than description, and warns against reading a function here as evidence
of what the viewer used to do. It also states the `explorer-worker-core.js`
dependency Finding 8 exposed.

---

## Finding 12 — the worker pool is never terminated and disables itself page-wide on one error

**Severity:** Low (informational) · **File:** `web/static/js/explorer-worker-client.js`

Two related observations:

1. `createBrowserClient()` exposes `terminate()` and `size`; nothing in the app
   calls either (`grep -rn "\.terminate()" web/static/js/` finds only the
   definition). Workers are therefore created lazily and then live for the page's
   lifetime, including after the last explorer pane is closed — up to
   `min(max(hardwareConcurrency - 1, 1), 4)` threads idling with a Highlight.js
   build resident in each. This is small, but it is state nothing can reclaim.
2. `_failWorker()` calls `_disable()`, which turns off the pool **for the whole
   page**, permanently, on a single worker error. The comment justifies this for
   a missing first-party asset. It also catches a `postMessage` throw, which is a
   per-job condition, and there is no path back — one transient failure costs
   every pane its worker offloading for the rest of the session.

**Suggested fix.** Either wire `terminate()` into pane teardown (the cached-group
close path in `terminals.js` already walks explorer panes) or delete the unused
API; and consider distinguishing "the worker could not be constructed / could not
import" (disable) from "one job failed" (retry once, then disable).

---

**Both halves fixed, each on its own terms.**

*The unused API.* `terminate()` is wired up rather than deleted, because the
threads it reclaims are real. `releaseExplorerResourcesIfIdle()` in
`terminals.js` runs from `dropCachedGroupView()` and `teardownCurrentGrid()`,
and fires only when no explorer pane is left in the visible grid **or** any
cached group. The predicate has to be that strict: terminating mid-flight
rejects the running jobs as superseded, so a pane still on screen would sit on
its plain first paint until something else happened to repaint it. `size` had no
such argument for existing and is deleted.

*The page-wide disable.* `_failWorker()` now spends a life and `_dispatch()`
respawns; the second failure disables. Construction failure still disables
immediately through `_spawn()`'s own `catch`, which is the case the original
comment actually justified.

*Test.* `tests/test_explorer_workers.py::test_one_worker_failure_is_survived_and_the_second_disables_the_pool`
kills a worker mid-job, asserts the pool stays available and serves the next
request from a respawn, then kills again and asserts it concedes and builds
nothing more.

---

## Finding 13 — the line-record cache is a two-entry global

**Severity:** Low (informational) · **File:** `web/static/js/explorer-viewer.js`

```js
const _explorerLineRecordCache = [];
…
_explorerLineRecordCache.unshift({ source, records });
_explorerLineRecordCache.length = Math.min(_explorerLineRecordCache.length, 2);
```

The comment explains the size: "Two entries, because the Source rows and the
editor's draft are both live during an edit and they are different strings." That
reasoning is per-pane, but the cache is module-level and shared by every pane. A
workspace with three explorer file panes evicts on every cross-pane call, so the
optimisation quietly stops applying in exactly the configuration where the total
cost is highest.

Secondly, it pins whatever two documents were last asked about — for a 4 MiB file
that is a ~100k-object record array plus the string — for the life of the page,
including after every explorer pane is closed.

**Suggested fix.** Key the cache on the pane (`pane._explorerLineRecords`, plus one
slot for the draft) rather than globally, which fixes both points at once.

---

**Fixed, in a simpler shape than suggested.** Keying the cache on the pane
would have meant threading a `pane` through `explorerSourceRowModel()` and
`explorerEditUnderlayLines()`, which are pure content functions today. Both
symptoms come apart without that:

* the limit is now `2 x live explorer file panes`, clamped to 8 — two is the
  right number *per pane* (the Source rows and the editor's draft), and it was
  only ever wrong as a global;
* `explorerReleaseLineRecordCache()` empties the cache from the same teardown
  hook Finding 12 added. While panes are open no sweep is needed: a document
  nobody is looking at falls out of an LRU sized to the panes that are.

An earlier attempt pruned non-live entries on every miss. It was dropped —
within a single render a transient string would evict an entry the next call was
about to ask for again, which is the thrash this finding is about.

*Test.* `tests/test_explorer_source_frame.py::test_line_record_cache_is_sized_by_the_panes_and_is_given_back`
observes hits by record identity: one pane, three panes, and a pane holding its
draft beside its file. Reverted to the flat 2, the three-pane case fails
`[False, False, False] != [True, True, True]` — the thrash, reproduced.

---

## Finding 14 — the lazy preview payload carries no revision

**Severity:** Low (informational) · **Files:** `web/explorer.py`, `web/static/js/explorer-viewer.js`

`get_explorer_file_preview_payload()` returns `root`, `path`, `preview_type`,
`preview_html`, `truncated` — no `revision` / `base_revision`. The client's guard
compares its own state before and after the flight:

```js
if (terminals[index] !== pane || sessionIds[index] !== sessionId
    || pane._explorerFilePath !== path
    || pane._explorerFileContent !== content
    || document.getElementById(`explorer-preview-${index}`) !== preview) {
    return null;
}
```

That catches the viewer moving on; it cannot catch the **file** moving on. Source
and Preview used to come from a single read and were consistent by construction;
they are now two reads, so a write landing between them yields a Preview of newer
bytes beside Source's older ones until the change watcher catches up.

The window is small and self-correcting, and the read-only contract is unaffected.
Noting it because "the panel's content is fetched separately" is a new invariant
and this is the property it gave up.

**Suggested fix.** Return the same revision token the file payload uses and have
`ensureExplorerPreviewLoaded()` compare it, falling back to a refetch (or simply
leaving `_explorerPreviewLoaded` false) on a mismatch.

---

**Fixed as suggested.** `get_explorer_file_preview_payload()` returns
`state_revision` from the `stat` it already performs — the same token the file
payload sets as the change listener's baseline, so the two are comparable by
construction. `ensureExplorerPreviewLoaded()` compares it against
`pane._explorerFileStateRevision` and declines a mismatch.

Declined rather than refetched: the open-file change listener is going to see
the same revision move and reload the file, and the panel paints from that. A
response carrying no token is accepted, so the check cannot turn an older server
into a permanently blank panel — and it is skipped when the pane holds no
baseline, which is exactly the case where no watcher would arrive to resolve it.

*Test.* `tests/test_api.py::test_explorer_file_preview_carries_the_revision_source_was_read_at`
covers the payload; `tests/test_explorer_scroll.py::test_a_preview_describing_different_bytes_is_declined`
drives the client through the real loader for all three cases — stale, matching,
and no token at all.

---

## Finding 15 — the editor underlay's splice still allocates a whole row model per frame

**Severity:** Low (informational) · **File:** `web/static/js/explorer-edit-overlay.js`

The header claims the splice path avoids `O(document)` work per keystroke. The DOM
work is indeed `O(edit)`, but the model in front of it is not:

```js
const model = explorerSourceRowModel(draft, language, new Set(), null, { foldControls: false });
const lines = model.records.map(record => record.text);
const plan  = policy.lineSplicePlan(pane._explorerEditOverlayLines, lines);
```

Per animation frame while typing, that is one `rows` array of N entries, one
`lines` array of N entries, an `O(N)` prefix/suffix walk (cheap — the record
strings are shared references from the line-record cache) and, on Markdown, a full
fence-aware `explorerMarkdownHeadingLevels()` pass over the document.

For a 20,000-line file this is on the order of 100k cheap operations per frame —
well under a millisecond, and orders of magnitude below the whole-document
Highlight.js pass it replaced, so this is not a defect. It is a gap between the
comment and the code, and it is the remaining `O(document)` term if the underlay
is ever pushed at larger buffers.

**Suggested fix.** None required. If it is ever measured as hot: cache the row
model on the draft identity the way `explorerCachedSourceRowModel()` already does
for the read-only view, and reuse `model.records` for `lines` instead of mapping
it.

---

**Confirmed, and left alone — which is what this finding asks for.** The
`O(document)` term is really there and is genuinely not a defect at the sizes
this path runs at. Caching the row model on the draft identity would trade a
measured non-problem for a second cache to keep coherent with a buffer that
changes on every keystroke.

---

## What was fixed

Applied 2026-08-20, after re-confirming each finding against the code. Every
fix carries a test that was checked to **fail with the fix reverted** — a test
added alongside a fix proves nothing until it has been seen to fail.

| # | Change | File |
|---|---|---|
| 1 | `applyExplorerSourceTier(pane, '')` in `renderExplorerCommitDiffFile()` and in `loadExplorerPane()`'s directory branch | `explorer-viewer.js` |
| 2 | `_explorerPreviewLoadInFlight = { path, content, promise }`; an identical load is joined, different bytes supersede | `explorer-viewer.js` |
| 3 | After `paintExplorerPreview()`, re-apply an active find with `scroll: false` when Preview is the shown panel | `explorer-viewer.js` |
| 4 | A sacrificial `\n` after the `<pre>` start tag in `explorerLargeSourceChunkHtml()` | `explorer-viewer.js` |
| 5 | `resolveTabView()` deletes `listScrollLeft`/`listScrollTop` in the branch that deletes `directory` | `explorer-scroll.js` |

Tests added — four cases, one harness fixture, one measure corrected:

| Test | Covers |
|---|---|
| `test_explorer_large_file_tier.py::test_a_commit_diff_does_not_inherit_the_previous_files_tier` | 1 |
| `test_explorer_scroll.py::test_the_first_preview_visit_costs_exactly_one_request` | 2 |
| `test_explorer_scroll.py::test_an_active_find_is_re_applied_to_the_preview_that_arrives` | 3 |
| `test_explorer_large_file_tier.py::test_a_blank_line_on_a_chunk_cut_survives_the_parser` | 4 |
| `test_explorer_scroll.py::test_a_new_listing_drops_both_spellings_of_the_old_listings_offset` | 5 |

Two harness changes support them. The large-file-tier harness gained a
`blanks` fixture and a parsed-text losslessness measure beside the markup one
(Finding 4), plus the DOM the commit-diff renderer needs (Finding 1). The
scroll adapter harness gained a `fetch` **call counter** — the existing
`fetches[]` queue is drained as each response resolves, so it cannot answer
"how many requests did this path issue".

No behaviour outside these five paths was changed, and no contract in
`CLAUDE.md` moved: the read-only explorer contract, the tier ceilings, the
persistence schema and the scroll policy's revision rules are all as they were.

### One correction to the review

Finding 2 states the double fetch happens "on every first visit"; the section's
trace runs through the find. It is in fact wider than the trace shows — the
no-query `else` branch of `applyExplorerSearch()` also calls
`restoreExplorerPreview()`, so the second request went out whether or not a
query was typed. The finding's headline was right and its trace was narrow.

### What was deliberately not done

* **Finding 6** — deferred by decision; see its section. It is the only finding
  in this review still open.
* **Finding 15** — confirmed and left alone, which is what it asks for.
* The **test gaps** listed under [Verification status](#verification-status)
  are closed.

---

## Findings 7–15

Applied 2026-08-20, in a second pass, on the same standard as Findings 1–5:
each finding re-confirmed against the code before anything was touched, and
each fix carrying a test that was **checked to fail with the fix reverted**.
Every finding held up. One claim inside Finding 7 did not, and is corrected in
its section: the sentence it attributes to `CLAUDE.md` is not in `CLAUDE.md`.

| # | Change | File |
|---|---|---|
| 7 | A failed highlight job tokenizes on this thread and re-renders, instead of caching a permanent "plain" miss; the third cache state goes with it | `explorer-viewer.js` |
| 8 | `explorerParsedDiffModel()` guards the lookup; the three call sites paint an explicit "renderer failed to load" message rather than throwing | `explorer-diff.js` |
| 9 | `sourceTierReason()` reports the ceiling that fired — lines or bytes — and pluralises | `explorer-tiers.js` |
| 10 | The dead `aside.hidden` term dropped from `explorerOverviewStoodDown()` | `explorer-overview.js` |
| 11 | Header rewritten: began as a pure move, since changed, and names its `explorer-worker-core.js` dependency | `explorer-diff.js` |
| 12 | One per-job failure is survived and respawns; the second disables. `terminate()` wired into pane teardown, unused `size` deleted | `explorer-worker-client.js`, `terminals.js` |
| 13 | Line-record cache sized at `2 x` live file panes (max 8) and released on teardown | `explorer-viewer.js`, `terminals.js` |
| 14 | Preview payload carries `state_revision`; the client declines a preview describing other bytes | `explorer.py`, `explorer-viewer.js` |
| 15 | None — confirmed as not a defect | — |

Tests added — seven cases across five suites:

| Test | Covers |
|---|---|
| `test_explorer_source_frame.py::test_a_failed_highlight_job_recolours_the_buffer_that_was_open` | 7 |
| `test_explorer_workers.py::test_a_missing_worker_core_degrades_the_diff_instead_of_throwing` | 8 |
| `test_explorer_tiers.py::test_the_notice_names_the_ceiling_that_actually_fired` | 9 |
| `test_explorer_overview.py::test_overview_stands_down_without_leaving_the_layout` (extended) | 10 |
| `test_explorer_workers.py::test_one_worker_failure_is_survived_and_the_second_disables_the_pool` | 12 |
| `test_explorer_source_frame.py::test_line_record_cache_is_sized_by_the_panes_and_is_given_back` | 13 |
| `test_api.py::test_explorer_file_preview_carries_the_revision_source_was_read_at` | 14 |
| `test_explorer_scroll.py::test_a_preview_describing_different_bytes_is_declined` | 14 |

Findings 11 and 15 carry no test by nature: 11 is a comment, and 15 is a
decision not to change code. Finding 11 was verified mechanically instead, by
re-running the per-function comparison against the merge base.

Two guardrails came out of this pass and are written into the **Regression
Guardrails** lists in `CLAUDE.md` and `AGENTS.md` — a page-wide lazy cache is
sized for its consumers and released when they go (Findings 12 and 13 are the
same defect twice), and one failed job is not a broken subsystem (Findings 7
and 12). Per the note at the top of this document, those rules live there and
are not cited back to this file.

---

## What was checked and found correct

Recorded so a later reader does not re-derive it.

**Backend (`web/explorer.py`, `web/api.py`).**

* `GIT_DIFF_CONTEXT_WIDTHS` is a name→width allowlist, resolved server-side; an
  unknown name raises rather than falling back, so a typo cannot serve the
  panel's `-U3` patch to a caller that asked for `zero`. No request value reaches
  argv. `_git_diff_args_for_mode()` places `--unified=N` correctly for all four
  modes, including `git show --format=`.
* `explorerDiffHunkStart()` handles the `-U0` pure-insertion hunk (`@@ -12,0 +13,3 @@`)
  by advancing the start, and correctly does **not** advance a `-0,0` new-file
  hunk. `tests/test_explorer_change_marks.py` executes the real parser against
  real Git output at both widths and asserts the marks are identical.
* `get_explorer_file_preview_payload()` uses the same `resolve_file` →
  root confinement → `stat` → bounded `read_explorer_file_preview` →
  binary check chain as the file payload, refuses non-Markdown, and adds no
  mutation. The read-only contract is unchanged; the route is a GET, so the
  cross-origin write guard is correctly not involved.
* `preview_type` is now an independent, always-present field rather than being
  derived from `preview_html is not None` — which is what keeps a save on a
  Markdown file from bailing `updateExplorerFileInPlace()` into a full rebuild.
  Both client readers were updated consistently, and no other consumer of
  `preview_html` exists.
* Guardrails 1 (security posture), 2 (locks and durable state) and 4 (subprocess
  bounds, `GIT_TERMINAL_PROMPT=0`) are untouched by this range.

**Worker boundary.**

* `explorer-worker.js` imports only `/static/vendor/highlight.min.js` and
  `/static/js/explorer-worker-core.js`, same-origin, carrying the page's
  cache-busting query string. No CDN, no build step (guardrail 3).
* `compactHighlightMarkup()`'s entity decoding is correct under nesting: source
  containing a literal `&amp;` round-trips, because the regex consumes
  `&amp;` left-to-right and leaves the remaining `amp;` alone. Escaped `<` cannot
  be mistaken for a span. Run coalescing is correctly prevented from crossing a
  line boundary, including across an empty line. `offset !== expectedSourceLength`
  is a genuine integrity check, and `lineRunStarts` is the standard
  `lines + 1` terminator form (verified by hand against `"a\nb"` and `"a\n"`).
* `decodeHighlightResult()` validates shape eagerly and materializes runs lazily,
  which is the property `HighlightLines.materialized` exists to let the tests
  observe.
* Abort terminates the running worker and removes its record so the pool can
  respawn; `_settle()` is idempotent and removes its own abort listener.

**Render machinery.**

* `explorerAbandonSourceRenderJob()` is called from all four places a build can
  stop without a successor — the editor mounting, the panel being replaced
  mid-flight, a suspended pane being closed, and `renderExplorerSource()`'s
  edit-mode guard — so the queued-reader strand described in the guardrails
  cannot recur. `explorerCancelSourceRenderJob()` deliberately does *not* flush,
  because the replacing build inherits the queue; the one synchronous path that
  has no successor flushes through `explorerFinishSourceRender()`.
* `renderExplorerFile()` and `updateExplorerFileInPlace()` both call
  `renderExplorerSource()` synchronously **before** queueing the scroll restore,
  so the restore is genuinely deferred behind a chunked build even when
  `applyExplorerSearch()` later yields. I traced this specifically because the
  ordering looks fragile; it is correct as written. (Finding 6 is about the third
  caller, which does not have this property.)
* `sourceRenderPlan()`'s ordering means a `decorate` can never run while a build
  is pending, so the single-row repaint path can never address rows that do not
  exist yet.
* `applyScrollMetrics()`'s exact-offset-first rule is consistent with
  `explorer-persistence.js`, which emits only `{scrollLeftRatio, scrollTopRatio,
  wasAtBottom}` for a restored record — so a persisted record still takes the
  proportional path, exactly as the comment claims.
* `clearExplorerChangeMarkGutter()` covers every attribute the gutter pass sets:
  `explorer-source-change-after` is only ever applied together with
  `data-explorer-change`, so the `[data-explorer-change]` selector is complete and
  the pass is idempotent.
* `explorerContentRevisionKey()`'s identity comparison is safe: `_explorerEntries`
  is always replaced, never mutated in place, at all three assignment sites.
* Abort slots are disjoint (`file`, `preview`, `diff`, `diffParse`, `highlight`,
  `editHighlight`, `changeMarks`); the editor's `PUT` save carries no signal and
  cannot be cancelled by a read.
* `Ctrl+F` degrades correctly in the large tier: no input element means
  `focusExplorerSearch()` returns `false`, so the handler does **not**
  `preventDefault()` and the browser's own find opens. (Finding 1 is the one place
  this breaks, and it breaks because the input is present.)

**Styling (guardrail 7).**

* Every new colour comes from a token (`--explorer-muted`, `--explorer-bar-bg`,
  `--explorer-border`, `--explorer-row-border`, `--explorer-text`); no palette
  literals were introduced.
* `--explorer-overview-ruler-width` is declared once, on `.explorer-editor-body`,
  and read by all three surfaces that must line up — the Source frame's grid
  track, the Preview scroller's reserved border, the Diff scroller's reserved
  border. The reserved-track rule (never `auto`) is implemented as documented,
  and the `map`-mode caveat is stated in the rule itself.

---

## Verification status

Re-run after the Findings 7–15 fixes (2026-08-20):

| Check | Result |
|---|---|
| `python tests/run_tests.py` | 1864 tests, OK (9 skipped) — 1852 at review time, 1857 after Findings 1–5, 1864 after Findings 7–15 |
| `python -m ruff check .` | clean |
| `node --check` on every changed module | clean |
| Each new test with its fix reverted | fails — checked one at a time |
| `node --check` on all new/changed JS | implicit — the Node-executed suites load and run every DOM-free module |
| Live browser | **still not run**, and it is still only Finding 6 that needs it. Findings 1–5 and 7–15 were verified against the code and are now regression-tested in Node. Finding 6 keeps its step-by-step **Verification scenario** with a console probe, a sized fixture and pass/fail criteria for each half. |

New behavioural cover added on this branch: `test_explorer_tiers.py`,
`test_explorer_large_file_tier.py`, `test_explorer_repaint.py`,
`test_explorer_scroll.py`, `test_explorer_workers.py`,
`test_explorer_change_marks.py`, plus four backend cases in `test_api.py`
(find bar follows the incoming file's tier, the named diff-context width, and the
two preview-route cases). The removals in `test_explorer_overview.py` and
`test_explorer_source_frame.py` are source-text assertions replaced with
behavioural ones, which is the direction `CLAUDE.md` asks for.

Gaps worth closing alongside the fixes — **all three closed 2026-08-20**, see
[What was fixed](#what-was-fixed):

* ~~No test observes the large tier's chunks through a parsed DOM (Finding 4),
  and no fixture puts a blank line at a chunk boundary.~~ Closed: a `blanks`
  fixture and a parsed-text measure. There is no DOM in this suite, so the one
  parser rule is modelled explicitly rather than left implied.
* ~~No test covers the Preview lazy-load path re-applying an active find
  (Finding 3) or requesting exactly once (Finding 2).~~ Closed: both, driven
  through the real loader in the scroll adapter harness.
* ~~No test covers the commit-diff view's find (Finding 1); `test_api.py`'s new
  tier/find-bar case covers `renderExplorerFile()` only.~~ Closed: the real
  `renderExplorerCommitDiffFile()` now runs in the large-file-tier harness.

---

## Suggested order of work

1. ~~**Finding 1** — one line, removes a dead control and a suppressed Ctrl+F.~~
   **Done.**
2. ~~**Findings 2 + 3** — same function; fix the in-flight join first, then the
   re-apply, and add the two missing tests.~~ **Done**, in that order.
3. ~~**Finding 4** — one character in the markup, plus a DOM-level fixture.~~
   **Done**; the fixture measures parsed text, modelling the parser rule
   explicitly because this suite has no DOM.
4. ~~**Finding 5** — one line in `resolveTabView()`, plus a Node case asserting
   the listing offset is dropped on a directory revision mismatch.~~ **Done.**
5. **Finding 6** — **deferred by decision.** Its verification scenario has not
   been run, and no fix has been applied. When it is picked up: Scenario B
   before Scenario A, because B destroys the stored offset outright while A
   only applies it short. The symmetry fix follows the scenario, not the other
   way round.
6. ~~**Findings 7–15**~~ **Done**, in a second pass — see
   [Findings 7–15](#findings-715). Eight fixed, Finding 15 confirmed as needing
   no change. Two of them turned out to be the same defect in two places
   (Findings 12 and 13, a page-wide cache sized for one consumer and never
   released), which is the pairing the new guardrail records.

Nothing here blocks the branch. Findings 1–5 were the ones a user could hit and
they are fixed; 7–15 were robustness, resource and accuracy items and are
closed. Only Finding 6 is open, and it is open on purpose: it needs a browser,
not a reading.
