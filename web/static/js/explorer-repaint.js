/* GridVibeExplorerRepaint — what a re-render may skip, and which rows it has
   to touch when it cannot skip.

   The Source view rebuilt every row from scratch on every interaction: a
   search keystroke, a step to the next match, a Markdown fold, entering and
   leaving the editor, and — twice over — every file open, because
   renderExplorerFile() renders the rows and then calls applyExplorerSearch(),
   which renders them again. On a large file each of those is one
   uninterruptible task: whole-document tokenization, a multi-MB HTML string,
   and an innerHTML parse over tens of thousands of elements.

   Almost none of that work is needed. Stepping from match 5 to match 6 changes
   two lines. A repaint with no query at all changes nothing. So the decisions
   are:

   * `skip` — the surface on screen already says what this render would say;
   * `decorate` — the line *text* is unchanged and only the search marks moved,
     so repaint exactly the rows whose marks differ;
   * `full` — content, language or the fold set moved, and the rows have to be
     rebuilt.

   The moving in-place-editor draft gets the same treatment from the other end:
   a keystroke changes one line, so `lineSplicePlan()` names the rows that
   actually differ and the underlay replaces those instead of re-tokenizing and
   re-emitting the whole document sixty times a second.

   DOM-free and require()-able from Node, so every rule here is executed by
   tests rather than asserted as source text. The callers hold the DOM:
   explorer-viewer.js for the Source rows, explorer-edit-overlay.js for the
   editor's underlay. Nothing here reads or writes the document, and nothing
   here is persisted. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerRepaint = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The floor under "how many rows may a decoration repaint touch". It is a
       floor and not the whole answer because the trade is relative: m single
       row parses against one parse of the *whole* document, so what a 600-row
       file should rebuild a 20,000-row file should not.

       The ceiling therefore scales with the document (see repaintCeiling), and
       this constant is what a small one gets. Getting the balance wrong the
       other way was the defect: a find on a large file matched more than 400
       lines on its first two keystrokes, so every one of them rebuilt tens of
       thousands of rows — through the frame-sliced build, losing the scroll
       position and the selection each time. */
    const MAX_DECORATION_REPAINT_ROWS = 400;

    /* Above the floor a repaint may touch this fraction of the document. A
       quarter is deliberately below break-even per row: the bulk build parses
       one string and the repaint parses m, so the repaint has to be clearly
       smaller to be worth it — and the find's own 1,000-match cap keeps every
       real query far under this on the files where it matters. */
    const DECORATION_REPAINT_DOCUMENT_SHARE = 4;

    /* The same trade for the editor's underlay. A keystroke moves one line and
       a paste moves a handful; a change spanning more rows than this is a
       different document, and rebuilding it in one pass beats splicing it row
       by row. */
    const MAX_SPLICE_ROWS = 200;

    /* Rows per animation frame in a chunked build, and the row count below
       which chunking is not worth its own bookkeeping. A file under the floor
       renders in one synchronous pass exactly as it always did — which is also
       what keeps every caller that reads the rows the instant the render
       returns correct for the overwhelming majority of files. */
    const CHUNK_ROWS = 2000;
    const CHUNK_MIN_ROWS = 4000;

    function toMap(value) {
        if (value instanceof Map) {
            return value;
        }
        const map = new Map();
        if (value && typeof value === 'object') {
            Object.keys(value).forEach(key => map.set(Number(key), String(value[key])));
        }
        return map;
    }

    /* Which lines carry which search marks, as a per-line key that compares by
       value. Offsets are stored *relative to the line* so that two renders of
       the same line with the same marks produce the same key regardless of
       where the line sits in the file — the point being that a decoration
       change on line 12 must not repaint line 4,000.

       Ranges are the find's own: `{ start, end, active }` over content
       offsets, ascending and non-overlapping. A range spanning a newline
       legitimately belongs to both lines, so both are keyed. `active` is part
       of the key because stepping to the next match is a repaint of exactly
       two rows — the one that stopped being active and the one that started. */
    function decorationMap(records, ranges) {
        const rows = Array.isArray(records) ? records : [];
        const marks = Array.isArray(ranges) ? ranges : [];
        const map = new Map();
        if (!rows.length || !marks.length) {
            return map;
        }

        let cursor = 0;
        for (let row = 0; row < rows.length; row += 1) {
            const record = rows[row] || {};
            const lineStart = Number(record.start) || 0;
            const lineEnd = row + 1 < rows.length
                ? Number(rows[row + 1].start) || 0
                : Infinity;
            while (cursor < marks.length && Number(marks[cursor].end) <= lineStart) {
                cursor += 1;
            }
            let key = '';
            for (let mark = cursor; mark < marks.length; mark += 1) {
                const range = marks[mark] || {};
                const start = Number(range.start) || 0;
                if (start >= lineEnd) {
                    break;
                }
                if (Number(range.end) <= lineStart) {
                    continue;
                }
                key += `${start - lineStart}:${Number(range.end) - lineStart}${range.active ? ':a' : ''}|`;
            }
            if (key) {
                map.set(Number(record.number), key);
            }
        }
        return map;
    }

    /* The rows whose marks differ between two decoration maps — the union of
       "gained marks", "lost marks" and "same marks, different active", sorted
       so the caller walks the document in one direction. */
    function decorationDelta(previous, next) {
        const before = toMap(previous);
        const after = toMap(next);
        const lines = new Set();
        before.forEach((value, line) => {
            if (after.get(line) !== value) {
                lines.add(line);
            }
        });
        after.forEach((value, line) => {
            if (before.get(line) !== value) {
                lines.add(line);
            }
        });
        return Array.from(lines).sort((a, b) => a - b);
    }

    /* The Source view's render decision.

       `sameSurface` is the caller's proof that the rows it last rendered are
       still the rows on screen — the element identity, not a hash of what was
       put into it. Anything that replaced the panel (the editor mounting, a
       tab switch, a rebuilt card) makes it false, so a skip can never leave an
       empty pane behind.

       `pending` — a frame-sliced build still emitting rows — is judged against
       what the render would *change*. The slices not yet emitted carry the
       ranges that build started from, so a render that moves the marks has to
       start again; a render that moves nothing is already being painted, and
       rebuilding it is pure duplicate work. */
    function repaintCeiling(options) {
        if (Number.isFinite(options.maxRepaintRows)) {
            return Number(options.maxRepaintRows);
        }
        const rows = Number(options.rowCount);
        if (!Number.isFinite(rows) || rows <= 0) {
            return MAX_DECORATION_REPAINT_ROWS;
        }
        return Math.max(
            MAX_DECORATION_REPAINT_ROWS,
            Math.floor(rows / DECORATION_REPAINT_DOCUMENT_SHARE)
        );
    }

    function sourceRenderPlan(input) {
        const options = input || {};
        if (
            !options.sameSurface
            || options.contentChanged
            || options.languageChanged
            || options.foldsChanged
        ) {
            return { mode: 'full', lines: [] };
        }
        const lines = decorationDelta(options.previousDecorations, options.nextDecorations);
        if (!lines.length) {
            /* Nothing this render would paint differs from what the surface
               already says — and that holds whether the rows are all on screen
               or a frame-sliced build is still emitting them, because the
               build was started with these exact ranges and will finish with
               them. Rebuilding here is what made every large file open build
               its rows twice: renderExplorerFile() renders, then
               applyExplorerSearch() renders again with nothing to add. */
            return { mode: 'skip', lines: [] };
        }
        if (options.pending) {
            /* The marks did move, and the slices this build has not emitted
               yet carry the ranges it started from. Half the document would
               be marked and half would not, so it starts again. */
            return { mode: 'full', lines: [] };
        }
        if (lines.length > repaintCeiling(options)) {
            return { mode: 'full', lines: [] };
        }
        return { mode: 'decorate', lines };
    }

    /* The moved-draft decision, as one contiguous splice: the run of lines
       between the common prefix and the common suffix. Typing appends to one
       line (`removed === inserted === 1`), Enter splits one into two, and a
       paste replaces a handful — all of which are a few rows out of tens of
       thousands.

       A first paint (no previous lines) and a change too wide to splice both
       answer `full`, which is the renderer the caller already had. */
    function lineSplicePlan(previous, next, options) {
        const after = Array.isArray(next) ? next : [];
        if (!Array.isArray(previous)) {
            return { mode: 'full', start: 0, removed: 0, inserted: 0 };
        }
        const before = previous;
        const shorter = Math.min(before.length, after.length);
        let prefix = 0;
        while (prefix < shorter && before[prefix] === after[prefix]) {
            prefix += 1;
        }
        if (prefix === before.length && prefix === after.length) {
            return { mode: 'none', start: prefix, removed: 0, inserted: 0 };
        }
        let suffix = 0;
        while (
            suffix < shorter - prefix
            && before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
        ) {
            suffix += 1;
        }
        const removed = before.length - prefix - suffix;
        const inserted = after.length - prefix - suffix;
        const ceiling = Number.isFinite(options && options.maxSpliceRows)
            ? Number(options.maxSpliceRows)
            : MAX_SPLICE_ROWS;
        if (removed + inserted > ceiling) {
            return { mode: 'full', start: 0, removed: 0, inserted: 0 };
        }
        return { mode: 'splice', start: prefix, removed, inserted };
    }

    /* The settle pass's decision: the underlay's rows already show this draft,
       so all a real Highlight.js answer can change is their *colour*.

       `previousKeys` describes the rows currently in the DOM — one entry per
       row, in row order, `null` where the row was painted by the per-line
       fallback lexer and its real colour is therefore unknown. `nextKeys`
       describes what the answer would paint. For an ordinary keystroke
       Highlight.js re-tokenizes the whole document and emits byte-identical
       runs for every line but the edited one, so the delta is the one or two
       rows the splice inserted.

       `full` for a delta that covers every row, because repainting m cells one
       at a time is only worth it against one parse of the whole document — at
       m = every row the bulk write is strictly cheaper. Above the
       document-scaled ceiling (`repaintCeiling`, shared with the Source view's
       decoration repaint) the same trade tips the same way: typing a `/*` at
       the top recolours the tail and rebuilds in one pass, exactly as it does
       today. Anything that cannot be compared — a missing or wrong-length key
       array — is `full` as well, which is the renderer the caller already had. */
    function highlightRepaintPlan(input) {
        const options = input || {};
        const previous = options.previousKeys;
        const next = options.nextKeys;
        const rows = Number(options.rowCount);
        if (!Array.isArray(previous) || !Array.isArray(next)
            || !Number.isFinite(rows) || rows <= 0
            || previous.length !== rows || next.length !== rows) {
            return { mode: 'full', lines: [] };
        }
        const lines = [];
        for (let at = 0; at < rows; at += 1) {
            const before = previous[at];
            if (before === null || before === undefined || before !== next[at]) {
                lines.push(at + 1);
            }
        }
        if (!lines.length) {
            return { mode: 'skip', lines: [] };
        }
        if (lines.length >= rows || lines.length > repaintCeiling(options)) {
            return { mode: 'full', lines: [] };
        }
        return { mode: 'repaint', lines };
    }

    /* How a full build is paced. Above the floor the rows are emitted in
       frame-sized slices so the file fills in from the top while the rest of
       the app keeps painting; below it the build stays one synchronous pass,
       because a render that returns before its rows exist is a contract change
       for every caller that reads them immediately afterwards, and that is not
       a price worth paying for a 300-line file. */
    function chunkPlan(rowCount, options) {
        const rows = Math.max(0, Number(rowCount) || 0);
        const floor = Number.isFinite(options && options.minRows)
            ? Number(options.minRows)
            : CHUNK_MIN_ROWS;
        const size = Math.max(1, Number.isFinite(options && options.chunkRows)
            ? Number(options.chunkRows)
            : CHUNK_ROWS);
        if (rows < floor || !(options && options.async)) {
            return { chunked: false, size: rows, slices: rows ? 1 : 0 };
        }
        return { chunked: true, size, slices: Math.ceil(rows / size) };
    }

    return {
        MAX_DECORATION_REPAINT_ROWS,
        DECORATION_REPAINT_DOCUMENT_SHARE,
        MAX_SPLICE_ROWS,
        CHUNK_ROWS,
        CHUNK_MIN_ROWS,
        decorationMap,
        decorationDelta,
        sourceRenderPlan,
        lineSplicePlan,
        highlightRepaintPlan,
        chunkPlan
    };
}));
