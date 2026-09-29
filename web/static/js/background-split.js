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
     the page's load asks `settled` first, so it never paints the new pane from
     the server before its place in the layout exists, nor restores a cached
     view the split is about to drop.
   - **A pane that exists is reported, whatever happened to its layout.** The
     server has made it. If the arrangement could not be saved the result says
     so, and the tab comes back with the default arrangement for its size. */
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
            /* The session tab this window holds a pane in without showing it,
               or '' — for a pane on screen, one this window does not hold, or
               a window that has not settled on a tab. */
            groupOf,
            /* Whether the tab is now the shown one, or about to be. */
            isShown,
            /* Resolves once the tab's queued presentation has landed. */
            settle,
            /* The tab as the server would rebuild it: `{ groupId, ids,
               rects, columnWeights, rowWeights, baseCount }`, panes in visual
               order. Null when the tab is gone. */
            readModel,
            /* The shared grid, measured for this model's weights:
               `{ narrow, surfaces, cell, headerHeight }`, one surface per
               rectangle. Null when there is nothing to measure. */
            measure,
            /* Where the cut lands and what the axis weights become. */
            plan,
            splitRect,
            /* GridVibe's own sentence for a rule that refused an axis. */
            reason,
            unmeasurable,
            /* Create the pane on the server: `{ ok, session, group, error }`. */
            split,
            /* Drop the tab's cached view, unless it is the one painted. */
            discard,
            /* Write the arrangement through the revisioned transaction:
               `{ ok, revision, error }`. */
            saveLayout,
            /* Take the server's record of the tab into the tab strip. */
            adopt,
            /* The visible handler, for a tab that was opened meanwhile. */
            performShown,
            limits,
            onError = () => {}
        } = page || {};

        /* The splits in flight, per tab. More than one can be out for the same
           tab when two intents land together, so it is a set, not a flag. */
        const inFlight = new Map();

        function hold(groupId) {
            let release = null;
            const pending = new Promise(resolve => { release = resolve; });
            const held = inFlight.get(groupId) || new Set();
            held.add(pending);
            inFlight.set(groupId, held);
            return () => {
                held.delete(pending);
                if (!held.size && inFlight.get(groupId) === held) {
                    inFlight.delete(groupId);
                }
                release();
            };
        }

        async function inspect(sessionId) {
            const id = String(sessionId || '');
            const groupId = String(groupOf(id) || '');
            if (!groupId) return null;
            /* Best effort: the write sends only the arrangement, so a queued
               presentation that failed to land is reported and not fatal. */
            try { await settle(groupId); } catch (error) { onError(error); }
            const model = await readModel(groupId);
            if (!model) return null;
            const visualIndex = model.ids.indexOf(id);
            if (visualIndex < 0) return null;
            const measured = measure(model);
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

        /* The split itself, once the tab is held: create, drop the cache, write
           the arrangement, take the record. */
        async function splitBehind(id, axis, request, view) {
            const cut = plan(view.model, view.visualIndex, axis);
            const posted = await split(id, axis, request);
            if (!posted || !posted.ok || !posted.session || !posted.session.session_id) {
                return {
                    ok: false,
                    error: String((posted && posted.error) || 'The split failed in this window.')
                };
            }

            /* From here the pane exists whatever else happens. */
            discard(view.groupId);
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
            let saved = { ok: false };
            const revision = posted.group && posted.group.presentation_revision;
            if (Number.isInteger(revision)) {
                try {
                    saved = await saveLayout({
                        groupId: view.groupId,
                        expectedRevision: revision,
                        ids: arranged.ids,
                        rects: arranged.rects,
                        columnWeights: arranged.columnWeights,
                        rowWeights: arranged.rowWeights,
                        baseCount: view.model.baseCount
                    }) || { ok: false };
                } catch (error) {
                    onError(error);
                    saved = { ok: false };
                }
            }
            adopt(
                posted.group,
                saved.ok
                    ? {
                        ids: arranged.ids,
                        rects: arranged.rects,
                        columnWeights: arranged.columnWeights,
                        rowWeights: arranged.rowWeights,
                        baseCount: view.model.baseCount,
                        revision: saved.revision
                    }
                    : null
            );
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

            /* Resolves once no split of this tab is in flight: the one thing a
               load of the tab has to wait for. Immediately, almost always. */
            async settled(groupId) {
                const id = String(groupId || '');
                let held = inFlight.get(id);
                while (held && held.size) {
                    await Promise.all(Array.from(held));
                    held = inFlight.get(id);
                }
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
                const release = hold(view.groupId);
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
