# Explorer Performance Research

**Status:** Research and implementation proposal  
**Date:** 2026-08-15  
**Scope:** Explorer change detection, repository refreshes, large files, large
diffs, and hardware utilization

## Executive summary

GridVibe can become substantially more responsive, but the largest gains will
come from doing less work and rendering less DOM rather than simply allocating
more RAM.

The current implementation already avoids rereading unchanged open files and
avoids DOM updates when a Git semantic revision has not changed. The principal
remaining problems are broader:

1. One repository change can cause many directory requests, each of which may
   launch its own `git rev-parse` and `git status` processes.
2. Large source files still create one DOM row per line after syntax
   highlighting has been disabled.
3. Large diffs perform synchronous parsing, intraline matching, syntax
   highlighting, HTML insertion, and sometimes per-row layout measurement on
   the browser's main thread.
4. The server currently captures a complete Git diff before truncating the
   response to the documented client limit.
5. Markdown preview generation is eager, even when the user stays in Source
   view.

The recommended direction is a repository-scoped change coordinator backed by
native filesystem notifications and bounded RAM caches, combined with
viewport-virtualized source and diff rendering. Web Workers can use additional
CPU cores for parsing and highlighting, but they cannot build the DOM, so
workerization does not replace virtualization.

## Current behavior

### Change detection that already works well

Open files are polled through the cheap `GET /api/explorer/<id>/file/state`
route. The route resolves the file and performs one `stat`, without reading its
contents or running Git. The browser only rereads and rerenders a file when its
size/mtime token changes. See
[`explorer-git-watch.js`](../web/static/js/explorer-git-watch.js) and
[`get_explorer_file_state_payload()`](../web/explorer.py).

Git-backed explorer surfaces use `GET /api/explorer/<id>/git/state`. The route
computes a semantic revision, and an unchanged revision causes no DOM writes or
full repository refetch. Local checks start at five seconds, SSH checks at ten
seconds, and both use duration-aware backoff and churn damping.

This means GridVibe already satisfies part of the desired behavior: unchanged
open files are not reread, and unchanged Git state is not repainted. The
remaining inefficiency is the amount and breadth of work needed to establish
that a repository changed and to refresh it afterward.

### Repository refresh fan-out

`_get_git_context()` currently runs:

1. `git rev-parse --show-toplevel --is-inside-work-tree`
2. `git status --porcelain=v2 -z --branch --untracked-files=all`

The `/entries` route calls `_get_git_context()` for every requested directory.
After a repository revision changes, the quiet client refresh can reload the
current directory plus as many as 16 expanded tree nodes. Therefore one edit
can result in many directory listings and dozens of Git subprocesses, even
though all of those surfaces belong to the same repository generation.

Relevant code:

- [`_get_git_context()`](../web/explorer.py)
- [`get_explorer_entries()`](../web/api.py)
- [`refreshExplorerTreeQuiet()`](../web/static/js/explorer-viewer.js)
- [`explorerGitWatchCheckOne()`](../web/static/js/explorer-git-watch.js)

The current polling implementation also operates per pane. Multiple panes
looking at the same repository may independently establish the same Git state.

### Large source files

The backend permits a text preview of up to 10 MiB. Above approximately 2 MiB,
the browser disables syntax highlighting, which avoids one expensive step but
does not change the rendering model.

`renderExplorerSourceLines()` still:

1. Splits the entire content into line records.
2. Builds an HTML string containing one row per line.
3. Assigns the complete string to `innerHTML`.
4. Repeats the process for search, wrapping, Markdown folding, and content
   refreshes.

A large file may be represented simultaneously as:

- the response bytes and decoded JSON;
- the pane's raw content string;
- line-record objects;
- highlighted run maps;
- the generated HTML string;
- the resulting DOM nodes.

More RAM makes an out-of-memory failure less likely, but it does not prevent the
main thread from freezing while these representations are created and laid
out.

Relevant code:

- [`EXPLORER_FILE_PREVIEW_MAX_BYTES`](../web/explorer.py)
- [`EXPLORER_PLAIN_PREVIEW_THRESHOLD`](../web/static/js/explorer-viewer.js)
- [`renderExplorerSourceLines()`](../web/static/js/explorer-viewer.js)
- [`renderExplorerSource()`](../web/static/js/explorer-viewer.js)

### Large diffs

The backend bounds returned diffs to 256 KiB and 4,000 lines. The browser then
uses Diff2Html with:

- side-by-side output;
- word matching;
- character-level intraline diffing;
- syntax highlighting;
- a line-matching comparison limit.

Those safeguards reduce pathological matching work, but Diff2Html parsing,
HTML generation, Highlight.js, `innerHTML`, and layout all remain synchronous.
When line wrapping is enabled, `synchroniseExplorerDiffWrappedRows()` calls
`getBoundingClientRect()` across the rendered row set and assigns matching
heights, which can force expensive layout over thousands of rows.

The Source overview also fetches a HEAD diff automatically to calculate gutter
marks and change peeks, even if the user does not open the full Diff panel.

Relevant code:

- [`_bounded_git_diff()`](../web/explorer.py)
- [`explorerDiff2HtmlConfig()`](../web/static/js/explorer-viewer.js)
- [`renderExplorerDiffWithDiff2Html()`](../web/static/js/explorer-viewer.js)
- [`synchroniseExplorerDiffWrappedRows()`](../web/static/js/explorer-viewer.js)
- [`loadExplorerChangeMarks()`](../web/static/js/explorer-overview.js)

Diff2Html provides `diffMaxChanges`, `diffMaxLineLength`, and matching limits.
Its troubleshooting guidance recommends `matching: "none"` for large files or
slow execution.

### Server-side diff collection

`_bounded_git_diff()` truncates `result.stdout` after Git finishes. However,
the shared Git runner already supports `max_output_bytes`, and this option is
not currently passed by `_bounded_git_diff()`.

A very large diff may therefore be fully generated and captured in server
memory even though the response returns only the first 256 KiB. The output
should be bounded while it is being read, with one additional byte retained to
detect truncation.

### Eager Markdown work

`get_explorer_file_payload()` renders and sanitizes Markdown during the main
file request. Python-Markdown and Bleach therefore process the complete preview
even when the user only wants Source view. The returned document is then
inserted into the browser, code blocks are highlighted, and Mermaid blocks may
be rendered.

Large Markdown should use source-only mode initially. Preview generation should
begin only when Preview is selected and should be cached by file revision.

## Recommended target architecture

### Repository change coordinator

Introduce a service keyed by canonical repository root rather than by pane.
Each live explorer session registers interest in a root, and registrations are
reference-counted and removed during session teardown.

The coordinator should:

- maintain a monotonic generation per repository;
- batch filesystem events for approximately 100-300 ms;
- keep a bounded set of affected relative paths and change kinds;
- collapse overflow or ambiguous events into a full invalidation;
- allow at most one Git-status calculation per repository generation;
- cache the resulting Git context and parsed statuses;
- emit room-scoped Socket.IO invalidations after releasing all shared locks;
- retain a slow safety rescan and focus-time validation.

An emitted event should be an invalidation, not an authoritative file payload.
For example, it can identify a repository generation and affected paths. The
client then applies its existing revision and pane/session identity checks when
fetching the surfaces that are actually visible.

This preserves the current correctness boundary: filesystem events are hints,
while final state is still derived from `stat`, directory listing, and Git.

### Native local watching

[`watchfiles`](https://watchfiles.helpmanual.io/api/watch/) is the strongest
candidate for local roots:

- it uses the Rust `notify` implementation and native platform facilities;
- it supports Windows, Linux, and macOS;
- it yields already-batched sets of added, modified, and deleted paths;
- it exposes debounce, step, filtering, recursive watching, and a stop event;
- it automatically forces polling when running under WSL, where native
  notification behavior may not be reliable.

`watchdog` is a mature alternative with inotify, FSEvents, kqueue,
ReadDirectoryChangesW, and a polling fallback. It fits the current threaded
Flask-SocketIO runtime, but `watchfiles` provides batching and WSL behavior that
align especially well with this use case.

Native watching should not be treated as infallible. Watch overflow,
permission errors, network-mounted filesystems, and unsupported roots must fall
back to the current adaptive polling strategy.

SSH/SFTP roots should continue to poll unless GridVibe deliberately introduces
an optional remote helper. There is no portable SFTP watch facility, and
requiring tools such as `inotifywait` would make remote behavior platform- and
installation-dependent.

### Targeted client invalidation

The client should use the changed-path set to refresh only affected consumers:

- Reread the open file only when its path changed.
- Relist a directory only for create, delete, or rename events in that
  directory.
- Refresh only expanded tree nodes whose direct contents changed.
- Refresh the Git sidebar only when the repository semantic revision changed.
- Refresh Source gutter marks only when the open file's HEAD diff changed.
- Do nothing for invisible and unrelated paths.

The existing pane/session/path identity checks should remain. Superseded file,
directory, and diff requests should also be canceled with `AbortController` so
discarding a stale response does not leave its server and browser work running
unnecessarily.

### Bounded repository snapshot cache

RAM should be used for bounded, revision-keyed caches:

- repository discovery results;
- parsed Git status and branch metadata;
- directory result signatures or payloads;
- Markdown preview HTML;
- raw or parsed diff results;
- optionally, small file payloads shared by several panes.

Use single-flight computation: when several panes request the same stale
repository generation, one caller computes it and the others await the same
result.

A global LRU-style budget in the range of 64-128 MiB is a reasonable starting
point, but the correct default should be established by measurement. Cache
entries must be keyed by revisions or generations, bounded by count and total
bytes, and cleared when a session root becomes invalid.

### Optional Git filesystem monitor

Git's built-in filesystem monitor can speed `git status` in large working trees
by avoiding a complete disk scan. The untracked cache can reduce scanning for
new files further. See the
[`git update-index` filesystem-monitor documentation](https://git-scm.com/docs/git-update-index)
and [`git fsmonitor--daemon`](https://www.kernel.org/pub/software/scm/git/docs/git-fsmonitor--daemon.html).

GridVibe should not silently enable or modify repository configuration. It may
detect and report whether `core.fsmonitor` is active, or document it as an
optional user optimization. Correctness must never depend on it.

## Large-content rendering strategy

### Immediate large-file mode

Before introducing full virtualization, add a protective rendering mode based
on both byte and line count.

Above the threshold:

- render a single plain `<pre>` or bounded chunks instead of per-line rows;
- disable syntax highlighting, line decorations, Markdown folding, occurrence
  tint, and wrapping;
- preserve download and basic text search;
- show a clear notice explaining which presentation features were disabled;
- render Markdown Preview only on explicit request.

This does not provide ideal navigation for very large files, but it prevents
the worst DOM explosion with relatively low implementation risk.

### Viewport virtualization

The durable fix is to create DOM nodes only for the visible line window plus a
small overscan margin.

A custom virtualizer is possible but complex because GridVibe supports:

- line wrapping with variable row heights;
- Markdown section folding;
- search marks and active matches;
- Git gutter marks and change peeks;
- in-place editing and its highlight overlay;
- scroll restoration and persisted tab state.

[`CodeMirror 6`](https://codemirror.net/docs/guide/) should therefore be
evaluated before implementing a custom variable-height virtualizer. CodeMirror
only draws the visible viewport and supports decorations, gutters, search,
folding, editing, and read-only views. Its merge package also supports bounded
diff detail through `scanLimit` and `timeout`, plus collapsing unchanged
sections. See the [CodeMirror reference manual](https://codemirror.net/docs/ref/).

Adopting it would be a significant integration:

- assets must be pinned and self-hosted;
- GridVibe tokens and controls must remain authoritative;
- presentation persistence and revision guards must be preserved;
- current editor, search, fold, gutter, and overview behavior needs behavioral
  parity tests;
- a bundling step or committed browser bundle would be required.

A small proof of concept should compare CodeMirror with a custom fixed-height
virtualizer before choosing the final path.

### CSS containment as an interim measure

`content-visibility: auto` with an appropriate `contain-intrinsic-size` can
allow browsers to skip layout and paint for off-screen chunks. See
[MDN's `content-visibility` documentation](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/content-visibility).

It can reduce layout and paint work but does not avoid:

- building the complete HTML string;
- parsing all elements into the DOM;
- retaining every node in memory;
- forced layout caused by code such as `getBoundingClientRect()`.

It is therefore an interim or complementary optimization, not a replacement
for virtualization.

## Large-diff strategy

Use size-dependent presentation tiers.

### Small diffs

Retain the current side-by-side Diff2Html presentation, word matching,
character-level emphasis, and syntax highlighting.

### Medium diffs

Keep structured presentation but switch to cheaper options:

- `matching: "none"`;
- `highlight: false`;
- no wrapped-row synchronization;
- optionally line-by-line rather than side-by-side output;
- explicit `diffMaxChanges` and `diffMaxLineLength` limits.

### Large or truncated diffs

Use a virtualized or plain unified-diff presentation with progressive chunks.
Avoid thousands of simultaneously mounted table rows. Preserve a clear
truncation notice and allow the user to request another bounded section rather
than attempting to draw everything at once.

The current Diff2Html documentation explicitly identifies big files and big
lines as causes of slow execution or out-of-memory behavior and recommends
disabling its matching algorithm.

### Separate change metadata from full diff text

Source gutter marks do not initially require a complete highlighted diff.
Introduce a lightweight change-metadata path using zero-context hunks, such as
`git diff --unified=0`, and return bounded hunk coordinates.

The full hunk text can be fetched when the user opens a change peek or the Diff
panel. This removes automatic full-diff parsing from the common Source-open
path.

### Bound Git output during collection

Pass `EXPLORER_GIT_DIFF_MAX_BYTES + 1` to the existing `max_output_bytes`
mechanism. The additional byte determines whether the result was truncated.
Line truncation can then be applied to the already bounded byte window.

This prevents a large Git diff from consuming unbounded server memory and
subprocess pipe time merely to return the documented 256 KiB response.

## Hardware utilization

### RAM

RAM is valuable for repository snapshots, revision-keyed Markdown previews,
diff results, and small file payloads. It should not be used to retain unbounded
copies of every visited file or every historical repository generation.

The largest browser-side memory improvement will come from virtualization,
which removes the raw-content-to-HTML-to-DOM multiplication for off-screen
lines.

### CPU cores

The browser UI runs on one main thread. Web Workers run JavaScript in separate
threads and can keep laborious parsing away from input and painting. See the
[Web Workers API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API).

Good worker candidates include:

- Highlight.js tokenization;
- parsing unified diff text into a compact model;
- source search indexing;
- Markdown structure analysis;
- line-offset indexing for large files.

Highlight.js explicitly documents Web Worker use for large code blocks:
[Highlight.js documentation](https://highlightjs.readthedocs.io/en/latest/readme.html).

Workers cannot manipulate the DOM. Diff2Html HTML insertion, row creation, and
layout would still freeze the UI if the complete result were committed in one
operation. A shared worker pool should therefore be combined with
virtualization, not used as a substitute for it.

A reasonable browser pool policy is to reserve one logical CPU for the UI and
cap worker count at a small value, for example:

```text
min(max(navigator.hardwareConcurrency - 1, 1), 4)
```

On the server, Git already runs in external processes and can use OS scheduling
outside Python's GIL. Independent repository jobs may run concurrently through
a small global semaphore, but work for the same repository should be
serialized and coalesced. Starting many Git processes against one disk is
usually slower than sharing one result.

Python-Markdown and Bleach work should first be made lazy and cached. A
long-lived process pool could use additional cores if profiling still shows
Markdown sanitization as a dominant cost, but Windows process startup and
serialization make this a later optimization.

### Storage

An SSD helps Git and directory scans, but native change notifications and a
shared repository snapshot remove far more work than faster storage alone.
Network and SSH roots remain latency-sensitive and benefit particularly from
request coalescing and caching.

### GPU

GPU acceleration offers little direct benefit. The observed costs are string
processing, Git status, syntax parsing, DOM creation, and layout. Browsers
already use the GPU for suitable compositing and paint work. A custom canvas or
WebGL text renderer could use the GPU but would sacrifice accessibility,
selection, native search, and maintainability without addressing server-side
work.

## Measurement plan

Optimization should begin with an explicit baseline.

### Browser measurements

Add `performance.mark()` and `performance.measure()` around:

- fetch and JSON decoding;
- source tokenization;
- source HTML/model creation;
- DOM commit and first animation frame;
- Diff2Html construction and `draw()`;
- syntax highlighting;
- wrapped-row synchronization;
- Markdown and Mermaid rendering.

Use `PerformanceObserver` for long tasks where supported. Record aggregate
durations, sizes, line counts, and node counts without logging paths or file
contents.

### Server measurements

Measure:

- repository discovery and `git status` separately;
- Git subprocess count per visible user action;
- directory listing time;
- file `stat`, read, decode, Markdown render, and sanitization;
- diff execution and bytes captured;
- cache hits, misses, evictions, and single-flight waiters.

`Server-Timing` headers are useful during development because they expose the
breakdown in browser developer tools without adding a high-frequency production
log stream.

### Benchmark fixtures

Include at least:

- a repository with many tracked files and several thousand changes;
- a repository with a large untracked tree;
- a 10 MiB file containing many short lines;
- a 10 MiB file containing a few extremely long lines;
- a large Markdown document with code and Mermaid blocks;
- a 256 KiB/4,000-line diff;
- a diff containing extremely long changed lines;
- an SSH explorer under realistic latency;
- two or more panes viewing the same repository.

### Suggested acceptance targets

- An unchanged local repository launches zero periodic `git status` processes.
- One debounced repository event burst causes at most one Git-status
  calculation per root.
- A changed path refreshes only directly affected visible surfaces.
- Source and diff DOM node counts are bounded by viewport size rather than file
  length.
- A 10 MiB source file and the maximum-size diff do not produce a UI task
  longer than approximately 150-200 ms.
- A loading or degraded presentation appears before any unavoidable expensive
  work begins.
- Cache memory is bounded and observable.
- Hidden pages perform no accumulated catch-up work.
- SSH polling remains adaptive, coalesced, and failure-bounded.

## Implementation sequence

### Phase 0: baseline and safety limits

1. Add browser and server measurements.
2. Add benchmark fixtures and acceptance checks.
3. Bound Git diff stdout during collection.
4. Add a protective plain large-file mode.
5. Add dynamic large-diff degradation.
6. Make Markdown Preview lazy for large documents.
7. Cancel superseded fetch and render jobs.

Any functional change to the Diff view must first satisfy the architecture
guardrail that extracts `explorer-diff.js`. The extraction is a pure move:
`test_explorer_source_frame.py` and `test_explorer_overview.py` must continue to
pass untouched.

### Phase 1: shared repository state

1. Cache repository discovery.
2. Introduce a repository-scoped snapshot and generation.
3. Add single-flight Git-status calculation.
4. Reuse one parsed status result across directory and sidebar consumers.
5. Add strict memory and lifetime bounds.

### Phase 2: event-driven local refresh

1. Add the local filesystem watcher registry.
2. Debounce and normalize path changes.
3. Push room-scoped invalidations through Socket.IO.
4. Refresh only affected client surfaces.
5. Preserve slow safety polling, watcher-overflow recovery, and SSH polling.

### Phase 3: worker offloading

1. Move Highlight.js work into a shared Web Worker pool.
2. Parse large diff text into a compact model in a worker.
3. Move large-file search/index work off the main thread.
4. Keep render tokens and cancellation checks across every async boundary.

### Phase 4: viewport virtualization

1. Build a CodeMirror proof of concept for Source and Diff.
2. Compare it with a small custom virtualized renderer.
3. Verify editor, folding, search, gutter, overview, wrapping, and persistence
   parity.
4. Adopt the selected renderer behind behavioral tests.

## Recommendation

The first implementation pass should not begin with more worker threads or a
larger cache. It should establish measurements, prevent unbounded diff capture,
introduce safe large-content degradation, and collapse repeated Git work into
one repository snapshot.

After that foundation, native change events and targeted Socket.IO
invalidations can remove nearly all unchanged local polling. Viewport
virtualization then addresses the remaining source and diff freezes at their
root. RAM caches and CPU workers become effective once the architecture ensures
they are accelerating bounded, non-duplicated work.
