/* GridVibeExplorerScroll — DOM-free policy for explorer panel restoration.

   A panel's scroll belongs to the panel and the content revision it was
   captured from. The DOM adapter decides when a panel is visible and reads
   its dimensions; this module decides whether a saved tab
   view is still valid, whether those dimensions can hold the requested
   offset, and whether another animation-frame attempt is still allowed.

   The classic-script adapter lives in explorer-scroll-adapter.js. This policy
   stays DOM-free and require()-able from Node so the revision and retry rules
   are executed by tests rather than inferred from the viewer's wiring. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerScroll = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const MAX_PANEL_SCROLL_RESTORE_ATTEMPTS = 6;

    function revisionMap(revisions) {
        if (typeof revisions === 'string') {
            return {
                source: revisions,
                preview: revisions,
                diff: revisions,
                directory: revisions
            };
        }
        return revisions && typeof revisions === 'object' ? revisions : {};
    }

    /* Durable view intent always returns, while each panel's content-bound
       scroll returns only when that panel's own revision still matches.

       "Matches" has to account for a revision the incoming render cannot know
       yet. Diff is fetched *after* the render, so at the moment a tab is
       restored the pane can say what the file's bytes are but not what its
       patch will be, and `current.diff` is simply absent. Absent is
       undetermined, not different — reading it as a mismatch is what threw
       the reader's Diff position away on every tab switch, and took the
       sidebar's offsets with it, because the diff's "mismatch" also made the
       whole view look changed. An undetermined Diff keeps its offset and
       hands it to that panel's own identity-guarded arrival path, which is
       the only thing that can actually tell whether the patch still matches.

       Undetermined is not a licence, though: it applies only while every
       revision the render *did* produce still matches. A file whose bytes
       moved has a patch that moved with them, so there the Diff offset drops
       with the rest rather than waiting to be told. And a revision that is
       present and different is a plain mismatch, exactly as before.

       Persisted v2 records apply the same rule in explorer-persistence.js,
       which remains the authority for them. */
    function resolveTabView(tab, revisions, options) {
        const view = tab && tab.view;
        if (!view) {
            return null;
        }
        const current = revisionMap(revisions);
        if (view.persistedRecord) {
            const resolveRecord = options && options.resolveRecord;
            return typeof resolveRecord === 'function'
                ? resolveRecord(view.persistedRecord, current) || null
                : null;
        }
        const storedRevisions = view.revisions || {};
        const stated = panel => !storedRevisions[panel]
            || current[panel] === storedRevisions[panel];
        // Every revision this render actually produced, and whether they all
        // still match. This is also what the sidebar's offsets ride on: they
        // describe the tree and the Git panel, which no unfetched patch can
        // invalidate.
        const same = Object.keys(storedRevisions)
            .filter(panel => current[panel] !== undefined)
            .every(stated);
        const matches = panel => stated(panel)
            || (panel === 'diff' && current.diff === undefined && same);
        const panels = {};
        Object.entries(view.scroll?.panels || {}).forEach(([panel, metrics]) => {
            if (matches(panel)) {
                panels[panel] = metrics;
            }
        });
        const scroll = {
            ...(view.scroll || {}),
            activeView: view.mode,
            panels,
            sidebar: same ? (view.scroll?.sidebar || {}) : {}
        };
        /* `listScrollLeft`/`listScrollTop` are the legacy exact-offset
           spelling of the very same scroller `directory` describes, and the
           restore falls back to them whenever `directory` is absent. Dropping
           only the one the revision guards left a genuinely new listing
           opening at the previous directory's offset — and, now that
           applyScrollMetrics() prefers an exact offset over a ratio,
           verbatim.
           They stand or fall together. */
        if (view.scroll?.directory && matches('directory')) {
            scroll.directory = view.scroll.directory;
        } else {
            delete scroll.directory;
            delete scroll.listScrollLeft;
            delete scroll.listScrollTop;
        }
        return {
            ...view,
            scroll,
            folds: matches('source') ? Array.from(tab.collapsedLines || []) : []
        };
    }

    function maxExtent(dimensions, scrollName, clientName) {
        const scrollSize = Number(dimensions && dimensions[scrollName]);
        const clientSize = Number(dimensions && dimensions[clientName]);
        return Math.max(
            0,
            (Number.isFinite(scrollSize) ? scrollSize : 0)
                - (Number.isFinite(clientSize) ? clientSize : 0)
        );
    }

    function axisIsSatisfiable(metrics, maxScroll, offsetName, ratioName) {
        const exact = Number(metrics && metrics[offsetName]);
        if (Number.isFinite(exact) && exact > 0) {
            return maxScroll >= exact;
        }
        const ratio = Number(metrics && metrics[ratioName]);
        if (Number.isFinite(ratio) && ratio > 0) {
            return maxScroll > 0;
        }
        return true;
    }

    function isSatisfiable(metrics, dimensions) {
        if (!metrics) {
            return true;
        }
        const maxTop = maxExtent(dimensions, 'scrollHeight', 'clientHeight');
        const maxLeft = maxExtent(dimensions, 'scrollWidth', 'clientWidth');
        return axisIsSatisfiable(metrics, maxTop, 'scrollTop', 'scrollTopRatio')
            && axisIsSatisfiable(metrics, maxLeft, 'scrollLeft', 'scrollLeftRatio');
    }

    /* Attempts are animation-frame opportunities, not a polling duration. An
       async panel gets a fresh bounded sequence when its content arrives; a
       permanently shorter replacement simply clamps on the final attempt. */
    function restorePlan(metrics, dimensions, attempt, options) {
        const currentAttempt = Math.max(0, Number(attempt) || 0);
        const configured = Number(options && options.maxAttempts);
        const maxAttempts = Number.isFinite(configured) && configured > 0
            ? Math.floor(configured)
            : MAX_PANEL_SCROLL_RESTORE_ATTEMPTS;
        const satisfiable = isSatisfiable(metrics, dimensions);
        const retry = Boolean(metrics)
            && !satisfiable
            && currentAttempt + 1 < maxAttempts;
        return {
            /* Do not manufacture a smaller offset while the panel is still
               growing. The final bounded attempt clamps only when no later
               readiness event can make the exact target satisfiable. */
            apply: Boolean(metrics) && (satisfiable || !retry),
            satisfiable,
            retry,
            nextAttempt: currentAttempt + 1
        };
    }

    function shouldReapplyAfterGrowth(metrics, blockOffset, restoredOffset) {
        if (!metrics) {
            return false;
        }
        if (metrics.wasAtBottom) {
            return true;
        }
        const exact = Number(metrics.scrollTop);
        const target = Number.isFinite(exact) ? exact : Number(restoredOffset);
        const offset = Number(blockOffset);
        return Number.isFinite(target) && target > 0
            && Number.isFinite(offset) && offset < target;
    }

    return {
        MAX_PANEL_SCROLL_RESTORE_ATTEMPTS,
        resolveTabView,
        isSatisfiable,
        restorePlan,
        shouldReapplyAfterGrowth
    };
}));
