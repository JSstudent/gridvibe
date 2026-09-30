/* GridVibeBackgroundResize — a divider moved in a session tab this window is
   not showing, done without showing it.

   The visible resize measures the live grid and repaints it. A tab that is not
   showing has no live grid, and must not be given one: switching to it would
   move what the person is looking at. So, like a split from behind, the
   resize is an edit to the tab's *model* — pane order, one rectangle each, two
   lists of track weights — measured against the window's one shared grid:

   1. read the tab's model (`background-tab.js`), and the server's record of
      it, and check the revision the agent read against the record;
   2. check that the model is the record: the same panes in the same order,
      the same rectangles, the same weights — the line index names a line of
      the grid the agent read, so a tab that differs is refused, not guessed;
   3. measure the shared grid for the model's weights, take the pointer drag's
      track groups off the model's rectangles, plan the move, and check every
      pane still meets its minimum at the new weights;
   4. hold the tab, drop its cached view, write the weights through the
      group's revisioned presentation transaction, and take the new record.

   Two halves, like every module here:

   - `policy` is pure: whether the model is the record, and whether every pane
     still fits at the new weights. No DOM, no timers, no fetch.
   - `create(page)` is the sequence, with every page read and write injected.

   Rules the sequence exists to keep:

   - **Refuse before anything is written**, with the visible resize's own
     sentence for each refusal: the revision changed, the layout differs, the
     line is outside the grid, no shared edge, no measurable space, a pane
     below its minimum.
   - **A tab that was opened meanwhile is resized by the visible handler.** It
     owns a live grid and repaints it; this path does not.
   - **A tab opened while its write is in flight waits for it**, through the
     tab's hold, which a load asks first.
   - **Once the write has started, a lost answer is `unknown`**, never a claim
     that nothing changed. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeBackgroundResize = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Hairline borders around a terminal: the live minimum check's allowance. */
    const EDGE_PX = 2;

    const positive = (value, fallback) => (Number(value) > 0 ? Number(value) : fallback);

    const sameNumbers = (a, b) => Array.isArray(a) && Array.isArray(b)
        && a.length === b.length
        && a.every((value, index) => Math.abs(Number(value) - Number(b[index])) < 0.000001);

    const policy = {
        /* Whether the tab's model is the arrangement the server holds — the
           one the agent read its line index and revision off. The same three
           comparisons the visible resize makes against the live grid. */
        sameLayout(model, live) {
            const stored = (live && live.geometry) || {};
            const rects = (model && model.rects) || [];
            const ids = (model && model.ids) || [];
            const panes = (live && Array.isArray(live.panes)) ? live.panes : [];
            const sameRects = Array.isArray(stored.split_slot_rects)
                && stored.split_slot_rects.length === rects.length
                && rects.every((rect, index) => ['x', 'y', 'w', 'h'].every(
                    key => Number(rect[key]) === Number(stored.split_slot_rects[index][key])
                ));
            return sameRects
                && sameNumbers(model.columnWeights, stored.column_weights)
                && sameNumbers(model.rowWeights, stored.row_weights)
                && ids.length === panes.length
                && ids.every((id, index) => id === (panes[index] && panes[index].session_id));
        },

        /* Whether every pane still holds its minimum at the candidate weights:
           an area no smaller than a sixteenth of the grid's track space, and,
           for a pane that draws a terminal, the character floor at the
           window's cell and header size. An explorer pane has no character
           floor. The rule `validateResizeCandidate` applies to the live grid. */
        fits({
            surfaces, columnTrackSpace, rowTrackSpace, exempt, cell, headerHeight,
            minCols, minRows, minSurfaceRatio
        }) {
            const minimumSurface = Number(columnTrackSpace) * Number(rowTrackSpace) * Number(minSurfaceRatio);
            const cellWidth = positive(cell && cell.width, 8);
            const cellHeight = positive(cell && cell.height, 17);
            const header = positive(headerHeight, 34);
            return (Array.isArray(surfaces) ? surfaces : []).every((surface, index) => {
                const width = Number(surface && surface.width) || 0;
                const height = Number(surface && surface.height) || 0;
                if (width * height < minimumSurface) {
                    return false;
                }
                if (exempt && exempt[index]) {
                    return true;
                }
                return Math.floor(Math.max(0, width - EDGE_PX) / cellWidth) >= minCols
                    && Math.floor(Math.max(0, height - header - EDGE_PX) / cellHeight) >= minRows;
            });
        }
    };

    function create(page) {
        const {
            /* The window's `GridVibeBackgroundTab`, shared with the split. */
            tab,
            /* Whether this window holds the tab in its list at all. */
            holds,
            /* Whether the tab is now the shown one, or about to be. */
            isShown,
            /* The server's record of the tab's arrangement, as `list_panes`
               reads it: `{ ok, presentation_revision, geometry, panes, error }`. */
            readLayout,
            /* Per pane of the model, whether it is exempt from the character
               floor (an explorer). */
            readExemptions,
            /* The pointer drag's track groups on either side of a line, and
               the shared edge along it, both off a given list of rectangles. */
            trackGroups,
            sharedEdges,
            /* `GridVibeSplitGeometry.planDividerResize`. */
            planResize,
            /* The window's record of the tab, taken with the new arrangement
               once it is written. */
            groupRecord,
            /* A counter that moves when a pane of the tab closes, and whether
               a close is waiting to be painted into the tab: its arrangement
               is settled when the tab is next shown, over anything written
               now. */
            closeEpoch = () => 0,
            closePending = () => false,
            /* The visible handler, for a tab that was opened meanwhile. */
            performShown,
            limits
        } = page || {};

        const refuse = error => ({ ok: false, error: `${error} Nothing changed.` });

        /* Everything decided before the tab is held: the reads, the checks,
           the plan. Answers `{ answer }` for a refusal or a hand-off to the
           visible handler, or `{ plan }` for a write. Throws when a read does. */
        async function prepare(intent, groupId, epoch) {
            const axis = String(intent.axis || '');
            const lineIndex = Number(intent.line_index);
            const position = Number(intent.position);
            const expectedRevision = Number(intent.expected_revision);
            const closed = () => closeEpoch(groupId) !== epoch || closePending(groupId);

            const model = await tab.read(groupId);
            if (!model) return { answer: refuse('The session is no longer open.') };
            const live = await readLayout(groupId);
            if (closed()) return { answer: refuse('A pane closed while the resize was being prepared.') };
            if (!live || !live.ok) {
                return { answer: refuse((live && live.error) || 'The session is no longer open.') };
            }
            if (live.presentation_revision !== expectedRevision) {
                return { answer: refuse('The session layout changed; read list_panes and retry.') };
            }
            const rects = model.rects;
            const vertical = axis === 'vertical';
            const count = vertical ? model.columnWeights.length : model.rowWeights.length;
            if (!Number.isInteger(lineIndex) || lineIndex < 1 || lineIndex >= count
                || !Number.isFinite(position) || position <= 0 || position >= 1) {
                return { answer: refuse('That divider or position is outside this grid.') };
            }
            if (!sharedEdges(rects, axis, lineIndex).length) {
                return { answer: refuse('There is no shared pane edge at that divider.') };
            }
            if (!policy.sameLayout(model, live)) {
                return { answer: refuse('The page and stored layout differ; refresh the session and retry.') };
            }

            const measured = tab.measure(model);
            if (model.ids.length < 2 || (measured && measured.narrow)) {
                return { answer: refuse('This viewport is too narrow to resize the grid.') };
            }
            const metrics = measured && measured.metrics;
            if (!metrics || !(metrics.gridContentWidth > 0) || !(metrics.gridContentHeight > 0)) {
                return { answer: refuse('The grid has no measurable space.') };
            }
            const groups = trackGroups(rects, axis, lineIndex);
            if (!groups) return { answer: refuse('That divider has no adjacent tracks.') };
            const candidate = planResize(
                vertical ? model.columnWeights : model.rowWeights,
                vertical ? metrics.columnSizes : metrics.rowSizes,
                vertical ? metrics.columnGap : metrics.rowGap,
                groups,
                lineIndex,
                position,
                vertical ? metrics.gridContentWidth : metrics.gridContentHeight
            );
            if (!candidate) return { answer: refuse('That position leaves no space beside the divider.') };
            const columnWeights = vertical ? candidate : model.columnWeights;
            const rowWeights = vertical ? model.rowWeights : candidate;
            const after = tab.measure({ ...model, columnWeights, rowWeights });
            const exempt = await readExemptions(groupId, model.ids);
            if (closed()) return { answer: refuse('A pane closed while the resize was being prepared.') };
            const fits = Boolean(after && after.metrics) && policy.fits({
                surfaces: after.surfaces,
                columnTrackSpace: after.metrics.columnTrackSpace,
                rowTrackSpace: after.metrics.rowTrackSpace,
                exempt,
                cell: after.cell,
                headerHeight: after.headerHeight,
                minCols: limits.minCols,
                minRows: limits.minRows,
                minSurfaceRatio: limits.minSurfaceRatio
            });
            if (!fits) {
                return { answer: refuse('That position would make a pane smaller than its minimum width or height.') };
            }
            return {
                plan: {
                    expectedRevision,
                    layout: { ids: model.ids, rects, columnWeights, rowWeights, baseCount: model.baseCount }
                }
            };
        }

        /* The write, with the tab held by the caller. From here a lost answer
           is `unknown`, never "nothing changed". */
        async function commit(groupId, epoch, plan) {
            const { expectedRevision, layout } = plan;
            const saved = await tab.write(groupId, expectedRevision, layout);
            if (closeEpoch(groupId) !== epoch) {
                return {
                    ok: false,
                    unknown: true,
                    error: 'A pane closed while the resize write was in flight. The page did not paint stale weights, but the saved layout may still hold them; read list_panes before retrying.'
                };
            }
            if (saved.thrown) {
                return {
                    ok: false,
                    unknown: true,
                    error: `The resize write could not be confirmed: ${saved.error}. Read list_panes before retrying; the outcome is unknown.`
                };
            }
            if (!saved.ok && !saved.unknown) {
                return refuse(saved.error || 'The layout could not be saved.');
            }
            if (!Number.isInteger(saved.revision) || saved.revision <= expectedRevision) {
                return {
                    ok: false,
                    unknown: true,
                    error: 'The resize write returned no valid revision. Read list_panes before retrying; the outcome is unknown.'
                };
            }
            tab.adopt(groupRecord(groupId), layout, saved);
            return { ok: true, result: {
                group_id: groupId,
                revision: saved.revision,
                column_weights: layout.columnWeights,
                row_weights: layout.rowWeights,
                panes: layout.ids.map((sessionId, index) => {
                    const rect = layout.rects[index];
                    return { session_id: sessionId, index, rect: { x: rect.x, y: rect.y, w: rect.w, h: rect.h } };
                })
            } };
        }

        async function perform(intent) {
            const request = intent || {};
            const groupId = String(request.group_id || '');
            if (groupId && isShown(groupId)) {
                return performShown(request);
            }
            if (!groupId || !holds(groupId)) {
                return refuse('This window is switching session tabs; try the resize again shortly.');
            }
            if (closePending(groupId)) {
                return refuse('A pane in this session closed and its tab has not been shown since, so its arrangement is not settled yet.');
            }
            const epoch = closeEpoch(groupId);
            let prepared = null;
            try {
                prepared = await prepare(request, groupId, epoch);
            } catch (error) {
                return refuse(`The resize failed: ${(error && error.message) || error}`);
            }
            if (prepared.answer) return prepared.answer;
            /* Read again *here*, with nothing between it and the hold: a tab
               opened while this was measured is resized by the handler that
               owns its grid, from the start. */
            if (isShown(groupId)) {
                return performShown(request);
            }
            const release = tab.hold(groupId);
            try {
                return await commit(groupId, epoch, prepared.plan);
            } finally {
                release();
            }
        }

        return { perform };
    }

    return { policy, create };
}));
