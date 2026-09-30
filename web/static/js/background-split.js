/* GridVibeBackgroundSplit — a split in a session tab this window is not
   showing, done without showing it.

   A person who has picked one tab must not see the window move because an
   agent in another tab asked for a split: no tab switch, no focus change, no
   repaint of what is on screen. So nothing here touches the grid that is
   showing. The tab's arrangement is a model — pane order, one rectangle each,
   two lists of track weights — and a split is an edit to that model:

   1. read the tab's model, from its cached view or the server's summary;
   2. measure the *shared* grid the tab would be shown in (a window has one
      grid; every tab is laid out in it), and apply the same rules the split
      button applies — the pane cap, the narrow window, the integer grid and
      the character floor;
   3. create the pane on the server;
   4. write the new arrangement through the group's revisioned presentation
      transaction, and drop the tab's cached view so it is rebuilt from what
      the server now holds the next time it is shown.

   Reading, measuring, the hold and the write are the tab's, shared with the
   divider resize: `background-tab.js`, handed in as `page.tab`.

   Two halves, the same split every other module here uses:

   - `policy` is pure: which rule refuses a pane, and what the tab looks like
     after a split. No DOM, no timers, no fetch.
   - `create(page)` is the sequence. Everything that reads or writes the page
     goes through the injected `page`, which is what lets Node execute the
     ordering — refuse before creating, create before writing, drop the cache
     before the write can be re-described from it — instead of tests asserting
     source text.

   Rules the sequence exists to keep:

   - **Refuse before anything is created.** Every refusal is the page's own
     sentence for the rule that refused, with the axis that would have worked.
   - **A tab that was opened meanwhile is not split from behind.** If the group
     became the shown one after it was read, the visible handler does the split
     instead: it owns a live grid, and this path does not.
   - **A tab opened while its split is in flight waits for it.** From the moment
     the request goes out until the arrangement is written, the tab is held:
     the page's load asks the tab's `settled` first, so it never paints the new
     pane from the server before its place in the layout exists, nor restores a
     cached view the split is about to drop.
   - **A pane that exists is reported, whatever happened to its layout.** The
     server has made it. If the arrangement could not be saved the result says
     so, and the tab comes back with the default arrangement for its size.
   - **A placement never puts back an arrangement it did not read.** A model
     carries the presentation revision it was read at. When the split answers
     at another one -- a divider was moved while the request was out -- the
     pane is placed on the arrangement the answer's record holds instead, and
     the cut planned again; a record that no longer holds the same panes is not
     written to at all.
   - **The visible handler places the same way when its window moves on.** A
     split asked for in the showing tab whose request was still out when
     another tab was picked hands its pane to `placeAfterMove`, with the model
     and cut it read before the request, and the tab is held for that write. */
(function (root, factory) {
    const api = factory(root);
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeBackgroundSplit = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The three reasons a pane cannot be halved. The page names the same three
       in `getSplitBlockers`, so the sentence it composes for a background pane
       is the one its own button tooltip carries. */
    const BLOCKED_BY_WINDOW = 'window';
    const BLOCKED_BY_GRID = 'grid';
    const BLOCKED_BY_SIZE = 'size';

    const AXES = ['vertical', 'horizontal'];

    /* Hairline borders around a terminal, the same allowance the divider
       resize makes when it asks whether a pane can still hold a terminal. */
    const EDGE_PX = 2;

    const SAVE_FAILED_NOTE = 'The pane was created, but its place in the layout could not be saved, so it will appear with the default arrangement.';

    const positive = (value, fallback) => (Number(value) > 0 ? Number(value) : fallback);

    const policy = {
        /* The characters a pane would hold *after* being halved on `axis`, off
           the pixels its rectangle spans. The same reading `estimatePaneCharacters`
           takes off a live terminal: a side-by-side half keeps the rows and
           half the columns; a stacked half keeps the columns and half the height
           less its own header. */
        capacity(surface, axis, cell, headerHeight) {
            const cellWidth = positive(cell && cell.width, 8);
            const cellHeight = positive(cell && cell.height, 17);
            const header = positive(headerHeight, 34);
            const width = Math.max(0, Number(surface && surface.width) - EDGE_PX) || 0;
            const height = Math.max(0, Number(surface && surface.height) - header - EDGE_PX) || 0;
            let cols = Math.floor(width / cellWidth);
            let rows = Math.floor(height / cellHeight);
            if (axis === 'vertical') {
                cols = Math.floor(cols / 2);
            } else if (axis === 'horizontal') {
                const half = Math.max(0, Number(surface && surface.height) / 2 - header) || 0;
                rows = Math.floor(half / cellHeight);
            }
            return { cols, rows };
        },

        /* Which rule, if any, refuses each axis for one pane. Tested in the
           order `getSplitBlockers` tests them: the two page-wide refusals, then
           the integer grid — a pane with no line left to halve has no halves
           to measure — then the character floor. An axis with no blocker is an
           axis that can be split. */
        blockers({ narrow, count, maxPanes, rect, surface, cell, headerHeight, minCols, minRows }) {
            if (narrow || Number(count) >= Number(maxPanes)) {
                return { vertical: BLOCKED_BY_WINDOW, horizontal: BLOCKED_BY_WINDOW };
            }
            const blocked = {};
            [['vertical', 'w'], ['horizontal', 'h']].forEach(([axis, span]) => {
                if (!(Number(rect && rect[span]) >= 2)) {
                    blocked[axis] = BLOCKED_BY_GRID;
                    return;
                }
                const measured = policy.capacity(surface, axis, cell, headerHeight);
                blocked[axis] = measured.cols >= minCols && measured.rows >= minRows
                    ? ''
                    : BLOCKED_BY_SIZE;
            });
            return blocked;
        },

        /* The tab after one split: the source pane's rectangle replaced by its
           two halves, the new pane placed right after the pane it came from,
           and the axis's track weights rewritten when the plan says they have
           to be — the same edit the visible handler makes to its own grid. */
        arrange({ ids, rects, columnWeights, rowWeights, visualIndex, axis, plan, newId, splitRect }) {
            const [first, second] = splitRect(rects[visualIndex], axis, plan && plan.firstSpan);
            const nextIds = ids.slice();
            nextIds.splice(visualIndex + 1, 0, newId);
            const nextRects = rects.slice();
            nextRects.splice(visualIndex, 1, first, second);
            const weights = plan && Array.isArray(plan.weights) ? plan.weights : null;
            return {
                ids: nextIds,
                rects: nextRects,
                columnWeights: axis === 'vertical' && weights ? weights : columnWeights,
                rowWeights: axis === 'horizontal' && weights ? weights : rowWeights,
                index: visualIndex + 1
            };
        }
    };

    function create(page) {
        const {
            /* The window's `GridVibeBackgroundTab`: the tab's reading,
               measuring, hold and write, shared with the divider resize. */
            tab,
            /* The session tab this window holds a pane in without showing it,
               or '' — for a pane on screen, one this window does not hold, or
               a window that has not settled on a tab. */
            groupOf,
            /* Whether the tab is now the shown one, or about to be. */
            isShown,
            /* Where the cut lands and what the axis weights become. */
            plan,
            splitRect,
            /* GridVibe's own sentence for a rule that refused an axis. */
            reason,
            unmeasurable,
            /* Create the pane on the server: `{ ok, session, group, error }`. */
            split,
            /* The tab as a group record describes it, without the pane just
               added: `{ ids, rects, columnWeights, rowWeights, baseCount }`,
               or null when the record has no arrangement for those panes. */
            recordModel,
            /* The visible handler, for a tab that was opened meanwhile. */
            performShown,
            limits
        } = page || {};

        async function inspect(sessionId) {
            const id = String(sessionId || '');
            const groupId = String(groupOf(id) || '');
            if (!groupId) return null;
            const model = await tab.read(groupId);
            if (!model) return null;
            const visualIndex = model.ids.indexOf(id);
            if (visualIndex < 0) return null;
            const measured = tab.measure(model);
            if (!measured) {
                return { groupId, model, visualIndex, measured: null, blockers: null, candidates: [] };
            }
            const blockers = policy.blockers({
                narrow: measured.narrow,
                count: model.ids.length,
                maxPanes: limits.maxPanes,
                rect: model.rects[visualIndex],
                surface: measured.surfaces[visualIndex],
                cell: measured.cell,
                headerHeight: measured.headerHeight,
                minCols: limits.minCols,
                minRows: limits.minRows
            });
            return {
                groupId,
                model,
                visualIndex,
                measured,
                blockers,
                candidates: AXES.filter(axis => !blockers[axis])
            };
        }

        /* The split itself, once the tab is held: create, then place. */
        async function splitBehind(id, axis, request, view) {
            const cut = plan(view.model, view.visualIndex, axis);
            const posted = await split(id, axis, request);
            if (!posted || !posted.ok || !posted.session || !posted.session.session_id) {
                return {
                    ok: false,
                    error: String((posted && posted.error) || 'The split failed in this window.')
                };
            }
            return place(view, axis, cut, posted);
        }

        /* The placement to write, brought up to the arrangement the server
           held when the split answered. A split does not move the tab's
           presentation revision, so the revision the response carries is the
           one a write compares against -- and a divider moved while the request
           was out has already raised it. Written unchanged, the model read
           before the request would pass that check and put the old weights
           back. So a model read at another revision is replaced by the record
           the response carries, and the cut is planned again on it. A record
           that no longer holds the same panes, or has no arrangement for them,
           cannot be placed: null. */
        function current(view, axis, cut, posted) {
            const group = posted.group || null;
            const revision = group && group.presentation_revision;
            if (Number.isInteger(view.model.revision) && view.model.revision === revision) {
                return { view, cut };
            }
            const sourceId = view.model.ids[view.visualIndex];
            const stored = typeof recordModel === 'function'
                ? recordModel(group, posted.session.session_id)
                : null;
            if (!stored || !Number.isInteger(revision)) return null;
            const sameSessions = stored.ids.length === view.model.ids.length
                && view.model.ids.every(id => stored.ids.includes(id));
            const visualIndex = stored.ids.indexOf(sourceId);
            if (!sameSessions || visualIndex < 0) return null;
            const model = { ...stored, groupId: view.groupId, revision };
            return {
                view: { ...view, model, visualIndex },
                cut: plan(model, visualIndex, axis)
            };
        }

        /* A pane the server has made, put in its place in a tab that is not
           painted: drop the cache, write the arrangement against the revision
           the split returned, take the record. The tab is held by the caller. */
        async function place(requested, axis, requestedCut, posted) {
            /* From here the pane exists whatever else happens. */
            const placement = current(requested, axis, requestedCut, posted);
            if (!placement) {
                tab.discard(requested.groupId);
                tab.adopt(posted.group, null, null);
                return {
                    ok: true,
                    session: posted.session,
                    index: null,
                    note: SAVE_FAILED_NOTE
                };
            }
            const { view, cut } = placement;
            const arranged = policy.arrange({
                ids: view.model.ids,
                rects: view.model.rects,
                columnWeights: view.model.columnWeights,
                rowWeights: view.model.rowWeights,
                visualIndex: view.visualIndex,
                axis,
                plan: cut,
                newId: posted.session.session_id,
                splitRect
            });
            const layout = {
                ids: arranged.ids,
                rects: arranged.rects,
                columnWeights: arranged.columnWeights,
                rowWeights: arranged.rowWeights,
                baseCount: view.model.baseCount
            };
            const saved = await tab.write(
                view.groupId, posted.group && posted.group.presentation_revision, layout
            );
            tab.adopt(posted.group, layout, saved);
            return {
                ok: true,
                session: posted.session,
                index: arranged.index,
                note: saved.ok ? '' : SAVE_FAILED_NOTE
            };
        }

        return {
            /* Whether this window holds the pane in a tab it is not showing. */
            holds(sessionId) {
                return Boolean(groupOf(String(sessionId || '')));
            },

            /* The axes the pane could be halved on, or null when it is not in
               a tab this window holds. */
            async candidates(sessionId) {
                const view = await inspect(sessionId);
                return view ? view.candidates : null;
            },

            async reason(axis, sessionId) {
                const view = await inspect(sessionId);
                if (!view) return reason(axis, '', 0);
                if (!view.measured) return unmeasurable();
                return reason(axis, view.blockers[axis], view.model.ids.length);
            },

            /* A split the visible handler made, whose window moved on while
               its request was out — a tab picked, or the tab left loading. The
               pane exists; it is placed the way a split from behind places
               one, off the model and the cut read *before* the request, since
               the grid now showing is another tab's. Held from the call, with
               nothing before it, so a return to the tab waits for the write. */
            async placeAfterMove(view, axis, cut, posted) {
                const release = tab.hold(String((view && view.groupId) || ''));
                try {
                    return await place(view, axis, cut, posted);
                } finally {
                    release();
                }
            },

            async perform(sessionId, axis, request) {
                const id = String(sessionId || '');
                const view = await inspect(id);
                if (!view) {
                    return {
                        ok: false,
                        error: 'That pane is not in a session tab this window holds. Nothing changed.'
                    };
                }
                if (!view.measured) {
                    return {
                        ok: false,
                        refusal: { axis, candidates: [], reason: unmeasurable() }
                    };
                }
                if (!view.candidates.includes(axis)) {
                    return {
                        ok: false,
                        refusal: {
                            axis,
                            candidates: view.candidates,
                            reason: reason(axis, view.blockers[axis], view.model.ids.length)
                        }
                    };
                }
                /* Read again *here*, with nothing between it and the request:
                   the tab can have been opened while it was being measured, and
                   a tab that is showing is split by the handler that owns its
                   grid. */
                if (isShown(view.groupId)) {
                    return performShown(id, axis, request);
                }
                /* Held from here, with nothing between the check and the hold:
                   a tab opened from now on waits in its load for the write. */
                const release = tab.hold(view.groupId);
                try {
                    return await splitBehind(id, axis, request, view);
                } finally {
                    release();
                }
            }
        };
    }

    return {
        BLOCKED_BY_WINDOW,
        BLOCKED_BY_GRID,
        BLOCKED_BY_SIZE,
        SAVE_FAILED_NOTE,
        policy,
        create
    };
}));
