(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.GridVibeSessionPersistence = api;
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const CONTINUOUS_UPDATE_FLOOR_MS = 1000;

    function cloneSnapshot(value) {
        if (value === undefined) return undefined;
        return JSON.parse(JSON.stringify(value));
    }

    function createPresentationQueue(options) {
        if (!options || typeof options.send !== 'function') {
            throw new TypeError('createPresentationQueue requires send(payload)');
        }

        const groups = new Map();

        function stateFor(groupId) {
            const key = String(groupId || '').trim();
            if (!key) throw new TypeError('groupId is required');
            if (!groups.has(key)) {
                groups.set(key, {
                    latest: undefined,
                    pending: undefined,
                    inFlight: null,
                    scheduled: false,
                    timer: null,
                    revision: null,
                    waiters: []
                });
            }
            return [key, groups.get(key)];
        }

        function isIdle(state) {
            return !state.scheduled && !state.timer && !state.inFlight && state.pending === undefined;
        }

        function settleWaiters(state, error) {
            if (!isIdle(state)) return;
            const waiters = state.waiters.splice(0);
            waiters.forEach((waiter) => {
                if (error) waiter.reject(error);
                else waiter.resolve(cloneSnapshot(state.latest));
            });
        }

        async function responsePayload(response) {
            if (response && typeof response.json === 'function') {
                const body = await response.json();
                return { status: response.status, body };
            }
            return {
                status: response && Number.isInteger(response.status) ? response.status : 200,
                body: response || {}
            };
        }

        function schedule(groupId, state, continuous) {
            if (state.inFlight || state.scheduled) return;
            if (continuous) {
                if (state.timer) return;
                const delay = Math.max(
                    CONTINUOUS_UPDATE_FLOOR_MS,
                    Number(options.continuousDelayMs) || CONTINUOUS_UPDATE_FLOOR_MS
                );
                state.timer = setTimeout(() => {
                    state.timer = null;
                    void drain(groupId, state);
                }, delay);
                return;
            }
            if (state.timer) {
                clearTimeout(state.timer);
                state.timer = null;
            }
            state.scheduled = true;
            Promise.resolve().then(() => {
                state.scheduled = false;
                void drain(groupId, state);
            });
        }

        async function handleConflict(groupId, state, requestSnapshot, response) {
            const currentRevision = response.body && response.body.presentation_revision;
            if (Number.isInteger(currentRevision) && currentRevision >= 0) {
                state.revision = currentRevision;
            }
            const serverSnapshot = typeof options.fetchCurrent === 'function'
                ? await options.fetchCurrent(groupId, cloneSnapshot(response.body))
                : response.body;
            let rebased = cloneSnapshot(state.latest);
            if (typeof options.reconcile === 'function') {
                rebased = await options.reconcile({
                    groupId,
                    local: cloneSnapshot(state.latest),
                    sent: cloneSnapshot(requestSnapshot),
                    server: cloneSnapshot(serverSnapshot)
                });
            }
            if (rebased !== undefined && rebased !== null) {
                if (state.revision !== null && Object.prototype.hasOwnProperty.call(rebased, 'expected_revision')) {
                    rebased.expected_revision = state.revision;
                }
                state.latest = cloneSnapshot(rebased);
                state.pending = cloneSnapshot(rebased);
                if (typeof options.onReconciled === 'function') {
                    options.onReconciled(groupId, cloneSnapshot(rebased), cloneSnapshot(serverSnapshot));
                }
            }
        }

        async function drain(groupId, state) {
            if (state.inFlight || state.pending === undefined) {
                settleWaiters(state);
                return;
            }
            const requestSnapshot = cloneSnapshot(state.pending);
            state.pending = undefined;
            if (
                state.revision !== null
                && requestSnapshot
                && Object.prototype.hasOwnProperty.call(requestSnapshot, 'expected_revision')
            ) {
                requestSnapshot.expected_revision = state.revision;
            }

            const task = Promise.resolve().then(() => options.send(requestSnapshot, groupId));
            state.inFlight = task;
            let failure = null;
            try {
                const response = await responsePayload(await task);
                if (response.status === 409) {
                    await handleConflict(groupId, state, requestSnapshot, response);
                } else if (response.status >= 400) {
                    const message = response.body && response.body.error;
                    throw new Error(message || `Presentation update failed (${response.status})`);
                } else {
                    if (
                        response.body
                        && Number.isInteger(response.body.presentation_revision)
                        && response.body.presentation_revision >= 0
                    ) {
                        state.revision = response.body.presentation_revision;
                    }
                    if (typeof options.onAccepted === 'function') {
                        options.onAccepted(groupId, state.revision);
                    }
                }
            } catch (error) {
                failure = error;
                if (typeof options.onError === 'function') {
                    options.onError(groupId, error, cloneSnapshot(requestSnapshot));
                }
            } finally {
                state.inFlight = null;
            }

            if (state.pending !== undefined) {
                schedule(groupId, state, false);
            }
            settleWaiters(state, failure);
        }

        function enqueue(groupId, snapshot, enqueueOptions) {
            const [key, state] = stateFor(groupId);
            state.latest = cloneSnapshot(snapshot);
            state.pending = cloneSnapshot(snapshot);
            const continuous = Boolean(enqueueOptions && enqueueOptions.continuous);
            schedule(key, state, continuous);
            return cloneSnapshot(state.latest);
        }

        function flush(groupId) {
            const [key, state] = stateFor(groupId);
            if (state.timer) {
                clearTimeout(state.timer);
                state.timer = null;
                schedule(key, state, false);
            } else if (state.pending !== undefined) {
                schedule(key, state, false);
            }
            if (isIdle(state)) return Promise.resolve(cloneSnapshot(state.latest));
            return new Promise((resolve, reject) => state.waiters.push({ resolve, reject }));
        }

        function latest(groupId) {
            const [, state] = stateFor(groupId);
            return cloneSnapshot(state.latest);
        }

        function revision(groupId) {
            const [, state] = stateFor(groupId);
            return state.revision;
        }

        /* Adopt a revision the server told us out of band (a groups refetch).
           Ignored while a write is in flight or queued: that write already owns
           the ordering, and overwriting its base would turn a fresh read into a
           lost update. */
        function setRevision(groupId, value) {
            const [, state] = stateFor(groupId);
            if (!Number.isInteger(value) || value < 0) return false;
            if (state.inFlight || state.pending !== undefined || state.timer || state.scheduled) {
                return false;
            }
            state.revision = value;
            return true;
        }

        function forget(groupId) {
            const key = String(groupId || '').trim();
            const state = groups.get(key);
            if (!state) return false;
            if (state.timer) clearTimeout(state.timer);
            state.waiters.splice(0).forEach(waiter => waiter.resolve(undefined));
            groups.delete(key);
            return true;
        }

        return { enqueue, flush, latest, revision, setRevision, forget };
    }

    /* ── Descriptor → wire payload ───────────────────────────────────────
       The host page describes a group in its own vocabulary (pane objects,
       DOM-derived order and geometry); this is the one place that turns that
       description into the bounded transaction body the server accepts. It is
       deliberately free of DOM and of any launch, credential, or status field
       — the route rejects those, and so should the client that builds them. */

    const PANE_MODE_EXPLORER = 'explorer';
    const PANE_MODE_BROWSER = 'browser';

    /* ── The Git pin pair ────────────────────────────────────────────────
       `explorer_git_pin_active` and `explorer_git_pinned_path` are one fact
       in two fields: whether a scope is pinned, and the root-relative path it
       names. They have to be read off a live pane and written back onto a
       rebuilt one at five points on the terminals page — the live describe,
       the Save Workspace launch config, both pane builds, and the
       close-driven overlay — and each spelled the same pair of ternaries out
       by hand. A pin that survives four of those and not the fifth is
       indistinguishable, from the user's side, from a pin that was never
       saved, which is most of what "it seems flaky" was describing. One
       mapping, executed by the tests, instead of five copies.

       The pane's own truth is the *type* of `_explorerGitPinnedPath`: a
       string (including '') means pinned, `undefined` means not pinned. That
       is what makes a pin at the explorer root a real, distinguishable state
       rather than a silent no-op. */
    function explorerGitPinDescriptor(pane) {
        const pinned = typeof pane?._explorerGitPinnedPath === 'string';
        return {
            active: pinned,
            path: pinned ? pane._explorerGitPinnedPath : ''
        };
    }

    /* The inverse: what a rebuilt pane's `_explorerGitPinnedPath` should be,
       given the session record the server just handed back. `undefined` is
       deliberate and load-bearing — assigning '' would make every unpinned
       explorer pane report itself as pinned to its root. */
    function explorerGitPinnedPathFromSession(session) {
        return session && session.explorer_git_pin_active
            ? String(session.explorer_git_pinned_path || '')
            : undefined;
    }

    function buildPanePresentation(pane) {
        const sessionId = String((pane && pane.sessionId) || '').trim();
        if (!sessionId) return null;
        const entry = { session_id: sessionId };
        if (pane.mode === PANE_MODE_EXPLORER && pane.explorer) {
            const explorer = pane.explorer;
            entry.explorer_tree_open = Boolean(explorer.treeOpen);
            entry.explorer_git_open = Boolean(explorer.gitOpen);
            entry.explorer_git_follow_browsing = Boolean(explorer.gitFollowBrowsing);
            entry.explorer_git_pin_active = Boolean(explorer.gitPinActive);
            entry.explorer_git_pinned_path = String(explorer.gitPinnedPath || '');
            entry.explorer_search_open = Boolean(explorer.searchOpen);
            entry.explorer_sidebar_width = Number.isInteger(explorer.sidebarWidth)
                ? explorer.sidebarWidth
                : 260;
            entry.explorer_sidebar_scroll = explorer.sidebarScroll
                && typeof explorer.sidebarScroll === 'object'
                ? explorer.sidebarScroll
                : {};
            entry.explorer_tree_expanded = Array.isArray(explorer.treeExpanded)
                ? explorer.treeExpanded.map(String)
                : [];
            entry.explorer_git_expanded = Array.isArray(explorer.gitExpanded)
                ? explorer.gitExpanded.map(String)
                : [];
            /* Active tab and views are only meaningful next to the tab list the
               server validates them against, so the three always travel together. */
            entry.explorer_open_tabs = Array.isArray(explorer.openTabs)
                ? explorer.openTabs.map(String)
                : [];
            entry.explorer_active_tab = String(explorer.activeTab || '');
            entry.explorer_tab_views = explorer.tabViews && typeof explorer.tabViews === 'object'
                ? explorer.tabViews
                : {};
            entry.explorer_theme = String(explorer.theme || '');
        } else if (pane.mode === PANE_MODE_BROWSER && pane.browser) {
            const tabs = Array.isArray(pane.browser.tabs)
                ? pane.browser.tabs.filter(url => typeof url === 'string' && url)
                : [];
            /* An empty strip is a pane that has not rendered yet, not a user
               closing every tab (the last tab cannot be closed). Sending it
               would blank the stored strip, so say nothing instead. */
            if (tabs.length) {
                entry.browser_tabs = tabs;
                entry.browser_active_tab = Number.isInteger(pane.browser.activeTab)
                    ? Math.max(0, Math.min(tabs.length - 1, pane.browser.activeTab))
                    : 0;
            }
        }
        return entry;
    }

    function buildGroupPresentationPayload(descriptor) {
        if (!descriptor) return null;
        const workspaceId = String(descriptor.workspaceId || '').trim();
        const groupId = String(descriptor.groupId || '').trim();
        const panes = Array.isArray(descriptor.panes) ? descriptor.panes : [];
        if (!workspaceId || !groupId || !panes.length) return null;

        const entries = [];
        for (let i = 0; i < panes.length; i += 1) {
            const entry = buildPanePresentation(panes[i]);
            /* The transaction is all-or-nothing: a pane we cannot identify makes
               the batch an incomplete picture of the group, which the server
               would reject anyway. Say nothing rather than send a partial one. */
            if (!entry) return null;
            entries.push(entry);
        }
        const order = entries.map(entry => entry.session_id);
        if (new Set(order).size !== order.length) return null;

        const payload = {
            workspace_id: workspaceId,
            group_id: groupId,
            expected_revision: Number.isInteger(descriptor.revision) && descriptor.revision >= 0
                ? descriptor.revision
                : 0,
            pane_order: order,
            panes: entries
        };
        if (descriptor.layout) {
            payload.layout = String(descriptor.layout);
        }
        if (descriptor.workspaceLayout) {
            payload.workspace_layout = descriptor.workspaceLayout;
        }
        return payload;
    }

    function buildWorkspacePresentationPayload(descriptor) {
        if (!descriptor) return null;
        const workspaceId = String(descriptor.workspaceId || '').trim();
        if (
            !workspaceId
            || typeof descriptor.topbarVisible !== 'boolean'
            || typeof descriptor.mdPreset !== 'string'
            || typeof descriptor.mdFont !== 'string'
            || typeof descriptor.sourceFont !== 'string'
        ) return null;
        return {
            workspace_id: workspaceId,
            expected_revision: Number.isInteger(descriptor.revision) && descriptor.revision >= 0
                ? descriptor.revision
                : 0,
            topbar_visible: descriptor.topbarVisible,
            md_preset: descriptor.mdPreset,
            md_font: descriptor.mdFont,
            source_font: descriptor.sourceFont
        };
    }

    /* ── The controller the page wires its change events to ──────────────
       One ordered queue per group plus one for workspace-window chrome, a
       re-capture on every enqueue so a coalesced write always carries the
       newest local state, and one explicit `flush()` barrier that Save
       Workspace — and, later, the lifecycle transaction — await before
       asking the server to capture a snapshot. */
    const MAX_CONSECUTIVE_REPAIRS = 2;

    function createPresentationController(options) {
        const opts = options || {};
        if (typeof opts.describeGroup !== 'function') {
            throw new TypeError('createPresentationController requires describeGroup(groupId)');
        }
        if (typeof opts.sendGroup !== 'function') {
            throw new TypeError('createPresentationController requires sendGroup(payload)');
        }

        const WORKSPACE_KEY = '__workspace__';
        const knownGroups = new Set();
        const repairs = new Map();

        function report(scope, error, id) {
            if (typeof opts.onError === 'function') {
                opts.onError(scope, error, id);
            }
        }

        /* A failed write leaves the server holding the older value, which the
           next autosave would then commit. Re-capture and re-enqueue on the
           continuous floor, bounded so a persistently failing server cannot
           turn the queue into a retry loop. */
        function repair(scope, key, capture) {
            const attempts = (repairs.get(key) || 0) + 1;
            repairs.set(key, attempts);
            if (attempts > MAX_CONSECUTIVE_REPAIRS) return;
            capture({ continuous: true });
        }

        const groupQueue = createPresentationQueue({
            continuousDelayMs: opts.continuousDelayMs,
            send: payload => opts.sendGroup(payload),
            fetchCurrent: opts.fetchCurrentGroup,
            reconcile: ({ groupId }) => captureGroup(groupId),
            onReconciled: opts.onReconciled,
            onAccepted: groupId => repairs.delete(groupId),
            onError: (groupId, error) => {
                report('group', error, groupId);
                repair('group', groupId, options => noteGroupChange(groupId, options));
            }
        });

        const workspaceQueue = createPresentationQueue({
            continuousDelayMs: opts.continuousDelayMs,
            send: payload => opts.sendWorkspace(payload),
            fetchCurrent: opts.fetchCurrentWorkspace,
            reconcile: () => captureWorkspace(),
            onAccepted: () => repairs.delete(WORKSPACE_KEY),
            onError: (_key, error) => {
                report('workspace', error, '');
                repair('workspace', WORKSPACE_KEY, options => noteWorkspaceChange(options));
            }
        });

        function captureGroup(groupId) {
            return buildGroupPresentationPayload(opts.describeGroup(groupId));
        }

        function captureWorkspace() {
            if (typeof opts.describeWorkspace !== 'function') return null;
            return buildWorkspacePresentationPayload(opts.describeWorkspace());
        }

        function noteGroupChange(groupId, enqueueOptions) {
            const key = String(groupId || '').trim();
            if (!key) return false;
            const payload = captureGroup(key);
            if (!payload) return false;
            knownGroups.add(key);
            groupQueue.enqueue(key, payload, enqueueOptions);
            return true;
        }

        function noteWorkspaceChange(enqueueOptions) {
            const payload = captureWorkspace();
            if (!payload) return false;
            workspaceQueue.enqueue(WORKSPACE_KEY, payload, enqueueOptions);
            return true;
        }

        function setGroupRevision(groupId, value) {
            const key = String(groupId || '').trim();
            if (!key) return false;
            knownGroups.add(key);
            repairs.delete(key);
            return groupQueue.setRevision(key, value);
        }

        function setWorkspaceRevision(value) {
            repairs.delete(WORKSPACE_KEY);
            return workspaceQueue.setRevision(WORKSPACE_KEY, value);
        }

        function forgetGroup(groupId) {
            const key = String(groupId || '').trim();
            knownGroups.delete(key);
            repairs.delete(key);
            return groupQueue.forget(key);
        }

        /* The exact-save barrier (audit Stage 3 items 5 and 9). Re-capture every
           named group and the window chrome *now*, then wait for the server to
           acknowledge them — so an explicit save is a point-in-time snapshot
           rather than whatever the server last happened to hear. */
        async function flush(groupIds) {
            /* An explicit list is honoured even when it is empty — "this window
               can describe no group right now" is a real answer, and falling
               back to every group ever seen would resend a detached one. */
            const targets = Array.isArray(groupIds)
                ? groupIds.map(id => String(id || '').trim()).filter(Boolean)
                : Array.from(knownGroups);
            const unique = Array.from(new Set(targets));
            unique.forEach(groupId => noteGroupChange(groupId));
            noteWorkspaceChange();
            const waits = unique.map(groupId => groupQueue.flush(groupId));
            waits.push(workspaceQueue.flush(WORKSPACE_KEY));
            await Promise.all(waits);
            return unique;
        }

        return {
            captureGroup,
            captureWorkspace,
            noteGroupChange,
            noteWorkspaceChange,
            setGroupRevision,
            setWorkspaceRevision,
            groupRevision: groupId => groupQueue.revision(groupId),
            workspaceRevision: () => workspaceQueue.revision(WORKSPACE_KEY),
            forgetGroup,
            flush
        };
    }

    return {
        createPresentationQueue,
        createPresentationController,
        explorerGitPinDescriptor,
        explorerGitPinnedPathFromSession,
        buildGroupPresentationPayload,
        buildWorkspacePresentationPayload,
        CONTINUOUS_UPDATE_FLOOR_MS,
        MAX_CONSECUTIVE_REPAIRS
    };
}));
