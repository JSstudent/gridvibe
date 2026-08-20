/* GridVibeExplorerTiers — how big is this, and what gets turned off.

   One place answers that question for both surfaces that can freeze the pane:
   the Source view's per-line rows and the Diff view's Diff2Html render. Both
   degrade on size, both have to tell the reader what they gave up, and both
   used to have no ceiling at all — a 10 MiB log built a million DOM nodes, and
   a maximum-size diff ran word matching and character-level intraline diffing
   over 4,000 lines synchronously.

   DOM-free and require()-able from Node, so the thresholds, the ladder and the
   notice each tier carries are executed by tests rather than asserted as
   source text. The paint-only adapters live where the surface does:
   explorer-viewer.js for Source, explorer-diff.js for Diff.

   The thresholds are constants here, deliberately not config.json keys: a
   setting nothing reads is dead weight (guardrail 5), and these are a
   protective floor rather than a preference — a user who raises them is asking
   for the freeze back.

   Relationship to EXPLORER_PLAIN_PREVIEW_THRESHOLD (explorer-viewer.js): that
   is the older and much lighter degradation — above 2 MiB the rows are still
   built one per line, they just skip Highlight.js. It stays exactly as it was,
   and the `large` tier below is the presentation tier that finally stops
   building rows at all. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerTiers = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Two ceilings, because there are two independent ways to freeze on a
       source file and neither implies the other.

       Lines drive the DOM: every line is a row <div> plus a gutter <span> plus
       a <code>, so 200k lines is 600k elements before a single highlight token
       is counted. 20,000 lines is roughly 60k elements — a large real file
       (this repository's own biggest is ~8k) still renders in full, and the
       generated and logged files that do not are the ones this exists for.

       Bytes drive tokenization, string assembly and the innerHTML parse, none
       of which care how the bytes are distributed: a 6 MiB single-line
       minified bundle is one row and still blocks the thread for seconds.

       So it is either, not both. A file that trips one ceiling has a real
       problem whether or not it trips the other. */
    const SOURCE_LARGE_MAX_LINES = 20000;
    const SOURCE_LARGE_MAX_BYTES = 4 * 1024 * 1024;

    /* The large tier's unit of DOM. The backend caps a preview at 10 MiB, so
       even a pathological file lands in a bounded few dozen chunks — the node
       count stops tracking the file's size, which is the whole point. Chunking
       rather than one giant text node keeps each layout unit small enough that
       a browser can lay it out incrementally. */
    const SOURCE_LARGE_CHUNK_LINES = 5000;

    /* And the same unit measured in bytes, because lines do not bound it. The
       byte ceiling above exists for the file that is one enormous line — a
       minified bundle — and such a file has no newline to cut at, so a
       line-only chunker handed the whole 6 MiB back as a single chunk: the
       tier degraded the view without removing the freeze it degraded it for.
       A cut inside a line is visible (two <pre> blocks are two block boxes),
       so it is the last resort and is never taken while a newline boundary is
       still within budget. */
    const SOURCE_LARGE_CHUNK_BYTES = 256 * 1024;

    /* The Diff ladder sits under the backend's own truncation ceiling
       (EXPLORER_GIT_DIFF_MAX_BYTES / _MAX_LINES = 256 KiB / 4,000 lines), so
       `large` is a band, not an overflow: a diff that reaches the ceiling is
       truncated *and* rendered through the large tier. */
    const DIFF_SMALL_MAX_BYTES = 64 * 1024;
    const DIFF_SMALL_MAX_LINES = 1000;
    const DIFF_MEDIUM_MAX_BYTES = 160 * 1024;
    const DIFF_MEDIUM_MAX_LINES = 2500;

    function count(value) {
        const number = Number(value);
        return Number.isFinite(number) && number > 0 ? number : 0;
    }

    /* Lines without allocating them. `String.split` on a 10 MiB buffer builds
       200k strings purely to throw them away, on the very path that is
       supposed to be deciding whether this file is too big to touch. */
    function lineCount(content) {
        const text = typeof content === 'string' ? content : '';
        if (!text) {
            return 0;
        }
        let lines = 1;
        let cursor = text.indexOf('\n');
        while (cursor !== -1) {
            lines += 1;
            cursor = text.indexOf('\n', cursor + 1);
        }
        // A trailing newline ends the last line rather than starting an empty
        // one — this is the count a reader would recognise, and it is what the
        // notice reports. It is deliberately *not* what the tier decides on:
        // see rowCount().
        return text.endsWith('\n') ? lines - 1 : lines;
    }

    /* How many rows the per-line renderer would actually build, which is what
       the tier is protecting against. explorerSourceLineRecords() emits one
       record per newline *plus* a final record for whatever follows the last
       one — so a file ending in a newline gets a trailing empty row, and even
       an empty buffer gets one. Deciding the tier on the reader-facing count
       instead would be off by one at exactly the boundary, which is the only
       place the number is ever consulted. */
    function rowCount(content) {
        const text = typeof content === 'string' ? content : '';
        let rows = 1;
        let cursor = text.indexOf('\n');
        while (cursor !== -1) {
            rows += 1;
            cursor = text.indexOf('\n', cursor + 1);
        }
        return rows;
    }

    function sourceMetrics(content) {
        const text = typeof content === 'string' ? content : '';
        return { bytes: text.length, lines: lineCount(text), rows: rowCount(text) };
    }

    /* 'full' — rows, exactly as before (highlighted or not; that is the
       separate 2 MiB plain threshold's business).
       'large' — bounded plain chunks and the notice below. */
    function sourceTier(metrics) {
        const bytes = count(metrics && metrics.bytes);
        // Rows, not the reader-facing line count — see rowCount(). Metrics
        // that carry only `lines` (a caller reasoning in whole lines) fall
        // back to it rather than silently reading 0.
        const rows = count(metrics && (metrics.rows === undefined ? metrics.lines : metrics.rows));
        return bytes > SOURCE_LARGE_MAX_BYTES || rows > SOURCE_LARGE_MAX_LINES
            ? 'large'
            : 'full';
    }

    function sourceTierForContent(content) {
        return sourceTier(sourceMetrics(content));
    }

    function formatCount(value) {
        return String(count(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    /* Kept here rather than borrowed from the viewer's formatExplorerSize() so
       the policy stays DOM-free and Node-executable. Same units, same
       one-decimal rounding. */
    function formatBytes(value) {
        const bytes = count(value);
        if (bytes >= 1024 * 1024) {
            return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
        }
        if (bytes >= 1024) {
            return `${(bytes / 1024).toFixed(1)} KB`;
        }
        return `${formatCount(bytes)} bytes`;
    }

    /* Why *this* file is in the tier, in the reader's terms. The two ceilings
       are independent, so reporting lines unconditionally described the wrong
       file exactly when the byte ceiling was the one that fired: a 5 MiB
       minified bundle is one line, and "1 lines rendered as plain text" is
       both ungrammatical and an answer to a question nobody asked — the
       reader's problem there is 5 MiB on one line. Lines are named first when
       both ceilings are crossed, because the row count is what the reader can
       see on screen. */
    function sourceTierReason(metrics) {
        const bytes = count(metrics && metrics.bytes);
        const lines = count(metrics && metrics.lines);
        const rows = count(metrics && (metrics.rows === undefined ? metrics.lines : metrics.rows));
        if (rows > SOURCE_LARGE_MAX_LINES) {
            return `${formatCount(lines)} ${lines === 1 ? 'line' : 'lines'}`;
        }
        return formatBytes(bytes);
    }

    /* What the reader is told. Every capability the tier removes is named — an
       unexplained missing gutter reads as a bug, and a missing Find reads as a
       broken one.

       Find is stated on its own line rather than buried in the list because it
       is the only *control* that goes away rather than a decoration. It is
       genuinely unavailable here: the offset-range machinery the Source find
       is built on addresses per-line rows that this tier does not create, and
       the plain-element path the Preview and Diff views use walks its whole
       subtree unbounded — which on a 10 MiB buffer is the freeze the tier
       exists to remove. Shipping a find that reintroduced it would be worse
       than not shipping one, so this says so plainly instead. */
    function sourceTierNotice(metrics) {
        const bytes = count(metrics && metrics.bytes);
        const lines = count(metrics && metrics.lines);
        // Decided on the same metrics the switch reads, `rows` included.
        // Re-deriving the tier from `lines` alone silently handed sourceTier()
        // the reader-facing count through its fallback, and the two disagree by
        // exactly one at exactly this boundary — so a file of precisely
        // SOURCE_LARGE_MAX_LINES lines rendered as plain chunks with the gutter,
        // the marks and the find all gone and nothing on screen saying why.
        if (sourceTier(metrics || { bytes, lines }) !== 'large') {
            return null;
        }
        return {
            title: 'Large file view',
            detail: `${sourceTierReason(metrics || { bytes, lines })} rendered as plain text so the pane stays responsive.`,
            disabled: [
                'syntax highlighting',
                'line numbers',
                'section folding',
                'change marks',
                'the overview ruler'
            ],
            findNote: 'Find is unavailable in this view.',
            retained: 'Download and Edit still work.'
        };
    }

    /* Whether a capability is offered at all in a tier. One predicate, so the
       notice and the code that switches the capability off cannot drift apart
       and promise different things. */
    function sourceTierAllows(tier, capability) {
        if (tier !== 'large') {
            return true;
        }
        return capability === 'download' || capability === 'edit';
    }

    /* The large tier's content, split into its bounded chunks. Text, not
       markup — the adapter escapes and wraps, this only decides where the cuts
       fall — and the cuts are found by walking newlines rather than by
       splitting the buffer into lines, because materializing 200k throwaway
       strings is the sort of thing this tier exists to stop doing.

       A chunk ends at whichever ceiling it reaches first, lines or bytes, so
       both of the ways a file gets too big to paint in one pass are bounded.
       Cuts fall on newline boundaries wherever one is still within the byte
       budget; only a single line longer than a whole chunk is cut mid-line.

       Chunks are contiguous slices and the walk never moves backwards, so
       joining the result reproduces the input byte for byte. */
    function sourceChunks(content) {
        const text = typeof content === 'string' ? content : '';
        const chunks = [];
        let start = 0;
        while (start < text.length) {
            const limit = Math.min(text.length, start + SOURCE_LARGE_CHUNK_BYTES);
            let cursor = start;
            for (let seen = 0; seen < SOURCE_LARGE_CHUNK_LINES; seen += 1) {
                const next = text.indexOf('\n', cursor);
                if (next === -1 || next + 1 > limit) {
                    break;
                }
                cursor = next + 1;
            }
            /* No newline boundary left inside the budget: either what remains
               is a short unterminated tail, or one line is longer than a whole
               chunk. Both end at the limit — the end of the buffer in the
               first case, a mid-line cut in the second. */
            if (cursor === start) {
                cursor = limit;
            }
            chunks.push(text.slice(start, cursor));
            start = cursor;
        }
        return chunks;
    }

    /* 'small'  — the current configuration, unchanged.
       'medium' — diff2html without word matching, character-level intraline
                  emphasis or syntax colour. Rows, line numbers, side-by-side
                  and the undo buttons all survive; only the emphasis goes.
       'large'  — GridVibe's own side-by-side renderer with no language, so
                  highlighting reduces to escaping. Chosen over a plain <pre>
                  precisely because it keeps .explorer-diff-row, and per-line
                  and per-block undo are wired onto those rows: a plain <pre>
                  would silently remove a mutation affordance from exactly the
                  diffs where it is most wanted. */
    function diffTier(metrics) {
        const bytes = count(metrics && metrics.bytes);
        const lines = count(metrics && metrics.lines);
        if (bytes > DIFF_MEDIUM_MAX_BYTES || lines > DIFF_MEDIUM_MAX_LINES) {
            return 'large';
        }
        if (bytes > DIFF_SMALL_MAX_BYTES || lines > DIFF_SMALL_MAX_LINES) {
            return 'medium';
        }
        return 'small';
    }

    function diffTierForContent(diff) {
        const text = typeof diff === 'string' ? diff : '';
        return diffTier({ bytes: text.length, lines: lineCount(text) });
    }

    /* The degraded diff2html configuration for the medium tier. These three
       keys degrade *gracefully* inside diff2html, unlike diffMaxChanges and
       diffMaxLineLength, which are a refusal lever: exceeding either sets
       isTooBig, empties the blocks and renders "Diff too big to be displayed"
       instead of a diff — and GridVibe's fallback guard still finds a
       .d2h-file-wrapper afterwards, so it would not even fall through to the
       handwritten renderer. They are deliberately not used here. */
    function diffTierConfigOverrides(tier) {
        if (tier !== 'medium') {
            return {};
        }
        return { matching: 'none', diffStyle: 'line', highlight: false };
    }

    function diffTierNotice(tier) {
        if (tier === 'medium') {
            return {
                title: 'Large diff',
                detail: 'Intraline emphasis and syntax colour are off so the diff stays responsive.'
                    + ' Side-by-side, line numbers and undo are unchanged.'
            };
        }
        if (tier === 'large') {
            return {
                title: 'Very large diff',
                detail: 'Rendered without syntax colour or intraline emphasis so the diff stays'
                    + ' responsive. Side-by-side, line numbers and undo are unchanged.'
            };
        }
        return null;
    }

    return {
        SOURCE_LARGE_MAX_LINES,
        SOURCE_LARGE_MAX_BYTES,
        SOURCE_LARGE_CHUNK_LINES,
        SOURCE_LARGE_CHUNK_BYTES,
        DIFF_SMALL_MAX_BYTES,
        DIFF_SMALL_MAX_LINES,
        DIFF_MEDIUM_MAX_BYTES,
        DIFF_MEDIUM_MAX_LINES,
        lineCount,
        rowCount,
        sourceMetrics,
        sourceTier,
        sourceTierForContent,
        sourceTierReason,
        sourceTierNotice,
        sourceTierAllows,
        sourceChunks,
        diffTier,
        diffTierForContent,
        diffTierConfigOverrides,
        diffTierNotice
    };
}));
