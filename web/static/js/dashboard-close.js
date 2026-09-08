/* GridVibeDashboardClose — ending a session or a workspace from the one window
   that lists every one of them.

   The dashboard already names three levels and opens all three; what it could
   not do was end any of them, so finding a session that had finished still
   meant travelling to the window that holds it. These are the same three verbs
   the rest of the app has, reached from here:

     · **Close session** — the session tab's ×, with its three-outcome prompt.
     · **Close workspace** — the launcher's per-row Close: ends every session in
       the workspace, and leaves whatever autosave or Save Workspace captured
       on offer in the restore chooser.
     · **Close workspace window** — the in-window Workspace ▸ Close Workspace
       Window: the window goes, the sessions keep running.

   Nothing here is a new verb, and nothing here asks a new question. Both
   dialogs are the existing ones — `close-session-modal.js` over the shared
   partial, and `confirmCloseLiveWorkspace()` over the generic confirm shell —
   because a second prompt for one irreversible act is how two surfaces come to
   warn about differently sized consequences. That matters more here than
   anywhere else: the reader pressing × on this window is looking at a list,
   not at the terminals they are about to end.

   Five decisions are the reason this is not the obvious code:

     · **The save runs before the delete, and a failed save cancels the
       close.** The whole point of *Save and close* is to not lose the group;
       a DELETE first would leave nothing to snapshot, and closing anyway after
       a failed save costs exactly what the button was pressed to preserve.
     · **The preset is composed by the server, not by this page.** The workspace
       window builds one out of its own live DOM, which this window does not
       have. `POST /api/session-groups/<id>/save` is that build asked for by
       name — it flushes the owning window first, so the preset carries what
       the reader sees rather than what the server last heard.
     · **Close workspace window is withheld in browser mode, not disabled.** A
       tab cannot close another tab it did not open, and `closeWorkspaceWindow`
       falls back to `window.close()` — which from here would close *the
       dashboard*. Same rule, and the same reason, as `minimize-all.js`: a dead
       button explaining itself in a tooltip is noise on a surface where
       everything else works.
     · **In flight is module state, not a class on a button.** The poll rebuilds
       these rows every couple of seconds, so a guard living on the pressed node
       is a guard a repaint can drop — and dropping it means a second press
       sends a second DELETE. The class is still put on the captured node, for
       the reader; the *decision* is held here.
     · **What a close reports is only what it could not do.** A closed row
       disappears on the next read, which says more than a message would. The
       exception is closing a window: nothing on this page changes, so that one
       says so.

   Two halves, the split `minimize-all.js` uses: `policy` is pure, and
   `create(runtime)` takes every DOM, bridge, dialog and network touch from an
   injected runtime, so the ordering rules above are executed by a Node test
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (!root || !root.document) return;

    const controller = api.create({
        getBridge: () => (root.pywebview && root.pywebview.api) || null,
        fetchJson: async (url, options) => {
            const response = await root.fetch(url, options);
            const data = await response.json().catch(() => ({}));
            return { ok: response.ok, status: response.status, data: data || {} };
        },
        confirmCloseSession: request => root.openCloseSessionConfirmModal(request),
        skipDecision: sessions => root.closeSessionPromptSkipDecision(sessions),
        connectedCount: sessions => root.closeSessionConnectedCount(sessions),
        /* Read from the prompt rather than spelled again here. A second copy
           of 'save-and-close' compares equal today and stops matching, in
           silence, the day the prompt's own constant changes — and the
           failure mode is a save that is simply skipped. Getters, so the
           order these two files load in cannot matter. */
        decisions: {
            get cancel() { return root.CLOSE_SESSION_CANCEL; },
            get saveAndClose() { return root.CLOSE_SESSION_SAVE_AND_CLOSE; }
        },
        confirmCloseWorkspace: workspace => root.confirmCloseLiveWorkspace(workspace),
        closeLiveWorkspace: workspaceId => root.closeLiveWorkspace(workspaceId),
        notifySavedSession: saved => root.notifySavedSessionUpdated(saved),
        notifyWorkspacesChanged: reason => root.notifyWorkspacesChanged(reason),
        notice: (message, tone) => root.setAgentDashboardNotice(message, 'action', tone),
        refresh: () => root.refreshAgentDashboard(),
        logError: (message, error) => console.error(message, error)
    });

    root.GridVibeDashboardClose = controller;
    root.dashboardCloseCanCloseWindow = () => controller.canCloseWindow();
}(typeof window !== 'undefined' ? window : null, function () {
    const SESSION_ACTION = 'close-session';
    const WORKSPACE_ACTION = 'close-workspace';
    const WORKSPACE_WINDOW_ACTION = 'close-workspace-window';
    const CLOSE_ACTIONS = [SESSION_ACTION, WORKSPACE_ACTION, WORKSPACE_WINDOW_ACTION];

    /* Native mode only: `close_workspace_window` is the launcher's own bridge
       method, and it is what closes *another* window by workspace id. */
    function canCloseWindow(bridge) {
        return Boolean(bridge && typeof bridge.close_workspace_window === 'function');
    }

    /* The two workspace verbs, in the order the session menu lists them: the
       harmless one first, the destructive one last, so the pointer travels
       further to reach the one that ends shells.

       Each row states what it costs in its own title, because the window verb
       and the workspace verb differ by exactly the thing a title can say and a
       label cannot — one of them keeps the sessions running. */
    function workspaceControls(workspace, { native = false } = {}) {
        const controls = [];
        if (native) {
            controls.push({
                action: WORKSPACE_WINDOW_ACTION,
                label: 'Close window',
                title: "Close this workspace's window — its sessions keep running",
                danger: false
            });
        }
        controls.push({
            action: WORKSPACE_ACTION,
            label: 'Close workspace',
            title: 'Close this workspace — ends every session in it; its saved'
                + ' snapshot stays on offer',
            danger: true
        });
        return controls;
    }

    function sessionCloseTitle(name) {
        return `Close ${name} — ends its terminals`;
    }

    function sessionListPath(workspaceId, groupId) {
        const params = new URLSearchParams({ workspace_id: String(workspaceId || '') });
        if (groupId) {
            params.set('group', String(groupId));
        }
        return `/api/sessions?${params.toString()}`;
    }

    function groupSavePath(groupId) {
        return `/api/session-groups/${encodeURIComponent(String(groupId || ''))}/save`;
    }

    /* One key per target, so two rows aimed at different things never block
       each other and one row pressed twice sends one request. */
    function targetKey(action, target) {
        return `${action}:${String(target?.groupId || target?.workspaceId || '')}`;
    }

    /* A failure the reader can act on. The server's own sentence when it sent
       one — it is the half that knows *why* — and the caller's fallback when
       the request never got far enough to produce one. */
    function failureMessage(fallback, data) {
        const stated = String(data?.error || '').trim();
        return stated || fallback;
    }

    const policy = {
        SESSION_ACTION,
        WORKSPACE_ACTION,
        WORKSPACE_WINDOW_ACTION,
        CLOSE_ACTIONS,
        canCloseWindow,
        workspaceControls,
        sessionCloseTitle,
        sessionListPath,
        groupSavePath,
        targetKey,
        failureMessage
    };

    function create(runtime) {
        const {
            getBridge,
            fetchJson,
            confirmCloseSession,
            skipDecision,
            connectedCount,
            decisions = {},
            confirmCloseWorkspace,
            closeLiveWorkspace,
            notifySavedSession = () => {},
            notifyWorkspacesChanged = () => {},
            notice = () => {},
            refresh = () => {},
            logError = () => {}
        } = runtime || {};

        const inFlight = new Set();

        function bridge() {
            try {
                return getBridge ? getBridge() : null;
            } catch (_error) {
                return null;
            }
        }

        function canCloseWindowHere() {
            return canCloseWindow(bridge());
        }

        /* The class is cosmetic and goes on the node that was pressed; the
           refusal to run twice is the Set above. A node the poll has already
           replaced is written to harmlessly and read by nobody. */
        function markBusy(element, busy) {
            element?.classList?.toggle?.('is-busy', Boolean(busy));
        }

        async function readGroupSessions(target) {
            try {
                const { ok, data } = await fetchJson(
                    sessionListPath(target.workspaceId, target.groupId),
                    { cache: 'no-store' }
                );
                return ok && Array.isArray(data.sessions) ? data.sessions : [];
            } catch (_error) {
                /* Could not find out. The safe reading is to ask, which an
                   empty list is exactly what produces. */
                return [];
            }
        }

        async function saveGroupPreset(target) {
            const { ok, data } = await fetchJson(groupSavePath(target.groupId), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!ok) {
                return {
                    ok: false,
                    error: failureMessage('This session could not be saved.', data)
                };
            }
            notifySavedSession(data);
            return { ok: true, data };
        }

        async function closeSession(target, element = null) {
            const key = targetKey(SESSION_ACTION, target);
            if (!target?.workspaceId || !target?.groupId || inFlight.has(key)) {
                return false;
            }
            const sessions = await readGroupSessions(target);
            const decision = skipDecision(sessions)
                || await confirmCloseSession({
                    group: { name: target.name, group_id: target.groupId },
                    connectedCount: connectedCount(sessions),
                    totalCount: sessions.length
                });
            if (!decision || decision === decisions.cancel) {
                return false;
            }

            inFlight.add(key);
            markBusy(element, true);
            try {
                /* Save first, and only close if it worked: a requested save
                   that failed must not cost the terminals it was meant to
                   preserve. */
                if (decision === decisions.saveAndClose) {
                    const saved = await saveGroupPreset(target);
                    if (!saved.ok) {
                        notice(saved.error, 'error');
                        return false;
                    }
                }
                const { ok, data } = await fetchJson(
                    sessionListPath(target.workspaceId, target.groupId),
                    { method: 'DELETE' }
                );
                if (!ok) {
                    notice(failureMessage('This session could not be closed.', data), 'error');
                    return false;
                }
            } catch (error) {
                logError('[GridVibe Dashboard] closing the session failed:', error);
                notice('This session could not be closed.', 'error');
                return false;
            } finally {
                inFlight.delete(key);
                markBusy(element, false);
            }
            /* The window that owns it hears this over its own socket; the
               launcher has none, so this window tells it. */
            notifyWorkspacesChanged('group_closed');
            notice('');
            await refresh();
            return true;
        }

        async function closeWorkspace(target, element = null) {
            const key = targetKey(WORKSPACE_ACTION, target);
            if (!target?.workspaceId || inFlight.has(key)) {
                return false;
            }
            const confirmed = await confirmCloseWorkspace({
                workspace_id: target.workspaceId,
                label: target.label,
                group_count: target.groupCount
            });
            if (!confirmed) {
                return false;
            }

            inFlight.add(key);
            markBusy(element, true);
            try {
                await closeLiveWorkspace(target.workspaceId);
            } catch (error) {
                logError('[GridVibe Dashboard] closing the workspace failed:', error);
                notice(
                    failureMessage('This workspace could not be closed.', { error: error?.message }),
                    'error'
                );
                return false;
            } finally {
                inFlight.delete(key);
                markBusy(element, false);
            }
            notice('');
            await refresh();
            return true;
        }

        /* Nothing live changes, so there is nothing to confirm and nothing on
           this page to re-read — which is also why this is the one verb here
           that reports its success: without a word, a press that worked and a
           press that did nothing look identical from the dashboard. */
        async function closeWorkspaceWindow(target, element = null) {
            const key = targetKey(WORKSPACE_WINDOW_ACTION, target);
            const api = bridge();
            if (!target?.workspaceId || inFlight.has(key) || !canCloseWindow(api)) {
                return false;
            }
            inFlight.add(key);
            markBusy(element, true);
            try {
                const result = await api.close_workspace_window(target.workspaceId);
                if (!result || result.ok === false) {
                    notice(
                        failureMessage('That workspace has no window open.', result),
                        'error'
                    );
                    return false;
                }
            } catch (error) {
                logError('[GridVibe Dashboard] closing the workspace window failed:', error);
                notice('That workspace window could not be closed.', 'error');
                return false;
            } finally {
                inFlight.delete(key);
                markBusy(element, false);
            }
            notice(`Closed the window for ${target.label || 'that workspace'}. Its sessions keep running.`, 'info');
            return true;
        }

        function handles(action) {
            return CLOSE_ACTIONS.includes(String(action || ''));
        }

        function run(action, target, element = null) {
            if (action === SESSION_ACTION) return closeSession(target, element);
            if (action === WORKSPACE_ACTION) return closeWorkspace(target, element);
            if (action === WORKSPACE_WINDOW_ACTION) return closeWorkspaceWindow(target, element);
            return Promise.resolve(false);
        }

        return {
            policy,
            handles,
            run,
            closeSession,
            closeWorkspace,
            closeWorkspaceWindow,
            canCloseWindow: canCloseWindowHere
        };
    }

    return { policy, create };
}));
