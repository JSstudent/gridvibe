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

    /* Past this many differing rows a decoration repaint stops being the cheap
       option: each repainted row is its own innerHTML parse, and a thousand of
       those cost more than one string and one parse for the whole document.
       The find caps itself at 1,000 matches, so this is reached only by a
       query that matches nearly every line — which is exactly when rebuilding
       once is the better answer. */
    const MAX_DECORATION_REPAINT_ROWS = 400;

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
       empty pane behind. A build still in flight counts as *not* the same
       surface: its remaining rows would be emitted with the ranges it started
       with. */
    function sourceRenderPlan(input) {
        const options = input || {};
        if (
            !options.sameSurface
            || options.contentChanged
            || options.languageChanged
            || options.foldsChanged
            || options.pending
        ) {
            return { mode: 'full', lines: [] };
        }
        const lines = decorationDelta(options.previousDecorations, options.nextDecorations);
        if (!lines.length) {
            return { mode: 'skip', lines: [] };
        }
        const ceiling = Number.isFinite(options.maxRepaintRows)
            ? Number(options.maxRepaintRows)
            : MAX_DECORATION_REPAINT_ROWS;
        if (lines.length > ceiling) {
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
        MAX_SPLICE_ROWS,
        CHUNK_ROWS,
        CHUNK_MIN_ROWS,
        decorationMap,
        decorationDelta,
        sourceRenderPlan,
        lineSplicePlan,
        chunkPlan
    };
}));
