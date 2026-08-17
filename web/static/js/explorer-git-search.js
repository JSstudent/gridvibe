/* GridVibeExplorerGitSearch — the commit-message find in the Git sidebar's
   Graph section.

   It behaves like the Source view's Ctrl+F find: type a query and every
   matching substring in the loaded commit subjects is highlighted in place,
   a counter reads `current/total`, and Enter/Shift+Enter or the prev/next
   buttons step through the matches (wrapping around), with the active match
   painted brighter and scrolled into view. Escape or the clear button drops
   the query.

   The haystack is the commit *subject* — the text the row actually shows —
   so every counted match has a visible highlight. Matching is
   case-insensitive, like the Source find's default.

   DOM-free and require()-able from Node so the matching, the active-index
   clamping and the mark wrapping are executed by tests rather than asserted
   as source text; the page's DOM adapter lives in explorer-viewer.js
   (paintExplorerGitCommitSearch).

   markedSubjectHtml() paints the same <mark> as explorerMarkedEscHtml() in
   explorer-viewer.js and is deliberately not shared with it: that one is
   closed over the viewer's IIFE and takes the Source find's absolute
   {start, end, active} ranges over a code line, which a module Node has to be
   able to require() cannot reach and does not want. What must not diverge is
   the markup — the class names here are the ones terminals.css styles for
   both. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerGitSearch = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function text(value) {
        return typeof value === 'string' ? value : '';
    }

    function escapeHtml(value) {
        return text(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /* The subject a row shows: the record's own subject, falling back to the
       full log line when the record carries no parsed subject. */
    function commitSubject(commit) {
        return text(commit && (commit.subject || commit.line));
    }

    /* All case-insensitive occurrences of `query` in `subject`, as sorted,
       non-overlapping [start, end) ranges. An empty query matches nothing. */
    function matchRanges(subject, query) {
        const needle = text(query).toLowerCase();
        if (!needle) {
            return [];
        }
        const haystack = text(subject).toLowerCase();
        const ranges = [];
        let cursor = 0;
        while (cursor + needle.length <= haystack.length) {
            const at = haystack.indexOf(needle, cursor);
            if (at === -1) {
                break;
            }
            ranges.push([at, at + needle.length]);
            cursor = at + needle.length;
        }
        return ranges;
    }

    /* One pass over the loaded commits: per-commit match ranges (aligned with
       the input array), the total match count, and the active index clamped
       into range — wrapping past either end, and pinned to 0 when nothing
       matches. The clamp is what makes prev/next cycle instead of sticking. */
    function searchPlan(commits, query, activeIndex) {
        const list = Array.isArray(commits) ? commits : [];
        const perCommit = list.map(commit => matchRanges(commitSubject(commit), query));
        const matchCount = perCommit.reduce((total, ranges) => total + ranges.length, 0);
        const index = Number.isFinite(activeIndex) ? Math.trunc(activeIndex) : 0;
        return {
            perCommit,
            matchCount,
            activeIndex: matchCount ? ((index % matchCount) + matchCount) % matchCount : 0
        };
    }

    /* The subject with every match wrapped in the find's <mark>, the active
       one (global ordinal `activeIndex`, counting from `startOrdinal` for the
       matches before this commit) carrying the brighter `.active` class.
       Plain text is escaped exactly as the unhighlighted render escapes it. */
    function markedSubjectHtml(subject, ranges, startOrdinal, activeIndex) {
        const value = text(subject);
        const list = Array.isArray(ranges) ? ranges : [];
        let html = '';
        let cursor = 0;
        list.forEach((range, offset) => {
            const [start, end] = range;
            html += escapeHtml(value.slice(cursor, start));
            const active = startOrdinal + offset === activeIndex;
            html += `<mark class="explorer-search-match${active ? ' active' : ''}">`
                + escapeHtml(value.slice(start, end))
                + '</mark>';
            cursor = end;
        });
        html += escapeHtml(value.slice(cursor));
        return html;
    }

    /* The short hash's own hit marking. A matched word near the cut-off end of
       a truncated subject is a highlight nobody can see, so the hash — always
       fully visible at the row's left edge — carries a tint for every commit
       with at least one match, and the brighter `.active` variant for the one
       holding the active match. Returns the class suffix (leading space), or
       '' for a row with no match. */
    function hashMarkClass(ranges, startOrdinal, activeIndex) {
        const list = Array.isArray(ranges) ? ranges : [];
        if (!list.length) {
            return '';
        }
        const active = activeIndex >= startOrdinal
            && activeIndex < startOrdinal + list.length;
        return ` explorer-git-commit-search-hit${active ? ' active' : ''}`;
    }

    return {
        commitSubject,
        matchRanges,
        searchPlan,
        markedSubjectHtml,
        hashMarkClass
    };
}));
