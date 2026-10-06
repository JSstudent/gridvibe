    /* ─────────────────────────────────────────────
       Explorer change listener. A file changed outside GridVibe refreshes in
       the *viewer* only — the editor buffer stays untouchable.

       One page-level scheduler runs three independent per-pane checks while
       the page is visible, feeding the same surfaces:

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
               while the independent directory poll below discovers membership
               changes in ignored folders and non-Git roots.
       2. Open file — while the viewer is showing a file, poll the cheaper
          GET /api/explorer/<id>/file/state (one `stat`, no read). A changed
          token re-reads the file and updates the viewer in place, so a file
          edited outside GridVibe stops going stale in the tab the user is
          actually looking at.
       3. Directory state — poll GET /api/explorer/<id>/directory/state (one
          bounded readdir, no Git, no content) for the browsed directory and
          every loaded, expanded Files-tree directory. This is the
          *independent*, Git-free change signal (ISSUE-2026-061): a child
          created, deleted or renamed outside GridVibe — inside an ignored
          folder or a root with no repository — advances its fingerprint where
          `git status` reports nothing. Each /entries load records the
          fingerprint as the baseline, so the same-path poll detects a change
          that lands between a surface load and the first poll. Git pin/Follow
          scope never chooses which directories this checks; neither
          `_explorerGitContext.available` nor any Git state gates it.

       Every apply is deferred while the user is interacting (commit message
       focused, IME composition, context menu or modal open, pointer down,
       reading a scrolled sidebar/tree, an active text selection).

       Invariants: read-only; the editor buffer is never touched, because a
       file viewer with an open editor is ineligible and the editor keeps its own
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
    const EXPLORER_GIT_WATCH_EDIT_SETTLE_MS = 800;    // bounded typing settle window
    const EXPLORER_GIT_WATCH_SWAP_SETTLE_MS = 200;    // tab-swap wake debounce
    // Directory state check: how many directories one pass polls. Mirrors
    // EXPLORER_FS_WATCH_MAX_TREE_NODES in explorer-tree.js so the tree-quiet
    // refresh and this poll agree on a bounded per-pass plan.
    const EXPLORER_DIR_WATCH_MAX_TREE_NODES = 16;
    let explorerGitWatchTimer = null;
    let explorerGitWatchSwapTimer = null;
    let explorerGitWatchRunning = false;
    let explorerGitWatchRerun = false;
    let explorerGitWatchWakeSerial = 0;               // advanced by every wake

    /* The due time a finished check installs. A check that was in flight when
       a wake landed answered from a snapshot older than the wake, and the
       wake's zeroed due time would otherwise be overwritten by its normal
       interval, so the rerun the wake asked for would skip this very pane.
       It stays due instead. */
    function explorerWatchDueAt(wakeSerial, delay) {
        return wakeSerial === explorerGitWatchWakeSerial ? Date.now() + delay : 0;
    }
    let explorerGitWatchPointerDown = false;
    let explorerGitWatchEditUntil = 0;                // genuine-edit deadline (Date.now ms)
    let explorerGitWatchInteractionResumed = true;

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
       in Git work. Only the state request is measured — the refresh a detected
       change triggers is the user's update, not the poll's cost, and counting
       it pushed the next poll out exactly when changes were landing. The
       churn damper doubles the interval per consecutive
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

    /* Overview consumer (source-view change marks): the gutter marks for the
       open file are HEAD-relative, so a commit made in a terminal invalidates
       them without ever touching the open-file watcher. Same shape as the
       filesystem consumer — bootstrapped silently by the tick, because the
       marks were loaded from the same revision the baseline then records.
       Commit-diff tabs and the in-place editor opt out exactly as
       loadExplorerChangeMarks() does. */
    function explorerOverviewWatchConsumer(pane) {
        if (!pane._explorerGitContext?.available) {
            return false;
        }
        if (pane._explorerEdit || pane._explorerDiffCommit) {
            return false;
        }
        return pane._explorerMode === 'file' && Boolean(pane._explorerFilePath);
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
        if (!explorerGitWatchSidebarConsumer(pane) && !explorerFsWatchConsumer(pane) && !explorerOverviewWatchConsumer(pane)) {
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

    /* Shared half of the deferral gates: a menu, a card or a modal is anchored
       to (or decides about) a row that must not move, and a pointer that is
       down is a drag, a selection, or a click in progress. */
    function explorerWatchInteractionActive() {
        if (document.visibilityState !== 'visible') {
            return true;
        }
        if (document.getElementById('explorer-ctx-menu')) {
            return true;
        }
        /* The commit card is the same kind of thing: pinned to one row, and
           closed by the re-render that would move it. It is named here rather
           than caught by the panel-focus gate below because it does not live
           in the panel -- it floats on document.body, so a focused copy
           control inside it is not `panel.contains(activeElement)`. */
        if (document.getElementById('explorer-git-commit-card')) {
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

    /* A genuine, bounded edit in the panel: an editable control the user is
       typing into, composing in, or holding an active selection in. Idle focus
       — a button they tabbed to, or an input they stopped typing in — is NOT
       editing and must not hold a pending update. The settle deadline is armed
       only by actual input/keyboard/selection events, so it is bounded and
       cannot be re-armed simply because focus returns to a control that stays
       inside the panel. */
    function explorerGitWatchTextEditingActive(panel) {
        const active = document.activeElement;
        if (!active || !panel.contains(active)) {
            return false;
        }
        if (!active.matches || !active.matches('input, textarea, [contenteditable]')) {
            return false;
        }
        if (Date.now() < explorerGitWatchEditUntil) {
            return true;
        }
        // A selection the user is still making (the page has focus) is an edit;
        // a remembered selection is not, and must not re-arm the gate when
        // focus returns to the retained input.
        if (explorerGitWatchInteractionResumed && document.hasFocus?.()) {
            return typeof active.selectionStart === 'number'
                && active.selectionStart !== active.selectionEnd;
        }
        return false;
    }

    /* Deferral gates (plan §6.2) — postpone the apply, never drop it. A panel
       re-render replaces innerHTML wholesale, so it must not happen while the
       user is typing, composing, choosing from a menu/modal, dragging, or
       reading a scrolled panel. Retained DOM focus alone is not interaction:
       a focused persistent button (or a text control left idle between
       keystrokes) must not hold a pending update while the page is otherwise
       visible but not interacted with. */
    function explorerGitWatchDeferralActive(index, pane) {
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!panel) {
            return false;
        }
        if (pane._explorerGitComposing) {
            return true;
        }
        if (explorerGitWatchTextEditingActive(panel)) {
            return true;
        }
        if (explorerWatchInteractionActive()) {
            return true;
        }
        const selection = window.getSelection?.();
        if (explorerGitWatchInteractionResumed && document.hasFocus?.()
            && selection && !selection.isCollapsed
            && (panel.contains(selection.anchorNode) || panel.contains(selection.focusNode))) {
            return true;
        }
        return explorerGitWatchInteractionResumed && document.hasFocus?.()
            && panel.matches(':hover') && panel.scrollTop > 0;
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
        if (document.visibilityState !== 'visible') {
            return;
        }
        const pane = terminals[index];
        if (!pane || !pane._explorerGitWatchPending) {
            return;
        }
        const { data, scopePath, scopeKind } = pane._explorerGitWatchPending;
        /* A deferral outlives a navigation: the pending payload belongs to the
           scope it was fetched under, never whatever the pane is asking about by
           the time a deferred flush runs. Under another scope it is dropped, not
           applied — labelling old data with the new scope would make the pane
           look loaded for a scope it has never asked the server about. */
        if (
            scopePath !== explorerGitRequestedScope(pane)
            || scopeKind !== explorerGitRequestedScopeKind(pane)
        ) {
            pane._explorerGitWatchPending = null;
            return;
        }
        if (explorerGitWatchDeferralActive(index, pane)) {
            return;
        }
        pane._explorerGitWatchPending = null;
        // A GridVibe Git action may have applied this exact state meanwhile.
        if (data.revision && data.revision === pane._explorerGitRevision) {
            return;
        }
        /* The payload's load identity is the scope it was *fetched* under, not
           whatever the pane's scope is by the time a deferred flush runs — a
           deferral outlives a scope change, and labelling old data with the
           new scope would make the pane look loaded for a scope it has never
           asked the server about. */
        applyExplorerGitRepoQuiet(index, data, scopePath, scopeKind);
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
        let measured = false;
        const wakeSerial = explorerGitWatchWakeSerial;
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
            pane._explorerFileWatchLastMs = performance.now() - started;
            measured = true;
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
                if (!measured) {
                    pane._explorerFileWatchLastMs = performance.now() - started;
                }
                pane._explorerFileWatchNextAt = explorerWatchDueAt(wakeSerial, delay);
            }
        }
    }

    /* Independent directory state watch (ISSUE-2026-061) ──────────────────
       The third, Git-free check the scheduler runs. Where the /git/state poll
       only sees what Git reports, this one sees the directory as the filesystem
       has it — ignored children, ordinary untracked children, and roots with no
       repository all count. Its targets are the browsed directory plus every
       loaded, expanded tree directory, never chosen by Git pin/Follow scope and
       never gated on `_explorerGitContext.available`. It has its own
       in-flight/due/backoff/failure state so a broken Git poll cannot take the
       filesystem freshness down with it, and vice versa. */

    /* The directories the poll checks, deduplicated and shallowest-first for
       the tree half. Only *loaded* tree directories are watched — the poll
       never bootstraps a node the tree has not fetched. */
    function explorerDirectoryWatchTargets(pane) {
        if (!pane) {
            return [];
        }
        const targets = [];
        const children = pane._explorerTreeChildren instanceof Map
            ? pane._explorerTreeChildren
            : null;
        const expanded = pane._explorerTreeExpanded instanceof Set
            ? pane._explorerTreeExpanded
            : new Set();
        if (pane._explorerMode === 'directory') {
            targets.push(String(pane._explorerPath || ''));
        }
        if (pane._explorerTreeSidebarOpen && children && children.has('')) {
            if (!targets.includes('')) {
                targets.push('');
            }
            const ordered = [...expanded].sort(
                (left, right) => left.split('/').length - right.split('/').length
            );
            for (const path of ordered) {
                if (children.has(path) && !targets.includes(path)) {
                    targets.push(path);
                }
            }
        }
        return targets;
    }

    /* A bounded, rotating window over the targets. Past the per-pass ceiling
       the window starts at a cursor that advances every pass, so a large
       expanded tree's deeper nodes are eventually checked instead of being
       permanently excluded beyond the first `EXPLORER_FS_WATCH_MAX_TREE_NODES`.
       The browsed directory is what the reader is looking at, so it leads
       every pass; only the tree directories behind it share the rotation. */
    function explorerDirectoryWatchWindow(pane) {
        const targets = explorerDirectoryWatchTargets(pane);
        const limit = EXPLORER_DIR_WATCH_MAX_TREE_NODES;
        if (targets.length <= limit) {
            return { paths: targets, advance: 0, total: targets.length };
        }
        const paths = pane._explorerMode === 'directory' ? targets.slice(0, 1) : [];
        const rotating = targets.slice(paths.length);
        const slots = limit - paths.length;
        const cursor = pane._explorerDirWatchCursor || 0;
        for (let i = 0; i < slots; i += 1) {
            paths.push(rotating[(cursor + i) % rotating.length]);
        }
        return { paths, advance: slots, total: rotating.length };
    }

    function explorerDirectoryWatchNextDelay(pane) {
        return explorerWatchNextDelay(
            pane, pane._explorerDirWatchLastMs, pane._explorerDirWatchChanges
        );
    }

    function explorerDirectoryWatchBackoff(pane) {
        return explorerWatchBackoff(
            pane._explorerDirWatchFailures, explorerDirectoryWatchNextDelay(pane)
        );
    }

    function explorerDirectoryWatchEligible(index) {
        const pane = terminals[index];
        if (!pane || !isExplorerPaneInstance(pane) || !sessionIds[index]) {
            return false;
        }
        if (!explorerDirectoryWatchTargets(pane).length) {
            return false;
        }
        // A GridVibe filesystem/Git action refreshes what it touched; an open
        // editor may still refresh its tree (the quiet re-list never touches the
        // file viewer or editor buffer).
        if (pane._explorerGitActionBusy || pane._explorerFsBusy) {
            return false;
        }
        if (pane._explorerDirWatchInFlight || pane._explorerDirWatchSuspended) {
            return false;
        }
        return true;
    }

    function explorerDirectoryWatchOnFailure(pane, status) {
        if (status === 'session_not_found') {
            // The session is gone; suspend immediately and silently.
            pane._explorerDirWatchFailures = EXPLORER_GIT_WATCH_MAX_FAILURES;
        } else {
            pane._explorerDirWatchFailures = (pane._explorerDirWatchFailures || 0) + 1;
        }
        if (pane._explorerDirWatchFailures >= EXPLORER_GIT_WATCH_MAX_FAILURES) {
            pane._explorerDirWatchSuspended = true;
        }
    }

    function queueExplorerDirectoryChange(pane, path) {
        const pending = pane._explorerDirWatchPending || { paths: new Set(), versions: new Map() };
        pending.versions = pending.versions || new Map();
        pane._explorerDirWatchSerial = (pane._explorerDirWatchSerial || 0) + 1;
        pending.paths.add(path);
        pending.versions.set(path, pane._explorerDirWatchSerial);
        pane._explorerDirWatchPending = pending;
    }

    /* Deferred apply: hold the newest dirty-directory set through interaction
       and the shared FS/Git busy flags, then re-list the selected directories
       through the ordinary quiet path. It never advances a baseline on its own;
       the quiet re-list re-records each directory's fresh revision. */
    async function explorerDirectoryWatchFlushPending(index) {
        if (document.visibilityState !== 'visible') {
            return;
        }
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const pending = pane?._explorerDirWatchPending;
        if (!pane || !sessionId || !pending || !pending.paths || !pending.paths.size) {
            return;
        }
        if (pane._explorerDirWatchSuspended || (pane._explorerDirWatchFailures
            && Date.now() < (pane._explorerDirWatchNextAt || 0))) return;
        if (pane._explorerGitActionBusy || pane._explorerFsBusy || pane._explorerFsWatchRefreshing) {
            return;
        }
        if (explorerFsWatchDeferralActive(index)) {
            return;
        }
        const owner = captureExplorerDirectoryOwner(index);
        const targets = new Set(explorerDirectoryWatchTargets(pane));
        for (const path of pending.paths) {
            if (!targets.has(path)) pending.paths.delete(path);
        }
        const dirs = [...pending.paths].slice(0, EXPLORER_DIR_WATCH_MAX_TREE_NODES);
        if (!dirs.length) {
            pane._explorerDirWatchPending = null;
            return;
        }
        const versions = new Map(dirs.map(path => [path, pending.versions?.get(path)]));
        const applied = await refreshExplorerFilesystemSurfacesQuiet(index, { dirs });
        if (!explorerDirectoryOwnerCurrent(index, owner)) {
            return;
        }
        if (applied) {
            pane._explorerDirWatchFailures = 0;
            // A poll may have queued a newer version during the fetch.
            if (pane._explorerDirWatchPending === pending) {
                for (const path of dirs) {
                    if (pending.versions?.get(path) === versions.get(path)) {
                        pending.paths.delete(path);
                        pending.versions?.delete(path);
                    }
                }
                if (!pending.paths.size) pane._explorerDirWatchPending = null;
            }
        } else {
            // Interaction starting during the fetch is a hold, not a failure.
            if (document.visibilityState !== 'visible' || explorerFsWatchDeferralActive(index)) return;
            explorerDirectoryWatchOnFailure(pane, 0);
            pane._explorerDirWatchNextAt = Date.now() + explorerDirectoryWatchBackoff(pane);
        }
    }

    async function explorerDirectoryWatchCheckOne(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        pane._explorerDirWatchInFlight = true;
        const started = performance.now();
        let measured = false;
        const wakeSerial = explorerGitWatchWakeSerial;
        let delay = explorerDirectoryWatchNextDelay(pane);
        const owner = captureExplorerDirectoryOwner(index);
        const windowPlan = explorerDirectoryWatchWindow(pane);
        const dirty = new Set();
        const missing = new Set();
        try {
            for (const path of windowPlan.paths) {
                if (!explorerDirectoryOwnerCurrent(index, owner, { tree: true })
                    || !explorerDirectoryWatchTargets(pane).includes(path)) return;
                if ([...missing].some(parent => path.startsWith(parent + '/'))) continue;
                const baselines = explorerDirectoryRenderedRevisions(pane, path);
                const known = baselines[0] || '';
                const response = await fetch(
                    `/api/explorer/${encodeURIComponent(sessionId)}/directory/state`
                    + `?path=${encodeURIComponent(path)}&known=${encodeURIComponent(known)}`,
                    { cache: 'no-store' }
                );
                if (!explorerDirectoryOwnerCurrent(index, owner, { tree: true })
                    || !explorerDirectoryWatchTargets(pane).includes(path)) {
                    return;
                }
                if (!response.ok) {
                    const error = await response.json();
                    if (!explorerDirectoryOwnerCurrent(index, owner, { tree: true })) return;
                    if (response.status === 404 && error?.code === 'not_found' && path) {
                        // A vanished expanded folder dirties its parent. The
                        // parent's applied listing prunes the cached branch.
                        // Drop impossible re-lists first: the missing folder
                        // may itself be open in Preview or already pending.
                        missing.add(path);
                        const pending = pane._explorerDirWatchPending;
                        if (pending) {
                            for (const queued of pending.paths) {
                                if (queued === path || queued.startsWith(path + '/')) {
                                    pending.paths.delete(queued);
                                    pending.versions?.delete(queued);
                                }
                            }
                        }
                        const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
                        queueExplorerDirectoryChange(pane, parent);
                        pane._explorerDirWatchPending.paths = new Set([
                            parent, ...pane._explorerDirWatchPending.paths
                        ]);
                        dirty.add(parent);
                        continue;
                    }
                    explorerDirectoryWatchOnFailure(pane, error?.code || response.status);
                    delay = explorerDirectoryWatchBackoff(pane);
                    return;
                }
                const data = await response.json();
                if (!explorerDirectoryOwnerCurrent(index, owner, { tree: true })
                    || !explorerDirectoryWatchTargets(pane).includes(path)) {
                    return;
                }
                // The root may have been reset/replaced during the flight; the
                // response then says nothing about the directory now on screen.
                if ((data.root_revision || '') !== owner.root) {
                    return;
                }
                const revision = typeof data?.revision === 'string' ? data.revision : '';
                if (!revision) {
                    // Past the state ceiling (or unreadable): no cheap signal for
                    // this path. Record nothing and leave its surface alone.
                    continue;
                }
                if (baselines.some(baseline => revision !== baseline)) {
                    dirty.add(path);
                    queueExplorerDirectoryChange(pane, path);
                }
            }
            // The state requests are the pass's cost; the re-list below is not.
            pane._explorerDirWatchLastMs = performance.now() - started;
            measured = true;
            if (dirty.size) {
                pane._explorerDirWatchChanges = (pane._explorerDirWatchChanges || 0) + 1;
            } else {
                pane._explorerDirWatchChanges = 0;
            }
            await explorerDirectoryWatchFlushPending(index);
            if (!pane._explorerDirWatchPending) pane._explorerDirWatchFailures = 0;
            if (explorerWatchPaneCurrent(index, pane, sessionId)) {
                pane._explorerDirWatchCursor = (
                    (pane._explorerDirWatchCursor || 0) + windowPlan.advance
                ) % Math.max(1, windowPlan.total);
                delay = pane._explorerDirWatchFailures > 0
                    ? explorerDirectoryWatchBackoff(pane)
                    : explorerDirectoryWatchNextDelay(pane);
            }
        } catch (error) {
            if (explorerWatchPaneCurrent(index, pane, sessionId)) {
                explorerDirectoryWatchOnFailure(pane, 0);
                delay = explorerDirectoryWatchBackoff(pane);
            }
        } finally {
            pane._explorerDirWatchInFlight = false;
            if (explorerWatchPaneCurrent(index, pane, sessionId)) {
                if (!measured) {
                    pane._explorerDirWatchLastMs = performance.now() - started;
                }
                pane._explorerDirWatchNextAt = explorerWatchDueAt(wakeSerial, delay);
            }
        }
    }

    function explorerGitWatchFlushAllPending() {
        if (document.visibilityState !== 'visible') {
            return;
        }
        for (let index = 0; index < terminals.length; index += 1) {
            explorerGitWatchFlushPending(index);
            explorerFileWatchFlushPending(index);
            explorerFsWatchFlushPending(index);
            explorerDirectoryWatchFlushPending(index);
        }
    }

    async function explorerGitWatchApplyRefresh(index, pane, sessionId) {
        if (document.visibilityState !== 'visible') {
            return;
        }
        const scopePath = explorerGitRequestedScope(pane);
        const scopeKind = explorerGitRequestedScopeKind(pane);
        const data = await refreshExplorerGitRepoQuiet(index, { background: true });
        if (terminals[index] !== pane || sessionIds[index] !== sessionId) {
            return;
        }
        // The quiet refetch already re-checks its own scope, but a scope can
        // move again between that check and this guard; a payload fetched under
        // an abandoned scope must never be queued for apply.
        if (
            scopePath !== explorerGitRequestedScope(pane)
            || scopeKind !== explorerGitRequestedScopeKind(pane)
        ) {
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
        /* A newer pending payload simply replaces the older one — only the
           newest state is ever applied.

           The payload and the scope it was fetched under are one record, in
           one field: three parallel fields were cleared in three places and
           one of them was always missed, leaving a scope from a deferral the
           pane had already left behind. */
        pane._explorerGitWatchPending = { data, scopePath, scopeKind };
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
        let measured = false;
        const wakeSerial = explorerGitWatchWakeSerial;
        let delay = explorerGitWatchNextDelay(pane);
        const sidebarConsumer = explorerGitWatchSidebarConsumer(pane);
        const fsConsumer = explorerFsWatchConsumer(pane);
        const overviewConsumer = explorerOverviewWatchConsumer(pane);
        try {
            /* `known` is informational only now that one poll serves two
               baselines — the response's `revision` is compared against each
               consumer's own. It is still sent so the server contract and the
               log filter stay exactly as specified. */
            const known = (sidebarConsumer
                ? pane._explorerGitRevision
                : pane._explorerFsWatchRevision) || '';
            const scopePath = explorerGitScopePath(pane);
            const scopeKind = explorerGitScopeKind(pane);
            const response = await fetch(
                explorerGitRequestUrl(
                    sessionId,
                    'state',
                    scopePath,
                    { known },
                    scopeKind
                ),
                { cache: 'no-store' }
            );
            // Stale results are discarded: the pane/session identity must
            // survive every await or the response belongs to another pane.
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || explorerGitScopePath(pane) !== scopePath
                || explorerGitScopeKind(pane) !== scopeKind
            ) {
                return;
            }
            if (!response.ok) {
                explorerGitWatchOnFailure(pane, response.status);
                delay = explorerGitWatchBackoff(pane);
                return;
            }
            const data = await response.json();
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || explorerGitScopePath(pane) !== scopePath
                || explorerGitScopeKind(pane) !== scopeKind
            ) {
                return;
            }
            // Durations are monotonic (performance.now); only the per-pane
            // due-time comparison uses the wall clock (plan E14).
            pane._explorerGitWatchLastMs = performance.now() - started;
            measured = true;
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
            let overviewStale = Boolean(
                revision && overviewConsumer && revision !== pane._explorerOverviewWatchRevision
            );
            if (overviewStale && !pane._explorerOverviewWatchRevision) {
                // Same silent bootstrap: the marks were fetched against this
                // revision when the file opened, so nothing is stale yet.
                pane._explorerOverviewWatchRevision = revision;
                overviewStale = false;
            }
            if (!sidebarStale && !fsStale && !overviewStale) {
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
                if (overviewStale && explorerWatchPaneCurrent(index, pane, sessionId)) {
                    // HEAD moved (a commit from a terminal): reload the gutter
                    // marks. The refetch is force-refreshed inside, so a same-
                    // path cache entry cannot short-circuit it.
                    pane._explorerOverviewWatchRevision = revision;
                    await refreshExplorerOverview(index, 'git-revision');
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
                if (!measured) {
                    pane._explorerGitWatchLastMs = performance.now() - started;
                }
                pane._explorerGitWatchNextAt = explorerWatchDueAt(wakeSerial, delay);
            }
        }
    }

    /* One recursive setTimeout for the whole page; the next pass is scheduled
       only after the current one settles, and panes are checked sequentially
       — a burst of simultaneous `git status` calls / SSH channels is a worse
       failure mode than a few hundred ms of extra background latency.

       A wake that lands while a pass is running is not dropped: the pass was
       planned from the due times and the pane list it started with, so it
       re-runs as soon as it settles instead of scheduling that stale delay. */
    async function explorerGitWatchTick() {
        if (explorerGitWatchRunning) {
            explorerGitWatchRerun = true;
            return;
        }
        explorerGitWatchRunning = true;
        try {
            await explorerGitWatchPass();
        } finally {
            explorerGitWatchRunning = false;
        }
        if (explorerGitWatchRerun) {
            explorerGitWatchRerun = false;
            if (explorerGitWatchTimer) {
                clearTimeout(explorerGitWatchTimer);
                explorerGitWatchTimer = null;
            }
            await explorerGitWatchTick();
        }
    }

    async function explorerGitWatchPass() {
        if (document.visibilityState !== 'visible') {
            scheduleExplorerGitWatch(EXPLORER_GIT_WATCH_BASE_MS);
            return;
        }
        /* A tab swap replaces `terminals` with another view's panes. Indexes
           from here on would name those panes against this pass's plan, so the
           pass stops at the next step and re-runs on the list now shown; the
           check still awaiting a pane that left is dropped by its own
           identity guards. */
        const panes = terminals;
        const abandoned = () => {
            if (terminals === panes) {
                return false;
            }
            explorerGitWatchRerun = true;
            return true;
        };
        let nextDelay = EXPLORER_GIT_WATCH_MAX_MS;
        const noteDue = dueAt => {
            if (dueAt) {
                nextDelay = Math.min(nextDelay, Math.max(dueAt - Date.now(), 0));
            }
        };
        for (let index = 0; index < panes.length; index += 1) {
            explorerGitWatchFlushPending(index);
            await explorerFileWatchFlushPending(index);
            await explorerFsWatchFlushPending(index);
            await explorerDirectoryWatchFlushPending(index);
            if (abandoned()) {
                return;
            }
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
                    if (abandoned()) {
                        return;
                    }
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
                    if (abandoned()) {
                        return;
                    }
                    if (terminals[index] === pane) {
                        noteDue(pane._explorerFileWatchNextAt);
                    }
                }
            }
            if (explorerDirectoryWatchEligible(index)) {
                const dueAt = pane._explorerDirWatchNextAt || 0;
                if (Date.now() < dueAt) {
                    noteDue(dueAt);
                } else {
                    await explorerDirectoryWatchCheckOne(index);
                    if (abandoned()) {
                        return;
                    }
                    if (terminals[index] === pane) {
                        noteDue(pane._explorerDirWatchNextAt);
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
    }

    /* Wake-ups (plan §5.5): missed intervals are never accumulated — a page
       hidden for an hour performs one check on return, not a backlog. */
    function explorerGitWatchWake() {
        explorerGitWatchWakeSerial += 1;
        terminals.forEach(pane => {
            if (pane) {
                pane._explorerGitWatchNextAt = 0;
                pane._explorerFileWatchNextAt = 0;
                pane._explorerDirWatchNextAt = 0;
            }
        });
        explorerGitWatchFlushAllPending();
        if (explorerGitWatchTimer) {
            clearTimeout(explorerGitWatchTimer);
            explorerGitWatchTimer = null;
        }
        explorerGitWatchTick();
    }

    /* A tab swap shows panes the page timer was not planned around: it was
       set from the tab just left, which — with no explorer pane of its own —
       leaves the next pass a full MAX away. terminals.js calls this once a
       cached view is restored or a grid rebuilt. The settle window coalesces
       fast tab cycling into one check per shown pane. */
    function explorerGitWatchWakeVisible() {
        if (explorerGitWatchSwapTimer) {
            clearTimeout(explorerGitWatchSwapTimer);
        }
        explorerGitWatchSwapTimer = setTimeout(() => {
            explorerGitWatchSwapTimer = null;
            if (document.visibilityState === 'visible') {
                explorerGitWatchWake();
            }
        }, EXPLORER_GIT_WATCH_SWAP_SETTLE_MS);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            explorerGitWatchWake();
        } else {
            /* A hidden page drops gesture delivery and suspends polling, so a
               pointer-down flag or an edit deadline left set would hold a
               pending update through the whole hidden interval and still be set
               on return. Reconcile both as the page goes away; the wake on
               return then only has to flush, never guess which half-finished
               gesture was real. */
            explorerGitWatchDeactivate();
        }
    });
    window.addEventListener('focus', explorerGitWatchWake);
    window.addEventListener('pageshow', explorerGitWatchWake);
    /* Window deactivation is the event a retained-DOM-focus switch away fires
       (an element keeps its focus, so no element blur). Reconcile there too:
       the page is visible but unfocused, and a pending update must apply rather
       than wait out a gesture the page never saw released. */
    window.addEventListener('blur', () => {
        explorerGitWatchDeactivate();
        explorerGitWatchFlushAllPending();
    });

    function explorerGitWatchDeactivate() {
        explorerGitWatchPointerDown = false;
        explorerGitWatchEditUntil = 0;
        // Focus return alone does not make remembered selections or hover a
        // new interaction. Actual input resumes their protection.
        explorerGitWatchInteractionResumed = false;
        terminals.forEach(pane => {
            if (pane) {
                pane._explorerGitComposing = false;
            }
        });
    }

    /* Genuine editing arms a bounded settle window: an actual value change, a
       keystroke (cursor/selection movement that changes no value), or a
       selection change inside an editable control. Re-rendering the panel
       takes the caret and the selection, so the apply stays deferred only while
       the user is demonstrably still editing — never merely because focus sits
       in a control. */
    function explorerGitWatchNoteEdit(event) {
        const target = event?.target;
        if (target?.matches?.('input, textarea, [contenteditable]')) {
            explorerGitWatchInteractionResumed = true;
            explorerGitWatchEditUntil = Date.now() + EXPLORER_GIT_WATCH_EDIT_SETTLE_MS;
        }
    }

    /* Deferred applies flush when the gate that held them clears: pointer
       release (splitter drag, selection, click-in-progress, modal clicks),
       the edit settle elapsing, IME composition end, or window deactivation. */
    document.addEventListener('pointerdown', () => {
        explorerGitWatchInteractionResumed = true;
        explorerGitWatchPointerDown = true;
    }, true);
    document.addEventListener('pointerup', () => {
        explorerGitWatchPointerDown = false;
        explorerGitWatchFlushAllPending();
    }, true);
    document.addEventListener('pointercancel', () => {
        explorerGitWatchPointerDown = false;
        explorerGitWatchFlushAllPending();
    }, true);
    document.addEventListener('blur', () => {
        // An element can blur during pointerdown's default focus change. Keep
        // that gesture intact until pointerup/cancel or actual window blur.
        explorerGitWatchEditUntil = 0;
        explorerGitWatchFlushAllPending();
    }, true);
    document.addEventListener('input', explorerGitWatchNoteEdit, true);
    document.addEventListener('keydown', explorerGitWatchNoteEdit, true);
    document.addEventListener('select', explorerGitWatchNoteEdit, true);
    for (const type of ['pointermove', 'wheel']) {
        document.addEventListener(type, () => {
            explorerGitWatchInteractionResumed = true;
        }, { capture: true, passive: true });
    }
    document.addEventListener('compositionstart', event => {
        explorerGitWatchInteractionResumed = true;
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
        if (!target?.matches?.('input, textarea, [contenteditable]')) {
            return null;
        }
        for (let index = 0; index < terminals.length; index += 1) {
            const panel = document.getElementById(`explorer-git-panel-${index}`);
            if (panel?.contains(target)) {
                return terminals[index] || null;
            }
        }
        return null;
    }

    scheduleExplorerGitWatch(EXPLORER_GIT_WATCH_BASE_MS);
