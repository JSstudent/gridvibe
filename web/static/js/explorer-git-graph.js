/* GridVibeExplorerGitGraph -- the Graph section's two non-search policies:
   what a commit's hover card says, and how far back the list may be read.

   Both are here rather than in explorer-git-sidebar.js for the reason
   explorer-git-pin.js is: they are decisions with no DOM in them, and the
   sidebar's job is to paint an answer rather than to compute one. DOM-free
   and require()-able from Node, so the date arithmetic and the page ladder
   are executed by tests rather than asserted as source text.

   The hover card replaces a native `title`, which cannot be styled and cannot
   usefully hold more than one line. It reports what a commit row has no room
   to show -- author, when, the full object id, any ref decoration -- from
   fields the sidebar has already loaded, so nothing here issues a request or
   widens the explorer's read-only contract.

   The page ladder is deliberately *derived from the server's answer* rather
   than from constants mirrored on this side. The ceiling belongs to
   web/explorer.py; a second copy here is how a client comes to keep asking
   for a page the server will not serve. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerGitGraph = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const MONTHS = [
        'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'
    ];
    const MINUTE = 60;
    const HOUR = 60 * MINUTE;
    const DAY = 24 * HOUR;
    const WEEK = 7 * DAY;
    /* Average civil month and year: the card says "3 months ago", not a
       calendar difference, so the coarse divisor is the honest one. */
    const MONTH = 2629800;
    const YEAR = 31557600;

    const ISO_PATTERN =
        /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:?\d{2})?$/;

    function text(value) {
        return typeof value === 'string' ? value : '';
    }

    function count(value) {
        return Number.isFinite(value) ? Math.trunc(value) : 0;
    }

    function plural(value, unit) {
        return value + ' ' + unit + (value === 1 ? '' : 's') + ' ago';
    }

    /* The wall-clock time git recorded, rendered in the author's own offset
       rather than in the reader's. That is what `git log` shows, and
       converting it would silently move a commit across a date boundary for
       anyone in another zone. Read straight off the ISO string's own fields,
       which also keeps the result independent of the machine running it. */
    function absoluteCommitDate(iso) {
        const match = ISO_PATTERN.exec(text(iso).trim());
        if (!match) {
            return '';
        }
        const year = match[1];
        const month = MONTHS[Number(match[2]) - 1];
        const day = Number(match[3]);
        if (!month) {
            return '';
        }
        return day + ' ' + month + ' ' + year + ', ' + match[4] + ':' + match[5];
    }

    /* How long ago, from the instant the offset in the string pins down. An
       unparseable stamp reads as '', and a future one as 'just now' rather
       than inventing a negative age. */
    function relativeCommitDate(iso, now) {
        const stamp = Date.parse(text(iso).trim());
        if (!Number.isFinite(stamp)) {
            return '';
        }
        const reference = Number.isFinite(now) ? now : Date.now();
        const seconds = Math.max(0, Math.round((reference - stamp) / 1000));
        if (seconds < MINUTE) {
            return 'just now';
        }
        if (seconds < HOUR) {
            return plural(Math.round(seconds / MINUTE), 'minute');
        }
        if (seconds < DAY) {
            return plural(Math.round(seconds / HOUR), 'hour');
        }
        if (seconds < WEEK) {
            return plural(Math.round(seconds / DAY), 'day');
        }
        if (seconds < MONTH) {
            return plural(Math.round(seconds / WEEK), 'week');
        }
        if (seconds < YEAR) {
            return plural(Math.round(seconds / MONTH), 'month');
        }
        return plural(Math.round(seconds / YEAR), 'year');
    }

    /* The card's content for one loaded commit row.

       A row with nothing to report is dropped, never blanked: a log that
       carries no author for a commit gets a shorter card, not an
       "Author: unknown" line that reads as a recorded fact. `hint` names the
       gesture the row answers to, which is where the native title's
       "(Alt: collapse all)" went. */
    function commitCard(commit, options) {
        const record = commit && typeof commit === 'object' ? commit : {};
        const settings = options && typeof options === 'object' ? options : {};
        const iso = text(record.authored_at);
        const absolute = absoluteCommitDate(iso);
        const relative = relativeCommitDate(iso, settings.now);
        const fullHash = text(record.full_hash);
        const shortHash = text(record.hash);
        const author = text(record.author).trim();
        const refs = text(record.refs).trim();
        const rows = [];
        if (author) {
            rows.push({ label: 'Author', value: author });
        }
        if (absolute || relative) {
            rows.push({
                label: 'Date',
                value: [absolute, relative].filter(Boolean).join('  ·  '),
                title: iso
            });
        }
        if (fullHash || shortHash) {
            rows.push({ label: 'Commit', value: fullHash || shortHash, mono: true });
        }
        if (refs) {
            rows.push({ label: 'Refs', value: refs });
        }
        /* The message the author wrote, without the ref decoration the row
           itself already renders in front of the subject. */
        const message = text(record.message) || text(record.subject) || text(record.line);
        return {
            message,
            rows,
            hint: settings.expanded
                ? 'Click to collapse · Alt-click collapses every commit'
                : 'Click to list this commit’s files',
            /* The same facts as one line, for the row button's accessible
               name. The card itself is hidden from assistive technology: it
               lives inside the button, so every word in it would otherwise be
               read out as part of the control's name a second time. */
            summary: [message]
                .concat(rows.map(row => row.label + ': ' + row.value))
                .filter(Boolean)
                .join(' — ')
        };
    }

    /* Which side of its row the hover card opens on.

       Below by default, because that is where the eye is already travelling.
       But the panel is a scroller, and a card opened from one of the last
       rows would be clipped by its bottom edge -- the affordance is only
       useful if it can be read, so it flips above once it no longer fits.

       Measurements come in as plain numbers rather than as elements, so the
       rule is decided here and only the class is applied by the page. `gap`
       is the overlap the card sits at, so the fit test is against the space
       the card would actually occupy.

       When neither side fits -- a card taller than the panel, which a long
       subject and a long ref list can reach in a short sidebar -- it takes
       the roomier side rather than defaulting, so the reader loses the least. */
    function cardPlacement(metrics) {
        const box = metrics && typeof metrics === 'object' ? metrics : {};
        const rowTop = Number(box.rowTop) || 0;
        const rowBottom = Number(box.rowBottom) || 0;
        const viewTop = Number(box.viewTop) || 0;
        const viewBottom = Number(box.viewBottom) || 0;
        const needed = (Number(box.cardHeight) || 0) + (Number(box.gap) || 0);
        const spaceBelow = viewBottom - rowBottom;
        const spaceAbove = rowTop - viewTop;
        if (needed <= spaceBelow) {
            return 'below';
        }
        if (needed <= spaceAbove) {
            return 'above';
        }
        return spaceAbove > spaceBelow ? 'above' : 'below';
    }

    /* The "Show more" control below the graph.

       Every number comes from the payload: `commit_limit` is the page that
       was read, `commit_page` the step, `commit_limit_max` the ceiling, and
       `commit_has_more` whether the repository holds anything past it. A
       payload from before those fields existed reports no more commits, which
       is the safe answer -- a control that cannot load anything is not shown.

       Four states, and the two wordless ones matter as much as the button. A
       graph that ends inside its first page says nothing at all, because
       there is nothing a reader could do about it. One the reader expanded
       says how far it got, so the button's disappearance reads as an answer
       rather than as the control having broken. */
    function pagePlan(repo, options) {
        const payload = repo && typeof repo === 'object' ? repo : {};
        const settings = options && typeof options === 'object' ? options : {};
        const loaded = Array.isArray(payload.commits) ? payload.commits.length : 0;
        const page = Math.max(1, count(payload.commit_page));
        const limit = Math.max(page, count(payload.commit_limit));
        const ceiling = Math.max(limit, count(payload.commit_limit_max));
        const expanded = loaded > page;
        if (!payload.commit_has_more) {
            return {
                visible: expanded,
                canLoadMore: false,
                atCeiling: false,
                nextLimit: null,
                loaded,
                label: '',
                detail: expanded ? 'All ' + loaded + ' commits in this scope.' : ''
            };
        }
        if (limit >= ceiling) {
            return {
                visible: true,
                canLoadMore: false,
                atCeiling: true,
                nextLimit: null,
                loaded,
                label: '',
                detail: 'Showing the newest ' + loaded
                    + ' commits — the graph stops here.'
            };
        }
        const nextLimit = Math.min(limit + page, ceiling);
        return {
            visible: true,
            canLoadMore: !settings.loading,
            atCeiling: false,
            nextLimit,
            loaded,
            label: settings.loading
                ? 'Loading more...'
                : 'Show ' + (nextLimit - limit) + ' more',
            detail: loaded + ' loaded'
        };
    }

    return {
        absoluteCommitDate,
        relativeCommitDate,
        commitCard,
        cardPlacement,
        pagePlan
    };
}));
