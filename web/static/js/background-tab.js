/* GridVibeBackgroundTab — a session tab this window holds without showing it,
   read and written as data.

   An agent working in one tab must not move what the person is looking at, so
   an edit to another tab's arrangement — a split, a divider moved — never goes
   through the grid on screen. The tab is a *model*: pane order, one rectangle
   each, two lists of track weights. This module is what every such edit shares:

   - **reading the tab**: its queued presentation settled first (best effort),
     then its model, from the page's cached view or the server's summary;
   - **measuring it** against the window's one shared grid, for the model's own
     weights;
   - **the hold**: from the moment an edit commits to changing the tab until
     its arrangement is written, a load of that tab waits (`settled`), so a tab
     picked mid-edit is never painted from the arrangement being replaced nor
     restored from a cached view about to be dropped, and the page does not
     publish the arrangement of a view a write is replacing (`held`). A load
     whose read was out when a hold was taken reads again (`holdCount`). The
     visible split handler holds its own tab the same way, from before its
     request until the new pane is painted or placed;
   - **the writes**, both through the group's revisioned presentation
     transaction, then taking the server's record into the tab strip. A write
     that changes the pane set (`write`, the split's) drops the tab's cached
     view first (the page keeps the painted one): its panes are rebuilt from
     the server. A write of geometry alone (`writeGeometry`, the resize's)
     drops nothing: the cached view keeps its panes, scrollback and pages and
     takes the new arrangement, or, when the outcome is unknown, reads the
     stored one before it is next painted. A refused one leaves it as it was.

   `background-split.js` and `background-resize.js` are the two edits on top.
   One instance per window is shared by both, so a load waits for either.

   DOM-free: everything that touches the page goes through the injected `page`,
   which is what lets Node execute the ordering. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeBackgroundTab = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    function create(page) {
        const {
            /* Resolves once the tab's queued presentation has landed. */
            settle,
            /* The tab as the server would rebuild it: `{ groupId, ids,
               rects, columnWeights, rowWeights, baseCount }`, panes in visual
               order. Null when the tab is gone. */
            readModel,
            /* The shared grid, measured for this model's weights:
               `{ narrow, metrics, surfaces, cell, headerHeight }`, one surface
               per rectangle. Null when there is nothing to measure. */
            measure,
            /* Drop the tab's cached view, unless it is the one painted. */
            discard,
            /* Write an arrangement that landed into the tab's cached view,
               unless it is the one painted: `(groupId, layout)`. */
            updateGeometry,
            /* The tab's cached view may no longer match its stored
               arrangement: read that before the view is next painted. */
            markGeometryStale,
            /* Write the arrangement through the revisioned transaction:
               `{ ok, revision, error, unknown }`. */
            saveLayout,
            /* Take the server's record of the tab into the tab strip, with the
               arrangement just written, or null when none was. */
            adopt,
            onError = () => {}
        } = page || {};

        /* The edits in flight, per tab. More than one can be out for the same
           tab when two intents land together, so it is a set, not a flag. */
        const inFlight = new Map();
        /* Every hold ever taken, per tab. A read that saw no hold at either
           end can still have had one start and finish while it was out. */
        const holdCounts = new Map();

        async function save(groupId, expectedRevision, layout) {
            if (!Number.isInteger(expectedRevision)) {
                return { ok: false, error: 'No revision to write the arrangement against.' };
            }
            try {
                return await saveLayout({ groupId, expectedRevision, ...layout }) || { ok: false };
            } catch (error) {
                onError(error);
                return { ok: false, thrown: true, error: String((error && error.message) || error) };
            }
        }

        return {
            /* Hold the tab until the returned release is called. Taken with
               nothing between it and the edit's last check that the tab is not
               showing: a tab opened from then on waits in its load. */
            hold(groupId) {
                const id = String(groupId || '');
                let release = null;
                const pending = new Promise(resolve => { release = resolve; });
                const held = inFlight.get(id) || new Set();
                held.add(pending);
                inFlight.set(id, held);
                holdCounts.set(id, (holdCounts.get(id) || 0) + 1);
                return () => {
                    held.delete(pending);
                    if (!held.size && inFlight.get(id) === held) {
                        inFlight.delete(id);
                    }
                    release();
                };
            },

            /* Whether an edit of this tab is in flight right now: its cached
               view may be about to take, or to lose, the arrangement it shows. */
            held(groupId) {
                const held = inFlight.get(String(groupId || ''));
                return Boolean(held && held.size);
            },

            /* How many holds the tab has been taken under so far. A load that
               reads the tab compares it before and after: a change means an
               edit began while the read was out, so the read may predate it. */
            holdCount(groupId) {
                return holdCounts.get(String(groupId || '')) || 0;
            },

            /* Resolves once no edit of this tab is in flight: the one thing a
               load of the tab has to wait for. Immediately, almost always. */
            async settled(groupId) {
                const id = String(groupId || '');
                let held = inFlight.get(id);
                while (held && held.size) {
                    await Promise.all(Array.from(held));
                    held = inFlight.get(id);
                }
            },

            /* The tab's model, after its queued presentation. Best effort on
               the queue: the write sends only the arrangement, so a queued
               presentation that failed to land is reported and not fatal. A
               list that cannot be read throws. */
            async read(groupId) {
                const id = String(groupId || '');
                try { await settle(id); } catch (error) { onError(error); }
                return readModel(id);
            },

            measure(model) {
                return measure(model);
            },

            /* Drop the tab's cached view without writing: an edit that made a
               change it cannot place still leaves a view that no longer matches. */
            discard(groupId) {
                discard(String(groupId || ''));
            },

            /* The pane-set-changing write: drop the cache, then write. From
               the drop on, the tab is rebuilt from what the server holds the
               next time it is shown, whatever the write answers. A write that
               threw is `{ ok: false, thrown }`: it may have landed. No
               revision to write against writes nothing. */
            async write(groupId, expectedRevision, layout) {
                discard(groupId);
                return save(groupId, expectedRevision, layout);
            },

            /* The geometry-only write: the same panes, new rectangles or
               weights. Nothing is dropped. Landed, the cached view takes the
               arrangement; possibly landed (thrown, unknown, or accepted with
               no newer revision), it is marked to read the stored one; refused
               or never sent, it is left exactly as it was. Same answer as
               `write`. */
            async writeGeometry(groupId, expectedRevision, layout) {
                const saved = await save(groupId, expectedRevision, layout);
                if (saved.ok && Number.isInteger(saved.revision) && saved.revision > expectedRevision) {
                    updateGeometry(groupId, layout);
                } else if (saved.ok || saved.thrown || saved.unknown) {
                    markGeometryStale(groupId);
                }
                return saved;
            },

            /* The server's record, with the arrangement just written when the
               write landed, so the next reading of the tab does not start from
               the one it replaced. */
            adopt(group, layout, saved) {
                adopt(
                    group,
                    saved && saved.ok ? { ...layout, revision: saved.revision } : null
                );
            }
        };
    }

    return { create };
}));
