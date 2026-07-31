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
        if (summary?.origin === 'manual') {
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

    function closeWorkspaceContextMenu() {
        const menu = document.getElementById('workspaceContextMenu');
        if (menu) {
            menu.hidden = true;
            menu.innerHTML = '';
        }
    }

    function openWorkspaceContextMenu(event, entries) {
        const menu = document.getElementById('workspaceContextMenu');
        if (!menu || !entries.length) {
            return;
        }
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
