    /* ─────────────────────────────────────────────
       Explorer change listener
       (docs/r&d/planed/done/explorer_git_change_listener_plan_2026-07-30.md,
       and its §14 amendment, which supersedes that plan's invariant 2 and its
       "no file-changed notice for an open file" non-goal for the *viewer*
       only — the editor buffer stays untouchable).

       One page-level scheduler runs two independent per-pane requests while
       the page is visible, feeding three surfaces:

       1. Repository state — poll the cheap GET /api/explorer/<id>/git/state
          semantic-revision endpoint. An unchanged revision costs one
          `git status` on the server and causes zero DOM writes, zero
          refetches, zero log lines. One poll serves two consumers, each with
          its own baseline, so a pane with both surfaces open still makes one
          request:
            a. the Git sidebar, while it is open — a changed revision quietly
               refetches the full /git/repo summary and swaps the sidebar in
               place;
            b. the directory listing and the Files tree (§15) — a changed
               revision quietly re-lists what is on screen through
               refreshExplorerFilesystemSurfacesQuiet, so a file created or
               modified outside GridVibe appears there with its `?`/`M` badge
               instead of waiting for a manual refresh. Only panes inside a
               Git worktree have this consumer: the revision *is* the signal,
               so an ignored or non-repository file is picked up exactly when
               Git itself would report it.
       2. Open file — while the viewer is showing a file, poll the cheaper
          GET /api/explorer/<id>/file/state (one `stat`, no read). A changed
          token re-reads the file and updates the viewer in place, so a file
          edited outside GridVibe stops going stale in the tab the user is
          actually looking at.

       Every apply is deferred while the user is interacting (commit message
       focused, IME composition, context menu or modal open, pointer down,
       reading a scrolled sidebar/tree, an active text selection).

       Invariants: read-only; the editor buffer is never touched, because a
       pane with an open editor is ineligible and the editor keeps its own
       save-conflict flow; every apply goes through one narrow quiet door
       (refreshExplorerGitRepoQuiet / refreshExplorerOpenFileQuiet /
       refreshExplorerFilesystemSurfacesQuiet) and never through
       loadExplorerPane, reloadExplorerTree or openExplorerFile, which reset
       scroll, tabs and search; no work when invisible; one in-flight request
       per pane per check; recursive setTimeout only; stale responses dropped
       by pane/session (and, for files, path) identity.
       Loaded before terminals.js; `terminals`, `sessionIds`,
       `isExplorerPaneInstance`, `refreshExplorerGitRepoQuiet`,
       `applyExplorerGitRepoQuiet`, `refreshExplorerOpenFileQuiet` and
       `refreshExplorerFilesystemSurfacesQuiet` all resolve at call time.
    ───────────────────────────────────────────── */
    const EXPLORER_GIT_WATCH_BASE_MS = 5000;          // local pane
    const EXPLORER_GIT_WATCH_REMOTE_BASE_MS = 10000;  // ssh pane
    const EXPLORER_GIT_WATCH_DURATION_FACTOR = 6;     // adaptive floor
    const EXPLORER_GIT_WATCH_MAX_MS = 60000;
    const EXPLORER_GIT_WATCH_BACKOFF_MS = [10000, 20000, 30000];
    const EXPLORER_GIT_WATCH_MAX_FAILURES = 5;        // then suspend
    const EXPLORER_GIT_WATCH_CHURN_LIMIT = 3;         // consecutive changes
    let explorerGitWatchTimer = null;
    let explorerGitWatchRunning = false;
    let explorerGitWatchPointerDown = false;

    function explorerGitWatchBaseMs(pane) {
        return pane?._session?.mode === 'ssh'
            ? EXPLORER_GIT_WATCH_REMOTE_BASE_MS
            : EXPLORER_GIT_WATCH_BASE_MS;
    }

    function explorerGitWatchChurnMultiplier(changes) {
        return (changes || 0) >= EXPLORER_GIT_WATCH_CHURN_LIMIT
            ? 2 ** (changes - EXPLORER_GIT_WATCH_CHURN_LIMIT + 1)
            : 1;
    }

    /* Adaptive floor (plan §5.4): a poll that takes 900 ms yields a 5.4 s
       floor; the listener can never consume more than ~1/6 of wall-clock time
       in Git work. The churn damper doubles the interval per consecutive
       changed poll past the limit, capped at MAX — a build or agent rewriting
       files stops repainting the sidebar every base interval. Shared by both
       checks: a `stat` is far cheaper than `git status`, so the Git cadence is
       a conservative upper bound on the open-file check's cost. */
    function explorerWatchNextDelay(pane, lastMs, changes) {
        const base = explorerGitWatchBaseMs(pane);
        const floor = Math.max(base, (lastMs || 0) * EXPLORER_GIT_WATCH_DURATION_FACTOR);
        return Math.min(
            Math.max(floor * explorerGitWatchChurnMultiplier(changes), base),
            EXPLORER_GIT_WATCH_MAX_MS
        );
    }

    function explorerWatchBackoff(failures, nextDelay) {
        const step = Math.min(failures || 0, EXPLORER_GIT_WATCH_BACKOFF_MS.length);
        return step > 0 ? EXPLORER_GIT_WATCH_BACKOFF_MS[step - 1] : nextDelay;
    }

    function explorerGitWatchNextDelay(pane) {
        return explorerWatchNextDelay(
            pane, pane._explorerGitWatchLastMs, pane._explorerGitWatchChanges
        );
    }

    function explorerGitWatchBackoff(pane) {
        return explorerWatchBackoff(
            pane._explorerGitWatchFailures, explorerGitWatchNextDelay(pane)
        );
    }

    function explorerFileWatchNextDelay(pane) {
        return explorerWatchNextDelay(
            pane, pane._explorerFileWatchLastMs, pane._explorerFileWatchChanges
        );
    }

    function explorerFileWatchBackoff(pane) {
        return explorerWatchBackoff(
            pane._explorerFileWatchFailures, explorerFileWatchNextDelay(pane)
        );
    }

    /* True while the pane/session pair a request was issued against is still
       the one sitting at `index` — every await in this file is followed by it,
       or the response belongs to another pane. */
    function explorerWatchPaneCurrent(index, pane, sessionId) {
        return terminals[index] === pane && sessionIds[index] === sessionId;
    }

    function scheduleExplorerGitWatch(delayMs) {
        if (explorerGitWatchTimer) {
            clearTimeout(explorerGitWatchTimer);
        }
        explorerGitWatchTimer = setTimeout(() => {
            explorerGitWatchTimer = null;
            explorerGitWatchTick();
        }, delayMs);
    }

    /* Sidebar consumer of the shared /git/state poll. The watcher never
       bootstraps a sidebar load; it only detects drift from a baseline the
       user's own actions established. */
    function explorerGitWatchSidebarConsumer(pane) {
        if (!pane._explorerGitSidebarOpen || !pane._explorerGitRepoLoaded) {
            return false;
        }
        if (typeof pane._explorerGitRevision !== 'string' || !pane._explorerGitRevision) {
            return false;
        }
        return !pane._explorerGitRepoLoading && !pane._explorerGitRepoRefreshing;
    }

    /* Filesystem-surface consumer (§15): a directory listing or an open Files
       tree, inside a Git worktree. `_explorerGitContext` is set by every
       listing and file load, so a non-repository root simply never has this
       consumer and never polls — matching the rule that the Git revision is
       the change signal. Unlike the sidebar this one *is* bootstrapped by the
       watcher (the surfaces carry no revision of their own), silently: the
       first poll records the baseline without repainting anything. */
    function explorerFsWatchConsumer(pane) {
        if (!pane._explorerGitContext?.available) {
            return false;
        }
        if (pane._explorerFsWatchSuspended) {
            return false;
        }
        return pane._explorerMode === 'directory' || Boolean(pane._explorerTreeSidebarOpen);
    }

    /* Eligibility gates (plan §5.3) — no request is made unless all hold.
       Closing every consumer surface, switching session-group tabs (the pane
       leaves `terminals`), or a non-Git root therefore stops all polling with
       no server-side unsubscribe. */
    function explorerGitWatchEligible(index) {
        const pane = terminals[index];
        if (!pane || !isExplorerPaneInstance(pane) || !sessionIds[index]) {
            return false;
        }
        if (!explorerGitWatchSidebarConsumer(pane) && !explorerFsWatchConsumer(pane)) {
            return false;
        }
        // A GridVibe Git action is authoritative, and a filesystem
        // mutation/save triggers its own sidebar and surface refresh.
        if (pane._explorerGitActionBusy || pane._explorerFsBusy || pane._explorerEdit?.saving) {
            return false;
        }
        if (pane._explorerGitWatchInFlight || pane._explorerGitWatchSuspended) {
            return false;
        }
        return true;
    }

    /* Open-file eligibility. The baseline (`_explorerFileStateRevision`, set by
       every file load and save) is what arms this check, so the views that
       have nothing to re-read — directory listings, the empty viewer, the
       image viewer, commit diffs — opt out simply by clearing it. */
    function explorerFileWatchEligible(index) {
        const pane = terminals[index];
        if (!pane || !isExplorerPaneInstance(pane) || !sessionIds[index]) {
            return false;
        }
        if (pane._explorerMode !== 'file' || !pane._explorerFilePath) {
            return false;
        }
        if (typeof pane._explorerFileStateRevision !== 'string' || !pane._explorerFileStateRevision) {
            return false;
        }
        // The in-place editor owns the buffer; drift is surfaced by its own
        // save-conflict bar, never by swapping the file out from under it.
        if (pane._explorerEdit) {
            return false;
        }
        // A GridVibe action already refreshes what it touched.
        if (pane._explorerGitActionBusy || pane._explorerFsBusy || pane._explorerDiffUndoBusy) {
            return false;
        }
        if (pane._explorerFileWatchInFlight || pane._explorerFileWatchSuspended) {
            return false;
        }
        return !pane._explorerFileWatchRefreshing;
    }

    function explorerGitWatchOnFailure(pane, status) {
        // A 404 means the session is gone: suspend immediately and silently.
        if (status === 404) {
            pane._explorerGitWatchFailures = EXPLORER_GIT_WATCH_MAX_FAILURES;
        } else {
            pane._explorerGitWatchFailures = (pane._explorerGitWatchFailures || 0) + 1;
        }
        if (pane._explorerGitWatchFailures >= EXPLORER_GIT_WATCH_MAX_FAILURES) {
            explorerGitWatchSuspend(pane);
        }
    }

    function explorerGitWatchSuspend(pane) {
        pane._explorerGitWatchSuspended = true;
        pane._explorerGitWatchPending = null;
        /* One render so the muted "Live updates paused — use Refresh" line
           appears above the last good sidebar; nothing else changes. Re-armed
           by refreshExplorerPane(), by reopening the sidebar, or by a
           successful GridVibe Git action (all clear the flag). */
        const index = terminals.indexOf(pane);
        if (index !== -1) {
            renderExplorerGitPanel(index);
        }
    }

    /* Shared half of the deferral gates: a menu or modal is anchored to (or
       decides about) a row that must not move, and a pointer that is down is a
       drag, a selection, or a click in progress. */
    function explorerWatchInteractionActive() {
        if (document.getElementById('explorer-ctx-menu')) {
            return true;
        }
        const confirmModal = document.getElementById('genericConfirmModal');
        if (confirmModal?.classList.contains('visible')) {
            return true;
        }
        const nameModal = document.getElementById('explorerNameModal');
        if (nameModal?.classList.contains('visible')) {
            return true;
        }
        return explorerGitWatchPointerDown;
    }

    /* Deferral gates (plan §6.2) — postpone the apply, never drop it. A panel
       re-render replaces innerHTML wholesale, so it must not happen while the
       user is typing, composing, choosing from a menu/modal, dragging, or
       reading a scrolled panel. */
    function explorerGitWatchDeferralActive(index, pane) {
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!panel) {
            return false;
        }
        const active = document.activeElement;
        if (active && panel.contains(active)) {
            return true;
        }
        if (pane._explorerGitComposing) {
            return true;
        }
        if (explorerWatchInteractionActive()) {
            return true;
        }
        return panel.matches(':hover') && panel.scrollTop > 0;
    }

    /* Viewer deferral gates. Scroll position, view mode and the search query
       all survive an in-place refresh, so the viewer needs a shorter list than
       the sidebar: the Find box's caret and a selection the user is in the
       middle of making are what a re-render would actually destroy. */
    function explorerFileWatchDeferralActive(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return false;
        }
        if (explorerWatchInteractionActive()) {
            return true;
        }
        const active = document.activeElement;
        if (active && list.contains(active) && active.matches('input, textarea, [contenteditable]')) {
            return true;
        }
        const selection = window.getSelection?.();
        return Boolean(
            selection
            && !selection.isCollapsed
            && selection.anchorNode
            && list.contains(selection.anchorNode)
        );
    }

    /* Filesystem-surface deferral gates. A listing re-render replaces the rows
       and a tree re-render replaces the panel, so the same rule as the sidebar
       applies: never while a menu/modal is open, a pointer is down, focus sits
       in one of those surfaces, or the user is reading them scrolled away from
       the top. Scroll offset, Find query and expansion state all survive the
       apply, so nothing else needs guarding. */
    function explorerFsWatchDeferralActive(index) {
        if (explorerWatchInteractionActive()) {
            return true;
        }
        const active = document.activeElement;
        const surfaces = [
            document.getElementById(`explorer-tree-panel-${index}`),
            document.getElementById(`explorer-list-${index}`)
        ];
        return surfaces.some(surface => {
            if (!surface) {
                return false;
            }
            if (active && surface.contains(active)) {
                return true;
            }
            return surface.matches(':hover') && surface.scrollTop > 0;
        });
    }

    function explorerFsWatchOnFailure(pane) {
        pane._explorerFsWatchFailures = (pane._explorerFsWatchFailures || 0) + 1;
        if (pane._explorerFsWatchFailures >= EXPLORER_GIT_WATCH_MAX_FAILURES) {
            // Silent, like the open-file watch: the listing and tree have no
            // "live" affordance to contradict, and they keep their last good
            // contents. Any manual refresh or navigation re-arms it through
            // resetExplorerFsWatchBaseline().
            pane._explorerFsWatchSuspended = true;
            pane._explorerFsWatchPending = null;
        }
    }

    /* The re-list is the expensive half here, so a deferred change holds only
       its revision and re-lists once the gate clears. The baseline advances
       only on a successful apply, so a failed pass simply retries. */
    async function explorerFsWatchFlushPending(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const revision = pane?._explorerFsWatchPending || '';
        if (!pane || !sessionId || !revision) {
            return;
        }
        if (!explorerFsWatchConsumer(pane) || revision === pane._explorerFsWatchRevision) {
            pane._explorerFsWatchPending = null;
            return;
        }
        // Hold the payload (never drop it) while a GridVibe action owns these
        // surfaces or an earlier quiet re-list is still running.
        if (pane._explorerGitActionBusy || pane._explorerFsBusy || pane._explorerFsWatchRefreshing) {
            return;
        }
        if (explorerFsWatchDeferralActive(index)) {
            return;
        }
        pane._explorerFsWatchPending = null;
        const applied = await refreshExplorerFilesystemSurfacesQuiet(index);
        if (!explorerWatchPaneCurrent(index, pane, sessionId)) {
            return;
        }
        if (applied) {
            pane._explorerFsWatchRevision = revision;
            pane._explorerFsWatchFailures = 0;
        } else {
            explorerFsWatchOnFailure(pane);
        }
    }

    function explorerGitWatchFlushPending(index) {
        const pane = terminals[index];
        if (!pane || !pane._explorerGitWatchPending) {
            return;
        }
        if (explorerGitWatchDeferralActive(index, pane)) {
            return;
        }
        const data = pane._explorerGitWatchPending;
        pane._explorerGitWatchPending = null;
        // A GridVibe Git action may have applied this exact state meanwhile.
        if (data.revision && data.revision === pane._explorerGitRevision) {
            return;
        }
        applyExplorerGitRepoQuiet(index, data);
    }

    function explorerFileWatchOnFailure(pane, status) {
        // A 404 means the session is gone or the open file no longer exists
        // (deleted or moved outside GridVibe); a 400 means it no longer
        // resolves. None recovers by retrying, so stop immediately — silently,
        // keeping the last good view.
        if (status === 404 || status === 400) {
            pane._explorerFileWatchFailures = EXPLORER_GIT_WATCH_MAX_FAILURES;
        } else {
            pane._explorerFileWatchFailures = (pane._explorerFileWatchFailures || 0) + 1;
        }
        if (pane._explorerFileWatchFailures >= EXPLORER_GIT_WATCH_MAX_FAILURES) {
            // No banner and no muted line: unlike the Git sidebar, the viewer
            // has no "live" affordance to contradict. Refresh, reopening the
            // file, or switching tabs re-arms it via a fresh baseline.
            pane._explorerFileWatchSuspended = true;
            pane._explorerFileWatchPending = null;
        }
    }

    /* Unlike the Git sidebar the *fetch* is the expensive half here, so a
       deferred open-file change holds only its token and re-reads once the
       gate clears — a stale re-read is never applied. */
    async function explorerFileWatchFlushPending(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const pending = pane?._explorerFileWatchPending;
        if (!pane || !sessionId || !pending) {
            return;
        }
        if (
            pane._explorerMode !== 'file'
            || pane._explorerFilePath !== pending.path
            || pane._explorerEdit
            || pending.revision === pane._explorerFileStateRevision
        ) {
            pane._explorerFileWatchPending = null;
            return;
        }
        if (explorerFileWatchDeferralActive(index)) {
            return;
        }
        pane._explorerFileWatchPending = null;
        const refreshed = await refreshExplorerOpenFileQuiet(index);
        if (!refreshed && explorerWatchPaneCurrent(index, pane, sessionId)) {
            explorerFileWatchOnFailure(pane, 0);
        }
    }

    async function explorerFileWatchCheckOne(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const path = pane?._explorerFilePath || '';
        if (!pane || !sessionId || !path) {
            return;
        }
        pane._explorerFileWatchInFlight = true;
        const started = performance.now();
        let delay = explorerFileWatchNextDelay(pane);
        try {
            const known = encodeURIComponent(pane._explorerFileStateRevision || '');
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/file/state`
                + `?path=${encodeURIComponent(path)}&known=${known}`,
                { cache: 'no-store' }
            );
            if (!explorerWatchPaneCurrent(index, pane, sessionId)) {
                return;
            }
            if (!response.ok) {
                explorerFileWatchOnFailure(pane, response.status);
                delay = explorerFileWatchBackoff(pane);
                return;
            }
            const data = await response.json();
            // The viewer may have moved to another file during the flight; that
            // response says nothing about the file now on screen.
            if (!explorerWatchPaneCurrent(index, pane, sessionId) || pane._explorerFilePath !== path) {
                return;
            }
            pane._explorerFileWatchFailures = 0;
            if (!data || data.changed === false || data.revision === pane._explorerFileStateRevision) {
                pane._explorerFileWatchChanges = 0;
                pane._explorerFileWatchPending = null;
            } else {
                pane._explorerFileWatchChanges = (pane._explorerFileWatchChanges || 0) + 1;
                pane._explorerFileWatchPending = { path, revision: data.revision };
                await explorerFileWatchFlushPending(index);
            }
            if (explorerWatchPaneCurrent(index, pane, sessionId)) {
                delay = pane._explorerFileWatchFailures > 0
                    ? explorerFileWatchBackoff(pane)
                    : explorerFileWatchNextDelay(pane);
            }
        } catch (error) {
            if (explorerWatchPaneCurrent(index, pane, sessionId)) {
                explorerFileWatchOnFailure(pane, 0);
                delay = explorerFileWatchBackoff(pane);
            }
        } finally {
            pane._explorerFileWatchInFlight = false;
            if (explorerWatchPaneCurrent(index, pane, sessionId)) {
                pane._explorerFileWatchLastMs = performance.now() - started;
                pane._explorerFileWatchNextAt = Date.now() + delay;
            }
        }
    }

    function explorerGitWatchFlushAllPending() {
        for (let index = 0; index < terminals.length; index += 1) {
            explorerGitWatchFlushPending(index);
            explorerFileWatchFlushPending(index);
            explorerFsWatchFlushPending(index);
        }
    }

    async function explorerGitWatchApplyRefresh(index, pane, sessionId) {
        const data = await refreshExplorerGitRepoQuiet(index);
        if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
            return;
        }
        if (!data) {
            // A failed quiet refetch keeps the last good panel; count it as a
            // poll failure so a persistently broken link still suspends.
            explorerGitWatchOnFailure(pane, 0);
            return;
        }
        if (data.revision && data.revision === pane._explorerGitRevision) {
            return;
        }
        // A newer pending payload simply replaces the older one — only the
        // newest state is ever applied.
        pane._explorerGitWatchPending = data;
        explorerGitWatchFlushPending(index);
    }

    async function explorerGitWatchCheckOne(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        pane._explorerGitWatchInFlight = true;
        const started = performance.now();
        let delay = explorerGitWatchNextDelay(pane);
        const sidebarConsumer = explorerGitWatchSidebarConsumer(pane);
        const fsConsumer = explorerFsWatchConsumer(pane);
        try {
            /* `known` is informational only now that one poll serves two
               baselines — the response's `revision` is compared against each
               consumer's own. It is still sent so the server contract and the
               log filter stay exactly as specified. */
            const known = encodeURIComponent(
                (sidebarConsumer ? pane._explorerGitRevision : pane._explorerFsWatchRevision) || ''
            );
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/git/state?known=${known}`,
                { cache: 'no-store' }
            );
            // Stale results are discarded: the pane/session identity must
            // survive every await or the response belongs to another pane.
            if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
                return;
            }
            if (!response.ok) {
                explorerGitWatchOnFailure(pane, response.status);
                delay = explorerGitWatchBackoff(pane);
                return;
            }
            const data = await response.json();
            if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
                return;
            }
            pane._explorerGitWatchFailures = 0;
            const revision = typeof data?.revision === 'string' ? data.revision : '';
            const sidebarStale = Boolean(
                revision && sidebarConsumer && revision !== pane._explorerGitRevision
            );
            let fsStale = Boolean(
                revision && fsConsumer && revision !== pane._explorerFsWatchRevision
            );
            if (fsStale && !pane._explorerFsWatchRevision) {
                // Silent bootstrap: the surfaces were loaded from the same
                // filesystem this revision describes, so there is no drift to
                // repaint — only a baseline to record.
                pane._explorerFsWatchRevision = revision;
                fsStale = false;
            }
            if (!sidebarStale && !fsStale) {
                // Unchanged (or a GridVibe action already applied it): zero DOM
                // writes, and any deferred payload is now stale.
                pane._explorerGitWatchChanges = 0;
                pane._explorerGitWatchPending = null;
                pane._explorerFsWatchPending = null;
            } else {
                pane._explorerGitWatchChanges = (pane._explorerGitWatchChanges || 0) + 1;
                if (sidebarStale) {
                    await explorerGitWatchApplyRefresh(index, pane, sessionId);
                }
                if (fsStale && explorerWatchPaneCurrent(index, pane, sessionId)) {
                    pane._explorerFsWatchPending = revision;
                    await explorerFsWatchFlushPending(index);
                }
            }
            if (terminals[index] === pane && sessionIds[index] === sessionId) {
                delay = pane._explorerGitWatchFailures > 0
                    ? explorerGitWatchBackoff(pane)
                    : explorerGitWatchNextDelay(pane);
            }
        } catch (error) {
            if (terminals[index] === pane && sessionIds[index] === sessionId) {
                explorerGitWatchOnFailure(pane, 0);
                delay = explorerGitWatchBackoff(pane);
            }
        } finally {
            pane._explorerGitWatchInFlight = false;
            if (terminals[index] === pane && sessionIds[index] === sessionId) {
                // Durations are monotonic (performance.now); only the per-pane
                // due-time comparison uses the wall clock (plan E14).
                pane._explorerGitWatchLastMs = performance.now() - started;
                pane._explorerGitWatchNextAt = Date.now() + delay;
            }
        }
    }

    /* One recursive setTimeout for the whole page; the next pass is scheduled
       only after the current one settles, and panes are checked sequentially
       — a burst of simultaneous `git status` calls / SSH channels is a worse
       failure mode than a few hundred ms of extra background latency. */
    async function explorerGitWatchTick() {
        if (explorerGitWatchRunning) {
            return;
        }
        explorerGitWatchRunning = true;
        try {
            if (document.visibilityState !== 'visible') {
                scheduleExplorerGitWatch(EXPLORER_GIT_WATCH_BASE_MS);
                return;
            }
            let nextDelay = EXPLORER_GIT_WATCH_MAX_MS;
            const noteDue = dueAt => {
                if (dueAt) {
                    nextDelay = Math.min(nextDelay, Math.max(dueAt - Date.now(), 0));
                }
            };
            for (let index = 0; index < terminals.length; index += 1) {
                explorerGitWatchFlushPending(index);
                await explorerFileWatchFlushPending(index);
                await explorerFsWatchFlushPending(index);
                const pane = terminals[index];
                if (!pane) {
                    continue;
                }
                if (explorerGitWatchEligible(index)) {
                    const dueAt = pane._explorerGitWatchNextAt || 0;
                    if (Date.now() < dueAt) {
                        noteDue(dueAt);
                    } else {
                        await explorerGitWatchCheckOne(index);
                        if (terminals[index] === pane) {
                            noteDue(pane._explorerGitWatchNextAt);
                        }
                    }
                }
                if (explorerFileWatchEligible(index)) {
                    const dueAt = pane._explorerFileWatchNextAt || 0;
                    if (Date.now() < dueAt) {
                        noteDue(dueAt);
                    } else {
                        await explorerFileWatchCheckOne(index);
                        if (terminals[index] === pane) {
                            noteDue(pane._explorerFileWatchNextAt);
                        }
                    }
                }
            }
            scheduleExplorerGitWatch(
                Math.min(
                    Math.max(nextDelay, EXPLORER_GIT_WATCH_BASE_MS),
                    EXPLORER_GIT_WATCH_MAX_MS
                )
            );
        } finally {
            explorerGitWatchRunning = false;
        }
    }

    /* Wake-ups (plan §5.5): missed intervals are never accumulated — a page
       hidden for an hour performs one check on return, not a backlog. */
    function explorerGitWatchWake() {
        terminals.forEach(pane => {
            if (pane) {
                pane._explorerGitWatchNextAt = 0;
                pane._explorerFileWatchNextAt = 0;
            }
        });
        explorerGitWatchFlushAllPending();
        if (explorerGitWatchTimer) {
            clearTimeout(explorerGitWatchTimer);
            explorerGitWatchTimer = null;
        }
        explorerGitWatchTick();
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            explorerGitWatchWake();
        }
    });
    window.addEventListener('focus', explorerGitWatchWake);
    window.addEventListener('pageshow', explorerGitWatchWake);

    /* Deferred applies flush when the gate that held them clears: pointer
       release (splitter drag, selection, click-in-progress, modal clicks),
       focus leaving the panel, IME composition end. */
    document.addEventListener('pointerdown', () => {
        explorerGitWatchPointerDown = true;
    }, true);
    document.addEventListener('pointerup', () => {
        explorerGitWatchPointerDown = false;
        explorerGitWatchFlushAllPending();
    }, true);
    document.addEventListener('blur', () => {
        explorerGitWatchFlushAllPending();
    }, true);
    document.addEventListener('compositionstart', event => {
        const pane = explorerGitWatchPaneForTextarea(event.target);
        if (pane) {
            pane._explorerGitComposing = true;
        }
    }, true);
    document.addEventListener('compositionend', event => {
        const pane = explorerGitWatchPaneForTextarea(event.target);
        if (pane) {
            pane._explorerGitComposing = false;
            explorerGitWatchFlushAllPending();
        }
    }, true);

    function explorerGitWatchPaneForTextarea(target) {
        const id = typeof target?.id === 'string' ? target.id : '';
        const match = id.match(/^explorer-git-commit-message-(\d+)$/);
        if (!match) {
            return null;
        }
        return terminals[Number(match[1])] || null;
    }

    scheduleExplorerGitWatch(EXPLORER_GIT_WATCH_BASE_MS);
