/* GridVibe workspaces — window-level workspace behaviour shared by both pages.

   Naming rule (multi-workspace plan §2): in this file "workspace" always means
   the window-level container (one OS window, one runtime_state slot). A session
   tab is a "group"; the pane grid inside a tab is the "pane grid"/"layout".
   The older `*Workspace*` helpers in terminals.js that mean "pane grid" keep
   their names until they are otherwise touched.

   Loaded after shared.js on both pages; declarations resolve as globals.
   launcher.js keeps form collection and launch orchestration, terminals.js
   keeps page integration — the workspace API calls, destination lists, move
   actions, window dispatch and the restore chooser all live here so neither
   page grows a parallel implementation (guardrail 6). */

    const WORKSPACE_DEFAULT_ID = 'default';
    const WORKSPACE_ID_PATTERN = /^[a-z0-9]{12}$/;
    const WORKSPACE_NEW_DESTINATION = '__new__';

    /* Stroke-style currentColor icons only — no emoji, no text glyphs
       (guardrail 7). */
    const WORKSPACE_ICONS = {
        window: '<svg class="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="3" y="4" width="18" height="16" rx="2"></rect><path d="M3 9h18"></path></svg>',
        move: '<svg class="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 12h12"></path><path d="M13 8l4 4-4 4"></path><rect x="18" y="4" width="3" height="16" rx="1"></rect></svg>',
        add: '<svg class="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 5v14"></path><path d="M5 12h14"></path></svg>',
        check: '<svg class="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M20 6 9 17l-5-5"></path></svg>',
        forget: '<svg class="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M4 7h16"></path><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"></path></svg>'
    };

    function isMultiWorkspaceEnabled() {
        return typeof MULTI_WORKSPACE_ENABLED !== 'undefined' && MULTI_WORKSPACE_ENABLED === true;
    }

    /* Client-side mirror of web/workspaces.py: ids are opaque, and a window
       name or URL is only ever derived from a normalized value. */
    function normalizeWorkspaceId(value) {
        const candidate = String(value || WORKSPACE_DEFAULT_ID).trim();
        return candidate === WORKSPACE_DEFAULT_ID || WORKSPACE_ID_PATTERN.test(candidate)
            ? candidate
            : WORKSPACE_DEFAULT_ID;
    }

    function workspaceWindowName(workspaceId) {
        return `gridvibe-workspace-${normalizeWorkspaceId(workspaceId)}`;
    }

    function workspaceUrl(workspaceId, groupId = '') {
        const params = new URLSearchParams({ workspace: normalizeWorkspaceId(workspaceId) });
        if (groupId) {
            params.set('group', groupId);
        }
        return `/terminals?${params.toString()}`;
    }

    /* A label is optional chrome. Fall back to a stable, human-readable name so
       a picker never shows a bare opaque id. */
    function workspaceDisplayLabel(workspace, index = 0) {
        const label = String(workspace?.label || '').trim();
        if (label) {
            return label;
        }
        if (normalizeWorkspaceId(workspace?.workspace_id) === WORKSPACE_DEFAULT_ID) {
            return 'Main workspace';
        }
        return `Workspace ${index + 1}`;
    }

    async function workspaceApiRequest(path, options = {}) {
        const response = await fetch(path, options);
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok, status: response.status, data };
    }

    async function fetchLiveWorkspaces() {
        const { ok, data } = await workspaceApiRequest('/api/workspaces');
        return ok && Array.isArray(data.workspaces) ? data.workspaces : [];
    }

    /* The Alt+W walk. Same list, same order as the Open Workspace menu, so the
       shortcut and the menu can never disagree about what "the next workspace"
       is. Returns null when there is nowhere else to go — a window whose own
       workspace is not in the list (it was just pruned) still walks from the
       front rather than getting stuck.

       Only workspaces that still hold a group are walked. A live workspace
       record is not the same thing as an open window: the `default` record is
       permanent and Workspace ▸ New Workspace keeps its (empty) workspace until
       its first group arrives, so an empty record routinely outlives — or never
       had — a window. Losing its last group is exactly when a window closes
       itself, so `group_count > 0` is the honest test for "there is a window
       there to switch to", and without it a single-window session cycles onto
       an empty record and *opens* a blank second window instead of switching.
       The current workspace stays in the list either way so the walk keeps its
       place in the order. */
    function nextWorkspaceInCycle(workspaces, currentWorkspaceId, step = 1) {
        const list = (Array.isArray(workspaces) ? workspaces : []).filter(
            workspace => Number(workspace?.group_count) > 0
                || workspace?.workspace_id === currentWorkspaceId
        );
        if (list.length < 2) {
            return null;
        }
        const currentIndex = list.findIndex(
            workspace => workspace.workspace_id === currentWorkspaceId
        );
        const offset = currentIndex === -1 ? 0 : currentIndex + step;
        const next = list[((offset % list.length) + list.length) % list.length];
        return next && next.workspace_id !== currentWorkspaceId ? next : null;
    }

    /* ── Cross-window workspace invalidation ──
       The launcher has no Socket.IO connection, so the workspace rooms cannot
       reach it. Terminal windows announce every change that alters a workspace
       list — a launched or closed tab, a move, a rename — on the same
       BroadcastChannel/localStorage pair the app-config broadcast already uses,
       and the launcher refreshes its destination menu and live list from it.
       A sender never receives its own message, so a window that made the change
       still refreshes itself directly. */
    const WORKSPACES_BROADCAST_CHANNEL = 'gridvibe.workspaces';
    const WORKSPACES_UPDATE_STORAGE_KEY = 'gridvibe.workspacesUpdated';

    function notifyWorkspacesChanged(reason = '') {
        const payload = {
            reason: String(reason || ''),
            timestamp: Date.now(),
            nonce: Math.random().toString(36).slice(2),
            source: GRIDVIBE_WINDOW_ID
        };
        try {
            const channel = new BroadcastChannel(WORKSPACES_BROADCAST_CHANNEL);
            channel.postMessage(payload);
            channel.close();
        } catch (_error) {}
        try {
            localStorage.setItem(WORKSPACES_UPDATE_STORAGE_KEY, JSON.stringify(payload));
        } catch (_error) {}
        return payload;
    }

    function onWorkspacesChanged(handler) {
        if (typeof handler !== 'function') {
            return;
        }
        try {
            const channel = new BroadcastChannel(WORKSPACES_BROADCAST_CHANNEL);
            channel.onmessage = event => {
                if (!isOwnBroadcast(event.data)) {
                    handler(event.data || {});
                }
            };
        } catch (_error) {}
        window.addEventListener('storage', event => {
            if (event.key !== WORKSPACES_UPDATE_STORAGE_KEY || !event.newValue) {
                return;
            }
            try {
                handler(JSON.parse(event.newValue));
            } catch (_error) {}
        });
        /* BroadcastChannel does not cross a native WebView2 window boundary in
           every host, and a window can be closed while hidden. Refreshing when
           the window is looked at again costs one request and closes that gap
           without any polling (guardrail 3). */
        window.addEventListener('focus', () => handler({ reason: 'focus' }));
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                handler({ reason: 'visible' });
            }
        });
    }

    async function createWorkspaceRecord(label = '') {
        const { ok, data } = await workspaceApiRequest('/api/workspaces', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: String(label || '') })
        });
        if (!ok) {
            throw new Error(data.error || 'Could not create the workspace');
        }
        return data;
    }

    async function renameWorkspaceRecord(workspaceId, label) {
        const { ok, data } = await workspaceApiRequest(
            `/api/workspaces/${encodeURIComponent(normalizeWorkspaceId(workspaceId))}`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: String(label || '') })
            }
        );
        if (!ok) {
            throw new Error(data.error || 'Could not rename the workspace');
        }
        return data;
    }

    /* The single move entry point: the tab context menu, the Workspace menu and
       (if shipped) the drag tray all call this, so there is one contract to
       reason about. `target` is {workspaceId} or {newWorkspace: true, label}. */
    async function moveGroupToWorkspace(groupId, target = {}) {
        const body = target.newWorkspace
            ? { new_workspace: true, label: String(target.label || '') }
            : { target_workspace_id: normalizeWorkspaceId(target.workspaceId) };
        const { ok, data } = await workspaceApiRequest(
            `/api/session-groups/${encodeURIComponent(String(groupId || ''))}/move`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            }
        );
        if (!ok) {
            throw new Error(data.error || 'Could not move the session');
        }
        return data;
    }

    /* ── Window dispatch ──
       Native windows cannot infer their caller from the shared js_api object,
       so every workspace-aware bridge call carries an explicit workspace id. */
    function nativeWorkspaceApi() {
        return window.pywebview?.api || null;
    }

    async function focusWorkspaceWindow(workspaceId) {
        const resolvedWorkspaceId = normalizeWorkspaceId(workspaceId);
        const api = nativeWorkspaceApi();
        if (api?.focus_workspace_window) {
            try {
                const result = await api.focus_workspace_window(resolvedWorkspaceId);
                if (result?.ok) {
                    return true;
                }
            } catch (error) {
                console.error('[GridVibe Workspaces] focus workspace window failed:', error);
            }
        }
        return false;
    }

    async function openWorkspaceWindow(workspaceId, options = {}) {
        const resolvedWorkspaceId = normalizeWorkspaceId(workspaceId);
        const groupId = String(options.groupId || '');
        const nativeZoomFactor = normalizeNativeZoomFactor(options.nativeZoomFactor);
        const api = nativeWorkspaceApi();
        if (api?.open_workspace_window) {
            try {
                const result = await api.open_workspace_window(
                    resolvedWorkspaceId,
                    groupId,
                    nativeZoomFactor
                );
                if (result?.ok) {
                    return true;
                }
            } catch (error) {
                console.error('[GridVibe Workspaces] open workspace window failed:', error);
            }
        }
        window.open(workspaceUrl(resolvedWorkspaceId, groupId), workspaceWindowName(resolvedWorkspaceId));
        return true;
    }

    async function closeWorkspaceWindow(workspaceId) {
        const api = nativeWorkspaceApi();
        if (api?.close_workspace_window) {
            try {
                const result = await api.close_workspace_window(normalizeWorkspaceId(workspaceId));
                if (result?.ok) {
                    return true;
                }
            } catch (error) {
                console.error('[GridVibe Workspaces] close workspace window failed:', error);
            }
        }
        window.close();
        return false;
    }

    /* ── Turning the multi-workspace mode on and off ──
       The launcher's Workspaces card owns the switch but not this policy; it
       lives here so a second surface can only ever call it (guardrail 6). */

    async function closeExtraWorkspaces() {
        const { ok, data } = await workspaceApiRequest('/api/workspaces/close-extra', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        if (!ok) {
            throw new Error(data.error || 'Could not close the extra workspaces');
        }
        return data;
    }

    /* Switching the mode off ends live shells in every workspace but the main
       one, so it is confirmed in page first (guardrail 8). Returns true when the
       save may proceed with the flag off. */
    async function confirmMultiWorkspaceDisable() {
        const extras = (await fetchLiveWorkspaces())
            .filter(workspace => workspace.workspace_id !== WORKSPACE_DEFAULT_ID
                && (Number(workspace.group_count) || 0) > 0);
        if (!extras.length) {
            return true;
        }
        const sessionCount = extras.reduce(
            (total, workspace) => total + (Number(workspace.group_count) || 0),
            0
        );
        return openGenericConfirmModal({
            title: 'Turn off multiple workspaces?',
            copy: `${extras.length} workspace${extras.length === 1 ? '' : 's'}`
                + ` (${sessionCount} session${sessionCount === 1 ? '' : 's'}) will be closed`
                + ' so everything runs in the main workspace again.',
            note: 'Only what auto-save or Workspace ▸ Save Workspace already captured'
                + ' can be restored afterwards. Close them?',
            confirmLabel: 'Turn off and close',
            danger: true
        });
    }

    /* Both pages render the mode from server-side markup, so a flag change is
       applied by reloading. A window whose workspace was just closed has nothing
       left to render and closes instead of reloading into a 400. */
    async function reactToMultiWorkspaceFlagChange(enabled, { currentWorkspaceId = '' } = {}) {
        const ownId = normalizeWorkspaceId(currentWorkspaceId);
        if (!enabled && currentWorkspaceId && ownId !== WORKSPACE_DEFAULT_ID) {
            await closeWorkspaceWindow(ownId);
            return;
        }
        window.location.reload();
    }

    /* The window that saved the setting also performs the close, once. Every
       other window only reacts (above), so the teardown never runs N times. */
    async function applyMultiWorkspaceFlagChange(enabled, options = {}) {
        if (!enabled) {
            try {
                await closeExtraWorkspaces();
            } catch (error) {
                console.error('[GridVibe Workspaces] closing extra workspaces failed:', error);
            }
            notifyWorkspacesChanged('multi_workspace_disabled');
        }
        await reactToMultiWorkspaceFlagChange(enabled, options);
    }

    /* The single entry point for changing the mode: confirm what turning it off
       costs, persist just this key, tell every other window, then apply it here.
       Only `multi_workspace_enabled` is sent, so a switch flip can never carry
       along a half-edited App Settings form — the backend keeps every key the
       payload omits. Returns the flag that is in effect afterwards, which is the
       old one when the user declines the confirm. */
    async function setMultiWorkspaceEnabled(enabled, { currentWorkspaceId = '' } = {}) {
        const next = Boolean(enabled);
        if (next === isMultiWorkspaceEnabled()) {
            return next;
        }
        if (!next && !(await confirmMultiWorkspaceDisable())) {
            return true;
        }
        const { ok, data } = await workspaceApiRequest('/api/app-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace: { multi_workspace_enabled: next } })
        });
        if (!ok) {
            throw new Error(data.error || 'Could not change the workspace mode');
        }
        /* Every window renders the mode from server-side markup, so the others
           only learn about the change from this broadcast (a sender never
           receives its own message — this window applies it below). */
        notifyAppConfigUpdated(data);
        await applyMultiWorkspaceFlagChange(next, { currentWorkspaceId });
        return next;
    }

    /* ── Saved-workspace snapshots (restore chooser) ── */

    async function fetchRestorableWorkspaces() {
        const { ok, data } = await workspaceApiRequest('/api/runtime-state/workspaces');
        return ok && Array.isArray(data.workspaces) ? data.workspaces : [];
    }

    async function restoreSavedWorkspaces(workspaceIds) {
        const { ok, data } = await workspaceApiRequest('/api/runtime-state/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace_ids: workspaceIds })
        });
        if (!ok) {
            throw new Error(data.error || 'Could not restore the selected workspaces');
        }
        return data;
    }

    /* Forget deletes the workspace *snapshot* only. Saved sessions live in a
       separate store and other snapshots may reference the same preset, so a
       Forget never removes a preset. Idempotent: a slot that was already gone
       still answers 200 with forgotten: false. */
    async function forgetSavedWorkspace(workspaceId) {
        const { ok, status, data } = await workspaceApiRequest(
            `/api/runtime-state?workspace_id=${encodeURIComponent(normalizeWorkspaceId(workspaceId))}`,
            { method: 'DELETE' }
        );
        if (!ok) {
            const error = new Error(data.error || 'Could not forget this saved workspace');
            error.status = status;
            error.liveConflict = Boolean(data.live_conflict);
            throw error;
        }
        return Boolean(data.forgotten);
    }

    function formatWorkspaceSavedAgo(savedAt) {
        const savedSeconds = Number(savedAt);
        if (!Number.isFinite(savedSeconds) || savedSeconds <= 0) {
            return '';
        }
        const elapsedSeconds = Math.max(0, Math.floor(Date.now() / 1000 - savedSeconds));
        if (elapsedSeconds < 60) {
            return 'just now';
        }
        const minutes = Math.floor(elapsedSeconds / 60);
        if (minutes < 60) {
            return `${minutes} min ago`;
        }
        const hours = Math.floor(minutes / 60);
        if (hours < 24) {
            return `${hours} h ago`;
        }
        const days = Math.floor(hours / 24);
        return `${days} day${days === 1 ? '' : 's'} ago`;
    }

    function describeRestorableWorkspace(summary) {
        const groupCount = Number(summary?.group_count) || 0;
        const paneCount = Number(summary?.pane_count) || 0;
        const savedAgo = formatWorkspaceSavedAgo(summary?.saved_at);
        const parts = [
            `${groupCount} session${groupCount === 1 ? '' : 's'}`,
            `${paneCount} pane${paneCount === 1 ? '' : 's'}`
        ];
        if (savedAgo) {
            parts.push(`saved ${savedAgo}`);
        }
        /* "Saved manually" is durable pinning metadata, not the writer of the
           current shape: a workspace the user saved by hand keeps the note
           after the autosave timer has refreshed it (which sets origin back to
           'auto'). Older slots only carry the conflated origin. */
        if (summary?.manually_saved_at || summary?.origin === 'manual') {
            parts.push('saved manually');
        }
        return parts.join(' • ');
    }

    /* ── In-page menus ──
       Built as DOM here so no page ever reaches for a native menu or a
       window.prompt/confirm/alert (guardrail 4; WebView2 blocks them). */
    function buildWorkspaceMenuItem({ label, icon = '', disabled = false, current = false, onSelect = null }) {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'app-menu-item workspace-menu-item';
        item.setAttribute('role', 'menuitem');
        item.disabled = Boolean(disabled);
        if (current) {
            item.classList.add('is-current');
            item.setAttribute('aria-current', 'true');
        }
        const iconMarkup = icon || (current ? WORKSPACE_ICONS.check : '');
        item.innerHTML = `${iconMarkup}<span class="workspace-menu-label">${escHtml(label)}</span>`;
        if (typeof onSelect === 'function' && !disabled) {
            item.addEventListener('click', event => {
                event.preventDefault();
                onSelect(event);
            });
        }
        return item;
    }

    /* ── Workspace name dialog (New Workspace / Rename Workspace) ──
       Resolves to the entered label, or null when cancelled. The shell only
       exists on the terminals page; callers elsewhere get null. */
    let workspaceNameResolver = null;

    function closeWorkspaceNameModal(result = null) {
        const modal = document.getElementById('workspaceNameModal');
        if (modal) {
            modal.classList.remove('visible');
            modal.setAttribute('aria-hidden', 'true');
        }
        const resolve = workspaceNameResolver;
        workspaceNameResolver = null;
        if (resolve) {
            resolve(result);
        }
    }

    function openWorkspaceNameModal({ title = 'New workspace', copy = '', value = '', confirmLabel = 'Save' } = {}) {
        const modal = document.getElementById('workspaceNameModal');
        const input = document.getElementById('workspaceNameInput');
        if (!modal || !input) {
            return Promise.resolve(null);
        }
        closeWorkspaceNameModal(null);

        document.getElementById('workspaceNameTitle').textContent = title;
        const copyElement = document.getElementById('workspaceNameCopy');
        copyElement.textContent = copy;
        copyElement.hidden = !copy;
        document.getElementById('workspaceNameAccept').textContent = confirmLabel;
        input.value = String(value || '');

        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');
        window.setTimeout(() => {
            input.focus();
            input.select();
        }, 0);

        return new Promise(resolve => {
            workspaceNameResolver = resolve;
        });
    }

    function wireWorkspaceNameModal() {
        const modal = document.getElementById('workspaceNameModal');
        if (!modal) {
            return;
        }
        document.getElementById('workspaceNameForm')?.addEventListener('submit', event => {
            event.preventDefault();
            closeWorkspaceNameModal(document.getElementById('workspaceNameInput')?.value || '');
        });
        document.getElementById('workspaceNameCancel')?.addEventListener('click', () => {
            closeWorkspaceNameModal(null);
        });
        modal.addEventListener('click', event => {
            if (event.target === modal) {
                closeWorkspaceNameModal(null);
            }
        });
        modal.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                event.stopPropagation();
                closeWorkspaceNameModal(null);
            }
        });
    }

    /* ── Session tab context menu ── */

    /* The control that opened the menu, so its aria-expanded can be told when
       the menu goes away — the menu closes on an outside click or Escape,
       neither of which the opener hears. */
    let workspaceContextMenuAnchor = null;

    function releaseWorkspaceContextMenuAnchor() {
        if (workspaceContextMenuAnchor) {
            workspaceContextMenuAnchor.setAttribute('aria-expanded', 'false');
            workspaceContextMenuAnchor = null;
        }
    }

    function closeWorkspaceContextMenu() {
        const menu = document.getElementById('workspaceContextMenu');
        if (menu) {
            menu.hidden = true;
            menu.innerHTML = '';
        }
        releaseWorkspaceContextMenuAnchor();
    }

    function openWorkspaceContextMenu(event, entries, { anchor = null } = {}) {
        const menu = document.getElementById('workspaceContextMenu');
        if (!menu || !entries.length) {
            return;
        }
        releaseWorkspaceContextMenuAnchor();
        menu.innerHTML = '';
        entries.forEach(entry => {
            menu.appendChild(
                buildWorkspaceMenuItem({
                    ...entry,
                    onSelect: () => {
                        closeWorkspaceContextMenu();
                        entry.onSelect?.();
                    }
                })
            );
        });
        menu.hidden = false;
        const { innerWidth, innerHeight } = window;
        const rect = menu.getBoundingClientRect();
        menu.style.left = `${Math.max(4, Math.min(event.clientX, innerWidth - rect.width - 8))}px`;
        menu.style.top = `${Math.max(4, Math.min(event.clientY, innerHeight - rect.height - 8))}px`;
        if (anchor?.hasAttribute?.('aria-expanded')) {
            workspaceContextMenuAnchor = anchor;
            anchor.setAttribute('aria-expanded', 'true');
        }
    }

    document.addEventListener('pointerdown', event => {
        const menu = document.getElementById('workspaceContextMenu');
        if (menu && !menu.hidden && !menu.contains(event.target)) {
            closeWorkspaceContextMenu();
        }
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeWorkspaceContextMenu();
        }
    });
    document.addEventListener('DOMContentLoaded', wireWorkspaceNameModal);

    function renderWorkspaceMenuList(container, entries) {
        if (!container) {
            return;
        }
        container.innerHTML = '';
        if (!entries.length) {
            const empty = document.createElement('div');
            empty.className = 'workspace-menu-empty';
            empty.textContent = 'No other workspaces';
            container.appendChild(empty);
            return;
        }
        entries.forEach(entry => container.appendChild(buildWorkspaceMenuItem(entry)));
    }
