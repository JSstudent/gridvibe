    /* ─────────────────────────────────────────────
       Explorer copy / paste / delete controller.

       Clipboard, confirmations, requests, busy state, and refreshes are bound
       to an immutable session id + pane reference + root revision. Nothing is
       persisted and no operation can cross Explorer sessions.
    ───────────────────────────────────────────── */

    const explorerFilesystemClipboards = new Map();
    const explorerFilesystemActionTokens = new Map();
    const explorerFilesystemInFlightSessions = new Set();
    let explorerFilesystemTokenCounter = 0;
    let explorerFilesystemMenuSessionId = '';

    function explorerFilesystemToken(sessionId) {
        explorerFilesystemTokenCounter += 1;
        return `${sessionId}:${explorerFilesystemTokenCounter}`;
    }

    function explorerFilesystemBaseName(path) {
        const parts = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        return parts.pop() || '';
    }

    function explorerFilesystemParentPath(path) {
        const parts = String(path || '').replace(/\\/g, '/').split('/').filter(Boolean);
        parts.pop();
        return parts.join('/');
    }

    function explorerFilesystemPathContains(parent, candidate) {
        const parentPath = String(parent || '').replace(/\\/g, '/').replace(/\/+$/, '');
        const candidatePath = String(candidate || '').replace(/\\/g, '/').replace(/\/+$/, '');
        return candidatePath === parentPath || candidatePath.startsWith(`${parentPath}/`);
    }

    function updateExplorerFilesystemRootRevision(index, revision) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        const nextRevision = String(revision || '');
        if (pane._explorerRootRevision && pane._explorerRootRevision !== nextRevision) {
            explorerFilesystemClipboards.delete(sessionId);
            explorerFilesystemActionTokens.delete(sessionId);
        }
        pane._explorerRootRevision = nextRevision;
    }

    function explorerFilesystemActionContext(index, rowContext) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId || !pane._explorerRootRevision) {
            return null;
        }
        const token = explorerFilesystemToken(sessionId);
        explorerFilesystemActionTokens.set(sessionId, token);
        explorerFilesystemMenuSessionId = sessionId;
        return Object.freeze({
            sessionId,
            paneRef: pane,
            rootRevision: pane._explorerRootRevision,
            index,
            path: String(rowContext.path || ''),
            kind: String(rowContext.kind || ''),
            revision: String(rowContext.revision || ''),
            surface: String(rowContext.surface || ''),
            token
        });
    }

    function isExplorerFsActionContextCurrent(context) {
        if (!context) {
            return false;
        }
        const pane = terminals[context.index];
        return (
            sessionIds[context.index] === context.sessionId
            && pane === context.paneRef
            && isExplorerSession(pane?._session)
            && pane._explorerRootRevision === context.rootRevision
            && explorerFilesystemActionTokens.get(context.sessionId) === context.token
            && !pane._closing
        );
    }

    function explorerFilesystemPasteDestination(context) {
        if (context.kind === 'directory') {
            return context.path;
        }
        if (context.surface === 'preview') {
            return context.paneRef._explorerPath || '';
        }
        return explorerFilesystemParentPath(context.path);
    }

    function copyExplorerFilesystemEntry(context) {
        if (!isExplorerFsActionContextCurrent(context) || !context.revision) {
            return;
        }
        explorerFilesystemClipboards.set(context.sessionId, {
            sessionId: context.sessionId,
            rootRevision: context.rootRevision,
            path: context.path,
            kind: context.kind,
            name: explorerFilesystemBaseName(context.path),
            revision: context.revision,
            copiedAt: Date.now()
        });
        showTerminalToast(`Copied ${explorerFilesystemBaseName(context.path)}`, 'success');
    }

    function explorerFilesystemMenuItems(index, rowContext) {
        const context = explorerFilesystemActionContext(index, rowContext);
        if (!context || !context.path || !context.revision) {
            return [];
        }
        const copyable = context.kind === 'file' || context.kind === 'directory';
        let clipboard = explorerFilesystemClipboards.get(context.sessionId) || null;
        if (clipboard && clipboard.rootRevision !== context.rootRevision) {
            explorerFilesystemClipboards.delete(context.sessionId);
            clipboard = null;
        }
        const destination = explorerFilesystemPasteDestination(context);
        const destinationName = explorerFilesystemBaseName(destination);
        const pasteLabel = clipboard
            ? (
                context.kind === 'file'
                    ? `Paste "${clipboard.name}" in containing folder`
                    : `Paste "${clipboard.name}" into "${destinationName || 'root'}"`
            )
            : 'Paste — nothing copied';
        const pasteTitle = clipboard
            ? `Copy ${clipboard.path} into ${destination || 'the explorer root'}`
            : 'Copy a file or folder in this Explorer session first';
        const items = [];
        if (copyable) {
            items.push({
                label: 'Copy',
                action: () => copyExplorerFilesystemEntry(context)
            });
        }
        items.push({
            label: pasteLabel,
            title: pasteTitle,
            disabled: !clipboard,
            action: clipboard
                ? () => pasteExplorerFilesystemEntry(context, clipboard, destination)
                : null
        });
        items.push({
            label: 'Delete…',
            title: `Permanently delete ${context.path}`,
            danger: true,
            placement: 'after-path',
            action: () => deleteExplorerFilesystemEntry(context)
        });
        return items;
    }

    function setExplorerFilesystemBusy(context, label) {
        const pane = context.paneRef;
        if (pane._explorerFsBusy) {
            return false;
        }
        const card = document.getElementById(`tc-${context.index}`);
        const status = document.createElement('div');
        status.className = 'explorer-fs-busy-label';
        status.setAttribute('role', 'status');
        status.textContent = label;
        card?.classList.add('explorer-fs-busy');
        card?.appendChild(status);
        pane._explorerFsBusy = {
            token: context.token,
            label,
            requestStarted: false,
            owner: '',
            card,
            status
        };
        return true;
    }

    function clearExplorerFilesystemBusy(context) {
        const busy = context.paneRef?._explorerFsBusy;
        if (!busy || busy.token !== context.token) {
            return;
        }
        busy.card?.classList.remove('explorer-fs-busy');
        busy.status?.remove();
        context.paneRef._explorerFsBusy = null;
        explorerFilesystemInFlightSessions.delete(context.sessionId);
    }

    function markExplorerFilesystemRequestStarted(context) {
        const busy = context.paneRef?._explorerFsBusy;
        if (!busy || busy.token !== context.token) {
            return false;
        }
        busy.requestStarted = true;
        explorerFilesystemInFlightSessions.add(context.sessionId);
        return true;
    }

    function clearExplorerFilesystemError(index) {
        document.getElementById(`explorer-fs-bar-${index}`)?.remove();
    }

    function explorerFilesystemErrorBarHost(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return null;
        }
        let bar = document.getElementById(`explorer-fs-bar-${index}`);
        if (!bar) {
            bar = document.createElement('div');
            bar.id = `explorer-fs-bar-${index}`;
            bar.className = 'explorer-fs-bar';
            const viewer = document.getElementById(`explorer-viewer-${index}`);
            list.insertBefore(bar, viewer || null);
        }
        return bar;
    }

    function refreshExplorerFilesystemSurfaces(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        if (pane._explorerMode === 'directory') {
            loadExplorerPane(index, null, { force: true, showLoading: false });
        }
        if (pane._explorerTreeSidebarOpen) {
            reloadExplorerTree(index);
        }
        if (pane._explorerGitSidebarOpen) {
            invalidateExplorerGitRepo(index);
            loadExplorerGitRepo(index);
        }
    }

    function showExplorerFilesystemError(context, data, retryAction = null) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        const bar = explorerFilesystemErrorBarHost(context.index);
        if (!bar) {
            return;
        }
        bar.replaceChildren();
        const message = document.createElement('span');
        message.className = 'explorer-fs-bar-message';
        message.setAttribute('role', 'alert');
        message.textContent = data?.error || 'The filesystem operation failed.';
        bar.appendChild(message);

        const actions = document.createElement('span');
        actions.className = 'explorer-fs-bar-actions';
        const primary = document.createElement('button');
        primary.type = 'button';
        primary.className = 'explorer-fs-bar-action';
        if (data?.mutated === false && typeof retryAction === 'function') {
            primary.textContent = 'Retry';
            primary.addEventListener('click', () => {
                clearExplorerFilesystemError(context.index);
                retryAction();
            });
        } else {
            primary.textContent = 'Refresh';
            primary.addEventListener('click', () => {
                clearExplorerFilesystemError(context.index);
                refreshExplorerFilesystemSurfaces(context.index);
            });
        }
        actions.appendChild(primary);
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'explorer-fs-bar-action';
        dismiss.textContent = 'Dismiss';
        dismiss.addEventListener('click', () => clearExplorerFilesystemError(context.index));
        actions.appendChild(dismiss);
        bar.appendChild(actions);
    }

    async function explorerFilesystemRequest(context, route, body) {
        if (!isExplorerFsActionContextCurrent(context)
            || !markExplorerFilesystemRequestStarted(context)) {
            return null;
        }
        try {
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(context.sessionId)}/${route}`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                }
            );
            const data = await response.json().catch(() => ({}));
            return { response, data };
        } catch (error) {
            return {
                response: null,
                data: {
                    error: error?.message || 'The server response is unknown. Refresh to inspect the filesystem.',
                    code: 'io_error',
                    mutated: true
                }
            };
        }
    }

    function invalidateExplorerFilesystemGit(index) {
        const pane = terminals[index];
        if (!pane?._explorerGitSidebarOpen) {
            return Promise.resolve();
        }
        invalidateExplorerGitRepo(index);
        return loadExplorerGitRepo(index);
    }

    function highlightExplorerFilesystemPath(index, path) {
        const escaped = typeof CSS !== 'undefined' && CSS.escape
            ? CSS.escape(path)
            : String(path).replace(/["\\]/g, '\\$&');
        const row = document.querySelector(
            `#tc-${index} [data-explorer-context-path="${escaped}"]`
        );
        if (!row) {
            return;
        }
        row.classList.add('explorer-fs-highlight');
        window.setTimeout(() => row.classList.remove('explorer-fs-highlight'), 1600);
    }

    async function refreshExplorerAfterFilesystemMutation(context, result) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        const pane = context.paneRef;
        const isDelete = Boolean(result.deleted_path);
        if (isDelete) {
            const deletedPath = result.deleted_path;
            const survivingParent = explorerFilesystemParentPath(deletedPath);
            ensureExplorerTabState(pane);
            const activeTab = explorerActiveTab(pane);
            pane._explorerTabs = pane._explorerTabs.filter(tab => (
                tab.id === EXPLORER_PREVIEW_TAB_ID
                || !tab.path
                || !explorerFilesystemPathContains(deletedPath, tab.path)
            ));
            if (activeTab?.path && explorerFilesystemPathContains(deletedPath, activeTab.path)) {
                pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            }
            const preview = explorerPreviewTab(pane);
            if (preview.dirPath && explorerFilesystemPathContains(deletedPath, preview.dirPath)) {
                preview.dirPath = survivingParent;
            }
            if (pane._explorerTreeExpanded instanceof Set) {
                pane._explorerTreeExpanded = new Set(
                    [...pane._explorerTreeExpanded].filter(
                        path => !explorerFilesystemPathContains(deletedPath, path)
                    )
                );
            }
            pane._explorerTreeChildren?.clear();
            pane._explorerTreeErrors?.clear();
            const clipboard = explorerFilesystemClipboards.get(context.sessionId);
            if (clipboard && explorerFilesystemPathContains(deletedPath, clipboard.path)) {
                explorerFilesystemClipboards.delete(context.sessionId);
            }
            if (
                pane._explorerMode === 'directory'
                && explorerFilesystemPathContains(deletedPath, pane._explorerPath || '')
            ) {
                await loadExplorerPane(
                    context.index,
                    survivingParent,
                    { force: true, showLoading: false }
                );
            } else if (pane._explorerMode === 'directory') {
                await loadExplorerPane(
                    context.index,
                    null,
                    { force: true, showLoading: false }
                );
            } else if (
                pane._explorerFilePath
                && explorerFilesystemPathContains(deletedPath, pane._explorerFilePath)
            ) {
                await loadExplorerPane(
                    context.index,
                    survivingParent,
                    { force: true, showLoading: false }
                );
            }
            renderExplorerTabStrip(context.index);
        } else {
            const destinationParent = explorerFilesystemParentPath(result.destination_path);
            if (
                pane._explorerMode === 'directory'
                && (pane._explorerPath || '') === destinationParent
            ) {
                await loadExplorerPane(
                    context.index,
                    null,
                    { force: true, showLoading: false }
                );
            }
        }
        if (pane._explorerTreeSidebarOpen) {
            await reloadExplorerTree(context.index);
        }
        await invalidateExplorerFilesystemGit(context.index);
        if (!isDelete) {
            highlightExplorerFilesystemPath(context.index, result.destination_path);
        }
    }

    async function pasteExplorerFilesystemEntry(context, clipboard, destination) {
        if (!setExplorerFilesystemBusy(
            context,
            clipboard.kind === 'directory' ? 'Copying folder…' : 'Copying file…'
        )) {
            return;
        }
        clearExplorerFilesystemError(context.index);
        try {
            if (!isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const result = await explorerFilesystemRequest(context, 'paste', {
                root_revision: context.rootRevision,
                source_path: clipboard.path,
                source_revision: clipboard.revision,
                destination_directory: destination
            });
            if (!result || !isExplorerFsActionContextCurrent(context)) {
                return;
            }
            if (!result.response?.ok) {
                const status = result.response?.status || 0;
                if (status === 404 || status === 409) {
                    explorerFilesystemClipboards.delete(context.sessionId);
                }
                showExplorerFilesystemError(
                    context,
                    result.data,
                    () => pasteExplorerFilesystemEntry(context, clipboard, destination)
                );
                return;
            }
            await refreshExplorerAfterFilesystemMutation(context, result.data);
            if (isExplorerFsActionContextCurrent(context)) {
                showTerminalToast(
                    `Created ${explorerFilesystemBaseName(result.data.destination_path)}`,
                    'success'
                );
            }
        } finally {
            clearExplorerFilesystemBusy(context);
        }
    }

    async function deleteExplorerFilesystemEntry(context) {
        const label = context.kind === 'directory' ? 'Deleting folder…' : 'Deleting…';
        if (!setExplorerFilesystemBusy(context, label)) {
            return;
        }
        clearExplorerFilesystemError(context.index);
        try {
            const edit = explorerEditState(context.paneRef);
            if (
                edit?.dirty
                && explorerFilesystemPathContains(context.path, edit.path || '')
                && !(await confirmDiscardExplorerEdit(context.index, 'Deleting this folder'))
            ) {
                return;
            }
            if (!isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const name = explorerFilesystemBaseName(context.path);
            const owner = `explorer-fs:${context.sessionId}:${context.token}`;
            context.paneRef._explorerFsBusy.owner = owner;
            const confirmed = await openGenericConfirmModal({
                title: context.kind === 'directory'
                    ? `Permanently delete the folder "${name}" and all of its contents?`
                    : `Permanently delete "${name}"?`,
                copy: context.path,
                note: 'GridVibe cannot undo this action.',
                confirmLabel: 'Delete',
                danger: true,
                owner
            });
            if (!confirmed || !isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const result = await explorerFilesystemRequest(context, 'delete', {
                root_revision: context.rootRevision,
                path: context.path,
                base_revision: context.revision,
                recursive: context.kind === 'directory'
            });
            if (!result || !isExplorerFsActionContextCurrent(context)) {
                return;
            }
            if (!result.response?.ok) {
                showExplorerFilesystemError(
                    context,
                    result.data,
                    result.data?.mutated === false
                        ? () => deleteExplorerFilesystemEntry(context)
                        : null
                );
                return;
            }
            await refreshExplorerAfterFilesystemMutation(context, result.data);
            if (isExplorerFsActionContextCurrent(context)) {
                showTerminalToast(`Deleted ${name}`, 'success');
            }
        } finally {
            clearExplorerFilesystemBusy(context);
        }
    }

    function hasActiveExplorerFilesystemOperation(index) {
        const sessionId = sessionIds[index];
        return Boolean(sessionId && explorerFilesystemInFlightSessions.has(sessionId));
    }

    function hasActiveExplorerFilesystemOperationForSession(sessionId) {
        return explorerFilesystemInFlightSessions.has(String(sessionId || ''));
    }

    function hasActiveExplorerFilesystemOperationForSessions(sessionIdList) {
        return (sessionIdList || []).some(hasActiveExplorerFilesystemOperationForSession);
    }

    function cancelExplorerFilesystemUiForSession(sessionId) {
        const key = String(sessionId || '');
        if (!key || explorerFilesystemInFlightSessions.has(key)) {
            return false;
        }
        explorerFilesystemClipboards.delete(key);
        explorerFilesystemActionTokens.delete(key);
        if (explorerFilesystemMenuSessionId === key) {
            dismissExplorerContextMenu({ restoreFocus: false });
            explorerFilesystemMenuSessionId = '';
        }
        const index = sessionIds.indexOf(key);
        const pane = index === -1 ? null : terminals[index];
        const busy = pane?._explorerFsBusy;
        if (busy?.owner) {
            closeGenericConfirmModalForOwner(busy.owner);
        }
        document.getElementById(`tc-${index}`)
            ?.querySelectorAll('.explorer-context-target, .explorer-fs-highlight')
            .forEach(node => node.classList.remove(
                'explorer-context-target',
                'explorer-fs-highlight'
            ));
        if (busy && !busy.requestStarted) {
            busy.card?.classList.remove('explorer-fs-busy');
            busy.status?.remove();
            pane._explorerFsBusy = null;
        }
        clearExplorerFilesystemError(index);
        return true;
    }
