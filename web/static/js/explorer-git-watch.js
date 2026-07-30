    /* ─────────────────────────────────────────────
       Explorer Git change listener
       (docs/r&d/explorer_git_change_listener_plan_2026-07-30.md).

       While an explorer Git sidebar is open and the page is visible, one
       page-level scheduler polls the cheap GET /api/explorer/<id>/git/state
       semantic-revision endpoint. An unchanged revision costs one
       `git status` on the server and causes zero DOM writes, zero refetches,
       zero log lines. A changed revision quietly refetches the full /git/repo
       summary and swaps the sidebar in place — deferred while the user is
       interacting with it (commit message focused, IME composition, context
       menu or modal open, pointer down, reading a scrolled panel).

       Invariants (plan §3): read-only, never touches the file viewer/editor
       (no openExplorerFile / tree or pane reload calls here by design), no
       work when invisible, one in-flight request per pane, recursive
       setTimeout only, stale responses dropped by pane/session identity.
       Loaded before terminals.js; `terminals`, `sessionIds`,
       `isExplorerPaneInstance`, `refreshExplorerGitRepoQuiet` and
       `applyExplorerGitRepoQuiet` all resolve at call time.
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

    function explorerGitWatchChurnMultiplier(pane) {
        const changes = pane?._explorerGitWatchChanges || 0;
        return changes >= EXPLORER_GIT_WATCH_CHURN_LIMIT
            ? 2 ** (changes - EXPLORER_GIT_WATCH_CHURN_LIMIT + 1)
            : 1;
    }

    /* Adaptive floor (plan §5.4): a poll that takes 900 ms yields a 5.4 s
       floor; the listener can never consume more than ~1/6 of wall-clock time
       in Git work. The churn damper doubles the interval per consecutive
       changed poll past the limit, capped at MAX — a build or agent rewriting
       files stops repainting the sidebar every base interval. */
    function explorerGitWatchNextDelay(pane) {
        const base = explorerGitWatchBaseMs(pane);
        const floor = Math.max(
            base,
            (pane._explorerGitWatchLastMs || 0) * EXPLORER_GIT_WATCH_DURATION_FACTOR
        );
        return Math.min(
            Math.max(floor * explorerGitWatchChurnMultiplier(pane), base),
            EXPLORER_GIT_WATCH_MAX_MS
        );
    }

    function explorerGitWatchBackoff(pane) {
        const failures = Math.min(
            pane._explorerGitWatchFailures || 0,
            EXPLORER_GIT_WATCH_BACKOFF_MS.length
        );
        return failures > 0
            ? EXPLORER_GIT_WATCH_BACKOFF_MS[failures - 1]
            : explorerGitWatchNextDelay(pane);
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

    /* Eligibility gates (plan §5.3) — no request is made unless all hold.
       Closing the sidebar, switching session-group tabs (the pane leaves
       `terminals`), or a non-Git root (no baseline revision) therefore stops
       all polling with no server-side unsubscribe. */
    function explorerGitWatchEligible(index) {
        const pane = terminals[index];
        if (!pane || !isExplorerPaneInstance(pane) || !sessionIds[index]) {
            return false;
        }
        if (!pane._explorerGitSidebarOpen || !pane._explorerGitRepoLoaded) {
            return false;
        }
        // The watcher never bootstraps a load; it only detects drift from a
        // baseline the user's own actions established.
        if (typeof pane._explorerGitRevision !== 'string' || !pane._explorerGitRevision) {
            return false;
        }
        if (pane._explorerGitRepoLoading || pane._explorerGitRepoRefreshing) {
            return false;
        }
        // A GridVibe Git action is authoritative, and a filesystem
        // mutation/save triggers its own sidebar refresh.
        if (pane._explorerGitActionBusy || pane._explorerFsBusy || pane._explorerEdit?.saving) {
            return false;
        }
        if (pane._explorerGitWatchInFlight || pane._explorerGitWatchSuspended) {
            return false;
        }
        return true;
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
        if (explorerGitWatchPointerDown) {
            return true;
        }
        return panel.matches(':hover') && panel.scrollTop > 0;
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

    function explorerGitWatchFlushAllPending() {
        for (let index = 0; index < terminals.length; index += 1) {
            explorerGitWatchFlushPending(index);
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
        try {
            const known = encodeURIComponent(pane._explorerGitRevision || '');
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
            if (!data || data.changed === false || data.revision === pane._explorerGitRevision) {
                // Unchanged (or a GridVibe Git action already applied it):
                // zero DOM writes, and any deferred payload is now stale.
                pane._explorerGitWatchChanges = 0;
                pane._explorerGitWatchPending = null;
            } else {
                pane._explorerGitWatchChanges = (pane._explorerGitWatchChanges || 0) + 1;
                await explorerGitWatchApplyRefresh(index, pane, sessionId);
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
            for (let index = 0; index < terminals.length; index += 1) {
                explorerGitWatchFlushPending(index);
                if (!explorerGitWatchEligible(index)) {
                    continue;
                }
                const pane = terminals[index];
                const dueAt = pane._explorerGitWatchNextAt || 0;
                if (Date.now() < dueAt) {
                    nextDelay = Math.min(nextDelay, dueAt - Date.now());
                    continue;
                }
                await explorerGitWatchCheckOne(index);
                if (terminals[index] === pane && pane._explorerGitWatchNextAt) {
                    nextDelay = Math.min(
                        nextDelay,
                        Math.max(pane._explorerGitWatchNextAt - Date.now(), 0)
                    );
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
