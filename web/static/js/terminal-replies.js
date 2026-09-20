/* GridVibeTerminalReplies — keeping a terminal's own answers out of the next
   program's input.

   A program asks the terminal about itself by writing a query into its own
   output stream (`\x1b]11;?\x1b\\` for the background colour, `\x1b[6n` for
   the cursor, DA, XTVERSION, DECRQM, …). The emulator answers by *typing* the
   reply back, so the answer travels the input channel and is indistinguishable
   from a keystroke once it gets there.

   On a real terminal that round trip is microseconds and the reply lands while
   the asker is still reading. In GridVibe the reply is born only when xterm.js
   finally parses the query, and GridVibe deliberately defers parsing: output
   for a pane that is not yet fitted is held in `_pendingOutput`, and the fit is
   a debounce plus a bounded retry ladder for a pane that has no size yet — a
   pane being replaced, restored, or switched to. The backlog is then written in
   one go, xterm answers every query inside it *now*, and the reply reaches a
   program that stopped listening hundreds of milliseconds ago. It types the
   answer into its prompt instead:

       › ]10;rgb:e0e0/e0e0/e0e0\]11;rgb:0d0d/0d0d/0d0d\

   The rule this module owns is therefore about *age*, not about identity: a
   query GridVibe is only now getting round to has no useful answer left, so it
   is removed before the parser can see it, and anything the parser produces
   from that late write is refused the input channel.

   Two layers, deliberately unequal:

   - **Stripping** is the cure. A query removed from the backlog generates no
     reply at all, and queries render nothing, so removing one is invisible. It
     is a known list, and a known list is never complete.
   - **The quiet window** is the backstop under it. While a late backlog is
     being parsed the pane's replies are refused outright, so a query form this
     module has never heard of still cannot leak. It is structural, and it is
     what makes this not merely a longer list.

   Both apply only to a backlog held longer than `STALE_DEFERRAL_MS`. That bound
   is the whole reason the budget exists: a pane that fits promptly answers
   colour queries normally, so agents keep detecting the theme, and only a pane
   that fell behind loses answers it could no longer have delivered. A lost
   answer costs a program its default assumption; a late one costs the reader a
   line of garbage in the prompt they are typing into. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeTerminalReplies = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Every sequence whose only purpose is to make the terminal talk back.
       Listed as separate sources rather than as one literal so each entry can
       say which request it is and, where the shape is shared with something
       that *renders*, which neighbour it is deliberately not matching. */
    const TERMINAL_QUERY_SOURCES = [
        /* Device Attributes — DA1 (`CSI c`), DA2 (`CSI > c`), DA3 (`CSI = c`),
           and the `CSI ? … c` shape a response shares with them. */
        '\\x1b\\[[>=]?(?:0?c|\\?[0-9;]*c)',
        /* Device Status Report, ANSI and DEC private alike: `CSI 5n`, `CSI 6n`,
           `CSI ?6n` (DECXCPR), `CSI ?25n`. `n` has no rendering use at all, so
           the parameter is left open rather than enumerated. */
        '\\x1b\\[\\??[0-9]{1,4}n',
        /* XTVERSION (`CSI > Ps q`). Never `CSI Ps SP q` (DECSCUSR), which sets
           the cursor shape and carries an intervening space. */
        '\\x1b\\[>[0-9]*q',
        /* DECRQM mode request, ANSI and DEC private. */
        '\\x1b\\[\\??[0-9;]{1,32}\\$p',
        /* XTWINOPS *reports* — 11, 13..16, 18..21. Emphatically not 22 and 23,
           which push and pop the title stack and are actions a live program
           relies on. */
        '\\x1b\\[(?:1[1345689]|2[01])(?:;[0-9]+)*t',
        /* OSC queries: foreground/background/cursor colour (10/11/12), the
           indexed palette (4) and the special colours (5), and the clipboard
           read (52). Each is a query only because of the `;?` before its
           terminator — the same OSCs *set* those values without it. */
        '\\x1b\\](?:1[012]|4;[0-9]+|5;[0-9]+|52;[a-zA-Z]*);\\?(?:\\x07|\\x1b\\\\)',
        /* DCS requests: XTGETTCAP (`DCS + q`) and DECRQSS (`DCS $ q`). */
        '\\x1bP[+$]q[^\\x1b\\x07]*(?:\\x07|\\x1b\\\\)'
    ];

    const TERMINAL_QUERY_PATTERN = TERMINAL_QUERY_SOURCES.join('|');

    /* How long a backlog may sit before the queries inside it are treated as
       unanswerable. Comfortably above a prompt fit (a 60 ms debounce and a
       frame) so the ordinary pane still answers, and comfortably below the
       window an agent CLI waits on a colour query, so a pane that hit the fit
       retry ladder never answers into a prompt. */
    const STALE_DEFERRAL_MS = 150;

    /* Only ever reached when a write never reports that it finished parsing.
       Refusing a pane's input is the safe direction for milliseconds and the
       wrong one forever, so the window always closes. */
    const QUIET_WRITE_TIMEOUT_MS = 1000;

    /* A depth rather than a flag: two late writes can overlap on one pane, and
       the first to finish must not reopen the channel for the second. */
    const SUPPRESSION_FIELD = '_replySuppressionDepth';

    function suppressionDepth(pane) {
        const depth = Number(pane && pane[SUPPRESSION_FIELD]);
        return Number.isFinite(depth) && depth > 0 ? depth : 0;
    }

    /* The one question the page's input forwarding asks. */
    function repliesSuppressed(pane) {
        return suppressionDepth(pane) > 0;
    }

    function stripTerminalQueries(data) {
        if (typeof data !== 'string' || !data) {
            return '';
        }
        /* Built per call: a `g` regexp carries `lastIndex` between calls, and
           one shared instance would start each scan part-way in. */
        return data.replace(new RegExp(TERMINAL_QUERY_PATTERN, 'g'), '');
    }

    /* What a deferred backlog becomes, decided before anything is written so
       the decision can be read on its own. */
    function deferredWritePlan(io) {
        const target = io || {};
        const data = typeof target.data === 'string' ? target.data : '';
        const heldMs = Number.isFinite(target.heldMs) && target.heldMs > 0 ? target.heldMs : 0;
        const budgetMs = Number.isFinite(target.budgetMs) && target.budgetMs >= 0
            ? target.budgetMs
            : STALE_DEFERRAL_MS;
        const stale = heldMs > budgetMs;
        return {
            stale,
            heldMs,
            budgetMs,
            data: stale ? stripTerminalQueries(data) : data
        };
    }

    /* Write bytes with the pane's replies refused for as long as the parser is
       working through them. `write` is the pane's own `term.write`, called with
       a completion callback; xterm.js parses asynchronously, so the window can
       only be closed by that callback — or, failing it, by the clock.

       Returns whether the write was actually made. */
    function quietWrite(io) {
        const target = io || {};
        const pane = target.pane || null;
        const write = typeof target.write === 'function' ? target.write : null;
        const data = typeof target.data === 'string' ? target.data : '';
        if (!pane || !write || !data) {
            return false;
        }

        const schedule = typeof target.setTimeout === 'function' ? target.setTimeout : null;
        const cancel = typeof target.clearTimeout === 'function' ? target.clearTimeout : () => {};
        const timeoutMs = Number.isFinite(target.timeoutMs) && target.timeoutMs >= 0
            ? target.timeoutMs
            : QUIET_WRITE_TIMEOUT_MS;

        pane[SUPPRESSION_FIELD] = suppressionDepth(pane) + 1;

        let released = false;
        let timer = null;
        function release() {
            if (released) {
                return;
            }
            released = true;
            if (timer !== null) {
                cancel(timer);
                timer = null;
            }
            pane[SUPPRESSION_FIELD] = Math.max(0, suppressionDepth(pane) - 1);
        }

        if (schedule) {
            timer = schedule(release, timeoutMs);
        }

        try {
            write(data, release);
        } catch (error) {
            release();
            throw error;
        }

        /* With no clock there is nothing to reopen the channel if the parser
           never reports back, and a permanently mute pane is a far worse
           failure than an unsuppressed reply. Close the window on the call. */
        if (!schedule) {
            release();
        }
        return true;
    }

    /* The page's entry point: flush a backlog — stripped and quiet when it is
       old enough that its queries can no longer be answered usefully, plain and
       unguarded when it is not. */
    function writeDeferred(io) {
        const target = io || {};
        const plan = deferredWritePlan(target);
        if (!plan.data) {
            return { written: false, stale: plan.stale, data: plan.data };
        }
        if (!plan.stale) {
            const write = typeof target.write === 'function' ? target.write : null;
            if (!write) {
                return { written: false, stale: false, data: plan.data };
            }
            write(plan.data);
            return { written: true, stale: false, data: plan.data };
        }
        const written = quietWrite({
            pane: target.pane,
            data: plan.data,
            write: target.write,
            setTimeout: target.setTimeout,
            clearTimeout: target.clearTimeout,
            timeoutMs: target.timeoutMs
        });
        return { written, stale: true, data: plan.data };
    }

    return {
        TERMINAL_QUERY_SOURCES,
        TERMINAL_QUERY_PATTERN,
        STALE_DEFERRAL_MS,
        QUIET_WRITE_TIMEOUT_MS,
        SUPPRESSION_FIELD,
        suppressionDepth,
        repliesSuppressed,
        stripTerminalQueries,
        deferredWritePlan,
        quietWrite,
        writeDeferred
    };
}));
