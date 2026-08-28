/* GridVibeExplorerGitSearch — the commit find in the Git sidebar's
   Graph section.

   It behaves like the Source view's Ctrl+F find: type a query and every
   matching substring in the loaded commit subjects or ids is highlighted in place,
   a counter reads `current/total`, and Enter/Shift+Enter or the prev/next
   buttons step through the matches (wrapping around), with the active match
   painted brighter and scrolled into view. The clear button drops the query.

   Unlike the Source find it is folded away by default: the Graph section's
   header carries a magnifier that reveals the bar, and the same button or
   Escape puts it back (see nextVisibility). The section header is the one
   place a sidebar this narrow has room for a permanent control.

   Subject mode searches the text the row actually shows. Hash mode searches
   the full commit id already carried by each loaded row, while the visible
   seven-character hash carries the marker. Matching is case-insensitive,
   like the Source find's default.

   DOM-free and require()-able from Node so the matching, the active-index
   clamping and the mark wrapping are executed by tests rather than asserted
   as source text; the page's DOM adapter lives in explorer-git-sidebar.js
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

    /* The find bar is folded away behind the Graph section's magnifier until
       it is asked for, so the state carries its own `open` flag beside the
       query. Closing is not merely hiding: a hidden bar still painting <mark>s
       over the commit rows would be a highlight with no visible control and no
       counter behind it, so closing drops the query the same way the × does.
       Opening never invents one — a reopened bar starts empty because that is
       what closing left. The mode survives because it is a control setting,
       not part of the query. Runtime-only and per pane, like the query itself.

       `action` is 'toggle' (the button), 'open', or 'close' (Escape). Returns
       a fresh state; the caller's object is never mutated. */
    function nextVisibility(state, action) {
        const current = state && typeof state === 'object' ? state : {};
        const open = action === 'toggle' ? !current.open : action === 'open';
        const mode = current.mode === 'hash' ? 'hash' : 'subject';
        return open
            ? { open: true, query: text(current.query), activeIndex: current.activeIndex || 0, mode }
            : { open: false, query: '', activeIndex: 0, mode };
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
    function searchPlan(commits, query, activeIndex, options = {}) {
        const list = Array.isArray(commits) ? commits : [];
        const mode = options && options.mode === 'hash' ? 'hash' : 'subject';
        const needle = text(query);
        const validHashQuery = /^[0-9a-f]{1,40}$/i.test(needle);
        const perCommit = list.map(commit => {
            if (mode !== 'hash') {
                return matchRanges(commitSubject(commit), needle);
            }
            if (!validHashQuery) {
                return [];
            }
            const hash = text(commit && (commit.full_hash || commit.hash));
            const at = hash.toLowerCase().indexOf(needle.toLowerCase());
            return at === -1 ? [] : [[at, at + needle.length]];
        });
        const matchCount = perCommit.reduce((total, ranges) => total + ranges.length, 0);
        const index = Number.isFinite(activeIndex) ? Math.trunc(activeIndex) : 0;
        return {
            perCommit,
            matchCount,
            activeIndex: matchCount ? ((index % matchCount) + matchCount) % matchCount : 0,
            /* Two different empty results, and one message for both told the
               reader the wrong thing about half of them: "not in the loaded
               graph" invites scrolling for a commit that was never an id in
               the first place. A query that is not hexadecimal is refused as
               an id rather than searched as a subject, so it says so. */
            emptyText: mode === 'hash' && needle && !matchCount
                ? (validHashQuery
                    ? 'No commit in the loaded graph'
                    : 'Not a commit id — hexadecimal characters only')
                : ''
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

    /* Hash rows show seven characters even though hash mode searches the full
       id. Mark the visible part when the match begins inside that abbreviation;
       a later match is represented by the row-level tint from hashMarkClass(). */
    function markedHashHtml(hash, range) {
        const visible = text(hash).slice(0, 7);
        if (!Array.isArray(range) || range.length < 2) {
            return escapeHtml(visible);
        }
        const start = Math.max(0, Math.trunc(Number(range[0]) || 0));
        const end = Math.max(start, Math.trunc(Number(range[1]) || 0));
        if (start >= visible.length || end <= start) {
            return escapeHtml(visible);
        }
        const visibleEnd = Math.min(end, visible.length);
        return escapeHtml(visible.slice(0, start))
            + '<mark class="explorer-git-commit-hash-match">'
            + escapeHtml(visible.slice(start, visibleEnd))
            + '</mark>'
            + escapeHtml(visible.slice(visibleEnd));
    }

    /* Collapse-only by design: expanding every row would also render every
       commit's file list.

       `changed` is the whole answer -- the caller owns the live expansion set
       and clears it in place, so handing back a second, empty copy of it was a
       field nothing read (guardrail 5). What `changed` buys is the skip: an
       Alt-click on an already-collapsed graph must not re-render the panel or
       write the pane's presentation. */
    function collapseAllPlan(expanded) {
        const list = Array.isArray(expanded) ? expanded : [];
        return { changed: Boolean(list.length) };
    }

    return {
        commitSubject,
        nextVisibility,
        matchRanges,
        searchPlan,
        markedSubjectHtml,
        hashMarkClass,
        markedHashHtml,
        collapseAllPlan
    };
}));
