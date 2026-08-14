/* GridVibeExplorerEditHighlight — the decisions behind the in-place editor's
   highlight overlay, with no DOM in sight.

   The overlay paints the read-only row renderer behind a transparent-background
   textarea so entering edit mode stops moving the text. Two things have to be
   decided about the overlay itself, and both are pure:

   * viability — is the overlay on at all for this buffer? It is an
     enhancement, never a precondition for editing, so an oversized buffer
     silently drops back to the bare textarea edit mode has always used. No
     notice, no second global sink.
   * refresh — the underlay has to be rebuilt as the draft changes (the gutter
     grows a line, a wrapped line grows a row), and that rebuild is a whole
     document render. It is coalesced into one animation frame, and a draft
     that has not moved since the last render is not re-rendered at all: arrow
     keys, a click, and a no-op keystroke all reach the same input path.

   Once there are rows behind the caret, the two things edit mode used to give
   up — the in-file find and the selected-word occurrence tint — can be painted
   on them, and both need pure rules of their own:

   * `findRefreshDecision` — when a query has to be resolved against the buffer
     again, rather than reusing what is already painted.
   * `selectionOccurrenceQuery` / `occurrenceSpans` — what a textarea selection
     is worth tinting, and where else that text appears in the draft.
   * `lineSpansForRanges` — the mapping both of them need, from absolute draft
     offsets to a row plus a column range inside it.

   DOM-free and require()-able from Node so every rule is executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerEditHighlight = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Above the bound the underlay stands down: it is the same whole-document
       row render the read-only view does, and repeating it inside a frame
       budget on every keystroke is what the performance guardrail forbids.
       The caller passes the bound (the viewer's existing plain-preview
       threshold) rather than this module inventing a second number. */
    function overlayViability(options) {
        const settings = options || {};
        const length = Number(settings.contentLength);
        const max = Number(settings.maxContentLength);
        if (!Number.isFinite(length) || length < 0) {
            return { enabled: false, reason: 'unknown' };
        }
        if (Number.isFinite(max) && max > 0 && length > max) {
            return { enabled: false, reason: 'oversized' };
        }
        return { enabled: true, reason: '' };
    }

    /* `off`      — the overlay stood down for this buffer; nothing to refresh.
       `skip`     — the draft is byte-identical to what the underlay already
                    shows, so the render would repaint the same rows.
       `coalesce` — a frame is already queued; it reads the draft when it runs,
                    so this input needs no second frame.
       `schedule` — queue one frame. */
    function refreshDecision(state) {
        const current = state || {};
        if (!current.enabled) {
            return 'off';
        }
        if (String(current.draft == null ? '' : current.draft)
            === String(current.rendered == null ? '' : current.rendered)) {
            return 'skip';
        }
        return current.pending ? 'coalesce' : 'schedule';
    }

    /* Absolute `[start, end)` offsets into a draft → the row each one lands on
       plus its column range inside that row, which is what a painter over the
       rendered rows can actually address.

       One pass builds the line table and each range is then placed by binary
       search, so a find with a thousand matches costs one scan of the buffer
       rather than a thousand. A range is clipped to the row it opens on: the
       find input is a single-line control and the occurrence tint refuses a
       multi-line selection, so no caller produces one that straddles a
       newline, and one that somehow did would otherwise address a column past
       the end of its row.

       Row numbers are 1-based and count every line, which is what the
       underlay renders — its fold controls are suppressed, so no row is ever
       missing from it. */
    function lineSpansForRanges(draft, ranges) {
        const text = String(draft == null ? '' : draft);
        const list = Array.isArray(ranges) ? ranges : [];
        if (!list.length) {
            return [];
        }
        const starts = [0];
        for (let at = text.indexOf('\n'); at !== -1; at = text.indexOf('\n', at + 1)) {
            starts.push(at + 1);
        }
        const lineAt = offset => {
            let low = 0;
            let high = starts.length - 1;
            while (low < high) {
                const mid = (low + high + 1) >> 1;
                if (starts[mid] <= offset) {
                    low = mid;
                } else {
                    high = mid - 1;
                }
            }
            return low;
        };

        const spans = [];
        list.forEach(range => {
            const start = Number(range && range.start);
            const end = Number(range && range.end);
            if (!Number.isFinite(start) || !Number.isFinite(end)) {
                return;
            }
            if (start < 0 || end <= start || start >= text.length) {
                return;
            }
            const line = lineAt(start);
            const lineStart = starts[line];
            const lineEnd = line + 1 < starts.length ? starts[line + 1] - 1 : text.length;
            const stop = Math.min(end, lineEnd);
            if (stop <= start) {
                return;
            }
            spans.push({
                line: line + 1,
                start: start - lineStart,
                end: stop - lineStart,
                active: Boolean(range && range.active)
            });
        });
        return spans;
    }

    /* What the editor's find owes the query it is currently holding.

       `off`       — no rows to paint on (the overlay stood down), so the find
                     stays the disabled control it has always been while
                     editing.
       `clear`     — the query went away; drop whatever is painted.
       `skip`      — this exact query was already resolved against this exact
                     buffer, so the ranges already computed still describe it.
                     Stepping between matches lands here, which is why moving
                     the active match costs no re-scan and no re-render.
       `recompute` — the query or the buffer moved.

       The buffer is part of the key because that is the whole difference from
       the read-only find: there the content is fixed for as long as the file
       is open, here it changes under every keystroke. */
    function findRefreshDecision(state) {
        const current = state || {};
        if (!current.enabled) {
            return 'off';
        }
        const query = String(current.query == null ? '' : current.query);
        if (!query) {
            return 'clear';
        }
        const draft = String(current.draft == null ? '' : current.draft);
        const resolvedQuery = String(current.resultQuery == null ? '' : current.resultQuery);
        const resolvedDraft = String(current.resultDraft == null ? '' : current.resultDraft);
        return query === resolvedQuery && draft === resolvedDraft ? 'skip' : 'recompute';
    }

    /* Is this textarea selection worth tinting every other occurrence of?

       The rules mirror the read-only view's: a collapsed selection is nothing,
       an over-long one is not a word, and a selection that crosses rows is a
       block rather than a token. The read-only check tests the *trimmed* text
       for newlines, which lets a selection ending in one through; this tests
       the raw slice, because a trailing newline means the selection really did
       reach the next row.

       The offsets come back with the query so the scan below can leave the
       reader's own selection alone — it already carries the textarea's
       ::selection wash, and tinting it as well would just mean two coats. */
    function selectionOccurrenceQuery(options) {
        const settings = options || {};
        const value = String(settings.value == null ? '' : settings.value);
        const start = Number(settings.selectionStart);
        const end = Number(settings.selectionEnd);
        const max = Number(settings.maxQueryLength);
        if (!Number.isFinite(start) || !Number.isFinite(end)) {
            return null;
        }
        if (start < 0 || end > value.length || end <= start) {
            return null;
        }
        const raw = value.slice(start, end);
        if (/[\r\n]/.test(raw)) {
            return null;
        }
        const query = raw.trim();
        if (!query) {
            return null;
        }
        if (Number.isFinite(max) && max > 0 && query.length > max) {
            return null;
        }
        return { query, start, end };
    }

    /* A bare identifier matches whole words only, so double-clicking `id` does
       not light up every `width` in the buffer. Same rule, same character
       class, as the read-only tint in explorer-viewer.js. */
    const OCCURRENCE_WORD_RE = /^[\w$]+$/;

    function isWholeWordAt(value, start, end) {
        const before = start > 0 ? value[start - 1] : '';
        const after = end < value.length ? value[end] : '';
        return !OCCURRENCE_WORD_RE.test(before) && !OCCURRENCE_WORD_RE.test(after);
    }

    /* Every *other* occurrence of the selected text, as absolute draft
       offsets. Case-insensitive and non-overlapping, like the read-only tint,
       and capped because the tint is decoration.

       It scans the draft string where the read-only tint walks rendered text
       nodes, which is not just convenience: a node walk cannot see a match
       that straddles two syntax spans, and it has to exclude the reader's own
       selection by node identity. Over the string both fall out — a match is a
       match wherever the tokenizer drew its boundaries, and the selection is
       excluded by overlapping offsets. */
    function occurrenceSpans(options) {
        const settings = options || {};
        const draft = String(settings.draft == null ? '' : settings.draft);
        const query = String(settings.query == null ? '' : settings.query);
        if (!draft || !query) {
            return [];
        }
        const max = Number(settings.maxMatches);
        const limit = Number.isFinite(max) && max > 0 ? max : Infinity;
        const selectionStart = Number(settings.selectionStart);
        const selectionEnd = Number(settings.selectionEnd);
        const hasSelection = Number.isFinite(selectionStart) && Number.isFinite(selectionEnd);
        const wholeWord = OCCURRENCE_WORD_RE.test(query);
        const haystack = draft.toLowerCase();
        const needle = query.toLowerCase();

        const spans = [];
        let cursor = 0;
        while (spans.length < limit) {
            const start = haystack.indexOf(needle, cursor);
            if (start === -1) {
                break;
            }
            const end = start + query.length;
            cursor = end;
            if (hasSelection && start < selectionEnd && end > selectionStart) {
                continue;
            }
            if (wholeWord && !isWholeWordAt(draft, start, end)) {
                continue;
            }
            spans.push({ start, end });
        }
        return spans;
    }

    /* Put a carried selection back on a buffer.

       Entering and leaving edit mode swaps which surface holds the text — a
       rendered row for the reader, a textarea for the editor — and a selection
       does not survive that on its own. Neither does the occurrence tint that
       hangs off it, which is why a double-clicked word used to go dark on the
       way in and have to be made again on the way back.

       So the selection is carried as `{ line, column, needle }` and re-resolved
       against whatever the other surface holds. Line and column alone would be
       a lie whenever the two buffers differ — a discarded draft, a CRLF file's
       normalized newlines — so the *text* is the authority: the recorded spot
       is taken only when the needle really is there, and otherwise the needle
       is looked for on that row.

       It is deliberately not searched for document-wide. A selection that
       silently reappears somewhere else entirely is worse than one that
       quietly does not come back, because the occurrence tint would then be
       anchored to a word the reader never picked. */
    function carriedSelectionRange(options) {
        const settings = options || {};
        const text = String(settings.text == null ? '' : settings.text);
        const needle = String(settings.needle == null ? '' : settings.needle);
        const line = Number(settings.line);
        const column = Number(settings.column);
        if (!needle || !Number.isFinite(line) || line < 1) {
            return null;
        }
        const starts = [0];
        for (let at = text.indexOf('\n'); at !== -1; at = text.indexOf('\n', at + 1)) {
            starts.push(at + 1);
        }
        if (line > starts.length) {
            return null;
        }
        const lineStart = starts[line - 1];
        const lineEnd = line < starts.length ? starts[line] - 1 : text.length;
        const accept = start => (
            start >= lineStart
                && start + needle.length <= lineEnd
                && text.slice(start, start + needle.length) === needle
                ? { start, end: start + needle.length }
                : null
        );
        if (Number.isFinite(column) && column >= 0) {
            const exact = accept(lineStart + column);
            if (exact) {
                return exact;
            }
        }
        return accept(text.indexOf(needle, lineStart));
    }

    return {
        overlayViability,
        refreshDecision,
        lineSpansForRanges,
        findRefreshDecision,
        selectionOccurrenceQuery,
        occurrenceSpans,
        carriedSelectionRange
    };
}));
