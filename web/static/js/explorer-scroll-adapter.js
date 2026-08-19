/* Explorer panel-scroll DOM adapter.

   explorer-scroll.js owns the revision/readiness policy; this classic script
   owns the live pane store, panel lookup, bounded animation-frame application
   and the one lazy-Mermaid correction. Loaded directly after
   explorer-viewer.js so both files share the same global function scope. */
    function explorerScrollPolicy() {
        return (typeof window !== 'undefined' && window.GridVibeExplorerScroll) || null;
    }

    function explorerPanelScrollStoreMatches(pane, store) {
        return Boolean(pane && store)
            && store.path === (pane._explorerFilePath || '')
            && store.content === pane._explorerFileContent;
    }

    function setExplorerPanelScrollState(index, state) {
        const pane = terminals[index];
        if (!pane) {
            return null;
        }
        const current = pane._explorerPanelScrollStore;
        if (state && current?.sourceState === state
            && explorerPanelScrollStoreMatches(pane, current)) {
            return current;
        }
        if (!state) {
            pane._explorerPanelScrollStore = null;
            return null;
        }
        const store = {
            path: pane._explorerFilePath || '',
            content: pane._explorerFileContent,
            sourceState: state,
            panels: {}
        };
        Object.entries(state.panels || {}).forEach(([mode, metrics]) => {
            if (!metrics) {
                return;
            }
            store.panels[mode] = {
                metrics: { ...metrics },
                sequence: 0,
                userMoved: false,
                mermaidRestoreAvailable: mode === 'preview',
                element: null,
                lastAppliedTop: null,
                lastAppliedLeft: null
            };
        });
        pane._explorerPanelScrollStore = store;
        return store;
    }

    function storeExplorerPanelMetrics(index, mode, metrics) {
        const pane = terminals[index];
        if (!pane || !mode || !metrics) {
            return null;
        }
        let store = pane._explorerPanelScrollStore;
        if (!explorerPanelScrollStoreMatches(pane, store)) {
            store = {
                path: pane._explorerFilePath || '',
                content: pane._explorerFileContent,
                sourceState: null,
                panels: {}
            };
            pane._explorerPanelScrollStore = store;
        }
        const previous = store.panels[mode];
        if (previous) {
            previous.sequence += 1;
        }
        const entry = {
            metrics: { ...metrics },
            sequence: 0,
            userMoved: false,
            mermaidRestoreAvailable: mode === 'preview',
            element: null,
            lastAppliedTop: null,
            lastAppliedLeft: null
        };
        store.panels[mode] = entry;
        return entry;
    }

    function explorerFilePanel(index, mode) {
        const list = document.getElementById(`explorer-list-${index}`);
        return list?.querySelector?.(`[data-explorer-file-panel="${mode}"]`) || null;
    }

    function rememberExplorerPanelScroll(index, mode) {
        const panel = explorerFilePanel(index, mode);
        const metrics = captureScrollMetrics(explorerPanelScrollTarget(panel));
        if (!metrics) {
            return null;
        }
        storeExplorerPanelMetrics(index, mode, metrics);
        return metrics;
    }

    function bindExplorerPanelScrollMovement(index, mode, pane, store, entry, scrollEl) {
        if (!scrollEl?.addEventListener || entry.element === scrollEl) {
            return;
        }
        entry.element = scrollEl;
        scrollEl.addEventListener('scroll', () => {
            const currentPane = terminals[index];
            const currentStore = currentPane?._explorerPanelScrollStore;
            const currentEntry = currentStore?.panels?.[mode];
            if (currentPane !== pane || currentStore !== store || currentEntry !== entry) {
                return;
            }
            const movedTop = Number.isFinite(entry.lastAppliedTop)
                && Math.abs(scrollEl.scrollTop - entry.lastAppliedTop) > 1;
            const movedLeft = Number.isFinite(entry.lastAppliedLeft)
                && Math.abs(scrollEl.scrollLeft - entry.lastAppliedLeft) > 1;
            if (movedTop || movedLeft) {
                entry.userMoved = true;
                entry.sequence += 1;
            }
        }, { passive: true });
    }

    function requestExplorerPanelScrollRestore(
        index,
        mode,
        { attempt = 0, sequence = null } = {}
    ) {
        const pane = terminals[index];
        const store = pane?._explorerPanelScrollStore;
        const entry = store?.panels?.[mode];
        if (!pane || !entry || entry.userMoved
            || !explorerPanelScrollStoreMatches(pane, store)) {
            return false;
        }
        /* The loader is a real scrollable box in a short pane. Applying a
           saved Preview offset to it clamps against the loader's tiny extent,
           and the page-level scroll capture then mistakes that programmatic
           clamp for the reader's new position. The Preview arrival below is
           the readiness event and starts its own bounded restore sequence. */
        if (mode === 'preview' && !pane._explorerPreviewLoaded) {
            return false;
        }
        const panel = explorerFilePanel(index, mode);
        if (!panel || panel.hidden) {
            return false;
        }
        const scrollEl = explorerPanelScrollTarget(panel);
        if (!scrollEl) {
            return false;
        }
        const restoreSequence = sequence === null
            ? (entry.sequence += 1)
            : sequence;
        if (entry.sequence !== restoreSequence) {
            return false;
        }
        const dimensions = {
            scrollHeight: scrollEl.scrollHeight,
            clientHeight: scrollEl.clientHeight,
            scrollWidth: scrollEl.scrollWidth,
            clientWidth: scrollEl.clientWidth
        };
        const policy = explorerScrollPolicy();
        const plan = policy
            ? policy.restorePlan(entry.metrics, dimensions, attempt)
            : { apply: true, retry: false, nextAttempt: attempt + 1 };
        if (plan.apply) {
            applyScrollMetrics(scrollEl, entry.metrics);
            entry.lastAppliedTop = scrollEl.scrollTop;
            entry.lastAppliedLeft = scrollEl.scrollLeft;
            bindExplorerPanelScrollMovement(index, mode, pane, store, entry, scrollEl);
        }
        if (plan.retry) {
            const schedule = typeof requestAnimationFrame === 'function'
                ? requestAnimationFrame
                : window.requestAnimationFrame;
            schedule?.(() => requestExplorerPanelScrollRestore(index, mode, {
                attempt: plan.nextAttempt,
                sequence: restoreSequence
            }));
        }
        return Boolean(plan.apply);
    }

    /* A lazy Mermaid placeholder can be shorter than the diagram that replaces
       it. One growth correction is allowed while the restored position is
       still untouched; the first reader scroll cancels it through the shared
       movement listener above. */
    function reapplyExplorerPreviewScrollAfterMermaid(preview, blockOffset) {
        const match = String(preview?.id || '').match(/^explorer-preview-(\d+)$/);
        if (!match) {
            return;
        }
        const index = Number(match[1]);
        const pane = terminals[index];
        const store = pane?._explorerPanelScrollStore;
        const entry = store?.panels?.preview;
        if (!pane || !entry || entry.userMoved || !entry.mermaidRestoreAvailable
            || document.getElementById(`explorer-preview-${index}`) !== preview
            || !explorerPanelScrollStoreMatches(pane, store)) {
            return;
        }
        const policy = explorerScrollPolicy();
        const shouldRestore = policy
            ? policy.shouldReapplyAfterGrowth(
                entry.metrics,
                blockOffset,
                entry.lastAppliedTop
            )
            : Number(blockOffset) < Number(entry.lastAppliedTop);
        if (!shouldRestore) {
            return;
        }
        entry.mermaidRestoreAvailable = false;
        requestExplorerPanelScrollRestore(index, 'preview');
    }
